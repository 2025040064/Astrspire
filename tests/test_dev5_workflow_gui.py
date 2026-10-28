"""Native editor user actions, with no devices or network."""
import pytest
from PySide6.QtWidgets import QMessageBox

from qx_stc_isp.vofa.workflow_panel import WorkflowPanel


def test_editor_load_edit_undo_and_save_never_execute(qtbot):
    sent = []
    panel = WorkflowPanel(target_provider=lambda: {"connection_id": "board", "generation": 1,
                          "can_send": True, "source_kind": "live"},
                          enqueue=lambda *args: sent.append(args) or True)
    qtbot.addWidget(panel)
    panel.show()
    before = panel.document()
    identifier = panel.add_node("Math")
    assert identifier in [item["id"] for item in panel.document()["nodes"]]
    panel.undo()
    assert panel.document() == before
    panel.redo()
    saved = panel.document()
    panel.load_document(saved)
    assert panel.document() == saved
    assert not panel.active and sent == []
    panel.close()


def test_manual_run_is_explicit_and_delivers_derived_result(qtbot):
    results = []
    panel = WorkflowPanel(target_provider=lambda: {"connection_id": "board", "generation": 3,
                          "can_send": False, "source_kind": "replay"})
    qtbot.addWidget(panel)
    panel.result_ready.connect(results.append)
    panel.load_document({"version": 1, "revision": 4, "nodes": [
        {"id": "s", "type": "Source", "config": {"output_type": "number"}},
        {"id": "m", "type": "Math", "config": {"operation": "multiply", "operand": 2}},
        {"id": "e", "type": "End", "config": {}}], "edges": [
        {"from": "s", "out": "value", "to": "m", "in": "value"},
        {"from": "m", "out": "value", "to": "e", "in": "value"}]})
    panel.feed("board", 3, 5, sequence=1)
    assert results == []
    assert panel.start_run()
    qtbot.waitUntil(lambda: bool(results))
    assert results[-1].value == 10
    assert not panel.active

