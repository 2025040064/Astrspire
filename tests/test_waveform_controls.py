from __future__ import annotations

import math

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent, QWheelEvent
from PySide6.QtWidgets import QApplication

from qx_stc_isp.vofa.waveform import WaveformWidget


@pytest.fixture
def waveform(qtbot):
    widget = WaveformWidget()
    widget.resize(640, 320)
    qtbot.addWidget(widget)
    widget.show()
    for index in range(200):
        widget.append_samples((float(index), float(index * 2)), ["A", "B"])
    yield widget
    widget.close()


def wheel(widget, position: QPointF, delta: int) -> QWheelEvent:
    event = QWheelEvent(position, position, QPoint(), QPoint(0, delta), Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
    QApplication.sendEvent(widget, event)
    return event


def mouse(widget, event_type, position: QPointF, *, button=Qt.NoButton, buttons=Qt.NoButton) -> None:
    event = QMouseEvent(event_type, position, position, button, buttons, Qt.NoModifier)
    QApplication.sendEvent(widget, event)


def test_wheel_zooms_around_cursor_and_ignores_non_plot_area(waveform) -> None:
    left, right = waveform.view_range
    area = waveform.plot_rect()
    position = QPointF(area.left() + area.width() / 4, area.center().y())
    wheel(waveform, position, 120)
    new_left, new_right = waveform.view_range
    assert new_right - new_left == pytest.approx((right - left) / 1.25)
    assert new_left + (new_right - new_left) / 4 == pytest.approx(left + (right - left) / 4)
    assert not waveform.auto_fit_enabled
    before = waveform.view_range
    outside = wheel(waveform, QPointF(0, 0), 120)
    assert not outside.isAccepted()
    assert waveform.view_range == before
    assert waveform.cursor_position is not None


def test_click_small_movement_drag_pan_and_escape_rollback(waveform, qtbot) -> None:
    waveform.zoom_view(2)
    initial = waveform.view_range
    position = waveform.plot_rect().center()
    mouse(waveform, QEvent.MouseButtonPress, position, button=Qt.LeftButton, buttons=Qt.LeftButton)
    mouse(waveform, QEvent.MouseMove, position + QPointF(2, 0), buttons=Qt.LeftButton)
    assert not waveform._dragging
    assert waveform.view_range == initial
    mouse(waveform, QEvent.MouseMove, position + QPointF(60, 0), buttons=Qt.LeftButton)
    assert waveform._dragging
    assert waveform.view_range[0] < initial[0]
    assert waveform.view_range[1] - waveform.view_range[0] == pytest.approx(initial[1] - initial[0])
    qtbot.keyClick(waveform, Qt.Key_Escape)
    assert waveform.view_range == initial
    assert waveform._drag_origin is None
    assert not waveform._dragging
    # A later click cannot accidentally commit the cancelled pan.
    mouse(waveform, QEvent.MouseButtonRelease, position + QPointF(60, 0), button=Qt.LeftButton)
    assert waveform.view_range == initial


def test_pan_commit_and_data_boundaries(waveform) -> None:
    waveform.zoom_view(4)
    initial = waveform.view_range
    position = waveform.plot_rect().center()
    mouse(waveform, QEvent.MouseButtonPress, position, button=Qt.LeftButton, buttons=Qt.LeftButton)
    mouse(waveform, QEvent.MouseMove, position + QPointF(10000, 0), buttons=Qt.LeftButton)
    mouse(waveform, QEvent.MouseButtonRelease, position + QPointF(10000, 0), button=Qt.LeftButton)
    assert waveform.view_range[0] == 0
    assert waveform.view_range[1] - waveform.view_range[0] == pytest.approx(initial[1] - initial[0])
    assert not waveform.auto_fit_enabled
    assert not waveform._dragging


def test_pause_freezes_snapshot_but_retains_new_samples(qtbot) -> None:
    widget = WaveformWidget(max_samples=16)
    qtbot.addWidget(widget)
    for index in range(10):
        widget.append_samples((float(index),), ["CH1"])
    widget.set_paused(True)
    frozen = widget.displayed_series
    frozen_range = widget.view_range
    for index in range(10, 40):
        widget.append_samples((float(index),), ["renamed"])
    assert widget.displayed_series == frozen
    assert widget.view_range == frozen_range
    assert len(widget._series[0]) == 16
    assert widget._series[0][-1] == 39
    widget.set_paused(False)
    assert widget.displayed_series[0][-1] == 39
    assert widget.view_range == (24, 39)


def test_visibility_changes_scale_without_losing_data(qtbot) -> None:
    widget = WaveformWidget()
    qtbot.addWidget(widget)
    widget.append_samples((1, 1000), ["small", "large"])
    assert widget.y_range[1] > 1000
    retained = widget.displayed_series
    widget.set_channel_visible(1, False)
    assert widget.y_range == (0, 2)
    assert widget.channel_visibility[1] is False
    assert widget.displayed_series == retained
    widget.set_channel_visible(0, False)
    assert widget.y_range == (-1, 1)
    widget.set_channel_visible(1, True)
    assert widget.y_range[1] > 1000
    with pytest.raises(ValueError):
        widget.set_channel_visible(16, False)


def test_keyboard_pause_zoom_pan_and_reset(waveform, qtbot) -> None:
    qtbot.keyClick(waveform, Qt.Key_Space)
    assert waveform.paused
    qtbot.keyClick(waveform, Qt.Key_Space)
    assert not waveform.paused
    qtbot.keyClick(waveform, Qt.Key_Plus)
    assert not waveform.auto_fit_enabled
    initial = waveform.view_range
    qtbot.keyClick(waveform, Qt.Key_Left)
    assert waveform.view_range[0] < initial[0]
    qtbot.keyClick(waveform, Qt.Key_Home)
    assert waveform.auto_fit_enabled
    assert waveform.view_range == (0, 199)


def test_cursor_time_step_and_text_signal(waveform, qtbot) -> None:
    messages = []
    waveform.cursor_text_changed.connect(messages.append)
    waveform.set_sample_interval_ms(2)
    waveform._read_cursor(waveform.plot_rect().center())
    assert waveform.cursor_position[0] == pytest.approx(0.199)
    assert messages and "t=0.199 s" in messages[-1]
    waveform.set_sample_interval_ms(4)
    assert waveform.cursor_position[0] == pytest.approx(0.398)
    assert "t=0.398 s" in messages[-1]
    waveform.set_sample_interval_ms(1000000)
    assert waveform.sample_interval_ms == 1000000
    for value in (0, -1, math.nan, math.inf):
        with pytest.raises(ValueError):
            waveform.set_sample_interval_ms(value)


def test_buffers_channels_and_nonfinite_frames_are_bounded(qtbot) -> None:
    widget = WaveformWidget(max_samples=16)
    qtbot.addWidget(widget)
    for index in range(25):
        assert widget.append_samples((float(index),) * 20, [f"CH{i}" for i in range(20)])
    assert len(widget._series) == 16
    assert all(len(series) == 16 for series in widget._series)
    assert widget._series[0][0] == 9
    retained = widget.displayed_series
    rejected = []
    widget.sample_rejected.connect(rejected.append)
    for frame in ((math.nan,), (math.inf,), (-math.inf,), ("invalid",)):
        assert not widget.append_samples(frame, ["bad"])
    assert widget.displayed_series == retained
    assert len(rejected) == 4
    assert not widget.append_samples((), [])
    widget.set_max_samples(20)
    assert widget._series[0].maxlen == 20
    for invalid in (True, 0, 100001, 2.5):
        with pytest.raises(ValueError):
            widget.set_max_samples(invalid)


def test_clear_while_paused_and_narrow_render(waveform, qtbot) -> None:
    waveform.set_paused(True)
    waveform.clear()
    assert waveform.paused
    assert waveform.displayed_series == ()
    assert waveform.cursor_position is None
    waveform.set_paused(False)
    waveform.resize(150, 200)
    waveform.append_samples((1e308, -1e308), [])
    qtbot.wait(10)
    image = waveform.grab()
    assert image.width() == 150
    assert all(math.isfinite(value) for value in waveform.y_range)
