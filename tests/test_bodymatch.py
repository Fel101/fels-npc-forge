"""npcgen/bodymatch.py: what a character's sheet says about its body. The build words, the skin, eye and hair colours, and what the tables must cover: every build,
skin tone, eye colour and hair colour the generator can roll (data/common.json and the settings), so a new word added to the data without a row here fails."""
import colorsys
import json
import re

import pytest

from npcgen import bodymatch, config

DATA = config.ROOT / "data"
BODY_CONTROLS = ["height", "head", "torso", "arms", "legs", "muscle", "fat", "shoulders", "chest", "waist", "hips", "limbs"]
CONTROLS = BODY_CONTROLS + ["face_width", "face_depth", "jaw_width", "chin_length", "cheeks", "brow", "nose", "eye_spacing", "mouth_width"]


def common():
    return json.loads((DATA / "common.json").read_text("utf-8"))


def labels(entries):
    return [e["label"] if isinstance(e, dict) else e for e in entries]


def rollable_builds():
    """Every build the generator can roll: the common list, each setting's own list and each race's."""
    found = set(labels(common()["builds"]))
    for path in sorted((DATA / "settings").glob("*.json")):
        setting = json.loads(path.read_text("utf-8"))
        found.update(labels(setting.get("builds") or []))
        for race in (setting.get("races") or {}).values():
            found.update(labels(race.get("builds") or []))
    return sorted(found)


def hls(hex_colour):
    return colorsys.rgb_to_hls(*bodymatch._rgb(hex_colour))


def values(text):
    return bodymatch.match({"build": text})["values"]


# ---- the build -------------------------------------------------------------------------------------------------------------------------------
def test_every_build_the_generator_can_roll_is_understood():
    assert len(rollable_builds()) >= 15
    for build in rollable_builds():
        found, heard = bodymatch.build_values(build)
        assert heard, f"nothing in data/body_match.json understands the build {build!r}"
    for odd in ("toned", "tall and slender", "hourglass", "full-figured", "thin", "lean and wiry", "sinewy", "Average", "  Athletic  "):    # (older characters and typed ones)
        assert bodymatch.build_values(odd)[1], odd
    assert bodymatch.match({"build": "mermaid"})["skipped"] == {"build": "mermaid"}                       # not understood: said, not guessed


def test_a_build_is_read_word_by_word_and_a_word_counts_once():
    tall, lanky = values("tall"), values("lanky")
    both = values("tall and lanky")
    assert both["height"] == tall["height"] and both["legs"] == pytest.approx(tall["legs"] + lanky["legs"]) and both["fat"] == lanky["fat"]
    assert values("slim and slender") == values("slim")                                                    # two words of one entry are one entry
    assert values("broad-shouldered") == values("broad shouldered") and values("broad-shouldered")["shoulders"] == 0.9    # a longer word is taken whole, not twice
    assert bodymatch.build_values("average")[0] == {} and bodymatch.build_values("average")[1] == ["average"]
    assert max(abs(v) for v in values("heavyset bloated obese huge").values()) <= 1.0                         # never beyond the proportion's end


def test_builds_are_ordered_the_way_the_words_are():
    def at(build, control):
        return values(build).get(control, 0.0)
    fat = [at(b, "fat") for b in ("gaunt and emaciated", "slim", "average", "stocky", "heavyset")]
    assert fat == sorted(fat) and len(set(fat)) == 5 and at("bloated and swollen", "waist") >= at("heavyset", "waist")
    assert at("petite", "height") < at("average", "height") < at("tall", "height") and at("stocky", "height") < 0 < at("tall and lanky", "height")
    assert at("slack", "muscle") < at("average", "muscle") < at("athletic", "muscle") < at("muscular", "muscle")
    assert at("curvy", "hips") > at("average", "hips") > at("slim", "hips") and at("curvy", "waist") < 0 < at("curvy", "chest")
    assert at("broad shouldered", "shoulders") > at("athletic", "shoulders") > at("petite", "shoulders")


def test_a_build_only_asks_for_proportions_the_body_has_and_never_beyond_their_ends():
    for entry in bodymatch.data()["builds"]:
        assert entry["words"] and all(w == w.lower() and w == " ".join(w.split()) and "-" not in w for w in entry["words"]), entry["words"]
        assert set(entry["values"]) <= set(BODY_CONTROLS) and all(-1 <= v <= 1 for v in entry["values"].values()), entry["words"]
    assert set(values("stocky")) <= set(BODY_CONTROLS)
    only = bodymatch.match({"build": "stocky"}, ["height", "fat"])["values"]
    assert set(only) == {"height", "fat"} and only["fat"] == values("stocky")["fat"]                         # the base's own proportions only


# ---- the colours -----------------------------------------------------------------------------------------------------------------------------
def test_every_skin_tone_the_generator_can_roll_is_understood_and_they_get_darker_in_order():
    rolled = sorted({tone for h in common()["heritages"].values() for tone in (h.get("skin") or {})})
    assert len(rolled) >= 10
    for tone in rolled:
        assert re.fullmatch(r"#[0-9a-f]{6}", bodymatch.colour(tone, "skin") or ""), tone
    ladder = [hls(bodymatch.colour(t, "skin"))[1] for t in ("very fair", "fair", "light", "tan", "brown", "dark brown", "deep dark brown")]
    assert ladder == sorted(ladder, reverse=True) and len(set(ladder)) == 7
    assert hls(bodymatch.colour("light olive", "skin"))[1] < hls(bodymatch.colour("light", "skin"))[1]
    assert hls(bodymatch.colour("medium brown", "skin"))[1] > hls(bodymatch.colour("dark brown", "skin"))[1]


def test_every_eye_colour_the_generator_can_roll_is_understood():
    rolled = sorted({eye for h in common()["heritages"].values() for eye in (h.get("eyes") or {})})
    assert {"blue", "brown", "dark brown", "gray", "green", "hazel"} <= set(rolled)
    for eye in rolled:
        assert re.fullmatch(r"#[0-9a-f]{6}", bodymatch.colour(eye, "eyes") or ""), eye
    blue, green, brown, dark, gray = (bodymatch._rgb(bodymatch.colour(e, "eyes")) for e in ("blue", "green", "brown", "dark brown", "gray"))
    assert blue[2] > blue[0] and green[1] > green[0] and green[1] > green[2] and sum(dark) < sum(brown) and colorsys.rgb_to_hls(*gray)[2] < 0.15
    assert bodymatch.colour("small glowing orange", "eyes") == bodymatch.colour("orange", "eyes") and bodymatch.colour("huge dark almond-shaped", "eyes") == bodymatch.colour("dark", "eyes")


def test_hair_gives_the_brows_a_colour_only_when_it_is_a_natural_one():
    natural = sorted({c for h in common()["heritages"].values() for c in (h.get("hair_colors") or {})})
    assert "dirty blonde" in natural
    for hair in natural:
        assert bodymatch.colour(f"{hair} wavy long hair", "hair"), hair
    for dyed in common()["dyed_hair_colors"]:                                                                # a dyed head does not say what the brows are
        assert bodymatch.colour(f"{dyed} wavy long hair", "hair") is None, dyed
    for text in ("bald", "no pink", "no pink, no green", "no black", "moss green wavy long hair", ""):
        assert bodymatch.colour(text, "hair") is None, text
    assert bodymatch.colour("dark brown wavy hair with a single streak of white", "hair") == bodymatch.colour("dark brown", "hair")    # the first colour it names
    assert bodymatch.colour("black two braids hairstyle", "hair") == bodymatch.colour("black", "hair") != bodymatch.colour("brown", "hair")
    assert hls(bodymatch.colour("black", "hair"))[1] < hls(bodymatch.colour("dark brown", "hair"))[1] < hls(bodymatch.colour("brown", "hair"))[1] < hls(bodymatch.colour("blonde", "hair"))[1]


def test_colours_that_are_not_a_person_s_still_get_a_colour_and_a_word_nobody_knows_is_left_out():
    green, sage, ash = (bodymatch._rgb(bodymatch.colour(t, "skin")) for t in ("emerald green", "pale sage green", "ash gray"))
    assert green[1] > green[0] and green[1] > green[2] and sage[1] > sage[0] and sage[1] > sage[2]
    assert sum(sage) > sum(green)                                                                          # 'pale' lightens
    assert colorsys.rgb_to_hls(*ash)[2] < 0.15
    assert bodymatch.colour("pale orange", "skin") != bodymatch.colour("pale", "skin")                      # not human 'pale' skin
    assert bodymatch.colour("translucent amber gel", "skin") == bodymatch.colour("amber", "skin")
    out = bodymatch.match({"skin_tone": "iridescent", "eyes": "kaleidoscopic"})
    assert out["skipped"] == {"skin_tone": "iridescent", "eyes": "kaleidoscopic"} and not out["said"] and not out["materials"]


# ---- the whole sheet -------------------------------------------------------------------------------------------------------------------------
def test_a_sheet_is_read_into_the_pieces_of_a_recipe_and_what_is_empty_changes_nothing():
    out = bodymatch.match({"build": "stocky", "skin_tone": "tan", "eyes": "hazel", "hair": "dark brown wavy hair in a bun", "outfit": "a coat"}, CONTROLS)
    assert out["values"] == values("stocky") and out["materials"] == {"skin_color": bodymatch.colour("tan", "skin"), "iris_color": bodymatch.colour("hazel", "eyes")}
    assert out["brows"] == {"color": bodymatch.colour("dark brown", "hair")}
    assert out["said"] == {"build": "stocky", "skin_tone": "tan", "eyes": "hazel", "hair": "dark brown wavy hair in a bun"}
    assert out["skipped"] == {"hair_style": "dark brown wavy hair in a bun"} and out["hair"] == {"style": None, "color": bodymatch.colour("dark brown", "hair")}   # (a bun has no game hair)
    for nothing in (None, {}, [], "stocky", {"build": "", "skin_tone": "  ", "eyes": None, "hair": 3}):
        empty = bodymatch.match(nothing)
        assert empty == {"values": {}, "materials": {}, "brows": {}, "hair": {}, "said": {}, "skipped": {}}, nothing
    odd = bodymatch.match({"build": "mermaid", "skin_tone": "tan", "hair": "pastel pink bob"})
    assert odd["said"] == {"skin_tone": "tan"} and odd["skipped"] == {"build": "mermaid", "hair": "pastel pink bob", "hair_style": "pastel pink bob"} and odd["brows"] == {}


def test_the_tables_are_well_formed_and_cover_the_dyes_the_generator_rolls():
    table = bodymatch.data()
    for section in ("skin", "eyes", "hair"):
        for phrase, colour in table[section]["phrases"].items():
            assert re.fullmatch(r"#[0-9a-f]{6}", colour) and phrase == phrase.lower() and phrase == " ".join(phrase.split()) and "-" not in phrase, (section, phrase)
        for word, colour in (table[section].get("colours") or {}).items():
            assert re.fullmatch(r"#[0-9a-f]{6}", colour) and word == word.lower() and " " not in word, (section, word)
        for word, (lighter, more_saturated) in (table[section].get("modifiers") or {}).items():
            assert -0.3 <= lighter <= 0.3 and -0.3 <= more_saturated <= 0.3, (section, word)
    assert set(common()["dyed_hair_colors"]) <= set(table["hair"]["ignore"])


# ---- the hair ----------------------------------------------------------------------------------------------------------------------------------
HAIR_STYLES = {                                                    # every style the generator rolls (data/common.json): the game hair it is, or None where the Body Designer has none yet
    "long hair": "long", "shoulder-length hair": None, "chin-length bob": None, "pixie cut": "crop", "high ponytail": None, "messy bun": None, "low bun": None,
    "curtain bangs": None, "two braids": None, "box braids": None, "natural afro": None, "cornrows": None, "locs": None, "short cropped hair": "crop", "buzz cut": "crop",
    "side-parted short hair": "sweep", "slicked-back hair": "sweep", "undercut with a textured top": "crop", "medium-length tousled hair": None, "man bun": None, "bald": "none"}


def test_every_hair_style_the_generator_rolls_is_the_game_hair_it_is_or_none_at_all():
    assert set(HAIR_STYLES) == set(labels(common()["hair_styles"])), "a new hair style in data/common.json needs a decision here"
    for label, style in HAIR_STYLES.items():
        assert bodymatch.hair_style(label) == style and bodymatch.hair_style(label + " hairstyle") == style, label
        assert bodymatch.hair_style(f"dark brown wavy {label}") == style, label                            # (the sheet's own wording: colour, curl, then the style)


def test_a_hair_style_that_has_no_game_hair_wins_over_a_word_that_has():
    assert bodymatch.hair_style("short ponytail") is None and bodymatch.hair_style("short braids") is None and bodymatch.hair_style("short, in a bun") is None
    assert bodymatch.hair_style("short, swept to one side") == "sweep" and bodymatch.hair_style("black buzz cut with a shaved pattern hairstyle") == "crop"
    assert bodymatch.hair_style("bald") == "none" and bodymatch.hair_style("shaved head") == "none" and bodymatch.hair_style("hairless") == "none"
    for nothing in ("", "no pink", "wild, soot-streaked hair sticking up at the back", "black patchy hair falling out in clumps"):
        assert bodymatch.hair_style(nothing) is None, nothing


LONG_HAIR = {                                                      # what a sheet says: (the game hair it is, the nearest one when it is not exactly one)
    "black long hair": ("long", None), "dark brown wavy long glossy hair": ("long", None), "waist-length black hair": ("long", None), "blonde long hair in big bouncy curls": ("long", None),
    "black long hair, side parted": ("long", None), "very long straight hair": ("long", None),
    "black wavy long hair in a loose braid": (None, "long"), "golden blonde long hair in a sleek high ponytail": (None, "long"), "dark brown long hair tied back in a tarred queue": (None, "long"),
    "dark brown wavy long hair with curtain bangs": (None, "long"), "brown long hair swept over one shoulder": (None, "long"), "a single long braid of hair down the back": (None, "long"),
    "black long hair pinned up in a bun": (None, None), "black curly long hair in a sleek low bun": (None, None), "dark brown long hair in a chin-length bob": (None, None),
    "dark brown wavy long bob": (None, None), "black long hair tied back with a leather cord": (None, "long"), "black wavy undercut with a long top": (None, None), "bald with a long white beard": (None, None),
    "short hair with a long fringe": (None, None), "shoulder-length hair": (None, None), "medium-length tousled hair": (None, None), "longer on top": (None, None),
    "two braids": (None, None), "": (None, None)}


def test_long_hair_is_the_body_designers_long_unless_something_says_it_is_not_that_and_the_nearest_when_it_nearly_is():
    for text, (style, near) in LONG_HAIR.items():
        for said in (text, text + " hairstyle", "dark brown wavy " + text):                                    # (the sheet's own wording: colour, curl, then the style)
            assert bodymatch.hair_style(said) == style and bodymatch.hair_nearest(said) == near, said


def test_every_word_of_the_long_hair_tables_does_what_its_table_says():
    styles = bodymatch.data()["hair_styles"]
    for word in styles["long"]:                                                                               # long hair, whatever way it is said
        assert bodymatch.hair_style(f"black {word} hair") == "long" and bodymatch.hair_nearest(f"black {word} hair") is None, word
    for word in styles["not_long"]:                                                                           # up, short or middling: not the Long, and nothing nearer than a Crop
        assert bodymatch.hair_style(f"black long hair {word}") is None and bodymatch.hair_nearest(f"black long hair {word}") is None, word
    for word in styles["long_near"]:                                                                          # long, but not the way the Long is: the Long as the nearest
        assert bodymatch.hair_style(f"black long hair {word}") is None and bodymatch.hair_nearest(f"black long hair {word}") == "long", word


def test_a_sheet_with_long_hair_says_so_and_one_that_is_nearly_long_says_what_is_nearest():
    exact = bodymatch.match({"hair": "dark brown wavy long hair"})
    assert exact["hair"] == {"style": "long", "color": bodymatch.colour("dark brown", "hair")} and exact["skipped"] == {}
    assert exact["said"] == {"hair_style": "dark brown wavy long hair", "hair": "dark brown wavy long hair"}
    near = bodymatch.match({"hair": "black wavy long hair in a loose braid"})
    assert near["hair"] == {"style": None, "near": "long", "color": bodymatch.colour("black", "hair")} and near["skipped"] == {"hair_style": "black wavy long hair in a loose braid"}
    bun = bodymatch.match({"hair": "black long hair pinned up in a bun"})
    assert bun["hair"] == {"style": None, "color": bodymatch.colour("black", "hair")}                         # (no `near` unless long hair is the nearest)


def test_the_hair_itself_may_be_dyed_and_the_brows_never_are():
    for dye in common()["dyed_hair_colors"]:
        found = bodymatch.hair_colour(f"{dye} pixie cut")
        assert found == bodymatch.data()["dyed"][dye] and bodymatch.colour(f"{dye} pixie cut", "hair") is None, dye
    assert bodymatch.hair_colour("black wavy short cropped hair") == bodymatch.colour("black", "hair")
    assert bodymatch.hair_colour("no pink") is None and bodymatch.hair_colour("bald") is None
    assert bodymatch.hair_colour("black buzz cut with a shaved pattern hairstyle") == bodymatch.colour("black", "hair")      # 'shaved' is no dye
    out = bodymatch.match({"hair": "pastel pink pixie cut"})
    assert out["hair"] == {"style": "crop", "color": bodymatch.data()["dyed"]["pastel pink"]} and out["brows"] == {} and out["said"] == {"hair_style": "pastel pink pixie cut"}
    assert bodymatch.match({"hair": "bald"})["said"] == {"hair_style": "bald"} and bodymatch.match({"hair": "bald"})["skipped"] == {}      # a bald head has no colour to be missing
    assert bodymatch.match({"hair": "black short cropped hair"})["brows"] == {"color": bodymatch.colour("black", "hair")}


def test_the_hair_tables_are_well_formed():
    table = bodymatch.data()
    for colour in table["dyed"].values():
        assert re.fullmatch(r"#[0-9a-f]{6}", colour)
    for kind, words in table["hair_styles"].items():
        assert kind in ("none", "other", "sweep", "crop", "long", "not_long", "long_near")
        for word in words:
            assert word == word.lower() and word == " ".join(word.split()) and "-" not in word, (kind, word)
    styles = table["hair_styles"]
    assert styles["long"] and not set(styles["long"]) & set(styles["other"]), "a word is long hair or another style, not both"
    assert not set(styles["long_near"]) & set(styles["not_long"]), "a word is the nearest to long hair or not long hair, not both"
