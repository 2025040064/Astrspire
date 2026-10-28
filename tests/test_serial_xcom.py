"""XCOM-inspired serial interaction, exclusively with fake devices."""

import threading

import pytest
import serial

from qx_stc_isp.transport import PortBusyError, PySerialTransport
from qx_stc_isp.vofa import serial_link


class DeferredSerial:
    def __init__(self, **kwargs):
        self.arguments = kwargs
        self.is_open = kwargs["port"] is not None
        self.dtr = self.rts = True
        self.operations = []
        self.open_error = None

    def open(self):
        self.operations.append(("open", self.port, self.dtr, self.rts))
        if self.open_error:
            raise self.open_error
        self.is_open = True

    def close(self):
        self.operations.append(("close",))
        self.is_open = False


@pytest.fixture(autouse=True)
def no_hardware(monkeypatch):
    monkeypatch.setattr("serial.Serial", lambda *a, **k: pytest.fail("No physical serial access"))


@pytest.mark.parametrize("dtr,rts", [(False, False), (True, False), (False, True), (True, True)])
def test_line_levels_are_set_before_open(dtr, rts):
    devices = []

    def factory(**kwargs):
        handle = DeferredSerial(**kwargs)
        devices.append(handle)
        return handle

    transport = PySerialTransport("COM_FAKE", dtr=dtr, rts=rts, serial_factory=factory)
    assert devices == []
    transport.open(115200)
    handle = devices[0]
    assert handle.arguments["port"] is None
    assert handle.operations == [("open", "COM_FAKE", dtr, rts)]
    assert transport.is_open
    transport.close()


def test_stc_transport_default_open_remains_unchanged():
    devices = []

    def factory(**kwargs):
        handle = DeferredSerial(**kwargs)
        devices.append(handle)
        return handle

    transport = PySerialTransport("COM_FAKE", serial_factory=factory)
    transport.open(2400)
    assert devices[0].arguments["port"] == "COM_FAKE"
    assert devices[0].dtr and devices[0].rts
    assert devices[0].operations == []
    transport.close()


def test_failed_deferred_open_closes_handle_and_reports_busy():
    handle = DeferredSerial(port=None)
    handle.open_error = serial.SerialException("access is denied")
    transport = PySerialTransport("COM_FAKE", dtr=False, rts=False,
                                  serial_factory=lambda **kwargs: handle)
    with pytest.raises(PortBusyError):
        transport.open(115200)
    assert handle.operations[-1] == ("close",)
    assert not transport.is_open


@pytest.mark.parametrize("field", ["dtr", "rts"])
@pytest.mark.parametrize("value", [0, 1, "false"])
def test_invalid_line_setting_is_rejected_without_open(field, value):
    with pytest.raises(ValueError):
        PySerialTransport("COM_FAKE", **{field: value})


def test_monitor_factory_uses_explicit_line_settings(monkeypatch):
    calls = []
    monkeypatch.setattr(serial_link, "PySerialTransport", lambda device, **kw: calls.append((device, kw)))
    for dtr, rts in ((False, False), (True, False), (False, True)):
        worker = serial_link.SerialLinkWorker("COM_FAKE", 115200, "RawData", dtr=dtr, rts=rts)
        worker._transport_factory("COM_FAKE")
        assert calls[-1] == ("COM_FAKE", {"dtr": dtr, "rts": rts})
    assert worker.statistics.accepted_bytes == 0


def test_cancel_and_close_do_not_race_driver_handle():
    entered, release, closing, closed = (threading.Event() for _ in range(4))
    failures = []

    class Handle(DeferredSerial):
        def cancel_read(self):
            entered.set()
            assert release.wait(2)
            if not self.is_open:
                raise TypeError("byref() argument must be a ctypes instance, not NoneType")
            self.operations.append(("cancel_read",))

        def cancel_write(self):
            assert self.is_open
            self.operations.append(("cancel_write",))

    handle = Handle(port="COM_FAKE")
    transport = PySerialTransport("COM_FAKE", serial_factory=lambda **kw: handle)
    transport.open(115200)

    def cancel():
        try:
            transport.cancel()
        except Exception as exc:
            failures.append(exc)

    def close():
        closing.set()
        transport.close()
        closed.set()

    cancel_thread = threading.Thread(target=cancel)
    close_thread = threading.Thread(target=close)
    cancel_thread.start()
    assert entered.wait(2)
    try:
        close_thread.start()
        assert closing.wait(2)
        closed_during_cancel = closed.wait(0.05)
    finally:
        release.set()
        cancel_thread.join(2)
        close_thread.join(2)
    assert not cancel_thread.is_alive() and not close_thread.is_alive()
    assert not closed_during_cancel
    assert failures == []
    assert handle.operations == [("cancel_read",), ("cancel_write",), ("close",)]
