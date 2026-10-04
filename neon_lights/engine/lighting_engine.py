"""Lighting engine: owns the light state and decides what color goes to the strip.

    GUI -> LightingEngine -> (Effect | ScreenSampler | AudioAnalyzer+MusicVisualizer) -> BleController

It ticks on its own thread, renders the active mode, applies global
brightness/power, and sends only changed colors. It knows nothing about Qt,
and nothing about bytes on the wire - that's the BLE layer's job.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable

from ..ble import BleController, ConnectionState, ConnectionStatus
from .audio import AudioAnalyzer
from .color import RGB, clamp_rgb, scale_rgb
from .effects import EFFECTS, Effect, speed_to_period
from .music import MusicVisualizer
from .screen import ScreenSampler

log = logging.getLogger("neon.engine")

BRIGHTNESS_GAMMA = 1.6  # perceptual curve for the brightness slider


@dataclass(frozen=True)
class Calibration:
    """Per-strip color correction applied to every frame, after brightness."""

    red: float = 1.0     # channel gains 0..1 (white balance)
    green: float = 1.0
    blue: float = 1.0
    gamma: float = 1.0   # >1 deepens mid-tones, giving richer colors on linear LEDs

    def apply(self, c: RGB) -> RGB:
        g = self.gamma
        if g == 1.0:
            return (c[0] * self.red, c[1] * self.green, c[2] * self.blue)
        return (255.0 * self.red * (c[0] / 255.0) ** g,
                255.0 * self.green * (c[1] / 255.0) ** g,
                255.0 * self.blue * (c[2] / 255.0) ** g)


class Mode(str, Enum):
    MANUAL = "manual"
    EFFECT = "effect"
    DEVICE_EFFECT = "device_effect"
    SCREEN = "screen"
    MUSIC = "music"


class LightingEngine:
    def __init__(self, ble: BleController, tick_hz: float = 60.0) -> None:
        self.ble = ble
        self.screen = ScreenSampler()
        self.audio = AudioAnalyzer()
        self.music = MusicVisualizer()
        self.tick_hz = tick_hz
        self.on_error: Callable[[str], None] | None = None
        self.screen.on_error = self._report_error
        self.audio.on_error = self._report_error

        self._lock = threading.RLock()
        self._mode = Mode.MANUAL
        self._power = True
        self._brightness = 100.0
        self._manual: RGB = (255.0, 0.0, 0.0)
        self._effect: Effect = EFFECTS["Rainbow"]()
        self._effect_speed = 50.0
        self._effect_colors: list[RGB] = [(255.0, 0.0, 0.0), (0.0, 255.0, 0.0), (0.0, 0.0, 255.0)]
        self._effect_t0 = time.perf_counter()
        self._device_effect = 0x8A
        self._device_speed = 50.0
        self._calibration = Calibration()
        self._test_color: RGB | None = None

        self._output: RGB = (0.0, 0.0, 0.0)
        self._last_sent: tuple[int, int, int] | None = None
        self._force_send = True
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

        ble.add_status_listener(self._on_ble_status)

    # ---------------------------------------------------------------- lifecycle
    def start(self) -> None:
        if self._thread is None:
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="lighting-engine", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(1.0)
        self._thread = None
        self.screen.shutdown()
        self.audio.shutdown()

    # ---------------------------------------------------------------- state (thread-safe)
    @property
    def mode(self) -> Mode:
        return self._mode

    @property
    def power(self) -> bool:
        return self._power

    @property
    def brightness(self) -> float:
        return self._brightness

    @property
    def output_color(self) -> RGB:
        """Color currently shown (after brightness), for UI previews."""
        return self._output if self._power else (0.0, 0.0, 0.0)

    def set_mode(self, mode: Mode | str) -> None:
        mode = Mode(mode)
        with self._lock:
            previous = self._mode
            if mode == previous:
                return
            self._mode = mode
            self._force_send = True

        if previous == Mode.SCREEN:
            self.screen.stop()
        if previous == Mode.MUSIC:
            self.audio.stop()
        if previous == Mode.DEVICE_EFFECT:
            self.ble.send_brightness(100)  # hand brightness back to software scaling

        if mode == Mode.SCREEN:
            self.screen.start()
        elif mode == Mode.MUSIC:
            self.audio.start()
        elif mode == Mode.DEVICE_EFFECT:
            self._push_device_effect()
        elif mode == Mode.EFFECT:
            self._restart_effect_clock()
        log.info("Mode: %s", mode.value)

    def set_power(self, on: bool) -> None:
        with self._lock:
            self._power = bool(on)
            self._force_send = True
        self.ble.send_power(on)
        if on and self._mode == Mode.DEVICE_EFFECT:
            self._push_device_effect()

    def toggle_power(self) -> bool:
        self.set_power(not self._power)
        return self._power

    def set_brightness(self, percent: float) -> None:
        with self._lock:
            self._brightness = max(0.0, min(100.0, float(percent)))
        if self._mode == Mode.DEVICE_EFFECT:
            self.ble.send_brightness(self._brightness)

    def set_manual_color(self, rgb: RGB) -> None:
        with self._lock:
            self._manual = clamp_rgb(rgb)

    def set_effect(self, name: str) -> None:
        cls = EFFECTS.get(name)
        if cls is None:
            return
        with self._lock:
            if not isinstance(self._effect, cls):
                self._effect = cls()
            self._restart_effect_clock()

    def set_effect_speed(self, speed: float) -> None:
        with self._lock:
            # Keep the animation phase continuous when the speed changes.
            now = time.perf_counter()
            old_period = speed_to_period(self._effect_speed)
            phase = (now - self._effect_t0) / old_period
            self._effect_speed = max(0.0, min(100.0, float(speed)))
            self._effect_t0 = now - phase * speed_to_period(self._effect_speed)

    def set_effect_colors(self, colors: list[RGB]) -> None:
        if colors:
            with self._lock:
                self._effect_colors = [clamp_rgb(c) for c in colors]

    def set_device_effect(self, code: int, speed: float) -> None:
        with self._lock:
            self._device_effect = int(code)
            self._device_speed = float(speed)
        if self._mode == Mode.DEVICE_EFFECT:
            self._push_device_effect()

    def configure_screen(self, **settings) -> None:
        self.screen.update(**settings)

    def configure_music(self, mode: str | None = None, color: RGB | None = None,
                        sensitivity: float | None = None, smoothing: float | None = None) -> None:
        if mode is not None:
            self.music.mode = mode
        if color is not None:
            self.music.color = clamp_rgb(color)
        if sensitivity is not None:
            self.audio.sensitivity = sensitivity
        if smoothing is not None:
            self.audio.smoothing = smoothing

    def set_calibration(self, calibration: Calibration) -> None:
        with self._lock:
            self._calibration = calibration

    def set_test_color(self, rgb: RGB | None) -> None:
        """Override the output with a fixed color (calibration test patterns). None = off."""
        with self._lock:
            self._test_color = None if rgb is None else clamp_rgb(rgb)
            self._force_send = True
        if rgb is None and self._mode == Mode.DEVICE_EFFECT:
            self._push_device_effect()  # the test pattern interrupted the on-strip animation

    def set_audio_device(self, index: int | None) -> None:
        self.audio.device_index = index
        if self._mode == Mode.MUSIC:
            self.audio.restart()

    # ---------------------------------------------------------------- internals
    def _restart_effect_clock(self) -> None:
        self._effect_t0 = time.perf_counter()
        self._effect.reset()

    def _push_device_effect(self) -> None:
        self.ble.send_device_effect(self._device_effect, self._device_speed)
        self.ble.send_brightness(self._brightness)

    def _report_error(self, message: str) -> None:
        if self.on_error:
            self.on_error(message)

    def _on_ble_status(self, status: ConnectionStatus) -> None:
        # Runs on the BLE thread. On every (re)connect, re-apply our full state.
        if status.state != ConnectionState.CONNECTED:
            return
        with self._lock:
            self._force_send = True
            power, mode = self._power, self._mode
        self.ble.send_power(power)
        if mode == Mode.DEVICE_EFFECT:
            self._push_device_effect()
        else:
            self.ble.send_brightness(100)

    def _render(self, now: float, dt: float) -> RGB:
        mode = self._mode
        if mode == Mode.MANUAL:
            return self._manual
        if mode == Mode.EFFECT:
            colors = self._effect_colors
            needed = max(1, self._effect.palette_size)
            palette = (colors * needed)[:max(needed, len(colors))] if colors else [(255.0, 255.0, 255.0)]
            return self._effect.render(now - self._effect_t0, speed_to_period(self._effect_speed), palette)
        if mode == Mode.SCREEN:
            return self.screen.latest
        if mode == Mode.MUSIC:
            return self.music.render(self.audio.features, dt)
        return (0.0, 0.0, 0.0)

    def _run(self) -> None:
        period = 1.0 / self.tick_hz
        last = next_tick = time.perf_counter()
        failures = 0
        while not self._stop.is_set():
            t0 = time.perf_counter()
            dt, last = t0 - last, t0
            try:
                self._tick(t0, dt)
                failures = 0
            except Exception:
                failures += 1
                if failures <= 3 or failures % 500 == 0:  # don't flood the log at 50 Hz
                    log.exception("Engine tick failed")
            # Deadline scheduling keeps the average rate right despite coarse Windows timers.
            next_tick = max(next_tick + period, time.perf_counter() - period)
            self._stop.wait(max(0.0005, next_tick - time.perf_counter()))

    def _tick(self, now: float, dt: float) -> None:
        with self._lock:
            color = clamp_rgb(self._render(now, dt))
            if self._test_color is not None:
                color = self._test_color
            scaled = scale_rgb(color, (self._brightness / 100.0) ** BRIGHTNESS_GAMMA)
            scaled = clamp_rgb(self._calibration.apply(scaled))
            self._output = scaled
            if not self._power or (self._mode == Mode.DEVICE_EFFECT and self._test_color is None):
                return
            frame = tuple(int(round(c)) for c in scaled)
            if frame == self._last_sent and not self._force_send:
                return
            self._last_sent = frame
            self._force_send = False
        self.ble.send_color(*frame)
