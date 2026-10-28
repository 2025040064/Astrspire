"""Strict Intel HEX parsing with an optional target program-area limit.

The block iterator is intended for the simulator. It does not establish an
addressing or padding rule for a physical ISP bootloader.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


PROGRAM_LIMIT = 0x2000
INTEL_HEX_ADDRESS_LIMIT = 0x100000000
MAX_SIMULATION_BLOCKS = 131072
MAX_HEX_TEXT_BYTES = 64 * 1024 * 1024


class HexFormatError(ValueError):
    """An Intel HEX file is malformed or cannot fit the program area."""


@dataclass(frozen=True, slots=True)
class HexImage:
    """Immutable, sorted segments; ``program_limit=None`` means parse-only."""

    segments: tuple[tuple[int, bytes], ...]
    path: Path | None = None
    program_limit: int | None = PROGRAM_LIMIT

    def __post_init__(self) -> None:
        limit = self._validated_limit(self.program_limit)
        normalized: list[tuple[int, bytes]] = []
        previous_end = 0
        for start, data in self.segments:
            if not isinstance(start, int) or not isinstance(data, (bytes, bytearray, memoryview)):
                raise TypeError("segments 必须由地址和字节数据组成")
            immutable_data = bytes(data)
            if not immutable_data or start < previous_end or start < 0 or start + len(immutable_data) > limit:
                raise ValueError("segments 的地址或顺序无效")
            normalized.append((start, immutable_data))
            previous_end = start + len(immutable_data)
        if not normalized:
            raise ValueError("HexImage 至少需要一个数据段")
        object.__setattr__(self, "segments", tuple(normalized))
        if self.path is not None:
            object.__setattr__(self, "path", Path(self.path))

    @staticmethod
    def _validated_limit(program_limit: int | None) -> int:
        if program_limit is None:
            return INTEL_HEX_ADDRESS_LIMIT
        if (
            isinstance(program_limit, bool)
            or not isinstance(program_limit, int)
            or not 0 < program_limit <= INTEL_HEX_ADDRESS_LIMIT
        ):
            raise ValueError("程序区上限必须是 1–2^32，或为 None（仅解析）")
        return program_limit

    @classmethod
    def from_file(
        cls, path: str | Path, *, program_limit: int | None = PROGRAM_LIMIT
    ) -> HexImage:
        source = Path(path)
        try:
            if source.stat().st_size > MAX_HEX_TEXT_BYTES:
                raise HexFormatError("HEX 文件超过 64 MiB 解析上限")
            content = source.read_text(encoding="ascii")
        except UnicodeError as exc:
            raise HexFormatError("HEX 文件含非 ASCII 字符") from exc
        return cls.from_text(content, path=source, program_limit=program_limit)

    @classmethod
    def from_text(
        cls,
        content: str,
        *,
        path: str | Path | None = None,
        program_limit: int | None = PROGRAM_LIMIT,
    ) -> HexImage:
        if len(content) > MAX_HEX_TEXT_BYTES:
            raise HexFormatError("HEX 文本超过 64 MiB 解析上限")
        limit = cls._validated_limit(program_limit)
        values: dict[int, int] = {}
        base = 0
        seen_eof = False

        for line_number, raw_line in enumerate(content.splitlines(), start=1):
            line = raw_line.strip()
            if not line:
                continue
            if seen_eof:
                raise HexFormatError(f"第 {line_number} 行：EOF 后还有记录")
            record = _parse_record(line, line_number)
            count, high, low, kind = record[:4]
            offset = (high << 8) | low
            payload = record[4:-1]

            if kind == 0x00:
                if offset + count > 0x10000:
                    raise HexFormatError(f"第 {line_number} 行：数据记录跨越 16 位地址边界")
                for index, value in enumerate(payload):
                    address = base + offset + index
                    if address >= limit:
                        raise HexFormatError(
                            f"第 {line_number} 行：地址 0x{address:04X} 超出程序区 0x0000–0x{limit - 1:04X}"
                        )
                    previous = values.get(address)
                    if previous is not None and previous != value:
                        raise HexFormatError(f"第 {line_number} 行：地址 0x{address:04X} 的数据冲突")
                    values[address] = value
            elif kind == 0x01:
                _require_control_record(count, offset, 0, line_number)
                seen_eof = True
            elif kind in (0x02, 0x04):
                _require_control_record(count, offset, 2, line_number)
                field = int.from_bytes(payload, "big")
                base = field << (4 if kind == 0x02 else 16)
            else:
                raise HexFormatError(f"第 {line_number} 行：不支持的记录类型 0x{kind:02X}")

        if not seen_eof:
            raise HexFormatError("HEX 文件缺少 EOF 记录")
        if not values:
            raise HexFormatError("HEX 文件没有程序数据")

        segments: list[tuple[int, bytes]] = []
        start: int | None = None
        previous_address: int | None = None
        current = bytearray()
        for address, value in sorted(values.items()):
            if previous_address is None or address != previous_address + 1:
                if start is not None:
                    segments.append((start, bytes(current)))
                start = address
                current = bytearray()
            current.append(value)
            previous_address = address
        if start is not None:
            segments.append((start, bytes(current)))
        return cls(
            tuple(segments),
            Path(path) if path is not None else None,
            program_limit,
        )

    @property
    def data_size(self) -> int:
        return sum(len(data) for _, data in self.segments)

    @property
    def min_address(self) -> int:
        return self.segments[0][0]

    @property
    def max_address(self) -> int:
        start, data = self.segments[-1]
        return start + len(data) - 1

    def iter_blocks(self, block_size: int, fill: int = 0xFF) -> Iterator[tuple[int, bytes]]:
        """Yield aligned blocks over the occupied span, filling holes for simulation."""
        if not isinstance(block_size, int) or block_size <= 0:
            raise ValueError("block_size 必须为正整数")
        if not isinstance(fill, int) or not 0 <= fill <= 0xFF:
            raise ValueError("fill 必须是 0–255")

        first = self.min_address // block_size * block_size
        stop = (self.max_address // block_size + 1) * block_size
        if (stop - first) // block_size > MAX_SIMULATION_BLOCKS:
            raise ValueError("稀疏地址跨度过大，不能生成模拟块")
        segment_index = 0
        for block_start in range(first, stop, block_size):
            block = bytearray([fill]) * block_size
            block_end = block_start + block_size
            while segment_index < len(self.segments):
                start, data = self.segments[segment_index]
                end = start + len(data)
                if start >= block_end:
                    break
                if end > block_start:
                    source_start = max(start, block_start)
                    source_end = min(end, block_end)
                    block[source_start - block_start : source_end - block_start] = data[
                        source_start - start : source_end - start
                    ]
                if end <= block_end:
                    segment_index += 1
                else:
                    break
            yield block_start, bytes(block)


def _parse_record(line: str, line_number: int) -> bytes:
    if not line.startswith(":"):
        raise HexFormatError(f"第 {line_number} 行：记录必须以冒号开头")
    hex_part = line[1:]
    if len(hex_part) < 10 or len(hex_part) % 2:
        raise HexFormatError(f"第 {line_number} 行：记录长度无效")
    if any(character not in "0123456789abcdefABCDEF" for character in hex_part):
        raise HexFormatError(f"第 {line_number} 行：记录含非十六进制字符")
    record = bytes.fromhex(hex_part)
    if len(record) != record[0] + 5:
        raise HexFormatError(f"第 {line_number} 行：数据长度与记录长度不符")
    if sum(record) & 0xFF:
        raise HexFormatError(f"第 {line_number} 行：行校验失败")
    return record


def _require_control_record(count: int, offset: int, expected_count: int, line_number: int) -> None:
    if count != expected_count or offset != 0:
        raise HexFormatError(f"第 {line_number} 行：控制记录长度或地址无效")
