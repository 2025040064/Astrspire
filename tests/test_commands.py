"""Inert command-list persistence; no test may send or open a device."""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError

import pytest

from qx_stc_isp.vofa.commands import CommandEntry, load_commands, save_commands


@pytest.fixture(autouse=True)
def no_devices(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("command model must not open serial/network devices")

    monkeypatch.setattr("serial.Serial", forbidden)
    monkeypatch.setattr("socket.socket", forbidden)


def entry_data(**changes):
    result = {"name": "读取", "payload": "READ", "mode": "text", "ending": "none"}
    result.update(changes)
    return result


def write_document(path, document):
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    return path


def test_model_defaults_are_frozen_inert_values():
    entry = CommandEntry("读取", "READ")
    assert entry.mode == "text" and entry.ending == "none"
    with pytest.raises(FrozenInstanceError):
        entry.payload = "WRITE"


@pytest.mark.parametrize("values", [
    {"name": ""}, {"name": " "}, {"name": "x" * 81}, {"name": 1}, {"name": "\ud800"},
    {"payload": ""}, {"payload": None}, {"payload": 1}, {"payload": "\ud800"},
    {"mode": "TEXT"}, {"mode": []}, {"ending": "lf"}, {"ending": []},
    {"mode": "hex", "payload": "F"}, {"mode": "hex", "payload": "0xGG"},
    {"mode": "hex", "payload": "  "}, {"payload": "x" * 16385},
    {"payload": "中" * 5462}, {"payload": "x" * 16384, "ending": "LF"},
])
def test_model_rejects_invalid_entries(values):
    with pytest.raises(ValueError):
        CommandEntry(**entry_data(**values))


@pytest.mark.parametrize("ending", ["none", "LF", "CRLF"])
def test_text_and_hex_entries_use_existing_encoder(ending):
    assert CommandEntry("文本", "测试", ending=ending).payload == "测试"
    assert CommandEntry("HEX", "01 0A ff", mode="hex", ending=ending).mode == "hex"


def test_roundtrip_preserves_unicode_and_payload_without_sending(tmp_path):
    entries = (CommandEntry("温度读取", "READ 温度", ending="CRLF"), CommandEntry("字节请求", "01 0A ff", "hex", "LF"))
    path = tmp_path / "commands.json"
    save_commands(path, entries)
    assert load_commands(path) == entries
    text = path.read_text(encoding="utf-8")
    assert "温度读取" in text and "\\u6e29" not in text
    data = json.loads(text)
    assert data["schema_version"] == 2 and len(data["commands"]) == 2


def test_empty_and_maximum_lists_are_valid(tmp_path):
    for count in (0, 64):
        path = tmp_path / f"{count}.json"
        entries = tuple(CommandEntry(str(index), "x") for index in range(count))
        save_commands(path, iter(entries))
        assert load_commands(path) == entries


def test_save_never_overwrites_existing_file(tmp_path):
    path = tmp_path / "existing.json"
    path.write_bytes(b"existing evidence")
    with pytest.raises(FileExistsError):
        save_commands(path, (CommandEntry("x", "x"),))
    assert path.read_bytes() == b"existing evidence"


@pytest.mark.parametrize("entries", [None, ["not an entry"], [entry_data()], [CommandEntry("x", "x")] * 65])
def test_save_validates_all_entries_before_creating_file(tmp_path, entries):
    path = tmp_path / "not-created.json"
    with pytest.raises(ValueError):
        save_commands(path, entries)
    assert not path.exists()


def test_save_rechecks_tampered_frozen_object_before_creation(tmp_path):
    path = tmp_path / "tampered.json"
    entry = CommandEntry("x", "x")
    object.__setattr__(entry, "mode", "invalid")
    with pytest.raises(ValueError):
        save_commands(path, (entry,))
    assert not path.exists()


def test_save_bounds_iterator_consumption(tmp_path):
    calls = []

    def entries():
        while True:
            calls.append(True)
            yield CommandEntry("x", "x")

    with pytest.raises(ValueError, match="64"):
        save_commands(tmp_path / "too-many.json", entries())
    assert len(calls) == 65


def test_save_rejects_oversized_json_before_creating_file(tmp_path):
    # Long, legal spaced HEX has a small encoded payload but its JSON text
    # exceeds the independent file-size cap when repeated in a 64-entry list.
    path = tmp_path / "large.json"
    command = CommandEntry("x", "01 " * 16384, mode="hex")
    with pytest.raises(ValueError, match="1 MiB"):
        save_commands(path, (command,) * 64)
    assert not path.exists()


@pytest.mark.parametrize("document", [
    [], {}, {"schema_version": 1, "commands": [], "extra": 0},
    {"schema_version": True, "commands": []}, {"schema_version": "1", "commands": []},
    {"schema_version": 3, "commands": []}, {"schema_version": 1, "commands": {}},
    {"schema_version": 1, "commands": [entry_data()] * 65},
    {"schema_version": 1, "commands": [entry_data(extra=1)]},
    {"schema_version": 1, "commands": [{"name": "x", "payload": "x"}]},
    {"schema_version": 1, "commands": ["x"]},
    {"schema_version": 1, "commands": [entry_data(payload=[]) ]},
])
def test_load_rejects_wrong_schema_types_and_keys(tmp_path, document):
    path = write_document(tmp_path / "bad.json", document)
    with pytest.raises(ValueError):
        load_commands(path)


@pytest.mark.parametrize("content", [
    b"\xff\xff", b"not json", b'{"schema_version":1,"schema_version":1,"commands":[]}',
    b'{"schema_version":1,"commands":[{"name":"x","payload":"x","payload":"y","mode":"text","ending":"none"}]}',
    b'{"schema_version":NaN,"commands":[]}',
])
def test_load_rejects_non_utf8_malformed_or_ambiguous_json(tmp_path, content):
    path = tmp_path / "bad.json"
    path.write_bytes(content)
    with pytest.raises(ValueError):
        load_commands(path)


def test_load_bounds_file_size_before_parsing(tmp_path):
    path = tmp_path / "large.json"
    path.write_bytes(b" " * (1024 * 1024 + 1))
    with pytest.raises(ValueError, match="1 MiB"):
        load_commands(path)


def test_load_rejects_directory(tmp_path):
    with pytest.raises(ValueError, match="普通 JSON"):
        load_commands(tmp_path)
