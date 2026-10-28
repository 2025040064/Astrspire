"""Bounded raw I/O history. Reads are chunks, not claims of protocol frames."""
from __future__ import annotations

import multiprocessing
import re
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone

from .transmit import encode_send_payload


@dataclass(frozen=True, slots=True)
class PacketRecord:
    sequence: int
    direction: str
    received_at: datetime
    monotonic_ns: int | None
    offset: int
    raw: bytes


class PacketStore:
    def __init__(self, *, max_records=10000, max_bytes=16 * 1024 * 1024):
        if any(type(value) is not int or value <= 0 for value in (max_records, max_bytes)):
            raise ValueError("报文缓存上限必须为正整数")
        self.max_records, self.max_bytes = max_records, max_bytes
        self._records = deque()
        self._bytes = self._sequence = 0
        self.evicted_records = self.evicted_bytes = 0
        self.total_received = self.total_transmitted = 0
        self.generation = 0

    @property
    def sequence(self):
        return self._sequence

    def append(self, direction, raw: bytes, *, at=None):
        if direction not in ("RX", "TX") or not isinstance(raw, bytes):
            raise ValueError("原始块方向或载荷无效")
        stamp = datetime.now(timezone.utc) if at is None else at
        if not isinstance(stamp, datetime) or stamp.tzinfo is None:
            raise ValueError("原始块时间需包含时区")
        if not raw:
            return None
        offset = self.total_received if direction == "RX" else self.total_transmitted
        if direction == "RX":
            self.total_received += len(raw)
        else:
            self.total_transmitted += len(raw)
        self._sequence += 1
        record = PacketRecord(self._sequence, direction, stamp, time.monotonic_ns(), offset, raw)
        self._records.append(record)
        self._bytes += len(raw)
        while len(self._records) > self.max_records or self._bytes > self.max_bytes:
            old = self._records.popleft()
            self._bytes -= len(old.raw)
            self.evicted_records += 1
            self.evicted_bytes += len(old.raw)
        return record

    def snapshot(self) -> tuple[PacketRecord, ...]:
        return tuple(self._records)

    def clear(self):
        self._records.clear()
        self._bytes = 0
        self.generation += 1

    def reset_session(self):
        self.clear()
        self.total_received = self.total_transmitted = 0
        self.evicted_records = self.evicted_bytes = 0

    def rx_window(self, after_sequence, max_bytes=4096):
        records = [record for record in self._records if record.sequence > after_sequence and record.direction == "RX"]
        result = bytearray()
        available = 0
        for record in records:
            available += len(record.raw)
            remaining = max_bytes - len(result)
            if remaining > 0:
                result.extend(record.raw[:remaining])
        missing = bool(self._records and self._records[0].sequence > after_sequence + 1)
        return bytes(result), missing or available > max_bytes


@dataclass(frozen=True, slots=True)
class PacketMatch:
    direction: str
    offset: int
    length: int
    sequences: tuple[int, ...]


def _regex_worker(pipe, data, pattern, limit):
    try:
        compiled = re.compile(pattern)
        result = []
        for match in compiled.finditer(data):
            result.append((match.start(), match.end()))
            if len(result) >= limit:
                break
        pipe.send((result, None))
    except Exception as exc:
        pipe.send((None, str(exc)[:512]))
    finally:
        pipe.close()


def _regex_ranges(data, pattern, limit, timeout, cancelled=None):
    # stdlib re has no timeout and can hold the GIL. Isolate it rather than
    # pretending a GUI background thread can interrupt a pathological regex.
    context = multiprocessing.get_context("spawn")
    receive, send = context.Pipe(duplex=False)
    process = context.Process(target=_regex_worker, args=(send, data, pattern, limit), daemon=True)
    try:
        process.start()
        send.close()
        deadline = time.monotonic() + timeout
        while not receive.poll(0.05):
            if cancelled is not None and cancelled.is_set():
                raise InterruptedError("搜索已取消")
            if time.monotonic() >= deadline:
                raise ValueError("正则搜索超时，已终止本次搜索")
        result, error = receive.recv()
        if error:
            raise ValueError(f"正则无效：{error}")
        return result
    finally:
        receive.close()
        send.close()
        if process.pid is not None:
            process.join(timeout=0.1)
            if process.is_alive():
                process.terminate()
                process.join(timeout=1)


def search_packets(records, query, *, mode="text", direction="ALL",
                   encoding="utf-8", limit=1000, regex_timeout=2.0, cancelled=None) -> list[PacketMatch]:
    """Find raw-byte matches across contiguous chunks, never across RX/TX."""
    if direction not in ("ALL", "RX", "TX") or mode not in ("text", "hex", "regex"):
        raise ValueError("搜索模式或方向无效")
    if not isinstance(query, str) or not 1 <= len(query) <= 1024:
        raise ValueError("搜索内容长度需为1–1024")
    if type(limit) is not int or not 1 <= limit <= 10000:
        raise ValueError("搜索数量上限无效")
    pattern = (encode_send_payload(query, mode="hex") if mode == "hex"
               else query.encode(encoding, errors="strict"))
    results = []

    def consume(group):
        if cancelled is not None and cancelled.is_set():
            raise InterruptedError("搜索已取消")
        if not group or len(results) >= limit:
            return
        data = b"".join(record.raw for record in group)
        if len(data) > 16 * 1024 * 1024:
            raise ValueError("单次搜索窗口超过16MiB")
        base = group[0].offset
        if mode == "regex":
            ranges = _regex_ranges(data, pattern, limit - len(results), regex_timeout, cancelled)
        else:
            ranges = []
            start = 0
            while len(ranges) + len(results) < limit:
                found = data.find(pattern, start)
                if found < 0:
                    break
                ranges.append((found, found + len(pattern)))
                start = found + max(1, len(pattern))
        for start, end in ranges:
            sequences = tuple(record.sequence for record in group
                              if record.offset < base + max(end, start + 1)
                              and record.offset + len(record.raw) > base + start)
            results.append(PacketMatch(group[0].direction, base + start, end - start, sequences))

    for selected in (("RX", "TX") if direction == "ALL" else (direction,)):
        group = []
        for record in records:
            if record.direction != selected:
                continue
            if group and record.offset != group[-1].offset + len(group[-1].raw):
                consume(group)
                group = []
            group.append(record)
        consume(group)
    return results


@dataclass(frozen=True)
class RecordingSearchResult:
    matches: tuple[PacketMatch, ...]
    records: tuple[PacketRecord, ...]
    scanned_records: int
    complete: bool


def search_recording(path, query, *, mode="text", direction="ALL", encoding="utf-8",
                     limit=1000, cancelled=None) -> RecordingSearchResult:
    """Stream explicit recordings; retain bounded results, never an entire file.

    Byte regexes use 1 MiB windows with 64 KiB overlap per direction. Expressions
    requiring a larger context are not a whole-file regex guarantee.
    """
    from .rotating_capture import iter_recording_records
    # Validate a query even when the recording is empty.
    search_packets((), query, mode=mode, direction=direction, encoding=encoding, limit=limit)
    window, results, selected, keys = [], [], {}, set()
    offsets = {"RX": 0, "TX": 0}
    size = sequence = selected_bytes = 0
    stopped = False
    overlap = 65536 if mode == "regex" else 4096

    def scan():
        nonlocal selected_bytes, stopped
        matches = search_packets(tuple(window), query, mode=mode, direction=direction,
                                 encoding=encoding, limit=limit, cancelled=cancelled)
        by_sequence = {record.sequence: record for record in window}
        for match in matches:
            key = (match.direction, match.offset, match.length)
            if key in keys:
                continue
            additions = [by_sequence[number] for number in match.sequences if number not in selected]
            extra = sum(len(record.raw) for record in additions)
            if len(results) >= limit or selected_bytes + extra > 16 * 1024 * 1024:
                stopped = True
                break
            results.append(match)
            keys.add(key)
            selected_bytes += extra
            selected.update((record.sequence, record) for record in additions)
        if len(matches) == limit or len(results) >= limit:
            stopped = True

    for record in iter_recording_records(path):
        if cancelled is not None and cancelled.is_set():
            raise InterruptedError("搜索已取消")
        sequence += 1
        window.append(PacketRecord(sequence, record.direction, record.timestamp, None,
                                   offsets[record.direction], record.payload))
        offsets[record.direction] += len(record.payload)
        size += len(record.payload)
        if size < 1024 * 1024 and len(window) < 4096:
            continue
        scan()
        if stopped:
            break
        carry = []
        for selected_direction in ("RX", "TX"):
            count = 0
            for item in reversed(window):
                if item.direction == selected_direction:
                    carry.append(item)
                    count += len(item.raw)
                    if count >= overlap:
                        break
        window = sorted(carry, key=lambda item: item.sequence)
        size = sum(len(item.raw) for item in window)
    if not stopped:
        scan()
    return RecordingSearchResult(tuple(results), tuple(selected[number] for number in sorted(selected)),
                                 sequence, not stopped)
