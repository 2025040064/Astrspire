"""只读解析 AiCube v6.97A 识别表记录（A3 字段追踪用）。

读取官方 EXE 的 PE 节映射，按 `0x504450 + (index+1)*32` 取 1345 条记录，
与项目静态名称表逐条对应，输出 JSON 到 captures/private/。不启动目标程序，
不连接串口，不执行任何官方代码。
"""

from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from qx_stc_isp.chip_catalog import ChipCatalog  # noqa: E402

TARGET = Path(r"C:\Users\Legion\Desktop\AiCube-ISP-v6.97A.exe")
TABLE_ADDRESS = 0x504450
RECORD_BYTES = 32
OUTPUT = ROOT / "captures" / "private" / "aicube-v697a-catalog-records-20261021.json"


def read_image(path: Path) -> bytes:
    return path.read_bytes()


def va_to_offset(data: bytes, va: int) -> int:
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    if data[pe:pe + 4] != b"PE\0\0":
        raise ValueError("not a PE file")
    coff = pe + 4
    sections = struct.unpack_from("<H", data, coff + 2)[0]
    optional = coff + 20
    magic = struct.unpack_from("<H", data, optional)[0]
    image_base = (struct.unpack_from("<Q", data, optional + 24)[0]
                  if magic == 0x20B else struct.unpack_from("<I", data, optional + 28)[0])
    section_table = optional + struct.unpack_from("<H", data, coff + 16)[0]
    rva = va - image_base
    for index in range(sections):
        offset = section_table + index * 40
        vsize, vaddr, rawsize, rawptr = struct.unpack_from("<IIII", data, offset + 8)
        if vaddr <= rva < vaddr + max(vsize, rawsize):
            return rawptr + (rva - vaddr)
    raise ValueError("address is not mapped by a PE section")


def main() -> int:
    data = read_image(TARGET)
    table = va_to_offset(data, TABLE_ADDRESS)
    catalog = ChipCatalog.from_static_evidence()
    records = []
    for index, entry in enumerate(catalog.entries, start=1):
        f0, f1, f2, f3, f4, f5, f6, f7 = struct.unpack_from("<8I", data, table + index * RECORD_BYTES)
        records.append({
            "index": index,
            "name": entry.name,
            "catalog_chip_id": entry.chip_id,
            "alternate_chip_id": entry.alternate_chip_id,
            "raw": [f"0x{value:08X}" for value in (f0, f1, f2, f3, f4, f5, f6, f7)],
            "field0": {
                "class_bits_15_22": (f0 >> 15) & 0xFF,
                "timer_category_bits_6_8": (f0 >> 6) & 7,
                "protocol_category_bits_9_11": (f0 >> 9) & 7,
                "low_flags": f0 & 0x3F,
            },
            "field1_name_pointer": f"0x{f1:08X}",
            "field2_identification_id": f"0x{f2:04X}",
            "field3": f"0x{f3:08X}",
            "field4": f"0x{f4:08X}",
            "field5": f"0x{f5:08X}",
            "field6": f"0x{f6:08X}",
            "field7": f"0x{f7:08X}",
        })
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps({
        "source": str(TARGET),
        "table_address": f"0x{TABLE_ADDRESS:08X}",
        "record_bytes": RECORD_BYTES,
        "record_count": len(records),
        "semantics": "field3/field4/field5/field6 sizes are observed values, not yet bound to code checks",
        "records": records,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUTPUT} ({len(records)} records)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
