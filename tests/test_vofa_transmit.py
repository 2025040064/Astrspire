"""Offline serial TX tests. Fake transports are used on every run path."""

from __future__ import annotations

import threading
import time
from collections import deque

import pytest
from PySide6.QtCore import Qt

from qx_stc_isp.port_lease import PortLeaseManager
from qx_stc_isp.transport import TransportCancelled, TransportDisconnected, TransportError, TransportTimeout
from qx_stc_isp.vofa.serial_link import MAX_RX_BATCH_BYTES, SerialLinkWorker
from qx_stc_isp.vofa.transmit import TransmitError, encode_send_payload


class FakeTransport:
    def __init__(self, chunks=()) -> None:
        self.chunks = deque(chunks)
        self.calls = []
        self.on_read = None
        self.on_write = None
        self.open_error = None
        self.close_error = None
        self.reads = 0

    def open(self, baudrate) -> None:
        self.calls.append(("open", baudrate, threading.get_ident()))
        if self.open_error:
            raise self.open_error

    def read_some(self, size, timeout) -> bytes:
        assert size == 4096
        assert 0 < timeout <= 0.05
        self.calls.append(("read", threading.get_ident()))
        self.reads += 1
        if self.on_read:
            self.on_read(self.reads)
        return self.chunks.popleft() if self.chunks else b""

    def write(self, data, timeout=5) -> None:
        assert timeout > 0
        self.calls.append(("write", bytes(data), threading.get_ident()))
        if self.on_write:
            self.on_write(bytes(data))

    def cancel(self) -> None:
        self.calls.append(("cancel", threading.get_ident()))

    def close(self) -> None:
        self.calls.append(("close", threading.get_ident()))
        if self.close_error:
            raise self.close_error


def make_worker(fake=None, **kwargs):
    fake = fake or FakeTransport()
    leases = PortLeaseManager()
    worker = SerialLinkWorker("COM98", 115200, kwargs.pop("decoder_name", "FireWater"),
                              transport_factory=lambda _device: fake,
                              lease_manager=leases, **kwargs)
    signals = {name: [] for name in ("events", "raw", "status", "error", "transmitted", "finished")}
    worker.events.connect(signals["events"].append)
    worker.raw.connect(signals["raw"].append)
    worker.status.connect(lambda kind, text: signals["status"].append((kind, text)))
    worker.error.connect(signals["error"].append)
    worker.transmitted.connect(signals["transmitted"].append)
    worker.finished.connect(lambda: signals["finished"].append(True))
    return worker, fake, leases, signals


@pytest.mark.parametrize("mode,ending,text,expected", [
    ("text", "none", "你好", "你好".encode()),
    ("text", "LF", "AT", b"AT\n"),
    ("text", "CRLF", "AT", b"AT\r\n"),
    ("hex", "none", "01 0a FF", b"\x01\x0a\xff"),
    ("hex", "CRLF", "010AFF", b"\x01\x0a\xff\r\n"),
    ("hex", "LF", "01\n02\t03", b"\x01\x02\x03\n"),
])
def test_explicit_send_format(mode, ending, text, expected) -> None:
    assert encode_send_payload(text, mode=mode, line_ending=ending) == expected


@pytest.mark.parametrize("text", ["", " ", "0", "0 1", "ABC", "0x", "AA,ZZ", "GG", "01-02", "0x0xAA", "0x,AA"])
def test_malformed_hex_is_rejected(text) -> None:
    with pytest.raises(TransmitError):
        encode_send_payload(text, mode="hex")


def test_explicit_cr_tab_and_prefixed_hex():
    assert encode_send_payload("AT", line_ending="CR") == b"AT\r"
    assert encode_send_payload("A", line_ending="TAB", encoding="utf-16le") == b"A\x00\t\x00"
    assert encode_send_payload(r"0xAA,\x01:FE|02", mode="hex") == bytes.fromhex("AA 01 FE 02")
    with pytest.raises(TransmitError):
        encode_send_payload("AA ZZ 01", mode="hex")


def test_encoding_bounds_and_invalid_configuration() -> None:
    with pytest.raises(TransmitError):
        encode_send_payload("你好", max_bytes=5)
    with pytest.raises(TransmitError):
        encode_send_payload("1234", line_ending="LF", max_bytes=4)
    with pytest.raises(TransmitError):
        encode_send_payload("data", line_ending="invalid")
    with pytest.raises(TransmitError):
        encode_send_payload("data", mode="binary")
    with pytest.raises(TransmitError):
        encode_send_payload("", mode="text")
    assert encode_send_payload("", mode="text", line_ending="LF") == b"\n"


def test_connect_does_not_send_or_allow_prequeued_data() -> None:
    worker, fake, leases, signals = make_worker(FakeTransport([b"1,", b"2\n"]))
    assert fake.calls == []
    with pytest.raises(TransmitError, match="先连接"):
        worker.enqueue_send(b"must-not-auto-send")
    fake.on_read = lambda count: worker.stop() if count == 2 else None
    worker.run()
    assert not [call for call in fake.calls if call[0] == "write"]
    assert b"".join(signals["raw"]) == b"1,2\n"
    assert [event.samples for batch in signals["events"] for event in batch] == [(1.0, 2.0)]
    assert worker.statistics.completed_packets == 0
    assert signals["finished"] == [True]
    assert leases.owner_for("COM98") is None


def test_send_is_serialized_with_rx_and_stop_discards_queued_packets() -> None:
    worker, fake, leases, signals = make_worker(FakeTransport([b"1\n", b"2\n"]))

    def on_status(kind, _text):
        if kind == "connected":
            assert worker.enqueue_send(b"A") == 1
            assert worker.enqueue_send(b"BC") == 2
            assert worker.enqueue_send(b"DEF") == 3

    worker.status.connect(on_status)
    fake.on_read = lambda count: worker.stop() if count == 2 else None
    worker.run()
    writes = [call for call in fake.calls if call[0] == "write"]
    assert [call[1] for call in writes] == [b"A", b"BC"]
    assert all(call[-1] == threading.get_ident() for call in fake.calls if call[0] in {"read", "write"})
    assert signals["transmitted"] == [b"A", b"BC"]
    stats = worker.statistics
    assert (stats.accepted_packets, stats.accepted_bytes) == (3, 6)
    assert (stats.completed_packets, stats.completed_bytes) == (2, 3)
    assert (stats.discarded_packets, stats.discarded_bytes) == (1, 3)
    assert (stats.failed_packets, stats.unconfirmed_bytes, stats.queued_packets) == (0, 0, 0)
    assert leases.owner_for("COM98") is None


def test_queue_packet_byte_limits_and_clear_pending() -> None:
    worker, fake, _leases, _signals = make_worker(max_queue_packets=2, max_queue_bytes=5, max_packet_bytes=5)

    def on_status(kind, _text):
        if kind != "connected":
            return
        worker.enqueue_send(b"123")
        with pytest.raises(TransmitError, match="队列已满"):
            worker.enqueue_send(b"456")
        worker.enqueue_send(b"45")
        with pytest.raises(TransmitError, match="队列已满"):
            worker.enqueue_send(b"x")
        assert worker.clear_pending() == 2
        assert worker.statistics.queued_bytes == 0
        worker.enqueue_send(b"ok")

    worker.status.connect(on_status)
    fake.on_read = lambda _count: worker.stop()
    worker.run()
    assert [call[1] for call in fake.calls if call[0] == "write"] == [b"ok"]
    assert worker.statistics.discarded_packets == 2
    with pytest.raises(TransmitError):
        worker.enqueue_send(b"after-stop")


@pytest.mark.parametrize("error", [TransportTimeout("write timeout"), TransportDisconnected("unplugged")])
def test_failed_write_is_not_retried_or_counted_complete(error) -> None:
    worker, fake, leases, signals = make_worker()
    worker.status.connect(lambda kind, _text: (worker.enqueue_send(b"first"), worker.enqueue_send(b"second")) if kind == "connected" else None)

    def fail(_data):
        raise error

    fake.on_write = fail
    worker.run()
    assert [call[1] for call in fake.calls if call[0] == "write"] == [b"first"]
    assert not signals["transmitted"]
    assert signals["error"]
    stats = worker.statistics
    assert (stats.completed_packets, stats.failed_packets, stats.unconfirmed_bytes) == (0, 1, 5)
    assert (stats.discarded_packets, stats.discarded_bytes) == (1, 6)
    assert leases.owner_for("COM98") is None
    assert fake.calls[-1][0] == "close"


def test_open_and_close_failures_still_release_lease() -> None:
    fake = FakeTransport()
    fake.open_error = TransportError("busy")
    fake.close_error = TransportError("already disconnected")
    worker, fake, leases, signals = make_worker(fake)
    worker.run()
    assert len(signals["error"]) == 2
    assert signals["finished"] == [True]
    assert leases.owner_for("COM98") is None
    assert not worker.connected


def test_stop_before_run_opens_nothing_and_worker_is_single_use() -> None:
    worker, fake, leases, signals = make_worker()
    worker.stop()
    worker.run()
    assert fake.calls == []
    assert leases.owner_for("COM98") is None
    assert signals["finished"] == [True]
    with pytest.raises(RuntimeError):
        worker.run()


def test_port_busy_does_not_construct_transport() -> None:
    worker, fake, leases, signals = make_worker()
    with leases.acquire("COM98", owner="isp"):
        worker.run()
        assert fake.calls == []
        assert "占用" in signals["error"][0]
        assert leases.owner_for("COM98") == "isp"


def test_raw_bytes_are_preserved_even_for_decoder_errors() -> None:
    fake = FakeTransport([b"nan,1\n"])
    worker, fake, _leases, signals = make_worker(fake)
    fake.on_read = lambda _count: worker.stop()
    worker.run()
    assert b"".join(signals["raw"]) == b"nan,1\n"
    assert signals["events"][0][0].error is not None


def test_rx_batches_are_bounded_and_overload_stops_cleanly(monkeypatch) -> None:
    # Keep the batch clock fixed to exercise the byte bound without delays.
    monkeypatch.setattr("qx_stc_isp.vofa.serial_link.time.monotonic", lambda: 1.0)
    chunk = b"x" * 4096
    worker, fake, leases, signals = make_worker(FakeTransport([chunk] * 17), decoder_name="RawData")
    worker.run()
    assert signals["error"]
    assert all(len(batch) <= MAX_RX_BATCH_BYTES for batch in signals["raw"])
    assert b"".join(signals["raw"]) == chunk * 17
    assert all(len(batch) <= 256 for batch in signals["events"])
    assert leases.owner_for("COM98") is None


def test_overload_decodes_the_final_already_received_chunk_before_stopping(monkeypatch):
    monkeypatch.setattr("qx_stc_isp.vofa.serial_link.time.monotonic", lambda: 1.0)
    chunk = b"x" * 4096
    worker, _fake, _leases, signals = make_worker(FakeTransport([chunk] * 17), decoder_name="RawData")
    worker.run()
    parsed_raw = b"".join(event.raw for batch in signals["events"] for event in batch)
    assert parsed_raw == chunk * 17
    assert signals["error"]


def test_gui_thread_enqueue_never_performs_the_serial_write(qapp) -> None:
    ready = threading.Event()
    request_queued = threading.Event()
    worker, fake, leases, _signals = make_worker()

    def on_read(_count):
        ready.set()
        assert request_queued.wait(1)

    fake.on_read = on_read
    fake.on_write = lambda _data: worker.stop()
    thread = threading.Thread(target=worker.run)
    thread.start()
    try:
        assert ready.wait(1)
        assert worker.connected
        worker.enqueue_send(b"from-gui")
        request_queued.set()
        thread.join(2)
        assert not thread.is_alive()
        writes = [call for call in fake.calls if call[0] == "write"]
        assert len(writes) == 1
        assert writes[0][-1] == thread.ident
        assert writes[0][-1] != threading.get_ident()
        assert worker.statistics.completed_bytes == len(b"from-gui")
        assert leases.owner_for("COM98") is None
    finally:
        request_queued.set()
        worker.stop()
        thread.join(2)
        qapp.processEvents()


def test_dense_rx_keeps_every_sample_in_bounded_batches() -> None:
    payload = b"1\n" * 700
    worker, fake, _leases, signals = make_worker(FakeTransport([payload]))
    fake.on_read = lambda _count: worker.stop()
    worker.run()
    decoded = [event for batch in signals["events"] for event in batch]
    assert len(decoded) == 700
    assert max(map(len, signals["events"])) <= 256
    assert all(event.samples == (1.0,) for event in decoded)
    assert all(event.received_at is not None for event in decoded)


def test_gui_backpressure_waits_for_ack_and_cancel_releases_wait():
    worker, fake, _leases, _signals = make_worker(FakeTransport([b"1\n" * 700]), await_ack=True)
    first = threading.Event()
    batches = []
    worker.events.connect(lambda batch: (batches.append(batch), first.set()), Qt.DirectConnection)
    thread = threading.Thread(target=worker.run)
    thread.start()
    try:
        assert first.wait(1)
        time.sleep(0.03)
        assert len(batches) == 1
        assert len(batches[0]) == 256
        worker.ack_batch()
        deadline = time.monotonic() + 1
        while len(batches) < 2 and time.monotonic() < deadline:
            time.sleep(0.005)
        assert len(batches) == 2
        worker.stop()
        thread.join(1)
        assert not thread.is_alive()
        assert sum(map(len, batches)) == 700
    finally:
        worker.stop()
        thread.join(1)


def test_disconnect_cancels_inflight_write_and_discards_pending(qapp) -> None:
    ready = threading.Event()
    queued = threading.Event()
    writing = threading.Event()
    cancelled = threading.Event()
    worker, fake, leases, signals = make_worker()

    def on_read(_count):
        ready.set()
        assert queued.wait(1)

    def blocking_write(_data):
        writing.set()
        assert cancelled.wait(1)
        raise TransportCancelled("cancelled")

    fake.on_read = on_read
    fake.on_write = blocking_write
    fake.cancel = cancelled.set
    thread = threading.Thread(target=worker.run)
    thread.start()
    try:
        assert ready.wait(1)
        worker.enqueue_send(b"first")
        worker.enqueue_send(b"second")
        queued.set()
        assert writing.wait(1)
        worker.stop()
        thread.join(2)
        assert not thread.is_alive()
        qapp.processEvents()
        assert worker.statistics.completed_packets == 0
        assert worker.statistics.failed_packets == 1
        assert worker.statistics.unconfirmed_bytes == 5
        assert worker.statistics.discarded_packets == 1
        assert worker.statistics.discarded_bytes == 6
        assert not signals["transmitted"]
        assert not signals["error"]
        assert leases.owner_for("COM98") is None
    finally:
        queued.set()
        cancelled.set()
        worker.stop()
        thread.join(2)
        qapp.processEvents()


def test_decoder_is_a_snapshot_for_the_connection() -> None:
    worker, fake, _leases, signals = make_worker(FakeTransport([b"1,", b"2\n"]))

    def on_read(count):
        worker.decoder_name = "RawData"
        if count == 2:
            worker.stop()

    fake.on_read = on_read
    worker.run()
    assert [event.samples for batch in signals["events"] for event in batch] == [(1.0, 2.0)]
