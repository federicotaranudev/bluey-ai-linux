"""Run with `python -m bluey`; smoke mode never connects or drives the desktop."""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path


def choose_qt_platform() -> None:
    """Point Qt at X11 when a terminal left over from Wayland still asks for wayland.

    Without this the Qt wayland plugin is loaded, fails to reach a compositor that
    no longer exists, and only then falls back — or simply refuses to start.
    """
    if not sys.platform.startswith("linux") or not os.environ.get("DISPLAY"):
        return
    from .backend import on_wayland
    if on_wayland():
        return
    if os.environ.get("QT_QPA_PLATFORM", "").lower() in ("", "wayland", "wayland-egl"):
        os.environ["QT_QPA_PLATFORM"] = "xcb"


def main() -> int:
    parser = argparse.ArgumentParser(description="Bluey desktop companion for Ubuntu and Windows")
    parser.add_argument("--port", type=int, default=8765, help="LAN TCP port (default: 8765)")
    parser.add_argument("--smoke-test", action="store_true", help="Open and render UI without networking, credentials or computer control, then exit")
    parser.add_argument("--screenshot", type=Path, help="Save setup window PNG (use with --smoke-test)")
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error("--port must be 0–65535")
    if args.smoke_test:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    if sys.platform == "win32":
        # Match screenshot/input physical pixels before Qt or PyAutoGUI initializes.
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            pass
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    from .settings import log_file
    from .visuals import register_fonts
    # Written to a file, because the window is not somewhere to read stack traces from.
    path = log_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(filename=str(path), level=logging.INFO,
                            format="%(asctime)s %(levelname)-7s %(name)s %(message)s")
    except OSError:
        pass
    from .app import DesktopApp
    choose_qt_platform()
    app = QApplication(sys.argv[:1])
    app.setApplicationName("BlueyDesktop")
    app.setOrganizationName("BlueyDesktop")
    app.setQuitOnLastWindowClosed(False)
    register_fonts()
    desktop = DesktopApp(port=args.port, smoke=args.smoke_test)
    app.aboutToQuit.connect(desktop.shutdown)
    if args.smoke_test:
        def finish():
            try:
                if args.screenshot:
                    args.screenshot.parent.mkdir(parents=True, exist_ok=True)
                    if not desktop.window.grab().save(str(args.screenshot)):
                        raise RuntimeError("Could not save screenshot")
                print("Bluey desktop smoke test passed")
                app.exit(0)
            except Exception as exc:
                print(f"Smoke test failed: {exc}", file=sys.stderr)
                app.exit(1)
        QTimer.singleShot(500, finish)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
