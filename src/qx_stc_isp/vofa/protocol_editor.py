"""Native field table over FrameDefinition; invalid drafts never go live."""
from __future__ import annotations

import json

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QPlainTextEdit, QPushButton, QSplitter, QTableWidget,
    QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget)

from .protocol import CHECKSUMS, FrameDefinition


class ProtocolFieldEditor(QWidget):
    definition_changed = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._restoring = False
        self._document = None
        self._frame = None
        self._selecting = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.header_edit, self.tail_edit = QLineEdit(), QLineEdit()
        row.addWidget(QLabel("帧头HEX"))
        row.addWidget(self.header_edit)
        row.addWidget(QLabel("帧尾HEX"))
        row.addWidget(self.tail_edit)
        layout.addLayout(row)
        self.fields = QTableWidget(0, 5)
        self.fields.setHorizontalHeaderLabels(["字段ID", "类型", "字节序", "宽度/数量", "详细选项JSON"])
        self.fields.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.fields.horizontalHeader().setStretchLastSection(True)
        self.fields.setColumnWidth(0, 110)
        self.fields.setColumnWidth(1, 80)
        self.fields.setColumnWidth(2, 70)
        self.fields.setColumnWidth(3, 85)
        self.fields.setSelectionBehavior(QAbstractItemView.SelectRows)
        layout.addWidget(self.fields, 3)
        row = QHBoxLayout()
        for title, callback in (("新增字段", self.add_field), ("删除字段", self.remove_field),
                                ("上移", lambda: self.move_field(-1)), ("下移", lambda: self.move_field(1))):
            button = QPushButton(title)
            button.clicked.connect(callback)
            row.addWidget(button)
        layout.addLayout(row)
        row = QHBoxLayout()
        self.checksum_combo = QComboBox()
        self.checksum_combo.addItems(CHECKSUMS)
        self.checksum_start, self.checksum_end = QLineEdit("0"), QLineEdit("checksum")
        self.checksum_order = QComboBox()
        self.checksum_order.addItems(["default", "little", "big"])
        for title, widget in (("校验", self.checksum_combo), ("起点", self.checksum_start),
                              ("终点(不含)", self.checksum_end), ("字节序", self.checksum_order)):
            row.addWidget(QLabel(title))
            row.addWidget(widget)
        layout.addLayout(row)
        self.error_label = QLabel("数组type=array、元素类型item_type；位域bits、枚举enum及分支when在详细选项中编辑。")
        self.error_label.setWordWrap(True)
        layout.addWidget(self.error_label)
        splitter = QSplitter(Qt.Vertical)
        self.decoded_fields = QTableWidget(0, 4)
        self.decoded_fields.setHorizontalHeaderLabels(["解析字段", "类型", "精确值/枚举", "字节范围[起,止)"])
        self.decoded_fields.horizontalHeader().setStretchLastSection(True)
        self.decoded_fields.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.decoded_fields.setSelectionBehavior(QAbstractItemView.SelectRows)
        splitter.addWidget(self.decoded_fields)
        self.raw_view = QPlainTextEdit()
        self.raw_view.setReadOnly(True)
        self.raw_view.setMaximumHeight(100)
        splitter.addWidget(self.raw_view)
        layout.addWidget(splitter, 2)
        self.fields.itemChanged.connect(self._changed)
        for edit in (self.header_edit, self.tail_edit, self.checksum_start, self.checksum_end):
            edit.textEdited.connect(self._changed)
        self.checksum_combo.currentTextChanged.connect(self._changed)
        self.checksum_order.currentTextChanged.connect(self._changed)
        self.decoded_fields.itemSelectionChanged.connect(self._select_field)
        self.raw_view.cursorPositionChanged.connect(self._select_byte)

    def set_document(self, document):
        canonical = FrameDefinition.from_document(document).document()
        self._restoring = True
        self._document = canonical
        self.header_edit.setText(canonical.get("header_hex", ""))
        self.tail_edit.setText(canonical.get("tail_hex", ""))
        self.fields.setRowCount(len(canonical["fields"]))
        for row, field in enumerate(canonical["fields"]):
            options = {key: value for key, value in field.items() if key not in ("id", "type", "endian", "width", "count")}
            width = field.get("count" if field["type"] == "array" else "width", "")
            values = (field["id"], field["type"], field.get("endian", "little"), str(width),
                      json.dumps(options, ensure_ascii=False, separators=(",", ":")))
            for column, value in enumerate(values):
                self.fields.setItem(row, column, QTableWidgetItem(value))
        check = canonical.get("checksum", {})
        self.checksum_combo.setCurrentText(check.get("algorithm", "none"))
        self.checksum_start.setText(str(check.get("start", 0)))
        self.checksum_end.setText(str(check.get("end", "checksum")))
        self.checksum_order.setCurrentText(check.get("byte_order", "default"))
        self._restoring = False
        self.error_label.setText("字段表与高级JSON共用同一协议；无效草稿不应用。")

    def document(self):
        fields = []
        for row in range(self.fields.rowCount()):
            cells = [self.fields.item(row, column).text().strip() for column in range(5)]
            options = json.loads(cells[4] or "{}")
            if not isinstance(options, dict):
                raise ValueError(f"fields[{row}]: 详细选项需为JSON对象")
            if set(options) & {"id", "type", "endian", "width", "count"}:
                raise ValueError(f"fields[{row}]: 详细选项不能重复表格列")
            item = {"id": cells[0], "type": cells[1], "endian": cells[2], **options}
            if cells[3]:
                item["count" if cells[1] == "array" else "width"] = int(cells[3])
            fields.append(item)
        end = self.checksum_end.text().strip()
        document = {"header_hex": self.header_edit.text(), "tail_hex": self.tail_edit.text(), "fields": fields,
                    "checksum": {"algorithm": self.checksum_combo.currentText(), "start": int(self.checksum_start.text()),
                                 "end": "checksum" if end == "checksum" else int(end),
                                 "byte_order": self.checksum_order.currentText()}}
        return FrameDefinition.from_document(document).document()

    def _changed(self, *_):
        if self._restoring:
            return
        try:
            document = self.document()
        except (ValueError, TypeError) as exc:
            self.error_label.setText(f"草稿未应用：{exc}")
            return
        self._document = document
        self.error_label.setText("字段定义有效；未连接、未发送。")
        self.definition_changed.emit(document)

    def add_field(self):
        if self._document is None:
            return
        document = self.document()
        names = {field["id"] for field in document["fields"]}
        index = 1
        while f"field{index}" in names:
            index += 1
        document["fields"].append({"id": f"field{index}", "type": "uint8"})
        self._replace(document)

    def remove_field(self):
        row = self.fields.currentRow()
        if row >= 0:
            document = self.document()
            del document["fields"][row]
            self._replace(document)

    def move_field(self, step):
        row = self.fields.currentRow()
        target = row + step
        if row >= 0 and 0 <= target < self.fields.rowCount():
            document = self.document()
            document["fields"][row], document["fields"][target] = document["fields"][target], document["fields"][row]
            self._replace(document)
            self.fields.selectRow(target)

    def _replace(self, document):
        try:
            self.set_document(document)
            self.definition_changed.emit(self.document())
        except ValueError as exc:
            self.error_label.setText(f"操作未应用：{exc}")

    def show_frame(self, frame):
        self._frame = frame
        self._selecting = True
        self.raw_view.setPlainText(frame.raw.hex(" ").upper())
        self.decoded_fields.setRowCount(len(frame.values))
        for row, (path, value) in enumerate(frame.values.items()):
            display = value.hex(" ").upper() if isinstance(value, bytes) else str(value)
            if path in frame.labels:
                display += " / " + frame.labels[path]
            values = (path, frame.field_types.get(path, ""), display[:2048], str(frame.spans.get(path, "")))
            for column, text in enumerate(values):
                self.decoded_fields.setItem(row, column, QTableWidgetItem(text))
        selections = []
        if frame.checksum_coverage[1] > frame.checksum_coverage[0]:
            selection = QTextEdit.ExtraSelection()
            selection.cursor = self._cursor_for_span(frame.checksum_coverage)
            selection.format.setBackground(QColor("#fff3c4"))
            selections.append(selection)
        self.raw_view.setExtraSelections(selections)
        self._selecting = False

    def _cursor_for_span(self, span):
        cursor = self.raw_view.textCursor()
        cursor.setPosition(span[0] * 3)
        cursor.setPosition(max(span[0] * 3, span[1] * 3 - 1), QTextCursor.KeepAnchor)
        return cursor

    def _select_field(self):
        if self._selecting or self._frame is None:
            return
        row = self.decoded_fields.currentRow()
        if row < 0:
            return
        path = self.decoded_fields.item(row, 0).text()
        if path in self._frame.spans:
            self._selecting = True
            self.raw_view.setTextCursor(self._cursor_for_span(self._frame.spans[path]))
            self._selecting = False

    def _select_byte(self):
        if self._selecting or self._frame is None:
            return
        offset = self.raw_view.textCursor().position() // 3
        candidates = [(end - start, path) for path, (start, end) in self._frame.spans.items() if start <= offset < end]
        if candidates:
            path = min(candidates)[1]
            self._selecting = True
            for row in range(self.decoded_fields.rowCount()):
                if self.decoded_fields.item(row, 0).text() == path:
                    self.decoded_fields.selectRow(row)
                    break
            self._selecting = False
