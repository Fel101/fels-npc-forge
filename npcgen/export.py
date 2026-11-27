"""Turn a finished animation clip into files a game engine can load.

Everything is built in memory as {filename: bytes}, so the CLI can write it to a folder and the web app can zip it.
Each export carries a provenance block (generator, model, seeds), because engines and stores increasingly ask for it.
"""
import html
import io
import json
import pathlib
import re
import zipfile

import numpy as np
from PIL import Image

from . import licences, media
from .anim import BASELINE, CANVAS, _rgb_to_lab
from .sheet import VIEWS

PRESETS = {
    "atlas": {"label": "Generic atlas: PNG grid + JSON + preview page", "layout": "grid", "frame": 256, "dirs": None, "frames": None},
    "grid4": {"label": "4-direction hero sheet: 4x4 grid of 256px, rows down / left / right / up", "layout": "grid", "frame": 256,
                   "dirs": ["down", "left", "right", "up"], "frames": 4, "name": "hero-walk"},
    "godot": {"label": "Godot 4 SpriteFrames: .tres + one PNG strip per animation", "layout": "strips", "frame": 128, "dirs": None, "frames": None},
    "unity": {"label": "Unity sprite atlas: PNG grid + animation metadata JSON", "layout": "grid", "frame": 128, "dirs": None, "frames": None},
    "aseprite": {"label": "Aseprite JSON hash: PNG grid + frame tags and timing", "layout": "grid", "frame": 128, "dirs": None, "frames": None},
}


def slugify(text):
    return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-") or "npc"


def _safe_member(name):
    """A relative POSIX archive/output path with no traversal, drive, or control characters."""
    text = str(name).replace("\\", "/")
    path = pathlib.PurePosixPath(text)
    if (not text or path.is_absolute() or text.startswith("//") or re.match(r"^[A-Za-z]:", text)
            or any(part in ("", ".", "..") for part in path.parts) or any(ord(c) < 32 for c in text)):
        raise ValueError(f"Unsafe export file name: {name!r}")
    return path.as_posix()


def _resource_path(value, default):
    """A quoted Godot res:// path that cannot inject another property or leave the project."""
    text = str(value or default).strip().replace("\\", "/").rstrip("/")
    rest = text[6:] if text.startswith("res://") else ""
    if (not rest or '"' in text or any(ord(c) < 32 for c in text)
            or any(part in ("", ".", "..") for part in rest.split("/"))):
        raise ValueError("Godot resource path must be a safe res:// path")
    return text


def _script_json(value):
    """JSON safe to embed in an HTML script element."""
    return json.dumps(value).replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")


def _provenance(npc, meta, groups, what):
    engines = sorted({engine for group in groups for engine in group})
    rating = licences.picture_report(groups, what)
    return {"generator": "NPC Forge", "ai_generated": True, "model": ", ".join(engines) or "Unknown",
            "engines": engines, "models": rating["models"], "verdict": rating["verdict"], "unknown": rating["unknown"],
            "npc_id": npc["id"], "style": npc.get("style"), "created": meta.get("created"),
            "note": "AI-generated. Review for suitability before shipping."}


def pixelate_set(frames, colors=24, outline=False):
    """Snap frames to ONE shared small palette with hard alpha, like hand-made pixel art.

    The palette must be shared by every frame of a clip: quantising each frame on its own gives slightly different
    colours per frame, which shimmers when the animation plays. Optionally adds a 1px dark outline.
    """
    arrays = [np.asarray(f.convert("RGBA")) for f in frames]
    opaque = sum(int(np.count_nonzero(a[..., 3] >= 128)) for a in arrays)
    if not opaque:
        return list(frames)
    # Pillow only needs a representative image to derive the shared palette. Feeding it every pixel can otherwise create
    # several hundred MB of temporary arrays for a 1024px multi-direction export.
    palette_samples = 262_144
    stride = max(1, int(np.ceil(opaque / palette_samples)))
    samples = []
    for a in arrays:
        visible = a[a[..., 3] >= 128][:, :3]
        if len(visible):
            samples.append(visible[::stride])
    pixels = np.concatenate(samples)[:palette_samples]
    palette = Image.new("P", (1, 1))
    chosen = np.round(shared_palette(pixels, colors)).astype(np.uint8)
    palette.putpalette(np.concatenate([chosen, np.repeat(chosen[:1], 256 - len(chosen), axis=0)]).ravel().tolist())
    out = []
    for a in arrays:
        solid = a[..., 3] >= 128
        rgb = a[..., :3].copy()
        rgb[~solid] = pixels[0]                                     # transparent pixels: any palette colour, alpha hides it
        snapped = np.asarray(Image.fromarray(rgb, "RGB").quantize(palette=palette, dither=Image.Dither.NONE).convert("RGB"))
        result = np.dstack([snapped, np.where(solid, 255, 0).astype(np.uint8)])
        if outline and solid.any():
            p = np.pad(solid, 1)
            edge = ~solid & (p[:-2, 1:-1] | p[2:, 1:-1] | p[1:-1, :-2] | p[1:-1, 2:])
            lum = result[..., 0] * 0.299 + result[..., 1] * 0.587 + result[..., 2] * 0.114        # by hand: see anim._apply on why not `@`
            result[edge] = [*result[solid][np.argmin(lum[solid])][:3], 255]
        out.append(Image.fromarray(result, "RGBA"))
    return out


def _median_cut(rgb, weights, colors):
    """Pillow's median cut over these colours, each repeated by its weight: (colors, 3) RGB."""
    samples = np.repeat(np.round(rgb).astype(np.uint8), np.maximum(1, np.round(weights)).astype(int), axis=0)
    side = int(np.ceil(np.sqrt(len(samples))))
    canvas = np.tile(samples[:1], (side * side, 1))
    canvas[:len(samples)] = samples
    cut = Image.fromarray(canvas.reshape(side, side, 3), "RGB").quantize(colors=colors, method=Image.Quantize.MEDIANCUT,
                                                                         dither=Image.Dither.NONE)
    return np.asarray(cut.getpalette()[:3 * colors], dtype=np.float64).reshape(-1, 3)


def shared_palette(pixels, colors, rounds=12):
    """Up to `colors` RGB colours (a float array) for these (N, 3) RGB pixels.

    A plain median cut shares the palette out by pixel count, so a robe that fills most of the figure takes most of the
    colours and a small face is snapped to the nearest robe shade (an elf's skin came out olive at 24 colours). Here each
    distinct colour (to 5 bits a channel) counts by the square root of how many pixels have it, and the median cut's
    colours are then moved to the weighted means of the colours nearest them in Lab, where distance follows what the eye
    sees. On eight characters' walks at 24 colours this halved the error on skin and lowered it overall.
    """
    px = pixels.astype(np.int64)
    key = ((px[:, 0] >> 3) << 10) | ((px[:, 1] >> 3) << 5) | (px[:, 2] >> 3)
    _, inverse, counts = np.unique(key, return_inverse=True, return_counts=True)
    rgb = np.zeros((len(counts), 3))
    np.add.at(rgb, inverse.ravel(), px)
    rgb /= counts[:, None]
    if len(rgb) <= colors:
        return rgb
    weights = np.sqrt(counts)
    start = _median_cut(rgb, weights, colors)
    lab, centres = _rgb_to_lab(rgb), _rgb_to_lab(start)
    nearest = None
    for _ in range(rounds):
        cross = lab[:, 0:1] * centres[:, 0] + lab[:, 1:2] * centres[:, 1] + lab[:, 2:3] * centres[:, 2]   # not `@`: anim._apply
        distance = (centres ** 2).sum(1)[None, :] - 2 * cross                  # each colour's own |lab|^2 does not change the nearest
        moved = distance.argmin(1)
        if nearest is not None and np.array_equal(moved, nearest):
            break
        nearest = moved
        for k in range(len(centres)):
            member = nearest == k
            if member.any():
                centres[k] = (lab[member] * weights[member, None]).sum(0) / weights[member].sum()
    out = start.copy()
    for k in range(len(centres)):                                # each colour as the weighted mean of its members' RGB
        member = nearest == k
        if member.any():
            out[k] = (rgb[member] * weights[member, None]).sum(0) / weights[member].sum()
    return out


def pixelate(im, colors=24, outline=False):
    return pixelate_set([im], colors, outline)[0]


def downscale(im, size, pixel):
    """Shrink a working-resolution frame. Pillow premultiplies RGBA when resizing, so there are no dark fringes."""
    return im.resize((size, size), Image.BOX if (pixel or size <= 96) else Image.LANCZOS)


def _tres(anims, size, res_path):
    ext = [f'[ext_resource type="Texture2D" path="{res_path}/{a["image"]}" id="tex_{a["godot_name"]}"]' for a in anims]
    sub = [f'[sub_resource type="AtlasTexture" id="AtlasTexture_{a["godot_name"]}_{i}"]\natlas = ExtResource("tex_{a["godot_name"]}")\n'
           f'region = Rect2({i * size}, 0, {size}, {size})\n' for a in anims for i in range(a["frames"])]
    blocks = []
    for a in anims:
        frames = ", ".join('{\n"duration": 1.0,\n"texture": SubResource("AtlasTexture_%s_%d")\n}' % (a["godot_name"], i) for i in range(a["frames"]))
        blocks.append('{\n"frames": [%s],\n"loop": %s,\n"name": &"%s",\n"speed": %.1f\n}' % (frames, "true" if a["loop"] else "false", a["godot_name"], a["fps"]))
    return (f'[gd_resource type="SpriteFrames" load_steps={1 + len(ext) + len(sub)} format=3]\n\n' + "\n".join(ext) + "\n\n"
            + "\n".join(sub) + "\n[resource]\nanimations = [" + ", ".join(blocks) + "]\n")


def _unity_manifest(anims, size, image, pixel, anchor, provenance):
    """Unity-friendly atlas metadata using bottom-left texture rects and bottom-centre sprite pivots."""
    sprites, animations = [], []
    sheet_height = len(anims) * size
    for animation in anims:
        frame_names = []
        for i in range(animation["frames"]):
            sprite_name = f'{animation["name"]}_{i:03d}'
            frame_names.append(sprite_name)
            sprites.append({
                "name": sprite_name,
                "rect": {"x": animation["x"] + i * size, "y": sheet_height - animation["y"] - size, "width": size, "height": size},
                "pivot": {"x": 0.5, "y": 1 - anchor[1] / size}, "border": {"left": 0, "bottom": 0, "right": 0, "top": 0},
            })
        animations.append({"name": animation["name"], "direction": animation["name"].rsplit("_", 1)[-1],
                           "frames": frame_names, "frameRate": animation["fps"], "loop": animation["loop"]})
    return {
        "format": "NPCForgeUnityAtlas", "version": 1, "texture": image, "frameSize": {"width": size, "height": size},
        "textureSize": {"width": (anims[0]["frames"] * size if anims else 0), "height": sheet_height},
        "rectOrigin": "bottom-left",
        "pixelsPerUnit": 100, "filterMode": "Point" if pixel else "Bilinear", "anchor": {"x": anchor[0], "y": anchor[1]},
        "sprites": sprites, "animations": animations, "provenance": provenance,
    }


def _aseprite_json(anims, size, image, count, directions, loop, fps, provenance):
    """Aseprite's JSON array layout, with every animation direction represented as a frame tag."""
    frames, tags = {}, []
    frame_index = 0
    for row, animation in enumerate(anims):
        start = frame_index
        for i in range(count):
            name = f"frame_{frame_index:04d}"
            rect = {"x": i * size, "y": row * size, "w": size, "h": size}
            frames[name] = {
                "frame": rect, "rotated": False, "trimmed": False,
                "spriteSourceSize": {"x": 0, "y": 0, "w": size, "h": size},
                "sourceSize": {"w": size, "h": size}, "duration": max(1, round(1000 / fps)),
            }
            frame_index += 1
        tags.append({"name": animation["name"], "from": start, "to": frame_index - 1,
                     "direction": "forward", "color": "#8080ffff"})
    return {
        "frames": frames,
        "meta": {"app": "NPC Forge", "version": "1.0", "image": image, "format": "RGBA8888",
                 "size": {"w": count * size, "h": len(directions) * size}, "scale": "1",
                 "frameTags": tags, "layers": [], "slices": [],
                 "npcforge": {"provenance": provenance,
                              "animations": [{"name": animation["name"], "direction": direction, "frames": count,
                                              "fps": fps, "loop": loop}
                                             for animation, direction in zip(anims, directions)]}},
    }


_PREVIEW = """<!doctype html>
<meta charset="utf-8"><title>__TITLE__</title>
<style>
body{font:14px system-ui,sans-serif;background:#1c2028;color:#e6e9ef;margin:24px}
h1{font-size:18px;margin:0 0 4px}p{color:#98a2b3;margin:0 0 16px}
.grid{display:flex;flex-wrap:wrap;gap:14px}.cell{background:#262b36;border-radius:10px;padding:10px;text-align:center}
canvas{display:block;image-rendering:pixelated;background:repeating-conic-gradient(#2c323e 0 25%,#242934 0 50%) 50%/16px 16px}
</style>
<h1>__TITLE__</h1><p>Animations play at their own frame rate. Made with NPC Forge. __MODELS__</p>
<div class="grid" id="g"></div>
<script>
const DATA = __DATA__;
const cache = {};
const load = src => cache[src] || (cache[src] = new Promise(r => { const i = new Image(); i.onload = () => r(i); i.src = src; }));
const scale = Math.max(1, Math.round(192 / DATA.frame[0]));
for (const a of DATA.anims) {
  const cell = document.createElement('div'); cell.className = 'cell';
  const c = document.createElement('canvas'); c.width = a.w * scale; c.height = a.h * scale;
  cell.append(c, a.name); document.getElementById('g').append(cell);
  const ctx = c.getContext('2d'); ctx.imageSmoothingEnabled = false;
  load(a.image).then(img => { let f = 0; const draw = () => { ctx.clearRect(0, 0, c.width, c.height);
    ctx.drawImage(img, a.x + f * a.w, a.y, a.w, a.h, 0, 0, c.width, c.height); f = (f + 1) % a.frames; };
    draw(); setInterval(draw, 1000 / a.fps); });
}
</script>
"""


def build(lib, npc, clip_id, preset_id, options=None, models=None):
    """Return {filename: bytes} for one clip of one NPC. Raises ValueError with a readable message."""
    options = options or {}
    if preset_id not in PRESETS:
        raise ValueError(f"Unknown export preset '{preset_id}' (available: {', '.join(PRESETS)})")
    preset = PRESETS[preset_id]
    meta = npc.get("assets", {}).get("animations", {}).get(clip_id)
    if not meta or not meta.get("files"):
        raise ValueError(f"This NPC has no finished '{clip_id}' animation yet")
    dirs = options.get("dirs") or preset["dirs"] or meta["order"]
    missing = [d for d in dirs if d not in meta["files"]]
    if missing:
        raise ValueError(f"'{clip_id}' has no frames for: {', '.join(missing)} (it has {', '.join(meta['order'])})")
    count = meta["frames"]
    if preset["frames"] and count != preset["frames"]:
        raise ValueError(f"The {preset['label'].split(':')[0]} preset needs {preset['frames']} frames per direction; this clip has {count}")
    size = max(16, min(1024, int(options.get("frame") or preset["frame"])))
    pixel = options.get("pixelate")
    pixel = (npc.get("style") in ("pixel", "pixel8") and size <= 128) if pixel is None else bool(pixel)   # auto: small pixel-art frames
    colors = max(4, min(64, int(options.get("colors") or 24)))
    outline = bool(options.get("outline"))
    name = slugify(options.get("name") or preset.get("name") or f'{npc["profile"]["first_name"]}-{npc["profile"]["last_name"]}')
    stem = name if preset_id == "grid4" else f"{name}_{clip_id}"

    frames = {d: [downscale(Image.open(lib.resolve(npc["id"], rel)).convert("RGBA"), size, pixel) for rel in meta["files"][d]]
              for d in dirs}
    used = {d: licences.animation_engines(meta, d) for d in dirs}
    provenance = _provenance(npc, meta, list(used.values()), "animation frames")
    provenance["seeds"] = {d: v.get("seed") for d, v in meta.get("directions", {}).items() if isinstance(v, dict)}
    if pixel:                                                        # one palette for the whole clip: no colour shimmer
        flat = pixelate_set([f for d in dirs for f in frames[d]], colors, outline)
        frames = {d: flat[i * count:(i + 1) * count] for i, d in enumerate(dirs)}
    files, anims = {}, []
    if preset["layout"] == "grid":
        sheet = Image.new("RGBA", (count * size, len(dirs) * size), (0, 0, 0, 0))
        for row, d in enumerate(dirs):
            for i, f in enumerate(frames[d]):
                sheet.paste(f, (i * size, row * size))
            anims.append({"name": f"{clip_id}_{d}", "image": f"{stem}.png", "x": 0, "y": row * size, "w": size, "h": size,
                          "frames": count, "fps": meta["fps"], "loop": meta["loop"], "godot_name": f"{clip_id}_{d}"})
        files[f"{stem}.png"] = media.png(sheet, models=provenance["engines"], optimize=True)
    else:
        for d in dirs:
            strip = Image.new("RGBA", (count * size, size), (0, 0, 0, 0))
            for i, f in enumerate(frames[d]):
                strip.paste(f, (i * size, 0))
            image = f"{stem}.png" if len(dirs) == 1 else f"{stem}_{d}.png"
            files[image] = media.png(strip, models=used[d], optimize=True)
            anims.append({"name": f"{clip_id}_{d}", "image": image, "x": 0, "y": 0, "w": size, "h": size, "frames": count,
                          "fps": meta["fps"], "loop": meta["loop"], "godot_name": clip_id if len(dirs) == 1 else f"{clip_id}_{d}"})

    atlas = {
        "clip": clip_id, "frame": [size, size], "columns": count, "fps": meta["fps"], "loop": meta["loop"],
        "anchor": [size // 2, round(BASELINE / CANVAS * size)],
        "mirrored": meta.get("mirrored", {}), "pixelated": pixel,
        "animations": {a["name"]: {"image": a["image"], "x": a["x"], "y": a["y"], "frames": a["frames"], "fps": a["fps"],
                                    "rects": [[a["x"] + i * size, a["y"], size, size] for i in range(a["frames"])]} for a in anims},
        "provenance": provenance,
    }
    if preset_id == "unity":
        manifest = _unity_manifest(anims, size, f"{stem}.png", pixel, atlas["anchor"], provenance)
    elif preset_id == "aseprite":
        manifest = _aseprite_json(anims, size, f"{stem}.png", count, dirs, meta["loop"], meta["fps"], provenance)
    else:
        manifest = atlas
    files[f"{stem}.json"] = json.dumps(manifest, indent=2).encode()
    if preset_id == "godot":
        res = _resource_path(options.get("res_path"), f"res://{name}")
        files[f"{name}.tres"] = _tres(anims, size, res).encode()
    title = f'{(npc["profile"]["first_name"] + " " + npc["profile"]["last_name"]).strip()}: {meta["label"]}'
    data = {"frame": [size, size], "anims": [{k: a[k] for k in ("name", "image", "x", "y", "w", "h", "frames", "fps")} for a in anims]}
    model_note = f"Models used: {', '.join(provenance['engines'])}." if provenance["engines"] else "Model unrecorded."
    files["preview.html"] = (_PREVIEW.replace("__MODELS__", html.escape(model_note))
                             .replace("__TITLE__", html.escape(title)).replace("__DATA__", _script_json(data)).encode())
    return files


def build_sheet(lib, npc, options=None):
    """{filename: bytes} for a finished model sheet: every view as its own PNG, the stitched sheet and a JSON record.

    The views are white-background panels of one size, with the figures at the same height on the same baseline, so
    they can be dropped into a 3D-modelling or image-to-3D workflow as they are.
    """
    meta = npc.get("assets", {}).get("sheet")
    if not meta or not meta.get("views"):
        raise ValueError("This NPC has no model sheet yet")
    name = slugify((options or {}).get("name") or f'{npc["profile"]["first_name"]}-{npc["profile"]["last_name"]}')
    order = [v for v in VIEWS if v in meta["views"]]
    used = {v: [licences.picture_engine(meta["views"][v], edit=True)] for v in order}
    provenance = _provenance(npc, meta, list(used.values()), "model-sheet views")
    files = {f"{name}_{v}.png": media.label(lib.read_asset(npc["id"], meta["views"][v]["file"]), used[v]) for v in order}
    if meta.get("file"):
        composite = lib.read_asset(npc["id"], meta["file"])
        models = media.source_models(composite) or ([] if meta.get("stale") else provenance["engines"])
        files[f"{name}_sheet.png"] = media.label(composite, models)
    record = {
        "character": f'{npc["profile"]["first_name"]} {npc["profile"]["last_name"]}'.strip(), "panel": meta.get("panel"),
        "pose": meta.get("pose"), "order": order, "report": meta.get("report"),
        "views": {v: {"file": f"{name}_{v}.png", "seed": meta["views"][v].get("seed"), "from": meta["views"][v].get("from"),
                      "prompt": meta["views"][v].get("prompt")} for v in order},
        "provenance": provenance,
    }
    files[f"{name}_sheet.json"] = json.dumps(record, indent=2).encode()
    return files


def to_zip(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(_safe_member(name), media.label(data))
    return buf.getvalue()


def write_dir(files, out_dir):
    out_dir.mkdir(parents=True, exist_ok=True)
    root = out_dir.resolve()
    for name, data in files.items():
        target = (root / _safe_member(name)).resolve()
        if not target.is_relative_to(root):
            raise ValueError(f"Unsafe export file name: {name!r}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(media.label(data))
    return sorted(files)
