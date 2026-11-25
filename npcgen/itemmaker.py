"""The steps of an item's job (npcgen/items.py makes the plan): main picture, four views, Trellis2 shape, two high-angle views (for the paint, when
the item asks for them), paint and game version.

Every step reads the item fresh from the store and writes what it makes back under the store's lock, so an item edited while a job
waits in the queue is made from its newest description. The GPU steps use the same calls as a character's (engines.image,
engines.edit, comfy.run_entry), so the one-model-at-a-time rule (npcgen/gpu.py) covers them without anything extra here.
"""
import random
import time

from . import cleanup3d, engines, items, licences, media, three_d
from .comfy import ComfyError
from .library import now

ENGINE_NOTE = "Drawn with the built-in engine instead: "      # (the same words as npcgen/jobs.py, which imports this file)
SIZE = (1024, 1024)                                           # every item picture: square, so the Edit model keeps the frame
RAW_FILE = "model_3d_original.glb"                            # the generator's own shape, kept for painting it again
SHAPE_TIMEOUT = 3600


def run(worker, job, step):
    """Run one step of an item job on the job worker's thread."""
    store = worker.items
    if store is None:
        raise ComfyError("The item store is unavailable")
    try:
        item = store.get(job.item_id)
    except KeyError:
        raise ComfyError("This item has been deleted") from None
    RUNNERS[step["kind"]](worker, job, step, item)


def _seed(job, item):
    opt = job.options
    if opt.get("seed") is not None:
        return int(opt["seed"])
    return random.randrange(2 ** 32) if opt.get("reroll") else item["seed"]


def _engine_meta(used):
    """What a picture records about its engine: the name of one that is not built in, or why the built-in one drew it instead."""
    return {k: used[k] for k in ("engine", "engine_note") if used.get(k)}


def _note(step, used):
    if used.get("engine_note") and not step.get("note"):
        step["note"] = ENGINE_NOTE + used["engine_note"]


def _quality(worker, job):
    cfg = worker.cfg
    return cfg["quality"].get(job.options.get("quality")) or cfg["quality"]["standard"]


def _views_of(store, item, what):
    """The four views as PNG bytes, or an error saying what to make first."""
    views = (item.get("assets") or {}).get("views") or {}
    missing = [v.replace("_", " ") for v in items.VIEWS if not (views.get(v) or {}).get("file")]
    if missing:
        raise ComfyError(f"{what} needs all four views (missing: {', '.join(missing)}): make them first")
    return {view: store.read_asset(item["id"], views[view]["file"]) for view in items.VIEWS}, views


def _hero(worker, job, step, item):
    cfg, comfy, store = worker.cfg, worker.comfy, worker.items
    quality = _quality(worker, job)
    seed = _seed(job, item)
    prompt = items.hero_prompt(item)
    png, used = engines.image(comfy, job.options.get("image_model"), prompt["positive"], prompt["negative"], SIZE[0], SIZE[1], seed,
                              quality["steps"], cfg["cfg"], job.cancel)
    _note(step, used)
    store.write_asset(item["id"], "hero.png", media.label(png, [licences.picture_engine(used)]))
    meta = {"file": "hero.png", "created": now(), "seed": seed, "prompt": prompt, **_engine_meta(used)}

    def apply(n):
        assets = n.setdefault("assets", {})
        assets["hero"] = meta
        for view in (*(assets.get("views") or {}).values(), *(assets.get("high") or {}).values()):      # drawn from the old picture
            view["stale"] = True
        if assets.get("model_3d"):
            assets["model_3d"]["stale"] = True
    store.update(item["id"], apply)
    store.make_thumb(item["id"])


def _draw(worker, job, step, item, names, folder, offset, what):
    """Draw views of the main picture with the Edit model, into `folder`: {view: what the item records of it}."""
    cfg, comfy, store = worker.cfg, worker.comfy, worker.items
    quality = _quality(worker, job)
    hero_meta = (item.get("assets") or {}).get("hero") or {}
    if not hero_meta.get("file"):
        raise ComfyError(f"The {what} are drawn from the main picture: make it first")
    seed = (_seed(job, item) + offset) % (2 ** 32)
    reference = comfy.upload_image(store.read_asset(item["id"], hero_meta["file"]), f"{item['id']}_hero.png")
    made = {}
    for view in names:
        step["detail"] = view.replace("_", " ")
        job.touch()
        prompt = items.view_prompt(item, view)
        png, used = engines.edit(comfy, job.options.get("edit_model"), reference, prompt["positive"], seed, quality["sheet_steps"],
                                 cfg["sheet_cfg"], megapixels=1.0, size=SIZE, negative=prompt["negative"], like=SIZE, cancel=job.cancel)
        _note(step, used)
        store.write_asset(item["id"], f"{folder}/{view}.png", media.label(png, [licences.picture_engine(used, edit=True)]))
        made[view] = {"file": f"{folder}/{view}.png", "created": now(), "seed": seed, "source_hero_created": hero_meta.get("created"),
                      "prompt": prompt, **_engine_meta(used)}
    step.pop("detail", None)
    return made


def _views(worker, job, step, item):
    made = _draw(worker, job, step, item, items.VIEWS, "views", 1, "four views")

    def apply(n):
        assets = n.setdefault("assets", {})
        assets["views"] = made
        if assets.get("model_3d"):                                 # made from the old views
            assets["model_3d"]["stale"] = True
    worker.items.update(item["id"], apply)


def _high(worker, job, step, item):
    """The two views from 45 degrees up (front and behind): only the paint reads them, so nothing made from the level views goes out of date."""
    made = _draw(worker, job, step, item, items.HIGH_VIEWS, "high", 2, "high-angle views")

    def apply(n):
        n.setdefault("assets", {})["high"] = made
    worker.items.update(item["id"], apply)


def _fit(item, glb):
    spec = items.kind_spec(item)
    try:
        return items.fit_glb(glb, item["size_m"], spec["pivot"], spec.get("grip_at"))
    except ValueError as exc:
        raise ComfyError(f"The model could not be scaled: {exc}") from None


def _shape(worker, job, step, item):
    comfy, store = worker.comfy, worker.items
    views, view_meta = _views_of(store, item, "Trellis2")
    quality = three_d.clean_quality(job.options.get("three_d_quality"))
    seed = _seed(job, item)
    names = {view: comfy.upload_image(png, f"{item['id']}_trellis2_{view}.png") for view, png in views.items()}
    graph = three_d.trellis2_graph(names, item["id"], job.id, seed, quality=quality)
    started = time.monotonic()
    entry = comfy.run_entry(graph, cancel=job.cancel, timeout=SHAPE_TIMEOUT, unload_models=True)
    model_file = three_d.saved_file(entry, "3d", "GLB model", node=three_d.TRELLIS2_OUTPUTS[0])
    preview_file = three_d.saved_file(entry, "images", "3D preview", node=three_d.TRELLIS2_OUTPUTS[1])
    model = comfy.fetch_file(model_file["filename"], model_file.get("subfolder", ""), model_file.get("type", "output"))
    preview = comfy.fetch_file(preview_file["filename"], preview_file.get("subfolder", ""), preview_file.get("type", "output"))
    seconds = round(time.monotonic() - started)
    fitted, fit = _fit(item, model)
    names = [licences.model_name("three_d:trellis2")]
    fitted = media.label(fitted, names)
    store.write_asset(item["id"], RAW_FILE, media.label(model, names))                  # the shape as the generator made it; the finish step paints this one
    store.write_asset(item["id"], "model_3d.glb", fitted)           # the same, scaled to the item's size
    store.write_asset(item["id"], "model_3d_preview.png", media.label(preview, names))
    meta = {"file": "model_3d.glb", "raw": RAW_FILE, "preview": "model_3d_preview.png", "created": now(), "seed": seed,
            "engine": three_d.GENERATORS["trellis2"]["label"], "engine_id": "trellis2", "size": len(fitted), "format": "glb",
            "textured": False, "generation_seconds": seconds, "quality": quality,
            "quality_label": three_d.PIXAL_QUALITIES[quality]["label"].split(" · ", 1)[0],
            "source_views": {view: view_meta[view].get("created") for view in items.VIEWS}, "fit": {**fit, "requested_m": item["size_m"]}}

    def apply(n):
        n.setdefault("assets", {})["model_3d"] = meta
    store.update(item["id"], apply)
    store.make_thumb(item["id"])


def _finish(worker, job, step, item):
    cfg, store = worker.cfg, worker.items
    meta = (item.get("assets") or {}).get("model_3d") or {}
    if not meta.get("file"):
        raise ComfyError("There is no 3D shape to paint: make it first")
    views, _view_meta = _views_of(store, item, "Painting the shape")
    high = (item.get("assets") or {}).get("high") or {}
    for view in items.HIGH_VIEWS:                                   # the raised pictures too, when there are fresh ones: they see the top
        if (high.get(view) or {}).get("file") and not high[view].get("stale"):
            views[view] = store.read_asset(item["id"], high[view]["file"])
    original = store.read_asset(item["id"], meta.get("raw") or meta["file"])      # always from the generator's own shape
    refined, poster, report = cleanup3d.refine(cfg, original, views, faces=item["faces"], cancel=job.cancel)
    fitted, fit = _fit(item, refined)
    names = [*licences.shape_models(meta, original), *media.source_models(*views.values())]
    fitted = media.label(fitted, names)
    store.write_asset(item["id"], "model_3d.glb", fitted)
    if poster:
        store.write_asset(item["id"], "model_3d_preview.png", media.label(poster, names))
    game = report.get("game_version", {})
    refined_info = {"created": now(), "faces": game.get("faces_after"), "faces_requested": item["faces"],
                    "faces_generated": report["inner_skin"]["faces_before"], "inner_skin_removed": report["inner_skin"]["faces_removed"],
                    "painted_from": sorted(views), "unwrapped": "unwrap" in report,
                    "paint": {v: {k: r.get(k) for k in ("outline_match", "trust")} for v, r in report.get("paint", {}).get("views", {}).items()}}

    def apply(n):
        model = n["assets"]["model_3d"]
        for key in ("game_stale", "fit_stale", "fit_error"):
            model.pop(key, None)
        model.update(file="model_3d.glb", raw=RAW_FILE, size=len(fitted), textured=True, refined=refined_info,
                     fit={**fit, "requested_m": n["size_m"]})
        if poster:
            model["preview"] = "model_3d_preview.png"
    store.update(item["id"], apply)
    store.make_thumb(item["id"])


RUNNERS = {"item_hero": _hero, "item_views": _views, "item_shape": _shape, "item_high": _high, "item_finish": _finish}
