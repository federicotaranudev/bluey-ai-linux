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
codesign --force --sign - "$APP"

echo "Built $APP"
echo "Run it with: open \"$APP\""
