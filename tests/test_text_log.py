"""Bounded UTF-8 text sink, wrapped by the same asynchronous writer seam."""

from pathlib import Path

import pytest

from qx_stc_isp.vofa.recorder import AsyncRecorder
from qx_stc_isp.vofa.text_log import TextLogSink


def test_text_sink_creates_parent_exclusively_and_preserves_exact_lines(tmp_path):
    path = tmp_path / "logs" / "monitor.log"
    recorder = AsyncRecorder(TextLogSink(path))
    recorder.open()
    assert recorder.record("[RX] 第一行\n")
    assert recorder.record("[TX] 01 FF\n")
    recorder.close()
    assert recorder.wait_closed(2)
    assert recorder.statistics.error is None
    assert path.read_bytes() == "[RX] 第一行\n[TX] 01 FF\n".encode("utf-8")
    duplicate = TextLogSink(path)
    with pytest.raises(FileExistsError):
        duplicate.open()
    assert path.read_text(encoding="utf-8").startswith("[RX]")


def test_text_sink_size_limit_stops_recording_without_overwriting_partial_file(tmp_path):
    path = tmp_path / "limited.log"
    sink = TextLogSink(path, max_bytes=4)
    recorder = AsyncRecorder(sink)
    recorder.open()
    assert recorder.record("abc\n")
    assert recorder.record("overflow\n")
    recorder.close()
    assert recorder.wait_closed(2)
    stats = recorder.statistics
    assert (stats.saved, stats.unsaved) == (1, 1)
    assert "上限" in stats.error
    assert path.read_bytes() == b"abc\n"


def test_text_write_failure_is_reported_by_async_recorder(tmp_path, monkeypatch):
    class FailedStream:
        def write(self, _value):
            raise OSError("disk disconnected")

        def flush(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(Path, "open", lambda *_args, **_kwargs: FailedStream())
    recorder = AsyncRecorder(TextLogSink(tmp_path / "failed.log"))
    recorder.open()
    assert recorder.record("not saved\n")
    recorder.close()
    assert recorder.wait_closed(2)
    assert recorder.statistics.unsaved == 1
    assert "disk disconnected" in recorder.statistics.error
