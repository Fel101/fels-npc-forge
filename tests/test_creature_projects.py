"""Projects (Phase 4 of the creature maker): each game's triangle budget, picture style and licence rule; the list a library keeps of
them; the briefs a game starts from; and the pack's licence gate. Nothing here calls ComfyUI or Blender."""
import hashlib
import json
from pathlib import Path

import pytest

from npcgen import creature_projects as projects
from npcgen import creaturemaker, creatures, creaturepack
from test_creature_freshness import FakeJob, FakeWorker, make_chain, new_store


class FakeLibrary:
    def __init__(self, root):
        self.root = Path(root)


def game(**changes):
    """A game's fields as the Projects panel sends them."""
    base = {"name": "Third Game", "tri_budget": 8000, "style_prefix": "Stylised 3D game concept art, render3d style,",
            "negative_extra": "", "background": "Flat solid green background.", "commercial": True, "engine": "godot-4",
            "pack_dir": "", "key_prefix": "", "clip_map": {}, "licence_acceptance": None, **changes}
    return base


def acceptance(**changes):
    return {"verdict": "conditions", "accepted_by": "Test Director", "date": "2026-10-17", "reason": "a test game", **changes}


def pack_ready(tmp_path, project, body_plan="serpentine"):
    """A creature in `project` with a fresh chain and a game model report, as the prep step leaves them."""
    store, cid = new_store(tmp_path, project=project, body_plan=body_plan)
    make_chain(store, cid)
    creature = store.get(cid)
    report = {"plan": creatures.PLANS[body_plan]["prep"], "triangles_after": 5000, "size_m": [0.5, 2.0, 0.8], "elongation": 2.3,
              "raw_sha256": hashlib.sha256(b"raw").hexdigest(), "settings": creatures.prep_settings(creature)}
    store.write_file(cid, "prep.json", json.dumps(report))
    return store, cid


# ---- the default list and the library's own copy ----------------------------------------------------------------------------------------

def test_the_default_list_has_the_two_games_with_their_budgets_and_licence_rules(tmp_path):
    registry = projects.ProjectRegistry(tmp_path)
    wots = registry.get("ember-hollow")
    assert wots["tri_budget"] == 20000 and wots["commercial"] is True
    assert wots["licence_acceptance"] == {"verdict": "conditions", "accepted_by": "Studio lead", "date": "2026-10-16",
                                          "reason": wots["licence_acceptance"]["reason"]}
    dust_moon = registry.get("dust-moon")
    assert dust_moon["tri_budget"] == 6000 and dust_moon["commercial"] is True
    assert dust_moon["licence_acceptance"] is None                       # pending the owner: nothing is accepted on their behalf
    assert dust_moon["clip_map"]["serpentine"]["idle"] == "Idle"


def test_the_first_look_copies_the_default_list_into_the_library_and_later_edits_stay(tmp_path):
    registry = projects.ProjectRegistry(tmp_path)
    assert registry.get("dust-moon")["name"] == "Dust Moon"
    assert (tmp_path / projects.LIVE_FILE).exists()
    registry.put("dust-moon", game(name="Dust Moon (renamed)", tri_budget=6000), creatures.BODY_PLANS)
    assert projects.ProjectRegistry(tmp_path).get("dust-moon")["name"] == "Dust Moon (renamed)"
    assert [p["slug"] for p in projects.ProjectRegistry(tmp_path).list()] == ["ember-hollow", "dust-moon"]


def test_a_third_game_is_created_and_listed_beside_the_others(tmp_path):
    registry = projects.ProjectRegistry(tmp_path)
    saved = registry.put("third-game", game(), creatures.BODY_PLANS)
    assert saved["slug"] == "third-game" and saved["tri_budget"] == 8000
    assert [p["slug"] for p in registry.list()] == ["ember-hollow", "dust-moon", "third-game"]


def test_a_saved_acceptance_keeps_its_date_and_reason(tmp_path):
    registry = projects.ProjectRegistry(tmp_path)
    saved = registry.put("third-game", game(licence_acceptance=acceptance(accepted_by=" Test Director ")), creatures.BODY_PLANS)
    assert saved["licence_acceptance"] == {"verdict": "conditions", "accepted_by": "Test Director", "date": "2026-10-17",
                                           "reason": "a test game"}


@pytest.mark.parametrize("slug, changes, message", [
    ("Bad Slug", {}, "slug"),
    ("third-game", {"tri_budget": 30000}, "triangle budget"),
    ("third-game", {"tri_budget": "lots"}, "whole number"),
    ("third-game", {"commercial": "yes"}, "sold commercially"),
    ("third-game", {"engine": "unity"}, "engine"),
    ("third-game", {"clip_map": {"biped": {"idle": "Idle"}}}, "does not know"),
    ("third-game", {"clip_map": {"serpentine": {"idle": ""}}}, "clip names"),
    ("third-game", {"licence_acceptance": acceptance(verdict="yes")}, 'Only a licence rated "conditions"'),
    ("third-game", {"licence_acceptance": acceptance(date="16/10/2026")}, "YYYY-MM-DD"),
    ("third-game", {"licence_acceptance": acceptance(reason="")}, "Say why"),
    ("third-game", {"key_prefix": "Dust!"}, "key prefix"),
])
def test_a_game_is_checked_before_it_is_saved(tmp_path, slug, changes, message):
    with pytest.raises(ValueError, match=message):
        projects.ProjectRegistry(tmp_path).put(slug, game(**changes), creatures.BODY_PLANS)


# ---- a creature's budget and style come from its game -------------------------------------------------------------------------------

def test_a_creature_takes_its_games_budget_unless_it_names_one(tmp_path):
    dust_moon = projects.ProjectRegistry(tmp_path).get("dust-moon")
    base = {"name": "Burrower", "body_plan": "serpentine", "project": "dust-moon", "prompt": "a worm", "seed": 1}
    assert creatures.clean_creature({**base, "tris": None}, dust_moon)["tris"] == 6000
    assert creatures.clean_creature({**base, "tris": 4000}, dust_moon)["tris"] == 4000
    assert creatures.clean_creature({**base, "tris": None})["tris"] == creatures.TRIS_DEFAULT      # no game record: the default


def test_a_concept_takes_the_games_style_and_background_once():
    style = {"style_prefix": "Stylised 3D game concept art, render3d style,", "background": "Flat solid green background.",
             "negative_extra": "photo-real, watermark"}
    text, negative = projects.concept_text(style, "a hound, seen from the side", "photograph, text")
    assert text == "Stylised 3D game concept art, render3d style, a hound, seen from the side. Flat solid green background."
    assert negative == "photograph, text, photo-real, watermark"
    assert projects.concept_text(style, text, negative) == (text, negative)                  # drawn again: nothing is added twice


def test_a_prompt_that_already_has_the_style_and_background_is_drawn_as_it_was_written():
    wots = {"style_prefix": "Stylised 3D game concept art, render3d style,", "background": "Flat solid green background.",
            "negative_extra": ""}
    prompt = "Stylised 3D game concept art, render3d style, Burrower, seen from the side. Flat solid green background."
    assert projects.concept_text(wots, prompt, "photograph") == (prompt, "photograph")
    assert projects.concept_text(None, "a hound", "photograph") == ("a hound", "photograph")


def test_a_creature_in_a_new_game_is_drawn_in_that_games_style(tmp_path, monkeypatch):
    store, cid = new_store(tmp_path, project="third-game")
    store.projects.put("third-game", game(style_prefix="Painted watercolour concept art,", background="Plain grey background."),
                       creatures.BODY_PLANS)
    drawn = {}

    def fake_image(comfy, engine, prompt, negative, *args, **kwargs):
        drawn.update(prompt=prompt, negative=negative)
        return b"png", {"steps": 4}

    monkeypatch.setattr(creaturemaker.engines, "image", fake_image)
    creaturemaker.run_concept(FakeWorker(store), FakeJob(), {"kind": "creature_concept"}, store.get(cid))
    assert drawn["prompt"] == "Painted watercolour concept art, a hound. Plain grey background."
    assert json.loads(store.read_file(cid, "concept.json"))["prompt"] == drawn["prompt"]


# ---- the briefs a game starts from -------------------------------------------------------------------------------------------------

def test_a_brief_is_listed_once_per_game_and_a_key_may_repeat_across_games(tmp_path):
    store = creatures.CreatureStore(FakeLibrary(tmp_path))
    store.create(creatures.clean_creature({"name": "Other", "body_plan": "serpentine", "project": "other-game",
                                           "key": "dustmoon-burrower", "prompt": "x", "length_m": 2.0, "segments": 12, "seed": 1}))
    assert store.seed("dust-moon")["added"] == 1
    assert store.seed("dust-moon")["added"] == 0
    listed = [store.get(s["id"]) for s in store.list()]
    assert ("other-game", "dustmoon-burrower") in [(c["project"], c["key"]) for c in listed]
    burrower = next(c for c in listed if c["project"] == "dust-moon")
    assert burrower["tris"] == 6000 and burrower["length_m"] == 2.0 and burrower["body_plan"] == "serpentine"


def test_the_bestiary_adds_every_game_with_a_brief_file(tmp_path):
    store = creatures.CreatureStore(FakeLibrary(tmp_path))
    store.seed_all()
    projects_listed = {c["project"] for c in (store.get(s["id"]) for s in store.list())}
    assert projects_listed == {"ember-hollow", "dust-moon"}


def test_a_brief_for_an_unsafe_game_name_is_not_read():
    assert creatures.load_briefs("../escape") == [] and creatures.load_briefs("Not A Slug") == []


# ---- the licence rule for a game ----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("verdict, sold, accepted, refused", [
    ("yes", True, False, False),
    ("conditions", True, False, True),
    ("conditions", True, True, False),
    ("unclear", True, True, True),
    ("no", True, True, True),
    ("none", True, True, True),
    ("conditions", False, False, False),
    ("unclear", False, False, False),
    ("no", False, False, False),
    ("none", False, False, True),
])
def test_the_licence_rule_for_a_game(verdict, sold, accepted, refused):
    project = {"commercial": sold, "licence_acceptance": acceptance() if accepted else None}
    assert bool(projects.licence_refusal(verdict, project, "label")) is refused


def test_a_creature_whose_game_is_not_on_the_list_is_refused_a_pack():
    assert "not on the project list" in projects.licence_refusal("yes", None)


def test_a_pack_for_a_sold_game_waits_for_its_conditions_to_be_accepted(tmp_path):
    store, cid = pack_ready(tmp_path, "third-game")
    store.projects.put("third-game", game(), creatures.BODY_PLANS)
    with pytest.raises(ValueError, match="has not accepted them yet"):
        creaturepack.build(store, cid)
    store.projects.put("third-game", game(licence_acceptance=acceptance()), creatures.BODY_PLANS)
    manifest = json.loads(creaturepack.build(store, cid)["manifest.json"])
    assert manifest["accepted_licence_conditions"] == acceptance(verdict="conditions", accepted_by="Test Director",
                                                                  date="2026-10-17", reason="a test game")
    assert manifest["project"] == "third-game"


def test_dust_moons_burrower_is_refused_until_the_owner_accepts_the_conditions(tmp_path):
    store, cid = pack_ready(tmp_path, "dust-moon")
    with pytest.raises(ValueError, match="has not accepted them yet"):
        creaturepack.build(store, cid)


# ---- what the review found: a game by its name, the budget of an old record, whole numbers, no replacing ------------------------------

def test_a_game_given_by_its_name_is_found_by_its_slug(tmp_path):
    store = creatures.CreatureStore(FakeLibrary(tmp_path))
    assert creatures.project_slug("Dust Moon") == "dust-moon"
    assert store.game("Dust Moon")["tri_budget"] == 6000
    assert store.game("No Such Game") is None


def test_a_creature_given_its_game_by_name_takes_the_games_budget(tmp_path):
    store = creatures.CreatureStore(FakeLibrary(tmp_path))
    base = {"name": "Burrower", "body_plan": "serpentine", "project": "Dust Moon", "prompt": "a worm", "seed": 1}
    clean = creatures.clean_creature({**base, "tris": None}, store.game("Dust Moon"))
    assert clean["project"] == "dust-moon" and clean["tris"] == 6000


def test_a_record_with_no_budget_takes_its_games_budget_when_it_is_read_and_when_it_is_prepped(tmp_path):
    store, cid = new_store(tmp_path, project="dust-moon")
    path = store.resolve(cid, "creature.json")
    record = json.loads(path.read_text(encoding="utf-8"))
    record.pop("tris", None)                                              # a record from before the game budgets
    path.write_text(json.dumps(record), encoding="utf-8")
    assert store.get(cid)["tris"] == 6000
    command = creaturemaker.prep_command(store.get(cid), Path("raw.glb"), Path("model.part.glb"), "serpentine")
    assert command[command.index("--tris") + 1] == "6000"


def test_seeding_takes_a_game_named_in_full_and_refuses_what_is_not_text(tmp_path):
    store = creatures.CreatureStore(FakeLibrary(tmp_path))
    assert store.seed(creatures.project_slug("Dust Moon"))["added"] == 1
    assert creatures.load_briefs(5) == [] and creatures.load_briefs(True) == [] and creatures.load_briefs(["x"]) == []


def test_a_key_listed_twice_in_one_brief_file_is_added_once(tmp_path):
    store = creatures.CreatureStore(FakeLibrary(tmp_path))
    brief = {"key": "dup-1", "name": "Dup", "body_plan": "serpentine", "prompt": "a hound", "makeable": True, "length_m": 2.0}
    assert store.seed("third-game", briefs=[brief, brief])["added"] == 1
    assert len([s for s in store.list() if s["project"] == "third-game"]) == 1


def test_a_save_that_leaves_out_the_acceptance_keeps_the_recorded_one(tmp_path):
    registry = projects.ProjectRegistry(tmp_path)
    current = registry.get("ember-hollow")
    fields = {k: v for k, v in current.items() if k not in ("slug", "licence_acceptance")}
    fields["tri_budget"] = 15000
    registry.put("ember-hollow", fields, creatures.BODY_PLANS)
    kept = registry.get("ember-hollow")
    assert kept["tri_budget"] == 15000 and kept["licence_acceptance"]["accepted_by"] == "Studio lead"
    registry.put("ember-hollow", {**fields, "licence_acceptance": None}, creatures.BODY_PLANS)
    assert registry.get("ember-hollow")["licence_acceptance"] is None           # only an explicit null removes it


def test_a_new_game_never_replaces_one_that_exists(tmp_path):
    registry = projects.ProjectRegistry(tmp_path)
    with pytest.raises(projects.ProjectExists, match="already exists"):
        registry.create("ember-hollow", game(licence_acceptance=None), creatures.BODY_PLANS)
    assert registry.get("ember-hollow")["licence_acceptance"]["accepted_by"] == "Studio lead"
    assert registry.create("third-game", game(), creatures.BODY_PLANS)["slug"] == "third-game"


def test_a_fraction_of_a_triangle_is_refused_and_a_whole_one_is_kept(tmp_path):
    registry = projects.ProjectRegistry(tmp_path)
    with pytest.raises(ValueError, match="whole number"):
        registry.put("third-game", game(tri_budget=6000.7), creatures.BODY_PLANS)
    assert registry.put("third-game", game(tri_budget=6000.0), creatures.BODY_PLANS)["tri_budget"] == 6000


def test_an_infinite_number_is_a_value_error_not_a_crash(tmp_path):
    with pytest.raises(ValueError, match="whole number"):
        projects.ProjectRegistry(tmp_path).put("third-game", game(tri_budget=float("inf")), creatures.BODY_PLANS)
    with pytest.raises(ValueError):
        creatures.clean_creature({"name": "x", "body_plan": "serpentine", "prompt": "a", "seed": 1, "segments": float("inf")})


def test_an_extra_negative_word_is_added_when_it_is_a_whole_term_and_only_once():
    base = creaturemaker.CONCEPT_NEGATIVE
    project = {"negative_extra": "sign, limb, rain"}
    text, negative = projects.concept_text(project, "a hound", base)
    assert negative == base + ", sign, limb, rain"                       # "limb" is not "extra limbs": it is added
    assert projects.concept_text(project, text, negative) == (text, negative)


def test_an_extra_word_already_a_term_of_the_negative_is_not_added_again():
    base = creaturemaker.CONCEPT_NEGATIVE
    assert projects.concept_text({"negative_extra": "watermark"}, "a hound", base)[1] == base


def test_the_dust_moon_clip_names_are_the_states_the_game_loader_reads():
    # scripts/creatures/creature_clips.gd in Dust Moon keys the Burrower's clips by these states.
    data = json.loads(projects.DEFAULT_PATH.read_text(encoding="utf-8"))
    clips = next(p for p in data["projects"] if p["slug"] == "dust-moon")["clip_map"]["serpentine"]
    assert set(clips) == {"idle", "move", "attack", "hurt", "die"} and clips["move"] == "Swim" and clips["die"] == "Death"
