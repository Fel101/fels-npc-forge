"""The 18+ gate, the Models setting and its rules.  Run from the NPC Forge folder:   python -m pytest extensions/adult_studio/tests

No GPU, model or network is needed: ComfyUI is unreachable and never called, and everything is written to temp folders.
"""
import importlib
import json
import os
import re
import sys
import threading
import types
import urllib.error
import urllib.request
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
EXT = HERE.parent
FORGE = EXT.parents[1]
sys.path[:0] = [str(EXT.parent), str(FORGE)]

from adult_studio import models, register, safety, studio as st      # noqa: E402
from npcgen import prompts, roll                                        # noqa: E402

YOUNG_WORDS = re.compile(r"\b(girl|girls|petite|schoolgirl|youthful|teen\w*|young|kid\w*|child\w*|baby|minor|underage)\b", re.I)


# ---------------------------------------------------------------- the setting file

@pytest.fixture
def data():
    roll.add_settings_dir(EXT / "settings")
    yield roll.load_data()
    roll.EXTRA_SETTINGS.clear()


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for k, v in value.items():
            yield from strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from strings(v)


def test_the_models_setting_states_its_hard_limits_and_comes_after_the_others(data):
    s = data["settings"]["models"]
    assert s["genders"] == ["female"] and s["min_age"] == safety.MIN_AVATAR_AGE
    assert s["negative"] == safety.YOUTH_NEGATIVE                                     # the same words the studio always adds
    assert s["default_style"] in data["styles"] and s["default_style"] == "photoreal"
    assert all(g["min"] >= safety.MIN_AVATAR_AGE for g in s["age_groups"].values())
    assert list(data["settings"])[-1] == "models" and s["short"] == "Models"


def test_no_table_text_sounds_young_trips_the_rules_or_is_revealing(data):
    raw = json.loads((EXT / "settings" / "models.json").read_text("utf-8"))
    for key in ("_readme", "negative"):                                               # the note and the negative prompt name what to avoid
        raw.pop(key)
    for text in strings(raw):
        assert safety.hard_problem(text) is None, text
        assert not YOUNG_WORDS.search(text), text
        assert not safety._SEXUAL_RE.search(text), text                               # the portrait is the everyday reference


def test_every_roll_is_an_adult_woman_who_passes_the_rules(data):
    seen_builds, seen_hair, seen_jobs, seen_ages = set(), set(), set(), set()
    table = data["settings"]["models"]
    for seed in range(800):
        npc = roll.roll_npc(data, "models", {"gender": "male" if seed % 7 == 0 else "", "age_group": "teen" if seed % 5 == 0 else ""}, seed=seed)
        npc["style"] = "photoreal"
        p, a = npc["profile"], npc["appearance"]
        assert p["gender"] == "female" and safety.MIN_AVATAR_AGE <= p["age"] <= 64 and p["age_group"] in table["age_groups"], seed
        assert models.problem(npc) is None, (seed, models.problem(npc))
        assert a["outfit"] and a["hair"] and a["build"] in {b["label"] for b in table["builds"]} and not a["facial_hair"]
        roll.normalize(data, npc)
        seen_builds.add(a["build"]), seen_hair.add(a["hair"]), seen_jobs.add(p["occupation"]), seen_ages.add(p["age"])
    assert len(seen_builds) == len(table["builds"]) and len(seen_jobs) >= 15 and len(seen_hair) > 40 and max(seen_ages) >= 40


def test_almost_every_model_gets_a_beauty_look_and_glamorous_hair(data):
    """Rolled models used to be plain (no look for 55%, a messy bun for some): now nearly all get a supermodel, princess, glamour... look."""
    looks, hairs, plain = {}, set(), 0
    for seed in range(400):
        a = roll.roll_npc(data, "models", seed=seed)["appearance"]
        hairs.add(a["hair"])
        if a["distinguishing"]:
            looks[a["distinguishing"]] = looks.get(a["distinguishing"], 0) + 1
        else:
            plain += 1
    assert plain < 400 * 0.08 and len(looks) >= 9                                        # the look table is rolled, and varied
    assert any("supermodel" in k for k in looks) and any("fairy-tale princess" in k for k in looks)
    assert any("Hollywood" in k for k in looks)
    assert not any("messy" in h for h in hairs)
    assert sum("glossy" in h or "voluminous" in h or "glamorous" in h or "sleek" in h or "bouncy" in h for h in hairs) > len(hairs) * 0.4


def test_a_roll_follows_the_choices_in_the_who_card(data):
    for seed in range(40):
        npc = roll.roll_npc(data, "models", {"age_group": "adult", "occupation": "librarian", "origin": "latina"}, seed=seed)
        p = npc["profile"]
        assert 30 <= p["age"] <= 39 and p["occupation"] == "librarian" and p["heritage"] == "Latina"


def test_models_offers_its_own_heritages_and_names_them_the_way_people_do(data):
    heritages = roll.setting_common(data, "models")["heritages"]
    assert {"caucasian", "scandinavian", "japanese", "korean", "chinese", "black", "latina", "brazilian", "south_asian"} <= set(heritages)
    assert {heritages[k]["label"] for k in ("caucasian", "japanese", "korean")} == {"Caucasian", "Japanese", "Korean"}
    for origin in ("japanese", "korean", "caucasian", "scandinavian"):
        for seed in range(25):
            p = roll.roll_npc(data, "models", {"origin": origin}, seed=seed)["profile"]
            assert p["heritage"] == heritages[origin]["label"], (origin, seed)
            assert p["first_name"] in heritages[origin]["names"]["female"] and p["last_name"] in heritages[origin]["surnames"], (origin, seed)
    assert "european" in data["common"]["heritages"] and "japanese" not in data["common"]["heritages"]      # the shared list is untouched


def test_the_hair_colours_models_offers_include_blonde_and_every_one_can_be_rolled(data):
    choices = roll.hair_color_choices(data, "models")
    assert {"blonde", "platinum blonde", "black", "brown", "red"} <= set(choices)
    for color in choices:
        for seed in range(6):
            hair = roll.roll_npc(data, "models", {"hair_color": color}, seed=seed)["appearance"]["hair"]
            assert hair.startswith(color), (color, hair)
    for origin in ("japanese", "korean", "black"):                                                           # a colour the tables would not roll
        assert roll.roll_npc(data, "models", {"origin": origin, "hair_color": "blonde"}, seed=1)["appearance"]["hair"].startswith("blonde")


def test_models_are_mostly_in_their_twenties_and_none_is_younger_than_18(data):
    ages = [roll.roll_npc(data, "models", seed=s)["profile"]["age"] for s in range(400)]
    assert 18 <= min(ages) < 21 and sum(a <= 29 for a in ages) / len(ages) > 0.55                # the youngest group opens at 18
    assert data["settings"]["models"]["age_groups"]["young_adult"]["label"] == "Ages 18-29"


def test_the_portrait_prompt_says_adult_woman_and_carries_the_negative(data):
    npc = roll.roll_npc(data, "models", seed=3)
    npc["style"] = "photoreal"
    style, setting = data["styles"]["photoreal"], data["settings"]["models"]
    made = prompts.portrait(npc, style, setting)
    assert re.search(r"a \d\d-year-old woman", made["positive"]) and safety.YOUTH_NEGATIVE in made["negative"]
    assert safety.YOUTH_NEGATIVE in prompts.sprite(npc, style, setting)["negative"]


# ---------------------------------------------------------------- the guard

def person(setting="models", age=30, **extra):
    return {"setting": setting, "style": "photoreal",
            "profile": {"first_name": "Ann", "last_name": "Lee", "gender": "female", "age": age, "bio": "A florist.", **extra.pop("profile", {})},
            "appearance": {"hair": "long brown hair", "outfit": "a linen apron", **extra}}


def test_the_guard_only_looks_at_models():
    assert models.problem(person("modern", age=8, extra="a schoolgirl")) is None
    assert models.problem(None) is None and models.problem("x") is None and models.problem({}) is None


@pytest.mark.parametrize("age, text", [
    (17, ""), (1, ""), (None, ""), ("young", ""),
    (30, "a schoolgirl in a uniform"), (30, "looks like Taylor Swift"), (30, "a teenager"), (30, "her kid sister"),
    (30, "a 17-year-old woman"), (30, "aged 16"), (30, "seventeen years old"), (30, "a 15 yo student"), (30, "14 y/o"),
    (30, "a 16-year-old"), (30, "non-consensual"),
])
def test_the_guard_refuses_a_model_who_is_young_or_described_that_way(age, text):
    for where in ("extra", "outfit", "hair"):
        assert models.problem(person(age=age, **{where: text})), (age, text, where)
    assert models.problem(person(age=age, profile={"bio": text})), (age, text)


@pytest.mark.parametrize("text", ["an 18-year-old woman", "aged 19", "20 y/o", "eighteen years old", "a 21-year-old woman", "in her twenties",
                                  "twenty-something", "aged 34", "a 45-year-old", "mid-20s",
                                  "wears a striped top", "loves her nieces", "freckles across the nose"])
def test_the_guard_lets_ordinary_adult_descriptions_through(text):
    assert models.problem(person(extra=text)) is None, text


def test_a_model_may_be_18_and_a_stated_age_of_17_is_refused():
    assert safety.hard_problem("a 19-year-old woman") is None
    assert models.problem(person(age=18)) is None and models.problem(person(extra="a 19-year-old woman")) is None
    assert "18 or older" in models.problem(person(age=17))
    assert models.problem(person(extra="a 17-year-old woman"))


# ---------------------------------------------------------------- through the real server

@pytest.fixture(scope="module")
def server(tmp_path_factory):
    os.environ["NPCGEN_LIBRARY"] = str(tmp_path_factory.mktemp("library"))
    os.environ["NPCGEN_COMFY_URL"] = "http://127.0.0.1:9"
    return importlib.import_module("server")


@pytest.fixture
def app(server, tmp_path):
    """The real server with this extension registered. Its settings file is in a temp folder, never the real one."""
    routes_before = len(server.ROUTES)
    core = server.App()
    host = server.make_host(root=tmp_path, app=core)
    studio = st.Studio(host, root=tmp_path, sync=True)
    register(host, studio=studio)
    httpd = server.create_server(("127.0.0.1", 0), core)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"

    def fetch(path, method="GET", body=None):
        headers = {"Content-Type": "application/json"} if method != "GET" else {}
        data = json.dumps(body if body is not None else {}).encode() if method != "GET" else None
        try:
            with urllib.request.urlopen(urllib.request.Request(base + path, data=data, method=method, headers=headers), timeout=15) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def call(path, method="GET", body=None):
        status, raw = fetch(path, method, body)
        try:
            return status, json.loads(raw)
        except ValueError:
            return status, raw

    runtime = types.SimpleNamespace(LIB=core.lib, make_host=lambda root=None: server.make_host(root=root, app=core))
    yield types.SimpleNamespace(call=call, studio=studio, server=runtime, tmp=tmp_path,
                                config=lambda: call("/api/config")[1], tick=lambda: call("/api/ext/adult/confirm", "POST", {"confirm_adult": True}),
                                untick=lambda: call("/api/ext/adult/confirm", "DELETE"))
    httpd.shutdown()
    httpd.server_close()
    del server.ROUTES[routes_before:]
    server.reset_extensions()


def test_nothing_adult_is_listed_until_the_box_is_ticked_and_it_goes_away_when_unticked(app):
    def hidden():
        cfg = app.config()
        return "models" not in cfg["settings"] and all(e["id"] != "adult" for e in cfg["extensions"])
    assert hidden() and app.config()["ui"] == [{"id": "adult-ui", "script": "/ext/adult-ui/gate.js", "style": "/ext/adult-ui/gate.css"}]
    assert app.call("/ext/adult/")[0] == 404 and app.call("/ext/adult/studio.js")[0] == 404 and app.call("/api/ext/adult/characters")[0] == 403
    assert app.call("/ext/adult-ui/gate.js")[0] == 200 and app.call("/ext/adult-ui/gate.css")[0] == 200      # what unlocks it is always there
    assert app.call("/api/roll", "POST", {"setting": "models", "style": "photoreal"})[0] == 400

    assert app.tick()[1]["enabled"] is True
    cfg = app.config()
    assert cfg["settings"]["models"]["label"] == "Models" and list(cfg["settings"])[-1] == "models"
    assert cfg["settings"]["models"]["genders"] == [{"id": "female", "label": "Female"}] and cfg["settings"]["models"]["min_age"] == 18
    assert [e["id"] for e in cfg["extensions"]] == ["adult"]
    assert app.call("/ext/adult/")[0] == 200
    assert app.call("/api/roll", "POST", {"setting": "models", "style": "photoreal"})[0] == 200

    assert app.untick()[1]["enabled"] is False
    assert hidden() and app.call("/ext/adult/")[0] == 404 and app.call("/api/roll", "POST", {"setting": "models"})[0] == 400


def test_the_3d_bodys_nude_choice_is_listed_only_while_the_box_is_ticked(app):
    from npcgen import bodymaker
    ids = lambda: [c["id"] for c in bodymaker.clothes_choices()]
    assert ids() == ["basic", "sheet", "none"]                                                  # unticked: the Clothes list is NPC Forge's own
    with pytest.raises(ValueError, match="clothes must be basic, sheet, none$"):
        bodymaker.clean_request({"clothes": "nude"})
    app.tick()
    assert ids() == ["basic", "sheet", "none", "nude"]
    choice = bodymaker.clothes_choices()[-1]
    assert choice["label"] == "Nothing (nude)" and choice["extra"] is True and choice["underwear"] == []
    assert bodymaker.clean_request({"clothes": "nude"})["clothes"] == "nude"
    app.untick()
    assert ids() == ["basic", "sheet", "none"]
    with pytest.raises(ValueError, match="clothes must be basic, sheet, none$"):
        bodymaker.clean_request({"clothes": "nude"})


def test_the_box_stays_ticked_after_a_restart(app, server):
    app.tick()
    again = st.Studio(app.server.make_host(root=app.tmp), root=app.tmp, sync=True)
    assert again.confirmed()
    app.untick()
    assert not st.Studio(app.server.make_host(root=app.tmp), root=app.tmp, sync=True).confirmed()


def test_a_model_can_only_be_an_adult_woman_whatever_is_sent(app):
    app.tick()
    for _ in range(25):
        status, npc = app.call("/api/roll", "POST", {"setting": "models", "style": "photoreal",
                                                    "constraints": {"gender": "male", "age_group": "teen"}})
        assert status == 200 and npc["profile"]["gender"] == "female" and npc["profile"]["age"] >= 18 and npc["freeform"] is False
    bad_age = person(age=17)
    assert app.call("/api/npcs", "POST", bad_age)[0] == 400 and app.call("/api/preview", "POST", {"npc": bad_age})[0] == 400
    assert "18 or older" in app.call("/api/npcs", "POST", bad_age)[1]["error"]
    male = person()
    male["profile"]["gender"] = "male"
    assert "only has female" in app.call("/api/npcs", "POST", male)[1]["error"]
    for text in ("a schoolgirl", "a 17-year-old woman", "looks like Taylor Swift"):
        status, body = app.call("/api/npcs", "POST", person(extra=text))
        assert status == 400 and "error" in body, text
    assert app.call("/api/roll", "POST", {"setting": "models", "style": "photoreal", "locked": {"profile.age": 17}})[0] == 400


def test_a_described_model_goes_through_the_same_check(app):
    app.tick()
    for text in ("a 17-year-old woman with long red hair", "a teenage girl", "looks like Emma Watson"):
        status, body = app.call("/api/roll", "POST", {"setting": "models", "style": "photoreal", "freeform": text})
        assert status == 400 and "error" in body, text
    status, npc = app.call("/api/roll", "POST", {"setting": "models", "style": "photoreal",
                                                 "freeform": "a tall redhead with green eyes, freckles and an athletic build"})
    assert status == 200 and npc["freeform"] is True and npc["profile"]["age"] >= 18
    # the other settings are not touched by the Models rules
    assert app.call("/api/roll", "POST", {"setting": "modern", "style": "pixel", "freeform": "a 9 year old boy"})[0] == 200


def test_a_cast_file_cannot_make_a_model_who_is_young_or_male(app):
    app.tick()

    def cast(*entries, setting="models"):
        return app.call("/api/cast", "POST", {"text": json.dumps(list(entries)), "format": "json", "setting": setting, "style": "photoreal"})
    status, body = cast({"name": "Ann Lee", "age": 17})
    assert status == 400 and "18 or older" in body["error"]
    assert cast({"name": "Ann Lee", "gender": "male"})[0] == 400
    assert cast({"name": "Ann Lee", "age": 30, "description": "a schoolgirl"})[0] == 400
    status, body = cast({"name": "Bea Lee", "age": 30, "hair": "long black hair", "outfit": "a linen apron"})
    try:
        assert status == 200 and body["count"] == 1
        made = app.server.LIB.get(body["npc_ids"][0])
        assert made["setting"] == "models" and made["profile"]["gender"] == "female" and made["profile"]["age"] == 30
    finally:
        for npc_id in body.get("npc_ids", []):
            app.server.LIB.delete(npc_id)


def test_while_the_box_is_unticked_a_cast_file_cannot_reach_the_models_setting(app):
    text = json.dumps([{"name": "Cara Lee", "age": 30, "setting": "models"}])
    status, body = app.call("/api/cast", "POST", {"text": text, "format": "json", "setting": "modern", "style": "pixel"})
    try:
        assert status == 200 and any("unknown setting 'models'" in w for w in body["warnings"])
        assert app.server.LIB.get(body["npc_ids"][0])["setting"] == "modern"
    finally:
        for npc_id in body.get("npc_ids", []):
            app.server.LIB.delete(npc_id)
    status, body = app.call("/api/cast", "POST", {"text": json.dumps([{"name": "Dee Lee", "age": 30}]), "format": "json", "setting": "models", "style": "pixel"})
    try:                                                                       # asking for it at the top level falls back the same way
        assert status == 200 and app.server.LIB.get(body["npc_ids"][0])["setting"] != "models"
    finally:
        for npc_id in body.get("npc_ids", []):
            app.server.LIB.delete(npc_id)


def test_saved_models_are_hidden_and_locked_with_the_box(app):
    app.tick()
    saved = app.call("/api/npcs", "POST", person())[1]
    try:
        assert saved["id"] in [n["id"] for n in app.call("/api/npcs")[1]]
        app.untick()
        assert saved["id"] not in [n["id"] for n in app.call("/api/npcs")[1]]
        assert app.call("/api/generate", "POST", {"npc_id": saved["id"], "assets": {"portrait": True}})[0] == 403
        assert app.call(f"/api/npcs/{saved['id']}", "PUT", person())[0] == 400
        assert app.call("/api/jobs")[1] == []                                              # nothing was queued
        app.tick()
        assert saved["id"] in [n["id"] for n in app.call("/api/npcs")[1]]
    finally:
        app.server.LIB.delete(saved["id"])


# ---------------------------------------------------------------- the page files

def test_the_gate_files_exist_say_what_the_user_asked_for_and_use_no_innerhtml():
    ui = EXT / "ui"
    assert {p.name for p in ui.iterdir()} == {"gate.js", "gate.css"}
    js = (ui / "gate.js").read_text("utf-8")
    assert "innerHTML" not in js
    # the owner's design: a π in the About box opens a terminal-looking box that types "ACCESS GRANTED", then the 18+ box and the options
    for wanted in ("'π'", "ACCESS GRANTED", "18+", "I am 18 or older", "aboutFooter", "NF.reload()", "/api/ext/adult/", "prefers-reduced-motion",
                   "'settings'", "Open the Adult studio"):
        assert wanted in js, wanted
    css = (ui / "gate.css").read_text("utf-8")
    assert ".about-pi" in css and ".pi-dialog" in css
