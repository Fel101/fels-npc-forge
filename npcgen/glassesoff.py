"""Take the glasses off the pictures a 3D model is made from, for the 3D model only.

An image-to-3D generator cannot tell thin frames from shape. Measured 2026-10-02 on Laura (thin round glasses): with the glasses in the
pictures Trellis2 made open frames, and the detailed head attached to that body then showed the arms and the outer corners of the lenses
twice; with the eyes behind them filled with skin (npcgen/eyesoft.py) it made solid lenses, like sunglasses. A model made without them has
neither fault, the eyes can be softened like anyone else's, and the glasses can come back as a part of their own.

So the pictures the shape, the paint and the detailed head are made from are copies with the glasses taken off. An edit model in ComfyUI does
it, on the head only: the head is cut out of the picture, made `ZOOM` times larger (the whole figure's face is only about 100 pixels wide,
too small for an edit model to redraw faithfully), edited, made small again, and just the pixels that changed (the glasses and what the model
redrew right round them) are put back into the original, so everything else in the picture is exactly as it was. The character's own
pictures are never touched; the copies are kept beside them (sheet/no_glasses/) and recorded on the model.

The job's own Edit model does it (Models card). Measured on Laura, both took the glasses off cleanly: Mage-Flow-Edit (built in) redrew
0.6% of the head crop and kept her face as it was, Qwen-Image-Edit 2511 redrew 12% (the eyes too, with liner), whole-picture edits of
either changed the face noticeably, which is why only the head crop is edited and only what changed is put back.
"""
import io

import numpy as np
from PIL import Image, ImageFilter

from . import engines, paint3d, three_d
from .comfy import ComfyError

ENGINE = "mage"                # the edit model when the job names none (engines.EDIT_ENGINES): the built-in one
ZOOM = 3                       # the head crop is made this many times larger for the edit model
THRESHOLD = 28                 # of 255: how different an edited pixel has to be from the original to count as redrawn (the glasses, the eyes)
GROW = 14                      # pixels (at the picture's size) the redrawn area is grown by: a lens rim the edit left faintly behind is within it
FAINT = 6                      # of 255: inside that area, an edited pixel that differs by more than this replaces the original
FEATHER = 3.0                  # pixels: the edge of the replaced area is blurred by this much
MIN_REDRAWN = 0.002            # of the head crop: less than this redrawn in the front picture means the glasses are still on (Mage-Flow-Edit: 0.006)
DIR = "sheet/no_glasses"

PROMPT = ("Take the glasses off this character. Keep everything else exactly the same: the same face with the same eyes, the same hair, "
          "the same clothes, the same pose, the same plain background and the same art style.")
NEGATIVE = ("glasses, sunglasses, spectacles, eyeglasses, goggles, monocle, frames, lenses, different face, different hair, different clothes, "
            "extra limbs, text, watermark")


class StripError(ComfyError):
    """The glasses could not be taken off. The message is safe to show."""


def head_box(rgb):
    """(x0, y0, x1, y1): a square round the head of the figure in an RGB picture, from the top of its mask, centred on the top rows."""
    mask = paint3d.foreground_mask(np.asarray(rgb))
    ys, xs = np.nonzero(mask)
    if not len(ys):
        raise StripError("no figure in the picture")
    top, height = int(ys.min()), int(ys.max() - ys.min())
    rows = mask[top:top + max(1, int(0.15 * height))]
    columns = np.nonzero(rows.any(0))[0]
    centre = (columns.min() + columns.max()) / 2
    side = max(32, int(0.30 * height))
    x0, y0 = int(round(centre - side / 2)), int(top - 0.03 * height)
    return x0, y0, x0 + side, y0 + side


def _png(image):
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def blend(original, edited):
    """(picture, redrawn, replaced): the edited head crop (the original's size) laid over the original. The glasses and what the edit redrew
    right round them differ from the original by more than THRESHOLD; the area within GROW pixels of those is where the edited pixels
    that differ at all (more than FAINT) replace the original ones, which also takes away the faint rim of a lens the edit left behind
    (measured: with a plain threshold the lens rims stayed as pale arcs on the cheeks). Everything farther away is the original. `redrawn`
    and `replaced` are the shares of the crop that are the strongly redrawn pixels and the whole replaced area."""
    a, b = np.asarray(original, float), np.asarray(edited, float)
    delta = np.abs(a - b).max(-1)
    seeds = Image.fromarray(((delta > THRESHOLD) * 255).astype(np.uint8))
    near = np.asarray(seeds.filter(ImageFilter.MaxFilter(2 * GROW + 1))) > 0
    core = Image.fromarray(((near & (delta > FAINT)) * 255).astype(np.uint8)).filter(ImageFilter.MaxFilter(3))
    alpha = np.maximum(np.asarray(core, float), np.asarray(core.filter(ImageFilter.GaussianBlur(FEATHER)), float))[..., None] / 255.0   # soft only outwards
    picture = Image.fromarray((b * alpha + a * (1 - alpha)).round().astype(np.uint8))
    return picture, float((delta > THRESHOLD).mean()), float((alpha > 0.5).mean())


def strip_picture(comfy, png, name, seed, steps, cfg, engine=None, cancel=None):
    """(png, info): one picture with the glasses taken off, info = {"redrawn", "replaced": shares of the head crop, "engine": label}."""
    original = Image.open(io.BytesIO(png)).convert("RGB")
    box = head_box(original)
    crop = original.crop(box)
    big = crop.resize((crop.width * ZOOM, crop.height * ZOOM), Image.LANCZOS)
    reference = comfy.upload_image(_png(big), name)
    edited, used = engines.edit(comfy, engine or ENGINE, [reference], PROMPT, seed, steps, cfg, megapixels=big.width * big.height / 1e6,
                                size=big.size, negative=NEGATIVE, like=big.size, cancel=cancel)
    small = Image.open(io.BytesIO(edited)).convert("RGB").resize(crop.size, Image.LANCZOS)
    head, redrawn, replaced = blend(crop, small)
    result = original.copy()
    result.paste(head, box[:2])
    return _png(result), {"redrawn": round(redrawn, 4), "replaced": round(replaced, 4), "engine": used.get("engine"),
                          **({"engine_note": used["engine_note"]} if used.get("engine_note") else {})}


def strip(comfy, pictures, npc_id, seed, steps, cfg, engine=None, cancel=None):
    """({view: png}, report): the four model-sheet pictures (`pictures`, {view: png bytes}) with the glasses taken off. Raises StripError when the
    front picture was hardly redrawn, which means the edit model left the glasses on."""
    out, views = {}, {}
    for view in three_d.PIXAL_SHEET_VIEWS:
        if view not in pictures:
            continue
        out[view], views[view] = strip_picture(comfy, pictures[view], f"{npc_id}_noglasses_{view}.png", seed, steps, cfg, engine, cancel)
    if views.get("front", {}).get("redrawn", 1) < MIN_REDRAWN:
        raise StripError("the edit model left the glasses on the front picture (it hardly changed)")
    notes = sorted({v["engine_note"] for v in views.values() if v.get("engine_note")})
    return out, {"engine": next((v["engine"] for v in views.values() if v.get("engine")), None), "views": views, **({"notes": notes} if notes else {})}


def pictures_for(read, model_meta, views):
    """{view: png bytes} of the pictures a model is cleaned up and painted from: its glasses-free copies when it was made from them
    (`read` is the library's read_asset for this character), else the model sheet's own views (`views`, sheet.views)."""
    kept = (model_meta or {}).get("glasses_off") or {}
    files = kept.get("pictures") or {}
    out = {}
    for view in three_d.PIXAL_SHEET_VIEWS:
        for path in (files.get(view), (views.get(view) or {}).get("file")):
            if not path:
                continue
            try:
                out[view] = read(path)
                break
            except FileNotFoundError:           # a copy that is gone: the sheet's own picture instead
                continue
    return out


def attach_note(worn, body_bare, head_bare):
    """What to tell about glasses when the detailed head's face is laid over the body's, or "" when there is nothing to say: both made without
    them (nothing to double), or neither (the head's thin frames and the body's chunky ones both show at the arms and the outer corners of
    the lenses), or only one of them (the other's frames show alone)."""
    if not worn or (body_bare and head_bare):
        return ""
    if body_bare:
        return (f"This character wears glasses ({worn}): the model was made without them but the detailed head was drawn with them, so its "
                "frames show on the face alone. Draw the head again with the glasses left off.")
    if head_bare:
        return (f"This character wears glasses ({worn}): the detailed head was drawn without them but the model has them, so their arms and "
                "outer corners show beside the face. Make the model again with the glasses left off.")
    return (f"This character wears glasses ({worn}): the head model's thin frames and the body's chunky ones both show at the arms and the outer "
            "corners of the lenses. Look at the result before relying on it, or make both again with the glasses left off.")
