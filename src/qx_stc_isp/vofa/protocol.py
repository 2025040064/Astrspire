"""Bounded serial-frame tools, separate from every STC ISP protocol.

Named CRC loops adapted from Lissio-Core 559d4a466bffe9e7ee2788730e9675ac84de6a12,
crates/protocol/device_profile/src/frame/checksum.rs.
Copyright (C) 2026 Horldsence and Lissio contributors. Apache-2.0;
see third_party/Lissio/LICENSE and NOTICE.md. Python integration is Astrspire's.
"""
from __future__ import annotations

import zlib
import json
import math
import re
import struct
from dataclasses import dataclass, field

from .transmit import MAX_SEND_BYTES, encode_send_payload

MAX_FRAME_BYTES = 16384
_TYPES = {"uint8": "B", "int8": "b", "uint16": "H", "int16": "h",
          "uint32": "I", "int32": "i", "float32": "f", "float64": "d"}

CHECKSUMS = ("none", "sum8", "sum16le", "sum16be", "xor8", "lrc", "crc8-smbus",
             "crc16-modbus", "crc16-ccitt-false", "crc32-iso-hdlc")


def checksum_bytes(algorithm: str, data: bytes) -> bytes:
    """Return named-variant wire bytes; the caller owns the coverage interval."""
    if algorithm not in CHECKSUMS:
        raise ValueError("checksum.algorithm: 不支持的校验算法")
    if algorithm == "none":
        return b""
    if algorithm == "sum8":
        return bytes([sum(data) & 255])
    if algorithm in ("sum16le", "sum16be"):
        return (sum(data) & 65535).to_bytes(2, "little" if algorithm.endswith("le") else "big")
    if algorithm == "lrc":
        return bytes([-sum(data) & 255])
    if algorithm == "xor8":
        value = 0
        for byte in data:
            value ^= byte
        return bytes([value])
    if algorithm == "crc32-iso-hdlc":
        return (zlib.crc32(data) & 0xffffffff).to_bytes(4, "little")
    if algorithm == "crc8-smbus":
        crc = 0
        for byte in data:
            crc ^= byte
            for _ in range(8):
                crc = ((crc << 1) ^ (0x07 if crc & 0x80 else 0)) & 255
        return bytes([crc])
    crc = 65535
    for byte in data:
        crc ^= byte if algorithm == "crc16-modbus" else byte << 8
        for _ in range(8):
            if algorithm == "crc16-modbus":
                crc = (crc >> 1) ^ (0xa001 if crc & 1 else 0)
            else:
                crc = ((crc << 1) ^ (0x1021 if crc & 0x8000 else 0)) & 65535
    return crc.to_bytes(2, "little" if algorithm == "crc16-modbus" else "big")


def _hex(text, path):
    if not isinstance(text, str) or len(text) > MAX_FRAME_BYTES * 8:
        raise ValueError(f"{path}: HEX内容无效或过长")
    return encode_send_payload(text, mode="hex") if text.strip() else b""


@dataclass(frozen=True)
class FrameDefinition:
    """Validated, detached document. No imports, evaluation or device access."""
    serialized: str

    @classmethod
    def from_document(cls, document):
        if not isinstance(document, dict):
            raise ValueError("$: 协议必须为对象")
        encoded = json.dumps(document, allow_nan=False, ensure_ascii=False)
        if len(encoded.encode("utf-8")) > 65536:
            raise ValueError("$: 协议超过64KiB")
        doc = json.loads(encoded)
        if set(doc) - {"header_hex", "tail_hex", "fields", "checksum"}:
            raise ValueError("$: 包含未知字段")
        header, tail = _hex(doc.get("header_hex", ""), "header_hex"), _hex(doc.get("tail_hex", ""), "tail_hex")
        fields = doc.get("fields")
        if not header or len(header) > 64 or len(tail) > 64:
            raise ValueError("header_hex/tail_hex: 帧头需1–64字节，帧尾最多64字节")
        if not isinstance(fields, list) or not 1 <= len(fields) <= 64:
            raise ValueError("fields: 需要1–64个字段")
        seen = {}
        fixed_size = len(header) + len(tail)
        for index, item in enumerate(fields):
            path = f"fields[{index}]"
            if not isinstance(item, dict) or set(item) - {
                "id", "type", "width", "endian", "length_from", "length_of",
                "length_mode", "adjustment", "value", "encoding", "bits", "when",
                "enum", "item_type", "count", "count_from",
            }:
                raise ValueError(f"{path}: 字段格式无效或含未知选项")
            name, kind = item.get("id"), item.get("type")
            if not isinstance(name, str) or not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_]{0,63}", name) or name in seen:
                raise ValueError(f"{path}.id: 名称无效或重复")
            if kind not in {*_TYPES, "length", "id", "bytes", "text", "bits", "array"}:
                raise ValueError(f"{path}.type: 不支持的字段类型")
            if item.get("endian", "little") not in ("little", "big"):
                raise ValueError(f"{path}.endian: 只接受little/big")
            if kind == "array":
                if item.get("item_type") not in _TYPES:
                    raise ValueError(f"{path}.item_type: 数组元素需为受支持数值类型")
                count = item.get("count")
                if count is None:
                    if item.get("count_from") not in seen or seen[item["count_from"]] not in ("length", "id"):
                        raise ValueError(f"{path}.count_from: 需引用前面的Length或ID")
                    width = 0
                elif type(count) is not int or not 1 <= count <= 1024 or "count_from" in item:
                    raise ValueError(f"{path}.count: 需1–1024，不能同时使用count_from")
                else:
                    width = count * struct.calcsize(_TYPES[item["item_type"]])
                if "width" in item:
                    raise ValueError(f"{path}.width: 数组宽度由元素类型和数量确定")
            elif kind in _TYPES:
                width = struct.calcsize(_TYPES[kind])
                if "width" in item and item["width"] != width:
                    raise ValueError(f"{path}.width: 数值类型宽度固定为{width}")
            else:
                width = item.get("width", 0 if kind in ("bytes", "text") else 1)
                if type(width) is not int or not 0 <= width <= MAX_FRAME_BYTES:
                    raise ValueError(f"{path}.width: 字节数无效")
                if kind in ("id", "length", "bits") and not 1 <= width <= 8:
                    raise ValueError(f"{path}.width: 整数/位域宽度为1–8")
            if kind in ("bytes", "text") and not width:
                reference = item.get("length_from")
                if reference not in seen or seen[reference] != "length":
                    raise ValueError(f"{path}.length_from: 动态字段需引用前面的Length")
            if kind == "length":
                if item.get("length_mode", "field") not in ("field", "frame", "count"):
                    raise ValueError(f"{path}.length_mode: 只接受field/frame/count")
                if type(item.get("adjustment", 0)) is not int or abs(item.get("adjustment", 0)) > MAX_FRAME_BYTES:
                    raise ValueError(f"{path}.adjustment: 无效长度修正")
            if kind == "text" and item.get("encoding", "utf-8") not in ("utf-8", "gbk", "ascii", "latin-1", "utf-16le"):
                raise ValueError(f"{path}.encoding: 编码无效")
            enumeration = item.get("enum")
            if enumeration is not None:
                if (kind not in {*_TYPES, "id", "bits"} or kind.startswith("float")
                        or not isinstance(enumeration, dict) or not 1 <= len(enumeration) <= 256
                        or any(not isinstance(key, str) or not re.fullmatch(r"-?(0|[1-9][0-9]*)", key)
                               or not isinstance(label, str) or not 1 <= len(label) <= 80
                               for key, label in enumeration.items())):
                    raise ValueError(f"{path}.enum: 需整数字段及十进制字符串到名称的映射")
            when = item.get("when")
            if when is not None:
                if (not isinstance(when, dict) or set(when) != {"id", "equals"}
                        or when["id"] not in seen or seen[when["id"]] != "id"
                        or type(when["equals"]) is not int):
                    raise ValueError(f"{path}.when: 条件需引用前面的ID整数")
            if kind == "bits":
                bits = item.get("bits")
                if not isinstance(bits, list) or not 1 <= len(bits) <= 16:
                    raise ValueError(f"{path}.bits: 需要1–16个位域切片")
                bit_ids = set()
                for bit in bits:
                    if (not isinstance(bit, dict) or set(bit) - {"id", "offset", "length", "signed"}
                            or not isinstance(bit.get("id"), str) or not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_]{0,63}", bit["id"])
                            or bit["id"] in bit_ids
                            or type(bit.get("offset")) is not int or type(bit.get("length")) is not int
                            or bit["offset"] < 0 or bit["length"] < 1
                            or bit["offset"] + bit["length"] > width * 8
                            or type(bit.get("signed", False)) is not bool):
                        raise ValueError(f"{path}.bits: 位域越界或名称无效")
                    bit_ids.add(bit["id"])
            seen[name] = kind
            fixed_size += width
        for index, item in enumerate(fields):
            if item.get("length_of") is not None and item["length_of"] not in seen:
                raise ValueError(f"fields[{index}].length_of: 引用不存在")
            if item.get("length_mode") == "count" and seen.get(item.get("length_of")) != "array":
                raise ValueError(f"fields[{index}].length_mode: count需明确引用数组")
            if item["type"] == "array" and "count_from" in item:
                source = next(field for field in fields if field["id"] == item["count_from"])
                if source["type"] == "length" and (source.get("length_mode") != "count" or source.get("length_of") != item["id"]):
                    raise ValueError(f"fields[{index}].count_from: Length需明确使用count模式并引用本数组")
        checksum = doc.get("checksum", {"algorithm": "none", "start": 0, "end": "checksum"})
        if not isinstance(checksum, dict) or set(checksum) - {"algorithm", "start", "end", "byte_order"}:
            raise ValueError("checksum: 配置无效")
        size = len(checksum_bytes(checksum.get("algorithm", "none"), b""))
        start, end = checksum.get("start", 0), checksum.get("end", "checksum")
        if type(start) is not int or start < 0 or start > MAX_FRAME_BYTES:
            raise ValueError("checksum.start: 必须为从帧首起算的字节偏移")
        if end != "checksum" and (type(end) is not int or end < start or end > MAX_FRAME_BYTES):
            raise ValueError("checksum.end: 结束偏移无效（左闭右开）")
        if checksum.get("byte_order", "default") not in ("default", "little", "big"):
            raise ValueError("checksum.byte_order: 字节序无效")
        if fixed_size + size > MAX_FRAME_BYTES:
            raise ValueError("fields: 固定帧超过16KiB")
        variable = any(item.get("when") or item["type"] == "array" and "count_from" in item
                       or item["type"] in ("bytes", "text") and not item.get("width", 0)
                       for item in fields)
        checksum_offset = fixed_size - len(tail)
        if not variable and (start > checksum_offset or type(end) is int and end > checksum_offset):
            raise ValueError("checksum: 覆盖范围超出固定帧数据")
        return cls(encoded)

    def document(self):
        return json.loads(self.serialized)


def _wire_checksum(spec, data, offset):
    start, end = spec.get("start", 0), spec.get("end", "checksum")
    end = offset if end == "checksum" else end
    if not 0 <= start <= end <= offset:
        raise ValueError("checksum: 覆盖范围超出校验字节之前的数据")
    algorithm = spec.get("algorithm", "none")
    value = checksum_bytes(algorithm, data[start:end])
    default = "big" if algorithm in ("sum16be", "crc16-ccitt-false") else "little"
    return value[::-1] if len(value) > 1 and spec.get("byte_order", "default") not in ("default", default) else value


def _width(field):
    if field["type"] == "array":
        return struct.calcsize(_TYPES[field["item_type"]]) * field.get("count", 0)
    return struct.calcsize(_TYPES[field["type"]]) if field["type"] in _TYPES else field.get("width", 1)


def _active(field, values):
    when = field.get("when")
    return when is None or values.get(when["id"]) == when["equals"]


def numeric_field_names(definition: FrameDefinition) -> set[str]:
    """Explicit scalar paths; dynamic arrays require a bounded element index."""
    names = set()
    for item in definition.document()["fields"]:
        name, kind = item["id"], item["type"]
        if kind in _TYPES or kind in ("id", "length"):
            names.add(name)
        elif kind == "bits":
            names.update(name + "." + bit["id"] for bit in item["bits"])
        elif kind == "array":
            names.update(f"{name}[{index}]" for index in range(item.get("count", 1024)))
    return names


class FrameTemplate:
    """Pure explicit command encoder; no routing, timing or automatic sends."""
    def __init__(self, definition: FrameDefinition):
        self.definition = definition

    def encode(self, parameters: dict) -> bytes:
        doc = self.definition.document()
        fields = doc["fields"]
        if not isinstance(parameters, dict) or set(parameters) - {item["id"] for item in fields}:
            raise ValueError("parameters: 参数对象含未知字段")
        data = bytearray(_hex(doc.get("header_hex", ""), "header_hex"))
        values, spans, lengths = {}, {}, []
        for item in fields:
            if not _active(item, values):
                continue
            name, kind = item["id"], item["type"]
            value = parameters.get(name, item.get("value"))
            width = _width(item)
            endian = item.get("endian", "little")
            try:
                if kind == "length":
                    raw = bytes(width)
                    lengths.append((item, len(data)))
                    value = 0
                elif kind in _TYPES:
                    if type(value) not in (int, float) or not math.isfinite(value):
                        raise ValueError("需要有限数值")
                    raw = struct.pack(("<" if endian == "little" else ">") + _TYPES[kind], value)
                elif kind in ("id", "bits"):
                    if type(value) is not int:
                        raise ValueError("需要整数；位域发送使用整个容器整数")
                    raw = value.to_bytes(width, "big" if kind == "bits" else endian)
                elif kind == "array":
                    if not isinstance(value, (tuple, list)) or not 1 <= len(value) <= 1024:
                        raise ValueError("需要1–1024个数值元素")
                    count = item.get("count", values.get(item.get("count_from")))
                    source = next((field for field in fields if field["id"] == item.get("count_from")), None)
                    if source is not None and source["type"] == "length" and source.get("length_mode") == "count":
                        count = len(value)
                    if count != len(value):
                        raise ValueError("数组数量与count/count_from不符")
                    if any(type(element) not in (int, float) or not math.isfinite(element) for element in value):
                        raise ValueError("数组需要有限数值")
                    raw = struct.pack(("<" if endian == "little" else ">") + _TYPES[item["item_type"]] * count, *value)
                elif kind == "bytes":
                    raw = _hex(value, name) if isinstance(value, str) else bytes(value) if isinstance(value, bytes) else None
                    if raw is None:
                        raise ValueError("需要HEX字符串或bytes")
                else:
                    if not isinstance(value, str):
                        raise ValueError("需要文本")
                    raw = value.encode(item.get("encoding", "utf-8"), errors="strict")
                if kind in ("bytes", "text") and item.get("width", 0) and len(raw) != item["width"]:
                    raise ValueError("数据长度与width不符")
            except (ValueError, TypeError, OverflowError, struct.error) as exc:
                raise ValueError(f"parameters.{name}: {exc}") from exc
            values[name] = value
            spans[name] = len(raw)
            data.extend(raw)
            if len(data) > MAX_FRAME_BYTES:
                raise ValueError("parameters: 帧超过16KiB")
        spec = doc.get("checksum", {})
        checksum_size = len(checksum_bytes(spec.get("algorithm", "none"), b""))
        tail = _hex(doc.get("tail_hex", ""), "tail_hex")
        total = len(data) + checksum_size + len(tail)
        if total > MAX_SEND_BYTES:
            raise ValueError("parameters: 帧超过16KiB")
        for item, offset in lengths:
            if item.get("length_mode", "field") == "frame":
                value = total - item.get("adjustment", 0)
            elif item.get("length_mode") == "count":
                target = values.get(item["length_of"])
                if not isinstance(target, (tuple, list)):
                    raise ValueError(f"fields.{item['id']}: count目标数组未激活")
                value = len(target) - item.get("adjustment", 0)
            else:
                reference = item.get("length_of")
                if reference is None:
                    candidates = [field["id"] for field in fields if field.get("length_from") == item["id"] and field["id"] in spans]
                    if len(candidates) != 1:
                        raise ValueError(f"fields.{item['id']}.length_of: 需要明确唯一目标")
                    reference = candidates[0]
                if reference not in spans:
                    raise ValueError(f"fields.{item['id']}.length_of: 目标字段未激活")
                value = spans[reference] - item.get("adjustment", 0)
            try:
                data[offset:offset + _width(item)] = value.to_bytes(_width(item), item.get("endian", "little"))
            except OverflowError as exc:
                raise ValueError(f"fields.{item['id']}: 长度无法表示") from exc
        data.extend(_wire_checksum(spec, data, len(data)))
        data.extend(tail)
        return bytes(data)


@dataclass(frozen=True)
class DecodedFrame:
    raw: bytes
    values: dict
    checksum_valid: bool
    error: str | None = None
    spans: dict[str, tuple[int, int]] = field(default_factory=dict)
    field_types: dict[str, str] = field(default_factory=dict)
    labels: dict[str, str] = field(default_factory=dict)
    checksum_span: tuple[int, int] = (0, 0)
    checksum_coverage: tuple[int, int] = (0, 0)


@dataclass(frozen=True)
class DecodedFrameEvent:
    """One parse result shared by views, samples and request/response rules."""
    protocol_id: str
    revision: int
    frame: DecodedFrame

    @property
    def fields(self):
        return self.frame.values

    @property
    def spans(self):
        return self.frame.spans

    @property
    def checksum_valid(self):
        return self.frame.checksum_valid


class _Incomplete(Exception):
    pass


class FrameDecoder:
    def __init__(self, definition: FrameDefinition):
        self.doc = definition.document()
        self.header = _hex(self.doc.get("header_hex", ""), "header_hex")
        self.tail = _hex(self.doc.get("tail_hex", ""), "tail_hex")
        self.buffer = bytearray()
        self.discarded_noise = 0

    @property
    def pending_bytes(self):
        return len(self.buffer)

    def _parse(self):
        cursor, values, declared = len(self.header), {}, []
        spans, intervals, types, labels = {}, {}, {}, {}
        for item in self.doc["fields"]:
            if not _active(item, values):
                continue
            kind, name = item["type"], item["id"]
            width = _width(item)
            if kind == "array" and "count_from" in item:
                count = values.get(item["count_from"])
                if type(count) is not int or not 1 <= count <= 1024:
                    raise ValueError(f"fields.{name}.count_from: 数组数量越界或字段未出现")
                width = count * struct.calcsize(_TYPES[item["item_type"]])
            if kind in ("bytes", "text") and not item.get("width", 0):
                if item["length_from"] not in values:
                    raise ValueError(f"fields.{name}.length_from: 条件长度字段未出现")
                width = values[item["length_from"]]
            if type(width) is not int or width < 0 or cursor + width > MAX_FRAME_BYTES:
                raise ValueError(f"fields.{name}: 目标帧长度越界")
            if cursor + width > len(self.buffer):
                raise _Incomplete
            raw = bytes(self.buffer[cursor:cursor + width])
            begin = cursor
            cursor += width
            spans[name] = width
            intervals[name] = (begin, cursor)
            types[name] = kind
            if declared and any(cursor > end for end in declared):
                raise ValueError("length: 字段越出声明的帧范围")
            endian = item.get("endian", "little")
            if kind in _TYPES:
                value = struct.unpack(("<" if endian == "little" else ">") + _TYPES[kind], raw)[0]
                if not math.isfinite(value):
                    raise ValueError(f"fields.{name}: 非有限数值")
            elif kind in ("id", "length"):
                value = int.from_bytes(raw, endian)
                if kind == "length":
                    value += item.get("adjustment", 0)
                    if not 0 <= value <= MAX_FRAME_BYTES:
                        raise ValueError(f"fields.{name}: 长度越界")
                    if item.get("length_mode") == "frame":
                        if value < cursor:
                            raise ValueError("length: 声明帧长过小")
                        declared.append(value)
            elif kind == "bits":
                value = int.from_bytes(raw, "big")
                for bit in item["bits"]:
                    extracted = (value >> (width * 8 - bit["offset"] - bit["length"])) & ((1 << bit["length"]) - 1)
                    if bit.get("signed") and extracted & (1 << (bit["length"] - 1)):
                        extracted -= 1 << bit["length"]
                    values[name + "." + bit["id"]] = extracted
                    intervals[name + "." + bit["id"]] = (
                        begin + bit["offset"] // 8, begin + (bit["offset"] + bit["length"] + 7) // 8)
                    types[name + "." + bit["id"]] = "int" if bit.get("signed") else "uint"
            elif kind == "array":
                code = _TYPES[item["item_type"]]
                element_width = struct.calcsize(code)
                value = struct.unpack(("<" if endian == "little" else ">") + code * (width // element_width), raw)
                if not all(math.isfinite(element) for element in value):
                    raise ValueError(f"fields.{name}: 数组包含非有限数")
                for index, element in enumerate(value):
                    key = f"{name}[{index}]"
                    values[key] = element
                    intervals[key] = (begin + index * element_width, begin + (index + 1) * element_width)
                    types[key] = item["item_type"]
            elif kind == "bytes":
                value = raw
            else:
                value = raw.decode(item.get("encoding", "utf-8"), errors="strict")
            values[name] = value
            if "enum" in item and str(value) in item["enum"]:
                labels[name] = item["enum"][str(value)]
        for item in self.doc["fields"]:
            if item["type"] == "length" and item["id"] in values and item.get("length_of"):
                target = item["length_of"]
                actual = len(values[target]) if item.get("length_mode") == "count" and target in values else spans.get(target)
                if target not in spans or values[item["id"]] != actual:
                    raise ValueError(f"fields.{item['id']}.length_of: 声明长度与字段大小不符")
        spec = self.doc.get("checksum", {})
        check_size = len(checksum_bytes(spec.get("algorithm", "none"), b""))
        end = cursor + check_size + len(self.tail)
        if end > MAX_FRAME_BYTES or any(value != end for value in declared):
            raise ValueError("length: 声明帧长与实际布局不符")
        if end > len(self.buffer):
            raise _Incomplete
        if self.tail and bytes(self.buffer[end - len(self.tail):end]) != self.tail:
            raise ValueError("tail_hex: 帧尾不符，重新同步")
        valid = bytes(self.buffer[cursor:cursor + check_size]) == _wire_checksum(spec, self.buffer, cursor)
        coverage_end = cursor if spec.get("end", "checksum") == "checksum" else spec["end"]
        return DecodedFrame(bytes(self.buffer[:end]), values, valid,
                            None if valid else "checksum: 校验不匹配", intervals, types, labels,
                            (cursor, cursor + check_size), (spec.get("start", 0), coverage_end)), end

    def feed(self, chunk: bytes) -> list[DecodedFrame]:
        if not isinstance(chunk, bytes):
            raise TypeError("输入必须为bytes")
        frames = []
        for start in range(0, len(chunk), MAX_FRAME_BYTES):
            self.buffer.extend(chunk[start:start + MAX_FRAME_BYTES])
            while self.buffer:
                index = self.buffer.find(self.header)
                if index < 0:
                    keep = min(len(self.buffer), len(self.header) - 1)
                    removed = len(self.buffer) - keep
                    self.discarded_noise += removed
                    del self.buffer[:removed]
                    break
                if index:
                    self.discarded_noise += index
                    del self.buffer[:index]
                try:
                    frame, size = self._parse()
                except _Incomplete:
                    break
                except (ValueError, UnicodeError) as exc:
                    frames.append(DecodedFrame(bytes(self.buffer[:len(self.header)]), {}, False, str(exc)))
                    del self.buffer[:1]
                else:
                    frames.append(frame)
                    del self.buffer[:size]
            if len(self.buffer) > MAX_FRAME_BYTES:
                self.discarded_noise += len(self.buffer)
                self.buffer.clear()
                frames.append(DecodedFrame(b"", {}, False, "buffer: 未完成帧超过16KiB"))
        return frames


def preview_protocol(definition: FrameDefinition, data: bytes) -> dict:
    decoder = FrameDecoder(definition)
    frames = decoder.feed(data[:4096])
    return {"frames": frames[:256], "pending_bytes": decoder.pending_bytes,
            "discarded_noise": decoder.discarded_noise,
            "truncated": len(data) > 4096 or len(frames) > 256}
