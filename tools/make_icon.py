"""Render the app icon to assets/icon.ico (used by the PyInstaller build)."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtGui import QGuiApplication  # noqa: E402

from neon_lights.ui.theme import make_icon_pixmap  # noqa: E402

app = QGuiApplication(sys.argv)
out = ROOT / "assets" / "icon.ico"
out.parent.mkdir(exist_ok=True)
if not make_icon_pixmap(256).save(str(out), "ICO"):
    sys.exit("Could not write icon")
print("Wrote", out)
