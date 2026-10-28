"""Restricted, transport-independent SRAM sampling for our HAL test firmware."""

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import struct
import threading

from .model import TelemetryEvent
from .json_data import load_json_document

TARGET_IDS = {"stm32f1x": 0x410, "stm32g4x": 0x468}
MAGIC = 0x41535744
HEADER_SIZE = 32
SLOT_SIZE = 76
CAPTURE_SIZE = HEADER_SIZE + 64 * SLOT_SIZE


@dataclass(frozen=True, slots=True)
class SwdCaptureConfig:
    adapter: str = "stlink"
    probe_serial: str = ""
    target: str = "stm32g4x"
    manifest_path: str = ""
    poll_ms: int = 50

    def __post_init__(self):
        if self.adapter not in ("stlink", "cmsis-dap") or self.target not in TARGET_IDS:
            raise ValueError("不支持的探针或STM32系列")
        if not isinstance(self.probe_serial, str) or (self.probe_serial and re.fullmatch(r"[A-Za-z0-9._-]{1,128}", self.probe_serial) is None):
            raise ValueError("探针序列号无效")
        if not isinstance(self.manifest_path, str) or len(self.manifest_path) > 4096 or "\x00" in self.manifest_path:
            raise ValueError("采集清单路径无效")
        if type(self.poll_ms) is not int or not 10 <= self.poll_ms <= 500:
            raise ValueError("SWD轮询周期须为10–500ms")

    def document(self):
        return asdict(self)

    def require_ready(self):
        if not self.probe_serial or not self.manifest_path:
            raise ValueError("请选择探针序列号和本轮测试固件的采集清单")


@dataclass(frozen=True, slots=True)
class SwdManifest:
    target: str
    address: int
    firmware_sha256: str
    version: int = 1
    channels: int = 16
    capacity: int = 64
    period_ms: int = 10

    def __post_init__(self):
        if self.target not in TARGET_IDS or type(self.address) is not int or self.address % 4:
            raise ValueError("采样目标或地址无效")
        # Intentionally restrict both profiles to the first 16KiB of SRAM.
        if not 0x20000000 <= self.address <= 0x20004000 - CAPTURE_SIZE:
            raise ValueError("采样区域超出受限SRAM范围")
        if (type(self.version) is not int or self.version != 1 or type(self.channels) is not int
                or self.channels != 16 or type(self.capacity) is not int or self.capacity != 64
                or type(self.period_ms) is not int or self.period_ms != 10):
            raise ValueError("仅支持本轮HAL测试固件的v1/16通道/64槽/10ms布局")
        if not isinstance(self.firmware_sha256, str) or re.fullmatch(r"[0-9a-f]{64}", self.firmware_sha256) is None:
            raise ValueError("固件SHA-256无效")

    @classmethod
    def load(cls, path):
        with Path(path).open("rb") as stream:
            value = load_json_document(stream.read(16385), max_bytes=16384)
        if not isinstance(value, dict):
            raise ValueError("采集清单必须为JSON对象")
        try:
            return cls(**value)
        except TypeError as exc:
            raise ValueError("采集清单字段不完整或包含未知字段") from exc


@dataclass(frozen=True, slots=True)
class SwdStatistics:
    frames: int = 0
    sequence_gaps: int = 0
    retries: int = 0
    overwritten: int = 0
    read_bytes: int = 0


class ProbeLease:
    """Process-local ownership; external debugger contention is backend error."""
    _lock = threading.Lock()
    _owners = set()

    def __init__(self, serial):
        if not isinstance(serial, str) or not serial:
            raise ValueError("探针序列号不能为空")
        self.key = serial.casefold()
        self._held = False

    def __enter__(self):
        with self._lock:
            if self.key in self._owners:
                raise ValueError("探针已被本程序中的采集或实验任务占用")
            self._owners.add(self.key)
            self._held = True
        return self

    def __exit__(self, *_args):
        with self._lock:
            if self._held:
                self._owners.discard(self.key)
                self._held = False


class SwdRingReader:
    def __init__(self, manifest):
        self.manifest = manifest
        self._last = None
        self._boot = None
        self._frames = self._gaps = self._retries = self._read_bytes = 0

    @property
    def statistics(self):
        return SwdStatistics(self._frames, self._gaps, self._retries, self._gaps, self._read_bytes)

    def _read(self, memory, address, size):
        if not self.manifest.address <= address <= self.manifest.address + CAPTURE_SIZE - size:
            raise ValueError("SWD读取超出采样区")
        data = memory.read(address, size)
        if not isinstance(data, bytes) or len(data) != size:
            raise ValueError("SWD返回短读，未生成采样")
        self._read_bytes += size
        return data

    def _header(self, memory):
        header = struct.unpack("<8I", self._read(memory, self.manifest.address, HEADER_SIZE))
        if header[:6] != (MAGIC, 1, TARGET_IDS[self.manifest.target], 64, 16, 10):
            raise ValueError("芯片身份或采样固件布局不匹配")
        if self._boot is not None and header[6] != self._boot:
            raise ValueError("检测到目标重启，已停止采集")
        if self._last is not None and header[7] < self._last:
            raise ValueError("设备序号回退或目标重启，已停止采集")
        if header[7] >= 0x80000000:
            raise ValueError("设备序号即将回绕，请重新开始实验")
        return header

    def begin(self, memory):
        """Baseline at attach: old ring history is not current-session loss."""
        header = self._header(memory)
        self._boot, self._last = header[6], header[7]

    def poll(self, memory):
        header = self._header(memory)
        self._boot = header[6]
        end = header[7]
        if self._last is None:
            self._last = max(0, end - 64)
        if end - self._last > 64:
            self._gaps += end - self._last - 64
            self._last = end - 64
        events = []
        last = self._last
        while last < end:
            first = last + 1
            count = min(end - last, 64 - first % 64)
            address = self.manifest.address + HEADER_SIZE + first % 64 * SLOT_SIZE
            decoded = None
            for _attempt in range(3):
                before = self._read(memory, address, count * SLOT_SIZE)
                after = self._read(memory, address, count * SLOT_SIZE)
                decoded = []
                for offset in range(count):
                    guard, seq, tick, *values = struct.unpack_from("<III16f", before, offset * SLOT_SIZE)
                    after_guard = struct.unpack_from("<I", after, offset * SLOT_SIZE)[0]
                    if guard != after_guard or guard != (first + offset) * 2 or seq != first + offset:
                        decoded = None
                        break
                    if any(not math.isfinite(value) for value in values):
                        raise ValueError("SRAM采样包含非有限数")
                    if values[0] != float(seq) or values[1] != float(tick):
                        raise ValueError("设备计数与采样载荷不一致")
                    decoded.append(TelemetryEvent(raw=b"", samples=tuple(values),
                        received_at=datetime.now(timezone.utc),
                        names=("设备序号", "板端毫秒") + tuple(f"测试{index}" for index in range(1, 15))))
                if decoded is not None:
                    break
                self._retries += 1
            if decoded is None:
                break
            events.extend(decoded)
            last += count
        # Do not emit a batch spanning a detected reset.
        self._header(memory)
        self._last = last
        self._frames += len(events)
        return events
