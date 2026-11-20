"""A described (freeform) character's data sheet, and a character asked for as a grown adult (API 44; npcgen/roll.py, prompts.py,
server.py).

A companion app's report of 2026-11-10: a full name locked as the first name still got a rolled surname after it ("Wren Ashby Okafor"),
a description of a woman could be rolled male on the sheet, a rolled age was shown as if the description had said it, and a
few words in a cartoon style were drawn so young that its age check read about 11, so it added "a grown adult, " itself."""
import copy
import importlib
import json
import os
import threading
import urllib.error
import urllib.request
from types import SimpleNamespace

import pytest

from npcgen import prompts, roll
from npcgen.jobs import Jobs
from npcgen.library import Library

DATA = roll.load_data()
WREN = {"profile.first_name": "Wren Ashby", "profile.last_name": ""}


# ---- no surname ------------------------------------------------------------------------------------------------------------
def test_a_locked_empty_last_name_means_no_surname():
    described = roll.roll_npc(DATA, "modern", locked=WREN, seed=1, freeform="a tall woman with grey eyes")
    assert (described["profile"]["first_name"], described["profile"]["last_name"]) == ("Wren Ashby", "")
    assert roll.roll_npc(DATA, "modern", locked={"profile.last_name": ""}, seed=2)["profile"]["last_name"] == ""
    assert roll.roll_npc(DATA, "modern", locked={"profile.last_name": "  "}, seed=2)["profile"]["last_name"] == ""
    assert roll.roll_npc(DATA, "modern", seed=3)["profile"]["last_name"]                     # nothing locked: rolled as before
    assert roll.roll_npc(DATA, "modern", locked={"profile.last_name": None}, seed=3)["profile"]["last_name"]


def test_a_job_for_a_character_without_a_surname_has_no_trailing_space(tmp_path, monkeypatch):
    monkeypatch.setattr(Jobs, "_run_step", lambda self, job, step: None)
    lib = Library(tmp_path / "library")
    jobs = Jobs({"jobs": {"watch_seconds": 0.05}}, SimpleNamespace(models={}), lib, state_path=tmp_path / "jobs.json")
    try:
        drawn = roll.roll_npc(DATA, "modern", locked=WREN, seed=4, freeform="a tall woman with grey eyes")
        npc = lib.create({**drawn, "style": "photoreal"})
        assert jobs.submit(npc, {"portrait": True}, {"quality": "draft"})["npc_name"] == "Wren Ashby"
    finally:
        jobs.close()


# ---- the gender the words give ----------------------------------------------------------------------------------------------
@pytest.mark.parametrize("text, gender", [
    ("a tall woman with grey eyes", "female"),
    ("A weathered man in a wax jacket", "male"),
    ("she runs the caravan and never smiles", "female"),
    ("a woman and her son", "female"),                                  # the first such word wins
    ("an old knight in his dented armour", "male"),
    ("a she-wolf shaman, a man of few words", "male"),                   # a word inside a hyphenated compound does not count
    ("mother-of-pearl buttons on a grey coat", None),
    ("an androgynous courier", "nonbinary"),
    ("a wiry caravan runner", None),
    ("", None),
])
def test_a_description_gives_its_own_gender(text, gender):
    assert prompts.described_gender(text) == gender


def test_a_described_characters_gender_follows_its_words_unless_one_is_locked():
    assert {roll.roll_npc(DATA, "modern", seed=s, freeform="a tall woman")["profile"]["gender"] for s in range(30)} == {"female"}
    assert {roll.roll_npc(DATA, "modern", seed=s, freeform="a bearded smith")["profile"]["gender"] for s in range(30)} == {"male"}
    locked = roll.roll_npc(DATA, "modern", seed=1, freeform="a tall woman", locked={"profile.gender": "male"})
    assert locked["profile"]["gender"] == "male"
    silent = {roll.roll_npc(DATA, "modern", seed=s, freeform="a wiry caravan runner")["profile"]["gender"] for s in range(40)}
    assert len(silent) > 1                                              # the words say nothing: rolled, as before


# ---- the age is not presented as fact -----------------------------------------------------------------------------------------
def test_a_described_characters_age_says_where_it_came_from():
    rolled = [roll.roll_npc(DATA, "modern", seed=s, freeform="a tall woman")["profile"] for s in range(40)]
    assert all(p["age_source"] == "rolled" and p["age"] >= roll.ADULT_AGE for p in rolled)
    chosen = roll.roll_npc(DATA, "modern", seed=1, freeform="a tall woman", constraints={"age_group": "senior"})["profile"]
    assert chosen["age_source"] == "chosen" and chosen["age"] >= 65
    locked = roll.roll_npc(DATA, "modern", seed=1, freeform="a tall woman", locked={"profile.age": 34})["profile"]
    assert locked["age_source"] == "locked" and locked["age"] == 34
    assert "age_source" not in roll.roll_npc(DATA, "modern", seed=1)["profile"]       # a rolled sheet is the whole character


# ---- a grown adult --------------------------------------------------------------------------------------------------------------
def test_a_grown_adult_is_never_rolled_young_and_keeps_the_mark():
    for s in range(30):
        npc = roll.roll_npc(DATA, "modern", seed=s, adult=True)
        assert npc["adult"] is True and npc["profile"]["age"] >= roll.ADULT_AGE
    with pytest.raises(ValueError, match="adult"):
        roll.roll_npc(DATA, "modern", seed=1, adult=True, locked={"profile.age": 16})
    with pytest.raises(ValueError, match="adult"):
        roll.roll_npc(DATA, "modern", seed=1, adult=True, constraints={"age_group": "teen"}, freeform="a girl")
    assert "adult" not in roll.roll_npc(DATA, "modern", seed=1)


def test_saving_keeps_the_adult_mark_and_the_ages_source_and_cleans_odd_values():
    npc = {**roll.roll_npc(DATA, "modern", seed=1, freeform="a tall woman", adult=True), "style": "photoreal"}
    clean = roll.normalize(DATA, npc)
    assert clean["adult"] is True and clean["profile"]["age_source"] == "rolled"
    young = copy.deepcopy(npc)
    young["profile"]["age"] = 15
    with pytest.raises(ValueError, match="grown adult"):
        roll.normalize(DATA, young)
    odd = copy.deepcopy(npc)
    odd["profile"]["age_source"], odd["adult"] = "guessed", "yes"
    clean = roll.normalize(DATA, odd)
    assert "age_source" not in clean["profile"] and "adult" not in clean


@pytest.mark.parametrize("gender, text, led", [
    ("female", "A wiry caravan runner", "a grown adult woman, a wiry caravan runner"),
    ("female", "a tall man with a scar", "a grown adult man, a tall man with a scar"),      # the words win over the sheet
    ("male", "She hums while she works", "a grown adult woman, she hums while she works"),
    ("nonbinary", "a quiet courier", "a grown adult, a quiet courier"),
    ("male", "a character", "a grown adult man"),
    ("female", "a grown adult elf archer", "a grown adult elf archer"),                        # never led twice
    ("female", "a rusty robot with glowing eyes", "a rusty robot with glowing eyes"),          # not a person
])
def test_a_grown_adults_description_is_led_by_those_words(gender, text, led):
    assert prompts.adult_lead({"gender": gender}, text) == led


def test_the_picture_prompt_leads_with_a_grown_adult_only_when_asked():
    style, setting = DATA["styles"]["cartoon"], DATA["settings"]["modern"]
    grown = {**roll.roll_npc(DATA, "modern", seed=1, freeform="a wiry caravan runner", adult=True,
                             locked={"profile.gender": "female"}), "style": "cartoon"}
    assert prompts.describe(grown, style, setting, portrait=True).endswith("a grown adult woman, a wiry caravan runner")
    plain = {**grown, "adult": False}
    assert "grown adult" not in prompts.describe(plain, style, setting, portrait=True)     # as it was


# ---- through the API -------------------------------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def api(tmp_path_factory):
    os.environ["NPCGEN_LIBRARY"] = str(tmp_path_factory.mktemp("library"))
    os.environ["NPCGEN_COMFY_URL"] = "http://127.0.0.1:9"                 # nothing listens there
    server = importlib.import_module("server")
    app = server.App()
    httpd = server.create_server(("127.0.0.1", 0), app)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"

    def call(method, path, body=None):
        data = json.dumps(body).encode() if body is not None else (b"{}" if method != "GET" else None)
        headers = {"Content-Type": "application/json"} if method != "GET" else {}
        try:
            with urllib.request.urlopen(urllib.request.Request(base + path, data=data, method=method, headers=headers), timeout=30) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    call.server = server
    yield call
    httpd.shutdown()
    httpd.server_close()
    app.close() if hasattr(app, "close") else None


def test_the_gpu_can_be_asked_about_before_a_job(api):
    status, report = api("GET", "/api/gpu")
    assert status == 200 and report["ready"] is False and report["code"] == "comfy_down"
    assert "http://127.0.0.1:9" in report["message"]
    assert {"ready", "code", "message", "device", "free_mb", "total_mb", "comfy_mb", "holders", "forge_loaded", "other_gpus",
            "warning", "one_model_at_a_time"} <= set(report)
    assert api.server.API_VERSION >= 44


def test_the_roll_asks_for_a_grown_adult_and_a_name_without_surname(api):
    status, npc = api("POST", "/api/roll", {"setting": "modern", "style": "cartoon", "freeform": "a wiry caravan runner",
                                            "locked": WREN, "adult": True})
    assert status == 200 and npc["adult"] is True and npc["profile"]["last_name"] == ""
    assert npc["profile"]["age"] >= 18 and npc["profile"]["age_source"] == "rolled"
    assert api("POST", "/api/roll", {"setting": "modern", "freeform": "a girl", "adult": True,
                                     "constraints": {"age_group": "teen"}})[0] == 400
    status, plain = api("POST", "/api/roll", {"setting": "modern", "freeform": "a wiry caravan runner", "adult": "yes"})
    assert status == 200 and "adult" not in plain                         # only true asks for it


def test_an_edit_keeps_the_mark_and_the_ages_source_until_the_age_is_changed(api):
    _, drawn = api("POST", "/api/roll", {"setting": "modern", "style": "cartoon", "freeform": "a tall woman", "adult": True})
    status, saved = api("POST", "/api/npcs", drawn)
    assert status == 200 and saved["adult"] is True and saved["profile"]["age_source"] == "rolled"
    try:
        older_client = copy.deepcopy(saved)                               # a client that knows neither field sends them back without
        older_client.pop("adult")
        del older_client["profile"]["age_source"]
        older_client["profile"]["occupation"] = "caravan runner"
        status, edited = api("PUT", f"/api/npcs/{saved['id']}", older_client)
        assert status == 200 and edited["adult"] is True and edited["profile"]["age_source"] == "rolled"
        older_client["profile"]["age"] = 15
        status, refused = api("PUT", f"/api/npcs/{saved['id']}", older_client)
        assert status == 400 and "grown adult" in refused["error"]
        typed = copy.deepcopy(edited)
        typed["profile"]["age"] = edited["profile"]["age"] + 1             # an age typed by hand is no longer the rolled one
        status, edited = api("PUT", f"/api/npcs/{saved['id']}", typed)
        assert status == 200 and "age_source" not in edited["profile"] and edited["adult"] is True
    finally:
        api("DELETE", f"/api/npcs/{saved['id']}")
