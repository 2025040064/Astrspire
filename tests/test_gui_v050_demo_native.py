"""Real GUI + worker integration; only explicitly selected local fixtures."""
from __future__ import annotations

import json

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtGui import QImage, QColor
from PySide6.QtWidgets import QCheckBox, QDialog, QPlainTextEdit, QPushButton

from qx_stc_isp.gui import MainWindow


@pytest.fixture(autouse=True)
def no_device_or_network(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("local Demo/native preview must not open serial, sockets or transmit")
    monkeypatch.setattr("serial.Serial", forbidden)
    monkeypatch.setattr("qx_stc_isp.transport.PySerialTransport.open", forbidden)
    monkeypatch.setattr("socket.socket", forbidden)
    monkeypatch.setattr("qx_stc_isp.gui.enumerate_ports", lambda: [])


@pytest.fixture
def window(qtbot, tmp_path):
    result = MainWindow(log_dir=tmp_path / "logs")
    qtbot.addWidget(result)
    result.tabs.setCurrentIndex(1)
    result.resize(1280, 800)
    result.show()
    yield result
    result.stop_monitor()
    qtbot.waitUntil(lambda: not result._monitor_active, timeout=3000)
    result.close()


def configure_demo(window, tmp_path, *, loop=False, decoder="JustFloat"):
    csv, jpg = tmp_path / "csv", tmp_path / "jpg"
    csv.mkdir()
    jpg.mkdir()
    (csv / "2.csv").write_bytes(b"2\n3\n")
    (csv / "10.csv").write_bytes(b"10\n11\n")
    image = QImage(4, 4, QImage.Format.Format_RGB32)
    image.fill(QColor("red"))
    assert image.save(str(jpg / "1.jpg"), "JPG")
    window.source_combo.setCurrentIndex(window.source_combo.findData("compat_demo"))
    window.compat_decoder_combo.setCurrentIndex(window.compat_decoder_combo.findData(decoder))
    for kind, path in (("csv", csv), ("jpg", jpg)):
        enabled, directory, period, looping, *_ = window.compat_sources[kind]
        enabled.setChecked(True)
        directory.setText(str(path))
        period.setValue(10)
        looping.setChecked(loop)
    return csv, jpg


@pytest.mark.parametrize("decoder", ["FireWater", "JustFloat"])
def test_gui_parallel_demo_delivers_all_rows_and_image_without_external_io(window, qtbot, tmp_path, decoder):
    configure_demo(window, tmp_path, decoder=decoder)
    assert window.compat_demo_panel.isVisible()
    assert window.monitor_connect_button.isEnabled()
    window.monitor_connect_button.click()
    qtbot.waitUntil(lambda: not window._monitor_active, timeout=4000)
    assert [frame.values for frame in window.sample_store.snapshot()] == [(2.,), (3.,), (10.,), (11.,)]
    assert window._image_store.get(0) is not None
    assert window._monitor_worker is None and window._monitor_thread is None
    assert not window.send_button.isEnabled() and window._tx_bytes == 0
    assert not window._monitor_connected and not window._periodic_timer.isActive()


def test_gui_demo_csv_pause_resume_and_stop_leave_jpg_live(window, qtbot, tmp_path):
    configure_demo(window, tmp_path, loop=True)
    window.monitor_connect_button.click()
    qtbot.waitUntil(lambda: len(window.sample_store) >= 2 and window._image_store.get(0) is not None, timeout=3000)
    csv_pause, csv_stop = window.compat_sources["csv"][4:6]
    jpg_stop = window.compat_sources["jpg"][5]
    assert csv_pause.isEnabled()
    csv_pause.click()
    qtbot.wait(80)  # Finish the current file, then pause at its boundary.
    paused_count = len(window.sample_store)
    image_generation = window._image_store.get(0)[2]
    qtbot.waitUntil(lambda: window._image_store.get(0)[2] > image_generation + 1, timeout=3000)
    assert len(window.sample_store) == paused_count
    assert window._monitor_active
    csv_pause.click()
    qtbot.waitUntil(lambda: len(window.sample_store) > paused_count, timeout=3000)
    csv_stop.click()
    qtbot.wait(80)
    stopped_count = len(window.sample_store)
    image_generation = window._image_store.get(0)[2]
    qtbot.waitUntil(lambda: window._image_store.get(0)[2] > image_generation + 1, timeout=3000)
    assert len(window.sample_store) == stopped_count and window._monitor_active
    assert not csv_pause.isEnabled() and not csv_stop.isEnabled()
    jpg_stop.click()
    qtbot.waitUntil(lambda: not window._monitor_active, timeout=3000)
    assert window._monitor_worker is None and window._monitor_thread is None and window._tx_bytes == 0


def write_native_context(directory, *, link_type=0, tabs=()):
    config = {"type": "config", "vnumber": 100, "ctx": {
        "text_buffer": {"encoder": "UTF-8"}, "datahub_global": {"global_decimal": 4},
        "wave_view": {"ctx": {
            "left_panel": {"ctx": {"pal": {"ctx": {
                "protocol_combo": "JustFloat", "link_type_combo": link_type,
                "serial": {"port": "COM99", "baud": "115200", "databits": "8", "parity": "None", "stopbits": "1"},
            }}}},
            "color_bar": {"ctx": {".": {"delta_t": 1, "buffer_max_size_input.text": "1000"}}},
        }},
    }}
    for name, document in (
        ("vofa+.config.json", config),
        ("vofa+.tabviews.json", {"type": "tabviews", "vnumber": 100, "ctx": [{"tabs": list(tabs)}]}),
        ("vofa+.cmds.json", {"type": "cmds", "vnumber": 100, "ctx": {"subCmds": []}}),
    ):
        (directory / name).write_text(json.dumps(document), encoding="utf-8")
    return directory / "vofa+.config.json"


def run_native_preview(window, qapp, monkeypatch, path, *, allow_partial=False, apply_mapping=False):
    monkeypatch.setattr("qx_stc_isp.gui.QFileDialog.getOpenFileName", lambda *_a, **_k: (str(path), "JSON"))
    observed = {}
    def dismiss():
        dialog = qapp.activeModalWidget()
        try:
            observed["dialog"] = isinstance(dialog, QDialog)
            if not isinstance(dialog, QDialog):
                return
            observed["text"] = dialog.findChild(QPlainTextEdit).toPlainText()
            button = next(item for item in dialog.findChildren(QPushButton) if item.text() == "应用映射")
            observed["initial_enabled"] = button.isEnabled()
            partial = next(item for item in dialog.findChildren(QCheckBox) if "仅导入" in item.text())
            observed["partial_visible"] = partial.isVisible()
            if allow_partial:
                partial.click()
                observed["partial_enabled"] = button.isEnabled()
            if apply_mapping:
                button.click()
        finally:
            if isinstance(dialog, QDialog) and not apply_mapping:
                dialog.reject()
    QTimer.singleShot(0, dismiss)
    window.workspace_native_button.click()
    return observed


def state_snapshot(window):
    return (window.source_combo.currentData(), window.channel_names_edit.text(),
            window.monitor_baud_edit.text(), window.sample_store.snapshot(),
            window.dashboard.pages, window._workspace_dirty)


def test_gui_native_preview_cancel_does_not_replace_current_workspace(window, qapp, tmp_path, monkeypatch):
    path = write_native_context(tmp_path)
    before = state_snapshot(window)
    observed = run_native_preview(window, qapp, monkeypatch, path)
    assert observed["dialog"] and observed["initial_enabled"]
    assert "已支持" in observed["text"]
    assert state_snapshot(window) == before
    assert window._monitor_worker is None and not window._monitor_active and window._tx_bytes == 0


def test_gui_native_unknown_connection_disables_apply_and_keeps_workspace(window, qapp, tmp_path, monkeypatch):
    path = write_native_context(tmp_path, link_type=99)
    before = state_snapshot(window)
    observed = run_native_preview(window, qapp, monkeypatch, path)
    assert observed["dialog"] and not observed["initial_enabled"]
    assert "link_type_combo" in observed["text"] and "不支持" in observed["text"]
    assert state_snapshot(window) == before
    assert window._monitor_worker is None and window._tx_bytes == 0


def test_gui_native_incomplete_mapping_requires_explicit_partial_choice(window, qapp, tmp_path, monkeypatch):
    path = write_native_context(tmp_path, tabs=[{"name": "unsupported"}])
    before = state_snapshot(window)
    observed = run_native_preview(window, qapp, monkeypatch, path, allow_partial=True)
    assert not observed["initial_enabled"] and observed["partial_visible"] and observed["partial_enabled"]
    assert "vofa+.tabviews.json.ctx[0].tabs" in observed["text"]
    assert state_snapshot(window) == before


def test_gui_native_apply_maps_settings_but_never_starts_connection(window, qapp, tmp_path, monkeypatch):
    path = write_native_context(tmp_path)
    observed = run_native_preview(window, qapp, monkeypatch, path, apply_mapping=True)
    assert observed["initial_enabled"]
    assert window.monitor_baud_edit.text() == "115200"
    assert window.decoder_combo.currentData() == "JustFloat"
    assert window._monitor_worker is None and window._monitor_thread is None
    assert not window._monitor_active and not window._monitor_connected and window._tx_bytes == 0
    assert not window._periodic_timer.isActive() and not window.send_button.isEnabled()
