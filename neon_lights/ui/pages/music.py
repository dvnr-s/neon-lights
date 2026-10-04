"""Music-reactive mode: capture system audio and light the strip by bass/mids/treble."""

from __future__ import annotations

import time

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QComboBox, QFormLayout, QPushButton, QWidget

from ...engine import LightingEngine, Mode
from ...engine.audio import AudioAnalyzer, smoothing_times
from ...engine.music import MUSIC_MODES
from .. import theme
from ..widgets import ColorSwatch, GlowPreview, HBox, LabeledSlider, LevelMeters, card, label, qcolor, rgb_of


_BLUETOOTH_HINTS = ("bluetooth", "buds", "airpods", "hands-free", "wh-", "wf-", "jbl", "bose", "beats",
                    "galaxy", "soundcore", "pixel buds", "headset")


class MusicPage(QWidget):
    changed = Signal()

    def __init__(self, engine: LightingEngine, settings: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.engine = engine
        self.settings = settings
        cfg = settings["music"]
        self._beat_seen = 0
        self._beat_time = 0.0

        root = HBox(self, spacing=14)

        ctl_card, cl = card(spacing=12)
        cl.addWidget(label("Music sync", "section"))
        cl.addWidget(label("Listens to whatever your PC is playing (no microphone needed).", "hint"))

        form = QFormLayout()
        form.setSpacing(10)
        dev_row = HBox(spacing=6)
        self.device = QComboBox()
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.setToolTip("Refresh audio devices / restart capture")
        dev_row.addWidget(self.device, 1)
        dev_row.addWidget(self.refresh_btn)
        form.addRow("Output", dev_row)

        self.mode = QComboBox()
        for key, text in MUSIC_MODES.items():
            self.mode.addItem(text, key)
        self.mode.setCurrentIndex(max(0, self.mode.findData(cfg["mode"])))
        form.addRow("Style", self.mode)

        color_row = HBox()
        self.color = ColorSwatch(cfg["color"], size=32, pickable=True, tooltip="Your color for Bass pulse, Drop strobe and Mood mix")
        color_row.addWidget(self.color)
        color_row.addWidget(label("used by Bass pulse, Drop strobe, Mood mix", "hint"))
        color_row.addStretch(1)
        form.addRow("Color", color_row)
        cl.addLayout(form)

        self.sensitivity = LabeledSlider("Sensitivity", 0, 100, int(cfg["sensitivity"]), "%")
        self.sensitivity.setToolTip("Higher = reacts to quieter sounds and triggers more beats")
        self.smoothing = LabeledSlider("Smoothing", 0, 100, int(cfg["smoothing"]), "%")
        self.smoothing.setToolTip("How long the lights take to fade after each hit (shown on the right)")
        self.smoothing.value_label.setMinimumWidth(96)
        self.delay = LabeledSlider("Light delay", 0, 400, int(cfg.get("delay_ms", 0)), " ms")
        self.delay.slider.setSingleStep(10)
        self.delay.slider.setPageStep(50)
        self.delay.setToolTip("Bluetooth headphones play sound ~150-300 ms after the PC does, so the lights "
                              "run ahead. Delay the lights until they match what you hear. Keep 0 for wired "
                              "speakers.")
        cl.addWidget(self.sensitivity)
        cl.addWidget(self.smoothing)
        cl.addWidget(self.delay)
        self.bt_hint = label("", "hint")
        self.bt_hint.setWordWrap(True)
        cl.addWidget(self.bt_hint)
        cl.addStretch(1)
        root.addWidget(ctl_card, 3)

        meter_card, ml = card()
        ml.addWidget(label("Levels", "section"))
        self.meters = LevelMeters([
            ("bass", "Bass", theme.ERROR), ("mids", "Mids", theme.OK),
            ("treble", "Treble", "#3d8bff"), ("volume", "Volume", theme.ACCENT),
        ])
        ml.addWidget(self.meters)
        self.beat = GlowPreview()
        self.beat.setMinimumHeight(70)
        self.beat.setMaximumHeight(110)
        ml.addWidget(self.beat)
        self.tempo = label("Tempo: -", "muted")
        ml.addWidget(self.tempo)
        self.stats = label("Not running", "muted")
        self.stats.setWordWrap(True)
        ml.addWidget(self.stats)
        ml.addStretch(1)
        root.addWidget(meter_card, 2)

        self._load_devices()
        self._apply()

        self.refresh_btn.clicked.connect(self._refresh_devices)
        self.device.currentIndexChanged.connect(self._on_device)
        self.mode.currentIndexChanged.connect(self._apply)
        self.color.colorChanged.connect(self._apply)
        self.sensitivity.valueChanged.connect(self._apply)
        self.smoothing.valueChanged.connect(self._apply)
        self.delay.valueChanged.connect(self._apply)

    def activate(self) -> None:
        self._apply()
        self.engine.set_mode(Mode.MUSIC)

    def reload(self) -> None:
        """Pull widget state from settings (after a scene is applied)."""
        cfg = self.settings["music"]
        self.mode.blockSignals(True)
        self.mode.setCurrentIndex(max(0, self.mode.findData(cfg["mode"])))
        self.mode.blockSignals(False)
        self.color.setColor(cfg["color"])
        self.sensitivity.setValue(int(cfg["sensitivity"]), silent=True)
        self.smoothing.setValue(int(cfg["smoothing"]), silent=True)
        self.delay.setValue(int(cfg.get("delay_ms", 0)), silent=True)
        self._apply()

    def refresh(self) -> None:
        audio = self.engine.audio
        f = audio.features
        self.meters.setLevels({"bass": f.bass, "mids": f.mids, "treble": f.treble, "volume": f.volume})
        if f.beat_count != self._beat_seen:
            self._beat_seen = f.beat_count
            self._beat_time = time.perf_counter()
        flashing = time.perf_counter() - self._beat_time < 0.12
        caption = "DROP!" if self.engine.music.strobing else ("BEAT" if flashing else " ")
        self.beat.setColor(qcolor(self.engine.output_color), caption)
        self.tempo.setText(f"Tempo: {f.bpm:.0f} BPM" if f.bpm else "Tempo: listening for a steady beat…")
        if audio.error:
            self.stats.setText(audio.error)
        elif audio.running:
            state = "hearing audio" if f.volume > 0.02 else "waiting for audio…"
            name = audio.device_name.replace(" [Loopback]", "") or "Default output"
            self.stats.setText(f"{name} - {state}")
            self._update_bt_hint(name)
        else:
            self.stats.setText("Not running")

    def _load_devices(self) -> None:
        want = self.settings["music"]["device"]
        self.device.blockSignals(True)
        self.device.clear()
        self.device.addItem("Default output device", None)
        for index, name in AudioAnalyzer.list_devices():
            self.device.addItem(name.replace(" [Loopback]", ""), index)
        self.device.setCurrentIndex(max(0, self.device.findText(want)) if want else 0)
        self.device.blockSignals(False)
        self.engine.audio.device_index = self.device.currentData()

    def _refresh_devices(self) -> None:
        self._load_devices()
        self.engine.audio.restart()  # reopen so a newly connected output is picked up

    def _update_bt_hint(self, device_name: str) -> None:
        wireless = any(k in device_name.lower() for k in _BLUETOOTH_HINTS)
        if wireless and self.delay.value() == 0:
            text = "Bluetooth headphones detected: their sound arrives late. Try a Light delay of about 200 ms."
        else:
            text = ""
        if text != self.bt_hint.text():
            self.bt_hint.setText(text)

    def _on_device(self, *_args) -> None:
        index = self.device.currentData()
        self.settings["music"]["device"] = None if index is None else self.device.currentText()
        self.engine.set_audio_device(index)
        self.changed.emit()

    def _apply(self, *_args) -> None:
        cfg = self.settings["music"]
        cfg.update({
            "mode": self.mode.currentData(),
            "sensitivity": self.sensitivity.value(),
            "smoothing": self.smoothing.value(),
            "delay_ms": self.delay.value(),
            "color": [int(v) for v in rgb_of(self.color.color())],
        })
        _attack, release = smoothing_times(cfg["smoothing"] / 100.0)
        fade_s = 2.3 * release  # time for the lights to fall to 10 % after a hit
        fade = f"{fade_s * 1000:.0f} ms" if fade_s < 1 else f"{fade_s:.1f} s"
        self.smoothing.value_label.setText(f"{cfg['smoothing']}% · {fade}")
        self.engine.audio.output_delay = cfg["delay_ms"] / 1000.0
        self.engine.configure_music(
            mode=cfg["mode"],
            color=tuple(cfg["color"]),
            sensitivity=cfg["sensitivity"] / 100.0,
            smoothing=cfg["smoothing"] / 100.0,
        )
        self.changed.emit()
