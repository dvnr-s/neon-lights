"""Manual mode: color wheel, RGB sliders, hex, presets and saved favorites."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QIcon, QKeySequence, QPainter, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QGridLayout,
    QInputDialog,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QWidget,
)

from ...engine import LightingEngine, Mode
from ...engine.color import from_hex, to_hex
from ..widgets import HBox, ColorSwatch, ColorWheel, LabeledSlider, VBox, card, label, qcolor, rgb_of

PRESETS: list[tuple[str, tuple[int, int, int]]] = [
    ("Red", (255, 0, 0)),
    ("Orange", (255, 80, 0)),
    ("Yellow", (255, 210, 0)),
    ("Green", (0, 255, 0)),
    ("Cyan", (0, 230, 255)),
    ("Blue", (0, 40, 255)),
    ("Purple", (150, 0, 255)),
    ("Pink", (255, 0, 140)),
    ("Warm white", (255, 160, 70)),
    ("Lime", (140, 255, 0)),
    ("Teal", (0, 255, 150)),
    ("Indigo", (70, 0, 255)),
    ("Magenta", (255, 0, 255)),
    ("White", (255, 255, 255)),
]


def _dot_icon(rgb, size: int = 40) -> QIcon:
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(qcolor(rgb))
    p.drawEllipse(2, 2, size - 4, size - 4)
    p.end()
    return QIcon(pm)


class ManualPage(QWidget):
    changed = Signal()

    def __init__(self, engine: LightingEngine, settings: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.engine = engine
        self.settings = settings
        self._rgb = tuple(float(v) for v in settings["manual_color"])

        root = HBox(self, spacing=14)

        # --- left: wheel + hex
        wheel_card, wl = card()
        wl.addWidget(label("Color", "section"))
        self.wheel = ColorWheel()
        wl.addWidget(self.wheel, 1, Qt.AlignHCenter)
        hex_row = HBox()
        self.swatch = ColorSwatch(self._rgb, size=34)
        self.swatch.setEnabled(False)
        self.hex_edit = QLineEdit()
        self.hex_edit.setMaxLength(7)
        self.hex_edit.setPlaceholderText("#FF0000")
        hex_row.addWidget(self.swatch)
        hex_row.addWidget(self.hex_edit, 1)
        wl.addLayout(hex_row)
        root.addWidget(wheel_card, 5)

        # --- right: sliders, presets, favorites
        right = VBox(spacing=14)
        root.addLayout(right, 6)

        rgb_card, rl = card()
        rl.addWidget(label("RGB", "section"))
        self.sliders = [
            LabeledSlider(name, 0, 255, 0, object_name=obj)
            for name, obj in (("Red", "red"), ("Green", "green"), ("Blue", "blue"))
        ]
        for s in self.sliders:
            rl.addWidget(s)
            s.valueChanged.connect(self._on_slider)
        right.addWidget(rgb_card)

        preset_card, pl = card()
        pl.addWidget(label("Presets", "section"))
        grid = QGridLayout()
        grid.setSpacing(8)
        self.preset_swatches: list[ColorSwatch] = []
        for i, (name, rgb) in enumerate(PRESETS):
            tip = f"{name}  (Alt+{i + 1})" if i < 9 else name
            sw = ColorSwatch(rgb, size=36, tooltip=tip)
            self.preset_swatches.append(sw)
            sw.clicked.connect(lambda _=False, c=rgb: self.set_color(c))
            grid.addWidget(sw, i // 7, i % 7)
        pl.addLayout(grid)
        right.addWidget(preset_card)

        fav_card, fl = card()
        head = HBox()
        head.addWidget(label("Favorites", "section"))
        head.addStretch(1)
        self.save_btn = QPushButton("＋ Save current")
        self.save_btn.setToolTip("Save the current color as a favorite (Ctrl+S)")
        self.save_btn.clicked.connect(lambda: self.save_favorite())
        head.addWidget(self.save_btn)
        fl.addLayout(head)
        self.fav_list = QListWidget()
        self.fav_list.setViewMode(QListWidget.IconMode)
        self.fav_list.setIconSize(QSize(34, 34))
        self.fav_list.setGridSize(QSize(84, 70))
        self.fav_list.setMovement(QListWidget.Static)
        self.fav_list.setResizeMode(QListWidget.Adjust)
        self.fav_list.setWordWrap(True)
        self.fav_list.setMinimumHeight(90)
        self.fav_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.fav_list.customContextMenuRequested.connect(self._fav_menu)
        self.fav_list.itemClicked.connect(self._fav_clicked)
        QShortcut(QKeySequence.Delete, self.fav_list, activated=self._delete_selected_favorite,
                  context=Qt.WidgetShortcut)
        fl.addWidget(self.fav_list, 1)
        self.fav_empty = label("No favorites yet. Pick a color and press  ＋ Save current  (Ctrl+S).", "muted")
        self.fav_empty.setAlignment(Qt.AlignCenter)
        self.fav_empty.setWordWrap(True)
        self.fav_empty.setMinimumHeight(90)
        fl.addWidget(self.fav_empty, 1)
        self.fav_hint = label("Click to apply · right-click to rename or delete", "hint")
        fl.addWidget(self.fav_hint)
        right.addWidget(fav_card, 1)

        self.wheel.colorPicked.connect(lambda c: self.set_color(rgb_of(c), source="wheel"))
        self.hex_edit.editingFinished.connect(self._on_hex)
        self._reload_favorites()
        self._sync_widgets(None)

    # ---------------------------------------------------------------- API
    @property
    def color(self) -> tuple[float, float, float]:
        return self._rgb

    def activate(self) -> None:
        self.engine.set_manual_color(self._rgb)
        self.engine.set_mode(Mode.MANUAL)

    def reload(self) -> None:
        """Pull state from settings (after a scene is applied)."""
        self._rgb = tuple(float(v) for v in self.settings["manual_color"])
        self._sync_widgets(None)
        self.engine.set_manual_color(self._rgb)
        self._reload_favorites()

    def set_color(self, rgb, source: str | None = None) -> None:
        self._rgb = tuple(float(max(0, min(255, round(v)))) for v in rgb)
        self._sync_widgets(source)
        self.engine.set_manual_color(self._rgb)
        self.settings["manual_color"] = [int(v) for v in self._rgb]
        self.changed.emit()

    def apply_preset(self, index: int) -> None:
        if 0 <= index < len(PRESETS):
            self.set_color(PRESETS[index][1])

    def save_favorite(self, name: str | None = None) -> None:
        if name is None:
            name, ok = QInputDialog.getText(self, "Save favorite", "Name:", text=to_hex(self._rgb))
            if not ok:
                return
        self.settings["favorites"].append({"name": name.strip() or to_hex(self._rgb),
                                           "color": [int(v) for v in self._rgb]})
        self._reload_favorites()
        self.changed.emit()

    # ---------------------------------------------------------------- internals
    def _sync_widgets(self, source: str | None) -> None:
        c = qcolor(self._rgb)
        if source != "wheel":
            self.wheel.setColor(c)
        if source != "slider":
            for s, v in zip(self.sliders, self._rgb):
                s.setValue(int(v), silent=True)
        if source != "hex":
            self.hex_edit.setText(to_hex(self._rgb))
        self.swatch.setColor(c)
        current = tuple(int(v) for v in self._rgb)
        for sw, (_name, rgb) in zip(self.preset_swatches, PRESETS):
            sw.setSelected(tuple(rgb) == current)  # ring on the preset that is showing

    def _on_slider(self, _v: int) -> None:
        self.set_color(tuple(s.value() for s in self.sliders), source="slider")

    def _on_hex(self) -> None:
        rgb = from_hex(self.hex_edit.text())
        if rgb is None:
            self.hex_edit.setText(to_hex(self._rgb))
        elif rgb != self._rgb:
            self.set_color(rgb, source="hex")

    def _reload_favorites(self) -> None:
        self.fav_list.clear()
        for fav in self.settings["favorites"]:
            item = QListWidgetItem(_dot_icon(fav["color"]), fav["name"])
            item.setToolTip(f"{fav['name']}  {to_hex(fav['color'])}")
            self.fav_list.addItem(item)
        has = bool(self.settings["favorites"])
        self.fav_list.setVisible(has)
        self.fav_hint.setVisible(has)
        self.fav_empty.setVisible(not has)

    def _fav_clicked(self, item: QListWidgetItem) -> None:
        row = self.fav_list.row(item)
        self.set_color(self.settings["favorites"][row]["color"])

    def _delete_selected_favorite(self) -> None:
        row = self.fav_list.currentRow()
        if row >= 0:
            del self.settings["favorites"][row]
            self._reload_favorites()
            self.changed.emit()

    def _fav_menu(self, pos) -> None:
        item = self.fav_list.itemAt(pos)
        if item is None:
            return
        row = self.fav_list.row(item)
        fav = self.settings["favorites"][row]
        menu = QMenu(self)
        rename = menu.addAction("Rename…")
        overwrite = menu.addAction("Replace with current color")
        menu.addSeparator()
        delete = menu.addAction("Delete")
        chosen = menu.exec(self.fav_list.mapToGlobal(pos))
        if chosen == rename:
            name, ok = QInputDialog.getText(self, "Rename favorite", "Name:", text=fav["name"])
            if ok and name.strip():
                fav["name"] = name.strip()
        elif chosen == overwrite:
            fav["color"] = [int(v) for v in self._rgb]
        elif chosen == delete:
            del self.settings["favorites"][row]
        else:
            return
        self._reload_favorites()
        self.changed.emit()
