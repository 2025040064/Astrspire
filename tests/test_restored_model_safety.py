"""Restored model paths are references, not permission to read local files."""

from qx_stc_isp.vofa.controls import ControlWorkspace


def test_loading_control_configuration_never_reads_referenced_model(qtbot, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("restoring a configuration read an external model")

    monkeypatch.setattr("qx_stc_isp.vofa.extra_controls.load_wireframe_model", forbidden)
    view = ControlWorkspace()
    qtbot.addWidget(view)
    view.load_config([{"kind": "cube", "channel": 2, "title": "姿态", "command": "", "settings": {"model_path": "F:/private/never-read.stl"}}])
    assert view.export_config()[0]["settings"]["model_path"] == "F:/private/never-read.stl"
