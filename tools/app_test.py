"""Drives the strip through the app's real BLE controller + lighting engine, asking what you see.

    app_test.bat   (close Neon Lights first)

Use it to check the full app path (BLE layer + engine) without the GUI.
Answers go to app_test_results.txt.
"""

from __future__ import annotations

import ctypes
import datetime
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from diagnose import ask, load_address  # noqa: E402
from neon_lights.ble import BleController, ConnectionState  # noqa: E402
from neon_lights.engine import LightingEngine, Mode  # noqa: E402

RESULTS = ROOT / "app_test_results.txt"


def main() -> int:
    ctypes.windll.winmm.timeBeginPeriod(1)  # same as the app
    logging.basicConfig(level=logging.WARNING)
    address = load_address()
    log = [f"Neon Lights app-code test  {datetime.datetime.now():%Y-%m-%d %H:%M:%S}", f"address {address}"]

    def save() -> None:
        RESULTS.write_text("\n".join(log) + "\n", encoding="utf-8")

    def step(step_id: str, action, question: str, wait: float = 1.0) -> str:
        print(f"\n[{step_id}]")
        action()
        time.sleep(wait)
        answer, note = ask(question)
        log.append(f"{step_id}: {answer}{' - ' + note if note else ''}   (state={ble.status.state.value}, "
                   f"writes={ble.writes_sent})")
        save()
        return answer

    print("\n=== Neon Lights app-code test ===")
    print("Close the Neon Lights app first, then watch the strip.\n")
    ble = BleController(max_send_rate_hz=30)
    ble.auto_reconnect = False
    ble.start()
    engine = LightingEngine(ble)
    engine.set_manual_color((255, 0, 0))
    engine.start()
    try:
        print(f"Connecting to {address} with the app's Bluetooth code ...")
        ble.connect(address)
        deadline = time.time() + 40
        while time.time() < deadline and ble.status.state not in (ConnectionState.CONNECTED, ConnectionState.ERROR):
            time.sleep(0.1)
        log.append(f"connect: {ble.status.state.value} - {ble.status.message} - profile={ble.protocol.profile.key}")
        save()
        if not ble.is_connected:
            print("Could not connect:", ble.status.message)
            return 1

        step("A1-connect-red", lambda: None,
             "Right after connecting, did the strip turn ON and RED?", wait=1.5)
        step("A2-manual-green", lambda: engine.set_manual_color((0, 255, 0)),
             "Manual color: did it turn GREEN?")
        step("A3-manual-blue", lambda: engine.set_manual_color((0, 0, 255)),
             "Manual color: did it turn BLUE?")
        step("A4-brightness", lambda: engine.set_brightness(25),
             "Brightness 25%: did it get clearly DIMMER?")
        engine.set_brightness(100)
        engine.set_effect("Rainbow")
        engine.set_effect_speed(70)
        step("A5-effect-rainbow", lambda: engine.set_mode(Mode.EFFECT),
             "Effects mode: is it CYCLING through rainbow colors?", wait=3.0)
        step("A6-back-to-manual", lambda: (engine.set_mode(Mode.MANUAL), engine.set_manual_color((255, 0, 255))),
             "Back to manual: is it solid MAGENTA (pink-purple)?")

    finally:
        engine.stop()
        ble.shutdown()

    log.append("DONE")
    save()
    print(f"\nAll done - thank you! Results saved to:\n  {RESULTS}")
    print("Then tell Claude you're finished.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(1)
