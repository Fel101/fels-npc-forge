"""The engine side of a creature pack (Phase 5 of the creature maker).

A pack's model and manifest are the same for every engine (metres, +Y up, +Z forward, glTF). What differs is the code a game reads
them with, so each engine has a profile that names its loader files. loader_files() makes those files for one creature: the game's
clip map for the creature's body plan goes into the clip file, so a game does not keep its own copy. install() copies a pack into a
game's pack folder (export_creature_pack.py --install).

The loader files have no class_name: a pack folder sits inside its game's project, and a game can hold more than one pack. Godot does
not report two scripts with one class_name; the name quietly goes to one of them. A game loads the files by path, for example
    const CreaturePack := preload("res://assets/creatures/eh-s01-reedcoil/pack/loader/creature_pack.gd")
"""
import pathlib
import re

from . import creature_projects

TEMPLATES = pathlib.Path(__file__).resolve().parent / "creature_templates"
LOADER_DIR = "loader"
CLIP_MAP_MARK = "__CLIP_MAP__"                        # where a template takes the clip map

PROFILES = {
    "godot-4": {
        "label": "Godot 4",
        "templates": {                                 # the file in the pack -> the template it is made from
            "creature_pack.gd": "godot/creature_pack.gd",
            "creature_clips.gd": "godot/creature_clips.gd.tmpl",
            "creature_pack_test.gd": "godot/creature_pack_test.gd",
        },
    },
}

STATE = re.compile(r"[a-z]+")                          # what a unit is doing: idle, move, attack, hurt, die
CLIP = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_ .-]{0,63}")   # a clip name in the model: one line, nothing that ends a GDScript string


def _slugify(text):
    return re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-")


def install_folder(key, name, key_prefix="", given=None):
    """The folder under a game's pack folder that holds this creature's pack: <pack folder>/<this folder>/pack.

    By default it is the bestiary key without the game's prefix ("dustmoon-burrower" is "burrower"), with the creature's name added when
    the key does not have it ("eh-s01" and "Reedcoil" give "eh-s01-reedcoil", as in the Ember Hollow example). `given` names it."""
    if given is not None:
        folder = str(given)
    else:
        base = str(key or "")
        if key_prefix and base.startswith(key_prefix):
            base = base[len(key_prefix):]
        base = _slugify(base)
        name_slug = _slugify(name)
        if not base:
            folder = name_slug
        elif name_slug and name_slug not in base.split("-"):
            folder = f"{base}-{name_slug}"
        else:
            folder = base
    if not creature_projects.SLUG.fullmatch(folder):
        raise ValueError(f"The pack folder '{folder}' must be lower-case words joined by dashes, for example eh-s01-reedcoil")
    return folder


def _clip_literal(clip_map):
    """The clip map as a GDScript dictionary literal. Anything that would not be a plain word or name is refused: it would be written
    into a script the game runs."""
    pairs = []
    for state, clip in sorted((clip_map or {}).items()):
        if not (isinstance(state, str) and STATE.fullmatch(state) and isinstance(clip, str) and CLIP.fullmatch(clip)):
            raise ValueError(f"The clip '{clip}' for '{state}' can not go into the game's loader: a state is a lower-case word, and a "
                             "clip is a name of letters, digits, spaces, dots, dashes or underscores, at most 64 characters")
        pairs.append(f'"{state}": "{clip}"')
    return "{" + ", ".join(pairs) + "}"


def loader_files(engine, clip_map):
    """The loader files for one creature's pack, as {"loader/<name>": bytes}. `clip_map` is the game's map for the creature's body plan
    (state -> clip name). Raises ValueError for an engine with no profile, or a clip the script can not take."""
    profile = PROFILES.get(engine)
    if profile is None:
        raise ValueError(f"No loader is made for the engine '{engine}' yet (Godot 4 is the one so far)")
    clips = _clip_literal(clip_map)
    files = {}
    for name, template in profile["templates"].items():
        text = (TEMPLATES / template).read_text(encoding="utf-8")
        files[f"{LOADER_DIR}/{name}"] = text.replace(CLIP_MAP_MARK, clips).encode("utf-8")
    return files


def install(files, pack_dir, folder):
    """Copy a pack's files into <pack_dir>/<folder>/pack. Returns [(file name, "new" | "replaced" | "unchanged")] in order.

    The game's pack folder must exist: a wrong path is refused, not created. A file with the same name is replaced; any other file
    already in the pack folder (a note, or a file from an older pack) is left as it is. Every name is checked before anything is written."""
    if not str(pack_dir or "").strip():                 # "" would mean the current folder
        raise ValueError("this game has no Pack folder: set it on the Projects panel")
    root = pathlib.Path(pack_dir)
    if not root.is_dir():
        raise ValueError(f"The game's pack folder {pack_dir} does not exist: check its Pack folder on the Projects panel")
    target = root / folder / "pack"
    planned = []
    for name in files:
        posix = pathlib.PurePosixPath(name)
        if not name or posix.is_absolute() or ".." in posix.parts:
            raise ValueError(f"'{name}' is not a file name a pack folder can take")
        path = target.joinpath(*posix.parts)
        if not path.exists():
            state = "new"
        elif path.read_bytes() == files[name]:
            state = "unchanged"
        else:
            state = "replaced"
        planned.append((name, path, state))
    for name, path, state in planned:
        if state != "unchanged":
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(files[name])
    return [(name, state) for name, _, state in planned]
