#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
PYTHON=${BLUEY_PYTHON:-python3}
VENV="$ROOT/.venv"
if [[ ! -x "$VENV/bin/python" ]]; then
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
if "$VENV/bin/python" -m pip --version >/dev/null 2>&1; then
    PIP=("$VENV/bin/python" -m pip)
else
    PIP=("$PYTHON" -m pip --python "$VENV/bin/python")
fi
cd "$ROOT"
"${PIP[@]}" install -e "$ROOT/Desktop[build]"
"$VENV/bin/python" -m PyInstaller --noconfirm --clean --distpath "$ROOT/dist" --workpath "$ROOT/build/desktop" "$ROOT/Desktop/bluey.spec"
tar -czf "$ROOT/dist/BlueyDesktop-ubuntu-$(uname -m).tar.gz" -C "$ROOT/dist" BlueyDesktop
echo "Built $ROOT/dist/BlueyDesktop/BlueyDesktop"
echo "Keep the complete BlueyDesktop folder together when copying it."
