from __future__ import annotations

import math

import pytest
from PySide6.QtCore import Qt

from qx_stc_isp.vofa.controls import ControlWorkspace, MAX_CONTROLS


@pytest.fixture
def workspace(qtbot, monkeypatch):
    monkeypatch.setattr("serial.Serial", lambda *_a, **_k: pytest.fail("controls must not access serial"))
    widget = ControlWorkspace()
    widget.resize(600, 420)
    qtbot.addWidget(widget)
    widget.show()
    return widget


def test_value_gauge_button_and_missing_channels(workspace):
    value = workspace.add_control("value", channel=0, title="电压")
    gauge = workspace.add_control("gauge", channel=1)
    button = workspace.add_control("button", command="LED ON\n")
    assert workspace.count == 3
    assert value.value_label.text() == "—"
    assert gauge.value_label.text() == "—" and gauge.gauge.value is None
    workspace.set_samples((0.25, 5.0), ("A", "B"))
    assert value.value_label.text() == "0.250000"
    assert value.channel_label.text() == "A"
    assert gauge.value_label.text() == "5.000000"
    assert gauge.gauge.value == 5.0
    assert not gauge.gauge.grab().isNull()
    workspace.set_samples((1.0,))
    assert gauge.value_label.text() == "—"
    assert gauge.gauge.value is None
    assert button.button.isEnabled()


def test_only_manual_button_click_emits_command_and_keyboard_works(workspace, qtbot):
    sent = []
    workspace.send_requested.connect(sent.append)
    button = workspace.add_control("button", command="A\nB", title="发送")
    workspace.set_samples((1.0,))
    workspace.set_theme("dark")
    config = workspace.export_config()
    workspace.load_config(config)
    assert sent == []
    button = workspace.controls[0].button
    qtbot.mouseClick(button, Qt.LeftButton)
    assert sent == ["A\nB"]
    button.setFocus()
    qtbot.keyClick(button, Qt.Key_Space)
    assert sent == ["A\nB", "A\nB"]


def test_loading_and_adding_receive_latest_samples_without_transmission(workspace):
    sent = []
    workspace.send_requested.connect(sent.append)
    workspace.set_samples((3.5,))
    value = workspace.add_control("value")
    assert value.value_label.text() == "3.500000"
    workspace.load_config([dict(kind="gauge", channel=0, title="T", command="")])
    assert workspace.controls[0].gauge.value == 3.5
    assert sent == []


@pytest.mark.parametrize("invalid", [
    [dict(kind="unknown", channel=0, title="", command="")],
    [dict(kind="button", channel=True, title="", command="")],
    [dict(kind="value", channel=-1, title="", command="")],
    [dict(kind="value", channel=16, title="", command="")],
    [dict(kind="value", channel=0, title="x" * 81, command="")],
    [dict(kind="button", channel=0, title="", command="x" * 4097)],
    [dict(kind="value", channel=0, title="", command="", extra=1)],
    [dict(kind="value", channel=0, title="")],
    [dict(kind="value", channel=0, title=None, command="")],
    [dict(kind="button", channel=0, title="", command=1)],
    "[]", {}, [None],
])
def test_invalid_configuration_keeps_all_existing_controls(workspace, invalid):
    workspace.add_control("value", title="保留")
    before = workspace.export_config()
    with pytest.raises(ValueError):
        workspace.load_config(invalid)
    assert workspace.export_config() == before


def test_entire_configuration_is_validated_before_replacing(workspace):
    workspace.add_control("value", title="旧值")
    before = workspace.controls[0]
    with pytest.raises(ValueError):
        workspace.load_config([
            dict(kind="gauge", channel=1, title="可用", command=""),
            dict(kind="gauge", channel=20, title="越界", command=""),
        ])
    assert workspace.controls == (before,)


def test_export_is_independent_and_round_trips(workspace):
    workspace.add_control("value", 15, "温度", "")
    workspace.add_control("button", 0, "命令", "开灯\r\n")
    config = workspace.export_config()
    workspace.clear_controls()
    assert workspace.count == 0
    workspace.load_config(config)
    assert workspace.export_config() == config
    config[0]["title"] = "改外部副本"
    assert workspace.controls[0].spec.title == "温度"


def test_maximum_controls_does_not_discard_previous_cards(workspace):
    for _ in range(MAX_CONTROLS):
        workspace.add_control("value")
    with pytest.raises(ValueError):
        workspace.add_control("button")
    assert workspace.count == MAX_CONTROLS
    with pytest.raises(ValueError):
        workspace.load_config(workspace.export_config() + [dict(kind="value", channel=0, title="", command="")])
    assert workspace.count == MAX_CONTROLS


@pytest.mark.parametrize("invalid", [math.nan, math.inf, -math.inf, True, "1", None])
def test_invalid_samples_display_missing_instead_of_zero(workspace, invalid):
    value = workspace.add_control("value")
    gauge = workspace.add_control("gauge")
    workspace.set_samples((invalid,))
    assert value.value_label.text() == "—"
    assert gauge.gauge.value is None


def test_titles_and_channel_names_are_plain_text(workspace):
    card = workspace.add_control("value", title='<img src="file:///private.png">')
    workspace.set_samples((1.0,), ('<b>name</b>',))
    assert card.title_label.textFormat() == Qt.PlainText
    assert card.title_label.text() == '<img src="file:///private.png">'
    assert card.channel_label.textFormat() == Qt.PlainText
    assert card.channel_label.text() == '<b>name</b>'


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_theme_and_narrow_layout_keep_values(workspace, qtbot, theme):
    workspace.add_control("gauge", 0, "A")
    workspace.add_control("value", 1, "B")
    workspace.set_samples((0.5, -3.0))
    config = workspace.export_config()
    workspace.set_theme(theme)
    workspace.resize(240, 420)
    qtbot.wait(1)
    assert workspace.theme == theme
    assert workspace.export_config() == config
    assert workspace.controls[1].value_label.text() == "-3.000000"
    assert workspace._grid.getItemPosition(1)[:2] == (1, 0)
    assert not workspace.grab().isNull()


def test_invalid_theme_keeps_current_theme_and_data(workspace):
    workspace.add_control("value")
    workspace.set_samples((2.0,))
    with pytest.raises(ValueError):
        workspace.set_theme("other")
    assert workspace.theme == "light"
    assert workspace.controls[0].value_label.text() == "2.000000"


def test_clear_disables_stale_button_and_emits_nothing(workspace):
    sent = []
    workspace.send_requested.connect(sent.append)
    button = workspace.add_control("button", command="old").button
    workspace.clear_controls()
    button.click()
    assert sent == []
    assert workspace.count == 0


def test_wide_narrow_wide_reflows_without_losing_control_order(workspace, qtbot):
    for kind in ("value", "gauge", "button"):
        workspace.add_control(kind)
    for width, columns in ((700, 2), (240, 1), (700, 2)):
        workspace.resize(width, 500)
        qtbot.wait(1)
        assert workspace._columns == columns
        assert workspace._grid.getItemPosition(1)[:2] == ((0, 1) if columns == 2 else (1, 0))
    assert [card.spec.kind for card in workspace.controls] == ["value", "gauge", "button"]


@pytest.mark.parametrize("kind", ["value", "gauge", "button"])
def test_long_unbroken_titles_and_channel_names_do_not_widen_scroll_host(workspace, qtbot, kind):
    workspace.add_control(kind, title="A" * 80)
    workspace.set_samples((0.5,), ("B" * 80,))
    workspace.resize(240, 400)
    qtbot.wait(1)
    assert workspace._host.width() <= workspace._scroll.viewport().width()


def test_widget_construction_failure_does_not_replace_previous_controls(workspace, monkeypatch):
    old = workspace.add_control("value", title="保留")
    real_make = workspace._make_control
    calls = 0

    def fail_second(spec):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("construction failed")
        return real_make(spec)

    monkeypatch.setattr(workspace, "_make_control", fail_second)
    with pytest.raises(RuntimeError, match="construction failed"):
        workspace.load_config([
            dict(kind="value", channel=0, title="新1", command=""),
            dict(kind="gauge", channel=1, title="新2", command=""),
        ])
    assert workspace.controls == (old,)
