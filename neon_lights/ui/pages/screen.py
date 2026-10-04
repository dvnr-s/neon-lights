"""Screen-reactive mode: sample the monitor and drive the strip with its colors."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QCheckBox, QComboBox, QFormLayout, QPushButton, QWidget

from ...engine import LightingEngine, Mode
from ...engine.screen import REGIONS, SAMPLE_MODES, ScreenSampler
from ..widgets import GlowPreview, HBox, LabeledSlider, card, label, qcolor


class ScreenPage(QWidget):
    changed = Signal()

    def __init__(self, engine: LightingEngine, settings: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.engine = engine
        self.settings = settings
        cfg = settings["screen"]

        root = HBox(self, spacing=14)

        ctl_card, cl = card(spacing=12)
        cl.addWidget(label("Screen sync", "section"))
        cl.addWidget(label("Matches the strip to what's on your monitor.", "hint"))

        form = QFormLayout()
        form.setSpacing(10)
        mon_row = HBox(spacing=6)
        self.monitor = QComboBox()
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.setToolTip("Refresh monitor list")
        mon_row.addWidget(self.monitor, 1)
        mon_row.addWidget(self.refresh_btn)
        form.addRow("Monitor", mon_row)

        self.region = QComboBox()
        for key, text in REGIONS.items():
            self.region.addItem(text, key)
        self.region.setCurrentIndex(max(0, self.region.findData(cfg["region"])))
        form.addRow("Area", self.region)

        self.mode = QComboBox()
        for key, text in SAMPLE_MODES.items():
            self.mode.addItem(text, key)
        self.mode.setCurrentIndex(max(0, self.mode.findData(cfg["mode"])))
        form.addRow("Color pick", self.mode)
        cl.addLayout(form)

        self.sensitivity = LabeledSlider("Sensitivity", 0, 100, int(cfg["sensitivity"]), "%")
        self.sensitivity.setToolTip("Higher = more saturated, brighter, more responsive to vivid areas")
        self.smoothing = LabeledSlider("Smoothing", 0, 95, int(cfg["smoothing"]), "%")
        self.smoothing.setToolTip("Higher = slower, softer transitions; lower = snappier")
        self.rate = LabeledSlider("Update rate", 5, 60, int(cfg["rate_hz"]), " Hz")
        self.rate.setToolTip("How often the screen is sampled. The strip itself accepts ~30 updates/s.")
        for w in (self.sensitivity, self.smoothing, self.rate):
            cl.addWidget(w)
        self.fast = QCheckBox("Fast GPU capture (DXGI)")
        self.fast.setToolTip("Uses Windows desktop duplication: ~60 fps for a few % CPU. "
                             "Turn off if a game or app captures as black.")
        self.fast.setChecked(bool(cfg.get("fast_capture", True)))
        cl.addWidget(self.fast)
        cl.addStretch(1)
        root.addWidget(ctl_card, 3)

        prev_card, pl = card()
        pl.addWidget(label("Live sample", "section"))
        self.preview = GlowPreview()
        self.preview.setMinimumHeight(120)
        self.preview.setMaximumHeight(170)
        pl.addWidget(self.preview)
        self.stats = label("Not running", "muted")
        self.stats.setWordWrap(True)
        pl.addWidget(self.stats)
        pl.addStretch(1)
        root.addWidget(prev_card, 2)

        self._load_monitors()
        self._apply()

        self.refresh_btn.clicked.connect(self._load_monitors)
        self.monitor.currentIndexChanged.connect(self._apply)
        self.region.currentIndexChanged.connect(self._apply)
        self.mode.currentIndexChanged.connect(self._apply)
        for w in (self.sensitivity, self.smoothing, self.rate):
            w.valueChanged.connect(self._apply)
        self.fast.toggled.connect(self._on_fast)

    def activate(self) -> None:
        self._apply()
        self.engine.set_mode(Mode.SCREEN)

    def reload(self) -> None:
        """Pull widget state from settings (after a scene is applied)."""
        cfg = self.settings["screen"]
        for combo, value in ((self.monitor, cfg["monitor"]), (self.region, cfg["region"]), (self.mode, cfg["mode"])):
            combo.blockSignals(True)
            combo.setCurrentIndex(max(0, combo.findData(value)))
            combo.blockSignals(False)
        self.sensitivity.setValue(int(cfg["sensitivity"]), silent=True)
        self.smoothing.setValue(int(cfg["smoothing"]), silent=True)
        self.rate.setValue(int(cfg["rate_hz"]), silent=True)
        self._apply()

    def refresh(self) -> None:
        sampler = self.engine.screen
        self.preview.setColor(qcolor(sampler.latest))
        if sampler.error:
            self.stats.setText(sampler.error)
        elif sampler.running:
            backend = f" via {sampler.backend}" if sampler.backend else ""
            self.stats.setText(f"Sampling at {sampler.fps:.0f} fps{backend}")
        else:
            self.stats.setText("Not running")

    def _on_fast(self, on: bool) -> None:
        self.settings["screen"]["fast_capture"] = on
        self.engine.screen.prefer_dxgi = on
        self.engine.screen.reopen()
        self.changed.emit()

    def _load_monitors(self) -> None:
        want = self.settings["screen"]["monitor"]
        self.monitor.blockSignals(True)
        self.monitor.clear()
        for index, text in ScreenSampler.list_monitors():
            self.monitor.addItem(text, index)
        self.monitor.setCurrentIndex(max(0, self.monitor.findData(want)))
        self.monitor.blockSignals(False)

    def _apply(self, *_args) -> None:
        cfg = {
            "monitor": self.monitor.currentData() if self.monitor.currentData() is not None else 1,
            "region": self.region.currentData(),
            "mode": self.mode.currentData(),
            "sensitivity": self.sensitivity.value(),
            "smoothing": self.smoothing.value(),
            "rate_hz": self.rate.value(),
        }
        self.settings["screen"].update(cfg)
        self.engine.screen.prefer_dxgi = bool(self.settings["screen"].get("fast_capture", True))
        self.engine.configure_screen(
            monitor=cfg["monitor"],
            region=cfg["region"],
            mode=cfg["mode"],
            sensitivity=cfg["sensitivity"] / 100.0,
            smoothing=cfg["smoothing"] / 100.0,
            rate_hz=float(cfg["rate_hz"]),
        )
        self.changed.emit()
