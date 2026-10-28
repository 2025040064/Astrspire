"""A local palette editor; configuration contains colours, never executable CSS."""

from __future__ import annotations

import json
from .vofa.json_data import load_json_document
from pathlib import Path

from PySide6.QtWidgets import (
    QColorDialog, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QVBoxLayout, QWidget,
)

from .appearance import THEMES, THEME_LABELS, register_custom_theme, validate_theme_document


class ThemeEditorDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("自定义主题")
        self.selected_theme: str | None = None
        self._extra_colors: dict = {}
        self.resize(460, 460)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.identifier = QLineEdit("personal")
        self.name = QLineEdit("我的主题")
        self.identifier.setMaxLength(40)
        self.name.setMaxLength(40)
        form.addRow("标识", self.identifier)
        form.addRow("名称", self.name)
        self.base = QComboBox()
        for key in THEMES:
            self.base.addItem(THEME_LABELS[key], key)
        form.addRow("基础主题", self.base)
        self.fields = {}
        for key, title in (("accent", "强调色"), ("background", "窗口背景"), ("surface", "面板背景"),
                           ("text", "文本"), ("plot_grid", "波形网格"), ("plot_text", "坐标文本")):
            host = QWidget()
            row = QHBoxLayout(host)
            row.setContentsMargins(0, 0, 0, 0)
            edit = QLineEdit()
            edit.setMaxLength(7)
            edit.setPlaceholderText("留空沿用基础主题")
            button = QPushButton("选择…")
            button.clicked.connect(lambda checked=False, field=edit: self.pick_color(field))
            row.addWidget(edit, 1)
            row.addWidget(button)
            form.addRow(title, host)
            self.fields[key] = edit
        layout.addLayout(form)
        file_row = QHBoxLayout()
        load = QPushButton("导入主题…")
        load.clicked.connect(self.import_theme)
        save = QPushButton("导出配置…")
        save.clicked.connect(self.export_theme)
        file_row.addWidget(load)
        file_row.addWidget(save)
        layout.addLayout(file_row)
        self.error = QLabel("")
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.apply_selected)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def pick_color(self, edit: QLineEdit) -> None:
        color = QColorDialog.getColor(parent=self, title="选择主题颜色")
        if color.isValid():
            edit.setText(color.name())

    def document(self) -> dict:
        return validate_theme_document({"id": self.identifier.text(), "name": self.name.text(),
            "base": self.base.currentData(), "colors": {**self._extra_colors, **{key: edit.text() for key, edit in self.fields.items() if edit.text()}}})

    def apply_selected(self) -> None:
        try:
            self.selected_theme = register_custom_theme(self.document())
        except ValueError as exc:
            self.error.setText(str(exc))
            return
        self.accept()

    def import_theme(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(self, "导入主题颜色", "", "JSON (*.json)")
        if not filename:
            return
        try:
            with Path(filename).open("rb") as stream:
                encoded = stream.read(16385)
            if len(encoded) > 16384:
                raise ValueError("主题配置超过16KiB")
            document = validate_theme_document(load_json_document(encoded, max_bytes=16384))
            self.identifier.setText(document["id"])
            self.name.setText(document["name"])
            self.base.setCurrentIndex(self.base.findData(document["base"]))
            self._extra_colors = {key: value for key, value in document['colors'].items() if key not in self.fields}
            for key, edit in self.fields.items():
                edit.setText(document["colors"].get(key, ""))
        except (OSError, ValueError, UnicodeError) as exc:
            QMessageBox.warning(self, "主题导入失败", str(exc))

    def export_theme(self) -> None:
        try:
            document = self.document()
        except ValueError as exc:
            self.error.setText(str(exc))
            return
        filename, _ = QFileDialog.getSaveFileName(self, "导出主题配置", document["id"] + ".json", "JSON (*.json)")
        if filename:
            try:
                with Path(filename).open("x", encoding="utf-8") as stream:
                    json.dump(document, stream, ensure_ascii=False, indent=2)
            except OSError as exc:
                QMessageBox.warning(self, "主题导出失败", str(exc))
