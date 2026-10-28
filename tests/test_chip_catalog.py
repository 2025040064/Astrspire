"""AiCube 静态目录的离线回归；不得打开真实串口。"""

from qx_stc_isp.chip_catalog import ChipCatalog, IDENTIFICATION_ENTRY_COUNT
from qx_stc_isp.dialects import HARDWARE_DIALECTS, resolve_hardware_dialect
from qx_stc_isp.framing import HARDWARE_DIALECTS as FRAME_HARDWARE_DIALECTS


def test_static_identification_table_count_and_uniqueness() -> None:
    catalog = ChipCatalog.from_static_evidence()
    assert len(catalog.entries) == IDENTIFICATION_ENTRY_COUNT == 1345
    assert len({chip.name for chip in catalog.entries}) == 1345
    assert catalog.entries[0].name == "STC32G144K246"
    assert catalog.entries[-1].name == "STC90LE516AD"


def test_stc89c52rc_is_one_combined_static_record() -> None:
    catalog = ChipCatalog.from_static_evidence()
    chip = catalog.get("STC89C52RC/LE52RC")
    assert chip is not None
    assert chip.chip_id == 0xF002
    assert chip.program_bytes is None
    assert chip.bootloader_family is None
    assert catalog.get("STC89C52RC") is None
    assert catalog.get("not-a-chip") is None


def test_static_id_never_enables_hardware_dialect() -> None:
    assert HARDWARE_DIALECTS is FRAME_HARDWARE_DIALECTS
    assert not HARDWARE_DIALECTS
    assert resolve_hardware_dialect(0xF002, "unknown") is None
