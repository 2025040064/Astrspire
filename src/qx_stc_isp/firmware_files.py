"""Firmware file/buffer operations, independent of any programming backend."""

from __future__ import annotations

from pathlib import Path

from .hex_image import HexImage

MAX_BINARY_BYTES = 8 * 1024 * 1024


def parse_address(text: str) -> int:
    value = text.strip()
    if value.lower().startswith("0x"):
        value = value[2:]
    if not value or len(value) > 8 or any(c not in "0123456789abcdefABCDEF" for c in value):
        raise ValueError("地址必须是 1–8 位十六进制数字")
    return int(value, 16)


def load_firmware(path: Path, *, base_address: int = 0, program_limit: int | None = None) -> HexImage:
    if path.suffix.lower() != ".bin":
        return HexImage.from_file(path, program_limit=program_limit)
    if isinstance(base_address, bool) or not isinstance(base_address, int) or not 0 <= base_address < 2**32:
        raise ValueError("BIN 起始地址无效")
    with path.open("rb") as source:
        content = source.read(MAX_BINARY_BYTES + 1)
    if not content or len(content) > MAX_BINARY_BYTES:
        raise ValueError("BIN 文件为空或超过 8 MiB 上限")
    return HexImage(((base_address, content),), path=path.resolve(), program_limit=program_limit)


def buffer_preview(image: HexImage, *, max_data_bytes: int = 4096) -> str:
    lines = [f"数据 {image.data_size} 字节；地址 0x{image.min_address:08X}–0x{image.max_address:08X}"]
    shown = 0
    for start, data in image.segments:
        lines.append(f"\n数据段 0x{start:08X} · {len(data)} 字节")
        for offset in range(0, len(data), 16):
            if shown >= max_data_bytes:
                lines.append(f"… 预览只显示前 {max_data_bytes} 个数据字节，完整数据仍保留在缓冲区。")
                return "\n".join(lines)
            chunk = data[offset:offset + min(16, max_data_bytes - shown)]
            readable = "".join(chr(c) if 32 <= c < 127 else "." for c in chunk)
            lines.append(f"{start + offset:08X}  {chunk.hex(' ').upper():47}  {readable}")
            shown += len(chunk)
    return "\n".join(lines)


def _record(address: int, kind: int, data: bytes) -> str:
    body = bytes((len(data), address >> 8, address & 255, kind)) + data
    return ":" + (body + bytes((-sum(body) & 255,))).hex().upper()


def export_firmware(path: Path, image: HexImage, *, kind: str) -> None:
    """Create a new output file; never silently overwrite prior firmware."""
    if kind == "bin":
        size = image.max_address - image.min_address + 1
        if size > MAX_BINARY_BYTES:
            raise ValueError("BIN 地址跨度超过 8 MiB；请导出稀疏 HEX")
        buffer = bytearray(b"\xff" * size)
        for start, data in image.segments:
            offset = start - image.min_address
            buffer[offset:offset + len(data)] = data
        with path.open("xb") as output:
            output.write(buffer)
        return
    if kind != "hex":
        raise ValueError("导出格式必须为 HEX 或 BIN")
    records = []
    upper = None
    for start, data in image.segments:
        offset = 0
        while offset < len(data):
            address = start + offset
            high = address >> 16
            if high != upper:
                if high != 0 or upper is not None:
                    records.append(_record(0, 4, high.to_bytes(2, "big")))
                upper = high
            size = min(16, len(data) - offset, 0x10000 - (address & 0xFFFF))
            records.append(_record(address & 0xFFFF, 0, data[offset:offset + size]))
            offset += size
    records.append(_record(0, 1, b""))
    with path.open("x", encoding="ascii", newline="\n") as output:
        output.write("\n".join(records) + "\n")
