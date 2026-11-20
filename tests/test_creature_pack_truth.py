"""The pack tells the truth (Phase 3 of the creature maker): the forward axis a pack states is the one its prep makes, only the shape
checks that apply to a body plan run, and a failing check stops the pack. Nothing here runs Blender: the game model's report is
written by hand with the numbers the prep step measures (the prep itself is tested in test_prep_blender.py)."""
import hashlib
import json

import pytest

from npcgen import creatures, creaturepack
from npcgen.blender import prep_rules as rules
from test_creature_freshness import make_chain, new_store

PACKABLE = [plan for plan in creatures.BODY_PLANS if "3d" in creatures.PLANS[plan]["make"]]   # a picture-only plan packs nothing


def chain(tmp_path, body_plan, **report):
    """A creature of this body plan with a fresh chain, and its game model's report with the numbers given."""
    store, cid = new_store(tmp_path, body_plan=body_plan)
    make_chain(store, cid)
    creature = store.get(cid)
    prep = {"plan": creatures.PLANS[body_plan]["prep"], "triangles_after": 5000, "size_m": [0.5, 2.0, 0.8],
            "raw_sha256": hashlib.sha256(b"raw").hexdigest(), "settings": creatures.prep_settings(creature), **report}
    store.write_file(cid, "prep.json", json.dumps(prep))
    return store, cid


def manifest_of(store, cid):
    return json.loads(creaturepack.build(store, cid)["manifest.json"])


# ---- the forward axis ------------------------------------------------------------------------------------------------------------------

def test_every_body_plan_states_plus_z_as_its_forward_axis():
    assert {plan: creatures.PLANS[plan]["facing_after_prep"] for plan in creatures.BODY_PLANS} == {plan: "+Z" for plan in creatures.BODY_PLANS}


@pytest.mark.parametrize("body_plan", PACKABLE)
def test_the_pack_states_the_forward_axis_of_its_own_plan(tmp_path, body_plan):
    store, cid = chain(tmp_path, body_plan, elongation=2.3)
    assert manifest_of(store, cid)["forward"] == creatures.PLANS[body_plan]["facing_after_prep"] == "+Z"


def test_the_forward_axis_is_read_from_the_plan_not_written_into_the_pack(tmp_path, monkeypatch):
    monkeypatch.setitem(creatures.PLANS["flying"], "facing_after_prep", "+X")      # a plan that said otherwise
    store, cid = chain(tmp_path, "flying", elongation=2.3)
    assert manifest_of(store, cid)["forward"] == "+X"


@pytest.mark.parametrize("facing", ["left", "right"])
def test_a_static_head_ends_on_plus_z_in_the_glb_axes(facing):
    # Blender's axes (Z up) become glTF's (Y up) as (x, y, z) -> (x, z, -y): the head at Blender -Y is glTF +Z, which is forward.
    x, y, z = rules.apply(rules.static_rotation(facing, rules.raw_up(-0.046)), rules.FACING_HEAD[facing])
    assert (x, z, -y) == pytest.approx((0.0, 0.0, 1.0))


# ---- which checks apply ---------------------------------------------------------------------------------------------------------------

UP_CONFIRMED = {"verdict": "confirmed", "standing": 0.9, "turned_over": 0.3, "margin": 0.05}


@pytest.mark.parametrize("body_plan, applies", [("serpentine", ["elongation"]), ("six-leg", ["taller_than_long", "upright_picture"]),
                                                ("quadruped", ["taller_than_long", "upright_picture"]),
                                                ("flying", ["upright_picture"]), ("prop", ["upright_picture"])])
def test_each_body_plan_runs_only_the_shape_checks_that_apply_to_it(tmp_path, body_plan, applies):
    store, cid = chain(tmp_path, body_plan, elongation=2.3, up_check=UP_CONFIRMED)
    checks = manifest_of(store, cid)["checks"]
    assert checks["applies"] == applies
    assert ("taller_than_long" in checks) == ("taller_than_long" in applies)
    assert ("elongation" in checks) == ("elongation" in applies)
    assert ("upright_picture" in checks) == ("upright_picture" in applies)


def test_a_ground_body_standing_taller_than_it_is_long_is_not_packed(tmp_path):
    store, cid = chain(tmp_path, "six-leg", size_m=[0.5, 1.0, 1.5], up_check=UP_CONFIRMED)
    with pytest.raises(ValueError, match="does not pass its shape check.*Taller than it is long"):
        creaturepack.build(store, cid)


def test_a_flyer_standing_taller_than_it_is_long_is_packed_with_no_ground_check(tmp_path):
    store, cid = chain(tmp_path, "flying", size_m=[0.5, 1.0, 1.5], up_check=UP_CONFIRMED)
    manifest = manifest_of(store, cid)
    assert manifest["checks"]["applies"] == ["upright_picture"] and manifest["checks"]["blocking"] == []
    assert "taller_than_long" not in manifest["checks"]
    assert manifest["warnings"] == []


def test_a_static_body_turned_over_by_its_picture_is_not_packed(tmp_path):
    turned = {"verdict": "contradicted", "standing": 0.3, "turned_over": 0.9, "margin": 0.05}
    store, cid = chain(tmp_path, "prop", up_check=turned)
    with pytest.raises(ValueError, match="does not pass its shape check.*Turned over"):
        creaturepack.build(store, cid)


def test_a_static_body_whose_picture_is_unclear_is_packed_with_a_warning(tmp_path):
    unclear = {"verdict": "unclear", "standing": 0.5, "turned_over": 0.5, "margin": 0.05}
    store, cid = chain(tmp_path, "flying", up_check=unclear)
    manifest = manifest_of(store, cid)
    assert manifest["checks"]["blocking"] == []
    assert any("not clear from the picture" in warning for warning in manifest["warnings"])


def test_a_static_body_made_before_the_picture_check_is_packed_with_a_warning(tmp_path):
    store, cid = chain(tmp_path, "prop")
    manifest = manifest_of(store, cid)
    assert manifest["checks"]["blocking"] == []
    assert any("not checked against its picture" in warning for warning in manifest["warnings"])


def test_a_static_body_whose_picture_could_not_be_cut_out_says_why(tmp_path):
    cannot = {"verdict": None, "reason": "ComfyUI is not running"}
    store, cid = chain(tmp_path, "six-leg", size_m=[1.0, 1.0, 0.5], up_check=cannot)
    manifest = manifest_of(store, cid)
    assert manifest["checks"]["blocking"] == []
    assert any("ComfyUI is not running" in warning for warning in manifest["warnings"])


def test_taller_than_long_compares_the_height_with_the_length_alone():
    # Deeper than it is long, and taller than it is long: the depth must not hide the height.
    checks = creatures.shape_checks("quadruped", {"size_m": [1.5, 1.0, 1.2]})
    assert checks["taller_than_long"] is True and checks["blocking"] == ["taller_than_long"]
    assert creatures.shape_checks("quadruped", {"size_m": [1.5, 1.0, 0.9]})["taller_than_long"] is False
    assert creatures.shape_checks("quadruped", {"size_m": [1.5, 1.0, 1.0]})["taller_than_long"] is False      # as tall as it is long


def test_a_ground_body_whose_size_was_not_measured_packs_with_a_warning_not_silently(tmp_path):
    store, cid = chain(tmp_path, "six-leg", size_m=None)
    manifest = manifest_of(store, cid)
    assert manifest["checks"]["taller_than_long"] is None and manifest["checks"]["blocking"] == []
    assert any("size was not measured" in warning for warning in manifest["warnings"])


def test_a_size_with_too_few_numbers_is_a_warning_and_not_a_crash(tmp_path):
    checks = creatures.shape_checks("six-leg", {"size_m": [0.5, 1.0]})
    assert checks["taller_than_long"] is None and checks["warnings"]
    store, cid = chain(tmp_path, "serpentine", elongation="long")       # a number of the wrong kind is not measured either
    assert store.report(cid)["checks"]["elongation"] is None


# ---- the serpent's elongation -----------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("elongation, blocking", [(2.2, []), (2.19, ["elongation"]), (1.01, ["elongation"])])
def test_a_serpent_is_refused_below_its_limit_and_not_at_it(elongation, blocking):
    assert creatures.shape_checks("serpentine", {"elongation": elongation})["blocking"] == blocking


def test_a_coil_is_not_packed(tmp_path):
    store, cid = chain(tmp_path, "serpentine", elongation=1.01)
    with pytest.raises(ValueError, match="does not pass its shape check.*Too short to be a serpent.*Redraw the picture"):
        creaturepack.build(store, cid)


def test_a_coil_whose_report_is_gone_is_not_packed_from_its_model_alone(tmp_path):
    store, cid = chain(tmp_path, "serpentine", elongation=1.01)
    store.resolve(cid, "prep.json").unlink()                       # the model is still on disk
    with pytest.raises(ValueError, match=r"report \(prep\.json\) is missing"):
        creaturepack.build(store, cid)


def test_a_body_plan_the_maker_does_not_know_is_not_packed(tmp_path):
    store, cid = chain(tmp_path, "six-leg")
    store.update(cid, lambda c: c.update(body_plan="six-legged-old"))
    with pytest.raises(ValueError, match="not one the creature maker knows"):
        creaturepack.build(store, cid)


def test_the_licence_is_refused_before_the_shape_checks(tmp_path):
    store, cid = chain(tmp_path, "serpentine", elongation=1.01)
    store.write_file(cid, "concept.json", json.dumps({"engine": "mystery-engine", "seed": 202,
                                                      "sha256": hashlib.sha256(b"picture-a").hexdigest()}))
    with pytest.raises(ValueError, match="not cleared for commercial work"):
        creaturepack.build(store, cid)


def test_a_serpent_made_before_its_elongation_was_measured_packs_with_a_warning(tmp_path):
    store, cid = chain(tmp_path, "serpentine")                  # no elongation in its report
    manifest = manifest_of(store, cid)
    assert manifest["checks"]["blocking"] == []
    assert any("elongation was not measured" in warning for warning in manifest["warnings"])


# ---- the licence acceptance -------------------------------------------------------------------------------------------------------------

def test_the_manifest_carries_the_acceptance_its_game_recorded(tmp_path):
    store, cid = chain(tmp_path, "serpentine", elongation=2.3)           # the Ember Hollow game, which has accepted them
    manifest = manifest_of(store, cid)
    assert manifest["accepted_licence_conditions"]["accepted_by"] == "Studio lead"
    assert manifest["licence"]["verdict"] == "conditions"


def test_a_size_that_is_not_a_number_is_not_measured_and_says_so():
    # NaN is a number to Python's isinstance check, so a NaN size used to pass the ground check with no warning at all.
    checks = creatures.shape_checks("six-leg", {"size_m": [0.5, 1.0, float("nan")]})
    assert checks["taller_than_long"] is None and checks["blocking"] == []
    assert any("size was not measured" in warning for warning in checks["warnings"])


def test_an_elongation_that_is_not_a_number_is_not_measured_and_says_so():
    checks = creatures.shape_checks("serpentine", {"elongation": float("inf")})
    assert checks["elongation"] is None and checks["blocking"] == []
    assert any("elongation was not measured" in warning for warning in checks["warnings"])
