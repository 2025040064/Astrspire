"""Native Qt packet/protocol workbench; no transport ownership or implicit TX."""
from __future__ import annotations

import json
import threading
from dataclasses import asdict
from uuid import uuid4

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QThread, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication, QAbstractItemView, QCheckBox, QComboBox, QDialog, QFileDialog,
    QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QListWidget,
    QMessageBox, QPlainTextEdit, QPushButton, QSplitter, QTabWidget, QTableView,
    QVBoxLayout, QWidget,
)

from .json_data import load_json_document
from .packets import search_packets, search_recording
from .plugins import EngineManifest, PLUGIN_REGISTRY
from .protocol import FrameDefinition, FrameTemplate, preview_protocol
from .serial_preferences import ConfigHistory, SerialPreset
from .commands import CommandEntry
from .transmit import encode_send_payload
from .profile_panel import ProfilePanel
from .protocol_editor import ProtocolFieldEditor

DEFAULT_DEFINITION = {
    "header_hex": "AA 55", "tail_hex": "0D 0A",
    "fields": [{"id": "message_id", "type": "id", "width": 1, "value": 1},
               {"id": "value", "type": "float32", "endian": "little"}],
    "checksum": {"algorithm": "crc16-modbus", "start": 0, "end": "checksum", "byte_order": "little"},
}


def _button(text, layout, callback):
    widget = QPushButton(text)
    widget.clicked.connect(callback)
    layout.addWidget(widget)
    return widget


class PacketTableModel(QAbstractTableModel):
    headers = ("方向", "软件接收/完成时间", "流偏移", "HEX（前32字节）", "ASCII", "字节数")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.records = ()

    def set_records(self, records):
        self.beginResetModel()
        self.records = tuple(records)
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.records)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.headers)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return self.headers[section]

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        record = self.records[index.row()]
        if role == Qt.ToolTipRole:
            return f"原始I/O块 #{record.sequence}；不是分帧边界。完整字节见下方，复制不加入显示折行。"
        if role != Qt.DisplayRole:
            return None
        return (record.direction, record.received_at.astimezone().strftime("%H:%M:%S.%f")[:-3],
                f"{record.offset:08X}", record.raw[:32].hex(" ").upper() + (" …" if len(record.raw) > 32 else ""),
                "".join(chr(byte) if 32 <= byte < 127 else "·" for byte in record.raw[:32]),
                str(len(record.raw)))[index.column()]


class _PacketSearch(QThread):
    result_ready = Signal(object, str)

    def __init__(self, records, query, mode, direction, encoding, parent, *, source_path=None):
        super().__init__(parent)
        self.records, self.query, self.mode = records, query, mode
        self.direction, self.encoding = direction, encoding
        self.cancelled = threading.Event()
        self.source_path = source_path
        self.recording_result = None

    def run(self):
        try:
            if self.source_path is None:
                results = search_packets(self.records, self.query, mode=self.mode,
                                         direction=self.direction, encoding=self.encoding,
                                         cancelled=self.cancelled)
            else:
                self.recording_result = search_recording(self.source_path, self.query, mode=self.mode,
                                                        direction=self.direction, encoding=self.encoding,
                                                        cancelled=self.cancelled)
                self.records = self.recording_result.records
                results = self.recording_result.matches
            if not self.cancelled.is_set():
                self.result_ready.emit(results, "")
        except Exception as exc:
            if not self.cancelled.is_set():
                self.result_ready.emit([], str(exc))


class SerialToolsDialog(QDialog):
    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.setWindowTitle("Astrspire · 报文与协议工作台")
        self.resize(1120, 780)
        self.setMinimumSize(760, 540)
        self._search = None
        self._search_key = None
        self._last_sequence = -1
        self._refreshing = False
        self._payload = None
        self._restoring = False
        self._preview_token = 0
        self._preview_active = False
        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        layout.addWidget(tabs)
        self._build_packets(tabs)
        self._build_protocol(tabs)
        self._build_presets(tabs)
        self.profile_panel = ProfilePanel(owner, self)
        tabs.addTab(self.profile_panel, "设备档案")
        self.apply_state(owner._serial_tools_state)
        self.history = ConfigHistory(self._snapshot())
        self._edit_timer = QTimer(self)
        self._edit_timer.setSingleShot(True)
        self._edit_timer.setInterval(400)
        self._edit_timer.timeout.connect(self._commit)
        for editor in (self.definition_edit, self.parameters_edit):
            editor.textChanged.connect(self._edited)
        self.definition_edit.textChanged.connect(self._sync_field_editor)
        self.field_editor.definition_changed.connect(self._fields_to_json)
        self._sync_field_editor()
        for edit in (self.channels_edit, self.identifier_edit, self.name_edit):
            edit.textChanged.connect(self._edited)
        self._timer = QTimer(self)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self.refresh_packets)
        self._timer.start()
        self.refresh_packets()

    def _build_packets(self, tabs):
        page = QWidget()
        layout = QVBoxLayout(page)
        row = QHBoxLayout()
        self.query_edit = QLineEdit()
        self.query_edit.setPlaceholderText("跨读取块搜索；不混合RX/TX流")
        self.query_edit.setMaxLength(1024)
        row.addWidget(self.query_edit, 1)
        self.search_mode = QComboBox()
        for title, mode in (("文本", "text"), ("HEX", "hex"), ("字节正则", "regex")):
            self.search_mode.addItem(title, mode)
        row.addWidget(self.search_mode)
        self.direction_combo = QComboBox()
        for value in ("ALL", "RX", "TX"):
            self.direction_combo.addItem("全部方向" if value == "ALL" else value, value)
        row.addWidget(self.direction_combo)
        self.search_button = _button("搜索快照", row, self.start_search)
        self.file_search_button = _button("搜索记录…", row, self.start_file_search)
        self.file_search_button.setToolTip("显式读取旧记录或会话清单。正则按1MiB窗口、每方向64KiB重叠搜索，不保证任意长跨窗表达式。")
        _button("取消搜索", row, self.cancel_search)
        layout.addLayout(row)
        row = QHBoxLayout()
        self.follow_check = QCheckBox("追踪最新")
        self.follow_check.setChecked(True)
        row.addWidget(self.follow_check)
        _button("返回最新", row, self.return_latest)
        _button("复制选中HEX", row, self.copy_packets)
        _button("导出选中原始字节…", row, self.export_packets)
        _button("清空此视图缓存", row, self.clear_packets)
        row.addStretch()
        layout.addLayout(row)
        splitter = QSplitter(Qt.Vertical)
        self.packet_table = QTableView()
        self.packet_model = PacketTableModel(self)
        self.packet_table.setModel(self.packet_model)
        self.packet_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.packet_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.packet_table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.packet_table.setColumnWidth(1, 150)
        self.packet_table.setColumnWidth(3, 420)
        self.packet_table.selectionModel().selectionChanged.connect(self.show_packet)
        self.packet_table.verticalScrollBar().valueChanged.connect(self._scrolled)
        splitter.addWidget(self.packet_table)
        self.packet_detail = QPlainTextEdit()
        self.packet_detail.setReadOnly(True)
        splitter.addWidget(self.packet_detail)
        splitter.setSizes([430, 180])
        layout.addWidget(splitter, 1)
        self.packet_status = QLabel()
        self.packet_status.setWordWrap(True)
        layout.addWidget(self.packet_status)
        tabs.addTab(page, "原始报文")

    def _build_protocol(self, tabs):
        page = QWidget()
        layout = QVBoxLayout(page)
        row = QHBoxLayout()
        self.identifier_edit, self.name_edit = QLineEdit("user.frame"), QLineEdit("自定义帧")
        self.identifier_edit.setMaxLength(64)
        self.name_edit.setMaxLength(80)
        row.addWidget(QLabel("ID"))
        row.addWidget(self.identifier_edit)
        row.addWidget(QLabel("名称"))
        row.addWidget(self.name_edit)
        _button("撤销", row, self.undo)
        _button("重做", row, self.redo)
        _button("导入草稿…", row, self.import_draft)
        _button("导出草稿…", row, self.export_draft)
        layout.addLayout(row)
        splitter = QSplitter(Qt.Horizontal)
        editors = QTabWidget()
        self.definition_edit = QPlainTextEdit()
        self.definition_edit.setPlaceholderText("声明式JSON：帧头、字段、长度、条件ID、位域、校验、帧尾。最多64KiB。")
        self.field_editor = ProtocolFieldEditor()
        editors.addTab(self.field_editor, "字段表单")
        editors.addTab(self.definition_edit, "高级JSON")
        splitter.addWidget(editors)
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.addWidget(QLabel("发送参数（只生成字节，不自动发送）"))
        self.parameters_edit = QPlainTextEdit()
        right_layout.addWidget(self.parameters_edit, 1)
        right_layout.addWidget(QLabel("波形字段（英文逗号分隔；最多16个；留空自动选数值字段）"))
        self.channels_edit = QLineEdit("value")
        right_layout.addWidget(self.channels_edit)
        self.frame_hex_edit = QPlainTextEdit()
        self.frame_hex_edit.setPlaceholderText("待解析HEX；生成的发送字节也显示在这里")
        self.frame_hex_edit.setMaximumHeight(100)
        right_layout.addWidget(self.frame_hex_edit)
        splitter.addWidget(right)
        splitter.setSizes([570, 450])
        layout.addWidget(splitter, 3)
        row = QHBoxLayout()
        _button("离线解析", row, self.preview_offline)
        self.preview_button = _button("预览500ms RX", row, self.preview_live)
        self.apply_engine_button = _button("应用为解析引擎", row, self.apply_engine)
        _button("生成发送字节", row, self.generate)
        self.fill_button = _button("填入发送框", row, self.fill_send)
        self.command_button = _button("加入当前命令组", row, self.add_command)
        self.fill_button.setEnabled(False)
        self.command_button.setEnabled(False)
        layout.addLayout(row)
        self.protocol_result = QPlainTextEdit()
        self.protocol_result.setReadOnly(True)
        layout.addWidget(self.protocol_result, 2)
        tabs.addTab(page, "协议与命令模板")

    def _build_presets(self, tabs):
        page = QWidget()
        layout = QVBoxLayout(page)
        self.preset_search = QLineEdit()
        self.preset_search.setPlaceholderText("搜索预设名称或端口")
        self.preset_search.textChanged.connect(self.refresh_presets)
        layout.addWidget(self.preset_search)
        self.preset_list = QListWidget()
        layout.addWidget(self.preset_list, 1)
        row = QHBoxLayout()
        _button("保存当前连接参数", row, self.capture_preset)
        _button("填入选中预设", row, self.apply_preset)
        _button("删除选中预设", row, self.delete_preset)
        layout.addLayout(row)
        layout.addWidget(QLabel("预设只填参数，不连接、不发送。DTR/RTS和连接确认始终复位关闭；预设随工作区保存。"))
        tabs.addTab(page, "连接预设")

    def _snapshot(self):
        return {"draft": self.state(), "presets": list(self.owner._connection_presets)}

    def state(self):
        return {"definition_text": self.definition_edit.toPlainText(),
                "parameters_text": self.parameters_edit.toPlainText(),
                "channels_text": self.channels_edit.text(),
                "identifier": self.identifier_edit.text(), "name": self.name_edit.text()}

    def apply_state(self, state):
        self._restoring = True
        self.definition_edit.setPlainText(state.get("definition_text", json.dumps(DEFAULT_DEFINITION, ensure_ascii=False, indent=2)))
        self.parameters_edit.setPlainText(state.get("parameters_text", '{"message_id": 1, "value": 1.0}'))
        self.channels_edit.setText(state.get("channels_text", "value"))
        self.identifier_edit.setText(state.get("identifier", "user.frame"))
        self.name_edit.setText(state.get("name", "自定义帧"))
        self._restoring = False
        self._payload = None
        self.fill_button.setEnabled(False)
        self.command_button.setEnabled(False)
        self.refresh_presets()

    def _edited(self):
        if not self._restoring:
            self._preview_token += 1
            self._preview_active = False
            self._payload = None
            self.fill_button.setEnabled(False)
            self.command_button.setEnabled(False)
            self._edit_timer.start()

    def _commit(self):
        try:
            self.history.commit(self._snapshot())
            self.owner._serial_tools_state = self.state()
            self.owner._mark_workspace_dirty()
        except ValueError as exc:
            self.protocol_result.setPlainText(str(exc))

    def _restore(self, snapshot):
        self._edit_timer.stop()
        self.apply_state(snapshot["draft"])
        self.owner._connection_presets = snapshot["presets"]
        self.owner._serial_tools_state = self.state()
        self.owner._mark_workspace_dirty()
        self.refresh_presets()

    def undo(self):
        self._commit()
        self._restore(self.history.undo())

    def redo(self):
        self._restore(self.history.redo())

    def _definition(self):
        return FrameDefinition.from_document(load_json_document(self.definition_edit.toPlainText(), max_bytes=65536))

    def _sync_field_editor(self):
        try:
            self.field_editor.set_document(self._definition().document())
        except (ValueError, TypeError) as exc:
            self.field_editor.error_label.setText(f"JSON草稿未应用：{exc}")

    def _fields_to_json(self, document):
        self.definition_edit.setPlainText(json.dumps(document, ensure_ascii=False, indent=2))

    def _report(self, result):
        if result.get("frames"):
            self.field_editor.show_frame(result["frames"][0])
        def convert(value):
            if isinstance(value, bytes):
                return value.hex(" ").upper()
            if hasattr(value, "__dataclass_fields__"):
                return asdict(value)
            raise TypeError(type(value).__name__)
        text = json.dumps(result, ensure_ascii=False, indent=2, default=convert)
        self.protocol_result.setPlainText(text[:65536] + ("\n…显示截断，原始缓存不变" if len(text) > 65536 else ""))

    def preview_offline(self):
        try:
            data = encode_send_payload(self.frame_hex_edit.toPlainText(), mode="hex")
            self._report(preview_protocol(self._definition(), data))
        except (ValueError, TypeError) as exc:
            self.protocol_result.setPlainText(str(exc))

    def preview_live(self):
        if not self.owner._monitor_connected:
            self.protocol_result.setPlainText("预览需要已经打开的RX源；不会为预览连接设备。")
            return
        try:
            definition = self._definition()
        except ValueError as exc:
            self.protocol_result.setPlainText(str(exc))
            return
        sequence = self.owner.packet_store.sequence
        generation = self.owner.packet_store.generation
        self._preview_token += 1
        token = self._preview_token
        self._preview_active = True
        self.preview_button.setEnabled(False)
        self.protocol_result.setPlainText("被动收集500ms RX，最多4KiB，不影响正式解析。")
        def finish():
            if token != self._preview_token or not self.isVisible():
                return
            self._preview_active = False
            if generation != self.owner.packet_store.generation:
                self.protocol_result.setPlainText("数据源或缓存已改变，旧预览未应用。")
                return
            data, truncated = self.owner.packet_store.rx_window(sequence)
            result = preview_protocol(definition, data)
            result["truncated"] |= truncated
            result["empty"] = not data
            self._report(result)
            self.preview_button.setEnabled(self.owner._monitor_connected)
        QTimer.singleShot(500, self, finish)

    def apply_engine(self):
        if self.owner._monitor_active or self.owner._demo_active:
            self.protocol_result.setPlainText("请先停止当前数据源；应用配置不会自行停止或重连。")
            return
        try:
            definition = self._definition()
            fields = definition.document()["fields"]
            channels = [value.strip() for value in self.channels_edit.text().split(",") if value.strip()]
            if not channels:
                channels = [field["id"] for field in fields if field["type"] in (
                    "int8", "uint8", "int16", "uint16", "int32", "uint32", "float32", "float64")]
            manifest = EngineManifest.from_document({
                "schema_version": 2, "id": self.identifier_edit.text(), "name": self.name_edit.text(),
                "mode": "framed", "options": {"definition": definition.document(), "channels": channels}})
            existing = next((item for item in PLUGIN_REGISTRY.engines if item.identifier == manifest.identifier), None)
            if existing is not None and existing.document() != manifest.document():
                raise ValueError("此引擎ID已存在且配置不同；请改用新ID，或在数据引擎扩展中移除旧配置。")
            if existing is None:
                PLUGIN_REGISTRY.register(manifest)
            self.owner._refresh_decoder_plugins(manifest.decoder_name)
            self._commit()
            self.protocol_result.setPlainText("已应用解析引擎；尚未连接、发送或采样。")
        except (ValueError, TypeError) as exc:
            self.protocol_result.setPlainText(str(exc))

    def generate(self):
        try:
            parameters = load_json_document(self.parameters_edit.toPlainText(), max_bytes=65536)
            self._payload = FrameTemplate(self._definition()).encode(parameters)
            self.frame_hex_edit.setPlainText(self._payload.hex(" ").upper())
            self.protocol_result.setPlainText(f"已生成 {len(self._payload)} 字节；未发送。所有校验采用明确字节范围。")
            self.fill_button.setEnabled(True)
            self.command_button.setEnabled(True)
            self._commit()
        except (ValueError, TypeError) as exc:
            self._payload = None
            self.fill_button.setEnabled(False)
            self.command_button.setEnabled(False)
            self.protocol_result.setPlainText(str(exc))

    def fill_send(self):
        if self._payload is None:
            return
        owner = self.owner
        if owner._send_job.active or owner.periodic_send_check.isChecked():
            self.protocol_result.setPlainText("请先停止发送任务；填入动作不应改变正在发送的内容。")
            return
        owner.send_mode_combo.setCurrentIndex(owner.send_mode_combo.findData("hex"))
        owner.send_ending_combo.setCurrentIndex(owner.send_ending_combo.findData("none"))
        owner.send_text_edit.setText(self._payload.hex(" ").upper())
        owner._mark_workspace_dirty()
        self.protocol_result.setPlainText("已填入发送框，未发送；请回主窗口手动点击发送。")

    def add_command(self):
        if self._payload is None:
            return
        if len(self.owner._commands) >= 64:
            self.protocol_result.setPlainText("当前命令组已满（64条）。")
            return
        entry = CommandEntry(self.name_edit.text() or "帧模板", self._payload.hex(" ").upper(), "hex", "none")
        self.owner._commands.append(entry)
        self.owner._command_ids.append("command-" + uuid4().hex)
        self.owner._sync_command_group()
        self.owner._refresh_command_list()
        self.owner._mark_workspace_dirty()
        self.protocol_result.setPlainText("已加入当前命令组（字节快照），未发送；修改模板不改变已加入的命令。")

    def import_draft(self):
        path, _ = QFileDialog.getOpenFileName(self, "导入协议草稿", "", "JSON (*.json)")
        if not path:
            return
        try:
            from pathlib import Path
            with Path(path).open("rb") as stream:
                state = load_json_document(stream.read(131073), max_bytes=131072)
            if (not isinstance(state, dict) or set(state) != {
                    "definition_text", "parameters_text", "channels_text", "identifier", "name"}
                    or any(not isinstance(value, str) or len(value.encode("utf-8")) > 65536 for value in state.values())):
                raise ValueError("草稿字段无效")
            self._commit()
            self.apply_state(state)
            self._commit()
        except (OSError, ValueError, TypeError) as exc:
            self.protocol_result.setPlainText(str(exc))

    def export_draft(self):
        path, _ = QFileDialog.getSaveFileName(self, "导出协议草稿（不覆盖）", "serial-protocol.json", "JSON (*.json)")
        if not path:
            return
        try:
            from pathlib import Path
            with Path(path).open("x", encoding="utf-8") as stream:
                stream.write(json.dumps(self.state(), ensure_ascii=False, indent=2) + "\n")
        except (OSError, ValueError) as exc:
            self.protocol_result.setPlainText(str(exc))

    def refresh_packets(self):
        if not self.isVisible():
            return
        self.preview_button.setEnabled(self.owner._monitor_connected and not self._preview_active)
        self.apply_engine_button.setEnabled(not self.owner._monitor_active and not self.owner._demo_active)
        store = self.owner.packet_store
        if self.follow_check.isChecked() and (store.sequence != self._last_sequence or store.generation != getattr(self, "_last_generation", -1)):
            records = store.snapshot()
            direction = self.direction_combo.currentData()
            self._refreshing = True
            self.packet_model.set_records(record for record in records if direction == "ALL" or record.direction == direction)
            self.packet_table.scrollToBottom()
            self._refreshing = False
            self._last_sequence, self._last_generation = store.sequence, store.generation
        if self.follow_check.isChecked():
            self.packet_status.setText(f"实时 · 缓存{len(store.snapshot())}块 · RX {store.total_received} B · TX {store.total_transmitted} B · 历史淘汰{store.evicted_records}块/{store.evicted_bytes} B；不影响文件记录")

    def _scrolled(self, value):
        if not self._refreshing and value < self.packet_table.verticalScrollBar().maximum():
            self.follow_check.setChecked(False)
            self.packet_status.setText("非实时快照；采集、记录和发送继续。点击返回最新恢复追底。")

    def return_latest(self):
        self.cancel_search()
        self.follow_check.setChecked(True)
        self._last_sequence = -1
        self.refresh_packets()

    def show_packet(self):
        selected = self.packet_table.selectionModel().selectedRows()
        if not selected:
            return
        record = self.packet_model.records[selected[0].row()]
        rows = []
        visible_bytes = min(len(record.raw), 65536)
        for offset in range(0, visible_bytes, 16):
            data = record.raw[offset:offset + 16]
            rows.append(f"{record.offset + offset:08X}  {data.hex(' ').upper():47}  "
                        + "".join(chr(byte) if 32 <= byte < 127 else "·" for byte in data))
        if visible_bytes < len(record.raw):
            rows.append(f"仅显示前65536字节，共{len(record.raw)}字节；复制和导出保留完整内容。")
        self.packet_detail.setPlainText("\n".join(rows))

    def _selected_bytes(self):
        selected = sorted(index.row() for index in self.packet_table.selectionModel().selectedRows())
        records = [self.packet_model.records[index] for index in selected]
        if len({record.direction for record in records}) > 1:
            raise ValueError("请选择同一方向的块，避免混合RX/TX")
        return b"".join(record.raw for record in records)

    def copy_packets(self):
        try:
            QApplication.clipboard().setText(self._selected_bytes().hex(" ").upper())
        except ValueError as exc:
            self.packet_status.setText(str(exc))

    def export_packets(self):
        try:
            raw = self._selected_bytes()
            if not raw:
                raise ValueError("请先选择原始块")
            path, _ = QFileDialog.getSaveFileName(self, "导出选中字节（不覆盖）", "selected.bin", "BIN (*.bin)")
            if path:
                from pathlib import Path
                with Path(path).open("xb") as stream:
                    stream.write(raw)
        except (OSError, ValueError) as exc:
            self.packet_status.setText(str(exc))

    def clear_packets(self):
        self.cancel_search()
        self.owner.packet_store.clear()
        self.return_latest()

    def _query_key(self):
        return (self.query_edit.text(), self.search_mode.currentData(), self.direction_combo.currentData(),
                self.owner._rx_encoding, self.owner.packet_store.generation)

    def start_search(self):
        if self._search is not None:
            return
        self.follow_check.setChecked(False)
        self._search_key = self._query_key()
        records = self.owner.packet_store.snapshot()
        self._search = _PacketSearch(records, *self._search_key[:4], self)
        self._search.result_ready.connect(self._show_search)
        self._search.finished.connect(self._search_finished)
        self.search_button.setEnabled(False)
        self.packet_status.setText("正在搜索有界快照；采集继续，正则在独立进程中限时执行。")
        self._search.start()

    def start_file_search(self):
        if self._search is not None:
            return
        path, _ = QFileDialog.getOpenFileName(self, "搜索原始记录（不回放、不发送）", "",
                                              "QX记录 (*.jsonl *.session.json)")
        if not path:
            return
        self.follow_check.setChecked(False)
        self._search_key = self._query_key()
        self._search = _PacketSearch((), *self._search_key[:4], self, source_path=path)
        self._search.result_ready.connect(self._show_search)
        self._search.finished.connect(self._search_finished)
        self.search_button.setEnabled(False)
        self.file_search_button.setEnabled(False)
        self.packet_status.setText("正在流式搜索文件；最多1000个匹配或16MiB结果，可取消，不改变数据源。")
        self._search.start()

    def _show_search(self, matches, error):
        if self._search_key is None or self._search is None or (
                self._search_key[:4] != self._query_key()[:4]
                or self._search.source_path is None and self._search_key != self._query_key()):
            self.packet_status.setText("查询或缓存已改变，过期结果未应用。")
            return
        if error:
            self.packet_status.setText(error)
            return
        selected = {sequence for match in matches for sequence in match.sequences}
        self.packet_model.set_records(record for record in self._search.records if record.sequence in selected)
        self.packet_status.setText(f"搜索快照 · {len(matches)}个匹配（最多1000），{len(selected)}个原始块；点击返回最新退出过滤。")
        if self._search.recording_result is not None:
            result = self._search.recording_result
            self.packet_status.setText(f"文件搜索 · 扫描{result.scanned_records}块 · {len(matches)}个匹配 · "
                                      + ("已扫描至文件末尾" if result.complete else "达到结果上限，未扫描全部")
                                      + ("；正则为有界窗口语义" if self.search_mode.currentData() == "regex" else ""))

    def _search_finished(self):
        worker, self._search = self._search, None
        self.search_button.setEnabled(True)
        self.file_search_button.setEnabled(True)
        if worker is not None:
            worker.deleteLater()

    def cancel_search(self):
        self._search_key = None
        if self._search is not None:
            self._search.cancelled.set()

    def refresh_presets(self):
        query = self.preset_search.text().casefold()
        self.preset_list.clear()
        for preset in self.owner._connection_presets:
            text = f"{preset['name']} · {preset['port'] or '未指定端口'} · {preset['baudrate']}"
            if query in text.casefold():
                from PySide6.QtWidgets import QListWidgetItem
                item = QListWidgetItem(text)
                item.setData(Qt.UserRole, preset["id"])
                self.preset_list.addItem(item)

    def capture_preset(self):
        if len(self.owner._connection_presets) >= 32:
            self.protocol_result.setPlainText("连接预设最多32个")
            return
        name, accepted = QInputDialog.getText(self, "保存连接预设", "名称")
        if not accepted:
            return
        try:
            owner = self.owner
            preset = SerialPreset("preset-" + uuid4().hex, name, owner.monitor_port_combo.currentData() or "",
                owner._monitor_baudrate(), owner.serial_bytesize_combo.currentData(),
                owner.serial_parity_combo.currentData(), owner.serial_stopbits_combo.currentData(),
                owner._rx_encoding, owner._tx_encoding, owner.send_ending_combo.currentData(),
                owner.serial_flow_combo.currentData())
            self._commit()
            owner._connection_presets.append(preset.document())
            self._commit()
            self.refresh_presets()
        except ValueError as exc:
            QMessageBox.warning(self, "无法保存预设", str(exc))

    def apply_preset(self):
        selected = self.preset_list.currentItem()
        if selected is None:
            return
        if self.owner._monitor_active or self.owner._demo_active:
            QMessageBox.information(self, "连接预设", "请先停止当前数据源；不会自动断开。")
            return
        preset = SerialPreset(**next(item for item in self.owner._connection_presets if item["id"] == selected.data(Qt.UserRole)))
        self.owner.apply_serial_preset(preset)

    def delete_preset(self):
        item = self.preset_list.currentItem()
        if item is None:
            return
        if QMessageBox.question(self, "删除预设", "删除1个预设；可在本窗口撤销，或从已保存工程恢复。",
                                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        self._commit()
        self.owner._connection_presets = [preset for preset in self.owner._connection_presets if preset["id"] != item.data(Qt.UserRole)]
        self._commit()
        self.refresh_presets()

    def showEvent(self, event):
        super().showEvent(event)
        if hasattr(self, "_timer"):
            self._timer.start()
            self.refresh_packets()

    def closeEvent(self, event):
        self._preview_token += 1
        self._preview_active = False
        self.cancel_search()
        if self._search is not None and self._search.isRunning() and not self._search.wait(1500):
            event.ignore()
            QTimer.singleShot(100, self.close)
            return
        self._edit_timer.stop()
        self._commit()
        self._timer.stop()
        super().closeEvent(event)
