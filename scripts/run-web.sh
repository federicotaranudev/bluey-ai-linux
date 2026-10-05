#!/usr/bin/env bash
# Bluey in the browser: no iPhone install, no sideloading, no expiry.
#   scripts/run-web.sh [port]
# The Groq key stays on this machine (in the OS keyring), never in the browser.
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
PORT=${1:-8790}
cd "$ROOT"

PYTHON=${PYTHON:-}
if [[ -z "$PYTHON" ]]; then
  if [[ -x "$ROOT/.venv/bin/python" ]]; then PYTHON="$ROOT/.venv/bin/python"; else PYTHON=python3; fi
fi

exec env BLUEY_WEB_PORT="$PORT" "$PYTHON" -m bluey.web