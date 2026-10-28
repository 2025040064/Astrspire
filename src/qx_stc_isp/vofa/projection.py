"""Incremental display columns, independent of widgets and device transports."""

from collections import deque
from dataclasses import dataclass, field
import math
import threading

import numpy as np

from PySide6.QtCore import QThread, Signal

from .channel_settings import ChannelDisplay
from .sample_store import SampleCursor, SampleStore


@dataclass(frozen=True, slots=True)
class PlotBuffer:
    """One immutable numeric view shared by all consumers of a projection."""

    cursor: SampleCursor
    generation: int
    values: np.ndarray
    indices: np.ndarray
    _render_cache: dict = field(default_factory=dict, compare=False, repr=False)

    def render_series(self, channel, left, right, pixels, max_samples):
        """Shared finite peak envelopes; full arrays remain measurement truth.

        Buckets containing gaps keep every original point, never join a gap.
        Cache only up to three display windows, never another raw history.
        """
        key = (channel, left, right, max(1, int(pixels)), max_samples)
        cached = self._render_cache.get(key)
        if cached is not None:
            return cached
        start = max(0, len(self.indices) - max_samples, int(np.searchsorted(self.indices, left)) - 1)
        stop = min(len(self.indices), int(np.searchsorted(self.indices, right, side="right")) + 1)
        x, y = self.indices[start:stop], self.values[channel, start:stop]
        stride = max(1, math.ceil(len(y) / max(1, int(pixels))))
        if stride > 4:
            count = len(y) // stride
            blocks = y[:count * stride].reshape(count, stride)
            bases = np.arange(count) * stride
            finite = np.isfinite(blocks).all(axis=1)
            good = bases[finite]
            gap_indices = (bases[~finite, None] + np.arange(stride)).ravel()
            selected = np.concatenate((good, good + stride - 1,
                                       good + blocks[finite].argmin(axis=1), good + blocks[finite].argmax(axis=1),
                                       gap_indices,
                                       np.arange(count * stride, len(y))))
            selected = np.unique(selected)
            x, y = x[selected], y[selected]
            x.setflags(write=False)
            y.setflags(write=False)
        if len(self._render_cache) >= 48:
            self._render_cache.clear()
        self._render_cache[key] = (x, y)
        return x, y

    @classmethod
    def from_columns(cls, columns, cursor: SampleCursor, generation: int):
        values = np.array(columns, dtype=np.float64, order="C", copy=True)
        if not len(columns):
            values = np.empty((0, 0), dtype=np.float64)
        if (values.ndim != 2 or values.shape[0] > 16 or values.shape[1] > 100000
                or values.shape[1] > cursor.sequence or np.isinf(values).any()):
            raise ValueError("显示数组尺寸或数值无效")
        indices = np.arange(cursor.sequence - values.shape[1], cursor.sequence, dtype=np.float64)
        values.setflags(write=False)
        indices.setflags(write=False)
        return cls(cursor, generation, values, indices)


def display_envelope(values, width):
    """Pixel buckets preserve endpoints and chronological extrema, with gaps."""
    stride = max(1, math.ceil(len(values) / max(1, width)))
    for start in range(0, len(values), stride):
        run = []
        def emit_run():
            if run:
                indices = {0, len(run) - 1,
                           min(range(len(run)), key=lambda i: run[i][1]),
                           max(range(len(run)), key=lambda i: run[i][1])}
                return (run[i] for i in sorted(indices))
            return ()
        for offset, value in enumerate(values[start:start + stride], start):
            if math.isfinite(value):
                run.append((offset, value))
            else:
                yield from emit_run()
                run = []
                yield offset, math.nan
        yield from emit_run()


@dataclass(frozen=True, slots=True)
class ProjectionSnapshot:
    cursor: SampleCursor
    generation: int
    columns: tuple[tuple[float, ...], ...]
    projected_frames: int
    errors: tuple[str, ...] = ()
    bounds: tuple = ()
    envelopes: tuple = ()
    plot_buffer: PlotBuffer | None = None


class ProjectionBuilder:
    """Single-writer projection; immutable output is shared by all visible cards.

    No result history/cache is kept. Paused consumers own their frozen snapshot.
    Missing channels and overflow stay as gaps, never compressed time indices.
    """

    def __init__(self):
        self._cursor = None
        self._config = None
        self._capacity = 0
        self._columns = []
        self._length = 0
        self._store = None
        self._block_cache = {}

    def build(self, store: SampleStore, config: tuple[ChannelDisplay, ...],
              generation: int) -> ProjectionSnapshot:
        config = tuple(config[:16])
        if any(not isinstance(item, ChannelDisplay) for item in config):
            raise ValueError("显示投影配置无效")
        changed = store is not self._store or config != self._config or store.capacity != self._capacity
        batch = store.read_since(None if changed else self._cursor)
        if changed or batch.reset or batch.eviction_gap:
            if not batch.reset:
                batch = store.read_since(None)
            self._columns = []
            self._length = 0
            self._block_cache.clear()
        self._capacity = batch.capacity
        errors = set()
        for frame in batch.frames:
            count = min(len(config), len(frame.values))
            while len(self._columns) < count:
                self._columns.append(deque([math.nan] * self._length, maxlen=batch.capacity))
            for index, column in enumerate(self._columns):
                try:
                    value = config[index].apply(frame.values[index]) if index < count else math.nan
                except ValueError as exc:
                    value = math.nan
                    if len(errors) < 16:
                        errors.add(str(exc))
                column.append(value)
            self._length = min(batch.capacity, self._length + 1)
        self._cursor, self._config = batch.cursor, config
        self._store = store
        columns = tuple(tuple(column) for column in self._columns)
        bounds, envelopes = self._summarize(columns, batch.cursor.sequence)
        return ProjectionSnapshot(batch.cursor, generation, columns, len(batch.frames),
                                  tuple(sorted(errors)), bounds, envelopes,
                                  PlotBuffer.from_columns(columns, batch.cursor, generation))

    def _summarize(self, columns, sequence):
        """Reuse aligned 512-frame blocks; only the two edge blocks change.

        At most 16*197 blocks, each with <=64 vertices, are retained (under
        64 MiB even with Python object overhead). Pathological gap density
        falls back to raw drawing instead of growing a second full history.
        """
        active, bounds, envelopes = {}, [], []
        for channel, values in enumerate(columns):
            first = sequence - len(values)
            points, limits = [], []
            fallback = False
            for block in range(first // 512, (sequence + 511) // 512):
                start, end = max(first, block * 512), min(sequence, (block + 1) * 512)
                key = (channel, block)
                cached = self._block_cache.get(key)
                if cached is None or cached[:2] != (start, end):
                    chunk = values[start - first:end - first]
                    finite = tuple(value for value in chunk if math.isfinite(value))
                    limit = (min(finite), max(finite)) if finite else None
                    envelope = tuple((start + offset, value) for offset, value in display_envelope(chunk, 1))
                    if len(envelope) > 64:
                        envelope = None
                    cached = (start, end, envelope, limit)
                active[key] = cached
                if cached[2] is None:
                    fallback = True
                else:
                    points.extend(cached[2])
                if cached[3] is not None:
                    limits.append(cached[3])
            bounds.append((min(pair[0] for pair in limits), max(pair[1] for pair in limits)) if limits else None)
            envelopes.append(None if fallback else tuple(points))
        self._block_cache = active
        return tuple(bounds), tuple(envelopes)


class ProjectionWorker(QThread):
    """One active build and one latest pending request, never an unbounded queue."""

    ready = Signal(object)
    failed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._condition = threading.Condition()
        self._pending = None
        self._stopping = False

    def request(self, store, config, generation, names):
        with self._condition:
            if self._stopping:
                return
            self._pending = (store, tuple(config), generation, tuple(names))
            self._condition.notify()

    def stop(self, timeout=2000):
        with self._condition:
            self._stopping = True
            self._pending = None
            self._condition.notify()
        return self.wait(timeout)

    def run(self):
        builder = ProjectionBuilder()
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._stopping or self._pending is not None)
                if self._stopping:
                    return
                store, config, generation, names = self._pending
                self._pending = None
            try:
                snapshot = builder.build(store, config, generation)
            except (ValueError, TypeError, OverflowError) as exc:
                self.failed.emit(f"显示投影失败：{exc}")
            else:
                self.ready.emit((store, snapshot, names))
