"""Install NPC Forge's own ComfyUI nodes into a ComfyUI custom_nodes folder: the Pixal3D head-texture node, and with --trellis2 the
Trellis2 shape-decode node (for the "Trellis2 four pictures" generator). Set up ComfyUI on the Models card does the same
(npcgen/comfy_setup.py)."""
import argparse
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from npcgen import comfy_setup  # noqa: E402

NODES = comfy_setup.NODES


def install(parser, custom_nodes, key):
    """Copy one node into custom_nodes. Returns True when something was written."""
    node = NODES[key]
    target = custom_nodes / node["folder"] / "__init__.py"
    try:
        wrote = comfy_setup.install_node(custom_nodes, key)
    except comfy_setup.SetupError:
        parser.error(f"Refusing to replace an unrelated custom node at {target}")
    print(f"Installed {node['name']}: {target}" if wrote else f"{node['name']} is already current: {target}")
    return wrote


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("comfy_dir", type=pathlib.Path, help="ComfyUI install directory (the folder containing custom_nodes)")
    parser.add_argument("--trellis2", action="store_true", help="also install the Trellis2 shape-decode node")
    args = parser.parse_args()

    comfy_dir = args.comfy_dir.resolve()
    custom_nodes = comfy_dir / "custom_nodes"
    if not (custom_nodes.is_dir() and (comfy_dir / "comfy_api" / "latest").is_dir()):
        parser.error(f"{comfy_dir} does not look like the ComfyUI source directory")

    changed = [install(parser, custom_nodes, key) for key in (["head", "trellis2"] if args.trellis2 else ["head"])]
    if any(changed):
        print("Restart ComfyUI once to load the node(s).")


if __name__ == "__main__":
    main()
