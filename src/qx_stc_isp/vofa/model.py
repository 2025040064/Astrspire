"""Value objects shared by the passive telemetry parsers and UI."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class TelemetryEvent:
    """One parsed frame or raw chunk; `error` never means a hardware response."""

    raw: bytes
    samples: tuple[float, ...] | None = None
    text: str | None = None
    error: str | None = None
    image: bytes | None = None
    image_index: int | None = None
    image_metadata: tuple[int, int, int] | None = None
    # Software arrival time is not the target's sampling clock. File replay
    # carries its original timeline separately so export is lossless.
    received_at: datetime | None = None
    source_timestamp: datetime | None = None
    names: tuple[str, ...] = ()
    # Structured frame from the same decode, never reparsed from display text.
    frame: object | None = None


@dataclass(frozen=True, slots=True)
class SampleFrame:
    """One immutable raw sample; display transforms must never modify it."""

    sequence: int
    received_at: datetime
    source_timestamp: datetime | None
    values: tuple[float, ...]
    names: tuple[str, ...] = ()

    @property
    def timestamp(self) -> datetime:
        """Original file time when present, otherwise software receive time."""
        return self.source_timestamp or self.received_at
