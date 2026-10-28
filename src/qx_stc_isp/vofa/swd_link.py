"""Owned OpenOCD helper and a receive-only Qt data-source worker."""

from collections import deque
import os
from pathlib import Path
import shutil
import socket
import struct
import subprocess
import threading
import time
import uuid

from PySide6.QtCore import QObject, Signal, Slot

from .source import BatchAcknowledger
from .swd import ProbeLease, SwdCaptureConfig, SwdManifest, SwdRingReader, TARGET_IDS
from .transmit import TransmitError


def backend_paths():
    executable = os.environ.get("ASTRSPIRE_OPENOCD") or shutil.which("openocd") or r"D:\tools\openocd\bin\openocd.exe"
    scripts = os.environ.get("ASTRSPIRE_OPENOCD_SCRIPTS") or str(Path(executable).parent.parent / "openocd" / "scripts")
    return Path(executable), Path(scripts)


class OpenOcdMemory:
    """Only fixed SRAM/debug-register reads are exposed; no command console."""

    def __init__(self, config, manifest, *, runtime_dir=None):
        self.config, self.manifest = config, manifest
        self.runtime_dir = Path(runtime_dir or os.environ.get("ASTRSPIRE_SWD_LOG_DIR",
            r"F:\A_LIU_Astrspire\VS\51烧录\build-preview\runtime-v060-dev3\swd"))
        self._process = self._socket = self._log = None
        self._buffer = bytearray()
        self._cancel = threading.Event()

    def open(self):
        if self._cancel.is_set():
            raise OSError("SWD启动已取消")
        executable, scripts = backend_paths()
        if not executable.is_file() or not scripts.is_dir():
            raise ValueError("未找到OpenOCD；请设置ASTRSPIRE_OPENOCD和ASTRSPIRE_OPENOCD_SCRIPTS")
        self.config.require_ready()
        if self.config.target != self.manifest.target:
            raise ValueError("所选系列与采集清单不一致")
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        token = uuid.uuid4().hex
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        config_path = self.runtime_dir / f"capture-{token}.cfg"
        interface = "stlink" if self.config.adapter == "stlink" else "cmsis-dap"
        transport = "dapdirect_swd" if interface == "stlink" else "swd"
        lines = ["bindto 127.0.0.1", f"tcl_port {port}", "gdb_port disabled", "telnet_port disabled",
                 f"source [find interface/{interface}.cfg]", f"transport select {transport}",
                 f"adapter serial {self.config.probe_serial}", "adapter speed 1000",
                 f"source [find target/{self.config.target}.cfg]",
                 '$_TARGETNAME configure -event examine-end {}',
                 '$_TARGETNAME configure -event reset-init {}',
                 '$_TARGETNAME configure -event reset-start {}',
                 '$_TARGETNAME configure -event trace-config {}',
                 f"set astrspire_session {token}", "init"]
        config_path.write_text("\n".join(lines) + "\n", encoding="ascii")
        self._log = (self.runtime_dir / f"openocd-{token}.log").open("xb")
        self._process = subprocess.Popen([str(executable), "-s", str(scripts), "-f", str(config_path)],
            stdin=subprocess.DEVNULL, stdout=self._log, stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            if self._cancel.is_set():
                raise OSError("SWD启动已取消")
            if self._process.poll() is not None:
                raise ValueError(f"OpenOCD未连接目标；探针可能忙或接线/系列不符。日志：{self._log.name}")
            try:
                self._socket = socket.create_connection(("127.0.0.1", port), timeout=0.3)
                self._socket.settimeout(0.6)
                break
            except OSError:
                time.sleep(0.05)
        else:
            raise ValueError("OpenOCD启动超时")
        if self._rpc("set astrspire_session") != token:
            raise ValueError("OpenOCD会话身份不匹配")
        chip = struct.unpack("<I", self.read(0xE0042000, 4))[0] & 0xFFF
        if chip != TARGET_IDS[self.config.target]:
            raise ValueError(f"目标芯片ID不符：{chip:03X}")
        self.ensure_running()

    def _rpc(self, command):
        if self._cancel.is_set():
            raise OSError("SWD读取已取消")
        if self._socket is None:
            raise ValueError("SWD连接未就绪")
        self._socket.sendall(command.encode("ascii") + b"\x1a")
        while b"\x1a" not in self._buffer:
            chunk = self._socket.recv(65536)
            if not chunk:
                raise ValueError("探针连接已断开")
            self._buffer.extend(chunk)
            if len(self._buffer) > 1024 * 1024:
                raise ValueError("OpenOCD返回超出上限")
        result, _, rest = self._buffer.partition(b"\x1a")
        self._buffer[:] = rest
        return result.decode("ascii", errors="replace").strip()

    def read(self, address, size):
        base = self.manifest.address
        from .swd import CAPTURE_SIZE
        allowed = (base <= address and address + size <= base + CAPTURE_SIZE) or (address in (0xE0042000, 0xE000EDF0) and size == 4)
        if (type(address) is not int or type(size) is not int or address % 4 or size % 4
                or not 0 < size <= 8192 or not allowed):
            raise ValueError("SWD读取超出许可范围")
        result = self._rpc(f"read_memory 0x{address:08x} 32 {size // 4}")
        try:
            values = [int(value, 0) for value in result.split()]
            if len(values) != size // 4:
                raise ValueError("short reply")
            return struct.pack(f"<{len(values)}I", *values)
        except (ValueError, struct.error) as exc:
            raise ValueError(f"OpenOCD内存读取失败：{result[:160]}") from exc

    def ensure_running(self):
        if struct.unpack("<I", self.read(0xE000EDF0, 4))[0] & (1 << 17):
            raise ValueError("目标处于暂停状态；采集不会自动恢复或复位目标")

    def cancel_io(self):
        self._cancel.set()
        if self._socket is not None:
            try:
                self._socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def close(self):
        if self._socket is not None:
            try:
                self._socket.sendall(b"shutdown\x1a")
            except OSError:
                pass
            self._socket.close()
            self._socket = None
        if self._process is not None:
            try:
                self._process.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                self._process.terminate()
                try:
                    self._process.wait(timeout=0.5)
                except subprocess.TimeoutExpired:
                    self._process.kill()
                    self._process.wait(timeout=0.5)
            self._process = None
        if self._log is not None:
            self._log.close()
            self._log = None


class SwdLinkWorker(QObject):
    events = Signal(object)
    raw = Signal(bytes)
    status = Signal(str, str)
    info = Signal(str)
    error = Signal(str)
    finished = Signal()
    transmitted = Signal(bytes)
    can_send = False

    def __init__(self, config: SwdCaptureConfig, *, memory_factory=OpenOcdMemory, await_ack=False):
        super().__init__()
        config.require_ready()
        self.config = config
        self._factory = memory_factory
        self._stop = threading.Event()
        self._ack = BatchAcknowledger(await_ack)
        self._memory = None
        self._reader = None
        self.connected = False

    @property
    def statistics(self):
        from .swd import SwdStatistics
        return self._reader.statistics if self._reader else SwdStatistics()

    def enqueue_send(self, _data):
        raise TransmitError("SWD数据源仅采集，不支持发送")

    def clear_pending(self):
        pass

    def ack_batch(self):
        self._ack.acknowledge()

    def stop(self):
        self._stop.set()
        self._ack.acknowledge()
        if self._memory is not None:
            self._memory.cancel_io()

    @Slot()
    def run(self):
        try:
            with ProbeLease(self.config.probe_serial):
                manifest = SwdManifest.load(self.config.manifest_path)
                if manifest.target != self.config.target:
                    raise ValueError("所选系列与采集清单不一致")
                self._reader = SwdRingReader(manifest)
                self._memory = self._factory(self.config, manifest)
                try:
                    self._memory.open()
                    self._reader.begin(self._memory)
                    self.connected = True
                    self.status.emit("connected", "SWD已连接 · SRAM采集 · 无串口发送")
                    last_status = time.monotonic()
                    last_count = 0
                    while not self._stop.is_set():
                        started = time.monotonic()
                        self._memory.ensure_running()
                        events = self._reader.poll(self._memory)
                        if events:
                            self._ack.before_emit(self._stop)
                            self.events.emit(events)
                        now = time.monotonic()
                        if now - last_status >= 1:
                            stats = self.statistics
                            rate = (stats.frames - last_count) / (now - last_status)
                            self.status.emit("notice", f"SWD {rate:.1f}帧/s · {stats.frames}帧 · 序号缺口{stats.sequence_gaps} · 重试{stats.retries} · 覆盖{stats.overwritten}")
                            last_count, last_status = stats.frames, now
                        self._stop.wait(max(0, self.config.poll_ms / 1000 - (time.monotonic() - started)))
                finally:
                    self.connected = False
                    self._memory.close()
                    self._memory = None
        except (OSError, ValueError, RuntimeError) as exc:
            if not self._stop.is_set():
                self.error.emit(str(exc))
                self.status.emit("error", str(exc))
        finally:
            self.connected = False
            self.status.emit("stopped", "SWD采集已停止，探针已释放")
            self.finished.emit()
