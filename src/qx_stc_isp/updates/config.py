"""Update identity, trusted key, filesystem locations and persisted preferences.

Nothing here performs network access or changes the installed application.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from dataclasses import asdict, dataclass, fields
from pathlib import Path

APP_ID = "com.astrspire.serial-workbench"
APP_NAME = "Astrspire"
RELEASE_REPO = "2025040064/Astrspire"
LATEST_RELEASE_URL = f"https://api.github.com/repos/{RELEASE_REPO}/releases/latest"
RELEASE_DOWNLOAD_PREFIX = f"https://github.com/{RELEASE_REPO}/releases/download/"
CHANNEL = "stable"
PLATFORM = "win-x64"
MANIFEST_ASSET = "update-win-x64.json"
SIGNATURE_ASSET = "update-win-x64.sig"

# 发布方在正式构建前填入 Ed25519 公钥（base64，32 字节）。私钥不得进入本仓库。
TRUSTED_PUBLIC_KEY_B64 = "STPSC8E2kOS5T8OVVEkPXkUndLVk0Zv7WJ/l87Q3tNs="
# 助手协议版本；清单要求更高时转人工安装。
ASSISTANT_PROTOCOL = 1

_CHECK_INTERVAL_SECONDS = 24 * 3600


def asset_name(version: str) -> str:
    return f"{APP_NAME}-{version}-win-x64-Setup.exe"


def source_name(version: str) -> str:
    return f"{APP_NAME}-{version}-win-x64-Source.zip"


def trusted_public_key() -> bytes:
    """Decode the built-in key; empty string means the build is unconfigured."""

    import base64
    import binascii

    if not TRUSTED_PUBLIC_KEY_B64:
        return b""
    try:
        key = base64.b64decode(TRUSTED_PUBLIC_KEY_B64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("内置更新公钥不是有效 base64") from exc
    if len(key) != 32:
        raise ValueError("内置更新公钥长度必须为 32 字节")
    return key


def _override(name: str, default: Path) -> Path:
    value = os.environ.get(name)
    return Path(value) if value else default


def app_data_dir() -> Path:
    """User data lives with the installation on F:, never under the C: profile."""

    return _override("ASTRSPIRE_APPDATA", install_root() / "data" / "app")


def local_data_dir() -> Path:
    return _override("ASTRSPIRE_LOCALAPPDATA", install_root() / "data" / "local")


def install_root() -> Path:
    override = os.environ.get("ASTRSPIRE_INSTALL_ROOT")
    if override:
        return Path(override)
    if getattr(sys, "frozen", False):
        executable = Path(sys.executable).resolve()
        # Only an installed version has two extra levels; portable executables
        # and the root launcher must not walk outside their own directory.
        version_dir = executable.parent
        if version_dir.parent.name.casefold() == "versions":
            return version_dir.parent.parent
        return version_dir
    return Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class UpdatePaths:
    root: Path
    app_data: Path
    local_data: Path

    @classmethod
    def for_root(cls, root: Path) -> "UpdatePaths":
        root = Path(root)
        return cls(
            root,
            _override("ASTRSPIRE_APPDATA", root / "data" / "app"),
            _override("ASTRSPIRE_LOCALAPPDATA", root / "data" / "local"),
        )

    @classmethod
    def current(cls) -> "UpdatePaths":
        return cls.for_root(install_root())

    @property
    def versions_dir(self) -> Path:
        return self.root / "versions"

    @property
    def active_file(self) -> Path:
        return self.root / "active.json"

    @property
    def journal_file(self) -> Path:
        return self.local_data / "update-journal.json"

    @property
    def downloads_dir(self) -> Path:
        return self.local_data / "updates"

    @property
    def settings_file(self) -> Path:
        return self.app_data / "settings.json"

    @property
    def recovery_dir(self) -> Path:
        return self.app_data / "recovery"

    @property
    def logs_dir(self) -> Path:
        return self.local_data / "logs"

    def version_dir(self, version: str) -> Path:
        return self.versions_dir / version

    def download_dir(self, version: str) -> Path:
        return self.downloads_dir / version


def atomic_write(path: Path, data: bytes) -> None:
    """Replace ``path`` with ``data`` without exposing a partial file."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", suffix=".tmp", prefix=f".{path.name}.", dir=path.parent, delete=False,
        ) as stream:
            temporary = stream.name
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and os.path.exists(temporary):
            os.unlink(temporary)


@dataclass
class UpdatePreferences:
    auto_check: bool = True
    auto_download: bool = False
    skipped_version: str = ""
    etag: str = ""
    last_check_utc: str = ""
    retry_after_utc: str = ""

    @classmethod
    def load(cls, path: Path) -> "UpdatePreferences":
        try:
            raw = path.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError):
            return cls()
        try:
            value = json.loads(raw)
        except ValueError:
            return cls()
        if not isinstance(value, dict):
            return cls()
        known = {item.name for item in fields(cls)}
        defaults = cls()
        clean = {}
        for name in known:
            item = value.get(name)
            default = getattr(defaults, name)
            if type(item) is type(default):
                clean[name] = item
        return cls(**clean)

    def save(self, path: Path) -> None:
        atomic_write(path, (json.dumps(asdict(self), ensure_ascii=False, indent=2) + "\n").encode("utf-8"))

    def checked_spent(self, now_iso: str, *, interval: int = _CHECK_INTERVAL_SECONDS) -> bool:
        """Return True when the last automatic check is younger than the interval."""

        from datetime import datetime, timezone

        if not self.last_check_utc:
            return False
        try:
            last = datetime.fromisoformat(self.last_check_utc)
            current = datetime.fromisoformat(now_iso)
        except ValueError:
            return False
        if last.tzinfo is None or current.tzinfo is None:
            return False
        return (current - last).total_seconds() < interval
