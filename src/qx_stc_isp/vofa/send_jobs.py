"""Explicit, timer-paced send jobs; progress means queued data, never ACKs.

Construction and import do not open a file or access any transport.  The GUI
owns connection policy and the outgoing queue; cancelling a job only prevents
future enqueue calls and does not discard bytes already queued by the caller.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import BinaryIO

from PySide6.QtCore import QObject, QTimer, Signal

from .commands import MAX_COMMANDS, CommandEntry
from .transmit import MAX_SEND_BYTES, encode_send_payload

MAX_FILE_BYTES = 16 * 1024 * 1024
MIN_INTERVAL_MS = 20
MAX_INTERVAL_MS = 60_000
MAX_REPEAT_COUNT = 10_000


class SendJobController(QObject):
    """Queue one packet per GUI-thread timer tick, without retries.

    ``progress(done, total)`` counts packets for a sequence and bytes for a
    file.  These are enqueue counts, not bytes acknowledged by a device.
    ``finished(cancelled)`` is emitted after a running job ends, including an
    error (``cancelled=False``); listen to ``error`` to distinguish failure.
    Rejected start requests emit ``error`` but do not emit ``finished``.
    """

    status = Signal(str)
    error = Signal(str)
    progress = Signal(int, int)
    finished = Signal(bool)

    def __init__(
        self,
        enqueue: Callable[[bytes], bool],
        can_send: Callable[[], bool],
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        if not callable(enqueue) or not callable(can_send):
            raise TypeError("enqueue 和 can_send 必须为可调用对象")
        self._enqueue = enqueue
        self._can_send = can_send
        self._active = False
        self._job_kind = ""
        self._packets: tuple[bytes, ...] = ()
        self._stream: BinaryIO | None = None
        self._chunk_size = 1024
        self._done = 0
        self._total = 0
        self._generation = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

    @property
    def active(self) -> bool:
        return self._active

    @property
    def job_kind(self) -> str:
        """``sequence`` or ``file`` while active, otherwise an empty string."""
        return self._job_kind

    @staticmethod
    def _validate_interval(interval_ms: int) -> None:
        if type(interval_ms) is not int or not MIN_INTERVAL_MS <= interval_ms <= MAX_INTERVAL_MS:
            raise ValueError("发送间隔必须为 20–60000 ms 的整数")

    def _reject(self, message: str) -> bool:
        self.error.emit(message)
        return False

    def start_sequence(
        self,
        entries: Sequence[CommandEntry],
        interval_ms: int,
        repeat_count: int = 1,
    ) -> bool:
        """Validate the complete command list before starting its timer."""
        if self._active:
            return self._reject("已有发送任务，请先停止当前任务")
        try:
            self._validate_interval(interval_ms)
            if type(repeat_count) is not int or not 1 <= repeat_count <= MAX_REPEAT_COUNT:
                raise ValueError("重复次数必须为 1–10000 的整数")
            if not isinstance(entries, Sequence) or isinstance(entries, (str, bytes, bytearray)):
                raise ValueError("顺序发送需要 CommandEntry 序列")
            if not 1 <= len(entries) <= MAX_COMMANDS:
                raise ValueError(f"顺序发送需要 1–{MAX_COMMANDS} 条命令")
            packets = []
            for index, entry in enumerate(entries, start=1):
                if not isinstance(entry, CommandEntry):
                    raise ValueError(f"第 {index} 条命令必须是 CommandEntry")
                # Revalidate frozen entries in case a caller bypassed the
                # dataclass protection; no partial list is ever queued.
                canonical = CommandEntry(entry.name, entry.payload, entry.mode, entry.ending, entry.encoding)
                packets.append(encode_send_payload(
                    canonical.payload, mode=canonical.mode, line_ending=canonical.ending, encoding=canonical.encoding,
                ))
            if not self._can_send():
                raise ValueError("未连接可发送的数据源，未启动顺序发送")
        except Exception as exc:
            return self._reject(f"无法启动顺序发送：{exc}")
        self._packets = tuple(packets)
        self._done = 0
        self._total = len(self._packets) * repeat_count
        self._begin("sequence", interval_ms)
        self.status.emit(f"顺序发送已开始：共 {self._total} 包；进度仅表示入队，不代表设备 ACK")
        return True

    def start_file(
        self,
        path: str | Path,
        chunk_size: int = 1024,
        interval_ms: int = 20,
    ) -> bool:
        """Open the explicitly selected regular file and stream bounded chunks."""
        if self._active:
            return self._reject("已有发送任务，请先停止当前任务")
        stream: BinaryIO | None = None
        try:
            self._validate_interval(interval_ms)
            if type(chunk_size) is not int or not 1 <= chunk_size <= MAX_SEND_BYTES:
                raise ValueError(f"文件分包大小必须为 1–{MAX_SEND_BYTES} 字节的整数")
            if not self._can_send():
                raise ValueError("未连接可发送的数据源，未打开文件")
            source = Path(path)
            info = source.stat()
            if not stat.S_ISREG(info.st_mode):
                raise ValueError("文件发送只接受普通文件")
            if not 1 <= info.st_size <= MAX_FILE_BYTES:
                raise ValueError("文件必须非空且不超过 16 MiB")
            stream = source.open("rb")
            opened_info = os.fstat(stream.fileno())
            if not stat.S_ISREG(opened_info.st_mode) or not 1 <= opened_info.st_size <= MAX_FILE_BYTES:
                raise ValueError("打开后的文件必须为非空普通文件且不超过 16 MiB")
            if not self._can_send():
                raise ValueError("数据源已断开，未启动文件发送")
        except Exception as exc:
            if stream is not None:
                try:
                    stream.close()
                except Exception:
                    pass
            return self._reject(f"无法启动文件发送：{exc}")
        self._stream = stream
        self._chunk_size = chunk_size
        self._done = 0
        self._total = opened_info.st_size
        self._begin("file", interval_ms)
        self.status.emit(f"文件发送已开始：{self._total} 字节；进度仅表示入队，不代表设备 ACK")
        return True

    def _begin(self, kind: str, interval_ms: int) -> None:
        self._generation += 1
        self._job_kind = kind
        self._active = True
        self._timer.start(interval_ms)
        self.progress.emit(0, self._total)

    def _tick(self) -> None:
        if not self._active:
            return
        generation = self._generation
        try:
            if not self._can_send():
                raise RuntimeError("数据源已断开；发送任务停止，不会自动重试")
            if not self._active or generation != self._generation:
                return
            if self._job_kind == "sequence":
                payload = self._packets[self._done % len(self._packets)]
                count = 1
            else:
                if self._stream is None:
                    raise RuntimeError("文件已关闭")
                info = os.fstat(self._stream.fileno())
                if info.st_size > MAX_FILE_BYTES:
                    raise ValueError("文件增长超过 16 MiB；发送已停止")
                if info.st_size < self._done:
                    raise ValueError("发送中的文件被截断；发送已停止")
                self._total = info.st_size
                payload = self._stream.read(self._chunk_size)
                if not payload:
                    if self._done != self._total:
                        raise ValueError("文件长度发生变化，未读取到预期数据；发送已停止")
                    self._end(cancelled=False)
                    return
                count = len(payload)
                # A file may grow between fstat and read; never queue a chunk
                # which would take this job beyond the absolute size limit.
                if self._done + count > MAX_FILE_BYTES:
                    raise ValueError("文件增长超过 16 MiB；发送已停止")
                after_read = os.fstat(self._stream.fileno())
                if after_read.st_size > MAX_FILE_BYTES:
                    raise ValueError("文件增长超过 16 MiB；发送已停止")
                if after_read.st_size < self._done + count:
                    raise ValueError("发送中的文件被截断；发送已停止")
                self._total = after_read.st_size
            if not self._enqueue(payload):
                # The transport may have accepted an unknown subset.  Never
                # retry this packet or rewind the file after a rejected call.
                raise RuntimeError("发送队列未接受数据；任务停止，不会自动重试")
            if not self._active or generation != self._generation:
                return
            self._done += count
            self.progress.emit(self._done, self._total)
            if not self._active or generation != self._generation:
                return
            if self._job_kind == "sequence" and self._done == self._total:
                self._end(cancelled=False)
        except Exception as exc:
            if self._active and generation == self._generation:
                self._end(cancelled=False, error_message=f"发送任务失败：{exc}")

    def stop(self) -> None:
        """Stop future queue submissions; the owner's existing queue is intact."""
        if self._active:
            self._end(cancelled=True)

    def _end(self, *, cancelled: bool, error_message: str | None = None) -> None:
        self._timer.stop()
        self._generation += 1
        self._active = False
        kind = self._job_kind
        self._job_kind = ""
        self._packets = ()
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.close()
            except Exception as exc:
                if error_message is None:
                    error_message = f"关闭发送文件失败：{exc}"
        unit = "包" if kind == "sequence" else "字节"
        if error_message is not None:
            self.status.emit(f"任务失败：已入队 {self._done}/{self._total} {unit}，未确认设备接收")
            self.error.emit(error_message)
        elif cancelled:
            self.status.emit(f"发送任务已停止：已入队 {self._done}/{self._total} {unit}；已入队内容由数据源处理")
        else:
            self.status.emit(f"发送入队完成：{self._done}/{self._total} {unit}，不代表设备 ACK")
        self.finished.emit(cancelled)
