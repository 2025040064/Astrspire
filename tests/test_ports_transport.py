from __future__ import annotations

from types import SimpleNamespace

import pytest

from qx_stc_isp.ports import enumerate_ports
from qx_stc_isp.port_lease import PortLeaseError, PortLeaseManager
from qx_stc_isp.transport import (
    PortBusyError,
    PySerialTransport,
    TransportCancelled,
    TransportDisconnected,
    TransportTimeout,
)


def _port(device: str, description: str, vid=None, pid=None):
    return SimpleNamespace(
        device=device,
        description=description,
        vid=vid,
        pid=pid,
        hwid="",
        serial_number=None,
        manufacturer=None,
        product=None,
    )


def test_enumeration_marks_ch340_without_auto_selecting() -> None:
    ports = enumerate_ports(
        lambda: [
            _port("COM9", "USB-SERIAL CH340", 0x1A86, 0x7523),
            _port("COM4", "Bluetooth Serial"),
            _port("COM8", "USB Serial", 0x1A86, 0x5523),
            _port("COM3", "Bluetooth Serial"),
        ]
    )
    assert [item.device for item in ports] == ["COM3", "COM4", "COM8", "COM9"]
    assert [item.is_ch340_candidate for item in ports] == [False, False, True, True]
    assert ports[0].vid is None
    assert [item.device for item in ports if item.is_ch340_candidate] == ["COM8", "COM9"]


def test_description_only_ch340_is_candidate_but_bluetooth_is_not() -> None:
    ports = enumerate_ports(lambda: [_port("COM7", "CH341 USB device"), _port("COM5", "Bluetooth")])
    assert [item.is_ch340_candidate for item in ports] == [False, True]


class FakeSerial:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.is_open = True
        self.timeout = kwargs["timeout"]
        self.write_timeout = kwargs["write_timeout"]
        self.baudrate = kwargs["baudrate"]
        self.bytesize = kwargs["bytesize"]
        self.parity = kwargs["parity"]
        self.stopbits = kwargs["stopbits"]
        self.incoming = [b"\x01", b"\x02\x03"]
        self.written = bytearray()

    def read(self, amount):
        if self.incoming:
            return self.incoming.pop(0)
        return b""

    def write(self, data):
        self.written.extend(data[:2])
        return min(2, len(data))

    def flush(self):
        return None

    def close(self):
        self.is_open = False


def test_transport_short_reads_and_partial_writes() -> None:
    fake = FakeSerial(port="COM7", baudrate=9600, bytesize=8, parity="N", stopbits=1, timeout=0.05, write_timeout=5)
    transport = PySerialTransport("COM7", serial_factory=lambda **kwargs: fake)
    assert not transport.is_open
    transport.open(9600)
    assert transport.read_exact(3, timeout=0.2) == b"\x01\x02\x03"
    fake.incoming = [b"\xAA\xBB"]
    assert transport.read_some(8, timeout=0.2) == b"\xAA\xBB"
    assert transport.read_some(8, timeout=0) == b""
    transport.write(b"hello")
    assert fake.written == b"hello"
    transport.reconfigure(19200)
    assert fake.baudrate == 19200
    transport.close()
    assert not transport.is_open


def test_busy_timeout_disconnect_and_cancel() -> None:
    def busy(**kwargs):
        raise PermissionError("Access is denied")

    with pytest.raises(PortBusyError):
        PySerialTransport("COM7", serial_factory=busy).open(9600)

    fake = FakeSerial(port="COM7", baudrate=9600, bytesize=8, parity="N", stopbits=1, timeout=0.05, write_timeout=5)
    fake.incoming = [b"\x01"]
    transport = PySerialTransport("COM7", serial_factory=lambda **kwargs: fake)
    transport.open(9600)
    with pytest.raises(TransportTimeout) as timeout:
        transport.read_exact(2, timeout=0.01)
    assert timeout.value.partial == b"\x01"
    transport.cancel()
    with pytest.raises(TransportCancelled):
        transport.read_exact(1, timeout=0.01)
    with pytest.raises(TransportCancelled):
        transport.read_some(8, timeout=0.01)
    transport.close()

    fake2 = FakeSerial(port="COM8", baudrate=9600, bytesize=8, parity="N", stopbits=1, timeout=0.05, write_timeout=5)
    def unplugged(amount):
        fake2.is_open = False
        raise OSError("device removed")
    fake2.read = unplugged
    unplugged_transport = PySerialTransport("COM8", serial_factory=lambda **kwargs: fake2)
    unplugged_transport.open(9600)
    with pytest.raises(TransportDisconnected):
        unplugged_transport.read_exact(1, timeout=0.1)
    assert not unplugged_transport.is_open

    fake3 = FakeSerial(port="COM9", baudrate=9600, bytesize=8, parity="N", stopbits=1, timeout=0.05, write_timeout=5)
    def unplugged_monitor(amount):
        fake3.is_open = False
        raise OSError("device removed")
    fake3.read = unplugged_monitor
    monitor_transport = PySerialTransport("COM9", serial_factory=lambda **kwargs: fake3)
    monitor_transport.open(9600)
    with pytest.raises(TransportDisconnected):
        monitor_transport.read_some(8, timeout=0.1)
    assert not monitor_transport.is_open


def test_port_lease_is_exclusive_and_release_is_single_use() -> None:
    manager = PortLeaseManager()
    lease = manager.acquire("COM7", owner="vofa")
    assert manager.owner_for("com7") == "vofa"
    with pytest.raises(PortLeaseError, match="vofa"):
        manager.acquire("com7", owner="isp")
    lease.release()
    lease.release()
    assert manager.owner_for("COM7") is None
    with manager.acquire("COM7", owner="isp"):
        assert manager.owner_for("com7") == "isp"
    assert manager.owner_for("COM7") is None
