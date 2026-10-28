"""v0.5.1 offline regressions at store, dashboard and file boundaries."""
import pytest
from qx_stc_isp.vofa.sample_store import SampleStore
from qx_stc_isp.vofa.model import TelemetryEvent

@pytest.fixture(autouse=True)
def no_hardware(monkeypatch):
    monkeypatch.setattr('qx_stc_isp.gui.enumerate_ports', lambda: [])
    def forbidden(*_args, **_kwargs):
        pytest.fail('v0.5.1 offline regressions must not open hardware')
    monkeypatch.setattr('serial.Serial', forbidden)

def test_default_names_start_at_one_but_explicit_names_are_preserved():
    store = SampleStore()
    assert store.append_event(TelemetryEvent(raw=b'', samples=(1., 2.))).names == ('CH1', 'CH2')
    assert store.append_event(TelemetryEvent(raw=b'', samples=(1.,), names=('CH0',))).names == ('CH0',)
    assert store.append_event(TelemetryEvent(raw=b'', samples=(1., 2.), names=('',))).names == ('CH1', 'CH2')

def test_wave_cards_keep_settings_and_interval_when_page_is_renamed(qtbot):
    from qx_stc_isp.vofa.dashboard import DashboardManager
    manager = DashboardManager()
    qtbot.addWidget(manager)
    page = manager.add_page('Page')
    card = manager.add_card('waveform', 'Plot')
    manager.set_sample_interval_ms(2.5)
    view = manager.card_widget(card)
    view.set_grid_visible(False)
    view.set_line_width(4)
    view.set_channel_visible(0, False)
    manager.rename_page(page, 'Renamed')
    restored = manager.card_widget(card)
    assert restored.sample_interval_ms == 2.5
    assert restored.line_width == 4
    assert not restored.grid_visible and not restored.visible_channels[0]

def test_image_card_save_button_opens_its_own_save_dialog(qtbot, monkeypatch):
    from qx_stc_isp.vofa.dashboard import DashboardManager
    from PySide6.QtGui import QImage
    from PySide6.QtCore import QByteArray, QBuffer, QIODevice
    manager = DashboardManager()
    qtbot.addWidget(manager)
    manager.add_page('Images')
    card = manager.add_card('image')
    viewer = manager.card_widget(card).viewer
    data = QByteArray(); buffer = QBuffer(data); buffer.open(QIODevice.WriteOnly)
    image = QImage(2, 2, QImage.Format_RGB32); image.fill(0xff123456); image.save(buffer, 'PNG')
    viewer.set_encoded_image(bytes(data))
    calls = []
    monkeypatch.setattr('PySide6.QtWidgets.QFileDialog.getSaveFileName', lambda *_a, **_k: calls.append(True) or ('', ''))
    viewer.save_button.click()
    assert calls == [True]

def test_toolbar_pause_targets_selected_wave_card(qtbot, tmp_path, monkeypatch):
    from qx_stc_isp.gui import MainWindow
    monkeypatch.setattr('qx_stc_isp.gui.enumerate_ports', lambda: [])
    window = MainWindow(log_dir=tmp_path)
    qtbot.addWidget(window)
    window.dashboard.add_page('Two plots')
    a = window.dashboard.add_card('waveform', 'A')
    b = window.dashboard.add_card('waveform', 'B')
    window.dashboard.select_card(b)
    window.wave_pause_button.click()
    assert window.dashboard.card_widget(b).paused
    assert not window.dashboard.card_widget(a).paused
    window.close()

def test_native_unknown_fields_are_not_reported_as_complete(tmp_path):
    import runpy, json
    from pathlib import Path
    from qx_stc_isp.vofa.native_project import inspect_native_context
    runpy.run_path(str(Path(__file__).with_name('test_native_project.py')))['write_context'](tmp_path)
    path = tmp_path / 'vofa+.config.json'
    document = json.loads(path.read_text(encoding='utf-8'))
    document['ctx']['unknown_setting'] = {'enabled': True}
    path.write_text(json.dumps(document), encoding='utf-8')
    report = inspect_native_context(tmp_path)
    assert not report.complete and report.can_apply_partial
    assert any('unknown_setting' in issue.path for issue in report.unsupported)

def test_workspace_new_card_format_uses_schema_six(tmp_path):
    import json
    from qx_stc_isp.vofa.workspace import WorkspaceSettings, save_workspace, load_workspace
    path = tmp_path / 'workspace.json'
    save_workspace(path, WorkspaceSettings())
    assert json.loads(path.read_text(encoding='utf-8'))['schema_version'] == 8
    assert load_workspace(path) == WorkspaceSettings()

def test_csv_default_names_match_display_without_changing_indices(tmp_path):
    import csv
    from qx_stc_isp.vofa.recorder import CsvRecorder
    path = tmp_path / 'names.csv'
    with CsvRecorder(path) as recorder:
        recorder.record(TelemetryEvent(raw=b'', samples=(1., 2.)))
    rows = list(csv.DictReader(path.open(encoding='utf-8-sig')))
    assert [(r['channel_index'], r['channel_name']) for r in rows] == [('0','CH1'), ('1','CH2')]

def test_legacy_wave_card_restores_card_config_not_stale_alias(qtbot):
    from qx_stc_isp.vofa.dashboard import DashboardManager, default_dashboard_pages
    from qx_stc_isp.vofa.waveform import WaveformWidget
    wave = WaveformWidget()
    manager = DashboardManager(legacy_widgets={'waveform':wave})
    qtbot.addWidget(manager)
    docs = [p.to_document() for p in default_dashboard_pages()]
    docs[0]['cards'][0]['config'].update(grid=False, line_width=4)
    manager.load_pages(docs)
    assert not wave.grid_visible and wave.line_width == 4

def test_analysis_uses_stable_wave_source_and_copy_remaps_it(qtbot, monkeypatch):
    from qx_stc_isp.vofa.dashboard import DashboardManager
    from PySide6.QtWidgets import QMessageBox
    manager = DashboardManager()
    qtbot.addWidget(manager)
    page = manager.add_page('Mixed')
    wave_id = manager.add_card('waveform', 'A')
    analysis_id = manager.add_card('analysis', 'FFT')
    wave = manager.card_widget(wave_id)
    analysis = manager.card_widget(analysis_id)
    assert analysis.source_selector.currentData() == wave_id
    assert analysis._waveform is wave
    wave.set_paused(True)
    assert analysis._waveform.paused
    copied = manager.copy_page(page)
    cards = next(p['cards'] for p in manager.pages() if p['id'] == copied)
    assert cards[1]['config']['source_waveform_id'] == cards[0]['id'] != wave_id
    monkeypatch.setattr(QMessageBox,'question',lambda *_a,**_k: QMessageBox.Yes)
    manager.remove_card(cards[0]['id'])
    analysis = manager.card_widget(cards[1]['id'])
    assert analysis.source_selector.currentData() == cards[0]['id']
    assert analysis._waveform.displayed_snapshot() == ()

def test_image_cards_export_their_own_original_bytes(qtbot, tmp_path, monkeypatch):
    from qx_stc_isp.vofa.dashboard import DashboardManager
    from PySide6.QtGui import QImage
    from PySide6.QtCore import QByteArray, QBuffer, QIODevice
    manager = DashboardManager()
    qtbot.addWidget(manager)
    manager.add_page('Images')
    ids = [manager.add_card('image', str(i)) for i in range(2)]
    for i, card_id in enumerate(ids):
        viewer = manager.card_widget(card_id).viewer
        data = QByteArray(); buffer = QBuffer(data); buffer.open(QIODevice.WriteOnly)
        image = QImage(2,2,QImage.Format_RGB32); image.fill(0xff123456 + i); image.save(buffer,'PNG')
        payload = bytes(data)
        viewer.set_encoded_image(payload)
        path = tmp_path / f'{i}.png'
        monkeypatch.setattr('PySide6.QtWidgets.QFileDialog.getSaveFileName',lambda *_a, p=path, **_k:(str(p),''))
        viewer.save_button.click()
        assert path.read_bytes() == payload

def test_schema_five_remains_readable_with_old_card_configuration(tmp_path):
    import json
    from qx_stc_isp.vofa.workspace import WorkspaceSettings, save_workspace, load_workspace
    path = tmp_path / 'old.json'
    save_workspace(path, WorkspaceSettings())
    doc = json.loads(path.read_text(encoding='utf-8'))
    doc['schema_version'] = 5
    doc.pop('swd')
    for key in ("tx_encoding", "serial_flow_control", "connection_presets", "serial_tools"):
        doc.pop(key)
    doc['plot_grid'] = False
    doc['plot_line_width'] = 3.5
    doc['pages'][0]['cards'][0]['config'] = {'mode':'xy','xy_channels':[2,3]}
    path.write_text(json.dumps(doc),encoding='utf-8')
    restored = load_workspace(path).pages[0]['cards'][0]['config']
    assert restored['xy_channels'] == [2,3]
    assert restored['grid'] is False and restored['line_width'] == 3.5

def test_deleting_selected_card_does_not_silently_target_another(qtbot, monkeypatch):
    from qx_stc_isp.vofa.dashboard import DashboardManager
    from PySide6.QtWidgets import QMessageBox
    manager = DashboardManager(); qtbot.addWidget(manager)
    manager.add_page('Two')
    first = manager.add_card('waveform','A')
    second = manager.add_card('waveform','B')
    manager.select_card(second)
    manager.move_card(second, -1)
    assert manager.active_waveform() is manager.card_widget(second)
    monkeypatch.setattr(QMessageBox,'question',lambda *_a,**_k: QMessageBox.Yes)
    manager.remove_card(second)
    assert manager.active_waveform() is None
    manager.select_card(first)
    assert manager.active_waveform() is manager.card_widget(first)

def test_control_editor_updates_own_card_and_keeps_id(qtbot, monkeypatch):
    from qx_stc_isp.vofa.dashboard import DashboardManager
    from qx_stc_isp.vofa.controls import ControlSpec
    from PySide6.QtWidgets import QDialog, QMenu
    from PySide6.QtCore import QPoint
    manager = DashboardManager(); qtbot.addWidget(manager)
    manager.add_page('Controls')
    a = manager.add_card('control','A'); b = manager.add_card('control','B')
    workspace = manager.card_widget(a)
    workspace.add_control('gauge',0,'Old')
    manager.pages()  # assign persistent ID before edit
    old_id = workspace.controls[0].dashboard_control_id
    class Dialog:
        def __init__(self, **kwargs):
            self.result_spec = ControlSpec('gauge',0,'Edited',settings={'minimum':-50,'maximum':50})
        def exec(self): return QDialog.Accepted
    monkeypatch.setattr('qx_stc_isp.vofa.control_editor.ControlEditorDialog', Dialog)
    def menu_factory(parent):
        menu = QMenu(parent)
        menu.exec = lambda *_a: menu.actions()[0].trigger()
        return menu
    monkeypatch.setattr('qx_stc_isp.vofa.dashboard.QMenu', menu_factory)
    workspace.controls[0].customContextMenuRequested.emit(QPoint())
    assert workspace.controls[0].spec.settings['minimum'] == -50
    assert workspace.controls[0].dashboard_control_id == old_id
    assert not manager.card_widget(b).controls

def test_toolbar_buffer_and_keyboard_pause_follow_selected_card(qtbot,tmp_path,monkeypatch):
    from qx_stc_isp.gui import MainWindow
    monkeypatch.setattr('qx_stc_isp.gui.enumerate_ports', lambda: [])
    window = MainWindow(log_dir=tmp_path); qtbot.addWidget(window)
    window.dashboard.add_page('Two')
    a = window.dashboard.add_card('waveform','A')
    b = window.dashboard.add_card('waveform','B')
    window.dashboard.select_card(b)
    old = window.dashboard.card_widget(a).max_samples
    window.wave_buffer_spin.setValue(250)
    assert window.dashboard.card_widget(b).max_samples == 250
    assert window.dashboard.card_widget(a).max_samples == old
    window.dashboard.card_widget(b).set_paused(True)
    assert window.wave_pause_button.isChecked()
    window.close()

def test_recreating_windows_does_not_accumulate_application_filters(qtbot,tmp_path):
    from qx_stc_isp.gui import MainWindow
    first = MainWindow(log_dir=tmp_path); qtbot.addWidget(first)
    second = MainWindow(log_dir=tmp_path); qtbot.addWidget(second)
    assert first._click_effects is second._click_effects
    first.close(); second.close()

def test_analysis_reference_to_non_wave_card_is_unbound(qtbot):
    from qx_stc_isp.vofa.dashboard import DashboardManager
    manager = DashboardManager(); qtbot.addWidget(manager)
    manager.add_page('Mixed')
    controls = manager.add_card('control','Controls')
    analysis = manager.add_card('analysis','FFT', {'source_waveform_id':controls})
    view = manager.card_widget(analysis)
    assert view._waveform.displayed_snapshot() == ()

@pytest.mark.parametrize('field', ['xoffset', 'future_field'])
def test_native_unmapped_channel_fields_and_known_ignored_metadata(tmp_path, field):
    import runpy, json
    from pathlib import Path
    from qx_stc_isp.vofa.native_project import inspect_native_context
    runpy.run_path(str(Path(__file__).with_name('test_native_project.py')))['write_context'](tmp_path)
    path = tmp_path / 'vofa+.config.json'
    document = json.loads(path.read_text(encoding='utf-8'))
    channels = document['ctx']['wave_view']['ctx']['settingsPanel']['ctx']['.']['settings_ctx']
    channels[0][field] = 2
    path.write_text(json.dumps(document), encoding='utf-8')
    report = inspect_native_context(tmp_path)
    assert not report.complete and report.can_apply_partial
    assert any(issue.path.endswith(field) for issue in report.unsupported)
    assert any(issue.path.endswith('list_size') for issue in report.ignored)

def test_plot_export_uses_selected_card(qtbot,tmp_path,monkeypatch):
    from qx_stc_isp.gui import MainWindow
    window = MainWindow(log_dir=tmp_path); qtbot.addWidget(window)
    window.dashboard.add_page('Export')
    a = window.dashboard.add_card('waveform','A')
    b = window.dashboard.add_card('waveform','B')
    calls = []
    monkeypatch.setattr(window.dashboard.card_widget(a),'export_png',lambda path: calls.append('A'))
    monkeypatch.setattr(window.dashboard.card_widget(b),'export_png',lambda path: calls.append('B'))
    monkeypatch.setattr('qx_stc_isp.gui.QFileDialog.getSaveFileName',lambda *_a,**_k:(str(tmp_path/'wave.png'),''))
    window.dashboard.select_card(b)
    window.export_plot_image()
    assert calls == ['B']
    window.close()
