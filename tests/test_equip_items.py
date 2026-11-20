"""Held items and hats on a character through the item store, the routes and the command line (the character and the items are made up: tests/equip_support.py)."""
import copy
import importlib
import json
import threading
import types
import urllib.error
import urllib.request

import pytest

import equip_support as es
from npcgen import cli, config, equip, items, roll
from npcgen.itemstore import ItemStore
from npcgen.library import Library
from npcgen.paint3d import Glb

PROFILE = {"first_name": "Equip", "last_name": "Test", "age": 30, "gender": "female", "occupation": "tester"}


def new_npc(lib, rigged=True, with_model=True):
    npc = lib.create({"setting": "modern", "style": "pixel", "seed": 1, "profile": PROFILE, "appearance": {}})
    if with_model:
        glb = es.skinned_character(rigged=rigged)
        lib.write_asset(npc["id"], "rig_3d.glb", glb)
        lib.update(npc["id"], lambda n: n.setdefault("assets", {}).update(
            rig_3d={"file": "rig_3d.glb", "created": "2026-10-13T10:00:00", "size": len(glb)}))
    return npc["id"]


def new_item(store, family, kind, glb, name=None, made=True):
    item = store.create(items.clean_item({"family": family, "kind": kind, "name": name or kind.title()}))
    if made:
        store.write_asset(item["id"], "model.glb", glb)
        store.update(item["id"], lambda n: n.setdefault("assets", {}).update(model_3d={"file": "model.glb", "created": "2026-10-13T09:00:00", "size": len(glb)}))
    return store.get(item["id"])


@pytest.fixture()
def world(tmp_path):
    lib = Library(tmp_path / "library")
    store = ItemStore(lib)
    yield store, lib
    store.close()


# ---- the store -----------------------------------------------------------------------------------------------------------------------------
def test_which_items_can_be_equipped(world):
    store, _lib = world
    sword = new_item(store, "weapon", "sword", es.sword_glb())
    assert store.equip_how(sword) == {"carry": "blade", "slot": "hand_r", "slots": ["hand_r", "hand_l"]}
    assert store.equip_how(new_item(store, "headwear", "hat", es.hat_glb()))["slots"] == ["head"]
    assert store.equip_how(new_item(store, "armor", "shield", es.shield_glb()))["slot"] == "hand_l"
    assert store.equip_how(new_item(store, "weapon", "pistol", es.sword_glb(), made=False)) is None      # not yet: its muzzle could be at either end
    assert store.equip_how(new_item(store, "garment", "coat", es.sword_glb(), made=False)) is None
    assert store.equip_how(items.clean_item({"family": "eyewear", "kind": "glasses"})) is None          # glasses have their own way onto a face


def test_where_an_item_goes_is_worked_out_and_what_is_kept_for_the_character_is_used(world, monkeypatch):
    store, lib = world
    npc_id, sword = new_npc(lib), new_item(store, "weapon", "sword", es.sword_glb())
    base = store.equip_placement(sword["id"], npc_id)
    assert (base["slot"], base["bone"], base["model"], base["saved"], base["notes"]) == ("hand_r", "hand_r", "rig_3d", None, [])
    assert base["attach"]["bone"] == "hand_r" and len(base["attach"]["translation"]) == 3 and base["scale"] == 1.0 and base["nudge"] == equip.NUDGE_DEFAULT
    assert "world" not in base                                                                       # (a matrix is not a thing to send)
    kept = store.save_equip(sword["id"], npc_id, "hand_l", {"up": 0.05, "turn": 30})
    assert kept["equips"][npc_id]["slot"] == "hand_l" and kept["equips"][npc_id]["nudge"]["up"] == 0.05 and kept["equips"][npc_id]["created"]
    again = store.equip_placement(sword["id"], npc_id)                                               # nothing asked: what is kept
    assert again["slot"] == "hand_l" and again["bone"] == "hand_l" and again["nudge"]["turn"] == 30 and again["saved"]["nudge"]["up"] == 0.05
    asked = store.equip_placement(sword["id"], npc_id, "hand_r", {"size": 1.5})                      # something asked: that
    assert asked["slot"] == "hand_r" and asked["scale"] == 1.5 and asked["nudge"]["up"] == 0.0 and asked["saved"] is not None
    assert store.get(sword["id"])["equips"] == kept["equips"]                                        # (the record keeps only slot, nudge and time)
    store.forget_equip(sword["id"], npc_id)
    assert "equips" not in store.get(sword["id"]) and store.equip_placement(sword["id"], npc_id)["slot"] == "hand_r"
    with pytest.raises(KeyError):
        store.forget_equip(sword["id"], npc_id)


def test_the_characters_rest_pose_is_read_once_while_its_file_stays_the_same_and_again_when_it_changes(world, monkeypatch):
    store, lib = world
    npc_id, hat = new_npc(lib), new_item(store, "headwear", "hat", es.hat_glb())
    reads = []
    real = equip.read_rest
    monkeypatch.setattr(equip, "read_rest", lambda data: (reads.append(1), real(data))[1])
    for nudge in (None, {"up": 0.01}, {"up": 0.02}, {"size": 1.2}):
        store.equip_placement(hat["id"], npc_id, nudge=nudge)
    assert len(reads) == 1
    lib.write_asset(npc_id, "rig_3d.glb", es.skinned_character(head_scale=1.4))                      # the model was made again
    wide = store.equip_placement(hat["id"], npc_id)
    assert len(reads) == 2 and wide["scale"] > 1.2                                                    # a bigger head: a bigger hat


def test_a_character_without_a_skeleton_or_a_model_and_an_item_without_a_model_are_refused_by_name(world):
    store, lib = world
    sword = new_item(store, "weapon", "sword", es.sword_glb())
    with pytest.raises(ValueError, match="no skeleton"):
        store.equip_placement(sword["id"], new_npc(lib, rigged=False))
    with pytest.raises(ValueError, match="no 3D model"):
        store.equip_placement(sword["id"], new_npc(lib, with_model=False))
    with pytest.raises(ValueError, match="has no 3D model yet"):
        store.equip_placement(new_item(store, "weapon", "axe", es.sword_glb(), made=False)["id"], new_npc(lib))
    with pytest.raises(ValueError, match="not done yet"):
        store.equip_placement(new_item(store, "weapon", "rifle", es.sword_glb())["id"], new_npc(lib))
    for bad in ({"slot": "head"}, {"slot": "knee"}):
        with pytest.raises(ValueError, match="cannot go in"):
            store.save_equip(sword["id"], new_npc(lib), bad["slot"], None)
    with pytest.raises(ValueError, match="between"):
        store.save_equip(sword["id"], new_npc(lib), None, {"up": 5})
    with pytest.raises(ValueError, match="Not a character id"):
        store.save_equip(sword["id"], "../x", None, None)


def test_the_character_comes_back_holding_everything_in_one_file(world):
    store, lib = world
    npc_id = new_npc(lib)
    sword, shield, hat = (new_item(store, "weapon", "sword", es.sword_glb()), new_item(store, "armor", "shield", es.shield_glb()),
                          new_item(store, "headwear", "hat", es.hat_glb()))
    glb, report = store.equipped(npc_id, [{"item": sword["id"]}, {"item": shield["id"]}, {"item": hat["id"], "nudge": {"up": 0.02}}])
    assert [(e["kind"], e["slot"], e["bone"]) for e in report["items"]] == [("sword", "hand_r", "hand_r"), ("shield", "hand_l", "hand_l"), ("hat", "head", "head")]
    assert report["model"] == "rig_3d" and report["notes"] == []
    doc = Glb(glb).json
    assert sorted(n["extras"]["slot"] for n in doc["nodes"] if (n.get("extras") or {}).get("npcforge") == "equip") == ["hand_l", "hand_r", "head"]
    with pytest.raises(equip.EquipError, match="already carries"):                                   # one thing in a hand
        store.equipped(npc_id, [{"item": sword["id"]}, {"item": new_item(store, "weapon", "axe", es.sword_glb())["id"]}])
    store.save_equip(sword["id"], npc_id, "hand_l", {"tilt": 20})
    left, _ = store.equipped(npc_id, [{"item": sword["id"]}])                                         # what is kept applies
    assert [n["extras"]["slot"] for n in Glb(left).json["nodes"] if (n.get("extras") or {}).get("npcforge") == "equip"] == ["hand_l"]


def test_the_catalogue_says_which_kinds_can_be_equipped():
    kinds = {k["id"]: k for fam in items.catalogue()["families"] for k in fam["kinds"]}
    assert kinds["sword"]["equip"] == {"carry": "blade", "slot": "hand_r", "slots": ["hand_r", "hand_l"]} and kinds["hat"]["equip"]["slots"] == ["head"]
    assert kinds["torch"]["equip"]["carry"] == "pole" and kinds["pistol"]["equip"] is None and kinds["coat"]["equip"] is None
    assert "equip" not in kinds["glasses"]                                                           # (a recipe family has its own try-on)


# ---- the routes -----------------------------------------------------------------------------------------------------------------------------
@pytest.fixture()
def api(tmp_path):
    server = importlib.import_module("server")
    cfg = copy.deepcopy(config.DEFAULTS)
    cfg.update(library_path=tmp_path / "library", comfy_url="http://127.0.0.1:9", auth_token="")
    app = server.App(cfg)
    httpd = server.create_server(("127.0.0.1", 0), app)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"

    def call(method, path, body=None, raw=False, headers=False):
        h = {"Content-Type": "application/json"} if method != "GET" else {}
        data = json.dumps(body).encode() if body is not None else (b"{}" if method != "GET" else None)
        try:
            with urllib.request.urlopen(urllib.request.Request(base + path, data=data, method=method, headers=h), timeout=30) as r:
                payload = r.read()
                out = payload if raw else json.loads(payload)
                return (r.status, out, dict(r.headers)) if headers else (r.status, out)
        except urllib.error.HTTPError as e:
            body = json.loads(e.read() or b"{}")
            return (e.code, body, dict(e.headers)) if headers else (e.code, body)

    call.app = app
    yield call
    httpd.shutdown()
    httpd.server_close()
    app.close()


def test_the_routes_place_keep_and_forget_and_hand_back_the_character_holding_the_item(api):
    npc_id = new_npc(api.app.lib)
    sword = new_item(api.app.items, "weapon", "sword", es.sword_glb())
    status, placed = api("POST", f"/api/items/{sword['id']}/equip", {"npc": npc_id})
    assert status == 200 and placed["slot"] == "hand_r" and placed["bone"] == "hand_r" and placed["attach"]["bone"] == "hand_r" and placed["saved"] is None
    status, moved = api("POST", f"/api/items/{sword['id']}/equip", {"npc": npc_id, "slot": "hand_l", "nudge": {"up": 0.03}})
    assert moved["slot"] == "hand_l" and moved["nudge"]["up"] == 0.03 and moved["attach"] != placed["attach"]
    status, kept = api("PUT", f"/api/items/{sword['id']}/equips/{npc_id}", {"slot": "hand_l", "nudge": {"up": 0.03}})
    assert status == 200 and kept["equips"][npc_id]["slot"] == "hand_l"
    assert api("POST", f"/api/items/{sword['id']}/equip", {"npc": npc_id})[1]["saved"]["nudge"]["up"] == 0.03
    status, glb, headers = api("GET", f"/api/items/{sword['id']}/worn/{npc_id}.glb", raw=True, headers=True)      # (what is kept: the left hand)
    equip_header = json.loads(headers["X-Equip"])
    assert status == 200 and glb[:4] == b"glTF" and equip_header["items"][0]["slot"] == "hand_l" and equip_header["items"][0]["bone"] == "hand_l"
    assert equip_header["model"] == "rig_3d" and "nudge" not in equip_header["items"][0] and headers["Cache-Control"] == "no-store"
    status, glb, headers = api("GET", f"/api/items/{sword['id']}/worn/{npc_id}.glb?slot=hand_r&up=0.04&size=1.25", raw=True, headers=True)
    shown = json.loads(headers["X-Equip"])["items"][0]
    assert status == 200 and shown["slot"] == "hand_r" and shown["scale"] == 1.25
    assert api("DELETE", f"/api/items/{sword['id']}/equips/{npc_id}")[0] == 200
    assert api("DELETE", f"/api/items/{sword['id']}/equips/{npc_id}")[0] == 404


def test_the_routes_refuse_what_cannot_be_done_with_a_reason(api):
    npc_id, bare = new_npc(api.app.lib), new_npc(api.app.lib, rigged=False)
    sword = new_item(api.app.items, "weapon", "sword", es.sword_glb())
    rifle = new_item(api.app.items, "weapon", "rifle", es.sword_glb())
    unmade = new_item(api.app.items, "weapon", "axe", es.sword_glb(), made=False)
    for body, said in (({}, "Say which character"), ({"npc": npc_id, "slot": "head"}, "cannot go in the head"),
                       ({"npc": npc_id, "nudge": {"up": 3}}, "between"), ({"npc": bare}, "no skeleton")):
        status, out = api("POST", f"/api/items/{sword['id']}/equip", body)
        assert status == 400 and said in out["error"], (body, out)
    assert api("POST", f"/api/items/{rifle['id']}/equip", {"npc": npc_id})[1]["error"].endswith("is not done yet")
    assert "no 3D model yet" in api("POST", f"/api/items/{unmade['id']}/equip", {"npc": npc_id})[1]["error"]
    assert api("POST", f"/api/items/{sword['id']}/equip", {"npc": "no-such-npc-123456"})[0] == 404
    assert api("POST", "/api/items/no-such-item-123456/equip", {"npc": npc_id})[0] == 404
    assert api("GET", f"/api/items/{sword['id']}/worn/{npc_id}.glb?up=lots")[0] == 400
    assert api("PUT", f"/api/items/{sword['id']}/equips/{npc_id}", {"slot": "knee"})[0] == 400


def test_a_whole_loadout_comes_as_one_file_and_two_things_in_one_hand_are_refused(api):
    npc_id, bare = new_npc(api.app.lib), new_npc(api.app.lib, rigged=False)
    sword = new_item(api.app.items, "weapon", "sword", es.sword_glb())
    staff = new_item(api.app.items, "weapon", "staff", es.staff_glb())
    shield = new_item(api.app.items, "armor", "shield", es.shield_glb())
    hat = new_item(api.app.items, "headwear", "hat", es.hat_glb())
    status, glb, headers = api("GET", f"/api/npcs/{npc_id}/equipped.glb?items={sword['id']},{shield['id']}:hand_l,{hat['id']}", raw=True, headers=True)
    assert status == 200 and glb[:4] == b"glTF"
    assert [(e["kind"], e["slot"]) for e in json.loads(headers["X-Equip"])["items"]] == [("sword", "hand_r"), ("shield", "hand_l"), ("hat", "head")]
    assert api("GET", f"/api/npcs/{npc_id}/equipped.glb?items={sword['id']},{staff['id']}")[1]["error"].endswith("already carries Sword")
    assert api("GET", f"/api/npcs/{npc_id}/equipped.glb")[0] == 400 and api("GET", f"/api/npcs/{npc_id}/equipped.glb?items=Not%20An%20Id")[0] == 400
    assert api("GET", f"/api/npcs/{npc_id}/equipped.glb?items=no-such-item-123456")[0] == 404
    status, out = api("GET", f"/api/npcs/{bare}/equipped.glb?items={sword['id']}")
    assert status == 409 and "no skeleton" in out["error"]


def test_the_config_tells_the_page_which_kinds_can_be_equipped(api):
    kinds = {k["id"]: k for fam in api("GET", "/api/config")[1]["items"]["families"] for k in fam["kinds"]}
    assert kinds["hat"]["equip"] == {"carry": "hat", "slot": "head", "slots": ["head"]} and kinds["rifle"]["equip"] is None


# ---- the command line ---------------------------------------------------------------------------------------------------------------------------
def _parse(*argv):
    return cli.build_parser(roll.load_data()).parse_args(["item", "equip", *argv])


@pytest.fixture()
def cli_app(tmp_path):
    lib = Library(tmp_path / "library")
    app = types.SimpleNamespace(items=ItemStore(lib), cfg=config.DEFAULTS, lib=lib)
    yield app
    app.items.close()


def test_the_command_line_equips_prints_the_bones_keeps_the_nudge_and_writes_the_file(cli_app, tmp_path, capsys):
    npc_id = new_npc(cli_app.lib)
    sword = new_item(cli_app.items, "weapon", "sword", es.sword_glb(), "Captain's blade")
    hat = new_item(cli_app.items, "headwear", "hat", es.hat_glb(), "Felt hat")
    a = _parse(npc_id, "captain", "felt", "--out", str(tmp_path / "out"))
    assert cli.cmd_item(a, roll.load_data(), cli_app) == 0
    said = capsys.readouterr().out
    assert f"{sword['id']}: the sword goes in the right hand, on the bone \"hand_r\"" in said and f"{hat['id']}: the hat goes in the head, on the bone \"head\"" in said
    out = (tmp_path / "out" / f"{npc_id}-equipped.glb").read_bytes()
    assert out[:4] == b"glTF" and "follows every animation clip" in said
    assert cli.cmd_item(_parse(npc_id, "captain=hand_l", "--up", "0.02", "--size", "1.2", "--save"), roll.load_data(), cli_app) == 0
    kept = cli_app.items.get(sword["id"])["equips"][npc_id]
    assert kept["slot"] == "hand_l" and kept["nudge"]["up"] == 0.02 and kept["nudge"]["size"] == 1.2
    assert "Kept as the way this is carried" in capsys.readouterr().out


def test_the_command_line_refuses_what_cannot_be_done(cli_app):
    npc_id = new_npc(cli_app.lib)
    sword = new_item(cli_app.items, "weapon", "sword", es.sword_glb())
    new_item(cli_app.items, "headwear", "hat", es.hat_glb())
    with pytest.raises(SystemExit, match="one item at a time"):
        cli.cmd_item(_parse(npc_id, sword["id"], "hat", "--up", "0.01"), roll.load_data(), cli_app)
    with pytest.raises(SystemExit, match="cannot go in the head"):
        cli.cmd_item(_parse(npc_id, f"{sword['id']}=head"), roll.load_data(), cli_app)
    with pytest.raises(SystemExit, match="No item matches"):
        cli.cmd_item(_parse(npc_id, "nothing-like-it"), roll.load_data(), cli_app)
    with pytest.raises(SystemExit, match="no skeleton"):
        cli.cmd_item(_parse(new_npc(cli_app.lib, rigged=False), sword["id"]), roll.load_data(), cli_app)
