"""Display-only channel calibration; raw samples and recorded bytes stay intact."""

from __future__ import annotations

import math
from dataclasses import dataclass

from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFormLayout, QLabel, QLineEdit, QSpinBox, QVBoxLayout)


@dataclass(frozen=True, slots=True)
class ChannelDisplay:
    name: str = ""
    scale: float = 1.0
    offset: float = 0.0
    unit: str = ""
    precision: int | None = None

    def __post_init__(self) -> None:
        for value, maximum in ((self.name, 64), (self.unit, 16)):
            if not isinstance(value, str) or len(value) > maximum or any(ord(char) < 32 for char in value):
                raise ValueError("通道名称或单位过长，或含控制字符")
        for value in (self.scale, self.offset):
            if type(value) not in (int, float) or not math.isfinite(value) or abs(value) > 1e9:
                raise ValueError("显示比例和偏移必须为绝对值不超过1e9的有限数")
        if self.precision is not None and (type(self.precision) is not int or not 0 <= self.precision <= 12):
            raise ValueError("独立小数位数须为0–12，或使用全局设置")

    def apply(self, value: float) -> float:
        result = value * self.scale + self.offset
        if not math.isfinite(result):
            raise ValueError("显示换算结果溢出；原始记录保留，该帧未用于显示")
        return result


class ChannelSettingsDialog(QDialog):
    def __init__(self, index: int, settings: ChannelDisplay, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"CH{index + 1} 通道参数")
        self.result_config: ChannelDisplay | None = None
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.name = QLineEdit(settings.name)
        self.name.setMaxLength(64)
        self.name.setPlaceholderText("留空使用通道列表名称")
        form.addRow("名称", self.name)
        self.scale, self.offset = QDoubleSpinBox(), QDoubleSpinBox()
        for widget, value in ((self.scale, settings.scale), (self.offset, settings.offset)):
            widget.setDecimals(9)
            widget.setRange(-1e9, 1e9)
            widget.setValue(value)
        form.addRow("显示比例 a", self.scale)
        form.addRow("显示偏移 b", self.offset)
        self.unit = QLineEdit(settings.unit)
        self.unit.setMaxLength(16)
        form.addRow("单位", self.unit)
        self.use_precision = QCheckBox("单独设置小数位数")
        self.use_precision.setChecked(settings.precision is not None)
        self.precision = QSpinBox()
        self.precision.setRange(0, 12)
        self.precision.setValue(settings.precision if settings.precision is not None else 6)
        self.precision.setEnabled(self.use_precision.isChecked())
        self.use_precision.toggled.connect(self.precision.setEnabled)
        form.addRow(self.use_precision, self.precision)
        layout.addLayout(form)
        note = QLabel("显示值 = 原始值 × a + b。仅影响波形、统计、数值和控件反馈；CSV及收发字节保留原始值。应用后按现存原始缓存重建波形并恢复自动视图。控件手动发送仍使用其自身模板和输入量程，不反算 a/b。")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.error = QLabel("")
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept_config)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def accept_config(self) -> None:
        try:
            self.result_config = ChannelDisplay(self.name.text(), self.scale.value(), self.offset.value(), self.unit.text(),
                                                self.precision.value() if self.use_precision.isChecked() else None)
        except ValueError as exc:
            self.error.setText(str(exc))
            return
        self.accept()
