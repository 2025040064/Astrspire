"""Explicit, bounded application-engine RX check. Never enqueues or writes TX."""

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import threading
import time

from PySide6.QtCore import Qt

from qx_stc_isp.port_lease import PORT_LEASES
from qx_stc_isp.ports import enumerate_ports
from qx_stc_isp.transport import PySerialTransport
from qx_stc_isp.vofa.serial_link import SerialLinkWorker


class ReceiveOnlyTransport(PySerialTransport):
    def __init__(self, device):
        super().__init__(device, dtr=False, rts=False)

    def write(self, *_args, **_kwargs):
        raise RuntimeError("TX is forbidden in this explicit RX-only check")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True)
    parser.add_argument("--baudrate", type=int, required=True)
    parser.add_argument("--vid", type=lambda value: int(value, 16), required=True)
    parser.add_argument("--pid", type=lambda value: int(value, 16), required=True)
    parser.add_argument("--seconds", type=float, default=10)
    parser.add_argument("--decoder", choices=("RawData", "FireWater", "JustFloat"), default="RawData")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 0 < args.seconds <= 60:
        parser.error("seconds must be >0 and <=60")
    root = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    if not output.is_relative_to(root / "build-preview") or root.drive.upper() != "F:":
        parser.error("output must stay in the F-drive project's build-preview directory")
    port = next((item for item in enumerate_ports() if item.device.casefold() == args.port.casefold()), None)
    if port is None or (port.vid, port.pid) != (args.vid, args.pid):
        parser.error("Port metadata does not match; no port opened")
    if output.exists():
        parser.error("Use a new output filename; existing evidence is never overwritten")

    result = {"started_utc": datetime.now(timezone.utc).isoformat(), "device": asdict(port),
        "baudrate": args.baudrate, "format": "8N1", "seconds_requested": args.seconds,
        "decoder": args.decoder, "dtr": False, "rts": False, "rx_bytes": 0, "numeric_frames": 0,
        "preview_hex": "", "status": [], "errors": [], "purpose": "RX only; no flash/reset/TX"}
    preview = bytearray()
    worker = SerialLinkWorker(port.device, args.baudrate, args.decoder,
                              transport_factory=ReceiveOnlyTransport)

    def raw(payload):
        result["rx_bytes"] += len(payload)
        preview.extend(payload[:max(0, 512 - len(preview))])

    def events(batch):
        result["numeric_frames"] += sum(item.samples is not None for item in batch)

    worker.raw.connect(raw, Qt.DirectConnection)
    worker.events.connect(events, Qt.DirectConnection)
    worker.status.connect(lambda kind, text: result["status"].append([kind, text]), Qt.DirectConnection)
    worker.error.connect(result["errors"].append, Qt.DirectConnection)
    timer = threading.Timer(args.seconds, worker.stop)
    started = time.monotonic()
    timer.start()
    try:
        worker.run()
    finally:
        timer.cancel()
        timer.join()
    result.update(elapsed=time.monotonic() - started, tx=asdict(worker.statistics),
                  receive=asdict(worker.receive_statistics),
                  preview_hex=bytes(preview).hex(" "), port_lease_released=PORT_LEASES.owner_for(port.device) is None)
    result["opened"] = any(kind == "connected" for kind, _text in result["status"])
    result["data_observed"] = result["rx_bytes"] > 0
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["opened"] and not result["errors"] and result["port_lease_released"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
