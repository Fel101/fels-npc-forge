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
python server.py --port 8775 --open
```

## Fel's Studio ports

The suite contract assigns these ports.

| What | Port |
|---|---|
| Voice Lab | 8765 |
| NPC Forge | 8766 (was 8765) |
| Fel's Altera | 8767 for new installs (was 5001) |
| A second copy of NPC Forge | 8768 |
| Voice Lab's MCP server over HTTP | 8769 |
| Altera's own voice service | 8770 |
| ComfyUI | 8188 |
| LM Studio | 1234 |
| Ollama | 11434 |

Voice Slate has no network port. Use 8775 when an example calls for a spare port.

## Shared suite files

Fel's Studio owns [`npcgen/fels_suite.py`](npcgen/fels_suite.py) and its shared
[`tests/suite_vectors.json`](tests/suite_vectors.json). Copy both files unchanged from Voice Lab's
`main`. Changes to either file go through the suite contract with the owner's approval.

## Commit history for the suite update

Start each step on its own branch from local `main`. Keep commits small enough to review, preserve them when
merging, and say which checks passed in each commit message. Run the relevant Python tests and the page checks
before each commit. Once the step meets its completion criteria and the full suite passes, fast-forward local
`main`.

## The checks

Run these before you open a pull request:

```bash
python -m pytest                                 # about 2,000 tests; run the files you touched while you work
node --test scripts/check_scene_studio.mjs scripts/check_scene_history.mjs scripts/check_scene_director.mjs scripts/check_scene_motion.mjs scripts/check_voice_workspace.mjs
```

CI runs both on Windows and Linux, with Python 3.10 and 3.12, for every push and pull request.

The full Python suite takes about 20 minutes. While you work, run just the files you touched, for example
`python -m pytest tests/test_roll.py`.

## How the code is laid out

[docs/files.md](docs/files.md) has the map (Layout). In short: `server.py` is the web server (standard library only), `npcgen/` holds everything
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
you can try one without restarting. [docs/customising.md](docs/customising.md) lists every field. Please draw a few portraits with a new setting
or style before you send it, and say which engine you used.

## Making a release

Check for updates and `update.bat` compare `__version__` in `npcgen/__init__.py` with the newest release on GitHub, so a
release is a version number, a tag and a GitHub release that agree:

1. Set `__version__` in `npcgen/__init__.py` and give the version its heading and date in `CHANGELOG.md`
   (`## 1.1.0 (2026-12-01)`). `python scripts/release_notes.py` shows the notes the release will get: that section, with each
   wrapped line joined (GitHub's release page shows every line break) and the repository's links made absolute at the tag.
2. Run every check, commit, then tag that commit `v` and the version (`git tag v1.1.0`) and push the commit and the tag.
3. Pushing the tag publishes the release once the checks pass. [The release workflow](.github/workflows/release.yml) runs
   [the tests](.github/workflows/tests.yml) on the tagged commit, refuses a tag that is not `v` and the version, writes the
   notes, and builds `FelsNPCForge-Setup-<version>.exe`, `FelsNPCForge-<version>.zip` and `SHA256SUMS` from the tag with
   `scripts/build_installer.py` (Inno Setup is on GitHub's Windows runner). It records a GitHub attestation of all three and
   makes the release as a draft with them attached. A second job, on a runner that did not build them, downloads the files
   from the draft, checks their names, hashes and attestations (`scripts/check_release_files.py`), and installs that
   Setup.exe over the release before it (`scripts/check_setup_update.ps1`). Only then is the draft published, and it is
   marked latest only when it is the newest version.

Setup.exe carries the file lists of every release tag up to its version, so an update can remove what the new version
dropped; the build needs the tags (`git fetch --tags`), and the workflows check out the whole history for them. Every push
also builds Setup.exe and installs it over the release before this version (the `setup-exe` job of the tests). To build it
here, install Inno Setup 6.7 or later (`winget install JRSoftware.InnoSetup`) and run `python scripts/build_installer.py`; it
writes to `dist\`. `scripts\check_setup_update.ps1 -Setup dist\FelsNPCForge-Setup-<version>.exe -Uninstall` then checks it
over the previous release, using a port, a ComfyUI address and a suite folder of its own.

When the checks fail on the tag, nothing is published; delete the draft if one was made. Do not move or delete the tag even
then: fix the problem and release the next patch number. When the checks passed and only publishing failed (GitHub was down,
say), run the Release workflow again from the Actions tab with the tag's name.

`update.bat` refuses a release whose `npcgen/__init__.py` names another version than its tag. It compares the new release
with the previous one's ZIP to tell a file the project removed from one a user changed, so do not move or delete a published
tag. Releases are not signed (the suite pays for no signing service); `SHA256SUMS` and the attestations are how people check
a download (README, "Checking a download").
