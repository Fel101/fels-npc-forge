"""Smooth out the rings some image-to-3D models sculpt around the eyes.

Pixal3D reads dark eye make-up as shape. On Clara (2026-09-26) each eye became a structure of its own: slices at eye
height show a closed loop of surface about 1.3 cm across, with walls running back into the head, which from the front
looks like a thick ring or a pair of goggles. Smoothing the whole mesh barely touches it (eight rounds of Taubin
smoothing) and anything stronger melts the nose and lips, so the fix works on the eyes only:

1. Find the eyes. A vision model in LM Studio marks them on the front picture's head (measured on Clara's cartoon eyes
   and Imani's and Devi's realistic ones: qwen3.6-35b-a3b and qwen3.8-27b agreed to within half a percent of the
   picture; gemma-4-e4b misplaced two of the three). The boxes are placed on the model with the front view's own fit
   (npcgen/paint3d.view_fit).
2. Fit the face around each eye: a smooth surface fitted to the front of the face in a band around the eye, with
   anything well in front of it (hair) left out of the fit.
3. If the eye region stands off that surface by more than a normal eye does, press it onto the surface: every point in
   front of the face within the eye region moves to it, blending out to the untouched face around it. The surfaces
   that were the ring's hidden walls end up flat and facing away; they are removed. The pictures then paint the eyes onto
   the smooth surface, the way game faces usually carry them.

Only when asked (the clean-up's "Smooth eye rings" option), and only for models that have the rings: it flattens
normal eyes too (Imani's and Devi's sculpted eyelids would be lost), and no measure of the shape tried here told the two
apart (distance from a fitted face, layers of surface, how far the region stands out). It needs LM Studio with a vision
model.
"""
import base64
import io
import json
import re
import time
import urllib.error
import urllib.request

import numpy as np
from PIL import Image

from . import gpu, headdetail
from .comfy import ComfyError
from .paint3d import Glb, _vertex_normals, rasterize, textured_primitives, view_fit

LMSTUDIO_URL = "http://127.0.0.1:1234"
VISION_MODELS = ("qwen/qwen3.6-35b-a3b", "qwen/qwen3.8-27b")   # measured, best first ("vision_model" in config.json overrides)
CONTEXT_LENGTH = 4096       # one picture and a short answer; LM Studio's default context would reserve far more memory
CROP_PIXELS = 768           # the head crop is shown to the model this size
LOAD_TIMEOUT = 600
ANSWER_TIMEOUT = 300

LATERAL, MEDIAL = 3.0, 1.8   # times the half-width: how far the smoothed region reaches towards the temple / the nose
UP, DOWN = 1.2, 2.4          # times the half-height: how far it reaches up towards the brow / down the cheek
DEPTH_BACK = 0.02    # of the height: how far behind the face surface the eye's structure is gathered in...
DEPTH_FRONT = 0.012  # ...and in front: the seeds. Anything joined to them inside the region counts too, however far it
REACH = 0.035        # stands out (the rings' loops), up to this far in front or behind; hair, not joined there, stays
BAND = 1.5           # the face is fitted between the region's edge and this many times its size
RELAX_ROUNDS = 300
GRID = 96            # depth-map cells across each eye region
CHECK_SECONDS = 30   # how long the page's "is LM Studio there" answer is kept

_clock = time.monotonic       # tests replace this
_CHECKED = {}

PROMPT = ("This is a close-up of a character's head, seen from the front. Find the character's two eyes. "
          "Answer with JSON only, no other text: {\"eyes\": [{\"side\": \"picture-left\", \"box\": [x0, y0, x1, y1]}, "
          "{\"side\": \"picture-right\", \"box\": [x0, y0, x1, y1]}]} where each box tightly surrounds one eye (the eyeball "
          "and eyelids, not the eyebrow), in coordinates from 0 to 1000 across the picture's width and height.")


class EyeError(ComfyError):
    """The eyes could not be found or smoothed. The message is safe to show."""


# ---------------------------------------------------------------- LM Studio

def _url(cfg):
    return str((cfg or {}).get("lmstudio_url") or LMSTUDIO_URL).rstrip("/")


def _call(cfg, path, body=None, timeout=30):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(_url(cfg) + path, data, {"Content-Type": "application/json"},
                                 method="GET" if body is None else "POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise EyeError(f"LM Studio refused {path}: {e.read().decode('utf-8', 'replace')[:300]}") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise EyeError(f"Cannot reach LM Studio at {_url(cfg)}: start it, with its local server on") from None


def _models(cfg):
    return _call(cfg, "/api/v1/models", timeout=10).get("models", [])


def vision_model(cfg, models=None, choices=VISION_MODELS):
    """The LM Studio model to ask: config.json's "vision_model", else the first of `choices` installed; None if none."""
    models = _models(cfg) if models is None else models
    have = {m.get("key") for m in models if (m.get("capabilities") or {}).get("vision")}
    wanted = (cfg or {}).get("vision_model")
    return next((k for k in ((wanted,) if wanted else ()) + tuple(choices) if k in have), None)


def availability(cfg, choices=VISION_MODELS):
    """{"model": key or None, "missing": [...]} for the page; asked of LM Studio at most every CHECK_SECONDS."""
    key = (_url(cfg), (cfg or {}).get("vision_model"), tuple(choices))
    cached = _CHECKED.get(key)
    if cached and _clock() - cached[0] < CHECK_SECONDS:
        return cached[1]
    try:
        model = vision_model(cfg, choices=choices)
        result = {"model": model, "missing": [] if model else
                  ["a vision model in LM Studio (" + " or ".join(choices) + ")"]}
    except EyeError:
        result = {"model": None, "missing": ["LM Studio, running with its local server on"]}
    _CHECKED[key] = (_clock(), result)
    return result


def parse_boxes(text):
    """The two eye boxes (0-1000, picture-left first) from the model's answer; EyeError if it does not make sense."""
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S)
    match = re.search(r"\{.*\}", text, re.S)
    try:
        boxes = [[float(v) for v in e["box"]] for e in json.loads(match.group(0))["eyes"]]
    except (AttributeError, KeyError, TypeError, ValueError):
        raise EyeError("The vision model did not answer with the eyes' positions") from None
    if len(boxes) != 2 or any(len(b) != 4 for b in boxes):
        raise EyeError(f"The vision model found {len(boxes)} eyes, not two")
    boxes.sort(key=lambda b: b[0] + b[2])
    (l0, t0, l1, b0), (r0, t1, r1, b1) = boxes
    widths, heights = (l1 - l0, r1 - r0), (b0 - t0, b1 - t1)
    sane = (all(0 <= v <= 1000 for b in boxes for v in b) and min(widths) >= 15 and min(heights) >= 10
            and max(widths) <= 400 and max(heights) <= 400 and max(widths) <= 2 * min(widths)
            and l1 <= r0 + 0.2 * min(widths)
            and abs((t0 + b0) - (t1 + b1)) / 2 <= max(heights))
    if not sane:
        raise EyeError(f"The eyes the vision model found do not look like a pair: {boxes}")
    return boxes


def converse(cfg, questions, comfy=None, cancel=None, choices=VISION_MODELS, context=CONTEXT_LENGTH, max_tokens=4000,
             needed_for="Smoothing eye rings"):
    """([answer text] for each (PNG bytes, prompt) in `questions`, model key), all from one LM Studio vision model. One model
    on the GPU at a time: ComfyUI is emptied first, and the vision model is unloaded again afterwards unless it was
    already loaded."""
    models = _models(cfg)
    model = vision_model(cfg, models, choices)
    if not model:
        raise EyeError(f"{needed_for} needs a vision model in LM Studio: " + " or ".join(choices))
    loaded = next((m.get("loaded_instances") or [] for m in models if m.get("key") == model), [])
    key = ("lmstudio", model)

    def run():
        instance = loaded[0]["id"] if loaded else None
        if instance is None:
            gpu.claim(key, comfy=comfy, cancel=cancel)
            instance = _call(cfg, "/api/v1/models/load", {"model": model, "context_length": context},
                             timeout=LOAD_TIMEOUT).get("instance_id", model)
        try:
            answers = []
            for png, prompt in questions:
                if cancel is not None and cancel.is_set():
                    raise EyeError("Stopped")
                body = {"model": instance, "temperature": 0, "max_tokens": max_tokens, "messages": [{"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(png).decode()}},
                    {"type": "text", "text": prompt}]}]}
                answer = _call(cfg, "/v1/chat/completions", body, timeout=ANSWER_TIMEOUT)
                answers.append(answer["choices"][0]["message"]["content"])
            return answers
        finally:
            if not loaded:
                try:
                    _call(cfg, "/api/v1/models/unload", {"instance_id": instance}, timeout=60)
                finally:
                    gpu.drop(key)

    if comfy is None:
        return run(), model
    with comfy.operation_lock.hold(cancel):              # no picture or voice starts on the GPU meanwhile
        return run(), model


def ask(cfg, png, comfy=None, cancel=None):
    """(eye boxes, model key) for a front head picture (PNG bytes), from LM Studio."""
    (answer,), model = converse(cfg, [(png, PROMPT)], comfy, cancel)
    return parse_boxes(answer), model


def locate(cfg, model_glb, front_png, comfy=None, cancel=None, boxes=None):
    """([(x, y, rx, ry)] of the two eyes on the model (centre and half-size, model units), {"boxes": the boxes found
    (0-1000 on the head crop), "model": the vision model asked}). `boxes` skips asking LM Studio."""
    head = headdetail.head_box(front_png)
    side = head[2] - head[0]
    model = None
    if boxes is None:
        crop = Image.open(io.BytesIO(headdetail.crop(front_png, head))).resize((CROP_PIXELS, CROP_PIXELS), Image.BICUBIC)
        buf = io.BytesIO()
        crop.save(buf, "PNG")
        boxes, model = ask(cfg, buf.getvalue(), comfy, cancel)
    pos, _nrm, _uv, tri, _img = textured_primitives(Glb(model_glb))[0]
    rgb = np.asarray(Image.open(io.BytesIO(front_png)).convert("RGB"))
    fit, _iou, _head_iou = view_fit(pos, tri, "front", rgb)
    s, tx, ty = fit.head or fit.body
    out = []
    for x0, y0, x1, y1 in boxes:
        px, py = head[0] + (x0 + x1) / 2000 * side, head[1] + (y0 + y1) / 2000 * side
        out.append((float((px - tx) / s), float((ty - py) / s), float((x1 - x0) / 2000 * side / s),
                    float((y1 - y0) / 2000 * side / s)))
    return out, {"boxes": [[round(v) for v in b] for b in boxes], "model": model}


# ---------------------------------------------------------------- smoothing

def _front_depth(pos, tri, x0, x1, y0, y1):
    """Nearest z to a front camera per cell of a GRID x GRID map over [x0, x1] x [y0, y1]; -inf where empty."""
    xy = np.stack([(pos[:, 0] - x0) / (x1 - x0) * GRID, (y1 - pos[:, 1]) / (y1 - y0) * GRID], 1)
    pix, t, bary = rasterize(xy, tri, GRID, GRID)
    z = np.einsum("nk,nk->n", bary, pos[:, 2][tri[t]])
    depth = np.full(GRID * GRID, -np.inf)
    np.maximum.at(depth, pix, z)
    return depth.reshape(GRID, GRID)


def _quadratic(x, y):
    return np.stack([np.ones_like(x), x, y, x * x, x * y, y * y], -1)


def face_surface(pos, tri, eye, other_x):
    """Coefficients of a quadratic z(x, y) (around the eye centre) fitted to the front of the face in a band just outside
    the smoothed region (where the face is untouched: the rings lie inside it), leaving out whatever stands well in
    front of it (hair)."""
    ex, ey, rx, ry = eye
    ax, down, up = rx * LATERAL * BAND, ry * DOWN * BAND, ry * UP * BAND
    x0, x1, y0, y1 = ex - ax, ex + ax, ey - down, ey + up
    depth = _front_depth(pos, tri, x0, x1, y0, y1)
    centres = (np.arange(GRID) + 0.5) / GRID
    gx, gy = np.meshgrid(x0 + centres * (x1 - x0), y1 - centres * (y1 - y0))
    r = _region(np.stack([gx.ravel(), gy.ravel()], 1), eye, other_x).reshape(GRID, GRID)
    use = np.isfinite(depth) & (r > 1.05) & (r < BAND)
    if use.sum() < 50:
        raise EyeError("There is too little face around an eye to fit")
    a = _quadratic(gx - ex, gy - ey)
    z = np.where(np.isfinite(depth), depth, 0)
    for _ in range(3):
        coef = np.linalg.lstsq(a[use], z[use], rcond=None)[0]
        res = z - np.einsum("...k,k->...", a, coef)
        use &= res < 3 * np.median(np.abs(res[use])) + 1e-9
    return coef


def _region(pos, eye, other_x):
    """Per vertex: how far into this eye's region it lies (below 1 = inside). The region reaches further towards the temple
    and below the eye, where Clara's rings run, than towards the nose and up to the brow."""
    ex, ey, rx, ry = eye
    u, v = (pos[:, 0] - ex) / rx, (pos[:, 1] - ey) / ry
    lateral = np.sign(ex - other_x) * u > 0
    a = np.where(lateral, LATERAL, MEDIAL)
    b = np.where(v > 0, UP, DOWN)
    return np.sqrt((u / a) ** 2 + (v / b) ** 2)


def _grow(seed, allowed, edges):
    """`seed` spread along the mesh's edges through `allowed` vertices."""
    grown = seed & allowed
    while True:
        reach = np.zeros_like(grown)
        reach[edges[grown[edges[:, 0]], 1]] = True
        new = reach & allowed & ~grown
        if not new.any():
            return grown
        grown |= new


def _weld(pos, height):
    """Vertices at the same place (glTF splits them along texture seams) share one index, so relaxing never tears a seam."""
    keys = np.round(pos / (1e-6 * height)).astype(np.int64)
    _, index = np.unique(keys, axis=0, return_inverse=True)
    return index.ravel()


def smooth(model_glb, eyes, report=None):
    """The GLB with its eye regions relaxed into a smooth membrane pinned to the face around them (see the module notes).
    `eyes` comes from locate(). Only for models with rings: it flattens normal eyes too."""
    glb = Glb(model_glb)
    prims = textured_primitives(glb, with_primitive=True)
    if len(prims) != 1:
        raise EyeError(f"Smoothing the eyes expects one textured mesh; this model has {len(prims)}")
    pos, _nrm, _uv, tri, _img, prim = prims[0]
    raw = glb.accessor(prim["attributes"]["POSITION"]).astype(np.float64)
    if not np.allclose(raw, pos, atol=1e-6):
        raise EyeError("The model's mesh is moved or scaled in its scene; smoothing the eyes expects it in place")
    report = report if report is not None else {}
    height = pos[:, 1].max() - pos[:, 1].min()
    weld = _weld(pos, height)
    count = weld.max() + 1
    point = np.zeros((count, 3))
    np.add.at(point, weld, pos)
    point /= np.bincount(weld, minlength=count)[:, None]
    region = np.zeros(count, bool)
    edges = weld[np.concatenate([tri[:, [0, 1]], tri[:, [1, 2]], tri[:, [2, 0]]])]
    edges = np.concatenate([edges, edges[:, ::-1]])
    for k, eye in enumerate(eyes):
        other_x = eyes[1 - k][0] if len(eyes) == 2 else eye[0] + 1
        coef = face_surface(pos, tri, eye, other_x)
        r = _region(point, eye, other_x)
        vx, vy = point[:, 0] - eye[0], point[:, 1] - eye[1]
        surface = np.einsum("nk,k->n", _quadratic(vx, vy), coef)
        seed = (r < 1) & (point[:, 2] > surface - DEPTH_BACK * height) & (point[:, 2] < surface + DEPTH_FRONT * height)
        mine = _grow(seed, (r < 1) & (np.abs(point[:, 2] - surface) < REACH * height), edges)
        point[mine, 2] = surface[mine]              # a first guess close to the answer, so relaxing needs few rounds
        region |= mine
    # relax: every region vertex moves to the mean of its neighbours; everything outside stays where it is
    edges = edges[region[edges[:, 0]]]
    degree = np.bincount(edges[:, 0], minlength=count).astype(float)
    for _ in range(RELAX_ROUNDS):
        total = np.zeros((count, 3))
        np.add.at(total, edges[:, 0], point[edges[:, 1]])
        point[region] = total[region] / np.maximum(degree[region], 1)[:, None]
    new = point[weld]
    moved = region[weld]
    # the rings' walls end up folded flat into the membrane, some facing away: they would shade dark, so they are turned
    # to face forward like the membrane (removing them instead leaves holes where they were the only surface)
    face_n = np.cross(new[tri[:, 1]] - new[tri[:, 0]], new[tri[:, 2]] - new[tri[:, 0]])
    flip = moved[tri].all(1) & (face_n[:, 2] < 0)
    tri = np.where(flip[:, None], tri[:, ::-1], tri)
    glb.set_triangles(prim, tri)
    glb.set_accessor(prim["attributes"]["POSITION"], new.astype(np.float32))
    if "NORMAL" in prim["attributes"]:
        normals = glb.accessor(prim["attributes"]["NORMAL"]).astype(np.float64)
        fresh = np.zeros((count, 3))
        np.add.at(fresh, weld[tri].ravel(), np.repeat(np.cross(new[tri[:, 1]] - new[tri[:, 0]], new[tri[:, 2]] - new[tri[:, 0]]),
                                                    3, axis=0))
        fresh = fresh[weld]
        fresh /= np.maximum(np.linalg.norm(fresh, axis=1, keepdims=True), 1e-12)
        normals[moved] = fresh[moved]
        glb.set_accessor(prim["attributes"]["NORMAL"], normals.astype(np.float32))
    report["eyes"] = {"vertices_moved": int(moved.sum()), "faces_turned": int(flip.sum())}
    return glb.dumps()
