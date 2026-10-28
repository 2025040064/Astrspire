"""Inert, stable dashboard documents and shared-source dashboard views.

Pages own presentation configuration, never a transport or raw acquisition
history. Snapshots come from the application's one SampleStore.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field, replace
import re
from uuid import uuid4

MAX_PAGES = 32
MAX_CARDS = 64
CARD_KINDS = frozenset({"waveform", "control", "image", "analysis"})


def new_id(prefix: str) -> str:
    return prefix + "-" + uuid4().hex


def _identifier(value: str) -> None:
    if not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9_-]{1,100}", value) is None:
        raise ValueError("页面、卡片或命令标识必须为1–100个英文字母、数字、下划线或连字符")


def _title(value: str) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > 80:
        raise ValueError("页面或卡片名称必须为1–80个非空字符")


@dataclass(frozen=True, slots=True)
class DashboardCard:
    id: str
    kind: str
    title: str
    config: dict = field(default_factory=dict)

    def __post_init__(self):
        _identifier(self.id)
        _title(self.title)
        if not isinstance(self.kind, str) or self.kind not in CARD_KINDS or not isinstance(self.config, dict):
            raise ValueError("卡片类型或配置无效")
        config = deepcopy(self.config)
        common = {"sizes"}
        if self.kind == "control":
            from .controls import ControlSpec, MAX_CONTROLS
            if set(config) - {"controls", "layout", "control_ids"} - common:
                raise ValueError("控件卡片含未知字段")
            controls = config.get("controls", [])
            if not isinstance(controls, list) or len(controls) > MAX_CONTROLS:
                raise ValueError("控件卡片配置数量无效")
            try:
                controls = [asdict(ControlSpec(**item)) for item in controls]
            except (TypeError, ValueError) as exc:
                raise ValueError(f"控件卡片配置无效：{exc}") from exc
            if config.get("layout", "grid") not in ("grid", "free"):
                raise ValueError("控件布局无效")
            ids = config.get("control_ids", [f"{self.id}-control-{index}" for index in range(len(controls))])
            if not isinstance(ids, list) or len(ids) != len(controls):
                raise ValueError("控件稳定标识数量或重复无效")
            for item in ids:
                _identifier(item)
            if len(set(ids)) != len(ids):
                raise ValueError("控件稳定标识重复")
            config["control_ids"] = ids
            config["controls"] = controls
        elif self.kind == "image":
            from .image_effects import IMAGE_EFFECTS
            if set(config) - {"channel", "effect", "effect_param", "rotation"} - common:
                raise ValueError("图像卡片含未知字段")
            if type(config.get("channel", 0)) is not int or not 0 <= config.get("channel", 0) <= 15:
                raise ValueError("图像卡片通道无效")
            if not isinstance(config.get("effect", "none"), str) or config.get("effect", "none") not in IMAGE_EFFECTS:
                raise ValueError("图像卡片效果无效")
            if type(config.get("effect_param", 0)) is not int or not 0 <= config.get("effect_param", 0) <= 255:
                raise ValueError("图像卡片效果参数无效")
            if type(config.get("rotation", 0)) is not int or config.get("rotation", 0) not in (0, 90, 180, 270):
                raise ValueError("图像卡片旋转无效")
        elif self.kind == "analysis":
            from .analysis_view import validate_analysis_config
            validate_analysis_config({k:v for k,v in config.items() if k != 'source_waveform_id'})
            if not isinstance(config.get('source_waveform_id', ''), str):
                raise ValueError('分析来源标识必须为字符串')
            if config.get('source_waveform_id'):
                _identifier(config['source_waveform_id'])
        elif config:
            # Cards share acquisition, but retain independent presentation.
            if set(config) - {"mode", "xy_channels", "grid", "line_width", "axis_ranges", "colors", "visible", "max_samples"}:
                raise ValueError("波形卡片含未知字段")
            if config.get("mode", "time") not in ("time", "xy"):
                raise ValueError("波形卡片模式无效")
            channels = config.get("xy_channels", [0, 1])
            if not isinstance(channels, (tuple, list)) or len(channels) != 2 or any(type(item) is not int or not 0 <= item <= 15 for item in channels):
                raise ValueError("波形卡片XY绑定无效")
            import math
            if type(config.get('grid', True)) is not bool or type(config.get('line_width', 1.3)) not in (int, float) or not .5 <= config.get('line_width', 1.3) <= 5:
                raise ValueError('波形网格或线宽无效')
            ranges = config.get('axis_ranges', [None]*4)
            if not isinstance(ranges, (list, tuple)) or len(ranges) != 4:
                raise ValueError('坐标轴上下限配置无效')
            for low, high in (ranges[:2], ranges[2:]):
                if (low is None) != (high is None) or (low is not None and (any(type(v) not in (int, float) or not math.isfinite(v) for v in (low, high)) or low >= high or not math.isfinite(high-low))):
                    raise ValueError('坐标轴范围必须有限且递增')
            colors = config.get('colors', [None]*16)
            visible = config.get('visible', [True]*16)
            if not isinstance(colors, (list, tuple)) or len(colors) != 16 or any(v is not None and (not isinstance(v,str) or re.fullmatch(r'#[0-9a-fA-F]{6}', v) is None) for v in colors):
                raise ValueError('通道颜色无效')
            if not isinstance(visible, (list, tuple)) or len(visible) != 16 or any(type(v) is not bool for v in visible):
                raise ValueError('通道显隐无效')
            if type(config.get('max_samples',100000)) is not int or not 100 <= config.get('max_samples',100000) <= 100000:
                raise ValueError('波形缓冲上限无效')
        object.__setattr__(self, "config", config)

    def to_document(self) -> dict:
        return {"id": self.id, "kind": self.kind, "title": self.title, "config": deepcopy(self.config)}

    @classmethod
    def from_document(cls, value: dict):
        if not isinstance(value, dict) or set(value) != {"id", "kind", "title", "config"}:
            raise ValueError("卡片字段不完整或含未知字段")
        return cls(**value)


@dataclass(frozen=True, slots=True)
class DashboardPage:
    id: str
    name: str
    cards: tuple[DashboardCard, ...] = ()
    layout: dict = field(default_factory=dict)

    def __post_init__(self):
        _identifier(self.id)
        _title(self.name)
        if not isinstance(self.cards, tuple) or len(self.cards) > MAX_CARDS or any(not isinstance(item, DashboardCard) for item in self.cards):
            raise ValueError("页面卡片配置无效")
        if len({item.id for item in self.cards}) != len(self.cards):
            raise ValueError("页面卡片标识重复")
        if not isinstance(self.layout, dict) or set(self.layout) - {"orientation", "sizes"}:
            raise ValueError("页面布局字段无效")
        orientation = self.layout.get("orientation", "vertical")
        sizes = self.layout.get("sizes", [])
        if orientation not in ("vertical", "horizontal") or not isinstance(sizes, list) or (sizes and len(sizes) != len(self.cards)) or any(type(item) is not int or not 0 <= item <= 100000 for item in sizes):
            raise ValueError("页面布局方向或尺寸无效")
        object.__setattr__(self, "layout", {"orientation": orientation, "sizes": list(sizes)})
        object.__setattr__(self, "cards", tuple(DashboardCard.from_document(item.to_document()) for item in self.cards))

    def to_document(self) -> dict:
        return {"id": self.id, "name": self.name, "cards": [item.to_document() for item in self.cards], "layout": deepcopy(self.layout)}

    @classmethod
    def from_document(cls, value: dict):
        if not isinstance(value, dict) or set(value) not in ({"id", "name", "cards"}, {"id", "name", "cards", "layout"}) or not isinstance(value["cards"], list):
            raise ValueError("页面字段不完整或含未知字段")
        return cls(value["id"], value["name"], tuple(DashboardCard.from_document(item) for item in value["cards"]), value.get("layout", {}))


class DashboardCollection:
    def __init__(self, pages: tuple[DashboardPage, ...]):
        if not isinstance(pages, tuple) or not 1 <= len(pages) <= MAX_PAGES or any(not isinstance(item, DashboardPage) for item in pages):
            raise ValueError("工程须有1–32个页面")
        if len({item.id for item in pages}) != len(pages):
            raise ValueError("页面标识重复")
        cards = [card.id for page in pages for card in page.cards]
        if len(set(cards)) != len(cards):
            raise ValueError("跨页面卡片标识重复")
        controls = [identifier for page in pages for card in page.cards for identifier in card.config.get("control_ids", [])]
        if len(set(controls)) != len(controls):
            raise ValueError("跨卡片控件标识重复")
        self._pages = [DashboardPage.from_document(item.to_document()) for item in pages]

    @property
    def pages(self) -> tuple[DashboardPage, ...]:
        return tuple(DashboardPage.from_document(item.to_document()) for item in self._pages)

    def to_documents(self) -> list[dict]:
        return [item.to_document() for item in self._pages]

    def add_page(self, name="新页面") -> DashboardPage:
        if len(self._pages) >= MAX_PAGES:
            raise ValueError("页面最多32个")
        page = DashboardPage(new_id("page"), name)
        self._pages.append(page)
        return page

    def index(self, page_id: str) -> int:
        for index, page in enumerate(self._pages):
            if page.id == page_id:
                return index
        raise ValueError("未找到页面标识")

    def rename_page(self, page_id: str, name: str):
        index = self.index(page_id)
        self._pages[index] = replace(self._pages[index], name=name)

    def copy_page(self, page_id: str) -> DashboardPage:
        if len(self._pages) >= MAX_PAGES:
            raise ValueError("页面最多32个")
        old = self._pages[self.index(page_id)]
        cards = []
        copied_ids = {item.id: new_id('card') for item in old.cards}
        for item in old.cards:
            config = deepcopy(item.config)
            if "control_ids" in config:
                config["control_ids"] = [new_id("control") for _ in config["control_ids"]]
            if config.get('source_waveform_id') in copied_ids:
                config['source_waveform_id'] = copied_ids[config['source_waveform_id']]
            cards.append(DashboardCard(copied_ids[item.id], item.kind, item.title, config))
        page = DashboardPage(new_id("page"), (old.name[:75] + " 副本"), tuple(cards), deepcopy(old.layout))
        self._pages.insert(self.index(page_id) + 1, page)
        return page

    def move_page(self, page_id: str, index: int):
        if type(index) is not int or not 0 <= index < len(self._pages):
            raise ValueError("页面排序位置无效")
        self._pages.insert(index, self._pages.pop(self.index(page_id)))

    def remove_page(self, page_id: str):
        if len(self._pages) == 1:
            raise ValueError("至少保留一个页面")
        return self._pages.pop(self.index(page_id))

    def replace_page(self, page: DashboardPage):
        current = self.index(page.id)
        proposed = list(self._pages)
        proposed[current] = page
        self._pages = DashboardCollection(tuple(proposed))._pages


def default_dashboard_pages(settings=None) -> tuple[DashboardPage, ...]:
    """Deterministic migration, without following referenced local files."""
    controls = list(getattr(settings, "controls", ()))
    wave = {'mode':getattr(settings,'plot_mode','time'), 'xy_channels':list(getattr(settings,'plot_xy_channels',(0,1))),
        'grid':getattr(settings,'plot_grid',True),'line_width':getattr(settings,'plot_line_width',1.3),
        'axis_ranges':list(getattr(settings,'plot_axis_ranges',(None,)*4)),
        'colors':list(getattr(settings,'channel_colors',(None,)*16)),
        'visible':list(getattr(settings,'visible_channels',(True,)*16)), 'max_samples':getattr(settings,'buffer_samples',800)}
    return (
        DashboardPage("legacy-page-waveform", "波形", (DashboardCard("legacy-card-waveform", "waveform", "波形", wave),)),
        DashboardPage("legacy-page-control", "控件", (DashboardCard("legacy-card-control", "control", "控件", {"controls": deepcopy(controls), "layout": getattr(settings, "controls_layout", "grid")}),)),
        DashboardPage("legacy-page-image", "图像", (DashboardCard("legacy-card-image", "image", "图像", {"channel": getattr(settings, "image_channel", 0), "effect": getattr(settings, "image_effect", "none"), "effect_param": getattr(settings, "image_effect_param", 0), "rotation": getattr(settings, "image_rotation", 0)}),)),
        DashboardPage("legacy-page-analysis", "统计 / 频谱", (DashboardCard("legacy-card-analysis", "analysis", "统计 / 频谱", deepcopy(getattr(settings, "analysis_config", {}))),)),
    )


# The model above stays independent of device/worker modules. Qt views below
# consume snapshots and forward explicit user command requests to the host.
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QInputDialog, QLabel, QMenu, QMessageBox, QFileDialog, QDialog,
    QPushButton, QScrollArea, QSpinBox, QSplitter, QTabWidget, QToolButton,
    QVBoxLayout, QWidget,
)


class _SnapshotWaveform:
    """Analysis adapter referencing one shared snapshot, with no raw history."""
    def __init__(self, owner):
        self.owner = owner
        self.sample_interval_ms = 1.0
        self.paused = False
        self.visible_channels = (True,) * 16

    @property
    def displayed_sample_count(self):
        if getattr(self.owner, '_projection', None) is not None:
            return self.owner._projection.cursor.sequence
        return len(self.owner._frames)

    def displayed_snapshot(self, maximum=8192):
        if getattr(self.owner, '_projection', None) is not None:
            return tuple(column[-maximum:] for column in self.owner._projection.columns)
        rows = self.owner._display_rows(maximum)
        channels = max((len(row) for row in rows), default=0)
        return tuple(tuple(row[index] if index < len(row) else float("nan") for row in rows) for index in range(channels))


class _ImageCard(QWidget):
    def __init__(self, config, owner):
        super().__init__()
        from .images import ImageViewWidget
        self.owner = owner
        self._generation = None
        self._pending_rotation = config.get("rotation", 0)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("图像通道"))
        self.channel = QSpinBox()
        self.channel.setRange(0, 15)
        self.channel.setValue(config.get("channel", 0))
        self.channel.valueChanged.connect(self._changed)
        toolbar.addWidget(self.channel)
        toolbar.addStretch()
        layout.addLayout(toolbar)
        self.viewer = ImageViewWidget()
        self.viewer.save_requested.connect(self.save_image)
        self.viewer.error.connect(owner.error)
        self.viewer.set_effect(config.get("effect", "none"))
        from .image_effects import effect_parameter
        spec = effect_parameter(config.get("effect", "none"))
        self.viewer.set_effect_parameter(min(spec.maximum, max(spec.minimum, config.get("effect_param", spec.default))))
        self.viewer.configuration_changed.connect(owner.configuration_changed)
        layout.addWidget(self.viewer, 1)

    def save_image(self):
        if not self.viewer.has_image:
            return
        title = '保存此卡原始图像（不含效果）' if self.viewer.default_suffix != '.raw' and self.viewer.save_button.text() == '保存原图…' else '保存此卡视图PNG（含效果）'
        filename, _ = QFileDialog.getSaveFileName(self, title, 'frame' + self.viewer.default_suffix, '图像 (*)')
        if filename:
            try:
                self.viewer.save_image(filename)
            except (OSError, ValueError) as exc:
                QMessageBox.warning(self, '保存失败', str(exc))

    def _changed(self):
        self._generation = None
        self.viewer.clear()
        self.refresh()
        self.owner.configuration_changed.emit()

    def refresh(self):
        if not self.isVisible() or self.owner._image_store is None:
            return
        index = self.channel.value()
        record = self.owner._image_store.get(index)
        if record is None or self._generation == (index, record[2]):
            return
        frame, error = self.owner._image_store.decoded(index)
        self._generation = (index, record[2])
        if frame is not None:
            rotation = self.viewer.rotation if self.viewer.has_image else self._pending_rotation
            self.viewer.set_decoded_frame(frame, record[0])
            for _ in range(((rotation - self.viewer.rotation) % 360) // 90):
                self.viewer.rotate_clockwise(notify=False)
        elif error:
            self.viewer.error.emit(error)

    def export_config(self):
        return {"channel": self.channel.value(), "effect": self.viewer.effect,
                "effect_param": self.viewer.effect_parameter,
                "rotation": self.viewer.rotation if self.viewer.has_image else self._pending_rotation}


class DashboardManager(QWidget):
    """User pages with stable references and resizable mixed presentation cards.

    Tab drag changes only ordering. Menu actions offer the same operations to
    keyboard users. Deletions require a default-No confirmation. No transition
    starts a source, reads an external model, or sends a packet.
    """
    packet_requested = Signal(bytes)
    configuration_changed = Signal()
    widgets_rebuilt = Signal()
    error = Signal(str)
    active_card_changed = Signal(object)

    def __init__(self, parent=None, *, legacy_widgets=None):
        super().__init__(parent)
        self._legacy = dict(legacy_widgets or {})
        self._legacy = {("control" if key == "controls" else key): widget for key, widget in self._legacy.items()}
        for widget in self._legacy.values():
            if hasattr(widget, "configuration_changed"):
                widget.configuration_changed.connect(self.configuration_changed)
        self._widgets = {}
        self._cards = {}
        self._splitters = {}
        self._image_store = None
        self._frames = ()
        self._projection = None
        self._projection_worker = None
        self._projection_store = None
        self._projection_config = None
        self._projection_generation = 0
        self._projection_errors = set()
        self._names = ()
        self._transform = lambda values: values
        self._transmit = False
        self._theme = "light"
        self._loading = False
        self._active_card_id = ''
        self._selection_page_id = ''
        self._selection_cleared = False
        self._interval_ms = 1.0
        self._collection = DashboardCollection(default_dashboard_pages())
        self._analysis_source = _SnapshotWaveform(self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.tabs = QTabWidget()
        self.tabs.setMovable(True)
        self.tabs.tabBar().tabMoved.connect(self._tab_moved)
        self.tabs.currentChanged.connect(self._current_changed)
        self.tabs.tabBar().setContextMenuPolicy(Qt.CustomContextMenu)
        self.tabs.tabBar().customContextMenuRequested.connect(self._page_menu)
        tools = QWidget()
        tools_layout = QHBoxLayout(tools)
        tools_layout.setContentsMargins(0, 0, 0, 0)
        add_page = QToolButton()
        add_page.setText("＋页面")
        add_page.setAccessibleName("新增页面")
        add_page.clicked.connect(self._prompt_add_page)
        tools_layout.addWidget(add_page)
        add_card = QToolButton()
        add_card.setText("＋卡片")
        add_card.setAccessibleName("向当前页面新增卡片")
        add_card.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(add_card)
        for kind, title in (("waveform", "波形"), ("control", "控件"), ("image", "图像"), ("analysis", "统计 / 频谱")):
            menu.addAction(title, lambda checked=False, k=kind, t=title: self.add_card(k, t))
        add_card.setMenu(menu)
        tools_layout.addWidget(add_card)
        self.tabs.setCornerWidget(tools)
        layout.addWidget(self.tabs)
        self.load_pages([page.to_document() for page in default_dashboard_pages()])

    def pages(self) -> tuple[dict, ...]:
        self._capture()
        return tuple(self._collection.to_documents())

    def active_page_id(self) -> str:
        index = self.tabs.currentIndex()
        return self.tabs.widget(index).property("dashboard_page_id") if index >= 0 else ""

    def primary_widget(self, kind):
        kind = "control" if kind == "controls" else kind
        if kind in self._legacy:
            return self._legacy[kind]
        return next((self._widgets[card.id] for page in self._collection.pages for card in page.cards if card.kind == kind), None)

    def load_pages(self, documents, active_page_id=""):
        proposed = DashboardCollection(tuple(DashboardPage.from_document(item) if isinstance(item, dict) else item for item in documents))
        if active_page_id and active_page_id not in {item.id for item in proposed.pages}:
            raise ValueError("当前页面不存在")
        self._loading = True
        try:
            # Keep original aliases alive for the host's existing setting panes.
            for widget in self._legacy.values():
                widget.hide()
                widget.setParent(self)
            while self.tabs.count():
                widget = self.tabs.widget(0)
                self.tabs.removeTab(0)
                widget.deleteLater()
            self._widgets.clear()
            self._cards.clear()
            self._splitters.clear()
            self._collection = proposed
            reused = set()
            for page in proposed.pages:
                host = QWidget()
                host.setProperty("dashboard_page_id", page.id)
                layout = QVBoxLayout(host)
                layout.setContentsMargins(0, 0, 0, 0)
                split = QSplitter(Qt.Horizontal if page.layout["orientation"] == "horizontal" else Qt.Vertical)
                split.setChildrenCollapsible(False)
                split.splitterMoved.connect(lambda *_args: self.configuration_changed.emit())
                self._splitters[page.id] = split
                layout.addWidget(split, 1)
                for card in page.cards:
                    view = self._make_view(card, reused)
                    self._widgets[card.id] = view
                    wrapper = QFrame()
                    wrapper.setProperty("dashboard_card_id", card.id)
                    wrapper_layout = QVBoxLayout(wrapper)
                    wrapper_layout.setContentsMargins(2, 2, 2, 2)
                    header = QHBoxLayout()
                    label = QLabel(card.title)
                    label.setTextFormat(Qt.PlainText)
                    header.addWidget(label)
                    if card.kind == 'waveform':
                        select = QToolButton()
                        select.setText('操作此卡')
                        select.clicked.connect(lambda checked=False, cid=card.id: self.select_card(cid))
                        header.addWidget(select)
                    header.addStretch()
                    if card.kind == "control":
                        add_control = QToolButton()
                        add_control.setText("＋控件")
                        add_control.clicked.connect(lambda checked=False, cid=card.id: self._prompt_control(cid))
                        header.addWidget(add_control)
                    actions = QToolButton()
                    actions.setText("⋮")
                    actions.setAccessibleName(card.title + "卡片操作")
                    actions.setPopupMode(QToolButton.InstantPopup)
                    card_menu = QMenu(actions)
                    card_menu.addAction("上移", lambda checked=False, cid=card.id: self.move_card(cid, -1))
                    card_menu.addAction("下移", lambda checked=False, cid=card.id: self.move_card(cid, 1))
                    card_menu.addAction("删除卡片…", lambda checked=False, cid=card.id: self.remove_card(cid))
                    actions.setMenu(card_menu)
                    header.addWidget(actions)
                    wrapper_layout.addLayout(header)
                    wrapper_layout.addWidget(view, 1)
                    split.addWidget(wrapper)
                    self._cards[card.id] = wrapper
                if page.layout["sizes"]:
                    split.setSizes(page.layout["sizes"])
                if not page.cards:
                    layout.addWidget(QLabel("点击“＋卡片”添加波形、控件、图像或分析。"))
                self.tabs.addTab(host, page.name)
            ids = [page.id for page in proposed.pages]
            self.tabs.setCurrentIndex(ids.index(active_page_id) if active_page_id else 0)
        finally:
            self._loading = False
        self._refresh_current()
        self._select_current_wave()
        self.widgets_rebuilt.emit()

    def card_widget(self, card_id):
        return self._widgets.get(card_id)

    def active_waveform(self):
        from .waveform import WaveformWidget
        widget = self._widgets.get(self._active_card_id)
        return widget if isinstance(widget, WaveformWidget) else None

    def select_card(self, card_id):
        page = next((p for p in self._collection.pages if any(c.id == card_id for c in p.cards)), None)
        if page is None or page.id != self.active_page_id():
            return
        self._active_card_id = card_id
        self._selection_cleared = False
        self.active_card_changed.emit(self.active_waveform())

    def _select_current_wave(self):
        page = next((p for p in self._collection.pages if p.id == self.active_page_id()), None)
        cards = [c.id for c in page.cards if c.kind == 'waveform'] if page else []
        if self._selection_page_id != self.active_page_id():
            self._selection_cleared = False
            self._selection_page_id = self.active_page_id()
            if self._active_card_id not in cards:
                self._active_card_id = ''
        elif self._active_card_id and self._active_card_id not in cards:
            self._active_card_id = ''
            self._selection_cleared = True
        if not self._active_card_id and not self._selection_cleared:
            self._active_card_id = cards[0] if cards else ''
        self.active_card_changed.emit(self.active_waveform())

    def activate_kind(self, kind):
        """Navigate by a stable card identity, not an obsolete numeric tab."""
        preferred = "legacy-card-" + kind
        pages = self._collection.pages
        page = next((page for page in pages if any(card.id == preferred for card in page.cards)), None)
        page = page or next((page for page in pages if any(card.kind == kind for card in page.cards)), None)
        if page is not None:
            for index in range(self.tabs.count()):
                if self.tabs.widget(index).property("dashboard_page_id") == page.id:
                    self.tabs.setCurrentIndex(index)
                    return True
        return False

    def is_kind_visible(self, kind):
        if not self.tabs.count():
            return False
        page_id = self.active_page_id()
        return any(card.kind == kind for page in self._collection.pages if page.id == page_id for card in page.cards)

    def _make_view(self, card, reused):
        # Reordering must not move the host's stable legacy alias onto a copy.
        if card.kind in self._legacy and card.kind not in reused and card.id == "legacy-card-" + card.kind:
            reused.add(card.kind)
            view = self._legacy[card.kind]
            # Root restores the legacy widgets via existing settings APIs;
            # dashboard configs take precedence when reloading edited pages.
            if card.kind == "control":
                view.load_config(deepcopy(card.config.get("controls", [])))
                view.set_layout_mode(card.config.get("layout", "grid"))
                for item, identifier in zip(view.controls, card.config.get("control_ids", [])):
                    item.dashboard_control_id = identifier
            elif card.kind == 'waveform':
                self.apply_wave_config(view, card.config)
            view.show()
            return view
        if card.kind == "waveform":
            from .waveform import WaveformWidget
            view = WaveformWidget(max_samples=100000)
            self.apply_wave_config(view, card.config)
        elif card.kind == "control":
            from .controls import ControlWorkspace
            view = ControlWorkspace()
            view.load_config(deepcopy(card.config.get("controls", [])))
            view.set_layout_mode(card.config.get("layout", "grid"))
            for item, identifier in zip(view.controls, card.config.get("control_ids", [])):
                item.dashboard_control_id = identifier
            view.set_image_store(self._image_store)
            view.set_transmit_enabled(self._transmit)
            view.packet_requested.connect(self.packet_requested)
            view.error.connect(self.error)
            view.configuration_changed.connect(self.configuration_changed)
            view.configuration_changed.connect(lambda selected=view: self._attach_editors(selected))
            self._attach_editors(view)
        elif card.kind == "image":
            view = _ImageCard(card.config, self)
        else:
            from .analysis_view import AnalysisView
            view = AnalysisView(self._analysis_source)
            view.load_config({k:v for k,v in card.config.items() if k != 'source_waveform_id'})
            source = QComboBox()
            source.addItem('共享实时数据（独立，不随波形暂停）', '')
            page = next(p for p in self._collection.pages if card in p.cards)
            waves = [c for c in page.cards if c.kind == 'waveform']
            for wave in waves:
                source.addItem('关联波形：'+wave.title, wave.id)
            selected = card.config.get('source_waveform_id', waves[0].id if waves else '')
            if source.findData(selected) < 0:
                source.addItem('关联波形已删除（无数据）', selected)
            source.setCurrentIndex(source.findData(selected))
            view.source_selector = source
            view.layout().insertWidget(0, source)
            source.currentIndexChanged.connect(lambda _index: (self.configuration_changed.emit(), self._refresh_current()))
        if hasattr(view, "set_theme"):
            view.set_theme(self._theme)
        return view

    def apply_wave_config(self, view, config):
        """Partial legacy configs inherit existing defaults; schema6 is explicit."""
        view.set_sample_interval_ms(self._interval_ms)
        view.set_projection_managed(True)
        if not getattr(view, '_dashboard_pause_connected', False):
            view._dashboard_pause_connected = True
            view.paused_changed.connect(lambda paused: self._wave_pause_changed(view, paused))
        view.set_mode(config.get('mode', view.mode))
        view.set_xy_channels(*config.get('xy_channels', view.xy_channels))
        if 'max_samples' in config:
            view.set_max_samples(config['max_samples'])
        if 'grid' in config:
            view.set_grid_visible(config['grid'])
        if 'line_width' in config:
            view.set_line_width(config['line_width'])
        if 'axis_ranges' in config:
            view.set_axis_ranges(**dict(zip(('x_min','x_max','y_min','y_max'),config['axis_ranges'])))
        for i, visible in enumerate(config.get('visible', ())):
            view.set_channel_visible(i, visible)
        if 'colors' in config:
            view.reset_channel_colors()
            for i, color in enumerate(config['colors']):
                if color:
                    view.set_channel_color(i, color)

    def _wave_pause_changed(self, view, paused):
        if not paused:
            self._refresh_current()
        if self.active_waveform() is view:
            self.active_card_changed.emit(view)

    def _capture(self):
        for page in self._collection.pages:
            cards = []
            for card in page.cards:
                view = self._widgets.get(card.id)
                config = deepcopy(card.config)
                if card.kind == "control" and view is not None:
                    controls = view.export_config()
                    # Retain IDs by the live card object, not positional indices.
                    old_ids = config.get("control_ids", [])
                    for index, item in enumerate(view.controls):
                        if not hasattr(item, "dashboard_control_id"):
                            item.dashboard_control_id = old_ids[index] if index < len(old_ids) else new_id("control")
                    config = {"controls": controls, "layout": view.layout_mode,
                              "control_ids": [item.dashboard_control_id for item in view.controls]}
                elif card.kind == "analysis" and view is not None:
                    config = view.export_config()
                    if hasattr(view, 'source_selector'):
                        config['source_waveform_id'] = view.source_selector.currentData()
                elif isinstance(view, _ImageCard):
                    config = view.export_config()
                elif card.kind == "waveform" and view is not None:
                    config = {"mode": view.mode, "xy_channels": list(view.xy_channels), 'grid':view.grid_visible,
                        'line_width':view.line_width,'axis_ranges':list(view.axis_ranges.values()),
                        'colors':[view.custom_channel_colors.get(i) for i in range(16)],
                        'visible':list(view.visible_channels),'max_samples':view.max_samples}
                cards.append(replace(card, config=config))
            split = self._splitters.get(page.id)
            layout = {"orientation": "horizontal" if split and split.orientation() == Qt.Horizontal else "vertical",
                      "sizes": split.sizes() if split and split.count() else []}
            self._collection.replace_page(replace(page, cards=tuple(cards), layout=layout))

    def _rebuild(self, active_id=None):
        active_id = active_id or self.active_page_id()
        self.load_pages(self._collection.to_documents(), active_id)
        self.configuration_changed.emit()

    def add_page(self, name="新页面"):
        self._capture()
        page = self._collection.add_page(name)
        self._rebuild(page.id)
        return page.id

    def rename_page(self, page_id, name):
        self._capture()
        self._collection.rename_page(page_id, name)
        self._rebuild(page_id)

    def copy_page(self, page_id):
        self._capture()
        page = self._collection.copy_page(page_id)
        self._rebuild(page.id)
        return page.id

    def move_page(self, page_id, index):
        self._capture()
        self._collection.move_page(page_id, index)
        self._rebuild(page_id)

    def remove_page(self, page_id):
        self._capture()
        page = self._collection.pages[self._collection.index(page_id)]
        if len(self._collection.pages) == 1:
            return False
        if QMessageBox.question(self, "删除页面", f"删除页面“{page.name}”及其{len(page.cards)}张卡片？已保存工程不受影响；未保存配置无法恢复。", QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return False
        self._collection.remove_page(page_id)
        self._rebuild(self._collection.pages[0].id)
        return True

    def add_card(self, kind, title=None, config=None):
        self._capture()
        page_id = self.active_page_id()
        page = self._collection.pages[self._collection.index(page_id)]
        card = DashboardCard(new_id("card"), kind, title or kind, config or {})
        self._collection.replace_page(replace(page, cards=page.cards + (card,), layout={"orientation": page.layout["orientation"], "sizes": []}))
        self._rebuild(page_id)
        return card.id

    def move_card(self, card_id, delta):
        self._capture()
        for page in self._collection.pages:
            ids = [card.id for card in page.cards]
            if card_id in ids:
                index = ids.index(card_id)
                target = index + delta
                if not 0 <= target < len(ids):
                    return
                cards = list(page.cards)
                cards.insert(target, cards.pop(index))
                sizes = list(page.layout["sizes"])
                if sizes:
                    sizes.insert(target, sizes.pop(index))
                self._collection.replace_page(replace(page, cards=tuple(cards), layout={"orientation": page.layout["orientation"], "sizes": sizes}))
                self._rebuild(page.id)
                return

    def remove_card(self, card_id):
        self._capture()
        for page in self._collection.pages:
            card = next((item for item in page.cards if item.id == card_id), None)
            if card is not None:
                count = len(card.config.get("controls", []))
                if QMessageBox.question(self, "删除卡片", f"删除1张“{card.title}”卡片（含{count}个控件）？已保存工程不受影响；未保存配置无法恢复。", QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                    return False
                self._collection.replace_page(replace(page, cards=tuple(item for item in page.cards if item.id != card_id), layout={"orientation": page.layout["orientation"], "sizes": []}))
                self._rebuild(page.id)
                return True
        return False

    def _prompt_add_page(self):
        name, accepted = QInputDialog.getText(self, "新增页面", "页面名称：")
        if accepted and name.strip():
            try:
                self.add_page(name)
            except ValueError as exc:
                QMessageBox.warning(self, "无法新增页面", str(exc))

    def _prompt_control(self, card_id):
        from .controls import CONTROL_KINDS
        kind, accepted = QInputDialog.getItem(self, "新增控件", "控件类型：", CONTROL_KINDS, 0, False)
        if not accepted:
            return
        from .control_editor import ControlEditorDialog
        dialog = ControlEditorDialog(kind, parent=self)
        if dialog.exec() != QDialog.Accepted or dialog.result_spec is None:
            return
        spec = dialog.result_spec
        try:
            self._widgets[card_id].add_control(spec.kind, spec.channel, spec.title, spec.command, spec.settings)
            self.configuration_changed.emit()
        except ValueError as exc:
            QMessageBox.warning(self, "无法新增控件", str(exc))

    def _attach_editors(self, workspace):
        for control in workspace.controls:
            if getattr(control, '_dashboard_editor_attached', False):
                continue
            control._dashboard_editor_attached = True
            control.setContextMenuPolicy(Qt.CustomContextMenu)
            control.customContextMenuRequested.connect(lambda point, card=control, owner=workspace: self._control_menu(owner, card, point))

    def _control_menu(self, workspace, card, point):
        if card not in workspace.controls:
            return
        menu = QMenu(self)
        def edit():
            from .control_editor import ControlEditorDialog
            dialog = ControlEditorDialog(spec=card.spec, parent=self)
            if dialog.exec() == QDialog.Accepted and dialog.result_spec is not None and card in workspace.controls:
                spec = dialog.result_spec
                workspace.update_control(workspace.controls.index(card), kind=spec.kind, channel=spec.channel,
                    title=spec.title, command=spec.command, settings=spec.settings)
        def remove():
            if QMessageBox.question(self, '删除控件', '删除1个控件？已保存文件不变，可重新载入；未保存内容不能恢复。', QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes and card in workspace.controls:
                workspace.remove_control(workspace.controls.index(card))
        menu.addAction('编辑属性…', edit)
        index = workspace.controls.index(card)
        menu.addAction('前移', lambda: workspace.move_control(workspace.controls.index(card), workspace.controls.index(card)-1)).setEnabled(index > 0)
        menu.addAction('后移', lambda: workspace.move_control(workspace.controls.index(card), workspace.controls.index(card)+1)).setEnabled(index+1 < workspace.count)
        menu.addAction('删除控件…', remove)
        menu.exec(card.mapToGlobal(point))

    def _page_menu(self, position):
        index = self.tabs.tabBar().tabAt(position)
        if index < 0:
            return
        page_id = self.tabs.widget(index).property("dashboard_page_id")
        menu = QMenu(self)
        menu.addAction("新增页面…", self._prompt_add_page)
        def rename():
            name, accepted = QInputDialog.getText(self, "重命名页面", "页面名称：", text=self.tabs.tabText(index))
            if accepted and name.strip():
                self.rename_page(page_id, name)
        menu.addAction("重命名…", rename)
        menu.addAction("复制页面", lambda: self.copy_page(page_id))
        def orient():
            split = self._splitters[page_id]
            split.setOrientation(Qt.Vertical if split.orientation() == Qt.Horizontal else Qt.Horizontal)
            self.configuration_changed.emit()
        menu.addAction("切换横向 / 纵向布局", orient)
        menu.addAction("左移", lambda: self.move_page(page_id, index - 1)).setEnabled(index > 0)
        menu.addAction("右移", lambda: self.move_page(page_id, index + 1)).setEnabled(index + 1 < self.tabs.count())
        menu.addAction("删除页面…", lambda: self.remove_page(page_id)).setEnabled(self.tabs.count() > 1)
        menu.exec(self.tabs.tabBar().mapToGlobal(position))

    def _tab_moved(self, previous, current):
        if not self._loading:
            page_id = self.tabs.widget(current).property("dashboard_page_id")
            self._collection.move_page(page_id, current)
            self.configuration_changed.emit()

    def _current_changed(self, index):
        if not self._loading:
            self._select_current_wave()
            self._refresh_current()
            self.configuration_changed.emit()

    def set_sample_snapshot(self, frames, names=(), transform=None):
        self._projection = None
        self._frames = tuple(frames)
        if not self._frames:
            self._projection_errors.clear()
        self._names = tuple(names)
        self._transform = transform or (lambda values: values)
        self._refresh_current()

    def request_projection(self, store, config, names=()):
        """Freeze plain display settings; the worker never receives a widget."""
        from .projection import ProjectionWorker
        config = tuple(config)
        if store is not self._projection_store or config != self._projection_config:
            self._projection_generation += 1
            self._projection = None
        self._projection_store, self._projection_config = store, config
        if self._projection_worker is None:
            self._projection_worker = ProjectionWorker(self)
            self._projection_worker.ready.connect(self._accept_projection)
            self._projection_worker.failed.connect(self.error)
            self._projection_worker.start()
        self._projection_worker.request(store, config, self._projection_generation, names)

    @property
    def projection_generation(self):
        return self._projection_generation

    def _accept_projection(self, result):
        store, snapshot, names = result
        if (store is not self._projection_store
                or snapshot.generation != self._projection_generation
                or snapshot.cursor.epoch != store.cursor.epoch):
            return
        if self._projection is not None and snapshot.cursor.sequence < self._projection.cursor.sequence:
            return
        self._projection, self._names = snapshot, tuple(names)
        self._frames = ()
        for message in snapshot.errors:
            self.error.emit(message)
        self._refresh_current()

    def stop_projection(self):
        if self._projection_worker is None:
            return True
        if not self._projection_worker.stop():
            return False
        self._projection_worker.deleteLater()
        self._projection_worker = None
        self._projection_store = None
        self._projection_generation += 1
        return True

    def closeEvent(self, event):
        if self.stop_projection():
            super().closeEvent(event)
        else:
            event.ignore()

    def _display_rows(self, maximum=100000):
        if self._projection is not None:
            return list(zip(*(column[-maximum:] for column in self._projection.columns)))
        rows = []
        for frame in self._frames[-maximum:]:
            values = getattr(frame, "values", getattr(frame, "raw_values", frame))
            try:
                rows.append(tuple(self._transform(tuple(values))))
            except (ValueError, OverflowError) as exc:
                message = f"显示换算失败：{exc}；原始采样仍保留。"
                if message not in self._projection_errors and len(self._projection_errors) < 16:
                    self._projection_errors.add(message)
                    self.error.emit(message)
        return rows

    def _refresh_current(self):
        if self._loading or not self.tabs.count():
            return
        page_id = self.active_page_id()
        page = self._collection.pages[self._collection.index(page_id)]
        rows = None
        # Analyses must consume this batch, regardless of visual card order.
        ordered_cards = sorted(page.cards, key=lambda card: card.kind == "analysis")
        for card in ordered_cards:
            widget = self._widgets.get(card.id)
            if widget is None:
                continue
            if card.kind == "waveform" and (widget is not self._legacy.get("waveform") or self._projection is not None):
                if not widget.paused:
                    if self._projection is not None:
                        widget.set_projection(self._projection, self._names)
                    else:
                        rows = rows if rows is not None else self._display_rows()
                        widget.replace_samples(rows, list(self._names), sample_count=getattr(self._frames[-1], 'sequence', len(rows)) if self._frames else 0)
            elif card.kind == "control":
                latest = rows if rows is not None else self._display_rows(1)
                widget.set_samples(latest[-1] if latest else (), self._names)
            elif card.kind == "analysis":
                if hasattr(widget, 'source_selector'):
                    source_id = widget.source_selector.currentData()
                    if source_id:
                        source = self._widgets.get(source_id)
                        from .waveform import WaveformWidget
                        if not isinstance(source, WaveformWidget):
                            from types import SimpleNamespace
                            source = _SnapshotWaveform(SimpleNamespace(_frames=(), _display_rows=lambda maximum: []))
                        widget._waveform = source
                    else:
                        widget._waveform = self._analysis_source
                widget.set_channel_names(self._names)
                widget.invalidate()
            elif isinstance(widget, _ImageCard):
                widget.refresh()

    def set_image_store(self, store):
        self._image_store = store
        for widget in self._widgets.values():
            if hasattr(widget, "set_image_store"):
                widget.set_image_store(store)
        self.refresh_live_images()

    def live_image_indices(self):
        indices = set()
        for widget in self._widgets.values():
            if isinstance(widget, _ImageCard):
                indices.add(widget.channel.value())
            elif hasattr(widget, "live_image_indices"):
                indices.update(widget.live_image_indices())
        return indices

    def refresh_live_images(self):
        for widget in self._widgets.values():
            if widget.isVisible():
                if isinstance(widget, _ImageCard):
                    widget.refresh()
                elif hasattr(widget, "refresh_live_images"):
                    widget.refresh_live_images()

    def set_transmit_enabled(self, enabled):
        self._transmit = bool(enabled)
        for widget in self._widgets.values():
            if hasattr(widget, "set_transmit_enabled"):
                widget.set_transmit_enabled(enabled)

    def set_theme(self, theme):
        self._theme = theme
        for widget in self._widgets.values():
            if hasattr(widget, "set_theme"):
                widget.set_theme(theme)
            elif isinstance(widget, _ImageCard):
                widget.viewer.set_theme(theme)

    def set_sample_interval_ms(self, value):
        self._interval_ms = value
        self._analysis_source.sample_interval_ms = value
        for widget in self._widgets.values():
            if hasattr(widget, "set_sample_interval_ms"):
                widget.set_sample_interval_ms(value)
