#!/usr/bin/env bash
# Install a built .ipa on a plugged-in iPhone from Ubuntu.
#   scripts/install-iphone.sh path/to/GooglyEyes.ipa
set -euo pipefail

IPA=${1:-}
[[ -n "$IPA" ]] || { echo "Usage: scripts/install-iphone.sh path/to/GooglyEyes.ipa" >&2; exit 64; }
[[ -f "$IPA" ]] || { echo "No such file: $IPA" >&2; exit 66; }

for tool in ideviceinfo idevicepair ideviceinstaller; do
    command -v "$tool" >/dev/null || {
        echo "$tool is missing. Install it with: sudo apt install libimobiledevice ideviceinstaller" >&2
        exit 1
    }
done

if ! ideviceinfo >/dev/null 2>&1; then
    echo "Plug in the iPhone (unlock it) and accept 'Trust This Computer' on the screen."
    idevicepair pair || { echo "Pairing failed. Unlock the phone and try again." >&2; exit 1; }
fi

echo "Installing $IPA …"
if ! ideviceinstaller -i "$IPA"; then
    echo "Install failed; removing any previous copy and retrying."
    ideviceinstaller -U co.visionairy.googly.phone >/dev/null 2>&1 || true
    ideviceinstaller -i "$IPA"
fi

echo
echo "Done. On the phone: Settings → General → VPN & Device Management → trust"
echo "'Apple Development: …' the first time, then open 'Googly Eyes'."
