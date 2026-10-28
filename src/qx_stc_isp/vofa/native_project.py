"""Read-only adapter for VOFA+ 1.3.10 context files (vnumber 100).

Only settings that can be mapped without guessing are imported.  Native tabs
and command groups have a different model from this application, so a context
containing them is rejected instead of being silently dropped.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

from .channel_settings import ChannelDisplay
from .json_data import load_json_document
from .workspace import WorkspaceSettings

MAX_CONTEXT_BYTES = 1024 * 1024
_VNUMBER = 100
_DECODERS = {"FireWater", "JustFloat", "RawData"}
_PARITY = {"None": "N", "Odd": "O", "Even": "E", "Mark": "M", "Space": "S"}


@dataclass(frozen=True, slots=True)
class NativeImportIssue:
    path: str
    message: str
    count: int = 1


@dataclass(frozen=True, slots=True)
class NativeImportReport:
    """An inert mapping preview, never a request to connect or send."""

    candidate: WorkspaceSettings | None
    supported: tuple[NativeImportIssue, ...] = ()
    unsupported: tuple[NativeImportIssue, ...] = ()
    errors: tuple[NativeImportIssue, ...] = ()
    ignored: tuple[NativeImportIssue, ...] = ()

    @property
    def complete(self) -> bool:
        return self.candidate is not None and not self.unsupported and not self.errors

    @property
    def can_apply_partial(self) -> bool:
        return self.candidate is not None and not self.errors


class _ContextError(ValueError):
    def __init__(self, path: str, message: str):
        self.path = path
        super().__init__(message)


def _object_at(value, *keys: str, base: str):
    current, path = value, base
    if not isinstance(current, dict):
        raise _ContextError(path, "VOFA+ 节点必须为对象")
    for key in keys:
        path += "." + key
        if key not in current or not isinstance(current[key], dict):
            raise _ContextError(path, f"VOFA+ 节点 {path} 缺少或不是对象")
        current = current[key]
    return current


def _read_document(path: Path, expected_type: str):
    if not path.is_file():
        raise ValueError(f"缺少 {path.name}")
    if path.stat().st_size > MAX_CONTEXT_BYTES:
        raise ValueError(f"{path.name} 超过 1 MiB 上限")
    try:
        with path.open("rb") as stream:
            content = stream.read(MAX_CONTEXT_BYTES + 1)
        document = load_json_document(content.decode("utf-8-sig"), max_bytes=MAX_CONTEXT_BYTES)
    except (OSError, UnicodeError, ValueError) as exc:
        raise _ContextError(path.name, f"{path.name} 不是有效的有界 UTF-8 JSON：{exc}") from exc
    if not isinstance(document, dict) or document.get("type") != expected_type:
        raise ValueError(f"{path.name} 类型不是 {expected_type}")
    if type(document.get("vnumber")) is not int or document["vnumber"] != _VNUMBER:
        raise ValueError(f"{path.name} 版本不是 vnumber {_VNUMBER}")
    return document


def _number(value, label: str, *, low: float, high: float) -> float:
    if isinstance(value, str):
        try:
            value = float(value)
        except ValueError as exc:
            raise ValueError(f"VOFA+ {label}不是数值") from exc
    if type(value) not in (int, float) or abs(value) > 1e12 or not low <= value <= high:
        raise ValueError(f"VOFA+ {label}超出允许范围")
    return float(value)


def _integer(value, label: str, *, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"VOFA+ {label}超出允许范围")
    return value


def _inspect_native_context(path: str | Path) -> NativeImportReport:
    selected = Path(path)
    base = selected if selected.is_dir() else selected.parent
    config = _read_document(base / "vofa+.config.json", "config")
    tabs = _read_document(base / "vofa+.tabviews.json", "tabviews")
    commands = _read_document(base / "vofa+.cmds.json", "cmds")

    tab_entries = tabs.get("ctx")
    if not isinstance(tab_entries, list):
        raise ValueError("VOFA+ tabviews 结构无效")
    unsupported = []
    ignored = []
    palette_shape = {'protocol_combo':None, 'link_type_combo':None,
        'serial':dict.fromkeys(('port','baud','databits','parity','stopbits'))}
    wave_shape = {
        'left_panel':{'ctx':{'pal':{'ctx':palette_shape}}},
        'settingsPanel':{'ctx':{'.':{'settings_ctx':[dict.fromkeys(('name','is_draw','scale','yoffset','decimal'))]}}},
        'color_bar':{'ctx':{'.':dict.fromkeys(('delta_t','buffer_max_size_input.text'))}},
        'tx_rx_zone':{'ctx':{'.':dict.fromkeys(('hex_on','cmd_hex','loop_ms'))}}}
    shape = {'type':None,'vnumber':None,'ctx':{
        'text_buffer':{'encoder':None,'list_size':'ignored'},
        'datahub_global':{'global_decimal':None}, 'wave_view':{'ctx':wave_shape}}}
    def inspect_fields(value, spec, path):
        if isinstance(value, dict) and isinstance(spec, dict):
            for key, item in value.items():
                child_path = path + '.' + key
                if key not in spec:
                    unsupported.append(NativeImportIssue(child_path, '未映射字段；仅显式部分导入时忽略'))
                elif spec[key] == 'ignored':
                    ignored.append(NativeImportIssue(child_path, '已知原生显示缓存设置，不迁移到本软件'))
                elif spec[key] is not None:
                    inspect_fields(item, spec[key], child_path)
        elif isinstance(value, list) and isinstance(spec, list):
            for i, item in enumerate(value):
                inspect_fields(item, spec[0], f'{path}[{i}]')
    inspect_fields(config, shape, 'vofa+.config.json')
    inspect_fields(tabs, {'type':None,'vnumber':None,'ctx':[{'tabs':None}]}, 'vofa+.tabviews.json')
    inspect_fields(commands, {'type':None,'vnumber':None,'ctx':{'subCmds':None}}, 'vofa+.cmds.json')
    for index, entry in enumerate(tab_entries):
        item_path = f"vofa+.tabviews.json.ctx[{index}]"
        if not isinstance(entry, dict) or not isinstance(entry.get("tabs", []), list):
            raise _ContextError(item_path, "VOFA+ 卡页节点必须为含 tabs 数组的对象")
        if entry.get("tabs"):
            unsupported.append(NativeImportIssue(item_path + ".tabs",
                "原生卡页/控件缺少非空配置证据，未映射", len(entry["tabs"])))
    command_ctx = commands.get("ctx")
    if not isinstance(command_ctx, dict) or not isinstance(command_ctx.get("subCmds", []), list):
        raise _ContextError("vofa+.cmds.json.ctx.subCmds", "VOFA+ 命令组必须为数组")
    if command_ctx.get("subCmds"):
        unsupported.append(NativeImportIssue("vofa+.cmds.json.ctx.subCmds",
            "原生命令组缺少非空配置证据，未映射", len(command_ctx["subCmds"])))

    root = _object_at(config, "ctx", base="vofa+.config.json")
    wave = _object_at(root, "wave_view", "ctx", base="vofa+.config.json.ctx")
    wave_path = "vofa+.config.json.ctx.wave_view.ctx"
    palette = _object_at(wave, "left_panel", "ctx", "pal", "ctx", base=wave_path)
    serial = _object_at(palette, "serial", base=wave_path + ".left_panel.ctx.pal.ctx")

    decoder = palette.get("protocol_combo", "FireWater")
    if decoder not in _DECODERS:
        raise ValueError(f"不支持的 VOFA+ 数据引擎：{decoder}")
    port = serial.get("port", "")
    if not isinstance(port, str) or len(port) > 32:
        raise ValueError("VOFA+ 串口名称无效")
    baud_text = serial.get("baud", "")
    try:
        baudrate = int(str(baud_text))
    except ValueError as exc:
        raise ValueError("VOFA+ 波特率无效") from exc
    if not 300 <= baudrate <= 4_000_000:
        raise ValueError("VOFA+ 波特率超出范围")
    data_bits = serial.get("databits", 8)
    if isinstance(data_bits, str) and data_bits.isascii() and data_bits.isdecimal():
        data_bits = int(data_bits)
    bytesize = _integer(data_bits, "数据位", low=5, high=8)
    parity = _PARITY.get(serial.get("parity", "None"))
    if parity is None:
        raise ValueError("VOFA+ 校验位无效")
    stopbits = _number(serial.get("stopbits", "1"), "停止位", low=1, high=2)

    color_bar = _object_at(wave, "color_bar", "ctx", ".", base=wave_path)
    interval = _number(color_bar.get("delta_t", 1), "Δt", low=.001, high=1_000_000)
    try:
        buffer_samples = int(str(color_bar.get("buffer_max_size_input.text", "800")))
    except ValueError as exc:
        raise ValueError("VOFA+ 缓冲上限无效") from exc
    buffer_samples = _integer(buffer_samples, "缓冲上限", low=100, high=100_000)

    datahub = _object_at(root, "datahub_global", base="vofa+.config.json.ctx")
    precision = _integer(datahub.get("global_decimal", 6), "小数位数", low=0, high=12)
    text_buffer = _object_at(root, "text_buffer", base="vofa+.config.json.ctx")
    encoding = str(text_buffer.get("encoder", "UTF-8")).casefold()
    if encoding not in {"utf-8", "gbk", "latin-1"}:
        raise _ContextError("vofa+.config.json.ctx.text_buffer.encoder", f"不支持的 VOFA+ 编码：{encoding}")

    panel = (_object_at(wave, "settingsPanel", "ctx", ".", base=wave_path)
             if "settingsPanel" in wave else {})
    channel_items = panel.get("settings_ctx", [])
    if not isinstance(channel_items, list) or len(channel_items) > 16:
        raise ValueError("VOFA+ 通道配置无效")
    names = [""] * 16
    visible = [True] * 16
    displays = []
    for index in range(16):
        item = channel_items[index] if index < len(channel_items) else {}
        if not isinstance(item, dict):
            raise ValueError("VOFA+ 通道配置项无效")
        name = item.get("name", "")
        if not isinstance(name, str) or len(name) > 64:
            raise ValueError("VOFA+ 通道名称过长")
        names[index] = name
        is_draw = item.get("is_draw", True)
        if type(is_draw) is not bool:
            raise _ContextError(f"{wave_path}.settingsPanel.ctx..settings_ctx[{index}].is_draw",
                                "VOFA+ 通道显隐必须为布尔值")
        visible[index] = is_draw
        decimal = item.get("decimal")
        if "decimal" in item and (type(decimal) is not int or not 0 <= decimal <= 12):
            raise _ContextError(f"{wave_path}.settingsPanel.ctx..settings_ctx[{index}].decimal",
                                "通道小数位必须为0–12的整数；未知继承标记未映射")
        displays.append(ChannelDisplay(
            name=name,
            scale=_number(item.get("scale", 1), "通道比例", low=-1e9, high=1e9),
            offset=_number(item.get("yoffset", 0), "通道偏移", low=-1e9, high=1e9),
            precision=decimal,
        ))

    tx = (_object_at(wave, "tx_rx_zone", "ctx", ".", base=wave_path)
          if "tx_rx_zone" in wave else {})
    tx_path = wave_path + ".tx_rx_zone.ctx.."
    if type(tx.get("hex_on", False)) is not bool:
        raise _ContextError(tx_path + "hex_on", "发送HEX开关必须为布尔值")
    tx_mode = "hex" if tx.get("hex_on", False) else "text"
    tx_text = tx.get("cmd_hex", "")
    if not isinstance(tx_text, str):
        raise _ContextError(tx_path + "cmd_hex", "发送内容必须为字符串")
    if len(tx_text) > 16384:
        raise ValueError("VOFA+ 发送内容超过 16 KiB")
    loop_ms = tx.get("loop_ms", 1000)
    if type(loop_ms) is not int or not 20 <= loop_ms <= 60000:
        raise _ContextError(tx_path + "loop_ms", "发送周期必须为20–60000毫秒的整数")
    tx_interval = loop_ms

    link_type = palette.get("link_type_combo", 0)
    if type(link_type) is not int or link_type not in (0, 4):
        raise _ContextError(wave_path + ".left_panel.ctx.pal.ctx.link_type_combo",
                            f"不支持的 VOFA+ 连接类型：{link_type}")
    source = "demo" if link_type == 4 else "serial"
    settings = WorkspaceSettings(
        port=port,
        baudrate=baudrate,
        decoder=decoder,
        channel_names=tuple(names),
        visible_channels=tuple(visible),
        sample_interval_ms=interval,
        buffer_samples=buffer_samples,
        tx_mode=tx_mode,
        tx_text=tx_text,
        tx_interval_ms=tx_interval,
        source=source,
        serial_bytesize=bytesize,
        serial_parity=parity,
        serial_stopbits=stopbits,
        rx_encoding=encoding,
        precision=precision,
        channel_config=tuple(asdict(item) for item in displays),
    )
    return NativeImportReport(settings, supported=(
        NativeImportIssue(wave_path + ".left_panel.ctx.pal.ctx", "连接参数和数据引擎；不自动连接"),
        NativeImportIssue(wave_path + ".color_bar.ctx..", "采样间隔与历史容量"),
        NativeImportIssue(wave_path + ".settingsPanel.ctx..settings_ctx", "通道换算和显隐", len(channel_items)),
        NativeImportIssue("vofa+.config.json.ctx.text_buffer.encoder", "终端字符编码"),
        NativeImportIssue("vofa+.config.json.ctx.datahub_global.global_decimal", "显示精度"),
        NativeImportIssue(wave_path + ".tx_rx_zone.ctx..", "发送设置；不自动发送"),
    ), unsupported=tuple(unsupported), ignored=tuple(ignored))


def inspect_native_context(path: str | Path) -> NativeImportReport:
    """Read three bounded JSON files, returning diagnostics without UI effects."""
    try:
        return _inspect_native_context(path)
    except (ValueError, OSError, TypeError, OverflowError) as exc:
        return NativeImportReport(None, errors=(NativeImportIssue(
            getattr(exc, "path", "vofa+.config.json"), str(exc)),))


def load_native_context(path: str | Path) -> WorkspaceSettings:
    """Strict legacy entry point: incomplete projects must be previewed first."""
    report = inspect_native_context(path)
    if not report.complete:
        issues = report.errors + report.unsupported
        raise ValueError("；".join(f"{item.path}: {item.message} ({item.count})" for item in issues))
    assert report.candidate is not None
    return report.candidate
