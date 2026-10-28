"""Loopback-only tests: network workers never open serial or external hosts."""

from __future__ import annotations

import errno
import socket
import threading
import time

import pytest
from PySide6.QtCore import Qt

from qx_stc_isp.vofa.network_link import NetworkConfig, NetworkLinkWorker
from qx_stc_isp.vofa.transmit import TransmitError


@pytest.fixture(autouse=True)
def no_serial(monkeypatch, qapp):
    def forbidden(*_args, **_kwargs):
        pytest.fail("network tests must never open a serial device")
    monkeypatch.setattr("serial.Serial", forbidden)


def wait_for(predicate, timeout=2):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            pytest.fail("loopback condition timed out")
        time.sleep(0.005)


def make_worker(config, decoder="FireWater", **kwargs):
    worker = NetworkLinkWorker(config, decoder, **kwargs)
    signals = {key: [] for key in ("events", "raw", "status", "transmitted", "error", "finished")}
    for key in ("events", "raw", "transmitted", "error"):
        getattr(worker, key).connect(signals[key].append, Qt.DirectConnection)
    worker.status.connect(lambda kind, text: signals["status"].append((kind, text)), Qt.DirectConnection)
    worker.finished.connect(lambda: signals["finished"].append(True), Qt.DirectConnection)
    return worker, signals


@pytest.fixture
def workers():
    active = []
    def start(worker):
        thread = threading.Thread(target=worker.run, daemon=True)
        active.append((worker, thread))
        thread.start()
        return thread
    yield start
    for worker, thread in active:
        worker.stop()
        thread.join(2)
        assert not thread.is_alive(), "network worker did not stop within its bounded polling interval"


@pytest.fixture
def tcp_listener():
    handle = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    handle.bind(("127.0.0.1", 0))
    handle.listen(1)
    handle.settimeout(2)
    yield handle
    handle.close()


def received_samples(signals):
    return [event.samples for batch in signals["events"] for event in batch if event.samples is not None]


def receive_exact(handle, size):
    data = b""
    handle.settimeout(2)
    while len(data) < size:
        chunk = handle.recv(size - len(data))
        assert chunk
        data += chunk
    return data


@pytest.mark.parametrize("options", [
    {"kind": "unknown"}, {"kind": []}, {"host": "localhost"},
    {"host": "::1"}, {"host": "127.0.0.1 "}, {"host": ""}, {"host": None},
    {"host": "0.0.0.0"}, {"host": "224.1.2.3"}, {"host": "255.255.255.255"},
    {"port": True}, {"port": 0}, {"port": -1}, {"port": 65536},
    {"peer_host": "127.0.0.1"},
    {"kind": "udp", "peer_port": 1234},
    {"kind": "tcp_server", "peer_host": "127.0.0.1", "peer_port": 1234},
    {"kind": "udp", "peer_host": "localhost", "peer_port": 1234},
    {"kind": "udp", "peer_host": "127.0.0.1", "peer_port": 0},
])
def test_invalid_configurations_reject_before_network(options):
    values = {"kind": "tcp_client", "host": "127.0.0.1", "port": 1234, **options}
    with pytest.raises(ValueError):
        NetworkConfig(**values)


def test_bind_configuration_and_construction_do_not_create_sockets(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("construction must not create a socket or resolve DNS")
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    config = NetworkConfig("udp", "0.0.0.0", 0)
    worker = NetworkLinkWorker(config, "RawData")
    assert not worker.connected and not worker.can_send
    assert worker.bound_address is None
    assert worker.statistics.completed_bytes == 0


@pytest.mark.parametrize("options", [
    {"max_queue_packets": 0}, {"max_queue_bytes": True}, {"max_packet_bytes": -1},
    {"max_queue_bytes": 100, "max_packet_bytes": 101},
    {"read_timeout": 0}, {"read_timeout": 0.1}, {"read_timeout": float("nan")},
    {"connect_timeout": float("inf")}, {"write_timeout": False},
])
def test_worker_limits_are_validated(options):
    with pytest.raises(ValueError):
        NetworkLinkWorker(NetworkConfig("udp", "127.0.0.1", 0), "FireWater", **options)


def test_tcp_client_fragmented_rx_and_explicit_tx(workers, tcp_listener, monkeypatch):
    worker, signals = make_worker(NetworkConfig("tcp_client", "127.0.0.1", tcp_listener.getsockname()[1]))
    calls = []
    original = worker._send_payload
    def record_write(handle, data):
        calls.append(threading.get_ident())
        original(handle, data)
    monkeypatch.setattr(worker, "_send_payload", record_write)
    with pytest.raises(TransmitError):
        worker.enqueue_send(b"not-auto-queued")
    thread = workers(worker)
    peer, _ = tcp_listener.accept()
    try:
        wait_for(lambda: worker.connected)
        peer.settimeout(0.08)
        with pytest.raises(socket.timeout):
            peer.recv(1)
        peer.sendall(b"trace:1,")
        peer.sendall(b"2\n")
        wait_for(lambda: (1.0, 2.0) in received_samples(signals))
        assert worker.enqueue_send(b"A\x00B") == 1
        assert receive_exact(peer, 3) == b"A\x00B"
        wait_for(lambda: signals["transmitted"] == [b"A\x00B"])
        assert calls == [thread.ident]
        assert worker.statistics.completed_bytes == 3
        assert worker.remote_address == ("127.0.0.1", tcp_listener.getsockname()[1])
    finally:
        worker.stop()
        peer.close()
    thread.join(2)
    assert signals["finished"] == [True]
    assert not signals["error"]
    assert b"".join(signals["raw"]) == b"trace:1,2\n"


def test_tcp_server_waits_accepts_one_and_does_not_reconnect(workers):
    worker, signals = make_worker(NetworkConfig("tcp_server", "127.0.0.1", 0))
    thread = workers(worker)
    wait_for(lambda: worker.bound_address is not None)
    assert not worker.connected and not worker.can_send
    with pytest.raises(TransmitError):
        worker.enqueue_send(b"before-accept")
    endpoint = worker.bound_address
    peer = socket.create_connection(endpoint, timeout=2)
    try:
        wait_for(lambda: worker.connected)
        assert worker.can_send
        peer.sendall(b"3,4\n")
        wait_for(lambda: (3.0, 4.0) in received_samples(signals))
        worker.enqueue_send(b"reply")
        assert receive_exact(peer, 5) == b"reply"
    finally:
        peer.close()
    thread.join(2)
    assert not thread.is_alive()
    assert signals["finished"] == [True]
    assert any(kind == "listening" for kind, _ in signals["status"])
    assert any("对端已关闭" in message for message in signals["error"])
    assert not worker.connected
    with pytest.raises(OSError):
        socket.create_connection(endpoint, timeout=0.2)


def test_udp_receives_and_only_sends_to_explicit_peer(workers):
    peer = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    peer.bind(("127.0.0.1", 0))
    peer.settimeout(2)
    config = NetworkConfig("udp", "127.0.0.1", 0, "127.0.0.1", peer.getsockname()[1])
    worker, signals = make_worker(config)
    thread = workers(worker)
    try:
        wait_for(lambda: worker.connected)
        assert worker.can_send
        peer.sendto(b"5,6\n", worker.bound_address)
        wait_for(lambda: (5.0, 6.0) in received_samples(signals))
        worker.enqueue_send(b"manual")
        data, sender = peer.recvfrom(1024)
        assert data == b"manual" and sender == worker.bound_address
        wait_for(lambda: worker.statistics.completed_packets == 1)
    finally:
        worker.stop()
        peer.close()
    thread.join(2)
    assert not signals["error"]
    assert signals["transmitted"] == [b"manual"]


def test_udp_without_peer_never_learns_tx_target_and_keeps_datagrams_separate(workers):
    worker, signals = make_worker(NetworkConfig("udp", "127.0.0.1", 0))
    thread = workers(worker)
    first = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    second = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        wait_for(lambda: worker.connected)
        assert not worker.can_send
        first.sendto(b"1,", worker.bound_address)
        # Wait for the first datagram before a second peer to make ordering
        # independent of loopback UDP delivery scheduling.
        wait_for(lambda: b"1," in b"".join(signals["raw"]))
        second.sendto(b"2\n", worker.bound_address)
        wait_for(lambda: (2.0,) in received_samples(signals))
        assert (1.0, 2.0) not in received_samples(signals)
        assert worker.config.peer is None and worker.remote_address is None
        with pytest.raises(TransmitError, match="发送目标"):
            worker.enqueue_send(b"no implicit response")
        assert worker.statistics.accepted_bytes == 0
    finally:
        worker.stop()
        first.close()
        second.close()
    thread.join(2)
    assert not signals["transmitted"] and not signals["error"]


def test_empty_udp_datagram_does_not_disconnect(workers):
    worker, signals = make_worker(NetworkConfig("udp", "127.0.0.1", 0))
    workers(worker)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as peer:
        wait_for(lambda: worker.connected)
        peer.sendto(b"", worker.bound_address)
        peer.sendto(b"7\n", worker.bound_address)
        wait_for(lambda: (7.0,) in received_samples(signals))
        assert worker.connected


def test_queue_limits_discard_on_stop_without_sending(workers, tcp_listener):
    worker, signals = make_worker(
        NetworkConfig("tcp_client", "127.0.0.1", tcp_listener.getsockname()[1]),
        max_queue_packets=2, max_queue_bytes=3, max_packet_bytes=3,
    )
    def on_connected(kind, _message):
        if kind == "connected":
            assert worker.enqueue_send(b"A") == 1
            assert worker.enqueue_send(b"BC") == 2
            with pytest.raises(TransmitError, match="队列已满"):
                worker.enqueue_send(b"D")
            worker.stop()
    worker.status.connect(on_connected, Qt.DirectConnection)
    thread = workers(worker)
    peer, _ = tcp_listener.accept()
    peer.close()
    thread.join(2)
    stats = worker.statistics
    assert (stats.accepted_packets, stats.accepted_bytes) == (2, 3)
    assert (stats.completed_packets, stats.completed_bytes) == (0, 0)
    assert (stats.discarded_packets, stats.discarded_bytes) == (2, 3)
    assert not signals["transmitted"] and not signals["error"]
    with pytest.raises(TransmitError):
        worker.enqueue_send(b"after-stop")


def test_send_validation_does_not_accept_unconnected_or_empty_payloads():
    worker, _ = make_worker(NetworkConfig("udp", "127.0.0.1", 0))
    for value in ("not-bytes", bytearray(b"1"), None):
        with pytest.raises(TypeError):
            worker.enqueue_send(value)
    for value in (b"", b"X" * 16385, b"not-connected"):
        with pytest.raises(TransmitError):
            worker.enqueue_send(value)
    assert worker.statistics.accepted_packets == 0


def test_stop_before_run_creates_no_socket(monkeypatch):
    worker, signals = make_worker(NetworkConfig("udp", "127.0.0.1", 0))
    monkeypatch.setattr(socket, "socket", lambda *_a, **_k: pytest.fail("cancelled worker created a socket"))
    worker.stop()
    worker.run()
    assert signals["finished"] == [True]
    assert not signals["error"]


def test_stop_cancels_server_accept_promptly(workers):
    worker, signals = make_worker(NetworkConfig("tcp_server", "127.0.0.1", 0))
    thread = workers(worker)
    wait_for(lambda: worker.bound_address is not None)
    started = time.monotonic()
    worker.stop()
    thread.join(1)
    assert not thread.is_alive() and time.monotonic() - started < 1
    assert signals["finished"] == [True]
    assert not signals["error"]


def test_connection_refused_closes_without_reconnect():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reserved:
        reserved.bind(("127.0.0.1", 0))
        port = reserved.getsockname()[1]
    worker, signals = make_worker(NetworkConfig("tcp_client", "127.0.0.1", port), connect_timeout=0.2)
    worker.run()
    assert signals["finished"] == [True]
    assert len(signals["error"]) == 1
    assert not any(kind == "connected" for kind, _ in signals["status"])
    assert worker._socket is None


def test_connect_timeout_is_bounded_and_closes(monkeypatch):
    class PendingSocket:
        closed = False
        def setblocking(self, _value): pass
        def connect_ex(self, endpoint):
            assert endpoint == ("127.0.0.1", 1234)
            return errno.EINPROGRESS
        def shutdown(self, _how): pass
        def close(self): self.closed = True
    fake = PendingSocket()
    monkeypatch.setattr(socket, "socket", lambda *_a, **_k: fake)
    def pending_select(*_args):
        time.sleep(0.001)
        return [], [], []
    monkeypatch.setattr("qx_stc_isp.vofa.network_link.select.select", pending_select)
    worker, signals = make_worker(NetworkConfig("tcp_client", "127.0.0.1", 1234), connect_timeout=0.01)
    started = time.monotonic()
    worker.run()
    assert time.monotonic() - started < 0.5
    assert fake.closed
    assert any("连接超时" in message for message in signals["error"])
    assert signals["finished"] == [True]


def test_failed_write_is_unconfirmed_and_queued_message_is_not_retried(workers, tcp_listener, monkeypatch):
    worker, signals = make_worker(NetworkConfig("tcp_client", "127.0.0.1", tcp_listener.getsockname()[1]))
    calls = []
    def fail_write(_handle, data):
        calls.append(data)
        raise OSError("injected write failure")
    monkeypatch.setattr(worker, "_send_payload", fail_write)
    def on_connected(kind, _message):
        if kind == "connected":
            worker.enqueue_send(b"A")
            worker.enqueue_send(b"BC")
    worker.status.connect(on_connected, Qt.DirectConnection)
    thread = workers(worker)
    peer, _ = tcp_listener.accept()
    peer.close()
    thread.join(2)
    stats = worker.statistics
    assert calls == [b"A"]
    assert stats.failed_packets == 1 and stats.unconfirmed_bytes == 1
    assert stats.completed_packets == 0 and stats.in_flight_packets == 0
    assert stats.discarded_packets == 1 and stats.discarded_bytes == 2
    assert not signals["transmitted"]
    assert signals["error"] and signals["finished"] == [True]


def test_cancel_active_write_counts_unknown_bytes_without_success(workers, tcp_listener, monkeypatch):
    worker, signals = make_worker(NetworkConfig("tcp_client", "127.0.0.1", tcp_listener.getsockname()[1]))
    writing = threading.Event()
    def active_write(_handle, _data):
        writing.set()
        worker._stop.wait(1)
        worker._check_stop()
    monkeypatch.setattr(worker, "_send_payload", active_write)
    def on_connected(kind, _message):
        if kind == "connected":
            worker.enqueue_send(b"ABC")
            worker.enqueue_send(b"pending")
    worker.status.connect(on_connected, Qt.DirectConnection)
    thread = workers(worker)
    peer, _ = tcp_listener.accept()
    try:
        assert writing.wait(1)
        assert worker.statistics.in_flight_bytes == 3
        worker.stop()
        thread.join(2)
    finally:
        peer.close()
    stats = worker.statistics
    assert stats.failed_packets == 1 and stats.unconfirmed_bytes == 3
    assert stats.discarded_packets == 1 and stats.discarded_bytes == 7
    assert stats.completed_packets == 0 and not signals["transmitted"]
    assert not signals["error"]


def test_invalid_sample_retains_original_network_bytes(workers, tcp_listener):
    worker, signals = make_worker(NetworkConfig("tcp_client", "127.0.0.1", tcp_listener.getsockname()[1]))
    thread = workers(worker)
    peer, _ = tcp_listener.accept()
    try:
        peer.sendall(b"nan,3\n")
        wait_for(lambda: signals["events"])
        assert any(event.error for batch in signals["events"] for event in batch)
        assert not received_samples(signals)
        assert b"".join(signals["raw"]) == b"nan,3\n"
    finally:
        worker.stop()
        peer.close()
    thread.join(2)
    assert not signals["error"]


def test_decoded_batches_are_bounded_without_discarding_dense_samples(workers, tcp_listener):
    worker, signals = make_worker(NetworkConfig("tcp_client", "127.0.0.1", tcp_listener.getsockname()[1]))
    thread = workers(worker)
    peer, _ = tcp_listener.accept()
    try:
        peer.sendall(b"1\n" * 1000)
        wait_for(lambda: len(received_samples(signals)) == 1000)
        assert all(len(batch) <= 256 for batch in signals["events"])
        assert len(b"".join(signals["raw"])) == 2000
        assert all(event.received_at is not None for batch in signals["events"] for event in batch)
        assert not any(kind == "notice" and "舍弃" in text for kind, text in signals["status"])
    finally:
        worker.stop()
        peer.close()
    thread.join(2)


def test_raw_batch_overflow_is_bounded_failure(workers, tcp_listener, monkeypatch):
    monkeypatch.setattr("qx_stc_isp.vofa.network_link.RX_BATCH_SECONDS", 10)
    worker, signals = make_worker(NetworkConfig("tcp_client", "127.0.0.1", tcp_listener.getsockname()[1]), "RawData")
    thread = workers(worker)
    peer, _ = tcp_listener.accept()
    try:
        peer.sendall(b"X" * 100_000)
        thread.join(2)
        assert not thread.is_alive()
        assert any("批次上限" in text for text in signals["error"])
        assert all(len(raw) <= 64 * 1024 for raw in signals["raw"])
        assert len(b"".join(signals["raw"])) >= 64 * 1024
    finally:
        peer.close()


def test_busy_listening_port_is_reported_without_false_connection(tcp_listener):
    worker, signals = make_worker(NetworkConfig("tcp_server", "127.0.0.1", tcp_listener.getsockname()[1]))
    worker.run()
    assert signals["error"]
    assert not any(kind == "connected" for kind, _ in signals["status"])
    assert signals["finished"] == [True]
