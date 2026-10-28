"""Explicit recording catalog and RX-only timeline controls in native Qt."""
from __future__ import annotations

import hashlib
import threading
from pathlib import Path

from PySide6.QtCore import QThread, QTimer, Signal, Qt
from PySide6.QtWidgets import (QCheckBox, QDialog, QDoubleSpinBox, QFileDialog,
    QHBoxLayout, QHeaderView, QLabel, QPushButton, QSlider, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget)

from .replay_index import IndexCancelled, IndexStaleError, RecordingCatalog, RecordingIndex


class _IndexJob(QThread):
    result = Signal(object)
    error = Signal(str)

    def __init__(self, path, directory, parent):
        super().__init__(parent)
        self.path, self.directory = Path(path), directory
        self.cancel = threading.Event()

    def run(self):
        try:
            cache = None
            if self.directory is not None:
                self.directory.mkdir(parents=True, exist_ok=True)
                key = hashlib.sha256(str(self.path.resolve()).encode("utf-8")).hexdigest()
                cache = self.directory / f"{key}.index.json"
            index = None
            if cache is not None and cache.exists():
                try:
                    index = RecordingIndex.load(self.path, cache, cancel=self.cancel)
                except IndexCancelled:
                    raise
                except (OSError, ValueError, TypeError):
                    # Cache is untrusted/rebuildable, never repair raw records.
                    pass
            if index is None:
                index = RecordingIndex.build(self.path, cancel=self.cancel)
                if cache is not None and not self.cancel.is_set():
                    index.save(cache)
            if not self.cancel.is_set():
                self.result.emit(index)
        except IndexCancelled:
            self.error.emit("索引已取消；原始文件未修改")
        except Exception as exc:
            self.error.emit(f"无法登记记录：{exc}")


class HistoryDialog(QDialog):
    """Callbacks are explicit playback controls, never generic device actions.

    start_callback(path, speed, loop, seek_seconds) may return the created
    playback worker. worker_provider() lets the owner supply it later. Only
    objects advertising can_send=False and seek_seconds are accepted.
    """

    def __init__(self, parent: QWidget | None = None, *, start_callback,
                 worker_provider=None, catalog=None, index_directory=None):
        super().__init__(parent)
        self.setWindowTitle("录制会话与时间轴")
        self.resize(1060, 600)
        self.setMinimumSize(720, 400)
        self.catalog = catalog if catalog is not None else RecordingCatalog()
        self._start_callback = start_callback
        self._worker_provider = worker_provider or (lambda: None)
        self._index_directory = Path(index_directory) if index_directory is not None else None
        self._job = None
        self._worker = None
        self._closing = False
        layout = QVBoxLayout(self)
        actions = QHBoxLayout()
        self.open_button = QPushButton("登记记录文件…")
        self.open_button.clicked.connect(self.choose_recording)
        self.remove_button = QPushButton("从目录移除")
        self.remove_button.clicked.connect(self.remove_selected)
        self.cancel_index = QPushButton("取消索引")
        self.cancel_index.setEnabled(False)
        self.cancel_index.clicked.connect(self.cancel_registration)
        actions.addWidget(self.open_button)
        actions.addWidget(self.remove_button)
        actions.addStretch()
        actions.addWidget(self.cancel_index)
        layout.addLayout(actions)
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(("来源", "开始时间", "结束时间", "RX / TX / 总数", "时长(秒)", "状态", "原文件"))
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeMode.Stretch)
        self.table.itemSelectionChanged.connect(self._selected_changed)
        layout.addWidget(self.table, 1)
        self.timeline = QSlider(Qt.Orientation.Horizontal)
        self.timeline.setRange(0, 10000)
        self.timeline.sliderReleased.connect(self.seek_slider)
        layout.addWidget(self.timeline)
        controls = QHBoxLayout()
        self.position = QDoubleSpinBox()
        self.position.setDecimals(3)
        self.position.setRange(0, 0)
        self.position.setSuffix(" s")
        self.seek_button = QPushButton("定位")
        self.seek_button.clicked.connect(self.seek_selected)
        self.speed = QDoubleSpinBox()
        self.speed.setRange(0.1, 20)
        self.speed.setDecimals(2)
        self.speed.setValue(1)
        self.speed.setSuffix(" ×")
        self.speed.valueChanged.connect(self._speed_changed)
        self.loop = QCheckBox("循环")
        self.loop.toggled.connect(self._loop_changed)
        self.start_button = QPushButton("离线回放")
        self.start_button.clicked.connect(self.start_selected)
        self.pause_button = QPushButton("暂停")
        self.pause_button.clicked.connect(self.toggle_pause)
        self.stop_button = QPushButton("停止")
        self.stop_button.clicked.connect(self.stop_playback)
        for control in (QLabel("时间"), self.position, self.seek_button, QLabel("速度"), self.speed,
                        self.loop, self.start_button, self.pause_button, self.stop_button):
            controls.addWidget(control)
        layout.addLayout(controls)
        self.notice = QLabel("显式选择记录后登记；回放只解析 RX，TX 仅供查看，永不向设备发送。")
        self.notice.setWordWrap(True)
        layout.addWidget(self.notice)
        self._refresh()

    def choose_recording(self):
        path, _ = QFileDialog.getOpenFileName(self, "登记已有完整记录", "",
                "Astrspire记录 (*.session.json *.jsonl *.csv);;所有文件 (*)")
        if path:
            self.register_path(path)

    def register_path(self, path):
        if self._job is not None and self._job.isRunning():
            self.notice.setText("正在校验记录，请先等待或取消当前索引")
            return False
        self._job = _IndexJob(path, self._index_directory, self)
        self._job.result.connect(self._registered)
        self._job.error.connect(self.notice.setText)
        self._job.finished.connect(self._index_finished)
        self.open_button.setEnabled(False)
        self.cancel_index.setEnabled(True)
        self.notice.setText("后台校验完整性并建立时间/字节索引；不连接设备，不修改原文件。")
        self._job.start()
        return True

    def cancel_registration(self):
        if self._job is not None:
            self._job.cancel.set()

    def _registered(self, index):
        self.catalog.register(index)
        self._refresh(index.source)
        self.notice.setText(f"已登记 {index.record_count} 条记录；SHA-256校验通过。回放 RX；TX仅查看。")

    def _index_finished(self):
        self.open_button.setEnabled(True)
        self.cancel_index.setEnabled(False)

    def _refresh(self, selected=None):
        entries = self.catalog.entries()
        self.table.setRowCount(len(entries))
        for row, item in enumerate(entries):
            values = (", ".join(item.connection_ids) or "旧版/未标来源", item.first_timestamp,
                      item.last_timestamp, f"{item.rx_count} / {item.tx_count} / {item.record_count}",
                      f"{item.duration_seconds:.3f}", "完整 · 已校验", item.source)
            for column, value in enumerate(values):
                cell = QTableWidgetItem(value)
                cell.setData(Qt.ItemDataRole.UserRole, item.source)
                cell.setToolTip(value)
                self.table.setItem(row, column, cell)
            if selected == item.source:
                self.table.selectRow(row)
        self._selected_changed()

    def selected_index(self):
        row = self.table.currentRow()
        items = self.catalog.entries()
        return items[row] if 0 <= row < len(items) else None

    def _selected_changed(self):
        index = self.selected_index()
        self.start_button.setEnabled(index is not None)
        self.remove_button.setEnabled(index is not None)
        self.position.setMaximum(index.duration_seconds if index is not None else 0)
        self.timeline.setEnabled(index is not None)

    def remove_selected(self):
        index = self.selected_index()
        if index is not None:
            self.catalog.remove(index.source)
            self._refresh()
            self.notice.setText("只从目录移除；原始记录和索引文件均保留。")

    def _active_worker(self):
        worker = self._worker_provider() or self._worker
        if worker is not None and getattr(worker, "can_send", None) is False and hasattr(worker, "seek_seconds"):
            return worker
        return None

    def set_worker(self, worker):
        if worker is None or getattr(worker, "can_send", None) is not False or not hasattr(worker, "seek_seconds"):
            raise ValueError("时间轴只接受只读离线回放对象")
        if self._worker is not None:
            try:
                self._worker.timeline_changed.disconnect(self._timeline_changed)
            except (TypeError, RuntimeError):
                pass
        self._worker = worker
        worker.timeline_changed.connect(self._timeline_changed)

    def start_selected(self):
        index = self.selected_index()
        if index is None:
            return
        try:
            worker = self._start_callback(index.source, self.speed.value(), self.loop.isChecked(), self.position.value())
            if worker is not None:
                self.set_worker(worker)
            self.notice.setText("离线 RX 回放已请求；TX仅供查看，不发送。可暂停、定位或停止。")
        except Exception as exc:
            self.notice.setText(f"无法开始回放：{exc}")

    def seek_slider(self):
        index = self.selected_index()
        if index is not None:
            self.position.setValue(index.duration_seconds * self.timeline.value() / 10000)
            self.seek_selected()

    def seek_selected(self):
        worker = self._active_worker()
        if worker is not None:
            try:
                worker.seek_seconds(self.position.value())
                self.notice.setText("正在重新建立解码状态；暂停时只移动定位，继续后才输出 RX。")
            except (ValueError, RuntimeError) as exc:
                self.notice.setText(str(exc))

    def _timeline_changed(self, offset, duration, generation):
        worker = self._active_worker()
        if worker is None or generation != worker.generation:
            return
        if not self.timeline.isSliderDown():
            self.timeline.setValue(round(min(1, offset / max(duration, 0.000001)) * 10000))
            if not self.position.hasFocus():
                self.position.setValue(offset)

    def _speed_changed(self, value):
        worker = self._active_worker()
        if worker is not None:
            worker.set_speed(value)

    def _loop_changed(self, checked):
        worker = self._active_worker()
        if worker is not None:
            worker.set_loop(checked)

    def toggle_pause(self):
        worker = self._active_worker()
        if worker is not None:
            worker.set_paused(not worker.paused)
            self.pause_button.setText("继续" if worker.paused else "暂停")

    def stop_playback(self):
        worker = self._active_worker()
        if worker is not None:
            worker.stop()

    def closeEvent(self, event):
        # Closing this view does not stop recording, acquisition, or playback.
        # It only cancels the derived-file job that belongs to this dialog.
        self.cancel_registration()
        if self._job is not None and self._job.isRunning() and not self._job.wait(500):
            event.ignore()
            if not self._closing:
                self._closing = True
                QTimer.singleShot(100, self._retry_close)
            return
        event.accept()

    def _retry_close(self):
        self._closing = False
        self.close()
