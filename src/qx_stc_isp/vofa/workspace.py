"""Explicitly saved monitor preferences; loading never opens a data source."""

from __future__ import annotations

import json
import ipaddress
import math
import os
import tempfile
import re
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any
from .json_data import load_json_document


_DECODERS = frozenset({"FireWater", "JustFloat", "RawData", "ImageStream", "FireWaterImage", "JustFloatImage"})
_MAX_WORKSPACE_BYTES = 2 * 1024 * 1024
_SOURCES = frozenset({"serial", "demo", "tcp_client", "tcp_server", "udp", "csv", "images", "stream", "matrix", "compat_demo", "swd"})
_V1_FIELDS = frozenset({"port", "baudrate", "decoder", "channel_names"})
_V2_FIELDS = frozenset({
    "visible_channels", "sample_interval_ms", "buffer_samples", "tx_mode",
    "tx_ending", "tx_text", "tx_interval_ms",
})
_V3_FIELDS = frozenset({
    "source", "network_host", "network_port", "udp_peer_host", "udp_peer_port",
    "replay_path", "replay_speed", "replay_loop", "theme", "precision",
    "rx_display", "monitor_splitter_sizes", "plot_splitter_sizes",
})
_V4_FIELDS = frozenset({
    "commands", "controls", "engine_manifests", "serial_bytesize", "serial_parity", "serial_stopbits",
    "image_directory", "image_fps", "image_loop", "image_rx", "demo_channels", "demo_interval_ms",
    "demo_amplitude", "demo_offset", "sequence_interval_ms", "sequence_repeat_count", "active_visual_tab",
    "themes",
    "plot_mode", "plot_xy_channels", "plot_grid", "plot_line_width", "plot_axis_ranges", "channel_colors",
    "rx_encoding", "show_rx", "show_tx", "show_timestamps", "terminal_font_size", "file_chunk_size", "file_interval_ms",
    "matrix_interval_ms",
    "image_channel", "image_effect", "image_effect_param", "image_rotation",
    "analysis_config",
    "channel_config",
    "controls_layout",
})
_V5_FIELDS = frozenset({"pages", "command_groups", "active_page_id", "compat_demo"})
_V8_FIELDS = frozenset({"tx_encoding", "serial_flow_control", "connection_presets", "serial_tools"})


def _validate_host(value: str, field: str) -> None:
    # Persist incomplete forms, not a connection request. Readiness (including
    # a nonempty endpoint and UDP peer pairing) belongs to the source worker.
    if not isinstance(value, str) or len(value) > 15:
        raise ValueError(f"{field}无效")
    if value:
        try:
            ipaddress.IPv4Address(value)
        except ipaddress.AddressValueError as exc:
            raise ValueError(f"{field}必须为 IPv4 地址或留空") from exc


def _validate_port(value: int | None, field: str) -> None:
    if value is not None and (type(value) is not int or not 1 <= value <= 65535):
        raise ValueError(f"{field}必须为 1–65535 的整数或未填写")


def _validate_sizes(value: tuple[int, ...], length: int, field: str) -> None:
    if (not isinstance(value, tuple) or len(value) != length
            or any(type(item) is not int or not 1 <= item <= 100000 for item in value)):
        raise ValueError(f"{field}必须是 {length} 个正整数（不超过 100000）")


@dataclass(frozen=True, slots=True)
class WorkspaceSettings:
    port: str = ""
    baudrate: int | None = None
    decoder: str = "FireWater"
    channel_names: tuple[str, ...] = ()
    visible_channels: tuple[bool, ...] = (True,) * 16
    sample_interval_ms: float = 1.0
    buffer_samples: int = 800
    tx_mode: str = "text"
    tx_ending: str = "none"
    tx_text: str = ""
    tx_interval_ms: int = 1000
    source: str = "serial"
    network_host: str = "127.0.0.1"
    network_port: int | None = None
    udp_peer_host: str = ""
    udp_peer_port: int | None = None
    replay_path: str = ""
    replay_speed: float = 1.0
    replay_loop: bool = False
    theme: str = "light"
    precision: int = 6
    rx_display: str = "hex"
    monitor_splitter_sizes: tuple[int, ...] = (280, 700, 190)
    plot_splitter_sizes: tuple[int, ...] = (750, 250)
    commands: tuple[dict, ...] = ()
    controls: tuple[dict, ...] = ()
    engine_manifests: tuple[dict, ...] = ()
    serial_bytesize: int = 8
    serial_parity: str = "N"
    serial_stopbits: float = 1
    image_directory: str = ""
    image_fps: float = 10
    image_loop: bool = False
    image_rx: bool = False
    demo_channels: int = 3
    demo_interval_ms: int = 100
    demo_amplitude: float = 1
    demo_offset: float = 0
    sequence_interval_ms: int = 1000
    sequence_repeat_count: int = 1
    active_visual_tab: int = 0
    themes: tuple[dict, ...] = ()
    plot_mode: str = "time"
    plot_xy_channels: tuple[int, int] = (0, 1)
    plot_grid: bool = True
    plot_line_width: float = 1.3
    plot_axis_ranges: tuple[float | None, ...] = (None, None, None, None)
    channel_colors: tuple[str | None, ...] = (None,) * 16
    rx_encoding: str = "utf-8"
    show_rx: bool = True
    show_tx: bool = True
    show_timestamps: bool = True
    terminal_font_size: int = 11
    file_chunk_size: int = 1024
    file_interval_ms: int = 20
    matrix_interval_ms: int = 100
    image_channel: int = 0
    image_effect: str = "none"
    image_effect_param: int = 0
    image_rotation: int = 0
    analysis_config: dict = field(default_factory=dict)
    channel_config: tuple[dict, ...] = ()
    controls_layout: str = "grid"
    pages: tuple[dict, ...] = ()
    command_groups: tuple[dict, ...] = ()
    active_page_id: str = ""
    compat_demo: dict = field(default_factory=dict)
    swd: dict = field(default_factory=dict)
    tx_encoding: str = "utf-8"
    serial_flow_control: str = "none"
    connection_presets: tuple[dict, ...] = ()
    serial_tools: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        from .serial_preferences import ENCODINGS, FLOW_CONTROLS, validate_presets
        if self.tx_encoding not in ENCODINGS or self.serial_flow_control not in FLOW_CONTROLS:
            raise ValueError("发送编码或流控无效")
        object.__setattr__(self, "connection_presets", validate_presets(self.connection_presets))
        if (not isinstance(self.serial_tools, dict) or set(self.serial_tools) - {
                "definition_text", "parameters_text", "channels_text", "identifier", "name"}
                or any(not isinstance(value, str) or len(value.encode("utf-8")) > 65536
                       for value in self.serial_tools.values())):
            raise ValueError("协议编辑草稿无效或过大")
        if self.serial_tools and set(self.serial_tools) != {
                "definition_text", "parameters_text", "channels_text", "identifier", "name"}:
            raise ValueError("协议编辑草稿字段不完整")
        from .serial_preferences import ConfigHistory
        ConfigHistory({"draft": self.serial_tools, "presets": self.connection_presets})
        object.__setattr__(self, "serial_tools", dict(self.serial_tools))
        from .swd import SwdCaptureConfig
        if not isinstance(self.swd, dict):
            raise ValueError("SWD配置必须为对象")
        try:
            object.__setattr__(self, "swd", SwdCaptureConfig(**self.swd).document())
        except TypeError as exc:
            raise ValueError("SWD配置字段不完整或包含未知字段") from exc
        if not isinstance(self.port, str) or len(self.port) > 256:
            raise ValueError("串口名称无效")
        if self.baudrate is not None and (
            isinstance(self.baudrate, bool)
            or not isinstance(self.baudrate, int)
            or not 1 <= self.baudrate <= 4_000_000
        ):
            raise ValueError("波特率无效")
        if not isinstance(self.decoder, str) or (self.decoder not in _DECODERS and re.fullmatch(r"plugin:[a-z][a-z0-9_.-]{0,63}", self.decoder) is None):
            raise ValueError("数据格式无效")
        if not isinstance(self.channel_names, tuple) or len(self.channel_names) > 256 or any(
            not isinstance(name, str) or len(name) > 128 for name in self.channel_names
        ):
            raise ValueError("通道名称无效")
        if (not isinstance(self.visible_channels, tuple) or len(self.visible_channels) != 16
                or any(type(item) is not bool for item in self.visible_channels)):
            raise ValueError("通道显隐配置无效")
        if (isinstance(self.sample_interval_ms, bool) or not isinstance(self.sample_interval_ms, (int, float))
                or not 0.001 <= self.sample_interval_ms <= 1_000_000 or not math.isfinite(self.sample_interval_ms)):
            raise ValueError("采样时间间隔无效")
        if type(self.buffer_samples) is not int or not 100 <= self.buffer_samples <= 100_000:
            raise ValueError("波形缓冲上限无效")
        if (not isinstance(self.tx_mode, str) or not isinstance(self.tx_ending, str)
                or self.tx_mode not in {"text", "hex"} or self.tx_ending not in {"none", "LF", "CRLF", "CR", "TAB"}):
            raise ValueError("发送格式无效")
        if not isinstance(self.tx_text, str) or len(self.tx_text) > 16384:
            raise ValueError("发送内容过长")
        if type(self.tx_interval_ms) is not int or not 20 <= self.tx_interval_ms <= 60000:
            raise ValueError("发送周期无效")
        if not isinstance(self.source, str) or self.source not in _SOURCES:
            raise ValueError("数据源无效")
        _validate_host(self.network_host, "网络主机")
        _validate_host(self.udp_peer_host, "UDP 对端")
        _validate_port(self.network_port, "网络端口")
        _validate_port(self.udp_peer_port, "UDP 对端端口")
        if not isinstance(self.replay_path, str) or len(self.replay_path) > 4096 or "\x00" in self.replay_path:
            raise ValueError("回放路径无效或超过 4096 个字符")
        if (isinstance(self.replay_speed, bool) or not isinstance(self.replay_speed, (int, float))
                or not 0.1 <= self.replay_speed <= 20 or not math.isfinite(self.replay_speed)):
            raise ValueError("回放速度必须为 0.1–20 的有限数")
        if type(self.replay_loop) is not bool:
            raise ValueError("循环回放配置无效")
        from ..appearance import THEMES, validate_theme_document
        if not isinstance(self.themes, tuple) or len(self.themes) > 16:
            raise ValueError("自定义主题配置最多16项")
        selected_themes = [validate_theme_document(item) for item in self.themes]
        if len({item['id'] for item in selected_themes}) != len(selected_themes):
            raise ValueError("自定义主题id重复")
        if not isinstance(self.theme, str) or self.theme not in set(THEMES) | {'custom:' + item['id'] for item in selected_themes}:
            raise ValueError("主题配置无效")
        if type(self.precision) is not int or not 0 <= self.precision <= 12:
            raise ValueError("小数位数必须为 0–12 的整数")
        if not isinstance(self.rx_display, str) or self.rx_display not in {"hex", "text"}:
            raise ValueError("接收显示模式无效")
        _validate_sizes(self.monitor_splitter_sizes, 3, "工作区分隔尺寸")
        _validate_sizes(self.plot_splitter_sizes, 2, "波形/日志分隔尺寸")
        if type(self.serial_bytesize) is not int or self.serial_bytesize not in (5, 6, 7, 8):
            raise ValueError("数据位必须为5、6、7、8")
        if self.serial_parity not in ("N", "E", "O", "M", "S"):
            raise ValueError("串口校验位无效")
        if type(self.serial_stopbits) not in (int, float) or self.serial_stopbits not in (1, 1.5, 2):
            raise ValueError("停止位必须为1、1.5、2")
        if not isinstance(self.image_directory, str) or len(self.image_directory) > 4096 or "\x00" in self.image_directory:
            raise ValueError("图像目录无效")
        if type(self.image_fps) not in (int, float) or not math.isfinite(self.image_fps) or not 0.1 <= self.image_fps <= 60:
            raise ValueError("图像回放帧率必须为0.1–60")
        if type(self.image_loop) is not bool or type(self.image_rx) is not bool:
            raise ValueError("图像选项必须为布尔值")
        if type(self.demo_channels) is not int or not 1 <= self.demo_channels <= 16:
            raise ValueError("演示通道数必须为1–16")
        if type(self.demo_interval_ms) is not int or not 10 <= self.demo_interval_ms <= 10000:
            raise ValueError("演示周期必须为10–10000ms")
        for value in (self.demo_amplitude, self.demo_offset):
            if type(value) not in (int, float) or not math.isfinite(value) or abs(value) > 1e12:
                raise ValueError("演示幅值/偏置无效")
        if type(self.sequence_interval_ms) is not int or not 20 <= self.sequence_interval_ms <= 60000:
            raise ValueError("命令序列间隔无效")
        if type(self.sequence_repeat_count) is not int or not 1 <= self.sequence_repeat_count <= 10000:
            raise ValueError("命令序列重复次数无效")
        if type(self.active_visual_tab) is not int or not 0 <= self.active_visual_tab <= 3:
            raise ValueError("可视页面无效")
        if self.plot_mode not in ("time", "xy") or not isinstance(self.plot_xy_channels, tuple) or len(self.plot_xy_channels) != 2 or any(type(value) is not int or not 0 <= value <= 15 for value in self.plot_xy_channels):
            raise ValueError("波形模式或XY绑定通道无效")
        if type(self.plot_grid) is not bool or type(self.plot_line_width) not in (int, float) or not .5 <= self.plot_line_width <= 5:
            raise ValueError("波形网格或线宽无效")
        if not isinstance(self.plot_axis_ranges, tuple) or len(self.plot_axis_ranges) != 4:
            raise ValueError("坐标轴上下限配置无效")
        for low, high in (self.plot_axis_ranges[:2], self.plot_axis_ranges[2:]):
            if (low is None) != (high is None):
                raise ValueError("坐标轴上下限必须成对")
            if low is not None and (any(type(value) not in (int, float) or not math.isfinite(value) for value in (low, high)) or low >= high or not math.isfinite(high - low)):
                raise ValueError("坐标轴范围必须有限且递增")
        if not isinstance(self.channel_colors, tuple) or len(self.channel_colors) != 16 or any(color is not None and (not isinstance(color, str) or re.fullmatch(r"#[0-9a-fA-F]{6}", color) is None) for color in self.channel_colors):
            raise ValueError("通道颜色必须为16项#RRGGBB或未自定义")
        if self.rx_encoding not in ("utf-8", "gbk", "latin-1", "ascii", "utf-16le"):
            raise ValueError("接收显示编码无效")
        if any(type(value) is not bool for value in (self.show_rx, self.show_tx, self.show_timestamps)):
            raise ValueError("终端显示选项必须为布尔值")
        if type(self.terminal_font_size) is not int or not 8 <= self.terminal_font_size <= 24:
            raise ValueError("终端字号必须为8–24")
        if type(self.file_chunk_size) is not int or not 1 <= self.file_chunk_size <= 16384 or type(self.file_interval_ms) is not int or not 20 <= self.file_interval_ms <= 60000:
            raise ValueError("文件分包大小或间隔无效")
        if type(self.matrix_interval_ms) is not int or not 1 <= self.matrix_interval_ms <= 60000:
            raise ValueError("矩阵回放周期必须为1–60000ms")
        if type(self.image_channel) is not int or not 0 <= self.image_channel <= 15:
            raise ValueError("图像通道必须为0–15")
        from .image_effects import IMAGE_EFFECTS
        if self.image_effect not in IMAGE_EFFECTS:
            raise ValueError("图像显示效果无效")
        if type(self.image_effect_param) is not int or not 0 <= self.image_effect_param <= 255:
            raise ValueError("图像效果参数必须为0–255的整数")
        if type(self.image_rotation) is not int or self.image_rotation not in (0, 90, 180, 270):
            raise ValueError("图像旋转必须为0、90、180、270度")
        from .analysis_view import validate_analysis_config
        validate_analysis_config(self.analysis_config)
        if self.controls_layout not in ("grid", "free"):
            raise ValueError("控件排列配置无效")
        from .channel_settings import ChannelDisplay
        if not isinstance(self.channel_config, tuple) or len(self.channel_config) not in (0, 16) or any(not isinstance(item, dict) for item in self.channel_config):
            raise ValueError("通道显示配置须为空或16个对象")
        try:
            for item in self.channel_config:
                ChannelDisplay(**item)
        except TypeError as exc:
            raise ValueError(f"通道显示配置含未知字段：{exc}") from exc
        from .commands import CommandEntry
        from .controls import ControlSpec, MAX_CONTROLS
        from .plugins import EngineManifest
        for name, values, maximum in (("命令", self.commands, 64), ("控件", self.controls, MAX_CONTROLS), ("扩展", self.engine_manifests, 32)):
            if not isinstance(values, tuple) or len(values) > maximum or any(not isinstance(item, dict) for item in values):
                raise ValueError(f"{name}配置格式或数量无效")
        try:
            for item in self.commands:
                CommandEntry(**item)
            for item in self.controls:
                ControlSpec(**item)
            engines = [EngineManifest.from_document(item) for item in self.engine_manifests]
            if len({entry.identifier for entry in engines}) != len(engines):
                raise ValueError("工作区扩展id重复")
        except TypeError as exc:
            raise ValueError(f"工作区配置字段不完整或未知：{exc}") from exc
        from .dashboard import DashboardCollection, DashboardPage, default_dashboard_pages
        from .commands import migrate_command_groups, validate_command_groups
        if not isinstance(self.pages, tuple) or any(not isinstance(item, dict) for item in self.pages):
            raise ValueError("工作区页面须为对象数组")
        pages = tuple(DashboardPage.from_document(item) for item in self.pages) if self.pages else default_dashboard_pages(self)
        DashboardCollection(pages)
        object.__setattr__(self, "pages", tuple(item.to_document() for item in pages))
        if not isinstance(self.command_groups, tuple) or any(not isinstance(item, dict) for item in self.command_groups):
            raise ValueError("工作区命令组须为对象数组")
        groups = validate_command_groups(self.command_groups) if self.command_groups else migrate_command_groups(self.commands)
        object.__setattr__(self, "command_groups", tuple(item.to_document() for item in groups))
        if not isinstance(self.active_page_id, str):
            raise ValueError("当前页面标识无效")
        active_id = self.active_page_id or pages[min(self.active_visual_tab, len(pages) - 1)].id
        if active_id not in {item.id for item in pages}:
            raise ValueError("当前页面标识未对应任何页面")
        object.__setattr__(self, "active_page_id", active_id)
        if not isinstance(self.compat_demo, dict):
            raise ValueError("Demo兼容设置须为对象")
        if self.compat_demo:
            from .compat_demo import CompatDemoConfig
            object.__setattr__(self, "compat_demo", CompatDemoConfig.from_dict(self.compat_demo).to_dict())


def save_workspace(path: str | Path, settings: WorkspaceSettings) -> None:
    """Atomically replace an explicitly selected JSON workspace file."""

    destination = Path(path)
    if not isinstance(settings, WorkspaceSettings):
        raise TypeError("工作区设置类型无效")
    # Revalidate nested mutable records immediately before serialization.
    settings = WorkspaceSettings(**{field: getattr(settings, field) for field in settings.__dataclass_fields__})
    payload = {"schema_version": 8, **asdict(settings)}
    payload["channel_names"] = list(settings.channel_names)
    payload["visible_channels"] = list(settings.visible_channels)
    payload["monitor_splitter_sizes"] = list(settings.monitor_splitter_sizes)
    payload["plot_splitter_sizes"] = list(settings.plot_splitter_sizes)
    serialized = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if len(serialized.encode("utf-8")) > _MAX_WORKSPACE_BYTES:
        raise ValueError("工作区内容超过 2 MiB 上限")
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", suffix=".tmp",
            prefix=f".{destination.name}.", dir=destination.parent, delete=False,
        ) as stream:
            temporary = stream.name
            stream.write(serialized)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary is not None and os.path.exists(temporary):
            os.unlink(temporary)


def load_workspace(path: str | Path) -> WorkspaceSettings:
    """Read preferences only; an imported port is never auto-connected."""

    source = Path(path)
    with source.open("rb") as stream:
        content = stream.read(_MAX_WORKSPACE_BYTES + 1)
    if len(content) > _MAX_WORKSPACE_BYTES:
        raise ValueError("工作区文件过大")
    value: Any = load_json_document(content, max_bytes=_MAX_WORKSPACE_BYTES)
    if (not isinstance(value, dict) or type(value.get("schema_version")) is not int
            or value["schema_version"] not in (1, 2, 3, 4, 5, 6, 7, 8)):
        raise ValueError("不支持的工作区文件版本")
    fields = _V1_FIELDS
    if value["schema_version"] >= 2:
        fields |= _V2_FIELDS
    if value["schema_version"] >= 3:
        fields |= _V3_FIELDS
    if value["schema_version"] >= 4:
        fields |= _V4_FIELDS
        if "image_effect_param" not in value:
            from .image_effects import effect_parameter
            value["image_effect_param"] = effect_parameter(value.get("image_effect", "none")).default
    if value["schema_version"] >= 5:
        fields |= _V5_FIELDS
    if value["schema_version"] >= 7:
        fields |= {"swd"}
    if value["schema_version"] >= 8:
        fields |= _V8_FIELDS
    if set(value) != fields | {"schema_version"}:
        raise ValueError("工作区字段不完整或包含未知字段")
    settings = {key: value[key] for key in fields}
    for key in ("channel_names", "visible_channels", "monitor_splitter_sizes", "plot_splitter_sizes", "commands", "controls", "engine_manifests", "themes", "plot_xy_channels", "plot_axis_ranges", "channel_colors", "channel_config", "pages", "command_groups", "connection_presets"):
        if key in settings:
            if not isinstance(settings[key], list):
                raise ValueError(f"工作区数组字段 {key} 无效")
            settings[key] = tuple(settings[key])
    if value["schema_version"] < 8:
        # Old manual/periodic TX used RX encoding; old commands remain UTF-8.
        settings["tx_encoding"] = settings.get("rx_encoding", "utf-8")
    result = WorkspaceSettings(**settings)
    if value['schema_version'] == 5:
        from copy import deepcopy
        from .dashboard import default_dashboard_pages
        defaults = default_dashboard_pages(result)[0].cards[0].config
        pages = deepcopy(result.pages)
        for page in pages:
            for card in page['cards']:
                if card['kind'] == 'waveform':
                    card['config'] = {**deepcopy(defaults), **card['config']}
        result = replace(result, pages=pages)
    return result
