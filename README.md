# Googly Eyes

A blueberry character who lives on an iPhone under your Mac's screen and points at things with his own big cursor.

**Now:** the phone face, a big cursor you can drive by hand, and a brain: hold ⌥Space, ask about what's on screen, and he answers out loud while pointing at the exact words he's talking about.

How a question works: Apple speech-to-text hears you, ScreenCaptureKit + Vision read every word on screen with its box, Claude (`claude-opus-5`, low effort) picks what to say and which box ids to point at on which word, and ElevenLabs (Eleven v4 Turbo through the text-to-dialogue endpoint, falling back to Flash v2.5, then the Mac's voice) speaks with character timestamps so the cursor lands on cue.

API keys go in the menu bar's **API Keys…** and are stored in ~/Library/Application Support/Googly/keys.json (private to your user), never in this repo.

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
| ⌥Space (hold) | Ask out loud; let go and he answers |
| ⌃⌥A | Ask by typing |

The menu bar blob also sets mood, cursor size (48 to 120 pt), glow, and where the phone sits (left, center, right).

## iPhone app

Needs full Xcode. Open `GooglyEyes.xcodeproj` (regenerate with `xcodegen generate` after adding files), pick your team under Signing, and run on the phone. It finds the Mac on the same Wi-Fi by itself.

On the phone: drag a finger to make him look at it, double tap to cycle moods.

## Layout

- `Shared/` pairing protocol (Bonjour `_googly._tcp`, newline JSON) and colors, used by both apps
- `Mac/` menu bar app (Swift package target `GooglyMac`)
- `iOS/` iPhone app (SwiftUI)
