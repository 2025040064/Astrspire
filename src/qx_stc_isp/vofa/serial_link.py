"""One serial-owner worker for bounded RX and explicitly queued manual TX.

Imports and construction never open a port. Only run() does so, and all serial
writes happen in that same loop. Connecting does not enqueue or send bytes.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from functools import partial

from PySide6.QtCore import QObject, Signal, Slot

from ..port_lease import PORT_LEASES, PortLeaseError, PortLeaseManager
from ..transport import PySerialTransport, SerialTransport, TransportCancelled, TransportError
from .decoders import create_decoder
from .model import TelemetryEvent
from .source import BatchAcknowledger
from .transmit import MAX_SEND_BYTES, TransmitError

MAX_RX_BATCH_BYTES = 64 * 1024
MAX_RX_BATCH_EVENTS = 256
RX_BATCH_SECONDS = 0.05
RX_DIAGNOSIS_SECONDS = 3.0


@dataclass(frozen=True, slots=True)
class RxStatistics:
    received_bytes: int
    numeric_frames: int
    decode_errors: int


@dataclass(frozen=True, slots=True)
class TxStatistics:
    accepted_packets: int
    accepted_bytes: int
    completed_packets: int
    completed_bytes: int
    failed_packets: int
    unconfirmed_bytes: int
    discarded_packets: int
    discarded_bytes: int
    queued_packets: int
    queued_bytes: int
    in_flight_packets: int
    in_flight_bytes: int


class SerialLinkWorker(QObject):
    """Connect once, read continuously, and write only queued explicit requests.

    The GUI may call enqueue_send(), clear_pending() and stop() from its own
    thread. All queue state is locked. stop() cancels active I/O and discards
    queued requests. A write already in flight may have partially reached the
    driver; an exception counts it as unconfirmed, never as a completed TX.

    transmitted reports a fully completed transport.write(), not an independent
    electrical measurement or a target-device ACK. No failed write is retried.
    """

    events = Signal(object)
    raw = Signal(bytes)
    status = Signal(str, str)
    info = Signal(str)
    error = Signal(str)
    finished = Signal()
    transmitted = Signal(bytes)

    def __init__(
        self,
        device: str,
        baudrate: int,
        decoder_name: str,
        *,
        transport_factory: Callable[[str], SerialTransport] | None = None,
        lease_manager: PortLeaseManager | None = None,
        max_queue_packets: int = 64,
        max_queue_bytes: int = 64 * 1024,
        max_packet_bytes: int = MAX_SEND_BYTES,
        read_timeout: float = 0.02,
        bytesize: int = 8,
        parity: str = "N",
        stopbits: float = 1,
        dtr: bool = False,
        rts: bool = False,
        flow_control: str = "none",
        await_ack: bool = False,
    ) -> None:
        super().__init__()
        if not isinstance(device, str) or not device.strip():
            raise ValueError("串口名称不能为空")
        if isinstance(baudrate, bool) or not isinstance(baudrate, int) or baudrate <= 0:
            raise ValueError("波特率必须为正整数")
        for limit in (max_queue_packets, max_queue_bytes, max_packet_bytes):
            if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
                raise ValueError("发送队列上限必须为正整数")
        if max_packet_bytes > max_queue_bytes:
            raise ValueError("单包上限不能超过队列字节上限")
        if not 0 < read_timeout <= 0.05:
            raise ValueError("读取超时必须在 0 到 0.05 秒之间")
        # Decoder is validated before a device can be opened. A separate
        # instance is created in run(), using this startup snapshot.
        create_decoder(decoder_name)
        if type(bytesize) is not int or bytesize not in (5, 6, 7, 8):
            raise ValueError("数据位必须为5–8")
        if parity not in ("N", "E", "O", "M", "S") or type(stopbits) not in (int, float) or stopbits not in (1, 1.5, 2):
            raise ValueError("串口校验位或停止位无效")
        if type(dtr) is not bool or type(rts) is not bool:
            raise ValueError("DTR/RTS必须为布尔值")
        self.device = device.strip()
        self.baudrate = baudrate
        self.decoder_name = decoder_name
        self.bytesize, self.parity, self.stopbits = bytesize, parity, stopbits
        self.dtr, self.rts = dtr, rts
        if flow_control not in ("none", "rtscts", "xonxoff"):
            raise ValueError("串口流控无效")
        self.flow_control = flow_control
        self._transport_factory = transport_factory or partial(
            PySerialTransport, dtr=dtr, rts=rts,
            **({"flow_control": flow_control} if flow_control != "none" else {}))
        self._lease_manager = lease_manager or PORT_LEASES
        self.max_queue_packets = max_queue_packets
        self.max_queue_bytes = max_queue_bytes
        self.max_packet_bytes = max_packet_bytes
        self.read_timeout = read_timeout
        self._stop = threading.Event()
        self._batch_ack = BatchAcknowledger(await_ack)
        self._lock = threading.Lock()
        self._transport: SerialTransport | None = None
        self._queue: deque[bytes] = deque()
        self._queued_bytes = 0
        self._connected = False
        self._started = False
        self._accepted_packets = self._accepted_bytes = 0
        self._completed_packets = self._completed_bytes = 0
        self._failed_packets = self._unconfirmed_bytes = 0
        self._discarded_packets = self._discarded_bytes = 0
        self._in_flight_bytes = 0
        self._received_bytes = self._numeric_frames = self._decode_errors = 0

    @property
    def receive_statistics(self) -> RxStatistics:
        with self._lock:
            return RxStatistics(self._received_bytes, self._numeric_frames, self._decode_errors)

    @property
    def connected(self) -> bool:
        with self._lock:
            return self._connected and not self._stop.is_set()

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
        """Queue one explicit request, returning the pending packet count.

        A caller cannot prequeue traffic before connect, or queue after stop.
        Queue rejection raises TransmitError and never drops an older request.
        """

        if not isinstance(data, bytes):
            raise TypeError("待发送数据必须为 bytes")
        if not data:
            raise TransmitError("发送内容不能为空")
        if len(data) > self.max_packet_bytes:
            raise TransmitError(f"单次发送不能超过 {self.max_packet_bytes} 字节")
        with self._lock:
            if not self._connected or self._stop.is_set():
                raise TransmitError("请先连接串口，再手动发送")
            if (len(self._queue) >= self.max_queue_packets
                    or self._queued_bytes + len(data) > self.max_queue_bytes):
                raise TransmitError("发送队列已满，请等待发送完成或停止定时发送")
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
        """Discard only not-yet-started requests; an active write is unchanged."""

        with self._lock:
            return self._clear_pending_locked()

    def stop(self) -> None:
        with self._lock:
            self._stop.set()
            self._connected = False
            self._clear_pending_locked()
            transport = self._transport
        self._batch_ack.acknowledge()
        if transport is not None:
            try:
                transport.cancel()
            except Exception as exc:
                # Cancellation is best effort. The bounded read/write timeout
                # still lets run() close the handle and release its lease.
                self._notify("notice", f"取消串口操作未立即完成：{exc}")

    def ack_batch(self) -> None:
        """GUI calls this after consuming events, including an empty RX batch."""
        self._batch_ack.acknowledge()

    def _notify(self, kind: str, message: str) -> None:
        self.status.emit(kind, message)
        if kind == "error":
            self.error.emit(message)
        else:
            self.info.emit(message)

    def _send_one(self, transport: SerialTransport) -> None:
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
            # This is the only call site that writes serial bytes. It executes
            # inside run(), never the UI-thread enqueue or timer callback.
            transport.write(data, timeout=2.0)
        except Exception:
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

    @Slot()
    def run(self) -> None:
        with self._lock:
            if self._started:
                raise RuntimeError("SerialLinkWorker cannot be run twice")
            self._started = True
        lease = None
        transport = None
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
            decoder = create_decoder(self.decoder_name)
            lease = self._lease_manager.acquire(self.device, owner="vofa")
            if self._stop.is_set():
                return
            transport = self._transport_factory(self.device)
            with self._lock:
                self._transport = transport
            if self._stop.is_set():
                return
            if (self.bytesize, self.parity, self.stopbits) == (8, "N", 1):
                transport.open(self.baudrate)
            else:
                transport.open(self.baudrate, bytesize=self.bytesize, parity=self.parity, stopbits=self.stopbits)
            with self._lock:
                if self._stop.is_set():
                    return
                self._connected = True
            serial_format = f"{self.bytesize}{self.parity}{self.stopbits:g}"
            self._notify("connected", f"{self.device} 端口已打开 · {self.baudrate}/{serial_format}\n"
                         f"请求 DTR={int(self.dtr)} RTS={int(self.rts)}；等待接收数据。")
            last_emit = time.monotonic()
            last_received = last_valid = last_emit
            warning = None
            receiving_reported = False
            numeric_mode = self.decoder_name.casefold().replace("-", "").replace("_", "").strip() in {"justfloat", "firewater"}
            while not self._stop.is_set():
                self._send_one(transport)
                if self._stop.is_set():
                    break
                chunk = transport.read_some(4096, timeout=self.read_timeout)
                valid_frames = 0
                if chunk:
                    overloaded = len(raw_pending) + len(chunk) > MAX_RX_BATCH_BYTES
                    if overloaded:
                        emit_batch()
                    raw_pending.extend(chunk)
                    received_at = datetime.now(timezone.utc)
                    decoded = decoder.feed(chunk)
                    valid_frames = sum(event.samples is not None for event in decoded)
                    with self._lock:
                        self._received_bytes += len(chunk)
                        self._numeric_frames += valid_frames
                        self._decode_errors += sum(bool(event.error) for event in decoded)
                    for event in decoded:
                        pending.append(replace(event, received_at=received_at))
                        if len(pending) >= MAX_RX_BATCH_EVENTS:
                            emit_batch()
                    if overloaded:
                        raise TransportError("接收速率超过日志批次上限，已保留并解析当前已读数据后停止连接")
                now = time.monotonic()
                if chunk:
                    last_received = now
                if valid_frames:
                    last_valid = now
                meaningful_data = valid_frames > 0 if numeric_mode else bool(chunk)
                if meaningful_data and (not receiving_reported or warning is not None):
                    detail = "有效数值帧" if numeric_mode else "原始字节（未验证设备身份）"
                    self._notify("receiving", f"{self.device} 已收到{detail}。")
                    receiving_reported = True
                    warning = None
                expected_at = last_valid if numeric_mode else last_received
                if not self._stop.is_set() and now - expected_at >= RX_DIAGNOSIS_SECONDS:
                    kind = "unparsed_data" if numeric_mode and now - last_received < RX_DIAGNOSIS_SECONDS else "waiting_data"
                    if kind != warning:
                        if kind == "unparsed_data":
                            message = f"{self.device} 收到字节，但近3秒没有有效 {self.decoder_name} 数值帧；请检查数据格式和波特率 {self.baudrate}。"
                        else:
                            message = (f"{self.device}（{self.baudrate}）近3秒没有收到数据；端口仍保持连接。"
                                       "空闲或等待命令时可能正常；请确认固件UART已启用及TX/RX/GND；不会自动发送。")
                        self._notify(kind, message)
                        warning = kind
                if now - last_emit >= RX_BATCH_SECONDS:
                    emit_batch()
                    last_emit = now
        except TransportCancelled:
            if not self._stop.is_set():
                self._notify("error", f"{self.device} 的串口操作意外取消。")
        except (PortLeaseError, TransportError, ValueError) as exc:
            self._notify("error", str(exc))
        except Exception as exc:
            self._notify("error", f"串口通信失败：{exc}")
        finally:
            with self._lock:
                self._connected = False
                self._stop.set()
                self._clear_pending_locked()
            emit_batch()
            if transport is not None:
                try:
                    transport.close()
                except Exception as exc:
                    self._notify("error", f"关闭 {self.device} 失败：{exc}")
            with self._lock:
                self._transport = None
            if lease is not None:
                lease.release()
            self._notify("stopped", f"{self.device} 已断开；未发送的队列已清空。")
            self.finished.emit()
