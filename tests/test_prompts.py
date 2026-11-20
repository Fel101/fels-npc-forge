import pytest

from npcgen import prompts, roll


@pytest.fixture(scope="module")
def data():
    return roll.load_data()


def npc_with(data, **appearance):
    npc = roll.roll_npc(data, "modern", seed=4)
    npc["style"] = "pixel"
    neutral = {"hair": "black hair", "eyes": "brown", "accessory": "", "skin_tone": "tan", "extra": ""}
    npc["appearance"].update({**neutral, **appearance})
    return npc


def test_upper_body_drops_footwear_and_lower_body_but_keeps_the_rest():
    assert prompts.upper_body("a hoodie, joggers and clean sneakers") == "a hoodie"
    assert prompts.upper_body("a tailored dark suit with polished leather shoes") == "a tailored dark suit"
    assert prompts.upper_body("an orange vest over a work shirt, a hard hat, work trousers and steel-toe boots") == \
        "an orange vest over a work shirt, a hard hat"
    assert prompts.upper_body("boots") == "boots"                       # never returns an empty outfit
    # only the footwear goes, not everything after the first "with": the stethoscope, the pendant and the staff are in the portrait
    assert prompts.upper_body("light blue scrubs with a stethoscope around the neck and white clogs") == \
        "light blue scrubs with a stethoscope around the neck"
    assert prompts.upper_body("a white and gold robe with a holy symbol pendant and simple sandals") == \
        "a white and gold robe with a holy symbol pendant"
    assert prompts.upper_body("a warm woolen robe, a shawl and a walking staff with simple leather shoes") == \
        "a warm woolen robe, a shawl and a walking staff"
    assert prompts.upper_body("a white lab coat over a blouse and slacks with flat shoes") == "a white lab coat over a blouse"


def test_no_outfit_in_the_data_leaks_footwear_into_a_portrait(data):
    outfits = [f if isinstance(f, str) else f["t"] for s in data["settings"].values()
               for o in s["occupations"] + [{"outfits": s["default_outfits"]}] for f in o["outfits"]]
    assert outfits
    for outfit in outfits:
        assert not prompts._LOWER_BODY.search(prompts.upper_body(outfit)), outfit


def test_portrait_omits_shoes_but_the_sprite_keeps_them(data):
    npc = npc_with(data, outfit="a green hoodie, dark jeans and white sneakers")
    style = data["styles"]["pixel"]
    assert "sneakers" not in prompts.portrait(npc, style)["positive"]
    assert "white sneakers" in prompts.sprite(npc, style)["positive"]


def test_structured_prompts_include_the_notes_field(data):
    npc = npc_with(data, extra="a lightning-shaped scar on the left cheek")
    assert "lightning-shaped scar" in prompts.portrait(npc, data["styles"]["pixel"])["positive"]


def test_freeform_prompts_use_the_description_verbatim_and_say_character(data):
    npc = roll.roll_npc(data, "modern", freeform="an elderly orc blacksmith with a bone crown", seed=1)
    npc["style"] = "cartoon"
    style = data["styles"]["cartoon"]
    gender = npc["profile"]["gender"]
    lead = {"female": "a female character, ", "male": "a male character, "}.get(gender, "")      # the Who card's gender, where the words say none
    assert f"Head and shoulders portrait of {lead}an elderly orc blacksmith with a bone crown." in prompts.portrait(npc, style)["positive"]
    assert "Show the same character" in prompts.sprite_edit(npc, style)["positive"]
    assert str(npc["profile"]["age"]) not in prompts.portrait(npc, style)["positive"]
    # a robot must not be told to keep its 'hair' and 'skin tone' (that turned a teapot-headed robot into a bald human)
    for text in (prompts.sprite_edit(npc, style)["positive"], prompts.portrait(npc, style)["positive"]):
        assert "skin tone" not in text and "person" not in text
    assert "every distinctive feature" in prompts.sprite_edit(npc, style)["positive"]
    structured = npc_with(data)
    assert "skin tone" in prompts.sprite_edit(structured, style)["positive"]


LEGACY_POSE = "standing in a relaxed A-pose with the arms held slightly away from the body, both hands empty and open"
CLERK = ("a small, ink-stained clerk with round glasses and her dark hair in a bun, a long grey wool coat with brass buttons, "
         "hands stained with ink, a leather pen-case on the belt, black leather boots")


def freeform(data, text, style="cartoon"):
    npc = roll.roll_npc(data, "modern", freeform=text, seed=1)
    npc["style"] = style
    return npc, data["styles"][style]


def test_a_description_made_for_a_full_body_sprite_does_not_pull_the_portrait_to_full_length(data):
    """A game's bulk run (2026-10-11): a stance sentence at the end of a free description made all 164 portraits full length."""
    npc, style = freeform(data, f"{CLERK}, {LEGACY_POSE}")
    portrait = prompts.portrait(npc, style)["positive"]
    for gone in ("A-pose", "standing", "arms held", "hands empty", "open", "boots"):
        assert gone not in portrait, gone
    for kept in ("round glasses", "dark hair in a bun", "grey wool coat", "hands stained with ink", "pen-case"):
        assert kept in portrait, kept
    assert "Head and shoulders portrait of" in portrait
    sprite = prompts.sprite(npc, style)["positive"]                       # the full-body sprite wants all of it
    assert LEGACY_POSE in sprite and "black leather boots" in sprite


def test_only_clauses_that_state_a_stance_or_a_whole_body_are_dropped():
    keep = ("hands stained with ink", "a coat standing open at the collar", "arms covered in tattoos", "feet wrapped in rags",
            "a hat with an open brim", "open-weave gloves on her hands", "legs of a heron", "a stand of pines behind her")
    for clause in keep:
        assert prompts.portrait_text(f"a tall woman, {clause}") == f"a tall woman, {clause}", clause
    for clause in ("standing guard at the gate", "stands with her arms crossed", "in a T-pose", "arms held out to the sides",
                   "her hands at her sides", "both hands empty and open", "full-body shot", "seen from head to toe",
                   "arms folded across the chest", "hands on her hips", "legs spread apart", "posed like a statue"):
        assert prompts.portrait_text(f"a tall woman, {clause}, with a red scarf") == "a tall woman, with a red scarf", clause


def test_text_with_nothing_to_drop_comes_back_exactly_as_written(data):
    for text in ("an elderly orc blacksmith with a bone crown", "A robot. Its head is a teapot; steam rises from the spout.",
                 "  a woman in a green coat ", "short hair, brown eyes"):
        assert prompts.portrait_text(text) == text
    assert prompts.portrait_text("a woman, standing in an A-pose") == "a woman"
    assert prompts.portrait_text("She is a clerk. She stands in a relaxed A-pose. She wears a grey coat.") == "She is a clerk. She wears a grey coat."
    assert prompts.portrait_text("standing in an A-pose") == ""                                   # describe() then says "a character"
    npc, style = freeform(data, "standing in a relaxed A-pose")
    lead = {"female": "a female character", "male": "a male character"}.get(npc["profile"]["gender"], "a character")
    assert f"Head and shoulders portrait of {lead}." in prompts.portrait(npc, style)["positive"]


def test_a_free_description_loses_its_lower_body_in_a_head_and_shoulders_portrait_as_an_outfit_does(data):
    npc, style = freeform(data, "a tall courier in a red jacket, black trousers and muddy boots")
    assert "trousers" not in prompts.portrait(npc, style)["positive"] and "red jacket" in prompts.portrait(npc, style)["positive"]
    assert "muddy boots" in prompts.sprite(npc, style)["positive"]


def test_the_portrait_framing_chooses_how_much_of_the_character_shows(data):
    npc, style = freeform(data, f"{CLERK}, {LEGACY_POSE}")
    assert prompts.portrait(npc, style, framing="bust") == prompts.portrait(npc, style)
    half = prompts.portrait(npc, style, framing="half")["positive"]
    assert "Waist-up portrait of" in half and "A-pose" not in half and "boots" not in half
    full = prompts.portrait(npc, style, framing="full")["positive"]
    assert "Full-length portrait of" in full and "A-pose" in full and "black leather boots" in full      # the whole body: as written
    assert prompts.clean_framing(None) == prompts.clean_framing("") == "bust"
    with pytest.raises(ValueError, match="Unknown portrait framing 'headshot'.*bust, half, full"):
        prompts.clean_framing("headshot")
    with pytest.raises(ValueError):
        prompts.portrait(npc, style, framing="halff")


def test_a_structured_characters_notes_lose_their_pose_in_the_portrait_too(data):
    npc = npc_with(data, extra=f"a lightning-shaped scar on the left cheek, {LEGACY_POSE}")
    style = data["styles"]["pixel"]
    portrait = prompts.portrait(npc, style)["positive"]
    assert "lightning-shaped scar" in portrait and "A-pose" not in portrait
    assert LEGACY_POSE in prompts.sprite(npc, style)["positive"]


def test_backdrop_colour_avoids_the_clothing_colours(data):
    assert prompts.pick_key(npc_with(data, outfit="a bright pink hoodie")) == "green"
    assert prompts.pick_key(npc_with(data, outfit="a pink and green striped scarf")) == "blue"
    assert prompts.pick_key(npc_with(data, outfit="a navy coat", hair="black bob", accessory="", skin_tone="tan")) == "magenta"


def test_watercolor_sprite_uses_a_blue_key_and_comic_sprite_excludes_scenery(data):
    npc = npc_with(data, outfit="a pale shirt and brown trousers")
    watercolor = data["styles"]["watercolor"]
    assert prompts.pick_key(npc, watercolor) == "blue"
    assert prompts.pick_key(npc_with(data, outfit="a navy coat"), watercolor) == "green"
    water_prompt = prompts.sprite(npc, watercolor, key="blue")
    assert "solid blue across the entire canvas" in water_prompt["positive"]
    comic_prompt = prompts.sprite(npc, data["styles"]["comic"])
    assert comic_prompt["positive"].startswith("A single isolated character cutout")
    assert "skyline" in comic_prompt["negative"]


def test_person_word_follows_age_and_gender():
    for age, gender, word in [(8, "female", "girl"), (15, "male", "teenage boy"), (40, "female", "woman"),
                              (40, "nonbinary", "androgynous person")]:
        assert prompts.person_word({"age": age, "gender": gender}) == word


def test_edit_prompts_name_the_style_once(data):
    style = data["styles"]["pixel"]
    assert "pixel art art" not in prompts.expression_edit("a big smile", style)["positive"]
    assert "16-bit pixel art style" in prompts.expression_edit("a big smile", style)["positive"]


def test_auto_sprite_mode_edits_people_but_draws_freeform_characters_from_their_description():
    from npcgen.jobs import resolve_sprite_mode
    assert resolve_sprite_mode("auto", freeform=False, has_portrait=True) == "match"
    assert resolve_sprite_mode("auto", freeform=True, has_portrait=True) == "independent"
    assert resolve_sprite_mode("match", freeform=True, has_portrait=True) == "match"           # an explicit choice wins
    assert resolve_sprite_mode("independent", freeform=False, has_portrait=True) == "independent"
    assert resolve_sprite_mode("match", freeform=False, has_portrait=False) == "independent"   # nothing to edit yet
    from npcgen import service
    assert service.clean_options({}, {"quality": {"standard": {}}})["sprite_mode"] == "auto"
    assert service.clean_options({}, {"quality": {"standard": {}}})["save_candidates"] is False
    assert service.clean_options({"save_candidates": True}, {"quality": {"standard": {}}})["save_candidates"] is True
    assert service.clean_options({"sprite_mode": "nonsense"}, {"quality": {"standard": {}}})["sprite_mode"] == "auto"
    assert service.clean_options({"seed": 0}, {"quality": {"standard": {}}})["seed"] == 0
    with pytest.raises(ValueError, match="Seed"):
        service.clean_options({"seed": "not-a-number"}, {"quality": {"standard": {}}})
    with pytest.raises(ValueError, match="options must be an object"):
        service.clean_options([], {"quality": {"standard": {}}})


def test_the_sprite_edit_restates_the_outfit_because_the_portrait_hides_everything_below_the_chest(data):
    """A shopkeeper whose outfit said brown boots came out in sandals: the edit only saw a head-and-shoulders portrait."""
    npc = roll.roll_npc(data, "western", locked={"appearance.outfit": "a cream blouse, a long olive skirt and worn brown boots",
                                                  "appearance.accessory": "a brass locket"}, seed=2)
    text = prompts.sprite_edit(npc, data["styles"]["photoreal"], "magenta", "front")["positive"]
    assert "everything below the shoulders included, is: a cream blouse, a long olive skirt and worn brown boots, with a brass locket." in text
    assert prompts.complete_outfit({**npc, "appearance": {**npc["appearance"], "accessory": ""}}).endswith("worn brown boots")


def test_a_freeform_character_is_not_dressed_by_the_sprite_edit(data):
    npc = roll.roll_npc(data, "modern", freeform="a tiny robot chef with a copper teapot head", seed=1)
    assert prompts.complete_outfit(npc) == ""
    assert "complete outfit" not in prompts.sprite_edit(npc, data["styles"]["cartoon"])["positive"]


def test_the_build_leads_the_description_so_a_portrait_shows_it(data):
    """'stocky build' in the middle of the list drew a slim man on Qwen Image 2.1 and Mage-Flow; worded up front by what a
    head-and-shoulders picture shows ("stocky, broad-shouldered, thick-necked"), it drew him visibly broader (2026-09-23)."""
    style = data["styles"]["semi_real"]
    stocky = roll.roll_npc(data, "modern", {"gender": "male"}, {"appearance.build": "stocky", "profile.age": 24}, seed=1)
    text = prompts.describe(stocky, style, portrait=True)
    assert "a stocky, broad-shouldered, thick-necked 24-year-old man" in text and "stocky build" not in text
    athletic = {**stocky, "appearance": {**stocky["appearance"], "build": "athletic"}}
    assert "an athletic, fit 24-year-old man" in prompts.describe(athletic, style)
    own = {**stocky, "appearance": {**stocky["appearance"], "build": "lean and wiry"}}     # a setting's own build: as before
    text = prompts.describe(own, style)
    assert "a 24-year-old man" in text and "lean and wiry build" in text


def test_only_the_manga_style_is_turned_grey(data):
    """Its words ask for no colour, yet a character described in colour came out coloured on Z-Image and Mage-Flow alike
    (2026-09-25), so its finished pictures are turned grey after the cut-out (keying.grey_image)."""
    grey = [sid for sid, style in data["styles"].items() if style.get("monochrome")]
    assert grey == ["manga"]


def test_a_portrait_leaves_out_what_the_filter_once_did_not_know_below_the_waist():
    """Measured 2026-10-13 on the 744 outfits of the data tables: 88 named skirts, stockings, oxfords, trainers, breeches, waders, spats, socks, bare feet or a single
    missing shoe that the head-and-shoulders portrait then drew (a gown "with a slit skirt" and "worn oxfords" pulled the picture down to the hips and the shoes)."""
    cases = {
        "a fitted sequined gown with a slit skirt, long gloves": "a fitted sequined gown, long gloves",
        "a fringed shawl over a patterned blouse, a long skirt, many rings": "a fringed shawl over a patterned blouse, many rings",
        "a rolled-sleeve blouse, a long damp apron over a plain skirt": "a rolled-sleeve blouse, a long damp apron",
        "a tailored blouse and pencil skirt, seamed stockings": "a tailored blouse",
        "a laced bodice over a white blouse, a long skirt and an apron,": "a laced bodice over a white blouse, an apron",
        "a rumpled trench coat over a white shirt, loosened tie, worn oxfords": "a rumpled trench coat over a white shirt, loosened tie",
        "a smart navy-blue coat with brass buttons, a cocked hat, white breeches": "a smart navy-blue coat with brass buttons, a cocked hat",
        "a heavy wool sweater under an oilskin cloak, waders": "a heavy wool sweater under an oilskin cloak",
        "a double-breasted pinstripe suit, a wide-brimmed fedora and polished spats": "a double-breasted pinstripe suit, a wide-brimmed fedora",
        "a torn vest over a stained shirt, a shredded bar apron and one shoe missing": "a torn vest over a stained shirt, a shredded bar apron",
        "a shredded leather jacket studded with patches, a single scuffed boot": "a shredded leather jacket studded with patches",
        "a patched leather vest over a rough tunic, a toll pouch at the belt and bare feet": "a patched leather vest over a rough tunic, a toll pouch at the belt",
        "a sleek black flight suit with a personalised patch, an expensive watch and white trainers": "a sleek black flight suit with a personalised patch, an expensive watch",
        "a black crew polo with a small patch, a hand controller on a strap, grip socks": "a black crew polo with a small patch, a hand controller on a strap",
    }
    for outfit, wanted in cases.items():
        assert prompts.upper_body(outfit) == wanted, outfit
    # a skirt SUIT is the jacket and stays; an outfit that is nothing but lower body comes back whole; the words as written stay as written
    for outfit in ("a tailored dark skirt suit with a blouse", "a grey skirt suit a size too big, a plain white blouse, an armband", "a long skirt",
                   "a white and gold robe", "a skirted coat with brass buttons"):
        assert prompts.upper_body(outfit) == outfit, outfit


def test_a_free_description_loses_its_skirt_and_stockings_in_a_portrait_but_the_sprite_keeps_them(data):
    npc, style = freeform(data, "a tall courier in a red jacket, a pleated skirt, seamed stockings and muddy boots")
    portrait = prompts.portrait(npc, style)["positive"]
    for gone in ("skirt", "stockings", "boots"):
        assert gone not in portrait, gone
    assert "red jacket" in portrait
    sprite = prompts.sprite(npc, style)["positive"]
    assert "pleated skirt" in sprite and "seamed stockings" in sprite and "muddy boots" in sprite
    assert "pleated skirt" in prompts.portrait(npc, style, framing="full")["positive"]               # a full-length portrait shows them
    assert prompts.portrait_text("a gown with a slit skirt, long gloves") == "a gown, long gloves"


def test_a_head_and_shoulders_portrait_says_where_the_frame_ends_and_the_other_framings_do_not(data):
    """Measured 2026-10-13 (six characters, four picture engines): the opening words alone drew elbows, wrists and sometimes the hips; this sentence, after the look and before the backdrop, drew fewer."""
    npc, style = freeform(data, CLERK)
    ends = prompts.FRAMING_ENDS["bust"]
    for key in (None, "green"):
        bust = prompts.portrait(npc, style, key=key)["positive"]
        assert bust.count(ends) == 1 and f"looking at the camera. {ends} " in bust, key                          # straight after the look, before the backdrop
        assert bust.endswith("no shadow." if key else style["backdrop"] + "."), key
    assert prompts.clean_framing("bust") == "bust" and set(prompts.FRAMING_ENDS) == {"bust"}                    # half and full are as they were: not measured, not changed
    for framing in ("half", "full"):
        assert ends not in prompts.portrait(npc, style, framing=framing)["positive"]
    assert ends not in prompts.sprite(npc, style)["positive"] and ends not in prompts.sprite_edit(npc, style)["positive"]
    assert "cut" not in ends and "crop" not in ends                       # a wording that said the picture cuts the body made Qwen draw a seam across the chest


# ---------- the Who card's gender leads a description that is silent about it (2026-10-19; Kessa Lark: a woman on the card, a man in the picture) ----------

SILENT = "A wiry caravan runner, lean and quick, a long dark braid over one shoulder, a rust-red scarf, a sleeveless brown jerkin"


def card(data, text, gender, style="cartoon"):
    """A freeform character whose Who card says `gender` (the roll's own gender is replaced, as the owner does on the card)."""
    npc, style = freeform(data, text, style)
    npc["profile"]["gender"] = gender
    return npc, style


def test_a_description_silent_about_the_sex_is_led_by_the_cards_gender_in_the_portrait_and_the_sprite(data):
    for gender, lead in (("female", "a female character, a wiry caravan runner"), ("male", "a male character, a wiry caravan runner")):
        npc, style = card(data, SILENT, gender)
        assert f"Head and shoulders portrait of {lead}, lean and quick" in prompts.portrait(npc, style)["positive"]
        assert f"centered, of {lead}, lean and quick" in prompts.sprite(npc, style)["positive"]
        assert prompts.describe(npc, style).endswith("a rust-red scarf, a sleeveless brown jerkin")        # the owner's words follow as written
    # the age on the card is rolled for the data sheet and is not said: the words decide how old the character looks
    npc, style = card(data, SILENT, "female")
    npc["profile"]["age"] = 9
    for said_age in ("girl", "year-old", " 9 "):
        assert said_age not in prompts.portrait(npc, style)["positive"], said_age


def test_a_setting_context_comes_before_the_gender(data):
    npc, style = card(data, SILENT, "female")
    setting = {"context": "In a dusty frontier town"}
    assert prompts.describe(npc, style, setting) == "In a dusty frontier town: a female character, a wiry caravan runner" + SILENT[len("A wiry caravan runner"):]


def test_words_that_say_the_sex_are_the_whole_answer_and_come_back_exactly_as_written(data):
    said = ("A 22-year-old adult woman with a feminine face, a wiry caravan runner", "a tall man in a green coat", "She is a clerk in a grey coat",
            "A courier and his satchel", "an old sage with a long grey beard", "a gentleman in a top hat", "A priestess in white",
            "a boy with a kite", "A handsome prince in a red cape", "a non-binary mechanic with oil-stained hands", "an androgynous elf")
    for gender in ("female", "male"):
        for text in said:
            npc, style = card(data, text, gender)
            assert prompts.describe(npc, style) == text, (gender, text)
            assert prompts.describe(npc, style, portrait=True) == text, (gender, text)
            assert "female character" not in prompts.portrait(npc, style)["positive"] and "male character" not in prompts.portrait(npc, style)["positive"], text
    # the owner's words win over the card, whichever way they differ
    npc, style = card(data, "a tall man in a green coat", "female")
    assert "female" not in prompts.sprite(npc, style)["positive"]


def test_a_thing_that_is_not_a_person_is_not_given_a_sex(data):
    for text in ("A rusty tin robot with glowing blue eyes", "A giant fire dragon sitting on a hoard", "Mira, a clockwork fox with brass whiskers",
                 "a tiny robot chef with a copper teapot head", "A robot. Its head is a teapot; steam rises from the spout.", "a slime with a crown"):
        for gender in ("female", "male"):
            npc, style = card(data, text, gender)
            assert prompts.describe(npc, style) == text, (gender, text)
    # ...but a person who keeps one is still a person
    for text in ("A ranger with a pet fox and a leather jerkin", "Kessa Lark, a ranger with a long braid, a wolf at the heel"):
        npc, style = card(data, text, "female")
        assert prompts.describe(npc, style).startswith("a female character, "), text


def test_only_female_and_male_lead_a_description(data):
    for gender in ("nonbinary", "", None, "other"):
        npc, style = card(data, SILENT, gender)
        assert prompts.describe(npc, style) == SILENT, gender
        assert prompts.portrait(npc, style)["positive"] == prompts.portrait(dict(npc, profile={**npc["profile"], "gender": "nonbinary"}), style)["positive"]


def test_a_portrait_is_led_when_only_its_dropped_pose_clause_named_the_sex(data):
    """"standing with her arms crossed" is dropped from a head-and-shoulders portrait, and with it the only word that said the sex."""
    text = "a wiry caravan runner with a red scarf, standing with her arms crossed"
    npc, style = card(data, text, "female")
    portrait = prompts.portrait(npc, style)["positive"]
    assert "arms crossed" not in portrait and "of a female character, a wiry caravan runner with a red scarf." in portrait
    sprite = prompts.sprite(npc, style)["positive"]                                  # the full-body sprite keeps the sentence, and with it her
    assert "her arms crossed" in sprite and "female character" not in sprite


def test_an_empty_description_is_a_character_of_the_cards_gender(data):
    npc, style = card(data, "standing in a relaxed A-pose", "male")
    assert "Head and shoulders portrait of a male character." in prompts.portrait(npc, style)["positive"]
    npc, style = card(data, "standing in a relaxed A-pose", "nonbinary")
    assert "Head and shoulders portrait of a character." in prompts.portrait(npc, style)["positive"]


def test_a_structured_character_is_described_as_before(data):
    """Only a freeform character is led by its gender: the structured prompt has always said the woman or the man."""
    npc = npc_with(data)
    assert "female character" not in prompts.portrait(npc, data["styles"]["pixel"])["positive"]
    assert "male character" not in prompts.portrait(npc, data["styles"]["pixel"])["positive"]
    assert prompts.person_word(npc["profile"]) in prompts.describe(npc, data["styles"]["pixel"])
