"""ISP 方言证据目录，故意不提供任何真实设备命令。

AiCube v6.97A 的静态帧层包含 8 位和 16 位求和分支，但识别表中的
型号/ID 并不能唯一确定 Bootloader 版本或该用哪一分支。此前 v6.96C
的候选协议亦不能直接移植为此版本、此设备上的烧录契约。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .framing import HARDWARE_DIALECTS, ProtocolDialect


class ProtocolFamily(str, Enum):
    UNKNOWN = "未知／未实机验证"
    SERIAL_SUM8_CANDIDATE = "串口 8 位和静态候选"
    SERIAL_SUM16_CANDIDATE = "串口 16 位和静态候选"


@dataclass(frozen=True, slots=True)
class StaticFrameCandidate:
    family: ProtocolFamily
    checksum_bits: int
    evidence_id: str
    hardware_enabled: bool = False


STATIC_FRAME_CANDIDATES: tuple[StaticFrameCandidate, ...] = (
    StaticFrameCandidate(
        ProtocolFamily.SERIAL_SUM8_CANDIDATE,
        8,
        "ev_c6dc1ec6d1d05b2ce06c7f4acb15e8eab517f9b8bc2204047bf214677b978b3d",
    ),
    StaticFrameCandidate(
        ProtocolFamily.SERIAL_SUM16_CANDIDATE,
        16,
        "ev_c6dc1ec6d1d05b2ce06c7f4acb15e8eab517f9b8bc2204047bf214677b978b3d",
    ),
)

# 复用 framing.py 中唯一的真实硬件注册表，不建立第二个状态源。


def resolve_hardware_dialect(
    chip_id: int | None, bootloader_version: str | None
) -> ProtocolDialect | None:
    """通用硬件方言仍为空；F002 / 66 43 FD 的严格指纹请用
    ``stc89.resolve_accepted_profile(chip_id, version_raw)`` 查询，二者不是同一注册表。
    """

    return None
