"""Tests for the adult studio. Run from the NPC Forge folder:   python -m pytest extensions/adult_studio/tests

No GPU, model or network is needed. ComfyUI and the vision model are fakes, and the library and settings live in temp folders.
Two tests look at Altera (the companion app) when NPCGEN_ALTERA names its folder, so the two projects cannot drift apart unnoticed.
"""
import itertools
import os
import json
import re
import subprocess
import sys
import threading
import types
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
EXT = HERE.parent
FORGE = EXT.parents[1]
sys.path[:0] = [str(EXT.parent), str(FORGE)]

from adult_studio import body as bodies, engines, register, safety, studio as st   # noqa: E402
from npcgen.comfy import DEFAULT_MODELS, Cancelled, ComfyError            # noqa: E402
from npcgen.jobs import PortraitFallback                       # noqa: E402
from npcgen.library import Library                             # noqa: E402

PNG = b"\x89PNG\r\n\x1a\nfake-image"
HER = Path(os.environ.get("NPCGEN_ALTERA") or FORGE / "no-altera-here")         # Altera's folder, when it is on this PC
HER_PYTHON = HER / ".venv" / "Scripts" / "python.exe"


class FakeComfy:
    models = DEFAULT_MODELS

    def __init__(self):
        self.uploads, self.graphs, self.error = [], [], None
        # what the Qwen engine asks of ComfyUI (nothing here means: the studio makes the picture with Mage-Flow)
        self.files = {}                                      # model folder -> the files ComfyUI "has"
        self.requests = []                                   # (method, path) of everything sent besides graphs: the flushes
        self.mb = {"vram_total": 32600, "vram_free": 31000, "ram_free": 45000}
        self.running_mb = {}                                 # overrides of mb once a picture is running
        self.busy = False                                    # the queue never goes idle
        self.finishes = True                                 # a submitted picture completes at once (False = it never does)
        self.outcome = "success"
        self.submitted = False
        self.dead_when_running = False                       # ComfyUI stops answering once a picture is running

    def upload_image(self, png, name, **kw):
        self.uploads.append((name, png))
        return f"npc_generator/{name} [temp]"

    def run(self, graph, cancel=None, timeout=900):
        self.graphs.append(graph)
        if self.error:
            raise self.error
        return PNG

    def get_json(self, path, timeout=30):
        if self.error and path.startswith("/models/"):
            raise self.error
        if path.startswith("/models/"):
            return list(self.files.get(path.split("/")[2], []))
        if path == "/system_stats":
            if self.submitted and self.dead_when_running:
                raise ComfyError("Cannot reach ComfyUI")
            m = {**self.mb, **(self.running_mb if self.submitted else {})}
            return {"devices": [{"vram_total": m["vram_total"] * 2 ** 20, "vram_free": m["vram_free"] * 2 ** 20}],
                    "system": {"ram_free": m["ram_free"] * 2 ** 20}}
        if path == "/queue":
            return {"queue_running": [1] if self.busy else [], "queue_pending": []}
        if path.startswith("/history/"):
            pid = path.split("/")[2]
            if not self.finishes:
                return {}
            return {pid: {"status": {"completed": True, "status_str": self.outcome, "messages": []},
                          "outputs": {"22": {"images": [{"filename": "q.png", "subfolder": "", "type": "temp"}]}}}}
        raise AssertionError(path)

    def _request(self, method, path, data=None, headers=None, timeout=30):
        self.requests.append((method, path))
        return b"{}"

    def submit(self, graph):
        self.graphs.append(graph)
        self.submitted = True
        return "job-1"

    def fetch_image(self, filename, subfolder="", image_type="temp"):
        return PNG + b"qwen"


def make_npc(lib, first="Mara", age=34, portrait=True, bio="A florist.", gender="female", style="photoreal"):
    npc = lib.create({
        "setting": "modern", "style": style, "seed": 1, "freeform": False,
        "profile": {"first_name": first, "last_name": "Bell", "age": age, "gender": gender, "bio": bio, "occupation": "florist"},
        "appearance": {"build": "sturdy", "skin_tone": "fair", "hair": "long brown hair", "eyes": "hazel", "outfit": "an apron"},
    })
    if portrait:
        (lib.root / npc["id"] / "portrait.png").write_bytes(PNG + b"portrait")
    return npc["id"]


@pytest.fixture
def env(tmp_path):
    lib = Library(tmp_path / "library")
    comfy = FakeComfy()
    host = types.SimpleNamespace(lib=lib, comfy=comfy)
    studio = st.Studio(host, root=tmp_path, sync=True)
    return types.SimpleNamespace(lib=lib, comfy=comfy, studio=studio, tmp=tmp_path, host=host)


@pytest.fixture
def ready(env):
    env.studio.confirm(True)
    env.npc = make_npc(env.lib)
    return env


def items_file(env):
    return json.loads((env.lib.root / env.npc / "adult" / "items.json").read_text("utf-8"))["items"]


# ---------------------------------------------------------------- the 18+ gate

def test_the_studio_is_off_until_18_plus_is_confirmed(env):
    npc = make_npc(env.lib)
    assert env.studio.state()["enabled"] is False
    for action in (lambda: env.studio.record(npc), lambda: env.studio.add_items(npc, ["cafe"]), lambda: env.studio.set_notes(npc, "x"),
                   lambda: env.studio.update_settings({"quality": "draft"}), lambda: env.studio.image_path(npc, "s-00000000"),
                   lambda: env.studio.set_keep(npc, "s-00000000", True), lambda: env.studio.redo(npc, "s-00000000")):
        with pytest.raises(st.Forbidden):
            action()
    for bad in (False, None, "yes", 1, "true"):
        with pytest.raises(st.StudioError, match="18 or older"):
            env.studio.confirm(bad)
    assert env.studio.confirm(True)["enabled"] is True
    first = env.studio.state()["confirmed_at"]
    assert env.studio.confirm(True)["confirmed_at"] == first                       # confirming again keeps the first timestamp
    assert json.loads((env.tmp / "local" / "adult_studio.json").read_text("utf-8"))["confirmed_at"] == first


def test_the_confirmation_survives_a_restart(env):
    env.studio.confirm(True)
    assert st.Studio(env.host, root=env.tmp, sync=True).confirmed()


class Clock:
    """Time that only moves when the code sleeps, so the guard's waits cost nothing."""
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(engines, "time", types.SimpleNamespace(monotonic=c, sleep=c.sleep))
    return c


QWEN = engines.load_template(engines.ENGINES["qwen"]["workflow"])


def give_comfy_the_qwen_files(env):
    for folder, name in engines.required_models(QWEN):
        env.comfy.files.setdefault(folder, []).append(name)


# ---------------------------------------------------------------- who can be used

def test_only_adults_with_a_portrait_and_clean_text_are_eligible(ready):
    make_npc(ready.lib, first="Young", age=17)
    make_npc(ready.lib, first="Nopic", portrait=False)
    make_npc(ready.lib, first="Schoolish", bio="Loves being a schoolgirl at heart.")
    make_npc(ready.lib, first="Noage", age=None)
    rows = {r["name"]: r for r in ready.studio.characters()}
    assert rows["Mara Bell"]["eligible"] and rows["Mara Bell"]["reason"] == ""
    assert not rows["Young Bell"]["eligible"] and "18 or older" in rows["Young Bell"]["reason"]
    assert not rows["Nopic Bell"]["eligible"] and "portrait" in rows["Nopic Bell"]["reason"]
    assert not rows["Schoolish Bell"]["eligible"] and "not allowed" in rows["Schoolish Bell"]["reason"]
    assert not rows["Noage Bell"]["eligible"] and "stated age" in rows["Noage Bell"]["reason"]
    with pytest.raises(st.StudioError, match="18 or older"):
        ready.studio.add_items(next(r["id"] for r in ready.studio.characters() if r["name"] == "Young Bell"), ["cafe"])


def test_the_character_list_carries_the_traits_the_page_filters_by(ready):
    row = next(r for r in ready.studio.characters() if r["name"] == "Mara Bell")
    assert (row["hair"], row["hair_color"], row["eyes"], row["build"], row["skin_tone"]) == ("long brown hair", "Brown", "hazel", "sturdy", "fair")
    assert (row["setting"], row["occupation"], row["heritage"], row["ethnicity"], row["style"], row["age"]) == ("modern", "florist", "", "", "photoreal", 34)


def test_unknown_and_hostile_character_ids(ready):
    for bad in ("nope-000000", "../x", ""):
        with pytest.raises(st.NotFound):
            ready.studio.record(bad)


# ---------------------------------------------------------------- body notes

def test_body_notes_are_saved_where_her_reads_them(ready):
    record = ready.studio.set_notes(ready.npc, "  athletic build,   defined waist  ")
    assert record["notes"] == "athletic build, defined waist"
    assert json.loads((ready.lib.root / ready.npc / "adult" / "studio.json").read_text("utf-8"))["notes"] == "athletic build, defined waist"
    assert ready.studio.set_notes(ready.npc, "")["notes"] == ""
    assert len(ready.studio.set_notes(ready.npc, "x" * 900)["notes"]) == st.MAX_NOTES


def test_body_notes_follow_the_hard_rules(ready):
    for bad in ("a petite schoolgirl figure", "looks like Emma Watson", "a childlike frame", "16 years old"):
        with pytest.raises(st.StudioError, match="Not allowed"):
            ready.studio.set_notes(ready.npc, bad)
    assert ready.studio.record(ready.npc)["notes"] == ""
    with pytest.raises(st.NotFound):
        ready.studio.set_notes("nope-000000", "athletic")


# ---------------------------------------------------------------- the body

def studio_json(env, npc=None):
    return json.loads((env.lib.root / (npc or env.npc) / "adult" / "studio.json").read_text("utf-8"))


def test_every_character_starts_with_its_own_body(ready):
    ids = [make_npc(ready.lib, first=f"Person{i}") for i in range(30)]
    starts = [ready.studio.body_of(i) for i in ids]
    assert len({b["bust"] for b in starts}) >= 3 and len({(b["shape"], b["areola_size"]) for b in starts}) >= 5
    assert starts == [ready.studio.body_of(i) for i in ids]                                # the same character always starts the same
    assert {b["areola_color"] for b in starts} == {"rose"}                                  # the test characters all have fair skin
    assert all(set(b) == set(bodies.FIELDS) for b in starts)


def test_the_first_picture_saves_the_body_and_the_words_her_reads(ready):
    ready.studio.set_notes(ready.npc, "defined waist")
    assert "body" not in studio_json(ready) and "body_text" not in studio_json(ready)        # nothing chosen yet
    ready.studio.add_items(ready.npc, ["cafe"])
    saved, chosen = studio_json(ready), ready.studio.body_of(ready.npc)
    assert saved["body"] == chosen and saved["notes"] == "defined waist"
    assert saved["body_text"] == f"{bodies.describe(chosen)}; defined waist"
    assert saved["body_text_explicit"] == bodies.areolas(chosen) and saved["body_text_explicit"].endswith("areolas")
    saved["body"]["bust"] = "very_large" if chosen["bust"] != "very_large" else "small"     # what is saved wins over the defaults
    (ready.lib.root / ready.npc / "adult" / "studio.json").write_text(json.dumps(saved), "utf-8")
    assert ready.studio.body_of(ready.npc)["bust"] == saved["body"]["bust"]


def test_choosing_body_options_reaches_the_prompts_and_marks_old_pictures(ready):
    ready.studio.add_items(ready.npc, ["cafe"])
    assert ready.studio.record(ready.npc)["items"][0]["outdated"] == []
    choice = {"bust": "large", "shape": "teardrop", "areola_size": "wide", "areola_color": "dark_brown"}
    record = ready.studio.set_body(ready.npc, choice)
    assert record["body"] == choice and record["body_saved"] is True and record["items"][0]["outdated"] == ["notes"]
    assert ready.studio.set_body(ready.npc, {"bust": "small"})["body"] == {**choice, "bust": "small"}     # options not named are kept
    ready.studio.set_body(ready.npc, {"bust": "large"})
    ready.studio.add_items(ready.npc, ["park", "robe", "figure_study"])                    # everyday, suggestive, explicit
    park, robe, study = [e["prompt"] for e in encoders(ready)[-3:]]
    for prompt in (park, robe, study):
        assert "a large, heavy bust, teardrop-shaped" in prompt
    assert "areolas" not in park and "areolas" not in robe                                 # the areolas only where they can show
    assert "with very wide dark brown areolas" in study
    assert len(ready.studio.redo_outdated(ready.npc)) == 1 and ready.studio.record(ready.npc)["items"][0]["outdated"] == []


def test_body_options_are_validated_and_need_the_18_plus_box(ready, env):
    for bad in ({}, {"nope": "x"}, {"bust": "gigantic"}, {"bust": None}, {"areola_color": "blue"}, [], "large", None):
        with pytest.raises(st.StudioError):
            ready.studio.set_body(ready.npc, bad)
    assert ready.studio.record(ready.npc)["body_saved"] is False                          # the refused ones changed nothing
    with pytest.raises(st.NotFound):
        ready.studio.set_body("nope-000000", {"bust": "small"})
    env.studio.unconfirm()
    with pytest.raises(st.Forbidden):
        env.studio.set_body(ready.npc, {"bust": "small"})


def test_every_combination_of_body_options_follows_the_hard_rules():
    ids = [[o["id"] for o in bodies.OPTIONS[f]] for f in bodies.FIELDS]
    for combo in itertools.product(*ids):
        choice = dict(zip(bodies.FIELDS, combo))
        for explicit in (False, True):
            assert safety.hard_problem(bodies.describe(choice, explicit)) is None, (choice, explicit)


def test_body_option_details():
    assert bodies.describe({}) == "" and bodies.areolas({}) == ""
    assert bodies.describe({"bust": "small", "shape": "perky"}) == "a small bust, high and firm"
    assert bodies.describe({"bust": "full", "shape": "round", "areola_size": "large", "areola_color": "brown"}, explicit=True) == \
        "a full bust, round, with large brown areolas"
    assert bodies.clean({"bust": "huge", "shape": "round", "junk": 1}, {"bust": "medium", "areola_size": "small"}) == \
        {"bust": "medium", "shape": "round", "areola_size": "small"}
    for skin, colour in (("very fair", "pale_pink"), ("fair", "rose"), ("olive", "rose_brown"), ("tan", "rose_brown"), ("brown", "brown"),
                         ("light brown", "light_brown"), ("dark brown", "dark_brown"), ("deep dark brown", "deep_brown"), ("", "rose_brown")):
        assert bodies.areola_color_for(skin) == colour, skin
    curvy = sum(bodies.defaults(f"c{i}", {"build": "curvy"})["bust"] in ("full", "large", "very_large") for i in range(200))
    slim = sum(bodies.defaults(f"c{i}", {"build": "slim"})["bust"] in ("full", "large", "very_large") for i in range(200))
    assert curvy > slim + 40                                                              # the build nudges the starting bust
    assert [c["field"] for c in bodies.choices()] == list(bodies.FIELDS)


# ---------------------------------------------------------------- making pictures

def test_scenes_make_pictures_and_write_the_files_her_reads(ready):
    made = ready.studio.add_items(ready.npc, ["cafe", "lingerie"])
    assert [i["status"] for i in made] == ["ready", "ready"] and [i["level"] for i in made] == ["off", "suggestive"]
    on_disk = items_file(ready)
    assert {"id", "scene", "label", "level", "status", "keep"} <= set(on_disk[0])           # what Altera's importer reads
    assert (ready.lib.root / ready.npc / "adult" / "img" / f"{made[0]['id']}.png").read_bytes() == PNG
    assert ready.studio.image_path(ready.npc, made[0]["id"]).read_bytes() == PNG
    assert all(i["keep"] is False for i in made)                                            # nothing is imported until you keep it


def test_the_prompt_carries_the_character_the_notes_and_the_clothing_rule(ready):
    ready.studio.set_notes(ready.npc, "athletic build")
    ready.studio.add_items(ready.npc, ["robe"])
    enc = next(v["inputs"] for v in ready.comfy.graphs[-1].values() if "prompt" in v.get("inputs", {}))
    p = enc["prompt"]
    assert p.startswith("Show the same woman from the reference image in a silk robe by a bedroom window")
    assert "an adult, 34 years old, with sturdy build, fair skin, long brown hair, hazel eyes." in p
    assert "Change the clothing completely: nothing the woman wears in the reference may appear." in p
    starting_body = bodies.describe(ready.studio.body_of(ready.npc))
    assert "photorealistic" in p and f"Body: {starting_body}; athletic build." in p and "a confident, sultry expression" in p
    assert "apron" not in p and "underage" in enc["negative_prompt"] and "schoolgirl" in enc["negative_prompt"]
    assert st.FRAMES["full"] in p and (enc["width"], enc["height"]) == (832, 1216)          # a robe is suggestive: the whole body
    assert ready.comfy.uploads[-1][1] == PNG + b"portrait"                                  # the character's own portrait is the reference


def test_each_level_gets_its_own_mood_and_a_different_seed(ready):
    ready.studio.add_items(ready.npc, ["cafe", "dinner"])
    prompts = [next(v["inputs"] for v in g.values() if "prompt" in v.get("inputs", {}))["prompt"] for g in ready.comfy.graphs]
    assert "a natural, friendly expression" in prompts[0] and "a warm, flirty, affectionate expression" in prompts[1]
    seeds = [next(v["inputs"]["seed"] for v in g.values() if "seed" in v.get("inputs", {})) for g in ready.comfy.graphs]
    assert seeds[0] != seeds[1]


def encoders(env):
    return [next(v["inputs"] for v in g.values() if "prompt" in v.get("inputs", {})) for g in env.comfy.graphs]


def test_suggestive_and_explicit_pictures_ask_for_the_whole_body(ready):
    """Measured: the portrait is a head-and-shoulders crop and the edit copies it, so 'nude' pictures ended at the collarbone."""
    ready.studio.add_items(ready.npc, ["cafe", "dinner", "robe", "figure_study"])
    cafe, dinner, robe, study = encoders(ready)
    for enc in (cafe, dinner):
        assert st.FRAMES["full"] not in enc["prompt"] and (enc["width"], enc["height"]) == (768, 1024)
    for enc in (robe, study):
        assert st.FRAMES["full"] in enc["prompt"] and (enc["width"], enc["height"]) == (832, 1216)
    ready.studio.update_settings({"quality": "draft"})
    ready.studio.add_items(ready.npc, ["cafe", "figure_study"])
    small_everyday, small_full = encoders(ready)[-2:]
    assert (small_everyday["width"], small_everyday["height"]) == (512, 704) and (small_full["width"], small_full["height"]) == (512, 768)


def test_pictures_made_before_the_full_length_frame_are_marked_and_can_be_redone(ready):
    """Older suggestive and explicit pictures were cropped at the chest: they are flagged so 'Redo outdated' remakes them."""
    ready.studio.add_items(ready.npc, ["cafe", "robe", "figure_study"])
    assert all(i["outdated"] == [] for i in ready.studio.record(ready.npc)["items"])
    path = ready.lib.root / ready.npc / "adult" / "items.json"
    data = json.loads(path.read_text("utf-8"))
    for item in data["items"]:
        item.pop("frame")                                                                   # what a picture from before looked like
    path.write_text(json.dumps(data), "utf-8")
    assert [i["outdated"] for i in ready.studio.record(ready.npc)["items"]] == [[], ["frame"], ["frame"]]   # everyday ones are fine
    redone = ready.studio.redo_outdated(ready.npc)
    assert len(redone) == 2 and all(i["outdated"] == [] for i in ready.studio.record(ready.npc)["items"])
    assert st.FRAMES["full"] in encoders(ready)[-1]["prompt"] and ready.studio.redo_outdated(ready.npc) == []


def test_explicit_scenes_wear_only_what_the_scene_says(ready):
    """Measured: 'everyday clothes for anything it does not mention' kept the reference outfit on in a nude mirror selfie."""
    ready.studio.add_items(ready.npc, ["park", "robe", "figure_study"])
    park, robe, study = [e["prompt"] for e in encoders(ready)]
    for prompt in (park, robe):
        assert "in ordinary everyday clothes for anything it does not mention" in prompt
    assert "whatever it does not say the woman wears, the woman is not wearing" in study and "everyday clothes" not in study
    assert "Change the clothing completely: nothing the woman wears in the reference may appear." in study


def test_a_scene_can_choose_its_own_frame(ready, tmp_path):
    scenes = tmp_path / "scenes.json"
    scenes.write_text(json.dumps({"scenes": [
        {"id": "close", "level": "explicit", "label": "Close", "text": "in a steamy shower, head tilted back", "frame": "auto"},
        {"id": "far", "level": "off", "label": "Far", "text": "standing on a cliff at sunrise", "frame": "full"},
        {"id": "plain", "level": "explicit", "label": "Plain", "text": "nude on a bed", "frame": "bogus"}]}), "utf-8")
    ready.studio.scenes_path = scenes
    assert [s["frame"] for s in ready.studio.scenes()] == ["auto", "full", None]
    ready.studio.add_items(ready.npc, ["close", "far", "plain"])
    close, far, plain = encoders(ready)
    assert st.FRAMES["full"] not in close["prompt"] and st.FRAMES["full"] in far["prompt"] and st.FRAMES["full"] in plain["prompt"]
    assert (close["width"], close["height"]) == (768, 1024) and (far["width"], far["height"]) == (832, 1216)
    ready.studio.redo(ready.npc, items_file(ready)[0]["id"])                                # a redone picture keeps its frame
    assert st.FRAMES["full"] not in encoders(ready)[-1]["prompt"]


def test_custom_scenes_and_what_is_refused(ready):
    made = ready.studio.add_items(ready.npc, [], [{"text": "reading a book on a rooftop at dusk", "level": "romantic"}])
    assert made[0]["scene"] == "reading a book on a rooftop at dusk" and made[0]["level"] == "romantic"
    for custom, message in (([{"text": "", "level": "off"}], "needs a description"),
                            ([{"text": "in a cafe", "level": "wild"}], "Unknown level"),
                            ([{"text": "a 16 year old at school", "level": "off"}], "never allowed"),
                            ([{"text": "looks like Emma Watson", "level": "off"}], "never allowed"),
                            ([{"text": "she is unconscious", "level": "off"}], "never allowed")):
        with pytest.raises(st.StudioError, match=message):
            ready.studio.add_items(ready.npc, [], custom)
    with pytest.raises(st.StudioError, match="Unknown scene"):
        ready.studio.add_items(ready.npc, ["nope"])
    with pytest.raises(st.StudioError, match="Pick at least one"):
        ready.studio.add_items(ready.npc, [], [])
    with pytest.raises(st.StudioError, match="at once"):
        ready.studio.add_items(ready.npc, [], [{"text": f"scene {i}", "level": "off"} for i in range(st.MAX_BATCH + 1)])
    assert len(items_file(ready)) == 1                                                       # the refusals stored nothing


def test_failures_are_reported_without_leaking_internals(ready):
    ready.comfy.error = ComfyError("Cannot reach ComfyUI at http://127.0.0.1:8188 (refused)")
    item = ready.studio.add_items(ready.npc, ["cafe"])[0]
    assert item["status"] == "failed" and "ComfyUI" in item["reason"]
    ready.comfy.error = RuntimeError("boom at C:\\secret\\path")
    item = ready.studio.add_items(ready.npc, ["cafe"])[0]
    assert item["status"] == "failed" and "secret" not in item["reason"]
    assert ready.studio.image_path(ready.npc, item["id"]) is None


# ---------------------------------------------------------------- the optional age check

def studio_with_check(env, reply=None, error=None, model="vision-model"):
    calls = []

    def post(url, payload, api_key="", timeout=240):
        calls.append((url, payload))
        if error:
            raise error
        return reply

    env.studio._post = post
    env.studio.confirm(True)
    env.studio.update_settings({"age_check_model": model})
    env.npc = make_npc(env.lib)
    return calls


def chat_reply(content=None, reasoning=None):
    return {"choices": [{"message": {"content": content, "reasoning_content": reasoning}}]}


def test_without_a_model_pictures_are_delivered_but_marked_unchecked(ready):
    item = ready.studio.add_items(ready.npc, ["cafe"])[0]
    assert item["status"] == "ready" and item["checked"] is False


def test_an_adult_passes_the_age_check(env):
    calls = studio_with_check(env, chat_reply('{"apparent_age": 31, "adult": true}'))
    item = env.studio.add_items(env.npc, ["cafe"])[0]
    assert item["status"] == "ready" and item["checked"] is True and item["apparent_age"] == 31
    url, payload = calls[0]
    assert url == "http://127.0.0.1:1234/v1/chat/completions" and payload["model"] == "vision-model"
    assert payload["messages"][0]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_a_picture_that_reads_as_young_is_discarded(env):
    studio_with_check(env, chat_reply('{"apparent_age": 17, "adult": false}'))
    item = env.studio.add_items(env.npc, ["cafe"])[0]
    assert item["status"] == "blocked" and "17" in item["reason"]
    assert env.studio.image_path(env.npc, item["id"]) is None and not list((env.lib.root / env.npc / "adult").glob("img/*.png"))


def test_an_unreachable_or_confused_vision_model_leaves_the_picture_unchecked(env):
    studio_with_check(env, error=OSError("connection refused"))
    assert env.studio.add_items(env.npc, ["cafe"])[0]["checked"] is False
    studio_with_check(env, chat_reply("I would rather not say."))
    assert env.studio.add_items(env.npc, ["cafe"])[0]["checked"] is False
    studio_with_check(env, chat_reply(content="", reasoning='{"apparent_age": 28, "adult": true}'))
    assert env.studio.add_items(env.npc, ["cafe"])[0]["checked"] is True


# ---------------------------------------------------------------- curating

def test_keep_redo_and_delete(ready):
    a = ready.studio.add_items(ready.npc, ["cafe"])[0]
    assert ready.studio.set_keep(ready.npc, a["id"], True)["keep"] is True
    assert ready.studio.set_keep(ready.npc, a["id"], False)["keep"] is False
    ready.studio.set_keep(ready.npc, a["id"], True)
    before_seed = items_file(ready)[0]["seed"]
    again = ready.studio.redo(ready.npc, a["id"])
    assert again["status"] == "ready" and again["keep"] is False                              # a redone picture must be approved again
    assert items_file(ready)[0]["seed"] != before_seed and len(ready.comfy.graphs) == 2
    ready.studio.delete_item(ready.npc, a["id"])
    assert items_file(ready) == [] and not list((ready.lib.root / ready.npc / "adult" / "img").glob("*.png"))
    with pytest.raises(st.NotFound):
        ready.studio.delete_item(ready.npc, a["id"])


def test_only_a_finished_picture_can_be_kept(ready):
    ready.comfy.error = ComfyError("down")
    failed = ready.studio.add_items(ready.npc, ["cafe"])[0]
    with pytest.raises(st.StudioError, match="finished"):
        ready.studio.set_keep(ready.npc, failed["id"], True)
    with pytest.raises(st.NotFound):
        ready.studio.set_keep(ready.npc, "s-00000000", True)


def test_changed_notes_or_a_new_portrait_mark_pictures_as_outdated(ready):
    a, b = ready.studio.add_items(ready.npc, ["cafe", "park"])
    assert all(i["outdated"] == [] for i in ready.studio.record(ready.npc)["items"])
    ready.studio.set_notes(ready.npc, "athletic build")
    assert [i["outdated"] for i in ready.studio.record(ready.npc)["items"]] == [["notes"], ["notes"]]
    (ready.lib.root / ready.npc / "portrait.png").write_bytes(PNG + b"a new portrait")
    assert ready.studio.record(ready.npc)["items"][0]["outdated"] == ["portrait", "notes"]
    redone = ready.studio.redo_outdated(ready.npc)
    assert len(redone) == 2 and all(i["outdated"] == [] for i in ready.studio.record(ready.npc)["items"])
    assert ready.studio.redo_outdated(ready.npc) == []


def test_inputs_changed_during_render_make_the_result_outdated(ready):
    original = ready.comfy.run

    def change_inputs(graph, cancel=None, timeout=900):
        ready.studio.set_notes(ready.npc, "changed while rendering")
        (ready.lib.root / ready.npc / "portrait.png").write_bytes(PNG + b"new during render")
        return original(graph, cancel, timeout)

    ready.comfy.run = change_inputs
    item = ready.studio.add_items(ready.npc, ["cafe"])[0]
    assert item["status"] == "ready"
    assert ready.studio.record(ready.npc)["items"][0]["outdated"] == ["portrait", "notes"]


def test_a_save_failure_does_not_leave_a_picture_running(ready, monkeypatch):
    original = ready.lib.write_asset

    def fail_adult_image(npc_id, rel, data):
        if rel.startswith("adult/img/"):
            raise OSError("disk full")
        return original(npc_id, rel, data)

    monkeypatch.setattr(ready.lib, "write_asset", fail_adult_image)
    item = ready.studio.add_items(ready.npc, ["cafe"])[0]
    assert item["status"] == "failed" and "saving or checking" in item["reason"]
    assert not ready.studio._image(ready.npc, item["id"]).exists()


def test_jobs_left_running_by_a_restart_are_marked_failed(ready):
    item = ready.studio.add_items(ready.npc, ["cafe"])[0]
    ready.studio._update_item(ready.npc, item["id"], status="running")
    st.Studio(ready.host, root=ready.tmp, sync=True)
    assert ready.studio.record(ready.npc)["items"][0]["status"] == "failed"


def test_a_damaged_index_is_treated_as_empty_and_bad_ids_are_ignored(ready):
    (ready.lib.root / ready.npc / "adult").mkdir(parents=True, exist_ok=True)
    (ready.lib.root / ready.npc / "adult" / "items.json").write_text("{ not json", "utf-8")
    assert ready.studio.record(ready.npc)["items"] == []
    (ready.lib.root / ready.npc / "adult" / "items.json").write_text(json.dumps({"items": [{"id": "../evil"}, {"id": "s-0123abcd"}]}), "utf-8")
    assert [i["id"] for i in ready.studio.record(ready.npc)["items"]] == ["s-0123abcd"]


def test_image_paths_only_for_finished_pictures_with_safe_ids(ready):
    item = ready.studio.add_items(ready.npc, ["cafe"])[0]
    assert ready.studio.image_path(ready.npc, item["id"]) is not None
    for bad in ("../evil", "s-zz", "", None, "s-0123abcd"):
        assert ready.studio.image_path(ready.npc, bad) is None, bad


# ---------------------------------------------------------------- the picture engines

def qwen_prompts(env):
    return [next(n["inputs"]["prompt"] for n in g.values() if n["class_type"] == "TextEncodeQwenImageEditPlus")
            for g in env.comfy.graphs if any(n["class_type"] == "TextEncodeQwenImageEditPlus" for n in g.values())]


def test_the_qwen_workflow_file_is_complete_and_says_what_it_needs():
    assert set(engines._placeholders(QWEN)) <= {"prompt", "negative", "seed", "width", "height", "steps", "reference", "reference_2"}
    needs = dict((name, folder) for folder, name in engines.required_models(QWEN))
    assert needs["Qwen-Image-Edit-2511-FP8_e4m3fn.safetensors"] == "diffusion_models" and needs["qwen_image_vae.safetensors"] == "vae"
    assert needs["qwen_2.5_vl_7b_fp8_scaled.safetensors"] == "text_encoders"
    assert needs["Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors"] == "loras" and len(needs) == 4
    graph = engines.fill_workflow(QWEN, {"prompt": "a scene", "negative": "n", "seed": 42, "width": 832, "height": 1216, "steps": 4,
                                         "reference": "r.png", "reference_2": "r.png"})
    assert "{{" not in json.dumps(graph) and "{{" in json.dumps(QWEN)                       # the template itself is untouched
    sampler = next(n for n in graph.values() if n["class_type"] == "KSampler")["inputs"]
    assert (sampler["steps"], sampler["cfg"], sampler["seed"]) == (4, 1.0, 42)              # the Lightning setting
    latent = next(n for n in graph.values() if n["class_type"] == "EmptySD3LatentImage")["inputs"]
    assert (latent["width"], latent["height"]) == (832, 1216)
    with pytest.raises(engines.EngineError, match="unknown placeholders"):
        engines.fill_workflow(QWEN, {"prompt": "only this"})


def test_which_model_files_are_missing(env):
    template = QWEN
    assert len(engines.missing_models(env.comfy, template)) == 4
    give_comfy_the_qwen_files(env)
    assert engines.missing_models(env.comfy, template) == []
    env.comfy.files["loras"] = []
    assert engines.missing_models(env.comfy, template) == ["loras/Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors"]


def test_a_guarded_picture_is_flushed_before_and_after_and_returns_the_bytes(env, clock):
    assert engines.run_guarded(env.comfy, {"1": {}}) == PNG + b"qwen"
    assert [p for _, p in env.comfy.requests] == ["/interrupt", "/free", "/free"]         # stop anything, free; then free again after


@pytest.mark.parametrize("setup, message", [
    (lambda c: c.mb.update(ram_free=20000), "Not enough free memory"),                            # would not fit: it never starts
    (lambda c: c.mb.update(vram_free=10000), "Not enough free memory"),                           # LLM/TTS has too much of the GPU
    (lambda c: setattr(c, "busy", True), "did not go idle"),
    (lambda c: (c.running_mb.update(ram_free=3000), setattr(c, "finishes", False)), "ran short of memory"),
    (lambda c: (c.running_mb.update(vram_free=100), setattr(c, "finishes", False)), "GPU memory stayed full"),
    (lambda c: setattr(c, "finishes", False), "took more than"),
    (lambda c: setattr(c, "dead_when_running", True), "stopped answering"),
    (lambda c: setattr(c, "outcome", "error"), "could not make the picture"),
])
def test_the_guard_cancels_and_frees_at_the_first_sign_of_trouble(env, clock, setup, message):
    setup(env.comfy)
    with pytest.raises(engines.EngineError, match=message):
        engines.run_guarded(env.comfy, {"1": {}})
    sent = [p for _, p in env.comfy.requests]
    assert sent[:2] == ["/interrupt", "/free"] and sent[-1] == "/free"                     # it was told to stop and to free memory
    if env.comfy.submitted:
        assert sent[-3:] == ["/interrupt", "/free", "/free"]                                # and again once the trouble showed


def test_qwen_makes_suggestive_and_explicit_pictures_when_its_files_are_there(ready, clock):
    give_comfy_the_qwen_files(ready)
    made = ready.studio.add_items(ready.npc, ["cafe", "robe", "figure_study"])
    assert [i["engine"] for i in made] == ["mage", "qwen", "qwen"] and all(i["engine_note"] is None for i in made)
    everyday, robe, study = ready.comfy.graphs
    assert any(n["class_type"] == "TextEncodeMageFlowEdit" for n in everyday.values())
    assert not any(n["class_type"] == "TextEncodeMageFlowEdit" for n in study.values())
    prompt = qwen_prompts(ready)[-1]
    assert st.FRAMES["full"] in prompt and "whatever it does not say the woman wears" in prompt and "Body:" in prompt
    latent = next(n for n in study.values() if n["class_type"] == "EmptySD3LatentImage")["inputs"]
    assert (latent["width"], latent["height"]) == (832, 1216)
    assert next(n for n in study.values() if n["class_type"] == "LoadImage")["inputs"]["image"].startswith("npc_generator/")
    assert ready.comfy.uploads[-1][1] == PNG + b"portrait"
    assert ("POST", "/free") in ready.comfy.requests                                        # the models were unloaded again
    assert ready.studio.record(ready.npc)["items"][2]["engine"] == "qwen" and items_file(ready)[2]["engine"] == "qwen"


def test_without_its_files_the_studio_uses_mage_flow_and_says_why(ready):
    made = ready.studio.add_items(ready.npc, ["figure_study"])[0]
    assert made["status"] == "ready" and made["engine"] == "mage"
    assert "instead of Qwen" in made["engine_note"] and "diffusion_models/Qwen-Image-Edit-2511-FP8_e4m3fn.safetensors" in made["engine_note"]
    assert any(n["class_type"] == "TextEncodeMageFlowEdit" for n in ready.comfy.graphs[-1].values())


def test_when_the_qwen_picture_goes_wrong_that_picture_is_made_with_mage_flow(ready, clock):
    give_comfy_the_qwen_files(ready)
    ready.comfy.running_mb.update(ram_free=3000)
    ready.comfy.finishes = False
    made = ready.studio.add_items(ready.npc, ["figure_study"])[0]
    assert made["status"] == "ready" and made["engine"] == "mage"
    assert "instead of Qwen" in made["engine_note"] and "ran short of memory" in made["engine_note"]
    assert any(n["class_type"] == "TextEncodeMageFlowEdit" for n in ready.comfy.graphs[-1].values())
    assert ("POST", "/interrupt") in ready.comfy.requests


def test_engines_can_be_chosen_per_level_and_a_bad_choice_is_refused(ready, clock):
    give_comfy_the_qwen_files(ready)
    assert engines.DEFAULT_ENGINES == {"off": "mage", "romantic": "mage", "suggestive": "qwen", "explicit": "qwen"}
    assert ready.studio.settings()["engines"] == {**engines.DEFAULT_ENGINES, "portrait": engines.DEFAULT_PORTRAIT_ENGINE}
    state = ready.studio.update_settings({"engines": {"suggestive": "mage", "explicit": "mage"}})
    assert state["engines"]["levels"] == {"off": "mage", "romantic": "mage", "suggestive": "mage", "explicit": "mage",
                                          "portrait": engines.DEFAULT_PORTRAIT_ENGINE}
    assert ready.studio.add_items(ready.npc, ["figure_study"])[0]["engine"] == "mage"
    ready.studio.update_settings({"engines": {"explicit": "qwen"}})
    assert ready.studio.add_items(ready.npc, ["nude_bed"])[0]["engine"] == "qwen"
    for bad in ({"engines": {"explicit": "midjourney"}}, {"engines": {"loud": "qwen"}}, {"engines": {}}, {"engines": "qwen"}):
        with pytest.raises(st.StudioError):
            ready.studio.update_settings(bad)
    assert ready.studio.settings()["engines"]["explicit"] == "qwen"                          # the refused ones changed nothing


QWEN_REAL = engines.load_template(engines.ENGINES["qwen_real"]["workflow"])
URP = "Qwen-Image-Edit-2511-Ultra-Realistic-Portrait-URP_20.safetensors"


def test_the_realism_workflow_is_the_qwen_one_plus_one_lora_at_half_strength():
    assert set(engines._placeholders(QWEN_REAL)) == set(engines._placeholders(QWEN))
    assert set(engines.required_models(QWEN_REAL)) - set(engines.required_models(QWEN)) == {("loras", URP)}
    assert set(engines.required_models(QWEN)) <= set(engines.required_models(QWEN_REAL))
    lora = next(n for n in QWEN_REAL.values() if (n.get("inputs") or {}).get("lora_name") == URP)["inputs"]
    assert lora["strength_model"] == 0.5 and QWEN_REAL[lora["model"][0]]["inputs"]["lora_name"].startswith("Qwen-Image-Edit-2511-Lightning")
    sampler = next(n for n in QWEN_REAL.values() if n["class_type"] == "KSampler")["inputs"]
    assert QWEN_REAL[sampler["model"][0]]["inputs"]["lora_name"] == URP                                # the sampler runs the model with both
    assert (sampler["steps"], sampler["cfg"]) == (4, 1.0)


def test_the_realism_engine_is_used_when_chosen_and_names_the_file_it_lacks(ready, clock):
    give_comfy_the_qwen_files(ready)                                                                    # the plain Qwen files only
    ready.studio.update_settings({"engines": {"explicit": "qwen_real"}})
    made = ready.studio.add_items(ready.npc, ["nude_bed"])[0]
    assert made["engine"] == "mage" and "instead of Qwen (realism)" in made["engine_note"] and URP in made["engine_note"]
    ready.comfy.files["loras"].append(URP)
    ready.comfy.graphs.clear()
    made = ready.studio.add_items(ready.npc, ["nude_bed"])[0]
    assert made["engine"] == "qwen_real" and made["engine_note"] is None
    graph = ready.comfy.graphs[-1]
    assert any((n.get("inputs") or {}).get("lora_name") == URP for n in graph.values())
    assert not any(n["class_type"] == "TextEncodeMageFlowEdit" for n in graph.values())
    assert ready.studio.record(ready.npc)["items"][-1]["engine"] == "qwen_real"


QWEN21_EDIT = engines.load_template(engines.ENGINES["qwen21"]["workflow"])


def test_the_qwen21_edit_workflow_is_complete_and_takes_the_same_values_as_qwen(env):
    """No Lightning LoRA exists for Qwen Image 2.1 yet, so it takes the same values _render_qwen always sends (steps included,
    unlike the portrait workflow below) but has no lora_name to check and its own model files."""
    assert set(engines._placeholders(QWEN21_EDIT)) <= {"prompt", "negative", "seed", "width", "height", "steps", "reference", "reference_2"}
    needs = dict((name, folder) for folder, name in engines.required_models(QWEN21_EDIT))
    assert needs["qwen_image_2.1_int8_convrot.safetensors"] == "diffusion_models"
    assert needs["qwen3vl_8b_int8_convrot.safetensors"] == "text_encoders" and needs["qwen_image_2.1_vae_bf16.safetensors"] == "vae"
    assert len(needs) == 3 and "loras" not in {f for f, _ in engines.required_models(QWEN21_EDIT)}
    graph = engines.fill_workflow(QWEN21_EDIT, {"prompt": "a scene", "negative": "n", "seed": 42, "width": 832, "height": 1216,
                                                "steps": 25, "reference": "r.png", "reference_2": "r.png"})
    assert "{{" not in json.dumps(graph) and "{{" in json.dumps(QWEN21_EDIT)
    sampler = next(n for n in graph.values() if n["class_type"] == "KSampler")["inputs"]
    assert (sampler["steps"], sampler["cfg"], sampler["seed"]) == (25, 1.0, 42)               # no Lightning: cfg 1, steps as given
    latent = next(n for n in graph.values() if n["class_type"] == "EmptyLatentImage")["inputs"]
    assert (latent["width"], latent["height"]) == (832, 1216)
    with pytest.raises(engines.EngineError, match="unknown placeholders"):
        engines.fill_workflow(QWEN21_EDIT, {"prompt": "only this"})


def test_qwen21_edit_is_a_selectable_scene_engine_alongside_qwen(ready, clock):
    give_comfy_files(ready, QWEN21_EDIT)
    ready.studio.update_settings({"engines": {"explicit": "qwen21"}})
    made = ready.studio.add_items(ready.npc, ["nude_bed"])[0]
    assert made["engine"] == "qwen21" and made["engine_note"] is None
    graph = ready.comfy.graphs[-1]
    assert any(n["class_type"] == "QwenImage21Cache" for n in graph.values())
    assert not any(n["class_type"] == "TextEncodeMageFlowEdit" for n in graph.values())
    assert ready.studio.record(ready.npc)["items"][-1]["engine"] == "qwen21"


def test_a_damaged_settings_file_cannot_pick_an_unknown_engine(ready):
    path = ready.tmp / "local" / "adult_studio.json"
    data = json.loads(path.read_text("utf-8"))
    path.write_text(json.dumps({**data, "engines": {"explicit": "evil", "off": 5, "suggestive": "mage"}}), "utf-8")
    assert ready.studio.settings()["engines"] == {**engines.DEFAULT_ENGINES, "suggestive": "mage", "portrait": engines.DEFAULT_PORTRAIT_ENGINE}


def test_the_page_is_told_which_engines_exist_and_which_files_are_missing(ready):
    state = ready.studio.state()["engines"]
    assert state["levels"] == {**engines.DEFAULT_ENGINES, "portrait": engines.DEFAULT_PORTRAIT_ENGINE} and [c["id"] for c in state["choices"]] == ["mage", "qwen", "qwen_real", "qwen21"]
    qwen = state["choices"][1]
    assert qwen["missing"] and all(m.count("/") == 1 for m in qwen["missing"]) and state["choices"][0]["missing"] == []
    give_comfy_the_qwen_files(ready)
    ready.studio._missing_cache.clear()                                                      # asked at most every 30 seconds
    assert ready.studio.state()["engines"]["choices"][1]["missing"] == []
    ready.studio._missing_cache.clear()
    ready.comfy.error = ComfyError("Cannot reach ComfyUI")
    assert ready.studio.state()["engines"]["choices"][1]["missing"] is None                  # ComfyUI could not be asked


# ---------------------------------------------------------------- the main picture of a Models character

PORTRAIT = engines.load_template(engines.PORTRAIT_ENGINES["qwen2512"]["workflow"])


def give_comfy_the_portrait_files(env):
    for folder, name in engines.required_models(PORTRAIT):
        env.comfy.files.setdefault(folder, []).append(name)


ZIMAGE = engines.load_template(engines.PORTRAIT_ENGINES["zimage"]["workflow"])


def give_comfy_files(env, template):
    for folder, name in engines.required_models(template):
        env.comfy.files.setdefault(folder, []).append(name)


def portrait_ctx(env, setting="models", **extra):
    return dict(npc={"setting": setting}, setting={}, prompt={"positive": "a fashion photograph of a woman", "negative": "n"},
                size=(1024, 1024), seed=7, comfy=env.comfy, cancel=threading.Event(), **extra)


def test_the_portrait_workflow_file_is_complete_and_says_what_it_needs():
    assert set(engines._placeholders(PORTRAIT)) == {"prompt", "seed", "width", "height"}
    needs = dict((name, folder) for folder, name in engines.required_models(PORTRAIT))
    assert needs == {"qwen_image_2512_fp8_e4m3fn.safetensors": "diffusion_models", "qwen_2.5_vl_7b_fp8_scaled.safetensors": "text_encoders",
                     "qwen_image_vae.safetensors": "vae", "Qwen-Image-Lightning-4steps-V2.0.safetensors": "loras"}
    graph = engines.fill_workflow(PORTRAIT, {"prompt": "a woman", "seed": 42, "width": 1024, "height": 1024})
    assert "{{" not in json.dumps(graph)
    sampler = next(n for n in graph.values() if n["class_type"] == "KSampler")["inputs"]
    assert (sampler["steps"], sampler["cfg"], sampler["seed"]) == (4, 1.0, 42)                # the Lightning setting
    assert next(n for n in graph.values() if n["class_type"] == "CLIPTextEncode")["inputs"]["text"] == "a woman"


def test_the_zimage_portrait_workflow_is_complete_and_says_what_it_needs():
    assert set(engines._placeholders(ZIMAGE)) == {"prompt", "seed", "width", "height"}
    assert dict((n, f) for f, n in engines.required_models(ZIMAGE)) == {
        "z_image_turbo_bf16.safetensors": "diffusion_models", "qwen_3_4b.safetensors": "text_encoders", "ae.safetensors": "vae"}
    graph = engines.fill_workflow(ZIMAGE, {"prompt": "a woman", "seed": 42, "width": 1024, "height": 1024})
    assert "{{" not in json.dumps(graph)
    sampler = next(n for n in graph.values() if n["class_type"] == "KSampler")["inputs"]
    assert (sampler["steps"], sampler["cfg"], sampler["seed"]) == (8, 1.0, 42)                # Z-Image Turbo's own setting
    assert next(n for n in graph.values() if n["class_type"] == "CLIPTextEncode")["inputs"]["text"] == "a woman"


QWEN21_PORTRAIT = engines.load_template(engines.PORTRAIT_ENGINES["qwen21"]["workflow"])


def test_the_qwen21_portrait_workflow_is_complete_and_says_what_it_needs():
    """Same caller (make_portrait_engine) and values as zimage/qwen2512, but no Lightning LoRA yet, so steps are hardcoded in
    the workflow itself (not a placeholder) the same way Z-Image and Qwen 2512 hardcode theirs."""
    assert set(engines._placeholders(QWEN21_PORTRAIT)) == {"prompt", "seed", "width", "height"}
    needs = dict((name, folder) for folder, name in engines.required_models(QWEN21_PORTRAIT))
    assert needs == {"qwen_image_2.1_int8_convrot.safetensors": "diffusion_models",
                     "qwen3vl_8b_int8_convrot.safetensors": "text_encoders", "qwen_image_2.1_vae_bf16.safetensors": "vae"}
    graph = engines.fill_workflow(QWEN21_PORTRAIT, {"prompt": "a woman", "seed": 42, "width": 1024, "height": 1024})
    assert "{{" not in json.dumps(graph)
    sampler = next(n for n in graph.values() if n["class_type"] == "KSampler")["inputs"]
    assert (sampler["steps"], sampler["cfg"], sampler["seed"]) == (25, 1.0, 42)                # no Lightning LoRA released for it yet
    assert next(n for n in graph.values() if n["class_type"] == "TextEncodeQwenImage21")["inputs"]["prompt"] == "a woman"


def test_qwen21_draws_a_models_portrait_the_careful_way_when_chosen(env, clock):
    give_comfy_files(env, QWEN21_PORTRAIT)
    made = engines.make_portrait_engine(lambda: "qwen21", PortraitFallback)(**portrait_ctx(env))
    assert made == {"png": PNG + b"qwen", "engine": "Qwen Image 2.1"}
    graph = env.comfy.graphs[-1]
    assert any(n["class_type"] == "UNETLoader" and n["inputs"]["unet_name"] == "qwen_image_2.1_int8_convrot.safetensors"
              for n in graph.values())
    assert [p for _, p in env.comfy.requests] == ["/interrupt", "/free", "/free"]              # flushed before and after, like the others


def test_qwen21_is_the_default_main_picture_engine_and_the_others_stay_choices(env, clock):
    """Z-Image was the default until the 2026-09-21 six-character comparison (same prompt+seed per character, blonde Caucasian,
    Japanese, Korean, Black, Latina, redhead): image quality was a close call either way, no defects on either engine, but
    Qwen 2.1 read consistently older on both age-check calibration models (real margin from 18; Z-Image's margin was thin) --
    the deciding factor for a Models-only feature. Z-Image and every other engine stay selectable."""
    assert list(engines.PORTRAIT_ENGINES) == ["zimage", "qwen2512", "qwen21", "mage"] and engines.DEFAULT_PORTRAIT_ENGINE == "qwen21"
    give_comfy_files(env, QWEN21_PORTRAIT)
    made = engines.make_portrait_engine(lambda: engines.DEFAULT_PORTRAIT_ENGINE, PortraitFallback)(**portrait_ctx(env))
    assert made == {"png": PNG + b"qwen", "engine": "Qwen Image 2.1"}
    graph = env.comfy.graphs[-1]
    assert any(n["class_type"] == "UNETLoader" and n["inputs"]["unet_name"] == "qwen_image_2.1_int8_convrot.safetensors"
              for n in graph.values())
    assert [p for _, p in env.comfy.requests] == ["/interrupt", "/free", "/free"]             # flushed before and after, like the others


def test_zimage_still_works_when_explicitly_chosen(env, clock):
    give_comfy_files(env, ZIMAGE)
    made = engines.make_portrait_engine(lambda: "zimage", PortraitFallback)(**portrait_ctx(env))
    assert made == {"png": PNG + b"qwen", "engine": "Z-Image Turbo"}
    graph = env.comfy.graphs[-1]
    assert any(n["class_type"] == "UNETLoader" and n["inputs"]["unet_name"] == "z_image_turbo_bf16.safetensors" for n in graph.values())
    assert [p for _, p in env.comfy.requests] == ["/interrupt", "/free", "/free"]             # flushed before and after, like the others


def test_zimage_starts_with_less_free_memory_than_qwen_needs(env, clock):
    give_comfy_files(env, ZIMAGE)
    give_comfy_files(env, PORTRAIT)
    env.comfy.mb["ram_free"] = 34000                                                          # between Z-Image's 32 GB and Qwen's 36 GB
    assert engines.make_portrait_engine(lambda: "zimage", PortraitFallback)(**portrait_ctx(env))["engine"] == "Z-Image Turbo"
    with pytest.raises(PortraitFallback, match="Not enough free memory"):
        engines.make_portrait_engine(lambda: "qwen2512", PortraitFallback)(**portrait_ctx(env))


def test_the_portrait_hook_draws_models_with_the_workflow_the_careful_way(env, clock):
    give_comfy_the_portrait_files(env)
    hook = engines.make_portrait_engine(lambda: "qwen2512", PortraitFallback)
    made = hook(**portrait_ctx(env))
    assert made == {"png": PNG + b"qwen", "engine": "Qwen-Image 2512 + Lightning"}
    graph = env.comfy.graphs[-1]
    assert next(n for n in graph.values() if n["class_type"] == "CLIPTextEncode")["inputs"]["text"] == "a fashion photograph of a woman"
    latent = next(n for n in graph.values() if n["class_type"] == "EmptySD3LatentImage")["inputs"]
    assert (latent["width"], latent["height"]) == (1024, 1024)
    assert [p for _, p in env.comfy.requests] == ["/interrupt", "/free", "/free"]             # flushed before and after, like Qwen-Edit


def test_the_portrait_hook_leaves_other_settings_and_the_built_in_choice_alone(env, clock):
    give_comfy_the_portrait_files(env)
    assert engines.make_portrait_engine(lambda: "qwen2512", PortraitFallback)(**portrait_ctx(env, setting="modern")) is None
    assert engines.make_portrait_engine(lambda: "mage", PortraitFallback)(**portrait_ctx(env)) is None
    assert engines.make_portrait_engine(lambda: "unknown", PortraitFallback)(**portrait_ctx(env)) is None
    assert env.comfy.graphs == [] and env.comfy.requests == []                                # nothing was sent to ComfyUI


def test_the_portrait_hook_falls_back_and_says_why_when_it_cannot_run(env, clock):
    hook = engines.make_portrait_engine(lambda: "qwen2512", PortraitFallback)
    with pytest.raises(PortraitFallback, match="ComfyUI is missing diffusion_models/qwen_image_2512_fp8_e4m3fn"):
        hook(**portrait_ctx(env))
    give_comfy_the_portrait_files(env)
    env.comfy.running_mb.update(ram_free=3000)
    env.comfy.finishes = False
    with pytest.raises(PortraitFallback, match="Qwen-Image 2512 \\+ Lightning could not draw it: The PC ran short of memory"):
        hook(**portrait_ctx(env))
    assert [p for _, p in env.comfy.requests][-3:] == ["/interrupt", "/free", "/free"]         # stopped and freed at once


def test_a_cancelled_portrait_stops_before_it_starts_or_at_once_while_it_runs(env, clock):
    give_comfy_the_portrait_files(env)
    hook = engines.make_portrait_engine(lambda: "qwen2512", PortraitFallback)
    ctx = portrait_ctx(env)
    ctx["cancel"].set()
    with pytest.raises(Cancelled):
        hook(**ctx)
    assert env.comfy.graphs == []                                                             # never submitted

    class LateCancel:
        calls = 0

        def is_set(self):
            self.calls += 1
            return self.calls > 3
    env.comfy.finishes = False
    with pytest.raises(Cancelled):
        engines.run_guarded(env.comfy, {"1": {}}, cancel=LateCancel())
    assert env.comfy.submitted and [p for _, p in env.comfy.requests][-3:] == ["/interrupt", "/free", "/free"]


def test_the_main_picture_engine_can_be_chosen_and_a_bad_choice_is_refused(ready):
    assert ready.studio.settings()["engines"]["portrait"] == engines.DEFAULT_PORTRAIT_ENGINE
    state = ready.studio.update_settings({"engines": {"portrait": "mage"}})
    assert state["engines"]["levels"]["portrait"] == "mage" and state["engines"]["levels"]["explicit"] == "qwen"
    assert [c["id"] for c in state["engines"]["portrait_choices"]] == ["zimage", "qwen2512", "qwen21", "mage"]
    choices = {c["id"]: c["missing"] for c in state["engines"]["portrait_choices"]}
    assert choices["zimage"] and choices["qwen2512"] and choices["qwen21"] and choices["mage"] == []
    assert ready.studio.update_settings({"engines": {"portrait": "qwen2512"}})["engines"]["levels"]["portrait"] == "qwen2512"
    for bad in ({"engines": {"portrait": "qwen"}}, {"engines": {"portrait": "midjourney"}}, {"engines": {"explicit": "qwen2512"}}):
        with pytest.raises(st.StudioError):
            ready.studio.update_settings(bad)
    assert ready.studio.settings()["engines"]["portrait"] == "qwen2512"
    path = ready.tmp / "local" / "adult_studio.json"
    data = json.loads(path.read_text("utf-8"))
    path.write_text(json.dumps({**data, "engines": {"portrait": "evil"}}), "utf-8")
    assert ready.studio.settings()["engines"]["portrait"] == engines.DEFAULT_PORTRAIT_ENGINE   # a damaged file cannot pick an unknown one


def test_register_hands_npc_forge_the_portrait_engine_and_it_follows_the_studio_setting(api, clock):
    hooks = api.route_host.portrait_engines
    assert len(hooks) == 1
    give_comfy_files(api, QWEN21_PORTRAIT)
    give_comfy_the_portrait_files(api)
    assert hooks[0](**portrait_ctx(api))["engine"] == "Qwen Image 2.1"                        # the default
    api.studio.confirm(True)                                                                  # changing a setting needs the 18+ box
    api.studio.update_settings({"engines": {"portrait": "qwen2512"}})
    assert hooks[0](**portrait_ctx(api))["engine"] == "Qwen-Image 2512 + Lightning"
    api.studio.update_settings({"engines": {"portrait": "mage"}})
    assert hooks[0](**portrait_ctx(api)) is None
    assert api.studio.settings()["engines"]["portrait"] == "mage"


# ---------------------------------------------------------------- the starter scenes

def test_the_starter_scenes_are_valid_and_pass_the_hard_rules(env):
    scenes = env.studio.scenes()
    assert len({s["id"] for s in scenes}) == len(scenes) and len({s["text"] for s in scenes}) == len(scenes)
    per_level = {lv: [s for s in scenes if s["level"] == lv] for lv in st.LEVELS}
    for level, group in per_level.items():                                                  # every level has plenty to pick from
        assert 10 <= len(group) <= st.MAX_BATCH, (level, len(group))                        # and "select all" always fits in one batch
    for s in scenes:
        assert safety.hard_problem(s["text"]) is None and safety.hard_problem(s["label"]) is None, s
        assert not any(w in re.findall(r"[a-z']+", s["text"].lower()) for w in ("her", "his", "she", "he", "him")), s
        assert len(s["text"]) <= st.MAX_SCENE and s["label"], s


def test_the_page_is_told_how_many_pictures_can_be_made_at_once(env):
    assert env.studio.state()["max_batch"] == st.MAX_BATCH == 16


# ---------------------------------------------------------------- the routes

class RouteHost:
    """Collects what register() adds, and calls handlers the way NPC Forge's server would."""
    class ApiError(Exception):
        def __init__(self, status, message):
            super().__init__(message)
            self.status, self.message = status, message

    class Raw:
        def __init__(self, body, ctype, headers=None, status=200):
            self.body, self.ctype, self.headers, self.status = body, ctype, headers or {}, status

    PortraitFallback = PortraitFallback

    def __init__(self, env):
        self.lib, self.comfy, self.root = env.lib, env.comfy, env.tmp
        self.routes, self.pages, self.settings, self.guards, self.uis, self.portrait_engines, self.clothes = [], [], [], [], [], [], []

    def route(self, method, pattern):
        def deco(fn):
            self.routes.append((method, re.compile("^" + pattern + "$"), fn))
            return fn
        return deco

    def add_page(self, ext_id, label, directory, visible=None):
        self.pages.append((ext_id, label, Path(directory), visible))

    def add_settings(self, directory, visible=None):
        self.settings.append((Path(directory), visible))

    def add_guard(self, fn):
        self.guards.append(fn)

    def add_portrait_engine(self, fn):
        self.portrait_engines.append(fn)

    def add_clothes_choice(self, choice_id, label, note, kinds=(), visible=None, check=None):
        self.clothes.append({"id": choice_id, "label": label, "note": note, "kinds": tuple(kinds), "visible": visible, "check": check})

    def add_ui(self, ext_id, directory, script="ui.js", style=None):
        self.uis.append((ext_id, Path(directory), script, style))

    def call(self, method, path, body=None):
        for m, rx, fn in self.routes:
            match = rx.match(path)
            if match and m == method:
                try:
                    return 200, fn({}, body or {}, *match.groups())
                except self.ApiError as e:
                    return e.status, {"error": e.message}
        return 404, {"error": "no route"}


@pytest.fixture
def api(env):
    host = RouteHost(env)
    register(host, studio=env.studio)                    # env.studio works inline, so a request returns the finished picture
    env.api = host.call
    env.route_host = host
    env.npc = make_npc(env.lib)
    return env


def test_the_routes_walk_the_whole_flow(api):
    P = "/api/ext/adult"
    assert api.api("GET", f"{P}/state")[1]["enabled"] is False
    assert api.api("GET", f"{P}/characters")[0] == 403 and api.api("GET", f"{P}/characters/{api.npc}")[0] == 403
    assert api.api("POST", f"{P}/confirm", {"confirm_adult": False})[0] == 422
    assert api.api("POST", f"{P}/confirm", {"confirm_adult": True})[1]["enabled"] is True

    assert [c["id"] for c in api.api("GET", f"{P}/characters")[1]["characters"]] == [api.npc]
    assert api.api("PUT", f"{P}/characters/{api.npc}/notes", {"notes": "athletic build"})[1]["notes"] == "athletic build"
    assert api.api("PUT", f"{P}/characters/{api.npc}/notes", {"notes": "a schoolgirl"})[0] == 422
    assert {c["field"] for c in api.api("GET", f"{P}/state")[1]["body_choices"]} == set(bodies.FIELDS)
    status, record = api.api("PUT", f"{P}/characters/{api.npc}/body", {"bust": "large", "areola_color": "dark_brown"})
    assert status == 200 and record["body"]["bust"] == "large" and record["body_saved"] is True
    assert api.api("PUT", f"{P}/characters/{api.npc}/body", {"bust": "gigantic"})[0] == 422
    assert api.api("PUT", f"{P}/characters/{api.npc}/body", {})[0] == 422
    status, made = api.api("POST", f"{P}/characters/{api.npc}/items", {"scene_ids": ["cafe"]})
    item = made["items"][0]
    assert status == 200 and item["status"] == "ready"
    assert api.api("PUT", f"{P}/characters/{api.npc}/items/{item['id']}", {"keep": True})[1]["keep"] is True
    image = api.api("GET", f"{P}/characters/{api.npc}/items/{item['id']}/image")[1]
    assert image.body == PNG and image.headers["Cache-Control"] == "no-store"
    assert api.api("POST", f"{P}/characters/{api.npc}/items/{item['id']}/redo")[1]["keep"] is False
    assert api.api("POST", f"{P}/characters/{api.npc}/redo-outdated")[1] == {"items": []}
    assert api.api("DELETE", f"{P}/characters/{api.npc}/items/{item['id']}")[1] == {"status": "ok"}
    assert api.api("GET", f"{P}/characters/{api.npc}/items/{item['id']}/image")[0] == 404
    assert api.api("PUT", f"{P}/settings", {"quality": "bogus"})[0] == 422
    assert api.api("PUT", f"{P}/settings", {"quality": "draft"})[1]["quality"] == "draft"


def test_route_errors_map_to_the_right_status_codes(api):
    P = "/api/ext/adult"
    api.api("POST", f"{P}/confirm", {"confirm_adult": True})
    assert api.api("GET", f"{P}/characters/nope-000000")[0] == 404
    assert api.api("POST", f"{P}/characters/{api.npc}/items", {})[0] == 422
    assert api.api("DELETE", f"{P}/characters/{api.npc}/items/s-0123abcd")[0] == 404
    assert api.api("GET", f"{P}/characters/{api.npc}/items/../image")[0] == 404
    assert api.route_host.pages[0][0] == "adult" and (api.route_host.pages[0][2] / "index.html").is_file()


def test_the_page_files_exist_and_use_no_innerhtml():
    web = EXT / "web"
    assert {p.name for p in web.iterdir()} == {"index.html", "studio.js", "studio.css"}
    assert "innerHTML" not in (web / "studio.js").read_text("utf-8")


# ---------------------------------------------------------------- switching it off again

def test_unticking_switches_the_adult_features_off_and_keeps_everything_else(env):
    env.studio.confirm(True)
    env.studio.update_settings({"quality": "draft", "age_check_model": "some-model"})
    state = env.studio.unconfirm()
    assert state["enabled"] is False and state["confirmed_at"] is None and env.studio.confirmed() is False
    assert state["quality"] == "draft" and state["age_check_model"] == "some-model"           # nothing else is lost
    with pytest.raises(st.Forbidden):
        env.studio.require_confirmed()
    assert env.studio.unconfirm()["enabled"] is False                                          # unticking twice is fine
    assert env.studio.confirm(True)["enabled"] is True


def test_the_route_that_unticks_and_what_register_hides_behind_the_box(api):
    P = "/api/ext/adult"
    host = api.route_host
    (ext_id, label, directory, visible), = host.pages
    assert (ext_id, label) == ("adult", "Adult studio") and visible is not None
    (settings_dir, settings_visible), = host.settings
    assert (settings_dir / "models.json").is_file() and settings_visible is not None
    assert host.guards == [register.__globals__["models"].problem]
    assert host.uis == [("adult-ui", EXT / "ui", "gate.js", "gate.css")] and (EXT / "ui" / "gate.js").is_file()
    assert visible() is False and settings_visible() is False                               # hidden until the box is ticked
    api.api("POST", f"{P}/confirm", {"confirm_adult": True})
    assert visible() is True and settings_visible() is True
    assert api.api("DELETE", f"{P}/confirm")[1]["enabled"] is False
    assert visible() is False and settings_visible() is False
    assert api.api("GET", f"{P}/characters")[0] == 403                                      # and the studio's own API is closed again


def test_register_adds_the_3d_bodys_nude_choice_behind_the_box_and_the_studios_rules(api):
    P = "/api/ext/adult"
    (entry,) = api.route_host.clothes
    assert (entry["id"], entry["label"], entry["kinds"]) == ("nude", "Nothing (nude)", ()) and "swinging" in entry["note"]
    assert entry["visible"]() is False                                                       # hidden until the box is ticked
    api.api("POST", f"{P}/confirm", {"confirm_adult": True})
    assert entry["visible"]() is True
    npc = api.lib.get(api.npc)
    assert entry["check"](npc) is None
    api.api("DELETE", f"{P}/confirm")
    assert entry["visible"]() is False
    young = {**npc, "profile": {**npc["profile"], "age": 17}}
    assert "18 or older" in entry["check"](young)                                             # a character that is not an adult never gets it
    unstated = {**npc, "profile": {**npc["profile"], "age": None}}
    assert "stated age" in entry["check"](unstated)
    for words in ("a schoolgirl", "looks like Taylor Swift"):
        odd = {**npc, "appearance": {**npc["appearance"], "extra": words}}
        assert "not allowed" in entry["check"](odd), words


def test_an_npc_forge_without_the_hook_still_loads_the_studio(env):
    class OldHost(RouteHost):
        @property
        def add_clothes_choice(self):                                                          # (an NPC Forge from before the hook has no such attribute)
            raise AttributeError("add_clothes_choice")

    host = OldHost(env)
    assert not hasattr(host, "add_clothes_choice")
    register(host, studio=env.studio)
    assert host.pages and not host.clothes


# ---------------------------------------------------------------- Altera, if it is here

@pytest.mark.skipif(not (HER / "core" / "safety.py").exists(), reason="set NPCGEN_ALTERA to an Altera folder to compare the rules")
def test_every_rule_here_is_still_one_of_alteras():
    """Altera's core/safety.py has more (its kids mode); every pattern and age floor here must still be in it, unchanged."""
    ours, theirs = (EXT / "safety.py").read_text("utf-8"), (HER / "core" / "safety.py").read_text("utf-8")
    patterns = re.findall(r'r?"[^"\n]*"', ours)
    missing = [p for p in patterns if p not in theirs]
    assert not missing, f"Altera's rules changed ({missing[:3]}): bring extensions/adult_studio/safety.py in line with its core/safety.py"
    for floor in ("MIN_AVATAR_AGE = 18", "MIN_APPARENT_AGE = 18"):
        assert floor in ours and floor in theirs


@pytest.mark.skipif(not HER_PYTHON.exists(), reason="Altera's environment is not at the expected path")
def test_her_can_read_what_the_studio_writes_and_accepts_the_starter_scenes(ready, tmp_path):
    ready.studio.set_notes(ready.npc, "athletic build")
    ids = [s["id"] for s in ready.studio.scenes()]
    made = [item for i in range(0, len(ids), st.MAX_BATCH) for item in ready.studio.add_items(ready.npc, ids[i:i + st.MAX_BATCH])]
    for item in made:
        ready.studio.set_keep(ready.npc, item["id"], True)
    script = tmp_path / "her_check.py"
    script.write_text(
        "import json, sys\n"
        f"sys.path.insert(0, r'{HER}')\n"
        "from core import adult, safety\n"
        "from core.avatars import AvatarStore\n"
        f"store = AvatarStore(r'{tmp_path / 'avatars'}', r'{ready.lib.root}')\n"
        f"items = store.forge_premade('{ready.npc}')\n"
        "problems = []\n"
        "for it in items:\n"
        "    if safety.hard_problem(it['scene']): problems.append(['hard', it['label']])\n"
        "    tier = adult.tier_problem(it['scene'], it['level'])\n"
        "    if tier: problems.append(['tier', it['label'], tier['required'], it['level']])\n"
        f"print(json.dumps({{'count': len(items), 'problems': problems, 'body': store.forge_studio_body('{ready.npc}')}}))\n",
        "utf-8")
    out = subprocess.run([str(HER_PYTHON), str(script)], capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-1500:]
    result = json.loads(out.stdout.strip().splitlines()[-1])
    saved = studio_json(ready)
    assert result["count"] == len(made) and result["body"] == {"text": saved["body_text"], "explicit": saved["body_text_explicit"]}
    assert result["body"]["text"].endswith("; athletic build") and result["body"]["explicit"].endswith("areolas")
    assert result["problems"] == [], "Altera would refuse to import these starter scenes at the level they are filed under"
