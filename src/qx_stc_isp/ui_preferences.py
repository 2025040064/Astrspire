"""应用级界面偏好：与工作区文件无关的小 JSON，只读写数值，不接触设备或网络。"""

from __future__ import annotations

import json
from pathlib import Path

from .appearance import normalize_text_scale
from .updates.config import app_data_dir, atomic_write

_FILE_NAME = "ui-preferences.json"
_MAX_BYTES = 4096


def preferences_path() -> Path:
    return app_data_dir() / _FILE_NAME


def load_text_scale(fallback: float = 1.0) -> float:
    """Return the saved UI text scale; any unreadable file falls back to the default."""

    try:
        data = preferences_path().read_bytes()
    except OSError:
        return normalize_text_scale(fallback)
    if len(data) > _MAX_BYTES:
        return normalize_text_scale(fallback)
    try:
        document = json.loads(data.decode("utf-8"))
    except (UnicodeError, ValueError):
        return normalize_text_scale(fallback)
    if not isinstance(document, dict):
        return normalize_text_scale(fallback)
    return normalize_text_scale(document.get("text_scale", fallback))


def save_text_scale(scale: float) -> bool:
    """Persist the scale; a failed write only means the choice lasts this session."""

    payload = json.dumps({"text_scale": normalize_text_scale(scale)},
                         ensure_ascii=False, indent=2).encode("utf-8")
    try:
        atomic_write(preferences_path(), payload)
    except (OSError, ValueError):
        return False
    return True
