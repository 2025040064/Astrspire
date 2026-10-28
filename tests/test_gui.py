from __future__ import annotations

import pytest

from qx_stc_isp.gui import MainWindow, _MonitorWorker, _sample_hex_path
from qx_stc_isp.port_lease import PORT_LEASES
from qx_stc_isp.session import SessionState
from qx_stc_isp.vofa import TelemetryEvent, WorkspaceSettings, save_workspace


@pytest.fixture
def window(qtbot, tmp_path):
    result = MainWindow(log_dir=tmp_path)
    result.simulation_action.setChecked(True)
    qtbot.addWidget(result)
    result.show()
    yield result
    result.close()


def test_simulator_preview_and_real_mode_gate(window, qtbot, monkeypatch) -> None:
    assert window.image is not None
    assert window.image.data_size == 21
    assert window.start_button.isEnabled()

    def forbidden_open(*args, **kwargs):
        raise AssertionError("real serial port must not be opened")

    monkeypatch.setattr("qx_stc_isp.transport.PySerialTransport.open", forbidden_open)
    window.mode_combo.setCurrentIndex(1)
    assert not window.start_button.isEnabled()
    window.start_simulation()
    assert not window._active
    window.mode_combo.setCurrentIndex(0)
    window.start_button.click()
    qtbot.waitUntil(lambda: window.session is not None and window.session.state == SessionState.WAIT_POWER)
    assert window.power_button.isEnabled()
    window.power_button.click()
    qtbot.waitUntil(lambda: not window._active, timeout=5000)
    assert window.progress_bar.value() == 100
    assert "模拟下载完成" in window.log_view.toPlainText()
    assert "TX" in window.log_view.toPlainText()
    assert "RX" in window.log_view.toPlainText()


def test_cancel_during_wait_power(window, qtbot) -> None:
    window.start_button.click()
    qtbot.waitUntil(lambda: window.session is not None and window.session.state == SessionState.WAIT_POWER)
    window.cancel_button.click()
    qtbot.waitUntil(lambda: not window._active, timeout=3000)
    assert "取消" in window.log_view.toPlainText()


def test_invalid_hex_disables_start(window, tmp_path) -> None:
    invalid = tmp_path / "invalid.hex"
    invalid.write_text(":00000001FE\n", encoding="ascii")
    window.load_hex(invalid)
    assert window.image is None
    assert not window.start_button.isEnabled()


def test_chip_catalog_is_searchable_but_never_unlocks_real_isp(window) -> None:
    assert window.chip_combo.count() == len(window.catalog.entries)
    assert window.chip_combo.isEditable()
    window.mode_combo.setCurrentIndex(1)
    assert not window.start_button.isEnabled()
    window.chip_combo.setEditText("此型号不在证据目录")
    assert window._selected_chip() is None
    window.start_simulation()
    assert not window._active


def test_monitor_requires_port_baud_and_dtr_acknowledgement(window, monkeypatch) -> None:
    def forbidden_open(*_args, **_kwargs):
        raise AssertionError("no serial open without explicit connect prerequisites")

    monkeypatch.setattr("qx_stc_isp.transport.PySerialTransport.open", forbidden_open)
    window.monitor_port_combo.addItem("COM99 · 测试", "COM99")
    window.monitor_port_combo.setCurrentIndex(window.monitor_port_combo.findData("COM99"))
    assert not window.monitor_connect_button.isEnabled()
    window.start_monitor()
    assert not window._monitor_active
    window.monitor_baud_edit.setText("115200")
    assert not window.monitor_connect_button.isEnabled()
    window.start_monitor()
    assert not window._monitor_active


def test_monitor_refuses_existing_port_owner(window) -> None:
    window.monitor_port_combo.addItem("COM99 · 测试", "COM99")
    window.monitor_port_combo.setCurrentIndex(window.monitor_port_combo.findData("COM99"))
    window.monitor_baud_edit.setText("115200")
    window.monitor_risk.setChecked(True)
    with PORT_LEASES.acquire("COM99", owner="isp"):
        window.start_monitor()
        assert not window._monitor_active
        assert "占用" in window.monitor_status_label.text()


def test_monitor_samples_and_explicit_csv_export(window, tmp_path, monkeypatch) -> None:
    window._handle_monitor_events(
        [TelemetryEvent(raw=b"1,2\n", samples=(1.0, 2.0), text="1,2")]
    )
    assert len(window._samples) == 1
    assert len(window.waveform._series) == 2
    assert window.monitor_export_button.isEnabled()
    path = tmp_path / "samples.csv"
    monkeypatch.setattr(
        "qx_stc_isp.gui.QFileDialog.getSaveFileName",
        lambda *_args, **_kwargs: (str(path), "CSV (*.csv)"),
    )
    window.export_monitor_csv()
    assert "channel_index" in path.read_text(encoding="utf-8-sig")


def test_workspace_import_does_not_connect(window, tmp_path, monkeypatch) -> None:
    path = tmp_path / "workspace.json"
    save_workspace(path, WorkspaceSettings(port="COM88", baudrate=None, decoder="RawData"))
    monkeypatch.setattr(
        "qx_stc_isp.gui.QFileDialog.getOpenFileName",
        lambda *_args, **_kwargs: (str(path), "JSON (*.json)"),
    )

    def forbidden_open(*_args, **_kwargs):
        raise AssertionError("loading a workspace must not open a serial device")

    monkeypatch.setattr("qx_stc_isp.transport.PySerialTransport.open", forbidden_open)
    window.load_monitor_workspace()
    assert window.decoder_combo.currentText() == "RawData"
    assert window.monitor_baud_edit.text() == ""
    assert not window._monitor_active
    assert not window.monitor_connect_button.isEnabled()


def test_synthetic_demo_never_opens_a_com_port(window, qtbot, monkeypatch) -> None:
    def forbidden_open(*_args, **_kwargs):
        raise AssertionError("synthetic demo must never open a serial port")

    monkeypatch.setattr("qx_stc_isp.transport.PySerialTransport.open", forbidden_open)
    window.source_combo.setCurrentIndex(1)
    assert window.monitor_connect_button.isEnabled()
    window.monitor_connect_button.click()
    qtbot.waitUntil(lambda: len(window._samples) >= 1, timeout=2000)
    assert window._demo_active
    assert not window._monitor_active
    window.monitor_disconnect_button.click()
    assert not window._demo_active


def test_monitor_worker_reads_only_and_releases_lease(monkeypatch) -> None:
    worker = _MonitorWorker("COM98", 115200, "FireWater")
    decoded = []
    worker.events.connect(lambda batch: decoded.extend(batch))

    class FakeTransport:
        def __init__(self) -> None:
            self.opened = False
            self.closed = False
            self.reads = 0

        def open(self, _baudrate: int) -> None:
            self.opened = True

        def read_some(self, _max_bytes: int, *, timeout: float) -> bytes:
            self.reads += 1
            if self.reads == 1:
                return b"1,2\n"
            worker.stop()
            return b""

        def write(self, *_args, **_kwargs) -> None:
            raise AssertionError("monitor worker must not transmit")

        def cancel(self) -> None:
            pass

        def close(self) -> None:
            self.closed = True

    fake = FakeTransport()
    monkeypatch.setattr("qx_stc_isp.gui.PySerialTransport", lambda _device: fake)
    worker.run()
    assert fake.opened and fake.closed
    assert fake.reads >= 1
    assert PORT_LEASES.owner_for("COM98") is None
    assert any(item.samples == (1.0, 2.0) for item in decoded)


def test_monitor_log_and_visible_sample_lines_are_bounded(window, monkeypatch) -> None:
    monkeypatch.setattr("qx_stc_isp.gui._MAX_MONITOR_LOG_BYTES", 256)
    window._append_monitor_log("X" * 100)
    window._append_monitor_log("Y" * 100)
    assert not window._monitor_log_limit_reached
    assert "持久化上限" not in window.monitor_log_view.toPlainText()

    window._handle_monitor_events(
        [TelemetryEvent(raw=b"1\n", samples=(1.0,)) for _ in range(20)]
    )
    assert len(window._samples) == 20
    assert "另有 8 条采样" in window.monitor_log_view.toPlainText()


def test_monitor_controls_scroll_instead_of_compressing_at_minimum_size(window, qtbot) -> None:
    window.resize(window.minimumSize())
    window.tabs.setCurrentIndex(1)
    qtbot.wait(50)
    assert window.monitor_side_scroll.widget().width() <= window.monitor_side_scroll.viewport().width()
    assert window.monitor_baud_edit.height() >= window.monitor_baud_edit.minimumSizeHint().height()
    assert window.monitor_connect_button.height() >= window.monitor_connect_button.minimumSizeHint().height()


def test_light_workspace_navigation_and_value_panel(window) -> None:
    assert window.isp_nav_button.isChecked()
    window.monitor_nav_button.click()
    assert window.tabs.currentIndex() == 1
    assert window.monitor_nav_button.isChecked()
    assert len(window.channel_value_labels) == 16
    window._handle_monitor_events([TelemetryEvent(raw=b"1,2\n", samples=(1.0, 2.0), text="1,2")])
    assert "1" in window.channel_value_labels[0].text()
    window.isp_nav_button.click()
    assert window.tabs.currentIndex() == 0


def test_bundled_sample_path_uses_pyinstaller_root(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("qx_stc_isp.gui.sys._MEIPASS", str(tmp_path), raising=False)
    assert _sample_hex_path() == tmp_path / "examples" / "led.hex"


def test_official_replay_updates_gui_without_serial_access(window, qtbot, monkeypatch) -> None:
    from pathlib import Path
    from test_official_replay import fixture_capture

    monkeypatch.setattr("qx_stc_isp.gui.OfficialCapture.from_file", lambda *_args: fixture_capture())
    monkeypatch.setattr("serial.Serial", lambda *_a, **_k: pytest.fail("offline replay opened hardware"))
    window.mode_combo.setCurrentIndex(1)
    window.start_official_replay(Path("test-fixture.jsonl"))
    qtbot.waitUntil(lambda: not window._active, timeout=5000)
    assert "官方会话离线回放完成" in window.log_view.toPlainText()
    assert window.progress_bar.value() == 100
    assert not window.start_button.isEnabled()
    assert not window.power_button.isEnabled()


def test_bad_official_capture_does_not_start_thread(window, tmp_path) -> None:
    path = tmp_path / "bad.jsonl"
    path.write_text('{}\n', encoding="utf-8")
    window.start_official_replay(path)
    assert not window._active
    assert "预检查失败" in window.status_label.text()
