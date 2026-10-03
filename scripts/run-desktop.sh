#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
PYTHON=${BLUEY_PYTHON:-python3}
VENV="$ROOT/.venv"

if [[ ! -x "$VENV/bin/python" ]]; then
    "$PYTHON" -c 'import sys; assert (3, 11) <= sys.version_info < (3, 15), "Bluey requires Python 3.11–3.14; Python 3.12 is recommended."'
    # Ubuntu ships pip's bootstrap as the separate python3-venv package. Without
    # it, create the environment pip-less and let pip install into it afterwards.
    if "$PYTHON" -c 'import ensurepip' >/dev/null 2>&1; then
        "$PYTHON" -m venv --system-site-packages "$VENV"
    else
        echo "python3-venv is not installed; creating $VENV without its own pip."
        "$PYTHON" -m venv --without-pip --system-site-packages "$VENV"
    fi
fi

# Uses the environment's pip when present, otherwise the system pip pointed at it.
pip_install() {
    if "$VENV/bin/python" -m pip --version >/dev/null 2>&1; then
        "$VENV/bin/python" -m pip "$@"
    else
        "$PYTHON" -m pip --python "$VENV/bin/python" "$@"
    fi
}

if ! "$VENV/bin/python" -c 'import importlib.metadata; importlib.metadata.version("bluey-desktop")' >/dev/null 2>&1; then
    pip_install install -e "$ROOT/Desktop"
fi
cd "$ROOT"
exec "$VENV/bin/python" -m bluey "$@"
