"""Offline contracts for the shared plotting backend and buffers."""

import math

import pytest


def test_plot_buffer_is_read_only_and_keeps_absolute_sample_indices():
    from qx_stc_isp.vofa.projection import PlotBuffer
    from qx_stc_isp.vofa.sample_store import SampleCursor
    buffer = PlotBuffer.from_columns(((1., 2.), (3., math.nan)), SampleCursor(7, 10), 2)
    assert buffer.values.shape == (2, 2)
    assert buffer.indices.tolist() == [8., 9.]
    assert buffer.cursor == SampleCursor(7, 10) and buffer.generation == 2
    assert math.isnan(buffer.values[1, 1])
    with pytest.raises(ValueError):
        buffer.values[0, 0] = 99.
    with pytest.raises(ValueError):
        buffer.indices[0] = 0.


def test_wave_uses_one_pyqtgraph_backend_and_keeps_shared_buffer(qtbot):
    import pyqtgraph as pg
    from qx_stc_isp.vofa.projection import PlotBuffer, ProjectionSnapshot
    from qx_stc_isp.vofa.sample_store import SampleCursor
    from qx_stc_isp.vofa.waveform import WaveformWidget
    cursor = SampleCursor(0, 3)
    columns = ((1., math.nan, 3.),)
    buffer = PlotBuffer.from_columns(columns, cursor, 1)
    snapshot = ProjectionSnapshot(cursor, 1, columns, 3, plot_buffer=buffer)
    waves = [WaveformWidget(), WaveformWidget()]
    for wave in waves:
        qtbot.addWidget(wave)
        wave.set_projection(snapshot, ["A"])
        assert len(wave.findChildren(pg.PlotWidget)) == 1
        assert wave.plot_buffer is buffer
        assert math.isnan(wave.displayed_series[0][1])
    waves[0].set_paused(True)
    newer = ProjectionSnapshot(SampleCursor(0, 4), 1, ((1., 2., 3., 4.),), 1)
    waves[0].set_projection(newer, ["A"])
    assert waves[0].plot_buffer is buffer


def test_pyqtgraph_peak_downsampling_keeps_extrema_and_a_nan_gap(qtbot):
    from qx_stc_isp.vofa.projection import PlotBuffer, ProjectionSnapshot
    from qx_stc_isp.vofa.sample_store import SampleCursor
    from qx_stc_isp.vofa.waveform import WaveformWidget
    wave = WaveformWidget(max_samples=100000)
    qtbot.addWidget(wave)
    values = [0.] * 10000
    values[2000] = math.nan
    values[7000] = 55.
    columns = (tuple(values),)
    cursor = SampleCursor(0, len(values))
    buffer = PlotBuffer.from_columns(columns, cursor, 1)
    wave.set_projection(ProjectionSnapshot(cursor, 1, columns, len(values), plot_buffer=buffer), ["A"])
    wave.resize(300, 240)
    wave.show()
    wave.grab()
    curve = wave._curves[0]
    _x, y = curve.getData()
    assert len(y) < len(values) and max(value for value in y if math.isfinite(value)) == 55.
    assert any(math.isnan(value) for value in y)
    path = curve.curve.getPath()
    assert sum(path.elementAt(index).isMoveTo() for index in range(path.elementCount())) >= 2
    assert wave.plot_buffer.values[0, 7000] == 55.
def test_shared_render_view_keeps_extrema_endpoints_and_nan_gaps():
    import numpy as np
    from qx_stc_isp.vofa.projection import PlotBuffer
    from qx_stc_isp.vofa.sample_store import SampleCursor
    values = np.zeros((1, 10000))
    values[0, 234] = 99
    values[0, 567] = -88
    buffer = PlotBuffer.from_columns(values, SampleCursor(0, 10000), 1)
    x, y = buffer.render_series(0, 0, 9999, 100, 10000)
    assert len(y) < 1000
    assert (x[0], x[-1], y.min(), y.max()) == (0, 9999, -88, 99)
    assert buffer.render_series(0, 0, 9999, 100, 10000)[1] is y
    assert not y.flags.writeable
    values[0, 500] = np.nan
    gap = PlotBuffer.from_columns(values, SampleCursor(0, 10000), 2)
    gx, gy = gap.render_series(0, 0, 9999, 100, 10000)
    assert np.isnan(gy[gx == 500]).all()

