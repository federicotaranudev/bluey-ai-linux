# Build on the target OS: python -m PyInstaller Desktop/bluey.spec
from pathlib import Path
import sys

from PyInstaller.utils.hooks import collect_submodules, copy_metadata

desktop = Path(SPECPATH)
repo = desktop.parent
hiddenimports = collect_submodules("keyring.backends")
hiddenimports += ["zeroconf", "PIL.Image", "pytesseract"]
if sys.platform == "win32":
    hiddenimports += [
        "mss.windows",
        "pyautogui._pyautogui_win",
        "pynput.keyboard._win32",
        "pynput.mouse._win32",
        "pynput._util.win32",
        "pywinauto",
    ]
else:
    hiddenimports += [
        "mss.linux",
        "pyautogui._pyautogui_x11",
        "pynput.keyboard._xorg",
        "pynput.mouse._xorg",
        "pynput._util.xorg",
        "keyring.backends.SecretService",
    ]

datas = [(str(repo / "Shared" / "Fonts"), "Shared/Fonts")]
datas += copy_metadata("keyring")
analysis = Analysis(
    [str(desktop / "launcher.py")],
    pathex=[str(desktop)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # AT-SPI is optional. MouseInfo is an unused Tk utility that can call
    # sys.exit while PyAutoGUI imports it if the build machine lacks Tk.
    excludes=["PyQt5", "PyQt6", "PySide2", "pyatspi", "gi", "mouseinfo", "tkinter"],
    noarchive=False,
)
archive = PYZ(analysis.pure)
executable = EXE(
    archive,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="BlueyDesktop",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
)
bundle = COLLECT(
    executable,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name="BlueyDesktop",
)
