"""Serial assistant preset and line settings must never access a device."""

import pytest

from qx_stc_isp.gui import MainWindow
from qx_stc_isp.vofa import WorkspaceSettings


@pytest.fixture
def window(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr("serial.Serial", lambda *a, **k: pytest.fail("No physical serial access"))
    monkeypatch.setattr("qx_stc_isp.gui.enumerate_ports", lambda: [])
    result = MainWindow(log_dir=tmp_path / "logs")
    qtbot.addWidget(result)
    result.tabs.setCurrentIndex(1)
    result.show()
    yield result
    result._monitor_active = False
    result._monitor_connected = False
    result._monitor_worker = None
    result.close()


def test_assistant_preset_is_explicit_and_never_connects(window):
    window.send_text_edit.setText("keep user text")
    window.serial_dtr_check.setChecked(True)
    window.serial_rts_check.setChecked(True)
    window.serial_assistant_button.click()
    assert window.source_combo.currentData() == "serial"
    assert window.monitor_baud_edit.text() == "115200"
    assert window.serial_bytesize_combo.currentData() == 8
    assert window.serial_parity_combo.currentData() == "N"
    assert window.serial_stopbits_combo.currentData() == 1
    assert window.decoder_combo.currentData() == "RawData"
    assert window.rx_display_combo.currentData() == "text"
    assert window.send_mode_combo.currentData() == "text"
    assert window.send_ending_combo.currentData() == "CRLF"
    assert not window.serial_dtr_check.isChecked()
    assert not window.serial_rts_check.isChecked()
    assert not window.monitor_risk.isChecked()
    assert not window.periodic_send_check.isChecked()
    assert window.send_text_edit.text() == "keep user text"
    assert window.monitor_port_combo.currentData() is None
    assert window._monitor_worker is None
    assert not window._monitor_active
    assert "未连接" in window.monitor_status_label.text()


def test_serial_settings_locked_while_running(window):
    window._monitor_active = True
    window._update_monitor_controls()
    assert not window.serial_dtr_check.isEnabled()
    assert not window.serial_rts_check.isEnabled()
    assert not window.serial_assistant_button.isEnabled()
    window.apply_serial_assistant_preset()
    assert window.monitor_baud_edit.text() != "115200"


def test_gui_passes_startup_lines_to_worker(window, monkeypatch):
    captured = []
    launched = []
    window.serial_assistant_button.click()
    window.monitor_port_combo.addItem("FAKE", "COM_FAKE")
    window.monitor_port_combo.setCurrentIndex(window.monitor_port_combo.findData("COM_FAKE"))
    window.monitor_risk.setChecked(True)
    window.serial_rts_check.setChecked(True)
    monkeypatch.setattr("qx_stc_isp.gui.SerialLinkWorker", lambda *a, **kw: captured.append(kw))
    monkeypatch.setattr(window, "_launch_monitor_worker", lambda *a: launched.append(a))
    window.start_monitor()
    assert len(captured) == 1 and len(launched) == 1
    assert captured[0]["dtr"] is False and captured[0]["rts"] is True


def test_loading_workspace_resets_lines_without_starting_link(window):
    window.serial_dtr_check.setChecked(True)
    window.serial_rts_check.setChecked(True)
    window.load_monitor_workspace(settings=WorkspaceSettings(baudrate=9600, decoder="JustFloat"))
    assert not window.serial_dtr_check.isChecked()
    assert not window.serial_rts_check.isChecked()
    assert window.monitor_baud_edit.text() == "9600"
    assert window.decoder_combo.currentData() == "JustFloat"
    assert window._monitor_worker is None


def test_text_preset_encodes_newline_once_and_counts_only_completed_bytes(window):
    class Link:
        def __init__(self):
            self.pending = []

        def stop(self):
            pass

        def clear_pending(self):
            self.pending.clear()

        def enqueue_send(self, payload):
            self.pending.append(payload)
            return len(self.pending)

    window.serial_assistant_button.click()
    link = Link()
    window._monitor_worker = link
    window._monitor_connected = True
    window._monitor_active = True
    window.send_text_edit.setText("blink 500")
    assert window.send_monitor_data()
    assert link.pending == [b"blink 500\r\n"]
    assert window._tx_bytes == 0 and window._rx_bytes == 0
    window._handle_monitor_transmitted(link.pending[0])
    assert window._tx_bytes == 11 and window._rx_bytes == 0
    window._handle_monitor_raw(b"Order ok;\r\n")
    assert window._rx_bytes == 11
    assert "Order ok;" in window.monitor_log_view.toPlainText()
