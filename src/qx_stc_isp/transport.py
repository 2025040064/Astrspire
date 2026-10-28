"""Serial transport boundary; construction and import never open a COM port."""

from __future__ import annotations

import errno
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any

import serial


class TransportError(Exception):
    """A serial transport operation failed."""


class PortBusyError(TransportError):
    """The port could not be opened because access was denied or it is busy."""


class TransportDisconnected(TransportError):
    """The port disappeared or closed while an operation was running."""


class TransportTimeout(TransportError):
    """A bounded operation did not finish by its deadline."""

    def __init__(self, message: str, partial: bytes = b"") -> None:
        super().__init__(message)
        self.partial = partial


class TransportCancelled(TransportError):
    """The user cancelled a pending operation."""


class SerialTransport(ABC):
    """Small interface shared by real and simulated serial transports."""

    @property
    @abstractmethod
    def is_open(self) -> bool: ...

    @abstractmethod
    def open(
        self,
        baudrate: int,
        *,
        bytesize: int = 8,
        parity: str = "N",
        stopbits: float = 1,
    ) -> None: ...

    @abstractmethod
    def reconfigure(
        self,
        baudrate: int,
        *,
        bytesize: int = 8,
        parity: str = "N",
        stopbits: float = 1,
    ) -> None: ...

    @abstractmethod
    def read_exact(self, size: int, timeout: float) -> bytes: ...

    @abstractmethod
    def read_some(self, max_bytes: int, timeout: float) -> bytes:
        """Read at most ``max_bytes`` bytes; return ``b''`` on timeout."""
        ...

    @abstractmethod
    def write(self, data: bytes, timeout: float = 5.0) -> None: ...

    @abstractmethod
    def flush(self) -> None: ...

    def purge_buffers(self) -> None:
        """Discard queued RX/TX bytes, if supported; this is not ``flush``.

        Existing transports remain valid without implementing this optional
        capability. Callers must not silently replace a failed purge with a
        flush, which waits for pending output instead of discarding it.
        """

        raise TransportError("此串口传输不支持丢弃收发缓冲区")

    @abstractmethod
    def cancel(self) -> None: ...

    @abstractmethod
    def close(self) -> None: ...


class PySerialTransport(SerialTransport):
    """pySerial adapter.  Only an explicit ``open`` accesses the device."""

    def __init__(
        self,
        device: str,
        *,
        serial_factory: Callable[..., Any] | None = None,
        dtr: bool | None = None,
        rts: bool | None = None,
        flow_control: str | None = None,
    ) -> None:
        if not device:
            raise ValueError("串口名称不能为空")
        if any(value is not None and type(value) is not bool for value in (dtr, rts)):
            raise ValueError("DTR/RTS必须为布尔值或未指定")
        self.device = device
        if flow_control not in (None, "none", "rtscts", "xonxoff"):
            raise ValueError("串口流控无效")
        self._flow_control = flow_control
        self._dtr, self._rts = dtr, rts
        self._serial_factory = serial_factory or serial.Serial
        self._serial: Any | None = None
        self._handle_lock = threading.RLock()
        self._cancelled = threading.Event()

    @property
    def is_open(self) -> bool:
        return self._serial is not None and bool(self._serial.is_open)

    @staticmethod
    def _baudrate(baudrate: int) -> int:
        if isinstance(baudrate, bool) or not isinstance(baudrate, int) or baudrate <= 0:
            raise ValueError("波特率必须为正整数")
        return baudrate

    @staticmethod
    def _is_busy(exc: BaseException) -> bool:
        if isinstance(exc, PermissionError):
            return True
        if getattr(exc, "errno", None) in (errno.EACCES, errno.EBUSY):
            return True
        if getattr(exc, "winerror", None) in (5, 32):
            return True
        message = str(exc).casefold()
        return "access is denied" in message or "permission denied" in message

    def open(
        self,
        baudrate: int,
        *,
        bytesize: int = 8,
        parity: str = "N",
        stopbits: float = 1,
    ) -> None:
        if self.is_open:
            raise TransportError(f"串口 {self.device} 已打开")
        rate = self._baudrate(baudrate)
        self._cancelled.clear()
        handle = None
        deferred = self._dtr is not None or self._rts is not None
        try:
            handle = self._serial_factory(
                port=None if deferred else self.device,
                baudrate=rate,
                bytesize=bytesize,
                parity=parity,
                stopbits=stopbits,
                timeout=0.05,
                write_timeout=5.0,
                **({"rtscts": self._flow_control == "rtscts", "xonxoff": self._flow_control == "xonxoff"}
                   if self._flow_control not in (None, "none") else {}),
            )
            if deferred:
                # Do not briefly assert pySerial's default DTR/RTS before the
                # user's requested levels. No explicit options means legacy ISP
                # callers retain their existing constructor/open behavior.
                if self._dtr is not None:
                    handle.dtr = self._dtr
                if self._rts is not None:
                    handle.rts = self._rts
                handle.port = self.device
                handle.open()
        except (serial.SerialException, OSError) as exc:
            if handle is not None:
                try:
                    handle.close()
                except (serial.SerialException, OSError):
                    pass
            if self._is_busy(exc):
                raise PortBusyError(f"串口 {self.device} 被占用或访问被拒绝：{exc}") from exc
            raise TransportError(f"无法打开串口 {self.device}：{exc}") from exc
        self._serial = handle

    def _require_open(self) -> Any:
        if not self.is_open:
            raise TransportDisconnected(f"串口 {self.device} 未连接")
        return self._serial

    def reconfigure(
        self,
        baudrate: int,
        *,
        bytesize: int = 8,
        parity: str = "N",
        stopbits: float = 1,
    ) -> None:
        handle = self._require_open()
        rate = self._baudrate(baudrate)
        try:
            handle.baudrate = rate
            handle.bytesize = bytesize
            handle.parity = parity
            handle.stopbits = stopbits
        except (serial.SerialException, OSError, ValueError) as exc:
            self.close()
            raise TransportError(f"串口 {self.device} 重配置失败：{exc}") from exc

    def _check_cancelled(self) -> None:
        if self._cancelled.is_set():
            raise TransportCancelled("串口操作已取消")

    def read_exact(self, size: int, timeout: float) -> bytes:
        if size < 0:
            raise ValueError("读取长度不能为负数")
        if timeout < 0:
            raise ValueError("超时不能为负数")
        handle = self._require_open()
        self._check_cancelled()
        if size == 0:
            return b""
        deadline = time.monotonic() + timeout
        result = bytearray()
        while len(result) < size:
            self._check_cancelled()
            if not self.is_open:
                raise TransportDisconnected(f"串口 {self.device} 读取时断开")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TransportTimeout(
                    f"串口 {self.device} 读取超时（收到 {len(result)}/{size} 字节）",
                    bytes(result),
                )
            try:
                handle.timeout = min(remaining, 0.05)
                chunk = handle.read(size - len(result))
            except (serial.SerialException, OSError) as exc:
                self._check_cancelled()
                self.close()
                raise TransportDisconnected(f"串口 {self.device} 读取时断开：{exc}") from exc
            if chunk:
                result.extend(chunk)
        return bytes(result)

    def read_some(self, max_bytes: int, timeout: float) -> bytes:
        """Bounded, passive receive for a monitor; never transmits data.

        An empty result means that the deadline expired without data, not that
        the device disconnected. A closed or unplugged port raises instead.
        """

        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 0:
            raise ValueError("最大读取长度必须为非负整数")
        if timeout < 0:
            raise ValueError("超时不能为负数")
        handle = self._require_open()
        self._check_cancelled()
        if max_bytes == 0:
            return b""
        try:
            handle.timeout = timeout
            data = handle.read(max_bytes)
        except (serial.SerialException, OSError) as exc:
            self._check_cancelled()
            self.close()
            raise TransportDisconnected(f"串口 {self.device} 读取时断开：{exc}") from exc
        self._check_cancelled()
        if not self.is_open:
            raise TransportDisconnected(f"串口 {self.device} 读取时断开")
        return bytes(data)

    def write(self, data: bytes, timeout: float = 5.0) -> None:
        if timeout <= 0:
            raise ValueError("写入超时必须大于零")
        handle = self._require_open()
        self._check_cancelled()
        payload = memoryview(data)
        deadline = time.monotonic() + timeout
        sent = 0
        while sent < len(payload):
            self._check_cancelled()
            if not self.is_open:
                raise TransportDisconnected(f"串口 {self.device} 写入时断开")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TransportTimeout(
                    f"串口 {self.device} 写入超时（已发送 {sent}/{len(payload)} 字节）"
                )
            try:
                handle.write_timeout = remaining
                count = handle.write(payload[sent:].tobytes())
            except serial.SerialTimeoutException as exc:
                raise TransportTimeout(f"串口 {self.device} 写入超时") from exc
            except (serial.SerialException, OSError) as exc:
                self._check_cancelled()
                self.close()
                raise TransportDisconnected(f"串口 {self.device} 写入时断开：{exc}") from exc
            if count is None or count < 0 or count > len(payload) - sent:
                raise TransportError("串口驱动报告无效写入长度")
            sent += count

    def flush(self) -> None:
        handle = self._require_open()
        self._check_cancelled()
        try:
            handle.flush()
        except (serial.SerialException, OSError) as exc:
            self._check_cancelled()
            self.close()
            raise TransportDisconnected(f"串口 {self.device} 刷新时断开：{exc}") from exc

    def purge_buffers(self) -> None:
        """Discard pending input and output without transmitting or waiting.

        Cancellation is checked before each driver call and after both calls.
        If either reset fails, the handle is retired rather than leaving an
        unknown partial purge available for subsequent protocol operations.
        """

        self._check_cancelled()
        handle = self._require_open()
        try:
            handle.reset_input_buffer()
            self._check_cancelled()
            self._require_open()
            handle.reset_output_buffer()
            self._check_cancelled()
            self._require_open()
        except (TransportCancelled, TransportDisconnected, serial.SerialException, OSError) as exc:
            was_cancelled = self._cancelled.is_set()
            cleanup_detail = ""
            try:
                self.close()
            except TransportError as cleanup_error:
                # close() detaches the handle before attempting driver cleanup.
                # Keep the original failure as the cause, including close errors
                # in the message instead of masking a cancellation/reset failure.
                cleanup_detail = f"；清理失败：{cleanup_error}"
            if was_cancelled or isinstance(exc, TransportCancelled):
                raise TransportCancelled(
                    f"串口 {self.device} 丢弃收发缓冲区已取消{cleanup_detail}"
                ) from exc
            raise TransportDisconnected(
                f"串口 {self.device} 丢弃收发缓冲区失败：{exc}{cleanup_detail}"
            ) from exc

    def cancel(self) -> None:
        self._cancelled.set()
        # pySerial's Windows cancellation uses OVERLAPPED objects which close()
        # clears. Serialize only cancellation/close, never blocking RX/TX.
        with self._handle_lock:
            handle = self._serial
            if handle is None:
                return
            for method_name in ("cancel_read", "cancel_write"):
                if self._serial is not handle:
                    break
                method = getattr(handle, method_name, None)
                if callable(method):
                    try:
                        method()
                    except (serial.SerialException, OSError):
                        # The handle may have been unplugged during cancellation.
                        pass

    def close(self) -> None:
        self._cancelled.set()
        with self._handle_lock:
            handle, self._serial = self._serial, None
            if handle is not None:
                try:
                    handle.close()
                except (serial.SerialException, OSError) as exc:
                    raise TransportError(f"关闭串口 {self.device} 失败：{exc}") from exc
