"""Bounded PyQtGraph waveform with shared data and immediate inspection controls.

Pausing freezes the displayed snapshot while acquisition continues. Time views
use sample indices internally; XY views use paired channel values. Neither
display mode nor styling changes the retained samples or sends device commands.
"""

from __future__ import annotations

import math
import re
import sys
from collections import deque
from itertools import islice
from html import escape
from pathlib import Path

import numpy as np
import pyqtgraph as pg

from PySide6.QtCore import QFile, QIODevice, QPoint, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QKeyEvent, QMouseEvent, QWheelEvent
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget

from ..appearance import theme_palette
from .projection import PlotBuffer, display_envelope
from .sample_store import SampleCursor


class _PlotAxis(pg.AxisItem):
    def __init__(self, orientation, formatter):
        super().__init__(orientation=orientation)
        self._formatter = formatter
        self.enableAutoSIPrefix(False)

    def tickStrings(self, values, scale, spacing):
        return [self._formatter(value) for value in values]


class WaveformWidget(QWidget):
    """Read-only plotting surface; never opens a source or sends a command.

    Wheel / +/- zoom the time axis. Left drag pans after the system drag
    threshold. Esc cancels an active drag, Home restores automatic fit, and
    Space toggles display pause. Every change is immediate (no animation).
    """

    _COLORS = ("#d51f75", "#168a42", "#245ec7", "#d4840d", "#8c3bb3", "#078b98")
    MAX_CHANNELS = 16
    MAX_SAMPLES = 100000

    cursor_moved = Signal(float, float)  # time: seconds,Y; XY: channel X,Y
    cursor_cleared = Signal()
    cursor_text_changed = Signal(str)
    view_changed = Signal(float, float, bool)  # sample-index range, auto-fit
    paused_changed = Signal(bool)
    sample_rejected = Signal(str)
    cursor_samples_changed = Signal(object)  # tuple[(index, name, value), ...]

    def __init__(self, parent: QWidget | None = None, *, max_samples: int = 800) -> None:
        super().__init__(parent)
        self._validate_max_samples(max_samples)
        self._max_samples = max_samples
        self._series: list[deque[float]] = []
        self._projection_columns = None
        self._projection_summary = ((), ())
        self._paused_summary = ((), ())
        self._names: list[str] = []
        self._sample_count = 0
        self._visible = [True] * self.MAX_CHANNELS
        self._paused = False
        self._paused_series: tuple[tuple[float, ...], ...] | None = None
        self._paused_names: tuple[str, ...] = ()
        self._paused_sample_count = 0
        self._sample_interval_ms = 1.0
        self._view_x: tuple[float, float] | None = None
        self._cursor: tuple[float, float] | None = None
        self._drag_origin: QPointF | None = None
        self._drag_initial_view: tuple[float, float] | None = None
        self._drag_initial_auto = True
        self._drag_initial_axes: dict[str, float | None] | None = None
        self._drag_original_view: tuple[float, float] | None = None
        self._dragging = False
        self._theme = "light"
        self._theme_palette = theme_palette(self._theme)
        self._custom_colors: dict[int, str] = {}
        self._grid_visible = True
        self._line_width = 1.3
        self._mode = "time"
        self._xy_channels = (0, 1)
        self._axis_ranges: dict[str, float | None] = dict.fromkeys(("x_min", "x_max", "y_min", "y_max"))
        self._cursor_samples: tuple[tuple[int, str, float], ...] = ()
        self._cursor_sample_index: int | None = None
        self._xy_cache_key = None
        self._xy_cache = ()
        self._xy_notify_timer = QTimer(self)
        self._xy_notify_timer.setSingleShot(True)
        self._xy_notify_timer.timeout.connect(self._notify_view)
        self._plot_buffer = None
        self._paused_plot_buffer = None
        self._projection_managed = False
        self._render_key = None
        self._render_x_scale = self._render_y_scale = 1.0
        self._plot_bounds = ()
        self._paused_plot_bounds = ()
        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(16)
        self._render_timer.timeout.connect(self._sync_plot)
        axes = {
            "bottom": _PlotAxis("bottom", self._format_x_tick),
            "left": _PlotAxis("left", lambda value: f"{value * self._render_y_scale:.3g}"),
        }
        self.plot_widget = pg.PlotWidget(axisItems=axes, background=self._theme_palette.surface)
        self.plot_widget.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.plot_widget.setFocusPolicy(Qt.NoFocus)
        self.plot_widget.setMenuEnabled(False)
        self.plot_widget.hideButtons()
        self.plot_widget.setMouseEnabled(x=False, y=False)
        self.plot_widget.getViewBox().disableAutoRange()
        self.plot_widget.getViewBox().setDefaultPadding(0)
        self._curves = [self.plot_widget.plot(connect="finite", skipFiniteCheck=False)
                        for _ in range(self.MAX_CHANNELS)]
        for curve in self._curves:
            curve.hide()
        self._legend = self.plot_widget.addLegend(offset=(8, 8))
        self._legend_names = ()
        self._vertical_cursor = pg.InfiniteLine(angle=90, movable=False)
        self._horizontal_cursor = pg.InfiniteLine(angle=0, movable=False)
        self._empty_label = pg.TextItem("暂无可见采样", anchor=(.5, .5))
        self._pause_label = pg.TextItem("显示暂停", anchor=(1, 0))
        for item in (self._vertical_cursor, self._horizontal_cursor, self._empty_label, self._pause_label):
            self.plot_widget.addItem(item, ignoreBounds=True)
        self._vertical_cursor.hide()
        self._horizontal_cursor.hide()
        self._pause_label.hide()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.plot_widget)
        self.setMinimumSize(120, 200)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.CrossCursor)
        self.setAccessibleName("多通道波形")
        self.setAccessibleDescription("滚轮或加减键缩放，拖动平移，Home 自动适应，空格暂停，Esc 取消拖动")
        self.setToolTip("滚轮缩放 · 拖动平移 · Home 自动适应 · 空格暂停")

    @property
    def theme(self) -> str:
        return self._theme

    @property
    def channel_colors(self) -> tuple[str, ...]:
        colors = self._theme_palette.channel_colors
        return tuple(self._custom_colors.get(index, colors[index % len(colors)]) for index in range(self.MAX_CHANNELS))

    def set_channel_color(self, index: int, color: str) -> None:
        if type(index) is not int or not 0 <= index < self.MAX_CHANNELS:
            raise ValueError("通道索引必须为0–15")
        if not isinstance(color, str) or re.fullmatch(r"#[0-9a-fA-F]{6}", color) is None:
            raise ValueError("通道颜色必须为#RRGGBB")
        self._custom_colors[index] = color.lower()
        self.update()

    def reset_channel_colors(self) -> None:
        self._custom_colors.clear()
        self.update()

    @property
    def custom_channel_colors(self) -> dict[int, str]:
        return dict(self._custom_colors)

    @property
    def grid_visible(self) -> bool:
        return self._grid_visible

    def set_grid_visible(self, visible: bool) -> None:
        if type(visible) is not bool:
            raise ValueError("网格显隐必须为布尔值")
        self._grid_visible = visible
        self.update()

    @property
    def line_width(self) -> float:
        return self._line_width

    def set_line_width(self, width: float) -> None:
        if type(width) not in (int, float) or not .5 <= width <= 5:
            raise ValueError("线宽必须为0.5–5")
        self._line_width = float(width)
        self.update()

    @property
    def mode(self) -> str:
        return self._mode

    def set_mode(self, mode: str) -> None:
        if mode not in ("time", "xy"):
            raise ValueError("波形模式必须为time或xy")
        if mode != self._mode:
            self._mode = mode
            self._cursor = None
            self._cursor_samples = ()
            self._cursor_sample_index = None
            self.cursor_cleared.emit()
            self.cursor_text_changed.emit("—")
            self.cursor_samples_changed.emit(())
            self.reset_view()

    @property
    def xy_channels(self) -> tuple[int, int]:
        return self._xy_channels

    def set_xy_channels(self, x: int | None, y: int | None) -> None:
        if x is None and y is None:
            self.set_mode("time")
            return
        if any(type(index) is not int or not 0 <= index < self.MAX_CHANNELS for index in (x, y)):
            raise ValueError("XY通道必须为0–15整数，或同时None关闭XY")
        self._xy_channels = (x, y)
        self._cursor = None
        self._cursor_samples = ()
        self._cursor_sample_index = None
        self.cursor_cleared.emit()
        self.cursor_text_changed.emit("—")
        self.cursor_samples_changed.emit(())
        self.reset_view()

    @property
    def axis_ranges(self) -> dict[str, float | None]:
        """Manual X limits are seconds in time mode, channel units in XY mode."""
        return dict(self._axis_ranges)

    def set_axis_ranges(self, x_min=None, x_max=None, y_min=None, y_max=None) -> None:
        values = dict(x_min=x_min, x_max=x_max, y_min=y_min, y_max=y_max)
        for axis in ("x", "y"):
            low, high = values[f"{axis}_min"], values[f"{axis}_max"]
            if (low is None) != (high is None):
                raise ValueError("每一轴上下限必须成对填写，或都留空自动适应")
            if low is not None:
                if any(type(value) not in (int, float) or abs(value) > sys.float_info.max or not math.isfinite(value) for value in (low, high)):
                    raise ValueError("坐标轴范围必须为有限数")
                if low >= high or not math.isfinite(high - low):
                    raise ValueError("坐标轴下限必须小于上限且跨度有限")
                values[f"{axis}_min"], values[f"{axis}_max"] = float(low), float(high)
        if self._mode == "time" and values["x_min"] is not None:
            if any(not math.isfinite(values[key] * (1000 / self._sample_interval_ms)) for key in ("x_min", "x_max")):
                raise ValueError("X轴时间范围过大，无法转换为采样索引")
        self._axis_ranges = values
        self._view_x = None
        self._notify_view()

    def export_png(self, path: str | Path) -> None:
        """Export the current displayed snapshot; never replace an existing file."""
        destination = Path(path)
        output = QFile(str(destination))
        if not output.open(QIODevice.WriteOnly | QIODevice.NewOnly):
            if destination.exists():
                raise FileExistsError(f"图片已存在，未覆盖：{destination}")
            raise OSError(output.errorString())
        try:
            pixmap = self.grab()
            if pixmap.isNull() or not pixmap.save(output, "PNG"):
                raise OSError("波形图片保存失败；已创建的文件可能不完整")
        finally:
            output.close()

    @property
    def cursor_samples(self) -> tuple[tuple[int, str, float], ...]:
        return self._cursor_samples

    @property
    def cursor_sample_index(self) -> int | None:
        return self._cursor_sample_index

    def set_theme(self, theme: str) -> None:
        """Change colours only; samples, inspection state and focus survive."""
        palette = theme_palette(theme)
        if theme == self._theme and palette == self._theme_palette:
            return
        self._theme, self._theme_palette = theme, palette
        self.update()

    @staticmethod
    def _validate_max_samples(value: int) -> None:
        if isinstance(value, bool) or not isinstance(value, int) or not 16 <= value <= 100000:
            raise ValueError("波形缓存上限必须为 16–100000 个点")

    @property
    def max_samples(self) -> int:
        return self._max_samples

    def set_max_samples(self, value: int) -> None:
        self._validate_max_samples(value)
        self._max_samples = value
        self._series = [deque(series, maxlen=value) for series in self._series]
        if self._projection_columns is not None:
            self._projection_columns = tuple(series[-value:] for series in self._projection_columns)
            self._projection_summary = ((), ())
        if self._paused_series is not None:
            self._paused_series = tuple(series[-value:] for series in self._paused_series)
            self._paused_summary = ((), ())
        self._notify_view()

    @property
    def paused(self) -> bool:
        return self._paused

    def set_paused(self, value: bool) -> None:
        value = bool(value)
        if value == self._paused:
            return
        if value:
            self._paused_series = self.displayed_series
            self._paused_summary = self._projection_summary
            self._paused_names = tuple(self._names)
            self._paused_sample_count = self._sample_count
            self._paused_plot_buffer = self._current_plot_buffer()
            if self._paused_plot_buffer is None or self._paused_plot_buffer.cursor.sequence != self._sample_count:
                self._paused_plot_buffer = PlotBuffer.from_columns(self._paused_series, SampleCursor(0, self._sample_count), 0)
            self._paused_plot_bounds = self._plot_bounds
        else:
            self._paused_series = None
            self._paused_names = ()
            self._paused_plot_buffer = None
            self._paused_plot_bounds = ()
        self._paused = value
        self.paused_changed.emit(value)
        self._notify_view()

    @property
    def displayed_series(self) -> tuple[tuple[float, ...], ...]:
        if self._paused_series is not None:
            return self._paused_series
        if self._projection_columns is not None:
            return self._projection_columns
        return tuple(tuple(series) for series in self._series)

    def displayed_snapshot(self, maximum: int = 8192) -> tuple[tuple[float, ...], ...]:
        """Bounded chronological tails for independent analysis views."""
        if type(maximum) is not int or not 8 <= maximum <= 8192:
            raise ValueError("分析快照上限须为8–8192")
        source = self._live_or_paused_series()
        return tuple(tuple(reversed(tuple(islice(reversed(series), maximum)))) for series in source)

    @property
    def displayed_sample_count(self) -> int:
        return self._count()

    @property
    def displayed_names(self) -> tuple[str, ...]:
        return self._paused_names if self._paused else tuple(self._names)

    @property
    def time_window_automatic(self) -> bool:
        return self._view_x is None and self._axis_ranges['x_min'] is None

    def _live_or_paused_series(self):
        if self._paused_series is not None:
            return self._paused_series
        return self._projection_columns if self._projection_columns is not None else self._series

    def set_projection(self, snapshot, names) -> None:
        """Adopt trusted immutable display columns without copying per card."""
        from .projection import ProjectionSnapshot
        if not isinstance(snapshot, ProjectionSnapshot):
            raise ValueError("显示快照类型无效")
        columns = snapshot.columns[:self.MAX_CHANNELS]
        summary = (snapshot.bounds, snapshot.envelopes)
        if any(len(column) > self._max_samples for column in columns):
            columns = tuple(column[-self._max_samples:] for column in columns)
            summary = ((), ())
        self._series.clear()
        self._projection_columns = columns
        self._plot_buffer = snapshot.plot_buffer or PlotBuffer.from_columns(snapshot.columns, snapshot.cursor, snapshot.generation)
        self._plot_bounds = snapshot.bounds
        self._projection_summary = summary
        self._names = [str(name)[:64] for name in names[:len(columns)]]
        self._sample_count = snapshot.cursor.sequence
        self._xy_cache_key = None
        self._notify_view()

    @property
    def plot_buffer(self):
        return self._paused_plot_buffer if self._paused and self._paused_plot_buffer is not None else self._current_plot_buffer()

    def set_projection_managed(self, managed: bool):
        self._projection_managed = bool(managed)

    def _current_plot_buffer(self):
        if self._plot_buffer is not None and (self._projection_managed or self._plot_buffer.cursor.sequence == self._sample_count):
            return self._plot_buffer
        if self._projection_managed:
            return None
        self._plot_buffer = PlotBuffer.from_columns(self.displayed_series, SampleCursor(0, self._sample_count), 0)
        return self._plot_buffer

    @property
    def visible_channels(self) -> tuple[bool, ...]:
        return tuple(self._visible)

    @property
    def sample_interval_ms(self) -> float:
        return self._sample_interval_ms

    def set_sample_interval_ms(self, value: float) -> None:
        value = float(value)
        if not math.isfinite(value) or not 0.000001 <= value <= 1000000:
            raise ValueError("采样间隔必须为 0.000001–1000000 ms")
        if self._mode == "time" and self._axis_ranges["x_min"] is not None and any(
            not math.isfinite(self._axis_ranges[key] * (1000 / value)) for key in ("x_min", "x_max")
        ):
            raise ValueError("当前固定X轴范围不适用于该采样间隔")
        self._sample_interval_ms = value
        if self.cursor_position is not None:
            self._emit_cursor()
        self.update()

    @property
    def channel_visibility(self) -> tuple[bool, ...]:
        return tuple(self._visible)

    def set_channel_visible(self, index: int, visible: bool) -> None:
        if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < self.MAX_CHANNELS:
            raise ValueError("通道索引必须为 0–15")
        self._visible[index] = bool(visible)
        if self._cursor is not None:
            self._emit_cursor()
        self.update()

    @property
    def cursor_position(self) -> tuple[float, float] | None:
        if self._cursor is None:
            return None
        return (self._cursor[0] * (self._sample_interval_ms / 1000) if self._mode == "time" else self._cursor[0]), self._cursor[1]

    @property
    def auto_fit_enabled(self) -> bool:
        return self._view_x is None and all(value is None for value in self._axis_ranges.values())

    def _count(self) -> int:
        return self._paused_sample_count if self._paused else self._sample_count

    def _data_bounds(self) -> tuple[float, float]:
        if self._mode == "xy":
            points = tuple(point for point in self._xy_points() if math.isfinite(point[1]) and math.isfinite(point[2]))
            if not points:
                return -1.0, 1.0
            return self._expanded_range(min(point[1] for point in points), max(point[1] for point in points))
        lengths = [len(series) for series in self._live_or_paused_series()]
        count = self._count()
        start = max(0, count - max(lengths, default=0))
        return float(start), float(max(start + 1, count - 1))

    def _clamp_view(self, start: float, end: float) -> tuple[float, float]:
        low, high = self._data_bounds()
        full_span = high - low
        if not all(math.isfinite(value) for value in (start, end, full_span, end - start)):
            return low, high
        minimum_span = 1.0 if self._mode == "time" else max(1e-12, full_span * 1e-6)
        span = min(full_span, max(minimum_span, end - start))
        start = max(low, min(high - span, start))
        return start, start + span

    @property
    def view_range(self) -> tuple[float, float]:
        if self._axis_ranges["x_min"] is not None:
            factor = 1000 / self._sample_interval_ms if self._mode == "time" else 1
            return self._axis_ranges["x_min"] * factor, self._axis_ranges["x_max"] * factor
        return self._data_bounds() if self._view_x is None else self._clamp_view(*self._view_x)

    def reset_view(self) -> None:
        self._view_x = None
        self._axis_ranges = dict.fromkeys(("x_min", "x_max", "y_min", "y_max"))
        self._notify_view()

    def auto_fit(self) -> None:
        self.reset_view()

    def _notify_view(self) -> None:
        self.view_changed.emit(*self.view_range, self.auto_fit_enabled)
        self.update()

    def zoom_view(self, factor: float, anchor_fraction: float = 0.5) -> bool:
        if not math.isfinite(factor) or factor <= 0 or not math.isfinite(anchor_fraction):
            raise ValueError("缩放参数必须为有限正数")
        anchor_fraction = max(0.0, min(1.0, anchor_fraction))
        left, right = self.view_range
        full_left, full_right = self._data_bounds()
        full_span = full_right - full_left
        if not math.isfinite(full_span) or not math.isfinite(right - left):
            return False
        minimum_span = 1.0 if self._mode == "time" else max(1e-12, full_span * 1e-6)
        span = min(full_span, max(minimum_span, (right - left) / factor))
        anchor = left + (right - left) * anchor_fraction
        result = self._clamp_view(anchor - span * anchor_fraction, anchor + span * (1 - anchor_fraction))
        if result == (left, right):
            return False
        self._axis_ranges["x_min"] = self._axis_ranges["x_max"] = None
        self._view_x = result
        self._notify_view()
        return True

    def clear(self) -> None:
        self._xy_notify_timer.stop()
        self._series.clear()
        self._plot_buffer = None
        self._paused_plot_buffer = None
        self._plot_bounds = self._paused_plot_bounds = ()
        self._render_key = None
        self._projection_columns = None
        self._projection_summary = ((), ())
        self._paused_summary = ((), ())
        self._names.clear()
        self._sample_count = 0
        self._paused_series = () if self._paused else None
        self._paused_names = ()
        self._paused_sample_count = 0
        self._cursor = None
        self._cursor_samples = ()
        self._cursor_sample_index = None
        self._xy_cache_key = None
        self._xy_cache = ()
        self._drag_origin = None
        self._dragging = False
        self.setCursor(Qt.CrossCursor)
        self.cursor_cleared.emit()
        self.cursor_samples_changed.emit(())
        self.cursor_text_changed.emit("—")
        self.reset_view()

    def append_samples(self, samples: tuple[float, ...], names: list[str]) -> bool:
        if not samples:
            return False
        try:
            values = tuple(float(value) for value in samples[:self.MAX_CHANNELS])
        except (TypeError, ValueError, OverflowError):
            self.sample_rejected.emit("波形采样包含非法数值")
            return False
        if not all(math.isfinite(value) for value in values):
            self.sample_rejected.emit("波形采样包含非有限值，已忽略该帧")
            return False
        if self._projection_columns is not None:
            self._series = [deque(series, maxlen=self._max_samples) for series in self._projection_columns]
            self._projection_columns = None
            self._projection_summary = ((), ())
        while len(self._series) < len(values):
            self._series.append(deque(maxlen=self._max_samples))
        del self._series[len(values):]
        self._names = [str(name)[:64] for name in names[:len(values)]]
        self._sample_count += 1
        for series, value in zip(self._series, values):
            series.append(value)
        if not self._paused:
            if self._mode == "xy":
                # Acquisition may deliver hundreds of frames per batch. Avoid
                # rescanning a full XY buffer for every individual sample;
                # interactions still notify immediately, data updates coalesce.
                if not self._xy_notify_timer.isActive():
                    self._xy_notify_timer.start(16)
                self.update()
            else:
                self._notify_view()
        return True

    def replace_samples(self, rows, names: list[str], *, sample_count: int | None = None) -> None:
        """Rebuild a display projection without replaying per-frame notifications."""
        series: list[deque[float]] = []
        count = 0
        for row in rows:
            if count >= self.MAX_SAMPLES:
                raise ValueError("显示重建最多100000帧")
            values = tuple(float(value) for value in row[:self.MAX_CHANNELS])
            if not values or not all(math.isfinite(value) for value in values):
                raise ValueError("显示重建包含空帧或非有限值")
            while len(series) < len(values):
                series.append(deque(maxlen=self._max_samples))
            del series[len(values):]
            for channel, value in zip(series, values):
                channel.append(value)
            count += 1
        if sample_count is not None and (type(sample_count) is not int or sample_count < count):
            raise ValueError("累计帧号不能小于保留帧数")
        total = count if sample_count is None else sample_count
        view = self._view_x
        axes = self._axis_ranges.copy()
        paused = self._paused
        frozen = (self._paused_series, self._paused_names, self._paused_sample_count)
        frozen_plot = (self._paused_plot_buffer, self._paused_plot_bounds)
        self.clear()
        self._series, self._sample_count = series, total
        self._view_x = view
        self._axis_ranges = axes
        self._paused = paused
        self._names = [str(name)[:64] for name in names[:len(series)]]
        if self._paused:
            self._paused_series, self._paused_names, self._paused_sample_count = frozen
            self._paused_plot_buffer, self._paused_plot_bounds = frozen_plot
        self._plot_buffer = PlotBuffer.from_columns(tuple(tuple(channel) for channel in series),
                                                    SampleCursor(0, total), 0)
        self._notify_view()

    def plot_rect(self) -> QRectF:
        if hasattr(self, 'plot_widget'):
            view = self.plot_widget
            rect = view.viewportTransform().mapRect(view.getViewBox().sceneBoundingRect())
            origin = view.viewport().mapTo(self, QPoint(0, 0))
            rect.translate(origin.x(), origin.y())
            if rect.width() > 10 and rect.height() > 10:
                return rect
        return QRectF(self.rect()).adjusted(52, 18, -16, -44)

    def grab(self, *args):
        # Explicit captures must include data even before the coalesced paint
        # timer fires, or while this card is hidden in a dashboard tab.
        self._sync_plot(force=True)
        return super().grab(*args)

    def _xy_points(self) -> tuple[tuple[int, float, float], ...]:
        x_channel, y_channel = self._xy_channels
        if not self._visible[x_channel] or not self._visible[y_channel]:
            return ()
        key = (self._paused, self._count(), self._xy_channels, self._max_samples)
        if key == self._xy_cache_key:
            return self._xy_cache
        source = self._live_or_paused_series()
        if max(x_channel, y_channel) >= len(source):
            return ()
        x_values, y_values = tuple(source[x_channel]), tuple(source[y_channel])
        common = min(len(x_values), len(y_values))
        if not common:
            return ()
        first = self._count() - common
        self._xy_cache = tuple((first + offset, x_value, y_value) for offset, (x_value, y_value) in enumerate(
            zip(x_values[-common:], y_values[-common:])
        ))
        self._xy_cache_key = key
        return self._xy_cache

    def _visible_segments(self):
        left, right = self.view_range
        count = self._count()
        for index, series in enumerate(self.displayed_series):
            if not self._visible[index] or not series:
                continue
            first = count - len(series)
            begin = max(0, math.floor(left - first) - 1)
            end = min(len(series), math.ceil(right - first) + 2)
            if end > begin:
                yield index, first + begin, series[begin:end]

    @property
    def y_range(self) -> tuple[float, float]:
        if self._axis_ranges["y_min"] is not None:
            return self._axis_ranges["y_min"], self._axis_ranges["y_max"]
        if self._mode == "xy":
            left, right = self.view_range
            values = [point[2] for point in self._xy_points() if left <= point[1] <= right and math.isfinite(point[2])]
            ranges = [(min(values), max(values))] if values else []
        else:
            bounds, _ = self._display_summary()
            if bounds:
                ranges = [pair for index, pair in enumerate(bounds) if pair is not None and self._visible[index]]
            else:
                ranges = []
                for _, _, values in self._visible_segments():
                    finite = [value for value in values if math.isfinite(value)]
                    if finite:
                        ranges.append((min(finite), max(finite)))
        if not ranges:
            return -1.0, 1.0
        low, high = min(pair[0] for pair in ranges), max(pair[1] for pair in ranges)
        return self._expanded_range(low, high)

    @staticmethod
    def _expanded_range(low: float, high: float) -> tuple[float, float]:
        if high == low:
            padding = max(1.0, abs(low) * 0.05)
        else:
            scale = max(abs(low), abs(high), 1.0)
            padding = (high / scale - low / scale) * 0.05 * scale
        limit = sys.float_info.max
        return max(-limit, low - padding), min(limit, high + padding)

    def _display_summary(self):
        if self._view_x is not None or self._axis_ranges['x_min'] is not None:
            return (), ()
        return self._paused_summary if self._paused else self._projection_summary

    @staticmethod
    def _y_fraction(value: float, low: float, high: float) -> float:
        scale = max(abs(low), abs(high), 1.0)
        span = high / scale - low / scale
        return (value / scale - low / scale) / span if span > 0 else 0.5

    def _emit_cursor(self) -> None:
        position = self.cursor_position
        if position is not None:
            index = round(self._cursor[0]) if self._mode == "time" else self._nearest_xy_sample()
            self._cursor_sample_index = index
            names = self._paused_names if self._paused else self._names
            values = []
            if index is not None:
                count = self._count()
                for channel, series in enumerate(self.displayed_series):
                    offset = index - (count - len(series))
                    if self._visible[channel] and 0 <= offset < len(series):
                        name = names[channel] if channel < len(names) and names[channel] else f"CH{channel + 1}"
                        values.append((channel, name, series[offset]))
            self._cursor_samples = tuple(values)
            self.cursor_moved.emit(*position)
            self.cursor_samples_changed.emit(self._cursor_samples)
            coordinates = f"t={position[0]:.6g} s" if self._mode == "time" else f"x={position[0]:.6g}"
            samples = " · ".join(f"{name[:12]}={value:.6g}" for _, name, value in values[:4])
            remaining = f" · +{len(values) - 4}通道" if len(values) > 4 else ""
            self.cursor_text_changed.emit(f"{coordinates} · y={position[1]:.6g}" + (f" · {samples}{remaining}" if samples else ""))

    def _nearest_xy_sample(self) -> int | None:
        points = self._xy_points()
        if not points or self._cursor is None:
            return None
        left, right = self.view_range
        low, high = self.y_range
        cursor_x = self._y_fraction(self._cursor[0], left, right)
        cursor_y = self._y_fraction(self._cursor[1], low, high)
        stride = max(1, len(points) // 5000)
        def distance(offset: int) -> float:
            _, x, y = points[offset]
            if not math.isfinite(x) or not math.isfinite(y):
                return math.inf
            return math.hypot(self._y_fraction(x, left, right) - cursor_x,
                              self._y_fraction(y, low, high) - cursor_y)
        coarse = min(range(0, len(points), stride), key=distance)
        nearest = min(range(max(0, coarse - stride), min(len(points), coarse + stride + 1)), key=distance)
        return points[nearest][0] if math.isfinite(distance(nearest)) else None

    def _read_cursor(self, position: QPointF) -> None:
        area = self.plot_rect()
        if area.width() <= 0 or area.height() <= 0 or not area.contains(position):
            return
        left, right = self.view_range
        low, high = self.y_range
        x_fraction = (position.x() - area.left()) / area.width()
        x = left * (1 - x_fraction) + right * x_fraction
        fraction = (position.y() - area.top()) / area.height()
        y = high * (1 - fraction) + low * fraction
        self._cursor = (x, y)
        self._emit_cursor()
        self.update()

    def wheelEvent(self, event: QWheelEvent) -> None:
        area = self.plot_rect()
        if area.width() <= 0 or not area.contains(event.position()) or not event.angleDelta().y():
            event.ignore()
            return
        steps = max(-8.0, min(8.0, event.angleDelta().y() / 120))
        self.zoom_view(1.25 ** steps, (event.position().x() - area.left()) / area.width())
        self._read_cursor(event.position())
        event.accept()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton and self.plot_rect().contains(event.position()):
            self.setFocus(Qt.MouseFocusReason)
            self._drag_origin = event.position()
            self._drag_initial_view = self.view_range
            self._drag_initial_auto = self.auto_fit_enabled
            self._drag_initial_axes = dict(self._axis_ranges)
            self._drag_original_view = self._view_x
            self._dragging = False
            self._read_cursor(event.position())
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_origin is not None and self._drag_initial_view is not None:
            difference = event.position() - self._drag_origin
            if self._dragging or abs(difference.x()) + abs(difference.y()) >= QApplication.startDragDistance():
                self._dragging = True
                self.setCursor(Qt.ClosedHandCursor)
                area = self.plot_rect()
                left, right = self._drag_initial_view
                shift = -difference.x() / max(1.0, area.width()) * (right - left)
                result = self._clamp_view(left + shift, right + shift)
                if result != self.view_range:
                    self._axis_ranges["x_min"] = self._axis_ranges["x_max"] = None
                    self._view_x = result
                    self._notify_view()
        self._read_cursor(event.position())
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton and self._drag_origin is not None:
            self._drag_origin = None
            self._drag_initial_view = None
            self._drag_initial_axes = None
            self._dragging = False
            self.setCursor(Qt.CrossCursor)
            self._read_cursor(event.position())
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def leaveEvent(self, event) -> None:
        if self._drag_origin is None:
            self._cursor = None
            self._cursor_samples = ()
            self._cursor_sample_index = None
            self.cursor_cleared.emit()
            self.cursor_samples_changed.emit(())
            self.cursor_text_changed.emit("—")
            self.update()
        super().leaveEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key_Space:
            self.set_paused(not self.paused)
        elif event.key() == Qt.Key_Home:
            self.reset_view()
        elif event.key() in (Qt.Key_Plus, Qt.Key_Equal):
            self.zoom_view(1.25)
        elif event.key() == Qt.Key_Minus:
            self.zoom_view(0.8)
        elif event.key() in (Qt.Key_Left, Qt.Key_Right):
            left, right = self.view_range
            shift = (right - left) * (-0.1 if event.key() == Qt.Key_Left else 0.1)
            result = self._clamp_view(left + shift, right + shift)
            if result != (left, right):
                self._axis_ranges["x_min"] = self._axis_ranges["x_max"] = None
                self._view_x = result
                self._notify_view()
        elif event.key() == Qt.Key_Escape:
            if self._drag_origin is not None:
                self._view_x = self._drag_original_view
                if self._drag_initial_axes is not None:
                    self._axis_ranges = self._drag_initial_axes
                self._drag_origin = None
                self._drag_initial_view = None
                self._drag_initial_axes = None
                self._dragging = False
                self.setCursor(Qt.CrossCursor)
                self._notify_view()
            self._cursor = None
            self._cursor_samples = ()
            self._cursor_sample_index = None
            self.cursor_cleared.emit()
            self.cursor_samples_changed.emit(())
            self.cursor_text_changed.emit("—")
            self.update()
        else:
            super().keyPressEvent(event)
            return
        event.accept()

    def _format_x_tick(self, value):
        actual = value * self._render_x_scale
        return f"{actual * self._sample_interval_ms / 1000:.3g}s" if self._mode == "time" else f"{actual:.3g}"

    def update(self, *args):
        if hasattr(self, '_render_timer') and not self._render_timer.isActive():
            self._render_timer.start()
        super().update(*args)

    def showEvent(self, event):
        super().showEvent(event)
        self._render_key = None
        self.update()

    def hideEvent(self, event):
        self._render_timer.stop()
        super().hideEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._render_key = None
        self.update()

    def _backend_y_range(self, buffer, indices, values, left, right):
        if self._axis_ranges['y_min'] is not None:
            return self._axis_ranges['y_min'], self._axis_ranges['y_max']
        limits = []
        if self._mode == 'xy':
            x, y = self._xy_channels
            if max(x, y) < len(values) and self._visible[x] and self._visible[y]:
                mask = np.isfinite(values[x]) & np.isfinite(values[y]) & (values[x] >= left) & (values[x] <= right)
                finite = values[y][mask]
                if finite.size:
                    limits.append((float(finite.min()), float(finite.max())))
        else:
            bounds = self._paused_plot_bounds if self._paused else self._plot_bounds
            full = (indices.size and left <= indices[0] and right >= indices[-1]
                    and buffer.values.shape[1] <= self._max_samples)
            if full and bounds:
                limits = [pair for index, pair in enumerate(bounds) if pair is not None and self._visible[index]]
            else:
                mask = (indices >= left) & (indices <= right)
                for index, column in enumerate(values):
                    if self._visible[index]:
                        finite = column[mask & np.isfinite(column)]
                        if finite.size:
                            limits.append((float(finite.min()), float(finite.max())))
        if not limits:
            return -1., 1.
        return self._expanded_range(min(pair[0] for pair in limits), max(pair[1] for pair in limits))

    def _sync_plot(self, *, force=False):
        if not force and not self.isVisible():
            return
        buffer = self.plot_buffer
        names = self._paused_names if self._paused else tuple(self._names)
        left, right = self.view_range
        key = (id(buffer), self._paused, self._max_samples, self._mode, self._xy_channels, tuple(self._visible),
               tuple(self._axis_ranges.values()), self._theme_palette, self.channel_colors,
               self._grid_visible, self._line_width, self._sample_interval_ms, names, left, right)
        if key != self._render_key or force:
            self._render_key = key
            self.plot_widget.setBackground(self._theme_palette.surface)
            self._legend.setLabelTextColor(self._theme_palette.plot_text)
            self.plot_widget.showGrid(x=self._grid_visible, y=self._grid_visible, alpha=.2)
            for axis in ('bottom', 'left'):
                self.plot_widget.getAxis(axis).setPen(self._theme_palette.plot_grid)
                self.plot_widget.getAxis(axis).setTextPen(self._theme_palette.plot_text)
            values = buffer.values[:, -self._max_samples:] if buffer is not None else np.empty((0, 0))
            indices = buffer.indices[-self._max_samples:] if buffer is not None else np.empty(0)
            low, high = self._backend_y_range(buffer, indices, values, left, right)
            self._render_x_scale = max(abs(left), abs(right), 1.) if max(abs(left), abs(right)) > 1e150 else 1.
            self._render_y_scale = max(abs(low), abs(high), 1.) if max(abs(low), abs(high)) > 1e150 else 1.
            visible = []
            for index, curve in enumerate(self._curves):
                active = index < len(values) and self._visible[index]
                if self._mode == 'xy':
                    x, y = self._xy_channels
                    active = index == y and max(x, y) < len(values) and self._visible[x] and self._visible[y]
                curve.setVisible(active)
                if not active:
                    continue
                visible.append(index)
                x_values = indices if self._mode == 'time' else values[self._xy_channels[0]]
                y_values = values[index]
                if self._mode == 'time':
                    x_values, y_values = buffer.render_series(index, left, right,
                        self.plot_widget.width(), self._max_samples)
                curve.setDownsampling(auto=False)
                curve.setClipToView(False)
                curve.setData(x_values / self._render_x_scale if self._render_x_scale != 1 else x_values,
                              y_values / self._render_y_scale if self._render_y_scale != 1 else y_values,
                              connect='finite', skipFiniteCheck=False, antialias=False,
                              pen=pg.mkPen(self.channel_colors[index], width=self._line_width))
            legend_names = tuple((i, names[i][:12] if i < len(names) else f'CH{i + 1}') for i in visible)
            if legend_names != self._legend_names:
                self._legend_names = legend_names
                self._legend.clear()
                for index, name in legend_names:
                    self._legend.addItem(self._curves[index], escape(name))
            self.plot_widget.setRange(xRange=(left / self._render_x_scale, right / self._render_x_scale),
                                      yRange=(low / self._render_y_scale, high / self._render_y_scale), padding=0)
            self._empty_label.setVisible(not visible or not indices.size)
            self._empty_label.setPos((left / 2 + right / 2) / self._render_x_scale,
                                     (low / 2 + high / 2) / self._render_y_scale)
            self._empty_label.setColor(self._theme_palette.muted)
            self._pause_label.setVisible(self._paused)
            self._pause_label.setColor(self._theme_palette.safety)
            self._pause_label.setPos(right / self._render_x_scale, high / self._render_y_scale)
        has_cursor = self._cursor is not None
        self._vertical_cursor.setVisible(has_cursor)
        self._horizontal_cursor.setVisible(has_cursor)
        if has_cursor:
            self._vertical_cursor.setValue(self._cursor[0] / self._render_x_scale)
            self._horizontal_cursor.setValue(self._cursor[1] / self._render_y_scale)
        for item in (self._vertical_cursor, self._horizontal_cursor):
            item.setPen(pg.mkPen(self._theme_palette.plot_cursor, style=Qt.DashLine))
