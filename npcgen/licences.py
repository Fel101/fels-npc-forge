"""What each model NPC Forge can use is licensed for, in plain words, and what that adds up to for one character.

data/licences.json is the list: every model (a picture engine, a 3D generator, a voice engine, a helper, a data pack) with its licence,
whether it may be used commercially (yes / conditions / unclear / no), a summary and the links, as its own page or licence file declared
on the date it says. Each entry names the selectors that reach it ("image:zimage", "edit:qwen2511", "three_d:trellis2", "voice:qwen",
"upscaler:RealESRGAN_x4plus.pth", "feature:pose"...) and may `use` other entries (a 3D generator that reads DINOv3 features is only as
free as DINOv3). Nothing here is legal advice; the file says so too.

For one character, `check` reads what its assets record about the engine that made them (assets.portrait.engine, the voice's engine,
the 3D model's generator...) and says which models made it and how free the whole is: the worst of what made it. A picture with no engine
recorded was drawn by the built-in Mage-Flow. What cannot be told (an animation made before its engine was recorded, an engine this list
does not know) is said as unknown and keeps the character from being rated better than "not cleared".
"""
import json
import pathlib
import threading

DATA = pathlib.Path(__file__).resolve().parent.parent / "data" / "licences.json"
ORDER = ("yes", "conditions", "unclear", "no")          # best to worst
RANK = {status: i for i, status in enumerate(ORDER)}
NOTHING = "none"                                          # a character with nothing made yet has nothing to rate

_LOCK = threading.Lock()
_CACHE = {}


def _effective(model_id, by_id, seen=()):
    entry = by_id.get(model_id)
    if entry is None:
        return "unclear"
    if model_id in seen:                                  # (a loop in the file: ignore the way back)
        return entry["status"]
    return worst([entry["status"], *(_effective(u, by_id, seen + (model_id,)) for u in entry.get("uses", []))])


def load():
    """The registry as it is on disk: {checked, disclaimer, statuses, kinds, notes, models: [entry...], by_id, by_selector, effective}.
    Read once per change of the file."""
    with _LOCK:
        stamp = DATA.stat().st_mtime_ns
        if _CACHE.get("stamp") != stamp:
            raw = json.loads(DATA.read_text("utf-8"))
            by_id = {m["id"]: m for m in raw["models"]}
            by_selector = {s: m["id"] for m in raw["models"] for s in m.get("selectors", [])}
            _CACHE.clear()
            _CACHE.update(stamp=stamp, data={**raw, "by_id": by_id, "by_selector": by_selector,
                                             "effective": {mid: _effective(mid, by_id) for mid in by_id}})
        return _CACHE["data"]


def stamp():
    """When the list was last changed (what a Library row was rated against), None when the file cannot be read."""
    try:
        return DATA.stat().st_mtime_ns
    except OSError:
        return None


def worst(statuses):
    """The worst of some statuses ("yes" when there are none)."""
    return max(statuses, key=RANK.get, default="yes")


def effective(model_id):
    """A model's status counting the models it uses: the worst of the two ("unclear" for a model the list does not know)."""
    return load()["effective"].get(model_id, "unclear")


def catalogue():
    """What the page shows: the registry with each model's effective status and the selectors that reach it, grouped by `kinds` order."""
    data = load()
    models = [{**m, "effective": effective(m["id"]), "uses": list(m.get("uses", [])), "selectors": list(m.get("selectors", []))}
              for m in data["models"]]
    return {"checked": data["checked"], "disclaimer": data["disclaimer"], "statuses": data["statuses"], "kinds": data["kinds"],
            "notes": data["notes"], "models": models, "by_selector": dict(data["by_selector"])}


def selector_status(selector):
    """The effective status of the model a selector reaches ("unclear" for one the list does not know)."""
    model_id = load()["by_selector"].get(selector)
    return effective(model_id) if model_id else "unclear"


# ---- one character ----------------------------------------------------------------------------------------------------------------
def _engine_selectors():
    """{engine label: selector} for the picture engines (an engine's label is what its pictures record)."""
    from . import engines
    table = {spec["label"]: f"image:{key}" for key, spec in engines.IMAGE_ENGINES.items()}
    table.update({spec["label"]: f"edit:{key}" for key, spec in engines.EDIT_ENGINES.items()})
    table.update({"Mage-Flow": "image:mage", "Mage-Flow-Edit": "edit:mage"})
    return table


def picture_engine(meta, *, edit=False):
    """The recorded label, or the built-in engine used before still images recorded one."""
    meta = meta or {}
    edit = edit or meta.get("mode") == "match" or meta.get("source") == "photo"
    return meta.get("engine") or ("Mage-Flow-Edit" if edit else "Mage-Flow")


def animation_engines(clip, direction):
    """Engine labels for the assembled frames, including mirrored and procedural directions."""
    assembled = clip.get("engines")
    source = (clip.get("mirrored") or {}).get(direction, direction)
    if isinstance(assembled, dict):
        return assembled.get(direction, assembled.get(source)) or []
    if isinstance(assembled, list):
        return assembled
    if clip.get("kind") == "procedural":
        return []  # The inputs to older procedural clips were never recorded.
    return ((clip.get("directions") or {}).get(source) or {}).get("engines") or []


def picture_report(groups, what):
    """Rate groups of recorded picture engines. An empty group is an unknown source."""
    labels = _engine_selectors()
    made, unknown = {}, []
    for group in groups:
        if not group:
            unknown.append(f"The engine that drew some {what} was not recorded.")
        for label in group:
            if label in labels:
                made[labels[label]] = [what]
            else:
                unknown.append(f"The {what} were drawn by '{label}', which this list does not know.")
    return _rate(made, unknown)


def model_name(selector):
    """The model name recorded by the licence registry for a generator or helper."""
    data = load()
    model = data["by_id"].get(data["by_selector"].get(selector))
    return model["name"] if model else None


def _three_d_selector(model):
    """The selector of a character's 3D model: a designed body, an imported model, or the generator that made it."""
    if model.get("designed"):
        return "feature:designed_body"
    if model.get("imported"):
        return "three_d:import"
    if model.get("engine_id"):
        return f"three_d:{model['engine_id']}"
    from . import three_d
    for row in three_d.choices():
        if row["label"] == model.get("engine"):
            return f"three_d:{row['id']}"
    return None


def shape_models(meta, *sources):
    """The labels on a shape, or its recorded generator for an older model."""
    from . import media
    names = media.source_models(*sources)
    model_id = _three_d_selector(meta)
    if not names and model_id not in (None, "three_d:import", "feature:designed_body"):
        name = model_name(model_id)
        if name:
            names.append(name)
    return names


def known_shape_ai(meta):
    """Whether the recorded workflow used AI, or None when its origin is unknown."""
    if meta.get("designed") or meta.get("engine_id") in ("bodydesigner", "glasses", "garment"):
        return False
    if meta.get("imported") or meta.get("engine_id") == "import":
        return None
    return True


def label_shape(data, meta):
    """Label delivery copies of older models or renders from their recorded generator."""
    from . import media
    generated = media.read_label(data).get("ai-generated", bool(known_shape_ai(meta)))
    return media.label(data, shape_models(meta, data), ai_generated=generated)


def used(npc):
    """({selector: [what it made]}, [what could not be told]) for one character."""
    a = npc.get("assets") or {}
    made, unknown = {}, []
    labels = _engine_selectors()

    def add(selector, what):
        made.setdefault(selector, [])
        if what not in made[selector]:
            made[selector].append(what)

    def picture(meta, what, default):
        label = (meta or {}).get("engine")
        if not label:
            add(default, what)                            # (no engine recorded: the built-in Mage-Flow drew it)
        elif label in labels:
            add(labels[label], what)
        else:
            unknown.append(f"The {what} was drawn by '{label}', which this list does not know.")

    if a.get("portrait"):
        picture(a["portrait"], "portrait", "edit:mage" if a["portrait"].get("source") == "photo" else "image:mage")
    if a.get("sprite"):
        picture(a["sprite"], "full-body sprite", "edit:mage" if a["sprite"].get("mode") == "match" else "image:mage")
    expressions = a.get("expressions") or {}
    for meta in expressions.values():
        picture(meta, "expressions", "edit:mage")
    for meta in ((a.get("sheet") or {}).get("views") or {}).values():
        picture(meta, "model sheet", "edit:mage")

    drawn = []                                            # animation frames: the engines they record, else unknown
    for meta in (a.get("animation_views") or {}).values():
        drawn.append(meta.get("engines"))
    for clip in (a.get("animations") or {}).values():
        if clip.get("files"):
            drawn.extend(animation_engines(clip, direction) for direction in clip["files"])
        elif clip.get("kind") == "procedural":
            continue
        elif clip.get("directions"):
            drawn.extend(d.get("engines") for d in clip["directions"].values())
        else:
            drawn.append(None)
    if any(not engines_ for engines_ in drawn):
        unknown.append("The engine that drew the animation frames was not recorded (animations made before NPC Forge kept it).")
    for engines_ in drawn:
        for label in engines_ or ():
            if label in labels:
                add(labels[label], "animation frames")
            else:
                unknown.append(f"The animation frames were drawn by '{label}', which this list does not know.")

    model = a.get("model_3d")
    if model:
        selector = _three_d_selector(model)
        if selector:
            add(selector, "3D model")
        else:
            unknown.append("The generator that made the 3D model was not recorded.")
        refined = model.get("refined") or {}
        face = refined.get("face")
        if isinstance(face, dict) and face.get("upscaler"):
            add(f"upscaler:{face['upscaler']}", "sharper face texture")
        if refined.get("hair") and not (isinstance(refined["hair"], dict) and refined["hair"].get("error")):
            add("feature:hair_mask", "hair matching")
        for key in ("hands", "head"):
            if isinstance(refined.get(key), dict) and not refined[key].get("error"):
                add("feature:pose", "hands and head placement")
    if a.get("head_3d"):
        add("three_d:pixal3d", "detailed head")
    rig = a.get("rig_3d")
    if rig:
        if rig.get("pose_model"):
            add("feature:pose", "skeleton joints")
        if rig.get("clip_set") == "library":
            add("feature:animation_library", "animation clips")

    voice = npc.get("voice") or {}
    engines_of_voice = {voice.get("engine"), (voice.get("reference") or {}).get("engine")} - {None}
    for engine in engines_of_voice:
        add(f"voice:{engine}", "voice")
    for line in (npc.get("voice_lines") or {}).values():
        if line.get("engine"):
            add(f"voice:{line['engine']}", "spoken lines")
    return made, unknown


def check(npc):
    """The licence report for one character: {verdict, label, models: [{id, name, kind, role, licence, status, effective, short, used_for}],
    unknown, checked}. The verdict is the worst `effective` status of the models that made something (a helper that only measures counts
    only when it is not cleared), "unclear" at best when something is unknown, and "none" when nothing has been made yet."""
    return _rate(*used(npc))


CREATURE_STAGES = {"concept": ("image", "concept picture"), "3d": ("three_d", "3D model")}


def creature(stages):
    """The licence report for one creature. `stages` maps each stage the creature has made ("concept", "3d") to the id of the
    engine that made it, or None when its record does not say. Rated the same way as a character: the worst of the models it uses."""
    made, unknown = {}, []
    for stage, (kind, what) in CREATURE_STAGES.items():
        if stage not in stages:
            continue
        if stages[stage]:
            made.setdefault(f"{kind}:{stages[stage]}", []).append(what)
        else:
            unknown.append(f"The engine that made the {what} was not recorded.")
    return _rate(made, unknown)


def _rate(made, unknown):
    """The report for what was made with: {selector: [what it made]}, and what could not be told."""
    data = load()
    rows, seen = [], {}
    for selector, what in made.items():
        model_id = data["by_selector"].get(selector)
        if model_id is None:
            unknown.append(f"{selector} is not in the licence list.")
            continue
        if model_id in seen:                              # (two selectors can reach one model: image:mage and edit:mage)
            seen[model_id]["used_for"] += [w for w in what if w not in seen[model_id]["used_for"]]
            continue
        entry = data["by_id"][model_id]
        seen[model_id] = {"id": model_id, "name": entry["name"], "kind": entry["kind"], "role": entry.get("role", "creates"),
                          "licence": entry["licence"], "status": entry["status"], "effective": data["effective"][model_id],
                          "short": entry.get("short", ""), "used_for": list(what)}
        rows.append(seen[model_id])
    counted = [r["effective"] for r in rows if r["role"] == "creates" or r["effective"] in ("unclear", "no")]
    if unknown:
        counted.append("unclear")
    verdict = worst(counted) if (rows or unknown) else NOTHING
    rows.sort(key=lambda r: (-RANK[r["effective"]], r["name"]))
    label = {s["id"]: s["label"] for s in data["statuses"]}.get(verdict, "Nothing made yet")
    return {"verdict": verdict, "label": label, "models": rows, "unknown": list(dict.fromkeys(unknown)), "checked": data["checked"]}


def verdict(npc):
    """Just the overall rating of a character ("yes", "conditions", "unclear", "no" or "none"): what the Library lists and filters by."""
    return check(npc)["verdict"]
