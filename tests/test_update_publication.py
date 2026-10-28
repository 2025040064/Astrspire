"""Public source completeness and portable runtime paths, without a device."""
import importlib.util
from pathlib import Path
import zipfile

import pytest

from qx_stc_isp.updates import config


def test_update_feed_uses_the_approved_public_repository():
    assert config.RELEASE_REPO == "2025040064/Astrspire"
    assert config.LATEST_RELEASE_URL == "https://api.github.com/repos/2025040064/Astrspire/releases/latest"


def test_installer_does_not_default_to_c_profile():
    root = Path(__file__).resolve().parents[1]
    installer = (root / "installer/Astrspire.iss").read_text(encoding="utf-8")
    assert "DefaultDirName={src}\\Astrspire" in installer


def test_installer_shortcuts_are_opt_in():
    root = Path(__file__).resolve().parents[1]
    installer = (root / "installer/Astrspire.iss").read_text(encoding="utf-8")
    tasks = installer.split("[Tasks]", 1)[1].split("[Files]", 1)[0]
    icons = installer.split("[Icons]", 1)[1].split("[Run]", 1)[0]
    for line in tasks.splitlines():
        if line.startswith("Name:"):
            assert "Flags: unchecked" in line
    for line in icons.splitlines():
        if line.startswith("Name:"):
            assert "Tasks:" in line


def test_monitor_log_location_uses_installation_data_directory(qtbot, monkeypatch, tmp_path):
    from qx_stc_isp.gui import MainWindow
    local_data = tmp_path / "data" / "local"
    monkeypatch.setenv("ASTRSPIRE_LOCALAPPDATA", str(local_data))
    window = MainWindow()
    qtbot.addWidget(window)
    assert window.log_location_label.toolTip() == str(local_data / "logs")
    window.close()


@pytest.mark.parametrize("layout", ["versions/0.7.0/Astrspire.exe", "Astrspire.exe",
                                  "portable/Astrspire.exe", "AstrspireLauncher.exe"])
def test_runtime_data_stays_with_actual_installation(monkeypatch, tmp_path, layout):
    monkeypatch.delenv("ASTRSPIRE_INSTALL_ROOT", raising=False)
    monkeypatch.delenv("ASTRSPIRE_APPDATA", raising=False)
    monkeypatch.delenv("ASTRSPIRE_LOCALAPPDATA", raising=False)
    executable = tmp_path / layout
    monkeypatch.setattr(config.sys, "frozen", True, raising=False)
    monkeypatch.setattr(config.sys, "executable", str(executable))
    expected = tmp_path if layout.startswith("versions/") else executable.parent
    assert config.install_root() == expected
    assert config.app_data_dir() == expected / "data" / "app"
    assert config.local_data_dir() == expected / "data" / "local"


def test_source_archive_includes_regression_tests_and_runtime_examples(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("publication_release_builder", root / "tools/build_update_release.py")
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    fixture = tmp_path / "source"
    for name in ("src/module.py", "tests/test_example.py", "examples/led.hex", "firmware/test.c",
                 "pyproject.toml", "uv.lock", "README.md", "app_launcher.py",
                 ".python-version", ".gitignore", "CLAUDE.md", "assets/fonts/README.md",
                 "assets/fonts/PingFang SC Regular.ttf", "tools/.env", "tools/signing.pem"):
        path = fixture / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture", encoding="utf-8")
    monkeypatch.setattr(builder, "ROOT", fixture)
    output = tmp_path / "source.zip"
    builder.write_source_zip(output, "0.7.0")
    with zipfile.ZipFile(output) as archive:
        paths = {name.split("/", 1)[1] for name in archive.namelist()}
    assert {"tests/test_example.py", "examples/led.hex", "firmware/test.c",
            ".python-version", ".gitignore", "CLAUDE.md"} <= paths
    assert not {"tools/.env", "tools/signing.pem", "assets/fonts/PingFang SC Regular.ttf"} & paths


def test_release_build_cannot_collect_dlls_from_unrelated_path(monkeypatch):
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("isolated_release_builder", root / "tools/build_update_release.py")
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    monkeypatch.setenv("PATH", r"C:\Unrelated\poppler\Library\bin")
    monkeypatch.setenv("PYTHONPATH", r"C:\Unrelated\packages")
    calls = []
    monkeypatch.setattr(builder.subprocess, "run", lambda *args, **kwargs: calls.append((args, kwargs)))
    builder.run([str(builder.PYTHON), "-m", "PyInstaller", "--version"])
    environment = calls[0][1].get("env")
    assert environment is not None, "Build subprocesses need an explicit DLL search environment"
    assert "Unrelated" not in environment["PATH"]
    assert "PYTHONPATH" not in environment
    assert str(root / ".venv" / "Scripts") in environment["PATH"]
    assert Path(environment["TEMP"]).is_relative_to(root)
    assert Path(environment["PYINSTALLER_CONFIG_DIR"]).is_relative_to(root)
