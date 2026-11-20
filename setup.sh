#!/bin/sh
# Sets up NPC Forge: a Python environment in .venv with its few packages (Pillow, numpy, xatlas).
# Nothing is started and no model is downloaded. Python 3.10 or newer; 3.12 is tried first.
set -eu
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
    python=""
    for candidate in python3.12 python3.13 python3.11 python3.10 python3; do
        if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 10))'; then
            python=$candidate
            break
        fi
    done
    if [ -z "$python" ]; then
        echo 'Python 3.10 or newer was not found. Install Python 3.12, then run ./setup.sh again.' >&2
        exit 1
    fi
    "$python" -m venv .venv
fi
.venv/bin/python -m pip install --upgrade pip >/dev/null
.venv/bin/python -m pip install -r requirements.txt
echo
echo 'NPC Forge is set up. Start ComfyUI, then run: .venv/bin/python server.py --open'
