"""Signed update manifests. Signature verification always precedes parsing."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .config import APP_ID, CHANNEL, PLATFORM, asset_name

_VERSION_RE = re.compile(r"^(0|[1-9]\d{0,5})\.(0|[1-9]\d{0,5})\.(0|[1-9]\d{0,5})$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_MANIFEST_FIELDS = frozenset({
    "app_id", "channel", "version", "platform", "asset", "size",
    "sha256", "files_sha256", "min_assistant", "notes",
})
_MAX_PACKAGE_BYTES = 1024 * 1024 * 1024
_MAX_ASSISTANT = 100


class UpdateError(Exception):
    """A verification, download or transaction failure that must be shown to the user."""


def parse_version(text: str) -> tuple[int, int, int]:
    if not isinstance(text, str):
        raise ValueError("版本号必须是文本")
    match = _VERSION_RE.match(text)
    if match is None:
        raise ValueError(f"版本号必须为三段数字：{text!r}")
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def compare_versions(left: str, right: str) -> int:
    """Return -1/0/1 comparing numeric version triples, never strings."""

    a, b = parse_version(left), parse_version(right)
    return (a > b) - (a < b)


@dataclass(frozen=True)
class UpdateManifest:
    app_id: str
    channel: str
    version: str
    platform: str
    asset: str
    size: int
    sha256: str
    files_sha256: str
    min_assistant: int
    notes: str = ""

    @classmethod
    def parse(cls, raw: bytes) -> "UpdateManifest":
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise UpdateError("更新清单不是有效 UTF-8 JSON") from exc
        if not isinstance(value, dict) or set(value) != _MANIFEST_FIELDS:
            raise UpdateError("更新清单字段不完整或包含未知字段")
        if type(value["min_assistant"]) is not int or not 1 <= value["min_assistant"] <= _MAX_ASSISTANT:
            raise UpdateError("更新清单助手版本无效")
        if not isinstance(value["notes"], str) or len(value["notes"]) > 20000:
            raise UpdateError("更新说明无效")
        manifest = cls(**value)
        manifest.validate()
        return manifest

    def validate(self) -> None:
        if self.app_id != APP_ID:
            raise UpdateError(f"更新清单目标应用不匹配：{self.app_id}")
        if self.channel != CHANNEL:
            raise UpdateError(f"更新清单通道不是 {CHANNEL}")
        if self.platform != PLATFORM:
            raise UpdateError(f"更新清单平台不是 {PLATFORM}")
        try:
            parse_version(self.version)
        except ValueError as exc:
            raise UpdateError(str(exc)) from exc
        if self.asset != asset_name(self.version):
            raise UpdateError("更新清单资产名与版本不一致")
        if type(self.size) is not int or not 1 <= self.size <= _MAX_PACKAGE_BYTES:
            raise UpdateError("更新清单包大小无效")
        if not _SHA256_RE.match(self.sha256) or not _SHA256_RE.match(self.files_sha256):
            raise UpdateError("更新清单哈希必须为小写 SHA-256")

    def canonical_bytes(self) -> bytes:
        """The byte sequence the publisher signs."""

        document = {name: getattr(self, name) for name in _MANIFEST_FIELDS}
        return json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


@dataclass(frozen=True)
class VerifiedUpdate:
    """Only :func:`verify_update` constructs this; install entry points require it."""

    manifest: UpdateManifest
    package: Path
    verified_at: str


def verify_signature(raw: bytes, signature: bytes, public_key: bytes) -> None:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    if not public_key:
        raise UpdateError("未配置可信更新公钥")
    if len(public_key) != 32:
        raise UpdateError("可信更新公钥长度无效")
    if not isinstance(signature, bytes) or len(signature) != 64:
        raise UpdateError("更新签名长度无效")
    try:
        Ed25519PublicKey.from_public_bytes(public_key).verify(signature, raw)
    except (InvalidSignature, ValueError) as exc:
        raise UpdateError("更新清单签名无效") from exc


def decode_signature(raw: bytes) -> bytes:
    try:
        signature = base64.b64decode(raw.strip(), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise UpdateError("更新签名不是有效 base64") from exc
    if len(signature) != 64:
        raise UpdateError("更新签名长度无效")
    return signature


def file_sha256(path: Path, *, chunk: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while block := stream.read(chunk):
            digest.update(block)
    return digest.hexdigest()


def verify_update(
    manifest_raw: bytes,
    signature_raw: bytes,
    package: Path,
    public_key: bytes,
) -> VerifiedUpdate:
    """Fixed verification order: signature, structure, size, SHA-256."""

    signature = decode_signature(signature_raw)
    verify_signature(manifest_raw, signature, public_key)
    manifest = UpdateManifest.parse(manifest_raw)
    package = Path(package)
    try:
        size = package.stat().st_size
    except OSError as exc:
        raise UpdateError(f"无法读取更新安装包：{exc}") from exc
    if size != manifest.size:
        raise UpdateError("更新安装包大小与清单不一致")
    if file_sha256(package) != manifest.sha256:
        raise UpdateError("更新安装包 SHA-256 与清单不一致")
    return VerifiedUpdate(
        manifest=manifest,
        package=package,
        verified_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
