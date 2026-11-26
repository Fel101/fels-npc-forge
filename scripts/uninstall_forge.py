"""What Setup.exe's uninstaller removes from NPC Forge's folder: everything but the library, or the library too.

    python\\python.exe -B scripts\\uninstall_forge.py --keep-library
    python\\python.exe -B scripts\\uninstall_forge.py --everything

The uninstaller asks first and keeps the library unless told otherwise (installer/FelsNPCForge.iss). The library is the
characters (library\\, or the folder config.json names when it is inside this one), local\\ (extensions, settings, update
backups) and config.json: LIBRARY. The rest is program: NPC Forge's files, which update.bat may have added to since the
install, .venv, the animation clips, Python's caches and the folders version 1.0 used for voices. A library config.json keeps
somewhere else is never touched, and neither are ComfyUI or its models. Two things are left for the uninstaller itself: the
Python this runs on (python\\, removed once it has stopped) and the uninstaller's own files (unins*). A link or junction is
removed as a link; what it points to is never touched.

Standard library only: it runs on NPC Forge's own Python with nothing installed.
"""
import argparse
import json
import os
import shutil
import stat
import sys
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
LIBRARY = ("library", "local", "config.json")
LEFT_FOR_THE_UNINSTALLER = ("python",)


def is_link(path):
    """A symbolic link or a Windows junction: something that points elsewhere and must never be followed or removed through."""
    return path.is_symlink() or getattr(os.path, "isjunction", lambda _path: False)(path)


def _key(path):
    return PurePosixPath(str(path).replace("\\", "/").lower())


def library_items(root):
    """LIBRARY, and the library folder config.json names when it lies inside root under another name."""
    items = list(LIBRARY)
    try:
        configured = json.loads((Path(root) / "config.json").read_text(encoding="utf-8")).get("library_dir")
    except (OSError, ValueError, AttributeError):
        configured = None
    if isinstance(configured, str) and configured.strip():
        folder = Path(configured) if Path(configured).is_absolute() else Path(root) / configured
        try:
            inside = folder.resolve().relative_to(Path(root).resolve())
        except (OSError, ValueError):
            inside = None
        if inside is not None and str(inside) not in ("", ".") and inside.as_posix() not in items:
            items.append(inside.as_posix())
    return items


def removals(root, *, keep_library):
    """The paths under root to remove, and the folders opened because they hold part of the library (innermost first): an
    opened folder is removed afterwards when nothing was kept in it."""
    root = Path(root)
    keep = [_key(item) for item in library_items(root)] if keep_library else []
    found, opened = [], []

    def visit(folder, relative):
        for entry in sorted(folder.iterdir(), key=lambda item: item.name.lower()):
            name = relative / entry.name
            key = _key(name)
            if relative == PurePosixPath(".") and (
                    entry.name.lower() in LEFT_FOR_THE_UNINSTALLER or entry.name.lower().startswith("unins")):
                continue
            if any(key == kept or kept in key.parents for kept in keep):
                continue
            if entry.is_dir() and not is_link(entry) and any(key in kept.parents for kept in keep):
                visit(entry, name)
                opened.append(entry)
                continue
            found.append(entry)

    visit(root, PurePosixPath("."))
    return found, opened


def _writable(function, path, _info):
    os.chmod(path, stat.S_IWRITE)
    function(path)


def remove(path):
    """Remove one file, folder or link. A link or junction goes; what it points to stays."""
    if is_link(path):
        os.rmdir(path) if path.is_dir() else path.unlink()
    elif path.is_dir():
        # onexc arrived in Python 3.12 and onerror is deprecated there; the tests also run on 3.10.
        shutil.rmtree(path, **({"onexc": _writable} if sys.version_info >= (3, 12) else {"onerror": _writable}))
    else:
        try:
            path.unlink()
        except PermissionError:
            os.chmod(path, stat.S_IWRITE)
            path.unlink()


def run(root, *, keep_library, say=print):
    root = Path(root)
    failed = []
    found, opened = removals(root, keep_library=keep_library)
    for path in found:
        try:
            remove(path)
        except OSError as exc:
            failed.append(f"{path.relative_to(root)} ({exc.strerror or exc})")
    for folder in opened:
        if folder.is_dir() and not any(folder.iterdir()):
            folder.rmdir()
    if keep_library:
        kept = [item for item in library_items(root) if (root / item).exists()]
        say(f"Kept the library in {root}: {', '.join(kept) or 'nothing was there yet'}.")
    else:
        say(f"Removed the library in {root} too.")
    if failed:
        say("Could not remove, probably because a program still has them open: " + "; ".join(failed) + ".")
        return 1
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Remove NPC Forge from its folder, keeping the library or not.")
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument("--keep-library", action="store_true", help="remove the program and keep the library")
    choice.add_argument("--everything", action="store_true", help="remove the library too")
    parser.add_argument("--root", type=Path, default=ROOT, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    return run(args.root, keep_library=args.keep_library)


if __name__ == "__main__":
    sys.exit(main())
