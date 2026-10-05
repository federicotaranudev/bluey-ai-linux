"""Primary-display capture and cancellable Windows / Linux X11 input.

GUI libraries are imported only when used, so importing this module and checking
capabilities works in installers, CI, and an Ubuntu Wayland session. Coordinates
are physical desktop pixels; the UI is responsible for Qt's logical-pixel scale.
"""

from __future__ import annotations

import base64
from collections import deque
from dataclasses import dataclass, field
import importlib.util
import io
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import time
from typing import Any
from urllib.parse import urlsplit


class BackendError(RuntimeError):
    """An actionable desktop capability or input error."""


class CancelledError(BackendError):
    """The user stopped the current action."""


@dataclass(frozen=True)
class Snapshot:
    jpeg_base64: str
    text: str
    targets: dict[str, tuple[int, int]] = field(default_factory=dict)


_MODIFIERS = ("ctrl", "alt", "shift", "win")
_KEY_ALIASES = {
    "control": "ctrl", "ctl": "ctrl", "control_l": "ctrl", "control_r": "ctrl",
    "ctrlleft": "ctrl", "ctrlright": "ctrl", "⌃": "ctrl",
    "option": "alt", "opt": "alt", "altleft": "alt", "altright": "alt", "⌥": "alt",
    "alt_l": "alt", "alt_r": "alt", "shiftleft": "shift", "shiftright": "shift",
    "shift_l": "shift", "shift_r": "shift", "⇧": "shift",
    "cmd": "win", "command": "win", "meta": "win", "super": "win", "windows": "win",
    "winleft": "win", "winright": "win", "super_l": "win", "super_r": "win", "⌘": "win",
    "return": "enter", "esc": "escape", "del": "delete", "forwarddelete": "delete",
    "spacebar": "space", "pgup": "pageup", "pgdn": "pagedown", "ins": "insert",
    "plus": "+", "minus": "-", "comma": ",", "period": ".", "slash": "/",
}
_SPECIAL_KEYS = {
    "enter", "escape", "tab", "space", "backspace", "delete", "insert",
    "home", "end", "pageup", "pagedown", "left", "right", "up", "down",
    *(f"f{number}" for number in range(1, 13)),
}

# AT-SPI role names to the short kinds the model is told to click, mirroring
# ControlsReader.swift on the Mac. Roles missing here are walked past.
_CONTROL_ROLES = {
    "push button": "button",
    "menu button": "menu button",
    "toggle button": "toggle button",
    "check box": "checkbox",
    "radio button": "option",
    "link": "link",
    "entry": "text field",
    "password text": "password field",
    "text": "text area",
    "combo box": "combo box",
    "search field": "search field",
    "slider": "slider",
    "spin button": "stepper",
    "menu item": "menu item",
    "check menu item": "menu item",
    "radio menu item": "menu item",
    "page tab": "tab",
    "expander": "disclosure",
}
# Unlabelled fields are still worth listing; unlabelled icon buttons are not.
_CONTROL_FIELD_KINDS = {"text field", "text area", "combo box", "search field", "password field"}


def normalize_keys(value: str | list[str]) -> tuple[str, ...]:
    """Validate one chord and normalize aliases before checking OS shortcuts."""
    if isinstance(value, str):
        parts = value.split("+")
    elif isinstance(value, list) and all(isinstance(part, str) for part in value):
        parts = value
    else:
        raise BackendError("Provide a key or one shortcut, such as ctrl+t.")
    if not parts or len(parts) > 5:
        raise BackendError("Provide one keyboard shortcut at a time.")
    keys = [_KEY_ALIASES.get(part.strip().lower(), part.strip().lower()) for part in parts]
    if any(not key for key in keys) or len(set(keys)) != len(keys):
        raise BackendError("That keyboard shortcut is malformed.")
    ordinary = [key for key in keys if key not in _MODIFIERS]
    if len(ordinary) != 1:
        raise BackendError("A shortcut needs exactly one key besides its modifiers.")
    key = ordinary[0]
    if key not in _SPECIAL_KEYS and not (len(key) == 1 and 32 < ord(key) < 127):
        raise BackendError(f"Unsupported key: {key}.")
    modifiers = set(keys) & set(_MODIFIERS)
    # All aliases and ordering are resolved first. Extra modifiers cannot bypass
    # these checks. Windows/Super combinations expose OS launchers and power UIs;
    # allow only explicit window-positioning shortcuts.
    unsafe = (
        ("win" in modifiers and (key not in {"left", "right", "up", "down"} or modifiers != {"win"}))
        or ("alt" in modifiers and key in {"f2", "f4"})
        or ({"ctrl", "alt"} <= modifiers and (key in {"delete", "backspace", "escape", "end", "l", "q", "t"} or key.startswith("f")))
        or ({"ctrl", "shift"} <= modifiers and key in {"escape", "q"})
        or ({"alt", "shift"} <= modifiers and key == "q")
    )
    if unsafe:
        raise BackendError("That system shortcut can lock, log out, quit, or open a system launcher. Please use it yourself.")
    return tuple(mod for mod in _MODIFIERS if mod in modifiers) + (key,)


X11_SESSION_DIR = Path("/usr/share/xsessions")


def _runtime_dir() -> Path:
    """Session runtime dir without assuming POSIX (os.getuid is missing on Windows)."""
    override = os.environ.get("XDG_RUNTIME_DIR")
    if override:
        return Path(override)
    getuid = getattr(os, "getuid", None)
    return Path(f"/run/user/{getuid()}") if callable(getuid) else Path("/run/user/0")


def wayland_socket() -> bool:
    """True when this session really has a Wayland compositor socket."""
    runtime = _runtime_dir()
    try:
        return any(runtime.glob("wayland-*"))
    except OSError:
        return False


def on_wayland() -> bool:
    """True only when Wayland is actually in use, not merely claimed.

    `XDG_SESSION_TYPE` and `WAYLAND_DISPLAY` can survive a reboot inside a shell's
    environment, and a stale value would switch off screen reading and input for no
    reason. The socket is the evidence; the variables are only a fallback.
    """
    if os.environ.get("XDG_RUNTIME_DIR"):
        return wayland_socket()
    if os.environ.get("WAYLAND_DISPLAY"):
        return True
    return os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"


def x11_sessions(directory: Path = X11_SESSION_DIR) -> list[Path]:
    """The X11 login sessions this system offers, if any."""
    try:
        return sorted(directory.glob("*.desktop"))
    except OSError:
        return []


def wayland_hint() -> str:
    """Wayland blocks screen capture and input; say what this machine can actually do."""
    if x11_sessions():
        return ("Wayland session detected. For screen reading, the overlay and computer control, sign out "
                "and choose an X11 session (for example Ubuntu on Xorg) from the login screen. "
                "Phone pairing still works here.")
    # GNOME 49 and later ship no X11 session at all, so "choose Xorg" is not advice.
    return ("Wayland session detected, and this system offers no X11 session to switch to. Install one with "
            "'sudo apt install xorg xfce4' and pick XFCE (or Xubuntu) at the login screen to get screen reading "
            "and computer control. Voice, pairing and the speech bubbles work here as they are.")


def validate_url(value: Any) -> str:
    """Only ordinary HTTP(S) links may reach the OS URL handler."""
    if not isinstance(value, str) or not value.strip() or len(value) > 8192:
        raise BackendError("Provide a website address.")
    address = value.strip()
    if any(ord(char) < 33 or ord(char) == 127 or char in "\\\"'`|<>^" for char in address):
        raise BackendError("That URL contains unsafe characters; use a percent-encoded HTTP or HTTPS link.")
    if not re.match(r"^https?://", address, re.I):
        if "://" in address or re.match(r"^[a-z][a-z0-9+.-]*:(?![0-9]+(?:/|$))", address, re.I):
            raise BackendError("Only HTTP and HTTPS website links are supported.")
        address = "https://" + address
    try:
        parsed = urlsplit(address)
        port = parsed.port
        host = parsed.hostname
    except ValueError as exc:
        raise BackendError("That website address is invalid.") from exc
    if parsed.scheme.lower() not in {"http", "https"} or not host or parsed.username or parsed.password:
        raise BackendError("Use an HTTP or HTTPS link without embedded credentials.")
    if not re.fullmatch(r"[\w.:%-]+", host, re.UNICODE) or host.startswith("-") or (port is not None and port == 0):
        raise BackendError("That website address is invalid.")
    return address


def _module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError, ModuleNotFoundError):
        return False


class _InputDriver:
    """A tiny lazy adapter; tests can replace it without touching the desktop."""

    def __init__(self) -> None:
        import pyautogui
        self.mouse = pyautogui
        self.mouse.PAUSE = 0
        self.keyboard = None

    def position(self) -> tuple[int, int]:
        point = self.mouse.position()
        return int(point[0]), int(point[1])

    def move(self, x: int, y: int) -> None:
        self.mouse.moveTo(x, y, _pause=False)

    def restore(self, x: int, y: int) -> None:
        self._cleanup(self.mouse.moveTo, x, y)

    def button_down(self, button: str) -> None:
        self.mouse.mouseDown(button=button, _pause=False)

    def button_up(self, button: str) -> None:
        self._cleanup(self.mouse.mouseUp, button=button)

    def key_down(self, key: str) -> None:
        self.mouse.keyDown("winleft" if key == "win" else key, _pause=False)

    def key_up(self, key: str) -> None:
        self._cleanup(self.mouse.keyUp, "winleft" if key == "win" else key)

    def char_down(self, char: str) -> None:
        if self.keyboard is None:
            from pynput.keyboard import Controller
            self.keyboard = Controller()
        self.keyboard.press(char)

    def char_up(self, char: str) -> None:
        if self.keyboard is not None:
            self.keyboard.release(char)

    def scroll(self, dx: int, dy: int) -> None:
        if dy:
            self.mouse.scroll(-dy, _pause=False)
        if dx:
            self.mouse.hscroll(dx, _pause=False)

    def _cleanup(self, method: Any, *args: Any, **kwargs: Any) -> None:
        # Fail-safe corners should stop actions, but must not leave a held button
        # or prevent restoring the pointer during cleanup.
        enabled = self.mouse.FAILSAFE
        try:
            self.mouse.FAILSAFE = False
            method(*args, _pause=False, **kwargs)
        finally:
            self.mouse.FAILSAFE = enabled


class DesktopBackend:
    def __init__(self) -> None:
        self._driver: Any = None
        self._snapshot: Snapshot | None = None
        self._snapshot_bounds: tuple[int, int, int, int] | None = None
        self._action_lock = threading.Lock()
        self._linux_focus: Any = None
        self._uia_local = threading.local()

    def _platform_reason(self) -> str | None:
        if sys.platform == "win32":
            return None
        if sys.platform.startswith("linux"):
            if on_wayland():
                return "Screen capture and computer control require an X11 session. Log out and choose an X11 session; Wayland is not supported."
            if not os.environ.get("DISPLAY"):
                return "No desktop display is available. Start Bluey in your graphical X11 session."
            return None
        return "This desktop port supports Windows and Linux X11."

    def capabilities(self) -> dict[str, Any]:
        reason = self._platform_reason()
        capture = reason is None and _module_available("mss") and _module_available("PIL")
        control = reason is None and _module_available("pyautogui") and _module_available("pynput")
        focus = _module_available("pywinauto" if sys.platform == "win32" else "pyatspi")
        ocr = _module_available("pytesseract") and shutil.which("tesseract") is not None
        messages = [reason] if reason else []
        if not capture and not reason:
            messages.append("Install the desktop dependencies to enable screen capture.")
        if not control and not reason:
            messages.append("Install the desktop dependencies to enable mouse and keyboard input.")
        if not ocr:
            messages.append("Tesseract OCR is unavailable; screenshots and 0–1000 grid coordinates still work.")
        if not focus:
            messages.append("Typing, paste and clickable control ids are unavailable until focused-field security can be checked. Install pywinauto on Windows or python3-pyatspi on Ubuntu.")
        return {"capture": capture, "screen_capture": capture, "control": control, "computer_control": control,
                "ocr": ocr, "focus_security": focus, "typing": control and focus, "messages": messages}

    def _require_desktop(self) -> None:
        reason = self._platform_reason()
        if reason:
            raise BackendError(reason)

    def _input(self) -> Any:
        self._require_desktop()
        if self._driver is None:
            try:
                self._driver = _InputDriver()
            except Exception as exc:
                raise BackendError("Mouse and keyboard input could not connect to the desktop. Check your session and dependencies.") from exc
        return self._driver

    def _monitor(self, capture: Any) -> dict[str, int]:
        monitors = capture.monitors[1:]
        if not monitors:
            raise BackendError("No screen is available to capture.")
        primary = getattr(capture, "primary_monitor", None)
        if primary is not None:
            try:
                return {key: int(primary[key]) for key in ("left", "top", "width", "height")}
            except (TypeError, KeyError):
                pass
        if len(monitors) == 1:
            return dict(monitors[0])
        if sys.platform == "win32":
            for monitor in monitors:
                if monitor["left"] == 0 and monitor["top"] == 0:
                    return dict(monitor)
        elif sys.platform.startswith("linux"):
            executable = shutil.which("xrandr")
            if executable:
                try:
                    result = subprocess.run([executable, "--current"], capture_output=True, text=True, timeout=2, check=True)
                    match = re.search(r"^\S+ connected primary (\d+)x(\d+)([+-]\d+)([+-]\d+)", result.stdout, re.M)
                    if match:
                        width, height, left, top = map(int, match.groups())
                        return {"left": left, "top": top, "width": width, "height": height}
                except (OSError, subprocess.SubprocessError):
                    pass
        raise BackendError("The primary monitor could not be identified. Set a primary display in your desktop settings.")

    def bounds(self) -> tuple[int, int, int, int]:
        self._require_desktop()
        try:
            import mss
            with mss.mss() as capture:
                monitor = self._monitor(capture)
            return tuple(monitor[key] for key in ("left", "top", "width", "height"))
        except BackendError:
            raise
        except Exception as exc:
            raise BackendError("The primary display could not be read. Check your desktop session and screen-capture dependencies.") from exc

    def mouse_position(self) -> tuple[int, int]:
        return self._input().position()

    def capture(self) -> Snapshot:
        self._require_desktop()
        try:
            import mss
            from PIL import Image
            with mss.mss() as capture:
                monitor = self._monitor(capture)
                raw = capture.grab(monitor)
                image = Image.frombytes("RGB", raw.size, raw.rgb)
        except BackendError:
            raise
        except Exception as exc:
            raise BackendError("Screen capture failed. Check your desktop session and screen-capture dependencies.") from exc
        bounds = tuple(monitor[key] for key in ("left", "top", "width", "height"))
        app_name, controls = self._controls(bounds)
        lead, control_targets = self._control_lines(app_name, controls, bounds)
        text, targets = self._ocr(image, bounds, lead=lead)
        targets.update(control_targets)
        pointer = self._mouse_line(targets, bounds)
        if pointer:
            text = text + "\n" + pointer
        image.thumbnail((1400, 1400))
        output = io.BytesIO()
        image.save(output, "JPEG", quality=78)
        snapshot = Snapshot(base64.b64encode(output.getvalue()).decode("ascii"), text, targets)
        self._snapshot, self._snapshot_bounds = snapshot, bounds
        return snapshot

    def _controls(self, bounds, limit=140, budget=0.45):
        """Frontmost application's clickable controls through AT-SPI, like the Mac's ControlsReader.

        Returns ``(app name, [(kind, label, x, y, w, h), ...])`` with physical
        screen pixels, or ``(None, [])`` when the accessibility bus, the Python
        bindings or the session do not allow reading them.
        """
        if not _module_available("pyatspi"):
            return None, []
        try:
            import pyatspi
        except Exception:
            return None, []
        left, top, width, height = bounds
        try:
            desktop = pyatspi.Registry.getDesktop(0)
            app = None
            for index in range(desktop.childCount):
                child = desktop.getChildAtIndex(index)
                try:
                    if child is not None and child.getState().contains(pyatspi.STATE_ACTIVE):
                        app = child
                        break
                except Exception:
                    continue
            if app is None:
                return None, []
            app_name = " ".join(str(app.name or "").split())[:100] or None
            roots = []
            for index in range(app.childCount):
                child = app.getChildAtIndex(index)
                try:
                    if child is not None and child.getState().contains(pyatspi.STATE_SHOWING):
                        roots.append(child)
                except Exception:
                    continue
            if not roots:
                roots = [app]
            deadline = time.monotonic() + budget
            found = []
            seen = set()
            queue = deque(roots)
            visited = 0
            while queue and visited < 4000 and time.monotonic() < deadline:
                element = queue.popleft()
                visited += 1
                try:
                    state = element.getState()
                    if state.contains(pyatspi.STATE_DEFUNCT):
                        continue
                    kind = _CONTROL_ROLES.get(element.getRoleName() or "")
                    if kind is not None:
                        rect = element.queryComponent().getExtents(pyatspi.DESKTOP_COORDS)
                        x, y = int(rect.x), int(rect.y)
                        w, h = int(rect.width), int(rect.height)
                        # Intersects the primary display and is big enough to hit.
                        if w > 3 and h > 3 and x < left + width and y < top + height and x + w > left and y + h > top:
                            parts = []
                            for value in (element.name, element.description):
                                clean = " ".join(str(value or "").split())[:60]
                                if clean and clean not in parts:
                                    parts.append(clean)
                            label = ", ".join(parts)[:80]
                            if label or kind in _CONTROL_FIELD_KINDS:
                                key = (x, y, w, h)
                                if key not in seen:
                                    seen.add(key)
                                    found.append((kind, label, x, y, w, h))
                    # Hidden windows, closed menus and invisible toolbars are skipped.
                    for child_index in range(min(element.childCount, 400)):
                        child = element.getChildAtIndex(child_index)
                        try:
                            if child is not None and child.getState().contains(pyatspi.STATE_SHOWING):
                                queue.append(child)
                        except Exception:
                            continue
                except Exception:
                    continue
            # Reading order: top to bottom, then left to right.
            found.sort(key=lambda item: (item[3], item[2]))
            return app_name, found[:limit]
        except Exception:
            return None, []

    @staticmethod
    def _control_lines(app_name, controls, bounds):
        """Format the controls block for the model and their C# target positions."""
        lines = []
        targets = {}
        if app_name:
            lines.append(f"Frontmost app: {app_name}")
        if controls:
            lines.append("Controls (click these by id):")
            left, top, width, height = bounds
            for number, (kind, label, x, y, w, h) in enumerate(controls, 1):
                center_x, center_y = x + w // 2, y + h // 2
                targets[f"C{number}"] = (center_x, center_y)
                grid_x = round((center_x - left) / max(1, width) * 1000)
                grid_y = round((center_y - top) / max(1, height) * 1000)
                line = f"C{number} {kind} @{grid_x},{grid_y}"
                if label:
                    line += f" \"{label}\""
                lines.append(line)
        return lines, targets

    def _mouse_line(self, targets, bounds):
        """Where the user's real pointer sits, so 'this', 'that' and 'here' mean something."""
        try:
            pointer = self.mouse_position()
        except (Exception, SystemExit):
            return None
        left, top, width, height = bounds
        x, y = pointer
        grid_x = max(0, min(1000, round((x - left) / max(1, width) * 1000)))
        grid_y = max(0, min(1000, round((y - top) / max(1, height) * 1000)))
        line = f"The user's mouse pointer is at @{grid_x},{grid_y}"
        nearest, nearest_distance = None, 30.0
        for target_id, (target_x, target_y) in targets.items():
            offset_x = (target_x - left) / max(1, width) * 1000 - grid_x
            offset_y = (target_y - top) / max(1, height) * 1000 - grid_y
            distance = math.hypot(offset_x, offset_y)
            if distance < nearest_distance:
                nearest, nearest_distance = target_id, distance
        if nearest:
            line += f", on {nearest}"
        return line + ". When they say \"this\", \"that\" or \"here\", they mean what's at their mouse pointer."

    def _ocr(self, image: Any, bounds: tuple[int, int, int, int], lead=()) -> tuple[str, dict[str, tuple[int, int]]]:
        header = "Primary screen. Coordinates are on a 0–1000 grid (top left 0,0; bottom right 1000,1000)."
        if not _module_available("pytesseract") or not shutil.which("tesseract"):
            return "\n".join([header, *lead, "OCR unavailable; use the screenshot and grid coordinates."]), {}
        try:
            import pytesseract
            data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT, timeout=12)
        except Exception:
            return "\n".join([header, *lead, "OCR could not read this screen; use the screenshot and grid coordinates."]), {}
        left, top, width, height = bounds
        targets: dict[str, tuple[int, int]] = {}
        lines: dict[tuple[int, int, int], list[tuple[str, str, int, int, int, int]]] = {}
        for index, value in enumerate(data["text"]):
            value = " ".join(str(value).split())[:150]
            if not value or float(data["conf"][index]) < 30:
                continue
            x, y, w, h = (int(data[key][index]) for key in ("left", "top", "width", "height"))
            target_id = f"W{len(targets) + 1}"
            targets[target_id] = (left + x + w // 2, top + y + h // 2)
            line_key = tuple(int(data[key][index]) for key in ("block_num", "par_num", "line_num"))
            lines.setdefault(line_key, []).append((target_id, value, x, y, w, h))
            if len(targets) >= 600:
                break
        descriptions = [header, *lead, "Text targets (L = line, W = word):"]
        for number, words in enumerate(lines.values(), 1):
            x = min(word[2] for word in words)
            y = min(word[3] for word in words)
            right = max(word[2] + word[4] for word in words)
            bottom = max(word[3] + word[5] for word in words)
            center_x, center_y = (x + right) // 2, (y + bottom) // 2
            target_id = f"L{number}"
            targets[target_id] = (left + center_x, top + center_y)
            descriptions.append(f"{target_id} @{round(center_x / width * 1000)},{round(center_y / height * 1000)} "
                                + " ".join(word[1] for word in words) + " | "
                                + " ".join(f"{word[0]}={word[1]}" for word in words))
        if not targets:
            descriptions.append("No text found; use the screenshot and grid coordinates.")
        return "\n".join(descriptions), targets

    def resolve(self, args: dict[str, Any], id_key: str = "target_id", x_key: str = "x", y_key: str = "y") -> tuple[int, int]:
        current = self.bounds()
        if self._snapshot_bounds is not None and current != self._snapshot_bounds:
            self._snapshot = None
            self._snapshot_bounds = None
            raise BackendError("The screen layout changed. Take a fresh screenshot before acting.")
        target = args.get(id_key)
        if target is not None:
            if not isinstance(target, str) or self._snapshot is None:
                raise BackendError("Take a fresh screenshot before using a target ID.")
            target = target.strip().upper()
            if target not in self._snapshot.targets:
                raise BackendError(f"Target {target} is missing. Take a fresh screenshot.")
            return self._snapshot.targets[target]
        left, top, width, height = current
        x, y = (_number(args.get(key), key, 0, 1000) for key in (x_key, y_key))
        return left + round(x / 1000 * (width - 1)), top + round(y / 1000 * (height - 1))

    def _focus_is_secure(self) -> bool | None:
        """True=password, False=known editable non-password field, None=unknown."""
        try:
            if sys.platform == "win32":
                if not getattr(self._uia_local, "initialized", False):
                    import comtypes
                    comtypes.CoInitializeEx(0)
                    self._uia_local.initialized = True
                from pywinauto.uia_defines import IUIA
                element = IUIA().iuia.GetFocusedElement()
                if not element or not element.CurrentHasKeyboardFocus:
                    return None
                if element.CurrentIsPassword:
                    return True
                # UIA Edit and Document are the standard text-entry controls.
                if element.CurrentControlType in {50004, 50030} and element.CurrentIsEnabled:
                    return False
                return None
            import pyatspi
            if self._linux_focus is not None:
                try:
                    states = self._linux_focus.getState()
                    if states.contains(pyatspi.STATE_FOCUSED) and not states.contains(pyatspi.STATE_DEFUNCT):
                        if self._linux_focus.getRole() == pyatspi.ROLE_PASSWORD_TEXT:
                            return True
                        if states.contains(pyatspi.STATE_EDITABLE):
                            return False
                except Exception:
                    pass
                self._linux_focus = None
            desktop = pyatspi.Registry.getDesktop(0)
            queue = deque([desktop])
            deadline = time.monotonic() + 0.4
            visited = 0
            while queue and visited < 2500 and time.monotonic() < deadline:
                element = queue.popleft()
                visited += 1
                states = element.getState()
                if states.contains(pyatspi.STATE_FOCUSED):
                    if element.getRole() == pyatspi.ROLE_PASSWORD_TEXT:
                        self._linux_focus = element
                        return True
                    if states.contains(pyatspi.STATE_EDITABLE):
                        self._linux_focus = element
                        return False
                if states.contains(pyatspi.STATE_DEFUNCT):
                    continue
                # The desktop and application roots are not necessarily visible.
                if visited > 1 and element.getRole() != pyatspi.ROLE_APPLICATION and not states.contains(pyatspi.STATE_SHOWING):
                    continue
                for index in range(min(element.childCount, 400)):
                    child = element.getChildAtIndex(index)
                    if child is not None:
                        queue.append(child)
        except Exception:
            return None
        return None

    def _require_safe_field(self) -> None:
        secure = self._focus_is_secure()
        if secure is True:
            raise BackendError("That is a password field. Please type into it yourself.")
        if secure is not False:
            raise BackendError("I cannot verify that the focused field is safe to type into. Click an accessible text field; install or enable desktop accessibility if needed.")

    def perform(self, name: str, args: dict[str, Any], cancel_event: threading.Event) -> str:
        self._require_desktop()
        _check_cancel(cancel_event)
        if not isinstance(args, dict):
            raise BackendError("Action arguments must be an object.")
        # Do not let two realtime calls interleave held buttons or shortcuts.
        while not self._action_lock.acquire(timeout=0.05):
            _check_cancel(cancel_event)
        try:
            return self._perform(name, args, cancel_event)
        finally:
            self._action_lock.release()

    def _perform(self, name: str, args: dict[str, Any], cancel: threading.Event) -> str:
        _check_cancel(cancel)
        if name == "open_url":
            return self._open_url(args.get("url"), cancel)
        if name == "open_app":
            return self._open_app(args.get("name"), cancel)
        if name not in {"click", "drag", "scroll", "type_text", "press_keys"}:
            raise BackendError(f"Unsupported computer action: {name}.")
        driver = self._input()
        saved = None
        held_buttons: list[str] = []
        held_keys: list[str] = []
        held_chars: list[str] = []

        def chord(keys: tuple[str, ...], *, safe_field: bool = False) -> None:
            for key in keys:
                _check_cancel(cancel)
                if safe_field:
                    self._require_safe_field()
                    _check_cancel(cancel)
                held_keys.append(key)
                driver.key_down(key)
            _pause(cancel, 0.025)
            for key in reversed(keys):
                driver.key_up(key)
                held_keys.remove(key)
            _check_cancel(cancel)

        try:
            if name in {"click", "drag", "scroll"}:
                saved = driver.position()
            if name == "click":
                point = self.resolve(args)
                button = "right" if args.get("right", False) else args.get("button", "left")
                if button not in {"left", "right"}:
                    raise BackendError("Choose a left or right mouse button. Middle-click paste is unavailable.")
                count = 2 if args.get("double", False) else int(_number(args.get("count", 1), "count", 1, 2))
                _check_cancel(cancel)
                driver.move(*point)
                for _ in range(count):
                    _check_cancel(cancel)
                    held_buttons.append(button)
                    driver.button_down(button)
                    _pause(cancel, 0.035)
                    driver.button_up(button)
                    held_buttons.remove(button)
                    _pause(cancel, 0.075)
                return "Clicked."
            if name == "drag":
                start = self.resolve(args, "from_id", "from_x", "from_y")
                end = self.resolve(args, "to_id", "to_x", "to_y")
                _check_cancel(cancel)
                driver.move(*start)
                _check_cancel(cancel)
                held_buttons.append("left")
                driver.button_down("left")
                for step in range(1, 31):
                    _pause(cancel, 0.015)
                    driver.move(round(start[0] + (end[0] - start[0]) * step / 30), round(start[1] + (end[1] - start[1]) * step / 30))
                _check_cancel(cancel)
                driver.button_up("left")
                held_buttons.remove("left")
                return "Dragged."
            if name == "scroll":
                point_args = args if any(key in args for key in ("target_id", "x", "y")) else {"x": 500, "y": 500}
                point = self.resolve(point_args)
                direction = args.get("direction", "down")
                if direction not in {"up", "down", "left", "right"}:
                    raise BackendError("Scroll direction must be up, down, left, or right.")
                amount = round(_number(args.get("amount", 3), "amount", 1, 10))
                dx, dy = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}[direction]
                _check_cancel(cancel)
                driver.move(*point)
                for _ in range(amount * 3):
                    _check_cancel(cancel)
                    driver.scroll(dx, dy)
                    _pause(cancel, 0.025)
                return f"Scrolled {direction}."
            if name == "press_keys":
                keys = normalize_keys(args.get("keys", args.get("combo", "")))
                ordinary = keys[-1]
                mods = set(keys[:-1])
                edits_text = (not (mods - {"shift"}) and (len(ordinary) == 1 or ordinary in {"space", "enter", "backspace", "delete"})) or ("ctrl" in mods and ordinary in {"v", "x"}) or ("shift" in mods and ordinary == "insert")
                if edits_text:
                    self._require_safe_field()
                chord(keys, safe_field=edits_text)
                return "Pressed " + "+".join(keys) + "."
            text = args.get("text")
            if not isinstance(text, str) or len(text) > 10000:
                raise BackendError("Provide at most 10,000 characters to type.")
            if any(ord(char) < 32 and char not in "\n\r\t" for char in text):
                raise BackendError("The text contains unsupported control characters.")
            if not text and not args.get("press_return", False):
                return "Nothing to type."
            self._require_safe_field()
            for char in text.replace("\r\n", "\n").replace("\r", "\n"):
                _check_cancel(cancel)
                # Recheck after every newline/tab or focus change, including
                # another program stealing focus partway through typing.
                self._require_safe_field()
                _check_cancel(cancel)
                if char in "\n\t":
                    chord(("enter" if char == "\n" else "tab",))
                else:
                    held_chars.append(char)
                    driver.char_down(char)
                    _check_cancel(cancel)
                    driver.char_up(char)
                    held_chars.remove(char)
                _pause(cancel, 0.012)
            if args.get("press_return", False):
                self._require_safe_field()
                chord(("enter",), safe_field=True)
            return "Typed text." + (" Pressed Return." if args.get("press_return", False) else "")
        finally:
            for char in reversed(held_chars):
                try:
                    driver.char_up(char)
                except Exception:
                    pass
            for key in reversed(held_keys):
                try:
                    driver.key_up(key)
                except Exception:
                    pass
            for button in reversed(held_buttons):
                try:
                    driver.button_up(button)
                except Exception:
                    pass
            if saved is not None:
                try:
                    driver.restore(*saved)
                except Exception:
                    pass

    def _open_url(self, value: Any, cancel: threading.Event) -> str:
        address = validate_url(value)
        _check_cancel(cancel)
        try:
            if sys.platform == "win32":
                os.startfile(address)
            else:
                executable = shutil.which("xdg-open")
                if not executable:
                    raise BackendError("Install xdg-utils to open websites in your default browser.")
                _check_cancel(cancel)
                subprocess.Popen([executable, address], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as exc:
            raise BackendError("The default browser could not be opened.") from exc
        _check_cancel(cancel)
        return f"Opened {address}."

    def _open_app(self, value: Any, cancel: threading.Event) -> str:
        if not isinstance(value, str):
            raise BackendError("Provide an application name.")
        name = value.strip().lower()
        if name in {"browser", "web browser", "default browser", "safari"}:
            return self._open_url("https://www.google.com", cancel)
        linux_apps = {
            "firefox": ("firefox",), "chrome": ("google-chrome", "chromium", "chromium-browser"),
            "chromium": ("chromium", "chromium-browser"), "files": ("nautilus", "dolphin", "thunar"),
            "file manager": ("nautilus", "dolphin", "thunar"), "nautilus": ("nautilus",),
            "calculator": ("gnome-calculator", "kcalc", "galculator"),
            "notes": ("gnome-text-editor", "gedit", "mousepad", "kate"),
            "text editor": ("gnome-text-editor", "gedit", "mousepad", "kate"),
            "gedit": ("gedit",), "libreoffice": ("libreoffice",), "vlc": ("vlc",),
        }
        windows_apps = {
            "notepad": ("notepad.exe",), "notes": ("notepad.exe",), "text editor": ("notepad.exe",),
            "calculator": ("calc.exe",), "paint": ("mspaint.exe",),
            "files": ("explorer.exe",), "file explorer": ("explorer.exe",), "explorer": ("explorer.exe",),
            "firefox": ("firefox.exe",), "chrome": ("chrome.exe",), "edge": ("msedge.exe",),
        }
        mapping = windows_apps if sys.platform == "win32" else linux_apps
        if name not in mapping:
            raise BackendError("That application is not in the supported app list. Supported names: " + ", ".join(sorted(mapping)) + ".")
        executable = next((found for item in mapping[name] if (found := shutil.which(item))), None)
        if executable is None and sys.platform == "win32":
            relative = {"chrome": "Google/Chrome/Application/chrome.exe", "edge": "Microsoft/Edge/Application/msedge.exe", "firefox": "Mozilla Firefox/firefox.exe"}.get(name)
            if relative:
                for variable in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
                    folder = os.environ.get(variable)
                    candidate = Path(folder) / relative if folder else None
                    if candidate is not None and candidate.is_file():
                        executable = str(candidate)
                        break
        if executable is None:
            raise BackendError(f"{value.strip()} does not appear to be installed.")
        _check_cancel(cancel)
        try:
            subprocess.Popen([executable], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as exc:
            raise BackendError(f"Could not open {value.strip()}.") from exc
        _check_cancel(cancel)
        return f"Opened {value.strip()}."


def _number(value: Any, name: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not minimum <= value <= maximum:
        raise BackendError(f"{name} must be a number from {minimum:g} to {maximum:g}.")
    return float(value)


def _check_cancel(event: threading.Event) -> None:
    if event.is_set():
        raise CancelledError("Computer action stopped.")


def _pause(event: threading.Event, seconds: float) -> None:
    if event.wait(seconds):
        raise CancelledError("Computer action stopped.")
