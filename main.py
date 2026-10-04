"""Neon Lights entry point:  python main.py"""

from __future__ import annotations

import logging
import sys


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    logging.getLogger("bleak").setLevel(logging.WARNING)

    if sys.platform == "win32":
        try:  # own taskbar icon/grouping instead of python.exe's
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("NeonLights.App")
            # 1 ms timer resolution: Windows' default 15.6 ms makes 30-50 Hz pacing stutter.
            ctypes.windll.winmm.timeBeginPeriod(1)
        except Exception:
            pass

    from PySide6.QtWidgets import QApplication

    from neon_lights import APP_NAME
    from neon_lights.ble import BleController
    from neon_lights.config import Settings
    from neon_lights.engine import LightingEngine
    from neon_lights.ui.main_window import MainWindow
    from neon_lights.ui.theme import apply_theme, make_app_icon

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setWindowIcon(make_app_icon())
    apply_theme(app)

    settings = Settings.load()
    ble = BleController(
        max_send_rate_hz=float(settings["max_send_rate_hz"]),
        write_with_response=settings["write_with_response"],
    )
    ble.start()
    engine = LightingEngine(ble)
    engine.start()

    window = MainWindow(settings, ble, engine)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
