"""In-process, synthetic serial peer for the safe GUI preview.

The ``SIM/`` payloads are deliberately unrelated to executable ISP commands.
No system COM port, pySerial object, or hardware dialect is used.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator

from .framing import decode_frame, encode_frame


class SimulationError(RuntimeError):
    """A simulated connection or response failed."""


class SimulationTimeout(SimulationError):
    """The simulated bootloader did not respond before the preview timeout."""


class SimulationDisconnected(SimulationError):
    """The simulated connection was unplugged."""


class SimulationCancelled(SimulationError):
    """The user cancelled the simulation."""


class SimulatedTransport:
    """A configurable preview peer that speaks only synthetic payloads.

    ``fault`` is one of ``timeout``, ``bad_checksum``, ``bad_ack`` or
    ``disconnect``; it takes effect at ``fault_stage``. ``WAIT_POWER`` can be
    used for boot timeout or disconnection before identification.
    """

    def __init__(
        self,
        *,
        cold_start_delay: float = 0.15,
        fragment_size: int = 7,
        fragment_delay: float = 0.0,
        fault: str | None = None,
        fault_stage: str = "PROGRAMMING",
        response_timeout: float = 0.25,
        checksum_bits: int = 8,
    ) -> None:
        if cold_start_delay < 0 or fragment_delay < 0 or response_timeout < 0:
            raise ValueError("simulation delays must be nonnegative")
        if fragment_size < 1:
            raise ValueError("fragment_size must be positive")
        if fault not in (None, "timeout", "bad_checksum", "bad_ack", "disconnect"):
            raise ValueError("unknown simulation fault")
        if checksum_bits not in (8, 16):
            raise ValueError("checksum_bits must be 8 or 16")
        self.cold_start_delay = cold_start_delay
        self.fragment_size = fragment_size
        self.fragment_delay = fragment_delay
        self.fault = fault
        self.fault_stage = fault_stage
        self.response_timeout = response_timeout
        self.checksum_bits = checksum_bits
        self.closed = False

    @staticmethod
    def _pause(duration: float, cancel_event: threading.Event) -> None:
        deadline = time.monotonic() + duration
        while True:
            if cancel_event.is_set():
                raise SimulationCancelled("用户已取消模拟下载")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            cancel_event.wait(min(remaining, 0.02))

    def wait_for_boot(self, cancel_event: threading.Event) -> None:
        if self.closed:
            raise SimulationDisconnected("模拟连接已关闭")
        if self.fault_stage == "WAIT_POWER" and self.fault == "disconnect":
            raise SimulationDisconnected("模拟设备在上电时断开")
        if self.fault_stage == "WAIT_POWER" and self.fault == "timeout":
            self._pause(self.response_timeout, cancel_event)
            raise SimulationTimeout("模拟设备上电后未响应")
        self._pause(self.cold_start_delay, cancel_event)

    def exchange(
        self,
        frame: bytes,
        *,
        stage: str,
        expected_ack: bytes,
        cancel_event: threading.Event,
    ) -> Iterator[bytes]:
        """Validate a synthetic TX frame and yield fragmented synthetic RX."""

        if self.closed:
            raise SimulationDisconnected("模拟连接已关闭")
        if cancel_event.is_set():
            raise SimulationCancelled("用户已取消模拟下载")
        payload = decode_frame(frame, direction="tx", checksum_bits=self.checksum_bits)
        if not payload.startswith(b"SIM/"):
            raise SimulationError("模拟器拒绝非 SIM/ 载荷")
        if self.fault_stage == stage and self.fault == "disconnect":
            raise SimulationDisconnected("模拟设备在通信中断开")
        if self.fault_stage == stage and self.fault == "timeout":
            self._pause(self.response_timeout, cancel_event)
            raise SimulationTimeout("模拟设备响应超时")

        ack = bytes(expected_ack)
        if self.fault_stage == stage and self.fault == "bad_ack":
            ack = b"SIM/WRONG-ACK"
        answer = encode_frame(ack, direction="rx", checksum_bits=self.checksum_bits)
        if self.fault_stage == stage and self.fault == "bad_checksum":
            corrupted = bytearray(answer)
            corrupted[-2] ^= 0x01
            answer = bytes(corrupted)
        for offset in range(0, len(answer), self.fragment_size):
            self._pause(self.fragment_delay, cancel_event)
            yield answer[offset : offset + self.fragment_size]

    def close(self) -> None:
        self.closed = True
