"""Creatures: the bestiary list, the checks on a creature's fields, the store on disk, the steps a request makes, and the graph of
the 3D step. Nothing here calls ComfyUI or Blender."""
import ast
import json
from pathlib import Path

import pytest

from npcgen import creaturemaker, creatures
from npcgen.library import ID_RE  # noqa: F401  (the store checks ids with it)


class FakeLibrary:
    def __init__(self, root):
        self.root = Path(root)


@pytest.fixture
def store(tmp_path):
    return creatures.CreatureStore(FakeLibrary(tmp_path))


def fields(**changes):
    base = {"name": "Reedcoil", "body_plan": "serpentine", "length_m": 2.5, "segments": 12, "seed": 7,
            "prompt": "a side view of a reed serpent", "shore": "tit", "key": "eh-s01"}
    base.update(changes)
    return base


# ---- the bestiary list ------------------------------------------------------------------------------------------------------------

def test_the_bestiary_list_has_every_creature_row_once():
    briefs = creatures.load_briefs("ember-hollow")
    keys = [b["key"] for b in briefs]
    assert len(briefs) == 6
    assert len(set(keys)) == len(keys)


def test_every_brief_has_a_known_body_plan_and_effects_are_not_makeable():
    for brief in creatures.load_briefs():
        assert brief["body_plan"] in creatures.BODY_PLANS + ("effect",)
        if brief["body_plan"] == "effect":
            assert brief["makeable"] is False and brief["prompt"] == ""
        else:
            assert brief["makeable"] is True and brief["prompt"].startswith("Stylised 3D game concept art")


def test_the_maws_are_serpentines_and_the_prompt_asks_for_the_side_view():
    maw = next(b for b in creatures.load_briefs() if b["id"] == "EH-S01")
    assert maw["body_plan"] == "serpentine" and maw["tier"] == "I"
    assert "seen from the side" in maw["prompt"] and maw["prompt"].endswith("Flat solid green background.")


# ---- the checks on a creature's fields ----------------------------------------------------------------------------------------------

def test_clean_creature_keeps_good_fields():
    clean = creatures.clean_creature(fields())
    assert clean["name"] == "Reedcoil" and clean["body_plan"] == "serpentine" and clean["shore"] == "TIT"
    assert clean["seed"] == 7 and clean["segments"] == 12


@pytest.mark.parametrize("change, message", [
    ({"name": "   "}, "name"),
    ({"body_plan": "wings"}, "body plan"),
    ({"length_m": 0.1}, "length"),
    ({"segments": 2}, "segments"),
    ({"seed": -1}, "seed"),
    ({"seed": "many"}, "numbers"),
    ({"prompt": "x" * 1600}, "1500"),
])
def test_clean_creature_refuses_bad_fields(change, message):
    with pytest.raises(ValueError, match=message):
        creatures.clean_creature(fields(**change))


def test_clean_creature_picks_a_seed_when_none_is_given():
    clean = creatures.clean_creature(fields(seed=None))
    assert 0 <= clean["seed"] <= creatures.SEED_MAX


# ---- the store on disk -------------------------------------------------------------------------------------------------------

def test_create_read_update_and_delete_a_creature(store):
    made = store.create(creatures.clean_creature(fields()))
    assert made["id"].startswith("reedcoil-") and made["name"] == "Reedcoil"
    assert store.get(made["id"])["_on_disk"] == {"concept": False, "3d": False, "prep": False}
    store.update(made["id"], lambda c: c.update(name="Brinemaw"))
    assert store.get(made["id"])["name"] == "Brinemaw"
    assert [s["name"] for s in store.list()] == ["Brinemaw"]
    store.delete(made["id"])
    assert store.list() == []


def test_files_are_written_and_read_inside_the_creature_folder(store):
    made = store.create(creatures.clean_creature(fields()))
    store.write_file(made["id"], "concept.png", b"png-bytes")
    assert store.read_file(made["id"], "concept.png") == b"png-bytes"
    assert store.get(made["id"])["_on_disk"]["concept"] is True
    with pytest.raises(KeyError):
        store.resolve(made["id"], "../../escape.txt")


def test_an_unknown_or_unsafe_id_is_refused(store):
    with pytest.raises(KeyError):
        store.get("../etc")


def test_seeding_adds_only_makeable_creatures_and_is_repeatable(store):
    briefs = creatures.load_briefs("ember-hollow")
    first = store.seed("ember-hollow")
    makeable = sum(1 for b in briefs if b["makeable"])
    assert first["added"] == makeable and len(store.list()) == makeable
    second = store.seed("ember-hollow")
    assert second["added"] == 0 and len(store.list()) == makeable


# ---- the steps a request makes ----------------------------------------------------------------------------------------------

def creature_with(*made, approved=False):
    return {"body_plan": "serpentine", "concept_approved": approved,
            "_on_disk": {stage: stage in made for stage in creatures.STAGES}}


def steps_of(plan):
    return [s["key"] for s in plan]


def test_a_new_creature_makes_its_picture_before_its_3d_model():
    with pytest.raises(ValueError, match="concept picture on its own first"):
        creatures.plan(creature_with(), {"steps": ["prep"]})
    assert steps_of(creatures.plan(creature_with(), {"steps": ["concept"]})) == ["concept"]


def test_steps_already_made_are_skipped_unless_asked_again():
    assert creatures.plan(creature_with("concept", "3d"), {"steps": ["concept", "3d"]}) == []
    again = creatures.plan(creature_with("concept", "3d", approved=True), {"steps": ["3d"], "redo": ["3d"]})
    assert steps_of(again) == ["3d"]


def test_the_game_model_brings_the_3d_model_when_that_is_missing():
    plan = creatures.plan(creature_with("concept", approved=True), {"steps": ["prep"]})
    assert steps_of(plan) == ["3d", "prep"]


def test_the_3d_model_waits_for_the_approved_picture():
    with pytest.raises(ValueError, match="Approve the concept picture"):
        creatures.plan(creature_with("concept"), {"steps": ["3d"]})
    assert steps_of(creatures.plan(creature_with("concept", approved=True), {"steps": ["3d"]})) == ["3d"]


def test_a_new_picture_needs_no_approval_before_it_is_made():
    assert steps_of(creatures.plan(creature_with("concept", "3d"), {"steps": ["concept"], "redo": ["concept"]})) == ["concept"]


def test_a_plan_needs_a_step_and_a_real_body_plan():
    with pytest.raises(ValueError, match="at least one step"):
        creatures.plan(creature_with(), {"steps": []})
    with pytest.raises(ValueError, match="no body plan"):
        creatures.plan({"body_plan": "effect", "_on_disk": {}}, {"steps": ["concept"]})


# ---- the 3D graph ------------------------------------------------------------------------------------------------------------

def test_the_trellis_graph_has_one_picture_in_and_a_glb_out():
    graph = creaturemaker.trellis_graph("reedcoil_concept.png", "creature/reedcoil", 7, "medium")
    assert graph["10"]["inputs"]["image"] == "reedcoil_concept.png"
    assert graph["16"]["inputs"]["modelname"] == creaturemaker.TRELLIS_MODEL
    assert graph["16"]["inputs"]["pixal3d_multiview"] is False
    assert graph["19"]["inputs"]["seed"] == 7
    assert graph[creaturemaker.SAVE_NODE]["class_type"] == "SaveGLB"
    assert graph[creaturemaker.SAVE_NODE]["inputs"]["filename_prefix"] == "creature/reedcoil"


def test_the_concept_uses_the_commercial_engine():
    assert creaturemaker.CONCEPT_ENGINE == "qwen2512"
    assert "mage" not in creaturemaker.CONCEPT_ENGINE


def test_the_blender_script_is_valid_python():
    source = creaturemaker.PREP_SCRIPT.read_text("utf-8")
    ast.parse(source)
    assert "def serpent_clips" in source and "REPORT:" in source


def test_the_job_queue_knows_creature_targets():
    from npcgen import jobs as jobsmod
    job = jobsmod.Job(None, "Reedcoil", [], {}, target_type="creature", creature_id="reedcoil-1")
    record = job.to_record()
    assert record["creature_id"] == "reedcoil-1"
    assert jobsmod.Job.from_record(record).creature_id == "reedcoil-1"
    assert json.dumps(job.to_dict())


def test_a_creature_job_never_lets_mage_flow_draw_its_picture():
    import threading
    import types
    from npcgen import jobs as jobsmod

    class Queue:
        def __init__(self):
            self.items = []

        def put(self, job, priority):
            self.items.append(job)

    class Creatures:
        def get(self, creature_id):
            return {"id": creature_id, "name": "Reedcoil", "body_plan": "serpentine", "concept_approved": False,
                    "_on_disk": {"concept": False, "3d": False, "prep": False}}

    queue = types.SimpleNamespace(creatures=Creatures(), lock=threading.RLock(), closed=False, jobs={}, queue=Queue(),
                                  _persist=lambda: None, _priority=lambda job: 0)
    jobsmod.Jobs.submit_creature(queue, "reedcoil-1", {"steps": ["concept"]}, {})
    assert queue.queue.items[-1].options["engine_fallback"] is False
    jobsmod.Jobs.submit_creature(queue, "reedcoil-1", {"steps": ["concept"]}, {"engine_fallback": True})
    assert queue.queue.items[-1].options["engine_fallback"] is False     # a caller can not turn the fallback on


# ---- body plans, the bestiary's rig wording, projects -----------------------------------------------------------------------------

def test_the_body_plans_are_the_five_the_maker_knows_and_only_the_serpent_is_rigged():
    assert creatures.BODY_PLANS == ("serpentine", "quadruped", "six-leg", "flying", "prop")
    assert creatures.RIG_BUILT == ("serpentine",)
    assert creatures.PLANS["serpentine"]["prep"] == "serpentine" and len(creatures.PLANS["serpentine"]["clips"]) == 5
    assert all(creatures.PLANS[p]["prep"] == "static" for p in creatures.BODY_PLANS if p != "serpentine")


def test_a_brief_says_what_the_maker_builds_and_keeps_the_bestiary_wording():
    for brief in creatures.load_briefs():
        assert brief["rigged"] is (brief["body_plan"] in creatures.RIG_BUILT)
    quad = next(b for b in creatures.load_briefs() if b["body_plan"] == "quadruped")
    assert quad["rig_intent"] is True and quad["rigged"] is False


def test_clean_creature_gives_a_game_a_slug_and_defaults_to_none():
    assert creatures.clean_creature(fields(project="Ember Hollow!"))["project"] == "ember-hollow"
    assert creatures.clean_creature(fields())["project"] == ""


def test_the_list_keeps_one_games_creatures(store):
    store.create(creatures.clean_creature(fields(name="Reedcoil", project="ember-hollow")))
    store.create(creatures.clean_creature(fields(name="Hound", project="dust-moon")))
    assert [s["name"] for s in store.list("ember-hollow")] == ["Reedcoil"]
    assert len(store.list()) == 2


def test_adopting_projects_backs_up_each_record_and_is_repeatable(store):
    old = store.create(creatures.clean_creature(fields(name="Reedcoil")))      # made before projects existed
    briefs = [{"key": "eh-s01", "project": "ember-hollow"}]
    assert store.adopt_projects(briefs, write=False) == 1
    assert store.get(old["id"])["project"] == ""                             # a dry run writes nothing
    assert store.adopt_projects(briefs) == 1
    assert store.get(old["id"])["project"] == "ember-hollow"
    assert (store._dir(old["id"]) / "creature.json.v1").is_file()
    assert store.adopt_projects(briefs) == 0


def test_a_picture_is_approved_by_hand_and_a_record_from_before_approval_counts_as_approved(store):
    creature = store.create(creatures.clean_creature(fields(name="Reedcoil")))
    with pytest.raises(ValueError, match="no concept picture"):
        store.approve_concept(creature["id"])
    store.write_file(creature["id"], "concept.png", b"png")
    store.update(creature["id"], lambda c: c.update(concept_approved=False))      # what drawing the picture does
    assert store.get(creature["id"])["concept_approved"] is False
    assert store.approve_concept(creature["id"])["concept_approved"] is True
    # a record written before the flag existed, with a picture on disk, reads as approved (its picture was already used)
    path = store._dir(creature["id"]) / "creature.json"
    record = json.loads(path.read_text("utf-8"))
    record.pop("concept_approved")
    path.write_text(json.dumps(record), "utf-8")
    assert store.get(creature["id"])["concept_approved"] is True


def test_the_game_model_report_flags_a_body_taller_than_it_is_long(store):
    creature = store.create(creatures.clean_creature(fields(name="Hound", body_plan="quadruped")))
    report = {"size_m": [0.956, 2.534, 3.55], "triangles_after": 19999, "triangles_before": 681159, "bones": 0, "clips": []}
    store.write_file(creature["id"], "prep.json", json.dumps(report))
    checks = store.report(creature["id"])["checks"]
    assert checks["taller_than_long"] is True and checks["warnings"]
    store.write_file(creature["id"], "prep.json", json.dumps({**report, "size_m": [0.3, 2.5, 0.4]}))
    assert store.report(creature["id"])["checks"]["taller_than_long"] is False


# ---- licences and the pack ----------------------------------------------------------------------------------------------------------

def test_a_creature_is_rated_by_the_engines_that_made_it():
    from npcgen import licences
    made = licences.creature({"concept": "qwen2512", "3d": "trellis2_single"})
    assert made["verdict"] == "conditions"                                   # TRELLIS reads DINOv3, which has conditions
    assert "trellis2_single" in [m["id"] for m in made["models"]]
    assert licences.creature({})["verdict"] == "none"
    assert licences.creature({"concept": None})["verdict"] == "unclear"      # an engine the record does not name


def made_creature(store):
    """A creature with its three steps on disk, made with the engines the licence list rates."""
    creature = store.create(creatures.clean_creature(fields(name="Reedcoil", project="ember-hollow")))
    cid = creature["id"]
    store.write_file(cid, "concept.png", b"png")
    store.write_file(cid, "concept.json", json.dumps({"engine": "qwen2512", "seed": 7, "size": 1024, "prompt": "p"}))
    store.write_file(cid, "raw/model.glb", b"raw")
    store.write_file(cid, "raw/run.json", json.dumps({"engine": "trellis2_single", "model": "microsoft/TRELLIS.2-4B", "quality": "medium", "seed": 7}))
    store.write_file(cid, "model.glb", b"glTF")
    store.write_file(cid, "prep.json", json.dumps({"plan": "serpentine", "size_m": [0.3, 2.5, 0.4], "triangles_after": 20000,
                                                   "triangles_before": 900000, "bones": 12, "minutes": 3.0,
                                                   "clips": ["Idle", "Swim", "Bite", "Hit", "Death"], "width_to_height": 1.0}))
    return cid


def test_a_pack_holds_the_model_the_picture_and_a_manifest_in_metres(store):
    import io
    import zipfile
    from npcgen import creaturepack
    files = creaturepack.build(store, made_creature(store))
    loader = {"loader/creature_pack.gd", "loader/creature_clips.gd", "loader/creature_pack_test.gd"}   # the game's loader (Phase 5)
    assert set(files) == {"model.glb", "manifest.json", "concept.png"} | loader
    manifest = json.loads(files["manifest.json"])
    assert manifest["schema"] == creaturepack.SCHEMA and manifest["project"] == "ember-hollow"
    assert manifest["size_m"] == [0.3, 0.4, 2.5]                             # Blender's height (z) becomes glTF's y
    assert manifest["clips"] == ["Idle", "Swim", "Bite", "Hit", "Death"] and manifest["bones"] == 12
    assert manifest["licence"]["verdict"] == "conditions"
    assert manifest["provenance"]["three_d"]["engine"] == "trellis2_single"
    with zipfile.ZipFile(io.BytesIO(creaturepack.zip_bytes(files))) as archive:
        assert sorted(archive.namelist()) == sorted(files)


def test_a_pack_is_refused_until_it_has_a_game_a_game_model_and_a_cleared_licence(store):
    from npcgen import creaturepack
    bare = store.create(creatures.clean_creature(fields(name="Reedcoil")))
    with pytest.raises(ValueError, match="Give this creature its game"):
        creaturepack.build(store, bare["id"])
    unmade = store.create(creatures.clean_creature(fields(name="Hound", project="ember-hollow")))
    with pytest.raises(ValueError, match="Make the game model first"):
        creaturepack.build(store, unmade["id"])
    cid = made_creature(store)
    store.write_file(cid, "concept.json", json.dumps({"seed": 7}))           # the picture's engine is not recorded
    with pytest.raises(ValueError, match="not cleared"):
        creaturepack.build(store, cid)


# ---- the picture grid -----------------------------------------------------------------------------------------------------------------

def test_a_grid_takes_one_to_four_different_whole_seeds():
    assert creatures.clean_seeds([1, 2]) == [1, 2]
    assert creatures.clean_seeds(["3"]) == [3]
    for bad in ([], [1, 1], [-1], [1, 2, 3, 4, 5], ["x"], "1,2", [creatures.SEED_MAX + 1]):
        with pytest.raises(ValueError):
            creatures.clean_seeds(bad)


def test_a_grid_is_kept_beside_the_creature_and_changes_nothing_until_a_picture_is_chosen(store, monkeypatch):
    import threading
    import types
    monkeypatch.setattr(creaturemaker.engines, "image", lambda comfy, engine, prompt, negative, w, h, seed, steps, cfg, cancel=None:
                        (f"png-{seed}".encode(), {"engine": engine}))
    creature = store.create(creatures.clean_creature(fields(name="Hound", body_plan="quadruped", project="ember-hollow")))
    cid = creature["id"]
    job = types.SimpleNamespace(options={"seeds": [3, 4]}, cancel=threading.Event(), beat=lambda text: None)
    creaturemaker.run_concept(types.SimpleNamespace(comfy=None, creatures=store), job, {}, store.get(cid))
    assert [c["seed"] for c in store.candidates(cid)] == [3, 4]
    assert not store._dir(cid).joinpath("concept.png").exists()              # the concept picture is not changed
    chosen = store.choose_concept(cid, 4)
    assert store.read_file(cid, "concept.png") == b"png-4"
    assert chosen["seed"] == 4 and chosen["concept_approved"] is True and chosen["made"]["concept"]
    assert json.loads(store.read_file(cid, "concept.json"))["seed"] == 4


def test_only_a_picture_in_the_grid_can_be_chosen(store):
    creature = store.create(creatures.clean_creature(fields(name="Hound", body_plan="quadruped")))
    with pytest.raises(ValueError, match="no picture with seed 9"):
        store.choose_concept(creature["id"], 9)


# ---- the picture-only plans and the triangle budget ----------------------------------------------------------------------------------

def test_a_quadruped_is_made_in_3d_like_the_other_static_bodies_now():
    # The owner's decision (2026-10-17): quadrupeds are made in 3D again, now that their up direction is checked against the picture.
    empty = {"concept": False, "3d": False, "prep": False}
    assert creatures.PLANS["quadruped"]["make"] == ["concept", "3d", "prep"]
    with pytest.raises(ValueError, match="Make the concept picture on its own first"):
        creatures.plan({"body_plan": "quadruped", "_on_disk": empty}, {"steps": ["concept", "3d", "prep"]})
    picture = {"body_plan": "quadruped", "concept_approved": True, "_on_disk": {"concept": True, "3d": False, "prep": False}}
    assert steps_of(creatures.plan(picture, {"steps": ["3d"]})) == ["3d"]
    assert steps_of(creatures.plan({"body_plan": "quadruped", "_on_disk": empty}, {"steps": ["concept"]})) == ["concept"]


def test_a_creature_has_a_triangle_budget_of_500_to_20000_and_20000_by_default():
    assert creatures.clean_creature(fields())["tris"] == 20000
    assert creatures.clean_creature(fields(tris=6000))["tris"] == 6000
    for bad in (100, 25000):
        with pytest.raises(ValueError, match="triangle budget"):
            creatures.clean_creature(fields(tris=bad))


def test_the_game_model_step_passes_the_creatures_own_budget():
    raw, out = Path("raw.glb"), Path("out.glb")
    small = creaturemaker.prep_command({"id": "drone-1", "length_m": 1.2, "segments": 12, "tris": 6000}, raw, out, "static")
    assert small[small.index("--tris") + 1] == "6000" and small[small.index("--plan") + 1] == "static"
    older = creaturemaker.prep_command({"id": "old-1", "length_m": 2.5, "segments": 12}, raw, out, "serpentine")
    assert older[older.index("--tris") + 1] == "20000"                              # records made before the budget existed
