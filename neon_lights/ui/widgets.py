"""Reusable widgets: color wheel, swatches, labeled sliders, cards."""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QConicalGradient, QPainter, QPen, QRadialGradient
from PySide6.QtWidgets import (
    QAbstractButton,
    QColorDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from . import theme


def qcolor(rgb) -> QColor:
    r, g, b = (max(0, min(255, int(round(v)))) for v in rgb)
    return QColor(r, g, b)


def rgb_of(c: QColor) -> tuple[float, float, float]:
    return (float(c.red()), float(c.green()), float(c.blue()))


def card(*, spacing: int = 10, margins: int = 14) -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName("card")
    lay = QVBoxLayout(frame)
    lay.setContentsMargins(margins, margins, margins, margins)
    lay.setSpacing(spacing)
    return frame, lay


def label(text: str, kind: str | None = None) -> QLabel:
    lab = QLabel(text)
    if kind:
        lab.setObjectName(kind)
    return lab


class ColorWheel(QWidget):
    """Hue around the circle, saturation along the radius (value is always full)."""

    colorPicked = Signal(QColor)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._hue = 0.0
        self._sat = 1.0
        self.setMinimumSize(180, 180)
        self.setMaximumSize(420, 420)
        self.setCursor(Qt.CrossCursor)

    def sizeHint(self) -> QSize:
        return QSize(260, 260)

    def setColor(self, c: QColor) -> None:
        h, s, _v, _a = c.getHsvF()
        if s > 0.001 and h >= 0:
            self._hue = h
        self._sat = s
        self.update()

    def _geometry(self) -> tuple[QPointF, float]:
        side = min(self.width(), self.height())
        return QPointF(self.width() / 2, self.height() / 2), side / 2 - 13  # room for the marker ring

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        center, radius = self._geometry()

        hue = QConicalGradient(center, 0)
        for i in range(13):
            hue.setColorAt(i / 12, QColor.fromHsvF((i / 12) % 1.0, 1.0, 1.0))
        p.setPen(Qt.NoPen)
        p.setBrush(hue)
        p.drawEllipse(center, radius, radius)

        white = QRadialGradient(center, radius)
        white.setColorAt(0.0, QColor(255, 255, 255, 255))
        white.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.setBrush(white)
        p.drawEllipse(center, radius, radius)

        angle = self._hue * 2 * math.pi
        marker = QPointF(center.x() + math.cos(angle) * self._sat * radius,
                         center.y() - math.sin(angle) * self._sat * radius)
        p.setBrush(QColor.fromHsvF(self._hue, self._sat, 1.0))
        p.setPen(QPen(QColor(theme.BG), 3))
        p.drawEllipse(marker, 9, 9)
        p.setPen(QPen(QColor("white"), 2))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(marker, 11, 11)

    def _pick(self, pos: QPointF) -> None:
        center, radius = self._geometry()
        dx, dy = pos.x() - center.x(), center.y() - pos.y()
        self._hue = (math.atan2(dy, dx) / (2 * math.pi)) % 1.0
        self._sat = min(1.0, math.hypot(dx, dy) / radius)
        self.update()
        self.colorPicked.emit(QColor.fromHsvF(self._hue, self._sat, 1.0))

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.LeftButton:
            self._pick(e.position())

    def mouseMoveEvent(self, e) -> None:
        if e.buttons() & Qt.LeftButton:
            self._pick(e.position())


class ColorSwatch(QAbstractButton):
    """A perfectly round color button, painted (not styled) so it stays round at any DPI.

    With `pickable`, clicking opens a color dialog. `setSelected(True)` draws an accent ring.
    """

    colorChanged = Signal(QColor)

    def __init__(self, color=(255, 255, 255), size: int = 34, pickable: bool = False,
                 tooltip: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._color = qcolor(color) if not isinstance(color, QColor) else QColor(color)
        self._selected = False
        self.setFixedSize(size, size)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(tooltip)
        self.setFocusPolicy(Qt.NoFocus)
        if pickable:
            self.clicked.connect(self._open_dialog)

    def color(self) -> QColor:
        return QColor(self._color)

    def setColor(self, c) -> None:
        self._color = qcolor(c) if not isinstance(c, QColor) else QColor(c)
        self.update()

    def setSelected(self, selected: bool) -> None:
        if selected != self._selected:
            self._selected = selected
            self.update()

    def sizeHint(self) -> QSize:
        return self.size()

    def enterEvent(self, e) -> None:
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:
        self.update()
        super().leaveEvent(e)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        side = min(self.width(), self.height())
        outer = QRectF((self.width() - side) / 2, (self.height() - side) / 2, side, side)
        hover = self.underMouse() and self.isEnabled()
        if self._selected or hover:
            ring = QColor(theme.ACCENT if self._selected else theme.TEXT)
            p.setPen(QPen(ring, 2.0))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(outer.adjusted(1, 1, -1, -1))
            inset = 4.5
        else:
            inset = 1.5
        fill = outer.adjusted(inset, inset, -inset, -inset)
        if self._color.lightness() < 60:  # faint outline so dark colors don't vanish
            p.setPen(QPen(QColor(255, 255, 255, 40), 1.0))
        else:
            p.setPen(Qt.NoPen)
        p.setBrush(self._color)
        p.drawEllipse(fill)
        if self.isDown():
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 50))
            p.drawEllipse(fill)

    def _open_dialog(self) -> None:
        c = QColorDialog.getColor(self._color, self, "Pick a color")
        if c.isValid():
            self.setColor(c)
            self.colorChanged.emit(c)


class PowerButton(QAbstractButton):
    """Round, checkable power toggle with a drawn power symbol."""

    def __init__(self, size: int = 44, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setFixedSize(size, size)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)

    def sizeHint(self) -> QSize:
        return self.size()

    def enterEvent(self, e) -> None:
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:
        self.update()
        super().leaveEvent(e)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        side = min(self.width(), self.height())
        r = QRectF((self.width() - side) / 2 + 1, (self.height() - side) / 2 + 1, side - 2, side - 2)
        on = self.isChecked()
        hover = self.underMouse()
        if on:
            glow = QColor(theme.ACCENT)
            glow.setAlpha(70 if hover else 45)
            p.setPen(QPen(glow, 3))
            p.setBrush(QColor("#6ff0ff" if hover else theme.ACCENT))
            p.drawEllipse(r.adjusted(1.5, 1.5, -1.5, -1.5))
            ink = QColor("#04121a")
        else:
            p.setPen(QPen(QColor(theme.ACCENT if hover else theme.BORDER), 1.5))
            p.setBrush(QColor(theme.PANEL_2))
            p.drawEllipse(r.adjusted(1, 1, -1, -1))
            ink = QColor(theme.TEXT if hover else theme.MUTED)
        # power glyph: open ring + vertical bar
        c = r.center()
        rad = side * 0.2
        pen = QPen(ink, max(2.0, side * 0.055), Qt.SolidLine, Qt.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        # Qt angles: 0 deg = 3 o'clock, counter-clockwise. Leave a 60 deg gap centred on 12 o'clock.
        p.drawArc(QRectF(c.x() - rad, c.y() - rad + side * 0.02, 2 * rad, 2 * rad), 120 * 16, 300 * 16)
        p.drawLine(QPointF(c.x(), c.y() - rad - side * 0.03), QPointF(c.x(), c.y() - side * 0.02))


class LabeledSlider(QWidget):
    valueChanged = Signal(int)

    def __init__(self, text: str, minimum: int = 0, maximum: int = 100, value: int = 50,
                 suffix: str = "", object_name: str | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._suffix = suffix
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        self.title = QLabel(text)
        self.title.setMinimumWidth(92)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(minimum, maximum)
        self.slider.setValue(value)
        if object_name:
            self.slider.setObjectName(object_name)
        self.value_label = QLabel()
        self.value_label.setMinimumWidth(48)
        self.value_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.value_label.setObjectName("muted")
        lay.addWidget(self.title)
        lay.addWidget(self.slider, 1)
        lay.addWidget(self.value_label)
        self.slider.valueChanged.connect(self._changed)
        self._update_label(value)

    def value(self) -> int:
        return self.slider.value()

    def setValue(self, v: int, *, silent: bool = False) -> None:
        if silent:
            self.slider.blockSignals(True)
            self.slider.setValue(int(v))
            self.slider.blockSignals(False)
            self._update_label(self.slider.value())
        else:
            self.slider.setValue(int(v))

    def _changed(self, v: int) -> None:
        self._update_label(v)
        self.valueChanged.emit(v)

    def _update_label(self, v: int) -> None:
        self.value_label.setText(f"{v}{self._suffix}")


class StatusDot(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._color = QColor(theme.MUTED)
        self.setFixedSize(14, 14)

    def setColor(self, c: str) -> None:
        self._color = QColor(c)
        self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        glow = QColor(self._color)
        glow.setAlpha(70)
        p.setPen(Qt.NoPen)
        p.setBrush(glow)
        p.drawEllipse(QRectF(0, 0, 14, 14))
        p.setBrush(self._color)
        p.drawEllipse(QRectF(3, 3, 8, 8))


class GlowPreview(QWidget):
    """Large live preview of what the strip is showing."""

    def __init__(self, parent: QWidget | None = None, background: str = theme.PANEL) -> None:
        super().__init__(parent)
        self._color = QColor(0, 0, 0)
        self._caption = ""
        self._background = QColor(background)
        # Paint our own backdrop so repaints don't force the styled card behind us to redraw.
        self.setAttribute(Qt.WA_OpaquePaintEvent)
        self.setMinimumSize(150, 56)

    def setColor(self, c: QColor, caption: str = "") -> None:
        # Repaint only on a visible change - reactive modes nudge colors every frame.
        old = self._color
        moved = max(abs(c.red() - old.red()), abs(c.green() - old.green()), abs(c.blue() - old.blue()))
        if moved >= 3 or caption != self._caption:
            self._color = QColor(c)
            self._caption = caption
            self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), self._background)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        glow = QColor(self._color)
        glow.setAlpha(90)
        p.setPen(QPen(glow, 4))
        p.setBrush(self._color)
        p.drawRoundedRect(r, 14, 14)
        lum = 0.299 * self._color.red() + 0.587 * self._color.green() + 0.114 * self._color.blue()
        p.setPen(QColor("#04121a") if lum > 140 else QColor(theme.TEXT))
        text = self._caption or self._color.name().upper()
        p.drawText(r, Qt.AlignCenter, text)


class LevelMeters(QWidget):
    """Labeled horizontal level bars painted directly - far cheaper than styled QProgressBars."""

    ROW = 34

    def __init__(self, bars: list[tuple[str, str, str]], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._bars = bars  # (key, label, color)
        self._values = {key: 0 for key, _l, _c in bars}
        self.setAttribute(Qt.WA_OpaquePaintEvent)
        self.setMinimumHeight(self.ROW * len(bars))

    def setLevels(self, levels: dict[str, float]) -> None:
        changed = False
        for key in self._values:
            v = int(max(0.0, min(1.0, levels.get(key, 0.0))) * 50)  # 2% steps
            if v != self._values[key]:
                self._values[key] = v
                changed = True
        if changed:
            self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(theme.PANEL))
        p.setRenderHint(QPainter.Antialiasing)
        w = self.width()
        track = QColor(theme.PANEL_2)
        muted = QColor(theme.MUTED)
        for i, (key, text, color) in enumerate(self._bars):
            y = i * self.ROW
            p.setPen(muted)
            p.drawText(QRectF(0, y, w, 16), Qt.AlignLeft | Qt.AlignVCenter, text)
            bar = QRectF(0, y + 19, w, 9)
            p.setPen(Qt.NoPen)
            p.setBrush(track)
            p.drawRoundedRect(bar, 4, 4)
            fill = self._values[key] / 50.0
            if fill > 0:
                p.setBrush(QColor(color))
                p.drawRoundedRect(QRectF(bar.x(), bar.y(), max(9.0, bar.width() * fill), bar.height()), 4, 4)


class VBox(QVBoxLayout):
    def __init__(self, parent: QWidget | None = None, spacing: int = 10, margins: int = 0) -> None:
        super().__init__(parent)
        self.setSpacing(spacing)
        self.setContentsMargins(margins, margins, margins, margins)


class HBox(QHBoxLayout):
    def __init__(self, parent: QWidget | None = None, spacing: int = 10, margins: int = 0) -> None:
        super().__init__(parent)
        self.setSpacing(spacing)
        self.setContentsMargins(margins, margins, margins, margins)
