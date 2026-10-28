"""Validate and replay the successful official capture entirely in memory."""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .framing import FrameStreamDecoder, decode_frame, encode_frame
from .hex_image import HexImage
from .session import SessionEvent, SessionState
from .stc89 import (
    CapturedBaudParameters, ProtocolError, Stc89Status, access_payload,
    erase_payload, require_write_ack, write_payload,
)

OFFICIAL_TRACE_SHA256 = "9955278c434dde1a80da9282634311a8494d22d31779b09ac76782f9ad35674c"
MAX_TRACE_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True)
class ReplayStep:
    direction: str
    frame: bytes
    state: SessionState
    progress: int


@dataclass(frozen=True)
class OfficialCapture:
    status: Stc89Status
    steps: tuple[ReplayStep, ...]
    sync_count: int
    discarded_noise: int
    digest: str

    @classmethod
    def from_file(cls, path: Path, image: HexImage) -> OfficialCapture:
        with path.open("rb") as source:
            data = source.read(MAX_TRACE_BYTES + 1)
        if len(data) > MAX_TRACE_BYTES:
            raise ProtocolError("抓包文件超过离线回放大小上限")
        digest = hashlib.sha256(data).hexdigest()
        if digest != OFFICIAL_TRACE_SHA256:
            raise ProtocolError("当前回放只接受 2026-10-21 已核对的官方成功记录")
        events = [json.loads(line) for line in data.decode("utf-8").splitlines()]
        if not events or events[-1].get("kind") != "CLOSE":
            raise ProtocolError("抓包尚未记录串口关闭")
        stream = FrameStreamDecoder()
        frames: list[tuple[str, bytes]] = []
        sync_count = 0
        ready = False
        for event in events:
            kind = event["kind"]
            if kind == "READY":
                ready = True
            if kind in {"FRIDA_SCRIPT_ERROR", "HOOK_UNAVAILABLE", "TX_FAILED"} or "PENDING" in kind:
                raise ProtocolError("抓包包含错误或未支持的异步 I/O 路径")
            if kind not in {"TX_COMPLETE", "RX_COMPLETE"}:
                continue
            raw = bytes.fromhex(event["hex"])
            if not ready or event.get("truncated") or len(raw) != event.get("captured"):
                raise ProtocolError("串口事件没有被完整记录")
            if kind == "TX_COMPLETE":
                if len(raw) != event.get("count"):
                    raise ProtocolError("实际完成的写入长度与记录不符")
                if raw == b"\x7f":
                    sync_count += 1
                else:
                    decode_frame(raw, direction="tx")
                    frames.append(("TX", raw))
            else:
                frames.extend(("RX", frame) for frame in stream.feed(raw))
        stream.finish()
        status, steps = validate_transactions(frames, image)
        return cls(status, steps, sync_count, stream.discarded_noise, digest)


def validate_transactions(frames: list[tuple[str, bytes]], image: HexImage) -> tuple[Stc89Status, tuple[ReplayStep, ...]]:
    """Independent check of every transaction, including order and source HEX."""
    if not frames or frames[0][0] != "RX":
        raise ProtocolError("会话必须以有效的目标状态帧开始")
    status = Stc89Status.parse(decode_frame(frames[0][1], direction="rx"))
    status.require_captured_profile()
    if image.min_address < 0 or image.max_address >= 8192:
        raise ProtocolError("HEX 超过本适配的 8 KiB 程序区")
    if len(frames) < 2 or frames[1][0] != "TX":
        raise ProtocolError("缺少波特率探测事务")
    parameters = CapturedBaudParameters.parse_probe(decode_frame(frames[1][1], direction="tx"))
    # This validates the parameter set from the captured negotiation. It never
    # treats the observed reload as a universal value for a physical device.
    end = (image.max_address // 512 + 1) * 512
    source = {start + i: value for start, data in image.segments for i, value in enumerate(data)}
    transactions: list[tuple[bytes, bytes | None, SessionState, int]] = [
        (parameters.probe(), parameters.probe(), SessionState.NEGOTIATING, 15),
        (parameters.activate(), parameters.activate(), SessionState.NEGOTIATING, 20),
    ]
    transactions.extend((access_payload(), b"\x80", SessionState.NEGOTIATING, 25) for _ in range(5))
    # Erase ACK includes device-specific fields; its complete frame checksum
    # and observed length are checked, but its UID is not hardcoded.
    transactions.append((erase_payload(image.max_address + 1), None, SessionState.ERASING, 30))
    for address in range(0, end, 128):
        block = bytes(source.get(address + i, 255) for i in range(128))
        progress = 30 + round(60 * (address + 128) / end)
        transactions.append((write_payload(address, block), bytes((0x80, sum(block) & 255)), SessionState.PROGRAMMING, progress))
    transactions.extend([
        (b"\xff", None, SessionState.VERIFYING, 95),
        (b"\x82", None, SessionState.VERIFYING, 98),
    ])
    steps = [ReplayStep("RX", frames[0][1], SessionState.IDENTIFIED, 10)]
    cursor = 1
    for command, expected_ack, state, progress in transactions:
        if cursor >= len(frames) or frames[cursor] != ("TX", encode_frame(command)):
            raise ProtocolError(f"官方 TX 顺序或 HEX 数据不匹配：{state.value}")
        steps.append(ReplayStep("TX", frames[cursor][1], state, max(0, progress - 1)))
        cursor += 1
        if command in (b"\xff", b"\x82"):
            continue  # No end ACK was observed; do not invent one.
        if cursor >= len(frames) or frames[cursor][0] != "RX":
            raise ProtocolError(f"缺少官方 RX 应答：{state.value}")
        ack = decode_frame(frames[cursor][1], direction="rx")
        if state == SessionState.ERASING:
            if len(ack) != 8 or ack[:3] != b"\x80\xf0\x02":
                raise ProtocolError("擦除应答格式或芯片 ID 不匹配")
        elif state == SessionState.PROGRAMMING:
            require_write_ack(ack, command[7:])
        elif ack != expected_ack:
            raise ProtocolError(f"官方应答不匹配：{state.value}")
        steps.append(ReplayStep("RX", frames[cursor][1], state, progress))
        cursor += 1
    if cursor != len(frames):
        raise ProtocolError("会话尾部含未解释的事务")
    return status, tuple(steps)


class OfficialReplaySession:
    """Shares GUI events with DownloadSession; has no transport capability."""

    def __init__(self, capture: OfficialCapture, emit: Callable[[SessionEvent], None], *, step_delay: float = 0.06) -> None:
        if not 0 <= step_delay <= 1:
            raise ValueError("invalid replay step delay")
        self.capture = capture
        self.emit = emit
        self.step_delay = step_delay
        self.state = SessionState.IDLE
        self.progress = 0
        self._cancelled = threading.Event()
        self._lock = threading.Lock()
        self._started = False

    def cancel(self) -> None:
        self._cancelled.set()

    def run(self) -> SessionState:
        with self._lock:
            if self._started:
                raise RuntimeError("OfficialReplaySession cannot be run twice")
            self._started = True
        self.state = SessionState.WAIT_POWER
        self.emit(SessionEvent("state", self.state, 0, f"离线回放：{self.capture.sync_count} 个同步字节；不连接设备"))
        for step in self.capture.steps:
            if self._cancelled.wait(self.step_delay):
                self.state = SessionState.CANCELLED
                self.emit(SessionEvent("state", self.state, self.progress, "离线回放已取消"))
                return self.state
            self.state, self.progress = step.state, step.progress
            self.emit(SessionEvent("state", self.state, self.progress, f"离线回放 · {self.state.value} · 未实际烧录"))
            self.emit(SessionEvent(step.direction.lower(), self.state, self.progress, "离线记录", step.direction, step.frame))
        self.state, self.progress = SessionState.COMPLETE, 100
        self.emit(SessionEvent("state", self.state, 100, "官方会话离线回放完成；未连接硬件、未实际烧录"))
        return self.state
