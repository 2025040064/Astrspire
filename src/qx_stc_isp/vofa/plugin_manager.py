"""User-operated declarative engine editor; does not load DLLs or scripts."""

from __future__ import annotations

import json
from .json_data import load_json_document
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QMessageBox,
    QPlainTextEdit, QPushButton, QVBoxLayout,
)

from .plugins import EngineManifest, PLUGIN_REGISTRY, read_manifest


class PluginManagerDialog(QDialog):
    def __init__(self, parent=None, *, busy: bool = False) -> None:
        super().__init__(parent)
        self.setWindowTitle("数据引擎扩展")
        self.resize(660, 460)
        self._busy = busy
        layout = QVBoxLayout(self)
        note = QLabel("内置引擎 + 自定义文本/定长二进制引擎。扩展仅使用本软件的 JSON 清单，不执行脚本，不加载 VOFA DLL/QML。")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.entries = QListWidget()
        layout.addWidget(self.entries, 1)
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setMaximumHeight(150)
        layout.addWidget(self.detail)
        row = QHBoxLayout()
        for title, handler in (("新增…", self.create_engine), ("导入…", self.import_engine), ("导出…", self.export_engine), ("移除", self.remove_engine)):
            button = QPushButton(title)
            button.clicked.connect(handler)
            if title in ("新增…", "导入…", "移除"):
                button.setEnabled(not busy)
            row.addWidget(button)
        layout.addLayout(row)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.entries.currentRowChanged.connect(self.show_detail)
        self.refresh()

    def refresh(self) -> None:
        self.entries.clear()
        self.entries.addItems(["内置 · FireWater", "内置 · JustFloat", "内置 · RawData"])
        for manifest in PLUGIN_REGISTRY.engines:
            self.entries.addItem(f"{manifest.name}  [{manifest.identifier}]")
        self.entries.setCurrentRow(0)

    def selected(self) -> EngineManifest | None:
        index = self.entries.currentRow() - 3
        values = PLUGIN_REGISTRY.engines
        return values[index] if 0 <= index < len(values) else None

    def show_detail(self, _index=0) -> None:
        manifest = self.selected()
        self.detail.setPlainText(json.dumps(manifest.document(), ensure_ascii=False, indent=2) if manifest else "内置引擎不可移除。导入扩展只注册解析设置，不连接数据源或发送数据。")

    def import_engine(self) -> None:
        if self._busy:
            return
        filename, _ = QFileDialog.getOpenFileName(self, "导入数据引擎清单", "", "JSON (*.json)")
        if filename:
            try:
                PLUGIN_REGISTRY.register(read_manifest(filename))
                self.refresh()
            except (OSError, UnicodeError, ValueError) as exc:
                QMessageBox.warning(self, "导入失败", str(exc))

    def export_engine(self) -> None:
        manifest = self.selected()
        if manifest is None:
            return
        filename, _ = QFileDialog.getSaveFileName(self, "导出数据引擎", manifest.identifier + ".json", "JSON (*.json)")
        if filename:
            try:
                with Path(filename).open("x", encoding="utf-8") as stream:
                    json.dump(manifest.document(), stream, ensure_ascii=False, indent=2, allow_nan=False)
            except (OSError, ValueError) as exc:
                QMessageBox.warning(self, "导出失败", str(exc))

    def remove_engine(self) -> None:
        manifest = self.selected()
        if manifest is None or self._busy:
            return
        answer = QMessageBox.question(self, "移除数据引擎", f"从当前会话移除1个扩展“{manifest.name}”？磁盘文件不删除，可重新导入；未导出的配置将丢失。", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer == QMessageBox.Yes:
            PLUGIN_REGISTRY.unregister(manifest.identifier)
            self.refresh()

    def create_engine(self) -> None:
        if self._busy:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("新增数据引擎")
        dialog.resize(540, 480)
        layout = QVBoxLayout(dialog)
        form = QFormLayout()
        identifier, name = QLineEdit(), QLineEdit()
        identifier.setMaxLength(64)
        identifier.setPlaceholderText("例如 sensor_text")
        name.setMaxLength(80)
        form.addRow("扩展标识", identifier)
        form.addRow("显示名称", name)
        mode = QComboBox()
        mode.addItem("换行文本", "delimited")
        mode.addItem("定长二进制", "binary_fixed")
        form.addRow("解析方式", mode)
        layout.addLayout(form)
        example = QLabel("高级参数（只接受声明式 JSON，不执行代码）")
        layout.addWidget(example)
        options = QPlainTextEdit()
        layout.addWidget(options, 1)
        def reset_options():
            payload = {"separator": ",", "prefix": "", "channels": 0, "scale": 1, "offset": 0} if mode.currentData() == "delimited" else {
                "types": ["float32", "float32"], "endian": "little", "header_hex": "AA 55",
                "trailer_hex": "0D 0A", "checksum": "none", "scale": 1, "offset": 0,
            }
            options.setPlainText(json.dumps(payload, ensure_ascii=False, indent=2))
        mode.currentIndexChanged.connect(reset_options)
        reset_options()
        status = QLabel("")
        status.setWordWrap(True)
        layout.addWidget(status)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.rejected.connect(dialog.reject)
        def accept():
            try:
                text = options.toPlainText()
                if len(text.encode("utf-8")) > 64 * 1024:
                    raise ValueError("参数超过64KiB")
                manifest = EngineManifest.from_document({"schema_version": 1, "id": identifier.text(),
                    "name": name.text(), "mode": mode.currentData(), "options": load_json_document(text, max_bytes=16384)})
                PLUGIN_REGISTRY.register(manifest)
            except (ValueError, TypeError) as exc:
                status.setText(str(exc))
                return
            dialog.accept()
        buttons.accepted.connect(accept)
        layout.addWidget(buttons)
        if dialog.exec() == QDialog.Accepted:
            self.refresh()
