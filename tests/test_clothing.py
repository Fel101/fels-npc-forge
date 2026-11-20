"""npcgen/clothing.py: the recipe of a shirt or trousers (the Body Designer's ranges, checked with words a person can read), a few words that set its controls, the pattern on its
own, which characters can wear it, what a fit depends on, and a colour put on a finished fit."""
import copy
import io
import json

import numpy as np
import pytest
from PIL import Image

from npcgen import clothing, items, outfit
from npcgen.paint3d import Glb, all_triangles
from test_underwear import khronos

needs_garments = pytest.mark.skipif(not clothing.available(), reason="the Body Designer is not installed")


def fabric(glb_bytes, name):
    """The mean (r, g, b) of the garment's cloth texture (a weave lightens and darkens it by 3 per cent either way), and its size."""
    glb = Glb(glb_bytes)
    material = next(m for m in glb.json["materials"] if m["name"] == name)
    image = glb.json["images"][glb.json["textures"][material["pbrMetallicRoughness"]["baseColorTexture"]["index"]]["source"]]
    picture = Image.open(io.BytesIO(glb.view_bytes(image["bufferView"]))).convert("RGB")
    return tuple(float(v) for v in np.asarray(picture).astype(float).reshape(-1, 3).mean(0)), picture.size


def near(colour, hexcode, tolerance=4):
    return all(abs(a - int(hexcode[i:i + 2], 16)) <= tolerance for a, i in zip(colour, (1, 3, 5)))


NOTE = "Shirt length 0.9 to 1.0 is the supported design range. Longer hems fold at the hip and thigh. Older saved recipes remain readable and are fitted individually, not rescaled."


def stand_in_designer(monkeypatch):
    """A Body Designer as it was on 2026-10-09: the shirt is offered at lengths 0.9 to 1.0 with a reason and still read from 0.8 to 1.2 (older designs), the trousers at 0.8 to 1.2 with no
    reason. The tests of what NPC Forge does with a kind's own range must not depend on what the real one offers today, which is its own to change."""
    def entry(kind, label, colour, ease, texture, length, **more):
        return {"id": kind, "label": label, "maker": "garment", "recipe": {"version": 2, "kind": kind, "colour": colour, "ease_mm": ease, "length": 1.0, "texture": texture},
                "controls": {"ease_mm": [0, 20], "length": length, "nudge_mm": [-5, 5]}, "textures": ["plain", "weave"], **more}

    entries = {"shirt": entry("shirt", "Sleeveless shirt", "#496b73", 12.0, "plain", [0.9, 1.0], control_notes={"length": NOTE}),
               "trousers": entry("trousers", "Fitted trousers", "#59606b", 4.0, "weave", [0.8, 1.2])}

    try:
        from bodydesigner import garments as real
    except ImportError:
        real = None

    class Designer:
        @staticmethod
        def clean_recipe(recipe):
            if not (0 <= recipe["ease_mm"] <= 20 and 0.8 <= recipe["length"] <= 1.2):                  # (also false for NaN)
                raise ValueError("garment.recipe: out of range")
            return recipe

        def __getattr__(self, name):                                                                   # (all the rest is the real one's: a pattern can still be built)
            if real is None:
                raise AttributeError(name)
            return getattr(real, name)

    monkeypatch.setattr(clothing, "_entries", lambda: entries)
    monkeypatch.setattr(clothing, "_garments", lambda: Designer())


# ---- the catalogue ---------------------------------------------------------------------------------------------------------------------------
@needs_garments
def test_the_catalogue_gives_the_kinds_with_the_body_designers_own_range_for_each_control_and_the_labels_and_swatches_of_ours():
    cat = clothing.catalogue()
    assert cat["available"] and cat["why"] == "" and list(cat["kinds"]) == ["shirt", "trousers", "jacket", "apron", "skirt", "dress"]
    assert cat["kinds"]["shirt"]["recipe"] == {"kind": "shirt", "colour": "#496b73", "ease_mm": 12.0, "length": 1.0, "texture": "plain"} and "version" not in cat["kinds"]["trousers"]["recipe"]
    assert cat["fields"]["ease_mm"]["label"] == "Looseness" and cat["fields"]["length"]["step"] == 0.01 and set(cat["choices"]["texture"]) == {"plain", "weave"}
    assert all(set(c) == {"id", "label", "hex"} and len(c["hex"]) == 7 for c in cat["colours"]) and len({c["id"] for c in cat["colours"]}) == len(cat["colours"])
    assert "measured" not in cat and not any(k in spec for spec in cat["fields"].values() for k in ("min", "max"))                # (a range belongs to a kind, not to a control)
    from bodydesigner import garments
    for entry in garments.catalogue():                                                                                     # (the Body Designer's own, not a copy of it)
        kind = cat["kinds"][entry["id"]]
        assert kind["controls"] == {key: list(entry["controls"][key]) for key in ("ease_mm", "length")}
        assert kind["notes"] == {k: v for k, v in entry.get("control_notes", {}).items() if k in ("ease_mm", "length")}
        low, high = kind["controls"]["length"]
        assert low <= kind["recipe"]["length"] <= high                                                                     # (a kind starts inside its own range)


def test_a_kind_has_its_own_range_and_its_reason_where_the_body_designer_gives_one(monkeypatch):
    stand_in_designer(monkeypatch)
    cat = clothing.catalogue()
    assert cat["kinds"]["shirt"]["controls"] == {"ease_mm": [0, 20], "length": [0.9, 1.0]} and cat["kinds"]["trousers"]["controls"] == {"ease_mm": [0, 20], "length": [0.8, 1.2]}
    assert cat["kinds"]["shirt"]["notes"] == {"length": NOTE} and cat["kinds"]["trousers"]["notes"] == {}
    assert "nudge_mm" not in cat["kinds"]["shirt"]["controls"]                                                         # (the Items tab has no nudge)


def test_the_swatches_are_the_colours_a_sheets_outfit_is_read_in():
    table = outfit.data()["cloth"]["colours"]
    for swatch in clothing.load()["colours"]:
        word = {"forest": "forest green", "sky": "sky blue"}.get(swatch["id"], swatch["id"])
        named = outfit.data()["cloth"]["phrases"].get(word) or table.get(word)
        assert named == swatch["hex"], swatch                                         # (a character's outfit and a designed garment use the same colours)


def test_without_the_body_designer_there_are_no_fitted_clothes_and_nothing_raises(monkeypatch):
    def missing():
        raise ValueError("Fitted clothes need the Body Designer (the bodydesigner folder next to npcgen), which is not installed here")
    monkeypatch.setattr(clothing, "_garments", missing)
    assert clothing.available() is False
    cat = clothing.catalogue()
    assert cat["available"] is False and "not installed" in cat["why"] and cat["kinds"] == {} and cat["colours"]
    assert "clothing" not in [f["id"] for f in items.catalogue()["families"]] and items.catalogue()["clothing"]["available"] is False
    with pytest.raises(ValueError, match="need the Body Designer"):
        items.clean_item({"family": "clothing", "kind": "shirt"})
    assert [f["id"] for f in items.catalogue()["families"]][-1] == "garment"            # (everything else is as it was)


# ---- the recipe ----------------------------------------------------------------------------------------------------------------------------------
@needs_garments
def test_a_recipe_is_completed_from_the_kinds_own_starting_values_and_the_kind_wins():
    shirt, trousers = clothing.clean_recipe({}), clothing.clean_recipe({"colour": "navy"}, kind="trousers")
    assert shirt == {"kind": "shirt", "colour": "#496b73", "ease_mm": 12.0, "length": 1.0, "texture": "plain"}
    assert trousers == {"kind": "trousers", "colour": "#243456", "ease_mm": 4.0, "length": 1.0, "texture": "weave"}
    assert clothing.clean_recipe({"kind": "shirt", "ease_mm": 5}, kind="trousers")["kind"] == "trousers"              # (the item's kind is the garment's)
    assert clothing.clean_recipe({"kind": "trousers"})["ease_mm"] == 4.0 and clothing.for_item({"ease_mm": 7}, "shirt", {})["ease_mm"] == 7
    once = clothing.clean_recipe({"colour": "#ABC", "ease_mm": "8.25", "length": 1.0049, "texture": "weave", "version": 3})
    assert once == {"kind": "shirt", "colour": "#aabbcc", "ease_mm": 8.25, "length": 1.0049, "texture": "weave"} and clothing.clean_recipe(once) == once       # (a version is dropped; a recipe cleans to itself)
    assert list(once) == ["kind", "colour", "ease_mm", "length", "texture"]
    assert clothing.clean_recipe({"colour": "", "ease_mm": None})["colour"] == "#496b73"


@needs_garments
@pytest.mark.parametrize("raw, message", [
    ({"ease_mm": 21}, "Looseness must be between 0 and 20"), ({"ease_mm": -1}, "Looseness must be between"), ({"length": 0.5}, "Length must be between"),
    ({"length": 1.3}, "Length must be between"), ({"length": 7}, "Length must be between"), ({"ease_mm": "loose"}, "Looseness must be a number"), ({"ease_mm": True}, "Looseness must be a number"),
    ({"length": [1]}, "Length must be a number"), ({"ease_mm": float("nan")}, "Looseness must be between"), ({"colour": "plaid"}, "The colour must be like"),
    ({"colour": 5}, "The colour must be like"), ({"texture": "leather"}, "Unknown cloth: leather"), ({"sleeves": 3}, "Unknown clothing setting: sleeves"),
    ("shirt", "must be an object"), ([], "must be an object")])
def test_bad_recipes_are_refused_saying_which_setting_is_wrong(raw, message):
    with pytest.raises(ValueError, match=message):
        clothing.clean_recipe(raw)


def test_a_value_the_body_designer_no_longer_offers_but_still_reads_is_kept_and_one_it_does_not_read_is_refused_with_the_range_it_offers(monkeypatch):
    stand_in_designer(monkeypatch)
    for length in (0.9, 1.0, 0.95):
        assert clothing.clean_recipe({"length": length})["length"] == length
    for length in (0.8, 1.1, 1.2):                                                                 # (a shirt saved when it offered 0.8 to 1.2: it keeps its length, whatever the fit then says)
        assert clothing.clean_recipe({"length": length})["length"] == length
        assert clothing.for_item({"length": length}, "shirt", {})["length"] == length
    assert clothing.clean_recipe({"length": 1.1}, kind="trousers")["length"] == 1.1
    for length in (0.5, 0.79, 1.21, float("nan")):
        with pytest.raises(ValueError, match="Length must be between 0.9 and 1$"):                   # (it says what is offered for a shirt, not what is still read)
            clothing.clean_recipe({"length": length})
    with pytest.raises(ValueError, match="Length must be between 0.8 and 1.2$"):
        clothing.clean_recipe({"length": 1.3}, kind="trousers")
    with pytest.raises(ValueError, match="Looseness must be between 0 and 20$"):
        clothing.clean_recipe({"ease_mm": 25})
    older = clothing.clean_recipe({"length": 1.1, "colour": "navy"})
    assert clothing.clean_recipe(older) == older                                                   # (an older design cleans to itself: saving it again changes nothing)
    assert clothing.fit_key(older, npc())["length"] == 1.1 and clothing.fit_key({**older, "length": 1.0}, npc()) != clothing.fit_key(older, npc())


def test_what_the_body_designer_says_about_a_setting_it_does_not_offer_is_said_for_that_recipe_only(monkeypatch):
    stand_in_designer(monkeypatch)
    assert clothing.notes({"kind": "shirt", "length": 1.1}) == [NOTE] and clothing.notes({"kind": "shirt", "length": 0.8}) == [NOTE]
    assert clothing.notes({"length": 1.2}) == [NOTE]                                               # (no kind: a shirt)
    assert clothing.notes({"kind": "shirt", "length": 1.0}) == [] and clothing.notes({"kind": "shirt", "length": 0.9}) == [] and clothing.notes({"kind": "shirt"}) == []
    assert clothing.notes({"kind": "trousers", "length": 1.2}) == [] and clothing.notes({"kind": "dress", "length": 3}) == []
    assert clothing.notes(None) == [] and clothing.notes("shirt") == [] and clothing.notes({"kind": "shirt", "length": True}) == [] and clothing.notes({"kind": "shirt", "length": "1.1"}) == []


def test_without_the_body_designer_there_are_no_notes(monkeypatch):
    def missing():
        raise ValueError("Fitted clothes need the Body Designer")
    monkeypatch.setattr(clothing, "_garments", missing)
    assert clothing.notes({"kind": "shirt", "length": 1.1}) == [] and clothing.explain("cut.volume: x").endswith("(cut.volume: x)")


@needs_garments
def test_the_live_body_designer_reads_what_it_offers_and_a_recipe_at_the_edge_of_its_range_is_clean():
    from bodydesigner import garments
    for entry in garments.catalogue():
        for key in ("ease_mm", "length"):
            low, high = entry["controls"][key]
            for value in (low, high):
                assert clothing.clean_recipe({key: value}, kind=entry["id"])[key] == value                  # (what it offers it reads)
                assert clothing.notes({"kind": entry["id"], key: value}) == []
            with pytest.raises(ValueError, match=f"must be between {low:g} and {high:g}$"):
                clothing.clean_recipe({key: 50}, kind=entry["id"])


@needs_garments
def test_a_kind_that_is_not_a_garment_is_refused():
    with pytest.raises(ValueError, match="Unknown garment: hat \\(one of shirt, trousers, jacket, apron, skirt, dress\\)"):
        clothing.clean_recipe({}, kind="hat")


@needs_garments
def test_a_swatch_can_be_named():
    assert clothing.clean_recipe({"colour": "burgundy"})["colour"] == "#6c2232" and clothing.clean_recipe({"colour": " Navy "})["colour"] == "#243456"


# ---- a few words ----------------------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("text, kind, changes", [
    ("a dark red loose shirt", "shirt", {"colour": outfit.named_colour("dark red"), "ease_mm": 15.0}),
    ("tight navy trousers, cropped", "trousers", {"colour": outfit.named_colour("navy"), "ease_mm": 0.0, "length": 0.8}),
    ("tight navy shirt, cropped", "shirt", {"colour": outfit.named_colour("navy"), "ease_mm": 0.0, "length": 0.9}),                 # (a shirt is offered from 0.9: the nearest length it has)
    ("baggy canvas shirt", "shirt", {"colour": outfit.data()["materials"]["canvas"], "ease_mm": 20.0, "texture": "weave"}),
    ("very loose long shirt in burgundy", "shirt", {"colour": outfit.named_colour("burgundy"), "ease_mm": 20.0, "length": 1.0}),      # (and to 1.0)
    ("very loose long trousers in burgundy", "trousers", {"colour": outfit.named_colour("burgundy"), "ease_mm": 20.0, "length": 1.1}),
    ("a snug fitted tailored shirt", "shirt", {"ease_mm": 3.0}),
    ("extra long, floor length", "shirt", {"length": 1.0}), ("extra long, floor length", "trousers", {"length": 1.2}),
    ("a long sleeved white shirt", "shirt", {"colour": outfit.named_colour("white")}),                    # (the sleeves are not the hem)
    ("a short sleeve shirt", "shirt", {}),
    ("nothing here at all", "shirt", {}), ("", "shirt", {}), (None, "shirt", {})])
def test_words_set_the_controls_inside_the_kinds_own_range_and_a_word_that_is_not_known_changes_nothing(monkeypatch, text, kind, changes):
    stand_in_designer(monkeypatch)
    base = clothing.clean_recipe({}, kind=kind)
    found, said = clothing.from_description(text, base)
    assert found == changes and bool(said) == bool(changes)
    merged = clothing.clean_recipe({**base, **found})                                              # (what it says is always a recipe that passes the check)
    assert all(merged[k] == v for k, v in found.items()) and clothing.notes(merged) == []         # (and one the Body Designer offers: words never ask for what it will not fit)


def test_the_longest_phrase_wins_and_what_is_already_so_is_not_said_again(monkeypatch):
    stand_in_designer(monkeypatch)
    assert clothing.from_description("very loose", None)[0] == {"ease_mm": 20.0} and clothing.from_description("loose", None)[0] == {"ease_mm": 15.0}
    trousers = clothing.clean_recipe({}, kind="trousers")
    assert clothing.from_description("very short", trousers)[0] == {"length": 0.8} and clothing.from_description("short", trousers)[0] == {"length": 0.9}
    assert clothing.from_description("very short", None)[0] == {"length": 0.9} and clothing.from_description("short", None)[0] == {"length": 0.9}
    current = clothing.clean_recipe({"colour": "navy", "texture": "weave"})
    assert clothing.from_description("navy woven", current) == ({}, [])
    assert clothing.from_description("plain", current)[0] == {"texture": "plain"}
    assert clothing.from_description("loose, long, silk", trousers)[0] == {"ease_mm": 15.0, "length": 1.1, "texture": "plain"}
    said = clothing.from_description("dark red, loose and cropped, woven", clothing.clean_recipe({}))[1]
    assert len(said) == 4 and said[0].startswith("the colour #") and "relaxed" in said[1] and said[3] == "woven cloth"
    assert said[2] == "cropped (length ×0.9, the shortest the Body Designer offers for a shirt)"       # (it says when the word asked for more than a shirt is made)
    assert clothing.from_description("long", clothing.clean_recipe({}))[1] == ["longer (length ×1, the longest the Body Designer offers for a shirt)"]
    assert clothing.from_description("cropped", trousers)[1] == ["cropped (length ×0.8)"] and clothing.from_description("long", trousers)[1] == ["longer (length ×1.1)"]


# ---- what a refusal means ------------------------------------------------------------------------------------------------------------------------------
def test_every_refusal_has_a_code_that_starts_the_designers_own_words_and_a_plain_sentence():
    refusals = clothing.load()["refusals"]
    codes = [r["code"] for r in refusals]
    assert len(codes) == len(set(codes)) and all(r["plain"] and r["plain"][0].islower() and not r["plain"].endswith(".") for r in refusals)
    assert all(c.startswith(("cut.", "garment.", "jacket.", "apron.", "skirt.", "dress.")) for c in codes)
    for code in ("jacket.fit.clearance", "jacket.fit.underwear", "jacket.fit.torso", "jacket.fit.budget", "apron.fit.clearance", "apron.fit.bib", "apron.fit.neck", "apron.fit.sections",
                 "skirt.fit.waist", "skirt.fit.section", "skirt.fit.clearance", "skirt.pattern",
                 "dress.fit.waist", "dress.fit.upper", "dress.fit.lower", "dress.fit.hem", "dress.fit.fabric", "dress.fit.topology", "dress.fit.clearance", "dress.pattern"):
        assert code in codes                                                                            # (the refusals the Body Designer's jacket, apron and skirt can give, in plain words)


def test_a_refusal_is_explained_in_plain_words_with_the_designers_own_after_it():
    raw = "cut.volume: guide edge incidence reaches 4; expected a manifold panel with shared edges"
    plain = f"the Body Designer could not close the cloth at this length on this body ({raw})"
    assert clothing.explain(raw) == plain
    assert clothing.explain("garment.fit.underwear: shirt intersects the supplied underwear; adjust the fit").startswith("the garment would sit inside this body's underwear (garment.fit.underwear")
    assert clothing.explain("something nobody has met") == "something nobody has met" and clothing.explain("") == ""
    advice = raw + "; shirt length 1.1: the hem guide folds at the hip/thigh seam. Try length 0.9 to 1.0 with zero nudge, or restore the default length 1.0."
    assert clothing.explain(advice) == f"the Body Designer could not close the cloth at this length on this body ({advice})"       # (its advice for a length it does not offer is its own: nothing is added)


# ---- the pattern on its own ------------------------------------------------------------------------------------------------------------------------
@needs_garments
def test_the_pattern_is_built_at_once_and_is_the_garment_the_recipe_says(tmp_path):
    recipe = clothing.clean_recipe({"colour": "#a02020", "ease_mm": 8, "length": 0.9, "texture": "weave"})
    glb, info = clothing.build(recipe, faces=30_000)
    parsed = Glb(glb)
    assert [n.get("name") for n in parsed.json["nodes"]] == ["ForgeShirt"] and info["kind"] == "shirt" and info["origin"] == "floor" and 2000 < info["triangles"] < 10000
    assert parsed.json["asset"]["extras"]["maker"] == "garment" and parsed.json["asset"]["extras"]["recipe"]["colour"] == "#a02020" and info["seconds"] < 5
    low, high = all_triangles(parsed)[0].min(0), all_triangles(parsed)[0].max(0)
    assert info["size_m"] == pytest.approx((high - low).tolist(), abs=1e-4) and 0.3 < info["size_m"][0] < 0.6 and 0.3 < info["size_m"][1] < 0.8
    assert 0.8 < low[1] < high[1] < 1.6                                                                  # (where it would be on an adult 1.7 m tall)
    assert near(fabric(glb, "ForgeShirtFabric")[0], "#a02020")                                           # (the red it was asked for, a faint weave on it)
    (tmp_path / "pattern.glb").write_bytes(glb)
    assert khronos(tmp_path / "pattern.glb") in (None, (0, 0, 0, 0))
    assert clothing.build(recipe)[0] == glb                                                              # (the same recipe, the same file)
    assert clothing.build({**recipe, "ease_mm": 14})[0] != glb and clothing.build({**recipe, "length": 1.0})[1]["size_m"][1] > info["size_m"][1]
    trousers, tinfo = clothing.build({"kind": "trousers"})
    assert [n.get("name") for n in Glb(trousers).json["nodes"]] == ["ForgeTrousers"] and tinfo["kind"] == "trousers" and tinfo["size_m"][1] > 0.6
    meta = clothing.model_meta(info)
    assert meta["engine_id"] == "garment" and meta["textured"] is True and meta["triangles"] == info["triangles"] and "not fitted" in meta["standing"]


@needs_garments
def test_a_recipe_that_does_not_pass_is_not_built():
    with pytest.raises(ValueError, match="Looseness"):
        clothing.build({"ease_mm": 99})


# ---- the body ---------------------------------------------------------------------------------------------------------------------------------------------
def npc(designed=True, rig=True, rig_from="designed body", model=True):
    assets = {}
    if model:
        assets["model_3d"] = {"file": "model_3d.glb", "created": "2026-10-08T10:00:00", "size": 111, **({"designed": {"base": "forge-anatomical-female-v7"}} if designed else {})}
    if rig:
        assets["rig_3d"] = {"file": "model_3d_rigged.glb", "created": "2026-10-08T10:00:01", "size": 222, "joints_from": rig_from}
    return {"id": "someone", "assets": assets}


def test_only_a_designed_body_can_wear_fitted_clothes_and_its_files_are_the_ones_that_are_fitted():
    assert clothing.body_of(npc()) == ("model_3d.glb", "model_3d_rigged.glb", {"base": "forge-anatomical-female-v7"})
    assert clothing.body_of(npc(rig=False))[1] is None and clothing.body_of(npc(rig_from="SDPose"))[1] is None             # (a rig that is not the body's own is not used)
    with pytest.raises(ValueError, match="no 3D model"):
        clothing.body_of(npc(model=False))
    with pytest.raises(ValueError, match="not made by the Body Designer"):
        clothing.body_of(npc(designed=False))
    with pytest.raises(ValueError, match="no 3D model"):
        clothing.body_of({"id": "x"})


@needs_garments
def test_a_fit_depends_on_the_garment_its_looseness_and_length_and_the_body_and_not_on_its_colour():
    recipe, body = clothing.clean_recipe({"ease_mm": 9, "length": 0.95}), npc()
    key = clothing.fit_key(recipe, body)
    assert key["kind"] == "shirt" and key["ease_mm"] == 9.0 and key["length"] == 0.95 and key["cut"] == outfit.SHIRT_VERSION and len(key["body"]) == 2
    assert clothing.fit_key({**recipe, "colour": "#112233", "texture": "weave"}, body) == key                                  # (colour and cloth go on the finished file)
    assert clothing.fit_key({**recipe, "ease_mm": 10}, body) != key and clothing.fit_key({**recipe, "length": 0.9}, body) != key
    assert clothing.fit_key({"kind": "trousers"}, body)["cut"] == 2
    other = copy.deepcopy(body)
    other["assets"]["rig_3d"]["created"] = "2026-10-09T00:00:00"
    assert clothing.fit_key(recipe, other) != key                                                                           # (the body was made again)
    json.dumps(key)                                                                                                          # (it is kept as JSON)


@needs_garments
def test_a_fitter_revision_the_body_designer_names_makes_the_fits_kept_before_it_out_of_date(monkeypatch):
    from bodydesigner import garments
    recipe, body = clothing.clean_recipe({"kind": "dress"}), npc()
    monkeypatch.delattr(garments, "FIT_REVISION", raising=False)
    key = clothing.fit_key(recipe, body)
    assert "fitter" not in key and clothing.fitter_revision() == 0                                                         # (it names none: the key is what it always was)
    for named in (0, -1, True, 1.5, "2", None):
        monkeypatch.setattr(garments, "FIT_REVISION", named, raising=False)
        assert clothing.fit_key(recipe, body) == key                                                                       # (only a whole number above 0 counts)
    monkeypatch.setattr(garments, "FIT_REVISION", 2, raising=False)
    revised = clothing.fit_key(recipe, body)
    assert revised == {**key, "fitter": 2} and revised != key                                                              # (a fit kept under the old key is stale now)
    monkeypatch.setattr(garments, "FIT_REVISION", 3, raising=False)
    assert clothing.fit_key(recipe, body)["fitter"] == 3
    json.dumps(revised)


@needs_garments
def test_a_colour_chosen_after_the_fit_is_put_on_the_finished_file_and_the_rest_is_left_alone(tmp_path):
    worn, _ = clothing.build(clothing.clean_recipe({"colour": "#a02020"}))                                                  # (a stand-in for a dressed body: it has the garment's own material)
    recoloured = clothing.recolour(worn, {"kind": "shirt", "colour": "#2020a0", "texture": "weave"})
    assert near(fabric(recoloured, "ForgeShirtFabric")[0], "#2020a0") and fabric(recoloured, "ForgeShirtFabric")[1] == (32, 32)
    before, after = Glb(worn), Glb(recoloured)
    assert [m["name"] for m in after.json["meshes"]] == [m["name"] for m in before.json["meshes"]] and after.json["nodes"] == before.json["nodes"]
    assert np.array_equal(all_triangles(after)[0], all_triangles(before)[0])                                                # (the shape is as it was)
    assert after.json["asset"] == before.json["asset"]
    (tmp_path / "recoloured.glb").write_bytes(recoloured)
    assert khronos(tmp_path / "recoloured.glb") in (None, (0, 0, 0, 0))
    again = clothing.recolour(recoloured, {"kind": "shirt", "colour": "#a02020", "texture": "plain"})
    assert near(fabric(again, "ForgeShirtFabric")[0], "#a02020", 1) and fabric(again, "ForgeShirtFabric")[0] == fabric(worn, "ForgeShirtFabric")[0]      # (and back again)
    with pytest.raises(ValueError, match="does not wear this trousers"):
        clothing.recolour(worn, {"kind": "trousers", "colour": "#000000"})


def test_a_garment_the_body_designer_adds_is_not_offered_until_it_is_wired_in(monkeypatch):                  # (a coat, say)
    def made(kind, label):
        return {"id": kind, "label": label, "maker": "garment", "recipe": {"version": 2, "kind": kind, "colour": "#496b73", "ease_mm": 12.0, "length": 1.0, "texture": "plain"},
                "controls": {"ease_mm": [0, 20], "length": [0.8, 1.2], "nudge_mm": [-5, 5]}, "textures": ["plain", "weave"]}

    class Designer:
        @staticmethod
        def catalogue():
            return [made("shirt", "Sleeveless shirt"), made("coat", "Long coat"), made("trousers", "Fitted trousers")]

    monkeypatch.setattr(clothing, "_garments", lambda: Designer())
    assert list(clothing.catalogue()["kinds"]) == ["shirt", "trousers"]
    with pytest.raises(ValueError, match=r"Unknown garment: coat \(one of shirt, trousers\)"):
        clothing.clean_recipe({"kind": "coat"})


# ---- the open jacket and the bib apron: the other two garments ----------------------------------------------------------------------------------------------
@needs_garments
def test_the_jacket_and_the_apron_have_their_own_ranges_and_say_what_they_go_over_and_what_was_checked():
    cat = clothing.catalogue()
    from bodydesigner import garments
    entries = {e["id"]: e for e in garments.catalogue()}
    for kind in ("jacket", "apron"):
        got = cat["kinds"][kind]
        assert got["label"] == entries[kind]["label"] and got["controls"] == {key: list(entries[kind]["controls"][key]) for key in ("ease_mm", "length")}
        assert got["layer_notes"] == entries[kind]["layer_notes"] and "no cloth simulation" in got["layer_notes"]       # (the Body Designer's own words about the layers, said as they are)
        assert got["over"] == ["shirt", "trousers"] == list(entries[kind]["tested_stacks"][0][:-1])               # (what it was checked over: read, not copied)
        assert not [k for k in got["notes"] if k not in ("ease_mm", "length")] and set(got["notes"]) == {"ease_mm", "length"}
    assert cat["kinds"]["shirt"]["over"] == [] and cat["kinds"]["trousers"]["over"] == [] and cat["kinds"]["shirt"]["layer_notes"] == ""
    assert cat["kinds"]["jacket"]["controls"]["length"][1] == 1.0 and cat["kinds"]["jacket"]["controls"]["ease_mm"][1] < cat["kinds"]["apron"]["controls"]["ease_mm"][1]
    assert "nudge_mm" not in cat["kinds"]["jacket"]["controls"] and "nudge_mm" not in cat["kinds"]["jacket"]["notes"]        # (their placement cannot be shifted: the page has no nudge at all)
    for kind in ("jacket", "apron"):
        assert cat["kinds"][kind]["recipe"]["ease_mm"] == 0.0 == cat["kinds"]["skirt"]["recipe"]["ease_mm"]       # (data/clothing.json says "a jacket, an apron, a skirt and a dress start at 0")
    assert "a jacket, an apron, a skirt and a dress start at 0" in clothing.load()["fields"]["ease_mm"]["hint"]


@needs_garments
def test_a_jacket_and_an_apron_are_completed_from_their_own_starting_values_and_checked_with_their_own_ranges():
    jacket, apron = clothing.clean_recipe({"colour": "navy"}, kind="jacket"), clothing.clean_recipe({}, kind="apron")
    assert jacket == {"kind": "jacket", "colour": "#243456", "ease_mm": 0.0, "length": 1.0, "texture": "plain"}
    assert apron == {"kind": "apron", "colour": "#c6b39a", "ease_mm": 0.0, "length": 1.0, "texture": "weave"}
    assert clothing.clean_recipe({"ease_mm": 8, "length": 0.95}, kind="jacket")["length"] == 0.95 and clothing.clean_recipe({"ease_mm": 20, "length": 0.8}, kind="apron")["ease_mm"] == 20.0
    for raw, message in (({"ease_mm": 9}, "Looseness must be between 0 and 8$"), ({"length": 0.9}, "Length must be between 0.95 and 1$"), ({"length": 1.1}, "Length must be between 0.95 and 1$")):
        with pytest.raises(ValueError, match=message):
            clothing.clean_recipe(raw, kind="jacket")
    for raw, message in (({"ease_mm": 21}, "Looseness must be between 0 and 20$"), ({"length": 1.3}, "Length must be between 0.8 and 1.2$")):
        with pytest.raises(ValueError, match=message):
            clothing.clean_recipe(raw, kind="apron")
    assert clothing.for_item({"colour": "#aa3322"}, "apron", {})["kind"] == "apron" and clothing.fit_key(jacket, npc())["cut"] == 2 and clothing.fit_key(apron, npc())["kind"] == "apron"


@needs_garments
def test_words_set_a_jackets_looseness_and_length_inside_its_range_and_say_when_a_word_asked_for_more():
    jacket = clothing.clean_recipe({}, kind="jacket")
    found, said = clothing.from_description("a snug brown jacket", jacket)
    assert found == {"colour": outfit.named_colour("brown"), "ease_mm": 3.0} and said[1] == "snug (3 mm looser than the body)"
    found, said = clothing.from_description("relaxed and long", jacket)
    assert found == {"ease_mm": 8, "length": 1.0} and said == ["relaxed (8 mm looser than the body, the loosest the Body Designer offers for a jacket)",
                                                                "longer (length \u00d71, the longest the Body Designer offers for a jacket)"]
    assert clothing.from_description("baggy", jacket)[0] == {"ease_mm": 8} and clothing.from_description("cropped", jacket)[0] == {"length": 0.95}
    assert clothing.from_description("baggy", clothing.clean_recipe({}, kind="apron"))[0] == {"ease_mm": 20.0}                 # (an apron takes the whole 20 mm: nothing to say)
    assert "the loosest" not in clothing.from_description("baggy", clothing.clean_recipe({}, kind="apron"))[1][0]
    merged = clothing.clean_recipe({**jacket, **clothing.from_description("baggy, long, woven", jacket)[0]})
    assert merged["ease_mm"] == 8.0 and merged["length"] == 1.0 and merged["texture"] == "weave"


@needs_garments
@pytest.mark.parametrize("kind, node, size_x", [("jacket", "ForgeJacket", (0.8, 1.4)), ("apron", "ForgeApron", (0.3, 0.6))])
def test_the_pattern_of_a_jacket_and_of_an_apron_is_built_at_once_in_the_colour_asked_for(tmp_path, kind, node, size_x):
    recipe = clothing.clean_recipe({"colour": "#a02020"}, kind=kind)
    glb, info = clothing.build(recipe)
    parsed = Glb(glb)
    assert [n.get("name") for n in parsed.json["nodes"]] == [node] and info["kind"] == kind and info["origin"] == "floor" and info["seconds"] < 5
    assert parsed.json["asset"]["extras"]["maker"] == "garment" and parsed.json["asset"]["extras"]["recipe"]["kind"] == kind
    assert size_x[0] < info["size_m"][0] < size_x[1] and 0.4 < info["size_m"][1] < 1.1 and info["triangles"] <= {"jacket": 16000, "apron": 10000}[kind]
    assert near(fabric(glb, node + "Fabric")[0], "#a02020", 6)
    (tmp_path / "pattern.glb").write_bytes(glb)
    assert khronos(tmp_path / "pattern.glb") in (None, (0, 0, 0, 0))
    assert clothing.build(recipe)[0] == glb and clothing.build({**recipe, "ease_mm": 4})[0] != glb
    recoloured = clothing.recolour(glb, {"kind": kind, "colour": "#2020a0", "texture": recipe["texture"]})
    assert near(fabric(recoloured, node + "Fabric")[0], "#2020a0", 6) and np.array_equal(all_triangles(Glb(recoloured))[0], all_triangles(parsed)[0])
    with pytest.raises(ValueError, match="does not wear this shirt"):
        clothing.recolour(glb, {"kind": "shirt", "colour": "#000000"})


def npc_wearing(*kinds, eases=None):
    """A character whose designed body was made wearing these garments (the record of the design says which, in what colour and how loose)."""
    body = npc()
    body["assets"]["model_3d"]["designed"]["clothes"] = [{"kind": k, "colour": "#112233", "version": 3 if k == "shirt" else 2, **({"ease_mm": eases[k]} if eases and k in eases else {})} for k in kinds]
    return body


@needs_garments
def test_a_jacket_or_an_apron_goes_only_on_a_body_that_wears_a_shirt_and_trousers_and_it_replaces_the_other_one():
    both = ("shirt", "trousers")
    for kind in ("jacket", "apron"):
        assert clothing.can_wear(npc_wearing(*both), {}, kind=kind) == (True, "")
        assert clothing.can_wear(npc_wearing(*both), {"kind": kind}) == (True, "")
        for wears, who in ((("shirt",), "no trousers"), (("trousers",), "no shirt"), ((), "neither")):
            ok, why = clothing.can_wear(npc_wearing(*wears), {}, kind=kind)
            assert not ok and why.endswith(f"and this body wears {who}") and "only (nothing else has been checked with it)" in why, (kind, wears, why)
    for kind, wearing in (("jacket", "apron"), ("apron", "jacket")):
        assert clothing.can_wear(npc_wearing(*both, wearing), {}, kind=kind) == (True, "")                           # (a body wears one outer garment: the new one takes the old one off)
    assert clothing.can_wear(npc_wearing(), {}, kind="shirt") == (True, "") and clothing.can_wear(npc_wearing("jacket"), {}, kind="trousers") == (True, "")
    ok, why = clothing.can_wear(npc(designed=False), {}, kind="jacket")
    assert not ok and "not made by the Body Designer" in why                                                          # (the first thing: a designed body)
    ok, why = clothing.can_wear(npc(model=False), {}, kind="apron")
    assert not ok and "no 3D model" in why
    assert clothing.can_wear(npc_wearing(*both), {"ease_mm": 99}, kind="jacket") == (True, "")                      # (a recipe that is wrong is the fit's to say)


@needs_garments
def test_a_shirt_or_trousers_looser_than_the_jacket_or_apron_was_checked_over_are_refused_and_what_is_not_known_is_not_judged(monkeypatch):
    from bodydesigner import garments
    real = garments.catalogue()
    narrow = [{**e, "layer_margins": {"shirt_ease_mm": 10, "trousers_ease_mm": 4}} if e["id"] in ("jacket", "apron") else e for e in real]
    monkeypatch.setattr(garments, "catalogue", lambda: narrow)
    body = npc_wearing("shirt", "trousers", eases={"shirt": 12.0, "trousers": 3.0})
    ok, why = clothing.can_wear(body, {}, kind="jacket")
    assert not ok and why == "The open jacket has been checked over a shirt no looser than 10 mm, and this one is 12 mm"
    assert clothing.can_wear(npc_wearing("shirt", "trousers", eases={"shirt": 9.0, "trousers": 4.0}), {}, kind="apron") == (True, "")
    assert clothing.can_wear(npc_wearing("shirt", "trousers"), {}, kind="jacket") == (True, "")                      # (a body made before the record kept looseness: not judged)
    worn = npc_wearing("shirt", "trousers", "jacket", eases={"shirt": 5.0, "trousers": 2.0})
    ok, why = clothing.can_wear(worn, {"ease_mm": 11.0}, kind="shirt")                                                  # (a loose shirt under the jacket it wears)
    assert not ok and why == "The open jacket has been checked over a shirt no looser than 10 mm, and this one is 11 mm"
    assert clothing.can_wear(worn, {"ease_mm": 10.0}, kind="shirt") == (True, "") and clothing.can_wear(worn, {"ease_mm": 5.0}, kind="trousers")[0] is False
    assert clothing.can_wear(npc_wearing("shirt", "trousers"), {"ease_mm": 19.0}, kind="shirt") == (True, "")           # (nothing outer is worn: nothing to be looser than)


# ---- the knee-length skirt: worn with a shirt, in place of trousers ---------------------------------------------------------------------------------------
@needs_garments
def test_the_skirt_says_what_it_is_worn_with_and_instead_of_and_has_its_own_ranges_and_words():
    cat = clothing.catalogue()
    from bodydesigner import garments
    entry = next(e for e in garments.catalogue() if e["id"] == "skirt")
    got = cat["kinds"]["skirt"]
    assert got["label"] == entry["label"] and got["controls"] == {key: list(entry["controls"][key]) for key in ("ease_mm", "length")}
    assert got["over"] == [] and got["with"] == ["shirt"] == list(entry["tested_stacks"][0][:-1]) and got["instead_of"] == ["trousers"]      # (read from the Body Designer's stack, not copied)
    assert got["layer_notes"] == entry["layer_notes"] and "no cloth simulation" in got["layer_notes"] and got["recipe"]["ease_mm"] == 0.0
    for kind in ("shirt", "trousers", "jacket", "apron"):
        assert cat["kinds"][kind]["with"] == [] and cat["kinds"][kind]["instead_of"] == [], kind
    assert clothing.clean_recipe({}, kind="skirt") == {"kind": "skirt", "colour": "#68735b", "ease_mm": 0.0, "length": 1.0, "texture": "weave"}
    assert clothing.clean_recipe({"ease_mm": 20, "length": 1.2}, kind="skirt")["length"] == 1.2 and clothing.for_item({"colour": "#aa3322"}, "skirt", {})["kind"] == "skirt"
    for raw, message in (({"ease_mm": 21}, "Looseness must be between 0 and 20$"), ({"length": 1.3}, "Length must be between 0.8 and 1.2$"), ({"length": 0.7}, "Length must be between 0.8 and 1.2$")):
        with pytest.raises(ValueError, match=message):
            clothing.clean_recipe(raw, kind="skirt")
    assert clothing.fit_key(clothing.clean_recipe({}, kind="skirt"), npc())["cut"] == 2
    glb, info = clothing.build(clothing.clean_recipe({"colour": "#a02020"}, kind="skirt"))
    assert info["kind"] == "skirt" and [n.get("name") for n in Glb(glb).json["nodes"]] == ["ForgeSkirt"] and near(fabric(glb, "ForgeSkirtFabric")[0], "#a02020", 6) and info["triangles"] <= 10000
    assert clothing.NAMES["skirt"] == "ForgeSkirt"


@needs_garments
def test_a_skirt_goes_on_any_designed_body_and_takes_the_trousers_and_an_outer_garment_off_and_nothing_goes_over_it():
    both = ("shirt", "trousers")
    for wears in (both, ("shirt",), (), both + ("jacket",), both + ("apron",)):
        assert clothing.can_wear(npc_wearing(*wears), {}, kind="skirt") == (True, ""), wears               # (the trousers it replaces, and the jacket or apron it was never checked with, come off)
    assert clothing.can_wear(npc_wearing("shirt", "skirt"), {}, kind="trousers") == (True, "")           # (trousers take the skirt off)
    for kind in ("jacket", "apron"):
        ok, why = clothing.can_wear(npc_wearing("shirt", "skirt"), {}, kind=kind)
        assert not ok and why.endswith("and this body wears a skirt") and "only (nothing else has been checked with it)" in why, (kind, why)
    assert clothing.can_wear(npc_wearing("shirt", "skirt"), {"ease_mm": 19.0}, kind="shirt") == (True, "")      # (the Body Designer claims no limit on a shirt under a skirt)
    ok, why = clothing.can_wear(npc(designed=False), {}, kind="skirt")
    assert not ok and "not made by the Body Designer" in why


# ---- the sleeveless dress: worn on its own, in place of the shirt and the trousers ---------------------------------------------------------------------------
@needs_garments
def test_the_dress_says_what_it_is_worn_with_and_instead_of_and_has_its_own_ranges_and_words():
    from bodydesigner import garments
    if not any(e["id"] == "dress" for e in garments.catalogue()):
        pytest.skip("this Body Designer does not make the dress")
    cat = clothing.catalogue()
    entry = next(e for e in garments.catalogue() if e["id"] == "dress")
    got = cat["kinds"]["dress"]
    assert got["label"] == entry["label"] and got["controls"] == {key: list(entry["controls"][key]) for key in ("ease_mm", "length")} and got["controls"]["ease_mm"][1] == 8
    assert got["over"] == [] and got["with"] == [] and got["instead_of"] == ["shirt", "trousers"] and entry["tested_stacks"] == [["dress"]]      # (read from the Body Designer's stack, not copied)
    assert got["layer_notes"] == entry["layer_notes"] and "no cloth simulation" in got["layer_notes"] and "about 110 mm" in got["notes"]["length"]
    assert clothing.clean_recipe({}, kind="dress") == {"kind": "dress", "colour": "#755e83", "ease_mm": 0.0, "length": 1.0, "texture": "weave"}
    for raw, message in (({"ease_mm": 9}, "Looseness must be between 0 and 8$"), ({"length": 1.3}, "Length must be between 0.8 and 1.2$"), ({"length": 0.7}, "Length must be between 0.8 and 1.2$")):
        with pytest.raises(ValueError, match=message):
            clothing.clean_recipe(raw, kind="dress")
    glb, info = clothing.build(clothing.clean_recipe({"colour": "#a02020"}, kind="dress"))
    assert info["kind"] == "dress" and [n.get("name") for n in Glb(glb).json["nodes"]] == ["ForgeDress"] and near(fabric(glb, "ForgeDressFabric")[0], "#a02020", 6) and info["triangles"] <= 18000
    assert clothing.NAMES["dress"] == "ForgeDress" and clothing.fit_key(clothing.clean_recipe({}, kind="dress"), npc())["cut"] == 2


@needs_garments
def test_a_dress_goes_on_any_designed_body_and_takes_the_shirt_the_trousers_and_an_outer_garment_off_and_nothing_goes_over_it():
    from bodydesigner import garments
    if not any(e["id"] == "dress" for e in garments.catalogue()):
        pytest.skip("this Body Designer does not make the dress")
    for wears in (("shirt", "trousers"), ("shirt",), (), ("shirt", "trousers", "jacket"), ("shirt", "skirt"), ("shirt", "trousers", "apron")):
        assert clothing.can_wear(npc_wearing(*wears), {}, kind="dress") == (True, ""), wears
    assert clothing.can_wear(npc_wearing("dress"), {}, kind="shirt") == (True, "") and clothing.can_wear(npc_wearing("dress"), {}, kind="skirt") == (True, "")      # (a shirt or a skirt takes the dress off)
    for kind in ("jacket", "apron"):
        ok, why = clothing.can_wear(npc_wearing("dress"), {}, kind=kind)
        assert not ok and why.endswith("and this body wears a dress") and "only (nothing else has been checked with it)" in why, (kind, why)
