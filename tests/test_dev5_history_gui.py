from datetime import datetime, timedelta, timezone

from qx_stc_isp.vofa.capture_stream import StreamRecorder


def test_history_registration_is_async_inert_and_start_has_explicit_seek(qtbot, tmp_path):
    from qx_stc_isp.vofa.history_panel import HistoryDialog
    path = tmp_path / "capture.jsonl"
    stamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with StreamRecorder(path, connection_id="source-a", generation=4) as sink:
        sink.record("TX", b"must not send", at=stamp)
        sink.record("RX", b"1\n", at=stamp + timedelta(seconds=10))
    starts = []
    dialog = HistoryDialog(start_callback=lambda *args: starts.append(args),
                           index_directory=tmp_path / "derived")
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.register_path(path)
    qtbot.waitUntil(lambda: len(dialog.catalog.entries()) == 1, timeout=3000)
    assert starts == []
    assert dialog.table.rowCount() == 1
    assert "source-a" in dialog.table.item(0, 0).text()
    assert list((tmp_path / "derived").glob("*.json"))
    dialog.table.selectRow(0)
    dialog.position.setValue(3)
    dialog.speed.setValue(2)
    dialog.loop.setChecked(True)
    dialog.start_selected()
    assert starts == [(str(path.resolve()), 2.0, True, 3.0)]
    assert "TX" in dialog.notice.text()
    assert dialog.close()
