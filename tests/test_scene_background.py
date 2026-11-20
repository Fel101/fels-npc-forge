"""The Scenes tab's 3D stage background: which picture engine draws it, what happens when that engine cannot, and the black bars cut off.
No ComfyUI calls; the engine is replaced (or, in one test, only the heavy part of it)."""
import copy
import io
import threading
import types

import pytest
from PIL import Image

from npcgen import config, engines, scene_background
from npcgen.comfy import DEFAULT_MODELS
from npcgen.jobs import ENGINE_NOTE, Jobs
from npcgen.library import Library
from npcgen.scenes import Scenes

QWEN = "Qwen-Image 2512 + Lightning"


def _png(size=(1280, 720), color="#445566", bars=0):
    image = Image.new("RGB", size, color)
    if bars:
        image.paste("black", (0, 0, size[0], bars))
        image.paste("black", (0, size[1] - bars, size[0], size[1]))
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


@pytest.fixture
def stage(tmp_path):
    lib = Library(tmp_path / "library")
    ids = []
    for first in ("Ada", "Bo"):
        npc = lib.create({"profile": {"first_name": first, "last_name": "Test", "age": 30},
                          "appearance": {}, "setting": "modern", "style": "pixel", "assets": {}})
        lib.write_asset(npc["id"], "portrait.png", _png((120, 120)))
        lib.update(npc["id"], lambda rec: rec["assets"].update(portrait={"file": "portrait.png", "created": "now"}))
        ids.append(npc["id"])
    scenes = Scenes(lib)
    scene = scenes.create({"title": "At the gate", "cast": ids, "aspect": "landscape", "mode": "stage3d",
                           "background_prompt": "An empty forest gate",
                           "beats": [{"id": "beat-one", "kind": "line", "speaker_id": ids[0], "text": "Hello there.",
                                      "layout": "split", "line_id": "greeting-one"}]})
    return scenes, scene


def _worker(scenes, **options):
    cfg = copy.deepcopy(config.DEFAULTS)
    cfg["options"] = options
    return types.SimpleNamespace(cfg=cfg, scenes=scenes,
                                 comfy=types.SimpleNamespace(base="http://scene-background-test", models=DEFAULT_MODELS, run=lambda graph, cancel=None: _png()))


def _job(scene, **options):
    return types.SimpleNamespace(scene_id=scene["id"], cancel=threading.Event(), options={"scene_background": {"prompt": scene["background_prompt"]}, **options})


class Drawn:
    """engines.image replaced: remembers every call and answers like the real one (Mage-Flow's meta is empty, another engine's names itself)."""

    def __init__(self, monkeypatch):
        self.calls, self.png, self.note = [], _png(), None
        monkeypatch.setattr(engines, "image", self)

    def __call__(self, comfy, engine_id, positive, negative, width, height, seed, steps, cfg, cancel=None):
        self.calls.append({"engine": engine_id, "positive": positive, "negative": negative, "size": (width, height),
                           "strict": getattr(engines._STRICT, "on", False)})
        meta = {} if engine_id == "mage" else {"engine": engines.IMAGE_ENGINES[engine_id]["label"]}
        return self.png, ({**meta, "engine_note": self.note} if self.note else meta)


@pytest.fixture
def drawn(monkeypatch):
    return Drawn(monkeypatch)


def test_the_background_is_drawn_by_the_apps_default_engine_and_never_falls_back_quietly(stage, drawn):
    scenes, scene = stage
    step = {}
    scene_background.run(_worker(scenes), _job(scene), step)
    call = drawn.calls[0]
    assert call["engine"] == "qwen2512" and call["strict"] is True and call["size"] == (1280, 720)
    assert "An empty forest gate" in call["positive"] and "deserted" in call["positive"] and "cinematic" not in call["positive"]
    saved = scenes.get(scene["id"])["background"]
    assert saved["engine"] == QWEN and saved["prompt"] == "An empty forest gate"
    with Image.open(scenes.resolve(scene["id"], saved["file"])) as picture:
        assert picture.size == (1280, 720)
    assert step == {}


def test_a_scene_that_names_mage_flow_gets_it_with_the_negative_prompt_that_keeps_people_out(stage, drawn):
    scenes, scene = stage
    scene_background.run(_worker(scenes), _job(scene, image_model="mage", engine_fallback=True), {})
    call = drawn.calls[0]
    assert call["engine"] == "mage" and "people" in call["negative"] and call["strict"] is False
    assert "no people" in call["positive"] and "cinematic" in call["positive"]      # the wording Mage-Flow was tuned with
    assert scenes.get(scene["id"])["background"]["engine"] == "Mage-Flow"


def test_an_engine_that_ignores_the_negative_prompt_gets_the_emptiness_in_the_positive_wording():
    mage, negative = scene_background.wording("A forest gate", "mage")
    other, other_negative = scene_background.wording("A forest gate", "qwen2512")
    assert "A forest gate" in mage and "A forest gate" in other and other_negative == negative
    assert "no people" in mage and "cinematic" in mage
    assert "nobody in it" in other and "unattended" in other and "edge to edge" in other
    assert "cinematic" not in other             # what draws the black letterbox bars


def test_config_json_moves_the_default_engine_of_the_backgrounds_too(stage, drawn):
    scenes, scene = stage
    scene_background.run(_worker(scenes, image_model="zimage"), _job(scene), {})
    assert drawn.calls[0]["engine"] == "zimage"
    scene_background.run(_worker(scenes, image_model="mage", engine_fallback=True), _job(scene), {})
    assert drawn.calls[1]["engine"] == "mage" and drawn.calls[1]["strict"] is False


def test_the_fallback_switch_of_the_job_decides_whether_mage_flow_may_draw_the_plate(stage, drawn):
    scenes, scene = stage
    scene_background.run(_worker(scenes), _job(scene, engine_fallback=True), {})
    scene_background.run(_worker(scenes), _job(scene, engine_fallback=False), {})
    assert [c["strict"] for c in drawn.calls] == [False, True]


def test_a_plate_qwen_cannot_draw_fails_with_the_reason_unless_the_fallback_is_on(stage, monkeypatch):
    scenes, scene = stage
    monkeypatch.setattr(engines, "missing_files", lambda comfy, spec, fresh=False: [])

    def no_memory(comfy, spec, graph, cancel):
        raise engines.EngineError("not enough free memory", transient=True)

    monkeypatch.setattr(engines, "_run_big", no_memory)
    worker = _worker(scenes)
    with pytest.raises(engines.EngineError, match="could not draw it: not enough free memory") as refused:
        scene_background.run(worker, _job(scene), {})
    assert refused.value.transient and not scenes.get(scene["id"]).get("background")
    step = {}
    scene_background.run(worker, _job(scene, engine_fallback=True), step)
    assert scenes.get(scene["id"])["background"]["engine"] == "Mage-Flow"
    assert step["note"].startswith(ENGINE_NOTE) and "not enough free memory" in step["note"]


def test_black_letterbox_bars_are_cut_off_and_said(stage, drawn):
    scenes, scene = stage
    drawn.png = _png(bars=65)
    step = {}
    scene_background.run(_worker(scenes), _job(scene), step)
    saved = scenes.get(scene["id"])["background"]
    with Image.open(scenes.resolve(scene["id"], saved["file"])) as picture:
        rows = picture.convert("L")
        assert picture.size == (1280, 720)
        assert rows.crop((0, 0, 1280, 1)).getextrema()[1] > 60 and rows.crop((0, 719, 1280, 720)).getextrema()[1] > 60
    assert step["note"] == scene_background.BARS_CUT


def test_what_is_and_is_not_taken_for_bars():
    def bars(**kw):
        return scene_background.bars(Image.open(io.BytesIO(_png(**kw))))

    assert bars() == (0, 0)
    assert bars(bars=65) == (65, 65)
    assert bars(bars=3) == (0, 0)                                  # a hair of black at an edge is not worth cutting
    dark_ceiling = Image.new("RGB", (1280, 720), "#445566")
    dark_ceiling.paste("black", (0, 0, 1280, 120))
    assert scene_background.bars(dark_ceiling) == (0, 0)           # black at one edge only: a dark ceiling, not bars
    assert scene_background.bars(Image.new("RGB", (1280, 720), "black")) == (0, 0)      # a black picture is not bars either
    night = Image.new("RGB", (1280, 720), "#101010")
    night.paste("#445566", (0, 100, 1280, 620))
    assert scene_background.bars(night) == (100, 100)


class Recorder(Jobs):
    """A Jobs that only records what it is asked to queue."""

    def __init__(self, scenes):
        self.cfg = copy.deepcopy(config.DEFAULTS)
        self.scenes = scenes
        self.queue = types.SimpleNamespace(put=lambda job, priority: None)
        self.jobs, self.lock, self.closed = {}, threading.RLock(), False

    def _persist(self):
        pass


def test_the_job_carries_the_engine_choice_it_was_given_and_nothing_else_of_the_options(stage):
    scenes, scene = stage
    jobs = Recorder(scenes)
    plain = jobs.submit_scene_background(scene["id"])
    chosen = jobs.submit_scene_background(scene["id"], {"image_model": "mage", "engine_fallback": True, "quality": "draft", "seed": 7})
    plain, chosen = (jobs.jobs[j["id"]] for j in (plain, chosen))
    assert plain.options == {"scene_background": {"prompt": "An empty forest gate"}}
    assert chosen.options == {"scene_background": {"prompt": "An empty forest gate"}, "image_model": "mage", "engine_fallback": True}
