"""Magic Lantern / ELK-BLEDOM / MELK wire protocol.

Every command is a 9-byte frame:  7E <len> <cmd> <p1> <p2> <p3> <p4> <p5> EF

The color frame is identical across the family and verified on the target
strip:  7E 07 05 03 RR GG BB 10 EF

Power, brightness and built-in effects differ per controller model, so they
come from a DeviceProfile chosen from the advertised device name. Profiles
follow the model table of the elkbledom Home Assistant integration
(github.com/dave-code-ruiz/elkbledom), which lists MELK-OA10 explicitly.

To add a command: add a method that returns a frame (use `self.frame`). To add
a model: add a DeviceProfile to PROFILES.
"""

from __future__ import annotations

from dataclasses import dataclass, field

SERVICE_UUID = "0000fff0-0000-1000-8000-00805f9b34fb"
WRITE_UUID = "0000fff3-0000-1000-8000-00805f9b34fb"
NOTIFY_UUID = "0000fff4-0000-1000-8000-00805f9b34fb"


@dataclass(frozen=True)
class DeviceProfile:
    key: str
    label: str
    name_prefixes: tuple[str, ...]
    effects: dict[str, int]
    power_on: tuple[int, ...]           # 7 body bytes (between 7E and EF)
    brightness: tuple[int, ...]         # body with None where the 0-100 value goes
    effect: tuple[int, ...]             # body with None where the effect code goes
    login: tuple[bytes, ...] = field(default=())  # raw frames sent right after connecting

    def matches(self, device_name: str) -> bool:
        name = device_name.strip().upper()
        return any(name.startswith(p) for p in self.name_prefixes)


_ELK_EFFECTS = {
    "Jump: red / green / blue": 0x87,
    "Jump: 7 colors": 0x88,
    "Crossfade: red / green / blue": 0x89,
    "Crossfade: 7 colors": 0x8A,
    "Breathe: red": 0x8B,
    "Breathe: green": 0x8C,
    "Breathe: blue": 0x8D,
    "Breathe: yellow": 0x8E,
    "Breathe: cyan": 0x8F,
    "Breathe: magenta": 0x90,
    "Breathe: white": 0x91,
    "Crossfade: red / green": 0x92,
    "Crossfade: red / blue": 0x93,
    "Crossfade: green / blue": 0x94,
    "Blink: 7 colors": 0x95,
    "Blink: red": 0x96,
    "Blink: green": 0x97,
    "Blink: blue": 0x98,
    "Blink: yellow": 0x99,
    "Blink: cyan": 0x9A,
    "Blink: magenta": 0x9B,
    "Blink: white": 0x9C,
}

_MELK_O_EFFECTS = {
    "Auto play (cycles all)": 0,
    "Magic back": 1,
    "Effect 2": 2,
    "Effect 3": 3,
    "Effect 4": 4,
    "Effect 5": 5,
    "Rainbow cycle": 16,
    "Color wave": 32,
    "Breathing": 48,
    "Strobe": 64,
    "Jump RGB": 128,
    "Fade RGB": 144,
    "Blue scroll": 207,
}

_MELK_EFFECTS = {
    "Switches (all, omni)": 0,
    "Soft fade (all)": 1,
    "Fade RGB left": 5,
    "Chase all right": 10,
    "Fast chase all right": 16,
    "Fade all right": 22,
    "Fade red right": 26,
    "Fade green right": 28,
    "Fade cyan left": 33,
    "Chase all (CO)": 58,
    "Chase white (CI)": 75,
    "Chase all (SL)": 77,
    "Chase white-blue-white left": 151,
    "Chase white-blue-white right": 152,
    "Chase red-white-red left": 155,
    "Chase red-white-red right": 156,
}

# Some integrations send a MELK "login" (7E 07 83, 7E 04 04) after connecting. Do NOT: on a
# MELK-OA10 it switches the strip off and makes it ignore all commands until power-cycled
# (verified with tools/diagnose.py). The `login` hook stays for models that truly need one.

PROFILES: tuple[DeviceProfile, ...] = (
    DeviceProfile(
        key="melk_o",
        label="MELK-Ox (OA10/OC10/OF10/OG10)",
        name_prefixes=("MELK-OA", "MELK-OC", "MELK-OF", "MELK-OG"),
        effects=_MELK_O_EFFECTS,
        power_on=(0x04, 0x04, 0xF0, 0x00, 0x01, 0xFF, 0x00),
        brightness=(0x04, 0x01, None, 0x01, 0xFF, 0xFF, 0x00),
        effect=(0x05, 0x03, None, 0x06, 0xFF, 0xFF, 0x00),
    ),
    DeviceProfile(
        key="melk",
        label="MELK",
        name_prefixes=("MELK",),
        effects=_MELK_EFFECTS,
        power_on=(0x00, 0x04, 0x01, 0x00, 0x00, 0x00, 0x00),
        brightness=(0x04, 0x01, None, 0xFF, 0x00, 0xFF, 0x00),
        effect=(0x05, 0x03, None, 0x06, 0xFF, 0xFF, 0x00),
    ),
    DeviceProfile(
        key="elk",
        label="ELK-BLEDOM (generic)",
        name_prefixes=(),  # fallback
        effects=_ELK_EFFECTS,
        power_on=(0x04, 0x04, 0xF0, 0x00, 0x01, 0xFF, 0x00),
        brightness=(0x04, 0x01, None, 0x01, 0xFF, 0x02, 0x01),
        effect=(0x07, 0x03, None, 0x03, 0xFF, 0xFF, 0x00),
    ),
)
DEFAULT_PROFILE = PROFILES[-1]


def profile_for_name(device_name: str | None) -> DeviceProfile:
    if device_name:
        for profile in PROFILES:
            if profile.name_prefixes and profile.matches(device_name):
                return profile
    return DEFAULT_PROFILE


def _clamp_byte(v: float) -> int:
    return max(0, min(255, int(round(v))))


def _clamp_pct(v: float) -> int:
    return max(0, min(100, int(round(v))))


class MagicLanternProtocol:
    """Frame builders for the active DeviceProfile. Stateless apart from the profile."""

    service_uuid = SERVICE_UUID
    write_uuid = WRITE_UUID
    notify_uuid = NOTIFY_UUID

    HEADER = 0x7E
    FOOTER = 0xEF

    def __init__(self, profile: DeviceProfile = DEFAULT_PROFILE) -> None:
        self.profile = profile

    def select_profile(self, device_name: str | None) -> DeviceProfile:
        self.profile = profile_for_name(device_name)
        return self.profile

    @property
    def device_effects(self) -> dict[str, int]:
        return self.profile.effects

    @classmethod
    def frame(cls, *body: int) -> bytes:
        """Wrap up to 7 body bytes in 7E ... EF, zero-padding to 9 bytes."""
        if len(body) > 7:
            raise ValueError("frame body is at most 7 bytes")
        padded = list(body) + [0x00] * (7 - len(body))
        return bytes([cls.HEADER, *(b & 0xFF for b in padded), cls.FOOTER])

    def _templated(self, template: tuple[int | None, ...], value: int) -> bytes:
        return self.frame(*(value if b is None else b for b in template))

    # --- commands -------------------------------------------------------------
    def color(self, r: float, g: float, b: float) -> bytes:
        return self.frame(0x07, 0x05, 0x03, _clamp_byte(r), _clamp_byte(g), _clamp_byte(b), 0x10)

    def power(self, on: bool) -> bytes:
        if on:
            return self.frame(*self.profile.power_on)
        return self.frame(0x04, 0x04, 0x00, 0x00, 0x00, 0xFF, 0x00)

    def brightness(self, percent: float) -> bytes:
        """Hardware brightness, 0-100."""
        return self._templated(self.profile.brightness, _clamp_pct(percent))

    def effect(self, code: int) -> bytes:
        """Start a built-in animation (codes from `device_effects`)."""
        return self._templated(self.profile.effect, code & 0xFF)

    def effect_speed(self, percent: float) -> bytes:
        """Speed of built-in animations, 0-100."""
        return self.frame(0x04, 0x02, _clamp_pct(percent), 0xFF, 0xFF, 0xFF, 0x00)

    def white(self, warm: float, cold: float) -> bytes:
        """Warm/cold white channels (only on strips that have them), 0-100 each."""
        return self.frame(0x06, 0x05, 0x02, _clamp_pct(warm), _clamp_pct(cold), 0xFF, 0x08)

    def login(self) -> tuple[bytes, ...]:
        """Handshake frames some models (MELK) expect right after connecting."""
        return self.profile.login

    # --- helpers --------------------------------------------------------------
    @staticmethod
    def parse_hex(text: str) -> bytes:
        """'7E 07 05 03 FF 00 00 10 EF' -> bytes. Accepts spaces, commas, 0x prefixes."""
        cleaned = text.replace(",", " ").replace("0x", "").replace("0X", "")
        return bytes.fromhex("".join(cleaned.split()))

    @staticmethod
    def to_hex(data: bytes) -> str:
        return " ".join(f"{b:02X}" for b in data)
