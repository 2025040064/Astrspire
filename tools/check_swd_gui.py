"""Explicit live SWD GUI/CSV acceptance; the caller owns backup/restoration."""
import argparse
import csv
import ctypes
from ctypes import wintypes
from dataclasses import asdict
import json
import math
from pathlib import Path
import time

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QFileDialog

from qx_stc_isp.gui import MainWindow
from qx_stc_isp.vofa.swd import SwdCaptureConfig, SwdManifest


def resident_bytes():
    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
            (name, ctypes.c_size_t) for name in ("PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
            "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage", "PrivateUsage")]
    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    query = ctypes.WinDLL("psapi").GetProcessMemoryInfo
    query.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
    query.restype = wintypes.BOOL
    if not query(wintypes.HANDLE(-1), ctypes.byref(counters), counters.cb):
        raise OSError("GetProcessMemoryInfo failed")
    return counters.WorkingSetSize


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--probe-serial", required=True)
    parser.add_argument("--seconds", type=float, default=60)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.seconds <= 600:
        parser.error("seconds must be 1–600")
    args.output.mkdir(parents=True, exist_ok=True)
    manifest = SwdManifest.load(args.manifest)
    app = QApplication([])
    window = MainWindow(log_dir=args.output / "logs")
    window.resize(1600, 950)
    window.tabs.setCurrentIndex(1)
    window.source_combo.setCurrentIndex(window.source_combo.findData("swd"))
    window.swd_panel.restore(SwdCaptureConfig("stlink", args.probe_serial, manifest.target, args.manifest).document())
    window.wave_buffer_spin.setValue(100000)
    window.monitor_values_tabs.setCurrentIndex(1)
    window.show()
    state = {"hardware": True, "scope": "Native GUI, SWD SRAM, AsyncRecorder CSV; not UART", "requested_seconds": args.seconds,
             "memory_samples": [], "pass": False}
    started = stopped = recorder = None
    last_memory = 0
    launch = time.monotonic()
    timer = QTimer()

    def tick():
        nonlocal started, stopped, recorder, last_memory
        now = time.monotonic()
        try:
            if stopped is not None:
                if not window._monitor_active:
                    state["stop_seconds"] = now - stopped
                    state["threads_closed"] = True
                    timer.stop()
                    window._workspace_dirty = False
                    window.close()
                    app.quit()
                elif now - stopped > 5:
                    raise RuntimeError("GUI worker did not stop")
                return
            if window._monitor_error:
                raise RuntimeError(window._monitor_error)
            if started is None:
                if window._monitor_connected:
                    original = QFileDialog.getSaveFileName
                    try:
                        QFileDialog.getSaveFileName = lambda *_a, **_k: (str(args.output / "samples.csv"), "CSV (*.csv)")
                        window.toggle_sample_recording()
                    finally:
                        QFileDialog.getSaveFileName = original
                    recorder = window._sample_recorder
                    if recorder is None:
                        raise RuntimeError("GUI recording did not start")
                    started = now
                elif now - launch > 12:
                    raise RuntimeError("GUI connection timeout")
                return
            if now - last_memory >= 1:
                state["memory_samples"].append({"seconds": round(now - started, 3), "rss_bytes": resident_bytes(),
                    "retained": window.sample_store.statistics.retained})
                last_memory = now
            if now - started >= args.seconds:
                state["statistics"] = asdict(window._monitor_worker.statistics)
                window.toggle_sample_recording()
                state["recording"] = asdict(recorder.statistics)
                state["elapsed_seconds"] = now - started
                state["send_disabled"] = not window.send_button.isEnabled() and not window.periodic_send_check.isEnabled()
                state["screenshot"] = window.grab().save(str(args.output / "swd-live.png"))
                window.stop_monitor()
                stopped = now
        except Exception as exc:
            state["error"] = str(exc)
            timer.stop()
            window.stop_monitor()
            QTimer.singleShot(1000, app.quit)

    timer.timeout.connect(tick)
    timer.start(100)
    QTimer.singleShot(0, window.start_monitor)
    try:
        app.exec()
    finally:
        window._workspace_dirty = False
        window.close()
        app.processEvents()
    if (args.output / "samples.csv").exists():
        previous = None
        rows = frames = gaps = duplicates = order = bad_values = tick_errors = 0
        current = []
        with (args.output / "samples.csv").open(encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                rows += 1
                if int(row["channel_index"]) != len(current):
                    raise ValueError("CSV channel order mismatch")
                current.append(float(row["value"]))
                if len(current) != 16:
                    continue
                frames += 1
                seq, tick_ms = map(int, current[:2])
                if previous:
                    gaps += max(0, seq - previous[0] - 1)
                    duplicates += int(seq == previous[0])
                    order += int(seq < previous[0])
                    tick_errors += int(tick_ms - previous[1] != (seq - previous[0]) * 10)
                for channel, value in enumerate(current[2:], 2):
                    bad_values += int(not math.isclose(value, (seq % 200 - 100) * (channel - 1) / 100, rel_tol=2e-6, abs_tol=2e-6))
                previous = seq, tick_ms
                current = []
        state.update(frames=frames, csv_rows=rows, sequence_gaps=gaps, duplicates=duplicates,
                     out_of_order=order, bad_values=bad_values, tick_errors=tick_errors, partial_frame=bool(current))
        stats = state.get("recording", {})
        state["pass"] = (not state.get("error") and state.get("threads_closed") and state.get("send_disabled")
            and frames >= args.seconds * 95 and not any((gaps, duplicates, order, bad_values, tick_errors, len(current)))
            and frames == stats.get("accepted") == stats.get("saved") and stats.get("unsaved") == 0
            and bool(state["memory_samples"]))
    (args.output / "result.json").write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in state.items() if key != "memory_samples"}, ensure_ascii=True, indent=2))
    if not state["pass"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
