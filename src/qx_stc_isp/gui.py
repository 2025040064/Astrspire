"""STC ISP 与手动连接的数据工作台；硬件下载受严格指纹约束。"""

from __future__ import annotations

import os
import math
import codecs
import json
import sys
import threading
import time
from uuid import uuid4
from collections import deque
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Qt, Signal, Slot, QTimer, QSize, QEvent, QPropertyAnimation, QEasingCurve, Property
from PySide6.QtGui import QAction, QCloseEvent, QFont, QFontDatabase, QPainter, QColor, QPen, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QCompleter,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGraphicsEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QInputDialog,
    QListWidget,
    QListWidgetItem,
    QAbstractItemView,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QTextBrowser,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from .chip_catalog import ChipCatalog
from . import __version__
from .appearance import (stylesheet, qt_palette, theme_palette, theme_choices,
                         custom_theme_documents, register_custom_theme, FONT_MONO,
                         TEXT_SCALES, TEXT_SCALE_LABELS, scaled_px, set_text_scale, ui_font)
from .ui_preferences import load_text_scale, save_text_scale
from .theme_editor import ThemeEditorDialog
from .panels import AdjustableSplitter, PlotToolbar
from .ui_icons import navigation_icon
from .hex_image import HexImage
from .firmware_files import buffer_preview, export_firmware, load_firmware, parse_address
from .official_replay import OfficialCapture, OfficialReplaySession
from .port_lease import PORT_LEASES, PortLeaseError
from .ports import PortDescriptor, enumerate_ports
from .session import DownloadSession, SessionEvent, SessionState
from .stc89 import ERASE_BYTES
from .stc89_session import Stc89DownloadSession, Stc89EraseSession, Stc89ReadOnlySession
from .simulator import SimulatedTransport
from .transport import PySerialTransport, TransportCancelled, TransportError
from .vofa.serial_link import SerialLinkWorker
from .vofa.ansi import AnsiSink
from .vofa.native_project import inspect_native_context
from .vofa.sample_store import SampleStore
from .vofa.recorder import AsyncRecorder
from .vofa.text_log import RotatingTextLogSink
from .vofa.dashboard import DashboardManager
from .vofa.compat_demo import CompatDemoConfig, CompatDemoWorker, DemoSchedule
from .vofa.transmit import encode_send_payload
from .vofa.packets import PacketStore
from .vofa.serial_preferences import SendHistory
from .vofa.serialweb import SerialWebTextDecoder
from .vofa.swd_panel import SwdSourcePanel
from .vofa.swd_link import SwdLinkWorker
from .vofa.waveform import WaveformWidget
from .vofa.network_link import NetworkConfig, NetworkLinkWorker
from .vofa.playback import CsvReplayWorker
from .vofa.commands import CommandEntry, CommandGroup, CommandItem, migrate_command_groups, load_commands, save_commands
from .vofa.send_jobs import SendJobController
from .vofa.controls import ControlWorkspace, ControlSpec
from .vofa.control_editor import ControlEditorDialog, CONTROL_LABELS
from .vofa.plugins import PLUGIN_REGISTRY, EngineManifest
from .vofa.plugin_manager import PluginManagerDialog
from .vofa.images import IncrementalImageDecoder, ImageViewWidget, ImageReplayWorker
from .vofa.image_store import ImageFrameStore
from .vofa.image_effects import effect_parameter
from .vofa.capture_stream import StreamRecorder, RawReplayWorker
from .vofa.rotating_capture import RotatingStreamRecorder
from .vofa.plot_settings import PlotSettingsDialog
from .vofa.matrix_source import MatrixReplayWorker
from .vofa.analysis_view import AnalysisView
from .vofa.channel_settings import ChannelDisplay, ChannelSettingsDialog
from .vofa.json_data import load_json_document
from .vofa import (
    CsvRecorder,
    TelemetryEvent,
    WorkspaceSettings,
    create_decoder,
    load_workspace,
    save_workspace,
)


_PHASES = [
    (SessionState.WAIT_POWER, "等待上电"),
    (SessionState.IDENTIFIED, "芯片识别"),
    (SessionState.NEGOTIATING, "波特率协商"),
    (SessionState.ERASING, "擦除"),
    (SessionState.PROGRAMMING, "写入"),
    (SessionState.VERIFYING, "结果确认"),
    (SessionState.COMPLETE, "完成"),
]

_MAX_MONITOR_CHANNELS = 16
_MAX_MONITOR_EVENTS_PER_BATCH = 256
_MAX_MONITOR_RAW_BATCH_BYTES = 64 * 1024
_MAX_SAMPLE_HISTORY = 100000
_MAX_MONITOR_LOG_BYTES = 16 * 1024 * 1024
_MAX_SAMPLE_LOG_LINES_PER_BATCH = 12


_STYLE = """
QMainWindow, QWidget#Root { background: #ffffff; color: #182638; }
QLabel { color: #182638; }
QLabel#Brand { font-size: 20px; font-weight: 750; color: #0860af; }
QLabel#Subtitle { color: #677789; font-size: 11px; }
QLabel#SectionTitle { font-size: 14px; font-weight: 700; color: #095ca9; }
QLabel#Muted { color: #68798c; font-size: 11px; }
QLabel#Value { font-size: 14px; font-weight: 650; color: #173450; }
QLabel#Status { color: #175d9b; font-size: 12px; }
QLabel#Safety { color: #a65b14; font-size: 12px; }
QFrame#Card { background: #ffffff; border: 1px solid #dfe5eb; border-radius: 5px; }
QFrame#Header { background: #ffffff; border-bottom: 1px solid #d8dfe7; }
QFrame#Rail { background: #e4e8ed; border-right: 1px solid #d3dbe4; }
QFrame#Badge { background: #fff2e4; border: 1px solid #efc794; border-radius: 5px; }
QLabel#BadgeText { color: #96530e; font-size: 11px; font-weight: 700; }
QPushButton { background: #f8fafc; color: #175b98; border: 1px solid #cdd8e3;
              border-radius: 4px; padding: 7px 10px; font-weight: 600; }
QPushButton:hover:enabled { background: #eaf3fc; border-color: #79addb; }
QPushButton:disabled { background: #f1f3f5; color: #788795; border-color: #dce2e8; }
QPushButton#Primary { background: #1171c4; border-color: #1171c4; color: white; font-weight: 750; }
QPushButton#Primary:hover:enabled { background: #2785d5; }
QPushButton#Power { background: #d9f5eb; border-color: #56b797; color: #086e53; }
QPushButton#Primary:disabled, QPushButton#Power:disabled {
    background: #edf0f3; border-color: #dce2e8; color: #647487;
}
QPushButton#Nav { background: transparent; color: #315e82; border: none;
                  border-left: 3px solid transparent; border-radius: 0; padding: 13px 2px; }
QPushButton#Nav:checked { background: #ffffff; color: #0562b2; border-left: 3px solid #0875cf; }
QComboBox, QLineEdit { color: #18334b; background: #ffffff; border: 1px solid #c6d1dc;
                       border-radius: 3px; padding: 6px 8px; min-height: 20px; }
QComboBox QAbstractItemView { color: #18334b; background: #ffffff;
                              selection-background-color: #d8eaff; }
QPlainTextEdit, QTextEdit { color: #173450; background: #ffffff; border: 1px solid #d8e0e8;
                 border-radius: 3px; padding: 6px; font-family: Consolas, monospace; font-size: 11px; }
QProgressBar { background: #edf2f7; border: 1px solid #ccd9e5; border-radius: 3px;
               color: #173450; text-align: center; min-height: 18px; }
QProgressBar::chunk { background: #2b91db; }
QScrollArea { border: none; background: #ffffff; }
QScrollBar:vertical { background: #f1f3f6; width: 9px; }
QScrollBar::handle:vertical { background: #b8c5d1; border-radius: 3px; }
QCheckBox { color: #30485e; spacing: 6px; }
"""


class _PressScaleEffect(QGraphicsEffect):
    """Scales a widget about its centre; used for a press-down feel without moving layout."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._scale = 1.0

    def _scale_value(self) -> float:
        return self._scale

    def _set_scale_value(self, value: float) -> None:
        if value == self._scale:
            return
        self._scale = value
        self.update()

    scaleValue = Property(float, _scale_value, _set_scale_value)

    def draw(self, painter: QPainter) -> None:
        if abs(self._scale - 1.0) < 1e-3:
            self.drawSource(painter)
            return
        rect = self.sourceBoundingRect()
        painter.save()
        painter.translate(rect.center())
        painter.scale(self._scale, self._scale)
        painter.translate(-rect.center())
        self.drawSource(painter)
        painter.restore()


class _ClickEffects(QObject):
    """One app-wide filter that gives buttons a brief scale-down while pressed.

    All animation state lives on the widget's own QGraphicsEffect, so nothing is
    retained after a click and widgets deleted mid-press leave no dangling refs.
    """

    _PRESS_SCALE = 0.94
    _PRESS_MS = 70
    _RELEASE_MS = 130

    def __init__(self, app: QApplication) -> None:
        super().__init__(app)
        app.installEventFilter(self)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        kind = event.type()
        if kind == QEvent.MouseButtonPress:
            if isinstance(obj, (QPushButton, QToolButton)) and obj.isEnabled():
                self._press(obj)
        elif kind == QEvent.MouseButtonRelease:
            if isinstance(obj, (QPushButton, QToolButton)):
                self._release(obj)
        return False

    def _animate(self, effect: _PressScaleEffect, start: float, end: int, duration: int) -> None:
        previous = getattr(effect, "_animation", None)
        if previous is not None:
            previous.stop()
        animation = QPropertyAnimation(effect, b"scaleValue", effect)
        animation.setDuration(duration)
        animation.setStartValue(start)
        animation.setEndValue(end)
        animation.setEasingCurve(QEasingCurve.OutQuad if end < start else QEasingCurve.OutCubic)
        effect._animation = animation
        animation.start()

    def _press(self, widget: QWidget) -> None:
        effect = _PressScaleEffect(widget)
        effect._done = False
        widget.setGraphicsEffect(effect)
        self._animate(effect, 1.0, self._PRESS_SCALE, self._PRESS_MS)

    def _release(self, widget: QWidget) -> None:
        effect = widget.graphicsEffect()
        if not isinstance(effect, _PressScaleEffect):
            return
        self._animate(effect, effect.scaleValue, 1.0, self._RELEASE_MS)
        effect._done = True
        # Defer removal so the effect is never deleted inside its own finished signal.
        QTimer.singleShot(self._RELEASE_MS + 20, lambda key=widget: self._restore(key))

    def _restore(self, widget: QWidget) -> None:
        try:
            effect = widget.graphicsEffect()
            if isinstance(effect, _PressScaleEffect) and getattr(effect, "_done", False):
                widget.setGraphicsEffect(None)
        except RuntimeError:
            pass


def _card(title: str) -> tuple[QFrame, QVBoxLayout]:
    card = QFrame()
    card.setObjectName("Card")
    layout = QVBoxLayout(card)
    layout.setContentsMargins(12, 10, 12, 10)
    layout.setSpacing(6)
    heading = QLabel(title)
    heading.setObjectName("SectionTitle")
    layout.addWidget(heading)
    return card, layout


def _sample_hex_path() -> Path:
    """Locate the demo HEX in source and PyInstaller one-file builds."""

    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root is not None:
        return Path(bundle_root) / "examples" / "led.hex"
    return Path(__file__).resolve().parents[2] / "examples" / "led.hex"


class _SessionWorker(QObject):
    event = Signal(object)
    finished = Signal()

    def __init__(self, session: DownloadSession | OfficialReplaySession | Stc89ReadOnlySession,
                 *, device: str | None = None) -> None:
        super().__init__()
        self.session = session
        self.device = device

    @Slot()
    def run(self) -> None:
        lease = None
        try:
            if self.device is not None:
                lease = PORT_LEASES.acquire(self.device, owner="stc-isp")
            self.session.run()
        except Exception as exc:  # 最后的 UI 边界，保留可见错误
            self.event.emit(
                SessionEvent(
                    kind="error",
                    state=SessionState.FAILED,
                    progress=0,
                    message=f"任务失败：{exc}",
                )
            )
        finally:
            if lease is not None:
                lease.release()
            self.finished.emit()


class _Waveform(QWidget):
    """Bounded, read-only sample display; no third-party graph assets."""

    _COLORS = ("#d51f75", "#168a42", "#245ec7", "#d4840d", "#8c3bb3", "#078b98")

    def __init__(self) -> None:
        super().__init__()
        self.setMinimumHeight(250)
        self._series: list[deque[float]] = []
        self._names: list[str] = []

    def clear(self) -> None:
        self._series.clear()
        self._names.clear()
        self.update()

    def append_samples(self, samples: tuple[float, ...], names: list[str]) -> None:
        while len(self._series) < len(samples):
            self._series.append(deque(maxlen=800))
        del self._series[len(samples):]
        self._names = names
        for index, value in enumerate(samples):
            self._series[index].append(float(value))

    def paintEvent(self, _event: object) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor("#ffffff"))
        area = self.rect().adjusted(48, 22, -16, -30)
        if area.width() <= 0 or area.height() <= 0:
            return
        painter.setPen(QPen(QColor("#e4e9ef"), 1))
        for step in range(5):
            y = area.top() + round(area.height() * step / 4)
            painter.drawLine(area.left(), y, area.right(), y)
        painter.drawRect(area)
        values = [value for series in self._series for value in series]
        if not values:
            painter.setPen(QColor("#8a9bad"))
            painter.drawText(area, Qt.AlignCenter, "连接后显示接收到的采样；此处不发送数据")
            return
        low, high = min(values), max(values)
        if high == low:
            low -= 1
            high += 1
        painter.setPen(QColor("#6b7e90"))
        painter.drawText(5, area.top() + 12, f"{high:.3g}")
        painter.drawText(5, area.bottom(), f"{low:.3g}")
        for index, series in enumerate(self._series):
            if not series:
                continue
            color = QColor(self._COLORS[index % len(self._COLORS)])
            painter.setPen(QPen(color, 1.5))
            points = list(series)
            span = max(1, len(points) - 1)
            last: tuple[int, int] | None = None
            for position, value in enumerate(points):
                x = area.left() + round(area.width() * position / span)
                y = area.bottom() - round(area.height() * (value - low) / (high - low))
                if last is not None:
                    painter.drawLine(last[0], last[1], x, y)
                last = (x, y)
            if index < 6:
                name = self._names[index] if index < len(self._names) else f"CH{index + 1}"
                painter.drawText(area.left() + index * 95, self.height() - 8, name[:12])


class _MonitorWorker(QObject):
    """Explicitly started passive serial RX; it has no TX method or command path."""

    events = Signal(object)
    raw = Signal(bytes)
    status = Signal(str, str)
    finished = Signal()

    def __init__(self, device: str, baudrate: int, decoder_name: str) -> None:
        super().__init__()
        self.device = device
        self.baudrate = baudrate
        self.decoder_name = decoder_name
        self._stop = threading.Event()
        self._transport: PySerialTransport | None = None

    def stop(self) -> None:
        self._stop.set()
        if self._transport is not None:
            self._transport.cancel()

    @Slot()
    def run(self) -> None:
        lease = None
        transport = None
        pending: list[object] = []
        raw_pending = bytearray()
        try:
            lease = PORT_LEASES.acquire(self.device, owner="vofa")
            if self._stop.is_set():
                return
            transport = PySerialTransport(self.device)
            self._transport = transport
            if self._stop.is_set():
                return
            transport.open(self.baudrate)
            if self._stop.is_set():
                return
            self.status.emit("connected", f"已连接 {self.device}，只接收数据；不发送串口命令。")
            decoder = create_decoder(self.decoder_name)
            last_emit = time.monotonic()
            while not self._stop.is_set():
                chunk = transport.read_some(4096, timeout=0.05)
                if chunk:
                    raw_pending.extend(chunk)
                    if len(raw_pending) > _MAX_MONITOR_RAW_BATCH_BYTES:
                        raise TransportError("接收速率超过安全预览上限；已停止监视，避免日志或界面耗尽内存")
                    decoded = decoder.feed(chunk)
                    received = datetime.now().astimezone()
                    pending.extend(replace(item, received_at=received) for item in decoded)
                    while len(pending) >= _MAX_MONITOR_EVENTS_PER_BATCH:
                        self.events.emit(pending[:_MAX_MONITOR_EVENTS_PER_BATCH])
                        del pending[:_MAX_MONITOR_EVENTS_PER_BATCH]
                now = time.monotonic()
                if now - last_emit >= 0.05:
                    if raw_pending:
                        self.raw.emit(bytes(raw_pending))
                        raw_pending.clear()
                    if pending:
                        self.events.emit(pending)
                    pending = []
                    last_emit = now
        except TransportCancelled:
            if not self._stop.is_set():
                self.status.emit("error", f"{self.device} 接收已取消。")
        except (PortLeaseError, TransportError, ValueError) as exc:
            self.status.emit("error", str(exc))
        except Exception as exc:  # 将意外错误收口到可见状态
            self.status.emit("error", f"监视失败：{exc}")
        finally:
            if raw_pending:
                self.raw.emit(bytes(raw_pending))
            if pending:
                self.events.emit(pending)
            if transport is not None:
                try:
                    transport.close()
                except TransportError as exc:
                    self.status.emit("error", str(exc))
            self._transport = None
            if lease is not None:
                lease.release()
            self.status.emit("stopped", f"{self.device} 监视已停止。")
            self.finished.emit()


class TerminalView(QTextEdit):
    """Rich terminal with the plain-text helpers the existing UI calls."""

    def __init__(self) -> None:
        super().__init__()
        self.setReadOnly(True)
        self.setAcceptRichText(False)
        self.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self.document().setMaximumBlockCount(3000)

    def setMaximumBlockCount(self, count: int) -> None:
        self.document().setMaximumBlockCount(count)

    def _end_cursor(self) -> QTextCursor:
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        return cursor

    def _insert_lines(self, cursor: QTextCursor, text: str, fmt: QTextCharFormat | None = None) -> None:
        for index, line in enumerate(text.split('\n')):
            if index:
                cursor.insertBlock()
            if line:
                cursor.insertText(line, fmt) if fmt is not None else cursor.insertText(line)

    def appendPlainText(self, text: str) -> None:
        cursor = self._end_cursor()
        self._insert_lines(cursor, text)
        cursor.insertBlock()
        self.setTextCursor(cursor)

    def append_ansi(self, text: str, sink: AnsiSink, prefix: str = "") -> None:
        cursor = self._end_cursor()
        if prefix:
            cursor.insertText(prefix)
        for segment, style in sink.feed(text):
            if not segment:
                continue
            fmt = QTextCharFormat()
            foreground, background = style.foreground, style.background
            if style.inverse:
                foreground, background = background or "#ffffff", foreground or "#000000"
            if foreground:
                fmt.setForeground(QColor(foreground))
            if background:
                fmt.setBackground(QColor(background))
            if style.bold:
                fmt.setFontWeight(QFont.Weight.Bold)
            if style.underline:
                fmt.setFontUnderline(True)
            self._insert_lines(cursor, segment, fmt)
        cursor.insertBlock()
        self.setTextCursor(cursor)


class MainWindow(QMainWindow):
    """STC ISP workbench and explicitly operated STM32 telemetry tools."""

    def __init__(self, *, log_dir: Path | None = None, text_scale: float | None = None) -> None:
        super().__init__()
        self.setWindowTitle("Astrspire v" + __version__.replace(".dev", "-dev"))
        self.resize(1260, 820)
        self.setMinimumSize(1020, 680)
        self._theme = "light"
        # 字体与字号必须在建立控件之前定下来，否则控件按旧字号算尺寸。
        self._text_scale = set_text_scale(1.0 if text_scale is None else text_scale)
        app = QApplication.instance()
        if app is not None:
            app.setFont(ui_font(self._text_scale))
        self.setStyleSheet(stylesheet(self._theme, self._text_scale))

        self.catalog = ChipCatalog.from_static_evidence()
        self.image: HexImage | None = None
        self.image_path: Path | None = None
        self.eeprom_image: HexImage | None = None
        self._ports: list[PortDescriptor] = []
        self.session: DownloadSession | OfficialReplaySession | Stc89ReadOnlySession | None = None
        self._thread: QThread | None = None
        self._worker: _SessionWorker | None = None
        self._active = False
        self._monitor_thread: QThread | None = None
        self._monitor_worker: SerialLinkWorker | NetworkLinkWorker | CsvReplayWorker | ImageReplayWorker | RawReplayWorker | MatrixReplayWorker | None = None
        self._monitor_active = False
        self._monitor_connected = False
        self._tx_bytes = 0
        self._rx_bytes = 0
        self._rx_text_decoder = SerialWebTextDecoder('utf-8')
        self._ansi_sink = AnsiSink()
        self._rx_encoding = 'utf-8'
        self._tx_encoding = 'utf-8'
        self.packet_store = PacketStore()
        self.send_history = SendHistory()
        self._tx_metadata = deque()
        self._tx_completion_count = 0
        self._connection_presets = []
        self._serial_tools_state = {}
        self.serial_tools_dialog = None
        self._terminal_font_size = max(8, min(24, round(11 * self._text_scale)))
        self._terminal_font_customized = False
        self._phase_state = None
        self._file_send_chunk_size = 1024
        self._file_send_interval_ms = 20
        self._last_values: tuple[float, ...] = ()
        self._channel_display = tuple(ChannelDisplay() for _ in range(16))
        self._source_running = ""
        self._source_channel_names: tuple[str, ...] = ()
        self._image_decoder = IncrementalImageDecoder()
        self._image_rx_enabled = False
        self._image_frames = 0
        self._image_store = ImageFrameStore()
        self._image_auto_selected = False
        self._last_image_render_at = 0.0
        self._image_render_timer = QTimer(self)
        self._image_render_timer.setSingleShot(True)
        self._image_render_timer.timeout.connect(self._show_cached_image)
        self._dashboard_timer = QTimer(self)
        self._dashboard_timer.setSingleShot(True)
        self._dashboard_timer.setInterval(50)
        self._dashboard_timer.timeout.connect(self._paint_dashboard)
        self._commands: list[CommandEntry] = []
        self._command_groups = list(migrate_command_groups(()))
        self._command_group_id = self._command_groups[0].id
        self._command_ids: list[str] = []
        self._workspace_dirty = False
        self._loading_workspace = False
        self._sample_recorder: AsyncRecorder | None = None
        self._stream_recorder: AsyncRecorder | None = None
        self._closing_recorders: list[tuple[str, AsyncRecorder]] = []
        self._record_poll_timer = QTimer(self)
        self._record_poll_timer.setInterval(200)
        self._record_poll_timer.timeout.connect(self._poll_recorders)
        self._record_poll_timer.start()
        self._stream_flush_timer = QTimer(self)
        self._stream_flush_timer.timeout.connect(self._flush_stream_recording)
        self._recorded_rows = 0
        self._periodic_timer = QTimer(self)
        self._periodic_timer.timeout.connect(self._send_periodic)
        self._send_job_running = False
        self._send_job = SendJobController(self._enqueue_payload, self._can_send_data, self)
        self._send_job.status.connect(self._append_monitor_log)
        self._send_job.error.connect(self._handle_send_job_error)
        self._send_job.progress.connect(self._handle_send_job_progress)
        self._send_job.finished.connect(self._handle_send_job_finished)
        self._record_flush_timer = QTimer(self)
        self._record_flush_timer.timeout.connect(self._flush_sample_recording)
        self._demo_active = False
        self._demo_step = 0
        self._demo_timer = QTimer(self)
        self._demo_timer.timeout.connect(self._emit_demo_sample)
        self._monitor_error: str | None = None
        self.sample_store = SampleStore(capacity=_MAX_SAMPLE_HISTORY)
        self._sample_index = 0
        self._last_chip_name: str | None = None
        from .updates.config import local_data_dir
        self._log_dir = log_dir or local_data_dir() / "logs"
        self._log_file: Path | None = None
        self._monitor_log_file: Path | None = None
        self._monitor_log_bytes = 0
        self._monitor_log_limit_reached = False
        self._monitor_text_recorder: AsyncRecorder | None = None
        self._metrics_previous = (time.monotonic(), 0, 0, 0, 0)
        self._metrics_timer = QTimer(self)
        self._metrics_timer.setInterval(1000)
        self._metrics_timer.timeout.connect(self._update_link_metrics)

        self._build_ui()
        from .updates.service import UpdateService

        self.update_service = UpdateService(parent=self)
        self.update_service.snapshot = self.save_update_snapshot
        self.update_service.check_finished.connect(self._on_background_check)
        self._auto_check_pending = False
        self._update_dialog = None
        if getattr(sys, "frozen", False):
            QTimer.singleShot(3000, self._auto_check_update)
        app = QApplication.instance()
        if not hasattr(app, '_astrspire_click_effects'):
            app._astrspire_click_effects = _ClickEffects(app)
        self._click_effects = app._astrspire_click_effects
        for combo in self.findChildren(QComboBox):
            # Long Windows device descriptions must not widen a clipped sidebar.
            combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(8)
            combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        for combo in (self.send_mode_combo, self.send_ending_combo):
            combo.setFixedWidth(88)
        self.theme_combo.setFixedWidth(84)
        self._fit_sidebar_widths()
        QTimer.singleShot(0, self._fit_sidebar_widths)
        self.refresh_ports()
        sample = _sample_hex_path()
        if sample.is_file():
            self.load_hex(sample)
            self._append_log("已加载内置 led.hex 样例，可直接体验模拟流程。")
        self._update_controls()
        self._metrics_timer.start()
        self._sync_active_plot()
        self.wave_buffer_spin.valueChanged.connect(lambda _value: self._rebuild_display_samples())
        # User edits mark the workspace dirty; programmatic load suppresses it.
        for widget in self.findChildren(QLineEdit):
            widget.textEdited.connect(self._mark_workspace_dirty)
        for widget in self.findChildren(QComboBox):
            widget.activated.connect(self._mark_workspace_dirty)
        for widget in (*self.findChildren(QSpinBox), *self.findChildren(QDoubleSpinBox)):
            widget.valueChanged.connect(self._mark_workspace_dirty)
        for widget in self.findChildren(QCheckBox):
            widget.clicked.connect(self._mark_workspace_dirty)
        # 启动时就按选定主题上色，波形/控件/图像视图不能停留在构造时的默认调色板。
        self.apply_theme()
        self._sync_text_scale_actions()

    @property
    def _samples(self):
        """Legacy read-only projection; SampleStore owns the only raw history."""
        return tuple((frame.source_timestamp or frame.received_at, frame.values)
                     for frame in self.sample_store.snapshot())

    @property
    def _sample_names_history(self):
        return tuple(frame.names for frame in self.sample_store.snapshot())

    def _mark_workspace_dirty(self, *_args) -> None:
        if not self._loading_workspace:
            self._workspace_dirty = True

    def _refresh_dashboard(self) -> None:
        if not self._dashboard_timer.isActive():
            self._dashboard_timer.start()

    def _paint_dashboard(self) -> None:
        if hasattr(self, 'dashboard'):
            latest = self.sample_store.latest_frame
            names = self._effective_channel_names()
            count = min(16, max(len(names), len(latest.values) if latest is not None else 0))
            names = [names[index] if index < len(names) else latest.names[index]
                     if latest is not None and index < len(latest.names) else f'CH{index + 1}'
                     for index in range(count)]
            self.dashboard.request_projection(self.sample_store, self._channel_display,
                                              self._display_names(names))
            self._show_cached_image()

    def _measurement_source(self):
        from .vofa.measurement_panel import MeasurementSource
        target = self._plot_target()
        if target is None or target.mode != 'time':
            return None
        buffer = target.plot_buffer
        if buffer is None:
            return None
        if not target.paused and (buffer.cursor.epoch != self.sample_store.cursor.epoch
                or buffer.generation != self.dashboard.projection_generation):
            return None
        visible = target.visible_channels
        window = target.view_range
        names = target.displayed_names
        digits = tuple(item.precision if item.precision is not None else self.precision_spin.value()
                       for item in self._channel_display)
        window_key = ('auto',) if target.time_window_automatic else window
        key = (id(target), target.paused, id(buffer) if target.paused else None,
               window_key, target.max_samples, visible, names, digits,
               target.sample_interval_ms, buffer.cursor.epoch, buffer.generation)
        return MeasurementSource(buffer, window, visible, target.max_samples, names,
                                 digits, key, target.sample_interval_ms, target.paused)

    def _plot_target(self):
        return self.dashboard.active_waveform() if hasattr(self, 'dashboard') else self.waveform

    def _sync_active_plot(self, *_args):
        if not hasattr(self, 'channel_visibility_checks'):
            return
        target = self._plot_target()
        self.plot_target_label.setText('操作目标：' + (next((card['title'] for page in self.dashboard.pages() for card in page['cards'] if self.dashboard.card_widget(card['id']) is target), '波形') if target else '当前页没有选中的波形卡'))
        self.plot_target_label.setToolTip(self.plot_target_label.text())
        for button in (self.wave_pause_button, self.wave_reset_button, self.plot_settings_button, self.plot_export_button):
            button.setEnabled(target is not None)
        self.wave_pause_button.blockSignals(True)
        self.wave_pause_button.setChecked(target.paused if target else False)
        self.wave_pause_button.blockSignals(False)
        self.wave_buffer_spin.blockSignals(True)
        self.wave_buffer_spin.setValue(target.max_samples if target else 800)
        self.wave_buffer_spin.setEnabled(target is not None)
        self.wave_buffer_spin.setToolTip('当前波形卡的显示点数；原始采样历史由共享存储保留。')
        self.wave_buffer_spin.blockSignals(False)
        for index, check in enumerate(self.channel_visibility_checks):
            check.blockSignals(True)
            check.setChecked(target.visible_channels[index] if target else True)
            check.setEnabled(target is not None)
            check.blockSignals(False)

    def _activate_visual_kind(self, kind: str) -> None:
        for page in self.dashboard.pages():
            if any(card['kind'] == kind for card in page['cards']):
                for index in range(self.visual_tabs.count()):
                    if self.visual_tabs.widget(index).property('dashboard_page_id') == page['id']:
                        self.visual_tabs.setCurrentIndex(index)
                        return
        self.dashboard.add_card(kind, {'control': '控件', 'image': '图像', 'waveform': '波形', 'analysis': '统计 / 频谱'}[kind])

    def _build_ui(self) -> None:
        palette = theme_palette(self._theme)
        self._menu_icon_buttons: list[QToolButton] = []
        tools_menu = self.menuBar().addMenu("工具")
        self.tools_menu = tools_menu
        tools_menu.addAction("数据引擎扩展…", self.open_plugin_manager)
        tools_menu.addAction("报文与协议工作台…", self.open_serial_tools)
        tools_menu.addAction("自定义主题…", self.open_theme_editor)
        self.text_scale_menu = tools_menu.addMenu("界面文字大小")
        self._text_scale_actions: list[tuple[QAction, float]] = []
        for key, factor in TEXT_SCALES:
            action = self.text_scale_menu.addAction(TEXT_SCALE_LABELS[key])
            action.setCheckable(True)
            action.triggered.connect(lambda checked=False, value=factor: self.apply_text_scale(value))
            self._text_scale_actions.append((action, factor))
        tools_menu.addAction("文件发送设置…", self.configure_file_sending)
        help_action = tools_menu.addAction("使用帮助…", self.show_workbench_help)
        help_action.setShortcut('F1')
        tools_menu.addAction("检查更新…", self.open_update_dialog)
        tools_menu.addAction("恢复更新前工作区…", self.restore_update_snapshot)
        self.menuBar().hide()
        self.simulation_action = QAction("下载流程模拟", self)
        self.simulation_action.setCheckable(True)
        self.simulation_action.toggled.connect(lambda enabled: self.mode_combo.setCurrentIndex(0 if enabled else 1))
        tools_menu.addAction(self.simulation_action)
        root = QWidget()
        root.setObjectName("Root")
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        header = QFrame()
        header.setObjectName("Header")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(14, 2, 10, 2)
        title = QLabel("Astrspire " + __version__.replace(".dev", "-dev"))
        title.setObjectName("Brand")
        header_layout.addWidget(title)
        self.tools_button = QToolButton()
        self.tools_button.setText("⋮")
        self.tools_button.setIcon(navigation_icon('menu', palette.muted))
        self._menu_icon_buttons.append(self.tools_button)
        self.tools_button.setText('')
        self.tools_button.setToolTip("工具")
        self.tools_button.setPopupMode(QToolButton.InstantPopup)
        self.tools_button.setMenu(tools_menu)
        header_layout.addWidget(self.tools_button)
        header_layout.addStretch()
        self.theme_combo = QComboBox()
        for key, title in theme_choices():
            self.theme_combo.addItem(title, key)
        self.theme_combo.currentIndexChanged.connect(self.apply_theme)
        header_layout.addWidget(self.theme_combo)
        outer.addWidget(header)

        work_area = QHBoxLayout()
        work_area.setContentsMargins(0, 0, 0, 0)
        work_area.setSpacing(0)
        outer.addLayout(work_area, 1)
        rail = QFrame()
        rail.setObjectName("Rail")
        rail.setFixedWidth(48)
        rail_layout = QVBoxLayout(rail)
        rail_layout.setContentsMargins(4, 8, 4, 8)
        rail_layout.setSpacing(4)
        self.isp_nav_button = QPushButton("◉")
        self.isp_nav_button.setObjectName("Nav")
        self.isp_nav_button.setCheckable(True)
        self.isp_nav_button.setToolTip("STC 烧录")
        self.isp_nav_button.clicked.connect(lambda: self.tabs.setCurrentIndex(0))
        rail_layout.addWidget(self.isp_nav_button)
        self.monitor_nav_button = QPushButton("≋")
        self.monitor_nav_button.setObjectName("Nav")
        self.monitor_nav_button.setCheckable(True)
        self.monitor_nav_button.setToolTip("串口数据监视")
        self.monitor_nav_button.clicked.connect(lambda: self._show_monitor_section(0))
        rail_layout.addWidget(self.monitor_nav_button)
        self.commands_nav_button = QPushButton("☷")
        self.commands_nav_button.setObjectName("Nav")
        self.commands_nav_button.setCheckable(True)
        self.commands_nav_button.setToolTip("命令")
        self.commands_nav_button.clicked.connect(lambda: self._show_monitor_section(1))
        rail_layout.addWidget(self.commands_nav_button)
        self.controls_nav_button = QPushButton("▣")
        self.controls_nav_button.setObjectName("Nav")
        self.controls_nav_button.setCheckable(True)
        self.controls_nav_button.setToolTip("控件")
        self.controls_nav_button.clicked.connect(lambda: self._show_monitor_section(2))
        rail_layout.addWidget(self.controls_nav_button)
        for button, kind, color in (
            (self.isp_nav_button, 'isp', palette.accent),
            (self.monitor_nav_button, 'connection', palette.muted),
            (self.commands_nav_button, 'commands', palette.muted),
            (self.controls_nav_button, 'controls', palette.muted),
        ):
            button.setText('')
            button.setFixedSize(40, 40)
            button.setIcon(navigation_icon(kind, color))
            button.setIconSize(QSize(24, 24))
        self._nav_buttons = ((self.isp_nav_button, 'isp'), (self.monitor_nav_button, 'connection'),
                             (self.commands_nav_button, 'commands'), (self.controls_nav_button, 'controls'))
        rail_layout.addStretch()
        work_area.addWidget(rail)

        tabs = QStackedWidget()
        self.tabs = tabs
        tabs.currentChanged.connect(self._update_navigation)
        work_area.addWidget(tabs, 1)
        isp_page = QWidget()
        isp_page.setObjectName("Root")
        isp_outer = QVBoxLayout(isp_page)
        isp_outer.setContentsMargins(0, 0, 0, 0)
        tabs.addWidget(isp_page)

        self.isp_splitter = AdjustableSplitter(Qt.Horizontal)
        isp_outer.addWidget(self.isp_splitter, 1)

        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        left_scroll.setMinimumWidth(240)
        self.isp_side_scroll = left_scroll
        left_host = QWidget()
        left_host.setObjectName("Root")
        left = QVBoxLayout(left_host)
        left.setContentsMargins(4, 0, 4, 0)
        left.setSpacing(0)
        left_scroll.setWidget(left_host)
        self.isp_splitter.addWidget(left_scroll)

        self.mode_combo = QComboBox(self)
        self.mode_combo.addItem("模拟演示", "simulator")
        self.mode_combo.addItem("STC 烧录", "hardware")
        self.mode_combo.setCurrentIndex(1)
        self.mode_combo.hide()
        self.mode_combo.currentIndexChanged.connect(self._update_controls)

        chip_card, chip = _card("目标芯片")
        self.chip_combo = QComboBox()
        for entry in self.catalog.entries:
            self.chip_combo.addItem(entry.name, entry.name)
        self.chip_combo.setEditable(True)
        self.chip_combo.setInsertPolicy(QComboBox.NoInsert)
        if self.chip_combo.completer() is not None:
            self.chip_combo.completer().setCompletionMode(QCompleter.PopupCompletion)
            self.chip_combo.completer().setFilterMode(Qt.MatchContains)
            self.chip_combo.completer().setCaseSensitivity(Qt.CaseInsensitive)
        known_index = self.chip_combo.findData("STC89C52RC/LE52RC")
        if known_index >= 0:
            self.chip_combo.setCurrentIndex(known_index)
        self.chip_combo.currentIndexChanged.connect(self._on_chip_changed)
        self.chip_combo.editTextChanged.connect(self._on_chip_changed)
        chip.addWidget(self.chip_combo)
        self._on_chip_changed()
        left.addWidget(chip_card)

        file_card, file_layout = _card("程序文件 · HEX / BIN")
        self.file_path_label = QLabel("尚未选择文件")
        self.file_path_label.setObjectName("Muted")
        self.file_path_label.setWordWrap(True)
        self.file_path_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        file_layout.addWidget(self.file_path_label)
        self.hex_summary = QLabel("大小 —  ·  地址 —")
        self.hex_summary.setObjectName("Value")
        file_layout.addWidget(self.hex_summary)
        self.bin_base_edit = QLineEdit("0000")
        self.bin_base_edit.setPlaceholderText("BIN 起始地址（十六进制）")
        self.bin_base_edit.setToolTip("只影响 BIN 文件载入；HEX 使用文件记录中的地址。修改后需重新选择文件。")
        base_row = QHBoxLayout()
        base_row.addWidget(QLabel("BIN 基址"))
        base_row.addWidget(self.bin_base_edit, 1)
        file_layout.addLayout(base_row)
        file_buttons = QHBoxLayout()
        self.choose_file_button = QPushButton("选择 HEX / BIN…")
        self.choose_file_button.clicked.connect(self.choose_hex)
        file_buttons.addWidget(self.choose_file_button)
        self.sample_button = QPushButton("加载示例")
        self.sample_button.clicked.connect(self.load_sample)
        file_buttons.addWidget(self.sample_button)
        file_layout.addLayout(file_buttons)
        buffer_buttons = QHBoxLayout()
        self.buffer_view_button = QPushButton("查看缓冲区")
        self.buffer_view_button.clicked.connect(lambda: self.show_firmware_buffer(self.image, "程序缓冲区"))
        buffer_buttons.addWidget(self.buffer_view_button)
        self.buffer_export_button = QPushButton("导出缓冲区…")
        self.buffer_export_button.clicked.connect(lambda: self.export_firmware_buffer(self.image))
        buffer_buttons.addWidget(self.buffer_export_button)
        file_layout.addLayout(buffer_buttons)
        self.eeprom_button = QPushButton("EEPROM 文件缓冲区…")
        self.eeprom_button.setToolTip("载入和查看 EEPROM 文件；当前不向设备写入 EEPROM。")
        self.eeprom_button.clicked.connect(self.choose_eeprom_file)
        file_layout.addWidget(self.eeprom_button)
        left.addWidget(file_card)

        port_card, port_layout = _card("串口设备")
        self.port_combo = QComboBox()
        self.port_combo.currentIndexChanged.connect(self._update_controls)
        port_layout.addWidget(self.port_combo)
        self.refresh_button = QPushButton("刷新串口")
        self.refresh_button.clicked.connect(self.refresh_ports)
        port_layout.addWidget(self.refresh_button)
        self.port_hint = QLabel()
        self.port_hint.setObjectName("Muted")
        self.port_hint.setWordWrap(True)
        port_layout.addWidget(self.port_hint)
        left.addWidget(port_card)

        action_card, action = _card("下载与识别")
        self.scenario_combo = QComboBox()
        self.scenario_combo.addItem("正常完成", None)
        self.scenario_combo.addItem("模拟超时", "timeout")
        self.scenario_combo.addItem("模拟错误帧校验", "bad_checksum")
        self.scenario_combo.addItem("模拟写入 ACK 错误", "bad_ack")
        self.scenario_combo.addItem("模拟设备断开", "disconnect")
        action.addWidget(self.scenario_combo)
        action_buttons = QHBoxLayout()
        self.start_button = QPushButton("开始模拟下载")
        self.start_button.setObjectName("Primary")
        self.start_button.clicked.connect(self.start_download)
        action_buttons.addWidget(self.start_button)
        self.cancel_button = QPushButton("取消")
        self.cancel_button.clicked.connect(self.cancel_simulation)
        action_buttons.addWidget(self.cancel_button)
        action.addLayout(action_buttons)
        self.power_button = QPushButton("模拟上电 · 继续握手")
        self.power_button.setObjectName("Power")
        self.power_button.clicked.connect(self.simulate_power_on)
        action.addWidget(self.power_button)
        self.replay_button = QPushButton("离线回放官方抓包…")
        self.replay_button.setToolTip("选择已核对的官方成功会话 JSONL，在内存中检查并回放，不连接 COM 口。")
        self.replay_button.clicked.connect(self.choose_official_replay)
        action.addWidget(self.replay_button)
        self.identify_button = QPushButton("只读识别 STC89 · 等待冷启动")
        self.identify_button.clicked.connect(self.start_readonly_identification)
        action.addWidget(self.identify_button)
        self.full_erase_check = QCheckBox("下载前擦除全部8KiB")
        self.full_erase_check.setToolTip("仅本次有效。默认按文件范围擦除；不修改EEPROM、ISP或选项。8KiB擦除ACK已实板复测，未做全区空白回读。")
        action.addWidget(self.full_erase_check)
        self.erase_button = QPushButton("擦除8KiB程序区…")
        self.erase_button.setToolTip("实验性：无需HEX。点击后等待冷上电，识别F002/66 43 FD后才请求擦除0x0000–0x1FFF；ACK不是空白回读。")
        self.erase_button.clicked.connect(self.start_full_erase)
        action.addWidget(self.erase_button)
        for selector in (self.mode_combo, self.port_combo, self.chip_combo):
            selector.currentIndexChanged.connect(lambda *_: self.full_erase_check.setChecked(False))
        left.addWidget(action_card)
        left.addStretch()

        self.isp_output_splitter = AdjustableSplitter(Qt.Vertical)
        self.isp_splitter.addWidget(self.isp_output_splitter)
        self.isp_splitter.set_default_sizes([318, 850])

        stage_card, stage_layout = _card("下载状态")
        self.status_label = QLabel("就绪")
        self.status_label.setObjectName("Status")
        self.status_label.setWordWrap(True)
        stage_layout.addWidget(self.status_label)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        stage_layout.addWidget(self.progress_bar)
        self.phase_labels: dict[SessionState, QLabel] = {}
        phase_grid = QHBoxLayout()
        phase_grid.setSpacing(5)
        for phase, label in _PHASES:
            widget = QLabel(label)
            widget.setAlignment(Qt.AlignCenter)
            widget.setStyleSheet(
                f"color: {theme_palette(self._theme).muted}; font-size: {scaled_px(11)}px; padding: 7px 2px;")
            phase_grid.addWidget(widget, 1)
            self.phase_labels[phase] = widget
        stage_layout.addLayout(phase_grid)
        self.isp_output_splitter.addWidget(stage_card)

        log_card, log_layout = _card("通信与事件日志")
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setPlaceholderText("通信日志")
        self.log_view.setMaximumBlockCount(5000)
        log_layout.addWidget(self.log_view, 1)
        log_buttons = QHBoxLayout()
        self.log_location_label = QLabel("运行日志")
        self.log_location_label.setToolTip(str(self._log_dir))
        self.log_location_label.setObjectName("Muted")
        self.log_location_label.setWordWrap(True)
        log_buttons.addWidget(self.log_location_label, 1)
        self.clear_log_button = QPushButton("清空显示")
        self.clear_log_button.clicked.connect(self.log_view.clear)
        log_buttons.addWidget(self.clear_log_button)
        log_layout.addLayout(log_buttons)
        self.isp_output_splitter.addWidget(log_card)
        self.isp_output_splitter.set_default_sizes([140, 600])


        self._build_monitor_page(tabs)
        self._update_navigation(0)

    def _update_navigation(self, index: int) -> None:
        self.isp_nav_button.setChecked(index == 0)
        section = self.monitor_sidebar_stack.currentIndex() if hasattr(self, 'monitor_sidebar_stack') else 0
        self.monitor_nav_button.setChecked(index == 1 and section == 0)
        self.commands_nav_button.setChecked(index == 1 and section == 1)
        self.controls_nav_button.setChecked(index == 1 and section == 2)
        self._tint_navigation()

    def _tint_navigation(self) -> None:
        """Rail icons follow the theme: current destination gold, the rest secondary."""

        palette = theme_palette(self._theme)
        for button, kind in getattr(self, '_nav_buttons', ()):
            button.setIcon(navigation_icon(kind, palette.accent if button.isChecked() else palette.muted))
        for button in getattr(self, '_menu_icon_buttons', ()):
            button.setIcon(navigation_icon('menu', palette.muted))

    def _show_monitor_section(self, section: int) -> None:
        self.monitor_sidebar_stack.setCurrentIndex(section)
        self.tabs.setCurrentIndex(1)
        self._update_navigation(1)

    def _build_monitor_page(self, tabs: QStackedWidget) -> None:
        page = QWidget()
        page.setObjectName("Root")
        layout = QHBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        tabs.addWidget(page)
        self.monitor_splitter = AdjustableSplitter(Qt.Horizontal)
        layout.addWidget(self.monitor_splitter)

        side_scroll = QScrollArea()
        side_scroll.setWidgetResizable(True)
        side_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        side_scroll.setMinimumWidth(220)
        self.monitor_side_scroll = side_scroll
        side_host = QWidget()
        side_host.setObjectName("Root")
        side = QVBoxLayout(side_host)
        side.setContentsMargins(4, 0, 4, 0)
        side.setSpacing(0)
        self.monitor_sidebar_stack = QStackedWidget()
        self.monitor_sidebar_stack.addWidget(side_host)
        side_scroll.setWidget(self.monitor_sidebar_stack)
        self.monitor_splitter.addWidget(side_scroll)

        connection_card, connection = _card("协议与连接")
        selectors = QGridLayout()
        selectors.setContentsMargins(0, 4, 0, 6)
        selectors.setVerticalSpacing(8)
        selectors.addWidget(QLabel("数据引擎"), 0, 0)
        selectors.addWidget(QLabel("数据接口"), 1, 0)
        connection.addLayout(selectors)
        self.source_combo = QComboBox()
        self.source_combo.addItem("串口", "serial")
        self.source_combo.addItem("Demo数据", "demo")
        self.source_combo.addItem("TCP 客户端", "tcp_client")
        self.source_combo.addItem("TCP 服务端", "tcp_server")
        self.source_combo.addItem("UDP", "udp")
        self.source_combo.addItem("CSV 回放", "csv")
        self.source_combo.addItem("图像目录回放", "images")
        self.source_combo.addItem("原始收发记录回放", "stream")
        self.source_combo.addItem("Demo CSV 数值矩阵", "matrix")
        self.source_combo.addItem("VOFA Demo · CSV + JPG", "compat_demo")
        self.source_combo.addItem("SWD 内存采集", "swd")
        self.source_combo.setToolTip("演示数据由本软件合成，不打开真实串口。")
        self.source_combo.currentIndexChanged.connect(self._update_monitor_controls)
        selectors.addWidget(self.source_combo, 1, 1)
        self.swd_panel = SwdSourcePanel()
        self.swd_panel.changed.connect(self._update_monitor_controls)
        self.swd_panel.changed.connect(self._mark_workspace_dirty)
        connection.addWidget(self.swd_panel)
        self.network_panel = QWidget()
        network_form = QGridLayout(self.network_panel)
        network_form.setContentsMargins(0, 0, 0, 0)
        self.network_host_edit = QLineEdit("127.0.0.1")
        self.network_port_edit = QLineEdit()
        self.network_port_edit.setPlaceholderText("端口")
        self.network_host_edit.setPlaceholderText("IPv4 地址")
        self.network_host_edit.setToolTip("TCP客户端填写目标地址；TCP服务端和UDP填写本地绑定地址。0.0.0.0会监听所有IPv4网卡。")
        self.network_host_edit.textChanged.connect(self._update_monitor_controls)
        self.network_port_edit.textChanged.connect(self._update_monitor_controls)
        network_form.addWidget(QLabel("地址"), 0, 0)
        network_form.addWidget(self.network_host_edit, 0, 1)
        network_form.addWidget(QLabel("端口"), 1, 0)
        network_form.addWidget(self.network_port_edit, 1, 1)
        self.udp_peer_panel = QWidget()
        peer_form = QGridLayout(self.udp_peer_panel)
        peer_form.setContentsMargins(0, 0, 0, 0)
        self.udp_peer_host_edit = QLineEdit()
        self.udp_peer_host_edit.setPlaceholderText("发送目标（可不填）")
        self.udp_peer_port_edit = QLineEdit()
        self.udp_peer_port_edit.setPlaceholderText("目标端口")
        self.udp_peer_host_edit.textChanged.connect(self._update_monitor_controls)
        self.udp_peer_port_edit.textChanged.connect(self._update_monitor_controls)
        peer_form.addWidget(self.udp_peer_host_edit, 0, 0)
        peer_form.addWidget(self.udp_peer_port_edit, 1, 0)
        network_form.addWidget(self.udp_peer_panel, 2, 0, 1, 2)
        connection.addWidget(self.network_panel)
        self.playback_panel = QWidget()
        playback_form = QVBoxLayout(self.playback_panel)
        playback_form.setContentsMargins(0, 0, 0, 0)
        self.playback_path_edit = QLineEdit()
        self.playback_path_edit.setReadOnly(True)
        self.playback_path_edit.setPlaceholderText("选择本软件导出的 CSV")
        playback_form.addWidget(self.playback_path_edit)
        self.playback_choose_button = QPushButton("选择 CSV…")
        self.playback_choose_button.clicked.connect(self.choose_playback_csv)
        playback_form.addWidget(self.playback_choose_button)
        self.matrix_directory_choose = QPushButton("选择 CSV 目录…")
        self.matrix_directory_choose.clicked.connect(self.choose_matrix_directory)
        playback_form.addWidget(self.matrix_directory_choose)
        playback_options = QHBoxLayout()
        self.playback_speed_spin = QDoubleSpinBox()
        self.playback_speed_spin.setRange(0.1, 20)
        self.playback_speed_spin.setValue(1)
        self.playback_speed_spin.setSuffix(" ×")
        playback_options.addWidget(self.playback_speed_spin)
        self.matrix_interval_spin = QSpinBox()
        self.matrix_interval_spin.setRange(1, 60000)
        self.matrix_interval_spin.setValue(100)
        self.matrix_interval_spin.setSuffix(" ms/行")
        playback_options.addWidget(self.matrix_interval_spin)
        self.playback_loop_check = QCheckBox("循环")
        playback_options.addWidget(self.playback_loop_check)
        self.playback_pause_button = QPushButton("暂停回放")
        self.playback_pause_button.setCheckable(True)
        self.playback_pause_button.toggled.connect(self.pause_csv_playback)
        playback_options.addWidget(self.playback_pause_button)
        playback_form.addLayout(playback_options)
        self.playback_progress = QProgressBar()
        self.playback_progress.setRange(0, 100)
        playback_form.addWidget(self.playback_progress)
        connection.addWidget(self.playback_panel)
        self.image_source_panel = QWidget()
        image_source_layout = QVBoxLayout(self.image_source_panel)
        image_source_layout.setContentsMargins(0, 0, 0, 0)
        self.image_directory_edit = QLineEdit()
        self.image_directory_edit.setReadOnly(True)
        self.image_directory_edit.setPlaceholderText("选择 JPEG / PNG 图像目录")
        image_source_layout.addWidget(self.image_directory_edit)
        self.image_directory_choose = QPushButton("选择图像目录…")
        self.image_directory_choose.clicked.connect(self.choose_image_directory)
        image_source_layout.addWidget(self.image_directory_choose)
        image_options = QHBoxLayout()
        self.image_fps_spin = QDoubleSpinBox()
        self.image_fps_spin.setRange(0.1, 60)
        self.image_fps_spin.setValue(10)
        self.image_fps_spin.setSuffix(" fps")
        image_options.addWidget(self.image_fps_spin)
        self.image_loop_check = QCheckBox("循环")
        image_options.addWidget(self.image_loop_check)
        self.image_pause_button = QPushButton("暂停")
        self.image_pause_button.setCheckable(True)
        self.image_pause_button.toggled.connect(self.pause_image_playback)
        image_options.addWidget(self.image_pause_button)
        image_source_layout.addLayout(image_options)
        self.image_progress = QProgressBar()
        self.image_progress.setRange(0, 100)
        image_source_layout.addWidget(self.image_progress)
        connection.addWidget(self.image_source_panel)
        self.demo_panel = QWidget()
        demo_form = QGridLayout(self.demo_panel)
        demo_form.setContentsMargins(0, 0, 0, 0)
        self.demo_channels_spin = QSpinBox()
        self.demo_channels_spin.setRange(1, 16)
        self.demo_channels_spin.setValue(3)
        self.demo_interval_spin = QSpinBox()
        self.demo_interval_spin.setRange(10, 10000)
        self.demo_interval_spin.setValue(100)
        self.demo_interval_spin.setSuffix(" ms")
        self.demo_amplitude_spin = QDoubleSpinBox()
        self.demo_amplitude_spin.setRange(-1e12, 1e12)
        self.demo_amplitude_spin.setValue(1)
        self.demo_offset_spin = QDoubleSpinBox()
        self.demo_offset_spin.setRange(-1e12, 1e12)
        for row, (text, widget) in enumerate((("通道数", self.demo_channels_spin), ("生成周期", self.demo_interval_spin), ("幅值", self.demo_amplitude_spin), ("偏置", self.demo_offset_spin))):
            demo_form.addWidget(QLabel(text), row, 0)
            demo_form.addWidget(widget, row, 1)
        connection.addWidget(self.demo_panel)
        self.compat_demo_panel = QWidget()
        compat_layout = QVBoxLayout(self.compat_demo_panel)
        compat_layout.setContentsMargins(0, 0, 0, 0)
        self.compat_decoder_combo = QComboBox()
        for name in ('FireWater', 'JustFloat'):
            self.compat_decoder_combo.addItem(name, name)
        compat_layout.addWidget(self.compat_decoder_combo)
        self.compat_sources = {}
        for kind, title in (('csv', 'CSV · 每周期整个文件'), ('jpg', 'JPG · 每周期一张')):
            enabled = QCheckBox(title)
            enabled.toggled.connect(self._update_monitor_controls)
            compat_layout.addWidget(enabled)
            path = QLineEdit()
            path.setPlaceholderText('选择目录（自然排序）')
            path.textChanged.connect(self._update_monitor_controls)
            row = QHBoxLayout()
            row.addWidget(path, 1)
            choose = QToolButton()
            choose.setText('…')
            choose.setAccessibleName('选择' + kind.upper() + '目录')
            choose.clicked.connect(lambda _checked=False, target=path: self._choose_compat_directory(target))
            row.addWidget(choose)
            compat_layout.addLayout(row)
            options = QHBoxLayout()
            period = QSpinBox()
            period.setRange(1, 60000)
            period.setValue(1000)
            period.setSuffix(' ms')
            loop = QCheckBox('循环')
            pause = QPushButton('暂停')
            pause.setCheckable(True)
            pause.toggled.connect(lambda paused, source=kind: self._pause_compat_source(source, paused))
            stop = QPushButton('停止')
            stop.clicked.connect(lambda _checked=False, source=kind: self._stop_compat_source(source))
            for widget in (period, loop, pause, stop):
                options.addWidget(widget)
            compat_layout.addLayout(options)
            self.compat_sources[kind] = (enabled, path, period, loop, pause, stop, choose)
        connection.addWidget(self.compat_demo_panel)
        self.monitor_port_search = QLineEdit()
        self.monitor_port_search.setMaxLength(256)
        self.monitor_port_search.setPlaceholderText("搜索 COM / 设备名称 / VID:PID")
        self.monitor_port_search.setToolTip("只筛选列表，不改变当前选择，不连接端口。")
        self.monitor_port_search.textChanged.connect(self._filter_monitor_ports)
        connection.addWidget(self.monitor_port_search)
        self.monitor_port_combo = QComboBox()
        self.monitor_port_combo.currentIndexChanged.connect(self._update_monitor_controls)
        connection.addWidget(self.monitor_port_combo)
        self.monitor_baud_edit = QLineEdit()
        self.monitor_baud_edit.setPlaceholderText("输入目标固件实际波特率，例如 115200")
        self.monitor_baud_edit.textChanged.connect(self._update_monitor_controls)
        connection.addWidget(self.monitor_baud_edit)
        self.serial_assistant_button = QPushButton("串口助手 · 常用配置")
        self.serial_assistant_button.setToolTip("仅填写115200/8N1、文本收发、CRLF和关闭DTR/RTS；不选择端口、不连接、不发送。")
        self.serial_assistant_button.clicked.connect(self.apply_serial_assistant_preset)
        connection.addWidget(self.serial_assistant_button)
        self.serial_format_panel = QWidget()
        serial_format_layout = QGridLayout(self.serial_format_panel)
        serial_format_layout.setContentsMargins(0, 0, 0, 0)
        self.serial_bytesize_combo = QComboBox()
        for size in (8, 7, 6, 5):
            self.serial_bytesize_combo.addItem(f"{size} 位", size)
        self.serial_parity_combo = QComboBox()
        for title, value in (("无校验", "N"), ("偶校验", "E"), ("奇校验", "O"), ("Mark", "M"), ("Space", "S")):
            self.serial_parity_combo.addItem(title, value)
        self.serial_stopbits_combo = QComboBox()
        for stop in (1, 1.5, 2):
            self.serial_stopbits_combo.addItem(f"{stop} 停止位", stop)
        for row, (label, widget) in enumerate((('数据位', self.serial_bytesize_combo), ('校验位', self.serial_parity_combo), ('停止位', self.serial_stopbits_combo))):
            serial_format_layout.addWidget(QLabel(label), row, 0)
            serial_format_layout.addWidget(widget, row, 1)
        line_controls = QHBoxLayout()
        self.serial_dtr_check = QCheckBox("DTR")
        self.serial_rts_check = QCheckBox("RTS")
        for check in (self.serial_dtr_check, self.serial_rts_check):
            check.setToolTip("连接前设置，勾选表示请求置位；断开后可修改。当前不保存到工程，加载工程后恢复关闭。")
            line_controls.addWidget(check)
        line_controls.addStretch(1)
        serial_format_layout.addLayout(line_controls, 3, 0, 1, 2)
        self.serial_flow_combo = QComboBox()
        self.serial_flow_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.serial_flow_combo.setMinimumContentsLength(7)
        self.serial_flow_combo.setMinimumWidth(0)
        for text, value in (("无流控", "none"), ("RTS/CTS 硬件流控", "rtscts"), ("XON/XOFF 软件流控", "xonxoff")):
            self.serial_flow_combo.addItem(text, value)
        serial_format_layout.addWidget(QLabel("流控"), 4, 0)
        serial_format_layout.addWidget(self.serial_flow_combo, 4, 1)
        connection.addWidget(self.serial_format_panel)
        self.decoder_combo = QComboBox()
        for decoder_name in ("FireWater", "JustFloat", "RawData"):
            self.decoder_combo.addItem(decoder_name, decoder_name)
        self.decoder_combo.addItem("JPEG / PNG", "ImageStream")
        self.decoder_combo.addItem("FireWater · 图像", "FireWaterImage")
        self.decoder_combo.addItem("JustFloat · 图像", "JustFloatImage")
        selectors.addWidget(self.decoder_combo, 0, 1)
        self.monitor_risk = QCheckBox("我了解打开串口的电平风险")
        self.monitor_risk.setToolTip("打开 COM 口可能改变 DTR/RTS 电平，请先确认目标板供电方式。")
        self.monitor_risk.toggled.connect(self._update_monitor_controls)
        connection.addWidget(self.monitor_risk)
        monitor_buttons = QHBoxLayout()
        self.monitor_connect_button = QPushButton("连接并接收")
        self.monitor_connect_button.setObjectName("Primary")
        self.monitor_connect_button.clicked.connect(self.start_monitor)
        monitor_buttons.addWidget(self.monitor_connect_button)
        self.monitor_disconnect_button = QPushButton("断开")
        self.monitor_disconnect_button.clicked.connect(self.stop_monitor)
        monitor_buttons.addWidget(self.monitor_disconnect_button)
        connection.addLayout(monitor_buttons)
        self.monitor_status_label = QLabel("未连接")
        self.monitor_status_label.setObjectName("Status")
        self.monitor_status_label.setWordWrap(True)
        connection.addWidget(self.monitor_status_label)
        side.addWidget(connection_card)

        workspace_card, workspace = _card("通道与工作区")
        self.channel_names_edit = QLineEdit()
        self.channel_names_edit.setPlaceholderText("通道名称，以英文逗号分隔；留空使用 CH1、CH2…")
        workspace.addWidget(self.channel_names_edit)
        file_buttons = QHBoxLayout()
        self.workspace_save_button = QPushButton("保存工作区")
        self.workspace_save_button.clicked.connect(self.save_monitor_workspace)
        file_buttons.addWidget(self.workspace_save_button)
        self.workspace_load_button = QPushButton("载入工作区")
        self.workspace_load_button.clicked.connect(self.load_monitor_workspace)
        file_buttons.addWidget(self.workspace_load_button)
        workspace.addLayout(file_buttons)
        self.workspace_native_button = QPushButton("导入 VOFA+ 配置")
        self.workspace_native_button.setToolTip("只读导入 vofa+.config.json 及其同目录上下文；含原生卡页/控件或命令组时拒绝")
        self.workspace_native_button.clicked.connect(self.import_monitor_native_context)
        workspace.addWidget(self.workspace_native_button)
        data_buttons = QHBoxLayout()
        self.monitor_export_button = QPushButton("导出采样 CSV")
        self.monitor_export_button.clicked.connect(self.export_monitor_csv)
        data_buttons.addWidget(self.monitor_export_button)
        self.monitor_clear_button = QPushButton("清空数据")
        self.monitor_clear_button.clicked.connect(self.clear_monitor_data)
        data_buttons.addWidget(self.monitor_clear_button)
        workspace.addLayout(data_buttons)
        self.monitor_record_button = QPushButton("开始实时记录 CSV…")
        self.monitor_record_button.clicked.connect(self.toggle_sample_recording)
        workspace.addWidget(self.monitor_record_button)
        self.stream_record_button = QPushButton("录制原始 RX/TX…")
        self.stream_record_button.clicked.connect(self.toggle_stream_recording)
        workspace.addWidget(self.stream_record_button)
        self.record_status_label = QLabel("实时记录未开始")
        self.record_status_label.setObjectName("Muted")
        self.record_status_label.setWordWrap(True)
        workspace.addWidget(self.record_status_label)
        self.sample_count_label = QLabel("采样点：0")
        self.sample_count_label.setObjectName("Muted")
        self.sample_count_label.setWordWrap(True)
        workspace.addWidget(self.sample_count_label)
        self.link_metrics_label = QLabel("RX 0 B/s · TX 0 B/s")
        self.link_metrics_label.setObjectName("Muted")
        self.link_metrics_label.setWordWrap(True)
        self.link_metrics_label.setToolTip("软件到达速率；不是芯片采样时钟测量，也不是写入确认。")
        workspace.addWidget(self.link_metrics_label)
        side.addWidget(workspace_card)

        side.addStretch()

        center_host = QWidget()
        content = QVBoxLayout(center_host)
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(0)
        self.monitor_splitter.addWidget(center_host)
        self.monitor_plot_splitter = AdjustableSplitter(Qt.Vertical)
        content.addWidget(self.monitor_plot_splitter, 1)
        wave_card, wave_layout = _card("多通道波形 · 本次采样")
        wave_layout.takeAt(0).widget().deleteLater()
        wave_layout.setContentsMargins(6, 4, 6, 4)
        self.waveform = WaveformWidget()
        self.waveform.setMinimumHeight(120)
        wave_controls = QHBoxLayout()
        self.wave_pause_button = QPushButton("暂停显示")
        self.wave_pause_button.setCheckable(True)
        self.wave_pause_button.toggled.connect(lambda paused: self._plot_target().set_paused(paused) if self._plot_target() is not None else None)
        wave_controls.addWidget(self.wave_pause_button)
        self.wave_reset_button = QPushButton("自动适应")
        self.wave_reset_button.clicked.connect(lambda: self._plot_target().reset_view() if self._plot_target() is not None else None)
        wave_controls.addWidget(self.wave_reset_button)
        self.plot_settings_button = QPushButton("显示设置…")
        self.plot_settings_button.clicked.connect(self.configure_plot)
        wave_controls.addWidget(self.plot_settings_button)
        self.plot_export_button = QPushButton("导出图…")
        self.plot_export_button.clicked.connect(self.export_plot_image)
        wave_controls.addWidget(self.plot_export_button)
        self.plot_target_label = QLabel('操作目标：未选择波形卡')
        self.plot_target_label.setMinimumWidth(0)
        self.plot_target_label.setMaximumWidth(170)
        self.plot_target_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        wave_controls.insertWidget(0, self.plot_target_label, 1)
        time_controls = QHBoxLayout()
        time_controls.addWidget(QLabel("Δt / ms"))
        self.sample_interval_spin = QDoubleSpinBox()
        self.sample_interval_spin.setDecimals(3)
        self.sample_interval_spin.setRange(0.001, 1_000_000)
        self.sample_interval_spin.setValue(1)
        self.sample_interval_spin.valueChanged.connect(self.waveform.set_sample_interval_ms)
        time_controls.addWidget(self.sample_interval_spin)
        time_controls.addWidget(QLabel("缓冲"))
        self.wave_buffer_spin = QSpinBox()
        self.wave_buffer_spin.setRange(100, 100000)
        self.wave_buffer_spin.setValue(800)
        self.wave_buffer_spin.valueChanged.connect(lambda value: self._plot_target().set_max_samples(value) if self._plot_target() is not None else None)
        time_controls.addWidget(self.wave_buffer_spin)
        self.sample_interval_spin.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
        self.wave_buffer_spin.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
        self.visual_tabs = QTabWidget()
        self.visual_tabs.addTab(self.waveform, "波形")
        self.control_workspace = ControlWorkspace()
        self.control_workspace.packet_requested.connect(self._send_control_packet)
        self.control_workspace.error.connect(self._append_monitor_log)
        self.control_workspace.set_image_store(self._image_store)
        self.visual_tabs.addTab(self.control_workspace, "控件")
        self.image_view = ImageViewWidget()
        self.image_view.set_theme(self._theme)
        self.image_view.error.connect(self._append_monitor_log)
        self.image_view.status.connect(self._append_monitor_log)
        self.image_view.save_requested.connect(self.save_current_image)
        image_page = QWidget()
        image_page_layout = QVBoxLayout(image_page)
        image_page_layout.setContentsMargins(0, 0, 0, 0)
        image_toolbar = QGridLayout()
        for column, (title, callback) in enumerate((("打开图片…", self.open_local_image), ("保存图像…", self.save_current_image), ("导出载荷…", self.save_image_payload))):
            button = QPushButton(title)
            button.clicked.connect(callback)
            image_toolbar.addWidget(button, 0, column)
        self.image_rx_check = QCheckBox("标准 JPEG/PNG 字节流")
        self.image_rx_check.setToolTip("独立标准图像流模式，不解释 VOFA 私有封装。")
        image_toolbar.addWidget(self.image_rx_check, 1, 0, 1, 2)
        self.image_channel_combo = QComboBox()
        for index in range(16):
            self.image_channel_combo.addItem(f'图像 {index}', index)
        self.image_channel_combo.currentIndexChanged.connect(self._show_cached_image)
        image_toolbar.addWidget(self.image_channel_combo, 1, 2)
        image_page_layout.addLayout(image_toolbar)
        image_page_layout.addWidget(self.image_view, 1)
        self.visual_tabs.addTab(image_page, "图像")
        self.analysis_view = AnalysisView(self.waveform)
        self.analysis_view.set_theme(self._theme)
        self.visual_tabs.addTab(self.analysis_view, "统计 / 频谱")
        old_tabs = self.visual_tabs
        self.dashboard = DashboardManager(legacy_widgets={
            'waveform': self.waveform, 'control': self.control_workspace,
            'image': image_page, 'analysis': self.analysis_view,
        })
        self.dashboard.set_image_store(self._image_store)
        self.dashboard.packet_requested.connect(self._send_control_packet)
        self.dashboard.configuration_changed.connect(self._mark_workspace_dirty)
        self.dashboard.active_card_changed.connect(self._sync_active_plot)
        self.dashboard.error.connect(self._append_monitor_log)
        self.dashboard.widgets_rebuilt.connect(self._dashboard_widgets_rebuilt)
        self.sample_interval_spin.valueChanged.connect(self.dashboard.set_sample_interval_ms)
        self.visual_tabs = self.dashboard.tabs
        self.visual_tabs.currentChanged.connect(lambda _index: self._paint_dashboard())
        old_tabs.deleteLater()
        wave_layout.addWidget(self.dashboard, 1)
        self.cursor_value_label = QLabel("")
        self.cursor_value_label.setObjectName("Muted")
        self.cursor_value_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.cursor_value_label.setMinimumWidth(0)
        self.cursor_value_label.setToolTip("滚轮缩放 · 拖动平移 · Home 自动适应 · Space 暂停")
        self.waveform.cursor_text_changed.connect(self.cursor_value_label.setText)
        time_controls.addWidget(self.cursor_value_label, 1)
        action_group, settings_group = QWidget(), QWidget()
        for group, group_layout in ((action_group, wave_controls), (settings_group, time_controls)):
            group_layout.setContentsMargins(0, 0, 0, 0)
            group_layout.setSpacing(4)
            group.setLayout(group_layout)
        self.plot_toolbar = PlotToolbar(action_group, settings_group)
        wave_layout.addWidget(self.plot_toolbar)
        self.monitor_plot_splitter.addWidget(wave_card)
        raw_card, raw_layout = _card("数据收发 · 原始字节 / 解析结果")
        raw_layout.takeAt(0).widget().deleteLater()
        raw_layout.setContentsMargins(6, 4, 6, 4)
        raw_layout.setSpacing(3)
        rx_controls = QHBoxLayout()
        self.rx_display_combo = QComboBox()
        self.rx_display_combo.addItem("HEX 接收", "hex")
        self.rx_display_combo.addItem("UTF-8 接收", "text")
        rx_controls.addWidget(self.rx_display_combo)
        self.rx_status_label = QLabel("RX：0 字节")
        self.rx_status_label.setObjectName("Muted")
        rx_controls.addWidget(self.rx_status_label, 1)
        terminal_menu = QMenu(self)
        self.show_rx_action = terminal_menu.addAction("显示 RX")
        self.show_tx_action = terminal_menu.addAction("显示 TX")
        self.show_timestamps_action = terminal_menu.addAction("显示时间戳")
        for action in (self.show_rx_action, self.show_tx_action, self.show_timestamps_action):
            action.setCheckable(True)
            action.setChecked(True)
        self.pause_terminal_action = terminal_menu.addAction("暂停终端显示（继续接收）")
        self.pause_terminal_action.setCheckable(True)
        encoding_menu = terminal_menu.addMenu("文本接收编码")
        for encoding in ('utf-8', 'gbk', 'latin-1', 'ascii', 'utf-16le'):
            encoding_menu.addAction(encoding.upper(), lambda checked=False, selected=encoding: self.set_receive_encoding(selected))
        terminal_menu.addAction("字体大小…", self.change_terminal_font)
        terminal_menu.addAction("查找…", self.find_terminal_text)
        terminal_menu.addAction("保存当前显示…", self.save_terminal_text)
        terminal_menu.addAction("清空当前显示", self.clear_terminal_text)
        terminal_button = QToolButton()
        terminal_button.setIcon(navigation_icon('menu', theme_palette(self._theme).muted))
        self._menu_icon_buttons.append(terminal_button)
        terminal_button.setPopupMode(QToolButton.InstantPopup)
        terminal_button.setMenu(terminal_menu)
        rx_controls.addWidget(terminal_button)
        self.serial_tools_button = QPushButton("报文 / 协议…")
        self.serial_tools_button.clicked.connect(self.open_serial_tools)
        rx_controls.addWidget(self.serial_tools_button)
        self.send_history_button = QToolButton()
        self.send_history_button.setText("历史")
        self.send_history_button.setPopupMode(QToolButton.InstantPopup)
        self.send_history_menu = QMenu(self)
        self.send_history_menu.aboutToShow.connect(self._refresh_send_history)
        self.send_history_button.setMenu(self.send_history_menu)
        rx_controls.addWidget(self.send_history_button)
        raw_layout.addLayout(rx_controls)
        self.monitor_log_view = TerminalView()
        self.monitor_log_view.setPlaceholderText("连接后显示 RX；发送按钮与周期开关由你手动操作。")
        self.monitor_log_view.setMaximumBlockCount(3000)
        self.monitor_log_view.setMinimumHeight(scaled_px(64))
        raw_layout.addWidget(self.monitor_log_view, 1)
        send_row = QHBoxLayout()
        self.send_text_edit = QLineEdit()
        self.send_text_edit.setMaxLength(16384)
        self.send_text_edit.setPlaceholderText("发送内容…")
        self.send_text_edit.setToolTip("手动发送内容；HEX 示例：01 03 FF。连接本身不发送，定时发送默认关闭。")
        self.send_text_edit.returnPressed.connect(self.send_monitor_data)
        send_row.addWidget(self.send_text_edit, 1)
        self.send_mode_combo = QComboBox()
        self.send_mode_combo.addItem("文本", "text")
        self.send_mode_combo.addItem("HEX", "hex")
        send_row.addWidget(self.send_mode_combo)
        self.tx_encoding_combo = QComboBox()
        for encoding in ('utf-8', 'gbk', 'latin-1', 'ascii', 'utf-16le'):
            self.tx_encoding_combo.addItem(encoding.upper(), encoding)
        self.tx_encoding_combo.setToolTip("发送编码；独立于接收显示编码")
        self.tx_encoding_combo.currentIndexChanged.connect(
            lambda: self.set_transmit_encoding(self.tx_encoding_combo.currentData()))
        send_row.addWidget(self.tx_encoding_combo)
        self.send_ending_combo = QComboBox()
        self.send_ending_combo.addItem("无换行", "none")
        self.send_ending_combo.addItem("LF", "LF")
        self.send_ending_combo.addItem("CRLF", "CRLF")
        self.send_ending_combo.addItem("CR", "CR")
        self.send_ending_combo.addItem("TAB", "TAB")
        send_row.addWidget(self.send_ending_combo)
        self.send_button = QPushButton("发送")
        self.send_button.clicked.connect(self.send_monitor_data)
        send_row.addWidget(self.send_button)
        self.send_file_button = QPushButton("文件…")
        self.send_file_button.setToolTip("将所选文件作为原始字节发送；不会自动解释为固件烧录。")
        self.send_file_button.clicked.connect(self.send_monitor_file)
        send_row.addWidget(self.send_file_button)
        raw_layout.addLayout(send_row)
        self.send_preview_label = QLabel("实际发送：0 字节")
        self.send_preview_label.setObjectName("Muted")
        self.send_preview_label.setMinimumWidth(0)
        self.send_preview_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        raw_layout.addWidget(self.send_preview_label)
        self.send_text_edit.textChanged.connect(self._update_send_preview)
        self.send_mode_combo.currentIndexChanged.connect(self._update_send_preview)
        self.send_ending_combo.currentIndexChanged.connect(self._update_send_preview)
        periodic_row = QHBoxLayout()
        self.periodic_send_check = QCheckBox("周期发送")
        self.periodic_send_check.toggled.connect(self._toggle_periodic_send)
        periodic_row.addWidget(self.periodic_send_check)
        self.send_interval_spin = QSpinBox()
        self.send_interval_spin.setRange(20, 60000)
        self.send_interval_spin.setValue(1000)
        self.send_interval_spin.setSuffix(" ms")
        self.send_interval_spin.valueChanged.connect(self._periodic_timer.setInterval)
        periodic_row.addWidget(self.send_interval_spin)
        self.tx_status_label = QLabel("TX：0 字节")
        self.tx_status_label.setObjectName("Muted")
        self.tx_status_label.setWordWrap(True)
        rx_controls.insertWidget(2, self.tx_status_label)
        periodic_panel = QWidget()
        periodic_row.setContentsMargins(8, 4, 8, 4)
        periodic_panel.setLayout(periodic_row)
        periodic_action = QWidgetAction(terminal_menu)
        periodic_action.setDefaultWidget(periodic_panel)
        terminal_menu.addSeparator()
        terminal_menu.addSection('周期发送（手动启用）')
        terminal_menu.addAction(periodic_action)
        job_row = QHBoxLayout()
        self.send_job_label = QLabel("发送任务")
        self.send_job_label.setObjectName("Muted")
        job_row.addWidget(self.send_job_label)
        self.send_job_progress = QProgressBar()
        self.send_job_progress.setRange(0, 100)
        self.send_job_progress.setValue(0)
        self.send_job_progress.setFormat("未开始")
        job_row.addWidget(self.send_job_progress, 1)
        self.send_job_stop_button = QPushButton("停止")
        self.send_job_stop_button.clicked.connect(self.stop_send_job)
        job_row.addWidget(self.send_job_stop_button)
        self.send_job_panel = QWidget()
        job_row.setContentsMargins(0, 0, 0, 0)
        self.send_job_panel.setLayout(job_row)
        raw_layout.addWidget(self.send_job_panel)
        self.send_job_panel.hide()
        self.monitor_plot_splitter.addWidget(raw_card)
        self.monitor_plot_splitter.setStretchFactor(0, 3)
        self.monitor_plot_splitter.setStretchFactor(1, 1)
        self.monitor_plot_splitter.set_default_sizes([750, 250])

        values_card, values_layout = _card("数据 · 当前采样")
        precision_row = QHBoxLayout()
        precision_row.addWidget(QLabel("小数位数"))
        self.precision_spin = QSpinBox()
        self.precision_spin.setRange(0, 12)
        self.precision_spin.setValue(6)
        self.precision_spin.valueChanged.connect(self._refresh_channel_values)
        precision_row.addWidget(self.precision_spin)
        values_layout.addLayout(precision_row)
        self.channel_visibility_checks: list[QCheckBox] = []
        visible_layout = QGridLayout()
        for index in range(16):
            check = QCheckBox(f"CH{index + 1}")
            check.setChecked(True)
            check.toggled.connect(lambda visible, channel=index: self._plot_target().set_channel_visible(channel, visible) if self._plot_target() is not None else None)
            check.setContextMenuPolicy(Qt.CustomContextMenu)
            check.customContextMenuRequested.connect(lambda point, channel=index: self.channel_context_menu(channel, point))
            visible_layout.addWidget(check, index // 2, index % 2)
            self.channel_visibility_checks.append(check)
        values_layout.addLayout(visible_layout)
        self.channel_value_labels: list[QLabel] = []
        for channel_index in range(16):
            value_label = QLabel(f"CH{channel_index + 1}    —")
            value_label.setTextFormat(Qt.PlainText)
            value_label.setStyleSheet(
                f"color: {WaveformWidget._COLORS[channel_index % len(WaveformWidget._COLORS)]};"
                f"font-family: {FONT_MONO}; font-size: {scaled_px(12)}px; font-weight: 700; padding: 5px 1px;"
            )
            value_label.setWordWrap(True)
            values_layout.addWidget(value_label)
            self.channel_value_labels.append(value_label)
        values_layout.addStretch()
        self.monitor_values_scroll = QScrollArea()
        self.monitor_values_scroll.setWidgetResizable(True)
        self.monitor_values_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.monitor_values_scroll.setWidget(values_card)
        self.monitor_values_scroll.setMinimumWidth(170)
        from .vofa.measurement_panel import MeasurementPanel
        self.measurement_panel = MeasurementPanel(self._measurement_source)
        self.monitor_values_tabs = QTabWidget()
        self.monitor_values_tabs.setMinimumWidth(170)
        self.monitor_values_tabs.addTab(self.monitor_values_scroll, '数据')
        self.monitor_values_tabs.addTab(self.measurement_panel, '测量')
        self.monitor_splitter.addWidget(self.monitor_values_tabs)
        self.monitor_splitter.set_default_sizes([280, 700, 190])
        self._build_command_controls_pages()
        self._update_monitor_controls()

    def _build_command_controls_pages(self) -> None:
        command_page = QWidget()
        command_layout = QVBoxLayout(command_page)
        command_layout.setContentsMargins(16, 12, 16, 12)
        command_heading = QHBoxLayout()
        title = QLabel('命令')
        title.setObjectName('SectionTitle')
        command_heading.addWidget(title, 1)
        menu = QMenu(self)
        menu.addAction('新增…', self.add_command)
        menu.addAction('载入…', self.load_command_list)
        menu.addAction('保存…', self.save_command_list)
        menu.addSeparator()
        menu.addAction('清空', self.clear_command_list)
        button = QToolButton()
        button.setIcon(navigation_icon('menu', theme_palette(self._theme).muted))
        self._menu_icon_buttons.append(button)
        button.setMenu(menu)
        button.setPopupMode(QToolButton.InstantPopup)
        command_heading.addWidget(button)
        command_layout.addLayout(command_heading)
        group_row = QHBoxLayout()
        self.command_group_combo = QComboBox()
        self.command_group_combo.currentIndexChanged.connect(self._select_command_group)
        group_row.addWidget(self.command_group_combo, 1)
        group_menu = QMenu(self)
        group_menu.addAction('新增命令组…', self.add_command_group)
        group_menu.addAction('重命名命令组…', self.rename_command_group)
        group_menu.addAction('命令组上移', lambda: self.move_command_group(-1))
        group_menu.addAction('命令组下移', lambda: self.move_command_group(1))
        group_menu.addSeparator()
        group_menu.addAction('删除命令组…', self.remove_command_group)
        group_button = QToolButton()
        group_button.setText('⋮')
        group_button.setMenu(group_menu)
        group_button.setPopupMode(QToolButton.InstantPopup)
        group_button.setAccessibleName('管理命令组')
        group_row.addWidget(group_button)
        command_layout.addLayout(group_row)
        self.command_list = QListWidget()
        self.command_list.itemDoubleClicked.connect(lambda _item: self.send_selected_command())
        self.command_list.setDragDropMode(QAbstractItemView.InternalMove)
        self.command_list.model().rowsMoved.connect(self._command_rows_moved)
        self.command_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.command_list.customContextMenuRequested.connect(self._command_context_menu)
        command_layout.addWidget(self.command_list, 1)
        command_buttons = QHBoxLayout()
        add = QPushButton('新增')
        add.clicked.connect(self.add_command)
        command_buttons.addWidget(add)
        self.command_send_button = QPushButton('发送')
        self.command_send_button.clicked.connect(self.send_selected_command)
        command_buttons.addWidget(self.command_send_button)
        command_layout.addLayout(command_buttons)
        sequence_settings = QGridLayout()
        sequence_settings.addWidget(QLabel('间隔'), 0, 0)
        self.sequence_interval_spin = QSpinBox()
        self.sequence_interval_spin.setRange(20, 60000)
        self.sequence_interval_spin.setValue(1000)
        self.sequence_interval_spin.setSuffix(' ms')
        sequence_settings.addWidget(self.sequence_interval_spin, 0, 1)
        sequence_settings.addWidget(QLabel('重复'), 1, 0)
        self.sequence_repeat_spin = QSpinBox()
        self.sequence_repeat_spin.setRange(1, 10000)
        self.sequence_repeat_spin.setValue(1)
        self.sequence_repeat_spin.setSuffix(' 次')
        sequence_settings.addWidget(self.sequence_repeat_spin, 1, 1)
        command_layout.addLayout(sequence_settings)
        self.command_sequence_button = QPushButton('发送本组')
        self.command_sequence_button.clicked.connect(self.start_command_sequence)
        command_layout.addWidget(self.command_sequence_button)
        self._refresh_command_groups()
        self.monitor_sidebar_stack.addWidget(command_page)
        control_page = QWidget()
        controls = QVBoxLayout(control_page)
        controls.setContentsMargins(16, 12, 16, 12)
        heading = QLabel('控件')
        heading.setObjectName('SectionTitle')
        controls.addWidget(heading)
        self.control_channel_spin = QSpinBox()
        self.control_channel_spin.setRange(1, 16)
        self.control_channel_spin.setPrefix('CH ')
        controls.addWidget(self.control_channel_spin)
        self.control_title_edit = QLineEdit()
        self.control_title_edit.setMaxLength(80)
        self.control_title_edit.setPlaceholderText('控件名称')
        controls.addWidget(self.control_title_edit)
        self.control_payload_edit = QLineEdit()
        self.control_payload_edit.setMaxLength(4096)
        self.control_payload_edit.setPlaceholderText('按钮发送内容')
        controls.addWidget(self.control_payload_edit)
        self.control_kind_combo = QComboBox()
        for kind, name in CONTROL_LABELS.items():
            self.control_kind_combo.addItem(name, kind)
        controls.addWidget(self.control_kind_combo)
        add = QPushButton('添加可视控件…')
        add.clicked.connect(lambda: self.add_visual_control(self.control_kind_combo.currentData(), prompt=True))
        controls.addWidget(add)
        save = QPushButton('保存控件…')
        save.clicked.connect(self.save_visual_controls)
        controls.addWidget(save)
        load = QPushButton('载入控件…')
        load.clicked.connect(self.load_visual_controls)
        controls.addWidget(load)
        clear = QPushButton('清空控件')
        clear.clicked.connect(self.clear_visual_controls)
        controls.addWidget(clear)
        controls.addStretch()
        self.monitor_sidebar_stack.addWidget(control_page)

    @Slot()
    def add_command(self) -> None:
        if len(self._commands) >= 64:
            QMessageBox.warning(self, '命令数量已满', '最多保存64条命令。')
            return
        dialog = QDialog(self)
        dialog.setWindowTitle('新增命令')
        form = QVBoxLayout(dialog)
        name = QLineEdit()
        name.setPlaceholderText('名称')
        name.setMaxLength(80)
        form.addWidget(name)
        payload = QLineEdit()
        payload.setPlaceholderText('发送内容')
        payload.setMaxLength(16384)
        form.addWidget(payload)
        mode = QComboBox()
        mode.addItem('文本', 'text')
        mode.addItem('HEX', 'hex')
        form.addWidget(mode)
        ending = QComboBox()
        for label, value in (('无换行', 'none'), ('LF', 'LF'), ('CRLF', 'CRLF'), ('CR', 'CR'), ('TAB', 'TAB')):
            ending.addItem(label, value)
        form.addWidget(ending)
        encoding = QComboBox()
        for codec in ('utf-8', 'gbk', 'latin-1', 'ascii', 'utf-16le'):
            encoding.addItem(codec.upper(), codec)
        encoding.setCurrentIndex(encoding.findData(self._tx_encoding))
        form.addWidget(encoding)
        save = QPushButton('添加')
        save.clicked.connect(dialog.accept)
        form.addWidget(save)
        if dialog.exec() != QDialog.Accepted:
            return
        try:
            entry = CommandEntry(name.text(), payload.text(), mode.currentData(), ending.currentData(), encoding.currentData())
        except ValueError as exc:
            QMessageBox.warning(self, '命令无效', str(exc))
            return
        self._commands.append(entry)
        self._refresh_command_list()

    def _sync_command_group(self) -> None:
        while len(self._command_ids) < len(self._commands):
            self._command_ids.append('command-' + uuid4().hex)
        self._command_ids = self._command_ids[:len(self._commands)]
        for index, group in enumerate(self._command_groups):
            if group.id == self._command_group_id:
                self._command_groups[index] = CommandGroup(group.id, group.name, tuple(
                    CommandItem(identifier, entry) for identifier, entry in zip(self._command_ids, self._commands)))
                break

    def _refresh_command_groups(self) -> None:
        self.command_group_combo.blockSignals(True)
        self.command_group_combo.clear()
        for group in self._command_groups:
            self.command_group_combo.addItem(group.name, group.id)
        self.command_group_combo.setCurrentIndex(max(0, self.command_group_combo.findData(self._command_group_id)))
        self.command_group_combo.blockSignals(False)

    def _select_command_group(self, _index: int) -> None:
        selected = self.command_group_combo.currentData()
        if not selected or selected == self._command_group_id:
            return
        self._sync_command_group()
        group = next(group for group in self._command_groups if group.id == selected)
        self._command_group_id = group.id
        self._commands = [item.entry for item in group.commands]
        self._command_ids = [item.id for item in group.commands]
        self._refresh_command_list()

    def add_command_group(self) -> None:
        if len(self._command_groups) >= 32:
            QMessageBox.warning(self, '命令组已满', '最多32个命令组。')
            return
        name, accepted = QInputDialog.getText(self, '新增命令组', '组名（1–80字）')
        if accepted and name.strip():
            self._sync_command_group()
            try:
                group = CommandGroup('group-' + uuid4().hex, name.strip())
            except ValueError as exc:
                QMessageBox.warning(self, '组名无效', str(exc))
                return
            self._command_groups.append(group)
            self._refresh_command_groups()
            self.command_group_combo.setCurrentIndex(self.command_group_combo.findData(group.id))
            self._mark_workspace_dirty()

    def rename_command_group(self) -> None:
        self._sync_command_group()
        index = next(index for index, group in enumerate(self._command_groups) if group.id == self._command_group_id)
        group = self._command_groups[index]
        name, accepted = QInputDialog.getText(self, '重命名命令组', '组名', text=group.name)
        if accepted and name.strip():
            try:
                self._command_groups[index] = CommandGroup(group.id, name.strip(), group.commands)
            except ValueError as exc:
                QMessageBox.warning(self, '组名无效', str(exc))
                return
            self._refresh_command_groups()
            self._mark_workspace_dirty()

    def move_command_group(self, delta: int) -> None:
        self._sync_command_group()
        index = next(index for index, group in enumerate(self._command_groups) if group.id == self._command_group_id)
        target = index + delta
        if 0 <= target < len(self._command_groups):
            self._command_groups.insert(target, self._command_groups.pop(index))
            self._refresh_command_groups()
            self._mark_workspace_dirty()

    def remove_command_group(self) -> None:
        self._sync_command_group()
        group = next(group for group in self._command_groups if group.id == self._command_group_id)
        if QMessageBox.question(self, '删除命令组',
            f'删除1组“{group.name}”及组内{len(group.commands)}条命令？已保存文件不变，可重新载入恢复；未保存内容无法恢复。',
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        self._command_groups = [item for item in self._command_groups if item.id != group.id] or list(migrate_command_groups(()))
        selected = self._command_groups[0]
        self._command_group_id = selected.id
        self._commands = [item.entry for item in selected.commands]
        self._command_ids = [item.id for item in selected.commands]
        self._refresh_command_groups()
        self._refresh_command_list()

    def _refresh_command_list(self) -> None:
        self._sync_command_group()
        selected = self.command_list.currentRow()
        self.command_list.clear()
        for identifier, entry in zip(self._command_ids, self._commands):
            item = QListWidgetItem(entry.name)
            item.setData(Qt.UserRole, entry)
            item.setData(Qt.UserRole + 1, identifier)
            item.setToolTip(f'{entry.mode} · {entry.ending}\n{entry.payload[:256]}')
            self.command_list.addItem(item)
        if self._commands:
            self.command_list.setCurrentRow(min(max(selected, 0), len(self._commands) - 1))
        self.command_sequence_button.setEnabled(self._can_send_data() and bool(self._commands) and not self._send_job.active)
        self._mark_workspace_dirty()

    def _command_rows_moved(self, *_args) -> None:
        self._commands = [self.command_list.item(index).data(Qt.UserRole) for index in range(self.command_list.count())]
        self._command_ids = [self.command_list.item(index).data(Qt.UserRole + 1) for index in range(self.command_list.count())]
        self._sync_command_group()
        self._mark_workspace_dirty()

    def _command_context_menu(self, position) -> None:
        item = self.command_list.itemAt(position)
        if item is None:
            return
        index = self.command_list.row(item)
        self.command_list.setCurrentRow(index)
        menu = QMenu(self)
        menu.addAction('编辑…', lambda: self.edit_selected_command(index))
        menu.addAction('复制到发送框', lambda: self._copy_command_to_input(index))
        menu.addAction('上移', lambda: self.move_command(index, -1)).setEnabled(index > 0)
        menu.addAction('下移', lambda: self.move_command(index, 1)).setEnabled(index < len(self._commands) - 1)
        menu.addSeparator()
        menu.addAction('删除', lambda: self.remove_command(index))
        menu.exec(self.command_list.mapToGlobal(position))

    def _copy_command_to_input(self, index: int) -> None:
        if not 0 <= index < len(self._commands):
            return
        entry = self._commands[index]
        self.send_text_edit.setText(entry.payload)
        self.send_mode_combo.setCurrentIndex(self.send_mode_combo.findData(entry.mode))
        self.send_ending_combo.setCurrentIndex(self.send_ending_combo.findData(entry.ending))
        self.set_transmit_encoding(entry.encoding)

    def move_command(self, index: int, delta: int) -> None:
        target = index + delta
        if not 0 <= index < len(self._commands) or not 0 <= target < len(self._commands):
            return
        self._commands.insert(target, self._commands.pop(index))
        self._command_ids.insert(target, self._command_ids.pop(index))
        self._refresh_command_list()
        self.command_list.setCurrentRow(target)

    def remove_command(self, index: int) -> None:
        if not 0 <= index < len(self._commands):
            return
        if QMessageBox.question(self, '删除命令', f'删除1条命令“{self._commands[index].name}”？已保存文件不受影响，未保存内容无法恢复。', QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
            del self._commands[index]
            del self._command_ids[index]
            self._refresh_command_list()

    def edit_selected_command(self, index: int) -> None:
        if not 0 <= index < len(self._commands):
            return
        old = self._commands[index]
        dialog = QDialog(self)
        dialog.setWindowTitle('编辑命令')
        form = QVBoxLayout(dialog)
        name, payload = QLineEdit(old.name), QLineEdit(old.payload)
        name.setMaxLength(80)
        payload.setMaxLength(16384)
        form.addWidget(QLabel('名称'))
        form.addWidget(name)
        form.addWidget(QLabel('发送内容'))
        form.addWidget(payload)
        mode, ending = QComboBox(), QComboBox()
        for label, value in (('文本', 'text'), ('HEX', 'hex')):
            mode.addItem(label, value)
        for label, value in (('无换行', 'none'), ('LF', 'LF'), ('CRLF', 'CRLF'), ('CR', 'CR'), ('TAB', 'TAB')):
            ending.addItem(label, value)
        mode.setCurrentIndex(mode.findData(old.mode))
        ending.setCurrentIndex(ending.findData(old.ending))
        encoding = QComboBox()
        for codec in ('utf-8', 'gbk', 'latin-1', 'ascii', 'utf-16le'):
            encoding.addItem(codec.upper(), codec)
        encoding.setCurrentIndex(encoding.findData(old.encoding))
        form.addWidget(encoding)
        form.addWidget(mode)
        form.addWidget(ending)
        save = QPushButton('保存修改')
        save.clicked.connect(dialog.accept)
        form.addWidget(save)
        if dialog.exec() == QDialog.Accepted:
            try:
                self._commands[index] = CommandEntry(name.text(), payload.text(), mode.currentData(), ending.currentData(), encoding.currentData())
            except ValueError as exc:
                QMessageBox.warning(self, '命令无效', str(exc))
                return
            self._refresh_command_list()

    @Slot()
    def save_command_list(self) -> None:
        filename, _ = QFileDialog.getSaveFileName(self, '保存命令列表', 'commands.json', 'JSON (*.json)')
        if filename:
            try:
                save_commands(filename, self._commands)
            except (OSError, ValueError) as exc:
                QMessageBox.warning(self, '保存失败', str(exc))

    @Slot()
    def load_command_list(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(self, '载入命令列表', '', 'JSON (*.json)')
        if filename:
            try:
                entries = load_commands(filename)
            except (OSError, ValueError) as exc:
                QMessageBox.warning(self, '载入失败', str(exc))
                return
            if self._commands and QMessageBox.question(self, '替换本组命令',
                f'将替换本组{len(self._commands)}条命令为{len(entries)}条。已保存文件可重新载入，未保存配置无法恢复。继续吗？',
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                return
            self._commands = list(entries)
            self._command_ids = []
            self._refresh_command_list()

    @Slot()
    def clear_command_list(self) -> None:
        if self._commands and QMessageBox.question(self, '清空命令列表', f'移除当前{len(self._commands)}条命令？已保存文件不受影响，未保存内容无法恢复。', QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
            self._commands.clear()
            self._refresh_command_list()

    def _enqueue_payload(self, payload: bytes) -> bool:
        if not self._can_send_data():
            self.monitor_status_label.setText('未连接可发送的数据源，未发送')
            return False
        try:
            self._monitor_worker.enqueue_send(payload)
            self._tx_metadata.append((payload, None))
        except (ValueError, TransportError) as exc:
            self._append_monitor_log(f'发送未入队：{exc}')
            return False
        return True

    @Slot()
    def send_monitor_file(self) -> None:
        if not self._can_send_data() or self._send_job.active:
            return
        filename, _ = QFileDialog.getOpenFileName(self, '发送原始文件（不执行 ISP）', '', '所有文件 (*)')
        if not filename or not self._can_send_data():
            return
        self.periodic_send_check.setChecked(False)
        if self._send_job.start_file(filename, chunk_size=self._file_send_chunk_size, interval_ms=self._file_send_interval_ms):
            self._send_job_running = True
            self._update_monitor_controls()

    @Slot()
    def start_command_sequence(self) -> None:
        if not self._can_send_data() or self._send_job.active:
            return
        self.periodic_send_check.setChecked(False)
        self._sync_command_group()
        group = next(group for group in self._command_groups if group.id == self._command_group_id)
        if self._send_job.start_sequence(group.snapshot(), self.sequence_interval_spin.value(), self.sequence_repeat_spin.value()):
            self._send_job_running = True
            self._update_monitor_controls()

    @Slot()
    def stop_send_job(self) -> None:
        if self._send_job.active:
            self._send_job.stop()
            if self._monitor_worker is not None:
                self._discard_pending_transmissions()

    def _discard_pending_transmissions(self) -> None:
        worker = self._monitor_worker
        if worker is None:
            return
        worker.clear_pending()
        stats = getattr(worker, "statistics", None)
        # Preserve completed signals not yet handled and the active write;
        # discarded queued packets must never become successful history.
        keep = (max(0, stats.completed_packets - self._tx_completion_count) + stats.in_flight_packets
                if stats is not None else 0)
        self._tx_metadata = deque(list(self._tx_metadata)[:keep])

    @Slot(str)
    def _handle_send_job_error(self, message: str) -> None:
        self.send_job_panel.show()
        if self._send_job_running and self._monitor_worker is not None:
            self._discard_pending_transmissions()
        self.send_job_progress.setFormat('发送失败')
        self.monitor_status_label.setText(message)
        self._append_monitor_log(message)

    @Slot(int, int)
    def _handle_send_job_progress(self, done: int, total: int) -> None:
        self.send_job_panel.show()
        self.send_job_label.setText('文件发送' if self._send_job.job_kind == 'file' else '顺序发送')
        self.send_job_progress.setValue(done * 100 // max(total, 1))
        unit = '字节' if self._send_job.job_kind == 'file' else '包'
        self.send_job_progress.setFormat(f'已入队 {done}/{total} {unit}')

    @Slot(bool)
    def _handle_send_job_finished(self, cancelled: bool) -> None:
        self._send_job_running = False
        if cancelled:
            self.send_job_progress.setFormat('已停止 · 不撤回已发送内容')
        self._update_monitor_controls()

    @Slot()
    def send_selected_command(self) -> bool:
        if self._send_job.active or self.periodic_send_check.isChecked():
            return False
        index = self.command_list.currentRow()
        if not 0 <= index < len(self._commands):
            return False
        entry = self._commands[index]
        return self._enqueue_payload(encode_send_payload(entry.payload, mode=entry.mode,
                                                        line_ending=entry.ending, encoding=entry.encoding))

    @Slot(bytes)
    def _send_control_packet(self, payload: bytes) -> None:
        if self._send_job.active or self.periodic_send_check.isChecked():
            self.monitor_status_label.setText('请先停止当前发送任务，再发送控件命令。')
            return
        self._enqueue_payload(payload)

    def add_visual_control(self, kind: str, *, prompt: bool = False) -> None:
        if prompt:
            dialog = ControlEditorDialog(kind, parent=self)
            dialog.channel.setValue(self.control_channel_spin.value())
            dialog.title.setText(self.control_title_edit.text())
            dialog.command.setText(self.control_payload_edit.text())
            if dialog.exec() != QDialog.Accepted or dialog.result_spec is None:
                return
            spec = dialog.result_spec
        else:
            spec = ControlSpec(kind, self.control_channel_spin.value() - 1, self.control_title_edit.text(), self.control_payload_edit.text())
        self._activate_visual_kind('control')
        try:
            card = self.control_workspace.add_control(spec.kind, spec.channel, spec.title, spec.command, spec.settings)
            self._attach_control_menu(card)
            self.control_workspace.set_samples(self._last_values, self._effective_channel_names())
        except ValueError as exc:
            QMessageBox.warning(self, '无法添加控件', str(exc))
            return
        self._activate_visual_kind('control')
        self._refresh_control_images()
        self._update_monitor_controls()

    def _refresh_control_images(self) -> None:
        self._image_store.set_pinned(self._pinned_image_indices())
        self.control_workspace.refresh_live_images()
        if hasattr(self, 'dashboard'):
            self.dashboard.refresh_live_images()

    def _attach_control_menu(self, card) -> None:
        card.setContextMenuPolicy(Qt.CustomContextMenu)
        card.customContextMenuRequested.connect(lambda point, selected=card: self._control_context_menu(selected, point))

    def _dashboard_widgets_rebuilt(self) -> None:
        for card in self.control_workspace.controls:
            self._attach_control_menu(card)
        self._refresh_control_images()
        self._refresh_dashboard()

    def _control_context_menu(self, card, position) -> None:
        if card not in self.control_workspace.controls:
            return
        index = self.control_workspace.controls.index(card)
        menu = QMenu(self)
        menu.addAction('编辑属性…', lambda: self.edit_visual_control(index))
        menu.addAction('前移', lambda: self.control_workspace.move_control(index, index - 1)).setEnabled(index > 0)
        menu.addAction('后移', lambda: self.control_workspace.move_control(index, index + 1)).setEnabled(index + 1 < self.control_workspace.count)
        menu.addSeparator()
        menu.addAction('删除控件', lambda: self.remove_visual_control(index))
        menu.exec(card.mapToGlobal(position))

    def edit_visual_control(self, index: int) -> None:
        if not 0 <= index < self.control_workspace.count:
            return
        dialog = ControlEditorDialog(spec=self.control_workspace.controls[index].spec, parent=self)
        if dialog.exec() == QDialog.Accepted and dialog.result_spec is not None:
            spec = dialog.result_spec
            try:
                self.control_workspace.update_control(index, kind=spec.kind, channel=spec.channel, title=spec.title,
                                                       command=spec.command, settings=spec.settings)
                self._attach_control_menu(self.control_workspace.controls[index])
                self._refresh_control_images()
                self._update_monitor_controls()
            except (TypeError, ValueError) as exc:
                QMessageBox.warning(self, '控件配置无效', str(exc))

    def remove_visual_control(self, index: int) -> None:
        if not 0 <= index < self.control_workspace.count:
            return
        title = self.control_workspace.controls[index].spec.display_title
        if QMessageBox.question(self, '删除控件', f'删除1个控件“{title}”？已保存布局不受影响，未保存配置无法恢复。', QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
            self.control_workspace.remove_control(index)
            self._refresh_control_images()

    @Slot()
    def clear_visual_controls(self) -> None:
        if self.control_workspace.count and QMessageBox.question(self, '清空控件', f'移除当前{self.control_workspace.count}个控件？已保存文件不受影响，未保存布局无法恢复。', QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
            self.control_workspace.clear_controls()
            self._refresh_control_images()

    @Slot()
    def save_visual_controls(self) -> None:
        filename, _ = QFileDialog.getSaveFileName(self, '保存控件布局', 'controls.json', 'JSON (*.json)')
        if filename:
            try:
                payload = {'schema_version': 2, 'controls': self.control_workspace.export_config(), 'layout': self.control_workspace.layout_mode}
                with Path(filename).open('x', encoding='utf-8') as output:
                    json.dump(payload, output, ensure_ascii=False, indent=2)
            except (OSError, ValueError) as exc:
                QMessageBox.warning(self, '保存失败', str(exc))

    @Slot()
    def load_visual_controls(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(self, '载入控件布局', '', 'JSON (*.json)')
        if not filename:
            return
        try:
            with Path(filename).open('rb') as stream:
                content = stream.read(128 * 1024 + 1)
            if len(content) > 128 * 1024:
                raise ValueError('控件布局文件过大')
            payload = load_json_document(content, max_bytes=128 * 1024)
            if (not isinstance(payload, dict) or type(payload.get('schema_version')) is not int
                    or payload['schema_version'] not in (1, 2)):
                raise ValueError('不支持的控件布局格式')
            expected = {'schema_version', 'controls'} | ({'layout'} if payload['schema_version'] == 2 else set())
            if set(payload) != expected or payload.get('layout', 'grid') not in ('grid', 'free'):
                raise ValueError('控件布局字段无效')
            if not isinstance(payload['controls'], list) or len(payload['controls']) > 24:
                raise ValueError('控件布局须为不超过24项的列表')
            for entry in payload['controls']:
                if not isinstance(entry, dict):
                    raise ValueError('控件条目必须为对象')
                ControlSpec(**entry)
            if self.control_workspace.count and QMessageBox.question(self, '载入控件布局',
                f'将替换当前{self.control_workspace.count}个控件。已保存文件不受影响，未保存配置不能恢复。继续吗？',
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                return
            self.control_workspace.load_config(payload['controls'])
            self.control_workspace.set_layout_mode(payload.get('layout', 'grid'))
        except (OSError, TypeError, ValueError, UnicodeError) as exc:
            QMessageBox.warning(self, '载入失败', str(exc))
            return
        self.control_workspace.set_theme(self._theme)
        self.control_workspace.set_samples(self._last_values, self._effective_channel_names())
        for card in self.control_workspace.controls:
            self._attach_control_menu(card)
        self._activate_visual_kind('control')

    @Slot()
    def apply_theme(self) -> None:
        self._theme = self.theme_combo.currentData() or "light"
        app = QApplication.instance()
        if app is not None:
            app.setPalette(qt_palette(self._theme))
        # 先清空再设置：子控件需要重新解析样式表才能继承新的原生调色板。
        self.setStyleSheet("")
        self.setStyleSheet(stylesheet(self._theme, self._text_scale))
        if hasattr(self, 'waveform'):
            self.waveform.set_theme(self._theme)
            self.control_workspace.set_theme(self._theme)
            self.image_view.set_theme(self._theme)
            self.analysis_view.set_theme(self._theme)
            if hasattr(self, 'dashboard'):
                self.dashboard.set_theme(self._theme)
            self._refresh_channel_values()
            self._apply_scaled_metrics()
            self._apply_phase_styles()
            if hasattr(self, 'tabs'):
                self._update_navigation(self.tabs.currentIndex())

    @Slot()
    def apply_text_scale(self, factor: float | None = None) -> None:
        """Scale every piece of UI text; the log follows until it gets its own size."""

        value = set_text_scale(factor if factor is not None else self._text_scale)
        if value == self._text_scale:
            return
        self._text_scale = value
        app = QApplication.instance()
        if app is not None:
            app.setFont(ui_font(value))
        if not self._terminal_font_customized:
            self._terminal_font_size = max(8, min(24, round(11 * value)))
            self.monitor_log_view.setStyleSheet(f'font-size: {self._terminal_font_size}px;')
        self.apply_theme()
        self._sync_text_scale_actions()
        save_text_scale(value)

    def _sync_text_scale_actions(self) -> None:
        for action, factor in getattr(self, '_text_scale_actions', ()):
            action.setChecked(abs(factor - self._text_scale) < 0.005)

    def _apply_scaled_metrics(self) -> None:
        """Widths and minimum heights pinned in pixels must grow with the text."""

        if hasattr(self, 'theme_combo'):
            self.theme_combo.setFixedWidth(max(84, round(84 * self._text_scale)))
        for name in ('send_mode_combo', 'send_ending_combo'):
            combo = getattr(self, name, None)
            if combo is not None:
                combo.setFixedWidth(max(88, round(88 * self._text_scale)))
        if hasattr(self, 'monitor_log_view'):
            self.monitor_log_view.setMinimumHeight(scaled_px(64))

    def _apply_phase_styles(self) -> None:
        state = getattr(self, '_phase_state', None)
        if state is not None and self.phase_labels:
            self._set_phase(state)
        else:
            color = theme_palette(self._theme).muted
            for label in self.phase_labels.values():
                label.setStyleSheet(
                    f"color: {color}; font-size: {scaled_px(11)}px; padding: 7px 2px;")

    def _refresh_channel_values(self, *_args) -> None:
        names = self._effective_channel_names()
        self.analysis_view.set_channel_names(names)
        precision = self.precision_spin.value()
        colors = self.waveform.channel_colors
        for index, label in enumerate(self.channel_value_labels):
            label.setStyleSheet(
                f"color: {colors[index % len(colors)]}; font-family: {FONT_MONO};"
                f"font-size: {scaled_px(12)}px; font-weight: 600; padding: 3px 1px;")
            name = names[index] if index < len(names) else f"CH{index + 1}"
            if index < len(self._last_values):
                value = self._last_values[index]
                digits = self._channel_display[index].precision
                digits = precision if digits is None else digits
                text = f"{value:.{digits}f}" if abs(value) < 1e12 else f"{value:.{digits}e}"
                unit = self._channel_display[index].unit
                label.setText(f"{name[:12]}    {text}" + (f" {unit}" if unit else ""))
            else:
                label.setText(f"{name[:12]}    —")

    @Slot()
    def open_theme_editor(self) -> None:
        dialog = ThemeEditorDialog(self)
        if dialog.exec() == QDialog.Accepted and dialog.selected_theme:
            self._refresh_theme_choices(dialog.selected_theme)

    @Slot()
    def configure_plot(self) -> None:
        target = self._plot_target()
        if target is None:
            return
        dialog = PlotSettingsDialog(target, self)
        if dialog.exec() == QDialog.Accepted and dialog.result_config is not None:
            config = dialog.result_config
            try:
                target.set_xy_channels(*config['channels'])
                target.set_mode(config['mode'])
                target.set_axis_ranges(**config['axes'])
                target.set_grid_visible(config['grid'])
                target.set_line_width(config['width'])
            except ValueError as exc:
                QMessageBox.warning(self, '显示设置无效', str(exc))

    def choose_channel_color(self, channel: int) -> None:
        target = self._plot_target()
        if target is None:
            return
        color = QColorDialog.getColor(QColor(target.channel_colors[channel]), self, f'CH{channel + 1} 曲线颜色')
        if color.isValid():
            target.set_channel_color(channel, color.name())
            self._refresh_channel_values()

    def channel_context_menu(self, channel: int, position) -> None:
        menu = QMenu(self)
        menu.addAction('通道参数…', lambda: self.configure_channel(channel))
        menu.addAction('曲线颜色…', lambda: self.choose_channel_color(channel))
        menu.exec(self.channel_visibility_checks[channel].mapToGlobal(position))

    def configure_channel(self, channel: int) -> None:
        dialog = ChannelSettingsDialog(channel, self._channel_display[channel], self)
        if dialog.exec() != QDialog.Accepted or dialog.result_config is None:
            return
        configs = list(self._channel_display)
        configs[channel] = dialog.result_config
        self._channel_display = tuple(configs)
        self._rebuild_display_samples()

    def _display_samples(self, samples) -> tuple[float, ...]:
        return tuple(config.apply(value) for config, value in zip(self._channel_display, samples))

    def _display_names(self, names) -> list[str]:
        return [name + (f' [{self._channel_display[index].unit}]' if self._channel_display[index].unit else '')
                for index, name in enumerate(names[:16])]

    def _rebuild_display_samples(self) -> None:
        rows = []
        for frame in self.sample_store.snapshot():
            try:
                rows.append(self._display_samples(frame.values))
            except ValueError:
                continue
        self._last_values = rows[-1] if rows else ()
        names = self._effective_channel_names()
        self.waveform.replace_samples(rows, self._display_names(names), sample_count=self.sample_store.statistics.parsed)
        self.control_workspace.set_samples(self._last_values, names)
        self.analysis_view.invalidate()
        if hasattr(self, 'dashboard'):
            self._refresh_dashboard()
        self._refresh_channel_values()

    @Slot()
    def export_plot_image(self) -> None:
        target = self._plot_target()
        if target is None:
            return
        filename, _ = QFileDialog.getSaveFileName(self, '导出当前波形图（不覆盖）', 'waveform.png', 'PNG (*.png)')
        if filename:
            try:
                target.export_png(filename)
            except (OSError, ValueError) as exc:
                QMessageBox.warning(self, '导出失败', str(exc))

    def _refresh_theme_choices(self, selected: str | None = None) -> None:
        selected = selected or self._theme
        self.theme_combo.blockSignals(True)
        self.theme_combo.clear()
        for key, title in theme_choices():
            self.theme_combo.addItem(title, key)
        self.theme_combo.setCurrentIndex(max(0, self.theme_combo.findData(selected)))
        self.theme_combo.blockSignals(False)
        self.apply_theme()

    @Slot()
    def show_workbench_help(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle('使用帮助 · Astrspire ' + __version__.replace(".dev", "-dev"))
        dialog.resize(850, 680)
        layout = QVBoxLayout(dialog)
        pages = QTabWidget()
        root = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[2]))
        for title, filename in (('dev4 新功能', 'SERIAL_TOOLS_DEV4.md'), ('基础操作', 'VOFA_WORKBENCH_GUIDE.md'), ('功能与限制', 'feature-status-0.6.0-dev4.md')):
            browser = QTextBrowser()
            browser.setOpenLinks(False)
            browser.setOpenExternalLinks(False)
            try:
                with (root / 'docs' / filename).open('rb') as stream:
                    content = stream.read(128 * 1024 + 1)
                if len(content) > 128 * 1024:
                    raise ValueError('帮助文档超过大小限制')
                browser.setMarkdown(content.decode('utf-8'))
            except (OSError, ValueError, UnicodeError):
                browser.setPlainText('帮助文档未包含在当前目录。请查看工程 docs/VOFA_WORKBENCH_GUIDE.md。\n本版为未运行验收的预览版，原生插件和工程格式尚未兼容。')
            pages.addTab(browser, title)
        layout.addWidget(pages, 1)
        close_button = QPushButton('关闭')
        close_button.clicked.connect(dialog.accept)
        layout.addWidget(close_button)
        dialog.exec()

    def _selected_decoder_name(self) -> str:
        return self.decoder_combo.currentData() or self.decoder_combo.currentText()

    def _refresh_decoder_plugins(self, selected: str | None = None) -> None:
        selected = selected or self._selected_decoder_name()
        self.decoder_combo.blockSignals(True)
        self.decoder_combo.clear()
        for title, value in (("FireWater", "FireWater"), ("JustFloat", "JustFloat"), ("RawData", "RawData"), ("JPEG / PNG", "ImageStream")):
            self.decoder_combo.addItem(title, value)
        self.decoder_combo.addItem("FireWater · 图像", "FireWaterImage")
        self.decoder_combo.addItem("JustFloat · 图像", "JustFloatImage")
        for manifest in PLUGIN_REGISTRY.engines:
            self.decoder_combo.addItem(f'扩展 · {manifest.name}', manifest.decoder_name)
        index = self.decoder_combo.findData(selected)
        if index < 0:
            self.decoder_combo.addItem(f'缺少扩展 · {selected}', selected)
            index = self.decoder_combo.count() - 1
        self.decoder_combo.setCurrentIndex(index)
        self.decoder_combo.blockSignals(False)

    @Slot()
    def open_plugin_manager(self) -> None:
        selected = self._selected_decoder_name()
        PluginManagerDialog(self, busy=self._monitor_active or self._demo_active).exec()
        self._refresh_decoder_plugins(selected)
        self._update_monitor_controls()

    @Slot()
    def choose_image_directory(self) -> None:
        filename = QFileDialog.getExistingDirectory(self, '选择 JPEG / PNG 目录')
        if filename:
            self.image_directory_edit.setText(filename)
        self._update_monitor_controls()

    @Slot()
    def open_local_image(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(self, '打开图像', '', '图片 (*.jpg *.jpeg *.png)')
        if not filename:
            return
        try:
            with Path(filename).open('rb') as stream:
                payload = stream.read(8 * 1024 * 1024 + 1)
            if len(payload) > 8 * 1024 * 1024:
                raise ValueError('图像文件超过8MiB')
            self._cache_received_image(self.image_channel_combo.currentData(), payload)
            self._activate_visual_kind('image')
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, '图像打开失败', str(exc))

    @Slot()
    def save_current_image(self) -> None:
        if not self.image_view.has_image:
            return
        extension = self.image_view.default_suffix
        title = '保存当前图像视图 PNG' if self.image_view.is_native_raw else '保存原始图像（不含显示效果）'
        filename, _ = QFileDialog.getSaveFileName(self, title, 'frame' + extension, '当前图像 (*' + extension + ')')
        if filename:
            try:
                self.image_view.save_image(filename)
            except (OSError, ValueError) as exc:
                QMessageBox.warning(self, '图像保存失败', str(exc))

    @Slot()
    def save_image_payload(self) -> None:
        if not self.image_view.has_image:
            return
        filename, _ = QFileDialog.getSaveFileName(self, '导出图像原始载荷（不含协议头）', 'frame.bin', '原始数据 (*.bin)')
        if not filename:
            return
        try:
            self.image_view.save_raw_payload(filename)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, '载荷导出失败', str(exc))

    @Slot(bool)
    def pause_image_playback(self, paused: bool) -> None:
        if self._source_running == 'images' and self._monitor_worker is not None:
            self._monitor_worker.set_paused(paused)
        self.image_pause_button.setText('继续' if paused else '暂停')

    @Slot(bytes)
    def _handle_replay_image(self, payload: bytes) -> None:
        worker = self._monitor_worker
        try:
            self._cache_received_image(0, payload)
            self._image_frames += 1
        finally:
            if worker is not None and hasattr(worker, 'ack_frame'):
                worker.ack_frame()

    def _pinned_image_indices(self) -> set[int]:
        pinned = set(self.control_workspace.live_image_indices())
        selected = self.image_channel_combo.currentData()
        if type(selected) is int:
            pinned.add(selected)
        if hasattr(self, 'dashboard'):
            pinned.update(self.dashboard.live_image_indices())
        return pinned

    def _cache_received_image(self, index: int, payload: bytes, metadata=None) -> None:
        if type(index) is not int or not 0 <= index <= 15 or len(payload) > 8 * 1024 * 1024:
            self._append_monitor_log('图像索引或大小超出本软件限制，未缓存。')
            return
        try:
            self._image_store.set_pinned(self._pinned_image_indices())
            self._image_store.put(index, payload, metadata)
        except ValueError as exc:
            self._append_monitor_log(f'图像未缓存：{exc}')
            return
        selected = self.image_channel_combo.currentData()
        for slot in range(self.image_channel_combo.count()):
            if slot != selected and self._image_store.get(slot) is None:
                self.image_channel_combo.setItemText(slot, f'图像 {slot}')
        self.image_channel_combo.setItemText(index, f'图像 {index} ●')
        self.image_channel_combo.setItemData(index, f'已接收{len(payload)}字节；头字段{metadata}', Qt.ToolTipRole)
        if selected == index or index in self._pinned_image_indices():
            elapsed = time.monotonic() - self._last_image_render_at
            if elapsed >= .1:
                self._show_cached_image()
            elif not self._image_render_timer.isActive():
                self._image_render_timer.start(max(1, math.ceil((.1 - elapsed) * 1000)))

    def _show_cached_image(self, *_args) -> None:
        self._image_render_timer.stop()
        self._last_image_render_at = time.monotonic()
        self.control_workspace.refresh_live_images()
        if hasattr(self, 'dashboard'):
            self.dashboard.refresh_live_images()
        if not self.image_view.isVisible():
            return
        index = self.image_channel_combo.currentData()
        entry = self._image_store.get(index)
        if entry is None:
            self.image_view.clear()
            return
        decoded, _error = self._image_store.decoded(index)
        if decoded is None:
            self.image_view.clear()
            return
        self.image_view.set_decoded_frame(decoded, entry[0])

    @Slot(int, int)
    def _handle_image_progress(self, done: int, total: int) -> None:
        self.image_progress.setValue(done * 100 // max(total, 1))
        self.image_progress.setFormat(f'{done}/{total} 张')

    @Slot()
    def choose_playback_csv(self) -> None:
        stream_source = self.source_combo.currentData() == 'stream'
        filename, _ = QFileDialog.getOpenFileName(self, "选择收发记录" if stream_source else "选择采样 CSV", "", "QX记录 (*.jsonl *.session.json)" if stream_source else "CSV (*.csv)")
        if filename:
            self.playback_path_edit.setText(filename)
        self._update_monitor_controls()

    @Slot(bool)
    def pause_csv_playback(self, paused: bool) -> None:
        if isinstance(self._monitor_worker, (CsvReplayWorker, RawReplayWorker, MatrixReplayWorker)):
            self._monitor_worker.set_paused(paused)
        self.playback_pause_button.setText("继续回放" if paused else "暂停回放")

    @Slot()
    def choose_matrix_directory(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, '选择数值矩阵CSV目录')
        if directory:
            self.playback_path_edit.setText(directory)
        self._update_monitor_controls()

    def _network_config(self) -> NetworkConfig:
        def port(text: str) -> int:
            if not text.isascii() or not text.isdecimal() or not 1 <= int(text) <= 65535:
                raise ValueError("端口必须为1–65535")
            return int(text)
        kind = self.source_combo.currentData()
        peer_host = self.udp_peer_host_edit.text().strip() if kind == 'udp' else ''
        peer_port = self.udp_peer_port_edit.text().strip() if kind == 'udp' else ''
        if bool(peer_host) != bool(peer_port):
            raise ValueError("UDP目标地址与端口需同时填写")
        return NetworkConfig(kind, self.network_host_edit.text().strip(),
                             port(self.network_port_edit.text().strip()),
                             peer_host or None, port(peer_port) if peer_port else None)

    @property
    def is_simulator_mode(self) -> bool:
        return self.mode_combo.currentData() == "simulator"

    def _selected_chip(self):
        name = self.chip_combo.currentText().strip()
        return self.catalog.get(name) if name else None

    @Slot()
    def _on_chip_changed(self, *_args: object) -> None:
        entry = self._selected_chip()
        selected_name = entry.name if entry is not None else None
        changed = selected_name != self._last_chip_name
        self._last_chip_name = selected_name
        if changed and entry is not None and self.image_path is not None and hasattr(self, "file_path_label"):
            self.load_hex(self.image_path)
        elif hasattr(self, "start_button"):
            self._update_controls()

    @Slot()
    def choose_hex(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(self, "选择程序文件", "", "固件文件 (*.hex *.ihx *.bin);;所有文件 (*)")
        if filename:
            self.load_hex(Path(filename))

    @Slot()
    def load_sample(self) -> None:
        sample = _sample_hex_path()
        if sample.is_file():
            self.load_hex(sample)
        else:
            QMessageBox.warning(self, "示例不可用", "找不到 examples/led.hex，请手动选择 HEX 文件。")

    def load_hex(self, path: Path) -> None:
        try:
            chip = self._selected_chip()
            limit = chip.program_bytes if chip is not None else None
            if chip is not None and chip.name == "STC89C52RC/LE52RC":
                limit = 8192
            base = parse_address(self.bin_base_edit.text()) if path.suffix.lower() == ".bin" else 0
            image = load_firmware(path, program_limit=limit, base_address=base)
        except (OSError, ValueError) as exc:
            self.image = None
            self.image_path = None
            self.file_path_label.setText(str(path))
            self.hex_summary.setText("文件无效")
            self.status_label.setText(f"固件解析失败：{exc}")
            self._append_log(f"固件解析失败：{exc}")
        else:
            self.image = image
            self.image_path = path.resolve()
            self.file_path_label.setText(str(self.image_path))
            self.hex_summary.setText(
                f"{image.data_size} 字节  ·  0x{image.min_address:04X}–0x{image.max_address:04X}"
            )
            validation = "BIN 文件已载入（没有 Intel HEX 行校验）" if path.suffix.lower() == ".bin" else "HEX 行校验通过"
            self.status_label.setText(f"{validation}，可开始模拟流程；未实际烧录。")
            qualifier = "；芯片容量未确认，仅校验文件格式" if limit is None else ""
            self._append_log(
                f"{validation}：{self.image_path}，数据 {image.data_size} 字节{qualifier}。"
            )
        self._update_controls()

    def show_firmware_buffer(self, image: HexImage | None, title: str) -> None:
        if image is None:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.resize(850, 540)
        layout = QVBoxLayout(dialog)
        viewer = QPlainTextEdit()
        viewer.setReadOnly(True)
        viewer.setPlainText(buffer_preview(image))
        layout.addWidget(viewer)
        export_button = QPushButton("导出此缓冲区…")
        export_button.clicked.connect(lambda: self.export_firmware_buffer(image))
        layout.addWidget(export_button)
        dialog.exec()

    def export_firmware_buffer(self, image: HexImage | None) -> None:
        if image is None:
            return
        filename, selected = QFileDialog.getSaveFileName(
            self, "导出缓冲区（BIN 从最低地址开始，空洞补 FF）", "firmware.hex", "Intel HEX (*.hex);;二进制 (*.bin)"
        )
        if not filename:
            return
        destination = Path(filename)
        suffix = destination.suffix.lower()
        if suffix not in {"", ".hex", ".ihx", ".bin"}:
            QMessageBox.warning(self, "导出格式不明确", "请使用 .hex、.ihx 或 .bin 后缀；格式以文件后缀为准。")
            return
        kind = ("bin" if suffix == ".bin" else "hex") if suffix else ("bin" if "*.bin" in selected else "hex")
        if not suffix:
            destination = destination.with_suffix("." + kind)
        try:
            export_firmware(destination, image, kind=kind)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "导出失败", str(exc))
            return
        self._append_log(f"缓冲区已导出：{destination}；最低地址 0x{image.min_address:08X}，未写入设备。")

    @Slot()
    def choose_eeprom_file(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(self, "选择 EEPROM 文件（仅文件缓冲区）", "", "固件文件 (*.hex *.ihx *.bin)")
        if not filename:
            return
        try:
            self.eeprom_image = load_firmware(Path(filename), program_limit=None)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "EEPROM 文件无效", str(exc))
            return
        self._append_log(f"载入 EEPROM 文件缓冲区：{filename}；当前不向设备写入此缓冲区。")
        self.show_firmware_buffer(self.eeprom_image, "EEPROM 文件缓冲区 · 未写入设备")

    @Slot()
    def refresh_ports(self) -> None:
        previous_isp = self.port_combo.currentData()
        previous_monitor = self.monitor_port_combo.currentData()
        try:
            self._ports = enumerate_ports()
        except Exception as exc:
            self._ports = []
            self.port_hint.setText(f"串口枚举失败：{exc}")
            self._append_log(f"串口枚举失败：{exc}")
        self.port_combo.clear()
        self.port_combo.addItem("请选择串口（不会自动连接）", None)
        self.monitor_port_combo.clear()
        self.monitor_port_combo.addItem("请选择接收串口", None)
        for port in self._ports:
            tag = " · 疑似 CH340/CH341" if port.is_ch340_candidate else ""
            ident = f" [{port.vid:04X}:{port.pid:04X}]" if port.vid is not None and port.pid is not None else ""
            label = f"{port.device} · {port.description}{ident}{tag}"
            self.port_combo.addItem(label, port.device)
            self.monitor_port_combo.addItem(label, port.device)
        for combo, value in ((self.port_combo, previous_isp), (self.monitor_port_combo, previous_monitor)):
            index = combo.findData(value)
            if value is not None and index >= 0:
                combo.setCurrentIndex(index)
        candidates = sum(port.is_ch340_candidate for port in self._ports)
        self.port_hint.setText(
            f"{len(self._ports)} 个串口 · {candidates} 个 CH340"
            if self._ports else "未检测到串口"
        )
        self._filter_monitor_ports()
        self._update_monitor_controls()

    def _fit_sidebar_widths(self):
        # Different Windows/fallback fonts have different natural button widths.
        # Preserve those controls instead of allowing a horizontally clipped
        # QScrollArea whose horizontal scrollbar is intentionally disabled.
        for scroll, minimum in ((self.isp_side_scroll, 240), (self.monitor_side_scroll, 220)):
            scroll.widget().ensurePolished()
            width = scroll.widget().minimumSizeHint().width()
            chrome = scroll.verticalScrollBar().sizeHint().width() + 2 * scroll.frameWidth() + 2
            scroll.setMinimumWidth(max(minimum, width + chrome))

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.FontChange and hasattr(self, "monitor_side_scroll"):
            QTimer.singleShot(0, self._fit_sidebar_widths)

    def _filter_monitor_ports(self, *_args):
        if not hasattr(self, "monitor_port_combo"):
            return
        query = self.monitor_port_search.text().strip().casefold()
        for index in range(self.monitor_port_combo.count()):
            self.monitor_port_combo.view().setRowHidden(
                index, index > 0 and query not in self.monitor_port_combo.itemText(index).casefold())

    def _update_controls(self, *_args: object) -> None:
        sim = self.is_simulator_mode
        chip = self._selected_chip()
        device = self.port_combo.currentData()
        self.simulation_action.blockSignals(True)
        self.simulation_action.setChecked(sim)
        self.simulation_action.blockSignals(False)
        self.simulation_action.setEnabled(not self._active)
        if sim:
            self.start_button.setEnabled(
                self.image is not None and chip is not None and not self._active
            )
            self.start_button.setText("开始模拟下载")
        else:
            real_ready = (
                not self._active
                and self.image is not None
                and chip is not None
                and chip.name == "STC89C52RC/LE52RC"
                and isinstance(device, str)
                and PORT_LEASES.owner_for(device) is None
            )
            self.start_button.setEnabled(real_ready)
            self.start_button.setText(
                "开始真实烧录…" if self.image is not None else "真实烧录需先载入 HEX"
            )
        self.cancel_button.setEnabled(self._active)
        self.buffer_view_button.setEnabled(self.image is not None)
        self.buffer_export_button.setEnabled(self.image is not None)
        self.choose_file_button.setEnabled(not self._active)
        self.bin_base_edit.setEnabled(not self._active)
        self.sample_button.setEnabled(not self._active)
        self.chip_combo.setEnabled(not self._active)
        self.replay_button.setEnabled(self.image is not None and not self._active)
        self.power_button.setEnabled(
            self._active and isinstance(self.session, DownloadSession)
            and self.session.state == SessionState.WAIT_POWER
        )
        self.power_button.setVisible(sim)
        self.scenario_combo.setVisible(sim)
        self.scenario_combo.setEnabled(sim and not self._active)
        self.mode_combo.setEnabled(not self._active)
        self.port_combo.setEnabled(not self._active)
        self.identify_button.setEnabled(
            not sim and not self._active and isinstance(self.port_combo.currentData(), str)
        )
        erase_ready = (not sim and not self._active and chip is not None
                       and chip.name == "STC89C52RC/LE52RC" and isinstance(device, str)
                       and PORT_LEASES.owner_for(device) is None)
        self.erase_button.setEnabled(erase_ready)
        self.full_erase_check.setEnabled(erase_ready)
        self.full_erase_check.setVisible(not sim)
        self.erase_button.setVisible(not sim)

    @Slot()
    def start_full_erase(self) -> None:
        chip, device = self._selected_chip(), self.port_combo.currentData()
        if (self.is_simulator_mode or self._active or chip is None
                or chip.name != "STC89C52RC/LE52RC" or not isinstance(device, str)):
            return
        owner = PORT_LEASES.owner_for(device)
        if owner is not None:
            self.status_label.setText(f"无法擦除：{device} 已由 {owner} 占用，请先断开。")
            return
        self.full_erase_check.setChecked(False)
        session = Stc89EraseSession(PySerialTransport(device),
                                   emit=lambda event: self._worker.event.emit(event), allow_erase=True)
        self.progress_bar.setValue(0)
        self._append_log(f"用户请求实验性全程序区擦除：{device}，0x0000–0x1FFF；等待冷上电。"
                         "不写程序、EEPROM或选项；仅验证擦除ACK，未全区回读。")
        self._launch_session(session, device=device)

    @Slot()
    def start_readonly_identification(self) -> None:
        device = self.port_combo.currentData()
        if self.is_simulator_mode or self._active or not isinstance(device, str):
            return
        owner = PORT_LEASES.owner_for(device)
        if owner is not None:
            self.status_label.setText(f"无法识别：{device} 已由 {owner} 占用，请先断开数据监视。")
            return
        self.progress_bar.setValue(0)
        session = Stc89ReadOnlySession(
            PySerialTransport(device), emit=lambda event: self._worker.event.emit(event),
        )
        self._append_log(f"手动请求只读识别：{device}，2400 / 8N1，等待 30 秒。仅发送 7F 同步；请手动断电再上电。")
        self._launch_session(session, device=device)

    @Slot()
    def start_download(self) -> None:
        if self.is_simulator_mode:
            self.start_simulation()
        else:
            self.start_real_download()

    @Slot()
    def start_real_download(self) -> None:
        chip = self._selected_chip()
        device = self.port_combo.currentData()
        if (self.is_simulator_mode or self._active or self.image is None
                or chip is None or not isinstance(device, str)):
            self._update_controls()
            return
        if chip.name != "STC89C52RC/LE52RC":
            self.status_label.setText("真实烧录只覆盖 F002 / 66 43 FD 指纹；当前型号未注册。")
            self._update_controls()
            return
        owner = PORT_LEASES.owner_for(device)
        if owner is not None:
            self.status_label.setText(f"无法烧录：{device} 已由 {owner} 占用，请先断开。")
            return
        end = (self.image.max_address // ERASE_BYTES + 1) * ERASE_BYTES
        session = Stc89DownloadSession(
            self.image,
            PySerialTransport(device),
            emit=lambda event: self._worker.event.emit(event),
            allow_erase=True,
            erase_full_program=self.full_erase_check.isChecked(),
        )
        if self.full_erase_check.isChecked():
            self._append_log("本次预擦除范围0x0000–0x1FFF（实验性）；写入仍按HEX范围。")
        self.full_erase_check.setChecked(False)
        self.progress_bar.setValue(0)
        self._set_phase(SessionState.IDLE)
        self._append_log(
            f"真实烧录请求：{device}，{chip.name}，范围 0x0000–0x{end - 1:04X}；"
            "先识别并核对 F002 / 66 43 FD 指纹，失败不会擦写。"
        )
        self._launch_session(session, device=device)

    @Slot()
    def start_simulation(self) -> None:
        chip = self._selected_chip()
        if not self.is_simulator_mode or self.image is None or chip is None or self._active:
            self._update_controls()
            return
        self.progress_bar.setValue(0)
        self._set_phase(SessionState.IDLE)
        fault = self.scenario_combo.currentData()
        simulator = SimulatedTransport(cold_start_delay=0.1, fragment_size=7, fault=fault)
        session = DownloadSession(
            self.image,
            emit=lambda event: self._worker.event.emit(event),
            simulator=simulator,
            chip_name=chip.name,
        )
        self._append_log(
            f"开始 {chip.name} 流程模拟；不使用该芯片真实 ISP 协议。"
            "点击“模拟上电”以触发冷启动，等待上限 30 秒。"
        )
        self._launch_session(session)

    @Slot()
    def choose_official_replay(self) -> None:
        if self._active or self.image is None:
            return
        filename, _ = QFileDialog.getOpenFileName(
            self, "选择官方成功会话（离线回放）", "", "官方抓包 (*.jsonl)"
        )
        if filename:
            self.start_official_replay(Path(filename))

    def start_official_replay(self, path: Path) -> None:
        if self._active or self.image is None:
            return
        chip = self._selected_chip()
        if chip is None or chip.name != "STC89C52RC/LE52RC":
            self.status_label.setText("离线回放请先选择 STC89C52RC/LE52RC，并加载对应的 led.hex。")
            return
        try:
            capture = OfficialCapture.from_file(path, self.image)
        except (OSError, ValueError) as exc:
            self.status_label.setText(f"离线回放预检查失败：{exc}")
            self._append_log(self.status_label.text())
            return
        self.progress_bar.setValue(0)
        self._set_phase(SessionState.IDLE)
        session = OfficialReplaySession(capture, emit=lambda event: self._worker.event.emit(event))
        self._append_log(
            f"官方会话离线回放：F002 / {capture.status.version_label}；"
            f"已核对 {len(capture.steps)} 个帧和当前 HEX。不打开串口，未实际烧录。"
        )
        self._launch_session(session)

    def _launch_session(self, session: DownloadSession | OfficialReplaySession
                        | Stc89ReadOnlySession | Stc89DownloadSession,
                        *, device: str | None = None) -> None:
        self._thread = QThread(self)
        self._worker = _SessionWorker(session, device=device)
        self.session = self._worker.session
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.event.connect(self._handle_event)
        self._worker.finished.connect(self._thread.quit)
        self._thread.finished.connect(self._on_thread_finished)
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._active = True
        self._update_controls()
        self._thread.start()

    @Slot()
    def simulate_power_on(self) -> None:
        if isinstance(self.session, DownloadSession) and self.session.state == SessionState.WAIT_POWER:
            self.session.power_on()
            self.power_button.setEnabled(False)
            self._append_log("模拟目标板重新上电。")

    @Slot()
    def cancel_simulation(self) -> None:
        if self.session is not None and self._active:
            self.session.cancel()
            self.cancel_button.setEnabled(False)
            self._append_log("用户请求取消，等待工作线程收尾。")

    @Slot(object)
    def _handle_event(self, event: SessionEvent) -> None:
        state = event.state
        self._set_phase(state)
        if event.progress is not None:
            self.progress_bar.setValue(max(0, min(100, int(event.progress))))
        if event.message:
            self.status_label.setText(event.message)
        if event.data:
            direction = event.direction or event.kind.upper()
            self._append_log(f"{direction.upper():2} {event.data.hex(' ').upper()}", event.timestamp)
        elif event.message:
            self._append_log(event.message, event.timestamp)
        self._update_controls()

    @Slot()
    def _on_thread_finished(self) -> None:
        self._active = False
        self.session = None
        self._worker = None
        self._thread = None
        self._update_controls()

    def _set_phase(self, state: SessionState) -> None:
        self._phase_state = state
        palette = theme_palette(self._theme)
        done, active, pending = palette.accent, palette.safety, palette.muted
        size = scaled_px(11)
        if isinstance(self.session, Stc89ReadOnlySession):
            phases = self.session.phase_states
            current = phases.index(state) if state in phases else -1
            for phase, label in self.phase_labels.items():
                used = phase in phases
                label.setText(dict(_PHASES)[phase] if used else "未执行")
                color = done if used and phases.index(phase) < current else active if phase == state else pending
                label.setStyleSheet(f"color: {color}; font-size: {size}px; padding: 7px 2px;")
            return
        current = next((i for i, (phase, _) in enumerate(_PHASES) if phase == state), -1)
        for index, (phase, _) in enumerate(_PHASES):
            label = self.phase_labels[phase]
            label.setText(dict(_PHASES)[phase])
            color = done if index < current else active if index == current else pending
            label.setStyleSheet(f"color: {color}; font-size: {size}px; font-weight: 700; padding: 7px 2px;")

    def _monitor_baudrate(self) -> int | None:
        value = self.monitor_baud_edit.text().strip()
        if not value.isascii() or not value.isdecimal():
            return None
        rate = int(value)
        return rate if 1 <= rate <= 4_000_000 else None

    def _entered_channel_names(self) -> list[str]:
        text = self.channel_names_edit.text()
        if not text.strip():
            return []
        return [item.strip() or f'CH{index + 1}' for index, item in enumerate(text.split(','))]

    def _effective_channel_names(self) -> list[str]:
        base = self._entered_channel_names() or list(self._source_channel_names)
        count = max(len(base), next((index + 1 for index in reversed(range(16)) if self._channel_display[index].name), 0))
        return [(self._channel_display[index].name or (base[index] if index < len(base) else f'CH{index + 1}'))
                for index in range(min(count, 16))]

    def _update_link_metrics(self) -> None:
        now = time.monotonic()
        before, rx, tx, samples, images = self._metrics_previous
        elapsed = max(.001, now - before)
        current = (self._rx_bytes, self._tx_bytes, self._sample_index, self._image_frames)
        rates = tuple(max(0, current_value - old_value) / elapsed for current_value, old_value in zip(current, (rx, tx, samples, images)))
        self._metrics_previous = (now, *current)
        def byte_rate(value):
            return f'{value / 1024:.1f} KiB/s' if value >= 1024 else f'{value:.0f} B/s'
        self.link_metrics_label.setText(f'RX {byte_rate(rates[0])} · TX {byte_rate(rates[1])}\n采样帧 {rates[2]:.1f}/s · 图像帧 {rates[3]:.1f}/s')

    def _reset_data_session(self) -> None:
        self.packet_store.reset_session()
        self._tx_metadata.clear()
        self._tx_completion_count = 0
        self._source_channel_names = ()
        self.sample_store.clear()
        self._last_values = ()
        self._sample_index = 0
        self._tx_bytes = self._rx_bytes = 0
        self._metrics_previous = (time.monotonic(), 0, 0, 0, 0)
        self._rx_text_decoder.reset()
        self._ansi_sink.reset()
        self._image_decoder.reset()
        self._image_frames = 0
        self._image_auto_selected = False
        self._image_render_timer.stop()
        self._image_store.clear()
        self.control_workspace.refresh_live_images()
        for index in range(self.image_channel_combo.count()):
            self.image_channel_combo.setItemText(index, f'图像 {index}')
        self.image_view.clear()
        self.waveform.clear()
        self.control_workspace.set_samples((), self._effective_channel_names())
        self._refresh_channel_values()
        self.tx_status_label.setText('TX：0 字节')
        self.rx_status_label.setText('RX：0 字节')
        self.sample_count_label.setText('采样点：0')
        self._append_monitor_log('新数据会话：采样、曲线和计数已独立重置。')

    def _update_monitor_controls(self, *_args: object) -> None:
        source = self.source_combo.currentData()
        demo = source == "demo"
        serial_source = source == "serial"
        swd_source = source == "swd"
        self.swd_panel.setVisible(swd_source)
        network = source in {"tcp_client", "tcp_server", "udp"}
        csv_source = source in {"csv", "stream", "matrix"}
        image_source = source == "images"
        compat_source = source == 'compat_demo'
        busy = self._monitor_active or self._demo_active
        self.swd_panel.setEnabled(not busy)
        if hasattr(self, 'compat_demo_panel'):
            self.compat_demo_panel.setVisible(compat_source)
            self.compat_decoder_combo.setEnabled(not busy)
            for kind, (enabled, path, period, loop, pause, stop, choose) in self.compat_sources.items():
                for widget in (enabled, path, period, loop, choose):
                    widget.setEnabled(not busy)
                active = busy and compat_source and isinstance(self._monitor_worker, CompatDemoWorker) and self._monitor_worker.source_active(kind)
                pause.setEnabled(active)
                stop.setEnabled(active)
                pause.setToolTip('当前文件完成后暂停，不会截断CSV或图像帧。')
        self.network_panel.setVisible(network)
        self.udp_peer_panel.setVisible(source == "udp")
        self.playback_panel.setVisible(csv_source)
        self.matrix_directory_choose.setVisible(source == 'matrix')
        self.matrix_directory_choose.setEnabled(not busy)
        self.matrix_interval_spin.setVisible(source == 'matrix')
        self.matrix_interval_spin.setEnabled(not busy)
        self.playback_speed_spin.setVisible(source != 'matrix')
        self.playback_choose_button.setText('选择收发记录…' if source == 'stream' else '选择 CSV…')
        self.image_source_panel.setVisible(image_source)
        self.demo_panel.setVisible(demo)
        self.demo_panel.setEnabled(not busy)
        for widget in (self.monitor_port_combo, self.monitor_baud_edit, self.monitor_risk, self.serial_format_panel, self.serial_assistant_button):
            widget.setVisible(serial_source)
        network_ready = False
        if network:
            try:
                self._network_config()
                network_ready = True
            except ValueError:
                pass
        swd_ready = False
        if swd_source:
            try:
                self.swd_panel.config().require_ready()
                swd_ready = True
            except ValueError:
                pass
        ready = (
            swd_ready or
            demo or network_ready or (compat_source and any(fields[0].isChecked() and fields[1].text().strip() for fields in self.compat_sources.values())) or (csv_source and bool(self.playback_path_edit.text()))
            or (image_source and bool(self.image_directory_edit.text())) or (serial_source and
                self.monitor_port_combo.currentData() is not None
                and self._monitor_baudrate() is not None
                and self.monitor_risk.isChecked()
            )
        )
        self.monitor_connect_button.setEnabled(ready and not busy)
        self.monitor_connect_button.setText("开始演示" if demo else "开始回放" if csv_source or image_source else "开始监听" if source in {"tcp_server", "udp"} else "连接")
        self.monitor_disconnect_button.setEnabled(busy)
        self.monitor_disconnect_button.setText("停止演示" if self._demo_active else "断开")
        self.source_combo.setEnabled(not busy)
        self.monitor_port_combo.setEnabled(not busy and serial_source)
        self.monitor_port_search.setEnabled(not busy and serial_source)
        self.monitor_baud_edit.setEnabled(not busy and serial_source)
        self.decoder_combo.setEnabled(not busy and source not in {'demo', 'csv', 'images', 'swd'})
        self.serial_format_panel.setEnabled(not busy)
        self.serial_assistant_button.setEnabled(not busy and serial_source)
        self.monitor_risk.setEnabled(not busy and serial_source)
        self.network_panel.setEnabled(not busy)
        self.playback_choose_button.setEnabled(not busy)
        self.playback_speed_spin.setEnabled(not busy)
        self.playback_loop_check.setEnabled(not busy)
        self.playback_pause_button.setEnabled(busy and csv_source)
        self.image_directory_choose.setEnabled(not busy)
        self.image_fps_spin.setEnabled(not busy)
        self.image_loop_check.setEnabled(not busy)
        self.image_pause_button.setEnabled(busy and image_source)
        self.image_rx_check.setEnabled(not busy)
        self.workspace_load_button.setEnabled(not busy)
        self.refresh_button.setEnabled(not busy)
        self.monitor_export_button.setEnabled(self.sample_store.statistics.retained > 0)
        can_send = self._can_send_data()
        job_active = self._send_job.active
        manual_send = can_send and not job_active
        self.send_button.setEnabled(manual_send)
        self.send_file_button.setEnabled(manual_send)
        self.periodic_send_check.setEnabled(manual_send)
        self.send_interval_spin.setEnabled(manual_send and not self.periodic_send_check.isChecked())
        self.monitor_record_button.setEnabled(busy or self._sample_recorder is not None)
        self.stream_record_button.setEnabled(self._stream_recorder is not None or self._monitor_connected and source in {'serial', 'tcp_client', 'tcp_server', 'udp'})
        self.command_send_button.setEnabled(manual_send and not self.periodic_send_check.isChecked())
        self.command_sequence_button.setEnabled(manual_send and bool(self._commands))
        self.sequence_interval_spin.setEnabled(not job_active)
        self.sequence_repeat_spin.setEnabled(not job_active)
        self.send_job_stop_button.setEnabled(job_active)
        self.control_workspace.set_transmit_enabled(manual_send and not self.periodic_send_check.isChecked())
        if hasattr(self, 'dashboard'):
            self.dashboard.set_transmit_enabled(manual_send and not self.periodic_send_check.isChecked())

    def _can_send_data(self) -> bool:
        return (self._monitor_connected and self._monitor_worker is not None and not self._demo_active
                and bool(getattr(self._monitor_worker, 'can_send', True)))

    @Slot()
    def apply_serial_assistant_preset(self) -> None:
        """Fill a familiar terminal setup without touching the device or payload."""
        if self._monitor_active or self._demo_active:
            return
        self.source_combo.setCurrentIndex(self.source_combo.findData("serial"))
        self.monitor_port_search.clear()
        self.monitor_baud_edit.setText("115200")
        for combo, value in ((self.serial_bytesize_combo, 8), (self.serial_parity_combo, "N"),
                             (self.serial_stopbits_combo, 1), (self.decoder_combo, "RawData"),
                             (self.rx_display_combo, "text"), (self.send_mode_combo, "text"),
                             (self.send_ending_combo, "CRLF")):
            combo.setCurrentIndex(combo.findData(value))
        self.set_receive_encoding("utf-8")
        self.serial_dtr_check.setChecked(False)
        self.serial_rts_check.setChecked(False)
        self.monitor_status_label.setText("串口助手：115200 / 8N1 · 文本 / CRLF · 未连接")
        self._mark_workspace_dirty()
        self._update_monitor_controls()

    @Slot()
    def start_monitor(self) -> None:
        source = self.source_combo.currentData()
        if self._monitor_active or self._demo_active:
            return
        self._source_running = source
        self._rx_text_decoder.reset()
        self._ansi_sink.reset()
        if source == 'swd':
            try:
                worker = SwdLinkWorker(self.swd_panel.config(), await_ack=True)
            except ValueError as exc:
                self.monitor_status_label.setText(str(exc))
                return
            self.sample_interval_spin.setValue(10)
            self._launch_monitor_worker(worker, 'SWD内存采集 · 本轮HAL测试固件')
            return
        if source == 'compat_demo':
            try:
                worker = CompatDemoWorker(self._compat_demo_config(), await_ack=True)
            except ValueError as exc:
                self.monitor_status_label.setText(str(exc))
                return
            for fields in self.compat_sources.values():
                fields[4].setChecked(False)
            self._launch_monitor_worker(worker, 'VOFA Demo兼容 · CSV/JPG独立定时（不发送设备）')
            return
        if source == "demo":
            if self._monitor_active or self._demo_active:
                return
            self._reset_data_session()
            self._demo_active = True
            self._demo_step = 0
            self.monitor_status_label.setText("本软件合成演示中 · 未打开 COM 口，未实际采集设备数据。")
            self._append_monitor_log("开始本软件合成演示；不代表官方 VOFA+ Demo 算法或真实硬件。")
            self._update_monitor_controls()
            self._demo_timer.start(self.demo_interval_spin.value())
            return
        if source == 'images':
            if not self.image_directory_edit.text():
                return
            worker = ImageReplayWorker(self.image_directory_edit.text(), fps=self.image_fps_spin.value(),
                                       loop=self.image_loop_check.isChecked(), await_ack=True)
            worker.encoded.connect(self._handle_replay_image)
            worker.progress.connect(self._handle_image_progress)
            self.image_pause_button.setChecked(False)
            self.image_channel_combo.setCurrentIndex(0)
            self.image_progress.setValue(0)
            self._activate_visual_kind('image')
            self._launch_monitor_worker(worker, f'图像回放：{self.image_directory_edit.text()}')
            return
        if source == 'csv':
            if not self.playback_path_edit.text():
                return
            worker = CsvReplayWorker(self.playback_path_edit.text(), speed=self.playback_speed_spin.value(),
                                     loop=self.playback_loop_check.isChecked(), await_ack=True)
            worker.progress.connect(self.playback_progress.setValue)
            worker.names_changed.connect(self._handle_playback_names)
            self.playback_pause_button.setChecked(False)
            self.playback_progress.setValue(0)
            self._launch_monitor_worker(worker, f"CSV 回放：{self.playback_path_edit.text()}")
            return
        if source == 'stream':
            if not self.playback_path_edit.text():
                return
            try:
                worker = RawReplayWorker(self.playback_path_edit.text(), self._selected_decoder_name(),
                    speed=self.playback_speed_spin.value(), loop=self.playback_loop_check.isChecked(), await_ack=True)
            except ValueError as exc:
                self.monitor_status_label.setText(str(exc))
                return
            worker.progress.connect(self.playback_progress.setValue)
            self.playback_pause_button.setChecked(False)
            self.playback_progress.setValue(0)
            self._launch_monitor_worker(worker, f'原始记录回放（TX不重发）：{self.playback_path_edit.text()}')
            return
        if source == 'matrix':
            if self._selected_decoder_name() not in {'FireWater', 'JustFloat'}:
                self.monitor_status_label.setText('数值矩阵请选择 FireWater 或 JustFloat 数据引擎。')
                return
            if not self.playback_path_edit.text():
                return
            worker = MatrixReplayWorker(self.playback_path_edit.text(), interval_ms=self.matrix_interval_spin.value(),
                loop=self.playback_loop_check.isChecked(), as_float32=self._selected_decoder_name() == 'JustFloat')
            worker.progress.connect(self.playback_progress.setValue)
            self.playback_pause_button.setChecked(False)
            self.playback_progress.setValue(0)
            self.sample_interval_spin.setValue(self.matrix_interval_spin.value())
            self._launch_monitor_worker(worker, f'Demo数值矩阵：{self.playback_path_edit.text()}')
            return
        if source in {'tcp_client', 'tcp_server', 'udp'}:
            try:
                config = self._network_config()
                worker = NetworkLinkWorker(config, self._selected_decoder_name(), await_ack=True)
            except ValueError as exc:
                self.monitor_status_label.setText(str(exc))
                return
            self._launch_monitor_worker(worker, f"{source} {config.host}:{config.port}")
            return
        device = self.monitor_port_combo.currentData()
        baudrate = self._monitor_baudrate()
        if (
            self._monitor_active or self._demo_active
            or not isinstance(device, str)
            or baudrate is None
            or not self.monitor_risk.isChecked()
        ):
            self._update_monitor_controls()
            return
        owner = PORT_LEASES.owner_for(device)
        if owner is not None:
            self.monitor_status_label.setText(f"无法连接：{device} 已由 {owner} 占用。")
            return
        try:
            worker = SerialLinkWorker(device, baudrate, self._selected_decoder_name(),
                await_ack=True,
                bytesize=self.serial_bytesize_combo.currentData(), parity=self.serial_parity_combo.currentData(),
                stopbits=self.serial_stopbits_combo.currentData(),
                dtr=self.serial_dtr_check.isChecked(), rts=self.serial_rts_check.isChecked(),
                **({"flow_control": self.serial_flow_combo.currentData()}
                   if self.serial_flow_combo.currentData() != "none" else {}))
        except ValueError as exc:
            self.monitor_status_label.setText(str(exc))
            return
        self._launch_monitor_worker(worker, f"{device} @ {baudrate}，{self.decoder_combo.currentText()}")

    def _launch_monitor_worker(self, worker, description: str) -> None:
        self._monitor_error = None
        self._reset_data_session()
        self._image_rx_enabled = self.image_rx_check.isChecked() or self._selected_decoder_name() == 'ImageStream'
        if self._selected_decoder_name() == 'ImageStream':
            self.image_channel_combo.setCurrentIndex(0)
        if self._image_rx_enabled or self._selected_decoder_name() in {'FireWaterImage', 'JustFloatImage'}:
            self._activate_visual_kind('image')
        self._monitor_thread = QThread(self)
        self._monitor_connected = False
        self._monitor_worker = worker
        self._monitor_worker.moveToThread(self._monitor_thread)
        self._monitor_thread.started.connect(self._monitor_worker.run)
        self._monitor_worker.events.connect(self._handle_monitor_events)
        self._monitor_worker.raw.connect(self._handle_monitor_raw)
        self._monitor_worker.status.connect(self._handle_monitor_status)
        self._monitor_worker.transmitted.connect(self._handle_monitor_transmitted)
        self._monitor_worker.finished.connect(self._monitor_thread.quit)
        self._monitor_thread.finished.connect(self._on_monitor_finished)
        self._monitor_thread.finished.connect(self._monitor_worker.deleteLater)
        self._monitor_thread.finished.connect(self._monitor_thread.deleteLater)
        self._monitor_active = True
        self.monitor_status_label.setText(f"正在启动 {description}")
        self._append_monitor_log(f"启动数据源：{description}。")
        self._update_monitor_controls()
        self._monitor_thread.start()

    def _choose_compat_directory(self, target) -> None:
        directory = QFileDialog.getExistingDirectory(self, '选择Demo目录', target.text())
        if directory:
            target.setText(directory)
            self._mark_workspace_dirty()

    def _compat_demo_config(self) -> CompatDemoConfig:
        schedules = {}
        for kind, fields in self.compat_sources.items():
            enabled, path, period, loop, *_rest = fields
            schedules[kind] = DemoSchedule(path.text(), enabled.isChecked(), period.value(), loop.isChecked())
        return CompatDemoConfig(self.compat_decoder_combo.currentData(), **schedules)

    def _load_compat_demo_config(self, document) -> None:
        config = CompatDemoConfig.from_dict(document)
        self.compat_decoder_combo.setCurrentIndex(self.compat_decoder_combo.findData(config.decoder))
        for kind, fields in self.compat_sources.items():
            schedule = getattr(config, kind)
            fields[0].setChecked(schedule.enabled)
            fields[1].setText(schedule.directory)
            fields[2].setValue(schedule.period_ms)
            fields[3].setChecked(schedule.loop)
            fields[4].setChecked(False)

    def _pause_compat_source(self, kind: str, paused: bool) -> None:
        if isinstance(self._monitor_worker, CompatDemoWorker):
            self._monitor_worker.set_source_paused(kind, paused)
        if hasattr(self, 'compat_sources'):
            self.compat_sources[kind][4].setText('继续' if paused else '暂停')

    def _stop_compat_source(self, kind: str) -> None:
        if isinstance(self._monitor_worker, CompatDemoWorker):
            self._monitor_worker.stop_source(kind)
            self.compat_sources[kind][4].setEnabled(False)
            self.compat_sources[kind][5].setEnabled(False)

    @Slot(object)
    def _handle_playback_names(self, _names) -> None:
        self._source_channel_names = tuple(_names)
        self._refresh_channel_values()

    @Slot()
    def stop_monitor(self) -> None:
        self.stop_send_job()
        self._stop_stream_recording()
        self.periodic_send_check.setChecked(False)
        self._periodic_timer.stop()
        self._stop_sample_recording()
        if self._demo_active:
            self._demo_timer.stop()
            self._demo_active = False
            self.monitor_status_label.setText("合成演示已停止 · 未连接真实设备。")
            self._append_monitor_log("合成演示已停止。")
            self._update_monitor_controls()
            return
        if self._monitor_worker is not None and self._monitor_active:
            self._monitor_connected = False
            self.monitor_status_label.setText("正在断开，等待接收线程退出…")
            self._monitor_worker.stop()
            self._update_monitor_controls()
            self.monitor_disconnect_button.setEnabled(False)

    @Slot(str, str)
    def _handle_monitor_status(self, kind: str, message: str) -> None:
        if kind == "connected":
            self._monitor_connected = True
        elif kind in {"error", "stopped", "completed"}:
            self._monitor_connected = False
            self.stop_send_job()
            self.periodic_send_check.setChecked(False)
            self._periodic_timer.stop()
        if kind == "error":
            self._monitor_error = message
        if kind not in {"stopped", "notice"} or self._monitor_error is None and kind == "stopped":
            self.monitor_status_label.setText(message)
        self._append_monitor_log(message)
        self._update_monitor_controls()

    @Slot()
    def send_monitor_data(self, *, record_history: bool = True) -> bool:
        worker = self._monitor_worker
        if not self._can_send_data() or self._send_job.active:
            return False
        try:
            payload = encode_send_payload(
                self.send_text_edit.text(), mode=self.send_mode_combo.currentData(),
                line_ending=self.send_ending_combo.currentData(),
                encoding=self._tx_encoding,
            )
            depth = worker.enqueue_send(payload)
            self._tx_metadata.append((payload, {
                "text": self.send_text_edit.text(), "mode": self.send_mode_combo.currentData(),
                "ending": self.send_ending_combo.currentData(), "encoding": self._tx_encoding,
            } if record_history else None))
        except (ValueError, TransportError) as exc:
            self._append_monitor_log(f"发送未入队：{exc}")
            self.tx_status_label.setText(f"发送失败：{exc}")
            self.periodic_send_check.setChecked(False)
            return False
        self.tx_status_label.setText(f"TX 已交驱动 {self._tx_bytes} 字节 · 待发送 {depth} 包")
        return True

    @Slot(bool)
    def _toggle_periodic_send(self, enabled: bool) -> None:
        self._periodic_timer.stop()
        if not enabled:
            if self._monitor_worker is not None:
                self._discard_pending_transmissions()
            self._update_monitor_controls()
            return
        if self._send_job.active:
            self.periodic_send_check.setChecked(False)
            return
        try:
            if not self._can_send_data():
                raise ValueError("当前数据源不可发送")
            encode_send_payload(self.send_text_edit.text(), mode=self.send_mode_combo.currentData(),
                                line_ending=self.send_ending_combo.currentData(), encoding=self._tx_encoding)
        except ValueError as exc:
            self._append_monitor_log(f"定时发送未启动：{exc}")
            self.periodic_send_check.setChecked(False)
            return
        self._periodic_timer.start(self.send_interval_spin.value())
        self._append_monitor_log(f"已手动开启定时发送：每 {self.send_interval_spin.value()} ms；断开后不恢复。")
        self._update_monitor_controls()

    @Slot()
    def _send_periodic(self) -> None:
        if not self.periodic_send_check.isChecked() or not self.send_monitor_data(record_history=False):
            self.periodic_send_check.setChecked(False)
            self._periodic_timer.stop()

    @Slot(bytes)
    def _handle_monitor_transmitted(self, payload: bytes) -> None:
        self._tx_bytes += len(payload)
        self._tx_completion_count += 1
        self.packet_store.append("TX", payload)
        if self._tx_metadata:
            expected, entry = self._tx_metadata.popleft()
            if expected != payload:
                self._tx_metadata.clear()
            elif entry is not None:
                self.send_history.record(**entry)
        self._record_stream_bytes('TX', payload)
        self.tx_status_label.setText(f"TX 已交驱动 {self._tx_bytes} 字节（不代表设备已执行）")
        self._append_monitor_log(f"TX {payload[:128].hex(' ').upper()}"
                                 + (" …（界面截断）" if len(payload) > 128 else ""),
                                 full_message=f"TX {payload.hex(' ').upper()}")

    @Slot()
    def configure_file_sending(self) -> None:
        if self._send_job.active:
            self.monitor_status_label.setText('请先停止发送任务，再修改文件分包设置。')
            return
        dialog = QDialog(self)
        dialog.setWindowTitle('文件分包发送设置')
        layout = QVBoxLayout(dialog)
        chunk = QSpinBox()
        chunk.setRange(1, 16384)
        chunk.setValue(self._file_send_chunk_size)
        chunk.setSuffix(' 字节/包')
        delay = QSpinBox()
        delay.setRange(20, 60000)
        delay.setValue(self._file_send_interval_ms)
        delay.setSuffix(' ms')
        layout.addWidget(QLabel('文件作为原始字节发送，不执行烧录协议。低波特率请使用更小分包或更长间隔。'))
        layout.addWidget(chunk)
        layout.addWidget(delay)
        accept = QPushButton('应用')
        accept.clicked.connect(dialog.accept)
        layout.addWidget(accept)
        if dialog.exec() == QDialog.Accepted:
            self._file_send_chunk_size, self._file_send_interval_ms = chunk.value(), delay.value()

    @Slot()
    def toggle_stream_recording(self) -> None:
        if self._stream_recorder is not None:
            self._stop_stream_recording()
            return
        if not self._monitor_connected or self._source_running not in {'serial', 'tcp_client', 'tcp_server', 'udp'}:
            return
        filename, _ = QFileDialog.getSaveFileName(self, '录制原始RX/TX（不覆盖）', 'rx-tx.jsonl', 'QX记录 (*.jsonl)')
        if not filename or not self._monitor_connected:
            return
        try:
            recorder = AsyncRecorder(RotatingStreamRecorder(filename))
            recorder.open()
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, '无法开始记录', str(exc))
            return
        self._stream_recorder = recorder
        self.stream_record_button.setText('停止原始记录')
        self._stream_flush_timer.start(1000)
        self._append_monitor_log(f'开始原始RX/TX记录：{filename}；128MiB分卷，会话清单 {filename}.session.json；TX为驱动完成事件，不是设备ACK。')
        self._update_monitor_controls()

    def _record_stream_bytes(self, direction: str, payload: bytes) -> None:
        if self._stream_recorder is None:
            return
        try:
            if not self._stream_recorder.record(direction, payload):
                self._flush_stream_recording()
        except (OSError, ValueError) as exc:
            self._append_monitor_log(f'原始记录停止：{exc}')
            self._stop_stream_recording()

    @Slot()
    def _flush_stream_recording(self) -> None:
        if self._stream_recorder is not None:
            if self._stream_recorder.statistics.error:
                self._append_monitor_log(f'原始记录写盘失败：{self._stream_recorder.statistics.error}')
                self._stop_stream_recording()

    def _stop_stream_recording(self) -> None:
        self._stream_flush_timer.stop()
        recorder, self._stream_recorder = self._stream_recorder, None
        if recorder is not None:
            recorder.close()
            self._closing_recorders.append(('原始RX/TX', recorder))
        if hasattr(self, 'stream_record_button'):
            self.stream_record_button.setText('录制原始 RX/TX…')

    def set_receive_encoding(self, encoding: str) -> None:
        if encoding not in ('utf-8', 'gbk', 'latin-1', 'ascii', 'utf-16le'):
            return
        self._rx_encoding = encoding
        self._rx_text_decoder = SerialWebTextDecoder(encoding)
        self._ansi_sink.reset()
        self.rx_display_combo.setItemText(self.rx_display_combo.findData('text'), f'{encoding.upper()} 接收')
        self._append_monitor_log(f'文本显示编码切换为 {encoding}；仅重置字符边界，不改变原始接收数据。')

    def set_transmit_encoding(self, encoding: str) -> None:
        index = self.tx_encoding_combo.findData(encoding)
        if index < 0:
            raise ValueError("发送编码无效")
        self._tx_encoding = encoding
        self.tx_encoding_combo.blockSignals(True)
        self.tx_encoding_combo.setCurrentIndex(index)
        self.tx_encoding_combo.blockSignals(False)
        self._update_send_preview()

    def _refresh_send_history(self) -> None:
        self.send_history_menu.clear()
        if not self.send_history.entries:
            self.send_history_menu.addAction("暂无成功手动发送记录").setEnabled(False)
        for entry in self.send_history.entries:
            title = f"{entry['mode']} / {entry['encoding']} / {entry['ending']} · {entry['text'][:36]}"
            self.send_history_menu.addAction(title, lambda checked=False, selected=entry: self._fill_send_history(selected))
        self.send_history_menu.addSeparator()
        self.send_history_menu.addAction("清空历史", self.send_history.clear)

    def _fill_send_history(self, entry) -> None:
        if self._send_job.active or self.periodic_send_check.isChecked():
            self.monitor_status_label.setText("请先停止当前发送任务；历史回填不会修改正在发送的内容。")
            return
        self.send_mode_combo.setCurrentIndex(self.send_mode_combo.findData(entry["mode"]))
        self.send_ending_combo.setCurrentIndex(self.send_ending_combo.findData(entry["ending"]))
        self.set_transmit_encoding(entry["encoding"])
        self.send_text_edit.setText(entry["text"])

    @Slot()
    def open_serial_tools(self) -> None:
        from .vofa.serial_tools import SerialToolsDialog
        if self.serial_tools_dialog is None:
            self.serial_tools_dialog = SerialToolsDialog(self)
        self.serial_tools_dialog.show()
        self.serial_tools_dialog.raise_()

    def apply_serial_preset(self, preset) -> None:
        if self._monitor_active or self._demo_active:
            return
        self.source_combo.setCurrentIndex(self.source_combo.findData("serial"))
        self.monitor_port_combo.setCurrentIndex(self.monitor_port_combo.findData(preset.port))
        self.monitor_baud_edit.setText(str(preset.baudrate))
        for widget, value in ((self.serial_bytesize_combo, preset.bytesize),
                              (self.serial_parity_combo, preset.parity),
                              (self.serial_stopbits_combo, preset.stopbits),
                              (self.serial_flow_combo, preset.flow_control),
                              (self.send_ending_combo, preset.ending)):
            widget.setCurrentIndex(widget.findData(value))
        self.set_receive_encoding(preset.rx_encoding)
        self.set_transmit_encoding(preset.tx_encoding)
        self.serial_dtr_check.setChecked(False)
        self.serial_rts_check.setChecked(False)
        self.monitor_risk.setChecked(False)
        self._mark_workspace_dirty()
        self._update_monitor_controls()
        self.monitor_status_label.setText("已填入预设，未连接、未发送；端口可能变化，请核对后手动连接。")

    def _update_send_preview(self, *_args) -> None:
        try:
            data = encode_send_payload(self.send_text_edit.text(), mode=self.send_mode_combo.currentData(),
                                       line_ending=self.send_ending_combo.currentData(), encoding=self._tx_encoding)
            shown = data[:24].hex(' ').upper()
            self.send_preview_label.setText(f"实际发送：{len(data)} 字节 · {shown}" + (" …" if len(data) > 24 else ""))
            self.send_preview_label.setToolTip(data.hex(' ').upper())
        except ValueError as exc:
            self.send_preview_label.setText(f"不能发送：{exc}")
            self.send_preview_label.setToolTip(str(exc))

    @Slot()
    def change_terminal_font(self) -> None:
        size, accepted = QInputDialog.getInt(self, '终端字体', '字号（px）', self._terminal_font_size, 8, 24)
        if accepted:
            self._terminal_font_size = size
            self._terminal_font_customized = True
            self.monitor_log_view.setStyleSheet(f'font-size: {size}px;')

    @Slot()
    def find_terminal_text(self) -> None:
        text, accepted = QInputDialog.getText(self, '查找终端文本', '向下查找（末尾后回到开头）')
        if accepted and text:
            if not self.monitor_log_view.find(text):
                self.monitor_log_view.moveCursor(QTextCursor.Start)
                if not self.monitor_log_view.find(text):
                    self.monitor_status_label.setText('当前终端显示中没有匹配文本。')

    @Slot()
    def save_terminal_text(self) -> None:
        filename, _ = QFileDialog.getSaveFileName(self, '保存终端当前显示（不覆盖）', 'terminal.log', '文本 (*.log *.txt)')
        if filename:
            try:
                with Path(filename).open('x', encoding='utf-8') as stream:
                    stream.write(self.monitor_log_view.toPlainText())
            except OSError as exc:
                QMessageBox.warning(self, '保存失败', str(exc))

    @Slot()
    def clear_terminal_text(self) -> None:
        count = self.monitor_log_view.document().blockCount()
        if self.monitor_log_view.toPlainText() and QMessageBox.question(self, '清空终端显示', f'清空当前约{count}行显示？磁盘日志不受影响，当前显示不能撤销。', QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
            self.monitor_log_view.clear()

    @Slot()
    def toggle_sample_recording(self) -> None:
        if self._sample_recorder is not None:
            self._stop_sample_recording()
            return
        if not (self._monitor_active or self._demo_active):
            return
        filename, _ = QFileDialog.getSaveFileName(self, "开始实时采样记录（新文件）", "capture.csv", "CSV (*.csv)")
        if not filename:
            return
        if Path(filename).exists():
            QMessageBox.warning(self, '记录失败', '文件已存在；请使用新文件名，不会覆盖已有记录。')
            return
        recorder = AsyncRecorder(CsvRecorder(filename, tuple(self._effective_channel_names())))
        try:
            recorder.open()
        except OSError as exc:
            recorder.close()
            QMessageBox.warning(self, "记录失败", str(exc))
            return
        self._sample_recorder = recorder
        self._recorded_rows = 0
        self.monitor_record_button.setText("停止实时记录")
        self.record_status_label.setText("记录中 · 0 行")
        self._append_monitor_log(f"开始 CSV 记录：{filename}；来源：{self._source_running or self.source_combo.currentData()}。")
        self._record_flush_timer.start(500)

    def _flush_sample_recording(self) -> None:
        if self._sample_recorder is not None:
            stats = self._sample_recorder.statistics
            self.record_status_label.setText(f'CSV 已保存 {stats.saved} 帧 · 排队 {stats.queued} · 未保存 {stats.unsaved}')
            if stats.error:
                self._append_monitor_log(f"CSV 记录失败：{stats.error}；未保存 {stats.unsaved} 帧")
                self._stop_sample_recording()

    def _stop_sample_recording(self) -> None:
        self._record_flush_timer.stop()
        recorder, self._sample_recorder = self._sample_recorder, None
        if recorder is not None:
            recorder.close()
            self._closing_recorders.append(('CSV', recorder))
            self.record_status_label.setText(f"正在收尾 · 已入队 {self._recorded_rows} 行（尚非全部写盘）")
        self.monitor_record_button.setText("开始实时记录 CSV")

    def _poll_recorders(self) -> None:
        self._flush_sample_recording()
        self._flush_stream_recording()
        if self._monitor_text_recorder is not None and self._monitor_text_recorder.statistics.error:
            recorder, self._monitor_text_recorder = self._monitor_text_recorder, None
            self._monitor_log_limit_reached = True
            recorder.close()
            self._closing_recorders.append(('文本日志', recorder))
            stats = recorder.statistics
            self.monitor_log_view.appendPlainText(f'日志记录已停止：{stats.error}；未保存 {stats.unsaved} 批。采集不受影响。')
        for kind, recorder in self._closing_recorders[:]:
            if recorder.wait_closed(0):
                self._closing_recorders.remove((kind, recorder))
                stats = recorder.statistics
                message = f'{kind}记录结束 · 已保存 {stats.saved} 帧 · 未保存 {stats.unsaved} 帧'
                if stats.error:
                    message += f' · {stats.error}'
                if kind == 'CSV':
                    self.record_status_label.setText(message)
                self._append_monitor_log(message)

    @Slot(object)
    def _handle_monitor_events(self, events: list[TelemetryEvent]) -> None:
        try:
            self._consume_monitor_events(events)
        finally:
            worker = self._monitor_worker
            if worker is not None and hasattr(worker, 'ack_batch'):
                worker.ack_batch()

    def _consume_monitor_events(self, events: list[TelemetryEvent]) -> None:
        names = self._effective_channel_names()
        lines: list[str] = []
        waveform_changed = False
        sample_log_lines = 0
        omitted_samples = 0
        latest_images: dict[int, TelemetryEvent] = {}
        sample_frames = 0
        for item in events:
            frame = self.sample_store.append_event(item, names=tuple(names))
            if item.error:
                lines.append(f"解析错误：{item.error}")
            if item.text and item.samples is None:
                lines.append(f"文本：{item.text[:300]}")
            if item.image is not None:
                latest_images[item.image_index if item.image_index is not None else 0] = item
                self._image_frames += 1
            if frame is not None:
                sample_frames += 1
                visible_samples = frame.values[:_MAX_MONITOR_CHANNELS]
                frame_names = tuple(names[index] if index < len(names) else frame.names[index]
                                    for index in range(min(16, len(frame.values))))
                self._sample_index += 1
                try:
                    display_samples = self._display_samples(visible_samples)
                    self._last_values = display_samples
                    self.waveform.append_samples(display_samples, self._display_names(frame_names))
                except ValueError as exc:
                    if sample_log_lines < _MAX_SAMPLE_LOG_LINES_PER_BATCH:
                        lines.append(str(exc))
                if self._sample_recorder is not None:
                    try:
                        accepted = self._sample_recorder.record(
                            TelemetryEvent(raw=b"", samples=frame.values, names=frame.names,
                                           received_at=frame.received_at, source_timestamp=frame.source_timestamp))
                        if accepted:
                            self._recorded_rows += len(frame.values)
                        else:
                            self._flush_sample_recording()
                    except (OSError, ValueError) as exc:
                        self._append_monitor_log(f"CSV 记录失败：{exc}")
                        self._stop_sample_recording()
                waveform_changed = True
                if sample_log_lines < _MAX_SAMPLE_LOG_LINES_PER_BATCH:
                    lines.append(
                        ("合成采样：" if self._demo_active else "回放采样：" if self._source_running in {'csv', 'stream', 'matrix'} else "采样：")
                        + ", ".join(f"{value:.6g}" for value in visible_samples)
                        + (f" …（只显示前 {_MAX_MONITOR_CHANNELS}/{len(item.samples)} 通道）"
                           if len(item.samples) > _MAX_MONITOR_CHANNELS else "")
                    )
                    sample_log_lines += 1
                else:
                    omitted_samples += 1
        if latest_images:
            for image_index, latest_image in latest_images.items():
                self._cache_received_image(image_index, latest_image.image, latest_image.image_metadata)
                lines.append(f'图像帧 {self._image_frames} · 索引 {latest_image.image_index} · 头字段 {latest_image.image_metadata}')
            if self._selected_decoder_name() in {'FireWaterImage', 'JustFloatImage'} and not self._image_auto_selected:
                self._activate_visual_kind('image')
                self._image_auto_selected = True
        if omitted_samples:
            lines.append(f"本批另有 {omitted_samples} 条采样仅更新波形和最近采样缓存。")
        if waveform_changed:
            self.sample_store.record_render_omitted(max(0, sample_frames - 1))
            self.waveform.update()
            self.control_workspace.set_samples(self._last_values, names)
            self._refresh_channel_values()
            if hasattr(self, 'dashboard'):
                self._refresh_dashboard()
        if lines:
            self._append_monitor_batch(lines)
        stats = self.sample_store.statistics
        self.sample_count_label.setText(f"解析 {stats.parsed} · 保留 {stats.retained} · 淘汰 {stats.evicted} · 绘图合并 {stats.render_omitted} · 无效 {stats.invalid}")
        self.sample_count_label.setToolTip(f'收到{stats.received_bytes}字节；绘图合并不丢采样，历史淘汰不等于解析丢帧。无效帧已拒绝，设备链路是否丢帧须检查发送序号。')
        self._update_monitor_controls()

    @Slot()
    def _emit_demo_sample(self) -> None:
        if not self._demo_active:
            return
        phase = self._demo_step * 0.13
        self._demo_step += 1
        values = tuple(self.demo_offset_spin.value() + self.demo_amplitude_spin.value()
                       * math.sin(phase * (1 + index * .07) + index * math.pi / 3)
                       for index in range(self.demo_channels_spin.value()))
        self._handle_monitor_events([TelemetryEvent(raw=b"", samples=values)])

    @Slot()
    def _on_monitor_finished(self) -> None:
        self._monitor_connected = False
        self.stop_send_job()
        self._stop_stream_recording()
        self.periodic_send_check.setChecked(False)
        self._periodic_timer.stop()
        self._stop_sample_recording()
        self._monitor_active = False
        self._monitor_worker = None
        self._monitor_thread = None
        self.playback_pause_button.setChecked(False)
        self.image_pause_button.setChecked(False)
        self.monitor_risk.setChecked(False)
        self._update_monitor_controls()

    @Slot(bytes)
    def _handle_monitor_raw(self, chunk: bytes) -> None:
        self._rx_bytes += len(chunk)
        self.packet_store.append("RX", chunk)
        self.sample_store.record_received_bytes(len(chunk))
        self._record_stream_bytes('RX', chunk)
        self.rx_status_label.setText(f"RX：{self._rx_bytes} 字节")
        if self._image_rx_enabled:
            try:
                images = self._image_decoder.feed(chunk)
                if images:
                    self._cache_received_image(0, images[-1])
                    self._image_frames += len(images)
                for error in self._image_decoder.errors[:4]:
                    self._append_monitor_log(f'图像帧：{error}')
            except (TypeError, ValueError) as exc:
                self._append_monitor_log(f'图像帧解析失败：{exc}')
        text = self._rx_text_decoder.decode(chunk)
        if self.rx_display_combo.currentData() == 'text':
            shown = text[:512]
            suffix = " …" if len(text) > 512 else ""
            self._append_monitor_ansi(f"RX {shown}{suffix}", full_message=f"RX {chunk.hex(' ').upper()}")
        else:
            shown = chunk[:128].hex(" ").upper()
            suffix = f" …（还有 {len(chunk) - 128} 字节）" if len(chunk) > 128 else ""
            self._append_monitor_log(f"RX {shown}{suffix}", full_message=f"RX {chunk.hex(' ').upper()}")

    def _append_monitor_ansi(self, message: str, *, full_message: str | None = None) -> None:
        stamp = datetime.now().astimezone()
        prefix = f"[{stamp:%H:%M:%S.%f}"[:-3] + "] " if self.show_timestamps_action.isChecked() else ""
        visible = self.show_rx_action.isChecked()
        if visible and not self.pause_terminal_action.isChecked():
            self.monitor_log_view.append_ansi(message, self._ansi_sink, prefix)
        self._persist_monitor_lines([(prefix + (full_message or message))])

    def _append_monitor_log(self, message: str, *, full_message: str | None = None) -> None:
        stamp = datetime.now().astimezone()
        prefix = f"[{stamp:%H:%M:%S.%f}"[:-3] + "] "
        visible = not ((message.startswith('RX ') and not self.show_rx_action.isChecked()) or (message.startswith('TX ') and not self.show_tx_action.isChecked()))
        if visible and not self.pause_terminal_action.isChecked():
            self.monitor_log_view.appendPlainText((prefix if self.show_timestamps_action.isChecked() else '') + message)
        self._persist_monitor_lines([prefix + (full_message or message)])

    def _append_monitor_batch(self, lines: list[str]) -> None:
        stamp = datetime.now().astimezone()
        prefix = f"[{stamp:%H:%M:%S.%f}"[:-3] + "] "
        entries = [prefix + line for line in lines]
        if not self.pause_terminal_action.isChecked():
            self.monitor_log_view.appendPlainText("\n".join(entries if self.show_timestamps_action.isChecked() else lines))
        self._persist_monitor_lines(entries)

    def _persist_monitor_lines(self, entries: list[str]) -> None:
        if self._monitor_log_limit_reached:
            return
        payload = "\n".join(entries) + "\n"
        byte_count = len(payload.encode("utf-8"))
        if self._monitor_text_recorder is None:
            self._monitor_log_file = self._log_dir / f"monitor-{datetime.now():%Y%m%d-%H%M%S-%f}.log"
            self._monitor_text_recorder = AsyncRecorder(RotatingTextLogSink(
                self._monitor_log_file, max_bytes=_MAX_MONITOR_LOG_BYTES))
            self._monitor_text_recorder.open()
        if self._monitor_text_recorder.record(payload):
            self._monitor_log_bytes += byte_count
        else:
            self._poll_recorders()

    @Slot()
    def clear_monitor_data(self) -> None:
        if self._samples or self.monitor_log_view.toPlainText():
            if QMessageBox.question(self, '清空当前采样',
                f'将清空内存中 {len(self._samples)} 帧采样、波形和终端显示。磁盘记录不受影响；未导出的内容无法恢复。继续吗？',
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                return
        self.sample_store.clear()
        self._sample_index = 0
        self._last_values = ()
        self._rx_text_decoder.reset()
        self._ansi_sink.reset()
        self._refresh_channel_values()
        self.control_workspace.set_samples((), self._effective_channel_names())
        self.waveform.clear()
        self._paint_dashboard()
        self.monitor_log_view.clear()
        self.sample_count_label.setText("采样点：0")
        self._update_monitor_controls()

    @Slot()
    def export_monitor_csv(self) -> None:
        if not self._samples:
            return
        filename, _ = QFileDialog.getSaveFileName(self, "导出最近采样 CSV", "samples.csv", "CSV (*.csv)")
        if not filename:
            return
        path = Path(filename)
        if path.exists():
            QMessageBox.warning(self, "文件已存在", "为避免覆盖已有记录，请选择新的 CSV 文件名。")
            return
        recorder = CsvRecorder(path, tuple(self._effective_channel_names()))
        try:
            with recorder:
                for frame in self.sample_store.snapshot():
                    recorder.record(TelemetryEvent(raw=b"", samples=frame.values, names=frame.names,
                                                   received_at=frame.received_at, source_timestamp=frame.source_timestamp))
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "导出失败", str(exc))
            return
        self._append_monitor_log(f"已导出最近 {len(self.sample_store.snapshot())} 点到 {path}；优先保留来源文件时间，否则使用软件接收时间（不是设备采样时间）。")

    @Slot()
    def save_monitor_workspace(self, _checked=False, *, path: Path | None = None) -> bool:
        baudrate = self._monitor_baudrate()
        if path is None:
            filename, _ = QFileDialog.getSaveFileName(self, "保存工作区", "qx-monitor.json", "JSON (*.json)")
            if not filename:
                return False
        else:
            filename = str(path)
        try:
            self._sync_command_group()
            def optional_port(text: str) -> int | None:
                text = text.strip()
                if not text:
                    return None
                if not text.isascii() or not text.isdecimal():
                    raise ValueError('端口必须为整数')
                return int(text)
            pane_sizes = tuple(self.monitor_splitter.sizes())
            plot_sizes = tuple(self.monitor_plot_splitter.sizes())
            settings = WorkspaceSettings(
                port=self.monitor_port_combo.currentData() or "",
                baudrate=baudrate,
                decoder=self._selected_decoder_name(),
                channel_names=tuple(self._entered_channel_names()),
                visible_channels=tuple(check.isChecked() for check in self.channel_visibility_checks),
                sample_interval_ms=self.sample_interval_spin.value(),
                buffer_samples=self.wave_buffer_spin.value(),
                tx_mode=self.send_mode_combo.currentData(), tx_ending=self.send_ending_combo.currentData(),
                tx_text=self.send_text_edit.text(), tx_interval_ms=self.send_interval_spin.value(),
                source=self.source_combo.currentData(),
                swd=self.swd_panel.config().document(),
                network_host=self.network_host_edit.text().strip(),
                network_port=optional_port(self.network_port_edit.text()),
                udp_peer_host=self.udp_peer_host_edit.text().strip(),
                udp_peer_port=optional_port(self.udp_peer_port_edit.text()),
                replay_path=self.playback_path_edit.text(), replay_speed=self.playback_speed_spin.value(),
                replay_loop=self.playback_loop_check.isChecked(), theme=self._theme,
                precision=self.precision_spin.value(), rx_display=self.rx_display_combo.currentData(),
                monitor_splitter_sizes=pane_sizes if all(x > 0 for x in pane_sizes) else (280, 700, 190),
                plot_splitter_sizes=plot_sizes if all(x > 0 for x in plot_sizes) else (750, 250),
                commands=tuple(asdict(entry) for entry in self._commands),
                controls=tuple(self.control_workspace.export_config()),
                engine_manifests=tuple(entry.document() for entry in PLUGIN_REGISTRY.engines),
                serial_bytesize=self.serial_bytesize_combo.currentData(),
                serial_parity=self.serial_parity_combo.currentData(),
                serial_stopbits=self.serial_stopbits_combo.currentData(),
                image_directory=self.image_directory_edit.text(), image_fps=self.image_fps_spin.value(),
                image_loop=self.image_loop_check.isChecked(), image_rx=self.image_rx_check.isChecked(),
                demo_channels=self.demo_channels_spin.value(), demo_interval_ms=self.demo_interval_spin.value(),
                demo_amplitude=self.demo_amplitude_spin.value(), demo_offset=self.demo_offset_spin.value(),
                sequence_interval_ms=self.sequence_interval_spin.value(), sequence_repeat_count=self.sequence_repeat_spin.value(),
                active_visual_tab=min(3, max(0, self.visual_tabs.currentIndex())),
                pages=self.dashboard.pages(),
                active_page_id=self.dashboard.active_page_id(),
                command_groups=tuple(group.to_document() for group in self._command_groups),
                compat_demo=self._compat_demo_config().to_dict(),
                analysis_config=self.analysis_view.export_config(),
                channel_config=tuple(asdict(item) for item in self._channel_display),
                controls_layout=self.control_workspace.layout_mode,
                themes=custom_theme_documents(),
                plot_mode=self.waveform.mode, plot_xy_channels=self.waveform.xy_channels,
                plot_grid=self.waveform.grid_visible, plot_line_width=self.waveform.line_width,
                plot_axis_ranges=tuple(self.waveform.axis_ranges[key] for key in ('x_min', 'x_max', 'y_min', 'y_max')),
                channel_colors=tuple(self.waveform.custom_channel_colors.get(index) for index in range(16)),
                rx_encoding=self._rx_encoding, tx_encoding=self._tx_encoding,
                serial_flow_control=self.serial_flow_combo.currentData(),
                connection_presets=tuple(self._connection_presets),
                serial_tools=self.serial_tools_dialog.state() if self.serial_tools_dialog is not None else self._serial_tools_state,
                show_rx=self.show_rx_action.isChecked(), show_tx=self.show_tx_action.isChecked(),
                show_timestamps=self.show_timestamps_action.isChecked(), terminal_font_size=self._terminal_font_size,
                file_chunk_size=self._file_send_chunk_size, file_interval_ms=self._file_send_interval_ms,
                matrix_interval_ms=self.matrix_interval_spin.value(),
                image_channel=self.image_channel_combo.currentData(), image_effect=self.image_view.effect,
                image_effect_param=self.image_view.effect_parameter,
                image_rotation=self.image_view.rotation,
            )
            save_workspace(filename, settings)
        except (OSError, TypeError, ValueError) as exc:
            QMessageBox.warning(self, "保存失败", str(exc))
            return False
        self._append_monitor_log(f"工作区已保存：{filename}；不包含连接状态与采样数据。")
        self._workspace_dirty = False
        return True

    def save_update_snapshot(self) -> bool:
        """Persist workspace and 20 send-history entries before an update installs."""

        from .updates.config import UpdatePaths, atomic_write
        from .updates.manifest import file_sha256

        paths = UpdatePaths.current()
        try:
            paths.recovery_dir.mkdir(parents=True, exist_ok=True)
            workspace_path = paths.recovery_dir / "workspace.json"
            history_path = paths.recovery_dir / "send-history.json"
            if not self.save_monitor_workspace(path=workspace_path):
                return False
            entries = list(self.send_history.entries)
            atomic_write(
                history_path,
                (json.dumps(entries, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
            )
            summary = {
                "version": __version__,
                "saved_utc": datetime.now().astimezone().isoformat(timespec="seconds"),
                "workspace_sha256": file_sha256(workspace_path),
                "send_history_sha256": file_sha256(history_path),
                "send_history_count": len(entries),
            }
            atomic_write(
                paths.recovery_dir / "recovery.json",
                (json.dumps(summary, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
            )
            return True
        except (OSError, TypeError, ValueError) as exc:
            QMessageBox.warning(self, "更新前保存失败", str(exc))
            return False

    def update_blocked_reason(self) -> str | None:
        if self._active or self.session is not None:
            return "STC 擦写/下载进行中，只能检查和下载更新。"
        return None

    def open_update_dialog(self) -> None:
        from .updates.dialog import UpdateDialog

        dialog = UpdateDialog(self.update_service, self, self)
        self._update_dialog = dialog
        dialog.finished.connect(lambda _code: setattr(self, "_update_dialog", None))
        dialog.exec()
        dialog.deleteLater()

    def restore_update_snapshot(self) -> None:
        """Restore only the pre-update configuration; never connect or send."""

        from .updates.config import UpdatePaths
        from .vofa.workspace import load_workspace

        paths = UpdatePaths.current()
        workspace = paths.recovery_dir / "workspace.json"
        if not workspace.is_file():
            QMessageBox.information(self, "恢复工作区", "没有找到更新前保存的工作区。")
            return
        if self._monitor_active or self._demo_active:
            QMessageBox.warning(self, "恢复工作区", "请先断开当前数据源；恢复不会断开或重连设备。")
            return
        try:
            settings = load_workspace(workspace)
            history_path = paths.recovery_dir / "send-history.json"
            entries = json.loads(history_path.read_text(encoding="utf-8")) if history_path.is_file() else []
            if not isinstance(entries, list):
                entries = []
        except (OSError, TypeError, ValueError) as exc:
            QMessageBox.warning(self, "恢复失败", str(exc))
            return
        self.load_monitor_workspace(settings=settings, source_label=f"更新前工作区 {paths.recovery_dir}")
        for entry in reversed(entries):
            try:
                self.send_history.record(**entry)
            except (TypeError, ValueError):
                continue
        self._append_monitor_log("已恢复更新前工作区与发送历史；未连接设备，未恢复周期发送。")

    def _auto_check_update(self) -> None:
        if self._update_dialog is not None or not self.update_service.should_auto_check():
            return
        self._auto_check_pending = True
        self.update_service.check(manual=False)

    @Slot(object)
    def _on_background_check(self, result) -> None:
        if not self._auto_check_pending:
            return
        self._auto_check_pending = False
        if result.status == "available":
            self.open_update_dialog()

    @Slot()
    def load_monitor_workspace(self, _checked=False, *, settings=None, source_label: str | None = None) -> None:
        if self._monitor_active or self._demo_active:
            return
        if settings is None:
            filename, _ = QFileDialog.getOpenFileName(self, "载入工作区", "", "JSON (*.json)")
            if not filename:
                return
            try:
                settings = load_workspace(filename)
            except (OSError, TypeError, ValueError) as exc:
                QMessageBox.warning(self, "载入失败", str(exc))
                return
        label = source_label or "工作区"
        try:
            new_engines = [EngineManifest.from_document(item) for item in settings.engine_manifests]
            existing_engines = {item.identifier: item for item in PLUGIN_REGISTRY.engines}
            for engine in new_engines:
                if engine.identifier in existing_engines and existing_engines[engine.identifier].document() != engine.document():
                    raise ValueError(f'扩展 {engine.identifier} 与当前已注册配置冲突；请先在扩展管理中处理，未覆盖。')
            if len(set(existing_engines) | {item.identifier for item in new_engines}) > 32:
                raise ValueError('载入后扩展引擎超过32个')
            existing_themes = {item['id']: item for item in custom_theme_documents()}
            for item in settings.themes:
                if item['id'] in existing_themes and existing_themes[item['id']] != item:
                    raise ValueError(f"主题 {item['id']} 与当前主题配置冲突；请先更换标识，未覆盖。")
            if len(set(existing_themes) | {item['id'] for item in settings.themes}) > 16:
                raise ValueError('载入后自定义主题超过16个')
        except (OSError, TypeError, ValueError) as exc:
            QMessageBox.warning(self, "载入失败", str(exc))
            return
        if self._workspace_dirty or self._commands or self.control_workspace.count:
            command_count = sum(len(group.commands) for group in self._command_groups)
            pages = self.dashboard.pages()
            if QMessageBox.question(self, '载入工作区',
                f'将替换当前 {len(pages)} 个页面、{sum(len(page["cards"]) for page in pages)} 张卡片、{len(self._command_groups)} 个命令组和 {command_count} 条命令。已保存文件可重新载入，未保存配置无法恢复。继续吗？',
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                return
        self._loading_workspace = True
        for engine in new_engines:
            if engine.identifier not in existing_engines:
                PLUGIN_REGISTRY.register(engine)
        for theme_document in settings.themes:
            register_custom_theme(theme_document)
        self._refresh_theme_choices(settings.theme)
        self._refresh_decoder_plugins(settings.decoder)
        self._command_groups = [CommandGroup.from_document(item) for item in settings.command_groups]
        selected_group = self._command_groups[0]
        self._command_group_id = selected_group.id
        self._commands = [item.entry for item in selected_group.commands]
        self._command_ids = [item.id for item in selected_group.commands]
        self._refresh_command_groups()
        self._channel_display = tuple(ChannelDisplay(**item) for item in settings.channel_config) if settings.channel_config else tuple(ChannelDisplay() for _ in range(16))
        self._refresh_command_list()
        self.control_workspace.load_config(list(settings.controls))
        self.control_workspace.set_layout_mode(settings.controls_layout)
        for card in self.control_workspace.controls:
            self._attach_control_menu(card)
        self.control_workspace.set_theme(self._theme)
        self.control_workspace.set_samples(self._last_values, self._effective_channel_names())
        self.dashboard.load_pages(settings.pages, settings.active_page_id)
        for card in self.control_workspace.controls:
            self._attach_control_menu(card)
        self._refresh_control_images()
        self.monitor_baud_edit.setText(str(settings.baudrate) if settings.baudrate is not None else "")
        self.source_combo.setCurrentIndex(self.source_combo.findData(settings.source))
        self.swd_panel.restore(settings.swd)
        self.decoder_combo.setCurrentIndex(self.decoder_combo.findData(settings.decoder))
        self.channel_names_edit.setText(",".join(settings.channel_names))
        self.periodic_send_check.setChecked(False)
        self._periodic_timer.stop()
        for check, visible in zip(self.channel_visibility_checks, settings.visible_channels):
            check.setChecked(visible)
        self.sample_interval_spin.setValue(settings.sample_interval_ms)
        self.wave_buffer_spin.setValue(settings.buffer_samples)
        self.send_mode_combo.setCurrentIndex(self.send_mode_combo.findData(settings.tx_mode))
        self.send_ending_combo.setCurrentIndex(self.send_ending_combo.findData(settings.tx_ending))
        self.send_text_edit.setText(settings.tx_text)
        self.send_interval_spin.setValue(settings.tx_interval_ms)
        self.network_host_edit.setText(settings.network_host)
        self.network_port_edit.setText(str(settings.network_port) if settings.network_port is not None else '')
        self.udp_peer_host_edit.setText(settings.udp_peer_host)
        self.udp_peer_port_edit.setText(str(settings.udp_peer_port) if settings.udp_peer_port is not None else '')
        self.playback_path_edit.setText(settings.replay_path)
        self.playback_speed_spin.setValue(settings.replay_speed)
        self.playback_loop_check.setChecked(settings.replay_loop)
        self.serial_bytesize_combo.setCurrentIndex(self.serial_bytesize_combo.findData(settings.serial_bytesize))
        self.serial_parity_combo.setCurrentIndex(self.serial_parity_combo.findData(settings.serial_parity))
        self.serial_stopbits_combo.setCurrentIndex(self.serial_stopbits_combo.findData(settings.serial_stopbits))
        self.serial_dtr_check.setChecked(False)
        self.serial_rts_check.setChecked(False)
        self.image_directory_edit.setText(settings.image_directory)
        self.image_fps_spin.setValue(settings.image_fps)
        self.image_loop_check.setChecked(settings.image_loop)
        self.image_rx_check.setChecked(settings.image_rx)
        self.demo_channels_spin.setValue(settings.demo_channels)
        self.demo_interval_spin.setValue(settings.demo_interval_ms)
        self.demo_amplitude_spin.setValue(settings.demo_amplitude)
        self.demo_offset_spin.setValue(settings.demo_offset)
        self.sequence_interval_spin.setValue(settings.sequence_interval_ms)
        self.sequence_repeat_spin.setValue(settings.sequence_repeat_count)
        self._load_compat_demo_config(settings.compat_demo)
        self.analysis_view.load_config(settings.analysis_config)
        self.set_receive_encoding(settings.rx_encoding)
        self.set_transmit_encoding(settings.tx_encoding)
        self.serial_flow_combo.setCurrentIndex(self.serial_flow_combo.findData(settings.serial_flow_control))
        self._connection_presets = list(settings.connection_presets)
        self._serial_tools_state = dict(settings.serial_tools)
        if self.serial_tools_dialog is not None:
            self.serial_tools_dialog.apply_state(settings.serial_tools)
            from .vofa.serial_preferences import ConfigHistory
            self.serial_tools_dialog.history = ConfigHistory(self.serial_tools_dialog._snapshot())
        self.full_erase_check.setChecked(False)
        self.show_rx_action.setChecked(settings.show_rx)
        self.show_tx_action.setChecked(settings.show_tx)
        self.show_timestamps_action.setChecked(settings.show_timestamps)
        self.pause_terminal_action.setChecked(False)
        self._terminal_font_size = settings.terminal_font_size
        self._terminal_font_customized = settings.terminal_font_size != round(11 * self._text_scale)
        self.monitor_log_view.setStyleSheet(f'font-size: {self._terminal_font_size}px;')
        self._file_send_chunk_size, self._file_send_interval_ms = settings.file_chunk_size, settings.file_interval_ms
        self.matrix_interval_spin.setValue(settings.matrix_interval_ms)
        self.image_channel_combo.setCurrentIndex(self.image_channel_combo.findData(settings.image_channel))
        self.image_view.set_effect(settings.image_effect)
        spec = effect_parameter(settings.image_effect)
        if spec.maximum > spec.minimum:
            self.image_view.set_effect_parameter(
                min(max(settings.image_effect_param, spec.minimum), spec.maximum)
            )
        for _ in range(((settings.image_rotation - self.image_view.rotation) % 360) // 90):
            self.image_view.rotate_clockwise()
        self.theme_combo.setCurrentIndex(self.theme_combo.findData(settings.theme))
        self.precision_spin.setValue(settings.precision)
        self.rx_display_combo.setCurrentIndex(self.rx_display_combo.findData(settings.rx_display))
        self.monitor_splitter.setSizes(list(settings.monitor_splitter_sizes))
        self.monitor_plot_splitter.setSizes(list(settings.plot_splitter_sizes))
        self._rebuild_display_samples()
        self.waveform.set_paused(False)
        self.waveform.reset_view()
        self.waveform.set_xy_channels(*settings.plot_xy_channels)
        self.waveform.set_mode(settings.plot_mode)
        self.waveform.set_grid_visible(settings.plot_grid)
        self.waveform.set_line_width(settings.plot_line_width)
        self.waveform.set_axis_ranges(**dict(zip(('x_min', 'x_max', 'y_min', 'y_max'), settings.plot_axis_ranges)))
        self.waveform.reset_channel_colors()
        for channel, color in enumerate(settings.channel_colors):
            if color is not None:
                self.waveform.set_channel_color(channel, color)
        applied_kinds = set()
        for page in settings.pages:
            for card in page['cards']:
                if card['kind'] in applied_kinds or card['id'] != 'legacy-card-' + card['kind']:
                    continue
                if card['kind'] == 'waveform':
                    self.dashboard.apply_wave_config(self.waveform, card['config'])
                elif card['kind'] == 'analysis':
                    self.analysis_view.load_config({k:v for k,v in card['config'].items() if k != 'source_waveform_id'})
                applied_kinds.add(card['kind'])
        self.dashboard.set_sample_interval_ms(settings.sample_interval_ms)
        self._sync_active_plot()
        self.control_workspace.set_samples(self._last_values, self._effective_channel_names())
        self._refresh_channel_values()
        index = self.monitor_port_combo.findData(settings.port)
        self.monitor_port_combo.setCurrentIndex(index if index >= 0 else 0)
        if settings.port and index < 0:
            self.monitor_status_label.setText(
                f"工作区引用的 {settings.port} 当前未枚举；请刷新并确认设备。未自动连接。"
            )
        else:
            self.monitor_status_label.setText("工作区已载入；未自动连接，也未发送数据。")
        self._append_monitor_log(f"已载入工作区：{label}。")
        self._loading_workspace = False
        self._workspace_dirty = False
        self._refresh_dashboard()
        self._update_monitor_controls()

    @Slot()
    def import_monitor_native_context(self) -> None:
        if self._monitor_active or self._demo_active:
            return
        filename, _ = QFileDialog.getOpenFileName(
            self, "导入 VOFA+ 原生上下文", "", "VOFA+ 配置 (vofa+.config.json);;JSON (*.json)")
        if not filename:
            return
        try:
            report = inspect_native_context(filename)
        except (OSError, TypeError, ValueError) as exc:
            QMessageBox.warning(self, "导入失败", str(exc))
            return
        preview = QDialog(self)
        preview.setWindowTitle('VOFA+ 原生工程 · 映射预览')
        preview.resize(700, 480)
        layout = QVBoxLayout(preview)
        details = QPlainTextEdit()
        details.setReadOnly(True)
        lines = ['检查不会替换当前工程，不连接设备，不发送，也不读取外部模型。']
        for label, issues in (('已支持', report.supported), ('明确忽略', report.ignored), ('未支持', report.unsupported), ('错误', report.errors)):
            lines.append(f'\n{label}：{sum(item.count for item in issues)} 项')
            lines.extend(f'{item.path} · {item.message}（{item.count}项）' for item in issues)
        details.setPlainText('\n'.join(lines))
        layout.addWidget(details, 1)
        partial = QCheckBox('仅导入已支持的设置；未支持的页面/命令不会导入')
        partial.setVisible(bool(report.unsupported))
        layout.addWidget(partial)
        buttons = QHBoxLayout()
        cancel = QPushButton('取消')
        cancel.clicked.connect(preview.reject)
        apply = QPushButton('应用映射')
        apply.setEnabled(report.complete)
        partial.toggled.connect(lambda checked: apply.setEnabled(report.complete or checked and report.can_apply_partial))
        apply.clicked.connect(preview.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(apply)
        layout.addLayout(buttons)
        if preview.exec() != QDialog.Accepted or report.candidate is None:
            return
        self.load_monitor_workspace(
            settings=report.candidate, source_label=f"VOFA+ 原生上下文 {Path(filename).parent}")

    def _append_log(self, message: str, at: datetime | None = None) -> None:
        when = at or datetime.now()
        entry = f"[{when:%H:%M:%S.%f}"[:-3] + f"] {message}"
        self.log_view.appendPlainText(entry)
        try:
            self._log_dir.mkdir(parents=True, exist_ok=True)
            if self._log_file is None:
                self._log_file = self._log_dir / f"session-{datetime.now():%Y%m%d-%H%M%S}.log"
            with self._log_file.open("a", encoding="utf-8") as stream:
                stream.write(entry + "\n")
        except OSError:
            # 日志目录不可写时仍能在 GUI 中查看；不影响模拟会话。
            pass

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.serial_tools_dialog is not None and not self.serial_tools_dialog.close():
            event.ignore()
            return
        self.stop_send_job()
        self._stop_stream_recording()
        self._periodic_timer.stop()
        self._stop_sample_recording()
        if self.session is not None:
            self.session.cancel()
        self._demo_timer.stop()
        if self._monitor_worker is not None:
            self._monitor_worker.stop()
        if self._monitor_thread is not None and self._monitor_thread.isRunning():
            self._monitor_thread.quit()
            if not self._monitor_thread.wait(5000):
                event.ignore()
                self.monitor_status_label.setText("正在停止串口接收，请稍候再关闭窗口。")
                return
        if self._thread is not None and self._thread.isRunning():
            self._thread.quit()
            if not self._thread.wait(5000):
                event.ignore()
                self.status_label.setText("正在取消模拟任务，请稍候再关闭窗口。")
                return
        for _kind, recorder in self._closing_recorders:
            if not recorder.wait_closed(.2):
                event.ignore()
                self.monitor_status_label.setText('记录线程正在收尾，未保存数量尚未确认，请稍后关闭。')
                return
        self._poll_recorders()
        if self._monitor_text_recorder is not None:
            if not self._monitor_text_recorder.close(.2):
                event.ignore()
                self.monitor_status_label.setText('日志线程正在收尾，请稍后关闭。')
                return
        self._record_poll_timer.stop()
        self._dashboard_timer.stop()
        if not self.measurement_panel.stop():
            event.ignore()
            self.monitor_status_label.setText('测量线程正在收尾，请稍后关闭。')
            return
        if not self.dashboard.stop_projection():
            event.ignore()
            self.monitor_status_label.setText('显示线程正在收尾，请稍后关闭。')
            return
        pending = getattr(self, 'update_service', None)
        if pending is not None:
            pending.cancel_download()
            pending.wait_idle(2000)
        if pending is not None and pending.pending is not None:
            if self.update_blocked_reason() is None:
                from .updates.manifest import UpdateError

                try:
                    pending.install_on_exit(pending.pending)
                except UpdateError as exc:
                    QMessageBox.warning(self, "更新未开始", str(exc))
        event.accept()


def _load_bundled_fonts() -> None:
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2])) / "assets" / "fonts"
    for name in ("PingFang SC Regular.ttf", "PingFang SC Medium.ttf", "PingFang SC Bold.ttf"):
        path = root / name
        if path.is_file():
            QFontDatabase.addApplicationFont(str(path))


def _health_check(argv: list[str]) -> int:
    """Startup smoke check launched by the update assistant; never touches devices."""

    try:
        target = Path(argv[argv.index("--health-check") + 1])
        target.write_text(
            json.dumps({"ok": True, "version": __version__}), encoding="utf-8",
        )
    except (IndexError, OSError, ValueError):
        return 1
    return 0


def main() -> int:
    if "--health-check" in sys.argv[1:]:
        return _health_check(sys.argv[1:])
    app = QApplication.instance() or QApplication([])
    app.setApplicationName("Astrspire")
    try:
        from .updates.process import InstanceMutex

        instance = InstanceMutex()
    except OSError:
        instance = None
    # 苹方随包加载，供中文使用；拉丁字形优先系统 UI 字体（Inter/Segoe UI）。
    _load_bundled_fonts()
    window = MainWindow(text_scale=load_text_scale())
    window.show()
    try:
        return app.exec()
    finally:
        if instance is not None:
            instance.close()
