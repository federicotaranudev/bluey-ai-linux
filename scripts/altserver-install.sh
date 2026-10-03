#!/usr/bin/env bash
# Sign and install the iPhone app with a FREE Apple ID, from Ubuntu, no Mac.
#   scripts/altserver-install.sh [path/to/GooglyEyes-unsigned.ipa]
#
# With no path it downloads the newest .ipa from the repository's Actions run.
# AltServer creates the certificate and provisioning profile with your Apple ID,
# so no Apple Developer Program membership is needed. Apps then expire after
# 7 days — run this script again with a fresh .ipa to re-sign.
set -euo pipefail

ALTSERVER=${ALTSERVER:-$HOME/altserver/AltServer}
ANISETTE=${ALTSERVER_ANISETTE_SERVER:-http://localhost:6969}
REPO=${BLUEY_REPO:-federicotaranudev/bluey-ai-linux}
IPA=${1:-}
UDID=${UDID:-$(idevice_id -l 2>/dev/null | head -1 || true)}

if ! command -v idevice_id >/dev/null; then
    echo "libimobiledevice is missing: sudo apt install libimobiledevice-utils" >&2
    exit 1
fi
if [[ ! -x "$ALTSERVER" ]]; then
    echo "AltServer-Linux not found at $ALTSERVER" >&2
    echo "Download it with:" >&2
    echo "  mkdir -p ~/altserver && curl -fsSL https://github.com/NyaMisty/AltServer-Linux/releases/download/v0.0.5/AltServer-x86_64 -o ~/altserver/AltServer && chmod +x ~/altserver/AltServer" >&2
    exit 1
fi
if [[ -z "$UDID" ]]; then
    echo "No iPhone found. Plug it in, unlock it, and accept 'Trust This Computer'." >&2
    exit 1
fi

# Anisette data is required for Apple's login handshake. The public servers come
# and go, so a local Docker one is the reliable choice.
if ! curl -fsS --max-time 5 "$ANISETTE/" >/dev/null 2>&1; then
    echo "No anisette server at $ANISETTE. Start one with:"
    echo "  docker run -d --restart unless-stopped --name anisette -p 6969:6969 dadoum/anisette-v3-server"
    exit 1
fi

if [[ -z "$IPA" ]]; then
    command -v gh >/dev/null || { echo "gh not found; pass the .ipa path as the first argument." >&2; exit 1; }
    WORK=$(mktemp -d)
    echo "Downloading the newest .ipa from $REPO …"
    RUN=$(gh run list -R "$REPO" --workflow "iPhone app (.ipa)" --status success --limit 1 --json databaseId -q '.[0].databaseId')
    [[ -n "$RUN" ]] || { echo "No successful workflow run found in $REPO (Actions → iPhone app (.ipa) → Run workflow)." >&2; exit 1; }
    gh run download "$RUN" -R "$REPO" -n GooglyEyes-unsigned-ipa -D "$WORK"
    IPA=$(ls "$WORK"/*.ipa | head -1)
fi

echo
echo "Device UDID: $UDID"
echo "Apple ID (the email you sign in to the App Store with):"
read -r -p "  " APPLE_ID
echo "Password. Prefer an app-specific password"
echo "  (appleid.apple.com → Sign-In and Security → App-Specific Passwords)."
read -r -s -p "  " APPLE_PASSWORD
echo
echo "If you use two-factor authentication, the code Apple sends will be asked for next."
echo

export ALTSERVER_ANISETTE_SERVER="$ANISETTE"
"$ALTSERVER" -u "$UDID" -a "$APPLE_ID" -p "$APPLE_PASSWORD" "$IPA"

echo
echo "On the phone: Settings → Privacy & Security → Developer Mode → turn it on and"
echo "reboot the first time. Then Settings → General → VPN & Device Management →"
echo "trust the 'Apple Development' profile, and open Googly Eyes."
