"""Glasses on a face: the item store's fit, saved fits and the character wearing them, and the routes (the face scan is stood in for: tests/facefit_support.py)."""
import copy
import importlib
import io
import json
import threading
import time
import urllib.error
import urllib.request

import numpy as np
import pytest
from PIL import Image
from facefit_support import make_scan
from test_glbmerge import character

from npcgen import config, facefit, glbmerge, items
from npcgen.itemstore import ItemStore
from npcgen.library import Library
from npcgen.paint3d import Glb

PROFILE = {"first_name": "Face", "last_name": "Fit", "age": 30, "gender": "female", "occupation": "tester"}


def picture(cfg, glb):
    """Stands in for the Blender picture of a pair."""
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), (230, 230, 235)).save(buf, "PNG")
    return buf.getvalue()


def pair(**more):
    return items.clean_item({"family": "eyewear", "kind": "glasses", **more})


def made(items_store, **more):
    """A new pair, once the thread that draws its list picture has finished with its files (on Windows a file being read cannot be replaced or removed)."""
    item = items_store.create(pair(**more))
    assert items_store.posters.wait_idle(10)
    return item


def new_npc(lib, with_model=True):
    npc = lib.create({"setting": "modern", "style": "pixel", "seed": 1, "profile": PROFILE, "appearance": {}})
    if with_model:
        glb = character()
        lib.write_asset(npc["id"], "model_3d.glb", glb)
        lib.update(npc["id"], lambda n: n.setdefault("assets", {}).update(model_3d={"file": "model_3d.glb", "created": "2026-10-01T10:00:00", "size": len(glb)}))
    return npc["id"]


def scan_of(**more):
    return facefit.Scan(make_scan(nose=0.02, **more))


# ---- the store ---------------------------------------------------------------------------------------------------------------------------------
@pytest.fixture()
def store(tmp_path):
    lib = Library(tmp_path / "library")
    made = ItemStore(lib, cfg={}, draw=picture)
    yield made, lib
    made.close()


def test_a_pair_is_fitted_to_a_face_and_the_nudge_saved_for_that_character_is_used_unless_another_is_given(store):
    items_store, lib = store
    item, npc_id, scan = made(items_store), new_npc(lib), scan_of()
    base = items_store.fit(item["id"], npc_id, scan)
    assert base["saved"] is None and base["report"]["nudge"] == facefit.clean_nudge(None) and base["report"]["behind_mm"] <= 0.01
    kept = items_store.save_fit(item["id"], npc_id, {"up": 2.5, "forward": -1}, "sdpose")
    assert kept["fits"][npc_id]["nudge"]["up"] == 2.5 and kept["fits"][npc_id]["source"] == "sdpose" and kept["fits"][npc_id]["created"]
    saved = items_store.fit(item["id"], npc_id, scan)
    assert saved["report"]["nudge"]["up"] == 2.5 and saved["saved"]["nudge"] == {"up": 2.5, "forward": -1.0, "tilt": 0.0, "size": 1.0}
    assert saved["position"][1] == pytest.approx(base["position"][1] + 0.0025, abs=1e-6)
    other = items_store.fit(item["id"], npc_id, scan, {"up": 0})                          # one is given: that one
    assert other["report"]["nudge"]["up"] == 0 and other["saved"] is not None
    assert items_store.get(item["id"])["fits"] == kept["fits"]                           # (and the record has only the nudge, never the scan)


def test_a_saved_fit_survives_the_pair_being_built_again_and_is_fitted_again_to_the_new_pair(store):
    items_store, lib = store
    item, npc_id, scan = made(items_store), new_npc(lib), scan_of()
    items_store.save_fit(item["id"], npc_id, {"forward": 2}, "sdpose")
    before = items_store.fit(item["id"], npc_id, scan)
    wide = items_store.edit(item["id"], pair(recipe={"lens_w": 60, "scale": 1.1}))
    assert items_store.posters.wait_idle(10)
    after = items_store.fit(wide["id"], npc_id, scan)
    assert after["report"]["nudge"]["forward"] == 2 and after["report"]["behind_mm"] <= 0.01
    assert after["position"] != before["position"]                                         # a bigger pair rests somewhere else


def test_fits_are_checked_and_can_be_forgotten(store):
    items_store, lib = store
    item, npc_id = made(items_store), new_npc(lib)
    with pytest.raises(ValueError, match="between"):
        items_store.save_fit(item["id"], npc_id, {"up": 400})
    with pytest.raises(ValueError, match="Not a character id"):
        items_store.save_fit(item["id"], "../etc", {})
    assert "fits" not in items_store.get(item["id"])
    items_store.save_fit(item["id"], npc_id, {"up": 1})
    assert "fits" not in items_store.forget_fit(item["id"], npc_id)                        # the last one takes the key away too
    with pytest.raises(KeyError):
        items_store.forget_fit(item["id"], npc_id)


def test_only_glasses_with_a_model_are_fitted(store):
    items_store, lib = store
    sword = items_store.create(items.clean_item({"family": "weapon", "kind": "cutlass", "description": "curved blade"}))
    npc_id = new_npc(lib)
    with pytest.raises(ValueError, match="Only glasses"):
        items_store.fit(sword["id"], npc_id, scan_of())
    item = made(items_store)
    items_store.delete_asset(item["id"], "model_3d")
    with pytest.raises(ValueError, match="no model yet"):
        items_store.fit(item["id"], npc_id, scan_of())


def test_the_character_wearing_the_pair_has_it_under_the_head_joint_where_it_was_fitted(store):
    items_store, lib = store
    item, npc_id, scan = made(items_store), new_npc(lib), scan_of(yaw=0.08)
    glb, info = items_store.worn(item["id"], npc_id, scan, {"forward": 3})
    doc = Glb(glb).json
    head = glbmerge.find_node(doc, "head")
    worn = doc["nodes"][head]["children"][-1]
    assert doc["nodes"][worn]["name"] == "glasses" and doc["nodes"][worn]["extras"]["npcforge"] == "glasses"
    placement = items_store.fit(item["id"], npc_id, scan, {"forward": 3})
    assert np.allclose(glbmerge.world_matrices(doc)[worn], facefit.world_matrix(placement), atol=1e-5)
    assert info["bone"] == "head" and info["model"] == "model_3d" and info["attach"]["bone"] == "head" and len(info["attach"]["translation"]) == 3
    assert info["placement"]["report"]["nudge"]["forward"] == 3


def test_a_character_with_no_model_or_a_pair_with_none_cannot_be_put_together(store):
    items_store, lib = store
    item = made(items_store)
    with pytest.raises(ValueError, match="no 3D model"):
        items_store.worn(item["id"], new_npc(lib, with_model=False), scan_of())
    with pytest.raises(KeyError):
        items_store.worn(item["id"], "no-such-npc-123456", scan_of())


# ---- the routes ------------------------------------------------------------------------------------------------------------------------------
@pytest.fixture()
def api(tmp_path):
    server = importlib.import_module("server")
    cfg = copy.deepcopy(config.DEFAULTS)
    cfg.update(library_path=tmp_path / "library", comfy_url="http://127.0.0.1:9", auth_token="")
    app = server.App(cfg)
    scans = []

    def find(cfg, comfy, glb, label, estimate=False, cancel=None, note=None):
        scans.append((label, estimate))
        return make_scan(nose=0.02, source="estimate" if estimate else "sdpose")
    app.faces.find = find
    app.items.posters.draw = picture                          # (no Blender for the list pictures)
    httpd = server.create_server(("127.0.0.1", 0), app)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"

    def call(method, path, body=None, raw=False, headers=False):
        h = {"Content-Type": "application/json"} if method != "GET" else {}
        data = json.dumps(body).encode() if body is not None else (b"{}" if method != "GET" else None)
        try:
            with urllib.request.urlopen(urllib.request.Request(base + path, data=data, method=method, headers=h), timeout=60) as r:
                payload = r.read()
                out = (r.status, payload if raw else json.loads(payload))
                return (*out, dict(r.headers)) if headers else out
        except urllib.error.HTTPError as e:
            out = (e.code, json.loads(e.read() or b"{}"))
            return (*out, dict(e.headers)) if headers else out

    call.app, call.scans = app, scans
    yield call
    httpd.shutdown()
    httpd.server_close()
    app.close()


def scanned(api, npc_id, *, force=False):
    """Ask for the face and wait for it, the way the page does."""
    status, state = api("POST", f"/api/npcs/{npc_id}/face/scan", {"force": force})
    assert status == 200
    deadline = time.monotonic() + 20
    while state["state"] in ("queued", "running") and time.monotonic() < deadline:
        time.sleep(0.05)
        state = api("GET", f"/api/npcs/{npc_id}/face")[1]
    return state


def test_a_characters_face_is_scanned_on_request_and_kept(api):
    npc_id = new_npc(api.app.lib)
    status, state = api("GET", f"/api/npcs/{npc_id}/face")
    assert status == 200 and state["state"] == "none" and state["scan"] is None and state["model"]["kind"] == "model_3d"
    done = scanned(api, npc_id)
    assert done["state"] == "done" and done["scan"]["source"] == "sdpose" and "grid" not in json.dumps(done)
    assert api("POST", f"/api/npcs/{npc_id}/face/scan")[1]["state"] == "done" and len(api.scans) == 1            # kept: not scanned again
    scanned_again = scanned(api, npc_id, force=True)
    assert scanned_again["state"] == "done" and scanned_again["scan"] is not None and len(api.scans) == 2
    assert api("DELETE", f"/api/npcs/{npc_id}/face/scan")[0] == 200


def test_the_face_routes_say_what_is_wrong(api):
    bare = new_npc(api.app.lib, with_model=False)
    status, body = api("POST", f"/api/npcs/{bare}/face/scan")
    assert status == 400 and "no 3D model" in body["error"]
    assert api("GET", "/api/npcs/no-such-npc-123456/face")[0] == 404
    assert api("POST", "/api/npcs/no-such-npc-123456/face/scan")[0] == 404
    assert api("DELETE", "/api/npcs/no-such-npc-123456/face/scan")[0] == 404


def test_a_pair_is_fitted_through_the_api_once_the_face_is_known(api):
    npc_id = new_npc(api.app.lib)
    item = api("POST", "/api/items", {"family": "eyewear", "kind": "glasses", "name": "Round"})[1]
    status, body = api("POST", f"/api/items/{item['id']}/fit", {"npc": npc_id})
    assert status == 409 and "find the face" in body["error"]
    scanned(api, npc_id)
    status, fit = api("POST", f"/api/items/{item['id']}/fit", {"npc": npc_id, "nudge": {"up": 2, "tilt": 3}})
    assert status == 200 and fit["npc"] == npc_id and fit["saved"] is None and len(fit["position"]) == 3 and len(fit["rotation"]) == 4
    assert fit["report"]["nudge"] == {"up": 2.0, "forward": 0.0, "tilt": 3.0, "size": 1.0} and fit["report"]["behind_mm"] <= 0.01 and fit["focus"]
    assert api("POST", f"/api/items/{item['id']}/fit", {"npc": npc_id, "nudge": {"up": 900}})[0] == 400
    assert api("POST", f"/api/items/{item['id']}/fit", {"nudge": {}})[0] == 400                          # which character?
    assert api("POST", f"/api/items/{item['id']}/fit", {"npc": 5})[0] == 400
    assert api("POST", f"/api/items/{item['id']}/fit", {"npc": "no-such-npc-123456"})[0] == 404
    assert api("POST", "/api/items/no-such-item-123456/fit", {"npc": npc_id})[0] == 404
    sword = api("POST", "/api/items", {"family": "weapon", "kind": "cutlass"})[1]
    assert api("POST", f"/api/items/{sword['id']}/fit", {"npc": npc_id})[0] == 400


def test_a_fit_is_saved_used_and_forgotten_through_the_api(api):
    npc_id = new_npc(api.app.lib)
    item = api("POST", "/api/items", {"family": "eyewear", "kind": "glasses"})[1]
    scanned(api, npc_id)
    status, kept = api("PUT", f"/api/items/{item['id']}/fits/{npc_id}", {"nudge": {"forward": -2, "size": 1.1}})
    assert status == 200 and kept["fits"][npc_id]["nudge"]["size"] == 1.1 and kept["fits"][npc_id]["source"] == "sdpose"
    again = api("POST", f"/api/items/{item['id']}/fit", {"npc": npc_id})[1]                                  # no nudge sent: the saved one
    assert again["report"]["nudge"]["forward"] == -2 and again["saved"]["nudge"]["size"] == 1.1
    assert api("GET", f"/api/items/{item['id']}")[1]["fits"] == kept["fits"]
    assert api("PUT", f"/api/items/{item['id']}/fits/{npc_id}", {"nudge": {"size": 9}})[0] == 400
    assert api("PUT", f"/api/items/{item['id']}/fits/no-such-npc-123456", {"nudge": {}})[0] == 404
    status, left = api("DELETE", f"/api/items/{item['id']}/fits/{npc_id}")
    assert status == 200 and "fits" not in left
    assert api("DELETE", f"/api/items/{item['id']}/fits/{npc_id}")[0] == 404


def test_the_character_wearing_the_pair_is_a_download(api):
    npc_id = new_npc(api.app.lib)
    item = api("POST", "/api/items", {"family": "eyewear", "kind": "glasses"})[1]
    assert api("GET", f"/api/items/{item['id']}/worn/{npc_id}.glb", raw=True)[0] == 409                 # no face yet
    scanned(api, npc_id)
    status, glb, headers = api("GET", f"/api/items/{item['id']}/worn/{npc_id}.glb", raw=True, headers=True)
    assert status == 200 and glb[:4] == b"glTF" and headers["Content-Type"] == "model/gltf-binary"
    assert headers["Content-Disposition"] == f'attachment; filename="{npc_id}-{item["id"]}.glb"'
    attach = json.loads(headers["X-Fit"])
    assert attach["bone"] == "head" and len(attach["rotation"]) == 4
    doc = Glb(glb).json
    assert [n["name"] for n in doc["nodes"] if n.get("extras", {}).get("npcforge") == "glasses"] == ["glasses"]
    moved = api("GET", f"/api/items/{item['id']}/worn/{npc_id}.glb?forward=6&up=1&tilt=2", raw=True, headers=True)
    assert moved[0] == 200 and json.loads(moved[2]["X-Fit"]) != attach
    assert api("GET", f"/api/items/{item['id']}/worn/{npc_id}.glb?up=high", raw=True)[0] == 400
    assert api("GET", f"/api/items/{item['id']}/worn/{npc_id}.glb?up=500", raw=True)[0] == 400
    assert api("GET", f"/api/items/{item['id']}/worn/no-such-npc-123456.glb", raw=True)[0] == 404


def test_deleting_a_character_forgets_its_scan_and_the_item_export_carries_its_fits(api):
    npc_id = new_npc(api.app.lib)
    item = api("POST", "/api/items", {"family": "eyewear", "kind": "glasses"})[1]
    scanned(api, npc_id)
    api("PUT", f"/api/items/{item['id']}/fits/{npc_id}", {"nudge": {"up": 1}})
    zipped = api("GET", f"/api/items/{item['id']}/export.zip", raw=True)[1]
    import zipfile
    record = json.loads(zipfile.ZipFile(io.BytesIO(zipped)).read(f"{item['id']}/item.json"))
    assert record["fits"][npc_id]["nudge"]["up"] == 1
    assert api("DELETE", f"/api/npcs/{npc_id}")[0] == 200
    assert api("GET", f"/api/npcs/{npc_id}/face")[0] == 404
