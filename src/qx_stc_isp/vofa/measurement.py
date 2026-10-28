"""Pure, overflow-aware statistics over full immutable display samples."""

from dataclasses import dataclass
import math
import threading

import numpy as np
from PySide6.QtCore import QThread, Signal

from .projection import PlotBuffer


@dataclass(frozen=True, slots=True)
class MeasurementSummary:
    channel: int
    count: int
    minimum: float | None
    maximum: float | None
    mean: float | None
    peak_to_peak: float | None
    rms: float | None
    notice: str = ""


def measure_window(buffer: PlotBuffer, view_range: tuple[float, float],
                   visible: tuple[bool, ...], maximum: int,
                   *, cancelled=None) -> tuple[MeasurementSummary, ...] | None:
    """Use inclusive sample-index bounds, retaining raw resolution and gaps.

    Cancellation is checked between channels. NaNs are counted as missing,
    not as zero. Scaling before arithmetic avoids overflow in mean/RMS.
    """
    if (not isinstance(buffer, PlotBuffer) or len(view_range) != 2
            or not all(math.isfinite(value) for value in view_range)
            or view_range[0] > view_range[1]):
        raise ValueError("测量窗口无效")
    if type(maximum) is not int or not 1 <= maximum <= 100000:
        raise ValueError("测量缓存上限无效")
    indices = buffer.indices[-maximum:]
    start = int(np.searchsorted(indices, view_range[0], side="left"))
    end = int(np.searchsorted(indices, view_range[1], side="right"))
    results = []
    for channel, source in enumerate(buffer.values):
        if cancelled is not None and cancelled():
            return None
        if channel >= len(visible) or not visible[channel]:
            continue
        window = source[-maximum:][start:end]
        values = window[np.isfinite(window)]
        if not values.size:
            results.append(MeasurementSummary(channel, 0, None, None, None, None, None))
            continue
        low, high = float(values.min()), float(values.max())
        scale = max(abs(low), abs(high))
        normalized = values / scale if scale else values
        mean = max(-1., min(1., float(np.mean(normalized)))) * scale
        rms = math.sqrt(max(0., min(1., float(np.mean(normalized * normalized))))) * scale
        span = high - low
        notice = "" if math.isfinite(span) else "峰峰值超出浮点范围"
        results.append(MeasurementSummary(channel, int(values.size), low, high, mean,
                                          span if math.isfinite(span) else None, rms, notice))
    return tuple(results)


@dataclass(frozen=True, slots=True)
class MeasurementJob:
    token: int
    buffer: PlotBuffer
    view_range: tuple[float, float]
    visible: tuple[bool, ...]
    maximum: int


class MeasurementWorker(QThread):
    """One running job and one latest pending job, with no widget references."""

    ready = Signal(object)
    failed = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._condition = threading.Condition()
        self._pending = None
        self._latest = 0
        self._active = True
        self._stopping = False

    def request(self, job: MeasurementJob):
        with self._condition:
            if self._stopping or not self._active:
                return
            self._pending, self._latest = job, job.token
            self._condition.notify()

    def set_active(self, active):
        with self._condition:
            self._active = active
            if not active:
                self._pending = None
            self._condition.notify()

    def _cancelled(self, token):
        with self._condition:
            return self._stopping or not self._active or token != self._latest

    def stop(self, timeout=2000):
        with self._condition:
            self._stopping = True
            self._pending = None
            self._condition.notify()
        return self.wait(timeout)

    def run(self):
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._stopping or (self._active and self._pending is not None))
                if self._stopping:
                    return
                job, self._pending = self._pending, None
            try:
                summaries = measure_window(job.buffer, job.view_range, job.visible, job.maximum,
                                           cancelled=lambda: self._cancelled(job.token))
            except (ValueError, TypeError, OverflowError) as exc:
                if not self._cancelled(job.token):
                    self.failed.emit((job.token, str(exc)))
            else:
                if summaries is not None and not self._cancelled(job.token):
                    self.ready.emit((job.token, summaries))
