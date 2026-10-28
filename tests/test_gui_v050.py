"""v0.5 regressions, offline only; no device may be opened by these tests."""

from datetime import datetime, timedelta, timezone

import pytest

from qx_stc_isp.gui import MainWindow
from qx_stc_isp.vofa.channel_settings import ChannelDisplay
from qx_stc_isp.vofa.model import TelemetryEvent


@pytest.fixture
def workbench(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr("qx_stc_isp.gui.enumerate_ports", lambda: [])
    def forbidden(*_args, **_kwargs):
        pytest.fail("offline UI tests must never open hardware")
    monkeypatch.setattr("serial.Serial", forbidden)
    window = MainWindow(log_dir=tmp_path / "logs")
    qtbot.addWidget(window)
    yield window
    window.close()


def test_rescaling_keeps_all_100000_retained_samples(workbench):
    workbench.wave_buffer_spin.setValue(100000)
    workbench._handle_monitor_events([
        TelemetryEvent(raw=b"", samples=(float(index),)) for index in range(100000)
    ])
    assert workbench.waveform.displayed_sample_count == 100000
    workbench._channel_display = (ChannelDisplay(scale=2, offset=3),) + workbench._channel_display[1:]
    workbench._rebuild_display_samples()
    assert workbench.waveform.displayed_sample_count == 100000
    assert len(workbench.sample_store.snapshot()) == 100000
    assert workbench.sample_store.snapshot()[0].values == (0.0,)
    assert workbench._last_values == (200001.0,)


def test_export_after_csv_replay_retains_original_microsecond_time_and_values(workbench, tmp_path, monkeypatch):
    import csv
    from qx_stc_isp.vofa.playback import CsvReplayWorker
    from qx_stc_isp.vofa.recorder import CsvRecorder
    first = datetime(2026, 10, 1, 2, 3, 4, 123456, tzinfo=timezone.utc)
    source = tmp_path / "source.csv"
    with CsvRecorder(source, ("raw",)) as recorder:
        recorder.record(TelemetryEvent(raw=b"", samples=(1.25,)), at=first)
        recorder.record(TelemetryEvent(raw=b"", samples=(2.5,)), at=first + timedelta(microseconds=500))
    worker = CsvReplayWorker(source, speed=20)
    events = []
    worker.events.connect(events.extend)
    worker.run()
    workbench._handle_monitor_events(events)
    output = tmp_path / "export.csv"
    monkeypatch.setattr("qx_stc_isp.gui.QFileDialog.getSaveFileName", lambda *_a, **_k: (str(output), ""))
    workbench.export_monitor_csv()
    with output.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert [datetime.fromisoformat(row["timestamp_utc"]) for row in rows] == [first, first + timedelta(microseconds=500)]
    assert [row["value"] for row in rows] == ["1.25", "2.5"]


def test_pause_does_not_stop_raw_acquisition(workbench):
    workbench._handle_monitor_events([TelemetryEvent(raw=b"", samples=(1.0,))])
    workbench.waveform.set_paused(True)
    workbench._handle_monitor_events([TelemetryEvent(raw=b"", samples=(2.0,))])
    assert len(workbench.sample_store.snapshot()) == 2
    assert workbench.waveform.displayed_sample_count == 1
    workbench.waveform.set_paused(False)
    assert workbench.waveform.displayed_sample_count == 2


def test_rescale_preserves_elapsed_frame_index_after_history_eviction(workbench):
    workbench.sample_store.set_capacity(100)
    workbench._handle_monitor_events([TelemetryEvent(raw=b'', samples=(float(i),)) for i in range(150)])
    before = workbench.waveform.displayed_sample_count
    workbench._rebuild_display_samples()
    assert workbench.waveform.displayed_sample_count == before == 150


def test_increasing_plot_buffer_rehydrates_retained_raw_history(workbench):
    workbench.wave_buffer_spin.setValue(100)
    workbench._handle_monitor_events([TelemetryEvent(raw=b'', samples=(float(i),)) for i in range(150)])
    assert len(workbench.waveform.displayed_series[0]) == 100
    workbench.wave_buffer_spin.setValue(200)
    assert len(workbench.waveform.displayed_series[0]) == 150


def test_hidden_legacy_image_does_not_decode(workbench, monkeypatch):
    workbench.show()
    workbench.tabs.setCurrentIndex(1)
    workbench.visual_tabs.setCurrentIndex(0)
    def forbidden(*_args):
        pytest.fail('hidden image must not decode')
    monkeypatch.setattr(workbench._image_store, 'decoded', forbidden)
    workbench._cache_received_image(0, b'not decoded while hidden')


def test_schema5_roundtrip_keeps_group_ids_and_active_mixed_page(workbench, tmp_path, monkeypatch):
    from qx_stc_isp.vofa.commands import CommandEntry
    from qx_stc_isp.vofa.workspace import load_workspace
    from PySide6.QtWidgets import QMessageBox
    workbench._commands = [CommandEntry('A', 'A'), CommandEntry('B', 'B')]
    workbench._refresh_command_list()
    command_ids = workbench._command_ids[:]
    workbench.move_command(1, -1)
    assert workbench._command_ids == command_ids[::-1]
    page_id = workbench.dashboard.add_page('混合看板')
    workbench.dashboard.add_card('waveform', '数据')
    workbench.dashboard.add_card('control', '控件')
    workbench.dashboard.add_card('image', '图像')
    workbench.dashboard.add_card('analysis', '频谱')
    output = tmp_path / 'schema5.json'
    monkeypatch.setattr('qx_stc_isp.gui.QFileDialog.getSaveFileName', lambda *_a, **_k: (str(output), ''))
    workbench.save_monitor_workspace()
    settings = load_workspace(output)
    assert settings.active_page_id == page_id
    assert len(settings.pages) == 5
    assert [item['id'] for item in settings.command_groups[0]['commands']] == command_ids[::-1]
    monkeypatch.setattr('qx_stc_isp.gui.QMessageBox.question', lambda *_a, **_k: QMessageBox.Yes)
    workbench.load_monitor_workspace(settings=settings)
    assert workbench.dashboard.active_page_id() == page_id
    assert workbench._command_ids == command_ids[::-1]
    assert not workbench._monitor_active
    assert not workbench._send_job.active


def test_active_sequence_is_frozen_when_current_group_is_edited(workbench, qtbot, monkeypatch):
    from qx_stc_isp.vofa.commands import CommandEntry
    sent = []
    monkeypatch.setattr(workbench, '_can_send_data', lambda: True)
    workbench._send_job._can_send = lambda: True
    workbench._send_job._enqueue = lambda packet: sent.append(packet) or True
    workbench._commands = [CommandEntry('A', 'A'), CommandEntry('B', 'B')]
    workbench._refresh_command_list()
    workbench.sequence_interval_spin.setValue(20)
    workbench.start_command_sequence()
    workbench._commands[:] = [CommandEntry('different', 'BAD')]
    workbench._refresh_command_list()
    qtbot.waitUntil(lambda: not workbench._send_job.active, timeout=2000)
    assert sent == [b'A', b'B']
