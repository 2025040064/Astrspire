"""Stable startup entry: resume an unfinished transaction, then start the active version."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from .config import ASSISTANT_PROTOCOL, UpdatePaths, install_root
from .state import ActiveVersion, pending_updates, resolve_start_executable


def _show_error(message: str) -> None:
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, "Astrspire", 0x10)
    except Exception:
        print(message, file=sys.stderr)


def _resume(paths: UpdatePaths, assistant: Path) -> None:
    subprocess.run(
        [str(assistant), "--resume", "--no-restart", "--root", str(paths.root)],
        close_fds=True, timeout=900,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="AstrspireLauncher")
    parser.add_argument("--root")
    args = parser.parse_args(argv)
    # The launcher sits at the install root, not inside versions\<version>\.
    root = Path(args.root) if args.root else (
        Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else install_root()
    )
    paths = UpdatePaths.for_root(root)
    journal = pending_updates(paths)
    if journal is not None:
        assistant = paths.local_data / "updater" / f"proto{ASSISTANT_PROTOCOL}" / "AstrspireUpdate.exe"
        if assistant.is_file():
            try:
                _resume(paths, assistant)
            except (OSError, subprocess.SubprocessError):
                pass
    executable = resolve_start_executable(paths, ActiveVersion.read(paths.active_file))
    if executable is None:
        _show_error("找不到可启动的 Astrspire 版本；请重新安装。")
        return 1
    subprocess.Popen([str(executable)], cwd=str(executable.parent), close_fds=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
