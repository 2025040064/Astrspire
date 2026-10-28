"""Offline user actions in the native packet/protocol workbench."""
import json
from uuid import uuid4

import pytest
from PySide6.QtCore import QTimer

from qx_stc_isp.gui import MainWindow
from qx_stc_isp.vofa.plugins import PLUGIN_REGISTRY
from qx_stc_isp.vofa.serial_preferences import SerialPreset


@pytest.fixture
def window(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr("serial.Serial", lambda *a, **k: pytest.fail("No hardware"))
    monkeypatch.setattr("qx_stc_isp.gui.enumerate_ports", lambda: [])
    result = MainWindow(log_dir=tmp_path / "logs")
    qtbot.addWidget(result)
    result.show()
    yield result
    result._monitor_active = False
    result._monitor_connected = False
    result._monitor_worker = None
    result.close()


def test_template_fill_command_and_engine_actions_never_connect(window):
    from qx_stc_isp.vofa.decoders import create_decoder
    window.open_serial_tools()
    tool = window.serial_tools_dialog
    tool.generate()
    raw = tool._payload
    assert raw and "未发送" in tool.protocol_result.toPlainText()
    tool.preview_offline()
    report = json.loads(tool.protocol_result.toPlainText())
    assert report["frames"][0]["checksum_valid"]
    tool.fill_send()
    assert window.send_mode_combo.currentData() == "hex"
    assert window.send_ending_combo.currentData() == "none"
    assert bytes.fromhex(window.send_text_edit.text()) == raw
    count = len(window._commands)
    tool.add_command()
    assert len(window._commands) == count + 1
    identifier = "test." + uuid4().hex
    tool.identifier_edit.setText(identifier)
    try:
        tool.apply_engine()
        decoder = create_decoder("plugin:" + identifier)
        assert decoder.feed(raw)[0].samples == (1.0,)
        assert window._monitor_worker is None and window._tx_bytes == 0
    finally:
        PLUGIN_REGISTRY.unregister(identifier)


def test_large_packet_detail_is_bounded_but_selected_bytes_remain_complete(window):
    window.open_serial_tools()
    tool = window.serial_tools_dialog
    raw = b"a" * 100000
    window.packet_store.append("RX", raw)
    tool.refresh_packets()
    tool.packet_table.selectRow(0)
    tool.show_packet()
    text = tool.packet_detail.toPlainText()
    assert "65536" in text and "100000" in text
    assert len(text) < 400000
    assert tool._selected_bytes() == raw


def test_live_preview_uses_private_parser_and_already_open_rx_only(window, qtbot):
    window.open_serial_tools()
    tool = window.serial_tools_dialog
    tool.generate()
    raw = tool._payload
    window._monitor_connected = True
    before = window.sample_store.statistics.parsed
    tool.preview_live()
    QTimer.singleShot(30, lambda: window._handle_monitor_raw(raw))
    qtbot.waitUntil(lambda: '"frames"' in tool.protocol_result.toPlainText(), timeout=2000)
    result = json.loads(tool.protocol_result.toPlainText())
    assert len(result["frames"]) == 1 and not result["truncated"]
    assert window.sample_store.statistics.parsed == before
    assert window._monitor_worker is None and window._tx_bytes == 0


def test_preset_load_is_inert_and_search_can_close_during_regex(window, qtbot):
    window.apply_serial_preset(SerialPreset("p1", "测试", "MISSING", tx_encoding="gbk"))
    assert window._tx_encoding == "gbk" and window.monitor_port_combo.currentData() is None
    assert not window.monitor_risk.isChecked() and window._monitor_worker is None
    window.packet_store.append("RX", b"a" * 30000 + b"!")
    window.open_serial_tools()
    tool = window.serial_tools_dialog
    tool.query_edit.setText(r"(a+)+$")
    tool.search_mode.setCurrentIndex(tool.search_mode.findData("regex"))
    tool.start_search()
    qtbot.wait(80)
    tool.close()
    qtbot.waitUntil(lambda: tool._search is None, timeout=3500)
    assert not tool.isVisible() and window._monitor_worker is None


def test_port_filter_changes_only_visible_choices_not_selection(window, monkeypatch):
    from qx_stc_isp.ports import PortDescriptor
    monkeypatch.setattr("qx_stc_isp.gui.enumerate_ports", lambda: [
        PortDescriptor("COM91", "CH340", 0x1a86, 0x7523, None, "FAKE", True),
        PortDescriptor("COM92", "DAPLink", 0x0d28, 0x0204, None, "FAKE", False)])
    window.refresh_ports()
    combo = window.monitor_port_combo
    combo.setCurrentIndex(combo.findData("COM91"))
    window.monitor_port_search.setText("0D28")
    assert combo.view().isRowHidden(combo.findData("COM91"))
    assert not combo.view().isRowHidden(combo.findData("COM92"))
    assert combo.currentData() == "COM91" and window._monitor_worker is None
