"""JSON settings persisted in %APPDATA%\\NeonLights\\settings.json."""

from __future__ import annotations

import copy
import json
import logging
import os
from pathlib import Path
from typing import Any

from . import APP_ID

log = logging.getLogger("neon.config")

DEFAULT_ADDRESS = "BE:69:6E:1A:68:05"
LEGACY_APP_IDS = ("FloatChatLights",)  # settings folders of earlier names, migrated on first run

# A scene stores any subset of these settings sections and is applied on top.
SCENE_KEYS = ("mode", "brightness", "manual_color", "effect", "effects_source",
              "device_effect", "screen", "music")

DEFAULT_SCENES: list[dict[str, Any]] = [
    {"name": "Focus", "state": {"mode": "manual", "brightness": 90, "manual_color": [255, 214, 170]}},
    {"name": "Relax", "state": {"mode": "effects", "effects_source": "pc", "brightness": 55,
                                "effect": {"name": "Candle", "speed": 35,
                                           "colors": [[255, 110, 20], [255, 60, 0], [255, 150, 40]]}}},
    {"name": "Gaming", "state": {"mode": "screen", "brightness": 100,
                                 "screen": {"region": "edges", "mode": "vivid", "sensitivity": 65,
                                            "smoothing": 30, "rate_hz": 60}}},
    {"name": "Movie", "state": {"mode": "screen", "brightness": 70,
                                "screen": {"region": "full", "mode": "average", "sensitivity": 35,
                                           "smoothing": 70, "rate_hz": 30}}},
    {"name": "Party", "state": {"mode": "music", "brightness": 100,
                                "music": {"mode": "bpm_sync", "sensitivity": 60, "smoothing": 35}}},
]

DEFAULTS: dict[str, Any] = {
    "device_address": DEFAULT_ADDRESS,
    "known_devices": [DEFAULT_ADDRESS],
    "auto_reconnect": True,
    "connect_on_start": True,
    "max_send_rate_hz": 20,  # color updates/s; the MELK-OA10 link carries ~30/s, beyond that it lags
    "write_with_response": None,  # null = auto (write-without-response when supported)
    "power": True,
    "brightness": 100,
    "mode": "manual",
    "manual_color": [255, 0, 0],
    "favorites": [],  # [{"name": str, "color": [r, g, b]}]
    "effect": {
        "name": "Rainbow",
        "speed": 50,
        "colors": [[255, 0, 0], [0, 255, 0], [0, 0, 255]],
    },
    "device_name": "",  # advertised name of the last strip, picks the protocol profile
    "device_effect": {"name": "", "speed": 50},
    "screen": {
        "monitor": 1,
        "rate_hz": 30,
        "smoothing": 50,
        "sensitivity": 50,
        "mode": "vivid",
        "region": "full",
        "fast_capture": True,  # DXGI desktop duplication when available
    },
    "music": {
        "mode": "spectrum",
        "sensitivity": 50,
        "smoothing": 40,
        "color": [255, 0, 140],
        "device": None,
        "delay_ms": 0,  # light delay to match Bluetooth headphones
    },
    "calibration": {"red": 100, "green": 100, "blue": 100, "gamma": 1.0},
    "scenes": DEFAULT_SCENES,
    "show_log": False,
    "window_geometry": None,
}


def _config_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / APP_ID


def _merge(defaults: dict, loaded: dict) -> dict:
    out = copy.deepcopy(defaults)
    for key, value in loaded.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


class Settings(dict):
    """A dict with load/save. Nested sections are plain dicts."""

    path: Path = _config_dir() / "settings.json"

    @classmethod
    def load(cls) -> "Settings":
        data: dict = {}
        source = cls.path
        if not source.exists():
            for legacy in LEGACY_APP_IDS:
                old = cls.path.parent.parent / legacy / cls.path.name
                if old.exists():
                    source = old
                    log.info("Migrating settings from %s", old)
                    break
        try:
            if source.exists():
                data = json.loads(source.read_text(encoding="utf-8"))
        except Exception as exc:
            log.warning("Ignoring unreadable settings file (%s): %s", source, exc)
        if not isinstance(data, dict):
            data = {}
        # "Effects" used to remember the on-strip source and silently replay it - the cause of
        # lights "stuck static" when that animation code didn't exist on the strip.
        data.pop("effects_source", None)
        # Rates above ~30/s overfill the Bluetooth link and the strip falls seconds behind
        # (measured: 40/s -> 1.3 s, 50/s -> 2.5 s); bring any such saved value back to the default.
        data.pop("rate_reviewed", None)
        data.pop("rate_v2", None)
        if data.get("max_send_rate_hz", 0) > 30:
            data["max_send_rate_hz"] = DEFAULTS["max_send_rate_hz"]
        return cls(_merge(DEFAULTS, data))

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self, indent=2), encoding="utf-8")
            tmp.replace(self.path)
        except Exception as exc:
            log.error("Could not save settings: %s", exc)
