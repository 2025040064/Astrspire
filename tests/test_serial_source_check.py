"""Hardware helper must match the assistant defaults, with no real device."""

import pytest

from tools import check_serial_source
from qx_stc_isp.transport import PySerialTransport


def test_readonly_check_uses_assistant_line_defaults(monkeypatch):
    calls = []
    monkeypatch.setattr(PySerialTransport, "__init__", lambda self, *a, **kw: calls.append((a, kw)))
    transport = check_serial_source.ReceiveOnlyTransport("COM_FAKE")
    assert calls == [(("COM_FAKE",), {"dtr": False, "rts": False})]
    with pytest.raises(RuntimeError, match="TX is forbidden"):
        transport.write(b"never send")
