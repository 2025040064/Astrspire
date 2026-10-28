"""Qt bridge: checks and downloads on worker threads, then hands off to the assistant."""

from __future__ import annotations

import subprocess
import threading
import time
from datetime import datetime, timezone

from PySide6.QtCore import QObject, QThread, Signal, Slot

from .. import __version__
from .assistant import stage_assistant
from .checker import CheckResult, UpdateChecker
from .config import MANIFEST_ASSET, SIGNATURE_ASSET, UpdatePaths, UpdatePreferences, trusted_public_key
from .manifest import UpdateError, UpdateManifest, VerifiedUpdate
from .state import ActiveVersion, UpdateJournal

_DETACHED_PROCESS = 0x00000008
_CREATE_NEW_PROCESS_GROUP = 0x00000200


class _CheckWorker(QObject):
    finished = Signal(object)

    def __init__(self, checker: UpdateChecker, manual: bool) -> None:
        super().__init__()
        self._checker = checker
        self._manual = manual

    @Slot()
    def run(self) -> None:
        try:
            result = self._checker.check(manual=self._manual)
        except Exception as exc:  # 网络/解析异常不能杀掉界面
            result = CheckResult("error", f"检查更新失败：{exc}")
        self.finished.emit(result)


class _DownloadWorker(QObject):
    progress = Signal(int, int)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, checker: UpdateChecker, manifest: UpdateManifest, url: str, cancel: threading.Event) -> None:
        super().__init__()
        self._checker = checker
        self._manifest = manifest
        self._url = url
        self._cancel = cancel

    @Slot()
    def run(self) -> None:
        try:
            update = self._checker.download(
                self._manifest, self._url,
                on_progress=lambda done, total: self.progress.emit(done, total or self._manifest.size),
                cancel=self._cancel,
            )
        except Exception as exc:
            self.failed.emit(str(exc))
            return
        self.finished.emit(update)


class UpdateService(QObject):
    check_finished = Signal(object)
    download_progress = Signal(int, int)
    download_finished = Signal(object)
    download_failed = Signal(str)

    def __init__(self, paths: UpdatePaths | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.paths = paths or UpdatePaths.current()
        self.preferences = UpdatePreferences.load(self.paths.settings_file)
        self.current_version = __version__
        self.pending: VerifiedUpdate | None = None
        self.snapshot = None  # set by the main window: callable() -> bool
        self._threads: list[QThread] = []
        self._workers: list[QObject] = []
        self._retired: list[tuple[QObject, QThread]] = []
        self._cancel = threading.Event()

    # ---- preferences ---------------------------------------------------
    def save_preferences(self) -> None:
        try:
            self.preferences.save(self.paths.settings_file)
        except OSError:
            pass

    def should_auto_check(self) -> bool:
        if not self.preferences.auto_check:
            return False
        return not self.preferences.checked_spent(datetime.now(timezone.utc).isoformat(timespec="seconds"))

    # ---- network operations --------------------------------------------
    def check(self, *, manual: bool = True) -> None:
        checker = UpdateChecker(
            self.paths, self.current_version, preferences=self.preferences,
            public_key=_trusted_key_or_empty(),
        )
        worker = _CheckWorker(checker, manual)
        worker.finished.connect(self._on_checked)
        self._start(worker)

    def download(self, manifest: UpdateManifest, asset_url: str) -> None:
        self._cancel = threading.Event()
        checker = UpdateChecker(
            self.paths, self.current_version, preferences=self.preferences,
            public_key=_trusted_key_or_empty(),
        )
        worker = _DownloadWorker(checker, manifest, asset_url, self._cancel)
        worker.progress.connect(self.download_progress)
        worker.finished.connect(self._on_downloaded)
        worker.failed.connect(self.download_failed)
        self._start(worker, failure=worker.failed)

    def cancel_download(self) -> None:
        self._cancel.set()

    def idle(self) -> bool:
        return not self._threads

    def wait_idle(self, timeout_ms: int = 2000) -> bool:
        """Give short checks/downloads a chance to end before process teardown."""

        deadline = time.monotonic() + timeout_ms / 1000
        for thread in list(self._threads):
            thread.wait(max(0, int((deadline - time.monotonic()) * 1000)))
        return self.idle()

    @Slot(object)
    def _on_checked(self, result: CheckResult) -> None:
        self.save_preferences()
        self.check_finished.emit(result)

    @Slot(object)
    def _on_downloaded(self, update: VerifiedUpdate) -> None:
        self.download_finished.emit(update)

    def _start(self, worker: QObject, *, failure: Signal | None = None) -> None:
        thread = QThread()
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(thread.quit)
        if failure is not None:
            failure.connect(thread.quit)

        def drop() -> None:
            # Keep the finished thread objects alive; deleting a QThread while its
            # OS thread is still winding down crashes Qt. References retire with
            # the service instead.
            if worker in self._workers:
                self._workers.remove(worker)
            if thread in self._threads:
                self._threads.remove(thread)
            self._retired.append((worker, thread))
            del self._retired[:-8]

        thread.finished.connect(drop)
        self._threads.append(thread)
        self._workers.append(worker)
        thread.start()

    # ---- install hand-off ----------------------------------------------
    def schedule(self, update: VerifiedUpdate) -> None:
        self.pending = update

    def cancel_schedule(self) -> None:
        self.pending = None

    def _capture(self) -> None:
        if self.snapshot is None or not self.snapshot():
            raise UpdateError("已取消更新：工作区未保存。")

    def begin_update(self, update: VerifiedUpdate, *, restart_now: bool, window) -> bool:
        """Snapshot, normal exit, then start the assistant. Returns False when refused."""

        manifest = update.manifest
        self._capture()
        if not window.close():
            raise UpdateError("应用未能正常退出，更新未开始；可稍后重试。")
        self._start_assistant(update, restart_now=restart_now)
        self.pending = None
        return True

    def install_on_exit(self, update: VerifiedUpdate) -> bool:
        """Called from closeEvent for a scheduled update. Never closes the window itself."""

        self._capture()
        self._start_assistant(update, restart_now=False)
        self.pending = None
        return True

    def _start_assistant(self, update: VerifiedUpdate, *, restart_now: bool) -> None:
        manifest = update.manifest
        current = ActiveVersion.read(self.paths.active_file)
        directory = self.paths.download_dir(manifest.version)
        journal = UpdateJournal.create(
            version=manifest.version,
            previous=current.version if current else "",
            installer=str(update.package),
            manifest=str(directory / MANIFEST_ASSET),
            signature=str(directory / SIGNATURE_ASSET),
        )
        journal.write(self.paths.journal_file)
        try:
            assistant = stage_assistant(self.paths, self.paths.root / "assistant")
            command = [
                str(assistant), "--root", str(self.paths.root),
                "--installer", str(update.package),
                "--manifest", journal.manifest, "--signature", journal.signature,
                "--version", manifest.version, "--previous", journal.previous,
            ]
            if not restart_now:
                command.append("--no-restart")
            subprocess.Popen(
                command, close_fds=True, cwd=str(assistant.parent),
                creationflags=_DETACHED_PROCESS | _CREATE_NEW_PROCESS_GROUP,
            )
        except (OSError, UpdateError) as exc:
            journal.with_stage("failed", str(exc)).write(self.paths.journal_file)
            raise UpdateError(f"无法启动更新助手：{exc}") from exc


def _trusted_key_or_empty() -> bytes:
    try:
        return trusted_public_key()
    except ValueError:
        return b""
