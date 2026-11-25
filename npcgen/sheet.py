"""Model sheets: portrait, front, both sides and back of one character, each drawn by Mage-Flow-Edit.

How it works
------------
1. The full-body sprite is the reference for the four full-body views (the portrait view uses the portrait when there is
   one). It is placed on a white panel first, so the model redraws it instead of having to move it.
2. Every view is its own edit with a plain-language direction ("a true side profile view facing right"). No pose guide is
   used: a mannequin reference was measured to be no better and to leak into the result (one back view lost the hat
   because the mannequin was bald), see docs/model-sheet.md.
3. Each full-body view is scaled and shifted so that every figure has the same height and stands on the same line, then the
   five panels are stitched into one sheet.
4. A coarse report flags a view whose colours differ a lot from the sprite.
"""
import io

import numpy as np
from PIL import Image

from . import anim, engines, licences, media, prompts, three_d
from .comfy import ComfyError
from .keying import grey_png
from .library import now

VIEWS = ("portrait", "front", "side_right", "side_left", "back")
FULL_BODY = ("front", "side_right", "side_left", "back")

PANEL_FILL = 0.90          # height of the figure as a share of the panel (models kept 0.86-0.93 on their own)
BASELINE = 0.97            # where the feet stand, as a share of the panel height
BG_TOLERANCE = 22          # how far a pixel may be from the backdrop colour and still count as backdrop
GAP = 16                   # white gutter between panels on the stitched sheet
MIN_COLOUR_MATCH = 0.55    # good views measured 0.76-0.99; this only catches wholesale colour changes (a blouse turning green)


# ---- prompts ----------------------------------------------------------------------------------------------------------
def _barefoot_ok(npc):
    outfit = (npc["appearance"].get("outfit") or "").lower()
    return bool(npc.get("freeform")) or any(w in outfit for w in ("barefoot", "bare feet", "sandals", "flip-flops"))


def _hair_line(npc):
    """The character's real hair, stated for the back view, where the model has to invent it. Data, not examples: a sentence
    listing 'a bun, braid or ponytail' made the model draw a bun on every character."""
    hair = (npc["appearance"].get("hair") or "").strip()
    if not hair:
        return ""
    return "Seen from behind, the head is bald." if hair.lower() == "bald" else f"Seen from behind, the hair is {hair}."


def view_prompt(spec, view, style, npc, pose):
    """Edit prompt for one view of a character. `spec` is data/sheet.json."""
    freeform = bool(npc.get("freeform"))
    v = spec["views"][view]
    arms = spec["poses"].get(pose, spec["poses"][spec["default_pose"]])["arms"]
    body = v["prompt"].format(who=prompts._who(npc), arms=arms, hair="" if freeform else ", the hair")
    if not freeform:
        body += " " + v.get("people_extra", "").format(hair_line=_hair_line(npc))
    positive = (f"{body.strip()} {prompts.keep_phrase(freeform)}, and keep the exact same {style['edit_name']} art style, "
                f"line style and colour palette. {spec['tail']}")
    terms = f"{prompts._edit_negative(style)}, {spec['negative']}" + ("" if _barefoot_ok(npc) else f", {spec['barefoot_negative']}")
    negative = ", ".join(dict.fromkeys(t.strip() for t in terms.split(",") if t.strip()))     # each term once, in order
    return {"positive": positive, "negative": negative}


# ---- image work (pure functions, no ComfyUI) ---------------------------------------------------------------------------
def _rgb(png):
    return Image.open(io.BytesIO(png)).convert("RGB")


def _png(img):
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def border_colour(arr):
    ring = np.concatenate([arr[:4].reshape(-1, 3), arr[-4:].reshape(-1, 3), arr[:, :4].reshape(-1, 3), arr[:, -4:].reshape(-1, 3)])
    return np.median(ring, axis=0).astype(int)


def figure_mask(arr, bg, tolerance=BG_TOLERANCE):
    return np.abs(arr.astype(int) - bg).max(axis=2) > tolerance


def mask_box(mask):
    """Bounding box of a mask, ignoring specks: a row or column needs at least 3 pixels to count."""
    rows, cols = np.where(mask.sum(axis=1) >= 3)[0], np.where(mask.sum(axis=0) >= 3)[0]
    if not len(rows) or not len(cols):
        return None
    return int(cols[0]), int(rows[0]), int(cols[-1]) + 1, int(rows[-1]) + 1


def fit_reference(png, panel):
    """Put the sprite (a cut-out with transparency) or the portrait (a full picture) on a white panel; returns PNG bytes."""
    w, h = panel
    im = Image.open(io.BytesIO(png))
    cut_out = im.mode == "RGBA" and im.getchannel("A").getextrema()[0] < 255
    if cut_out:
        flat = Image.new("RGBA", im.size, (255, 255, 255, 255))
        flat.alpha_composite(im.convert("RGBA"))
        box = im.getchannel("A").point(lambda a: 255 if a > 8 else 0).getbbox() or (0, 0, *im.size)
        fig = flat.convert("RGB").crop(box)
        scale = min(h * PANEL_FILL / fig.height, w * 0.96 / fig.width)
    else:
        fig = im.convert("RGB")
        scale = min(h / fig.height, w / fig.width)
    fig = fig.resize((max(1, round(fig.width * scale)), max(1, round(fig.height * scale))), Image.LANCZOS)
    out = Image.new("RGB", (w, h), "white")
    out.paste(fig, ((w - fig.width) // 2, round(h * BASELINE) - fig.height if cut_out else (h - fig.height) // 2))
    return _png(out)


def align_panel(png, panel):
    """Scale and shift a full-body view so the figure is PANEL_FILL of the panel high and stands on the baseline.

    The whole picture is transformed (not a cut-out pasted on white), and the margin is filled with the picture's own
    backdrop colour, so an off-white background never shows a seam. Returns (PNG bytes, {height, scale}).
    """
    w, h = panel
    im = _rgb(png)
    if im.size != (w, h):
        im = im.resize((w, h), Image.LANCZOS)
    arr = np.asarray(im)
    bg = border_colour(arr)
    box = mask_box(figure_mask(arr, bg))
    if box is None:
        return _png(im), {"height": 0.0, "scale": 1.0, "aligned": False}
    fig_w, fig_h = box[2] - box[0], box[3] - box[1]
    scale = min(h * PANEL_FILL / fig_h, w * 0.96 / fig_w)
    scale = max(0.7, min(1.4, scale))                      # a broken cut-out must not blow the picture up
    scaled = im.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.LANCZOS)
    out = Image.new("RGB", (w, h), tuple(int(c) for c in bg))
    out.paste(scaled, (round(w / 2 - (box[0] + box[2]) / 2 * scale), round(h * BASELINE - box[3] * scale)))
    return _png(out), {"height": round(fig_h / h, 3), "scale": round(scale, 3), "aligned": True}


def colour_match(png, reference_palette):
    """Share of the figure's pixels whose colour is near the reference palette (anim.palette of the sprite)."""
    arr = np.asarray(_rgb(png))
    bg = border_colour(arr)
    mask = figure_mask(arr, bg)
    rgba = np.dstack([arr, np.where(mask, 255, 0)]).astype(np.uint8)
    return anim.palette_coverage(Image.fromarray(rgba, "RGBA"), reference_palette)


def stitch(panels, gap=GAP):
    """Panels side by side on white."""
    w, h = panels[0].size
    sheet = Image.new("RGB", (len(panels) * w + gap * (len(panels) - 1), h), "white")
    for i, p in enumerate(panels):
        sheet.paste(p, (i * (w + gap), 0))
    return sheet


def view_seed(seed, view):
    """Each view gets its own noise, so one bad view can be re-rolled without disturbing the rest."""
    return (int(seed) + 1013 * VIEWS.index(view)) % (2 ** 32)


# ---- generation (talks to ComfyUI) ----------------------------------------------------------------------------------------
class Sheeter:
    """Runs the steps of a model-sheet job. `job` needs .npc_id, .options, .cancel (an Event) and .cache (a dict)."""

    def __init__(self, comfy, lib, cfg):
        self.comfy, self.lib, self.cfg = comfy, lib, cfg

    def settings(self, quality_id):
        q = self.cfg["quality"].get(quality_id) or self.cfg["quality"]["standard"]
        return tuple(q["sheet"]), q["sheet_steps"]

    def _reference(self, job, kind, panel):
        """The sprite or portrait fitted to the panel and uploaded to ComfyUI, once per job and asset version."""
        meta = self.lib.get(job.npc_id)["assets"].get(kind)
        if not meta:
            raise ComfyError("The model sheet is drawn from the full-body sprite: generate the sprite first")
        cache = job.cache.setdefault("sheet_refs", {})
        stamp = (meta.get("created"), tuple(panel))
        if kind in cache and cache[kind]["stamp"] == stamp:
            return cache[kind]["name"]
        fitted = fit_reference(self.lib.read_asset(job.npc_id, meta["file"]), panel)
        cache[kind] = {"stamp": stamp, "name": self.comfy.upload_image(fitted, f"{job.npc_id}_sheet_{kind}.png")}
        return cache[kind]["name"]

    def make_view(self, job, npc, data, view, seed):
        spec, style = data["sheet"], data["styles"][npc["style"]]
        panel, steps = self.settings(job.options["quality"])
        pose = job.options.get("sheet", {}).get("pose") or spec["default_pose"]
        kind = "portrait" if view == "portrait" and "portrait" in npc["assets"] else "sprite"
        pr = view_prompt(spec, view, style, npc, pose)
        png, used = engines.edit(self.comfy, job.options.get("edit_model"), [self._reference(job, kind, panel)], pr["positive"],
                                 view_seed(seed, view), steps, self.cfg["sheet_cfg"], megapixels=panel[0] * panel[1] / 1e6,
                                 size=panel, negative=pr["negative"], cancel=job.cancel)
        if used.get("engine_note"):
            job.cache["engine_note"] = used["engine_note"]
        final, info = (png, {}) if view == "portrait" else align_panel(png, panel)
        if style.get("monochrome"):                      # see keying.grey_image
            final = grey_png(final)
        self.lib.archive_picture(job.npc_id, f"sheet/{view}")          # (the view this one replaces stays among its earlier versions)
        used_models = [licences.picture_engine(used, edit=True)]
        self.lib.write_asset(job.npc_id, f"sheet/raw/{view}.png", media.label(png, used_models))
        self.lib.write_asset(job.npc_id, f"sheet/{view}.png", media.label(final, used_models))
        source_sprite_created = npc.get("assets", {}).get("sprite", {}).get("created") if view in FULL_BODY else None

        def apply(n):
            slot = n["assets"].setdefault("sheet", {"views": {}})
            slot.setdefault("views", {})[view] = {
                "file": f"sheet/{view}.png", "raw": f"sheet/raw/{view}.png", "seed": view_seed(seed, view), "prompt": pr["positive"],
                "negative": pr["negative"], "created": now(), "size": list(panel), "from": kind, "pose": pose, **info,
                **({"engine": used["engine"]} if used.get("engine") else {})}
            if view in FULL_BODY:
                slot["views"][view]["source_sprite_created"] = source_sprite_created
            slot["views"][view].pop("stale", None)
            model = n["assets"].get("model_3d", {})
            if model.get("engine_id") in three_d.FOUR_VIEW_GENERATORS and view in FULL_BODY:
                model["stale"] = True
        self.lib.update(job.npc_id, apply)

    def stitch(self, job, npc, data):
        """Stitch the views that exist into the sheet and measure how well their colours hold to the sprite."""
        assets = self.lib.get(job.npc_id)["assets"]
        views = assets.get("sheet", {}).get("views", {})
        order = [v for v in VIEWS if v in views]
        if not order:
            raise ComfyError("There are no model-sheet views to stitch yet")
        images = [_rgb(self.lib.read_asset(job.npc_id, views[v]["file"])) for v in order]
        if len({im.size for im in images}) > 1:            # views drawn at different quality settings
            w, h = images[0].size
            images = [im if im.size == (w, h) else im.resize((w, h), Image.LANCZOS) for im in images]
        sheet = stitch(images)
        used_models = [licences.picture_engine(views[v], edit=True) for v in order]
        self.lib.write_asset(job.npc_id, "sheet/sheet.png", media.png(sheet, models=used_models, optimize=True))

        report, flags = {}, []
        sprite = assets.get("sprite")
        if sprite:
            pal = anim.palette(Image.open(io.BytesIO(self.lib.read_asset(job.npc_id, sprite["file"]))).convert("RGBA"))
            for v in order:
                if v == "portrait":
                    continue
                match = round(colour_match(self.lib.read_asset(job.npc_id, views[v]["file"]), pal), 3)
                report[v] = {"colour_match": match, "height": views[v].get("height")}
                if match < MIN_COLOUR_MATCH:
                    flags.append(f"{data['sheet']['views'][v]['label']}: the colours differ a lot from the sprite "
                                 f"(match {match:.2f}); re-roll this view")
        complete = set(job.options.get("sheet", {}).get("views", ())) >= set(VIEWS)

        def apply(n):
            slot = n["assets"]["sheet"]
            slot.update(file="sheet/sheet.png", size=list(sheet.size), panel=list(images[0].size), order=order, created=now(),
                        style=npc["style"], pose=job.options.get("sheet", {}).get("pose"), report={"views": report, "flags": flags, "ok": not flags})
            if complete:                                    # a single re-rolled view does not make old ones current
                slot.pop("stale", None)
        self.lib.update(job.npc_id, apply)
