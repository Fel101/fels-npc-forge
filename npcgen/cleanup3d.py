"""Remove the hidden inner skin of image-to-3D models.

Pixal3D (TRELLIS-style) meshes are thin hollow shells: every surface has a second skin a millimetre or two inside it,
facing inwards, that no camera can ever see. Measured on Clara (Pixal3D medium): about half of the 700k faces are never
visible from 64 directions all round, and a third are that inner skin directly behind a visible surface.

Only the inner skin behind a visible surface is removed: while that surface stays, the removal can never open a hole.
Hidden surfaces deeper in (the body under a vest or a skirt, and its own inner skin) are kept, because a moving leg can
show them. The rule that would catch those cannot tell the inside of a hollow limb from the gap between a garment and
the body from one direction, so it is not used.
"""
import json
import pathlib
import tempfile
import time

import numpy as np

from . import eyes as eyes_mod, hands3d, headdetail, headgraft, paint3d, rig3d, unwrap
from .comfy import Cancelled, ComfyError
from .paint3d import Glb, dot3, rasterize

LOD_SCRIPT = pathlib.Path(__file__).resolve().parent / "blender" / "game_lod.py"
ORIGINAL_FILE = "model_3d_original.glb"   # the generator's own model, kept as the model's "raw" file
GAME_FACES = 100_000     # the game version's size; Clara at 100k looked the same as at 460k in lit renders
# The size is chosen per character (the "game_faces" option): these are the presets the page offers, any whole number between the
# limits is accepted. A hand has only a few hundred vertices at 100k, so the clean hands and the skeleton's finger weights want
# the larger sizes (docs/model-cleanup.md has what was measured at each).
GAME_SIZES = (("light", 25_000, "Light"), ("standard", 50_000, "Standard"), ("detailed", GAME_FACES, "Detailed"), ("high", 200_000, "High"))
MIN_GAME_FACES, MAX_GAME_FACES = 10_000, 400_000
LOD_TIMEOUT_SECONDS = 900


def clean_faces(value):
    """The game version's size in faces from an option: nothing (the default, 100,000), a preset name ("light", "standard",
    "detailed", "high") or a whole number between 10,000 and 400,000. Anything else is a ValueError (a typo used to be
    indistinguishable from the default)."""
    if value is None or value == "":
        return GAME_FACES
    if isinstance(value, str) and value.strip().lower() in {name for name, _faces, _label in GAME_SIZES}:
        return next(faces for name, faces, _label in GAME_SIZES if name == value.strip().lower())
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError("game_faces must be a number of faces or one of: " + ", ".join(name for name, _f, _l in GAME_SIZES))
    try:
        faces = int(float(value))
    except (TypeError, ValueError):
        raise ValueError("game_faces must be a number of faces or one of: " + ", ".join(name for name, _f, _l in GAME_SIZES)) from None
    if not MIN_GAME_FACES <= faces <= MAX_GAME_FACES:
        raise ValueError(f"game_faces must be between {MIN_GAME_FACES:,} and {MAX_GAME_FACES:,} faces (or one of: "
                         + ", ".join(name for name, _f, _l in GAME_SIZES) + ")")
    return faces


def game_size_choices():
    """What the page lists: [{id, faces, label}]."""
    return [{"id": name, "faces": faces, "label": f"{label} ({faces:,} faces)"} for name, faces, label in GAME_SIZES]
# the clean-up's options: (job step key, asset flag), each only when asked
OPTIONS = (("face", "sharp_face"), ("eyes", "smooth_eyes"), ("hair", "match_hair"), ("hands", "clean_hands"),
           ("head", "attach_head"))
OPTION_LABELS = {"face": "a sharper face", "eyes": "the eye rings smoothed", "hair": "the hair matched to the pictures",
                 "hands": "clean five-finger hands", "head": "the detailed head attached"}

DIRECTIONS = 64         # views spread evenly over a sphere (Fibonacci), including from below
RESOLUTION = 1024        # depth-buffer size per view
WALL = 0.008             # of the model height: how far behind a visible surface an inner skin may sit
SAME_SURFACE = 2e-4      # of the bounding radius: depth difference still counted as the visible surface itself


def sphere_directions(count):
    i = np.arange(count) + 0.5
    phi = np.arccos(1 - 2 * i / count)
    theta = np.pi * (1 + 5 ** 0.5) * i
    return np.stack([np.cos(theta) * np.sin(phi), np.cos(phi), np.sin(theta) * np.sin(phi)], 1)


def inner_skin(pos, tri, directions=DIRECTIONS, resolution=RESOLUTION, wall=WALL):
    """Boolean per triangle: never visible, and seen facing away just behind a visible surface from some direction."""
    normal = np.cross(pos[tri[:, 1]] - pos[tri[:, 0]], pos[tri[:, 2]] - pos[tri[:, 0]])
    normal /= np.maximum(np.linalg.norm(normal, axis=1, keepdims=True), 1e-20)
    height = pos[:, 1].max() - pos[:, 1].min()
    centre = (pos.max(0) + pos.min(0)) / 2
    radius = np.linalg.norm(pos - centre, axis=1).max()
    rel = pos - centre
    seen = np.zeros(len(tri), bool)
    behind = np.zeros(len(tri), bool)
    for d in sphere_directions(directions):
        a = np.cross(d, [0, 1, 0] if abs(d[1]) < 0.9 else [1, 0, 0])
        a /= np.linalg.norm(a)
        b = np.cross(d, a)
        xy = np.stack([(dot3(rel, a) / radius + 1) * resolution / 2, (dot3(rel, b) / radius + 1) * resolution / 2], 1)
        pix, t, bary = rasterize(xy, tri, resolution, resolution)
        depth = np.einsum("nk,nk->n", bary, dot3(rel, d)[tri[t]])
        nearest = np.full(resolution * resolution, -np.inf)
        np.maximum.at(nearest, pix, depth)
        gap = nearest[pix] - depth
        seen[t[gap <= SAME_SURFACE * radius]] = True
        behind[t[(gap > SAME_SURFACE * radius) & (gap < wall * height) & (dot3(normal[t], d) < 0)]] = True
    return ~seen & behind


def remove_inner_skin(model_glb, report=None):
    """The GLB without its hidden inner skin (vertices, UVs and textures untouched; unused vertices stay)."""
    glb = Glb(model_glb)
    report = report if report is not None else {}
    removed = total = 0
    for mesh in glb.json.get("meshes", []):
        for prim in mesh["primitives"]:
            if prim.get("mode", 4) != 4 or "indices" not in prim:
                continue
            pos = glb.accessor(prim["attributes"]["POSITION"]).astype(np.float64)
            tri = glb.accessor(prim["indices"]).reshape(-1, 3).astype(np.int64)
            drop = inner_skin(pos, tri)
            glb.set_triangles(prim, tri[~drop])
            removed += int(drop.sum())
            total += len(tri)
    report["inner_skin"] = {"faces_before": total, "faces_removed": removed,
                            "share_removed": round(removed / max(total, 1), 3)}
    return glb.dumps()


def refine(cfg, model_glb, views=None, faces=GAME_FACES, cancel=None, closeups=None, eyes=None, hair=None, hands=None, head=None):
    """The whole clean-up of a generated model: inner skin removed, painted from `views` ({sheet view: PNG bytes}; None
    skips painting), then the game version made in Blender. Returns (GLB bytes, poster PNG bytes or None, report).

    With `closeups` (npcgen/headdetail.closeups), the head first gets a fine texture of its own (headdetail.head_layout)
    and the close-ups are painted onto it: the sharper face. With `eyes` (npcgen/eyes.locate), the eye regions are smoothed
    first (eyes.smooth), and the head gets its own texture too, so the smoothed surface has fresh texture room. With
    `hair` (npcgen/hairmask.finder), the model's hair is painted only from hair in the pictures (paint3d.paint). With
    `hands` (npcgen/hands3d.Finder), the hands are found first (npcgen/hands3d.find), the pictures' fingers are not
    painted onto what lies behind them, and the game version's hands are replaced by clean five-finger ones in the
    pictures' skin colour; if that fails the game version keeps its own hands and report["hands"] says why. With `head`
    (npcgen/headgraft.Attacher), the game version's face is replaced by the detailed head's, last of all (npcgen/headgraft.py); if
    that fails the game version keeps its own face and report["head"] says why.

    A model with no colour texture of its own (Hunyuan's shapes) is unwrapped first (npcgen/unwrap.py: the head already
    gets a half of the texture) and painted from blank, so it needs `views`."""
    report = {}

    def check():
        if cancel is not None and cancel.is_set():
            raise Cancelled()

    started = time.monotonic()
    model = remove_inner_skin(model_glb, report)
    report["inner_skin"]["seconds"] = round(time.monotonic() - started, 1)
    check()
    keys, hand_points, skin, hand_pixels = None, None, None, None
    if hands is not None:
        started = time.monotonic()
        try:
            keys = hands3d.find(cfg, model, hands, cancel=cancel)
            hand_points = hands3d.points(keys, paint3d.all_triangles(paint3d.Glb(model))[0])
            if views:
                skin, hand_pixels, report["hands_pictures"] = hands3d.picture_hands(hands, views)
                unsure = hands3d.unprotected(hand_points)
                if unsure:                           # the pictures' masks would take the paint off that forearm
                    hand_pixels = None
                    report["hands_pictures"]["masks_unused"] = ("SDPose was unsure of the model's "
                                                                 + " and ".join({"l": "left", "r": "right"}[s] for s in unsure)
                                                                 + " wrist or elbow")
        except Cancelled:
            raise
        except ComfyError as e:                      # the rest of the clean-up is still worth having
            report["hands"] = {"error": str(e)}
            keys = hand_points = skin = hand_pixels = None     # and the model keeps its own hands
        report["hands_found_seconds"] = round(time.monotonic() - started, 1)
        check()
    blank = unwrap.needs_texture(model)
    if blank:
        if not views:
            raise unwrap.UnwrapError("A model with no colour of its own is painted from the turnaround pictures: make the model sheet first")
        started = time.monotonic()
        model = unwrap.unwrap(model, report, **({"cancel": cancel} if cancel is not None else {}))
        report["unwrap"]["seconds"] = round(time.monotonic() - started, 1)
        check()
    if eyes:
        started = time.monotonic()
        model = eyes_mod.smooth(model, eyes, report)
        report["eyes"]["seconds"] = round(time.monotonic() - started, 1)
        check()
    if views and (closeups or eyes) and not blank:
        started = time.monotonic()
        model = headdetail.head_layout(model, report, **({"cancel": cancel} if cancel is not None else {}))
        report["head_layout"]["seconds"] = round(time.monotonic() - started, 1)
        check()
    if views:
        started = time.monotonic()
        paint_report = {}
        model = paint3d.paint(model, views, report=paint_report, closeups=closeups, blank=blank, hair=hair,
                              hands=hand_points, hand_pixels=hand_pixels)
        report["paint"] = {"views": paint_report.get("views", {}), "textures": paint_report.get("textures", []),
                           "closeups": paint_report.get("closeups", {}), "seconds": round(time.monotonic() - started, 1)}
        for part in ("hair", "hands"):
            if part in paint_report:
                report["paint"][part] = paint_report[part]
        check()
    with tempfile.TemporaryDirectory(prefix="npcforge-lod-") as tmp:
        tmp = pathlib.Path(tmp)
        src, out, poster, lod_report = tmp / "model.glb", tmp / "game.glb", tmp / "poster.png", tmp / "report.json"
        src.write_bytes(model)
        rig3d.run_script(cfg, LOD_SCRIPT, [str(src), str(out), "--faces", str(int(faces)), "--report", str(lod_report),
                                           "--poster", str(poster)],
                         tmp, out, "Making the game version", "Blender could not make the game version",
                         cancel=cancel, timeout=LOD_TIMEOUT_SECONDS)
        report["game_version"] = json.loads(lod_report.read_text("utf-8")) if lod_report.exists() else {}
        game, still = out.read_bytes(), poster.read_bytes() if poster.exists() else None
    if keys is not None:
        check()
        started = time.monotonic()
        game, new_still, report["hands"] = hands3d.try_replace(cfg, game, keys, cancel=cancel, skin=skin)
        report["hands"]["seconds"] = round(time.monotonic() - started, 1)
        still = new_still or still
    if head is not None:
        check()
        game, new_still, report["head"] = headgraft.try_attach(cfg, game, head, cancel=cancel)
        still = new_still or still
    return game, still, report
