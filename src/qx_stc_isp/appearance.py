"""Small, self-owned application palettes; applying a theme performs no I/O."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import re
from string import Template


THEMES = ("light", "warm", "dark", "eye-care", "midnight", "high-contrast")
THEME_LABELS = {"light": "浅色", "warm": "暖黑", "dark": "深色",
                "eye-care": "护眼", "midnight": "夜蓝", "high-contrast": "高对比"}

# design.md §4.1 的三族字体：界面无衬线、展示衬线、数字等宽。
FONT_BODY = ('"Inter", "Segoe UI Variable Text", "Segoe UI", "PingFang SC", '
             '"Microsoft YaHei UI", "Microsoft YaHei", sans-serif')
FONT_DISPLAY = 'Georgia, "Noto Serif SC", "Songti SC", SimSun, serif'
FONT_MONO = '"Cascadia Mono", "SF Mono", Consolas, "Courier New", monospace'
FONT_FAMILIES = ("Inter", "Segoe UI Variable Text", "Segoe UI", "PingFang SC",
                 "Microsoft YaHei UI", "Microsoft YaHei")

# 界面文字大小：只缩放字号与跟随字号的固定宽度，不改布局结构。
TEXT_SCALES = (("compact", 0.9), ("normal", 1.0), ("comfortable", 1.12), ("large", 1.2))
TEXT_SCALE_LABELS = {"compact": "紧凑", "normal": "标准", "comfortable": "舒适", "large": "大"}
_BASE_FONT_POINTS = 10
_SCALE_LIMITS = (0.85, 1.35)


@dataclass(frozen=True, slots=True)
class ThemePalette:
    name: str
    background: str
    surface: str
    text: str
    muted: str
    accent: str
    border: str
    rail: str
    nav_text: str
    nav_selected: str
    input_background: str
    input_border: str
    selection: str
    button: str
    button_hover: str
    button_border: str
    disabled_background: str
    disabled_text: str
    primary: str
    primary_hover: str
    power_background: str
    power_border: str
    power_text: str
    safety: str
    badge_background: str
    badge_border: str
    scrollbar: str
    scrollbar_handle: str
    plot_grid: str
    plot_text: str
    plot_cursor: str
    focus: str
    on_accent: str
    channel_colors: tuple[str, ...]


# 暖黑 × 鼠尾草绿 × 杏橙：色值取自 design.md 的语义 token，未另建近似色。
_WARM = ThemePalette(
    name="warm", background="#1c1815", surface="#25211e", text="#f0ece3",
    muted="#b5ae9f", accent="#aec9a0", border="#443c33", rail="#1c1815",
    nav_text="#b5ae9f", nav_selected="#2d2822", input_background="#1c1815",
    input_border="#8f826f", selection="#3c4634", button="#2d2822",
    button_hover="#38322a", button_border="#443c33", disabled_background="#221e1a",
    disabled_text="#8c8679", primary="#aec9a0", primary_hover="#c0d8b2",
    power_background="#362f25", power_border="#75613d", power_text="#ddb76c",
    safety="#ddb76c", badge_background="#362f25", badge_border="#75613d",
    scrollbar="#1c1815", scrollbar_handle="#443c33", plot_grid="#3a342c",
    plot_text="#b5ae9f", plot_cursor="#ddb76c", focus="#ddb76c", on_accent="#1c1815",
    channel_colors=("#aec9a0", "#e9ac7d", "#abc8e5", "#ddb76c", "#f2a49a", "#5e7856"),
)


_LIGHT = ThemePalette(
    name="light", background="#ffffff", surface="#ffffff", text="#182638",
    muted="#68798c", accent="#0860af", border="#dfe5eb", rail="#e4e8ed",
    nav_text="#315e82", nav_selected="#ffffff", input_background="#ffffff",
    input_border="#c6d1dc", selection="#d8eaff", button="#f8fafc",
    button_hover="#eaf3fc", button_border="#cdd8e3", disabled_background="#f1f3f5",
    disabled_text="#788795", primary="#1171c4", primary_hover="#2785d5",
    power_background="#d9f5eb", power_border="#56b797", power_text="#086e53",
    safety="#a65b14", badge_background="#fff2e4", badge_border="#efc794",
    scrollbar="#f1f3f6", scrollbar_handle="#b8c5d1", plot_grid="#e4e9ef",
    plot_text="#6b7e90", plot_cursor="#7c8d9c", focus="#0860af", on_accent="#ffffff",
    channel_colors=("#d51f75", "#168a42", "#245ec7", "#d4840d", "#8c3bb3", "#078b98"),
)

_DARK = ThemePalette(
    name="dark", background="#111827", surface="#182334", text="#e5edf7",
    muted="#a6b4c8", accent="#71baff", border="#334257", rail="#0e1726",
    nav_text="#a1c8ec", nav_selected="#223954", input_background="#101a29",
    input_border="#45566e", selection="#274b72", button="#202e42",
    button_hover="#2b4160", button_border="#45566e", disabled_background="#1c2736",
    disabled_text="#8795a8", primary="#136fbd", primary_hover="#2488d8",
    power_background="#163c34", power_border="#408c77", power_text="#8be2c1",
    safety="#ffd088", badge_background="#34291f", badge_border="#70533a",
    scrollbar="#111c2b", scrollbar_handle="#52657f", plot_grid="#334257",
    plot_text="#b3c4d9", plot_cursor="#c3d6ec", focus="#71baff", on_accent="#ffffff",
    channel_colors=("#ff6eae", "#61d98c", "#78a9ff", "#ffbd61", "#d596ff", "#55d8e4"),
)


_PRESETS = {
    "warm": _WARM, "light": _LIGHT, "dark": _DARK,
    "eye-care": replace(_LIGHT, name="eye-care", background="#f3f6e9", surface="#f8faef",
        input_background="#fcfff5", rail="#e4ead5", plot_grid="#dbe3cd", accent="#326548"),
    "midnight": replace(_DARK, name="midnight", background="#101629", surface="#17213b",
        input_background="#111a30", rail="#0c1225", accent="#a5b9ff", primary="#485da9"),
    "high-contrast": replace(_DARK, name="high-contrast", background="#000000", surface="#000000",
        text="#ffffff", muted="#dddddd", accent="#ffff00", plot_text="#ffffff", border="#ffffff",
        input_background="#000000", input_border="#ffffff", plot_grid="#666666", primary="#164c94",
        focus="#ffff00"),
}
_CUSTOM_THEMES: dict[str, tuple[ThemePalette, dict]] = {}


def validate_theme_document(document: dict) -> dict:
    if not isinstance(document, dict) or set(document) != {"id", "name", "base", "colors"}:
        raise ValueError("主题需要id、name、base和colors字段")
    identifier, name, base, colors = (document[key] for key in ("id", "name", "base", "colors"))
    if not isinstance(identifier, str) or re.fullmatch(r"[a-z][a-z0-9_-]{0,39}", identifier) is None:
        raise ValueError("主题id必须为小写字母开头的1–40个字母、数字、下划线或横线")
    if not isinstance(name, str) or not name.strip() or len(name) > 40:
        raise ValueError("主题名称无效")
    if not isinstance(base, str) or base not in THEMES or not isinstance(colors, dict):
        raise ValueError("主题底色或颜色表无效")
    allowed = set(ThemePalette.__dataclass_fields__) - {"name"}
    if set(colors) - allowed:
        raise ValueError("主题包含未知颜色字段")
    canonical = {}
    for key, value in colors.items():
        selected = value if key == "channel_colors" else [value]
        if not isinstance(selected, (list, tuple)) or not 1 <= len(selected) <= 16:
            raise ValueError("通道颜色必须为1–16项")
        if any(not isinstance(item, str) or re.fullmatch(r"#[0-9a-fA-F]{6}", item) is None for item in selected):
            raise ValueError("颜色必须使用#RRGGBB格式")
        canonical[key] = list(selected) if key == "channel_colors" else selected[0]
    return {"id": identifier, "name": name, "base": base, "colors": canonical}


def register_custom_theme(document: dict) -> str:
    value = validate_theme_document(document)
    key = "custom:" + value["id"]
    if key not in _CUSTOM_THEMES and len(_CUSTOM_THEMES) >= 16:
        raise ValueError("最多注册16个自定义主题")
    colors = dict(value["colors"])
    if "channel_colors" in colors:
        colors["channel_colors"] = tuple(colors["channel_colors"])
    palette = replace(_PRESETS[value["base"]], name=key, **colors)
    _CUSTOM_THEMES[key] = (palette, value)
    return key


def custom_theme_documents() -> tuple[dict, ...]:
    return tuple({**document, "colors": {**document["colors"]}} for _, document in _CUSTOM_THEMES.values())


def theme_choices() -> tuple[tuple[str, str], ...]:
    return tuple(THEME_LABELS.items()) + tuple((key, value[1]["name"]) for key, value in _CUSTOM_THEMES.items())


def validate_theme(theme: str) -> str:
    if not isinstance(theme, str) or theme not in _PRESETS and theme not in _CUSTOM_THEMES:
        raise ValueError("未知主题，请先载入主题配置")
    return theme


def theme_palette(theme: str = "light") -> ThemePalette:
    """Return immutable colours; unknown choices never silently change state."""
    key = validate_theme(theme)
    return _PRESETS[key] if key in _PRESETS else _CUSTOM_THEMES[key][0]


def _channel(value: int, target: int, ratio: float) -> int:
    return max(0, min(255, int(round(value + (target - value) * ratio))))


def _blend(color: str, other: str, ratio: float) -> str:
    """Blend two #RRGGBB colours; used to derive press/hover shades per theme."""
    a, b = color.lstrip("#"), other.lstrip("#")
    ar, ag, ab = int(a[0:2], 16), int(a[2:4], 16), int(a[4:6], 16)
    br, bg, bb = int(b[0:2], 16), int(b[2:4], 16), int(b[4:6], 16)
    return "#{:02x}{:02x}{:02x}".format(
        _channel(ar, br, ratio), _channel(ag, bg, ratio), _channel(ab, bb, ratio))


def _derived_states(colors: dict) -> dict:
    """Extra press/hover tokens computed from the palette; never stored on it."""
    states = dict(colors)
    states["button_pressed"] = _blend(colors["button_hover"], "#000000", 0.10)
    states["button_pressed_border"] = _blend(colors["accent"], "#000000", 0.10)
    states["primary_pressed"] = _blend(colors["primary"], "#000000", 0.16)
    states["power_hover"] = _blend(colors["power_background"], colors["power_border"], 0.22)
    states["power_pressed"] = _blend(colors["power_background"], colors["power_border"], 0.45)
    states["power_pressed_border"] = _blend(colors["power_border"], "#000000", 0.12)
    states["nav_pressed"] = _blend(colors["nav_selected"], "#000000", 0.06)
    states["nav_tile"] = _blend(colors["background"], colors["accent"], 0.14)
    states["tab_pressed"] = _blend(colors["rail"], "#000000", 0.08)
    return states


_STYLE_TEMPLATE = Template("""
/* 暖黑 × 鼠尾草绿 × 杏橙。桌面 10pt 密度下圆角按 0.75 缩放：
   卡片 16→12、输入 12→8。Qt 只在半径不超过控件短边一半时才画圆角，
   因此胶囊半径按控件高度取 12/14/10px，而不是网页里的 999px。
   字号全部走 fs_* 占位符，界面文字大小可整体缩放。 */
QMainWindow, QDialog, QWidget#Root { background: $background; color: $text; font-family: $font_body; }
QLabel { color: $text; }
QLabel#Brand { font-family: $font_display; font-size: ${fs_brand}; font-weight: 500; color: $text; }
QLabel#Subtitle { color: $muted; font-size: ${fs_small}; }
QLabel#SectionTitle { font-size: ${fs_section}; font-weight: 600; color: $accent; }
QLabel#Muted { color: $muted; font-size: ${fs_small}; }
QLabel#Value { font-family: $font_mono; font-size: ${fs_value}; font-weight: 600; color: $text; }
QLabel#Status { color: $accent; font-size: ${fs_status}; }
QLabel#Safety { color: $safety; font-size: ${fs_status}; }
QFrame#Card { background: $surface; border: 1px solid $border; border-radius: 12px; }
QFrame#Header { background: $surface; border-bottom: 1px solid $border; }
QFrame#Rail { background: $rail; border-right: 1px solid $border; }
QFrame#Badge { background: $badge_background; border: 1px solid $badge_border; border-radius: 10px; }
QLabel#BadgeText { color: $safety; font-size: ${fs_small}; font-weight: 600; }
QPushButton { background: $button; color: $text; border: 1px solid $button_border;
              border-radius: 12px; padding: 5px 11px; font-weight: 500; }
QPushButton:hover:enabled { background: $button_hover; }
QPushButton:pressed:enabled { background: $button_pressed; border-color: $button_pressed_border;
                             border-radius: 12px; }
QPushButton:focus { border-color: $focus; border-radius: 12px; }
QPushButton:disabled { background: $disabled_background; color: $disabled_text; border-color: $border;
                       border-radius: 12px; }
QPushButton#Primary { background: $primary; border-color: $primary; color: $on_accent; font-weight: 600;
                      border-radius: 12px; }
QPushButton#Primary:hover:enabled { background: $primary_hover; border-color: $primary_hover;
                                    border-radius: 12px; }
QPushButton#Primary:pressed:enabled { background: $primary_pressed; border-color: $primary_pressed;
                                      border-radius: 12px; }
QPushButton#Primary:focus { border-color: $focus; border-radius: 12px; }
QPushButton#Power { background: $power_background; border-color: $power_border; color: $power_text;
                    border-radius: 12px; }
QPushButton#Power:hover:enabled { background: $power_hover; border-color: $power_pressed_border;
                                  border-radius: 12px; }
QPushButton#Power:pressed:enabled { background: $power_pressed; border-color: $power_pressed_border;
                                    border-radius: 12px; }
QPushButton#Primary:disabled, QPushButton#Power:disabled {
    background: $disabled_background; border-color: $border; color: $disabled_text; border-radius: 12px;
}
QPushButton#Nav { background: transparent; border: 1px solid transparent; border-radius: 10px;
                  padding: 0; }
QPushButton#Nav:hover { background: $button_hover; border-color: $border; }
QPushButton#Nav:pressed { background: $nav_pressed; }
QPushButton#Nav:checked { background: $nav_tile; border-color: $border; }
QPushButton#Nav:focus { border-color: $focus; }
QComboBox, QLineEdit { color: $text; background: $input_background; border: 1px solid $input_border;
                       border-radius: 8px; padding: 4px 8px; min-height: 18px;
                       selection-background-color: $selection; selection-color: $text; }
QComboBox:focus, QLineEdit:focus { border-color: $focus; border-radius: 8px; }
QComboBox:disabled, QLineEdit:disabled { color: $disabled_text; background: $disabled_background;
                                         border-color: $border; border-radius: 8px; }
QComboBox QAbstractItemView { color: $text; background: $surface; border: 1px solid $border;
                              selection-background-color: $selection; selection-color: $text;
                              font-family: $font_body; }
QSpinBox, QDoubleSpinBox { color: $text; background: $input_background;
                          border: 1px solid $input_border; border-radius: 8px; padding: 3px 6px; }
QSpinBox:focus, QDoubleSpinBox:focus { border-color: $focus; border-radius: 8px; }
QSpinBox:disabled, QDoubleSpinBox:disabled { color: $disabled_text; background: $disabled_background;
                                             border-radius: 8px; }
QPlainTextEdit, QTextEdit { color: $text; background: $input_background; border: 1px solid $input_border;
                 border-radius: 8px; padding: 8px; font-family: $font_mono; font-size: ${fs_mono};
                 selection-background-color: $selection; selection-color: $text; }
QProgressBar { background: $disabled_background; border: 1px solid $border; border-radius: 8px;
               color: $text; text-align: center; min-height: 16px; font-size: ${fs_small}; }
QProgressBar::chunk { background: $primary; border-radius: 8px; }
QScrollArea { border: none; background: $background; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 0; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 0; }
QScrollBar::handle:vertical { background: $scrollbar_handle; border-radius: 5px; min-height: 24px; }
QScrollBar::handle:horizontal { background: $scrollbar_handle; border-radius: 5px; min-width: 24px; }
QScrollBar::handle:hover { background: $muted; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
QSplitter::handle { background: $border; }
QSplitter::handle:hover { background: $accent; }
QTabWidget::pane { border: none; background: $surface; }
QTabBar::tab { background: transparent; color: $muted; padding: 6px 14px; margin-right: 4px;
               border: 1px solid transparent; border-radius: 14px; font-weight: 500; }
QTabBar::tab:hover { background: $button_hover; color: $text; }
QTabBar::tab:selected { background: $button; color: $accent; border-color: $border; font-weight: 600;
                        border-radius: 14px; }
QTabBar::tab:pressed { background: $tab_pressed; }
QToolButton { color: $muted; background: transparent; border: 1px solid transparent;
              border-radius: 12px; padding: 4px 8px; }
QToolButton:hover { background: $button_hover; }
QToolButton:pressed { background: $button_pressed; }
QToolButton:focus { border-color: $focus; border-radius: 12px; }
QMenu { color: $text; background: $surface; border: 1px solid $border; border-radius: 12px;
        padding: 4px; font-family: $font_body; }
QMenu::item { padding: 6px 20px; border-radius: 8px; }
QMenu::item:selected { background: $selection; }
QMenu::separator { height: 1px; background: $border; margin: 4px 8px; }
QListWidget, QTreeWidget, QTableWidget { color: $text; background: $surface;
                                         border: 1px solid $border; border-radius: 8px; }
QListWidget::item:hover, QTreeWidget::item:hover, QTableWidget::item:hover { background: $button_hover; }
QListWidget::item:selected, QTreeWidget::item:selected, QTableWidget::item:selected {
    background: $selection; color: $text;
}
QHeaderView::section { background: $rail; color: $muted; border: none;
                       border-bottom: 1px solid $border; padding: 4px 6px; font-weight: 600; }
QTableCornerButton::section { background: $rail; border: none; }
QGroupBox { border: 1px solid $border; border-radius: 12px; margin-top: 16px; padding-top: 8px; }
QGroupBox::title { color: $accent; subcontrol-origin: margin; left: 12px; padding: 0 4px; }
QCheckBox, QRadioButton { color: $text; spacing: 6px; }
QCheckBox:disabled, QRadioButton:disabled { color: $disabled_text; }
/* 只画未选中框：选中态沿用原生勾选标记，方块与勾都保留。 */
QCheckBox::indicator:unchecked { border: 1px solid $input_border; border-radius: 4px;
                                 background: $input_background; }
QCheckBox::indicator:unchecked:hover { border-color: $focus; border-radius: 4px; }
QCheckBox::indicator:unchecked:disabled { border-color: $border; background: $disabled_background;
                                          border-radius: 4px; }
QToolTip { color: $text; background: $surface; border: 1px solid $border; border-radius: 8px;
           padding: 4px 8px; font-family: $font_body; }
""")


def stylesheet(theme: str = "light", text_scale: float | None = None) -> str:
    """The host applies this immediately; this function never opens a window."""
    scale = _text_scale if text_scale is None else normalize_text_scale(text_scale)
    tokens = {**_derived_states(asdict(theme_palette(theme))), **_font_tokens(scale)}
    return _STYLE_TEMPLATE.substitute(tokens)


_text_scale = 1.0


def normalize_text_scale(value) -> float:
    """Accept only finite numbers; keep the value inside a range the layout survives."""

    try:
        number = float(value)
    except (TypeError, ValueError):
        return 1.0
    if number != number or number in (float("inf"), float("-inf")):
        return 1.0
    return round(min(_SCALE_LIMITS[1], max(_SCALE_LIMITS[0], number)), 2)


def set_text_scale(value) -> float:
    """Set the process-wide UI text scale; widgets read it when they style inline text."""

    global _text_scale
    _text_scale = normalize_text_scale(value)
    return _text_scale


def scaled_px(value: float) -> int:
    """Scale an inline px size by the current UI text scale."""

    return max(1, round(value * _text_scale))


def _font_tokens(scale: float) -> dict[str, str]:
    def px(value: int) -> str:
        return f"{max(8, round(value * scale))}px"

    return {
        "font_body": FONT_BODY,
        "font_display": FONT_DISPLAY,
        "font_mono": FONT_MONO,
        "fs_brand": px(21),
        "fs_section": px(13),
        "fs_value": px(13),
        "fs_status": px(12),
        "fs_small": px(11),
        "fs_mono": px(11),
    }


def ui_font(scale: float | None = None):
    """Application font: Latin from the system UI face, CJK from the bundled one."""

    from PySide6.QtGui import QFont

    value = _text_scale if scale is None else normalize_text_scale(scale)
    font = QFont()
    font.setFamilies(list(FONT_FAMILIES))
    font.setPointSizeF(max(7.0, round(_BASE_FONT_POINTS * value * 2) / 2))
    return font


def qt_palette(theme: str = "light"):
    """原生绘制的部分（勾选标记、下拉箭头、微调按钮）跟随同一套颜色。"""

    from PySide6.QtGui import QColor, QPalette

    colors = theme_palette(theme)
    palette = QPalette()
    roles = {
        QPalette.Window: colors.background,
        QPalette.WindowText: colors.text,
        QPalette.Base: colors.input_background,
        QPalette.AlternateBase: colors.surface,
        QPalette.Text: colors.text,
        QPalette.Button: colors.button,
        QPalette.ButtonText: colors.text,
        QPalette.BrightText: colors.text,
        QPalette.Highlight: colors.selection,
        QPalette.HighlightedText: colors.text,
        QPalette.ToolTipBase: colors.surface,
        QPalette.ToolTipText: colors.text,
        QPalette.PlaceholderText: colors.muted,
        QPalette.Link: colors.accent,
        QPalette.LinkVisited: colors.accent,
        QPalette.Light: colors.surface,
        QPalette.Midlight: colors.button,
        QPalette.Mid: colors.border,
        QPalette.Dark: colors.border,
        QPalette.Shadow: colors.background,
    }
    for role, color in roles.items():
        palette.setColor(role, QColor(color))
    # Qt 6.6 起 Windows 11 样式用 Accent 画勾选框；不设就会沿用系统强调色。
    # 系统勾选标记固定用浅色，因此这里压深主色，保证标记与方块的对比度。
    accent = getattr(QPalette, "Accent", None)
    if accent is not None:
        palette.setColor(accent, QColor(_blend(colors.primary, "#000000", 0.35)))
        palette.setColor(QPalette.Disabled, accent, QColor(colors.disabled_text))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText,
                 QPalette.HighlightedText, QPalette.Link, QPalette.PlaceholderText):
        palette.setColor(QPalette.Disabled, role, QColor(colors.disabled_text))
    palette.setColor(QPalette.Disabled, QPalette.Base, QColor(colors.disabled_background))
    palette.setColor(QPalette.Disabled, QPalette.Button, QColor(colors.disabled_background))
    palette.setColor(QPalette.Disabled, QPalette.Highlight, QColor(colors.border))
    return palette
