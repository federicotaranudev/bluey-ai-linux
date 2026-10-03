"""Qt/main-thread orchestration; network, OCR and tools run off the UI thread."""
from __future__ import annotations

import json
import logging
import platform
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import QObject, QPointF, QTimer, Signal, Slot
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from .backend import DesktopBackend
from .protocol import PhoneServer
from .realtime import ACTION_NAMES, mint_token, research
from .settings import Credentials, Preferences
from .visuals import CursorOverlay, berry_icon
from .window import MainWindow, ReportWindow

log = logging.getLogger(__name__)


class Bridge(QObject):
    packet = Signal(str, object)
    pending = Signal(str, str, str)
    phones = Signal(object)
    disconnected = Signal(str)
    result = Signal(str, object)
    failure = Signal(str)
    ready = Signal(int)
    job_done = Signal()
    point = Signal(float, float)
    caption = Signal(str, float)
    report = Signal(object)
    action = Signal(str)
    capture_visibility = Signal(bool, object)


class DesktopApp(QObject):
    def __init__(self, *, port=8765, smoke=False):
        super().__init__()
        self.smoke = smoke
        self.prefs = Preferences() if smoke else Preferences.load()
        self.credentials = Credentials(use_keyring=not smoke)
        self.backend = DesktopBackend()
        self.bridge = Bridge()
        self.window = MainWindow(self.prefs)
        self.overlay = CursorOverlay(self.prefs)
        self.reports = ReportWindow()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="bluey-tools")
        self.network_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="bluey-network")
        self.cancel = threading.Event()
        self.control_enabled = False
        self.approved_peer = None
        self.approval_boxes = {}
        self.jobs = 0
        self.quitting = False
        self.awake = False
        self.hotkeys = None
        self.tray = None
        self.last_token = {}
        self.server = PhoneServer(socket.gethostname(), self.bridge.packet.emit,
                                  self.bridge.pending.emit, self.bridge.phones.emit,
                                  port=port, on_disconnect=self.bridge.disconnected.emit)
        self.bridge.packet.connect(self.handle_packet)
        self.bridge.pending.connect(self.pending_phone)
        self.bridge.phones.connect(self.phones_changed)
        self.bridge.disconnected.connect(self.disconnected)
        self.bridge.result.connect(self.send_result)
        self.bridge.failure.connect(self.show_error)
        self.bridge.ready.connect(self.server_ready)
        self.bridge.job_done.connect(self.job_done)
        self.bridge.point.connect(self.point_physical)
        self.bridge.caption.connect(self.overlay.speak)
        self.bridge.report.connect(self.reports.show_report)
        self.bridge.action.connect(self.action)
        self.bridge.capture_visibility.connect(self.capture_visibility)
        self.window.action.connect(self.action)
        self.window.save_key.connect(self.save_key)
        self.window.remove_key.connect(self.remove_key)
        self.window.preferences_changed.connect(self.save_preferences)
        self.window.control_changed.connect(self.set_control)
        self.face_timer = QTimer(self)
        self.face_timer.timeout.connect(lambda: self.server.broadcast({"face": self.overlay.face()}))
        self.face_timer.start(50)
        self.refresh_key_status()
        self.window.show()
        if smoke:
            self.window.network.setText("Preview mode · no network or API calls")
            self.window.diagnostics.setText("Desktop smoke test")
        else:
            self.setup_tray()
            self.setup_hotkeys()
            self.refresh_capabilities()
            self.overlay.show()
            self.network_executor.submit(self.start_server)

    def start_server(self):
        try:
            self.bridge.ready.emit(self.server.start())
        except Exception as exc:
            self.bridge.failure.emit(f"Phone discovery could not start: {exc}. Close other Bluey instances and check your network.")

    @Slot(int)
    def server_ready(self, port):
        self.window.network.setText(f"Discoverable as {self.server.name} · TCP {port}")

    def setup_tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(berry_icon(), self.window)
        self.tray.setToolTip("Bluey · Desktop companion")
        menu = QMenu()
        for title, action in (("Open Bluey", "show"), ("Wake / sleep", "wake"), ("Point here", "point"), ("Go home", "home"), ("Stop actions · Ctrl+Alt+S", "stop"), ("Quit", "quit")):
            menu.addAction(title, lambda action=action: self.action(action))
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason: self.action("show") if reason == QSystemTrayIcon.ActivationReason.Trigger else None)
        self.tray.show()
        self.window.has_tray = True

    def setup_hotkeys(self):
        import os
        if platform.system() == "Linux" and os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland":
            return
        try:
            from pynput.keyboard import GlobalHotKeys
            mapping = {"p": "point", "d": "home", "f": "follow", "h": "hide", "t": "demo", "s": "stop", "<space>": "wake"}
            self.hotkeys = GlobalHotKeys({f"<ctrl>+<alt>+{key}": lambda action=action: self.bridge.action.emit(action) for key, action in mapping.items()})
            self.hotkeys.start()
        except Exception:
            self.window.show_notice("Global shortcuts are unavailable. Use the window or tray controls.")

    def refresh_capabilities(self):
        caps = self.backend.capabilities()
        messages = caps.get("messages", [])
        if isinstance(messages, str):
            messages = [messages]
        self.window.diagnostics.setText("\n".join(messages) or "Primary display · local phone discovery · optional Tesseract OCR")
        import os
        if platform.system() == "Linux" and os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland":
            self.window.show_notice("Wayland session detected. For screen reading, the overlay and computer control, sign out and choose “Ubuntu on Xorg” at login. Phone pairing still works here.")
            self.window.control.setEnabled(False)

    @Slot(str, str, str)
    def pending_phone(self, peer_id, name, address):
        if self.quitting or self.approved_peer is not None or self.approval_boxes:
            self.server.reject(peer_id)
            return
        box = QMessageBox(self.window)
        box.setWindowTitle("Connect an iPhone")
        box.setText(f"Allow {name[:100]} to connect?")
        box.setInformativeText(f"Address: {address}\n\nThis phone can request screenshots and voice sessions, and control the computer when you enable it. Allow it only if this is your phone on a trusted network.")
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        box.setDefaultButton(QMessageBox.StandardButton.No)
        self.approval_boxes[peer_id] = box
        def decided(result):
            self.approval_boxes.pop(peer_id, None)
            if result == QMessageBox.StandardButton.Yes and not self.quitting and self.server.accept(peer_id):
                self.approved_peer = peer_id
                self.cancel = threading.Event()
            else:
                self.server.reject(peer_id)
            box.deleteLater()
        box.finished.connect(decided)
        self.window.show()
        self.window.raise_()
        box.open()

    @Slot(object)
    def phones_changed(self, names):
        self.window.set_phones(names)
        if not names:
            self.set_awake(False)

    @Slot(str)
    def disconnected(self, peer_id):
        box = self.approval_boxes.pop(peer_id, None)
        if box:
            box.reject()
        self.last_token.pop(peer_id, None)
        if peer_id == self.approved_peer:
            self.approved_peer = None
            self.stop_actions(show_caption=False)

    def set_awake(self, enabled):
        self.awake = enabled
        self.overlay.awake = enabled
        self.window.wake.setText("Put Bluey to sleep" if enabled else "Wake Bluey")
        if not enabled:
            self.overlay.home()
            self.overlay.speak("")

    @Slot(str, object)
    def handle_packet(self, peer, packet):
        if not self.server.is_approved(peer) or self.quitting:
            return
        command = packet.get("command")
        if command == "awake":
            self.set_awake(True)
        elif command == "asleep":
            self.cancel.set()
            self.cancel = threading.Event()
            self.set_awake(False)
        elif command in {"caption", "captionDone"}:
            text = packet.get("text", "")
            if isinstance(text, str):
                duration = min(15, max(5, len(text.split()) * .32 + 4)) if command == "captionDone" else 0
                self.overlay.speak(text, duration)
                self.window.latest.setText(text[:600] or "Listening…")
        elif command in {"tool", "realtimeToken"}:
            call_id = packet.get("callID")
            if not isinstance(call_id, str) or not 1 <= len(call_id) <= 200:
                return
            reply_command = "toolResult" if command == "tool" else "realtimeToken"
            if self.jobs >= 8:
                self.server.send(peer, {"command": reply_command, "callID": call_id, "text": "Busy. Wait for the previous request." if command == "tool" else None})
                return
            if command == "realtimeToken":
                now = time.monotonic()
                if now - self.last_token.get(peer, -100) < 3:
                    self.server.send(peer, {"command": "realtimeToken", "callID": call_id})
                    return
                self.last_token[peer] = now
            cancel = self.cancel
            key = self.credentials.get()
            control = self.control_enabled
            personality = self.prefs.personality
            self.jobs += 1
            self.executor.submit(self.run_request, peer, packet, cancel, key, control, personality)

    def run_request(self, peer, packet, cancel, key, control, personality):
        result = {"command": "toolResult" if packet["command"] == "tool" else "realtimeToken", "callID": packet["callID"]}
        try:
            if cancel.is_set() or not self.server.is_approved(peer):
                raise RuntimeError("Request cancelled. Ask again when ready.")
            if packet["command"] == "realtimeToken":
                result["text"] = mint_token(key, personality, control, platform.system())
            else:
                raw = packet.get("text", "{}")
                if not isinstance(raw, str) or len(raw) > 65536:
                    raise ValueError("Tool arguments are too large.")
                args = json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Invalid number")))
                if not isinstance(args, dict):
                    raise ValueError("Tool arguments must be a JSON object.")
                text, image = self.run_tool(packet.get("tool", ""), args, cancel, key, control)
                result["text"] = text
                if image:
                    result["image"] = image
        except Exception as exc:
            message = str(exc)[:600] or "The request could not be completed."
            if key:
                message = message.replace(key, "[redacted]")
            if packet["command"] == "tool":
                result["text"] = message
            self.bridge.failure.emit(message)
        finally:
            if cancel.is_set():
                result.pop("image", None)
                result["text"] = "Request cancelled." if packet["command"] == "tool" else None
            self.bridge.result.emit(peer, result)
            self.bridge.job_done.emit()

    def capture(self, cancel):
        ready = threading.Event()
        self.bridge.capture_visibility.emit(True, ready)
        try:
            if not ready.wait(3) or cancel.is_set():
                raise RuntimeError("Screen capture cancelled.")
            snapshot = self.backend.capture()
            if cancel.is_set():
                raise RuntimeError("Screen capture cancelled.")
            return snapshot.text, snapshot.jpeg_base64
        finally:
            self.bridge.capture_visibility.emit(False, None)

    @Slot(bool, object)
    def capture_visibility(self, hidden, ready):
        self.overlay.suspended = hidden
        if hidden:
            self.overlay.hide()
            QTimer.singleShot(90, ready.set)
        elif not self.quitting and not self.smoke:
            self.overlay.show()

    def run_tool(self, name, args, cancel, key, control):
        if name == "look_at_screen":
            return self.capture(cancel)
        if name in {"point_at", "point_at_spot"}:
            point = self.backend.resolve(args)
            self.bridge.point.emit(*point)
            return "Pointing there.", None
        if name == "stop_pointing":
            self.bridge.action.emit("home")
            return "Heading home.", None
        if name == "go_to_sleep":
            self.bridge.action.emit("sleep_after_reply")
            return "Going to sleep. Say a very short goodbye.", None
        if name == "web_research":
            question = args.get("question", "")
            if not isinstance(question, str) or not question.strip() or len(question) > 4000:
                raise ValueError("Provide a short research question.")
            self.bridge.caption.emit("Doing some research…", 0)
            report = research(key, question, None)
            if cancel.is_set():
                raise RuntimeError("Research cancelled.")
            self.bridge.report.emit(report)
            report_text = "\n\n".join([report.get("title", "Research"), *report.get("paragraphs", [])])
            return "The report is on screen. Reply with one short takeaway.\n---REPORT---\n" + report_text, None
        if name in ACTION_NAMES:
            if not control or not self.control_enabled or cancel.is_set():
                return "Computer control is off. The user can enable it in Desktop Settings.", None
            if name in {"click", "scroll"} and ("target_id" in args or "x" in args):
                self.bridge.point.emit(*self.backend.resolve(args))
                if cancel.wait(.25):
                    raise RuntimeError("Action stopped.")
            result = self.backend.perform(name, args, cancel)
            if cancel.is_set():
                raise RuntimeError("Action stopped.")
            if name == "type_text" and not args.get("press_return"):
                return result, None
            # Capture failure must not encourage the model to repeat a completed action.
            try:
                text, image = self.capture(cancel)
                return f"{result}\nScreen afterwards (target IDs changed):\n{text}", image
            except Exception:
                return f"{result}\nThe action completed, but a follow-up screenshot is unavailable. Do not repeat it automatically.", None
        return f"Unknown tool: {str(name)[:80]}", None

    @Slot(float, float)
    def point_physical(self, x, y):
        try:
            left, top, width, height = self.backend.bounds()
            self.overlay.point((x - left) / width * self.overlay.width(), (y - top) / height * self.overlay.height())
        except Exception as exc:
            self.show_error(str(exc))

    @Slot(str, object)
    def send_result(self, peer, result):
        if not self.quitting:
            self.server.send(peer, result)

    @Slot()
    def job_done(self):
        self.jobs = max(0, self.jobs - 1)

    @Slot(str)
    def show_error(self, message):
        self.window.latest.setText(message)
        self.overlay.speak(message, 8)
        if "discovery" in message:
            self.window.show_notice(message)

    @Slot(bool)
    def set_control(self, enabled):
        self.control_enabled = enabled
        if enabled:
            self.cancel = threading.Event()
        else:
            self.cancel.set()
            self.cancel = threading.Event()
        # Tool definitions are set when the phone opens its session.
        if self.awake:
            self.server.broadcast({"command": "sleep"})
            self.set_awake(False)
            self.window.latest.setText("Computer control updated. Wake Bluey to start a new session.")

    def stop_actions(self, show_caption=True):
        self.cancel.set()
        self.control_enabled = False
        self.window.control.blockSignals(True)
        self.window.control.setChecked(False)
        self.window.control.blockSignals(False)
        self.server.broadcast({"command": "sleep"})
        self.set_awake(False)
        if show_caption:
            self.overlay.speak("Stopped. Computer control is off.", 5)
            self.window.latest.setText("Stopped. Re-enable computer control in Settings when you are ready.")
        # New read-only requests remain possible; queued jobs retain the cancelled event.
        self.cancel = threading.Event()

    @Slot(str)
    def action(self, action):
        if action == "quit":
            QApplication.instance().quit()
        elif action == "show":
            self.window.show()
            self.window.raise_()
            self.window.activateWindow()
        elif action == "wake":
            self.server.broadcast({"command": "sleep" if self.awake else "wake"})
        elif action == "home":
            self.overlay.home()
        elif action == "sleep_after_reply":
            self.overlay.home()
        elif action == "stop":
            self.stop_actions()
        elif action == "point":
            point = self.overlay.mapFromGlobal(QCursor.pos())
            self.overlay.point(point.x(), point.y())
        elif action == "demo":
            self.overlay.point(self.overlay.width() * .64, self.overlay.height() * .4)
            self.overlay.speak("Right here. I've got a point, you know.", 6)
        elif action in {"follow", "hide"}:
            name = "follow_mouse" if action == "follow" else "show_cursor"
            setattr(self.prefs, name, not getattr(self.prefs, name))
            self.overlay.home()
            self.save_preferences()
        elif action == "personality":
            if self.awake:
                self.server.broadcast({"command": "sleep"})
                self.set_awake(False)
            self.window.latest.setText("Personality saved. It will apply next time you wake Bluey.")

    @Slot()
    def save_preferences(self):
        if not self.smoke:
            try:
                self.prefs.save()
            except OSError:
                self.show_error("Could not save preferences. Check that your settings folder is writable.")

    @Slot(str)
    def save_key(self, key):
        try:
            self.credentials.set(key)
            self.window.key.clear()
            self.refresh_key_status()
        except ValueError as exc:
            self.window.key_status.setText(str(exc))

    @Slot()
    def remove_key(self):
        try:
            self.credentials.clear()
            self.stop_actions(show_caption=False)
            self.refresh_key_status()
        except RuntimeError as exc:
            self.window.key_status.setText(str(exc))

    def refresh_key_status(self):
        if self.credentials.get():
            self.window.key_status.setText("API key saved in your system credential vault." if self.credentials.persisted else "API key available for this launch only. Unlock your system credential vault to save it.")
        else:
            self.window.key_status.setText("Add an OpenAI API key for voice and research. Your main key stays on this computer.")

    def shutdown(self):
        if self.quitting:
            return
        self.quitting = True
        self.cancel.set()
        self.face_timer.stop()
        self.overlay.timer.stop()
        if self.hotkeys:
            self.hotkeys.stop()
        self.server.stop()
        if self.tray:
            self.tray.hide()
        self.overlay.close()
        self.reports.close()
        self.executor.shutdown(wait=False, cancel_futures=True)
        self.network_executor.shutdown(wait=False, cancel_futures=True)
