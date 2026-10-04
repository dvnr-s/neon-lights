"""Finds how fast the strip accepts color updates before its firmware locks up.

    rate_test.bat   (close Neon Lights first)

Streams a rainbow at increasing rates; after each run asks whether the strip
animated and whether it still responds. Answers go to rate_test_results.txt.
"""

from __future__ import annotations

import asyncio
import colorsys
import datetime
import sys
import time
from pathlib import Path

from bleak import BleakClient, BleakScanner

sys.path.insert(0, str(Path(__file__).resolve().parent))
from diagnose import RED, WRITE, ask, load_address  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "rate_test_results.txt"
RATES = (10, 20, 25, 30, 40, 60)  # updates per second
SECONDS = 4.0


def color_frame(hue: float) -> bytes:
    r, g, b = (int(v * 255) for v in colorsys.hsv_to_rgb(hue % 1.0, 1.0, 1.0))
    return bytes([0x7E, 0x07, 0x05, 0x03, r, g, b, 0x10, 0xEF])


async def stream(client: BleakClient, hz: float) -> int:
    gap = 1.0 / hz
    sent = 0
    start = time.perf_counter()
    due = start
    while time.perf_counter() - start < SECONDS:
        while time.perf_counter() < due:
            await asyncio.sleep(0.001)
        await client.write_gatt_char(WRITE, color_frame((time.perf_counter() - start) / 2.0), response=False)
        sent += 1
        due += gap
    return sent


async def connect(address: str) -> BleakClient | None:
    device = await BleakScanner.find_device_by_address(address, timeout=20)
    if device is None:
        return None
    client = BleakClient(device, timeout=20)
    await client.connect()
    return client


async def main() -> int:
    address = load_address()
    log = [f"Neon Lights rate test  {datetime.datetime.now():%Y-%m-%d %H:%M:%S}", f"address {address}"]

    def save() -> None:
        RESULTS.write_text("\n".join(log) + "\n", encoding="utf-8")

    print("\n=== Neon Lights speed test ===")
    print("Close the Neon Lights app first, then watch the strip.\n")
    client = await connect(address)
    if client is None:
        print("Strip not found - is Neon Lights closed and the strip powered?")
        log.append("RESULT: not found")
        save()
        return 1
    try:
        await client.write_gatt_char(WRITE, bytes.fromhex(RED.replace(" ", "")), response=False)
        answer, note = ask("Baseline: is the strip solid RED?")
        log.append(f"baseline red: {answer} {note}")
        save()
        if answer != "yes":
            print("   Please unplug the strip for 10 s, plug it back in, then run this test again.")
            return 1

        for hz in RATES:
            print(f"\n[{hz}/s] Streaming a rainbow at {hz} updates per second for {SECONDS:.0f} s ...")
            sent = await stream(client, hz)
            animated, n1 = ask("Did the strip smoothly cycle through colors (rainbow)?")
            await asyncio.sleep(0.5)
            await client.write_gatt_char(WRITE, bytes.fromhex(RED.replace(" ", "")), response=False)
            responds, n2 = ask("Now: is the strip solid RED?")
            log.append(f"{hz}/s ({sent} frames): animated={animated} {n1} | responds_after={responds} {n2}")
            save()
            if responds != "yes":
                log.append(f"LOCKED at {hz}/s")
                save()
                print("\n   Found the limit. Please UNPLUG the strip for 10 seconds and plug it back in.")
                print("   (That clears the lock-up.) No need to continue.")
                break
    finally:
        await client.disconnect()

    log.append("DONE")
    save()
    print(f"\nAll done - thank you! Results saved to:\n  {RESULTS}\nTell Claude you're finished.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        sys.exit(1)
