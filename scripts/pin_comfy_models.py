"""Pin the model files the ComfyUI helper installs (data/comfy_models.json, npcgen/comfy_setup.py): for each file, the Hugging Face
repository and the commit it is taken from, its size and its SHA-256, read from Hugging Face's own file list.

    python scripts/pin_comfy_models.py            write data/comfy_models.json from the repositories' current commits
    python scripts/pin_comfy_models.py --check    check that every pinned commit still serves the same size and SHA-256

Run it only when a pin has to change: the commit, size and hash are what the helper checks each download against. It reads
Hugging Face's public file lists (no model is downloaded) and needs the network."""
import argparse
import datetime
import json
import os
import pathlib
import sys
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from npcgen import comfy_setup  # noqa: E402

# (ComfyUI folder, file name as NPC Forge's graphs ask for it, repository, path in the repository). The files are those of
# the engines in comfy_setup.PARTS; a test checks that every one of them is pinned.
SOURCES = [
    ("diffusion_models", "z_image_turbo_bf16.safetensors", "Comfy-Org/z_image_turbo", "split_files/diffusion_models/z_image_turbo_bf16.safetensors"),
    ("text_encoders", "qwen_3_4b.safetensors", "Comfy-Org/z_image_turbo", "split_files/text_encoders/qwen_3_4b.safetensors"),
    ("vae", "ae.safetensors", "Comfy-Org/z_image_turbo", "split_files/vae/ae.safetensors"),
    ("diffusion_models", "qwen_image_2512_fp8_e4m3fn.safetensors", "Comfy-Org/Qwen-Image_ComfyUI", "split_files/diffusion_models/qwen_image_2512_fp8_e4m3fn.safetensors"),
    ("text_encoders", "qwen_2.5_vl_7b_fp8_scaled.safetensors", "Comfy-Org/Qwen-Image_ComfyUI", "split_files/text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors"),
    ("vae", "qwen_image_vae.safetensors", "Comfy-Org/Qwen-Image_ComfyUI", "split_files/vae/qwen_image_vae.safetensors"),
    ("loras", "Qwen-Image-Lightning-4steps-V2.0.safetensors", "lightx2v/Qwen-Image-Lightning", "Qwen-Image-Lightning-4steps-V2.0.safetensors"),
    ("diffusion_models", "Qwen-Image-Edit-2511-FP8_e4m3fn.safetensors", "1038lab/Qwen-Image-Edit-2511-FP8", "Qwen-Image-Edit-2511-FP8_e4m3fn.safetensors"),
    ("loras", "Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors", "lightx2v/Qwen-Image-Edit-2511-Lightning", "Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors"),
    ("checkpoints", "hunyuan_3d_v2.1.safetensors", "Comfy-Org/hunyuan3D_2.1_repackaged", "hunyuan_3d_v2.1.safetensors"),
    ("diffusion_models", "pixal3d_multiview_int8_convrot.safetensors", "Comfy-Org/Pixal3D", "diffusion_models/pixal3d_multiview_int8_convrot.safetensors"),
    ("clip_vision", "dino_v3_L_naf_fp32.safetensors", "Comfy-Org/Pixal3D", "clip_vision/dino_v3_L_naf_fp32.safetensors"),
    ("vae", "trellis_2_shape_vae_bf16.safetensors", "Comfy-Org/Pixal3D", "vae/trellis_2_shape_vae_bf16.safetensors"),
    ("vae", "trellis_2_texture_vae_bf16.safetensors", "Comfy-Org/Pixal3D", "vae/trellis_2_texture_vae_bf16.safetensors"),
    ("background_removal", "birefnet.safetensors", "Comfy-Org/BiRefNet", "background_removal/birefnet.safetensors"),
    ("diffusion_models", "mage_flow_int8_convrot.safetensors", "Comfy-Org/Mage-Flow", "diffusion_models/mage_flow_int8_convrot.safetensors"),
    ("diffusion_models", "mage_flow_edit_int8_convrot.safetensors", "Comfy-Org/Mage-Flow", "diffusion_models/mage_flow_edit_int8_convrot.safetensors"),
    ("text_encoders", "qwen3vl_4b_bf16.safetensors", "Comfy-Org/Mage-Flow", "text_encoders/qwen3vl_4b_bf16.safetensors"),
    ("vae", "mage_flow_vae_bf16.safetensors", "Comfy-Org/Mage-Flow", "vae/mage_flow_vae_bf16.safetensors"),
]
API = (os.environ.get("HF_ENDPOINT") or comfy_setup.HUB).rstrip("/") + "/api/models"


def _get(url):
    request = urllib.request.Request(url, headers={"User-Agent": comfy_setup.USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as answer:
        return json.load(answer)


def _tree(repo, revision, cache):
    if (repo, revision) not in cache:
        rows = _get(f"{API}/{repo}/tree/{revision}?recursive=true")
        cache[(repo, revision)] = {row["path"]: row for row in rows if row.get("type") == "file"}
    return cache[(repo, revision)]


def pin():
    heads, trees, files = {}, {}, []
    for folder, name, repo, path in SOURCES:
        if repo not in heads:
            heads[repo] = _get(f"{API}/{repo}")["sha"]
        row = _tree(repo, heads[repo], trees).get(path)
        if not row or not (row.get("lfs") or {}).get("oid"):
            sys.exit(f"{repo} has no stored file {path} at {heads[repo]}")
        files.append({"folder": folder, "name": name, "repo": repo, "revision": heads[repo], "path": path,
                      "size": row["lfs"]["size"], "sha256": row["lfs"]["oid"]})
        print(f"{folder}/{name}: {repo}@{heads[repo][:10]} {row['lfs']['size']:,} bytes")
    table = json.loads(comfy_setup.DATA.read_text("utf-8")) if comfy_setup.DATA.exists() else {}
    table.update(checked=datetime.date.today().isoformat(), files=files)
    comfy_setup.DATA.write_text(json.dumps(table, indent=1, ensure_ascii=False) + "\n", "utf-8")
    print(f"Wrote {comfy_setup.DATA.relative_to(ROOT)}")


def check():
    trees, bad = {}, 0
    for entry in comfy_setup.manifest()["files"]:
        try:
            row = _tree(entry["repo"], entry["revision"], trees).get(entry["path"])
        except urllib.error.HTTPError as error:
            row, why = None, f"HTTP {error.code}"
        else:
            why = "missing"
        lfs = (row or {}).get("lfs") or {}
        if lfs.get("oid") == entry["sha256"] and lfs.get("size") == entry["size"]:
            print(f"ok   {entry['folder']}/{entry['name']}")
        else:
            bad += 1
            print(f"FAIL {entry['folder']}/{entry['name']}: {why if not row else 'a different size or hash'}")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="check the pins instead of writing them")
    check() if parser.parse_args().check else pin()
