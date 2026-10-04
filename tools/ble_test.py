"""Headless BLE check using the app's own BLE layer (no GUI).

    python tools/ble_test.py                 # connect, cycle red/green/blue, then back to red
    python tools/ble_test.py --scan          # list nearby devices
    python tools/ble_test.py --raw "7E 07 05 03 FF 00 00 10 EF"
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from neon_lights.ble import BleController, ConnectionState  # noqa: E402
from neon_lights.config import DEFAULT_ADDRESS  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--address", default=DEFAULT_ADDRESS)
    ap.add_argument("--scan", action="store_true")
    ap.add_argument("--raw", help="hex frame to send instead of the color cycle")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")

    ble = BleController()
    ble.start()
    try:
        if args.scan:
            for r in ble.scan(6.0).result(15):
                print(f"{'*' if r.likely_strip else ' '} {r.address}  {r.rssi:>4} dBm  {r.name}")
            return 0

        ble.auto_reconnect = False
        ble.connect(args.address)
        deadline = time.time() + 30
        while time.time() < deadline and ble.status.state not in (ConnectionState.CONNECTED, ConnectionState.ERROR):
            time.sleep(0.1)
        if not ble.is_connected:
            print("FAILED:", ble.status.message)
            return 1

        if args.raw:
            ble.send(ble.protocol.parse_hex(args.raw))
        else:
            for rgb in ((255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 0, 0)):
                ble.send_color(*rgb)
                time.sleep(0.8)
        time.sleep(0.5)
        print("OK - frames written:", ble.writes_sent)
        return 0
    finally:
        ble.shutdown()


if __name__ == "__main__":
    sys.exit(main())
