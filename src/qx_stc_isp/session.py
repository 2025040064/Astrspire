"""Synthetic download state machine for a manually triggered power cycle."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING

from .framing import FrameError, decode_frame, encode_frame
from .simulator import (
    SimulatedTransport,
    SimulationCancelled,
    SimulationDisconnected,
    SimulationError,
    SimulationTimeout,
)

if TYPE_CHECKING:
    from .hex_image import HexImage


MAX_SESSION_BLOCKS = 1024


class SessionState(str, Enum):
    IDLE = "IDLE"
    WAIT_POWER = "WAIT_POWER"
    IDENTIFIED = "IDENTIFIED"
    NEGOTIATING = "NEGOTIATING"
    ERASING = "ERASING"
    PROGRAMMING = "PROGRAMMING"
    VERIFYING = "VERIFYING"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True)
class SessionEvent:
    kind: str
    state: SessionState
    progress: int
    message: str
    direction: str | None = None
    data: bytes = b""
    timestamp: datetime = field(default_factory=lambda: datetime.now().astimezone())


class DownloadSession:
    """Run a demonstration session; never constructs or opens a real port.

    The caller invokes ``run`` in a worker thread. A separate GUI action calls
    ``power_on`` during WAIT_POWER. ``cancel`` is safe from another thread.
    """

    def __init__(
        self,
        image: HexImage,
        emit: Callable[[SessionEvent], None],
        simulator: SimulatedTransport | None = None,
        wait_power_seconds: float = 30,
        *,
        chip_name: str = "STC89C52RC",
    ) -> None:
        if wait_power_seconds <= 0:
            raise ValueError("wait_power_seconds must be positive")
        self.image = image
        self.emit = emit
        self.simulator = simulator if simulator is not None else SimulatedTransport()
        self.wait_power_seconds = wait_power_seconds
        self.chip_name = chip_name
        self.state = SessionState.IDLE
        self.progress = 0
        self._power_event = threading.Event()
        self._cancel_event = threading.Event()
        self._run_lock = threading.Lock()
        self._started = False

    def power_on(self) -> None:
        """Model a user-controlled cold start; no DTR or serial I/O occurs."""

        self._power_event.set()

    def cancel(self) -> None:
        self._cancel_event.set()

    def _event(
        self,
        kind: str,
        message: str,
        *,
        direction: str | None = None,
        data: bytes = b"",
    ) -> None:
        self.emit(SessionEvent(kind, self.state, self.progress, message, direction, data))

    def _transition(self, state: SessionState, progress: int, message: str) -> None:
        self.state = state
        self.progress = progress
        self._event("state", message)

    def _check_cancelled(self) -> None:
        if self._cancel_event.is_set():
            raise SimulationCancelled("用户已取消模拟下载")

    def _wait_power(self) -> None:
        self._transition(
            SessionState.WAIT_POWER,
            0,
            f"等待手动模拟上电（最长 {self.wait_power_seconds:g} 秒）",
        )
        deadline = time.monotonic() + self.wait_power_seconds
        while not self._power_event.is_set():
            self._check_cancelled()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise SimulationTimeout("等待模拟上电超时")
            self._power_event.wait(min(remaining, 0.02))
        self._check_cancelled()
        self._event("info", "已触发模拟上电，等待模拟设备启动")
        remaining = deadline - time.monotonic()
        if remaining <= 0 or self.simulator.cold_start_delay > remaining:
            raise SimulationTimeout("模拟冷启动超出等待时间")
        self.simulator.wait_for_boot(self._cancel_event)
        self._check_cancelled()

    def _exchange(self, stage: SessionState, label: str, payload: bytes) -> None:
        self._check_cancelled()
        command = b"SIM/" + label.encode("ascii") + b"/" + payload
        expected_ack = b"SIM/ACK/" + label.encode("ascii")
        tx = encode_frame(
            command, direction="tx", checksum_bits=self.simulator.checksum_bits
        )
        self._event("tx", "模拟 TX", direction="TX", data=tx)
        chunks: list[bytes] = []
        for chunk in self.simulator.exchange(
            tx,
            stage=stage.value,
            expected_ack=expected_ack,
            cancel_event=self._cancel_event,
        ):
            self._event("rx", "模拟 RX（分片）", direction="RX", data=chunk)
            chunks.append(chunk)
        self._check_cancelled()
        response = decode_frame(
            b"".join(chunks),
            direction="rx",
            checksum_bits=self.simulator.checksum_bits,
        )
        if response != expected_ack:
            raise SimulationError(f"{stage.value} 模拟 ACK 不匹配")

    def run(self) -> SessionState:
        with self._run_lock:
            if self._started:
                raise RuntimeError("DownloadSession cannot be run twice")
            self._started = True
        try:
            self._wait_power()
            self._exchange(SessionState.IDENTIFIED, "IDENTIFY", b"")
            self._transition(SessionState.IDENTIFIED, 10, f"模拟芯片已识别：{self.chip_name}（演示数据）")
            self._transition(SessionState.NEGOTIATING, 20, "模拟链路协商")
            self._exchange(SessionState.NEGOTIATING, "NEGOTIATE", b"")
            self._transition(SessionState.ERASING, 30, "模拟擦除程序区")
            self._exchange(SessionState.ERASING, "ERASE", b"")
            span_blocks = self.image.max_address // 64 - self.image.min_address // 64 + 1
            if span_blocks > MAX_SESSION_BLOCKS:
                raise SimulationError(
                    "模拟数据跨度超过 64 KiB 安全预览上限；未执行真实设备操作"
                )
            blocks = tuple(self.image.iter_blocks(block_size=64, fill=0xFF))
            self._transition(SessionState.PROGRAMMING, 30, "模拟写入程序块")
            for index, (address, data) in enumerate(blocks, 1):
                self._exchange(
                    SessionState.PROGRAMMING,
                    "PROGRAM",
                    address.to_bytes(4, "big") + data,
                )
                self.progress = 30 + round(60 * index / len(blocks))
                self._event(
                    "progress",
                    f"模拟写入 {index}/{len(blocks)} 块，地址 0x{address:04X}",
                )
            self._transition(SessionState.VERIFYING, 95, "模拟结果确认")
            self._exchange(SessionState.VERIFYING, "VERIFY", b"")
            self._transition(SessionState.COMPLETE, 100, "模拟下载完成；未连接或修改真实芯片")
        except SimulationCancelled as exc:
            self._transition(SessionState.CANCELLED, self.progress, str(exc))
        except (SimulationError, FrameError, ValueError) as exc:
            self.state = SessionState.FAILED
            self._event("error", f"模拟下载失败：{exc}")
        finally:
            self.simulator.close()
        return self.state
