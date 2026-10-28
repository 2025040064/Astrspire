"""Offline sample-file playback tests; serial/network access is forbidden."""

from __future__ import annotations

import csv
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest
from PySide6.QtCore import Qt

from qx_stc_isp.vofa import CsvRecorder, TelemetryEvent
from qx_stc_isp.vofa.playback import (
    CSV_HEADER, CsvReplayError, CsvReplayWorker, iter_csv_frames,
)


@pytest.fixture(autouse=True)
def no_devices(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("CSV playback must not access a device or network")

    monkeypatch.setattr("serial.Serial", forbidden)
    monkeypatch.setattr("socket.socket", forbidden)


def make_csv(path, frames, *, names=("电压", "电流")):
    with CsvRecorder(path, names) as recorder:
        for timestamp, values in frames:
            recorder.record(TelemetryEvent(raw=b"", samples=values), at=timestamp)
    return path


def raw_csv(path, rows, *, header=CSV_HEADER):
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(header)
        writer.writerows(rows)
    return path


def test_iter_preserves_duplicate_timestamps_and_channel_names(tmp_path):
    now = datetime(2026, 10, 21, tzinfo=timezone.utc)
    path = make_csv(tmp_path / "samples.csv", [(now, (1.0, 2.0)), (now, (3.0,)), (now + timedelta(milliseconds=1), (4.0, 5.0))])
    frames = list(iter_csv_frames(path))
    assert [frame.samples for frame in frames] == [(1.0, 2.0), (3.0,), (4.0, 5.0)]
    assert frames[0].names == ("电压", "电流")
    assert frames[0].timestamp == frames[1].timestamp
    assert frames[0].bytes_read < frames[-1].bytes_read <= frames[-1].file_size


def test_constructor_and_early_stop_do_not_open_file(tmp_path, monkeypatch):
    worker = CsvReplayWorker(tmp_path / "missing.csv")
    monkeypatch.setattr("pathlib.Path.open", lambda *_a, **_k: pytest.fail("construction/early cancel opened a file"))
    states, finished = [], []
    worker.status.connect(lambda kind, text: states.append((kind, text)))
    worker.finished.connect(lambda: finished.append(True))
    worker.stop()
    worker.run()
    assert states[-1][0] == "stopped" and finished == [True]
    assert not worker.can_send and worker.channel_names == ()
    assert worker.clear_pending() == 0
    with pytest.raises(ValueError, match="不能发送"):
        worker.enqueue_send(b"x")


@pytest.mark.parametrize("speed", [0, -1, 0.001, 1001, float("nan"), float("inf"), True, "1"])
def test_rejects_bad_speed(tmp_path, speed):
    with pytest.raises(ValueError):
        CsvReplayWorker(tmp_path / "a.csv", speed=speed)


def test_rejects_non_boolean_options(tmp_path):
    with pytest.raises(ValueError):
        CsvReplayWorker(tmp_path / "a.csv", loop=1)
    worker = CsvReplayWorker(tmp_path / "a.csv")
    with pytest.raises(ValueError):
        worker.set_paused(1)


@pytest.mark.parametrize("rows,match", [
    ([("2026-10-21T00:00:00+00:00", "1", "x", "1")], "通道 0"),
    ([("2026-10-21T00:00:00+00:00", "0", "x", "1"), ("2026-10-21T00:00:00+00:00", "2", "x", "2")], "连续递增"),
    ([("2026-10-21T00:00:00+00:00", "256", "x", "1")], "0–255"),
    ([("2026-10-21T00:00:00+00:00", "00", "x", "1")], "规范整数"),
    ([("2026-10-21T00:00:00", "0", "x", "1")], "时区"),
    ([("not-a-time", "0", "x", "1")], "时间格式"),
    ([("2026-10-21T00:00:00+00:00", "0", "x", "nan")], "NaN"),
    ([("2026-10-21T00:00:00+00:00", "0", "x", "inf")], "无穷大"),
    ([("2026-10-21T00:00:00+00:00", "0", "x", "oops")], "数值无效"),
    ([("2026-10-21T00:00:00+00:00", "0", "x" * 129, "1")], "长度上限"),
    ([("2026-10-21T00:00:00+00:00", "0", "x")], "四列"),
    ([("2026-10-21T00:00:01+00:00", "0", "x", "1"), ("2026-10-21T00:00:00+00:00", "0", "x", "2")], "倒退"),
    ([("2026-10-21T00:00:00+00:00", "0", "x", "1"), ("2026-10-21T00:00:01+00:00", "1", "x", "2")], "时间必须一致"),
])
def test_rejects_malformed_rows(tmp_path, rows, match):
    path = raw_csv(tmp_path / "bad.csv", rows)
    with pytest.raises(CsvReplayError, match=match):
        list(iter_csv_frames(path))


def test_rejects_empty_header_only_wrong_header_and_invalid_encoding(tmp_path):
    path = tmp_path / "bad.csv"
    for content, exception in [("", CsvReplayError), (",".join(CSV_HEADER) + "\n", CsvReplayError), ("x,y\n", CsvReplayError)]:
        path.write_text(content, encoding="utf-8")
        with pytest.raises(exception):
            list(iter_csv_frames(path))
    path.write_bytes(b"\xff\xff")
    with pytest.raises(UnicodeDecodeError):
        list(iter_csv_frames(path))


def test_size_and_line_limits_are_enforced_before_allocating_whole_file(tmp_path, monkeypatch):
    path = tmp_path / "limits.csv"
    path.write_text("x" * 100, encoding="utf-8")
    monkeypatch.setattr("qx_stc_isp.vofa.playback.MAX_FILE_BYTES", 16)
    with pytest.raises(CsvReplayError, match="128 MiB"):
        list(iter_csv_frames(path))
    monkeypatch.setattr("qx_stc_isp.vofa.playback.MAX_FILE_BYTES", 10000)
    monkeypatch.setattr("qx_stc_isp.vofa.playback.MAX_LINE_CHARS", 32)
    with pytest.raises(CsvReplayError, match="单行"):
        list(iter_csv_frames(path))


def test_generator_closes_source_when_consumer_stops(tmp_path):
    now = datetime.now(timezone.utc)
    path = make_csv(tmp_path / "close.csv", [(now, (1.0,)), (now, (2.0,))])
    generator = iter_csv_frames(path)
    assert next(generator).samples == (1.0,)
    generator.close()
    # Windows would reject rename when the active read handle was retained.
    renamed = path.with_name("closed.csv")
    path.rename(renamed)
    assert renamed.exists()


def test_worker_completes_and_emits_no_raw_or_tx(tmp_path):
    now = datetime.now(timezone.utc)
    path = make_csv(tmp_path / "play.csv", [(now, (1.0, 2.0)), (now, (3.0, 4.0))])
    worker = CsvReplayWorker(path, speed=1000)
    events, statuses, progress, raw, tx, names = [], [], [], [], [], []
    worker.events.connect(lambda batch: events.extend(batch))
    worker.status.connect(lambda kind, text: statuses.append((kind, text)))
    worker.progress.connect(progress.append)
    worker.raw.connect(raw.append)
    worker.transmitted.connect(tx.append)
    worker.names_changed.connect(names.append)
    worker.run()
    assert [event.samples for event in events] == [(1.0, 2.0), (3.0, 4.0)]
    assert statuses[0][0] == "playback" and statuses[-1][0] == "completed"
    assert progress[-1] == 100
    assert raw == tx == []
    assert worker.channel_names == ("电压", "电流")
    assert names == [("电压", "电流")]
    with pytest.raises(RuntimeError):
        worker.run()


def test_worker_error_never_reports_completed_and_releases_file(tmp_path):
    path = raw_csv(tmp_path / "error.csv", [])
    worker = CsvReplayWorker(path)
    statuses, done = [], []
    worker.status.connect(lambda kind, text: statuses.append((kind, text)))
    worker.finished.connect(lambda: done.append(True))
    worker.run()
    assert statuses[-1][0] == "error" and done == [True]
    assert not any(kind == "completed" for kind, _ in statuses)
    path.rename(path.with_name("released.csv"))


def test_worker_delivers_first_frame_before_long_wait_and_cancels_promptly(tmp_path):
    now = datetime.now(timezone.utc)
    path = make_csv(tmp_path / "gap.csv", [(now, (1.0,)), (now + timedelta(hours=1), (2.0,))])
    worker = CsvReplayWorker(path)
    first = threading.Event()
    events, statuses = [], []
    worker.events.connect(lambda batch: (events.extend(batch), first.set()), Qt.DirectConnection)
    worker.status.connect(lambda kind, text: statuses.append((kind, text)), Qt.DirectConnection)
    thread = threading.Thread(target=worker.run)
    thread.start()
    try:
        assert first.wait(0.5)
        start = time.monotonic()
        worker.stop()
        thread.join(0.3)
        assert not thread.is_alive()
        assert time.monotonic() - start < 0.15
        assert [event.samples for event in events] == [(1.0,)]
        assert statuses[-1][0] == "stopped"
    finally:
        worker.stop()
        thread.join(1)


def test_paused_clock_does_not_consume_gap_and_resume_continues(tmp_path):
    now = datetime.now(timezone.utc)
    path = make_csv(tmp_path / "pause.csv", [(now, (1.0,)), (now + timedelta(milliseconds=150), (2.0,))])
    worker = CsvReplayWorker(path)
    first, second = threading.Event(), threading.Event()
    events = []

    def receive(batch):
        events.extend(batch)
        if len(events) == 1:
            worker.set_paused(True)
            first.set()
        else:
            second.set()

    worker.events.connect(receive, Qt.DirectConnection)
    thread = threading.Thread(target=worker.run)
    thread.start()
    try:
        assert first.wait(0.5)
        assert not second.wait(0.2)
        assert worker.paused
        resumed = time.monotonic()
        worker.set_paused(False)
        assert second.wait(0.6)
        assert time.monotonic() - resumed >= 0.1
        thread.join(0.5)
        assert not thread.is_alive()
    finally:
        worker.stop()
        thread.join(1)


def test_start_paused_and_stop_does_not_emit_samples(tmp_path):
    now = datetime.now(timezone.utc)
    path = make_csv(tmp_path / "paused.csv", [(now, (1.0,))])
    worker = CsvReplayWorker(path)
    worker.set_paused(True)
    events = []
    worker.events.connect(lambda batch: events.extend(batch), Qt.DirectConnection)
    thread = threading.Thread(target=worker.run)
    thread.start()
    time.sleep(0.06)
    worker.stop()
    thread.join(0.3)
    assert not thread.is_alive() and events == []


def test_loop_reopens_file_and_stops_without_completed(tmp_path):
    now = datetime.now(timezone.utc)
    path = make_csv(tmp_path / "loop.csv", [(now, (1.0,))])
    worker = CsvReplayWorker(path, loop=True)
    samples, statuses = [], []

    def receive(batch):
        samples.extend(batch)
        if len(samples) >= 3:
            worker.stop()

    worker.events.connect(receive)
    worker.status.connect(lambda kind, text: statuses.append((kind, text)))
    worker.run()
    assert len(samples) == 3
    assert statuses[-1][0] == "stopped"
    assert not any(kind == "completed" for kind, _ in statuses)
    path.rename(path.with_name("loop-closed.csv"))


def test_large_same_time_recording_has_bounded_batches(tmp_path):
    now = datetime.now(timezone.utc)
    path = make_csv(tmp_path / "batch.csv", [(now, (float(index),)) for index in range(145)])
    worker = CsvReplayWorker(path, speed=1000)
    batches = []
    worker.events.connect(lambda batch: batches.append(batch))
    worker.run()
    assert sum(len(batch) for batch in batches) == 145
    assert max(len(batch) for batch in batches) <= 64


def test_equal_time_name_changes_emit_separate_consistent_batches(tmp_path):
    stamp = "2026-10-21T00:00:00+00:00"
    path = raw_csv(tmp_path / "names.csv", [
        (stamp, "0", "第一", "1"),
        (stamp, "0", "第二", "2"),
        (stamp, "0", "第三", "3"),
    ])
    worker = CsvReplayWorker(path)
    seen, names = [], []
    worker.names_changed.connect(names.append)
    worker.events.connect(lambda events: seen.append((worker.channel_names, [event.samples for event in events])))
    worker.run()
    assert seen == [(("第一",), [(1.0,)]), (("第二",), [(2.0,)]), (("第三",), [(3.0,)])]
    assert names == [("第一",), ("第二",), ("第三",)]


def test_replay_export_preserves_original_microsecond_time_and_raw_values(tmp_path):
    original = datetime(2026, 10, 21, 1, 2, 3, 456789, tzinfo=timezone.utc)
    source = raw_csv(tmp_path / "original.csv", [
        (original.isoformat(), "0", "电压", "1.25"),
        ((original + timedelta(microseconds=123)).isoformat(), "0", "电压", "-7.5"),
    ])
    worker = CsvReplayWorker(source, speed=1000)
    replayed = []
    worker.events.connect(lambda batch: replayed.extend(batch))
    worker.run()
    target = tmp_path / "export.csv"
    with CsvRecorder(target) as recorder:
        for event in replayed:
            recorder.record(event)
    frames = list(iter_csv_frames(target))
    assert [(frame.timestamp, frame.samples, frame.names) for frame in frames] == [
        (original, (1.25,), ("电压",)),
        (original + timedelta(microseconds=123), (-7.5,), ("电压",)),
    ]


def test_replay_backpressure_blocks_next_batch_until_ack_and_stop_releases(tmp_path):
    stamp = datetime(2026, 10, 21, tzinfo=timezone.utc)
    source = make_csv(tmp_path / "ack.csv", [(stamp, (float(index),)) for index in range(145)])
    worker = CsvReplayWorker(source, await_ack=True)
    batches = []
    first = threading.Event()
    worker.events.connect(lambda batch: (batches.append(batch), first.set()), Qt.DirectConnection)
    thread = threading.Thread(target=worker.run)
    thread.start()
    try:
        assert first.wait(1)
        time.sleep(0.08)
        assert len(batches) == 1
        worker.ack_batch()
        limit = time.monotonic() + 1
        while len(batches) < 2 and time.monotonic() < limit:
            time.sleep(0.005)
        assert len(batches) == 2
        worker.stop()
        thread.join(1)
        assert not thread.is_alive()
    finally:
        worker.stop()
        thread.join(1)
