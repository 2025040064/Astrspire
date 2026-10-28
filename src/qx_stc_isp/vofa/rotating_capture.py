"""Exclusive, bounded raw-recording parts with an explicit session manifest."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from .capture_stream import MAX_FILE_BYTES, StreamRecorder, iter_stream_records
from .json_data import load_json_document


def _digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


class RotatingStreamRecorder:
    def __init__(self, path, *, max_part_bytes=MAX_FILE_BYTES,
                 connection_id=None, generation=0):
        if type(max_part_bytes) is not int or not 128 <= max_part_bytes <= MAX_FILE_BYTES:
            raise ValueError("原始分卷上限需128字节至128MiB")
        self.path = Path(path)
        # Validate source metadata before reserving or touching any output file.
        prototype = StreamRecorder(self.path, connection_id=connection_id, generation=generation)
        self.connection_id, self.generation = connection_id, generation
        self.format_version = prototype.format_version
        self.manifest_path = self.path.with_name(self.path.name + ".session.json")
        self.max_part_bytes = max_part_bytes
        self.parts = []
        self._current = None
        self._started = False
        self._owned_manifest = False
        self._last_stamp = None
        self._error = None

    @property
    def is_open(self):
        return self._current is not None

    def _manifest(self, complete=False):
        document = {"schema": "qx-stream-session", "version": self.format_version,
                    "complete": complete, "parts": self.parts, "error": self._error}
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                    dir=self.path.parent, prefix=".qx-session-", suffix=".tmp", delete=False) as stream:
                temporary = stream.name
                json.dump(document, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
            os.replace(temporary, self.manifest_path)
        finally:
            if temporary is not None and os.path.exists(temporary):
                os.unlink(temporary)

    def _new_part(self):
        if len(self.parts) >= 4096:
            raise ValueError("会话已达到4096个分卷，请开始新会话")
        number = len(self.parts) + 1
        path = self.path if number == 1 else self.path.with_name(f"{self.path.stem}.part{number:04d}{self.path.suffix}")
        current = StreamRecorder(path, connection_id=self.connection_id, generation=self.generation)
        current.open()
        self._current = current
        self.parts.append({"path": path.name, "bytes": current.bytes_written,
                           "records": 0, "sha256": None})
        self._manifest()

    def open(self):
        if self._started:
            raise RuntimeError("录制会话只能打开一次")
        self._started = True
        # Reserve the manifest exclusively before touching the first part.
        with self.manifest_path.open("x", encoding="utf-8") as stream:
            stream.write("{}\n")
        self._owned_manifest = True
        try:
            self._new_part()
        except Exception as exc:
            self._error = str(exc)
            self._manifest()
            raise

    def _seal_part(self):
        current, self._current = self._current, None
        if current is not None:
            try:
                current.flush()
            finally:
                current.close()
            self.parts[-1].update(bytes=current.bytes_written, records=current.record_count,
                                  sha256=_digest(current.path))

    def record(self, direction, payload, at=None):
        if self._current is None:
            raise RuntimeError("原始会话未打开")
        try:
            if direction not in ("RX", "TX") or not isinstance(payload, bytes) or len(payload) > 65536:
                raise ValueError("原始块方向/长度无效")
            stamp = datetime.now(timezone.utc) if at is None else at
            if not isinstance(stamp, datetime) or stamp.tzinfo is None:
                raise ValueError("记录时间需包含时区")
            stamp = stamp.astimezone(timezone.utc)
            if self._last_stamp is not None and stamp < self._last_stamp:
                raise ValueError("记录时间倒退，未写入")
            document = {"direction": direction, "timestamp_utc": stamp.isoformat(timespec="microseconds"),
                        "hex": payload.hex()}
            if self.format_version == 2:
                document.update(connection_id=self.connection_id, generation=self.generation)
            size = len((json.dumps(document, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8"))
            header_size = len((json.dumps({"schema": "qx-stream", "version": self.format_version},
                                         separators=(",", ":")) + "\n").encode())
            if header_size + size > self.max_part_bytes:
                raise ValueError("单条记录大于分卷上限，未写入")
            if self._current.bytes_written + size > self.max_part_bytes:
                self._seal_part()
                self._new_part()
            result = self._current.record(direction, payload, at=stamp)
            self._last_stamp = stamp
            self.parts[-1].update(bytes=self._current.bytes_written, records=self._current.record_count)
            return result
        except Exception as exc:
            self._error = str(exc)
            raise

    def flush(self):
        if self._current is not None:
            self._current.flush()

    def close(self):
        if not self._owned_manifest:
            return
        try:
            self._seal_part()
        except Exception as exc:
            self._error = str(exc)
            raise
        finally:
            self._manifest(complete=self._error is None)
            self._owned_manifest = False


def iter_recording_records(path):
    """Explicit file import only; retain old qx-stream v1 readers unchanged."""
    source = Path(path)
    if not source.name.casefold().endswith(".session.json"):
        yield from iter_stream_records(source)
        return
    with source.open("rb") as stream:
        document = load_json_document(stream.read(4 * 1024 * 1024 + 1), max_bytes=4 * 1024 * 1024)
    if (not isinstance(document, dict) or set(document) != {"schema", "version", "complete", "parts", "error"}
            or document["schema"] != "qx-stream-session" or type(document["version"]) is not int
            or document["version"] not in (1, 2) or document["complete"] is not True
            or document["error"] is not None):
        raise ValueError("会话未完整结束或格式无效；可显式选择已完成的单个分卷检查")
    parts = document["parts"]
    if not isinstance(parts, list) or not 1 <= len(parts) <= 4096:
        raise ValueError("会话分卷列表无效")
    selected, names = [], set()
    for part in parts:
        if not isinstance(part, dict) or set(part) != {"path", "bytes", "records", "sha256"}:
            raise ValueError("分卷字段无效")
        name = part["path"]
        if (not isinstance(name, str) or not name or Path(name).name != name
                or "\\" in name or "/" in name or name in names):
            raise ValueError("分卷路径需为唯一同目录文件名")
        target = source.parent / name
        if target.resolve().parent != source.parent.resolve():
            raise ValueError("分卷越出会话目录")
        if (type(part["bytes"]) is not int or not 1 <= part["bytes"] <= MAX_FILE_BYTES
                or type(part["records"]) is not int or part["records"] < 0
                or not isinstance(part["sha256"], str) or len(part["sha256"]) != 64):
            raise ValueError("分卷大小/数量/哈希无效")
        names.add(name)
        selected.append((target, part))
    total = sum(part["bytes"] for _, part in selected)
    before, last = 0, None
    for part_index, (target, part) in enumerate(selected):
        if target.stat().st_size != part["bytes"] or _digest(target) != part["sha256"]:
            raise ValueError(f"分卷大小或SHA-256不匹配：{target.name}")
        count = 0
        for record in iter_stream_records(target):
            if last is not None and record.timestamp < last:
                raise ValueError("跨分卷时间倒退")
            last = record.timestamp
            count += 1
            if (record.connection_id is None) != (document["version"] == 1):
                raise ValueError("会话与分卷来源格式版本不一致")
            yield replace(record, bytes_read=before + record.bytes_read, file_size=total,
                          part_index=part_index)
        if count != part["records"]:
            raise ValueError("分卷记录数量不匹配")
        before += part["bytes"]
