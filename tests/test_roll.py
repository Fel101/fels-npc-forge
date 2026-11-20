import pytest

from npcgen import roll


@pytest.fixture(scope="module")
def data():
    return roll.load_data()


def test_same_seed_gives_the_same_npc(data):
    assert roll.roll_npc(data, "modern", seed=11) == roll.roll_npc(data, "modern", seed=11)
    assert roll.roll_npc(data, "modern", seed=11) != roll.roll_npc(data, "modern", seed=12)


@pytest.mark.parametrize("setting", ["modern", "fantasy", "scifi", "western"])
def test_every_setting_rolls_a_complete_npc(data, setting):
    for seed in range(20):
        npc = roll.roll_npc(data, setting, seed=seed)
        p, a = npc["profile"], npc["appearance"]
        assert p["first_name"] and p["last_name"] and p["occupation"] and p["bio"]
        assert a["hair"] and a["outfit"] and a["skin_tone"]
        assert not npc["freeform"]
        roll.normalize(data, {**npc, "style": "pixel"})          # what we roll must always be accepted back


def test_constraints_are_respected(data):
    npc = roll.roll_npc(data, "modern", {"gender": "female", "age_group": "senior", "occupation": "nurse"}, seed=3)
    assert npc["profile"]["gender"] == "female"
    assert npc["profile"]["age"] >= 65 and npc["profile"]["age_group"] == "senior"
    assert npc["profile"]["occupation"] == "nurse"


def test_locked_fields_win_and_an_empty_lock_on_an_optional_field_sticks(data):
    lock = {"profile.first_name": "Maya", "profile.age": 40, "appearance.hair": "bright teal bob",
            "appearance.glasses": "", "appearance.accessory": ""}
    for seed in range(30):
        npc = roll.roll_npc(data, "modern", locked=lock, seed=seed)
        assert npc["profile"]["first_name"] == "Maya" and npc["profile"]["age"] == 40
        assert npc["appearance"]["hair"] == "bright teal bob"
        assert npc["appearance"]["glasses"] == "" and npc["appearance"]["accessory"] == ""


def test_children_and_teens_only_appear_when_asked_for(data):
    groups = {roll.roll_npc(data, "modern", seed=s)["profile"]["age_group"] for s in range(300)}
    assert groups <= {"young_adult", "adult", "middle_aged", "senior"}
    assert roll.roll_npc(data, "modern", {"age_group": "child"}, seed=1)["profile"]["age"] <= 12


def test_nonbinary_bios_use_they(data):
    for seed in range(40):
        bio = " " + roll.roll_npc(data, "modern", {"gender": "nonbinary"}, seed=seed)["profile"]["bio"].lower() + " "
        assert " she " not in bio and " he " not in bio and " her " not in bio and " his " not in bio


def test_freeform_only_fills_identity_and_keeps_the_description(data):
    npc = roll.roll_npc(data, "modern", freeform="a weathered lighthouse keeper with a wool sweater", seed=5)
    assert npc["freeform"] is True
    assert npc["appearance"]["extra"] == "a weathered lighthouse keeper with a wool sweater"
    assert all(not npc["appearance"][k] for k in roll.APPEARANCE_TEXT if k != "extra")
    assert npc["profile"]["first_name"] and npc["profile"]["occupation"] == "" and npc["profile"]["bio"] == ""


def test_user_supplied_heritage_and_race_text_is_kept_even_when_not_in_the_tables(data):
    assert roll.roll_npc(data, "modern", locked={"profile.heritage": "Filipino"}, seed=1)["profile"]["heritage"] == "Filipino"
    fantasy = roll.roll_npc(data, "fantasy", locked={"profile.race": "dragonkin"}, seed=1)
    assert fantasy["profile"]["race"] == "dragonkin"


def test_normalize_rejects_bad_drafts(data):
    good = {**roll.roll_npc(data, "modern", seed=1), "style": "pixel"}
    assert roll.normalize(data, good)["profile"]["age_group"]
    for mutate, message in [
        (lambda n: n["profile"].update(first_name=" "), "first name"),
        (lambda n: n["profile"].update(gender="robot"), "Gender"),
        (lambda n: n["profile"].update(age="old"), "Age"),
        (lambda n: n.update(style="nope"), "style"),
        (lambda n: n.update(setting="nope"), "setting"),
    ]:
        bad = {**good, "profile": dict(good["profile"])}
        mutate(bad)
        with pytest.raises(ValueError, match=message):
            roll.normalize(data, bad)


def test_normalize_carries_custom_data_but_drops_unserialisable_values(data):
    good = {**roll.roll_npc(data, "modern", seed=1), "style": "pixel"}
    assert roll.normalize(data, {**good, "custom": {"schedule": ["07:00 open"]}})["custom"] == {"schedule": ["07:00 open"]}
    assert roll.normalize(data, {**good, "custom": {"bad": object()}})["custom"] == {}


def test_the_old_west_uses_its_own_period_tables(data):
    """Shared tables hold undercuts and pastel dye; a setting can replace them, and the Old West must not roll them."""
    west = data["settings"]["western"]
    first = set(west["names"]["female"] + west["names"]["male"] + west["names"]["neutral"])
    modern_only = ("undercut", "pastel", "bright blue", "lavender", "teal", "mint", "platinum", "vivid", "man bun", "pixie", "buzz", "locs", "afro")
    glasses = {"", *[g["label"] for g in west["glasses"]]}
    for seed in range(300):
        npc = roll.roll_npc(data, "western", seed=seed)
        p, a = npc["profile"], npc["appearance"]
        assert p["first_name"] in first and p["last_name"] in west["names"]["surnames"]
        assert not any(word in a["hair"] for word in modern_only), a["hair"]
        assert a["glasses"] in glasses
    assert roll.roll_npc(data, "modern", seed=1)["appearance"]["hair"] == roll.roll_npc(data, "modern", seed=1)["appearance"]["hair"]


def test_every_old_west_job_has_an_outfit_for_everyone_and_children_get_children_s_jobs(data):
    west = data["settings"]["western"]
    for occ in west["occupations"]:
        for gender in data["common"]["genders"]:
            assert roll._outfits_for(occ, west, gender), (occ["id"], gender)
    assert roll.roll_npc(data, "western", {"age_group": "child"}, seed=1)["profile"]["occupation"] == "town child"
    assert roll.roll_npc(data, "western", {"age_group": "teen"}, seed=1)["profile"]["occupation"] == "young ranch hand"


@pytest.mark.parametrize("setting", ["modern", "fantasy", "scifi", "western", "postapoc", "cyberpunk",
                                      "horror", "noir", "superhero", "guild", "pirates", "nordic", "zombies", "anthro",
                                      "witches", "astronauts", "steampunk", "dystopia", "aliens", "mythical"])
def test_every_job_in_every_setting_has_an_outfit_for_every_gender_and_a_unique_id(data, setting):
    occs = data["settings"][setting]["occupations"]
    ids = [o["id"] for o in occs]
    assert len(ids) == len(set(ids)), setting
    for occ in occs:
        for gender in data["common"]["genders"]:
            assert roll._outfits_for(occ, data["settings"][setting], gender), (setting, occ["id"], gender)


@pytest.mark.parametrize("setting", ["postapoc", "cyberpunk", "horror", "noir", "superhero", "guild", "pirates", "nordic",
                                      "zombies", "anthro", "witches", "astronauts", "steampunk", "dystopia", "aliens", "mythical"])
def test_the_newer_settings_roll_a_complete_npc_across_many_seeds(data, setting):
    for seed in range(200):
        npc = roll.roll_npc(data, setting, seed=seed)
        p, a = npc["profile"], npc["appearance"]
        assert p["first_name"] and p["last_name"] and p["occupation"] and p["bio"]
        assert a["hair"] and a["outfit"] and a["skin_tone"]
        roll.normalize(data, {**npc, "style": "pixel"})


def test_the_zombie_setting_never_rolls_a_child_or_teen(data):
    """min_age is the setting's own content rule (no child/teen zombies), enforced the same generic way Models enforces
    its own age floor: nobody below it is ever rolled, constrained for, or accepted back through normalize."""
    for seed in range(300):
        assert roll.roll_npc(data, "zombies", seed=seed)["profile"]["age"] >= 18
    for age_group in ("child", "teen"):
        npc = roll.roll_npc(data, "zombies", {"age_group": age_group}, seed=1)
        assert npc["profile"]["age_group"] not in ("child", "teen")
    young = {**roll.roll_npc(data, "zombies", seed=1), "style": "pixel"}
    young["profile"] = {**young["profile"], "age": 12}
    with pytest.raises(ValueError, match="18"):
        roll.normalize(data, young)


# ---- how they talk and how they come across ---------------------------------------------------------------------------
@pytest.mark.parametrize("setting", ["modern", "fantasy", "scifi", "western"])
def test_every_setting_rolls_a_speech_style_and_a_demeanor(data, setting):
    for seed in range(20):
        p = roll.roll_npc(data, setting, seed=seed)["profile"]
        assert p["speech"].strip() and p["demeanor"].strip()


def test_manner_follows_the_seed_and_leaves_the_rest_of_the_roll_alone(data):
    a, b = roll.roll_npc(data, "modern", seed=5), roll.roll_npc(data, "modern", seed=5)
    assert a["profile"]["speech"] == b["profile"]["speech"] and a["profile"]["demeanor"] == b["profile"]["demeanor"]
    # the NPC seed is the last draw of the roll, so the manner (drawn from it afterwards) cannot change anything before it
    plain = {k: v for k, v in a["profile"].items() if k not in ("speech", "demeanor")}
    assert plain["personality"] and a["seed"] == roll.roll_npc(data, "modern", seed=5)["seed"]


def test_manner_suits_the_traits_and_never_clashes_with_them(data):
    traits = ["shy", "polite", "anxious"]
    clash = {e["t"] for table in ("speech_styles", "demeanors") for e in data["common"][table]
             if set(e.get("not") or []) & set(traits)}
    suit = {e["t"] for table in ("speech_styles", "demeanors") for e in data["common"][table] if set(e["for"]) & set(traits)}
    import random
    picks = [roll.roll_manner(data, "western", traits, random.Random(i)) for i in range(300)]
    assert clash and suit
    assert not any(m["speech"] in clash or m["demeanor"] in clash for m in picks)
    assert sum(m["speech"] in suit for m in picks) > 0.9 * len(picks)
    assert sum(m["demeanor"] in suit for m in picks) > 0.9 * len(picks)


def test_a_setting_can_replace_the_manner_tables_and_a_missing_table_gives_nothing(data):
    import copy
    own = copy.deepcopy(data)
    own["settings"]["modern"]["speech_styles"] = [{"t": "only this way", "for": []}]
    assert roll.roll_manner(own, "modern", ["calm"])["speech"] == "only this way"
    assert roll.roll_manner(own, "western", ["calm"])["speech"] != "only this way"
    none = copy.deepcopy(data)
    none["common"].pop("demeanors")
    assert roll.roll_manner(none, "modern", ["calm"])["demeanor"] == ""


def test_locked_manner_wins_and_a_freeform_character_starts_without_one(data):
    lock = {"profile.speech": "sings every word", "profile.demeanor": "never sits down"}
    for seed in range(10):
        p = roll.roll_npc(data, "modern", locked=lock, seed=seed)["profile"]
        assert p["speech"] == "sings every word" and p["demeanor"] == "never sits down"
    free = roll.roll_npc(data, "modern", freeform="a teapot-headed robot chef", seed=1)["profile"]
    assert free["speech"] == "" and free["demeanor"] == ""


def test_normalize_keeps_the_manner_and_limits_its_length(data):
    npc = roll.roll_npc(data, "modern", seed=2)
    npc["profile"]["speech"], npc["profile"]["demeanor"] = "  talks in riddles ", "x" * 5000
    clean = roll.normalize(data, {**npc, "style": "pixel"})["profile"]
    assert clean["speech"] == "talks in riddles" and len(clean["demeanor"]) == 800


def test_a_rolled_character_never_keeps_a_raw_pronoun_placeholder(data):
    """The tables write '{poss} own jokes'; the stored fields (not only the bio) carry the real pronoun. Appearance
    fields (accessory, distinguishing...) are never run through the formatter at all, so a placeholder there is a
    table bug, not just a missed substitution: check every appearance value too, not only the profile ones."""
    seen = set()
    for setting in data["settings"]:
        for seed in range(60):
            npc = roll.roll_npc(data, setting, seed=seed)
            p, a = npc["profile"], npc["appearance"]
            for key in ("quirk", "goal", "peeve", "bio", "speech", "demeanor"):
                assert "{" not in p[key], (setting, seed, key, p[key])
            assert all("{" not in h for h in p["hobbies"]), (setting, seed, p["hobbies"])
            for key, value in a.items():
                assert "{" not in str(value), (setting, seed, "appearance." + key, value)
            seen.add(p["gender"])
    assert {"female", "male"} <= seen


def test_a_pronoun_in_a_table_entry_matches_the_gender(data):
    import copy
    own = copy.deepcopy(data)
    own["settings"]["modern"]["goals"] = ["opening a place of {poss} own"]
    own["settings"]["modern"]["quirks"] = ["laughing at {poss} own jokes"]
    for gender, word in (("female", "her"), ("male", "his"), ("nonbinary", "their")):
        p = roll.roll_npc(own, "modern", locked={"profile.gender": gender}, seed=4)["profile"]
        assert p["goal"] == f"opening a place of {word} own" and p["quirk"] == f"laughing at {word} own jokes"
