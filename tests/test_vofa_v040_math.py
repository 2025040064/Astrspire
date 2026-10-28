"""Synthetic offline regression definitions. Added but NOT RUN in this session."""

import math

import pytest

from qx_stc_isp.vofa.analysis_view import amplitude_spectrum, histogram, validate_analysis_config
from qx_stc_isp.vofa.channel_settings import ChannelDisplay
from qx_stc_isp.vofa.layout_handles import validate_control_rect
from qx_stc_isp.vofa.json_data import load_json_document
from qx_stc_isp.vofa.controls import ControlSpec


def test_rectangular_spectrum_reports_single_sided_amplitude():
    values = tuple(2.5 * math.sin(2 * math.pi * 16 * index / 128) for index in range(128))
    frequencies, amplitudes, count = amplitude_spectrum(values, 1, 128, window="rectangular")
    assert count == 128
    assert frequencies[16] == 125
    assert amplitudes[16] == pytest.approx(2.5)
    assert amplitudes[0] == pytest.approx(0, abs=1e-12)


def test_dc_and_mean_removal_are_distinct():
    frequencies, amplitudes, count = amplitude_spectrum([7.0] * 64, 2, 64, window="rectangular", remove_mean=False)
    assert count == 64 and len(frequencies) == 33
    assert amplitudes[0] == pytest.approx(7)
    _, removed, _ = amplitude_spectrum([7.0] * 64, 2, 64, window="rectangular", remove_mean=True)
    assert removed == pytest.approx([0] * 33)


def test_spectrum_uses_latest_power_of_two_and_rejects_gaps():
    assert amplitude_spectrum([1.0] * 100, 1, 128)[2] == 64
    assert amplitude_spectrum([1.0] * 7, 1, 64) == ((), (), 0)
    assert amplitude_spectrum([1.0] * 20 + [math.nan], 1, 64) == ((), (), 0)


@pytest.mark.parametrize("size", [True, 100, 4096, 0])
def test_spectrum_rejects_unsupported_windows(size):
    with pytest.raises(ValueError):
        amplitude_spectrum([0.0] * 128, 1, size)


def test_histogram_retains_boundary_counts_and_handles_constant_data():
    centers, counts, width = histogram([0.0, 1.0, 2.0, 3.0], 4)
    assert len(centers) == 4 and counts == (1, 1, 1, 1) and width > 0
    _, constant_counts, constant_width = histogram([5.0] * 12, 8)
    assert sum(constant_counts) == 12 and constant_width > 0


def test_display_calibration_does_not_mutate_input():
    raw = (2.0, 3.0)
    config = ChannelDisplay("温度", .5, -1, "℃", 2)
    assert tuple(config.apply(value) for value in raw) == (0, .5)
    assert raw == (2, 3)
    with pytest.raises(ValueError):
        config.apply(math.inf)


def test_geometry_and_analysis_config_fail_closed():
    assert validate_control_rect([8, 16, 240, 180]) == (8, 16, 240, 180)
    for rect in ([-1, 0, 240, 180], [0, 0, 0, 180], [0, 0, True, 180], [0, 0, 3000, 180]):
        with pytest.raises(ValueError):
            validate_control_rect(rect)
    validate_analysis_config({"mode": "spectrum", "fft_size": 2048, "remove_mean": True})
    with pytest.raises(ValueError):
        validate_analysis_config({"mode": "spectrum", "run": "external"})


@pytest.mark.parametrize("content", ['{"x":1,"x":2}', '{"x":NaN}', '[' * 40 + '0' + ']' * 40])
def test_json_configuration_rejects_duplicates_constants_and_depth(content):
    with pytest.raises(ValueError):
        load_json_document(content, max_bytes=8192)


def test_json_configuration_has_node_and_size_budgets():
    with pytest.raises(ValueError):
        load_json_document('[1,2,3,4]', max_bytes=8192, max_nodes=3)
    with pytest.raises(ValueError):
        load_json_document('{"value":123}', max_bytes=4)
    assert load_json_document('{"a":[1,2]}', max_bytes=1024) == {"a": [1, 2]}


def test_light_configuration_has_explicit_threshold_and_color_validation():
    spec = ControlSpec("light", settings={"three_color": True, "lower_threshold": 10, "upper_threshold": 20, "high_color": "#ff0000"})
    assert spec.settings["lower_threshold"] == 10
    with pytest.raises(ValueError):
        ControlSpec("light", settings={"three_color": True, "lower_threshold": 20, "upper_threshold": 10})
    with pytest.raises(ValueError):
        ControlSpec("light", settings={"high_color": "url(file.png)"})
