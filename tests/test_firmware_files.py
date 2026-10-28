from pathlib import Path

import pytest

from qx_stc_isp.firmware_files import buffer_preview, export_firmware, load_firmware, parse_address
from qx_stc_isp.hex_image import HexImage


def test_binary_base_and_program_limit(tmp_path):
    source = tmp_path / "app.bin"
    source.write_bytes(b"\x01\x02")
    image = load_firmware(source, base_address=0x100, program_limit=8192)
    assert image.segments == ((0x100, b"\x01\x02"),)
    with pytest.raises(ValueError):
        load_firmware(source, base_address=8191, program_limit=8192)


def test_hex_export_preserves_sparse_segments_and_extended_boundary(tmp_path):
    image = HexImage(((0xFFFE, b"abcd"), (0x20000, b"xyz")), program_limit=None)
    path = tmp_path / "app.hex"
    export_firmware(path, image, kind="hex")
    restored = HexImage.from_file(path, program_limit=None)
    assert restored.segments == image.segments
    with pytest.raises(FileExistsError):
        export_firmware(path, image, kind="hex")


def test_bin_export_fills_only_internal_holes_and_preview_is_bounded(tmp_path):
    image = HexImage(((0x100, b"AB"), (0x104, b"CD")))
    path = tmp_path / "buffer.bin"
    export_firmware(path, image, kind="bin")
    assert path.read_bytes() == b"AB\xff\xffCD"
    assert "前 2" in buffer_preview(image, max_data_bytes=2)


@pytest.mark.parametrize("text", ["", "-1", "xyz", "100000000"])
def test_invalid_base_address(text):
    with pytest.raises(ValueError):
        parse_address(text)


def test_address_hex_and_empty_binary(tmp_path):
    assert parse_address("0x2000") == parse_address("2000") == 8192
    path = tmp_path / "empty.bin"
    path.write_bytes(b"")
    with pytest.raises(ValueError):
        load_firmware(path)
