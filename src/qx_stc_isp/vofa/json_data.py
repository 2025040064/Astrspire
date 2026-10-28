"""Bounded inert JSON for our configuration formats; never evaluate code."""

from __future__ import annotations

import json


def load_json_document(content: str | bytes, *, max_bytes: int,
                       max_depth: int = 24, max_nodes: int = 20000):
    if not isinstance(content, (str, bytes)):
        raise TypeError("JSON输入必须为文本或字节")
    if len(content.encode("utf-8") if isinstance(content, str) else content) > max_bytes:
        raise ValueError("JSON输入超过文件大小限制")
    def unique(pairs):
        value = {}
        for key, entry in pairs:
            if key in value:
                raise ValueError(f"JSON字段重复：{key}")
            value[key] = entry
        return value
    def reject_constant(value):
        raise ValueError(f"JSON不接受非有限数：{value}")
    try:
        result = json.loads(content, object_pairs_hook=unique, parse_constant=reject_constant)
    except RecursionError as exc:
        raise ValueError("JSON嵌套过深") from exc
    stack, count = [(result, 0)], 0
    while stack:
        value, depth = stack.pop()
        count += 1
        if depth > max_depth or count > max_nodes:
            raise ValueError("JSON嵌套或节点数量超过限制")
        if isinstance(value, dict):
            stack.extend((entry, depth + 1) for entry in value.values())
        elif isinstance(value, list):
            stack.extend((entry, depth + 1) for entry in value)
    return result
