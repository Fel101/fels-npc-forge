"""Animation sets: consistent multi-frame clips made from one character with Mage-Flow-Edit.

How it works
------------
1. The character's full-body sprite is the *front* reference. Canonical side and back views are generated from it once.
2. Each frame is its own edit, with a pose phrase ("left leg forward, right leg back...") and the sprite plus the canonical
   view as references. Using both keeps the look stable while still letting the pose change (measured, see
   data/animations.json). Optionally several candidates are drawn per frame and the best one is kept.
3. Frames are cut out, scaled to a common figure height and anchored at the feet and torso centre on a square canvas.
4. Each clip gets a report: did the legs really move, did the head keep its shape, did the palette hold.
"""
import io
import itertools
import math
import statistics
import types

import numpy as np
from PIL import Image, ImageOps

from . import engines, pose, prompts
from .comfy import ComfyError
from .keying import grey_image, remove_background
from .library import now

CANVAS = 512        # working size of aligned frames (square). Exports resample from this.
FIGURE_H = 440      # standing height of the figure on that canvas
BASELINE = 480      # y of the feet

STATIC_LEG_VARIATION = 0.12   # below this the legs barely moved between frames (real walks measured 0.28-0.33, static 0.00-0.06)
HEAD_DRIFT_LIMIT = 0.85       # below this the head/hair changed shape between frames (stable looks measured 0.98, drifting 0.89)
STATIC_LEG_VARIATION_FRONT_BACK = 0.05   # a guided walk seen from the front or back: the legs differ less there (static copies of the sprite
                                          # measured 0.03, good steps 0.06-0.11)
STRIDE_CONTRAST_LIMIT = 1.35  # side views: the widest step over the narrowest. The original text-driven walks (one stride four times)
                              # measured 1.08-1.22; pose-guided walks that read as walking 1.49-2.56
RUN_PHASE_SIMILARITY_LIMIT = 0.65  # both opposite run phases in two failed GPU trials exceeded this leg-silhouette overlap

# Pose-guided frames (see pose.py). The puppet stands 84% of the picture high with its feet at 93%. Measured on drawn frames: side
# and back views come out 78-86% high; front views keep the sprite's own framing, 82-95% high. A zoomed-in draw is 93% or more AND
# cut off at the top or the feet, so "fits" asks for the whole figure inside the picture rather than for a size close to the puppet's.
GUIDE_HEIGHT, GUIDE_FLOOR = 0.84, 0.93
GUIDE_FIT = (0.70, 0.965)     # the figure's share of the picture height that counts as "fits"
GUIDE_TRIES = 3               # how many draws a later frame gets before the best one is kept
GUIDE_SEEDS = 4               # how many seeds are tried on the probe frames before the best one is taken
FACING_LIMITS = (1.3, 0.75)   # torso width of a draw over the character's own standing view: a side view may be at most 1.3x (a figure that
                              # turned to face the camera measured 1.47x, good draws 0.85-1.07x); a front or back view at least 0.75x
MIN_MOVED = 0.12              # front and back views: how far the legs must differ from the standing view (static copies measured 0.02-0.05,
                              # good steps 0.27-0.40)
STRIDE_BAND = (0.75, 0.16)    # side views: a draw's feet must be at least 0.75x as far apart as the puppet's, and no more than 0.16 of the
                              # figure's height further apart (measured: good draws were 0.8-1.2x; the failure, feet together on a stride, 0.5x)
WIDE_STRIDE = 0.30            # the lower limit only applies to a puppet with its feet at least this far apart: legs closer together than a
                              # passing puppet's are fine (a passing step wants the feet together)


# ---- cut-outs and measurements (pure functions, no ComfyUI) -------------------------------------------------------
def cutout(png, hard_edges):
    """Key the backdrop out and trim. Returns an RGBA image, or None when the backdrop could not be removed."""
    data, info = remove_background(png, hard_edges=hard_edges, trim=True)
    return Image.open(io.BytesIO(data)).convert("RGBA") if info["keyed"] else None


def cutout_full(png, hard_edges):
    """Like cutout, but the figure stays where the frame put it (nothing is trimmed). None when the backdrop is not flat."""
    data, info = remove_background(png, hard_edges=hard_edges, trim=False)
    return Image.open(io.BytesIO(data)).convert("RGBA") if info["keyed"] else None


def figure_box(im, threshold=8):
    a = np.asarray(im.convert("RGBA"))[..., 3] > threshold
    ys, xs = np.where(a)
    if not len(xs):
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def _band_mask(im, lo, hi, n, own_extent):
    """Silhouette of a horizontal band of the figure (fractions of its height), resized to n x n."""
    a = np.asarray(im.convert("RGBA"))[..., 3] > 128
    ys, xs = np.where(a)
    if not len(ys):
        return np.zeros((n, n), bool)
    top = int(ys.min())
    height = int(ys.max()) + 1 - top
    r0 = top + int(height * lo)
    band = a[r0:max(top + int(height * hi), r0 + 1)]
    cols = np.where(band.any(axis=0))[0] if own_extent else np.arange(xs.min(), xs.max() + 1)
    if not len(cols):
        return np.zeros((n, n), bool)
    band = band[:, cols.min(): cols.max() + 1]
    small = Image.fromarray((band * 255).astype(np.uint8)).resize((n, n), Image.BILINEAR)
    return np.asarray(small) > 128


def head_mask(im):
    return _band_mask(im, 0.0, 0.22, 48, own_extent=True)       # own extent: arm swing must not change the head crop


def leg_mask(im):
    return _band_mask(im, 0.55, 1.0, 64, own_extent=False)      # whole-figure extent: the stride width matters here


def iou(a, b):
    return float((a & b).sum() / max(1, (a | b).sum()))


def _mean_pairwise(masks):
    pairs = list(itertools.combinations(masks, 2))
    return float(np.mean([iou(a, b) for a, b in pairs])) if pairs else 1.0


def leg_variation(frames):
    """0 = the legs are in the same place in every frame (a static 'walk'); a real walk cycle is around 0.3."""
    return 1.0 - _mean_pairwise([leg_mask(f) for f in frames])


def head_stability(frames):
    """1 = identical head/hair silhouette in every frame."""
    return _mean_pairwise([head_mask(f) for f in frames])


def palette(im, n=24):
    a = np.asarray(im.convert("RGBA"))
    px = a[a[..., 3] > 200][:, :3]
    if not len(px):
        return np.zeros((1, 3), np.int16)
    vals, counts = np.unique(((px // 16) * 16 + 8).reshape(-1, 3), axis=0, return_counts=True)
    return vals[np.argsort(-counts)][:n].astype(np.int16)


def palette_coverage(im, reference_palette):
    """Share of the frame's opaque pixels whose colour is within 40 (RGB) of the reference palette."""
    a = np.asarray(im.convert("RGBA"))
    px = a[a[..., 3] > 200][:, :3].astype(np.int16)
    if not len(px):
        return 0.0
    d = np.min(np.linalg.norm(px[:, None, :] - reference_palette[None, :, :], axis=2), axis=1)
    return float((d < 40).mean())


def candidate_score(candidate, canonical):
    """Higher is better: the head should match the canonical view, while the legs should really have moved."""
    head = iou(head_mask(candidate), head_mask(canonical))
    moved = 1.0 - iou(leg_mask(candidate), leg_mask(canonical))
    return head + 0.6 * min(moved, 0.35), {"head": round(head, 3), "legs": round(moved, 3)}


def _alpha_mask(im):
    return np.asarray(im.convert("RGBA"))[..., 3] > 128


def _puppet_mask(puppet):
    return np.asarray(puppet.convert("RGB")).min(axis=2) < 235                # everything that is not the white page


def foot_spread(mask, band=0.08):
    """How wide apart the feet are (the width of the bottom `band` of the figure), as a share of the figure's height."""
    ys, xs = np.where(mask)
    if not len(ys):
        return 0.0
    top, bottom = int(ys.min()), int(ys.max()) + 1
    cols = np.where(mask[int(bottom - (bottom - top) * band): bottom].any(axis=0))[0]
    return float((cols.max() - cols.min() + 1) / (bottom - top)) if len(cols) else 0.0


def stride_error(spread, target):
    """0 when the feet are about as far apart as the puppet's; otherwise how far outside the accepted band (a share of the figure's height)."""
    lower = STRIDE_BAND[0] * target if target >= WIDE_STRIDE else 0.0
    return max(0.0, lower - spread, spread - (target + STRIDE_BAND[1]))


def torso_width(mask, lo=0.25, hi=0.45):
    """Width of the chest band as a share of the figure's height. A figure facing the camera is far wider here than one seen from the side."""
    ys, xs = np.where(mask)
    if not len(ys):
        return 0.0
    top, bottom = int(ys.min()), int(ys.max()) + 1
    height = bottom - top
    cols = np.where(mask[top + int(height * lo): top + int(height * hi)].any(axis=0))[0]
    return float((cols.max() - cols.min() + 1) / height) if len(cols) else 0.0


def guided_score(full, puppet, view, canonical):
    """Score a frame drawn from a puppet. `full` is its cut-out on the full canvas, `canonical` the character's standing view from the
    same direction. Returns (score, detail); detail['ok'] says whether the frame can be used as it is:

    - the whole figure is inside the picture,
    - it faces the way the puppet does (a side view drawn facing the camera passed every other check once),
    - in a side view the feet are where the puppet's are, and in a front or back view the legs are not just the standing pose again.
    """
    box = figure_box(full)
    if box is None:
        return -1.0, {"fits": False, "facing": False, "head": 0.0, "stride_error": 1.0, "legs": 0.0, "ok": False}
    w, h = full.size
    share = (box[3] - box[1]) / h
    fits = (GUIDE_FIT[0] <= share <= GUIDE_FIT[1] and box[0] >= 0.015 * w and box[2] <= 0.985 * w
            and box[1] >= 0.01 * h and box[3] <= 0.995 * h)      # nothing cut off
    side = view == "side"
    error = stride_error(foot_spread(_alpha_mask(full)), foot_spread(_puppet_mask(puppet))) if side else 0.0
    ratio = torso_width(_alpha_mask(full)) / max(torso_width(_alpha_mask(canonical)), 0.05)
    facing = ratio <= FACING_LIMITS[0] if side else ratio >= FACING_LIMITS[1]
    legs = 1.0 - iou(leg_mask(full), leg_mask(canonical))
    head = iou(head_mask(full), head_mask(canonical))
    ok = bool(fits and facing and error == 0.0 and (side or legs >= MIN_MOVED))
    score = (1.0 if fits else 0.0) + (1.0 if facing else 0.0) + head + (1.0 - min(1.0, error / 0.25)) + (0.0 if side else min(legs, 0.4))
    return score, {"fits": bool(fits), "facing": bool(facing), "head": round(head, 3), "stride_error": round(error, 3),
                   "legs": round(legs, 3), "ok": ok}


def stride_contrast(frames):
    """Side views: the widest step over the narrowest, across a cycle's frames. A walk alternates a long stride (a foot
    landing) with feet close together (passing), so about 3 is healthy; near 1 is a figure sliding along in one stance."""
    spreads = [foot_spread(_alpha_mask(f)) for f in frames]
    return max(spreads) / max(min(spreads), 0.02)


def run_phase_similarity(frames):
    """Four-frame run: overlap of opposing contact and flight leg silhouettes."""
    if len(frames) != 4:
        return None
    masks = [leg_mask(f) for f in frames]
    return (iou(masks[0], masks[2]), iou(masks[1], masks[3]))


# ---- colour match ---------------------------------------------------------------------------------------------------------
_SRGB_TO_XYZ = np.array([[0.4124564, 0.3575761, 0.1804375], [0.2126729, 0.7151522, 0.0721750], [0.0193339, 0.1191920, 0.9503041]])
_XYZ_TO_SRGB = np.linalg.inv(_SRGB_TO_XYZ)
_WHITE = np.array([0.95047, 1.0, 1.08883])


def _apply(matrix, rows):
    """rows (N, 3) times a 3x3 matrix, written out by hand. NOT `rows @ matrix.T`: on the machine this was developed on, numpy's BLAS
    gave a different answer for the same 130,000-row product in about 1 run in 50, which showed up as bands of noise across a
    frame (found by re-assembling the same frames twice and getting different pictures)."""
    return rows[:, 0:1] * matrix[:, 0] + rows[:, 1:2] * matrix[:, 1] + rows[:, 2:3] * matrix[:, 2]


def _rgb_to_lab(rgb):
    """(N, 3) sRGB 0-255 -> CIE Lab."""
    c = rgb / 255.0
    c = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    xyz = _apply(_SRGB_TO_XYZ, c) / _WHITE
    f = np.where(xyz > 216 / 24389, np.cbrt(xyz), (24389 / 27 * xyz + 16) / 116)
    return np.stack([116 * f[:, 1] - 16, 500 * (f[:, 0] - f[:, 1]), 200 * (f[:, 1] - f[:, 2])], axis=1)


def _lab_to_rgb(lab):
    fy = (lab[:, 0] + 16) / 116
    f = np.stack([fy + lab[:, 1] / 500, fy, fy - lab[:, 2] / 200], axis=1)
    xyz = np.where(f ** 3 > 216 / 24389, f ** 3, (116 * f - 16) / (24389 / 27)) * _WHITE
    c = np.clip(_apply(_XYZ_TO_SRGB, xyz), 0.0, 1.0)
    c = np.where(c <= 0.0031308, 12.92 * c, 1.055 * c ** (1 / 2.4) - 0.055)
    return np.clip(c * 255.0, 0.0, 255.0)


def match_colours(frame, reference, spread=(0.75, 1.33)):
    """Move a frame's overall colours (lightness and colour balance) to those of a reference figure, both RGBA cut-outs.

    The seed decides a generated character's look, and a frame that had to be redrawn with another seed can come back in a paler
    shirt or darker trousers, which flickers when the clip plays. This pulls every frame toward the character's own standing view.
    The contrast (spread) of each Lab channel is matched too, within `spread`, so a frame is never stretched far."""
    fa = np.asarray(frame.convert("RGBA")).astype(np.float32)
    ra = np.asarray(reference.convert("RGBA")).astype(np.float32)
    fm, rm = fa[..., 3] > 200, ra[..., 3] > 200
    if fm.sum() < 200 or rm.sum() < 200:
        return frame
    f_lab, r_lab = _rgb_to_lab(fa[fm][:, :3]), _rgb_to_lab(ra[rm][:, :3])
    ratio = np.clip((r_lab.std(axis=0) + 1e-3) / (f_lab.std(axis=0) + 1e-3), *spread)
    everything = fa[..., 3] > 0
    lab = (_rgb_to_lab(fa[everything][:, :3]) - f_lab.mean(axis=0)) * ratio + r_lab.mean(axis=0)
    out = fa.copy()
    out[everything, :3] = _lab_to_rgb(lab)
    return Image.fromarray((out + 0.5).astype(np.uint8), "RGBA")


# ---- alignment ------------------------------------------------------------------------------------------------------------
def _torso_x(im):
    a = np.asarray(im.convert("RGBA"))[..., 3] > 128
    ys, xs = np.where(a)
    if not len(ys):
        return im.width / 2
    top, height = int(ys.min()), int(ys.max()) + 1 - int(ys.min())
    band = a[top + int(height * 0.22): top + int(height * 0.55)]
    cols = np.where(band.any(axis=0))[0]
    return float(np.mean(np.where(band)[1])) if len(cols) else (xs.min() + xs.max()) / 2


def _paste(canvas, im, x, y):
    """Alpha-composite `im` onto `canvas` at (x, y), clipping whatever falls outside."""
    x0, y0 = max(x, 0), max(y, 0)
    x1, y1 = min(x + im.width, canvas.width), min(y + im.height, canvas.height)
    if x1 <= x0 or y1 <= y0:
        return
    piece = im.crop((x0 - x, y0 - y, x1 - x, y1 - y))
    canvas.alpha_composite(piece, (x0, y0))


def align(frames_by_dir, canvas=CANVAS, figure_h=FIGURE_H, baseline=BASELINE):
    """Scale every frame to a common figure height and anchor it (feet on the baseline, torso on the centre line)."""
    boxes = [figure_box(f) for fr in frames_by_dir.values() for f in fr]
    heights = [b[3] - b[1] for b in boxes if b]
    if not heights:
        return {d: [Image.new("RGBA", (canvas, canvas)) for _ in fr] for d, fr in frames_by_dir.items()}
    target = statistics.median(heights)
    out = {}
    for direction, frames in frames_by_dir.items():
        out[direction] = []
        for f in frames:
            img = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
            box = figure_box(f)
            if box:
                # generated frames can differ a lot in size (the back view of one test character was up to 25% smaller than
                # the front view), so allow a wide correction; the clamp only guards against a broken cut-out
                scale = (figure_h / target) * min(1.6, max(0.6, target / (box[3] - box[1])))
                g = f.resize((max(1, round(f.width * scale)), max(1, round(f.height * scale))), Image.LANCZOS)
                gbox = figure_box(g)
                _paste(img, g, round(canvas / 2 - _torso_x(g)), round(baseline - gbox[3]))
            out[direction].append(img)
    return out


def _smoothstep(x):
    x = np.clip(x, 0.0, 1.0)
    return x * x * (3.0 - 2.0 * x)


def _premultiply(im):
    a = np.asarray(im.convert("RGBA"), np.float32) / 255.0
    a[..., :3] *= a[..., 3:]                       # premultiplied, so resampling an edge never leaves a dark or bright fringe
    return a


def _unpremultiply(a):
    alpha = np.clip(a[..., 3:], 0.0, 1.0)
    rgb = np.where(alpha > 1e-4, a[..., :3] / np.maximum(alpha, 1e-4), 0.0)
    return Image.fromarray((np.clip(np.concatenate([rgb, alpha], axis=2), 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8), "RGBA")


def _bilinear(a, xs, ys):
    """Sample the H x W x 4 array `a` at float coordinates (edges repeat)."""
    h, w = a.shape[:2]
    xs, ys = np.clip(xs, 0, w - 1.001), np.clip(ys, 0, h - 1.001)
    x0, y0 = xs.astype(np.int32), ys.astype(np.int32)
    fx, fy = (xs - x0)[..., None], (ys - y0)[..., None]
    return (a[y0, x0] * (1 - fx) * (1 - fy) + a[y0, x0 + 1] * fx * (1 - fy)
            + a[y0 + 1, x0] * (1 - fx) * fy + a[y0 + 1, x0 + 1] * fx * fy)


def raise_upper_body(im, lift, chest=0.0):
    """Lift the head and shoulders by `lift` (a fraction of the figure's height, negative lowers them) and widen the
    ribcage by `chest` (a fraction of its width). The hips and legs never move, so the feet stay planted: a breath and the
    up-and-down of a step are both this warp."""
    box = figure_box(im)
    if box is None or (not lift and not chest):
        return im.copy()
    top, height = box[1], box[3] - box[1]
    cx = _torso_x(im)
    ys, xs = np.mgrid[0:im.height, 0:im.width].astype(np.float32)
    v = (ys - top) / height                                          # 0 at the top of the head, 1 at the soles
    rise = _smoothstep((0.56 - v) / 0.26)                            # 1 from the mid-chest up, easing to 0 at the hips
    swell = np.exp(-(((v - 0.27) / 0.10) ** 2))                      # strongest at the ribcage
    return _unpremultiply(_bilinear(_premultiply(im), cx + (xs - cx) / (1 + chest * swell), ys + lift * height * rise))


def procedural_idle(still, frames, lift, chest=0.0):
    """A breathing loop from one aligned still. Nothing is generated.

    Only the upper body moves, the way a chest does. The still must already sit on the canvas (see align): frames of a
    breath differ in height by design, so they must not go through align() again, which would rescale them all to one
    height and cancel the breath.
    """
    if figure_box(still) is None:
        return [still.copy() for _ in range(frames)]
    out = []
    for i in range(frames):
        breath = (1 - math.cos(2 * math.pi * i / frames)) / 2        # 0 at rest, 1 at the top of the breath, smooth both ways
        out.append(raise_upper_body(still, lift * breath, chest * breath))
    return out


def add_step_bob(frames, bob):
    """The up-and-down of a walk. align() gives every frame the same height, which flattens it; this puts it back: the body
    dips as a foot lands and rises as the legs pass. `bob` is one value per frame (fractions of the figure's height)."""
    return [raise_upper_body(f, b) for f, b in zip(frames, bob)]


def clip_fps(clip, count):
    """Playback rate. A clip that defines its cycle length keeps that length whatever the frame count, so a 4-frame walk
    is not played twice as fast as an 8-frame one."""
    return round(count / clip["cycle"], 1) if clip.get("cycle") else clip["fps"]


# ---- prompts ----------------------------------------------------------------------------------------------------------------
def _tail(style, key, freeform=False):
    return (f"on {prompts.KEY_PHRASES[key]} with no floor and no shadow. {prompts.keep_phrase(freeform)}, "
            f"and keep the exact same {style['edit_name']} art style, line style and color palette.")


def view_prompt(anim, direction, style, key, freeform=False):
    return {"positive": f"{anim['canonical_prompts'][direction]}, {_tail(style, key, freeform)}", "negative": prompts._edit_negative(style)}


def frame_prompt(anim, clip_id, direction, index, count, style, key, freeform=False):
    clip, d = anim["clips"][clip_id], anim["directions"][direction]
    pose = clip["poses"][d["group"]][str(count)][index]
    return {"positive": f"Show the same character {clip['verb']} {d['view']}, {pose}, full body head to toe, {_tail(style, key, freeform)}",
            "negative": prompts._edit_negative(style)}


def guided_frame_prompt(anim, clip_id, direction, who, style, key, freeform=False, kind=None):
    """Prompt for a frame drawn from a puppet: the puppet is the first reference, the character's sprite the second. `kind` is the
    kind of pose the frame is ('contact', 'passing', 'step'...), which can add wording of its own."""
    clip, d = anim["clips"][clip_id], anim["directions"][direction]
    keep = f"{prompts.keep_phrase(freeform)}, and keep the exact same {style['edit_name']} art style, line style and color palette"
    notes = anim.get("guided_notes", {}).get(direction, {})
    by_pose = notes.get("by_pose", {}).get(kind, "") if kind else ""
    text = anim["guided_prompt"].format(who=who, verb=clip["verb"], view=d["view"], backdrop=prompts.KEY_PHRASES[key], keep=keep,
                                        caught=notes.get("caught", ""), note=notes.get("note", "") + by_pose)
    return {"positive": text, "negative": prompts._edit_negative(style)}


def frame_counts(clip):
    """The frame counts a clip can be made with."""
    if clip.get("frames"):
        return sorted(clip["frames"])
    return sorted({int(n) for group in clip.get("poses", {}).values() for n in group})


def generated_directions(dirs, mirror_left):
    """Directions that need real generation: 'left' is a mirror of 'right' unless asked otherwise."""
    wanted = [d for d in dirs if not (d == "left" and mirror_left)]
    if "left" in dirs and mirror_left and "right" not in wanted:
        wanted.append("right")
    return wanted


# ---- generation (talks to ComfyUI) -------------------------------------------------------------------------------------------
class Animator:
    """Runs the steps of an animation job. `job` needs .npc_id, .options, .cancel (an Event) and .cache (a dict)."""

    def __init__(self, comfy, lib, cfg):
        self.comfy, self.lib, self.cfg = comfy, lib, cfg

    def settings(self, quality_id):
        q = self.cfg["quality"].get(quality_id) or self.cfg["quality"]["standard"]
        return tuple(q["anim"]), q["anim_steps"]

    def _edit(self, job, refs, pr, seed, steps, size):
        """One edit with the job's edit engine. When that engine could not draw it (Mage-Flow did), the reason is left in
        job.cache["engine_note"] for the step."""
        png, used = engines.edit(self.comfy, job.options.get("edit_model"), refs, pr["positive"], seed, steps, self.cfg["edit_cfg"],
                                 size=size, negative=pr["negative"], cancel=job.cancel)
        # which engines drew the frames, kept with them (the licence check: an engine that fell back to Mage-Flow counts as Mage-Flow)
        job.cache.setdefault("edit_engines", set()).add(used.get("engine") or engines.EDIT_ENGINES["mage"]["label"])
        if used.get("engine_note"):
            job.cache["engine_note"] = used["engine_note"]
        return png

    def _upload(self, job, name, png):
        return self.comfy.upload_image(png, f"{job.npc_id}_{name}.png")

    def front_reference(self, job, size):
        """The sprite's raw canvas (flat backdrop) at animation size, uploaded once per job."""
        cache = job.cache.setdefault("refs", {})
        if "front" in cache and cache["front"]["size"] == tuple(size):
            return cache["front"]
        meta = self.lib.get(job.npc_id)["assets"].get("sprite")
        if not meta:
            raise ComfyError("Animation starts from the full-body sprite: generate the sprite first")
        im = Image.open(io.BytesIO(self.lib.read_asset(job.npc_id, meta.get("raw") or meta["file"]))).convert("RGB")
        if im.size != tuple(size):
            im = im.resize(tuple(size), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "PNG")
        cache["front"] = {"name": self._upload(job, "anim_front", buf.getvalue()), "png": buf.getvalue(), "size": tuple(size),
                          "key": meta.get("key")}
        return cache["front"]

    def _view_reference(self, job, direction):
        cache = job.cache.setdefault("refs", {})
        if direction not in cache:
            png = self.lib.read_asset(job.npc_id, f"animations/views/{direction}.png")
            cache[direction] = {"name": self._upload(job, f"anim_view_{direction}", png), "png": png}
        return cache[direction]

    def _canonical_cut(self, job, direction, hard_edges):
        """The cut-out reference figure for a direction (measured against, never sent to ComfyUI). Read locally."""
        cache = job.cache.setdefault("canon", {})
        if direction not in cache:
            if direction == "down":
                meta = self.lib.get(job.npc_id)["assets"].get("sprite")
                if not meta:
                    raise ComfyError("Animation starts from the full-body sprite: generate the sprite first")
                png = self.lib.read_asset(job.npc_id, meta.get("raw") or meta["file"])
            else:
                png = self.lib.read_asset(job.npc_id, f"animations/views/{direction}.png")
            cache[direction] = cutout(png, hard_edges)
        return cache[direction]

    def view_exists(self, npc, direction):
        return direction in npc.get("assets", {}).get("animation_views", {}) and \
            self.lib.resolve(npc["id"], f"animations/views/{direction}.png").exists()

    def make_view(self, job, npc, data, direction, seed):
        """Generate the canonical side/back view (unless it exists and we are not re-rolling)."""
        size, steps = self.settings(job.options["quality"])
        front = self.front_reference(job, size)
        if self.view_exists(npc, direction) and not (job.options.get("reroll") or job.cache.get("sprite_regenerated")):
            self._view_reference(job, direction)
            return
        style = data["styles"][npc["style"]]
        key = front["key"] or prompts.pick_key(npc, style)
        pr = view_prompt(data["animations"], direction, style, key, npc.get("freeform", False))
        png = self._edit(job, [front["name"]], pr, seed, steps, size)
        self.lib.write_asset(job.npc_id, f"animations/views/{direction}.png", png)
        job.cache.setdefault("refs", {}).pop(direction, None)
        job.cache.get("canon", {}).pop(direction, None)
        self._view_reference(job, direction)

        def apply(n):
            n["assets"].setdefault("animation_views", {})[direction] = {
                "file": f"animations/views/{direction}.png", "seed": seed, "prompt": pr["positive"], "created": now(), "size": list(size),
                "engines": sorted(job.cache.get("edit_engines", ()))}
        self.lib.update(job.npc_id, apply)

    def _puppet(self, job, npc, direction, index, count, size, hard):
        """The drawn pose for frame `index` of a guided clip, in the character's colours, and its uploaded name."""
        if "palette" not in job.cache:
            palette = pose.sample_palette(self._canonical_cut(job, "down", hard))
            if not npc.get("freeform"):                      # a hat hides the hair in the picture, so its colour comes from the data
                palette["hair"] = pose.hair_colour(npc["appearance"].get("hair"))
            job.cache["palette"] = palette
        view = pose.VIEW_OF[direction]
        image = pose.paint(pose.cycle(view, count)[index][1], view, job.cache["palette"], size, height=GUIDE_HEIGHT, floor=GUIDE_FLOOR)
        buf = io.BytesIO()
        image.save(buf, "PNG")
        return image, self._upload(job, f"puppet_{direction}_{index}", buf.getvalue())

    def make_direction(self, job, step, npc, data, clip_id, direction, seed):
        """All frames of one clip in one direction, keeping the best of N candidates per frame.

        A guided clip (the walk) draws a puppet of each pose first and repaints it as the character. All its frames share
        one seed, because the seed decides the look (measured: at one seed the stride frames came out facing the camera and
        the passing frames had auburn hair; frames redrawn with another seed looked like another outfit). Which seed suits the
        whole cycle is found on two probe frames, the first stride and the first passing step, which are the poses a seed most
        often gets wrong: a seed is accepted when both come out right (up to GUIDE_SEEDS seeds are tried, the best is taken
        otherwise) and the other frames are then drawn with it. A later frame that still fails is drawn again with other
        seeds (up to GUIDE_TRIES draws), and the best draw is kept.
        """
        anim, opts = data["animations"], job.options["animations"]
        clip = anim["clips"][clip_id]
        count, candidates = opts["frames"], opts["candidates"]
        size, steps = self.settings(job.options["quality"])
        style = data["styles"][npc["style"]]
        guided = bool(clip.get("guide")) and style.get("guided_walk", True)      # flat-shaded styles get the puppet handed back: words instead
        freeform = npc.get("freeform", False)
        hard = style.get("hard_edges", False)
        view = pose.VIEW_OF[direction]
        front = self.front_reference(job, size)
        key = front["key"] or prompts.pick_key(npc, style)
        strategy = anim["guided_strategy" if guided else "reference_strategy"][direction]
        names = []
        for ref in strategy:
            if ref == "front":
                names.append(front["name"])
            elif direction != "down":
                names.append(self._view_reference(job, direction)["name"])
        canonical = self._canonical_cut(job, direction, hard)
        if canonical is None:
            raise ComfyError(f"Could not cut out the {direction} view: its background is not one flat colour")
        bad_draw = (-1.0, {"fits": False, "facing": False, "head": 0.0, "stride_error": 1.0, "legs": 0.0, "ok": False})

        def draw(i, first_seed, tries, note=""):
            """Draw frame i with up to `tries` seeds (first_seed, then steps of 7919). Returns (score, png, detail, k)."""
            job.touch()                                      # lets the UI show progress inside a long step
            if guided:
                puppet, puppet_name = self._puppet(job, npc, direction, i, count, size, hard)
                kind = pose.cycle(view, count)[i][0].rsplit("_", 1)[0]
                pr = guided_frame_prompt(anim, clip_id, direction, prompts._who(npc), style, key, freeform, kind)
                refs = [puppet_name] + names
            else:
                pr = frame_prompt(anim, clip_id, direction, i, count, style, key, freeform)
                refs = names
            best = None
            for k in range(tries):
                step["detail"] = f"frame {i + 1}/{count}" + note + (f", draw {k + 1}" if k else "")
                png = self._edit(job, refs, pr, first_seed + 7919 * k, steps, size)
                if guided:
                    full = cutout_full(png, hard)
                    score, detail = guided_score(full, puppet, view, canonical) if full is not None else bad_draw
                else:
                    cut = cutout(png, hard)
                    score, detail = candidate_score(cut, canonical) if cut is not None else (-1.0, {"head": 0, "legs": 0})
                if best is None or score > best[0]:
                    best = (score, png, detail, k)
                if guided and candidates == 1 and detail["ok"]:
                    break                                    # good enough: no need to spend another draw
            return best

        drawn, clip_seed = {}, seed
        if guided and candidates == 1:
            probes = pose.probe_indices(view, count)
            attempts = []
            for a in range(GUIDE_SEEDS):
                seed_a, tried = seed + 7919 * a, {}
                for i in probes:
                    tried[i] = draw(i, seed_a, 1, note=f", seed try {a + 1}")
                    if not tried[i][2]["ok"]:
                        break                                # this seed fails on a probe: no point drawing the other one with it
                attempts.append((seed_a, tried))
                if len(tried) == len(probes) and all(d[2]["ok"] for d in tried.values()):
                    break
            clip_seed, drawn = max(attempts, key=lambda at: (sum(d[2]["ok"] for d in at[1].values()), sum(d[0] for d in at[1].values())))
        for i in range(count):
            if i not in drawn:
                drawn[i] = draw(i, clip_seed, GUIDE_TRIES if guided and candidates == 1 else candidates)

        raw_files, scores = [], []
        for i in range(count):
            score, png, detail, k = drawn[i]
            rel = f"animations/{clip_id}/raw/{direction}_{i}.png"
            self.lib.write_asset(job.npc_id, rel, png)
            raw_files.append(rel)
            scores.append({"frame": i, "score": round(score, 3), "candidate": k, **detail})

        def apply(n):
            slot = n["assets"].setdefault("animations", {}).setdefault(clip_id, {})
            slot.setdefault("directions", {})[direction] = {"raw": raw_files, "scores": scores, "frames": count, "seed": clip_seed,
                                                            "refs": strategy, "candidates": candidates, "guided": guided,
                                                            "engines": sorted(job.cache.get("edit_engines", ())), "created": now()}
        self.lib.update(job.npc_id, apply)

    def assemble(self, job, npc, data, clip_id):
        """Cut out, measure, align and store the frames of one clip; build the report."""
        anim, opts = data["animations"], job.options["animations"]
        clip = anim["clips"][clip_id]
        style = data["styles"][npc["style"]]
        hard = style.get("hard_edges", False)
        dirs, mirror = opts["dirs"], opts["mirror_left"]
        frames_by_dir, report_dirs, flags = {}, {}, []

        if clip["kind"] == "procedural":
            count = opts["frames"] if opts["frames"] in clip["frames"] else clip["frames"][0]
            for d in generated_directions(dirs, mirror):
                still = self._canonical_cut(job, d, hard)
                if still is None:
                    raise ComfyError(f"Could not cut out the {d} view")
                frames_by_dir[d] = procedural_idle(align({d: [still]})[d][0], count, clip["lift"], clip.get("chest", 0.0))
            aligned = frames_by_dir                              # already on the canvas: aligning again would cancel the breath
        else:
            count = opts["frames"]
            current = self.lib.get(job.npc_id)["assets"].get("animations", {}).get(clip_id, {}).get("directions", {})
            for d in generated_directions(dirs, mirror):
                info = current.get(d)
                if not info or info["frames"] != count:
                    raise ComfyError(f"No {count}-frame {clip_id} frames for '{d}' yet")
                cuts = [cutout(self.lib.read_asset(job.npc_id, rel), hard) for rel in info["raw"]]
                if any(c is None for c in cuts):
                    raise ComfyError(f"Could not cut out every {d} frame: the background is not one flat colour")
                canonical = self._canonical_cut(job, d, hard)
                if clip.get("guide"):                                # frames drawn with different seeds differ in tone: see match_colours
                    cuts = [match_colours(c, canonical) for c in cuts]
                frames_by_dir[d] = cuts
                pal = palette(canonical)
                legs, head = leg_variation(cuts), head_stability(cuts)
                cover = min(palette_coverage(c, pal) for c in cuts)
                report_dirs[d] = {"leg_variation": round(legs, 3), "head_stability": round(head, 3), "palette_coverage": round(cover, 3),
                                  "frames": info["scores"]}
                if legs < (STATIC_LEG_VARIATION_FRONT_BACK if info.get("guided") and pose.VIEW_OF[d] != "side" else STATIC_LEG_VARIATION):
                    flags.append(f"{d}: the legs barely move between frames (variation {legs:.2f}); re-roll this direction or use more candidates")
                if head < HEAD_DRIFT_LIMIT:
                    flags.append(f"{d}: the head or hair changes shape between frames (stability {head:.2f}); try more candidates")
                if clip.get("guide"):
                    missed = [s["frame"] + 1 for s in info["scores"] if s.get("ok") is False]         # only drawn-from-a-puppet frames have an 'ok'
                    if missed:
                        flags.append(f"{d}: frame{'s' if len(missed) > 1 else ''} {', '.join(map(str, missed))} did not come out in the pose "
                                     f"drawn for {'it' if len(missed) == 1 else 'them'} (zoomed in or the feet in the wrong place); re-roll this direction")
                    if pose.VIEW_OF[d] == "side":
                        contrast = stride_contrast(cuts)
                        report_dirs[d]["stride_contrast"] = round(contrast, 2)
                        if contrast < STRIDE_CONTRAST_LIMIT:
                            flags.append(f"{d}: the feet never come together and apart (stride contrast {contrast:.1f}), so it will look like "
                                         f"sliding rather than walking; re-roll this direction")
                if clip_id == "run" and pose.VIEW_OF[d] == "side":
                    similarity = run_phase_similarity(cuts)
                    if similarity:
                        report_dirs[d]["phase_similarity"] = [round(v, 3) for v in similarity]
                        if all(v >= RUN_PHASE_SIMILARITY_LIMIT for v in similarity):
                            flags.append(f"{d}: the opposite run phases have nearly identical leg silhouettes; "
                                         "the cycle may look like two repeated frames")

            aligned = align(frames_by_dir)
            if clip.get("guide"):                               # align() gave every frame one height; put the step's up-and-down back
                aligned = {d: add_step_bob(fr, pose.step_bob(count, pose.VIEW_OF[d])) for d, fr in aligned.items()}
        if "left" in dirs and mirror and "right" in aligned:
            aligned["left"] = [ImageOps.mirror(f) for f in aligned["right"]]
        order = [d for d in ("down", "left", "right", "up") if d in aligned]
        files = {}
        for d in order:
            files[d] = []
            for i, frame in enumerate(aligned[d]):
                if style.get("monochrome"):                  # after the cut-out and the checks, which need the colours (keying.grey_image)
                    frame = grey_image(frame)
                buf = io.BytesIO()
                frame.save(buf, "PNG", optimize=True)
                rel = f"animations/{clip_id}/{d}_{i}.png"
                self.lib.write_asset(job.npc_id, rel, buf.getvalue())
                files[d].append(rel)
        meta = {"kind": clip["kind"], "label": clip["label"], "fps": clip_fps(clip, count), "loop": clip.get("loop", True), "frames": count,
                "experimental": clip.get("experimental", False), "quality_note": clip.get("quality_note", ""),
                "canvas": CANVAS, "created": now(), "style": npc["style"], "order": order, "files": files,
                "mirrored": {"left": "right"} if "left" in order and mirror and "left" not in report_dirs else {},
                "report": {"directions": report_dirs, "flags": flags, "ok": not flags}}

        def apply(n):
            slot = n["assets"].setdefault("animations", {}).setdefault(clip_id, {})
            slot.update(meta)
            slot.pop("stale", None)
        self.lib.update(job.npc_id, apply)

    def reassemble(self, npc_id, data, clip_id):
        """Re-run cut-out, alignment and the report from the stored raw frames. Local only: needs no ComfyUI."""
        npc = self.lib.get(npc_id)
        meta = npc.get("assets", {}).get("animations", {}).get(clip_id)
        if not meta or not meta.get("order"):
            raise ComfyError(f"This character has no '{clip_id}' animation to re-assemble")
        job = types.SimpleNamespace(npc_id=npc_id, cache={}, options={"animations": {
            "frames": meta["frames"], "dirs": meta["order"], "mirror_left": bool(meta.get("mirrored", {}).get("left")), "candidates": 1}})
        self.assemble(job, npc, data, clip_id)
