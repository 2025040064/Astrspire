"""Explicit CSV capture of decoded samples, independent of any transport."""

from __future__ import annotations

import csv
import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TextIO

from .model import TelemetryEvent


@dataclass(frozen=True, slots=True)
class RecorderStatistics:
    accepted: int
    saved: int
    unsaved: int
    queued: int
    queued_bytes: int
    rejected: int
    active: bool
    error: str | None


class AsyncRecorder:
    """Bounded nonblocking submission; all sink I/O belongs to one writer.

    ``saved`` means the sink's record and flush completed, not an fsync or a
    device acknowledgement. On overload/failure, queued entries are counted as
    unsaved and that recording stops; acquisition remains independent.
    """

    def __init__(self, sink, *, max_queue_records: int = 4096,
                 max_queue_bytes: int = 8 * 1024 * 1024) -> None:
        for limit in (max_queue_records, max_queue_bytes):
            if type(limit) is not int or limit <= 0:
                raise ValueError("记录队列上限必须为正整数")
        self.sink = sink
        self.path = getattr(sink, "path", None)
        self.max_queue_records = max_queue_records
        self.max_queue_bytes = max_queue_bytes
        self._condition = threading.Condition()
        self._queue: deque[tuple[tuple, dict, int]] = deque()
        self._queued_bytes = 0
        self._accepted = self._saved = self._unsaved = self._rejected = 0
        self._closing = False
        self._error: str | None = None
        self._thread: threading.Thread | None = None
        self._finished = threading.Event()

    @property
    def statistics(self) -> RecorderStatistics:
        with self._condition:
            return RecorderStatistics(self._accepted, self._saved, self._unsaved,
                                      len(self._queue), self._queued_bytes, self._rejected,
                                      self._thread is not None and not self._closing and self._error is None,
                                      self._error)

    @property
    def is_open(self) -> bool:
        return self.statistics.active

    def open(self) -> None:
        """Start asynchronously; inspect statistics.error for opening failures."""
        with self._condition:
            if self._thread is not None:
                raise RuntimeError("记录线程只能启动一次")
            self._thread = threading.Thread(target=self._run, name="Astrspire recorder", daemon=True)
            self._thread.start()

    @staticmethod
    def _size(value) -> int:
        if isinstance(value, bytes):
            return len(value)
        if isinstance(value, str):
            return len(value.encode("utf-8"))
        if isinstance(value, TelemetryEvent):
            return (len(value.raw) + len(value.image or b"") + len(value.samples or ()) * 8
                    + sum(len(name.encode("utf-8")) for name in value.names) + 128)
        return 32

    def _fail_locked(self, message: str) -> None:
        if self._error is None:
            self._error = message
        elif message not in self._error:
            self._error += f"；{message}"
        self._closing = True
        self._unsaved += len(self._queue)
        self._queue.clear()
        self._queued_bytes = 0
        self._condition.notify_all()

    def record(self, *args, **kwargs) -> bool:
        """Queue immutable event/string/bytes arguments without disk access."""
        size = sum(self._size(value) for value in args) + sum(self._size(value) for value in kwargs.values())
        with self._condition:
            if self._thread is None:
                raise RuntimeError("记录线程尚未启动")
            if self._closing:
                self._rejected += 1
                self._unsaved += 1
                return False
            if (len(self._queue) >= self.max_queue_records
                    or self._queued_bytes + size > self.max_queue_bytes):
                self._rejected += 1
                self._unsaved += 1
                self._fail_locked("记录队列已满，已停止此记录；未保存数量请查看记录统计")
                return False
            self._queue.append((args, dict(kwargs), size))
            self._queued_bytes += size
            self._accepted += 1
            self._condition.notify()
            return True

    def close(self, timeout: float = 0) -> bool:
        """Stop submissions and drain accepted records; default never waits."""
        with self._condition:
            self._closing = True
            self._condition.notify_all()
        return self.wait_closed(timeout)

    def wait_closed(self, timeout: float = 0) -> bool:
        if self._thread is None:
            return True
        return self._finished.wait(max(0, timeout))

    def _run(self) -> None:
        try:
            self.sink.open()
            self.sink.flush()
            while True:
                with self._condition:
                    while not self._queue and not self._closing:
                        self._condition.wait()
                    if not self._queue:
                        break
                    args, kwargs, size = self._queue.popleft()
                    self._queued_bytes -= size
                try:
                    self.sink.record(*args, **kwargs)
                    self.sink.flush()
                except Exception as exc:
                    with self._condition:
                        self._unsaved += 1
                        self._fail_locked(f"记录写入失败：{exc}")
                    break
                with self._condition:
                    self._saved += 1
        except Exception as exc:
            with self._condition:
                self._fail_locked(f"记录打开失败：{exc}")
        finally:
            try:
                self.sink.close()
            except Exception as exc:
                with self._condition:
                    self._fail_locked(f"记录关闭失败：{exc}")
            with self._condition:
                self._closing = True
            self._finished.set()


class CsvRecorder:
    """Record one row per channel with a stable schema.

    `open` uses exclusive creation to avoid silently overwriting old captures.
    No recording starts merely by constructing the object.
    """

    def __init__(self, path: str | Path, channel_names: tuple[str, ...] = ()) -> None:
        self.path = Path(path)
        self.channel_names = channel_names
        self._stream: TextIO | None = None
        self._writer: csv.writer | None = None

    @property
    def is_open(self) -> bool:
        return self._stream is not None

    def open(self) -> None:
        if self._stream is not None:
            raise RuntimeError("CSV 记录已开始")
        self._stream = self.path.open("x", newline="", encoding="utf-8-sig")
        self._writer = csv.writer(self._stream)
        self._writer.writerow(("timestamp_utc", "channel_index", "channel_name", "value"))

    def record(self, event: TelemetryEvent, *, at: datetime | None = None) -> int:
        if self._writer is None:
            raise RuntimeError("CSV 记录尚未开始")
        if event.samples is None or event.error is not None:
            return 0
        stamp = event.source_timestamp or at or event.received_at or datetime.now(timezone.utc)
        if stamp.tzinfo is None:
            raise ValueError("记录时间必须包含时区")
        timestamp = stamp.astimezone(timezone.utc).isoformat(timespec="microseconds")
        names = event.names or self.channel_names
        for index, value in enumerate(event.samples):
            name = names[index] if index < len(names) and names[index] else f"CH{index + 1}"
            self._writer.writerow((timestamp, index, name, repr(value)))
        return len(event.samples)

    def flush(self) -> None:
        if self._stream is not None:
            self._stream.flush()

    def close(self) -> None:
        stream, self._stream = self._stream, None
        self._writer = None
        if stream is not None:
            stream.close()

    def __enter__(self) -> CsvRecorder:
        self.open()
        return self

    def __exit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        self.close()
