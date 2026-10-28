import json

import pytest

from qx_stc_isp.vofa.workspace import WorkspaceSettings, load_workspace, save_workspace


def test_schema_seven_roundtrip_does_not_read_external_manifest(tmp_path):
    settings = WorkspaceSettings(source="swd", swd={"adapter": "stlink", "probe_serial": "TEST123",
        "target": "stm32f1x", "manifest_path": "missing-external-file.json", "poll_ms": 50})
    path = tmp_path / "workspace.json"
    save_workspace(path, settings)
    assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == 8
    assert load_workspace(path) == settings


def test_swd_gui_selection_does_not_connect_and_disables_send(qtbot, tmp_path, monkeypatch):
    from qx_stc_isp.gui import MainWindow
    monkeypatch.setattr("qx_stc_isp.gui.enumerate_ports", lambda: [])
    monkeypatch.setattr("serial.Serial", lambda *a, **k: pytest.fail("No serial connection"))
    window = MainWindow(log_dir=tmp_path)
    qtbot.addWidget(window)
    window.source_combo.setCurrentIndex(window.source_combo.findData("swd"))
    assert window.source_combo.currentData() == "swd"
    assert not window.send_button.isEnabled()
    assert not window.monitor_connect_button.isEnabled()
    assert window._monitor_worker is None
    window.close()
