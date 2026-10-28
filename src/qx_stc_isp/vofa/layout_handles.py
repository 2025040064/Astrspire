"""Explicit move/resize handles for our control canvas; never transmit data."""

from __future__ import annotations

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QApplication, QWidget

from ..appearance import theme_palette


def validate_control_rect(value) -> tuple[int, int, int, int]:
    if not isinstance(value, (tuple, list)) or len(value) != 4 or any(type(number) is not int for number in value):
        raise ValueError("控件位置必须为4个整数 [x,y,width,height]")
    x, y, width, height = value
    if not 0 <= x <= 8000 or not 0 <= y <= 8000 or not 160 <= width <= 2000 or not 80 <= height <= 2000:
        raise ValueError("控件位置须在0–8000，宽160–2000，高80–2000")
    return x, y, width, height


class LayoutHandle(QWidget):
    def __init__(self, card, owner, *, resize=False) -> None:
        super().__init__(card)
        self._card, self._owner, self._resize = card, owner, resize
        self._origin = None
        self._initial = None
        self._dragging = False
        self.setFixedSize(22, 22)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.SizeFDiagCursor if resize else Qt.SizeAllCursor)
        self.setToolTip("拖动拉伸；方向键每次8像素，Esc取消" if resize else "拖动移动；方向键每次8像素，Esc取消")
        self.setAccessibleName("调整控件大小" if resize else "移动控件")

    def paintEvent(self, _event) -> None:
        palette = theme_palette(getattr(self._owner, '_theme', 'warm'))
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(palette.button))
        painter.setPen(QColor(palette.accent))
        painter.drawText(self.rect(), Qt.AlignCenter, "◢" if self._resize else "✥")
        if self.hasFocus():
            painter.drawRect(self.rect().adjusted(0, 0, -1, -1))

    def _adjust(self, base, dx, dy) -> None:
        minimum = self._card.minimumSizeHint()
        minimum_width = min(2000, max(160, minimum.width()))
        minimum_height = min(2000, max(80, minimum.height()))
        if self._resize:
            rect = QRect(base.x(), base.y(), min(2000, max(minimum_width, base.width() + dx)),
                         min(2000, max(minimum_height, base.height() + dy)))
        else:
            rect = QRect(min(8000, max(0, base.x() + dx)), min(8000, max(0, base.y() + dy)), base.width(), base.height())
        self._card.setGeometry(rect)
        self._card.raise_()
        self._owner._update_canvas_extent()

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.LeftButton:
            super().mousePressEvent(event)
            return
        self.setFocus(Qt.MouseFocusReason)
        self._origin = event.globalPosition().toPoint()
        self._initial = self._card.geometry()
        self._dragging = False
        event.accept()

    def mouseMoveEvent(self, event) -> None:
        if self._origin is None:
            super().mouseMoveEvent(event)
            return
        delta = event.globalPosition().toPoint() - self._origin
        if not self._dragging and delta.manhattanLength() < QApplication.startDragDistance():
            return
        self._dragging = True
        self._adjust(self._initial, round(delta.x() / 8) * 8, round(delta.y() / 8) * 8)
        event.accept()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self._origin is not None:
            self._origin = self._initial = None
            self._dragging = False
            self._owner._commit_geometry(self._card)
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def cancel(self) -> None:
        if self._initial is not None:
            self._card.setGeometry(self._initial)
            self._owner._update_canvas_extent()
        self._origin = self._initial = None
        self._dragging = False

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self.cancel()
            event.accept()
            return
        moves = {Qt.Key_Left: (-8, 0), Qt.Key_Right: (8, 0), Qt.Key_Up: (0, -8), Qt.Key_Down: (0, 8)}
        if event.key() in moves and self._origin is None:
            self._adjust(self._card.geometry(), *moves[event.key()])
            self._owner._commit_geometry(self._card)
            event.accept()
            return
        super().keyPressEvent(event)

    def focusOutEvent(self, event) -> None:
        self.cancel()
        super().focusOutEvent(event)

    def hideEvent(self, event) -> None:
        self.cancel()
        super().hideEvent(event)
