"""Capture the serial assistant UI with device and network I/O forbidden."""

import json
from pathlib import Path
import socket

import serial
from PySide6.QtGui import QFont
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

import qx_stc_isp.gui as gui


def main():
    def forbidden(*_args, **_kwargs):
        raise AssertionError("UI preview must not access serial ports or networks")

    serial.Serial = forbidden
    socket.socket = forbidden
    gui.enumerate_ports = lambda: []
    output = Path(__file__).resolve().parents[1] / "build-preview/runtime-g431-uart-20261027/xcom-preview"
    output.mkdir(parents=True, exist_ok=True)
    app = QApplication([])
    gui._load_bundled_fonts()
    app.setFont(QFont("PingFang SC", 10))
    window = gui.MainWindow(log_dir=output / "logs")
    window.tabs.setCurrentIndex(1)
    window.show()
    window.serial_assistant_button.click()
    checks = []
    try:
        for width, height in ((1920, 1080), (1280, 800), (1020, 680)):
            window.resize(width, height)
            window.monitor_plot_splitter.restore_default_sizes()
            QTest.qWait(150)
            visible = all(widget.isVisible() for widget in
                          (window.serial_assistant_button, window.serial_dtr_check, window.serial_rts_check))
            image_path = output / f"{width}x{height}.png"
            saved = window.grab().save(str(image_path))
            checks.append(dict(size=[width, height], actual_size=[window.width(), window.height()],
                               controls_visible=visible, image_saved=saved,
                               connected=window._monitor_active, sending=window._send_job.active))
            assert visible and saved and not window._monitor_active and not window._send_job.active
            assert not window.serial_dtr_check.isChecked() and not window.serial_rts_check.isChecked()
    finally:
        window.close()
        app.processEvents()
    assert window.measurement_panel._worker is None
    assert window.dashboard._projection_worker is None
    (output / "result.json").write_text(json.dumps(checks, indent=2), encoding="utf-8")
    print(json.dumps(checks, indent=2))


if __name__ == "__main__":
    main()
