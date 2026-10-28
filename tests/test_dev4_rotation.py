from datetime import datetime, timezone
import json
import pytest


def test_raw_rotation_keeps_legacy_parts_and_session_chronology(tmp_path):
    from qx_stc_isp.vofa.rotating_capture import RotatingStreamRecorder, iter_recording_records
    path = tmp_path / "capture.jsonl"
    sink = RotatingStreamRecorder(path, max_part_bytes=300)
    sink.open()
    stamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for index in range(5):
        sink.record("RX" if index % 2 == 0 else "TX", bytes([index]) * 20, at=stamp)
    sink.close()
    records = list(iter_recording_records(sink.manifest_path))
    assert len(sink.parts) >= 3
    assert [record.payload[0] for record in records] == [0, 1, 2, 3, 4]
    assert records[-1].bytes_read == records[-1].file_size
    assert list(iter_recording_records(path))[0].payload == bytes(20)


def test_session_tamper_and_parent_traversal_are_rejected(tmp_path):
    from qx_stc_isp.vofa.rotating_capture import RotatingStreamRecorder, iter_recording_records
    path = tmp_path / "capture.jsonl"
    sink = RotatingStreamRecorder(path)
    sink.open()
    sink.record("RX", b"original")
    sink.close()
    manifest = json.loads(sink.manifest_path.read_text(encoding="utf-8"))
    path.write_bytes(path.read_bytes().replace(b"6f726967696e616c", b"74616d7065726564"))
    with pytest.raises(ValueError, match="SHA-256"):
        list(iter_recording_records(sink.manifest_path))
    manifest["parts"][0]["path"] = "../outside.jsonl"
    sink.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="路径"):
        list(iter_recording_records(sink.manifest_path))


def test_text_log_rotates_without_overwriting_first_part(tmp_path):
    from qx_stc_isp.vofa.text_log import RotatingTextLogSink
    sink = RotatingTextLogSink(tmp_path / "text.log", max_bytes=8)
    sink.open()
    sink.record("first\n")
    sink.record("second\n")
    sink.close()
    assert (tmp_path / "text.log").read_text(encoding="utf-8") == "first\n"
    assert (tmp_path / "text.part0002.log").read_text(encoding="utf-8") == "second\n"
