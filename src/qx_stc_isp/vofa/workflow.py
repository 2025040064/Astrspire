"""Typed, bounded, event-driven workflow engine with no device dependencies.

Qt owns pacing; this module never creates a thread, reads a file, opens a
transport, evaluates user code, or chooses a connection from current UI focus.
"""
from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field
import copy
import heapq
import json
import math
import time
from types import MappingProxyType
from uuid import uuid4

import numpy as np

from .dsp import StatefulFilter, spectrum, MAX_SAMPLES

MAX_NODES = 128
MAX_EDGES = 256
MAX_DOCUMENT_BYTES = 256 * 1024
MAX_PENDING = 256
MAX_SEND_BYTES = 16384
TYPES = ("any", "number", "series", "text", "bytes", "fields", "spectrum", "bool")
NODE_TYPES = ("Source", "Select", "Math", "String", "Filter", "FFT", "Condition", "Delay", "Template", "Send", "Wait", "Repeat", "End")
_UNSET = object()


def _number(value, label="数值"):
    if isinstance(value, bool) or not isinstance(value, (int, float, np.number)) or not math.isfinite(value):
        raise ValueError(f"{label}需要有限数字")
    if isinstance(value, (int, np.integer)) and abs(int(value)) > 2 ** 53:
        raise ValueError(f"{label}转浮点将丢失整数精度")
    return float(value)


def _bounded_int(value, minimum, maximum, label):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{label}必须为{minimum}–{maximum}的整数")
    return value


def _path(value, path):
    for part in path.split("."):
        if isinstance(value, Mapping) and part in value:
            value = value[part]
        elif isinstance(value, (tuple, list)) and part.isdigit() and int(part) < len(value):
            value = value[int(part)]
        else:
            raise ValueError(f"字段不存在：{path}")
    return value


def _config(node):
    kind, config = node["type"], node.setdefault("config", {})
    if kind in ("Source", "Select"):
        if config.get("output_type", "any" if kind == "Source" else "number") not in TYPES:
            raise ValueError(f"{node['id']}: 输出类型无效")
        if kind == "Select" and (not isinstance(config.get("field"), str) or not config["field"] or len(config["field"]) > 256):
            raise ValueError("Select需要字段路径")
    if kind == "Math":
        if config.get("operation", "multiply") not in ("add", "subtract", "multiply", "divide", "abs", "sqrt", "sin", "cos", "minimum", "maximum", "clamp"):
            raise ValueError("Math运算无效")
        _number(config.get("operand", 1), "operand")
        if config.get("operation") == "divide" and config.get("operand", 1) == 0:
            raise ValueError("除数不能为0")
        if config.get("operation") == "clamp":
            if _number(config.get("minimum", 0)) > _number(config.get("maximum", 1)):
                raise ValueError("clamp下限不能大于上限")
    if kind == "Filter":
        StatefulFilter(config.get("kind", "moving_average"), **{key: config[key] for key in ("window", "alpha", "b", "a") if key in config})
    if kind == "FFT":
        _number(config.get("sample_rate"), "FFT采样率")
        if config["sample_rate"] <= 0:
            raise ValueError("FFT采样率必须大于0")
        if config.get("window", "hann") not in ("rect", "hann", "hamming") or config.get("mode", "magnitude") not in ("magnitude", "power", "psd", "db"):
            raise ValueError("FFT窗口或模式无效")
    if kind == "String" and config.get("operation", "format") not in ("format", "parse_number", "hex", "from_hex"):
        raise ValueError("String运算无效")
    if kind == "Condition" and config.get("operator", "eq") not in ("eq", "ne", "gt", "ge", "lt", "le"):
        raise ValueError("Condition比较运算无效")
    if kind in ("Delay", "Wait"):
        _bounded_int(config.get("milliseconds", 100) if kind == "Delay" else config.get("timeout_ms", 1000),
                     0 if kind == "Delay" else 1, 60_000, kind)
    if kind == "Wait" and (not isinstance(config.get("field"), str) or not config["field"]):
        raise ValueError("Wait需要匹配字段")
    if kind == "Repeat":
        _bounded_int(config.get("count", 1), 1, 100, "重复次数")
        _bounded_int(config.get("interval_ms", 100), 100, 60_000, "重复间隔")
    if kind == "Template":
        if not isinstance(config.get("template_id"), str) or not config["template_id"]:
            raise ValueError("Template需要模板ID")
        _bounded_int(config.get("revision", 1), 1, 2 ** 31 - 1, "模板修订")
        if not isinstance(config.get("parameters", {}), dict) or not isinstance(config.get("bindings", {}), dict):
            raise ValueError("模板参数或绑定格式无效")
        for binding in config.get("bindings", {}).values():
            if not isinstance(binding, str) or not (binding == "value" or binding.startswith("fields.")):
                raise ValueError("模板绑定只接受value或fields.字段路径")


def _output_type(node, input_type):
    kind, config = node["type"], node.get("config", {})
    if kind == "Source":
        return config.get("output_type", "any")
    if kind == "Select":
        return config.get("output_type", "number")
    if kind == "String":
        return {"parse_number": "number", "from_hex": "bytes"}.get(config.get("operation", "format"), "text")
    if kind == "FFT":
        return "spectrum"
    if kind in ("Template", "Send"):
        return "bytes"
    return input_type


def _check_type(value, kind):
    if kind == "any":
        return
    valid = {"number": lambda: not isinstance(value, bool) and isinstance(value, (int, float, np.number)),
             "series": lambda: isinstance(value, (tuple, list, np.ndarray)),
             "text": lambda: isinstance(value, str), "bytes": lambda: isinstance(value, bytes),
             "fields": lambda: isinstance(value, Mapping), "bool": lambda: isinstance(value, bool),
             "spectrum": lambda: hasattr(value, "frequencies") and hasattr(value, "values")}
    if not valid[kind]():
        raise ValueError(f"数据类型不匹配，需要{kind}")


def _freeze(value):
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, np.ndarray):
        array = np.array(value, copy=True)
        array.setflags(write=False)
        return array
    if isinstance(value, bytearray):
        return bytes(value)
    return value


def _same_value(first, second):
    if isinstance(first, np.ndarray) or isinstance(second, np.ndarray):
        try:
            return bool(np.array_equal(first, second, equal_nan=True))
        except TypeError:
            return False
    if isinstance(first, Mapping) and isinstance(second, Mapping):
        return first.keys() == second.keys() and all(_same_value(first[key], second[key]) for key in first)
    if isinstance(first, (tuple, list)) and isinstance(second, (tuple, list)):
        return len(first) == len(second) and all(_same_value(a, b) for a, b in zip(first, second))
    if isinstance(first, float) and isinstance(second, float) and math.isnan(first) and math.isnan(second):
        return True
    return first == second


@dataclass(frozen=True)
class WorkflowInput:
    connection_id: str
    generation: int
    sequence: int
    value: object
    fields: Mapping = field(default_factory=dict)
    valid: bool = True

    def __post_init__(self):
        object.__setattr__(self, "value", _freeze(self.value))
        object.__setattr__(self, "fields", _freeze(self.fields))


@dataclass(frozen=True)
class WorkflowResult:
    node_id: str
    kind: str
    value: object
    connection_id: str
    generation: int
    sequence: int
    revision: int
    message: str = ""

    def __post_init__(self):
        object.__setattr__(self, "value", _freeze(self.value))


class WorkflowProgram:
    """Detached document, validated once and frozen again for each execution."""

    def __init__(self, serialized):
        self._serialized = serialized

    @classmethod
    def compile(cls, document):
        encoded = json.dumps(document, ensure_ascii=False, allow_nan=False)
        if len(encoded.encode("utf-8")) > MAX_DOCUMENT_BYTES:
            raise ValueError("工作流超过256KiB")
        doc = json.loads(encoded)
        if not isinstance(doc, dict) or doc.get("version") != 1 or not isinstance(doc.get("nodes"), list):
            raise ValueError("工作流需要version=1和nodes列表")
        nodes = doc["nodes"]
        if not 1 <= len(nodes) <= MAX_NODES:
            raise ValueError("工作流需要1–128个节点")
        if any(not isinstance(node, dict) for node in nodes):
            raise ValueError("节点必须为对象")
        names = [node.get("id") for node in nodes]
        if any(not isinstance(name, str) or not name or len(name) > 128 for name in names) or len(set(names)) != len(names):
            raise ValueError("节点ID为空、重复或过长")
        if any(node.get("type") not in NODE_TYPES or not isinstance(node.get("config", {}), dict) for node in nodes):
            raise ValueError("节点类型或配置无效")
        by_id = {node["id"]: node for node in nodes}
        for node in nodes:
            _config(node)
        sources = [node["id"] for node in nodes if node["type"] == "Source"]
        if len(sources) != 1:
            raise ValueError("工作流必须有且只有一个Source")
        edges = doc.setdefault("edges", [])
        if not isinstance(edges, list) or len(edges) > MAX_EDGES:
            raise ValueError("连线数量超过256")
        incoming = set()
        links = {name: [] for name in names}
        for edge in edges:
            if not isinstance(edge, dict) or set(edge) != {"from", "out", "to", "in"}:
                raise ValueError("连线需要from/out/to/in")
            if edge["from"] not in links or edge["to"] not in links:
                raise ValueError("连线引用了不存在的节点")
            ports = ("true", "false") if by_id[edge["from"]]["type"] == "Condition" else ("value",)
            if edge["in"] != "value" or edge["out"] not in ports or by_id[edge["from"]]["type"] == "End":
                raise ValueError("节点端口无效")
            if edge["to"] in incoming or edge["to"] in sources:
                raise ValueError("输入端口重复或Source不能接入")
            incoming.add(edge["to"])
            links[edge["from"]].append(edge["to"])
        ordered = []
        pending = [sources[0]]
        visited = set()
        while pending:
            identifier = pending.pop()
            if identifier in visited:
                raise ValueError("工作流不允许循环或重复输入")
            visited.add(identifier)
            ordered.append(identifier)
            pending.extend(links[identifier])
        if len(visited) != len(nodes):
            raise ValueError("工作流含循环或未连接节点")
        types = {}
        for identifier in ordered:
            input_edge = next((edge for edge in edges if edge["to"] == identifier), None)
            input_type = types[input_edge["from"]] if input_edge else "any"
            kind = by_id[identifier]["type"]
            accepted = {"Math": ("number", "series"), "Filter": ("number", "series"),
                        "FFT": ("series",), "Select": ("fields", "series"), "Send": ("bytes",)}.get(kind)
            if accepted and input_type not in accepted + ("any",):
                raise ValueError(f"{identifier}: 输入类型{input_type}不匹配{accepted}")
            types[identifier] = _output_type(by_id[identifier], input_type)
        _bounded_int(doc.setdefault("revision", 1), 1, 2 ** 31 - 1, "工作流修订")
        trigger = doc.setdefault("trigger", {"mode": "manual", "interval_ms": 1000})
        if not isinstance(trigger, dict) or trigger.get("mode") not in ("manual", "timer", "onChange"):
            raise ValueError("触发方式只能为manual/timer/onChange")
        _bounded_int(trigger.setdefault("interval_ms", 1000), 100, 60_000, "触发间隔")
        limits = doc.setdefault("limits", {"per_second": 10, "total": 1000, "seconds": 300})
        if not isinstance(limits, dict):
            raise ValueError("发送限制必须为对象")
        for key, maximum in (("per_second", 10), ("total", 1000), ("seconds", 300)):
            _bounded_int(limits.setdefault(key, maximum), 1, maximum, key)
        return cls(json.dumps(doc, ensure_ascii=False, allow_nan=False))

    def document(self):
        return json.loads(self._serialized)

    @property
    def revision(self):
        return self.document()["revision"]

    @property
    def has_send(self):
        return any(node["type"] == "Send" for node in self.document()["nodes"])


class WorkflowRunner:
    """Explicitly started evaluator. Injected callbacks are the only I/O seam."""

    def __init__(self, *, enqueue=None, template_resolver=None, guard=None,
                 cancel_job=None, clock=time.monotonic, on_result=None, on_state=None):
        self.enqueue = enqueue or (lambda *_: False)
        self.template_resolver = template_resolver
        self.guard = guard or (lambda *_: True)
        self.cancel_job = cancel_job or (lambda *_: None)
        self.clock = clock
        self.on_result = on_result or (lambda *_: None)
        self.on_state = on_state or (lambda *_: None)
        self.active = False
        self.state = "idle"
        self.error = ""
        self.sent_count = 0
        self.job_id = ""
        self._pending = []
        self._waits = []

    def start(self, program, *, connection_id, generation, parameters=None,
              arm_sends=False, source_kind="live", initial_value=_UNSET):
        if self.active:
            raise ValueError("工作流正在运行，请先停止")
        self.program = WorkflowProgram.compile(program.document() if isinstance(program, WorkflowProgram) else program)
        self._document = self.program.document()
        self._nodes = {node["id"]: node for node in self._document["nodes"]}
        if not isinstance(connection_id, str) or not connection_id or type(generation) is not int or generation < 0:
            raise ValueError("需要明确的连接ID和会话代次")
        if self.program.has_send and (not arm_sends or source_kind != "live"):
            raise ValueError("发送需要明确启用且只能使用可写实时连接；回放/预览/SWD为只读")
        self.connection_id, self.generation = str(connection_id), generation
        self.parameters = _freeze(json.loads(json.dumps(parameters or {}, allow_nan=False)))
        self.arm_sends, self.source_kind = bool(arm_sends), source_kind
        self._templates = {}
        for node in self._nodes.values():
            if node["type"] != "Template":
                continue
            config = node.get("config", {})
            if self.template_resolver is None:
                raise ValueError(f"{node['id']}: 未提供模板解析器")
            freeze = getattr(self.template_resolver, "freeze", None)
            if callable(freeze):
                self._templates[node["id"]] = freeze(config["template_id"], config.get("revision", 1))
                if not callable(self._templates[node["id"]]):
                    raise ValueError("模板freeze必须返回独立的编码函数")
            else:
                if config.get("bindings"):
                    raise ValueError("动态参数模板需要freeze接口以冻结模板修订")
                params = dict(self.parameters)
                params.update(config.get("parameters", {}))
                payload = self.template_resolver(config["template_id"], params)
                self._validate_payload(payload)
                self._templates[node["id"]] = lambda _, frozen=bytes(payload): frozen
        self.job_id = "workflow-" + uuid4().hex
        self._pending, self._waits = [], []
        self._filters = {}
        self._counter = self.sent_count = 0
        self.skipped_triggers = 0
        self._recent_sends = deque()
        self._triggered = False
        self._response_message = ""
        self._started = self.clock()
        self._next_tick = self._started + self._document["trigger"]["interval_ms"] / 1000
        self._latest = None
        self.error = ""
        self.active = True
        self._state("running")
        if initial_value is not _UNSET:
            self.feed(WorkflowInput(self.connection_id, generation, 0, initial_value))
        return self.job_id

    def _state(self, state):
        self.state = state
        self.on_state(state)

    def stop(self, reason="用户停止"):
        was_active = self.active
        self.active = False
        self._pending.clear()
        self._waits.clear()
        if was_active:
            try:
                self.cancel_job(self.connection_id, self.job_id)
            except Exception as exc:
                self.error = f"停止后撤销队列失败：{exc}"
            self._state("stopped")

    def _error(self, message):
        self.error = str(message)
        self.stop(self.error)
        self._state("failed")

    def feed(self, event):
        if not self.active or event.connection_id != self.connection_id or not event.valid:
            return
        if event.generation != self.generation:
            self.stop("连接代次已变化")
            return
        if not self.guard(self.connection_id, self.generation):
            self.stop("连接已关闭、目标变化或被ISP占用")
            return
        previous = self._latest
        self._latest = event
        try:
            remaining = []
            for deadline, identifier, value, original in self._waits:
                config = self._nodes[identifier].get("config", {})
                try:
                    match = _path(event.fields, config["field"]) == config.get("equals")
                except ValueError:
                    match = False
                if match:
                    self._response_message = "设备响应匹配"
                    self._emit(identifier, "response", event.fields, event, self._response_message)
                    self._route(identifier, "value", value, original)
                else:
                    remaining.append((deadline, identifier, value, original))
            self._waits = remaining
            mode = self._document["trigger"]["mode"]
            if mode == "manual" and not self._triggered:
                self.trigger()
            elif mode == "onChange" and previous is not None and not _same_value(previous.value, event.value):
                self.trigger()
            self.poll()
        except Exception as exc:
            self._error(exc)

    def trigger(self):
        """Start one frozen graph traversal from the latest matching source event."""
        if not self.active or self._latest is None:
            return False
        if self._pending or self._waits:
            self.skipped_triggers += 1
            return False
        event = self._latest
        source = next(node["id"] for node in self._nodes.values() if node["type"] == "Source")
        self._triggered = True
        self._response_message = ""
        self._schedule(source, event.value, event, self.clock())
        return True

    def _schedule(self, identifier, value, event, due):
        if len(self._pending) + len(self._waits) >= MAX_PENDING:
            raise ValueError("工作流待处理步骤超过256")
        self._counter += 1
        heapq.heappush(self._pending, (due, self._counter, identifier, value, event))

    def poll(self):
        if not self.active:
            return
        try:
            if not self.guard(self.connection_id, self.generation):
                self.stop("连接已关闭、目标变化或被ISP占用")
                return
            now = self.clock()
            if now - self._started >= self._document["limits"]["seconds"]:
                raise ValueError("工作流达到运行时限")
            if any(deadline <= now for deadline, *_ in self._waits):
                raise ValueError("等待设备响应超时；未自动重发")
            if self._document["trigger"]["mode"] == "timer" and now + 1e-9 >= self._next_tick:
                self._next_tick = now + self._document["trigger"]["interval_ms"] / 1000
                self.trigger()
            count = 0
            while self.active and self._pending and self._pending[0][0] <= self.clock():
                _, _, identifier, value, event = heapq.heappop(self._pending)
                node = self._nodes[identifier]
                config, kind = node.get("config", {}), node["type"]
                if kind == "Delay":
                    self._route(identifier, "value", value, event, self.clock() + config.get("milliseconds", 100) / 1000)
                elif kind == "Wait":
                    self._waits.append((self.clock() + config.get("timeout_ms", 1000) / 1000, identifier, value, event))
                    self._state("waiting")
                elif kind == "Repeat":
                    for index in range(config.get("count", 1)):
                        self._route(identifier, "value", value, event, self.clock() + index * config.get("interval_ms", 100) / 1000)
                else:
                    value = self._evaluate(node, value, event)
                    if kind == "End":
                        self._emit(identifier, "result", value, event, self._response_message)
                    elif kind == "Condition":
                        port = "true" if self._condition(config, value) else "false"
                        self._route(identifier, port, value, event)
                    else:
                        self._route(identifier, "value", value, event)
                count += 1
                if count >= MAX_NODES * 2:
                    break
            if not self._pending and not self._waits and self._triggered and self._document["trigger"]["mode"] == "manual":
                self.active = False
                self._state("complete")
            elif self.active and not self._waits:
                self._state("running")
        except Exception as exc:
            self._error(exc)

    def _route(self, identifier, port, value, event, due=None):
        for edge in self._document["edges"]:
            if edge["from"] == identifier and edge["out"] == port:
                self._schedule(edge["to"], value, event, self.clock() if due is None else due)

    def _emit(self, identifier, kind, value, event, message=""):
        self.on_result(WorkflowResult(identifier, kind, value, self.connection_id,
                                      self.generation, event.sequence, self.program.revision, message))

    @staticmethod
    def _validate_payload(payload):
        if not isinstance(payload, bytes) or not 1 <= len(payload) <= MAX_SEND_BYTES:
            raise ValueError("发送模板必须生成1–16384字节")

    def _send(self, node, value, event):
        self._validate_payload(value)
        if not self.arm_sends or self.source_kind != "live" or not self.guard(self.connection_id, self.generation):
            raise ValueError("当前连接无真实发送许可")
        now = self.clock()
        while self._recent_sends and now - self._recent_sends[0] >= 1:
            self._recent_sends.popleft()
        limits = self._document["limits"]
        if self.sent_count >= limits["total"]:
            raise ValueError(f"达到累计{limits['total']}包限制")
        if len(self._recent_sends) >= limits["per_second"]:
            raise ValueError(f"达到每秒{limits['per_second']}包限制")
        if not self.enqueue(self.connection_id, self.generation, bytes(value), self.job_id):
            raise ValueError("发送队列拒绝请求，已停止且不会自动重发")
        self._recent_sends.append(now)
        self.sent_count += 1
        self._emit(node["id"], "queued", value, event, "已进入发送队列，不代表驱动写完或设备确认")
        if self.sent_count >= limits["total"]:
            raise ValueError(f"达到累计{limits['total']}包限制，已停止后续步骤")

    @staticmethod
    def _condition(config, value):
        operand = config.get("operand", config.get("equals", True))
        operator = config.get("operator", "eq")
        operations = {"eq": lambda: value == operand, "ne": lambda: value != operand,
                      "gt": lambda: value > operand, "ge": lambda: value >= operand,
                      "lt": lambda: value < operand, "le": lambda: value <= operand}
        result = operations[operator]()
        if not isinstance(result, (bool, np.bool_)):
            raise ValueError("条件比较只接受单个值，不接受数组")
        return bool(result)

    def _evaluate(self, node, value, event):
        config = node.get("config", {})
        kind = node["type"]
        if kind == "Source":
            _check_type(value, config.get("output_type", "any"))
        elif kind == "Select":
            value = _path(value, config["field"])
            _check_type(value, config.get("output_type", "number"))
        elif kind == "Math":
            scalar = isinstance(value, (int, float, np.number)) and not isinstance(value, bool)
            if scalar:
                value = _number(value)
            else:
                _check_type(value, "series")
                value = np.array(value, dtype=float, copy=True)
                if value.ndim != 1 or len(value) > MAX_SAMPLES:
                    raise ValueError("Math需要最多100000点的一维数据")
            operation, operand = config.get("operation", "multiply"), config.get("operand", 1)
            operators = {"add": lambda x: x + operand, "subtract": lambda x: x - operand,
                         "multiply": lambda x: x * operand, "divide": lambda x: x / operand,
                         "abs": np.abs, "sqrt": np.sqrt, "sin": np.sin, "cos": np.cos,
                         "minimum": lambda x: np.minimum(x, operand), "maximum": lambda x: np.maximum(x, operand),
                         "clamp": lambda x: np.clip(x, config.get("minimum", 0), config.get("maximum", 1))}
            with np.errstate(all="ignore"):
                output = operators[operation](value)
            if np.any(np.isinf(output)) or (not np.any(np.isnan(value)) and np.any(np.isnan(output))):
                raise ValueError("数学结果溢出或超出定义域")
            value = float(output) if scalar else output
        elif kind == "String":
            operation = config.get("operation", "format")
            if operation == "parse_number":
                if not isinstance(value, str) or len(value) > 4096:
                    raise ValueError("解析数字需要最多4096字的文本")
                value = _number(float(value.strip()))
            elif operation == "hex":
                if not isinstance(value, bytes):
                    raise ValueError("HEX格式化需要字节数据")
                value = value.hex(" ").upper()
            elif operation == "from_hex":
                if not isinstance(value, str):
                    raise ValueError("HEX解析需要文本")
                from .transmit import encode_send_payload
                value = encode_send_payload(value, mode="hex")
            else:
                text = config.get("format", "{value}")
                if not isinstance(text, str) or len(text) > 4096:
                    raise ValueError("格式文本最多4096字")
                # Literal replacement only: no attribute/index traversal or evaluation.
                value = text.replace("{value}", str(value))
                if len(value) > 65536:
                    raise ValueError("格式化结果超过64KiB")
        elif kind == "Filter":
            scalar = isinstance(value, (int, float, np.number)) and not isinstance(value, bool)
            if node["id"] not in self._filters:
                self._filters[node["id"]] = StatefulFilter(config.get("kind", "moving_average"),
                    **{key: config[key] for key in ("window", "alpha", "b", "a") if key in config})
            output = self._filters[node["id"]].process([_number(value)] if scalar else value)
            value = float(output[0]) if scalar else output
        elif kind == "FFT":
            value = spectrum(value, sample_rate=config["sample_rate"], window=config.get("window", "hann"), mode=config.get("mode", "magnitude"))
        elif kind == "Template":
            parameters = dict(self.parameters)
            parameters.update(config.get("parameters", {}))
            for name, binding in config.get("bindings", {}).items():
                parameters[name] = value if binding == "value" else _path(event.fields, binding.removeprefix("fields."))
            value = self._templates[node["id"]](parameters)
            self._validate_payload(value)
        elif kind == "Send":
            self._send(node, value, event)
        return value
