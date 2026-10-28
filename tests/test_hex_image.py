"""Intel HEX validation and sparse image behavior."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path
from zipfile import ZipFile

import pytest

from qx_stc_isp.hex_image import HexFormatError, HexImage


ARCHIVE = Path(__file__).resolve().parents[1] / "3-烧录软件.zip"


def record(address: int, kind: int, payload: bytes = b"") -> str:
    body = bytes((len(payload), address >> 8, address & 0xFF, kind)) + payload
    return ":" + (body + bytes((-sum(body) & 0xFF,))).hex().upper()


@pytest.mark.parametrize(
    ("member", "size", "minimum", "maximum"),
    [
        ("3-烧录软件/flashled.hex", 94, 0x0000, 0x005D),
        ("3-烧录软件/led/Objects/led.hex", 21, 0x0000, 0x0014),
    ],
)
def test_archive_samples(member: str, size: int, minimum: int, maximum: int) -> None:
    if not ARCHIVE.is_file():
        pytest.skip("Original vendor ZIP is private; public sample regression runs separately")
    with ZipFile(ARCHIVE) as archive:
        image = HexImage.from_text(archive.read(member).decode("ascii"))
    assert image.data_size == size
    assert (image.min_address, image.max_address) == (minimum, maximum)
    assert sum(len(data) for _, data in image.segments) == size


@pytest.mark.parametrize("name,size,maximum", [("flashled.hex", 94, 0x005D), ("led.hex", 21, 0x0014)])
def test_public_samples(name, size, maximum):
    image = HexImage.from_file(Path(__file__).resolve().parents[1] / "examples" / name)
    assert image.data_size == size
    assert (image.min_address, image.max_address) == (0, maximum)
    assert sum(len(data) for _, data in image.segments) == size


def test_from_file_preserves_path(tmp_path: Path) -> None:
    path = tmp_path / "test.hex"
    path.write_text("\n".join([record(3, 0, b"\x12"), record(0, 1)]), encoding="ascii")
    image = HexImage.from_file(path)
    assert image.path == path
    assert image.segments == ((3, b"\x12"),)
    with pytest.raises(FrozenInstanceError):
        image.path = None  # type: ignore[misc]


@pytest.mark.parametrize(
    "content",
    [
        ":0200000012EC\n:00000001FF",  # Declared length differs.
        ":0100000012EE\n:00000001FF",  # Bad line checksum.
        "0100000012ED\n:00000001FF",  # Missing colon.
        ":0100000012EG\n:00000001FF",  # Non-hex character.
        ":0100000012E\n:00000001FF",  # Odd number of hex digits.
    ],
)
def test_rejects_malformed_records(content: str) -> None:
    with pytest.raises(HexFormatError):
        HexImage.from_text(content)


def test_requires_eof_and_rejects_trailing_record() -> None:
    data = record(0, 0, b"\x42")
    with pytest.raises(HexFormatError, match="EOF"):
        HexImage.from_text(data)
    with pytest.raises(HexFormatError, match="EOF 后"):
        HexImage.from_text("\n".join([data, record(0, 1), data]))
    with pytest.raises(HexFormatError, match="没有程序数据"):
        HexImage.from_text(record(0, 1))


def test_duplicate_address_requires_matching_value() -> None:
    same = "\n".join([record(4, 0, b"\x12"), record(4, 0, b"\x12"), record(0, 1)])
    assert HexImage.from_text(same).data_size == 1
    different = "\n".join([record(4, 0, b"\x12"), record(4, 0, b"\x34"), record(0, 1)])
    with pytest.raises(HexFormatError, match="数据冲突"):
        HexImage.from_text(different)


def test_sparse_segments_and_padded_simulation_blocks() -> None:
    content = "\n".join([record(2, 0, b"\x11\x22"), record(8, 0, b"\x33"), record(0, 1)])
    image = HexImage.from_text(content)
    assert image.segments == ((2, b"\x11\x22"), (8, b"\x33"))
    assert image.data_size == 3
    assert list(image.iter_blocks(4)) == [
        (0, b"\xff\xff\x11\x22"),
        (4, b"\xff\xff\xff\xff"),
        (8, b"\x33\xff\xff\xff"),
    ]
    with pytest.raises(ValueError, match="block_size"):
        list(image.iter_blocks(0))
    with pytest.raises(ValueError, match="fill"):
        list(image.iter_blocks(4, fill=256))


@pytest.mark.parametrize("kind", [2, 4])
def test_zero_extended_address_is_accepted(kind: int) -> None:
    content = "\n".join([record(0, kind, b"\x00\x00"), record(0x1FFF, 0, b"\x99"), record(0, 1)])
    image = HexImage.from_text(content)
    assert image.max_address == 0x1FFF


@pytest.mark.parametrize(
    "records",
    [
        [record(0x1FFF, 0, b"\x01\x02")],
        [record(0, 4, b"\x00\x01"), record(0, 0, b"\x01")],
        [record(0, 2, b"\x02\x00"), record(0, 0, b"\x01")],
    ],
)
def test_rejects_addresses_outside_eight_kib(records: list[str]) -> None:
    with pytest.raises(HexFormatError, match="超出程序区"):
        HexImage.from_text("\n".join([*records, record(0, 1)]))


def test_target_specific_limit_and_parse_only_mode() -> None:
    content = "\n".join(
        [record(0, 4, b"\x00\x01"), record(0x2345, 0, b"\xA5"), record(0, 1)]
    )
    with pytest.raises(HexFormatError, match="超出程序区"):
        HexImage.from_text(content)
    image = HexImage.from_text(content, program_limit=0x20000)
    assert image.max_address == 0x12345
    assert image.program_limit == 0x20000
    unknown_target = HexImage.from_text(content, program_limit=None)
    assert unknown_target.max_address == 0x12345
    assert unknown_target.program_limit is None


def test_parse_only_accepts_highest_address_but_bounds_simulation() -> None:
    highest = "\n".join(
        [record(0, 4, b"\xFF\xFF"), record(0xFFFF, 0, b"\x11"), record(0, 1)]
    )
    assert HexImage.from_text(highest, program_limit=None).max_address == 0xFFFFFFFF
    sparse = "\n".join(
        [record(0, 0, b"\x11"), record(0, 4, b"\x01\x00"), record(0, 0, b"\x22"), record(0, 1)]
    )
    image = HexImage.from_text(sparse, program_limit=None)
    with pytest.raises(ValueError, match="跨度过大"):
        list(image.iter_blocks(64))


def test_hex_text_size_limit(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    content = "\n".join([record(0, 0, b"\x01"), record(0, 1)])
    monkeypatch.setattr("qx_stc_isp.hex_image.MAX_HEX_TEXT_BYTES", 12)
    with pytest.raises(HexFormatError, match="64 MiB"):
        HexImage.from_text(content)
    path = tmp_path / "large.hex"
    path.write_text(content, encoding="ascii")
    with pytest.raises(HexFormatError, match="64 MiB"):
        HexImage.from_file(path)


def test_rejects_unsupported_record_and_invalid_control_shape() -> None:
    for content in (
        "\n".join([record(0, 0, b"\x01"), record(0, 5, b"\x00\x00\x00\x00"), record(0, 1)]),
        "\n".join([record(0, 4, b"\x00"), record(0, 0, b"\x01"), record(0, 1)]),
        "\n".join([record(1, 1), record(0, 0, b"\x01")]),
    ):
        with pytest.raises(HexFormatError):
            HexImage.from_text(content)
