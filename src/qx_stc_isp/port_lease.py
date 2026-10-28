"""In-process ownership of a COM port across monitor and ISP workspaces.

This does not replace operating-system serial exclusivity. A caller must keep
its lease until its transport has stopped and closed. Merely constructing a
manager or acquiring a lease never opens a hardware device.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass


class PortLeaseError(RuntimeError):
    """A workspace already owns the selected serial device."""


def _port_key(device: str) -> str:
    if not isinstance(device, str) or not device.strip():
        raise ValueError("串口名称不能为空")
    return device.strip().casefold()


@dataclass
class PortLease:
    """Single-use release handle returned by :meth:`PortLeaseManager.acquire`."""

    manager: PortLeaseManager
    device: str
    owner: str
    _token: object
    _released: bool = False

    def release(self) -> None:
        if not self._released:
            self.manager._release(self.device, self._token)
            self._released = True

    def __enter__(self) -> PortLease:
        return self

    def __exit__(self, *_args: object) -> None:
        self.release()


class PortLeaseManager:
    """Reject a second owner of one port, including case-only COM aliases."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._owners: dict[str, tuple[str, object]] = {}

    def acquire(self, device: str, *, owner: str) -> PortLease:
        key = _port_key(device)
        if not isinstance(owner, str) or not owner.strip():
            raise ValueError("占用者名称不能为空")
        with self._lock:
            if key in self._owners:
                existing = self._owners[key][0]
                raise PortLeaseError(f"串口 {device} 已由 {existing} 占用")
            token = object()
            self._owners[key] = (owner, token)
        return PortLease(self, device, owner, token)

    def owner_for(self, device: str) -> str | None:
        with self._lock:
            item = self._owners.get(_port_key(device))
            return None if item is None else item[0]

    def _release(self, device: str, token: object) -> None:
        key = _port_key(device)
        with self._lock:
            current = self._owners.get(key)
            if current is not None and current[1] is token:
                del self._owners[key]


PORT_LEASES = PortLeaseManager()
