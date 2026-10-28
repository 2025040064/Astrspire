"""Statistics use the full visible window, never a plotting envelope."""

import pytest


def test_measurement_constant_and_mixed_samples_have_known_results():
    from qx_stc_isp.vofa.measurement import measure_window
    from qx_stc_isp.vofa.projection import PlotBuffer
    from qx_stc_isp.vofa.sample_store import SampleCursor
    buffer = PlotBuffer.from_columns(((3., 3., 3., 3.), (-3., -1., 1., 3.)), SampleCursor(0, 4), 1)
    result = measure_window(buffer, (0., 3.), (True, True), 100000)
    assert (result[0].count, result[0].minimum, result[0].maximum, result[0].mean, result[0].peak_to_peak, result[0].rms) == (4, 3., 3., 3., 0., 3.)
    assert result[1].mean == 0. and result[1].peak_to_peak == 6.
    assert result[1].rms == pytest.approx(2.23606797749979)


def test_measurement_window_gaps_visibility_and_history_limit():
    import math
    from qx_stc_isp.vofa.measurement import measure_window
    from qx_stc_isp.vofa.projection import PlotBuffer
    from qx_stc_isp.vofa.sample_store import SampleCursor
    buffer = PlotBuffer.from_columns(((99., 1., math.nan, 3., 5.), (2.,) * 5), SampleCursor(0, 15), 1)
    result = measure_window(buffer, (10., 13.), (True, False), 4)
    assert len(result) == 1 and result[0].count == 2
    assert (result[0].minimum, result[0].maximum, result[0].mean) == (1., 3., 2.)
    assert math.isnan(buffer.values[0, 2]) and buffer.values[0, 0] == 99.


def test_measurement_sine_and_extreme_values_are_not_silent_zero():
    import math
    import sys
    from qx_stc_isp.vofa.measurement import measure_window
    from qx_stc_isp.vofa.projection import PlotBuffer
    from qx_stc_isp.vofa.sample_store import SampleCursor
    sine = tuple(2 * math.sin(2 * math.pi * i / 64) for i in range(64))
    signal = PlotBuffer.from_columns((sine,), SampleCursor(0, 64), 1)
    result = measure_window(signal, (0., 63.), (True,), 100000)[0]
    assert result.rms == pytest.approx(math.sqrt(2))
    assert result.mean == pytest.approx(0., abs=1e-14) and result.peak_to_peak == 4.
    extreme = PlotBuffer.from_columns(((-sys.float_info.max, sys.float_info.max),), SampleCursor(0, 2), 1)
    result = measure_window(extreme, (0., 1.), (True,), 100000)[0]
    assert result.mean == 0. and result.rms == sys.float_info.max
    assert result.peak_to_peak is None and '超出' in result.notice


def test_empty_measurement_and_cancellation_do_not_return_fabricated_values():
    import math
    from qx_stc_isp.vofa.measurement import measure_window
    from qx_stc_isp.vofa.projection import PlotBuffer
    from qx_stc_isp.vofa.sample_store import SampleCursor
    buffer = PlotBuffer.from_columns(((math.nan,),), SampleCursor(0, 1), 1)
    result = measure_window(buffer, (0., 0.), (True,), 16)[0]
    assert result.count == 0 and result.minimum is result.rms is result.mean is None
    assert measure_window(buffer, (0., 0.), (True,), 16, cancelled=lambda: True) is None


def test_measurement_worker_returns_typed_results_and_can_close(qtbot):
    from qx_stc_isp.vofa.measurement import MeasurementJob, MeasurementWorker
    from qx_stc_isp.vofa.projection import PlotBuffer
    from qx_stc_isp.vofa.sample_store import SampleCursor
    buffer = PlotBuffer.from_columns(((2., 4.),), SampleCursor(0, 2), 1)
    worker = MeasurementWorker()
    results = []
    worker.ready.connect(results.append)
    worker.start()
    try:
        worker.request(MeasurementJob(1, buffer, (0., 1.), (True,), 100000))
        qtbot.waitUntil(lambda: len(results) == 1)
        assert results[0][0] == 1 and results[0][1][0].mean == 3.
    finally:
        assert worker.stop()


def test_measurement_panel_uses_latest_source_and_stops_when_hidden(qtbot):
    from qx_stc_isp.vofa.measurement_panel import MeasurementPanel, MeasurementSource
    from qx_stc_isp.vofa.projection import PlotBuffer
    from qx_stc_isp.vofa.sample_store import SampleCursor
    buffer = PlotBuffer.from_columns(((1., 3.),), SampleCursor(0, 2), 1)
    selected = [MeasurementSource(buffer, (0., 1.), (True,), 100000, ("A [V]",), (3,), ("source-a",))]
    panel = MeasurementPanel(lambda: selected[0])
    qtbot.addWidget(panel)
    panel.show()
    qtbot.waitUntil(lambda: len(panel.summaries) == 1)
    assert panel.summaries[0].mean == 2.
    assert panel.tree.topLevelItem(0).text(0) == "A [V]"
    panel.hide()
    assert panel.summaries == ()
    selected[0] = MeasurementSource(buffer, (1., 1.), (True,), 100000, ("B",), (3,), ("source-b",))
    qtbot.wait(250)
    assert panel.summaries == ()
    panel.show()
    qtbot.waitUntil(lambda: bool(panel.summaries) and panel.summaries[0].count == 1)
    assert panel.summaries[0].mean == 3.
    panel.close()
    assert panel.stop()


@pytest.mark.parametrize("change", ["window", "buffer"])
def test_measurement_panel_rejects_a_result_after_the_visible_snapshot_changes(qtbot, change):
    from dataclasses import replace
    from qx_stc_isp.vofa.measurement import measure_window
    from qx_stc_isp.vofa.measurement_panel import MeasurementPanel, MeasurementSource
    from qx_stc_isp.vofa.projection import PlotBuffer
    from qx_stc_isp.vofa.sample_store import SampleCursor
    buffer = PlotBuffer.from_columns(((1., 3.),), SampleCursor(0, 2), 1)
    original = MeasurementSource(buffer, (0., 1.), (True,), 100000, ("A",), (3,), ("auto",))
    selected = [original]
    panel = MeasurementPanel(lambda: selected[0])
    qtbot.addWidget(panel)
    panel.show()
    panel._timer.stop()
    panel._token = 1
    panel._requested_source = original
    if change == "window":
        selected[0] = replace(original, view_range=(1., 1.))
    else:
        newer = PlotBuffer.from_columns(((3., 9.),), SampleCursor(0, 3), 1)
        selected[0] = replace(original, buffer=newer)
    panel._accept((1, measure_window(buffer, (0., 1.), (True,), 100000)))
    assert panel.summaries == ()
    assert panel.stop()
