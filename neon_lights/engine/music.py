"""Turns AudioFeatures into a color. Each style is a small render method."""

from __future__ import annotations

import math
import time

from .audio import AudioFeatures
from .color import RGB, hsv_to_rgb, rgb_to_hsv, scale_rgb

MUSIC_MODES = {
    "spectrum": "Spectrum  (bass→red, mids→green, treble→blue)",
    "bass_pulse": "Bass pulse  (your color, pumps with the bass)",
    "beat_hop": "Beat hop  (new color on every beat)",
    "bpm_sync": "BPM sync  (color changes locked to the tempo)",
    "drop_strobe": "Drop strobe  (your color, strobes when the drop hits)",
    "mood": "Mood mix  (your color, shimmering with the music)",
    "energy": "Energy rainbow  (hue flows faster when it's loud)",
}

_GOLDEN = 0.618033988749895
_DROP_SURGE = 1.6       # short/long loudness ratio that counts as a drop
_STROBE_SECONDS = 1.8
_STROBE_COOLDOWN = 6.0


class MusicVisualizer:
    def __init__(self) -> None:
        self.mode = "spectrum"
        self.color: RGB = (255.0, 0.0, 140.0)
        self._hue = 0.0
        self._flash = 0.0
        self._last_beat = 0
        self._tick_key: tuple[float, int] | None = None
        self._strobe_until = 0.0
        self._strobe_ready_at = 0.0

    def render(self, f: AudioFeatures, dt: float) -> RGB:
        new_beat = f.beat_count != self._last_beat
        self._last_beat = f.beat_count
        renderer = getattr(self, f"_render_{self.mode}", self._render_spectrum)
        return renderer(f, dt, new_beat)

    @property
    def strobing(self) -> bool:
        return time.perf_counter() < self._strobe_until

    # ---------------------------------------------------------------- styles
    def _render_spectrum(self, f: AudioFeatures, dt: float, new_beat: bool) -> RGB:
        return (f.bass * 255.0, f.mids * 255.0, f.treble * 255.0)

    def _render_bass_pulse(self, f: AudioFeatures, dt: float, new_beat: bool) -> RGB:
        return scale_rgb(self.color, 0.06 + 0.94 * f.bass)

    def _render_beat_hop(self, f: AudioFeatures, dt: float, new_beat: bool) -> RGB:
        if new_beat:
            self._hue = (self._hue + _GOLDEN) % 1.0
            self._flash = 1.0
        self._flash *= math.exp(-dt * 5.0)
        value = 0.12 + 0.88 * max(self._flash, f.bass * 0.7)
        return hsv_to_rgb(self._hue, 1.0, value)

    def _render_bpm_sync(self, f: AudioFeatures, dt: float, new_beat: bool) -> RGB:
        if f.bpm <= 0:
            return self._render_beat_hop(f, dt, new_beat)  # until the tempo locks
        period = 60.0 / f.bpm
        since = max(0.0, time.perf_counter() - f.beat_time)
        # Beat grid anchored on the last detected beat; ticks keep coming even when
        # a quiet beat isn't detected.
        tick = int(since / period)
        phase = (since / period) - tick
        key = (f.beat_time, tick)
        if key != self._tick_key:
            self._tick_key = key
            self._hue = (self._hue + _GOLDEN) % 1.0
        value = 0.15 + 0.85 * math.exp(-phase * 3.5) * (0.5 + 0.5 * max(f.volume, f.bass))
        return hsv_to_rgb(self._hue, 1.0, value)

    def _render_drop_strobe(self, f: AudioFeatures, dt: float, new_beat: bool) -> RGB:
        now = time.perf_counter()
        if f.surge > _DROP_SURGE and f.volume > 0.4 and now >= self._strobe_ready_at:
            self._strobe_until = now + _STROBE_SECONDS
            self._strobe_ready_at = now + _STROBE_COOLDOWN
        if now < self._strobe_until:
            flash = int(now * 16.0) % 2 == 0  # ~8 flashes/s, within the strip's update rate
            return (255.0, 255.0, 255.0) if flash else scale_rgb(self.color, 0.15)
        return scale_rgb(self.color, 0.1 + 0.9 * f.bass)

    def _render_mood(self, f: AudioFeatures, dt: float, new_beat: bool) -> RGB:
        h, s, _v = rgb_to_hsv(self.color)
        self._hue = (self._hue + dt * 0.4) % 1.0  # slow drift clock
        if new_beat:
            self._flash = 1.0
        self._flash *= math.exp(-dt * 4.0)
        hue = h + 0.05 * math.sin(self._hue * 2 * math.pi) + 0.07 * (f.mids - 0.3)
        sat = s * (1.0 - 0.35 * f.treble) * (1.0 - 0.3 * self._flash)  # treble/beat whiten it
        value = 0.12 + 0.68 * max(f.volume, 0.8 * f.bass) + 0.2 * self._flash
        return hsv_to_rgb(hue, sat, value)

    def _render_energy(self, f: AudioFeatures, dt: float, new_beat: bool) -> RGB:
        self._hue = (self._hue + dt * (0.02 + 0.5 * f.volume)) % 1.0
        return hsv_to_rgb(self._hue, 1.0, 0.1 + 0.9 * f.volume)
