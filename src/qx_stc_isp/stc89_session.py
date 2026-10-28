"""Transport-injected STC89 identification and explicitly gated download core.

Importing or constructing these objects never discovers or opens a device.
The GUI may expose only Stc89ReadOnlySession; the write engine is not registered
in HARDWARE_DIALECTS and defaults to refusing every erase/write operation.
"""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable

from .framing import FrameError, FrameStreamDecoder, decode_frame, encode_frame
from .hex_image import HexImage
from .session import SessionEvent, SessionState
from .stc89 import (
    BLOCK_BYTES, ERASE_BYTES, PROGRAM_BYTES, ProtocolError, Stc89BaudPlan,
    Stc89Status, access_payload, erase_payload, require_write_ack,
    resolve_accepted_profile, write_payload,
)
from .transport import SerialTransport, TransportCancelled, TransportError, TransportTimeout


def _positive_seconds(value: float, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be finite and positive")
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be finite and positive")
    return float(value)


class Stc89ReadOnlySession:
    """Only send bounded 7F sync bytes, parse status, then close the port.

    "Read-only" means no erase/program/options/reset commands, not zero TX.
    Opening a physical serial port may change DTR/RTS through its driver.
    The caller must acquire its shared port lease before run() and release it
    afterwards; this engine owns transport open/close, not global GUI leases.
    """

    phase_states = (SessionState.WAIT_POWER, SessionState.IDENTIFIED, SessionState.COMPLETE)
    operation_label = "只读识别"
    identification_note = "仅发送 7F，不擦写"

    def __init__(self, transport: SerialTransport,
                 emit: Callable[[SessionEvent], None] | None = None, *,
                 wait_power_seconds: float = 30, initial_baudrate: int = 2400,
                 sync_interval: float = 0.05) -> None:
        self.wait_power_seconds = _positive_seconds(wait_power_seconds, "wait_power_seconds")
        self.sync_interval = _positive_seconds(sync_interval, "sync_interval")
        if initial_baudrate != 2400:
            raise ValueError("当前只读识别仅覆盖 2400 / 8N1")
        self.transport = transport
        self.emit = emit if emit is not None else lambda _event: None
        self.initial_baudrate = initial_baudrate
        self.state = SessionState.IDLE
        self.progress = 0
        self.status: Stc89Status | None = None
        self.error: str | None = None
        self._cancelled = threading.Event()
        self._run_lock = threading.Lock()
        self._started = False
        self._decoder = FrameStreamDecoder()

    @property
    def known_profile(self) -> bool:
        if self.status is None:
            return False
        try:
            self.status.require_captured_profile()
        except ProtocolError:
            return False
        return True

    def cancel(self) -> None:
        self._cancelled.set()
        try:
            self.transport.cancel()
        except TransportError:
            # Cancellation still succeeds logically. run() always attempts
            # close and all reads use a <=50 ms timeout for cooperative exit.
            pass

    def _event(self, kind: str, message: str, *, direction: str | None = None,
               data: bytes = b"") -> None:
        self.emit(SessionEvent(kind, self.state, self.progress, message, direction, data))

    def _transition(self, state: SessionState, progress: int, message: str) -> None:
        self.state, self.progress = state, progress
        self._event("state", message)

    def _check_cancelled(self) -> None:
        if self._cancelled.is_set():
            raise TransportCancelled("用户已取消；没有继续发送命令")

    def _write(self, raw: bytes, *, timeout: float = 1) -> None:
        self._check_cancelled()
        self.transport.write(raw, timeout=timeout)
        self._event("tx", "同步字节" if raw == b"\x7f" else "ISP 帧", direction="TX", data=raw)
        self._check_cancelled()

    def _read_chunk(self, timeout: float) -> list[bytes]:
        self._check_cancelled()
        raw = self.transport.read_some(256, timeout=max(0.0001, min(0.05, timeout)))
        self._check_cancelled()
        if raw:
            self._event("rx", "串口接收原始字节", direction="RX", data=raw)
        return self._decoder.feed(raw)

    def _identify(self) -> None:
        self._transition(SessionState.WAIT_POWER, 0,
                         f"{self.operation_label}：请手动断电后重新上电，最长等待 {self.wait_power_seconds:g} 秒；{self.identification_note}")
        deadline = time.monotonic() + self.wait_power_seconds
        next_sync = 0.0
        while True:
            self._check_cancelled()
            now = time.monotonic()
            if now >= deadline:
                raise TransportTimeout("等待冷启动状态包超时；未发送擦写或复位命令")
            if now >= next_sync:
                self._write(b"\x7f", timeout=min(1, max(0.0001, deadline - now)))
                next_sync = time.monotonic() + self.sync_interval
            remaining = min(deadline, next_sync) - time.monotonic()
            frames = self._read_chunk(max(0.0001, remaining))
            if not frames:
                continue
            if len(frames) != 1:
                raise ProtocolError("识别阶段收到多个意外状态帧；拒绝继续")
            self._decoder.finish()
            self.status = Stc89Status.parse(decode_frame(frames[0], direction="rx"))
            disposition = "与成功抓包的版本指纹匹配" if self.known_profile else "已识别但不支持烧录"
            self._transition(SessionState.IDENTIFIED, 20,
                             f"芯片 ID 0x{self.status.chip_id:04X}，Bootloader {self.status.version_label}；{disposition}")
            self._event("info", f"状态包 {self.status.payload_size} 字节；帧前噪声 {self._decoder.discarded_noise} 字节")
            return

    def _execute(self) -> None:
        self._identify()

    def _completion_message(self) -> str:
        return "只读识别完成；未擦除、未写入、未修改选项、未发送结束复位命令"

    def _terminal_message(self, state: SessionState, message: str) -> str:
        return message

    def run(self) -> SessionState:
        with self._run_lock:
            if self._started:
                raise RuntimeError("STC89 session cannot be run twice")
            self._started = True
        terminal_state = SessionState.FAILED
        terminal_message = "识别未完成"
        try:
            self._check_cancelled()
            self.transport.open(self.initial_baudrate, bytesize=8, parity="N", stopbits=1)
            self._check_cancelled()
            self._execute()
            self._check_cancelled()
            terminal_state, terminal_message = SessionState.COMPLETE, self._completion_message()
        except TransportCancelled as exc:
            terminal_state, terminal_message = SessionState.CANCELLED, str(exc)
        except (TransportError, FrameError, ProtocolError, ValueError) as exc:
            terminal_message = str(exc)
        except Exception as exc:
            # Unexpected adapter failures must still close the port and never
            # leave the GUI waiting indefinitely for a final state event.
            terminal_message = f"串口会话异常：{type(exc).__name__}: {exc}"
        finally:
            try:
                self.transport.close()
            except Exception as exc:
                terminal_state = SessionState.FAILED
                terminal_message = f"串口关闭失败，不能确认已释放：{exc}"
        terminal_message = self._terminal_message(terminal_state, terminal_message)
        if terminal_state == SessionState.FAILED:
            self.error = terminal_message
        self._transition(terminal_state, 100 if terminal_state == SessionState.COMPLETE else self.progress,
                         terminal_message)
        return self.state


class Stc89EraseSession(Stc89ReadOnlySession):
    """Explicit, experimental 8 KiB application-area erase for F002/6.6C.

    The sector-count rule has static evidence; only the one-sector request has
    hardware captures. A matching ACK is not a blank check. No write, options,
    EEPROM or unobserved erase-only finish/reset commands are sent.
    """

    phase_states = (SessionState.WAIT_POWER, SessionState.IDENTIFIED,
                    SessionState.NEGOTIATING, SessionState.ERASING, SessionState.COMPLETE)
    operation_label = "全程序区擦除"
    identification_note = "识别严格指纹后请求擦除8KiB用户程序区"

    def __init__(self, transport: SerialTransport,
                 emit: Callable[[SessionEvent], None] | None = None, *,
                 allow_erase: bool = False, wait_power_seconds: float = 30,
                 response_timeout: float = 5, requested_baudrate: int = 115200,
                 initial_baudrate: int = 2400, sync_interval: float = 0.05) -> None:
        super().__init__(transport, emit, wait_power_seconds=wait_power_seconds,
                         initial_baudrate=initial_baudrate, sync_interval=sync_interval)
        if not isinstance(allow_erase, bool):
            raise ValueError("allow_erase must be an explicit boolean")
        self.allow_erase = allow_erase
        self.response_timeout = _positive_seconds(response_timeout, "response_timeout")
        self.requested_baudrate = requested_baudrate
        self.baud_plan: Stc89BaudPlan | None = None
        self.erase_started = False
        self.erase_request_bytes = 0
        self.erase_ack_received = False
        self.acknowledged_bytes = 0
        self.profile = None

    def _read_payload(self) -> bytes:
        deadline = time.monotonic() + self.response_timeout
        while True:
            self._check_cancelled()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TransportTimeout("ISP 应答超时；不自动重试，必须重新上电后重新开始")
            frames = self._read_chunk(remaining)
            if not frames:
                continue
            if len(frames) != 1:
                raise ProtocolError("一个事务收到多个意外应答，停止下载")
            self._decoder.finish()
            return decode_frame(frames[0], direction="rx")

    def _send_payload(self, payload: bytes) -> None:
        self._write(encode_frame(payload))

    def _expect(self, expected: bytes) -> None:
        if self._read_payload() != expected:
            raise ProtocolError("ISP 应答不匹配；不重试，请重新上电后重新开始")

    def _pause(self, seconds: float) -> None:
        if self._cancelled.wait(seconds):
            self._check_cancelled()

    def _set_baud(self, rate: int, *, settle: bool) -> None:
        if settle:
            # FUN_0043d310 waits 200 ms at the captured initial 2400 rate
            # BEFORE changing the DCB; this is not an arbitrary retry delay.
            self._pause(0.2)
        self._check_cancelled()
        self.transport.reconfigure(rate, bytesize=8, parity="N", stopbits=1)
        # The official capture issues PURGECOMM after every DCB change,
        # including the switch back to 2400. flush() is not a purge.
        self.transport.purge_buffers()
        self._decoder = FrameStreamDecoder()
        self._event("info", f"串口配置 {rate} / 8N1；已清空收发缓冲区")

    def _prepare(self) -> None:
        if not callable(getattr(self.transport, "purge_buffers", None)):
            raise ProtocolError("传输层缺少收发缓冲区清空能力；未进入识别或协商")
        self.transport.purge_buffers()
        self._event("info", "串口打开后已清空收发缓冲区")
        self._identify()
        assert self.status is not None
        self.status.require_captured_profile()
        self.baud_plan = Stc89BaudPlan.derive(self.status,
                                           initial_baudrate=self.initial_baudrate,
                                           requested_baudrate=self.requested_baudrate)
        self.profile = resolve_accepted_profile(self.status.chip_id, self.status.version_raw)
        if self.profile is None:
            raise ProtocolError("该芯片/版本指纹未注册为已验收硬件方言；未进入协商或擦写")
        if not self.allow_erase:
            raise ProtocolError("真实擦写保持锁定；本会话在波特率协商和擦除之前停止")

    def _negotiate(self) -> None:
        assert self.baud_plan is not None and self.profile is not None
        self._transition(SessionState.NEGOTIATING, 20,
                         f"已注册指纹 {self.profile.key}：测量时钟 {self.baud_plan.clock_hz} Hz，目标 {self.baud_plan.baudrate}")
        self._send_payload(self.baud_plan.parameters.probe())
        self._set_baud(self.baud_plan.baudrate, settle=True)
        self._expect(self.baud_plan.parameters.probe())
        self._set_baud(self.initial_baudrate, settle=False)
        self._send_payload(self.baud_plan.parameters.activate())
        self._set_baud(self.baud_plan.baudrate, settle=True)
        self._expect(self.baud_plan.parameters.activate())
        self._pause(0.01)
        for _ in range(5):
            self._send_payload(access_payload())
            self._expect(b"\x80")

    def _erase(self, end: int) -> None:
        erase_command = erase_payload(end)
        self._check_cancelled()
        self._transition(SessionState.ERASING, 30,
                         f"请求擦除用户程序区 0x0000–0x{end - 1:04X}；此后异常不自动重试")
        self._check_cancelled()
        self.erase_request_bytes = end
        self.erase_started = True
        self._send_payload(erase_command)
        erase_ack = self._read_payload()
        if len(erase_ack) != 8 or erase_ack[:3] != b"\x80\xf0\x02":
            raise ProtocolError("擦除应答长度、标记或芯片 ID 不匹配；停止且不重试")
        self.erase_ack_received = True

    def _execute(self) -> None:
        self._prepare()
        self._negotiate()
        self._erase(PROGRAM_BYTES)

    def _completion_message(self) -> str:
        return "8 KiB 用户程序区擦除请求应答匹配；未全区回读、未写入程序。请手动断电后重新上电"

    def _terminal_message(self, state: SessionState, message: str) -> str:
        if self.erase_started and state != SessionState.COMPLETE:
            return message + "；擦除已开始，目标程序可能不完整；立即中止，不自动重试，请重新上电后重新开始"
        return message


class Stc89DownloadSession(Stc89EraseSession):
    """Existing captured download; full application erase is opt-in only."""

    phase_states = (SessionState.WAIT_POWER, SessionState.IDENTIFIED,
                    SessionState.NEGOTIATING, SessionState.ERASING,
                    SessionState.PROGRAMMING, SessionState.VERIFYING, SessionState.COMPLETE)
    operation_label = "烧录等待"
    identification_note = "先核对指纹，再按本次范围擦写"

    def __init__(self, image: HexImage, transport: SerialTransport,
                 emit: Callable[[SessionEvent], None] | None = None, *,
                 erase_full_program: bool = False, **kwargs) -> None:
        if type(erase_full_program) is not bool:
            raise ValueError("erase_full_program must be an explicit boolean")
        super().__init__(transport, emit, **kwargs)
        self.image = image
        self.erase_full_program = erase_full_program

    def _execute(self) -> None:
        self._prepare()
        if self.image.min_address < 0 or self.image.max_address >= PROGRAM_BYTES:
            raise ProtocolError("HEX 超过 F002 的 8 KiB 程序区；未进入擦写")
        # Validate every write before erase. Erasing all sectors must not turn
        # a short image into an 8 KiB write of padding.
        end = (self.image.max_address // ERASE_BYTES + 1) * ERASE_BYTES
        padded = bytearray(b"\xff" * end)
        for start, data in self.image.segments:
            padded[start:start + len(data)] = data
        commands = tuple((write_payload(address, bytes(padded[address:address + BLOCK_BYTES])),
                          bytes(padded[address:address + BLOCK_BYTES]))
                         for address in range(0, end, BLOCK_BYTES))
        self._negotiate()
        self._erase(PROGRAM_BYTES if self.erase_full_program else self.image.max_address + 1)
        self._transition(SessionState.PROGRAMMING, 30, "逐块发送并核对写入 ACK；不是全片回读")
        for command, block in commands:
            self._send_payload(command)
            require_write_ack(self._read_payload(), block)
            self.acknowledged_bytes += len(block)
            self.progress = 30 + round(60 * self.acknowledged_bytes / end)
            self._event("progress", f"已收到块 ACK：{self.acknowledged_bytes}/{end} 字节")
            self._pause(0.005)
        self._transition(SessionState.VERIFYING, 95, "全部块 ACK 匹配；无独立全片回读，结束序列没有已知应答")
        self._send_payload(b"\xff")
        self._send_payload(b"\x82")
        self._pause(0.05)

    def _completion_message(self) -> str:
        return "已注册指纹事务完成，块 ACK 全部匹配；未做全片回读，结束序列没有已知应答"

    def _terminal_message(self, state: SessionState, message: str) -> str:
        if self.erase_started and state != SessionState.COMPLETE:
            return message + "；擦除已开始，目标程序可能不完整；立即中止，不自动重试，请重新上电后重新开始"
        return message

