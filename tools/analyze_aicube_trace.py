"""Offline validation of a captured STC89 sum8 session; never opens a device.

Generated golden data contains device-specific bytes and must stay in the
ignored captures/private directory. Command roles are observations of this
session, not a complete definition for other bootloader versions.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from qx_stc_isp.framing import decode_frame, encode_frame
from qx_stc_isp.hex_image import HexImage


def split_rx(raw: bytes) -> tuple[list[bytes], list[str]]:
    frames: list[bytes] = []
    noise: list[str] = []
    offset = 0
    while offset < len(raw):
        start = raw.find(b"\x46\xb9\x68", offset)
        if start < 0:
            raise ValueError("Unframed RX bytes remain at the end of capture")
        if start != offset:
            noise.append(raw[offset:start].hex())
        if start + 5 > len(raw):
            raise ValueError("Truncated RX header")
        size = int.from_bytes(raw[start + 3:start + 5], "big") + 2
        frame = raw[start:start + size]
        decode_frame(frame, direction="rx", checksum_bits=8)
        frames.append(frame)
        offset = start + size
    return frames, noise


def analyze(trace: Path, firmware: Path) -> tuple[dict, dict]:
    raw_trace = trace.read_bytes()
    events = [json.loads(line) for line in raw_trace.decode("utf-8").splitlines()]
    invalid = {"FRIDA_SCRIPT_ERROR", "HOOK_UNAVAILABLE", "TX_FAILED"}
    if any(event["kind"] in invalid for event in events):
        raise ValueError("Capture contains an error or unavailable hook")
    if not any(event["kind"] == "READY" for event in events):
        raise ValueError("No READY event")
    if not events or events[-1]["kind"] != "CLOSE":
        raise ValueError("Capture has not recorded closing the COM handle")

    tx_frames: list[bytes] = []
    rx = bytearray()
    sync_count = 0
    completed: list[dict] = []
    for event in events:
        kind = event["kind"]
        if "PENDING" in kind:
            raise ValueError("This offline analyzer requires synchronous completed I/O")
        if kind not in {"TX_COMPLETE", "RX_COMPLETE"}:
            continue
        if event.get("truncated") or event.get("read_error") or not event.get("hex"):
            raise ValueError("An I/O buffer was not captured completely")
        data = bytes.fromhex(event["hex"])
        if len(data) != event.get("captured"):
            raise ValueError("Captured buffer length mismatch")
        completed.append({"at": event["at"], "direction": kind[:2], "hex": data.hex()})
        if kind == "RX_COMPLETE":
            rx.extend(data)
            continue
        if event.get("count") != len(data):
            raise ValueError("WriteFile completed fewer bytes than submitted")
        if data == b"\x7f":
            sync_count += 1
        else:
            decode_frame(data, direction="tx", checksum_bits=8)
            tx_frames.append(data)

    rx_frames, noise = split_rx(bytes(rx))
    tx_payloads = [decode_frame(frame, direction="tx") for frame in tx_frames]
    rx_payloads = [decode_frame(frame, direction="rx") for frame in rx_frames]
    image = HexImage.from_file(firmware)
    source = {start + i: value for start, data in image.segments for i, value in enumerate(data)}
    blocks: list[dict] = []
    for payload in tx_payloads:
        if len(payload) < 7 or payload[:3] != b"\x00\x00\x00":
            continue
        address = int.from_bytes(payload[3:5], "big")
        declared = int.from_bytes(payload[5:7], "big")
        data = payload[7:]
        if declared != len(data):
            raise ValueError("Observed write-block length does not match its field")
        expected = bytes(source.get(address + i, 0xff) for i in range(declared))
        if data != expected:
            raise ValueError("Observed block differs from HEX and FF padding")
        blocks.append({"address": address, "size": declared, "sum8": sum(data) & 255})
    acks = [payload[1] for payload in rx_payloads if len(payload) == 2 and payload[0] == 0x80]
    if not blocks or acks != [block["sum8"] for block in blocks]:
        raise ValueError("Observed write ACK bytes do not match every data-block sum")
    covered = {block["address"] + i for block in blocks for i in range(block["size"])}
    if not set(source).issubset(covered):
        raise ValueError("Some firmware bytes were not present in observed write blocks")
    status = rx_payloads[0]
    if len(status) < 22 or status[0] != 0:
        raise ValueError("First RX payload does not match this session's status shape")

    digest = hashlib.sha256(raw_trace).hexdigest()
    summary = {
        "trace_sha256": digest,
        "trace_events": len(events),
        "source_hex_sha256": hashlib.sha256(firmware.read_bytes()).hexdigest(),
        "source_data_bytes": image.data_size,
        "tx_sync_bytes": sync_count,
        "tx_frames_sum8_valid": len(tx_frames),
        "rx_frames_sum8_valid": len(rx_frames),
        "rx_bytes": len(rx),
        "rx_unframed_prefix_hex": noise,
        "status_chip_id_candidate": status[20:22].hex(),
        "status_version_raw": status[17:20].hex(),
        "dcb_requests": [
            {key: event[key] for key in ("baudrate", "byte_size", "parity", "stop_bits")}
            for event in events if event["kind"] == "DCB_REQUEST"
        ],
        "tx_command_sequence": [payload[0] for payload in tx_payloads],
        "write_blocks": blocks,
        "write_ack_sum8_matches": True,
        "readback_verified": False,
        "hardware_dialect_enabled": False,
    }
    golden = {
        "source_trace_sha256": digest,
        "source": "AiCube-ISP-v6.97A official successful session, 2026-10-21",
        "notice": "Private device-specific evidence; API completion is not physical UART proof.",
        "completed_io": completed,
    }
    return summary, golden


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trace", type=Path)
    parser.add_argument("firmware", type=Path)
    parser.add_argument("--golden", type=Path, help="New private golden-trace output path")
    parser.add_argument("--redacted-fixture", type=Path, help="New test fixture with device-specific fields zeroed")
    args = parser.parse_args()
    summary, golden = analyze(args.trace, args.firmware)
    if args.golden:
        with args.golden.open("x", encoding="utf-8") as output:
            json.dump(golden, output, indent=2)
    if args.redacted_fixture:
        from qx_stc_isp.official_replay import OfficialCapture

        capture = OfficialCapture.from_file(args.trace, HexImage.from_file(args.firmware))
        records = []
        for step in capture.steps:
            payload = bytearray(decode_frame(step.frame, direction=step.direction.lower()))
            if step.direction == "RX" and len(payload) == 53:
                payload[22:] = bytes(len(payload) - 22)
            elif step.direction == "RX" and len(payload) == 8 and payload[:3] == b"\x80\xf0\x02":
                payload[3:] = bytes(5)
            frame = encode_frame(payload, direction=step.direction.lower())
            records.append({"direction": step.direction, "hex": frame.hex()})
        with args.redacted_fixture.open("x", encoding="utf-8") as output:
            json.dump({
                "source_trace_sha256": capture.digest,
                "redacted": True,
                "notice": "Derived offline test data: status bytes 22 onward and erase ACK UID zeroed; checksums recomputed. Not an exact official golden trace.",
                "frames": records,
            }, output, indent=2)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
