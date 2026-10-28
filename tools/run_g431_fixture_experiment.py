"""Explicit, identity-bound UART fixture experiment with mandatory restoration.

This standalone tool is never imported by the GUI or ordinary hardware tests.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys

from intelhex import IntelHex

from qx_stc_isp.ports import enumerate_ports
from qx_stc_isp.transport import PySerialTransport

FLASH_BYTES = 131072
FIXTURE_HASH = "da27ab2869478e717a2ba005f7b720b92d7042365cb209b30f198f86b70f232c"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_fixture(path):
    image = IntelHex(str(path))
    if image.segments() != [(0x08000000, 0x08000000 + 9408)]:
        raise ValueError("Fixture must contain only its known contiguous Flash image")
    if hashlib.sha256(image.tobinarray().tobytes()).hexdigest() != FIXTURE_HASH:
        raise ValueError("Fixture image hash mismatch")


def validate_backups(first, second):
    if first.stat().st_size != FLASH_BYTES or second.stat().st_size != FLASH_BYTES:
        raise ValueError("Two complete 128 KiB backups required")
    if digest(first) != digest(second):
        raise ValueError("Fresh backups differ; no programming allowed")
    return digest(first)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-flash", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.allow_flash:
        parser.error("Requires explicit --allow-flash")
    root = Path(__file__).resolve().parents[1]
    runtime = root / "build-preview/runtime-g431-uart-20261027"
    output = args.output.resolve()
    if root.drive.upper() != "F:" or not output.is_relative_to(runtime):
        parser.error("Keep private experiment evidence inside this F-drive task folder")
    ports = [p for p in enumerate_ports() if (p.vid, p.pid) == (0x0D28, 0x0204)]
    if len(ports) != 1 or ports[0].device != "COM7" or ports[0].serial_number != "4T_DAPLINK_":
        parser.error("Expected one freshly enumerated DAP/COM7; no programming")
    image = runtime / "firmware/g431_uart.hex"
    validate_fixture(image)
    expected_uid = struct.unpack("<3I", (runtime / "g431-uid.bin").read_bytes())
    # Only checks exclusive availability; this never transmits or resets.
    availability = PySerialTransport("COM7", dtr=False, rts=False)
    try:
        availability.open(115200)
    finally:
        availability.close()
    output.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(image, output / "fixture.hex")
    state = dict(at_utc=datetime.now(timezone.utc).isoformat(), attempted=False, restored=False,
                 flash_bytes=FLASH_BYTES, uart_ready=False, captures=[], errors=[],
                 scope="Explicit G431 UART backend experiment; not GUI acceptance")
    executable = r"D:\tools\openocd\bin\openocd.exe"
    scripts = r"D:\tools\openocd\openocd\scripts"
    preamble = ["adapter driver cmsis-dap", "cmsis-dap backend hid", "cmsis-dap vid_pid 0x0d28 0x0204",
        "transport select swd", "gdb port disabled", "telnet port disabled", "tcl port disabled",
        "source [find target/stm32g4x.cfg]", "adapter speed 1000",
        '$_TARGETNAME configure -event examine-end {}', '$_TARGETNAME configure -event reset-init {}',
        '$_TARGETNAME configure -event reset-start {}', '$_TARGETNAME configure -event trace-config {}',
        "init", 'set chip [lindex [read_memory 0xe0042000 32 1] 0]',
        'if {($chip & 0xfff) != 0x468} {error "Chip identity mismatch"}',
        'if {[lindex [read_memory 0x1fff75e0 16 1] 0] != 128} {error "Flash size mismatch"}',
        'if {([lindex [read_memory 0x40022020 32 1] 0] & 0xff) != 0xaa} {error "Read protection enabled; no unlock"}',
        'set uid [read_memory 0x1fff7590 32 3]',
        'foreach actual $uid expected {' + " ".join(hex(v) for v in expected_uid)
            + '} {if {$actual != $expected} {error "Target UID mismatch"}}']

    def persist():
        (output / "result.json").write_text(json.dumps(state, indent=2), encoding="utf-8")

    def probe(name, commands):
        config = output / (name + ".cfg")
        config.write_text("\n".join([*preamble, *commands, "shutdown"]) + "\n", encoding="utf-8")
        result = subprocess.run([executable, "-s", scripts, "-f", str(config)], cwd=output,
                                capture_output=True, timeout=45,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        (output / (name + ".log")).write_bytes(result.stdout + result.stderr)
        if result.returncode:
            raise RuntimeError(name + " failed; see private log")

    try:
        probe("backup", ["dump_image before-a.bin 0x08000000 0x20000",
                         "dump_image before-b.bin 0x08000000 0x20000"])
        state["backup_sha256"] = validate_backups(output / "before-a.bin", output / "before-b.bin")
        persist()
        print("Two fresh backups match; target identity and RDP checked", flush=True)
        state["attempted"] = True
        persist()
        probe("program", ["reset halt", "flash write_image erase fixture.hex", "verify_image fixture.hex", "reset run"])
        state["program_verified"] = True
        probe("uart-ready", [
            'set cr1 [lindex [read_memory 0x40013800 32 1] 0]',
            'set brr [lindex [read_memory 0x4001380c 32 1] 0]',
            'set moder [lindex [read_memory 0x48000000 32 1] 0]',
            'set afrh [lindex [read_memory 0x48000024 32 1] 0]',
            'echo [format "UART_CR1=0x%08x BRR=0x%08x GPIO_MODER=0x%08x GPIO_AFRH=0x%08x" $cr1 $brr $moder $afrh]',
            'if {($cr1 & 0xd) != 0xd || $brr == 0 || (($moder >> 18) & 0xf) != 0xa || (($afrh >> 4) & 0xff) != 0x77} {error "Fixture UART not running; do not send"}'])
        state["uart_ready"] = True
        persist()
        print("Fixture Flash verified and USART1 running; begin UART handshake/60s", flush=True)
        for seconds in (60, 600):
            capture = output / f"capture-{seconds}s"
            result = subprocess.run([sys.executable, "-B", str(root / "tools/g431_uart_acceptance.py"),
                "--port", "COM7", "--vid", "0D28", "--pid", "0204", "--fixture-installed",
                "--seconds", str(seconds), "--output", str(capture)], cwd=root,
                capture_output=True, timeout=seconds + 35,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            (output / f"capture-{seconds}s.log").write_bytes(result.stdout + result.stderr)
            report = json.loads((capture / "result.json").read_text(encoding="utf-8")) if (capture / "result.json").exists() else {}
            state["captures"].append(dict(seconds=seconds, passed=report.get("passed", False),
                                           frames=report.get("samples", {}).get("frames", 0)))
            persist()
            if result.returncode or not report.get("passed"):
                raise RuntimeError(f"UART {seconds}s failed; no further captures or reprogramming")
            print(f"UART {seconds}s passed; {state['captures'][-1]['frames']} frames", flush=True)
    except Exception as exc:
        state["errors"].append(str(exc))
    finally:
        if state["attempted"]:
            print("Restoring fresh full Flash backup", flush=True)
            try:
                probe("restore", ["reset halt", "flash write_image erase before-a.bin 0x08000000 bin",
                    "verify_image before-a.bin 0x08000000 bin", "reset run",
                    "dump_image restored.bin 0x08000000 0x20000"])
                state["restored"] = digest(output / "restored.bin") == state["backup_sha256"]
                if not state["restored"]:
                    raise RuntimeError("Full Flash readback mismatch after restoration")
            except Exception as exc:
                state["errors"].append("RESTORE FAILED: " + str(exc))
        state["passed"] = bool(state["restored"] and not state["errors"] and len(state["captures"]) == 2)
        persist()
    print(json.dumps(state, indent=2), flush=True)
    return 0 if state["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
