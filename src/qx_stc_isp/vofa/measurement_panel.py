"""A selected-waveform measurement view; workers receive only frozen data."""

from dataclasses import dataclass

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QHeaderView, QLabel, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget

from .measurement import MeasurementJob, MeasurementWorker
from .projection import PlotBuffer


@dataclass(frozen=True, slots=True)
class MeasurementSource:
    buffer: PlotBuffer
    view_range: tuple[float, float]
    visible: tuple[bool, ...]
    maximum: int
    names: tuple[str, ...]
    precision: tuple[int, ...]
    key: tuple
    interval_ms: float = 1.
    paused: bool = False


class MeasurementPanel(QWidget):
    def __init__(self, source_provider, parent=None):
        super().__init__(parent)
        self._source_provider = source_provider
        self._worker = None
        self._token = 0
        self._requested_source = None
        self._last_identity = None
        self._summaries = ()
        self._items = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        self.status = QLabel("选择一张时间波形查看测量")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["通道 / 指标", "测量值"])
        self.tree.setMinimumWidth(0)
        self.tree.header().setMinimumSectionSize(40)
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.tree.setRootIsDecorated(True)
        self.tree.setIndentation(12)
        layout.addWidget(self.tree, 1)
        self._timer = QTimer(self)
        self._timer.setInterval(200)
        self._timer.timeout.connect(self._request)

    @property
    def summaries(self):
        return self._summaries

    def showEvent(self, event):
        super().showEvent(event)
        if self._worker is None:
            self._worker = MeasurementWorker(self)
            self._worker.ready.connect(self._accept)
            self._worker.failed.connect(self._failed)
            self._worker.start()
        self._worker.set_active(True)
        self._last_identity = None
        self.status.setText("等待所选波形的最新测量")
        self._timer.start()

    def hideEvent(self, event):
        self._timer.stop()
        self._token += 1
        self._requested_source = None
        self._last_identity = None
        if self._worker is not None:
            self._worker.set_active(False)
        self._clear("测量已暂停（页面隐藏）")
        super().hideEvent(event)

    def _clear(self, message):
        self._summaries = ()
        self._items.clear()
        self.tree.clear()
        self.status.setText(message)

    def _request(self):
        if not self.isVisible():
            return
        source = self._source_provider()
        if source is None:
            self._token += 1
            self._requested_source = None
            self._last_identity = None
            self._clear("请选择时间波形，或等待显示数据更新；XY不按时间窗口测量。")
            return
        identity = (source.key, id(source.buffer), source.buffer.cursor, source.buffer.generation)
        if identity == self._last_identity:
            return
        self._last_identity = identity
        self._token += 1
        self._requested_source = source
        self._worker.request(MeasurementJob(self._token, source.buffer, source.view_range,
                                             source.visible, source.maximum))

    def _matches(self, token):
        if not self.isVisible() or token != self._token or self._requested_source is None:
            return False
        current = self._source_provider()
        source = self._requested_source
        return (current is not None and current.key == source.key
                and current.buffer is source.buffer and current.view_range == source.view_range)

    def _accept(self, result):
        token, summaries = result
        if not self._matches(token):
            return
        source = self._requested_source
        self._summaries = summaries
        channels = tuple(item.channel for item in summaries)
        if tuple(self._items) != channels:
            expanded = {channel for channel, item in self._items.items() if item.isExpanded()}
            self.tree.clear()
            self._items = {}
            for slot, channel in enumerate(channels):
                parent = QTreeWidgetItem(self.tree)
                for label in ("最小值", "最大值", "平均值", "峰峰值", "RMS"):
                    QTreeWidgetItem(parent, [label, "—"])
                parent.setExpanded(channel in expanded or (not expanded and slot == 0))
                self._items[channel] = parent
        for summary in summaries:
            channel = summary.channel
            parent = self._items[channel]
            name = source.names[channel] if channel < len(source.names) else f"CH{channel + 1}"
            parent.setText(0, name)
            parent.setToolTip(0, name)
            parent.setText(1, f"N={summary.count}")
            digits = source.precision[channel] if channel < len(source.precision) else 6
            values = (summary.minimum, summary.maximum, summary.mean, summary.peak_to_peak, summary.rms)
            for row, value in enumerate(values):
                item = parent.child(row)
                if value is None:
                    text = "无有效采样" if not summary.count else "超出范围"
                else:
                    text = f"{value:.{digits}f}"
                    if len(text) > 18 or (value != 0 and float(text) == 0):
                        text = f"{value:.{max(1, digits)}g}"
                item.setText(1, text)
                item.setToolTip(1, summary.notice if value is None and summary.notice else text)
        prefix = "暂停快照" if source.paused else "可见时间窗口"
        if not summaries or not any(item.count for item in summaries):
            self.status.setText("无有效采样")
        else:
            begin, end = (value * source.interval_ms / 1000 for value in source.view_range)
            self.status.setText(f"{prefix} {begin:.6g}–{end:.6g}s · Δt={source.interval_ms:g}ms（设定值）")

    def _failed(self, result):
        token, message = result
        if self._matches(token):
            self._clear(f"测量失败：{message}")
            self._last_identity = None

    def stop(self):
        self._timer.stop()
        self._token += 1
        self._requested_source = None
        self._last_identity = None
        if self._worker is None:
            return True
        if not self._worker.stop():
            return False
        self._worker.deleteLater()
        self._worker = None
        return True

    def closeEvent(self, event):
        if self.stop():
            super().closeEvent(event)
        else:
            event.ignore()
