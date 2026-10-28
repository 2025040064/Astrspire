"""One bounded raw sampling history shared by every dashboard consumer."""

from __future__ import annotations

import math
import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from itertools import islice

from .model import SampleFrame, TelemetryEvent


@dataclass(frozen=True, slots=True)
class SampleCursor:
    epoch: int
    sequence: int


@dataclass(frozen=True, slots=True)
class SampleBatch:
    frames: tuple[SampleFrame, ...]
    cursor: SampleCursor
    eviction_gap: int
    reset: bool
    capacity: int


@dataclass(frozen=True, slots=True)
class SampleStatistics:
    received_bytes: int
    parsed: int
    retained: int
    evicted: int
    render_omitted: int
    invalid: int


class SampleStore:
    """Single-writer raw storage, immutable snapshots, separately named loss.

    Capacity eviction is intentional history retirement, not a parser failure.
    Consumers may reduce paint frequency without discarding stored frames.
    """

    def __init__(self, capacity: int = 100_000) -> None:
        self._validate_capacity(capacity)
        self._capacity = capacity
        self._frames: deque[SampleFrame] = deque()
        self._lock = threading.RLock()
        self._received_bytes = self._parsed = self._evicted = 0
        self._render_omitted = self._invalid = 0
        self._epoch = 0

    @staticmethod
    def _validate_capacity(value: int) -> None:
        if type(value) is not int or not 1 <= value <= 100_000:
            raise ValueError("原始采样缓存须为 1–100000 帧")

    @property
    def capacity(self) -> int:
        return self._capacity

    def __len__(self) -> int:
        with self._lock:
            return len(self._frames)

    @property
    def statistics(self) -> SampleStatistics:
        with self._lock:
            return SampleStatistics(self._received_bytes, self._parsed, len(self._frames),
                                    self._evicted, self._render_omitted, self._invalid)

    def record_received_bytes(self, count: int) -> None:
        if type(count) is not int or count < 0:
            raise ValueError("接收字节数必须为非负整数")
        with self._lock:
            self._received_bytes += count

    def record_render_omitted(self, count: int) -> None:
        if type(count) is not int or count < 0:
            raise ValueError("合并绘制帧数必须为非负整数")
        with self._lock:
            self._render_omitted += count

    def append_event(self, event: TelemetryEvent, *, names: tuple[str, ...] = (),
                     received_at: datetime | None = None) -> SampleFrame | None:
        """Keep a valid numeric event without interpreting its display scale."""
        with self._lock:
            if event.error is not None:
                self._invalid += 1
                return None
            if event.samples is None:
                return None
            values = tuple(event.samples)
            if (not values or len(values) > 256
                    or any(isinstance(value, bool) or not isinstance(value, (int, float))
                           or not math.isfinite(value) for value in values)):
                self._invalid += 1
                return None
            arrival = event.received_at or received_at or datetime.now(timezone.utc)
            for stamp in (arrival, event.source_timestamp):
                if stamp is not None and (not isinstance(stamp, datetime)
                                          or stamp.tzinfo is None or stamp.utcoffset() is None):
                    raise ValueError("采样时间必须包含时区")
            frame_names = event.names or names
            frame_names = tuple(frame_names[index] if index < len(frame_names) and frame_names[index] else f"CH{index + 1}"
                                for index in range(len(values)))
            self._parsed += 1
            frame = SampleFrame(self._parsed, arrival, event.source_timestamp, values, frame_names)
            if len(self._frames) == self._capacity:
                self._frames.popleft()
                self._evicted += 1
            self._frames.append(frame)
            return frame

    def snapshot(self) -> tuple[SampleFrame, ...]:
        with self._lock:
            return tuple(self._frames)

    @property
    def latest_frame(self) -> SampleFrame | None:
        with self._lock:
            return self._frames[-1] if self._frames else None

    @property
    def cursor(self) -> SampleCursor:
        with self._lock:
            return SampleCursor(self._epoch, self._parsed)

    def read_since(self, cursor: SampleCursor | None) -> SampleBatch:
        """Read only new retained frames; a clear is a distinct generation.

        Walking backwards avoids scanning the entire deque on each small batch.
        A gap is retired history, not a parser or device loss assertion.
        """
        with self._lock:
            if cursor is not None and (not isinstance(cursor, SampleCursor)
                    or type(cursor.epoch) is not int or type(cursor.sequence) is not int
                    or cursor.epoch < 0 or cursor.sequence < 0):
                raise ValueError("采样游标无效")
            reset = cursor is None or cursor.epoch != self._epoch
            sequence = 0 if reset else cursor.sequence
            if sequence > self._parsed:
                raise ValueError("采样游标超前")
            oldest = self._frames[0].sequence if self._frames else self._parsed + 1
            gap = max(0, oldest - sequence - 1)
            count = min(len(self._frames), self._parsed - sequence)
            frames = tuple(reversed(tuple(islice(reversed(self._frames), count))))
            return SampleBatch(frames, SampleCursor(self._epoch, self._parsed), gap, reset, self._capacity)

    def set_capacity(self, capacity: int) -> None:
        self._validate_capacity(capacity)
        with self._lock:
            self._capacity = capacity
            while len(self._frames) > capacity:
                self._frames.popleft()
                self._evicted += 1

    def clear(self) -> None:
        """Begin a new explicit session; immutable old snapshots stay valid."""
        with self._lock:
            self._frames.clear()
            self._epoch += 1
            self._received_bytes = self._parsed = self._evicted = 0
            self._render_omitted = self._invalid = 0
