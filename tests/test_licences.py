"""The licence list (data/licences.json, npcgen/licences.py): every model the program can use is on it with a rating the page can show,
and what one character was made with adds up to the worst of the models that made it."""
import re

import pytest

from npcgen import engines, licences, three_d
from npcgen.voice import engines as voice_engines

QWEN_EDIT = "Qwen-Image-Edit 2511 + Lightning"


def made(**assets):
    return {"assets": assets}


def models_of(report):
    return {m["id"]: m for m in report["models"]}


# ---- the list itself --------------------------------------------------------------------------------------------------------------------
def test_every_model_a_picker_offers_is_on_the_list():
    """A model added to a picker without an entry here would show no licence at all: this is the check that stops that."""
    by_selector = licences.load()["by_selector"]
    wanted = [f"image:{key}" for key in engines.IMAGE_ENGINES] + [f"edit:{key}" for key in engines.EDIT_ENGINES]
    wanted += [f"three_d:{row['id']}" for row in three_d.choices()] + [f"voice:{key}" for key in voice_engines.ENGINES]
    assert [selector for selector in wanted if selector not in by_selector] == []


def test_every_upscaler_the_face_step_can_use_is_on_the_list():
    from npcgen import headdetail
    by_selector = licences.load()["by_selector"]
    assert [name for name in headdetail.UPSCALERS if f"upscaler:{name}" not in by_selector] == []
    for name, text in headdetail.LICENCES.items():                        # the face step's own words and the list must agree on who is free
        assert (licences.selector_status(f"upscaler:{name}") == "no") == ("non-commercial" in text.lower()), name


def test_each_entry_is_complete_and_its_links_are_web_addresses():
    data = licences.load()
    kinds = {k["id"] for k in data["kinds"]}
    ids = [m["id"] for m in data["models"]]
    assert len(ids) == len(set(ids))
    selectors = [s for m in data["models"] for s in m.get("selectors", [])]
    assert len(selectors) == len(set(selectors))                         # a selector reaches exactly one model
    assert {s["id"] for s in data["statuses"]} == set(licences.ORDER)
    for m in data["models"]:
        assert m["kind"] in kinds and m["status"] in licences.ORDER and m["role"] in ("creates", "helper"), m["id"]
        for field in ("name", "licence", "summary", "short", "used_for", "checked"):
            assert str(m.get(field, "")).strip(), (m["id"], field)
        if m["id"] != "imported":                                             # (a model made elsewhere has no page of ours to link)
            assert m["links"] and re.match(r"https://\S+$", m["licence_url"]), m["id"]
        assert all(l["label"] and re.match(r"https://\S+$", l["url"]) for l in m["links"]), m["id"]
        assert re.match(r"\d{4}-\d{2}-\d{2}$", m["checked"]), m["id"]
        assert all(use in ids for use in m.get("uses", [])), m["id"]
        assert m["selectors"] or any(m["id"] in other.get("uses", []) for other in data["models"]), m["id"]    # (reachable, or only read by another)


def test_the_cleared_picture_engines_are_the_apache_ones_and_the_others_say_why_not():
    """The owner asked whether Qwen as the default is fine for commercial work: this pins the answer the page shows."""
    assert [licences.selector_status(s) for s in ("image:zimage", "image:qwen2512", "edit:qwen2511")] == ["yes", "yes", "yes"]
    assert licences.selector_status("image:qwen21") == "no" and licences.selector_status("edit:qwen21_edit") == "no"
    assert licences.selector_status("image:mage") == "unclear" and licences.selector_status("edit:mage") == "unclear"
    assert licences.selector_status("voice:breeze") == "no"
    assert licences.selector_status("upscaler:4x-UltraSharp.pth") == "no"


def test_a_model_is_only_as_free_as_the_models_it_uses():
    by_id = {"a": {"status": "yes", "uses": ["b"]}, "b": {"status": "conditions", "uses": ["c"]}, "c": {"status": "no"}, "d": {"status": "yes"}}
    assert licences._effective("a", by_id) == "no" and licences._effective("b", by_id) == "no" and licences._effective("d", by_id) == "yes"
    by_id["c"]["uses"] = ["a"]                                           # a loop in the file must not hang the page
    assert licences._effective("a", by_id) == "no"
    assert licences._effective("missing", by_id) == "unclear"
    assert licences.effective("trellis2") == "conditions" and licences.load()["by_id"]["trellis2"]["status"] == "yes"   # (it reads DINOv3)


def test_an_unknown_model_is_never_called_free():
    assert licences.effective("no-such-model") == "unclear" and licences.selector_status("image:no-such-engine") == "unclear"


def test_the_catalogue_carries_each_models_effective_status_and_the_selectors_that_reach_it():
    catalogue = licences.catalogue()
    row = next(m for m in catalogue["models"] if m["id"] == "trellis2")
    assert row["effective"] == "conditions" and row["status"] == "yes" and "three_d:trellis2" in row["selectors"]
    assert catalogue["by_selector"]["image:zimage"] == "zimage" and catalogue["checked"] and catalogue["disclaimer"]
    assert [s["id"] for s in catalogue["statuses"]] == list(licences.ORDER)


def test_the_voice_enginess_own_flag_agrees_with_the_list():
    """The voice designer used to say 'non-commercial' from its own flag; the list is now the one source, so they must agree."""
    for key, spec in voice_engines.ENGINES.items():
        free = licences.selector_status(f"voice:{key}") in ("yes", "conditions")
        assert bool(spec.get("commercial_ok")) == free, key


# ---- one character ------------------------------------------------------------------------------------------------------------------------
def test_a_character_with_nothing_made_has_nothing_to_rate():
    report = licences.check(made())
    assert report["verdict"] == licences.NOTHING == "none" and report["models"] == [] and report["unknown"] == []
    assert licences.verdict({"profile": {}}) == "none"


def test_a_picture_with_no_engine_recorded_was_drawn_by_mage_flow_and_is_not_cleared():
    assert licences.verdict(made(portrait={"file": "portrait.png"})) == "unclear"
    fell_back = made(portrait={"file": "portrait.png", "engine_note": "Z-Image Turbo could not draw it: out of memory"})
    report = licences.check(fell_back)
    assert report["verdict"] == "unclear" and list(models_of(report)) == ["mage"]        # the note says Mage-Flow drew it instead


def test_pictures_from_the_apache_engines_are_cleared_and_qwen_image_2_1_is_not():
    cleared = made(portrait={"engine": "Z-Image Turbo"}, sprite={"engine": QWEN_EDIT, "mode": "match"},
                   expressions={"happy": {"engine": QWEN_EDIT}}, sheet={"views": {"front": {"engine": QWEN_EDIT}}})
    report = licences.check(cleared)
    assert report["verdict"] == "yes" and set(models_of(report)) == {"zimage", "qwen2511"}
    assert models_of(report)["qwen2511"]["used_for"] == ["full-body sprite", "expressions", "model sheet"]
    drawn_by_21 = made(portrait={"engine": "Qwen Image 2.1"}, sprite={"engine": QWEN_EDIT, "mode": "match"})
    assert licences.check(drawn_by_21)["verdict"] == "no"


def test_a_character_is_rated_by_the_worst_model_that_made_anything_and_the_worst_is_listed_first():
    npc = made(portrait={"engine": "Z-Image Turbo"}, sprite={"file": "sprite.png"})                       # Mage-Flow drew the sprite
    npc["voice"] = {"engine": "breeze"}
    report = licences.check(npc)
    assert report["verdict"] == "no" and report["label"] == "Non-commercial only"
    assert [m["id"] for m in report["models"]] == ["breeze", "mage", "zimage"]
    assert licences.verdict(npc) == "no"


def test_a_sprite_that_is_an_edit_of_the_portrait_is_credited_to_the_edit_engine():
    npc = made(portrait={"engine": "Z-Image Turbo"}, sprite={"engine": QWEN_EDIT, "mode": "match"})
    assert set(models_of(licences.check(npc))) == {"zimage", "qwen2511"}
    built_in = made(sprite={"mode": "match"})
    assert licences.check(built_in)["models"][0]["id"] == "mage"                                           # (Mage-Flow-Edit is Mage-Flow)


def test_a_helper_that_only_measures_counts_only_when_it_is_not_cleared():
    """SAM only finds the hair and SDPose the joints: nothing they make is part of the character, so conditions on them do not downgrade it."""
    npc = made(portrait={"engine": "Z-Image Turbo"}, model_3d={"designed": True, "refined": {"hair": {"mask": "x"}, "hands": {"l": {}}}},
               rig_3d={"pose_model": "sdpose"})
    report = licences.check(npc)
    got = models_of(report)
    assert got["sam31"]["role"] == "helper" and got["sam31"]["effective"] == "conditions" and "sdpose" in got
    assert report["verdict"] == "yes"
    broken = made(portrait={"engine": "Z-Image Turbo"}, model_3d={"designed": True, "refined": {"hair": {"error": "no"}}})
    assert "sam31" not in models_of(licences.check(broken))                                                # a step that failed made nothing


def test_the_models_behind_a_3d_model_are_found_from_what_it_records():
    trellis = made(model_3d={"engine_id": "trellis2", "engine": "Trellis2 four pictures (shape only)"})
    assert licences.check(trellis)["verdict"] == "conditions"                                              # Trellis2 reads DINOv3 features
    by_label = made(model_3d={"engine": "Hunyuan3D v2.1"})
    assert models_of(licences.check(by_label)) and licences.check(by_label)["models"][0]["id"] == "hunyuan21"
    imported = made(model_3d={"imported": True})
    assert licences.check(imported)["models"][0]["id"] == "imported" and licences.check(imported)["verdict"] == "unclear"
    designed = made(model_3d={"designed": True})
    assert licences.check(designed)["verdict"] == "yes"
    nameless = made(model_3d={"file": "model_3d.glb"})
    report = licences.check(nameless)
    assert report["verdict"] == "unclear" and report["unknown"] == ["The generator that made the 3D model was not recorded."]
    assert licences.check(made(head_3d={"file": "head_3d.glb"}))["models"][0]["id"] == "pixal3d"


def test_a_sharper_face_names_the_upscaler_and_the_non_commercial_one_downgrades_the_character():
    def with_upscaler(name):
        return made(model_3d={"designed": True, "refined": {"face": {"upscaler": name}}})
    assert licences.check(with_upscaler("RealESRGAN_x4plus.pth"))["verdict"] == "yes"
    report = licences.check(with_upscaler("4x-UltraSharp.pth"))
    assert report["verdict"] == "no" and "ultrasharp" in models_of(report)
    assert licences.check(with_upscaler("a-file-nobody-listed.pth"))["verdict"] == "unclear"


def test_animation_frames_name_their_engines_and_an_unrecorded_one_keeps_the_character_unlisted_as_free():
    recorded = made(animation_views={"east": {"engines": [QWEN_EDIT]}},
                    animations={"walk": {"directions": {"east": {"engines": [QWEN_EDIT]}}}, "wave": {"kind": "procedural"}})
    assert licences.check(recorded)["verdict"] == "yes"
    old = made(animations={"walk": {"directions": {"east": {}}}})
    report = licences.check(old)
    assert report["verdict"] == "unclear" and "not recorded" in report["unknown"][0]
    stranger = made(animations={"walk": {"directions": {"east": {"engines": ["Some Future Model"]}}}})
    report = licences.check(stranger)
    assert report["verdict"] == "unclear" and "Some Future Model" in report["unknown"][0]


def test_the_voice_and_every_spoken_line_are_rated_by_their_engines():
    npc = {"assets": {}, "voice": {"engine": "qwen", "reference": {"engine": "kokoro"}},
           "voice_lines": {"a": {"engine": "chatterbox"}, "b": {"engine": "breeze"}, "c": {}}}
    report = licences.check(npc)
    assert set(models_of(report)) == {"qwen3tts", "kokoro", "chatterbox", "breeze"} and report["verdict"] == "no"
    assert models_of(report)["breeze"]["used_for"] == ["spoken lines"]
    assert licences.verdict({"assets": {}, "voice": {"engine": "qwen"}}) == "yes"


def test_an_engine_the_list_does_not_know_is_said_and_keeps_the_character_from_being_called_free():
    report = licences.check(made(portrait={"engine": "Brand New Model"}))
    assert report["verdict"] == "unclear" and report["models"] == [] and "Brand New Model" in report["unknown"][0]
    report = licences.check({"assets": {}, "voice": {"engine": "made-up"}})
    assert report["verdict"] == "unclear" and report["unknown"] == ["voice:made-up is not in the licence list."]


def test_the_report_lists_each_model_once_with_everything_it_made():
    npc = made(portrait={"engine": "Z-Image Turbo"}, sprite={"engine": "Z-Image Turbo"}, expressions={"happy": {"engine": "Z-Image Turbo"}})
    report = licences.check(npc)
    assert [(m["id"], m["used_for"]) for m in report["models"]] == [("zimage", ["portrait", "full-body sprite", "expressions"])]
    assert report["models"][0]["licence"] == "Apache-2.0"                  # (the page shows the licence's name under each model)
    assert report["checked"] == licences.load()["checked"]


@pytest.mark.parametrize("status", licences.ORDER)
def test_the_worst_of_some_statuses_follows_the_order_and_nothing_at_all_is_yes(status):
    assert licences.worst([status]) == status and licences.worst([]) == "yes"
    assert licences.worst(["yes", status]) == status
    assert licences.worst([status, "no"]) == "no"
