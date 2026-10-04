"""Threaded, non-blocking BLE controller built on Bleak.

All Bleak work runs on a private asyncio loop in a background thread. The
public methods are thread-safe and return immediately, so the UI and the
lighting engine never block on Bluetooth.

Writes go through a keyed, coalescing queue: sending a new color replaces any
color that has not been written yet, so a fast producer (screen/music sync)
always gets the freshest frame instead of building up lag.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import itertools
import logging
import threading
import time
from collections import OrderedDict
from contextlib import suppress
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Hashable

from bleak import BleakClient, BleakScanner

from .protocol import MagicLanternProtocol

log = logging.getLogger("neon.ble")


class ConnectionState(str, Enum):
    DISCONNECTED = "Disconnected"
    SCANNING = "Searching"
    CONNECTING = "Connecting"
    CONNECTED = "Connected"
    RECONNECTING = "Reconnecting"
    ERROR = "Error"


@dataclass(frozen=True)
class ConnectionStatus:
    state: ConnectionState
    message: str = ""
    address: str = ""
    attempt: int = 0
    device_name: str = ""


@dataclass(frozen=True)
class ScanResult:
    name: str
    address: str
    rssi: int
    likely_strip: bool


_STRIP_NAME_PREFIXES = ("ELK", "MELK", "BLEDOM", "LEDBLE", "LEDNET", "TRIONES", "DUOCO", "MAGIC")
_MAX_QUEUE = 64
_WRITE_TIMEOUT = 2.0
# Backlog watchdog. Writes-without-response are queued by Windows and never block, so a link that
# carries fewer frames than we send (a MELK-OA10 manages ~30/s, less while Bluetooth headphones
# share the radio) silently builds seconds of lag. Every few seconds one regular frame goes out
# as an acknowledged write: its round trip waits behind the queue and reveals the backlog.
_PROBE_EVERY = 3.0
_PROBE_LAGGING = 0.35   # s round trip = frames are piling up
_PROBE_HEALTHY = 0.15
_MAX_BACKOFF_INTERVAL = 0.125
_LOGIN_GAP = 0.4


async def _sleep_until(deadline: float) -> None:
    """Sleep until time.perf_counter() >= deadline, accurate to ~1 ms."""
    while True:
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            return
        await asyncio.sleep(remaining if remaining > 0.004 else 0.001)


class BleController:
    def __init__(
        self,
        protocol: MagicLanternProtocol | None = None,
        max_send_rate_hz: float = 30.0,
        write_with_response: bool | None = None,
        scan_timeout: float = 8.0,
        connect_timeout: float = 15.0,
        max_backoff: float = 15.0,
    ) -> None:
        self.protocol = protocol or MagicLanternProtocol()
        self.auto_reconnect = True
        self.scan_timeout = scan_timeout
        self.connect_timeout = connect_timeout
        self.max_backoff = max_backoff
        self.write_with_response = write_with_response  # None = pick from characteristic properties
        self._min_interval = 1.0 / max(1.0, max_send_rate_hz)
        self._backoff_interval = 0.0  # extra spacing set by the backlog watchdog

        self._status_listeners: list[Callable[[ConnectionStatus], None]] = []
        self._notify_listeners: list[Callable[[bytes], None]] = []
        self._status = ConnectionStatus(ConnectionState.DISCONNECTED)

        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()

        # Everything below is only touched from the BLE loop thread.
        self._queue: OrderedDict[Hashable, bytes] = OrderedDict()
        self._seq = itertools.count()
        self._wake: asyncio.Event | None = None
        self._writer_task: asyncio.Task | None = None
        self._conn_task: asyncio.Task | None = None
        self._client: BleakClient | None = None
        self._write_char = None
        self._response = False
        self._address = ""
        self._device_name = ""
        self._want_connected = False
        self.writes_sent = 0

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run_loop, name="ble-loop", daemon=True)
        self._thread.start()
        self._ready.wait(5.0)

    def shutdown(self, timeout: float = 5.0) -> None:
        if self._loop is None:
            return
        with suppress(Exception):
            asyncio.run_coroutine_threadsafe(self._disconnect(), self._loop).result(timeout)
        self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread is not None:
            self._thread.join(timeout)
        self._thread = None
        self._loop = None

    def _run_loop(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._wake = asyncio.Event()
        self._writer_task = loop.create_task(self._writer_supervisor())
        self._ready.set()
        try:
            loop.run_forever()
        finally:
            for task in asyncio.all_tasks(loop):
                task.cancel()
            with suppress(Exception):
                loop.run_until_complete(asyncio.sleep(0.1))
            loop.close()

    # ------------------------------------------------------------------ listeners
    def add_status_listener(self, fn: Callable[[ConnectionStatus], None]) -> None:
        """Called from the BLE thread on every state change. Keep it fast."""
        self._status_listeners.append(fn)

    def add_notify_listener(self, fn: Callable[[bytes], None]) -> None:
        """Called from the BLE thread with raw FFF4 notification payloads."""
        self._notify_listeners.append(fn)

    @property
    def status(self) -> ConnectionStatus:
        return self._status

    @property
    def is_connected(self) -> bool:
        return self._status.state == ConnectionState.CONNECTED

    @property
    def wants_connection(self) -> bool:
        return self._want_connected

    def set_max_send_rate(self, hz: float) -> None:
        self._min_interval = 1.0 / max(1.0, hz)

    # ------------------------------------------------------------------ public API (thread-safe)
    def connect(self, address: str) -> None:
        self._call(self._start_connection, address)

    def disconnect(self) -> None:
        if self._loop is not None:
            asyncio.run_coroutine_threadsafe(self._disconnect(), self._loop)

    def scan(self, timeout: float = 5.0) -> concurrent.futures.Future:
        """Returns a Future resolving to a list[ScanResult]."""
        if self._loop is None:
            raise RuntimeError("BleController.start() was not called")
        return asyncio.run_coroutine_threadsafe(self._scan(timeout), self._loop)

    def send(self, data: bytes, key: Hashable | None = None) -> None:
        """Queue a raw frame. Frames with the same key replace each other (latest wins)."""
        self._call(self._enqueue, key, bytes(data))

    def send_color(self, r: float, g: float, b: float) -> None:
        self.send(self.protocol.color(r, g, b), key="color")

    def send_power(self, on: bool) -> None:
        self.send(self.protocol.power(on), key="power")

    def send_brightness(self, percent: float) -> None:
        self.send(self.protocol.brightness(percent), key="brightness")

    def send_device_effect(self, code: int, speed: float) -> None:
        self.send(self.protocol.effect(code), key="effect")
        self.send(self.protocol.effect_speed(speed), key="effect_speed")

    def _call(self, fn: Callable, *args) -> None:
        if self._loop is not None and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(fn, *args)

    # ------------------------------------------------------------------ loop-thread internals
    def _set_status(self, state: ConnectionState, message: str = "", attempt: int = 0) -> None:
        self._status = ConnectionStatus(state, message, self._address, attempt, self._device_name)
        log.info("%s%s", state.value, f": {message}" if message else "")
        for fn in list(self._status_listeners):
            try:
                fn(self._status)
            except Exception:
                log.exception("status listener failed")

    def _enqueue(self, key: Hashable | None, data: bytes) -> None:
        if key is None:
            key = ("raw", next(self._seq))
        self._queue.pop(key, None)
        self._queue[key] = data
        while len(self._queue) > _MAX_QUEUE:
            self._queue.popitem(last=False)
        self._wake.set()

    async def _writer_supervisor(self) -> None:
        # The writer must never die silently - that would freeze the lights on their last color.
        while True:
            try:
                await self._writer()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("BLE writer crashed - restarting")
                await asyncio.sleep(0.2)

    async def _writer(self) -> None:
        next_due = 0.0
        probe_due = 0.0
        while True:
            await self._wake.wait()
            self._wake.clear()
            while self._queue and self._client is not None and self._client.is_connected:
                # Evenly spaced frames, never a catch-up burst. perf_counter is used because the
                # asyncio clock on Windows/Py3.11 only ticks every ~15.6 ms.
                await _sleep_until(next_due)
                client, char = self._client, self._write_char
                if not self._queue or client is None or char is None:
                    break
                _, data = self._queue.popitem(last=False)
                now = time.perf_counter()
                interval = max(self._min_interval, self._backoff_interval)
                # Stay on a fixed grid while on schedule (wake-ups run ~1 ms late, which would
                # otherwise stretch every period); after idling, restart the grid from now.
                base = next_due if now - next_due < interval * 0.5 else now
                next_due = max(base + interval, now + interval * 0.75)  # never a burst
                probe = not self._response and now >= probe_due
                try:
                    started = time.perf_counter()
                    await asyncio.wait_for(
                        client.write_gatt_char(char, data, response=self._response or probe), _WRITE_TIMEOUT
                    )
                    if probe:
                        self._on_probe(time.perf_counter() - started)
                        probe_due = time.perf_counter() + _PROBE_EVERY
                    self.writes_sent += 1
                    if log.isEnabledFor(logging.DEBUG):
                        log.debug("TX %s", self.protocol.to_hex(data))
                except asyncio.CancelledError:
                    raise
                except asyncio.TimeoutError:
                    if probe:  # a probe stuck behind >2 s of queued frames
                        self._on_probe(_WRITE_TIMEOUT)
                        probe_due = time.perf_counter() + _PROBE_EVERY
                    log.warning("Write timed out")
                except Exception as exc:
                    # A dead link is reported by the disconnect callback; just move on.
                    log.warning("Write failed: %s", exc or exc.__class__.__name__)

    def _on_probe(self, round_trip: float) -> None:
        if round_trip > _PROBE_LAGGING:
            base = max(self._backoff_interval, self._min_interval)
            was_normal = self._backoff_interval <= self._min_interval
            self._backoff_interval = min(_MAX_BACKOFF_INTERVAL, base * 1.4)
            if was_normal:
                log.info("Strip lagging %.0f ms behind - slowing updates to %.0f/s",
                         round_trip * 1000, 1.0 / self._backoff_interval)
        elif round_trip < _PROBE_HEALTHY and self._backoff_interval > 0:
            self._backoff_interval *= 0.85
            if self._backoff_interval <= self._min_interval:
                self._backoff_interval = 0.0
                log.info("Strip caught up - back to %.0f updates/s", 1.0 / self._min_interval)

    @property
    def effective_rate(self) -> float:
        return 1.0 / max(self._min_interval, self._backoff_interval)

    def _start_connection(self, address: str) -> None:
        address = address.strip().upper()
        if not address:
            self._set_status(ConnectionState.ERROR, "No device address set")
            return
        if self._conn_task is not None and not self._conn_task.done():
            if address == self._address:
                return
            self._conn_task.cancel()
        self._address = address
        self._want_connected = True
        self._conn_task = self._loop.create_task(self._connection_loop(address))

    async def _connection_loop(self, address: str) -> None:
        attempt = 0
        try:
            while self._want_connected:
                reason = "Connection lost"
                client: BleakClient | None = None
                try:
                    if attempt == 0:
                        self._set_status(ConnectionState.SCANNING, f"Looking for {address}...")
                    device = await BleakScanner.find_device_by_address(address, timeout=self.scan_timeout)
                    if device is None:
                        raise ConnectionError(
                            f"{address} not found - is the strip powered and disconnected from the phone app?"
                        )
                    label = (device.name or address).strip()
                    self._device_name = label
                    profile = self.protocol.select_profile(device.name)
                    self._set_status(ConnectionState.CONNECTING, f"Connecting to {label}...", attempt)

                    lost = asyncio.Event()
                    loop = asyncio.get_running_loop()
                    client = BleakClient(
                        device,
                        disconnected_callback=lambda _c: loop.call_soon_threadsafe(lost.set),
                        timeout=self.connect_timeout,
                    )
                    await client.connect()
                    self._write_char = self._resolve_write_char(client)
                    await self._login(client)
                    await self._try_start_notify(client)
                    # Never request Windows "preferred connection parameters" (low-latency link):
                    # a MELK-OA10 stays connected and acknowledges writes, but stops applying
                    # them (verified with tools/app_test.py).
                    log.info("Device profile: %s", profile.label)

                    self._queue.clear()  # stale frames; listeners re-send full state on CONNECTED
                    self._client = client
                    attempt = 0
                    self._set_status(ConnectionState.CONNECTED, f"Connected to {label}")
                    self._wake.set()
                    await lost.wait()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    reason = str(exc) or exc.__class__.__name__
                    log.warning("Connection attempt failed: %s", reason)
                finally:
                    self._client = None
                    self._write_char = None
                    if client is not None:
                        with suppress(Exception):
                            await asyncio.wait_for(client.disconnect(), 5.0)

                if not self._want_connected:
                    break
                if not self.auto_reconnect:
                    self._want_connected = False
                    self._set_status(ConnectionState.ERROR, reason)
                    break
                attempt += 1
                # Windows needs a moment to tear down the old GATT session before a retry.
                delay = min(self.max_backoff, 2.0 * 2 ** min(attempt - 1, 3))
                self._set_status(
                    ConnectionState.RECONNECTING,
                    f"{reason} - retrying in {delay:.0f}s (attempt {attempt})",
                    attempt,
                )
                await asyncio.sleep(delay)
        except asyncio.CancelledError:
            pass

    def _resolve_write_char(self, client: BleakClient):
        char = client.services.get_characteristic(self.protocol.write_uuid)
        if char is None:
            raise ConnectionError(f"Write characteristic {self.protocol.write_uuid} not found on device")
        if self.write_with_response is None:
            # Without-response is much faster and is what these strips expect.
            self._response = "write-without-response" not in char.properties
        else:
            self._response = self.write_with_response
        log.info("Write char %s props=%s response=%s", char.uuid, char.properties, self._response)
        return char

    async def _login(self, client: BleakClient) -> None:
        for frame in self.protocol.login():
            await asyncio.wait_for(
                client.write_gatt_char(self._write_char, frame, response=False), _WRITE_TIMEOUT
            )
            await asyncio.sleep(_LOGIN_GAP)

    async def _try_start_notify(self, client: BleakClient) -> None:
        char = client.services.get_characteristic(self.protocol.notify_uuid)
        if char is None:
            return
        try:
            await asyncio.wait_for(client.start_notify(char, self._on_notify), 3.0)
        except Exception as exc:  # optional - never fatal
            log.info("Notifications unavailable: %s", exc)

    def _on_notify(self, _char, data: bytearray) -> None:
        payload = bytes(data)
        log.debug("RX %s", self.protocol.to_hex(payload))
        for fn in list(self._notify_listeners):
            try:
                fn(payload)
            except Exception:
                log.exception("notify listener failed")

    async def _disconnect(self) -> None:
        self._want_connected = False
        task = self._conn_task
        self._conn_task = None
        if task is not None and not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await task
        self._queue.clear()
        if self._status.state != ConnectionState.DISCONNECTED:
            self._set_status(ConnectionState.DISCONNECTED, "Disconnected")

    async def _scan(self, timeout: float) -> list[ScanResult]:
        found = await BleakScanner.discover(timeout=timeout, return_adv=True)
        results: list[ScanResult] = []
        service = self.protocol.service_uuid.lower()
        for device, adv in found.values():
            name = device.name or adv.local_name or ""
            likely = service in (u.lower() for u in adv.service_uuids) or name.upper().startswith(
                _STRIP_NAME_PREFIXES
            )
            results.append(ScanResult(name, device.address, adv.rssi, likely))
        results.sort(key=lambda r: (not r.likely_strip, -r.rssi))
        return results
