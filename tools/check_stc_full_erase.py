"""Explicit, bounded hardware experiment using the production STC session."""
import argparse
from dataclasses import asdict
from datetime import datetime
import hashlib
import json
from pathlib import Path

from qx_stc_isp.hex_image import HexImage
from qx_stc_isp.port_lease import PORT_LEASES
from qx_stc_isp.ports import enumerate_ports
from qx_stc_isp.session import SessionState
from qx_stc_isp.stc89_session import Stc89DownloadSession, Stc89EraseSession
from qx_stc_isp.transport import PySerialTransport


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True)
    parser.add_argument("--operation", choices=("erase", "download"), required=True)
    parser.add_argument("--confirm", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--wait-power", type=float, default=60)
    args = parser.parse_args()
    if args.confirm != "ERASE F002 66-43-FD 8192" or not 0 < args.wait_power <= 60:
        parser.error("Explicit target/range confirmation and a <=60 second wait are required")
    root = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    if not output.is_relative_to(root / "captures" / "private"):
        parser.error("Raw hardware evidence must stay in captures/private")
    port = next((p for p in enumerate_ports() if p.device.upper() == args.port.upper()), None)
    if port is None or (port.vid, port.pid) != (0x1A86, 0x7523):
        parser.error("Expected CH340 is not currently enumerated; no port opened")
    firmware = root / "examples" / "led.hex"
    image = None
    if args.operation == "download":
        if hashlib.sha256(firmware.read_bytes()).hexdigest().upper() != "8DF1C8ED7E875F067D0280FED35B3B072FD06201C722CEB4288F37CAE618BA3B":
            parser.error("The previously validated led.hex has changed; no port opened")
        image = HexImage.from_file(firmware)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        def save(value):
            stream.write(json.dumps(value, ensure_ascii=False) + "\n")
            stream.flush()

        def emit(event):
            save({"time": event.timestamp.isoformat(), "kind": event.kind,
                  "state": event.state.name, "message": event.message,
                  "direction": event.direction, "hex": event.data.hex()})
            if event.kind not in ("tx", "rx"):
                print(event.message, flush=True)

        save({"started": datetime.now().astimezone().isoformat(), "device": asdict(port),
              "operation": args.operation, "authorized_range": [0, 8191],
              "source_sha256": {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
                                for p in (root / "src").rglob("*.py")}})
        transport = PySerialTransport(port.device)
        options = {"allow_erase": True, "wait_power_seconds": args.wait_power}
        session = (Stc89EraseSession(transport, emit, **options) if image is None else
                   Stc89DownloadSession(image, transport, emit, erase_full_program=True, **options))
        with PORT_LEASES.acquire(port.device, owner="explicit-stc-8k-experiment"):
            state = session.run()
        result = {"state": state.name, "error": session.error,
                  "known_profile": session.known_profile,
                  "erase_started": session.erase_started,
                  "erase_request_bytes": session.erase_request_bytes,
                  "erase_ack_received": session.erase_ack_received,
                  "acknowledged_bytes": session.acknowledged_bytes,
                  "chip_id": session.status.chip_id if session.status else None,
                  "version": session.status.version_label if session.status else None,
                  "blank_readback_verified": False, "program_running_observed": False}
        save({"result": result})
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0 if state == SessionState.COMPLETE else 1


if __name__ == "__main__":
    raise SystemExit(main())
