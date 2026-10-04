"""Scene bar: one-click snapshots of a whole lighting setup (mode, effect, palette, brightness...)."""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QInputDialog, QMenu, QMessageBox, QPushButton, QWidget

from .widgets import HBox, label

MAX_SHORTCUT_SCENES = 9


class SceneBar(QWidget):
    changed = Signal()            # scenes list edited (save settings)
    applied = Signal(str)         # scene name

    def __init__(self, settings: dict, snapshot: Callable[[], dict], apply: Callable[[dict], None],
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self._snapshot = snapshot
        self._apply = apply
        self._layout = HBox(self, spacing=8)
        self._chips: list[QPushButton] = []
        self._active: int | None = None
        self._rebuild()

    @property
    def scenes(self) -> list[dict]:
        return self.settings.setdefault("scenes", [])

    def apply_index(self, index: int) -> None:
        if 0 <= index < len(self.scenes):
            scene = self.scenes[index]
            self._apply(scene["state"])
            self.set_active(index)  # after applying, so the edits it caused don't clear it
            self.applied.emit(scene["name"])

    def set_active(self, index: int | None) -> None:
        """Highlight the scene that is currently showing (None = none)."""
        self._active = index
        for i, chip in enumerate(self._chips):
            chip.setChecked(i == index)

    def save_new(self) -> None:
        name, ok = QInputDialog.getText(self, "Save scene",
                                        "Scene name (saves mode, effect, colors and brightness):",
                                        text=f"Scene {len(self.scenes) + 1}")
        if ok and name.strip():
            self.scenes.append({"name": name.strip(), "state": self._snapshot()})
            self._rebuild()
            self.changed.emit()

    # ---------------------------------------------------------------- internals
    def _rebuild(self) -> None:
        while self._layout.count():
            item = self._layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._chips = []
        self._layout.addWidget(label("Scenes", "muted"))
        for i, scene in enumerate(self.scenes):
            chip = QPushButton(scene["name"])
            chip.setObjectName("sceneChip")
            chip.setCheckable(True)
            chip.setChecked(i == self._active)
            chip.setFocusPolicy(Qt.NoFocus)
            chip.setCursor(Qt.PointingHandCursor)
            tip = f"Apply '{scene['name']}'"
            if i < MAX_SHORTCUT_SCENES:
                tip += f"  (Ctrl+Shift+{i + 1})"
            chip.setToolTip(tip + "\nRight-click to update, rename or delete")
            chip.clicked.connect(lambda _=False, i=i: self.apply_index(i))
            self._chips.append(chip)
            chip.setContextMenuPolicy(Qt.CustomContextMenu)
            chip.customContextMenuRequested.connect(lambda pos, i=i, c=chip: self._menu(i, c, pos))
            self._layout.addWidget(chip)
        add = QPushButton("＋ Save scene")
        add.setObjectName("sceneChip")
        add.setToolTip("Save the current setup as a scene (Ctrl+Shift+S)")
        add.clicked.connect(self.save_new)
        self._layout.addWidget(add)
        self._layout.addStretch(1)

    def _menu(self, index: int, chip: QPushButton, pos) -> None:
        scene = self.scenes[index]
        menu = QMenu(self)
        update = menu.addAction("Update with current setup")
        rename = menu.addAction("Rename…")
        left = menu.addAction("Move left")
        left.setEnabled(index > 0)
        menu.addSeparator()
        delete = menu.addAction("Delete")
        chosen = menu.exec(chip.mapToGlobal(pos))
        if chosen == update:
            scene["state"] = self._snapshot()
        elif chosen == rename:
            name, ok = QInputDialog.getText(self, "Rename scene", "Name:", text=scene["name"])
            if not (ok and name.strip()):
                return
            scene["name"] = name.strip()
        elif chosen == left:
            self.scenes[index - 1], self.scenes[index] = self.scenes[index], self.scenes[index - 1]
        elif chosen == delete:
            if QMessageBox.question(self, "Delete scene", f"Delete scene '{scene['name']}'?") != QMessageBox.Yes:
                return
            del self.scenes[index]
        else:
            return
        if chosen in (left, delete):
            self._active = None  # positions shifted
        self._rebuild()
        self.changed.emit()
