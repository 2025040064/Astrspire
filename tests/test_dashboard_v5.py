"""Stable dashboard identities and inert legacy migration."""

from copy import deepcopy

import pytest


def test_copy_and_reorder_keep_binding_and_independent_configuration():
    from qx_stc_isp.vofa.dashboard import DashboardCard, DashboardCollection, DashboardPage

    card = DashboardCard("temperature", "control", "温度", {"controls": [{"kind": "value", "channel": 4}]})
    pages = DashboardCollection((DashboardPage("main", "主页面", (card,)),))
    duplicate = pages.copy_page("main")
    pages.move_page(duplicate.id, 0)
    assert pages.pages[0].cards[0].id != "temperature"
    assert pages.pages[0].cards[0].config["controls"][0]["channel"] == 4
    exported = pages.to_documents()
    exported[0]["cards"][0]["config"]["controls"][0]["channel"] = 2
    assert pages.pages[1].cards[0].config["controls"][0]["channel"] == 4
    assert pages.pages[0].cards[0].config["controls"][0]["channel"] == 4


def test_manager_mixed_page_uses_shared_snapshot_and_never_sends_when_restored(qtbot):
    from qx_stc_isp.vofa.dashboard import DashboardCard, DashboardManager, DashboardPage

    page = DashboardPage("mixed", "混合", (
        DashboardCard("plot", "waveform", "波形"),
        DashboardCard("readout", "control", "读数", {"controls": [{"kind": "value", "channel": 1}]}),
        DashboardCard("stats", "analysis", "统计"),
    ))
    manager = DashboardManager()
    qtbot.addWidget(manager)
    packets = []
    manager.packet_requested.connect(packets.append)
    manager.load_pages([page.to_document()], "mixed")
    manager.set_sample_snapshot([(1.0, 2.0), (3.0, 4.0)], ("A", "B"), lambda values: tuple(value * 2 for value in values))
    assert manager.primary_widget("waveform").displayed_sample_count == 2
    assert manager.primary_widget("waveform").displayed_series[1] == (4.0, 8.0)
    assert packets == []
    assert manager.active_page_id() == "mixed"


def test_control_stable_ids_follow_edits_and_reordering(qtbot):
    from qx_stc_isp.vofa.dashboard import DashboardCard, DashboardManager, DashboardPage
    page = DashboardPage("main", "页面", (DashboardCard("controls", "control", "读数", {
        "controls": [{"kind": "value", "channel": 2}, {"kind": "value", "channel": 7}],
        "control_ids": ["temperature", "speed"],
    }),))
    manager = DashboardManager()
    qtbot.addWidget(manager)
    manager.load_pages([page.to_document()])
    controls = manager.primary_widget("control")
    controls.move_control(1, 0)
    controls.update_control(0, title="转速")
    config = manager.pages()[0]["cards"][0]["config"]
    assert config["control_ids"] == ["speed", "temperature"]
    assert [item["channel"] for item in config["controls"]] == [7, 2]


def test_mixed_card_splitter_geometry_roundtrips(qtbot):
    from PySide6.QtWidgets import QSplitter
    from qx_stc_isp.vofa.dashboard import DashboardCard, DashboardManager, DashboardPage
    page = DashboardPage("main", "页面", (DashboardCard("plot", "waveform", "波形"), DashboardCard("stats", "analysis", "分析")))
    view = DashboardManager()
    qtbot.addWidget(view)
    view.resize(900, 900)
    view.load_pages([page.to_document()])
    view.show()
    qtbot.waitExposed(view)
    split = view.tabs.currentWidget().findChild(QSplitter)
    split.setSizes([500, 250])
    document = view.pages()[0]
    assert document["layout"]["sizes"] == split.sizes()
    assert DashboardPage.from_document(document).layout["sizes"] == split.sizes()


def test_reordering_copied_page_never_rebinds_legacy_widget(qtbot):
    from qx_stc_isp.vofa.controls import ControlWorkspace
    from qx_stc_isp.vofa.dashboard import DashboardManager
    legacy = ControlWorkspace()
    manager = DashboardManager(legacy_widgets={"control": legacy})
    qtbot.addWidget(manager)
    legacy.add_control("value", 4)
    copied = manager.copy_page("legacy-page-control")
    manager.move_page(copied, 0)
    original_host = next(manager.tabs.widget(index) for index in range(manager.tabs.count()) if manager.tabs.widget(index).property("dashboard_page_id") == "legacy-page-control")
    assert original_host.isAncestorOf(legacy)


def test_pausing_custom_plot_freezes_view_but_shared_snapshot_keeps_growing(qtbot):
    from qx_stc_isp.vofa.dashboard import DashboardCard, DashboardManager, DashboardPage
    view = DashboardManager()
    qtbot.addWidget(view)
    view.load_pages([DashboardPage("page", "页面", (DashboardCard("plot", "waveform", "图"),)).to_document()])
    view.set_sample_snapshot([(1.0,), (2.0,)])
    plot = view.primary_widget("waveform")
    plot.set_paused(True)
    view.set_sample_snapshot([(1.0,), (2.0,), (3.0,)])
    assert plot.displayed_sample_count == 2
    plot.set_paused(False)
    view.set_sample_snapshot([(1.0,), (2.0,), (3.0,)])
    assert plot.displayed_sample_count == 3


def test_control_edits_report_unsaved_changes_and_analysis_uses_configured_interval(qtbot):
    from qx_stc_isp.vofa.dashboard import DashboardCard, DashboardManager, DashboardPage
    view = DashboardManager()
    qtbot.addWidget(view)
    view.load_pages([DashboardPage("page", "页面", (DashboardCard("controls", "control", "控件"), DashboardCard("stats", "analysis", "分析"))).to_document()])
    changes = []
    view.configuration_changed.connect(lambda: changes.append(True))
    controls = view.primary_widget("control")
    controls.add_control("value", 3)
    assert changes
    view.set_sample_interval_ms(10)
    analysis = view.primary_widget("analysis")
    assert analysis._waveform.sample_interval_ms == 10


def test_image_settings_survive_restore_before_a_frame_arrives(qtbot):
    from qx_stc_isp.vofa.dashboard import DashboardCard, DashboardManager, DashboardPage
    view = DashboardManager()
    qtbot.addWidget(view)
    view.load_pages([DashboardPage("page", "页面", (DashboardCard("image", "image", "图像", {"rotation": 90}),)).to_document()])
    assert view.pages()[0]["cards"][0]["config"]["rotation"] == 90


@pytest.mark.parametrize("kind,config", [("image", {"effect": []}), ("control", {"control_ids": [None], "controls": [{"kind": "value"}]})])
def test_malformed_card_values_produce_readable_value_error(kind, config):
    from qx_stc_isp.vofa.dashboard import DashboardCard
    with pytest.raises(ValueError):
        DashboardCard("bad", kind, "无效", config)


def test_same_control_id_cannot_bind_two_separate_cards():
    from qx_stc_isp.vofa.dashboard import DashboardCard, DashboardCollection, DashboardPage
    a = DashboardCard("a", "control", "A", {"controls": [{"kind": "value"}], "control_ids": ["same"]})
    b = DashboardCard("b", "control", "B", {"controls": [{"kind": "value", "channel": 7}], "control_ids": ["same"]})
    with pytest.raises(ValueError, match="标识重复"):
        DashboardCollection((DashboardPage("p", "页面", (a, b)),))


def test_display_conversion_failure_does_not_break_snapshot_delivery(qtbot):
    from qx_stc_isp.vofa.dashboard import DashboardCard, DashboardManager, DashboardPage
    view = DashboardManager()
    qtbot.addWidget(view)
    view.load_pages([DashboardPage("page", "页面", (DashboardCard("plot", "waveform", "图"),)).to_document()])
    errors = []
    view.error.connect(errors.append)
    def invalid_display(values):
        raise ValueError("显示换算溢出")
    view.set_sample_snapshot([(1e308,)], transform=invalid_display)
    assert errors == ["显示换算失败：显示换算溢出；原始采样仍保留。"]
    view.set_sample_snapshot([(1e308,)], transform=lambda values: values)
    assert view.primary_widget("waveform").displayed_series == ((1e308,),)
