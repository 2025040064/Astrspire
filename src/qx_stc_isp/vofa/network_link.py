"""Explicit, bounded IPv4 network sources for this application's telemetry UI.

This is an independent implementation, not a VOFA+ protocol compatibility claim.
TCP is a byte stream. UDP datagrams are decoded independently so fragments from
different senders can never be combined into one telemetry frame. Constructing a
configuration or worker performs no DNS lookup, socket creation, bind or connect.
"""

from __future__ import annotations

import errno
import ipaddress
import math
import os
import select
import socket
import threading
import time
from collections import deque
from dataclasses import dataclass, replace
from datetime import datetime, timezone

from PySide6.QtCore import QObject, Signal, Slot

from .decoders import create_decoder
from .model import TelemetryEvent
from .serial_link import MAX_RX_BATCH_BYTES, MAX_RX_BATCH_EVENTS, RX_BATCH_SECONDS, TxStatistics
from .source import BatchAcknowledger
from .transmit import MAX_SEND_BYTES, TransmitError


class NetworkError(Exception):
    """A network operation failed without automatically retrying a message."""


class _Stopped(Exception):
    """Internal cooperative cancellation, not a connection failure."""


def _ipv4(value: str, field: str, *, destination: bool = False) -> str:
    if not isinstance(value, str) or value != value.strip():
        raise ValueError(f"{field}必须是 IPv4 地址")
    try:
        address = ipaddress.IPv4Address(value)
    except ipaddress.AddressValueError as exc:
        raise ValueError(f"{field}必须是 IPv4 地址，不进行 DNS 查询") from exc
    if destination and (address.is_unspecified or address.is_multicast or int(address) == 0xFFFFFFFF):
        raise ValueError(f"{field}必须是明确的单播 IPv4 地址")
    return str(address)


def _port(value: int, field: str, *, allow_zero: bool = False) -> None:
    minimum = 0 if allow_zero else 1
    if type(value) is not int or not minimum <= value <= 65535:
        raise ValueError(f"{field}必须为 {minimum}–65535 的整数")


@dataclass(frozen=True, slots=True)
class NetworkConfig:
    kind: str
    host: str
    port: int
    peer_host: str | None = None
    peer_port: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, str) or self.kind not in {"tcp_client", "tcp_server", "udp"}:
            raise ValueError("不支持的网络数据源")
        _ipv4(self.host, "主机", destination=self.kind == "tcp_client")
        # Zero is useful for explicit loopback tests / OS-selected listeners.
        # A GUI can impose a nonzero port when users need a stable endpoint.
        _port(self.port, "端口", allow_zero=self.kind != "tcp_client")
        if (self.peer_host is None) != (self.peer_port is None):
            raise ValueError("UDP 发送目标地址和端口必须同时填写")
        if self.kind != "udp" and self.peer_host is not None:
            raise ValueError("只有 UDP 接受独立发送目标")
        if self.peer_host is not None:
            _ipv4(self.peer_host, "UDP 发送目标", destination=True)
            _port(self.peer_port, "UDP 发送端口")

    @property
    def peer(self) -> tuple[str, int] | None:
        if self.peer_host is None or self.peer_port is None:
            return None
        return self.peer_host, self.peer_port

    @property
    def label(self) -> str:
        name = {"tcp_client": "TCP 客户端", "tcp_server": "TCP 服务端", "udp": "UDP"}[self.kind]
        return f"{name} {self.host}:{self.port}"


class NetworkLinkWorker(QObject):
    """One TCP connection / UDP socket with an explicit, bounded TX queue.

    `run()` owns all reads and writes. UI calls only enqueue_send(),
    clear_pending() and stop(). A TCP server accepts a single peer then closes
    its listener; there is no implicit reconnect. UDP never learns a TX target
    from received traffic. A completed TX means socket acceptance, not a target
    application acknowledgement. Failed or cancelled active writes count as
    unconfirmed and are never retried.
    """

    events = Signal(object)
    raw = Signal(bytes)
    status = Signal(str, str)
    info = Signal(str)
    error = Signal(str)
    transmitted = Signal(bytes)
    finished = Signal()

    def __init__(self, config: NetworkConfig, decoder_name: str, *,
                 max_queue_packets: int = 64, max_queue_bytes: int = 64 * 1024,
                 max_packet_bytes: int = MAX_SEND_BYTES, read_timeout: float = 0.02,
                 connect_timeout: float = 5, write_timeout: float = 2,
                 await_ack: bool = False) -> None:
        super().__init__()
        if not isinstance(config, NetworkConfig):
            raise TypeError("网络配置类型无效")
        create_decoder(decoder_name)
        for limit in (max_queue_packets, max_queue_bytes, max_packet_bytes):
            if type(limit) is not int or limit <= 0:
                raise ValueError("发送队列上限必须为正整数")
        if max_packet_bytes > max_queue_bytes:
            raise ValueError("单包上限不能超过队列字节上限")
        for name, value in (("读取超时", read_timeout), ("连接超时", connect_timeout), ("写入超时", write_timeout)):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name}必须是有限正数")
        if read_timeout > 0.05:
            raise ValueError("读取超时不能超过 0.05 秒")
        self.config = config
        self._batch_ack = BatchAcknowledger(await_ack)
        self.decoder_name = decoder_name
        self.max_queue_packets = max_queue_packets
        self.max_queue_bytes = max_queue_bytes
        self.max_packet_bytes = max_packet_bytes
        self.read_timeout = float(read_timeout)
        self.connect_timeout = float(connect_timeout)
        self.write_timeout = float(write_timeout)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._socket: socket.socket | None = None
        self._listener: socket.socket | None = None
        self._bound_address: tuple[str, int] | None = None
        self._remote_address: tuple[str, int] | None = None
        self._connected = False
        self._started = False
        self._queue: deque[bytes] = deque()
        self._queued_bytes = 0
        self._accepted_packets = self._accepted_bytes = 0
        self._completed_packets = self._completed_bytes = 0
        self._failed_packets = self._unconfirmed_bytes = 0
        self._discarded_packets = self._discarded_bytes = 0
        self._in_flight_bytes = 0

    @property
    def connected(self) -> bool:
        with self._lock:
            return self._connected and not self._stop.is_set()

    @property
    def can_send(self) -> bool:
        return self.connected and (self.config.kind != "udp" or self.config.peer is not None)

    @property
    def bound_address(self) -> tuple[str, int] | None:
        with self._lock:
            return self._bound_address

    @property
    def remote_address(self) -> tuple[str, int] | None:
        with self._lock:
            return self._remote_address

    @property
    def statistics(self) -> TxStatistics:
        with self._lock:
            return TxStatistics(
                self._accepted_packets, self._accepted_bytes,
                self._completed_packets, self._completed_bytes,
                self._failed_packets, self._unconfirmed_bytes,
                self._discarded_packets, self._discarded_bytes,
                len(self._queue), self._queued_bytes,
                int(self._in_flight_bytes > 0), self._in_flight_bytes,
            )

    def enqueue_send(self, data: bytes) -> int:
        if not isinstance(data, bytes):
            raise TypeError("待发送数据必须为 bytes")
        if not data:
            raise TransmitError("发送内容不能为空")
        if len(data) > self.max_packet_bytes:
            raise TransmitError(f"单次发送不能超过 {self.max_packet_bytes} 字节")
        with self._lock:
            if not self._connected or self._stop.is_set():
                raise TransmitError("请先连接网络数据源，再手动发送")
            if self.config.kind == "udp" and self.config.peer is None:
                raise TransmitError("UDP 未设置明确发送目标，仅接收")
            if len(self._queue) >= self.max_queue_packets or self._queued_bytes + len(data) > self.max_queue_bytes:
                raise TransmitError("发送队列已满，请等待或停止定时发送")
            self._queue.append(data)
            self._queued_bytes += len(data)
            self._accepted_packets += 1
            self._accepted_bytes += len(data)
            return len(self._queue)

    def _clear_pending_locked(self) -> int:
        count = len(self._queue)
        self._discarded_packets += count
        self._discarded_bytes += self._queued_bytes
        self._queue.clear()
        self._queued_bytes = 0
        return count

    def clear_pending(self) -> int:
        with self._lock:
            return self._clear_pending_locked()

    def stop(self) -> None:
        self._batch_ack.acknowledge()
        with self._lock:
            self._stop.set()
            self._connected = False
            self._clear_pending_locked()
            handles = (self._socket, self._listener)
        # Closing cancels select/connect/accept on supported OSes. Every loop
        # also polls <=50 ms so stop does not depend on that OS behaviour.
        for handle in handles:
            if handle is not None:
                self._close_socket(handle)

    def ack_batch(self) -> None:
        """Acknowledge consumption without performing any socket operation."""
        self._batch_ack.acknowledge()

    @staticmethod
    def _close_socket(handle: socket.socket) -> None:
        try:
            handle.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            handle.close()
        except OSError:
            pass

    def _notify(self, kind: str, message: str) -> None:
        self.status.emit(kind, message)
        (self.error if kind == "error" else self.info).emit(message)

    def _check_stop(self) -> None:
        if self._stop.is_set():
            raise _Stopped()

    def _new_socket(self, kind: int, *, listener: bool = False) -> socket.socket:
        self._check_stop()
        handle = socket.socket(socket.AF_INET, kind)
        try:
            handle.setblocking(False)
            with self._lock:
                if listener:
                    self._listener = handle
                else:
                    self._socket = handle
            self._check_stop()
            return handle
        except BaseException:
            self._close_socket(handle)
            raise

    def _remember_bound(self, handle: socket.socket) -> None:
        bound = handle.getsockname()
        with self._lock:
            self._bound_address = bound[0], bound[1]

    def _set_connected(self, handle: socket.socket, remote: tuple[str, int] | None) -> None:
        self._check_stop()
        self._remember_bound(handle)
        with self._lock:
            if self._stop.is_set():
                raise _Stopped()
            self._connected = True
            self._remote_address = remote
        suffix = f"；对端 {remote[0]}:{remote[1]}" if remote else "；仅接收（未设置发送目标）"
        self._notify("connected", f"已连接 {self.config.label}{suffix}")

    def _connect_client(self) -> socket.socket:
        handle = self._new_socket(socket.SOCK_STREAM)
        result = handle.connect_ex((self.config.host, self.config.port))
        pending = {errno.EINPROGRESS, errno.EWOULDBLOCK, errno.EALREADY, errno.EINTR,
                   getattr(errno, "WSAEWOULDBLOCK", 10035), getattr(errno, "WSAEINPROGRESS", 10036)}
        if result not in pending | {0}:
            raise OSError(result, os.strerror(result))
        deadline = time.monotonic() + self.connect_timeout
        while result != 0:
            self._check_stop()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise NetworkError("TCP 连接超时；未自动重试")
            _, writable, failed = select.select([], [handle], [handle], min(0.05, remaining))
            if writable or failed:
                result = handle.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
                if result:
                    raise OSError(result, os.strerror(result))
                break
        self._set_connected(handle, (self.config.host, self.config.port))
        return handle

    def _accept_client(self) -> socket.socket:
        listener = self._new_socket(socket.SOCK_STREAM, listener=True)
        listener.bind((self.config.host, self.config.port))
        listener.listen(1)
        self._remember_bound(listener)
        address = self.bound_address
        self._notify("listening", f"正在监听 TCP {address[0]}:{address[1]}，等待一个连接")
        while True:
            self._check_stop()
            readable, _, _ = select.select([listener], [], [], self.read_timeout)
            if not readable:
                continue
            try:
                handle, remote = listener.accept()
            except BlockingIOError:
                continue
            with self._lock:
                # Track the accepted handle before any operation that could
                # fail, so both cancellation and finally always own its close.
                self._socket = handle
                self._listener = None
            self._close_socket(listener)
            handle.setblocking(False)
            self._set_connected(handle, (remote[0], remote[1]))
            return handle

    def _open(self) -> socket.socket:
        if self.config.kind == "tcp_client":
            return self._connect_client()
        if self.config.kind == "tcp_server":
            return self._accept_client()
        handle = self._new_socket(socket.SOCK_DGRAM)
        handle.bind((self.config.host, self.config.port))
        self._set_connected(handle, self.config.peer)
        return handle

    def _send_payload(self, handle: socket.socket, data: bytes) -> None:
        deadline = time.monotonic() + self.write_timeout
        sent = 0
        while sent < len(data):
            self._check_stop()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise NetworkError("网络发送超时；部分数据可能已交给系统，不重试")
            _, writable, failed = select.select([], [handle], [handle], min(0.05, remaining))
            if failed:
                raise NetworkError("网络发送连接错误；不重试")
            if not writable:
                continue
            try:
                if self.config.kind == "udp":
                    if self.config.peer is None:
                        raise NetworkError("UDP 未设置发送目标")
                    count = handle.sendto(data, self.config.peer)
                    if count != len(data):
                        raise NetworkError("UDP 数据报未完整发送；不重试")
                else:
                    count = handle.send(data[sent:])
                    if count <= 0:
                        raise NetworkError("TCP 发送时连接已断开")
            except BlockingIOError:
                continue
            sent += count

    def _send_one(self, handle: socket.socket) -> None:
        with self._lock:
            if self._stop.is_set() or not self._queue:
                return
            data = self._queue.popleft()
            self._queued_bytes -= len(data)
            self._in_flight_bytes = len(data)
        if self._stop.is_set():
            with self._lock:
                self._discarded_packets += 1
                self._discarded_bytes += len(data)
                self._in_flight_bytes = 0
            return
        try:
            self._send_payload(handle, data)
        except BaseException:
            with self._lock:
                self._failed_packets += 1
                self._unconfirmed_bytes += len(data)
                self._in_flight_bytes = 0
            raise
        with self._lock:
            self._completed_packets += 1
            self._completed_bytes += len(data)
            self._in_flight_bytes = 0
        self.transmitted.emit(data)

    def _receive(self, handle: socket.socket) -> bytes:
        self._check_stop()
        readable, _, failed = select.select([handle], [], [handle], self.read_timeout)
        if failed:
            raise NetworkError("网络连接发生错误")
        if not readable:
            return b""
        try:
            if self.config.kind == "udp":
                data, _sender = handle.recvfrom(65535)
                return data
            data = handle.recv(4096)
        except BlockingIOError:
            return b""
        if data == b"":
            raise NetworkError("TCP 对端已关闭连接；未自动重连")
        return data

    @Slot()
    def run(self) -> None:
        with self._lock:
            if self._started:
                raise RuntimeError("NetworkLinkWorker cannot be run twice")
            self._started = True
        pending: list[TelemetryEvent] = []
        raw_pending = bytearray()

        def emit_batch() -> None:
            nonlocal pending
            if not raw_pending and not pending:
                return
            self._batch_ack.before_emit(self._stop)
            if raw_pending:
                self.raw.emit(bytes(raw_pending))
                raw_pending.clear()
            if pending or self._batch_ack.enabled:
                self.events.emit(pending)
                pending = []

        try:
            self._check_stop()
            decoder = create_decoder(self.decoder_name)
            handle = self._open()
            self._check_stop()
            last_emit = time.monotonic()
            while not self._stop.is_set():
                self._send_one(handle)
                self._check_stop()
                chunk = self._receive(handle)
                if chunk:
                    overloaded = len(raw_pending) + len(chunk) > MAX_RX_BATCH_BYTES
                    if overloaded:
                        emit_batch()
                    raw_pending.extend(chunk)
                    received_at = datetime.now(timezone.utc)
                    if self.config.kind == "udp":
                        decoder.reset()
                    decoded = decoder.feed(chunk)
                    for event in decoded:
                        pending.append(replace(event, received_at=received_at))
                        if len(pending) >= MAX_RX_BATCH_EVENTS:
                            emit_batch()
                    if overloaded:
                        raise NetworkError("接收速率超过日志批次上限，已保留并解析当前已读数据后停止连接")
                now = time.monotonic()
                if now - last_emit >= RX_BATCH_SECONDS:
                    emit_batch()
                    last_emit = now
        except _Stopped:
            pass
        except (NetworkError, OSError, ValueError) as exc:
            if not self._stop.is_set():
                self._notify("error", f"网络通信失败：{exc}")
        except Exception as exc:
            if not self._stop.is_set():
                self._notify("error", f"网络通信异常：{type(exc).__name__}: {exc}")
        finally:
            with self._lock:
                self._connected = False
                self._stop.set()
                self._clear_pending_locked()
                handles = self._socket, self._listener
                self._socket = self._listener = None
            emit_batch()
            for handle in handles:
                if handle is not None:
                    self._close_socket(handle)
            self._notify("stopped", f"{self.config.label} 已断开；未发送的队列已清空")
            self.finished.emit()
