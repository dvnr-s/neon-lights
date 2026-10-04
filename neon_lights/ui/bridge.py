"""Marshals callbacks from worker threads (BLE loop, engine, logging) onto the Qt GUI thread."""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, Signal


class UiBridge(QObject):
    status_changed = Signal(object)   # ble.ConnectionStatus
    notification = Signal(bytes)      # raw FFF4 payload
    scan_finished = Signal(object)    # list[ScanResult] or Exception
    engine_error = Signal(str)
    log_message = Signal(str, int)    # text, logging level


class QtLogHandler(logging.Handler):
    def __init__(self, bridge: UiBridge) -> None:
        super().__init__()
        self._bridge = bridge
        self.setFormatter(logging.Formatter("%(asctime)s  %(name)s  %(message)s", "%H:%M:%S"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._bridge.log_message.emit(self.format(record), record.levelno)
        except RuntimeError:
            pass  # bridge already destroyed during shutdown
