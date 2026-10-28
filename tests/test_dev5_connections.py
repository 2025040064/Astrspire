"""Session boundaries; importing and configuring never opens a device."""
from qx_stc_isp.vofa.model import TelemetryEvent


def test_sessions_isolate_samples_and_reject_previous_epoch_events():
    from qx_stc_isp.vofa.connection_sessions import ConnectionConfig, ConnectionSession, DataEvent
    a = ConnectionSession(ConnectionConfig("a", "A", "demo"))
    b = ConnectionSession(ConnectionConfig("b", "B", "demo"))
    epoch = a.begin()
    assert a.accept(DataEvent("a", epoch, 1, "events", [TelemetryEvent(raw=b"", samples=(12.5,))]))
    assert a.sample_store.snapshot()[0].values == (12.5,)
    assert b.sample_store.statistics.retained == 0
    a.end()
    a.begin()
    assert not a.accept(DataEvent("a", epoch, 2, "events", [TelemetryEvent(raw=b"", samples=(99.0,))]))
    assert a.sample_store.statistics.retained == 0


def test_four_connections_restore_inert_and_share_no_transports(qapp, monkeypatch):
    import pytest
    from qx_stc_isp.vofa.connection_sessions import ConnectionConfig, ConnectionManager, SendRequest
    monkeypatch.setattr("serial.Serial", lambda *a, **k: pytest.fail("configuration opened a port"))
    manager = ConnectionManager()
    for index in range(4):
        manager.add(ConnectionConfig(f"c{index}", f"设备{index}", "demo"))
    with pytest.raises(ValueError):
        manager.add(ConnectionConfig("fifth", "第五", "demo"))
    restored = ConnectionManager()
    restored.load_document(manager.document())
    assert len(restored.sessions) == 4 and all(not s.running for s in restored.sessions.values())
    with pytest.raises(ValueError):
        restored.sessions["c0"].send(SendRequest("c0", 0, "job", b"test"))


def test_demo_timer_runs_independently_and_stops_cleanly(qtbot):
    from qx_stc_isp.vofa.connection_sessions import ConnectionConfig, ConnectionManager
    manager = ConnectionManager()
    a = manager.add(ConnectionConfig("a", "A", "demo", options={"interval_ms": 2}))
    b = manager.add(ConnectionConfig("b", "B", "demo", options={"interval_ms": 3}))
    manager.start("a")
    manager.start("b")
    qtbot.waitUntil(lambda: a.sample_store.statistics.parsed > 3 and b.sample_store.statistics.parsed > 3)
    manager.stop("a")
    count = a.sample_store.statistics.parsed
    before_b = b.sample_store.statistics.parsed
    qtbot.waitUntil(lambda: b.sample_store.statistics.parsed > before_b + 3)
    assert a.sample_store.statistics.parsed == count
    manager.stop_all()
    assert manager.all_stopped()


def test_session_csv_recording_flushes_and_preserves_samples(tmp_path):
    from qx_stc_isp.vofa.connection_sessions import ConnectionConfig, ConnectionSession, DataEvent
    session = ConnectionSession(ConnectionConfig("a", "A", "demo"))
    generation = session.begin()
    session.start_recording(csv_path=tmp_path / "samples.csv")
    session.accept(DataEvent("a", generation, 1, "events", [TelemetryEvent(raw=b"", samples=(12.5,))]))
    session.end()
    assert all(recorder.wait_closed(2) for recorder in session.closing_recorders)
    assert "12.5" in (tmp_path / "samples.csv").read_text(encoding="utf-8-sig")
