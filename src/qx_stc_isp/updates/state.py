"""Version selection, atomic active-version record and the update journal."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .config import UpdatePaths, atomic_write
from .manifest import UpdateError, parse_version

_STAGES = frozenset({"waiting", "installing", "verifying", "done", "failed"})


@dataclass(frozen=True)
class ActiveVersion:
    version: str
    previous: str = ""

    @classmethod
    def read(cls, path: Path) -> "ActiveVersion | None":
        try:
            value = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeDecodeError, ValueError):
            return None
        if not isinstance(value, dict):
            return None
        version, previous = value.get("version"), value.get("previous", "")
        if not isinstance(version, str) or not isinstance(previous, str):
            return None
        try:
            parse_version(version)
            if previous:
                parse_version(previous)
        except ValueError:
            return None
        return cls(version, previous)

    def write(self, path: Path) -> None:
        atomic_write(path, (json.dumps({"version": self.version, "previous": self.previous}) + "\n").encode("utf-8"))


def executable_for(paths: UpdatePaths, version: str) -> Path:
    return paths.version_dir(version) / "Astrspire.exe"


def resolve_start_executable(paths: UpdatePaths, active: ActiveVersion | None = None) -> Path | None:
    """Return the active executable, falling back to the previous or newest installed one."""

    active = active or ActiveVersion.read(paths.active_file)
    if active is not None:
        for version in (active.version, active.previous):
            if not version:
                continue
            candidate = executable_for(paths, version)
            if candidate.is_file():
                return candidate
    # Missing or unreadable active.json (first install, manual upgrade, interrupted
    # write): use the newest directory that actually contains the program.
    installed: list[str] = []
    if paths.versions_dir.is_dir():
        for child in paths.versions_dir.iterdir():
            if not child.is_dir() or not (child / "Astrspire.exe").is_file():
                continue
            try:
                parse_version(child.name)
            except ValueError:
                continue
            installed.append(child.name)
    if not installed:
        return None
    return executable_for(paths, max(installed, key=parse_version))


@dataclass(frozen=True)
class UpdateJournal:
    version: str
    previous: str
    stage: str
    installer: str = ""
    manifest: str = ""
    signature: str = ""
    message: str = ""
    created_utc: str = ""

    @classmethod
    def create(cls, *, version: str, previous: str, installer: str, manifest: str, signature: str) -> "UpdateJournal":
        return cls(
            version=version,
            previous=previous,
            stage="waiting",
            installer=installer,
            manifest=manifest,
            signature=signature,
            created_utc=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )

    @classmethod
    def read(cls, path: Path) -> "UpdateJournal | None":
        try:
            value = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeDecodeError, ValueError):
            return None
        if not isinstance(value, dict) or value.get("stage") not in _STAGES:
            return None
        try:
            journal = cls(
                version=str(value["version"]),
                previous=str(value.get("previous", "")),
                stage=value["stage"],
                installer=str(value.get("installer", "")),
                manifest=str(value.get("manifest", "")),
                signature=str(value.get("signature", "")),
                message=str(value.get("message", "")),
                created_utc=str(value.get("created_utc", "")),
            )
            parse_version(journal.version)
        except (KeyError, ValueError):
            return None
        return journal

    def with_stage(self, stage: str, message: str = "") -> "UpdateJournal":
        return UpdateJournal(
            version=self.version, previous=self.previous, stage=stage, installer=self.installer,
            manifest=self.manifest, signature=self.signature, message=message,
            created_utc=self.created_utc,
        )

    def write(self, path: Path) -> None:
        payload = {
            "version": self.version, "previous": self.previous, "stage": self.stage,
            "installer": self.installer, "manifest": self.manifest,
            "signature": self.signature, "message": self.message,
            "created_utc": self.created_utc,
        }
        atomic_write(path, (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))

    @staticmethod
    def clear(path: Path) -> None:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass


def pending_updates(paths: UpdatePaths) -> UpdateJournal | None:
    journal = UpdateJournal.read(paths.journal_file)
    if journal is None or journal.stage in ("done", "failed"):
        return None
    return journal


def require_executable(paths: UpdatePaths, version: str) -> Path:
    target = executable_for(paths, version)
    if not target.is_file():
        raise UpdateError(f"新版本目录缺少程序文件：{target}")
    return target
