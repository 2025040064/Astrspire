"""Bounded streaming filters and explicit-rate spectra; never modify raw input.

These NumPy routines are an independent Astrspire implementation. No serial,
device access, user code evaluation, or external process is involved.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import math

import numpy as np

MAX_SAMPLES = 100_000


def _samples(values):
    result = np.array(values, dtype=np.float64, copy=True)
    if result.ndim != 1 or len(result) > MAX_SAMPLES:
        raise ValueError("数据必须为最多100000点的一维数值")
    return result


class StatefulFilter:
    """One channel per object; state crosses batches, reset is explicit."""

    def __init__(self, kind="moving_average", *, window=5, alpha=.2, b=None, a=None):
        if kind not in ("moving_average", "exponential", "fir", "biquad"):
            raise ValueError("不支持的滤波器")
        if type(window) is not int or not 1 <= window <= 10_000:
            raise ValueError("移动平均窗口必须为1–10000")
        if isinstance(alpha, bool) or not isinstance(alpha, (int, float)) or not math.isfinite(alpha) or not 0 < alpha <= 1:
            raise ValueError("指数滤波alpha必须在(0,1]内")
        self.kind, self.window, self.alpha = kind, window, float(alpha)
        self.b = _samples([1.] if b is None else b)
        self.a = _samples([1., 0., 0.] if a is None else a)
        if not 1 <= len(self.b) <= 256 or not np.isfinite(self.b).all():
            raise ValueError("FIR系数需1–256个有限数")
        if kind == "biquad":
            if len(self.b) != 3 or len(self.a) != 3 or not np.isfinite(self.a).all() or self.a[0] == 0:
                raise ValueError("biquad需3个b系数及3个a系数，a0不能为0")
            self.b, self.a = self.b / self.a[0], self.a / self.a[0]
            if not np.isfinite(self.b).all() or not np.isfinite(self.a).all() or np.any(np.abs(np.roots(self.a)) >= 1):
                raise ValueError("biquad系数溢出或不稳定")
        self.reset()

    def reset(self):
        self._history = deque(maxlen=self.window)
        self._sum = 0.
        self._previous = None
        self._x = deque([0.] * len(self.b), maxlen=len(self.b))
        self._y = deque([0., 0.], maxlen=2)

    def process(self, values):
        result = _samples(values)
        for index, value in enumerate(result):
            if not math.isfinite(value):
                self.reset()
                result[index] = np.nan
                continue
            value = float(value)
            if self.kind == "moving_average":
                if len(self._history) == self.window:
                    self._sum -= self._history[0]
                self._history.append(value)
                self._sum += value
                output = self._sum / len(self._history)
            elif self.kind == "exponential":
                output = value if self._previous is None else self.alpha * value + (1 - self.alpha) * self._previous
                self._previous = output
            else:
                self._x.appendleft(value)
                output = math.fsum(float(coefficient) * old for coefficient, old in zip(self.b, self._x))
                if self.kind == "biquad":
                    output -= math.fsum(float(coefficient) * old for coefficient, old in zip(self.a[1:], self._y))
                    self._y.appendleft(output)
            if not math.isfinite(output):
                self.reset()
                raise ValueError("滤波结果溢出，已重置状态")
            result[index] = output
        result.setflags(write=False)
        return result


@dataclass(frozen=True)
class Spectrum:
    frequencies: np.ndarray
    values: np.ndarray
    mode: str
    sample_rate: float
    window: str


def spectrum(values, *, sample_rate=None, window="hann", mode="magnitude"):
    """One-sided spectrum. Power is bin mean-square, PSD is per Hz.

    Magnitude uses coherent-gain correction; dB is 20log10(magnitude/1),
    clipped at -300dB. Caller must provide a trustworthy uniform rate.
    """
    if isinstance(sample_rate, bool) or not isinstance(sample_rate, (int, float)) or not math.isfinite(sample_rate) or sample_rate <= 0:
        raise ValueError("FFT需要明确、有限且大于0的采样率")
    data = _samples(values)
    if len(data) < 4:
        raise ValueError("FFT至少需要4点")
    if not np.isfinite(data).all():
        raise ValueError("FFT窗口含缺口或无效数值")
    windows = {"rect": np.ones, "hann": np.hanning, "hamming": np.hamming}
    if window not in windows or mode not in ("magnitude", "power", "psd", "db"):
        raise ValueError("FFT窗口或输出模式无效")
    weights = windows[window](len(data))
    with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
        bins = np.abs(np.fft.rfft(data * weights))
        factor = np.full(len(bins), 2.)
        factor[0] = 1.
        if len(data) % 2 == 0:
            factor[-1] = 1.
        amplitude = bins / weights.sum() * factor
        if mode == "power":
            output = amplitude ** 2 / factor
        elif mode == "psd":
            output = bins ** 2 * factor / (sample_rate * np.sum(weights ** 2))
        elif mode == "db":
            output = 20 * np.log10(np.maximum(amplitude, 1e-15))
        else:
            output = amplitude
    if not np.isfinite(output).all():
        raise ValueError("FFT结果溢出")
    frequencies = np.fft.rfftfreq(len(data), d=1 / sample_rate)
    output.setflags(write=False)
    frequencies.setflags(write=False)
    return Spectrum(frequencies, output, mode, float(sample_rate), window)
