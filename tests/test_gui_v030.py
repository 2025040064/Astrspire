"""Offline integration for compact navigation, panels, controls and commands."""

from __future__ import annotations

import json

import pytest
from PySide6.QtCore import QObject, QPoint, Signal
from PySide6.QtWidgets import QLabel, QMessageBox

from qx_stc_isp.gui import MainWindow
from qx_stc_isp.vofa import TelemetryEvent
from qx_stc_isp.vofa.commands import CommandEntry, load_commands


@pytest.fixture(autouse=True)
def no_physical_ports(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("GUI integration must not open a physical serial port")

    monkeypatch.setattr("serial.Serial", forbidden)
    monkeypatch.setattr("qx_stc_isp.transport.PySerialTransport.open", forbidden)
    monkeypatch.setattr("qx_stc_isp.gui.enumerate_ports", lambda: [])


@pytest.fixture
def window(qtbot, tmp_path, monkeypatch):
    warnings = []
    monkeypatch.setattr("qx_stc_isp.gui.QMessageBox.warning", lambda *args: warnings.append(args[2]))
    monkeypatch.setattr("qx_stc_isp.gui.QMessageBox.question", lambda *_a, **_k: QMessageBox.No)
    instance = MainWindow(log_dir=tmp_path / "logs")
    instance.test_warnings = warnings
    qtbot.addWidget(instance)
    instance.show()
    yield instance
    instance.close()


class FakeLink(QObject):
    transmitted = Signal(bytes)
    can_send = True
    channel_names = ()

    def __init__(self, parent):
        super().__init__(parent)
        self.pending = []
        self.stopped = False

    def enqueue_send(self, payload):
        self.pending.append(bytes(payload))
        return len(self.pending)

    def clear_pending(self):
        count = len(self.pending)
        self.pending.clear()
        return count

    def stop(self):
        self.stopped = True
        self.clear_pending()


def fake_connection(window):
    link = FakeLink(window)
    link.transmitted.connect(window._handle_monitor_transmitted)
    window._monitor_worker = link
    window._monitor_active = True
    window._handle_monitor_status("connected", "离线假连接")
    return link


def choose_open(monkeypatch, path):
    monkeypatch.setattr("qx_stc_isp.gui.QFileDialog.getOpenFileName", lambda *_a, **_k: (str(path), "JSON (*.json)"))


def choose_save(monkeypatch, path):
    monkeypatch.setattr("qx_stc_isp.gui.QFileDialog.getSaveFileName", lambda *_a, **_k: (str(path), "JSON (*.json)"))


def test_default_hardware_layout_has_no_old_persistent_explanatory_labels(window):
    assert window.mode_combo.currentData() == "hardware"
    assert window.mode_combo.isHidden()
    assert not window._active
    assert not window.start_button.isEnabled()
    for label in window.findChildren(QLabel):
        assert all(term not in label.text() for term in ("工作模式", "静态识别表", "官方成功会话"))


def test_simulation_tool_action_is_explicit_and_does_not_start_a_download(window, monkeypatch):
    launched = []
    monkeypatch.setattr(window, "_launch_session", lambda *_a, **_k: launched.append(True))
    assert not window.simulation_action.isChecked()
    window.simulation_action.trigger()
    assert window.is_simulator_mode
    assert window.simulation_action.isChecked()
    assert window.start_button.isEnabled()
    assert not window._active and not launched
    window.simulation_action.trigger()
    assert not window.is_simulator_mode
    assert not window._active and not launched


def test_sidebar_navigation_selects_protocol_command_and_control_sections(window):
    buttons = (window.monitor_nav_button, window.commands_nav_button, window.controls_nav_button)
    for index in range(3):
        window._show_monitor_section(index)
        assert window.tabs.currentIndex() == 1
        assert window.monitor_sidebar_stack.currentIndex() == index
        assert [button.isChecked() for button in buttons] == [n == index for n in range(3)]


def test_all_sixteen_values_and_precision_update_without_another_sample(window):
    window._show_monitor_section(0)
    values = tuple(1.234567 + channel for channel in range(16))
    window._handle_monitor_events([TelemetryEvent(raw=b"", samples=values)])
    assert len(window.channel_value_labels) == 16
    assert "16.234567" in window.channel_value_labels[15].text()
    window.precision_spin.setValue(2)
    assert "1.23" in window.channel_value_labels[0].text()
    assert "16.23" in window.channel_value_labels[15].text()
    window.precision_spin.setValue(0)
    assert window.channel_value_labels[0].text().endswith("1")


def test_theme_changes_preserve_waveform_and_visual_controls(window):
    window._handle_monitor_events([TelemetryEvent(raw=b"", samples=(0.5, -2.0))])
    window.control_channel_spin.setValue(2)
    window.add_visual_control("gauge")
    before = tuple(tuple(series) for series in window.waveform._series)
    config = window.control_workspace.export_config()
    window.theme_combo.setCurrentIndex(window.theme_combo.findData("dark"))
    assert window.waveform.theme == "dark"
    assert window.control_workspace.theme == "dark"
    assert tuple(tuple(series) for series in window.waveform._series) == before
    assert window.control_workspace.export_config() == config
    assert window.control_workspace.controls[0].gauge.value == -2.0


def test_main_splitters_have_expected_panes_and_restore_without_collapse(window, qtbot):
    specs = ((window.isp_splitter, 2, 0), (window.isp_output_splitter, 2, 0),
             (window.monitor_splitter, 3, 1), (window.monitor_plot_splitter, 2, 1))
    for splitter, count, page in specs:
        window.tabs.setCurrentIndex(page)
        qtbot.wait(1)
        assert splitter.count() == count
        assert not splitter.childrenCollapsible()
        initial = splitter.sizes()
        splitter.setSizes([initial[0] * 2, *initial[1:]])
        assert all(value > 0 for value in splitter.sizes())
        assert splitter.restore_default_sizes()
        assert all(value > 0 for value in splitter.sizes())


@pytest.mark.parametrize("page,scroll_name", [(0, "isp_side_scroll"), (1, "monitor_side_scroll")])
def test_minimum_window_sidebars_and_waveform_are_not_clipped(window, qtbot, page, scroll_name):
    window.resize(window.minimumSize())
    window.tabs.setCurrentIndex(page)
    qtbot.wait(5)
    scroll = getattr(window, scroll_name)
    assert scroll.widget().width() <= scroll.viewport().width()
    if page == 1:
        assert window.waveform.height() >= window.waveform.minimumHeight()
        wave_bottom = window.waveform.mapToGlobal(QPoint(0, window.waveform.height())).y()
        cursor_top = window.cursor_value_label.mapToGlobal(QPoint(0, 0)).y()
        assert wave_bottom <= cursor_top


def test_add_visual_controls_binds_selected_channel_and_uses_current_data(window):
    window._handle_monitor_events([TelemetryEvent(raw=b"", samples=(1.0, 0.25))])
    window.control_channel_spin.setValue(2)
    window.control_title_edit.setText("通道B")
    window.add_visual_control("gauge")
    window.add_visual_control("value")
    cards = window.control_workspace.controls
    assert [card.spec.channel for card in cards] == [1, 1]
    assert cards[0].gauge.value == 0.25
    assert cards[1].value_label.text() == "0.250000"
    assert window.visual_tabs.currentIndex() == 1


def test_control_button_only_queues_after_a_sendable_connection(window):
    window.control_payload_edit.setText("LED ON")
    window.add_visual_control("button")
    button = window.control_workspace.controls[0].button
    button.click()
    assert window._tx_bytes == 0
    assert not button.isEnabled()
    assert "未连接" in window.monitor_status_label.text()
    link = fake_connection(window)
    button.click()
    assert link.pending == [b"LED ON"]
    assert window._tx_bytes == 0
    link.transmitted.emit(link.pending[0])
    assert window._tx_bytes == 6


def test_load_visual_controls_is_inert_until_user_clicks(window, tmp_path, monkeypatch):
    path = tmp_path / "controls.json"
    path.write_text(json.dumps({"schema_version": 1, "controls": [
        dict(kind="button", channel=0, title="BTN", command="DO")
    ]}), encoding="utf-8")
    link = fake_connection(window)
    choose_open(monkeypatch, path)
    window.load_visual_controls()
    assert window.control_workspace.count == 1
    assert link.pending == [] and window._tx_bytes == 0
    window.control_workspace.controls[0].button.click()
    assert link.pending == [b"DO"]


def test_invalid_visual_control_file_keeps_previous_controls(window, tmp_path, monkeypatch):
    window.add_visual_control("value")
    before = window.control_workspace.export_config()
    path = tmp_path / "invalid-controls.json"
    path.write_text(json.dumps({"schema_version": 1, "controls": [
        dict(kind="gauge", channel=16, title="bad", command="")
    ]}), encoding="utf-8")
    choose_open(monkeypatch, path)
    window.load_visual_controls()
    assert window.control_workspace.export_config() == before
    assert window.test_warnings


def test_rejecting_visual_control_clear_confirmation_preserves_layout(window, monkeypatch):
    window.add_visual_control("value")
    questions = []
    monkeypatch.setattr("qx_stc_isp.gui.QMessageBox.question", lambda *args: questions.append(args[2]) or QMessageBox.No)
    window.clear_visual_controls()
    assert window.control_workspace.count == 1
    assert "1个" in questions[0] and "无法恢复" in questions[0]


def test_rejecting_command_clear_confirmation_preserves_entries(window, monkeypatch):
    window._commands = [CommandEntry("A", "A")]
    window._refresh_command_list()
    questions = []
    monkeypatch.setattr("qx_stc_isp.gui.QMessageBox.question", lambda *args: questions.append(args[2]) or QMessageBox.No)
    window.clear_command_list()
    assert window.command_list.count() == 1
    assert "1条" in questions[0] and "无法恢复" in questions[0]


def test_command_file_save_load_preserves_inert_entries(window, tmp_path, monkeypatch):
    entries = [CommandEntry("first", "01 02", "hex"), CommandEntry("second", "PING", ending="LF")]
    window._commands = entries[:]
    window._refresh_command_list()
    path = tmp_path / "commands.json"
    choose_save(monkeypatch, path)
    window.save_command_list()
    assert load_commands(path) == tuple(entries)
    window._commands = []
    window._refresh_command_list()
    link = fake_connection(window)
    choose_open(monkeypatch, path)
    window.load_command_list()
    assert window._commands == entries
    assert window.command_list.count() == 2
    assert link.pending == [] and window._tx_bytes == 0


def test_only_selected_command_queues_and_tx_count_waits_for_completion(window):
    window._commands = [CommandEntry("not selected", "WRONG"), CommandEntry("selected", "01 02", "hex")]
    window._refresh_command_list()
    link = fake_connection(window)
    window.command_list.setCurrentRow(-1)
    assert not window.send_selected_command()
    assert link.pending == []
    window.command_list.setCurrentRow(1)
    assert window.send_selected_command()
    assert link.pending == [b"\x01\x02"]
    assert window._tx_bytes == 0
    link.transmitted.emit(b"\x01\x02")
    assert window._tx_bytes == 2
