"""One user-level configuration store; no auto-connect or transport actions."""
from contextlib import contextmanager
from dataclasses import dataclass
import json
import os
from pathlib import Path

from ..updates.config import app_data_dir, atomic_write
from .serial_preferences import SendHistory

MAX_BYTES = 4 * 1024 * 1024


def validate_history(entries):
    if not isinstance(entries, list) or len(entries) > 20:
        raise ValueError("发送历史最多20条")
    history = SendHistory()
    for entry in reversed(entries):
        if not isinstance(entry, dict) or set(entry) != {"text", "mode", "ending", "encoding"}:
            raise ValueError("发送历史字段无效")
        history.record(entry["text"], entry["mode"], entry["ending"], entry["encoding"])
    return list(history.entries)


@dataclass(frozen=True)
class AppState:
    revision: int
    data: dict


@contextmanager
def _exclusive(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise ValueError("其他窗口正在修改配置，请稍后重试") from exc
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


class AppStateStore:
    def __init__(self, path=None):
        self.path = Path(path) if path is not None else app_data_dir() / "application.json"

    def load(self):
        try:
            with self.path.open("rb") as stream:
                raw = stream.read(MAX_BYTES + 1)
        except FileNotFoundError:
            return AppState(0, {})
        if len(raw) > MAX_BYTES:
            raise ValueError("应用配置超过4MiB")
        value = json.loads(raw)
        if (not isinstance(value, dict) or set(value) != {"schema_version", "revision", "data"}
                or value["schema_version"] != 1 or type(value["revision"]) is not int
                or value["revision"] < 0 or not isinstance(value["data"], dict)):
            raise ValueError("应用配置结构无效")
        if "history" in value["data"]:
            value["data"]["history"] = validate_history(value["data"]["history"])
        return AppState(value["revision"], value["data"])

    def save(self, data, *, expected_revision):
        if not isinstance(data, dict) or type(expected_revision) is not int:
            raise ValueError("配置数据或修订无效")
        detached = json.loads(json.dumps(data, allow_nan=False))
        if "history" in detached:
            detached["history"] = validate_history(detached["history"])
        with _exclusive(self.path.with_suffix(".lock")):
            previous = self.load()
            if previous.revision != expected_revision:
                raise ValueError("配置已被其他窗口修改，请重新加载后保存")
            value = {"schema_version": 1, "revision": previous.revision + 1, "data": detached}
            raw = json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2).encode("utf-8")
            if len(raw) > MAX_BYTES:
                raise ValueError("应用配置超过4MiB")
            atomic_write(self.path, raw)
        return AppState(value["revision"], detached)
