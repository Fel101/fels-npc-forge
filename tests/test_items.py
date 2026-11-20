"""Items: the tables, the prompts, the plan, the store on disk, the scaling of a model, the job steps and the API."""
import copy
import io
import json
import struct
import threading
import time
import types
import urllib.error
import urllib.request
import zipfile

import numpy as np
import pytest
from PIL import Image

from npcgen import cleanup3d, cli, clothing, config, dressing, engines, glasses, itemmaker, itemposter, items, roll, three_d
from npcgen.comfy import ComfyError
from npcgen.itemstore import ItemStore
from npcgen.jobs import Job, Jobs
from npcgen.library import Library
from npcgen.paint3d import Glb, all_triangles

VIEWS = three_d.PIXAL_SHEET_VIEWS


# ---- a tiny model to scale -----------------------------------------------------------------------------------------------------

def _box(x=(0, 2), y=(0, 4), z=(0, 1), extra=()):
    positions = [(a, b, c) for a in x for b in y for c in z]            # index = 4 * i + 2 * j + k
    triangles = []
    for a, b, c, d in ((0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)):
        triangles += [(a, b, c), (a, c, d)]
    for point_a, point_b, point_c in extra:
        base = len(positions)
        positions += [point_a, point_b, point_c]
        triangles.append((base, base + 1, base + 2))
    return positions, triangles


def _glb(positions, triangles):
    pos = np.asarray(positions, np.float32)
    blob = pos.tobytes() + np.asarray(triangles, np.uint32).ravel().tobytes()
    doc = {"asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0}],
           "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "indices": 1}]}],
           "accessors": [{"bufferView": 0, "componentType": 5126, "count": len(pos), "type": "VEC3",
                          "min": pos.min(0).tolist(), "max": pos.max(0).tolist()},
                         {"bufferView": 1, "componentType": 5125, "count": len(triangles) * 3, "type": "SCALAR"}],
           "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": pos.nbytes},
                           {"buffer": 0, "byteOffset": pos.nbytes, "byteLength": len(blob) - pos.nbytes}],
           "buffers": [{"byteLength": len(blob)}]}
    text = json.dumps(doc).encode()
    text += b" " * (-len(text) % 4)
    return (struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(text) + 8 + len(blob)) + struct.pack("<II", len(text), 0x4E4F534A) + text
            + struct.pack("<II", len(blob), 0x004E4942) + blob)


def _world(glb_bytes):
    pos, _tri = all_triangles(Glb(glb_bytes))
    return pos.min(0), pos.max(0)


def _png(colour=(200, 120, 60), size=(64, 64)):
    out = io.BytesIO()
    Image.new("RGB", size, colour).save(out, "PNG")
    return out.getvalue()


# ---- the tables ----------------------------------------------------------------------------------------------------------------

def test_every_kind_is_complete_and_its_pose_exists():
    data = items.load()
    assert set(data["views"]) == set(VIEWS) and items.LOOK_DEFAULT in data["looks"] and all(look["phrase"] for look in data["looks"].values())
    for fid, family in data["families"].items():
        assert family["label"] and family["blurb"] and family["kinds"], fid
        labels = [k["label"] for k in family["kinds"].values()]
        assert len(labels) == len(set(labels)), fid
        for kid, kind in family["kinds"].items():
            assert kind["pose"] in data["poses"] and kind["pivot"] in ("base", "centre", "grip", "bridge", "floor"), (fid, kid)
            assert kind["noun"] and items.SIZE_RANGE[0] <= kind["size_m"] <= items.SIZE_RANGE[1], (fid, kid)
            assert kind["hint"] or kid == "other", (fid, kid)
            if kind["pivot"] == "grip":
                assert 0 < kind["grip_at"] < 1 and kind["pose"] == "upright", (fid, kid)     # a grip is a height, so the thing stands up


def test_the_catalogue_lists_what_the_tab_offers_in_the_tables_order():
    shown = items.catalogue()
    assert [f["id"] for f in shown["families"]] == ["weapon", "armor", "headwear", "eyewear", "footwear", "bag", "prop", "garment"] + (["clothing"] if clothing.available() else [])
    weapon = shown["families"][0]
    assert weapon["kinds"][0]["id"] == "sword" and {"label", "hint", "pose", "size_m", "pivot", "attach"} <= set(weapon["kinds"][0])
    assert [f["value"] for f in shown["faces"]] == list(items.FACES) and shown["default_faces"] in items.FACES
    assert shown["best"] == items.BEST_ENGINES and [s["name"] for s in shown["steps"]] == ["hero", "views", "shape", "high", "finish"]


# ---- what the editor sends -----------------------------------------------------------------------------------------------------

def test_a_valid_item_is_cleaned_named_and_given_its_kinds_size():
    fields = items.clean_item({"family": "weapon", "kind": "cutlass", "description": "  curved   steel blade, brass guard  ",
                               "setting": "pirates", "look": "stylised", "id": "evil", "assets": {"x": 1}, "seed": 4})
    assert fields == {"name": "Cutlass", "family": "weapon", "kind": "cutlass", "description": "curved steel blade, brass guard",
                      "setting": "pirates", "look": "stylised", "size_m": 0.85, "faces": items.DEFAULT_FACES, "high_views": False}
    assert items.clean_item({})["kind"] == "sword"                              # the first of the first family
    other = items.clean_item({"family": "prop", "kind": "other", "description": "an old brass spyglass with a leather grip"})
    assert other["name"] == "An old brass spyglass" and other["size_m"] == 0.5
    assert items.clean_item({"family": "prop", "kind": "other", "name": "Spyglass"})["name"] == "Spyglass"


@pytest.mark.parametrize("raw, message", [
    ("not an object", "must be an object"),
    ({"family": "vehicle"}, "Unknown family"),
    ({"family": "weapon", "kind": "boots"}, "Unknown kind"),
    ({"setting": "narnia"}, "Unknown setting"),
    ({"look": "neon"}, "Unknown look"),
    ({"description": "x" * 501}, "at most 500"),
    ({"name": 7}, "must be text"),
    ({"size_m": 0}, "between"),
    ({"size_m": 9}, "between"),
    ({"size_m": "big"}, "must be a number"),
    ({"size_m": True}, "must be a number"),
    ({"faces": 12345}, "must be one of"),
    ({"family": "prop", "kind": "other"}, "Name what this is"),
])
def test_bad_items_are_refused_with_a_reason(raw, message):
    with pytest.raises(ValueError, match=message):
        items.clean_item(raw)


# ---- prompts -------------------------------------------------------------------------------------------------------------------

def _item(**more):
    base = {"id": "x", "name": "Cutlass", "family": "weapon", "kind": "cutlass", "description": "curved steel blade with a brass guard",
            "setting": "", "look": "realistic", "size_m": 0.85, "faces": 30000, "seed": 5, "assets": {}}
    return {**base, **more}


def test_the_main_picture_is_a_studio_shot_of_the_one_object():
    prompt = items.hero_prompt(_item())
    assert prompt["positive"].startswith("a cutlass: curved steel blade with a brass guard, standing upright")
    assert "plain white background" in prompt["positive"] and "realistic materials" in prompt["positive"]
    assert "person" in prompt["negative"] and "shadow on the floor" in prompt["negative"]
    assert items.hero_prompt(_item(description=""))["positive"].startswith("a cutlass: a curved single-edged steel blade")   # the kind's own words


def test_a_pair_a_garment_and_a_custom_thing_are_staged_and_named_their_own_way():
    boots = items.hero_prompt(_item(family="footwear", kind="boots", description="worn brown leather"))
    assert boots["positive"].startswith("a pair of boots: worn brown leather, a matching pair standing side by side")
    assert "the single pair" in boots["positive"] and "a single one" in boots["negative"]
    coat = items.hero_prompt(_item(family="garment", kind="coat", description="grey wool"))
    assert "ghost mannequin" in coat["positive"] and "single garment" in coat["positive"]
    assert "mannequin" not in coat["negative"].split(", ") and "face" in coat["negative"]     # the base's "no mannequin" would fight the pose
    assert "mannequin" in items.hero_prompt(_item())["negative"].split(", ")
    other = items.hero_prompt(_item(family="prop", kind="other", name="Spyglass", description="brass, with a leather grip"))
    assert other["positive"].startswith("a spyglass: brass") or other["positive"].startswith("a Spyglass: brass")
    assert items.hero_prompt(_item(family="headwear", kind="helmet", description=""))["positive"].startswith("a helmet:")


def test_the_setting_and_the_look_change_the_words():
    pirate = items.hero_prompt(_item(setting="pirates", look="voxel"))
    assert "designed for a pirates setting" in pirate["positive"] and "cube-shaped blocks" in pirate["positive"]
    assert "smooth" in pirate["negative"].split(", ")
    assert "setting" not in items.hero_prompt(_item())["positive"]
    assert items.hero_prompt(_item(look="unknown look"))["positive"].count("realistic materials") == 1        # an unknown look is the default


def test_a_thing_with_no_front_is_asked_for_with_the_wording_the_first_runs_used():
    prompts = {v: items.view_prompt(_item(), v)["positive"] for v in VIEWS}
    assert prompts["front"] == ("Show the exact same cutlass again, seen straight from the front. Keep its shape, proportions, colours and materials "
                                "exactly the same, the whole object in frame, the same plain white background, no shadow, no person.")
    assert "seen straight from its left side (turned 90 degrees)" in prompts["side_left"] and "from behind" in prompts["back"]
    assert "seen straight from its right side (turned 90 degrees)" in prompts["side_right"]
    barrel = {v: items.view_prompt(_item(family="prop", kind="barrel"), v)["positive"] for v in VIEWS}
    assert barrel["side_left"].startswith("Show the exact same barrel again, seen straight from its left side")
    assert not any("front" in p.replace("from the front", "") for p in barrel.values())


def test_a_thing_with_a_front_is_told_which_way_it_points_in_the_side_views():
    boots = {v: items.view_prompt(_item(family="footwear", kind="boots"), v)["positive"] for v in VIEWS}
    assert boots["front"].startswith("Show the exact same pair of boots again. The toe end is the front. It is seen straight from the front.")
    assert "front points to the left of the picture" in boots["side_left"] and "front points to the right of the picture" in boots["side_right"]
    assert boots["side_left"] != boots["side_right"] and "from behind" in boots["back"]               # (the pair came back with the same side twice)
    helmet = items.view_prompt(_item(family="headwear", kind="helmet"), "side_right")["positive"]
    assert "The face opening is the front." in helmet and "points to the right of the picture" in helmet


def test_things_that_stand_up_have_no_front_because_telling_a_cutlass_which_way_it_points_laid_it_on_its_side():
    data = items.load()
    assert set(data["views_facing"]) == {"side_left", "side_right"}
    for kind in data["families"]["weapon"]["kinds"].values():
        assert "front" not in kind
    assert "front" not in data["families"]["armor"]["kinds"]["shield"]
    for family in data["families"].values():                                   # a front, when a kind has one, is a short clause
        for kind in family["kinds"].values():
            assert kind.get("front", "x").strip() and kind.get("front", "").count(".") == 0


# ---- the request and its steps -------------------------------------------------------------------------------------------------

def _made(hero=True, views=True, shape=True, finish=True, stale=()):
    assets = {}
    if hero:
        assets["hero"] = {"file": "hero.png", "created": "h"}
    if views:
        assets["views"] = {v: {"file": f"views/{v}.png", "created": f"c-{v}"} for v in VIEWS}
    if shape:
        assets["model_3d"] = {"file": "model_3d.glb", "raw": "model_3d_original.glb", "created": "m",
                              **({"refined": {"created": "r"}} if finish else {})}
    for kind in stale:
        meta = assets["views"]["front"] if kind == "views" else assets["hero" if kind == "hero" else "model_3d"]
        meta["stale"] = True
    return _item(assets=assets)


def _kinds(steps):
    return [s["kind"] for s in steps]


def test_an_empty_item_made_in_one_go_is_four_steps_and_painting_can_be_left_out():
    assert _kinds(items.plan(_item(), {"make": True})) == ["item_hero", "item_views", "item_shape", "item_finish"]
    assert _kinds(items.plan(_item(), {"make": True, "finish": False})) == ["item_hero", "item_views", "item_shape"]
    step = items.plan(_item(), {"make": True})[0]
    assert step == {"kind": "item_hero", "key": "hero", "label": "Main picture", "status": "pending", "error": None}


def test_make_does_what_is_missing_or_out_of_date_and_everything_after_it():
    assert _kinds(items.plan(_made(finish=False), {"make": True})) == ["item_finish"]
    assert _kinds(items.plan(_made(shape=False, finish=False), {"make": True})) == ["item_shape", "item_finish"]
    assert _kinds(items.plan(_made(stale=("hero",)), {"make": True})) == ["item_hero", "item_views", "item_shape", "item_finish"]
    assert _kinds(items.plan(_made(stale=("views",)), {"make": True})) == ["item_views", "item_shape", "item_finish"]
    assert _kinds(items.plan(_made(stale=("model",)), {"make": True})) == ["item_shape", "item_finish"]
    done = _made()
    done["assets"]["model_3d"]["game_stale"] = True                             # a new game-version size: only the finish again
    assert _kinds(items.plan(done, {"make": True})) == ["item_finish"]


def test_nothing_to_make_is_an_error_not_an_empty_job():
    with pytest.raises(ValueError, match="nothing to make"):
        items.plan(_made(), {"make": True})
    with pytest.raises(ValueError, match="nothing to make"):
        items.plan(_made(finish=False), {"make": True, "finish": False})
    with pytest.raises(ValueError, match="nothing to make"):
        items.plan(_made(), {})


def test_redo_makes_one_part_again_and_brings_along_what_it_cannot_start_without():
    assert _kinds(items.plan(_made(), {"redo": ["shape"]})) == ["item_shape"]
    assert _kinds(items.plan(_made(), {"redo": ["finish"]})) == ["item_finish"]
    assert _kinds(items.plan(_made(), {"redo": ["hero", "finish"]})) == ["item_hero", "item_finish"]
    assert _kinds(items.plan(_item(), {"redo": ["finish"]})) == ["item_hero", "item_views", "item_shape", "item_finish"]
    assert _kinds(items.plan(_made(hero=False, views=False, shape=False, finish=False), {"redo": ["shape"]})) == ["item_hero", "item_views", "item_shape"]
    assert _kinds(items.plan(_made(shape=False, finish=False), {"redo": ["views"]})) == ["item_views"]


def test_a_request_is_checked():
    assert items.clean_request(None) == {"make": False, "finish": True, "redo": []}
    assert items.clean_request({"make": True, "finish": False, "redo": ["finish", "hero", "hero"]}) == {"make": True, "finish": False, "redo": ["hero", "finish"]}
    for bad in ({"redo": ["everything"]}, {"redo": "hero"}, "make"):
        with pytest.raises(ValueError):
            items.clean_request(bad)


def test_best_quality_pins_the_engines_and_otherwise_the_models_settings_are_used():
    cfg = config.DEFAULTS
    best = items.clean_options({"image_model": "mage", "edit_model": "mage", "seed": "7", "reroll": True}, cfg)
    assert best["best"] is True and (best["image_model"], best["edit_model"]) == ("zimage", "qwen2511") and best["seed"] == 7 and best["reroll"]
    mine = items.clean_options({"best": False, "image_model": "zimage", "edit_model": "bogus", "three_d_quality": "low"}, cfg)
    assert mine["best"] is False and (mine["image_model"], mine["edit_model"], mine["three_d_quality"]) == ("zimage", "mage", "low")
    assert items.clean_options(None, cfg)["quality"] == "standard"
    with pytest.raises(ValueError, match="whole number"):
        items.clean_options({"seed": "lots"}, cfg)


def test_without_best_an_engine_that_is_not_named_is_the_apps_default_one():
    """The command line names none unless asked; the default is Qwen (jobs.DEFAULT_OPTIONS), or what config.json's "options" says."""
    cfg = config.DEFAULTS
    plain = items.clean_options({"best": False}, cfg)
    assert (plain["image_model"], plain["edit_model"]) == ("qwen2512", "qwen2511")
    assert items.clean_options({"best": False, "image_model": None, "edit_model": ""}, cfg)["image_model"] == "qwen2512"
    configured = items.clean_options({"best": False}, {**cfg, "options": {"image_model": "mage", "edit_model": "mage"}})
    assert (configured["image_model"], configured["edit_model"]) == ("mage", "mage")
    named = items.clean_options({"best": False, "image_model": "zimage"}, cfg)
    assert (named["image_model"], named["edit_model"]) == ("zimage", "qwen2511")


def test_the_tab_says_what_it_is_missing():
    assert items.availability({"online": False})["missing"][0] == "ComfyUI"
    status = {"online": True, "three_d_missing": {"trellis2": ["node A", "model B"]}, "rig_3d": {"missing": ["Blender"]}}
    assert items.availability(status) == {"missing": ["node A", "model B", "Blender"]}
    assert items.availability({"online": True, "three_d_missing": {"trellis2": []}, "rig_3d": {"missing": []}}) == {"missing": []}


# ---- size and origin ---------------------------------------------------------------------------------------------------------------

def test_the_model_is_scaled_so_its_longest_side_is_the_size_and_stands_on_its_base():
    out, info = items.fit_glb(_glb(*_box()), 1.0, "base")
    low, high = _world(out)
    assert np.allclose(high - low, [0.5, 1.0, 0.25], atol=1e-5)                          # 2 x 4 x 1 scaled to 1 m
    assert np.allclose(low, [-0.25, 0, -0.125], atol=1e-5) and np.allclose(high, [0.25, 1, 0.125], atol=1e-5)
    assert info["scale"] == 0.25 and info["size_m"] == [0.5, 1.0, 0.25] and info["origin"] == "base"


def test_the_origin_can_be_the_centre_or_the_grip():
    low, high = _world(items.fit_glb(_glb(*_box()), 1.0, "centre")[0])
    assert np.allclose((low + high) / 2, 0, atol=1e-5)
    stick = _box(extra=[((0.2, 1, 0.5), (0.4, 1, 0.5), (0.3, 1, 0.6))])                 # a few vertices at the grip's height
    out, info = items.fit_glb(_glb(*stick), 1.0, "grip", 0.25)
    low, high = _world(out)
    assert np.isclose(low[1], -0.25, atol=1e-5) and np.isclose(high[1], 0.75, atol=1e-5)  # the grip (a quarter up) is at the origin
    assert info["origin"] == "grip" and info["grip_at"] == 0.25
    pos, _ = all_triangles(Glb(out))
    grip = pos[-3:]                                                                       # the three vertices put at the grip's height
    assert np.allclose(grip[:, 1], 0, atol=1e-5) and np.isclose(grip[:, 0].mean(), 0, atol=1e-5)    # their middle is under the hand


def test_fitting_again_changes_the_same_node_instead_of_stacking_another():
    once, _ = items.fit_glb(_glb(*_box()), 1.0, "base")
    twice, info = items.fit_glb(once, 2.0, "base")
    doc = Glb(twice).json
    assert [n.get("name") for n in doc["nodes"]].count(items.FIT_NODE) == 1 and len(doc["scenes"][0]["nodes"]) == 1
    low, high = _world(twice)
    assert np.allclose(high - low, [1.0, 2.0, 0.5], atol=1e-5) and np.isclose(low[1], 0, atol=1e-6) and info["scale"] == 0.5


def test_a_model_with_nothing_in_it_is_not_scaled():
    empty = _glb(np.zeros((3, 3)), [(0, 1, 2)])
    with pytest.raises(ValueError, match="no size"):
        items.fit_glb(empty, 1.0)


# ---- the store -----------------------------------------------------------------------------------------------------------------------------

@pytest.fixture()
def store(tmp_path):
    lib = Library(tmp_path / "library")
    yield ItemStore(lib), lib


def _create(store, **more):
    return store.create(items.clean_item({"family": "weapon", "kind": "cutlass", "description": "curved blade", **more}))


def test_an_item_is_created_listed_read_and_kept_apart_from_the_characters(store):
    items_store, lib = store
    item = _create(items_store)
    assert item["id"].startswith("cutlass-") and item["seed"] >= 0 and item["assets"] == {}
    assert (items_store.root / item["id"] / "item.json").is_file() and items_store.root.name == ".npcforge-items"
    assert [s["id"] for s in items_store.list()] == [item["id"]] and items_store.get(item["id"])["name"] == "Cutlass"
    card = items_store.list()[0]
    assert card["kind_label"] == "Cutlass" and card["has"] == {"hero": False, "views": 0, "high": 0, "model_3d": False, "finished": False} and not card["stale"]
    assert lib.list() == [] and lib.refresh_index() == []                              # a character list never sees an item
    again = ItemStore(lib)                                                             # and a restart finds it again
    assert [s["id"] for s in again.list()] == [item["id"]]


def test_a_path_cannot_leave_the_items_folder(store):
    items_store, _lib = store
    item = _create(items_store)
    for rel in ("../../escape.txt", "..\\..\\escape.txt", "/etc/passwd"):
        with pytest.raises(KeyError):
            items_store.resolve(item["id"], rel)
    for bad_id in ("../x", "A B", "", None):
        with pytest.raises(KeyError):
            items_store.get(bad_id)
    with pytest.raises(KeyError):
        items_store.get("no-such-item-123456")


def test_two_items_with_the_same_name_get_different_ids(store):
    items_store, _lib = store
    assert _create(items_store)["id"] != _create(items_store)["id"]


def _with_everything(items_store, item):
    store_ = items_store
    glb = _glb(*_box())
    fitted, fit = items.fit_glb(glb, item["size_m"], "grip", 0.2)
    store_.write_asset(item["id"], "hero.png", _png())
    for view in VIEWS:
        store_.write_asset(item["id"], f"views/{view}.png", _png())
    store_.write_asset(item["id"], "model_3d.glb", fitted)
    store_.write_asset(item["id"], "model_3d_original.glb", glb)
    store_.write_asset(item["id"], "model_3d_preview.png", _png((10, 10, 10)))
    store_.update(item["id"], lambda n: n["assets"].update(
        hero={"file": "hero.png", "created": "h"}, views={v: {"file": f"views/{v}.png", "created": f"c-{v}"} for v in VIEWS},
        model_3d={"file": "model_3d.glb", "raw": "model_3d_original.glb", "preview": "model_3d_preview.png", "created": "m",
                  "refined": {"created": "r", "faces": 100}, "fit": fit}))
    store_.make_thumb(item["id"])
    return store_.get(item["id"])


def test_changing_what_the_pictures_are_drawn_from_marks_them_and_the_model_out_of_date(store):
    items_store, _lib = store
    item = _with_everything(items_store, _create(items_store))
    assert items_store.list()[0]["thumb"] and not items_store.list()[0]["stale"]
    edited = items_store.edit(item["id"], items.clean_item({**item, "description": "a different blade"}))
    assets = edited["assets"]
    assert assets["hero"]["stale"] and all(v["stale"] for v in assets["views"].values()) and assets["model_3d"]["stale"]
    assert items_store.list()[0]["stale"] is True
    renamed = items_store.edit(item["id"], items.clean_item({**edited, "name": "Captain's blade"}))
    assert renamed["name"] == "Captain's blade" and renamed["assets"]["hero"]["stale"]          # (still out of date from before)


def test_a_new_name_alone_leaves_everything_fresh_and_a_new_game_size_only_asks_for_the_finish(store):
    items_store, _lib = store
    item = _with_everything(items_store, _create(items_store))
    renamed = items_store.edit(item["id"], items.clean_item({**item, "name": "Old faithful"}))
    assert renamed["name"] == "Old faithful" and not items_store.list()[0]["stale"]
    bigger = items_store.edit(item["id"], items.clean_item({**renamed, "faces": 100000}))
    assert bigger["assets"]["model_3d"]["game_stale"] is True and not bigger["assets"]["hero"].get("stale")
    assert items.state(bigger) == {"item_hero": True, "item_views": True, "item_shape": True, "item_high": True, "item_finish": False}
    assert [s["kind"] for s in items.plan(bigger, {"make": True})] == ["item_finish"]


def test_a_new_size_is_applied_to_the_model_at_once(store):
    items_store, _lib = store
    item = _with_everything(items_store, _create(items_store))
    before = _world(items_store.read_asset(item["id"], "model_3d.glb"))
    assert np.isclose((before[1] - before[0]).max(), 0.85, atol=1e-4)
    edited = items_store.edit(item["id"], items.clean_item({**item, "size_m": 0.4}))
    low, high = _world(items_store.read_asset(item["id"], "model_3d.glb"))
    assert np.isclose((high - low).max(), 0.4, atol=1e-4)
    fit = edited["assets"]["model_3d"]["fit"]
    assert fit["requested_m"] == 0.4 and "fit_stale" not in edited["assets"]["model_3d"]
    assert np.isclose(low[1], -0.08, atol=1e-4)                                        # and the grip (a fifth up) is still the origin


def test_taking_a_part_away_marks_what_was_made_from_it(store):
    items_store, _lib = store
    item = _with_everything(items_store, _create(items_store))
    after = items_store.delete_asset(item["id"], "hero")
    assert "hero" not in after["assets"] and not (items_store.root / item["id"] / "hero.png").exists()
    assert all(v["stale"] for v in after["assets"]["views"].values()) and after["assets"]["model_3d"]["stale"]
    assert items_store.list()[0]["thumb"] is True                                      # the model's preview takes the card
    after = items_store.delete_asset(item["id"], "views")
    assert "views" not in after["assets"] and not (items_store.root / item["id"] / "views").exists()
    after = items_store.delete_asset(item["id"], "model_3d")
    assert "model_3d" not in after["assets"]
    assert not any((items_store.root / item["id"] / name).exists() for name in ("model_3d.glb", "model_3d_original.glb", "model_3d_preview.png"))
    assert items_store.list()[0]["thumb"] is False
    with pytest.raises(KeyError):
        items_store.delete_asset(item["id"], "hero")
    with pytest.raises(KeyError):
        items_store.delete_asset(item["id"], "item.json")


def test_an_item_is_deleted_with_its_files(store):
    items_store, _lib = store
    item = _with_everything(items_store, _create(items_store))
    items_store.delete(item["id"])
    assert not (items_store.root / item["id"]).exists() and items_store.list() == []


def test_an_item_exports_as_a_zip_of_its_record_pictures_and_game_model(store):
    items_store, _lib = store
    item = _with_everything(items_store, _create(items_store))
    with zipfile.ZipFile(io.BytesIO(items_store.export_zip(item["id"]))) as z:
        names = set(z.namelist())
        record = json.loads(z.read(f"{item['id']}/item.json"))
    expected = {"item.json", "hero.png", "model_3d.glb", "model_3d_preview.png", *[f"views/{v}.png" for v in VIEWS]}
    assert names == {f"{item['id']}/{n}" for n in expected}
    assert "model_3d_original.glb" not in "".join(names) and record["name"] == "Cutlass"


# ---- the job steps ---------------------------------------------------------------------------------------------------------------------

class FakeComfy:
    def __init__(self, shape):
        self.uploads, self.graphs, self.shape = [], [], shape

    def upload_image(self, data, name, **kw):
        self.uploads.append((name, data))
        return f"{name} [temp]"

    def run_entry(self, graph, cancel=None, timeout=900, unload_models=False):
        self.graphs.append((graph, timeout, unload_models))
        return {"outputs": {"82": {"3d": [{"filename": "model.glb", "subfolder": "npcforge", "type": "output"}]},
                            "84": {"images": [{"filename": "preview.png", "subfolder": "npcforge", "type": "output"}]}}}

    def fetch_file(self, filename, subfolder="", image_type="temp", byte_range=None):
        return {"model.glb": self.shape, "preview.png": _png((1, 2, 3))}[filename]


@pytest.fixture()
def bench(tmp_path, monkeypatch):
    lib = Library(tmp_path / "library")
    store = ItemStore(lib)
    jobs = Jobs(copy.deepcopy(config.DEFAULTS), None, lib, items=store)
    comfy = FakeComfy(_glb(*_box()))
    jobs.comfy = comfy
    calls = {"image": [], "edit": [], "refine": []}

    def image(comfy_, engine_id, prompt, negative, width, height, seed, steps, cfg, cancel=None):
        calls["image"].append({"engine": engine_id, "prompt": prompt, "negative": negative, "size": (width, height), "seed": seed})
        return _png((250, 250, 250), (width, height)), {}

    def edit(comfy_, engine_id, refs, prompt, seed, steps, cfg, megapixels=1.0, size=None, negative="", like=None, cancel=None):
        calls["edit"].append({"engine": engine_id, "refs": refs, "prompt": prompt, "seed": seed, "size": size, "negative": negative})
        if engine_id == "qwen2511":
            return _png((240, 240, 240), size), {"engine_note": "Qwen-Image-Edit 2511 could not draw it: ComfyUI is missing x"}
        return _png((240, 240, 240), size), {}

    def refine(cfg, model_glb, views=None, faces=0, cancel=None, **extra):
        calls["refine"].append({"model": model_glb, "views": views, "faces": faces, "extra": extra})
        return _glb(*_box(x=(0, 1), y=(0, 3), z=(0, 1))), _png((9, 9, 9)), {
            "inner_skin": {"faces_before": 700000, "faces_removed": 300000}, "game_version": {"faces_after": faces},
            "paint": {"views": {v: {"outline_match": 0.95, "trust": 1.0} for v in views}}}

    monkeypatch.setattr(engines, "image", image)
    monkeypatch.setattr(engines, "edit", edit)
    monkeypatch.setattr(cleanup3d, "refine", refine)
    item = _create(store, description="curved blade")

    def run(kind, **options):
        job = types.SimpleNamespace(id="job1", item_id=item["id"], cancel=threading.Event(), cache={}, touch=lambda: None,
                                    options={"quality": "standard", "best": True, "image_model": "zimage", "edit_model": "qwen2511",
                                             "three_d_quality": "medium", "reroll": False, "seed": None, **options})
        run.step = {"kind": kind, "key": kind, "label": kind}
        jobs._run_step(job, run.step)
        return store.get(item["id"])
    yield store, item["id"], comfy, calls, run
    jobs.close()


def test_the_main_picture_is_drawn_with_the_image_engine_from_the_prompt_and_kept(bench):
    store, item_id, _comfy, calls, run = bench
    item = run("item_hero")
    call = calls["image"][0]
    assert call["engine"] == "zimage" and call["size"] == (1024, 1024) and call["seed"] == item["seed"]
    assert call["prompt"] == items.hero_prompt(item)["positive"] and call["negative"] == items.hero_prompt(item)["negative"]
    meta = item["assets"]["hero"]
    assert meta["file"] == "hero.png" and meta["seed"] == item["seed"] and meta["prompt"]["positive"] == call["prompt"] and "engine" not in meta
    assert Image.open(io.BytesIO(store.read_asset(item_id, "hero.png"))).size == (1024, 1024) and store.list()[0]["thumb"]


def test_a_redraw_uses_a_new_seed_and_marks_the_views_and_model_made_from_the_old_picture(bench):
    store, item_id, _comfy, calls, run = bench
    run("item_hero")
    store.update(item_id, lambda n: n["assets"].update(views={v: {"file": f"views/{v}.png", "created": "c"} for v in VIEWS},
                                                       model_3d={"file": "model_3d.glb", "created": "m"}))
    again = run("item_hero", reroll=True)
    assert calls["image"][1]["seed"] != calls["image"][0]["seed"] and again["seed"] == calls["image"][0]["seed"]   # the item keeps its own seed
    assert all(v["stale"] for v in again["assets"]["views"].values()) and again["assets"]["model_3d"]["stale"]
    assert run("item_hero", seed=77)["assets"]["hero"]["seed"] == 77


def test_the_four_views_are_edits_of_the_main_picture_and_say_what_they_were_drawn_from(bench):
    store, item_id, comfy, calls, run = bench
    hero = run("item_hero")["assets"]["hero"]
    item = run("item_views")
    assert [name for name, _d in comfy.uploads] == [f"{item_id}_hero.png"]
    assert [c["prompt"] for c in calls["edit"]] == [items.view_prompt(item, v)["positive"] for v in VIEWS]
    assert {c["engine"] for c in calls["edit"]} == {"qwen2511"} and {c["size"] for c in calls["edit"]} == {(1024, 1024)}
    assert {c["refs"] for c in calls["edit"]} == {f"{item_id}_hero.png [temp]"} and {c["seed"] for c in calls["edit"]} == {(item["seed"] + 1) % 2 ** 32}
    views = item["assets"]["views"]
    assert list(views) == list(VIEWS) and all(m["source_hero_created"] == hero["created"] for m in views.values())
    assert all(store.read_asset(item_id, m["file"]) for m in views.values()) and views["front"]["engine_note"].startswith("Qwen-Image-Edit 2511 could not")


def test_a_fallback_engine_is_said_on_the_step(bench):
    _store, _item_id, _comfy, _calls, run = bench
    run("item_hero")
    run("item_views")
    assert run.step["note"].startswith(itemmaker.ENGINE_NOTE + "Qwen-Image-Edit 2511 could not draw it")
    assert "detail" not in run.step


def test_the_views_need_the_picture_the_shape_needs_the_views_and_the_finish_needs_the_shape(bench):
    store, item_id, comfy, _calls, run = bench
    with pytest.raises(ComfyError, match="drawn from the main picture"):
        run("item_views")
    with pytest.raises(ComfyError, match="Trellis2 needs all four views"):
        run("item_shape")
    with pytest.raises(ComfyError, match="no 3D shape to paint"):
        run("item_finish")
    assert comfy.graphs == []


def test_the_shape_is_made_from_the_four_views_and_scaled_to_the_items_size(bench):
    store, item_id, comfy, _calls, run = bench
    run("item_hero")
    run("item_views")
    comfy.uploads.clear()
    item = run("item_shape", three_d_quality="low")
    assert [name for name, _d in comfy.uploads] == [f"{item_id}_trellis2_{v}.png" for v in VIEWS]
    graph, timeout, unload = comfy.graphs[0]
    names = {v: f"{item_id}_trellis2_{v}.png [temp]" for v in VIEWS}
    assert graph == three_d.trellis2_graph(names, item_id, "job1", item["seed"], quality="low") and timeout == 3600 and unload is True
    meta = item["assets"]["model_3d"]
    assert (meta["engine_id"], meta["textured"], meta["quality"], meta["quality_label"]) == ("trellis2", False, "low", "Low")
    assert meta["raw"] == "model_3d_original.glb" and store.read_asset(item_id, "model_3d_original.glb") == comfy.shape
    low, high = _world(store.read_asset(item_id, "model_3d.glb"))
    assert np.isclose((high - low).max(), 0.85, atol=1e-4) and np.isclose(low[1], -0.17, atol=1e-4)     # the grip (a fifth up) is the origin
    assert meta["fit"]["requested_m"] == 0.85 and meta["source_views"] == {v: item["assets"]["views"][v]["created"] for v in VIEWS}
    assert store.read_asset(item_id, "model_3d_preview.png") == _png((1, 2, 3))


def test_the_finish_paints_the_generators_own_shape_makes_the_game_version_and_scales_it(bench):
    store, item_id, comfy, calls, run = bench
    for kind in ("item_hero", "item_views", "item_shape"):
        run(kind)
    store.update(item_id, lambda n: n["assets"]["model_3d"].update(stale=True, game_stale=True, fit_error="old"))
    item = run("item_finish")
    call = calls["refine"][0]
    assert call["model"] == comfy.shape and call["faces"] == 30000 and list(call["views"]) == list(VIEWS) and call["extra"] == {}
    meta = item["assets"]["model_3d"]
    assert meta["textured"] is True and meta["refined"]["faces"] == 30000 and meta["refined"]["faces_generated"] == 700000
    assert meta["refined"]["painted_from"] == sorted(VIEWS) and meta["refined"]["paint"]["front"] == {"outline_match": 0.95, "trust": 1.0}
    assert not {"game_stale", "fit_error", "fit_stale"} & set(meta) and meta["preview"] == "model_3d_preview.png"
    low, high = _world(store.read_asset(item_id, "model_3d.glb"))
    assert np.isclose((high - low).max(), 0.85, atol=1e-4)
    assert store.read_asset(item_id, "model_3d_original.glb") == comfy.shape                              # the raw shape is not replaced
    assert items.state(item) == {"item_hero": True, "item_views": True, "item_shape": False, "item_high": True, "item_finish": False}   # (the shape was marked stale above)


def test_painting_again_starts_from_the_raw_shape_never_from_the_last_game_version(bench):
    store, item_id, comfy, calls, run = bench
    for kind in ("item_hero", "item_views", "item_shape", "item_finish"):
        run(kind)
    store.update(item_id, lambda n: n.update(faces=100000))
    run("item_finish")
    assert calls["refine"][1]["model"] == comfy.shape and calls["refine"][1]["faces"] == 100000


def test_a_deleted_item_stops_its_job_with_a_clear_message(bench):
    store, item_id, _comfy, _calls, run = bench
    store.delete(item_id)
    with pytest.raises(ComfyError, match="has been deleted"):
        run("item_hero")


def test_submitting_queues_the_planned_steps_as_an_item_job_and_the_item_is_busy_until_it_ends(tmp_path, monkeypatch):
    monkeypatch.setattr(Jobs, "_run_job", lambda self, job: None)              # the worker takes the job and does nothing with it
    lib = Library(tmp_path / "library")
    store = ItemStore(lib)
    jobs = Jobs(copy.deepcopy(config.DEFAULTS), None, lib, items=store)
    try:
        item = _create(store)
        assert not jobs.busy_item(item["id"])
        record = jobs.submit_item(item["id"], {"make": True}, items.clean_options({}, jobs.cfg))
        assert record["target_type"] == "item" and record["item_id"] == item["id"] and record["npc_id"] is None
        assert record["npc_name"] == "Cutlass" and [s["key"] for s in record["steps"]] == ["hero", "views", "shape", "finish"]
        assert jobs.busy_item(item["id"]) and not jobs.busy_item("someone-else-123456")
        rebuilt = Job.from_record(json.loads(json.dumps(jobs.jobs[record["id"]].to_record())))
        assert rebuilt.item_id == item["id"] and rebuilt.target_type == "item" and rebuilt.npc_id is None
        with pytest.raises(ValueError, match="nothing to make"):
            jobs.submit_item(_create(store)["id"], {}, items.clean_options({}, jobs.cfg))
        with pytest.raises(KeyError):
            jobs.submit_item("no-such-item-123456", {"make": True}, items.clean_options({}, jobs.cfg))
    finally:
        jobs.close()


def test_an_old_job_record_without_an_item_loads_as_before():
    old = {"id": "abc", "npc_id": "x-1", "npc_name": "X", "options": {}, "steps": [], "status": "done", "created": "t"}
    job = Job.from_record(old)
    assert job.item_id is None and job.target_type == "npc"


# ---- the API --------------------------------------------------------------------------------------------------------------------------------

@pytest.fixture()
def api(tmp_path):
    import importlib
    server = importlib.import_module("server")
    cfg = copy.deepcopy(config.DEFAULTS)
    cfg.update(library_path=tmp_path / "library", comfy_url="http://127.0.0.1:9", auth_token="")
    app = server.App(cfg)
    httpd = server.create_server(("127.0.0.1", 0), app)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"

    def call(method, path, body=None, raw=False):
        headers = {"Content-Type": "application/json"} if method != "GET" else {}
        data = json.dumps(body).encode() if body is not None else (b"{}" if method != "GET" else None)
        try:
            with urllib.request.urlopen(urllib.request.Request(base + path, data=data, method=method, headers=headers), timeout=30) as r:
                payload = r.read()
                return r.status, (payload if raw else json.loads(payload))
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    call.app = app
    call.base = base
    yield call
    httpd.shutdown()
    httpd.server_close()
    app.close()


def test_the_config_and_health_describe_the_items_tab(api):
    status, cfg = api("GET", "/api/config")
    assert status == 200 and cfg["items"]["families"][0]["id"] == "weapon" and cfg["items"]["best"] == items.BEST_ENGINES
    status, health = api("GET", "/api/health")
    missing = [m for m in health["items"]["missing"] if not m.startswith("Blender")]   # (Blender: installed here or not)
    assert status == 200 and missing == ["ComfyUI"]                                   # ComfyUI is not reachable in these tests


def test_an_item_lives_through_the_api(api):
    status, item = api("POST", "/api/items", {"family": "footwear", "kind": "boots", "description": "worn brown leather", "setting": "western"})
    assert status == 200 and item["name"] == "Boots" and item["size_m"] == 0.45 and item["setting"] == "western"
    item_id = item["id"]
    assert api("GET", "/api/items")[1][0]["id"] == item_id and api("GET", f"/api/items/{item_id}")[1]["description"] == "worn brown leather"
    status, changed = api("PUT", f"/api/items/{item_id}", {**item, "name": "Riding boots", "size_m": 0.5})
    assert status == 200 and changed["name"] == "Riding boots" and changed["size_m"] == 0.5
    assert api("PUT", f"/api/items/{item_id}", {**item, "kind": "cutlass"})[0] == 400
    assert api("GET", "/api/items/no-such-item-123456")[0] == 404 and api("PUT", "/api/items/no-such-item-123456", item)[0] == 404
    status, zipped = api("GET", f"/api/items/{item_id}/export.zip", raw=True)
    assert status == 200 and f"{item_id}/item.json" in zipfile.ZipFile(io.BytesIO(zipped)).namelist()
    assert api("DELETE", f"/api/items/{item_id}/assets/hero")[0] == 404
    assert api("DELETE", f"/api/items/{item_id}")[1] == {"deleted": item_id} and api("GET", "/api/items")[1] == []


def test_bad_item_requests_are_client_errors(api):
    assert api("POST", "/api/items", {"family": "weapon", "kind": "boots"})[0] == 400
    assert api("POST", "/api/items", {"size_m": 99})[0] == 400
    assert api("POST", "/api/items", ["not", "an", "object"])[0] == 400
    item = api("POST", "/api/items", {})[1]
    assert api("POST", f"/api/items/{item['id']}/make", {"request": {"redo": ["nothing"]}})[0] == 400
    assert api("POST", f"/api/items/{item['id']}/make", {"request": {}})[0] == 400                  # nothing asked for
    assert api("POST", f"/api/items/{item['id']}/make", {"request": {"make": True}, "options": {"seed": "x"}})[0] == 400
    assert api("POST", "/api/items/no-such-item-123456/make", {"request": {"make": True}})[0] == 404


def test_files_are_served_from_the_item_folder_only(api):
    item = api("POST", "/api/items", {})[1]
    store = api.app.items
    store.write_asset(item["id"], "hero.png", _png())
    status, picture = api("GET", f"/api/items/{item['id']}/files/hero.png", raw=True)
    assert status == 200 and picture[:4] == b"\x89PNG"
    assert api("GET", f"/api/items/{item['id']}/files/item.json")[0] == 404                       # the record is read through the API
    assert api("GET", f"/api/items/{item['id']}/files/views/front.png")[0] == 404
    assert api("GET", f"/api/items/{item['id']}/files/..%2F..%2Fitem.json")[0] == 404
    assert api("GET", "/api/items/no-such-item-123456/files/hero.png")[0] == 404


def test_making_an_item_without_comfyui_fails_the_step_and_frees_the_item(api):
    item = api("POST", "/api/items", {})[1]
    status, made = api("POST", f"/api/items/{item['id']}/make", {"request": {"make": True}})
    assert status == 200 and made["job"]["target_type"] == "item" and made["job"]["item_id"] == item["id"]
    deadline = time.time() + 30
    job = made["job"]
    while time.time() < deadline:
        job = next(j for j in api("GET", "/api/jobs")[1] if j["id"] == made["job"]["id"])
        if job["status"] in ("done", "error", "cancelled"):
            break
        time.sleep(0.2)
    assert job["status"] == "error" and "ComfyUI" in (job["error"] or "") and job["steps"][0]["status"] == "error"
    status, again = api("POST", f"/api/items/{item['id']}/make", {"request": {"make": True}})
    assert status == 200 and again["job"]["id"] != made["job"]["id"]                              # free again once the first job ended


# ---- the high-angle views (two more pictures, for the paint only) -------------------------------------------------------------------

HIGH_VIEWS = items.HIGH_VIEWS


def _with_high(item, fresh=True, painted=True):
    """An item with its two high-angle pictures, and a game version that was painted from them (or not)."""
    assets = item["assets"]
    assets["high"] = {v: {"file": f"high/{v}.png", "created": f"h-{v}", **({} if fresh else {"stale": True})} for v in HIGH_VIEWS}
    if "model_3d" in assets and "refined" in assets["model_3d"]:
        assets["model_3d"]["refined"]["painted_from"] = sorted([*VIEWS, *(HIGH_VIEWS if painted else ())])
    return item


def test_a_kind_wants_the_high_views_when_it_says_so_or_its_family_does():
    data = items.load()
    fam = data["families"]
    assert items.wants_high(fam["headwear"], fam["headwear"]["kinds"]["helmet"]) is True
    assert items.wants_high(fam["footwear"], fam["footwear"]["kinds"]["boots"]) is True
    assert items.wants_high(fam["weapon"], fam["weapon"]["kinds"]["cutlass"]) is False             # a long thin thing stood up or turned upside down in them
    assert items.wants_high(fam["armor"], fam["armor"]["kinds"]["shield"]) is False                # the kind says otherwise
    assert items.wants_high(fam["prop"], fam["prop"]["kinds"]["torch"]) is False
    assert items.wants_high(fam["garment"], fam["garment"]["kinds"]["coat"]) is False
    offered = {k["id"]: k["high"] for f in items.catalogue()["families"] for k in f["kinds"]}
    assert offered["helmet"] and offered["barrel"] and offered["backpack"] and not offered["cutlass"] and not offered["shield"]
    assert set(data["views_high"]) == set(HIGH_VIEWS)


def test_an_item_asks_for_the_high_views_as_its_kind_does_unless_it_says_otherwise():
    assert items.clean_item({"family": "headwear", "kind": "helmet"})["high_views"] is True
    assert items.clean_item({"family": "weapon", "kind": "cutlass"})["high_views"] is False
    assert items.clean_item({"family": "headwear", "kind": "helmet", "high_views": False})["high_views"] is False
    assert items.clean_item({"family": "weapon", "kind": "cutlass", "high_views": True})["high_views"] is True
    assert items.clean_item({"family": "headwear", "kind": "helmet", "high_views": ""})["high_views"] is True
    with pytest.raises(ValueError, match="true or false"):
        items.clean_item({"high_views": "yes"})


def test_the_high_views_are_asked_for_from_the_front_and_above_and_from_behind_and_above():
    helmet = _item(family="headwear", kind="helmet")
    front, back = (items.view_prompt(helmet, v)["positive"] for v in HIGH_VIEWS)
    assert front.startswith("Show the exact same helmet again. The face opening is the front. It is seen from the front and above, from a high angle of 45 degrees looking down on it. Keep")
    assert "seen from behind and above, from a high angle of 45 degrees" in back
    barrel = items.view_prompt(_item(family="prop", kind="barrel"), "front_high")["positive"]
    assert barrel.startswith("Show the exact same barrel again, seen from the front and above, from a high angle of 45 degrees looking down on it. Keep")
    assert "plain white background" in front and items.view_prompt(helmet, "front_high")["negative"] == items.view_prompt(helmet, "front")["negative"]


def test_an_item_that_wants_them_gets_the_high_views_before_the_paint_and_one_that_does_not_never_does():
    assert _kinds(items.plan(_item(high_views=True), {"make": True})) == ["item_hero", "item_views", "item_shape", "item_high", "item_finish"]
    assert _kinds(items.plan(_item(high_views=False), {"make": True})) == ["item_hero", "item_views", "item_shape", "item_finish"]
    assert _kinds(items.plan(_item(high_views=True), {"make": True, "finish": False})) == ["item_hero", "item_views", "item_shape", "item_high"]
    assert [s["label"] for s in items.plan(_item(high_views=True), {"make": True})][3] == "High-angle views"


def test_asking_for_the_high_views_later_makes_them_and_then_paints_again_from_them():
    done = _made(finish=True)
    done["high_views"] = True
    assert _kinds(items.plan(done, {"make": True})) == ["item_high", "item_finish"]            # (the paint was made without them)
    with_high = _with_high(done, painted=False)
    assert items.state(with_high) == {"item_hero": True, "item_views": True, "item_shape": True, "item_high": True, "item_finish": False}
    assert _kinds(items.plan(with_high, {"make": True})) == ["item_finish"]                    # they are there, the paint just has not read them
    painted = _with_high(_made(finish=True), painted=True)
    painted["high_views"] = True
    with pytest.raises(ValueError, match="nothing to make"):
        items.plan(painted, {"make": True})
    painted["high_views"] = False                                                              # not asked for any more: nothing to do either
    with pytest.raises(ValueError, match="nothing to make"):
        items.plan(painted, {"make": True})


def test_new_main_picture_or_stale_high_views_are_made_again_and_redo_high_needs_the_picture():
    stale = _with_high(_made(), fresh=False)
    stale["high_views"] = True
    assert _kinds(items.plan(stale, {"make": True})) == ["item_high", "item_finish"]
    assert _kinds(items.plan(_made(hero=False, views=False, shape=False, finish=False), {"redo": ["high"]})) == ["item_hero", "item_high"]
    assert _kinds(items.plan(_made(), {"redo": ["high"]})) == ["item_high"]
    assert _kinds(items.plan(_made(stale=("hero",)) | {"high_views": True}, {"make": True})) == ["item_hero", "item_views", "item_shape", "item_high", "item_finish"]


def test_the_high_views_are_drawn_from_the_main_picture_and_leave_the_shape_alone(bench):
    store, item_id, comfy, calls, run = bench
    store.update(item_id, lambda n: n.update(high_views=True))
    run("item_hero")
    run("item_views")
    store.update(item_id, lambda n: n["assets"].update(model_3d={"file": "model_3d.glb", "created": "m"}))
    calls["edit"].clear()
    item = run("item_high")
    assert [c["prompt"] for c in calls["edit"]] == [items.view_prompt(item, v)["positive"] for v in HIGH_VIEWS]
    assert {c["seed"] for c in calls["edit"]} == {(item["seed"] + 2) % 2 ** 32} and {c["size"] for c in calls["edit"]} == {(1024, 1024)}
    assert {c["refs"] for c in calls["edit"]} == {f"{item_id}_hero.png [temp]"} and {c["engine"] for c in calls["edit"]} == {"qwen2511"}
    high = item["assets"]["high"]
    assert list(high) == list(HIGH_VIEWS) and high["front_high"]["file"] == "high/front_high.png"
    assert all(store.read_asset(item_id, m["file"]) and m["source_hero_created"] == item["assets"]["hero"]["created"] for m in high.values())
    assert not item["assets"]["model_3d"].get("stale") and not any(v.get("stale") for v in item["assets"]["views"].values())
    again = run("item_hero", reroll=True)
    assert all(m["stale"] for m in again["assets"]["high"].values())                              # drawn from the old picture


def test_the_paint_reads_the_fresh_high_views_and_says_so_and_a_stale_one_is_left_out(bench):
    store, item_id, comfy, calls, run = bench
    store.update(item_id, lambda n: n.update(high_views=True))
    for kind in ("item_hero", "item_views", "item_shape", "item_high"):
        run(kind)
    item = run("item_finish")
    assert list(calls["refine"][0]["views"]) == [*VIEWS, *HIGH_VIEWS]
    assert item["assets"]["model_3d"]["refined"]["painted_from"] == sorted([*VIEWS, *HIGH_VIEWS])
    assert items.state(item)["item_finish"] is True
    store.update(item_id, lambda n: n["assets"]["high"]["back_high"].update(stale=True))
    run("item_finish")
    assert list(calls["refine"][1]["views"]) == [*VIEWS, "front_high"]                             # only the pictures that are still right


def test_the_high_views_are_counted_marked_removed_and_exported(store):
    items_store, _lib = store
    item = _with_everything(items_store, _create(items_store, family="headwear", kind="helmet", description="iron"))
    for view in HIGH_VIEWS:
        items_store.write_asset(item["id"], f"high/{view}.png", _png())
    items_store.update(item["id"], lambda n: n["assets"].update(high={v: {"file": f"high/{v}.png", "created": f"h-{v}"} for v in HIGH_VIEWS}))
    assert items_store.list()[0]["has"]["high"] == 2 and not items_store.list()[0]["stale"]
    with zipfile.ZipFile(io.BytesIO(items_store.export_zip(item["id"]))) as z:
        assert {f"{item['id']}/high/{v}.png" for v in HIGH_VIEWS} <= set(z.namelist())
    edited = items_store.edit(item["id"], items.clean_item({**item, "description": "gold"}))
    assert all(m["stale"] for m in edited["assets"]["high"].values()) and items_store.list()[0]["stale"]
    removed = items_store.delete_asset(item["id"], "high")
    assert "high" not in removed["assets"] and not (items_store.root / item["id"] / "high").exists()
    assert removed["assets"]["model_3d"]["stale"] is True                                           # (from the edit above, not from taking them away)
    again = items_store.delete_asset(item["id"], "hero")
    assert "hero" not in again["assets"]
    with pytest.raises(KeyError):
        items_store.delete_asset(item["id"], "high")


# ---- the command line ------------------------------------------------------------------------------------------------------------------------

def _parse(*argv):
    return cli.build_parser(roll.load_data()).parse_args(["item", *argv])


def test_the_item_command_takes_what_an_item_is_and_how_to_make_it():
    a = _parse("make", "--family", "footwear", "--kind", "boots", "--describe", "worn", "--setting", "western", "--size", "0.5", "--faces", "10000",
               "--no-finish", "--no-best", "--image-model", "zimage", "--edit-model", "qwen2511", "--redo", "shape", "finish", "--seed", "4")
    assert (a.item_cmd, a.family, a.kind, a.describe, a.setting, a.size, a.faces) == ("make", "footwear", "boots", "worn", "western", 0.5, 10000)
    assert a.no_finish and a.no_best and a.redo == ["shape", "finish"] and (a.image_model, a.edit_model, a.seed) == ("zimage", "qwen2511", 4)
    assert a.high_views is None and _parse("make", "--high-views", "off").high_views == "off"
    plain = _parse("make")
    assert (plain.family, plain.look, plain.faces, plain.redo, plain.setting, plain.no_best) == ("weapon", "realistic", 30000, [], "", False)
    with pytest.raises(SystemExit):
        _parse("make", "--family", "vehicle")
    assert _parse("export", "x", "--out", "somewhere").item_cmd == "export"


class StubJobs:
    def __init__(self):
        self.calls = []

    def submit_item(self, item_id, request, options):
        self.calls.append((item_id, request, options))
        return {"id": "j1", "npc_name": "Boots", "status": "done", "steps": [{"label": "Main picture"}] * 2}


@pytest.fixture()
def cli_app(tmp_path, monkeypatch):
    lib = Library(tmp_path / "library")
    app = types.SimpleNamespace(items=ItemStore(lib), jobs=StubJobs(), cfg=config.DEFAULTS, lib=lib)
    monkeypatch.setattr(cli, "wait_for", lambda _app, submitted: [{"id": j["id"], "status": "done"} for j in submitted])
    return app


def test_making_an_item_from_the_command_line_creates_it_and_queues_the_steps(cli_app, capsys):
    a = _parse("make", "--family", "footwear", "--kind", "boots", "--describe", "worn leather", "--no-finish", "--no-best", "--reroll")
    assert cli.cmd_item(a, roll.load_data(), cli_app) == 0
    item_id, request, options = cli_app.jobs.calls[0]
    item = cli_app.items.get(item_id)
    assert (item["kind"], item["description"], item["name"], item["size_m"]) == ("boots", "worn leather", "Boots", 0.45)
    assert request == {"make": True, "finish": False, "redo": []}
    assert options["best"] is False and options["reroll"] is True and (options["image_model"], options["edit_model"]) == ("qwen2512", "qwen2511")   # (the app's defaults)
    assert "2 step(s) queued" in capsys.readouterr().out


def test_the_high_views_follow_the_kind_unless_the_command_line_says(cli_app):
    for family, kind, flag, wanted in (("headwear", "helmet", None, True), ("headwear", "helmet", "off", False), ("weapon", "cutlass", None, False),
                                       ("weapon", "cutlass", "on", True)):
        argv = ["make", "--family", family, "--kind", kind] + (["--high-views", flag] if flag else [])
        assert cli.cmd_item(_parse(*argv), roll.load_data(), cli_app) == 0
        item_id = cli_app.jobs.calls[-1][0]
        assert cli_app.items.get(item_id)["high_views"] is wanted


def test_an_existing_item_is_found_by_part_of_its_name_and_a_bad_one_is_refused(cli_app, capsys):
    item = cli_app.items.create(items.clean_item({"family": "weapon", "kind": "cutlass", "name": "Captain's blade"}))
    assert cli.cmd_item(_parse("make", "--id", "captain", "--redo", "hero"), roll.load_data(), cli_app) == 0
    assert cli_app.jobs.calls[0][0] == item["id"] and cli_app.jobs.calls[0][1]["redo"] == ["hero"]
    with pytest.raises(SystemExit, match="No item matches"):
        cli.cmd_item(_parse("make", "--id", "nothing-like-it"), roll.load_data(), cli_app)
    with pytest.raises(SystemExit, match="Unknown kind"):
        cli.cmd_item(_parse("make", "--family", "weapon", "--kind", "boots"), roll.load_data(), cli_app)


def test_items_are_listed_and_exported_from_the_command_line(cli_app, tmp_path, capsys):
    item = cli_app.items.create(items.clean_item({"family": "headwear", "kind": "helmet"}))
    assert cli.cmd_item(_parse("list"), roll.load_data(), cli_app) == 0
    listing = capsys.readouterr().out
    assert item["id"] in listing and "Helmet" in listing and "picture=0 views=0 model=0 finished=0" in listing
    assert cli.cmd_item(_parse("export", "helmet", "--out", str(tmp_path / "out")), roll.load_data(), cli_app) == 0
    with zipfile.ZipFile(tmp_path / "out" / f"{item['id']}.zip") as z:
        assert z.namelist() == [f"{item['id']}/item.json"]


def test_a_command_line_run_closes_the_app_when_it_ends_so_the_gpu_is_let_go(monkeypatch, tmp_path, capsys):
    closed = []

    class FakeApp:
        def __init__(self):
            self.items = ItemStore(Library(tmp_path / "library"))

        def close(self):
            closed.append(True)
    monkeypatch.setattr(cli, "App", FakeApp)
    assert cli.main(["item", "list"]) == 0
    with pytest.raises(SystemExit):
        cli.main(["item", "make", "--id", "no-such-item-123456"])
    assert closed == [True, True]                                  # (a run that fails closes it too)


# ---- glasses: items built from a recipe, with no pictures and no job ---------------------------------------------------------------------

@pytest.fixture(autouse=True)
def quiet_posters(monkeypatch):
    """No test starts Blender: a picture for a pair of glasses is a small stand-in unless a test says otherwise."""
    monkeypatch.setattr(itemposter, "render", lambda cfg, glb, **more: _png((228, 230, 236), (64, 48)))


def _glasses(**more):
    return items.clean_item({"family": "eyewear", "kind": "glasses", **more})


@pytest.fixture()
def glass_store(tmp_path):
    drawn = []

    def draw(cfg, glb):
        drawn.append(len(glb))
        return _png((230, 230, 235), (64, 48))
    lib = Library(tmp_path / "library")
    made = ItemStore(lib, cfg={}, draw=draw)
    yield made, drawn, lib
    made.close()


def test_glasses_are_a_family_built_from_a_recipe_the_others_are_made_from_pictures():
    catalogue = items.catalogue()
    family = next(f for f in catalogue["families"] if f["id"] == "eyewear")
    assert family["maker"] == "glasses" and [k["id"] for k in family["kinds"]] == ["glasses", "sunglasses"]
    assert all(k["preset"] in glasses.load()["presets"] and k["pivot"] == "bridge" and k["attach"] == "Head" for k in family["kinds"])
    assert next(k for k in family["kinds"] if k["id"] == "sunglasses")["recipe"] == {"lens_tint": "grey"}
    assert all(f["maker"] == "photo" for f in catalogue["families"] if f["id"] not in ("eyewear", "clothing")) and "recipe" not in catalogue["families"][0]["kinds"][0]
    assert catalogue["glasses"]["presets"] and "lens_w" in catalogue["glasses"]["fields"]
    assert items.is_recipe({"family": "eyewear"}) and not items.is_recipe({"family": "weapon"}) and items.maker("nothing-like-it") == "photo"


def test_a_pair_of_glasses_is_cleaned_with_its_complete_recipe_and_the_picture_fields_are_set_aside():
    item = _glasses(name="Reading", recipe={"preset": "reading", "lens_w": 47}, setting="western", look="voxel", high_views=True)
    assert item["recipe"]["shape"] == "half_moon" and item["recipe"]["lens_w"] == 47 and list(item["recipe"]) == list(glasses.load()["defaults"])
    assert (item["setting"], item["look"], item["high_views"]) == ("", "realistic", False)
    assert _glasses()["recipe"]["preset"] == "round_wire"
    sun = items.clean_item({"family": "eyewear", "kind": "sunglasses"})
    assert sun["recipe"]["preset"] == "aviator" and sun["recipe"]["lens_tint"] == "grey" and sun["name"] == "Sunglasses"
    with pytest.raises(ValueError, match="Lens width"):
        _glasses(recipe={"lens_w": 5})
    with pytest.raises(ValueError, match="no recipe"):
        items.clean_item({"family": "weapon", "kind": "sword", "recipe": {"lens_w": 50}})
    assert "recipe" not in items.clean_item({"family": "weapon", "kind": "sword", "recipe": {}})


def test_there_is_nothing_to_queue_for_glasses():
    with pytest.raises(ValueError, match="built from their settings"):
        items.plan({"family": "eyewear", "kind": "glasses", "assets": {}}, {"make": True})


def test_creating_glasses_builds_the_model_at_once_and_gives_the_item_its_size(glass_store):
    store, drawn, _lib = glass_store
    item = store.create(_glasses(name="Gold rounds"))
    model = item["assets"]["model_3d"]
    assert model["file"] == "model_3d.glb" and model["engine_id"] == "glasses" and model["triangles"] > 4000 and model["format"] == "glb"
    assert model["fit"]["origin"] == "bridge" and model["refined"]["created"] and "hero" not in item["assets"] and "views" not in item["assets"]
    parsed = Glb(store.read_asset(item["id"], "model_3d.glb"))
    low, high = _world(store.read_asset(item["id"], "model_3d.glb"))
    assert item["size_m"] == pytest.approx(float((high - low).max()), abs=1e-4) and 0.12 < item["size_m"] < 0.2
    assert model["fit"]["size_m"] == pytest.approx((high - low).tolist(), abs=1e-4) and parsed.json["asset"]["extras"]["recipe"] == item["recipe"]
    card = store.list()[0]
    assert card["maker"] == "glasses" and card["has"] == {"hero": False, "views": 0, "high": 0, "model_3d": True, "finished": True} and not card["stale"]


def test_the_picture_is_drawn_in_the_background_and_becomes_the_card(glass_store):
    store, drawn, _lib = glass_store
    item = store.create(_glasses())
    assert store.posters.wait_idle(10)
    item = store.get(item["id"])
    model = item["assets"]["model_3d"]
    assert model["preview"] == "model_3d_preview.png" and model["preview_of"] == model["build"] and "preview_error" not in model and len(drawn) == 1
    assert store.resolve(item["id"], "model_3d_preview.png").is_file() and store.list()[0]["thumb"] is True
    assert Image.open(store.resolve(item["id"], "thumb.png")).size == (64, 48)


def test_a_new_recipe_builds_again_and_a_new_name_does_not(glass_store):
    store, drawn, _lib = glass_store
    item = store.create(_glasses())
    store.posters.wait_idle(10)
    first = store.get(item["id"])
    same = store.edit(item["id"], {**_glasses(), "name": "Renamed", "description": "a note"})
    assert same["assets"]["model_3d"]["build"] == first["assets"]["model_3d"]["build"] and same["name"] == "Renamed"
    store.posters.wait_idle(10)
    assert len(drawn) == 1                                                         # (nothing about the glasses changed: no new model, no new picture)
    changed = store.edit(item["id"], _glasses(recipe={"preset": "cat_eye", "lens_tint": "rose"}))
    assert changed["assets"]["model_3d"]["build"] != first["assets"]["model_3d"]["build"] and changed["recipe"]["shape"] == "cat_eye"
    assert changed["assets"]["model_3d"]["triangles"] != first["assets"]["model_3d"]["triangles"] or changed["size_m"] != first["size_m"]
    store.posters.wait_idle(10)
    again = store.get(item["id"])["assets"]["model_3d"]
    assert len(drawn) == 2 and again["preview_of"] == again["build"]
    fine = store.edit(item["id"], _glasses(recipe=changed["recipe"], faces=100000))
    assert fine["assets"]["model_3d"]["detail"] == "fine" and fine["assets"]["model_3d"]["triangles"] > changed["assets"]["model_3d"]["triangles"]


def test_the_size_the_editor_sends_is_ignored_because_the_size_comes_from_the_measurements(glass_store):
    store, _drawn, _lib = glass_store
    item = store.create(_glasses())
    edited = store.edit(item["id"], {**_glasses(), "size_m": 3.0})
    assert edited["size_m"] == item["size_m"] and "fit_stale" not in edited["assets"]["model_3d"]
    low, high = _world(store.read_asset(item["id"], "model_3d.glb"))
    assert float((high - low).max()) == pytest.approx(item["size_m"], abs=1e-4)               # (never rescaled)


def test_glasses_cannot_become_another_sort_of_item_nor_the_other_way_round(glass_store):
    store, _drawn, _lib = glass_store
    pair = store.create(_glasses())
    blade = store.create(items.clean_item({"family": "weapon", "kind": "sword"}))
    with pytest.raises(ValueError, match="cannot become"):
        store.edit(pair["id"], items.clean_item({"family": "weapon", "kind": "sword"}))
    with pytest.raises(ValueError, match="cannot become"):
        store.edit(blade["id"], _glasses())
    assert store.posters.wait_idle(10)                                 # (the picture thread writes the pair's record: on Windows a read can meet it half way)
    assert store.get(pair["id"])["family"] == "eyewear" and store.get(blade["id"])["family"] == "weapon"


def test_a_picture_asked_for_while_one_is_being_drawn_is_drawn_again_from_the_newest_model(tmp_path):
    gate, started, drawn = threading.Event(), threading.Event(), []

    def slow(cfg, glb):
        drawn.append(len(glb))
        started.set()
        gate.wait(10)
        return _png()
    store = ItemStore(Library(tmp_path / "library"), cfg={}, draw=slow)
    try:
        item = store.create(_glasses())
        assert started.wait(10)                                                     # the first picture is being drawn from the first model
        store.edit(item["id"], _glasses(recipe={"preset": "aviator"}))              # ... and the model changes meanwhile
        gate.set()
        assert store.posters.wait_idle(20)
        model = store.get(item["id"])["assets"]["model_3d"]
        assert len(drawn) == 2 and model["preview_of"] == model["build"] and drawn[0] != drawn[1]
    finally:
        gate.set()
        store.close()


def test_a_picture_that_cannot_be_drawn_is_said_and_never_stops_the_item_or_the_next_picture(tmp_path):
    attempts = []

    def flaky(cfg, glb):
        attempts.append(1)
        if len(attempts) == 1:
            raise ComfyError("Blender was not found")
        return _png()
    store = ItemStore(Library(tmp_path / "library"), cfg={}, draw=flaky)
    try:
        item = store.create(_glasses())
        assert store.posters.wait_idle(10)
        model = store.get(item["id"])["assets"]["model_3d"]
        assert model["preview_error"] == "Blender was not found" and "preview" not in model and store.list()[0]["thumb"] is False
        store.build_recipe(item["id"])                                              # asked again: it works, and the error goes
        assert store.posters.wait_idle(10)
        model = store.get(item["id"])["assets"]["model_3d"]
        assert "preview_error" not in model and model["preview_of"] == model["build"]
    finally:
        store.close()


def test_a_picture_for_a_pair_deleted_while_it_was_being_drawn_does_not_bring_its_folder_back(tmp_path):
    go, made = threading.Event(), []

    def draw(cfg, glb):
        go.wait(10)
        store.delete(made[0])                                                       # the pair goes while its picture is still being drawn
        return _png()
    store = ItemStore(Library(tmp_path / "library"), cfg={}, draw=draw)
    try:
        made.append(store.create(_glasses())["id"])
        go.set()
        assert store.posters.wait_idle(10)
        assert not (store.root / made[0]).exists() and store.list() == []
    finally:
        go.set()
        store.close()


def test_without_a_config_glasses_are_still_built_and_just_have_no_picture(tmp_path):
    store = ItemStore(Library(tmp_path / "library"))
    item = store.create(_glasses())
    assert store.posters is None and item["assets"]["model_3d"]["file"] == "model_3d.glb" and "preview" not in item["assets"]["model_3d"]
    with pytest.raises(ValueError, match="made from pictures"):
        store.build_recipe(store.create(items.clean_item({"family": "weapon", "kind": "sword"}))["id"])


def test_the_model_can_be_taken_away_and_built_again_and_the_item_exports_with_its_picture(glass_store):
    store, _drawn, lib = glass_store
    item = store.create(_glasses())
    store.posters.wait_idle(10)
    names = zipfile.ZipFile(io.BytesIO(store.export_zip(item["id"]))).namelist()
    assert sorted(names) == sorted(f"{item['id']}/{n}" for n in ("item.json", "model_3d.glb", "model_3d_preview.png"))
    gone = store.delete_asset(item["id"], "model_3d")
    assert "model_3d" not in gone["assets"] and not store.resolve(item["id"], "model_3d.glb").exists() and not store.list()[0]["has"]["model_3d"]
    store.build_recipe(item["id"])
    assert store.resolve(item["id"], "model_3d.glb").is_file() and store.list()[0]["has"]["model_3d"]
    store.posters.wait_idle(10)
    again = ItemStore(lib)                                                          # a restart finds it, picture and all
    assert again.list()[0]["id"] == item["id"] and again.list()[0]["thumb"] is True


def test_glasses_live_through_the_api_and_a_recipe_can_be_previewed_without_saving(api):
    status, item = api("POST", "/api/items", {"family": "eyewear", "kind": "sunglasses", "name": "Pilot", "recipe": {"lens_tint": "green", "lens_w": 56}})
    assert status == 200 and item["recipe"]["lens_tint"] == "green" and item["recipe"]["preset"] == "aviator" and item["assets"]["model_3d"]["file"] == "model_3d.glb"
    status, model = api("GET", f"/api/items/{item['id']}/files/model_3d.glb", raw=True)
    assert status == 200 and model[:4] == b"glTF" and len(model) == item["assets"]["model_3d"]["size"]
    api.app.items.posters.wait_idle(10)
    assert api("GET", f"/api/items/{item['id']}")[1]["assets"]["model_3d"]["preview"] == "model_3d_preview.png"
    assert api("GET", f"/api/items/{item['id']}/files/thumb.png", raw=True)[0] == 200
    status, changed = api("PUT", f"/api/items/{item['id']}", {**item, "recipe": {**item["recipe"], "lens_w": 50, "pose": "folded"}})
    assert status == 200 and changed["recipe"]["pose"] == "folded" and changed["size_m"] != item["size_m"]       # (folded, the arms make it wider than deep)
    assert changed["assets"]["model_3d"]["build"] != item["assets"]["model_3d"]["build"]
    status, made = api("POST", f"/api/items/{item['id']}/make", {"request": {"make": True}})
    assert status == 200 and made["job"] is None and made["item"]["id"] == item["id"]
    assert api("GET", "/api/jobs")[1] == []                                          # (no job: it is not queued behind the GPU's work)
    request = urllib.request.Request(api.base + "/api/items/recipe-preview", method="POST", headers={"Content-Type": "application/json"},
                                     data=json.dumps({"kind": "glasses", "recipe": {"preset": "hexagon"}, "faces": 10000}).encode())
    with urllib.request.urlopen(request, timeout=30) as response:
        preview, info = response.read(), json.loads(response.headers["X-Item-Info"])
    assert preview[:4] == b"glTF" and info["detail"] == "light" and info["origin"] == "bridge" and info["triangles"] < 10000
    assert [i["id"] for i in api("GET", "/api/items")[1]] == [item["id"]]            # (a preview saves nothing)


def test_bad_glasses_requests_are_client_errors(api):
    assert api("POST", "/api/items", {"family": "eyewear", "recipe": {"lens_w": 5}})[0] == 400
    assert api("POST", "/api/items", {"family": "eyewear", "recipe": {"colour": "red"}})[0] == 400
    assert api("POST", "/api/items/recipe-preview", {"recipe": {"shape": "star"}})[0] == 400
    assert api("POST", "/api/items/recipe-preview", {"family": "weapon", "kind": "sword"})[0] == 400
    status, item = api("POST", "/api/items", {"family": "eyewear"})
    assert status == 200
    assert api("PUT", f"/api/items/{item['id']}", {"family": "weapon", "kind": "sword"})[0] == 400                 # (not another sort of item)
    assert api("PUT", f"/api/items/{item['id']}", {**item, "recipe": {"temple_len": 500}})[0] == 400
    assert api("GET", "/api/items")[1][0]["id"] == item["id"]


def test_the_config_gives_the_editor_the_glasses_tables(api):
    cfg = api("GET", "/api/config")[1]
    assert cfg["items"]["glasses"]["presets"][0]["recipe"]["shape"] and cfg["items"]["glasses"]["choices"]["shape"]["round"] and cfg["api_version"] >= 25


def test_glasses_are_made_from_the_command_line_and_changed_by_naming_what_to_change(cli_app, capsys):
    a = _parse("make", "--family", "eyewear", "--kind", "glasses", "--style", "aviator", "--set", "lens_w=54", "--set", "lens_tint=green", "--set", "pads=false",
               "--name", "Pilot")
    assert cli.cmd_item(a, roll.load_data(), cli_app) == 0
    item = cli_app.items.list()[0]
    made = cli_app.items.get(item["id"])
    assert (made["name"], made["recipe"]["shape"], made["recipe"]["lens_w"], made["recipe"]["lens_tint"], made["recipe"]["pads"]) == ("Pilot", "aviator", 54, "green", False)
    assert "triangles" in capsys.readouterr().out and cli_app.jobs.calls == []                 # (built on the spot: nothing queued)
    assert cli.cmd_item(_parse("make", "--id", "pilot", "--set", "pose=folded"), roll.load_data(), cli_app) == 0
    folded = cli_app.items.get(item["id"])
    assert folded["recipe"]["pose"] == "folded" and folded["recipe"]["lens_w"] == 54
    assert folded["assets"]["model_3d"]["build"] != made["assets"]["model_3d"]["build"]
    assert cli.cmd_item(_parse("make", "--id", "pilot", "--style", "cat_eye"), roll.load_data(), cli_app) == 0
    cat = cli_app.items.get(item["id"])
    assert cat["recipe"]["shape"] == "cat_eye" and cat["recipe"]["lens_w"] == 54 and cat["recipe"]["pose"] == "folded"      # (the style, and what was set stays)
    assert cli.cmd_item(_parse("make", "--id", "pilot"), roll.load_data(), cli_app) == 0                              # (nothing named: built again)
    assert cli.cmd_item(_parse("list"), roll.load_data(), cli_app) == 0
    assert "model=1 finished=1" in capsys.readouterr().out


def test_bad_glasses_settings_on_the_command_line_are_refused_with_a_reason(cli_app):
    with pytest.raises(SystemExit, match="KEY=VALUE"):
        cli.cmd_item(_parse("make", "--family", "eyewear", "--set", "lens_w"), roll.load_data(), cli_app)
    with pytest.raises(SystemExit, match="Unknown glasses setting: colour"):
        cli.cmd_item(_parse("make", "--family", "eyewear", "--set", "colour=red"), roll.load_data(), cli_app)
    with pytest.raises(SystemExit, match="Lens width must be between"):
        cli.cmd_item(_parse("make", "--family", "eyewear", "--set", "lens_w=5"), roll.load_data(), cli_app)
    assert cli_app.items.list() == []
    assert cli.recipe_settings(["a=1", "b=true", "c=False", "d=#aabbcc", "e=1.5", "f=x y"]) == {"a": 1.0, "b": True, "c": False, "d": "#aabbcc", "e": 1.5, "f": "x y"}


def test_the_controls_can_be_set_from_a_few_words_through_the_api(api):
    status, said = api("POST", "/api/items/recipe-from-text", {"text": "round gold wire glasses with green lenses", "kind": "glasses"})
    assert status == 200 and said["recipe"]["preset"] == "round_wire" and said["recipe"]["lens_tint"] == "green" and "green lenses" in said["understood"]
    assert "lens_tint" in said["changed"] and list(said["recipe"]) == list(glasses.load()["defaults"])
    current = said["recipe"]
    status, bigger = api("POST", "/api/items/recipe-from-text", {"text": "oversized", "kind": "glasses", "recipe": current})
    assert status == 200 and bigger["recipe"]["lens_w"] == current["lens_w"] + 6 and bigger["recipe"]["frame_color"] == current["frame_color"]
    status, nothing = api("POST", "/api/items/recipe-from-text", {"text": "asdf", "recipe": current})
    assert status == 200 and nothing["understood"] == [] and nothing["recipe"] == current and nothing["changed"] == []
    assert api("POST", "/api/items/recipe-from-text", {"text": 5})[0] == 400
    assert api("POST", "/api/items/recipe-from-text", {"text": "x" * 501})[0] == 400
    assert api("POST", "/api/items/recipe-from-text", {"text": "round", "family": "weapon", "kind": "sword"})[0] == 400
    assert api("POST", "/api/items/recipe-from-text", {"text": "round", "recipe": {"lens_w": 5}})[0] == 400


# ---- fitted clothes: the second family built from a recipe ---------------------------------------------------------------------------------

needs_garments = pytest.mark.skipif(not clothing.available(), reason="the Body Designer is not installed")
from test_clothing import NOTE, stand_in_designer


def _shirt(**more):
    return items.clean_item({"family": "clothing", "kind": "shirt", **more})


@needs_garments
def test_fitted_clothes_are_a_family_built_from_a_recipe_with_their_origin_on_the_floor():
    catalogue = items.catalogue()
    family = next(f for f in catalogue["families"] if f["id"] == "clothing")
    assert family["maker"] == "garment" and [k["id"] for k in family["kinds"]] == ["shirt", "trousers", "jacket", "apron", "skirt", "dress"] and all(k["pivot"] == "floor" and k["attach"] == "" for k in family["kinds"])
    assert catalogue["clothing"]["available"] and set(catalogue["clothing"]["kinds"]) == {"shirt", "trousers", "jacket", "apron", "skirt", "dress"} and "ease_mm" in catalogue["clothing"]["fields"]
    assert [k["id"] for k in family["kinds"]] == list(catalogue["clothing"]["kinds"])                       # (every kind the page lists is one the clothing designer knows, in the same order)
    assert items.is_recipe({"family": "clothing"}) and items.maker("clothing") == "garment" and items.recipe_maker("clothing") is clothing and items.recipe_maker("eyewear") is glasses
    assert items.recipe_maker("weapon") is None
    drawn = next(f for f in catalogue["families"] if f["id"] == "garment")
    assert drawn["maker"] == "photo" and "fitted" in drawn["blurb"].lower()                    # (the drawn garments say where the fitted ones are)


@needs_garments
def test_a_garment_is_cleaned_with_its_complete_recipe_and_the_picture_fields_are_set_aside():
    item = _shirt(name="Red shirt", recipe={"colour": "#a02020", "ease_mm": 8}, setting="western", look="voxel", high_views=True, size_m=3)
    assert item["recipe"] == {"kind": "shirt", "colour": "#a02020", "ease_mm": 8.0, "length": 1.0, "texture": "plain"} and (item["setting"], item["look"], item["high_views"]) == ("", "realistic", False)
    trousers = items.clean_item({"family": "clothing", "kind": "trousers", "recipe": {"kind": "shirt", "colour": "navy"}})
    assert trousers["recipe"]["kind"] == "trousers" and trousers["recipe"]["texture"] == "weave" and trousers["name"] == "Fitted trousers"          # (the item's kind is the garment's)
    jacket = items.clean_item({"family": "clothing", "kind": "jacket", "recipe": {"colour": "navy"}})
    apron = items.clean_item({"family": "clothing", "kind": "apron"})
    assert jacket["recipe"] == {"kind": "jacket", "colour": "#243456", "ease_mm": 0.0, "length": 1.0, "texture": "plain"} and jacket["name"] == "Open jacket"
    assert apron["recipe"]["kind"] == "apron" and apron["recipe"]["texture"] == "weave" and apron["name"] == "Bib apron"
    with pytest.raises(ValueError, match="Looseness must be between 0 and 8"):
        items.clean_item({"family": "clothing", "kind": "jacket", "recipe": {"ease_mm": 12}})
    with pytest.raises(ValueError, match="Looseness must be between"):
        _shirt(recipe={"ease_mm": 40})
    with pytest.raises(ValueError, match="Unknown clothing setting: lens_w"):
        _shirt(recipe={"lens_w": 50})
    with pytest.raises(ValueError, match="Unknown kind"):
        items.clean_item({"family": "clothing", "kind": "coat"})
    with pytest.raises(ValueError, match="built from their settings"):
        items.plan({"family": "clothing", "kind": "shirt", "assets": {}}, {"make": True})


@pytest.fixture()
def cloth_store(tmp_path):
    drawn = []

    def draw(cfg, glb):
        drawn.append(len(glb))
        return _png((230, 230, 235), (64, 48))
    lib = Library(tmp_path / "library")
    made = ItemStore(lib, cfg={}, draw=draw)
    yield made, drawn, lib
    made.close()


@needs_garments
def test_creating_a_garment_builds_its_pattern_at_once_and_the_picture_is_drawn_in_the_background(cloth_store):
    store, drawn, _lib = cloth_store
    item = store.create(_shirt(name="Teal shirt"))
    model = item["assets"]["model_3d"]
    assert model["file"] == "model_3d.glb" and model["engine_id"] == "garment" and model["textured"] is True and model["triangles"] > 2000 and model["format"] == "glb"
    assert model["fit"]["origin"] == "floor" and model["refined"]["created"] and "hero" not in item["assets"] and "views" not in item["assets"] and "frame_width_mm" not in model
    parsed = Glb(store.read_asset(item["id"], "model_3d.glb"))
    low, high = _world(store.read_asset(item["id"], "model_3d.glb"))
    assert item["size_m"] == pytest.approx(float((high - low).max()), abs=1e-4) and 0.4 < item["size_m"] < 0.8
    assert parsed.json["asset"]["extras"]["recipe"]["colour"] == item["recipe"]["colour"] and [n["name"] for n in parsed.json["nodes"]] == ["ForgeShirt"]
    assert store.posters.wait_idle(10)
    card = store.list()[0]
    assert card["maker"] == "garment" and card["has"] == {"hero": False, "views": 0, "high": 0, "model_3d": True, "finished": True} and card["thumb"] is True and not card["stale"]
    assert store.get(item["id"])["assets"]["model_3d"]["preview_of"] == model["build"] and len(drawn) == 1


@needs_garments
def test_a_new_recipe_builds_again_a_new_name_does_not_and_the_size_comes_from_the_garment(cloth_store):
    store, drawn, _lib = cloth_store
    item = store.create(_shirt(recipe={"ease_mm": 6}))
    store.posters.wait_idle(10)
    first = store.get(item["id"])
    same = store.edit(item["id"], {**_shirt(recipe={"ease_mm": 6}), "name": "Renamed", "description": "a note", "size_m": 3.0})
    assert same["assets"]["model_3d"]["build"] == first["assets"]["model_3d"]["build"] and same["name"] == "Renamed" and same["size_m"] == first["size_m"]
    store.posters.wait_idle(10)
    assert len(drawn) == 1
    shorter = store.edit(item["id"], _shirt(recipe={"ease_mm": 6, "length": 0.9, "colour": "navy"}))
    assert shorter["assets"]["model_3d"]["build"] != first["assets"]["model_3d"]["build"] and shorter["recipe"]["colour"] == "#243456" and shorter["size_m"] < first["size_m"]     # (a shorter shirt is a smaller thing)
    store.posters.wait_idle(10)
    assert len(drawn) == 2 and store.get(item["id"])["assets"]["model_3d"]["preview_of"] == shorter["assets"]["model_3d"]["build"]
    other = store.edit(item["id"], items.clean_item({"family": "clothing", "kind": "trousers", "name": "Now trousers"}))                           # (the other garment of the same family)
    assert other["kind"] == "trousers" and other["recipe"]["kind"] == "trousers" and [n["name"] for n in Glb(store.read_asset(item["id"], "model_3d.glb")).json["nodes"]] == ["ForgeTrousers"]


@needs_garments
def test_a_garment_cannot_become_glasses_or_a_drawn_thing_nor_the_other_way_round(cloth_store):
    store, _drawn, _lib = cloth_store
    shirt = store.create(_shirt())
    pair = store.create(items.clean_item({"family": "eyewear", "kind": "glasses"}))
    blade = store.create(items.clean_item({"family": "weapon", "kind": "sword"}))
    for item_id, other in ((shirt["id"], {"family": "eyewear", "kind": "glasses"}), (shirt["id"], {"family": "weapon", "kind": "sword"}), (blade["id"], {"family": "clothing", "kind": "shirt"}),
                           (pair["id"], {"family": "clothing", "kind": "shirt"})):
        with pytest.raises(ValueError, match="cannot become another sort of item"):
            store.edit(item_id, items.clean_item(other))
    with pytest.raises(ValueError, match="Fitted clothes cannot become"):
        store.edit(shirt["id"], items.clean_item({"family": "weapon", "kind": "sword"}))
    with pytest.raises(ValueError, match="Glasses cannot become"):
        store.edit(pair["id"], items.clean_item({"family": "weapon", "kind": "sword"}))
    with pytest.raises(ValueError, match="Only glasses are fitted to a face"):
        store.fit(shirt["id"], "someone", None)


@needs_garments
def test_a_garment_lives_through_the_api_is_previewed_without_saving_and_set_from_words(api):
    status, item = api("POST", "/api/items", {"family": "clothing", "kind": "trousers", "name": "Work trousers", "recipe": {"colour": "brown", "ease_mm": 7}})
    assert status == 200 and item["recipe"]["colour"] == "#6e4a32" and item["recipe"]["kind"] == "trousers" and item["assets"]["model_3d"]["file"] == "model_3d.glb"
    status, model = api("GET", f"/api/items/{item['id']}/files/model_3d.glb", raw=True)
    assert status == 200 and model[:4] == b"glTF" and len(model) == item["assets"]["model_3d"]["size"]
    status, made = api("POST", f"/api/items/{item['id']}/make", {"request": {"make": True}})
    assert status == 200 and made["job"] is None and made["item"]["id"] == item["id"] and api("GET", "/api/jobs")[1] == []
    request = urllib.request.Request(api.base + "/api/items/recipe-preview", method="POST", headers={"Content-Type": "application/json"},
                                     data=json.dumps({"family": "clothing", "kind": "shirt", "recipe": {"colour": "#a02020", "length": 0.9}}).encode())
    with urllib.request.urlopen(request, timeout=30) as response:
        preview, info = response.read(), json.loads(response.headers["X-Item-Info"])
    assert preview[:4] == b"glTF" and info["kind"] == "shirt" and info["origin"] == "floor" and info["triangles"] > 2000
    assert [i["id"] for i in api("GET", "/api/items")[1]] == [item["id"]]                                  # (a preview saves nothing)
    status, said = api("POST", "/api/items/recipe-from-text", {"text": "a dark red loose shirt, cropped", "family": "clothing", "kind": "shirt"})
    shortest = clothing.catalogue()["kinds"]["shirt"]["controls"]["length"][0]                            # ("cropped" is 0.8, or the shortest a shirt is offered)
    assert status == 200 and said["recipe"]["kind"] == "shirt" and said["recipe"]["ease_mm"] == 15.0 and said["recipe"]["length"] == max(0.8, shortest) and sorted(said["changed"]) == ["colour", "ease_mm", "length"]
    assert api("POST", "/api/items", {"family": "clothing", "recipe": {"ease_mm": 99}})[0] == 400
    assert api("POST", "/api/items/recipe-preview", {"family": "clothing", "recipe": {"length": 2}})[0] == 400
    assert api("POST", "/api/items/recipe-from-text", {"text": "loose", "family": "clothing", "recipe": {"texture": "leather"}})[0] == 400
    assert api("PUT", f"/api/items/{item['id']}", {"family": "weapon", "kind": "sword"})[0] == 400
    config = api("GET", "/api/config")[1]["items"]["clothing"]
    from bodydesigner import garments
    for entry in garments.catalogue():                                                                    # (each kind's own range, the Body Designer's, reaches the page)
        assert config["kinds"][entry["id"]]["controls"]["length"] == list(entry["controls"]["length"]) and config["kinds"][entry["id"]]["controls"]["ease_mm"] == list(entry["controls"]["ease_mm"])
    assert "max" not in config["fields"]["length"] and "measured" not in config


@needs_garments
def test_a_shirt_saved_with_a_length_the_body_designer_no_longer_offers_is_kept_through_the_api_and_one_it_does_not_read_is_refused(api, monkeypatch):
    stand_in_designer(monkeypatch)
    status, item = api("POST", "/api/items", {"family": "clothing", "kind": "shirt", "name": "Old shirt", "recipe": {"length": 1.1}})
    assert status == 200 and item["recipe"]["length"] == 1.1
    status, again = api("PUT", f"/api/items/{item['id']}", {**item, "name": "Renamed"})                      # (the page sends the whole item back, as it was)
    assert status == 200 and again["recipe"] == item["recipe"] and again["name"] == "Renamed"
    assert api("PUT", f"/api/items/{item['id']}", {**item, "recipe": {**item["recipe"], "length": 1.3}})[0] == 400
    assert api("POST", "/api/items", {"family": "clothing", "kind": "shirt", "recipe": {"length": 1.3}})[0] == 400
    status, preview = api("POST", "/api/items/recipe-preview", {"family": "clothing", "kind": "shirt", "recipe": item["recipe"]}, raw=True)
    assert status == 200 and preview[:4] == b"glTF"                                                          # (and it still builds: the pattern, whatever the fit will say)
    status, said = api("POST", "/api/items/recipe-from-text", {"text": "dark red", "family": "clothing", "kind": "shirt", "recipe": item["recipe"]})
    assert status == 200 and said["recipe"]["length"] == 1.1 and said["changed"] == ["colour"]               # (words that say nothing of the length leave it alone)
    config = api("GET", "/api/config")[1]["items"]["clothing"]
    assert config["kinds"]["shirt"]["controls"]["length"] == [0.9, 1.0] and config["kinds"]["shirt"]["notes"]["length"] == NOTE and config["kinds"]["trousers"]["controls"]["length"] == [0.8, 1.2]


@needs_garments
def test_a_garment_is_put_on_a_designed_body_through_the_api_and_the_character_wearing_it_is_a_download(api):
    from test_dressing import designed, worn_report
    app = api.app
    calls = []

    def fit(body, pieces, cancel=None):
        calls.append(pieces[0])
        return clothing.build(pieces[0])[0], worn_report(pieces[0])
    app.dressings.fit = fit
    status, item = api("POST", "/api/items", {"family": "clothing", "kind": "shirt", "recipe": {"colour": "#a02020", "texture": "plain"}})
    npc = designed(app.lib)
    assert api("GET", f"/api/items/{item['id']}/worn/{npc}.glb")[0] == 409                                      # (nothing fitted yet)
    status, started = api("POST", f"/api/items/{item['id']}/dress", {"npc": npc})
    assert status == 200 and started["state"] in ("queued", "running", "done") and started["eligible"]
    assert app.dressings.wait_idle(20)
    status, done = api("GET", f"/api/items/{item['id']}/dress/{npc}")
    assert status == 200 and done["state"] == "done" and done["fit"]["kind"] == "shirt" and done["stale"] is False and len(calls) == 1
    request = urllib.request.Request(api.base + f"/api/items/{item['id']}/worn/{npc}.glb")
    with urllib.request.urlopen(request, timeout=30) as response:
        glb, headers = response.read(), dict(response.headers)
    assert glb[:4] == b"glTF" and json.loads(headers["X-Dress"]) == {"stale": False, "recoloured": False} and f"{npc}-{item['id']}.glb" in headers["Content-Disposition"]
    status, again = api("POST", f"/api/items/{item['id']}/dress", {"npc": npc})
    assert status == 200 and again["state"] == "done" and len(calls) == 1                                       # (kept, still good: not fitted again)
    status, forced = api("POST", f"/api/items/{item['id']}/dress", {"npc": npc, "force": True})
    assert status == 200 and app.dressings.wait_idle(20) and len(calls) == 2
    status, changed = api("PUT", f"/api/items/{item['id']}", {**item, "recipe": {**item["recipe"], "colour": "#2020a0"}})
    assert status == 200 and api("GET", f"/api/items/{item['id']}/dress/{npc}")[1]["recoloured"] is True
    with urllib.request.urlopen(urllib.request.Request(api.base + f"/api/items/{item['id']}/worn/{npc}.glb"), timeout=30) as response:
        assert json.loads(response.headers["X-Dress"])["recoloured"] is True
    status, stopped = api("DELETE", f"/api/items/{item['id']}/dress/{npc}")
    assert status == 200 and stopped["state"] == "done"                                                         # (nothing to stop: it is as it was)
    assert (app.items.root / item["id"] / "worn" / f"{npc}.glb").is_file()
    assert api("DELETE", f"/api/npcs/{npc}")[0] == 200 and not (app.items.root / item["id"] / "worn" / f"{npc}.glb").exists()      # (a deleted character's fits go with it)


@needs_garments
def test_the_dress_routes_say_what_is_wrong(api):
    from test_dressing import designed
    status, item = api("POST", "/api/items", {"family": "clothing", "kind": "shirt"})
    npc = designed(api.app.lib)
    plain = designed(api.app.lib, designed_body=False, name="plain")
    pair = api("POST", "/api/items", {"family": "eyewear"})[1]
    assert api("POST", f"/api/items/{item['id']}/dress", {})[0] == 400                                         # (no character named)
    assert api("POST", f"/api/items/{item['id']}/dress", {"npc": 5})[0] == 400
    assert api("POST", f"/api/items/{item['id']}/dress", {"npc": "nobody-1"})[0] == 404
    assert api("POST", "/api/items/nothing-1/dress", {"npc": npc})[0] == 404
    status, refused = api("POST", f"/api/items/{item['id']}/dress", {"npc": plain})
    assert status == 400 and "not made by the Body Designer" in refused["error"]
    status, refused = api("POST", f"/api/items/{pair['id']}/dress", {"npc": npc})
    assert status == 400 and "Only fitted clothes" in refused["error"]
    assert api("GET", f"/api/items/{item['id']}/dress/nobody-1")[0] == 404 and api("DELETE", f"/api/items/{item['id']}/dress/nobody-1")[0] == 404
    status, state = api("GET", f"/api/items/{item['id']}/dress/{plain}")
    assert status == 200 and state["eligible"] is False and state["state"] == "none"
    cards = {card["id"]: card for card in api("GET", "/api/npcs")[1]}                                      # (the try-on lists only the characters whose model is a designed body)
    assert cards[npc]["has"]["designed"] is True and cards[plain]["has"]["designed"] is False
    assert api("POST", f"/api/items/{pair['id']}/fit", {"npc": npc})[0] in (400, 409)                          # (glasses are fitted to a face: not this route)


@needs_garments
def test_the_command_line_says_what_the_body_designer_says_about_a_setting_it_does_not_offer(cli_app, capsys, monkeypatch):
    stand_in_designer(monkeypatch)
    assert cli.cmd_item(_parse("make", "--family", "clothing", "--kind", "shirt", "--set", "length=1.1", "--name", "Long shirt"), roll.load_data(), cli_app) == 0
    said = capsys.readouterr().out
    assert "the pattern on its own" in said and f"Note: {NOTE}" in said
    assert cli.cmd_item(_parse("make", "--family", "clothing", "--kind", "shirt", "--set", "length=0.95", "--name", "Fine shirt"), roll.load_data(), cli_app) == 0
    assert "Note:" not in capsys.readouterr().out
    assert cli.cmd_item(_parse("make", "--family", "clothing", "--kind", "trousers", "--set", "length=1.1", "--name", "Long trousers"), roll.load_data(), cli_app) == 0
    assert "Note:" not in capsys.readouterr().out                                                            # (the trousers are offered that long)


@needs_garments
def test_garments_are_made_and_put_on_a_body_from_the_command_line(cli_app, capsys, tmp_path):
    from test_dressing import designed, worn_report
    a = _parse("make", "--family", "clothing", "--kind", "trousers", "--set", "colour=navy", "--set", "ease_mm=6", "--set", "length=0.9", "--name", "Work trousers")
    assert cli.cmd_item(a, roll.load_data(), cli_app) == 0
    made = cli_app.items.get(cli_app.items.list()[0]["id"])
    assert made["name"] == "Work trousers" and made["recipe"] == {"kind": "trousers", "colour": "#243456", "ease_mm": 6.0, "length": 0.9, "texture": "weave"} and cli_app.jobs.calls == []
    assert "the pattern on its own (not fitted to a body)" in capsys.readouterr().out
    assert cli.cmd_item(_parse("make", "--id", "work", "--set", "texture=plain"), roll.load_data(), cli_app) == 0
    assert cli_app.items.get(made["id"])["recipe"]["texture"] == "plain" and cli_app.items.get(made["id"])["recipe"]["ease_mm"] == 6.0           # (what was set stays)
    with pytest.raises(SystemExit, match="Only glasses have styles"):
        cli.cmd_item(_parse("make", "--family", "clothing", "--style", "aviator"), roll.load_data(), cli_app)
    with pytest.raises(SystemExit, match="Only glasses have styles"):
        cli.cmd_item(_parse("make", "--id", "work", "--style", "aviator"), roll.load_data(), cli_app)
    with pytest.raises(SystemExit, match="Unknown clothing setting: lens_w"):
        cli.cmd_item(_parse("make", "--family", "clothing", "--set", "lens_w=50"), roll.load_data(), cli_app)
    with pytest.raises(SystemExit, match="Length must be between"):
        cli.cmd_item(_parse("make", "--family", "clothing", "--set", "length=3"), roll.load_data(), cli_app)
    capsys.readouterr()
    calls = []

    def fit(body, pieces, cancel=None):
        calls.append(pieces[0]["kind"])
        return clothing.build(pieces[0])[0], worn_report(pieces[0], replaced=("ForgeTrousers",))
    cli_app.dressings = dressing.Dressings(cli_app.items, cli_app.lib, fit=fit)
    npc = designed(cli_app.lib, name="runner")
    try:
        out = tmp_path / "worn"
        assert cli.cmd_item(_parse("dress", "work", "runner", "--out", str(out)), roll.load_data(), cli_app) == 0
        said = capsys.readouterr().out
        assert "the trousers (cut 2) fit:" in said and "in place of the trousers it wore" in said and calls == ["trousers"]
        assert (out / f"{npc}-{made['id']}.glb").read_bytes()[:4] == b"glTF"
        assert cli.cmd_item(_parse("dress", "work", "runner"), roll.load_data(), cli_app) == 0 and calls == ["trousers"]               # (kept: not fitted again)
        assert cli.cmd_item(_parse("dress", "work", "runner", "--refit"), roll.load_data(), cli_app) == 0 and calls == ["trousers", "trousers"]
        designed(cli_app.lib, designed_body=False, name="plain")
        with pytest.raises(SystemExit, match="not made by the Body Designer"):
            cli.cmd_item(_parse("dress", "work", "plain"), roll.load_data(), cli_app)
        with pytest.raises(SystemExit, match="No item matches"):
            cli.cmd_item(_parse("dress", "nothing-like-it", "runner"), roll.load_data(), cli_app)
    finally:
        cli_app.dressings.close()
