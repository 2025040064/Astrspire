import struct

import pytest

from qx_stc_isp.vofa.swd import SwdCaptureConfig, SwdManifest, SwdRingReader, ProbeLease


def manifest(**changes):
    return SwdManifest(**{"target": "stm32f1x", "address": 0x20000100,
                          "firmware_sha256": "a" * 64, **changes})


class Memory:
    def __init__(self):
        self.data = bytearray(8192)
        self.base = 0x20000000
        self.requests = []
        self.publish(2)

    def publish(self, sequence, boot=7):
        struct.pack_into("<8I", self.data, 0x100, 0x41535744, 1, 0x410, 64, 16, 10, boot, sequence)
        for seq in range(max(1, sequence - 63), sequence + 1):
            struct.pack_into("<III16f", self.data, 0x120 + seq % 64 * 76,
                             seq * 2, seq, seq * 10, float(seq), float(seq * 10), *([0.5] * 14))

    def read(self, address, size):
        self.requests.append((address, size))
        return bytes(self.data[address - self.base:address - self.base + size])


def test_capture_config_and_manifest_are_pure_and_bounded():
    assert SwdCaptureConfig("stlink", "TEST123", "stm32f1x", "not-read.json").poll_ms == 50
    with pytest.raises(ValueError):
        manifest(address=0x08000000)


def test_ring_reads_exact_values_and_no_duplicates():
    memory = Memory()
    reader = SwdRingReader(manifest())
    assert [event.samples[:3] for event in reader.poll(memory)] == [(1.0, 10.0, 0.5), (2.0, 20.0, 0.5)]
    assert reader.poll(memory) == []
    memory.publish(3)
    assert reader.poll(memory)[0].samples[0] == 3


def test_partial_frame_is_rejected_without_advancing_cursor():
    memory = Memory()
    struct.pack_into("<I", memory.data, 0x120 + 76, 3)
    reader = SwdRingReader(manifest())
    assert reader.poll(memory) == []
    assert reader.statistics.retries == 3
    memory.publish(2)
    assert len(reader.poll(memory)) == 2


def test_overrun_counts_missing_device_frames():
    memory = Memory()
    reader = SwdRingReader(manifest())
    reader.poll(memory)
    memory.publish(70)
    assert len(reader.poll(memory)) == 64
    assert reader.statistics.sequence_gaps == 4


def test_reset_or_wrong_chip_stops_capture():
    memory = Memory()
    reader = SwdRingReader(manifest())
    reader.poll(memory)
    memory.publish(3, boot=8)
    with pytest.raises(ValueError, match="重启"):
        reader.poll(memory)
    struct.pack_into("<I", memory.data, 0x108, 0x468)
    with pytest.raises(ValueError):
        SwdRingReader(manifest()).poll(memory)


def test_probe_lease_does_not_allow_a_second_owner():
    with ProbeLease("TEST123"):
        with pytest.raises(ValueError):
            with ProbeLease("test123"):
                pass
    with ProbeLease("TEST123"):
        pass


def test_worker_supports_gui_batch_ack_and_rejects_every_send():
    from qx_stc_isp.vofa.swd_link import SwdLinkWorker
    from qx_stc_isp.vofa.transmit import TransmitError
    worker = SwdLinkWorker(SwdCaptureConfig("stlink", "TEST123", "stm32f1x", "not-read.json"), await_ack=True)
    assert worker.can_send is False
    worker.ack_batch()
    with pytest.raises(TransmitError):
        worker.enqueue_send(b"hello")


def test_start_baseline_excludes_pre_connection_samples():
    memory = Memory()
    reader = SwdRingReader(manifest())
    reader.begin(memory)
    assert reader.poll(memory) == []
    memory.publish(3)
    assert reader.poll(memory)[0].samples[0] == 3


def test_full_ring_batch_has_bounded_debugger_round_trips():
    memory = Memory()
    memory.publish(64)
    reader = SwdRingReader(manifest())
    assert len(reader.poll(memory)) == 64
    assert len(memory.requests) <= 6


def test_receive_worker_runs_multiple_batches_and_releases_probe_on_cancel(tmp_path):
    from dataclasses import asdict
    import json
    import threading
    from PySide6.QtCore import Qt
    from qx_stc_isp.vofa.swd_link import SwdLinkWorker
    path = tmp_path / "capture.json"
    path.write_text(json.dumps(asdict(manifest())), encoding="utf-8")

    class Backend(Memory):
        closed = False
        def open(self):
            pass
        def ensure_running(self):
            value = struct.unpack_from("<I", self.data, 0x11C)[0]
            self.publish(value + 1)
        def cancel_io(self):
            pass
        def close(self):
            self.closed = True

    backend = Backend()
    config = SwdCaptureConfig("stlink", "WORKER123", "stm32f1x", str(path), 10)
    worker = SwdLinkWorker(config, memory_factory=lambda *_a: backend, await_ack=True)
    received, errors = [], []
    def consume(events):
        received.extend(events)
        worker.ack_batch()
        if len(received) >= 3:
            worker.stop()
    worker.events.connect(consume, Qt.DirectConnection)
    worker.error.connect(errors.append, Qt.DirectConnection)
    thread = threading.Thread(target=worker.run)
    thread.start()
    thread.join(2)
    if thread.is_alive():
        worker.stop()
        thread.join(2)
    assert not thread.is_alive() and backend.closed
    assert [event.samples[0] for event in received] == [3., 4., 5.]
    assert not errors
    with ProbeLease("WORKER123"):
        pass


def test_cancelled_backend_does_not_start_a_helper(tmp_path, monkeypatch):
    from qx_stc_isp.vofa.swd_link import OpenOcdMemory
    memory = OpenOcdMemory(SwdCaptureConfig("stlink", "TEST123", "stm32f1x", "missing.json"),
                           manifest(), runtime_dir=tmp_path)
    def forbidden(*_args, **_kwargs):
        pytest.fail("Cancellation must prevent helper launch")
    monkeypatch.setattr("subprocess.Popen", forbidden)
    memory.cancel_io()
    with pytest.raises(OSError, match="取消"):
        memory.open()
    memory.close()
