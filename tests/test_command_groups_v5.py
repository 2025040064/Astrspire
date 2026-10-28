"""Command groups retain identity and freeze sends before later edits."""

from dataclasses import replace

import pytest


def test_group_snapshot_is_inert_and_unchanged_by_later_edit():
    from qx_stc_isp.vofa.commands import CommandEntry, CommandGroup, CommandItem

    group = CommandGroup("boot", "启动", (CommandItem("read", CommandEntry("读取", "READ")),))
    snapshot = group.snapshot()
    edited = replace(group, commands=(CommandItem("read", CommandEntry("读取", "NEW")),))
    assert snapshot[0].payload == "READ"
    assert edited.commands[0].id == "read"
    assert edited.snapshot()[0].payload == "NEW"
