"""Receive diagnosis uses virtual time and fake serial devices only."""
from collections import deque
from types import SimpleNamespace
import struct

import pytest

from qx_stc_isp.port_lease import PortLeaseManager
from qx_stc_isp.vofa import serial_link


def run_link(monkeypatch, chunks, decoder="JustFloat", **line_settings):
    clock = [0.0]
    pending = deque(chunks)
    statuses, batches, errors = [], [], []

    class Transport:
        closed = False
        writes = []

        def open(self, baudrate):
            assert baudrate == 115200

        def read_some(self, size, timeout):
            clock[0] += 1
            if not pending:
                worker.stop()
                return b""
            return pending.popleft()

        def cancel(self):
            pass

        def close(self):
            self.closed = True

        def write(self, *args, **kwargs):
            raise AssertionError("Diagnosis must not send commands")

    transport = Transport()
    leases = PortLeaseManager()
    monkeypatch.setattr(serial_link, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    worker = serial_link.SerialLinkWorker("COM_FAKE", 115200, decoder,
        transport_factory=lambda _: transport, lease_manager=leases, **line_settings)
    worker.status.connect(lambda kind, message: statuses.append((kind, message)))
    worker.events.connect(batches.extend)
    worker.error.connect(errors.append)
    worker.run()
    assert transport.closed
    assert leases.owner_for("COM_FAKE") is None
    assert not errors
    assert worker.statistics.accepted_bytes == 0
    return worker, statuses, batches


def test_opening_without_rx_reports_waiting_once_and_does_not_disconnect(monkeypatch):
    worker, statuses, _ = run_link(monkeypatch, [b""] * 8)
    waiting = [message for kind, message in statuses if kind == "waiting_data"]
    assert len(waiting) == 1
    assert "115200" in waiting[0]
    assert "没有收到" in waiting[0]
    assert worker.receive_statistics.received_bytes == 0
    assert worker.receive_statistics.numeric_frames == 0


@pytest.mark.parametrize("dtr,rts", [(False, False), (True, False), (False, True)])
def test_connection_message_names_requested_format_and_lines(monkeypatch, dtr, rts):
    _, statuses, _ = run_link(monkeypatch, [b""], dtr=dtr, rts=rts)
    connected = next(text for kind, text in statuses if kind == "connected")
    assert "8N1" in connected
    assert f"DTR={int(dtr)}" in connected and f"RTS={int(rts)}" in connected
    assert "请求" in connected  # Not a physical voltage measurement.


def test_idle_hint_distinguishes_idle_from_firmware_and_wiring_fault(monkeypatch):
    _, statuses, _ = run_link(monkeypatch, [b""] * 8)
    hint = next(text for kind, text in statuses if kind == "waiting_data")
    assert "空闲" in hint and "固件" in hint and "不会自动发送" in hint


def test_noise_reports_format_mismatch_without_claiming_valid_data(monkeypatch):
    worker, statuses, _ = run_link(monkeypatch, [b"\x00"] * 7)
    assert sum(kind == "unparsed_data" for kind, _ in statuses) == 1
    assert not any(kind == "receiving" for kind, _ in statuses)
    assert worker.receive_statistics.received_bytes == 7
    assert worker.receive_statistics.numeric_frames == 0


def test_fragmented_frame_recovers_and_counts_full_frames_not_chunks(monkeypatch):
    packet = struct.pack("<2f", 1, -2) + b"\x00\x00\x80\x7f"
    worker, statuses, events = run_link(monkeypatch, [b""] * 4 + [packet[:3], packet[3:]])
    assert any(kind == "waiting_data" for kind, _ in statuses)
    assert sum(kind == "receiving" for kind, _ in statuses) == 1
    assert worker.receive_statistics.received_bytes == len(packet)
    assert worker.receive_statistics.numeric_frames == 1
    assert worker.receive_statistics.decode_errors == 0
    assert [event.samples for event in events if event.samples] == [(1, -2)]


def test_raw_mode_accepts_arbitrary_bytes_without_numeric_warning(monkeypatch):
    worker, statuses, _ = run_link(monkeypatch, [b"\x00"] * 7, "RawData")
    assert not any(kind == "unparsed_data" for kind, _ in statuses)
    assert any(kind == "receiving" for kind, _ in statuses)
    assert worker.receive_statistics.received_bytes == 7
    assert worker.receive_statistics.numeric_frames == 0


@pytest.mark.parametrize("decoder,payload", [
    ("JustFloat", b"x\x00\x00\x80\x7f"),
    ("FireWater", b"nan,1\n"),
])
def test_decode_error_is_not_a_sample(monkeypatch, decoder, payload):
    worker, _, events = run_link(monkeypatch, [payload], decoder)
    assert worker.receive_statistics.decode_errors == 1
    assert worker.receive_statistics.numeric_frames == 0
    assert len([event for event in events if event.error]) == 1


def test_firewater_plain_text_remains_text_not_a_decoder_error(monkeypatch):
    worker, _, events = run_link(monkeypatch, [b"hello\n"], "FireWater")
    assert worker.receive_statistics.decode_errors == 0
    assert worker.receive_statistics.numeric_frames == 0
    assert events[0].text == "hello"
