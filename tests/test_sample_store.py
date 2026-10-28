"""Shared raw-history seam; no transports or GUI side channels."""

from datetime import datetime, timedelta, timezone

from qx_stc_isp.vofa.model import TelemetryEvent
from qx_stc_isp.vofa.sample_store import SampleStore


def test_full_history_snapshot_remains_raw_and_independent_after_display_conversion():
    store = SampleStore(capacity=100_000)
    stamp = datetime(2026, 10, 23, tzinfo=timezone.utc)
    for index in range(100_000):
        store.append_event(TelemetryEvent(raw=b"", samples=(float(index),), received_at=stamp))
    before = store.snapshot()
    converted = tuple(frame.values[0] * 2 + 7 for frame in before)
    assert len(converted) == 100_000
    assert (converted[0], converted[-1]) == (7.0, 200005.0)
    assert before[0].values == (0.0,)
    assert before[-1].values == (99999.0,)
    store.append_event(TelemetryEvent(raw=b"", samples=(100000.0,), received_at=stamp))
    assert before[0].sequence == 1
    assert store.snapshot()[0].sequence == 2
    stats = store.statistics
    assert (stats.parsed, stats.retained, stats.evicted) == (100001, 100000, 1)


def test_file_timeline_names_and_counters_are_separate_from_arrival_time():
    store = SampleStore(capacity=2)
    arrival = datetime(2026, 10, 23, tzinfo=timezone.utc)
    original = arrival - timedelta(days=2)
    frame = store.append_event(TelemetryEvent(raw=b"1\n", samples=(1.0,), received_at=arrival,
                                             source_timestamp=original, names=("voltage",)))
    assert frame.received_at == arrival
    assert frame.timestamp == original
    assert frame.names == ("voltage",)
    store.record_received_bytes(27)
    store.record_render_omitted(1)
    assert store.append_event(TelemetryEvent(raw=b"bad", error="invalid")) is None
    assert store.append_event(TelemetryEvent(raw=b"text", text="hello")) is None
    stats = store.statistics
    assert (stats.received_bytes, stats.parsed, stats.retained, stats.invalid, stats.render_omitted) == (27, 1, 1, 1, 1)


def test_resize_evicts_oldest_and_clear_resets_session_counters():
    store = SampleStore(capacity=3)
    for value in (1.0, 2.0, 3.0):
        store.append_event(TelemetryEvent(raw=b"", samples=(value,)))
    store.set_capacity(1)
    assert [frame.values for frame in store.snapshot()] == [(3.0,)]
    assert store.statistics.evicted == 2
    store.clear()
    assert store.snapshot() == ()
    assert store.statistics.parsed == store.statistics.evicted == 0


def test_raw_history_keeps_256_channels_even_when_views_only_show_16():
    store = SampleStore()
    frame = store.append_event(TelemetryEvent(raw=b"", samples=tuple(float(i) for i in range(256))))
    assert frame is not None
    assert len(frame.values) == 256
    assert frame.values[-1] == 255.0
    assert frame.names[-1] == "CH256"
    assert store.append_event(TelemetryEvent(raw=b"", samples=(0.0,) * 257)) is None
    assert store.statistics.invalid == 1
