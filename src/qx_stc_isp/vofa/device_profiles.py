"""Versioned device descriptions and pure, single-attempt command snapshots.

Independent Qt/Python model inspired by Lissio's profile/operation separation.
No transport, user scripts, file access, clock threads or implicit sends.
"""
from __future__ import annotations

import json
import math
import re
import time
from dataclasses import dataclass, replace

from .protocol import DecodedFrameEvent, FrameDefinition, FrameTemplate, numeric_field_names

_ID = re.compile(r"[a-z][a-z0-9_.-]{0,63}\Z")
MAX_LIBRARY_BYTES = 2 * 1024 * 1024


def _identifier(value, path):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError(f"{path}: 需小写字母开头的1–64字符ID")
    return value


def _name(value, path):
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 80 or any(ord(c) < 32 for c in value):
        raise ValueError(f"{path}: 需1–80个可显示字符")
    return value


def _detached(value, limit=262144):
    try:
        text = json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError(f"无效JSON配置：{exc}") from exc
    if len(text.encode("utf-8")) > limit:
        raise ValueError("配置超过大小限制")
    return text


def _validate_parameter(spec, value):
    name, kind = spec["id"], spec["type"]
    if kind == "integer":
        valid = type(value) is int
    elif kind == "number":
        valid = type(value) in (int, float) and math.isfinite(value)
    elif kind == "boolean":
        valid = type(value) is bool
    elif kind in ("text", "hex"):
        valid = isinstance(value, str) and len(value.encode("utf-8")) <= spec.get("max_length", 16384)
    elif kind == "array":
        valid = isinstance(value, list) and 1 <= len(value) <= spec.get("max_length", 1024)
        valid = valid and all(type(item) in (int, float) and math.isfinite(item) for item in value)
    else:
        valid = False
    if not valid:
        raise ValueError(f"parameters.{name}: 参数类型或长度不符")
    if kind in ("integer", "number"):
        if "minimum" in spec and value < spec["minimum"] or "maximum" in spec and value > spec["maximum"]:
            raise ValueError(f"parameters.{name}: 超出允许范围")
    if "choices" in spec and value not in spec["choices"]:
        raise ValueError(f"parameters.{name}: 不属于允许值")
    return value


@dataclass(frozen=True)
class DeviceProfile:
    serialized: str

    @classmethod
    def from_document(cls, value):
        document = json.loads(_detached(value))
        required = {"schema_version", "id", "name", "revision", "connection", "definition", "channels", "operations"}
        if not isinstance(document, dict) or set(document) != required or document["schema_version"] != 1:
            raise ValueError("设备档案需要schema_version=1及完整字段")
        _identifier(document["id"], "id")
        _name(document["name"], "name")
        if type(document["revision"]) is not int or not 1 <= document["revision"] <= 1000000:
            raise ValueError("revision: 需正整数修订号")
        connection = document["connection"]
        if (not isinstance(connection, dict) or set(connection) - {"kinds", "baudrate"}
                or not isinstance(connection.get("kinds"), list) or not connection["kinds"]
                or len(set(connection["kinds"])) != len(connection["kinds"])
                or any(kind not in ("serial", "tcp_client", "tcp_server", "udp", "offline", "swd") for kind in connection["kinds"])):
            raise ValueError("connection: 连接类型无效")
        if "baudrate" in connection and (type(connection["baudrate"]) is not int or not 1 <= connection["baudrate"] <= 4000000):
            raise ValueError("connection.baudrate: 波特率无效")
        definition = FrameDefinition.from_document(document["definition"])
        names = numeric_field_names(definition)
        channels = document["channels"]
        if (not isinstance(channels, list) or len(channels) > 16 or any(not isinstance(c, str) or c not in names for c in channels)
                or len(set(channels)) != len(channels)):
            raise ValueError("channels: 需0–16个唯一数值路径")
        operations = document["operations"]
        if not isinstance(operations, list) or len(operations) > 64:
            raise ValueError("operations: 最多64个操作")
        seen = set()
        for operation in operations:
            if (not isinstance(operation, dict) or set(operation) - {"id", "name", "definition", "parameters", "timeout_ms", "response"}
                    or not {"id", "name", "definition", "parameters"} <= set(operation)):
                raise ValueError("operations: 操作格式无效")
            identifier = _identifier(operation["id"], "operations.id")
            if identifier in seen:
                raise ValueError("operations.id: 操作重复")
            seen.add(identifier)
            _name(operation["name"], "operations.name")
            send_definition = FrameDefinition.from_document(operation["definition"])
            field_names = {item["id"] for item in send_definition.document()["fields"]}
            parameters = operation["parameters"]
            if not isinstance(parameters, list) or len(parameters) > 64:
                raise ValueError("parameters: 最多64个参数")
            param_ids = set()
            for parameter in parameters:
                if (not isinstance(parameter, dict) or set(parameter) - {"id", "type", "default", "minimum", "maximum", "choices", "max_length"}
                        or not {"id", "type"} <= set(parameter) or parameter["id"] not in field_names
                        or parameter["id"] in param_ids
                        or parameter["type"] not in ("integer", "number", "boolean", "text", "hex", "array")):
                    raise ValueError("parameters: 参数结构无效或字段不存在")
                param_ids.add(parameter["id"])
                for bound in ("minimum", "maximum"):
                    if bound in parameter and (type(parameter[bound]) not in (int, float) or not math.isfinite(parameter[bound])):
                        raise ValueError(f"parameters.{bound}: 需有限数值")
                if parameter.get("minimum", -math.inf) > parameter.get("maximum", math.inf):
                    raise ValueError("parameters: 最小值大于最大值")
                if "max_length" in parameter and (type(parameter["max_length"]) is not int or not 1 <= parameter["max_length"] <= 16384):
                    raise ValueError("parameters.max_length: 超出范围")
                if "choices" in parameter and (not isinstance(parameter["choices"], list) or not 1 <= len(parameter["choices"]) <= 256):
                    raise ValueError("parameters.choices: 需1–256个候选值")
                if "default" in parameter:
                    _validate_parameter(parameter, parameter["default"])
            timeout = operation.get("timeout_ms", 1000)
            if type(timeout) is not int or not 1 <= timeout <= 300000:
                raise ValueError("timeout_ms: 需1–300000毫秒")
            response = operation.get("response")
            if response is not None:
                available = {item["id"] for item in definition.document()["fields"]} | names
                if (not isinstance(response, dict) or set(response) != {"fields"}
                        or not isinstance(response["fields"], dict) or not 1 <= len(response["fields"]) <= 64
                        or any(key not in available or type(expected) not in (int, float, str, bool)
                               for key, expected in response["fields"].items())):
                    raise ValueError("response.fields: 需要已有字段的明确匹配值")
        return cls(_detached(document))

    def document(self):
        return json.loads(self.serialized)

    @property
    def identifier(self):
        return self.document()["id"]

    @property
    def revision(self):
        return self.document()["revision"]

    @property
    def name(self):
        return self.document()["name"]

    @property
    def definition(self):
        return FrameDefinition.from_document(self.document()["definition"])

    def operation(self, identifier):
        operation = next((item for item in self.document()["operations"] if item["id"] == identifier), None)
        if operation is None:
            raise ValueError("操作引用不存在，未生成发送字节")
        return operation


@dataclass(frozen=True)
class ResponseMatcher:
    profile_id: str
    revision: int
    serialized_fields: str

    def matches(self, event: DecodedFrameEvent):
        return (isinstance(event, DecodedFrameEvent) and event.protocol_id == self.profile_id
                and event.revision == self.revision and event.checksum_valid
                and all(key in event.fields and type(event.fields[key]) is type(expected) and event.fields[key] == expected
                        for key, expected in json.loads(self.serialized_fields).items()))


class ProfileDecoder:
    """Reuse the same frame/telemetry decode with a pinned profile revision."""
    def __init__(self, profile: DeviceProfile):
        from .plugins import DeclarativeDecoder, EngineManifest
        self.profile = DeviceProfile.from_document(profile.document())
        doc = self.profile.document()
        self._decoder = DeclarativeDecoder(EngineManifest.from_document({"schema_version": 2,
            "id": doc["id"], "name": doc["name"], "mode": "framed",
            "options": {"definition": doc["definition"], "channels": doc["channels"]}}))

    def reset(self):
        self._decoder.reset()

    def feed(self, chunk):
        events = self._decoder.feed(chunk)
        self._decoder.take_decoded_frames()
        return [replace(event, frame=replace(event.frame, revision=self.profile.revision))
                if event.frame is not None else event for event in events]


@dataclass(frozen=True)
class FrozenOperation:
    profile_id: str
    revision: int
    operation_id: str
    payload: bytes
    serialized_parameters: str
    timeout_ms: int
    matcher: ResponseMatcher | None


class ProfileLibrary:
    def __init__(self):
        self._profiles = {}

    @classmethod
    def from_document(cls, document):
        document = json.loads(_detached(document, MAX_LIBRARY_BYTES))
        if (not isinstance(document, dict) or set(document) != {"schema_version", "profiles"}
                or document["schema_version"] != 1 or not isinstance(document["profiles"], list)):
            raise ValueError("档案库格式无效")
        library = cls()
        for item in document["profiles"]:
            library.add(DeviceProfile.from_document(item))
        return library

    @property
    def profiles(self):
        return tuple(self._profiles.values())

    def document(self):
        return {"schema_version": 1, "profiles": [profile.document() for profile in self.profiles]}

    def add(self, profile):
        canonical = DeviceProfile.from_document(profile.document())
        key = canonical.identifier, canonical.revision
        if key in self._profiles:
            if self._profiles[key].serialized != canonical.serialized:
                raise ValueError("已有修订不可覆盖；请创建新修订")
            return
        if len(self._profiles) >= 128:
            raise ValueError("档案库最多128个修订")
        candidate = self.document()
        candidate["profiles"].append(canonical.document())
        _detached(candidate, MAX_LIBRARY_BYTES)
        self._profiles[key] = canonical

    def get(self, profile_id, revision):
        try:
            return self._profiles[profile_id, revision]
        except KeyError as exc:
            raise ValueError("设备档案或指定修订不存在") from exc

    def delete(self, profile_id, revision):
        self.get(profile_id, revision)
        del self._profiles[profile_id, revision]

    def freeze_operation(self, profile_id, operation_id, revision, params):
        profile = self.get(profile_id, revision)
        operation = profile.operation(operation_id)
        if not isinstance(params, dict) or set(params) - {parameter["id"] for parameter in operation["parameters"]}:
            raise ValueError("parameters: 包含未知参数")
        values = {}
        for spec in operation["parameters"]:
            if spec["id"] not in params and "default" not in spec:
                raise ValueError(f"parameters.{spec['id']}: 缺少必需参数")
            values[spec["id"]] = _validate_parameter(spec, params.get(spec["id"], spec.get("default")))
        payload = FrameTemplate(FrameDefinition.from_document(operation["definition"])).encode(values)
        response = operation.get("response")
        matcher = ResponseMatcher(profile_id, revision, _detached(response["fields"])) if response else None
        return FrozenOperation(profile_id, revision, operation_id, payload, _detached(values), operation.get("timeout_ms", 1000), matcher)

    def encode_operation(self, profile_id, operation_id, revision, params):
        return self.freeze_operation(profile_id, operation_id, revision, params).payload


class RequestResponseTask:
    """Caller owns routing and time. Every task emits its bytes at most once."""
    def __init__(self, operation: FrozenOperation):
        self.operation = operation
        self.state = "ready"
        self.error = None
        self.deadline = None

    def start(self):
        if self.state != "ready":
            raise ValueError("任务已启动，禁止自动重发")
        self.state = "awaiting_write"
        return self.operation.payload

    def written(self, count: int | None, *, now=None):
        if self.state != "awaiting_write":
            return
        if type(count) is not int or count != len(self.operation.payload):
            self.fail("部分或未知写入，已停止；不会自动重试")
        elif self.operation.matcher is None:
            self.state = "complete"
        else:
            self.state = "waiting"
            self.deadline = (time.monotonic() if now is None else now) + self.operation.timeout_ms / 1000

    def accept(self, event):
        if self.state == "waiting" and self.operation.matcher.matches(event):
            self.state = "complete"
            return True
        return False

    def poll(self, *, now=None):
        if self.state == "waiting" and (time.monotonic() if now is None else now) >= self.deadline:
            self.fail("等待设备应答超时，未自动重发")
        return self.state

    def fail(self, message):
        if self.state not in ("complete", "cancelled", "failed"):
            self.state, self.error = "failed", str(message)

    def cancel(self):
        if self.state not in ("complete", "failed"):
            self.state, self.error = "cancelled", "已取消后续等待；已写入字节不可撤销"
