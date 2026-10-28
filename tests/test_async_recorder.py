"""Recording boundary uses a dedicated writer, bounded memory and visible loss."""

import threading
from datetime import datetime, timezone

from qx_stc_isp.vofa.recorder import AsyncRecorder, CsvRecorder
from qx_stc_isp.vofa.capture_stream import RawReplayWorker, StreamRecorder, iter_stream_records
from qx_stc_isp.vofa.model import TelemetryEvent
from qx_stc_isp.vofa.playback import iter_csv_frames


class BlockingSink:
    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.operations = []

    def open(self):
        self.operations.append(("open", threading.get_ident()))

    def record(self, value):
        self.operations.append((value, threading.get_ident()))
        self.entered.set()
        assert self.release.wait(2)

    def flush(self):
        self.operations.append(("flush", threading.get_ident()))

    def close(self):
        self.operations.append(("close", threading.get_ident()))


def test_slow_disk_never_blocks_submit_and_full_queue_reports_unsaved():
    sink = BlockingSink()
    recorder = AsyncRecorder(sink, max_queue_records=2)
    recorder.open()
    try:
        assert recorder.record("in-flight")
        assert sink.entered.wait(1)
        assert recorder.record("pending-1")
        assert recorder.record("pending-2")
        assert not recorder.record("queue-full")
        assert not recorder.statistics.active
        assert recorder.statistics.unsaved == 3
        assert "队列" in recorder.statistics.error
        assert not recorder.close()
    finally:
        sink.release.set()
        assert recorder.wait_closed(2)
    assert recorder.statistics.saved == 1
    assert recorder.statistics.unsaved == 3
    assert len({thread_id for _, thread_id in sink.operations}) == 1
    assert sink.operations[0][1] != threading.get_ident()
    assert sink.operations[-1][0] == "close"


def test_disk_failure_closes_sink_and_reports_inflight_and_pending_unsaved():
    class FailedSink(BlockingSink):
        def record(self, value):
            super().record(value)
            raise OSError("disk full")

    sink = FailedSink()
    recorder = AsyncRecorder(sink)
    recorder.open()
    assert recorder.record("failed")
    assert sink.entered.wait(1)
    assert recorder.record("pending")
    sink.release.set()
    assert recorder.wait_closed(2)
    stats = recorder.statistics
    assert (stats.saved, stats.unsaved, stats.queued) == (0, 2, 0)
    assert "disk full" in stats.error
    assert sink.operations[-1][0] == "close"


def test_exclusive_file_open_failure_is_visible_and_preserves_existing_file(tmp_path):
    path = tmp_path / "existing.csv"
    path.write_text("keep me", encoding="utf-8")
    recorder = AsyncRecorder(CsvRecorder(path))
    recorder.open()
    assert recorder.wait_closed(2)
    assert recorder.statistics.error is not None
    assert path.read_text(encoding="utf-8") == "keep me"


def test_async_csv_drains_before_close_and_keeps_each_events_timeline_and_names(tmp_path):
    stamp = datetime(2026, 10, 21, 1, 2, 3, 456789, tzinfo=timezone.utc)
    path = tmp_path / "capture.csv"
    sink = CsvRecorder(path, channel_names=("mutable fallback",))
    recorder = AsyncRecorder(sink)
    recorder.open()
    for value in (1.0, 2.0, 3.0):
        assert recorder.record(TelemetryEvent(raw=b"", samples=(value,), names=("source",),
                                               source_timestamp=stamp))
    recorder.close()
    assert recorder.wait_closed(2)
    assert recorder.statistics.saved == 3
    assert recorder.statistics.unsaved == 0
    frames = list(iter_csv_frames(path))
    assert [frame.samples for frame in frames] == [(1.0,), (2.0,), (3.0,)]
    assert all(frame.timestamp == stamp and frame.names == ("source",) for frame in frames)


def test_async_raw_capture_and_rx_replay_keep_file_time_without_replaying_tx(tmp_path):
    stamp = datetime(2026, 10, 21, 1, 2, 3, 123456, tzinfo=timezone.utc)
    path = tmp_path / "stream.jsonl"
    recorder = AsyncRecorder(StreamRecorder(path))
    recorder.open()
    assert recorder.record("RX", b"1,2\n", at=stamp)
    assert recorder.record("TX", b"never resend", at=stamp)
    recorder.close()
    assert recorder.wait_closed(2)
    assert [record.direction for record in iter_stream_records(path)] == ["RX", "TX"]
    worker = RawReplayWorker(path, "FireWater", speed=20)
    events, transmitted = [], []
    worker.events.connect(lambda batch: events.extend(batch))
    worker.transmitted.connect(transmitted.append)
    worker.run()
    assert len(events) == 1
    assert events[0].samples == (1.0, 2.0)
    assert events[0].source_timestamp == stamp
    assert events[0].received_at is not None
    assert transmitted == []


def test_header_flush_failure_is_reported_before_any_record_and_cleanup_is_preserved():
    class HeaderFailure(BlockingSink):
        def flush(self):
            raise OSError("header disk full")

        def close(self):
            raise OSError("close failed")

    recorder = AsyncRecorder(HeaderFailure())
    recorder.open()
    assert recorder.wait_closed(1)
    assert "header disk full" in recorder.statistics.error
    assert "close failed" in recorder.statistics.error


def test_raw_256_channels_can_be_saved_and_read_back_without_display_truncation(tmp_path):
    path = tmp_path / "full.csv"
    with CsvRecorder(path) as recorder:
        recorder.record(TelemetryEvent(raw=b"", samples=tuple(float(index) for index in range(256))))
    frames = list(iter_csv_frames(path))
    assert len(frames[0].samples) == 256
    assert frames[0].samples[-1] == 255.0
