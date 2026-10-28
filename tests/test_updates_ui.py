"""The update entry is reachable from the existing tools menu without layout changes."""

def test_main_window_exposes_update_entry(qtbot, tmp_path):
    from qx_stc_isp.gui import MainWindow

    window = MainWindow(log_dir=tmp_path / "logs")
    qtbot.addWidget(window)
    labels = [action.text() for action in window.tools_menu.actions()]
    assert "检查更新…" in labels
    assert window.update_service is not None
    assert window.update_service.snapshot == window.save_update_snapshot


def test_update_dialog_reports_unconfigured_without_network(qtbot, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QWidget

    import qx_stc_isp.updates.config as update_config
    from qx_stc_isp.updates.config import UpdatePaths
    from qx_stc_isp.updates.dialog import UpdateDialog
    from qx_stc_isp.updates.service import UpdateService

    monkeypatch.setattr(update_config, "TRUSTED_PUBLIC_KEY_B64", "")
    paths = UpdatePaths(tmp_path / "root", tmp_path / "app", tmp_path / "local")
    service = UpdateService(paths)
    host = QWidget()
    host.update_blocked_reason = lambda: None
    dialog = UpdateDialog(service, host)
    qtbot.waitUntil(lambda: "未配置" in dialog.status.text(), timeout=5000)
    qtbot.waitUntil(service.idle, timeout=5000)
    assert not dialog.apply_now_button.isEnabled()


def test_snapshot_and_restore_roundtrip_without_connecting(qtbot, tmp_path, monkeypatch):
    from qx_stc_isp.gui import MainWindow

    monkeypatch.setenv("ASTRSPIRE_APPDATA", str(tmp_path / "app"))
    monkeypatch.setenv("ASTRSPIRE_LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("ASTRSPIRE_INSTALL_ROOT", str(tmp_path / "root"))
    window = MainWindow(log_dir=tmp_path / "logs")
    qtbot.addWidget(window)
    window.send_history.record("before-update", "text", "none", "utf-8")
    assert window.save_update_snapshot()
    recovery = tmp_path / "app" / "recovery"
    assert (recovery / "workspace.json").is_file()
    assert (recovery / "send-history.json").is_file()
    window.send_history.clear()
    window.restore_update_snapshot()
    assert window.send_history.entries[0]["text"] == "before-update"
    assert window._monitor_worker is None


