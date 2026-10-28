"""Inert serial preferences and configuration history, never device actions."""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass

from .transmit import encode_send_payload

ENCODINGS = ("utf-8", "gbk", "latin-1", "ascii", "utf-16le")
ENDINGS = ("none", "LF", "CRLF", "CR", "TAB")
FLOW_CONTROLS = ("none", "rtscts", "xonxoff")


class SendHistory:
    def __init__(self):
        self._entries = []

    @property
    def entries(self):
        return tuple(dict(item) for item in self._entries)

    def record(self, text, mode, ending, encoding):
        encode_send_payload(text, mode=mode, line_ending=ending, encoding=encoding)
        item = {"text": text, "mode": mode, "ending": ending, "encoding": encoding}
        self._entries = [item] + [entry for entry in self._entries if entry != item]
        del self._entries[20:]

    def clear(self):
        self._entries.clear()


class ConfigHistory:
    """JSON snapshots only. Undo/redo never holds callbacks or transports."""
    def __init__(self, initial, *, limit=200):
        if type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError("撤销上限需为1–200")
        self.limit = limit
        self._states = [self._encode(initial)]
        self._index = 0

    @staticmethod
    def _encode(value):
        result = json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
        if len(result.encode("utf-8")) > 128 * 1024:
            raise ValueError("单次编辑快照超过128KiB")
        return result

    @property
    def can_undo(self):
        return self._index > 0

    @property
    def can_redo(self):
        return self._index + 1 < len(self._states)

    def commit(self, value):
        encoded = self._encode(value)
        if encoded != self._states[self._index]:
            del self._states[self._index + 1:]
            self._states.append(encoded)
            self._states = self._states[-self.limit - 1:]
            self._index = len(self._states) - 1

    def undo(self):
        if self.can_undo:
            self._index -= 1
        return json.loads(self._states[self._index])

    def redo(self):
        if self.can_redo:
            self._index += 1
        return json.loads(self._states[self._index])


@dataclass(frozen=True, slots=True)
class SerialPreset:
    id: str
    name: str
    port: str = ""
    baudrate: int = 115200
    bytesize: int = 8
    parity: str = "N"
    stopbits: float = 1
    rx_encoding: str = "utf-8"
    tx_encoding: str = "utf-8"
    ending: str = "none"
    flow_control: str = "none"

    def __post_init__(self):
        if not isinstance(self.id, str) or not re.fullmatch(r"[a-zA-Z0-9_.-]{1,64}", self.id):
            raise ValueError("预设id无效")
        if not isinstance(self.name, str) or not self.name.strip() or len(self.name) > 80:
            raise ValueError("预设名称需1–80字")
        if not isinstance(self.port, str) or len(self.port) > 256 or "\0" in self.port:
            raise ValueError("预设端口无效")
        if type(self.baudrate) is not int or not 1 <= self.baudrate <= 4000000:
            raise ValueError("预设波特率无效")
        if type(self.bytesize) is not int or self.bytesize not in (5, 6, 7, 8):
            raise ValueError("预设数据位无效")
        if self.parity not in ("N", "E", "O", "M", "S") or type(self.stopbits) not in (int, float) or self.stopbits not in (1, 1.5, 2):
            raise ValueError("预设停止/校验位无效")
        if self.rx_encoding not in ENCODINGS or self.tx_encoding not in ENCODINGS:
            raise ValueError("预设编码无效")
        if self.ending not in ENDINGS or self.flow_control not in FLOW_CONTROLS:
            raise ValueError("预设结束符/流控无效")

    def document(self):
        return asdict(self)


def validate_presets(values):
    if not isinstance(values, (tuple, list)) or len(values) > 32:
        raise ValueError("连接预设最多32个")
    try:
        presets = tuple(SerialPreset(**item) for item in values)
    except TypeError as exc:
        raise ValueError("连接预设字段无效") from exc
    if len({item.id for item in presets}) != len(presets):
        raise ValueError("连接预设id重复")
    return tuple(item.document() for item in presets)
