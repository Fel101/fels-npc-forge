# Contributing

Thank you for helping. Bug reports, fixes, new settings and styles, and documentation corrections are all welcome. For a
larger change, open an issue first to describe it, so no one spends a weekend on something that doesn't fit.

Security problems go through private reporting instead: see [SECURITY.md](SECURITY.md).

## Getting set up

You need Python 3.10 or newer (CI runs 3.10 and 3.12 on Windows and Linux) and, for the page checks, Node.js 20 or newer.
From the repository folder:

```bash
setup.bat                      # Windows; on Linux or macOS: ./setup.sh
.venv\Scripts\python -m pip install pytest
```

The tests need no GPU and no ComfyUI: the API tests point ComfyUI at a closed port on purpose. To try a change against a real
ComfyUI without touching your own characters, give it a library of its own and another port:

```bash
set NPCGEN_LIBRARY=%TEMP%\npcforge-try          # Linux or macOS: export NPCGEN_LIBRARY=/tmp/npcforge-try
python server.py --port 8766 --open
```

## The checks

Run these before you open a pull request:

```bash
python -m pytest                                 # about 2,000 tests; run the files you touched while you work
node --test scripts/check_scene_studio.mjs scripts/check_scene_history.mjs scripts/check_soft_preview.mjs scripts/check_scene_director.mjs scripts/check_scene_motion.mjs scripts/check_voice_workspace.mjs
```

CI runs both on Windows and Linux, with Python 3.10 and 3.12, for every push and pull request.

The full Python suite takes about 20 minutes. While you work, run just the files you touched, for example
`python -m pytest tests/test_roll.py`.

## How the code is laid out

`README.md` has the map (Layout). In short: `server.py` is the web server (standard library only), `npcgen/` holds everything
else, `web/` is the page (plain JavaScript modules, no build step), `data/` holds the tables a roll draws from, and `docs/`
explains each feature with what was measured.

Some habits the code follows:

- **Prompts are measured, not guessed.** A change to a prompt says what it was tried on and what came out (the docs keep the
  numbers, failures included).
- **Nothing is quietly replaced.** When a model cannot run, the step fails and says why, or says which model drew it instead.
- **Optional repair steps stay optional.** Clean-up and enhancement steps are opt-in buttons, never automatic.
- **Every model has a licence entry** in `data/licences.json`; a test refuses a picker that offers a model without one.
- **The page talks plainly.** Labels and messages say what happens, in everyday words.

## Adding a setting or an art style

A setting is one file in `data/settings/`; an art style is an entry in `data/styles.json`. Both are re-read on every roll, so
you can try one without restarting. `README.md` (Customising) lists every field. Please draw a few portraits with a new setting
or style before you send it, and say which engine you used.
