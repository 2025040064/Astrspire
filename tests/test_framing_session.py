from __future__ import annotations

import threading
import time

import pytest

from qx_stc_isp.framing import HARDWARE_DIALECTS, FrameError, decode_frame, encode_frame
from qx_stc_isp.hex_image import HexImage
from qx_stc_isp.session import DownloadSession, SessionState
from qx_stc_isp.simulator import SimulatedTransport


def test_candidate_frames_have_distinct_checksum_widths() -> None:
    assert encode_frame(b"\x01", direction="tx", checksum_bits=8) == bytes.fromhex(
        "46 B9 6A 00 06 01 71 16"
    )
    assert encode_frame(b"\x01", direction="tx", checksum_bits=16) == bytes.fromhex(
        "46 B9 6A 00 07 01 00 72 16"
    )
    for width in (8, 16):
        frame = encode_frame(b"\x50\x01", direction="rx", checksum_bits=width)
        assert decode_frame(frame, direction="rx", checksum_bits=width) == b"\x50\x01"
    assert not HARDWARE_DIALECTS


@pytest.mark.parametrize("change", ["header", "length", "checksum", "end", "extra"])
def test_candidate_frame_rejects_corruption(change: str) -> None:
    original = encode_frame(b"abc", direction="rx")
    changed = bytearray(original)
    if change == "header":
        changed[2] = 0x6A
    elif change == "length":
        changed[4] += 1
    elif change == "checksum":
        changed[-2] ^= 1
    elif change == "end":
        changed[-1] = 0
    else:
        changed.append(0)
    with pytest.raises(FrameError):
        decode_frame(bytes(changed), direction="rx")


def _image() -> HexImage:
    return HexImage(((0, b"\x01\x02\x03"), (0x80, b"\x55")))


def test_manual_power_then_success_emits_all_stages_and_fragments() -> None:
    events = []
    simulator = SimulatedTransport(cold_start_delay=0, fragment_size=2)
    session = DownloadSession(_image(), events.append, simulator, wait_power_seconds=1)
    runner = threading.Thread(target=session.run)
    runner.start()
    try:
        deadline = time.monotonic() + 1
        while session.state != SessionState.WAIT_POWER and time.monotonic() < deadline:
            time.sleep(0.001)
        assert session.state == SessionState.WAIT_POWER
        assert not any(event.kind == "tx" for event in events)
        session.power_on()
        runner.join(2)
        assert not runner.is_alive()
        assert session.state == SessionState.COMPLETE
        assert simulator.closed
        assert {event.state for event in events if event.kind == "state"} >= {
            SessionState.WAIT_POWER,
            SessionState.IDENTIFIED,
            SessionState.NEGOTIATING,
            SessionState.ERASING,
            SessionState.PROGRAMMING,
            SessionState.VERIFYING,
            SessionState.COMPLETE,
        }
        assert all(event.data.startswith(b"\x46\xB9") for event in events if event.kind == "tx")
        assert sum(event.kind == "rx" for event in events) > sum(event.kind == "tx" for event in events)
        assert session.progress == 100
    finally:
        session.cancel()
        runner.join(2)


@pytest.mark.parametrize("fault", ["timeout", "bad_checksum", "bad_ack", "disconnect"])
def test_simulator_faults_fail_and_close(fault: str) -> None:
    events = []
    peer = SimulatedTransport(cold_start_delay=0, response_timeout=0.002, fault=fault)
    session = DownloadSession(_image(), events.append, peer, wait_power_seconds=1)
    session.power_on()
    assert session.run() == SessionState.FAILED
    assert peer.closed
    assert any(event.kind == "error" for event in events)


def test_wait_timeout_and_cancel() -> None:
    timeout_session = DownloadSession(_image(), lambda event: None, wait_power_seconds=0.02)
    assert timeout_session.run() == SessionState.FAILED
    assert timeout_session.simulator.closed

    events = []
    session = DownloadSession(_image(), events.append, wait_power_seconds=30)
    session.cancel()
    assert session.run() == SessionState.CANCELLED
    assert session.simulator.closed


def test_large_sparse_image_is_bounded_in_simulation() -> None:
    image = HexImage(((0, b"\x01"), (0x10000, b"\x02")), program_limit=None)
    events = []
    session = DownloadSession(
        image,
        events.append,
        SimulatedTransport(cold_start_delay=0),
        wait_power_seconds=1,
    )
    session.power_on()
    assert session.run() == SessionState.FAILED
    assert any("64 KiB" in event.message for event in events if event.kind == "error")
