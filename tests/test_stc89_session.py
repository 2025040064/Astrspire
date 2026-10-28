from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path

import pytest

from qx_stc_isp.framing import HARDWARE_DIALECTS, decode_frame, encode_frame
from qx_stc_isp.hex_image import HexImage
from qx_stc_isp.official_replay import OfficialCapture
from qx_stc_isp.session import SessionState
from qx_stc_isp.stc89 import (
    CAPTURED_VERSION, KNOWN_CHIP_ID, ProtocolError, Stc89BaudPlan, Stc89Status,
    resolve_accepted_profile,
)
from qx_stc_isp.stc89_session import Stc89DownloadSession, Stc89ReadOnlySession
from qx_stc_isp.transport import (
    PortBusyError, SerialTransport, TransportCancelled, TransportDisconnected,
    TransportError,
)

ROOT = Path(__file__).resolve().parents[1]


def captured_frames() -> list[tuple[str, bytes]]:
    doc = json.loads((ROOT / "tests/fixtures/stc89_f002_66c_redacted.json").read_text())
    assert doc["redacted"] is True
    return [(item["direction"], bytes.fromhex(item["hex"])) for item in doc["frames"]]


def captured_status() -> Stc89Status:
    return Stc89Status.parse(decode_frame(captured_frames()[0][1], direction="rx"))


class ScriptedTransport(SerialTransport):
    """Byte-level fake, responses taken from the official-derived fixture.

    Response delivery starts on read, allowing tests to preserve the recorded
    baud-switch/purge-before-ACK order without inventing device timing facts.
    """

    def __init__(self, frames=None, *, fragment=7, with_noise=True):
        source = captured_frames() if frames is None else frames
        self.status_frame = (b"\x00" if with_noise else b"") + source[0][1]
        self.transactions = []
        for direction, frame in source[1:]:
            if direction == "TX":
                self.transactions.append([frame, None])
            else:
                self.transactions[-1][1] = frame
        self.fragment = fragment
        self.writes = []
        self.rates = []
        self.settings = []
        self.cursor = 0
        self.pending = b""
        self.buffer = bytearray()
        self.opened = False
        self.closed = 0
        self.cancelled = False
        self.purges = 0
        self.sync_seen = False
        self.open_error = None
        self.close_error = None
        self.disconnect_at = None
        self.never_respond = False
        self.on_write = None

    @property
    def is_open(self):
        return self.opened

    def open(self, baudrate, **settings):
        if self.open_error:
            raise self.open_error
        self.opened = True
        self.rates.append(baudrate)
        self.settings.append(settings)

    def reconfigure(self, baudrate, **settings):
        self.rates.append(baudrate)
        self.settings.append(settings)

    def write(self, data, timeout=5):
        if self.cancelled:
            raise TransportCancelled("fake cancelled")
        raw = bytes(data)
        self.writes.append(raw)
        if self.on_write is not None:
            self.on_write(raw)
        if raw == b"\x7f":
            if not self.sync_seen:
                self.sync_seen = True
                self.pending = self.status_frame
            return
        if self.disconnect_at is not None and self.cursor == self.disconnect_at:
            raise TransportDisconnected("fake unplugged")
        if self.pending or self.buffer:
            raise AssertionError("new request sent before previous response was consumed")
        expected, response = self.transactions[self.cursor]
        assert raw == expected
        self.cursor += 1
        self.pending = response or b""

    def read_some(self, max_bytes, timeout):
        if self.cancelled:
            raise TransportCancelled("fake cancelled")
        if self.never_respond:
            time.sleep(min(timeout, 0.001))
            return b""
        if self.pending:
            self.buffer.extend(self.pending)
            self.pending = b""
        size = min(self.fragment, max_bytes, len(self.buffer))
        raw = bytes(self.buffer[:size])
        del self.buffer[:size]
        return raw

    def read_exact(self, size, timeout):
        raise AssertionError("engine must use bounded cancellable reads")

    def flush(self):
        raise AssertionError("flush is not a purge operation")

    def purge_buffers(self):
        self.purges += 1
        self.buffer.clear()

    def cancel(self):
        self.cancelled = True

    def close(self):
        self.opened = False
        self.closed += 1
        if self.close_error:
            raise self.close_error


@pytest.fixture(autouse=True)
def no_physical_serial(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("offline STC89 tests must not open physical devices")
    monkeypatch.setattr("serial.Serial", forbidden)


@pytest.fixture
def fast_download(monkeypatch):
    # Production delay behavior remains unchanged. Unit tests eliminate only
    # deliberate protocol settling waits, never transport timeout handling.
    pauses = []
    def pause(session, seconds):
        pauses.append(seconds)
        session._check_cancelled()
    monkeypatch.setattr(Stc89DownloadSession, "_pause", pause)
    return pauses


def test_clock_calculation_reconstructs_exact_official_probe():
    plan = Stc89BaudPlan.derive(captured_status())
    assert plan.clock_hz == 11030400
    assert plan.baudrate == 115200
    assert plan.timer_divisor == 3
    assert plan.parameters.probe() == decode_frame(captured_frames()[1][1], direction="tx")
    assert plan.parameters.activate() == decode_frame(captured_frames()[3][1], direction="tx")


def test_erase_only_requests_full_program_without_hex_or_writes(fast_download, monkeypatch):
    from qx_stc_isp.stc89_session import Stc89EraseSession
    # Static-derived 16-sector request, NOT an official full-erase capture.
    frames = []
    for direction, frame in captured_frames():
        payload = decode_frame(frame, direction=direction.lower())
        if direction == "TX" and payload[:1] == b"\x84":
            frame = encode_frame(bytes.fromhex("84 10 33 33 33 33 33 33"))
        if direction == "TX" and (payload[:1] == b"\x00" or payload in (b"\xff", b"\x82")):
            break
        frames.append((direction, frame))
    transport = ScriptedTransport(frames, fragment=1)
    events = []
    monkeypatch.setattr(Stc89EraseSession, "_pause", lambda self, seconds: self._check_cancelled())
    session = Stc89EraseSession(transport, events.append, allow_erase=True)
    assert session.run() == SessionState.COMPLETE
    assert transport.cursor == 8 and transport.closed == 1
    assert decode_frame(transport.writes[-1], direction="tx") == bytes.fromhex("84 10 33 33 33 33 33 33")
    assert session.erase_ack_received and session.erase_request_bytes == 8192
    assert SessionState.PROGRAMMING not in {event.state for event in events}
    assert "未全区回读" in events[-1].message


def test_full_preerase_changes_only_erase_request_not_write_length(fast_download):
    frames = [(direction, encode_frame(bytes.fromhex("84 10 33 33 33 33 33 33"))
               if direction == "TX" and decode_frame(frame, direction="tx")[:1] == b"\x84" else frame)
              for direction, frame in captured_frames()]
    transport = ScriptedTransport(frames)
    session = Stc89DownloadSession(HexImage.from_file(ROOT / "examples/led.hex"), transport,
                                   allow_erase=True, erase_full_program=True)
    assert session.run() == SessionState.COMPLETE
    assert session.erase_request_bytes == 8192 and session.acknowledged_bytes == 512
    assert transport.cursor == 14


@pytest.mark.parametrize("fault", ["locked", "unknown", "ack", "cancel", "close"])
def test_erase_only_rejects_invalid_conditions_without_writes_or_finish(fault, monkeypatch):
    from qx_stc_isp.stc89_session import Stc89EraseSession
    frames = captured_frames()
    if fault == "unknown":
        status = bytearray(decode_frame(frames[0][1], direction="rx"))
        status[17] ^= 1
        frames[0] = ("RX", encode_frame(status, direction="rx"))
    transformed = []
    for direction, frame in frames:
        payload = decode_frame(frame, direction=direction.lower())
        if direction == "TX" and payload[:1] == b"\x00":
            break
        if direction == "TX" and payload[:1] == b"\x84":
            frame = encode_frame(bytes.fromhex("84 10 33 33 33 33 33 33"))
        if fault == "ack" and direction == "RX" and len(payload) == 8:
            frame = encode_frame(payload[:1] + b"\xf1" + payload[2:], direction="rx")
        transformed.append((direction, frame))
    transport = ScriptedTransport(transformed)
    monkeypatch.setattr(Stc89EraseSession, "_pause", lambda self, seconds: self._check_cancelled())
    session = Stc89EraseSession(transport, allow_erase=fault != "locked")
    if fault == "cancel":
        transport.on_write = lambda raw: session.cancel() if raw != b"\x7f" and decode_frame(raw, direction="tx")[:1] == b"\x84" else None
    if fault == "close":
        transport.close_error = OSError("offline close failure")
    assert session.run() in (SessionState.FAILED, SessionState.CANCELLED)
    payloads = [decode_frame(raw, direction="tx") for raw in transport.writes if raw != b"\x7f"]
    assert not any(raw[:1] == b"\x00" or raw in (b"\xff", b"\x82") for raw in payloads)
    if fault in ("locked", "unknown"):
        assert not payloads and not session.erase_started
    assert transport.closed == 1


def test_clock_uses_second_sample_not_average_or_first():
    status = captured_status()
    altered = Stc89Status(status.chip_id, status.version_raw,
                         (1, status.timing_samples[1], 2, 3, 4, 5, 6, 7), 53)
    assert Stc89BaudPlan.derive(altered).parameters == Stc89BaudPlan.derive(status).parameters


@pytest.mark.parametrize("requested", [921600, 460800, 230400, 115200])
def test_baud_selection_caps_to_observed_valid_timer_rate(requested):
    assert Stc89BaudPlan.derive(captured_status(), requested_baudrate=requested).baudrate == 115200


@pytest.mark.parametrize("kwargs", [{"initial_baudrate": 4800}, {"requested_baudrate": 12345}])
def test_uncovered_baud_configuration_rejected(kwargs):
    with pytest.raises(ProtocolError):
        Stc89BaudPlan.derive(captured_status(), **kwargs)


@pytest.mark.parametrize("fragment", [1, 2, 7, 128])
def test_readonly_identify_only_sync_never_baud_erase_write_or_reset(fragment):
    transport = ScriptedTransport(fragment=fragment)
    events = []
    session = Stc89ReadOnlySession(transport, events.append)
    assert not transport.is_open and not transport.writes
    assert session.run() == SessionState.COMPLETE
    assert session.known_profile
    assert session.status == captured_status()
    assert transport.rates == [2400]
    assert transport.settings == [{"bytesize": 8, "parity": "N", "stopbits": 1}]
    assert transport.writes and set(transport.writes) == {b"\x7f"}
    assert transport.closed == 1 and not transport.is_open
    assert transport.purges == 0
    assert "未擦除" in events[-1].message and "未发送结束复位" in events[-1].message
    assert {event.state for event in events} == {
        SessionState.WAIT_POWER, SessionState.IDENTIFIED, SessionState.COMPLETE}
    assert any(event.direction == "RX" for event in events)
    with pytest.raises(RuntimeError):
        session.run()


@pytest.mark.parametrize("offset,value", [(20, 0xF1), (17, 0x67), (19, 0xFE)])
def test_readonly_can_show_unknown_profile_without_enabling_writes(offset, value):
    frames = captured_frames()
    status = bytearray(decode_frame(frames[0][1], direction="rx"))
    status[offset] = value  # Deliberately corrupted test identity, not protocol evidence.
    frames[0] = ("RX", encode_frame(status, direction="rx"))
    transport = ScriptedTransport(frames)
    events = []
    session = Stc89ReadOnlySession(transport, events.append)
    assert session.run() == SessionState.COMPLETE
    assert session.status is not None and not session.known_profile
    assert any("已识别但不支持烧录" in event.message for event in events)
    assert set(transport.writes) == {b"\x7f"}


@pytest.mark.parametrize("fault", ["checksum", "length", "tail", "zero_timing", "noise"])
def test_readonly_rejects_bad_status_and_closes(fault):
    transport = ScriptedTransport(with_noise=False)
    frame = bytearray(transport.status_frame)
    if fault == "checksum":
        frame[-2] ^= 1
    elif fault == "length":
        frame[3:5] = b"\xff\xff"
    elif fault == "tail":
        frame[-1] = 0
    elif fault == "zero_timing":
        payload = bytearray(decode_frame(frame, direction="rx"))
        payload[3:5] = b"\x00\x00"
        frame = bytearray(encode_frame(payload, direction="rx"))
    else:
        frame = bytearray(b"\x00" * 257 + frame)
    transport.status_frame = bytes(frame)
    session = Stc89ReadOnlySession(transport)
    assert session.run() == SessionState.FAILED
    assert session.error
    assert transport.closed == 1 and set(transport.writes) == {b"\x7f"}


def test_readonly_timeout_is_bounded_and_cancel_does_not_wait_for_30_seconds():
    transport = ScriptedTransport()
    transport.never_respond = True
    session = Stc89ReadOnlySession(transport, wait_power_seconds=0.025, sync_interval=0.005)
    start = time.monotonic()
    assert session.run() == SessionState.FAILED
    assert time.monotonic() - start < 1
    assert 1 <= len(transport.writes) <= 8 and transport.closed == 1
    transport = ScriptedTransport()
    transport.never_respond = True
    session = Stc89ReadOnlySession(transport)
    worker = threading.Thread(target=session.run)
    worker.start()
    deadline = time.monotonic() + 1
    while not transport.writes and worker.is_alive() and time.monotonic() < deadline:
        time.sleep(0.001)
    session.cancel()
    worker.join(timeout=1)
    assert not worker.is_alive()
    assert session.state == SessionState.CANCELLED and transport.closed == 1


def test_cancel_before_run_and_port_busy_never_send():
    transport = ScriptedTransport()
    session = Stc89ReadOnlySession(transport)
    session.cancel()
    assert session.run() == SessionState.CANCELLED
    assert not transport.writes and not transport.rates and transport.closed == 1
    transport = ScriptedTransport()
    transport.open_error = PortBusyError("busy")
    session = Stc89ReadOnlySession(transport)
    assert session.run() == SessionState.FAILED
    assert session.error == "busy" and not transport.writes and transport.closed == 1


def test_close_failure_never_reports_success():
    transport = ScriptedTransport()
    transport.close_error = OSError("close failed")
    events = []
    session = Stc89ReadOnlySession(transport, events.append)
    assert session.run() == SessionState.FAILED
    assert "关闭失败" in events[-1].message
    assert not any(event.state == SessionState.COMPLETE for event in events)


@pytest.mark.parametrize("seconds", [0, -1, float("nan"), float("inf"), True])
def test_invalid_timeout_configuration_rejected(seconds):
    with pytest.raises(ValueError):
        Stc89ReadOnlySession(ScriptedTransport(), wait_power_seconds=seconds)


def test_accepted_profile_is_exact_fingerprint_only():
    profile = resolve_accepted_profile(KNOWN_CHIP_ID, CAPTURED_VERSION)
    assert profile is not None and profile.key == "stc89-f002-66c"
    assert resolve_accepted_profile(KNOWN_CHIP_ID, b"\x66\x43\xFE") is None
    assert resolve_accepted_profile(0xF001, CAPTURED_VERSION) is None
    assert resolve_accepted_profile(None, CAPTURED_VERSION) is None


def test_default_download_gate_stops_before_negotiation_and_erase(fast_download):
    transport = ScriptedTransport()
    image = HexImage.from_file(ROOT / "examples/led.hex")
    session = Stc89DownloadSession(image, transport)
    assert session.run() == SessionState.FAILED
    assert "锁定" in session.error
    assert session.baud_plan is not None and not session.erase_started
    assert set(transport.writes) == {b"\x7f"}
    assert transport.closed == 1 and not HARDWARE_DIALECTS


def test_opt_in_engine_exactly_matches_all_derived_capture_frames(fast_download):
    transport = ScriptedTransport(fragment=1)
    events = []
    session = Stc89DownloadSession(HexImage.from_file(ROOT / "examples/led.hex"),
                                   transport, events.append, allow_erase=True)
    assert session.run() == SessionState.COMPLETE
    assert transport.writes[1:] == [frame for direction, frame in captured_frames() if direction == "TX"]
    assert transport.rates == [2400, 115200, 2400, 115200]
    assert transport.purges == 4 and transport.cursor == 14 and transport.closed == 1
    assert fast_download == [0.2, 0.2, 0.01] + [0.005] * 4 + [0.05]
    assert session.acknowledged_bytes == 512 and session.erase_started
    assert "未做全片回读" in events[-1].message and "尚未" not in events[-1].message
    assert "已注册指纹" in events[-1].message


@pytest.mark.parametrize("offset,value", [(20, 0xF1), (17, 0x67), (19, 0xFE)])
def test_unknown_download_identity_never_negotiates_or_erases(offset, value, fast_download):
    frames = captured_frames()
    status = bytearray(decode_frame(frames[0][1], direction="rx"))
    status[offset] = value
    frames[0] = ("RX", encode_frame(status, direction="rx"))
    transport = ScriptedTransport(frames)
    session = Stc89DownloadSession(HexImage.from_file(ROOT / "examples/led.hex"),
                                   transport, allow_erase=True)
    assert session.run() == SessionState.FAILED
    assert set(transport.writes) == {b"\x7f"} and not session.erase_started


@pytest.mark.parametrize("fault", ["write_ack", "erase_id", "probe_ack", "disconnect"])
def test_download_failure_never_retries_or_sends_finish(fault, fast_download):
    frames = captured_frames()
    target = None
    for index, (direction, frame) in enumerate(frames):
        payload = decode_frame(frame, direction=direction.lower())
        if direction != "RX":
            continue
        if fault == "write_ack" and len(payload) == 2:
            target = index
            payload = bytes((payload[0], payload[1] ^ 1))
        elif fault == "erase_id" and len(payload) == 8:
            target = index
            payload = payload[:1] + bytes((payload[1] ^ 1,)) + payload[2:]
        elif fault == "probe_ack" and payload[:1] == b"\x8f":
            target = index
            payload = payload[:-1] + bytes((payload[-1] ^ 1,))
        else:
            continue
        frames[index] = ("RX", encode_frame(payload, direction="rx"))
        break
    transport = ScriptedTransport(frames)
    if fault == "disconnect":
        transport.disconnect_at = 8  # First programming request after erase.
    else:
        assert target is not None
    events = []
    session = Stc89DownloadSession(HexImage.from_file(ROOT / "examples/led.hex"),
                                   transport, events.append, allow_erase=True)
    assert session.run() == SessionState.FAILED
    payloads = [decode_frame(frame, direction="tx") for frame in transport.writes if frame != b"\x7f"]
    assert b"\xff" not in payloads and b"\x82" not in payloads
    write_blocks = [payload for payload in payloads if len(payload) == 135]
    assert len(write_blocks) <= 1
    assert transport.closed == 1
    assert session.error == events[-1].message
    if fault == "probe_ack":
        assert not session.erase_started
        assert "目标程序可能不完整" not in events[-1].message
    else:
        assert session.erase_started
        assert "目标程序可能不完整" in events[-1].message
        assert "不自动重试" in events[-1].message
        assert "重新上电后重新开始" in events[-1].message


def test_missing_purge_or_oversize_image_never_erases(fast_download):
    image = HexImage.from_file(ROOT / "examples/led.hex")
    for scenario in ("purge", "bounds"):
        transport = ScriptedTransport()
        if scenario == "purge":
            transport.purge_buffers = None
            current_image = image
        else:
            current_image = HexImage(((8192, b"\x00"),), program_limit=None)
        session = Stc89DownloadSession(current_image, transport, allow_erase=True)
        assert session.run() == SessionState.FAILED
        # 无 purge 能力时在识别前停止（没有 7F）；越界固件在识别后、擦除前停止。
        assert set(transport.writes) == (set() if scenario == "purge" else {b"\x7f"})
        assert not session.erase_started and transport.closed == 1


def test_cancel_after_erase_stops_without_any_retry_or_finish(fast_download):
    transport = ScriptedTransport()
    events = []
    session = Stc89DownloadSession(HexImage.from_file(ROOT / "examples/led.hex"),
                                   transport, events.append, allow_erase=True)
    def on_write(raw):
        if raw != b"\x7f" and len(decode_frame(raw, direction="tx")) == 135:
            session.cancel()
    transport.on_write = on_write
    assert session.run() == SessionState.CANCELLED
    assert session.erase_started and session.acknowledged_bytes == 0
    assert transport.closed == 1
    assert len(transport.writes) == 10  # Sync, 2 baud, 5 access, erase, first write.
    assert "目标程序可能不完整" in events[-1].message
    assert "重新上电后重新开始" in events[-1].message
    assert not any(event.state == SessionState.COMPLETE for event in events)


@pytest.mark.parametrize("allow_erase", [False, True])
def test_download_close_failure_is_failure_and_preserves_erase_warning(allow_erase, fast_download):
    transport = ScriptedTransport()
    transport.close_error = OSError("fake close failed")
    events = []
    session = Stc89DownloadSession(HexImage.from_file(ROOT / "examples/led.hex"),
                                   transport, events.append, allow_erase=allow_erase)
    assert session.run() == SessionState.FAILED
    assert transport.closed == 1
    assert "关闭失败" in events[-1].message
    assert session.error == events[-1].message
    assert not any(event.state == SessionState.COMPLETE for event in events)
    assert ("目标程序可能不完整" in events[-1].message) == allow_erase
    assert ("重新上电后重新开始" in events[-1].message) == allow_erase


def test_original_private_capture_also_matches_computed_engine_when_present(fast_download):
    path = ROOT / "captures/private/aicube-com8-20261021-1646.jsonl"
    if not path.is_file():
        pytest.skip("private original official evidence is not distributed")
    image = HexImage.from_file(ROOT / "examples/led.hex")
    capture = OfficialCapture.from_file(path, image)
    frames = [(step.direction, step.frame) for step in capture.steps]
    transport = ScriptedTransport(frames)
    session = Stc89DownloadSession(image, transport, allow_erase=True)
    assert session.run() == SessionState.COMPLETE
    assert transport.writes[1:] == [frame for direction, frame in frames if direction == "TX"]
    assert capture.digest == hashlib.sha256(path.read_bytes()).hexdigest()

