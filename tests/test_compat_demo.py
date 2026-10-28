"""Offline VOFA-style Demo: file boundaries, strict conversion, no transport."""
from __future__ import annotations

import struct
import threading
import time

import pytest
from PySide6.QtCore import Qt

from qx_stc_isp.vofa.compat_demo import CompatDemoConfig, DemoSchedule, encode_csv_file, encode_jpg_file


def test_compat_demo_configuration_is_inert_and_round_trips():
    config = CompatDemoConfig.from_dict({
        "decoder": "JustFloat",
        "csv": {"directory": "missing/csv", "enabled": True, "period_ms": 25, "loop": True},
        "jpg": {"directory": "missing/jpg", "enabled": True, "period_ms": 40, "loop": False},
    })
    assert config.csv == DemoSchedule("missing/csv", True, 25, True)
    assert CompatDemoConfig.from_dict(config.to_dict()) == config
    assert not CompatDemoConfig.from_dict({}).csv.enabled


def test_compat_demo_whole_csv_envelopes_match_recorded_static_format(tmp_path):
    csv = tmp_path / "values.csv"
    csv.write_bytes(b"1,2\n3,4\n")
    assert encode_csv_file(csv, "FireWater") == b"1,2\n3,4\n"
    assert encode_csv_file(csv, "JustFloat") == bytes.fromhex(
        "00 00 80 3F 00 00 00 40 00 00 80 7F 00 00 40 40 00 00 80 40 00 00 80 7F")


def test_firewater_demo_preserves_whole_csv_file_including_empty_lines(tmp_path):
    csv = tmp_path / "values.csv"
    csv.write_bytes(b"1,2\r\n\r\n3,4\n")
    assert encode_csv_file(csv, "FireWater") == b"1,2\r\n\r\n3,4\n"


def test_compat_demo_jpg_headers_follow_observed_dimensions_not_actual_pixels(tmp_path):
    jpg = tmp_path / "photo.jpg"
    jpg.write_bytes(b"\xff\xd8test\xff\xd9")
    assert encode_jpg_file(jpg, "FireWater") == b"image:0,8,30,40,27\n\xff\xd8test\xff\xd9"
    assert encode_jpg_file(jpg, "JustFloat") == struct.pack(
        "<7I", 0, 8, 200, 300, 27, 0x7f800000, 0x7f800000) + jpg.read_bytes()


@pytest.mark.parametrize("text", [b"1,bad\n", b"1,nan\n", b"1,inf\n", b"1,,2\n"])
def test_compat_demo_invalid_numbers_are_errors_not_zero(tmp_path, text):
    csv = tmp_path / "bad.csv"
    csv.write_bytes(text)
    for decoder in ("FireWater", "JustFloat"):
        with pytest.raises(ValueError):
            encode_csv_file(csv, decoder)


def test_explicit_local_file_boundary_releases_last_justfloat_frame():
    from qx_stc_isp.vofa.decoders import create_decoder
    decoder = create_decoder("JustFloatImage")
    assert decoder.feed(bytes.fromhex("00 00 80 3F 00 00 80 7F")) == []
    assert [event.samples for event in decoder.finish_file()] == [(1.0,)]
    assert decoder.buffered_bytes == 0


@pytest.mark.parametrize("decoder", ["FireWater", "JustFloat"])
def test_parallel_demo_uses_natural_file_order_and_emits_all_file_rows(tmp_path, decoder):
    from qx_stc_isp.vofa.compat_demo import CompatDemoWorker
    csv, jpg = tmp_path / "csv", tmp_path / "jpg"
    csv.mkdir()
    jpg.mkdir()
    (csv / "10.csv").write_bytes(b"10\n11\n")
    (csv / "2.csv").write_bytes(b"2\n3\n")
    (jpg / "1.jpg").write_bytes(b"\xff\xd8test\xff\xd9")
    worker = CompatDemoWorker(CompatDemoConfig(decoder,
        DemoSchedule(str(csv), True, 1), DemoSchedule(str(jpg), True, 2)))
    events, statuses, raw = [], [], []
    worker.events.connect(lambda batch: (events.extend(batch), worker.ack_batch()), Qt.DirectConnection)
    worker.raw.connect(raw.append, Qt.DirectConnection)
    worker.status.connect(lambda code, message: statuses.append((code, message)), Qt.DirectConnection)
    worker.run()
    assert [event.samples for event in events if event.samples] == [(2.,), (3.,), (10.,), (11.,)]
    assert [event.image for event in events if event.image] == [b"\xff\xd8test\xff\xd9"]
    assert not worker.can_send and not worker.connected
    assert statuses[-1][0] == "completed" and raw


def test_demo_one_source_failure_does_not_stop_the_other(tmp_path):
    from qx_stc_isp.vofa.compat_demo import CompatDemoWorker
    csv, jpg = tmp_path / "csv", tmp_path / "jpg"
    csv.mkdir()
    jpg.mkdir()
    (csv / "bad.csv").write_bytes(b"bad\n")
    (jpg / "1.jpg").write_bytes(b"\xff\xd8test\xff\xd9")
    worker = CompatDemoWorker(CompatDemoConfig("FireWater",
        DemoSchedule(str(csv), True, 1), DemoSchedule(str(jpg), True, 2)))
    events, statuses = [], []
    worker.events.connect(lambda batch: (events.extend(batch), worker.ack_batch()), Qt.DirectConnection)
    worker.status.connect(lambda code, message: statuses.append((code, message)), Qt.DirectConnection)
    worker.run()
    assert len([event for event in events if event.image]) == 1
    assert any(code == "notice" and "CSV" in message and "失败" in message for code, message in statuses)
    assert statuses[-1][0] == "completed_with_errors"


def test_demo_cancel_interrupts_unacknowledged_batch_without_sending(tmp_path):
    from qx_stc_isp.vofa.compat_demo import CompatDemoWorker
    (tmp_path / "1.csv").write_bytes(b"1\n2\n")
    worker = CompatDemoWorker(CompatDemoConfig(csv=DemoSchedule(str(tmp_path), True, 1, True)))
    ready = threading.Event()
    worker.events.connect(lambda _batch: ready.set(), Qt.DirectConnection)
    thread = threading.Thread(target=worker.run)
    thread.start()
    assert ready.wait(2)
    with pytest.raises(ValueError, match="不能.*发送"):
        worker.enqueue_send(b"test")
    worker.stop()
    thread.join(2)
    assert not thread.is_alive()


def test_demo_csv_pause_does_not_pause_jpg_and_can_resume(tmp_path):
    from qx_stc_isp.vofa.compat_demo import CompatDemoWorker
    csv, jpg = tmp_path / "csv", tmp_path / "jpg"
    csv.mkdir()
    jpg.mkdir()
    (csv / "1.csv").write_bytes(b"1\n")
    (jpg / "1.jpg").write_bytes(b"\xff\xd8test\xff\xd9")
    worker = CompatDemoWorker(CompatDemoConfig("JustFloat",
        DemoSchedule(str(csv), True, 1), DemoSchedule(str(jpg), True, 1)))
    worker.set_source_paused("csv", True)
    image_ready, samples_ready, events = threading.Event(), threading.Event(), []
    def receive(batch):
        events.extend(batch)
        if any(event.image for event in batch):
            image_ready.set()
        if any(event.samples for event in batch):
            samples_ready.set()
        worker.ack_batch()
    worker.events.connect(receive, Qt.DirectConnection)
    thread = threading.Thread(target=worker.run)
    thread.start()
    assert image_ready.wait(2) and not samples_ready.is_set()
    worker.set_source_paused("csv", False)
    assert samples_ready.wait(2)
    thread.join(2)
    assert not thread.is_alive()


def test_demo_backpressure_preserves_more_than_one_batch_of_rows(tmp_path):
    from qx_stc_isp.vofa.compat_demo import CompatDemoWorker
    (tmp_path / "1.csv").write_text("".join(f"{index}\n" for index in range(1000)), encoding="utf-8")
    worker = CompatDemoWorker(CompatDemoConfig(csv=DemoSchedule(str(tmp_path), True, 1)))
    values, batch_sizes = [], []
    def receive(batch):
        batch_sizes.append(len(batch))
        values.extend(event.samples[0] for event in batch if event.samples)
        time.sleep(.001)
        worker.ack_batch()
    worker.events.connect(receive, Qt.DirectConnection)
    worker.run()
    assert values == list(range(1000))
    assert max(batch_sizes) <= 128


def test_stopping_one_demo_source_leaves_the_other_running(tmp_path):
    from qx_stc_isp.vofa.compat_demo import CompatDemoWorker
    csv, jpg = tmp_path / "csv", tmp_path / "jpg"
    csv.mkdir()
    jpg.mkdir()
    (csv / "1.csv").write_bytes(b"1\n")
    (jpg / "1.jpg").write_bytes(b"\xff\xd8test\xff\xd9")
    worker = CompatDemoWorker(CompatDemoConfig("FireWater",
        DemoSchedule(str(csv), True, 1, True), DemoSchedule(str(jpg), True, 1)))
    events = []
    worker.stop_source("csv")
    worker.events.connect(lambda batch: (events.extend(batch), worker.ack_batch()), Qt.DirectConnection)
    worker.run()
    assert len([event for event in events if event.image]) == 1
    assert not any(event.samples for event in events)


def test_demo_reports_each_source_liveness_for_gui_controls(tmp_path):
    from qx_stc_isp.vofa.compat_demo import CompatDemoWorker
    (tmp_path / "1.csv").write_bytes(b"1\n")
    worker = CompatDemoWorker(CompatDemoConfig(csv=DemoSchedule(str(tmp_path), True, 1, True)))
    alive = []
    def receive(_batch):
        alive.append(worker.source_active("csv"))
        worker.stop_source("csv")
        alive.append(worker.source_active("csv"))
        worker.ack_batch()
    worker.events.connect(receive, Qt.DirectConnection)
    worker.run()
    assert alive == [True, False]
    assert not worker.source_active("jpg")
