"""npcgen/outfit.py: what a character's outfit text says about the Body Designer's two garments (which, in what colour, and what the sheet names that cannot be made), the
tables behind it, and the putting on of the garments: on real bodies, over their underwear, the plain file and the animated one alike, with a garment that does not fit left off."""
import io
import json
import re
import types

import numpy as np
import pytest
from PIL import Image

from npcgen import bodymatch, outfit, underwear
from npcgen.paint3d import Glb
from test_underwear import FEMALE, MALE, build, khronos, needs_designer


def kinds(text):
    """(top colour or 'plain', bottom colour or 'plain', left) for an outfit text; None for a garment it does not name."""
    found = outfit.read(text)
    return tuple(None if found[part] is None else (found[part]["colour"] or "plain") for part in ("top", "bottom")) + (found["left"],)


def cloth(word):
    return bodymatch.colour(word, "cloth", outfit.data()["cloth"])


# ---- reading an outfit -----------------------------------------------------------------------------------------------------------------------
def test_a_shirt_and_trousers_are_found_with_the_colour_that_goes_with_each():
    assert kinds("a white shirt and black trousers") == (cloth("white"), cloth("black"), [])
    assert kinds("a camel blazer over a white top, wide-leg trousers and heels") == (cloth("white"), "plain", [])       # a jacket shows the shirt: its colour is the shirt's (and the blazer is put on)
    assert kinds("a casual t-shirt, jeans and sneakers") == ("plain", outfit.data()["materials"]["jeans"], [])
    assert kinds("a black turtleneck, tailored trousers and heels with a statement necklace") == (cloth("black"), "plain", [])
    assert kinds("a clean white medical tunic with a glowing scanner on the wrist, gloves and soft boots") == (cloth("white"), None, [])      # no trousers named: none
    assert kinds("") == kinds(None) == kinds("a straw hat and boots") == (None, None, [])


def test_a_colour_that_is_named_wins_over_the_one_a_material_implies_and_a_modifier_changes_it():
    black, dark = cloth("black"), outfit.read("dark jeans")["bottom"]["colour"]
    assert kinds("black jeans")[1] == black != outfit.data()["materials"]["jeans"]                         # 'black' is said; 'jeans' only implies blue
    assert dark != outfit.data()["materials"]["jeans"]                                                      # 'dark' darkens what the material implies
    assert kinds("canvas trousers")[1] == outfit.data()["materials"]["canvas"] and kinds("a linen shirt")[0] == outfit.data()["materials"]["linen"]
    assert kinds("a faded blue shirt")[0] != cloth("blue") and kinds("a dark green shirt")[0] != cloth("green")
    assert kinds("a navy blue shirt")[0] == cloth("navy blue") == cloth("navy")                           # (a colour of two words)
    assert kinds("a shirt")[0] == "plain"                                                                    # nothing said: the Body Designer's own colour is used
    assert kinds("a leather vest over a white shirt")[0] == cloth("white") and kinds("a blue vest over a linen shirt")[0] == cloth("blue")     # (a colour said, wherever it is, beats a material's)


def test_a_vest_covers_the_chest_and_a_jacket_shows_the_shirt_and_stands_in_only_when_there_is_none():
    assert kinds("a black vest over a white shirt, black jeans")[0] == cloth("black")                      # the vest is what is seen
    assert kinds("a rumpled trench coat over a white shirt and loosened tie, dark trousers")[0] == cloth("white")
    assert kinds("a mustard cardigan over a striped top, dark jeans")[0] == cloth("mustard")               # the shirt has no colour: the cardigan's is the one said
    assert kinds("a weathered coat with a chart tube slung across the back and canvas trousers") == ("plain", outfit.data()["materials"]["canvas"], ["coat"])
    assert kinds("a hooded cloak over a plain shirt, canvas trousers")[0] == "plain"                       # a cloak is never a stand-in, and never a colour
    assert kinds("a hooded green-brown cloak and boots") == (None, None, ["cloak"])
    assert kinds("a camel coat and trousers")[0] == cloth("camel")                                          # a coat alone: it stands in for the shirt


def test_a_uniform_is_a_shirt_and_trousers_unless_a_skirt_or_a_dress_says_otherwise():
    navy = cloth("navy")
    assert kinds("navy coveralls with grease stains and black work boots") == (navy, navy, [])
    assert kinds("a dark gray uniform with a badge, a baton at the belt and heavy boots")[:2] == (outfit.read("a dark gray uniform")["top"]["colour"],) * 2
    assert kinds("torn overalls over a filthy shirt, one strap hanging loose and mud-caked boots")[:2] == ("plain", "plain")
    assert kinds("a navy uniform and a long skirt") == (navy, None, [])                                        # a skirt covers the legs: the uniform puts no trousers there (and the skirt is made)
    assert kinds("a navy uniform and a tartan kilt") == (navy, None, ["kilt"])                                 # (a kilt is not a skirt: said)
    assert kinds("a tailored dark skirt suit with a blouse and pumps") == ("plain", None, [])               # the suit has a skirt: no trousers
    assert kinds("a suit of plate armor and a heavy axe")[:2] == (None, None) and kinds("a business suit")[:2] == ("plain", "plain")
    assert kinds("a rumpled khaki uniform shirt, a flashlight on the belt, dark trousers and heavy boots")[:2] == (cloth("khaki"), "plain")    # 'uniform shirt' is a shirt, not a whole outfit


def test_what_the_body_designer_cannot_make_is_said_never_guessed():
    assert kinds("a satin midi dress with a tailored blazer and heels") == (None, None, [])                  # a dress (made): no stand-in shirt from the blazer (which is put on, over what is there)
    assert kinds("a satin midi dress with a tailored coat and heels") == (None, None, ["coat"])               # (a coat is longer than the jacket: said)
    assert kinds("a fitted rash-guard top and a leotard") == ("plain", None, ["leotard"])                     # (a leotard, a swimsuit, a bikini, a robe, a toga, a kimono and a cassock are not that dress: said)
    assert kinds("a laced bodice over a white blouse, a long skirt and an apron, and leather shoes") == (cloth("white"), None, ["bodice"])        # (the skirt and the apron are made)
    assert kinds("a fitted rash-guard top, board shorts and flip-flops") == ("plain", None, ["shorts"])
    assert kinds("bone-and-leather armour with fur shoulder pads, a heavy axe and leather arm wraps") == (None, None, ["armour"])
    assert kinds("a fur-trimmed cloak over fine layered wool robes") == (None, None, ["cloak", "robe"])      # (a plural is the word)
    assert kinds("two shirts, patched trousers")[:2] == ("plain", "plain")


def test_words_that_only_look_like_garments_are_not_and_a_word_counts_once():
    assert kinds("a red ringmaster's tailcoat with gold braid, a top hat with gears, white gloves") == (cloth("red"), None, ["tailcoat"])      # 'top hat' is no top
    assert kinds("a tank top and a flight suit")[:2] == ("plain", "plain")                                  # 'flight suit' is one thing, 'tank top' is one thing
    assert kinds("a top hat and a suit of armor") == (None, None, ["armor"])


def test_a_phrase_with_a_dress_word_in_it_that_names_something_else_is_read_as_what_it_is():
    assert kinds("black dress shoes and a white dress shirt with dark dress pants") == (cloth("white"), "plain", [])                 # (shoes, a shirt, trousers: no dress)
    assert kinds("a navy dress uniform with brass buttons") == (cloth("navy"), cloth("navy"), [])                 # a uniform: a shirt and trousers in its colour
    assert kinds("a stained surgical gown over green scrubs") == (None, None, ["robe"])                           # (a robe: said, not made, and it blocks a stand-in as a dress does)
    assert kinds("a worn dressing gown and slippers") == (None, None, ["robe"])
    assert kinds("an embroidered dress with long sleeves") == (None, None, []) and kinds("a sequined gown") == (None, None, [])      # (a dress and a gown are made)
    assert "dress" not in outfit.read("a pair of dress shoes")["left"] and outfit.read("a pair of dress shoes") == {"top": None, "bottom": None, "over": [], "instead": [], "left": []}
    assert all(outfit.data()["rename"][k] for k in outfit.data()["rename"]) and not set(outfit.data()["rename"]) & set(outfit.data()["ignore"])


def test_what_is_put_on_follows_the_choice_and_only_a_choice_that_exists():
    sheet = {"outfit": "a cream blouse, a tartan kilt and a long coat"}                                    # (a kilt and a coat are not made: said)
    none = {"pieces": [], "left": [], "why": {}, "problem": ""}
    assert outfit.pieces("none", sheet) == none
    assert outfit.pieces("sheet", sheet) == {"pieces": [{"kind": "shirt", "colour": cloth("cream")}], "left": ["kilt", "coat"], "why": {}, "problem": ""}
    assert outfit.pieces("basic", sheet) == {"pieces": [{"kind": "shirt", "colour": cloth("cream")}, {"kind": "trousers"}], "left": ["kilt", "coat"], "why": {}, "problem": ""}
    assert outfit.pieces("basic", {}) == {"pieces": [{"kind": "shirt"}, {"kind": "trousers"}], "left": [], "why": {}, "problem": ""} and outfit.pieces("sheet", None) == none
    assert outfit.pieces("basic", {"outfit": 3}) == {"pieces": [{"kind": "shirt"}, {"kind": "trousers"}], "left": [], "why": {}, "problem": ""}
    with pytest.raises(ValueError, match="clothes must be basic, sheet, none"):
        outfit.pieces("all", sheet)


def test_the_tables_are_well_formed_and_no_word_is_two_kinds_of_garment():
    table = outfit.data()
    seen = {}
    for cls in outfit.CLASSES:
        for word in table[cls]:
            assert word == word.lower() and word == " ".join(word.split()) and "-" not in word, (cls, word)
            assert word not in seen, f"{word!r} is both {seen[word]} and {cls}"
            seen[word] = cls
    for colour in list(table["cloth"]["colours"].values()) + list(table["cloth"]["phrases"].values()) + list(table["materials"].values()):
        assert re.fullmatch(r"#[0-9a-f]{6}", colour)
    for phrase in table["ignore"]:
        assert phrase == phrase.lower()
    made = [(kind, word) for kind, words in table["makes"].items() for word in words]
    assert {kind for kind, _ in made} <= set(outfit.NODES) and set(outfit.OUTER) <= {kind for kind, _ in made} and len({word for _, word in made}) == len(made)        # (a garment made, and each word of it once)
    assert all(seen[word] in outfit.MAKEABLE for _, word in made) and set(outfit.NODES) == set(outfit.ORDER) and set(outfit.OUTER) | set(outfit.BASE) <= set(outfit.NODES)
    assert all(kind not in outfit.BASE for kind, _ in made)                                                        # (a shirt and trousers are not "made" from a word: they are the outfit's start)
    for word, (lighter, more_saturated) in table["cloth"]["modifiers"].items():
        assert -0.3 <= lighter <= 0.3 and -0.3 <= more_saturated <= 0.3, word
    assert not set(table["cloth"]["colours"]) & set(table["materials"]), "a material that is also a colour word would never be read as a material"


# ---- the putting on --------------------------------------------------------------------------------------------------------------------------
def meshes(blob):
    return [m["name"] for m in Glb(blob).json["meshes"]]


def fabric_colour(blob, name):
    """The colour of a garment's fabric texture (it is one colour)."""
    glb = Glb(blob)
    doc = glb.json
    material = next(m for m in doc["materials"] if m["name"] == name)
    image = doc["images"][doc["textures"][material["pbrMetallicRoughness"]["baseColorTexture"]["index"]]["source"]]
    return tuple(int(v) for v in np.asarray(Image.open(io.BytesIO(glb.view_bytes(image["bufferView"]))).convert("RGB")).astype(float).reshape(-1, 3).mean(0))


@pytest.fixture(scope="module")
def dressed_bodies():
    """A female and a male body (the plain file stands in for the animated one: they have the same fitting signature), with their underwear on and then their clothes."""
    out = {}
    for sex, base in (("female", FEMALE), ("male", MALE)):
        plain = build(base)
        plain, _ = underwear.dress(plain, underwear.kinds_for(sex))
        out[sex] = (plain, outfit.wear(plain, plain, [{"kind": "shirt", "colour": "#aa3322"}, {"kind": "trousers", "colour": "#2f5a3a"}]))
    return out


@needs_designer
def test_both_files_are_dressed_alike_over_the_underwear_in_the_colours_asked_for(dressed_bodies, tmp_path):
    for sex, (before, (plain, animated, report)) in dressed_bodies.items():
        assert plain == animated and meshes(plain) == meshes(before) + ["ForgeShirt", "ForgeTrousers"], sex          # appended after what was there: the body, the eyes, the underwear
        assert meshes(plain)[:3] == ["ForgeBody", "ForgeEye_l", "ForgeEye_r"] and "ForgeUnderwear" in meshes(plain)
        assert fabric_colour(plain, "ForgeShirtFabric") == (0xaa, 0x33, 0x22) and fabric_colour(plain, "ForgeTrousersFabric") == (0x2f, 0x5a, 0x3a)
        assert report["failed"] == {} and [w["kind"] for w in report["worn"]] == ["shirt", "trousers"]
        assert [w["colour"] for w in report["worn"]] == ["#aa3322", "#2f5a3a"] and all(w["triangles"] > 2000 and w["version"] in (2, 3) for w in report["worn"])
        path = tmp_path / f"{sex}.glb"
        path.write_bytes(plain)
        assert khronos(path) in (None, (0, 0, 0, 0)), sex                                                          # (the Khronos validator is silent where it is installed)
        assert Glb(before).json["skins"] == Glb(plain).json["skins"] and len(Glb(plain).json["animations"] if "animations" in Glb(plain).json else []) == 0


@needs_designer
def test_the_garments_stand_off_the_body_where_the_underwear_is_by_more_than_its_thickness(dressed_bodies):
    for sex, (before, (plain, _, report)) in dressed_bodies.items():                                              # (the Designer's own measure: the rule is 3 mm, the shell is 1.8 mm thick)
        assert all(w["over_underwear_mm"] is not None and w["over_underwear_mm"] >= 3.0 for w in report["worn"]), (sex, report["worn"])


@needs_designer
def test_a_garment_that_does_not_fit_is_left_off_and_said_and_the_other_is_kept(dressed_bodies, monkeypatch):
    from bodydesigner import garments
    plain = dressed_bodies["male"][0]
    real = garments.fit

    def fit(blob, recipe, params=None):
        if recipe["kind"] == "trousers":
            raise ValueError("garment.fit: the body is inside the garment")
        return real(blob, recipe, params)

    monkeypatch.setattr(garments, "fit", fit)
    out, animated, report = outfit.wear(plain, plain, [{"kind": "shirt"}, {"kind": "trousers"}])
    assert meshes(out)[-1] == "ForgeShirt" and "ForgeTrousers" not in meshes(out) and out == animated
    assert [w["kind"] for w in report["worn"]] == ["shirt"] and report["failed"] == {"trousers": "garment.fit: the body is inside the garment"}
    both, _, report = outfit.wear(plain, plain, [{"kind": "trousers"}])                                            # nothing fits: the body as it was
    assert both == plain and report == {"worn": [], "failed": {"trousers": "garment.fit: the body is inside the garment"}}

    def fit_asserts(blob, recipe, params=None):                                                                     # (the Designer's geometry can assert on a shape it was not made for)
        if recipe["kind"] == "shirt":
            raise AssertionError("counts.max() == 2")
        return real(blob, recipe, params)

    monkeypatch.setattr(garments, "fit", fit_asserts)
    out, _, report = outfit.wear(plain, plain, [{"kind": "shirt"}, {"kind": "trousers"}])
    assert meshes(out)[-1] == "ForgeTrousers" and "ForgeShirt" not in meshes(out) and report["failed"] == {"shirt": "AssertionError: counts.max() == 2"}


@needs_designer
def test_a_pair_that_cannot_be_put_on_together_is_tried_one_by_one(dressed_bodies, monkeypatch):
    from bodydesigner import garments
    plain = dressed_bodies["male"][0]
    real = garments.dress

    def dress(blob, fitted):
        if len(fitted) > 1:
            raise ValueError("garment.dress.budget: too many triangles")
        if b"ForgeTrousers" in fitted[0]:
            raise ValueError("garment.dress.underwear: refit this garment")
        return real(blob, fitted)

    monkeypatch.setattr(garments, "dress", dress)
    out, _, report = outfit.wear(plain, plain, [{"kind": "shirt"}, {"kind": "trousers"}])
    assert meshes(out)[-1] == "ForgeShirt" and "ForgeTrousers" not in meshes(out)
    assert [w["kind"] for w in report["worn"]] == ["shirt"] and report["failed"] == {"trousers": "garment.dress.underwear: refit this garment"}


def test_the_newest_shirt_the_body_designer_knows_is_the_one_made_and_the_catalogue_is_the_fallback(monkeypatch):
    catalogue = [{"id": "shirt", "recipe": {"version": 2, "kind": "shirt", "colour": "#496b73"}}, {"id": "trousers", "recipe": {"version": 2, "kind": "trousers", "colour": "#59606b"}}]

    def garments(versions):
        def clean(recipe):
            if recipe["version"] not in versions:
                raise ValueError("garment.recipe.version: expected one of " + str(versions))
            return dict(recipe)
        return types.SimpleNamespace(catalogue=lambda: catalogue, clean_recipe=clean)

    assert outfit._recipe(garments((1, 2)), "shirt", None) == {"version": 2, "kind": "shirt", "colour": "#496b73"}           # a Body Designer that does not make the combined shirt
    assert outfit._recipe(garments((1, 2, 3)), "shirt", "#112233") == {"version": 3, "kind": "shirt", "colour": "#112233"}   # one that does
    assert outfit._recipe(garments((1, 2, 3)), "trousers", None)["version"] == 2                                              # (the trousers have one version)


@needs_designer
def test_the_command_a_process_runs_writes_the_two_files_and_the_report_or_says_why_not(dressed_bodies, tmp_path, capsys):
    plain = dressed_bodies["female"][0]
    (tmp_path / "plain.glb").write_bytes(plain)
    (tmp_path / "pieces.json").write_text(json.dumps([{"kind": "trousers", "colour": "#2f5a3a"}]), "utf-8")
    args = ["--plain", str(tmp_path / "plain.glb"), "--animated", str(tmp_path / "plain.glb"), "--pieces", str(tmp_path / "pieces.json"),
            "--out-plain", str(tmp_path / "p.glb"), "--out-animated", str(tmp_path / "a.glb"), "--report", str(tmp_path / "r.json")]
    assert outfit.main(args) == 0
    report = json.loads((tmp_path / "r.json").read_text("utf-8"))
    assert [w["kind"] for w in report["worn"]] == ["trousers"] and meshes((tmp_path / "p.glb").read_bytes())[-1] == "ForgeTrousers"
    assert (tmp_path / "p.glb").read_bytes() == (tmp_path / "a.glb").read_bytes() and json.loads(capsys.readouterr().out) == report
    (tmp_path / "pieces.json").write_text("not json", "utf-8")
    with pytest.raises(SystemExit) as stopped:
        outfit.main(args)
    assert stopped.value.code == 2 and "error:" in capsys.readouterr().err                                         # the line the build reads as the reason
    (tmp_path / "pieces.json").write_text(json.dumps([{"colour": "#000000"}]), "utf-8")                             # a piece with no kind
    with pytest.raises(SystemExit):
        outfit.main(args)


# ---- a garment in place of the one a body wears (the Items tab's try-on) ------------------------------------------------------------------------------
@needs_designer
def test_a_garment_can_be_put_on_in_place_of_the_one_of_its_kind_the_body_wears_and_everything_else_stays(dressed_bodies, tmp_path):
    for sex, (underwear_only, (dressed, _, _)) in dressed_bodies.items():
        assert meshes(dressed)[-2:] == ["ForgeShirt", "ForgeTrousers"]
        out, none, report = outfit.wear(dressed, None, [{"kind": "shirt", "colour": "#2020a0", "ease_mm": 8.0, "length": 0.95, "texture": "weave"}], replace=True)
        assert none is None and report["failed"] == {} and report["replaced"] == ["ForgeShirt"] and [w["kind"] for w in report["worn"]] == ["shirt"]
        assert meshes(out) == meshes(dressed)[:-2] + ["ForgeTrousers", "ForgeShirt"], sex                                    # (the old shirt is gone, the trousers stay, the new shirt is added last)
        assert fabric_colour(out, "ForgeShirtFabric") != fabric_colour(dressed, "ForgeShirtFabric") and fabric_colour(out, "ForgeTrousersFabric") == fabric_colour(dressed, "ForgeTrousersFabric")
        assert all(abs(a - b) <= 6 for a, b in zip(fabric_colour(out, "ForgeShirtFabric"), (0x20, 0x20, 0xa0)))
        before, after = Glb(dressed), Glb(out)
        for name in meshes(dressed)[:-2] + ["ForgeTrousers"]:                                                               # what was kept is value for value what it was
            old = next(m for m in before.json["meshes"] if m["name"] == name)["primitives"][0]
            new = next(m for m in after.json["meshes"] if m["name"] == name)["primitives"][0]
            for key, accessor in old["attributes"].items():
                assert np.array_equal(before.accessor(accessor), after.accessor(new["attributes"][key])), (sex, name, key)
        assert before.json["skins"] == after.json["skins"] and len(after.json["nodes"]) == len(before.json["nodes"])
        path = tmp_path / f"{sex}.glb"
        path.write_bytes(out)
        assert khronos(path) in (None, (0, 0, 0, 0)), sex


@needs_designer
def test_the_fit_does_not_depend_on_what_the_body_already_wears(dressed_bodies):
    from bodydesigner import garments
    from npcgen import glbprune
    before, (dressed, _, _) = dressed_bodies["female"]
    recipe = outfit._recipe(garments, "shirt", "#2020a0", ease_mm=8.0)
    bare, _ = glbprune.remove_nodes(dressed, ["ForgeShirt", "ForgeTrousers"])
    assert garments.fit(bare, recipe)[0] == garments.fit(dressed, recipe)[0] == garments.fit(before, recipe)[0]           # (the Designer reads the body and its underwear, and nothing else)


@needs_designer
def test_one_file_is_dressed_as_two_are_and_a_body_with_no_file_is_refused(dressed_bodies):
    plain = dressed_bodies["male"][0]
    piece = [{"kind": "trousers", "colour": "#2f5a3a", "ease_mm": 6.0}]
    both = outfit.wear(plain, plain, piece)
    only_plain, nothing, report = outfit.wear(plain, None, piece)
    only_animated = outfit.wear(None, plain, piece)
    assert nothing is None and only_plain == both[0] and only_animated[0] is None and only_animated[1] == both[1]

    def untimed(found):                                                                                                   # (how long a fit took is the one thing that differs between two runs)
        return {**found, "worn": [{k: v for k, v in w.items() if k != "seconds"} for w in found["worn"]]}
    assert untimed(report) == untimed(both[2]) == untimed(only_animated[2]) and report["worn"][0]["seconds"] > 0
    assert "replaced" not in report                                                                                        # (it says what it replaced only when asked to replace)
    with pytest.raises(ValueError, match="no body"):
        outfit.wear(None, None, piece)


@needs_designer
def test_a_garment_that_does_not_fit_leaves_the_one_the_body_wears_and_a_pair_that_cannot_go_on_together_is_replaced_one_by_one(dressed_bodies, monkeypatch):
    from bodydesigner import garments
    dressed = dressed_bodies["female"][1][0]
    real_fit, real_dress = garments.fit, garments.dress

    def fit(blob, recipe, params=None):
        if recipe["kind"] == "shirt":
            raise ValueError("cut.volume: guide edge incidence reaches 4; expected a manifold panel with shared edges")
        return real_fit(blob, recipe, params)

    monkeypatch.setattr(garments, "fit", fit)
    out, _, report = outfit.wear(dressed, None, [{"kind": "shirt", "length": 1.1}], replace=True)
    assert out == dressed and report == {"worn": [], "failed": {"shirt": "cut.volume: guide edge incidence reaches 4; expected a manifold panel with shared edges"}, "replaced": []}
    out, _, report = outfit.wear(dressed, None, [{"kind": "shirt", "length": 1.1}, {"kind": "trousers", "colour": "#aa3322"}], replace=True)
    assert meshes(out)[-2:] == ["ForgeShirt", "ForgeTrousers"] and report["replaced"] == ["ForgeTrousers"] and list(report["failed"]) == ["shirt"]       # (the old shirt stays, the trousers are new)
    assert fabric_colour(out, "ForgeTrousersFabric") == (0xaa, 0x33, 0x22) and fabric_colour(out, "ForgeShirtFabric") == fabric_colour(dressed, "ForgeShirtFabric")
    monkeypatch.setattr(garments, "fit", real_fit)

    def dress(blob, fitted):
        if len(fitted) > 1:
            raise ValueError("garment.dress.budget: too many triangles")
        if b"ForgeTrousers" in fitted[0]:
            raise ValueError("garment.dress.underwear: refit this garment")
        return real_dress(blob, fitted)

    monkeypatch.setattr(garments, "dress", dress)
    out, _, report = outfit.wear(dressed, None, [{"kind": "shirt", "colour": "#2020a0"}, {"kind": "trousers", "colour": "#aa3322"}], replace=True)
    assert meshes(out) == meshes(dressed)[:-2] + ["ForgeTrousers", "ForgeShirt"] and report["replaced"] == ["ForgeShirt"] and report["failed"] == {"trousers": "garment.dress.underwear: refit this garment"}
    assert fabric_colour(out, "ForgeTrousersFabric") == fabric_colour(dressed, "ForgeTrousersFabric")                       # (the trousers it wore are still on it)


@needs_designer
def test_the_command_can_dress_one_file_and_replace_what_the_body_wears(dressed_bodies, tmp_path, capsys):
    dressed = dressed_bodies["female"][1][0]
    (tmp_path / "body.glb").write_bytes(dressed)
    (tmp_path / "pieces.json").write_text(json.dumps([{"kind": "trousers", "colour": "#2f5a3a", "ease_mm": 6, "length": 0.9, "texture": "plain"}]), "utf-8")
    args = ["--animated", str(tmp_path / "body.glb"), "--out-animated", str(tmp_path / "worn.glb"), "--pieces", str(tmp_path / "pieces.json"), "--replace", "--report", str(tmp_path / "r.json")]
    assert outfit.main(args) == 0
    report = json.loads((tmp_path / "r.json").read_text("utf-8"))
    assert report["replaced"] == ["ForgeTrousers"] and meshes((tmp_path / "worn.glb").read_bytes()).count("ForgeTrousers") == 1
    assert json.loads(capsys.readouterr().out) == report
    for bad in (["--pieces", str(tmp_path / "pieces.json"), "--report", str(tmp_path / "r.json")],                                                       # no body at all
                ["--plain", str(tmp_path / "body.glb"), "--pieces", str(tmp_path / "pieces.json"), "--report", str(tmp_path / "r.json")],               # a body with no place to put it
                ["--out-plain", str(tmp_path / "x.glb"), "--pieces", str(tmp_path / "pieces.json"), "--report", str(tmp_path / "r.json")]):
        with pytest.raises(SystemExit) as stopped:
            outfit.main(bad)
        assert stopped.value.code == 2


@needs_designer
def test_the_try_ons_process_fits_one_garment_to_one_file_in_place_of_the_one_it_wears(dressed_bodies):
    from npcgen import clothing, dressing
    dressed = dressed_bodies["male"][1][0]
    recipe = clothing.clean_recipe({"colour": "#aa3322", "ease_mm": 7.0, "length": 0.95, "texture": "weave"})
    worn, report = dressing.fit_process(dressed, [recipe])                                                                  # (the process the Items tab starts: python -m npcgen.outfit --replace)
    assert report["failed"] == {} and report["replaced"] == ["ForgeShirt"] and [(w["kind"], w["colour"], w["ease_mm"], w["length"], w["texture"]) for w in report["worn"]] == [("shirt", "#aa3322", 7.0, 0.95, "weave")]
    assert meshes(worn) == meshes(dressed)[:-2] + ["ForgeTrousers", "ForgeShirt"] and fabric_colour(worn, "ForgeTrousersFabric") == fabric_colour(dressed, "ForgeTrousersFabric")
    assert all(abs(a - b) <= 6 for a, b in zip(fabric_colour(worn, "ForgeShirtFabric"), (0xaa, 0x33, 0x22)))
    in_process = outfit.wear(dressed, None, [recipe], replace=True)[0]
    assert worn == in_process                                                                                                # (the same bytes as doing it here: the process only moves it)
    refused, report = dressing.fit_process(dressed, [clothing.clean_recipe({"length": 1.2})])                              # (a length the Designer refuses: nothing is taken off, and it says why)
    assert refused == dressed and report["worn"] == [] and "cut.volume" in report["failed"]["shirt"] and report["replaced"] == []


# ---- the jacket and the apron: what the words name, and what goes over what -------------------------------------------------------------------------
def test_a_jacket_and_an_apron_the_sheet_names_are_found_with_their_colours_and_are_neither_left_nor_a_stand_in():
    seen = outfit.read("a camel blazer over a white top, wide-leg trousers and heels")
    assert seen["over"] == [{"kind": "jacket", "word": "blazer", "colour": cloth("camel")}] and seen["left"] == []
    assert seen["top"] == {"colour": cloth("white"), "under": cloth("white"), "word": "top"}
    seen = outfit.read("a mustard cardigan over a striped top, dark jeans")
    assert seen["top"] == {"colour": cloth("mustard"), "under": None, "word": "top"}                      # (the cardigan stands in for the colour of the shirt, unless it is put on: then the shirt has none of its own)
    seen = outfit.read("a leather jacket and a canvas apron")
    assert seen["over"] == [{"kind": "jacket", "word": "jacket", "colour": outfit.data()["materials"]["leather"]}, {"kind": "apron", "word": "apron", "colour": outfit.data()["materials"]["canvas"]}]
    assert outfit.read("an apron")["over"] == [{"kind": "apron", "word": "apron", "colour": None}] and outfit.read("two aprons")["over"][0]["kind"] == "apron"
    assert [o["word"] for o in outfit.read("a jacket, a blazer and a cardigan")["over"]] == ["jacket"]       # (one of each kind: a body wears one jacket)
    seen = outfit.read("a tailored blazer dress and heels")
    assert seen["over"] == [] and seen["left"] == ["blazer"] and seen["instead"][0]["kind"] == "dress"       # (a blazer that is a dress is not a jacket)
    assert outfit.read("a fitted black dress under a leather jacket with ankle boots")["over"][0]["kind"] == "jacket"   # (but a jacket over a dress is)
    for word in ("coat", "hoodie", "tailcoat", "overcoat", "greatcoat", "raincoat", "parka", "windbreaker", "duster", "cloak", "cape", "shawl", "poncho", "scarf"):
        seen = outfit.read(f"a {word} and trousers")
        assert seen["over"] == [] and word in seen["left"], word                                            # (what is longer or is not a jacket: said, not made)


@needs_designer
def test_what_goes_over_what_is_the_body_designers_own_list_and_it_is_read_not_copied():
    from bodydesigner import garments
    entries = {e["id"]: e for e in garments.catalogue()}
    assert outfit.needs("shirt") == [] and outfit.needs("trousers") == []
    for kind in outfit.OUTER:
        assert outfit.needs(kind) == list(entries[kind]["tested_stacks"][0][:-1]) == ["shirt", "trousers"]
    assert outfit.offered() == ["shirt", "trousers", "skirt", "dress", "jacket", "apron"] and outfit.needs("jacket", {"jacket": {}}) is None      # (no stack listed: nothing is claimed)
    assert outfit.needs("skirt") == [] and outfit.worn_with("skirt") == ["shirt"] and outfit.takes_off("skirt") == ["trousers"]              # (a skirt is worn with a shirt, not over anything, and without trousers)
    assert outfit.needs("dress") == [] and outfit.worn_with("dress") == [] and outfit.takes_off("dress") == ["shirt", "trousers"]            # (a dress is worn on its own: it takes the shirt and the trousers off)
    assert outfit.worn_with("shirt") is None and outfit.takes_off("shirt") == outfit.takes_off("trousers") == outfit.takes_off("jacket") == outfit.takes_off("apron") == []
    assert outfit.needs("jacket", {"jacket": {"tested_stacks": [["shirt", "jacket"], ["shirt", "trousers", "jacket"]]}}) == ["shirt"]      # (the first stack that ends with it)


@needs_designer
def test_a_stack_the_body_designer_has_not_checked_is_refused_in_a_sentence():
    assert outfit.stack_problem(["shirt", "trousers", "jacket"]) is None and outfit.stack_problem(["trousers", "shirt", "apron"]) is None
    assert outfit.stack_problem(["shirt", "trousers"]) is None and outfit.stack_problem([]) is None and outfit.stack_problem(["shirt"]) is None
    assert outfit.stack_problem(["shirt", "jacket"]) == "The open jacket goes over a shirt and trousers only (nothing else has been checked with it), and this body wears no trousers"
    assert outfit.stack_problem(["trousers", "apron"]).startswith("The bib apron goes over") and outfit.stack_problem(["trousers", "apron"]).endswith("this body wears no shirt")
    assert outfit.stack_problem(["jacket"]).endswith("this body wears neither") and outfit.stack_problem(["apron"], who="outfit").endswith("this outfit has neither")
    assert outfit.stack_problem(["shirt", "trousers", "jacket", "apron"]) == "A body wears one outer garment: the open jacket or the bib apron, not both"
    assert outfit.stack_problem(["shirt", "jacket", "apron"], only=["jacket"]).startswith("A body wears one outer garment")
    assert outfit.stack_problem(["shirt", "jacket"], only=["shirt"]) is None                                 # (only the ones asked about are looked at)
    assert outfit.stack_problem(["shirt", "trousers", "jacket"], catalogue={"jacket": {"label": "Open jacket"}}).startswith("The Body Designer lists no garments the open jacket")


@needs_designer
def test_a_shirt_or_trousers_looser_than_the_body_designer_checked_under_a_jacket_or_an_apron_are_refused():
    assert outfit.margin_problem("jacket", {"shirt": 20, "trousers": 20.0}) is None and outfit.margin_problem("apron", {}) is None
    assert outfit.margin_problem("jacket", {"shirt": 21}) == "The open jacket has been checked over a shirt no looser than 20 mm, and this one is 21 mm"
    assert outfit.margin_problem("apron", {"trousers": 25.5}) == "The bib apron has been checked over trousers no looser than 20 mm, and this one is 25.5 mm"
    assert outfit.margin_problem("jacket", {"shirt": None, "trousers": "loose"}) is None and outfit.margin_problem("shirt", {"shirt": 99}) is None   # (what is not a number, or has no margin, is not judged)
    assert outfit.margin_problem("jacket", {"shirt": 11}, {"jacket": {"label": "Open jacket", "layer_margins": {"shirt_ease_mm": 10}}}).endswith("no looser than 10 mm, and this one is 11 mm")


def test_a_colour_for_a_garment_is_checked_and_a_garment_that_is_not_one_is_refused():
    assert outfit.clean_colours(None) == {} and outfit.clean_colours({}) == {} and outfit.clean_colours({"shirt": None, "apron": ""}) == {}
    assert outfit.clean_colours({"shirt": "#ABCDEF", "jacket": " #112233 "}) == {"shirt": "#abcdef", "jacket": "#112233"}
    for raw, message in (([], "must be an object"), ("red", "must be an object"), ({"hat": "#112233"}, r"colours.hat: not a garment \(one of shirt, trousers, skirt, dress, jacket, apron\)"),
                         ({"shirt": "red"}, "colours.shirt: the colour must be like #3f6ba8"), ({"shirt": "#abc"}, "colours.shirt"), ({"shirt": 5}, "colours.shirt"), ({"shirt": "#12345g"}, "colours.shirt")):
        with pytest.raises(ValueError, match=message):
            outfit.clean_colours(raw)


@needs_designer
def test_the_jacket_or_the_apron_is_put_on_over_the_shirt_and_trousers_only_and_one_of_them():
    sheet = {"outfit": "a camel blazer over a white top, wide-leg trousers and heels"}
    wanted = [{"kind": "shirt", "colour": cloth("white")}, {"kind": "trousers"}, {"kind": "jacket", "colour": cloth("camel")}]
    for mode in ("basic", "sheet"):
        assert outfit.pieces(mode, sheet) == {"pieces": wanted, "left": [], "why": {}, "problem": ""}
    only = outfit.pieces("sheet", {"outfit": "a leather jacket and canvas trousers"})                         # (no shirt named: the jacket has its colour and the shirt its own)
    assert only["pieces"] == [{"kind": "shirt"}, {"kind": "trousers", "colour": outfit.data()["materials"]["canvas"]}, {"kind": "jacket", "colour": outfit.data()["materials"]["leather"]}]
    kilt = {"outfit": "a cream blouse, a tartan kilt and a green cardigan"}                                 # no trousers named: `sheet` has nothing for the jacket to go over
    got = outfit.pieces("sheet", kilt)
    assert got["pieces"] == [{"kind": "shirt", "colour": cloth("cream")}] and got["left"] == ["kilt", "cardigan"] and list(got["why"]) == ["cardigan"]
    assert got["why"]["cardigan"] == "The open jacket goes over a shirt and trousers only (nothing else has been checked with it), and this outfit has no trousers" and got["problem"] == ""
    assert [p["kind"] for p in outfit.pieces("basic", kilt)["pieces"]] == ["shirt", "trousers", "jacket"] and outfit.pieces("basic", kilt)["left"] == ["kilt"]    # (`basic` has trousers)
    stand_in = outfit.pieces("sheet", {"outfit": "a leather jacket and boots"})                               # (a jacket that cannot go on is still the colour of the shirt, as it was)
    assert stand_in["pieces"] == [{"kind": "shirt", "colour": outfit.data()["materials"]["leather"]}] and stand_in["left"] == ["jacket"]
    both = {"outfit": "a leather apron over a linen shirt, canvas trousers and a brown jacket"}                 # (a body wears one outer garment: the first the sheet names)
    got = outfit.pieces("basic", both)
    assert [p["kind"] for p in got["pieces"]] == ["shirt", "trousers", "apron"] and got["pieces"][0] == {"kind": "shirt", "colour": outfit.data()["materials"]["linen"]}
    assert got["left"] == ["jacket"] and got["why"] == {"jacket": "A body wears one outer garment, and the apron came first"}
    assert outfit.pieces("basic", {"outfit": "a brown jacket and a leather apron over a linen shirt and canvas trousers"})["pieces"][-1] == {"kind": "jacket", "colour": cloth("brown")}


@needs_designer
def test_what_goes_over_them_can_be_chosen_and_a_colour_given_for_each_garment_and_what_cannot_be_done_is_a_problem_not_an_omission():
    sheet = {"outfit": "a camel blazer over a white top, wide-leg trousers and heels"}
    assert [p["kind"] for p in outfit.pieces("basic", sheet, outer="none")["pieces"]] == ["shirt", "trousers"] and outfit.pieces("basic", sheet, outer="none")["left"] == []
    assert outfit.pieces("basic", sheet, outer="apron")["pieces"][-1] == {"kind": "apron"} and outfit.pieces("basic", {}, outer="jacket")["pieces"][-1] == {"kind": "jacket"}
    assert outfit.pieces("basic", sheet, outer="jacket")["pieces"][-1] == {"kind": "jacket", "colour": cloth("camel")}      # (a jacket asked for is in the sheet's colour for one)
    painted = outfit.pieces("basic", sheet, colours={"shirt": "#ABCDEF", "jacket": "#112233", "apron": "#000000"})["pieces"]
    assert painted == [{"kind": "shirt", "colour": "#abcdef"}, {"kind": "trousers"}, {"kind": "jacket", "colour": "#112233"}]      # (a colour for a garment not put on changes nothing)
    assert outfit.pieces("none", sheet) == {"pieces": [], "left": [], "why": {}, "problem": ""} and outfit.pieces("none", sheet, outer="none")["pieces"] == []
    assert outfit.pieces("none", sheet, outer="jacket")["problem"].endswith("this outfit has neither")
    assert outfit.pieces("sheet", {"outfit": "a jacket"}, outer="apron")["problem"].endswith("this outfit has no trousers")
    assert outfit.pieces("sheet", {"outfit": "a jacket"}, outer="apron")["pieces"] == [{"kind": "shirt"}]
    with pytest.raises(ValueError, match="outer must be sheet, none, jacket, apron"):
        outfit.pieces("basic", sheet, outer="coat")
    with pytest.raises(ValueError, match="colours.hat"):
        outfit.pieces("basic", sheet, colours={"hat": "#000000"})
    with pytest.raises(ValueError, match="colours.shirt"):
        outfit.pieces("basic", sheet, colours={"shirt": "red"})


# ---- the jacket and the apron put on ---------------------------------------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def outer_bodies(dressed_bodies):
    """The female body in its shirt and trousers with a jacket fitted over them, and the male one with an apron: only the outer garment is fitted (the fit reads the body and its underwear and nothing else)."""
    out = {}
    for sex, kind, colour in (("female", "jacket", "#634c3e"), ("male", "apron", "#c6b39a")):
        before = dressed_bodies[sex][1][0]
        out[sex] = (before, kind, colour, outfit.wear(before, None, [{"kind": kind, "colour": colour}], replace=True))
    return out


@needs_designer
def test_a_jacket_or_an_apron_goes_over_the_shirt_and_trousers_a_body_wears_and_they_stay_as_they_were(outer_bodies, tmp_path):
    for sex, (before, kind, colour, (worn, none, report)) in outer_bodies.items():
        node = outfit.NODES[kind]
        assert none is None and report["failed"] == {} and [w["kind"] for w in report["worn"]] == [kind] and report["replaced"] == [], sex
        assert meshes(worn) == meshes(before) + [node] and outfit.worn_kinds(worn) == ["shirt", "trousers", kind], sex
        assert all(abs(a - b) <= 6 for a, b in zip(fabric_colour(worn, node + "Fabric"), (int(colour[1:3], 16), int(colour[3:5], 16), int(colour[5:7], 16)))), sex
        assert fabric_colour(worn, "ForgeShirtFabric") == fabric_colour(before, "ForgeShirtFabric") and fabric_colour(worn, "ForgeTrousersFabric") == fabric_colour(before, "ForgeTrousersFabric")
        old, new = Glb(before), Glb(worn)
        for name in meshes(before):                                                                         # what was there is value for value what it was
            a = next(m for m in old.json["meshes"] if m["name"] == name)["primitives"][0]
            b = next(m for m in new.json["meshes"] if m["name"] == name)["primitives"][0]
            for key, accessor in a["attributes"].items():
                assert np.array_equal(old.accessor(accessor), new.accessor(b["attributes"][key])), (sex, name, key)
        assert old.json["skins"] == new.json["skins"] and report["worn"][0]["triangles"] > 2000 and report["worn"][0]["version"] == 2
        path = tmp_path / f"{sex}.glb"
        path.write_bytes(worn)
        assert khronos(path) in (None, (0, 0, 0, 0)), sex


@needs_designer
def test_an_apron_takes_the_jacket_off_and_a_second_outer_garment_is_never_kept(outer_bodies):
    before, _, _, (worn, _, _) = outer_bodies["female"]
    out, none, report = outfit.wear(worn, None, [{"kind": "apron", "colour": "#c6b39a"}], replace=True)
    assert none is None and report["failed"] == {} and report["replaced"] == ["ForgeJacket"] and outfit.worn_kinds(out) == ["shirt", "trousers", "apron"]
    assert meshes(out) == meshes(before) + ["ForgeApron"] and "ForgeJacket" not in meshes(out)           # (the same as the body in its shirt and trousers with the apron added)
    kept, _, refused = outfit.wear(worn, None, [{"kind": "apron"}])                                       # (without `replace` the body would wear two: refused)
    assert kept == worn and refused["worn"] == [] and refused["failed"]["apron"].startswith("A body wears one outer garment")
    with pytest.raises(ValueError, match="one outer garment"):
        outfit.wear(before, None, [{"kind": "jacket"}, {"kind": "apron"}])


@needs_designer
def test_a_jacket_is_not_put_on_a_body_without_the_shirt_and_trousers_it_was_checked_over(dressed_bodies):
    from npcgen import glbprune
    bare, dressed = dressed_bodies["male"][0], dressed_bodies["female"][1][0]
    out, none, report = outfit.wear(bare, None, [{"kind": "jacket"}], replace=True)                       # (nothing is fitted: it is refused before the half minute)
    assert out == bare and none is None and report["worn"] == [] and list(report["failed"]) == ["jacket"] and report["failed"]["jacket"].endswith("this body wears neither")
    no_trousers, _ = glbprune.remove_nodes(dressed, ["ForgeTrousers"])
    out, _, report = outfit.wear(no_trousers, None, [{"kind": "apron"}], replace=True)
    assert out == no_trousers and report["failed"] == {"apron": "The bib apron goes over a shirt and trousers only (nothing else has been checked with it), and this body wears no trousers"}
    assert report["replaced"] == []


@needs_designer
def test_the_garments_are_fitted_and_put_on_from_the_body_outward_whatever_order_they_are_asked_for_in(dressed_bodies, monkeypatch):
    from bodydesigner import garments
    plain = dressed_bodies["male"][0]
    order = []

    def fit(blob, recipe, params=None):
        order.append(recipe["kind"])
        return recipe["kind"].encode(), {"triangles": 1}

    monkeypatch.setattr(garments, "fit", fit)
    monkeypatch.setattr(garments, "dress", lambda blob, fitted: blob + b"|" + b"|".join(fitted))
    out, _, report = outfit.wear(plain, None, [{"kind": "jacket"}, {"kind": "trousers"}, {"kind": "shirt"}])
    assert order == ["shirt", "trousers", "jacket"] and [w["kind"] for w in report["worn"]] == ["shirt", "trousers", "jacket"] and out == plain + b"|shirt|trousers|jacket"
    with pytest.raises(ValueError, match="not 'hat'"):
        outfit.wear(plain, None, [{"kind": "hat"}])


@needs_designer
def test_a_jacket_whose_shirt_or_trousers_did_not_fit_is_left_off_too_and_one_that_cannot_be_put_on_after_them_is_not_forced(dressed_bodies, monkeypatch):
    from bodydesigner import garments
    plain = dressed_bodies["male"][0]

    def fit(blob, recipe, params=None):
        if recipe["kind"] == "trousers":
            raise ValueError("garment.fit.clearance: the body is inside the garment")
        return recipe["kind"].encode(), {"triangles": 1}

    monkeypatch.setattr(garments, "fit", fit)
    monkeypatch.setattr(garments, "dress", lambda blob, fitted: blob + b"|" + b"|".join(fitted))
    out, _, report = outfit.wear(plain, None, [{"kind": "shirt"}, {"kind": "trousers"}, {"kind": "jacket"}])
    assert out == plain + b"|shirt" and [w["kind"] for w in report["worn"]] == ["shirt"]
    assert report["failed"]["trousers"] == "garment.fit.clearance: the body is inside the garment" and report["failed"]["jacket"].endswith("this body wears no trousers")
    calls = []

    def dress(blob, fitted):                                                                               # (all three together do not go on: one by one, in order, and the jacket is judged on what is on by then)
        calls.append(len(fitted))
        if len(fitted) > 1 or fitted[0] == b"trousers":
            raise ValueError("garment.dress.underwear: refit this garment")
        return blob + b"|" + b"|".join(fitted)

    monkeypatch.setattr(garments, "fit", lambda blob, recipe, params=None: (recipe["kind"].encode(), {"triangles": 1}))
    monkeypatch.setattr(garments, "dress", dress)
    out, _, report = outfit.wear(plain, None, [{"kind": "shirt"}, {"kind": "trousers"}, {"kind": "jacket"}])
    assert [w["kind"] for w in report["worn"]] == ["shirt"] and out == plain + b"|shirt" and calls == [3, 1, 1]
    assert report["failed"]["trousers"] == "garment.dress.underwear: refit this garment" and report["failed"]["jacket"].endswith("this body wears no trousers")


@needs_designer
def test_a_shirt_looser_than_the_jacket_it_goes_under_was_checked_with_is_refused_before_it_is_fitted(outer_bodies, monkeypatch):
    _, _, _, (jacketed, _, _) = outer_bodies["female"]
    real = outfit._catalogue()
    monkeypatch.setattr(outfit, "_catalogue", lambda: {k: ({**v, "layer_margins": {"shirt_ease_mm": 10, "trousers_ease_mm": 10}} if k == "jacket" else v) for k, v in real.items()})
    out, _, report = outfit.wear(jacketed, None, [{"kind": "shirt", "ease_mm": 12.0}], replace=True)
    assert out == jacketed and report["worn"] == [] and report["replaced"] == []
    assert report["failed"] == {"shirt": "The open jacket has been checked over a shirt no looser than 10 mm, and this one is 12 mm"}


@needs_designer
def test_new_colours_go_onto_the_garments_of_a_real_body_and_nothing_else_of_it_changes(outer_bodies, tmp_path):
    from npcgen import bodymaker
    from npcgen.library import Library
    _, _, _, (worn, _, _) = outer_bodies["female"]                                                       # (shirt #aa3322, trousers #2f5a3a, jacket #634c3e)
    lib = Library(tmp_path / "library")
    npc = lib.create({"setting": "modern", "style": "pixel", "seed": 1, "profile": {"first_name": "Real", "last_name": "Body", "age": 30, "gender": "female", "occupation": "tester"}, "appearance": {}})
    for rel in ("model_3d.glb", "model_3d_rigged.glb"):
        lib.write_asset(npc["id"], rel, worn)
    clothes = [{"kind": "shirt", "colour": "#aa3322", "version": 3, "texture": "plain"}, {"kind": "trousers", "colour": "#2f5a3a", "version": 2, "texture": "weave"},
               {"kind": "jacket", "colour": "#634c3e", "version": 2, "texture": "plain"}]
    lib.update(npc["id"], lambda n: n.setdefault("assets", {}).update(
        model_3d={"file": "model_3d.glb", "created": "2026-10-10T10:00:00", "size": len(worn), "designed": {"base": "forge-anatomical-female-v7", "clothes": clothes}},
        rig_3d={"file": "model_3d_rigged.glb", "created": "2026-10-10T10:00:00", "size": len(worn), "joints_from": "designed body"}))
    done = bodymaker.recolour(None, lib, npc["id"], {"jacket": "#2020a0", "trousers": "#101010"})
    assert done["changed"] == {"jacket": "#2020a0", "trousers": "#101010"} and done["preview"] is False
    out = lib.read_asset(npc["id"], "model_3d.glb")
    assert out == lib.read_asset(npc["id"], "model_3d_rigged.glb") and meshes(out) == meshes(worn)
    assert all(abs(a - b) <= 6 for a, b in zip(fabric_colour(out, "ForgeJacketFabric"), (0x20, 0x20, 0xa0)))
    assert all(abs(a - b) <= 6 for a, b in zip(fabric_colour(out, "ForgeTrousersFabric"), (0x10, 0x10, 0x10)))
    assert fabric_colour(out, "ForgeShirtFabric") == fabric_colour(worn, "ForgeShirtFabric")             # (a garment that was not asked for keeps its cloth)
    old, new = Glb(worn), Glb(out)
    for name in meshes(worn):                                                                            # the shapes and the skin are value for value what they were
        a = next(m for m in old.json["meshes"] if m["name"] == name)["primitives"][0]
        b = next(m for m in new.json["meshes"] if m["name"] == name)["primitives"][0]
        for key, accessor in a["attributes"].items():
            assert np.array_equal(old.accessor(accessor), new.accessor(b["attributes"][key])), (name, key)
    assert old.json["skins"] == new.json["skins"] and old.json["nodes"] == new.json["nodes"]
    path = tmp_path / "recoloured.glb"
    path.write_bytes(out)
    assert khronos(path) in (None, (0, 0, 0, 0))
    assert [c["colour"] for c in lib.get(npc["id"])["assets"]["model_3d"]["designed"]["clothes"]] == ["#aa3322", "#101010", "#2020a0"]


# ---- the skirt: what the words name, what it is worn with, and the putting on ---------------------------------------------------------------------------
def test_a_skirt_the_sheet_names_is_found_and_a_kilt_shorts_a_sarong_a_loincloth_and_the_skirt_of_a_dress_are_not():
    seen = outfit.read("a white blouse, a long green skirt and a tan apron")
    assert seen["instead"] == [{"kind": "skirt", "word": "skirt", "colour": cloth("green")}] and seen["over"][0]["kind"] == "apron" and seen["left"] == [] and seen["bottom"] is None
    assert outfit.read("a red wool skirt")["instead"][0]["colour"] == cloth("red") and outfit.read("a skirt")["instead"] == [{"kind": "skirt", "word": "skirt", "colour": None}]
    assert len(outfit.read("a skirt and a skirt")["instead"]) == 1 and outfit.read("two skirts")["instead"][0]["word"] == "skirt"            # (one skirt: a body wears one)
    for word in ("kilt", "shorts", "sarong", "loincloth"):
        seen = outfit.read(f"a linen shirt and a {word}")
        assert seen["instead"] == [] and word in seen["left"], word                                         # (not a skirt: said, not made)
    seen = outfit.read("a ball gown with a full skirt")                                                      # the skirt of a dress is the dress's own (and there is nothing to say about it)
    assert seen["instead"] == [{"kind": "dress", "word": "gown", "colour": None}] and seen["left"] == []
    seen = outfit.read("a fur-trimmed robe over a full skirt")                                               # (a robe is not made: the skirt is still not a garment of its own, and both are said)
    assert seen["instead"] == [] and seen["left"] == ["robe", "skirt"]
    seen = outfit.read("a plaid skirt with leggings")                                                        # (the legs' garment the skirt takes the place of is named, to be said)
    assert seen["instead"][0]["kind"] == "skirt" and seen["bottom"]["word"] == "leggings"
    assert outfit.read("jeans and a denim skirt")["bottom"]["word"] == "jeans" and outfit.read("a white shirt")["bottom"] is None and outfit.read("coveralls")["bottom"]["word"] == "coveralls"
    assert outfit.read("a navy uniform and a long skirt")["bottom"] is None                                  # (a uniform puts no trousers where a skirt is)


@needs_designer
def test_a_skirt_takes_the_place_of_the_trousers_and_is_worn_with_the_shirt():
    sheet = {"outfit": "a white blouse, a long green skirt and sandals"}
    skirted = {"pieces": [{"kind": "shirt", "colour": cloth("white")}, {"kind": "skirt", "colour": cloth("green")}], "left": [], "why": {}, "problem": ""}
    for mode in ("sheet", "basic"):
        assert outfit.pieces(mode, sheet) == skirted, mode                                                    # (the default and the sheet's own: a sheet that says skirt gets one, not trousers)
    assert outfit.pieces("none", sheet)["pieces"] == []
    assert outfit.pieces("sheet", {"outfit": "a red skirt"})["pieces"] == [{"kind": "skirt", "colour": cloth("red")}]          # (no top named: the skirt alone, over the underwear)
    assert [p["kind"] for p in outfit.pieces("basic", {"outfit": "a red skirt"})["pieces"]] == ["shirt", "skirt"]
    both = outfit.pieces("sheet", {"outfit": "a blue top, canvas trousers and a long skirt"})                  # trousers and a skirt: the skirt is the garment seen, and the trousers are said
    assert [p["kind"] for p in both["pieces"]] == ["shirt", "skirt"] and both["left"] == ["trousers"]
    assert both["why"] == {"trousers": "A body in a skirt wears no trousers (nothing else has been checked with it)"}
    painted = outfit.pieces("basic", sheet, colours={"skirt": "#112233", "trousers": "#445566"})["pieces"]
    assert painted == [{"kind": "shirt", "colour": cloth("white")}, {"kind": "skirt", "colour": "#112233"}]          # (a colour for a garment not put on changes nothing)
    two = outfit.pieces("sheet", {"outfit": "a long skirt and a pleated skirt"})
    assert [p["kind"] for p in two["pieces"]] == ["skirt"] and two["left"] == []                               # (the same word: one skirt)
    # what goes over a skirt: nothing the Body Designer has tested, so it is left off, in words
    apron = outfit.pieces("sheet", {"outfit": "a white blouse, a green skirt and a tan apron"})
    assert [p["kind"] for p in apron["pieces"]] == ["shirt", "skirt"] and apron["left"] == ["apron"]
    assert apron["why"]["apron"] == "The bib apron goes over a shirt and trousers only (nothing else has been checked with it), and this outfit has a skirt"
    cardigan = outfit.pieces("basic", {"outfit": "a white blouse, a green skirt and a grey cardigan"})
    assert [p["kind"] for p in cardigan["pieces"]] == ["shirt", "skirt"] and cardigan["why"]["cardigan"].endswith("this outfit has a skirt")
    asked = outfit.pieces("basic", sheet, outer="apron")
    assert asked["problem"].endswith("this outfit has a skirt") and [p["kind"] for p in asked["pieces"]] == ["shirt", "skirt"]      # (asked for, and not possible: a problem, not an omission)


@needs_designer
def test_a_skirt_is_worn_with_a_shirt_and_not_with_trousers_a_jacket_or_an_apron_and_a_body_keeps_what_the_new_garment_goes_with():
    assert outfit.together("shirt", "trousers") and outfit.together("skirt", "shirt") and outfit.together("shirt", "jacket") and outfit.together("trousers", "apron")
    assert not any(outfit.together(a, b) for a, b in (("skirt", "trousers"), ("skirt", "jacket"), ("skirt", "apron"), ("jacket", "apron"), ("shirt", "shirt"), ("skirt", "skirt")))
    assert outfit.stack_problem(["shirt", "skirt"]) is None and outfit.stack_problem(["skirt"]) is None
    assert outfit.stack_problem(["shirt", "trousers", "skirt"], who="outfit") == "The knee-length skirt is worn with a shirt only (nothing else has been checked with it), and this outfit has trousers"
    assert outfit.stack_problem(["shirt", "trousers", "skirt"]).endswith("this body wears trousers")
    assert outfit.stack_problem(["shirt", "skirt", "jacket"], only=["jacket"]) == "The open jacket goes over a shirt and trousers only (nothing else has been checked with it), and this body wears a skirt"
    assert outfit.stack_problem(["shirt", "skirt", "apron"], only=["apron"], who="outfit").endswith("this outfit has a skirt") and outfit.stack_problem(["skirt", "apron"], only=["skirt"]).endswith("this body wears an apron")
    assert outfit.stack_problem(["shirt", "skirt"], catalogue={"skirt": {"label": "Skirt"}}).startswith("The Body Designer lists no garments the skirt has been checked with")
    assert outfit.kept({"shirt", "trousers"}, {"skirt"}) == {"shirt"} and outfit.kept({"shirt", "trousers", "jacket"}, {"skirt"}) == {"shirt"}      # (a skirt takes the trousers off, and the jacket it was never checked with)
    assert outfit.kept({"shirt", "skirt"}, {"trousers"}) == {"shirt"} and outfit.kept({"shirt", "skirt"}, {"shirt"}) == {"skirt"} and outfit.kept({"shirt", "trousers", "jacket"}, {"apron"}) == {"shirt", "trousers"}
    assert outfit.kept({"shirt", "skirt"}, {"jacket"}) == {"shirt", "skirt"} and outfit.kept({"shirt", "trousers"}, {"jacket"}) == {"shirt", "trousers"}      # (an outer garment takes nothing but the other outer garment off)
    assert outfit.kept({"shirt", "trousers", "apron"}, {"skirt", "shirt"}) == set() and outfit.kept(set(), {"skirt"}) == set()


def test_a_dress_the_body_designer_may_make_later_is_worn_alone_and_takes_everything_off_by_the_same_rules():
    cat = {"dress": {"label": "Dress", "tested_stacks": [["dress"]]}, "skirt": {"label": "Skirt", "tested_stacks": [["shirt", "skirt"]]},
           "jacket": {"label": "Open jacket", "tested_stacks": [["shirt", "trousers", "jacket"]]}}
    assert outfit.takes_off("dress", cat) == ["shirt", "trousers"] and outfit.worn_with("dress", cat) == [] and outfit.takes_off("skirt", cat) == ["trousers"]
    assert outfit.kept({"shirt", "trousers", "jacket"}, {"dress"}, cat) == set() and outfit.kept({"dress"}, {"shirt"}, cat) == set() and outfit.kept({"dress"}, {"jacket"}, cat) == {"dress"}
    assert outfit.kept({"shirt", "skirt"}, {"dress"}, cat) == set() and outfit.kept({"dress"}, {"skirt"}, cat) == set()
    assert not outfit.together("dress", "shirt", cat) and not outfit.together("dress", "jacket", cat)
    assert outfit.takes_off("dress", {"dress": {}}) is None and outfit.worn_with("dress", {"dress": {}}) is None                  # (no stack listed: nothing is claimed)


@needs_designer
def test_a_skirt_is_refused_beside_trousers_before_anything_is_fitted_and_a_jacket_or_an_apron_is_refused_over_a_skirt(dressed_bodies):
    dressed = dressed_bodies["female"][1][0]
    with pytest.raises(ValueError, match="The knee-length skirt is worn with a shirt only .* this outfit has trousers"):
        outfit.wear(dressed, None, [{"kind": "trousers"}, {"kind": "skirt"}])
    out, none, report = outfit.wear(dressed, None, [{"kind": "skirt"}])                                      # (not replacing: the trousers are still on the body, so the skirt is refused before the half minute it takes)
    assert out == dressed and none is None and report["worn"] == [] and report["failed"] == {"skirt": "The knee-length skirt is worn with a shirt only (nothing else has been checked with it), and this body wears trousers"}


@pytest.fixture(scope="module")
def skirt_bodies(dressed_bodies):
    """Each body in its shirt and trousers with a skirt put on in their place (what the Items tab's try-on does)."""
    return {sex: (dressed, outfit.wear(dressed, None, [{"kind": "skirt", "colour": "#4a7a4c"}], replace=True)) for sex, (before, (dressed, _, _)) in dressed_bodies.items()}


@needs_designer
def test_a_skirt_goes_on_in_place_of_the_trousers_and_the_shirt_and_everything_else_stay_as_they_were(skirt_bodies, tmp_path):
    for sex, (dressed, (worn, none, report)) in skirt_bodies.items():
        assert none is None and report["failed"] == {} and [w["kind"] for w in report["worn"]] == ["skirt"] and report["replaced"] == ["ForgeTrousers"], sex
        assert outfit.worn_kinds(worn) == ["shirt", "skirt"] and meshes(worn) == [m for m in meshes(dressed) if m != "ForgeTrousers"] + ["ForgeSkirt"], sex
        assert all(abs(a - b) <= 6 for a, b in zip(fabric_colour(worn, "ForgeSkirtFabric"), (0x4a, 0x7a, 0x4c))) and fabric_colour(worn, "ForgeShirtFabric") == fabric_colour(dressed, "ForgeShirtFabric")
        assert report["worn"][0]["version"] == 2 and report["worn"][0]["triangles"] > 5000 and report["worn"][0]["over_underwear_mm"] >= 3.0
        old, new = Glb(dressed), Glb(worn)
        for name in [m for m in meshes(dressed) if m != "ForgeTrousers"]:                                    # what was kept is value for value what it was
            a = next(m for m in old.json["meshes"] if m["name"] == name)["primitives"][0]
            b = next(m for m in new.json["meshes"] if m["name"] == name)["primitives"][0]
            for key, accessor in a["attributes"].items():
                assert np.array_equal(old.accessor(accessor), new.accessor(b["attributes"][key])), (sex, name, key)
        assert old.json["skins"] == new.json["skins"]
        path = tmp_path / f"{sex}.glb"
        path.write_bytes(worn)
        assert khronos(path) in (None, (0, 0, 0, 0)), sex


@needs_designer
def test_trousers_take_the_skirt_off_again_and_a_jacket_is_not_put_over_a_skirt(skirt_bodies):
    skirted, (worn, _, _) = skirt_bodies["female"]
    out, none, report = outfit.wear(worn, None, [{"kind": "jacket"}], replace=True)                          # (refused before the fit: the jacket was checked over a shirt and trousers)
    assert out == worn and report["worn"] == [] and report["failed"] == {"jacket": "The open jacket goes over a shirt and trousers only (nothing else has been checked with it), and this body wears a skirt"}
    assert report["replaced"] == []
    back, _, report = outfit.wear(worn, None, [{"kind": "trousers", "colour": "#2f5a3a"}], replace=True)
    assert report["failed"] == {} and report["replaced"] == ["ForgeSkirt"] and outfit.worn_kinds(back) == ["shirt", "trousers"] and "ForgeSkirt" not in meshes(back)


@needs_designer
def test_a_skirt_is_fitted_after_the_shirt_whatever_order_it_is_asked_for_in_and_a_skirt_that_does_not_fit_leaves_the_trousers(dressed_bodies, monkeypatch):
    from bodydesigner import garments
    plain = dressed_bodies["male"][0]
    dressed = dressed_bodies["male"][1][0]
    order = []

    def fit(blob, recipe, params=None):
        order.append(recipe["kind"])
        if recipe["kind"] == "skirt" and blob == dressed:
            raise ValueError("skirt.fit.clearance: body vertex -1.000, centre -1.000 mm; choose the default cut")
        return recipe["kind"].encode(), {"triangles": 1}

    monkeypatch.setattr(garments, "fit", fit)
    monkeypatch.setattr(garments, "dress", lambda blob, fitted: blob + b"|" + b"|".join(fitted))
    out, _, report = outfit.wear(plain, None, [{"kind": "skirt"}, {"kind": "shirt"}])
    assert order == ["shirt", "skirt"] and [w["kind"] for w in report["worn"]] == ["shirt", "skirt"] and out == plain + b"|shirt|skirt"
    out, _, report = outfit.wear(dressed, None, [{"kind": "skirt"}], replace=True)                          # (the skirt does not fit this body: the trousers it wears stay on it)
    assert out == dressed and report["worn"] == [] and report["replaced"] == [] and report["failed"]["skirt"].startswith("skirt.fit.clearance")


# ---- the dress: what the words name, and the putting on ---------------------------------------------------------------------------------------------------
needs_dress = pytest.mark.skipif("dress" not in outfit._catalogue(), reason="this Body Designer does not make the dress")


def test_a_dress_or_a_gown_the_sheet_names_is_found_and_a_robe_a_toga_a_swimsuit_and_the_rest_of_its_class_are_not():
    seen = outfit.read("a homespun dress and a white apron")
    assert seen["instead"] == [{"kind": "dress", "word": "dress", "colour": outfit.data()["materials"]["homespun"]}] and seen["over"][0]["kind"] == "apron" and seen["left"] == []
    assert outfit.read("a sequined gown")["instead"] == [{"kind": "dress", "word": "gown", "colour": None}] and len(outfit.read("a dress, a dress and a gown")["instead"]) == 1       # (one dress: a body wears one)
    for word in ("robe", "toga", "kimono", "cassock", "swimsuit", "bikini", "leotard"):
        seen = outfit.read(f"a linen {word}")
        assert seen["instead"] == [] and word in seen["left"], word                                         # (not that dress: said, not made)
    assert outfit.read("a navy dress uniform")["instead"] == [] and outfit.read("black dress shoes")["left"] == []        # (a dress uniform is a uniform, dress shoes are shoes)
    seen = outfit.read("a white blouse and canvas trousers")
    assert seen["top"]["word"] == "blouse" and seen["bottom"]["word"] == "trousers"                         # (the words that name the shirt and the trousers: said when a dress takes their place)


@needs_designer
@needs_dress
def test_a_dress_takes_the_place_of_the_shirt_and_the_trousers_and_nothing_goes_over_it():
    sheet = {"outfit": "a long red dress and sandals"}
    dressed = {"pieces": [{"kind": "dress", "colour": cloth("red")}], "left": [], "why": {}, "problem": ""}
    for mode in ("sheet", "basic"):
        assert outfit.pieces(mode, sheet) == dressed, mode                                                    # (the default and the sheet's own)
    assert outfit.pieces("none", sheet)["pieces"] == []
    both = outfit.pieces("sheet", {"outfit": "a white blouse, canvas trousers and a green gown"})             # a blouse and trousers with a gown: the gown is the garment seen, the others are said
    assert [p["kind"] for p in both["pieces"]] == ["dress"] and both["left"] == ["blouse", "trousers"]
    assert both["why"] == {"blouse": "A body in a dress wears no shirt (nothing else has been checked with it)", "trousers": "A body in a dress wears no trousers (nothing else has been checked with it)"}
    apron = outfit.pieces("basic", {"outfit": "a plain dress and a tan apron"})
    assert [p["kind"] for p in apron["pieces"]] == ["dress"] and apron["left"] == ["apron"] and apron["why"]["apron"].endswith("and this outfit has a dress")
    asked = outfit.pieces("basic", sheet, outer="jacket")
    assert asked["problem"].endswith("this outfit has a dress") and [p["kind"] for p in asked["pieces"]] == ["dress"]
    assert [p["kind"] for p in outfit.pieces("basic", {"outfit": "an embroidered robe"})["pieces"]] == ["shirt", "trousers"]          # (a robe is not made: the basic outfit)
    both = outfit.pieces("basic", {"outfit": "a blue dress and a green skirt"})                                # (the skirt of a dress is its own)
    assert [p["kind"] for p in both["pieces"]] == ["dress"] and both["left"] == []
    assert outfit.pieces("basic", sheet, colours={"dress": "#112233", "shirt": "#445566"})["pieces"] == [{"kind": "dress", "colour": "#112233"}]


@needs_designer
@needs_dress
def test_a_dress_is_worn_on_its_own_and_takes_everything_off_a_body_keeps_what_the_new_garment_goes_with():
    assert not any(outfit.together("dress", other) for other in ("shirt", "trousers", "skirt", "jacket", "apron"))
    assert outfit.stack_problem(["dress"]) is None
    assert outfit.stack_problem(["dress", "shirt"], only=["dress"], who="outfit") == "The sleeveless knee-length dress is worn on its own (nothing else has been checked with it), and this outfit has a shirt"
    assert outfit.stack_problem(["dress", "shirt", "trousers"]).endswith("this body wears a shirt and trousers")
    assert outfit.stack_problem(["dress", "jacket"], only=["jacket"]) == "The open jacket goes over a shirt and trousers only (nothing else has been checked with it), and this body wears a dress"
    assert outfit.kept({"shirt", "trousers", "jacket"}, {"dress"}) == set() and outfit.kept({"dress"}, {"shirt"}) == set() and outfit.kept({"dress"}, {"jacket"}) == {"dress"}
    assert outfit.kept({"shirt", "skirt"}, {"dress"}) == set() and outfit.kept({"dress"}, {"skirt"}) == set()


@pytest.fixture(scope="module")
def dress_bodies(dressed_bodies):
    """Each body in its shirt and trousers with a dress put on in their place (what the Items tab's try-on does)."""
    return {sex: (dressed, outfit.wear(dressed, None, [{"kind": "dress", "colour": "#7a2030"}], replace=True)) for sex, (before, (dressed, _, _)) in dressed_bodies.items()}


@needs_designer
@needs_dress
def test_a_dress_goes_on_in_place_of_the_shirt_and_the_trousers_and_everything_else_stays_as_it_was(dress_bodies, tmp_path):
    for sex, (dressed, (worn, none, report)) in dress_bodies.items():
        assert none is None and report["failed"] == {} and [w["kind"] for w in report["worn"]] == ["dress"] and report["replaced"] == ["ForgeShirt", "ForgeTrousers"], sex
        gone = ("ForgeShirt", "ForgeTrousers")
        assert outfit.worn_kinds(worn) == ["dress"] and meshes(worn) == [m for m in meshes(dressed) if m not in gone] + ["ForgeDress"], sex
        assert all(abs(a - b) <= 6 for a, b in zip(fabric_colour(worn, "ForgeDressFabric"), (0x7a, 0x20, 0x30)))
        assert report["worn"][0]["version"] == 2 and 8000 < report["worn"][0]["triangles"] < 18000 and report["worn"][0]["over_underwear_mm"] >= 3.0
        old, new = Glb(dressed), Glb(worn)
        for name in [m for m in meshes(dressed) if m not in gone]:                                          # what was kept is value for value what it was
            a = next(m for m in old.json["meshes"] if m["name"] == name)["primitives"][0]
            b = next(m for m in new.json["meshes"] if m["name"] == name)["primitives"][0]
            for key, accessor in a["attributes"].items():
                assert np.array_equal(old.accessor(accessor), new.accessor(b["attributes"][key])), (sex, name, key)
        assert old.json["skins"] == new.json["skins"]
        path = tmp_path / f"{sex}.glb"
        path.write_bytes(worn)
        assert khronos(path) in (None, (0, 0, 0, 0)), sex


@needs_designer
@needs_dress
def test_a_shirt_takes_the_dress_off_again_and_a_jacket_is_not_put_over_a_dress(dress_bodies):
    _, (worn, _, _) = dress_bodies["female"]
    out, none, report = outfit.wear(worn, None, [{"kind": "jacket"}], replace=True)                          # (refused before the fit: the jacket was checked over a shirt and trousers)
    assert out == worn and report["worn"] == [] and report["failed"] == {"jacket": "The open jacket goes over a shirt and trousers only (nothing else has been checked with it), and this body wears a dress"}
    back, _, report = outfit.wear(worn, None, [{"kind": "shirt", "colour": "#2f5a3a"}], replace=True)
    assert report["failed"] == {} and report["replaced"] == ["ForgeDress"] and outfit.worn_kinds(back) == ["shirt"] and "ForgeDress" not in meshes(back)
    with pytest.raises(ValueError, match="The sleeveless knee-length dress is worn on its own .* this outfit has a shirt"):
        outfit.wear(worn, None, [{"kind": "dress"}, {"kind": "shirt"}])
