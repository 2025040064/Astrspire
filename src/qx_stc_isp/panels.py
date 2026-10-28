"""Accessible, non-collapsing split panes with an explicit default layout."""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeyEvent, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import QGridLayout, QSplitter, QSplitterHandle, QWidget


class PlotToolbar(QWidget):
    """Keep existing action groups on one or two rows without reconnecting them."""

    def __init__(self, actions: QWidget, settings: QWidget, parent=None):
        super().__init__(parent)
        self._actions, self._settings = actions, settings
        self._stacked = None
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(4)
        self._arrange(True)

    def _arrange(self, stacked):
        if stacked == self._stacked:
            return
        self._stacked = stacked
        self._grid.removeWidget(self._actions)
        self._grid.removeWidget(self._settings)
        self._grid.addWidget(self._actions, 0, 0)
        self._grid.addWidget(self._settings, 1 if stacked else 0, 0 if stacked else 1)
        self._grid.setColumnStretch(0, 1)
        self._grid.setColumnStretch(1, 0 if stacked else 1)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Logical pixels: fonts and native controls still obey Qt DPI scaling.
        required = max(1050, self._actions.minimumSizeHint().width() + self._settings.minimumSizeHint().width() + 4)
        self._arrange(self.width() < required)


class _AdjustableHandle(QSplitterHandle):
    def __init__(self, orientation: Qt.Orientation, parent: QSplitter) -> None:
        super().__init__(orientation, parent)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAccessibleName("左右分区分隔条" if orientation == Qt.Horizontal else "上下分区分隔条")
        self.setAccessibleDescription("拖动调整大小；方向键每次调整10像素；双击或Home键恢复默认布局")

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key_Home:
            self.splitter().restore_default_sizes()
            event.accept()
            return
        negative, positive = (
            (Qt.Key_Left, Qt.Key_Right) if self.orientation() == Qt.Horizontal
            else (Qt.Key_Up, Qt.Key_Down)
        )
        if event.key() in (negative, positive):
            position = self.pos().x() if self.orientation() == Qt.Horizontal else self.pos().y()
            splitter = self.splitter()
            target = position + (-10 if event.key() == negative else 10)
            if self.orientation() == Qt.Horizontal and splitter.isRightToLeft():
                target = splitter.width() - target - self.width()
            # The splitter accepts a logical right-origin position in RTL;
            # Qt still owns its legal range and all child min/max constraints.
            index = next((i for i in range(1, splitter.count()) if splitter.handle(i) is self), None)
            if index is not None:
                splitter.moveSplitter(target, index)
            event.accept()
            return
        super().keyPressEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            self.splitter().restore_default_sizes()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def focusInEvent(self, event) -> None:
        super().focusInEvent(event)
        self.update()

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        self.update()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if self.hasFocus():
            painter = QPainter(self)
            painter.setPen(QPen(self.palette().highlight().color(), 1))
            painter.drawRect(self.rect().adjusted(0, 0, -1, -1))


class AdjustableSplitter(QSplitter):
    """Native split panes; layout defaults are applied only when requested.

    set_default_sizes() applies and stores a relative size list once. A left
    double-click or Home on a focused handle restores that list, constrained
    by the current available space and the child widgets' native size limits.
    Ordinary resizes keep the user's current layout; there is no resize hook
    that repeatedly restores defaults. If no default was supplied, first show
    captures the initial visible layout.
    """

    def __init__(self, orientation: Qt.Orientation = Qt.Horizontal,
                 parent: QWidget | None = None) -> None:
        super().__init__(orientation, parent)
        self.setChildrenCollapsible(False)
        self.setHandleWidth(6)
        self.setAccessibleName("可调节分区")
        self._default_sizes: tuple[int, ...] | None = None

    def createHandle(self) -> QSplitterHandle:
        return _AdjustableHandle(self.orientation(), self)

    @property
    def default_sizes(self) -> tuple[int, ...] | None:
        return self._default_sizes

    def set_default_sizes(self, sizes: Sequence[int]) -> None:
        if (not isinstance(sizes, (list, tuple)) or not sizes
                or len(sizes) != self.count()
                or any(type(size) is not int or size <= 0 for size in sizes)):
            raise ValueError("默认分区大小必须与分区数量一致，且每项为正整数")
        values = tuple(sizes)
        self.setSizes(list(values))
        self._default_sizes = values

    def restore_default_sizes(self) -> bool:
        if self._default_sizes is None or len(self._default_sizes) != self.count():
            return False
        self.setSizes(list(self._default_sizes))
        return True

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._default_sizes is None:
            values = self.sizes()
            if values and all(value > 0 for value in values):
                self._default_sizes = tuple(values)
