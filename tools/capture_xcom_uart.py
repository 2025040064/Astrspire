"""Bounded read-only serial API observation of a user-visible XCOM process.

Never opens a port, sends commands, changes API results, or terminates XCOM.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import threading
from datetime import datetime, timezone
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", required=True, type=int)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--seconds", type=int, default=120)
    args = parser.parse_args()
    if args.pid <= 0 or not 1 <= args.seconds <= 180:
        parser.error("Positive PID and duration 1..180 seconds required")

    import frida

    script_path = Path(__file__).with_name("xcom_serial_readonly.js")
    source = script_path.read_text(encoding="utf-8")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    stopped = threading.Event()
    session = None
    script = None
    event_count = 0
    errors = []
    with args.output.open("x", encoding="utf-8") as trace:
        def record(value: dict) -> None:
            trace.write(json.dumps(value, ensure_ascii=False) + "\n")
            trace.flush()

        def on_message(message: dict, _data: bytes | None) -> None:
            nonlocal event_count
            if message.get("type") == "send":
                payload = message.get("payload")
                if not isinstance(payload, dict):
                    return
                record(payload)
                event_count += 1
                print(f"EVENT {event_count}: {payload.get('kind', 'unknown')}", flush=True)
            else:
                errors.append(message)
                record({"kind": "script_error", "message": message})
                stopped.set()

        record({"kind": "capture_header", "pid": args.pid, "seconds": args.seconds,
                "at": datetime.now(timezone.utc).isoformat(), "frida": frida.__version__,
                "script_sha256": hashlib.sha256(source.encode()).hexdigest(),
                "limitation": "API observation, not physical UART capture; RX may be incomplete"})
        try:
            session = frida.attach(args.pid)
            session.on("detached", lambda *_args: stopped.set())
            script = session.create_script(source)
            script.on("message", on_message)
            script.load()
            print(f"CAPTURE_READY pid={args.pid} seconds={args.seconds}", flush=True)
            stopped.wait(args.seconds)
        except KeyboardInterrupt:
            print("CAPTURE_STOP_REQUESTED", flush=True)
        except Exception as exc:
            errors.append({"type": type(exc).__name__, "message": str(exc)})
            record({"kind": "capture_error", "error": errors[-1]})
            print(f"CAPTURE_FAILED {type(exc).__name__}: {exc}", flush=True)
        finally:
            if script is not None:
                try:
                    script.unload()
                except frida.InvalidOperationError:
                    pass
            if session is not None:
                try:
                    session.detach()
                except frida.InvalidOperationError:
                    pass
            record({"kind": "capture_end", "events": event_count, "errors": len(errors),
                    "xcom_terminated_by_helper": False})
    print(f"CAPTURE_CLOSED events={event_count} errors={len(errors)}", flush=True)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
