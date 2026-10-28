"""GUI source integration: loopback sockets/files only, never physical COM."""

from __future__ import annotations

import socket
import csv
import time
from datetime import datetime, timedelta, timezone

import pytest

from qx_stc_isp.gui import MainWindow
from qx_stc_isp.vofa import CsvRecorder, TelemetryEvent, WorkspaceSettings, save_workspace
from qx_stc_isp.vofa.playback import CSV_HEADER


@pytest.fixture(autouse=True)
def no_real_serial(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("source GUI tests must never open physical serial ports")

    monkeypatch.setattr("serial.Serial", forbidden)
    monkeypatch.setattr("qx_stc_isp.transport.PySerialTransport.open", forbidden)
    monkeypatch.setattr("qx_stc_isp.gui.enumerate_ports", lambda: [])


@pytest.fixture
def source_window(qtbot, tmp_path):
    window = MainWindow(log_dir=tmp_path / "logs")
    qtbot.addWidget(window)
    window.tabs.setCurrentIndex(1)
    window.show()
    yield window
    window.stop_monitor()
    qtbot.waitUntil(lambda: not window._monitor_active, timeout=2000)
    window.close()


def select_source(window, source):
    index = window.source_combo.findData(source)
    assert index >= 0
    window.source_combo.setCurrentIndex(index)


def reserve_port(kind=socket.SOCK_STREAM):
    with socket.socket(socket.AF_INET, kind) as handle:
        handle.bind(("127.0.0.1", 0))
        return handle.getsockname()[1]


def configure_network(window, kind, port):
    select_source(window, kind)
    window.network_host_edit.setText("127.0.0.1")
    window.network_port_edit.setText(str(port))
    assert window.monitor_connect_button.isEnabled()


def test_uart_waiting_and_format_warning_keep_connection_active(source_window):
    window = source_window
    window._monitor_active = True
    try:
        window._handle_monitor_status("connected", "COM_FAKE port open")
        for kind in ("waiting_data", "unparsed_data", "receiving"):
            window._handle_monitor_status(kind, kind)
            assert window._monitor_connected
            assert window._monitor_error is None
            assert window.monitor_status_label.text() == kind
    finally:
        window._monitor_active = False
        window._monitor_connected = False


def make_recording(path, frames):
    start = datetime(2026, 10, 21, tzinfo=timezone.utc)
    with CsvRecorder(path, ("温度", "速度")) as recorder:
        for offset, values in frames:
            recorder.record(TelemetryEvent(raw=b"", samples=values), at=start + timedelta(seconds=offset))
    return path


def choose_csv(window, path, monkeypatch):
    select_source(window, "csv")
    monkeypatch.setattr("qx_stc_isp.gui.QFileDialog.getOpenFileName", lambda *_a, **_k: (str(path), "CSV (*.csv)"))
    window.playback_choose_button.click()
    assert window.playback_path_edit.text() == str(path)
    assert window.monitor_connect_button.isEnabled()


def test_all_six_sources_have_appropriate_visible_panels(source_window):
    window = source_window
    assert {window.source_combo.itemData(i) for i in range(window.source_combo.count())} == {
        "serial", "demo", "tcp_client", "tcp_server", "udp", "csv",
        "images", "stream", "matrix", "compat_demo", "swd",
    }
    for source in ("serial", "demo", "tcp_client", "tcp_server", "udp", "csv"):
        select_source(window, source)
        assert window.network_panel.isHidden() == (source not in {"tcp_client", "tcp_server", "udp"})
        assert window.playback_panel.isHidden() == (source != "csv")
        assert window.monitor_port_combo.isHidden() == (source != "serial")
        assert not window.send_button.isEnabled()
        assert not window.periodic_send_check.isEnabled()


@pytest.mark.parametrize("kind", ["tcp_client", "tcp_server", "udp"])
def test_network_configuration_requires_valid_address_and_port(source_window, kind):
    window = source_window
    select_source(window, kind)
    window.network_host_edit.setText("127.0.0.1")
    for bad in ("", "0", "65536", "nine", "1.5"):
        window.network_port_edit.setText(bad)
        assert not window.monitor_connect_button.isEnabled()
    window.network_port_edit.setText("23456")
    assert window.monitor_connect_button.isEnabled()
    window.network_host_edit.setText("not-a-literal-ip")
    assert not window.monitor_connect_button.isEnabled()
    assert not window._monitor_active


def test_udp_without_peer_receives_but_never_enables_send(source_window, qtbot):
    window = source_window
    configure_network(window, "udp", reserve_port(socket.SOCK_DGRAM))
    window.start_monitor()
    qtbot.waitUntil(lambda: window._monitor_connected, timeout=2000)
    worker = window._monitor_worker
    assert worker is not None and not worker.can_send
    assert not window.send_button.isEnabled()
    assert not window.periodic_send_check.isEnabled()
    window.send_text_edit.setText("not sent")
    assert window.send_monitor_data() is False
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
        sender.sendto(b"1,2\n", worker.bound_address)
    qtbot.waitUntil(lambda: len(window._samples) == 1, timeout=2000)
    assert window._samples[0][1] == (1.0, 2.0)
    assert not window.send_button.isEnabled()
    window.stop_monitor()
    qtbot.waitUntil(lambda: not window._monitor_active, timeout=2000)
    assert window._monitor_worker is None and window._monitor_thread is None


def test_udp_target_requires_both_fields(source_window):
    window = source_window
    configure_network(window, "udp", 23456)
    window.udp_peer_host_edit.setText("127.0.0.1")
    assert not window.monitor_connect_button.isEnabled()
    window.udp_peer_port_edit.setText("23457")
    assert window.monitor_connect_button.isEnabled()
    assert window._network_config().peer == ("127.0.0.1", 23457)
    window.udp_peer_host_edit.clear()
    assert not window.monitor_connect_button.isEnabled()


def test_tcp_listener_without_peer_has_no_send_capability(source_window, qtbot):
    window = source_window
    configure_network(window, "tcp_server", reserve_port())
    window.start_monitor()
    qtbot.waitUntil(lambda: window._monitor_worker is not None and window._monitor_worker.bound_address is not None, timeout=2000)
    qtbot.waitUntil(lambda: "监听" in window.monitor_status_label.text(), timeout=2000)
    assert window._monitor_active and not window._monitor_connected
    assert not window.send_button.isEnabled()
    assert not window.periodic_send_check.isEnabled()
    assert window.send_monitor_data() is False
    window.stop_monitor()
    qtbot.waitUntil(lambda: not window._monitor_active, timeout=2000)
    assert not window._periodic_timer.isActive()


def test_tcp_client_connection_receives_and_only_explicitly_sends(source_window, qtbot):
    window = source_window
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(1)
        configure_network(window, "tcp_client", listener.getsockname()[1])
        window.start_monitor()
        peer, _ = listener.accept()
        with peer:
            peer.settimeout(0.08)
            qtbot.waitUntil(lambda: window._monitor_connected, timeout=2000)
            with pytest.raises(socket.timeout):
                peer.recv(1)
            assert window.send_button.isEnabled()
            peer.sendall(b"3,4\n")
            qtbot.waitUntil(lambda: len(window._samples) == 1, timeout=2000)
            window.send_text_edit.setText("hello")
            window.send_button.click()
            peer.settimeout(1)
            assert peer.recv(5) == b"hello"
            qtbot.waitUntil(lambda: window._tx_bytes == 5, timeout=2000)
            assert not window.periodic_send_check.isChecked()
            window.stop_monitor()
            qtbot.waitUntil(lambda: not window._monitor_active, timeout=2000)


def test_csv_selection_and_completion_close_thread_and_file(source_window, qtbot, tmp_path, monkeypatch):
    window = source_window
    path = make_recording(tmp_path / "replay.csv", [(0, (1.0, 2.0)), (0, (3.0, 4.0)), (0.02, (5.0, 6.0))])
    choose_csv(window, path, monkeypatch)
    window.playback_speed_spin.setValue(20)
    window.start_monitor()
    assert not window.send_button.isEnabled()
    worker = window._monitor_worker
    enabled_states = []
    worker.status.connect(lambda *_a: enabled_states.append(window.send_button.isEnabled()))
    qtbot.waitUntil(lambda: not window._monitor_active, timeout=2000)
    assert [item[1] for item in window._samples] == [(1.0, 2.0), (3.0, 4.0), (5.0, 6.0)]
    assert window.playback_progress.value() == 100
    assert window._monitor_worker is None and window._monitor_thread is None
    assert not window._monitor_connected
    assert not window.send_button.isEnabled() and not any(enabled_states)
    assert not window.periodic_send_check.isChecked()
    assert "CSV 回放完成" in window.monitor_status_label.text()
    assert "回放采样" in window.monitor_log_view.toPlainText()
    assert "RX " not in window.monitor_log_view.toPlainText()
    path.rename(path.with_name("closed.csv"))


def test_csv_gui_pause_resume_and_cancel(source_window, qtbot, tmp_path, monkeypatch):
    window = source_window
    path = make_recording(tmp_path / "pause.csv", [(0, (1.0,)), (0.5, (2.0,)), (30, (3.0,))])
    choose_csv(window, path, monkeypatch)
    window.start_monitor()
    qtbot.waitUntil(lambda: len(window._samples) == 1, timeout=2000)
    window.playback_pause_button.click()
    assert window._monitor_worker.paused
    assert window.playback_pause_button.text() == "继续回放"
    qtbot.wait(200)
    assert len(window._samples) == 1
    window.playback_pause_button.click()
    assert not window._monitor_worker.paused
    qtbot.waitUntil(lambda: len(window._samples) == 2, timeout=2000)
    assert not window.send_button.isEnabled()
    window.stop_monitor()
    qtbot.waitUntil(lambda: not window._monitor_active, timeout=2000)
    assert len(window._samples) == 2
    assert not window.playback_pause_button.isChecked()
    assert not window.playback_pause_button.isEnabled()
    path.rename(path.with_name("cancelled.csv"))


def test_csv_bad_input_reports_error_not_completion(source_window, qtbot, tmp_path, monkeypatch):
    window = source_window
    path = tmp_path / "bad.csv"
    path.write_text("wrong,header\n", encoding="utf-8")
    choose_csv(window, path, monkeypatch)
    window.start_monitor()
    qtbot.waitUntil(lambda: not window._monitor_active, timeout=2000)
    assert window._monitor_error is not None
    assert "CSV 回放失败" in window.monitor_status_label.text()
    assert "CSV 回放完成" not in window.monitor_log_view.toPlainText()
    assert not window._samples and not window.send_button.isEnabled()


@pytest.mark.parametrize("source", ["udp", "tcp_server", "csv"])
def test_workspace_loading_never_starts_any_source(source_window, tmp_path, monkeypatch, source):
    window = source_window
    path = tmp_path / "workspace.json"
    save_workspace(path, WorkspaceSettings(port="COM99", baudrate=115200, tx_text="never sent"))
    select_source(window, source)

    def forbidden(*_a, **_k):
        pytest.fail("loading a workspace started a source")

    monkeypatch.setattr("qx_stc_isp.gui.NetworkLinkWorker.run", forbidden)
    monkeypatch.setattr("qx_stc_isp.gui.CsvReplayWorker.run", forbidden)
    monkeypatch.setattr("qx_stc_isp.gui.SerialLinkWorker.run", forbidden)
    monkeypatch.setattr("qx_stc_isp.gui.QFileDialog.getOpenFileName", lambda *_a, **_k: (str(path), "JSON (*.json)"))
    window.load_monitor_workspace()
    assert window._monitor_worker is None and window._monitor_thread is None
    assert not window._monitor_active and not window._demo_active
    assert not window._monitor_connected
    assert not window._periodic_timer.isActive() and not window.periodic_send_check.isChecked()
    assert not window.send_button.isEnabled()


def named_recording(path, frames):
    start = datetime(2026, 10, 21, tzinfo=timezone.utc)
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(CSV_HEADER)
        for offset, name, value in frames:
            writer.writerow(((start + timedelta(seconds=offset)).isoformat(), 0, name, value))
    return path


def capture_plot_names(window, monkeypatch):
    seen = []
    original = window.waveform.append_samples

    def append(samples, names):
        seen.append((tuple(samples), tuple(names)))
        return original(samples, names)

    monkeypatch.setattr(window.waveform, "append_samples", append)
    return seen


def test_csv_names_survive_completion_and_precision_refresh(source_window, qtbot, tmp_path, monkeypatch):
    window = source_window
    path = make_recording(tmp_path / "names.csv", [(0, (1.0, 2.0)), (0.01, (3.0, 4.0))])
    choose_csv(window, path, monkeypatch)
    seen = capture_plot_names(window, monkeypatch)
    window.start_monitor()
    qtbot.waitUntil(lambda: not window._monitor_active, timeout=2000)
    assert seen == [((1.0, 2.0), ("温度", "速度")), ((3.0, 4.0), ("温度", "速度"))]
    assert window._source_channel_names == ("温度", "速度")
    window.precision_spin.setValue(3)
    assert "温度" in window.channel_value_labels[0].text()
    assert "速度" in window.channel_value_labels[1].text()


def test_csv_user_channel_names_override_file_names(source_window, qtbot, tmp_path, monkeypatch):
    window = source_window
    path = make_recording(tmp_path / "user-names.csv", [(0, (1.0, 2.0))])
    choose_csv(window, path, monkeypatch)
    window.channel_names_edit.setText("用户一,用户二")
    seen = capture_plot_names(window, monkeypatch)
    window.start_monitor()
    qtbot.waitUntil(lambda: not window._monitor_active, timeout=2000)
    assert seen == [((1.0, 2.0), ("用户一", "用户二"))]
    assert "用户一" in window.channel_value_labels[0].text()


def test_csv_queued_name_signals_use_per_batch_snapshots(source_window, qtbot, tmp_path, monkeypatch):
    window = source_window
    path = named_recording(tmp_path / "changing-names.csv", [(0, "第一", 1.0), (0.08, "第二", 2.0), (0.16, "第三", 3.0)])
    choose_csv(window, path, monkeypatch)
    seen = capture_plot_names(window, monkeypatch)
    window.start_monitor()
    # Allow the producer to advance while deliberately leaving GUI signals
    # queued: a mutable worker property must not relabel an earlier batch.
    time.sleep(0.25)
    qtbot.waitUntil(lambda: not window._monitor_active, timeout=2000)
    assert seen == [((1.0,), ("第一",)), ((2.0,), ("第二",)), ((3.0,), ("第三",))]


def test_csv_same_timestamp_name_changes_do_not_share_wrong_batch_names(source_window, qtbot, tmp_path, monkeypatch):
    window = source_window
    path = named_recording(tmp_path / "same-time-names.csv", [(0, "第一", 1.0), (0, "第二", 2.0), (0, "第三", 3.0)])
    choose_csv(window, path, monkeypatch)
    seen = capture_plot_names(window, monkeypatch)
    window.start_monitor()
    qtbot.waitUntil(lambda: not window._monitor_active, timeout=2000)
    assert seen == [((1.0,), ("第一",)), ((2.0,), ("第二",)), ((3.0,), ("第三",))]


def test_starting_csv_starts_fresh_sampling_session(source_window, qtbot, tmp_path, monkeypatch):
    window = source_window
    window._handle_monitor_events([TelemetryEvent(raw=b"", samples=(999.0,))])
    path = make_recording(tmp_path / "fresh.csv", [(0, (1.0,))])
    choose_csv(window, path, monkeypatch)
    window.start_monitor()
    qtbot.waitUntil(lambda: not window._monitor_active, timeout=2000)
    assert [sample for _, sample in window._samples] == [(1.0,)]
    assert window._sample_index == 1
    qtbot.waitUntil(lambda: bool(window.waveform.displayed_series), timeout=2000)
    assert tuple(window.waveform.displayed_series[0]) == (1.0,)


def test_demo_after_csv_does_not_reuse_file_names_or_samples(source_window, qtbot, tmp_path, monkeypatch):
    window = source_window
    path = make_recording(tmp_path / "before-demo.csv", [(0, (99.0,))])
    choose_csv(window, path, monkeypatch)
    window.start_monitor()
    qtbot.waitUntil(lambda: not window._monitor_active, timeout=2000)
    select_source(window, "demo")
    window.start_monitor()
    qtbot.waitUntil(lambda: window._demo_step >= 1, timeout=2000)
    window.stop_monitor()
    assert window._source_channel_names == ()
    assert all(values != (99.0,) for _, values in window._samples)
    assert window._sample_index == window._demo_step
    assert "温度" not in window.channel_value_labels[0].text()


def test_failed_new_csv_does_not_retain_previous_full_progress(source_window, qtbot, tmp_path, monkeypatch):
    window = source_window
    good = make_recording(tmp_path / "good.csv", [(0, (1.0,))])
    choose_csv(window, good, monkeypatch)
    window.start_monitor()
    qtbot.waitUntil(lambda: not window._monitor_active, timeout=2000)
    assert window.playback_progress.value() == 100
    bad = tmp_path / "bad-after-good.csv"
    bad.write_text("invalid,header\n", encoding="utf-8")
    choose_csv(window, bad, monkeypatch)
    window.start_monitor()
    qtbot.waitUntil(lambda: not window._monitor_active, timeout=2000)
    assert window.playback_progress.value() < 100
    assert "CSV 回放失败" in window.monitor_status_label.text()
    assert window._monitor_error is not None


def test_closing_paused_csv_releases_thread_and_file(source_window, qtbot, tmp_path, monkeypatch):
    window = source_window
    path = make_recording(tmp_path / "close-paused.csv", [(0, (1.0,)), (30, (2.0,))])
    choose_csv(window, path, monkeypatch)
    window.start_monitor()
    qtbot.waitUntil(lambda: len(window._samples) == 1, timeout=2000)
    window.playback_pause_button.click()
    assert window._monitor_worker.paused
    assert window.close()
    qtbot.waitUntil(lambda: not window._monitor_active, timeout=2000)
    path.rename(path.with_name("closed-paused.csv"))
    assert window._monitor_worker is None and window._monitor_thread is None


def test_csv_recent_export_preserves_detected_channel_names(source_window, qtbot, tmp_path, monkeypatch):
    window = source_window
    path = make_recording(tmp_path / "named-input.csv", [(0, (1.0, 2.0))])
    choose_csv(window, path, monkeypatch)
    window.start_monitor()
    qtbot.waitUntil(lambda: not window._monitor_active, timeout=2000)
    exported = tmp_path / "named-export.csv"
    monkeypatch.setattr("qx_stc_isp.gui.QFileDialog.getSaveFileName", lambda *_a, **_k: (str(exported), "CSV (*.csv)"))
    window.export_monitor_csv()
    with exported.open("r", encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.reader(stream))
    assert [row[2] for row in rows[1:]] == ["温度", "速度"]
