"""Screen color sampler (ambilight-style) on a worker thread.

Capture backends, fastest first:
  * DXGI desktop duplication (dxcam) - GPU-side, ~60 fps at 1440p for a few % CPU,
    zero-copy frame views, and frames only arrive when the screen changes.
  * mss (GDI BitBlt) - universal fallback, ~18 fps at 1440p.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

import numpy as np

from .color import RGB

log = logging.getLogger("neon.screen")

SAMPLE_MODES = {"vivid": "Vivid (favor saturated colors)", "average": "Average"}
REGIONS = {"full": "Full screen", "center": "Center", "edges": "Edges (ambilight)"}

_TARGET_SAMPLES_ACROSS = 160  # subsample to roughly this many pixels wide
KEEP_WARM_SECONDS = 120.0     # keep the capture open this long after leaving Screen mode


@dataclass
class ScreenSettings:
    monitor: int = 1          # 1..n (0 = all monitors combined, mss only)
    rate_hz: float = 30.0
    smoothing: float = 0.5    # 0 (instant) .. 0.95 (very smooth)
    sensitivity: float = 0.5  # 0..1, how much color/brightness is boosted
    mode: str = "vivid"
    region: str = "full"


# ---------------------------------------------------------------- capture backends
class _DxgiCapture:
    name = "DXGI"

    def __init__(self, monitor: int) -> None:
        import dxcam  # optional dependency

        self._cam = dxcam.create(output_idx=max(0, monitor - 1), output_color="BGRA",
                                 processor_backend="numpy")
        if self._cam is None:
            raise RuntimeError("dxcam could not open the monitor")
        self.monitor = monitor

    def grab(self, region: str) -> np.ndarray | None:
        """BGRA view of the region, or None when the screen hasn't changed."""
        cam = self._cam
        box = None
        if region == "center":
            w, h = cam.width, cam.height
            box = (w // 4, h // 4, w - w // 4, h - h // 4)
        return cam.grab(region=box, copy=False) if box else cam.grab(copy=False)

    def close(self) -> None:
        try:
            self._cam.release()
        except Exception:
            pass
        self._cam = None


class _MssCapture:
    name = "GDI"

    def __init__(self, monitor: int) -> None:
        import mss

        factory = getattr(mss, "MSS", None) or mss.mss  # mss >= 10 renamed the factory
        self._sct = factory()
        self.monitor = monitor

    def grab(self, region: str) -> np.ndarray:
        monitors = self._sct.monitors
        mon = monitors[self.monitor] if 0 <= self.monitor < len(monitors) else monitors[1]
        box = dict(mon)
        if region == "center":
            box = {"left": mon["left"] + mon["width"] // 4, "top": mon["top"] + mon["height"] // 4,
                   "width": mon["width"] // 2, "height": mon["height"] // 2}
        shot = self._sct.grab(box)
        return np.frombuffer(shot.bgra, dtype=np.uint8).reshape(shot.height, shot.width, 4)

    def close(self) -> None:
        try:
            self._sct.close()
        except Exception:
            pass


def _open_capture(monitor: int, prefer_dxgi: bool):
    if prefer_dxgi and monitor > 0:
        try:
            return _DxgiCapture(monitor)
        except Exception as exc:
            log.info("DXGI capture unavailable (%s) - using GDI", exc)
    return _MssCapture(monitor)


# ---------------------------------------------------------------- sampler
class ScreenSampler:
    def __init__(self) -> None:
        self.settings = ScreenSettings()
        self.prefer_dxgi = True
        self._lock = threading.Lock()
        self._latest = np.zeros(3, dtype=np.float32)
        self._thread: threading.Thread | None = None
        self._shutdown = threading.Event()
        self._restart_capture = threading.Event()
        self._active = False
        self._warm_until = 0.0
        self._frames_total = 0  # sampling iterations, for diagnostics
        self._lifecycle = threading.Lock()
        self.fps = 0.0
        self.backend = ""
        self.error: str | None = None
        self.on_error = None  # Callable[[str], None]

    # ---------------------------------------------------------------- control
    # One long-lived worker owns the capture. Leaving Screen mode only pauses sampling, so
    # switching back is instant; the capture is released after KEEP_WARM_SECONDS unused.
    @property
    def running(self) -> bool:
        return self._active and self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        self.error = None
        self._active = True
        self._ensure_worker()

    def stop(self) -> None:
        """Pause sampling (non-blocking). The capture stays open for a quick return."""
        self._active = False
        self._warm_until = time.perf_counter() + KEEP_WARM_SECONDS
        self.fps = 0.0

    def warm_up(self) -> None:
        """Open the capture in the background without sampling (app start)."""
        self._warm_until = max(self._warm_until, time.perf_counter() + KEEP_WARM_SECONDS)
        self._ensure_worker()

    def reopen(self) -> None:
        """Re-create the capture (backend preference changed)."""
        self._restart_capture.set()

    def shutdown(self) -> None:
        self._active = False
        self._shutdown.set()
        if self._thread is not None:
            self._thread.join(1.5)
        self._thread = None
        self.fps = 0.0

    def _ensure_worker(self) -> None:
        with self._lifecycle:
            if self._thread is not None and self._thread.is_alive():
                return
            self._shutdown = threading.Event()
            self._thread = threading.Thread(target=self._run, args=(self._shutdown,),
                                            name="screen-sampler", daemon=True)
            self._thread.start()

    def _idle_expired(self) -> bool:
        return not self._active and time.perf_counter() > self._warm_until

    def update(self, **kwargs) -> None:
        monitor_changed = "monitor" in kwargs and kwargs["monitor"] != self.settings.monitor
        for key, value in kwargs.items():
            setattr(self.settings, key, value)
        if monitor_changed:
            self._restart_capture.set()

    @property
    def latest(self) -> RGB:
        with self._lock:
            return tuple(float(v) for v in self._latest)  # type: ignore[return-value]

    @staticmethod
    def list_monitors() -> list[tuple[int, str]]:
        try:
            import mss

            factory = getattr(mss, "MSS", None) or mss.mss
            with factory() as sct:
                return [
                    (i, f"Monitor {i}  ({m['width']}x{m['height']})")
                    for i, m in enumerate(sct.monitors)
                    if i > 0
                ] + [(0, "All monitors")]
        except Exception as exc:
            log.warning("Could not list monitors: %s", exc)
            return [(1, "Monitor 1")]

    # ---------------------------------------------------------------- worker
    def _run(self, shutdown: threading.Event) -> None:
        capture = None
        target: np.ndarray | None = None
        frames, fps_t0 = 0, time.perf_counter()
        failures = 0
        try:
            while not shutdown.is_set():
                s = self.settings
                if capture is None or self._restart_capture.is_set():
                    self._restart_capture.clear()
                    if capture is not None:
                        capture.close()
                    capture = _open_capture(s.monitor, self.prefer_dxgi and failures < 3)
                    self.backend = capture.name
                    log.info("Screen capture: %s, monitor %s", capture.name, s.monitor)

                if not self._active:
                    if self._idle_expired():
                        break  # unused long enough: release the capture
                    shutdown.wait(0.05)  # paused: capture stays open, nothing grabbed
                    frames, fps_t0 = 0, time.perf_counter()
                    continue

                t0 = time.perf_counter()
                try:
                    img = capture.grab(s.region)
                    failures = 0
                except Exception as exc:
                    # DXGI loses access on mode switches, UAC prompts, lock screen... reopen.
                    failures += 1
                    log.info("Capture error (%s) - reopening", exc)
                    capture.close()
                    capture = None
                    shutdown.wait(0.5)
                    continue

                if img is not None:  # None = screen unchanged since last frame
                    target = self._color_of(img, s)
                    frames += 1
                self._frames_total += 1
                if target is not None:
                    alpha = 1.0 - max(0.0, min(0.95, s.smoothing))
                    with self._lock:
                        self._latest += (target - self._latest) * alpha

                now = time.perf_counter()
                if now - fps_t0 >= 1.0:
                    self.fps = frames / (now - fps_t0)
                    frames, fps_t0 = 0, now
                wait = 1.0 / max(1.0, s.rate_hz) - (now - t0)
                if wait > 0:
                    shutdown.wait(wait)
        except ImportError:
            self._fail("Screen capture needs the 'mss' package (pip install mss)")
        except Exception as exc:
            self._fail(f"Screen capture failed: {exc}")
        finally:
            if capture is not None:
                capture.close()
            log.info("Screen capture closed")

    def _fail(self, message: str) -> None:
        self.error = message
        log.error(message)
        if self.on_error:
            self.on_error(message)

    @staticmethod
    def _color_of(img: np.ndarray, s: ScreenSettings) -> np.ndarray:
        step = max(1, img.shape[1] // _TARGET_SAMPLES_ACROSS)
        px = img[::step, ::step, 2::-1]  # BGRA -> RGB, subsampled (a small copy of a view)
        if s.region == "edges":
            h, w = px.shape[:2]
            bh, bw = max(1, h // 7), max(1, w // 7)
            mask = np.zeros((h, w), dtype=bool)
            mask[:bh, :] = mask[-bh:, :] = True
            mask[:, :bw] = mask[:, -bw:] = True
            px = px[mask]
        px = px.reshape(-1, 3).astype(np.float32) * (1.0 / 255.0)
        return ScreenSampler._reduce(px, s.mode, s.sensitivity) * 255.0

    @staticmethod
    def _reduce(px: np.ndarray, mode: str, sensitivity: float) -> np.ndarray:
        sens = max(0.0, min(1.0, sensitivity))
        mx = px.max(axis=1)
        brightness = float(mx.mean())

        if mode == "vivid":
            # Hue comes from the most colorful pixels; brightness from the whole image,
            # so a small red icon on a black screen doesn't light the room red.
            sat = (mx - px.min(axis=1)) / (mx + 1e-6)
            weights = mx * (0.05 + sat) ** (1.0 + 5.0 * sens)
            color = (px * weights[:, None]).sum(axis=0) / (weights.sum() + 1e-6)
        else:
            color = px.mean(axis=0)

        peak = float(color.max())
        if peak < 1e-4:
            return np.zeros(3, dtype=np.float32)
        direction = color / peak
        # Saturation boost: push the weakest channels down.
        direction = np.clip(1.0 - (1.0 - direction) * (1.0 + 1.5 * sens), 0.0, 1.0)
        value = min(1.0, brightness * (1.0 + 1.5 * sens))
        return (direction * value).astype(np.float32)
