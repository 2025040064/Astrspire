"""Explicit raw-byte capture and RX-only playback of a self-owned JSONL format.

``qx-stream`` is not a VOFA+ native recording format.  Constructors do not open
files or data sources.  Replay never holds a transport and never retransmits TX
records; its ``transmitted`` signal exists only for the host worker interface.
"""

from __future__ import annotations

import json
import math
import os
import re
import stat
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO

from PySide6.QtCore import QObject, Signal, Slot

from .decoders import create_decoder
from .model import TelemetryEvent


STREAM_SCHEMA = "qx-stream"
STREAM_VERSION = 1
MAX_PACKET_BYTES = 64 * 1024
MAX_FILE_BYTES = 128 * 1024 * 1024
MAX_LINE_BYTES = MAX_PACKET_BYTES * 2 + 512
MAX_HEADER_BYTES = 512
MAX_BATCH_EVENTS = 256
MAX_BATCH_RAW_BYTES = 64 * 1024
DECODE_CHUNK_BYTES = 4096
BATCH_SECONDS = 0.05
_HEX = re.compile(r"[0-9a-fA-F]*\Z")
_RECORD_FIELDS = frozenset({"direction", "timestamp_utc", "hex"})


class StreamCaptureError(ValueError):
    """A capture operation or bounded recording is invalid."""


def _reject_constant(value: str) -> None:
    raise StreamCaptureError(f"JSON 不接受非常量数值 {value}")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise StreamCaptureError(f"JSON 含重复字段 {key}")
        result[key] = value
    return result


def _read_object(line: bytes, line_number: int) -> dict[str, Any]:
    try:
        value = json.loads(line.decode("utf-8"), object_pairs_hook=_unique_object,
                           parse_constant=_reject_constant)
    except (UnicodeError, json.JSONDecodeError, StreamCaptureError) as exc:
        raise StreamCaptureError(f"原始记录第 {line_number} 行：{exc}") from exc
    if not isinstance(value, dict):
        raise StreamCaptureError(f"原始记录第 {line_number} 行必须为 JSON 对象")
    return value


def _utc_stamp(value: datetime | None) -> datetime:
    stamp = datetime.now(timezone.utc) if value is None else value
    if not isinstance(stamp, datetime) or stamp.tzinfo is None or stamp.utcoffset() is None:
        raise StreamCaptureError("记录时间必须为包含时区的 datetime")
    try:
        return stamp.astimezone(timezone.utc)
    except (ValueError, OverflowError) as exc:
        raise StreamCaptureError("记录时间超出支持范围") from exc


class StreamRecorder:
    """Exclusive-create a bounded raw-byte recording after explicit ``open``.

    ``record`` returns the payload byte count, not a device acknowledgement or
    disk-sync guarantee.  ``flush`` and ``close`` are explicit host lifecycle
    operations.  Errors preserve the partial file for the user; no prior file
    is overwritten or removed.
    """

    def __init__(self, path: str | Path, *, connection_id: str | None = None,
                 generation: int = 0) -> None:
        if connection_id is not None and (not isinstance(connection_id, str)
                or not 1 <= len(connection_id) <= 128 or any(ord(char) < 32 for char in connection_id)):
            raise StreamCaptureError("来源ID需为1–128字符且不含控制字符")
        if type(generation) is not int or not 0 <= generation < 2**63:
            raise StreamCaptureError("来源代次需为非负整数")
        if connection_id is None and generation != 0:
            raise StreamCaptureError("来源代次需要明确来源ID")
        self.path = Path(path)
        self.connection_id, self.generation = connection_id, generation
        self.format_version = 1 if connection_id is None else 2
        self._stream: TextIO | None = None
        self._bytes_written = 0
        self._record_count = 0
        self._last_stamp: datetime | None = None
        self._lock = threading.RLock()

    @property
    def is_open(self) -> bool:
        with self._lock:
            return self._stream is not None

    @property
    def bytes_written(self) -> int:
        with self._lock:
            return self._bytes_written

    @property
    def record_count(self) -> int:
        with self._lock:
            return self._record_count

    def open(self) -> None:
        with self._lock:
            if self._stream is not None:
                raise RuntimeError("原始记录已开始")
            stream = self.path.open("x", encoding="utf-8", newline="\n")
            self._stream = stream
            self._bytes_written = 0
            self._record_count = 0
            self._last_stamp = None
            try:
                self._write({"schema": STREAM_SCHEMA, "version": self.format_version})
                stream.flush()
            except Exception:
                self._stream = None
                stream.close()
                raise

    def _write(self, value: dict[str, Any]) -> None:
        if self._stream is None:
            raise RuntimeError("原始记录尚未开始")
        line = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n"
        size = len(line.encode("utf-8"))
        if size > MAX_LINE_BYTES:
            raise StreamCaptureError("原始记录单行超出 128 KiB + 512 字节上限")
        if self._bytes_written + size > MAX_FILE_BYTES:
            raise StreamCaptureError("原始记录达到 128 MiB 上限，请停止并创建新文件")
        self._stream.write(line)
        self._bytes_written += size

    def record(self, direction: str, payload: bytes, at: datetime | None = None) -> int:
        if not isinstance(direction, str) or direction not in {"RX", "TX"}:
            raise StreamCaptureError("原始记录方向必须为 RX 或 TX")
        if not isinstance(payload, bytes) or len(payload) > MAX_PACKET_BYTES:
            raise StreamCaptureError("原始记录载荷必须为 bytes，单包不超过 64 KiB")
        stamp = _utc_stamp(at)
        with self._lock:
            if self._stream is None:
                raise RuntimeError("原始记录尚未开始")
            if self._last_stamp is not None and stamp < self._last_stamp:
                raise StreamCaptureError("原始记录时间倒退；未写入本包")
            document = {
                "direction": direction,
                "timestamp_utc": stamp.isoformat(timespec="microseconds"),
                "hex": payload.hex(),
            }
            if self.format_version == 2:
                document.update(connection_id=self.connection_id, generation=self.generation)
            self._write(document)
            self._last_stamp = stamp
            self._record_count += 1
        return len(payload)

    def flush(self) -> None:
        with self._lock:
            if self._stream is not None:
                self._stream.flush()

    def close(self) -> None:
        with self._lock:
            stream, self._stream = self._stream, None
            if stream is not None:
                stream.close()

    def __enter__(self) -> StreamRecorder:
        self.open()
        return self

    def __exit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        self.close()


@dataclass(frozen=True, slots=True)
class StreamRecord:
    direction: str
    payload: bytes
    timestamp: datetime
    bytes_read: int
    file_size: int
    connection_id: str | None = None
    generation: int = 0
    byte_offset: int = 0
    part_index: int = 0


def iter_stream_records(path: str | Path) -> Iterator[StreamRecord]:
    """Read one record at a time and close on exhaustion or generator close.

    Both RX and TX records are yielded for chronology and diagnostics.  Callers
    must explicitly select RX for replay; this iterator never transmits data.
    Each physical line is bounded before UTF-8 decoding or JSON parsing.
    """
    source = Path(path)
    before = source.stat()
    if not stat.S_ISREG(before.st_mode) or not 1 <= before.st_size <= MAX_FILE_BYTES:
        raise StreamCaptureError("原始记录必须是非空普通文件且不超过 128 MiB")
    with source.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        if not stat.S_ISREG(opened.st_mode) or not 1 <= opened.st_size <= MAX_FILE_BYTES:
            raise StreamCaptureError("打开后的原始记录文件类型或大小无效")
        file_size = opened.st_size
        header = stream.readline(MAX_HEADER_BYTES + 1)
        if not header or len(header) > MAX_HEADER_BYTES:
            raise StreamCaptureError("原始记录格式头为空或超过 512 字节")
        value = _read_object(header, 1)
        if (set(value) != {"schema", "version"} or value["schema"] != STREAM_SCHEMA
                or type(value["version"]) is not int or value["version"] not in (1, 2)):
            raise StreamCaptureError("不是受支持的 qx-stream / version 1或2 原始记录")
        version = value["version"]
        position = len(header)
        line_number = 1
        last_stamp: datetime | None = None
        has_records = False
        while True:
            start_offset = position
            line = stream.readline(MAX_LINE_BYTES + 1)
            if not line:
                break
            line_number += 1
            position += len(line)
            if len(line) > MAX_LINE_BYTES:
                raise StreamCaptureError(f"原始记录第 {line_number} 行超过长度上限")
            if position > MAX_FILE_BYTES:
                raise StreamCaptureError("原始记录在回放时增长超过 128 MiB")
            value = _read_object(line, line_number)
            expected = _RECORD_FIELDS if version == 1 else _RECORD_FIELDS | {"connection_id", "generation"}
            if set(value) != expected:
                raise StreamCaptureError(f"原始记录第 {line_number} 行字段缺失或包含未知字段")
            connection_id, generation = value.get("connection_id"), value.get("generation", 0)
            if version == 2 and (not isinstance(connection_id, str) or not 1 <= len(connection_id) <= 128
                    or any(ord(char) < 32 for char in connection_id)
                    or type(generation) is not int or not 0 <= generation < 2**63):
                raise StreamCaptureError(f"原始记录第 {line_number} 行来源ID/代次无效")
            direction, text, encoded = value["direction"], value["timestamp_utc"], value["hex"]
            if not isinstance(direction, str) or direction not in {"RX", "TX"}:
                raise StreamCaptureError(f"原始记录第 {line_number} 行方向不是 RX/TX")
            if not isinstance(text, str) or len(text) > 64:
                raise StreamCaptureError(f"原始记录第 {line_number} 行时间格式无效")
            try:
                stamp = _utc_stamp(datetime.fromisoformat(text))
            except (ValueError, OverflowError) as exc:
                raise StreamCaptureError(f"原始记录第 {line_number} 行时间无效：{exc}") from exc
            if last_stamp is not None and stamp < last_stamp:
                raise StreamCaptureError(f"原始记录第 {line_number} 行时间倒退")
            if (not isinstance(encoded, str) or len(encoded) > MAX_PACKET_BYTES * 2
                    or len(encoded) % 2 or _HEX.fullmatch(encoded) is None):
                raise StreamCaptureError(f"原始记录第 {line_number} 行 HEX 无效或超过 64 KiB")
            payload = bytes.fromhex(encoded)
            last_stamp, has_records = stamp, True
            yield StreamRecord(direction, payload, stamp, position, file_size,
                               connection_id, generation, start_offset)
        if not has_records:
            raise StreamCaptureError("原始记录只有格式头，没有 RX/TX 数据")


class RawReplayWorker(QObject):
    """Replay captured RX with the selected decoder, never captured TX.

    Original timestamps determine ordering and delays.  Dense batches are
    throttled to a maximum of 256 events / 64 KiB per 50 ms; this can slow a
    recording which exceeds display throughput rather than dropping records or
    replaying them as writes to a device.  Pauses suspend the playback clock.
    """

    events = Signal(object)
    raw = Signal(bytes)
    status = Signal(str, str)
    progress = Signal(int)
    finished = Signal()
    transmitted = Signal(bytes)  # Compatibility only: never emitted.
    generation_changed = Signal(int)
    generation_events = Signal(int, object)
    generation_raw = Signal(int, bytes)
    timeline_changed = Signal(float, float, int)

    def __init__(self, path: str | Path, decoder_name: str, speed: float = 1.0, loop: bool = False,
                 *, await_ack: bool = False) -> None:
        super().__init__()
        if not isinstance(decoder_name, str) or not 1 <= len(decoder_name) <= 256:
            raise ValueError("原始回放解码器名称无效")
        if (isinstance(speed, bool) or not isinstance(speed, (int, float))
                or not math.isfinite(speed) or not 0.1 <= speed <= 20):
            raise ValueError("原始回放倍率必须在 0.1–20 之间")
        if type(loop) is not bool:
            raise ValueError("原始回放循环设置必须为布尔值")
        if type(await_ack) is not bool:
            raise ValueError("批次应答设置必须为布尔值")
        self.path, self.decoder_name = Path(path), decoder_name
        self.speed, self.loop = float(speed), loop
        self._condition = threading.Condition()
        self._stop = False
        self._started = False
        self._paused = False
        self._pause_at: float | None = None
        self._paused_seconds = 0.0
        self._last_batch_at = 0.0
        self._last_progress = -1
        self._events_pending: list[TelemetryEvent] = []
        self._raw_pending = bytearray()
        self._ignored_tx_packets = 0
        self._await_ack, self._batch_pending = await_ack, False
        self._generation = 0
        self._seek_request: float | None = None
        self._position_seconds = 0.0
        self._duration_seconds = 0.0
        self._rate_changed = False

    @property
    def generation(self) -> int:
        with self._condition:
            return self._generation

    @property
    def position_seconds(self) -> float:
        with self._condition:
            return self._position_seconds

    @property
    def duration_seconds(self) -> float:
        with self._condition:
            return self._duration_seconds

    def seek_seconds(self, seconds: float) -> int:
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or seconds < 0:
            raise StreamCaptureError("回放定位需为非负有限秒数")
        with self._condition:
            if self._stop:
                raise StreamCaptureError("回放已结束，请重新打开记录")
            self._generation += 1
            self._seek_request = float(seconds)
            self._batch_pending = False
            self._condition.notify_all()
            generation = self._generation
        self.generation_changed.emit(generation)
        return generation

    def set_speed(self, speed: float) -> None:
        if isinstance(speed, bool) or not isinstance(speed, (int, float)) or not math.isfinite(speed) or not 0.1 <= speed <= 20:
            raise StreamCaptureError("原始回放倍率必须在0.1–20之间")
        with self._condition:
            self.speed = float(speed)
            self._rate_changed = True
            self._condition.notify_all()

    def set_loop(self, loop: bool) -> None:
        if type(loop) is not bool:
            raise StreamCaptureError("回放循环设置必须为布尔值")
        with self._condition:
            self.loop = loop
            self._condition.notify_all()

    @property
    def can_send(self) -> bool:
        return False

    @property
    def connected(self) -> bool:
        return False

    @property
    def channel_names(self) -> tuple[str, ...]:
        return ()

    @property
    def paused(self) -> bool:
        with self._condition:
            return self._paused

    @property
    def ignored_tx_packets(self) -> int:
        return self._ignored_tx_packets

    def enqueue_send(self, _payload: bytes) -> int:
        raise StreamCaptureError("原始记录回放没有设备连接，不能发送或重放 TX")

    def clear_pending(self) -> int:
        return 0

    def stop(self) -> None:
        with self._condition:
            self._stop = True
            self._condition.notify_all()

    def ack_batch(self) -> None:
        with self._condition:
            self._batch_pending = False
            self._condition.notify_all()

    def set_paused(self, paused: bool) -> None:
        if type(paused) is not bool:
            raise ValueError("暂停设置必须为布尔值")
        with self._condition:
            if self._stop or paused == self._paused:
                return
            now = time.monotonic()
            if paused:
                self._pause_at = now
            elif self._pause_at is not None:
                self._paused_seconds += now - self._pause_at
                self._pause_at = None
            self._paused = paused
            notify = self._started
            self._condition.notify_all()
        if notify:
            self.status.emit("paused" if paused else "replay", "原始记录回放已暂停" if paused else "原始记录回放继续")

    def _clock(self) -> float:
        now = time.monotonic()
        return now - self._paused_seconds - (now - self._pause_at if self._pause_at is not None else 0.0)

    def _wait_due(self, deadline: float, speed: float | None = None) -> bool:
        with self._condition:
            rate = self.speed if speed is None else speed
            while not self._stop and self._seek_request is None:
                if rate != self.speed:
                    deadline = self._clock() + max(0.0, deadline - self._clock()) * rate / self.speed
                    rate = self.speed
                remaining = deadline - self._clock()
                if not self._paused and remaining <= 0:
                    return True
                self._condition.wait(0.05 if self._paused else min(0.05, max(0.0001, remaining)))
        return False

    def _emit_pending(self) -> bool:
        if not self._events_pending and not self._raw_pending:
            return True
        with self._condition:
            while not self._stop and self._seek_request is None:
                remaining = self._last_batch_at + BATCH_SECONDS - time.monotonic()
                if not self._paused and remaining <= 0 and not self._batch_pending:
                    break
                self._condition.wait(0.05 if self._paused or self._batch_pending else min(0.05, max(0.0001, remaining)))
            if self._stop or self._seek_request is not None:
                return False
            self._last_batch_at = time.monotonic()
            self._batch_pending = self._await_ack
            events, self._events_pending = self._events_pending, []
            raw, self._raw_pending = bytes(self._raw_pending), bytearray()
            generation = self._generation
        if raw:
            self.generation_raw.emit(generation, raw)
            self.raw.emit(raw)
        if events or self._await_ack:
            self.generation_events.emit(generation, events)
            self.events.emit(events)
        return True

    def _receive(self, payload: bytes, decoder, source_timestamp: datetime | None = None) -> bool:
        for offset in range(0, len(payload), DECODE_CHUNK_BYTES):
            piece = payload[offset:offset + DECODE_CHUNK_BYTES]
            if len(self._raw_pending) + len(piece) > MAX_BATCH_RAW_BYTES and not self._emit_pending():
                return False
            self._raw_pending.extend(piece)
            received_at = datetime.now(timezone.utc)
            for event in decoder.feed(piece):
                self._events_pending.append(replace(event, received_at=received_at,
                                                    source_timestamp=source_timestamp))
                if len(self._events_pending) >= MAX_BATCH_EVENTS and not self._emit_pending():
                    return False
            with self._condition:
                if self._stop or self._seek_request is not None:
                    return False
            if time.monotonic() - self._last_batch_at >= BATCH_SECONDS and not self._emit_pending():
                return False
        return True

    def _update_progress(self, record: StreamRecord) -> None:
        position = min(99, int(record.bytes_read * 100 / max(1, record.file_size)))
        if position != self._last_progress:
            self._last_progress = position
            self.progress.emit(position)

    @Slot()
    def run(self) -> None:
        with self._condition:
            if self._started:
                self.status.emit("error", "原始记录回放实例只能运行一次")
                self.finished.emit()
                return
            self._started = True
            self._paused_seconds = 0.0
            self._pause_at = time.monotonic() if self._paused else None
        iterator = None
        terminal_kind, terminal_message = "stopped", "原始记录回放已停止"
        try:
            from .replay_index import RecordingIndex
            from .rotating_capture import iter_recording_records
            # Build/validate in this worker, never on the GUI thread. Warm-up is
            # deliberately from the start so a seek preserves partial frames
            # and stateful decoders; no pre-target samples are emitted.
            index = RecordingIndex.build(self.path, cancel=_ReplayCancellation(self))
            with self._condition:
                self._duration_seconds = index.duration_seconds
            decoder = create_decoder(self.decoder_name)
            self.status.emit("replay", f"原始 RX 回放：{self.path.name} · {self.speed:g}×；TX 记录只忽略，不发送")
            rounds = 0
            offset = 0.0
            while True:
                with self._condition:
                    if self._stop:
                        break
                    if self._seek_request is not None:
                        offset = min(self._seek_request, self._duration_seconds)
                        self._seek_request = None
                    self._position_seconds = offset
                    self._events_pending = []
                    self._raw_pending.clear()
                    self._batch_pending = False
                    origin = self._clock()
                    generation = self._generation
                    self._rate_changed = False
                    speed = self.speed
                self.timeline_changed.emit(offset, self._duration_seconds, generation)
                decoder.reset()
                self._last_progress = -1
                self.progress.emit(0)
                iterator = iter_recording_records(self.path)
                first_stamp: datetime | None = None
                rx_count = tx_count = 0
                cancelled = False
                for record in iterator:
                    with self._condition:
                        if self._stop or self._seek_request is not None:
                            cancelled = True
                            break
                    if first_stamp is None:
                        first_stamp = record.timestamp
                    elapsed = (record.timestamp - first_stamp).total_seconds()
                    if not math.isfinite(elapsed) or elapsed < 0:
                        raise StreamCaptureError("原始记录时间间隔无效")
                    if elapsed < offset:
                        if record.direction == "RX":
                            for start in range(0, len(record.payload), DECODE_CHUNK_BYTES):
                                decoder.feed(record.payload[start:start + DECODE_CHUNK_BYTES])
                        continue
                    with self._condition:
                        if self._rate_changed:
                            # Change the clock scale without replaying a record
                            # or changing the target's original timestamp.
                            offset = self._position_seconds
                            origin = self._clock()
                            speed = self.speed
                            self._rate_changed = False
                    due = origin + (elapsed - offset) / speed
                    with self._condition:
                        ahead = due > self._clock()
                    if ahead and not self._emit_pending():
                        cancelled = True
                        break
                    if not self._wait_due(due, speed):
                        cancelled = True
                        break
                    if record.direction == "TX":
                        tx_count += 1
                        self._ignored_tx_packets += 1
                    else:
                        rx_count += 1
                        if not self._receive(record.payload, decoder, record.timestamp):
                            cancelled = True
                            break
                    with self._condition:
                        self._position_seconds = elapsed
                    self.timeline_changed.emit(elapsed, self._duration_seconds, self.generation)
                    self._update_progress(record)
                if cancelled or not self._emit_pending():
                    iterator.close()
                    iterator = None
                    with self._condition:
                        if self._seek_request is not None and not self._stop:
                            continue
                    break
                iterator.close()
                iterator = None
                self.progress.emit(100)
                self.status.emit("notice", f"本轮原始回放包含 {rx_count} 包 RX；已忽略 {tx_count} 包 TX，未向设备发送")
                if not self.loop:
                    terminal_kind, terminal_message = "completed", "原始 RX 记录回放完成；未连接设备或重放 TX"
                    break
                rounds += 1
                offset = 0.0
                with self._condition:
                    self._generation += 1
                self.generation_changed.emit(self.generation)
                self.status.emit("replay", f"原始 RX 循环回放 · 第 {rounds + 1} 轮")
                if not self._wait_due(self._clock() + BATCH_SECONDS):
                    with self._condition:
                        if self._seek_request is not None and not self._stop:
                            continue
                    break
        except Exception as exc:
            with self._condition:
                if not self._stop:
                    terminal_kind, terminal_message = "error", f"原始记录回放失败：{exc}"
        finally:
            if iterator is not None:
                try:
                    iterator.close()
                except Exception as exc:
                    terminal_kind, terminal_message = "error", f"原始记录文件关闭失败：{exc}"
            self._events_pending = []
            self._raw_pending.clear()
            with self._condition:
                self._stop = True
                self._condition.notify_all()
            self.status.emit(terminal_kind, terminal_message)
            self.finished.emit()


class _ReplayCancellation:
    def __init__(self, worker):
        self.worker = worker

    def is_set(self):
        with self.worker._condition:
            return self.worker._stop
