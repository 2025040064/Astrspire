"""Offline GUI evidence using app.exec; never open ports or flash a device."""
import argparse
import hashlib
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

import serial
from PySide6.QtCore import QTimer
from PySide6.QtGui import QFont, QFontDatabase, QRawFont
from PySide6.QtWidgets import QApplication, QTabWidget

import qx_stc_isp.gui as gui
from qx_stc_isp.vofa.model import TelemetryEvent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    if not output.is_relative_to(root / "build-preview") or root.drive.upper() != "F:":
        raise ValueError("Evidence must stay under the F-drive task build-preview")
    if (output / "result.json").exists():
        raise FileExistsError("Use a fresh evidence directory")
    output.mkdir(parents=True, exist_ok=True)
    calls = []
    def forbidden(*args, **kwargs):
        calls.append("serial-open")
        raise RuntimeError("GUI evidence must never open hardware")
    serial.Serial = forbidden
    gui.enumerate_ports = lambda: []
    app = QApplication.instance() or QApplication([])
    gui._load_bundled_fonts()
    system_font = Path("C:/Windows/Fonts/msyh.ttc")
    if system_font.is_file():
        QFontDatabase.addApplicationFont(str(system_font))
    app.setFont(QFont("Microsoft YaHei UI", 10))
    font = QRawFont.fromFont(app.font())
    if not font.supportsCharacter(ord("A")) or not font.supportsCharacter(ord("中")):
        raise RuntimeError("Readable Latin/Chinese fonts were not loaded")
    window = gui.MainWindow(log_dir=output / "logs")
    report = {"scope": "Offline GUI only; synthetic samples; no hardware", "screens": [],
              "errors": [], "hardware_calls": calls}
    source_hashes = {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
                     for path in (root / "src").rglob("*.py")}
    report["source_sha256"] = source_hashes
    window.show()
    tool = None

    def save(name, widget):
        path = output / name
        if not widget.grab().save(str(path)):
            raise RuntimeError("Screenshot failed")
        report["screens"].append({"file": name, "width": widget.width(), "height": widget.height()})

    def guarded(callback):
        def invoke():
            try:
                callback()
            except Exception as exc:
                report["errors"].append(f"{type(exc).__name__}: {exc}")
                finish()
        return invoke

    def populate():
        window.tabs.setCurrentIndex(1)
        window.resize(1920, 1080)
        window.sample_interval_spin.setValue(10)
        start = datetime.now(timezone.utc)
        events = [TelemetryEvent(raw=b"", samples=(math.sin(index / 25), math.cos(index / 25), index / 1000),
                                 source_timestamp=start + timedelta(milliseconds=10 * index),
                                 received_at=start) for index in range(1000)]
        window._handle_monitor_events(events)
        window.monitor_status_label.setText("离线界面示例 · 合成采样 · 未连接硬件")
        window.send_text_edit.setText("ASTR:HELLO")
        QTimer.singleShot(500, guarded(main_screens))

    def main_screens():
        save("monitor-1920.png", window)
        window.resize(1280, 800)
        QTimer.singleShot(100, guarded(small_main))

    def small_main():
        save("monitor-1280.png", window)
        window.resize(window.minimumSize())
        QTimer.singleShot(100, guarded(minimum_main))

    def minimum_main():
        scroll = window.monitor_side_scroll
        if scroll.widget().width() > scroll.viewport().width():
            raise AssertionError("Monitor sidebar clipped at minimum size")
        save("monitor-minimum.png", window)
        window.tabs.setCurrentIndex(0)
        window.mode_combo.setCurrentIndex(1)
        QTimer.singleShot(100, guarded(isp_screen))

    def isp_screen():
        nonlocal tool
        scroll = window.isp_side_scroll
        if scroll.widget().width() > scroll.viewport().width():
            raise AssertionError("ISP sidebar clipped at minimum size")
        save("isp-minimum.png", window)
        window.open_serial_tools()
        tool = window.serial_tools_dialog
        tool.generate()
        packet = tool._payload
        if not packet:
            raise AssertionError("Template did not generate bytes")
        window.packet_store.append("RX", packet[:4])
        window.packet_store.append("TX", b"offline-demo")
        window.packet_store.append("RX", packet[4:])
        tool.refresh_packets()
        QTimer.singleShot(100, guarded(packet_screen))

    def packet_screen():
        save("tools-packets.png", tool)
        tool.findChild(QTabWidget).setCurrentIndex(1)
        tool.preview_offline()
        parsed = json.loads(tool.protocol_result.toPlainText())
        if not parsed["frames"][0]["checksum_valid"]:
            raise AssertionError("Offline protocol preview failed")
        save("tools-protocol.png", tool)
        tool.resize(tool.minimumSize())
        QTimer.singleShot(100, guarded(minimum_tools))

    def minimum_tools():
        save("tools-minimum.png", tool)
        tool.fill_send()
        if window._monitor_worker is not None or window._tx_bytes:
            raise AssertionError("Configuration action produced a connection or TX")
        finish()

    closing = False
    close_attempts = 0
    def finish():
        nonlocal closing, close_attempts
        closing = True
        close_attempts += 1
        if not window.close():
            if close_attempts < 40:
                QTimer.singleShot(100, finish)
                return
            report["errors"].append("Window did not close normally")
        report["passed"] = not report["errors"] and not calls
        report["closed"] = not window.isVisible()
        (output / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"passed": report["passed"], "screens": len(report["screens"]),
                          "errors": report["errors"], "report": str(output / "result.json")}, ensure_ascii=False))
        app.quit()

    QTimer.singleShot(100, guarded(populate))
    QTimer.singleShot(20000, lambda: (report["errors"].append("GUI check timeout"), finish()) if not closing else None)
    app.exec()
    return 0 if report.get("passed") and report.get("closed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
