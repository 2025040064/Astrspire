"""Explicit local-only MainWindow throughput acceptance; never flashes hardware.

Run from the project with its existing Python environment. The producer binds
127.0.0.1 only. Output is restricted to the project's F-drive runtime directory.
This is an application/loopback result, not physical-board verification.
"""

from __future__ import annotations

import argparse
import csv
import ctypes
import hashlib
import json
import math
import os
from pathlib import Path
import socket
import statistics
import struct
import threading
import time
from datetime import datetime, timezone


def resident_bytes() -> int | None:
    if os.name != "nt":
        return None

    class ProcessMemoryCounters(ctypes.Structure):
        _fields_ = [
            ("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong),
            ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    try:
        counters = ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        get_process = ctypes.windll.kernel32.GetCurrentProcess
        get_process.restype = ctypes.c_void_p
        result = ctypes.windll.psapi.GetProcessMemoryInfo(
            ctypes.c_void_p(get_process()), ctypes.byref(counters), counters.cb)
        return counters.WorkingSetSize if result else None
    except (AttributeError, OSError):
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=int, default=60)
    parser.add_argument("--fps", type=int, default=2500)
    parser.add_argument("--name", default="loopback-60s")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--record-csv", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.seconds <= 600 or not 100 <= args.fps <= 10000:
        parser.error("seconds must be 1–600; fps must be 100–10000")
    if not args.name or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for char in args.name):
        parser.error("name must contain only ASCII letters, numbers, hyphens or underscores")
    root = Path(__file__).resolve().parents[1]
    output = (args.output or root / "build-preview" / "runtime-v050" / args.name).resolve()
    if root.drive.upper() != "F:":
        raise RuntimeError("Acceptance output must stay on the F-drive project")
    if not output.is_relative_to(root / "build-preview"):
        raise RuntimeError("Acceptance output must stay in this project's build-preview directory")
    output.mkdir(parents=True, exist_ok=True)
    if (output / "result.json").exists() or (output / "samples.csv").exists():
        raise RuntimeError("Use a new output directory; existing evidence will not be overwritten")
    source_hashes = {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (
            Path(__file__).resolve(), root / "src/qx_stc_isp/gui.py",
            root / "src/qx_stc_isp/vofa/sample_store.py",
            root / "src/qx_stc_isp/vofa/network_link.py",
            root / "src/qx_stc_isp/vofa/waveform.py",
            root / "src/qx_stc_isp/vofa/projection.py",
            root / "src/qx_stc_isp/vofa/measurement.py",
            root / "src/qx_stc_isp/vofa/measurement_panel.py",
            root / "src/qx_stc_isp/vofa/dashboard.py",
            root / "src/qx_stc_isp/vofa/recorder.py",
            root / "pyproject.toml", root / "uv.lock",
        )
    }
    os.environ["TEMP"] = str(output)
    os.environ["TMP"] = str(output)
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

    import serial
    from PySide6.QtCore import QObject, QTimer, Qt, Slot
    from PySide6.QtWidgets import QApplication
    import qx_stc_isp.gui as gui
    from qx_stc_isp.vofa.channel_settings import ChannelDisplay

    def no_hardware(*_args, **_kwargs):
        raise RuntimeError("Loopback acceptance must never open a physical serial port")

    serial.Serial = no_hardware
    gui.enumerate_ports = lambda: []
    target_frames = args.seconds * args.fps
    producer_stop = threading.Event()
    producer_continue = threading.Event()
    producer_done = threading.Event()
    producer_state = {"sent": 0, "bytes": 0, "error": None, "elapsed": 0.0}
    producer_lock = threading.Lock()
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(0.1)
    port = listener.getsockname()[1]

    def produce() -> None:
        peer = None
        try:
            while not producer_stop.is_set():
                try:
                    peer, _address = listener.accept()
                    break
                except socket.timeout:
                    continue
            if peer is None:
                return
            peer.settimeout(2)
            start = time.monotonic()
            pause_adjustment = 0.0
            sent = 0
            while sent < target_frames and not producer_stop.is_set():
                count = min(100, target_frames - sent)
                if sent < 6000 < sent + count:
                    count = 6000 - sent
                payload = b"".join(
                    struct.pack("<fff", float(index), math.sin(index / 100), float(index % 100))
                    + b"\x00\x00\x80\x7f"
                    for index in range(sent + 1, sent + count + 1)
                )
                peer.sendall(payload)
                sent += count
                with producer_lock:
                    producer_state["sent"] = sent
                    producer_state["bytes"] += len(payload)
                if sent == 6000 and target_frames > 6000:
                    pause_start = time.monotonic()
                    while not producer_stop.is_set() and not producer_continue.wait(0.05):
                        pass
                    pause_adjustment += time.monotonic() - pause_start
                deadline = start + sent / args.fps + pause_adjustment
                producer_stop.wait(max(0, deadline - time.monotonic()))
            with producer_lock:
                producer_state["elapsed"] = time.monotonic() - start
            producer_done.set()
            # Keep the connection alive until the GUI consumed all sent data.
            producer_stop.wait(args.seconds + 60)
        except Exception as exc:
            with producer_lock:
                producer_state["error"] = f"{type(exc).__name__}: {exc}"
            producer_done.set()
        finally:
            if peer is not None:
                peer.close()
            listener.close()

    producer_thread = threading.Thread(target=produce, name="local acceptance producer", daemon=True)
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    gui._load_bundled_fonts()
    window = gui.MainWindow(log_dir=output / "logs")
    window.resize(1440, 900)
    window.show()
    window.tabs.setCurrentIndex(1)
    window.source_combo.setCurrentIndex(window.source_combo.findData("tcp_client"))
    window.network_host_edit.setText("127.0.0.1")
    window.network_port_edit.setText(str(port))
    decoder_index = window.decoder_combo.findData("JustFloat")
    if decoder_index < 0:
        decoder_index = window.decoder_combo.findText("JustFloat")
    window.decoder_combo.setCurrentIndex(decoder_index)
    window.wave_buffer_spin.setValue(5000)
    window.monitor_values_tabs.setCurrentIndex(1)
    producer_thread.start()

    class Acceptance(QObject):
        def __init__(self):
            super().__init__()
            self.started = time.monotonic()
            self.observed = 0
            self.expected_sequence = 1
            self.gaps = self.duplicates = self.out_of_order = 0
            self.non_numeric = 0
            self.transmitted = 0
            self.max_batch = 0
            self.checkpoints = {}
            self.samples = []
            self.pause_started = None
            self.pause_before = None
            self.pause_finished = False
            self.capacity_changed = False
            self.rescaled = False
            self.finishing = False
            self.final_exit = 1
            self.next_sample_at = 0.0
            self.finish_deadline = None
            self.errors = []
            self.tick_intervals = []
            self.last_tick = self.started
            self.timer = QTimer(self)
            self.timer.setInterval(50)
            self.timer.timeout.connect(self.tick)

        @Slot(object)
        def observe(self, events):
            self.max_batch = max(self.max_batch, len(events))
            for event in events:
                if event.samples is None:
                    if event.error:
                        self.non_numeric += 1
                    continue
                value = event.samples[0]
                sequence = int(value)
                if sequence != value:
                    self.non_numeric += 1
                if sequence > self.expected_sequence:
                    self.gaps += sequence - self.expected_sequence
                elif sequence < self.expected_sequence:
                    if sequence == self.expected_sequence - 1:
                        self.duplicates += 1
                    else:
                        self.out_of_order += 1
                self.expected_sequence = sequence + 1
                self.observed += 1

        @Slot(bytes)
        def sent(self, _payload):
            self.transmitted += 1

        def screenshot(self, name):
            image_path = output / f"{name}.png"
            if not window.grab().save(str(image_path)):
                self.errors.append(f"could not save {image_path.name}")
            return str(image_path)

        def checkpoint(self, name):
            stats = window.sample_store.statistics
            return {
                "elapsed": round(time.monotonic() - self.started, 3),
                "parsed": stats.parsed, "retained": stats.retained,
                "evicted": stats.evicted, "invalid": stats.invalid,
                "received_bytes": stats.received_bytes,
                "wave_displayed": window.waveform.displayed_sample_count,
                # displayed_sample_count is the cumulative X-axis index; the
                # public series snapshot reports actually retained history.
                "wave_retained": max(map(len, window.waveform.displayed_series), default=0),
                "rss_bytes": resident_bytes(),
                "screenshot": self.screenshot(name),
            }

        def finish(self, reason=None):
            if self.finishing:
                return
            self.finishing = True
            if reason:
                self.errors.append(reason)
            self.checkpoints["final"] = self.checkpoint("final")
            frames = window.sample_store.snapshot()
            retained_sequences = [int(frame.values[0]) for frame in frames]
            with producer_lock:
                producer = dict(producer_state)
            stats = window.sample_store.statistics
            tail_expected = list(range(max(1, producer["sent"] - len(frames) + 1), producer["sent"] + 1))
            checks = {
                "exact_total_parsed": stats.parsed == target_frames == producer["sent"] == self.observed,
                "all_frames_ordered_without_gaps": self.gaps == self.duplicates == self.out_of_order == 0,
                "retained_tail_order_and_values": retained_sequences == tail_expected,
                "bounded_100000_history": stats.retained == min(100000, target_frames),
                "evictions_accounted": stats.evicted == max(0, target_frames - stats.retained),
                "exact_received_bytes": stats.received_bytes == producer["bytes"] == target_frames * 16,
                "no_invalid_samples": stats.invalid == self.non_numeric == 0,
                "no_application_tx": self.transmitted == 0,
                "all_batches_bounded": self.max_batch <= 256,
                "5000_capacity_checked": self.capacity_changed,
                "pause_kept_acquiring": self.checkpoints.get("pause_end", {}).get("passed", False),
                "rescale_kept_100000": self.checkpoints.get("rescale", {}).get("passed", False),
            }
            if producer["error"]:
                self.errors.append(producer["error"])
            self.report = {
                "scope": "local TCP loopback through full MainWindow; not a physical-board result",
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                "source_sha256": source_hashes,
                "parameters": {"seconds": args.seconds, "fps": args.fps, "frames": target_frames,
                               "protocol": "JustFloat", "bytes_per_frame": 16, "host": "127.0.0.1"},
                "producer": producer, "observed_frames": self.observed,
                "gaps": self.gaps, "duplicates": self.duplicates,
                "out_of_order": self.out_of_order, "max_batch_events": self.max_batch,
                "checks": checks, "checkpoints": self.checkpoints,
                "memory_samples": self.samples, "errors": self.errors,
                "gui_tick_ms": {"p50": statistics.median(self.tick_intervals),
                                "p95": sorted(self.tick_intervals)[int(.95 * (len(self.tick_intervals) - 1))],
                                "max": max(self.tick_intervals)},
                "passed": all(checks.values()) and not self.errors,
            }
            window.stop_monitor()
            producer_stop.set()
            producer_continue.set()
            self.finish_deadline = time.monotonic() + 8

        @Slot()
        def tick(self):
            now = time.monotonic()
            if self.finishing:
                if window._monitor_active and now < self.finish_deadline:
                    return
                if window._monitor_active:
                    self.report["errors"].append("monitor thread did not stop within 8 seconds")
                    self.report["passed"] = False
                window.close()
                if window.isVisible() and now < self.finish_deadline:
                    return
                if window.isVisible():
                    self.report["errors"].append("window/recorder cleanup did not finish within 8 seconds")
                    self.report["passed"] = False
                self.timer.stop()
                self.report["monitor_closed"] = not window._monitor_active
                self.report["recorder_threads_pending"] = len(window._closing_recorders)
                text_recorder = getattr(window, "_monitor_text_recorder", None)
                if text_recorder is not None:
                    log_stats = text_recorder.statistics
                    self.report["text_log"] = {"saved": log_stats.saved, "unsaved": log_stats.unsaved,
                                               "error": log_stats.error,
                                               "closed": text_recorder.wait_closed(0)}
                self.report["checks"]["all_threads_closed"] = (not window._monitor_active
                    and not window._closing_recorders
                    and window.measurement_panel._worker is None
                    and window.dashboard._projection_worker is None
                    and (text_recorder is None or text_recorder.wait_closed(0)))
                if sample_recorder is not None:
                    stats = sample_recorder.statistics
                    self.report["csv_recorder"] = {"accepted": stats.accepted, "saved": stats.saved,
                        "unsaved": stats.unsaved, "rejected": stats.rejected, "error": stats.error,
                        "closed": sample_recorder.wait_closed(0)}
                    self.report["checks"]["csv_writer_complete"] = (stats.accepted == stats.saved == target_frames
                        and stats.unsaved == stats.rejected == 0 and not stats.error and sample_recorder.wait_closed(0))
                    rows = 0
                    bad_rows = 0
                    previous_stamp = ""
                    frame_stamp = ""
                    with (output / "samples.csv").open(newline="", encoding="utf-8-sig") as stream:
                        reader = csv.DictReader(stream)
                        for row in reader:
                            sequence, channel = divmod(rows, 3)
                            sequence += 1
                            expected = struct.unpack("<fff", struct.pack("<fff", float(sequence),
                                math.sin(sequence / 100), float(sequence % 100)))[channel]
                            stamp = row["timestamp_utc"]
                            if channel == 0:
                                if stamp < previous_stamp:
                                    bad_rows += 1
                                frame_stamp = stamp
                                previous_stamp = stamp
                            if int(row["channel_index"]) != channel or float(row["value"]) != expected or stamp != frame_stamp:
                                bad_rows += 1
                            rows += 1
                    with (output / "samples.csv").open("rb") as stream:
                        csv_sha256 = hashlib.file_digest(stream, "sha256").hexdigest()
                    self.report["csv_file"] = {"rows": rows, "bad_rows": bad_rows,
                        "bytes": (output / "samples.csv").stat().st_size,
                        "sha256": csv_sha256}
                    self.report["checks"]["csv_all_frames_and_values_ordered"] = rows == target_frames * 3 and bad_rows == 0
                stable = [item["rss_bytes"] for item in self.samples
                          if item["retained"] == 100000 and item["rss_bytes"] is not None]
                if stable:
                    self.report["bounded_history_rss"] = {"min": min(stable), "max": max(stable),
                        "first_ten_median": statistics.median(stable[:10]),
                        "last_ten_median": statistics.median(stable[-10:])}
                self.report["passed"] = (self.report["passed"]
                    and all(self.report["checks"].values()) and not self.report["errors"])
                (output / "result.json").write_text(json.dumps(self.report, ensure_ascii=False, indent=2), encoding="utf-8")
                self.final_exit = 0 if self.report["passed"] else 1
                print(json.dumps({"passed": self.report["passed"], "checks": self.report["checks"],
                                  "errors": self.report["errors"], "result": str(output / "result.json")}), flush=True)
                app.quit()
                return
            stats = window.sample_store.statistics
            self.tick_intervals.append((now - self.last_tick) * 1000)
            self.last_tick = now
            elapsed = now - self.started
            if elapsed >= self.next_sample_at:
                self.next_sample_at = elapsed + 1
                self.samples.append({"elapsed": round(elapsed, 3), "parsed": stats.parsed,
                                     "retained": stats.retained, "rss_bytes": resident_bytes()})
                if len(self.samples) % 10 == 0:
                    print(json.dumps({"elapsed": round(elapsed, 1), "parsed": stats.parsed,
                                      "retained": stats.retained, "rss_bytes": self.samples[-1]["rss_bytes"]}), flush=True)
            if not self.capacity_changed and stats.parsed == 6000 and target_frames > 6000:
                checkpoint = self.checkpoint("capacity_5000")
                checkpoint["passed"] = stats.retained == 6000 and checkpoint["wave_retained"] == 5000
                self.checkpoints["capacity_5000"] = checkpoint
                if not checkpoint["passed"]:
                    self.errors.append("5000-frame capacity checkpoint failed")
                window.wave_buffer_spin.setValue(100000)
                self.capacity_changed = True
                producer_continue.set()
            if self.capacity_changed and self.pause_started is None and stats.parsed >= min(30000, target_frames // 3):
                window.waveform.set_paused(True)
                self.pause_started = now
                self.pause_before = (stats.parsed, window.waveform.displayed_sample_count)
                self.checkpoints["pause_start"] = self.checkpoint("pause_start")
            if self.pause_started is not None and not self.pause_finished and now - self.pause_started >= 2:
                checkpoint = self.checkpoint("pause_end")
                checkpoint["passed"] = stats.parsed > self.pause_before[0] and checkpoint["wave_displayed"] == self.pause_before[1]
                self.checkpoints["pause_end"] = checkpoint
                self.pause_finished = True
                window.waveform.set_paused(False)
            if (stats.retained == 100000 and self.pause_finished and not self.rescaled
                    and max(map(len, window.waveform.displayed_series), default=0) == 100000):
                raw_last = window.sample_store.snapshot()[-1].values
                count_before = max(map(len, window.waveform.displayed_series), default=0)
                window._channel_display = (ChannelDisplay(scale=2, offset=3),) + window._channel_display[1:]
                window._rebuild_display_samples()
                checkpoint = self.checkpoint("rescale")
                checkpoint["count_before"] = count_before
                checkpoint["passed"] = (count_before == checkpoint["wave_retained"] == 100000
                                         and window.sample_store.snapshot()[-1].values == raw_last)
                self.checkpoints["rescale"] = checkpoint
                self.rescaled = True
            if (producer_done.is_set() and stats.parsed == target_frames and self.observed == target_frames
                    and window.waveform.displayed_sample_count == target_frames):
                self.finish()
            elif producer_done.is_set() and producer_state["error"]:
                self.finish("producer failed")
            elif elapsed > args.seconds + 45:
                self.finish("timed out waiting for all loopback frames")

    acceptance = Acceptance()
    window.start_monitor()
    sample_recorder = None
    if args.record_csv:
        original_dialog = gui.QFileDialog.getSaveFileName
        gui.QFileDialog.getSaveFileName = lambda *_args, **_kwargs: (str(output / "samples.csv"), "CSV (*.csv)")
        try:
            window.toggle_sample_recording()
            sample_recorder = window._sample_recorder
            if sample_recorder is None:
                raise RuntimeError("Explicit CSV recording did not start")
        finally:
            gui.QFileDialog.getSaveFileName = original_dialog
    window._monitor_worker.events.connect(acceptance.observe, Qt.QueuedConnection)
    window._monitor_worker.transmitted.connect(acceptance.sent, Qt.QueuedConnection)
    acceptance.timer.start()
    app.exec()
    producer_stop.set()
    producer_continue.set()
    producer_thread.join(3)
    return acceptance.final_exit


if __name__ == "__main__":
    raise SystemExit(main())
