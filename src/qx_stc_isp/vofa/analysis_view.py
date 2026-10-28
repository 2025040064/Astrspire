"""Independent bounded statistics/FFT views, not VOFA's proprietary algorithms.

Spectrum uses the configured uniform sample interval, a radix-2 FFT, optional
mean removal and coherent-gain-corrected single-sided amplitude. Missing samples
are not interpolated. No source, device or plugin is opened by this module.
"""

from __future__ import annotations

import cmath
import csv
import math
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QGridLayout, QLabel, QMessageBox,
    QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from ..appearance import theme_palette


def amplitude_spectrum(values, interval_ms: float, maximum: int = 1024,
                       *, window: str = "hann", remove_mean: bool = True):
    if maximum not in (64, 128, 256, 512, 1024, 2048):
        raise ValueError("FFT窗口须为64–2048的指定二次幂")
    if window not in ("hann", "rectangular"):
        raise ValueError("未知FFT窗函数")
    if not math.isfinite(interval_ms) or interval_ms <= 0:
        raise ValueError("采样间隔必须大于0")
    latest = tuple(values)[-maximum:]
    if len(latest) < 8 or any(not math.isfinite(value) for value in latest):
        return (), (), 0
    count = 1 << (len(latest).bit_length() - 1)
    latest = latest[-count:]
    # Scale before arithmetic to avoid overflow for finite extreme inputs.
    scale = max(abs(value) for value in latest) or 1.0
    normalized = [value / scale for value in latest]
    mean = math.fsum(normalized) / count if remove_mean else 0.0
    weights = [(.5 - .5 * math.cos(2 * math.pi * i / (count - 1)))
               if window == "hann" else 1.0 for i in range(count)]
    transformed = [complex((value - mean) * weight) for value, weight in zip(normalized, weights)]
    target = 0
    for index in range(1, count):
        bit = count >> 1
        while target & bit:
            target ^= bit
            bit >>= 1
        target ^= bit
        if index < target:
            transformed[index], transformed[target] = transformed[target], transformed[index]
    length = 2
    while length <= count:
        factor = cmath.exp(-2j * math.pi / length)
        for base in range(0, count, length):
            rotation = 1 + 0j
            for offset in range(length // 2):
                even = transformed[base + offset]
                odd = transformed[base + offset + length // 2] * rotation
                transformed[base + offset] = even + odd
                transformed[base + offset + length // 2] = even - odd
                rotation *= factor
        length *= 2
    gain = math.fsum(weights)
    frequencies = tuple(index * 1000 / (count * interval_ms) for index in range(count // 2 + 1))
    amplitudes = []
    for index in range(count // 2 + 1):
        multiplier = 1 if index in (0, count // 2) else 2
        value = abs(transformed[index]) / gain * multiplier * scale
        if not math.isfinite(value):
            raise ValueError("频谱幅值超出数值范围，请调整输入量程")
        amplitudes.append(value)
    return frequencies, tuple(amplitudes), count


def histogram(values, bins: int = 32):
    if type(bins) is not int or not 4 <= bins <= 256:
        raise ValueError("直方图区间数须为4–256")
    samples = tuple(value for value in values if math.isfinite(value))[-8192:]
    if not samples:
        return (), (), 0.0
    low, high = min(samples), max(samples)
    scale = max(abs(low), abs(high), 1.0)
    left, right = low / scale, high / scale
    if left == right:
        pad = max(abs(left) * .05, 1e-6)
        left, right = left - pad, right + pad
    span = right - left
    counts = [0] * bins
    for value in samples:
        index = min(bins - 1, max(0, int((value / scale - left) / span * bins)))
        counts[index] += 1
    centers = tuple((left + (i + .5) * span / bins) * scale for i in range(bins))
    width = span / bins * scale
    if not all(math.isfinite(value) for value in centers) or not math.isfinite(width):
        raise ValueError("直方图范围过大，请调整输入量程")
    return centers, tuple(counts), width


class _AnalysisCanvas(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumSize(160, 160)
        self.palette = theme_palette("light")
        self.points = ()
        self.bars = True
        self.labels = ()
        self.x_title = ""
        self.y_title = ""
        self.message = "等待采样"
        self.color = self.palette.channel_colors[0]

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor(self.palette.surface))
        area = QRectF(68, 28, max(1, self.width() - 90), max(1, self.height() - 76))
        painter.setPen(QPen(QColor(self.palette.plot_grid), 1))
        painter.drawRect(area)
        painter.setPen(QColor(self.palette.text))
        if not self.points:
            painter.drawText(area, Qt.AlignCenter | Qt.TextWordWrap, self.message)
            return
        xs, ys = zip(*self.points)
        x_low, x_high = min(xs), max(xs)
        y_low, y_high = min(0.0, min(ys)), max(0.0, max(ys))
        x_scale = max(abs(x_low), abs(x_high), 1.0)
        y_scale = max(abs(y_low), abs(y_high), 1.0)
        xn_low, xn_high = x_low / x_scale, x_high / x_scale
        yn_low, yn_high = y_low / y_scale, y_high / y_scale
        if xn_low == xn_high:
            xn_low, xn_high = xn_low - .5, xn_high + .5
        if yn_low == yn_high:
            yn_low, yn_high = yn_low - .5, yn_high + .5
        if self.bars:
            x_pad = (xn_high - xn_low) / max(1, len(xs) - 1) * .6
            xn_low, xn_high = xn_low - x_pad, xn_high + x_pad
        y_pad = (yn_high - yn_low) * .08
        yn_low, yn_high = yn_low - y_pad, yn_high + y_pad
        def point(x, y):
            return QPointF(area.left() + (x / x_scale - xn_low) / (xn_high - xn_low) * area.width(),
                           area.bottom() - (y / y_scale - yn_low) / (yn_high - yn_low) * area.height())
        for step in range(5):
            ratio = step / 4
            y = area.bottom() - ratio * area.height()
            painter.setPen(QPen(QColor(self.palette.plot_grid), 1, Qt.DotLine))
            painter.drawLine(QPointF(area.left(), y), QPointF(area.right(), y))
            painter.setPen(QColor(self.palette.text))
            value = (yn_low + ratio * (yn_high - yn_low)) * y_scale
            painter.drawText(QRectF(0, y - 9, 62, 18), Qt.AlignRight | Qt.AlignVCenter, f"{value:.3g}")
            x = area.left() + ratio * area.width()
            x_value = (xn_low + ratio * (xn_high - xn_low)) * x_scale
            painter.drawText(QRectF(x - 32, area.bottom() + 4, 64, 18), Qt.AlignCenter, f"{x_value:.3g}")
        painter.save()
        painter.setClipRect(area)
        if self.bars:
            width = max(1.0, area.width() / max(1, len(xs)) * .72)
            zero = point(0, 0).y()
            for index, (x, y) in enumerate(self.points):
                p = point(x, y)
                color = self.palette.channel_colors[index % len(self.palette.channel_colors)] if self.labels else self.color
                painter.fillRect(QRectF(p.x() - width / 2, min(p.y(), zero), width, max(1, abs(p.y() - zero))), QColor(color))
        else:
            path = QPainterPath()
            for index, (x, y) in enumerate(self.points):
                p = point(x, y)
                path.moveTo(p) if index == 0 else path.lineTo(p)
            painter.setPen(QPen(QColor(self.color), 1.5))
            painter.drawPath(path)
        painter.restore()
        painter.setPen(QColor(self.palette.text))
        painter.drawText(QRectF(area.left(), self.height() - 24, area.width(), 20), Qt.AlignCenter, self.x_title)
        painter.drawText(QRectF(area.left(), 3, area.width(), 20), Qt.AlignLeft, self.y_title)


class AnalysisView(QWidget):
    """Snapshot analysis of the waveform's visible/paused data, at most 5 Hz."""

    def __init__(self, waveform, parent=None) -> None:
        super().__init__(parent)
        self._waveform = waveform
        self._rows = ()
        self._last_key = None
        self._channel_names = ()
        layout = QVBoxLayout(self)
        tools = QGridLayout()
        self.mode = QComboBox()
        for title, value in (("最新值柱状图", "bar"), ("分布直方图", "histogram"), ("幅度频谱", "spectrum")):
            self.mode.addItem(title, value)
        self.channel = QSpinBox()
        self.channel.setRange(1, 16)
        self.channel.setPrefix("CH ")
        self.bins = QSpinBox()
        self.bins.setRange(4, 256)
        self.bins.setValue(32)
        self.fft_size = QComboBox()
        for size in (64, 128, 256, 512, 1024, 2048):
            self.fft_size.addItem(str(size), size)
        self.fft_size.setCurrentIndex(4)
        self.window = QComboBox()
        self.window.addItem("Hann 窗", "hann")
        self.window.addItem("矩形窗", "rectangular")
        self.remove_mean = QCheckBox("去直流")
        self.remove_mean.setChecked(True)
        tools.addWidget(self.mode, 0, 0, 1, 2)
        tools.addWidget(self.channel, 0, 2)
        tools.addWidget(QLabel("直方图区间"), 1, 0)
        tools.addWidget(self.bins, 1, 1)
        tools.addWidget(self.fft_size, 1, 2)
        tools.addWidget(self.window, 2, 0)
        tools.addWidget(self.remove_mean, 2, 1)
        export = QPushButton("导出分析 CSV…")
        export.clicked.connect(self.export_csv)
        tools.addWidget(export, 2, 2)
        layout.addLayout(tools)
        self.canvas = _AnalysisCanvas()
        layout.addWidget(self.canvas, 1)
        self.status = QLabel("频谱按设置的 Δt 计算，要求等间隔采样；不是设备采样率测量。")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        for widget in (self.mode, self.channel, self.bins, self.fft_size, self.window):
            signal = widget.currentIndexChanged if isinstance(widget, QComboBox) else widget.valueChanged
            signal.connect(self.invalidate)
        self.remove_mean.toggled.connect(self.invalidate)
        self._timer = QTimer(self)
        self._timer.setInterval(200)
        self._timer.timeout.connect(self.refresh)

    def set_theme(self, theme: str) -> None:
        self.canvas.palette = theme_palette(theme)
        self.invalidate()

    def set_channel_names(self, names) -> None:
        names = tuple(names)
        if names != self._channel_names:
            self._channel_names = names
            self.invalidate()

    def invalidate(self, *_args) -> None:
        self._last_key = None
        if self.isVisible():
            self.refresh()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._timer.start()
        self.refresh()

    def hideEvent(self, event) -> None:
        self._timer.stop()
        super().hideEvent(event)

    def refresh(self) -> None:
        mode = self.mode.currentData()
        channel = self.channel.value() - 1
        self.channel.setEnabled(mode != "bar")
        self.bins.setEnabled(mode == "histogram")
        self.fft_size.setEnabled(mode == "spectrum")
        self.window.setEnabled(mode == "spectrum")
        self.remove_mean.setEnabled(mode == "spectrum")
        series = self._waveform.displayed_snapshot()
        interval = self._waveform.sample_interval_ms
        key = (mode, channel, self.bins.value(), self.fft_size.currentData(), self.window.currentData(), self.remove_mean.isChecked(),
               interval, tuple((len(values), values[-1] if values else None) for values in series), self._waveform.displayed_sample_count,
               self._waveform.paused, self._waveform.visible_channels)
        if key == self._last_key:
            return
        self._last_key = key
        self.canvas.points, self.canvas.labels, self._rows = (), (), ()
        self.canvas.message = "等待所选通道的有限采样值"
        self.canvas.color = self.canvas.palette.channel_colors[channel % len(self.canvas.palette.channel_colors)]
        try:
            if mode == "bar":
                points = [(i + 1, values[-1]) for i, values in enumerate(series)
                          if values and math.isfinite(values[-1]) and self._waveform.visible_channels[i]]
                self.canvas.points = tuple(points)
                self.canvas.labels = tuple(self._channel_names[i - 1] if i <= len(self._channel_names) else f"CH{i}" for i, _ in points)
                self.canvas.x_title, self.canvas.y_title = "通道序号", "最新值"
                self._rows = tuple((name, x, y) for name, (x, y) in zip(self.canvas.labels, points))
                self.status.setText("；".join(f"{name}: {y:.6g}" for name, _x, y in self._rows) or "等待采样")
            elif channel < len(series):
                values = tuple(series[channel])[-8192:]
                if mode == "histogram":
                    xs, ys, width = histogram(values, self.bins.value())
                    self.canvas.points = tuple(zip(xs, ys))
                    self.canvas.x_title, self.canvas.y_title = "采样值（区间中心）", "计数"
                    self.status.setText(f"CH{channel + 1} · 最近 {len(values)} 点 · 区间宽度 {width:.6g}")
                else:
                    xs, ys, count = amplitude_spectrum(values, interval, self.fft_size.currentData(),
                                                      window=self.window.currentData(), remove_mean=self.remove_mean.isChecked())
                    self.canvas.points = tuple(zip(xs, ys))
                    self.canvas.x_title, self.canvas.y_title = "频率 / Hz", "单边幅度"
                    self.canvas.message = "频谱至少需要8个连续有限采样值"
                    peak = max(range(1, len(ys)), key=ys.__getitem__, default=0)
                    detail = f" · 峰值 {xs[peak]:.6g} Hz / {ys[peak]:.6g}" if ys else ""
                    self.status.setText(f"N={count} · Δt={interval:g}ms（设定值，要求等间隔采样）" + detail)
                self._rows = tuple((f"CH{channel + 1}", x, y) for x, y in self.canvas.points)
            else:
                self.status.setText("所选通道尚无数据")
        except (ValueError, OverflowError) as exc:
            self.canvas.message = str(exc)
            self.status.setText(str(exc))
        self.canvas.bars = mode != "spectrum"
        self.canvas.update()

    def export_csv(self) -> None:
        self.refresh()
        if not self._rows:
            return
        filename, _ = QFileDialog.getSaveFileName(self, "导出当前分析", "analysis.csv", "CSV (*.csv)")
        if not filename:
            return
        try:
            with Path(filename).open("x", encoding="utf-8-sig", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(("mode", "channel", "x", "y", "configured_interval_ms"))
                for channel, x, y in self._rows:
                    writer.writerow((self.mode.currentData(), channel, x, y, self._waveform.sample_interval_ms))
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "分析导出失败", str(exc))

    def export_config(self) -> dict:
        return dict(mode=self.mode.currentData(), channel=self.channel.value() - 1, bins=self.bins.value(),
                    fft_size=self.fft_size.currentData(), window=self.window.currentData(), remove_mean=self.remove_mean.isChecked())

    def load_config(self, config: dict) -> None:
        validate_analysis_config(config)
        self.mode.setCurrentIndex(self.mode.findData(config.get("mode", "bar")))
        self.channel.setValue(config.get("channel", 0) + 1)
        self.bins.setValue(config.get("bins", 32))
        self.fft_size.setCurrentIndex(self.fft_size.findData(config.get("fft_size", 1024)))
        self.window.setCurrentIndex(self.window.findData(config.get("window", "hann")))
        self.remove_mean.setChecked(config.get("remove_mean", True))
        self.invalidate()


def validate_analysis_config(config: dict) -> None:
    if not isinstance(config, dict) or set(config) - {"mode", "channel", "bins", "fft_size", "window", "remove_mean"}:
        raise ValueError("分析视图配置字段无效")
    if config.get("mode", "bar") not in ("bar", "histogram", "spectrum"):
        raise ValueError("分析视图类型无效")
    for key, default, low, high in (("channel", 0, 0, 15), ("bins", 32, 4, 256)):
        value = config.get(key, default)
        if type(value) is not int or not low <= value <= high:
            raise ValueError(f"分析参数 {key} 超出范围")
    if type(config.get("fft_size", 1024)) is not int or config.get("fft_size", 1024) not in (64, 128, 256, 512, 1024, 2048):
        raise ValueError("FFT窗口无效")
    if config.get("window", "hann") not in ("hann", "rectangular") or type(config.get("remove_mean", True)) is not bool:
        raise ValueError("FFT窗函数或去直流配置无效")
