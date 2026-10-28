"""Read-only telemetry source boundary; no implicit connection or transmission."""

from __future__ import annotations

from typing import Protocol
import threading
from dataclasses import replace
from datetime import datetime, timezone

from .decoders import FeedDecoder
from .model import TelemetryEvent


class BatchAcknowledger:
    """Allow at most one unacknowledged GUI batch while acquisition runs."""

    def __init__(self, enabled: bool = False) -> None:
        if type(enabled) is not bool:
            raise ValueError("批次应答设置必须为布尔值")
        self.enabled = enabled
        self._ready = threading.Event()
        self._ready.set()

    def acknowledge(self) -> None:
        self._ready.set()

    def before_emit(self, stop: threading.Event) -> None:
        if not self.enabled:
            return
        while not stop.is_set() and not self._ready.wait(0.05):
            pass
        self._ready.clear()
        # Cancellation permits draining only the already-read bounded chunk.
        # It does not read another chunk or silently discard decoded samples.


class PassiveByteSource(Protocol):
    """A caller-opened source that provides only bounded reads and close."""

    def read_some(self, size: int, timeout: float) -> bytes: ...

    def close(self) -> None: ...


class MonitorSession:
    """Pull incoming bytes through a decoder, without any write capability."""

    def __init__(self, source: PassiveByteSource, decoder: FeedDecoder) -> None:
        self.source = source
        self.decoder = decoder
        self._closed = False

    @property
    def is_closed(self) -> bool:
        return self._closed

    def read_once(self, *, size: int = 4096, timeout: float = 0.1) -> list[TelemetryEvent]:
        if self._closed:
            raise RuntimeError("监视会话已关闭")
        if size <= 0 or timeout < 0:
            raise ValueError("读取长度或超时无效")
        chunk = self.source.read_some(size, timeout)
        received_at = datetime.now(timezone.utc)
        return [replace(event, received_at=received_at) for event in self.decoder.feed(chunk)]

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.source.close()

    def __enter__(self) -> MonitorSession:
        return self

    def __exit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        self.close()
