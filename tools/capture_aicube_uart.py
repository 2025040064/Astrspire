"""Attach a passive Frida UART trace to an already-open AiCube process.

This helper never starts a download, opens a COM port, or changes API results.
It requires the optional ``frida`` Python package and an explicit target PID.
"""

from __future__ import annotations

import argparse
import json
import threading
from datetime import datetime, timezone
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="只读记录官方 AiCube 的 Win32 串口 API")
    parser.add_argument("--pid", type=int, required=True, help="用户已打开的 AiCube 进程 PID")
    parser.add_argument("--port", required=True, help="已确认的 CH340 COM 口，例如 COM8")
    parser.add_argument("--output", type=Path, required=True, help="新建的私有 JSONL 抓包文件")
    arguments = parser.parse_args()
    port = arguments.port.upper()
    if not port.startswith("COM") or not port[3:].isdigit() or arguments.pid <= 0:
        parser.error("必须提供明确的 COM 数字端口和正数 PID")

    try:
        import frida
    except ImportError as exc:
        parser.error(f"缺少可选 Frida 依赖：{exc}")

    script_path = Path(__file__).with_name("aicube-uart-readonly.js")
    source = script_path.read_text(encoding="utf-8")
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    stopped = threading.Event()
    session = None
    script = None
    event_count = 0
    with arguments.output.open("x", encoding="utf-8") as trace:
        trace.write(json.dumps({
            "kind": "CAPTURE_HEADER", "at": datetime.now(timezone.utc).isoformat(),
            "pid": arguments.pid, "port": port, "script": str(script_path),
            "status": "API observation only; physical UART completeness unverified",
        }, ensure_ascii=False) + "\n")
        trace.flush()

        def on_message(message: dict, _data: bytes | None) -> None:
            nonlocal event_count
            if message.get("type") == "send":
                payload = message.get("payload")
                if not isinstance(payload, dict) or payload.get("tag") != "aicube-uart":
                    return
                event_port = str(payload.get("port", "")).upper()
                if event_port and not event_port.endswith(port):
                    return
                record = payload
            else:
                record = {"kind": "FRIDA_SCRIPT_ERROR", "message": message}
            trace.write(json.dumps(record, ensure_ascii=False) + "\n")
            trace.flush()
            event_count += 1
            if record.get("kind") not in {"TX_COMPLETE", "RX_COMPLETE"} or event_count % 1000 == 0:
                print(f"事件 {event_count}: {record.get('kind', 'EVENT')} {record.get('port', '')}", flush=True)

        try:
            session = frida.attach(arguments.pid)
            session.on("detached", lambda *_args: stopped.set())
            script = session.create_script(source)
            script.on("message", on_message)
            script.load()
            print(f"开始只读抓包：PID {arguments.pid} / {port}。按 Ctrl+C 停止；未触发下载。", flush=True)
            while not stopped.wait(0.2):
                pass
        except KeyboardInterrupt:
            print("正在停止抓包并卸载钩子。", flush=True)
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
