"""Native, explicit-run editor for the typed workflow core.

Drafts and undo only change configuration. Transport access is injected by the
owning window; an unbound editor has no sending capability.
"""
from __future__ import annotations

import copy
import json
from uuid import uuid4

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QGraphicsItem, QGraphicsRectItem, QGraphicsScene,
    QGraphicsSimpleTextItem, QGraphicsView, QHBoxLayout, QLabel, QPlainTextEdit,
    QPushButton, QSplitter, QVBoxLayout, QWidget,
)

from .workflow import MAX_DOCUMENT_BYTES, NODE_TYPES, WorkflowInput, WorkflowProgram, WorkflowRunner


class _NodeItem(QGraphicsRectItem):
    def __init__(self, node, moved):
        super().__init__(0, 0, 155, 64)
        self.identifier = node["id"]
        self.moved = moved
        self.setBrush(QBrush(QColor("#edf6ff")))
        self.setPen(QPen(QColor("#3a82bf")))
        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsMovable |
                      QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        label = QGraphicsSimpleTextItem(f"{node['type']}\n{node['id'][:18]}", self)
        label.setPos(8, 10)
        self.setPos(node.get("x", 0), node.get("y", 0))

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        self.moved(self.identifier, self.pos().x(), self.pos().y())


class WorkflowPanel(QWidget):
    state_changed = Signal(bool)
    result_ready = Signal(object)
    error = Signal(str)
    document_changed = Signal()

    def __init__(self, parent=None, *, target_provider=None, enqueue=None,
                 template_resolver=None, cancel_job=None, guard=None):
        super().__init__(parent)
        self.target_provider = target_provider or (lambda: {})
        self._guard = guard
        self._latest = None
        self._history, self._future = [], []
        self._document = {"version": 1, "revision": 1, "nodes": [
            {"id": "source", "type": "Source", "config": {}, "x": 20, "y": 30},
            {"id": "end", "type": "End", "config": {}, "x": 240, "y": 30}],
            "edges": [{"from": "source", "out": "value", "to": "end", "in": "value"}]}
        self.runner = WorkflowRunner(enqueue=enqueue, template_resolver=template_resolver,
            cancel_job=cancel_job, guard=self._run_guard,
            on_result=self.result_ready.emit, on_state=self._state)
        layout = QVBoxLayout(self)
        toolbar = QHBoxLayout()
        self.node_types = QComboBox()
        self.node_types.addItems(NODE_TYPES)
        toolbar.addWidget(self.node_types)
        for text, callback in (("添加节点", lambda: self.add_node(self.node_types.currentText())),
                               ("撤销", self.undo), ("重做", self.redo),
                               ("运行", self.start_run), ("停止", self.stop)):
            button = QPushButton(text)
            button.clicked.connect(callback)
            toolbar.addWidget(button)
        self.arm = QCheckBox("本次允许发送")
        toolbar.addWidget(self.arm)
        toolbar.addStretch()
        layout.addLayout(toolbar)
        self.status = QLabel("未运行；载入、编辑与撤销不会发送数据")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.scene = QGraphicsScene(self)
        self.view = QGraphicsView(self.scene)
        self.view.setRenderHint(QPainter.RenderHint.Antialiasing)
        splitter.addWidget(self.view)
        draft = QWidget()
        draft_layout = QVBoxLayout(draft)
        self.editor = QPlainTextEdit()
        self.editor.setPlaceholderText("工作流 JSON；无效草稿不会替换已应用配置")
        draft_layout.addWidget(self.editor)
        apply_button = QPushButton("应用 JSON 草稿（不运行）")
        apply_button.clicked.connect(self.apply_draft)
        draft_layout.addWidget(apply_button)
        splitter.addWidget(draft)
        splitter.setSizes([650, 350])
        layout.addWidget(splitter, 1)
        self._timer = QTimer(self)
        self._timer.setInterval(20)
        self._timer.timeout.connect(self.runner.poll)
        self._render()

    @property
    def active(self):
        return self.runner.active

    def document(self):
        return copy.deepcopy(self._document)

    @staticmethod
    def _draft(document):
        serialized = json.dumps(document, ensure_ascii=False, allow_nan=False)
        if len(serialized.encode("utf-8")) > MAX_DOCUMENT_BYTES:
            raise ValueError("工作流超过256KiB")
        value = json.loads(serialized)
        if not isinstance(value, dict) or value.get("version") != 1 or not isinstance(value.get("nodes"), list):
            raise ValueError("工作流需要 version=1 和 nodes 列表")
        if len(value["nodes"]) > 128 or not isinstance(value.get("edges", []), list) or len(value.get("edges", [])) > 256:
            raise ValueError("节点或连线超过上限")
        ids = set()
        for node in value["nodes"]:
            if (not isinstance(node, dict) or not isinstance(node.get("id"), str) or not node["id"] or
                node["id"] in ids or node.get("type") not in NODE_TYPES or not isinstance(node.get("config", {}), dict)):
                raise ValueError("节点类型、ID或配置无效")
            ids.add(node["id"])
            for coordinate in ("x", "y"):
                number = node.get(coordinate, 0)
                if type(number) not in (int, float) or abs(number) > 100000:
                    raise ValueError("节点坐标无效")
        for edge in value.get("edges", []):
            if not isinstance(edge, dict) or set(edge) != {"from", "out", "to", "in"} or edge["from"] not in ids or edge["to"] not in ids:
                raise ValueError("连线引用无效")
        return value

    def load_document(self, document):
        candidate = self._draft(document)
        self.stop("配置已载入；未恢复发送许可")
        self._document = candidate
        self._history.clear()
        self._future.clear()
        self._latest = None
        self.arm.setChecked(False)
        self._render()
        self.document_changed.emit()

    def _commit(self, document):
        candidate = self._draft(document)
        if candidate == self._document:
            return
        self._history.append(self.document())
        while len(self._history) > 200 or sum(len(json.dumps(item)) for item in self._history) > 8 * 1024 * 1024:
            self._history.pop(0)
        self._future.clear()
        self._document = candidate
        self._render()
        self.document_changed.emit()

    def add_node(self, kind):
        if kind not in NODE_TYPES:
            raise ValueError("节点类型无效")
        document = self.document()
        identifier = "node_" + uuid4().hex[:12]
        document["nodes"].append({"id": identifier, "type": kind, "config": {},
                                   "x": 30 + (len(document["nodes"]) % 4) * 180,
                                   "y": 140 + (len(document["nodes"]) // 4) * 100})
        self._commit(document)
        return identifier

    def _moved(self, identifier, x, y):
        document = self.document()
        for node in document["nodes"]:
            if node["id"] == identifier:
                node.update(x=round(x, 2), y=round(y, 2))
        self._commit(document)

    def undo(self):
        if self._history:
            self._future.append(self.document())
            self._document = self._history.pop()
            self._render()
            self.document_changed.emit()

    def redo(self):
        if self._future:
            self._history.append(self.document())
            self._document = self._future.pop()
            self._render()
            self.document_changed.emit()

    def apply_draft(self):
        try:
            self._commit(json.loads(self.editor.toPlainText()))
            self.status.setText("草稿已应用，未运行；运行前仍会校验类型和连线")
            return True
        except (ValueError, TypeError) as exc:
            self.status.setText(str(exc))
            self.error.emit(str(exc))
            return False

    def _render(self):
        self.editor.setPlainText(json.dumps(self._document, ensure_ascii=False, indent=2))
        self.scene.clear()
        positions = {}
        for index, node in enumerate(self._document["nodes"]):
            visual = dict(node)
            visual.setdefault("x", (index % 4) * 190)
            visual.setdefault("y", (index // 4) * 100)
            item = _NodeItem(visual, self._moved)
            self.scene.addItem(item)
            positions[node["id"]] = item.pos()
        for edge in self._document.get("edges", []):
            first, second = positions[edge["from"]], positions[edge["to"]]
            self.scene.addLine(first.x() + 155, first.y() + 32, second.x(), second.y() + 32, QPen(QColor("#6a98bf"), 2))
        self.scene.setSceneRect(self.scene.itemsBoundingRect().adjusted(-30, -30, 30, 30))

    def _run_guard(self, connection_id, generation):
        if self._guard is not None:
            return bool(self._guard(connection_id, generation))
        # Resolve the frozen target, never re-route a running job to selection.
        target = self.target_provider() or {}
        return (target.get("connection_id") == connection_id and target.get("generation") == generation and
                (not self.runner.program.has_send or target.get("can_send", False)))

    def start_run(self):
        try:
            program = WorkflowProgram.compile(self._document)
            target = dict(self.target_provider() or {})
            if program.has_send and not target.get("can_send", False):
                raise ValueError("选中数据源不可发送")
            self.runner.start(program, connection_id=target.get("connection_id"),
                generation=target.get("generation"), arm_sends=self.arm.isChecked(),
                source_kind=target.get("source_kind", "preview"))
            self._timer.start()
            if self._latest is not None:
                self.runner.feed(self._latest)
            return True
        except (ValueError, TypeError, KeyError) as exc:
            self.status.setText(str(exc))
            self.error.emit(str(exc))
            return False

    def feed(self, connection_id, generation, value, *, fields=None, sequence=0, valid=True):
        event = WorkflowInput(connection_id, generation, sequence, value, fields or {}, valid)
        self._latest = event
        if self.active:
            self.runner.feed(event)

    def _state(self, state):
        self.status.setText({"running": "运行中", "waiting": "等待有效响应", "complete": "计算完成",
                             "stopped": "已停止", "failed": "失败：" + self.runner.error}.get(state, state))
        if not self.runner.active:
            self._timer.stop()
        if state == "failed":
            self.error.emit(self.runner.error)
        self.state_changed.emit(self.runner.active)

    def stop(self, reason="用户停止"):
        self._timer.stop()
        self.runner.stop(str(reason))
        self.arm.setChecked(False)

    def closeEvent(self, event):
        self.stop("编辑器已关闭")
        super().closeEvent(event)
