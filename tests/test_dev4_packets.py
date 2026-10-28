from datetime import datetime, timezone
import threading
import pytest


def test_packet_search_crosses_reads_but_not_directions_and_eviction_counts():
    from qx_stc_isp.vofa.packets import PacketStore, search_packets
    store = PacketStore(max_records=3, max_bytes=20)
    store.append("RX", b"\xaa")
    store.append("TX", b"\x01")
    store.append("RX", b"\x02\xbb")
    result = search_packets(store.snapshot(), "AA 02", mode="hex", direction="RX")
    assert len(result) == 1 and result[0].offset == 0
    store.append("RX", b"\xcc", at=datetime(2026, 1, 1, tzinfo=timezone.utc))
    assert store.evicted_records == 1 and store.evicted_bytes == 1
    assert store.snapshot()[-1].offset == 3
    assert store.total_received == 4 and store.total_transmitted == 1


def test_recording_search_includes_data_outside_live_cache_and_never_sends(tmp_path):
    from qx_stc_isp.vofa.capture_stream import StreamRecorder
    from qx_stc_isp.vofa.packets import search_recording
    path = tmp_path / "old.jsonl"
    recorder = StreamRecorder(path)
    recorder.open()
    recorder.record("RX", b"first-aa")
    recorder.record("TX", b"not-rx")
    recorder.record("RX", b"bb-last")
    recorder.close()
    result = search_recording(path, "aa" + "bb", direction="RX")
    assert result.complete and len(result.matches) == 1
    assert result.matches[0].offset == 6
    assert len(result.records) == 2 and result.scanned_records == 3


def test_byte_regex_has_timeout_and_can_be_cancelled():
    from qx_stc_isp.vofa.packets import PacketStore, search_packets
    store = PacketStore()
    store.append("RX", b"ab123cd")
    matches = search_packets(store.snapshot(), r"\d+", mode="regex")
    assert matches[0].offset == 2 and matches[0].length == 3
    store.clear()
    store.append("RX", b"a" * 30000 + b"!")
    with pytest.raises(ValueError, match="超时"):
        search_packets(store.snapshot(), r"(a+)+$", mode="regex", regex_timeout=0.25)
    event = threading.Event()
    event.set()
    with pytest.raises(InterruptedError):
        search_packets(store.snapshot(), "a", cancelled=event)
