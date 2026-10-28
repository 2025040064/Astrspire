"""Explicit command-list files; loading never queues or sends a command."""

from __future__ import annotations

import json
import stat
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from itertools import islice
from pathlib import Path
from typing import Any

from .transmit import encode_send_payload

MAX_COMMANDS = 64
MAX_COMMAND_FILE_BYTES = 1024 * 1024
_ENTRY_KEYS = {"name", "payload", "mode", "ending"}
MAX_COMMAND_GROUPS = 32


@dataclass(frozen=True, slots=True)
class CommandEntry:
    name: str
    payload: str
    mode: str = "text"
    ending: str = "none"
    encoding: str = "utf-8"

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not 1 <= len(self.name) <= 80 or not self.name.strip():
            raise ValueError("命令名称必须为 1–80 个字符，不能全为空白")
        try:
            self.name.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ValueError("命令名称包含无法编码为 UTF-8 的字符") from exc
        if not isinstance(self.payload, str) or not self.payload:
            raise ValueError("命令内容必须为非空字符串")
        if not isinstance(self.mode, str) or self.mode not in {"text", "hex"}:
            raise ValueError("命令格式必须为 text 或 hex")
        if not isinstance(self.ending, str) or self.ending not in {"none", "LF", "CRLF", "CR", "TAB"}:
            raise ValueError("命令结束符必须为 none、LF、CRLF、CR 或 TAB")
        if self.encoding not in ("utf-8", "gbk", "latin-1", "ascii", "utf-16le"):
            raise ValueError("命令编码无效")
        encode_send_payload(self.payload, mode=self.mode, line_ending=self.ending, encoding=self.encoding)


@dataclass(frozen=True, slots=True)
class CommandItem:
    """A command's stable identity, independent of its displayed row."""
    id: str
    entry: CommandEntry

    def __post_init__(self):
        from .dashboard import _identifier
        _identifier(self.id)
        if not isinstance(self.entry, CommandEntry):
            raise ValueError("命令内容必须为CommandEntry")
        object.__setattr__(self, "entry", CommandEntry(**asdict(self.entry)))

    def to_document(self):
        return {"id": self.id, **asdict(self.entry)}

    @classmethod
    def from_document(cls, value):
        if not isinstance(value, dict) or set(value) not in (_ENTRY_KEYS | {"id"}, _ENTRY_KEYS | {"id", "encoding"}):
            raise ValueError("分组命令字段不完整或含未知字段")
        return cls(value["id"], CommandEntry(**{key: value[key] for key in _ENTRY_KEYS},
                                             encoding=value.get("encoding", "utf-8")))


@dataclass(frozen=True, slots=True)
class CommandGroup:
    id: str
    name: str
    commands: tuple[CommandItem, ...] = ()

    def __post_init__(self):
        from .dashboard import _identifier, _title
        _identifier(self.id)
        _title(self.name)
        if not isinstance(self.commands, tuple) or len(self.commands) > MAX_COMMANDS or any(not isinstance(item, CommandItem) for item in self.commands):
            raise ValueError("每个命令组最多64条命令")
        if len({item.id for item in self.commands}) != len(self.commands):
            raise ValueError("命令稳定标识重复")
        object.__setattr__(self, "commands", tuple(CommandItem(item.id, item.entry) for item in self.commands))

    def snapshot(self) -> tuple[CommandEntry, ...]:
        """Value snapshot: editing a group can never alter an active sequence."""
        return tuple(CommandEntry(**asdict(item.entry)) for item in self.commands)

    def to_document(self):
        return {"id": self.id, "name": self.name, "commands": [item.to_document() for item in self.commands]}

    @classmethod
    def from_document(cls, value):
        if not isinstance(value, dict) or set(value) != {"id", "name", "commands"} or not isinstance(value["commands"], list):
            raise ValueError("命令组字段不完整或含未知字段")
        return cls(value["id"], value["name"], tuple(CommandItem.from_document(item) for item in value["commands"]))


def migrate_command_groups(entries) -> tuple[CommandGroup, ...]:
    """Turn legacy flat preferences into a deterministic, inert default group."""
    items = tuple(CommandItem(f"legacy-command-{index}", item if isinstance(item, CommandEntry) else CommandEntry(**item)) for index, item in enumerate(entries))
    return (CommandGroup("legacy-group-default", "默认组", items),)


def validate_command_groups(values) -> tuple[CommandGroup, ...]:
    if not isinstance(values, (tuple, list)) or not 1 <= len(values) <= MAX_COMMAND_GROUPS:
        raise ValueError("命令组须为1–32组")
    groups = tuple(item if isinstance(item, CommandGroup) else CommandGroup.from_document(item) for item in values)
    if len({item.id for item in groups}) != len(groups):
        raise ValueError("命令组标识重复")
    ids = [command.id for group in groups for command in group.commands]
    if len(set(ids)) != len(ids):
        raise ValueError("跨组命令标识重复")
    return groups


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"命令文件含重复字段：{key}")
        result[key] = value
    return result


def _invalid_constant(value: str) -> None:
    raise ValueError(f"命令文件包含非标准 JSON 数值：{value}")


def load_commands(path: str | Path) -> tuple[CommandEntry, ...]:
    """Read only this application's schema 1; return inert value objects."""
    source = Path(path)
    info = source.stat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("命令列表只接受普通 JSON 文件")
    if info.st_size > MAX_COMMAND_FILE_BYTES:
        raise ValueError("命令列表超过 1 MiB 上限")
    with source.open("rb") as stream:
        content = stream.read(MAX_COMMAND_FILE_BYTES + 1)
    if len(content) > MAX_COMMAND_FILE_BYTES:
        raise ValueError("命令列表超过 1 MiB 上限")
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("命令列表必须使用 UTF-8 编码") from exc
    try:
        document = json.loads(text, object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
    except json.JSONDecodeError as exc:
        raise ValueError("命令列表不是有效的 JSON 文件") from exc
    if not isinstance(document, dict) or set(document) != {"schema_version", "commands"}:
        raise ValueError("命令列表顶层字段不完整或含未知字段")
    if type(document["schema_version"]) is not int or document["schema_version"] not in (1, 2):
        raise ValueError("不支持的命令列表版本")
    entries = document["commands"]
    if not isinstance(entries, list) or len(entries) > MAX_COMMANDS:
        raise ValueError("命令列表必须为数组且最多 64 条")
    result = []
    for index, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict) or set(entry) != (_ENTRY_KEYS | ({"encoding"} if document["schema_version"] == 2 else set())):
            raise ValueError(f"第 {index} 条命令字段不完整或含未知字段")
        try:
            result.append(CommandEntry(**entry))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"第 {index} 条命令无效：{exc}") from exc
    return tuple(result)


def save_commands(path: str | Path, entries: Iterable[CommandEntry]) -> None:
    """Validate the entire bounded list before exclusively creating a file."""
    try:
        selected = list(islice(iter(entries), MAX_COMMANDS + 1))
    except TypeError as exc:
        raise ValueError("命令列表必须为可迭代的 CommandEntry 集合") from exc
    if len(selected) > MAX_COMMANDS:
        raise ValueError("命令列表最多 64 条")
    canonical = []
    for entry in selected:
        if not isinstance(entry, CommandEntry):
            raise ValueError("命令列表的每一项必须是 CommandEntry")
        # Revalidate even a frozen object: callers may have bypassed dataclass
        # protection through object.__setattr__, so saved files stay canonical.
        canonical.append(CommandEntry(entry.name, entry.payload, entry.mode, entry.ending, entry.encoding))
    text = json.dumps({"schema_version": 2, "commands": [asdict(item) for item in canonical]},
                      ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    encoded = text.encode("utf-8")
    if len(encoded) > MAX_COMMAND_FILE_BYTES:
        raise ValueError("命令列表超过 1 MiB 上限")
    with Path(path).open("xb") as stream:
        stream.write(encoded)
