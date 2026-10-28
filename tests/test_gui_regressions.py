"""Offline regressions for safe file handling and constrained window layouts."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QScrollArea

from qx_stc_isp.gui import MainWindow
from qx_stc_isp.hex_image import HexImage
from qx_stc_isp.ports import PortDescriptor


@pytest.fixture(autouse=True)
def forbid_real_hardware(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("GUI regression tests must not open physical serial ports")

    monkeypatch.setattr("serial.Serial", forbidden)
    monkeypatch.setattr("qx_stc_isp.transport.PySerialTransport.open", forbidden)
    monkeypatch.setattr("qx_stc_isp.gui.enumerate_ports", lambda: [])


@pytest.fixture
def window(qtbot, tmp_path):
    workbench = MainWindow(log_dir=tmp_path / "logs")
    qtbot.addWidget(workbench)
    workbench.show()
    yield workbench
    workbench.close()


@pytest.mark.parametrize("failed_step", ["open", "flush"])
@pytest.mark.parametrize("close_fails", [False, True])
def test_initial_csv_failure_is_reported_even_when_cleanup_also_fails(
    window, monkeypatch, tmp_path, qtbot, failed_step, close_fails,
):
    calls = []
    warnings = []

    class FailingRecorder:
        def __init__(self, *_args, **_kwargs):
            pass

        def open(self):
            calls.append("open")
            if failed_step == "open":
                raise OSError("initial open failure")

        def flush(self):
            calls.append("flush")
            raise OSError("initial flush failure")

        def record(self, *_args, **_kwargs):
            return 1

        def close(self):
            calls.append("close")
            if close_fails:
                raise OSError("independent cleanup failure")

    monkeypatch.setattr("qx_stc_isp.gui.CsvRecorder", FailingRecorder)
    monkeypatch.setattr(
        "qx_stc_isp.gui.QFileDialog.getSaveFileName",
        lambda *_a, **_k: (str(tmp_path / "never-created.csv"), "CSV (*.csv)"),
    )
    monkeypatch.setattr(
        "qx_stc_isp.gui.QMessageBox.warning",
        lambda _parent, title, message: warnings.append((title, message)),
    )
    window._demo_active = True  # Enable recording without starting any source.

    window.toggle_sample_recording()  # Cleanup must not raise out of this slot.
    qtbot.waitUntil(lambda: "close" in calls, timeout=2000)
    window._poll_recorders()
    assert calls == (["open"] if failed_step == "open" else ["open", "flush"]) + ["close"]
    assert not warnings  # Background errors use visible status/logs, not a blocking modal.
    assert f"initial {failed_step} failure" in window.monitor_log_view.toPlainText()
    assert window._sample_recorder is None
    assert not window._record_flush_timer.isActive()
    assert "停止实时记录" not in window.monitor_record_button.text()
    assert not (tmp_path / "never-created.csv").exists()
    if close_fails:
        assert "independent cleanup failure" in window.monitor_log_view.toPlainText()


@pytest.mark.parametrize(
    ("filename", "selected_filter", "expected_filename", "expected_kind"),
    [
        ("firmware.hex", "二进制 (*.bin)", "firmware.hex", "hex"),
        ("firmware.ihx", "二进制 (*.bin)", "firmware.ihx", "hex"),
        ("firmware.bin", "Intel HEX (*.hex)", "firmware.bin", "bin"),
        ("firmware.BIN", "Intel HEX (*.hex)", "firmware.BIN", "bin"),
        ("firmware", "Intel HEX (*.hex)", "firmware.hex", "hex"),
        ("firmware", "二进制 (*.bin)", "firmware.bin", "bin"),
        ("firmware", "", "firmware.hex", "hex"),
    ],
)
def test_export_respects_explicit_suffix_or_adds_selected_suffix(
    window, monkeypatch, tmp_path, filename, selected_filter, expected_filename, expected_kind,
):
    source_image = HexImage(((0x100, b"AB"), (0x104, b"CD")))
    destination = tmp_path / filename
    expected = tmp_path / expected_filename
    warnings = []
    monkeypatch.setattr(
        "qx_stc_isp.gui.QFileDialog.getSaveFileName",
        lambda *_a, **_k: (str(destination), selected_filter),
    )
    monkeypatch.setattr(
        "qx_stc_isp.gui.QMessageBox.warning",
        lambda *_args: warnings.append(_args),
    )

    window.export_firmware_buffer(source_image)

    assert not warnings
    assert expected.is_file()
    if expected_kind == "bin":
        assert expected.read_bytes() == b"AB\xff\xffCD"
    else:
        assert expected.read_text(encoding="ascii").startswith(":")
        assert HexImage.from_file(expected).segments == source_image.segments
    if not destination.suffix:
        assert not destination.exists()
    assert str(expected) in window.log_view.toPlainText()


def test_export_rejects_unknown_suffix_without_creating_a_file(window, monkeypatch, tmp_path):
    destination = tmp_path / "firmware.txt"
    warnings = []
    monkeypatch.setattr(
        "qx_stc_isp.gui.QFileDialog.getSaveFileName",
        lambda *_a, **_k: (str(destination), "二进制 (*.bin)"),
    )
    monkeypatch.setattr(
        "qx_stc_isp.gui.QMessageBox.warning",
        lambda _parent, title, message: warnings.append((title, message)),
    )

    window.export_firmware_buffer(HexImage(((0, b"AB"),)))

    assert not destination.exists()
    assert len(warnings) == 1
    assert warnings[0][0] == "导出格式不明确"


@pytest.mark.parametrize("page_index", [0, 1])
def test_long_port_description_stays_inside_sidebar_at_minimum_window_size(
    window, monkeypatch, qtbot, page_index,
):
    port = PortDescriptor(
        device="COM987",
        description="USB-SERIAL CH340 " + "very-long-device-description-" * 40,
        vid=0x1A86,
        pid=0x7523,
        serial_number="fake-offline-device",
        hwid="FAKE-ONLY",
        is_ch340_candidate=True,
    )
    monkeypatch.setattr("qx_stc_isp.gui.enumerate_ports", lambda: [port])
    window.refresh_ports()
    window.resize(1020, 680)
    window.tabs.setCurrentIndex(page_index)
    combo = window.port_combo if page_index == 0 else window.monitor_port_combo
    combo.setCurrentIndex(combo.findData(port.device))
    qtbot.waitUntil(lambda: combo.isVisible() and window.width() == 1020)
    qtbot.wait(10)  # Let the layout process its queued geometry update.

    scroll = combo.parentWidget()
    while scroll is not None and not isinstance(scroll, QScrollArea):
        scroll = scroll.parentWidget()
    assert isinstance(scroll, QScrollArea)
    viewport = scroll.viewport()
    position = combo.mapTo(viewport, QPoint(0, 0))
    assert len(combo.currentText()) > 500
    assert scroll.widget().width() <= viewport.width()
    assert position.x() >= 0
    assert position.x() + combo.width() <= viewport.width()

    if page_index == 1:
        waveform_position = window.waveform.mapTo(window, QPoint(0, 0))
        log_position = window.monitor_log_view.mapTo(window, QPoint(0, 0))
        assert waveform_position.y() + window.waveform.height() <= log_position.y()
