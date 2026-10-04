"""Color calibration dialog: white balance (per-channel gain) + gamma, with test patterns."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QButtonGroup, QDialog, QPushButton, QWidget

from ..engine import Calibration, LightingEngine
from .widgets import HBox, LabeledSlider, VBox, card, label

TEST_PATTERNS = [
    ("Off", None),
    ("White", (255, 255, 255)),
    ("50% grey", (128, 128, 128)),
    ("Warm white", (255, 170, 90)),
    ("Red", (255, 0, 0)),
    ("Green", (0, 255, 0)),
    ("Blue", (0, 0, 255)),
]


def calibration_from_settings(cfg: dict) -> Calibration:
    return Calibration(
        red=cfg.get("red", 100) / 100.0,
        green=cfg.get("green", 100) / 100.0,
        blue=cfg.get("blue", 100) / 100.0,
        gamma=float(cfg.get("gamma", 1.0)),
    )


class CalibrationDialog(QDialog):
    changed = Signal()

    def __init__(self, engine: LightingEngine, settings: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.engine = engine
        self.settings = settings
        self.setWindowTitle("Color calibration")
        self.setMinimumWidth(520)
        self.setObjectName("root")

        root = VBox(self, spacing=12, margins=16)

        test_card, tl = card()
        tl.addWidget(label("1. Show a test pattern on the strip", "section"))
        row = HBox(spacing=6)
        self.patterns = QButtonGroup(self)
        for i, (name, _rgb) in enumerate(TEST_PATTERNS):
            b = QPushButton(name)
            b.setCheckable(True)
            b.setObjectName("sceneChip")
            self.patterns.addButton(b, i)
            row.addWidget(b)
        self.patterns.button(1).setChecked(True)
        tl.addLayout(row)
        root.addWidget(test_card)

        wb_card, wl = card()
        wl.addWidget(label("2. White balance", "section"))
        hint = label("With White showing, lower whichever channel looks too strong until the strip "
                     "looks neutral white (most cheap strips run blue or green).", "hint")
        hint.setWordWrap(True)
        wl.addWidget(hint)
        cfg = settings["calibration"]
        self.red = LabeledSlider("Red", 0, 100, int(cfg["red"]), "%", object_name="red")
        self.green = LabeledSlider("Green", 0, 100, int(cfg["green"]), "%", object_name="green")
        self.blue = LabeledSlider("Blue", 0, 100, int(cfg["blue"]), "%", object_name="blue")
        for s in (self.red, self.green, self.blue):
            wl.addWidget(s)
        root.addWidget(wb_card)

        gamma_card, gl = card()
        gl.addWidget(label("3. Gamma", "section"))
        hint = label("LEDs are linear, eyes aren't. Raise gamma (try 1.8–2.2) if colors look washed out "
                     "and mixes like orange or pink look too white; 1.00 leaves colors untouched.", "hint")
        hint.setWordWrap(True)
        gl.addWidget(hint)
        self.gamma = LabeledSlider("Gamma", 100, 280, int(round(float(cfg["gamma"]) * 100)))
        self.gamma.value_label.setText(f"{self.gamma.value() / 100:.2f}")
        gl.addWidget(self.gamma)
        root.addWidget(gamma_card)

        buttons = HBox()
        reset = QPushButton("Reset")
        reset.clicked.connect(self._reset)
        buttons.addWidget(reset)
        buttons.addStretch(1)
        done = QPushButton("Done")
        done.setObjectName("primary")
        done.clicked.connect(self.close)
        buttons.addWidget(done)
        root.addLayout(buttons)

        for s in (self.red, self.green, self.blue, self.gamma):
            s.valueChanged.connect(self._apply)
        self.patterns.idClicked.connect(self._show_pattern)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._show_pattern(self.patterns.checkedId())

    def closeEvent(self, event) -> None:
        self.engine.set_test_color(None)
        super().closeEvent(event)

    def reject(self) -> None:  # Esc
        self.close()

    def _show_pattern(self, index: int) -> None:
        self.engine.set_test_color(TEST_PATTERNS[index][1])

    def _apply(self, *_args) -> None:
        self.gamma.value_label.setText(f"{self.gamma.value() / 100:.2f}")
        cfg = {
            "red": self.red.value(),
            "green": self.green.value(),
            "blue": self.blue.value(),
            "gamma": round(self.gamma.value() / 100.0, 2),
        }
        self.settings["calibration"] = cfg
        self.engine.set_calibration(calibration_from_settings(cfg))
        self.changed.emit()

    def _reset(self) -> None:
        for s in (self.red, self.green, self.blue):
            s.setValue(100, silent=True)
        self.gamma.setValue(100, silent=True)
        self._apply()

