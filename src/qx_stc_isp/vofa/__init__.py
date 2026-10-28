"""Independent, offline VOFA-style telemetry primitives.

Importing this package never opens a serial port or starts a background task.
"""

from .decoders import FeedDecoder, create_decoder
from .model import TelemetryEvent
from .recorder import CsvRecorder
from .source import MonitorSession, PassiveByteSource
from .workspace import WorkspaceSettings, load_workspace, save_workspace

__all__ = [
    "CsvRecorder",
    "FeedDecoder",
    "MonitorSession",
    "PassiveByteSource",
    "TelemetryEvent",
    "WorkspaceSettings",
    "create_decoder",
    "load_workspace",
    "save_workspace",
]
