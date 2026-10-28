"""Freeze-only Qt loading isolation must not break external tool discovery."""
import importlib.util
import os
from pathlib import Path
from types import ModuleType

import pytest


def entry_module():
    spec = importlib.util.spec_from_file_location("tested_app_entry", Path(__file__).resolve().parents[1] / "app_launcher.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def mock_gui(monkeypatch, entry, getter):
    module = ModuleType("qx_stc_isp.gui")
    def lookup(name):
        if name == "main":
            return getter()
        raise AttributeError(name)
    module.__getattr__ = lookup
    monkeypatch.setitem(entry.sys.modules, "qx_stc_isp.gui", module)


def test_frozen_qt_import_is_isolated_but_original_tool_path_is_restored(monkeypatch, tmp_path):
    entry = entry_module()
    original = r"C:\Unrelated\poppler\Library\bin;F:\OpenOCD\bin"
    monkeypatch.setenv("PATH", original)
    monkeypatch.setattr(entry.sys, "frozen", True, raising=False)
    monkeypatch.setattr(entry.sys, "_MEIPASS", str(tmp_path), raising=False)
    observed = []
    main = lambda: 0
    mock_gui(monkeypatch, entry, lambda: observed.append(os.environ["PATH"]) or main)
    assert entry.load_gui_main() is main
    assert "poppler" not in observed[0]
    assert str(tmp_path) in observed[0]
    assert os.environ["PATH"] == original


def test_failed_frozen_import_also_restores_path(monkeypatch, tmp_path):
    entry = entry_module()
    monkeypatch.setenv("PATH", "original-tool-path")
    monkeypatch.setattr(entry.sys, "frozen", True, raising=False)
    monkeypatch.setattr(entry.sys, "_MEIPASS", str(tmp_path), raising=False)
    def fail():
        raise ImportError("test DLL failure")
    mock_gui(monkeypatch, entry, fail)
    with pytest.raises(ImportError):
        entry.load_gui_main()
    assert os.environ["PATH"] == "original-tool-path"


def test_source_entry_does_not_change_search_path(monkeypatch):
    entry = entry_module()
    monkeypatch.setattr(entry.sys, "frozen", False, raising=False)
    monkeypatch.setenv("PATH", "source-tool-path")
    observed = []
    mock_gui(monkeypatch, entry, lambda: observed.append(os.environ["PATH"]) or (lambda: 0))
    entry.load_gui_main()
    assert observed == ["source-tool-path"]
