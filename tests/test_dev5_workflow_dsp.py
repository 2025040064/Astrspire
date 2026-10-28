"""Public DSP seams: independent hand-calculated vectors, no devices."""
import numpy as np
import pytest

from qx_stc_isp.vofa.dsp import StatefulFilter, spectrum


def test_moving_average_keeps_batch_state_and_never_mutates_source():
    source = np.array([2., 4., 6., 8.])
    filt = StatefulFilter("moving_average", window=3)
    first = filt.process(source[:2])
    second = filt.process(source[2:])
    np.testing.assert_allclose(first, [2, 3])
    np.testing.assert_allclose(second, [4, 6])
    np.testing.assert_array_equal(source, [2, 4, 6, 8])
    assert not first.flags.writeable


@pytest.mark.parametrize("kind, options, expected", [
    ("exponential", {"alpha": .5}, [2, 3, 4.5, 6.25]),
    ("fir", {"b": [.5, .5]}, [1, 3, 5, 7]),
    ("biquad", {"b": [.5, 0, 0], "a": [1, -.5, 0]}, [1, 2.5, 4.25, 6.125]),
])
def test_streaming_filters_keep_cross_batch_state(kind, options, expected):
    filt = StatefulFilter(kind, **options)
    actual = np.concatenate([filt.process([2, 4]), filt.process([6, 8])])
    np.testing.assert_allclose(actual, expected)
    filt.reset()
    np.testing.assert_allclose(filt.process([2]), expected[:1])


def test_nan_gap_is_preserved_and_resets_filter_state():
    filt = StatefulFilter("moving_average", window=2)
    result = filt.process([2, 4, np.nan, 8, 10])
    np.testing.assert_allclose(result, [2, 3, np.nan, 8, 9], equal_nan=True)


def test_fft_known_sine_and_energy_are_calibrated_not_raw_dft():
    t = np.arange(1000) / 1000
    samples = 2 * np.sin(2 * np.pi * 50 * t)
    result = spectrum(samples, sample_rate=1000, window="rect", mode="magnitude")
    assert result.frequencies[50] == 50
    assert result.values[50] == pytest.approx(2)
    assert spectrum(samples, sample_rate=1000, window="rect", mode="power").values[50] == pytest.approx(2)
    assert spectrum(samples, sample_rate=1000, window="rect", mode="psd").values[50] == pytest.approx(2)
    assert spectrum(samples, sample_rate=1000, window="rect", mode="db").values[50] == pytest.approx(6.020599913)
    assert not result.values.flags.writeable


@pytest.mark.parametrize("kwargs", [{}, {"sample_rate": 0}, {"sample_rate": float("nan")}])
def test_fft_without_reliable_rate_does_not_invent_hertz(kwargs):
    with pytest.raises(ValueError, match="采样率"):
        spectrum([1, 2, 3, 4], **kwargs)


def test_fft_rejects_gaps_instead_of_joining_missing_samples():
    with pytest.raises(ValueError, match="缺口"):
        spectrum([1, np.nan, 2, 3], sample_rate=100)


@pytest.mark.parametrize("options", [{"alpha": 0}, {"alpha": 2}, {"alpha": float("nan")}])
def test_exponential_rejects_invalid_coefficients(options):
    with pytest.raises(ValueError):
        StatefulFilter("exponential", **options)

