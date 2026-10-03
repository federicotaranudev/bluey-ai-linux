#!/usr/bin/env bash
# iPhone signing helpers that run on Ubuntu: no Mac, no Xcode.
#   scripts/ios-signing.sh csr  <common-name>        -> ios.key + ios.csr
#   scripts/ios-signing.sh p12  <cert.cer> <key> <out.p12> [password] -> signing.p12
# Full walkthrough: docs/iphone-ubuntu.md
set -euo pipefail

usage() {
    sed -n '2,5p' "$0" >&2
    exit 64
}

csr() {
    local name=${1:?Usage: scripts/ios-signing.sh csr <common-name>}
    command -v openssl >/dev/null || { echo "openssl is required: sudo apt install openssl" >&2; exit 1; }
    openssl req -new -newkey rsa:2048 -nodes -sha256 \
        -keyout "ios.key" -out "ios.csr" \
        -subj "/emailAddress=$name/O=Bluey/C=US/CN=Bluey iPhone Development"
    echo "Created $PWD/ios.key and $PWD/ios.csr"
    echo "Upload ios.csr at developer.apple.com → Certificates → + → Apple Development."
}

p12() {
    local cer=${1:?Usage: scripts/ios-signing.sh p12 <cert.cer> <key> <out.p12> [password]}
    local key=${2:?Missing private key path}
    local out=${3:?Missing output .p12 path}
    local pass=${4:-}
    local tmp
    tmp=$(mktemp -d)
    trap 'rm -rf "$tmp"' RETURN
    if openssl x509 -inform DER -in "$cer" -noout 2>/dev/null; then
        openssl x509 -inform DER -in "$cer" -out "$tmp/cert.pem"
    else
        openssl x509 -in "$cer" -out "$tmp/cert.pem"
    fi
    # The certificate and the key must belong together, or iOS refuses the app.
    if [ "$(openssl x509 -in "$tmp/cert.pem" -noout -pubkey | openssl md5)" != \
         "$(openssl pkey -in "$key" -pubout 2>/dev/null | openssl md5)" ]; then
        echo "That certificate does not match $key. Use the key that made the CSR." >&2
        exit 1
    fi
    openssl pkcs12 -export -out "$out" -inkey "$key" -in "$tmp/cert.pem" \
        -passout "pass:$pass" -name "Bluey iPhone Development"
    echo "Created $PWD/$out"
    echo "Base64 it for GitHub with: base64 -w0 $out"
}

case "${1:-}" in
    csr) shift; csr "$@" ;;
    p12) shift; p12 "$@" ;;
    *) usage ;;
esac
