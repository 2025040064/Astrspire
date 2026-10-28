"""Pure file validation only; never invokes an experiment or accesses hardware."""

import hashlib

from intelhex import IntelHex
import pytest

from tools.run_g431_fixture_experiment import validate_backups, validate_fixture


def test_matching_full_backups_are_required(tmp_path):
    first, second = tmp_path / "a.bin", tmp_path / "b.bin"
    content = bytes(131072)
    first.write_bytes(content)
    second.write_bytes(content)
    assert validate_backups(first, second) == hashlib.sha256(content).hexdigest()
    second.write_bytes(content[:-1])
    with pytest.raises(ValueError, match="complete"):
        validate_backups(first, second)
    second.write_bytes(b"X" + content[1:])
    with pytest.raises(ValueError, match="differ"):
        validate_backups(first, second)


def test_unknown_or_nonflash_image_is_never_accepted(tmp_path):
    path = tmp_path / "bad.hex"
    image = IntelHex()
    image.frombytes(bytes(9408), offset=0x08000000)
    image.write_hex_file(str(path))
    with pytest.raises(ValueError, match="hash mismatch"):
        validate_fixture(path)
    image[0x1fff7800] = 0
    image.write_hex_file(str(path))
    with pytest.raises(ValueError, match="known contiguous"):
        validate_fixture(path)
