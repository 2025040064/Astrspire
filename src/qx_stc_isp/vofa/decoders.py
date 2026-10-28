"""Bounded streaming decoders based on installed data-engine format metadata.

These are independent implementations, not claims of byte-for-byte equivalence
with VOFA+ internals.  They never access a transport or transmit data.
"""

from __future__ import annotations

import math
import struct
from typing import Protocol

from .model import TelemetryEvent
from .serialweb import SerialWebLineFramer


MAX_FRAME_BYTES = 16_384
JUSTFLOAT_TAIL = b"\x00\x00\x80\x7f"


class FeedDecoder(Protocol):
    def feed(self, chunk: bytes) -> list[TelemetryEvent]: ...

    def reset(self) -> None: ...


class FireWaterDecoder:
    """Parse newline-terminated comma-separated numbers, optionally prefixed.

The installed metadata specifies a line ending but not encoding or malformed
line semantics.  UTF-8 replacement and preserving malformed lines as text are
explicit choices made by this application.
    """

    def __init__(self, *, max_frame_bytes: int = MAX_FRAME_BYTES) -> None:
        if max_frame_bytes < 1:
            raise ValueError("帧长度限制必须为正数")
        self.max_frame_bytes = max_frame_bytes
        self._framer = SerialWebLineFramer(max_frame_bytes)

    def reset(self) -> None:
        self._framer.reset()

    @staticmethod
    def _decode_line(raw: bytes) -> TelemetryEvent:
        text = raw.rstrip(b"\r\n").decode("utf-8", errors="replace")
        numeric_part = text.rsplit(":", 1)[-1].strip()
        fields = [part.strip() for part in numeric_part.split(",")]
        if not fields or not all(fields):
            return TelemetryEvent(raw=raw, text=text)
        try:
            values = tuple(float(field) for field in fields)
        except ValueError:
            return TelemetryEvent(raw=raw, text=text)
        if not all(math.isfinite(value) for value in values):
            return TelemetryEvent(raw=raw, text=text, error="采样值包含非有限数")
        return TelemetryEvent(raw=raw, samples=values, text=text)

    def feed(self, chunk: bytes) -> list[TelemetryEvent]:
        if not isinstance(chunk, bytes):
            raise TypeError("输入必须为 bytes")
        events: list[TelemetryEvent] = []
        for raw in self._framer.feed(chunk):
            if len(raw) > self.max_frame_bytes:
                events.append(TelemetryEvent(raw=raw, error="FireWater 行超过长度限制"))
            else:
                events.append(self._decode_line(raw))
        return events


class JustFloatDecoder:
    """Parse little-endian float32 arrays terminated by the documented tail."""

    def __init__(self, *, max_frame_bytes: int = MAX_FRAME_BYTES) -> None:
        if max_frame_bytes < 8:
            raise ValueError("帧长度限制至少为 8 字节")
        self.max_frame_bytes = max_frame_bytes
        self._buffer = bytearray()

    def reset(self) -> None:
        self._buffer.clear()

    @staticmethod
    def _decode_frame(raw: bytes) -> TelemetryEvent:
        payload = raw[: -len(JUSTFLOAT_TAIL)]
        if not payload or len(payload) % 4:
            return TelemetryEvent(raw=raw, error="JustFloat 数据长度不是非零 float32 数组")
        values = struct.unpack(f"<{len(payload) // 4}f", payload)
        if not all(math.isfinite(value) for value in values):
            return TelemetryEvent(raw=raw, error="采样值包含非有限数")
        return TelemetryEvent(raw=raw, samples=values)

    def feed(self, chunk: bytes) -> list[TelemetryEvent]:
        if not isinstance(chunk, bytes):
            raise TypeError("输入必须为 bytes")
        self._buffer.extend(chunk)
        events: list[TelemetryEvent] = []
        while True:
            index = self._buffer.find(JUSTFLOAT_TAIL)
            if index < 0:
                if len(self._buffer) > self.max_frame_bytes:
                    # Only a suffix matching the tail's prefix can finish a
                    # marker in the next chunk; arbitrary trailing noise must
                    # not contaminate the next valid frame.
                    keep = next(
                        (
                            length
                            for length in range(len(JUSTFLOAT_TAIL) - 1, 0, -1)
                            if self._buffer.endswith(JUSTFLOAT_TAIL[:length])
                        ),
                        0,
                    )
                    discarded = bytes(self._buffer[:-keep]) if keep else bytes(self._buffer)
                    if keep:
                        del self._buffer[:-keep]
                    else:
                        self._buffer.clear()
                    events.append(TelemetryEvent(raw=discarded, error="JustFloat 帧超过长度限制"))
                break
            frame_end = index + len(JUSTFLOAT_TAIL)
            raw = bytes(self._buffer[:frame_end])
            del self._buffer[:frame_end]
            if len(raw) > self.max_frame_bytes:
                events.append(TelemetryEvent(raw=raw, error="JustFloat 帧超过长度限制"))
            else:
                events.append(self._decode_frame(raw))
        return events


class RawDataDecoder:
    """Preserve exactly the chunks received; no sample interpretation."""

    def reset(self) -> None:
        pass

    def feed(self, chunk: bytes) -> list[TelemetryEvent]:
        if not isinstance(chunk, bytes):
            raise TypeError("输入必须为 bytes")
        return [TelemetryEvent(raw=chunk)] if chunk else []


def create_decoder(name: str) -> FeedDecoder:
    """Return one of the three installed format variants by display name."""

    if not isinstance(name, str):
        raise ValueError("数据格式必须为文本")
    if name.startswith("plugin:"):
        from .plugins import PLUGIN_REGISTRY
        return PLUGIN_REGISTRY.create(name)
    if name in ("FireWaterImage", "JustFloatImage"):
        from .image_protocol import FireWaterImageDecoder, JustFloatImageDecoder
        return FireWaterImageDecoder() if name == "FireWaterImage" else JustFloatImageDecoder()
    normalized = name.casefold().replace("-", "").replace("_", "").strip()
    match normalized:
        case "firewater":
            return FireWaterDecoder()
        case "justfloat":
            return JustFloatDecoder()
        case "rawdata" | "imagestream":
            return RawDataDecoder()
        case _:
            raise ValueError(f"未知数据格式：{name}")
