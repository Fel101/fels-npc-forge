"""Attach the detailed head to the whole-body model: an option of the clean-up (npcgen/cleanup3d.py), docs/detailed-head.md.

The detailed head (npcgen/head3d.py) is a separate, fine model of the head; the whole-body model still has its mask of a face.
Attaching lays the head's FACE over the body's: a patch of the head model from the chin to the hairline and cheek to cheek, fitted
on the body's face and joined to it, so the body keeps its own hair, ears, neck and clothes, and the rig (npcgen/rig3d.py) and the
game version carry the new face like any other part of the model.

How (the geometry is npcgen/blender/head_graft.py's, its numpy parts npcgen/blender/graft_math.py's):
  1. attach(): Blender renders a front view of the body's head and of the head model (npcgen/blender/face_views.py);
     SDPose (ComfyUI) finds the 68 face points in each (face_points(): a detection, then a close-up of the face cut from it, as
     posekeys.keypoints does); head_graft.py lifts them onto the models and fits the head on the body from the nose, eyes and
     mouth, cuts the patch, eases its edge onto the body, lays the body's face on a backing just behind it and joins the two.
  2. blend_colour(): the patch's paint is matched to the body's: along its edge the paint crosses over to the colour the body
     has there, and the whole patch is given the gain that makes its skin the body's tone (a face at one tone and a neck at another
     is the seam that shows most).
  3. fill_backing_colour(): the body's skin under the patch (its mask, laid flat just behind the patch) takes the patch's colour, so a
     hole in the patch shows skin that matches, not the body's own tone in a tile with hard edges.
The patch keeps its own material ("npcforge_head") and textures, so the model has two materials afterwards.

Only when asked ("Attach the detailed head" in the Game version section, or --attach-head): it needs the detailed head, Blender and
SDPose, and it is not for characters whose face is not a human's (a face detector finds no face on a paw or a beak), or who wear
something over it (a helmet, a mask): the model then keeps its own face and the job says why.
"""
import io
import json
import pathlib
import tempfile
import time

import numpy as np
from PIL import Image

from . import posekeys, rig3d
from .blender import graft_math as gm
from .comfy import Cancelled, ComfyError
from .paint3d import Glb, _colour_grid, _grid_read, dot3, texel_samples, textured_primitives

BLENDER_DIR = pathlib.Path(__file__).resolve().parent / "blender"
VIEWS_SCRIPT = BLENDER_DIR / "face_views.py"
GRAFT_SCRIPT = BLENDER_DIR / "head_graft.py"
PATCH_MATERIAL = "npcforge_head"           # the patch's material's name, set by head_graft.py
TIMEOUT_SECONDS = 900
MIN_FACE_SCORE = 0.3


class GraftError(ComfyError):
    """The head cannot be attached. The message is safe to show."""


def availability(cfg, comfy):
    """{"model", "licence", "missing": [...]}: Blender, and SDPose in ComfyUI to find the faces."""
    pose = posekeys.availability(comfy) if comfy is not None else {"missing": ["ComfyUI"]}
    return {"model": posekeys.CHECKPOINT, "licence": posekeys.LICENCE, "missing": rig3d.availability(cfg)["missing"] + pose["missing"]}


class Attacher:
    """What attaching needs beyond the body: ComfyUI (SDPose), the character's id (to name the pictures sent) and the head model."""

    def __init__(self, comfy, npc_id, head_glb, head_created=None):
        self.comfy, self.npc_id, self.head_glb, self.head_created = comfy, npc_id, head_glb, head_created


def face_points(comfy, picture, npc_id, key, cancel=None):
    """SDPose's 68 face points of the face in a picture, as [[u, v, score], ...] in the picture's pixels: a detection on the picture,
    then a close-up of the face cut from it (npcgen/posekeys.py; the close-up finds the jaw, lips, eyes and brows better)."""
    coarse = posekeys.detect(comfy, {key: picture}, npc_id, cancel)[key]["face_keypoints_2d"][:68]
    box = posekeys.face_box(coarse, picture.size)
    if box is None:
        raise GraftError(f"SDPose found no face in the {key} (a face detector needs a human face, without a helmet or a mask over it)")
    zoom = posekeys.detect(comfy, {key + "_zoom": posekeys.crop(picture, box)}, npc_id, cancel)[key + "_zoom"]["face_keypoints_2d"][:68]
    points = [[u + box[0], v + box[1], s] for u, v, s in zoom]
    if len(points) < 68 or float(np.mean([p[2] for p in points])) < MIN_FACE_SCORE:
        raise GraftError(f"SDPose was not sure of the face in the {key}")
    return points


def attach(cfg, body_glb, attacher, cancel=None):
    """The body model (GLB bytes) with the detailed head's face laid over its own: (GLB bytes, poster PNG bytes or None, report).
    Raises ComfyError with a readable reason when the faces cannot be found or fitted."""
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="npcforge-head-") as tmp:
        tmp = pathlib.Path(tmp)
        body, head, out = tmp / "body.glb", tmp / "head.glb", tmp / "grafted.glb"
        body.write_bytes(body_glb)
        head.write_bytes(attacher.head_glb)
        views = tmp / "views"
        rig3d.run_script(cfg, VIEWS_SCRIPT, [str(body), str(head), str(views)], tmp, views / "views.json",
                         "Rendering the faces", "Blender could not render the faces for the face detector", cancel=cancel,
                         timeout=TIMEOUT_SECONDS)
        found = {}
        for name in ("body", "head"):
            picture = Image.open(views / f"{name}_front.png").convert("RGB")
            found[name] = {"points": face_points(attacher.comfy, picture, attacher.npc_id, name, cancel)}
        (tmp / "landmarks.json").write_text(json.dumps(found), "utf-8")
        report, poster = tmp / "report.json", tmp / "poster.png"
        rig3d.run_script(cfg, GRAFT_SCRIPT, [str(body), str(head), str(out), "--views", str(views / "views.json"),
                                             "--landmarks", str(tmp / "landmarks.json"), "--report", str(report), "--poster", str(poster)],
                         tmp, out, "Attaching the head", "The detailed head could not be attached", cancel=cancel,
                         timeout=TIMEOUT_SECONDS)
        info = json.loads(report.read_text("utf-8")) if report.exists() else {}
        info["face_scores"] = {n: round(float(np.mean([p[2] for p in found[n]["points"]])), 2) for n in found}
        matched, info["colour"] = blend_colour(out.read_bytes())
        matched, info["holes"] = fill_backing_colour(matched)
        info["seconds"] = round(time.monotonic() - started, 1)
        return matched, poster.read_bytes() if poster.exists() else None, info


def try_attach(cfg, body_glb, attacher, cancel=None):
    """attach(), but a face that cannot be attached leaves the model as it was: (GLB, poster or None, report), the report
    {"error": reason} then. The rest of a clean-up is minutes of work worth keeping."""
    try:
        return attach(cfg, body_glb, attacher, cancel=cancel)
    except Cancelled:
        raise
    except ComfyError as e:
        return body_glb, None, {"error": str(e)}


def patch_distance(pos, tri):
    """Distance along the patch's surface (model units) from its outer edge, per vertex, and the weld that numbers each place once
    however many texture-seam copies of it there are: (distance per vertex, weld). The outer edge is the widest boundary loop seen
    from the front; the others are holes in the patch."""
    _, weld = np.unique(np.round(pos / 1e-5).astype(np.int64), axis=0, return_inverse=True)
    weld = weld.reshape(-1)
    n = int(weld.max()) + 1
    corners = weld[tri]
    pairs = np.sort(np.concatenate([corners[:, [0, 1]], corners[:, [1, 2]], corners[:, [2, 0]]]), axis=1)
    key = pairs[:, 0].astype(np.int64) * n + pairs[:, 1]
    unique, counts = np.unique(key, return_counts=True)
    edges = np.stack([unique // n, unique % n], 1)
    boundary = edges[counts == 1]
    placed = np.zeros((n, 3))
    placed[weld] = pos
    outer = gm.outer_loop(placed, gm.boundary_loops(boundary), axes=(0, 1))          # glTF: x across, y up
    dist, _origin = gm.surface_distance(placed, edges, outer)
    return dist[weld], weld


def blend_colour(glb_bytes, blend_mm=9.0):
    """(GLB bytes, info): the patch's colour matched to the body's, as this module's docstring says. `info` says the gain used and how
    many texels were changed; a model with no patch (or no body) comes back as it was, with an "error"."""
    glb = Glb(glb_bytes)
    prims = textured_primitives(glb, with_primitive=True)
    materials = glb.json.get("materials", [])
    patch = [p for p in prims if materials[p[5]["material"]].get("name") == PATCH_MATERIAL]
    body = [p for p in prims if materials[p[5]["material"]].get("name") != PATCH_MATERIAL]
    if not patch or not body:
        return glb_bytes, {"error": "the model has no patch or no body to match"}
    pos, nrm, uv, tri, image_index, _prim = patch[0]
    all_pos = np.concatenate([b[0] for b in body])
    height = float(all_pos[:, 1].max() - all_pos[:, 1].min())
    mm = height / 1700.0                                       # a millimetre of a 1.7 m person
    dist, weld = patch_distance(pos, tri)
    from_edge = dist[tri].mean(1) / mm                        # per triangle, in mm
    image = glb.image(image_index).convert("RGB")
    width, tall = image.size
    pixels = np.asarray(image, np.float32).reshape(-1, 3)
    pix, points, _n, t = texel_samples(pos, nrm, uv, tri, (width, tall), with_triangles=True)
    d_tex = from_edge[t]
    # the body's colour near each patch texel: its own texels' mean in a fine 3D grid
    sources, colours = [], []
    for bpos, bnrm, buv, btri, bimg, _bp in body:
        bimage = glb.image(bimg).convert("RGB")
        bpix, bpoints, _bn = texel_samples(bpos, bnrm, buv, btri, bimage.size)
        colours.append(np.asarray(bimage, np.float32).reshape(-1, 3)[bpix])
        sources.append(bpoints)
    sources, colours = np.concatenate(sources), np.concatenate(colours)
    everything = np.concatenate([sources, points])
    grid = _colour_grid(everything, np.concatenate([np.ones(len(sources), bool), np.zeros(len(points), bool)]),
                        np.concatenate([colours, np.zeros((len(points), 3))]), height, 0.004, 3)
    _keys, _rgb, lo, size, _dims = grid
    near, found = _grid_read(grid, ((points - lo) / size).astype(int))
    mine = pixels[pix]
    luminance = np.array([0.2126, 0.7152, 0.0722], np.float32)
    lum_mine, lum_near = dot3(mine, luminance) / 255.0, dot3(near, luminance) / 255.0      # by hand: see paint3d.dot3
    usable = found & (lum_mine > 0.25) & (lum_mine < 0.95) & (lum_near > 0.2) & (lum_near < 0.95)     # skin on both, not hair or shade
    gain = np.ones(3)
    if usable.sum() > 200:
        ratio = near[usable] / np.maximum(mine[usable], 1.0)
        good = ((ratio > 0.4) & (ratio < 2.5)).all(1)
        if good.sum() > 100:
            gain = np.clip(np.median(ratio[good], axis=0), 0.6, 1.5)
    toned = mine * gain
    weight = (gm.smoothstep(1.0 - d_tex / blend_mm) * found)[:, None]
    new = toned * (1 - weight) + near * weight
    out = pixels.copy()
    out[pix] = np.clip(new, 0, 255)
    buf = io.BytesIO()
    Image.fromarray(out.reshape(tall, width, 3).astype(np.uint8)).save(buf, "PNG")
    glb.replace_image(image_index, buf.getvalue())
    return glb.dumps(), {"gain": [round(float(g), 3) for g in gain], "texels": int(len(pix)), "edge_blend_texels": int((weight[:, 0] > 0).sum())}


def _blender_axes(points):
    """glTF's axes (x across, y up, z to the front) as head_graft.py and graft_math work in them (x across, y to the back, z up)."""
    return np.stack([points[:, 0], -points[:, 2], points[:, 1]], 1)


def fill_backing_colour(glb_bytes, fade_mm=3.0, tolerance_mm=3.0, spread_rounds=80):
    """(GLB bytes, info): the body's skin under the patch takes the patch's colour. head_graft.py lays the body's face flat just behind the
    patch; a hole in the patch (a lid, a lash, a dropped strand) shows that skin, in the body's own tone and texture: a flat tile with
    hard edges. Seen from the front, the patch's colour at every pixel of its area is found (its holes filled from the colours round
    them: graft_math.spread_colour), and the body's paint at every texel of the surface laid there is replaced by it, fading in over
    `fade_mm` from the patch's edge. A model with no patch (or no body) comes back as it was, with an "error"."""
    glb = Glb(glb_bytes)
    prims = textured_primitives(glb, with_primitive=True)
    materials = glb.json.get("materials", [])
    patch = [p for p in prims if materials[p[5]["material"]].get("name") == PATCH_MATERIAL]
    body = [p for p in prims if materials[p[5]["material"]].get("name") != PATCH_MATERIAL]
    if not patch or not body:
        return glb_bytes, {"error": "the model has no patch or no body to fill under"}
    pos, _nrm, uv, tri, image_index, _prim = patch[0]
    all_pos = np.concatenate([b[0] for b in body])
    mm = float(all_pos[:, 1].max() - all_pos[:, 1].min()) / 1700.0
    pixel = gm.BACKING["pixel"] * mm
    frame = _blender_axes(pos)
    maps = gm.backing_maps(frame, tri, pixel, max(1, int(round(gm.BACKING["env"] * mm / pixel))), int(gm.BACKING["fill_rounds"]))
    lo_x, hi_z = maps["origin"]
    width, height = maps["size"]
    hull, filled = maps["hull"], maps["filled"]
    rgb = np.asarray(glb.image(image_index).convert("RGB"), np.float64)
    seen, covered = gm.front_colours(frame, uv, tri, rgb, (width, height), (lo_x, hi_z), pixel)
    seen, spread = gm.spread_colour(seen, covered & hull, hull, rounds=spread_rounds)
    usable = (covered & hull) | spread                                         # where there is a colour to lay: not the middle of a huge hole
    edge_mm = gm.chamfer_inside(hull) * pixel / mm
    backing = filled + gm.BACKING["back"] * mm
    changed = 0
    for bpos, bnrm, buv, btri, bimage, _bp in body:
        q = _blender_axes(bpos)[btri].mean(1)                                  # which triangles are near the patch's area at all
        cx = np.floor((q[:, 0] - lo_x) / pixel).astype(int)
        cz = np.floor((hi_z - q[:, 2]) / pixel).astype(int)
        framed = (cx >= 0) & (cx < width) & (cz >= 0) & (cz < height)
        cx, cz = np.clip(cx, 0, width - 1), np.clip(cz, 0, height - 1)
        near = framed & hull[cz, cx] & (np.abs(q[:, 1] - backing[cz, cx]) < 60 * mm)
        if not near.any():
            continue
        image = glb.image(bimage).convert("RGB")
        size = image.size
        pix, points, _n = texel_samples(bpos, bnrm, buv, btri[near], size)
        b = _blender_axes(points)
        px = np.floor((b[:, 0] - lo_x) / pixel).astype(int)
        pz = np.floor((hi_z - b[:, 2]) / pixel).astype(int)
        ok = (px >= 0) & (px < width) & (pz >= 0) & (pz < height)
        px, pz = np.clip(px, 0, width - 1), np.clip(pz, 0, height - 1)
        under = ok & usable[pz, px] & (np.abs(b[:, 1] - backing[pz, px]) < tolerance_mm * mm)
        weight = gm.smoothstep(edge_mm[pz, px] / fade_mm) * under
        use = weight > 0
        if not use.any():
            continue
        pixels = np.asarray(image, np.float64).reshape(-1, 3)
        pixels[pix[use]] = pixels[pix[use]] * (1 - weight[use, None]) + seen[pz[use], px[use]] * weight[use, None]
        buf = io.BytesIO()
        Image.fromarray(np.clip(np.rint(pixels), 0, 255).reshape(size[1], size[0], 3).astype(np.uint8)).save(buf, "PNG")
        glb.replace_image(bimage, buf.getvalue())
        changed += int(use.sum())
    return glb.dumps(), {"texels": changed, "hole_share": round(float(spread.sum()) / max(int(hull.sum()), 1), 4)}


def summary(report):
    """What the model's record keeps (model_3d.refined.head): the fit, what was dropped, the counts and the colour match."""
    keep = ("fit", "u_mm", "dropped", "cut", "patch_faces", "eased", "body", "faces_after", "face_scores", "colour", "holes", "seconds")
    return {k: report[k] for k in keep if k in report}
