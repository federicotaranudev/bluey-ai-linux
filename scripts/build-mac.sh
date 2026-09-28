#!/bin/zsh
# Builds "Googly Eyes.app" into build/ from the Swift package. Works with just the Command Line Tools.
set -euo pipefail
cd "$(dirname "$0")/.."

swift build -c release --product GooglyMac

APP="build/Googly Eyes.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$(swift build -c release --show-bin-path)/GooglyMac" "$APP/Contents/MacOS/GooglyMac"
cp Mac/Info.plist "$APP/Contents/Info.plist"
cp Shared/Fonts/*.ttf "$APP/Contents/Resources/"

# Sign with your Apple Development certificate when there is one, so macOS remembers the
# Screen Recording and Microphone permissions across rebuilds. Otherwise sign ad hoc.
IDENTITY=$(security find-identity -v -p codesigning | grep -o '"Apple Development[^"]*"' | head -1 | tr -d '"' || true)
codesign --force --sign "${IDENTITY:--}" "$APP"

echo "Built $APP"
echo "Run it with: open \"$APP\""
