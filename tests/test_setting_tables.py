"""A setting file can offer a hair colour choice, carry its own heritage list and default expression, and word the portrait
prompt for an art style its own way. Nothing here needs a GPU."""
import json

import pytest

from npcgen import prompts, roll


def write_setting(folder, name="strict", **rules):
    base = json.loads((roll.DATA_DIR / "settings" / "modern.json").read_text("utf-8"))
    base.update({"label": name.title(), "short": name.title(), "order": 9, **rules})
    (folder / f"{name}.json").write_text(json.dumps(base), "utf-8")


@pytest.fixture
def data_with(tmp_path):
    """load_data() with the settings written by the test added; the extra folder is forgotten afterwards."""
    def make(**settings):
        for name, rules in settings.items():
            write_setting(tmp_path, name, **rules)
        roll.add_settings_dir(tmp_path)
        return roll.load_data()
    yield make
    roll.EXTRA_SETTINGS.clear()


def heritage(label, names, surnames, hair):
    return {"label": label, "weight": 1, "skin": {"fair": 1}, "hair_colors": hair, "hair_textures": {"straight": 1},
            "eyes": {"dark brown": 1}, "names": {"female": names}, "surnames": surnames}


@pytest.fixture(scope="module")
def data():
    return roll.load_data()


def test_a_hair_colour_choice_is_used_and_changes_nothing_else_in_the_roll():
    data = roll.load_data()
    for seed in range(60):
        plain = roll.roll_npc(data, "modern", seed=seed)
        blonde = roll.roll_npc(data, "modern", {"hair_color": "blonde"}, seed=seed)
        assert "blonde" in blonde["appearance"]["hair"] or blonde["appearance"]["hair"] == "bald", seed
        assert {**blonde["appearance"], "hair": ""} == {**plain["appearance"], "hair": ""}, seed         # same seed, same everything else
        assert blonde["profile"] == plain["profile"]


def test_the_hair_colour_wins_over_greying_and_dyes():
    data = roll.load_data()
    for seed in range(150):
        hair = roll.roll_npc(data, "modern", {"hair_color": "black", "age_group": "senior"}, seed=seed)["appearance"]["hair"]
        assert hair == "bald" or hair.split()[0] == "black", (seed, hair)


def test_an_unknown_hair_colour_is_ignored_and_case_does_not_matter():
    data = roll.load_data()
    plain = roll.roll_npc(data, "modern", seed=4)
    assert roll.roll_npc(data, "modern", {"hair_color": "plaid"}, seed=4) == plain
    hair = roll.roll_npc(data, "modern", {"hair_color": " BLONDE "}, seed=4)["appearance"]["hair"]
    assert "blonde" in hair or hair == "bald"


def test_the_hair_colours_offered_come_from_the_setting_or_from_its_heritage_tables(data_with):
    data = data_with(listed={"hair_color_choices": ["platinum blonde", "black"]},
                     own={"heritages": {"only": heritage("Only", ["Ann"], ["Roe"], {"teal": 2, "pink": 1})}})
    assert roll.hair_color_choices(data, "listed") == ["platinum blonde", "black"]
    assert roll.hair_color_choices(data, "own") == ["teal", "pink"]                                       # most likely first
    assert {"blonde", "black"} <= set(roll.hair_color_choices(data, "modern"))                            # every colour the tables can roll
    hair = roll.roll_npc(data, "listed", {"hair_color": "platinum blonde"}, seed=1)["appearance"]["hair"]
    assert hair == "bald" or hair.startswith("platinum blonde")


def test_a_setting_can_have_its_own_heritage_list_with_its_own_names(data_with):
    data = data_with(asia={"genders": ["female"], "heritages": {
        "japanese": heritage("Japanese", ["Yui", "Hana"], ["Sato", "Tanaka"], {"black": 8, "chestnut brown": 1}),
        "korean": heritage("Korean", ["Ji-woo", "Min-seo"], ["Kim", "Park"], {"black": 7, "dark brown": 4})}})
    assert list(roll.setting_common(data, "asia")["heritages"]) == ["japanese", "korean"]
    assert "european" in data["common"]["heritages"] and "european" in roll.setting_common(data, "modern")["heritages"]   # shared list untouched
    for seed in range(40):
        for origin, first, last in (("japanese", {"Yui", "Hana"}, {"Sato", "Tanaka"}), ("korean", {"Ji-woo", "Min-seo"}, {"Kim", "Park"})):
            p = roll.roll_npc(data, "asia", {"origin": origin}, seed=seed)["profile"]
            assert p["heritage"] == origin.title() and p["first_name"] in first and p["last_name"] in last, (origin, seed)
    assert {roll.roll_npc(data, "asia", seed=s)["profile"]["heritage"] for s in range(30)} == {"Japanese", "Korean"}


def test_a_setting_can_word_the_portrait_style_its_own_way():
    data = roll.load_data()
    style, setting = data["styles"]["photoreal"], data["settings"]["modern"]
    npc = {**roll.roll_npc(data, "modern", seed=2), "style": "photoreal"}
    assert prompts.portrait(npc, style, setting)["positive"].startswith(style["style"] + ".")
    worded = {**setting, "style_text": {"photoreal": "high-end fashion beauty photograph"}}
    assert prompts.portrait(npc, style, worded)["positive"].startswith("high-end fashion beauty photograph. Head and shoulders portrait")
    other = {**npc, "style": "cartoon"}                                                                    # worded for photoreal only
    assert prompts.portrait(other, data["styles"]["cartoon"], worded)["positive"].startswith(data["styles"]["cartoon"]["style"] + ".")


def test_a_setting_can_replace_the_default_expression(data_with):
    data = data_with(smile={"default_expression": "a soft, confident half-smile"})
    seen = {roll.roll_npc(data, "smile", seed=s)["appearance"]["expression"] for s in range(40)}
    assert "a soft, confident half-smile" in seen and "a calm, pleasant, neutral expression" not in seen


def test_a_setting_can_change_how_often_hair_is_dyed(data_with):
    """Default is rare (about 6% under 40); a setting like Cyberpunk can turn it way up or off entirely."""
    data = data_with(neon={"dye_chance": 1.0, "dyed_hair_colors": ["glow green", "signal red"]},
                     plain={"dye_chance": 0.0})
    dyed = ["glow green", "signal red"]
    young = {"age_group": "young_adult"}
    always = [roll.roll_npc(data, "neon", young, seed=s)["appearance"]["hair"] for s in range(40)]
    never = [roll.roll_npc(data, "plain", young, seed=s)["appearance"]["hair"] for s in range(40)]
    assert all(h == "bald" or any(c in h for c in dyed) for h in always)
    assert any(any(c in h for c in dyed) for h in always)                 # the table is actually reachable
    assert not any(any(c in h for c in dyed) for h in never)


def test_built_in_settings_always_sort_before_an_extensions_even_with_a_lower_order_number(data_with):
    """An extension's own setting file could pick any 'order' it likes; it must never crowd in among the built-in four
    (now twenty) just because the number happens to be lower or to tie with one of them."""
    data = data_with(early={"order": 0})
    ids = list(data["settings"])
    built_in = {"modern", "fantasy", "scifi", "western", "postapoc", "cyberpunk", "horror", "noir", "superhero", "guild",
                "pirates", "nordic", "zombies", "anthro", "witches", "astronauts", "steampunk", "dystopia", "aliens", "mythical"}
    assert set(ids) >= built_in | {"early"}
    assert ids.index("early") > max(ids.index(b) for b in built_in)


def test_a_setting_can_replace_the_bio_templates(data_with):
    """The zombie setting needs past-tense bios ('was', not 'is'); bio_templates is a normal TABLE_OVERRIDES entry like
    any other, so a missing one falls back to the shared common.json templates."""
    data = data_with(elegy={"bio_templates": ["{first} was {occ_a}, {t1} to the end."]})
    for seed in range(30):
        assert roll.roll_npc(data, "elegy", seed=seed)["profile"]["bio"].endswith("to the end.")
    assert not roll.roll_npc(data, "modern", seed=1)["profile"]["bio"].endswith("to the end.")


def test_a_race_can_roll_its_own_hair_colours_instead_of_its_heritages(data):
    """Fantasy's elf race states its own hair_colors (silvers and blondes); it must never roll a plain-human colour
    like auburn that only appears in the shared heritage tables. Age-graying (40+) can still turn hair gray or white,
    same as any other race, so this only checks young adults."""
    hair = [roll.roll_npc(data, "fantasy", {"origin": "elf", "age_group": "young_adult"}, seed=s)["appearance"]["hair"] for s in range(80)]
    assert all(h == "bald" or h.split()[0] in ("silver", "platinum", "golden", "black", "chestnut", "copper", "white") for h in hair)
    assert any("silver" in h for h in hair) and not any("auburn" in h for h in hair)


def test_every_anthro_species_is_reachable_and_keeps_its_own_look(data):
    """All ten species in the anthro setting's races table roll, each with its own skin (coat/plumage/scale colour) --
    except 'therian', which deliberately has none, so a therianthrope keeps a plain human skin tone (the species is
    still mostly human, unlike the other nine)."""
    seen = {}
    for seed in range(400):
        npc = roll.roll_npc(data, "anthro", {"origin": "therian"} if seed % 10 == 0 else None, seed=seed)
        seen.setdefault(npc["profile"]["race"], []).append(npc["appearance"]["skin_tone"])
    races = data["settings"]["anthro"]["races"]
    assert set(seen) == {r["label"] for r in races.values()}, set(seen) ^ {r["label"] for r in races.values()}
    wolf_skins = set(seen[races["wolf"]["label"]])
    assert wolf_skins <= set(races["wolf"]["skin"])
    human_skins = {s for h in data["common"]["heritages"].values() if not h.get("mixed") for s in h["skin"]}
    therian_skins = set(seen[races["therian"]["label"]])
    assert therian_skins <= human_skins, therian_skins - human_skins


def test_the_anthro_distinguishing_table_never_names_one_species_only_feature(data):
    """Two of the ten species are scaled, not furred (reptile, dragontaur), and three have a human or beaked face, not
    a muzzle (avian, taur, dragontaur): a table entry that only reads right for the fur-and-muzzle majority would be a
    mismatch fed straight into the image prompt for those species. Caught once already (see the 2026-09-22 commit) --
    this keeps it from coming back."""
    for entry in data["settings"]["anthro"]["distinguishing"]:
        assert "muzzle" not in entry["label"] and "whisker" not in entry["label"], entry["label"]


@pytest.mark.parametrize("setting_id", ["aliens", "mythical"])
def test_every_alien_and_mythical_race_is_reachable_and_keeps_its_own_look(data, setting_id):
    """A race can state its own eyes, and a race that is not human at all can state fixed 'hair' and 'facial_hair' text: a
    grey alien never rolls 'auburn wavy long hair' or a beard, a gorgon always has snakes for hair."""
    races = data["settings"][setting_id]["races"]
    by_label = {r["label"]: r for r in races.values()}
    seen = set()
    for seed in range(600):
        npc = roll.roll_npc(data, setting_id, seed=seed)
        race, a = by_label[npc["profile"]["race"]], npc["appearance"]
        seen.add(race["label"])
        if race.get("eyes"):
            assert a["eyes"] in race["eyes"], (race["label"], a["eyes"])
        if race.get("hair"):
            assert a["hair"] == race["hair"], (race["label"], a["hair"])
        if "facial_hair" in race:
            assert a["facial_hair"] == race["facial_hair"], (race["label"], a["facial_hair"])
    assert seen == set(by_label), set(by_label) - seen


def test_a_chosen_hair_colour_or_style_wins_over_a_race_s_fixed_hair(data):
    """Fixed race hair is only the default: someone who picks a hair colour for a grey alien gets it."""
    grey = {"origin": "grey"}
    assert roll.roll_npc(data, "aliens", grey, seed=1)["appearance"]["hair"] == "a smooth hairless head"
    hair = roll.roll_npc(data, "aliens", {**grey, "hair_color": "silver"}, seed=1)["appearance"]["hair"]
    assert hair == "bald" or hair.startswith("silver"), hair


def test_mythical_body_plans_come_from_the_race(data):
    """Merfolk, centaurs and naga have no legs to walk on, so the full-body pipeline must know it; the rest are bipedal."""
    from npcgen import morphology
    assert morphology.from_race(data, "mythical", "merfolk") == "merfolk"
    assert morphology.from_race(data, "mythical", "Centaur") == "taur"
    assert morphology.from_race(data, "mythical", "naga") == "serpentine"
    assert morphology.from_race(data, "mythical", "fairy") == "bipedal"
    assert morphology.clean("mermaid") == "merfolk"
    assert morphology.unsupported_reason("merfolk", {"sprite": True}) and not morphology.unsupported_reason("merfolk", {"portrait": True})
    walkers = {roll.roll_npc(data, "mythical", {"morphology": "bipedal"}, seed=s)["profile"]["race"] for s in range(60)}
    assert not walkers & {"Merfolk", "Centaur", "Naga"}


def test_no_hair_style_label_starts_with_an_article(data):
    """The roller puts the colour (and texture) in front of the label: "a single long braid" came out as "auburn wavy a single
    long braid" in three settings before this test."""
    for setting_id in data["settings"]:
        for style in roll.setting_common(data, setting_id)["hair_styles"]:
            assert not style["label"].startswith(("a ", "an ")) or style.get("bald"), (setting_id, style["label"])


def test_no_setting_context_names_props_the_portrait_would_paint(data):
    """context goes into every image prompt. The witches' first context ("candlelit, with herbs, charms and old spellbooks")
    put a lit candle and an open book in front of both test witches, and steampunk's "gears and clockwork gadgets" glued brass
    cogs all over every outfit (2026-09-23). Props belong in an outfit or accessory, where one character gets them."""
    for setting_id, setting in data["settings"].items():
        context = setting.get("context", "").lower()
        for word in ("candle", "spellbook", "gears", "clockwork"):
            assert word not in context, (setting_id, word)


def test_the_superhero_setting_context_names_no_real_comic_book_city(data):
    """context (unlike blurb) is injected into every image prompt verbatim. With the word 'metropolis' in it, a chibi
    'sidekick' portrait (seed 1656717965) came back wearing a full, unmistakable Superman shield logo. Removing the
    word alone was NOT enough on its own (re-tested on GPU and the shield was still there) -- the outfit tables'
    "chest emblem" wording below turned out to be the bigger contributor. Keeping both regression tests: this one
    guards the word that first tipped generic "superhero" language into one specific trademarked character."""
    context = data["settings"]["superhero"]["context"].lower()
    for name in ("metropolis", "gotham"):
        assert name not in context, name


def test_the_superhero_setting_never_asks_for_a_bare_chest_emblem(data):
    """The real fix for the Superman-shield leak (see the context test above): 'a chest emblem'/'an emblem' with no
    further qualifier reads, to the image model, as "draw a generic iconic superhero emblem" -- which collapses to
    Superman's shield by default often enough to be a real problem, GPU-confirmed both before this fix (bare
    "a chest emblem") and after (still leaked with only the context-field fix applied). The outfit tables must always
    qualify it (e.g. "a symbol of their own design") rather than leaving it bare, and the setting should also carry
    its own negative-prompt terms as a second layer, not rely on wording alone."""
    setting = data["settings"]["superhero"]
    assert setting.get("negative"), "superhero needs its own negative-prompt terms as a second layer of defence"
    for occ in setting["occupations"]:
        for outfit in occ.get("outfits", []):
            if "emblem" in outfit or "logo" in outfit:
                assert "of their own" in outfit or "own design" in outfit, (occ["id"], outfit)
    for acc in setting.get("accessories", []):
        assert "emblem" not in acc, acc


# ---- the Advanced options popup: a chosen hair style, and what each field can be ------------------------------------------------------
def test_a_hair_style_choice_is_used_and_changes_nothing_else_in_the_roll(data):
    for seed in range(60):
        plain = roll.roll_npc(data, "modern", seed=seed)
        pixie = roll.roll_npc(data, "modern", {"hair_style": "pixie cut"}, seed=seed)
        assert pixie["appearance"]["hair"].endswith("pixie cut hairstyle"), (seed, pixie["appearance"]["hair"])
        assert {**pixie["appearance"], "hair": ""} == {**plain["appearance"], "hair": ""}, seed
        assert pixie["profile"] == plain["profile"]


def test_choosing_bald_or_a_style_for_someone_the_roll_made_bald_changes_nothing_else(data):
    """The roll stops before the colour draws when it rolls 'bald'; a chosen style must not shift any later draw either way."""
    old_men = {"gender": "male", "age_group": "senior"}
    bald_seeds = [s for s in range(200) if roll.roll_npc(data, "modern", old_men, seed=s)["appearance"]["hair"] == "bald"]
    assert bald_seeds, "no bald senior in 200 seeds: the test needs one"
    for seed in bald_seeds[:10] + list(range(10)):
        plain = roll.roll_npc(data, "modern", old_men, seed=seed)
        for choice in ("bald", "buzz cut"):
            chosen = roll.roll_npc(data, "modern", {**old_men, "hair_style": choice}, seed=seed)
            hair = chosen["appearance"]["hair"]
            assert hair == "bald" if choice == "bald" else hair.endswith("buzz cut hairstyle") and hair != "buzz cut hairstyle", (seed, hair)
            assert {**chosen["appearance"], "hair": ""} == {**plain["appearance"], "hair": ""}, (seed, choice)


def test_an_unknown_hair_style_is_ignored(data):
    assert roll.roll_npc(data, "modern", {"hair_style": "mohawk of flames"}, seed=4) == roll.roll_npc(data, "modern", seed=4)


def test_a_style_for_one_hair_texture_gets_that_texture(data):
    """Box braids are only rolled for coily or curly hair; chosen for straight-haired heritages they still read right."""
    hair = {roll.roll_npc(data, "modern", {"hair_style": "box braids", "origin": "east_asian"}, seed=s)["appearance"]["hair"] for s in range(20)}
    assert all(h.endswith("box braids hairstyle") or h.endswith("box braids") for h in hair), hair


@pytest.mark.parametrize("setting_id", list(roll.load_data()["settings"]))
def test_every_setting_offers_a_choice_for_every_field(data, setting_id):
    ch = roll.field_choices(data, setting_id)
    for key in ("builds", "hair_styles", "facial_hair", "glasses", "distinguishing", "accessories", "expressions", "traits",
                "hobbies", "quirks", "goals", "peeves", "speech", "demeanor"):
        assert ch[key], (setting_id, key)
    assert ch["skin"]["all"] and ch["eyes"]["all"]
    assert set(ch["outfits"]) == {o["id"] for o in data["settings"][setting_id]["occupations"]}
    for key in ("hobbies", "quirks", "goals", "peeves", "speech", "demeanor"):
        for entry in ch[key]:
            assert "{" not in entry["label"], (setting_id, key, entry)           # shown with "their", never a raw placeholder
    for none in ("none", "clean-shaven"):
        assert none not in ch["glasses"] + ch["facial_hair"] + ch["distinguishing"]   # "None" is its own choice in the popup


def test_a_chosen_entry_with_a_pronoun_is_worded_for_the_character(data):
    goal = next(e for e in roll.field_choices(data, "modern")["goals"] if "{poss}" in e["value"])
    assert goal["label"] == "opening a place of their own"
    for gender, word in (("female", "her"), ("male", "his")):
        npc = roll.roll_npc(data, "modern", {"gender": gender}, {"profile.goal": goal["value"]}, seed=3)
        assert npc["profile"]["goal"] == f"opening a place of {word} own"


def test_every_choice_the_popup_offers_is_taken_by_the_roll(data):
    """A popup value goes to roll_npc as a locked field; whatever the popup lists must come out on the sheet as chosen."""
    ch = roll.field_choices(data, "pirates")
    locks = {"appearance.build": ch["builds"][-1]["label"], "appearance.skin_tone": ch["skin"]["all"][-1], "appearance.eyes": ch["eyes"]["all"][-1],
             "appearance.facial_hair": ch["facial_hair"][-1], "appearance.glasses": "", "appearance.accessory": ch["accessories"][-1],
             "appearance.expression": ch["expressions"][0], "appearance.outfit": ch["outfits"]["captain"][0]["t"],
             "profile.personality": ch["traits"][:2], "profile.speech": ch["speech"][0]["value"], "profile.demeanor": ch["demeanor"][0]["value"]}
    npc = roll.roll_npc(data, "pirates", {"gender": "male"}, locks, seed=9)
    for path, value in locks.items():
        group, key = path.split(".")
        got = npc[group][key]
        assert got[:len(value)] == value if isinstance(value, list) else got == value, (path, got)
    assert len(npc["profile"]["personality"]) == 3                               # two chosen, the third rolled


def test_no_art_style_describes_proportions_in_heads(data):
    """Chibi's prompt said "super-deformed proportions about two heads tall". Z-Image Turbo, which runs at cfg 1 and so never
    sees the "multiple people" in the negative prompt, read it literally: a second head or a second person behind the character
    in 4 of 6 portraits (same prompt and seeds, 2026-09-23). Worded as "an oversized head on a tiny body" it was 0 of 6, and
    Mage-Flow's chibi portraits and full-body sprites came out the same as before at the same seeds."""
    for style_id, style in data["styles"].items():
        assert "heads tall" not in style["style"] and "two heads" not in style["style"], style_id
