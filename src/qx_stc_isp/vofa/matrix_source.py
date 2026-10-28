"""Numeric Demo CSV input: rows are frames, columns are finite channels.

This is separate from timestamped long-format capture files. The installed
application's Demo supplied numeric CSV to its engines; strict validation here
is an independent choice, not a reproduction of failed conversions to zero.
"""

from __future__ import annotations

import math
import os
import re
import stat
import struct
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot

from .model import TelemetryEvent

MAX_FILE_BYTES = 128 * 1024 * 1024
MAX_LINE_BYTES = 8192
MAX_DIRECTORY_FILES = 10000
MAX_DIRECTORY_ENTRIES = 50000


@dataclass(frozen=True, slots=True)
class MatrixRow:
    samples: tuple[float, ...]
    raw: bytes
    bytes_read: int
    file_size: int


def iter_matrix_rows(path: str | Path, *, as_float32: bool = False):
    if type(as_float32) is not bool:
        raise ValueError("float32设置必须为布尔值")
    source = Path(path)
    if source.is_symlink() or source.is_junction():
        raise ValueError("数值CSV不能是符号链接或目录联接")
    info = source.stat()
    if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_FILE_BYTES:
        raise ValueError("数值CSV必须为非空普通文件且不超过128MiB")
    consumed = 0
    with source.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        if not stat.S_ISREG(opened.st_mode) or not 0 < opened.st_size <= MAX_FILE_BYTES:
            raise ValueError("打开后的CSV类型或大小无效")
        if info.st_ino and opened.st_ino and (info.st_dev, info.st_ino) != (opened.st_dev, opened.st_ino):
            raise ValueError("CSV在打开时被替换")
        line_number = 0
        while True:
            raw = stream.readline(MAX_LINE_BYTES + 1)
            if not raw:
                break
            line_number += 1
            consumed += len(raw)
            if consumed > MAX_FILE_BYTES or len(raw) > MAX_LINE_BYTES:
                raise ValueError(f"第{line_number}行或文件超过大小限制")
            text = raw.decode("utf-8-sig" if line_number == 1 else "utf-8").strip()
            if not text:
                continue
            if '"' in text or "'" in text:
                raise ValueError(f"第{line_number}行不接受引号或文本表头")
            fields = text.split(",")
            if not 1 <= len(fields) <= 16 or any(not field.strip() for field in fields):
                raise ValueError(f"第{line_number}行需要1–16列非空数值")
            try:
                samples = tuple(float(field) for field in fields)
                if as_float32:
                    samples = struct.unpack(f"<{len(samples)}f", struct.pack(f"<{len(samples)}f", *samples))
            except (ValueError, OverflowError, struct.error) as exc:
                raise ValueError(f"第{line_number}行不是有效数值矩阵：{exc}") from exc
            if not all(math.isfinite(value) for value in samples):
                raise ValueError(f"第{line_number}行包含无穷或NaN")
            yield MatrixRow(samples, raw, consumed, opened.st_size)


class MatrixReplayWorker(QObject):
    events = Signal(object)
    raw = Signal(bytes)  # File input does not pretend to be hardware RX.
    transmitted = Signal(bytes)  # Never emitted; no transport exists here.
    status = Signal(str, str)
    progress = Signal(int)
    finished = Signal()

    def __init__(self, path: str | Path, interval_ms: int = 100, loop: bool = False,
                 as_float32: bool = False) -> None:
        super().__init__()
        if type(interval_ms) is not int or not 1 <= interval_ms <= 60000:
            raise ValueError("矩阵回放周期必须为1–60000ms")
        if type(loop) is not bool or type(as_float32) is not bool:
            raise ValueError("回放选项必须为布尔值")
        self.path = Path(path)
        self.interval_ms = interval_ms
        self.loop, self.as_float32 = loop, as_float32
        self._condition = threading.Condition()
        self._stop = False
        self._paused = False
        self._started = False

    @property
    def can_send(self) -> bool:
        return False

    @property
    def connected(self) -> bool:
        return False

    def enqueue_send(self, _payload: bytes) -> int:
        raise ValueError("数值矩阵回放不能向设备发送")

    def clear_pending(self) -> int:
        return 0

    def set_paused(self, paused: bool) -> None:
        if type(paused) is not bool:
            raise ValueError("暂停状态无效")
        with self._condition:
            self._paused = paused
            self._condition.notify_all()

    def stop(self) -> None:
        with self._condition:
            self._stop = True
            self._condition.notify_all()

    def _wait(self, seconds: float) -> bool:
        remaining = max(0, seconds)
        with self._condition:
            while not self._stop:
                if self._paused:
                    self._condition.wait(.1)
                    continue
                if remaining <= 0:
                    return True
                before = time.monotonic()
                self._condition.wait(min(remaining, .1))
                remaining -= max(0, time.monotonic() - before)
            return False

    def _files(self) -> list[Path]:
        if self.path.is_symlink() or self.path.is_junction():
            raise ValueError("矩阵回放路径不能为符号链接或目录联接")
        if self.path.is_file():
            return [self.path]
        root = self.path.resolve(strict=True)
        if not root.is_dir():
            raise ValueError("请选择CSV文件或目录")
        files = []
        with os.scandir(root) as entries:
            for index, entry in enumerate(entries):
                if index >= MAX_DIRECTORY_ENTRIES:
                    raise ValueError("矩阵目录超过50000条目")
                if self._stop:
                    return []
                if entry.is_symlink() or not entry.is_file(follow_symlinks=False):
                    continue
                path = Path(entry.path)
                if path.suffix.lower() == ".csv":
                    files.append(path)
                    if len(files) > MAX_DIRECTORY_FILES:
                        raise ValueError("矩阵目录超过10000个CSV文件")
        if not files:
            raise ValueError("目录中没有CSV文件")
        return sorted(files, key=lambda path: tuple(int(part) if re.fullmatch(r"[0-9]+", part) else part
            for part in re.split(r"([0-9]+)", path.name.casefold())))

    @Slot()
    def run(self) -> None:
        with self._condition:
            if self._started:
                self.status.emit("error", "矩阵回放实例不能重复运行")
                self.finished.emit()
                return
            self._started = True
        iterator = None
        try:
            files = self._files()
            while not self._stop:
                for index, path in enumerate(files):
                    self.progress.emit(0)
                    self.status.emit("playback", f"数值矩阵回放 {index + 1}/{len(files)}：{path.name}；进度为当前文件")
                    iterator = iter_matrix_rows(path, as_float32=self.as_float32)
                    pending, frames, last_batch = [], 0, time.monotonic()
                    for row in iterator:
                        if not self._wait(0):
                            break
                        pending.append(TelemetryEvent(raw=row.raw, samples=row.samples))
                        frames += 1
                        if len(pending) >= 256 or time.monotonic() - last_batch >= .05:
                            self.events.emit(pending)
                            pending = []
                            last_batch = time.monotonic()
                        self.progress.emit(min(99, row.bytes_read * 100 // max(1, row.file_size)))
                        if not self._wait(self.interval_ms / 1000):
                            break
                    iterator.close()
                    iterator = None
                    if pending and not self._stop:
                        self.events.emit(pending)
                    if self._stop:
                        break
                    if not frames:
                        raise ValueError(f"{path.name}没有有效数值行")
                    self.progress.emit(100)
                if self._stop:
                    break
                if not self.loop:
                    self.status.emit("completed", "数值矩阵回放完成；未连接设备")
                    return
            self.status.emit("stopped", "数值矩阵回放已停止")
        except Exception as exc:
            self.status.emit("error", f"数值矩阵回放失败：{exc}")
        finally:
            try:
                if iterator is not None:
                    iterator.close()
            except Exception as exc:
                self.status.emit("error", f"关闭矩阵文件失败：{exc}")
            finally:
                self.finished.emit()
