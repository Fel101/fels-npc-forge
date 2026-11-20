import json
import pathlib

import pytest

from npcgen import cast, roll


@pytest.fixture(scope="module")
def data():
    return roll.load_data()


def test_parse_json_list_dict_and_csv():
    assert len(cast.parse_cast('[{"name": "A"}, {"name": "B"}]')) == 2
    assert len(cast.parse_cast('{"characters": [{"name": "A"}]}')) == 1
    assert cast.parse_cast('{"name": "Solo"}') == [{"name": "Solo"}]
    rows = cast.parse_cast("name,age,job\nMaya Okafor,34,barista\nJon,50,plumber")
    assert rows[0]["job"] == "barista" and len(rows) == 2


@pytest.mark.parametrize("text,fmt,message", [
    ("", None, "empty"), ("[1, 2]", None, "list of objects"), ("[{bad json", None, "not valid JSON"),
    ("name\n", None, "no characters"), ("x", "xml", "Unknown cast format"),
])
def test_parse_errors_are_readable(text, fmt, message):
    with pytest.raises(ValueError, match=message):
        cast.parse_cast(text, fmt)


def test_parse_limits_the_size():
    with pytest.raises(ValueError, match="up to"):
        cast.parse_cast(json.dumps([{"name": str(i)} for i in range(cast.MAX_CAST + 1)]))


def test_entry_fields_understands_aliases_names_and_types():
    locked, control, custom, warnings = cast.entry_fields({
        "Full Name": "Mary Jane Watson", "Sex": "F", "age": "34 years", "Career": "actress", "traits": "kind; brave | funny",
        "clothing": "a red coat", "description": "freckles", "seed": "42", "schedule": ["07:00 wake"], "id": 7})
    assert locked["profile.first_name"] == "Mary Jane" and locked["profile.last_name"] == "Watson"
    assert locked["profile.gender"] == "female" and locked["profile.age"] == 34
    assert locked["profile.occupation"] == "actress" and locked["appearance.outfit"] == "a red coat"
    assert locked["profile.personality"] == ["kind", "brave", "funny"]
    assert locked["appearance.extra"] == "freckles" and control == {"seed": "42"}
    assert custom == {"schedule": ["07:00 wake"], "id": 7} and warnings == []


def test_entry_fields_warns_and_falls_back_for_unusable_values():
    locked, _, _, warnings = cast.entry_fields({"name": "X", "gender": "banana", "age": "old"})
    assert "profile.gender" not in locked and "profile.age" not in locked
    assert len(warnings) == 2


def test_description_only_entries_become_freeform_and_looks_make_them_structured(data):
    drafts, _ = cast.build_drafts(data, [
        {"name": "Ada Vale", "description": "an elderly orc blacksmith with a bone crown"},
        {"name": "Bo Chen", "description": "loves tea", "hair": "short black hair", "outfit": "a green coat"},
    ], "modern", "pixel")
    freeform, structured = drafts
    assert freeform["freeform"] and freeform["appearance"]["extra"].startswith("an elderly orc")
    assert not structured["freeform"] and structured["appearance"]["extra"] == "loves tea"
    assert structured["appearance"]["hair"] == "short black hair" and structured["style"] == "pixel"
    for d in drafts:
        roll.normalize(data, d)


def test_per_entry_overrides_custom_data_and_warnings(data):
    drafts, warnings = cast.build_drafts(data, [
        {"name": "Kel", "setting": "fantasy", "style": "cartoon", "seed": 99, "quest": "find the ring"},
        {"name": "Nia", "setting": "atlantis", "style": "oil"},
    ], "modern", "pixel")
    assert drafts[0]["setting"] == "fantasy" and drafts[0]["style"] == "cartoon" and drafts[0]["seed"] == 99
    assert drafts[0]["custom"] == {"quest": "find the ring"}
    assert drafts[1]["setting"] == "modern" and drafts[1]["style"] == "pixel"
    assert len(warnings) == 2


def test_freeform_mode_without_a_description_is_rolled_normally(data):
    drafts, warnings = cast.build_drafts(data, [{"name": "Zed", "mode": "freeform"}], "modern", "pixel")
    assert not drafts[0]["freeform"] and warnings


def test_the_bundled_old_west_example_imports_cleanly(data):
    path = pathlib.Path(__file__).resolve().parent.parent / "examples" / "frontier_town_cast.json"
    drafts, warnings = cast.build_drafts(data, cast.parse_cast(path.read_text("utf-8")), "western", "photoreal")
    assert len(drafts) == 5 and not warnings
    assert drafts[0]["custom"] == {"id": "npc_saloon_01", "shift": "noon-midnight"} and drafts[0]["profile"]["occupation"] == "saloon keeper"
    assert drafts[-1]["freeform"] and "threadbare gray coat" in drafts[-1]["appearance"]["extra"]      # the description-only entry
    for d in drafts:
        assert d["setting"] == "western" and roll.normalize(data, d)["style"] == "photoreal"


def test_none_rules_out_a_random_extra_but_an_empty_cell_still_means_roll_it(data):
    """A roster that says 'no accessory' must not get a random hunting knife (it did, on a shopkeeper)."""
    entries = [{"name": f"Test {i}", "job": "storekeeper", "gender": "f", "accessory": "None", "beard": "no", "glasses": "n/a", "features": "-"}
               for i in range(40)]
    drafts, _ = cast.build_drafts(data, entries, "western", "photoreal")
    assert all(d["appearance"]["accessory"] == "" and d["appearance"]["facial_hair"] == "" and d["appearance"]["glasses"] == ""
               and d["appearance"]["distinguishing"] == "" for d in drafts)
    blank = [{"name": f"Test {i}", "job": "storekeeper", "gender": "f", "accessory": ""} for i in range(60)]
    assert any(d["appearance"]["accessory"] for d in cast.build_drafts(data, blank, "western", "photoreal")[0])


def test_speech_and_demeanor_columns_are_read_and_kept(data):
    rows = cast.parse_cast("name,speech,manner\nMaya Okafor,talks in rhymes,wary of strangers")
    drafts, _ = cast.build_drafts(data, rows, "modern", "pixel")
    p = drafts[0]["profile"]
    assert p["speech"] == "talks in rhymes" and p["demeanor"] == "wary of strangers"
