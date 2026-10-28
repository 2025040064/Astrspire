"""Standalone update assistant.

Runs outside the program directory, waits for every application instance to
exit, installs the signed package, verifies the result and switches the active
version. It never terminates another process and never overwrites a running
installation in place.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from .config import ASSISTANT_PROTOCOL, UpdatePaths, trusted_public_key
from .manifest import UpdateError, UpdateManifest, file_sha256, verify_signature, verify_update, decode_signature
from .process import MUTEX_NAME, wait_mutex_gone
from .state import ActiveVersion, UpdateJournal, pending_updates, require_executable, resolve_start_executable

_HEALTH_TIMEOUT = 60.0
_INSTALL_TIMEOUT = 600.0
_WAIT_SECONDS = 120.0
_MIN_FREE_BYTES = 64 * 1024 * 1024


def _trace(message: str) -> None:
    """Opt-in stderr trace for diagnosing a stuck transaction (never contains secrets)."""

    if os.environ.get("ASTRSPIRE_UPDATE_TRACE"):
        print(f"assistant: {message}", file=sys.stderr, flush=True)


def _log(paths: UpdatePaths, message: str) -> None:
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        paths.logs_dir.mkdir(parents=True, exist_ok=True)
        with (paths.logs_dir / "update.log").open("a", encoding="utf-8") as stream:
            stream.write(f"[{stamp}] {message}\n")
    except OSError:
        pass


def stage_assistant(paths: UpdatePaths, source: Path) -> Path:
    """Copy the assistant once to a location outside the program directory."""

    destination = paths.local_data / "updater" / f"proto{ASSISTANT_PROTOCOL}"
    executable = destination / "AstrspireUpdate.exe"
    if executable.is_file():
        return executable
    source_exe = Path(source) / "AstrspireUpdate.exe"
    if not source_exe.is_file():
        raise UpdateError(f"缺少更新助手：{source_exe}")
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copytree(Path(source), destination, dirs_exist_ok=True)
    return executable


def _run_installer(paths: UpdatePaths, journal: UpdateJournal) -> None:
    installer = Path(journal.installer)
    if not installer.is_file():
        raise UpdateError(f"更新安装包不存在：{installer}")
    try:
        free = shutil.disk_usage(paths.root).free
    except OSError:
        free = None
    if free is not None and free < installer.stat().st_size * 2 + _MIN_FREE_BYTES:
        raise UpdateError("磁盘空间不足，未执行安装；旧版本保持不变。")
    log_file = paths.logs_dir / f"install-{journal.version}.log"
    paths.logs_dir.mkdir(parents=True, exist_ok=True)
    command = [
        str(installer),
        "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
        f"/DIR={paths.root}", f"/LOG={log_file}",
    ]
    _log(paths, f"running installer {installer}")
    process = subprocess.Popen(command, close_fds=True)
    try:
        code = process.wait(timeout=_INSTALL_TIMEOUT)
    except subprocess.TimeoutExpired:
        # 不结束安装进程；事务保留为失败，旧版本继续可用。
        raise UpdateError("安装器未在限定时间内结束；未切换版本。")
    if code != 0:
        raise UpdateError(f"安装器返回错误码 {code}")


def _verify_installed(paths: UpdatePaths, journal: UpdateJournal, public_key: bytes) -> None:
    version_dir = paths.version_dir(journal.version)
    executable = require_executable(paths, journal.version)
    build_manifest = version_dir / "build-manifest.json"
    if not build_manifest.is_file():
        raise UpdateError("安装结果缺少 build-manifest.json")
    try:
        manifest_raw = Path(journal.manifest).read_bytes()
        signature_raw = Path(journal.signature).read_bytes()
        verify_signature(manifest_raw, decode_signature(signature_raw), public_key)
        manifest = UpdateManifest.parse(manifest_raw)
    except (OSError, UpdateError) as exc:
        raise UpdateError(f"无法验证已签名清单：{exc}") from exc
    if manifest.version != journal.version:
        raise UpdateError("待安装版本与清单不一致")
    if file_sha256(build_manifest) != manifest.files_sha256:
        raise UpdateError("安装文件清单哈希与签名清单不一致")
    token = paths.local_data / "health-check.json"
    token.parent.mkdir(parents=True, exist_ok=True)
    token.unlink(missing_ok=True)
    process = subprocess.Popen(
        [str(executable), "--health-check", str(token)], cwd=str(version_dir), close_fds=True,
    )
    try:
        code = process.wait(timeout=_HEALTH_TIMEOUT)
    except subprocess.TimeoutExpired:
        # 不强杀健康检查进程；保留事务与旧版本。
        raise UpdateError("新版本健康检查未正常退出；未切换版本。")
    if code != 0:
        raise UpdateError(f"新版本健康检查失败：退出码 {code}")
    try:
        report = json.loads(token.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise UpdateError("健康检查报告无效") from exc
    if not isinstance(report, dict) or report.get("ok") is not True:
        raise UpdateError("新版本健康检查未通过")
    if report.get("version") != journal.version:
        raise UpdateError("健康检查版本与目标版本不一致")


def _switch_active(paths: UpdatePaths, journal: UpdateJournal) -> None:
    current = ActiveVersion.read(paths.active_file)
    previous = journal.previous or (current.version if current else "")
    ActiveVersion(journal.version, previous).write(paths.active_file)
    for child in paths.versions_dir.iterdir() if paths.versions_dir.is_dir() else ():
        if child.is_dir() and child.name not in (journal.version, previous):
            shutil.rmtree(child, ignore_errors=True)


def _restart(paths: UpdatePaths) -> None:
    launcher = paths.root / "AstrspireLauncher.exe"
    target = launcher if launcher.is_file() else resolve_start_executable(paths)
    if target is None:
        raise UpdateError("找不到可启动的版本")
    subprocess.Popen([str(target)], cwd=str(target.parent), close_fds=True)


def execute(
    paths: UpdatePaths,
    journal: UpdateJournal,
    *,
    wait_seconds: float = _WAIT_SECONDS,
    no_restart: bool = False,
    public_key: bytes | None = None,
) -> int:
    try:
        key = trusted_public_key() if public_key is None else public_key
        _trace(f"execute stage={journal.stage}")
        if journal.stage == "waiting":
            manifest_path = Path(journal.manifest)
            signature_path = Path(journal.signature)
            verified = verify_update(
                manifest_path.read_bytes(), signature_path.read_bytes(), Path(journal.installer), key,
            )
            if verified.manifest.version != journal.version:
                raise UpdateError("待安装版本与清单不一致")
            _trace("manifest and package verified")
            if not wait_mutex_gone(MUTEX_NAME, timeout=wait_seconds):
                raise UpdateError("应用仍在运行，未执行安装；旧版本保持不变。")
            _trace("application mutex released")
            journal = journal.with_stage("installing")
            journal.write(paths.journal_file)
        if journal.stage == "installing":
            _run_installer(paths, journal)
            _trace("installer finished")
            journal = journal.with_stage("verifying")
            journal.write(paths.journal_file)
        if journal.stage == "verifying":
            _verify_installed(paths, journal, key)
            _trace("health check passed")
            _switch_active(paths, journal)
            journal = journal.with_stage("done")
            journal.write(paths.journal_file)
            _log(paths, f"active version switched to {journal.version}")
        if journal.stage == "done":
            if not no_restart:
                _restart(paths)
            return 0
        if journal.stage == "failed":
            return 2
    except (UpdateError, OSError, ValueError) as exc:
        failed = journal.with_stage("failed", str(exc))
        try:
            failed.write(paths.journal_file)
        except OSError:
            pass
        _log(paths, f"update failed: {exc}")
        return 1
    return 1


def _paths(root: str) -> UpdatePaths:
    return UpdatePaths.for_root(Path(root))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="AstrspireUpdate")
    parser.add_argument("--root", required=True)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--installer")
    parser.add_argument("--manifest")
    parser.add_argument("--signature")
    parser.add_argument("--version")
    parser.add_argument("--previous", default="")
    parser.add_argument("--wait-seconds", type=float, default=_WAIT_SECONDS)
    parser.add_argument("--no-restart", action="store_true")
    args = parser.parse_args(argv)
    paths = _paths(args.root)
    if args.resume:
        journal = pending_updates(paths)
        if journal is None:
            return 0
    else:
        missing = [name for name in ("installer", "manifest", "signature", "version") if not getattr(args, name)]
        if missing:
            parser.error("缺少参数：" + ", ".join(missing))
        journal = UpdateJournal.create(
            version=args.version, previous=args.previous, installer=args.installer,
            manifest=args.manifest, signature=args.signature,
        )
        journal.write(paths.journal_file)
        _trace("journal written")
    return execute(paths, journal, wait_seconds=args.wait_seconds, no_restart=args.no_restart)


if __name__ == "__main__":
    raise SystemExit(main())
