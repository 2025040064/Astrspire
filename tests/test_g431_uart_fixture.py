"""Validate the fixture's data and handshake offline; never opens a COM port."""
import csv
import importlib.util
from pathlib import Path
import struct
from itertools import count
from types import SimpleNamespace

import pytest

from qx_stc_isp.vofa.decoders import JustFloatDecoder
from qx_stc_isp.vofa.model import TelemetryEvent
from qx_stc_isp.vofa.recorder import AsyncRecorder, CsvRecorder

spec = importlib.util.spec_from_file_location("g431_fixture", Path(__file__).resolve().parents[1] / "tools/g431_uart_acceptance.py")
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


def test_fixture_handshake_uses_the_same_lines_as_monitor(monkeypatch):
    calls = []
    monkeypatch.setattr(fixture, "PySerialTransport", lambda device, **kw: calls.append((device, kw)))
    fixture.make_fixture_transport("COM_FAKE")
    assert calls == [("COM_FAKE", {"dtr": False, "rts": False})]


def values(sequence, tick=None):
    row = [float(sequence), float(sequence * 10 if tick is None else tick)]
    row += [(sequence % 200 - 100) * (channel - 1) / 100 for channel in range(2, 15)]
    return row + [0.0]


def test_fragmented_16_channel_stream_and_async_csv_round_trip(tmp_path):
    payload = b"".join(struct.pack("<16f", *values(sequence)) + b"\x00\x00\x80\x7f" for sequence in range(1, 201))
    decoder, verifier = JustFloatDecoder(), fixture.FrameVerifier()
    path = tmp_path / "samples.csv"
    recorder = AsyncRecorder(CsvRecorder(path))
    recorder.open()
    for offset in range(0, len(payload), 13):
        for event in decoder.feed(payload[offset:offset + 13]):
            assert not event.error
            verifier.observe(event.samples)
            assert recorder.record(event)
    assert recorder.close(timeout=5)
    assert verifier.passed and verifier.frames == 200
    assert recorder.statistics.accepted == recorder.statistics.saved == 200
    assert recorder.statistics.unsaved == 0
    assert fixture.check_csv(path).report() == verifier.report()


@pytest.mark.parametrize("sequences,counter", [([1, 3], "gaps"), ([1, 1], "duplicates"), ([2, 1], "out_of_order")])
def test_sequence_faults_are_not_silently_accepted(sequences, counter):
    verifier = fixture.FrameVerifier()
    for sequence in sequences:
        verifier.observe(values(sequence))
    assert not verifier.passed
    assert verifier.report()[counter] == 1


@pytest.mark.parametrize("channel,change,counter", [(2, 1, "value_errors"), (15, 1, "uart_errors"), (1, -100, "value_errors")])
def test_wrong_values_and_uart_errors_are_reported(channel, change, counter):
    verifier = fixture.FrameVerifier()
    row = values(1)
    row[channel] += change
    verifier.observe(row)
    assert not verifier.passed
    assert verifier.report()[counter] >= 1


def test_unknown_firmware_never_receives_start_or_ping():
    class Transport:
        written = []
        closed = False
        def open(self, baudrate):
            assert baudrate == 115200
        def purge_buffers(self):
            pass
        def write(self, payload):
            self.written.append(payload)
        def read_some(self, *_args):
            return b"UNKNOWN\r\n"
        def close(self):
            self.closed = True
    transport = Transport()
    with pytest.raises(ValueError, match="did not identify"):
        fixture.handshake(transport)
    assert transport.closed
    assert transport.written == [b"ASTR:HELLO\n"]


@pytest.mark.parametrize("response,expected", [(b"", "timeout: received 0"),
                                               (b"PART", "timeout: received 4"),
                                               (b"X" * 128, "oversized reply: received 128")])
def test_handshake_failure_reports_observed_byte_count(monkeypatch, response, expected):
    clock = count(0, 0.5)
    monkeypatch.setattr(fixture, "time", SimpleNamespace(monotonic=lambda: next(clock)))

    class Transport:
        closed = False
        written = []
        response_pending = response

        def open(self, _baudrate):
            pass

        def purge_buffers(self):
            pass

        def write(self, payload):
            self.written.append(payload)

        def read_some(self, limit, _timeout):
            chunk = self.response_pending[:limit]
            self.response_pending = self.response_pending[limit:]
            return chunk

        def close(self):
            self.closed = True

    transport = Transport()
    with pytest.raises(ValueError, match=expected):
        fixture.handshake(transport)
    assert transport.closed and transport.written == [b"ASTR:HELLO\n"]


def test_csv_missing_channel_is_rejected(tmp_path):
    path = tmp_path / "bad.csv"
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(("channel_index", "value"))
        writer.writerow((1, 1))
    with pytest.raises(ValueError, match="missing or out of order"):
        fixture.check_csv(path)


def test_csv_incomplete_frame_is_rejected(tmp_path):
    path = tmp_path / "partial.csv"
    with CsvRecorder(path) as recorder:
        recorder.record(TelemetryEvent(raw=b"", samples=(1, 2)))
    with pytest.raises(ValueError, match="incomplete frame"):
        fixture.check_csv(path)
