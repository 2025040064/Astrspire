"""Bounded playback of this application's long-form sample CSV.

This is an independent file format adapter, not a VOFA+ project/CSV decoder.
Construction never opens files, serial ports, or network connections.
"""

from __future__ import annotations

import csv
import math
import stat
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TextIO

from PySide6.QtCore import QObject, Signal, Slot

from .model import TelemetryEvent

CSV_HEADER = ("timestamp_utc", "channel_index", "channel_name", "value")
MAX_FILE_BYTES = 128 * 1024 * 1024
MAX_LINE_CHARS = 8192
MAX_CHANNELS = 256
MAX_BATCH_FRAMES = 64
BATCH_SECONDS = 0.05


class CsvReplayError(ValueError):
    """The selected file is not a supported, bounded sample recording."""


@dataclass(frozen=True, slots=True)
class CsvReplayFrame:
    timestamp: datetime
    samples: tuple[float, ...]
    names: tuple[str, ...]
    bytes_read: int = 0
    file_size: int = 0


class _BoundedLines:
    def __init__(self, stream: TextIO) -> None:
        self.stream = stream
        self.bytes_read = 0

    def __iter__(self) -> _BoundedLines:
        return self

    def __next__(self) -> str:
        line = self.stream.readline(MAX_LINE_CHARS + 1)
        if not line:
            raise StopIteration
        if len(line) > MAX_LINE_CHARS:
            raise CsvReplayError("CSV 单行超过 8192 字符上限")
        self.bytes_read += len(line.encode("utf-8"))
        if self.bytes_read > MAX_FILE_BYTES:
            raise CsvReplayError("CSV 超过 128 MiB 上限；文件可能在回放时发生增长")
        return line


def _row_fields(row: list[str], line_number: int) -> tuple[datetime, int, str, float]:
    prefix = f"CSV 第 {line_number} 行："
    if len(row) != 4:
        raise CsvReplayError(prefix + "应有时间、通道序号、通道名和数值四列")
    stamp_text, index_text, name, value_text = row
    if len(stamp_text) > 64 or len(index_text) > 3 or len(name) > 128 or len(value_text) > 64:
        raise CsvReplayError(prefix + "字段超过长度上限")
    try:
        stamp = datetime.fromisoformat(stamp_text)
    except ValueError as exc:
        raise CsvReplayError(prefix + "时间格式无效") from exc
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise CsvReplayError(prefix + "时间必须带时区")
    try:
        stamp = stamp.astimezone(timezone.utc)
    except (ValueError, OverflowError) as exc:
        raise CsvReplayError(prefix + "时间超出支持范围") from exc
    try:
        index = int(index_text)
    except ValueError as exc:
        raise CsvReplayError(prefix + "通道序号无效") from exc
    if str(index) != index_text or not 0 <= index < MAX_CHANNELS:
        raise CsvReplayError(prefix + "通道序号必须为 0–255 的规范整数")
    try:
        value = float(value_text)
    except ValueError as exc:
        raise CsvReplayError(prefix + "数值无效") from exc
    if not math.isfinite(value):
        raise CsvReplayError(prefix + "不接受 NaN 或无穷大")
    return stamp, index, name, value


def iter_csv_frames(path: str | Path) -> Iterator[CsvReplayFrame]:
    """Yield at most one 256-channel frame at a time; close on exhaustion/close.

    Channel zero starts a new frame, even when timestamps are identical. A
    caller stopping iteration early must close the generator; the worker does.
    """
    source = Path(path)
    source_info = source.stat()
    if not stat.S_ISREG(source_info.st_mode):
        raise CsvReplayError("CSV 回放只接受普通文件")
    file_size = source_info.st_size
    if file_size > MAX_FILE_BYTES:
        raise CsvReplayError("CSV 文件超过 128 MiB 上限")
    with source.open("r", encoding="utf-8-sig", newline="") as stream:
        lines = _BoundedLines(stream)
        reader = csv.reader(lines, strict=True)
        try:
            header = next(reader)
        except StopIteration as exc:
            raise CsvReplayError("CSV 文件为空") from exc
        if tuple(header) != CSV_HEADER:
            raise CsvReplayError("CSV 表头不匹配；请选择本应用导出的采样长表")
        stamp: datetime | None = None
        samples: list[float] = []
        names: list[str] = []
        frame_position = lines.bytes_read
        for row in reader:
            at, index, name, value = _row_fields(row, reader.line_num)
            if index == 0:
                if stamp is not None:
                    if at < stamp:
                        raise CsvReplayError(f"CSV 第 {reader.line_num} 行：采样时间倒退")
                    yield CsvReplayFrame(stamp, tuple(samples), tuple(names), frame_position, file_size)
                stamp, samples, names = at, [], []
            elif stamp is None:
                raise CsvReplayError(f"CSV 第 {reader.line_num} 行：每帧必须从通道 0 开始")
            if index != len(samples):
                raise CsvReplayError(f"CSV 第 {reader.line_num} 行：通道必须从 0 连续递增，不能缺失或重复")
            if at != stamp:
                raise CsvReplayError(f"CSV 第 {reader.line_num} 行：同一帧的时间必须一致")
            samples.append(value)
            names.append(name)
            frame_position = lines.bytes_read
        if stamp is None:
            raise CsvReplayError("CSV 没有采样数据")
        yield CsvReplayFrame(stamp, tuple(samples), tuple(names), frame_position, file_size)


class CsvReplayWorker(QObject):
    """Play a recording on a QThread, without any device or TX capability.

    Pausing suspends the playback clock and file progression, rather than only
    freezing a chart. Samples are delivered in bounded, rate-limited batches.
    """

    events = Signal(object)
    status = Signal(str, str)
    finished = Signal()
    progress = Signal(int)
    raw = Signal(bytes)  # Compatibility only: playback never emits hardware RX.
    transmitted = Signal(bytes)  # Compatibility only: playback never sends.
    names_changed = Signal(object)
    generation_changed = Signal(int)
    generation_events = Signal(int, object)
    generation_raw = Signal(int, bytes)  # CSV has no raw device data.
    generation_names_changed = Signal(int, object)
    timeline_changed = Signal(float, float, int)

    def __init__(self, path: str | Path, speed: float = 1.0, loop: bool = False,
                 *, await_ack: bool = False) -> None:
        super().__init__()
        if isinstance(speed, bool) or not isinstance(speed, (int, float)) or not math.isfinite(speed) or not 0.01 <= speed <= 1000:
            raise ValueError("CSV 回放倍率必须在 0.01–1000 之间")
        if type(loop) is not bool:
            raise ValueError("CSV 循环设置必须为布尔值")
        if type(await_ack) is not bool:
            raise ValueError("批次应答设置必须为布尔值")
        self.path = Path(path)
        self.speed = float(speed)
        self.loop = loop
        self._condition = threading.Condition()
        self._stop = False
        self._started = False
        self._paused = False
        self._pause_at: float | None = None
        self._paused_seconds = 0.0
        self._channel_names: tuple[str, ...] = ()
        self._last_batch_at = 0.0
        self._last_progress = -1
        self._await_ack, self._batch_pending = await_ack, False
        self._generation = 0
        self._seek_request: float | None = None
        self._position_seconds = 0.0
        self._duration_seconds = 0.0
        self._rate_changed = False

    @property
    def generation(self):
        with self._condition:
            return self._generation

    @property
    def position_seconds(self):
        with self._condition:
            return self._position_seconds

    @property
    def duration_seconds(self):
        with self._condition:
            return self._duration_seconds

    def seek_seconds(self, seconds):
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or seconds < 0:
            raise CsvReplayError("CSV定位需为非负有限秒数")
        with self._condition:
            if self._stop:
                raise CsvReplayError("CSV回放已结束，请重新打开记录")
            self._generation += 1
            self._seek_request = float(seconds)
            self._batch_pending = False
            self._condition.notify_all()
            generation = self._generation
        self.generation_changed.emit(generation)
        return generation

    def set_speed(self, speed):
        if isinstance(speed, bool) or not isinstance(speed, (int, float)) or not math.isfinite(speed) or not 0.01 <= speed <= 1000:
            raise CsvReplayError("CSV回放倍率必须在0.01–1000之间")
        with self._condition:
            self.speed = float(speed)
            self._rate_changed = True
            self._condition.notify_all()

    def set_loop(self, loop):
        if type(loop) is not bool:
            raise CsvReplayError("CSV循环设置必须为布尔值")
        with self._condition:
            self.loop = loop
            self._condition.notify_all()

    @property
    def can_send(self) -> bool:
        return False

    @property
    def paused(self) -> bool:
        with self._condition:
            return self._paused

    @property
    def channel_names(self) -> tuple[str, ...]:
        with self._condition:
            return self._channel_names

    def enqueue_send(self, _payload: bytes) -> int:
        raise ValueError("CSV 回放没有设备连接，不能发送数据")

    def clear_pending(self) -> int:
        return 0

    def stop(self) -> None:
        with self._condition:
            self._stop = True
            self._condition.notify_all()

    def ack_batch(self) -> None:
        with self._condition:
            self._batch_pending = False
            self._condition.notify_all()

    def set_paused(self, paused: bool) -> None:
        if type(paused) is not bool:
            raise ValueError("暂停设置必须为布尔值")
        with self._condition:
            if paused == self._paused or self._stop:
                return
            now = time.monotonic()
            if paused:
                self._pause_at = now
            elif self._pause_at is not None:
                self._paused_seconds += now - self._pause_at
                self._pause_at = None
            self._paused = paused
            notify = self._started
            self._condition.notify_all()
        if notify:
            self.status.emit("paused" if paused else "playback", "CSV 回放已暂停" if paused else "CSV 回放继续")

    def _clock(self) -> float:
        now = time.monotonic()
        return now - self._paused_seconds - (now - self._pause_at if self._pause_at is not None else 0.0)

    def _wait_due(self, deadline: float, speed: float | None = None) -> bool:
        with self._condition:
            rate = self.speed if speed is None else speed
            while not self._stop and self._seek_request is None:
                if rate != self.speed:
                    deadline = self._clock() + max(0.0, deadline - self._clock()) * rate / self.speed
                    rate = self.speed
                remaining = deadline - self._clock()
                if not self._paused and remaining <= 0:
                    return True
                self._condition.wait(0.05 if self._paused else min(0.05, max(0.0001, remaining)))
        return False

    def _emit_batch(self, frames: list[CsvReplayFrame]) -> bool:
        if not frames:
            return True
        with self._condition:
            while not self._stop and self._seek_request is None:
                remaining = self._last_batch_at + BATCH_SECONDS - time.monotonic()
                if not self._paused and remaining <= 0 and not self._batch_pending:
                    break
                self._condition.wait(0.05 if self._paused or self._batch_pending else min(0.05, max(0.0001, remaining)))
            if self._stop or self._seek_request is not None:
                return False
            self._last_batch_at = time.monotonic()
            self._batch_pending = self._await_ack
            changed = frames[0].names != self._channel_names
            self._channel_names = frames[0].names
            generation = self._generation
        if changed:
            self.generation_names_changed.emit(generation, frames[0].names)
            self.names_changed.emit(frames[0].names)
        events = [
            TelemetryEvent(raw=b"", samples=frame.samples, names=frame.names,
                           received_at=datetime.now(timezone.utc), source_timestamp=frame.timestamp)
            for frame in frames
        ]
        self.generation_events.emit(generation, events)
        self.events.emit(events)
        position = min(99, int(frames[-1].bytes_read * 100 / max(1, frames[-1].file_size)))
        if position != self._last_progress:
            self._last_progress = position
            self.progress.emit(position)
        frames.clear()
        return True

    @Slot()
    def run(self) -> None:
        with self._condition:
            if self._started:
                raise RuntimeError("CsvReplayWorker cannot be run twice")
            self._started = True
            self._paused_seconds = 0.0
            self._pause_at = time.monotonic() if self._paused else None
        iterator = None
        terminal_kind, terminal_message = "stopped", "CSV 回放已停止"
        try:
            with self._condition:
                if self._stop:
                    return
            self.status.emit("playback", f"CSV 文件回放：{self.path.name} · {self.speed:g}×")
            from .replay_index import RecordingIndex
            index = RecordingIndex.build(self.path, cancel=_ReplayCancellation(self))
            with self._condition:
                self._duration_seconds = index.duration_seconds
            rounds = 0
            offset = 0.0
            while True:
                with self._condition:
                    if self._stop:
                        break
                    if self._seek_request is not None:
                        offset = min(self._seek_request, self._duration_seconds)
                        self._seek_request = None
                    self._position_seconds = offset
                    self._batch_pending = False
                    self._channel_names = ()
                    self._rate_changed = False
                    speed = self.speed
                    origin = self._clock()
                    generation = self._generation
                self.timeline_changed.emit(offset, self._duration_seconds, generation)
                iterator = iter_csv_frames(self.path)
                self._last_progress = -1
                first_stamp = None
                pending: list[CsvReplayFrame] = []
                cancelled = False
                for frame in iterator:
                    with self._condition:
                        if self._stop or self._seek_request is not None:
                            cancelled = True
                            break
                    if first_stamp is None:
                        first_stamp = frame.timestamp
                    elapsed = (frame.timestamp - first_stamp).total_seconds()
                    if elapsed < offset:
                        continue
                    with self._condition:
                        if self._rate_changed:
                            offset = self._position_seconds
                            origin = self._clock()
                            speed = self.speed
                            self._rate_changed = False
                    due = origin + (elapsed - offset) / speed
                    with self._condition:
                        ahead = due > self._clock()
                    # Deliver already-due samples before waiting for a distant
                    # next timestamp, so an otherwise quiet file stays visible.
                    if ahead and not self._emit_batch(pending):
                        cancelled = True
                        break
                    if not self._wait_due(due, speed):
                        cancelled = True
                        break
                    # The matching names signal is queued immediately before
                    # this batch. Keep a single immutable name snapshot for
                    # every sample in it, including equal-time input frames.
                    if pending and pending[-1].names != frame.names:
                        if not self._emit_batch(pending):
                            cancelled = True
                            break
                    pending.append(frame)
                    with self._condition:
                        self._position_seconds = elapsed
                    self.timeline_changed.emit(elapsed, self._duration_seconds, self.generation)
                    if len(pending) >= MAX_BATCH_FRAMES or time.monotonic() - self._last_batch_at >= BATCH_SECONDS:
                        if not self._emit_batch(pending):
                            cancelled = True
                            break
                if cancelled or not self._emit_batch(pending):
                    iterator.close()
                    iterator = None
                    with self._condition:
                        if self._seek_request is not None and not self._stop:
                            continue
                    break
                iterator.close()
                iterator = None
                self.progress.emit(100)
                if not self.loop:
                    terminal_kind, terminal_message = "completed", "CSV 回放完成"
                    break
                rounds += 1
                offset = 0.0
                with self._condition:
                    self._generation += 1
                self.generation_changed.emit(self.generation)
                self.status.emit("playback", f"CSV 循环回放 · 第 {rounds + 1} 轮")
                self.progress.emit(0)
                with self._condition:
                    self._condition.wait(0.01)
        except (OSError, UnicodeError, csv.Error, ValueError) as exc:
            with self._condition:
                if not self._stop:
                    terminal_kind, terminal_message = "error", f"CSV 回放失败：{exc}"
        except Exception as exc:
            terminal_kind, terminal_message = "error", f"CSV 回放异常：{type(exc).__name__}: {exc}"
        finally:
            if iterator is not None:
                try:
                    iterator.close()
                except Exception as exc:
                    terminal_kind, terminal_message = "error", f"CSV 文件关闭失败：{exc}"
            with self._condition:
                self._stop = True
                self._condition.notify_all()
            self.status.emit(terminal_kind, terminal_message)
            self.finished.emit()


class _ReplayCancellation:
    def __init__(self, worker):
        self.worker = worker

    def is_set(self):
        with self.worker._condition:
            return self.worker._stop
