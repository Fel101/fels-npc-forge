"""Soften the eyes in the model-sheet pictures that a shape-only 3D generator (Trellis2) reads.

An image-to-3D model reads dark eye make-up, lashes and lid lines as shape. At the model sheet's size an eye is about 17 pixels
wide, so it cannot tell a painted line from a ridge, and it sculpts one: measured 2026-10-02 on Devi, Laura and Natalie, the
Trellis2 route gave narrow slits with a raised rim round each eye (Natalie's also sat under a hair visor). The eyes of a painted
model are drawn by the paint anyway, from the pictures, so nothing is lost by letting the SHAPE generator see skin there:

1. SDPose finds the face points (the same model the skeleton uses) in the front and side pictures, the front one again on a close-up.
2. Each eye, a soft ellipse stopping short of the eyebrow, is filled from the skin around it (a harmonic fill that never takes the
   white background in), and everything else in the picture stays as it was.
3. Only the shape generator gets these copies. The paint (npcgen/paint3d.py) still reads the original pictures.

Result (same seeds, same cameras, clay renders, then painted): Natalie's slits became soft indents and the hair visor over her eyes
parted; Devi's ringed slits went, leaving a smooth face or a flat band across the eyes, with no eye sockets either way. Glasses are
the exception: filling the eyes behind a pair turned Laura's open frames into solid lenses, like sunglasses, so a character whose
description has glasses is left alone (skip_reason). Only when asked: a model whose eyes come out well anyway loses their depth too,
and a character whose eyes SDPose cannot find (a mask, a visor, no face) stops the job with a message rather than being made unchanged.
"""
import io
import re

import numpy as np
from PIL import Image

from . import posekeys
from .comfy import ComfyError

VIEWS = ("front", "side_left", "side_right")        # the pictures that see the face; the back picture is left as it is
EYES = {"r": range(36, 42), "l": range(42, 48)}     # the 68 face points (iBUG): 36-41 the character's right eye, 42-47 the left
BROWS = {"r": range(17, 22), "l": range(22, 27)}
MIN_SCORE = 0.35                                    # an eye is only softened where SDPose is at least this sure of its points
WHITE_DISTANCE = 40                                 # a pixel closer than this (summed over the three channels) to white is background


GLASSES_WORDS = re.compile(r"\b(glasses|spectacles|sunglasses|goggles|monocle|shades|eyewear)\b", re.I)
GLASSES_FIELDS = ("glasses", "accessory", "extra", "distinguishing")      # the character sheet's appearance fields that can say so
NO_GLASSES = ("", "none", "no", "no glasses", "n/a")


class SoftenError(ComfyError):
    """The eyes could not be softened. The message is safe to show."""


def glasses_of(npc):
    """What the character wears on the face, from the character sheet ("thin round glasses"), or None: the `glasses` appearance field, or
    sunglasses, spectacles, goggles, a monocle named in the accessory, extra or distinguishing-features fields."""
    appearance = npc.get("appearance") or {}
    for field in GLASSES_FIELDS:
        value = appearance.get(field)
        if isinstance(value, str) and value.strip().lower() not in NO_GLASSES and (field == "glasses" or GLASSES_WORDS.search(value)):
            return value.strip()
    return None


def without_glasses(npc):
    """A copy of the character with the glasses left out of every field that can name them, for the pictures drawn without them: the
    `glasses` field emptied, and from accessory, extra and distinguishing features the comma-separated parts that name a pair."""
    clean = dict(npc)
    appearance = dict(npc.get("appearance") or {})
    for field in GLASSES_FIELDS:
        value = appearance.get(field)
        if not isinstance(value, str):
            continue
        if field == "glasses":
            appearance[field] = ""
        else:
            appearance[field] = ", ".join(part.strip() for part in value.split(",") if part.strip() and not GLASSES_WORDS.search(part))
    clean["appearance"] = appearance
    return clean


def skip_reason(npc, glasses_off=False):
    """Why the eyes of this character are better left alone, or None. A pair of glasses is read from the picture as lenses: with the eyes
    behind it filled with skin the generator made solid lenses (Laura, 2026-10-02), so a character with glasses is left alone, unless the
    pictures the shape is made from have had the glasses taken off (`glasses_off`: then there are none to turn into lenses)."""
    worn = glasses_of(npc)
    if worn and not glasses_off:
        return f"this character wears glasses ({worn}), and with the eyes behind them filled in the generator makes solid lenses"
    return None


def foreground(a):
    """Boolean mask of the pixels that are not the white background."""
    return np.abs(a.astype(int) - 255).sum(2) > WHITE_DISTANCE


def fill(a, mask, fg, rounds=400):
    """The pixels under `mask` replaced by a smooth fill from the foreground pixels around them (a harmonic fill: each pixel the
    mean of its neighbours, the white background never counted). The picture is returned as floats."""
    out = a.astype(float).copy()
    ys, xs = np.nonzero(mask)
    if not len(ys):
        return out
    y0, y1, x0, x1 = max(ys.min() - 2, 0), ys.max() + 3, max(xs.min() - 2, 0), xs.max() + 3
    sub, m, f = out[y0:y1, x0:x1], mask[y0:y1, x0:x1], fg[y0:y1, x0:x1].astype(float)
    known = f * (~m)
    sub[m] = (sub * known[..., None]).sum((0, 1)) / max(known.sum(), 1)      # start from the mean colour round the hole
    for _ in range(rounds):
        p = np.pad(sub * f[..., None], ((1, 1), (1, 1), (0, 0)), mode="edge")
        w = np.pad(f, 1, mode="edge")
        total = p[:-2, 1:-1] + p[2:, 1:-1] + p[1:-1, :-2] + p[1:-1, 2:]
        count = w[:-2, 1:-1] + w[2:, 1:-1] + w[1:-1, :-2] + w[1:-1, 2:]
        sub[m] = (total / np.maximum(count, 1)[..., None])[m]
    out[y0:y1, x0:x1] = sub
    return out


def eye_masks(points, eye_w, shape, fg, min_score=MIN_SCORE):
    """(weights, found): a soft elliptical weight (0-1) round each eye whose points scored well, and {side: [x, y, score]} of the
    eyes found. The ellipse reaches higher than the eye (lid and shadow), stops short of the eyebrow, and leaves the picture's
    outline pixels alone."""
    h, w = shape
    yy, xx = np.mgrid[0:h, 0:w]
    weight = np.zeros(shape)
    found = {}
    for side, idx in EYES.items():
        pts = np.array([points[i] for i in idx])
        if pts[:, 2].mean() < min_score:
            continue
        c = pts[:, :2].mean(0)
        c[1] -= 0.10 * eye_w                              # the lid and its shadow reach higher than the lower lashes
        a, b = 0.85 * eye_w, 0.62 * eye_w
        r = np.sqrt(((xx - c[0]) / a) ** 2 + ((yy - c[1]) / b) ** 2)
        ring = np.clip((1.25 - r) / 0.35, 0, 1)            # full inside r 0.9, fading out to r 1.25
        brow = np.array([points[i] for i in BROWS[side]])
        if brow[:, 2].mean() >= min_score:                 # stop short of the eyebrow: its line plus a margin
            order = np.argsort(brow[:, 0])
            line = np.interp(xx, brow[order, 0], brow[order, 1])
            ring *= np.clip((yy - (line + 0.18 * eye_w)) / (0.12 * eye_w), 0, 1)
        weight = np.maximum(weight, ring)
        found[side] = [float(c[0]), float(c[1]), float(pts[:, 2].mean())]
    inner = fg.copy()                                     # keep the anti-aliased outline pixels as they are
    for _ in range(2):
        inner[1:-1, 1:-1] &= inner[:-2, 1:-1] & inner[2:, 1:-1] & inner[1:-1, :-2] & inner[1:-1, 2:]
    return weight * inner, found


def soften_picture(picture, points, eye_w):
    """(softened picture as uint8, the sides of the eyes softened) for one RGB picture and its 68 face points."""
    a = np.asarray(picture)
    fg = foreground(a)
    weight, found = eye_masks(points, eye_w, a.shape[:2], fg)
    if not found or weight.max() <= 0.02:
        return a, []
    filled = fill(a, weight > 0.02, fg)
    soft = (a * (1 - weight[..., None]) + filled * weight[..., None]).round().clip(0, 255).astype(np.uint8)
    return soft, sorted(found)


def _rgb(png):
    """The picture as RGB, on white where it has transparency."""
    image = Image.open(io.BytesIO(png))
    if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
        rgba = image.convert("RGBA")
        flat = Image.new("RGB", rgba.size, (255, 255, 255))
        flat.paste(rgba, mask=rgba.getchannel("A"))
        return flat
    return image.convert("RGB")


def _png(a):
    out = io.BytesIO()
    Image.fromarray(a).save(out, "PNG")
    return out.getvalue()


def eye_width(points):
    """The mean width of the two eyes, in pixels, from the 68 face points (corner to corner)."""
    pts = np.asarray(points, dtype=float)
    return float(np.mean([np.linalg.norm(pts[39, :2] - pts[36, :2]), np.linalg.norm(pts[45, :2] - pts[42, :2])]))


def soften(comfy, pictures, npc_id, cancel=None):
    """Copies of the model-sheet pictures ({view: PNG bytes}) with the eyes filled with the skin round them, for a shape generator
    only. Returns ({view: PNG bytes} of every picture given, a report {"eye_width_px", "views": {view: [sides softened]}}).
    Raises SoftenError when SDPose cannot find the front picture's eyes."""
    images = {view: _rgb(pictures[view]) for view in VIEWS if view in pictures}
    if "front" not in images:
        raise SoftenError("Softening the eyes needs the front model-sheet picture")
    try:
        found = posekeys.detect(comfy, images, f"{npc_id}_eyes", cancel)
        faces = {view: found[view]["face_keypoints_2d"][:68] for view in images}
        box = posekeys.face_box(faces["front"], images["front"].size)
        if box is not None:                                # the front face again on a close-up: its points are much surer
            zoom = posekeys.detect(comfy, {"face": posekeys.crop(images["front"], box)}, f"{npc_id}_eyes", cancel)["face"]
            faces["front"] = [[u + box[0], v + box[1], s] for u, v, s in zoom["face_keypoints_2d"][:68]]
    except posekeys.PoseError as exc:
        raise SoftenError(f"Softening the eyes needs SDPose to find the face in the model-sheet pictures, and it did not: {exc}") from exc
    front = faces["front"]
    if len(front) < 48:
        raise SoftenError("SDPose found no face in the front model-sheet picture, so there are no eyes to soften")
    eye_w = eye_width(front)
    out = dict(pictures)
    report = {"eye_width_px": round(eye_w, 1), "views": {}}
    for view, image in images.items():
        points = faces[view]
        soft, sides = soften_picture(image, points, eye_w) if len(points) >= 48 and eye_w > 0 else (None, [])
        report["views"][view] = sides
        if sides:
            out[view] = _png(soft)
    if not report["views"]["front"]:
        raise SoftenError("SDPose found the face in the front model-sheet picture but not its eyes with enough certainty, so nothing "
                          "was softened. Untick 'Soften the eyes' to make the model from the pictures as they are")
    return out, report
