"""System-audio analyzer: WASAPI loopback capture (PyAudioWPatch) + FFT band levels.

The capture callback only copies samples into a ring buffer; analysis runs on
its own thread at a fixed rate. WASAPI loopback delivers no data while nothing
is playing, so silence is detected by the absence of new samples.

Features produced: normalized volume/bass/mids/treble, beat onsets, a tempo
estimate (BPM) and an energy "surge" ratio used to spot drops.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from collections import deque
from dataclasses import dataclass, replace

import numpy as np

log = logging.getLogger("neon.audio")

BANDS = {"bass": (20.0, 250.0), "mids": (250.0, 4000.0), "treble": (4000.0, 16000.0)}
_FFT_SIZE = 2048
_RING = _FFT_SIZE * 4
_ANALYSIS_HZ = 60.0
_STALE_AFTER = 0.06  # s without new samples = silence (WASAPI stops sending when nothing plays)
_PEAK_TAU = 1.0      # s, how fast auto-volume recovers after a loud moment
KEEP_WARM_SECONDS = 120.0  # keep the capture stream open this long after leaving Music mode


@dataclass(frozen=True)
class AudioFeatures:
    volume: float = 0.0     # all levels normalized 0..1
    bass: float = 0.0
    mids: float = 0.0
    treble: float = 0.0
    beat_count: int = 0     # increments on every detected beat
    beat_time: float = 0.0  # time.perf_counter() of the last beat
    bpm: float = 0.0        # tempo estimate, 0 = not locked yet
    surge: float = 1.0      # short-term / long-term loudness (>1.6 ~ a drop)
    active: bool = False    # True while audio is flowing


class _TempoTracker:
    """Median inter-beat interval over the last ~10 s, folded into 70-180 BPM."""

    def __init__(self) -> None:
        self._beats: deque[float] = deque(maxlen=24)

    def add(self, t: float) -> None:
        self._beats.append(t)

    def bpm(self, now: float) -> float:
        beats = [b for b in self._beats if now - b < 10.0]
        if len(beats) < 5:
            return 0.0
        intervals = sorted(b - a for a, b in zip(beats, beats[1:]) if 0.25 < b - a < 2.0)
        if len(intervals) < 4:
            return 0.0
        median = intervals[len(intervals) // 2]
        consistent = sum(1 for i in intervals if abs(i - median) / median < 0.18 or
                         abs(i / 2 - median) / median < 0.18)
        if consistent < 0.6 * len(intervals):
            return 0.0  # too irregular to trust
        bpm = 60.0 / median
        while bpm < 70.0:
            bpm *= 2.0
        while bpm > 180.0:
            bpm /= 2.0
        return bpm


def smoothing_times(smoothing: float) -> tuple[float, float]:
    """Slider 0..1 -> (attack, release) time constants in seconds, perceptually spread."""
    sm = max(0.0, min(1.0, smoothing))
    return 0.008 + 0.07 * sm, 0.035 * (35.0 ** sm)  # release: 35 ms .. 1.2 s


class LevelProcessor:
    """Turns raw band energies into 0..1 lighting levels, beats, tempo and drop surge.

    Pure computation (no audio I/O), so it can be tested with synthetic input.
    """

    NAMES = ("volume", *BANDS)

    def __init__(self) -> None:
        self.peaks = dict.fromkeys(self.NAMES, 1e-4)
        self.levels = dict.fromkeys(self.NAMES, 0.0)
        self.bass_history: deque[float] = deque(maxlen=int(_ANALYSIS_HZ * 0.8))
        self.loud_short = 0.0
        self.loud_long = 0.0
        self.heard_seconds = 0.0  # audio heard since start; drops need a baseline first
        self.tempo = _TempoTracker()
        self.beat_count = 0
        self.last_beat = 0.0

    def update(self, raw: dict[str, float], active: bool, dt: float, now: float,
               sensitivity: float, smoothing: float) -> AudioFeatures:
        sens = max(0.0, min(1.0, sensitivity))
        attack_tau, release_tau = smoothing_times(smoothing)
        attack = 1.0 - math.exp(-dt / attack_tau)
        release = 1.0 - math.exp(-dt / release_tau)
        peak_decay = math.exp(-dt / _PEAK_TAU)
        gain = 0.5 * 4.0 ** sens  # 0.5x .. 2x, 1x at 50 %

        for name in self.NAMES:
            # Auto-volume: follow the loudest recent level so quiet and loud songs both
            # use the full range; recovers within ~1 s after a loud moment.
            self.peaks[name] = max(raw[name], self.peaks[name] * peak_decay, 1e-4)
        loudest = max(self.peaks[b] for b in BANDS)
        for name in self.NAMES:
            # A near-silent band may not be amplified more than ~7x the loudest one,
            # otherwise spectral leakage lights it up.
            ref = self.peaks[name] if name == "volume" else max(self.peaks[name], 0.15 * loudest)
            x = raw[name] / ref
            x = max(0.0, (x - 0.12) / 0.88)  # small contrast floor so pulses read clearly
            target = min(1.0, x ** 1.2 * gain)
            coeff = attack if target > self.levels[name] else release
            self.levels[name] += (target - self.levels[name]) * coeff

        # Beat: a rising bass onset well above its recent average.
        bass = raw["bass"]
        history = self.bass_history
        avg = (sum(history) / len(history)) if history else 0.0
        recent = list(history)[-4:]
        rising = not recent or bass > 1.15 * min(recent)
        if (active and rising and bass > avg * (1.55 - 0.4 * sens) and bass > 0.3 * self.peaks["bass"]
                and now - self.last_beat > 0.18):
            self.beat_count += 1
            self.last_beat = now
            self.tempo.add(now)
        history.append(bass)

        # Surge: ~0.3 s loudness vs ~5 s loudness. Only meaningful once a baseline exists.
        loud = raw["volume"]
        if loud > 0:
            self.heard_seconds += dt
            if self.loud_long <= 0:
                self.loud_long = loud  # seed the baseline instead of starting from zero
        self.loud_short += (loud - self.loud_short) * (1.0 - math.exp(-dt / 0.3))
        self.loud_long += (loud - self.loud_long) * (1.0 - math.exp(-dt / 5.0))
        surge = self.loud_short / self.loud_long if self.heard_seconds > 3.0 and self.loud_long > 1e-5 else 1.0

        return AudioFeatures(
            volume=self.levels["volume"],
            bass=self.levels["bass"],
            mids=self.levels["mids"],
            treble=self.levels["treble"],
            beat_count=self.beat_count,
            beat_time=self.last_beat,
            bpm=self.tempo.bpm(now),
            surge=surge,
            active=active,
        )


class AudioAnalyzer:
    def __init__(self) -> None:
        self.sensitivity = 0.5   # 0..1
        self.smoothing = 0.4     # 0..1
        self.device_index: int | None = None  # None = default output's loopback
        self.output_delay = 0.0  # s; delay the lights to match Bluetooth headphones
        self.on_error = None     # Callable[[str], None]
        self.error: str | None = None
        self.device_name = ""

        self._features = AudioFeatures()
        self._history: deque[tuple[float, AudioFeatures]] = deque(maxlen=int(_ANALYSIS_HZ * 1.2))
        self._thread: threading.Thread | None = None
        self._shutdown = threading.Event()
        self._reopen = threading.Event()
        self._analyzing = False
        self._warm_until = 0.0
        self._lifecycle = threading.Lock()
        self._ring = np.zeros(_RING, dtype=np.float32)
        self._ring_pos = 0
        self._buf_lock = threading.Lock()
        self._last_data = 0.0
        self._rate = 48000
        self._channels = 2

    # ---------------------------------------------------------------- control
    @property
    def running(self) -> bool:
        """True while Music mode is analyzing (the stream may stay open, paused, after that)."""
        return self._analyzing and self._thread is not None and self._thread.is_alive()

    @property
    def features(self) -> AudioFeatures:
        delay = self.output_delay
        if delay <= 0:
            return self._features
        # Bluetooth headphones play ~150-300 ms after the PC renders audio; replay the
        # analysis that much later so the lights match what you hear.
        cutoff = time.perf_counter() - delay
        for t, features in reversed(self._history):
            if t <= cutoff:
                return replace(features, beat_time=features.beat_time + delay)
        return AudioFeatures()

    # One long-lived worker owns the audio device. Leaving Music mode only pauses analysis,
    # so switching back is instant; the device is released after KEEP_WARM_SECONDS unused.
    def start(self) -> None:
        self.error = None
        if not self._analyzing:
            with self._buf_lock:  # drop audio from before the pause
                self._ring[:] = 0.0
            self._last_data = 0.0
        self._analyzing = True
        self._ensure_worker()

    def stop(self) -> None:
        """Pause analysis (non-blocking). The stream stays open for a quick return."""
        self._analyzing = False
        self._warm_until = time.perf_counter() + KEEP_WARM_SECONDS
        self._features = AudioFeatures()
        self._history.clear()

    def warm_up(self) -> None:
        """Open the capture stream in the background without analyzing (app start)."""
        self._warm_until = max(self._warm_until, time.perf_counter() + KEEP_WARM_SECONDS)
        self._ensure_worker()

    def restart(self) -> None:
        """Reopen the stream (output device changed or device list refreshed)."""
        self.error = None
        self._reopen.set()
        self._ensure_worker()

    def shutdown(self) -> None:
        """Close the device and end the worker (app exit)."""
        self._analyzing = False
        self._shutdown.set()
        if self._thread is not None:
            self._thread.join(2.0)
        self._thread = None
        self._features = AudioFeatures()

    def _ensure_worker(self) -> None:
        with self._lifecycle:
            if self._thread is not None and self._thread.is_alive():
                return
            self._shutdown = threading.Event()
            self._thread = threading.Thread(target=self._run, args=(self._shutdown,),
                                            name="audio-analyzer", daemon=True)
            self._thread.start()

    def _idle_expired(self) -> bool:
        return not self._analyzing and time.perf_counter() > self._warm_until

    @staticmethod
    def list_devices() -> list[tuple[int, str]]:
        """Loopback-capable output devices as (index, name)."""
        try:
            import pyaudiowpatch as pyaudio
        except ImportError:
            return []
        p = pyaudio.PyAudio()
        try:
            return [(d["index"], d["name"]) for d in p.get_loopback_device_info_generator()]
        except Exception as exc:
            log.warning("Could not list audio devices: %s", exc)
            return []
        finally:
            p.terminate()

    # ---------------------------------------------------------------- worker
    def _fail(self, message: str) -> None:
        self.error = message
        log.error(message)
        if self.on_error:
            self.on_error(message)

    def _on_audio(self, in_data, frame_count, time_info, status):
        if not self._analyzing:  # paused (stream kept warm): skip the work
            return (None, self._pa_continue)
        samples = np.frombuffer(in_data, dtype=np.float32)
        if self._channels > 1:
            samples = samples.reshape(-1, self._channels).mean(axis=1)
        n = min(len(samples), _RING)
        samples = samples[-n:]
        with self._buf_lock:
            end = self._ring_pos + n
            if end <= _RING:
                self._ring[self._ring_pos:end] = samples
            else:
                split = _RING - self._ring_pos
                self._ring[self._ring_pos:] = samples[:split]
                self._ring[:n - split] = samples[split:]
            self._ring_pos = end % _RING
        self._last_data = time.perf_counter()
        return (None, self._pa_continue)

    def _latest_window(self) -> np.ndarray:
        with self._buf_lock:
            start = self._ring_pos - _FFT_SIZE
            if start >= 0:
                return self._ring[start:self._ring_pos].copy()
            return np.concatenate((self._ring[start:], self._ring[:self._ring_pos]))

    def _run(self, shutdown: threading.Event) -> None:
        try:
            import pyaudiowpatch as pyaudio
        except ImportError:
            self._fail("System audio capture needs PyAudioWPatch (pip install PyAudioWPatch)")
            return
        self._pa_continue = pyaudio.paContinue

        while not shutdown.is_set() and not self._idle_expired():
            self._reopen.clear()
            # A fresh PyAudio per (re)open so newly connected devices and default changes are seen.
            p = pyaudio.PyAudio()
            stream = None
            try:
                if self.device_index is None:
                    info = p.get_default_wasapi_loopback()
                else:
                    info = p.get_device_info_by_index(self.device_index)
                self._channels = max(1, int(info["maxInputChannels"]))
                self._rate = int(info["defaultSampleRate"])
                self.device_name = info["name"]
                stream = p.open(
                    format=pyaudio.paFloat32,
                    channels=self._channels,
                    rate=self._rate,
                    input=True,
                    input_device_index=info["index"],
                    frames_per_buffer=512,
                    stream_callback=self._on_audio,
                )
                stream.start_stream()
                self.error = None
                log.info("Capturing system audio from: %s (%d Hz, %d ch)", self.device_name, self._rate, self._channels)
                self._analyze_loop(shutdown, stream)
            except Exception as exc:
                self._fail(f"Audio capture failed: {exc}")
                # e.g. headphones just disconnected: retry while Music mode wants audio
                shutdown.wait(2.0)
                if not self._analyzing:
                    break
            finally:
                if stream is not None:
                    try:
                        stream.stop_stream()
                        stream.close()
                    except Exception:
                        pass
                p.terminate()
        log.info("Audio capture closed")

    def _analyze_loop(self, shutdown: threading.Event, stream) -> None:
        window = np.hanning(_FFT_SIZE).astype(np.float32)
        freqs = np.fft.rfftfreq(_FFT_SIZE, 1.0 / self._rate)
        band_bins = {name: (freqs >= lo) & (freqs < hi) for name, (lo, hi) in BANDS.items()}
        processor = LevelProcessor()
        period = 1.0 / _ANALYSIS_HZ
        last = next_tick = time.perf_counter()

        while not shutdown.is_set() and not self._reopen.is_set():
            if not stream.is_active():
                raise RuntimeError("audio stream stopped (device removed?)")
            if not self._analyzing:
                if self._idle_expired():
                    return  # unused long enough: release the device
                shutdown.wait(0.05)  # paused: stream keeps running, nothing to compute
                last = next_tick = time.perf_counter()
                continue

            now = time.perf_counter()
            dt, last = min(0.25, max(1e-3, now - last)), now
            since_data = now - self._last_data
            fresh = since_data < _STALE_AFTER  # don't keep analyzing the last buffer after audio stops
            sens = max(0.0, min(1.0, self.sensitivity))

            raw = dict.fromkeys(LevelProcessor.NAMES, 0.0)
            if fresh:
                x = self._latest_window()
                rms = float(np.sqrt(np.mean(x * x)))
                gate = 10 ** ((-45.0 - 20.0 * sens) / 20.0)  # -45 dB (0 %) .. -65 dB (100 %)
                if rms > gate:
                    spectrum = np.abs(np.fft.rfft(x * window))
                    raw["volume"] = rms
                    for name, mask in band_bins.items():
                        raw[name] = float(np.sqrt(np.mean(spectrum[mask] ** 2)))

            features = processor.update(raw, since_data < 0.25, dt, now, sens, self.smoothing)
            if self._analyzing:  # may have been paused mid-tick
                self._features = features
                self._history.append((now, features))

            next_tick = max(next_tick + period, time.perf_counter() - period)
            wait = next_tick - time.perf_counter()
            if wait > 0:
                shutdown.wait(wait)
