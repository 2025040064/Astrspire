from pathlib import Path

from tools import a2_f002_download


def test_unreadable_optional_recovery_tool_is_reported_not_crashed(tmp_path, monkeypatch):
    executable = tmp_path / "official.exe"
    executable.write_bytes(b"MZ")
    monkeypatch.setattr(a2_f002_download, "AICUBE", executable)
    def denied(*_args, **_kwargs):
        raise PermissionError("test-only denied file")
    monkeypatch.setattr(Path, "open", denied)
    assert "无法读取" in a2_f002_download.check_recovery_tool()
