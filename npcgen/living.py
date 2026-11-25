"""Living portrait: a set of frames that lets an app animate a character's portrait (blink, talk, change expression).

The frames are the character's expressions (see data/expressions.json, the entries marked "living"), but an expression edit
redraws the whole picture a little: hair, clothes and background come back a few brightness levels off, and sometimes a few
pixels to one side. Swapping between such pictures flickers. So every frame here is the portrait itself, with only the
part of the face that changed blended in from the expression, after lining the expression up with the portrait. Everything
outside the face is then pixel-identical in every frame, and a blink or a word only moves the eyes or the mouth.

Finding the face: an edit also redraws texture (skin grain, satin, feathers) differently each time, and on a character in a
shiny dress that change can outweigh the face. So the images are compared slightly blurred, the face is placed where all the
expressions changed the picture (weighted towards the upper middle, where a portrait has it), and only changes inside it
are kept. Measured on three characters (photoreal and painterly, one in satin): blink and mouth come through, clothes and
background stay identical.

What changed is taken whole (a half-on mask showed a smile's teeth through closed lips), and the frame's colours are matched
to the portrait first (a painterly edit came back more orange). A blink or a word takes only the eyes or the mouth: a
painterly edit re-paints the texture of the whole face, which would flicker with every word. They are found from the frames
themselves, by the direction of the change: closing eyes turns two dark irises into lighter lids at the same height, an
opening mouth is a dark cavity, looked for below and between the eyes. (Finding them from "the strongest change" or "where
the face is" failed on real characters: a one-eyed blink, a mouth patch on a turned head's far eye.)
What no blending can fix: now and then the edit redraws the head itself, bigger or turned, which shows as a doubled cheek or
jaw. Four ways of measuring it on real frames did not separate it from a big laugh, so it is left to the eye: the Living
portrait card shows every frame with a Redo button.

Written next to the character:  living/<expression>.png  and  living/living.json  (what an app reads):
    {"version": 1, "portrait": {"created": <portrait stamp>, "sha256": <hash of portrait.png>}, "size": [w, h],
     "frames": {"blink": {"file": "living/blink.png", "source_created": <expression stamp>}, ...}, "made": <stamp>}
An app should only use the set while `portrait.sha256` matches the portrait it shows.
"""
import hashlib
import io
import json

import numpy as np
from PIL import Image, ImageFilter

from . import licences, media
from .library import now

VERSION = 1
SMALL = 256                  # the size the alignment and the change mask are worked out at
MAX_SHIFT = 0.05             # a larger estimated shift is a wrong estimate, not a moved head: the frame is used as it is
# How strong a change must be to be taken. A blink or a word shows for a tenth of a second at a time, so those take only the
# eyes or the mouth: at the everyday cut, a painterly character's re-painted cheek and neck came along and flickered.
SOLID = {"blink": 0.7, "talking": 0.7}


def living_ids(data):
    """The expressions that make up a living portrait, in the order they are listed."""
    return [e["id"] for e in data["expressions"] if e.get("living")]


def _gray_small(img):
    return np.asarray(img.convert("L").resize((SMALL, SMALL), Image.BILINEAR), dtype=np.float32)


def estimate_shift(base, frame):
    """(dy, dx) in full-size pixels that moves `frame` onto `base`, by phase correlation."""
    a, b = _gray_small(base), _gray_small(frame)
    fa, fb = np.fft.fft2(a - a.mean()), np.fft.fft2(b - b.mean())
    cross = fa * np.conj(fb)
    cross /= np.abs(cross) + 1e-9
    corr = np.fft.ifft2(cross).real
    y, x = np.unravel_index(int(corr.argmax()), corr.shape)
    y = y - SMALL if y > SMALL // 2 else y
    x = x - SMALL if x > SMALL // 2 else x
    sy, sx = base.size[1] / SMALL, base.size[0] / SMALL
    dy, dx = round(int(y) * sy), round(int(x) * sx)
    if abs(dy) > base.size[1] * MAX_SHIFT or abs(dx) > base.size[0] * MAX_SHIFT:
        return 0, 0
    return dy, dx


def _smooth(img):
    """Small and slightly blurred: the grain an edit redraws differently every time (skin, satin) is smoothed away, the
    shapes that move with an expression (eyelids, lips, brows) are not."""
    return np.asarray(img.convert("RGB").resize((SMALL, SMALL), Image.BILINEAR).filter(ImageFilter.GaussianBlur(2)),
                      dtype=np.float32)


def align(base, frame):
    """The frame at the portrait's size and lined up with it. Returns (frame, [dy, dx])."""
    base, frame = base.convert("RGBA"), frame.convert("RGBA")
    if frame.size != base.size:
        frame = frame.resize(base.size, Image.LANCZOS)
    dy, dx = estimate_shift(base, frame)
    if dy or dx:
        moved = base.copy()                              # where the frame is moved away from an edge, the portrait shows
        moved.paste(frame, (dx, dy))
        frame = moved
    return frame, [dy, dx]


def face_centre(base, frames):
    """(y, x) of her face in SMALL pixels: where the expressions changed the picture, weighted towards where a portrait
    puts a face (upper middle). One frame's change can be anywhere -- an edit also redraws a satin dress or a feather --
    but every expression changes the face."""
    b = _smooth(base)
    total = sum(np.abs(b - _smooth(f)).mean(axis=2) for f in frames)
    yy, xx = np.mgrid[0:SMALL, 0:SMALL] / SMALL
    weight = total * np.exp(-(((yy - 0.36) / 0.22) ** 2 + ((xx - 0.5) / 0.2) ** 2) / 2)
    top = weight >= np.percentile(weight, 97)
    if not top.any() or weight[top].sum() <= 0:
        return SMALL * 0.36, SMALL * 0.5
    ys, xs = np.nonzero(top)
    return float(np.average(ys, weights=weight[top])), float(np.average(xs, weights=weight[top]))


def change_mask(base, frame, centre, radius=(0.2, 0.16), solid=0.35):
    """A soft mask (L, full size) of what changed in her face: inside an ellipse around the face, the parts that differ
    most from the portrait, closed (so a nose or teeth leave no hole) and feathered."""
    d = np.abs(_smooth(base) - _smooth(frame)).mean(axis=2)
    yy, xx = np.mgrid[0:SMALL, 0:SMALL]
    dist = np.hypot((yy - centre[0]) / (SMALL * radius[0]), (xx - centre[1]) / (SMALL * radius[1]))
    face = np.clip((1.25 - dist) / 0.35, 0, 1)           # 1 inside the face, fading to 0 just beyond it
    inside = d[dist < 1]
    lo, hi = np.percentile(inside, 55), np.percentile(inside, 92)
    if hi - lo < 2 and inside.max() < 8:                 # nothing changed that stands out: the frame is the portrait
        return Image.new("L", base.size, 0)
    m = np.clip((d - lo) / max(hi - lo, 1e-3), 0, 1) * face
    # Solid where it changed, soft only at the edge. A mask that is partly on over a changed feature shows both versions of
    # it: measured, a wide-smile portrait under closed lips kept a faint row of teeth. So the change is taken whole (closed
    # so a nose or teeth leave no hole, grown a little into the skin around it) and only that outer edge is feathered.
    core = Image.fromarray(((m > solid) * 255).astype(np.uint8))
    core = core.filter(ImageFilter.MaxFilter(11)).filter(ImageFilter.MinFilter(7)).filter(ImageFilter.MaxFilter(5))
    mask = core.filter(ImageFilter.GaussianBlur(3)).resize(base.size, Image.BILINEAR)
    return mask.filter(ImageFilter.GaussianBlur(max(2, round(8 * base.size[0] / 1024))))


def _lum(img):
    return np.asarray(img.convert("L").resize((SMALL, SMALL), Image.BILINEAR).filter(ImageFilter.GaussianBlur(1.5)),
                      dtype=np.float32)


def _strongest(change, allowed):
    """(y, x, value) of the strongest change inside `allowed`, after a little blur."""
    work = np.asarray(Image.fromarray(np.clip(change, 0, 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(2)),
                      dtype=np.float32) * allowed
    y, x = np.unravel_index(int(work.argmax()), work.shape)
    return int(y), int(x), float(work[y, x]), work


def find_eyes(base, blink, centre):
    """Her eyes, [(y, x), ...] in SMALL pixels, from the blink frame: closing an eye turns a dark iris into lighter skin,
    so the picture gets brighter in two spots at about the same height. The other changes an edit makes (a redrawn smile,
    re-painted skin) do not do that. A second eye is only taken when it brightened nearly as much (a turned head may hide it)."""
    yy, xx = np.mgrid[0:SMALL, 0:SMALL]
    near = np.hypot((yy - centre[0]) / (SMALL * 0.28), (xx - centre[1]) / (SMALL * 0.22)) < 1
    y1, x1, v1, work = _strongest(_lum(blink) - _lum(base), near)
    if v1 <= 0:
        return []
    eyes = [(y1, x1)]
    work = work.copy()
    work[np.hypot(yy - y1, xx - x1) < SMALL * 0.05] = 0
    work[np.abs(yy - y1) > SMALL * 0.05] = 0
    y2, x2 = np.unravel_index(int(work.argmax()), work.shape)
    if work[y2, x2] >= 0.35 * v1:
        eyes.append((int(y2), int(x2)))
    return sorted(eyes, key=lambda e: e[1])


def find_mouth(base, talking, centre, eyes):
    """Her mouth, (y, x) in SMALL pixels, from the talking frame: an opening mouth is a dark cavity where there was none.
    It is looked for below and between her eyes (measured: without that, a darkened strand of hair by a turned head's far
    eye won), or below the face's centre when the eyes are not known."""
    yy, xx = np.mgrid[0:SMALL, 0:SMALL]
    if len(eyes) == 2:
        (ya, xa), (yb, xb) = eyes
        gap = max(np.hypot(yb - ya, xb - xa), SMALL * 0.06)
        my, mx = (ya + yb) / 2, (xa + xb) / 2
        allowed = (yy > my + 0.6 * gap) & (yy < my + 2.2 * gap) & (np.abs(xx - mx) < 1.1 * gap)
    else:
        top = eyes[0][0] + SMALL * 0.04 if eyes else centre[0] - SMALL * 0.02
        allowed = (yy > top) & (yy < top + SMALL * 0.22) & (np.abs(xx - centre[1]) < SMALL * 0.1)
    y, x, v, _ = _strongest(_lum(base) - _lum(talking), allowed)
    return (y, x) if v > 0 else None


def feature_mask(size, spots, radius):
    """A soft mask (L, full size) of small ellipses around `spots` (SMALL pixels), solid inside and feathered at the edge."""
    yy, xx = np.mgrid[0:SMALL, 0:SMALL]
    region = np.zeros((SMALL, SMALL), dtype=np.float32)
    for y, x in spots:
        e = np.hypot((yy - y) / (SMALL * radius[0]), (xx - x) / (SMALL * radius[1]))
        region = np.maximum(region, np.clip((1.3 - e) / 0.3, 0, 1))
    mask = Image.fromarray((region * 255).astype(np.uint8)).resize(size, Image.BILINEAR)
    return mask.filter(ImageFilter.GaussianBlur(max(2, round(6 * size[0] / 1024))))


EYE_RADIUS, MOUTH_RADIUS = (0.04, 0.06), (0.055, 0.085)


def match_colour(base, frame, mask):
    """The frame with its colours shifted so the skin around the change matches the portrait's. A painterly edit can come back
    warmer or cooler all over (measured: a whole face a shade more orange), which a blink or a word would flash. The shift is
    measured on the mask's soft rim, where the skin is blended but the expression did not really change it."""
    b = np.asarray(base.convert("RGB"), dtype=np.float32)
    f = np.asarray(frame.convert("RGB"), dtype=np.float32)
    m = np.asarray(mask, dtype=np.float32) / 255
    w = 4 * m * (1 - m)
    if w.sum() < 1:
        return frame
    shift = (b * w[..., None]).sum(axis=(0, 1)) / w.sum() - (f * w[..., None]).sum(axis=(0, 1)) / w.sum()
    out = Image.fromarray(np.clip(f + shift, 0, 255).astype(np.uint8)).convert("RGBA")
    out.putalpha(frame.convert("RGBA").getchannel("A"))
    return out


def composite(base, frame, centre=None):
    """The portrait with only the changed part of her face taken from `frame`. `centre` is her face (face_centre); without
    it, it is worked out from this frame alone. Returns (image, info)."""
    base = base.convert("RGBA")
    frame, shift = align(base, frame)
    centre = centre if centre is not None else face_centre(base, [frame])
    mask = change_mask(base, frame, centre)
    out = Image.composite(match_colour(base, frame, mask), base, mask)
    coverage = float(np.asarray(mask, dtype=np.float32).mean() / 255)
    return out, {"shift": shift, "coverage": round(coverage, 4)}


def assemble(lib, npc_id, data):
    """Build living/ from the portrait and the living expressions that exist and match the portrait. Returns the manifest.
    Raises ValueError when there is no portrait or not a single usable expression."""
    npc = lib.get(npc_id)
    assets = npc.get("assets", {})
    portrait = assets.get("portrait")
    if not portrait:
        raise ValueError("There is no portrait yet")
    portrait_png = lib.read_asset(npc_id, portrait["file"])
    base = Image.open(io.BytesIO(portrait_png))
    exprs = assets.get("expressions", {})
    aligned, skipped = {}, []
    for eid in living_ids(data):
        meta = exprs.get(eid)
        if not meta or meta.get("stale"):
            skipped.append(eid)
            continue
        aligned[eid] = align(base, Image.open(io.BytesIO(lib.read_asset(npc_id, meta["file"]))))
    base = base.convert("RGBA")
    centre = face_centre(base, [f for f, _ in aligned.values()]) if aligned else None
    # A blink or a word shows for a tenth of a second at a time: those take only her eyes or her mouth, found from the
    # frames themselves, so nothing else the edit re-painted can flicker.
    eyes = find_eyes(base, aligned["blink"][0], centre) if "blink" in aligned else []
    mouth = find_mouth(base, aligned["talking"][0], centre, eyes) if "talking" in aligned else None
    frames = {}
    for eid, (frame, shift) in aligned.items():
        meta = exprs[eid]
        if eid == "blink" and eyes:
            mask = feature_mask(base.size, eyes, EYE_RADIUS)
        elif eid == "talking" and mouth:
            mask = feature_mask(base.size, [mouth], MOUTH_RADIUS)
        else:
            mask = change_mask(base, frame, centre, solid=SOLID.get(eid, 0.35))
        out = Image.composite(match_colour(base, frame, mask), base, mask)
        info = {"shift": shift, "coverage": round(float(np.asarray(mask, dtype=np.float32).mean() / 255), 4)}
        rel = f"living/{eid}.png"
        models = [licences.picture_engine(portrait), licences.picture_engine(meta, edit=True)]
        lib.write_asset(npc_id, rel, media.png(out, models=models, optimize=True))
        frames[eid] = {"file": rel, "source_created": meta.get("created"), **info}
    if not frames:
        raise ValueError("None of the living-portrait expressions exist for the current portrait yet")
    manifest = {
        "version": VERSION,
        "portrait": {"created": portrait.get("created"), "sha256": hashlib.sha256(portrait_png).hexdigest()},
        "size": list(base.size), "frames": frames, "missing": skipped, "made": now(),
        "face": [round(centre[0] / SMALL, 3), round(centre[1] / SMALL, 3)] if centre else None,
        "eyes": [[round(y / SMALL, 3), round(x / SMALL, 3)] for y, x in eyes],
        "mouth": [round(mouth[0] / SMALL, 3), round(mouth[1] / SMALL, 3)] if mouth else None,
    }
    lib.write_asset(npc_id, "living/living.json", json.dumps(manifest, indent=2).encode("utf-8"))

    def apply(n):
        n.setdefault("assets", {})["living"] = {"file": "living/living.json", "frames": sorted(frames), "missing": skipped,
                                                "portrait_created": portrait.get("created"), "created": manifest["made"],
                                                "face": manifest["face"]}
    lib.update(npc_id, apply)
    return manifest
