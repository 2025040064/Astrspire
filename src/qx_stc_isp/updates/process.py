"""Named instance mutex and waiting logic used by the update assistant."""

from __future__ import annotations

import ctypes
import sys
import time

MUTEX_NAME = r"Local\Astrspire.Instance.v1"
_SYNCHRONIZE = 0x00100000
_ERROR_FILE_NOT_FOUND = 2


def _kernel32():
    return ctypes.windll.kernel32


class InstanceMutex:
    """Held for the whole application lifetime; closes only at process exit."""

    def __init__(self, name: str = MUTEX_NAME) -> None:
        self._handle = None
        if sys.platform != "win32":
            return
        handle = _kernel32().CreateMutexW(None, False, name)
        if not handle:
            raise OSError(ctypes.get_last_error(), "无法创建应用实例互斥量")
        self._handle = handle

    def close(self) -> None:
        if self._handle:
            _kernel32().CloseHandle(self._handle)
            self._handle = None

    def __enter__(self) -> "InstanceMutex":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()


def mutex_exists(name: str = MUTEX_NAME) -> bool:
    if sys.platform != "win32":
        return False
    kernel32 = _kernel32()
    handle = kernel32.OpenMutexW(_SYNCHRONIZE, False, name)
    if handle:
        kernel32.CloseHandle(handle)
        return True
    return kernel32.GetLastError() != _ERROR_FILE_NOT_FOUND


def wait_mutex_gone(
    name: str = MUTEX_NAME,
    *,
    timeout: float = 120.0,
    poll: float = 0.25,
    sleep=time.sleep,
) -> bool:
    """Wait until no instance holds the mutex. Never terminates another process."""

    if sys.platform != "win32":
        return True
    deadline = time.monotonic() + timeout
    while mutex_exists(name):
        if time.monotonic() >= deadline:
            return False
        sleep(poll)
    return True
