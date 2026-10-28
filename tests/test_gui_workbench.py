"""Offline workbench integration: UI actions must never reach real hardware."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest
from PySide6.QtCore import QObject, Signal

from qx_stc_isp.gui import MainWindow
from qx_stc_isp.port_lease import PORT_LEASES
from qx_stc_isp.session import SessionState
from qx_stc_isp.stc89_session import Stc89DownloadSession
from qx_stc_isp.transport import TransportError
from qx_stc_isp.vofa import TelemetryEvent, WorkspaceSettings, load_workspace, save_workspace


@pytest.fixture(autouse=True)
def no_real_serial(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("workbench tests must never open a physical serial port")

    monkeypatch.setattr("serial.Serial", forbidden)
    monkeypatch.setattr("qx_stc_isp.transport.PySerialTransport.open", forbidden)
    monkeypatch.setattr("qx_stc_isp.gui.enumerate_ports", lambda: [])


@pytest.fixture
def workbench(qtbot, tmp_path):
    window = MainWindow(log_dir=tmp_path / "logs")
    qtbot.addWidget(window)
    window.show()
    yield window
    window.close()


class QueuedWorker(QObject):
    """Only an explicit completion signal acknowledges bytes handed to driver."""

    transmitted = Signal(bytes)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pending: list[bytes] = []
        self.clear_count = 0
        self.stop_count = 0
        self.failure: Exception | None = None

    def enqueue_send(self, payload):
        if self.failure is not None:
            raise self.failure
        self.pending.append(bytes(payload))
        return len(self.pending)

    def clear_pending(self):
        self.pending.clear()
        self.clear_count += 1

    def stop(self):
        self.stop_count += 1


def attach_fake_link(window):
    worker = QueuedWorker(window)
    worker.transmitted.connect(window._handle_monitor_transmitted)
    window._monitor_worker = worker
    window._monitor_active = True
    window._handle_monitor_status("connected", "离线测试连接")
    return worker


def select_data(combo, value):
    index = combo.findData(value)
    assert index >= 0
    combo.setCurrentIndex(index)


def test_send_queues_without_counting_until_completion_signal(workbench):
    worker = attach_fake_link(workbench)
    workbench.send_text_edit.setText("测试")
    select_data(workbench.send_ending_combo, "CRLF")
    payload = "测试\r\n".encode("utf-8")

    assert workbench.send_monitor_data() is True
    assert worker.pending == [payload]
    assert workbench._tx_bytes == 0
    assert "待发送 1 包" in workbench.tx_status_label.text()
    assert "TX " not in workbench.monitor_log_view.toPlainText()

    worker.transmitted.emit(payload)
    assert workbench._tx_bytes == len(payload)
    assert "不代表设备已执行" in workbench.tx_status_label.text()
    assert f"TX {payload.hex(' ').upper()}" in workbench.monitor_log_view.toPlainText()


def test_receive_encoding_does_not_change_transmit_bytes(workbench):
    worker = attach_fake_link(workbench)
    workbench.set_receive_encoding("gbk")
    workbench.send_text_edit.setText("测试")
    assert workbench.send_monitor_data()
    assert worker.pending == [bytes.fromhex("E6 B5 8B E8 AF 95")]


def test_raw_packet_store_and_manual_history_use_real_completion(workbench):
    worker = attach_fake_link(workbench)
    workbench.send_text_edit.setText("hello")
    assert workbench.send_monitor_data()
    assert not workbench.send_history.entries
    worker.transmitted.emit(b"hello")
    assert workbench.send_history.entries[0]["text"] == "hello"
    workbench._handle_monitor_raw(b"rx")
    assert [packet.direction for packet in workbench.packet_store.snapshot()] == ["TX", "RX"]
    workbench.open_serial_tools()
    assert workbench.serial_tools_dialog.isVisible()
    assert not workbench.serial_tools_dialog.preview_button.isEnabled() or workbench._monitor_connected


def test_cancelled_queued_manual_send_does_not_poison_next_history(workbench):
    worker = attach_fake_link(workbench)
    workbench.send_text_edit.setText("old")
    assert workbench.send_monitor_data()
    workbench._toggle_periodic_send(False)
    workbench.send_text_edit.setText("new")
    assert workbench.send_monitor_data()
    worker.transmitted.emit(b"new")
    assert [entry["text"] for entry in workbench.send_history.entries] == ["new"]


def test_send_refuses_disconnected_demo_and_invalid_payload(workbench):
    worker = attach_fake_link(workbench)
    workbench.send_text_edit.setText("01 03 FF")
    select_data(workbench.send_mode_combo, "hex")
    workbench._monitor_connected = False
    workbench._update_monitor_controls()
    assert not workbench.send_button.isEnabled()
    assert workbench.send_monitor_data() is False
    assert not worker.pending

    workbench._monitor_connected = True
    workbench._demo_active = True
    assert workbench.send_monitor_data() is False
    assert not worker.pending
    workbench._demo_active = False
    workbench.send_text_edit.setText("0G")
    assert workbench.send_monitor_data() is False
    assert not worker.pending
    assert "发送失败" in workbench.tx_status_label.text()


def test_periodic_send_is_opt_in_and_stop_discards_queued_data(workbench):
    assert not workbench.periodic_send_check.isChecked()
    assert not workbench._periodic_timer.isActive()
    worker = attach_fake_link(workbench)
    assert not workbench.periodic_send_check.isChecked()
    assert not workbench._periodic_timer.isActive()
    workbench.send_text_edit.setText("query")
    workbench.send_interval_spin.setValue(60000)
    workbench.periodic_send_check.setChecked(True)
    assert workbench._periodic_timer.isActive()
    assert not worker.pending  # Checking does not immediately send.
    workbench._send_periodic()
    assert worker.pending == [b"query"]

    workbench.stop_monitor()
    assert not workbench.periodic_send_check.isChecked()
    assert not workbench._periodic_timer.isActive()
    assert worker.clear_count >= 1
    assert not worker.pending
    assert worker.stop_count == 1
    assert not workbench._monitor_connected
    workbench._send_periodic()
    assert not worker.pending


def test_failed_enqueue_disables_periodic_without_tx_increment(workbench):
    worker = attach_fake_link(workbench)
    workbench.send_text_edit.setText("status")
    workbench.send_interval_spin.setValue(60000)
    workbench.periodic_send_check.setChecked(True)
    worker.failure = TransportError("offline queue failure")
    workbench._send_periodic()
    assert not workbench.periodic_send_check.isChecked()
    assert not workbench._periodic_timer.isActive()
    assert workbench._tx_bytes == 0
    assert "offline queue failure" in workbench.tx_status_label.text()


def test_workspace_schema2_roundtrip_restores_controls_without_connection(workbench, tmp_path, monkeypatch):
    path = tmp_path / "workspace.json"
    visible = tuple(index % 3 != 1 for index in range(16))
    settings = WorkspaceSettings(
        port="COM88", baudrate=460800, decoder="JustFloat",
        channel_names=("温度", "压力"), visible_channels=visible,
        sample_interval_ms=2.125, buffer_samples=3200,
        tx_mode="hex", tx_ending="CRLF", tx_text="01 03 FF", tx_interval_ms=250,
    )
    save_workspace(path, settings)
    assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == 8
    monkeypatch.setattr("qx_stc_isp.gui.QFileDialog.getOpenFileName", lambda *_a, **_k: (str(path), "JSON (*.json)"))
    workbench.waveform.set_paused(True)
    workbench.load_monitor_workspace()

    assert not workbench._monitor_active
    assert not workbench._demo_active
    assert not workbench._monitor_connected
    assert workbench._monitor_worker is None
    assert not workbench.periodic_send_check.isChecked()
    assert not workbench._periodic_timer.isActive()
    assert not workbench.send_button.isEnabled()
    assert workbench.monitor_baud_edit.text() == "460800"
    assert workbench.decoder_combo.currentText() == "JustFloat"
    assert workbench.channel_names_edit.text() == "温度,压力"
    assert workbench.waveform.channel_visibility == visible
    assert tuple(check.isChecked() for check in workbench.channel_visibility_checks) == visible
    assert workbench.waveform.sample_interval_ms == 2.125
    assert workbench.waveform.max_samples == 3200
    assert not workbench.waveform.paused
    assert workbench.send_mode_combo.currentData() == "hex"
    assert workbench.send_ending_combo.currentData() == "CRLF"
    assert workbench.send_text_edit.text() == "01 03 FF"
    assert workbench.send_interval_spin.value() == 250
    assert "COM88" in workbench.monitor_status_label.text()
    assert "未自动连接" in workbench.monitor_status_label.text()

    saved = tmp_path / "saved.json"
    monkeypatch.setattr("qx_stc_isp.gui.QFileDialog.getSaveFileName", lambda *_a, **_k: (str(saved), "JSON (*.json)"))
    workbench.save_monitor_workspace()
    restored = load_workspace(saved)
    # A missing COM is deliberately not selected when loading a workspace.
    assert restored.port == ""
    assert restored.visible_channels == settings.visible_channels
    assert restored.sample_interval_ms == settings.sample_interval_ms
    assert restored.buffer_samples == settings.buffer_samples
    assert restored.tx_mode == settings.tx_mode
    assert restored.tx_ending == settings.tx_ending
    assert restored.tx_text == settings.tx_text
    assert restored.tx_interval_ms == settings.tx_interval_ms


@pytest.mark.parametrize("finish", ["stop", "close"])
def test_live_csv_flushes_and_closes_on_stop_or_window_close(workbench, tmp_path, monkeypatch, finish):
    path = tmp_path / f"live-{finish}.csv"
    monkeypatch.setattr("qx_stc_isp.gui.QFileDialog.getSaveFileName", lambda *_a, **_k: (str(path), "CSV (*.csv)"))
    workbench._demo_active = True  # No demo timer, connection or real source.
    workbench.channel_names_edit.setText("温度,压力")
    workbench.toggle_sample_recording()
    recorder = workbench._sample_recorder
    assert recorder is not None and recorder.is_open
    assert workbench._record_flush_timer.isActive()
    workbench._handle_monitor_events([
        TelemetryEvent(raw=b"1,2\n", samples=(1.0, 2.0)),
        TelemetryEvent(raw=b"3,4\n", samples=(3.0, 4.0)),
        TelemetryEvent(raw=b"bad", error="offline malformed sample"),
    ])
    assert workbench._recorded_rows == 4
    if finish == "stop":
        workbench.stop_monitor()
    else:
        assert workbench.close()
    assert workbench._sample_recorder is None
    assert not recorder.is_open
    assert recorder.wait_closed(2)
    workbench._poll_recorders()
    assert not workbench._record_flush_timer.isActive()
    with path.open(newline="", encoding="utf-8-sig") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 4
    assert [row["channel_name"] for row in rows] == ["温度", "压力", "温度", "压力"]
    assert [float(row["value"]) for row in rows] == [1, 2, 3, 4]
    assert all(row["timestamp_utc"].endswith("+00:00") for row in rows)
    assert "已保存 2 帧" in workbench.record_status_label.text()
    assert "未保存 0 帧" in workbench.record_status_label.text()


def test_live_csv_requires_explicit_destination_and_never_overwrites(workbench, tmp_path, monkeypatch):
    path = tmp_path / "existing.csv"
    path.write_text("keep existing data", encoding="utf-8")
    monkeypatch.setattr("qx_stc_isp.gui.QFileDialog.getSaveFileName", lambda *_a, **_k: (str(path), "CSV (*.csv)"))
    notices = []
    monkeypatch.setattr("qx_stc_isp.gui.QMessageBox.warning", lambda *_args: notices.append(_args))
    workbench.toggle_sample_recording()
    assert workbench._sample_recorder is None  # Not connected / no demo.
    workbench._demo_active = True
    workbench.toggle_sample_recording()
    assert workbench._sample_recorder is None
    assert not workbench._record_flush_timer.isActive()
    assert path.read_text(encoding="utf-8") == "keep existing data"
    assert notices


def test_binary_firmware_uses_explicit_hex_base_without_hex_checksum_claim(workbench, tmp_path):
    path = tmp_path / "program.bin"
    path.write_bytes(b"\x02\x00\xff\x80")
    workbench.bin_base_edit.setText("0x0100")
    workbench.load_hex(path)
    assert workbench.image is not None
    assert workbench.image.segments == ((0x100, b"\x02\x00\xff\x80"),)
    assert workbench.image.data_size == 4
    assert workbench.image.min_address == 0x100
    assert workbench.image.max_address == 0x103
    assert workbench.image_path == path.resolve()
    assert "0x0100–0x0103" in workbench.hex_summary.text()
    assert "没有 Intel HEX 行校验" in workbench.status_label.text()
    assert "HEX 行校验通过" not in workbench.status_label.text()
    workbench.mode_combo.setCurrentIndex(1)
    assert not workbench.start_button.isEnabled()


@pytest.mark.parametrize("base", ["nonsense", "0x1FFF"])
def test_binary_invalid_base_or_range_clears_loaded_image(workbench, tmp_path, base):
    path = tmp_path / "invalid.bin"
    path.write_bytes(b"\x00\x01")
    workbench.bin_base_edit.setText(base)
    workbench.load_hex(path)
    assert workbench.image is None
    assert "固件解析失败" in workbench.status_label.text()
    assert not workbench.start_button.isEnabled()


class ReadOnlyStatusTransport:
    """Supply only the first status from a sanitized official-derived fixture."""

    def __init__(self, device):
        fixture = Path(__file__).parent / "fixtures" / "stc89_f002_66c_redacted.json"
        document = json.loads(fixture.read_text(encoding="utf-8"))
        assert document["redacted"] is True
        first = document["frames"][0]
        assert first["direction"] == "RX"
        self.pending = bytearray.fromhex(first["hex"])
        self.device = device
        self.is_open = False
        self.writes = []
        self.settings = []
        self.close_count = 0
        self.lease_seen = None

    def open(self, baudrate, **settings):
        self.lease_seen = PORT_LEASES.owner_for(self.device)
        self.is_open = True
        self.settings.append((baudrate, settings))

    def write(self, payload, timeout=5):
        assert payload == b"\x7f", "only read-only synchronization may be sent"
        self.writes.append(bytes(payload))

    def read_some(self, max_bytes, timeout):
        assert self.writes
        size = min(5, max_bytes, len(self.pending))
        chunk = bytes(self.pending[:size])
        del self.pending[:size]
        return chunk

    def cancel(self):
        pass

    def close(self):
        self.is_open = False
        self.close_count += 1


def select_readonly_device(window, device="COM91"):
    window.mode_combo.setCurrentIndex(1)
    window.port_combo.addItem(f"{device} · 离线测试", device)
    select_data(window.port_combo, device)


def test_readonly_identification_only_syncs_then_closes_and_releases_lease(workbench, monkeypatch, qtbot):
    fake = ReadOnlyStatusTransport("COM91")
    monkeypatch.setattr("qx_stc_isp.gui.PySerialTransport", lambda device: fake)
    select_readonly_device(workbench)
    assert workbench.identify_button.isEnabled()
    workbench.start_readonly_identification()
    session = workbench.session
    qtbot.waitUntil(lambda: not workbench._active, timeout=3000)
    assert session.state == SessionState.COMPLETE
    assert session.status.chip_id == 0xF002
    assert fake.writes == [b"\x7f"]
    assert fake.settings == [(2400, {"bytesize": 8, "parity": "N", "stopbits": 1})]
    assert fake.close_count == 1
    assert not fake.is_open
    assert fake.lease_seen == "stc-isp"
    assert PORT_LEASES.owner_for("COM91") is None
    assert "未擦除、未写入" in workbench.status_label.text()


def test_readonly_identification_refuses_monitor_owned_port(workbench):
    select_readonly_device(workbench)
    with PORT_LEASES.acquire("COM91", owner="serial-monitor"):
        workbench._update_controls()
        workbench.start_readonly_identification()
        assert not workbench._active
        assert "serial-monitor" in workbench.status_label.text()
        assert "占用" in workbench.status_label.text()
        assert not workbench.start_button.isEnabled()


def select_real_write_device(window, device="COM91"):
    window.mode_combo.setCurrentIndex(1)
    window.port_combo.addItem(f"{device} · 离线测试", device)
    select_data(window.port_combo, device)
    index = window.chip_combo.findText("STC89C52RC/LE52RC")
    assert index >= 0
    window.chip_combo.setCurrentIndex(index)
    window._update_controls()


def test_real_write_gate_requires_known_chip(workbench):
    workbench.mode_combo.setCurrentIndex(1)
    workbench.port_combo.addItem("COM91 · 离线测试", "COM91")
    select_data(workbench.port_combo, "COM91")
    workbench.chip_combo.setCurrentIndex(workbench.chip_combo.findText("STC89C52RC/LE52RC"))
    assert workbench.start_button.isEnabled()
    workbench.chip_combo.setEditText("未注册的型号")
    workbench._update_controls()
    assert not workbench.start_button.isEnabled()


def test_real_write_one_click_sets_erase_gate(workbench, monkeypatch):
    select_real_write_device(workbench)
    captured = []
    monkeypatch.setattr(
        workbench, "_launch_session",
        lambda session, device=None: captured.append((session, device)),
    )
    assert workbench.start_button.isEnabled()
    workbench.start_button.click()
    assert len(captured) == 1
    session, device = captured[0]
    assert isinstance(session, Stc89DownloadSession)
    assert session.allow_erase is True
    assert device == "COM91"
    assert "COM91" in workbench.log_view.toPlainText()


def test_erase_only_ui_needs_no_image_and_full_erase_is_one_shot(workbench, monkeypatch):
    from qx_stc_isp.stc89_session import Stc89EraseSession
    select_real_write_device(workbench)
    captured = []
    monkeypatch.setattr(workbench, "_launch_session",
                        lambda session, device=None: captured.append(session))
    workbench.full_erase_check.setChecked(True)
    workbench.start_real_download()
    assert captured[-1].erase_full_program
    assert not workbench.full_erase_check.isChecked()
    workbench.image = None
    workbench._update_controls()
    assert workbench.erase_button.isEnabled()
    workbench.erase_button.click()
    assert type(captured[-1]) is Stc89EraseSession
    workbench.session = captured[-1]
    workbench._set_phase(SessionState.ERASING)
    assert workbench.phase_labels[SessionState.PROGRAMMING].text() == "未执行"


def test_real_write_refuses_monitor_owned_port(workbench):
    select_real_write_device(workbench)
    with PORT_LEASES.acquire("COM91", owner="serial-monitor"):
        workbench.start_real_download()
        assert not workbench._active
        assert "serial-monitor" in workbench.status_label.text()
        assert "占用" in workbench.status_label.text()
