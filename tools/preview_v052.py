"""Offline layout evidence. Run with the project's existing Python runtime."""
from __future__ import annotations

import json
import math
from pathlib import Path

from PySide6.QtCore import QPoint
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

import qx_stc_isp.gui as gui
from qx_stc_isp.vofa.model import TelemetryEvent


def main():
    import serial
    import socket
    import os

    def forbidden(*args, **kwargs):
        raise AssertionError('Offline preview must not access devices or network')

    serial.Serial = forbidden
    socket.socket = forbidden
    gui.enumerate_ports = lambda: []
    root = Path(__file__).resolve().parents[1]
    scale = os.environ.get('QT_SCALE_FACTOR', '1')
    output = root / 'build-preview' / 'runtime-v052' / ('preview-' + scale)
    output.mkdir(parents=True, exist_ok=True)
    app = QApplication([])
    gui._load_bundled_fonts()
    font = QFont()
    available = set(QFontDatabase.families())
    font.setFamilies([name for name in ('SF Pro Text', 'SF Pro Display', 'PingFang SC', 'Microsoft YaHei UI') if name in available])
    font.setPointSize(10)
    app.setFont(font)
    window = gui.MainWindow(log_dir=output / 'logs')
    window.tabs.setCurrentIndex(1)
    window._handle_monitor_events([
        TelemetryEvent(raw=b'', samples=(math.sin(i/20), .5*math.cos(i/13)))
        for i in range(400)
    ])
    window.show()
    results = []
    for width, height in ((1920,1080),(1280,800),(1020,680)):
        window.resize(width,height)
        window.monitor_plot_splitter.restore_default_sizes()
        QTest.qWait(80)
        upper, lower = window.monitor_plot_splitter.sizes()
        controls = (window.wave_pause_button, window.sample_interval_spin,
                    window.wave_buffer_spin, window.send_button, window.send_text_edit)
        contained = True
        for widget in controls:
            point = widget.mapTo(window.monitor_plot_splitter, QPoint())
            contained &= (point.x() >= 0 and point.y() >= 0
                and point.x()+widget.width() <= window.monitor_plot_splitter.width()
                and point.y()+widget.height() <= window.monitor_plot_splitter.height())
        actions_y = window.wave_pause_button.mapToGlobal(QPoint()).y()
        settings_y = window.sample_interval_spin.mapToGlobal(QPoint()).y()
        saved = window.grab().save(str(output / f'{width}x{height}.png'))
        result = dict(size=[width,height], actual_size=[window.width(),window.height()],
            split=[upper,lower], upper_ratio=upper/(upper+lower),
            two_rows=settings_y-actions_y > 12, controls_contained=bool(contained),
            image_saved=saved, connected=window._monitor_active, sending=window._send_job.active)
        results.append(result)
        assert contained and saved and not window._monitor_active and not window._send_job.active
        if width == 1920:
            assert .72 <= result['upper_ratio'] <= .78 and not result['two_rows']
        else:
            assert result['two_rows']
    window.close()
    app.processEvents()
    (output / 'result.json').write_text(json.dumps(results, ensure_ascii=False, indent=2),encoding='utf-8')
    print(json.dumps(results, ensure_ascii=False))


if __name__ == '__main__':
    main()
