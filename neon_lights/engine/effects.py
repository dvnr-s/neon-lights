"""PC-rendered lighting effects.

An effect is a pure-ish function of time: render(t, period, colors) -> RGB.
`period` is the length of one cycle in seconds (derived from the speed
slider); `colors` is the user palette. Add a new effect by subclassing Effect
and registering it in EFFECTS.
"""

from __future__ import annotations

import math
import random

from .color import RGB, hsv_to_rgb, lerp_rgb, scale_rgb


def speed_to_period(speed: float, slowest: float = 20.0, fastest: float = 0.4) -> float:
    """Map a 0-100 speed slider to a cycle length (exponential feels linear)."""
    s = max(0.0, min(100.0, speed)) / 100.0
    return slowest * (fastest / slowest) ** s


def _smoothstep(x: float) -> float:
    return x * x * (3.0 - 2.0 * x)


class Effect:
    name = "Effect"
    palette_size = 1  # how many palette colors the effect uses (0 = none)

    def reset(self) -> None:
        pass

    def render(self, t: float, period: float, colors: list[RGB]) -> RGB:
        raise NotImplementedError


class Static(Effect):
    name = "Static"

    def render(self, t, period, colors):
        return colors[0]


class Fade(Effect):
    name = "Fade"
    palette_size = 3

    def render(self, t, period, colors):
        n = len(colors)
        pos = (t / period) * n
        i = int(pos) % n
        return lerp_rgb(colors[i], colors[(i + 1) % n], _smoothstep(pos - math.floor(pos)))


class Pulse(Effect):
    name = "Pulse"

    def render(self, t, period, colors):
        phase = (1.0 - math.cos(2.0 * math.pi * t / period)) / 2.0
        return scale_rgb(colors[0], 0.03 + 0.97 * phase**1.6)


class Rainbow(Effect):
    name = "Rainbow"
    palette_size = 0

    def render(self, t, period, colors):
        return hsv_to_rgb(t / period, 1.0, 1.0)


class ColorJump(Effect):
    name = "Color Jump"
    palette_size = 3

    def render(self, t, period, colors):
        step = period / len(colors)
        return colors[int(t / step) % len(colors)]


class Strobe(Effect):
    name = "Strobe"
    palette_size = 3

    def render(self, t, period, colors):
        flash = period / 4.0
        n = int(t / flash)
        on = (t / flash) - n < 0.35
        return colors[n % len(colors)] if on else (0.0, 0.0, 0.0)


class Candle(Effect):
    name = "Candle"

    def __init__(self) -> None:
        self._level = 0.8
        self._target = 0.8
        self._next = 0.0

    def reset(self) -> None:
        self._next = 0.0

    def render(self, t, period, colors):
        if t >= self._next:
            self._target = random.uniform(0.35, 1.0)
            self._next = t + random.uniform(0.03, 0.12) * (period / 4.0)
        self._level += (self._target - self._level) * 0.35
        return scale_rgb(colors[0], self._level)


EFFECTS: dict[str, type[Effect]] = {
    cls.name: cls for cls in (Static, Fade, Pulse, Rainbow, ColorJump, Strobe, Candle)
}
