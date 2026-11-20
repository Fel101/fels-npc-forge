"""Items: what a character holds, wears or finds (weapons, armour, helmets and hats, shoes, bags, props, garments).

An item is made the way a character's 3D model is (npcgen/best3d.py), only the character is left out: a main picture from the
description, four views of it (front, left, back, right) drawn by an Edit model from that picture, a Trellis2 shape from the four
views, and the clean-up that paints the shape from them and makes the game version (npcgen/cleanup3d.py). Measured 2026-10-02 with
a cutlass, a pair of boots and a Nordic helmet (Z-Image Turbo for the picture, Qwen-Image-Edit 2511 for the views): every view
kept the object's shape, proportions and materials, and each shape took about five minutes. For a thing whose top shows (a helmet, a
crate, a boot's opening) two more pictures from 45 degrees up, in front and behind, are drawn for the paint only (the shape does not
need them): the four level views never see the top, which came out patchy.

This file holds what has no GPU in it: the kinds an item can be (data/items.json), the prompts, the request and the steps it
becomes, and the scaling that gives the finished model its real-world size and its origin (a sword's grip, a boot's sole).
npcgen/itemstore.py keeps the items on disk and npcgen/itemmaker.py runs the steps.

Some families are not drawn at all: a family with a `maker` in data/items.json is built from a recipe of measurements instead
(glasses: npcgen/glasses.py), because image-to-3D cannot make a frame a millimetre and a half thick or a lens with no surface.
Those items have a `recipe`, no pictures and no job: saving one builds its model at once, with no GPU. Fitted clothes (npcgen/clothing.py,
the Body Designer's garments) are the second such family: their model on its own is the pattern, and putting one on a body is npcgen/dressing.py.
"""
import json
import secrets

import numpy as np

from . import clothing, config as cfgmod, engines, equip, glasses, roll, three_d
from .paint3d import Glb, all_triangles

DATA_FILE = cfgmod.ROOT / "data" / "items.json"
VIEWS = three_d.PIXAL_SHEET_VIEWS
HIGH_VIEWS = ("front_high", "back_high")    # npcgen/paint3d.py's two raised-camera views: the paint reads them, Trellis2 does not
FACES = (10_000, 30_000, 100_000)           # the game version's size: light, standard, detailed
DEFAULT_FACES = 30_000
SIZE_RANGE = (0.02, 5.0)                    # metres, the longest side
NAME_LIMIT, DESCRIPTION_LIMIT = 80, 500
LOOK_DEFAULT = "realistic"
FIT_NODE = "item_fit"                       # the node the scaling lives in (fitting again changes it, never adds a second one)
GRIP_BAND = 0.04                            # of the height: the vertices this near the grip height decide where the hand goes
# What "Best quality" asks of the engines. Z-Image Turbo drew the cleanest studio pictures (8 s) and Qwen-Image-Edit 2511 kept every
# view of the same object consistent. An engine that is not installed is replaced by the built-in one with a note on the step.
BEST_ENGINES = {"image_model": "zimage", "edit_model": "qwen2511"}
STEPS = (("item_hero", "hero", "Main picture"), ("item_views", "views", "Four views"), ("item_shape", "shape", "3D shape"),
         ("item_high", "high", "High-angle views"), ("item_finish", "finish", "Paint and game version"))
STEP_KINDS = tuple(kind for kind, _name, _label in STEPS)
STEP_NAMES = {name: kind for kind, name, _label in STEPS}
MAKERS = {"glasses": glasses, "garment": clothing}   # the modules that build a recipe family's things: clean_recipe, for_item, from_description, build, model_meta, catalogue


def load():
    """The item tables, read fresh so edits to data/items.json apply without restarting the server."""
    return json.loads(DATA_FILE.read_text("utf-8"))


def catalogue():
    """What the Items tab offers, for /api/config."""
    data = load()
    families = [{"id": fid, "label": fam["label"], "blurb": fam["blurb"], "maker": fam.get("maker") or "photo",
                 "kinds": [{"id": kid, "label": kind["label"], "hint": kind["hint"], "pose": kind["pose"], "size_m": kind["size_m"],
                            "pivot": kind["pivot"], "attach": kind.get("attach", ""), "high": wants_high(fam, kind),
                            **({"preset": kind.get("preset"), "recipe": kind.get("recipe") or {}} if fam.get("maker") else {}),
                            **({"equip": equip_info(kid, kind)} if not fam.get("maker") else {})}
                           for kid, kind in fam["kinds"].items()]}
                for fid, fam in data["families"].items() if fam.get("maker") != "garment" or clothing.available()]       # (fitted clothes are the Body Designer's: without it there are none)
    return {"families": families, "looks": [{"id": lid, "label": look["label"]} for lid, look in data["looks"].items()],
            "faces": [{"value": n, "label": f"{label} ({n:,} faces)"} for n, label in zip(FACES, ("Light", "Standard", "Detailed"))],
            "default_faces": DEFAULT_FACES, "size_range": list(SIZE_RANGE), "best": dict(BEST_ENGINES),
            "steps": [{"name": name, "label": label} for _kind, name, label in STEPS], "glasses": glasses.catalogue(), "clothing": clothing.catalogue()}


def equip_info(kind_id, spec):
    """How a kind is put on a character (npcgen/equip.py): {"carry", "slot", "slots"}, or None when equipping it is not done yet."""
    how = equip.support(kind_id, spec)
    return None if how is None else {**how, "slots": list(equip.slots_for(how["carry"]))}


def maker(family_id, data=None):
    """How a family's things are made: "photo" (a picture, four views, a Trellis2 shape, paint) or the name of a recipe maker ("glasses")."""
    return ((data or load())["families"].get(family_id) or {}).get("maker") or "photo"


def is_recipe(item, data=None):
    """Whether an item is built from a recipe (no pictures, no job)."""
    return maker(item.get("family"), data) != "photo"


def recipe_maker(family_id, data=None):
    """The module that builds the things of a family made from a recipe (glasses: npcgen/glasses.py, fitted clothes: npcgen/clothing.py), or None for a family drawn from pictures."""
    return MAKERS.get(maker(family_id, data))


def wants_high(family, kind):
    """Whether a kind of thing has its two high-angle views by default: the kind says (`high`), else its family does."""
    return bool(kind.get("high", family.get("high", False)))


def kind_spec(item, data=None):
    """The table entry of an item's kind. An item whose kind was taken out of the table since gets the plain 'other' one."""
    data = data or load()
    fam = data["families"].get(item.get("family")) or {}
    return fam.get("kinds", {}).get(item.get("kind")) or data["families"]["prop"]["kinds"]["other"]


# ---- what the editor sends ---------------------------------------------------------------------------------------------------
def _text(raw, key, limit, label):
    value = raw.get(key, "")
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise ValueError(f"{label} must be text")
    value = " ".join(value.split())
    if len(value) > limit:
        raise ValueError(f"{label} must be at most {limit} characters")
    return value


def _number(raw, key, label, low, high, default):
    value = raw.get(key)
    if value in (None, ""):
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError(f"{label} must be a number")
    try:
        number = float(value)
    except ValueError:
        raise ValueError(f"{label} must be a number") from None
    if not low <= number <= high:                     # (also false for NaN)
        raise ValueError(f"{label} must be between {low:g} and {high:g}")
    return number


def clean_item(raw):
    """The fields of an item from what the editor sent: {name, family, kind, description, setting, look, size_m, faces, high_views}, and
    for a family that is built from a recipe the complete `recipe` too. Anything else (the id, the assets, the dates) belongs to the
    store and is ignored."""
    if not isinstance(raw, dict):
        raise ValueError("The item must be an object")
    data = load()
    family = raw.get("family") or next(iter(data["families"]))
    if family not in data["families"]:
        raise ValueError(f"Unknown family: {family}")
    kinds = data["families"][family]["kinds"]
    kind = raw.get("kind") or next(iter(kinds))
    if kind not in kinds:
        raise ValueError(f"Unknown kind for {data['families'][family]['label']}: {kind}")
    spec = kinds[kind]
    description = _text(raw, "description", DESCRIPTION_LIMIT, "The description")
    name = _text(raw, "name", NAME_LIMIT, "The name")
    if kind == "other" and not (name or description):
        raise ValueError("Name what this is (or describe it): it is not one of the listed kinds")
    setting = raw.get("setting") or ""
    if setting and (not isinstance(setting, str) or setting not in roll.load_data()["settings"]):
        raise ValueError("Unknown setting")
    look = raw.get("look") or LOOK_DEFAULT
    if look not in data["looks"]:
        raise ValueError(f"Unknown look: {look}")
    size = round(_number(raw, "size_m", "The size in metres", *SIZE_RANGE, spec["size_m"]), 3)
    faces = int(_number(raw, "faces", "The game version's size", 1, 10 ** 7, DEFAULT_FACES))
    if faces not in FACES:
        raise ValueError("The game version's size must be one of " + ", ".join(f"{n:,}" for n in FACES) + " faces")
    high = raw.get("high_views")
    if high in (None, ""):
        high = wants_high(data["families"][family], spec)               # not said: what the kind usually wants
    elif not isinstance(high, bool):
        raise ValueError("high_views must be true or false")
    fields = {"name": name or default_name(kind, spec, description), "family": family, "kind": kind, "description": description,
              "setting": setting, "look": look, "size_m": size, "faces": faces, "high_views": high}
    made = recipe_maker(family, data)
    if made is not None:
        fields.update(setting="", look=LOOK_DEFAULT, high_views=False, recipe=made.for_item(raw.get("recipe"), kind, spec))
    elif raw.get("recipe"):
        raise ValueError(f"{data['families'][family]['label']} are made from pictures: they have no recipe")
    return fields


def default_name(kind, spec, description):
    """A name for an item that was not given one: the first words of the description, else the kind's own name."""
    words = description.split()
    if kind == "other" and words:
        return " ".join(words[:4]).capitalize()
    return spec["label"]


# ---- prompts -------------------------------------------------------------------------------------------------------------------
def _article(phrase):
    return "an" if phrase[:1].lower() in "aeiou" else "a"


def _noun(item, spec):
    """How the prompts call the thing: the kind's noun; for 'other' the name the user gave it."""
    return spec["noun"] if item.get("kind") != "other" else (item.get("name") or "object")


def subject(item, data=None):
    """'a cutlass: a curved single-edged blade...': the kind, then the user's words (the kind's own description when there are none),
    then the setting it is made for."""
    data = data or load()
    spec = kind_spec(item, data)
    noun = _noun(item, spec)
    detail = (item.get("description") or "").strip().rstrip(".") or spec["hint"]
    text = f"{_article(noun)} {noun}" + (f": {detail}" if detail else "")
    setting = (roll.load_data()["settings"].get(item.get("setting") or "") or {}).get("label")
    if setting:
        text += f", designed for {_article(setting)} {setting.lower()} setting"
    return text


def _negative(data, pose, look):
    terms = [t for t in data["prompts"]["hero_negative"].split(", ") if not (pose == "ghost" and t == "mannequin")]
    return ", ".join([*terms, *[t for t in (data["poses"][pose]["negative"], data["looks"][look]["negative"]) if t]])


def hero_prompt(item):
    """{"positive", "negative"} for the main picture: a studio shot of the single object on a plain white background."""
    data = load()
    spec = kind_spec(item, data)
    pose = data["poses"][spec["pose"]]
    look_id = item.get("look") if item.get("look") in data["looks"] else LOOK_DEFAULT
    positive = data["prompts"]["hero"].format(subject=subject(item, data), pose=pose["hero"], thing=pose["thing"],
                                              look=data["looks"][look_id]["phrase"])
    return {"positive": positive, "negative": _negative(data, spec["pose"], look_id)}


def view_prompt(item, view):
    """{"positive", "negative"} for one of the four views (or a high-angle one), an Edit model's instruction about the main picture."""
    data = load()
    spec = kind_spec(item, data)
    pose = data["poses"][spec["pose"]]
    front = spec.get("front") or ""
    # a thing with a front is told which way it points in the side views; the high-angle views have their own wording
    where = data["views_high"].get(view) or (data["views_facing"].get(view) if front else None)
    template = data["prompts"]["view_front" if front else "view"]      # (the plain one is what the first Items runs used)
    positive = template.format(noun=_noun(item, spec), where=where or data["views"][view], thing=pose["thing"],
                               front=f" {front[:1].upper()}{front[1:]}." if front else "")
    look_id = item.get("look") if item.get("look") in data["looks"] else LOOK_DEFAULT
    return {"positive": positive, "negative": _negative(data, spec["pose"], look_id)}


# ---- the request and its steps ---------------------------------------------------------------------------------------------
def clean_request(raw):
    """What the Make buttons send: {"make": bool, "finish": bool, "redo": [step names]}. `make` makes what is missing or out of date
    (and everything after it), `finish` is whether that includes painting the shape and making the game version, `redo` makes the
    named steps again whatever state they are in."""
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError("The request must be an object")
    redo = raw.get("redo") or []
    if not isinstance(redo, list) or any(not isinstance(n, str) or n not in STEP_NAMES for n in redo):
        raise ValueError("redo must list some of " + ", ".join(STEP_NAMES))
    return {"make": raw.get("make") is True, "finish": raw.get("finish", True) is not False, "redo": [n for n in STEP_NAMES if n in redo]}


def state(item):
    """Which of the steps an item has, fresh: {step kind: True/False}. A step is fresh when its result exists and nothing it was
    made from has changed since (the store marks results `stale` when the description or an earlier step changes). The high-angle views
    only count when the item asks for them, and then the paint is fresh only if it used them."""
    assets = item.get("assets") or {}
    hero, views, model = assets.get("hero") or {}, assets.get("views") or {}, assets.get("model_3d") or {}
    high = assets.get("high") or {}
    fresh = lambda meta: bool(meta.get("file")) and not meta.get("stale")           # noqa: E731
    out = {"item_hero": fresh(hero), "item_views": all(fresh(views.get(v) or {}) for v in VIEWS), "item_shape": fresh(model)}
    out["item_high"] = not item.get("high_views") or all(fresh(high.get(v) or {}) for v in HIGH_VIEWS)
    refined = model.get("refined") or {}
    used = set(refined.get("painted_from") or ())
    out["item_finish"] = (out["item_shape"] and bool(refined.get("created")) and not model.get("game_stale")
                          and (not item.get("high_views") or set(HIGH_VIEWS) <= used))
    return out


def exists(item):
    """Which steps have a result at all (stale or not): what a later step can start from."""
    assets = item.get("assets") or {}
    views = assets.get("views") or {}
    high = assets.get("high") or {}
    return {"item_hero": bool((assets.get("hero") or {}).get("file")), "item_views": all((views.get(v) or {}).get("file") for v in VIEWS),
            "item_shape": bool((assets.get("model_3d") or {}).get("file")), "item_high": all((high.get(v) or {}).get("file") for v in HIGH_VIEWS)}


def plan(item, request):
    """The ordered steps for a request. A step that has nothing to start from brings the one before it along (the views need a main
    picture, the high-angle views need the main picture, the shape needs the four views, the finish needs a shape)."""
    request = clean_request(request)
    if is_recipe(item):
        raise ValueError(f"{kind_spec(item)['label']} are built from their settings when they are saved: there is nothing to queue")
    fresh, have = state(item), exists(item)
    wanted = {kind: False for kind in STEP_KINDS}
    if request["make"]:
        later = False
        for kind in STEP_KINDS:
            if (kind == "item_finish" and not request["finish"]) or (kind == "item_high" and not item.get("high_views")):
                continue
            later = later or not fresh[kind]
            wanted[kind] = later
    for name in request["redo"]:
        wanted[STEP_NAMES[name]] = True
    for before, after in (("item_shape", "item_finish"), ("item_views", "item_shape"), ("item_hero", "item_views"),
                          ("item_hero", "item_high")):                                           # last first, so it carries back
        if wanted[after] and not have[before]:
            wanted[before] = True
    steps = [{"kind": kind, "key": name, "label": label, "status": "pending", "error": None}
             for kind, name, label in STEPS if wanted[kind]]
    if not steps:
        raise ValueError("There is nothing to make: the item is complete and up to date. Use a Redo button to make a part again.")
    return steps


def _default_engine(cfg, key):
    """The engine the app draws with when a request names none: jobs.DEFAULT_OPTIONS (Qwen-Image 2512 and Qwen-Image-Edit 2511) over config.json's "options"."""
    from .jobs import DEFAULT_OPTIONS                       # (late: jobs imports this module)
    return ((cfg or {}).get("options") or {}).get(key) or DEFAULT_OPTIONS[key]


def clean_options(raw, cfg):
    """The job's options from what the editor sent: {best, quality, image_model, edit_model, three_d_quality, reroll, seed}. Without `best`, an engine the
    editor names none for is the app's default one."""
    raw = raw if isinstance(raw, dict) else {}
    best = raw.get("best", True) is not False
    quality = raw.get("quality") if raw.get("quality") in cfg["quality"] else "standard"
    opt = {"best": best, "quality": quality, "three_d_quality": three_d.clean_quality(raw.get("three_d_quality", "medium")),
           "image_model": engines.clean(raw.get("image_model") or _default_engine(cfg, "image_model"), "image"),
           "edit_model": engines.clean(raw.get("edit_model") or _default_engine(cfg, "edit_model"), "edit"),
           "reroll": raw.get("reroll") is True, "seed": None}
    if best:
        opt.update(BEST_ENGINES)
    if raw.get("seed") not in (None, ""):
        try:
            if isinstance(raw["seed"], bool):
                raise ValueError
            opt["seed"] = int(raw["seed"]) % (2 ** 32)
        except (TypeError, ValueError):
            raise ValueError("Seed must be a whole number") from None
    return opt


def availability(status):
    """{"missing": [...]} for the steps, from the health report's own entries (server.py): ComfyUI, the Trellis2 nodes and models, and
    Blender (the game version)."""
    missing = []
    if not status.get("online"):
        missing.append("ComfyUI")
    for part in ((status.get("three_d_missing") or {}).get("trellis2"), (status.get("rig_3d") or {}).get("missing")):
        missing += [m for m in part or [] if m not in missing]
    return {"missing": missing}


# ---- size and origin -------------------------------------------------------------------------------------------------------------
def fit_glb(model_glb, size_m, pivot="base", grip_at=None):
    """The model scaled so its longest side is `size_m` metres, with its origin at `pivot`: "base" (the middle of the bottom), "centre"
    or "grip" (`grip_at` of the way up, 0 the bottom and 1 the top, in the middle of the model there). It is one parent node over
    the scene, so the mesh and its textures stay as they are and fitting again (another size) changes that node instead of adding
    one. The model stands on +Y and faces +Z, as the characters do. Returns (GLB bytes, {"size_m": [x, y, z], "scale", "origin"})."""
    glb = Glb(model_glb)
    doc = glb.json
    nodes = doc.setdefault("nodes", [])
    scenes = doc.setdefault("scenes", [{"nodes": list(range(len(nodes)))}])
    scene = scenes[doc.get("scene", 0)]
    roots = scene["nodes"]
    wrapper = nodes[roots[0]] if len(roots) == 1 and nodes[roots[0]].get("name") == FIT_NODE else None
    if wrapper is None:
        nodes.append({"name": FIT_NODE, "children": list(roots)})
        wrapper = nodes[-1]
        scene["nodes"] = [len(nodes) - 1]
    for key in ("matrix", "scale", "translation", "rotation"):
        wrapper.pop(key, None)
    pos, _tri = all_triangles(glb)
    if not len(pos):
        raise ValueError("The model has no mesh to scale")
    low, high = pos.min(0), pos.max(0)
    extent = high - low
    if not extent.max() > 0:
        raise ValueError("The model has no size")
    scale = float(size_m) / float(extent.max())
    middle = (low + high) / 2
    if pivot == "grip" and grip_at is not None:
        height = low[1] + float(grip_at) * extent[1]
        near = pos[np.abs(pos[:, 1] - height) <= GRIP_BAND * max(extent[1], 1e-9)]
        around = near[:, [0, 2]].mean(0) if len(near) else middle[[0, 2]]
        origin = np.array([around[0], height, around[1]])
    elif pivot == "centre":
        origin = middle
    else:
        origin = np.array([middle[0], low[1], middle[2]])
    wrapper["scale"] = [scale, scale, scale]
    wrapper["translation"] = [float(v) for v in -origin * scale]
    return glb.dumps(), {"size_m": [round(float(v) * scale, 4) for v in extent], "scale": round(scale, 6),
                         "origin": pivot if pivot in ("centre", "grip") else "base",
                         **({"grip_at": float(grip_at)} if pivot == "grip" and grip_at is not None else {})}


def new_seed():
    return secrets.randbelow(2 ** 32)
