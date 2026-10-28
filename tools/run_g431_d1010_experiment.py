"""Explicit backed-up G431/DAP experiment; always attempts Flash restoration.

This tool is never imported by the application. No option/protection writes.
The uniquely attached 0D28:0204 probe is explicitly selected by VID/PID because
this device's USB serial differs from its DAP_Info serial.
"""
import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import queue
import struct
import subprocess
import threading
import time

from PySide6.QtCore import Qt
from qx_stc_isp.port_lease import PORT_LEASES
from qx_stc_isp.ports import enumerate_ports
from qx_stc_isp.vofa.serial_link import SerialLinkWorker


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-flash", action="store_true")
    parser.add_argument("--seconds", type=int, default=60, choices=(60,))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.allow_flash:
        parser.error("Explicit --allow-flash is required")
    root = Path(__file__).resolve().parents[1]
    runtime = root / "build-preview/runtime-g431-uart-20261027"
    output = args.output.resolve()
    if not output.is_relative_to(runtime):
        parser.error("Evidence must stay inside this task's runtime folder")
    backup, confirmation = runtime / "g431-before-a.bin", runtime / "g431-before-b.bin"
    expected_hash = "9ffa6e14cc91f271dfd16428620621648a18699669e69a77c18121a43416d18e"
    if (backup.stat().st_size != 131072 or confirmation.stat().st_size != 131072
            or digest(backup) != expected_hash or digest(confirmation) != expected_hash):
        raise ValueError("Two matching full target backups required")
    firmware = root / "dist/D10.10_serial-test-20261027.hex"
    if digest(firmware) != "13e758d65cf384af203e5b436d94ffe39b6744d07b99c0cdb0ecf86024594e3a":
        raise ValueError("Unexpected test firmware")
    uid = struct.unpack("<3I", (runtime / "g431-uid.bin").read_bytes())
    candidates = [port for port in enumerate_ports() if (port.vid, port.pid) == (0x0D28, 0x0204)]
    if len(candidates) != 1 or candidates[0].device != "COM7" or candidates[0].serial_number != "4T_DAPLINK_":
        raise ValueError("Expected exactly one freshly bound DAP/COM7")
    output.mkdir(parents=True, exist_ok=False)
    (output / "test-image.hex").write_bytes(firmware.read_bytes())
    (output / "original-flash.bin").write_bytes(backup.read_bytes())
    if digest(output / "original-flash.bin") != expected_hash or digest(output / "test-image.hex") != digest(firmware):
        raise ValueError("Staged experiment files differ")
    executable = r"D:\tools\openocd\bin\openocd.exe"
    scripts = r"D:\tools\openocd\openocd\scripts"
    state = dict(at_utc=datetime.now(timezone.utc).isoformat(), chip="STM32G431", port=asdict(candidates[0]),
                 backup_sha256=expected_hash, attempted=False, restored=False, pass_uart=False,
                 transactions=[], errors=[])

    def persist():
        (output / "result.json").write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")

    preamble = ["adapter driver cmsis-dap", "cmsis-dap backend hid", "cmsis-dap vid_pid 0x0d28 0x0204",
        "transport select swd", "gdb port disabled", "telnet port disabled", "tcl port disabled",
        "source [find target/stm32g4x.cfg]", "adapter speed 1000",
        '$_TARGETNAME configure -event examine-end {}', '$_TARGETNAME configure -event reset-init {}',
        '$_TARGETNAME configure -event reset-start {}', '$_TARGETNAME configure -event trace-config {}',
        "init", 'set chip [lindex [read_memory 0xe0042000 32 1] 0]',
        'if {($chip & 0xfff) != 0x468} {error "Chip identity mismatch"}',
        'if {[lindex [read_memory 0x1fff75e0 16 1] 0] != 128} {error "Flash capacity mismatch"}',
        'if {([lindex [read_memory 0x40022020 32 1] 0] & 0xff) != 0xaa} {error "Read protection enabled; no unlock"}',
        'set uid [read_memory 0x1fff7590 32 3]',
        'foreach actual $uid expected {' + " ".join(hex(value) for value in uid) + '} {if {$actual != $expected} {error "Target UID mismatch"}}']

    def run_probe(name, commands):
        config = output / (name + ".cfg")
        config.write_text("\n".join([*preamble, *commands, "shutdown"]) + "\n", encoding="utf-8")
        result = subprocess.run([executable, "-s", scripts, "-f", str(config)], cwd=output,
                                capture_output=True, timeout=90,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        (output / (name + ".log")).write_bytes(result.stdout + result.stderr)
        if result.returncode:
            raise RuntimeError(name + " failed; see saved log")

    try:
        run_probe("rebind", ["dump_image before-rebind.bin 0x08000000 0x20000"])
        if digest(output / "before-rebind.bin") != expected_hash:
            raise ValueError("Current Flash no longer matches backup; no write")
        state["attempted"] = True
        persist()
        image_name = "test-image.hex"
        run_probe("program", ["reset halt", f'flash write_image erase "{image_name}"',
                              f'verify_image "{image_name}"', "reset run"])
        state["program_verified"] = True
        persist()
        print("Program verified; testing actual application UART worker", flush=True)
        worker = SerialLinkWorker("COM7", 115200, "RawData")
        connected, stopped = threading.Event(), threading.Event()
        replies = queue.Queue()
        partial = bytearray()
        state["status"] = []

        def status(kind, text):
            state["status"].append([kind, text])
            if kind == "connected":
                connected.set()

        def receive(raw):
            partial.extend(raw)
            if len(partial) > 4096:
                state["errors"].append("Oversized UART reply")
                worker.stop()
                return
            while b"\n" in partial:
                line, _, remainder = partial.partition(b"\n")
                partial[:] = remainder
                if line:
                    replies.put(bytes(line).rstrip(b"\r"))

        commands = [(b"blink 0\r\n", b"Order error;"), (b"flow 0\r\n", b"Order error;"),
                    (b"blink 500\r\n", b"Order ok;"), (b"flow 200\r\n", b"Order ok;")]

        def control():
            try:
                if not connected.wait(3):
                    raise ValueError("COM7 connection timeout")
                started = time.monotonic()
                index = 0
                while time.monotonic() - started < args.seconds:
                    payload, expected = commands[index % len(commands)]
                    sent_at = time.monotonic()
                    worker.enqueue_send(payload)
                    reply = replies.get(timeout=2)
                    transaction = dict(request=payload.decode().rstrip(), reply=reply.decode("ascii", errors="replace"),
                                       match=reply == expected, seconds=time.monotonic() - sent_at)
                    state["transactions"].append(transaction)
                    if reply != expected:
                        raise ValueError("Unexpected command reply")
                    index += 1
                    if index % 20 == 0:
                        print(json.dumps(dict(transactions=index, elapsed=round(time.monotonic() - started, 2))), flush=True)
                    stopped.wait(0.5)
                state["elapsed_seconds"] = time.monotonic() - started
                state["pass_uart"] = len(state["transactions"]) >= 100 and replies.empty()
            except Exception as exc:
                state["errors"].append("UART request/response failed: " + (str(exc) or type(exc).__name__))
            finally:
                worker.stop()
                stopped.set()

        worker.status.connect(status, Qt.DirectConnection)
        worker.raw.connect(receive, Qt.DirectConnection)
        worker.error.connect(state["errors"].append, Qt.DirectConnection)
        controller = threading.Thread(target=control, name="G431 acceptance controller", daemon=True)
        controller.start()
        try:
            worker.run()
        finally:
            connected.set()
            stopped.set()
            worker.stop()
            controller.join(timeout=4)
        state.update(rx=asdict(worker.receive_statistics), tx=asdict(worker.statistics),
                     port_released=PORT_LEASES.owner_for("COM7") is None, controller_closed=not controller.is_alive())
        if state["errors"] or not state["pass_uart"] or not state["port_released"] or not state["controller_closed"]:
            raise ValueError("UART acceptance did not pass")
    except Exception as exc:
        state["errors"].append(str(exc))
    finally:
        if state["attempted"]:
            try:
                backup_name = "original-flash.bin"
                run_probe("restore", ["reset halt", f'flash write_image erase "{backup_name}" 0x08000000 bin',
                    f'verify_image "{backup_name}" 0x08000000 bin', "reset run",
                    "dump_image restored.bin 0x08000000 0x20000"])
                state["restore_sha256"] = digest(output / "restored.bin")
                state["restored"] = state["restore_sha256"] == expected_hash
                if not state["restored"]:
                    raise ValueError("Full Flash restoration mismatch")
            except Exception as exc:
                state["restore_error"] = str(exc)
        persist()
    print(json.dumps({key: value for key, value in state.items() if key not in ("transactions", "status")}, indent=2), flush=True)
    return 0 if state["pass_uart"] and state["restored"] and not state["errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
