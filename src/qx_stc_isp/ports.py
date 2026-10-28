"""Read-only serial-port discovery.  Discovery never opens a device."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Iterable

from serial.tools import list_ports


_WCH_VID = 0x1A86
_CH340_PIDS = frozenset({0x7523, 0x5523, 0x55D4})
_CHIP_HINT = re.compile(r"\bCH34[01][A-Z0-9]*\b", re.IGNORECASE)
_COM_NUMBER = re.compile(r"^COM(\d+)$", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class PortDescriptor:
    device: str
    description: str
    vid: int | None
    pid: int | None
    serial_number: str | None
    hwid: str
    is_ch340_candidate: bool


def _port_sort_key(port: PortDescriptor) -> tuple[int, int, str]:
    match = _COM_NUMBER.fullmatch(port.device)
    if match:
        return (0, int(match.group(1)), port.device.casefold())
    return (1, 0, port.device.casefold())


def enumerate_ports(
    port_source: Callable[[], Iterable[object]] | None = None,
) -> list[PortDescriptor]:
    """List all ports without choosing one or touching its file handle.

    The candidate flag is a UI hint, not proof of the connected MCU or its ISP
    protocol.  ``port_source`` allows tests to provide in-memory port metadata.
    """

    source = port_source or list_ports.comports
    discovered: list[PortDescriptor] = []
    for info in source():
        device = str(getattr(info, "device", "") or "").strip()
        if not device:
            continue
        description = str(getattr(info, "description", "") or "")
        hwid = str(getattr(info, "hwid", "") or "")
        serial_number = getattr(info, "serial_number", None)
        vid = getattr(info, "vid", None)
        pid = getattr(info, "pid", None)
        if not isinstance(vid, int):
            vid = None
        if not isinstance(pid, int):
            pid = None
        chip_text = " ".join(
            str(value or "")
            for value in (
                description,
                hwid,
                getattr(info, "manufacturer", None),
                getattr(info, "product", None),
            )
        )
        candidate = (vid == _WCH_VID and pid in _CH340_PIDS) or bool(
            _CHIP_HINT.search(chip_text)
        )
        discovered.append(
            PortDescriptor(
                device=device,
                description=description,
                vid=vid,
                pid=pid,
                serial_number=str(serial_number) if serial_number else None,
                hwid=hwid,
                is_ch340_candidate=candidate,
            )
        )
    return sorted(discovered, key=_port_sort_key)
