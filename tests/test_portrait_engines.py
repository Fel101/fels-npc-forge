"""An extension can draw portraits with its own engine (jobs.add_portrait_engine); the built-in engine is the fallback."""
import copy
import io
import threading
import types

import pytest
from PIL import Image

from npcgen import config, jobs as jobsmod
from npcgen.comfy import DEFAULT_MODELS
from npcgen.jobs import DEFAULT_OPTIONS, Jobs, PortraitFallback
from npcgen.library import Library


def png(colour):
    out = io.BytesIO()
    Image.new("RGB", (64, 64), colour).save(out, "PNG")
    return out.getvalue()


BUILT_IN, EXTENSION = png((200, 30, 30)), png((30, 30, 200))


class FakeComfy:
    models = DEFAULT_MODELS

    def __init__(self):
        self.graphs = []

    def run(self, graph, cancel=None):
        self.graphs.append(graph)
        return BUILT_IN


@pytest.fixture()
def world(tmp_path):
    jobsmod.PORTRAIT_ENGINES.clear()
    lib = Library(tmp_path / "library")
    npc = lib.create({"setting": "modern", "style": "photoreal", "seed": 11, "freeform": False,
                      "profile": {"first_name": "Mara", "last_name": "Test", "age": 30, "gender": "female", "occupation": "shopkeeper"},
                      "appearance": {"outfit": "a cream blouse"}})
    comfy = FakeComfy()
    jobs = Jobs(copy.deepcopy(config.DEFAULTS), comfy, lib)

    def draw():
        # (the built-in engine, Mage-Flow, is what an extension's portrait falls back to; it stopped being the default engine on 2026-10-13, so it is named here)
        job = types.SimpleNamespace(npc_id=npc["id"], cancel=threading.Event(), cache={}, ref=None,
                                    options={**DEFAULT_OPTIONS, "image_model": "mage", "edit_model": "mage"})
        step = {"kind": "portrait", "label": "Portrait"}
        jobs._run_step(job, step)
        return step, lib.get(npc["id"])["assets"]["portrait"], lib.resolve(npc["id"], "portrait.png").read_bytes()
    yield comfy, draw
    jobsmod.PORTRAIT_ENGINES.clear()


def test_without_an_engine_the_built_in_one_draws_the_portrait(world):
    comfy, draw = world
    step, meta, data = draw()
    assert len(comfy.graphs) == 1 and data == BUILT_IN and "engine" not in meta and "note" not in step


def test_an_engine_that_takes_the_portrait_replaces_the_built_in_one(world):
    comfy, draw = world
    seen = {}

    def engine(**ctx):
        seen.update(ctx)
        return {"png": EXTENSION, "engine": "Test engine"}
    jobsmod.add_portrait_engine(engine)
    step, meta, data = draw()
    assert comfy.graphs == [] and data == EXTENSION and meta["engine"] == "Test engine"
    assert set(seen) == {"npc", "setting", "prompt", "size", "seed", "comfy", "cancel"}
    assert seen["npc"]["profile"]["first_name"] == "Mara" and seen["seed"] == 11
    assert seen["size"] == tuple(config.DEFAULTS["quality"]["standard"]["portrait"])
    assert "Head and shoulders portrait" in seen["prompt"]["positive"] and seen["comfy"] is comfy


def test_an_engine_that_is_not_for_this_character_leaves_it_to_the_next_one_or_the_built_in_one(world):
    comfy, draw = world
    jobsmod.add_portrait_engine(lambda **ctx: None)
    jobsmod.add_portrait_engine(lambda **ctx: {"png": EXTENSION})
    step, meta, data = draw()
    assert data == EXTENSION and meta["engine"] == "extension"
    jobsmod.PORTRAIT_ENGINES.clear()
    jobsmod.add_portrait_engine(lambda **ctx: None)
    assert draw()[2] == BUILT_IN


def test_an_engine_that_cannot_run_falls_back_and_the_reason_is_kept_and_shown(world, capsys):
    comfy, draw = world

    def engine(**ctx):
        raise PortraitFallback("it needs files ComfyUI does not have")
    jobsmod.add_portrait_engine(engine)
    step, meta, data = draw()
    assert data == BUILT_IN and len(comfy.graphs) == 1
    assert meta["engine_note"] == "it needs files ComfyUI does not have" and "engine" not in meta
    assert step["note"] == "Drawn with the built-in engine instead: it needs files ComfyUI does not have"
    assert "using the built-in one" in capsys.readouterr().err


def test_a_real_failure_in_an_engine_is_not_hidden(world):
    comfy, draw = world

    def engine(**ctx):
        raise RuntimeError("boom")
    jobsmod.add_portrait_engine(engine)
    with pytest.raises(RuntimeError, match="boom"):
        draw()
    assert comfy.graphs == []
