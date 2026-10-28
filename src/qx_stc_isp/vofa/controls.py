"""Own telemetry cards and manual command controls, without device access."""

from __future__ import annotations

from collections import deque

import math
import re
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from string import Formatter
from uuid import uuid4

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QImageReader, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea, QSizePolicy,
    QVBoxLayout, QWidget,
)

from ..appearance import FONT_MONO, scaled_px, stylesheet, theme_palette
from .extra_controls import AnalogInput, CubeView, DelayedButton, IndicatorLight, Joystick, PressReleaseButton
from .transmit import encode_send_payload
from .layout_handles import LayoutHandle, validate_control_rect


MAX_CONTROLS = 24
CONTROL_KINDS = ("value", "gauge", "circular_gauge", "button", "slider", "dial", "material_dial", "toggle", "delayed_button", "joystick", "light", "cube", "image")
_CONFIG_KEYS = {"kind", "channel", "title", "command"}
_KIND_LABELS = {"value": "数值", "gauge": "仪表", "circular_gauge": "环形仪表", "button": "命令", "slider": "滑块", "dial": "旋钮", "material_dial": "扁平旋钮", "toggle": "开关", "delayed_button": "长按按钮", "joystick": "摇杆", "light": "指示灯", "cube": "姿态", "image": "图像"}
_RANGE_SETTINGS = {"minimum", "maximum", "step", "precision"}
SENDING_CONTROL_KINDS = frozenset({"button", "slider", "dial", "material_dial", "toggle", "delayed_button", "joystick"})
_TX_SETTINGS = {"tx_mode", "tx_ending", "tx_encoding"}
_SETTING_KEYS = {
    "value": {"precision"}, "gauge": {"minimum", "maximum", "precision", "danger_min", "danger_max", "major_ticks"},
    "circular_gauge": {"minimum", "maximum", "precision", "danger_min", "danger_max", "start_angle", "end_angle", "major_ticks"},
    "button": {"press_command", "release_command"}, "slider": _RANGE_SETTINGS, "dial": _RANGE_SETTINGS,
    "material_dial": _RANGE_SETTINGS,
    "toggle": {"threshold", "threshold_inverted", "off_command"}, "delayed_button": {"delay_ms"},
    "joystick": _RANGE_SETTINGS | {"channel_y", "return_to_center"},
    "light": {"threshold", "threshold_inverted", "precision", "three_color", "lower_threshold", "upper_threshold", "low_color", "middle_color", "high_color", "on_color"}, "cube": {
        "channel_y", "channel_z", "precision", "quaternion", "channel_w", "radians",
        "roll_offset", "pitch_offset", "yaw_offset", "scale", "show_axes", "model_path",
    },
    "image": {"image_path", "image_source", "image_index"},
}


def _finite_number(value, label: str) -> float:
    if type(value) not in (int, float) or abs(value) > 1e12 or not math.isfinite(value):
        raise ValueError(f"{label}必须为绝对值不超过1e12的有限数")
    return float(value)


class _NumericFormatValue:
    """Finite format proxy admitting integral byte fields without expression evaluation."""

    def __init__(self, value: float) -> None:
        self.value = value

    def __format__(self, spec: str) -> str:
        if spec and spec[-1] in "dxbX":
            if not math.isfinite(self.value) or not float(self.value).is_integer():
                raise ValueError("整数或HEX数值字段要求当前值为整数")
            return format(int(self.value), spec)
        return format(self.value, spec)


def validate_command_template(command: str, names: set[str]) -> str:
    """Admit named numeric fields only, without conversion/member/index access."""
    try:
        for _literal, name, format_spec, conversion in Formatter().parse(command):
            if name is None:
                continue
            if name not in names or conversion is not None or "{" in format_spec or "}" in format_spec:
                raise ValueError("命令模板只允许指定的数值字段，不允许属性、索引或转换")
            if len(format_spec) > 16 or any(char not in "+- .0123456789efgEFGdxbX" for char in format_spec):
                raise ValueError("命令模板的数值格式无效")
            # Reject unbounded format widths before Python would allocate text.
            for part in format_spec.replace("+", "").replace("-", "").strip().rstrip("efgEFGdxbX").split("."):
                if part and (not part.isdigit() or int(part) > 32):
                    raise ValueError("数值格式宽度和精度不得超过32")
        rendered = command.format_map({name: _NumericFormatValue(0.0) for name in names})
    except (KeyError, ValueError, IndexError) as exc:
        raise ValueError(f"命令模板无效：{exc}") from exc
    if len(rendered.encode("utf-8")) > 16384:
        raise ValueError("命令模板展开不能超过16384字节")
    return rendered


@dataclass(frozen=True, slots=True)
class ControlSpec:
    kind: str
    channel: int = 0
    title: str = ""
    command: str = ""
    settings: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.kind, str) or self.kind not in CONTROL_KINDS:
            raise ValueError("未知的控件类型")
        if type(self.channel) is not int or not 0 <= self.channel <= 15:
            raise ValueError("绑定通道必须为 0–15 的整数")
        if not isinstance(self.title, str) or len(self.title) > 80:
            raise ValueError("控件标题不能超过80个字符")
        if not isinstance(self.command, str) or len(self.command) > 4096:
            raise ValueError("按钮命令不能超过4096个字符")
        allowed_settings = _SETTING_KEYS[self.kind] | {"rect"} | (_TX_SETTINGS if self.kind in SENDING_CONTROL_KINDS else set())
        if not isinstance(self.settings, dict) or set(self.settings) - allowed_settings:
            raise ValueError("控件参数含有未知字段或不适用于当前类型")
        settings = dict(self.settings)
        if "rect" in settings:
            settings["rect"] = list(validate_control_rect(settings["rect"]))
        if "tx_mode" in settings and settings["tx_mode"] not in ("text", "hex"):
            raise ValueError("控件发送格式必须为text或hex")
        if "tx_ending" in settings and settings["tx_ending"] not in ("none", "LF", "CRLF", "CR", "TAB"):
            raise ValueError("控件结束符必须为none、LF或CRLF")
        if settings.get("tx_encoding", "utf-8") not in ("utf-8", "gbk", "latin-1", "ascii", "utf-16le"):
            raise ValueError("控件编码无效")
        for key in ("minimum", "maximum", "step", "threshold", "lower_threshold", "upper_threshold", "roll_offset", "pitch_offset", "yaw_offset", "scale", "danger_min", "danger_max", "start_angle", "end_angle"):
            if key in settings:
                settings[key] = _finite_number(settings[key], key)
        minimum, maximum = settings.get("minimum", -1.0), settings.get("maximum", 1.0)
        if self.kind in {"gauge", "circular_gauge", "slider", "dial", "material_dial", "joystick"}:
            if minimum >= maximum:
                raise ValueError("控件最小值必须小于最大值")
        if self.kind in {"slider", "dial", "material_dial", "joystick"}:
            step = settings.get("step", .01)
            if step <= 0 or step > maximum - minimum or (maximum - minimum) / step > 100000:
                raise ValueError("步进必须大于0、不超过量程，且量程最多100000步")
            precision = settings.get("precision", 3)
            if type(precision) is int and 0 <= precision <= 9 and (
                round(minimum, precision) >= round(maximum, precision) or step < 10 ** -precision
            ):
                raise ValueError("小数位数不足以表示当前量程或步进")
        for key in ("channel_y", "channel_z", "channel_w"):
            if key in settings and (type(settings[key]) is not int or not 0 <= settings[key] <= 15):
                raise ValueError("附加通道必须为0–15的整数")
        if "precision" in settings and (type(settings["precision"]) is not int or not 0 <= settings["precision"] <= 9):
            raise ValueError("控件小数位数必须为0–9的整数")
        for key in ("return_to_center", "quaternion", "radians", "show_axes", "threshold_inverted", "three_color"):
            if key in settings and type(settings[key]) is not bool:
                raise ValueError(f"{key}必须为布尔值")
        if self.kind == "light" and settings.get("lower_threshold", 0) >= settings.get("upper_threshold", 1):
            raise ValueError("指示灯下阈值必须小于上阈值")
        for key in ("low_color", "middle_color", "high_color", "on_color"):
            if key in settings and (not isinstance(settings[key], str) or re.fullmatch(r"#[0-9a-fA-F]{6}", settings[key]) is None):
                raise ValueError("指示灯颜色必须为#RRGGBB")
        if "scale" in settings and not .1 <= settings["scale"] <= 2:
            raise ValueError("立方体显示尺度必须为0.1–2")
        if "major_ticks" in settings and (type(settings["major_ticks"]) is not int or not 2 <= settings["major_ticks"] <= 20):
            raise ValueError("仪表主刻度数必须为2–20的整数")
        for key in ("danger_min", "danger_max"):
            if key in settings and not minimum <= settings[key] <= maximum:
                raise ValueError("仪表危险区必须位于量程内")
        if settings.get("danger_min", minimum) > settings.get("danger_max", maximum):
            raise ValueError("仪表危险区起点不能大于终点")
        start_angle, end_angle = settings.get("start_angle", 225), settings.get("end_angle", -45)
        if self.kind == "circular_gauge" and not 1 <= abs(start_angle - end_angle) <= 360:
            raise ValueError("环形仪表角度跨度必须为1–360度")
        if "delay_ms" in settings and (type(settings["delay_ms"]) is not int or not 100 <= settings["delay_ms"] <= 60000):
            raise ValueError("长按时间必须为100–60000ms整数")
        if "off_command" in settings:
            if not isinstance(settings["off_command"], str) or len(settings["off_command"]) > 4096:
                raise ValueError("关断命令必须为不超过4096字符的文本")
            validate_command_template(settings["off_command"], {"value"})
        for key in ("press_command", "release_command"):
            if key in settings and (not isinstance(settings[key], str) or len(settings[key]) > 4096):
                raise ValueError("按下/松开命令必须为不超过4096字符的文本")
        if "image_path" in settings and (not isinstance(settings["image_path"], str) or len(settings["image_path"]) > 4096 or "\x00" in settings["image_path"]):
            raise ValueError("图像路径无效")
        if "model_path" in settings and (not isinstance(settings["model_path"], str) or len(settings["model_path"]) > 1024 or "\x00" in settings["model_path"]):
            raise ValueError("模型路径无效")
        if self.kind == "image":
            if settings.get("image_source", "local") not in ("local", "live"):
                raise ValueError("图像来源必须为local或live")
            if type(settings.get("image_index", 0)) is not int or not 0 <= settings.get("image_index", 0) <= 15:
                raise ValueError("实时图像索引必须为0–15的整数")
        if self.kind in SENDING_CONTROL_KINDS:
            fields = {"value"} if self.kind in {"slider", "dial", "material_dial", "toggle"} else {"x", "y"} if self.kind == "joystick" else set()
            edge_mode = self.kind == "button" and any(key in settings for key in ("press_command", "release_command"))
            commands = [] if edge_mode else [self.command]
            if "off_command" in settings:
                commands.append(settings["off_command"])
            commands.extend(settings[key] for key in ("press_command", "release_command") if key in settings)
            for command in commands:
                if not command:
                    continue
                prototype = validate_command_template(command, fields) if fields else command
                encode_send_payload(prototype, mode=settings.get("tx_mode", "text"),
                                    line_ending=settings.get("tx_ending", "none"), encoding=settings.get("tx_encoding", "utf-8"))
        # Copy caller state; mutating the input dictionary cannot alter a card.
        object.__setattr__(self, "settings", settings)

    @property
    def display_title(self) -> str:
        return self.title or (
            "发送命令" if self.kind in {"button", "delayed_button"} else f"{_KIND_LABELS[self.kind]} · CH{self.channel + 1}"
        )


def _plain_label(text: str = "") -> QLabel:
    label = QLabel(text)
    # Configuration is text, never a rich-text resource or a file/image URL.
    label.setTextFormat(Qt.PlainText)
    return label


class _Gauge(QWidget):
    """Read-only -1..1 dial; only its needle position is visually bounded."""

    def __init__(self, title: str, parent: QWidget | None = None, minimum: float = -1, maximum: float = 1,
                 *, settings: dict | None = None, circular: bool = False) -> None:
        super().__init__(parent)
        self._value: float | int | None = None
        self._palette = theme_palette("light")
        self._minimum, self._maximum = minimum, maximum
        self._settings = dict(settings or {})
        self._circular = circular
        self.setMinimumSize(120, 112)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setAccessibleName(title + "仪表")
        self.setAccessibleDescription(f"范围{minimum:g}到{maximum:g}；当前值缺失")

    @property
    def value(self) -> float | int | None:
        return self._value

    def set_value(self, value: float | int | None) -> None:
        self._value = value
        self.setAccessibleDescription(
            f"范围{self._minimum:g}到{self._maximum:g}；当前值" + ("缺失" if value is None else repr(value))
        )
        self.update()

    def set_theme(self, theme: str) -> None:
        self._palette = theme_palette(theme)
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        diameter = min(max(0, self.width() - 24), max(0, self.height() - 28))
        if diameter <= 0:
            return
        dial = QRectF((self.width() - diameter) / 2, 8, diameter, diameter)
        center, radius = dial.center(), diameter / 2
        start = self._settings.get("start_angle", 225) if self._circular else 225
        end = self._settings.get("end_angle", -45) if self._circular else -45
        span = end - start
        painter.setPen(QPen(QColor(self._palette.plot_grid), 6, Qt.SolidLine, Qt.RoundCap))
        painter.drawArc(dial, round(start * 16), round(span * 16))
        if "danger_min" in self._settings or "danger_max" in self._settings:
            danger_start = (self._settings.get("danger_min", self._minimum) - self._minimum) / (self._maximum - self._minimum)
            danger_end = (self._settings.get("danger_max", self._maximum) - self._minimum) / (self._maximum - self._minimum)
            painter.setPen(QPen(QColor("#d75b6d"), 6, Qt.SolidLine, Qt.RoundCap))
            painter.drawArc(dial, round((start + danger_start * span) * 16), round((danger_end - danger_start) * span * 16))
        ticks = self._settings.get("major_ticks", 5)
        painter.setPen(QPen(QColor(self._palette.plot_text), 1))
        for index in range(ticks):
            angle = math.radians(start + span * index / (ticks - 1))
            painter.drawLine(QPointF(center.x() + math.cos(angle) * radius * .82, center.y() - math.sin(angle) * radius * .82),
                             QPointF(center.x() + math.cos(angle) * radius * .94, center.y() - math.sin(angle) * radius * .94))
        if self._value is not None:
            fraction = (max(self._minimum, min(self._maximum, self._value)) - self._minimum) / (self._maximum - self._minimum)
            angle = math.radians(start + fraction * span)
            tip = QPointF(center.x() + math.cos(angle) * radius * 0.76,
                          center.y() - math.sin(angle) * radius * 0.76)
            in_danger = ("danger_min" in self._settings or "danger_max" in self._settings) and (
                self._settings.get("danger_min", self._minimum) <= self._value <= self._settings.get("danger_max", self._maximum)
            )
            active_colour = "#d75b6d" if in_danger else self._palette.accent
            painter.setPen(QPen(QColor(active_colour), 6, Qt.SolidLine, Qt.RoundCap))
            painter.drawArc(dial, round(start * 16), round(fraction * span * 16))
            if not self._circular:
                painter.setPen(QPen(QColor(active_colour), 2.5, Qt.SolidLine, Qt.RoundCap))
                painter.drawLine(center, tip)
                painter.drawEllipse(center, 3, 3)
        else:
            painter.setPen(QColor(self._palette.muted))
            painter.drawText(dial, Qt.AlignCenter, "—")
        painter.setPen(QColor(self._palette.plot_text))
        painter.drawText(QRectF(dial.left() - 5, self.height() - 20, 65, 18), Qt.AlignLeft, f"{self._minimum:g}")
        painter.drawText(QRectF(dial.right() - 60, self.height() - 20, 65, 18), Qt.AlignRight, f"{self._maximum:g}")


class _ControlCard(QFrame):
    def __init__(self, spec: ControlSpec, owner: ControlWorkspace) -> None:
        super().__init__()
        self.spec = spec
        self._owner = owner
        self._image_pixmap = None
        self._live_pixmap = None
        self._live_generation = None
        self.setObjectName("Card")
        self.setMinimumWidth(160)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.setAccessibleName(f"{_KIND_LABELS[spec.kind]}控件：{spec.display_title}")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(6)
        self.title_label = _plain_label(spec.display_title)
        self.title_label.setObjectName("SectionTitle")
        self.title_label.setWordWrap(True)
        self.title_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        layout.addWidget(self.title_label)
        self.channel_label = _plain_label(f"CH{spec.channel + 1}")
        self.channel_label.setObjectName("Muted")
        self.channel_label.setWordWrap(True)
        self.channel_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        layout.addWidget(self.channel_label)
        self.value_label: QLabel | None = None
        self.gauge: _Gauge | None = None
        self.button: QPushButton | None = None
        self.interactive: QWidget | None = None
        self.light: IndicatorLight | None = None
        self.cube: CubeView | None = None
        self.image_label: QLabel | None = None
        self.move_handle = LayoutHandle(self, owner)
        self.resize_handle = LayoutHandle(self, owner, resize=True)
        self.move_handle.hide()
        self.resize_handle.hide()
        if spec.kind in {"button", "delayed_button", "toggle"}:
            edge_commands = spec.kind == "button" and any(key in spec.settings for key in ("press_command", "release_command"))
            self.button = (DelayedButton(spec.display_title, spec.settings.get("delay_ms", 1000))
                           if spec.kind == "delayed_button" else PressReleaseButton(spec.display_title)
                           if edge_commands else QPushButton(spec.display_title))
            self.button.setAccessibleName(spec.display_title)
            self.button.setToolTip(spec.command)
            self.button.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            if spec.kind == "delayed_button":
                self.button.committed.connect(lambda: self._request(spec.command))
            elif spec.kind == "toggle":
                self.button.setCheckable(True)
                self.button.clicked.connect(self._toggle_command)
            elif edge_commands:
                self.button.press_committed.connect(lambda: self._request(spec.settings.get("press_command", "")))
                self.button.release_committed.connect(lambda: self._request(spec.settings.get("release_command", "")))
            else:
                self.button.clicked.connect(lambda _checked=False: self._request(spec.command))
            self.button.setFocusPolicy(Qt.StrongFocus)
            layout.addWidget(self.button)
        elif spec.kind == "image":
            self.image_label = _plain_label("未载入图片")
            self.image_label.setAlignment(Qt.AlignCenter)
            self.image_label.setMinimumHeight(120)
            self.image_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
            layout.addWidget(self.image_label, 1)
            if spec.settings.get("image_source", "local") == "live":
                index = spec.settings.get("image_index", 0)
                self.channel_label.setText(f"实时 · 图像 {index}")
                self.image_label.setText(f"实时图像 {index} · 等待帧")
                self.button = None
            else:
                self.button = QPushButton("载入本地图片…")
                self.button.clicked.connect(self._choose_image)
                layout.addWidget(self.button)
                # A restored path remains a draft until a new explicit file action.
                if spec.settings.get("image_path"):
                    self.image_label.setText("图片路径已恢复；点击载入")
        else:
            if spec.kind in {"gauge", "circular_gauge"}:
                self.gauge = _Gauge(spec.display_title, minimum=spec.settings.get("minimum", -1), maximum=spec.settings.get("maximum", 1), settings=spec.settings, circular=spec.kind == "circular_gauge")
                layout.addWidget(self.gauge, 1)
            elif spec.kind in {"slider", "dial", "material_dial"}:
                self.interactive = AnalogInput(spec.settings.get("minimum", -1), spec.settings.get("maximum", 1),
                    spec.settings.get("step", .01), spec.settings.get("precision", 3), dial=spec.kind == "dial", material=spec.kind == "material_dial")
                self.interactive.committed.connect(lambda value: self._request(spec.command, value=value))
                layout.addWidget(self.interactive)
            elif spec.kind == "joystick":
                self.interactive = Joystick(spec.settings.get("minimum", -1), spec.settings.get("maximum", 1),
                    spec.settings.get("step", .01), spec.settings.get("return_to_center", False))
                self.interactive.committed.connect(lambda x, y: self._request(spec.command, x=x, y=y))
                layout.addWidget(self.interactive, 1)
            elif spec.kind == "light":
                self.light = IndicatorLight(spec.settings.get("threshold", .5), spec.settings.get("threshold_inverted", False), settings=spec.settings)
                layout.addWidget(self.light, 1)
            elif spec.kind == "cube":
                self.cube = CubeView(radians=spec.settings.get("radians", False),
                    offsets=tuple(spec.settings.get(key, 0) for key in ("roll_offset", "pitch_offset", "yaw_offset")),
                    scale=spec.settings.get("scale", 1), show_axes=spec.settings.get("show_axes", True),
                    model_path=spec.settings.get("model_path", ""), load_external_model=False)
                layout.addWidget(self.cube, 1)
                self.model_button = QPushButton("载入本地模型…")
                self.model_button.setToolTip("恢复的模型路径不会自动读取；点击后选择文件载入。")
                self.model_button.clicked.connect(self._choose_model)
                layout.addWidget(self.model_button)
            self.value_label = _plain_label("—")
            self.value_label.setAlignment(Qt.AlignCenter)
            self.value_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self.value_label.setAccessibleName(spec.display_title + "当前数值")
            layout.addWidget(self.value_label)

    def _choose_model(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(self, "载入本地模型", "", "模型 (*.stl *.obj)")
        if filename:
            if not self.cube.load_model(filename):
                self._owner.error.emit("模型无法读取或格式无效")
                return
            self.spec = replace(self.spec, settings={**self.spec.settings, "model_path": filename})

    def _request(self, template: str, **values: float) -> None:
        if not template or not self.isEnabled() or not self._owner._transmit_enabled or self._owner._layout_editing:
            return
        try:
            if values:
                validate_command_template(template, set(values))
                text = template.format_map({name: _NumericFormatValue(value) for name, value in values.items()})
            else:
                text = template
            payload = encode_send_payload(text, mode=self.spec.settings.get("tx_mode", "text"),
                                          line_ending=self.spec.settings.get("tx_ending", "none"),
                                          encoding=self.spec.settings.get("tx_encoding", "utf-8"))
        except (KeyError, TypeError, ValueError, UnicodeError) as exc:
            self._owner.error.emit(f"控件命令无效：{exc}")
            return
        self._owner.packet_requested.emit(payload)
        # Kept for old read-only observers. Hosts should connect only the packet
        # signal to transport, otherwise their own two connections would send twice.
        self._owner.send_requested.emit(text)

    def _toggle_command(self, checked: bool) -> None:
        self._request(self.spec.command if checked else self.spec.settings.get("off_command", self.spec.command), value=1.0 if checked else 0.0)

    def _choose_image(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(self, "载入本地图片", self.spec.settings.get("image_path", ""), "图片 (*.jpg *.jpeg *.png *.bmp)")
        if not filename:
            return
        try:
            path = Path(filename)
            if not path.is_file() or not 0 < path.stat().st_size <= 16 * 1024 * 1024:
                raise ValueError("图片必须为不超过16MiB的普通文件")
            reader = QImageReader(str(path))
            size = reader.size()
            if not size.isValid() or size.width() * size.height() > 16_777_216:
                raise ValueError("图片尺寸无效或超过1600万像素")
            image = reader.read()
            if image.isNull():
                raise ValueError("图片解码失败")
            self._image_pixmap = QPixmap.fromImage(image)
            self.spec = replace(self.spec, settings={**self.spec.settings, "image_path": str(path)})
            self._update_image()
        except (OSError, ValueError) as exc:
            self._owner.error.emit(f"无法载入图片：{exc}")

    def _is_live_image(self) -> bool:
        return self.image_label is not None and self.spec.settings.get("image_source", "local") == "live"

    def refresh_live_image(self) -> None:
        if not self._is_live_image() or not self.isVisible():
            return
        store = self._owner.image_store
        index = self.spec.settings.get("image_index", 0)
        if store is None:
            self._show_image_placeholder("未连接实时图像缓存")
            return
        pixmap, error, generation = store.pixmap(index)
        if pixmap is None:
            self._live_pixmap = None
            self._live_generation = None
            self._show_image_placeholder(error or f"未收到图像 {index}")
            return
        if generation != self._live_generation:
            self._live_pixmap = pixmap
            self._live_generation = generation
            self._update_image()

    def _show_image_placeholder(self, text: str) -> None:
        if self.image_label is not None:
            self.image_label.setPixmap(QPixmap())
            self.image_label.setText(text)

    def _update_image(self) -> None:
        if self.image_label is None:
            return
        pixmap = self._live_pixmap if self._is_live_image() else self._image_pixmap
        if pixmap is not None:
            self.image_label.setPixmap(pixmap.scaled(self.image_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.refresh_live_image()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.move_handle.move(max(0, self.width() - 26), 3)
        self.resize_handle.move(max(0, self.width() - 24), max(0, self.height() - 24))
        self.move_handle.raise_()
        self.resize_handle.raise_()
        self._update_image()

    def set_samples(self, samples: tuple[float | int | None, ...], names: tuple[str, ...]) -> None:
        channel = self.spec.channel
        name = names[channel] if channel < len(names) and names[channel] else f"CH{channel + 1}"
        self.channel_label.setText(name)
        value = samples[channel] if channel < len(samples) else None
        if self.value_label is not None:
            precision = self.spec.settings.get("precision", 6)
            self.value_label.setText("—" if value is None else f"{value:.{precision}f}")
        if self.gauge is not None:
            self.gauge.set_value(value)
        if isinstance(self.interactive, AnalogInput):
            self.interactive.set_value(value)
        if isinstance(self.interactive, Joystick):
            y = self.spec.settings.get("channel_y", min(channel + 1, 15))
            self.interactive.set_values(value, samples[y] if y < len(samples) else None)
        if self.light is not None:
            self.light.set_value(value)
        if self.cube is not None:
            y, z = self.spec.settings.get("channel_y", min(channel + 1, 15)), self.spec.settings.get("channel_z", min(channel + 2, 15))
            if self.spec.settings.get("quaternion", False):
                w = self.spec.settings.get("channel_w", min(channel + 3, 15))
                self.cube.set_quaternion(samples[w] if w < len(samples) else None, value,
                    samples[y] if y < len(samples) else None, samples[z] if z < len(samples) else None)
            else:
                self.cube.set_values(value, samples[y] if y < len(samples) else None, samples[z] if z < len(samples) else None)
        if self.spec.kind == "toggle" and self.button is not None:
            threshold = self.spec.settings.get("threshold", .5)
            on = value is not None and (value <= threshold if self.spec.settings.get("threshold_inverted", False) else value >= threshold)
            self.button.setChecked(on)

    def set_transmit_enabled(self, enabled: bool) -> None:
        if self.interactive is not None:
            if not enabled and hasattr(self.interactive, "cancel"):
                self.interactive.cancel()
            self.interactive.setEnabled(enabled)
        if self.spec.kind in {"button", "toggle", "delayed_button"} and self.button is not None:
            if not enabled and isinstance(self.button, (DelayedButton, PressReleaseButton)):
                self.button.cancel()
            self.button.setEnabled(enabled)

    def set_theme(self, theme: str) -> None:
        palette = theme_palette(theme)
        if self.spec.kind == "toggle" and self.button is not None:
            self.button.setStyleSheet(
                f"QPushButton:checked {{background:{palette.accent};color:{palette.on_accent};border-color:{palette.accent};}}"
            )
        if isinstance(self.button, DelayedButton):
            self.button.set_theme(theme)
        if self.value_label is not None:
            self.value_label.setStyleSheet(
                f"font-family: {FONT_MONO}; font-size: {scaled_px(28 if not self.gauge else 22)}px;"
                f" font-weight: 650; color: {palette.accent};"
            )
        if self.gauge is not None:
            self.gauge.set_theme(theme)
        for widget in (self.interactive, self.light, self.cube):
            if widget is not None and hasattr(widget, "set_theme"):
                widget.set_theme(theme)
        for handle in (self.move_handle, self.resize_handle):
            handle.update()

    def detach(self) -> None:
        self.move_handle.cancel()
        self.resize_handle.cancel()
        if self.interactive is not None and hasattr(self.interactive, "cancel"):
            self.interactive.cancel()
        if isinstance(self.button, (DelayedButton, PressReleaseButton)):
            self.button.cancel()
        if self.button is not None:
            self.button.setEnabled(False)
            try:
                self.button.clicked.disconnect()
            except RuntimeError:
                pass
        self.hide()
        self.setParent(None)
        self.deleteLater()


class ControlWorkspace(QWidget):
    """Responsive cards; command buttons only request a manual send via signal.

    File persistence, deletion confirmation, the connection and encoding remain
    the host's responsibility. Neither adding/loading controls nor incoming
    telemetry sends commands or imports external plugins.
    """

    send_requested = Signal(str)
    packet_requested = Signal(bytes)
    error = Signal(str)
    configuration_changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Root")
        self.setAccessibleName("可视控件工作区")
        self._cards: list[_ControlCard] = []
        self._samples: tuple[float | int | None, ...] = ()
        self._names: tuple[str, ...] = ()
        self._theme = "light"
        self._image_store = None
        self._transmit_enabled = True
        self._layout_mode = "grid"
        self._layout_editing = False
        self._layout_undo = deque(maxlen=64)
        self._layout_redo = deque(maxlen=64)
        self._columns = 0
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        layout_tools = QHBoxLayout()
        self.layout_mode_combo = QComboBox()
        self.layout_mode_combo.addItem("自适应排列", "grid")
        self.layout_mode_combo.addItem("自由布局", "free")
        self.layout_mode_combo.currentIndexChanged.connect(lambda _index: self.set_layout_mode(self.layout_mode_combo.currentData()))
        layout_tools.addWidget(self.layout_mode_combo)
        self.edit_layout_check = QCheckBox("编辑布局")
        self.edit_layout_check.setToolTip("编辑时禁用控件发送；拖动标题角标移动，右下角拉伸，Esc取消。")
        self.edit_layout_check.toggled.connect(self.set_layout_editing)
        self.edit_layout_check.setEnabled(False)
        layout_tools.addWidget(self.edit_layout_check)
        self.undo_layout_button, self.redo_layout_button = QPushButton("↶"), QPushButton("↷")
        for button, title, callback in ((self.undo_layout_button, "撤销布局", self.undo_layout), (self.redo_layout_button, "重做布局", self.redo_layout)):
            button.setToolTip(title)
            button.setAccessibleName(title)
            button.setMaximumWidth(32)
            button.setEnabled(False)
            button.clicked.connect(callback)
            layout_tools.addWidget(button)
        layout_tools.addStretch()
        outer.addLayout(layout_tools)
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.viewport().installEventFilter(self)
        self._host = QWidget()
        self._host.setObjectName("Root")
        self._grid = QGridLayout(self._host)
        self._grid.setContentsMargins(5, 5, 5, 5)
        self._grid.setHorizontalSpacing(10)
        self._grid.setVerticalSpacing(10)
        self._scroll.setWidget(self._host)
        outer.addWidget(self._scroll)
        self.set_theme("light")

    @property
    def controls(self) -> tuple[_ControlCard, ...]:
        return tuple(self._cards)

    @property
    def count(self) -> int:
        return len(self._cards)

    @property
    def theme(self) -> str:
        return self._theme

    @property
    def image_store(self):
        return self._image_store

    def set_image_store(self, store) -> None:
        self._image_store = store
        self.refresh_live_images()

    def live_image_indices(self) -> set[int]:
        return {
            card.spec.settings.get("image_index", 0)
            for card in self._cards
            if card.spec.kind == "image" and card.spec.settings.get("image_source", "local") == "live"
        }

    def refresh_live_images(self) -> None:
        for card in self._cards:
            card.refresh_live_image()

    def _make_control(self, spec: ControlSpec) -> _ControlCard:
        card = _ControlCard(spec, self)
        card.dashboard_control_id = "control-" + uuid4().hex
        card.hide()
        card.set_theme(self._theme)
        card.set_samples(self._samples, self._names)
        card.set_transmit_enabled(self._transmit_enabled and not self._layout_editing)
        return card

    def add_control(self, kind: str, channel: int = 0, title: str = "", command: str = "", settings: dict | None = None) -> _ControlCard:
        spec = ControlSpec(kind, channel, title, command, settings or {})
        if self.count >= MAX_CONTROLS:
            raise ValueError(f"最多添加{MAX_CONTROLS}个可视控件")
        card = self._make_control(spec)
        self._cards.append(card)
        self._reflow()
        self.configuration_changed.emit()
        return card

    def update_control(self, index: int, *, kind=None, channel=None, title=None, command=None, settings=None) -> _ControlCard:
        if type(index) is not int or not 0 <= index < self.count:
            raise ValueError("控件索引无效")
        old = self._cards[index]
        spec = old.spec
        replacement = ControlSpec(kind if kind is not None else spec.kind,
            channel if channel is not None else spec.channel,
            title if title is not None else spec.title,
            command if command is not None else spec.command,
            settings if settings is not None else dict(spec.settings))
        staged = self._make_control(replacement)
        staged.dashboard_control_id = old.dashboard_control_id
        self._cards[index] = staged
        self._clear_layout_history()
        old.detach()
        self._reflow()
        self.configuration_changed.emit()
        return staged

    def remove_control(self, index: int) -> None:
        """Host obtains confirmation before removing an unsaved control."""
        if type(index) is not int or not 0 <= index < self.count:
            raise ValueError("控件索引无效")
        self._cards.pop(index).detach()
        self._clear_layout_history()
        self._reflow()
        self.configuration_changed.emit()

    def move_control(self, index: int, new_index: int) -> None:
        if type(index) is not int or type(new_index) is not int or not 0 <= index < self.count or not 0 <= new_index < self.count:
            raise ValueError("控件顺序索引无效")
        self._cards.insert(new_index, self._cards.pop(index))
        self._reflow()
        self.configuration_changed.emit()

    def clear_controls(self) -> None:
        # The host must obtain its own user confirmation before calling this.
        while self._grid.count():
            self._grid.takeAt(0)
        cards, self._cards = self._cards, []
        self._clear_layout_history()
        for card in cards:
            card.detach()
        self._reflow()
        self.configuration_changed.emit()

    def set_samples(self, samples: tuple, names=()) -> None:
        if not isinstance(samples, (tuple, list)) or not isinstance(names, (tuple, list)):
            raise ValueError("控件采样与名称必须是序列")
        if any(not isinstance(name, str) for name in names[:16]):
            raise ValueError("通道名称必须为文本")
        values: list[float | int | None] = []
        for value in samples[:16]:
            try:
                valid = type(value) in (int, float) and math.isfinite(value)
            except OverflowError:
                valid = False
            values.append(value if valid else None)
        self._samples = tuple(values)
        self._names = tuple(name[:80] for name in names[:16])
        for card in self._cards:
            card.set_samples(self._samples, self._names)

    def set_theme(self, theme: str) -> None:
        css = stylesheet(theme)
        self._theme = theme
        self.setStyleSheet(css)
        for card in self._cards:
            card.set_theme(theme)

    def set_transmit_enabled(self, enabled: bool) -> None:
        """The host supplies connection/job state; disabling cancels gestures."""
        if type(enabled) is not bool:
            raise ValueError("发送启用状态必须为布尔值")
        self._transmit_enabled = enabled
        for card in self._cards:
            card.set_transmit_enabled(enabled and not self._layout_editing)

    @property
    def layout_mode(self) -> str:
        return self._layout_mode

    def set_layout_mode(self, mode: str) -> None:
        if mode not in ("grid", "free"):
            raise ValueError("控件排列须为grid或free")
        self.set_layout_editing(False)
        self._layout_mode = mode
        self.edit_layout_check.setEnabled(mode == "free")
        self.layout_mode_combo.blockSignals(True)
        self.layout_mode_combo.setCurrentIndex(self.layout_mode_combo.findData(mode))
        self.layout_mode_combo.blockSignals(False)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded if mode == "free" else Qt.ScrollBarAlwaysOff)
        self._reflow()
        self.configuration_changed.emit()

    def set_layout_editing(self, enabled: bool) -> None:
        self._layout_editing = bool(enabled) and self._layout_mode == "free"
        if self.edit_layout_check.isChecked() != self._layout_editing:
            self.edit_layout_check.blockSignals(True)
            self.edit_layout_check.setChecked(self._layout_editing)
            self.edit_layout_check.blockSignals(False)
        for card in self._cards:
            card.set_transmit_enabled(self._transmit_enabled and not self._layout_editing)
            card.move_handle.setVisible(self._layout_editing)
            card.resize_handle.setVisible(self._layout_editing)
        self._refresh_layout_history_buttons()

    def _commit_geometry(self, card) -> None:
        if card not in self._cards or self._layout_mode != "free":
            return
        rect = card.geometry()
        values = [rect.x(), rect.y(), rect.width(), rect.height()]
        validate_control_rect(values)
        previous = list(card.spec.settings.get("rect", values))
        if previous != values:
            self._layout_undo.append((card, previous, values))
            self._layout_redo.clear()
        card.spec = replace(card.spec, settings={**card.spec.settings, "rect": values})
        self._update_canvas_extent()
        self._refresh_layout_history_buttons()
        if previous != values:
            self.configuration_changed.emit()

    def _clear_layout_history(self) -> None:
        self._layout_undo.clear()
        self._layout_redo.clear()
        self._refresh_layout_history_buttons()

    def _refresh_layout_history_buttons(self) -> None:
        self.undo_layout_button.setEnabled(self._layout_editing and bool(self._layout_undo))
        self.redo_layout_button.setEnabled(self._layout_editing and bool(self._layout_redo))

    def _restore_layout_step(self, record, *, redo: bool) -> None:
        card, old, new = record
        if card not in self._cards:
            return
        values = new if redo else old
        card.setGeometry(*values)
        card.spec = replace(card.spec, settings={**card.spec.settings, "rect": list(values)})
        card.raise_()
        self._update_canvas_extent()
        self.configuration_changed.emit()

    def undo_layout(self) -> None:
        if self._layout_editing and self._layout_undo:
            record = self._layout_undo.pop()
            self._restore_layout_step(record, redo=False)
            self._layout_redo.append(record)
            self._refresh_layout_history_buttons()

    def redo_layout(self) -> None:
        if self._layout_editing and self._layout_redo:
            record = self._layout_redo.pop()
            self._restore_layout_step(record, redo=True)
            self._layout_undo.append(record)
            self._refresh_layout_history_buttons()

    def _update_canvas_extent(self) -> None:
        if self._layout_mode == "free":
            width = max((card.geometry().right() + 20 for card in self._cards), default=200)
            height = max((card.geometry().bottom() + 20 for card in self._cards), default=120)
            self._host.setMinimumSize(width, height)

    def export_config(self) -> list[dict]:
        config = []
        for card in self._cards:
            item = asdict(card.spec)
            if not item["settings"]:
                del item["settings"]
            config.append(item)
        return config

    def load_config(self, config: list) -> None:
        if not isinstance(config, list) or len(config) > MAX_CONTROLS:
            raise ValueError(f"控件配置必须为不超过{MAX_CONTROLS}项的列表")
        specs = []
        for item in config:
            if not isinstance(item, dict) or set(item) not in (_CONFIG_KEYS, _CONFIG_KEYS | {"settings"}):
                raise ValueError("控件配置字段缺失或包含未知字段")
            specs.append(ControlSpec(**item))
        staged: list[_ControlCard] = []
        try:
            for spec in specs:
                staged.append(self._make_control(spec))
        except Exception:
            for card in staged:
                card.detach()
            raise
        self.clear_controls()
        self._cards = staged
        self._reflow()

    def _reflow(self) -> None:
        columns = 1 if self._scroll.viewport().width() < 440 else 2
        self._columns = columns
        while self._grid.count():
            self._grid.takeAt(0)
        if self._layout_mode == "free":
            for index, card in enumerate(self._cards):
                card.setParent(self._host)
                rect = card.spec.settings.get("rect", [12 + index % 2 * 280, 12 + index // 2 * 300, 260, 280])
                minimum = card.minimumSizeHint()
                x, y, width, height = validate_control_rect(rect)
                card.setGeometry(x, y, min(2000, max(width, minimum.width())), min(2000, max(height, minimum.height())))
                card.spec = replace(card.spec, settings={**card.spec.settings, "rect": [card.x(), card.y(), card.width(), card.height()]})
                card.show()
                card.move_handle.setVisible(self._layout_editing)
                card.resize_handle.setVisible(self._layout_editing)
            self._update_canvas_extent()
            return
        self._host.setMinimumSize(0, 0)
        for row in range(MAX_CONTROLS + 1):
            self._grid.setRowStretch(row, 0)
        for index, card in enumerate(self._cards):
            self._grid.addWidget(card, index // columns, index % columns)
            card.show()
        self._grid.setColumnStretch(0, 1)
        self._grid.setColumnStretch(1, 1 if columns == 2 else 0)
        self._grid.setRowStretch((self.count + columns - 1) // columns, 1)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._layout_mode != "grid":
            return
        columns = 1 if self._scroll.viewport().width() < 440 else 2
        if columns != self._columns:
            self._reflow()

    def eventFilter(self, watched, event) -> bool:
        if self._layout_mode == "grid" and watched is self._scroll.viewport() and event.type() == QEvent.Resize:
            columns = 1 if self._scroll.viewport().width() < 440 else 2
            if columns != self._columns:
                self._reflow()
        return super().eventFilter(watched, event)
