"""Pure ISP frame codecs. No hardware dialect is enabled here.

These byte layouts come from the read-only analysis in
``docs/analysis/protocol-evidence.md``. The sum8 branch is also observed in
the successful official F002 capture. A valid frame alone does not establish
that another bootloader accepts commands in its payload.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Mapping, Protocol


class FrameError(ValueError):
    """A candidate frame has an invalid shape, length, or checksum."""


class ProtocolDialect(Protocol):
    """Future, hardware-verified ISP operation interface.

    Implementations must be registered only after a matching full-session
    capture on the target bootloader. The preview does not implement one.
    """

    name: str

    def identify(self) -> None: ...

    def negotiate(self) -> None: ...

    def erase(self) -> None: ...

    def program_block(self, address: int, data: bytes) -> None: ...

    def verify(self) -> None: ...

    def finish(self) -> None: ...


HARDWARE_DIALECTS: Mapping[str, ProtocolDialect] = MappingProxyType({})


def _parameters(direction: str, checksum_bits: int) -> tuple[int, int]:
    if direction not in ("tx", "rx"):
        raise ValueError("direction must be 'tx' or 'rx'")
    if checksum_bits not in (8, 16):
        raise ValueError("checksum_bits must be 8 or 16")
    return (0x6A if direction == "tx" else 0x68), checksum_bits // 8


def encode_frame(
    payload: bytes, *, direction: str = "tx", checksum_bits: int = 8
) -> bytes:
    """Encode the statically observed candidate frame, for offline use only.

    The big-endian length counts direction, both length bytes, payload,
    checksum, and terminator. The checksum covers direction through payload.
    """

    marker, checksum_bytes = _parameters(direction, checksum_bits)
    payload = bytes(payload)
    length = len(payload) + 4 + checksum_bytes
    if length > 0xFFFF:
        raise ValueError("candidate frame exceeds 16-bit length")
    body = bytes((marker,)) + length.to_bytes(2, "big") + payload
    checksum = (sum(body) & ((1 << checksum_bits) - 1)).to_bytes(
        checksum_bytes, "big"
    )
    return b"\x46\xB9" + body + checksum + b"\x16"


def decode_frame(
    frame: bytes, *, direction: str = "rx", checksum_bits: int = 8
) -> bytes:
    """Validate a complete candidate frame and return its payload.

    Input must contain exactly one complete frame. No bytes are silently
    discarded as noise; FrameStreamDecoder handles bounded streaming input.
    """

    marker, checksum_bytes = _parameters(direction, checksum_bits)
    frame = bytes(frame)
    minimum_length = 4 + checksum_bytes
    if len(frame) < minimum_length + 2:
        raise FrameError("frame is shorter than its minimum length")
    if frame[:3] != bytes((0x46, 0xB9, marker)):
        raise FrameError("frame header or direction is invalid")
    declared_length = int.from_bytes(frame[3:5], "big")
    if declared_length < minimum_length:
        raise FrameError("declared frame length is too short")
    if len(frame) != declared_length + 2:
        raise FrameError("declared frame length does not match received bytes")
    if frame[-1] != 0x16:
        raise FrameError("frame terminator is invalid")
    checksum_start = len(frame) - 1 - checksum_bytes
    actual_checksum = int.from_bytes(frame[checksum_start:-1], "big")
    expected_checksum = sum(frame[2:checksum_start]) & (
        (1 << checksum_bits) - 1
    )
    if actual_checksum != expected_checksum:
        raise FrameError("frame checksum does not match")
    return frame[5:checksum_start]


class FrameStreamDecoder:
    """Bounded frame reassembly, confirmed against the official RX capture.

    Noise before a header is counted. Once a header is found, malformed
    length/checksum/terminator is a hard error, not silently resynchronized.
    This object has no transport or device access.
    """

    def __init__(self, *, direction: str = "rx", checksum_bits: int = 8,
                 max_frame_bytes: int = 1024, max_noise_bytes: int = 256) -> None:
        marker, _ = _parameters(direction, checksum_bits)
        if max_frame_bytes < 8 or max_frame_bytes > 65537 or max_noise_bytes < 0:
            raise ValueError("invalid stream decoder limits")
        self.direction = direction
        self.checksum_bits = checksum_bits
        self.max_frame_bytes = max_frame_bytes
        self.max_noise_bytes = max_noise_bytes
        self.discarded_noise = 0
        self._header = bytes((0x46, 0xB9, marker))
        self._buffer = bytearray()

    def feed(self, chunk: bytes) -> list[bytes]:
        # Consume incrementally so even a large caller chunk cannot grow the
        # retained buffer beyond one bounded frame.
        frames: list[bytes] = []
        for byte in chunk:
            self._buffer.append(byte)
            while self._buffer and not self._header.startswith(self._buffer[:3]):
                del self._buffer[0]
                self.discarded_noise += 1
                if self.discarded_noise > self.max_noise_bytes:
                    raise FrameError("noise before frame exceeds limit")
            if len(self._buffer) < 5:
                continue
            total = int.from_bytes(self._buffer[3:5], "big") + 2
            minimum = 6 + self.checksum_bits // 8
            if total < minimum or total > self.max_frame_bytes:
                raise FrameError("declared stream frame length exceeds limits")
            if len(self._buffer) == total:
                frame = bytes(self._buffer)
                decode_frame(frame, direction=self.direction, checksum_bits=self.checksum_bits)
                frames.append(frame)
                self._buffer.clear()
        return frames

    def finish(self) -> None:
        if self._buffer:
            raise FrameError("capture ended with an incomplete frame")
