from __future__ import annotations

from collections.abc import Callable

import pytest
import serial

from qx_stc_isp.transport import (
    PySerialTransport,
    SerialTransport,
    TransportCancelled,
    TransportDisconnected,
    TransportError,
)


class LegacyTransport(SerialTransport):
    """A transport written before the optional purge capability existed."""

    @property
    def is_open(self):
        return False

    def open(self, *args, **kwargs):
        raise AssertionError("must not open hardware")

    def reconfigure(self, *args, **kwargs):
        raise AssertionError("unexpected reconfigure")

    def read_exact(self, *args, **kwargs):
        raise AssertionError("unexpected read")

    def read_some(self, *args, **kwargs):
        raise AssertionError("unexpected read")

    def write(self, *args, **kwargs):
        raise AssertionError("unexpected write")

    def flush(self):
        raise AssertionError("purge must not flush")

    def cancel(self):
        pass

    def close(self):
        pass


class ResetSerial:
    """No OS device access: records every driver operation."""

    def __init__(self):
        self.is_open = True
        self.calls: list[str] = []
        self.input_action: Callable[[], None] | None = None
        self.output_action: Callable[[], None] | None = None
        self.close_error: Exception | None = None

    def reset_input_buffer(self):
        self.calls.append("reset_input")
        if self.input_action:
            self.input_action()

    def reset_output_buffer(self):
        self.calls.append("reset_output")
        if self.output_action:
            self.output_action()

    def flush(self):
        raise AssertionError("purge must discard, not flush")

    def close(self):
        self.calls.append("close")
        self.is_open = False
        if self.close_error:
            raise self.close_error


def opened_transport():
    handle = ResetSerial()
    transport = PySerialTransport("FAKE-ONLY", serial_factory=lambda **kwargs: handle)
    transport.open(9600)
    return transport, handle


def fail_with(error: Exception):
    def action():
        raise error

    return action


def test_optional_purge_does_not_break_legacy_transport():
    with pytest.raises(TransportError, match="不支持"):
        LegacyTransport().purge_buffers()


def test_purge_on_unopened_transport_never_constructs_serial():
    factory_calls = []

    def factory(**kwargs):
        factory_calls.append(kwargs)
        raise AssertionError("must not open hardware")

    transport = PySerialTransport("FAKE-ONLY", serial_factory=factory)
    with pytest.raises(TransportDisconnected, match="未连接"):
        transport.purge_buffers()
    assert factory_calls == []


def test_purge_on_cancelled_unopened_transport_is_cancelled():
    transport = PySerialTransport(
        "FAKE-ONLY", serial_factory=lambda **kwargs: pytest.fail("must not open")
    )
    transport.cancel()
    with pytest.raises(TransportCancelled):
        transport.purge_buffers()


def test_purge_discards_both_buffers_in_order_without_flushing():
    transport, handle = opened_transport()
    transport.purge_buffers()
    assert handle.calls == ["reset_input", "reset_output"]
    assert transport.is_open
    transport.purge_buffers()
    assert handle.calls == ["reset_input", "reset_output"] * 2
    transport.close()


def test_purge_cancelled_before_reset_does_not_touch_buffers():
    transport, handle = opened_transport()
    transport.cancel()
    with pytest.raises(TransportCancelled):
        transport.purge_buffers()
    assert handle.calls == []
    transport.close()


def test_purge_cancelled_after_input_stops_before_output_and_closes():
    transport, handle = opened_transport()
    handle.input_action = transport.cancel
    with pytest.raises(TransportCancelled):
        transport.purge_buffers()
    assert handle.calls == ["reset_input", "close"]
    assert not transport.is_open


def test_purge_cancelled_after_output_does_not_report_success():
    transport, handle = opened_transport()
    handle.output_action = transport.cancel
    with pytest.raises(TransportCancelled):
        transport.purge_buffers()
    assert handle.calls == ["reset_input", "reset_output", "close"]
    assert not transport.is_open


@pytest.mark.parametrize("reset_name", ["input", "output"])
@pytest.mark.parametrize("error_type", [OSError, serial.SerialException])
def test_reset_failure_closes_transport_and_keeps_original_cause(reset_name, error_type):
    transport, handle = opened_transport()
    original = error_type("device removed")
    setattr(handle, f"{reset_name}_action", fail_with(original))
    with pytest.raises(TransportDisconnected, match="丢弃收发缓冲区失败") as failure:
        transport.purge_buffers()
    assert failure.value.__cause__ is original
    expected = ["reset_input"] + (["reset_output"] if reset_name == "output" else [])
    assert handle.calls == expected + ["close"]
    assert not transport.is_open


@pytest.mark.parametrize("reset_name", ["input", "output"])
def test_disconnect_after_reset_is_detected_before_success(reset_name):
    transport, handle = opened_transport()
    setattr(handle, f"{reset_name}_action", lambda: setattr(handle, "is_open", False))
    with pytest.raises(TransportDisconnected):
        transport.purge_buffers()
    expected = ["reset_input"] + (["reset_output"] if reset_name == "output" else [])
    assert handle.calls == expected + ["close"]
    assert not transport.is_open


def test_reset_failure_and_close_failure_do_not_mask_original_error():
    transport, handle = opened_transport()
    original = serial.SerialException("reset failed")
    handle.input_action = fail_with(original)
    handle.close_error = OSError("close failed")
    with pytest.raises(TransportDisconnected, match="清理失败.*close failed") as failure:
        transport.purge_buffers()
    assert failure.value.__cause__ is original
    assert not transport.is_open
    transport.close()  # Detached handle is not reused even if driver close failed.
    assert handle.calls == ["reset_input", "close"]


def test_cancel_during_failed_reset_remains_cancellation_not_disconnect():
    transport, handle = opened_transport()

    def cancelled_reset():
        transport.cancel()
        raise OSError("driver aborted")

    handle.input_action = cancelled_reset
    with pytest.raises(TransportCancelled):
        transport.purge_buffers()
    assert handle.calls == ["reset_input", "close"]
    assert not transport.is_open


def test_purge_on_closed_handle_never_resets_buffers():
    transport, handle = opened_transport()
    handle.is_open = False
    with pytest.raises(TransportDisconnected):
        transport.purge_buffers()
    assert handle.calls == []
    transport.close()
