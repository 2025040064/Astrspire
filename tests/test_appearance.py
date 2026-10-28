from __future__ import annotations

import pytest
from pathlib import Path
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QFontDatabase
from PySide6.QtWidgets import QFrame, QLabel, QLineEdit, QMainWindow, QPlainTextEdit, QVBoxLayout, QWidget

from qx_stc_isp.appearance import THEMES, stylesheet, theme_palette, validate_theme
from qx_stc_isp.gui import _load_bundled_fonts
from qx_stc_isp.vofa.waveform import WaveformWidget


def test_optional_local_fonts_or_system_fallback_are_usable(qtbot):
    available_before = set(QFontDatabase.families())
    _load_bundled_fonts()
    available_after = set(QFontDatabase.families())
    assert available_before <= available_after
    local_font = Path(__file__).resolve().parents[1] / "assets/fonts/PingFang SC Regular.ttf"
    if local_font.is_file():
        assert "PingFang SC" in available_after
    else:
        assert available_after, "Public source uses the platform font fallback"


@pytest.mark.parametrize("theme", THEMES)
def test_stylesheet_is_complete_and_has_application_surfaces(theme):
    css = stylesheet(theme)
    assert "$" not in css
    assert theme_palette(theme).background in css
    for selector in ("QMainWindow", "QDialog", "QFrame#Card", "QLineEdit", "QPlainTextEdit",
                     "QSpinBox", "QDoubleSpinBox", "QScrollBar", "QSplitter::handle"):
        assert selector in css


@pytest.mark.parametrize("theme", [None, [], "", "system", "Dark", 1])
def test_unknown_themes_fail_without_silent_fallback(theme):
    with pytest.raises(ValueError):
        validate_theme(theme)
    with pytest.raises(ValueError):
        stylesheet(theme)


def test_theme_change_preserves_waveform_inspection_state(qtbot):
    wave = WaveformWidget()
    qtbot.addWidget(wave)
    wave.resize(420, 240)
    wave.show()
    wave.setFocus()
    for index in range(30):
        wave.append_samples((float(index), -float(index)), ["A", "B"])
    wave.zoom_view(2)
    wave.set_channel_visible(1, False)
    wave._read_cursor(wave.plot_rect().center())
    wave.set_paused(True)
    frozen = wave.displayed_series
    retained = tuple(tuple(series) for series in wave._series)
    view, cursor, visible = wave.view_range, wave.cursor_position, wave.channel_visibility
    wave.set_theme("dark")
    assert wave.theme == "dark"
    assert wave.paused
    assert wave.displayed_series == frozen
    assert tuple(tuple(series) for series in wave._series) == retained
    assert wave.view_range == view
    assert wave.cursor_position == cursor
    assert wave.channel_visibility == visible
    assert wave.focusPolicy() == Qt.StrongFocus
    palette = theme_palette("dark").channel_colors
    assert wave.channel_colors == tuple(palette[i % len(palette)] for i in range(len(wave.channel_colors)))
    wave.set_theme("light")
    palette = theme_palette("light").channel_colors
    assert wave.channel_colors == tuple(palette[i % len(palette)] for i in range(len(wave.channel_colors)))
    assert wave.displayed_series == frozen


def test_invalid_waveform_theme_keeps_old_palette(qtbot):
    wave = WaveformWidget()
    qtbot.addWidget(wave)
    with pytest.raises(ValueError):
        wave.set_theme("not-a-theme")
    assert wave.theme == "light"
    palette = theme_palette("light").channel_colors
    assert wave.channel_colors == tuple(palette[i % len(palette)] for i in range(len(wave.channel_colors)))


@pytest.mark.parametrize("theme", THEMES)
def test_waveform_renders_requested_background_at_narrow_size(qtbot, theme):
    wave = WaveformWidget()
    qtbot.addWidget(wave)
    wave.resize(180, 200)
    wave.set_theme(theme)
    wave.append_samples((1.0, 2.0), ["A", "B"])
    wave.show()
    image = wave.grab().toImage()
    assert image.pixelColor(1, 1) == QColor(theme_palette(theme).surface)
    assert not image.isNull()


@pytest.mark.parametrize("theme", THEMES)
def test_application_stylesheet_applies_to_input_and_log(qtbot, theme):
    window = QMainWindow()
    root = QWidget()
    root.setObjectName("Root")
    window.setCentralWidget(root)
    layout = QVBoxLayout(root)
    card = QFrame()
    card.setObjectName("Card")
    inside = QVBoxLayout(card)
    inside.addWidget(QLabel("Theme"))
    entry, log = QLineEdit("input"), QPlainTextEdit("log")
    inside.addWidget(entry)
    inside.addWidget(log)
    layout.addWidget(card)
    window.setStyleSheet(stylesheet(theme))
    window.resize(400, 300)
    qtbot.addWidget(window)
    window.show()
    qtbot.wait(1)
    palette = theme_palette(theme)
    assert entry.palette().color(entry.backgroundRole()) == QColor(palette.input_background)
    assert log.palette().color(log.backgroundRole()) == QColor(palette.input_background)
    assert not window.grab().isNull()
