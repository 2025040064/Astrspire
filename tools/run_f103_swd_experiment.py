"""Explicit F103-only experiment, with verified backup and finally restoration.

Not imported by the application or tests. Hardware writes require --allow-flash.
No protection/option-byte/EEPROM operations are performed.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from datetime import datetime, timezone


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def flash_arguments(probe_serial, image):
    return ["-c", "port=SWD", "sn=" + probe_serial, "mode=NORMAL", "freq=1000",
            "-w", str(image), "0x08000000", "-v", "-rst"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe-serial", required=True)
    parser.add_argument("--allow-flash", action="store_true")
    parser.add_argument("--gui", action="store_true", help="Use native GUI and its async recorder for acceptance")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.allow_flash:
        parser.error("This hardware experiment requires --allow-flash")
    root = Path(__file__).resolve().parents[1]
    runtime = root / "build-preview/runtime-v060-dev3"
    backup, confirm = runtime / "f103-before.bin", runtime / "f103-confirm.bin"
    if backup.stat().st_size != 131072 or confirm.stat().st_size != 131072 or digest(backup) != digest(confirm):
        raise ValueError("Two matching full Flash backups are required")
    args.output.mkdir(parents=True, exist_ok=False)
    cli = r"D:\tools\STM32CubeCLT_1.18.0\STM32CubeProgrammer\bin\STM32_Programmer_CLI.exe"
    connection = ["-c", "port=SWD", "sn=" + args.probe_serial, "mode=HOTPLUG"]
    state = {"date_utc": datetime.now(timezone.utc).isoformat(), "probe_serial": args.probe_serial,
             "backup_sha256": digest(backup), "target": "stm32f1x", "stages": [], "restored": False}

    def persist():
        (args.output / "result.json").write_text(json.dumps(state, indent=2), encoding="utf-8")

    def run_cli(name, options):
        result = subprocess.run([cli, *options], capture_output=True, timeout=90)
        (args.output / (name + ".log")).write_bytes(result.stdout + result.stderr)
        if result.returncode:
            raise RuntimeError(name + " failed; see saved log")
        return result.stdout.decode("utf-8", errors="replace")

    attempted = False
    try:
        identity = run_cli("identity-protection", [*connection, "-ob", "displ"])
        if not re.search(r"Device ID\s*:\s*0x410\b", identity) or not re.search(r"Flash size\s*:\s*128 KBytes", identity):
            raise ValueError("Fresh target identity/capacity differs from the F103 backup profile")
        if "Flash memory is not read-protected" not in identity:
            raise ValueError("Read protection state is not confirmed disabled")
        run_cli("uid-before", [*connection, "-u", "0x1FFFF7E8", "12", str(args.output / "uid-before.bin")])
        run_cli("backup-rebind", [*connection, "-u", "0x08000000", "131072", str(args.output / "before-rebind.bin")])
        if digest(args.output / "before-rebind.bin") != digest(backup):
            raise ValueError("Current target Flash does not match the two backups; no write attempted")
        state["backup_verified"] = True
        persist()
        firmware = runtime / "firmware/stm32f1x/capture.bin"
        manifest = runtime / "firmware/stm32f1x/capture.json"
        if json.loads(manifest.read_text())["firmware_sha256"] != digest(firmware):
            raise ValueError("Test firmware differs from the manifest")
        attempted = True
        run_cli("program-verify", flash_arguments(args.probe_serial, firmware))
        state["program_verified"] = True
        persist()
        for seconds in (60, 600):
            stage = args.output / f"capture-{seconds}s"
            checker = "check_swd_gui.py" if args.gui else "check_swd_capture.py"
            result = subprocess.run([sys.executable, str(root / "tools" / checker),
                "--manifest", str(manifest), "--probe-serial", args.probe_serial,
                "--seconds", str(seconds), "--output", str(stage)], capture_output=True, timeout=seconds + 30)
            (args.output / f"capture-{seconds}s.log").write_bytes(result.stdout + result.stderr)
            report = json.loads((stage / "result.json").read_text(encoding="utf-8")) if (stage / "result.json").exists() else {"pass": False}
            state["stages"].append({"seconds": seconds, "report": report})
            persist()
            print(json.dumps({"stage": seconds, "pass": report.get("pass"), "frames": report.get("frames")}), flush=True)
            if result.returncode:
                raise RuntimeError(f"SWD {seconds}s capture failed")
    except Exception as exc:
        state["error"] = str(exc)
    finally:
        if attempted:
            try:
                run_cli("restore-program-verify", flash_arguments(args.probe_serial, backup))
                run_cli("uid-after", [*connection, "-u", "0x1FFFF7E8", "12", str(args.output / "uid-after.bin")])
                run_cli("restore-full-read", [*connection, "-u", "0x08000000", "131072", str(args.output / "restored.bin")])
                state["restore_sha256"] = digest(args.output / "restored.bin")
                state["restored"] = state["restore_sha256"] == digest(backup) and (args.output / "uid-before.bin").read_bytes() == (args.output / "uid-after.bin").read_bytes()
                if not state["restored"]:
                    raise ValueError("Restoration identity or full Flash checksum mismatch")
            except Exception as exc:
                state["restore_error"] = str(exc)
        persist()
    console = {key: value for key, value in state.items() if key != "stages"}
    console["stages"] = [{"seconds": item["seconds"], "pass": item["report"].get("pass"),
                          "frames": item["report"].get("frames")} for item in state["stages"]]
    print(json.dumps(console, indent=2), flush=True)
    if state.get("error") or not state["restored"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
