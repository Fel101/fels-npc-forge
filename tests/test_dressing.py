"""npcgen/dressing.py: a garment of the Items tab fitted to a designed body on a thread of its own, kept with the item, judged again when the garment or the body changes, stoppable.
The Body Designer's fit is stood in for here (it takes half a minute: tests/test_outfit.py fits for real); what is real is everything around it."""
import io
import json
import threading
import time

import numpy as np
import pytest
from PIL import Image

from npcgen import clothing, dressing, items
from npcgen.comfy import Cancelled
from npcgen.itemstore import ItemStore
from npcgen.library import Library
from npcgen.paint3d import Glb
from test_clothing import stand_in_designer

needs_garments = pytest.mark.skipif(not clothing.available(), reason="the Body Designer is not installed")
pytestmark = needs_garments

PROFILE = {"first_name": "Fit", "last_name": "Ted", "age": 30, "gender": "female", "occupation": "tester"}


def garment(**more):
    return items.clean_item({"family": "clothing", "kind": "shirt", "name": "Test shirt", **more})


def designed(lib, designed_body=True, name="ted", clothes=(), shapes=False, swing=None):
    """A character whose 3D model is (or is not) a designed body: its two files are stand-ins, the stub fit does not read them. `clothes`: the garments its design says it was made
    wearing (kinds, or {kind, ease_mm...} records); `shapes`: its design has the Body Designer's face shapes and `swing` ({hair, cloth}) its spring bones (it is built again for a garment, not fitted)."""
    npc = lib.create({"setting": "modern", "style": "pixel", "seed": 1, "profile": {**PROFILE, "last_name": name}, "appearance": {}})
    lib.write_asset(npc["id"], "model_3d.glb", b"plain-body")
    lib.write_asset(npc["id"], "model_3d_rigged.glb", b"animated-body")
    model = {"file": "model_3d.glb", "created": "2026-10-08T10:00:00", "size": 10, **({"designed": {"base": "forge-anatomical-female-v7", "clothes": [c if isinstance(c, dict) else {"kind": c, "colour": "#112233", "version": 3 if c == "shirt" else 2} for c in clothes],
                                                                                      **({"recipe": {"version": 10, "base": "forge-anatomical-female-v7", **({"expressions": True} if shapes else {}), **({"secondary": swing} if swing else {})}, "skeleton": "low"} if shapes or swing else {})}} if designed_body else {})}
    rig = {"file": "model_3d_rigged.glb", "created": "2026-10-08T10:00:01", "size": 13, "joints_from": "designed body"}
    lib.update(npc["id"], lambda n: n.setdefault("assets", {}).update(model_3d=model, rig_3d=rig))
    return npc["id"]


def worn_report(recipe, replaced=None):
    replaced = {"shirt": ("ForgeShirt",), "trousers": ("ForgeTrousers",), "skirt": ("ForgeTrousers",)}.get(recipe["kind"], ()) if replaced is None else replaced      # (what a body made with that garment loses: a jacket or an apron goes over, a skirt takes the trousers off)
    return {"worn": [{"kind": recipe["kind"], "colour": recipe["colour"], "version": 3 if recipe["kind"] == "shirt" else 2, "triangles": 3200, "seconds": 0.1, "over_underwear_mm": 3.05}], "failed": {}, "replaced": list(replaced)}


@pytest.fixture()
def bench(tmp_path):
    lib = Library(tmp_path / "library")
    store = ItemStore(lib, cfg=None)
    calls = []

    def fit(body, pieces, cancel=None):
        calls.append({"body": body, "pieces": pieces})
        glb, _ = clothing.build(pieces[0])                          # (it has the garment's own material, so a colour can be put on it)
        return glb, worn_report(pieces[0])

    dr = dressing.Dressings(store, lib, fit=fit)
    yield types_namespace(lib=lib, store=store, calls=calls, dr=dr)
    dr.close()
    store.close()


def types_namespace(**kw):
    import types
    return types.SimpleNamespace(**kw)


def settle(dr):
    assert dr.wait_idle(20)


def fitted(bench, **recipe):
    """A garment and a designed character, fitted: (item id, npc id)."""
    item = bench.store.create(garment(recipe=recipe))
    npc = designed(bench.lib)
    bench.dr.request(item["id"], npc)
    settle(bench.dr)
    return item["id"], npc


def colour_of(glb_bytes, name="ForgeShirtFabric"):
    glb = Glb(glb_bytes)
    material = next(m for m in glb.json["materials"] if m["name"] == name)
    image = glb.json["images"][glb.json["textures"][material["pbrMetallicRoughness"]["baseColorTexture"]["index"]]["source"]]
    return tuple(int(v) for v in np.asarray(Image.open(io.BytesIO(glb.view_bytes(image["bufferView"]))).convert("RGB")).astype(float).reshape(-1, 3).mean(0))


# ---- a fit, kept ---------------------------------------------------------------------------------------------------------------------------------
def test_a_fit_is_made_on_a_thread_of_its_own_kept_with_the_item_and_not_made_again_until_something_changes(bench):
    item = bench.store.create(garment(recipe={"colour": "#a02020", "ease_mm": 9}))
    npc = designed(bench.lib)
    first = bench.dr.request(item["id"], npc)
    assert first["state"] in ("queued", "running") and first["eligible"] and first["fit"] is None
    settle(bench.dr)
    done = bench.dr.status(item["id"], npc)
    assert done["state"] == "done" and done["stale"] is False and done["recoloured"] is False and done["message"] == ""
    assert done["fit"]["kind"] == "shirt" and done["fit"]["cut"] == 3 and done["fit"]["triangles"] == 3200 and done["fit"]["over_underwear_mm"] == 3.05 and done["fit"]["replaced"] == ["ForgeShirt"]
    assert len(bench.calls) == 1 and bench.calls[0]["body"] == b"animated-body" and bench.calls[0]["pieces"] == [items.clean_item(garment(recipe={"colour": "#a02020", "ease_mm": 9}))["recipe"]]
    folder = bench.store.root / item["id"] / "worn"
    assert (folder / f"{npc}.glb").is_file() and (folder / f"{npc}.json").is_file()
    record = json.loads((folder / f"{npc}.json").read_text("utf-8"))
    assert record["key"] == clothing.fit_key(item["recipe"], bench.lib.get(npc)) and record["recipe"] == item["recipe"] and record["report"]["worn"][0]["triangles"] == 3200
    glb, info = bench.dr.worn(item["id"], npc)
    assert glb[:4] == b"glTF" and info == {"stale": False, "recoloured": False, "fit": done["fit"]}
    again = bench.dr.request(item["id"], npc)                                  # (a fit that is kept and still good is left as it is)
    assert again["state"] == "done" and len(bench.calls) == 1
    bench.dr.request(item["id"], npc, force=True)
    settle(bench.dr)
    assert len(bench.calls) == 2 and bench.dr.status(item["id"], npc)["state"] == "done"


def test_a_body_with_face_shapes_is_built_again_with_the_garment_instead_of_fitted_in_place(bench):
    """The Body Designer's fit refuses a body that has morph targets, so the try-on of such a character builds the neutral body again (npcgen/bodymaker.py refit); any other character is fitted as before."""
    rebuilds = []

    def rebuild(npc, pieces, cancel, say):
        rebuilds.append({"npc": npc["id"], "pieces": pieces, "recipe": npc["assets"]["model_3d"]["designed"]["recipe"]})
        say("Building the body from its recipe")
        glb, _ = clothing.build(pieces[0])
        return glb, worn_report(pieces[0], replaced=[])

    dr = dressing.Dressings(bench.store, bench.lib, fit=lambda *a: pytest.fail("a body with face shapes is not fitted in place"), rebuild=rebuild)
    try:
        item = bench.store.create(garment(recipe={"colour": "#a02020"}))
        npc = designed(bench.lib, shapes=True, name="shaped")
        dr.request(item["id"], npc)
        assert dr.wait_idle(20)
        done = dr.status(item["id"], npc)
        assert done["state"] == "done" and done["fit"]["kind"] == "shirt" and done["fit"]["replaced"] == []
        assert len(rebuilds) == 1 and rebuilds[0]["npc"] == npc and rebuilds[0]["pieces"][0]["colour"] == "#a02020" and rebuilds[0]["recipe"]["expressions"] is True
        glb, info = dr.worn(item["id"], npc)
        assert glb[:4] == b"glTF" and info["stale"] is False
    finally:
        dr.close()
    plain = designed(bench.lib, name="plain")
    bench.dr.request(item["id"], plain)
    settle(bench.dr)
    assert len(bench.calls) == 1 and bench.calls[0]["body"] == b"animated-body"


def test_a_body_with_spring_bones_is_built_again_with_the_garment_too_and_the_page_is_told_what_was_left_off(bench):
    """A file with spring bones must never go into the Body Designer's fitter (bodydesigner/secondary.py): the try-on of such a character builds the body again from its recipe, which swings again."""
    seen, rebuilds = [], []

    def rebuild(npc, pieces, cancel, say):
        seen.append(dr.status(item["id"], npc["id"])["message"])                 # (what the page shows while it works)
        rebuilds.append(npc["assets"]["model_3d"]["designed"]["recipe"])
        glb, _ = clothing.build(pieces[0])
        return glb, {**worn_report(pieces[0], replaced=[]), "swinging_left_off": {"cloth": "this try-on has no skirt or dress with nothing over it to swing on"}}

    dr = dressing.Dressings(bench.store, bench.lib, fit=lambda *a: pytest.fail("a body with spring bones is not fitted in place"), rebuild=rebuild)
    try:
        item = bench.store.create(garment(recipe={"colour": "#a02020"}))
        npc = designed(bench.lib, swing={"hair": True, "cloth": True}, name="swinger")
        dr.request(item["id"], npc)
        assert dr.wait_idle(20)
        done = dr.status(item["id"], npc)
        assert done["state"] == "done" and done["fit"]["kind"] == "shirt"
        assert done["fit"]["swinging_left_off"] == {"cloth": "this try-on has no skirt or dress with nothing over it to swing on"}      # (the page can say it)
        assert rebuilds == [{"version": 10, "base": "forge-anatomical-female-v7", "secondary": {"hair": True, "cloth": True}}]
        assert seen == ["Building its body again to fit the shirt (about a minute: it has swinging hair or cloth)"]
        both = designed(bench.lib, shapes=True, swing={"hair": True, "cloth": False}, name="both")
        dr.request(item["id"], both)
        assert dr.wait_idle(20)
        assert seen[-1].endswith("it has face shapes and swinging hair or cloth)")
        glb, info = dr.worn(item["id"], npc)
        assert glb[:4] == b"glTF" and info["fit"]["swinging_left_off"]
    finally:
        dr.close()
    plain = designed(bench.lib, name="plain")                                                            # any other character is fitted in place, as before
    bench.dr.request(item["id"], plain)
    settle(bench.dr)
    assert len(bench.calls) == 1 and bench.calls[0]["body"] == b"animated-body" and bench.dr.status(item["id"], plain)["fit"]["swinging_left_off"] == {}


def test_a_build_again_that_fails_says_why_and_leaves_the_character_alone(bench, monkeypatch):
    from npcgen import bodymaker
    monkeypatch.setattr(bodymaker, "refit", lambda cfg, npc, pieces, cancel=None, note=None: (_ for _ in ()).throw(bodymaker.BodyError("The body could not be built again with the shirt it wears: it did not fit")))
    dr = dressing.Dressings(bench.store, bench.lib, fit=lambda *a: pytest.fail("not fitted in place"))
    try:
        item = bench.store.create(garment())
        npc = designed(bench.lib, shapes=True, name="shaped")
        before = bench.lib.get(npc)["assets"]
        dr.request(item["id"], npc)
        assert dr.wait_idle(20)
        status = dr.status(item["id"], npc)
        assert status["state"] == "error" and "could not be built again with the shirt it wears" in status["message"] and status["fit"] is None
        assert bench.lib.get(npc)["assets"] == before
    finally:
        dr.close()


def test_the_body_is_fitted_from_its_animated_file_else_its_plain_one(bench):
    item = bench.store.create(garment())
    npc = designed(bench.lib)
    bench.lib.update(npc, lambda n: n["assets"].pop("rig_3d"))
    bench.dr.request(item["id"], npc)
    settle(bench.dr)
    assert bench.calls[0]["body"] == b"plain-body"


def test_nothing_is_known_about_an_item_that_has_not_been_fitted(bench):
    item = bench.store.create(garment())
    npc = designed(bench.lib)
    status = bench.dr.status(item["id"], npc)
    assert status == {"item": item["id"], "npc": npc, "eligible": True, "why": "", "state": "none", "message": "", "fit": None, "stale": False, "recoloured": False}
    with pytest.raises(ValueError, match="not been fitted"):
        bench.dr.worn(item["id"], npc)
    with pytest.raises(KeyError):
        bench.dr.status("nothing-here", npc)
    with pytest.raises(KeyError):
        bench.dr.status(item["id"], "nobody")


# ---- what a change does ---------------------------------------------------------------------------------------------------------------------------
def test_a_colour_changed_after_the_fit_is_put_on_the_kept_file_and_needs_no_new_fit(bench):
    item_id, npc = fitted(bench, colour="#a02020", texture="plain")
    glb, _ = bench.dr.worn(item_id, npc)
    assert all(abs(a - b) <= 2 for a, b in zip(colour_of(glb), (0xa0, 0x20, 0x20)))
    bench.store.edit(item_id, garment(recipe={"colour": "#2020a0", "texture": "weave"}))
    status = bench.dr.status(item_id, npc)
    assert status["state"] == "done" and status["recoloured"] is True and status["stale"] is False
    recoloured, info = bench.dr.worn(item_id, npc)
    assert info["recoloured"] is True and all(abs(a - b) <= 4 for a, b in zip(colour_of(recoloured), (0x20, 0x20, 0xa0)))
    assert bench.dr.request(item_id, npc)["state"] == "done" and len(bench.calls) == 1                     # (the colour is not part of a fit)
    assert (bench.store.root / item_id / "worn" / f"{npc}.glb").read_bytes() == glb                         # (and the file kept is as the fit made it)


def test_a_new_looseness_or_length_makes_the_fit_out_of_date_and_it_is_made_again_when_asked_for(bench):
    item_id, npc = fitted(bench, ease_mm=9, length=1.0)
    for change in ({"ease_mm": 14, "length": 1.0}, {"ease_mm": 14, "length": 0.9}):
        bench.store.edit(item_id, garment(recipe=change))
        status = bench.dr.status(item_id, npc)
        assert status["stale"] is True and status["state"] == "done" and status["fit"]["recipe"]["ease_mm"] in (9.0, 14.0)           # (the fit that was made is still there to look at)
        assert bench.dr.worn(item_id, npc)[1]["stale"] is True
        before = len(bench.calls)
        bench.dr.request(item_id, npc)
        settle(bench.dr)
        assert len(bench.calls) == before + 1 and bench.dr.status(item_id, npc)["stale"] is False and bench.calls[-1]["pieces"][0]["ease_mm"] == change["ease_mm"]


def test_a_body_made_again_makes_the_fit_out_of_date(bench):
    item_id, npc = fitted(bench)
    bench.lib.update(npc, lambda n: n["assets"]["rig_3d"].update(created="2026-10-09T09:00:00"))
    assert bench.dr.status(item_id, npc)["stale"] is True
    bench.dr.request(item_id, npc)
    settle(bench.dr)
    assert bench.dr.status(item_id, npc)["stale"] is False and len(bench.calls) == 2


# ---- what cannot be fitted -----------------------------------------------------------------------------------------------------------------------
def test_a_character_that_is_not_a_designed_body_cannot_wear_it_and_a_thing_that_is_not_a_garment_is_not_put_on(bench):
    item = bench.store.create(garment())
    plain = designed(bench.lib, designed_body=False, name="plain")
    status = bench.dr.status(item["id"], plain)
    assert status["eligible"] is False and "not made by the Body Designer" in status["why"] and status["state"] == "none"
    with pytest.raises(ValueError, match="not made by the Body Designer"):
        bench.dr.request(item["id"], plain)
    nothing = bench.lib.create({"setting": "modern", "style": "pixel", "seed": 2, "profile": PROFILE, "appearance": {}})["id"]
    with pytest.raises(ValueError, match="no 3D model"):
        bench.dr.request(item["id"], nothing)
    pair = bench.store.create(items.clean_item({"family": "eyewear", "kind": "glasses"}))
    with pytest.raises(ValueError, match="Only fitted clothes"):
        bench.dr.request(pair["id"], designed(bench.lib, name="other"))
    assert bench.calls == []


def test_a_garment_that_does_not_fit_is_an_error_that_says_so_and_the_fit_that_was_there_is_kept(bench, monkeypatch):
    stand_in_designer(monkeypatch)                                                                          # (a shirt of length 1.1 is a design saved when the Designer offered it: still read, not offered)
    item_id, npc = fitted(bench, ease_mm=9)
    refusal = "cut.volume: guide edge incidence reaches 4; expected a manifold panel with shared edges"

    def refuse(body, pieces, cancel=None):
        return b"", {"worn": [], "failed": {"shirt": refusal}}

    bench.dr.fit = refuse
    bench.store.edit(item_id, garment(recipe={"ease_mm": 9, "length": 1.1}))
    bench.dr.request(item_id, npc)
    settle(bench.dr)
    status = bench.dr.status(item_id, npc)
    assert status["state"] == "error" and status["message"] == f"This shirt does not fit this body: the Body Designer could not close the cloth at this length on this body ({refusal})"
    assert status["fit"] is not None and status["stale"] is True                                           # (the earlier fit is still there, said to be out of date)
    bench.store.edit(item_id, garment(recipe={"ease_mm": 9, "length": 1.1, "colour": "navy"}))             # (a colour is not part of a fit: the same garment still does not fit)
    assert bench.dr.status(item_id, npc)["state"] == "error"
    bench.store.edit(item_id, garment(recipe={"ease_mm": 9, "length": 0.95}))                              # (another length: the refusal was about a garment that is not this one any more)
    after = bench.dr.status(item_id, npc)
    assert after["state"] == "done" and after["message"] == "" and after["stale"] is True
    bench.store.edit(item_id, garment(recipe={"ease_mm": 9, "length": 1.1}))
    assert bench.dr.status(item_id, npc)["state"] == "error"                                              # (and the same garment again: it is the same news)

    def refuse_another(body, pieces, cancel=None):
        return b"", {"worn": [], "failed": {"shirt": "garment.fit.somethingnew: a reason nobody has met"}}

    bench.dr.fit = refuse_another                                                                           # (a length inside what it offers gets no advice about length; a refusal not known is said as it was)
    bench.store.edit(item_id, garment(recipe={"ease_mm": 9, "length": 0.95}))
    bench.dr.request(item_id, npc, force=True)
    settle(bench.dr)
    assert bench.dr.status(item_id, npc)["message"] == "This shirt does not fit this body: garment.fit.somethingnew: a reason nobody has met"
    bench.dr.fit = refuse

    def broken(body, pieces, cancel=None):
        raise dressing.DressingError("Fitting the clothes failed: no output")

    bench.dr.fit = broken
    bench.dr.request(item_id, npc, force=True)
    settle(bench.dr)
    assert bench.dr.status(item_id, npc)["message"] == "Fitting the clothes failed: no output" and bench.dr.status(item_id, npc)["state"] == "error"

    def crash(body, pieces, cancel=None):
        raise RuntimeError("out of memory")

    bench.dr.fit = crash
    bench.dr.request(item_id, npc, force=True)
    settle(bench.dr)
    assert bench.dr.status(item_id, npc)["message"] == "The fit stopped unexpectedly (RuntimeError: out of memory); the character was not changed."

    def bare(body, pieces, cancel=None):
        raise KeyError()

    bench.dr.fit = bare                                                                                         # (an exception with no words of its own: its name, in the same sentence)
    bench.dr.request(item_id, npc, force=True)
    settle(bench.dr)
    assert bench.dr.status(item_id, npc)["message"] == "The fit stopped unexpectedly (KeyError); the character was not changed."
    bench.dr.fit = lambda body, pieces, cancel=None: (clothing.build(pieces[0])[0], worn_report(pieces[0]))
    bench.dr.request(item_id, npc, force=True)                                                                # (an error does not stop the next fit)
    settle(bench.dr)
    assert bench.dr.status(item_id, npc)["state"] == "done"


def test_a_garment_none_of_which_was_put_on_is_refused_even_when_the_designer_gives_no_reason(bench):
    item = bench.store.create(garment())
    npc = designed(bench.lib)
    bench.dr.fit = lambda body, pieces, cancel=None: (b"", {"worn": [], "failed": {}})
    bench.dr.request(item["id"], npc)
    settle(bench.dr)
    assert "it was not put on" in bench.dr.status(item["id"], npc)["message"]


# ---- stopping, order, deleting -------------------------------------------------------------------------------------------------------------------
def test_a_fit_can_be_stopped_whether_it_is_running_or_waiting_and_the_next_one_goes_on(bench):
    started, release = threading.Event(), threading.Event()
    order = []

    def slow(body, pieces, cancel=None):
        order.append(pieces[0]["colour"])
        started.set()
        while not cancel.is_set() and not release.is_set():
            time.sleep(0.01)
        if cancel.is_set():
            raise Cancelled()
        return clothing.build(pieces[0])[0], worn_report(pieces[0])

    bench.dr.fit = slow
    one, two, three = (bench.store.create(garment(name=f"s{i}", recipe={"colour": c})) for i, c in enumerate(("#111111", "#222222", "#333333")))
    npc = designed(bench.lib)
    for item in (one, two, three):
        bench.dr.request(item["id"], npc)
    assert started.wait(10)
    assert bench.dr.status(one["id"], npc)["state"] == "running" and bench.dr.status(two["id"], npc)["state"] == "queued"
    assert bench.dr.cancel(two["id"], npc)["state"] == "cancelled"                                          # (waiting: it never starts)
    assert bench.dr.cancel(one["id"], npc)["state"] in ("running", "cancelled")                              # (running: its process is stopped)
    release.set()
    settle(bench.dr)
    assert bench.dr.status(one["id"], npc)["state"] == "cancelled" and bench.dr.status(two["id"], npc)["state"] == "cancelled"
    assert bench.dr.status(three["id"], npc)["state"] == "done" and order == ["#111111", "#333333"]
    assert not (bench.store.root / one["id"] / "worn").exists() and not (bench.store.root / two["id"] / "worn").exists()
    bench.store.edit(one["id"], garment(name="s0", recipe={"colour": "#111111", "length": 0.9}))
    assert bench.dr.status(one["id"], npc)["state"] == "none" and bench.dr.status(two["id"], npc)["state"] == "cancelled"    # (a stop of another garment says nothing about this one; a stop of a garment still as it was does)


def test_fits_are_made_one_at_a_time_in_the_order_they_were_asked_for(bench):
    live, most, order = [], [], []
    lock = threading.Lock()

    def counted(body, pieces, cancel=None):
        with lock:
            live.append(1)
            most.append(len(live))
            order.append(pieces[0]["colour"])
        time.sleep(0.05)
        with lock:
            live.pop()
        return clothing.build(pieces[0])[0], worn_report(pieces[0])

    bench.dr.fit = counted
    npc = designed(bench.lib)
    made = [bench.store.create(garment(name=f"s{i}", recipe={"colour": f"#00000{i}"})) for i in range(4)]
    for item in made:
        bench.dr.request(item["id"], npc)
    settle(bench.dr)
    assert max(most) == 1 and order == [f"#00000{i}" for i in range(4)] and all(bench.dr.status(m["id"], npc)["state"] == "done" for m in made)


def test_a_late_fit_for_an_item_deleted_meanwhile_does_not_bring_its_folder_back(bench):
    started, release = threading.Event(), threading.Event()

    def slow(body, pieces, cancel=None):
        started.set()
        release.wait(10)
        return clothing.build(pieces[0])[0], worn_report(pieces[0])

    bench.dr.fit = slow
    item = bench.store.create(garment())
    npc = designed(bench.lib)
    bench.dr.request(item["id"], npc)
    assert started.wait(10)
    bench.store.delete(item["id"])
    release.set()
    settle(bench.dr)
    assert not (bench.store.root / item["id"]).exists()
    with pytest.raises(KeyError):
        bench.dr.status(item["id"], npc)


def test_deleting_a_character_takes_its_fits_out_of_every_item_and_deleting_an_item_forgets_it(bench):
    one, npc = fitted(bench)
    two = bench.store.create(garment(name="Other"))["id"]
    bench.dr.request(two, npc)
    settle(bench.dr)
    keep = designed(bench.lib, name="kept")
    bench.dr.request(one, keep)
    settle(bench.dr)
    for item_id in (one, two):
        assert (bench.store.root / item_id / "worn" / f"{npc}.glb").is_file()
    bench.dr.forget_npc(npc)
    for item_id in (one, two):
        assert not (bench.store.root / item_id / "worn" / f"{npc}.glb").exists() and not (bench.store.root / item_id / "worn" / f"{npc}.json").exists()
    assert (bench.store.root / one / "worn" / f"{keep}.glb").is_file()                                      # (another character's is kept)
    bench.dr.forget_item(one)
    assert bench.dr.status(one, keep)["state"] == "done"                                                    # (the files are the record: forgetting is only the memory of a run)


def test_a_fit_asked_for_a_character_deleted_while_it_waited_is_dropped(bench):
    started, release = threading.Event(), threading.Event()

    def slow(body, pieces, cancel=None):
        started.set()
        release.wait(10)
        return clothing.build(pieces[0])[0], worn_report(pieces[0])

    bench.dr.fit = slow
    first, second = bench.store.create(garment(name="a")), bench.store.create(garment(name="b"))
    npc, gone = designed(bench.lib), designed(bench.lib, name="gone")
    bench.dr.request(first["id"], npc)
    assert started.wait(10)
    bench.dr.request(second["id"], gone)
    bench.lib.delete(gone)
    release.set()
    settle(bench.dr)
    assert bench.dr.status(first["id"], npc)["state"] == "done" and not (bench.store.root / second["id"] / "worn").exists()


# ---- the process --------------------------------------------------------------------------------------------------------------------------------
def test_the_fit_runs_as_a_command_in_a_process_of_its_own_and_says_what_the_body_designer_or_the_process_said():
    body, report = dressing.fit_process(b"this is not a body", [clothing.clean_recipe({})])
    assert body == b"this is not a body" and report["worn"] == [] and report["replaced"] == [] and list(report["failed"]) == ["shirt"]      # (a garment that cannot be fitted is said, not raised)
    assert "garment.body" in report["failed"]["shirt"] or "GLB" in report["failed"]["shirt"] or "Not a GLB" in report["failed"]["shirt"]
    with pytest.raises(dressing.DressingError, match="Fitting the clothes failed"):
        dressing.fit_process(b"this is not a body", [{"colour": "#000000"}])                                    # (a request that is not one: the process itself stops, and says so)


def test_a_process_that_is_stopped_raises_cancelled():
    stopped = threading.Event()
    stopped.set()
    with pytest.raises(Cancelled):
        dressing.fit_process(b"this is not a body", [clothing.clean_recipe({})], stopped)


# ---- the jacket and the apron ---------------------------------------------------------------------------------------------------------------------------
def test_a_jacket_or_an_apron_is_fitted_only_to_a_body_made_with_a_shirt_and_trousers_and_the_page_is_told_why(bench):
    item = bench.store.create(garment(kind="jacket"))
    bare = designed(bench.lib)
    status = bench.dr.status(item["id"], bare)
    assert status["eligible"] is False and status["why"].startswith("The open jacket goes over a shirt and trousers only") and status["why"].endswith("this body wears neither")
    with pytest.raises(ValueError, match="this body wears neither"):
        bench.dr.request(item["id"], bare)
    assert not bench.calls and bench.dr.status(item["id"], bare)["state"] == "none"
    only_shirt = designed(bench.lib, name="shirted", clothes=("shirt",))
    assert bench.dr.status(item["id"], only_shirt)["why"].endswith("this body wears no trousers")
    dressed = designed(bench.lib, name="dressed", clothes=("shirt", "trousers"))
    assert bench.dr.status(item["id"], dressed)["eligible"] is True
    bench.dr.request(item["id"], dressed)
    settle(bench.dr)
    done = bench.dr.status(item["id"], dressed)
    assert done["state"] == "done" and done["fit"]["kind"] == "jacket" and done["fit"]["cut"] == 2 and bench.calls[0]["pieces"][0]["kind"] == "jacket"
    assert done["fit"]["replaced"] == [] and len(bench.calls) == 1
    apron = bench.store.create(garment(kind="apron", name="Test apron"))
    jacketed = designed(bench.lib, name="jacketed", clothes=("shirt", "trousers", "jacket"))
    assert bench.dr.status(apron["id"], jacketed)["eligible"] is True                       # (an apron takes the jacket off: a body wears one outer garment)
    assert bench.dr.status(item["id"], designed(bench.lib, name="aproned", clothes=("shirt", "trousers", "apron")))["eligible"] is True
    shirt = bench.store.create(garment(name="Another shirt"))
    assert bench.dr.status(shirt["id"], jacketed)["eligible"] is True and bench.dr.status(shirt["id"], bare)["eligible"] is True


def test_a_colour_changed_after_a_jackets_fit_goes_onto_its_cloth_and_needs_no_new_fit(bench):
    item = bench.store.create(garment(kind="jacket", recipe={"colour": "#a02020"}))
    npc = designed(bench.lib, clothes=("shirt", "trousers"))
    bench.dr.request(item["id"], npc)
    settle(bench.dr)
    glb, _ = bench.dr.worn(item["id"], npc)
    assert all(abs(a - b) <= 6 for a, b in zip(colour_of(glb, "ForgeJacketFabric"), (0xa0, 0x20, 0x20)))
    bench.store.edit(item["id"], garment(kind="jacket", recipe={"colour": "#2020a0"}))
    status = bench.dr.status(item["id"], npc)
    assert status["state"] == "done" and status["recoloured"] is True and status["stale"] is False
    recoloured, _ = bench.dr.worn(item["id"], npc)
    assert all(abs(a - b) <= 6 for a, b in zip(colour_of(recoloured, "ForgeJacketFabric"), (0x20, 0x20, 0xa0))) and len(bench.calls) == 1


def test_a_shirt_looser_than_the_jacket_the_body_wears_was_checked_over_is_not_offered(bench, monkeypatch):
    from bodydesigner import garments
    real = garments.catalogue()
    monkeypatch.setattr(garments, "catalogue", lambda: [{**e, "layer_margins": {"shirt_ease_mm": 10, "trousers_ease_mm": 10}} if e["id"] == "jacket" else e for e in real])
    loose = bench.store.create(garment(recipe={"ease_mm": 12}))
    snug = bench.store.create(garment(recipe={"ease_mm": 10}, name="Snug shirt"))
    npc = designed(bench.lib, clothes=("shirt", "trousers", "jacket"))
    status = bench.dr.status(loose["id"], npc)
    assert status["eligible"] is False and status["why"] == "The open jacket has been checked over a shirt no looser than 10 mm, and this one is 12 mm"
    with pytest.raises(ValueError, match="no looser than 10 mm"):
        bench.dr.request(loose["id"], npc)
    assert bench.dr.status(snug["id"], npc)["eligible"] is True and not bench.calls


# ---- the skirt ------------------------------------------------------------------------------------------------------------------------------------------
def test_a_skirt_is_fitted_to_any_designed_body_in_place_of_the_trousers_and_a_jacket_or_an_apron_is_not_fitted_to_a_body_in_a_skirt(bench):
    skirt = bench.store.create(garment(kind="skirt", name="Test skirt"))
    for number, clothes in enumerate((("shirt", "trousers"), ("shirt",), (), ("shirt", "trousers", "jacket"), ("shirt", "trousers", "apron"))):
        assert bench.dr.status(skirt["id"], designed(bench.lib, name=f"wearer{number}", clothes=clothes))["eligible"] is True, clothes       # (what it replaces comes off, and so does an outer garment it was never checked with)
    skirted = designed(bench.lib, name="skirted", clothes=("shirt", "skirt"))
    for kind in ("jacket", "apron"):
        item = bench.store.create(garment(kind=kind, name=f"Test {kind}"))
        status = bench.dr.status(item["id"], skirted)
        assert status["eligible"] is False and status["why"].endswith("and this body wears a skirt") and "only (nothing else has been checked with it)" in status["why"], (kind, status["why"])
        with pytest.raises(ValueError, match="this body wears a skirt"):
            bench.dr.request(item["id"], skirted)
    assert not bench.calls
    trousers = bench.store.create(garment(kind="trousers", name="Test trousers"))
    assert bench.dr.status(trousers["id"], skirted)["eligible"] is True                       # (trousers take the skirt off)
    dressed = designed(bench.lib, name="dressed", clothes=("shirt", "trousers"))
    bench.dr.request(skirt["id"], dressed)
    settle(bench.dr)
    done = bench.dr.status(skirt["id"], dressed)
    assert done["state"] == "done" and done["fit"]["kind"] == "skirt" and done["fit"]["cut"] == 2 and bench.calls[0]["pieces"][0]["kind"] == "skirt"
    assert done["fit"]["replaced"] == ["ForgeTrousers"] and len(bench.calls) == 1


def test_the_command_line_says_how_far_off_the_underwear_a_garment_stands_only_when_there_is_underwear(monkeypatch, capsys):
    import types
    from npcgen import cli
    fit = {"kind": "shirt", "cut": 3, "triangles": 2819, "over_underwear_mm": 3.05, "seconds": 15.3, "replaced": []}
    dressings = types.SimpleNamespace(request=lambda item, npc, force=False: {"state": "done", "fit": fit}, status=lambda item, npc: {"state": "done", "fit": fit})
    monkeypatch.setattr(cli, "find_item", lambda app, key: {"id": "shirt-1"})
    monkeypatch.setattr(cli, "find_npc", lambda app, key: {"id": "ted-1"})
    app = types.SimpleNamespace(dressings=dressings)
    args = types.SimpleNamespace(id="shirt-1", character="ted-1", refit=False, out=None)
    assert cli.cmd_item_dress(args, app) == 0
    assert "2,819 triangles, 3.05 mm off the underwear, fitted in 15.3 s" in capsys.readouterr().out
    fit["over_underwear_mm"] = None                                                         # (a body that wears no underwear: nothing to stand off)
    assert cli.cmd_item_dress(args, app) == 0
    said = capsys.readouterr().out
    assert "2,819 triangles, fitted in 15.3 s" in said and "None" not in said and "underwear" not in said
