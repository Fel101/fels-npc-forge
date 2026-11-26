"""Update NPC Forge to its newest release on GitHub, keeping everything that is yours.

    update.bat                 show the plan and ask before changing anything
    update.bat --check         only say whether there is a newer release
    update.bat --dry-run       show the plan; change nothing
    update.bat --yes           accept the plan without asking
    update.bat --undo          put back the version the last update replaced

Close NPC Forge first: the update refuses while it runs from this folder.

A folder made with git clone is moved to the release's tag with git, as a fast-forward; it refuses when files git tracks were
changed or commits were made here. A folder that came from a ZIP gets the release's files from GitHub: the update downloads the
new release and the one this folder came from, replaces the files NPC Forge ships, removes the ones the new version no longer
has, and leaves every other file alone. Your library, config.json, local\\ (your extensions, settings and these backups) and the
.venv folder are never touched. Every file it replaces or removes is first copied to local\\update\\<from>-to-<to>, so --undo can
put the previous version back; a shipped file you had changed yourself is named at the end, and its copy is there.

Once the files are in place, the new version's own update.py runs with --finish: it installs the Python packages again when
requirements.txt changed, removes the folders of dropped code that held nothing but Python's caches, and leaves a note the page
shows once.

Setup.exe of a newer version, run over an installed NPC Forge, is an update too. It removes the files the new version dropped,
copies the new files and ends with --after-setup (installer/FelsNPCForge.iss). This records the update as "setup", keeps a copy
of each dropped file, and finishes with the packages from Setup's own wheels. Setup keeps no copy of the files it replaced, so
--undo refuses that update.

Standard library only.
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import textwrap
import time
import urllib.error
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from npcgen import __version__ as VERSION  # noqa: E402
from npcgen import updates  # noqa: E402

FOLDER = "local/update"
RECORD = "last.json"
NOTICE = "notice.json"
RUNNING = "local/running.json"              # written by server.py while it runs (pid, port)
DOWNLOADS = "downloads"
BACKUPS_KEPT = 3
DOWNLOAD_LIMIT = 300 * 10**6
WIDTH = 110
NOTES_LINES = 12
APP_CLOSE_SECONDS = 120
CLOSE_SECONDS = 10
SCHEMA = 1
# The file lists of the releases Setup.exe carries for --after-setup (scripts/build_installer.py writes them).
SETUP_FILES_SCHEMA = 1
# python: NPC Forge's own Python (Setup.exe, setup.bat); dist: release files built here (scripts/build_installer.py).
PROTECTED_FOLDERS = {".git", "library", "local", "animations", "__pycache__", ".pytest_cache", ".ruff_cache", "python", "dist"}
PROTECTED_FILES = {"config.json", ".env"}
REQUIREMENTS = "requirements.txt"
# Folders version 1.0 made for its own voice engines (scripts/install_voice.py). Voices come from Voice Lab since 1.1, so an
# update from 1.0 says they can go; it never deletes them.
VOICE_FOLDERS = ("voice-envs", "voice-models")
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
NEW_CONSOLE = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)


class UpdateStopped(Exception):
    """The update cannot go on; the message says why and what to do."""


def say(text=""):
    print(text, flush=True)


def fill(text, **options):
    return textwrap.fill(text, width=WIDTH, **options)


def now_text():
    return time.strftime("%Y-%m-%d %H:%M:%S")


# ------------------------------------------------------------------------------------------------ is NPC Forge running here?

def process_alive(pid):
    """Whether a process with this id runs now. (On Windows os.kill(pid, 0) would send Ctrl+C, so it asks Windows instead.)"""
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        handle = kernel32.OpenProcess(0x1000, False, pid)               # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = wintypes.DWORD()
            kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
            return code.value == 259                                      # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def process_images():
    """{process id: the program it runs} for the processes Windows lets this user see; {} anywhere else."""
    if os.name != "nt":
        return {}
    import ctypes
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.QueryFullProcessImageNameW.argtypes = (wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD))
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    psapi.EnumProcesses.restype = wintypes.BOOL
    psapi.EnumProcesses.argtypes = (ctypes.POINTER(wintypes.DWORD), wintypes.DWORD, ctypes.POINTER(wintypes.DWORD))
    count = 1024
    while True:
        ids = (wintypes.DWORD * count)()
        used = wintypes.DWORD()
        if not psapi.EnumProcesses(ids, ctypes.sizeof(ids), ctypes.byref(used)):
            return {}
        if used.value < ctypes.sizeof(ids):
            break
        count *= 2
    images = {}
    for pid in ids[: used.value // ctypes.sizeof(wintypes.DWORD)]:
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            continue
        try:
            size = wintypes.DWORD(32768)
            name = ctypes.create_unicode_buffer(size.value)
            if kernel32.QueryFullProcessImageNameW(handle, 0, name, ctypes.byref(size)):
                images[int(pid)] = name.value
        finally:
            kernel32.CloseHandle(handle)
    return images


def app_processes(root, *, images=None, own=None, alive=process_alive):
    """[(process id, what)] for NPC Forge running from this folder: the server named in local/running.json while it is alive,
    and any program running from this folder's .venv (a venv's python.exe on Windows stays running beside the Python it
    starts, so both are found). This updater and its own launcher are left out."""
    root = Path(root)
    own = {os.getpid(), os.getppid()} if own is None else set(own)
    found = {}
    try:
        running = json.loads((root / RUNNING).read_text(encoding="utf-8"))
        pid = running.get("pid")
        own.discard(pid)                     # Update now: the server started this updater, so it is the parent, and still waited for
        if alive(pid):
            found[pid] = f"NPC Forge's server on port {running.get('port')}"
    except (OSError, ValueError, AttributeError):
        pass
    folder = os.path.normcase(os.path.abspath(root)).rstrip("\\/") + os.sep
    for pid, image in (process_images() if images is None else images).items():
        if pid in own or pid in found:
            continue
        path = os.path.normcase(os.path.abspath(image))
        if path.startswith(folder) and path[len(folder):].split(os.sep, 1)[0].startswith(".venv"):
            found[pid] = image
    return sorted(found.items())


def running_problem(running):
    shown = "; ".join(f"{what} (process {pid})" for pid, what in running[:4])
    more = f" and {len(running) - 4} more" if len(running) > 4 else ""
    return f"NPC Forge is running from this folder: {shown}{more}. Close its window (or press Ctrl+C in it), then run update.bat again."


# ------------------------------------------------------------------------------------------------ what an update may touch

def protected(path):
    """Whether a path in a release (relative, with /) is one an update never writes, replaces or removes."""
    parts = PurePosixPath(path).parts
    first = parts[0].lower() if parts else ""
    if first.startswith(".venv") or first in PROTECTED_FOLDERS:
        return True
    return len(parts) == 1 and (first in PROTECTED_FILES or (first.startswith(".env.") and first != ".env.example"))


def read_bytes(path):
    try:
        return Path(path).read_bytes()
    except (FileNotFoundError, NotADirectoryError):
        return None


def write_file(path, data, *, attempts=5, pause=None):
    """Write a file whole: into a temporary file beside it, then over it, tried again for a few seconds because a virus scanner
    may still hold the file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".update-tmp")
    temporary.write_bytes(data)
    for attempt in range(attempts):
        try:
            os.replace(temporary, path)
            return
        except PermissionError:
            if attempt + 1 == attempts:
                temporary.unlink(missing_ok=True)
                raise
            (pause or time.sleep)(1)


def remove_file(root, path):
    """Remove a file the new version no longer has, and the folders it leaves empty (never NPC Forge's folder itself)."""
    target = Path(root) / path
    try:
        target.unlink(missing_ok=True)
    except NotADirectoryError:
        return
    folder = target.parent
    while folder != Path(root) and folder.is_dir() and not any(folder.iterdir()):
        folder.rmdir()
        folder = folder.parent


# ------------------------------------------------------------------------------------------------ releases as ZIP files

def download(url, destination, *, opener=None, limit=DOWNLOAD_LIMIT):
    """Save a GitHub download to destination; None when GitHub has no such file (404)."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".partial")
    try:
        with updates.open_url(url, opener=opener, timeout=60) as response, partial.open("wb") as output:
            total = 0
            while chunk := response.read(1 << 20):
                total += len(chunk)
                if total > limit:
                    raise UpdateStopped(f"{url} is larger than {limit // 10**6} MB, which no release of NPC Forge is. Nothing was changed.")
                output.write(chunk)
    except urllib.error.HTTPError as exc:
        partial.unlink(missing_ok=True)
        if exc.code == 404:
            return None
        raise UpdateStopped(f"GitHub answered {exc.code} {exc.reason} for {url}. Nothing was changed; try again later.") from exc
    except updates.NETWORK_ERRORS as exc:
        partial.unlink(missing_ok=True)
        raise UpdateStopped(updates.network_problem(exc) + " Nothing was changed.") from exc
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    os.replace(partial, destination)
    return destination


def read_archive(path):
    """{path: bytes} for the files in a GitHub source ZIP, below the one folder it holds. Refuses links and paths that leave
    that folder; zipfile checks every file's CRC as it reads it."""
    try:
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            if len({entry.filename.split("/", 1)[0] for entry in entries}) != 1:
                raise UpdateStopped(f"{Path(path).name} is not a release of NPC Forge: it does not hold one folder. Nothing was changed.")
            files = {}
            for entry in entries:
                inside = entry.filename.split("/", 1)[1] if "/" in entry.filename else ""
                if entry.is_dir() or not inside:
                    continue
                pure = PurePosixPath(inside)
                if pure.is_absolute() or ".." in pure.parts or "\\" in inside or ":" in inside:
                    raise UpdateStopped(f"{Path(path).name} names a path the update will not write: {inside}. Nothing was changed.")
                if (entry.external_attr >> 16) & 0o170000 == 0o120000:
                    raise UpdateStopped(f"{Path(path).name} holds a link, {inside}, which no release of NPC Forge has. Nothing was changed.")
                files[pure.as_posix()] = archive.read(entry)
            return files
    except (zipfile.BadZipFile, OSError) as exc:
        raise UpdateStopped(f"{Path(path).name} could not be read as a ZIP file ({exc}). Run update.bat again to download it again.") from exc


def archive_version(files):
    """The __version__ a release's npcgen/__init__.py names, or None."""
    for line in (files.get("npcgen/__init__.py") or b"").decode("utf-8", "replace").splitlines():
        if line.startswith("__version__ = "):
            return line.split("=", 1)[1].strip().strip("\"'")
    return None


def zip_plan(root, new_files, old_files):
    """What replacing this folder's copy of the release old_files (None when GitHub does not have it) with new_files does:
    {"add", "replace", "remove", "keep", "yours", "same", "protected", "changed"}, lists of paths except "same", a count."""
    root = Path(root)
    plan = {"add": [], "replace": [], "remove": [], "keep": [], "yours": [], "same": 0, "protected": [], "changed": []}
    for path, data in sorted(new_files.items()):
        if protected(path):
            plan["protected"].append(path)
            continue
        local = read_bytes(root / path)
        if local == data:
            plan["same"] += 1
            continue
        plan["add" if local is None else "replace"].append(path)
        if local is not None and old_files is not None and old_files.get(path) != local:
            plan["yours"].append(path)                                  # changed here since its release: its copy goes in the backup
    for path, data in sorted((old_files or {}).items()):
        if path in new_files or protected(path):
            continue
        local = read_bytes(root / path)
        if local is None:
            continue
        (plan["remove"] if local == data else plan["keep"]).append(path)
    if old_files is None:
        plan["changed"] = plan["add"] + plan["replace"]
    else:
        plan["changed"] = sorted(p for p in set(new_files) | set(old_files) if new_files.get(p) != old_files.get(p) and not protected(p))
    return plan


def backup_name(old, new):
    return f"{old}-to-{new}"


def shown_path(path):
    return str(path).replace("/", os.sep)


def apply_zip(root, plan, new_files, record):
    """Copy every file the plan replaces or removes into the record's backup, then write the new files and remove the old ones.
    Anything that fails, Ctrl+C included, puts back what was already done before it is raised."""
    root = Path(root)
    backup = root / record["backup"] / "files"
    for path in plan["replace"] + plan["remove"]:
        target = backup / path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / path, target)
    record.update({"added": list(plan["add"]), "replaced": list(plan["replace"]), "removed": list(plan["remove"]),
                   "yours": list(plan["yours"]), "kept": list(plan["keep"]), "state": "applying"})
    save_record(root, record)
    try:
        for path in plan["add"] + plan["replace"]:
            write_file(root / path, new_files[path])
        for path in plan["remove"]:
            remove_file(root, path)
    except BaseException as exc:
        restore_zip(root, record)
        record["state"] = "rolled back"
        save_record(root, record)
        if isinstance(exc, OSError):
            raise UpdateStopped(f"A file could not be written ({exc}). Everything the update had changed was put back. Close any "
                                "program that has a file of NPC Forge open, then run update.bat again.") from exc
        raise
    record["state"] = "applied"
    save_record(root, record)


def restore_zip(root, record):
    """Put back what an update from a ZIP replaced and removed, from its backup, and remove what it added."""
    root = Path(root)
    backup = root / record["backup"] / "files"
    for path in record.get("added", []):
        if not protected(path):
            remove_file(root, path)
    for path in record.get("replaced", []) + record.get("removed", []):
        data = read_bytes(backup / path)
        if data is not None and not protected(path):
            write_file(root / path, data)


def prune_backups(root, keep=BACKUPS_KEPT):
    folder = Path(root) / FOLDER
    backups = sorted((p for p in folder.glob("*-to-*") if p.is_dir()), key=lambda p: p.stat().st_mtime)
    for old in backups[:-keep] if keep else backups:
        shutil.rmtree(old, ignore_errors=True)


# ------------------------------------------------------------------------------------------------ releases with git

def git_checkout(root):
    return (Path(root) / ".git").exists()


def git(root, *args, runner=subprocess.run, check=True):
    done = runner(["git", "-C", str(root), *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
                  creationflags=NO_WINDOW)
    if check and done.returncode:
        message = "\n".join(((done.stderr or "") + (done.stdout or "")).strip().splitlines()[-6:])
        raise UpdateStopped(f"git {' '.join(args)} stopped:\n{message}\nNothing was changed.")
    return done


def git_plan(root, tag, *, source=None, runner=subprocess.run):
    """Fetch the release's tag and check that this checkout can move to it: no changed tracked files, and no commits of its
    own. {"from", "to", "changed"} with the two commits and the files that differ."""
    if shutil.which("git") is None and runner is subprocess.run:
        raise UpdateStopped("This folder is a Git checkout and git is not on PATH. Install Git, or update it with the git you use. "
                            "Nothing was changed.")
    changed_here = git(root, "status", "--porcelain", "--untracked-files=no", runner=runner).stdout.splitlines()
    if changed_here:
        shown = ", ".join(line[3:] for line in changed_here[:5]) + (" and more" if len(changed_here) > 5 else "")
        raise UpdateStopped(f"Files git tracks were changed here: {shown}. Commit or stash them (or undo them) and run update.bat "
                            "again, or update with git yourself. Nothing was changed.")
    source = source or f"https://github.com/{updates.REPOSITORY}.git"
    git(root, "fetch", "--quiet", "--no-tags", source, f"refs/tags/{tag}:refs/tags/{tag}", runner=runner)
    old = git(root, "rev-parse", "HEAD", runner=runner).stdout.strip()
    new = git(root, "rev-parse", f"refs/tags/{tag}^{{commit}}", runner=runner).stdout.strip()
    ancestor = git(root, "merge-base", "--is-ancestor", "HEAD", f"refs/tags/{tag}", runner=runner, check=False)
    if ancestor.returncode == 1:
        raise UpdateStopped(f"This checkout has commits that {tag} does not, so it cannot simply move to it. Update it with git "
                            "yourself (git merge, or git rebase). Nothing was changed.")
    if ancestor.returncode:
        git(root, "merge-base", "--is-ancestor", "HEAD", f"refs/tags/{tag}", runner=runner)
    changed = git(root, "diff", "--name-only", old, new, runner=runner).stdout.splitlines()
    return {"from": old, "to": new, "changed": sorted(changed)}


# ------------------------------------------------------------------------------------------------ the record and the notice

def record_path(root):
    return Path(root) / FOLDER / RECORD


def save_record(root, record):
    write_file(record_path(root), (json.dumps(record, indent=2) + "\n").encode("utf-8"))


def read_record(root):
    try:
        record = json.loads(record_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return record if isinstance(record, dict) and record.get("schema") == SCHEMA else None


def release_url(tag):
    return f"https://github.com/{updates.REPOSITORY}/releases/tag/{tag}" if tag else f"https://github.com/{updates.REPOSITORY}/releases"


def notice_path(root):
    return Path(root) / FOLDER / NOTICE


def save_notice(root, notice):
    """What the page says the next time it opens."""
    data = json.dumps({"schema": SCHEMA, "at": now_text(), **notice}, indent=2) + "\n"
    write_file(notice_path(root), data.encode("utf-8"))


# ------------------------------------------------------------------------------------------------ the plan

def release_lines(release):
    date = (release.get("published_at") or "")[:10]
    lines = [f"{release['name']}{', released ' + date if date else ''}: {release['url']}"]
    notes = [line.rstrip() for line in (release.get("notes") or "").strip().splitlines()]
    if notes:
        lines += ["", *notes[:NOTES_LINES]]
        if len(notes) > NOTES_LINES:
            lines.append(f"... the rest is on the release page ({len(notes) - NOTES_LINES} more lines).")
    return "\n".join(lines)


def names(paths, limit=6):
    return ", ".join(paths[:limit]) + (f" and {len(paths) - limit} more" if len(paths) > limit else "")


KEPT = "Your library, config.json, local\\ and the .venv folder are not touched."


def zip_plan_sentences(plan, old, new, old_known):
    """(the steps, the notes) of an update from a ZIP, one sentence or a few each, unwrapped."""
    counts = f"{len(plan['replace'])} to replace, {len(plan['add'])} new, {len(plan['remove'])} to remove"
    first = f"NPC Forge's own files, from the release on GitHub: {counts}; {plan['same']} are unchanged. {KEPT}"
    if not old_known:
        first += f" GitHub has no release {old} to compare with, so files that version had and {new} does not are left in place."
    steps = [first,
             f"First a copy of every file it replaces or removes goes to {shown_path(FOLDER)}{os.sep}{backup_name(old, new)}, so "
             f"update.bat --undo can put version {old} back.",
             "Then the new version's last step: the Python packages are installed again when requirements.txt changed."]
    notes = []
    if plan["yours"]:
        notes.append(f"Changed here since version {old}, so your copy goes in the backup and the new one replaces it: {names(plan['yours'])}.")
    if plan["keep"]:
        notes.append(f"Version {new} no longer has these, and they were changed here, so they are left in place: {names(plan['keep'])}.")
    if plan["protected"]:
        notes.append(f"The release holds files in folders an update never writes, so they are left out: {names(plan['protected'])}.")
    return steps, notes


def git_plan_sentences(plan, tag, old):
    steps = [f"git moves this checkout from {plan['from'][:7]} to {tag} ({plan['to'][:7]}), a fast-forward: {len(plan['changed'])} "
             "files change. Files git does not track (your library, config.json, local\\ and the .venv folder) are not touched.",
             f"update.bat --undo moves it back to {plan['from'][:7]}, version {old} (git reset --keep).",
             "Then the new version's last step: the Python packages are installed again when requirements.txt changed."]
    return steps, []


def plan_text(steps, notes):
    numbered = "\n".join(fill(text, initial_indent=f"{n}. ", subsequent_indent="   ") for n, text in enumerate(steps, 1))
    return numbered + "".join("\n\n" + fill(note) for note in notes)


def confirmed(args, *, ask=input, interactive=None):
    if args.yes:
        return True
    if not (sys.stdin.isatty() if interactive is None else interactive):
        raise UpdateStopped("This is not an interactive window, so there is nobody to ask. Nothing was changed. Add --yes to accept the plan.")
    try:
        return ask("Type yes to update, or press Enter to stop: ").strip().lower() in {"y", "yes"}
    except EOFError:
        return False


# ------------------------------------------------------------------------------------------------ the last step

def venv_python(root):
    for candidate in (Path(root) / ".venv" / "Scripts" / "python.exe", Path(root) / ".venv" / "bin" / "python"):
        if candidate.is_file():
            return candidate
    return None


def only_caches(folder):
    """Whether a folder holds nothing but Python's caches (__pycache__ folders of .pyc files), at any depth."""
    for entry in Path(folder).iterdir():
        if entry.is_symlink() or not entry.is_dir():
            return False
        if entry.name == "__pycache__":
            if any(not item.is_file() or item.suffix != ".pyc" for item in entry.iterdir()):
                return False
        elif not only_caches(entry):
            return False
    return True


def clear_leftovers(root, gone):
    """Remove the folders of dropped files that now hold nothing but Python's caches, such as npcgen/voice/ after 1.0's voice
    code went: a folder of .pyc files alone would still import as an empty package. Returns the folders removed, relative."""
    root = Path(root).resolve()
    removed = []
    for path in sorted(gone):
        if protected(path) or (root / path).exists():
            continue
        folder, top = (root / path).parent, None
        while folder != root and folder.is_dir() and only_caches(folder):
            top, folder = folder, folder.parent
        if top is None:
            continue
        shown = top.relative_to(root).as_posix()
        shutil.rmtree(top, ignore_errors=True)
        removed.append(shown)
        parent = top.parent
        while parent != root and parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()
            parent = parent.parent
    return removed


def folder_bytes(folder):
    total = 0
    for base, _folders, files in os.walk(folder):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(base, name))
            except OSError:
                pass
    return total


def leftover_notes(root, record, *, measure=folder_bytes):
    """Notes for an update from a version before 1.1: the folders its voice engines used, which nothing uses now."""
    from_version = updates.parse_version(record.get("from"))
    if record.get("state") != "applied" or from_version is None or from_version >= (1, 1, 0):
        return []
    found = [name for name in VOICE_FOLDERS if (Path(root) / name).is_dir()]
    if not found:
        return []
    size = sum(measure(Path(root) / name) for name in found) / 1e9
    shown = " and ".join(f"{name}{os.sep}" for name in found)
    return [f"NPC Forge now speaks with Voice Lab's voices, so {shown} ({size:.1f} GB), where version {record['from']} kept its "
            "own voice engines, are no longer used. Delete them when you no longer need them; the update left them in place."]


def finish(root, *, runner=subprocess.run, say=say, wheels=None, problems=()):
    """The new version's last step (or, after --undo, the old version's): install the packages again when requirements.txt
    changed (from Setup's wheels when it gives them), clear folders of dropped code, and leave the page a note."""
    root = Path(root)
    record = read_record(root)
    if record is None or record.get("state") not in ("applied", "undone"):
        say("There is no finished update or undo to complete.")
        return 0
    status, problems = 0, list(problems)
    if REQUIREMENTS in set(record.get("changed", [])):
        if venv_python(root) is None:
            problems.append("requirements.txt changed. Install the packages it lists again: python -m pip install -r requirements.txt "
                            "(or run setup.bat, which makes the .venv folder start.bat uses).")
            say(fill(problems[-1]))
        else:
            say("The packages in requirements.txt changed: updating .venv (pip prints its own progress).")
            code = runner([sys.executable, str(root / "scripts" / "setup_core.py"), *(["--wheels", str(wheels)] if wheels else [])],
                          cwd=root).returncode
            if code:
                status = 1
                problems.append(f"Installing the packages stopped with exit code {code}, so .venv may still have the old ones. Fix "
                                "what its message says, then run update.bat --finish to try again.")
                say(fill(problems[-1]))
    if record["state"] == "applied":
        gone = record.get("changed") if record.get("method") == "git" else record.get("removed")
        cleared = clear_leftovers(root, gone or [])
        if cleared:
            say(fill(f"Removed folders of code version {record['to']} dropped, which held only Python's caches: {names(cleared)}."))
    notes = leftover_notes(root, record)
    for note in notes:
        say(fill(note))
    try:
        save_notice(root, {"state": "updated" if record["state"] == "applied" else "undone", "from": record["from"], "to": record["to"],
                           "release_url": release_url(record.get("tag")), "problems": problems, "notes": notes})
    except OSError as exc:
        say(f"The note for NPC Forge's page could not be written ({exc}).")
    if record["state"] == "applied":
        say(f"Updated to version {record['to']}. Start NPC Forge as usual; update.bat --undo puts version {record['from']} back.")
    else:
        say(f"Back to version {record['from']}. Start NPC Forge as usual.")
    if record.get("yours"):
        say(fill(f"Your own copies of the files you had changed are in {shown_path(record.get('backup', FOLDER))}{os.sep}files: "
                 f"{names(record['yours'])}."))
    return status


def digest(path):
    """A file's SHA-256, or None when it is not there."""
    data = read_bytes(path)
    return None if data is None else hashlib.sha256(data).hexdigest()


def read_setup_files(path):
    """The file lists Setup.exe carries: {"this": {path: SHA-256} of the version it installs, "releases": {version: {path:
    SHA-256}} of each release up to it}."""
    try:
        lists = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise UpdateStopped(f"Setup's list of the releases' files could not be read ({exc}).") from exc
    if not isinstance(lists, dict) or lists.get("schema") != SETUP_FILES_SCHEMA:
        raise UpdateStopped("Setup's list of the releases' files is not one this version can read.")
    return lists


def summary_text(root, record, kept):
    """What Setup.exe's last page says after --after-setup: the update, then what the notice says (finish)."""
    try:
        notice = json.loads(notice_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        notice = {}
    paragraphs = [f"Updated from version {record['from']} to {record['to']}. Your characters, settings and extensions are as they were."]
    paragraphs.extend(notice.get("problems") or [])
    paragraphs.extend(notice.get("notes") or [])
    if kept:
        paragraphs.append(f"Version {record['to']} no longer has these files, which you had changed, so they were left: {names(kept)}.")
    return "\n\n".join(paragraphs) + "\n"


def after_setup(root, *, args, runner=subprocess.run, say=say):
    """--after-setup: Setup.exe has copied this version over version `args.from_version` in this folder. Finish as update.bat
    does once its files are in place: keep a copy of each dropped file (those [InstallDelete] removed too), leave files
    changed here in place, and run finish with Setup's wheels. Its last page shows the summary (`args.summary`)."""
    root = Path(root)
    lists = read_setup_files(args.setup_files)
    this, released = lists["this"], lists["releases"].get(args.from_version)
    record = {"schema": SCHEMA, "from": args.from_version, "to": VERSION, "tag": f"v{VERSION}", "at": now_text(), "method": "setup",
              "backup": f"{FOLDER}/{backup_name(args.from_version, VERSION)}", "yours": []}
    removed, kept, problems = [], [], []
    if released is None:
        # A version that was never released, such as a build of your own: what it had is not known.
        changed = [REQUIREMENTS]
        problems.append(f"This Setup does not have the list of version {args.from_version}'s files, so any file that version had "
                        f"and {VERSION} does not was left in place.")
    else:
        changed = sorted(path for path in set(this) | set(released) if this.get(path) != released.get(path) and not protected(path))
        backup = root / record["backup"] / "files"
        staged = Path(args.setup_files).with_name("retired")
        for path in sorted(set(released) - set(this)):
            if protected(path):
                continue
            local = digest(root / path)
            if local is not None and local != released[path]:
                kept.append(path)
                continue
            source = root / path if local is not None else staged / path
            if local is None and digest(source) != released[path]:
                continue                                                 # gone before Setup ran: nothing to keep
            (backup / path).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, backup / path)
            remove_file(root, path)
            removed.append(path)
    record.update({"changed": changed, "added": sorted(set(this) - set(released or {})),
                   "replaced": sorted(path for path in set(this) & set(released or {}) if this[path] != released[path]),
                   "removed": removed, "kept": kept, "state": "applied"})
    save_record(root, record)
    if removed:
        say(f"Removed {len(removed)} file(s) version {VERSION} no longer has: {names(removed)}.")
    if kept:
        say(fill(f"Left in place because you had changed them, though version {VERSION} no longer has them: {names(kept)}."))
    status = finish(root, runner=runner, say=say, wheels=args.wheels, problems=problems)
    if args.summary:
        try:
            write_file(Path(args.summary), summary_text(root, record, kept).encode("ascii", "replace"))   # Setup reads plain text
        except OSError as exc:
            say(f"Setup's last page could not be given the summary ({exc}).")
    return status


def run_finish(root, *, runner=subprocess.run):
    """Start the update.py that is now in the folder with --finish: the code of the version just put in place."""
    return runner([sys.executable, str(Path(root) / "scripts" / "update.py"), "--finish"], cwd=root).returncode


# ------------------------------------------------------------------------------------------------ the commands

def undo(root, *, args, runner=subprocess.run, processes=app_processes, ask=input, interactive=None, say=say):
    root = Path(root)
    record = read_record(root)
    if record is None or record.get("state") != "applied":
        raise UpdateStopped("There is no update to undo: update.bat --undo puts back the version the last update replaced, once.")
    if record.get("method") == "setup":
        raise UpdateStopped(f"Setup.exe made the last update, from {record['from']} to {record['to']}, and it keeps no copy of the files "
                            f"it replaced, so update.bat cannot put version {record['from']} back. To go back, uninstall NPC Forge and "
                            f"keep your library, then install version {record['from']} from {release_url(None)}.")
    running = processes(root)
    if running:
        raise UpdateStopped(running_problem(running))
    if record["method"] == "git":
        head = git(root, "rev-parse", "HEAD", runner=runner).stdout.strip()
        if head != record["git"]["to"]:
            raise UpdateStopped(f"This checkout has moved on since the update to {record['to']} (it is at {head[:7]}), so update.bat "
                                f"will not move it back. Use git yourself: git reset --keep {record['git']['from'][:7]}.")
    say(f"This puts version {record['from']} back in place of {record['to']}"
        + (f", with git reset --keep {record['git']['from'][:7]}." if record["method"] == "git"
           else f", from the copies in {shown_path(record['backup'])}."))
    if args.dry_run:
        say("This was a dry run, so nothing was changed.")
        return 0
    if not confirmed(args, ask=ask, interactive=interactive):
        say("Nothing was changed.")
        return 1
    if record["method"] == "git":
        git(root, "reset", "--keep", record["git"]["from"], runner=runner)
    else:
        restore_zip(root, record)
    record.update({"state": "undone", "undone_at": now_text()})
    save_record(root, record)
    return run_finish(root, runner=runner)


def update(root, *, args, check=updates.check, runner=subprocess.run, processes=app_processes, fetch=download, ask=input,
           interactive=None, say=say, report=None, version=VERSION):
    """update.bat's update. `report`, a dict, is filled with what a dry run found, for --json (the page's Update now)."""
    root = Path(root)
    report = {} if report is None else report
    running = [] if args.check else processes(root)
    if running and not args.dry_run:
        raise UpdateStopped(running_problem(running))
    try:
        result = check()
    except updates.UpdateError as exc:
        raise UpdateStopped(str(exc)) from exc
    say(result["message"])
    report.update({"from": version, "newer": bool(result["newer"]), "message": result["message"], "running": bool(running)})
    if args.check or not result["newer"]:
        return 0
    release = result["release"]
    new, tag = result["latest"], release["tag"]
    report.update({"to": new, "release": {key: release.get(key) for key in ("name", "url", "notes", "published_at")}})
    say("")
    say(f"NPC Forge: update from {version} to {new}")
    say("")
    say(release_lines(release))
    say("")
    record = {"schema": SCHEMA, "from": version, "to": new, "tag": tag, "at": now_text(), "state": "planned"}
    if git_checkout(root):
        plan = git_plan(root, tag, runner=runner)
        steps, notes = git_plan_sentences(plan, tag, version)
        record.update({"method": "git", "git": {"from": plan["from"], "to": plan["to"]}, "changed": plan["changed"]})
    else:
        downloads = root / FOLDER / DOWNLOADS
        say("Downloading the release from GitHub...")
        try:
            new_zip = fetch(updates.source_zip_url(tag), downloads / f"{tag}.zip")
            if new_zip is None:
                raise UpdateStopped(f"GitHub has no source ZIP for {tag}. Nothing was changed; try again later.")
            old_zip = fetch(updates.source_zip_url(f"v{version}"), downloads / f"v{version}.zip")
            new_files = read_archive(new_zip)
            old_files = read_archive(old_zip) if old_zip else None
        finally:
            shutil.rmtree(downloads, ignore_errors=True)                # both are read into memory; nothing needs them on disk
        if archive_version(new_files) != new:
            raise UpdateStopped(f"The release {tag} says it is version {archive_version(new_files)}, not {new}, so it was not used. "
                                "Nothing was changed; tell the project on GitHub.")
        plan = zip_plan(root, new_files, old_files)
        steps, notes = zip_plan_sentences(plan, version, new, old_files is not None)
        record.update({"method": "zip", "backup": f"{FOLDER}/{backup_name(version, new)}", "changed": plan["changed"]})
    say(plan_text(steps, notes))
    report.update({"steps": steps, "notes": notes, "method": record["method"]})
    say("")
    if args.dry_run:
        if running:
            say("NPC Forge is running from this folder: close it before you update.")
        say("This was a dry run, so nothing was changed.")
        return 0
    if not confirmed(args, ask=ask, interactive=interactive):
        say("Nothing was changed.")
        return 1
    if record["method"] == "git":
        try:
            git(root, "merge", "--ff-only", "--quiet", f"refs/tags/{tag}", runner=runner)
        except UpdateStopped:
            save_record(root, {**record, "state": "failed"})
            raise
        record["state"] = "applied"
        save_record(root, record)
    else:
        shutil.rmtree(root / record["backup"], ignore_errors=True)
        apply_zip(root, plan, new_files, record)
        prune_backups(root)
    say(f"The files of version {new} are in place.")
    return run_finish(root, runner=runner)


# ------------------------------------------------------------------------------------------------ Update now, from the page

def start_app(root, *, starter=subprocess.Popen):
    """Start NPC Forge again the way its folder starts it (start.bat on Windows, which also opens the browser)."""
    root = Path(root)
    if os.name == "nt" and (root / "start.bat").is_file():
        starter(["cmd", "/c", str(root / "start.bat")], cwd=root, close_fds=True, creationflags=NEW_CONSOLE)
        return True
    return False


def from_app(root, *, args, processes=app_processes, wait=time.sleep, clock=time.monotonic, run=update, starter=start_app,
             ask=input, say=say, **options):
    """The update that About > Update now starts in a window of its own: waits for NPC Forge to close, updates as
    update.bat --yes does, and starts NPC Forge again. A failure, or the answer that there is nothing newer, is kept in the
    notice for the page."""
    root = Path(root)
    say("NPC Forge: Update now")
    say("")
    say("Waiting for NPC Forge to close...")
    deadline = clock() + APP_CLOSE_SECONDS
    running = processes(root)
    while running and clock() < deadline:
        wait(0.5)
        running = processes(root)
    report, status = {}, 1
    if running:
        message = f"NPC Forge did not close within {APP_CLOSE_SECONDS // 60} minutes, so nothing was changed. " + running_problem(running)
        say(fill(message))
        save_notice(root, {"state": "failed", "from": VERSION, "message": message})
    else:
        message = None
        try:
            status = run(root, args=build_parser().parse_args(["--yes"]), processes=processes, say=say, report=report, **options)
        except UpdateStopped as exc:
            message = str(exc)
        except Exception as exc:                                        # the window closes when this ends: say it and keep it
            message = f"The update stopped: {type(exc).__name__}: {exc}. If it had begun changing files, it put back what it had changed."
        if message:
            status = 1
            say("")
            say(fill(message) if "\n" not in message else message)
            save_notice(root, {"state": "failed", "from": VERSION, "to": report.get("to"), "message": message})
        elif not report.get("newer"):
            save_notice(root, {"state": "current", "from": VERSION, "message": report.get("message", "")})
        say("")
        if args.reopen and starter(root):
            say("NPC Forge is starting again.")
        else:
            say("Start NPC Forge again: run start.bat in its folder.")
    if status:
        say("Press Enter to close this window.")
        try:
            ask()
        except EOFError:
            pass
    else:
        say(f"This window closes in {CLOSE_SECONDS} seconds.")
        wait(CLOSE_SECONDS)
    return status


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--check", action="store_true", help="only say whether GitHub has a newer release")
    action.add_argument("--undo", action="store_true", help="put back the version the last update replaced")
    action.add_argument("--finish", action="store_true", help=argparse.SUPPRESS)
    action.add_argument("--from-app", action="store_true", help=argparse.SUPPRESS)          # About > Update now
    # Setup.exe over an installed NPC Forge (after_setup): the version that was there, Setup's file lists and wheels, and where
    # its last page reads the summary.
    action.add_argument("--after-setup", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--from", dest="from_version", help=argparse.SUPPRESS)
    parser.add_argument("--setup-files", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--wheels", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--summary", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--reopen", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--json", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--dry-run", action="store_true", help="show the plan; change nothing")
    parser.add_argument("--yes", action="store_true", help="accept the plan without asking")
    return parser


def plan_report(root, *, args, **options):
    """--dry-run --json: what update() found, as one line of JSON. {"ok": false, "problem": why} when it made no plan."""
    report = {}
    try:
        update(root, args=args, say=lambda text="": None, report=report, **options)
    except UpdateStopped as exc:
        return {"ok": False, "problem": str(exc)}
    return {"ok": True, **report}


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.json and not args.dry_run:
        parser.error("--json goes with --dry-run")
    if args.after_setup and not (args.from_version and args.setup_files):
        parser.error("--after-setup needs --from and --setup-files")
    try:
        if args.after_setup:
            return after_setup(ROOT, args=args)
        if args.finish:
            return finish(ROOT)
        if args.undo:
            return undo(ROOT, args=args)
        if args.from_app:
            return from_app(ROOT, args=args)
        if args.json:
            report = plan_report(ROOT, args=args)
            print(json.dumps(report), flush=True)
            return 0 if report["ok"] else 1
        return update(ROOT, args=args)
    except UpdateStopped as exc:
        say("")
        say(fill(str(exc)) if "\n" not in str(exc) else str(exc))
        return 1
    except KeyboardInterrupt:
        say("\nStopped. If the update had begun changing files, what it had changed was put back.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
