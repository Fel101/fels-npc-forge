"""Local extensions: a git-ignored folder can add API routes and a page. Nothing is loaded unless main() asks for it."""
import importlib
import json
import os
import pathlib
import re
import textwrap
import threading
import urllib.error
import urllib.request

import pytest


class ServerHarness:
    def __init__(self, module):
        self.module = module
        self.app = None

    def __getattr__(self, name):
        return getattr(self.module, name)

    @property
    def LIB(self):
        return self.app.lib

    def load_extensions(self, directory):
        return self.module.load_extensions(directory, app=self.app)


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    os.environ["NPCGEN_LIBRARY"] = str(tmp_path_factory.mktemp("library"))
    os.environ["NPCGEN_COMFY_URL"] = "http://127.0.0.1:9"
    return ServerHarness(importlib.import_module("server"))


@pytest.fixture
def running(server):
    """An in-process server, with the extension state put back afterwards."""
    routes_before = len(server.ROUTES)
    server.app = server.App()
    httpd = server.create_server(("127.0.0.1", 0), server.app)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    yield base
    httpd.shutdown()
    httpd.server_close()
    server.app = None
    del server.ROUTES[routes_before:]
    server.reset_extensions()


def fetch(base, path, method="GET", body=None, headers=None):
    h = {"Content-Type": "application/json"} if method != "GET" else {}
    h.update(headers or {})
    data = json.dumps(body if body is not None else {}).encode() if method != "GET" else None
    try:
        with urllib.request.urlopen(urllib.request.Request(base + path, data=data, method=method, headers=h), timeout=15) as r:
            return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)


def make_extension(root, name, register_body, page=True):
    folder = root / name
    folder.mkdir(parents=True)
    (folder / "__init__.py").write_text(textwrap.dedent(register_body), "utf-8")
    if page:
        (folder / "web").mkdir()
        (folder / "web" / "index.html").write_text(f"<h1>{name} page</h1>", "utf-8")
    return folder


DEMO = """
    from pathlib import Path

    def register(host):
        @host.route("GET", "/api/ext/demo/ping")
        def ping(q, body):
            return {"pong": True, "has_library": host.lib is not None, "has_comfy": host.comfy is not None}

        @host.route("POST", "/api/ext/demo/act")
        def act(q, body):
            return {"did": body.get("what")}

        host.add_page("demo", "Demo", Path(__file__).parent / "web")
"""


def test_nothing_is_loaded_at_import_time(server):
    assert server.EXTENSIONS == [] and server.MOUNTS == {} and server.UI == [] and server.GUARDS == [] and server.roll.EXTRA_SETTINGS == [] and server.bodymaker.EXTRA_CLOTHES == {}


def test_an_extension_can_add_routes_and_a_page(server, running, tmp_path):
    make_extension(tmp_path, "demo", DEMO)
    assert server.load_extensions(tmp_path) == ["demo"]

    status, body, _ = fetch(running, "/api/ext/demo/ping")
    assert status == 200 and json.loads(body) == {"pong": True, "has_library": True, "has_comfy": True}
    assert json.loads(fetch(running, "/api/ext/demo/act", "POST", {"what": "x"})[1]) == {"did": "x"}

    status, body, headers = fetch(running, "/ext/demo/")
    assert status == 200 and b"demo page" in body and headers["Cache-Control"] == "no-store"
    assert json.loads(fetch(running, "/api/config")[1])["extensions"] == [{"id": "demo", "label": "Demo", "url": "/ext/demo/"}]


def test_extension_pages_cannot_read_outside_their_folder(server, running, tmp_path):
    make_extension(tmp_path, "demo", DEMO)
    (tmp_path / "demo" / "secret.txt").write_text("not for the web", "utf-8")
    server.load_extensions(tmp_path)
    assert fetch(running, "/ext/demo/missing.html")[0] == 404
    for attempt in ("/ext/demo/..%2F__init__.py", "/ext/demo/%2e%2e/%2e%2e/server.py", "/ext/demo/..%5c..%5cserver.py",
                    "/ext/demo/web/../__init__.py"):
        status, body, _ = fetch(running, attempt)
        assert status in (404, 200) and b"register" not in body and b"ThreadingHTTPServer" not in body, attempt
    assert fetch(running, "/ext/other/")[0] == 404


def test_extension_routes_get_the_same_protection_as_the_built_in_ones(server, running, tmp_path):
    make_extension(tmp_path, "demo", DEMO)
    server.load_extensions(tmp_path)
    assert fetch(running, "/api/ext/demo/act", "POST", {}, {"Origin": "http://evil.example"})[0] == 403
    assert fetch(running, "/api/ext/demo/act", "POST", {}, {"Content-Type": "text/plain"})[0] == 415
    assert fetch(running, "/api/ext/demo/ping", headers={"Host": "evil.example"})[0] == 403


def test_a_broken_extension_is_skipped_and_the_others_still_load(server, running, tmp_path, capsys):
    make_extension(tmp_path, "aaa_broken", "raise RuntimeError('boom at import')\n", page=False)
    make_extension(tmp_path, "bbb_no_register", "x = 1\n", page=False)
    make_extension(tmp_path, "ccc_bad_id", 'def register(host):\n    host.add_page("Bad Id!", "x", ".")\n', page=False)
    make_extension(tmp_path, "demo", DEMO)
    make_extension(tmp_path, "_ignored", "raise SystemExit('never loaded')\n", page=False)
    (tmp_path / "not_a_package").mkdir()

    assert server.load_extensions(tmp_path) == ["demo"]
    err = capsys.readouterr().err
    assert "boom at import" in err and "'aaa_broken' failed to load" in err and "'bbb_no_register' failed" in err
    assert fetch(running, "/api/ext/demo/ping")[0] == 200


def test_failed_registration_rolls_back_every_partial_change(server, running, tmp_path):
    make_extension(tmp_path, "partial", """
        def register(host):
            @host.route("GET", "/api/ext/partial")
            def partial(q, body):
                return {"leaked": True}
            host.add_page("partial", "Partial", host.root)
            host.add_guard(lambda npc: "leaked guard")
            raise RuntimeError("fail after registration")
    """, page=False)
    assert server.load_extensions(tmp_path) == []
    assert fetch(running, "/api/ext/partial")[0] == 404
    assert server.EXTENSIONS == [] and server.MOUNTS == {} and server.GUARDS == []


def test_a_failed_extension_leaves_neither_body_build_stage_registered(server, running, tmp_path):
    make_extension(tmp_path, "partial", """
        from npcgen.bodymaker import BuildStep

        def register(host):
            noop = lambda *args, **kwargs: None
            step = BuildStep(noop, noop, noop, noop, noop)
            host.add_body_build_step("before_clothes", "fit_test", step)
            host.add_body_build_step("body_finished", "finish_test", step)
            raise ValueError("fail after both registrations")
    """, page=False)
    assert server.load_extensions(tmp_path) == []
    assert all(not steps for steps in server.bodymaker.BUILD_STEPS.values())


def test_an_extension_can_add_a_choice_of_clothes_and_a_failed_registration_takes_it_back(server, running, tmp_path):
    make_extension(tmp_path, "broken", """
        def register(host):
            host.add_clothes_choice("leaked", "Leaked", "Should not stay.")
            raise RuntimeError("fail after registration")
    """, page=False)
    make_extension(tmp_path, "wardrobe", """
        def register(host):
            host.add_clothes_choice("bare", "Nothing at all", "No underwear either.", kinds=(), visible=lambda: True)
    """, page=False)
    assert server.load_extensions(tmp_path) == ["wardrobe"]
    assert sorted(server.bodymaker.EXTRA_CLOTHES) == ["bare"]                          # (the broken one left nothing behind)
    assert [c["id"] for c in server.bodymaker.clothes_choices()] == ["basic", "sheet", "none", "bare"]
    server.reset_extensions()
    assert server.bodymaker.EXTRA_CLOTHES == {}


def test_a_local_extension_with_the_name_of_a_shipped_one_is_skipped(server, running, tmp_path, capsys):
    make_extension(tmp_path, "demo", DEMO)
    make_extension(tmp_path, "other", DEMO.replace("demo", "other"))
    assert server.module.load_extensions(tmp_path, app=server.app, skip=["demo"]) == ["other"]
    assert "'demo'" in capsys.readouterr().err and fetch(running, "/api/ext/other/ping")[0] == 200


def test_the_core_has_no_adult_add_on_without_an_install(server, running):
    root = server_root()
    assert not (root / "extensions" / "adult_studio" / "__init__.py").exists()
    for path in ("/ext/adult/", "/ext/adult-ui/gate.js", "/api/ext/adult/state", "/soft-solver.js"):
        assert fetch(running, path)[0] == 404
    config = json.loads(fetch(running, "/api/config")[1])
    assert "models" not in config["settings"] and not config["extensions"] and not config["ui"]
    terms = re.compile(r"\bbust\b|\bjiggle\b|adult studio|π|18\+", re.IGNORECASE)
    for path in ("/", "/app.js"):
        assert not terms.search(fetch(running, path)[1].decode("utf-8")), path
    for file in (root / "README.md", *(root / "docs").rglob("*.md")):
        content = file.read_text("utf-8")
        if file.name == "extensions.md":
            content = "\n".join(line for line in content.splitlines() if not line.startswith("Separate add-ons for adults"))
        assert not terms.search(content), file


@pytest.mark.parametrize("registration", [
    'host.route("POST", "/api/roll")(lambda q, body: {"age": 12})',
    'host.add_portrait_engine(lambda **request: {"png": b"picture"})',
])
def test_an_extension_cannot_replace_the_roll_or_register_an_unguarded_portrait(server, running, tmp_path, capsys, registration):
    make_extension(tmp_path, "unsafe", f"""
        def register(host):
            host.add_guard(lambda npc: "A guard left over from a failed registration")
            {registration}
    """, page=False)
    assert server.load_extensions(tmp_path) == []
    assert "'unsafe' failed to load" in capsys.readouterr().err
    assert not server.GUARDS and not server.jobsmod.PORTRAIT_ENGINES

    status, _, _ = fetch(running, "/api/roll", "POST", {"setting": "modern", "adult": True, "locked": {"profile.age": 12}})
    assert status == 400
    status, data, _ = fetch(running, "/api/roll", "POST", {"setting": "modern", "adult": True, "locked": {"profile.age": 24}})
    assert status == 200 and json.loads(data)["profile"]["age"] == 24


def test_reading_an_old_character_reports_the_missing_add_on_without_editing_its_files(server, running):
    lib = server.LIB
    npc = server.roll.roll_npc(server.roll.load_data(), "modern", seed=1, adult=True)
    npc = lib.create({**npc, "style": "pixel"})
    npc_id = npc["id"]
    lib.write_asset(npc_id, "model_3d_rigged.glb", b"rig from the original release")
    lib.update(npc_id, lambda n: n["assets"].update({
        "model_3d": {"designed": {"soft": {"request": {"intensity": 0.5}}}},
        "rig_3d": {"file": "model_3d_rigged.glb"},
    }))
    saved = lib.resolve(npc_id, "npc.json")
    before = saved.read_bytes()
    status, data, _ = fetch(running, f"/api/npcs/{npc_id}")
    assert status == 200
    response = json.loads(data)
    assert "add-on" in response["body_extension_notice"]
    assert response["assets"]["model_3d"]["designed"]["soft"]["request"] == {"intensity": 0.5}
    assert saved.read_bytes() == before
    assert lib.read_asset(npc_id, "model_3d_rigged.glb") == b"rig from the original release"


def server_root():
    return pathlib.Path(__file__).resolve().parents[1]


def test_a_missing_extensions_folder_is_fine(server, tmp_path):
    assert server.load_extensions(tmp_path / "nope") == []


def test_add_page_refuses_ids_that_would_escape_the_url_space(server, tmp_path):
    for bad in ("", "Bad", "a/b", "..", "a b", "x" * 40, None):
        with pytest.raises(ValueError):
            server.add_page(bad, "Label", tmp_path)
    assert server.EXTENSIONS == [] and server.MOUNTS == {}


def test_adding_the_same_page_twice_keeps_one_link(server, running, tmp_path):
    server.add_page("demo", "One", tmp_path)
    server.add_page("demo", "Two", tmp_path)
    assert server.EXTENSIONS == [{"id": "demo", "label": "Two", "url": "/ext/demo/"}]


# ---------------------------------------------------------------- pages that can be switched off

def test_a_page_can_be_switched_off_and_on_without_a_restart(server, running, tmp_path):
    (tmp_path / "index.html").write_text("<h1>secret page</h1>", "utf-8")
    switch = {"on": False}
    server.add_page("gated", "Gated", tmp_path, visible=lambda: switch["on"])
    server.add_page("plain", "Plain", tmp_path)

    def links():
        return [e["id"] for e in json.loads(fetch(running, "/api/config")[1])["extensions"]]
    assert links() == ["plain"] and fetch(running, "/ext/gated/")[0] == 404 and fetch(running, "/ext/gated/index.html")[0] == 404
    assert fetch(running, "/ext/plain/")[0] == 200
    switch["on"] = True
    assert links() == ["gated", "plain"] and b"secret page" in fetch(running, "/ext/gated/")[1]
    switch["on"] = False
    assert links() == ["plain"] and fetch(running, "/ext/gated/")[0] == 404


def test_a_switch_that_breaks_counts_as_off(server, running, tmp_path):
    (tmp_path / "index.html").write_text("x", "utf-8")

    def broken():
        raise RuntimeError("boom")
    server.add_page("gated", "Gated", tmp_path, visible=broken)
    assert json.loads(fetch(running, "/api/config")[1])["extensions"] == [] and fetch(running, "/ext/gated/")[0] == 404


def test_registering_a_page_again_without_a_switch_removes_the_old_switch(server, tmp_path):
    server.add_page("demo", "One", tmp_path, visible=lambda: False)
    server.add_page("demo", "Two", tmp_path)
    assert server._shown("demo")
    server.reset_extensions()


# ---------------------------------------------------------------- a script for the main page

def make_ui(tmp_path, name="ui-dir"):
    folder = tmp_path / name
    folder.mkdir()
    (folder / "ui.js").write_text("window.demoLoaded = true;", "utf-8")
    (folder / "ui.css").write_text("body { --demo: 1; }", "utf-8")
    return folder


def test_an_extension_can_load_a_script_and_a_stylesheet_into_the_main_page(server, running, tmp_path):
    folder = make_ui(tmp_path)
    server.add_ui("demo-ui", folder, "ui.js")
    assert json.loads(fetch(running, "/api/config")[1])["ui"] == [{"id": "demo-ui", "script": "/ext/demo-ui/ui.js"}]
    server.add_ui("demo-ui", folder, "ui.js", "ui.css")                        # again: replaces, never duplicates
    ui = json.loads(fetch(running, "/api/config")[1])["ui"]
    assert ui == [{"id": "demo-ui", "script": "/ext/demo-ui/ui.js", "style": "/ext/demo-ui/ui.css"}]
    assert b"demoLoaded" in fetch(running, "/ext/demo-ui/ui.js")[1] and b"--demo" in fetch(running, "/ext/demo-ui/ui.css")[1]


def test_add_ui_refuses_missing_files_odd_names_and_bad_ids(server, tmp_path):
    folder = make_ui(tmp_path)
    (folder / "run.py").write_text("print(1)", "utf-8")
    for script, style in (("missing.js", None), ("run.py", None), ("../ui.js", None), ("ui.js", "nope.css"), ("ui.js", "ui.js.txt")):
        with pytest.raises(ValueError):
            server.add_ui("demo-ui", folder, script, style)
    with pytest.raises(ValueError):
        server.add_ui("Bad Id", folder)
    assert server.UI == [] and server.MOUNTS == {}


def test_the_main_page_has_the_about_dialog_and_an_empty_footer_for_extensions(server, running):
    status, body, _ = fetch(running, "/")
    page = body.decode()
    assert status == 200 and 'id="about"' in page and '<div class="about-foot" id="aboutFoot"></div>' in page
    assert '<script type="module" src="/app.js"></script>' in page
    js = fetch(running, "/app.js")[1].decode()
    assert "window.NPCForge = {" in js and "aboutFooter:" in js and "loadExtensionUi()" in js
    for module in ("/core.js", "/library-view.js", "/queue-view.js"):
        assert fetch(running, module)[0] == 200


# ---------------------------------------------------------------- settings that can be switched off

DATA = pathlib.Path(__file__).resolve().parent.parent / "data"


def make_settings(tmp_path, name="extra-settings", **rules):
    folder = tmp_path / name
    folder.mkdir()
    base = json.loads((DATA / "settings" / "modern.json").read_text("utf-8"))
    base.update({"label": "Hidden Setting", "short": "Hidden", "order": 9, **rules})
    (folder / "hidden.json").write_text(json.dumps(base), "utf-8")
    return folder


def person(setting="hidden", first="Ann", age=30, **extra):
    return {"setting": setting, "style": "pixel", "seed": 1, "profile": {"first_name": first, "last_name": "Lee", "gender": "female", "age": age},
            "appearance": {"outfit": "a coat", **extra}}


def test_an_extension_setting_appears_and_disappears_with_its_switch(server, running, tmp_path):
    switch = {"on": False}
    server.roll.add_settings_dir(make_settings(tmp_path), visible=lambda: switch["on"])

    def settings():
        return json.loads(fetch(running, "/api/config")[1])["settings"]
    assert "hidden" not in settings()
    assert fetch(running, "/api/roll", "POST", {"setting": "hidden", "style": "pixel"})[0] == 400
    assert fetch(running, "/api/npcs", "POST", person())[0] == 400

    switch["on"] = True
    assert settings()["hidden"]["label"] == "Hidden Setting" and list(settings())[-1] == "hidden"
    assert fetch(running, "/api/roll", "POST", {"setting": "hidden", "style": "pixel"})[0] == 200


def test_characters_of_a_switched_off_setting_are_hidden_and_cannot_be_drawn(server, running, tmp_path):
    switch = {"on": True}
    server.roll.add_settings_dir(make_settings(tmp_path), visible=lambda: switch["on"])
    hidden_id = json.loads(fetch(running, "/api/npcs", "POST", person())[1])["id"]
    normal_id = json.loads(fetch(running, "/api/npcs", "POST", person("modern", "Bob"))[1])["id"]

    def listed():
        return [n["id"] for n in json.loads(fetch(running, "/api/npcs")[1])]

    try:
        assert hidden_id in listed() and normal_id in listed()
        switch["on"] = False
        assert normal_id in listed() and hidden_id not in listed()
        everything = fetch(running, "/api/export.zip?ids=all")[1]
        assert normal_id.encode() in everything and hidden_id.encode() not in everything          # the zip lists its members' names
        assert fetch(running, "/api/generate", "POST", {"npc_id": hidden_id, "assets": {"portrait": True}})[0] == 403
        assert fetch(running, "/api/generate", "POST", {"npc_ids": [normal_id, hidden_id], "assets": {"portrait": True}})[0] == 403
        assert json.loads(fetch(running, "/api/jobs")[1]) == []                                     # nothing was queued
    finally:
        server.LIB.delete(hidden_id)
        server.LIB.delete(normal_id)


def test_a_settings_switch_that_breaks_counts_as_off(server, tmp_path):
    def broken():
        raise RuntimeError("boom")
    server.roll.add_settings_dir(make_settings(tmp_path), visible=broken)
    assert "hidden" not in server.roll.load_data()["settings"] and server.roll.hidden_setting_ids() == {"hidden"}
    server.reset_extensions()


def test_an_extension_setting_never_replaces_a_built_in_one(server, tmp_path):
    folder = make_settings(tmp_path)
    (folder / "hidden.json").rename(folder / "modern.json")
    server.roll.add_settings_dir(folder)
    assert server.roll.load_data()["settings"]["modern"]["label"] == "Modern life"
    server.reset_extensions()


def test_the_host_hands_extensions_the_new_hooks(server, running, tmp_path):
    make_extension(tmp_path, "hooks", """
        def register(host):
            assert host.add_settings and host.add_guard and host.add_ui and host.add_page
            host.add_guard(lambda npc: "blocked by the demo guard" if npc["profile"]["first_name"] == "Blocked" else None)
    """, page=False)
    assert server.load_extensions(tmp_path) == ["hooks"]
    status, body, _ = fetch(running, "/api/npcs", "POST", person("modern", "Blocked"))
    assert status == 400 and json.loads(body)["error"] == "blocked by the demo guard"


# ---------------------------------------------------------------- the guard

def test_a_guard_refuses_a_character_before_it_is_saved_previewed_or_drawn(server, running):
    server.add_guard(lambda npc: "nope: " + npc["appearance"]["extra"] if "forbidden" in npc["appearance"].get("extra", "") else None)
    bad = person("modern", "Ann", extra="a forbidden thing")
    good = person("modern", "Ann", extra="a harmless thing")
    for path, body in (("/api/npcs", bad), ("/api/preview", {"npc": bad}), ("/api/generate", {"npc": bad, "assets": {"portrait": True}})):
        status, payload, _ = fetch(running, path, "POST", body)
        assert status == 400 and json.loads(payload)["error"].startswith("nope: "), path
    assert fetch(running, "/api/preview", "POST", {"npc": good})[0] == 200
    saved = json.loads(fetch(running, "/api/npcs", "POST", good)[1])
    try:
        assert fetch(running, f"/api/npcs/{saved['id']}", "PUT", bad)[0] == 400
        assert json.loads(fetch(running, "/api/jobs")[1]) == []
    finally:
        server.LIB.delete(saved["id"])


def test_a_guard_also_sees_a_described_character(server, running):
    server.add_guard(lambda npc: "described text refused" if "forbidden" in npc["appearance"].get("extra", "") else None)
    status, body, _ = fetch(running, "/api/roll", "POST", {"setting": "modern", "style": "pixel", "freeform": "a forbidden robot"})
    assert status == 400 and json.loads(body)["error"] == "described text refused"
    assert fetch(running, "/api/roll", "POST", {"setting": "modern", "style": "pixel", "freeform": "a friendly robot"})[0] == 200


def test_generating_for_several_characters_is_all_or_nothing(server, running):
    ok = json.loads(fetch(running, "/api/npcs", "POST", person("modern", "Okay"))[1])["id"]
    bad = json.loads(fetch(running, "/api/npcs", "POST", person("modern", "Blocked"))[1])["id"]         # saved before the guard exists
    server.add_guard(lambda npc: "refused" if npc["profile"]["first_name"] == "Blocked" else None)
    try:
        status, body, _ = fetch(running, "/api/generate", "POST", {"npc_ids": [ok, bad], "assets": {"portrait": True}})
        assert status == 400 and json.loads(body)["error"] == "refused"
        assert json.loads(fetch(running, "/api/jobs")[1]) == []
    finally:
        server.LIB.delete(ok)
        server.LIB.delete(bad)


def test_a_guard_that_crashes_refuses_instead_of_waving_the_character_through(server, running, capsys):
    def broken(npc):
        raise RuntimeError("guard exploded")
    server.add_guard(broken)
    status, body, _ = fetch(running, "/api/npcs", "POST", person("modern", "Ann"))
    assert status == 400 and "could not run" in json.loads(body)["error"]
    assert "guard exploded" in capsys.readouterr().err


def test_the_config_tells_the_page_what_each_setting_allows(server, running, tmp_path):
    groups = {"young_adult": {"label": "Young adult (21-29)", "min": 21, "max": 29, "weight": 3}}
    server.roll.add_settings_dir(make_settings(tmp_path, genders=["female"], min_age=21, age_groups=groups, default_style="photoreal"))
    settings = json.loads(fetch(running, "/api/config")[1])["settings"]
    assert settings["hidden"]["genders"] == [{"id": "female", "label": "Female"}]
    assert settings["hidden"]["age_groups"] == [{"id": "young_adult", "label": "Young adult (21-29)"}]
    assert settings["hidden"]["min_age"] == 21 and settings["hidden"]["default_style"] == "photoreal"
    assert [g["id"] for g in settings["modern"]["genders"]] == ["female", "male", "nonbinary"]      # unchanged for a setting without rules
    assert settings["modern"]["min_age"] == 1 and "default_style" not in settings["modern"]
    assert [g["id"] for g in settings["modern"]["age_groups"]][:2] == ["child", "teen"]
