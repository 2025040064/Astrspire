"""Independent STC89 sum8 field codec, based on the captured F002 session.

Pure data operations. A strict fingerprint registry records only the profile
that passed a real-board acceptance; the generic ``HARDWARE_DIALECTS`` table
stays empty. The clock/reload calculation is restricted to the statically
traced F002 / 6.6C profile. It is not a protocol implementation for other STC
families.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

PROGRAM_BYTES = 8192
BLOCK_BYTES = 128
ERASE_BYTES = 512
CAPTURED_VERSION = bytes.fromhex("66 43 FD")
KNOWN_CHIP_ID = 0xF002
BAUDRATES = (921600, 460800, 230400, 115200, 57600, 38400, 28800,
             19200, 14400, 9600, 4800, 2400, 1200)
# The executable compares against a single-precision 0.01, not decimal 0.01.
_BAUD_ERROR_LIMIT = 0.009999999776482582


@dataclass(frozen=True, slots=True)
class AcceptedHardwareProfile:
    """A fingerprint that passed a captured-session and real-board acceptance."""

    key: str
    chip_name: str
    chip_id: int
    version_raw: bytes
    program_bytes: int
    block_bytes: int
    evidence: str


ACCEPTED_HARDWARE_PROFILES: Mapping[tuple[int, bytes], AcceptedHardwareProfile] = (
    MappingProxyType({
        (KNOWN_CHIP_ID, CAPTURED_VERSION): AcceptedHardwareProfile(
            key="stc89-f002-66c",
            chip_name="STC89C52RC/LE52RC",
            chip_id=KNOWN_CHIP_ID,
            version_raw=CAPTURED_VERSION,
            program_bytes=PROGRAM_BYTES,
            block_bytes=BLOCK_BYTES,
            evidence=(
                "A2 2026-10-21: 实板写入 led.hex 成功，14 条 TX 与官方记录逐字节一致，"
                "擦除 ACK 与 4 个块 ACK 匹配，断电重启运行；官方工具同板同文件成功。"
            ),
        ),
    })
)


def resolve_accepted_profile(
    chip_id: int | None, version_raw: bytes | None
) -> AcceptedHardwareProfile | None:
    """Return the accepted profile only for an exact chip/version fingerprint."""

    if chip_id is None or version_raw is None:
        return None
    return ACCEPTED_HARDWARE_PROFILES.get((chip_id, bytes(version_raw)))



class ProtocolError(ValueError):
    """Observed response or identity is incompatible with this profile."""


@dataclass(frozen=True)
class Stc89Status:
    chip_id: int
    version_raw: bytes
    timing_samples: tuple[int, ...]
    payload_size: int

    @classmethod
    def parse(cls, payload: bytes) -> Stc89Status:
        if len(payload) != 53 or payload[0] != 0:
            raise ProtocolError("状态包长度或首字节不属于本次已观察格式")
        timing = tuple(int.from_bytes(payload[i:i + 2], "big") for i in range(1, 17, 2))
        if not all(timing):
            raise ProtocolError("状态包时钟测量字段包含零值")
        return cls(int.from_bytes(payload[20:22], "big"), bytes(payload[17:20]), timing, len(payload))

    @property
    def version_label(self) -> str:
        value, suffix = self.version_raw[:2]
        return f"{value >> 4}.{value & 15}{chr(suffix) if 32 <= suffix < 127 else '?'}"

    def require_captured_profile(self) -> None:
        if self.chip_id != KNOWN_CHIP_ID:
            raise ProtocolError(f"未知芯片 ID 0x{self.chip_id:04X}，不能进入擦写阶段")
        if self.version_raw != CAPTURED_VERSION:
            raise ProtocolError("Bootloader 原始版本字段未被本次成功会话覆盖")


@dataclass(frozen=True)
class CapturedBaudParameters:
    """Decode observed probe fields; does not synthesize unverified settings."""

    fields: bytes

    @classmethod
    def parse_probe(cls, payload: bytes, *, initial_baudrate: int = 2400) -> CapturedBaudParameters:
        if len(payload) != 7 or payload[0] != 0x8F:
            raise ProtocolError("波特率探测包格式错误")
        fields = bytes(payload[1:])
        expected_wait = min(250, max(40, initial_baudrate // 1200 * 20))
        expected_delay = min(254, (256 - fields[1]) * 2)
        if (fields[2] != (fields[0] ^ 255) or fields[3] != expected_delay
                or fields[4] != expected_wait):
            raise ProtocolError("波特率参数与静态字段关系不一致")
        return cls(fields)

    def probe(self) -> bytes:
        return b"\x8f" + self.fields

    def activate(self) -> bytes:
        return b"\x8e" + self.fields[:5]


@dataclass(frozen=True)
class Stc89BaudPlan:
    """Independent calculation from the traced old STC89 timer-category 3.

    Evidence and the deliberately narrower safety checks are documented in
    docs/analysis/07-stc89-core-evidence.md. This does not enable hardware I/O.
    """

    initial_baudrate: int
    baudrate: int
    clock_hz: int
    timer_divisor: int
    parameters: CapturedBaudParameters

    @classmethod
    def derive(cls, status: Stc89Status, *, initial_baudrate: int = 2400,
               requested_baudrate: int = 115200) -> Stc89BaudPlan:
        status.require_captured_profile()
        if initial_baudrate != 2400:
            raise ProtocolError("当前证据仅覆盖初始波特率 2400 / 8N1")
        if requested_baudrate not in BAUDRATES:
            raise ProtocolError("请求波特率不在静态观察的标准速率表中")
        if len(status.timing_samples) != 8 or any(
            not isinstance(value, int) or not 0 < value <= 0xffff
            for value in status.timing_samples
        ):
            raise ProtocolError("时钟测量字段无效")
        # The official old-version branch selects payload[3:5], i.e. the
        # SECOND sample, rather than averaging all eight timing values.
        clock_hz = status.timing_samples[1] * initial_baudrate * 12 // 7

        def divisor_for(rate: int) -> int:
            denominator = 32 * rate
            return (2 * clock_hz + denominator) // (2 * denominator)

        def acceptable(rate: int) -> bool:
            exact_divisor = clock_hz / (32 * rate)
            divisor = divisor_for(rate)
            return (exact_divisor >= 2 and 2 <= divisor < 65536
                    and abs(clock_hz / (32 * divisor * rate) - 1) < _BAUD_ERROR_LIMIT)

        fastest = next((rate for rate in BAUDRATES if acceptable(rate)), None)
        slowest = next((rate for rate in reversed(BAUDRATES)
                        if clock_hz / (32 * rate) < 65535), None)
        if fastest is None or slowest is None:
            raise ProtocolError("无法从测量时钟获得有证据支持的定时器参数")
        baudrate = max(slowest, min(requested_baudrate, fastest))
        # More conservative than the official clamping: re-check the chosen
        # rate too, so an intermediate rate cannot silently exceed 1% error.
        if not acceptable(baudrate):
            raise ProtocolError("所选波特率的定时器范围或误差不满足安全检查")
        divisor = divisor_for(baudrate)
        reload_value = (-divisor) & 0xffff
        high, low = reload_value.to_bytes(2, "big")
        marker = (0x83 if clock_hz < 6_000_000 else
                  0x82 if clock_hz < 12_000_000 else
                  0x81 if clock_hz < 24_000_000 else 0x80)
        fields = bytes((high, low, high ^ 255, min(254, (256 - low) * 2),
                        min(250, max(40, initial_baudrate // 1200 * 20)), marker))
        parameters = CapturedBaudParameters.parse_probe(b"\x8f" + fields,
                                                       initial_baudrate=initial_baudrate)
        return cls(initial_baudrate, baudrate, clock_hz, divisor, parameters)


def access_payload(chip_id: int = KNOWN_CHIP_ID) -> bytes:
    if chip_id != KNOWN_CHIP_ID:
        raise ProtocolError("本适配只覆盖 F002 的离线协议测试")
    return b"\x80\x00\x00\x36\x01" + chip_id.to_bytes(2, "big")


def erase_payload(program_end_exclusive: int) -> bytes:
    if not 0 < program_end_exclusive <= PROGRAM_BYTES:
        raise ProtocolError("擦除地址范围必须位于 8 KiB 程序区")
    sectors = (program_end_exclusive + ERASE_BYTES - 1) // ERASE_BYTES
    return bytes((0x84, sectors)) + b"\x33" * 6


def write_payload(address: int, data: bytes) -> bytes:
    if (len(data) != BLOCK_BYTES or address < 0 or address % BLOCK_BYTES
            or address + len(data) > PROGRAM_BYTES):
        raise ProtocolError("写块必须为程序区内对齐的 128 字节")
    return b"\x00" + address.to_bytes(4, "big") + len(data).to_bytes(2, "big") + bytes(data)


def require_write_ack(payload: bytes, data: bytes) -> None:
    if payload != bytes((0x80, sum(data) & 255)):
        raise ProtocolError("写块应答或数据和不匹配；应停止并重新上电")
