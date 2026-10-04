# Neon Lights

Windows desktop controller for Magic Lantern / ELK-BLEDOM / MELK–compatible RGB LED strips.
It talks to the strip's existing Bluetooth controller directly; you don't need the phone app or an ESP32.

- **Manual**: color wheel, RGB sliders, hex entry, 14 presets, saved favorites
- **Effects**: PC-rendered Static, Fade, Pulse, Rainbow, Color Jump, Strobe, Candle with adjustable speed and a 3-color palette. Also plays the strip's own built-in animations; the list matches your controller model.
- **Screen sync**: samples a monitor (full, center or edges) with adjustable sensitivity, smoothing and update rate. It uses GPU capture (DXGI) at up to 60 fps and falls back to GDI.
- **Music sync**: captures system audio through WASAPI loopback (no microphone). Seven styles: Spectrum, Bass pulse, Beat hop, BPM sync (locked to the detected tempo), Drop strobe, Mood mix and Energy rainbow. **Smoothing** sets how long the lights take to fade after a hit (shown in ms). **Light delay** syncs the lights to Bluetooth headphones, which play sound about 150–300 ms after the PC does.
- **Scenes**: one click restores a whole setup (mode, effect, palette, screen/music settings, brightness). Five starter scenes are included.
- **Color calibration**: per-channel white balance and gamma, with test patterns shown on the strip
- Auto-reconnect with backoff, live connection status, non-blocking Bluetooth, keyboard shortcuts, settings saved between runs

## Quick start

Requirements: Windows 10/11 with Bluetooth LE, and Python 3.10+ on PATH.

```bat
run.bat
```

The first run creates `.venv` and installs the dependencies. Later runs just start the app.

To set it up by hand instead:

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

> **Disconnect the phone app first.** These strips accept only one Bluetooth connection at a time. If the phone is connected, the strip stops advertising and the PC can't find it.

The default device is `BE:69:6E:1A:68:05`. To use a different strip, type its address or click **Scan**. Devices that look like LED strips get a ★. The app connects automatically on startup; you can turn that off under **File → Connect on startup**.

### Guided strip test

Close the app first, watch the strip, and answer the prompts. Answers are saved next to the script:

- `diagnose.bat` sends one raw command at a time: colors, brightness, power and built-in animations.
- `app_test.bat` drives the strip through the app's own Bluetooth and engine code.
- `rate_test.bat` checks how many updates per second the strip accepts.

### Headless BLE test

```bat
.venv\Scripts\python tools\ble_test.py            :: red → green → blue → red
.venv\Scripts\python tools\ble_test.py --scan
.venv\Scripts\python tools\ble_test.py --raw "7E 07 05 03 FF 00 00 10 EF"
```

## Keyboard shortcuts

| Keys | Action |
|---|---|
| Ctrl+K | Connect / disconnect |
| Ctrl+P | Power on / off |
| Ctrl+Up / Ctrl+Down | Brightness ±10% |
| Ctrl+1 … Ctrl+4 | Manual / Effects / Screen / Music |
| Alt+1 … Alt+9 | Preset colors 1–9 |
| Ctrl+S | Save current color as a favorite |
| Ctrl+Shift+1 … 9 | Apply scene 1–9 |
| Ctrl+Shift+S | Save current setup as a scene |
| Ctrl+Shift+C | Color calibration |
| Ctrl+Right / Ctrl+Left | Next / previous effect |
| Ctrl+= / Ctrl+− | Effect speed ±10 |
| Ctrl+L | Show / hide the log and raw command panel |
| F1 | Shortcut list |

## Architecture

```
GUI (PySide6)  ──►  LightingEngine  ──►  Effect / ScreenSampler / AudioAnalyzer+MusicVisualizer
   ui/                engine/                     engine/
                         │
                         ▼  send_color / send_power / send(...)
                    BleController  ──►  MagicLanternProtocol  ──►  FFF3
                      ble/                ble/protocol.py
```

```
main.py                       entry point (wires the layers together)
neon_lights/
  config.py                   JSON settings in %APPDATA%\NeonLights\settings.json
  ble/                        HARDWARE LAYER: no Qt, no effects
    protocol.py               9-byte 7E…EF frame builders + per-model DeviceProfiles
    controller.py             Bleak on its own asyncio thread; coalescing write queue; reconnect
  engine/                     LOGIC LAYER: no Qt, no bytes
    lighting_engine.py        mode/power/brightness/calibration, 60 Hz render loop, change-only sends
    effects.py                PC effects (subclass Effect + register in EFFECTS)
    screen.py                 DXGI (dxcam) or GDI (mss) capture → color (worker thread)
    audio.py                  WASAPI loopback → FFT bands, beats, tempo, drop surge (worker thread)
    music.py                  audio features → color styles
    color.py                  color helpers
  ui/                         PRESENTATION LAYER
    main_window.py            connection bar, power/brightness, modes, scenes, log dock, shortcuts
    pages/                    manual.py, effects.py, screen.py, music.py
    scenes.py, calibration.py scene bar; calibration dialog
    widgets.py, theme.py      color wheel, swatches, sliders; dark neon theme + generated icon
    bridge.py                 thread → Qt-signal bridge for BLE status and logging
tools/ble_test.py             headless connection test using the real BLE layer
```

How the threads work:
- **BLE thread**: a private asyncio loop runs every Bleak call. The public methods only schedule work, so they never block.
- **Engine thread**: renders the active mode 60 times a second and sends a frame only when the color changes.
- **Screen and audio threads**: each produces a "latest value" that the engine reads. Both open in the background at startup and only pause when you leave their mode, so switching modes takes effect in about 20–50 ms. The device is released after 2 minutes unused.
- **GUI thread**: only reads state on a 25 Hz timer, and skips drawing entirely while the window is minimized. Worker events reach it through Qt signals.

The BLE write queue is **keyed and coalescing**. A new color replaces any color that hasn't been sent yet, so screen and music sync always send the newest frame and lag never builds up. Writes are capped at 20 per second by default (`max_send_rate_hz`). Every 3 s, one frame is sent as an acknowledged write. Its round trip waits behind anything queued, so a slow reply (over 350 ms) means a backlog, and the writer slows down until it clears.

Timing: Windows sleeps are normally rounded to 15.6 ms. The app requests 1 ms timer resolution and keeps a fixed minimum gap between frames, timed with a high-resolution clock, so it holds its rate without bursts.

### Strip quirks (MELK-OA10)

Each of these was verified on the real strip with the guided tests:

- **No "login" handshake.** Some integrations send `7E 07 83` / `7E 04 04` after connecting. On this strip it switches the LEDs off and locks the controller until it's power-cycled.
- **No low-latency link request.** If Windows is asked for "preferred connection parameters", the strip stays connected and acknowledges every write, but stops applying them.
- **The Bluetooth link carries about 30 frames/s.** Faster updates don't fail. They queue up silently in Windows and the strip falls behind (measured: 10–30/s ≈ 0.1–0.2 s, 40/s ≈ 1.3 s, 50/s ≈ 2.5 s), so mode switches show up late. The app sends 20/s by default (**Tools → Update rate**, max 30). A watchdog also times one acknowledged frame every 3 s and slows down if a backlog appears, for example while Bluetooth headphones share the radio.

If the strip ever shows one static color and ignores the app, unplug it for 10 seconds to clear a lock-up, then run `diagnose.bat`.

### Performance

These figures are from a 2560×1440 PC, measured as percent of one CPU core:

| Mode | Window visible | Minimized |
|---|---|---|
| Manual | ~1% | ~1% |
| Effects | ~5–10% | ~7% |
| Music | ~10% | ~6% |
| Screen (DXGI, screen constantly changing) | ~20–35% | ~20–25% |

The old GDI screen capture alone used about 50% of a core at only 18 fps.

### Brightness

Brightness is applied in software, by scaling RGB with a perceptual curve, so it behaves the same in every mode. On each connect the app sets the strip's hardware dimmer to 100%. The exception is built-in strip animations, which the PC can't scale; in that mode the slider drives the hardware dimmer instead.

### Controller models

Power, hardware brightness and the built-in animations differ between controller models. `ble/protocol.py` holds a `DeviceProfile` per model family, and the app picks one from the name the strip advertises:

| Profile | Matches | Notes |
|---|---|---|
| MELK-Ox | `MELK-OA…`, `OC`, `OF`, `OG` (yours: **MELK-OA10**) | 13 animations |
| MELK | other `MELK…` | 16 animations |
| ELK-BLEDOM | everything else | Classic 0x87–0x9C animations |

The command tables follow the model list of the [elkbledom Home Assistant integration](https://github.com/dave-code-ruiz/elkbledom). That integration also sends a MELK "login" handshake; Neon Lights deliberately doesn't, because on a MELK-OA10 it switches the strip off and locks it until power-cycled. Verified on your strip with `diagnose.bat`: color, brightness (both frame formats) and power on/off. To support another model, add a `DeviceProfile` to `PROFILES`.

### Adding protocol commands

1. Add a frame builder to `MagicLanternProtocol` in `ble/protocol.py`:
   ```python
   def my_command(self, value: int) -> bytes:
       return self.frame(0x04, 0x0A, value, 0xFF, 0x00, 0x00, 0x00)   # → 7E 04 0A vv FF 00 00 00 EF
   ```
2. Send it with `ble.send(ble.protocol.my_command(5), key="my_command")`. The key makes repeated sends coalesce.
3. Before writing code, you can try candidate frames in the **Log and raw commands** panel (Ctrl+L). Tick **Log TX/RX frames** to see traffic, including FFF4 notifications.

## Settings

Settings are stored in `%APPDATA%\NeonLights\settings.json`. Advanced keys:

| Key | Default | Meaning |
|---|---|---|
| `max_send_rate_hz` | 20 | Color updates per second (also under Tools → Update rate). Above ~30 the strip lags. |
| `write_with_response` | `null` | `null` uses write-without-response when available (fast). `true` makes every write acknowledged (slower). |
| `connect_on_start` | `true` | Connect automatically when the app opens |

## Troubleshooting

- **"not found"**: power-cycle the strip and close the phone app. Toggling Windows Bluetooth off and on also helps. On some machines the first connection after boot takes 10–20 s.
- **Lights run ahead of the music on Bluetooth headphones**: raise **Light delay** on the Music page until the beats line up (usually 150–250 ms).
- **Music sync says "waiting for audio"**: play something; levels only move while audio is playing. If you switch outputs (for example to headphones), pick the device under **Output** or press **Refresh**.
- **Screen sync shows black for a game or video**: some DRM players and exclusive-fullscreen games block GPU capture. Untick **Fast GPU capture (DXGI)** on the Screen page to use GDI, or run the game in borderless-windowed mode.
- **Built-in animations do nothing**: check that **Controller profile** on the Effects page matches your strip. It's picked automatically when you connect.
- **Colors lag in reactive modes**: lower **Smoothing**. If the strip seems overwhelmed, also lower `max_send_rate_hz`.

## Packaging as a Windows .exe

Run this once the app works from source:

```bat
build_exe.bat
```

The script installs PyInstaller into `.venv`, renders `assets\icon.ico`, and builds:

```
dist\Neon Lights\Neon Lights.exe
```

Ship the whole `dist\Neon Lights\` folder, for example zipped. It doesn't need Python. The PyInstaller flags it uses:

- `--windowed`: no console window
- `--collect-submodules bleak --collect-submodules winrt --collect-submodules dxcam`: these load backends dynamically, and PyInstaller misses those modules without this
- Building a one-folder app instead of using `--onefile` makes startup much faster. `--onefile` also works, but it unpacks about 100 MB to a temp folder on every launch.

Optional extras:

- **Smaller build**: PySide6 brings a lot of Qt. Adding `--exclude-module PySide6.QtWebEngineCore --exclude-module PySide6.Qt3DCore` (and similar) trims modules you don't use.
- **Installer**: point [Inno Setup](https://jrsoftware.org/isinfo.php) at `dist\Neon Lights\` to get a `Setup.exe` with Start-menu and desktop shortcuts.
- **Start with Windows**: put a shortcut to the .exe in `shell:startup`.
- **SmartScreen**: unsigned executables show an "unknown publisher" prompt the first time they run. Code-signing removes it.
