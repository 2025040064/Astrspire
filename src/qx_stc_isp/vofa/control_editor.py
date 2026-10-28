"""Explicit control-property editing; accepting a dialog never sends data."""

from __future__ import annotations

import json

from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout,
    QLabel, QLineEdit, QPlainTextEdit, QScrollArea, QSpinBox, QTabWidget,
    QVBoxLayout, QWidget,
)

from .controls import CONTROL_KINDS, SENDING_CONTROL_KINDS, ControlSpec
from .json_data import load_json_document

CONTROL_LABELS = {
    "value": "数值", "gauge": "指针仪表", "circular_gauge": "环形仪表", "button": "按钮",
    "slider": "滑块", "dial": "经典旋钮", "material_dial": "扁平旋钮", "toggle": "开关", "delayed_button": "长按按钮",
    "joystick": "摇杆", "light": "指示灯", "cube": "姿态立方体", "image": "图像卡片",
}

# Visible fields use one-based channel numbers; persisted settings stay zero-based.
_RANGE = ("minimum", "maximum", "step", "precision")
_FIELDS = {
    "value": ("precision",),
    "gauge": ("minimum", "maximum", "precision", "major_ticks"),
    "circular_gauge": ("minimum", "maximum", "precision", "major_ticks", "start_angle", "end_angle"),
    "button": (), "slider": _RANGE, "dial": _RANGE, "material_dial": _RANGE,
    "toggle": ("threshold", "threshold_inverted", "off_command"),
    "delayed_button": ("delay_ms",),
    "joystick": _RANGE + ("channel_y", "return_to_center"),
    "light": ("threshold", "threshold_inverted", "three_color", "lower_threshold", "upper_threshold", "precision"),
    "cube": ("channel_y", "channel_z", "quaternion", "channel_w", "radians", "roll_offset", "pitch_offset", "yaw_offset", "scale", "show_axes", "model_path", "precision"),
    "image": ("image_source", "image_index", "image_path"),
}
_LABELS = {
    "minimum": "最小值", "maximum": "最大值", "step": "步进", "precision": "小数位数",
    "major_ticks": "主刻度数", "start_angle": "起始角度", "end_angle": "结束角度",
    "threshold": "触发阈值", "threshold_inverted": "反向判断", "off_command": "关闭命令",
    "delay_ms": "长按时长（ms）", "channel_y": "第二通道", "channel_z": "第三通道", "channel_w": "四元数 W 通道",
    "return_to_center": "松开后回中（不发送零值）", "quaternion": "四元数输入", "radians": "弧度输入",
    "roll_offset": "Roll 偏移", "pitch_offset": "Pitch 偏移", "yaw_offset": "Yaw 偏移",
    "scale": "显示比例", "show_axes": "显示坐标轴", "image_path": "图片路径（不自动读取）",
    "model_path": "模型路径（.stl/.obj，留空用立方体）",
    "image_source": "图像来源", "image_index": "实时图像索引（0–15）",
    "three_color": "三色区间模式", "lower_threshold": "下阈值", "upper_threshold": "上阈值",
}
_DEFAULTS = {
    "minimum": -1.0, "maximum": 1.0, "step": .01, "precision": 3,
    "major_ticks": 5, "start_angle": 225.0, "end_angle": -45.0,
    "threshold": .5, "threshold_inverted": False, "off_command": "", "delay_ms": 1000,
    "channel_y": 1, "channel_z": 2, "channel_w": 3, "return_to_center": True,
    "quaternion": False, "radians": False, "roll_offset": 0.0, "pitch_offset": 0.0,
    "yaw_offset": 0.0, "scale": 1.0, "show_axes": True, "image_path": "", "model_path": "",
    "image_source": "local", "image_index": 0,
    "three_color": False, "lower_threshold": 0.0, "upper_threshold": 1.0,
}


class ControlEditorDialog(QDialog):
    def __init__(self, kind: str = "value", *, spec: ControlSpec | None = None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("编辑控件" if spec else "新增控件")
        self.resize(560, 680)
        self.result_spec: ControlSpec | None = None
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.kind = QComboBox()
        for value in CONTROL_KINDS:
            self.kind.addItem(CONTROL_LABELS.get(value, value), value)
        self.kind.setCurrentIndex(max(0, self.kind.findData(spec.kind if spec else kind)))
        form.addRow("类型", self.kind)
        self.channel = QSpinBox()
        self.channel.setRange(1, 16)
        self.channel.setValue(spec.channel + 1 if spec else 1)
        form.addRow("绑定通道", self.channel)
        self.title = QLineEdit(spec.title if spec else "")
        self.title.setMaxLength(80)
        form.addRow("名称", self.title)
        self.command = QLineEdit(spec.command if spec else "")
        self.command.setMaxLength(4096)
        self.command.setPlaceholderText("例如 set:{value} 或 joy:{x},{y}")
        form.addRow("发送模板", self.command)
        layout.addLayout(form)
        self.tabs = QTabWidget()
        self.properties_scroll = QScrollArea()
        self.properties_scroll.setWidgetResizable(True)
        self.tabs.addTab(self.properties_scroll, "常用参数")
        self.options = QPlainTextEdit()
        self.tabs.addTab(self.options, "附加参数 JSON")
        layout.addWidget(self.tabs, 1)
        self._editors: dict[str, QWidget] = {}
        self._edge_enabled: QCheckBox | None = None
        self._build_properties(spec.settings if spec else {})
        self.kind.currentIndexChanged.connect(lambda _index: self._build_properties({}))
        hint = QLabel("发送模板：{value} 或 {x},{y}；HEX 可用 {value:02X}。常用参数由表单保存，不要在附加 JSON 中重复。仪表危险区可在 JSON 写 danger_min / danger_max。按下/松开模式启用后忽略主发送模板；取消交互不补发松开命令。")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.error = QLabel("")
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._accept_config)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _build_properties(self, settings: dict) -> None:
        content = QWidget()
        form = QFormLayout(content)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self._editors = {}
        self._edge_enabled = None
        kind = self.kind.currentData()
        keys = list(_FIELDS[kind])
        if kind in SENDING_CONTROL_KINDS:
            keys += ["tx_mode", "tx_ending", "tx_encoding"]
        if kind == "button":
            self._edge_enabled = QCheckBox("分别发送按下和松开命令")
            self._edge_enabled.setChecked(any(key in settings for key in ("press_command", "release_command")))
            form.addRow(self._edge_enabled)
            keys += ["press_command", "release_command"]
        for key in keys:
            default = _DEFAULTS.get(key, "")
            value = settings.get(key, default)
            if key == "image_source":
                editor = QComboBox()
                for title, data in (("本地图片", "local"), ("实时图像", "live")):
                    editor.addItem(title, data)
                editor.setCurrentIndex(max(0, editor.findData(value)))
                label = _LABELS[key]
            elif key == "image_index":
                editor = QSpinBox()
                editor.setRange(0, 15)
                editor.setValue(int(value))
                label = _LABELS[key]
            elif key == "tx_encoding":
                editor = QComboBox()
                for codec in ("utf-8", "gbk", "latin-1", "ascii", "utf-16le"):
                    editor.addItem(codec.upper(), codec)
                editor.setCurrentIndex(max(0, editor.findData(settings.get(key, "utf-8"))))
                label = "发送编码"
            elif key in ("tx_mode", "tx_ending"):
                editor = QComboBox()
                choices = (("文本", "text"), ("HEX", "hex")) if key == "tx_mode" else (("无", "none"), ("LF", "LF"), ("CRLF", "CRLF"), ("CR", "CR"), ("TAB", "TAB"))
                for title, data in choices:
                    editor.addItem(title, data)
                editor.setCurrentIndex(max(0, editor.findData(value)))
                label = "发送格式" if key == "tx_mode" else "结束符"
            elif isinstance(default, bool):
                editor = QCheckBox()
                editor.setChecked(bool(value))
                label = _LABELS[key]
            elif key.startswith("channel_") or key in ("precision", "major_ticks", "delay_ms"):
                editor = QSpinBox()
                if key.startswith("channel_"):
                    editor.setRange(1, 16)
                    editor.setValue(int(value) + 1)
                else:
                    bounds = {"precision": (0, 9), "major_ticks": (2, 20), "delay_ms": (100, 60000)}
                    editor.setRange(*bounds[key])
                    editor.setValue(int(value))
                label = _LABELS[key]
            elif isinstance(default, float):
                editor = QDoubleSpinBox()
                editor.setDecimals(9)
                editor.setRange(-1e12, 1e12)
                if key == "scale":
                    editor.setRange(.1, 2)
                editor.setValue(float(value))
                label = _LABELS[key]
            else:
                editor = QLineEdit(str(value))
                editor.setMaxLength(4096)
                label = {"press_command": "按下命令", "release_command": "松开命令"}.get(key, _LABELS.get(key, key))
            self._editors[key] = editor
            form.addRow(label, editor)
        if self._edge_enabled is not None:
            def toggle_edges(enabled):
                self.command.setEnabled(not enabled)
                self._editors["press_command"].setEnabled(enabled)
                self._editors["release_command"].setEnabled(enabled)
            self._edge_enabled.toggled.connect(toggle_edges)
            toggle_edges(self._edge_enabled.isChecked())
        else:
            self.command.setEnabled(kind in SENDING_CONTROL_KINDS)
        self.properties_scroll.setWidget(content)
        extras = {key: value for key, value in settings.items() if key not in keys}
        self.options.setPlainText(json.dumps(extras, ensure_ascii=False, indent=2))

    def _property_values(self) -> dict:
        values = {}
        for key, editor in self._editors.items():
            if key in ("press_command", "release_command") and not self._edge_enabled.isChecked():
                continue
            if isinstance(editor, QComboBox):
                value = editor.currentData()
            elif isinstance(editor, QCheckBox):
                value = editor.isChecked()
            elif isinstance(editor, QSpinBox):
                value = editor.value() - (1 if key.startswith("channel_") else 0)
            elif isinstance(editor, QDoubleSpinBox):
                value = editor.value()
            else:
                value = editor.text()
            values[key] = value
        return values

    def _accept_config(self) -> None:
        try:
            text = self.options.toPlainText()
            if len(text.encode("utf-8")) > 16384:
                raise ValueError("控件参数超过16KiB")
            options = load_json_document(text, max_bytes=16384)
            if not isinstance(options, dict):
                raise ValueError("参数必须为JSON对象")
            properties = self._property_values()
            duplicates = set(options) & set(self._editors)
            if duplicates:
                raise ValueError("这些参数请在表单填写，不要重复：" + ", ".join(sorted(duplicates)))
            options.update(properties)
            self.result_spec = ControlSpec(self.kind.currentData(), self.channel.value() - 1,
                                           self.title.text(), self.command.text(), options)
        except (TypeError, ValueError) as exc:
            self.error.setText(str(exc))
            return
        self.accept()
