from datetime import datetime, timedelta, timezone
import json

import pytest

from qx_stc_isp.vofa.capture_stream import StreamRecorder, iter_stream_records
from qx_stc_isp.vofa.rotating_capture import RotatingStreamRecorder, iter_recording_records


def test_source_recording_is_explicit_v2_and_old_default_remains_v1(tmp_path):
    stamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
    source = tmp_path / "source.jsonl"
    sink = RotatingStreamRecorder(source, connection_id="board-g431", generation=7, max_part_bytes=400)
    sink.open()
    for index in range(5):
        sink.record("RX" if index % 2 == 0 else "TX", b"sample", at=stamp + timedelta(seconds=index))
    sink.close()
    assert json.loads(source.read_text().splitlines()[0])["version"] == 2
    assert json.loads(sink.manifest_path.read_text())["version"] == 2
    records = list(iter_recording_records(sink.manifest_path))
    assert [(record.connection_id, record.generation) for record in records] == [("board-g431", 7)] * 5
    assert records[-1].timestamp == stamp + timedelta(seconds=4)
    assert records[-1].part_index > 0
    legacy = tmp_path / "old.jsonl"
    with StreamRecorder(legacy) as old:
        old.record("RX", b"old", at=stamp)
    assert json.loads(legacy.read_text().splitlines()[0])["version"] == 1
    assert list(iter_stream_records(legacy))[0].connection_id is None


def test_index_is_sparse_complete_and_invalidates_same_size_changed_input(tmp_path):
    from qx_stc_isp.vofa.replay_index import RecordingIndex, IndexStaleError
    source = tmp_path / "indexed.jsonl"
    stamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with StreamRecorder(source) as sink:
        for index in range(600):
            sink.record("RX", b"1\n", at=stamp + timedelta(milliseconds=index))
    before = source.read_bytes()
    index = RecordingIndex.build(source)
    assert (index.record_count, index.rx_count, index.tx_count) == (600, 600, 0)
    assert index.duration_seconds == pytest.approx(0.599)
    assert len(index.entries) == 3
    assert index.checkpoint(0.3).sequence == 256
    sidecar = tmp_path / "derived.index.json"
    index.save(sidecar)
    restored = RecordingIndex.load(source, sidecar)
    assert restored == index
    assert source.read_bytes() == before
    source.write_bytes(before.replace(b'"hex":"310a"', b'"hex":"320a"'))
    with pytest.raises(IndexStaleError, match="变化"):
        RecordingIndex.load(source, sidecar)


def test_index_cancellation_and_incomplete_session_never_create_index(tmp_path):
    import threading
    from qx_stc_isp.vofa.replay_index import RecordingIndex, IndexCancelled
    source = tmp_path / "incomplete.jsonl"
    sink = RotatingStreamRecorder(source)
    sink.open()
    sink.record("RX", b"1\n")
    sink.flush()
    with pytest.raises(ValueError, match="完整"):
        RecordingIndex.build(sink.manifest_path)
    sink.close()
    cancellation = threading.Event()
    cancellation.set()
    with pytest.raises(IndexCancelled):
        RecordingIndex.build(source, cancel=cancellation)


def test_paused_seek_discards_old_half_frame_and_only_replays_rx_after_resume(qtbot, tmp_path):
    import threading
    from qx_stc_isp.vofa.capture_stream import RawReplayWorker
    source = tmp_path / "seek.jsonl"
    stamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with StreamRecorder(source) as sink:
        sink.record("RX", b"12", at=stamp)
        sink.record("TX", b"must not send", at=stamp + timedelta(seconds=1))
        sink.record("RX", b",34\n", at=stamp + timedelta(seconds=2))
        sink.record("RX", b"56,78\n", at=stamp + timedelta(seconds=3))
    worker = RawReplayWorker(source, "FireWater", speed=20)
    worker.set_paused(True)
    generations, events, raw, transmitted, positions, statuses = [], [], [], [], [], []
    worker.generation_changed.connect(generations.append)
    worker.generation_events.connect(lambda generation, batch: events.extend((generation, event) for event in batch))
    worker.generation_raw.connect(lambda generation, payload: raw.append((generation, payload)))
    worker.transmitted.connect(transmitted.append)
    worker.timeline_changed.connect(lambda at, duration, generation: positions.append((at, duration, generation)))
    worker.status.connect(lambda kind, message: statuses.append((kind, message)))
    worker.seek_seconds(2)
    thread = threading.Thread(target=worker.run)
    thread.start()
    try:
        qtbot.waitUntil(lambda: any(at == 2 for at, _, _ in positions), timeout=2000)
        assert worker.paused
        assert events == raw == []
        worker.set_speed(10)
        worker.set_loop(False)
        worker.set_paused(False)
        qtbot.waitUntil(lambda: not thread.is_alive(), timeout=2000)
        assert transmitted == []
        assert [event.samples for _, event in events] == [(12.0, 34.0), (56.0, 78.0)]
        assert {generation for generation, _ in events} == {1}
        assert events[0][1].source_timestamp == stamp + timedelta(seconds=2)
        assert b"12" not in [payload for _, payload in raw]
        assert all("失败" not in message for _, message in statuses)
    finally:
        worker.stop()
        thread.join(2)
    assert not thread.is_alive()


def test_stop_paused_replay_and_seek_while_waiting_rebuilds_generation(qtbot, tmp_path):
    import threading
    from qx_stc_isp.vofa.capture_stream import RawReplayWorker
    source = tmp_path / "waiting.jsonl"
    stamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with StreamRecorder(source) as sink:
        sink.record("RX", b"1\n", at=stamp)
        sink.record("RX", b"2\n", at=stamp + timedelta(seconds=100))
    worker = RawReplayWorker(source, "FireWater")
    events = []
    worker.generation_events.connect(lambda generation, batch: events.extend((generation, event.samples) for event in batch))
    thread = threading.Thread(target=worker.run)
    thread.start()
    try:
        qtbot.waitUntil(lambda: events == [(0, (1.0,))], timeout=2000)
        worker.set_paused(True)
        generation = worker.seek_seconds(100)
        qtbot.wait(100)
        assert events == [(0, (1.0,))]
        worker.set_paused(False)
        qtbot.waitUntil(lambda: not thread.is_alive(), timeout=2000)
        assert events == [(0, (1.0,)), (generation, (2.0,))]
    finally:
        worker.stop()
        thread.join(2)
    stopped = RawReplayWorker(source, "FireWater")
    stopped.set_paused(True)
    next_thread = threading.Thread(target=stopped.run)
    next_thread.start()
    stopped.stop()
    next_thread.join(2)
    assert not next_thread.is_alive()


def test_speed_change_affects_in_progress_wait(qtbot, tmp_path):
    import threading
    import time
    from qx_stc_isp.vofa.capture_stream import RawReplayWorker
    source = tmp_path / "speed.jsonl"
    stamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with StreamRecorder(source) as sink:
        sink.record("RX", b"1\n", at=stamp)
        sink.record("RX", b"2\n", at=stamp + timedelta(seconds=4))
    worker = RawReplayWorker(source, "FireWater")
    events = []
    worker.events.connect(events.extend)
    thread = threading.Thread(target=worker.run)
    thread.start()
    try:
        qtbot.waitUntil(lambda: len(events) == 1, timeout=1000)
        worker.set_speed(20)
        qtbot.waitUntil(lambda: not thread.is_alive(), timeout=1200)
        assert [event.samples for event in events] == [(1.0,), (2.0,)]
    finally:
        worker.stop()
        thread.join(2)


def test_csv_index_and_paused_seek_preserve_original_time(qtbot, tmp_path):
    import threading
    from qx_stc_isp.vofa.playback import CsvReplayWorker
    from qx_stc_isp.vofa.replay_index import RecordingIndex
    source = tmp_path / "samples.csv"
    source.write_text("timestamp_utc,channel_index,channel_name,value\n"
                      "2026-01-01T00:00:00+00:00,0,CH1,1\n"
                      "2026-01-01T00:00:05+00:00,0,CH1,2\n"
                      "2026-01-01T00:00:10+00:00,0,CH1,3\n", encoding="utf-8")
    index = RecordingIndex.build(source)
    assert index.kind == "csv" and index.record_count == 3 and index.duration_seconds == 10
    worker = CsvReplayWorker(source, speed=100)
    worker.set_paused(True)
    worker.seek_seconds(5)
    frames, positions = [], []
    worker.generation_events.connect(lambda generation, batch: frames.extend((generation, item) for item in batch))
    worker.timeline_changed.connect(lambda offset, duration, generation: positions.append(offset))
    thread = threading.Thread(target=worker.run)
    thread.start()
    try:
        qtbot.waitUntil(lambda: positions == [5.0], timeout=2000)
        assert not frames
        worker.set_paused(False)
        # Thread termination does not imply queued Qt callbacks were delivered.
        qtbot.waitUntil(lambda: not thread.is_alive() and len(frames) == 2, timeout=2000)
        assert [item.samples for _, item in frames] == [(2.0,), (3.0,)]
        assert {generation for generation, _ in frames} == {1}
        assert frames[0][1].source_timestamp == datetime(2026, 1, 1, 0, 0, 5, tzinfo=timezone.utc)
        with pytest.raises(ValueError):
            worker.enqueue_send(b"never")
    finally:
        worker.stop()
        thread.join(2)
