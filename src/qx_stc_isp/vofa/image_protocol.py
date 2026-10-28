"""Bounded adapters for two statically observed VOFA+ image envelopes.

Evidence scope is the installed application's transmitters and native image
provider, not a claim about every receiver version. The Demo image index is 0;
the receiver exposes an index field, bounded to 0–15 by this application.
The provider dispatches 25–34 to encoded file formats (27=JPG, 28=PNG), and
other fields to Qt5 raw pixel formats. This implementation admits 1–34;
the viewer independently validates the format, dimensions and payload length.
These opt-in adapters do not change the original
FireWater or JustFloat decoders and never access a device.

FireWater producer: ev_6911e013682ad931d9248d04fa525beae58d4c2b097613cdf938b9cbbc5c5410
JustFloat producer: ev_c5ba10cd5c3f8706f567468e70f2a6bef5b965d081b399b28449c6d56aac45da
Image provider: ev_cd686bbd28b65d8ebf1639ea61711c62c14abdee7792edccd56510c99aac4109
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass

from .model import TelemetryEvent


MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_IMAGE_PIXELS = 16_000_000
MAX_DIMENSION = 16_384
MAX_FEED_BYTES = 256 * 1024
MAX_TEXT_BYTES = 16_384
MAX_IMAGE_HEADER_BYTES = 256
MAX_IMAGE_INDEX = 15
OBSERVED_TYPE_FIELD = 27
SUPPORTED_TYPE_FIELDS = frozenset(range(1, 35))
FIREWATER_IMAGE_PREFIX = b"image:"
JUSTFLOAT_TAIL = b"\x00\x00\x80\x7f"
_DECIMAL = re.compile(rb"[0-9]{1,10}\Z")


@dataclass(frozen=True, slots=True)
class _PendingImage:
    header: bytes
    index: int
    size: int
    width: int
    height: int
    type_field: int
    rejection: str | None = None


def _pending(header: bytes, fields: tuple[int, ...]) -> _PendingImage:
    index, size, width, height, type_field = fields
    if not 1 <= size <= MAX_IMAGE_BYTES:
        raise ValueError("图像长度必须为 1 字节至 8 MiB；未按该长度分配或等待")
    reasons = []
    if not 0 <= index <= MAX_IMAGE_INDEX:
        reasons.append(f"图像索引 {index} 超出本软件0–15的容量限制")
    if type_field not in SUPPORTED_TYPE_FIELDS:
        reasons.append(f"图像类型字段 {type_field} 不在当前静态适配范围1–34内")
    if not 1 <= width <= MAX_DIMENSION or not 1 <= height <= MAX_DIMENSION:
        reasons.append("图像宽高元数据须在 1–16384 之间")
    elif width * height > MAX_IMAGE_PIXELS:
        reasons.append("图像宽高元数据超过 1600 万像素上限")
    return _PendingImage(header, index, size, width, height, type_field,
                         "；".join(reasons) if reasons else None)


def _image_event(pending: _PendingImage, payload: bytes) -> TelemetryEvent | None:
    if pending.rejection is not None:
        return None
    # Encoded formats carry descriptive dimensions; raw formats require exact
    # dimensions/stride. The viewer handles those distinct validation rules.
    return TelemetryEvent(
        raw=pending.header + payload,
        image=payload,
        image_index=pending.index,
        image_metadata=(pending.width, pending.height, pending.type_field),
    )


class FireWaterImageDecoder:
    """Parse ASCII image headers followed by exactly N encoded image bytes.

    Ordinary non-image lines retain the original FireWater numeric semantics.
    Unknown bounded envelopes are consumed but reported, never interpreted as
    numerical samples.  Oversized or malformed headers are rejected without
    allocating their advertised length.
    """

    def __init__(self) -> None:
        # Local import avoids a module initialisation cycle with the factory.
        from .decoders import FireWaterDecoder

        self._numeric = FireWaterDecoder()
        self._buffer = bytearray()
        self._pending: _PendingImage | None = None

    @property
    def buffered_bytes(self) -> int:
        return len(self._buffer)

    def reset(self) -> None:
        self._buffer.clear()
        self._pending = None
        self._numeric.reset()

    @staticmethod
    def _parse_header(header: bytes) -> _PendingImage:
        if len(header) > MAX_IMAGE_HEADER_BYTES:
            raise ValueError("FireWater 图像头超过 256 字节")
        body = header[len(FIREWATER_IMAGE_PREFIX):].rstrip(b"\r\n")
        parts = body.split(b",")
        if len(parts) != 5 or any(_DECIMAL.fullmatch(part) is None for part in parts):
            raise ValueError("FireWater 图像头必须为 image:index,N,width,height,type 的十进制五字段")
        values = tuple(int(part) for part in parts)
        if any(value > 0xFFFFFFFF for value in values):
            raise ValueError("FireWater 图像头字段超过 uint32 上限")
        return _pending(header, values)

    def feed(self, chunk: bytes) -> list[TelemetryEvent]:
        if not isinstance(chunk, bytes):
            raise TypeError("图像引擎输入必须为 bytes")
        if len(chunk) > MAX_FEED_BYTES:
            raise ValueError("单批图像引擎输入不能超过 256 KiB，请分片输入")
        self._buffer.extend(chunk)
        events: list[TelemetryEvent] = []
        while self._buffer:
            if self._pending is not None:
                current = self._pending
                if len(self._buffer) < current.size:
                    break
                payload = bytes(self._buffer[:current.size])
                del self._buffer[:current.size]
                self._pending = None
                event = _image_event(current, payload)
                if event is not None:
                    events.append(event)
                continue
            newline = self._buffer.find(b"\n")
            prefix = self._buffer.find(FIREWATER_IMAGE_PREFIX)
            if prefix > 0 and (newline < 0 or prefix < newline):
                raw = bytes(self._buffer[:prefix])
                del self._buffer[:prefix]
                self._numeric.reset()
                events.append(TelemetryEvent(raw=raw, error="已跳过 FireWater 图像头前的非行边界噪声"))
                continue
            is_image = self._buffer.startswith(FIREWATER_IMAGE_PREFIX)
            limit = MAX_IMAGE_HEADER_BYTES if is_image else MAX_TEXT_BYTES
            if newline < 0:
                if len(self._buffer) > limit:
                    raw = bytes(self._buffer)
                    # Keep only a possible split image-prefix suffix.
                    keep = next((size for size in range(len(FIREWATER_IMAGE_PREFIX) - 1, 0, -1)
                                 if self._buffer.endswith(FIREWATER_IMAGE_PREFIX[:size])), 0)
                    self._buffer[:] = self._buffer[-keep:] if keep else b""
                    self._numeric.reset()
                    events.append(TelemetryEvent(raw=raw[:-keep] if keep else raw,
                                                 error="FireWater 图像头或数值行超出长度上限，已重新同步"))
                break
            line = bytes(self._buffer[:newline + 1])
            del self._buffer[:newline + 1]
            if not is_image:
                events.extend(self._numeric.feed(line))
                continue
            try:
                current = self._parse_header(line)
            except ValueError as exc:
                self._numeric.reset()
                events.append(TelemetryEvent(raw=line, error=str(exc)))
                continue
            self._pending = current
            if current.rejection is not None:
                events.append(TelemetryEvent(raw=line, error=current.rejection + "；已按有界长度跳过该图像"))
        return events


class JustFloatImageDecoder:
    """Parse the observed seven-u32 envelope and ordinary numeric frames.

    The producer envelope ends its 28-byte header with two consecutive standard
    JustFloat tails.  A single-tail numeric frame is delayed until four further
    bytes arrive, allowing a split double-tail header to be recognised without
    prematurely yielding its fields as float samples.  No stream-EOF guessing
    or unsupported image-type inference is performed.
    """

    def __init__(self) -> None:
        from .decoders import JustFloatDecoder

        self._numeric = JustFloatDecoder()
        self._buffer = bytearray()
        self._pending: _PendingImage | None = None

    @property
    def buffered_bytes(self) -> int:
        return len(self._buffer)

    def reset(self) -> None:
        self._buffer.clear()
        self._pending = None
        self._numeric.reset()

    def finish_file(self) -> list[TelemetryEvent]:
        """Finish an explicitly complete local file, never an idle UART read.

        A known EOF rules out another half of a double-tail image header. It
        therefore releases the final complete numeric frame without inventing
        marker bytes. Incomplete images/noise remain errors, not samples.
        """
        remaining = bytes(self._buffer)
        if self._pending is not None:
            result = [TelemetryEvent(raw=remaining, error="本地文件结束时图像载荷不完整")]
        elif not remaining:
            result = []
        elif remaining.endswith(JUSTFLOAT_TAIL):
            result = self._numeric.feed(remaining)
        else:
            result = [TelemetryEvent(raw=remaining, error="本地文件结束时JustFloat帧不完整")]
        self.reset()
        return result

    def feed(self, chunk: bytes) -> list[TelemetryEvent]:
        if not isinstance(chunk, bytes):
            raise TypeError("图像引擎输入必须为 bytes")
        if len(chunk) > MAX_FEED_BYTES:
            raise ValueError("单批图像引擎输入不能超过 256 KiB，请分片输入")
        self._buffer.extend(chunk)
        events: list[TelemetryEvent] = []
        while self._buffer:
            if self._pending is not None:
                current = self._pending
                if len(self._buffer) < current.size:
                    break
                payload = bytes(self._buffer[:current.size])
                del self._buffer[:current.size]
                self._pending = None
                event = _image_event(current, payload)
                if event is not None:
                    events.append(event)
                continue
            marker = self._buffer.find(JUSTFLOAT_TAIL)
            if marker < 0:
                if len(self._buffer) > MAX_TEXT_BYTES:
                    # A future double-tail may finish the last 20-byte field
                    # group, so preserve enough bytes for a split full header.
                    keep = min(27, len(self._buffer))
                    discarded = bytes(self._buffer[:-keep])
                    del self._buffer[:-keep]
                    self._numeric.reset()
                    events.append(TelemetryEvent(raw=discarded, error="JustFloat 图像/数值帧超长或有噪声，已重新同步"))
                break
            if len(self._buffer) < marker + 8:
                break
            double_tail = self._buffer[marker + 4:marker + 8] == JUSTFLOAT_TAIL
            if not double_tail or marker < 20:
                end = marker + 4
                raw = bytes(self._buffer[:end])
                del self._buffer[:end]
                events.extend(self._numeric.feed(raw))
                continue
            start = marker - 20
            if start:
                discarded = bytes(self._buffer[:start])
                del self._buffer[:start]
                self._numeric.reset()
                events.append(TelemetryEvent(raw=discarded, error="已跳过 JustFloat 双尾图像头之前的噪声"))
            header = bytes(self._buffer[:28])
            del self._buffer[:28]
            words = struct.unpack("<7I", header)
            try:
                current = _pending(header, words[:5])
            except ValueError as exc:
                self._numeric.reset()
                events.append(TelemetryEvent(raw=header, error=str(exc)))
                continue
            self._pending = current
            if current.rejection is not None:
                events.append(TelemetryEvent(raw=header, error=current.rejection + "；已按有界长度跳过该图像"))
        return events
