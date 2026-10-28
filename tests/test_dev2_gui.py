"""Main-window integration without opening any hardware."""

import pytest


@pytest.fixture
def window(qtbot, tmp_path, monkeypatch):
    from qx_stc_isp.gui import MainWindow
    monkeypatch.setattr('qx_stc_isp.gui.enumerate_ports', lambda: [])
    monkeypatch.setattr('serial.Serial', lambda *a, **k: pytest.fail('No hardware in GUI regressions'))
    view = MainWindow(log_dir=tmp_path)
    qtbot.addWidget(view)
    view.show()
    view.tabs.setCurrentIndex(1)
    yield view
    view.close()


def test_measurement_tab_tracks_selected_card_units_pause_and_resume(window, qtbot):
    from qx_stc_isp.vofa.channel_settings import ChannelDisplay
    from qx_stc_isp.vofa.model import TelemetryEvent
    window._channel_display = (ChannelDisplay(unit='V'),) + window._channel_display[1:]
    window.dashboard.add_page('Measurements')
    first = window.dashboard.add_card('waveform', 'A')
    second = window.dashboard.add_card('waveform', 'B')
    window.dashboard.select_card(first)
    window._handle_monitor_events([TelemetryEvent(raw=b'', samples=(1.,)), TelemetryEvent(raw=b'', samples=(3.,))])
    window._paint_dashboard()
    window.monitor_values_tabs.setCurrentIndex(1)
    panel = window.measurement_panel
    qtbot.waitUntil(lambda: bool(panel.summaries))
    assert panel.summaries[0].mean == 2.
    assert '[V]' in panel.tree.topLevelItem(0).text(0)
    wave = window.dashboard.card_widget(first)
    wave.set_paused(True)
    window._handle_monitor_events([TelemetryEvent(raw=b'', samples=(100.,))])
    window._paint_dashboard()
    qtbot.wait(250)
    assert panel.summaries[0].mean == 2.
    window.dashboard.select_card(second)
    qtbot.waitUntil(lambda: bool(panel.summaries) and panel.summaries[0].count == 3)
    assert panel.summaries[0].mean == pytest.approx(104 / 3)
    window.dashboard.select_card(first)
    wave.set_paused(False)
    qtbot.waitUntil(lambda: bool(panel.summaries) and panel.summaries[0].count == 3)
    assert panel.summaries[0].mean == pytest.approx(104 / 3)


def test_measurement_metric_names_fit_in_the_minimum_window(window, qtbot):
    from PySide6.QtGui import QFontMetrics
    from qx_stc_isp.vofa.model import TelemetryEvent
    window.resize(1020, 680)
    window.monitor_values_tabs.setCurrentIndex(1)
    window._handle_monitor_events([TelemetryEvent(raw=b'', samples=(1., 2.))])
    panel = window.measurement_panel
    qtbot.waitUntil(lambda: bool(panel.summaries))
    metrics = QFontMetrics(panel.tree.font())
    required = metrics.horizontalAdvance('平均值') + 2 * panel.tree.indentation() + 8
    assert panel.tree.columnWidth(0) >= required
