"""Explicit manual-send formatting; this module never accesses a device."""

from __future__ import annotations

import re

from .serialweb import encode_text, normalize_encoding_label

MAX_SEND_BYTES = 16_384
_HEX_TOKEN = re.compile(r"(?:(?:0x|\\x)?[0-9a-f]{2})+", re.IGNORECASE)
_ENDINGS = {"none": b"", "lf": b"\n", "crlf": b"\r\n", "cr": b"\r", "tab": b"\t"}


class TransmitError(ValueError):
    """A user send request is invalid or cannot currently be queued."""


def encode_send_payload(
    text: str,
    *,
    mode: str = "text",
    line_ending: str = "none",
    max_bytes: int = MAX_SEND_BYTES,
    encoding: str = "utf-8",
) -> bytes:
    """Encode one user-confirmed text/HEX request with an explicit ending.

    HEX accepts byte pairs with whitespace/comma/colon/semicolon/pipe separators
    and explicit 0x or backslash-x prefixes. Invalid tokens are never skipped.
    Text endings use its codec; HEX endings are appended as literal bytes.
    """

    if not isinstance(text, str):
        raise TypeError("发送内容必须为字符串")
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
        raise ValueError("发送字节上限必须为正整数")
    if not isinstance(mode, str) or mode.casefold() not in {"text", "hex"}:
        raise TransmitError("发送格式必须为 text 或 hex")
    if not isinstance(line_ending, str) or line_ending.casefold() not in _ENDINGS:
        raise TransmitError("结束符必须为 none、LF、CRLF、CR 或 TAB")
    suffix = _ENDINGS[line_ending.casefold()]
    if mode.casefold() == "text":
        if len(text) > max_bytes:
            raise TransmitError(f"单次发送不能超过 {max_bytes} 字节")
        try:
            codec = normalize_encoding_label(encoding)
            text.encode(codec, errors="strict")
            payload = encode_text(text, codec)
            suffix = suffix.decode("ascii").encode(codec)
        except (UnicodeEncodeError, ValueError) as exc:
            raise TransmitError(f"发送文本无法无损编码为 {encoding}") from exc
    else:
        # Bound parsing input as well as output; do not scan arbitrarily large
        # whitespace-only or otherwise invalid input in a GUI event handler.
        if len(text) > max_bytes * 8 + 1024:
            raise TransmitError("HEX 输入超过单次发送的解析上限")
        tokens = re.split(r"[\s,;:|]+", text.strip())
        if not tokens or any(_HEX_TOKEN.fullmatch(token) is None for token in tokens):
            raise TransmitError("HEX 必须使用完整的十六进制字节，例如 01 0A FF")
        payload = bytes.fromhex(re.sub(r"0x|\\x", "", " ".join(tokens), flags=re.IGNORECASE))
    if not payload and not suffix:
        raise TransmitError("发送内容不能为空")
    result = payload + suffix
    if len(result) > max_bytes:
        raise TransmitError(f"单次发送（含结束符）不能超过 {max_bytes} 字节")
    return result
