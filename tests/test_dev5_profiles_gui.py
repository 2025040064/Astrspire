"""Native field/profile editor user actions are inert until explicit send."""
import json
from types import SimpleNamespace

from PySide6.QtWidgets import QWidget

from test_dev5_profiles_library import profile_document
from test_dev5_profiles_protocol import array_definition


def test_visual_field_changes_and_json_share_definition_and_highlight(qtbot):
    from qx_stc_isp.vofa.protocol import FrameDefinition, FrameDecoder
    from qx_stc_isp.vofa.protocol_editor import ProtocolFieldEditor
    editor = ProtocolFieldEditor()
    qtbot.addWidget(editor)
    definition = FrameDefinition.from_document(array_definition())
    editor.set_document(definition.document())
    seen = []
    editor.definition_changed.connect(seen.append)
    editor.fields.item(0, 0).setText("kind")
    assert not seen and "when" in editor.error_label.text()
    editor.set_document(definition.document())
    editor.fields.item(2, 0).setText("state")
    assert seen[-1]["fields"][2]["id"] == "state"
    frame = FrameDecoder(definition).feed(bytes.fromhex("AA 01 FE FF 2C 01 D5 55"))[0]
    editor.show_frame(frame)
    row = next(row for row in range(editor.decoded_fields.rowCount())
               if editor.decoded_fields.item(row, 0).text() == "values[1]")
    editor.decoded_fields.selectRow(row)
    assert editor.raw_view.textCursor().selectedText() == "2C 01"


def test_profile_library_preview_and_load_never_invoke_sender(qtbot):
    from qx_stc_isp.vofa.device_profiles import ProfileLibrary
    from qx_stc_isp.vofa.profile_panel import ProfilePanel
    owner = QWidget()
    owner.profile_library = ProfileLibrary()
    owner._mark_workspace_dirty = lambda: None
    sends = []
    owner.send_profile_operation = lambda frozen: sends.append(frozen)
    panel = ProfilePanel(owner)
    qtbot.addWidget(panel)
    panel.profile_edit.setPlainText(json.dumps(profile_document(), ensure_ascii=False))
    panel.save_profile()
    assert len(owner.profile_library.profiles) == 1 and not sends
    panel.parameters_edit.setPlainText('{"value": 300}')
    panel.preview_operation()
    assert "CC 2C 01 F9" in panel.result.toPlainText() and not sends
    panel.send_operation()
    assert len(sends) == 1 and sends[0].payload == bytes.fromhex("CC 2C 01 F9")
    panel.parameters_edit.setPlainText('{"value": 1001}')
    panel.send_operation()
    assert len(sends) == 1
    modified = profile_document()
    modified["operations"][0]["definition"]["header_hex"] = "DD"
    panel.profile_edit.setPlainText(json.dumps(modified))
    panel.parameters_edit.setPlainText('{"value": 300}')
    panel.send_operation()
    assert len(sends) == 1 and "保存" in panel.result.toPlainText()


def test_device_profiles_and_field_editor_are_reachable_in_workbench(qtbot, tmp_path, monkeypatch):
    from qx_stc_isp.gui import MainWindow
    monkeypatch.setattr("serial.Serial", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("No hardware")))
    monkeypatch.setattr("qx_stc_isp.gui.enumerate_ports", lambda: [])
    owner = MainWindow(log_dir=tmp_path / "logs")
    qtbot.addWidget(owner)
    owner.open_serial_tools()
    tool = owner.serial_tools_dialog
    assert tool.profile_panel.library is owner.profile_library
    tool.generate()
    tool.preview_offline()
    assert tool.field_editor.decoded_fields.rowCount() >= 2
    assert owner._monitor_worker is None and owner._tx_bytes == 0
    tool.close()
    owner.close()
