"""Main window: connection bar, power/brightness, mode switcher, pages, log dock, shortcuts."""

from __future__ import annotations

import copy
import logging
import re

from PySide6.QtCore import QByteArray, Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDockWidget,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QWidget,
)

from .. import APP_NAME, __version__
from ..ble import BleController, ConnectionState, ConnectionStatus
from ..config import SCENE_KEYS, Settings
from ..engine import LightingEngine, Mode
from . import theme
from .bridge import QtLogHandler, UiBridge
from .calibration import CalibrationDialog, calibration_from_settings
from .scenes import MAX_SHORTCUT_SCENES, SceneBar
from .pages import EffectsPage, ManualPage, MusicPage, ScreenPage
from .widgets import GlowPreview, HBox, LabeledSlider, PowerButton, StatusDot, VBox, card, label, qcolor

log = logging.getLogger("neon.ui")

MAC_RE = re.compile(r"([0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5})")
MODES = [("manual", "Manual"), ("effects", "Effects"), ("screen", "Screen"), ("music", "Music")]
RATE_CHOICES = [(10, ""), (15, ""), (20, "  (recommended)"), (25, ""), (30, "  (maximum)")]

STATE_COLORS = {
    ConnectionState.CONNECTED: theme.OK,
    ConnectionState.SCANNING: theme.WARN,
    ConnectionState.CONNECTING: theme.WARN,
    ConnectionState.RECONNECTING: theme.WARN,
    ConnectionState.ERROR: theme.ERROR,
    ConnectionState.DISCONNECTED: theme.MUTED,
}

SHORTCUTS = [
    ("Ctrl+K", "Connect / disconnect"),
    ("Ctrl+P", "Power on / off"),
    ("Ctrl+Up / Ctrl+Down", "Brightness +10% / −10%"),
    ("Ctrl+1 … Ctrl+4", "Manual / Effects / Screen / Music mode"),
    ("Alt+1 … Alt+9", "Apply preset color 1–9"),
    ("Ctrl+S", "Save current manual color as favorite"),
    ("Ctrl+Shift+1 … 9", "Apply scene 1–9"),
    ("Ctrl+Shift+S", "Save current setup as a scene"),
    ("Ctrl+Shift+C", "Color calibration"),
    ("Ctrl+Right / Ctrl+Left", "Next / previous effect"),
    ("Ctrl+= / Ctrl+−", "Effect speed +10 / −10"),
    ("Ctrl+L", "Show / hide log"),
    ("F1", "This list"),
    ("Ctrl+Q", "Quit"),
]


class MainWindow(QMainWindow):
    def __init__(self, settings: Settings, ble: BleController, engine: LightingEngine) -> None:
        super().__init__()
        self.settings = settings
        self.ble = ble
        self.engine = engine
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(theme.make_app_icon())
        self.resize(1120, 840)
        self.setMinimumSize(760, 520)

        # Worker threads -> GUI thread
        self.bridge = UiBridge(self)
        self._log_handler = QtLogHandler(self.bridge)
        logging.getLogger("neon").addHandler(self._log_handler)
        ble.add_status_listener(self.bridge.status_changed.emit)
        ble.add_notify_listener(self.bridge.notification.emit)
        engine.on_error = self.bridge.engine_error.emit
        self.bridge.status_changed.connect(self._on_status)
        self.bridge.scan_finished.connect(self._on_scan_finished)
        self.bridge.engine_error.connect(self._on_engine_error)
        self.bridge.log_message.connect(self._append_log)
        self.bridge.notification.connect(self._on_notification)

        self._save_timer = QTimer(self, singleShot=True, interval=600, timeout=self.settings.save)
        self._last_writes = 0
        self._calibration_dialog: CalibrationDialog | None = None

        # Pick the protocol profile of the last-used strip so built-in effects are right offline too.
        ble.protocol.select_profile(settings.get("device_name"))
        engine.set_calibration(calibration_from_settings(settings["calibration"]))

        self._build_ui()
        self._build_log_dock()
        self._build_menu()
        self._build_shortcuts()
        self._restore_state()

        self._ui_timer = QTimer(self, interval=40, timeout=self._refresh)
        self._ui_timer.start()
        self._rate_timer = QTimer(self, interval=1000, timeout=self._update_rate)
        self._rate_timer.start()

        # Open screen/audio capture in the background so the first switch to those modes is instant.
        QTimer.singleShot(1500, self.engine.screen.warm_up)
        QTimer.singleShot(1800, self.engine.audio.warm_up)

        if settings["connect_on_start"] and settings["device_address"]:
            QTimer.singleShot(250, self.connect_device)

    # ================================================================== layout
    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        lay = VBox(root, spacing=12, margins=16)

        # --- header
        header = HBox()
        title = label("Neon", "title")
        accent = label("Lights", "titleAccent")
        header.addWidget(title)
        header.addWidget(accent)
        header.addStretch(1)
        self.output_preview = GlowPreview(background=theme.BG)
        self.output_preview.setFixedSize(230, 54)
        self.output_preview.setToolTip("What the strip is showing right now")
        header.addWidget(self.output_preview)
        lay.addLayout(header)

        # --- connection
        conn_card, cl = card(margins=12)
        row = HBox()
        row.addWidget(label("Device", "muted"))
        self.address = QComboBox()
        self.address.setEditable(True)
        self.address.setMinimumWidth(270)
        self.address.lineEdit().setPlaceholderText("AA:BB:CC:DD:EE:FF")
        row.addWidget(self.address)
        self.scan_btn = QPushButton("Scan")
        self.scan_btn.setToolTip("Search for nearby Bluetooth LED strips")
        self.scan_btn.clicked.connect(self.scan_devices)
        row.addWidget(self.scan_btn)
        self.connect_btn = QPushButton("Connect")
        self.connect_btn.setObjectName("primary")
        self.connect_btn.setMinimumWidth(110)
        self.connect_btn.setToolTip("Connect / disconnect (Ctrl+K)")
        self.connect_btn.clicked.connect(self.toggle_connection)
        row.addWidget(self.connect_btn)
        row.addSpacing(8)
        self.status_dot = StatusDot()
        row.addWidget(self.status_dot)
        self.status_label = QLabel("Disconnected")
        self.status_label.setMinimumWidth(120)
        row.addWidget(self.status_label, 1)
        self.auto_reconnect = QCheckBox("Auto-reconnect")
        self.auto_reconnect.toggled.connect(self._on_auto_reconnect)
        row.addWidget(self.auto_reconnect)
        cl.addLayout(row)
        lay.addWidget(conn_card)

        # --- power + brightness + modes
        ctl = HBox(spacing=14)
        self.power_btn = PowerButton(44)
        self.power_btn.setToolTip("Power (Ctrl+P)")
        self.power_btn.toggled.connect(self._on_power)
        ctl.addWidget(self.power_btn)
        self.brightness = LabeledSlider("Brightness", 1, 100, 100, "%")
        self.brightness.setToolTip("Ctrl+Up / Ctrl+Down")
        self.brightness.valueChanged.connect(self._on_brightness)
        ctl.addWidget(self.brightness, 1)
        ctl.addSpacing(10)
        self.mode_group = QButtonGroup(self)
        self.mode_group.setExclusive(True)
        for i, (_key, text) in enumerate(MODES):
            b = QPushButton(text)
            b.setObjectName("modeButton")
            b.setCheckable(True)
            b.setToolTip(f"{text} mode (Ctrl+{i + 1})")
            self.mode_group.addButton(b, i)
            ctl.addWidget(b)
        self.mode_group.idClicked.connect(self.switch_mode)
        self.mode_group.idClicked.connect(lambda _i: self.scene_bar.set_active(None))
        lay.addLayout(ctl)

        # --- scenes
        self.scene_bar = SceneBar(self.settings, self.snapshot_scene, self.apply_scene)
        self.scene_bar.changed.connect(self._schedule_save)
        self.scene_bar.applied.connect(lambda name: self.statusBar().showMessage(f"Scene '{name}' applied", 4000))
        lay.addWidget(self.scene_bar)

        # --- pages
        self.manual_page = ManualPage(self.engine, self.settings)
        self.effects_page = EffectsPage(self.engine, self.settings)
        self.screen_page = ScreenPage(self.engine, self.settings)
        self.music_page = MusicPage(self.engine, self.settings)
        self.pages = [self.manual_page, self.effects_page, self.screen_page, self.music_page]
        self.stack = QStackedWidget()
        for page in self.pages:
            # Pages scroll instead of squashing when the window is small.
            scroller = QScrollArea()
            scroller.setWidget(page)
            scroller.setWidgetResizable(True)
            scroller.setFrameShape(QScrollArea.NoFrame)
            self.stack.addWidget(scroller)
            page.changed.connect(self._schedule_save)
            page.changed.connect(lambda: self.scene_bar.set_active(None))  # edited -> no longer the scene
        lay.addWidget(self.stack, 1)

        self.rate_label = label("", "muted")
        self.statusBar().addPermanentWidget(self.rate_label)

    def _build_log_dock(self) -> None:
        self.log_dock = QDockWidget("Log and raw commands", self)
        self.log_dock.setObjectName("logDock")
        self.log_dock.setAllowedAreas(Qt.BottomDockWidgetArea)
        body = QWidget()
        body.setObjectName("root")
        lay = VBox(body, spacing=8, margins=8)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(1000)
        self.log_view.setMinimumHeight(70)
        lay.addWidget(self.log_view, 1)
        row = HBox(spacing=8)
        self.raw_input = QLineEdit()
        self.raw_input.setPlaceholderText("Raw hex frame, e.g. 7E 07 05 03 FF 00 00 10 EF")
        self.raw_input.returnPressed.connect(self._send_raw)
        send = QPushButton("Send")
        send.clicked.connect(self._send_raw)
        self.debug_frames = QCheckBox("Log TX/RX frames")
        self.debug_frames.toggled.connect(
            lambda on: logging.getLogger("neon.ble").setLevel(logging.DEBUG if on else logging.INFO)
        )
        row.addWidget(self.raw_input, 1)
        row.addWidget(send)
        row.addWidget(self.debug_frames)
        lay.addLayout(row)
        self.log_dock.setWidget(body)
        self.addDockWidget(Qt.BottomDockWidgetArea, self.log_dock)
        self.log_dock.visibilityChanged.connect(self._on_log_visibility)

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("&File")
        self.connect_on_start_action = QAction("Connect on startup", self, checkable=True)
        self.connect_on_start_action.toggled.connect(lambda on: self._set_setting("connect_on_start", on))
        file_menu.addAction(self.connect_on_start_action)
        file_menu.addSeparator()
        file_menu.addAction(QAction("Quit", self, shortcut=QKeySequence("Ctrl+Q"), triggered=self.close))

        tools = self.menuBar().addMenu("&Tools")
        tools.addAction(QAction("Color calibration…", self, shortcut=QKeySequence("Ctrl+Shift+C"),
                                triggered=self.show_calibration))
        tools.addAction(QAction("Save current setup as scene…", self, shortcut=QKeySequence("Ctrl+Shift+S"),
                                triggered=self.scene_bar.save_new))
        tools.addSeparator()
        rate_menu = tools.addMenu("Update rate")
        self.rate_group = QActionGroup(self)
        current = int(self.settings["max_send_rate_hz"])
        for hz, note in RATE_CHOICES:
            action = QAction(f"{hz} per second{note}", self, checkable=True)
            action.setChecked(hz == current)
            action.triggered.connect(lambda _=False, hz=hz: self._set_rate(hz))
            self.rate_group.addAction(action)
            rate_menu.addAction(action)

        view = self.menuBar().addMenu("&View")
        toggle_log = self.log_dock.toggleViewAction()
        toggle_log.setShortcut(QKeySequence("Ctrl+L"))
        view.addAction(toggle_log)

        help_menu = self.menuBar().addMenu("&Help")
        help_menu.addAction(QAction("Keyboard shortcuts", self, shortcut=QKeySequence("F1"),
                                    triggered=self.show_shortcuts))
        help_menu.addAction(QAction("About", self, triggered=self.show_about))

    def _build_shortcuts(self) -> None:
        def sc(keys: str, fn) -> None:
            QShortcut(QKeySequence(keys), self, activated=fn, context=Qt.ApplicationShortcut)

        sc("Ctrl+K", self.toggle_connection)
        sc("Ctrl+P", lambda: self.power_btn.toggle())
        sc("Ctrl+Up", lambda: self.brightness.setValue(min(100, self.brightness.value() + 10)))
        sc("Ctrl+Down", lambda: self.brightness.setValue(max(1, self.brightness.value() - 10)))
        for i in range(len(MODES)):
            sc(f"Ctrl+{i + 1}", lambda i=i: self.switch_mode(i))
        for i in range(9):
            sc(f"Alt+{i + 1}", lambda i=i: self._preset(i))
        sc("Ctrl+S", self._save_favorite)
        sc("Ctrl+Right", lambda: self._step_effect(1))
        sc("Ctrl+Left", lambda: self._step_effect(-1))
        sc("Ctrl+=", lambda: self.effects_page.nudge_speed(10))
        sc("Ctrl++", lambda: self.effects_page.nudge_speed(10))
        sc("Ctrl+-", lambda: self.effects_page.nudge_speed(-10))
        for i in range(MAX_SHORTCUT_SCENES):
            # Shift+digit is a different key per layout, so bind the digit with both modifiers.
            sc(f"Ctrl+Shift+{i + 1}", lambda i=i: self.scene_bar.apply_index(i))

    def _restore_state(self) -> None:
        s = self.settings
        geometry = s.get("window_geometry")
        if geometry:
            self.restoreGeometry(QByteArray.fromBase64(geometry.encode()))

        known = list(dict.fromkeys([s["device_address"], *s.get("known_devices", [])]))
        self.address.addItems([a for a in known if a])
        self.address.setCurrentText(s["device_address"])

        self.auto_reconnect.setChecked(bool(s["auto_reconnect"]))
        self.ble.auto_reconnect = bool(s["auto_reconnect"])
        self.connect_on_start_action.setChecked(bool(s["connect_on_start"]))

        self.brightness.setValue(int(s["brightness"]))
        self.engine.set_brightness(s["brightness"])
        self.power_btn.setChecked(bool(s["power"]))
        self.engine.set_power(bool(s["power"]))

        self.log_dock.setVisible(bool(s.get("show_log")))
        self.resizeDocks([self.log_dock], [190], Qt.Vertical)
        names = [m[0] for m in MODES]
        self.switch_mode(names.index(s["mode"]) if s["mode"] in names else 0)
        self._on_status(self.ble.status)

    # ================================================================== actions
    def switch_mode(self, index: int) -> None:
        self.mode_group.button(index).setChecked(True)
        self.stack.setCurrentIndex(index)
        self.pages[index].activate()
        self._set_setting("mode", MODES[index][0])

    def connect_device(self) -> None:
        match = MAC_RE.search(self.address.currentText())
        if not match:
            QMessageBox.warning(self, APP_NAME, "Enter a Bluetooth address like BE:69:6E:1A:68:05, or use Scan.")
            return
        address = match.group(1).upper()
        known = self.settings.setdefault("known_devices", [])
        if address not in known:
            known.insert(0, address)
        self._set_setting("device_address", address)
        self.ble.auto_reconnect = self.auto_reconnect.isChecked()
        self.ble.connect(address)

    def toggle_connection(self) -> None:
        if self.ble.wants_connection or self.ble.status.state in (
            ConnectionState.SCANNING, ConnectionState.CONNECTING,
            ConnectionState.CONNECTED, ConnectionState.RECONNECTING,
        ):
            self.ble.disconnect()
        else:
            self.connect_device()

    def scan_devices(self) -> None:
        self.scan_btn.setEnabled(False)
        self.scan_btn.setText("Scanning…")
        self.statusBar().showMessage("Scanning for Bluetooth devices (5 s)…")
        future = self.ble.scan(5.0)
        future.add_done_callback(
            lambda f: self.bridge.scan_finished.emit(f.exception() or f.result())
        )

    def snapshot_scene(self) -> dict:
        state = {k: copy.deepcopy(self.settings[k]) for k in SCENE_KEYS if k in self.settings}
        state["effects_source"] = "device" if self.engine.mode == Mode.DEVICE_EFFECT else "pc"
        return state

    def apply_scene(self, state: dict) -> None:
        for key in SCENE_KEYS:
            if key not in state or key == "effects_source":
                continue
            value = copy.deepcopy(state[key])
            if isinstance(value, dict) and isinstance(self.settings.get(key), dict):
                self.settings[key].update(value)
            else:
                self.settings[key] = value
        if "brightness" in state:
            self.brightness.setValue(int(state["brightness"]))
        if not self.power_btn.isChecked():
            self.power_btn.setChecked(True)
        for page in self.pages:
            page.reload()
        names = [m[0] for m in MODES]
        index = names.index(self.settings["mode"]) if self.settings["mode"] in names else 0
        self.switch_mode(index)
        if index == 1 and state.get("effects_source") == "device":
            self.effects_page.play_on_strip()
        self._schedule_save()

    def show_calibration(self) -> None:
        if self._calibration_dialog is None:
            self._calibration_dialog = CalibrationDialog(self.engine, self.settings, self)
            self._calibration_dialog.changed.connect(self._schedule_save)
        self._calibration_dialog.show()
        self._calibration_dialog.raise_()
        self._calibration_dialog.activateWindow()

    def show_shortcuts(self) -> None:
        rows = "".join(
            f"<tr><td style='padding:3px 18px 3px 0'><b>{k}</b></td><td>{d}</td></tr>" for k, d in SHORTCUTS
        )
        QMessageBox.information(self, "Keyboard shortcuts", f"<table>{rows}</table>")

    def show_about(self) -> None:
        QMessageBox.about(
            self, f"About {APP_NAME}",
            f"<b>{APP_NAME}</b> {__version__}<br>Direct Bluetooth control for Magic Lantern / "
            "ELK-BLEDOM LED strips.<br><br>GUI → Lighting engine → Effects / Screen / Music → BLE → FFF3",
        )

    # ================================================================== slots
    def _on_status(self, status: ConnectionStatus) -> None:
        if status.state == ConnectionState.CONNECTED and status.device_name:
            if status.device_name != self.settings.get("device_name"):
                self._set_setting("device_name", status.device_name)
            self.effects_page.reload_device_effects()
        self.status_dot.setColor(STATE_COLORS.get(status.state, theme.MUTED))
        connected_to = f" · {status.device_name}" if status.state == ConnectionState.CONNECTED else ""
        self.status_label.setText(status.state.value + connected_to)
        self.status_label.setToolTip(status.message)
        active = self.ble.wants_connection or status.state in (
            ConnectionState.SCANNING, ConnectionState.CONNECTING,
            ConnectionState.CONNECTED, ConnectionState.RECONNECTING,
        )
        self.connect_btn.setText("Disconnect" if active else "Connect")
        self.connect_btn.setObjectName("danger" if active else "primary")
        self.connect_btn.style().unpolish(self.connect_btn)
        self.connect_btn.style().polish(self.connect_btn)
        if status.message:
            self.statusBar().showMessage(status.message, 0 if status.state != ConnectionState.CONNECTED else 5000)

    def _on_scan_finished(self, result) -> None:
        self.scan_btn.setEnabled(True)
        self.scan_btn.setText("Scan")
        if isinstance(result, Exception):
            self.statusBar().showMessage(f"Scan failed: {result}", 8000)
            log.error("Scan failed: %s", result)
            return
        current = self.address.currentText()
        self.address.clear()
        likely = [r for r in result if r.likely_strip]
        for r in result:
            star = "★ " if r.likely_strip else ""
            self.address.addItem(f"{r.address}   {star}{r.name or '(unnamed)'}   {r.rssi} dBm")
        if likely:
            self.address.setCurrentIndex(result.index(likely[0]))
        else:
            self.address.setCurrentText(current)
        self.statusBar().showMessage(
            f"Found {len(result)} device(s), {len(likely)} look like LED strips (★).", 8000
        )

    def _on_engine_error(self, message: str) -> None:
        self.statusBar().showMessage(message, 10000)

    def _on_notification(self, data: bytes) -> None:
        pass  # Hook for future protocol features (state readback etc.); logged at DEBUG by the BLE layer.

    def _append_log(self, text: str, level: int) -> None:
        color = theme.ERROR if level >= logging.ERROR else theme.WARN if level >= logging.WARNING else theme.MUTED
        self.log_view.appendHtml(f"<span style='color:{color}'>{text.replace('<', '&lt;')}</span>")

    def _on_log_visibility(self, visible: bool) -> None:
        if not self.isMinimized() and self.isVisible():
            self._set_setting("show_log", visible)

    def _on_power(self, on: bool) -> None:
        self.engine.set_power(on)
        self.power_btn.setToolTip(f"Power {'on' if on else 'off'} (Ctrl+P)")
        self._set_setting("power", on)

    def _on_brightness(self, value: int) -> None:
        self.engine.set_brightness(value)
        self._set_setting("brightness", value)

    def _set_rate(self, hz: int) -> None:
        self.ble.set_max_send_rate(hz)
        self._set_setting("max_send_rate_hz", hz)
        self.statusBar().showMessage(f"Update rate: {hz} per second", 4000)

    def _on_auto_reconnect(self, on: bool) -> None:
        self.ble.auto_reconnect = on
        self._set_setting("auto_reconnect", on)

    def _preset(self, index: int) -> None:
        if self.stack.currentIndex() != 0:
            self.switch_mode(0)
        self.manual_page.apply_preset(index)

    def _save_favorite(self) -> None:
        if self.stack.currentIndex() != 0:
            self.switch_mode(0)
        self.manual_page.save_favorite()

    def _step_effect(self, delta: int) -> None:
        if self.stack.currentIndex() != 1:
            self.switch_mode(1)
        self.effects_page.step_effect(delta)

    def _send_raw(self) -> None:
        text = self.raw_input.text().strip()
        if not text:
            return
        try:
            data = self.ble.protocol.parse_hex(text)
        except ValueError:
            self.statusBar().showMessage("Invalid hex - use pairs like 7E 07 05 03 FF 00 00 10 EF", 6000)
            return
        if not self.ble.is_connected:
            self.statusBar().showMessage("Not connected", 4000)
            return
        self.ble.send(data)
        log.info("Sent raw: %s", self.ble.protocol.to_hex(data))

    def _refresh(self) -> None:
        if self.isMinimized() or not self.isVisible():
            return  # lights keep running; nothing to draw
        mode = self.engine.mode
        if not self.engine.power:
            self.output_preview.setColor(qcolor((0, 0, 0)), "OFF")
        elif mode == Mode.DEVICE_EFFECT:
            self.output_preview.setColor(qcolor((40, 44, 70)), "On-strip animation")
        else:
            self.output_preview.setColor(qcolor(self.engine.output_color))
        page = self.pages[self.stack.currentIndex()]
        if hasattr(page, "refresh"):
            page.refresh()

    def _update_rate(self) -> None:
        sent = self.ble.writes_sent
        rate, self._last_writes = sent - self._last_writes, sent
        self.rate_label.setText(f"BLE {rate} msg/s" if self.ble.is_connected else "")

    def _set_setting(self, key: str, value) -> None:
        self.settings[key] = value
        self._schedule_save()

    def _schedule_save(self) -> None:
        self._save_timer.start()

    # ================================================================== shutdown
    def closeEvent(self, event) -> None:
        self._ui_timer.stop()
        if self._calibration_dialog is not None:
            self._calibration_dialog.close()
        self.settings["window_geometry"] = bytes(self.saveGeometry().toBase64()).decode()
        self.settings["show_log"] = self.log_dock.isVisible()
        self.settings.save()
        logging.getLogger("neon").removeHandler(self._log_handler)
        self.engine.stop()
        self.ble.shutdown()
        super().closeEvent(event)
