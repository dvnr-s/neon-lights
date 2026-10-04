"""Render every page of the app to PNG for before/after UI checks. Never connects or saves settings.

    .venv\\Scripts\\python tools\\screenshots.py [out_dir]      (default: screenshots\\)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from neon_lights.ble import BleController  # noqa: E402
from neon_lights.config import Settings  # noqa: E402
from neon_lights.engine import LightingEngine  # noqa: E402
from neon_lights.ui.main_window import MODES, MainWindow  # noqa: E402
from neon_lights.ui.theme import apply_theme  # noqa: E402

Settings.save = lambda self: None  # the real settings file is never touched

SIZES = [("large", 1120, 840), ("small", 760, 560)]


def main() -> None:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / "screenshots")
    out.mkdir(parents=True, exist_ok=True)

    app = QApplication(sys.argv)
    apply_theme(app)
    settings = Settings.load()
    settings["connect_on_start"] = False
    ble = BleController(max_send_rate_hz=20)
    ble.start()
    engine = LightingEngine(ble)
    engine.start()  # colors are only queued; nothing is sent without a connection
    window = MainWindow(settings, ble, engine)
    window.move(-4000, 0)  # off-screen
    window.show()

    shots = [(size, w, h, i) for size, w, h in SIZES for i in range(len(MODES))]

    def shoot(n: int = 0) -> None:
        if n == len(shots):
            print(f"Saved {len(shots)} screenshots to {out}", flush=True)
            os._exit(0)  # skip closeEvent, which would save settings
        size, w, h, i = shots[n]
        window.resize(w, h)
        window.switch_mode(i)

        def grab() -> None:
            window.grab().save(str(out / f"{size}-{MODES[i][0]}.png"))
            shoot(n + 1)

        QTimer.singleShot(400, grab)

    QTimer.singleShot(1000, shoot)
    app.exec()


if __name__ == "__main__":
    main()
