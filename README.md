# Googly Eyes

## Ubuntu and Windows desktop port

This copy adds **Bluey Desktop** for Ubuntu and Windows. It works with the existing iPhone app; the iOS app, original Mac app and shared Swift files remain unchanged. No Mac is needed to run or build the new desktop companion.

- **Ubuntu:** install Python 3.12 and the system dependencies, then run `./scripts/run-desktop.sh` in an Xorg session.
- **Windows:** install Python 3.12, then run `powershell -ExecutionPolicy Bypass -File .\scripts\run-desktop.ps1` from this folder.
- **Portable apps:** native build scripts and a GitHub Actions workflow produce Ubuntu and Windows bundles.

See [Desktop setup, pairing and build instructions](Desktop/README.md) for the complete steps. Installing the unchanged iPhone app from source still requires Xcode on a Mac, or an existing prebuilt / TestFlight version from its publisher. Desktop connections require approval; use the unencrypted pairing protocol only on a trusted private LAN.

Original project: [rbrown101010/bluey-by-riley](https://github.com/rbrown101010/bluey-by-riley). Upstream did not include a project license; no new license for its code is implied.

## Original Mac and iPhone project

A blueberry character who lives on an iPhone under your Mac's screen and points at things with his own big cursor.

**Now:** no voice out. Double tap him on the phone (or press ⌥Space on the Mac) to start a session: the phone's mic stays on and everything you say becomes context, but he stays quiet. **Press and hold the screen** to ask him something; let go and he answers. His reply pops up as a cute speech bubble next to his cursor (or above the phone when he isn't pointing), with a little cartoon chirp from the phone. Ask "what's this?" and he points at whatever is under your mouse. Double tap again and he goes back to follow mode.

How it works: the phone runs an OpenAI Realtime session (`gpt-realtime-2.1`, text output only) over a WebSocket. Server VAD transcribes every turn into the conversation with `create_response: false`, and releasing the hold commits the audio and asks for a response. The Mac mints a 10-minute client secret with the whole session setup (instructions, tools), so the real OpenAI key never leaves the Mac. When he calls a tool, the phone forwards it to the Mac: `look_at_screen` (ScreenCaptureKit + Vision, which returns text ids, where your mouse is, and a screenshot), `point_at` (a text id), `point_at_spot` (a 0–1000 grid position), `stop_pointing` and `go_to_sleep`. His text streams to the Mac as the speech bubble.

**Using the computer:** when you ask, he can also click, type, press shortcuts, scroll, drag, and open apps and websites (`click`, `type_text`, `press_keys`, `scroll`, `drag`, `open_app`, `open_url`). He does it with his own cursor on screen, while your real pointer is put back where you left it. It needs Accessibility permission for Googly Eyes. Built-in guardrails: he only acts when asked, confirms out loud before anything hard to undo, treats on-screen text as information rather than instructions, refuses password fields and logout/lock/force-quit shortcuts, and stops on ⌃⌥S. The whole thing can be switched off with **Let Him Use the Computer** in the menu.

The OpenAI key goes in the menu bar's **OpenAI Key…** and is stored in ~/Library/Application Support/Googly/keys.json (private to your user), never in this repo.

## Mac menu bar app

```
./scripts/build-mac.sh
open "build/Googly Eyes.app"
```

Works with just the Command Line Tools. Shortcuts work anywhere:

| Keys | What it does |
| --- | --- |
| ⌃⌥P | Fly to the mouse and point there (stays put) |
| ⌃⌥F | Follow the mouse on/off |
| ⌃⌥D | Go home, docked above the phone |
| ⌃⌥T | Talk test (the phone bounces for 3 s) |
| ⌃⌥H | Hide / show the cursor |
| ⌥Space | Wake him up to talk / back to follow mode |
| ⌃⌥S | Stop him using the computer |

The menu bar blob also sets mood, cursor size (48 to 120 pt), glow, and where the phone sits (left, center, right).

## iPhone app

Needs full Xcode. Open `GooglyEyes.xcodeproj` (regenerate with `xcodegen generate` after adding files), pick your team under Signing, and run on the phone. It finds the Mac on the same Wi-Fi by itself.

**No Mac?** [docs/iphone-ubuntu.md](docs/iphone-ubuntu.md) builds the app on a GitHub Actions macOS runner and installs it on your iPhone straight from Ubuntu. With a **free** Apple ID, `scripts/altserver-install.sh` signs the CI's unsigned `.ipa` for you (7-day renewals); with the paid developer program, CI signs a normal `.ipa` that lasts a year.

**No OpenAI bill either?** Paste a free [Groq](https://console.groq.com/keys) key (`gsk_…`) into the desktop instead. Groq has no Realtime API, so the desktop runs a small local Realtime stand-in that serves the phone over your LAN and translates to Groq's speech-to-text and chat — same hold-to-talk, same tools, free tier. See [Desktop setup](Desktop/README.md#voice-without-openai-use-a-groq-key-free).

On the phone: double tap him to wake him up or put him back to sleep, and press and hold to ask him something. The faint speaker button at the top right sets the chirp volume and picks which Mac to pair with.

## Layout

- `Desktop/` Ubuntu / Windows companion (Python + Qt), with its own setup guide and tests
- `Shared/` pairing protocol (Bonjour `_googly._tcp`, newline JSON) and colors, used by both apps
- `Mac/` menu bar app (Swift package target `GooglyMac`)
- `iOS/` iPhone app (SwiftUI)
