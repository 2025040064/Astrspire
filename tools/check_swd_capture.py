"""Explicit hardware-only acceptance; never flashes, halts or resets a target."""
import argparse
import csv
from dataclasses import asdict
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import time

from qx_stc_isp.vofa.swd import SwdCaptureConfig, SwdManifest, SwdRingReader, ProbeLease
from qx_stc_isp.vofa.swd_link import OpenOcdMemory


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--probe-serial", required=True)
    parser.add_argument("--adapter", choices=("stlink", "cmsis-dap"), default="stlink")
    parser.add_argument("--seconds", type=float, default=60)
    parser.add_argument("--attach-only", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.seconds <= 600:
        parser.error("seconds must be 1–600")
    args.output.mkdir(parents=True, exist_ok=True)
    manifest = SwdManifest.load(args.manifest)
    config = SwdCaptureConfig(args.adapter, args.probe_serial, manifest.target, args.manifest)
    reader = SwdRingReader(manifest)
    memory = OpenOcdMemory(config, manifest, runtime_dir=args.output)
    result = {"date_utc": datetime.now(timezone.utc).isoformat(), "config": config.document(),
              "firmware_sha256": manifest.firmware_sha256, "requested_seconds": args.seconds,
              "transport": "SWD SRAM (not UART)", "hardware": True, "frames": 0,
              "duplicates": 0, "out_of_order": 0, "bad_values": 0, "tick_errors": 0}
    previous = None
    try:
        with ProbeLease(config.probe_serial):
            try:
                memory.open()
                result["attached_running"] = True
                if not args.attach_only:
                    reader.begin(memory)
                    with (args.output / "samples.csv").open("x", encoding="utf-8", newline="") as stream:
                        writer = csv.writer(stream)
                        writer.writerow(["received_at_utc", "device_sequence", "device_tick_ms"] + [f"CH{i + 1}" for i in range(16)])
                        start = time.monotonic()
                        while time.monotonic() - start < args.seconds:
                            memory.ensure_running()
                            for event in reader.poll(memory):
                                sequence, tick = (int(value) for value in event.samples[:2])
                                if previous is not None:
                                    result["duplicates"] += int(sequence == previous[0])
                                    result["out_of_order"] += int(sequence < previous[0])
                                    result["tick_errors"] += int(tick - previous[1] != (sequence - previous[0]) * 10)
                                for channel, value in enumerate(event.samples[2:], 2):
                                    expected = (sequence % 200 - 100) * (channel - 1) / 100
                                    result["bad_values"] += int(not math.isclose(value, expected, rel_tol=2e-6, abs_tol=2e-6))
                                writer.writerow([event.received_at.isoformat(), sequence, tick, *event.samples])
                                result["frames"] += 1
                                previous = (sequence, tick)
                            time.sleep(config.poll_ms / 1000)
                        result["elapsed_seconds"] = time.monotonic() - start
                    result["statistics"] = asdict(reader.statistics)
                    result["pass"] = result["frames"] >= args.seconds * 95 and not any(result[key] for key in
                        ("duplicates", "out_of_order", "bad_values", "tick_errors")) and reader.statistics.sequence_gaps == 0
            finally:
                memory.close()
                result["helper_closed"] = memory._process is None and memory._socket is None
    except (OSError, ValueError, RuntimeError) as exc:
        result["error"] = str(exc)
        result["pass"] = False
    (args.output / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=True, indent=2))
    if not args.attach_only and not result.get("pass") or result.get("error"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
