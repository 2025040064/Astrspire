from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget

from qx_stc_isp.appearance import stylesheet
from qx_stc_isp.panels import AdjustableSplitter


def make_splitter(qtbot, orientation=Qt.Horizontal):
    splitter = AdjustableSplitter(orientation)
    for _ in range(2):
        pane = QWidget()
        pane.setMinimumSize(60, 60)
        splitter.addWidget(pane)
    splitter.resize(600, 400)
    qtbot.addWidget(splitter)
    splitter.show()
    qtbot.wait(1)
    splitter.set_default_sizes([200, 394] if orientation == Qt.Horizontal else [150, 244])
    return splitter


@pytest.mark.parametrize("orientation", [Qt.Horizontal, Qt.Vertical])
def test_default_handles_are_accessible_non_collapsing_and_keyboard_focusable(qtbot, orientation):
    splitter = make_splitter(qtbot, orientation)
    handle = splitter.handle(1)
    assert splitter.handleWidth() == 6
    assert not splitter.childrenCollapsible()
    assert handle.focusPolicy() == Qt.StrongFocus
    assert "分隔条" in handle.accessibleName()
    assert "方向键" in handle.accessibleDescription()
    handle.setFocus()
    qtbot.waitUntil(handle.hasFocus)


@pytest.mark.parametrize("orientation,positive,negative", [
    (Qt.Horizontal, Qt.Key_Right, Qt.Key_Left),
    (Qt.Vertical, Qt.Key_Down, Qt.Key_Up),
])
def test_direction_keys_move_ten_pixels_and_home_restores(qtbot, orientation, positive, negative):
    splitter = make_splitter(qtbot, orientation)
    initial = splitter.sizes()
    handle = splitter.handle(1)
    qtbot.keyClick(handle, positive)
    assert splitter.sizes() == [initial[0] + 10, initial[1] - 10]
    qtbot.keyClick(handle, negative)
    assert splitter.sizes() == initial
    qtbot.keyClick(handle, positive)
    qtbot.keyClick(handle, Qt.Key_Home)
    assert splitter.sizes() == initial


@pytest.mark.parametrize("orientation,positive,negative", [
    (Qt.Horizontal, Qt.Key_Right, Qt.Key_Left),
    (Qt.Vertical, Qt.Key_Down, Qt.Key_Up),
])
def test_keyboard_respects_child_minimum_and_maximum(qtbot, orientation, positive, negative):
    splitter = make_splitter(qtbot, orientation)
    if orientation == Qt.Horizontal:
        splitter.widget(0).setMaximumWidth(210)
        splitter.widget(1).setMinimumWidth(380)
    else:
        splitter.widget(0).setMaximumHeight(160)
        splitter.widget(1).setMinimumHeight(230)
    handle = splitter.handle(1)
    for _ in range(30):
        qtbot.keyClick(handle, positive)
    values = splitter.sizes()
    assert values[0] <= (210 if orientation == Qt.Horizontal else 160)
    assert values[1] >= (380 if orientation == Qt.Horizontal else 230)
    for _ in range(80):
        qtbot.keyClick(handle, negative)
    assert splitter.sizes()[0] >= 60
    assert all(value > 0 for value in splitter.sizes())


def test_double_click_restores_default_without_resets_on_ordinary_resize(qtbot):
    splitter = make_splitter(qtbot)
    initial = splitter.sizes()
    handle = splitter.handle(1)
    qtbot.keyClick(handle, Qt.Key_Right)
    assert splitter.sizes() != initial
    qtbot.mouseDClick(handle, Qt.LeftButton, pos=handle.rect().center())
    assert splitter.sizes() == initial
    splitter.setSizes([350, 244])
    manual = splitter.sizes()
    splitter.resize(700, 400)
    qtbot.wait(1)
    assert splitter.sizes()[0] > 300
    assert splitter.sizes()[0] / sum(splitter.sizes()) > 0.45
    assert splitter.default_sizes == tuple(initial)
    assert manual != initial


@pytest.mark.parametrize("invalid", [[], [1], [1, 2, 3], [0, 2], [-1, 2], [True, 2], [1.0, 2], "100,200", None])
def test_invalid_default_sizes_do_not_mutate_existing_layout(qtbot, invalid):
    splitter = make_splitter(qtbot)
    before = splitter.sizes()
    defaults = splitter.default_sizes
    with pytest.raises(ValueError):
        splitter.set_default_sizes(invalid)
    assert splitter.sizes() == before
    assert splitter.default_sizes == defaults


def test_first_visible_layout_is_default_when_not_explicit(qtbot):
    splitter = AdjustableSplitter()
    splitter.addWidget(QWidget())
    splitter.addWidget(QWidget())
    splitter.resize(400, 200)
    qtbot.addWidget(splitter)
    splitter.show()
    qtbot.wait(1)
    assert splitter.default_sizes is not None
    first = splitter.sizes()
    splitter.setSizes([100, 294])
    assert splitter.restore_default_sizes()
    assert splitter.sizes() == first


def test_restore_ignores_changed_child_count_safely(qtbot):
    splitter = make_splitter(qtbot)
    splitter.addWidget(QWidget())
    before = splitter.sizes()
    assert not splitter.restore_default_sizes()
    assert splitter.sizes() == before


def test_horizontal_arrow_directions_follow_physical_direction_in_rtl(qtbot):
    splitter = make_splitter(qtbot)
    splitter.setLayoutDirection(Qt.RightToLeft)
    qtbot.wait(1)
    handle = splitter.handle(1)
    before = handle.x()
    qtbot.keyClick(handle, Qt.Key_Right)
    assert handle.x() == before + 10
    qtbot.keyClick(handle, Qt.Key_Left)
    assert handle.x() == before


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_handle_can_render_focus_with_both_themes(qtbot, theme):
    splitter = make_splitter(qtbot)
    splitter.setStyleSheet(stylesheet(theme))
    splitter.handle(1).setFocus()
    assert not splitter.grab().isNull()
