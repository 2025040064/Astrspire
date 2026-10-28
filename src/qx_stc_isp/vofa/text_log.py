"""Exclusive, size-bounded UTF-8 sink; the host uses AsyncRecorder for I/O."""

from __future__ import annotations

from pathlib import Path
from typing import TextIO


class TextLogSink:
    """Write exact caller-supplied lines without touching files at construction."""

    def __init__(self, path: str | Path, *, max_bytes: int = 16 * 1024 * 1024) -> None:
        if type(max_bytes) is not int or max_bytes <= 0:
            raise ValueError("文本日志大小上限必须为正整数")
        self.path = Path(path)
        self.max_bytes = max_bytes
        self.bytes_written = 0
        self._stream: TextIO | None = None

    def open(self) -> None:
        if self._stream is not None:
            raise RuntimeError("文本日志已打开")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._stream = self.path.open("x", encoding="utf-8", newline="")
        self.bytes_written = 0

    def record(self, text: str) -> int:
        if not isinstance(text, str):
            raise TypeError("文本日志只接受字符串")
        if self._stream is None:
            raise RuntimeError("文本日志尚未打开")
        size = len(text.encode("utf-8"))
        if self.bytes_written + size > self.max_bytes:
            raise OSError(f"文本日志达到 {self.max_bytes} 字节上限，已停止此日志")
        written = self._stream.write(text)
        if written != len(text):
            raise OSError("文本日志只写入部分内容，不能确认此条记录完整")
        self.bytes_written += size
        return size

    def flush(self) -> None:
        if self._stream is not None:
            self._stream.flush()

    def close(self) -> None:
        stream, self._stream = self._stream, None
        if stream is not None:
            stream.close()


class RotatingTextLogSink(TextLogSink):
    """Keep the existing sink contract, rotate instead of discarding history."""
    def __init__(self, path, *, max_bytes=16 * 1024 * 1024):
        super().__init__(path, max_bytes=max_bytes)
        self.base_path = self.path
        self.part = 1

    def record(self, text):
        if not isinstance(text, str):
            raise TypeError("文本日志只接受字符串")
        size = len(text.encode("utf-8"))
        if size > self.max_bytes:
            raise OSError("单条文本日志超过分卷上限")
        if self.bytes_written + size > self.max_bytes:
            self.flush()
            self.close()
            self.part += 1
            self.path = self.base_path.with_name(f"{self.base_path.stem}.part{self.part:04d}{self.base_path.suffix}")
            self.open()
        return super().record(text)
