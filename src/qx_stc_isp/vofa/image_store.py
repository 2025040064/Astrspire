"""Bounded shared image frames for the image page and live image cards."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable

from PySide6.QtGui import QPixmap

from .image_formats import DecodedNativeFrame, decode_native_frame

MAX_IMAGE_PAYLOAD = 8 * 1024 * 1024
MAX_IMAGE_PIXELS = 16_777_216
PAYLOAD_BUDGET = 32 * 1024 * 1024
DECODED_BUDGET = 64 * 1024 * 1024
_PNG = b"\x89PNG\r\n\x1a\n"
_JPEG = b"\xff\xd8\xff"


class ImageFrameStore:
    """Payload LRU plus a decoded/pixmap cache shared by all consumers.

    Payloads are immutable bytes; decoding and QPixmap creation happen only in
    the GUI thread.  A generation changes on every frame for one index, so a
    card can skip redundant scaling and never shows an older index's frame.
    """

    def __init__(
        self,
        *,
        payload_limit: int = PAYLOAD_BUDGET,
        decoded_limit: int = DECODED_BUDGET,
        decoder: Callable[[bytes, tuple[int, int, int]], DecodedNativeFrame] = decode_native_frame,
    ) -> None:
        if payload_limit <= 0 or decoded_limit <= 0:
            raise ValueError("图像缓存预算必须为正数")
        self._payload_limit = payload_limit
        self._decoded_limit = decoded_limit
        self._decoder = decoder
        self._frames: OrderedDict[int, tuple[bytes, tuple[int, int, int] | None, int]] = OrderedDict()
        self._payload_bytes = 0
        self._pinned: frozenset[int] = frozenset()
        # index -> (generation, decoded frame, pixmap, error, byte estimate)
        self._decoded: OrderedDict[int, tuple[int, DecodedNativeFrame | None, QPixmap | None, str | None, int]] = OrderedDict()
        self._decoded_bytes = 0

    @property
    def payload_bytes(self) -> int:
        return self._payload_bytes

    @property
    def decoded_bytes(self) -> int:
        return self._decoded_bytes

    def set_pinned(self, indices) -> None:
        self._pinned = frozenset(index for index in indices if type(index) is int and 0 <= index <= 15)

    def put(self, index: int, payload: bytes, metadata=None) -> int:
        if type(index) is not int or not 0 <= index <= 15:
            raise ValueError("图像索引必须为0–15")
        if not isinstance(payload, bytes) or not payload or len(payload) > MAX_IMAGE_PAYLOAD:
            raise ValueError("图像载荷必须为1字节到8MiB的bytes")
        if metadata is not None and (
            not isinstance(metadata, tuple) or len(metadata) != 3
            or any(type(value) is not int for value in metadata)
        ):
            raise ValueError("图像元数据必须为width、height、format三个整数")
        previous = self._frames.pop(index, None)
        if previous is not None:
            self._payload_bytes -= len(previous[0])
        self._drop_decoded(index)
        generation = previous[2] + 1 if previous is not None else 1
        self._frames[index] = (payload, metadata, generation)
        self._payload_bytes += len(payload)
        while self._payload_bytes > self._payload_limit and self._frames:
            candidates = [key for key in self._frames if key not in self._pinned]
            oldest = candidates[0] if candidates else next(iter(self._frames))
            removed, _, _ = self._frames.pop(oldest)
            self._payload_bytes -= len(removed)
            self._drop_decoded(oldest)
        return generation

    def get(self, index: int) -> tuple[bytes, tuple[int, int, int] | None, int] | None:
        return self._frames.get(index)

    def pixmap(self, index: int) -> tuple[QPixmap | None, str | None, int | None]:
        entry = self._frames.get(index)
        if entry is None:
            return None, f"未收到图像 {index}", None
        generation = entry[2]
        cached = self._decoded.get(index)
        if cached is not None and cached[0] == generation:
            return cached[2], cached[3], generation
        frame, error = self._decode(index, entry[0], entry[1], generation)
        if frame is None:
            return None, error, generation
        cached = self._decoded.get(index)
        return (cached[2], cached[3], generation) if cached else (None, "图像显示缓存失败", generation)

    def decoded(self, index: int) -> tuple[DecodedNativeFrame | None, str | None]:
        entry = self._frames.get(index)
        if entry is None:
            return None, f"未收到图像 {index}"
        generation = entry[2]
        cached = self._decoded.get(index)
        if cached is not None and cached[0] == generation:
            return cached[1], cached[3]
        return self._decode(index, entry[0], entry[1], generation)

    def clear(self) -> None:
        self._frames.clear()
        self._decoded.clear()
        self._payload_bytes = 0
        self._decoded_bytes = 0

    @staticmethod
    def _metadata(payload: bytes, metadata):
        if metadata is not None:
            return metadata
        if payload.startswith(_PNG):
            return (1, 1, 28)
        if payload.startswith(_JPEG):
            return (1, 1, 27)
        raise ValueError("图像流只接受JPEG/PNG文件签名")

    def _decode(self, index: int, payload: bytes, metadata, generation: int):
        try:
            frame = self._decoder(payload, self._metadata(payload, metadata))
            if frame.image.isNull():
                raise ValueError("图像为空")
            if frame.image.width() * frame.image.height() > MAX_IMAGE_PIXELS:
                raise ValueError("解码图像超过1600万像素")
            pixmap = QPixmap.fromImage(frame.image)
            if pixmap.isNull():
                raise ValueError("无法建立显示图像")
        except Exception as exc:
            message = f"图像 {index} 解码失败：{exc}"
            self._drop_decoded(index)
            self._decoded[index] = (generation, None, None, message, 0)
            return None, message
        size = frame.image.sizeInBytes()
        self._drop_decoded(index)
        if size <= self._decoded_limit:
            while self._decoded_bytes + size > self._decoded_limit and self._decoded:
                self._drop_decoded(next(iter(self._decoded)))
            self._decoded[index] = (generation, frame, pixmap, None, size)
            self._decoded_bytes += size
        return frame, None

    def _drop_decoded(self, index: int) -> None:
        cached = self._decoded.pop(index, None)
        if cached is not None:
            self._decoded_bytes -= cached[4]
