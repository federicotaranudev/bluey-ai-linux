# Bluey Desktop for Ubuntu and Windows

This is a Python / Qt desktop port of [rbrown101010/bluey-by-riley](https://github.com/rbrown101010/bluey-by-riley). It replaces the Mac companion on Ubuntu and Windows and speaks the same `_googly._tcp` Bonjour / newline-delimited JSON protocol to the original iPhone app. The Swift iOS, Mac and shared protocol sources remain unchanged.

The desktop shows the character and speech bubbles, follows or points around the screen, captures screenshots and optional OCR, handles the phone's tool requests, and creates temporary OpenAI client secrets. Computer control starts disabled and can be enabled in the desktop window. The API key is kept in the OS credential store when available; otherwise it lasts only for the current session.

## Voice without OpenAI: use a Groq key (free)

Groq has no Realtime API, so the desktop runs a small **local Realtime stand-in**
(`bluey/realtime_proxy.py`): the phone connects to it instead of OpenAI, and it
translates to Groq's speech-to-text (`whisper-large-v3-turbo`) and chat
(`llama-3.3-70b-versatile`) with the same desktop tools. Everything else — hold to
talk, the eyes, the bubbles, the chirps, the transcripts, computer control — behaves
as before.

Just paste a Groq key (`gsk_…`) into the same box. The provider is detected from the
key, and the desktop hands the phone its own `ws://` address automatically. An OpenAI
key keeps the original path, including `web_research`.

| | OpenAI key | Groq key |
|---|---|---|
| Cost | needs a paid plan | **free tier** (rate limits) |
| Speech to text | Realtime VAD | `whisper-large-v3-turbo` |
| Model | `gpt-realtime-2.1` | `llama-3.3-70b-versatile` |
| `web_research` report card | yes | no (Groq has no search tool) |
| Screenshots sent to the model | yes | only with a vision model |
| Latency | lower | ~0.5–1 s more (transcribe, then answer) |

Screenshots still reach the model as **OCR text and control IDs** from
`look_at_screen`; the JPEG itself is only forwarded when the chosen Groq model can
see images. Override the defaults with environment variables:

```bash
BLUEY_GROQ_MODEL=meta-llama/llama-4-scout-17b-16e-instruct ./scripts/run-desktop.sh   # vision
BLUEY_GROQ_STT_MODEL=whisper-large-v3 ./scripts/run-desktop.sh                          # slower, better
```

The proxy listens on a random LAN port and only accepts the phone, using a token
minted per session; it never talks to OpenAI and holds no audio longer than the
90-second buffer.

## Run on Ubuntu

Use Ubuntu 24.04 with Python 3.12 for the simplest source setup. Python 3.11–3.14 are accepted. On Ubuntu 22.04, use the native bundle or provide Python 3.11 or newer; its default Python 3.10 is too old.

For full screenshot, overlay and computer-control support you need an **X11 session**: Wayland deliberately blocks global screen capture and synthetic input.

- Where an X11 session exists (Ubuntu before 24.04, KDE, Xfce): log out, pick your user, choose **Ubuntu on Xorg** from the gear menu, log in.
- On **GNOME 49 and later** (Ubuntu 24.04+ with GNOME, and all of Ubuntu 26.04) the X11 session was removed from GNOME itself, so there is nothing to switch to. Install another X11 desktop and pick it at login:
  ```bash
  sudo apt install xorg xfce4        # or: sudo apt install xubuntu-desktop
  ```
  XFCE and Xubuntu are ordinary X11 sessions, so Bluey gets screen reading, its cursor overlay and computer control.

Phone discovery, voice and the speech bubbles keep working on Wayland; only the screen-reading and control features need the X11 session.

From the repository folder:

```bash
sudo apt update
sudo apt install python3-venv python3-dev python3-tk build-essential libegl1 libgl1 libxkbcommon-x11-0 libxcb-cursor0 libxcb-icccm4 libxcb-keysyms1 libxcb-render-util0 libxcb-xinerama0 libxcb-xfixes0
./scripts/run-desktop.sh
```

The first run creates `.venv` and installs the Python dependencies. Later runs reuse it. To select a different Python before creating the environment:

```bash
BLUEY_PYTHON=python3.12 ./scripts/run-desktop.sh
```

For text recognition, install the optional OCR engine. For native accessibility information in a source install, install AT-SPI bindings and use the distribution's Python:

```bash
sudo apt install tesseract-ocr python3-pyatspi
```

The launcher creates its environment with `--system-site-packages`, so distribution-provided AT-SPI bindings can be found. They may not work with a separately installed Python version. Without AT-SPI, typing and paste shortcuts are refused because the app cannot check whether the focused field is a password field, and screen reading falls back to OCR text targets (`L` / `W`) plus 0–1000 grid positions. With AT-SPI, `look_at_screen` also lists the frontmost app and its clickable controls as `C` ids, matching the original Mac app. Mouse actions, capture / OCR, supported app launches and navigation shortcuts remain available. Apps that do not expose an accessible editable field may refuse typing even with AT-SPI installed.

**Portable Ubuntu bundles do not include AT-SPI, so they cannot type, paste, or read clickable control ids.** Use the source installation with the distribution's Python and `python3-pyatspi` for those actions. OCR requires the separate `tesseract` executable even in a bundle.

## Run on Windows

Install 64-bit Python 3.12 from [python.org](https://www.python.org/downloads/windows/) with the Python launcher, then open PowerShell in this repository:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run-desktop.ps1
```

The execution-policy option applies only to this process. The first run creates `.venv` and installs the dependencies. To use a specific Python installation, set `$env:BLUEY_PYTHON` to the full path of its `python.exe` before running the script.

Optional OCR needs Tesseract installed separately and its installation folder added to `PATH`; see the [Tesseract installation instructions](https://tesseract-ocr.github.io/tessdoc/Installation.html). Restart the terminal after changing `PATH`. Native Windows accessibility uses UI Automation. Run as your normal user; control of applications running with administrator privileges may be unavailable.

## Pair the existing iPhone app

1. Start Bluey Desktop and enter your OpenAI API key in its settings. An API key with access to the model configured by the original iOS app is required for conversation.
2. Put the desktop and iPhone on the same trusted local network. Approve the desktop connection prompt when your phone connects.
3. Open the existing iPhone app and select the desktop using its existing device picker. The phone interface still calls these devices “Macs”.
4. Double-tap the character to wake or sleep. Hold the phone screen to ask a question and release to request a reply.

Discovery uses multicast DNS on UDP 5353; the desktop listens on TCP 8765 by default. Permit these on the private LAN if your firewall blocks discovery or pairing. Guest Wi-Fi, client isolation and some VPNs can prevent devices from seeing each other. Change the TCP port if needed:

```bash
./scripts/run-desktop.sh --port 8877
```

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run-desktop.ps1 --port 8877
```

Bonjour advertises the selected port, so no iOS source change is necessary. Each new connection needs desktop approval. The inherited LAN protocol is unencrypted: use a trusted private network and do not expose the listener to the internet. Screen content and speech used by the assistant are sent to OpenAI during conversation.

**The iPhone app stays as it is.** Installing it from source still needs full Xcode on a Mac and Apple signing, or access to an already-built / TestFlight version from its publisher. This desktop port does not build or sign iOS apps itself — but [docs/iphone-ubuntu.md](../docs/iphone-ubuntu.md) shows how to have a GitHub Actions macOS runner build a signed `.ipa` and install it from Ubuntu, so no Mac is required.

## Desktop shortcuts

| Keys | Action |
| --- | --- |
| Ctrl+Alt+P | Point at the real mouse location |
| Ctrl+Alt+F | Toggle following the mouse |
| Ctrl+Alt+D | Return home above the phone position |
| Ctrl+Alt+H | Hide / show the character |
| Ctrl+Alt+T | Show a speech / talking demo |
| Ctrl+Alt+Space | Wake / sleep the phone session |
| Ctrl+Alt+S | Emergency stop; disable computer control until re-enabled |

Global shortcuts require an active desktop session and may conflict with shortcuts assigned by your OS. Controls in the app window remain available. Enable computer control only when you want the phone assistant to click, type, scroll, drag, or open apps and websites.

## Build a portable desktop bundle

Build on the operating system you want to target. [PyInstaller bundles the Python runtime and app dependencies](https://pyinstaller.org/en/stable/usage.html); it is not a cross-compiler. No Mac is required for either desktop build.

Ubuntu:

```bash
./scripts/build-desktop.sh
./dist/BlueyDesktop/BlueyDesktop
```

Windows:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build-desktop.ps1
.\dist\BlueyDesktop\BlueyDesktop.exe
```

Copy the entire `dist/BlueyDesktop` directory, including `_internal`; do not copy only the executable. The build also produces a `.tar.gz` archive on Ubuntu or a `.zip` on Windows. The bundles contain Python, Qt and the fonts, but still use the host's display system and optional Tesseract installation. Ubuntu may need the display libraries listed above. These are portable unsigned builds, without an installer or automatic updates.

`.github/workflows/desktop.yml` runs tests and builds x64 bundles on Ubuntu 22.04 and Windows using Python 3.12. Once this repository is pushed to GitHub, run **Desktop checks and bundles** from Actions and download the successful run's artifact for your OS. The workflow does not publish a release. Building Linux on Ubuntu 22.04 keeps its glibc baseline compatible with Ubuntu 22.04 and newer; local builds inherit the build machine's baseline.

## Development checks

After the launcher has created `.venv`, Ubuntu:

```bash
.venv/bin/python -m pip install -e 'Desktop[test,build]'
.venv/bin/python -m pytest Desktop/tests
QT_QPA_PLATFORM=offscreen .venv/bin/python -m bluey --smoke-test
```

Windows:

```powershell
.\.venv\Scripts\python.exe -m pip install -e 'Desktop[test,build]'
.\.venv\Scripts\python.exe -m pytest Desktop/tests
$env:QT_QPA_PLATFORM = 'offscreen'
.\.venv\Scripts\python.exe -m bluey --smoke-test
Remove-Item Env:QT_QPA_PLATFORM
```

The smoke test constructs the Qt interface and exits without starting the LAN server, global shortcuts or an OpenAI request. Add `--screenshot /path/to/preview.png` to save its window. Automated checks do not replace testing real iPhone pairing, microphones, display permissions and input injection on each target OS.

## Attribution

Original project: [Bluey by Riley](https://github.com/rbrown101010/bluey-by-riley). The upstream snapshot did not include a project license; this port does not grant new rights to the original code or artwork. Font licenses are preserved in `Shared/Fonts/`. Qt / PySide6 and the other bundled dependencies retain their own licenses.
