"""Compact preferences-only SWD panel; no imports trigger hardware access."""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QWidget, QFormLayout, QComboBox, QLineEdit, QSpinBox, QPushButton, QFileDialog

from .swd import SwdCaptureConfig


class SwdSourcePanel(QWidget):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        form = QFormLayout(self)
        form.setContentsMargins(0, 0, 0, 0)
        self.adapter = QComboBox()
        self.adapter.addItem("ST-LINK", "stlink")
        self.adapter.addItem("CMSIS-DAP", "cmsis-dap")
        self.target = QComboBox()
        self.target.addItem("STM32G431", "stm32g4x")
        self.target.addItem("STM32F103", "stm32f1x")
        self.serial = QLineEdit()
        self.serial.setPlaceholderText("填写探针序列号")
        self.path = QLineEdit()
        self.path.setPlaceholderText("本轮测试固件的采集清单")
        self.period = QSpinBox()
        self.period.setRange(10, 500)
        self.period.setValue(50)
        self.period.setSuffix(" ms")
        choose = QPushButton("选择采集清单…")
        choose.clicked.connect(self.choose_manifest)
        for name, widget in (("探针", self.adapter), ("目标", self.target), ("序列号", self.serial),
                             ("采集清单", self.path), ("轮询周期", self.period)):
            form.addRow(name, widget)
        form.addRow(choose)
        for combo in (self.adapter, self.target):
            combo.currentIndexChanged.connect(self.changed)
        for edit in (self.serial, self.path):
            edit.textChanged.connect(self.changed)
        self.period.valueChanged.connect(self.changed)

    def choose_manifest(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择SWD采集清单", "", "JSON (*.json)")
        if path:
            self.path.setText(path)

    def config(self):
        return SwdCaptureConfig(self.adapter.currentData(), self.serial.text().strip(),
                                self.target.currentData(), self.path.text(), self.period.value())

    def restore(self, document):
        config = SwdCaptureConfig(**document)
        self.adapter.setCurrentIndex(self.adapter.findData(config.adapter))
        self.target.setCurrentIndex(self.target.findData(config.target))
        self.serial.setText(config.probe_serial)
        self.path.setText(config.manifest_path)
        self.period.setValue(config.poll_ms)
