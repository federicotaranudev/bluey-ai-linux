"""Desktop safety and geometry tests; never generate real desktop input."""

import base64
import glob
import importlib.util
import os
import sys
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from bluey import backend
from bluey.backend import BackendError, CancelledError, DesktopBackend, Snapshot, normalize_keys, validate_url


class FakeInput:
    def __init__(self, cancel=None, stop_on=None):
        self.events = []
        self.cancel = cancel
        self.stop_on = stop_on

    def event(self, kind, *values):
        self.events.append((kind, *values))
        if kind == self.stop_on:
            self.cancel.set()

    def position(self):
        return (37, 49)

    def move(self, *point):
        self.event("move", *point)

    def restore(self, *point):
        self.event("restore", *point)

    def button_down(self, button):
        self.event("button_down", button)

    def button_up(self, button):
        self.event("button_up", button)

    def key_down(self, key):
        self.event("key_down", key)

    def key_up(self, key):
        self.event("key_up", key)

    def char_down(self, char):
        self.event("char_down", char)

    def char_up(self, char):
        self.event("char_up", char)

    def scroll(self, dx, dy):
        self.event("scroll", dx, dy)


class FakeState:
    def __init__(self, states=()):
        self.states = set(states)

    def contains(self, flag):
        return flag in self.states


class FakeRect:
    def __init__(self, x, y, width, height):
        self.x, self.y, self.width, self.height = x, y, width, height


class FakeComponent:
    def __init__(self, rect):
        self.rect = rect

    def getExtents(self, coords):
        return self.rect


class FakeAccessible:
    def __init__(self, name="", role="fill", states=(), rect=None, children=(), description=""):
        self.name = name
        self.role = role
        self.state = FakeState(states)
        self.rect = rect
        self.children = list(children)
        self.description = description

    def getState(self):
        return self.state

    def getRoleName(self):
        return self.role

    def queryComponent(self):
        if self.rect is None:
            raise RuntimeError("no component interface")
        return FakeComponent(self.rect)

    @property
    def childCount(self):
        return len(self.children)

    def getChildAtIndex(self, index):
        return self.children[index] if 0 <= index < len(self.children) else None


def fake_atspi(apps):
    """A pyatspi stand-in: STATE_ACTIVE=1, STATE_SHOWING=2, STATE_DEFUNCT=3."""
    desktop = SimpleNamespace(childCount=len(apps),
                              getChildAtIndex=lambda index: apps[index] if 0 <= index < len(apps) else None)
    return SimpleNamespace(Registry=SimpleNamespace(getDesktop=lambda index: desktop),
                           STATE_ACTIVE=1, STATE_SHOWING=2, STATE_DEFUNCT=3, DESKTOP_COORDS=4)


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {"DISPLAY": ":fake", "XDG_SESSION_TYPE": "x11"}, clear=True)
        self.environment.start()
        self.platform = patch("bluey.backend.sys.platform", "linux")
        self.platform.start()
        self.addCleanup(self.environment.stop)
        self.addCleanup(self.platform.stop)
        self.backend = DesktopBackend()
        self.cancel = threading.Event()
        self.driver = FakeInput(self.cancel)
        self.backend._driver = self.driver
        self.geometry = patch.object(self.backend, "bounds", return_value=(-1920, 120, 1920, 1080))
        self.geometry.start()
        self.addCleanup(self.geometry.stop)

    def perform(self, name, args):
        return self.backend.perform(name, args, self.cancel)

    def test_grid_edges_respect_negative_monitor_origin(self):
        self.assertEqual(self.backend.resolve({"x": 0, "y": 0}), (-1920, 120))
        self.assertEqual(self.backend.resolve({"x": 1000, "y": 1000}), (-1, 1199))
        self.assertEqual(self.backend.resolve({"x": 500, "y": 500}), (-960, 660))

    def test_bad_coordinates_are_rejected_instead_of_clicking_another_display(self):
        for value in [-1, 1001, float("nan"), float("inf"), True, "500", None]:
            with self.subTest(value=value), self.assertRaises(BackendError):
                self.backend.resolve({"x": value, "y": 200})

    def test_targets_use_snapshot_pixels_and_accept_case(self):
        self.backend._snapshot = Snapshot("", "", {"W2": (-1500, 170)})
        self.backend._snapshot_bounds = (-1920, 120, 1920, 1080)
        self.assertEqual(self.backend.resolve({"target_id": " w2 "}), (-1500, 170))
        with self.assertRaises(BackendError):
            self.backend.resolve({"target_id": "W3", "x": 500, "y": 500})

    def test_changed_monitor_layout_invalidates_targets(self):
        self.backend._snapshot = Snapshot("", "", {"W2": (100, 170)})
        self.backend._snapshot_bounds = (0, 0, 1920, 1080)
        with self.assertRaisesRegex(BackendError, "layout changed"):
            self.backend.resolve({"target_id": "W2"})
        self.assertIsNone(self.backend._snapshot)

    def test_click_restores_user_pointer(self):
        self.perform("click", {"x": 300, "y": 200, "right": True, "double": True})
        self.assertEqual(self.driver.events.count(("button_down", "right")), 2)
        self.assertEqual(self.driver.events.count(("button_up", "right")), 2)
        self.assertEqual(self.driver.events[-1], ("restore", 37, 49))

    def test_cancelled_before_action_never_moves(self):
        self.cancel.set()
        with self.assertRaises(CancelledError):
            self.perform("click", {"x": 300, "y": 200})
        self.assertEqual(self.driver.events, [])

    def test_cancel_during_click_releases_button_and_restores_pointer(self):
        self.driver.stop_on = "button_down"
        with self.assertRaises(CancelledError):
            self.perform("click", {"x": 300, "y": 200, "double": True})
        self.assertEqual(self.driver.events[-2:], [("button_up", "left"), ("restore", 37, 49)])
        self.assertEqual(self.driver.events.count(("button_down", "left")), 1)

    def test_cancel_after_move_does_not_click(self):
        self.driver.stop_on = "move"
        with self.assertRaises(CancelledError):
            self.perform("click", {"x": 300, "y": 200})
        self.assertFalse(any(event[0] == "button_down" for event in self.driver.events))
        self.assertEqual(self.driver.events[-1], ("restore", 37, 49))

    def test_drag_cancellation_releases_button(self):
        self.driver.stop_on = "button_down"
        with self.assertRaises(CancelledError):
            self.perform("drag", {"from_x": 50, "from_y": 60, "to_x": 800, "to_y": 900})
        self.assertEqual(self.driver.events[-2:], [("button_up", "left"), ("restore", 37, 49)])

    def test_drag_finishes_at_requested_end_then_restores(self):
        with patch("bluey.backend._pause"):
            self.perform("drag", {"from_x": 0, "from_y": 0, "to_x": 1000, "to_y": 1000})
        self.assertEqual(self.driver.events[-3:], [("move", -1, 1199), ("button_up", "left"), ("restore", 37, 49)])

    def test_scroll_can_cancel_between_steps(self):
        self.driver.stop_on = "scroll"
        with self.assertRaises(CancelledError):
            self.perform("scroll", {"direction": "right", "amount": 10})
        self.assertEqual(self.driver.events.count(("scroll", 1, 0)), 1)
        self.assertEqual(self.driver.events[-1], ("restore", 37, 49))

    def test_cancelled_key_chord_releases_partial_modifiers(self):
        self.driver.stop_on = "key_down"
        with self.assertRaises(CancelledError):
            self.perform("press_keys", {"keys": "control+shift+t"})
        self.assertEqual(self.driver.events, [("key_down", "ctrl"), ("key_up", "ctrl")])

    def test_unknown_password_status_blocks_text_and_paste(self):
        for secure in (None, True):
            with patch.object(self.backend, "_focus_is_secure", return_value=secure):
                for name, args in [("type_text", {"text": "secret"}),
                                   ("press_keys", {"keys": "CTRL+V"}),
                                   ("press_keys", {"keys": "v+control+shift"}),
                                   ("press_keys", {"keys": "insert+shift"}),
                                   ("press_keys", {"keys": "a"})]:
                    with self.subTest(secure=secure, args=args), self.assertRaises(BackendError):
                        self.perform(name, args)
        self.assertEqual(self.driver.events, [])

    def test_middle_click_cannot_bypass_paste_guard(self):
        with self.assertRaises(BackendError):
            self.perform("click", {"x": 0, "y": 0, "button": "middle"})
        self.assertFalse(any(event[0] == "button_down" for event in self.driver.events))

    def test_unicode_typing_and_return_use_keyboard_not_clipboard(self):
        with patch.object(self.backend, "_focus_is_secure", return_value=False), patch("bluey.backend._pause"):
            self.perform("type_text", {"text": "hé😊", "press_return": True})
        self.assertEqual(self.driver.events, [("char_down", "h"), ("char_up", "h"),
                                             ("char_down", "é"), ("char_up", "é"),
                                             ("char_down", "😊"), ("char_up", "😊"),
                                             ("key_down", "enter"), ("key_up", "enter")])

    def test_focus_change_after_tab_stops_before_password_characters(self):
        # Initial field check, before 'a', before Tab, then password field.
        with patch.object(self.backend, "_focus_is_secure", side_effect=[False, False, False, True]), patch("bluey.backend._pause"):
            with self.assertRaisesRegex(BackendError, "password"):
                self.perform("type_text", {"text": "a\tsecret"})
        self.assertEqual(self.driver.events, [("char_down", "a"), ("char_up", "a"),
                                             ("key_down", "tab"), ("key_up", "tab")])

    def test_cancel_during_unicode_typing_releases_character(self):
        self.driver.stop_on = "char_down"
        with patch.object(self.backend, "_focus_is_secure", return_value=False):
            with self.assertRaises(CancelledError):
                self.perform("type_text", {"text": "hello"})
        self.assertEqual(self.driver.events, [("char_down", "h"), ("char_up", "h")])

    def test_wayland_is_not_mistaken_for_x11_when_display_exists(self):
        with patch.dict(os.environ, {"WAYLAND_DISPLAY": "wayland-0"}):
            self.assertFalse(self.backend.capabilities()["control"])
            with self.assertRaisesRegex(BackendError, "Wayland"):
                self.perform("open_app", {"name": "calculator"})

    def test_capabilities_work_without_display_or_gui_imports(self):
        with patch.dict(os.environ, {}, clear=True):
            capabilities = self.backend.capabilities()
        self.assertFalse(capabilities["capture"])
        self.assertFalse(capabilities["control"])
        self.assertIn("No desktop display", capabilities["messages"][0])

    def test_app_names_never_become_commands_or_flags(self):
        with patch("bluey.backend.subprocess.Popen") as launch:
            for name in ["bash", "cmd", "powershell", "python", "firefox --remote-debugging-port=1234", "calculator; touch /tmp/no", "/usr/bin/firefox", "$(whoami)"]:
                with self.subTest(name=name), self.assertRaises(BackendError):
                    self.perform("open_app", {"name": name})
        launch.assert_not_called()

    def test_known_app_launch_uses_one_executable_without_shell(self):
        with patch("bluey.backend.shutil.which", return_value="/usr/bin/gnome-calculator"), patch("bluey.backend.subprocess.Popen") as launch:
            self.perform("open_app", {"name": "calculator"})
        self.assertEqual(launch.call_args.args[0], ["/usr/bin/gnome-calculator"])
        self.assertNotIn("shell", launch.call_args.kwargs)

    def test_url_handler_receives_one_argument_without_shell(self):
        address = "https://example.org/search?q=a&other=b;done=true"
        with patch("bluey.backend.shutil.which", return_value="/usr/bin/xdg-open"), patch("bluey.backend.subprocess.Popen") as launch:
            self.perform("open_url", {"url": address})
        self.assertEqual(launch.call_args.args[0], ["/usr/bin/xdg-open", address])
        self.assertNotIn("shell", launch.call_args.kwargs)

    def test_cancelled_open_url_never_launches(self):
        self.cancel.set()
        with patch("bluey.backend.subprocess.Popen") as launch:
            with self.assertRaises(CancelledError):
                self.perform("open_url", {"url": "https://example.org"})
        launch.assert_not_called()

    def test_old_mss_uses_declared_x11_primary_instead_of_first_monitor(self):
        capture = SimpleNamespace(monitors=[{}, {"left": 0, "top": 0, "width": 1920, "height": 1080},
                                           {"left": -1280, "top": 0, "width": 1280, "height": 720}])
        with patch("bluey.backend.shutil.which", return_value="/usr/bin/xrandr"), patch("bluey.backend.subprocess.run", return_value=SimpleNamespace(stdout="DP-2 connected primary 1280x720-1280+0 (normal)\n")):
            self.assertEqual(self.backend._monitor(capture), {"left": -1280, "top": 0, "width": 1280, "height": 720})

    @unittest.skipUnless(importlib.util.find_spec("PIL"), "Pillow is not installed")
    def test_capture_without_ocr_returns_real_jpeg_and_grid(self):
        monitor = {"left": -100, "top": 20, "width": 100, "height": 80}
        raw = SimpleNamespace(size=(100, 80), rgb=b"\x80\x90\xa0" * 8000)
        class Capture:
            monitors = [{}, monitor]
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def grab(self, region):
                self.region = region
                return raw
        with patch.dict(sys.modules, {"mss": SimpleNamespace(mss=Capture)}), patch("bluey.backend._module_available", return_value=False):
            snapshot = self.backend.capture()
        self.assertTrue(base64.b64decode(snapshot.jpeg_base64).startswith(b"\xff\xd8"))
        self.assertIn("OCR unavailable", snapshot.text)
        self.assertEqual(snapshot.targets, {})
        self.assertEqual(self.backend._snapshot_bounds, (-100, 20, 100, 80))

    def test_ocr_targets_include_monitor_origin(self):
        data = {"text": ["hello", "world"], "conf": [95, 90], "left": [10, 40], "top": [20, 20],
                "width": [20, 30], "height": [10, 10], "block_num": [1, 1], "par_num": [1, 1], "line_num": [1, 1]}
        fake_ocr = SimpleNamespace(Output=SimpleNamespace(DICT="dict"), image_to_data=lambda *args, **kwargs: data)
        with patch.dict(sys.modules, {"pytesseract": fake_ocr}), patch("bluey.backend._module_available", return_value=True), patch("bluey.backend.shutil.which", return_value="/usr/bin/tesseract"):
            text, targets = self.backend._ocr(None, (-100, 50, 100, 100))
        self.assertEqual(targets, {"W1": (-80, 75), "W2": (-45, 75), "L1": (-60, 75)})
        self.assertIn("L1 @400,250 hello world", text)

    def test_ocr_lead_lines_sit_between_header_and_text_targets(self):
        with patch("bluey.backend._module_available", return_value=False):
            text, _ = self.backend._ocr(None, (0, 0, 100, 100), lead=["Frontmost app: Firefox"])
        lines = text.splitlines()
        self.assertTrue(lines[0].startswith("Primary screen."))
        self.assertEqual(lines[1], "Frontmost app: Firefox")
        self.assertTrue(any("OCR unavailable" in line for line in lines[2:]))

    def test_controls_reader_names_the_active_app_and_orders_controls(self):
        save = FakeAccessible("Save", "push button", {2}, FakeRect(100, 200, 60, 30))
        icon = FakeAccessible("", "push button", {2}, FakeRect(300, 200, 24, 24))
        field = FakeAccessible("", "entry", {2}, FakeRect(400, 240, 120, 24))
        cancel = FakeAccessible("Cancel", "push button", {2}, FakeRect(50, 100, 60, 30))
        window = FakeAccessible("Editor", "frame", {2}, FakeRect(0, 0, 1920, 1080), children=[save, icon, field, cancel])
        hidden_child = FakeAccessible("Ghost", "push button", {2}, FakeRect(10, 10, 40, 20))
        hidden_window = FakeAccessible("Hidden", "frame", set(), FakeRect(0, 0, 100, 100), children=[hidden_child])
        app = FakeAccessible("Firefox", "application", {1, 2}, None, children=[hidden_window, window])
        other = FakeAccessible("Terminal", "application", set(), None, children=[window])
        with patch.dict(sys.modules, {"pyatspi": fake_atspi([other, app])}), patch("bluey.backend._module_available", return_value=True):
            name, controls = self.backend._controls((0, 0, 1920, 1080))
        self.assertEqual(name, "Firefox")
        # Reading order, unlabelled icon buttons skipped, unlabelled fields kept, hidden windows pruned.
        self.assertEqual([(kind, label) for kind, label, *_ in controls],
                         [("button", "Cancel"), ("button", "Save"), ("text field", "")])
        self.assertEqual(controls[0][2:], (50, 100, 60, 30))

    def test_controls_reader_without_pyatspi_returns_nothing(self):
        with patch("bluey.backend._module_available", return_value=False):
            self.assertEqual(self.backend._controls((0, 0, 1920, 1080)), (None, []))

    @unittest.skipUnless(importlib.util.find_spec("PIL"), "Pillow is not installed")
    def test_capture_lists_front_app_and_control_ids(self):
        monitor = {"left": 0, "top": 0, "width": 100, "height": 80}
        raw = SimpleNamespace(size=(100, 80), rgb=b"\x80\x90\xa0" * 8000)
        class Capture:
            monitors = [{}, monitor]
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def grab(self, region):
                return raw
        with patch.dict(sys.modules, {"mss": SimpleNamespace(mss=Capture)}), \
                patch("bluey.backend._module_available", return_value=False), \
                patch.object(self.backend, "_controls", return_value=("Firefox", [("button", "Save", 10, 20, 40, 20)])):
            snapshot = self.backend.capture()
        self.assertIn("Frontmost app: Firefox", snapshot.text)
        self.assertIn("Controls (click these by id):", snapshot.text)
        self.assertIn('C1 button @300,375 "Save"', snapshot.text)
        self.assertIn("OCR unavailable", snapshot.text)
        self.assertIn("The user's mouse pointer is at @", snapshot.text)
        self.assertEqual(snapshot.targets["C1"], (30, 30))



class WaylandDetectionTests(unittest.TestCase):
    """A stale XDG_SESSION_TYPE must not switch everything off after a reboot."""

    def test_a_stale_wayland_variable_is_ignored_when_there_is_no_socket(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.dict(os.environ, {"XDG_SESSION_TYPE": "wayland", "WAYLAND_DISPLAY": "",
                                         "XDG_RUNTIME_DIR": folder, "DISPLAY": ":0.0"}, clear=True):
                self.assertFalse(backend.on_wayland())
                self.assertIsNone(backend.DesktopBackend()._platform_reason())

    def test_a_real_wayland_socket_is_believed_even_if_the_variable_disagrees(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "wayland-0").write_text("")
            with patch.dict(os.environ, {"XDG_SESSION_TYPE": "x11", "XDG_RUNTIME_DIR": folder,
                                         "DISPLAY": ":0.0"}, clear=True):
                self.assertTrue(backend.on_wayland())

    def test_without_a_runtime_directory_the_variables_are_the_only_evidence(self):
        with patch.dict(os.environ, {"XDG_SESSION_TYPE": "wayland", "DISPLAY": ":0.0"}, clear=True):
            self.assertTrue(backend.on_wayland())
        with patch.dict(os.environ, {"XDG_SESSION_TYPE": "x11", "DISPLAY": ":0.0"}, clear=True):
            self.assertFalse(backend.on_wayland())

    def test_what_this_machine_reports_matches_its_own_wayland_socket(self):
        # Bluey must believe whatever the machine actually is, not a stale variable.
        runtime = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
        has_socket = bool(glob.glob(os.path.join(runtime, "wayland-*")))
        self.assertEqual(backend.on_wayland(), has_socket)


class WaylandHintTests(unittest.TestCase):
    def test_it_points_at_the_x11_session_when_the_system_has_one(self):
        with tempfile.TemporaryDirectory() as folder:
            sessions = Path(folder)
            (sessions / "ubuntu.desktop").write_text("")
            self.assertEqual(len(backend.x11_sessions(sessions)), 1)
            with patch("bluey.backend.x11_sessions", return_value=backend.x11_sessions(sessions)):
                self.assertIn("choose an X11 session", backend.wayland_hint())

    def test_it_says_how_to_get_an_x11_session_when_there_is_none(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch("bluey.backend.x11_sessions", return_value=backend.x11_sessions(Path(folder))):
                hint = backend.wayland_hint()
        self.assertIn("sudo apt install xorg xfce4", hint)
        self.assertNotIn("choose an X11 session", hint)

    def test_a_missing_session_directory_is_not_an_error(self):
        self.assertEqual(backend.x11_sessions(Path("/does/not/exist")), [])


class ShortcutAndURLTests(unittest.TestCase):
    def test_aliases_and_modifier_order_are_canonical(self):
        self.assertEqual(normalize_keys(" T + Control + Shift "), ("ctrl", "shift", "t"))
        self.assertEqual(normalize_keys(["option", "left"]), ("alt", "left"))
        self.assertEqual(normalize_keys("return"), ("enter",))

    def test_os_shortcuts_cannot_bypass_guard_with_aliases_or_order(self):
        blocked = ["win+l", "L + Windows", "super+l", "cmd+l", "meta+r", "command+x", "⌘+l",
                   "Del + Alt + Control", "CTRL+ALT+Backspace", "f4+option", "shift+alt+f4",
                   "escape+shift+control", "f1+ctrl+alt", "q+shift+ctrl", "alt+f2", "ctrl+alt+l",
                   "super_l+l", "shift+win+l", "pause", "sleep", "power", "prtsc+alt"]
        for keys in blocked:
            with self.subTest(keys=keys), self.assertRaises(BackendError):
                normalize_keys(keys)

    def test_reject_multiple_keys_unknown_names_and_duplicate_modifiers(self):
        for keys in ["a+b", "ctrl+", "ctrl+control+t", "ctrl", "f99", "", ["ctrl", 2], None]:
            with self.subTest(keys=keys), self.assertRaises(BackendError):
                normalize_keys(keys)

    def test_regular_shortcuts_are_usable(self):
        for keys in ["ctrl+l", "ctrl+t", "ctrl+shift+t", "ctrl+s", "alt+left", "tab", "escape", "f5", "win+left"]:
            self.assertTrue(normalize_keys(keys))

    def test_valid_urls(self):
        self.assertEqual(validate_url("example.org/page"), "https://example.org/page")
        self.assertEqual(validate_url("https://example.org/?q=hello%20world"), "https://example.org/?q=hello%20world")
        self.assertEqual(validate_url("localhost:8080/test"), "https://localhost:8080/test")

    def test_url_protocols_credentials_and_injection_are_rejected(self):
        for url in ["javascript:alert(1)", "file:///etc/passwd", "ms-settings:privacy", "ftp://example.org", "--help",
                    "https://example.org/\" & calc.exe", "https://example.org/`touch`", "https://example.org/\ncalc",
                    "https://user:password@example.org/", "https://example.org:99999", "https://", "C:\\Windows\\cmd.exe"]:
            with self.subTest(url=url), self.assertRaises(BackendError):
                validate_url(url)


if __name__ == "__main__":
    unittest.main()
