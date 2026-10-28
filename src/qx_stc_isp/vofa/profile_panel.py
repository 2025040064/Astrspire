"""Native named profile/operation library, without transport ownership."""
from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QComboBox, QFileDialog, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QMessageBox, QPlainTextEdit, QPushButton, QSplitter, QVBoxLayout, QWidget)

from .device_profiles import DeviceProfile, ProfileLibrary
from .json_data import load_json_document
from .protocol import FrameDecoder, preview_protocol
from .protocol_editor import ProtocolFieldEditor
from .transmit import encode_send_payload


def _example():
    return {"schema_version": 1, "id": "user.device", "name": "新设备档案", "revision": 1,
            "connection": {"kinds": ["serial", "tcp_client", "offline"]},
            "definition": {"header_hex": "AA", "fields": [{"id": "value", "type": "uint16", "endian": "little"}]},
            "channels": ["value"], "operations": [{"id": "read", "name": "生成读取命令", "parameters": [],
                "definition": {"header_hex": "BB", "fields": [{"id": "command", "type": "id", "value": 1}]},
                "timeout_ms": 1000, "response": {"fields": {"value": 1}}}]}


class ProfilePanel(QWidget):
    changed = Signal()

    def __init__(self, owner, parent=None):
        super().__init__(parent)
        self.owner = owner
        if not hasattr(owner, "profile_library"):
            owner.profile_library = ProfileLibrary()
        self._frozen = None
        self._restoring = False
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("档案和参数只描述自有设备协议，不是芯片ISP协议。加载、编辑、生成和预览均不连接、不发送。"))
        row = QHBoxLayout()
        for title, callback in (("新档案", self.new_profile), ("保存修订", self.save_profile),
                                ("新修订", self.new_revision), ("复制档案", self.copy_profile),
                                ("导入…", self.import_library), ("导出…", self.export_library), ("删除修订…", self.delete_profile)):
            button = QPushButton(title)
            button.clicked.connect(callback)
            row.addWidget(button)
        layout.addLayout(row)
        split = QSplitter(Qt.Horizontal)
        self.profile_list = QListWidget()
        self.profile_list.setMinimumWidth(160)
        self.profile_list.currentItemChanged.connect(self._selected)
        split.addWidget(self.profile_list)
        right = QWidget()
        right_layout = QVBoxLayout(right)
        self.profile_edit = QPlainTextEdit()
        self.profile_edit.setPlaceholderText("完整设备档案JSON：接收定义、通道、操作、参数和应答。更改后显式保存为新修订。")
        right_layout.addWidget(self.profile_edit, 3)
        row = QHBoxLayout()
        row.addWidget(QLabel("发送操作"))
        self.operation_combo = QComboBox()
        row.addWidget(self.operation_combo, 1)
        for title, callback in (("生成预览", self.preview_operation), ("填入发送框", self.fill_operation),
                                ("执行一次", self.send_operation)):
            button = QPushButton(title)
            button.clicked.connect(callback)
            row.addWidget(button)
        right_layout.addLayout(row)
        self.parameters_edit = QPlainTextEdit("{}")
        self.parameters_edit.setMaximumHeight(100)
        self.parameters_edit.setPlaceholderText("参数JSON；未指定参数使用档案默认值")
        right_layout.addWidget(self.parameters_edit)
        self.result = QPlainTextEdit()
        self.result.setReadOnly(True)
        self.result.setMaximumHeight(140)
        right_layout.addWidget(self.result)
        split.addWidget(right)
        split.setSizes([180, 700])
        layout.addWidget(split, 3)
        self.field_editor = ProtocolFieldEditor()
        self.field_editor.setMinimumHeight(220)
        layout.addWidget(self.field_editor, 2)
        row = QHBoxLayout()
        self.rx_hex = QPlainTextEdit()
        self.rx_hex.setMaximumHeight(60)
        self.rx_hex.setPlaceholderText("接收帧HEX：仅离线解析和字段字节高亮")
        row.addWidget(self.rx_hex, 1)
        button = QPushButton("解析字段")
        button.clicked.connect(self.preview_receive)
        row.addWidget(button)
        layout.addLayout(row)
        self.profile_edit.textChanged.connect(self._draft_changed)
        self.parameters_edit.textChanged.connect(lambda: setattr(self, "_frozen", None))
        self.field_editor.definition_changed.connect(self._fields_changed)
        self.refresh()
        self.new_profile()

    @property
    def library(self):
        return self.owner.profile_library

    def refresh(self):
        current = self.profile_list.currentItem()
        key = current.data(Qt.UserRole) if current else None
        self.profile_list.blockSignals(True)
        self.profile_list.clear()
        for profile in self.library.profiles:
            item = QListWidgetItem(f"{profile.name} · r{profile.revision}\n{profile.identifier}")
            item.setData(Qt.UserRole, (profile.identifier, profile.revision))
            self.profile_list.addItem(item)
            if item.data(Qt.UserRole) == key:
                self.profile_list.setCurrentItem(item)
        self.profile_list.blockSignals(False)

    def _selected(self, item, _previous=None):
        if item is not None:
            profile = self.library.get(*item.data(Qt.UserRole))
            self.profile_edit.setPlainText(json.dumps(profile.document(), ensure_ascii=False, indent=2))
            self.result.setPlainText("已加载档案；未连接、未发送。")

    def new_profile(self):
        self.profile_list.clearSelection()
        self.profile_edit.setPlainText(json.dumps(_example(), ensure_ascii=False, indent=2))
        self.result.setPlainText("新草稿；未保存、未连接、未发送。")

    def _draft_changed(self):
        self._frozen = None
        if self._restoring:
            return
        selected = self.operation_combo.currentData()
        try:
            profile = DeviceProfile.from_document(load_json_document(self.profile_edit.toPlainText(), max_bytes=262144))
            self.field_editor.set_document(profile.document()["definition"])
            self.operation_combo.clear()
            for operation in profile.document()["operations"]:
                self.operation_combo.addItem(operation["name"], operation["id"])
            index = self.operation_combo.findData(selected)
            if index >= 0:
                self.operation_combo.setCurrentIndex(index)
        except (ValueError, TypeError) as exc:
            self.result.setPlainText(f"无效草稿未应用：{exc}")

    def _fields_changed(self, document):
        try:
            draft = load_json_document(self.profile_edit.toPlainText(), max_bytes=262144)
            draft["definition"] = document
            # Keep invalid channel/path references visible to be repaired, never apply.
            self.profile_edit.setPlainText(json.dumps(draft, ensure_ascii=False, indent=2))
        except (ValueError, TypeError) as exc:
            self.result.setPlainText(f"字段未应用：{exc}")

    def _dirty(self):
        callback = getattr(self.owner, "_mark_workspace_dirty", None)
        if callback:
            callback()
        self.changed.emit()

    def save_profile(self):
        try:
            profile = DeviceProfile.from_document(load_json_document(self.profile_edit.toPlainText(), max_bytes=262144))
            self.library.add(profile)
            self.refresh()
            self._dirty()
            self.result.setPlainText(f"已保存 {profile.identifier} r{profile.revision}；未连接、未发送。")
        except (ValueError, TypeError) as exc:
            self.result.setPlainText(str(exc))

    def new_revision(self):
        try:
            draft = load_json_document(self.profile_edit.toPlainText(), max_bytes=262144)
            revisions = [profile.revision for profile in self.library.profiles if profile.identifier == draft["id"]]
            draft["revision"] = max([draft["revision"], *revisions]) + 1
            self.profile_edit.setPlainText(json.dumps(draft, ensure_ascii=False, indent=2))
            self.result.setPlainText("已创建独立修订草稿，请检查后保存。现有任务快照不变。")
        except (ValueError, KeyError, TypeError) as exc:
            self.result.setPlainText(str(exc))

    def copy_profile(self):
        try:
            draft = load_json_document(self.profile_edit.toPlainText(), max_bytes=262144)
            draft.update(id="copy." + uuid4().hex[:12], revision=1, name=draft["name"] + " 副本")
            self.profile_edit.setPlainText(json.dumps(draft, ensure_ascii=False, indent=2))
            self.result.setPlainText("已复制为新ID草稿；配置独立，尚未保存。")
        except (ValueError, KeyError, TypeError) as exc:
            self.result.setPlainText(str(exc))

    def preview_operation(self):
        self._frozen = None
        try:
            profile = DeviceProfile.from_document(load_json_document(self.profile_edit.toPlainText(), max_bytes=262144))
            # Preview uses a private library; it cannot alter a saved revision.
            library = ProfileLibrary()
            library.add(profile)
            parameters = load_json_document(self.parameters_edit.toPlainText(), max_bytes=65536)
            self._frozen = library.freeze_operation(profile.identifier, self.operation_combo.currentData(), profile.revision, parameters)
            self.result.setPlainText(f"{profile.identifier} r{profile.revision} · {self._frozen.operation_id}\n"
                f"{len(self._frozen.payload)} 字节：{self._frozen.payload.hex(' ').upper()}\n"
                f"应答等待：{self._frozen.timeout_ms} ms；仅预览，未发送。")
        except (ValueError, TypeError) as exc:
            self.result.setPlainText(str(exc))

    def fill_operation(self):
        self.preview_operation()
        if self._frozen is None:
            return
        callback = getattr(self.owner, "fill_send_payload", None)
        if callback:
            callback(self._frozen.payload)
        elif all(hasattr(self.owner, name) for name in ("send_text_edit", "send_mode_combo", "send_ending_combo")):
            self.owner.send_text_edit.setText(self._frozen.payload.hex(" ").upper())
            self.owner.send_mode_combo.setCurrentIndex(self.owner.send_mode_combo.findData("hex"))
            self.owner.send_ending_combo.setCurrentIndex(self.owner.send_ending_combo.findData("none"))
        self.result.appendPlainText("仅填入发送框，未发送。")

    def send_operation(self):
        self.preview_operation()
        if self._frozen is None:
            return
        try:
            saved = self.library.get(self._frozen.profile_id, self._frozen.revision)
            draft = DeviceProfile.from_document(load_json_document(self.profile_edit.toPlainText(), max_bytes=262144))
            if saved.document() != draft.document():
                raise ValueError("草稿与已保存修订不同，请先另存为新修订；未发送。")
        except ValueError as exc:
            self.result.appendPlainText(f"请先保存有效档案：{exc}")
            return
        callback = getattr(self.owner, "send_profile_operation", None)
        if callback is None:
            self.result.appendPlainText("当前窗口未提供受控任务调度；请填入发送框显式发送，未直接打开串口。")
            return
        try:
            accepted = callback(self._frozen)
            self.result.appendPlainText("任务已提交；驱动完成和设备应答由会话状态分别报告。" if accepted is not False else "会话拒绝任务，未发送。")
        except (ValueError, RuntimeError) as exc:
            self.result.appendPlainText(str(exc))

    def preview_receive(self):
        try:
            profile = DeviceProfile.from_document(load_json_document(self.profile_edit.toPlainText(), max_bytes=262144))
            raw = encode_send_payload(self.rx_hex.toPlainText(), mode="hex")
            result = preview_protocol(profile.definition, raw)
            if result["frames"]:
                self.field_editor.show_frame(result["frames"][0])
            self.result.setPlainText(f"私有解析 {len(result['frames'])} 帧；半帧 {result['pending_bytes']} 字节；"
                                     f"噪声 {result['discarded_noise']}；截断 {result['truncated']}。\n"
                                     + "\n".join(frame.error or "校验通过" for frame in result["frames"]))
        except (ValueError, TypeError) as exc:
            self.result.setPlainText(str(exc))

    def delete_profile(self):
        item = self.profile_list.currentItem()
        if item is None:
            return
        identifier, revision = item.data(Qt.UserRole)
        callback = getattr(self.owner, "profile_reference_count", None)
        references = callback(identifier, revision) if callback else None
        impact = str(references) if references is not None else "未知（引用将失效并拒绝执行）"
        if QMessageBox.question(self, "删除档案修订", f"删除 {identifier} r{revision}？\n影响引用：{impact}。\n"
                "删除前可导出备份；此删除不撤销已经发送的设备操作。",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        self.library.delete(identifier, revision)
        self.refresh()
        self._dirty()

    def import_library(self):
        path, _ = QFileDialog.getOpenFileName(self, "导入档案库", "", "JSON (*.json)")
        if not path:
            return
        try:
            with Path(path).open("rb") as stream:
                raw = stream.read(2 * 1024 * 1024 + 1)
            imported = ProfileLibrary.from_document(load_json_document(raw.decode("utf-8-sig"), max_bytes=2 * 1024 * 1024))
            candidate = ProfileLibrary.from_document(self.library.document())
            for profile in imported.profiles:
                candidate.add(profile)
            # Commit atomically only when the whole candidate passes validation.
            self.owner.profile_library = candidate
            self.refresh()
            self._dirty()
            self.result.setPlainText("档案库导入完成；未连接、未发送。")
        except (ValueError, OSError, UnicodeError) as exc:
            self.result.setPlainText(str(exc))

    def export_library(self):
        path, _ = QFileDialog.getSaveFileName(self, "导出档案库", "device-profiles.json", "JSON (*.json)")
        if path:
            try:
                Path(path).write_text(json.dumps(self.library.document(), ensure_ascii=False, indent=2), encoding="utf-8")
            except OSError as exc:
                self.result.setPlainText(str(exc))
