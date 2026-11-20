"""A sharper face for a cleaned-up 3D model: head close-ups, and a texture layout with room for them.

Measured on Clara and Imani (Pixal3D medium, 2026-09-26), the face was held back twice over:
  - the turnaround pictures show the face about 145 pixels across, and
  - the model's texture gives the whole body the same density, so the face gets about 190 texels across.
Painting from sharper pictures alone could gain at most that 1.3x, and a bigger texture alone gains nothing.

So, only when asked (the "Sharper face" button; never automatically):
  1. Close-ups. The head of each turnaround view is cut out and upscaled 4x by an upscale model in ComfyUI. Only an
     upscaler: Mage-Flow-Edit redrawing the close-up (strength 0.35 to 1) changed the character (lip colour in one view
     only, eyelashes, restyled line work), and a change in one view but not the others shows as a mismatch on the model.
     The upscaled picture lines up with its view pixel for pixel, so it needs no fitting of its own (paint3d uses the
     view's fit, scaled).
  2. Head layout (head_layout). The model's colour texture becomes twice as wide: the left half is the generator's own
     texture, untouched, and the right half holds the head alone, unwrapped afresh by xatlas. The head gets about 2.5x
     the texel density (Clara: 1300 -> 3300 texels per unit of height), in one material. The generator's normal map
     stays on its own layout as a second UV set until the game version bakes a new one (npcgen/blender/game_lod.py).
     The generator's layout cannot simply be repacked: after the clean-up it is about 6,500 islands, 1,600 of them a
     single triangle, and Blender's packer gave different, overlapping results on it from run to run.
"""
import io

import numpy as np
from PIL import Image

from .comfy import ComfyError
from .atlasprocess import AtlasProcessError, generate_layout
from .paint3d import Glb, _bilinear, _dilate_into, rasterize, textured_primitives

LAYOUT_HEAD_FROM = 0.18    # of the height, down from the top: the part that gets the fine texture (chin and neck included)
ATLAS_RESOLUTION = 2048    # the head's half of the texture
ATLAS_PADDING = 2          # texels between xatlas charts (at ATLAS_RESOLUTION)
CROP_SHARE = 0.25          # close-up crop side, as a share of the figure's height (head, hair and neck)
CROP_TOP = 0.02            # margin above the top of the head, as a share of the figure's height
# Upscale models looked for, in this order ("face_upscaler" in config.json names another). Their licences matter for
# anything you sell (NOTICE.md). Compared on Clara and Imani (2026-09-26): RealESRGAN_x4plus is crisp and faithful, a little
# smooth; 4x-UltraSharp keeps more skin and wound texture but invents a little (a white highlight under Imani's nose) and is
# non-commercial; 4x-ESRGAN.pth was barely sharper than a plain resize.
UPSCALERS = ("RealESRGAN_x4plus.pth", "4x-UltraSharp.pth")
LICENCES = {"4x-UltraSharp.pth": "CC BY-NC-SA 4.0: non-commercial use only",
            "RealESRGAN_x4plus.pth": "BSD-3-Clause"}


class HeadDetailError(ComfyError):
    """The sharper face cannot be made (no upscale model, no xatlas). The message is safe to show."""


# ---------------------------------------------------------------- close-ups

def head_box(view_png):
    """Square crop box (x0, y0, x1, y1) around the head in a turnaround view, from the figure's outline. It may reach past
    the picture's edges; crop() fills that with the backdrop."""
    from .paint3d import foreground_mask
    rgb = np.asarray(Image.open(io.BytesIO(view_png)).convert("RGB"))
    mask = foreground_mask(rgb)
    ys = np.nonzero(mask.any(1))[0]
    if not len(ys):
        raise HeadDetailError("A turnaround view shows no figure to take the head from")
    top, height = int(ys[0]), int(ys[-1] - ys[0] + 1)
    cols = np.nonzero(mask[top:top + max(1, int(0.15 * height))].any(0))[0]
    centre = (cols[0] + cols[-1] + 1) / 2
    side = max(16, int(round(CROP_SHARE * height / 16)) * 16)
    x0, y0 = int(round(centre - side / 2)), int(round(top - CROP_TOP * height))
    return x0, y0, x0 + side, y0 + side


def crop(view_png, box):
    """The box cut from the view as PNG bytes, the backdrop colour filling anything outside the picture."""
    im = Image.open(io.BytesIO(view_png)).convert("RGB")
    rgb = np.asarray(im)
    border = np.concatenate([rgb[:4].reshape(-1, 3), rgb[-4:].reshape(-1, 3), rgb[:, :4].reshape(-1, 3), rgb[:, -4:].reshape(-1, 3)])
    out = Image.new("RGB", (box[2] - box[0], box[3] - box[1]), tuple(int(c) for c in np.median(border, 0)))
    out.paste(im, (-box[0], -box[1]))
    buf = io.BytesIO()
    out.save(buf, "PNG")
    return buf.getvalue()


def upscale_graph(image, model_name):
    return {"1": {"class_type": "LoadImage", "inputs": {"image": image}},
            "2": {"class_type": "UpscaleModelLoader", "inputs": {"model_name": model_name}},
            "3": {"class_type": "ImageUpscaleWithModel", "inputs": {"upscale_model": ["2", 0], "image": ["1", 0]}},
            "4": {"class_type": "PreviewImage", "inputs": {"images": ["3", 0]}}}


def _xatlas():
    try:
        import xatlas  # noqa: F401
        return True
    except ImportError:
        return False


def _upscalers(comfy):
    try:
        return comfy.get_json("/models/upscale_models", timeout=10)
    except ComfyError:
        return []


def _choose(have, wanted=None):
    return next((name for name in ((wanted,) if wanted else ()) + UPSCALERS if name in have), None)


def availability(comfy, wanted=None):
    """{"upscaler": name or None, "licence": text, "missing": [...]} for the page: what the sharper face would use."""
    name = _choose(_upscalers(comfy), wanted)
    missing = [] if _xatlas() else ["the xatlas Python package (pip install -r requirements.txt)"]
    if not name:
        missing.append("a 4x upscale model in ComfyUI's models/upscale_models (" + " or ".join(UPSCALERS) + ")")
    return {"upscaler": name, "licence": LICENCES.get(name, "see the model's own page") if name else None, "missing": missing}


def pick_upscaler(comfy, wanted=None):
    """The upscale model to use: `wanted` if ComfyUI has it, else the first of UPSCALERS it has."""
    name = _choose(_upscalers(comfy), wanted)
    if name:
        return name
    raise HeadDetailError("The sharper face needs a 4x upscale model in ComfyUI's models/upscale_models folder "
                          f"(any of {', '.join(UPSCALERS)}); none was found")


def closeups(comfy, views, npc_id, model_name, cancel=None):
    """{view: {"png": upscaled head, "box": crop box in the view}} for the turnaround views ({view: PNG bytes})."""
    out = {}
    for view, png in views.items():
        box = head_box(png)
        name = comfy.upload_image(crop(png, box), f"{npc_id}_head_{view}.png")
        big = comfy.run(upscale_graph(name, model_name), cancel=cancel, timeout=300)
        w, h = Image.open(io.BytesIO(big)).size
        if w != h or w < 2 * (box[2] - box[0]):
            raise HeadDetailError(f"{model_name} returned a {w}x{h} picture for a {box[2] - box[0]}-pixel crop; "
                                  "the sharper face needs a 4x upscale model")
        out[view] = {"png": big, "box": list(box)}
    return out


# ---------------------------------------------------------------- head layout

def _cross2(a, b):
    return a[..., 0] * b[..., 1] - a[..., 1] * b[..., 0]


def _density(pos, uv, tri, size):
    """Texels per unit of length over these triangles, for a texture `size` (w, h)."""
    t = uv[tri] * size
    ua = np.abs(_cross2(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0])).sum() / 2
    wa = np.linalg.norm(np.cross(pos[tri[:, 1]] - pos[tri[:, 0]], pos[tri[:, 2]] - pos[tri[:, 0]]), axis=1).sum() / 2
    return float(np.sqrt(ua / max(wa, 1e-20)))


def _widen(image, head_uv, head_tri, old_uv):
    """The image twice as wide: itself on the left, and on the right each head texel sampled from where the generator's
    layout (old_uv, per head vertex) had that surface point."""
    mode = "RGBA" if image.mode in ("RGBA", "LA", "P") and "A" in image.getbands() else "RGB"
    old = np.asarray(image.convert(mode)).astype(np.float32)
    h, w = old.shape[:2]
    right = np.zeros((h * w, old.shape[2]), np.float32)
    pix, t, bary = rasterize(head_uv * [w, h], head_tri, w, h)
    at = np.einsum("nk,nkd->nd", bary, old_uv[head_tri[t]]) * [w, h]
    right[pix] = _bilinear(old, at[:, 0], at[:, 1])
    known = np.zeros(h * w, bool)
    known[pix] = True
    right = _dilate_into(right.reshape(h, w, -1), known.reshape(h, w), 8)
    wide = np.concatenate([old, right], 1)
    buf = io.BytesIO()
    Image.fromarray(np.clip(wide, 0, 255).astype(np.uint8), mode).save(buf, "PNG", compress_level=6)
    return buf.getvalue()


def head_layout(model_glb, report=None, *, cancel=None):
    """The GLB with the head given a fine texture of its own (see the module notes). Needs xatlas (pip install xatlas)."""
    try:
        import xatlas
    except ImportError:
        raise HeadDetailError("The sharper face needs the xatlas package: run  pip install -r requirements.txt") from None
    glb = Glb(model_glb)
    prims = textured_primitives(glb, with_primitive=True)
    if len(prims) != 1:
        raise HeadDetailError(f"The sharper face expects one textured mesh; this model has {len(prims)}")
    pos, nrm, uv, tri, image_index, prim = prims[0]
    attrs = prim["attributes"]
    material = glb.json["materials"][prim["material"]]
    pbr = material.get("pbrMetallicRoughness", {})
    if "TEXCOORD_1" in attrs or pbr["baseColorTexture"].get("texCoord", 0) != 0:
        raise HeadDetailError("The model already has a second texture layout")
    raw_uv = glb.accessor(attrs["TEXCOORD_0"])
    if raw_uv.dtype != np.float32:
        raise HeadDetailError("The model's texture coordinates are not plain floats")
    report = report if report is not None else {}

    top, height = pos[:, 1].max(), pos[:, 1].max() - pos[:, 1].min()
    head = pos[tri].mean(1)[:, 1] > top - LAYOUT_HEAD_FROM * height
    used = np.unique(tri[head])
    local = np.full(len(pos), -1)
    local[used] = np.arange(len(used))
    try:
        layout = generate_layout(pos[used], local[tri[head]], nrm[used], resolution=ATLAS_RESOLUTION,
                                 padding=ATLAS_PADDING, cancel=cancel)
    except AtlasProcessError as exc:
        raise HeadDetailError(str(exc)) from None
    if int(layout["atlas_count"]) != 1:
        raise HeadDetailError("The head did not fit one texture")
    vmap, head_tri, head_uv = layout["vmap"], layout["triangles"], layout["uv"]
    width, height = int(layout["width"]), int(layout["height"])
    head_uv = head_uv.astype(np.float64) * np.array([width, height]) / max(width, height)
    head_vert = used[vmap.astype(np.int64)]                  # the original vertex behind each new head vertex
    head_tri = head_tri.astype(np.int64)
    n = len(pos)

    for name, index in list(attrs.items()):
        if name != "TEXCOORD_0":
            values = glb.accessor(index)
            glb.set_accessor(index, np.concatenate([values, values[head_vert]]).astype(values.dtype))
    new_uv = np.concatenate([raw_uv * [0.5, 1], head_uv * [0.5, 1] + [0.5, 0]]).astype(np.float32)
    glb.set_accessor(attrs["TEXCOORD_0"], new_uv)
    attrs["TEXCOORD_1"] = glb.add_accessor(np.concatenate([raw_uv, raw_uv[head_vert]]).astype(np.float32))
    glb.set_triangles(prim, np.concatenate([tri[~head], head_tri + n]))

    # every map but the normal map is widened; the normal map stays on the generator's layout (TEXCOORD_1)
    textures = glb.json["textures"]
    normal = material.get("normalTexture")
    normal_image = textures[normal["index"]]["source"] if normal else None
    widened = set()
    for slot in (pbr.get("baseColorTexture"), pbr.get("metallicRoughnessTexture"), material.get("occlusionTexture"),
                 material.get("emissiveTexture")):
        if not slot:
            continue
        source = textures[slot["index"]]["source"]
        if source == normal_image:
            raise HeadDetailError("The model's normal map is shared with another map")
        slot["texCoord"] = 0
        if source not in widened:
            glb.replace_image(source, _widen(glb.image(source), head_uv, head_tri, raw_uv[head_vert].astype(np.float64)))
            glb.json["images"][source]["mimeType"] = "image/png"
            widened.add(source)
    if normal:
        normal["texCoord"] = 1

    size = glb.image(image_index).size
    report["head_layout"] = {
        "head_faces": int(head.sum()), "charts": int(layout["chart_count"]), "texture": list(size),
        "head_texels_per_unit_before": round(_density(pos, uv, tri[head], (size[0] // 2, size[1]))),
        "head_texels_per_unit": round(_density(pos[head_vert], head_uv * [0.5, 1] + [0.5, 0], head_tri, size)),
    }
    return glb.dumps()
