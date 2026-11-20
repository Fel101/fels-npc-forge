"""Bust size for a designed female body (lead, 2026-10-14).

The owner, looking at the soft-body motion ([softbody.py](softbody.py)): "I need to be able to adjust the size of the boobs for a better look." The Body Designer's own Chest size slider scales the whole
chest about the spine and moves the bust by only about 8 mm each way (measured: apex 115.4, 125.7 and 132.6 mm in front of the spine at -1, 0 and +1), so this is a control of its own: one number from -1
(smaller) through 0 (the body as the Designer made it) to +1 (larger).

Where it sits in a build: right after the Body Designer has made the neutral game body (a GLB, about a second), BEFORE the clips are copied on, the underwear is put on and the clothes are fitted, so every
later step sees the body as it now is (the underwear is made from its triangles, the clothes are fitted to it, the clip copier skins it as it stands). It changes the positions of the body's own mesh
(ForgeBody) and nothing else: not a triangle, not a UV, not a skin weight, not a bone, so the skeleton, the weights near the bust that npcgen/softbody.py reads, the textures and the face are exactly what they
were. At 0 nothing is done and the file is the Designer's byte for byte.

What it moves: every vertex of the front of the body within an oval around the apex of each breast is pushed forward (and a little down: a larger bust hangs a little lower) by a share of the move at the apex that falls
from 1 at the apex to 0 at the edge of the oval, smoothly (a cosine; the oval reaches farther below the apex than above it, and the same toward the middle as toward the arm). The back and the sides of the body
(behind the apex by more than WALL depth) and the vertices the arm pulls on are left alone, so the skin does not drag at the armpit. The move is added to the shape the Designer made (the surface details
ride along on it), and its size is about the apex alone: the surface moves by the shape of the falloff. Normals are worked out again where the surface moved (the Designer's own normal plus the change the new
triangles make), so the shading follows. Measured limits are in the Body Designer add-on's notes.

What it does not do: change the proportions the Designer's sliders make (it is applied after them), simulate anything (npcgen/softbody.py hangs its spring bones on the bust where it now is and reads the mark
this step leaves in the file's extras, `forge_bust`), or take a body that is not a designed adult female body.
"""
import math

import numpy as np

from . import softbody
from .paint3d import Glb

MARK = softbody.BUST_MARK
REACH_OUT = 0.042                       # metres the apex moves forward at +1 (for the Designer's default body, 1.59 m tall: bigger or smaller bodies scale with their height)
REACH_IN = 0.012                        # ... and back at -1
DROOP = 0.20                            # a larger bust hangs a little lower: this share of the forward move, downward
FOOTPRINT = {"up": 0.075, "down": 0.105, "out": 0.085, "in": 0.085}        # how far the change reaches from the apex, metres at the reference height: above it, below it, toward the arm, toward the middle
WALL = {"depth": 0.095, "fade": 0.03}   # the body's wall: vertices more than `depth` behind the apex (the back, the sides, the arms) are not moved; the edge of that is `fade` wide
FOREIGN_FADE = softbody.FOREIGN_FADE    # a vertex the arm pulls on is eased out of the move, as in the soft-body step
ARM_BONES = ("upperarm", "lowerarm", "hand", "thumb", "index", "middle", "ring", "pinky")
LABEL = "Bust size"


class BustError(ValueError):
    """The file cannot take a bust size, with the reason in words."""


def info():
    """For the card: {label, min, max, default, note}: the words and the numbers its slider needs."""
    return {"label": LABEL, "min": -1.0, "max": 1.0, "default": 0.0,
            "note": f"Makes the bust smaller (to the left) or larger (to the right) than the Body Designer made it: at the far right the tip of the bust is about {REACH_OUT * 1000:.0f} mm farther forward, at the far left {REACH_IN * 1000:.0f} mm "
                    "farther back (for a body 1.59 m tall), the skin around them following smoothly. It is made in the body itself, so the underwear and the clothes fit it, and a change means making the body again. "
                    "For the female body only. Soft-body motion, if you choose it, follows the new size."}


SHAPE_TOP = 3.0                        # the Body Designer's body shape (bodydesigner/body_design.py) takes a bust size up to this (its Advanced view); this module's own slider stops at 1


def clean(raw, where="bust", top=1.0):
    """The bust size a request asks for, checked: a number from -1 to `top` (1: this module's own slider; SHAPE_TOP for the size inside a body shape) (None, False and 0 are 0.0: no change).
    ValueError naming `where` for anything else (a true or a text is not a size)."""
    if raw is None or raw is False:
        return 0.0
    if type(raw) not in (int, float) or not math.isfinite(raw) or not -1 <= raw <= top:
        raise ValueError(f"{where}: expected a number from -1 to {top:g}")
    return float(raw)


def reach(size, scale=1.0):
    """How far the apex moves forward (metres; negative: back) for a size from -1 to 1 on a body `scale` times the Designer's default height."""
    size = clean(size)
    return (REACH_OUT * size if size > 0 else REACH_IN * size) * scale


def problem(extras):
    """Why a file cannot take a bust size, in words, or None. `extras` is the asset's extras: a designed adult female body, as the Body Designer makes it."""
    extras = extras or {}
    design = extras.get("body_design") or {}
    if extras.get("export_tier") != "game" or not design:
        return "Bust size is made on a body the Body Designer made."
    if type(extras.get("minimum_age")) is not int or extras["minimum_age"] < 18:
        return "The body does not say it is an adult's, so its bust size is not changed."
    if "female" not in str(design.get("base", "")):
        return "Bust size is made for the female body."
    return None


# ---- the field -------------------------------------------------------------------------------------------------------------------------------
def _smooth(t):
    t = np.clip(t, 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def _falloff(across, up, scale):
    """1 at the apex, 0 at the edge of the oval, smoothly: `across` is the distance from the apex toward the arm (negative: toward the middle), `up` upward (negative: below)."""
    ru = np.where(across >= 0, FOOTPRINT["out"], FOOTPRINT["in"]) * scale
    rv = np.where(up >= 0, FOOTPRINT["up"], FOOTPRINT["down"]) * scale
    t = np.minimum(np.hypot(across / ru, up / rv), 1.0)
    return 0.5 * (1.0 + np.cos(math.pi * t))


def displacement(points, weights_on_arm, apex, move, scale):
    """The move of each point (n x 3, metres) for a bust whose left apex is `apex` and whose apex moves forward by `move`: both breasts, the right one as the mirror of the left."""
    out = np.zeros_like(points)
    across = np.abs(points[:, 0]) - apex[0]                                    # (the body is the same on both sides: a point answers to the breast on its own side)
    up = points[:, 1] - apex[1]
    wall = _smooth((points[:, 2] - (apex[2] - WALL["depth"] * scale)) / (WALL["fade"] * scale))
    ease = 1.0 - _smooth(weights_on_arm / FOREIGN_FADE)
    share = _falloff(across, up, scale) * wall * ease
    out[:, 2] = move * share
    out[:, 1] = -DROOP * abs(move) * share * (1.0 if move > 0 else 0.0)
    return out


def _vertex_normals(positions, tri, weld):
    """Unit area-weighted normals of every vertex, the vertices at one place (the two sides of a texture seam) sharing one."""
    a, b, c = positions[tri[:, 0]], positions[tri[:, 1]], positions[tri[:, 2]]
    face = np.cross(b - a, c - a)
    total = np.zeros((int(weld.max()) + 1, 3))
    for column in range(3):
        np.add.at(total, weld[tri[:, column]], face)
    length = np.linalg.norm(total, axis=1)[:, None]
    return (total / np.where(length > 0, length, 1.0))[weld]


def _write(glb, accessor_index, array):
    """Put new float32 rows into an accessor where it already lies in the binary chunk (same place, same stride), and the new bounds in its JSON when it has them."""
    acc = glb.json["accessors"][accessor_index]
    view = glb.json["bufferViews"][acc["bufferView"]]
    width = {"VEC3": 3}[acc["type"]]
    if acc["componentType"] != 5126 or "sparse" in acc:
        raise BustError("The body's mesh is stored in a form the bust size does not handle.")
    item = 4 * width
    stride = view.get("byteStride") or item
    start = view.get("byteOffset", 0) + acc.get("byteOffset", 0)
    rows = np.ascontiguousarray(array, np.float32).reshape(-1, width)
    if len(rows) != acc["count"]:
        raise BustError("The body's mesh changed size.")
    buffer = bytearray(glb.bin)
    raw = np.frombuffer(buffer, np.uint8)
    np.lib.stride_tricks.as_strided(raw[start:], shape=(len(rows), item), strides=(stride, 1))[...] = rows.view(np.uint8).reshape(len(rows), item)
    glb.bin = bytes(buffer)
    if "min" in acc or "max" in acc:
        acc["min"], acc["max"] = rows.min(axis=0).astype(float).tolist(), rows.max(axis=0).astype(float).tolist()


def apply(data, size):
    """(the file with the bust size, a report). `size` is from -1 to 1 (0: nothing is done and `data` comes back as it is). BustError when the file cannot take it."""
    size = clean(size)
    if size == 0.0:
        return data, {"size": 0.0}
    glb = Glb(data)
    doc = glb.json
    extras = (doc.get("asset") or {}).get("extras")
    why = problem(extras)
    if why:
        raise BustError(why)
    if MARK in extras:
        raise BustError("This body already has a bust size: build it again from its recipe to change it.")
    if len(doc.get("skins", [])) != 1:
        raise BustError("The bust size needs a body with one skin.")
    skin = doc["skins"][0]
    joints = list(skin["joints"])
    names = [doc["nodes"][j]["name"] for j in joints]
    if any(name not in names for name in softbody.REQUIRED_BONES):
        raise BustError("The bust size needs the body's own spine and pelvis bones.")
    world = softbody._worlds(doc)
    bones = {name: world[j][:3, 3] for name, j in zip(names, joints)}
    scale = float((extras.get("landmarks") or {}).get("height_m") or softbody.REFERENCE_HEIGHT) / softbody.REFERENCE_HEIGHT
    skinned = softbody._skinned(doc)
    if "ForgeBody" not in skinned:
        raise BustError("The bust size needs the body's own skin.")
    primitive = skinned["ForgeBody"]
    attributes = primitive["attributes"]
    points = glb.accessor(attributes["POSITION"]).astype(np.float64)
    normals = glb.accessor(attributes["NORMAL"]).astype(np.float64)
    tri = glb.accessor(primitive["indices"]).reshape(-1, 3).astype(np.int64)
    jn = glb.accessor(attributes["JOINTS_0"]).astype(np.int64)
    wt = glb.accessor(attributes["WEIGHTS_0"]).astype(np.float64)
    try:
        apex = softbody._apex(points, bones, "chest", scale)
    except softbody.SoftBodyError:
        raise BustError("The bust could not be found on this body") from None
    arm = np.array([name.startswith(ARM_BONES) for name in names])
    on_arm = (wt * arm[jn]).sum(axis=1)
    move = reach(size, scale)
    shift = displacement(points, on_arm, apex, move, scale)
    moved = np.flatnonzero(np.abs(shift).max(axis=1) > 1e-7)
    if not len(moved):
        raise BustError("The bust size moved nothing on this body")
    after = points + shift
    key = np.round(points * 1e6).astype(np.int64)
    _, weld = np.unique(key, axis=0, return_inverse=True)
    weld = weld.reshape(-1)
    before_n, after_n = _vertex_normals(points, tri, weld), _vertex_normals(after, tri, weld)
    new_normals = normals + (after_n - before_n)
    length = np.linalg.norm(new_normals, axis=1)[:, None]
    new_normals = np.where(length > 0, new_normals / np.where(length > 0, length, 1.0), normals)
    flips = _flipped(points, after, tri)
    if flips:
        raise BustError(f"The bust size would fold {flips} triangles of this body: choose a smaller size.")
    _write(glb, attributes["POSITION"], after)
    _write(glb, attributes["NORMAL"], new_normals)
    apex_after = softbody._apex(after, bones, "chest", scale)
    extras[MARK] = {"version": 1, "size": round(size, 4), "reach_m": round(move, 5), "apex_before": [round(float(v), 5) for v in apex], "apex_after": [round(float(v), 5) for v in apex_after],
                    "scale": round(scale, 4), "moved": int(len(moved)), "footprint_m": {key: round(value * scale, 4) for key, value in FOOTPRINT.items()}, "droop": DROOP}
    report = {"size": round(size, 4), "reach_mm": round(move * 1000, 1), "apex": [round(float(v), 4) for v in apex], "apex_after": [round(float(v), 4) for v in apex_after], "moved": int(len(moved)),
              "biggest_mm": round(float(np.linalg.norm(shift, axis=1).max()) * 1000, 1), "scale": round(scale, 4)}
    return glb.dumps(), report


def mark_shape(shaped, neutral, size, shift, reach, moved=0):
    """(the file with the mark `forge_bust` of a body the Body Designer's body shape (bodydesigner/body_design.py `apply`) has shaped, a report {size, reach_mm, drop_mm, apex, apex_after}). Nothing of the
    body moves here: the body shape has made the bust, and this says where the bust is now, as `apply` does for its own, so that the soft-body step follows a bust that hangs lower or lies farther forward
    than the Designer's default one (`softbody.window_shift`, the bust's reach as the wall). `neutral` is the same body before the shape (for the mark's `apex_before`), `size` the shape's bust size (-1 to
    SHAPE_TOP), `shift` and `reach` how far the tip of the bust moved, down (negative) and forward, in metres, and `moved` how many vertices the shape moved. A body can have this mark or the bust size's
    own, never both. BustError when the file cannot take it."""
    size = clean(size, "bust", SHAPE_TOP)
    for name, value in (("shift", shift), ("reach", reach)):
        if type(value) not in (int, float) or not math.isfinite(value):
            raise BustError(f"The bust's {name} is not a number.")
    glb, before = Glb(shaped), Glb(neutral)
    doc = glb.json
    extras = (doc.get("asset") or {}).get("extras")
    why = problem(extras)
    if why:
        raise BustError(why)
    if MARK in extras:
        raise BustError("This body already has a bust size: build it again from its recipe to change it.")
    if len(doc.get("skins", [])) != 1:
        raise BustError("The bust size needs a body with one skin.")
    skin = doc["skins"][0]
    joints = list(skin["joints"])
    names = [doc["nodes"][j]["name"] for j in joints]
    if any(name not in names for name in softbody.REQUIRED_BONES):
        raise BustError("The bust size needs the body's own spine and pelvis bones.")
    world = softbody._worlds(doc)
    bones = {name: world[j][:3, 3] for name, j in zip(names, joints)}
    scale = float((extras.get("landmarks") or {}).get("height_m") or softbody.REFERENCE_HEIGHT) / softbody.REFERENCE_HEIGHT
    skinned, skinned_before = softbody._skinned(doc), softbody._skinned(before.json)
    if "ForgeBody" not in skinned or "ForgeBody" not in skinned_before:
        raise BustError("The bust size needs the body's own skin.")
    points = glb.accessor(skinned["ForgeBody"]["attributes"]["POSITION"]).astype(np.float64)
    points_before = before.accessor(skinned_before["ForgeBody"]["attributes"]["POSITION"]).astype(np.float64)
    shift = round(float(shift), 5)
    try:
        apex_before = softbody._apex(points_before, bones, "chest", scale)
        apex_after = softbody._apex(points, bones, "chest", scale, shift)
    except softbody.SoftBodyError:
        raise BustError("The bust could not be found on this body") from None
    extras[MARK] = {"version": 2, "source": "body_shape", "size": round(size, 4), "reach_m": round(float(reach), 5), "window_shift_m": shift,
                    "apex_before": [round(float(v), 5) for v in apex_before], "apex_after": [round(float(v), 5) for v in apex_after], "scale": round(scale, 4), "moved": int(moved)}
    return glb.dumps(), {"size": round(size, 4), "reach_mm": round(float(reach) * 1000, 1), "drop_mm": round(-shift * 1000, 1) + 0.0, "apex": [round(float(v), 4) for v in apex_before],
                         "apex_after": [round(float(v), 4) for v in apex_after]}


def copy_body(source, target):
    """`target` with the positions and the normals of the body's own mesh (ForgeBody) that `source` has: two files of one body (the same vertices in the same order) that differ in the bust. This is how the Body
    Designer's face shapes, which it works out only on the body exactly as it made it (bodydesigner/expressions.py compares every position), are added to a body with a bust size: the Designer's own positions
    go back on for that step and the bust's go on again after it (npcgen/bodymaker.py `build`). Nothing else of `target` changes (the morph targets it may have stay where they are). BustError when the
    two are not one body."""
    src, dst = Glb(source), Glb(target)
    try:
        a, b = softbody._skinned(src.json)["ForgeBody"]["attributes"], softbody._skinned(dst.json)["ForgeBody"]["attributes"]
    except KeyError:
        raise BustError("The bust size needs the body's own skin.") from None
    for name in ("POSITION", "NORMAL"):
        rows = src.accessor(a[name])
        if rows.shape != dst.accessor(b[name]).shape:
            raise BustError("The two files are not the same body.")
        _write(dst, b[name], rows)
    return dst.dumps()


def _flipped(before, after, tri):
    """How many triangles the move turned over or collapsed (their normal now points the other way, or has almost no length)."""
    def faces(p):
        return np.cross(p[tri[:, 1]] - p[tri[:, 0]], p[tri[:, 2]] - p[tri[:, 0]])
    old, new = faces(before), faces(after)
    dots = (old * new).sum(axis=1)
    return int(((dots <= 0.05 * (old * old).sum(axis=1)) & ((old * old).sum(axis=1) > 1e-14)).sum())


def check_file(data, size, shaped=False):
    """Why the file is not a body with the bust size `size` (a number from -1 to 1), in words, or None: the mark must be there and say the size, and the bust must lie where the mark says. With
    `shaped` (a body the Body Designer's body shape made: `mark_shape`) the size is the shape's, from -1 to SHAPE_TOP, the mark must be that of a body shape (also for a size of 0, since the
    shape moves the bust by its other controls too), and the bust must lie where the mark says with the window the mark gives; without it, a body shape's mark is not the bust size that was asked for."""
    size = clean(size, "bust", SHAPE_TOP if shaped else 1.0)
    glb = Glb(data)
    extras = (glb.json.get("asset") or {}).get("extras") or {}
    mark = extras.get(MARK)
    if shaped:
        if not isinstance(mark, dict) or mark.get("source") != "body_shape" or abs(float(mark.get("size", 0.0)) - size) > 1e-3:
            return "The body has not got the body shape's bust that was asked for"
    else:
        if isinstance(mark, dict) and mark.get("source") == "body_shape":
            return "The body has a bust size that was not asked for"
        if size == 0.0:
            return None if mark is None else "The body has a bust size that was not asked for"
        if not isinstance(mark, dict) or abs(float(mark.get("size", 0.0)) - size) > 1e-3:
            return "The body has not got the bust size that was asked for"
    try:
        doc = glb.json
        joints = doc["skins"][0]["joints"]
        world = softbody._worlds(doc)
        bones = {doc["nodes"][j]["name"]: world[j][:3, 3] for j in joints}
        points = glb.accessor(softbody._skinned(doc)["ForgeBody"]["attributes"]["POSITION"]).astype(np.float64)
        apex = softbody._apex(points, bones, "chest", float(mark["scale"]), softbody.window_shift(mark))
    except (KeyError, IndexError, ValueError, softbody.SoftBodyError):
        return "The bust of the body could not be looked at"
    if np.abs(apex - np.array(mark["apex_after"])).max() > 2e-3:
        return "The bust of the body is not where its bust size says it is"
    return None
