#!/usr/bin/env sh
# Updates NPC Forge to its newest release on GitHub. It shows what will change and asks first, and keeps your library,
# config.json, local/ and .venv. --check only says whether there is a newer release, --dry-run shows the plan, and --undo
# puts back the version the last update replaced. Close NPC Forge before you run it.
cd "$(dirname "$0")" || exit 1
if [ -x .venv/bin/python ]; then PY=.venv/bin/python; else PY=python3; fi
exec "$PY" scripts/update.py "$@"
