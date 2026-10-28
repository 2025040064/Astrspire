"""Independent acquisition sessions; configuration never opens a device."""
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
import re

from PySide6.QtCore import QObject, QThread, QTimer, Signal, Slot

from .image_store import ImageFrameStore
from .model import TelemetryEvent
from .packets import PacketStore
from .sample_store import SampleStore

MAX_CONNECTIONS = 4
SOURCES = frozenset({"external", "serial", "tcp_client", "tcp_server", "udp", "demo", "stream", "csv"})


@dataclass(frozen=True)
class ConnectionConfig:
    id: str
    name: str
    source: str
    decoder: str = "FireWater"
    options: dict = field(default_factory=dict)
    auto_reconnect: bool = False

    def __post_init__(self):
        if not isinstance(self.id, str) or re.fullmatch(r"[A-Za-z0-9_-]{1,100}", self.id) is None:
            raise ValueError("连接ID无效")
        if not isinstance(self.name, str) or not self.name.strip() or len(self.name) > 80:
            raise ValueError("连接名称需1–80字符")
        if self.source not in SOURCES or not isinstance(self.decoder, str) or not self.decoder:
            raise ValueError("数据源或解析器无效")
        if not isinstance(self.options, dict) or type(self.auto_reconnect) is not bool:
            raise ValueError("连接选项无效")
        detached = json.loads(json.dumps(self.options, allow_nan=False))
        if len(json.dumps(detached).encode()) > 65536:
            raise ValueError("连接配置超过64KiB")
        object.__setattr__(self, "options", detached)

    def document(self):
        return json.loads(json.dumps(asdict(self), allow_nan=False))

    @classmethod
    def from_document(cls, value):
        if not isinstance(value, dict) or set(value) - {"id", "name", "source", "decoder", "options", "auto_reconnect"}:
            raise ValueError("连接配置含未知字段")
        try:
            return cls(**value)
        except TypeError as exc:
            raise ValueError("连接字段不完整") from exc


@dataclass(frozen=True)
class DataEvent:
    connection_id: str
    generation: int
    sequence: int
    kind: str
    payload: object
    received_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class SendRequest:
    connection_id: str
    generation: int
    job_id: str
    payload: bytes
    origin: str = "manual"


class ConnectionSession:
    def __init__(self, config):
        self.config = ConnectionConfig.from_document(config.document())
        self.generation = 0
        self.sequence = 0
        self.running = False
        self.connected = False
        self.error = ""
        self.sample_store = SampleStore(capacity=100000)
        self.packet_store = PacketStore()
        self.image_store = ImageFrameStore()
        self.logs = deque(maxlen=500)
        self.rx_bytes = self.tx_bytes = 0
        self.active_job = None
        self.worker = self.thread = None
        self.external_sender = None
        self.recorders = {}
        self.closing_recorders = []
        self.names = ()

    @property
    def id(self):
        return self.config.id

    @property
    def can_send(self):
        return self.running and self.connected and self.config.source in {"external", "serial", "tcp_client", "tcp_server", "udp"}

    def begin(self):
        if self.running:
            raise ValueError("该连接仍在运行")
        self.generation += 1
        self.sequence = 0
        self.running = True
        self.connected = self.config.source == "demo"
        self.error = ""
        self.active_job = None
        self.sample_store.clear()
        self.packet_store.reset_session()
        self.image_store.clear()
        self.rx_bytes = self.tx_bytes = 0
        return self.generation

    def end(self):
        self.running = self.connected = False
        self.active_job = None
        self.stop_recording()

    def accept(self, event):
        if (event.connection_id != self.id or event.generation != self.generation
                or not self.running or event.sequence <= self.sequence):
            return False
        self.sequence = event.sequence
        if event.kind in ("rx", "tx"):
            raw = event.payload
            self.packet_store.append(event.kind.upper(), raw, at=event.received_at)
            if event.kind == "rx":
                self.rx_bytes += len(raw)
                self.sample_store.record_received_bytes(len(raw))
            else:
                self.tx_bytes += len(raw)
            self._record("raw", event.kind.upper(), raw, at=event.received_at)
        elif event.kind == "events":
            for item in event.payload:
                frame = self.sample_store.append_event(item)
                if frame is not None:
                    self.names = frame.names or self.names
                    self._record("csv", item)
                if item.image is not None:
                    self.image_store.put(item.image_index or 0, item.image, item.image_metadata)
                if item.error:
                    self.logs.append(item.error)
        elif event.kind == "status":
            kind, message = event.payload
            self.connected = kind == "connected" or (self.connected and kind not in {"disconnected", "error"})
            self.logs.append(message)
            if kind in {"error", "disconnected"}:
                self.active_job = None
                if kind == "error":
                    self.error = message
        return True

    def claim_job(self, job_id):
        if not self.can_send or self.active_job not in (None, job_id):
            raise ValueError("连接不可发送或正在执行其他任务")
        self.active_job = job_id

    def release_job(self, job_id):
        if self.active_job == job_id:
            self.active_job = None

    def send(self, request):
        if (request.connection_id != self.id or request.generation != self.generation
                or not self.can_send or self.active_job not in (None, request.job_id)):
            raise ValueError("发送目标失效、只读或被其他任务占用")
        if not isinstance(request.payload, bytes) or not 1 <= len(request.payload) <= 16384:
            raise ValueError("发送字节需1–16384")
        if self.external_sender is not None:
            return bool(self.external_sender(request.payload))
        if self.worker is None:
            raise ValueError("传输尚未就绪")
        self.worker.enqueue_send(request.payload)
        return True

    def start_recording(self, *, raw_path=None, csv_path=None):
        from .recorder import AsyncRecorder
        from .rotating_capture import RotatingStreamRecorder
        from .recorder import CsvRecorder
        if self.recorders or not self.running:
            raise ValueError("记录已启动或连接未运行")
        try:
            if raw_path is not None:
                self.recorders["raw"] = AsyncRecorder(RotatingStreamRecorder(raw_path, connection_id=self.id, generation=self.generation))
                self.recorders["raw"].open()
            if csv_path is not None:
                self.recorders["csv"] = AsyncRecorder(CsvRecorder(csv_path))
                self.recorders["csv"].open()
        except Exception:
            self.stop_recording()
            raise

    def _record(self, kind, *args, **kwargs):
        recorder = self.recorders.get(kind)
        if recorder is not None:
            try:
                accepted = recorder.record(*args, **kwargs)
            except (ValueError, OSError) as exc:
                accepted = False
                self.logs.append(f"记录失败：{exc}")
            if not accepted:
                self.logs.append("记录已停止；请检查未保存数量")
                self.recorders.pop(kind).close(.01)
                self.closing_recorders.append(recorder)

    def stop_recording(self):
        for recorder in self.recorders.values():
            recorder.close(.01)
            self.closing_recorders.append(recorder)
        self.recorders.clear()

    def recording_closed(self):
        return all(item.wait_closed(0) for item in self.closing_recorders)


class _SessionRelay(QObject):
    def __init__(self, manager, session, generation):
        super().__init__(manager)
        self.manager, self.session, self.generation = manager, session, generation
        self.sequence = 0

    def dispatch(self, kind, payload):
        self.sequence += 1
        event = DataEvent(self.session.id, self.generation, self.sequence, kind, payload)
        if self.session.accept(event):
            self.manager.changed.emit(self.session.id)
            if kind == "events":
                self.manager.events.emit(self.session.id, self.generation, payload)

    @Slot(object)
    def on_events(self, payload):
        try:
            self.dispatch("events", payload)
        finally:
            worker = self.session.worker
            if worker is not None and hasattr(worker, "ack_batch"):
                worker.ack_batch()

    @Slot(bytes)
    def on_raw(self, raw):
        self.dispatch("rx", raw)

    @Slot(bytes)
    def on_sent(self, raw):
        self.dispatch("tx", raw)

    @Slot(str, str)
    def on_status(self, kind, message):
        self.dispatch("status", (kind, message))

    @Slot()
    def on_finished(self):
        session = self.session
        if session.generation == self.generation:
            session.end()
            session.worker = session.thread = None
            self.manager.changed.emit(session.id)
            self.manager.closed.emit(session.id)
        self.deleteLater()


class ConnectionManager(QObject):
    changed = Signal(str)
    events = Signal(str, int, object)
    closed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.sessions = {}
        self._relays = {}
        self._demo_timers = {}

    def add(self, config):
        if len(self.sessions) >= MAX_CONNECTIONS or config.id in self.sessions:
            raise ValueError("最多4个连接，ID不能重复")
        session = ConnectionSession(config)
        self.sessions[config.id] = session
        self.changed.emit(config.id)
        return session

    def remove(self, connection_id):
        session = self.sessions[connection_id]
        if session.running or not session.recording_closed():
            raise ValueError("先停止连接并等待记录收尾")
        del self.sessions[connection_id]
        self.changed.emit(connection_id)

    def document(self):
        return [session.config.document() for session in self.sessions.values()]

    def load_document(self, document):
        if any(s.running for s in self.sessions.values()):
            raise ValueError("先停止全部连接再恢复配置")
        if not isinstance(document, list) or not 1 <= len(document) <= MAX_CONNECTIONS:
            raise ValueError("连接配置需要1–4项")
        configs = [ConnectionConfig.from_document(value) for value in document]
        if len({c.id for c in configs}) != len(configs):
            raise ValueError("连接ID重复")
        self.sessions = {c.id: ConnectionSession(c) for c in configs}
        self.changed.emit("")

    def start(self, connection_id, *, worker=None):
        session = self.sessions[connection_id]
        config = ConnectionConfig.from_document(session.config.document())
        if config.source == "external":
            raise ValueError("主工作台连接请在主窗口开始")
        if worker is None and config.source != "demo":
            worker = self._make_worker(config)
        generation = session.begin()
        relay = _SessionRelay(self, session, generation)
        self._relays[connection_id] = relay
        if config.source == "demo":
            import math
            timer = QTimer(self)
            timer.setInterval(max(1, min(10000, int(config.options.get("interval_ms", 20)))))
            step = [0]
            def tick():
                step[0] += 1
                count = max(1, min(16, int(config.options.get("channels", 3))))
                relay.on_events([TelemetryEvent(raw=b"", samples=tuple(math.sin(step[0] * .03 + i) for i in range(count)))])
            timer.timeout.connect(tick)
            self._demo_timers[connection_id] = timer
            timer.start()
            self.changed.emit(connection_id)
            return
        session.worker = worker
        thread = QThread(self)
        session.thread = thread
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.events.connect(relay.on_events)
        worker.raw.connect(relay.on_raw)
        worker.transmitted.connect(relay.on_sent)
        worker.status.connect(relay.on_status)
        worker.finished.connect(thread.quit)
        thread.finished.connect(relay.on_finished)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.start()
        self.changed.emit(connection_id)

    @staticmethod
    def _make_worker(config):
        options = config.options
        if config.source == "serial":
            from .serial_link import SerialLinkWorker
            return SerialLinkWorker(options.get("port", ""), options.get("baudrate", 115200), config.decoder,
                bytesize=options.get("bytesize", 8), parity=options.get("parity", "N"), stopbits=options.get("stopbits", 1),
                dtr=False, rts=False, flow_control=options.get("flow_control", "none"), await_ack=True)
        if config.source in {"tcp_client", "tcp_server", "udp"}:
            from .network_link import NetworkConfig, NetworkLinkWorker
            return NetworkLinkWorker(NetworkConfig(config.source, options.get("host", "127.0.0.1"), options.get("port", 5000),
                options.get("peer_host"), options.get("peer_port")), config.decoder, await_ack=True)
        if config.source == "stream":
            from .capture_stream import RawReplayWorker
            return RawReplayWorker(options.get("path", ""), config.decoder, speed=options.get("speed", 1),
                                   loop=options.get("loop", False), await_ack=True)
        if config.source == "csv":
            from .playback import CsvReplayWorker
            return CsvReplayWorker(options.get("path", ""), speed=options.get("speed", 1),
                                   loop=options.get("loop", False), await_ack=True)
        raise ValueError("不支持该数据源")

    def stop(self, connection_id):
        session = self.sessions[connection_id]
        timer = self._demo_timers.pop(connection_id, None)
        if timer is not None:
            timer.stop()
            timer.deleteLater()
            session.end()
            self.changed.emit(connection_id)
        elif session.worker is not None:
            session.active_job = None
            session.worker.stop()

    def stop_all(self):
        for key, session in self.sessions.items():
            if session.config.source != "external":
                self.stop(key)

    def all_stopped(self):
        return all((not s.running or s.config.source == "external") and s.recording_closed() for s in self.sessions.values())
