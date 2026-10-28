"""Self-owned interactive controls. Only explicit user commits emit commands.

These widgets are independent of transport and of third-party QML plug-ins.
Incoming telemetry, construction, configuration and theme changes never emit
``committed``. Drag previews are local; Escape, focus loss and disabling cancel.
"""

from __future__ import annotations

import math
import struct
from pathlib import Path

from PySide6.QtCore import QElapsedTimer, QEvent, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPalette, QPen
from PySide6.QtWidgets import (
    QDial, QDoubleSpinBox, QHBoxLayout, QLabel, QPushButton, QSlider,
    QSizePolicy, QVBoxLayout, QWidget,
)

from ..appearance import theme_palette


_ADJUST_KEYS = {
    Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down, Qt.Key_PageUp,
    Qt.Key_PageDown, Qt.Key_Home, Qt.Key_End,
}


class _CommitSlider(QSlider):
    keyboard_committed = Signal()

    def keyReleaseEvent(self, event) -> None:
        super().keyReleaseEvent(event)
        if event.key() in _ADJUST_KEYS and not event.isAutoRepeat():
            self.keyboard_committed.emit()

    def wheelEvent(self, event) -> None:
        # Scrolling a tall control workspace must not operate the device.
        event.ignore()


class _CommitDial(QDial):
    keyboard_committed = Signal()

    def keyReleaseEvent(self, event) -> None:
        super().keyReleaseEvent(event)
        if event.key() in _ADJUST_KEYS and not event.isAutoRepeat():
            self.keyboard_committed.emit()

    def wheelEvent(self, event) -> None:
        event.ignore()


class MaterialDial(QWidget):
    """Flat self-painted rotary input, with preview and release-only commit.

    Its small QSlider-compatible API is used by AnalogInput; it never knows a
    transport. Mouse drags and arrows preview, Escape/focus loss cancel, wheel
    events remain available to the containing scroll area.
    """

    valueChanged = Signal(int)
    sliderReleased = Signal()
    keyboard_committed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._minimum, self._maximum, self._value = 0, 100, 0
        self._single_step, self._page_step = 1, 10
        self._down = False
        self._key_pending = False
        self._before = 0
        self._palette = theme_palette("light")
        self.setMinimumSize(110, 110)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAccessibleName("扁平旋钮")
        self.setAccessibleDescription("拖动或方向键调节，松开提交；Esc和失焦取消，滚轮不发送")

    def setRange(self, minimum: int, maximum: int) -> None:
        self._minimum, self._maximum = minimum, maximum
        self.setValue(self._value)

    def setSingleStep(self, value: int) -> None:
        self._single_step = value

    def setPageStep(self, value: int) -> None:
        self._page_step = value

    def setNotchesVisible(self, _value: bool) -> None:
        pass

    def setWrapping(self, _value: bool) -> None:
        pass

    def value(self) -> int:
        return self._value

    def setValue(self, value: int) -> None:
        bounded = max(self._minimum, min(self._maximum, value))
        if bounded != self._value:
            self._value = bounded
            self.valueChanged.emit(bounded)
            self.update()

    def isSliderDown(self) -> bool:
        return self._down

    def set_theme(self, theme: str) -> None:
        self._palette = theme_palette(theme)
        self.update()

    def _point_value(self, point: QPointF) -> None:
        center = QPointF(self.width() / 2, self.height() / 2)
        angle = math.degrees(math.atan2(center.y() - point.y(), point.x() - center.x()))
        # Sweep clockwise from 225° to -45°, leaving the lower 90° inactive.
        sweep = (225 - angle) % 360
        if sweep > 270:
            sweep = 0 if sweep > 315 else 270
        self.setValue(round(self._minimum + sweep / 270 * (self._maximum - self._minimum)))

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self.isEnabled():
            self.setFocus(Qt.MouseFocusReason)
            self._before = self._value
            self._down = True
            self._point_value(event.position())
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._down:
            self._point_value(event.position())
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self._down:
            self._point_value(event.position())
            self._down = False
            self.sliderReleased.emit()
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self.cancel()
            event.accept()
            return
        if event.key() not in _ADJUST_KEYS:
            super().keyPressEvent(event)
            return
        if not self._key_pending:
            self._before = self._value
        self._key_pending = True
        if event.key() == Qt.Key_Home:
            value = self._minimum
        elif event.key() == Qt.Key_End:
            value = self._maximum
        else:
            step = self._page_step if event.key() in (Qt.Key_PageUp, Qt.Key_PageDown) else self._single_step
            direction = -1 if event.key() in (Qt.Key_Left, Qt.Key_Down, Qt.Key_PageDown) else 1
            value = self._value + direction * step
        self.setValue(value)
        event.accept()

    def keyReleaseEvent(self, event) -> None:
        if event.key() in _ADJUST_KEYS and self._key_pending:
            if not event.isAutoRepeat():
                self._key_pending = False
                self.keyboard_committed.emit()
            event.accept()
        else:
            super().keyReleaseEvent(event)

    def wheelEvent(self, event) -> None:
        event.ignore()

    def cancel(self) -> None:
        if not self._down and not self._key_pending:
            return
        self._down = self._key_pending = False
        self.setValue(self._before)

    def event(self, event) -> bool:
        if hasattr(self, "_before") and event.type() in (
            QEvent.FocusOut, QEvent.Hide, QEvent.HideToParent, QEvent.Close, QEvent.WindowDeactivate,
        ):
            self.cancel()
        if hasattr(self, "_before") and event.type() == QEvent.EnabledChange and not self.isEnabled():
            self.cancel()
        return super().event(event)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        diameter = max(1, min(self.width(), self.height()) - 20)
        ring = QRectF((self.width() - diameter) / 2, (self.height() - diameter) / 2, diameter, diameter)
        palette = self._palette
        painter.setBrush(QColor(palette.input_background))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(ring.adjusted(8, 8, -8, -8))
        fraction = (self._value - self._minimum) / max(1, self._maximum - self._minimum)
        painter.setPen(QPen(QColor(palette.border), 7, Qt.SolidLine, Qt.RoundCap))
        painter.drawArc(ring, 225 * 16, -270 * 16)
        accent = palette.accent if self.isEnabled() else palette.disabled_text
        painter.setPen(QPen(QColor(accent), 7, Qt.SolidLine, Qt.RoundCap))
        painter.drawArc(ring, 225 * 16, round(-fraction * 270 * 16))
        angle = math.radians(225 - fraction * 270)
        center, radius = ring.center(), diameter / 2
        painter.drawLine(QPointF(center.x() + math.cos(angle) * radius * .35, center.y() - math.sin(angle) * radius * .35),
                         QPointF(center.x() + math.cos(angle) * radius * .72, center.y() - math.sin(angle) * radius * .72))


class AnalogInput(QWidget):
    """Slider/dial plus finite numeric editor, sending on release/commit only."""

    committed = Signal(float)

    def __init__(self, minimum: float, maximum: float, step: float,
                 precision: int, *, dial: bool = False, material: bool = False) -> None:
        super().__init__()
        self._minimum, self._maximum, self._step = minimum, maximum, step
        self._updating = False
        self._dirty = False
        self._committed_value = minimum
        self._ticks = min(100000, max(1, math.ceil((maximum - minimum) / step)))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.input = MaterialDial() if material else _CommitDial() if dial else _CommitSlider(Qt.Horizontal)
        self.input.setRange(0, self._ticks)
        self.input.setSingleStep(1)
        self.input.setPageStep(max(1, self._ticks // 10))
        self.input.setFocusPolicy(Qt.StrongFocus)
        if dial or material:
            self.input.setNotchesVisible(True)
            self.input.setMinimumSize(110, 110)
            self.input.setWrapping(False)
        layout.addWidget(self.input)
        self.editor = QDoubleSpinBox()
        self.editor.setDecimals(precision)
        self.editor.setRange(minimum, maximum)
        self.editor.setSingleStep(step)
        self.editor.setKeyboardTracking(False)
        layout.addWidget(self.editor)
        self.input.valueChanged.connect(self._preview_tick)
        self.input.sliderReleased.connect(self._commit_slider)
        self.input.keyboard_committed.connect(self._commit_slider)
        self.editor.valueChanged.connect(self._preview_editor)
        self.editor.editingFinished.connect(self._commit_editor)
        self.editor.installEventFilter(self)
        self.editor.lineEdit().installEventFilter(self)
        self.input.installEventFilter(self)
        self.set_value(minimum)

    def _value_at(self, tick: int) -> float:
        # Endpoints remain attainable when span is not divisible by step.
        return min(self._maximum, self._minimum + tick * self._step)

    def _tick_at(self, value: float) -> int:
        return min(self._ticks, max(0, round((value - self._minimum) / self._step)))

    def _preview_tick(self, tick: int) -> None:
        if self._updating:
            return
        self._updating = True
        self.editor.setValue(self._value_at(tick))
        self._updating = False
        self._dirty = True

    def _preview_editor(self, value: float) -> None:
        if self._updating:
            return
        self._updating = True
        self.input.setValue(self._tick_at(value))
        self._updating = False
        self._dirty = True

    def _commit_slider(self) -> None:
        if not self.isEnabled() or not self._dirty:
            return
        self._committed_value = self.editor.value()
        self._dirty = False
        self.committed.emit(self._committed_value)

    def _commit_editor(self) -> None:
        self._commit_slider()

    def set_value(self, value: float | int | None) -> None:
        if value is None or self.input.isSliderDown() or self._dirty:
            return
        value = min(self._maximum, max(self._minimum, float(value)))
        self._updating = True
        self.editor.setValue(value)
        self.input.setValue(self._tick_at(value))
        self._updating = False
        self._committed_value = value

    def cancel(self) -> None:
        self._dirty = False
        self._updating = True
        if isinstance(self.input, MaterialDial):
            self.input.cancel()
        self.editor.setValue(self._committed_value)
        self.input.setValue(self._tick_at(self._committed_value))
        self._updating = False

    def set_theme(self, theme: str) -> None:
        colours = theme_palette(theme)
        palette = self.palette()
        for role, colour in ((QPalette.Window, colours.surface), (QPalette.Base, colours.input_background),
                             (QPalette.Button, colours.button), (QPalette.Text, colours.text),
                             (QPalette.WindowText, colours.text), (QPalette.ButtonText, colours.text),
                             (QPalette.Highlight, colours.accent), (QPalette.Dark, colours.border)):
            palette.setColor(role, QColor(colour))
        self.setPalette(palette)
        self.input.setPalette(palette)
        self.editor.setPalette(palette)
        if isinstance(self.input, MaterialDial):
            self.input.set_theme(theme)
        self.input.setStyleSheet(
            f"QSlider::groove:horizontal {{height:5px;background:{colours.border};}}"
            f"QSlider::handle:horizontal {{width:13px;margin:-5px 0;background:{colours.accent};border-radius:6px;}}"
            f"QSlider::handle:horizontal:disabled {{background:{colours.disabled_text};}}"
        )
        self.update()

    def eventFilter(self, watched, event) -> bool:
        if event.type() == QEvent.KeyPress and event.key() == Qt.Key_Escape:
            self.cancel()
            event.accept()
            return True
        if event.type() in (QEvent.FocusOut, QEvent.Hide, QEvent.HideToParent, QEvent.WindowDeactivate):
            self.cancel()
        if event.type() == QEvent.EnabledChange and not self.isEnabled():
            self.cancel()
        return super().eventFilter(watched, event)


class PressReleaseButton(QPushButton):
    """Explicit physical button edges; cancellation never synthesizes release."""

    press_committed = Signal()
    release_committed = Signal()
    _ACTIVATION_KEYS = (Qt.Key_Space, Qt.Key_Return, Qt.Key_Enter)

    def __init__(self, text: str) -> None:
        super().__init__(text)
        self._armed_kind: str | None = None
        self._armed_key = None
        self.setAutoRepeat(False)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self.isEnabled():
            if self._armed_kind is not None:
                event.accept()
                return
            super().mousePressEvent(event)
            if self.isDown():
                self._armed_kind = "mouse"
                self.press_committed.emit()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self._armed_kind == "mouse":
            self._armed_kind = None
            enabled = self.isEnabled()
            super().mouseReleaseEvent(event)
            if enabled:
                self.release_committed.emit()
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self.cancel()
            event.accept()
        elif event.key() in self._ACTIVATION_KEYS and self.isEnabled():
            if not event.isAutoRepeat() and self._armed_kind is None:
                self._armed_kind, self._armed_key = "key", event.key()
                self.setDown(True)
                self.press_committed.emit()
            event.accept()
        else:
            super().keyPressEvent(event)

    def keyReleaseEvent(self, event) -> None:
        if event.key() in self._ACTIVATION_KEYS:
            if self._armed_kind == "key" and self._armed_key == event.key() and not event.isAutoRepeat():
                self._armed_kind = self._armed_key = None
                self.setDown(False)
                if self.isEnabled():
                    self.release_committed.emit()
            event.accept()
        else:
            super().keyReleaseEvent(event)

    def cancel(self) -> None:
        self._armed_kind = self._armed_key = None
        self.setDown(False)

    def event(self, event) -> bool:
        if hasattr(self, "_armed_kind") and event.type() in (
            QEvent.FocusOut, QEvent.Hide, QEvent.HideToParent, QEvent.Close,
            QEvent.WindowDeactivate,
        ):
            self.cancel()
        if hasattr(self, "_armed_kind") and event.type() == QEvent.EnabledChange and not self.isEnabled():
            self.cancel()
        return super().event(event)


class DelayedButton(QPushButton):
    """Hold to trigger once. No delayed command survives release or focus loss."""

    committed = Signal()

    def __init__(self, text: str, delay_ms: int) -> None:
        super().__init__(text)
        self._label = text
        self._delay_ms = delay_ms
        self._armed = False
        self._hold_progress = 0
        self._elapsed = QElapsedTimer()
        self._palette = theme_palette("light")
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setTimerType(Qt.PreciseTimer)
        self._timer.timeout.connect(self._trigger)
        self._progress_timer = QTimer(self)
        self._progress_timer.setInterval(40)
        self._progress_timer.timeout.connect(self._update_progress)
        self.pressed.connect(self._begin)
        self.released.connect(self.cancel)
        self.setAutoRepeat(False)
        self.setToolTip(f"按住 {delay_ms} ms 触发；提前松开取消")

    def _begin(self) -> None:
        if not self.isEnabled():
            return
        self._armed = True
        self._hold_progress = 0
        self._elapsed.start()
        self.setText("按住… 0%")
        self._timer.start(self._delay_ms)
        self._progress_timer.start()
        self.update()

    @property
    def hold_progress(self) -> int:
        return self._hold_progress

    def _update_progress(self) -> None:
        if not self._armed or not self.isEnabled() or not self.isVisible() or not self.isDown():
            self.cancel()
            return
        self._hold_progress = min(99, self._elapsed.elapsed() * 100 // self._delay_ms)
        self.setText(f"按住… {self._hold_progress}%")
        self.update()

    def _trigger(self) -> None:
        if self._armed and self.isEnabled() and self.isVisible() and self.isDown():
            self._armed = False
            self._progress_timer.stop()
            self._hold_progress = 100
            self.setText(self._label + " · 100%")
            self.update()
            self.committed.emit()
        else:
            self.cancel()

    def cancel(self) -> None:
        self._timer.stop()
        if hasattr(self, "_progress_timer"):
            self._progress_timer.stop()
        self._armed = False
        self._hold_progress = 0
        self.setText(self._label)
        self.update()

    def set_theme(self, theme: str) -> None:
        self._palette = theme_palette(theme)
        self.update()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if self._hold_progress:
            painter = QPainter(self)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(self._palette.accent))
            width = max(0, self.width() - 4) * self._hold_progress / 100
            painter.drawRect(QRectF(2, max(0, self.height() - 4), width, 2))

    def event(self, event) -> bool:
        if hasattr(self, "_timer") and event.type() in (
            QEvent.FocusOut, QEvent.Hide, QEvent.HideToParent, QEvent.Close,
            QEvent.WindowDeactivate,
        ):
            self.cancel()
        if hasattr(self, "_timer") and event.type() == QEvent.EnabledChange and not self.isEnabled():
            self.cancel()
        return super().event(event)


class Joystick(QWidget):
    """Two-axis local drag preview with explicit mouse/key release commits."""

    committed = Signal(float, float)

    def __init__(self, minimum: float, maximum: float, step: float,
                 return_to_center: bool = False) -> None:
        super().__init__()
        self._minimum, self._maximum = minimum, maximum
        self._key_step = min(2.0, step * 2 / (maximum - minimum))
        self._return_to_center = return_to_center
        self._x = self._y = 0.0
        self._before = (0.0, 0.0)
        self._dragging = False
        self._key_pending = False
        self._palette = theme_palette("light")
        self.setMinimumSize(150, 150)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAccessibleName("双轴摇杆")
        self.setAccessibleDescription("拖动后松开或使用方向键后松开发送，Esc取消；滚轮不发送")

    def _geometry(self) -> tuple[QPointF, float]:
        return QPointF(self.width() / 2, self.height() / 2), max(1.0, min(self.width(), self.height()) / 2 - 14)

    def _position(self, position: QPointF) -> None:
        center, radius = self._geometry()
        self._x = max(-1.0, min(1.0, (position.x() - center.x()) / radius))
        self._y = max(-1.0, min(1.0, (center.y() - position.y()) / radius))
        self.update()

    def _mapped(self, value: float) -> float:
        return self._minimum + (value + 1) * 0.5 * (self._maximum - self._minimum)

    def _commit(self) -> None:
        if not self.isEnabled():
            return
        self.committed.emit(self._mapped(self._x), self._mapped(self._y))
        if self._return_to_center:
            # Return-to-centre is a local spring preview, not a second hidden
            # transmission. A new explicit Home/release sends the centre.
            self._x = self._y = 0.0
        self._before = (self._x, self._y)
        self.update()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self.isEnabled():
            self.setFocus(Qt.MouseFocusReason)
            self._before = (self._x, self._y)
            self._dragging = True
            self._position(event.position())
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._dragging:
            self._position(event.position())
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self._dragging:
            self._position(event.position())
            self._dragging = False
            self._commit()
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self.cancel()
            event.accept()
            return
        if event.key() not in {Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down, Qt.Key_Home}:
            super().keyPressEvent(event)
            return
        if not self._key_pending:
            self._before = (self._x, self._y)
        self._key_pending = True
        if event.key() == Qt.Key_Home:
            self._x = self._y = 0.0
        elif event.key() == Qt.Key_Left:
            self._x = max(-1.0, self._x - self._key_step)
        elif event.key() == Qt.Key_Right:
            self._x = min(1.0, self._x + self._key_step)
        elif event.key() == Qt.Key_Up:
            self._y = min(1.0, self._y + self._key_step)
        else:
            self._y = max(-1.0, self._y - self._key_step)
        self.update()
        event.accept()

    def keyReleaseEvent(self, event) -> None:
        if self._key_pending and event.key() in {Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down, Qt.Key_Home}:
            if not event.isAutoRepeat():
                self._key_pending = False
                self._commit()
            event.accept()
        else:
            super().keyReleaseEvent(event)

    def cancel(self) -> None:
        self._dragging = self._key_pending = False
        self._x, self._y = self._before
        self.update()

    def set_values(self, x, y) -> None:
        if self._dragging or self._key_pending or x is None or y is None:
            return
        span = self._maximum - self._minimum
        self._x = max(-1.0, min(1.0, (x - self._minimum) / span * 2 - 1))
        self._y = max(-1.0, min(1.0, (y - self._minimum) / span * 2 - 1))
        self._before = (self._x, self._y)
        self.update()

    def set_theme(self, theme: str) -> None:
        self._palette = theme_palette(theme)
        self.update()

    def event(self, event) -> bool:
        if hasattr(self, "_before") and event.type() in (
            QEvent.FocusOut, QEvent.Hide, QEvent.HideToParent, QEvent.Close, QEvent.WindowDeactivate,
        ):
            self.cancel()
        if hasattr(self, "_before") and event.type() == QEvent.EnabledChange and not self.isEnabled():
            self.cancel()
        return super().event(event)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        center, radius = self._geometry()
        palette = self._palette
        painter.setBrush(QColor(palette.input_background))
        painter.setPen(QPen(QColor(palette.border), 1))
        painter.drawEllipse(center, radius, radius)
        painter.drawLine(QPointF(center.x() - radius, center.y()), QPointF(center.x() + radius, center.y()))
        painter.drawLine(QPointF(center.x(), center.y() - radius), QPointF(center.x(), center.y() + radius))
        tip = QPointF(center.x() + self._x * radius * .85, center.y() - self._y * radius * .85)
        painter.setPen(QPen(QColor(palette.accent), 3))
        painter.drawLine(center, tip)
        painter.setBrush(QColor(palette.accent))
        painter.drawEllipse(tip, 8, 8)
        painter.setPen(QColor(palette.text))
        painter.drawText(self.rect(), Qt.AlignBottom | Qt.AlignHCenter,
                         f"X {self._mapped(self._x):.3g}  Y {self._mapped(self._y):.3g}")


class IndicatorLight(QWidget):
    def __init__(self, threshold: float, inverted: bool = False, *, settings=None) -> None:
        super().__init__()
        self._threshold = threshold
        self._inverted = inverted
        self._settings = dict(settings or {})
        self._value = None
        self._palette = theme_palette("light")
        self.setMinimumSize(80, 80)
        self.setAccessibleName("通道状态指示灯")

    def set_value(self, value) -> None:
        self._value = value
        self.setAccessibleDescription(self._state_label())
        self.update()

    def set_theme(self, theme: str) -> None:
        self._palette = theme_palette(theme)
        self.update()

    def _is_on(self) -> bool:
        return self._value is not None and (self._value <= self._threshold if self._inverted else self._value >= self._threshold)

    def _band(self) -> int:
        value = self._value
        low, high = self._settings.get("lower_threshold", 0), self._settings.get("upper_threshold", 1)
        band = 0 if value < low else 2 if value >= high else 1
        return 2 - band if self._inverted else band

    def _state_label(self) -> str:
        if self._value is None:
            return "无数据"
        if self._settings.get("three_color", False):
            return ("低区间", "中区间", "高区间")[self._band()]
        return "亮" if self._is_on() else "灭"

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        radius = max(1, min(self.width(), self.height()) / 2 - 12)
        if self._value is None:
            color = self._palette.disabled_background
        elif self._settings.get("three_color", False):
            colors = tuple(self._settings.get(key, default) for key, default in (
                ("low_color", "#245ec7"), ("middle_color", "#16a375"), ("high_color", "#df6b25")))
            color = colors[self._band()]
        else:
            color = self._settings.get("on_color", "#16a375") if self._is_on() else self._palette.rail
        painter.setBrush(QColor(color))
        painter.setPen(QPen(QColor(self._palette.border), 2))
        painter.drawEllipse(QPointF(self.width() / 2, self.height() / 2), radius, radius)


_CUBE_VERTICES = ((-1, -1, -1), (1, -1, -1), (1, 1, -1), (-1, 1, -1),
                  (-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1))
_CUBE_EDGES = ((0, 1), (1, 2), (2, 3), (3, 0), (4, 5), (5, 6), (6, 7), (7, 4),
               (0, 4), (1, 5), (2, 6), (3, 7))
_MAX_MODEL_BYTES = 4 * 1024 * 1024
_MAX_MODEL_FACES = 4000


def _model_geometry(triangles):
    """Normalize triangles to a unit box and build unique wireframe edges."""
    if not triangles or len(triangles) > _MAX_MODEL_FACES:
        return None
    vertices = [point for triangle in triangles for point in triangle]
    if any(not math.isfinite(value) or abs(value) > 1e6 for point in vertices for value in point):
        return None
    minimum = tuple(min(point[axis] for point in vertices) for axis in range(3))
    maximum = tuple(max(point[axis] for point in vertices) for axis in range(3))
    extent = max(maximum[axis] - minimum[axis] for axis in range(3))
    if extent <= 0:
        return None
    scale = 2.0 / extent
    center = tuple((minimum[axis] + maximum[axis]) / 2 for axis in range(3))
    points, lookup, edges = [], {}, set()
    for triangle in triangles:
        indices = []
        for point in triangle:
            normalized = tuple(round((point[axis] - center[axis]) * scale, 6) for axis in range(3))
            index = lookup.get(normalized)
            if index is None:
                index = len(points)
                lookup[normalized] = index
                points.append(normalized)
            indices.append(index)
        for first, second in ((0, 1), (1, 2), (2, 0)):
            a, b = indices[first], indices[second]
            if a != b:
                edges.add((a, b) if a < b else (b, a))
    if not edges:
        return None
    return tuple(points), tuple(sorted(edges))


def _stl_triangles(data: bytes):
    if len(data) >= 84:
        count = struct.unpack_from("<I", data, 80)[0]
        if 84 + count * 50 == len(data):
            if not 0 < count <= _MAX_MODEL_FACES:
                return None
            triangles = []
            for index in range(count):
                values = struct.unpack_from("<12f", data, 84 + index * 50)
                triangles.append((values[3:6], values[6:9], values[9:12]))
            return triangles
    text = data.decode("utf-8", errors="replace")
    points = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[0].lower() == "vertex":
            try:
                points.append(tuple(float(value) for value in parts[1:4]))
            except ValueError:
                return None
        if len(points) // 3 > _MAX_MODEL_FACES:
            return None
    if len(points) < 3 or len(points) % 3:
        return None
    return [(points[index], points[index + 1], points[index + 2])
            for index in range(0, len(points), 3)]


def _obj_triangles(text: str):
    points, triangles = [], []
    for line in text.splitlines():
        parts = line.split()
        if not parts:
            continue
        if parts[0] == "v" and len(parts) >= 4:
            try:
                points.append(tuple(float(value) for value in parts[1:4]))
            except ValueError:
                return None
            if len(points) > 3 * _MAX_MODEL_FACES:
                return None
        elif parts[0] == "f" and len(parts) >= 4:
            face = []
            for token in parts[1:]:
                try:
                    index = int(token.split("/", 1)[0])
                except ValueError:
                    return None
                if index < 0:
                    index += len(points)
                elif index > 0:
                    index -= 1
                if not 0 <= index < len(points):
                    return None
                face.append(index)
            for index in range(1, len(face) - 1):
                triangles.append((points[face[0]], points[face[index]], points[face[index + 1]]))
                if len(triangles) > _MAX_MODEL_FACES:
                    return None
    return triangles or None


def load_wireframe_model(path) -> tuple[tuple, tuple] | None:
    """Load a bounded local STL/OBJ for wireframe display; failures fall back."""
    if not isinstance(path, str) or not path.strip():
        return None
    try:
        file = Path(path)
        if not file.is_file() or file.stat().st_size > _MAX_MODEL_BYTES:
            return None
        data = file.read_bytes()
    except OSError:
        return None
    try:
        if file.suffix.casefold() == ".stl":
            triangles = _stl_triangles(data)
        elif file.suffix.casefold() == ".obj":
            triangles = _obj_triangles(data.decode("utf-8", errors="replace"))
        else:
            return None
    except (ValueError, struct.error, IndexError):
        return None
    return _model_geometry(triangles)


class CubeView(QWidget):
    """Read-only Euler/quaternion wireframe; local models only, no OpenGL."""

    def __init__(self, *, radians: bool = False, offsets=(0, 0, 0), scale: float = 1,
                 show_axes: bool = True, model_path: str = "", load_external_model: bool = True) -> None:
        super().__init__()
        self._angles = None
        self._radians = radians
        self._offsets = tuple(value if radians else math.radians(value) for value in offsets)
        self._scale = scale
        self._show_axes = show_axes
        self._palette = theme_palette("light")
        self._vertices, self._edges = _CUBE_VERTICES, _CUBE_EDGES
        self._model_loaded = False
        if model_path and load_external_model:
            geometry = load_wireframe_model(model_path)
            if geometry is not None:
                self._vertices, self._edges = geometry
                self._model_loaded = True
        self.setMinimumSize(160, 160)
        self.setAccessibleName("三轴姿态模型" if self._model_loaded else "三轴姿态立方体")

    def load_model(self, path: str) -> bool:
        """Read a local model only following the host's explicit file action."""
        geometry = load_wireframe_model(path)
        if geometry is None:
            return False
        self._vertices, self._edges = geometry
        self._model_loaded = True
        self.setAccessibleName("三轴姿态模型")
        self.update()
        return True

    def set_values(self, x, y, z) -> None:
        if any(value is None for value in (x, y, z)):
            self._angles = None
        else:
            self._angles = tuple(
                math.remainder(value if self._radians else math.radians(math.remainder(value, 360)), math.tau) + offset
                for value, offset in zip((x, y, z), self._offsets)
            )
        self.update()

    def set_quaternion(self, w, x, y, z) -> None:
        if any(value is None for value in (w, x, y, z)):
            self._angles = None
            self.update()
            return
        largest = max(abs(value) for value in (w, x, y, z))
        if largest == 0:
            self._angles = None
            self.update()
            return
        # Scale first to avoid overflow for finite but very large telemetry.
        w, x, y, z = (value / largest for value in (w, x, y, z))
        norm = math.sqrt(w * w + x * x + y * y + z * z)
        w, x, y, z = (value / norm for value in (w, x, y, z))
        angles = (
            math.atan2(2 * (w * x + y * z), 1 - 2 * (x * x + y * y)),
            math.asin(max(-1, min(1, 2 * (w * y - z * x)))),
            math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z)),
        )
        self._angles = tuple(value + offset for value, offset in zip(angles, self._offsets))
        self.update()

    def set_theme(self, theme: str) -> None:
        self._palette = theme_palette(theme)
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        if self._angles is None:
            painter.setPen(QColor(self._palette.muted))
            painter.drawText(self.rect(), Qt.AlignCenter, "等待三轴数据")
            return
        ax, ay, az = self._angles
        sx, cx, sy, cy, sz, cz = math.sin(ax), math.cos(ax), math.sin(ay), math.cos(ay), math.sin(az), math.cos(az)

        def project(point):
            x, y, z = point
            y, z = y * cx - z * sx, y * sx + z * cx
            x, z = x * cy + z * sy, -x * sy + z * cy
            x, y = x * cz - y * sz, x * sz + y * cz
            scale = min(self.width(), self.height()) * .24 * self._scale / (1 + z * .13)
            return QPointF(self.width() / 2 + x * scale, self.height() / 2 - y * scale)

        points = [project(point) for point in self._vertices]
        painter.setPen(QPen(QColor(self._palette.accent), 2))
        for a, b in self._edges:
            painter.drawLine(points[a], points[b])
        if self._show_axes:
            origin = project((0, 0, 0))
            painter.setPen(QPen(QColor("#e25e6a"), 3))
            painter.drawLine(origin, project((.9, 0, 0)))
            painter.setPen(QPen(QColor("#19ad82"), 3))
            painter.drawLine(origin, project((0, .9, 0)))
            painter.setPen(QPen(QColor("#4788e9"), 3))
            painter.drawLine(origin, project((0, 0, .9)))
