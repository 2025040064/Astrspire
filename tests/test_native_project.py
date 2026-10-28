"""VOFA+ 1.3.10 native context import: bounded, settings-only, no guessing."""

from __future__ import annotations

import json

import pytest

from qx_stc_isp.vofa.native_project import load_native_context


def write_context(tmp_path, *, tabs=None, sub_cmds=None, version=100):
    config = {
        "type": "config", "vnumber": version,
        "ctx": {
            "text_buffer": {"encoder": "GBK", "list_size": 20},
            "datahub_global": {"global_decimal": 4},
            "wave_view": {"ctx": {
                "left_panel": {"ctx": {"pal": {"ctx": {
                    "protocol_combo": "JustFloat", "link_type_combo": 0,
                    "serial": {"port": "COM7", "baud": "921600", "databits": "8",
                               "parity": "Even", "stopbits": "1"},
                }}}},
                "settingsPanel": {"ctx": {".": {"settings_ctx": [
                    {"name": "A", "is_draw": True, "scale": 2, "yoffset": 1, "decimal": 3},
                ]}}},
                "color_bar": {"ctx": {".": {"delta_t": 0.5, "buffer_max_size_input.text": "2000"}}},
                "tx_rx_zone": {"ctx": {".": {"hex_on": False, "cmd_hex": "hi", "loop_ms": 250}}},
            }},
        },
    }
    (tmp_path / "vofa+.config.json").write_text(json.dumps(config), encoding="utf-8")
    (tmp_path / "vofa+.tabviews.json").write_text(json.dumps({
        "type": "tabviews", "vnumber": version,
        "ctx": [{"tabs": tabs or []}]}), encoding="utf-8")
    (tmp_path / "vofa+.cmds.json").write_text(json.dumps({
        "type": "cmds", "vnumber": version,
        "ctx": {"subCmds": sub_cmds or []}}), encoding="utf-8")


def test_native_context_maps_known_settings(tmp_path):
    write_context(tmp_path)
    settings = load_native_context(tmp_path / "vofa+.config.json")
    assert (settings.port, settings.baudrate, settings.decoder, settings.source) == ("COM7", 921600, "JustFloat", "serial")
    assert (settings.serial_parity, settings.serial_stopbits, settings.rx_encoding) == ("E", 1.0, "gbk")
    assert (settings.sample_interval_ms, settings.buffer_samples, settings.precision) == (0.5, 2000, 4)
    assert settings.channel_names[0] == "A" and settings.visible_channels[0] is True
    assert settings.channel_config[0]["scale"] == 2 and settings.channel_config[0]["offset"] == 1
    assert settings.channel_config[0]["precision"] == 3
    assert (settings.tx_mode, settings.tx_text, settings.tx_interval_ms) == ("text", "hi", 250)


def test_native_components_and_bad_versions_are_rejected(tmp_path):
    write_context(tmp_path, tabs=[{"name": "wave"}])
    with pytest.raises(ValueError, match="卡页"):
        load_native_context(tmp_path)
    write_context(tmp_path, sub_cmds=[{"name": "g"}])
    with pytest.raises(ValueError, match="命令组"):
        load_native_context(tmp_path)
    write_context(tmp_path, version=99)
    with pytest.raises(ValueError, match="vnumber"):
        load_native_context(tmp_path)


def test_native_missing_files_and_paths(tmp_path):
    with pytest.raises(ValueError, match="缺少"):
        load_native_context(tmp_path)


@pytest.mark.parametrize("field,value,label", [
    ("link_type_combo", 99, "连接类型"),
    ("encoder", "UTF-16", "编码"),
])
def test_native_unknown_connection_or_encoding_is_not_silently_replaced(tmp_path, field, value, label):
    write_context(tmp_path)
    config_path = tmp_path / "vofa+.config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    target = (config["ctx"]["text_buffer"] if field == "encoder" else
              config["ctx"]["wave_view"]["ctx"]["left_panel"]["ctx"]["pal"]["ctx"])
    target[field] = value
    config_path.write_text(json.dumps(config), encoding="utf-8")
    with pytest.raises(ValueError, match=label):
        load_native_context(tmp_path)


def test_native_inspection_reports_unsupported_paths_without_losing_supported_settings(tmp_path):
    from qx_stc_isp.vofa.native_project import inspect_native_context
    write_context(tmp_path, tabs=[{"name": "wave"}, {"name": "image"}], sub_cmds=[{"name": "group"}])
    report = inspect_native_context(tmp_path)
    assert report.candidate.port == "COM7"
    assert not report.complete and report.can_apply_partial
    assert [(item.path, item.count) for item in report.unsupported] == [
        ("vofa+.tabviews.json.ctx[0].tabs", 2), ("vofa+.cmds.json.ctx.subCmds", 1)]
    assert report.errors == () and len(report.supported) >= 4


@pytest.mark.parametrize("node", [None, 3, "bad", []])
def test_native_malformed_nested_node_is_a_readable_report(tmp_path, node):
    from qx_stc_isp.vofa.native_project import inspect_native_context
    write_context(tmp_path)
    config_path = tmp_path / "vofa+.config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["ctx"]["wave_view"]["ctx"]["left_panel"] = node
    config_path.write_text(json.dumps(config), encoding="utf-8")
    report = inspect_native_context(tmp_path)
    assert report.candidate is None and not report.complete and not report.can_apply_partial
    assert report.errors and "left_panel" in report.errors[0].path
    assert report.errors[0].message


def test_native_non_boolean_visibility_is_rejected(tmp_path):
    write_context(tmp_path)
    config_path = tmp_path / "vofa+.config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    wave = config["ctx"]["wave_view"]["ctx"]
    wave["settingsPanel"]["ctx"]["."]["settings_ctx"][0]["is_draw"] = "false"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    with pytest.raises(ValueError, match="显隐"):
        load_native_context(tmp_path)


def test_native_float_version_is_not_an_integer_schema_version(tmp_path):
    write_context(tmp_path, version=100.0)
    with pytest.raises(ValueError, match="vnumber"):
        load_native_context(tmp_path)


@pytest.mark.parametrize("data_bits", [8.0, 8.5, True, "8.5"])
def test_native_data_bits_do_not_truncate_or_accept_boolean(tmp_path, data_bits):
    write_context(tmp_path)
    config_path = tmp_path / "vofa+.config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["ctx"]["wave_view"]["ctx"]["left_panel"]["ctx"]["pal"]["ctx"]["serial"]["databits"] = data_bits
    config_path.write_text(json.dumps(config), encoding="utf-8")
    with pytest.raises(ValueError, match="数据位"):
        load_native_context(tmp_path)


def test_native_context_requires_utf8_not_auto_detected_utf16(tmp_path):
    write_context(tmp_path)
    config_path = tmp_path / "vofa+.config.json"
    text = config_path.read_text(encoding="utf-8")
    config_path.write_bytes(text.encode("utf-16"))
    with pytest.raises(ValueError, match="UTF-8"):
        load_native_context(tmp_path)
