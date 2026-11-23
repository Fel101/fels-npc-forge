"""End-to-end API tests against a real in-process server. ComfyUI is deliberately unreachable (nothing listens on
port 9), which also lets us test the failure path of a generation job."""
import base64
import importlib
import io
import json
import os
import threading
import time
import urllib.error
import urllib.request
import zipfile

import pytest
from PIL import Image


@pytest.fixture(scope="module")
def api(tmp_path_factory):
    os.environ["NPCGEN_LIBRARY"] = str(tmp_path_factory.mktemp("library"))
    os.environ["NPCGEN_COMFY_URL"] = "http://127.0.0.1:9"
    server = importlib.import_module("server")
    app = server.App()
    httpd = server.create_server(("127.0.0.1", 0), app)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"

    def call(method, path, body=None, headers=None, raw=False, raw_body=None):
        h = {"Content-Type": "application/json"} if method != "GET" else {}
        h.update(headers or {})
        data = raw_body if raw_body is not None else json.dumps(body).encode() if body is not None else (b"{}" if method != "GET" else None)
        try:
            with urllib.request.urlopen(urllib.request.Request(base + path, data=data, method=method, headers=h), timeout=30) as r:
                payload = r.read()
                return r.status, (payload if raw else json.loads(payload))
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    call.app = app
    yield call
    httpd.shutdown()
    httpd.server_close()


def test_requests_that_did_not_come_from_our_own_page_are_refused(api):
    assert api("POST", "/api/jobs/cancel-all", headers={"Content-Type": "text/plain"})[0] == 415
    assert api("POST", "/api/jobs/cancel-all", headers={"Origin": "http://evil.example"})[0] == 403
    assert api("GET", "/api/health", headers={"Host": "evil.example"})[0] == 403


def test_character_audition_api_validates_before_queueing_and_keeps_partial_reports(api, monkeypatch):
    from npcgen.voice import audio, record
    import numpy as np
    server = importlib.import_module('server')
    _, draft = api('POST', '/api/roll', {'setting': 'modern', 'style': 'semi_real'})
    _, npc = api('POST', '/api/npcs', draft)
    path = f'/api/npcs/{npc["id"]}/voice/auditions'
    assert api('POST', path, {})[0] == 400
    wav = audio.write_wav(.2 * np.sin(2 * np.pi * 220 * np.arange(48000) / 16000), 16000)
    def install(rec):
        v = record.voice_of(rec)
        v.update(engine='qwen', mode='upload', language='English')
        record.install_reference(api.app.lib, rec, v, wav, {'id': 'a' * 12, 'text': 'Hello.'})
        rec['voice'] = v
    api.app.lib.update(npc['id'], install)
    assert api('POST', path, {'names': []})[0] == 400
    captured = []
    monkeypatch.setattr(server, '_voice_job', lambda npc_id, request: captured.append(request) or {'id': 'audition-test', 'total': 4})
    status, queued = api('POST', path, {'names': 'Vael'})
    assert status == 200 and queued['job']['total'] == 4 and len(captured[0]['tests']) == 4
    suite = queued['npc']['voice']['auditions'][0]
    assert suite['job'] == 'audition-test'
    assert len([row for row in suite['cases'] if row['status'] == 'skipped']) == 3
    assert api('PUT', path + '/' + suite['id'] + '/cases/neutral', {'sha256': 'not-generated', 'identity': 'clear'})[0] == 400
    status, packed = api('GET', path + '/' + suite['id'] + '/pack.zip', raw=True)
    assert status == 200
    with zipfile.ZipFile(io.BytesIO(packed)) as archive:
        assert {'reference.wav', 'audition.json'} == set(archive.namelist())
    assert api('GET', path + '/' + 'f' * 12 + '/pack.zip')[0] == 404


def test_importing_server_does_not_create_an_application_singleton(api):
    server = importlib.import_module("server")
    assert not hasattr(server, "APP") and not hasattr(server, "LIB") and server._CURRENT_APP.get() is None


def test_non_loopback_requires_authentication_and_never_exposes_the_token(tmp_path):
    import base64
    import copy
    from npcgen import config as cfgmod

    server = importlib.import_module("server")
    cfg = copy.deepcopy(cfgmod.DEFAULTS)
    cfg.update(library_path=tmp_path / "unprotected", comfy_url="http://127.0.0.1:9", auth_token="")
    with pytest.raises(ValueError, match="NPCGEN_AUTH_TOKEN"):
        server.create_server(("0.0.0.0", 0), server.App(cfg), require_auth=False)

    secret = "correct-horse-battery-staple"
    cfg.update(library_path=tmp_path / "protected", auth_token=secret)
    app = server.App(cfg)
    httpd = server.create_server(("127.0.0.1", 0), app, require_auth=True)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{httpd.server_address[1]}/api/config"
    try:
        with pytest.raises(urllib.error.HTTPError) as static_denied:
            urllib.request.urlopen(url.removesuffix("api/config"))
        assert static_denied.value.code == 401
        with pytest.raises(urllib.error.HTTPError) as denied:
            urllib.request.urlopen(url)
        assert denied.value.code == 401 and denied.value.headers["WWW-Authenticate"].startswith("Basic")
        token = base64.b64encode(f"npcforge:{secret}".encode()).decode()
        request = urllib.request.Request(url, headers={"Authorization": "Basic " + token, "Host": "lan.example"})
        with urllib.request.urlopen(request) as response:
            body = json.loads(response.read())
        assert response.status == 200 and secret not in json.dumps(body)
    finally:
        httpd.shutdown()
        httpd.server_close()
    assert not app.jobs.thread.is_alive()


def test_factory_servers_keep_application_state_isolated(tmp_path):
    import copy
    from npcgen import config as cfgmod

    server = importlib.import_module("server")
    apps, servers = [], []
    for name in ("one", "two"):
        cfg = copy.deepcopy(cfgmod.DEFAULTS)
        cfg.update(library_path=tmp_path / name, comfy_url="http://127.0.0.1:9")
        app = server.App(cfg)
        httpd = server.create_server(("127.0.0.1", 0), app)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        apps.append(app)
        servers.append(httpd)
    apps[0].lib.create({"setting": "modern", "style": "pixel", "seed": 1,
                        "profile": {"first_name": "Only", "last_name": "One", "age": 30, "gender": "female", "occupation": "tester"},
                        "appearance": {}})
    try:
        rows = []
        for httpd in servers:
            with urllib.request.urlopen(f"http://127.0.0.1:{httpd.server_address[1]}/api/npcs") as response:
                rows.append(json.loads(response.read()))
        assert [len(group) for group in rows] == [1, 0]
    finally:
        for httpd in servers:
            httpd.shutdown()
            httpd.server_close()


def test_a_library_has_only_one_live_application_owner(tmp_path):
    import copy
    from npcgen import config as cfgmod
    from npcgen.app import App

    cfg = copy.deepcopy(cfgmod.DEFAULTS)
    cfg.update(library_path=tmp_path / "shared", comfy_url="http://127.0.0.1:9")
    first = App(cfg)
    try:
        with pytest.raises(RuntimeError, match="already using this library"):
            App(cfg)
    finally:
        first.close()

    reopened = App(cfg)
    reopened.close()


def test_config_lists_settings_styles_and_expressions(api):
    status, cfg = api("GET", "/api/config")
    assert status == 200 and {"modern", "fantasy", "scifi", "western"} <= set(cfg["settings"])
    assert {"semi_real", "pixel", "cartoon", "photoreal"} <= set(cfg["styles"]) and len(cfg["expressions"]) >= 4
    assert cfg["settings"]["western"]["short"] == "Western" and cfg["settings"]["western"]["label"] == "Old West"    # 'Old' alone would be a poor button
    assert [v["id"] for v in cfg["sheet"]["views"]] == ["portrait", "front", "side_right", "side_left", "back"]
    assert {"a_pose", "relaxed"} <= {p["id"] for p in cfg["sheet"]["poses"]}


def test_config_lists_the_picture_engines_with_mage_flow_first(api):
    _, cfg = api("GET", "/api/config")
    assert [e["id"] for e in cfg["engines"]["image"]][0] == "mage" and [e["id"] for e in cfg["engines"]["edit"]][0] == "mage"
    assert {"zimage", "qwen21", "qwen2512"} <= {e["id"] for e in cfg["engines"]["image"]}
    assert {"qwen2511", "qwen21_edit"} <= {e["id"] for e in cfg["engines"]["edit"]}
    assert all(e["label"] and e["note"] for kind in cfg["engines"].values() for e in kind)
    assert cfg["defaults"]["options"]["image_model"] == "qwen2512" and cfg["defaults"]["options"]["edit_model"] == "qwen2511"   # (the page follows these)


def test_config_carries_the_licence_list_for_every_model_the_page_offers(api):
    """The page shows each model's commercial-use rating from this list: a picker option without an entry would show nothing."""
    _, cfg = api("GET", "/api/config")
    lic = cfg["licences"]
    assert [s["id"] for s in lic["statuses"]] == ["yes", "conditions", "unclear", "no"] and lic["checked"] and lic["disclaimer"]
    by_id = {m["id"]: m for m in lic["models"]}
    assert all(m["effective"] in ("yes", "conditions", "unclear", "no") and m["summary"] and m["licence"] for m in lic["models"])
    offered = [f"image:{e['id']}" for e in cfg["engines"]["image"]] + [f"edit:{e['id']}" for e in cfg["engines"]["edit"]]
    offered += [f"three_d:{e['id']}" for e in cfg["engines"]["three_d"]]
    assert [s for s in offered if s not in lic["by_selector"]] == []
    assert by_id[lic["by_selector"]["image:qwen2512"]]["effective"] == "yes" and by_id[lic["by_selector"]["image:qwen21"]]["effective"] == "no"
    assert by_id[lic["by_selector"]["image:mage"]]["effective"] == "unclear"


def test_a_characters_licence_report_and_its_rating_in_the_library_list(api):
    _, npc = api("POST", "/api/roll", {"setting": "modern", "style": "pixel"})
    _, saved = api("POST", "/api/npcs", npc)
    try:
        status, report = api("GET", f"/api/npcs/{saved['id']}/licences")
        assert status == 200 and report["verdict"] == "none" and report["models"] == [] and report["checked"]       # nothing made yet
        row = next(r for r in api("GET", "/api/npcs")[1] if r["id"] == saved["id"])
        assert row["commercial"] == "none"
        api.app.lib.update(saved["id"], lambda n: n.setdefault("assets", {}).update(portrait={"file": "portrait.png", "engine": "Qwen Image 2.1"}))
        status, report = api("GET", f"/api/npcs/{saved['id']}/licences")
        assert report["verdict"] == "no" and [m["id"] for m in report["models"]] == ["qwen21"]
        assert next(r for r in api("GET", "/api/npcs")[1] if r["id"] == saved["id"])["commercial"] == "no"
        assert api("GET", "/api/npcs/not-a-character/licences")[0] == 404
    finally:
        api("DELETE", f"/api/npcs/{saved['id']}")


def test_config_offers_the_detailed_head_settings_and_faces(api):
    _, cfg = api("GET", "/api/config")
    assert [q["id"] for q in cfg["head_qualities"]] == ["medium", "high"] and all(q["estimate"] and q["summary"] for q in cfg["head_qualities"])
    assert [e["id"] for e in cfg["head_expressions"]] == ["neutral", "keep"]
    assert cfg["defaults"]["options"]["head_quality"] == "medium" and cfg["defaults"]["options"]["head_expression"] == "neutral"


def test_the_detailed_head_can_be_asked_for_and_its_options_are_cleaned_by_the_server(api):
    _, npc = api("POST", "/api/roll", {"setting": "modern", "style": "photoreal"})
    _, saved = api("POST", "/api/npcs", npc)
    status, out = api("POST", "/api/generate", {"npc_id": saved["id"], "assets": {"head_3d": True},
                                                "options": {"head_quality": "high", "head_expression": "sulking"}})
    assert status == 200
    job = out["jobs"][0]
    assert [s["kind"] for s in job["steps"]][-5:] == ["head_view"] * 4 + ["head_3d"]
    assert api("DELETE", f"/api/npcs/{saved['id']}/assets/head_3d")[0] == 404          # nothing made yet
    api("POST", "/api/jobs/cancel-all")
    api("DELETE", f"/api/npcs/{saved['id']}")


def test_one_close_up_of_the_detailed_head_can_be_asked_for_alone(api):
    _, npc = api("POST", "/api/roll", {"setting": "modern", "style": "photoreal"})
    _, saved = api("POST", "/api/npcs", npc)
    status, out = api("POST", "/api/generate", {"npc_id": saved["id"], "assets": {"head_3d": {"views": ["back"], "model": False}}})
    assert status == 200 and [s["kind"] for s in out["jobs"][0]["steps"]][-1:] == ["head_view"] and out["jobs"][0]["total"] >= 1
    api("POST", "/api/jobs/cancel-all")
    assert api("POST", "/api/generate", {"npc_id": saved["id"], "assets": {"head_3d": {"views": [], "model": False}}})[0] == 400
    api("DELETE", f"/api/npcs/{saved['id']}")


def test_health_says_what_the_detailed_head_is_missing(api):
    _, health = api("GET", "/api/health")
    assert health["head_3d"] == {"missing": ["ComfyUI"]}


def test_the_advanced_options_choices_come_from_the_settings_tables(api):
    status, ch = api("GET", "/api/choices?setting=pirates")
    assert status == 200 and "captain" in ch["outfits"] and ch["traits"] and ch["optional"]
    assert api("GET", "/api/choices?setting=atlantis")[0] == 404
    assert api("GET", "/api/choices")[0] == 404


def test_a_batch_gives_every_character_the_advanced_options(api):
    locked = {"appearance.build": "stocky", "profile.quirk": "humming sea shanties", "appearance.glasses": ""}
    status, out = api("POST", "/api/generate", {"count": 2, "setting": "pirates", "style": "pixel", "constraints": {},
                                                "locked": locked, "assets": {"portrait": True}, "options": {"quality": "draft"}})
    assert status == 200 and len(out["npc_ids"]) == 2
    for npc_id in out["npc_ids"]:
        npc = api("GET", f"/api/npcs/{npc_id}")[1]
        assert npc["appearance"]["build"] == "stocky" and npc["profile"]["quirk"] == "humming sea shanties"
        assert npc["appearance"]["glasses"] == ""
    assert api("POST", "/api/generate", {"count": 1, "setting": "pirates", "locked": ["no"], "assets": {"portrait": True}})[0] == 400


def test_health_reports_comfyui_offline_instead_of_failing(api):
    status, health = api("GET", "/api/health")
    assert status == 200 and health["online"] is False
    assert "gpu_rule" in health
    config = api("GET", "/api/config")[1]
    assert health["fels"] == {"contract": 1, "app": "npc-forge", "version": config["version"], "api": config["api_version"]}


def test_identity_health_does_not_need_comfyui(api, monkeypatch):
    def unexpected_check():
        pytest.fail("Identity-only health must not contact ComfyUI")

    monkeypatch.setattr(api.app.comfy, "health", unexpected_check)
    status, health = api("GET", "/api/health?identity=1")
    assert status == 200
    assert set(health) == {"fels"}
    assert health["fels"]["app"] == "npc-forge"


def test_roll_normal_and_freeform(api):
    _, npc = api("POST", "/api/roll", {"setting": "modern", "style": "pixel", "constraints": {"gender": "male"}})
    assert npc["profile"]["gender"] == "male" and not npc["freeform"]
    _, free = api("POST", "/api/roll", {"setting": "modern", "style": "cartoon", "freeform": "a tiny robot chef"})
    assert free["freeform"] and free["appearance"]["extra"] == "a tiny robot chef"
    assert api("POST", "/api/roll", {"setting": "modern", "freeform": "   "})[0] == 400
    assert api("POST", "/api/roll", {"setting": "atlantis"})[0] == 400


def test_npc_lifecycle_and_preview(api):
    _, draft = api("POST", "/api/roll", {"setting": "modern", "style": "semi_real"})
    status, saved = api("POST", "/api/npcs", draft)
    assert status == 200 and saved["id"]
    saved["profile"]["first_name"] = "Renamed"
    assert api("PUT", f"/api/npcs/{saved['id']}", saved)[1]["profile"]["first_name"] == "Renamed"
    status, prev = api("POST", "/api/preview", {"npc": saved, "options": {}})
    assert status == 200 and "Renamed" not in prev["portrait"]["positive"] and prev["key_colour"]
    assert api("GET", f"/api/npcs/{saved['id']}/files/../../server.py")[0] == 404
    assert api("DELETE", f"/api/npcs/{saved['id']}")[0] == 200 and api("GET", f"/api/npcs/{saved['id']}")[0] == 404


def test_a_deleted_character_goes_to_the_trash_and_comes_back_through_the_routes(api):
    _, draft = api("POST", "/api/roll", {"setting": "modern", "style": "pixel", "freeform": "a clockwork gardener"})
    _, saved = api("POST", "/api/npcs", draft)
    assert api("DELETE", f"/api/npcs/{saved['id']}")[0] == 200 and api("GET", f"/api/npcs/{saved['id']}")[0] == 404
    status, trash = api("GET", "/api/trash")
    assert status == 200 and saved["id"] in [row["id"] for row in trash]
    assert saved["id"] not in [n["id"] for n in api("GET", "/api/npcs")[1]]
    status, card = api("POST", f"/api/trash/{saved['id']}/restore")
    assert status == 200 and card["id"] == saved["id"]
    assert api("GET", f"/api/npcs/{saved['id']}")[0] == 200 and saved["id"] not in [row["id"] for row in api("GET", "/api/trash")[1]]
    assert api("POST", f"/api/trash/{saved['id']}/restore")[0] == 404                 # nothing of it is left in the trash
    api("DELETE", f"/api/npcs/{saved['id']}")


def test_the_list_can_be_asked_for_one_project_and_without_the_scratch_characters(api):
    made = []
    for custom in ({"project": "wots-test-a", "key": "a"}, {"project": "wots-test-a", "test": True}, {"project": "wots-test-b"}, {}):
        _, draft = api("POST", "/api/roll", {"setting": "modern", "style": "pixel"})
        made.append(api("POST", "/api/npcs", {**draft, "custom": custom})[1]["id"])
    try:
        ids = lambda query: {n["id"] for n in api("GET", "/api/npcs" + query)[1]} & set(made)
        assert ids("") == set(made)                                                    # no filter: as before
        assert ids("?project=wots-test-a") == {made[0], made[1]}
        assert ids("?project=wots-test-a&test=0") == {made[0]} and ids("?project=wots-test-a&test=1") == {made[1]}
        assert ids("?test=0") == {made[0], made[2], made[3]} and ids("?test=1") == {made[1]}
        assert ids("?project=nothing-here") == set() and ids("?test=maybe") == set(made)
    finally:
        for npc_id in made:
            api("DELETE", f"/api/npcs/{npc_id}")


def test_an_edit_can_name_the_assets_it_leaves_fresh(api):
    """NF-07: fixing one picture's wording meant marking the sprite, the sheet and the 3D model stale too."""
    _, draft = api("POST", "/api/roll", {"setting": "modern", "style": "pixel", "freeform": "a clockwork gardener"})
    _, saved = api("POST", "/api/npcs", draft)

    def add_assets(npc):
        npc["assets"] = {"portrait": {"file": "portrait.png"}, "sprite": {"file": "sprite.png"}, "model_3d": {"file": "m.glb"},
                         "sheet": {"file": "s.png", "views": {"front": {"file": "f.png"}}},
                         "expressions": {"happy": {"file": "happy.png"}}, "animations": {"walk": {"file": "walk.png"}}}

    api.app.lib.update(saved["id"], add_assets)
    edited = api("GET", f"/api/npcs/{saved['id']}")[1]
    edited["appearance"]["extra"] = "a brass clockwork gardener"
    status, updated = api("PUT", f"/api/npcs/{saved['id']}", {**edited, "keep_fresh": ["sprite", "sheet", "model_3d", "animations"]})
    assert status == 200
    assets = updated["assets"]
    assert assets["portrait"]["stale"] and assets["expressions"]["happy"]["stale"]       # what the edit does touch
    assert "stale" not in assets["sprite"] and "stale" not in assets["model_3d"] and "stale" not in assets["sheet"]
    assert "stale" not in assets["sheet"]["views"]["front"] and "stale" not in assets["animations"]["walk"]
    for bad in ("sprite", ["sprites"], [3]):
        assert api("PUT", f"/api/npcs/{saved['id']}", {**edited, "keep_fresh": bad})[0] == 400
    again = api("PUT", f"/api/npcs/{saved['id']}", {**edited, "appearance": {**edited["appearance"], "extra": "a rusty clockwork gardener"}})[1]
    assert again["assets"]["sprite"]["stale"] and again["assets"]["sheet"]["views"]["front"]["stale"]       # the default is unchanged
    api("DELETE", f"/api/npcs/{saved['id']}")


def test_pictures_that_are_still_right_can_be_marked_fresh_without_making_them_again(api):
    """NF-07: the only way to take a stale flag off was to generate the asset again."""
    _, draft = api("POST", "/api/roll", {"setting": "modern", "style": "pixel", "freeform": "a clockwork gardener"})
    _, saved = api("POST", "/api/npcs", draft)

    def add_assets(npc):
        npc["assets"] = {"portrait": {"file": "portrait.png"}, "sprite": {"file": "sprite.png"}, "model_3d": {"file": "m.glb"},
                         "sheet": {"file": "s.png", "views": {"front": {"file": "f.png"}}},
                         "expressions": {"happy": {"file": "happy.png"}}, "animations": {"walk": {"file": "walk.png"}}}

    api.app.lib.update(saved["id"], add_assets)
    edited = api("GET", f"/api/npcs/{saved['id']}")[1]
    edited["appearance"]["extra"] = "a brass clockwork gardener"
    stale = api("PUT", f"/api/npcs/{saved['id']}", edited)[1]["assets"]
    assert all(stale[k]["stale"] for k in ("portrait", "sprite", "model_3d", "sheet")) and stale["sheet"]["views"]["front"]["stale"]
    status, fresh = api("POST", f"/api/npcs/{saved['id']}/fresh", {"kinds": ["sprite", "sheet", "animations"]})
    assets = fresh["assets"]
    assert status == 200 and "stale" not in assets["sprite"] and "stale" not in assets["sheet"] and "stale" not in assets["sheet"]["views"]["front"]
    assert "stale" not in assets["animations"]["walk"] and assets["portrait"]["stale"] and assets["model_3d"]["stale"] and assets["expressions"]["happy"]["stale"]
    for bad in ({}, {"kinds": []}, {"kinds": "sprite"}, {"kinds": ["sprites"]}):
        assert api("POST", f"/api/npcs/{saved['id']}/fresh", bad)[0] == 400
    assert api("POST", "/api/npcs/no-such-npc-123456/fresh", {"kinds": ["sprite"]})[0] == 404
    api("DELETE", f"/api/npcs/{saved['id']}")


def test_a_misspelt_engine_or_framing_is_refused_instead_of_quietly_replaced(api):
    _, draft = api("POST", "/api/roll", {"setting": "modern", "style": "pixel"})
    _, saved = api("POST", "/api/npcs", draft)
    ask = lambda options: api("POST", "/api/generate", {"npc_id": saved["id"], "assets": {"portrait": True}, "options": options})
    status, out = ask({"image_model": "qwen25122"})
    assert status == 400 and "unknown image engine 'qwen25122'" in out["error"] and "qwen2512" in out["error"]
    assert ask({"edit_model": "qwen2511 "})[0] == 400 and ask({"image_model": "qwen2511"})[0] == 400
    status, out = ask({"portrait_framing": "headshot"})
    assert status == 400 and "headshot" in out["error"] and "bust, half, full" in out["error"]
    assert ask({"engine_fallback": "no"})[0] == 400
    api("DELETE", f"/api/npcs/{saved['id']}")


def test_the_page_is_told_the_new_option_defaults(api):
    options = api("GET", "/api/config")[1]["defaults"]["options"]
    assert options["engine_fallback"] is False and options["portrait_framing"] == "bust" and options["game_faces"] == 100_000


def test_the_scene_background_follows_the_models_choice_the_page_sends_and_a_misspelt_engine_is_refused(api, monkeypatch):
    seen = []
    monkeypatch.setattr(api.app.jobs, "submit_scene_background", lambda scene_id, options=None: seen.append((scene_id, options)) or {"id": "job"})
    assert api("POST", "/api/scenes/scene-one/background")[0] == 200
    assert seen[-1][1]["image_model"] == "qwen2512" and seen[-1][1]["engine_fallback"] is False       # the app's defaults when the page names nothing
    status, out = api("POST", "/api/scenes/scene-one/background", {"options": {"image_model": "mage", "engine_fallback": True}})
    assert status == 200 and out["job"] == {"id": "job"}
    assert seen[-1][0] == "scene-one" and seen[-1][1]["image_model"] == "mage" and seen[-1][1]["engine_fallback"] is True
    status, out = api("POST", "/api/scenes/scene-one/background", {"options": {"image_model": "qwen25122"}})
    assert status == 400 and "unknown image engine 'qwen25122'" in out["error"] and len(seen) == 2


def test_the_page_is_offered_the_game_sizes_and_a_wrong_one_is_refused(api):
    _, cfg = api("GET", "/api/config")
    assert [(s["id"], s["faces"]) for s in cfg["game_sizes"]] == [("light", 25_000), ("standard", 50_000), ("detailed", 100_000), ("high", 200_000)]
    _, draft = api("POST", "/api/roll", {"setting": "modern", "style": "pixel"})
    _, saved = api("POST", "/api/npcs", draft)
    ask = lambda options: api("POST", "/api/generate", {"npc_id": saved["id"], "assets": {"refine_3d": True}, "options": options})
    status, out = ask({"game_faces": 5000})
    assert status == 400 and "game_faces" in out["error"] and "10,000" in out["error"]
    assert ask({"game_faces": "huge"})[0] == 400
    api("DELETE", f"/api/npcs/{saved['id']}")


def test_editing_prompt_data_marks_every_generated_asset_stale(api):
    _, draft = api("POST", "/api/roll", {"setting": "modern", "style": "pixel", "freeform": "a clockwork gardener"})
    _, saved = api("POST", "/api/npcs", draft)
    def add_assets(npc):
        npc["assets"] = {
            "portrait": {"file": "portrait.png"}, "sprite": {"file": "sprite.png"},
            "expressions": {"happy": {"file": "happy.png"}},
            "animation_views": {"right": {"file": "right.png"}},
            "animations": {"walk": {"file": "walk.png"}}, "sheet": {"file": "sheet.png"},
        }

    api.app.lib.update(saved["id"], add_assets)
    edited = api("GET", f"/api/npcs/{saved['id']}")[1]
    edited["appearance"]["extra"] = "a brass clockwork gardener"
    updated = api("PUT", f"/api/npcs/{saved['id']}", edited)[1]
    assert updated["freeform"] is True
    assets = updated["assets"]
    assert all(assets[k]["stale"] for k in ("portrait", "sprite", "sheet"))
    assert all(meta["stale"] for k in ("expressions", "animation_views", "animations") for meta in assets[k].values())
    api("DELETE", f"/api/npcs/{saved['id']}")


def test_candidate_selection_updates_the_active_image_and_preserves_the_previous_one(api):
    _, draft = api("POST", "/api/roll", {"setting": "modern", "style": "semi_real"})
    _, saved = api("POST", "/api/npcs", draft)
    old_png = io.BytesIO()
    Image.new("RGBA", (8, 8), (220, 30, 30, 255)).save(old_png, "PNG")
    variant_png = io.BytesIO()
    Image.new("RGBA", (8, 8), (30, 30, 220, 255)).save(variant_png, "PNG")
    npc_id, candidate_id = saved["id"], "abc123abc123"
    api.app.lib.write_asset(npc_id, "portrait.png", old_png.getvalue())
    api.app.lib.write_asset(npc_id, f"candidates/portrait/{candidate_id}.png", variant_png.getvalue())
    api.app.lib.update(npc_id, lambda npc: npc["assets"].update({
        "portrait": {"file": "portrait.png", "style": "semi_real"},
        "candidates": {"portrait": [{"id": candidate_id, "file": f"candidates/portrait/{candidate_id}.png",
                                      "style": "semi_real", "created": "candidate"}]},
    }))

    status, updated = api("POST", f"/api/npcs/{npc_id}/assets/portrait/candidates/{candidate_id}/select")
    assert status == 200 and updated["assets"]["portrait"]["candidate_id"] == candidate_id
    assert api.app.lib.read_asset(npc_id, "portrait.png") == variant_png.getvalue()
    assert api.app.lib.read_asset(npc_id, updated["assets"]["candidates"]["portrait"][1]["file"]) == old_png.getvalue()
    api("DELETE", f"/api/npcs/{npc_id}")


def test_dialogue_pack_can_be_generated_edited_and_exported_without_a_text_model(api):
    _, draft = api("POST", "/api/roll", {"setting": "modern", "style": "pixel"})
    _, saved = api("POST", "/api/npcs", draft)
    npc_id = saved["id"]
    status, result = api("POST", f"/api/npcs/{npc_id}/dialogue/generate")
    assert status == 200 and len(result["dialogue"]["lines"]) == 5
    assert api("GET", f"/api/npcs/{npc_id}")[1]["dialogue"]["generator"] == "npcforge-profile-templates"

    pack = result["dialogue"]
    pack["lines"]["greeting"][0]["text"] = "Welcome to my workshop."
    status, saved_pack = api("PUT", f"/api/npcs/{npc_id}/dialogue", {"dialogue": pack})
    assert status == 200 and saved_pack["dialogue"]["lines"]["greeting"][0]["text"] == "Welcome to my workshop."
    assert api("PUT", f"/api/npcs/{npc_id}/dialogue", {"dialogue": {"lines": {"greeting": [""]}}})[0] == 400

    for fmt, expected in (("json", b"Welcome to my workshop."), ("ink", b"workshop"), ("yarn", b"workshop")):
        status, content = api("GET", f"/api/npcs/{npc_id}/dialogue/export/{fmt}", raw=True)
        assert status == 200 and expected in content
    edited_npc = api("GET", f"/api/npcs/{npc_id}")[1]
    edited_npc["profile"]["goal"] = "open an honest shop"
    status, updated_npc = api("PUT", f"/api/npcs/{npc_id}", edited_npc)
    assert status == 200 and updated_npc["dialogue"]["stale"] is True
    api("DELETE", f"/api/npcs/{npc_id}")


def _photo_data_url():
    out = io.BytesIO()
    Image.new("RGB", (48, 48), (10, 120, 200)).save(out, "PNG")
    return "data:image/png;base64," + base64.b64encode(out.getvalue()).decode("ascii")


def test_uploading_a_photo_attaches_it_marks_the_portrait_stale_and_is_never_exported(api):
    _, npc = api("POST", "/api/roll", {"setting": "pirates", "style": "render3d"})
    _, saved = api("POST", "/api/npcs", npc)
    npc_id = saved["id"]
    api.app.lib.write_asset(npc_id, "portrait.png", base64.b64decode(_photo_data_url().split(",", 1)[1]))
    api.app.lib.update(npc_id, lambda n: n.setdefault("assets", {}).update(portrait={"file": "portrait.png"}))

    assert api("POST", "/api/npcs/no-such-npc/photo", {"image": _photo_data_url()})[0] == 404
    assert api("POST", f"/api/npcs/{npc_id}/photo", {"image": "not an image"})[0] == 400

    status, updated = api("POST", f"/api/npcs/{npc_id}/photo", {"image": _photo_data_url()})
    assert status == 200
    photo = updated["assets"]["source_photo"]
    assert photo["file"] == "source_photo.png" and photo["size"] == [48, 48] and photo["created"]
    assert updated["assets"]["portrait"]["stale"] is True, "an old portrait was made without this photo, so it should not look current"

    status, prev = api("POST", "/api/preview", {"npc": updated, "npc_id": npc_id, "options": {}})
    assert status == 200 and "identity" in prev["portrait"]["positive"] and "reference photo" in prev["portrait"]["positive"]

    status, blob = api("GET", "/api/export.zip?ids=" + npc_id, raw=True)
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        assert not any("source_photo" in name for name in z.namelist()), "the real photo must never leave the machine via export"

    assert api("DELETE", f"/api/npcs/{npc_id}/assets/source_photo")[0] == 200
    assert api("DELETE", f"/api/npcs/{npc_id}/assets/source_photo")[0] == 404
    api("DELETE", f"/api/npcs/{npc_id}")


def test_an_oversized_photo_upload_is_rejected_before_it_is_read(api):
    """The server checks Content-Length and refuses an oversized body BEFORE reading it, so it never buffers a huge upload;
    it then drains the unread bytes (Handler._drain_body), so the client gets the 413 instead of a reset connection."""
    _, npc = api("POST", "/api/roll", {"setting": "pirates", "style": "render3d"})
    _, saved = api("POST", "/api/npcs", npc)
    huge = "data:image/png;base64," + "A" * 3_000_000
    status, _ = api("POST", f"/api/npcs/{saved['id']}/photo", {"image": huge})
    assert status == 413
    api("DELETE", f"/api/npcs/{saved['id']}")


def test_a_refused_request_gets_its_error_reply_not_a_dropped_connection(api):
    """Refused before its body was read (here: not JSON), the reply must still arrive. Without draining the unread body,
    closing the socket reset the connection on Windows and the client saw ConnectionAbortedError instead of the 415: 2 times
    in 30 with a 1 MB body, and now and then with a 2-byte one when the machine was busy (2026-09-25)."""
    for _ in range(20):
        assert api("POST", "/api/jobs/cancel-all", headers={"Content-Type": "text/plain"}, raw_body=b"x" * 1_000_000)[0] == 415


def test_cast_import_with_custom_data_warnings_and_export(api):
    text = json.dumps([{"name": "Ada Vale", "description": "an elderly orc blacksmith", "schedule": "dawn-dusk"},
                       {"name": "Bo Chen", "age": "old", "job": "plumber"}])
    status, res = api("POST", "/api/cast", {"text": text, "setting": "modern", "style": "pixel"})
    assert status == 200 and res["count"] == 2 and len(res["warnings"]) == 1
    _, ada = api("GET", f"/api/npcs/{res['npc_ids'][0]}")
    assert ada["freeform"] and ada["custom"] == {"schedule": "dawn-dusk"}
    assert api("POST", "/api/cast", {"text": "not a cast"})[0] == 400
    status, blob = api("GET", "/api/export.zip?ids=" + ",".join(res["npc_ids"]), raw=True)
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        assert "index.json" in z.namelist()


def test_cast_pack_import_edit_export_and_member_cleanup(api):
    text = json.dumps({
        "pack": {"name": "Lanternwatch", "template": "guild", "shared_context": "A harbor patrol.",
                 "uniqueness_constraints": ["Only one captain"], "rumors": ["A bell rings underwater."],
                 "plot_hooks": ["A sealed door appears at low tide."]},
        "characters": [
            {"name": "Ada Vale", "cast_role": "Captain", "schedule": "dawn-dusk"},
            {"name": "Bo Chen", "cast_role": "Scout", "job": "dockworker"},
        ],
        "relationships": [{"from": "Ada Vale", "to": 2, "type": "mentor", "notes": "Old rivals."}],
    })
    status, result = api("POST", "/api/cast-packs/import", {"text": text, "format": "json", "setting": "modern", "style": "pixel"})
    assert status == 200 and result["count"] == 2
    pack = result["pack"]
    assert pack["name"] == "Lanternwatch" and pack["template"] == "guild"
    assert [member["role"] for member in pack["members"]] == ["Captain", "Scout"]
    assert pack["relationships"][0]["from"] == result["npc_ids"][0]
    assert api("GET", "/api/cast-packs")[1][0]["id"] == pack["id"]

    first = api("GET", f"/api/npcs/{result['npc_ids'][0]}")[1]
    assert first["custom"] == {"schedule": "dawn-dusk"}
    status, blob = api("GET", f"/api/cast-packs/{pack['id']}/export.zip", raw=True)
    assert status == 200
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        assert "cast-pack.json" in archive.namelist()
        assert f"members/{result['npc_ids'][0]}/npc.json" in archive.namelist()
        exported_npc = json.loads(archive.read(f"members/{result['npc_ids'][0]}/npc.json"))
        assert exported_npc["custom"] == {"schedule": "dawn-dusk"}

    edited = {**pack, "name": "Lanternwatch Expanded", "shared_context": "The docks are under curfew."}
    assert api("PUT", f"/api/cast-packs/{pack['id']}", edited)[1]["name"] == "Lanternwatch Expanded"
    invalid = {**edited, "members": [pack["members"][0], pack["members"][0]]}
    assert api("PUT", f"/api/cast-packs/{pack['id']}", invalid)[0] == 400

    api("DELETE", f"/api/npcs/{result['npc_ids'][0]}")
    remaining = api("GET", f"/api/cast-packs/{pack['id']}")[1]
    assert [member["npc_id"] for member in remaining["members"]] == [result["npc_ids"][1]]
    assert remaining["relationships"] == []
    api("DELETE", f"/api/npcs/{result['npc_ids'][1]}")
    assert api("GET", f"/api/cast-packs/{pack['id']}")[0] == 404


def test_invalid_cast_pack_import_does_not_leave_orphan_npcs(api):
    before = {npc["id"] for npc in api("GET", "/api/npcs")[1]}
    bad = {"pack": {"name": "Bad links"}, "characters": [{"name": "Pack Import Temporary"}],
           "relationships": [{"from": 1, "to": 2, "type": "ally"}]}
    status, error = api("POST", "/api/cast-packs/import", {"text": json.dumps(bad), "format": "json"})
    assert status == 400 and "outside this cast" in error["error"]
    assert {npc["id"] for npc in api("GET", "/api/npcs")[1]} == before


def test_cast_validation_is_transactional(api):
    before = {n["id"] for n in api("GET", "/api/npcs")[1]}
    status, _ = api("POST", "/api/cast", {
        "text": json.dumps([{"name": "Must Not Persist"}]), "setting": "modern", "style": "pixel",
        "generate": True, "assets": [],
    })
    assert status == 400
    assert {n["id"] for n in api("GET", "/api/npcs")[1]} == before


def test_generate_can_target_several_existing_characters_at_once(api):
    _, res = api("POST", "/api/cast", {"text": json.dumps([{"name": "One Test"}, {"name": "Two Test"}]), "setting": "modern", "style": "pixel"})
    status, out = api("POST", "/api/generate", {"npc_ids": res["npc_ids"], "assets": {"portrait": True}, "options": {"quality": "draft"}})
    assert status == 200 and out["npc_ids"] == res["npc_ids"] and len(out["jobs"]) == 2
    assert api("POST", "/api/generate", {"npc_ids": ["no-such-npc"], "assets": {"portrait": True}})[0] == 404


def test_morphology_metadata_and_taur_full_body_requests_are_handled_safely(api):
    _, config = api("GET", "/api/config")
    assert config["morphologies"]["taur"]["full_body_supported"] is False
    anthro_origins = {origin["id"]: origin for origin in config["settings"]["anthro"]["origins"]}
    assert anthro_origins["taur"]["morphology"] == "taur"
    assert anthro_origins["dragontaur"]["morphology"] == "taur"
    assert anthro_origins["wolf"]["morphology"] == "bipedal"

    _, rolled = api("POST", "/api/roll", {"setting": "anthro", "constraints": {"origin": "taur"}})
    assert rolled["morphology"] == "taur"
    _, saved = api("POST", "/api/npcs", rolled)
    assert api("POST", "/api/generate", {"npc_id": saved["id"], "assets": {"sprite": True}})[0] == 400
    assert api("POST", "/api/generate", {"npc_id": saved["id"], "assets": {"portrait": True}})[0] == 200

    before = {npc["id"] for npc in api("GET", "/api/npcs")[1]}
    status, error = api("POST", "/api/generate", {
        "count": 1, "setting": "anthro", "constraints": {"origin": "taur"}, "assets": {"sprite": True}})
    assert status == 400 and "stay blocked until a checked method is ready" in error["error"]
    assert {npc["id"] for npc in api("GET", "/api/npcs")[1]} == before


def test_animation_requests_are_planned_validated_and_exported_through_the_api(api):
    _, npc = api("POST", "/api/roll", {"setting": "modern", "style": "pixel"})
    _, saved = api("POST", "/api/npcs", npc)
    status, res = api("POST", "/api/generate", {"npc_id": saved["id"], "options": {"quality": "draft"}, "assets": {
        "animations": {"clips": ["walk", "idle"], "dirs": ["down", "left", "right", "up"], "frames": 4, "candidates": 2}}})
    assert status == 200
    kinds = [(s["kind"], s["key"]) for s in res["jobs"][0]["steps"]]
    assert kinds[:3] == [("sprite", None), ("view", "right"), ("view", "up")] and ("anim", "walk:right") in kinds and kinds[-1] == ("assemble", "idle")

    assert api("POST", "/api/generate", {"npc_id": saved["id"], "assets": {"animations": {"clips": ["walk"], "frames": 5}}})[0] == 400
    assert api("POST", "/api/generate", {"npc_id": saved["id"], "assets": {"animations": {"clips": ["fly"]}}})[0] == 400
    assert api("DELETE", f"/api/npcs/{saved['id']}/assets/animations/walk")[0] == 404
    assert api("DELETE", f"/api/npcs/{saved['id']}/assets/animations")[0] == 400
    status, err = api("GET", f"/api/npcs/{saved['id']}/export/walk/atlas.zip")
    assert status == 400 and "no finished 'walk' animation" in err["error"]
    _, cfg = api("GET", "/api/config")
    assert {"walk", "idle"} <= {c["id"] for c in cfg["animations"]["clips"]}
    assert {p["id"] for p in cfg["animations"]["presets"]} == {"atlas", "grid4", "godot", "unity", "aseprite"}
    assert {c["id"] for c in cfg["animations"]["clips"]} >= {"run", "attack", "hurt", "cast", "death"}


def test_model_sheet_requests_are_planned_validated_and_exported_through_the_api(api):
    _, npc = api("POST", "/api/roll", {"setting": "western", "style": "photoreal"})
    _, saved = api("POST", "/api/npcs", npc)
    status, res = api("POST", "/api/generate", {"npc_id": saved["id"], "options": {"quality": "draft"},
                                                 "assets": {"sheet": {"views": ["back", "front"], "pose": "relaxed"}}})
    assert status == 200
    assert [(s["kind"], s["key"]) for s in res["jobs"][0]["steps"]] == [("sprite", None), ("sheet", "front"), ("sheet", "back"), ("sheet_stitch", None)]
    assert api("POST", "/api/generate", {"npc_id": saved["id"], "assets": {"sheet": {"pose": "cartwheel"}}})[0] == 400
    assert api("POST", "/api/generate", {"npc_id": saved["id"], "assets": {"sheet": {"views": ["nope"]}}})[0] == 400
    status, err = api("GET", f"/api/npcs/{saved['id']}/sheet.zip")
    assert status == 400 and "no model sheet" in err["error"]
    assert api("DELETE", f"/api/npcs/{saved['id']}/assets/sheet")[0] == 404
    assert api("DELETE", f"/api/npcs/{saved['id']}/assets/sheet/front")[0] == 400


def test_a_generation_job_fails_cleanly_when_comfyui_is_unreachable(api):
    _, res = api("POST", "/api/generate", {"count": 1, "setting": "modern", "style": "pixel",
                                           "assets": {"portrait": True}, "options": {"quality": "draft"}})
    job_id = res["jobs"][0]["id"]
    for _ in range(150):                    # earlier tests' jobs are queued ahead, and a refused connection can take 2 s on Windows
        job = next(j for j in api("GET", "/api/jobs")[1] if j["id"] == job_id)
        if job["status"] not in ("queued", "running"):
            break
        time.sleep(0.2)
    assert job["status"] == "error" and job["error"].startswith("Cannot reach ComfyUI")
    assert api("POST", "/api/generate", {"count": 1, "setting": "modern", "assets": {}})[0] == 400


def test_manner_suggestions_come_from_the_tables_and_need_a_known_setting(api):
    status, got = api("POST", "/api/manner", {"setting": "western", "traits": ["shy", "polite"]})
    assert status == 200 and got["speech"] and got["demeanor"]
    assert api("POST", "/api/manner", {"setting": "nowhere", "traits": []})[0] == 400
    assert api("POST", "/api/manner", {"setting": "modern", "traits": "calm, patient"})[0] == 200      # a comma list is accepted too
    assert api("POST", "/api/manner", {"setting": "modern", "traits": {"calm": True}})[0] == 400


def test_wrong_request_shapes_are_client_errors_and_browser_keeps_freeform(api):
    assert api("POST", "/api/generate", {"setting": "modern", "assets": ["portrait"]})[0] == 400
    assert api("POST", "/api/preview", {"npc": [], "options": {}})[0] == 400
    _, npc = api("POST", "/api/roll", {"setting": "modern", "style": "pixel"})
    npc["profile"] = []
    assert api("POST", "/api/npcs", npc)[0] == 400
    import pathlib
    js = (pathlib.Path(__file__).resolve().parent.parent / "web" / "core.js").read_text("utf-8")
    assert "freeform: !!npc.freeform" in js


def test_a_rolled_character_carries_its_manner_through_the_library(api):
    status, npc = api("POST", "/api/roll", {"setting": "modern", "style": "pixel"})
    assert status == 200 and npc["profile"]["speech"] and npc["profile"]["demeanor"]
    npc["profile"]["speech"] = "whispers everything"
    status, saved = api("POST", "/api/npcs", npc)
    assert status == 200 and saved["profile"]["speech"] == "whispers everything"
    assert api("GET", f"/api/npcs/{saved['id']}")[1]["profile"]["demeanor"] == npc["profile"]["demeanor"]


def test_the_page_and_the_server_agree_on_the_api_version(api):
    """web/app.js and server.py must be bumped together, or a stale server would crash the page instead of showing a banner."""
    import pathlib
    import re
    js = (pathlib.Path(__file__).resolve().parent.parent / "web" / "app.js").read_text("utf-8")
    required = int(re.search(r"const REQUIRED_API = (\d+);", js).group(1))
    assert api("GET", "/api/config")[1]["api_version"] == required


def test_starting_a_second_copy_on_a_busy_port_explains_what_is_going_on(api):
    import socket
    import subprocess
    import sys
    busy = socket.socket()
    busy.bind(("127.0.0.1", 0))
    busy.listen(1)
    try:
        out = subprocess.run([sys.executable, "server.py", "--port", str(busy.getsockname()[1])], capture_output=True, text=True,
                             timeout=60, cwd=str(__import__("pathlib").Path(__file__).resolve().parent.parent),
                             env={**__import__("os").environ, "NPCGEN_LIBRARY": __import__("tempfile").mkdtemp()})
    finally:
        busy.close()
    assert out.returncode != 0
    assert "Close the app using that port" in out.stderr
    assert "separate NPCGEN_LIBRARY" in out.stderr and "--port 8768" in out.stderr


def test_the_trash_can_be_looked_at_emptied_one_at_a_time_or_all_at_once_through_the_routes(api):
    folders = []
    for name in ("Trash", "Bin"):
        _, draft = api("POST", "/api/roll", {"setting": "modern", "style": "pixel"})
        draft["profile"]["first_name"], draft["profile"]["last_name"] = name, "Test"
        _, saved = api("POST", "/api/npcs", draft)
        picture = io.BytesIO()
        Image.new("RGBA", (8, 8), (30, 30, 220, 255)).save(picture, "PNG")
        api.app.lib.write_asset(saved["id"], "portrait.png", picture.getvalue())
        api.app.lib.update(saved["id"], lambda npc: npc["assets"].update(portrait={"file": "portrait.png"}))
        api.app.lib.make_thumb(saved["id"])
        assert api("DELETE", f"/api/npcs/{saved['id']}")[0] == 200
        folders.append(next(row for row in api("GET", "/api/trash")[1] if row["id"] == saved["id"]))
    assert all(row["thumb"] and row["has"]["portrait"] and row["occupation"] and row["deleted"] for row in folders)
    first, second = folders
    status, shown = api("GET", f"/api/trash/{first['folder']}/thumb.png", raw=True)
    assert status == 200 and shown.startswith(b"\x89PNG")
    assert api("GET", "/api/trash/not-a-folder/thumb.png")[0] == 404
    assert api("GET", f"/api/trash/{first['folder']}/npc.json")[0] == 404                       # only the small picture is served
    assert api("DELETE", f"/api/trash/{first['folder']}")[1] == {"removed": first["folder"]}
    assert api("DELETE", f"/api/trash/{first['folder']}")[0] == 404 and api("GET", f"/api/trash/{first['folder']}/thumb.png")[0] == 404
    assert api("POST", f"/api/trash/{first['id']}/restore")[0] == 404                             # gone for good
    assert second["id"] in [row["id"] for row in api("GET", "/api/trash")[1]]
    status, emptied = api("POST", "/api/trash/empty")
    assert status == 200 and emptied["removed"] >= 1 and api("GET", "/api/trash")[1] == []


def test_the_page_is_told_how_long_the_trash_waits_and_how_many_earlier_versions_are_kept(api):
    library = api("GET", "/api/config")[1]["library"]
    assert library == {"trash_days": 30, "picture_history": 6, "model_history": 2}


def test_the_earlier_3d_models_are_listed_restored_and_deleted_through_the_routes(api):
    _, draft = api("POST", "/api/roll", {"setting": "modern", "style": "pixel"})
    _, saved = api("POST", "/api/npcs", draft)
    npc_id, lib = saved["id"], api.app.lib

    def model(tag):
        lib.write_asset(npc_id, "model_3d.glb", f"model {tag}".encode())
        lib.write_asset(npc_id, "model_3d_preview.png", f"picture {tag}".encode())
        lib.update(npc_id, lambda npc: npc["assets"].update(model_3d={"file": "model_3d.glb", "preview": "model_3d_preview.png", "created": f"2026-10-15T1{tag}:00:00",
                                                                     "size": 7, "engine": f"Engine {tag}"}))
    model("1")
    kept = lib.archive_model(npc_id)
    model("2")
    listed = api("GET", f"/api/npcs/{npc_id}")[1]["assets"]["model_history"]
    assert [e["id"] for e in listed] == [kept["id"]] and listed[0]["model"]["engine"] == "Engine 1" and listed[0]["bytes"] > 0
    assert api("GET", f"/api/npcs/{npc_id}/files/model_history/{kept['id']}/model_3d_preview.png", raw=True)[0] == 200       # the page can show its picture

    busy = api.app.jobs.busy_npc                                       # a job for this character is working: nothing changes under it
    api.app.jobs.busy_npc = lambda other: other == npc_id
    try:
        assert api("POST", f"/api/npcs/{npc_id}/model-history/{kept['id']}/restore")[0] == 409
        assert api("DELETE", f"/api/npcs/{npc_id}/model-history/{kept['id']}")[0] == 409
    finally:
        api.app.jobs.busy_npc = busy
    assert lib.read_asset(npc_id, "model_3d.glb") == b"model 2"

    status, after = api("POST", f"/api/npcs/{npc_id}/model-history/{kept['id']}/restore")
    assert status == 200 and after["assets"]["model_3d"]["engine"] == "Engine 1" and lib.read_asset(npc_id, "model_3d.glb") == b"model 1"
    (other,) = after["assets"]["model_history"]
    assert other["model"]["engine"] == "Engine 2"                      # the one that was in use is kept in turn
    assert api("POST", f"/api/npcs/{npc_id}/model-history/ffffffffffff/restore")[0] == 404
    assert api("POST", f"/api/npcs/{npc_id}/model-history/{kept['id']}/restore")[0] == 404         # (it is the one in use now)
    status, after = api("DELETE", f"/api/npcs/{npc_id}/model-history/{other['id']}")
    assert status == 200 and "model_history" not in after["assets"] and lib.read_asset(npc_id, "model_3d.glb") == b"model 1"
    assert api("DELETE", f"/api/npcs/{npc_id}/model-history/{other['id']}")[0] == 404
    api("DELETE", f"/api/npcs/{npc_id}")


def test_a_glb_from_the_owners_computer_becomes_the_model_through_the_route(api, monkeypatch):
    from test_import3d import glb, mesh_doc
    server = importlib.import_module("server")
    _, draft = api("POST", "/api/roll", {"setting": "modern", "style": "pixel"})
    _, saved = api("POST", "/api/npcs", draft)
    npc_id, lib = saved["id"], api.app.lib
    path, kind = f"/api/npcs/{npc_id}/model-upload?name=Kessa%20final.glb", {"Content-Type": "application/x-npcforge-glb"}
    data = glb(mesh_doc(faces=300))

    assert api("POST", path, headers={"Content-Type": "text/plain"}, raw_body=data)[0] == 415             # (only our own page's kind of request)
    status, refused = api("POST", path, {})                                                              # (a JSON body is not a file)
    assert status == 400 and ".glb file itself" in refused["error"]
    assert api("POST", f"/api/npcs/nobody-000000/model-upload", headers=kind, raw_body=data)[0] == 404

    status, made = api("POST", path, headers=kind, raw_body=data)
    assert status == 200 and made["assets"]["model_3d"]["engine"] == "Uploaded: Kessa final.glb" and made["upload"]["faces"] == 300
    assert api("GET", f"/api/npcs/{npc_id}/files/model_3d.glb", raw=True)[1] == data                    # (the page's viewer and the download get the same bytes)
    assert "model_history" not in made["assets"]

    status, again = api("POST", path.replace("final", "second"), headers=kind, raw_body=glb(mesh_doc(faces=400)))
    assert status == 200 and again["assets"]["model_3d"]["engine"] == "Uploaded: Kessa second.glb"
    (kept,) = again["assets"]["model_history"]                                                           # (the first upload is among the earlier models)
    assert kept["model"]["engine"] == "Uploaded: Kessa final.glb"

    status, bad = api("POST", path, headers=kind, raw_body=b"this is not a model at all, it is some text")
    assert status == 400 and "not a GLB" in bad["error"]
    assert lib.get(npc_id)["assets"]["model_3d"]["engine"] == "Uploaded: Kessa second.glb"

    busy = api.app.jobs.busy_npc
    api.app.jobs.busy_npc = lambda other: other == npc_id
    try:
        assert api("POST", path, headers=kind, raw_body=data)[0] == 409
    finally:
        api.app.jobs.busy_npc = busy
    monkeypatch.setattr(server, "MAX_MODEL_BODY", 100)
    status, big = api("POST", path, headers=kind, raw_body=data)
    assert status == 413 and "too large" in big["error"]
    api("DELETE", f"/api/npcs/{npc_id}")


def test_the_earlier_versions_of_a_picture_are_listed_restored_and_deleted_through_the_routes(api):
    _, draft = api("POST", "/api/roll", {"setting": "modern", "style": "pixel"})
    _, saved = api("POST", "/api/npcs", draft)
    npc_id, lib = saved["id"], api.app.lib

    def picture(colour):
        out = io.BytesIO()
        Image.new("RGBA", (8, 8), colour).save(out, "PNG")
        return out.getvalue()

    red, blue = picture((220, 30, 30, 255)), picture((30, 30, 220, 255))
    lib.write_asset(npc_id, "expressions/happy.png", red)
    lib.update(npc_id, lambda npc: npc["assets"].update(expressions={"happy": {"file": "expressions/happy.png", "seed": 1}}))
    kept = lib.archive_picture(npc_id, "expressions/happy")
    lib.write_asset(npc_id, "expressions/happy.png", blue)                                         # the redraw
    lib.update(npc_id, lambda npc: npc["assets"]["expressions"]["happy"].update(seed=2))
    listed = api("GET", f"/api/npcs/{npc_id}")[1]["assets"]["candidates"]["expressions/happy"]
    assert [c["id"] for c in listed] == [kept["id"]] and listed[0]["thumb"] and listed[0]["seed"] == 1
    assert api("GET", f"/api/npcs/{npc_id}/files/{listed[0]['thumb']}", raw=True)[0] == 200       # the page can show it

    # a queued or running job for this character is drawing: nothing changes under it
    busy = api.app.jobs.busy_npc
    api.app.jobs.busy_npc = lambda other: other == npc_id
    try:
        assert api("POST", f"/api/npcs/{npc_id}/assets/expressions/happy/candidates/{kept['id']}/select")[0] == 409
        assert api("DELETE", f"/api/npcs/{npc_id}/assets/expressions/happy/candidates/{kept['id']}")[0] == 409
    finally:
        api.app.jobs.busy_npc = busy
    assert lib.read_asset(npc_id, "expressions/happy.png") == blue

    status, after = api("POST", f"/api/npcs/{npc_id}/assets/expressions/happy/candidates/{kept['id']}/select")
    assert status == 200 and after["assets"]["expressions"]["happy"]["seed"] == 1
    assert lib.read_asset(npc_id, "expressions/happy.png") == red
    assert [c["seed"] for c in after["assets"]["candidates"]["expressions/happy"]] == [1, 2]       # the blue one is kept in turn
    blue_entry = after["assets"]["candidates"]["expressions/happy"][1]
    assert api("POST", f"/api/npcs/{npc_id}/assets/expressions/happy/candidates/ffffffffffff/select")[0] == 404
    assert api("POST", f"/api/npcs/{npc_id}/assets/sprite/candidates/{blue_entry['id']}/select")[0] == 404
    status, after = api("DELETE", f"/api/npcs/{npc_id}/assets/expressions/happy/candidates/{blue_entry['id']}")
    assert status == 200 and [c["seed"] for c in after["assets"]["candidates"]["expressions/happy"]] == [1]
    assert not lib.resolve(npc_id, blue_entry["file"]).exists() and lib.read_asset(npc_id, "expressions/happy.png") == red
    assert api("DELETE", f"/api/npcs/{npc_id}/assets/expressions/happy/candidates/{blue_entry['id']}")[0] == 404
    # names that are not pictures never reach the library
    assert api("POST", f"/api/npcs/{npc_id}/assets/model_3d/candidates/{blue_entry['id']}/select")[0] in (404, 405)
    assert api("DELETE", f"/api/npcs/{npc_id}/assets/sheet/front/candidates/abc")[0] == 404
    api("DELETE", f"/api/npcs/{npc_id}")


def test_a_make_preview_says_what_a_redo_leaves_out_of_date_and_queues_nothing(api):
    import hashlib
    from npcgen import creatures
    store = api.app.creatures
    _, made = api("POST", "/api/creatures", {"name": "Hound Two", "project": "ember-hollow", "body_plan": "serpentine",
                                             "prompt": "a worm"})
    cid = made["id"]
    picture = hashlib.sha256(b"picture").hexdigest()
    store.write_file(cid, "concept.png", b"picture")
    store.write_file(cid, "concept.json", json.dumps({"engine": "qwen2512", "sha256": picture}))
    store.write_file(cid, "raw/model.glb", b"raw")
    store.write_file(cid, "raw/run.json", json.dumps({"engine": "trellis2_single", "concept_sha256": picture, "seed": made["seed"]}))
    store.write_file(cid, "model.glb", b"glb")
    store.write_file(cid, "prep.json", json.dumps({"raw_sha256": hashlib.sha256(b"raw").hexdigest(),
                                                   "settings": creatures.prep_settings(store.get(cid))}))
    api("POST", f"/api/creatures/{cid}/approve")
    try:
        status, preview = api("POST", f"/api/creatures/{cid}/make",
                              {"request": {"steps": ["concept"], "redo": ["concept"]}, "preview": True})
        assert status == 200 and preview["out_of_date"] == ["3d", "prep"]
        assert [step["key"] for step in preview["steps"]] == ["concept"]
        assert store.resolve(cid, "concept.png").read_bytes() == b"picture"          # a preview changes nothing
        assert not any(job.creature_id == cid for job in api.app.jobs.jobs.values())  # and queues nothing
        _, view = api("GET", f"/api/creatures/{cid}")
        assert view["freshness"]["3d"]["state"] == "fresh" and view["freshness"]["prep"]["state"] == "fresh"
    finally:
        api("DELETE", f"/api/creatures/{cid}")                                    # always: the library is shared by the module's tests


def test_the_creature_api_lists_by_game_approves_a_picture_and_serves_a_pack(api):
    store = api.app.creatures
    _, mine = api("POST", "/api/creatures", {"name": "Reedcoil", "project": "Ember Hollow", "body_plan": "serpentine",
                                             "key": "eh-s01", "prompt": "a side view", "seed": 7})
    assert mine["project"] == "ember-hollow" and mine["licence"]["verdict"] == "none"
    _, other = api("POST", "/api/creatures", {"name": "Hound", "project": "dust-moon", "body_plan": "quadruped"})
    _, listed = api("GET", "/api/creatures?project=ember-hollow")
    assert [c["name"] for c in listed["creatures"]] == ["Reedcoil"]
    assert "plans" in listed and listed["plans"]["serpentine"]["status"] == "rigged"
    cid = mine["id"]
    assert api("POST", f"/api/creatures/{cid}/approve")[0] == 400           # no picture yet
    assert api("GET", f"/api/creatures/{cid}/pack.zip")[0] == 409           # nothing made yet
    store.write_file(cid, "concept.png", b"png")
    store.write_file(cid, "concept.json", json.dumps({"engine": "qwen2512", "seed": 7}))
    store.write_file(cid, "raw/model.glb", b"raw")
    store.write_file(cid, "raw/run.json", json.dumps({"engine": "trellis2_single", "model": "microsoft/TRELLIS.2-4B"}))
    store.write_file(cid, "model.glb", b"glTF")
    store.write_file(cid, "prep.json", json.dumps({"plan": "serpentine", "size_m": [0.3, 2.5, 0.4], "triangles_after": 20000,
                                                   "triangles_before": 90000, "bones": 12, "clips": ["Idle"], "minutes": 1.0}))
    assert api("POST", f"/api/creatures/{cid}/approve")[1]["concept_approved"] is True
    status, pack = api("GET", f"/api/creatures/{cid}/pack.zip", raw=True)
    assert status == 200
    with zipfile.ZipFile(io.BytesIO(pack)) as archive:
        assert "manifest.json" in archive.namelist() and "model.glb" in archive.namelist()
        assert json.loads(archive.read("manifest.json"))["licence"]["verdict"] == "conditions"
    assert api("DELETE", f"/api/creatures/{other['id']}")[0] == 200
    assert api("DELETE", f"/api/creatures/{cid}")[0] == 200


def test_the_picture_grid_refuses_bad_seeds_and_choices_before_anything_is_queued(api):
    _, made = api("POST", "/api/creatures", {"name": "Grid hound", "project": "ember-hollow", "body_plan": "quadruped",
                                             "prompt": "a side view"})
    cid = made["id"]
    steps = {"steps": ["concept"]}
    assert api("POST", f"/api/creatures/{cid}/make", {"request": steps, "options": {"seeds": ["x"]}})[0] == 400
    assert api("POST", f"/api/creatures/{cid}/make", {"request": steps, "options": {"seeds": [1, 1]}})[0] == 400
    assert api("POST", f"/api/creatures/{cid}/concept/choose", {"seed": 5})[0] == 400
    assert api("POST", f"/api/creatures/{cid}/concept/choose", {})[0] == 400
    assert api("DELETE", f"/api/creatures/{cid}")[0] == 200


def test_the_projects_api_makes_a_game_whose_creatures_take_its_budget_and_wait_for_its_acceptance(api):
    store = api.app.creatures
    game = {"name": "Third Game", "tri_budget": 8000, "style_prefix": "Painted watercolour concept art,", "negative_extra": "",
            "background": "Plain grey background.", "commercial": True, "engine": "godot-4", "pack_dir": "", "key_prefix": "",
            "clip_map": {"serpentine": {"idle": "Idle"}}, "licence_acceptance": None}
    status, saved = api("PUT", "/api/creature-projects/third-game", game)
    assert status == 200 and saved["slug"] == "third-game" and saved["tri_budget"] == 8000
    assert api("GET", "/api/creature-projects/third-game")[1]["style_prefix"] == "Painted watercolour concept art,"
    assert api("GET", "/api/creature-projects/no-such-game")[0] == 404
    assert api("PUT", "/api/creature-projects/third-game", {**game, "tri_budget": 9})[0] == 400
    _, listed = api("GET", "/api/creature-projects")
    assert "third-game" in [p["slug"] for p in listed["projects"]] and listed["engines"] == ["godot-4"]
    _, made = api("POST", "/api/creatures", {"name": "Third hound", "project": "third-game", "body_plan": "serpentine",
                                             "prompt": "a hound", "seed": 3})
    cid = made["id"]
    assert made["tris"] == 8000                                                  # the game's budget: none was given
    store.write_file(cid, "concept.png", b"png")
    store.write_file(cid, "concept.json", json.dumps({"engine": "qwen2512", "seed": 3}))
    store.write_file(cid, "raw/model.glb", b"raw")
    store.write_file(cid, "raw/run.json", json.dumps({"engine": "trellis2_single", "model": "microsoft/TRELLIS.2-4B", "seed": 3}))
    store.write_file(cid, "model.glb", b"glTF")
    store.write_file(cid, "prep.json", json.dumps({"plan": "serpentine", "size_m": [0.3, 2.5, 0.4], "triangles_after": 6000,
                                                   "elongation": 2.3, "bones": 12, "clips": ["Idle"], "minutes": 1.0}))
    assert api("POST", f"/api/creatures/{cid}/approve")[0] == 200
    status, refused = api("GET", f"/api/creatures/{cid}/pack.zip")
    assert status == 409 and "has not accepted them yet" in refused["error"]
    game["licence_acceptance"] = {"verdict": "conditions", "accepted_by": "Test Director", "date": "2026-10-17", "reason": "a test game"}
    assert api("PUT", "/api/creature-projects/third-game", game)[0] == 200
    status, pack = api("GET", f"/api/creatures/{cid}/pack.zip", raw=True)
    assert status == 200
    with zipfile.ZipFile(io.BytesIO(pack)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
    assert manifest["accepted_licence_conditions"]["accepted_by"] == "Test Director" and manifest["project"] == "third-game"
    assert api("DELETE", f"/api/creatures/{cid}")[0] == 200


def test_the_bestiary_route_adds_one_game_and_says_how_many(api):
    status, result = api("POST", "/api/creatures", {"seed_bestiary": True, "project": "dust-moon"})
    assert status == 200 and result["added"] == 1 and result["listed"] == 1
    _, again = api("POST", "/api/creatures", {"seed_bestiary": True, "project": "dust-moon"})
    assert again["added"] == 0
    _, listed = api("GET", "/api/creatures?project=dust-moon")
    burrower = next(c for c in listed["creatures"] if c["key"] == "dustmoon-burrower")
    assert api("GET", f"/api/creatures/{burrower['id']}")[1]["tris"] == 6000       # the game's budget, as its brief was seeded
    assert api("DELETE", f"/api/creatures/{burrower['id']}")[0] == 200


def test_a_new_game_can_not_replace_one_that_exists_and_a_game_named_in_full_is_found(api):
    game = {"name": "Fourth Game", "tri_budget": 8000, "style_prefix": "Painted watercolour concept art,", "negative_extra": "",
            "background": "Plain grey background.", "commercial": True, "engine": "godot-4", "pack_dir": "", "key_prefix": "",
            "clip_map": {"serpentine": {"idle": "Idle"}}, "licence_acceptance": None}
    assert api("POST", "/api/creature-projects", {"slug": "ember-hollow", **game})[0] == 409      # never replaced
    status, created = api("POST", "/api/creature-projects", {"slug": "fourth-game", **game})
    assert status == 200 and created["slug"] == "fourth-game"
    assert api("POST", "/api/creature-projects", {"slug": "fourth-game", **game})[0] == 409
    assert api("POST", "/api/creature-projects", {"slug": "Bad Slug", **game})[0] == 400
    _, made = api("POST", "/api/creatures", {"name": "Named hound", "project": "Fourth Game", "body_plan": "serpentine",
                                             "prompt": "a hound", "seed": 4})
    assert made["project"] == "fourth-game" and made["tris"] == 8000
    assert api("DELETE", f"/api/creatures/{made['id']}")[0] == 200
