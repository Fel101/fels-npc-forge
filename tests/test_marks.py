"""npcgen/marks.py: the skin marks a character's sheet names, as the Body Designer's recipe marks. What it paints, where, how big, on which side; what it leaves out and says (a tattoo, the chin, a scar with no place);
that a long description is not read as a list of places; that the same character always gets the same marks; the words for the card. The Designer's own catalogue is replaced by a copy of it, so none of this needs the Body Designer."""
import sys

import pytest

from npcgen import marks

PLACES = ["face", "cheeks", "nose", "forehead", "neck", "shoulders", "arms", "hands", "chest", "back", "legs"]
CAT = {"version": 1, "minimum_age": 18, "maximum_marks": 16, "places": PLACES,
       "kinds": [{"id": "freckles", "size_mm": [8, 150]}, {"id": "vitiligo", "size_mm": [8, 150]}, {"id": "mole", "size_mm": [1, 12]}, {"id": "beauty_mark", "size_mm": [1, 8]},
                 {"id": "birthmark", "size_mm": [3, 150]}, {"id": "scar", "size_mm": [5, 100]}]}
needs_marks = pytest.mark.skipif(not marks.offered(), reason="this Body Designer paints no skin marks")


def read(text, ident="ilsa", field="distinguishing", cat=CAT):
    return marks.read({field: text}, ident, cat)


def found(text, **more):
    return [(m["kind"], m["place"], m.get("side")) for m in read(text, **more)["marks"]]


# ---- what the sheet names ------------------------------------------------------------------------------------------------------------------------
def test_each_kind_of_mark_is_found_with_the_place_the_sheet_gives_it():
    assert found("freckles across the nose") == [("freckles", "nose", None)]
    assert found("a birthmark on the neck")[0][:2] == ("birthmark", "neck")
    assert found("a beauty mark above the lip")[0:1] == [] and read("a beauty mark above the lip")["skipped"] == ["a beauty mark on the lip"]       # (the Designer has no place for a lip: said, not moved to a cheek)
    assert found("a beauty mark on the cheek")[0][:2] == ("beauty_mark", "cheeks")
    assert found("a jagged scar across the brow")[0][:2] == ("scar", "forehead")
    assert found("scars across his back")[0][:2] == ("scar", "back")
    assert found("a dog bite scar on one calf")[0][:2] == ("scar", "legs")
    assert found("a small mole on the cheek")[0][:2] == ("mole", "cheeks")


def test_a_sentence_with_two_marks_gives_each_its_own_place():
    assert found("a scar across the brow and a small mole on the cheek")[0][:2] == ("scar", "forehead")
    assert [m[:2] for m in found("a scar across the brow and a small mole on the cheek")] == [("scar", "forehead"), ("mole", "cheeks")]
    assert [m[:2] for m in found("a cheek scar and a neck birthmark")] == [("scar", "cheeks"), ("birthmark", "neck")]          # (a place right before a mark word belongs to it)
    assert [m[:2] for m in found("freckles on the nose, cheeks and shoulders, a faded scar on the left forearm")] == [("freckles", "nose"), ("freckles", "cheeks"), ("freckles", "shoulders"), ("scar", "arms")]
    assert [m[:2] for m in found("a mole on the cheek. freckles across the nose")] == [("mole", "cheeks"), ("freckles", "nose")]       # (a full stop ends a reading)


def test_the_words_before_a_mark_change_its_size_and_strength_and_the_designers_range_has_the_last_word():
    plain, small, tiny = read("a mole on the cheek")["marks"][0], read("a small mole on the cheek")["marks"][0], read("a tiny mole on the cheek")["marks"][0]
    assert (plain["size"], small["size"], tiny["size"]) == (5.0, 3.5, 2.5) and plain["strength"] == small["strength"] == 1.0
    assert read("faint freckles on the nose")["marks"][0]["strength"] == 0.45 and read("heavy freckles on the nose")["marks"][0]["strength"] == 0.91
    assert read("a long scar on the cheek")["marks"][0]["size"] == 50.4 and read("a huge scar on the cheek")["marks"][0]["size"] == 64.8
    narrow = {**CAT, "kinds": [{"id": "mole", "size_mm": [6, 8]}, {"id": "scar", "size_mm": [5, 40]}]}
    assert read("a tiny mole on the cheek", cat=narrow)["marks"][0]["size"] == 6.0 and read("a huge scar on the cheek", cat=narrow)["marks"][0]["size"] == 40.0       # (clipped into the range)
    assert read("freckles across the nose", cat=narrow)["marks"] == [] and read("freckles across the nose", cat=narrow)["skipped"] == ["freckles across the nose"]      # (a kind the Designer has no longer: said)


def test_vitiligo_and_freckles_with_no_place_go_where_such_skin_has_them_and_the_card_is_told_it_was_assumed():
    got = read("vitiligo")
    assert [(m["kind"], m["place"]) for m in got["marks"]] == [("vitiligo", "hands"), ("vitiligo", "cheeks"), ("vitiligo", "arms")] and got["assumed"] == ["vitiligo"]
    assert [m["size"] for m in got["marks"]] == [30.0, 34.0, 42.0] and got["marks"][0].get("side") is None and got["marks"][1]["side"] in ("left", "right") and got["marks"][2]["side"] in ("left", "right")
    assert [(m["kind"], m["place"], m.get("side")) for m in read("sun-weathered, freckled skin")["marks"]] == [("freckles", "nose", None), ("freckles", "cheeks", None)]
    assert read("sun-weathered, freckled skin")["assumed"] == ["freckles"]
    assert read("freckles across the nose")["assumed"] == [] and read("vitiligo on the hands")["assumed"] == [] and [m["place"] for m in read("vitiligo on the hands")["marks"]] == ["hands"]


def test_a_scar_a_mole_or_a_birthmark_that_names_no_place_is_left_out_and_said_not_put_on_a_cheek():
    for text, kind in [("a scar", "a scar"), ("a mole", "a mole"), ("a birthmark", "a birthmark"), ("a beauty mark", "a beauty mark")]:
        got = read(text)
        assert got["marks"] == [] and got["skipped"] == [f"{kind} (no place named)"] and got["assumed"] == []
    assert read("forearms roped with muscle and scarred by hoops, a leather apron")["skipped"] == ["a scar (no place named)"]


def test_what_the_designer_does_not_paint_is_said_and_never_guessed():
    for text in ("a tattoo on the forearm", "dimples", "a faded anchor tattoo on the forearm", "burns across one side of the face", "bioluminescent spots along the neck", "acne"):
        got = read(text)
        assert got["marks"] == [] and len(got["skipped"]) >= 1, text
    assert read("a faded anchor tattoo on the forearm")["skipped"] == ["tattoo on the forearm"]
    assert read("a burn scar on the right hand") == {"marks": [], "skipped": ["a burn scar on the right hand"], "assumed": [], "dropped": 0}               # (a burn scar is a burn)
    mixed = read("a tattoo on the forearm and a scar on the cheek")
    assert [(m["kind"], m["place"]) for m in mixed["marks"]] == [("scar", "cheeks")] and mixed["skipped"] == ["a tattoo on the forearm"]
    assert read("a gap between the front teeth") == {"marks": [], "skipped": [], "assumed": [], "dropped": 0}


def test_a_place_the_designer_has_no_equivalent_for_is_left_out_and_the_rest_of_the_sentence_is_kept():
    chin = read("a scar on the chin")
    assert chin["marks"] == [] and chin["skipped"] == ["a scar on the chin"]
    partly = read("freckles across the nose and chin")
    assert [(m["kind"], m["place"]) for m in partly["marks"]] == [("freckles", "nose")] and partly["skipped"] == ["freckles on the chin"]
    assert read("a white scar running from brow to jaw")["skipped"] == ["a scar on the jaw"] and found("a white scar running from brow to jaw")[0][:2] == ("scar", "forehead")


# ---- sides -----------------------------------------------------------------------------------------------------------------------------------------
def test_left_and_right_are_the_characters_own_and_a_pair_without_a_word_is_both_or_one_by_the_words():
    assert found("a scar on the left cheek")[0] == ("scar", "cheeks", "left") and found("a scar on the right cheek")[0] == ("scar", "cheeks", "right")
    assert found("freckles on both cheeks")[0] == ("freckles", "cheeks", None) and found("freckles on the cheeks")[0] == ("freckles", "cheeks", None)         # (both is what the Designer does without a side)
    assert found("scarred knuckles")[0] == ("scar", "hands", None)
    one = read("a dueling scar on one cheek")["marks"][0]
    assert one["side"] in ("left", "right") and read("a small mole on the cheek")["marks"][0]["side"] in ("left", "right")                                        # (a single mole is on one cheek)
    assert read("a small scar through one eyebrow")["marks"][0]["side"] in ("left", "right") and read("a birthmark on the neck")["marks"][0].get("side") is None
    assert read("freckles across the nose")["marks"][0].get("side") is None                                                                                      # (the nose is in the middle)


def test_the_same_character_always_gets_the_same_marks_and_another_gets_other_freckles_and_the_other_cheek_sometimes():
    a, b = read("a dueling scar on one cheek", ident="ilsa"), read("a dueling scar on one cheek", ident="ilsa")
    assert a == b
    seeds = {read("freckles across the nose", ident=name)["marks"][0]["seed"] for name in ("ilsa", "otto", "mara", "kell", "wren")}
    assert len(seeds) == 5 and all(0 <= s <= 2147483647 for s in seeds)
    sides = {read("a small mole on the cheek", ident=f"npc-{i}")["marks"][0]["side"] for i in range(12)}
    assert sides == {"left", "right"}
    assert [m["seed"] for m in read("a mole on the cheek and a mole on the neck")["marks"]][0] != [m["seed"] for m in read("a mole on the cheek and a mole on the neck")["marks"]][1]


def test_a_scar_is_pale_or_dark_as_the_words_say_by_the_parity_the_designer_reads_its_seed_with():
    fresh = [read(f"a fresh livid scar down the left cheek", ident=f"n{i}")["marks"][0]["seed"] for i in range(6)]
    old = [read(f"an old white scar on the left cheek", ident=f"n{i}")["marks"][0]["seed"] for i in range(6)]
    assert all(s % 2 == 1 for s in fresh) and all(s % 2 == 0 for s in old)
    plain = {read("a scar on the left cheek", ident=f"n{i}")["marks"][0]["seed"] % 2 for i in range(12)}
    assert plain == {0, 1}                                                                                                                                  # (no word: the seed decides, as it would)
    assert read("a scar on the right cheek")["marks"][0]["direction"] == 325.0 and read("a scar on the left cheek")["marks"][0]["direction"] == 35.0       # (mirrored)
    assert read("a scar across the cheek")["marks"][0]["direction"] == 60.0 and read("a scar across the brow")["marks"][0]["direction"] == 90.0
    assert "direction" not in read("a mole on the left cheek")["marks"][0]


# ---- a long description ----------------------------------------------------------------------------------------------------------------------------
def test_a_long_description_is_not_read_as_a_list_of_places():
    text = "a wind-tanned face and a freckled nose, a long dark brown plait over one shoulder, forearms roped with muscle, scarred by hoops, a leather apron over a grey shirt, large cracked hands"
    got = read(text, field="extra")
    assert [(m["kind"], m["place"]) for m in got["marks"]] == [("freckles", "nose")] and got["skipped"] == ["a scar (no place named)"]
    assert found("a freckled face, a bright steady stare, a leather cap pulled low over one ear, boots", field="extra") == [("freckles", "face", None)]
    assert found("a scarred jaw, large cracked hands, standing in a relaxed pose") == []                                                                         # (the hands are not the scar's)
    assert read("a scarred jaw, large cracked hands, standing in a relaxed pose")["skipped"] == ["a scar on the jaw"]
    assert found("a scar on the back of his hand")[0][:2] == ("scar", "hands")                                                                                     # (not the back)
    assert found("a freckled wind burned face with a determined jaw", field="extra")[0][:2] == ("freckles", "face")                                              # (wind burn is weather)


def test_the_extra_field_is_read_too_and_the_same_mark_in_both_fields_is_one_mark():
    ap = {"distinguishing": "freckles across the nose", "extra": "a scar on the left cheek, freckles across the nose"}
    got = marks.read(ap, "ilsa", CAT)
    assert [(m["kind"], m["place"], m.get("side")) for m in got["marks"]] == [("freckles", "nose", None), ("scar", "cheeks", "left")]
    assert marks.read({"skin_tone": "freckled"}, "ilsa", CAT)["marks"] == [] and marks.read(None, "ilsa", CAT)["marks"] == [] and marks.read({"distinguishing": None}, "x", CAT)["skipped"] == []


def test_at_most_the_designers_limit_of_marks_is_made_and_the_rest_is_counted():
    text = ", ".join(f"a mole on the {place}" for place in ("left cheek", "right cheek", "neck", "chest", "back", "nose", "forehead", "face", "left shoulder", "right shoulder",
                                                         "left arm", "right arm", "left hand", "right hand", "left leg", "right leg", "left knee", "right knee"))
    got = read(text)
    assert len(got["marks"]) == 16 and got["dropped"] == 0                                                                                                       # (the left and right knee are the same mark as the legs)
    more = read(text + ", a scar on the neck, a scar on the nose")
    assert len(more["marks"]) == 16 and more["dropped"] == 2 and [m["kind"] for m in more["marks"]].count("scar") == 0                                            # (the first sixteen the sheet names are the ones made)
    small = {**CAT, "maximum_marks": 3}
    assert len(read(text, cat=small)["marks"]) == 3 and read(text, cat=small)["dropped"] > 10


@needs_marks
def test_every_mark_it_makes_is_one_the_designer_accepts():
    from bodydesigner import skin_marks
    texts = ["vitiligo", "freckles across the nose", "a dueling scar on one cheek", "a small mole on the cheek", "a birthmark on the neck", "a tiny beauty mark on the left cheek",
             "heavy freckles on the nose, cheeks and shoulders, a faded scar on the left forearm", "a huge scar across the back", "a jagged scar along the hairline", "scarred knuckles"]
    for text in texts:
        mine = marks.read({"distinguishing": text}, "ilsa")
        assert mine["marks"], text
        assert skin_marks.clean(mine["marks"]) == [{**m, "size": float(m["size"]), "strength": float(m["strength"]), **({"direction": float(m["direction"])} if "direction" in m else {})} for m in mine["marks"]]


def test_whatever_the_words_every_mark_it_makes_is_inside_the_designers_ranges():
    import random
    table = marks.data()
    vocab = ([w for names in table["kinds"].values() for n in names for w in n.split()] + table["unmade"] + list(table["places"]) + table["no_place"] + list(table["modifiers"])
             + ["left", "right", "both", "one", "each", "and", "with", "a", "the", "his", "her", "across", "on", "of", "back", "from", "to", ",", ",", ".", ";", "running", "leather", "scarred", "freckled"])
    ranges = {k["id"]: k["size_mm"] for k in CAT["kinds"]}
    rng = random.Random(7)
    real = None
    if marks.offered():
        from bodydesigner import skin_marks as real
    for i in range(400):
        text = " ".join(rng.choice(vocab) for _ in range(rng.randint(1, 40)))
        got = marks.read({"distinguishing": text[:rng.randint(10, 300)], "extra": text}, f"id{i}", CAT)
        assert len(got["marks"]) <= 16 and len(set((m["kind"], m["place"], m.get("side")) for m in got["marks"])) == len(got["marks"])
        for mark in got["marks"]:
            low, high = ranges[mark["kind"]]
            assert low <= mark["size"] <= high and 0 <= mark["strength"] <= 1 and 0 <= mark["seed"] <= 2147483647 and mark["place"] in PLACES
            assert mark.get("side", "left") in ("left", "right") and ("direction" in mark) == (mark["kind"] == "scar") and 0 <= mark.get("direction", 0) <= 360
        if real is not None:
            real.clean(got["marks"])                                                                  # (the Designer takes every one)
        marks.describe(got["marks"])


# ---- saying it --------------------------------------------------------------------------------------------------------------------------------------
def test_the_marks_are_said_in_words_for_the_card():
    assert marks.describe([]) == "" and marks.describe(None) == ""
    got = read("freckles on the nose, cheeks and shoulders, a faded scar on the left forearm, a mole on the right cheek and a mole on the neck, a birthmark on the neck")
    assert marks.describe(got["marks"]) == "freckles on the nose, both cheeks and both shoulders; a scar on the left arm; moles on the right cheek and the neck; a birthmark on the neck"
    assert marks.describe(read("vitiligo")["marks"]).startswith("vitiligo on both hands, the ") and marks.describe(read("a scar across the brow")["marks"]) == "a scar on the forehead"
    assert marks.describe([{"kind": "scar", "place": "neck", "side": "right"}]) == "a scar on the right side of the neck"


def test_the_card_gets_nothing_from_a_body_designer_that_paints_no_marks(monkeypatch):
    package = sys.modules.get("bodydesigner")
    if package is not None:                                                      # (one that already has the module loaded: take it off the package too)
        monkeypatch.delattr(package, "skin_marks", raising=False)
    monkeypatch.setitem(sys.modules, "bodydesigner.skin_marks", None)            # (an import that fails)
    assert marks.catalogue() == {} and marks.offered() is False and marks.summary({"distinguishing": "vitiligo"}, "ilsa") is None
    assert marks.read({"distinguishing": "vitiligo"}, "ilsa") == {"marks": [], "skipped": [], "assumed": [], "dropped": 0}


def test_the_summary_for_the_card_has_the_marks_the_words_and_what_was_left_out(monkeypatch):
    monkeypatch.setattr(marks, "catalogue", lambda: CAT)
    got = marks.summary({"distinguishing": "freckles across the nose, a faded anchor tattoo on the forearm"}, "ilsa")
    assert set(got) == {"marks", "skipped", "assumed", "dropped", "text"} and got["text"] == "freckles on the nose" and got["skipped"] == ["tattoo on the forearm"] and got["dropped"] == 0


def test_the_table_is_complete_for_the_kinds_and_places_the_designer_has():
    table = marks.data()
    assert set(table["kinds"]) == set(table["defaults"]) == {k["id"] for k in CAT["kinds"]} == set(marks.KIND_WORDS)
    assert set(table["places"].values()) <= set(PLACES) and set(table["scar"]["direction"]) == set(table["scar"]["across_direction"]) == set(PLACES)
    assert not set(table["places"]) & set(table["no_place"]) and not {w for names in table["kinds"].values() for n in names for w in n.split()} & set(table["places"])
    for kind, default in table["defaults"].items():
        assert default.get("place_required") or default["places"], kind
        for entry in default.get("places", []):
            assert entry["place"] in PLACES and entry.get("side", "one") in ("both", "one")
    assert set(marks.PAIRED) <= set(PLACES) and set(marks.PLACE_WORDS) == set(PLACES)
