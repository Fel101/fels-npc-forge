"""Lineage and staleness (Phase 2 of the creature maker): each stage records what it was made from, the store says whether each stage
is fresh, stale or unknown, and the pack refuses what is out of date. Nothing here runs Blender or ComfyUI; the Blender step is the
stand-in from test_creature_shape.py."""
import hashlib
import json
import os
import threading
import time
from pathlib import Path

import pytest

from npcgen import creaturemaker, creatures, creaturepack
from npcgen.comfy import ComfyError
from test_creature_shape import FakeProcess


class FakeLibrary:
    def __init__(self, root):
        self.root = Path(root)


class FakeJob:
    options = None                                            # a job's options (a picture grid sets seeds)

    def __init__(self):
        self.cancel = threading.Event()

    def beat(self, text):
        pass


class FakeWorker:
    comfy = None                                              # the ComfyUI client; a test that draws puts a fake here

    def __init__(self, store):
        self.creatures = store


def sha(data):
    return hashlib.sha256(data).hexdigest()


def new_store(tmp_path, **changes):
    store = creatures.CreatureStore(FakeLibrary(tmp_path))
    fields = {"name": "Hound", "project": "ember-hollow", "body_plan": "serpentine", "length_m": 2.0, "segments": 12,
              "seed": 202, "prompt": "a hound", "key": "hound-test", **changes}
    return store, store.create(creatures.clean_creature(fields))["id"]


def make_chain(store, cid, picture=b"picture-a", hashes=True):
    """Files as the pipeline writes them: a concept picture, the 3D model made from it, and the game model made from that model.
    With hashes=False they are the records from before the freshness check existed."""
    creature = store.get(cid)
    concept = {"engine": "qwen2512", "seed": creature["seed"]}
    run = {"engine": "trellis2_single", "model": "microsoft/TRELLIS.2-4B", "seed": creature["seed"]}
    prep = {"plan": "serpentine", "triangles_after": 5000, "size_m": [0.7, 2.0, 0.8], "elongation": 2.3}
    if hashes:
        concept["sha256"] = sha(picture)
        run["concept_sha256"] = sha(picture)
        prep["raw_sha256"] = sha(b"raw")
        prep["settings"] = creatures.prep_settings(creature)
    store.write_file(cid, "concept.png", picture)
    store.write_file(cid, "concept.json", json.dumps(concept))
    store.write_file(cid, "raw/model.glb", b"raw")
    store.write_file(cid, "raw/run.json", json.dumps(run))
    store.write_file(cid, "model.glb", b"glb")
    store.write_file(cid, "prep.json", json.dumps(prep))
    store.update(cid, lambda c: c.update(concept_approved=True))
    return store.get(cid)


def states(store, cid):
    return {stage: store.freshness(cid)[stage]["state"] for stage in ("concept", "3d", "prep")}


# ---- what the stages record, and what the store says about it -------------------------------------------------------------------------

def test_a_chain_made_from_what_is_there_is_fresh(tmp_path):
    store, cid = new_store(tmp_path)
    make_chain(store, cid)
    assert states(store, cid) == {"concept": "fresh", "3d": "fresh", "prep": "fresh"}


def test_a_new_picture_makes_the_3d_and_the_game_model_stale_with_the_reason(tmp_path):
    store, cid = new_store(tmp_path)
    make_chain(store, cid)
    store.write_file(cid, "concept.png", b"picture-b")                       # a redone concept picture
    fresh = store.freshness(cid)
    assert fresh["3d"] == {"state": "stale", "reason": "the concept picture changed after this model was made"}
    assert fresh["prep"]["state"] == "stale" and "3D model it was made from" in fresh["prep"]["reason"]


def test_choosing_a_grid_picture_leaves_the_models_stale(tmp_path):
    store, cid = new_store(tmp_path)
    make_chain(store, cid)
    store.write_file(cid, "concepts/101.png", b"grid-picture")
    store.write_file(cid, "concepts/101.json", json.dumps({"seed": 101, "engine": "qwen2512"}))
    store.choose_concept(cid, 101)
    assert states(store, cid) == {"concept": "fresh", "3d": "stale", "prep": "stale"}
    assert store.freshness(cid)["3d"]["reason"] == "the concept picture changed after this model was made"
    with pytest.raises(ValueError, match="3D model is out of date"):          # and the pack will not ship it
        creaturepack.build(store, cid)


def test_a_changed_setting_makes_only_the_game_model_stale_and_says_which(tmp_path):
    store, cid = new_store(tmp_path, body_plan="quadruped")
    make_chain(store, cid)
    store.update(cid, lambda c: c.update(tris=6000, facing="right"))
    fresh = store.freshness(cid)
    assert fresh["3d"]["state"] == "fresh"
    assert fresh["prep"]["state"] == "stale"
    assert "settings changed" in fresh["prep"]["reason"] and "tris" in fresh["prep"]["reason"] and "facing" in fresh["prep"]["reason"]


def test_a_changed_length_or_segments_makes_the_game_model_stale(tmp_path):
    store, cid = new_store(tmp_path)
    make_chain(store, cid)
    store.update(cid, lambda c: c.update(length_m=2.5, segments=16))
    reason = store.freshness(cid)["prep"]["reason"]
    assert "length_m" in reason and "segments" in reason


def test_a_changed_seed_makes_a_model_stale_even_when_it_has_no_hashes(tmp_path):
    store, cid = new_store(tmp_path)
    make_chain(store, cid, hashes=False)
    store.update(cid, lambda c: c.update(seed=999))
    assert store.freshness(cid)["3d"] == {"state": "stale", "reason": "the seed changed after this model was made"}


def test_the_summary_that_the_list_shows_carries_the_freshness(tmp_path):
    store, cid = new_store(tmp_path)
    make_chain(store, cid)
    [listed] = store.list()
    assert listed["freshness"]["3d"]["state"] == "fresh" and listed["freshness"]["prep"]["state"] == "fresh"


def test_a_picture_and_its_3d_model_can_not_be_asked_for_in_one_request(tmp_path):
    creature = {"body_plan": "serpentine", "concept_approved": True, "_on_disk": {"concept": True, "3d": True, "prep": True}}
    with pytest.raises(ValueError, match="concept picture on its own first"):
        creatures.plan(creature, {"steps": ["concept", "3d"], "redo": ["concept", "3d"]})


def test_the_concept_and_the_3d_model_record_what_they_were_made_from(tmp_path, monkeypatch):
    store, cid = new_store(tmp_path)
    drawn = b"drawn-picture"
    monkeypatch.setattr(creaturemaker.engines, "image", lambda comfy, *args, **kwargs: (drawn, {"steps": 4}))
    creaturemaker.run_concept(FakeWorker(store), FakeJob(), {"kind": "creature_concept"}, store.get(cid))
    concept = json.loads(store.read_file(cid, "concept.json"))
    assert concept["sha256"] == sha(drawn) and store.read_file(cid, "concept.png") == drawn

    class FakeComfy:
        def upload_image(self, data, name):
            return name

        def run_entry(self, graph, cancel=None, timeout=None, unload_models=None):
            return {}

        def fetch_file(self, filename, subfolder, kind):
            return b"glb-model"

    worker = FakeWorker(store)
    worker.comfy = FakeComfy()
    monkeypatch.setattr(creaturemaker.three_d, "saved_file", lambda entry, label, what, node=None: {"filename": "creature.glb"})
    store.update(cid, lambda c: c.update(concept_approved=True))
    creaturemaker.run_3d(worker, FakeJob(), {"kind": "creature_3d"}, store.get(cid))
    run = json.loads(store.read_file(cid, "raw/run.json"))
    assert run["concept_sha256"] == sha(drawn) and run["seed"] == 202 and run["quality"] == "medium"
    assert store.freshness(cid)["3d"]["state"] == "fresh"


def test_a_picture_rewritten_within_the_same_clock_tick_is_hashed_again(tmp_path):
    store, cid = new_store(tmp_path)
    path = store.resolve(cid, "concept.png")
    path.write_bytes(b"picture-a")
    now = time.time_ns()
    os.utime(path, ns=(now, now))                                              # written just now: inside the racy window
    first = store.sha256(cid, "concept.png")
    path.write_bytes(b"picture-b")                                             # the same size, in the same clock tick
    os.utime(path, ns=(now, now))
    assert first == sha(b"picture-a")
    assert store.sha256(cid, "concept.png") == sha(b"picture-b")


def test_a_serpent_is_not_made_stale_by_its_facing(tmp_path):
    store, cid = new_store(tmp_path)                                         # a serpent
    make_chain(store, cid)
    store.update(cid, lambda c: c.update(facing="right"))
    assert store.freshness(cid)["prep"]["state"] == "fresh"                  # its head is found from its shape, not its picture


def test_a_model_made_before_the_check_is_unknown_not_fresh(tmp_path):
    store, cid = new_store(tmp_path)
    make_chain(store, cid, hashes=False)
    fresh = store.freshness(cid)
    assert fresh["3d"]["state"] == "unknown" and fresh["prep"]["state"] == "unknown"
    assert "made before the freshness check" in fresh["3d"]["reason"]


def test_a_game_model_whose_3d_model_is_gone_is_stale(tmp_path):
    store, cid = new_store(tmp_path)
    make_chain(store, cid)
    store.resolve(cid, "raw/model.glb").unlink()
    assert store.freshness(cid)["prep"] == {"state": "stale", "reason": "its 3D model is gone"}


def test_the_game_model_records_what_it_was_made_from(tmp_path, monkeypatch):
    store, cid = new_store(tmp_path)
    creature = make_chain(store, cid)
    store.resolve(cid, "model.glb").unlink()
    store.resolve(cid, "prep.json").unlink()
    store.update(cid, lambda c: c.update(concept_approved=True))
    FakeProcess.code = 0
    FakeProcess.text = ""
    FakeProcess.report = 'REPORT: {"plan": "serpentine", "triangles_after": 5000, "size_m": [0.7, 2.0, 0.8]}\n'
    monkeypatch.setattr(creaturemaker.subprocess, "Popen", FakeProcess)
    creaturemaker.run_prep(FakeWorker(store), FakeJob(), {"kind": "creature_prep"}, store.get(cid))
    saved = json.loads(store.read_file(cid, "prep.json"))
    assert saved["raw_sha256"] == sha(b"raw")
    assert saved["settings"] == creatures.prep_settings(creature)
    assert store.freshness(cid)["prep"]["state"] == "fresh"


def test_a_redo_of_a_stage_says_which_later_stages_it_leaves_out_of_date():
    everything = {"concept": True, "3d": True, "prep": True}
    assert creatures.out_of_date(everything, ["concept"]) == ["3d", "prep"]
    assert creatures.out_of_date(everything, ["3d"]) == ["prep"]
    assert creatures.out_of_date(everything, ["prep"]) == []
    assert creatures.out_of_date(everything, ["concept", "3d"]) == ["prep"]
    assert creatures.out_of_date({"concept": True, "3d": False, "prep": False}, ["concept"]) == []


def test_the_settings_are_the_defaults_a_record_with_nulls_gets():
    settings = creatures.prep_settings({"body_plan": "quadruped", "length_m": 2.5, "segments": 12, "tris": None, "facing": None})
    assert settings == {"tris": 20000, "length_m": 2.5, "segments": 12, "plan": "static", "facing": "left"}
    serpent = creatures.prep_settings({"body_plan": "serpentine", "length_m": 2.0, "segments": 12, "facing": "right"})
    assert serpent["facing"] is None


# ---- the pack ------------------------------------------------------------------------------------------------------------------------

def test_a_fresh_chain_packs_with_no_warnings(tmp_path):
    store, cid = new_store(tmp_path)
    make_chain(store, cid)
    manifest = json.loads(creaturepack.build(store, cid)["manifest.json"])
    assert manifest["freshness"] == {"concept": "fresh", "3d": "fresh", "prep": "fresh"}
    assert manifest["warnings"] == []


def test_a_pack_is_refused_once_the_picture_has_been_redone(tmp_path):
    store, cid = new_store(tmp_path)
    make_chain(store, cid)
    store.write_file(cid, "concept.png", b"picture-b")
    with pytest.raises(ValueError, match="3D model is out of date: the concept picture changed.*Make it again"):
        creaturepack.build(store, cid)


def test_a_pack_is_refused_for_an_unapproved_picture(tmp_path):
    store, cid = new_store(tmp_path)
    make_chain(store, cid)
    store.update(cid, lambda c: c.update(concept_approved=False))
    with pytest.raises(ValueError, match="Approve the concept picture"):
        creaturepack.build(store, cid)


def test_a_picture_only_plan_is_refused_a_pack_as_it_is_refused_a_3d_model(tmp_path, monkeypatch):
    # No body plan is picture-only now (the quadruped is made in 3D again), so a plan is made picture-only here.
    monkeypatch.setitem(creatures.PLANS["quadruped"], "make", ["concept"])
    store, cid = new_store(tmp_path, body_plan="quadruped")
    make_chain(store, cid)
    with pytest.raises(ValueError, match="concept picture only"):
        creaturepack.build(store, cid)


def test_a_game_model_made_from_other_settings_stops_the_pack(tmp_path):
    store, cid = new_store(tmp_path)
    make_chain(store, cid)
    store.update(cid, lambda c: c.update(tris=6000))
    with pytest.raises(ValueError, match="game model is out of date: the settings changed.*tris"):
        creaturepack.build(store, cid)


def test_a_model_from_before_the_check_packs_with_a_warning_in_the_manifest(tmp_path):
    store, cid = new_store(tmp_path)
    make_chain(store, cid, hashes=False)
    manifest = json.loads(creaturepack.build(store, cid)["manifest.json"])
    assert manifest["freshness"]["3d"] == "unknown" and manifest["freshness"]["prep"] == "unknown"
    assert any("3D model" in w for w in manifest["warnings"]) and any("game model" in w for w in manifest["warnings"])
