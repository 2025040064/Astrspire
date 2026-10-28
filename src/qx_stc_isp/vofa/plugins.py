"""Declarative, bounded data-engine extensions; never execute plugin code.

This application's JSON manifests are not ABI-compatible with VOFA DLL/QML
plugins. Parsing or registering a manifest cannot open a device or send bytes.
"""

from __future__ import annotations

import json
import math
import re
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .model import TelemetryEvent
from .json_data import load_json_document

MAX_MANIFEST_BYTES = 64 * 1024
MAX_PLUGINS = 32
MAX_FRAME_BYTES = 16384
_ID = re.compile(r"[a-z][a-z0-9_.-]{0,63}\Z")
_STRUCT_CODES = {"float32": "f", "float64": "d", "int8": "b", "uint8": "B",
                 "int16": "h", "uint16": "H", "int32": "i", "uint32": "I"}


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"扩展清单含重复字段：{key}")
        result[key] = value
    return result


def _constant(value):
    raise ValueError(f"扩展清单含非标准数值：{value}")


def _hex(value: Any, label: str) -> bytes:
    if not isinstance(value, str) or len(value) > 128:
        raise ValueError(f"{label}必须为最多64字节的HEX文本")
    try:
        return bytes.fromhex(value)
    except ValueError as exc:
        raise ValueError(f"{label}包含无效HEX") from exc


@dataclass(frozen=True, slots=True)
class EngineManifest:
    identifier: str
    name: str
    mode: str
    options: dict

    @classmethod
    def from_document(cls, value: Any) -> EngineManifest:
        if not isinstance(value, dict) or set(value) != {"schema_version", "id", "name", "mode", "options"}:
            raise ValueError("扩展清单需要 schema_version、id、name、mode、options 五个字段")
        if (type(value["schema_version"]) is not int
                or value["schema_version"] != (2 if value["mode"] == "framed" else 1)):
            raise ValueError("不支持的扩展清单版本")
        identifier, name, mode, options = (value[key] for key in ("id", "name", "mode", "options"))
        if not isinstance(identifier, str) or _ID.fullmatch(identifier) is None:
            raise ValueError("扩展id必须为小写字母开头的1–64个字母、数字、点、横线或下划线")
        if not isinstance(name, str) or not name.strip() or len(name) > 80 or any(ord(x) < 32 for x in name):
            raise ValueError("扩展名称必须为1–80个可显示字符")
        if mode not in ("delimited", "binary_fixed", "framed") or not isinstance(options, dict):
            raise ValueError("扩展模式只接受 delimited、binary_fixed 或 framed")
        # Round-trip also detaches mutable input from a caller's document.
        options = json.loads(json.dumps(options, allow_nan=False))
        if mode == "framed":
            from .protocol import FrameDefinition, numeric_field_names
            if set(options) != {"definition", "channels"}:
                raise ValueError("framed选项需要definition和channels")
            definition = FrameDefinition.from_document(options["definition"])
            channels = options["channels"]
            names = numeric_field_names(definition)
            if (not isinstance(channels, list) or len(channels) > 16
                    or any(not isinstance(item, str) or item not in names for item in channels)
                    or len(set(channels)) != len(channels)):
                raise ValueError("channels: 需0–16个唯一数值字段名")
        elif mode == "delimited":
            if set(options) - {"separator", "prefix", "channels", "scale", "offset"}:
                raise ValueError("文本引擎含未知选项")
            separator = options.get("separator", ",")
            prefix = options.get("prefix", "")
            if not isinstance(separator, str) or not 1 <= len(separator) <= 4 or "\n" in separator or "\r" in separator:
                raise ValueError("分隔符长度应为1–4且不能包含换行")
            if not isinstance(prefix, str) or len(prefix) > 128 or "\n" in prefix or "\r" in prefix:
                raise ValueError("固定前缀无效")
            channels = options.get("channels", 0)
            if type(channels) is not int or not 0 <= channels <= 16:
                raise ValueError("通道数应为0（自动）或1–16")
        else:
            if set(options) - {"types", "endian", "header_hex", "trailer_hex", "checksum", "scale", "offset"}:
                raise ValueError("定长二进制引擎含未知选项")
            types = options.get("types")
            if not isinstance(types, list) or not 1 <= len(types) <= 16 or any(not isinstance(x, str) or x not in _STRUCT_CODES for x in types):
                raise ValueError("types需要1–16个受支持数值类型")
            if options.get("endian", "little") not in ("little", "big"):
                raise ValueError("字节序必须为 little 或 big")
            _hex(options.get("header_hex", ""), "帧头")
            _hex(options.get("trailer_hex", ""), "帧尾")
            if options.get("checksum", "none") not in ("none", "sum8", "sum16le", "sum16be"):
                raise ValueError("校验只接受 none、sum8、sum16le、sum16be（仅求和数据载荷）")
        for field, default in (("scale", 1.0), ("offset", 0.0)):
            number = options.get(field, default)
            if type(number) not in (int, float) or not math.isfinite(number) or abs(number) > 1e100:
                raise ValueError(f"{field}必须为有限数且绝对值不超过1e100")
        return cls(identifier, name, mode, options)

    @property
    def decoder_name(self) -> str:
        return f"plugin:{self.identifier}"

    def document(self) -> dict:
        return {"schema_version": 2 if self.mode == "framed" else 1, "id": self.identifier, "name": self.name,
                "mode": self.mode, "options": dict(self.options)}


def read_manifest(path: str | Path) -> EngineManifest:
    with Path(path).open("rb") as stream:
        content = stream.read(MAX_MANIFEST_BYTES + 1)
    if len(content) > MAX_MANIFEST_BYTES:
        raise ValueError("扩展清单超过64KiB")
    document = load_json_document(content.decode("utf-8-sig"), max_bytes=MAX_MANIFEST_BYTES)
    return EngineManifest.from_document(document)


class DeclarativeDecoder:
    def __init__(self, manifest: EngineManifest) -> None:
        self.manifest = EngineManifest.from_document(manifest.document())
        self._buffer = bytearray()
        self._options = self.manifest.options
        self._decoded_frames = []
        if self.manifest.mode == "framed":
            from .protocol import FrameDefinition, FrameDecoder
            self._framed = FrameDecoder(FrameDefinition.from_document(self._options["definition"]))
        if self.manifest.mode == "binary_fixed":
            self._header = _hex(self._options.get("header_hex", ""), "帧头")
            self._trailer = _hex(self._options.get("trailer_hex", ""), "帧尾")
            self._struct = struct.Struct(("<" if self._options.get("endian", "little") == "little" else ">")
                                         + "".join(_STRUCT_CODES[x] for x in self._options["types"]))
            self._checksum = self._options.get("checksum", "none")
            self._checksum_bytes = 0 if self._checksum == "none" else 1 if self._checksum == "sum8" else 2
            self._frame_size = len(self._header) + self._struct.size + self._checksum_bytes + len(self._trailer)

    def reset(self) -> None:
        self._buffer.clear()
        self._decoded_frames.clear()
        if self.manifest.mode == "framed":
            from .protocol import FrameDefinition, FrameDecoder
            self._framed = FrameDecoder(FrameDefinition.from_document(self._options["definition"]))

    def take_decoded_frames(self):
        """Compatibility drain; TelemetryEvent.frame is the preferred stream API."""
        result = tuple(self._decoded_frames)
        self._decoded_frames.clear()
        return result

    def _event(self, raw: bytes, values, text=None) -> TelemetryEvent:
        scale, offset = self._options.get("scale", 1), self._options.get("offset", 0)
        try:
            samples = tuple(float(x) * scale + offset for x in values)
        except (ValueError, OverflowError) as exc:
            return TelemetryEvent(raw=raw, text=text, error=f"扩展引擎数值错误：{exc}")
        if not all(math.isfinite(x) for x in samples):
            return TelemetryEvent(raw=raw, text=text, error="扩展引擎包含非有限数")
        return TelemetryEvent(raw=raw, text=text, samples=samples)

    def feed(self, chunk: bytes) -> list[TelemetryEvent]:
        if not isinstance(chunk, bytes):
            raise TypeError("输入必须为bytes")
        if self.manifest.mode == "framed":
            from .protocol import DecodedFrameEvent
            events = []
            for frame in self._framed.feed(chunk):
                structured = DecodedFrameEvent(self.manifest.identifier, 1, frame)
                self._decoded_frames.append(structured)
                del self._decoded_frames[:-256]
                if not frame.checksum_valid:
                    events.append(TelemetryEvent(raw=frame.raw, frame=structured, error=frame.error or "帧校验失败"))
                    continue
                channels = self._options["channels"]
                summary = {key: value.hex(" ").upper() if isinstance(value, bytes) else value
                           for key, value in frame.values.items()}
                text = json.dumps(summary, ensure_ascii=False)
                try:
                    originals = tuple(frame.values[name] for name in channels)
                    values = tuple(float(value) for value in originals)
                    if not all(math.isfinite(value) for value in values):
                        raise ValueError("数值无法有限表示")
                    if any(type(original) is int and int(value) != original
                           for original, value in zip(originals, values)):
                        raise ValueError("整数映射将丢失精度；原始值仍保留在字段详情")
                except (KeyError, ValueError, OverflowError) as exc:
                    events.append(TelemetryEvent(raw=frame.raw, text=text, frame=structured,
                                                 error=f"channels: 字段未出现或无效：{exc}"))
                    continue
                events.append(TelemetryEvent(raw=frame.raw, samples=values or None, names=tuple(channels),
                                             text=text, frame=structured))
            return events
        events = []
        # Bound staging even when a caller supplies a much larger chunk.
        for start in range(0, len(chunk), MAX_FRAME_BYTES):
            self._buffer.extend(chunk[start:start + MAX_FRAME_BYTES])
            if self.manifest.mode == "delimited":
                while (end := self._buffer.find(b"\n")) >= 0:
                    raw = bytes(self._buffer[:end + 1])
                    del self._buffer[:end + 1]
                    if len(raw) > MAX_FRAME_BYTES:
                        events.append(TelemetryEvent(raw=raw, error="扩展文本行过长"))
                        continue
                    text = raw.rstrip(b"\r\n").decode("utf-8", errors="replace")
                    prefix = self._options.get("prefix", "")
                    if prefix and not text.startswith(prefix):
                        events.append(TelemetryEvent(raw=raw, text=text))
                        continue
                    fields = text[len(prefix):].split(self._options.get("separator", ","))
                    expected = self._options.get("channels", 0)
                    if not 1 <= len(fields) <= 16 or expected and len(fields) != expected:
                        events.append(TelemetryEvent(raw=raw, text=text, error="扩展文本通道数不匹配"))
                        continue
                    events.append(self._event(raw, fields, text))
                if len(self._buffer) > MAX_FRAME_BYTES:
                    events.append(TelemetryEvent(raw=bytes(self._buffer), error="扩展文本行超过16KiB"))
                    self._buffer.clear()
            else:
                self._decode_binary(events)
        return events

    def _decode_binary(self, events: list) -> None:
        while len(self._buffer) >= self._frame_size:
            if self._header:
                index = self._buffer.find(self._header)
                if index < 0:
                    keep = len(self._header) - 1
                    if keep:
                        del self._buffer[:-keep]
                    else:
                        self._buffer.clear()
                    return
                if index:
                    del self._buffer[:index]
                if len(self._buffer) < self._frame_size:
                    return
            raw = bytes(self._buffer[:self._frame_size])
            begin = len(self._header)
            payload = raw[begin:begin + self._struct.size]
            error = None
            if self._trailer and not raw.endswith(self._trailer):
                error = "扩展二进制帧尾不匹配"
            elif self._checksum_bytes:
                actual = raw[begin + self._struct.size:begin + self._struct.size + self._checksum_bytes]
                endian = "little" if self._checksum in ("sum8", "sum16le") else "big"
                expected = (sum(payload) & ((1 << (8 * self._checksum_bytes)) - 1)).to_bytes(self._checksum_bytes, endian)
                if actual != expected:
                    error = "扩展二进制载荷求和校验不匹配"
            if error:
                events.append(TelemetryEvent(raw=raw, error=error))
                del self._buffer[:1 if self._header else self._frame_size]
            else:
                del self._buffer[:self._frame_size]
                events.append(self._event(raw, self._struct.unpack(payload)))


class PluginRegistry:
    def __init__(self) -> None:
        self._engines: dict[str, EngineManifest] = {}

    @property
    def engines(self) -> tuple[EngineManifest, ...]:
        return tuple(self._engines.values())

    def register(self, manifest: EngineManifest) -> None:
        canonical = EngineManifest.from_document(manifest.document())
        if canonical.identifier in self._engines:
            raise ValueError("扩展id已存在，未覆盖")
        if len(self._engines) >= MAX_PLUGINS:
            raise ValueError("最多注册32个扩展引擎")
        self._engines[canonical.identifier] = canonical

    def unregister(self, identifier: str) -> None:
        self._engines.pop(identifier, None)

    def create(self, name: str) -> DeclarativeDecoder:
        identifier = name.removeprefix("plugin:")
        if identifier not in self._engines:
            raise ValueError(f"工作区引用的扩展未导入：{identifier}")
        return DeclarativeDecoder(self._engines[identifier])


PLUGIN_REGISTRY = PluginRegistry()
