"""Guided strip diagnostic: sends one command at a time and asks what the strip did.

    diagnose.bat            (or: .venv\\Scripts\\python tools\\diagnose.py)

Answers are saved to diagnose_results.txt in the project folder.
Uses Bleak directly (not the app) so the app's own logic can't interfere.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import os
import sys
from pathlib import Path

from bleak import BleakClient, BleakScanner

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "diagnose_results.txt"
WRITE = "0000fff3-0000-1000-8000-00805f9b34fb"
NOTIFY = "0000fff4-0000-1000-8000-00805f9b34fb"
DEFAULT_ADDRESS = "BE:69:6E:1A:68:05"

RED = "7E 07 05 03 FF 00 00 10 EF"
GREEN = "7E 07 05 03 00 FF 00 10 EF"
BLUE = "7E 07 05 03 00 00 FF 10 EF"

# (id, what we send, frames, question). Frames are sent in order with a short gap.
STEPS = [
    ("1-red", "Color RED (the frame that worked on day one)", [RED],
     "Did the strip turn RED?"),
    ("2-green-alt", "Color GREEN, alternative frame format", ["7E 00 05 03 00 FF 00 00 EF"],
     "Did the strip turn GREEN?"),
    ("3-blue-ack", "Color BLUE, sent as acknowledged write", ["ACK:" + BLUE],
     "Did the strip turn BLUE?"),
    ("4-bright-old", "Brightness 15% (original app's frame), then RED", ["7E 04 01 0F FF 00 00 00 EF", RED],
     "Is the strip RED and clearly DIMMER than before?"),
    ("5-bright-old-100", "Brightness back to 100% (original frame)", ["7E 04 01 64 FF 00 00 00 EF"],
     "Is the strip back to FULL brightness?"),
    ("6-bright-melk", "Brightness 15% (new MELK frame), then GREEN", ["7E 04 01 0F 01 FF FF 00 EF", GREEN],
     "Is the strip GREEN and clearly DIMMER?"),
    ("7-bright-melk-100", "Brightness back to 100% (new MELK frame), then BLUE", ["7E 04 01 64 01 FF FF 00 EF", BLUE],
     "Is the strip BLUE at FULL brightness?"),
    ("8-power-off", "Power OFF", ["7E 04 04 00 00 00 FF 00 EF"],
     "Did the strip turn OFF?"),
    ("9-power-on", "Power ON, then RED", ["7E 04 04 F0 00 01 FF 00 EF", RED],
     "Is the strip ON and RED?"),
    ("10-melk-effect", "Built-in animation 'Rainbow cycle' (MELK code 16)", ["7E 05 03 10 06 FF FF 00 EF"],
     "Is the strip ANIMATING (changing colors by itself)?"),
    ("11-effect-speed", "Animation speed to fast (90%)", ["7E 04 02 5A FF FF FF 00 EF"],
     "Did the animation get noticeably FASTER?"),
    ("12-color-after-effect", "Color RED (should stop the animation)", [RED],
     "Did the animation STOP and the strip turn solid RED?"),
    ("13-melk-breathing", "Built-in animation 'Breathing' (MELK code 48)", ["7E 05 03 30 06 FF FF 00 EF"],
     "Is the strip fading in and out (breathing)?"),
    ("14-melk-autoplay", "Built-in animation 'Auto play' (MELK code 0)", ["7E 05 03 00 06 FF FF 00 EF"],
     "Is the strip ANIMATING?"),
    ("15-elk-effect", "Built-in animation, classic ELK code 0x8A (old app's format)", ["7E 05 03 8A 03 FF FF 00 EF"],
     "Did the animation CHANGE to a different pattern?"),
    ("16-final-red", "Color RED", [RED],
     "Is the strip solid RED?"),
]


def ask(question: str) -> tuple[str, str]:
    while True:
        raw = input(f"   >> {question}  [y = yes, n = no change, o = something else]: ").strip().lower()
        if raw in ("y", "yes"):
            return "yes", ""
        if raw in ("n", "no"):
            return "no", ""
        if raw in ("o", "other", "something else"):
            note = input("      What did you see? ").strip()
            return "other", note
        print("      Please type y, n or o.")


def load_address() -> str:
    try:
        cfg = Path(os.environ.get("APPDATA", "")) / "NeonLights" / "settings.json"
        return json.loads(cfg.read_text(encoding="utf-8")).get("device_address") or DEFAULT_ADDRESS
    except Exception:
        return DEFAULT_ADDRESS


async def send(client: BleakClient, frames: list[str]) -> None:
    for f in frames:
        ack = f.startswith("ACK:")
        data = bytes.fromhex(f.removeprefix("ACK:").replace(" ", ""))
        await client.write_gatt_char(WRITE, data, response=ack)
        await asyncio.sleep(0.6)


async def main() -> int:
    address = load_address()
    log: list[str] = [f"Neon Lights diagnostic  {datetime.datetime.now():%Y-%m-%d %H:%M:%S}", f"address {address}"]
    notifications: list[str] = []

    def save() -> None:
        RESULTS.write_text("\n".join(log + ["", "notifications:"] + notifications) + "\n", encoding="utf-8")

    print("\n=== Neon Lights strip diagnostic ===")
    print("Close the Neon Lights app and the phone app first, then watch the strip.\n")
    print(f"Looking for {address} ...")
    device = await BleakScanner.find_device_by_address(address, timeout=15)
    if device is None:
        print("\nStrip not found. Make sure Neon Lights and the phone app are CLOSED and the strip is powered.")
        log.append("RESULT: strip not found")
        save()
        return 1

    async with BleakClient(device, timeout=20) as client:
        name = (device.name or "?").strip()
        props = client.services.get_characteristic(WRITE).properties
        log.append(f"connected to '{name}'  write props={props}")
        print(f"Connected to {name}.\n")
        try:
            await client.start_notify(NOTIFY, lambda _c, d: notifications.append(d.hex(" ").upper()))
        except Exception as exc:
            log.append(f"notify unavailable: {exc}")

        power_cycled = False
        for step_id, what, frames, question in STEPS:
            print(f"[{step_id}] Sending: {what}")
            try:
                await send(client, frames)
            except Exception as exc:
                log.append(f"{step_id}: SEND FAILED {exc}")
                print(f"   send failed: {exc}")
                save()
                return 1
            answer, note = ask(question)
            log.append(f"{step_id}: {answer}{' - ' + note if note else ''}   | {what} | {frames}")
            save()
            if step_id == "1-red" and answer != "yes" and not power_cycled:
                print("\n   The basic color command didn't work. The strip may be stuck in a mode.")
                print("   Please UNPLUG the strip's power/USB for 10 seconds, plug it back in,")
                input("   then press Enter here to reconnect and retry... ")
                log.append("-- user power-cycled the strip, reconnecting --")
                save()
                return await retry_after_power_cycle(address, log, save)
            if not client.is_connected:
                log.append(f"{step_id}: connection dropped")
                print("   Connection dropped.")
                break

    log.append("DONE")
    save()
    print(f"\nAll done - thank you! Results saved to:\n  {RESULTS}\nTell Claude you're finished.")
    return 0


async def retry_after_power_cycle(address: str, log: list[str], save) -> int:
    device = await BleakScanner.find_device_by_address(address, timeout=20)
    if device is None:
        log.append("RESULT: not found after power cycle")
        save()
        print("Strip not found after the power cycle.")
        return 1
    async with BleakClient(device, timeout=20) as client:
        for step_id, what, frames, question in STEPS:
            print(f"[{step_id}] Sending: {what}")
            await send(client, frames)
            answer, note = ask(question)
            log.append(f"(after power cycle) {step_id}: {answer}{' - ' + note if note else ''}   | {what}")
            save()
    log.append("DONE")
    save()
    print(f"\nAll done - thank you! Results saved to:\n  {RESULTS}\nTell Claude you're finished.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        sys.exit(1)
