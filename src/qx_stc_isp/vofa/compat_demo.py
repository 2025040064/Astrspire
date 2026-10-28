"""Independent CSV/JPG Demo producers, based on recorded VOFA 1.3.10 evidence.

The producer never owns a serial/socket transport. Files are fed to the same
decoder factory used by live sources; strict numerical validation is our own
documented difference from native failed-toFloat-to-zero behaviour.
"""
from __future__ import annotations

import os
import re
import stat
import struct
import threading
import time
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot

from .decoders import create_decoder
from .matrix_source import MAX_DIRECTORY_ENTRIES, MAX_DIRECTORY_FILES, MAX_FILE_BYTES, iter_matrix_rows

_DECODERS = frozenset({"FireWater", "JustFloat"})
_MAX_IMAGE_BYTES = 8 * 1024 * 1024
_FEED_BYTES = 16 * 1024
_BATCH_EVENTS = 128


@dataclass(frozen=True, slots=True)
class DemoSchedule:
    directory: str = ""
    enabled: bool = False
    period_ms: int = 1000
    loop: bool = False

    def __post_init__(self):
        if not isinstance(self.directory, str) or len(self.directory) > 4096 or "\x00" in self.directory:
            raise ValueError("Demo目录无效或超过4096字符")
        if type(self.enabled) is not bool or type(self.loop) is not bool:
            raise ValueError("Demo启用和循环选项必须为布尔值")
        if type(self.period_ms) is not int or not 1 <= self.period_ms <= 60000:
            raise ValueError("Demo周期必须为1–60000ms")

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict) or set(value) - {"directory", "enabled", "period_ms", "loop"}:
            raise ValueError("Demo定时源配置无效或含未知字段")
        return cls(**value)


@dataclass(frozen=True, slots=True)
class CompatDemoConfig:
    decoder: str = "FireWater"
    csv: DemoSchedule = DemoSchedule()
    jpg: DemoSchedule = DemoSchedule()

    def __post_init__(self):
        if not isinstance(self.decoder, str) or self.decoder not in _DECODERS:
            raise ValueError("VOFA Demo兼容模式仅支持FireWater或JustFloat")
        if not isinstance(self.csv, DemoSchedule) or not isinstance(self.jpg, DemoSchedule):
            raise ValueError("Demo源配置必须为DemoSchedule")

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict) or set(value) - {"decoder", "csv", "jpg"}:
            raise ValueError("VOFA Demo配置无效或含未知字段")
        return cls(value.get("decoder", "FireWater"),
                   DemoSchedule.from_dict(value.get("csv", {})),
                   DemoSchedule.from_dict(value.get("jpg", {})))

    to_document = to_dict
    from_document = from_dict


def encode_csv_file(path: str | Path, decoder: str) -> bytes:
    """One timer tick is one whole file, never one row."""
    if decoder not in _DECODERS:
        raise ValueError("CSV Demo引擎无效")
    data = bytearray()
    rows = 0
    source = Path(path)
    before = source.stat()
    for row in iter_matrix_rows(path, as_float32=decoder == "JustFloat"):
        rows += 1
        if decoder == "JustFloat":
            data.extend(struct.pack(f"<{len(row.samples)}f", *row.samples))
            data.extend(b"\x00\x00\x80\x7f")
        if len(data) > MAX_FILE_BYTES:
            raise ValueError("Demo转换后数据超过128MiB上限")
    if not rows:
        raise ValueError("CSV Demo文件没有有效数值行")
    if decoder == "FireWater":
        # Preserve *all* source bytes (also blank lines), as observed in the
        # native producer. Validation above intentionally rejects bad numbers.
        with source.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            data = stream.read(MAX_FILE_BYTES + 1)
        after = source.stat()
        if (len(data) > MAX_FILE_BYTES or len(data) != before.st_size
                or (before.st_size, before.st_mtime_ns, before.st_ino)
                != (opened.st_size, opened.st_mtime_ns, opened.st_ino)
                or (after.st_size, after.st_mtime_ns, after.st_ino)
                != (before.st_size, before.st_mtime_ns, before.st_ino)):
            raise ValueError("CSV文件在校验与读取之间发生变化")
    return bytes(data)


def encode_jpg_file(path: str | Path, decoder: str) -> bytes:
    if decoder not in _DECODERS:
        raise ValueError("JPG Demo引擎无效")
    source = Path(path)
    if source.is_symlink() or source.is_junction():
        raise ValueError("JPG不能为符号链接或目录联接")
    info = source.stat()
    if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= _MAX_IMAGE_BYTES:
        raise ValueError("JPG必须为非空普通文件且不超过8MiB")
    with source.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        if not stat.S_ISREG(opened.st_mode):
            raise ValueError("JPG打开后不是普通文件")
        data = stream.read(_MAX_IMAGE_BYTES + 1)
    if not 0 < len(data) <= _MAX_IMAGE_BYTES or not data.startswith(b"\xff\xd8") or not data.endswith(b"\xff\xd9"):
        raise ValueError("JPG文件签名或大小无效")
    if decoder == "FireWater":
        return f"image:0,{len(data)},30,40,27\n".encode("ascii") + data
    return struct.pack("<7I", 0, len(data), 200, 300, 27, 0x7f800000, 0x7f800000) + data


def _directory_files(directory: str, kind: str) -> list[Path]:
    source = Path(directory)
    if not directory or source.is_symlink() or source.is_junction() or not source.is_dir():
        raise ValueError("请选择普通文件目录，不接受链接")
    files = []
    with os.scandir(source) as entries:
        for index, entry in enumerate(entries):
            if index >= MAX_DIRECTORY_ENTRIES:
                raise ValueError("Demo目录超过50000条目")
            if entry.is_symlink() or not entry.is_file(follow_symlinks=False):
                continue
            path = Path(entry.path)
            if path.suffix.casefold() in ({".csv"} if kind == "csv" else {".jpg", ".jpeg"}):
                files.append(path)
                if len(files) > MAX_DIRECTORY_FILES:
                    raise ValueError("Demo目录超过10000个文件")
    if not files:
        raise ValueError(f"目录中没有{kind.upper()}文件")
    return sorted(files, key=lambda path: tuple(
        int(part) if part.isascii() and part.isdecimal() else part
        for part in re.split(r"([0-9]+)", path.name.casefold())))


class CompatDemoWorker(QObject):
    """Two independent schedules sharing one parser and one bounded delivery.

    Call ``ack_batch`` after every events delivery (including empty batches).
    Only one batch is in flight. A consumer that stops acknowledging causes
    an explicit failure rather than silent sample loss or an unbounded queue.
    Control methods are thread-safe direct calls; queued slots cannot interrupt
    a worker whose run loop currently owns its QThread.
    """

    events = Signal(object)
    raw = Signal(bytes)
    transmitted = Signal(bytes)  # Intentionally never emitted.
    status = Signal(str, str)
    progress = Signal(int)
    finished = Signal()

    def __init__(self, config: CompatDemoConfig, *, await_ack: bool = True,
                 acknowledgement_timeout: float = 10):
        super().__init__()
        if not isinstance(config, CompatDemoConfig):
            raise ValueError("Demo配置类型无效")
        if type(acknowledgement_timeout) not in (int, float) or not .05 <= acknowledgement_timeout <= 60:
            raise ValueError("Demo确认超时必须为0.05–60秒")
        if type(await_ack) is not bool:
            raise ValueError("Demo确认开关必须为布尔值")
        self.config = config
        self.await_ack = await_ack
        self.acknowledgement_timeout = acknowledgement_timeout
        self._condition = threading.Condition()
        self._stop = False
        self._started = False
        self._awaiting_ack = False
        self._paused = {"csv": False, "jpg": False}
        self._disabled = {"csv": False, "jpg": False}
        self._schedules: dict[str, dict] = {}

    @property
    def can_send(self) -> bool:
        return False

    @property
    def connected(self) -> bool:
        return False

    def enqueue_send(self, _payload: bytes) -> int:
        raise ValueError("Demo数据不能向设备发送")

    def clear_pending(self) -> int:
        return 0

    @staticmethod
    def _kind(kind: str):
        if kind not in ("csv", "jpg"):
            raise ValueError("Demo源必须为csv或jpg")

    def set_source_paused(self, kind: str, paused: bool):
        """Pause the next file tick; an in-progress file finishes atomically."""
        self._kind(kind)
        if type(paused) is not bool:
            raise ValueError("暂停状态必须为布尔值")
        with self._condition:
            if self._paused[kind] == paused:
                return
            self._paused[kind] = paused
            state = self._schedules.get(kind)
            if state:
                if paused:
                    state["remaining"] = max(0., state["deadline"] - time.monotonic())
                else:
                    state["deadline"] = time.monotonic() + state.pop("remaining", 0.)
            self._condition.notify_all()

    def set_paused(self, paused: bool):
        for kind in ("csv", "jpg"):
            self.set_source_paused(kind, paused)

    def stop_source(self, kind: str):
        self._kind(kind)
        with self._condition:
            self._disabled[kind] = True
            self._condition.notify_all()

    def source_active(self, kind: str) -> bool:
        """Thread-safe liveness; a paused source is still active."""
        self._kind(kind)
        with self._condition:
            return kind in self._schedules and not self._disabled[kind] and not self._stop

    def stop(self):
        with self._condition:
            self._stop = True
            self._condition.notify_all()

    def ack_batch(self):
        with self._condition:
            self._awaiting_ack = False
            self._condition.notify_all()

    def _deliver(self, events: list, raw: bytes = b"") -> bool:
        batches = [events[index:index + _BATCH_EVENTS] for index in range(0, len(events), _BATCH_EVENTS)] or [[]]
        for index, batch in enumerate(batches):
            with self._condition:
                if self._stop:
                    return False
                self._awaiting_ack = self.await_ack
            if raw and index == 0:
                self.raw.emit(raw)
            self.events.emit(batch)
            deadline = time.monotonic() + self.acknowledgement_timeout
            with self._condition:
                while self._awaiting_ack and not self._stop:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise ValueError("界面未确认接收Demo批次；已停止源以避免丢帧")
                    self._condition.wait(min(remaining, .1))
                if self._stop:
                    return False
        return True

    def _process(self, kind: str, path: Path, decoder) -> bool:
        data = encode_csv_file(path, self.config.decoder) if kind == "csv" else encode_jpg_file(path, self.config.decoder)
        # A complete source file is indivisible between the two timer streams,
        # while feeding and delivery are bounded, cancelable chunks.
        for offset in range(0, len(data), _FEED_BYTES):
            with self._condition:
                if self._stop or self._disabled[kind]:
                    decoder.reset()
                    return False
            chunk = data[offset:offset + _FEED_BYTES]
            stamp = datetime.now(timezone.utc)
            events = [replace(event, received_at=stamp) for event in decoder.feed(chunk)]
            if not self._deliver(events, chunk):
                return False
        if kind == "csv" and self.config.decoder == "JustFloat":
            stamp = datetime.now(timezone.utc)
            return self._deliver([replace(event, received_at=stamp) for event in decoder.finish_file()])
        if getattr(decoder, "buffered_bytes", 0):
            decoder.reset()
            raise ValueError("Demo文件在帧边界前结束，未拼接到下一个文件")
        return True

    @Slot()
    def run(self):
        with self._condition:
            if self._started:
                self.status.emit("error", "Demo实例不能重复运行")
                self.finished.emit()
                return
            self._started = True
        failed = False
        try:
            decoder = create_decoder(self.config.decoder + "Image")
            for kind in ("csv", "jpg"):
                schedule = getattr(self.config, kind)
                if not schedule.enabled:
                    continue
                try:
                    files = _directory_files(schedule.directory, kind)
                except (OSError, ValueError) as exc:
                    self.status.emit("notice", f"{kind.upper()} Demo失败并停止：{exc}；另一数据源不受影响")
                    failed = True
                    continue
                with self._condition:
                    self._schedules[kind] = {"files": files, "index": 0,
                        "deadline": time.monotonic() + schedule.period_ms / 1000,
                        "remaining": schedule.period_ms / 1000}
            while True:
                with self._condition:
                    active = [kind for kind in self._schedules if not self._disabled[kind]]
                    if self._stop or not active:
                        break
                    ready = [kind for kind in active if not self._paused[kind]]
                    if not ready:
                        self._condition.wait(.1)
                        continue
                    kind = min(ready, key=lambda item: self._schedules[item]["deadline"])
                    state = self._schedules[kind]
                    remaining = state["deadline"] - time.monotonic()
                    if remaining > 0:
                        self._condition.wait(min(remaining, .1))
                        continue
                    path = state["files"][state["index"]]
                try:
                    self.status.emit("playback", f"{kind.upper()} Demo：{path.name}")
                    if not self._process(kind, path, decoder):
                        continue
                except (OSError, ValueError, UnicodeError, OverflowError) as exc:
                    decoder.reset()
                    self.stop_source(kind)
                    failed = True
                    self.status.emit("notice", f"{kind.upper()} Demo失败并停止：{exc}；另一数据源不受影响")
                    continue
                with self._condition:
                    state["index"] += 1
                    schedule = getattr(self.config, kind)
                    if state["index"] >= len(state["files"]):
                        if schedule.loop:
                            state["index"] = 0
                        else:
                            self._disabled[kind] = True
                    state["deadline"] = time.monotonic() + schedule.period_ms / 1000
                    if self._paused[kind]:
                        state["remaining"] = schedule.period_ms / 1000
            code = "stopped" if self._stop else "completed_with_errors" if failed else "completed"
            message = ("Demo已停止" if self._stop else "Demo已结束（存在失败源）" if failed else "Demo全部文件完成")
            self.status.emit(code, message + "；未连接设备")
        except Exception as exc:
            self.status.emit("error", f"Demo运行失败：{exc}")
        finally:
            self.finished.emit()
