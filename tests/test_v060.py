"""Offline regression checks for the v0.6 workbench changes."""

import pytest


@pytest.fixture(autouse=True)
def no_hardware(monkeypatch):
    monkeypatch.setattr("qx_stc_isp.gui.enumerate_ports", lambda: [])
    def forbidden(*args, **kwargs):
        pytest.fail("Offline regression must not open hardware")
    monkeypatch.setattr("serial.Serial", forbidden)


def test_control_before_wave_does_not_truncate_history(qtbot):
    from qx_stc_isp.vofa.dashboard import DashboardCard, DashboardManager, DashboardPage
    manager = DashboardManager()
    qtbot.addWidget(manager)
    manager.load_pages([DashboardPage("p", "Mixed", (
        DashboardCard("c", "control", "Value"),
        DashboardCard("w", "waveform", "Wave"),
    )).to_document()])
    manager.set_sample_snapshot([(1.,), (2.,), (3.,)])
    assert manager.card_widget("w").displayed_series == ((1., 2., 3.),)


def test_replacing_live_data_preserves_fixed_axes(qtbot):
    from qx_stc_isp.vofa.waveform import WaveformWidget
    wave = WaveformWidget()
    qtbot.addWidget(wave)
    wave.set_axis_ranges(0., 10., -2., 2.)
    wave.replace_samples([(1.,), (2.,)], ["A"])
    assert wave.axis_ranges == dict(x_min=0., x_max=10., y_min=-2., y_max=2.)


def test_analysis_before_wave_observes_current_batch(qtbot):
    from qx_stc_isp.vofa.dashboard import DashboardCard, DashboardManager, DashboardPage
    manager = DashboardManager()
    qtbot.addWidget(manager)
    manager.load_pages([DashboardPage("p", "Mixed", (
        DashboardCard("a", "analysis", "Analysis", {"source_waveform_id": "w"}),
        DashboardCard("w", "waveform", "Wave"),
    )).to_document()])
    manager.show()
    manager.set_sample_snapshot([(1.,), (2.,), (3.,)], ("A",))
    assert manager.card_widget("a").canvas.points == ((1, 3.),)


def test_resume_without_new_input_catches_up_immediately(qtbot):
    from qx_stc_isp.vofa.dashboard import DashboardManager
    manager = DashboardManager()
    qtbot.addWidget(manager)
    manager.add_page("Page")
    card = manager.add_card("waveform")
    wave = manager.card_widget(card)
    manager.set_sample_snapshot([(1.,), (2.,)])
    wave.set_paused(True)
    manager.set_sample_snapshot([(1.,), (2.,), (3.,)])
    assert wave.displayed_sample_count == 2
    wave.set_paused(False)
    assert wave.displayed_series == ((1., 2., 3.),)


def test_new_image_settings_report_changes_but_frames_do_not(qtbot):
    from PySide6.QtCore import QByteArray, QBuffer, QIODevice
    from PySide6.QtGui import QImage
    from qx_stc_isp.vofa.dashboard import DashboardManager
    manager = DashboardManager()
    qtbot.addWidget(manager)
    manager.add_page("Images")
    card = manager.add_card("image")
    viewer = manager.card_widget(card).viewer
    changes = []
    manager.configuration_changed.connect(lambda: changes.append(True))
    viewer.set_effect("sepia")
    assert changes
    changes.clear()
    viewer.set_effect_parameter(35)
    assert changes
    changes.clear()
    image = QImage(2, 2, QImage.Format_RGB32)
    image.fill(0xff123456)
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.WriteOnly)
    image.save(buffer, "PNG")
    viewer.set_encoded_image(bytes(data))
    assert not changes
    viewer.rotate_clockwise()
    assert changes


@pytest.mark.parametrize("field,value", [
    ("hex_on", "false"), ("cmd_hex", 123), ("loop_ms", False),
    ("loop_ms", 19), ("decimal", "3"), ("decimal", -7),
])
def test_native_present_invalid_values_return_field_diagnostic(tmp_path, field, value):
    import json
    from test_native_project import write_context
    from qx_stc_isp.vofa.native_project import inspect_native_context
    write_context(tmp_path)
    path = tmp_path / "vofa+.config.json"
    config = json.loads(path.read_text(encoding="utf-8"))
    wave = config["ctx"]["wave_view"]["ctx"]
    target = (wave["settingsPanel"]["ctx"]["."]["settings_ctx"][0] if field == "decimal"
              else wave["tx_rx_zone"]["ctx"]["."])
    target[field] = value
    path.write_text(json.dumps(config), encoding="utf-8")
    report = inspect_native_context(tmp_path)
    assert not report.complete and report.candidate is None
    assert any(issue.path.endswith(field) for issue in report.errors)


def test_sepia_strength_blends_source_and_full_effect():
    from PySide6.QtGui import QColor, QImage
    from qx_stc_isp.vofa.image_effects import apply_image_effect
    image = QImage(1, 1, QImage.Format_RGBA8888)
    image.setPixelColor(0, 0, QColor(50, 100, 150, 123))
    low = apply_image_effect(image, "sepia", 25).image.pixelColor(0, 0)
    high = apply_image_effect(image, "sepia", 100).image.pixelColor(0, 0)
    assert 50 < low.red() < high.red()
    assert low.alpha() == high.alpha() == 123
    assert image.pixelColor(0, 0) == QColor(50, 100, 150, 123)


def test_posterize_has_eight_endpoint_levels_without_changing_alpha():
    from PySide6.QtGui import QColor, QImage
    from qx_stc_isp.vofa.image_effects import apply_image_effect
    image = QImage(256, 1, QImage.Format_RGBA8888)
    for value in range(256):
        image.setPixelColor(value, 0, QColor(value, value, value, 123))
    result = apply_image_effect(image, "posterize", 100).image
    assert {result.pixelColor(x, 0).red() for x in range(256)} == {0, 36, 73, 109, 146, 182, 219, 255}
    assert all(result.pixelColor(x, 0).alpha() == 123 for x in range(256))


def test_sample_cursor_reports_eviction_and_clear_without_reusing_epoch():
    from qx_stc_isp.vofa.model import TelemetryEvent
    from qx_stc_isp.vofa.sample_store import SampleStore
    store = SampleStore(capacity=2)
    empty = store.read_since(None)
    for value in (1., 2., 3.):
        store.append_event(TelemetryEvent(raw=b"", samples=(value,)))
    batch = store.read_since(empty.cursor)
    assert [frame.values for frame in batch.frames] == [(2.,), (3.,)]
    assert batch.eviction_gap == 1 and not batch.reset
    assert store.read_since(batch.cursor).frames == ()
    store.clear()
    store.append_event(TelemetryEvent(raw=b"", samples=(9.,)))
    fresh = store.read_since(batch.cursor)
    assert fresh.reset and fresh.cursor.epoch != batch.cursor.epoch
    assert fresh.frames[0].sequence == 1


def test_incremental_projection_reuses_history_and_rebuilds_on_config_change():
    from qx_stc_isp.vofa.model import TelemetryEvent
    from qx_stc_isp.vofa.sample_store import SampleStore
    from qx_stc_isp.vofa.channel_settings import ChannelDisplay
    from qx_stc_isp.vofa.projection import ProjectionBuilder
    store = SampleStore(capacity=3)
    builder = ProjectionBuilder()
    for value in (1., 2.):
        store.append_event(TelemetryEvent(raw=b"", samples=(value,)))
    first = builder.build(store, (ChannelDisplay(scale=2),), 1)
    store.append_event(TelemetryEvent(raw=b"", samples=(3.,)))
    second = builder.build(store, (ChannelDisplay(scale=2),), 1)
    assert first.columns == ((2., 4.),)
    assert second.columns == ((2., 4., 6.),) and second.projected_frames == 1
    changed = builder.build(store, (ChannelDisplay(offset=1),), 2)
    assert changed.columns == ((2., 3., 4.),) and changed.projected_frames == 3
    store.clear()
    store.append_event(TelemetryEvent(raw=b"", samples=(9.,)))
    assert builder.build(store, (ChannelDisplay(),), 3).columns == ((9.,),)


def test_wave_projection_is_shared_and_pause_is_frozen(qtbot):
    from qx_stc_isp.vofa.projection import ProjectionSnapshot
    from qx_stc_isp.vofa.sample_store import SampleCursor
    from qx_stc_isp.vofa.waveform import WaveformWidget
    wave = WaveformWidget(max_samples=100000)
    qtbot.addWidget(wave)
    first = ProjectionSnapshot(SampleCursor(0, 2), 1, ((1., 2.),), 2)
    wave.set_projection(first, ["A"])
    assert wave.displayed_series is first.columns
    wave.set_paused(True)
    wave.set_projection(ProjectionSnapshot(SampleCursor(0, 3), 1, ((1., 2., 3.),), 1), ["A"])
    assert wave.displayed_series == ((1., 2.),)
    wave.set_paused(False)
    assert wave.displayed_series == ((1., 2., 3.),)
    wave.append_samples((4.,), ["A"])
    assert wave.displayed_series == ((1., 2., 3., 4.),)


def test_dashboard_projection_worker_updates_visible_cards_and_shuts_down(qtbot):
    from qx_stc_isp.vofa.dashboard import DashboardManager
    from qx_stc_isp.vofa.sample_store import SampleStore
    from qx_stc_isp.vofa.model import TelemetryEvent
    from qx_stc_isp.vofa.channel_settings import ChannelDisplay
    manager = DashboardManager()
    qtbot.addWidget(manager)
    manager.add_page("Page")
    ids = [manager.add_card("waveform") for _ in range(3)]
    store = SampleStore()
    store.append_event(TelemetryEvent(raw=b"", samples=(2.,)))
    manager.request_projection(store, (ChannelDisplay(scale=2),), ("A",))
    qtbot.waitUntil(lambda: manager.card_widget(ids[-1]).displayed_series == ((4.,),))
    assert manager.card_widget(ids[0]).displayed_series is manager.card_widget(ids[-1]).displayed_series
    manager.close()
    assert manager.stop_projection()


def test_projection_summary_preserves_extrema_and_absolute_indices_after_eviction():
    from qx_stc_isp.vofa.projection import ProjectionBuilder
    from qx_stc_isp.vofa.sample_store import SampleStore
    from qx_stc_isp.vofa.channel_settings import ChannelDisplay
    from qx_stc_isp.vofa.model import TelemetryEvent
    store = SampleStore(capacity=4)
    builder = ProjectionBuilder()
    for value in (99., 1., 8., -3., 2.):
        store.append_event(TelemetryEvent(raw=b"", samples=(value,)))
    result = builder.build(store, (ChannelDisplay(),), 1)
    assert result.bounds == ((-3., 8.),)
    assert result.envelopes == (((1, 1.), (2, 8.), (3, -3.), (4, 2.)),)


def test_main_window_uses_shared_projection_for_three_cards(qtbot, tmp_path):
    from qx_stc_isp.gui import MainWindow
    from qx_stc_isp.vofa.model import TelemetryEvent
    window = MainWindow(log_dir=tmp_path)
    qtbot.addWidget(window)
    window.dashboard.add_page("Shared")
    ids = [window.dashboard.add_card("waveform") for _ in range(3)]
    window._handle_monitor_events([TelemetryEvent(raw=b"", samples=(1.,)), TelemetryEvent(raw=b"", samples=(2.,))])
    window._paint_dashboard()
    qtbot.waitUntil(lambda: window.dashboard.card_widget(ids[-1]).displayed_series == ((1., 2.),))
    assert window.dashboard.card_widget(ids[0]).displayed_series is window.dashboard.card_widget(ids[-1]).displayed_series
    window.close()


def test_display_envelope_preserves_peak_order_endpoints_and_gaps():
    import math
    from qx_stc_isp.vofa.waveform import display_envelope
    points = list(display_envelope((0., 9., -5., 1., math.nan, 2., 8., 3.), 2))
    assert points[:4] == [(0, 0.), (1, 9.), (2, -5.), (3, 1.)]
    assert math.isnan(points[4][1])
    assert points[5:] == [(5, 2.), (6, 8.), (7, 3.)]


def test_projection_missing_channel_keeps_time_positions_and_finite_axes(qtbot):
    import math
    from qx_stc_isp.vofa.projection import ProjectionBuilder
    from qx_stc_isp.vofa.sample_store import SampleStore
    from qx_stc_isp.vofa.channel_settings import ChannelDisplay
    from qx_stc_isp.vofa.model import TelemetryEvent
    from qx_stc_isp.vofa.waveform import WaveformWidget
    store = SampleStore()
    for values in ((1., 10.), (2.,), (3., 30.)):
        store.append_event(TelemetryEvent(raw=b"", samples=values))
    result = ProjectionBuilder().build(store, (ChannelDisplay(), ChannelDisplay()), 1)
    assert math.isnan(result.columns[1][1])
    assert result.columns[1][0] == 10. and result.columns[1][2] == 30.
    wave = WaveformWidget()
    qtbot.addWidget(wave)
    wave.set_projection(result, ("A", "B"))
    assert all(math.isfinite(value) for value in wave.y_range)
    assert not wave.grab().isNull()


def test_dashboard_rejects_late_projection_after_clear_and_configuration_change(qtbot):
    from qx_stc_isp.vofa.dashboard import DashboardManager
    from qx_stc_isp.vofa.sample_store import SampleStore
    from qx_stc_isp.vofa.channel_settings import ChannelDisplay
    from qx_stc_isp.vofa.model import TelemetryEvent
    manager = DashboardManager()
    qtbot.addWidget(manager)
    manager.add_page("Page")
    card = manager.add_card("waveform")
    store = SampleStore()
    store.append_event(TelemetryEvent(raw=b"", samples=(1.,)))
    manager.request_projection(store, (ChannelDisplay(scale=2),), ("A",))
    store.clear()
    store.append_event(TelemetryEvent(raw=b"", samples=(9.,)))
    manager.request_projection(store, (ChannelDisplay(scale=3),), ("A",))
    qtbot.waitUntil(lambda: manager.card_widget(card).displayed_series == ((27.,),))
    qtbot.wait(30)
    assert manager.card_widget(card).displayed_series == ((27.,),)
    manager.close()
