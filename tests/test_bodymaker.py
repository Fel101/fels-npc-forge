"""npcgen/bodymaker.py: a designed body as a character's 3D model. The request and the recipe, what is looked at before a body is used, what replacing a model
removes, the job step (cancelled, failing, done), the routes; and one real build, which skips where the Body Designer, Blender or the animation library is not there."""
import copy
import hashlib
import importlib
import json
import pathlib
import struct
import threading
import time
import urllib.error
import urllib.request

import pytest

from npcgen import bodymaker, bodymatch, check3d, cleanup3d, config, marks, morphology, service
from npcgen.bodymaker import BodyError
from npcgen.comfy import Cancelled
from npcgen.jobs import Jobs
from npcgen.library import Library

PROFILE = {"first_name": "Body", "last_name": "Test", "age": 30, "gender": "female", "occupation": "tester"}
FEMALE, MALE = "forge-anatomical-female-v7", "forge-anatomical-male-v7"
CLIPS = [f"Clip{i:02d}" for i in range(82)] + ["Idle", "Walk", "Wave"]
needs_designer = pytest.mark.skipif(not bodymaker.bases(), reason="the Body Designer is not installed")
HAS_LONG = bool(bodymaker.bases()) and "long" in bodymaker.hair_styles()        # (what a sheet that says long hair gets depends on what this Body Designer makes)


def glb(doc):
    """A GLB with only a JSON chunk: enough for what bodymaker looks at."""
    raw = json.dumps(doc).encode()
    raw += b" " * (-len(raw) % 4)
    return struct.pack("<4sII", b"glTF", 2, 20 + len(raw)) + struct.pack("<I4s", len(raw), b"JSON") + raw


def body_doc(skeleton="low", minimum_age=18, tier="game", joints=None):
    joints = joints if joints is not None else {"low": 23, "medium": 60}[skeleton]
    return {"asset": {"version": "2.0", "extras": {"export_tier": tier, "minimum_age": minimum_age, "skeleton": skeleton}},
            "nodes": [{"name": f"bone{i}"} for i in range(joints)], "skins": [{"joints": list(range(joints))}]}


def animated_doc(plain_doc, names=CLIPS):
    doc = copy.deepcopy(plain_doc)
    doc["animations"] = [{"name": n, "extras": {"seconds": 1.0, "loop": n != "Wave", "source": f"lib: {n}"}} for n in names]
    return doc


def fake_build(skeleton="low", names=CLIPS, wait=None, error=None, calls=None):
    """A stand-in for bodymaker.build: two small GLBs and the reports, after waiting for `wait` (a cancel can arrive meanwhile)."""
    def build(cfg, recipe, tier, cancel=None, note=None, texture=2048, npc=None, clothes=()):
        if calls is not None:
            calls.append({"recipe": recipe, "skeleton": tier, "texture": texture, "npc": npc and npc["id"], "clothes": list(clothes)})
        if note:
            note("Building")
        if wait is not None:
            while not wait.is_set():
                if cancel is not None and cancel.is_set():
                    raise Cancelled()
                time.sleep(0.01)
        if error:
            raise error
        plain = body_doc(tier)
        return glb(plain), glb(animated_doc(plain, names)), {"build": {"triangles": 43268, "size_m": [0.5, 1.62, 0.3], "sha256": "abc"},
                                                              "copy": {}, "seconds": 12.5}
    return build


@pytest.fixture
def lib(tmp_path):
    return Library(tmp_path / "library")


def new_npc(lib, model=False, gender="female", **more):
    npc = lib.create({"setting": "modern", "style": "pixel", "seed": 1, "profile": {**PROFILE, "gender": gender}, "appearance": {}, **more})
    if model:
        lib.write_asset(npc["id"], "model_3d.glb", b"old model")
        lib.write_asset(npc["id"], "model_3d_original.glb", b"old original")
        lib.write_asset(npc["id"], "model_3d_old_preview.png", b"old picture")
        lib.write_asset(npc["id"], "model_3d_rigged.glb", b"old rig")
        lib.write_asset(npc["id"], "model_3d_rigged.png", b"old rig picture")
        lib.write_asset(npc["id"], cleanup3d.ORIGINAL_FILE, b"kept from the clean-up")
        lib.write_asset(npc["id"], check3d.BOARD_FILE, b"a board")
        lib.write_asset(npc["id"], "model_3d_head/front.png", b"close-up")
        lib.write_asset(npc["id"], "portrait.png", b"a portrait")

        def apply(n):
            n["assets"] = {"portrait": {"file": "portrait.png", "created": "2026-10-01T10:00:00"},
                           "model_3d": {"file": "model_3d.glb", "raw": "model_3d_original.glb", "preview": "model_3d_old_preview.png",
                                        "created": "2026-10-01T10:00:00", "refined": {"created": "2026-10-02T10:00:00"}},
                           "rig_3d": {"file": "model_3d_rigged.glb", "preview": "model_3d_rigged.png", "created": "2026-10-01T11:00:00"}}
        lib.update(npc["id"], apply)
    return npc["id"]


def request(**more):
    return bodymaker.clean_request({"base": FEMALE, **more})


# ---- the request -----------------------------------------------------------------------------------------------------------------------------
def test_nothing_is_requested_unless_a_request_is_made_and_a_plain_yes_means_the_defaults():
    for nothing in (None, False, {}):
        assert bodymaker.clean_request(nothing) is None
    assert bodymaker.clean_request(True) == {"base": None, "skeleton": "low", "values": None, "saved": False, "replace": False, "face": None, "texture": 2048,
                                             "match": True, "clothes": "none", "outer": "sheet", "colours": {}, "hair": None, "marks": None, "expressions": None, "secondary": None, "soft": {}, "bust": 0.0}      # (a call that says nothing gets what it always got: underwear)


def test_a_request_is_checked_for_its_shape_and_says_which_field_is_wrong():
    ok = bodymaker.clean_request({"base": MALE, "skeleton": "medium", "values": {"height": 0.5, "fat": -1}, "saved": True, "replace": True,
                                  "face": "classic", "texture": 1024, "match": False, "clothes": "sheet", "outer": "jacket", "colours": {"jacket": "#ABCDEF", "shirt": None}, "hair": "sweep",
                                  "marks": "none", "expressions": False, "secondary": {"hair": True}})
    assert ok == {"base": MALE, "skeleton": "medium", "values": {"height": 0.5, "fat": -1.0}, "saved": True, "replace": True, "face": "classic", "texture": 1024,
                  "match": False, "clothes": "sheet", "outer": "jacket", "colours": {"jacket": "#abcdef"}, "hair": "sweep", "marks": "none", "expressions": False,
                  "secondary": {"hair": True, "cloth": False}, "soft": {}, "bust": 0.0}
    for raw, field in [("low", "body_3d must be"), ({"extra": 1}, "body_3d.extra"), ({"skeleton": "high"}, "skeleton"), ({"base": 5}, "base"),
                       ({"values": []}, "values"), ({"values": {"height": 1.5}}, "values.height"), ({"values": {"height": True}}, "values.height"),
                       ({"values": {"height": float("nan")}}, "values.height"), ({"values": {"height": "0.1"}}, "values.height"), ({"recipe": 3}, "recipe"),
                       ({"face": "round"}, "body_3d.face must be natural or classic"), ({"face": True}, "face"), ({"texture": 4096}, "body_3d.texture must be 2048, 1024, 512"),
                       ({"texture": "1024"}, "texture"), ({"texture": True}, "texture"), ({"texture": 1024.0}, "texture"),
                       ({"match": "yes"}, "body_3d.match must be true or false"), ({"match": 0}, "match"), ({"match": None}, "match"),
                       ({"clothes": "suit"}, "body_3d.clothes must be basic, sheet, none"), ({"clothes": True}, "clothes"), ({"clothes": None}, "clothes"),
                       ({"outer": "coat"}, "body_3d.outer must be sheet, none, jacket, apron"), ({"outer": True}, "outer"), ({"outer": None}, "outer"),
                       ({"colours": []}, "body_3d.colours must be an object"), ({"colours": {"hat": "#112233"}}, "body_3d.colours.hat: not a garment"),
                       ({"colours": {"shirt": "red"}}, "body_3d.colours.shirt: the colour must be like #3f6ba8"), ({"colours": {"shirt": "#12345"}}, "colours.shirt"),
                       ({"colours": {"shirt": 5}}, "colours.shirt"),
                       ({"hair": "bob"}, "body_3d.hair must be sheet, none, crop, sweep, long"), ({"hair": True}, "hair"), ({"hair": ""}, "hair"),
                       ({"marks": "freckles"}, "body_3d.marks must be sheet or none"), ({"marks": True}, "marks"), ({"marks": ""}, "marks"),
                       ({"expressions": "yes"}, "body_3d.expressions must be true or false"), ({"expressions": 1}, "expressions"), ({"expressions": 0}, "expressions"),
                       ({"secondary": "yes"}, "body_3d.secondary must be"), ({"secondary": {"mane": True}}, "body_3d.secondary must be"), ({"secondary": {"hair": 1}}, "body_3d.secondary.hair must be true or false")]:
        with pytest.raises(ValueError, match=field):
            bodymaker.clean_request(raw)
    assert bodymaker.clean_request({"replace": "yes", "saved": 1})["replace"] is False             # only a real true says yes


def test_a_designed_body_is_asked_for_on_its_own_and_is_a_whole_body_request():
    data = {"expressions": [{"id": "happy"}]}
    assets = service.clean_assets({"body_3d": {"base": FEMALE}}, data)
    assert assets["body_3d"]["base"] == FEMALE and not assets["portrait"]                          # (it counts as something to make)
    for clash in ("model_3d", "rig_3d", "refine_3d", "best_3d"):
        with pytest.raises(ValueError, match="on its own"):
            service.clean_assets({"body_3d": True, clash: True}, data)
    assert morphology.needs_full_body({"body_3d": {"base": FEMALE}})


# ---- the recipe -------------------------------------------------------------------------------------------------------------------------------
@needs_designer
def test_the_recipe_is_settled_from_the_base_the_gender_and_the_proportions_asked_for(lib):
    woman, man = lib.get(new_npc(lib)), lib.get(new_npc(lib, gender="male"))
    chosen = bodymaker.prepare(lib, woman, request(values={"height": 0.2, "muscle": -0.5}))
    assert chosen["base"] == FEMALE and chosen["base_label"] == "Adult female natural anatomy" and chosen["recipe"]["version"] == 10
    assert chosen["recipe"]["values"]["height"] == 0.2 and chosen["recipe"]["values"]["muscle"] == -0.5 and chosen["recipe"]["values"]["fat"] == 0
    assert bodymaker.prepare(lib, woman, bodymaker.clean_request(True))["base"] == FEMALE            # no base: by the character's gender
    assert bodymaker.prepare(lib, man, bodymaker.clean_request(True))["base"] == MALE
    other = lib.get(new_npc(lib, gender="nonbinary"))
    with pytest.raises(ValueError, match="Choose a body"):
        bodymaker.prepare(lib, other, bodymaker.clean_request(True))
    with pytest.raises(ValueError, match="values.heigth"):
        bodymaker.prepare(lib, woman, request(values={"heigth": 0.2}))


@needs_designer
def test_a_whole_recipe_is_checked_by_the_body_designers_own_validator(lib):
    woman = lib.get(new_npc(lib))
    from bodydesigner import api
    recipe = api.default_recipe(FEMALE)
    recipe["materials"]["skin_color"] = "#a0522d"
    assert bodymaker.prepare(lib, woman, request(recipe=recipe))["recipe"]["materials"]["skin_color"] == "#a0522d"
    recipe["materials"]["skin_color"] = "brown"
    with pytest.raises(ValueError, match="materials.skin_color"):
        bodymaker.prepare(lib, woman, request(recipe=recipe))


@needs_designer
def test_the_body_tabs_saved_body_is_carried_by_the_body_designers_own_rule(lib):
    npc_id = new_npc(lib)
    woman = lib.get(npc_id)
    from bodydesigner import api, model
    from bodydesigner.face_finish import PROFILES
    with pytest.raises(ValueError, match="has not saved"):
        bodymaker.prepare(lib, woman, request(saved=True))
    old = model.recipe(values={"height": 0.3, "chest": 0.4}, base="forge-humanoid-female-v14")      # an older Body-tab recipe: 21 values, its own foundation
    lib.write_asset(npc_id, "body_design.json", json.dumps(old).encode())
    assert bodymaker.status(lib, woman)["saved"] is True
    chosen = bodymaker.prepare(lib, woman, bodymaker.clean_request({"saved": True}))              # no base: the body of the saved one's sex
    assert chosen["base"] == FEMALE and chosen["saved"] and chosen["recipe"]["values"]["height"] == 0.3 and chosen["recipe"]["values"]["chest"] == 0.4
    man = bodymaker.prepare(lib, woman, request(saved=True, base=MALE))                           # another base takes the same proportions
    assert man["base"] == MALE and man["recipe"]["values"]["height"] == 0.3
    own = api.default_recipe(FEMALE)                                                               # a current recipe stays whole: colours, finish, face, locks
    own.update(PROFILES)
    own["materials"] = {**own["materials"], "iris_color": "#112233"}
    own["values"]["height"] = -0.2
    own["locks"] = ["fat"]
    lib.write_asset(npc_id, "body_design.json", json.dumps(own).encode())
    kept = bodymaker.prepare(lib, woman, request(saved=True, values={"fat": 0.1}))["recipe"]
    assert kept["materials"]["iris_color"] == "#112233" and kept["values"]["height"] == -0.2 and kept["values"]["fat"] == 0.1 and kept["locks"] == ["fat"]
    assert {k: kept[k] for k in PROFILES} == PROFILES                                              # a saved Natural face is not turned back into the classic one
    other = bodymaker.prepare(lib, woman, request(saved=True, base=MALE))["recipe"]                # another body takes only the proportions
    assert other["values"]["height"] == -0.2 and other["materials"]["iris_color"] != "#112233" and other["locks"] == []
    odd = {**old, "values": {k: v for k, v in old["values"].items() if k != "arms"}}                # a recipe that cannot be carried is refused, naming the field
    lib.write_asset(npc_id, "body_design.json", json.dumps(odd).encode())
    with pytest.raises(ValueError, match=r"saved body cannot be used \(values\.arms"):
        bodymaker.prepare(lib, woman, request(saved=True))
    whole = bodymaker.prepare(lib, woman, request(saved=True, recipe=own))                         # a whole recipe in the request is not held up by it
    assert whole["recipe"]["values"]["height"] == -0.2 and whole["saved"] is False


@needs_designer
def test_the_natural_face_is_the_default_of_a_new_body_and_every_other_body_keeps_its_own_face(lib):
    from bodydesigner import api
    from bodydesigner.face_finish import PROFILES as NO_LIPS
    from bodydesigner.lip_finish import PROFILES
    npc_id = new_npc(lib)
    woman = lib.get(npc_id)
    classic = {k: api.default_recipe(FEMALE)[k] for k in bodymaker.FACE_FIELDS}
    assert classic != PROFILES and bodymaker.natural_profiles() == PROFILES != NO_LIPS                                # the Natural face is the one with the lip colour
    assert bodymaker.face_of({**api.default_recipe(FEMALE), **NO_LIPS}) == "natural"                                  # a body saved before the lips has the Natural face too
    assert bodymaker.face_of({**api.default_recipe(FEMALE), **{**NO_LIPS, "brow_profile": classic["brow_profile"]}}) == "classic"     # (a mixture is no Natural face)
    fresh = bodymaker.prepare(lib, woman, request())
    assert fresh["face"] == "natural" and {k: fresh["recipe"][k] for k in PROFILES} == PROFILES                # a new body: the Natural face
    assert fresh["recipe"]["values"] == api.default_recipe(FEMALE)["values"]                                   # (and nothing else about it changes)
    old = bodymaker.prepare(lib, woman, request(face="classic"))
    assert old["face"] == "classic" and {k: old["recipe"][k] for k in PROFILES} == classic
    assert api.clean_recipe(old["recipe"]) == old["recipe"] and api.clean_recipe(fresh["recipe"]) == fresh["recipe"]     # the validator takes both: never a mixture
    own = api.default_recipe(FEMALE)                                                                           # a whole recipe is what it is, unless a face is asked for
    assert bodymaker.prepare(lib, woman, request(recipe=own))["face"] == "classic"
    assert bodymaker.prepare(lib, woman, request(recipe=own, face="natural"))["face"] == "natural"
    assert {k: own[k] for k in PROFILES} == classic                                                            # (the caller's recipe is not changed)
    kept = copy.deepcopy(own)
    bodymaker.with_face(api, own, "natural"), bodymaker.with_face(api, own, "classic")
    assert own == kept                                                                                         # (nor does asking for a face change the recipe it is given)
    lib.write_asset(npc_id, "body_design.json", json.dumps(own).encode())                                      # a saved body keeps its face, a face asked for wins
    assert bodymaker.prepare(lib, woman, request(saved=True))["face"] == "classic"
    assert bodymaker.prepare(lib, woman, request(saved=True, face="natural"))["face"] == "natural"
    assert bodymaker.prepare(lib, woman, request(saved=True, base=MALE))["face"] == "natural"                  # another body made from its proportions is a new body
    lib.write_asset(npc_id, "body_design.json", json.dumps({**own, **PROFILES}).encode())
    assert bodymaker.prepare(lib, woman, request(saved=True))["face"] == "natural"
    assert bodymaker.prepare(lib, woman, request(saved=True, face="classic"))["face"] == "classic"
    lib.write_asset(npc_id, "body_design.json", json.dumps({**own, **NO_LIPS}).encode())                              # a saved Natural face without lips stays as it was saved
    kept = bodymaker.prepare(lib, woman, request(saved=True))
    assert kept["face"] == "natural" and kept["recipe"]["skin_detail_profile"] == NO_LIPS["skin_detail_profile"]


@needs_designer
def test_a_polish_face_is_a_natural_face_with_profiles_of_its_own_that_a_request_cannot_turn_classic(monkeypatch):
    """The Body Designer's recipe 11 (a Natural face with eyes centred on their openings). Tried here with a stand-in for its module, so that it is the Forge's own rule that is tested."""
    import sys
    import types
    stub = types.ModuleType("bodydesigner.face_identity")
    stub.PROFILES = {"deformation_profile": "polish-deformation", "eye_profile": "polish-eyes"}
    monkeypatch.setitem(sys.modules, "bodydesigner.face_identity", stub)
    natural = bodymaker.natural_profiles()
    polish = {"version": 11, "identity": {"version": 1}, **stub.PROFILES, "brow_profile": natural["brow_profile"], "skin_detail_profile": natural["skin_detail_profile"]}
    assert bodymaker.polish_face(polish) and bodymaker.face_of(polish) == "natural"
    assert bodymaker.face_of({**polish, "skin_detail_profile": "some-other-skin"}) == "classic"      # (a Polish recipe is still no Natural face without the Natural brows and skin)
    assert bodymaker.face_of({**polish, "brow_profile": "classic-brows"}) == "classic"
    assert bodymaker.with_face(None, polish, "natural") is polish                                  # it has the Natural face already: its profiles are not set again
    assert bodymaker.with_face(None, polish, "natural", explicit=False) is polish
    with pytest.raises(ValueError, match="has no classic face"):
        bodymaker.with_face(None, polish, "classic")
    assert bodymaker.with_expressions(polish, True)["expressions"] is True                         # (face shapes need the Natural face: it is one)
    for other in ({**polish, "version": 10}, {**polish, "identity": None}, {**polish, "identity": []}, {**polish, "eye_profile": natural["eye_profile"]},
                  {**polish, "deformation_profile": "other"}, {k: v for k, v in polish.items() if k != "eye_profile"}, None, "recipe", {}):
        assert not bodymaker.polish_face(other), other
    monkeypatch.setitem(sys.modules, "bodydesigner.face_identity", None)                           # a Body Designer that does not have it does not know it
    assert not bodymaker.polish_face(polish) and bodymaker.face_of(polish) == "classic"


def has_polish_face():
    try:
        return bool(bodymaker.bases()) and hasattr(bodymaker._api(), "upgrade_recipe")
    except BodyError:
        return False


@needs_designer
@pytest.mark.skipif(not has_polish_face(), reason="this Body Designer has no Polish face (recipe 11)")
def test_a_body_with_the_designers_polish_face_is_used_like_a_natural_one_and_is_never_made_classic(lib):
    from bodydesigner import api
    npc_id = new_npc(lib)
    woman = lib.get(npc_id)
    own = api.upgrade_recipe({**api.default_recipe(FEMALE), **bodymaker.natural_profiles()})
    assert own["version"] == 11 and bodymaker.polish_face(own) and bodymaker.face_of(own) == "natural"
    made = bodymaker.prepare(lib, woman, request(recipe=own, expressions=True, hair="sweep"))
    assert made["face"] == "natural" and made["recipe"]["version"] == 11 and made["recipe"]["expressions"] is True and made["recipe"]["identity"] == own["identity"]
    assert made["recipe"]["hair"]["style"] == "sweep"
    assert bodymaker.prepare(lib, woman, request(recipe=own, face="natural"))["recipe"] == api.clean_recipe(own)      # (asking for the Natural face it has changes nothing)
    with pytest.raises(ValueError, match="has no classic face"):
        bodymaker.prepare(lib, woman, request(recipe=own, face="classic"))
    lib.write_asset(npc_id, "body_design.json", json.dumps(own).encode())                           # saved in the Body Designer tab
    saved = bodymaker.prepare(lib, woman, request(saved=True))
    assert saved["face"] == "natural" and saved["recipe"]["identity"] == own["identity"] and saved["saved"] is True
    assert bodymaker.prepare(lib, woman, request(saved=True, expressions=True))["recipe"]["expressions"] is True


@needs_designer
def test_a_new_body_looks_like_its_character_and_what_is_asked_for_is_applied_over_that(lib):
    from bodydesigner import api
    sheet = {"build": "heavyset", "skin_tone": "tan", "eyes": "blue", "hair": "black long hair", "glasses": "round glasses"}
    npc_id = new_npc(lib, appearance=sheet)
    woman = lib.get(npc_id)
    neutral = api.default_recipe(FEMALE)
    got = bodymaker.prepare(lib, woman, request())
    recipe = got["recipe"]
    assert recipe["values"]["fat"] == 1.0 and recipe["values"]["waist"] == 0.8 and recipe["values"]["height"] == 0.0 and recipe["values"]["nose"] == 0.0
    assert recipe["materials"]["skin_color"] == bodymatch.colour("tan", "skin") != neutral["materials"]["skin_color"]
    assert recipe["materials"]["iris_color"] == bodymatch.colour("blue", "eyes") and recipe["brows"] == {"color": bodymatch.colour("black", "hair")}
    assert {k: recipe["materials"][k] for k in ("skin_roughness", "eye_roughness")} == {k: neutral["materials"][k] for k in ("skin_roughness", "eye_roughness")}
    assert got["matched"] == {"said": {"build": "heavyset", "skin_tone": "tan", "eyes": "blue", "hair": "black long hair", "hair_style": "black long hair"}, "skipped": {}}
    long_or_crop = "long" if HAS_LONG else "crop"                                                                   # long hair: the Long, or a Crop said so when this Body Designer has none
    assert recipe["hair"] == {"version": 1, "style": long_or_crop, "color": bodymatch.colour("black", "hair")} and got["hair_said"] == {"style": long_or_crop, "stand_in": not HAS_LONG}
    assert api.clean_recipe(recipe) == recipe                                                                       # (the Body Designer takes it as it is)
    over = bodymaker.prepare(lib, woman, request(values={"fat": -0.2, "height": 0.3}))["recipe"]["values"]
    assert over["fat"] == -0.2 and over["height"] == 0.3 and over["waist"] == 0.8                                    # what is asked for wins; the rest stays matched
    plain = bodymaker.prepare(lib, woman, request(match=False))
    assert plain["recipe"]["values"] == neutral["values"] and plain["recipe"]["materials"] == neutral["materials"] and plain["matched"] is None
    assert plain["recipe"]["brows"] == neutral["brows"] and "hair" not in plain["recipe"] and plain["hair_said"] is None          # a neutral body has no sheet's hair either
    male = bodymaker.prepare(lib, woman, request(base=MALE))                                                         # another body takes the same sheet
    assert male["recipe"]["values"]["fat"] == 1.0 and male["recipe"]["materials"]["skin_color"] == bodymatch.colour("tan", "skin")
    assert bodymaker.status(lib, woman)["match"] == {"said": got["matched"]["said"], "skipped": {}, "hair": {"style": "long", "color": bodymatch.colour("black", "hair")}}


@needs_designer
def test_a_saved_body_and_a_whole_recipe_are_somebodys_design_and_are_not_matched_to_the_sheet(lib):
    from bodydesigner import api
    npc_id = new_npc(lib, appearance={"build": "heavyset", "skin_tone": "tan", "eyes": "blue"})
    woman = lib.get(npc_id)
    own = api.default_recipe(FEMALE)
    own["materials"] = {**own["materials"], "skin_color": "#112233"}
    own["values"]["fat"] = -0.5
    lib.write_asset(npc_id, "body_design.json", json.dumps(own).encode())
    kept = bodymaker.prepare(lib, woman, request(saved=True))
    assert kept["recipe"]["values"]["fat"] == -0.5 and kept["recipe"]["materials"]["skin_color"] == "#112233" and kept["matched"] is None and "hair" not in kept["recipe"]
    other = bodymaker.prepare(lib, woman, request(saved=True, base=MALE))                                            # another body: the proportions are carried, the looks are the sheet's
    assert other["recipe"]["values"]["fat"] == -0.5 and other["recipe"]["materials"]["skin_color"] == bodymatch.colour("tan", "skin")
    assert other["matched"]["said"]["skin_tone"] == "tan"
    whole = bodymaker.prepare(lib, woman, request(recipe=own))
    assert whole["recipe"]["materials"]["skin_color"] == "#112233" and whole["recipe"]["values"]["fat"] == -0.5 and whole["matched"] is None and "hair" not in whole["recipe"]


@needs_designer
def test_words_the_tables_do_not_know_are_said_and_an_empty_sheet_changes_nothing(lib):
    from bodydesigner import api
    odd = lib.get(new_npc(lib, appearance={"build": "mermaid", "skin_tone": "iridescent", "eyes": "dark brown"}))
    got = bodymaker.prepare(lib, odd, request())
    assert got["matched"] == {"said": {"eyes": "dark brown"}, "skipped": {"build": "mermaid", "skin_tone": "iridescent"}}
    assert got["recipe"]["values"] == api.default_recipe(FEMALE)["values"] and got["recipe"]["materials"]["skin_color"] == api.default_recipe(FEMALE)["materials"]["skin_color"]
    blank = bodymaker.prepare(lib, lib.get(new_npc(lib)), request())
    assert blank["matched"] is None and blank["recipe"]["values"] == api.default_recipe(FEMALE)["values"]


def test_the_body_records_what_it_wears_and_counts_it_in_its_triangles(lib):
    worn = {"kinds": ["briefs", "bandeau"], "colour": "#d8d2c8", "triangles": 1674, "layer": "underwear"}
    assets = stored(lib, new_npc(lib), dressing=worn)
    assert assets["model_3d"]["designed"]["wears"] == worn and assets["model_3d"]["designed"]["triangles"] == 43268     # (the body's own, as the Body Designer counts it)
    assert assets["model_3d"]["faces"] == assets["rig_3d"]["faces"] == 43268 + 1674 and "underwear" in assets["rig_3d"]["notes"][0]
    bare = stored(lib, new_npc(lib))
    assert "wears" not in bare["model_3d"]["designed"] and bare["model_3d"]["faces"] == 43268 and "no hair, clothes" in bare["rig_3d"]["notes"][0]


def test_the_body_records_what_it_was_matched_to(lib):
    said = {"said": {"build": "stocky", "skin_tone": "tan"}, "skipped": {"hair": "pastel pink bob"}}
    assert stored(lib, new_npc(lib), matched=said)["model_3d"]["designed"]["matched"] == said
    assert "matched" not in stored(lib, new_npc(lib))["model_3d"]["designed"]


@needs_designer
def test_a_body_designer_without_the_natural_face_keeps_the_classic_one(lib, monkeypatch):
    monkeypatch.setattr(bodymaker, "natural_profiles", lambda: None)
    woman = lib.get(new_npc(lib))
    assert bodymaker.prepare(lib, woman, request())["face"] == "classic"                                       # the default quietly falls back
    with pytest.raises(ValueError, match="no Natural face"):
        bodymaker.prepare(lib, woman, request(face="natural"))
    assert [f["id"] for f in bodymaker.availability(config.load())["faces"]] == ["classic"]                    # and the card is not offered a face it cannot make


def test_the_skin_size_reaches_the_command_that_builds_the_body(tmp_path, monkeypatch):
    seen = {}

    def fake_run(command, tmp, name, doing, timeout, cancel=None):
        seen[name] = command
        for rel in (("plain.glb", "build.json") if name == "build" else ("animated.glb", "copy.json")):
            (tmp / rel).write_text("{}" if rel.endswith(".json") else "glb", "utf-8")

    monkeypatch.setattr(bodymaker, "_run", fake_run)
    monkeypatch.setattr(bodymaker.rig3d, "find_blender", lambda cfg: "blender")
    monkeypatch.setattr(bodymaker.rig3d, "library_files", lambda cfg: [tmp_path / "library" / "clips.glb"])
    monkeypatch.setattr(bodymaker, "COPIER", pathlib.Path(__file__))
    monkeypatch.setattr(bodymaker, "_dressing", lambda recipe, npc=None: [])                                 # (the placeholder files are not bodies)
    bodymaker.build({}, {"base": FEMALE}, "low", texture=1024)
    assert seen["build"][seen["build"].index("--texture") + 1] == "1024"
    bodymaker.build({}, {"base": FEMALE}, "low")
    assert seen["build"][seen["build"].index("--texture") + 1] == "2048"                                      # (no request: the Body Designer's own default)


def fake_files(tmp_path, monkeypatch):
    """bodymaker.build with its two commands faked (they write placeholder files) and the underwear recorded instead of made."""
    def fake_run(command, tmp, name, doing, timeout, cancel=None):
        for rel in (("plain.glb", "build.json") if name == "build" else ("animated.glb", "copy.json")):
            (tmp / rel).write_text("{}" if rel.endswith(".json") else name + "-glb", "utf-8")

    monkeypatch.setattr(bodymaker, "_run", fake_run)
    monkeypatch.setattr(bodymaker.rig3d, "find_blender", lambda cfg: "blender")
    monkeypatch.setattr(bodymaker.rig3d, "library_files", lambda cfg: [tmp_path / "library" / "clips.glb"])
    monkeypatch.setattr(bodymaker, "COPIER", pathlib.Path(__file__))
    monkeypatch.setattr(bodymaker, "_dressing", bodymaker._dressing)                                       # (restored when the test ends, whatever set_dressing does)
    calls = []

    def dress(blob, kinds, colour=None):
        calls.append((blob, list(kinds)))
        return blob + b"+" + ",".join(kinds).encode(), {"kinds": list(kinds), "triangles": 100 * len(kinds), "layer": "underwear"}

    monkeypatch.setattr(bodymaker.underwear, "dress", dress)
    return calls


def test_a_new_body_is_dressed_in_its_underwear_both_files_alike(tmp_path, monkeypatch):
    calls = fake_files(tmp_path, monkeypatch)
    plain, animated, reports = bodymaker.build({}, {"base": FEMALE}, "low")
    assert calls == [(b"build-glb", ["briefs", "bandeau"]), (b"copy-glb", ["briefs", "bandeau"])]               # the plain body and the animated one, the same pieces
    assert plain == b"build-glb+briefs,bandeau" and animated == b"copy-glb+briefs,bandeau"
    assert reports["dressing"] == {"kinds": ["briefs", "bandeau"], "triangles": 200, "layer": "underwear"}
    calls.clear()
    bodymaker.build({}, {"base": MALE}, "low")
    assert [kinds for _, kinds in calls] == [["briefs"], ["briefs"]]                                           # the male body has no bandeau
    calls.clear()
    plain, animated, reports = bodymaker.build({}, {"base": "forge-anatomical-other-v7"}, "low")               # a body of no sex wears nothing: it is not guessed
    assert not calls and "dressing" not in reports and plain == b"build-glb"


def test_only_a_local_extension_can_change_what_a_body_wears(tmp_path, monkeypatch):
    calls = fake_files(tmp_path, monkeypatch)
    seen = []
    bodymaker.set_dressing(lambda recipe, npc: seen.append((recipe["base"], npc)) or [])
    plain, animated, reports = bodymaker.build({}, {"base": FEMALE}, "low", npc={"id": "someone"})
    assert seen == [(FEMALE, {"id": "someone"})] and not calls and plain == b"build-glb" and animated == b"copy-glb" and "dressing" not in reports
    bodymaker.set_dressing(lambda recipe, npc: ["briefs"])
    bodymaker.build({}, {"base": FEMALE}, "low")
    assert [kinds for _, kinds in calls] == [["briefs"], ["briefs"]]
    bodymaker.set_dressing(lambda recipe, npc: ["thong"])
    with pytest.raises(BodyError, match="not a piece of underwear"):
        bodymaker.build({}, {"base": FEMALE}, "low")
    bodymaker.set_dressing()
    calls.clear()
    bodymaker.build({}, {"base": FEMALE}, "low")
    assert [kinds for _, kinds in calls] == [["briefs", "bandeau"]] * 2                                        # None puts the default back
    for place in ("web/app.js", "server.py", "npcgen/service.py", "npcgen/cli.py"):                             # and nothing a person can reach has a way to ask for it
        assert "set_dressing" not in (config.ROOT / place).read_text("utf-8"), place
    assert "underwear" not in json.dumps(sorted(bodymaker.REQUEST_FIELDS))
    assert "wear" not in bodymaker.REQUEST_FIELDS                                                              # (and the choice of clothes a local extension may add is its own: see below)


def test_a_body_that_cannot_be_dressed_is_not_made_bare_behind_anyones_back(tmp_path, monkeypatch):
    fake_files(tmp_path, monkeypatch)

    def refuse(blob, kinds, colour=None):
        raise ValueError("The body has no single skeleton and mesh to put underwear on")

    monkeypatch.setattr(bodymaker.underwear, "dress", refuse)
    with pytest.raises(BodyError, match="could not be dressed: The body has no single skeleton"):
        bodymaker.build({}, {"base": FEMALE}, "low")


@needs_designer
def test_only_a_two_legged_character_gets_a_body_and_an_existing_model_is_replaced_only_when_that_is_said(lib):
    with pytest.raises(ValueError, match="two-legged"):
        bodymaker.prepare(lib, lib.get(new_npc(lib, morphology="taur")), request())
    have = lib.get(new_npc(lib, model=True))
    with pytest.raises(ValueError, match="already has a 3D model"):
        bodymaker.prepare(lib, have, request())
    assert bodymaker.prepare(lib, have, request(replace=True))["replace"] is True
    bare = lib.get(new_npc(lib))
    assert bodymaker.prepare(lib, bare, request())["replace"] is False                          # (nothing to replace: no need to say so)


def test_what_the_card_needs_to_know_about_a_character(lib):
    npc_id = new_npc(lib, model=True)
    assert bodymaker.status(lib, lib.get(npc_id))["designed"] is None
    lib.update(npc_id, lambda n: n["assets"]["model_3d"].update(designed={"base": FEMALE}))
    assert bodymaker.status(lib, lib.get(npc_id))["designed"] == {"base": FEMALE}
    if bodymaker.bases():
        assert bodymaker.status(lib, lib.get(npc_id))["base"] == FEMALE


def test_a_designed_body_is_not_cleaned_up_painted_or_rigged_again():
    designed = {"assets": {"model_3d": {"file": "m.glb", "designed": {"base": FEMALE}}}}
    plain = {"assets": {"model_3d": {"file": "m.glb"}}}
    for k in bodymaker.CLEAN_UP:
        assert "designed body" in bodymaker.refuses(designed, {k: True})
        assert bodymaker.refuses(plain, {k: True}) is None
        for new in bodymaker.NEW_MODEL:
            assert bodymaker.refuses(designed, {k: True, new: True}) is None                    # a new model makes them meaningful again
    assert bodymaker.refuses(designed, {"portrait": True}) is None


# ---- what is looked at before a body is used -----------------------------------------------------------------------------------------
def test_a_body_is_used_only_if_it_is_an_adults_the_skeleton_asked_for_and_the_whole_clip_library():
    for tier in ("low", "medium"):
        plain = body_doc(tier)
        clips = bodymaker.check(glb(plain), glb(animated_doc(plain)), tier)
        assert len(clips) == 85 and clips[-1] == {"name": "Wave", "seconds": 1.0, "loop": False, "source": "lib: Wave"}
    plain = body_doc()
    cases = [(body_doc(minimum_age=17), animated_doc(body_doc(minimum_age=17)), "adult"), (body_doc(tier="full"), animated_doc(body_doc(tier="full")), "game body"),
             (body_doc("medium"), animated_doc(body_doc("medium")), "low skeleton"), (body_doc(joints=22), animated_doc(body_doc(joints=22)), "low skeleton"),
             (plain, animated_doc(plain, CLIPS[:-1]), "84 clips"), (plain, animated_doc(plain, CLIPS[:-3] + ["a", "b", "c"]), "85 clips"),
             (plain, {**animated_doc(plain), "nodes": [{"name": "other"}] * 23}, "not the body's own")]
    for body, animated, reason in cases:
        with pytest.raises(BodyError, match=reason):
            bodymaker.check(glb(body), glb(animated), "low")
    with pytest.raises(BodyError, match="not a GLB"):
        bodymaker.glb_json(b"nope")
    undated = body_doc()
    del undated["asset"]["extras"]["minimum_age"]
    with pytest.raises(BodyError, match="adult"):
        bodymaker.check(glb(undated), glb(animated_doc(undated)), "low")


def test_a_command_that_fails_is_reported_by_its_own_error_line():
    assert bodymaker._failure("usage: x\nx: error: recipe: base: choose an id\n") == "recipe: base: choose an id"
    assert bodymaker._failure("a\nTraceback\nValueError: boom\n") == "ValueError: boom"
    assert bodymaker._failure("") == "no output"


# ---- storing it ------------------------------------------------------------------------------------------------------------------------------
def prepared(replace=True, skeleton="low", **more):
    return {"base": FEMALE, "base_label": "Adult female natural anatomy", "skeleton": skeleton, "saved": False, "replace": replace,
            "recipe": {"version": 10, "base": FEMALE, "values": {"height": 0.2}}, **more}


def stored(lib, npc_id, dressing=None, clothes=None, swing=None, **more):
    plain = body_doc()
    reports = {"build": {"triangles": 43268, "size_m": [0.5, 1.62, 0.3], "sha256": "abc"}, "seconds": 12.5, **({"dressing": dressing} if dressing else {}),
               **({"clothes": clothes} if clothes else {}), **({"swing": swing} if swing else {})}
    rig = swung_doc(plain, [p for p in ("hair", "cloth") if swing["made"][p]]) if swing else animated_doc(plain)
    clips = bodymaker.check(glb(plain), glb(rig), "low", swinging=swing and swing["made"])
    bodymaker.store(lib, npc_id, glb(plain), glb(rig), clips, prepared(**more), reports, more.get("picture") and b"a picture")
    return lib.get(npc_id)["assets"]


def test_the_body_becomes_the_model_and_rig_and_the_old_ones_leave_nothing_behind(lib):
    npc_id = new_npc(lib, model=True)
    assets = stored(lib, npc_id, picture=True)
    model, rig = assets["model_3d"], assets["rig_3d"]
    assert model["file"] == "model_3d.glb" and model["engine_id"] == "bodydesigner" and model["preview"] == "model_3d_preview.png" and model["textured"]
    assert model["designed"]["base"] == FEMALE and model["designed"]["recipe"]["values"] == {"height": 0.2} and model["designed"]["height_m"] == 1.62
    assert "refined" not in model and "raw" not in model                                           # (the old model's clean-up is gone with it)
    assert rig["file"] == "model_3d_rigged.glb" and len(rig["clips"]) == 85 and len(rig["clip_info"]) == 85 and rig["bones"] == 23 and rig["clip_set"] == "library"
    assert rig["joints_from"] == "designed body" and rig["source_model_created"] == model["created"] and not rig.get("stale") and "preview" not in rig
    for rel in ("model_3d.glb", "model_3d_rigged.glb", "model_3d_preview.png", "portrait.png"):
        assert lib.resolve(npc_id, rel).is_file()
    assert lib.resolve(npc_id, "model_3d.glb").read_bytes() != b"old model"
    for rel in ("model_3d_original.glb", "model_3d_old_preview.png", "model_3d_rigged.png", cleanup3d.ORIGINAL_FILE, check3d.BOARD_FILE, "model_3d_head"):
        assert not lib.resolve(npc_id, rel).exists(), rel
    assert "portrait" in assets and assets["portrait"]["file"] == "portrait.png"                  # (what the model did not make is not touched)


def test_the_model_a_body_replaces_is_kept_among_the_earlier_ones_with_its_skeleton_and_original(lib):
    npc_id = new_npc(lib, model=True)
    assets = stored(lib, npc_id, picture=True)
    (entry,) = assets["model_history"]
    kept = f"model_history/{entry['id']}/"
    assert entry["model"]["raw"] == "model_3d_original.glb" and entry["rig"]["file"] == "model_3d_rigged.glb" and "designed" not in entry["model"]
    assert [lib.read_asset(npc_id, kept + name) for name in ("model_3d.glb", "model_3d_original.glb", "model_3d_old_preview.png", "model_3d_rigged.glb", "model_3d_head/front.png")] == \
        [b"old model", b"kept from the clean-up", b"old picture", b"old rig", b"close-up"]                  # (the helper writes the original twice; the clean-up's copy is the last)
    assert lib.read_asset(npc_id, kept + check3d.BOARD_FILE) == b"a board" and lib.read_asset(npc_id, kept + "model_3d_rigged.png") == b"old rig picture"
    assert assets["model_3d"]["engine_id"] == "bodydesigner" and not lib.resolve(npc_id, "model_3d_original.glb").exists()      # (the body is in use; the old files are in the earlier models only)
    back = lib.restore_model(npc_id, entry["id"])                                       # and the old model comes back whole
    assert back["assets"]["model_3d"]["raw"] == "model_3d_original.glb" and lib.read_asset(npc_id, "model_3d_head/front.png") == b"close-up"
    assert back["assets"]["model_history"][0]["model"]["engine_id"] == "bodydesigner"


def test_a_body_without_a_picture_has_none_and_the_old_picture_is_not_kept_for_it(lib):
    npc_id = new_npc(lib, model=True)
    lib.write_asset(npc_id, "model_3d_preview.png", b"the old model's picture")
    assets = stored(lib, npc_id)
    assert "preview" not in assets["model_3d"] and not lib.resolve(npc_id, "model_3d_preview.png").exists()


def test_a_model_that_appeared_meanwhile_is_not_replaced_and_nothing_is_written(lib):
    npc_id = new_npc(lib, model=True)
    with pytest.raises(BodyError, match="was being made"):
        stored(lib, npc_id, replace=False)
    assert lib.resolve(npc_id, "model_3d.glb").read_bytes() == b"old model" and lib.resolve(npc_id, "model_3d_rigged.glb").read_bytes() == b"old rig"
    assert "designed" not in lib.get(npc_id)["assets"]["model_3d"]
    bare = new_npc(lib)
    assert stored(lib, bare, replace=False)["model_3d"]["designed"]                                # a character with no model needs no yes


# ---- the job step ----------------------------------------------------------------------------------------------------------------------------
@pytest.fixture
def jobs(tmp_path, lib):
    worker = Jobs({}, type("Comfy", (), {"models": {}})(), lib, state_path=tmp_path / "jobs.json")
    yield worker
    worker.close()


def wait_done(worker, job_id, seconds=15):
    deadline = time.monotonic() + seconds
    while worker.jobs[job_id].status in ("queued", "running") and time.monotonic() < deadline:
        time.sleep(0.02)
    return next(j for j in worker.snapshot() if j["id"] == job_id)


def submit(worker, lib, npc_id, **more):
    return worker.submit(lib.get(npc_id), {"body_3d": prepared(**more)}, {})


def test_the_job_step_builds_checks_and_stores_the_body(jobs, lib, monkeypatch):
    calls = []
    monkeypatch.setattr(bodymaker, "build", fake_build(calls=calls))
    monkeypatch.setattr(bodymaker, "preview", lambda cfg, plain: b"a picture")
    npc_id = new_npc(lib, model=True)
    job = submit(jobs, lib, npc_id, skeleton="low")
    assert [s["kind"] for s in job["steps"]] == ["body_3d"] and job["steps"][0]["label"].startswith("Build the designed body")
    done = wait_done(jobs, job["id"])
    step = done["steps"][0]
    assert done["status"] == "done" and step["status"] == "done" and "85 clips" in step["note"] and "detail" not in step
    assert calls == [{"recipe": prepared()["recipe"], "skeleton": "low", "texture": 2048, "npc": npc_id, "clothes": []}]     # (it is built for this character)
    assets = lib.get(npc_id)["assets"]
    assert assets["model_3d"]["designed"]["base"] == FEMALE and assets["rig_3d"]["joints_from"] == "designed body" and assets["model_3d"]["preview"]
    assert assets["model_3d"]["designed"]["face"] == "classic" and assets["model_3d"]["designed"]["texture"] == 2048          # (prepared() is a classic recipe)
    calls.clear()                                                                                                          # the skin size asked for reaches the build
    assert wait_done(jobs, submit(jobs, lib, new_npc(lib), texture=512)["id"])["status"] == "done"
    assert [c["texture"] for c in calls] == [512]
    calls.clear()                                                                                                          # the garments settled by prepare reach the build
    shirt = [{"kind": "shirt", "colour": "#e6e3dc"}, {"kind": "trousers"}]
    assert wait_done(jobs, submit(jobs, lib, new_npc(lib), pieces=shirt)["id"])["status"] == "done"
    assert [c["clothes"] for c in calls] == [shirt]


def test_a_picture_that_cannot_be_drawn_does_not_undo_the_body(jobs, lib, monkeypatch):
    monkeypatch.setattr(bodymaker, "build", fake_build())
    monkeypatch.setattr(bodymaker, "preview", lambda cfg, plain: None)
    npc_id = new_npc(lib)
    assert wait_done(jobs, submit(jobs, lib, npc_id)["id"])["status"] == "done"
    assert "preview" not in lib.get(npc_id)["assets"]["model_3d"] and lib.get(npc_id)["assets"]["rig_3d"]["file"]


def test_a_body_that_fails_is_reported_and_the_old_model_is_left_alone(jobs, lib, monkeypatch):
    monkeypatch.setattr(bodymaker, "build", fake_build(error=BodyError("Copying the animation clips failed: no Blender")))
    npc_id = new_npc(lib, model=True)
    done = wait_done(jobs, submit(jobs, lib, npc_id)["id"])
    assert done["status"] == "error" and done["steps"][0]["error"] == "Copying the animation clips failed: no Blender"
    assert lib.resolve(npc_id, "model_3d.glb").read_bytes() == b"old model" and "designed" not in lib.get(npc_id)["assets"]["model_3d"]
    monkeypatch.setattr(bodymaker, "build", fake_build(names=CLIPS[:10]))                           # a body with too few clips is not used either
    done = wait_done(jobs, submit(jobs, lib, npc_id)["id"])
    assert done["status"] == "error" and "85" in done["steps"][0]["error"] and lib.resolve(npc_id, "model_3d.glb").read_bytes() == b"old model"


def test_cancel_stops_the_build_and_changes_nothing(jobs, lib, monkeypatch):
    hold, calls = threading.Event(), []
    monkeypatch.setattr(bodymaker, "build", fake_build(wait=hold, calls=calls))
    npc_id = new_npc(lib, model=True)
    job = submit(jobs, lib, npc_id)
    deadline = time.monotonic() + 5
    while not calls and time.monotonic() < deadline:
        time.sleep(0.01)
    assert calls
    jobs.cancel(job["id"])
    done = wait_done(jobs, job["id"])
    assert done["status"] == "cancelled" and done["steps"][0]["status"] == "cancelled"
    assert lib.resolve(npc_id, "model_3d.glb").read_bytes() == b"old model"


def test_a_job_with_no_recipe_says_so(jobs, lib, monkeypatch):
    npc_id = new_npc(lib)
    job = jobs.submit(lib.get(npc_id), {"body_3d": {"skeleton": "low"}}, {})
    assert wait_done(jobs, job["id"])["steps"][0]["error"] == "The designed body has no recipe"


# ---- the routes ------------------------------------------------------------------------------------------------------------------------------
@pytest.fixture()
def api(tmp_path):
    server = importlib.import_module("server")
    cfg = copy.deepcopy(config.DEFAULTS)
    cfg.update(library_path=tmp_path / "library", comfy_url="http://127.0.0.1:9", auth_token="")
    app = server.App(cfg)
    httpd = server.create_server(("127.0.0.1", 0), app)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"

    def call(method, path, body=None):
        h = {"Content-Type": "application/json"} if method != "GET" else {}
        data = json.dumps(body).encode() if body is not None else (b"{}" if method != "GET" else None)
        try:
            with urllib.request.urlopen(urllib.request.Request(base + path, data=data, method=method, headers=h), timeout=60) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    call.app = app
    yield call
    httpd.shutdown()
    httpd.server_close()
    app.close()


def test_the_page_is_told_what_a_designed_body_needs_and_what_a_character_has(api):
    status, health = api("GET", "/api/health")
    assert status == 200 and set(health["body_3d"]) == {"installed", "missing", "bases", "skeletons", "faces", "textures", "clothes", "outers", "hairs", "marks", "expressions", "swinging", "clips"} and health["body_3d"]["clips"] == 85
    assert (health["body_3d"]["swinging"] is not None) == (bodymaker.offers("secondary") and bodymaker.swinging_info() is not None and bool(bodymaker.bases()))
    assert [c["id"] for c in health["body_3d"]["clothes"]] == (["basic", "sheet", "none"] if bodymaker.offers("garments") and bodymaker.bases() else [])
    assert [c["id"] for c in health["body_3d"]["outers"]] == (["sheet", "none", "jacket", "apron"] if bodymaker.offers("garments") and bodymaker.bases() else [])
    assert [c["id"] for c in health["body_3d"]["hairs"]] == (["sheet", "none", *bodymaker.hair_styles()] if bodymaker.offers("hair") and bodymaker.bases() else [])
    assert [m["id"] for m in health["body_3d"]["marks"]] == (["sheet", "none"] if marks.offered() and bodymaker.bases() else [])
    assert (health["body_3d"]["expressions"] is not None) == (bodymaker.offers("expressions") and bodymaker.expressions_info() is not None and bool(bodymaker.bases()))
    assert [k["id"] for k in health["body_3d"]["skeletons"]] == ["low", "medium"]
    assert [t["id"] for t in health["body_3d"]["textures"]] == [2048, 1024, 512]
    assert [f["id"] for f in health["body_3d"]["faces"]] == (["natural", "classic"] if bodymaker.natural_profiles() else ["classic"])
    npc_id = new_npc(api.app.lib)
    status, state = api("GET", f"/api/npcs/{npc_id}/body-3d")
    assert status == 200 and state["saved"] is False and state["designed"] is None
    assert api("GET", "/api/npcs/no-such-npc-123456/body-3d")[0] == 404
    assert config.DEFAULTS and importlib.import_module("server").API_VERSION >= 27


@needs_designer
def test_a_body_is_asked_for_through_generate_and_the_mistakes_are_refused_before_anything_is_queued(api, monkeypatch):
    lib = api.app.lib
    monkeypatch.setattr(bodymaker, "build", fake_build())
    monkeypatch.setattr(bodymaker, "preview", lambda cfg, plain: b"a picture")
    have = new_npc(lib, model=True)
    assert api("POST", "/api/generate", {"assets": {"body_3d": {"base": FEMALE}}})[0] == 400                    # which character?
    status, body = api("POST", "/api/generate", {"npc_id": have, "assets": {"body_3d": {"base": FEMALE}}})
    assert status == 400 and "already has a 3D model" in body["error"]
    status, body = api("POST", "/api/generate", {"npc_id": have, "assets": {"body_3d": {"base": FEMALE, "values": {"height": 9}}}})
    assert status == 400 and "values.height" in body["error"]
    assert api("POST", "/api/generate", {"npc_id": have, "assets": {"body_3d": {"base": FEMALE}, "model_3d": True}})[0] == 400
    assert api("POST", "/api/generate", {"npc_id": "no-such-npc-123456", "assets": {"body_3d": {"base": FEMALE}}})[0] == 404
    assert not api.app.jobs.snapshot()                                                                         # (nothing was queued)
    status, queued = api("POST", "/api/generate", {"npc_id": have, "assets": {"body_3d": {"base": FEMALE, "values": {"height": 0.3}, "replace": True}}})
    assert status == 200 and queued["jobs"][0]["steps"][0]["kind"] == "body_3d"
    deadline = time.monotonic() + 15
    while api.app.jobs.snapshot()[0]["status"] in ("queued", "running") and time.monotonic() < deadline:
        time.sleep(0.02)
    assert api.app.jobs.snapshot()[0]["status"] == "done"
    state = api("GET", f"/api/npcs/{have}/body-3d")[1]
    assert state["designed"]["recipe"]["values"]["height"] == 0.3
    assert api("POST", "/api/generate", {"npc_id": have, "assets": {"rig_3d": True}})[0] == 400              # (the rig is the body's own)
    assert api("POST", "/api/generate", {"npc_id": have, "assets": {"refine_3d": True}})[0] == 400


# ---- the hair and the clothes -----------------------------------------------------------------------------------------------------------------
@needs_designer
def test_a_new_body_has_the_hair_its_sheet_says_and_every_other_body_keeps_its_own(lib):
    from bodydesigner import api
    neutral = api.default_recipe(FEMALE)

    def made(text, **more):
        return bodymaker.prepare(lib, lib.get(new_npc(lib, appearance={"hair": text} if text else {})), request(**more))

    short = made("gray wavy short cropped hair")
    assert short["recipe"]["hair"] == {"version": 1, "style": "crop", "color": bodymatch.colour("gray", "hair")} and short["hair_said"] == {"style": "crop", "stand_in": False}
    assert made("black side-parted short hair")["recipe"]["hair"]["style"] == "sweep"
    bald = made("bald")
    assert "hair" not in bald["recipe"] and bald["hair_said"] == {"style": "none", "stand_in": False}
    dyed = made("pastel pink pixie cut")
    assert dyed["recipe"]["hair"]["color"] == bodymatch.data()["dyed"]["pastel pink"] and dyed["recipe"]["brows"] == neutral["brows"]       # the hair is dyed, the brows are not
    long = made("dark brown wavy long hair with curtain bangs")                                                                           # no game hair for it: a stand-in, said so
    nearest = "long" if HAS_LONG else "crop"                                                                                              # (nearly long: the Long as the nearest, or a Crop)
    assert long["recipe"]["hair"] == {"version": 1, "style": nearest, "color": bodymatch.colour("dark brown", "hair")} and long["hair_said"] == {"style": nearest, "stand_in": True}
    plain_long = made("black long hair")                                                                                                  # the Long exactly, or a Crop said so
    assert plain_long["recipe"]["hair"] == {"version": 1, "style": "long" if HAS_LONG else "crop", "color": bodymatch.colour("black", "hair")}
    assert plain_long["hair_said"] == {"style": "long" if HAS_LONG else "crop", "stand_in": not HAS_LONG}
    bun = made("dark brown wavy long hair pinned up in a bun")                                                                            # no game hair for a bun: a Crop as a stand-in, said so
    assert bun["recipe"]["hair"] == {"version": 1, "style": "crop", "color": bodymatch.colour("dark brown", "hair")} and bun["hair_said"] == {"style": "crop", "stand_in": True}
    blank = made(None)
    assert blank["recipe"]["hair"] == {"version": 1, "style": "crop", "color": neutral["brows"]["color"]} and blank["hair_said"]["stand_in"] is True    # nothing said: the brows' colour
    for got in (short, dyed, long, plain_long, bun, blank):
        assert api.clean_recipe(got["recipe"]) == got["recipe"]                                                                          # the Body Designer takes it as it is
    assert made("black short hair", hair="none")["recipe"].get("hair") is None and made("black short hair", hair="none")["hair_said"] == {"style": "none", "stand_in": False}
    sweep = made("gray wavy short cropped hair", hair="sweep")                                                                           # a style asked for, in the sheet's colour
    assert sweep["recipe"]["hair"] == {"version": 1, "style": "sweep", "color": bodymatch.colour("gray", "hair")}
    assert made("gray short hair", match=False)["recipe"].get("hair") is None                                                            # a neutral body: no hair unless asked for
    asked = made("gray short hair", match=False, hair="crop")["recipe"]["hair"]
    assert asked == {"version": 1, "style": "crop", "color": neutral["brows"]["color"]}                                                  # (and it is not coloured by the sheet)
    own = api.default_recipe(FEMALE)                                                                                                     # a whole recipe keeps its own hair, or none
    assert "hair" not in made("black short hair", recipe=own)["recipe"]
    own["hair"] = {"version": 1, "style": "sweep", "color": "#123456"}
    assert made("black short hair", recipe=own)["recipe"]["hair"] == own["hair"]
    assert made("black short hair", recipe=own, hair="none")["recipe"].get("hair") is None
    assert made("black short hair", recipe=own, hair="crop")["recipe"]["hair"] == {"version": 1, "style": "crop", "color": neutral["brows"]["color"]}
    woman = lib.get(new_npc(lib, appearance={"hair": "black short hair"}))                                                               # a saved body keeps its hair too
    lib.write_asset(woman["id"], "body_design.json", json.dumps(own).encode())
    assert bodymaker.prepare(lib, woman, request(saved=True))["recipe"]["hair"] == own["hair"]
    assert bodymaker.prepare(lib, woman, request(saved=True, hair="none"))["recipe"].get("hair") is None
    assert bodymaker.prepare(lib, woman, request(saved=True, base=MALE))["recipe"]["hair"]["style"] == "crop"                             # another body is a new body


@needs_designer
def test_a_body_designer_without_game_hair_leaves_the_head_bare_unless_hair_was_asked_for(lib, monkeypatch):
    monkeypatch.setattr(bodymaker, "offers", lambda part: part != "hair")
    woman = lib.get(new_npc(lib, appearance={"hair": "black short hair"}))
    assert "hair" not in bodymaker.prepare(lib, woman, request())["recipe"]
    with pytest.raises(ValueError, match="no game hair"):
        bodymaker.prepare(lib, woman, request(hair="crop"))
    assert bodymaker.availability(config.load())["hairs"] == []


def test_the_long_hair_is_chosen_only_where_this_body_designer_makes_it(monkeypatch):
    monkeypatch.setattr(bodymaker, "offers", lambda part: True)
    exact, nearest = {"style": "long", "color": "#111111"}, {"style": None, "near": "long", "color": "#111111"}
    monkeypatch.setattr(bodymaker, "hair_styles", lambda: ["crop", "sweep", "long"])
    assert bodymaker.with_hair({}, "sheet", exact) == ({"hair": {"version": 1, "style": "long", "color": "#111111"}}, {"style": "long", "stand_in": False})
    assert bodymaker.with_hair({}, "sheet", nearest) == ({"hair": {"version": 1, "style": "long", "color": "#111111"}}, {"style": "long", "stand_in": True})
    assert bodymaker.with_hair({}, "long", {"color": "#111111"})[1] == {"style": "long", "stand_in": False}
    assert bodymaker.with_hair({}, "sheet", {"style": None, "color": "#111111"})[1] == {"style": "crop", "stand_in": True}              # (a bun: still a Crop)
    monkeypatch.setattr(bodymaker, "hair_styles", lambda: ["crop", "sweep"])                                                             # an older Body Designer
    assert bodymaker.with_hair({}, "sheet", exact)[1] == {"style": "crop", "stand_in": True}
    assert bodymaker.with_hair({}, "sheet", nearest)[1] == {"style": "crop", "stand_in": True}
    assert bodymaker.with_hair({}, "sweep", {"color": "#111111"})[1] == {"style": "sweep", "stand_in": False}
    with pytest.raises(ValueError, match="no Long hair"):
        bodymaker.with_hair({}, "long", {})


@needs_designer
def test_the_card_offers_the_hair_this_body_designer_makes(monkeypatch):
    game_hair = pytest.importorskip("bodydesigner.game_hair")
    assert bodymaker.hair_styles() == [style["id"] for style in game_hair.catalogue()["styles"]]                  # (read from its own catalogue, not copied)
    for styles in (["crop", "sweep"], ["crop", "sweep", "long"]):
        monkeypatch.setattr(bodymaker, "hair_styles", lambda styles=styles: styles)
        assert [h["id"] for h in bodymaker.availability(config.load())["hairs"]] == ["sheet", "none", *styles]


@needs_designer
def test_a_body_designer_that_cannot_list_its_hair_keeps_the_two_it_always_had(monkeypatch):
    game_hair = pytest.importorskip("bodydesigner.game_hair")
    monkeypatch.setattr(game_hair, "catalogue", lambda: (_ for _ in ()).throw(ValueError("damaged")))
    assert bodymaker.hair_styles() == ["crop", "sweep"]


@needs_designer
def test_the_clothes_are_settled_from_the_sheet_and_from_what_is_asked_for(lib):
    from npcgen import outfit
    blazer = lib.get(new_npc(lib, appearance={"outfit": "a satin midi robe with a tailored coat and heels"}))
    got = bodymaker.prepare(lib, blazer, request(clothes="basic"))
    assert got["clothes"] == "basic" and got["pieces"] == [{"kind": "shirt"}, {"kind": "trousers"}] and got["left"] == ["robe", "coat"]       # (a robe and a coat: not made)
    gown = lib.get(new_npc(lib, appearance={"outfit": "a satin midi dress with a tailored coat and heels"}))                                  # a dress IS made: it takes the place of the shirt and the trousers
    for mode in ("basic", "sheet"):
        got = bodymaker.prepare(lib, gown, request(clothes=mode))
        assert got["pieces"] == [{"kind": "dress"}] and got["left"] == ["coat"] and got["why"] == {}, mode
    state = bodymaker.status(lib, gown)["outfit"]
    assert state["basic"]["sheet"]["pieces"] == [{"kind": "dress"}] and state["basic"]["apron"]["problem"].endswith("this outfit has a dress") and state["sheet"]["none"]["pieces"] == [{"kind": "dress"}]
    with pytest.raises(ValueError, match="goes over a shirt and trousers only .* this outfit has a dress"):
        bodymaker.prepare(lib, gown, request(clothes="basic", outer="jacket"))
    kilt = lib.get(new_npc(lib, appearance={"outfit": "a cream cotton blouse with rolled sleeves, a faded olive kilt and worn boots"}))
    cream = outfit.read("a cream blouse")["top"]["colour"]
    assert bodymaker.prepare(lib, kilt, request(clothes="sheet"))["pieces"] == [{"kind": "shirt", "colour": cream}]                      # only what the sheet names
    both = bodymaker.prepare(lib, kilt, request(clothes="basic"))
    assert both["pieces"] == [{"kind": "shirt", "colour": cream}, {"kind": "trousers"}] and both["left"] == ["kilt"]                    # a basic outfit, whatever it says
    assert bodymaker.prepare(lib, kilt, request(clothes="none"))["pieces"] == [] and bodymaker.prepare(lib, kilt, request(clothes="none"))["left"] == []
    assert bodymaker.prepare(lib, kilt, request())["pieces"] == []                                                                       # a call that says nothing: underwear, as it always was
    nothing = lib.get(new_npc(lib))
    assert bodymaker.prepare(lib, nothing, request(clothes="sheet"))["pieces"] == []                                                      # a sheet with no outfit
    assert bodymaker.prepare(lib, nothing, request(clothes="basic"))["pieces"] == [{"kind": "shirt"}, {"kind": "trousers"}]
    state = bodymaker.status(lib, kilt)["outfit"]
    assert set(state) == {"basic", "sheet"} and state["sheet"]["sheet"]["pieces"] == [{"kind": "shirt", "colour": cream}] and state["basic"]["sheet"]["left"] == ["kilt"]
    skirt = lib.get(new_npc(lib, appearance={"outfit": "a cream cotton blouse with rolled sleeves, a faded olive skirt and worn boots"}))      # a skirt IS made: it takes the place of the trousers
    olive = outfit.read("a faded olive skirt")["instead"][0]["colour"]
    worn = [{"kind": "shirt", "colour": cream}, {"kind": "skirt", "colour": olive}]
    for mode in ("sheet", "basic"):
        got = bodymaker.prepare(lib, skirt, request(clothes=mode))
        assert got["pieces"] == worn and got["left"] == [] and got["why"] == {}, mode                                                      # (the default too: the sheet says skirt)
    assert bodymaker.prepare(lib, skirt, request(clothes="none"))["pieces"] == [] and bodymaker.prepare(lib, skirt, request())["pieces"] == []
    state = bodymaker.status(lib, skirt)["outfit"]
    assert state["sheet"]["sheet"]["pieces"] == worn == state["basic"]["sheet"]["pieces"] and state["basic"]["none"]["pieces"] == worn
    assert state["basic"]["apron"]["problem"].endswith("this outfit has a skirt") and state["basic"]["apron"]["pieces"] == worn          # (asked for, and not possible with a skirt: a problem)
    with pytest.raises(ValueError, match="goes over a shirt and trousers only .* this outfit has a skirt"):
        bodymaker.prepare(lib, skirt, request(clothes="basic", outer="jacket"))
    painted = bodymaker.prepare(lib, skirt, request(clothes="basic", colours={"skirt": "#112233", "trousers": "#445566"}))
    assert painted["pieces"] == [{"kind": "shirt", "colour": cream}, {"kind": "skirt", "colour": "#112233"}]                             # (the colour of a garment not put on is ignored)


@needs_designer
def test_a_body_designer_without_garments_makes_no_clothes_and_says_so(lib, monkeypatch):
    monkeypatch.setattr(bodymaker, "offers", lambda part: part != "garments")
    woman = lib.get(new_npc(lib, appearance={"outfit": "a white shirt and black trousers"}))
    assert bodymaker.prepare(lib, woman, request())["pieces"] == []
    with pytest.raises(ValueError, match="no garments"):
        bodymaker.prepare(lib, woman, request(clothes="basic"))
    assert bodymaker.availability(config.load())["clothes"] == []


def test_a_body_is_dressed_in_its_clothes_over_its_underwear_both_files_alike(tmp_path, monkeypatch):
    fake_files(tmp_path, monkeypatch)
    dressed = []

    def fake_dress(plain, animated, wanted, cancel=None):
        dressed.append((plain, animated, wanted, cancel))
        return plain + b"+clothes", animated + b"+clothes", {"worn": [{"kind": p["kind"], "triangles": 100} for p in wanted], "failed": {}}

    monkeypatch.setattr(bodymaker, "_dress", fake_dress)
    wanted, notes, cancel = [{"kind": "shirt", "colour": "#aabbcc"}, {"kind": "trousers"}], [], threading.Event()
    plain, animated, reports = bodymaker.build({}, {"base": FEMALE}, "low", cancel=cancel, note=notes.append, clothes=wanted)
    assert dressed == [(b"build-glb+briefs,bandeau", b"copy-glb+briefs,bandeau", wanted, cancel)]                  # over the underwear, the same for the plain file and the animated one
    assert plain == b"build-glb+briefs,bandeau+clothes" and animated == b"copy-glb+briefs,bandeau+clothes"
    assert notes == ["Building the body from its recipe", "Copying the animation clips onto its skeleton", "Putting its underwear on", "Fitting its shirt and trousers"]
    assert [w["kind"] for w in reports["clothes"]["worn"]] == ["shirt", "trousers"] and reports["dressing"]["kinds"] == ["briefs", "bandeau"]
    dressed.clear()
    plain, animated, reports = bodymaker.build({}, {"base": FEMALE}, "low")                                          # no clothes asked for: no step, nothing in the reports
    assert not dressed and "clothes" not in reports and plain == b"build-glb+briefs,bandeau"
    bodymaker.set_dressing(lambda recipe, npc: [])                                                                   # (a body a local extension left bare is dressed as asked)
    plain, animated, reports = bodymaker.build({}, {"base": FEMALE}, "low", clothes=wanted)
    bodymaker.set_dressing()
    assert plain == b"build-glb+clothes" and dressed[0][0] == b"build-glb"


def test_the_clothes_are_fitted_in_a_process_of_their_own_that_cancel_can_stop(tmp_path, monkeypatch):
    seen = {}

    def fake_run(command, tmp, name, doing, timeout, cancel=None):
        seen.update(command=command, name=name, cancel=cancel, timeout=timeout, pieces=json.loads((tmp / "pieces.json").read_text("utf-8")),
                    plain=(tmp / "plain.glb").read_bytes(), animated=(tmp / "animated.glb").read_bytes())
        (tmp / "plain_out.glb").write_bytes(b"P")
        (tmp / "animated_out.glb").write_bytes(b"A")
        (tmp / "report.json").write_text(json.dumps({"worn": [], "failed": {"shirt": "no room"}}), "utf-8")

    monkeypatch.setattr(bodymaker, "_run", fake_run)
    cancel = threading.Event()
    assert bodymaker._dress(b"plain", b"animated", [{"kind": "shirt"}], cancel) == (b"P", b"A", {"worn": [], "failed": {"shirt": "no room"}})
    assert seen["command"][1:3] == ["-m", "npcgen.outfit"] and seen["name"] == "outfit" and seen["cancel"] is cancel and seen["timeout"] == bodymaker.DRESS_SECONDS
    assert seen["pieces"] == [{"kind": "shirt"}] and seen["plain"] == b"plain" and seen["animated"] == b"animated"

    def failing(command, tmp, name, doing, timeout, cancel=None):
        raise BodyError("Fitting the clothes failed: no memory")

    monkeypatch.setattr(bodymaker, "_run", failing)
    with pytest.raises(BodyError, match="Fitting the clothes failed"):                                              # a process that fails is not hidden: no body behind anyone's back
        bodymaker._dress(b"plain", b"animated", [{"kind": "shirt"}])


def test_the_body_records_its_clothes_its_hair_and_what_did_not_fit(lib):
    worn = {"kinds": ["briefs", "bandeau"], "colour": "#d8d2c8", "triangles": 1674, "layer": "underwear"}
    clothes = {"worn": [{"kind": "shirt", "colour": "#e6e3dc", "version": 2, "triangles": 2826, "seconds": 8.8},
                        {"kind": "trousers", "colour": "#59606b", "version": 2, "triangles": 2652, "seconds": 3.9}], "failed": {}}
    recipe = {"version": 10, "base": FEMALE, "values": {"height": 0.2}, "hair": {"version": 1, "style": "crop", "color": "#33241a"}}
    assets = stored(lib, new_npc(lib), dressing=worn, clothes=clothes, recipe=recipe, left=["coat"], hair_said={"style": "crop", "stand_in": True})
    designed, rig = assets["model_3d"]["designed"], assets["rig_3d"]
    assert designed["clothes"] == [{"kind": "shirt", "colour": "#e6e3dc", "version": 2}, {"kind": "trousers", "colour": "#59606b", "version": 2}]       # (a report that has no looseness: as it was)
    assert designed["clothes_left"] == ["coat"] and "clothes_why" not in designed and designed["hair"] == {"style": "crop", "stand_in": True} and "clothes_failed" not in designed
    assert assets["model_3d"]["faces"] == rig["faces"] == 43268 + 1674 + 2826 + 2652 and designed["triangles"] == 43268                      # (all it is made of)
    assert rig["notes"] == ["an adult body with eyes and Crop hair, wearing plain underwear (a layer of its own: the mesh ForgeUnderwear), a sleeveless shirt and fitted trousers "
                            "(layers of their own)"]
    missed = {"worn": clothes["worn"][:1], "failed": {"trousers": "garment.fit: the body is inside the garment"}}
    designed = stored(lib, new_npc(lib), dressing=worn, clothes=missed)["model_3d"]["designed"]
    assert [c["kind"] for c in designed["clothes"]] == ["shirt"] and designed["clothes_failed"] == {"trousers": "garment.fit: the body is inside the garment"}
    only = stored(lib, new_npc(lib), clothes={"worn": clothes["worn"][1:], "failed": {}})["rig_3d"]["notes"]                                  # (no underwear: a local extension took it off)
    assert only == ["an adult body with eyes, wearing fitted trousers (layers of their own)"]
    bare = stored(lib, new_npc(lib), recipe=recipe)
    assert "clothes" not in bare["model_3d"]["designed"] and bare["rig_3d"]["notes"] == ["an adult body with eyes and Crop hair: no clothes or garments are part of it yet"]


# ---- the real thing --------------------------------------------------------------------------------------------------------------------------
def real_cfg():
    cfg = config.load()
    return cfg if bodymaker.availability(cfg)["installed"] else None


@pytest.mark.skipif(real_cfg() is None, reason="needs the Body Designer, Blender and the animation library")
def test_a_real_body_is_built_rigged_and_animated_and_passes_its_own_checks(tmp_path):
    cfg, notes = real_cfg(), []
    from bodydesigner import api as designer
    recipe = designer.default_recipe(FEMALE)
    recipe["values"]["height"] = 0.1
    plain, animated, reports = bodymaker.build(cfg, designer.clean_recipe(recipe), "low", note=notes.append)
    clips = bodymaker.check(plain, animated, "low")
    assert len(clips) == 85 and notes == ["Building the body from its recipe", "Copying the animation clips onto its skeleton", "Putting its underwear on"]
    assert bodymaker.underwear.worn(plain) and bodymaker.underwear.worn(animated)                             # the same underwear on the body and on the animated one
    worn = reports["dressing"]
    assert worn["kinds"] == ["briefs", "bandeau"] and 1000 < worn["triangles"] < 3000 and worn["bytes_added"] < 300_000
    report = reports["build"]
    assert report["triangles"] == 43268 and report["joints"] == 23 and report["file_bytes"] + worn["bytes_added"] == len(plain) < 20_000_000
    assert len(animated) < 20_000_000 and 1.6 < report["size_m"][1] < 1.9 and reports["seconds"] < 120
    assert bodymaker.glb_json(plain)["asset"]["extras"]["body_design"]["values"]["height"] == 0.1


# ---- the jacket and the apron, and the colour of each garment ---------------------------------------------------------------------------------------
@needs_designer
def test_the_jacket_or_the_apron_the_sheet_names_goes_over_the_shirt_and_trousers_and_what_goes_over_them_can_be_chosen(lib):
    from npcgen import outfit
    blazer = lib.get(new_npc(lib, appearance={"outfit": "a camel blazer over a white top, wide-leg trousers and heels"}))
    white, camel = outfit.read("a white top")["top"]["colour"], outfit.named_colour("camel")
    got = bodymaker.prepare(lib, blazer, request(clothes="basic"))
    assert got["pieces"] == [{"kind": "shirt", "colour": white}, {"kind": "trousers"}, {"kind": "jacket", "colour": camel}] and got["left"] == [] and got["why"] == {}
    assert got["outer"] == "sheet" and got["colours"] == {}
    assert bodymaker.prepare(lib, blazer, request(clothes="sheet"))["pieces"] == got["pieces"]                                 # (the sheet names all three)
    bare = bodymaker.prepare(lib, blazer, request(clothes="basic", outer="none"))
    assert bare["pieces"] == [{"kind": "shirt", "colour": white}, {"kind": "trousers"}] and bare["left"] == []                  # (asked for none: nothing is said about the blazer)
    apron = bodymaker.prepare(lib, blazer, request(clothes="basic", outer="apron"))
    assert apron["pieces"][-1] == {"kind": "apron"} and [p["kind"] for p in apron["pieces"]] == ["shirt", "trousers", "apron"]
    painted = bodymaker.prepare(lib, blazer, request(clothes="basic", colours={"jacket": "#112233", "trousers": "#ABCDEF", "apron": "#000000"}))
    assert painted["pieces"] == [{"kind": "shirt", "colour": white}, {"kind": "trousers", "colour": "#abcdef"}, {"kind": "jacket", "colour": "#112233"}]      # (a colour for a garment not worn is ignored)
    assert painted["colours"] == {"jacket": "#112233", "trousers": "#abcdef", "apron": "#000000"}
    with pytest.raises(ValueError, match="goes over a shirt and trousers only .* this outfit has neither"):
        bodymaker.prepare(lib, blazer, request(clothes="none", outer="jacket"))
    cook = lib.get(new_npc(lib, appearance={"outfit": "a cream blouse, a long skirt and an apron"}))
    cream = outfit.read("a cream blouse")["top"]["colour"]
    sheet = bodymaker.prepare(lib, cook, request(clothes="sheet"))
    assert sheet["pieces"] == [{"kind": "shirt", "colour": cream}, {"kind": "skirt"}] and sheet["left"] == ["apron"]                 # a skirt: no trousers, so the apron has nothing to go over
    assert list(sheet["why"]) == ["apron"] and sheet["why"]["apron"].endswith("and this outfit has a skirt")
    with pytest.raises(ValueError, match="this outfit has a skirt"):
        bodymaker.prepare(lib, cook, request(clothes="sheet", outer="apron"))                                                   # (asked for: said, not left off)
    basic = bodymaker.prepare(lib, cook, request(clothes="basic"))
    assert [p["kind"] for p in basic["pieces"]] == ["shirt", "skirt"] and basic["left"] == ["apron"] and list(basic["why"]) == ["apron"]
    state = bodymaker.status(lib, cook)["outfit"]
    assert set(state) == {"basic", "sheet"} and set(state["basic"]) == set(outfit.OUTERS) == {"sheet", "none", "jacket", "apron"}
    assert state["sheet"]["none"]["pieces"] == [{"kind": "shirt", "colour": cream}, {"kind": "skirt"}] and state["basic"]["jacket"]["problem"].endswith("this outfit has a skirt")
    assert state["sheet"]["jacket"]["problem"].endswith("this outfit has a skirt") and state["basic"]["sheet"]["problem"] == ""
    trousers = lib.get(new_npc(lib, appearance={"outfit": "a cream blouse, canvas trousers and an apron"}))                      # (the same sheet with trousers: the apron goes on)
    assert [p["kind"] for p in bodymaker.prepare(lib, trousers, request(clothes="sheet"))["pieces"]] == ["shirt", "trousers", "apron"]


@needs_designer
def test_a_body_designer_without_a_jacket_does_not_offer_it_and_leaves_the_word_with_its_reason(lib, monkeypatch):
    from npcgen import outfit
    real = outfit._catalogue()
    monkeypatch.setattr(outfit, "_catalogue", lambda: {k: v for k, v in real.items() if k != "jacket"})
    blazer = lib.get(new_npc(lib, appearance={"outfit": "a camel blazer over a white top, wide-leg trousers and heels"}))
    got = bodymaker.prepare(lib, blazer, request(clothes="basic"))
    assert [p["kind"] for p in got["pieces"]] == ["shirt", "trousers"] and got["left"] == ["blazer"] and got["why"] == {"blazer": "This Body Designer does not make it"}
    with pytest.raises(ValueError, match="does not make the jacket"):
        bodymaker.prepare(lib, blazer, request(clothes="basic", outer="jacket"))
    assert [o["id"] for o in bodymaker.availability(config.load())["outers"]] == ["sheet", "none", "apron"]


def test_the_body_records_how_loose_and_how_long_each_garment_is_and_why_a_word_was_left_off(lib):
    worn = {"kinds": ["briefs", "bandeau"], "colour": "#d8d2c8", "triangles": 1674, "layer": "underwear"}
    clothes = {"worn": [{"kind": "shirt", "colour": "#e6e3dc", "version": 3, "ease_mm": 12.0, "length": 1.0, "texture": "plain", "triangles": 2826, "seconds": 8.8},
                        {"kind": "trousers", "colour": "#59606b", "version": 2, "ease_mm": 4.0, "length": 1.0, "texture": "weave", "triangles": 2652, "seconds": 3.9},
                        {"kind": "apron", "colour": "#c6b39a", "version": 2, "ease_mm": 0.0, "length": 1.0, "texture": "weave", "triangles": 6712, "seconds": 5.1}], "failed": {}}
    recipe = {"version": 10, "base": FEMALE, "values": {"height": 0.2}}
    assets = stored(lib, new_npc(lib), dressing=worn, clothes=clothes, recipe=recipe, left=["jacket"], why={"jacket": "A body wears one outer garment, and the apron came first"})
    designed, rig = assets["model_3d"]["designed"], assets["rig_3d"]
    assert designed["clothes"] == [{"kind": "shirt", "colour": "#e6e3dc", "version": 3, "ease_mm": 12.0, "length": 1.0, "texture": "plain"},
                                   {"kind": "trousers", "colour": "#59606b", "version": 2, "ease_mm": 4.0, "length": 1.0, "texture": "weave"},
                                   {"kind": "apron", "colour": "#c6b39a", "version": 2, "ease_mm": 0.0, "length": 1.0, "texture": "weave"}]
    assert designed["clothes_left"] == ["jacket"] and designed["clothes_why"] == {"jacket": "A body wears one outer garment, and the apron came first"}
    assert rig["notes"] == ["an adult body with eyes, wearing plain underwear (a layer of its own: the mesh ForgeUnderwear), a sleeveless shirt, fitted trousers and a bib apron (layers of their own)"]


# ---- new colours on a body that is made ---------------------------------------------------------------------------------------------------------------
@pytest.fixture
def painted(lib):
    """A character whose model and rig are a shirt's own pattern (it has the material a dressed body's shirt has: all `recolour` looks at), with the record of a designed body in a shirt."""
    from npcgen import clothing
    pattern, _ = clothing.build({"kind": "shirt", "colour": "#a02020"})
    npc_id = new_npc(lib)
    lib.write_asset(npc_id, "model_3d.glb", pattern)
    lib.write_asset(npc_id, "model_3d_rigged.glb", pattern)
    lib.write_asset(npc_id, "model_3d_preview.png", b"old picture")

    def apply(npc):
        npc["assets"] = {"model_3d": {"file": "model_3d.glb", "created": "2026-10-10T10:00:00", "size": len(pattern), "preview": "model_3d_preview.png",
                                      "designed": {"base": FEMALE, "clothes": [{"kind": "shirt", "colour": "#a02020", "version": 3, "texture": "plain"}]}},
                         "rig_3d": {"file": "model_3d_rigged.glb", "created": "2026-10-10T10:00:00", "size": len(pattern), "source_model_created": "2026-10-10T10:00:00", "joints_from": "designed body"}}
    lib.update(npc_id, apply)
    return npc_id


def shirt_colour(lib, npc_id, rel):
    from test_clothing import fabric
    return fabric(lib.read_asset(npc_id, rel), "ForgeShirtFabric")[0]


@needs_designer
def test_new_colours_go_onto_both_files_and_the_record_without_a_new_body(lib, painted, monkeypatch):
    from test_clothing import near
    monkeypatch.setattr(bodymaker, "preview", lambda cfg, plain: b"a new picture")
    before = lib.get(painted)["assets"]
    done = bodymaker.recolour({}, lib, painted, {"shirt": "#2020A0"})
    assert done["changed"] == {"shirt": "#2020a0"} and done["clothes"] == [{"kind": "shirt", "colour": "#2020a0", "version": 3, "texture": "plain"}] and done["preview"] is True
    after = lib.get(painted)["assets"]
    for rel in ("model_3d.glb", "model_3d_rigged.glb"):
        assert near(shirt_colour(lib, painted, rel), "#2020a0", 4), rel                                             # (the plain file and the animated one alike)
    assert lib.read_asset(painted, "model_3d_preview.png") == b"a new picture"
    assert after["model_3d"]["created"] != before["model_3d"]["created"] and after["rig_3d"]["created"] == after["model_3d"]["created"] == after["rig_3d"]["source_model_created"]
    assert after["model_3d"]["size"] == len(lib.read_asset(painted, "model_3d.glb")) and after["rig_3d"]["size"] == len(lib.read_asset(painted, "model_3d_rigged.glb"))
    assert after["model_3d"]["designed"]["base"] == FEMALE and after["rig_3d"]["joints_from"] == "designed body"      # (nothing else of the record changed)
    again = bodymaker.recolour({}, lib, painted, {"shirt": "#2020a0"})
    assert again == {"changed": {}, "clothes": done["clothes"], "preview": False} and lib.get(painted)["assets"] == after       # (the same colour: nothing is written)


@needs_designer
def test_a_colour_that_cannot_go_on_this_body_is_refused_and_nothing_is_changed(lib, painted, monkeypatch):
    before = lib.get(painted)["assets"]
    files = {rel: lib.read_asset(painted, rel) for rel in ("model_3d.glb", "model_3d_rigged.glb")}
    for colours, message in (({}, "Say which garment"), (None, "Say which garment"), ({"jacket": "#112233"}, r"wears no jacket \(it wears shirt\)"), ({"hat": "#112233"}, "not a garment"),
                             ({"shirt": "red"}, "the colour must be like"), ({"shirt": 5}, "the colour must be like"), ("red", "must be an object")):
        with pytest.raises(ValueError, match=message):
            bodymaker.recolour({}, lib, painted, colours)
    plain = new_npc(lib)
    with pytest.raises(ValueError, match="not a designed body"):
        bodymaker.recolour({}, lib, plain, {"shirt": "#112233"})
    assert lib.get(painted)["assets"] == before and {rel: lib.read_asset(painted, rel) for rel in files} == files
    lib.update(painted, lambda npc: npc["assets"]["model_3d"]["designed"].pop("clothes"))
    with pytest.raises(ValueError, match="wears no clothes"):
        bodymaker.recolour({}, lib, painted, {"shirt": "#112233"})


@needs_designer
def test_a_body_that_changed_while_its_colours_were_changed_is_left_alone(lib, painted, monkeypatch):
    def newer(cfg, plain):                                                    # (the picture takes a few seconds: a new body can arrive meanwhile)
        lib.update(painted, lambda npc: npc["assets"]["model_3d"].update(created="2026-10-10T11:00:00"))
        return b"a picture"
    monkeypatch.setattr(bodymaker, "preview", newer)
    before = {rel: lib.read_asset(painted, rel) for rel in ("model_3d.glb", "model_3d_rigged.glb")}
    with pytest.raises(BodyError, match="changed while its colours were being changed"):
        bodymaker.recolour({}, lib, painted, {"shirt": "#112233"})
    assert {rel: lib.read_asset(painted, rel) for rel in before} == before and lib.read_asset(painted, "model_3d_preview.png") == b"old picture"


@needs_designer
def test_the_route_changes_the_colours_and_refuses_what_cannot_be_done(api, painted_in_app, monkeypatch):
    monkeypatch.setattr(bodymaker, "preview", lambda cfg, plain: b"a picture")
    status, got = api("POST", f"/api/npcs/{painted_in_app}/body-3d/colours", {"colours": {"shirt": "#2020a0"}})
    assert status == 200 and got["changed"] == {"shirt": "#2020a0"} and got["preview"] is True
    status, got = api("POST", f"/api/npcs/{painted_in_app}/body-3d/colours", {"colours": {"jacket": "#2020a0"}})
    assert status == 400 and "wears no jacket" in got["error"]
    assert api("POST", f"/api/npcs/{painted_in_app}/body-3d/colours", {})[0] == 400
    assert api("POST", "/api/npcs/no-such-npc-123456/body-3d/colours", {"colours": {"shirt": "#2020a0"}})[0] == 404
    wait = threading.Event()
    monkeypatch.setattr(bodymaker, "build", fake_build(wait=wait))
    api.app.jobs.submit(api.app.lib.get(painted_in_app), {"body_3d": prepared()}, {})
    time.sleep(0.2)
    status, got = api("POST", f"/api/npcs/{painted_in_app}/body-3d/colours", {"colours": {"shirt": "#aa0000"}})
    wait.set()
    assert status == 409 and "waiting or running" in got["error"]


@pytest.fixture
def painted_in_app(api):
    from npcgen import clothing
    lib = api.app.lib
    pattern, _ = clothing.build({"kind": "shirt", "colour": "#a02020"})
    npc_id = new_npc(lib)
    lib.write_asset(npc_id, "model_3d.glb", pattern)
    lib.update(npc_id, lambda npc: npc.update(assets={"model_3d": {"file": "model_3d.glb", "created": "2026-10-10T10:00:00", "size": len(pattern),
                                                                  "designed": {"base": FEMALE, "clothes": [{"kind": "shirt", "colour": "#a02020", "version": 3, "texture": "plain"}]}}}))
    return npc_id


def test_the_note_says_the_garments_being_fitted_as_a_list(tmp_path, monkeypatch):
    fake_files(tmp_path, monkeypatch)
    monkeypatch.setattr(bodymaker, "_dress", lambda plain, animated, wanted, cancel=None: (plain, animated, {"worn": [], "failed": {}}))
    notes = []
    bodymaker.build({}, {"base": FEMALE}, "low", note=notes.append, clothes=[{"kind": "shirt"}, {"kind": "trousers"}, {"kind": "jacket"}])
    assert notes[-1] == "Fitting its shirt, trousers and jacket"
    notes.clear()
    bodymaker.build({}, {"base": FEMALE}, "low", note=notes.append, clothes=[{"kind": "apron"}])
    assert notes[-1] == "Fitting its apron"


def test_the_colours_of_the_command_line_are_garment_equals_colour():
    from npcgen import cli
    assert cli.garment_colours([]) == {} and cli.garment_colours(["shirt=#aabbcc", "jacket = navy"]) == {"shirt": "#aabbcc", "jacket": "navy"}       # (what the garment and the colour must be is the body maker's to say)
    for bad in (["shirt"], ["=#aabbcc"], ["shirt="], [""]):
        with pytest.raises(SystemExit, match="GARMENT=#RRGGBB"):
            cli.garment_colours(bad)


@needs_designer
def test_the_command_line_changes_the_colours_of_a_made_body_and_says_what_cannot_be_done(lib, painted, monkeypatch, capsys):
    import types
    from npcgen import cli
    monkeypatch.setattr(bodymaker, "preview", lambda cfg, plain: b"a picture")
    app = types.SimpleNamespace(lib=lib, cfg={})
    args = types.SimpleNamespace(body_cmd="colours", character=painted, colour=["shirt=#2020A0"])
    assert cli.cmd_body(args, None, app) == 0
    said = capsys.readouterr().out
    assert "shirt #2020a0" in said and "picture drawn again" in said
    assert lib.get(painted)["assets"]["model_3d"]["designed"]["clothes"][0]["colour"] == "#2020a0"
    assert cli.cmd_body(args, None, app) == 0 and "already those colours" in capsys.readouterr().out
    for colours, message in ((["jacket=#112233"], "wears no jacket"), (["shirt=red"], "the colour must be like"), (["nonsense"], "GARMENT=#RRGGBB")):
        args.colour = colours
        with pytest.raises(SystemExit, match=message):
            cli.cmd_body(args, None, app)


# ---- skin marks and face expressions ---------------------------------------------------------------------------------------------------------------
needs_marks = pytest.mark.skipif(not bodymaker.bases() or not marks.offered(), reason="this Body Designer paints no skin marks")
needs_shapes = pytest.mark.skipif(not bodymaker.bases() or not bodymaker.offers("expressions"), reason="this Body Designer has no face expressions")
MARK_PLACES = ["face", "cheeks", "nose", "forehead", "neck", "shoulders", "arms", "hands", "chest", "back", "legs"]
MARKS_CATALOGUE = {"version": 1, "minimum_age": 18, "maximum_marks": 16, "places": MARK_PLACES,
                   "kinds": [{"id": k, "size_mm": r} for k, r in (("freckles", [8, 150]), ("vitiligo", [8, 150]), ("mole", [1, 12]), ("beauty_mark", [1, 8]), ("birthmark", [3, 150]), ("scar", [5, 100]))]}
FREE_TEXT = "freckles across the nose, a faded scar on the left cheek and a tattoo on the forearm"


def test_the_marks_of_the_sheet_go_into_the_recipe_and_a_request_can_clear_them(monkeypatch):
    monkeypatch.setattr(marks, "catalogue", lambda: MARKS_CATALOGUE)                                      # (this Body Designer paints marks: a stand-in for its catalogue)
    npc = {"id": "ilsa-1", "appearance": {"distinguishing": FREE_TEXT}}
    base = {"version": 10, "base": FEMALE, "values": {"height": 0.2}}
    got, said = bodymaker.with_marks(base, "sheet", npc)
    assert [(m["kind"], m["place"]) for m in got["appearance"]["marks"]] == [("freckles", "nose"), ("scar", "cheeks")] and got["values"] == {"height": 0.2}
    assert said == {"text": "freckles on the nose; a scar on the left cheek", "skipped": ["a tattoo on the forearm"], "assumed": [], "dropped": 0}
    assert bodymaker.with_marks(base, "sheet", npc) == (got, said)                                        # (the same character, the same marks)
    other = {"id": "otto-2", "appearance": {"distinguishing": FREE_TEXT}}
    assert bodymaker.with_marks(base, "sheet", other)[0]["appearance"]["marks"][0]["seed"] != got["appearance"]["marks"][0]["seed"]
    assert bodymaker.with_marks(got, "none", npc) == (base, None)                                          # none: clear skin, whatever the recipe had
    replaced, _ = bodymaker.with_marks({**base, "appearance": {"marks": [{"kind": "mole", "place": "neck", "size": 4.0, "strength": 1.0, "seed": 1}]}}, "sheet", npc)
    assert [m["kind"] for m in replaced["appearance"]["marks"]] == ["freckles", "scar"]                    # (the sheet's marks take the place of the old ones)
    blank, said = bodymaker.with_marks(base, "sheet", {"id": "x", "appearance": {"distinguishing": "a gap between the front teeth"}})
    assert blank == base and said["text"] == "" and said["skipped"] == []                                  # (nothing named: a body without marks, and the card is told so)


def test_a_body_designer_that_paints_no_marks_leaves_the_recipe_alone_unless_marks_were_asked_for(monkeypatch):
    monkeypatch.setattr(marks, "catalogue", lambda: {})
    base = {"version": 10, "base": FEMALE}
    assert bodymaker.with_marks(base, "sheet", {"id": "x", "appearance": {"distinguishing": "vitiligo"}}, explicit=False) == (base, None)
    with pytest.raises(ValueError, match="paints no skin marks"):
        bodymaker.with_marks(base, "sheet", {"id": "x", "appearance": {"distinguishing": "vitiligo"}})
    assert bodymaker.availability(config.load())["marks"] == []


@needs_marks
def test_a_new_body_has_the_marks_its_sheet_names_and_every_other_body_keeps_its_own(lib):
    from bodydesigner import api
    woman = lib.get(new_npc(lib, appearance={"distinguishing": FREE_TEXT}))

    def made(npc=woman, **more):
        return bodymaker.prepare(lib, npc, request(**more))

    got = made()
    assert [(m["kind"], m["place"]) for m in got["recipe"]["appearance"]["marks"]] == [("freckles", "nose"), ("scar", "cheeks")] and got["marks_said"]["skipped"] == ["a tattoo on the forearm"]
    assert api.clean_recipe(got["recipe"]) == got["recipe"]                                              # the Body Designer takes it as it is
    assert "appearance" not in made(match=False)["recipe"] and made(match=False)["marks_said"] is None   # a neutral body: no marks unless asked for
    assert "appearance" not in made(marks="none")["recipe"] and made(marks="none")["marks_said"] is None
    assert made(match=False, marks="sheet")["recipe"]["appearance"] == got["recipe"]["appearance"]       # (asked for: the sheet's, matched or not)
    own = api.default_recipe(FEMALE)                                                                     # a whole recipe keeps its own marks, or none
    assert "appearance" not in made(recipe=own)["recipe"]
    own["appearance"] = {"marks": [{"kind": "mole", "place": "neck", "size": 4.0, "strength": 1.0, "seed": 9}]}
    assert made(recipe=own)["recipe"]["appearance"] == own["appearance"] and made(recipe=own, marks="none")["recipe"].get("appearance") is None
    assert made(recipe=own, marks="sheet")["recipe"]["appearance"] == got["recipe"]["appearance"]
    saved = lib.get(new_npc(lib, appearance={"distinguishing": FREE_TEXT}))                              # a saved body keeps its marks too
    lib.write_asset(saved["id"], "body_design.json", json.dumps(own).encode())
    assert bodymaker.prepare(lib, saved, request(saved=True))["recipe"]["appearance"] == own["appearance"]
    assert bodymaker.prepare(lib, saved, request(saved=True, marks="none"))["recipe"].get("appearance") is None
    assert bodymaker.prepare(lib, saved, request(saved=True, base=MALE))["recipe"]["appearance"]["marks"][0]["kind"] == "freckles"      # another body is a new body
    state = bodymaker.status(lib, woman)["marks"]
    assert state["text"] == "freckles on the nose; a scar on the left cheek" and state["skipped"] == ["a tattoo on the forearm"] and [m["kind"] for m in state["marks"]] == ["freckles", "scar"]


@needs_designer
def test_marks_asked_for_of_a_body_designer_that_paints_none_are_refused_and_the_default_is_a_body_without(lib, monkeypatch):
    monkeypatch.setattr(marks, "catalogue", lambda: {})
    woman = lib.get(new_npc(lib, appearance={"distinguishing": "vitiligo"}))
    assert "appearance" not in bodymaker.prepare(lib, woman, request())["recipe"] and bodymaker.status(lib, woman)["marks"] is None
    with pytest.raises(ValueError, match="paints no skin marks"):
        bodymaker.prepare(lib, woman, request(marks="sheet"))


@needs_shapes
def test_face_expressions_are_asked_for_and_need_the_natural_face(lib):
    from bodydesigner import api, expressions
    woman = lib.get(new_npc(lib))
    plain = bodymaker.prepare(lib, woman, request())
    assert "expressions" not in plain["recipe"] and plain["expressions"] is False                        # off unless asked for
    shaped = bodymaker.prepare(lib, woman, request(expressions=True))
    assert shaped["recipe"]["expressions"] is True and shaped["expressions"] is True and api.clean_recipe(shaped["recipe"]) == shaped["recipe"]
    assert {k: v for k, v in shaped["recipe"].items() if k != "expressions"} == plain["recipe"]           # (the same body, with the option)
    with pytest.raises(ValueError, match="Natural face"):
        bodymaker.prepare(lib, woman, request(expressions=True, face="classic"))
    assert "expressions" not in bodymaker.prepare(lib, woman, request(expressions=False))["recipe"]
    own = api.default_recipe(FEMALE)
    own.update(bodymaker.natural_profiles())
    own["expressions"] = True                                                                            # a whole recipe or a saved body keeps its own, unless the request says
    assert bodymaker.prepare(lib, woman, request(recipe=own))["recipe"]["expressions"] is True
    assert "expressions" not in bodymaker.prepare(lib, woman, request(recipe=own, expressions=False))["recipe"]
    lib.write_asset(woman["id"], "body_design.json", json.dumps(own).encode())
    assert bodymaker.prepare(lib, woman, request(saved=True))["recipe"]["expressions"] is True
    assert "expressions" not in bodymaker.prepare(lib, woman, request(saved=True, expressions=False))["recipe"]
    info = bodymaker.expressions_info()
    assert info["presets"] == list(expressions.catalogue()["presets"]) and len(info["presets"]) == 13 and str(len(info["presets"])) in info["note"]       # (read from its own catalogue)
    assert bodymaker.availability(config.load())["expressions"] == info


def test_a_body_designer_without_face_expressions_refuses_them_and_has_nothing_to_switch_off(monkeypatch):
    monkeypatch.setattr(bodymaker, "offers", lambda part: part != "expressions")
    with pytest.raises(ValueError, match="no face expressions"):
        bodymaker.with_expressions({"base": FEMALE}, True)
    assert bodymaker.with_expressions({"base": FEMALE, "expressions": True}, False) == {"base": FEMALE}
    assert bodymaker.expressions_info() is None


# ---- the order of a body with face shapes --------------------------------------------------------------------------------------------------------------
SHAPES = ["blink", "blinkLeft", "blinkRight", "happy", "angry", "sad", "relaxed", "surprised", "aa", "ih", "ou", "ee", "oh"]


def shaped_doc(skeleton="low", eyes=True, names=SHAPES):
    doc = body_doc(skeleton)
    doc["meshes"] = [{"name": "ForgeBody", "primitives": [{}], "extras": {"targetNames": list(names)}}]
    if eyes:
        doc["nodes"] += [{"name": "ForgeEye_l", "extras": {"gaze": {"yaw": [-20, 20]}}}, {"name": "ForgeEye_r", "extras": {"gaze": {"yaw": [-20, 20]}}}]
    return doc


@pytest.fixture
def stages(monkeypatch, tmp_path):
    """bodymaker.build with every stage replaced by a recorder, to see the order the files go through (what each stage does is tested where it lives)."""
    log = []
    library = tmp_path / "clips" / "a.glb"
    library.parent.mkdir()
    library.write_bytes(b"x")
    copier = tmp_path / "copier.py"
    copier.write_text("")
    monkeypatch.setattr(bodymaker.rig3d, "find_blender", lambda cfg: "blender")
    monkeypatch.setattr(bodymaker.rig3d, "library_files", lambda cfg: [str(library)])
    monkeypatch.setattr(bodymaker, "COPIER", copier)

    def run(command, tmp, name, doing, timeout, cancel=None):
        if name == "build":
            recipe = json.loads(pathlib.Path(command[command.index("--recipe") + 1]).read_text("utf-8"))
            log.append(("build", sorted(recipe)))
            pathlib.Path(command[command.index("--out") + 1]).write_bytes(glb(body_doc()))
            pathlib.Path(command[command.index("--report") + 1]).write_text(json.dumps({"triangles": 43268, "size_m": [0.5, 1.62, 0.3], "sha256": "abc", "morph_targets": 0}))
        else:
            source = bodymaker.glb_json(pathlib.Path(command[command.index("--source") + 1]).read_bytes())
            log.append(("copy", "shapes" if source.get("meshes") else "neutral"))
            pathlib.Path(command[command.index("--out") + 1]).write_bytes(glb(animated_doc(source)))
            pathlib.Path(command[command.index("--report") + 1]).write_text("{}")
        return ""

    def underwear(blob, kinds):
        log.append(("underwear", "shapes" if bodymaker.glb_json(blob).get("meshes") else "neutral"))
        return blob, {"kinds": kinds, "triangles": 5}

    def dress(plain, animated, wanted, cancel=None):
        log.append(("clothes", [p["kind"] for p in wanted], "plain only" if animated is None else "both files"))
        return plain, animated, {"worn": [{"kind": p["kind"], "triangles": 9} for p in wanted], "failed": {}}

    def shapes(plain, recipe):
        log.append(("shapes", recipe["expressions"]))
        return glb(shaped_doc())

    monkeypatch.setattr(bodymaker, "_run", run)
    monkeypatch.setattr(bodymaker.underwear, "dress", underwear)
    monkeypatch.setattr(bodymaker, "_dress", dress)
    monkeypatch.setattr(bodymaker, "_shapes", shapes)
    return log


def test_a_body_without_face_shapes_is_built_animated_and_then_dressed_both_files_alike(stages):
    plain, animated, reports = bodymaker.build({}, {"version": 10, "base": FEMALE}, "low", clothes=[{"kind": "shirt"}, {"kind": "trousers"}])
    assert stages == [("build", ["base", "version"]), ("copy", "neutral"), ("underwear", "neutral"), ("underwear", "neutral"), ("clothes", ["shirt", "trousers"], "both files")]
    assert reports["clothes"]["worn"][0]["kind"] == "shirt" and reports["build"]["morph_targets"] == 0
    assert bodymaker.glb_json(animated).get("animations")


def test_a_body_with_face_shapes_is_built_without_them_dressed_once_given_them_and_animated_last(stages, monkeypatch):
    monkeypatch.setattr(bodymaker, "expressions_info", lambda: {"presets": SHAPES})                      # (this Body Designer makes them: a stand-in for its catalogue)
    notes = []
    plain, animated, reports = bodymaker.build({}, {"version": 10, "base": FEMALE, "expressions": True}, "low", note=notes.append, clothes=[{"kind": "shirt"}, {"kind": "trousers"}])
    assert stages == [("build", ["base", "version"]), ("underwear", "neutral"), ("clothes", ["shirt", "trousers"], "plain only"), ("shapes", True), ("copy", "shapes")]     # (the file the Designer builds has no option in its recipe)
    assert notes == ["Building the body from its recipe", "Putting its underwear on", "Fitting its shirt and trousers", "Adding its face shapes", "Copying the animation clips onto its skeleton"]
    assert reports["build"]["morph_targets"] == 13 and [w["kind"] for w in reports["clothes"]["worn"]] == ["shirt", "trousers"] and reports["dressing"]["kinds"]
    assert bodymaker.check(plain, animated, "low", expressions=True) and bodymaker.glb_json(animated)["animations"]
    stages.clear()
    bare = bodymaker.build({}, {"version": 10, "base": FEMALE, "expressions": True}, "low")
    assert stages == [("build", ["base", "version"]), ("underwear", "neutral"), ("shapes", True), ("copy", "shapes")]
    assert bodymaker.glb_json(bare[0])["meshes"][0]["extras"]["targetNames"] == SHAPES


def test_face_shapes_and_eye_pivots_are_looked_for_in_both_files_when_they_were_asked_for(monkeypatch):
    monkeypatch.setattr(bodymaker, "expressions_info", lambda: {"presets": SHAPES})
    plain, animated = shaped_doc(), animated_doc(shaped_doc())
    assert len(bodymaker.check(glb(plain), glb(animated), "low", expressions=True)) == 85
    assert len(bodymaker.check(glb(plain), glb(animated), "low")) == 85                                    # (not asked for: not looked for)
    cases = [(body_doc(), animated_doc(body_doc()), "face shapes"), (plain, animated_doc(shaped_doc(names=SHAPES[:-1])), "face shapes"), (shaped_doc(names=SHAPES[::-1]), animated_doc(shaped_doc(names=SHAPES[::-1])), "face shapes"),
             (shaped_doc(eyes=False), animated_doc(shaped_doc(eyes=False)), "eye pivots")]
    for body, rig, reason in cases:
        with pytest.raises(BodyError, match="face shapes and eye pivots"):
            bodymaker.check(glb(body), glb(rig), "low", expressions=True)


def test_the_face_shapes_are_part_of_what_the_body_records_and_says_it_has(lib):
    recipe = {"version": 10, "base": FEMALE, "values": {"height": 0.2}, "expressions": True,
              "appearance": {"marks": [{"kind": "scar", "place": "cheeks", "size": 36.0, "strength": 1.0, "seed": 4, "side": "left", "direction": 35.0}]}}
    said = {"text": "a scar on the left cheek", "skipped": ["a tattoo on the forearm"], "assumed": [], "dropped": 0}
    assets = stored(lib, new_npc(lib), recipe=recipe, marks_said=said)
    designed = assets["model_3d"]["designed"]
    assert designed["marks"] == said and designed["expressions"] is True and designed["recipe"]["expressions"] is True
    assert assets["rig_3d"]["notes"] == ["an adult body with eyes, 1 skin mark painted into its skin, face shapes the engine can blend (a blink, a smile, mouth shapes) and eyes that can look about: no hair, clothes or garments are part of it yet"]
    plain = stored(lib, new_npc(lib), recipe={"version": 10, "base": FEMALE, "values": {}})["model_3d"]["designed"]
    assert "marks" not in plain and "expressions" not in plain


# ---- the try-on of a body with face shapes ---------------------------------------------------------------------------------------------------------------
def shaped_npc(lib, clothes=(), **designed):
    npc_id = new_npc(lib, model=True)
    wearing = [{"kind": k, "colour": c, "version": 3, "ease_mm": 12.0, "length": 1.0, "texture": "plain"} for k, c in clothes]
    record = {"base": FEMALE, "skeleton": "low", "texture": 1024, "clothes": wearing, "recipe": {"version": 10, "base": FEMALE, "expressions": True}, **designed}
    lib.update(npc_id, lambda n: n["assets"]["model_3d"].update(designed=record))
    return lib.get(npc_id)


def fake_rebuild(made):
    def build(cfg, recipe, skeleton, cancel=None, note=None, texture=2048, npc=None, clothes=()):
        made.append({"recipe": recipe, "skeleton": skeleton, "texture": texture, "clothes": list(clothes)})
        failing = {c["kind"] for c in clothes if c.get("colour") == "#000000"}
        worn = [{"kind": c["kind"], "colour": c.get("colour"), "triangles": 5} for c in clothes if c["kind"] not in failing]
        return b"plain", b"animated", {"clothes": {"worn": worn, "failed": {k: "garment.fit: the body is inside the garment" for k in failing}}}
    return build


def test_the_try_on_of_a_body_with_face_shapes_builds_it_again_with_the_clothes_it_keeps_and_the_new_one(lib, monkeypatch):
    made = []
    monkeypatch.setattr(bodymaker, "build", fake_rebuild(made))
    npc = shaped_npc(lib, clothes=[("shirt", "#112233"), ("trousers", "#445566")])
    jacket = {"kind": "jacket", "colour": "#634c3e", "ease_mm": 12.0, "length": 1.0, "texture": "weave"}
    animated, report = bodymaker.refit({}, npc, [jacket])
    assert animated == b"animated" and report == {"worn": [{"kind": "jacket", "colour": "#634c3e", "triangles": 5}], "failed": {}, "replaced": []}
    assert made[0]["recipe"]["expressions"] is True and made[0]["skeleton"] == "low" and made[0]["texture"] == 1024
    assert made[0]["clothes"] == [{"kind": "shirt", "colour": "#112233", "ease_mm": 12.0, "length": 1.0, "texture": "plain"}, {"kind": "trousers", "colour": "#445566", "ease_mm": 12.0, "length": 1.0, "texture": "plain"}, jacket]
    animated, report = bodymaker.refit({}, npc, [{"kind": "dress", "colour": "#7a2030"}])                    # a dress takes the shirt and the trousers off
    assert report["replaced"] == ["shirt", "trousers"] and [c["kind"] for c in made[1]["clothes"]] == ["dress"]
    animated, report = bodymaker.refit({}, npc, [{"kind": "shirt", "colour": "#a02020"}])                    # a shirt takes the place of the shirt
    assert report["replaced"] == ["shirt"] and [(c["kind"], c["colour"]) for c in made[2]["clothes"]] == [("shirt", "#a02020"), ("trousers", "#445566")]


def test_a_garment_that_does_not_fit_the_rebuilt_body_is_said_and_a_garment_it_wears_that_is_lost_stops_the_rebuild(lib, monkeypatch):
    made = []
    monkeypatch.setattr(bodymaker, "build", fake_rebuild(made))
    npc = shaped_npc(lib, clothes=[("shirt", "#112233"), ("trousers", "#445566")])
    _animated, report = bodymaker.refit({}, npc, [{"kind": "jacket", "colour": "#000000"}])
    assert report["worn"] == [] and report["failed"] == {"jacket": "garment.fit: the body is inside the garment"}                       # (the Items tab says it does not fit)
    lost = shaped_npc(lib, clothes=[("shirt", "#000000"), ("trousers", "#445566")])
    with pytest.raises(BodyError, match="could not be built again with the shirt it wears: garment.fit: the body is inside"):
        bodymaker.refit({}, lost, [{"kind": "jacket", "colour": "#634c3e"}])
    for odd in ({"recipe": None}, {"skeleton": "huge"}):
        with pytest.raises(BodyError, match="design was not kept"):
            bodymaker.refit({}, shaped_npc(lib, **odd), [{"kind": "jacket"}])
    assert len(made) == 2                                                                                  # (the two that were built; nothing is built for a design that was not kept)


def test_the_command_line_asks_for_skin_marks_and_face_expressions_and_says_what_it_painted_and_what_it_did_not(lib, monkeypatch, capsys):
    import types
    from npcgen import cli, roll
    data = roll.load_data()
    parse = lambda *more: cli.build_parser(data).parse_args(["body", "use", "ilsa", *more])
    assert (parse().marks, parse().expressions) == (None, None)
    assert (parse("--marks", "none").marks, parse("--expressions").expressions, parse("--no-expressions").expressions) == ("none", True, False)
    with pytest.raises(SystemExit):
        parse("--marks", "freckles")
    seen = {}
    npc = lib.get(new_npc(lib))

    def prepare(lib_, npc_, request_):
        seen.update(request_)
        return {**request_, "recipe": {"version": 10, "base": FEMALE}, "base_label": "Adult female natural anatomy", "face": "natural", "pieces": [{"kind": "shirt"}], "clothes": "basic", "left": [], "why": {},
                "marks_said": {"text": "vitiligo on both hands", "skipped": ["a tattoo on the forearm"], "assumed": ["vitiligo"], "dropped": 0}, "expressions": True}

    monkeypatch.setattr(bodymaker, "prepare", prepare)
    monkeypatch.setattr(cli, "find_npc", lambda app_, key: npc)
    monkeypatch.setattr(cli, "wait_for", lambda app_, jobs: [{"status": "done"}])
    app = types.SimpleNamespace(lib=lib, cfg=config.load(), jobs=types.SimpleNamespace(submit=lambda *args: {"id": "job"}))
    args = parse("--marks", "sheet", "--expressions", "--clothes", "basic")
    assert cli.cmd_body(args, data, app) == 0
    assert seen["marks"] == "sheet" and seen["expressions"] is True
    out, err = capsys.readouterr()
    assert "skin marks: vitiligo on both hands (the sheet names vitiligo but not where, so the places are the usual ones)" in out and "with face shapes and eyes that can look about" in out
    assert "the sheet also names a tattoo on the forearm, which is not painted" in err
    assert cli.cmd_body(parse(), data, app) == 0 and seen["marks"] is None and seen["expressions"] is None


# ---- swinging hair and cloth: the Body Designer's chains of spring bones (bodydesigner/secondary.py) -------------------------------------------------------
needs_swing = pytest.mark.skipif(not bodymaker.bases() or not bodymaker.offers("secondary"), reason="this Body Designer has no swinging hair and cloth")
HAIR_BONES = [f"secondary_hair_{chain:02d}_{segment:02d}" for chain in range(1, 7) for segment in range(1, 4)]
CLOTH_BONES = [f"secondary_dress_{chain:02d}_{segment:02d}" for chain in range(1, 9) for segment in range(1, 4)]


def swung_doc(plain, parts=("hair",)):
    """The animated body of `plain` with the chains of spring bones of `parts` ("hair", "cloth") the way the Body Designer's finalizer writes them: new nodes after the body's own, the first of each chain hung
    on the head (hair) or the pelvis (cloth), their joints after the body's, and a note of what was added in the file's own extras."""
    doc = animated_doc(plain)
    core = len(plain["skins"][0]["joints"])
    names = []
    for part in parts:
        bones, parent = (HAIR_BONES, 5) if part == "hair" else (CLOTH_BONES, 1)
        for start in range(0, len(bones), 3):
            first = len(doc["nodes"])
            for i, name in enumerate(bones[start:start + 3]):
                doc["nodes"].append({"name": name, **({"extras": {"forge_secondary": {"chain": name}}} if i == 0 else {})})
                doc["skins"][0]["joints"].append(len(doc["nodes"]) - 1)
                names.append(name)
            doc["nodes"][parent].setdefault("children", []).append(first)
    doc["asset"]["extras"].update(skeleton="secondary", secondary={"version": 1, "base_skeleton": plain["asset"]["extras"]["skeleton"], "core_joints": core, "joints": core + len(names),
                                                                  "parts": ["hair" if p == "hair" else "dress" for p in parts], "chains": [n[:-3] for n in names if n.endswith("_01")], "bone_names": names})
    return doc


def swing_report(hair=True, cloth=False, left_off=None):
    """What `bodymaker._swing` reports for a body that has the chains asked for."""
    parts = (["hair"] if hair else []) + (["dress"] if cloth else [])
    return {"made": {"hair": hair, "cloth": cloth}, "left_off": left_off or {}, "parts": parts, "joints": 23 + 18 * hair + 24 * cloth, "core_joints": 23,
            "chains": [f"{p}_{i:02d}" for p in parts for i in range(1, 7 if p == "hair" else 9)], "triangles": 43268, "file_bytes": 1000, "sha256": "feed", "seconds": 0.5}


def test_the_parts_that_swing_are_asked_for_as_hair_and_cloth_and_nothing_else():
    assert request()["secondary"] is None and request(secondary=None)["secondary"] is None                  # (not said: none for a new body, as it was for a saved one)
    assert request(secondary=False)["secondary"] == {"hair": False, "cloth": False} == request(secondary={})["secondary"]
    assert request(secondary={"hair": True})["secondary"] == {"hair": True, "cloth": False}
    assert request(secondary={"cloth": True, "hair": True})["secondary"] == {"hair": True, "cloth": True}
    for odd, field in ((True, "secondary"), ("yes", "secondary"), ([], "secondary"), ({"mane": True}, "secondary"), ({"hair": 1}, "secondary.hair"), ({"cloth": "yes"}, "secondary.cloth")):
        with pytest.raises(ValueError, match="body_3d." + field.replace(".", "[.]")):
            request(secondary=odd)


@needs_swing
def test_the_card_is_told_what_this_body_designer_swings_from_its_own_catalogue():
    from bodydesigner import api
    info, data = bodymaker.swinging_info(), api.secondary_catalogue()
    assert info["hair"]["label"] == data["controls"]["hair"]["label"] == "Swinging Long hair" and info["cloth"]["label"] == data["controls"]["cloth"]["label"] == "Swinging skirt or dress"
    assert (info["hair"]["bones"], info["cloth"]["bones"]) == (18, 24) and "18 bones" in info["hair"]["note"] and "24 bones" in info["cloth"]["note"]
    assert info["joints"]["low"] == {"nothing": 23, "hair": 41, "cloth": 47, "both": 65} and info["joints"]["medium"] == {"nothing": 60, "hair": 78, "cloth": 84, "both": 102}
    assert "nothing stops it" in info["note"] and "examples/godot/forge_secondary_motion.gd" in info["note"]
    assert bodymaker.availability(config.load())["swinging"] == info


def test_a_body_designer_without_swinging_parts_refuses_them_and_has_nothing_to_switch_off(monkeypatch):
    monkeypatch.setattr(bodymaker, "offers", lambda part: part != "secondary")
    with pytest.raises(ValueError, match="no swinging hair or cloth"):
        bodymaker.with_secondary({"base": FEMALE}, {"hair": True})
    assert bodymaker.with_secondary({"base": FEMALE, "secondary": {"hair": True, "cloth": False}}, {}) == {"base": FEMALE}
    assert bodymaker.with_secondary({"base": FEMALE}, None) == {"base": FEMALE}
    assert bodymaker.swinging_info() is None


@needs_swing
def test_swinging_hair_needs_the_long_hair_and_swinging_cloth_a_skirt_or_a_dress_with_nothing_over_it(lib):
    from bodydesigner import api, game_hair
    skirt = lib.get(new_npc(lib, appearance={"hair": "black long hair", "outfit": "a cream cotton blouse with rolled sleeves, a faded olive skirt and worn boots"}))
    plain = bodymaker.prepare(lib, skirt, request(clothes="basic"))
    assert "secondary" not in plain["recipe"] and plain["secondary"] is None                                # off unless asked for
    both = bodymaker.prepare(lib, skirt, request(clothes="basic", secondary={"hair": HAS_LONG, "cloth": True}))
    assert both["recipe"]["secondary"] == {"hair": HAS_LONG, "cloth": True} and both["secondary"] == both["recipe"]["secondary"] and api.clean_recipe(both["recipe"]) == both["recipe"]
    assert {k: v for k, v in both["recipe"].items() if k != "secondary"} == plain["recipe"]                 # (the same body, with the option)
    assert "secondary" not in bodymaker.prepare(lib, skirt, request(clothes="basic", secondary=False))["recipe"]
    with pytest.raises(ValueError, match="Swinging Long hair needs the Long hair"):                          # a Crop does not swing
        bodymaker.prepare(lib, skirt, request(clothes="basic", hair="crop", secondary={"hair": True}))
    trousers = lib.get(new_npc(lib, appearance={"outfit": "a white shirt and black trousers"}))
    for mode in ("none", "basic"):                                                                          # no skirt or dress on the body: nothing to swing
        with pytest.raises(ValueError, match="needs a skirt or a dress on the body"):
            bodymaker.prepare(lib, trousers, request(clothes=mode, secondary={"cloth": True}))
    gown = lib.get(new_npc(lib, appearance={"outfit": "a satin midi dress with heels"}))
    assert bodymaker.prepare(lib, gown, request(clothes="basic", secondary={"cloth": True}))["recipe"]["secondary"] == {"hair": False, "cloth": True}
    assert bodymaker.swing_problem({"secondary": {"cloth": True}}, [{"kind": "shirt"}, {"kind": "skirt"}, {"kind": "jacket"}]).startswith("Swinging skirt or dress is made for a skirt or a dress with nothing worn over it")
    assert bodymaker.swing_problem({"secondary": {"cloth": True}}, [{"kind": "apron"}, {"kind": "dress"}]).endswith("or turn swinging cloth off.")
    assert bodymaker.swing_problem({"secondary": {"hair": False, "cloth": False}}, []) is None and bodymaker.swing_problem({}, []) is None
    own = api.default_recipe(FEMALE)                                                                        # a whole recipe or a saved body keeps its own, unless the request says
    own["hair"] = {**game_hair.DEFAULT, "style": "long"}
    own["secondary"] = {"hair": True, "cloth": False}
    assert bodymaker.prepare(lib, skirt, request(recipe=own))["recipe"]["secondary"] == {"hair": True, "cloth": False}
    assert "secondary" not in bodymaker.prepare(lib, skirt, request(recipe=own, secondary=False))["recipe"]
    lib.write_asset(skirt["id"], "body_design.json", json.dumps(own).encode())
    assert bodymaker.prepare(lib, skirt, request(saved=True))["recipe"]["secondary"] == {"hair": True, "cloth": False}
    assert "secondary" not in bodymaker.prepare(lib, skirt, request(saved=True, secondary=False))["recipe"]
    if HAS_LONG:                                                                                            # the sheet's own long hair is enough
        assert bodymaker.prepare(lib, skirt, request(clothes="basic", secondary={"hair": True}))["recipe"]["hair"]["style"] == "long"
    assert bodymaker.prepare(lib, skirt, request(recipe=own, secondary={"hair": True}))["recipe"]["secondary"] == {"hair": True, "cloth": False}


# ---- the order of a body that swings ---------------------------------------------------------------------------------------------------------------------
@pytest.fixture
def swing_stages(stages, monkeypatch):
    """`stages` with the Body Designer's finalizer replaced by a recorder that writes the chains the way it does (swung_doc)."""
    from bodydesigner import secondary

    def apply(source, recipe):
        wanted = secondary.clean(recipe["secondary"])
        stages.append(("secondary", wanted, sorted(recipe)))
        return glb(swung_doc(body_doc("low"), [part for part in ("hair", "cloth") if wanted[part]]))

    def describe(final):
        doc = bodymaker.glb_json(final)
        return {"joints": len(doc["skins"][0]["joints"]), "file_bytes": len(final), "sha256": "feed", "triangles": 43268, "secondary": doc["asset"]["extras"]["secondary"]}

    def shapes(plain, recipe):
        assert "secondary" not in recipe                                                                     # (the face shapes are put on the neutral body: it has no option)
        stages.append(("shapes", recipe["expressions"]))
        return glb(shaped_doc())

    monkeypatch.setattr(secondary, "apply", apply)
    monkeypatch.setattr(secondary, "describe", describe)
    monkeypatch.setattr(bodymaker, "_shapes", shapes)
    return stages


@needs_swing
def test_the_chains_are_added_last_to_the_animated_file_only_and_the_neutral_body_never_sees_the_option(swing_stages):
    notes = []
    recipe = {"version": 10, "base": FEMALE, "secondary": {"hair": True, "cloth": True}}
    plain, animated, reports = bodymaker.build({}, recipe, "low", note=notes.append, clothes=[{"kind": "dress"}])
    assert swing_stages == [("build", ["base", "version"]), ("copy", "neutral"), ("underwear", "neutral"), ("underwear", "neutral"), ("clothes", ["dress"], "both files"),
                            ("secondary", {"hair": True, "cloth": True}, ["base", "secondary", "version"])]
    assert notes[-1] == "Adding the spring bones that swing"
    swing = reports["swing"]
    assert swing["made"] == {"hair": True, "cloth": True} and swing["left_off"] == {} and swing["joints"] == 65 and swing["core_joints"] == 23 and swing["parts"] == ["hair", "dress"]
    assert len(bodymaker.glb_json(plain)["skins"][0]["joints"]) == 23 and len(bodymaker.glb_json(animated)["skins"][0]["joints"]) == 65            # (the plain file keeps the body's own bones)
    assert len(bodymaker.check(plain, animated, "low", swinging=swing["made"])) == 85
    swing_stages.clear()                                                                                    # with face shapes it is still the last of all, and the shapes see no option
    shaped = {"version": 10, "base": FEMALE, "expressions": True, "secondary": {"hair": True, "cloth": False}}
    bodymaker.build({}, shaped, "low", clothes=[{"kind": "shirt"}])
    assert swing_stages == [("build", ["base", "version"]), ("underwear", "neutral"), ("clothes", ["shirt"], "plain only"), ("shapes", True), ("copy", "shapes"),
                            ("secondary", {"hair": True, "cloth": False}, ["base", "expressions", "secondary", "version"])]
    swing_stages.clear()                                                                                    # off: the finalizer is not even called
    bodymaker.build({}, {"version": 10, "base": FEMALE}, "low")
    assert [step[0] for step in swing_stages] == ["build", "copy", "underwear", "underwear"]


@needs_swing
def test_cloth_with_no_skirt_or_dress_to_swing_on_is_left_off_and_said_and_the_rest_is_made(swing_stages, monkeypatch):
    monkeypatch.setattr(bodymaker, "_dress", lambda plain, animated, wanted, cancel=None: (plain, animated, {"worn": [{"kind": "shirt", "triangles": 9}], "failed": {"skirt": "garment.fit: the body is inside the garment"}}))
    both = {"version": 10, "base": FEMALE, "secondary": {"hair": True, "cloth": True}}
    plain, animated, reports = bodymaker.build({}, both, "low", clothes=[{"kind": "shirt"}, {"kind": "skirt"}])
    swing = reports["swing"]
    assert swing["made"] == {"hair": True, "cloth": False} and "skirt or dress" in swing["left_off"]["cloth"] and swing["joints"] == 41 and swing_stages[-1][1] == {"hair": True, "cloth": False}
    assert len(bodymaker.check(plain, animated, "low", swinging=swing["made"])) == 85
    swing_stages.clear()
    cloth_only = {"version": 10, "base": FEMALE, "secondary": {"hair": False, "cloth": True}}
    plain, animated, reports = bodymaker.build({}, cloth_only, "low", clothes=[{"kind": "shirt"}, {"kind": "skirt"}])
    assert reports["swing"]["made"] == {"hair": False, "cloth": False} and "cloth" in reports["swing"]["left_off"] and all(step[0] != "secondary" for step in swing_stages)     # (nothing left to make: the Designer is not asked)
    assert len(bodymaker.check(plain, animated, "low", swinging=reports["swing"]["made"])) == 85                                                                    # (and the body is its own skeleton)


@needs_swing
def test_a_finalizer_that_refuses_is_said_in_its_own_words_and_a_body_set_to_wear_no_underwear_is_refused_before_anything_is_built(swing_stages, monkeypatch):
    from bodydesigner import secondary
    recipe = {"version": 10, "base": FEMALE, "secondary": {"hair": True, "cloth": False}}
    monkeypatch.setattr(secondary, "apply", lambda source, recipe_: (_ for _ in ()).throw(ValueError("secondary.hair: choose Long hair, or turn swinging hair off")))
    with pytest.raises(BodyError, match="The swinging hair could not be added: secondary.hair: choose Long hair, or turn swinging hair off"):
        bodymaker.build({}, recipe, "low")
    swing_stages.clear()
    bodymaker.set_dressing(lambda recipe_, npc: [])
    try:
        with pytest.raises(BodyError, match="plain underwear layer"):
            bodymaker.build({}, recipe, "low")
    finally:
        bodymaker.set_dressing()
    assert swing_stages == []                                                                               # (nothing was built)


@needs_swing
def test_a_dress_the_finalizer_will_not_swing_is_left_off_and_said_in_its_own_words_and_the_rest_is_made(swing_stages, monkeypatch):
    from bodydesigner import secondary
    stand_in = secondary.apply                                                                               # (the fixture's recorder)
    refused = []

    def apply(source, recipe_):
        if recipe_["secondary"]["cloth"]:                                                                    # (a dress adjusted to fit a body shape: the Designer swings only the original fabric)
            refused.append(dict(recipe_["secondary"]))
            raise ValueError("secondary.cloth: use the original 1.2 mm closed fabric")
        return stand_in(source, recipe_)

    monkeypatch.setattr(secondary, "apply", apply)
    both = {"version": 10, "base": FEMALE, "secondary": {"hair": True, "cloth": True}}
    plain, animated, reports = bodymaker.build({}, both, "low", clothes=[{"kind": "dress"}])
    swing = reports["swing"]
    assert swing["made"] == {"hair": True, "cloth": False} and swing["joints"] == 41 and swing["parts"] == ["hair"] and list(swing["left_off"]) == ["cloth"]
    assert "secondary.cloth: use the original 1.2 mm closed fabric" in swing["left_off"]["cloth"] and swing["left_off"]["cloth"].startswith("the Body Designer would not make this skirt or dress swing")
    assert refused == [{"hair": True, "cloth": True}] and swing_stages[-1][:2] == ("secondary", {"hair": True, "cloth": False})        # (asked with both, then with the hair alone)
    assert len(bodymaker.check(plain, animated, "low", swinging=swing["made"])) == 85
    swing_stages.clear()
    refused.clear()
    cloth_only = {"version": 10, "base": FEMALE, "secondary": {"hair": False, "cloth": True}}
    plain, animated, reports = bodymaker.build({}, cloth_only, "low", clothes=[{"kind": "dress"}])
    assert reports["swing"]["made"] == {"hair": False, "cloth": False} and list(reports["swing"]["left_off"]) == ["cloth"] and refused == [{"hair": False, "cloth": True}]
    assert all(step[0] != "secondary" for step in swing_stages) and len(bodymaker.check(plain, animated, "low", swinging=reports["swing"]["made"])) == 85       # (nothing left to make: no spring bones, the body is its own skeleton)
    monkeypatch.setattr(secondary, "apply", lambda source, recipe_: (_ for _ in ()).throw(ValueError("secondary.hair: choose Long hair, or turn swinging hair off")))
    with pytest.raises(BodyError, match="The swinging hair and cloth could not be added: secondary.hair: choose Long hair"):                 # (any other refusal still stops the build, in the Designer's words)
        bodymaker.build({}, both, "low", clothes=[{"kind": "dress"}])


# ---- looking inside the two files -------------------------------------------------------------------------------------------------------------------------
def test_the_animated_body_must_be_the_plain_body_and_exactly_the_chains_that_were_asked_for():
    plain = body_doc("low")
    both = swung_doc(plain, ("hair", "cloth"))
    assert len(bodymaker.check(glb(plain), glb(both), "low", swinging={"hair": True, "cloth": True})) == 85
    assert len(bodymaker.check(glb(plain), glb(swung_doc(plain, ("hair",))), "low", swinging={"hair": True, "cloth": False})) == 85
    medium = body_doc("medium")
    assert len(bodymaker.check(glb(medium), glb(swung_doc(medium, ("cloth",))), "medium", swinging={"hair": False, "cloth": True})) == 85
    assert len(bodymaker.check(glb(plain), glb(animated_doc(plain)), "low", swinging={"hair": False, "cloth": False})) == 85                       # (none asked for: the body's own skeleton)

    def renamed(doc):
        doc["nodes"][-1]["name"] = "tail_03"

    def forgot(doc):
        del doc["asset"]["extras"]["secondary"]

    def other_joint(doc):
        doc["skins"][0]["joints"][2], doc["skins"][0]["joints"][3] = doc["skins"][0]["joints"][3], doc["skins"][0]["joints"][2]

    def stray(doc):
        doc["nodes"].append({"name": "secondary_stray_01_01"})

    def moved(doc):
        doc["nodes"][7]["translation"] = [0.0, 1.0, 0.0]

    wrong = [(plain, both, {"hair": True, "cloth": False}, "swinging chains"), (plain, swung_doc(plain, ("hair",)), {"hair": True, "cloth": True}, "swinging chains"),                  # more or fewer than asked for
             (plain, swung_doc(plain, ("hair",)), None, "not the body's own"), (plain, swung_doc(plain, ("hair",)), {"hair": False, "cloth": False}, "not the body's own")]       # chains nobody asked for
    for mutate, reason in ((renamed, "swinging chains"), (forgot, "swinging chains"), (other_joint, "swinging chains"), (stray, "swinging chains"), (moved, "not the body's own")):
        doc = swung_doc(plain, ("hair",))
        mutate(doc)
        wrong.append((plain, doc, {"hair": True, "cloth": False}, reason))
    keeps = body_doc("low")
    keeps["nodes"][4]["children"] = [9]                                                                      # a body whose own node has a child keeps it, with the chains after it
    kept = swung_doc(keeps, ("hair",))
    assert len(bodymaker.check(glb(keeps), glb(kept), "low", swinging={"hair": True, "cloth": False})) == 85
    lost = swung_doc(keeps, ("hair",))
    lost["nodes"][4]["children"] = []
    wrong.append((keeps, lost, {"hair": True, "cloth": False}, "not the body's own"))
    for body, rig, swinging, reason in wrong:
        with pytest.raises(BodyError, match=reason):
            bodymaker.check(glb(body), glb(rig), "low", swinging=swinging)


# ---- what the body records and says it has -----------------------------------------------------------------------------------------------------------------
@needs_swing
def test_a_body_with_spring_bones_records_them_in_its_model_and_its_rig_and_what_was_left_off(lib):
    recipe = {"version": 10, "base": FEMALE, "hair": {"version": 1, "style": "long", "color": "#463121"}, "secondary": {"hair": True, "cloth": True}}
    left = {"cloth": "the skirt or dress it was to swing on did not fit this body"}
    swing = swing_report(hair=True, cloth=False, left_off=left)
    assets = stored(lib, new_npc(lib), recipe=recipe, swing=swing)
    designed, rig = assets["model_3d"]["designed"], assets["rig_3d"]
    assert designed["recipe"]["secondary"] == {"hair": True, "cloth": False}                                # (the recipe says what swings, not what was asked for)
    assert designed["swinging"] == {"parts": ["hair"], "joints": 41, "core_joints": 23, "chains": swing["chains"]} and designed["swinging_left_off"] == left
    assert rig["bones"] == 41 and rig["core_bones"] == 23 and rig["swinging"] == ["hair"] and rig["skeleton"] == "low" and len(rig["clips"]) == 85
    assert rig["notes"][0].endswith("its Long hair has spring bones (secondary_...) that the game engine swings; no clip moves them")
    both = stored(lib, new_npc(lib), recipe={**recipe, "secondary": {"hair": True, "cloth": True}}, swing=swing_report(hair=True, cloth=True))
    assert both["rig_3d"]["bones"] == 65 and both["rig_3d"]["swinging"] == ["hair", "dress"] and "swinging_left_off" not in both["model_3d"]["designed"]
    assert both["rig_3d"]["notes"][0].endswith("its Long hair and its dress have spring bones (secondary_...) that the game engine swings; no clip moves them")
    none = stored(lib, new_npc(lib), recipe={"version": 10, "base": FEMALE, "values": {}})
    assert "swinging" not in none["model_3d"]["designed"] and none["rig_3d"]["bones"] == 23 and "core_bones" not in none["rig_3d"] and "swinging" not in none["rig_3d"]
    nothing = stored(lib, new_npc(lib), recipe={"version": 10, "base": FEMALE, "secondary": {"hair": False, "cloth": True}},
                     swing={"made": {"hair": False, "cloth": False}, "left_off": left})                   # asked for, none made
    assert "secondary" not in nothing["model_3d"]["designed"]["recipe"] and nothing["model_3d"]["designed"]["swinging_left_off"] == left and nothing["rig_3d"]["bones"] == 23


@needs_swing
def test_the_job_checks_the_chains_it_made_and_says_so_in_its_note(jobs, lib, monkeypatch):
    plain = body_doc("low")
    recipe = {"version": 10, "base": FEMALE, "hair": {"version": 1, "style": "long", "color": "#463121"}, "secondary": {"hair": True, "cloth": False}}

    def build(rig):
        def made(cfg, recipe_, tier, cancel=None, note=None, texture=2048, npc=None, clothes=()):
            return glb(plain), glb(rig), {"build": {"triangles": 43268, "size_m": [0.5, 1.62, 0.3], "sha256": "abc"}, "copy": {}, "swing": swing_report(), "seconds": 12.5}
        return made

    monkeypatch.setattr(bodymaker, "preview", lambda cfg, plain_: b"a picture")
    monkeypatch.setattr(bodymaker, "build", build(swung_doc(plain, ("hair",))))
    npc_id = new_npc(lib)
    done = wait_done(jobs, submit(jobs, lib, npc_id, recipe=recipe)["id"])
    assert done["status"] == "done" and "spring bones on its hair" in done["steps"][0]["note"]
    assert lib.get(npc_id)["assets"]["rig_3d"]["bones"] == 41 and lib.get(npc_id)["assets"]["model_3d"]["designed"]["swinging"]["parts"] == ["hair"]
    monkeypatch.setattr(bodymaker, "build", build(animated_doc(plain)))                                      # a file with no chains although the build says it made them: not used, nothing written
    other = new_npc(lib, model=True)
    done = wait_done(jobs, submit(jobs, lib, other, recipe=recipe)["id"])
    assert done["status"] == "error" and "swinging chains" in done["steps"][0]["error"] and lib.resolve(other, "model_3d.glb").read_bytes() == b"old model"


# ---- the try-on of a body that swings ----------------------------------------------------------------------------------------------------------------------
def test_a_body_with_face_shapes_or_spring_bones_cannot_be_dressed_in_place():
    assert bodymaker.needs_rebuild(None) == "" and bodymaker.needs_rebuild({"recipe": {"version": 10}}) == "" and bodymaker.needs_rebuild({}) == ""
    assert bodymaker.needs_rebuild({"recipe": {"expressions": True}}) == "face shapes"
    assert bodymaker.needs_rebuild({"recipe": {"secondary": {"hair": True, "cloth": False}}}) == "swinging hair or cloth"
    assert bodymaker.needs_rebuild({"recipe": {"expressions": True, "secondary": {"hair": False, "cloth": True}}}) == "face shapes and swinging hair or cloth"


def swinging_npc(lib, clothes=(("shirt", "#112233"), ("skirt", "#445566")), secondary=None):
    return shaped_npc(lib, clothes=clothes, recipe={"version": 10, "base": FEMALE, "hair": {"version": 1, "style": "long", "color": "#463121"}, "secondary": secondary or {"hair": True, "cloth": True}})


@needs_swing
def test_the_try_on_of_a_body_that_swings_keeps_the_hair_and_keeps_the_cloth_only_while_a_skirt_or_a_dress_is_worn(lib, monkeypatch):
    made = []
    monkeypatch.setattr(bodymaker, "build", fake_rebuild(made))
    npc = swinging_npc(lib)
    _animated, report = bodymaker.refit({}, npc, [{"kind": "skirt", "colour": "#a02020"}])                    # another skirt: the cloth swings on it
    assert made[0]["recipe"]["secondary"] == {"hair": True, "cloth": True} and "swinging_left_off" not in report and report["replaced"] == ["skirt"]
    _animated, report = bodymaker.refit({}, npc, [{"kind": "dress", "colour": "#a02020"}])                    # a dress takes the shirt and the skirt off and swings too
    assert made[1]["recipe"]["secondary"] == {"hair": True, "cloth": True} and "swinging_left_off" not in report
    _animated, report = bodymaker.refit({}, npc, [{"kind": "trousers", "colour": "#a02020"}])                 # trousers take the skirt's place: nothing for the cloth to swing on
    assert made[2]["recipe"]["secondary"] == {"hair": True, "cloth": False} and list(report["swinging_left_off"]) == ["cloth"] and "skirt or dress" in report["swinging_left_off"]["cloth"]
    _animated, report = bodymaker.refit({}, npc, [{"kind": "jacket", "colour": "#634c3e"}])                   # a jacket over it: the cloth is not made for that
    assert made[3]["recipe"]["secondary"] == {"hair": True, "cloth": False} and list(report["swinging_left_off"]) == ["cloth"]
    hair_only = swinging_npc(lib, secondary={"hair": True, "cloth": False})
    _animated, report = bodymaker.refit({}, hair_only, [{"kind": "trousers", "colour": "#a02020"}])
    assert made[4]["recipe"]["secondary"] == {"hair": True, "cloth": False} and "swinging_left_off" not in report                              # (the hair does not depend on the clothes)
    assert npc["assets"]["model_3d"]["designed"]["recipe"]["secondary"] == {"hair": True, "cloth": True}                                       # the character itself is not changed
    monkeypatch.setattr(bodymaker, "build", lambda *a, **k: (b"plain", b"animated", {"clothes": {"worn": [{"kind": "skirt"}, {"kind": "shirt"}], "failed": {}}, "swing": swing_report(left_off={"cloth": "its own reason"})}))
    report = bodymaker.refit({}, npc, [{"kind": "skirt", "colour": "#a02020"}])[1]
    assert report["swinging_left_off"] == {"cloth": "its own reason"}                                         # (what the build left off is said too)


# ---- the command line ----------------------------------------------------------------------------------------------------------------------------------------
def test_the_command_line_asks_for_swinging_hair_and_cloth_and_says_what_swings(lib, monkeypatch, capsys):
    import types
    from npcgen import cli, roll
    data = roll.load_data()
    parse = lambda *more: cli.build_parser(data).parse_args(["body", "use", "ilsa", *more])
    assert (parse().swinging_hair, parse().swinging_cloth) == (None, None)
    assert (parse("--swinging-hair").swinging_hair, parse("--no-swinging-cloth").swinging_cloth, parse("--swinging-cloth").swinging_cloth) == (True, False, True)
    seen = {}
    npc = lib.get(new_npc(lib))

    def prepare(lib_, npc_, request_):
        seen.update(request_)
        return {**request_, "recipe": {"version": 10, "base": FEMALE}, "base_label": "Adult female natural anatomy", "face": "natural", "pieces": [{"kind": "dress"}], "clothes": "basic", "left": [], "why": {},
                "secondary": {"hair": True, "cloth": True}}

    monkeypatch.setattr(bodymaker, "prepare", prepare)
    monkeypatch.setattr(cli, "find_npc", lambda app_, key: npc)
    monkeypatch.setattr(cli, "wait_for", lambda app_, jobs: [{"status": "done"}])
    app = types.SimpleNamespace(lib=lib, cfg=config.load(), jobs=types.SimpleNamespace(submit=lambda *args: {"id": "job"}))
    assert cli.cmd_body(parse("--hair", "long", "--swinging-hair", "--swinging-cloth"), data, app) == 0
    assert seen["secondary"] == {"hair": True, "cloth": True}
    assert "with spring bones on its hair and skirt or dress for the game engine to swing" in capsys.readouterr().out
    assert cli.cmd_body(parse("--no-swinging-hair"), data, app) == 0 and seen["secondary"] == {"hair": False, "cloth": False}         # (saying either switches the other off)
    assert cli.cmd_body(parse(), data, app) == 0 and seen["secondary"] is None



# ---- a choice of clothes that a local extension adds -------------------------------------------------------------------------------------------
@pytest.fixture
def choice(monkeypatch):
    """A fresh list of extra choices with one registered the way the private extension does it ("bare": no garments, no underwear); `state` switches it on and off and sets what its check says."""
    monkeypatch.setattr(bodymaker, "EXTRA_CLOTHES", {})
    state = {"on": True, "problem": None}
    bodymaker.add_clothes_choice("bare", "Nothing at all", "No underwear either.", kinds=(), visible=lambda: state["on"], check=lambda npc: state["problem"])
    return state


def test_a_local_extension_adds_a_choice_to_the_clothes_list_only_while_it_says_so(choice):
    ids = lambda: [c["id"] for c in bodymaker.clothes_choices()]
    assert ids() == ["basic", "sheet", "none", "bare"]
    assert bodymaker.clothes_choices()[-1] == {"id": "bare", "label": "Nothing at all", "note": "No underwear either.", "extra": True, "underwear": []}
    assert all("extra" not in c for c in bodymaker.clothes_choices()[:-1])                                  # (the built-in choices say nothing of the kind)
    assert bodymaker.clean_request({"clothes": "bare"})["clothes"] == "bare"
    choice["on"] = False                                                                                   # (the extension's switch goes off: the choice is gone, and the API does not take it)
    assert ids() == ["basic", "sheet", "none"]
    with pytest.raises(ValueError, match="clothes must be basic, sheet, none$"):
        bodymaker.clean_request({"clothes": "bare"})

    def broken():
        raise RuntimeError("the switch broke")

    bodymaker.EXTRA_CLOTHES["bare"]["visible"] = broken                                                   # (a switch that fails hides it too)
    assert ids() == ["basic", "sheet", "none"]
    with pytest.raises(ValueError, match="clothes must be basic, sheet, none$"):
        bodymaker.clean_request({"clothes": "bare"})
    bodymaker.EXTRA_CLOTHES["bare"]["visible"] = None                                                     # (no switch: always listed)
    assert ids()[-1] == "bare"
    for odd in (3, None, ["bare"], {"bare": 1}):
        with pytest.raises(ValueError, match="clothes must be"):
            bodymaker.clean_request({"clothes": odd})


def test_a_choice_of_clothes_needs_a_plain_id_that_is_not_taken_and_pieces_that_exist(choice):
    for bad in ("Bare", "b", "basic", "none", "has space", "", None, 3, "x" * 40):
        with pytest.raises(ValueError, match="cannot be the id"):
            bodymaker.add_clothes_choice(bad, "Label", "Note")
    with pytest.raises(ValueError, match="'thong', which is not a piece of underwear"):
        bodymaker.add_clothes_choice("thong_only", "Label", "Note", kinds=["thong"])
    assert sorted(bodymaker.EXTRA_CLOTHES) == ["bare"]                                                    # (a refused one is not half-registered)
    bodymaker.add_clothes_choice("briefs_only", "Briefs", "Only briefs.", kinds=["briefs"])
    assert bodymaker.EXTRA_CLOTHES["briefs_only"]["kinds"] == ["briefs"] and bodymaker.clothes_choices()[-1]["underwear"] == ["briefs"]
    bodymaker.add_clothes_choice("bare", "Renamed", "Again.")                                              # (the same id again replaces it, as for a page)
    assert bodymaker.EXTRA_CLOTHES["bare"]["label"] == "Renamed"


@needs_designer
def test_the_extra_choice_makes_a_body_with_no_garments_and_the_underwear_it_names(lib, choice):
    npc = lib.get(new_npc(lib))
    got = bodymaker.prepare(lib, npc, request(clothes="bare"))
    assert got["clothes"] == "bare" and got["pieces"] == [] and got["wear"] == [] and got["left"] == [] and got["why"] == {}
    assert "wear" not in bodymaker.prepare(lib, npc, request()) and "wear" not in bodymaker.prepare(lib, npc, request(clothes="none"))     # (the usual choices: the request is what it always was)
    bodymaker.add_clothes_choice("briefs_only", "Briefs only", "Only briefs.", kinds=["briefs"])
    assert bodymaker.prepare(lib, npc, request(clothes="briefs_only"))["wear"] == ["briefs"]
    assert bodymaker.prepare(lib, npc, request(clothes="bare", outer="none"))["pieces"] == []
    assert bodymaker.prepare(lib, npc, request(clothes="bare", colours={"shirt": "#ff0000"}))["pieces"] == []                                 # (a colour for a garment it does not wear is nothing)
    with pytest.raises(ValueError, match="Nothing is worn over this choice of clothes"):
        bodymaker.prepare(lib, npc, request(clothes="bare", outer="jacket"))
    choice["problem"] = "She is not an adult."                                                            # (the extension's check on this character)
    with pytest.raises(ValueError, match="She is not an adult."):
        bodymaker.prepare(lib, npc, request(clothes="bare"))
    assert bodymaker.prepare(lib, npc, request(clothes="none"))["clothes"] == "none"                       # (and only on its own choice)
    choice["problem"] = None

    def cannot_run(who):
        raise RuntimeError("no way to look")

    bodymaker.EXTRA_CLOTHES["bare"]["check"] = cannot_run                                                  # (a check that cannot run refuses instead of waving it through)
    with pytest.raises(ValueError, match=r"could not run \(no way to look\), so nothing was made"):
        bodymaker.prepare(lib, npc, request(clothes="bare"))
    choice["on"] = False
    with pytest.raises(ValueError, match="clothes must be basic, sheet, none"):                              # (the box was unticked between the request and the job: nothing is made)
        bodymaker.prepare(lib, npc, {**request(), "clothes": "bare"})


@needs_designer
def test_swinging_hair_and_cloth_are_refused_early_for_a_choice_that_leaves_the_underwear_off(lib, choice):
    if not bodymaker.offers("secondary"):
        pytest.skip("this Body Designer has no swinging hair or cloth")
    npc = lib.get(new_npc(lib))
    for swing in ({"hair": True}, {"cloth": True}):
        with pytest.raises(ValueError, match="in its underwear, and this choice of clothes leaves it off"):
            bodymaker.prepare(lib, npc, request(clothes="bare", hair="long", secondary=swing))
    bodymaker.add_clothes_choice("briefs_only", "Briefs only", "Only briefs.", kinds=["briefs"])
    assert bodymaker.prepare(lib, npc, request(clothes="briefs_only", hair="long", secondary={"hair": True}))["secondary"] == {"hair": True, "cloth": False}       # (a choice with underwear keeps them possible)


def test_a_body_built_with_a_wear_list_is_dressed_in_exactly_those_pieces(stages, monkeypatch):
    seen = []
    monkeypatch.setattr(bodymaker, "_dressing", lambda recipe, npc=None: seen.append(recipe["base"]) or ["briefs", "bandeau"])
    plain, animated, reports = bodymaker.build({}, {"version": 10, "base": FEMALE}, "low", wear=[])
    assert stages == [("build", ["base", "version"]), ("copy", "neutral")] and "dressing" not in reports            # (nothing on it but its skin: no underwear step at all)
    assert seen == []                                                                                     # (the policy is not asked: the choice decides)
    stages.clear()
    plain, animated, reports = bodymaker.build({}, {"version": 10, "base": FEMALE}, "low", wear=["briefs"])
    assert reports["dressing"]["kinds"] == ["briefs"] and stages == [("build", ["base", "version"]), ("copy", "neutral"), ("underwear", "neutral"), ("underwear", "neutral")]
    stages.clear()
    plain, animated, reports = bodymaker.build({}, {"version": 10, "base": FEMALE}, "low")
    assert reports["dressing"]["kinds"] == ["briefs", "bandeau"] and seen == [FEMALE]                      # (no wear list: the policy decides, as ever)
    stages.clear()
    with pytest.raises(BodyError, match="'thong', which is not a piece of underwear"):
        bodymaker.build({}, {"version": 10, "base": FEMALE}, "low", wear=["thong"])
    with pytest.raises(BodyError, match="wears the plain underwear layer"):                                # (the Body Designer's swing step needs the layer: refused before anything runs)
        bodymaker.build({}, {"version": 10, "base": FEMALE, "secondary": {"hair": True, "cloth": False}}, "low", wear=[])
    assert stages == []


def test_a_body_made_with_an_extra_choice_records_it_and_what_it_wears(lib):
    plain = body_doc()
    rig = animated_doc(plain)
    reports = {"build": {"triangles": 43268, "size_m": [0.5, 1.62, 0.3], "sha256": "abc"}, "seconds": 12.5}
    clips = bodymaker.check(glb(plain), glb(rig), "low")
    npc_id = new_npc(lib)
    bodymaker.store(lib, npc_id, glb(plain), glb(rig), clips, prepared(clothes="bare", wear=[]), reports, None)
    designed = lib.get(npc_id)["assets"]["model_3d"]["designed"]
    assert designed["clothes_choice"] == "bare" and designed["underwear"] == [] and "wears" not in designed and "clothes" not in designed
    other = new_npc(lib)
    bodymaker.store(lib, other, glb(plain), glb(rig), clips, prepared(clothes="none"), reports, None)
    usual = lib.get(other)["assets"]["model_3d"]["designed"]
    assert "clothes_choice" not in usual and "underwear" not in usual                                      # (a body made the usual way records what it always did)


def test_the_job_gives_the_build_the_underwear_of_the_choice(jobs, lib, monkeypatch):
    monkeypatch.setattr(bodymaker, "EXTRA_CLOTHES", {})
    bodymaker.add_clothes_choice("bare", "Bare for testing", "", kinds=())
    made = []

    def build(cfg, recipe, skeleton, cancel=None, note=None, texture=2048, npc=None, clothes=(), soft=None, bust=0.0, wear=None):
        made.append(wear)
        return fake_build()(cfg, recipe, skeleton, cancel, note, texture, npc, clothes)

    monkeypatch.setattr(bodymaker, "build", build)
    monkeypatch.setattr(bodymaker, "preview", lambda cfg, plain: b"a picture")
    npc_id = new_npc(lib)
    assert wait_done(jobs, submit(jobs, lib, npc_id, clothes="bare", wear=[])["id"])["status"] == "done"
    assert made == [[]]                                                                                    # (an empty list is a choice: it is not left out)
    designed = lib.get(npc_id)["assets"]["model_3d"]["designed"]
    assert designed["underwear"] == [] and designed["clothes_choice"] == "bare"
    made.clear()
    assert wait_done(jobs, submit(jobs, lib, new_npc(lib))["id"])["status"] == "done"
    assert made == [None]


def test_the_try_on_builds_the_body_again_in_the_underwear_it_was_made_with(lib, monkeypatch):
    monkeypatch.setattr(bodymaker, "EXTRA_CLOTHES", {})
    bodymaker.add_clothes_choice("bare", "Bare for testing", "", kinds=())
    bodymaker.add_clothes_choice("briefs_only", "Briefs for testing", "", kinds=("briefs",))
    made = []

    def build(cfg, recipe, skeleton, cancel=None, note=None, texture=2048, npc=None, clothes=(), soft=None, bust=0.0, wear=None):
        made.append(wear)
        return b"plain", b"animated", {"clothes": {"worn": [{"kind": c["kind"], "triangles": 5} for c in clothes], "failed": {}}}

    monkeypatch.setattr(bodymaker, "build", build)
    jacket = {"kind": "jacket", "colour": "#634c3e", "ease_mm": 12.0, "length": 1.0, "texture": "weave"}
    bodymaker.refit({}, shaped_npc(lib, underwear=[], clothes_choice="bare"), [jacket])
    bodymaker.refit({}, shaped_npc(lib, underwear=["briefs"], clothes_choice="briefs_only"), [jacket])
    bodymaker.refit({}, shaped_npc(lib), [jacket])
    bodymaker.refit({}, shaped_npc(lib, underwear="bare"), [jacket])                                       # (a record that is not a list is not believed)
    assert made == [[], ["briefs"], None, None]


def test_nothing_a_person_can_reach_adds_a_choice_of_clothes():
    for place in ("web/app.js", "npcgen/service.py", "npcgen/cli.py"):
        assert "add_clothes_choice" not in (config.ROOT / place).read_text("utf-8"), place
    assert "wear" not in bodymaker.REQUEST_FIELDS and "underwear" not in bodymaker.REQUEST_FIELDS
    with pytest.raises(ValueError, match="body_3d.wear: unknown field"):
        bodymaker.clean_request({"base": FEMALE, "wear": []})
