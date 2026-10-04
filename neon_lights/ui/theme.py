"""Dark neon theme and the programmatic app icon."""

from __future__ import annotations

import tempfile
from pathlib import Path

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QConicalGradient, QIcon, QPainter, QPalette, QPixmap, QRadialGradient
from PySide6.QtWidgets import QApplication

BG = "#0c0e18"
PANEL = "#141726"
PANEL_2 = "#1c2034"
BORDER = "#2a2f4a"
TEXT = "#e7e9f4"
MUTED = "#8a90b0"
ACCENT = "#22e4ff"
ACCENT_2 = "#ff3dcb"
OK = "#3ddc84"
WARN = "#ffb020"
ERROR = "#ff4d6d"

_ICONS = {
    "chevron": f"""<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12">
<path d="M2.5 4.5 L6 8 L9.5 4.5" fill="none" stroke="{MUTED}" stroke-width="1.8"
 stroke-linecap="round" stroke-linejoin="round"/></svg>""",
    "check": """<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16">
<path d="M3.8 8.4 L6.7 11.2 L12.3 5.2" fill="none" stroke="#04121a" stroke-width="2.2"
 stroke-linecap="round" stroke-linejoin="round"/></svg>""",
}


def _icon_paths() -> dict[str, str]:
    """Write the small vector icons the stylesheet needs; returns QSS-safe paths."""
    folder = Path(tempfile.gettempdir()) / "neon_lights_ui"
    folder.mkdir(exist_ok=True)
    paths = {}
    for name, svg in _ICONS.items():
        file = folder / f"{name}.svg"
        if not file.exists() or file.read_text(encoding="utf-8") != svg:
            file.write_text(svg, encoding="utf-8")
        paths[name] = file.as_posix()
    return paths


def build_qss(icons: dict[str, str]) -> str:
    return f"""
* {{ font-family: "Segoe UI", sans-serif; font-size: 10pt; color: {TEXT}; }}
QMainWindow, QWidget#root {{ background: {BG}; }}
QWidget {{ background: transparent; }}
QToolTip {{ background: {PANEL_2}; color: {TEXT}; border: 1px solid {BORDER}; padding: 4px; }}

QFrame#card {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 12px; }}
QLabel#title {{ font-size: 18pt; font-weight: 700; color: {TEXT}; }}
QLabel#titleAccent {{ font-size: 18pt; font-weight: 700; color: {ACCENT}; }}
QLabel#section {{ font-size: 11pt; font-weight: 600; color: {TEXT}; }}
QLabel#muted {{ color: {MUTED}; }}
QLabel#hint {{ color: {MUTED}; font-size: 9pt; }}

QPushButton {{
    background: {PANEL_2}; border: 1px solid {BORDER}; border-radius: 8px;
    padding: 6px 14px; color: {TEXT}; outline: none;
}}
QPushButton:hover {{ border-color: {ACCENT}; }}
QPushButton:pressed {{ background: {BORDER}; }}
QPushButton:disabled {{ color: {MUTED}; border-color: {PANEL_2}; }}
QPushButton#primary {{ background: {ACCENT}; color: #04121a; border: none; font-weight: 600; }}
QPushButton#primary:hover {{ background: #6ff0ff; }}
QPushButton#danger {{ background: transparent; border: 1px solid {ERROR}; color: {ERROR}; }}

QPushButton#modeButton {{
    background: {PANEL}; border: 1px solid {BORDER}; border-radius: 10px;
    padding: 9px 18px; font-weight: 600; color: {MUTED}; min-width: 64px;
}}
QPushButton#modeButton:hover {{ color: {TEXT}; border-color: {ACCENT}; }}
QPushButton#modeButton:checked {{
    color: #04121a; border: none;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {ACCENT}, stop:1 {ACCENT_2});
}}

QPushButton#sceneChip {{
    background: {PANEL}; border: 1px solid {BORDER}; border-radius: 13px;
    padding: 4px 12px; color: {TEXT}; font-size: 9pt;
}}
QPushButton#sceneChip:hover {{ border-color: {ACCENT_2}; color: {ACCENT_2}; }}
QPushButton#sceneChip:checked {{ background: {ACCENT_2}; color: #1a0414; border-color: {ACCENT_2}; }}


QLineEdit, QComboBox, QSpinBox {{
    background: {PANEL_2}; border: 1px solid {BORDER}; border-radius: 8px; padding: 5px 8px;
    selection-background-color: {ACCENT}; selection-color: #04121a;
}}
QLineEdit:focus, QComboBox:focus {{ border-color: {ACCENT}; }}
QComboBox {{ padding-right: 26px; }}
QComboBox::drop-down {{ border: none; width: 26px; subcontrol-origin: padding; subcontrol-position: center right; }}
QComboBox::down-arrow {{ image: url("{icons['chevron']}"); width: 12px; height: 12px; }}
QComboBox::down-arrow:on {{ top: 1px; }}
QComboBox QAbstractItemView {{
    background: {PANEL_2}; border: 1px solid {BORDER}; selection-background-color: {BORDER};
    outline: none;
}}

/* Tall enough for the whole knob; the groove is inset so the knob is never clipped at the ends. */
QSlider:horizontal {{ min-height: 26px; outline: none; }}
QSlider::groove:horizontal {{ height: 6px; background: {PANEL_2}; border-radius: 3px; margin: 0 9px; }}
QSlider::sub-page:horizontal {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {ACCENT}, stop:1 {ACCENT_2});
    border-radius: 3px; margin-left: 9px;
}}
QSlider::add-page:horizontal {{ background: {PANEL_2}; border-radius: 3px; margin-right: 9px; }}
QSlider::handle:horizontal {{
    background: {TEXT}; border: 2px solid {BG};
    width: 14px; height: 14px; margin: -6px -9px; border-radius: 9px;
}}
QSlider::handle:horizontal:hover {{ background: #ffffff; border-color: {ACCENT}; }}
QSlider::handle:horizontal:pressed {{ background: {ACCENT}; border-color: {ACCENT}; }}
QSlider#red::sub-page:horizontal {{ background: #ff4d6d; }}
QSlider#green::sub-page:horizontal {{ background: #3ddc84; }}
QSlider#blue::sub-page:horizontal {{ background: #3d8bff; }}

QListWidget {{
    background: {PANEL_2}; border: 1px solid {BORDER}; border-radius: 8px; padding: 4px; outline: none;
}}
QListWidget::item {{ padding: 6px; border-radius: 6px; }}
QListWidget::item:selected {{ background: {BORDER}; color: {TEXT}; border-left: 3px solid {ACCENT}; }}
QListWidget::item:hover {{ background: {PANEL}; }}

QCheckBox {{ spacing: 8px; outline: none; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 5px; border: 1px solid {BORDER}; background: {PANEL_2}; }}
QCheckBox::indicator:hover {{ border-color: {ACCENT}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; image: url("{icons['check']}"); }}

QProgressBar {{ background: {PANEL_2}; border: none; border-radius: 4px; height: 10px; text-align: center; }}
QProgressBar::chunk {{ border-radius: 4px; background: {ACCENT}; }}
QProgressBar#bass::chunk {{ background: #ff4d6d; }}
QProgressBar#mids::chunk {{ background: #3ddc84; }}
QProgressBar#treble::chunk {{ background: #3d8bff; }}

QPlainTextEdit {{
    background: {BG}; border: 1px solid {BORDER}; border-radius: 8px;
    font-family: Consolas, monospace; font-size: 9pt;
}}
QDockWidget {{ color: {MUTED}; }}
QMainWindow::separator {{ background: {BORDER}; width: 3px; height: 3px; }}
QScrollArea {{ border: none; background: transparent; }}
QDockWidget::title {{ background: {PANEL}; padding: 6px; }}
QMenuBar {{ background: {BG}; }}
QMenuBar::item:selected {{ background: {PANEL_2}; }}
QMenu {{ background: {PANEL_2}; border: 1px solid {BORDER}; padding: 4px; }}
QMenu::item {{ padding: 6px 22px 6px 14px; border-radius: 6px; }}
QMenu::item:selected {{ background: {BORDER}; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 4px 8px; }}
QStatusBar {{ background: {PANEL}; color: {MUTED}; }}
QScrollBar:vertical {{ background: transparent; width: 10px; }}
QScrollBar::handle:vertical {{ background: {BORDER}; border-radius: 5px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
"""


def apply_theme(app: QApplication) -> None:
    app.setStyle("Fusion")
    pal = QPalette()
    pal.setColor(QPalette.Window, QColor(BG))
    pal.setColor(QPalette.Base, QColor(PANEL_2))
    pal.setColor(QPalette.AlternateBase, QColor(PANEL))
    pal.setColor(QPalette.Text, QColor(TEXT))
    pal.setColor(QPalette.WindowText, QColor(TEXT))
    pal.setColor(QPalette.Button, QColor(PANEL_2))
    pal.setColor(QPalette.ButtonText, QColor(TEXT))
    pal.setColor(QPalette.Highlight, QColor(ACCENT))
    pal.setColor(QPalette.HighlightedText, QColor("#04121a"))
    pal.setColor(QPalette.ToolTipBase, QColor(PANEL_2))
    pal.setColor(QPalette.ToolTipText, QColor(TEXT))
    app.setPalette(pal)
    app.setStyleSheet(build_qss(_icon_paths()))


def make_app_icon(size: int = 256) -> QIcon:
    return QIcon(make_icon_pixmap(size))


def make_icon_pixmap(size: int = 256) -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    c = QPointF(size / 2, size / 2)

    glow = QRadialGradient(c, size / 2)
    glow.setColorAt(0.55, QColor(34, 228, 255, 110))
    glow.setColorAt(1.0, QColor(255, 61, 203, 0))
    p.setBrush(glow)
    p.setPen(Qt.NoPen)
    p.drawEllipse(c, size / 2, size / 2)

    ring = QConicalGradient(c, 90)
    for i, col in enumerate(("#ff3dcb", "#7b5cff", "#22e4ff", "#3ddc84", "#ffb020", "#ff3dcb")):
        ring.setColorAt(i / 5, QColor(col))
    p.setBrush(ring)
    p.drawEllipse(c, size * 0.36, size * 0.36)
    p.setBrush(QColor(BG))
    p.drawEllipse(c, size * 0.24, size * 0.24)
    p.setBrush(QColor(TEXT))
    p.drawEllipse(c, size * 0.09, size * 0.09)
    p.end()
    return pm
