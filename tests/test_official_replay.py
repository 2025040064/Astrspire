from __future__ import annotations

import json
from pathlib import Path

import pytest

from qx_stc_isp.framing import FrameError, FrameStreamDecoder, decode_frame, encode_frame
from qx_stc_isp.hex_image import HexImage
from qx_stc_isp.official_replay import OfficialCapture, OfficialReplaySession, validate_transactions
from qx_stc_isp.session import SessionState
from qx_stc_isp.stc89 import CapturedBaudParameters, ProtocolError, Stc89Status, erase_payload, write_payload

ROOT = Path(__file__).resolve().parents[1]


def fixture_frames() -> list[tuple[str, bytes]]:
    document = json.loads((ROOT / "tests/fixtures/stc89_f002_66c_redacted.json").read_text())
    assert document["redacted"] is True
    return [(item["direction"], bytes.fromhex(item["hex"])) for item in document["frames"]]


def fixture_capture() -> OfficialCapture:
    image = HexImage.from_file(ROOT / "examples/led.hex")
    status, steps = validate_transactions(fixture_frames(), image)
    return OfficialCapture(status, steps, 71, 1, "derived-redacted-test-fixture")


@pytest.mark.parametrize("fragment_size", [1, 2, 7, 55, 4096])
def test_rx_reassembly_preserves_all_recorded_frames(fragment_size) -> None:
    frames = [frame for direction, frame in fixture_frames() if direction == "RX"]
    stream = FrameStreamDecoder()
    raw = b"\x00" + b"".join(frames)
    result = []
    for offset in range(0, len(raw), fragment_size):
        result.extend(stream.feed(raw[offset:offset + fragment_size]))
    stream.finish()
    assert result == frames
    assert len(result) == 13
    assert stream.discarded_noise == 1


@pytest.mark.parametrize("corruption", ["length", "checksum", "terminator"])
def test_recognized_invalid_frame_is_not_silently_resynchronized(corruption) -> None:
    raw = bytearray(encode_frame(b"\x80", direction="rx"))
    if corruption == "length":
        raw[3:5] = b"\xff\xff"
    elif corruption == "checksum":
        raw[-2] ^= 1
    else:
        raw[-1] = 0
    with pytest.raises(FrameError):
        FrameStreamDecoder().feed(raw + encode_frame(b"\x80", direction="rx"))


def test_noise_limit_and_incomplete_tail_fail() -> None:
    with pytest.raises(FrameError, match="noise"):
        FrameStreamDecoder(max_noise_bytes=3).feed(b"\x00" * 4)
    stream = FrameStreamDecoder()
    stream.feed(b"\x46\xb9\x68\x00")
    with pytest.raises(FrameError, match="incomplete"):
        stream.finish()


def test_status_and_captured_baud_fields_are_decoded() -> None:
    frames = fixture_frames()
    status = Stc89Status.parse(decode_frame(frames[0][1], direction="rx"))
    assert status.chip_id == 0xF002
    assert status.version_label == "6.6C"
    assert status.timing_samples == (0x0A79,) * 8
    parameters = CapturedBaudParameters.parse_probe(decode_frame(frames[1][1], direction="tx"))
    assert parameters.probe().hex() == "8ffffd00062882"
    assert parameters.activate().hex() == "8efffd000628"


@pytest.mark.parametrize("offset,value", [(20, 0xF1), (17, 0x67), (19, 0xFE)])
def test_unknown_chip_or_profile_refuses_before_replay(offset, value) -> None:
    frames = fixture_frames()
    status = bytearray(decode_frame(frames[0][1], direction="rx"))
    status[offset] = value
    frames[0] = ("RX", encode_frame(status, direction="rx"))
    with pytest.raises(ProtocolError):
        validate_transactions(frames, HexImage.from_file(ROOT / "examples/led.hex"))


def test_write_ack_wrong_even_with_valid_frame_checksum_fails() -> None:
    frames = fixture_frames()
    for index, (direction, frame) in enumerate(frames):
        payload = decode_frame(frame, direction=direction.lower())
        if direction == "RX" and len(payload) == 2:
            frames[index] = ("RX", encode_frame(b"\x80\x00", direction="rx"))
            break
    with pytest.raises(ProtocolError, match="应答或数据和"):
        validate_transactions(frames, HexImage.from_file(ROOT / "examples/led.hex"))


def test_wrong_firmware_missing_end_and_extra_command_fail() -> None:
    frames = fixture_frames()
    with pytest.raises(ProtocolError, match="HEX 数据不匹配"):
        validate_transactions(frames, HexImage.from_file(ROOT / "examples/flashled.hex"))
    image = HexImage.from_file(ROOT / "examples/led.hex")
    with pytest.raises(ProtocolError):
        validate_transactions(frames[:-1], image)
    with pytest.raises(ProtocolError):
        validate_transactions(frames + [("TX", encode_frame(b"\x8d"))], image)


@pytest.mark.parametrize("address,size", [(-128, 128), (1, 128), (8192, 128), (0, 127)])
def test_block_bounds(address, size) -> None:
    with pytest.raises(ProtocolError):
        write_payload(address, bytes(size))


def test_erase_sector_parameter_has_static_and_capture_evidence() -> None:
    assert erase_payload(21) == bytes.fromhex("84 01 33 33 33 33 33 33")
    assert erase_payload(513)[1] == 2
    with pytest.raises(ProtocolError):
        erase_payload(8193)


def test_complete_replay_and_cancel_do_not_open_a_device(monkeypatch) -> None:
    def forbidden(*_args, **_kwargs):
        raise AssertionError("offline replay must not open a COM port")

    monkeypatch.setattr("serial.Serial", forbidden)
    events = []
    session = OfficialReplaySession(fixture_capture(), events.append, step_delay=0)
    assert session.run() == SessionState.COMPLETE
    assert sum(event.kind == "tx" for event in events) == 14
    assert sum(event.kind == "rx" for event in events) == 13
    assert "离线回放" in events[-1].message
    assert "未实际烧录" in events[-1].message
    with pytest.raises(RuntimeError):
        session.run()
    cancelled = OfficialReplaySession(fixture_capture(), events.append, step_delay=0)
    cancelled.cancel()
    assert cancelled.run() == SessionState.CANCELLED


def test_original_private_capture_when_present(monkeypatch) -> None:
    trace = ROOT / "captures/private/aicube-com8-20261021-1646.jsonl"
    if not trace.is_file():
        pytest.skip("private official evidence is intentionally not distributed")
    monkeypatch.setattr("serial.Serial", lambda *_a, **_k: pytest.fail("hardware accessed"))
    capture = OfficialCapture.from_file(trace, HexImage.from_file(ROOT / "examples/led.hex"))
    assert len(capture.steps) == 27
    assert capture.sync_count == 71
    assert capture.status.chip_id == 0xF002


def test_modified_capture_identity_is_refused(tmp_path) -> None:
    path = tmp_path / "unknown.jsonl"
    path.write_text('{}\n', encoding="utf-8")
    with pytest.raises(ProtocolError, match="已核对"):
        OfficialCapture.from_file(path, HexImage.from_file(ROOT / "examples/led.hex"))
