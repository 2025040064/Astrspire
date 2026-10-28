"""Manual display preferences; applying them never changes acquired samples."""

from __future__ import annotations

import math

from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QDoubleSpinBox, QFormLayout, QLabel, QLineEdit, QSpinBox, QVBoxLayout)


class PlotSettingsDialog(QDialog):
    def __init__(self, waveform, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("波形显示设置")
        self.resize(420, 430)
        self.result_config = None
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.mode = QComboBox()
        self.mode.addItem("时间曲线", "time")
        self.mode.addItem("XY 曲线", "xy")
        self.mode.setCurrentIndex(self.mode.findData(waveform.mode))
        form.addRow("显示方式", self.mode)
        self.x_channel, self.y_channel = QSpinBox(), QSpinBox()
        for spin, value in zip((self.x_channel, self.y_channel), waveform.xy_channels):
            spin.setRange(1, 16)
            spin.setValue(value + 1)
            spin.setPrefix("CH ")
        form.addRow("XY 横轴", self.x_channel)
        form.addRow("XY 纵轴", self.y_channel)
        self.grid = QCheckBox("显示网格")
        self.grid.setChecked(waveform.grid_visible)
        form.addRow(self.grid)
        self.width = QDoubleSpinBox()
        self.width.setRange(.5, 5)
        self.width.setSingleStep(.1)
        self.width.setValue(waveform.line_width)
        form.addRow("曲线宽度", self.width)
        self.ranges = {}
        values = waveform.axis_ranges
        for key, title in (("x_min", "X下限"), ("x_max", "X上限"), ("y_min", "Y下限"), ("y_max", "Y上限")):
            edit = QLineEdit("" if values[key] is None else repr(values[key]))
            edit.setMaxLength(64)
            edit.setPlaceholderText("留空自动，同轴上下限成对填写")
            self.ranges[key] = edit
            form.addRow(title, edit)
        layout.addLayout(form)
        hint = QLabel("时间曲线的X单位为秒；XY使用绑定通道的实际值。Home或自动适应可解除固定范围。通道颜色在右侧通道项右键修改。")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.error = QLabel("")
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept_config)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def accept_config(self) -> None:
        try:
            values = {key: float(edit.text()) if edit.text().strip() else None for key, edit in self.ranges.items()}
            for axis in ("x", "y"):
                low, high = values[axis + "_min"], values[axis + "_max"]
                if (low is None) != (high is None):
                    raise ValueError("同一坐标轴上下限需要成对填写或都留空")
                if low is not None and (not all(math.isfinite(value) for value in (low, high)) or low >= high or not math.isfinite(high - low)):
                    raise ValueError("坐标轴上下限必须为有限数且下限小于上限")
        except ValueError as exc:
            self.error.setText(str(exc))
            return
        self.result_config = {"mode": self.mode.currentData(), "channels": (self.x_channel.value() - 1, self.y_channel.value() - 1),
                              "grid": self.grid.isChecked(), "width": self.width.value(), "axes": values}
        self.accept()
