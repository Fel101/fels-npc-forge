"""Install NPC Forge's own ComfyUI nodes into a ComfyUI custom_nodes folder: the Pixal3D head-texture node, and with --trellis2 the
Trellis2 shape-decode node (for the "Trellis2 four pictures" generator)."""
import argparse
import pathlib


ROOT = pathlib.Path(__file__).resolve().parents[1]
EXTENSIONS = ROOT / "npcgen" / "comfy_extensions"
NODES = {
    "head": {
        "name": "NPC Forge Pixal3D head-texture node",
        "source": EXTENSIONS / "npcforge_pixal_head_texture.py",
        "folder": "npcforge_pixal_head_texture",
        "marker": '"""ComfyUI node that adds head-resolution Pixal3D projection features to NPC Forge textures."""',
    },
    "trellis2": {
        "name": "NPC Forge Trellis2 shape-decode node",
        "source": EXTENSIONS / "npcforge_trellis2_decode.py",
        "folder": "npcforge_trellis2_decode",
        "marker": '"""ComfyUI node that decodes a Trellis2 shape latent to a mesh without the CuMesh hole fill, which crashes the GPU on ROCm for Windows."""',
    },
}


def install(parser, custom_nodes, node):
    """Copy one node into custom_nodes. Returns True when something was written."""
    target_dir = custom_nodes / node["folder"]
    target = target_dir / "__init__.py"
    content = node["source"].read_text("utf-8")
    if target.exists():
        existing = target.read_text("utf-8")
        if existing == content:
            print(f"{node['name']} is already current: {target}")
            return False
        if node["marker"] not in existing:
            parser.error(f"Refusing to replace an unrelated custom node at {target}")
    target_dir.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8", newline="\n")
    print(f"Installed {node['name']}: {target}")
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("comfy_dir", type=pathlib.Path, help="ComfyUI install directory (the folder containing custom_nodes)")
    parser.add_argument("--trellis2", action="store_true", help="also install the Trellis2 shape-decode node")
    args = parser.parse_args()

    comfy_dir = args.comfy_dir.resolve()
    custom_nodes = comfy_dir / "custom_nodes"
    if not (custom_nodes.is_dir() and (comfy_dir / "comfy_api" / "latest").is_dir()):
        parser.error(f"{comfy_dir} does not look like the ComfyUI source directory")

    changed = [install(parser, custom_nodes, NODES[key]) for key in (["head", "trellis2"] if args.trellis2 else ["head"])]
    if any(changed):
        print("Restart ComfyUI once to load the node(s).")


if __name__ == "__main__":
    main()
