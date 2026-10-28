"""Offline dev3 layout/measurement evidence with all device I/O forbidden."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
import socket

import serial
from PySide6.QtCore import QPoint
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

import qx_stc_isp.gui as gui
from qx_stc_isp.vofa.model import TelemetryEvent


def main():
    def forbidden(*_args, **_kwargs):
        raise AssertionError("Offline layout evidence must not open hardware or network")

    serial.Serial = forbidden
    socket.socket = forbidden
    gui.enumerate_ports = lambda: []
    root = Path(__file__).resolve().parents[1]
    scale = os.environ.get("QT_SCALE_FACTOR", "1")
    output = root / "build-preview/runtime-v060-dev3" / ("preview-" + scale)
    output.mkdir(parents=True, exist_ok=True)
    app = QApplication([])
    gui._load_bundled_fonts()
    available = set(QFontDatabase.families())
    font = QFont()
    font.setFamilies([name for name in ("SF Pro Text", "SF Pro Display", "PingFang SC", "Microsoft YaHei UI")
                      if name in available])
    font.setPointSize(10)
    app.setFont(font)
    window = gui.MainWindow(log_dir=output / "logs")
    window.tabs.setCurrentIndex(1)
    window.channel_names_edit.setText("正弦,余弦,方波")
    window.wave_buffer_spin.setValue(4000)
    window.monitor_values_tabs.setCurrentIndex(1)
    window.show()
    window._demo_active = True
    window._handle_monitor_events([TelemetryEvent(raw=b"", samples=(math.sin(i / 90),
        .6 * math.cos(i / 135), .4 if i % 300 < 150 else -.4)) for i in range(4000)])
    window._demo_active = False
    window._update_monitor_controls()
    window.monitor_status_label.setText("离线界面检查：合成采样 · 未连接设备")
    results = []
    try:
        for width, height in ((1920, 1080), (1280, 800), (1020, 680)):
            window.resize(width, height)
            window.monitor_plot_splitter.restore_default_sizes()
            for _step in range(40):
                QTest.qWait(25)
                if window.measurement_panel.summaries:
                    break
            if not window.measurement_panel.summaries:
                raise AssertionError("Measurement panel did not display results")
            controls = (window.wave_pause_button, window.sample_interval_spin,
                        window.wave_buffer_spin, window.send_button, window.send_text_edit)
            contained = True
            for widget in controls:
                point = widget.mapTo(window.monitor_plot_splitter, QPoint())
                contained &= (point.x() >= 0 and point.y() >= 0
                    and point.x() + widget.width() <= window.monitor_plot_splitter.width()
                    and point.y() + widget.height() <= window.monitor_plot_splitter.height())
            screenshot = output / f"{width}x{height}.png"
            saved = window.grab().save(str(screenshot))
            split = window.monitor_plot_splitter.sizes()
            result = dict(size=[width, height], actual_size=[window.width(), window.height()],
                split=split, upper_ratio=split[0] / sum(split), controls_contained=bool(contained),
                plot_rect=[window.waveform.plot_rect().width(), window.waveform.plot_rect().height()],
                measurement_channels=len(window.measurement_panel.summaries),
                connected=window._monitor_active, sending=window._send_job.active,
                image_saved=saved, image=str(screenshot))
            results.append(result)
            assert contained and saved and not window._monitor_active and not window._send_job.active
        window.measurement_panel.tree.expandAll()
        window.resize(1920, 1080)
        window.monitor_plot_splitter.restore_default_sizes()
        QTest.qWait(250)
        assert window.grab().save(str(output / "measurements-expanded.png"))
    finally:
        window.close()
        app.processEvents()
    assert window.measurement_panel._worker is None
    assert window.dashboard._projection_worker is None
    (output / "result.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
