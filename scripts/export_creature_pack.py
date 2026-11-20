"""Write one creature's pack into a folder, and with --install into its game's pack folder (npcgen/creaturepack.py).

    python scripts/export_creature_pack.py <bestiary key or creature id> <output folder> [--install] [--as <folder name>]

Example, for the Ember Hollow serpent (its game's Pack folder is set on the Projects panel):
    python scripts/export_creature_pack.py eh-s01 "C:/path/to/game/assets/creatures/eh-s01-reedcoil/pack"
    python scripts/export_creature_pack.py eh-s01 "C:/path/to/out/eh-s01" --install

A pack is refused until the creature has a game (its Game field), a game model, and a licence rating that is cleared: the error
says which. The pack is written to the output folder. --install also copies it into the game's Pack folder, as <Pack folder>/<name>/pack,
where the name is the bestiary key without the game's prefix, with the creature's name added when the key lacks it (--as gives another;
npcgen/creature_export.py). Files already there with the same name are replaced, and other files in that folder are left alone.
Nothing is written to the library.
"""
import argparse
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from npcgen import creature_export, creatures, creaturepack  # noqa: E402
from npcgen.library import Library  # noqa: E402


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("creature", help="the bestiary key (for example eh-s01) or the creature's id in the library")
    parser.add_argument("out", help="the folder to write the pack into")
    parser.add_argument("--install", action="store_true", help="also copy the pack into the game's Pack folder")
    parser.add_argument("--as", dest="as_name", metavar="FOLDER", help="the folder name under the game's Pack folder")
    parser.add_argument("--library", default=str(ROOT / "library"), help="the library folder (default: the Forge's own)")
    args = parser.parse_args(argv)
    store = creatures.CreatureStore(Library(pathlib.Path(args.library)))
    matches = [c for c in store.list() if args.creature in (c.get("id"), c.get("key"))]
    if len(matches) != 1:
        print(f"Found {len(matches)} creatures for '{args.creature}'; give one bestiary key or creature id.", file=sys.stderr)
        return 1
    creature = store.get(matches[0]["id"])
    try:
        files = creaturepack.build(store, creature["id"])
    except ValueError as error:
        print(f"Not packed: {error}", file=sys.stderr)
        return 2
    out = pathlib.Path(args.out)
    for name, data in files.items():
        path = out / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    print(f"Wrote {len(files)} files for {creature['name']} to {out}")
    if not args.install:
        return 0
    project = store.projects.get(creature["project"]) or {}
    pack_dir = project.get("pack_dir") or ""
    if not pack_dir:
        print("Not installed: this game has no Pack folder yet (set it on the Projects panel). The pack is in the output folder.",
              file=sys.stderr)
        return 3
    try:
        folder = creature_export.install_folder(creature.get("key"), creature["name"], project.get("key_prefix", ""), args.as_name)
        states = creature_export.install(files, pack_dir, folder)
    except ValueError as error:
        print(f"Not installed: {error}", file=sys.stderr)
        return 3
    target = pathlib.Path(pack_dir) / folder / "pack"
    for name, state in states:
        print(f"  {state:9} {target / name}")
    print(f"Installed {creature['name']} into {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
