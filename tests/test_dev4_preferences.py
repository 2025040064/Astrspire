import pytest


def test_history_is_bounded_deduplicated_and_configuration_undo_is_detached():
    from qx_stc_isp.vofa.serial_preferences import SendHistory, ConfigHistory
    history = SendHistory()
    for index in range(25):
        history.record(str(index), "text", "none", "utf-8")
    history.record("24", "text", "none", "utf-8")
    assert len(history.entries) == 20 and history.entries[0]["text"] == "24"
    config = {"text": "first"}
    undo = ConfigHistory(config)
    config["text"] = "external"
    undo.commit({"text": "second"})
    assert undo.undo() == {"text": "first"}
    assert undo.redo() == {"text": "second"}
    undo.undo()
    undo.commit({"text": "branch"})
    assert not undo.can_redo


def test_schema_eight_preserves_tx_and_migrates_old_manual_encoding(tmp_path):
    import json
    from qx_stc_isp.vofa.workspace import WorkspaceSettings, save_workspace, load_workspace
    settings = WorkspaceSettings(rx_encoding="gbk", tx_encoding="ascii", serial_flow_control="xonxoff")
    path = tmp_path / "new.json"
    save_workspace(path, settings)
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["schema_version"] == 8 and load_workspace(path) == settings
    document["schema_version"] = 7
    for key in ("tx_encoding", "serial_flow_control", "connection_presets", "serial_tools"):
        document.pop(key)
    path.write_text(json.dumps(document), encoding="utf-8")
    restored = load_workspace(path)
    assert restored.tx_encoding == "gbk" and restored.serial_flow_control == "none"


def test_flow_control_is_explicit_and_does_not_change_legacy_isp_constructor():
    from qx_stc_isp.transport import PySerialTransport
    calls = []
    class Handle:
        is_open = True
        def close(self):
            self.is_open = False
    def factory(**kwargs):
        calls.append(kwargs)
        return Handle()
    transport = PySerialTransport("offline", serial_factory=factory, flow_control="rtscts")
    transport.open(115200)
    transport.close()
    assert calls[0]["rtscts"] and calls[0]["xonxoff"] is False
    legacy = PySerialTransport("offline", serial_factory=factory)
    legacy.open(2400)
    legacy.close()
    assert "rtscts" not in calls[1] and "xonxoff" not in calls[1]


def test_commands_and_controls_have_explicit_encoding_without_rx_coupling():
    from qx_stc_isp.vofa.commands import CommandEntry
    from qx_stc_isp.vofa.controls import ControlSpec
    command = CommandEntry("test", "测试", encoding="gbk", ending="CR")
    assert command.encoding == "gbk"
    control = ControlSpec("button", command="测试", settings={"tx_encoding": "gbk", "tx_ending": "CR"})
    assert control.settings["tx_encoding"] == "gbk"
