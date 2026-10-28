"""Schema migrations persist preferences, never connections or replay I/O."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import pytest

from qx_stc_isp.vofa.workspace import (
    _MAX_WORKSPACE_BYTES, _V4_FIELDS as V4_KEYS, _V5_FIELDS as V5_KEYS,
    WorkspaceSettings, load_workspace, save_workspace,
)


V1_KEYS = {"port", "baudrate", "decoder", "channel_names"}
V2_KEYS = {"visible_channels", "sample_interval_ms", "buffer_samples", "tx_mode", "tx_ending", "tx_text", "tx_interval_ms"}
V3_KEYS = {
    "source", "network_host", "network_port", "udp_peer_host", "udp_peer_port",
    "replay_path", "replay_speed", "replay_loop", "theme", "precision", "rx_display",
    "monitor_splitter_sizes", "plot_splitter_sizes",
}


def write_payload(tmp_path, payload):
    path = tmp_path / "workspace.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def payload(version=3):
    values = asdict(WorkspaceSettings())
    fields = V1_KEYS | (V2_KEYS if version >= 2 else set()) | (V3_KEYS if version >= 3 else set())
    # Roundtrip to represent actual JSON arrays rather than Python tuples.
    return json.loads(json.dumps({"schema_version": version, **{k: values[k] for k in fields}}))


def test_all_new_fields_roundtrip_in_schema_three(tmp_path):
    settings = WorkspaceSettings(
        port="COM91", baudrate=230400, decoder="JustFloat", channel_names=("温度", "电压"),
        visible_channels=(False, True) + (True,) * 14, sample_interval_ms=2.5,
        buffer_samples=20000, tx_mode="hex", tx_ending="CRLF", tx_text="01 FF", tx_interval_ms=500,
        source="udp", network_host="0.0.0.0", network_port=18888,
        udp_peer_host="127.0.0.1", udp_peer_port=18889,
        replay_path="F:/captures/采样.csv", replay_speed=2.5, replay_loop=True,
        theme="dark", precision=10, rx_display="text",
        monitor_splitter_sizes=(310, 750, 220), plot_splitter_sizes=(560, 280),
    )
    path = tmp_path / "saved.json"
    save_workspace(path, settings)
    assert load_workspace(path) == settings
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["schema_version"] == 8
    assert data["monitor_splitter_sizes"] == [310, 750, 220]
    assert data["plot_splitter_sizes"] == [560, 280]
    assert set(data) == {"schema_version", "swd", "tx_encoding", "serial_flow_control", "connection_presets", "serial_tools"} | V1_KEYS | V2_KEYS | V3_KEYS | V4_KEYS | V5_KEYS


@pytest.mark.parametrize("version", [1, 2])
def test_legacy_schemas_migrate_with_new_defaults(tmp_path, version):
    data = payload(version)
    data.update(port="COM88", baudrate=None, decoder="RawData", channel_names=["A"])
    settings = load_workspace(write_payload(tmp_path, data))
    assert settings.port == "COM88" and settings.baudrate is None
    assert settings.channel_names == ("A",)
    defaults = WorkspaceSettings()
    assert all(getattr(settings, key) == getattr(defaults, key) for key in V3_KEYS | V4_KEYS)
    destination = tmp_path / "upgraded.json"
    save_workspace(destination, settings)
    assert json.loads(destination.read_text(encoding="utf-8"))["schema_version"] == 8


@pytest.mark.parametrize("source", ["serial", "demo", "tcp_client", "tcp_server", "udp", "csv"])
def test_unfinished_valid_forms_are_preserved_not_connected(tmp_path, source):
    settings = WorkspaceSettings(
        source=source, network_host="", network_port=None,
        udp_peer_host="", udp_peer_port=12345, replay_path="",
    )
    path = tmp_path / "partial.json"
    save_workspace(path, settings)
    assert load_workspace(path) == settings
    # A configured peer address with an unset port is also a valid draft.
    assert WorkspaceSettings(source="udp", udp_peer_host="127.0.0.1", udp_peer_port=None).udp_peer_port is None


@pytest.mark.parametrize("change", [
    {"source": "network"}, {"source": []},
    {"network_host": None}, {"network_host": "localhost"}, {"network_host": "::1"},
    {"network_host": "127.0.0.1 "}, {"network_host": "999.1.1.1"},
    {"udp_peer_host": []}, {"udp_peer_host": "not-an-ip"},
    {"network_port": 0}, {"network_port": True}, {"network_port": "9000"},
    {"network_port": 65536}, {"udp_peer_port": -1}, {"udp_peer_port": 10.0},
    {"replay_path": 1}, {"replay_path": "X" * 4097}, {"replay_path": "a\x00b.csv"},
    {"replay_speed": True}, {"replay_speed": float("nan")}, {"replay_speed": float("inf")},
    {"replay_speed": 0.01}, {"replay_speed": 21}, {"replay_speed": 1 << 10000},
    {"replay_loop": 1}, {"theme": "system"}, {"theme": []},
    {"precision": True}, {"precision": -1}, {"precision": 13}, {"precision": "6"},
    {"rx_display": "binary"}, {"rx_display": []},
    {"monitor_splitter_sizes": [280, 700, 190]},
    {"monitor_splitter_sizes": (280, 700)},
    {"monitor_splitter_sizes": (0, 700, 190)},
    {"monitor_splitter_sizes": (280, True, 190)},
    {"monitor_splitter_sizes": (280, 700, 100001)},
    {"plot_splitter_sizes": (420, -1)},
    {"plot_splitter_sizes": (420, 260.0)},
    {"plot_splitter_sizes": (420, 260, 1)},
])
def test_new_settings_are_strictly_validated(change):
    with pytest.raises(ValueError):
        WorkspaceSettings(**change)


@pytest.mark.parametrize("key", sorted(V3_KEYS))
def test_schema_three_requires_every_new_field(tmp_path, key):
    data = payload()
    del data[key]
    with pytest.raises(ValueError, match="不完整"):
        load_workspace(write_payload(tmp_path, data))


@pytest.mark.parametrize("version", [1, 2, 3])
def test_all_versions_reject_unknown_fields(tmp_path, version):
    data = payload(version)
    data["auto_connect"] = True
    with pytest.raises(ValueError, match="未知字段"):
        load_workspace(write_payload(tmp_path, data))


@pytest.mark.parametrize("version", [True, 1.0, 2.0, 3.0, "3", None])
def test_schema_version_is_not_coerced(tmp_path, version):
    data = payload()
    data["schema_version"] = version
    with pytest.raises(ValueError, match="版本"):
        load_workspace(write_payload(tmp_path, data))


@pytest.mark.parametrize("key", ["channel_names", "visible_channels", "monitor_splitter_sizes", "plot_splitter_sizes"])
def test_json_array_fields_reject_non_arrays(tmp_path, key):
    data = payload()
    data[key] = "not-an-array"
    with pytest.raises(ValueError, match="数组字段"):
        load_workspace(write_payload(tmp_path, data))


@pytest.mark.parametrize("key,value", [
    ("network_port", "9000"), ("udp_peer_port", 0),
    ("monitor_splitter_sizes", [1, False, 3]),
    ("plot_splitter_sizes", [1, 100001]),
    ("theme", "unknown"), ("replay_loop", 1), ("replay_speed", float("nan")),
])
def test_loaded_invalid_values_are_not_silently_replaced(tmp_path, key, value):
    data = payload()
    data[key] = value
    with pytest.raises(ValueError):
        load_workspace(write_payload(tmp_path, data))


def test_loading_only_opens_selected_json_not_a_device_or_replay(tmp_path, monkeypatch):
    path = tmp_path / "preferences.json"
    settings = WorkspaceSettings(
        source="csv", replay_path=str(tmp_path / "never-open.csv"), replay_loop=True,
        network_host="192.0.2.50", network_port=9999,
        udp_peer_host="192.0.2.51", udp_peer_port=9998, tx_text="NEVER AUTO SEND",
    )
    save_workspace(path, settings)
    opened = []
    original = Path.open
    def guarded_open(selected, *args, **kwargs):
        assert selected == path, f"unexpected file access: {selected}"
        opened.append(selected)
        return original(selected, *args, **kwargs)
    def forbidden(*_args, **_kwargs):
        pytest.fail("workspace loading attempted device/network activity")
    monkeypatch.setattr(Path, "open", guarded_open)
    monkeypatch.setattr("socket.socket", forbidden)
    monkeypatch.setattr("socket.getaddrinfo", forbidden)
    monkeypatch.setattr("serial.Serial", forbidden)
    assert load_workspace(path) == settings
    assert opened == [path]
    assert not Path(settings.replay_path).exists()


def test_workspace_read_has_a_byte_limit(tmp_path):
    path = tmp_path / "large.json"
    path.write_bytes(b" " * (_MAX_WORKSPACE_BYTES + 1))
    with pytest.raises(ValueError, match="过大"):
        load_workspace(path)


def test_save_rejects_oversized_aggregate_before_replacing_existing_file(tmp_path, monkeypatch):
    path = tmp_path / "keep.json"
    path.write_text("original", encoding="ascii")
    # Keep the test small while exercising the same aggregate-byte check.
    monkeypatch.setattr("qx_stc_isp.vofa.workspace._MAX_WORKSPACE_BYTES", 1024)
    settings = WorkspaceSettings(tx_text="度" * 2000)
    with pytest.raises(ValueError, match="上限"):
        save_workspace(path, settings)
    assert path.read_text(encoding="ascii") == "original"
    assert list(tmp_path.iterdir()) == [path]


def test_extreme_legal_settings_roundtrip(tmp_path):
    settings = WorkspaceSettings(
        precision=0, replay_speed=0.1, network_port=65535, udp_peer_port=1,
        monitor_splitter_sizes=(1, 100000, 1), plot_splitter_sizes=(1, 100000),
    )
    path = tmp_path / "bounds.json"
    save_workspace(path, settings)
    assert load_workspace(path) == settings
    assert WorkspaceSettings(precision=12, replay_speed=20).precision == 12
