"""Schema5 pages/groups migrate without loading referenced data sources."""

import json
from dataclasses import asdict

import pytest

from qx_stc_isp.vofa.workspace import WorkspaceSettings, load_workspace, save_workspace


def test_schema_five_roundtrip_preserves_stable_pages_and_groups(tmp_path):
    from qx_stc_isp.vofa.dashboard import DashboardCard, DashboardPage
    from qx_stc_isp.vofa.commands import CommandEntry, CommandGroup, CommandItem

    page = DashboardPage("work", "实验", (DashboardCard("readout", "control", "电压", {"controls": [{"kind": "value", "channel": 3}]}),))
    group = CommandGroup("queries", "查询", (CommandItem("get", CommandEntry("读取", "GET")),))
    settings = WorkspaceSettings(pages=(page.to_document(),), command_groups=(group.to_document(),), active_page_id="work")
    path = tmp_path / "workspace.json"
    save_workspace(path, settings)
    assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == 8
    assert load_workspace(path) == settings


@pytest.mark.parametrize("version", [1, 2, 3, 4])
def test_every_legacy_schema_migrates_to_four_default_pages(tmp_path, version):
    from qx_stc_isp.vofa.workspace import _V1_FIELDS, _V2_FIELDS, _V3_FIELDS, _V4_FIELDS
    fields = _V1_FIELDS | (_V2_FIELDS if version >= 2 else set()) | (_V3_FIELDS if version >= 3 else set()) | (_V4_FIELDS if version >= 4 else set())
    original = WorkspaceSettings(
        active_visual_tab=2,
        controls=({"kind": "value", "channel": 6, "title": "传感器", "command": "", "settings": {"rect": [20, 30, 200, 100]}},),
        commands=({"name": "启动", "payload": "RUN", "mode": "text", "ending": "LF"},),
    )
    values = asdict(original)
    path = tmp_path / f"legacy-{version}.json"
    path.write_text(json.dumps({"schema_version": version, **{key: values[key] for key in fields}}), encoding="utf-8")
    migrated = load_workspace(path)
    assert len(migrated.pages) == 4
    if version == 4:
        assert migrated.active_page_id == "legacy-page-image"
        assert migrated.pages[1]["cards"][0]["config"]["controls"][0]["channel"] == 6
        assert migrated.pages[1]["cards"][0]["config"]["controls"][0]["settings"]["rect"] == [20, 30, 200, 100]
        assert migrated.command_groups[0]["commands"][0]["payload"] == "RUN"
    else:
        assert migrated.active_page_id == "legacy-page-waveform"
        assert migrated.command_groups[0]["commands"] == []
