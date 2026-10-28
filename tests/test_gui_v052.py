"""Layout-only acceptance; hardware is prohibited."""
import pytest
from PySide6.QtCore import QPoint
from qx_stc_isp.gui import MainWindow

@pytest.fixture
def window(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr('qx_stc_isp.gui.enumerate_ports', lambda: [])
    def forbidden(*args, **kwargs):
        pytest.fail('layout tests must not open hardware')
    monkeypatch.setattr('serial.Serial', forbidden)
    result = MainWindow(log_dir=tmp_path)
    qtbot.addWidget(result)
    result.tabs.setCurrentIndex(1)
    result.show()
    yield result
    result.close()

def test_large_canvas_and_tools_below_it(window, qtbot):
    window.resize(1920,1080)
    qtbot.wait(20)
    upper, lower = window.monitor_plot_splitter.sizes()
    assert 0.72 <= upper / (upper+lower) <= 0.78
    assert window.wave_pause_button.mapToGlobal(QPoint()).y() >= window.waveform.mapToGlobal(QPoint(0,window.waveform.height())).y()

def test_toolbar_wraps_without_hiding_primary_controls(window, qtbot):
    for width, height in [(1920,1080),(1280,800)]:
        window.resize(width,height)
        qtbot.wait(20)
        actions_y = window.wave_pause_button.mapToGlobal(QPoint()).y()
        settings_y = window.sample_interval_spin.mapToGlobal(QPoint()).y()
        assert abs(actions_y-settings_y) < 12 if width == 1920 else settings_y > actions_y
        for control in (window.wave_pause_button,window.plot_settings_button,window.plot_export_button,window.sample_interval_spin,window.wave_buffer_spin):
            assert control.isVisible()
            assert control.width() >= control.minimumSizeHint().width()

def test_restore_default_changes_old_split_but_schema_stays_six(window, qtbot, tmp_path):
    from qx_stc_isp.vofa.workspace import WorkspaceSettings,save_workspace,load_workspace
    import json
    old = WorkspaceSettings(plot_splitter_sizes=(420,260))
    window.load_monitor_workspace(settings=old)
    window.resize(1920,1080)
    qtbot.wait(20)
    before=window.monitor_plot_splitter.sizes()
    assert before[0]/sum(before) < .7
    window.monitor_plot_splitter.restore_default_sizes()
    after=window.monitor_plot_splitter.sizes()
    assert .72 <= after[0]/sum(after) <= .78
    path=tmp_path/'workspace.json'
    save_workspace(path,old)
    assert load_workspace(path).plot_splitter_sizes == (420,260)
    assert json.loads(path.read_text(encoding='utf-8'))['schema_version'] == 8

def test_minimum_window_primary_controls_stay_inside_center(window, qtbot):
    window.resize(window.minimumSize())
    qtbot.wait(20)
    parent = window.monitor_plot_splitter
    for control in (window.wave_pause_button,window.sample_interval_spin,window.wave_buffer_spin,window.send_button,window.send_text_edit):
        point=control.mapTo(parent,QPoint())
        assert point.x() >= 0 and point.y() >= 0
        assert point.x()+control.width() <= parent.width()
        assert point.y()+control.height() <= parent.height()

def test_resize_keeps_selected_target_and_single_pause_signal(window, qtbot):
    window.dashboard.add_page('Two')
    a=window.dashboard.add_card('waveform','A')
    b=window.dashboard.add_card('waveform','B')
    window.dashboard.select_card(b)
    changed=[]
    window.dashboard.card_widget(b).paused_changed.connect(changed.append)
    for size in [(1920,1080),(1280,800),(1920,1080)]:
        window.resize(*size)
        qtbot.wait(5)
    window.wave_pause_button.click()
    assert changed == [True]
    assert not window.dashboard.card_widget(a).paused

def test_periodic_settings_remain_accessible_without_starting_send(window, qtbot):
    from PySide6.QtWidgets import QMenu
    menu=next(m for m in window.findChildren(QMenu) if window.show_rx_action in m.actions())
    menu.popup(window.mapToGlobal(QPoint(50,50)))
    qtbot.wait(5)
    assert window.periodic_send_check.isVisible()
    assert window.send_interval_spin.isVisible()
    assert not window.periodic_send_check.isChecked()
    assert not window._periodic_timer.isActive()
    menu.close()

def test_right_time_label_is_not_clipped(window):
    # The backend owns tick placement now; preserve the visible-axis contract,
    # not the removed QPainter implementation's drawText call signature.
    window.waveform.replace_samples([(float(i),) for i in range(400)],['CH1'])
    window.waveform.grab()
    view = window.waveform.plot_widget
    axis = view.getAxis('bottom')
    assert axis.tickStrings([399.], 1., 100.) == ['0.399s']
    rect = view.viewportTransform().mapRect(axis.sceneBoundingRect())
    assert rect.right() <= view.viewport().width() + 1

def test_task_error_is_visible_in_compact_terminal(window):
    window._send_job.error.emit('Offline test error')
    assert window.send_job_progress.isVisible()
    assert window.send_job_progress.format() == '发送失败'
    assert window.monitor_status_label.text() == 'Offline test error'
