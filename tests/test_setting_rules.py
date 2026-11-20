"""A setting file may state rules of its own: allowed genders, a youngest age, its own age groups and build table, a negative
prompt and a preferred art style. Nothing here needs a GPU."""
import json

import pytest

from npcgen import prompts, roll

STRICT_GROUPS = {"young_adult": {"label": "Young adult (21-29)", "min": 21, "max": 29, "weight": 3},
                 "adult": {"label": "Adult (30-49)", "min": 30, "max": 49, "weight": 2}}


def write_setting(folder, name="strict", **rules):
    base = json.loads((roll.DATA_DIR / "settings" / "modern.json").read_text("utf-8"))
    base.update({"label": "Strict", "short": "Strict", "order": 9, **rules})
    (folder / f"{name}.json").write_text(json.dumps(base), "utf-8")


@pytest.fixture
def strict(tmp_path):
    write_setting(tmp_path, genders=["female"], min_age=21, age_groups=STRICT_GROUPS, negative="childlike, youthful",
                  default_style="photoreal", builds=[{"label": "athletic", "w": 1}])
    roll.add_settings_dir(tmp_path)
    yield roll.load_data()
    roll.EXTRA_SETTINGS.clear()


def test_a_setting_that_allows_only_some_genders_and_ages_rolls_only_those(strict):
    for seed in range(300):
        npc = roll.roll_npc(strict, "strict", seed=seed)
        p = npc["profile"]
        assert p["gender"] == "female" and 21 <= p["age"] <= 49 and p["age_group"] in STRICT_GROUPS, seed
        assert npc["appearance"]["build"] == "athletic"                                        # its own build table
        roll.normalize(strict, {**npc, "style": "photoreal"})


def test_choices_the_setting_does_not_have_are_ignored_when_rolling(strict):
    for seed in range(50):
        p = roll.roll_npc(strict, "strict", {"gender": "male", "age_group": "teen"}, seed=seed)["profile"]
        assert p["gender"] == "female" and p["age"] >= 21


def test_a_locked_value_the_setting_does_not_allow_is_an_error_not_a_quiet_swap(strict):
    with pytest.raises(ValueError, match="only has female"):
        roll.roll_npc(strict, "strict", locked={"profile.gender": "male"}, seed=1)
    with pytest.raises(ValueError, match="21 or older"):
        roll.roll_npc(strict, "strict", locked={"profile.age": 17}, seed=1)
    assert roll.roll_npc(strict, "strict", locked={"profile.age": 21, "profile.gender": "female"}, seed=1)["profile"]["age"] == 21


def test_a_described_character_follows_the_same_limits(strict):
    with pytest.raises(ValueError, match="21 or older"):
        roll.roll_npc(strict, "strict", locked={"profile.age": 15}, freeform="a tall woman", seed=1)
    assert roll.roll_npc(strict, "strict", freeform="a tall woman", seed=1)["profile"]["age"] >= 21


def test_normalize_enforces_the_limits_on_what_the_page_sends(strict):
    npc = roll.roll_npc(strict, "strict", seed=5)
    npc["style"] = "photoreal"
    for change, message in (({"gender": "male"}, "only has female"), ({"gender": "nonbinary"}, "only has female"),
                            ({"age": 20}, "21 or older"), ({"age": 1}, "21 or older"), ({"age": "-5"}, "21 or older")):
        bad = {**npc, "profile": {**npc["profile"], **change}}
        with pytest.raises(ValueError, match=message):
            roll.normalize(strict, bad)
    assert roll.normalize(strict, {**npc, "profile": {**npc["profile"], "age": 21}})["profile"]["age"] == 21


def test_a_minimum_age_alone_removes_the_younger_shared_age_groups(tmp_path):
    write_setting(tmp_path, min_age=21)
    roll.add_settings_dir(tmp_path)
    try:
        data = roll.load_data()
        for seed in range(200):
            age = roll.roll_npc(data, "strict", {"age_group": "teen" if seed % 2 else "young_adult"}, seed=seed)["profile"]["age"]
            assert age >= 21
    finally:
        roll.EXTRA_SETTINGS.clear()


def test_settings_without_rules_are_unchanged(strict):
    kids = {roll.roll_npc(strict, "modern", {"age_group": "child"}, seed=s)["profile"]["age"] for s in range(10)}
    assert kids and max(kids) <= 12
    genders = {roll.roll_npc(strict, "modern", seed=s)["profile"]["gender"] for s in range(60)}
    assert {"female", "male"} <= genders


def test_a_setting_negative_is_added_to_the_text_to_image_prompts(strict):
    npc = roll.roll_npc(strict, "strict", seed=2)
    npc["style"] = "photoreal"
    style, setting = strict["styles"]["photoreal"], strict["settings"]["strict"]
    for made in (prompts.portrait(npc, style, setting), prompts.sprite(npc, style, setting)):
        assert made["negative"].startswith(style["negative"]) and "childlike, youthful" in made["negative"]
    assert prompts.sprite(npc, style, setting)["negative"].endswith("cut-off feet, ground shadow")
    plain = prompts.portrait(npc, style, strict["settings"]["modern"])
    assert plain["negative"] == style["negative"]                                                # no setting negative: exactly as before


def test_hidden_settings_are_left_out_and_reported(tmp_path):
    write_setting(tmp_path)
    switch = {"on": False}
    roll.add_settings_dir(tmp_path, visible=lambda: switch["on"])
    try:
        assert "strict" not in roll.load_data()["settings"] and roll.hidden_setting_ids() == {"strict"}
        switch["on"] = True
        assert "strict" in roll.load_data()["settings"] and roll.hidden_setting_ids() == set()
    finally:
        roll.EXTRA_SETTINGS.clear()
