"""The picture engines (npcgen/engines.py): which model draws or edits a picture, and the careful way the big ones run.

Nothing here needs a GPU. A stand-in for ComfyUI answers the calls the guarded runner makes (/system_stats, /queue, /history,
/models, /free, /interrupt) and records them, so the memory rules can be checked step by step: when models are unloaded, when a
picture is refused, and when it falls back to Mage-Flow."""
import copy
import io
import threading
import types

import pytest
from PIL import Image

from npcgen import config, engines, gpu, service
from npcgen import jobs as jobsmod
from npcgen.comfy import DEFAULT_MODELS, Cancelled, edit_graph, t2i_graph
from npcgen.jobs import DEFAULT_OPTIONS, Jobs
from npcgen.library import Library

GB = 1024 * 2 ** 20                                   # ComfyUI reports bytes


def png(size=(64, 64), colour=(90, 120, 150)):
    out = io.BytesIO()
    Image.new("RGB", size, colour).save(out, "PNG")
    return out.getvalue()


class FakeComfy:
    """ComfyUI with a 32 GB card and plenty of RAM unless told otherwise. `ram_during` is the free RAM (GB) it reports while a
    picture runs; `files` is what its model folders hold (everything by default)."""
    models = DEFAULT_MODELS

    def __init__(self, ram_free=50, vram_free=31, ram_during=None, files=None, finish=True):
        self.base = f"fake://{id(self)}"
        self.ram_free, self.vram_free, self.ram_during, self.files, self.finish = ram_free, vram_free, ram_during, files, finish
        self.events, self.graphs, self.running = [], [], None

    # what the guarded runner uses
    def get_json(self, path, timeout=30):
        if path == "/system_stats":
            ram = self.ram_during if (self.running and self.ram_during is not None) else self.ram_free
            vram_free = 6 if self.running else self.vram_free
            return {"system": {"ram_free": ram * GB}, "devices": [{"vram_total": 32 * GB, "vram_free": vram_free * GB}]}
        if path == "/queue":
            return {"queue_running": [], "queue_pending": []}
        if path.startswith("/history/"):
            pid = path.rsplit("/", 1)[1]
            if not self.finish:
                return {}
            self.running = None
            return {pid: {"status": {"completed": True, "status_str": "success"},
                          "outputs": {"9": {"images": [{"filename": "out.png", "subfolder": "", "type": "temp"}]}}}}
        if path.startswith("/models/"):
            folder = path.split("/")[2]
            if self.files is None:
                return [name for spec in (*engines.IMAGE_ENGINES.values(), *engines.EDIT_ENGINES.values()) if "build" in spec
                        for f, name in engines.engine_files(spec) if f == folder]
            return self.files.get(folder, [])
        raise AssertionError(path)

    def submit(self, graph):
        self.events.append("submit")
        self.graphs.append(graph)
        self.running = f"p{len(self.graphs)}"
        return self.running

    def fetch_image(self, filename, subfolder="", image_type="temp"):
        return png()

    def free_memory(self):
        self.events.append("free")

    def interrupt(self):
        self.events.append("interrupt")

    # the Mage-Flow path
    def run(self, graph, cancel=None):
        self.events.append("mage")
        self.graphs.append(graph)
        return png()


@pytest.fixture(autouse=True)
def fresh_engines(monkeypatch):
    """A fake clock that sleeping moves forward, and no memory of what earlier tests loaded."""
    now = [0.0]
    monkeypatch.setattr(engines, "_clock", lambda: now[0])
    monkeypatch.setattr(engines, "_sleep", lambda s: now.__setitem__(0, now[0] + s))
    engines._LOADED.clear()
    engines._FILES.clear()
    yield
    engines._LOADED.clear()
    engines._FILES.clear()


def draw(comfy, engine="zimage", size=(1024, 1024), cancel=None):
    return engines.image(comfy, engine, "a pirate captain", "blurry", size[0], size[1], 7, 30, 5.0, cancel)


def edit(comfy, engine="qwen2511", refs=("a.png",), size=(768, 1152), cancel=None, **kw):
    return engines.edit(comfy, engine, list(refs), "make her wave", 7, 30, 3.5, size=size, negative="blurry", cancel=cancel, **kw)


# ---- Mage-Flow: exactly what the app did before engines existed -------------------------------------------------------------------
def test_mage_flow_builds_the_very_same_graphs_as_before():
    comfy = FakeComfy()
    png_bytes, meta = draw(comfy, "mage", (768, 768))
    assert meta == {} and comfy.events == ["mage"]
    assert comfy.graphs[-1] == t2i_graph(DEFAULT_MODELS, "a pirate captain", "blurry", 768, 768, 7, 30, 5.0)
    engines.edit(comfy, "mage", "p.png", "smile", 3, 30, 3.5, megapixels=0.6, negative="n")
    assert comfy.graphs[-1] == edit_graph(DEFAULT_MODELS, "p.png", "smile", 3, 30, 3.5, megapixels=0.6, negative="n")
    engines.edit(comfy, "mage", ["a.png", "b.png"], "walk", 3, 25, 3.5, size=(576, 864), negative="n")
    assert comfy.graphs[-1] == edit_graph(DEFAULT_MODELS, ["a.png", "b.png"], "walk", 3, 25, 3.5, size=(576, 864), negative="n")


@pytest.mark.parametrize("engine", [None, "", "no-such-model"])
def test_an_unknown_or_missing_engine_is_mage_flow(engine):
    comfy = FakeComfy()
    assert draw(comfy, engine)[1] == {} and comfy.events == ["mage"]


def test_options_take_only_known_engines_of_the_right_kind_and_refuse_the_rest():
    """A misspelt engine used to become Mage-Flow without a word: in a commercial project that is a picture it may not use."""
    clean = lambda raw: service.clean_options(raw, config.DEFAULTS)
    assert clean({})["image_model"] == "qwen2512" and clean({})["edit_model"] == "qwen2511"        # (since 2026-10-13; it was Mage-Flow)
    assert clean({"image_model": None, "edit_model": ""})["image_model"] == "qwen2512"             # naming none is naming the default
    assert clean({"image_model": "zimage", "edit_model": "qwen2511"}) == {**clean({}), "image_model": "zimage", "edit_model": "qwen2511"}
    for raw, said in (({"image_model": "qwen2511"}, "an edit engine, not an image engine"),
                      ({"edit_model": "zimage"}, "an image engine, not an edit engine"),
                      ({"image_model": "qwen25122"}, "unknown image engine 'qwen25122'"),
                      ({"edit_model": "qwen2511 "}, "unknown edit engine 'qwen2511 '"),
                      ({"image_model": ["zimage"]}, "unknown image engine")):
        with pytest.raises(ValueError, match=said):
            clean(raw)
    with pytest.raises(ValueError, match="image_model takes one of: mage, zimage, qwen21, qwen2512"):
        clean({"image_model": "nope"})


def test_config_json_sets_the_engines_a_request_without_them_gets():
    """A PC that has not the Qwen files, or wants the lighter Mage-Flow back, names its engines once, in config.json's "options"."""
    cfg = {**config.DEFAULTS, "options": {"image_model": "mage", "edit_model": "mage", "engine_fallback": True, "bogus": 1}}
    assert service.default_options(cfg)["image_model"] == "mage" and "bogus" not in service.default_options(cfg)
    got = service.clean_options({}, cfg)
    assert (got["image_model"], got["edit_model"], got["engine_fallback"]) == ("mage", "mage", True)
    chosen = service.clean_options({"image_model": "qwen2512", "engine_fallback": False}, cfg)       # a request still chooses
    assert chosen["image_model"] == "qwen2512" and chosen["edit_model"] == "mage" and chosen["engine_fallback"] is False
    built_in = service.clean_options({}, config.DEFAULTS)
    assert (built_in["image_model"], built_in["edit_model"], built_in["engine_fallback"]) == ("qwen2512", "qwen2511", False)   # Apache-2.0 pair, no quiet Mage-Flow
    with pytest.raises(ValueError, match="engine_fallback must be true or false"):
        service.clean_options({"engine_fallback": "no"}, config.DEFAULTS)
    with pytest.raises(ValueError, match="unknown image engine"):                                    # a wrong config.json says so too
        service.clean_options({}, {**config.DEFAULTS, "options": {"image_model": "typo"}})


def test_the_default_engines_are_the_apache_licensed_qwen_pair_and_the_fallback_is_off():
    """2026-10-13, the owner's decision: Qwen-Image 2512 draws and Qwen-Image-Edit 2511 edits unless told otherwise, and a picture they cannot draw fails instead of
    Mage-Flow (whose licence is unclear) drawing it. The licence list agrees: both defaults are rated commercial-use."""
    from npcgen import jobs, licences
    assert (jobs.DEFAULT_OPTIONS["image_model"], jobs.DEFAULT_OPTIONS["edit_model"], jobs.DEFAULT_OPTIONS["engine_fallback"]) == ("qwen2512", "qwen2511", False)
    assert jobs.DEFAULT_OPTIONS["image_model"] in engines.IMAGE_ENGINES and jobs.DEFAULT_OPTIONS["edit_model"] in engines.EDIT_ENGINES
    for kind, key in (("image", "image_model"), ("edit", "edit_model")):
        assert licences.selector_status(f"{kind}:{jobs.DEFAULT_OPTIONS[key]}") == "yes", (kind, jobs.DEFAULT_OPTIONS[key])


def test_the_command_line_names_an_engine_only_when_asked_and_the_fallback_is_a_switch_either_way():
    """Not naming one leaves it to config.json's "options" and then the built-in defaults (a command line that always said Mage-Flow could not be moved off it)."""
    from npcgen import cli, roll
    parse = lambda *more: cli.build_parser(roll.load_data()).parse_args(["generate", *more])
    plain = parse("--portrait")
    assert (plain.image_model, plain.edit_model, plain.engine_fallback) == (None, None, None)
    named = parse("--portrait", "--image-model", "mage", "--edit-model", "mage", "--engine-fallback")
    assert (named.image_model, named.edit_model, named.engine_fallback) == ("mage", "mage", True)
    assert parse("--portrait", "--no-engine-fallback").engine_fallback is False
    for given, want in ((plain, ("qwen2512", "qwen2511", False)), (named, ("mage", "mage", True)), (parse("--no-engine-fallback"), ("qwen2512", "qwen2511", False))):
        got = service.clean_options({"image_model": given.image_model, "edit_model": given.edit_model,
                                     **({"engine_fallback": given.engine_fallback} if given.engine_fallback is not None else {})}, config.DEFAULTS)
        assert (got["image_model"], got["edit_model"], got["engine_fallback"]) == want


def test_strict_mode_fails_a_picture_instead_of_letting_mage_flow_draw_it():
    with engines.strict():
        comfy = FakeComfy(ram_free=10)                                       # far too little RAM for a Qwen picture
        with pytest.raises(engines.EngineError, match=r"Qwen-Image 2512 \+ Lightning could not draw it: not enough free memory") as refused:
            draw(comfy, "qwen2512")
        assert refused.value.transient and "mage" not in comfy.events        # nothing was drawn by Mage-Flow
        comfy = FakeComfy(ram_free=10)
        with pytest.raises(engines.EngineError, match="Qwen-Image-Edit 2511"):
            edit(comfy)
        assert "mage" not in comfy.events
        with pytest.raises(engines.EngineError, match="ComfyUI is missing") as missing:
            draw(FakeComfy(files={}), "zimage")
        assert not missing.value.transient                                    # files that are not there will not come back
        assert draw(FakeComfy(), "mage")[1] == {}                             # choosing Mage-Flow is not a fallback
    comfy = FakeComfy(ram_free=10)                                            # off again: the same call falls back, as it always did
    _, meta = draw(comfy, "qwen2512")
    assert "not enough free memory" in meta["engine_note"] and comfy.events[-1] == "mage"


def test_strict_mode_belongs_to_the_thread_that_asked_for_it():
    seen = []
    with engines.strict():
        worker = threading.Thread(target=lambda: seen.append(getattr(engines._STRICT, "on", False)))
        worker.start()
        worker.join()
        assert engines._STRICT.on is True
    assert seen == [False] and engines._STRICT.on is False


def test_a_gpu_that_was_busy_at_submit_is_a_transient_failure():
    class Busy(FakeComfy):
        def submit(self, graph):
            raise gpu.GpuBusy("ComfyUI still holds 4.6 GB of GPU memory")
    with engines.strict():
        with pytest.raises(engines.EngineError, match="still holds 4.6 GB") as busy:
            draw(Busy(), "qwen2512")
    assert busy.value.transient


def test_waits_inside_a_big_picture_tell_the_job_they_are_alive():
    class Flag(threading.Event):
        beats = 0

        def beat(self, note=None):
            Flag.beats += 1
    draw(FakeComfy(), "zimage", cancel=Flag())
    assert Flag.beats >= 1


# ---- the graphs -----------------------------------------------------------------------------------------------------------------
def _links_resolve(graph):
    for node in graph.values():
        for value in node["inputs"].values():
            if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str):
                assert value[0] in graph, (node["class_type"], value)


@pytest.mark.parametrize("engine_id", [k for k, v in engines.IMAGE_ENGINES.items() if "build" in v])
def test_each_image_engine_builds_a_complete_graph(engine_id):
    spec = engines.IMAGE_ENGINES[engine_id]
    graph = spec["build"]("a knight", 832, 1216, 9, spec["steps"])
    _links_resolve(graph)
    sampler = next(n for n in graph.values() if n["class_type"] == "KSampler")["inputs"]
    assert sampler["cfg"] == 1.0 and sampler["steps"] == spec["steps"] and sampler["seed"] == 9
    latent = next(n for n in graph.values() if "Latent" in n["class_type"])["inputs"]
    assert (latent["width"], latent["height"]) == (832, 1216)
    assert engines.engine_files(spec) and all(folder in ("diffusion_models", "text_encoders", "vae", "loras")
                                              for folder, _ in engines.engine_files(spec))


@pytest.mark.parametrize("engine_id", [k for k, v in engines.EDIT_ENGINES.items() if "build" in v])
@pytest.mark.parametrize("count", [1, 2, 3])
def test_each_edit_engine_wires_one_to_three_references(engine_id, count):
    """Animation frames send the drawn pose, the front sprite and the side view together: every reference must reach the
    text encoder (both of them, for Qwen-Image-Edit 2511's positive and negative)."""
    spec = engines.EDIT_ENGINES[engine_id]
    refs = [f"ref{j}.png" for j in range(count)]
    graph = spec["build"](refs, "walk", "blurry", 4, spec["steps"], size=(576, 864))
    _links_resolve(graph)
    loads = {n["inputs"]["image"] for n in graph.values() if n["class_type"] == "LoadImage"}
    assert loads == set(refs)
    encoders = [n["inputs"] for n in graph.values() if n["class_type"].startswith("TextEncodeQwen")]
    for enc in encoders:
        wired = [k for k in enc if k.startswith("image") or k.startswith("images.")]
        assert len(wired) == count, (engine_id, enc)
    latent = next(n for n in graph.values() if "Latent" in n["class_type"] and "Method" not in n["class_type"])["inputs"]
    assert (latent["width"], latent["height"]) == (576, 864)


@pytest.mark.parametrize("engine_id", [k for k, v in engines.EDIT_ENGINES.items() if "build" in v])
def test_an_edit_with_no_size_takes_the_first_references_shape(engine_id):
    spec = engines.EDIT_ENGINES[engine_id]
    graph = spec["build"](["p.png"], "smile", "", 4, spec["steps"], size=None, megapixels=0.6)
    _links_resolve(graph)
    latent = next(n for n in graph.values() if "Latent" in n["class_type"] and "Method" not in n["class_type"])["inputs"]
    assert latent["width"][0] == latent["height"][0] and graph[latent["width"][0]]["class_type"] == "GetImageSize"


def test_sizes_are_capped_for_the_big_engines_and_scaled_back():
    assert engines._capped((1024, 1024)) == (1024, 1024)
    w, h = engines._capped((1536, 1536))
    assert w * h <= engines.MAX_BIG_MEGAPIXELS * 1e6 and w % 16 == 0 and w == h
    comfy = FakeComfy()
    out, _ = draw(comfy, "zimage", (1536, 1536))
    latent = comfy.graphs[-1]["7"]["inputs"]
    assert (latent["width"], latent["height"]) == (w, h)
    assert Image.open(io.BytesIO(out)).size == (1536, 1536)                 # downstream still gets the size it asked for
    w, h = engines.scaled_size((1024, 1536), 0.6)                           # an expression at draft quality keeps the shape
    assert w % 16 == 0 and h % 16 == 0 and abs(w * h - 0.6e6) < 0.05e6 and abs(h / w - 1.5) < 0.05


# ---- memory: the careful way -------------------------------------------------------------------------------------------------------
def test_a_big_picture_starts_from_an_empty_gpu_and_records_its_engine():
    comfy = FakeComfy()
    _, meta = draw(comfy, "zimage")
    assert meta == {"engine": "Z-Image Turbo"}
    assert comfy.events == ["free", "submit"] and engines.loaded(comfy) == "zimage"


def test_the_same_big_engine_stays_loaded_while_there_is_ram_to_spare():
    comfy = FakeComfy()
    for _ in range(3):
        edit(comfy)
    assert comfy.events == ["free", "submit", "submit", "submit"]


def test_the_same_big_engine_is_unloaded_when_ram_runs_low_between_pictures():
    comfy = FakeComfy()
    edit(comfy)
    comfy.ram_free = engines.KEEP_LOADED_FREE_RAM_MB // 1024 - 1
    _, meta = edit(comfy)
    assert comfy.events == ["free", "submit", "free", "mage"] and "not enough free memory" in meta["engine_note"]
    assert comfy.graphs[-1]["4"]["class_type"] == "TextEncodeMageFlowEdit"    # Mage-Flow drew it instead


def test_switching_between_big_engines_or_back_to_mage_flow_unloads_first():
    comfy = FakeComfy()
    draw(comfy, "qwen21")
    edit(comfy, "qwen21_edit")                                              # the same weights: no unload
    edit(comfy, "qwen2511")                                                 # other weights: unload
    draw(comfy, "mage")                                                     # Mage-Flow needs the room too
    assert comfy.events == ["free", "submit", "submit", "free", "submit", "free", "mage"]
    assert engines.loaded(comfy) is None


def test_release_unloads_a_big_engine_and_is_harmless_otherwise():
    comfy = FakeComfy()
    engines.release(comfy)
    assert comfy.events == []
    edit(comfy)
    engines.release(comfy)
    assert comfy.events[-1] == "free" and engines.loaded(comfy) is None


def test_running_short_of_ram_mid_picture_stops_it_and_mage_flow_draws_it():
    comfy = FakeComfy(ram_during=4, finish=False)
    _, meta = edit(comfy)
    assert "short of memory" in meta["engine_note"] and "Qwen-Image-Edit 2511" in meta["engine_note"]
    assert comfy.events == ["free", "submit", "interrupt", "free", "mage"] and engines.loaded(comfy) is None


def test_a_stalled_picture_is_stopped():
    comfy = FakeComfy(finish=False)
    _, meta = draw(comfy, "qwen2512")
    assert "took more than" in meta["engine_note"] and comfy.events[-1] == "mage"


def test_missing_files_fall_back_before_anything_is_unloaded():
    comfy = FakeComfy(files={"diffusion_models": [], "text_encoders": [], "vae": [], "loras": []})
    _, meta = draw(comfy, "zimage")
    assert "ComfyUI is missing diffusion_models/z_image_turbo_bf16.safetensors" in meta["engine_note"]
    assert comfy.events == ["mage"]
    assert engines.availability(comfy)["zimage"] and set(engines.availability(comfy)) == {
        k for t in (engines.IMAGE_ENGINES, engines.EDIT_ENGINES) for k, v in t.items() if "build" in v}


def test_too_many_references_fall_back_rather_than_dropping_one():
    comfy = FakeComfy()
    _, meta = edit(comfy, refs=("a", "b", "c", "d"))
    assert "at most 3" in meta["engine_note"] and comfy.events == ["mage"]


def test_cancelling_stops_the_picture_and_is_not_a_fallback():
    comfy, cancel = FakeComfy(finish=False), threading.Event()
    cancel.set()
    with pytest.raises(Cancelled):
        draw(comfy, "zimage", cancel=cancel)
    assert "submit" not in comfy.events


# ---- the job steps use the chosen engines --------------------------------------------------------------------------------------------
@pytest.fixture()
def world(tmp_path, monkeypatch):
    """A library character and stub engines that record which engine each step asked for."""
    jobsmod.PORTRAIT_ENGINES.clear()
    lib = Library(tmp_path / "library")
    npc = lib.create({"setting": "pirates", "style": "render3d", "seed": 11, "freeform": False,
                      "profile": {"first_name": "Mara", "last_name": "Test", "age": 30, "gender": "female", "occupation": "ship's cook"},
                      "appearance": {"outfit": "a tricorn hat"}})
    asked, fail = [], set()

    def fake_image(comfy, engine_id, *a, **kw):
        asked.append(("image", engine_id))
        return png((1024, 1024)), ({"engine_note": "Z-Image Turbo could not draw it: test"} if engine_id in fail else {"engine": engine_id})

    def fake_edit(comfy, engine_id, refs, *a, **kw):
        asked.append(("edit", engine_id, len(refs) if isinstance(refs, list) else 1))
        return png((1024, 1024)), {"engine": engine_id}
    monkeypatch.setattr(engines, "image", fake_image)
    monkeypatch.setattr(engines, "edit", fake_edit)
    comfy = types.SimpleNamespace(models=DEFAULT_MODELS, upload_image=lambda data, name: f"npc_generator/{name} [temp]")
    jobs = Jobs(copy.deepcopy(config.DEFAULTS), comfy, lib)

    def run(kind, key=None, **options):
        job = types.SimpleNamespace(npc_id=npc["id"], cancel=threading.Event(), cache={}, ref=None,
                                    options={**DEFAULT_OPTIONS, **options})
        step = {"kind": kind, "key": key, "label": kind}
        jobs._run_step(job, step)
        return step, lib.get(npc["id"])["assets"]
    yield run, asked, fail
    jobsmod.PORTRAIT_ENGINES.clear()


def test_portrait_sprite_and_expression_use_the_chosen_engines(world):
    run, asked, _ = world
    _, assets = run("portrait", image_model="zimage", edit_model="qwen2511")
    assert asked[-1] == ("image", "zimage") and assets["portrait"]["engine"] == "zimage"
    _, assets = run("sprite", image_model="zimage", edit_model="qwen2511", sprite_mode="match", transparent_sprite=False)
    assert asked[-1] == ("edit", "qwen2511", 1) and assets["sprite"]["engine"] == "qwen2511"
    _, assets = run("sprite", image_model="zimage", edit_model="qwen2511", sprite_mode="independent", transparent_sprite=False)
    assert asked[-1] == ("image", "zimage")
    _, assets = run("expression", "happy", edit_model="qwen21_edit")
    assert asked[-1] == ("edit", "qwen21_edit", 1) and assets["expressions"]["happy"]["engine"] == "qwen21_edit"


def test_a_job_saved_before_engines_existed_still_runs_on_mage_flow(world):
    run, asked, _ = world
    run("portrait", image_model=None)                                    # the option is simply absent in an old job record
    assert asked[-1] == ("image", None)                                  # engines.image treats that as Mage-Flow (tested above)


def test_a_fallback_is_said_on_the_step_and_kept_with_the_picture(world):
    run, asked, fail = world
    fail.add("zimage")
    step, assets = run("portrait", image_model="zimage")
    assert step["note"] == jobsmod.ENGINE_NOTE + "Z-Image Turbo could not draw it: test"
    assert assets["portrait"]["engine_note"].startswith("Z-Image Turbo")


def test_the_animator_and_the_sheeter_pass_the_edit_engine_and_its_note(monkeypatch):
    from npcgen.anim import Animator
    seen = []

    def fake_edit(comfy, engine_id, refs, *a, **kw):
        seen.append((engine_id, list(refs)))
        return png(), {"engine_note": "Qwen could not draw it: test"}
    monkeypatch.setattr(engines, "edit", fake_edit)
    animator = Animator(types.SimpleNamespace(), None, copy.deepcopy(config.DEFAULTS))
    job = types.SimpleNamespace(options={"edit_model": "qwen2511"}, cache={}, cancel=threading.Event())
    animator._edit(job, ["puppet", "front", "side"], {"positive": "walk", "negative": ""}, 5, 25, (576, 864))
    assert seen == [("qwen2511", ["puppet", "front", "side"])] and job.cache["engine_note"].startswith("Qwen")
