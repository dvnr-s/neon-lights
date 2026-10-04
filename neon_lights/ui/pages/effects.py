"""Effects mode: PC-rendered animations plus the strip's own built-in animations."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QComboBox, QLabel, QListWidget, QPushButton, QWidget

from ...engine import LightingEngine, Mode
from ...engine.effects import EFFECTS
from ..widgets import ColorSwatch, HBox, LabeledSlider, VBox, card, label, repolish, rgb_of

_EFFECT_HINTS = {
    "Static": "Solid Color 1.",
    "Fade": "Smoothly blends through Colors 1 → 2 → 3.",
    "Pulse": "Breathes Color 1 in and out.",
    "Rainbow": "Cycles through the full hue wheel. Ignores the palette.",
    "Color Jump": "Snaps between Colors 1, 2 and 3.",
    "Strobe": "Flashes Colors 1, 2 and 3. Use with care.",
    "Candle": "Warm flicker using Color 1 (try an orange).",
}


class EffectsPage(QWidget):
    changed = Signal()

    def __init__(self, engine: LightingEngine, settings: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.engine = engine
        self.settings = settings
        self._names = list(EFFECTS)

        root = HBox(self, spacing=14)

        # --- PC effects
        self.pc_card, pl = card()
        self.pc_badge = self._card_header(pl, "Effects")
        pl.addWidget(label("Rendered on this PC and streamed to the strip.", "hint"))
        body = HBox(spacing=14)
        self.list = QListWidget()
        self.list.addItems(self._names)
        self.list.setFixedWidth(150)
        body.addWidget(self.list)

        opts = VBox(spacing=12)
        self.speed = LabeledSlider("Speed", 0, 100, 50, "%")
        self.speed.title.setMinimumWidth(0)  # leave the slider room in a narrow window
        opts.addWidget(self.speed)
        opts.addWidget(label("Palette", "section"))
        sw_row = HBox(spacing=10)
        self.palette = []
        for i in range(3):
            sw = ColorSwatch((255, 255, 255), size=44, pickable=True, tooltip=f"Color {i + 1} - click to change")
            sw.colorChanged.connect(self._on_palette)
            self.palette.append(sw)
            sw_row.addWidget(sw)
        sw_row.addStretch(1)
        opts.addLayout(sw_row)
        self.hint = label("", "hint")
        self.hint.setWordWrap(True)
        opts.addWidget(self.hint)
        opts.addStretch(1)
        body.addLayout(opts, 1)
        pl.addLayout(body, 1)
        root.addWidget(self.pc_card, 3)

        # --- built-in device effects
        self.dev_card, dl = card()
        self.dev_badge = self._card_header(dl, "Built-in strip animations")
        hint = label("Run on the LED controller itself - smooth and no PC load. "
                     "Brightness uses the strip's hardware dimmer here.", "hint")
        hint.setWordWrap(True)
        dl.addWidget(hint)
        self.model_label = label("", "hint")
        dl.addWidget(self.model_label)
        self.device_combo = QComboBox()
        dl.addWidget(self.device_combo)
        self.device_speed = LabeledSlider("Speed", 0, 100, 50, "%")
        dl.addWidget(self.device_speed)
        self.play_device = QPushButton("▶  Play on strip")
        self.play_device.setObjectName("primary")
        dl.addWidget(self.play_device)
        self.now_playing = label("", "hint")
        self.now_playing.setWordWrap(True)
        dl.addWidget(self.now_playing)
        dl.addStretch(1)
        root.addWidget(self.dev_card, 2)

        self.reload_device_effects()
        self.reload()

        self.list.currentTextChanged.connect(self._on_effect)
        self.list.itemClicked.connect(lambda _i: self.use_pc())
        self.speed.valueChanged.connect(self._on_speed)
        self.device_combo.currentTextChanged.connect(self._on_device_changed)
        self.device_speed.valueChanged.connect(self._on_device_changed)
        self.play_device.clicked.connect(self.play_on_strip)

    # ---------------------------------------------------------------- API
    def activate(self) -> None:
        # Always resume the PC effect: on-strip animations only run when explicitly started.
        self.use_pc()

    def reload(self) -> None:
        """Pull widget state from settings (after a scene is applied)."""
        cfg = self.settings["effect"]
        name = cfg["name"] if cfg["name"] in EFFECTS else "Rainbow"
        self.list.blockSignals(True)
        self.list.setCurrentRow(self._names.index(name))
        self.list.blockSignals(False)
        self.speed.setValue(int(cfg["speed"]), silent=True)
        colors = (list(cfg["colors"]) + [[255, 255, 255]] * 3)[:3]
        for sw, rgb in zip(self.palette, colors):
            sw.setColor(rgb)
        dev = self.settings["device_effect"]
        if dev.get("name") and self.device_combo.findText(dev["name"]) >= 0:
            self.device_combo.blockSignals(True)
            self.device_combo.setCurrentText(dev["name"])
            self.device_combo.blockSignals(False)
        self.device_speed.setValue(int(dev.get("speed", 50)), silent=True)
        self._update_hint(name)
        self.engine.set_effect(name)
        self.engine.set_effect_speed(cfg["speed"])
        self.engine.set_effect_colors([tuple(c) for c in colors])
        self._update_now_playing()

    def reload_device_effects(self) -> None:
        """Repopulate built-in animations for the connected strip's model."""
        protocol = self.engine.ble.protocol
        wanted = self.settings["device_effect"].get("name") or self.device_combo.currentText()
        self.device_combo.blockSignals(True)
        self.device_combo.clear()
        self.device_combo.addItems(list(protocol.device_effects))
        index = self.device_combo.findText(wanted)
        self.device_combo.setCurrentIndex(max(0, index))
        self.device_combo.blockSignals(False)
        self.model_label.setText(f"Controller profile: {protocol.profile.label}")

    def step_effect(self, delta: int) -> None:
        row = (self.list.currentRow() + delta) % self.list.count()
        self.list.setCurrentRow(row)
        self.use_pc()

    def nudge_speed(self, delta: int) -> None:
        target = self.device_speed if self.engine.mode == Mode.DEVICE_EFFECT else self.speed
        target.setValue(max(0, min(100, target.value() + delta)))

    def use_pc(self) -> None:
        self.engine.set_mode(Mode.EFFECT)
        self._update_now_playing()

    def play_on_strip(self) -> None:
        self._push_device()
        self.engine.set_mode(Mode.DEVICE_EFFECT)
        self._update_now_playing()

    # ---------------------------------------------------------------- internals
    @staticmethod
    def _card_header(lay, title: str) -> QLabel:
        """Section title with a hidden "Playing" badge on the right; returns the badge."""
        head = HBox()
        head.addWidget(label(title, "section"))
        head.addStretch(1)
        badge = label("● Playing", "badge")
        badge.hide()
        head.addWidget(badge)
        lay.addLayout(head)
        return badge

    def _push_device(self) -> None:
        effects = self.engine.ble.protocol.device_effects
        name = self.device_combo.currentText()
        if name in effects:
            self.engine.set_device_effect(effects[name], self.device_speed.value())

    def _on_effect(self, name: str) -> None:
        if not name:
            return
        self.engine.set_effect(name)
        self.settings["effect"]["name"] = name
        self._update_hint(name)
        self.use_pc()
        self.changed.emit()

    def _on_speed(self, v: int) -> None:
        self.engine.set_effect_speed(v)
        self.settings["effect"]["speed"] = v
        self.changed.emit()

    def _on_palette(self, _c) -> None:
        colors = [rgb_of(sw.color()) for sw in self.palette]
        self.engine.set_effect_colors(colors)
        self.settings["effect"]["colors"] = [[int(v) for v in c] for c in colors]
        self.changed.emit()

    def _on_device_changed(self, *_args) -> None:
        self.settings["device_effect"] = {
            "name": self.device_combo.currentText(),
            "speed": self.device_speed.value(),
        }
        if self.engine.mode == Mode.DEVICE_EFFECT:
            self._push_device()
        self.changed.emit()

    def _update_hint(self, name: str) -> None:
        self.hint.setText(_EFFECT_HINTS.get(name, ""))

    def _update_now_playing(self) -> None:
        mode = self.engine.mode
        if mode == Mode.DEVICE_EFFECT:
            text = f"▶ Playing on strip: {self.device_combo.currentText()}. Pick a PC effect to switch back."
        else:
            text = "Press Play to run this animation on the strip instead of the PC effect."
        self.now_playing.setText(text)
        # Accent border + badge on whichever card is driving the strip.
        for frame, badge, active in ((self.pc_card, self.pc_badge, mode == Mode.EFFECT),
                                     (self.dev_card, self.dev_badge, mode == Mode.DEVICE_EFFECT)):
            badge.setVisible(active)
            if frame.property("active") != active:
                frame.setProperty("active", active)
                repolish(frame)
