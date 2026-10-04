"""Small color helpers. Colors are (r, g, b) floats in 0..255."""

from __future__ import annotations

import colorsys

RGB = tuple[float, float, float]


def clamp_rgb(c) -> RGB:
    return tuple(max(0.0, min(255.0, float(v))) for v in c)  # type: ignore[return-value]


def lerp_rgb(a, b, t: float) -> RGB:
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t)


def scale_rgb(c, k: float) -> RGB:
    return (c[0] * k, c[1] * k, c[2] * k)


def hsv_to_rgb(h: float, s: float, v: float) -> RGB:
    r, g, b = colorsys.hsv_to_rgb(h % 1.0, max(0.0, min(1.0, s)), max(0.0, min(1.0, v)))
    return (r * 255.0, g * 255.0, b * 255.0)


def rgb_to_hsv(c) -> tuple[float, float, float]:
    return colorsys.rgb_to_hsv(c[0] / 255.0, c[1] / 255.0, c[2] / 255.0)


def boost_saturation(c, factor: float) -> RGB:
    h, s, v = rgb_to_hsv(c)
    return hsv_to_rgb(h, s * factor, v)


def to_hex(c) -> str:
    r, g, b = (int(round(v)) for v in clamp_rgb(c))
    return f"#{r:02X}{g:02X}{b:02X}"


def from_hex(text: str) -> RGB | None:
    text = text.strip().lstrip("#")
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    if len(text) != 6:
        return None
    try:
        return (float(int(text[0:2], 16)), float(int(text[2:4], 16)), float(int(text[4:6], 16)))
    except ValueError:
        return None
