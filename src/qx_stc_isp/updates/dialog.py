"""Check/update dialog. Adds no other main-window layout changes."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QPlainTextEdit,
    QProgressBar, QPushButton, QVBoxLayout,
)

from .. import __version__
from .checker import CheckResult
from .manifest import UpdateError, VerifiedUpdate
from .service import UpdateService


class UpdateDialog(QDialog):
    def __init__(self, service: UpdateService, window, parent=None) -> None:
        super().__init__(parent or window)
        self.service = service
        self.window = window
        self._result: CheckResult | None = None
        self._update: VerifiedUpdate | None = None
        self._busy = False
        self.setWindowTitle("检查更新 · Astrspire " + __version__.replace(".dev", "-dev"))
        self.setMinimumWidth(480)

        layout = QVBoxLayout(self)
        self.status = QLabel("正在检查更新…")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.notes = QPlainTextEdit()
        self.notes.setReadOnly(True)
        self.notes.setVisible(False)
        self.notes.setMaximumHeight(160)
        layout.addWidget(self.notes)
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        preferences = service.preferences
        self.auto_check = QCheckBox("启动后自动检查更新")
        self.auto_check.setChecked(preferences.auto_check)
        self.auto_download = QCheckBox("发现新版后自动下载")
        self.auto_download.setChecked(preferences.auto_download)
        self.skip_version = QCheckBox("跳过此版本（手动检查仍显示）")
        layout.addWidget(self.auto_check)
        layout.addWidget(self.auto_download)
        layout.addWidget(self.skip_version)
        self.auto_check.toggled.connect(self._save_preferences)
        self.auto_download.toggled.connect(self._save_preferences)
        self.skip_version.toggled.connect(self._toggle_skip)

        buttons = QHBoxLayout()
        self.check_button = QPushButton("检查更新")
        self.download_button = QPushButton("下载更新")
        self.apply_now_button = QPushButton("立即重启更新")
        self.apply_later_button = QPushButton("稍后更新")
        self.cancel_button = QPushButton("取消安装安排")
        close_box = QDialogButtonBox(QDialogButtonBox.Close)
        close_box.rejected.connect(self.reject)
        for button in (self.check_button, self.download_button, self.apply_now_button,
                       self.apply_later_button, self.cancel_button):
            buttons.addWidget(button)
        buttons.addStretch()
        buttons.addWidget(close_box)
        layout.addLayout(buttons)

        self.check_button.clicked.connect(lambda: self._check(manual=True))
        self.download_button.clicked.connect(self._download)
        self.apply_now_button.clicked.connect(lambda: self._apply(restart_now=True))
        self.apply_later_button.clicked.connect(lambda: self._apply(restart_now=False))
        self.cancel_button.clicked.connect(self._cancel_schedule)

        service.check_finished.connect(self._on_check)
        service.download_progress.connect(self._on_progress)
        service.download_finished.connect(self._on_downloaded)
        service.download_failed.connect(self._on_failed)
        self._sync_buttons()
        self._check(manual=True)

    # ---- actions -------------------------------------------------------
    def _check(self, *, manual: bool) -> None:
        if self._busy:
            return
        self._busy = True
        self.status.setText("正在检查更新…")
        self._sync_buttons()
        self.service.check(manual=manual)

    def _download(self) -> None:
        if self._result is None or self._result.manifest is None or not self._result.asset_url:
            return
        self._busy = True
        self.progress.setRange(0, max(1, self._result.manifest.size))
        self.progress.setValue(0)
        self.progress.setVisible(True)
        self.status.setText("正在下载并验证更新…")
        self._sync_buttons()
        self.service.download(self._result.manifest, self._result.asset_url)

    def _apply(self, *, restart_now: bool) -> None:
        if self._update is None:
            return
        blocked = self.window.update_blocked_reason()
        if blocked:
            self.status.setText(blocked)
            return
        try:
            if restart_now:
                self.service.begin_update(self._update, restart_now=True, window=self.window)
                self.accept()
            else:
                self.service.schedule(self._update)
                self.status.setText("已安排：下次正常退出时安装更新，不会自动重启。")
                self._sync_buttons()
        except UpdateError as exc:
            self.status.setText(str(exc))

    def _cancel_schedule(self) -> None:
        self.service.cancel_schedule()
        self.status.setText("已取消安装安排；已下载的验证包保留。")
        self._sync_buttons()

    # ---- signals -------------------------------------------------------
    def _on_check(self, result: CheckResult) -> None:
        self._busy = False
        self._result = result
        self._update = None
        notes = ""
        if result.manifest is not None:
            notes = result.manifest.notes
            self.skip_version.blockSignals(True)
            self.skip_version.setChecked(result.manifest.version == self.service.preferences.skipped_version)
            self.skip_version.blockSignals(False)
        self.status.setText(result.message)
        self.notes.setPlainText(notes)
        self.notes.setVisible(bool(notes))
        if result.status == "available" and result.manifest is not None and self.service.preferences.auto_download:
            self._download()
        self._sync_buttons()

    def _on_progress(self, done: int, total: int) -> None:
        self.progress.setRange(0, max(1, total))
        self.progress.setValue(done)
        self.status.setText(f"正在下载更新… {done // 1024} / {total // 1024} KiB")

    def _on_downloaded(self, update: VerifiedUpdate) -> None:
        self._busy = False
        self._update = update
        self.progress.setVisible(False)
        self.status.setText(f"更新 {update.manifest.version} 已验证，准备安装。")
        self._sync_buttons()

    def _on_failed(self, message: str) -> None:
        self._busy = False
        self.progress.setVisible(False)
        self.status.setText(f"下载失败：{message}")
        self._sync_buttons()

    # ---- helpers -------------------------------------------------------
    def _save_preferences(self) -> None:
        self.service.preferences.auto_check = self.auto_check.isChecked()
        self.service.preferences.auto_download = self.auto_download.isChecked()
        self.service.save_preferences()

    def _toggle_skip(self, checked: bool) -> None:
        manifest = self._result.manifest if self._result else None
        self.service.preferences.skipped_version = manifest.version if checked and manifest else ""
        self.service.save_preferences()
        self._sync_buttons()

    def _sync_buttons(self) -> None:
        available = bool(self._result and self._result.status == "available")
        manifest = self._result.manifest if self._result else None
        self.check_button.setEnabled(not self._busy)
        self.download_button.setEnabled(available and self._update is None and not self._busy)
        self.download_button.setVisible(self._update is None)
        self.apply_now_button.setEnabled(self._update is not None and not self._busy)
        self.apply_later_button.setEnabled(self._update is not None and not self._busy)
        self.apply_now_button.setText("立即重启更新")
        self.cancel_button.setEnabled(self.service.pending is not None)
        self.skip_version.setEnabled(manifest is not None)
