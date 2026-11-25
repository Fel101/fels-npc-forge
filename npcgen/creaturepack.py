"""A creature's pack: the files a game takes, with a manifest that says what they are and how they were made.

  model.glb       the game model (prep step): one matte colour, at most the triangle budget, rigged for a serpentine
  loader/         the code a game reads the pack with, made for the game's engine (npcgen/creature_export.py): the loader,
                  the clip map for this body plan, and a headless test that checks the pack
  concept.png     the concept picture it was made from (when there is one)
  manifest.json   schema "npcforge.creature-pack/1": units, axes, origin, size, forward, bones and clips, the shape checks that apply
                  to its body plan, the engines and seeds, the licence

Units are metres, up is +Y and forward is +Z, with the bottom of the body on Y = 0 and its centre on the origin. glTF exports
Blender's Z-up as Y-up, so the sizes below are read from the prep report's Blender axes and turned the same way.

A pack is refused while its creature's licence rating is not cleared for its game: a game that is sold takes a rating of "yes", or
"conditions" once the director has accepted them (the acceptance is on the game's record, and the manifest carries it); a game that
is not sold takes any rating that was made. See npcgen/creature_projects.py and data/licences.json.
"""
import io
import json
import zipfile

from . import creature_export, creature_projects, creatures, licences, media

SCHEMA = "npcforge.creature-pack/1"


def _stamp(creature, stage):
    return (creature.get("made") or {}).get(stage)


def _gltf_size(size_m):
    """A size from the prep report, Blender's [x, y, z] with z up, as glTF's [x, y, z] with y up: the height moves to y."""
    if not size_m or len(size_m) != 3:
        return None
    return [round(size_m[0], 4), round(size_m[2], 4), round(size_m[1], 4)]


def build(store, creature_id):
    """The pack as {file name: bytes}. Raises ValueError with the reason a pack can not be made yet."""
    creature = store.get(creature_id)                       # KeyError when there is no such creature
    on_disk = creature["_on_disk"]
    if not creature.get("project"):
        raise ValueError("Give this creature its game (the Game field) before making a pack")
    if creature["body_plan"] not in creatures.PLANS:
        raise ValueError(f"The body plan '{creature['body_plan']}' is not one the creature maker knows (data/creature_plans.json): "
                         "choose one before making a pack")
    info = creatures.PLANS[creature["body_plan"]]
    if "3d" not in info.get("make", list(creatures.STAGES)):              # the plan first: its reason is the one to show
        raise ValueError(f"A {info['label'].lower()} creature is a concept picture only, for now. " + info["note"])
    if not on_disk["prep"]:
        raise ValueError("Make the game model first: the pack is built from it")
    report = store.report(creature_id)
    if report is None:                                      # the shape checks read the report: without it nothing is checked
        raise ValueError("The game model's report (prep.json) is missing or unreadable: make the game model again before making a pack")
    if not creature.get("concept_approved"):
        raise ValueError("Approve the concept picture before making a pack: the game model is made from an approved picture")
    rating = store.licence(creature_id)
    project = store.projects.get(creature["project"])
    refusal = creature_projects.licence_refusal(rating["verdict"], project, rating["label"])
    if refusal:
        raise ValueError(refusal)
    fresh = store.freshness(creature_id, creature)
    for stage, label in (("3d", "3D model"), ("prep", "game model")):
        if fresh[stage]["state"] == "stale":
            raise ValueError(f"The {label} is out of date: {fresh[stage]['reason']}. Make it again before making the pack.")
    # A stage made before the freshness check is packed, with a warning in the manifest: it is never shipped without one.
    warnings = [f"{label}: {fresh[stage]['reason']}" for stage, label in (("3d", "3D model"), ("prep", "game model"))
                if fresh[stage]["state"] == "unknown"]

    plan = creatures.PLANS.get(creature["body_plan"], {})
    shape = report.get("checks", {})                        # only the shape checks that apply to this body plan (creatures.shape_checks)
    if shape.get("blocking"):
        raise ValueError("The game model does not pass its shape check, so the pack is refused: " + " ".join(shape["warnings"]))
    warnings += shape.get("warnings", [])                  # a check that can not be measured yet is packed with a warning, never silently
    rigged = creature["body_plan"] in creatures.RIG_BUILT
    concept = store.record(creature_id, "concept.json")
    run = store.record(creature_id, "raw/run.json")
    three_d_engine = run.get("engine") or creatures.THREE_D_ENGINE_BY_MODEL.get(run.get("model"))     # (older records name the model only)
    clip_map = (project.get("clip_map") or {}).get(creature["body_plan"], {})
    loader = creature_export.loader_files(project["engine"], clip_map)          # the game's loader for this pack
    if rigged and not clip_map:                          # a rig with no clip names: the game would play nothing, so the pack says so
        warnings.append(f"The game names no clips for the {creature['body_plan']} body plan (its clip map), so the game will play no animation")
    manifest = {
        "schema": SCHEMA,
        "id": creature_id,
        "name": creature["name"],
        "project": creature["project"],
        "engine": project["engine"],
        "tri_budget": project.get("tri_budget"),          # the game's triangle budget; the game's test checks the model against it
        "accepted_licence_conditions": creature_projects.accepted_conditions(rating["verdict"], project),
        "body_plan": creature["body_plan"],
        "rigged": rigged,
        "units": "metres",
        "up": "+Y",
        "forward": plan.get("facing_after_prep"),         # the axis the game model's head points along (data/creature_plans.json)
        "origin": "centre of the footprint; the bottom of the body sits on Y = 0",
        "size_m": _gltf_size(report.get("size_m")),
        "length_m": creature.get("length_m"),
        "triangles": report.get("triangles_after"),
        "bones": report.get("bones", 0) if rigged else 0,
        "clips": list(plan.get("clips", [])) if rigged else [],
        "files": ["model.glb", "manifest.json"] + (["concept.png"] if on_disk["concept"] else []) + sorted(loader),
        "checks": report.get("checks", {}),
        "freshness": {stage: fresh[stage]["state"] for stage in ("concept", "3d", "prep")},
        "warnings": warnings,
        "made": {stage: _stamp(creature, stage) for stage in creatures.STAGES},
        "provenance": {
            "concept": {"engine": concept.get("engine"), "seed": concept.get("seed"),
                        "size": concept.get("size") or concept.get("width"), "prompt": concept.get("prompt")}
            if on_disk["concept"] else None,
            "three_d": {"engine": three_d_engine, "model": run.get("model"), "quality": run.get("quality"),
                        "seed": run.get("seed")} if on_disk["3d"] else None,
            "prep": {"plan": report.get("plan") or ("serpentine" if rigged else "static"),
                     "width_to_height": report.get("width_to_height"), "minutes": report.get("minutes")},
        },
        "licence": {"verdict": rating["verdict"], "label": rating["label"], "checked": rating["checked"],
                    "models": [{"name": m["name"], "licence": m["licence"], "status": m["effective"], "used_for": m["used_for"]}
                               for m in rating["models"]],
                    "unknown": rating["unknown"]},
    }
    model = store.read_file(creature_id, "model.glb")
    names = media.source_models(model) or [run.get("model") or licences.model_name(f"three_d:{three_d_engine}")]
    files = {"model.glb": media.label(model, names),
             "manifest.json": (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode("utf-8")}
    if on_disk["concept"]:
        picture = store.read_file(creature_id, "concept.png")
        names = media.source_models(picture) or [licences.model_name(f"image:{concept.get('engine')}")]
        files["concept.png"] = media.label(picture, names)
    files.update(loader)
    return files


def zip_bytes(files):
    """The pack's files as one ZIP (the file names are already safe: they are the ones build() chose)."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return buffer.getvalue()
