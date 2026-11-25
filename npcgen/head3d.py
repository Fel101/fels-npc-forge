"""A detailed head: a separate, much finer 3D model of one character's head, made from four close-ups of it.

Why (measured on Natalie, Devi and Laura, 2026-09-29; docs/detailed-head.md): the whole-body Pixal3D run reads a face from
about 125 pixels of a 1024-pixel picture, and its grid puts a voxel about 1.1 mm across on the person. Its faces come out as
masks: the eyes are goggle-like rings or holes, the nose a bump, the lips a ridge. Given only the head, filling the same
1024-pixel frame, Pixal3D builds real eyelids, nostrils, lip contours and ears, with a grid about 3 to 5 times finer, in about
13 minutes (a whole person takes about 25).

What it does, only when asked (the "Detailed head" section of the 3D model card; never automatically):
  1. Four close-ups (front, both sides, back) of the head and neck by the edit model: the front one from the portrait, the
     other three from the front one, so all four agree about the hair and the skin. A neutral expression unless asked
     otherwise (a smile is baked into the geometry, and a jaw bone should open a mouth that starts closed). A portrait that
     shows the whole body (a photo of a standing person) gives the face too few pixels, so the model sheet's front picture,
     its head cut out and upscaled 4x, is the front one's reference instead.
  2. The four are cut out with BiRefNet and framed like Pixal3D's orbit rig with ONE scale for all: Pixal3D reads the
     framing as the camera, and its own crop node scales each picture to its own bounding box, which is right for a tall
     figure (the height decides in every view) and wrong for a bust (width decides in some views, height in others).
  3. Pixal3D multi-view makes the shape (three_d.pixal3d_head_graph), and the hidden inner skin and loose specks are removed.
  4. The colour is painted from the four pictures (headpaint.py) with the rig's own cameras: Pixal3D's colour for a head is
     an orange tan with muddy olive on everything thin, and the pictures have the skin tone, the irises and the lashes.
The head is its own asset (`head_3d`): nothing about the body model changes. Attaching it to the body is a separate step.
"""
import io
import time

import numpy as np
from PIL import Image

from . import cleanup3d, engines, eyesoft, hairmask, headdetail, headpaint, licences, media, prompts, sheet, three_d
from .comfy import ComfyError
from .library import now

VIEWS = three_d.PIXAL_SHEET_VIEWS            # front, side_left, back, side_right: the sheet's own names
PANEL = (1024, 1024)                         # the close-ups' size: Pixal3D reads 1024 x 1024 whatever is given
FRAME_PAD = three_d.RIG_PAD                  # the unit cube fills 1/1.1 of the frame in Pixal3D's rig
# Only the top of each close-up is kept (the head and the neck a little way down; the shoulders go), so the head fills the frame:
# 0.70 of the height of the bust, whose chin measured at 0.50 to 0.55 of it on Natalie, Devi and Laura. Measured on Natalie and
# Devi at the same scale (docs/detailed-head.md): the head model is 1.4 to 1.6 times finer, with separate lashes, thinner
# brows, real ears and a finer hair, and needs no other change.
KEEP = 0.70
EXPRESSIONS = ("neutral", "keep")
ASSET_DIR = "head_3d"                        # the four close-ups: head_3d/<view>.png; framed ones in head_3d/framed/
MODEL_FILE, ORIGINAL_FILE, PREVIEW_FILE = "head_3d.glb", "head_3d_original.glb", "head_3d.png"
# a whole-body picture: its figure is tall and narrow. A head-and-shoulders one is about as wide as it is high (measured on the
# library's portraits: outline width / height 0.66 to 1.0 for busts, 0.3 for the one photo of a standing person).
FULL_BODY_ASPECT = 0.6
FULL_BODY_HEIGHT = 0.85
# Loose pieces smaller than this are dropped. Measured on Laura, Devi and Natalie: the finer grid leaves hair as hundreds of
# detached fragments (Laura 955 pieces under 100 faces, 4% of the faces; Devi 322; Natalie 1), which show as speckle. Real
# separate parts are bigger: her glasses were 750 faces, the eyeballs about 360 each.
SPECK_FACES = 100


class HeadError(ComfyError):
    """The detailed head cannot be made. The message is safe to show."""


# ---- choices ---------------------------------------------------------------------------------------------------------------
def clean_quality(value):
    return three_d.clean_head_quality(value)


def clean_expression(value):
    return value if isinstance(value, str) and value in EXPRESSIONS else "neutral"


def quality_choices():
    return three_d.head_quality_choices()


def expression_choices(spec):
    return [{"id": k, "label": spec["expressions"][k]["label"]} for k in EXPRESSIONS if k in spec.get("expressions", {})]


def view_label(view):
    return view.replace("_", " ")


# ---- prompts ---------------------------------------------------------------------------------------------------------------
def view_prompt(spec, view, style, npc, expression="neutral", glasses_off=False):
    """Edit prompt for one close-up of a character. `spec` is data/head3d.json. glasses_off: the character is drawn without the glasses
    its sheet gives it (npcgen/glassesoff.py: a 3D model made from these has none to double or turn into lenses)."""
    freeform = bool(npc.get("freeform"))
    v = spec["views"][view]
    ex = spec["expressions"].get(clean_expression(expression)) or spec["expressions"][spec["default_expression"]]
    bare = bool(glasses_off and eyesoft.glasses_of(npc))
    body = v["prompt"].format(who=prompts._who(eyesoft.without_glasses(npc) if bare else npc), hair="" if freeform else ", the hair",
                              expression=ex["phrase"])
    if not freeform:
        body += " " + v.get("people_extra", "").format(hair_line=sheet._hair_line(npc))
    if bare and view != "back":
        body += spec["glasses_off"]["phrase"]
    positive = (f"{body.strip()} {prompts.keep_phrase(freeform)}, and keep the exact same {style['edit_name']} art style, "
                f"line style and colour palette. {spec['tail']}")
    terms = ", ".join(t for t in (prompts._edit_negative(style), spec["negative"], ex.get("negative", "") if view != "back" else "",
                                  spec["glasses_off"]["negative"] if bare else "") if t)
    negative = ", ".join(dict.fromkeys(t.strip() for t in terms.split(",") if t.strip()))     # each term once, in order
    return {"positive": positive, "negative": negative}


# ---- the reference picture -----------------------------------------------------------------------------------------------
def is_whole_body(mask):
    """True for a picture of a whole standing figure (a photo of a person, restyled), whose head is too small to draw close-ups
    from, judged from the figure's mask (BiRefNet's; a backdrop that is not flat fooled an outline against the edge colour):
    tall and narrow."""
    box = head_box(mask)
    if box is None:
        return False
    x0, y0, x1, y1 = box
    h, w = y1 - y0, x1 - x0
    return h >= FULL_BODY_HEIGHT * mask.shape[0] and w <= FULL_BODY_ASPECT * h


# ---- framing ---------------------------------------------------------------------------------------------------------------
def mask_graph(image):
    """ComfyUI graph that returns BiRefNet's mask of a picture, as a grey image."""
    return {
        "1": {"class_type": "LoadBackgroundRemovalModel", "inputs": {"bg_removal_name": "birefnet.safetensors"}},
        "2": {"class_type": "LoadImage", "inputs": {"image": image}},
        "3": {"class_type": "RemoveBackground", "inputs": {"bg_removal_model": ["1", 0], "image": ["2", 0]}},
        "4": {"class_type": "MaskToImage", "inputs": {"mask": ["3", 0]}},
        "5": {"class_type": "PreviewImage", "inputs": {"images": ["4", 0]}},
    }


def head_box(mask):
    """(x0, y0, x1, y1) of what a mask (0..1) covers, or None."""
    ys, xs = np.nonzero(mask > 0.5)
    if not len(xs):
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def frame_arrays(pictures, masks, size=1024, pad=FRAME_PAD, keep=1.0):
    """The four close-ups framed like Pixal3D's orbit rig, with one scale for all of them.

    pictures {view: RGB PIL image}, masks {view: float array 0..1, same size, 1 = the head}. Every view is cut out on black and
    cropped to a square whose side is `pad` times the same height (the median height of the four figures, top of the hair to
    the cut at the neck), centred on that view's own bounding box: the unit cube then spans the same length in every view
    and sits in the middle of each. `keep` < 1 first cuts each figure off below that share of its height (the shoulders).
    Returns ({view: RGB uint8 array}, {view: float mask array}, info), all size x size.
    """
    boxes = {}
    masks = dict(masks)
    for view in VIEWS:
        box = head_box(masks[view])
        if box is None:
            raise HeadError(f"Could not find the head in the {view_label(view)} picture, so the four pictures cannot be framed")
        if keep < 1.0:
            masks[view] = masks[view].copy()
            masks[view][int(round(box[1] + keep * (box[3] - box[1]))):] = 0.0
            box = head_box(masks[view])
        boxes[view] = box
    heights = sorted(b[3] - b[1] for b in boxes.values())
    height = (heights[1] + heights[2]) / 2                         # the median of four
    side = max(8, int(round(height * pad)))
    pics, cuts, info = {}, {}, {"height": round(float(height), 1), "side": side, "keep": keep, "views": {}}
    for view in VIEWS:
        x0, y0, x1, y1 = boxes[view]
        left, top = int(round((x0 + x1) / 2 - side / 2)), int(round((y0 + y1) / 2 - side / 2))
        arr = np.asarray(pictures[view].convert("RGB"), np.float32) * masks[view][..., None]      # the head on black
        canvas = np.zeros((side, side, 3), np.float32)
        cut = np.zeros((side, side), np.float32)
        sx0, sy0 = max(0, left), max(0, top)
        sx1, sy1 = min(arr.shape[1], left + side), min(arr.shape[0], top + side)
        if sx1 > sx0 and sy1 > sy0:
            canvas[sy0 - top:sy1 - top, sx0 - left:sx1 - left] = arr[sy0:sy1, sx0:sx1]
            cut[sy0 - top:sy1 - top, sx0 - left:sx1 - left] = masks[view][sy0:sy1, sx0:sx1]
        pics[view] = np.asarray(Image.fromarray(np.clip(canvas, 0, 255).astype(np.uint8)).resize((size, size), Image.LANCZOS))
        cuts[view] = np.asarray(Image.fromarray((cut * 255).astype(np.uint8)).resize((size, size), Image.LANCZOS), np.float32) / 255.0
        info["views"][view] = {"box": list(boxes[view]), "head_height": y1 - y0}
    info["spread"] = round((heights[-1] - heights[0]) / height, 3)   # how unequal the four heads were drawn
    return pics, cuts, info


def png_bytes(array):
    buf = io.BytesIO()
    Image.fromarray(array).save(buf, "PNG")
    return buf.getvalue()


def frame(pictures, masks, size=1024, pad=FRAME_PAD, keep=1.0):
    """frame_arrays, with the pictures as PNG bytes: ({view: PNG bytes}, info)."""
    pics, _cuts, info = frame_arrays(pictures, masks, size, pad, keep)
    return {view: png_bytes(pics[view]) for view in VIEWS}, info


# ---- clean-up --------------------------------------------------------------------------------------------------------------
def remove_specks(model_glb, min_faces=SPECK_FACES, report=None):
    """The GLB without its loose specks: connected pieces of fewer than `min_faces` triangles (vertices, UVs and textures
    untouched, like cleanup3d.remove_inner_skin). What was dropped goes in report["specks"].

    Pieces are found by POSITION, not by vertex number: the textured model has its vertices split along every texture seam,
    so by number each texture island is a piece of its own, and going by number cut holes in the nostrils, around the eyes
    and in the hair of the first head this was run on."""
    from .paint3d import Glb
    glb = Glb(model_glb)
    dropped = pieces = total = 0
    for mesh in glb.json.get("meshes", []):
        for prim in mesh["primitives"]:
            if prim.get("mode", 4) != 4 or "indices" not in prim:
                continue
            tri = glb.accessor(prim["indices"]).reshape(-1, 3).astype(np.int64)
            pos = np.ascontiguousarray(glb.accessor(prim["attributes"]["POSITION"]), np.float32)
            _, welded = np.unique(pos, axis=0, return_inverse=True)        # one number for every place, however many copies
            counts, labels = piece_sizes(welded.reshape(-1)[tri], int(welded.max()) + 1 if len(welded) else 0)
            small = counts[labels] < min_faces
            if small.any():
                glb.set_triangles(prim, tri[~small])
            dropped += int(small.sum())
            pieces += int(len(np.unique(labels[small]))) if small.any() else 0
            total += len(tri)
    if report is not None:
        report["specks"] = {"faces_before": total, "faces_removed": dropped, "pieces_removed": pieces}
    return glb.dumps()


def piece_sizes(tri, vertex_count):
    """(triangles per piece, piece of each triangle) for triangles that share vertices. Union-find over the vertices."""
    parent = np.arange(vertex_count)

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for a, b, c in tri.tolist():
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra
        rc = find(c)
        ra = find(a)
        if ra != rc:
            parent[rc] = ra
    roots = np.array([find(a) for a in tri[:, 0].tolist()]) if len(tri) else np.zeros(0, np.int64)
    ids, labels, counts = np.unique(roots, return_inverse=True, return_counts=True)
    return counts, labels


# ---- availability ----------------------------------------------------------------------------------------------------------
def availability(comfy):
    """{"missing": [...]} for the page: what the detailed head needs that ComfyUI does not have. It needs Pixal3D and
    BiRefNet, but not NPC Forge's own head-texture node (the whole-body run's), so it works without restarting ComfyUI."""
    missing = [m for m in three_d.availability(comfy).get("pixal3d", []) if m != "nodes/" + three_d.HEAD_TEXTURE_NODE]
    return {"missing": missing}


# ---- the job steps ---------------------------------------------------------------------------------------------------------
class Headmaker:
    """Runs the steps of a detailed-head job. `job` needs .npc_id, .options, .cancel (an Event) and .cache (a dict)."""

    def __init__(self, comfy, lib, cfg):
        self.comfy, self.lib, self.cfg = comfy, lib, cfg

    # -- the close-ups
    def _reference(self, job, npc):
        """(name in ComfyUI, what it is) of the picture the close-ups are drawn from, uploaded once per job."""
        cached = job.cache.get("head_ref")
        if cached:
            return cached
        assets = npc.get("assets", {})
        portrait = assets.get("portrait")
        front = assets.get("sheet", {}).get("views", {}).get("front") or {}
        png = kind = None
        if portrait:
            png = self.lib.read_asset(job.npc_id, portrait.get("raw") or portrait["file"])
            kind = "portrait"
            if is_whole_body(self._mask(job, Image.open(io.BytesIO(png)).convert("RGB"), "portrait")):
                if front.get("file"):                               # the head of the model sheet's front picture, upscaled 4x
                    view = self.lib.read_asset(job.npc_id, front["file"])
                    upscaler = headdetail.pick_upscaler(self.comfy, self.cfg.get("face_upscaler"))
                    png = headdetail.closeups(self.comfy, {"front": view}, job.npc_id, upscaler, cancel=job.cancel)["front"]["png"]
                    kind = "model sheet front (the portrait shows the whole body)"
                else:
                    kind = "portrait (it shows the whole body, so the face is small)"
        elif front.get("file"):
            view = self.lib.read_asset(job.npc_id, front["file"])
            upscaler = headdetail.pick_upscaler(self.comfy, self.cfg.get("face_upscaler"))
            png = headdetail.closeups(self.comfy, {"front": view}, job.npc_id, upscaler, cancel=job.cancel)["front"]["png"]
            kind = "model sheet front"
        if png is None:
            raise HeadError("The detailed head is drawn from the portrait: make the portrait first")
        name = self.comfy.upload_image(sheet.fit_reference(png, PANEL), f"{job.npc_id}_head_reference.png")
        job.cache["head_ref"] = (name, kind)
        return job.cache["head_ref"]

    def _front_reference(self, job):
        """(name in ComfyUI, what it is) of the front close-up, uploaded once per job. The side and back pictures are drawn
        from it, not from the portrait: four separate edits of the portrait disagree about the hair, the make-up and the
        mouth, and Pixal3D turns every disagreement into a flap or a lump. Measured on Natalie and Devi (docs/detailed-head.md):
        the heads made from pictures drawn from the front one have smooth, coherent hair and far fewer skin-coloured flaps."""
        cached = job.cache.get("head_front_ref")
        if cached:
            return cached
        try:
            png = self.lib.read_asset(job.npc_id, f"{ASSET_DIR}/front.png")
        except FileNotFoundError:
            raise HeadError("The side and back pictures are drawn from the front picture: make the front picture first") from None
        name = self.comfy.upload_image(sheet.fit_reference(png, PANEL), f"{job.npc_id}_head_front_reference.png")
        job.cache["head_front_ref"] = (name, "front close-up")
        return job.cache["head_front_ref"]

    def make_view(self, job, npc, data, view, seed):
        """One close-up of the head, drawn by the edit model, stored as head_3d/<view>.png. The front one is drawn from the
        portrait, the others from the front one."""
        spec, style = data["head3d"], data["styles"][npc["style"]]
        expression = clean_expression(job.options.get("head_expression"))
        if view == "front":
            job.cache.pop("head_front_ref", None)                 # a new front picture: the others are drawn from that one
            ref_name, reference = self._reference(job, npc)
        else:
            ref_name, reference = self._front_reference(job)
        glasses_off = bool(job.options.get("three_d_glasses_off") and eyesoft.glasses_of(npc))
        pr = view_prompt(spec, view, style, npc, expression, glasses_off)
        quality = self.cfg["quality"].get(job.options.get("quality")) or self.cfg["quality"]["standard"]
        png, used = engines.edit(self.comfy, job.options.get("edit_model"), [ref_name], pr["positive"], sheet.view_seed(seed, view),
                                 quality["sheet_steps"], self.cfg["sheet_cfg"], megapixels=PANEL[0] * PANEL[1] / 1e6, size=PANEL,
                                 negative=pr["negative"], cancel=job.cancel)
        if used.get("engine_note"):
            job.cache["engine_note"] = used["engine_note"]
        self.lib.write_asset(job.npc_id, f"{ASSET_DIR}/{view}.png", media.label(png, [licences.picture_engine(used, edit=True)]))
        meta = {"file": f"{ASSET_DIR}/{view}.png", "seed": sheet.view_seed(seed, view), "prompt": pr["positive"],
                "negative": pr["negative"], "created": now(), "size": list(PANEL), "from": reference, "expression": expression,
                **({"glasses_off": True} if glasses_off else {}),
                **({"engine": used["engine"]} if used.get("engine") else {})}
        portrait_created = (npc.get("assets", {}).get("portrait") or {}).get("created")

        def apply(n):
            slot = n["assets"].setdefault("head_3d", {})
            slot.setdefault("views", {})[view] = meta
            if view == "front":                             # the others are drawn from the front one, so it says what they all are
                slot["glasses_off"] = glasses_off
            slot["source_portrait_created"] = portrait_created
            if slot.get("file"):
                slot["stale"] = True                        # the model there was made from other pictures
        self.lib.update(job.npc_id, apply)

    # -- the model
    def _mask(self, job, picture, label):
        """BiRefNet's mask of a picture (0..1, the picture's own size), made in ComfyUI."""
        buf = io.BytesIO()
        picture.save(buf, "PNG")
        name = self.comfy.upload_image(buf.getvalue(), f"{job.npc_id}_head_{label}_mask.png")
        grey = Image.open(io.BytesIO(self.comfy.run(mask_graph(name), cancel=job.cancel, timeout=300))).convert("L")
        if grey.size != picture.size:
            grey = grey.resize(picture.size, Image.LANCZOS)
        return np.asarray(grey, np.float32) / 255.0

    def _masks(self, job, pictures):
        return {view: self._mask(job, pictures[view], view) for view in VIEWS}

    def make_model(self, job, npc, seed):
        """Frame the four close-ups, run Pixal3D on them, clean the result up and store it."""
        assets = npc.get("assets", {})
        views = (assets.get("head_3d") or {}).get("views") or {}
        pictures = {}
        for view in VIEWS:
            meta = views.get(view) or {}
            if not meta.get("file"):
                raise HeadError(f"The detailed head needs its {view_label(view)} picture: make the pictures first")
            pictures[view] = Image.open(io.BytesIO(self.lib.read_asset(job.npc_id, meta["file"]))).convert("RGB")
        quality = clean_quality(job.options.get("head_quality"))
        masks = self._masks(job, pictures)
        pics, cuts, framing = frame_arrays(pictures, masks, keep=KEEP)
        framed = {view: png_bytes(pics[view]) for view in VIEWS}
        names = {}
        for view in VIEWS:
            self.lib.write_asset(job.npc_id, f"{ASSET_DIR}/framed/{view}.png", media.label(framed[view], [licences.picture_engine(views[view], edit=True)]))
            names[view] = self.comfy.upload_image(framed[view], f"{job.npc_id}_head_framed_{view}.png")
        graph = three_d.pixal3d_head_graph(names, job.npc_id, getattr(job, "id", "head"), seed, quality=quality)
        started = time.monotonic()
        entry = self.comfy.run_entry(graph, cancel=job.cancel, timeout=three_d.head_timeout(quality), unload_models=True)
        model_file = three_d.saved_file(entry, "3d", "GLB model")
        preview_file = three_d.saved_file(entry, "images", "3D preview")
        original = self.comfy.fetch_file(model_file["filename"], model_file.get("subfolder", ""), model_file.get("type", "output"))
        preview = self.comfy.fetch_file(preview_file["filename"], preview_file.get("subfolder", ""), preview_file.get("type", "output"))
        seconds = round(time.monotonic() - started)
        report = {}
        model = remove_specks(cleanup3d.remove_inner_skin(original, report), report=report)
        notes = []
        painted = {}
        find = None
        if job.options.get("head_match_hair"):           # only when asked: it helps where hair falls over the face, and can leave grey patches
            missing = hairmask.availability(self.comfy)["missing"]
            if missing:
                notes.append("the hair was not matched to the pictures: it needs " + " and ".join(missing))
            else:
                find = hairmask.finder(self.comfy, job.npc_id, cancel=job.cancel)      # SAM 3.1 keeps skin off the model's hair
        try:                                             # the colour comes from the four pictures, not from Pixal3D's texture stage
            model = headpaint.paint(model, pics, cuts, painted, find=find)
        except ValueError as exc:
            notes.append(f"painted with Pixal3D's own colours, not from the pictures: {exc}")
        generator = [licences.model_name("three_d:pixal3d")]
        model = media.label(model, [*generator, *(licences.picture_engine(views[v], edit=True) for v in VIEWS)])
        self.lib.write_asset(job.npc_id, ORIGINAL_FILE, media.label(original, generator))
        self.lib.write_asset(job.npc_id, MODEL_FILE, model)
        self.lib.write_asset(job.npc_id, PREVIEW_FILE, media.label(preview, generator))
        settings = three_d.HEAD_QUALITIES[quality]
        expressions = {(views[v] or {}).get("expression") for v in VIEWS} - {None}
        expression = expressions.pop() if len(expressions) == 1 else "mixed"
        reference = (views["front"] or {}).get("from")              # what the front picture was drawn from; the others come from it
        if framing["spread"] > 0.15:
            notes.append("the four pictures drew the head at different sizes (%d%% apart), so the model may be slightly off"
                         % round(framing["spread"] * 100))

        def apply(n):
            slot = n["assets"].setdefault("head_3d", {})
            slot.update(file=MODEL_FILE, raw=ORIGINAL_FILE, preview=PREVIEW_FILE, created=now(), seed=seed, size=len(model),
                        format="glb", textured=True, engine="Pixal3D multi-view, head only", quality=quality,
                        quality_label=settings["label"].split(" · ", 1)[0], generation_seconds=seconds,
                        faces_generated=report["inner_skin"]["faces_before"],
                        faces=report["specks"]["faces_before"] - report["specks"]["faces_removed"],
                        inner_skin_removed=report["inner_skin"]["faces_removed"], specks_removed=report["specks"]["faces_removed"],
                        expression=expression, reference=reference,
                        framing=framing, painted=painted or None,
                        source_portrait_created=slot.get("source_portrait_created"), notes=notes)
            slot.pop("stale", None)
        self.lib.update(job.npc_id, apply)
