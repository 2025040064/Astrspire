"""Rebuildable, bounded time/byte indexes; raw recordings are never modified.

Indexes are derived data, not a trust source. Loading re-hashes every original
part before exposing metadata. No files are read merely by restoring a catalog.
"""
from __future__ import annotations

import bisect
import hashlib
import json
import math
import os
import stat
import tempfile
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from .json_data import load_json_document

MAX_INDEX_BYTES = 16 * 1024 * 1024
MAX_ENTRIES = 65536
INDEX_STRIDE = 256


class IndexStaleError(ValueError):
    """The original source changed; discard and rebuild this cache."""


class IndexCancelled(ValueError):
    """An explicit stop interrupted index validation or construction."""


def _check_cancel(cancel):
    if cancel is not None and cancel.is_set():
        raise IndexCancelled("索引操作已取消；原始记录未修改")


def _fingerprint(path, cancel=None):
    path = Path(path)
    before = path.stat()
    if not stat.S_ISREG(before.st_mode):
        raise ValueError("索引来源必须为普通文件")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            _check_cancel(cancel)
            digest.update(chunk)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise IndexStaleError("记录在索引期间变化，请停止录制后重试")
    return {"name": path.name, "size": after.st_size, "sha256": digest.hexdigest()}


def _source_files(source):
    source = Path(source)
    files = [source]
    if source.name.casefold().endswith(".session.json"):
        with source.open("rb") as stream:
            doc = load_json_document(stream.read(4 * 1024 * 1024 + 1), max_bytes=4 * 1024 * 1024)
        if (not isinstance(doc, dict) or doc.get("schema") != "qx-stream-session"
                or doc.get("version") not in (1, 2) or doc.get("complete") is not True
                or doc.get("error") is not None or not isinstance(doc.get("parts"), list)
                or not 1 <= len(doc["parts"]) <= 4096):
            raise ValueError("会话未完整结束或分卷清单无效")
        seen = set()
        for part in doc["parts"]:
            name = part.get("path") if isinstance(part, dict) else None
            if (not isinstance(name, str) or not name or Path(name).name != name
                    or "/" in name or "\\" in name or name in seen):
                raise ValueError("分卷路径必须是唯一同目录文件名")
            child = source.parent / name
            if child.resolve().parent != source.parent.resolve() or child.resolve() == source.resolve():
                raise ValueError("分卷路径越界或引用自身")
            seen.add(name)
            files.append(child)
    return files


def _timestamp(text):
    if not isinstance(text, str) or len(text) > 64:
        raise ValueError("索引时间无效")
    value = datetime.fromisoformat(text)
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("索引时间必须有时区")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True, slots=True)
class IndexEntry:
    sequence: int
    timestamp: str
    byte_offset: int
    part_index: int


@dataclass(frozen=True, slots=True)
class RecordingIndex:
    source: str
    kind: str
    fingerprints: tuple[dict, ...]
    record_count: int
    rx_count: int
    tx_count: int
    first_timestamp: str
    last_timestamp: str
    connection_ids: tuple[str, ...]
    entries: tuple[IndexEntry, ...]
    stride: int = INDEX_STRIDE

    @property
    def duration_seconds(self):
        return (_timestamp(self.last_timestamp) - _timestamp(self.first_timestamp)).total_seconds()

    def checkpoint(self, seconds):
        seconds = self._offset(seconds)
        target = _timestamp(self.first_timestamp).timestamp() + seconds
        position = bisect.bisect_right([_timestamp(item.timestamp).timestamp() for item in self.entries], target) - 1
        return self.entries[max(0, position)]

    def _offset(self, seconds):
        if isinstance(seconds, bool) or not isinstance(seconds, (float, int)) or not math.isfinite(seconds):
            raise ValueError("时间定位需为有限秒数")
        return min(max(0.0, float(seconds)), self.duration_seconds)

    @classmethod
    def build(cls, source, *, cancel: threading.Event | None = None):
        source = Path(source).resolve()
        _check_cancel(cancel)
        files = _source_files(source)
        before = tuple(_fingerprint(path, cancel) for path in files)
        kind = "csv" if source.suffix.casefold() == ".csv" else "raw"
        if kind == "csv":
            from .playback import iter_csv_frames
            iterator = iter_csv_frames(source)
        else:
            from .rotating_capture import iter_recording_records
            iterator = iter_recording_records(source)
        entries, sources = [], set()
        count = rx = tx = 0
        first = last = None
        stride = INDEX_STRIDE
        try:
            for record in iterator:
                _check_cancel(cancel)
                stamp = record.timestamp.isoformat(timespec="microseconds")
                first, last = stamp if first is None else first, stamp
                if count % stride == 0:
                    entries.append(IndexEntry(count, stamp, getattr(record, "byte_offset", record.bytes_read),
                                              getattr(record, "part_index", 0)))
                    if len(entries) >= MAX_ENTRIES:
                        entries = entries[::2]
                        stride *= 2
                connection_id = getattr(record, "connection_id", None)
                if connection_id:
                    sources.add(connection_id)
                    if len(sources) > 4096:
                        raise ValueError("会话来源超过4096个")
                direction = getattr(record, "direction", "RX")
                rx += direction == "RX"
                tx += direction == "TX"
                count += 1
        finally:
            iterator.close()
        if count == 0:
            raise ValueError("记录没有可索引的数据")
        after = tuple(_fingerprint(path, cancel) for path in files)
        if before != after:
            raise IndexStaleError("记录在索引期间变化")
        return cls(str(source), kind, after, count, rx, tx, first, last,
                   tuple(sorted(sources)), tuple(entries), stride)

    def save(self, index_path):
        destination = Path(index_path).resolve()
        raw_paths = {path.resolve() for path in _source_files(self.source)}
        if destination in raw_paths:
            raise ValueError("派生索引不能覆盖原始记录或分卷")
        doc = {"schema": "qx-replay-index", "version": 1, **asdict(self)}
        payload = json.dumps(doc, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
        if len(payload) > MAX_INDEX_BYTES:
            raise ValueError("索引超过16MiB上限")
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=".qx-index-", suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(payload)
                stream.flush()
            os.replace(temporary, destination)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    @classmethod
    def load(cls, source, index_path, *, cancel=None):
        source = Path(source).resolve()
        with Path(index_path).open("rb") as stream:
            doc = load_json_document(stream.read(MAX_INDEX_BYTES + 1), max_bytes=MAX_INDEX_BYTES)
        expected = {"schema", "version", "source", "kind", "fingerprints", "record_count", "rx_count", "tx_count",
                    "first_timestamp", "last_timestamp", "connection_ids", "entries", "stride"}
        if (not isinstance(doc, dict) or set(doc) != expected or doc["schema"] != "qx-replay-index"
                or type(doc["version"]) is not int or doc["version"] != 1
                or doc["source"] != str(source) or doc["kind"] not in ("csv", "raw")):
            raise ValueError("索引格式或来源无效")
        fingerprints = tuple(_fingerprint(path, cancel) for path in _source_files(source))
        if list(fingerprints) != doc["fingerprints"]:
            raise IndexStaleError("原始记录大小或SHA-256已变化，请重建索引")
        for key in ("record_count", "rx_count", "tx_count", "stride"):
            if type(doc[key]) is not int or doc[key] < 0:
                raise ValueError("索引数量无效")
        if (doc["record_count"] < 1 or doc["rx_count"] + doc["tx_count"] != doc["record_count"]
                or not 1 <= doc["stride"] <= 2**40):
            raise ValueError("索引记录数量不一致")
        first, last = _timestamp(doc["first_timestamp"]), _timestamp(doc["last_timestamp"])
        if last < first:
            raise ValueError("索引时间倒退")
        ids = doc["connection_ids"]
        if (not isinstance(ids, list) or len(ids) > 4096 or len(set(ids)) != len(ids)
                or any(not isinstance(value, str) or not 1 <= len(value) <= 128 for value in ids)):
            raise ValueError("索引来源ID无效")
        items = doc["entries"]
        if not isinstance(items, list) or not 1 <= len(items) <= MAX_ENTRIES:
            raise ValueError("索引检查点数量无效")
        entries, prior = [], -1
        for item in items:
            if not isinstance(item, dict) or set(item) != {"sequence", "timestamp", "byte_offset", "part_index"}:
                raise ValueError("索引检查点字段无效")
            entry = IndexEntry(**item)
            part_count = len(fingerprints) - 1 if source.name.casefold().endswith(".session.json") else 1
            if (type(entry.sequence) is not int or not prior < entry.sequence < doc["record_count"]
                    or type(entry.part_index) is not int or not 0 <= entry.part_index < part_count
                    or type(entry.byte_offset) is not int or entry.byte_offset < 0
                    or not first <= _timestamp(entry.timestamp) <= last):
                raise ValueError("索引检查点位置无效")
            prior = entry.sequence
            entries.append(entry)
        if entries[0].sequence != 0:
            raise ValueError("索引缺少起始检查点")
        return cls(str(source), doc["kind"], fingerprints, doc["record_count"], doc["rx_count"], doc["tx_count"],
                   doc["first_timestamp"], doc["last_timestamp"], tuple(ids), tuple(entries), doc["stride"])


class RecordingCatalog:
    """An explicit user-maintained catalog, never an automatic file crawler."""

    def __init__(self):
        self._items = {}

    def register(self, index):
        if not isinstance(index, RecordingIndex):
            raise ValueError("会话目录只接受已验证索引")
        self._items[index.source] = index
        return index

    def remove(self, source):
        self._items.pop(str(Path(source).resolve()), None)

    def entries(self):
        return tuple(self._items.values())
