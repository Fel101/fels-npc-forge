"""Clean hands: an option of the clean-up (npcgen/cleanup3d.py) that replaces a model's generated hands with clean
five-finger ones.

Image-to-3D generators read each picture in 16-pixel squares, and in a full-body view a finger is about 6 pixels wide, so
the hands come out as mittens: two or three lumps for five fingers, although the turnaround pictures draw five clean
fingers (all nine library models, 2026-09-27; Pixal3D's High setting cannot help, its grid is already finer than a finger).
A mitten cannot be rigged into fingers that bend, and it looks wrong close up.

How (cleanup3d.refine, with `hands`):
  1. find(): Blender renders the model for the pose detector (npcgen/blender/pose_views.py) and SDPose finds the wrists,
     elbows and hand points (npcgen/posekeys.py).
  2. points(): those hand points in the model's own frame, for the painter: a picture's fingers are not painted onto the
     clothes or skin behind the hand (npcgen/paint3d.py). The pictures draw longer, straighter fingers than the mitten
     has, so painting put finger-shaped skin on the trousers beside Devi's hands, which the mitten half hid and a clean
     hand does not.
  3. picture_hands(): SDPose finds the hands in the turnaround pictures too. Where each picture shows a hand goes to the
     painter as well (in Imani's side pictures her hand is drawn a little off the model's, and a whole second hand was
     painted on her thigh), and the pixels inside the hands give the skin colour: the old hand's paint was the first
     source tried, and Naomi's hands, which rest on her black skirt and were fused to it, came out black.
  4. replace(), after the game version is made: npcgen/blender/hands.py cuts each mitten off at the arm's narrowest point
     past the wrist, builds a hand (npcgen/blender/hand_shape.py), squeezes its wrist to the arm's cut, joins the two and
     paints it: the skin colour, its own creases, lighter palms and nails (one 512 x 512 texture, material
     "npcforge_hand"). The new hand's 21 points and wrist ring go into the report, for the rig (npcgen/rig3d.py), whose
     finger bones then sit exactly in the new fingers.

Only when asked ("Clean hands" in the Game version section, or --clean-hands): paws, claws and hooves must keep their
own shape, and a future generator may make good hands itself.
"""
import io
import json
import pathlib
import tempfile

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from . import posekeys, rig3d
from .comfy import Cancelled, ComfyError

SCRIPT = pathlib.Path(__file__).resolve().parent / "blender" / "hands.py"
HEIGHT_M = 1.7            # the frame the pose views and the hands script share (any height works; they must agree)
TIMEOUT_SECONDS = 600
SIDE_VIEW = {"l": "left", "r": "right"}   # the pose view that sees each hand from the side (rig_keypoints.SIDE_VIEW)
ARM = {"l": (6, 7), "r": (3, 4)}          # SDPose's body points of each elbow and wrist (rig_keypoints.BODY)
ARM_WIDTH = 0.045                         # of a picture's height: a forearm, and a little either side, across
MIN_SCORE = 0.3


def availability(cfg, comfy):
    """{"model", "licence", "missing": [...]}: Blender, and SDPose in ComfyUI to find the hands."""
    pose = posekeys.availability(comfy) if comfy is not None else {"missing": ["ComfyUI"]}
    return {"model": posekeys.CHECKPOINT, "licence": posekeys.LICENCE,
            "missing": rig3d.availability(cfg)["missing"] + pose["missing"]}


class Finder:
    """SDPose in ComfyUI, as clean hands uses it: on the model's pose renders, and on the turnaround pictures."""

    def __init__(self, comfy, npc_id, cancel=None):
        self.comfy, self.npc_id, self.cancel = comfy, npc_id, cancel

    def renders(self, views_dir):
        """posekeys.keypoints of the renders in views_dir."""
        return posekeys.keypoints(self.comfy, views_dir, self.npc_id, self.cancel)

    def pictures(self, images):
        """{key: PIL image} -> {key: SDPose's person} (posekeys.detect)."""
        return posekeys.detect(self.comfy, images, self.npc_id, self.cancel)


def find(cfg, model_glb, finder, cancel=None):
    """SDPose's keypoints of model_glb (bytes) in its pose views (posekeys.keypoints' form, with the cameras); finder is
    a Finder."""
    with tempfile.TemporaryDirectory(prefix="npcforge-hands-") as tmp:
        tmp = pathlib.Path(tmp)
        (tmp / "model.glb").write_bytes(model_glb)
        views = tmp / "views"
        rig3d.run_script(cfg, rig3d.VIEWS_SCRIPT, [str(tmp / "model.glb"), str(views), "--height", str(HEIGHT_M)], tmp,
                         views / "cams.json", "Rendering the model for the pose detector",
                         "Blender could not render the model for the pose detector", cancel=cancel, timeout=TIMEOUT_SECONDS)
        return finder.renders(views)


def points(keys, positions):
    """Each hand's 21 points and then its elbow (22 rows) in the model's own frame (glTF: Y up), or NaN where SDPose was
    unsure, from keys (find()) and the model's vertex positions (world space, any number). Across comes from the front
    view, depth from the view that sees the hand's side and height from both, as rig_keypoints.Keypoints.point does; the
    pose views put the model where npcgen/blender/rig_humanoid.load does (Blender's axes, scaled to HEIGHT_M, feet at 0,
    centred), which is undone."""
    b = np.stack([positions[:, 0], -positions[:, 2], positions[:, 1]], 1)       # glTF -> Blender axes
    lo, hi = b.min(0), b.max(0)
    s = HEIGHT_M / (hi[2] - lo[2])
    shift = np.array([-(lo[0] + hi[0]) / 2 * s, -(lo[1] + hi[1]) / 2 * s, -lo[2] * s])
    cams = keys["cams"]

    def plane(view, uvs):
        c = cams[view]
        w, h = c["size"]
        uv = np.asarray(uvs, float)
        return (np.asarray(c["centre"]) + np.outer((uv[:, 0] - w / 2) * c["px"], c["right"])
                + np.outer((h / 2 - uv[:, 1]) * c["px"], c["up"]))

    out = {}
    for side in "lr":
        front = np.asarray(keys["views"]["front"][f"hand_{side}"], float).reshape(-1, 3)
        beside = np.asarray(keys["views"][SIDE_VIEW[side]][f"hand_{side}"], float).reshape(-1, 3)
        if len(front) < 21 or len(beside) < 21:
            continue
        elbow = ARM[side][0]
        body_f = np.asarray(keys["views"]["front"].get("body") or [], float).reshape(-1, 3)
        body_s = np.asarray(keys["views"][SIDE_VIEW[side]].get("body") or [], float).reshape(-1, 3)
        no = np.array([[0.0, 0.0, 0.0]])
        front = np.concatenate([front[:21], body_f[elbow:elbow + 1] if len(body_f) > elbow else no])
        beside = np.concatenate([beside[:21], body_s[elbow:elbow + 1] if len(body_s) > elbow else no])
        pf, ps = plane("front", front[:, :2]), plane(SIDE_VIEW[side], beside[:, :2])
        p = np.stack([pf[:, 0], ps[:, 1], (pf[:, 2] + ps[:, 2]) / 2], 1)
        p[(front[:, 2] < MIN_SCORE) | (beside[:, 2] < MIN_SCORE)] = np.nan
        blender = (p - shift) / s
        out[side] = np.stack([blender[:, 0], blender[:, 2], -blender[:, 1]], 1)      # Blender -> glTF axes
    return out


def _hull(points):
    """The convex hull of 2D points, in order (monotone chain)."""
    p = sorted(set(map(tuple, np.round(points, 3))))
    if len(p) < 3:
        return p

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lower, upper = [], []
    for q in p:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], q) <= 0:
            lower.pop()
        lower.append(q)
    for q in reversed(p):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], q) <= 0:
            upper.pop()
        upper.append(q)
    return lower[:-1] + upper[:-1]


def typical_colour(rgb):
    """The median of the colours whose tint (colour over brightness) is typical of them: a few pixels of sleeve,
    background or shadow among a hand's do not move it."""
    lum = rgb[:, 0] * 0.2126 + rgb[:, 1] * 0.7152 + rgb[:, 2] * 0.0722
    tint = rgb / np.maximum(lum[:, None], 1e-4)
    typical = np.linalg.norm(tint - np.median(tint, 0), axis=1) < 0.25
    if not typical.any():                             # tints split in two: the median of all, not of none
        typical[:] = True
    return np.median(rgb[typical], 0), float(typical.mean())


def picture_hands(finder, pictures, least=300, margin=0.012):
    """Where the turnaround pictures show hands and forearms, and the hands' colour there. For each hand SDPose finds
    sure enough (21 points, mean score 0.5): the hull of its sure points, grown by `margin` of the picture's height
    (fingertips and edges SDPose's points stop short of) for the mask, and shrunk by a sixth towards its middle for the
    colour (the pixels there that are not the white background; their typical colour). Each forearm whose elbow and
    wrist SDPose is sure of (0.5) goes into the mask too, ARM_WIDTH across: in Imani's side pictures her tattooed forearm
    hangs across her hip, and was painted there. pictures: {view: PNG bytes}. Returns (colour (sRGB, 0-1) or None,
    {view: boolean mask (height x width)}, report)."""
    images = {v: Image.open(io.BytesIO(raw)).convert("RGB") for v, raw in pictures.items()}
    found = finder.pictures(images)
    samples, masks, hands, arms = [], {}, 0, 0
    for view, person in found.items():
        rgb = np.asarray(images[view], np.float32) / 255
        grow = max(1, int(round(margin * images[view].height)))
        body = np.asarray(person.get("pose_keypoints_2d") or [], float).reshape(-1, 3)
        for elbow, wrist in ARM.values():
            if len(body) > wrist and min(body[elbow, 2], body[wrist, 2]) >= 0.5:
                arm = Image.new("L", images[view].size, 0)
                width = max(1, int(round(ARM_WIDTH * images[view].height)))
                a, b = tuple(body[elbow, :2]), tuple(body[wrist, :2])
                draw = ImageDraw.Draw(arm)
                draw.line([a, b], fill=255, width=width)
                for c in (a, b):                              # round ends
                    draw.ellipse([c[0] - width / 2, c[1] - width / 2, c[0] + width / 2, c[1] + width / 2], fill=255)
                masks[view] = masks.get(view, np.zeros(rgb.shape[:2], bool)) | (np.asarray(arm) > 0)
                arms += 1
        for part in ("hand_left_keypoints_2d", "hand_right_keypoints_2d"):
            pts = np.asarray(person.get(part) or [], float).reshape(-1, 3)
            if len(pts) < 21 or pts[:21, 2].mean() < 0.5:
                continue
            sure = pts[:21][pts[:21, 2] >= MIN_SCORE, :2]
            if len(sure) < 8:
                continue
            hull = _hull(sure)
            middle = sure.mean(0)
            inner = Image.new("L", images[view].size, 0)
            ImageDraw.Draw(inner).polygon([tuple(middle + (np.array(q) - middle) * (5 / 6)) for q in hull], fill=255)
            px = rgb[np.asarray(inner) > 0]
            samples.append(px[(px.min(1) < 0.9) & (px.max(1) > 0.03)])      # not the white background, nor black
            outer = Image.new("L", images[view].size, 0)
            ImageDraw.Draw(outer).polygon([tuple(q) for q in hull], fill=255)
            outer = outer.filter(ImageFilter.MaxFilter(2 * grow + 1))      # grown by `margin` all round
            masks[view] = masks.get(view, np.zeros(rgb.shape[:2], bool)) | (np.asarray(outer) > 0)
            hands += 1
    px = np.concatenate(samples) if samples else np.zeros((0, 3))
    report = {"hands_seen": hands, "forearms_seen": arms, "pixels": int(len(px)),
              "masked": {v: int(m.sum()) for v, m in masks.items()}}
    if len(px) < least:
        return None, masks, report
    colour, share = typical_colour(px)
    report.update({"typical_share": round(share, 3), "srgb": [round(float(c), 3) for c in colour]})
    return colour, masks, report


def replace(cfg, model_glb, keys, cancel=None, skin=None):
    """Replace the hands of model_glb (bytes), placed with keys (find(), of this model or the one it was simplified
    from), in the colour `skin` (sRGB 0-1, picture_hands(); None: from the old hand's paint). Returns (GLB bytes, poster
    PNG bytes or None, report). Raises ComfyError with a readable reason when a hand cannot be found or joined."""
    with tempfile.TemporaryDirectory(prefix="npcforge-hands-") as tmp:
        tmp = pathlib.Path(tmp)
        src, out, poster, report = tmp / "model.glb", tmp / "hands.glb", tmp / "poster.png", tmp / "report.json"
        src.write_bytes(model_glb)
        (tmp / "keys.json").write_text(json.dumps(keys), "utf-8")
        colour = ["--skin", ",".join(f"{float(c):.4f}" for c in skin)] if skin is not None else []
        rig3d.run_script(cfg, SCRIPT, [str(src), str(out), "--keys", str(tmp / "keys.json"), "--height", str(HEIGHT_M),
                                       "--report", str(report), "--poster", str(poster), *colour],
                         tmp, out, "Making clean hands", "Clean hands", cancel=cancel, timeout=TIMEOUT_SECONDS)
        info = json.loads(report.read_text("utf-8")) if report.exists() else {}
        info["keypoint_scores"] = posekeys.summary(keys)
        return out.read_bytes(), poster.read_bytes() if poster.exists() else None, info


def try_replace(cfg, model_glb, keys, cancel=None, skin=None):
    """replace(), but a hand that cannot be made leaves the model as it was: (GLB, poster or None, report), the report
    {"error": reason} then. The rest of a clean-up is minutes of work worth keeping."""
    try:
        return replace(cfg, model_glb, keys, cancel=cancel, skin=skin)
    except Cancelled:
        raise
    except ComfyError as e:
        return model_glb, None, {"error": str(e)}


def colour_note(colour, pictures=True):
    """Where the new hands' colour came from, when not from the pictures' hands (None when it did): for the job's note
    and the card. colour: the report's "colour" (its "from": "pictures", "old hand" or "default"); pictures: whether
    there were turnaround pictures to look in."""
    source = (colour or {}).get("from")
    if source == "pictures":
        return None
    if source == "default":
        return ("given a plain skin tone: neither the pictures' hands nor the old model's paint "
                "showed a colour to use")
    if not pictures:
        return "coloured from the old model's paint: there are no turnaround pictures"
    return "coloured from the old model's paint: the model sheet's pictures did not show the hands clearly enough"


def unprotected(hand_points):
    """The sides whose wrist or elbow SDPose was unsure of on the model (hand_points: points()): there the painter has
    no forearm bone to keep the pictures' hand and forearm masks (picture_hands) off the model's own forearm, which would
    lose its paint (Imani's tattoos) to the colour around it."""
    out = []
    for side in "lr":
        p = np.asarray(hand_points.get(side, np.full((22, 3), np.nan)), float)
        if len(p) < 22 or not (np.isfinite(p[0]).all() and np.isfinite(p[21]).all()):
            out.append(side)
    return out


def summary(report, pictures=True):
    """What the model's record keeps (model_3d.refined.hands): per hand its 21 points and wrist ring (the model's own
    frame, for the rig) and length, the colour used, and a note when that colour is not the pictures' (colour_note)."""
    out = {side: {"points": report[side]["points"], "ring": report[side]["ring"], "length": report[side]["hand_length"],
                  **({"notes": report[side]["notes"]} if report[side].get("notes") else {})}
           for side in "lr" if side in report}
    out["colour"] = report.get("colour", {})
    note = colour_note(out["colour"], pictures)
    if note:
        out["colour_note"] = note
    return out
