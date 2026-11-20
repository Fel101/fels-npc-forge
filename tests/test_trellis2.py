"""The "Trellis2 four pictures" 3D generator: its ComfyUI graph, what it needs, the plan and the model step."""
import copy
import threading
import types

import pytest

from npcgen import config, eyesoft, glassesoff, gpu, posekeys, service, three_d
from npcgen.comfy import ComfyError
from npcgen.jobs import Jobs
from npcgen.library import Library

VIEWS = three_d.PIXAL_SHEET_VIEWS
IMAGES = {v: f"npc_{v}.png [temp]" for v in VIEWS}


def _graph(**more):
    return three_d.trellis2_graph(IMAGES, "mira", "job1", 12345, **more)


# ---- the graph -----------------------------------------------------------------------------------------------------------

def test_every_link_in_the_graph_goes_to_a_node_that_is_there():
    g = _graph()
    for node_id, node in g.items():
        for name, value in node["inputs"].items():
            if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str):
                assert value[0] in g, (node_id, name, value)


def test_the_four_pictures_become_the_packs_four_views_at_the_right_angles():
    g = _graph()
    loads = [n["inputs"]["image"] for n in g.values() if n["class_type"] == "LoadImage"]
    assert loads == [IMAGES[v] for v in ("front", "side_left", "back", "side_right")]
    config_node = g["71"]
    assert config_node["class_type"] == "Trellis2Pixal3DMultiViewConfig"
    assert config_node["inputs"]["azimuths"] == "0,90,180,270" and config_node["inputs"]["elevations"] == "0,0,0,0"
    assert config_node["inputs"]["fov"] == three_d.RIG_FOV
    batch = g[config_node["inputs"]["images"][0]]["inputs"]
    assert list(batch) == ["images.image0", "images.image1", "images.image2", "images.image3"]
    for index, key in enumerate(batch):                     # each view is cut out, cropped square with a margin, then given its alpha
        join = g[batch[key][0]]
        assert join["class_type"] == "JoinImageWithAlpha"
        crop = g[join["inputs"]["image"][0]]
        assert crop["class_type"] == "ImageCropToMask" and crop["inputs"]["pad_factor"] == three_d.RIG_PAD
        assert g[crop["inputs"]["images"][0]]["inputs"]["image"] == loads[index]


def test_the_model_is_loaded_for_multi_view_and_let_go_of_afterwards():
    load = _graph()["72"]["inputs"]
    assert load["modelname"] == three_d.TRELLIS2_MODEL and load["pixal3d_multiview"] is True and load["keep_models_loaded"] is False
    assert load["backend"] == "sdpa" and load["sparse_backend"] == "sdpa"                 # flash attention is not on every card


def test_the_three_stages_share_the_views_and_follow_the_packs_example_settings():
    g = _graph()
    for node in ("75", "76", "77"):
        inputs = g[node]["inputs"]
        assert inputs["pixal3d_mv_views"] == ["71", 0] and inputs["moge_camera_config"] == ["71", 1]
    assert g["75"]["inputs"]["seed"] == 12345 and g["75"]["inputs"]["sparse_structure_sampler"] == "heun"
    assert g["76"]["inputs"]["resolution"] == 512 and g["77"]["inputs"]["to_resolution"] == 1024
    assert g["77"]["inputs"]["shape_slat"] == ["76", 0] and g["77"]["inputs"]["from_resolution"] == ["76", 1]


def test_the_packs_own_decode_is_replaced_by_ones_without_cumesh_and_its_mesh_goes_to_the_remesh():
    g = _graph()
    assert not any(n["class_type"] in ("Trellis2DecodeLatents", "Trellis2FillHoles") for n in g.values())
    assert g["78"]["class_type"] == three_d.DECODE_NODE and g["78"]["inputs"]["shape_slat"] == ["77", 0]
    assert g["79"]["class_type"] == "RemeshMesh" and g["79"]["inputs"]["mesh"] == ["78", 1]     # its second output: ComfyUI's own MESH


def test_the_saved_model_and_picture_are_found_by_their_node_not_by_order():
    g = _graph()
    glb, shot = three_d.TRELLIS2_OUTPUTS
    assert g[glb]["class_type"] == "SaveGLB" and g[glb]["inputs"]["filename_prefix"] == "npcforge/mira-job1"
    assert g[shot]["class_type"] == "SaveImage" and g[shot]["inputs"]["images"] == ["83", 0]
    entry = {"outputs": {"73": {"images": [{"filename": "a pack node's preview"}]}, glb: {"3d": [{"filename": "model.glb"}]},
                         shot: {"images": [{"filename": "preview.png"}]}}}
    assert three_d.saved_file(entry, "images", "preview", node=shot)["filename"] == "preview.png"
    assert three_d.saved_file(entry, "images", "preview")["filename"] == "a pack node's preview"
    with pytest.raises(ComfyError, match="no preview"):
        three_d.saved_file({"outputs": {}}, "images", "preview", node=shot)


def test_the_faces_kept_follow_the_detail_setting_like_an_import():
    medium = three_d.PIXAL_QUALITIES["medium"]
    assert _graph()["80"]["inputs"]["target_face_count"] == medium["faces"] and _graph()["79"]["inputs"]["resolution"] == medium["remesh"]
    assert _graph(quality="high")["80"]["inputs"]["target_face_count"] == three_d.PIXAL_QUALITIES["high"]["faces"]
    assert _graph(quality="nonsense")["80"]["inputs"]["target_face_count"] == medium["faces"]


def test_a_missing_seed_is_drawn_and_every_seed_fits_the_packs_integer():
    seeds = {three_d.trellis2_graph(IMAGES, "m", "j", None)["75"]["inputs"]["seed"] for _ in range(5)}
    assert len(seeds) > 1 and all(0 <= s <= 2147483647 for s in seeds)
    assert three_d.trellis2_graph(IMAGES, "m", "j", 2 ** 40 + 5)["75"]["inputs"]["seed"] <= 2147483647


def test_the_gpu_gate_sees_the_model_the_pack_loads_though_its_name_has_no_file_suffix():
    assert "TencentARC/Pixal3D" in gpu.graph_models(_graph())
    assert gpu.graph_models(_graph()) != gpu.graph_models(three_d.import_graph("a.glb [output]", "m", "j"))


# ---- what it needs ---------------------------------------------------------------------------------------------------------------

class Comfy:
    base = "http://test-trellis2-availability"

    def __init__(self, nodes=None, models=("TencentARC/Pixal3D",), inputs=("modelname", "pixal3d_multiview"), asked=True,
                 decode_outputs=("MESHWITHVOXEL", "MESH")):
        self.nodes = set(nodes) if nodes is not None else set(three_d.GENERATORS["trellis2"]["nodes"])
        self.models, self.inputs, self.asked, self.decode_outputs = list(models), inputs, asked, list(decode_outputs)

    def get_json(self, path, timeout=30):
        if path == "/object_info":
            return {name: {} for name in self.nodes}
        if path == "/object_info/Trellis2LoadModel":
            if not self.asked:
                raise ComfyError("Cannot reach ComfyUI")
            if "Trellis2LoadModel" not in self.nodes:
                return {}
            required = {"modelname": ["COMBO", {"options": self.models}]}
            required.update({name: ["BOOLEAN", {}] for name in self.inputs if name != "modelname"})
            return {"Trellis2LoadModel": {"input": {"required": required}}}
        if path == f"/object_info/{three_d.DECODE_NODE}":
            if not self.asked:
                raise ComfyError("Cannot reach ComfyUI")
            return {three_d.DECODE_NODE: {"input": {"required": {}}, "output": self.decode_outputs}} if three_d.DECODE_NODE in self.nodes else {}
        if path == "/models/background_removal":
            return ["birefnet.safetensors"]
        return []


@pytest.fixture(autouse=True)
def fresh_caches():
    for cache in (three_d._FILES, three_d._NODES, three_d._INFO):
        cache.clear()
    yield
    for cache in (three_d._FILES, three_d._NODES, three_d._INFO):
        cache.clear()


def _missing(comfy):
    return three_d.availability(comfy)["trellis2"]


def test_a_comfyui_with_the_pack_its_model_and_the_node_is_ready():
    assert _missing(Comfy()) == []


def test_a_model_the_pack_does_not_list_is_named():
    assert _missing(Comfy(models=("microsoft/TRELLIS.2-4B",))) == ["models/TencentARC/Pixal3D"]


def test_an_older_pack_without_the_multi_view_setting_is_named():
    assert _missing(Comfy(inputs=("modelname",))) == ["nodes/Trellis2LoadModel.pixal3d_multiview"]


def test_an_older_copy_of_the_decode_node_is_named_with_how_to_replace_it():
    """ComfyUI keeps a custom node as it was when it started, so after the file is updated the old one still runs until a restart."""
    (message,) = _missing(Comfy(decode_outputs=("MESHWITHVOXEL",)))
    assert message.startswith("nodes/" + three_d.DECODE_NODE) and "install_pixal_head_node.py --trellis2" in message and "restart ComfyUI" in message


def test_a_missing_node_is_named_once_and_its_details_are_not_asked_about():
    assert _missing(Comfy(nodes=set(three_d.GENERATORS["trellis2"]["nodes"]) - {three_d.DECODE_NODE})) == ["nodes/" + three_d.DECODE_NODE]
    gone = set(three_d.GENERATORS["trellis2"]["nodes"]) - {"Trellis2LoadModel"}
    three_d._INFO.clear()
    three_d._NODES.clear()
    assert _missing(Comfy(nodes=gone)) == ["nodes/Trellis2LoadModel"]


def test_a_comfyui_that_cannot_be_asked_about_a_node_is_not_blamed_for_it():
    assert _missing(Comfy(asked=False)) == []


def test_the_other_generators_are_unchanged_by_the_new_checks():
    got = three_d.availability(Comfy())
    assert set(got) == {"hunyuan21", "pixal3d", "trellis2"} and got["trellis2"] == []
    assert all(any(m.startswith(("nodes/", "checkpoints/", "diffusion_models/")) for m in got[k]) for k in ("hunyuan21", "pixal3d"))


def test_the_new_generator_is_offered_and_every_generator_says_whether_it_has_colour():
    ids = [c["id"] for c in three_d.choices()]
    assert ids == ["hunyuan21", "pixal3d", "trellis2"] and three_d.clean("trellis2") == "trellis2" and three_d.clean("other") == "hunyuan21"
    assert {k: v["textured"] for k, v in three_d.GENERATORS.items()} == {"hunyuan21": False, "pixal3d": True, "trellis2": False}
    assert all(v["short"] for v in three_d.GENERATORS.values())
    assert three_d.FOUR_VIEW_GENERATORS == ("pixal3d", "trellis2")


# ---- the plan ------------------------------------------------------------------------------------------------------------------------

TRELLIS = {"three_d_model": "trellis2"}


def _have(views=True, sprite=True):
    have = {}
    if sprite:
        have["sprite"] = {"file": "sprite.png", "created": "t"}
    if views:
        have["sheet"] = {"views": {v: {"file": f"sheet/{v}.png", "source_sprite_created": "t"} for v in VIEWS}}
    return {"assets": have}


def _kinds(steps):
    return [s["kind"] for s in steps]


def test_a_trellis2_model_asks_for_the_four_pictures_first_and_names_them_for_it():
    steps = Jobs.plan(_have(views=False), {"model_3d": True}, TRELLIS)
    assert _kinds(steps) == ["sheet"] * 4 + ["model_3d"]
    assert [s["key"] for s in steps[:4]] == list(VIEWS) and steps[0]["label"] == "Trellis2 view: front"
    assert steps[-1]["label"] == "Trellis2 model from four pictures"


def test_with_the_pictures_made_it_is_one_step_and_a_sprite_comes_first_when_there_is_none():
    assert _kinds(Jobs.plan(_have(), {"model_3d": True}, TRELLIS)) == ["model_3d"]
    assert _kinds(Jobs.plan(_have(views=False, sprite=False), {"model_3d": True}, TRELLIS))[:2] == ["sprite", "sheet"]


def test_hunyuans_front_only_picture_is_not_asked_for_instead_of_the_four():
    steps = Jobs.plan(_have(views=False), {"model_3d": True}, {**TRELLIS, "three_d_hunyuan_source": "turnaround"})
    assert [s["key"] for s in steps if s["kind"] == "sheet"] == list(VIEWS)


def test_painting_it_needs_no_picture_the_generator_did_not_already_need():
    steps = Jobs.plan(_have(views=False), {"model_3d": True, "refine_3d": True}, TRELLIS)
    assert _kinds(steps) == ["sheet"] * 4 + ["model_3d", "refine_3d"]


# ---- the model step -------------------------------------------------------------------------------------------------------------------

class FakeComfy:
    def __init__(self):
        self.uploads, self.graphs, self.fetched = [], [], []

    def upload_image(self, data, name, **kw):
        self.uploads.append((name, data))
        return f"{name} [temp]"

    def run_entry(self, graph, cancel=None, timeout=900, unload_models=False):
        self.graphs.append((graph, timeout, unload_models))
        return {"outputs": {"73": {"images": [{"filename": "decoy.png", "subfolder": "", "type": "temp"}]},
                            "82": {"3d": [{"filename": "model.glb", "subfolder": "npcforge", "type": "output"}]},
                            "84": {"images": [{"filename": "preview.png", "subfolder": "npcforge", "type": "output"}]}}}

    def fetch_file(self, filename, subfolder="", image_type="temp", byte_range=None):
        self.fetched.append(filename)
        return {"model.glb": b"GLB BYTES", "preview.png": b"PREVIEW", "decoy.png": b"WRONG"}[filename]


@pytest.fixture()
def bench(tmp_path):
    lib = Library(tmp_path / "library")
    npc = lib.create({"setting": "modern", "style": "render3d", "seed": 3, "appearance": {},
                      "profile": {"first_name": "Mira", "last_name": "Vale", "age": 30, "gender": "female", "occupation": "tester"}})
    lib.write_asset(npc["id"], "sprite.png", b"sprite")
    for view in VIEWS:
        lib.write_asset(npc["id"], f"sheet/{view}.png", view.encode())
    lib.update(npc["id"], lambda n: n["assets"].update(
        sprite={"file": "sprite.png", "created": "t"},
        sheet={"views": {v: {"file": f"sheet/{v}.png", "source_sprite_created": "t", "created": f"c-{v}"} for v in VIEWS}}))
    jobs = Jobs(copy.deepcopy(config.DEFAULTS), None, lib)
    comfy = FakeComfy()
    jobs.comfy = comfy

    def run(**options):
        job = types.SimpleNamespace(id="job1", npc_id=npc["id"], cancel=threading.Event(), cache={}, ref=None,
                                    options={"quality": "standard", "seed": 9, "three_d_model": "trellis2", **options})
        run.step = {"kind": "model_3d", "label": "model"}          # kept, for what a step says about itself (its note)
        jobs._run_step(job, run.step)
        return lib.get(npc["id"])["assets"]
    yield lib, npc["id"], comfy, run
    jobs.close()


def test_the_step_sends_the_four_pictures_runs_the_graph_and_keeps_the_model(bench):
    lib, npc_id, comfy, run = bench
    assets = run(three_d_quality="high")

    assert [name for name, _data in comfy.uploads] == [f"{npc_id}_trellis2_{v}.png" for v in VIEWS]
    assert [data for _name, data in comfy.uploads] == [v.encode() for v in VIEWS]
    graph, timeout, unload = comfy.graphs[0]
    names = {v: f"{npc_id}_trellis2_{v}.png [temp]" for v in VIEWS}
    assert graph == three_d.trellis2_graph(names, npc_id, "job1", 9, quality="high") and timeout == 3600 and unload is True
    assert lib.read_asset(npc_id, "model_3d.glb") == b"GLB BYTES" and lib.read_asset(npc_id, "model_3d_preview.png") == b"PREVIEW"
    assert "decoy.png" not in comfy.fetched                                   # the preview is the tail's, not another node's
    meta = assets["model_3d"]
    assert (meta["engine_id"], meta["textured"], meta["engine"]) == ("trellis2", False, three_d.GENERATORS["trellis2"]["label"])
    assert (meta["quality"], meta["quality_label"]) == ("high", "High") and "front_reference" not in meta
    assert meta["source_sheet_views"] == {v: f"c-{v}" for v in VIEWS} and meta["source_sprite_created"] == "t"


def test_a_picture_that_is_out_of_date_stops_the_step_before_anything_is_sent(bench):
    lib, npc_id, comfy, run = bench
    lib.update(npc_id, lambda n: n["assets"]["sheet"]["views"]["side_left"].update(stale=True))
    with pytest.raises(ComfyError, match="Trellis2 needs a current 'side left' model-sheet view"):
        run()
    assert comfy.graphs == []
    lib.update(npc_id, lambda n: n["assets"]["sheet"]["views"]["side_left"].pop("stale"))
    lib.update(npc_id, lambda n: n["assets"]["sheet"]["views"]["back"].update(source_sprite_created="older"))
    with pytest.raises(ComfyError, match="'back'"):
        run()


def test_the_step_does_not_need_a_portrait_or_a_head_picture(bench):
    _lib, _npc_id, comfy, run = bench
    run()
    assert len(comfy.uploads) == 4 and not any("head" in name for name, _ in comfy.uploads)


def test_a_second_model_keeps_the_first_among_the_earlier_ones(bench):
    lib, npc_id, comfy, run = bench
    run()
    lib.write_asset(npc_id, "model_3d_original.glb", b"the first one, before its clean-up")
    lib.update(npc_id, lambda n: n["assets"]["model_3d"].update(raw="model_3d_original.glb", created="2026-10-14T10:00:00"))
    assets = run()
    (entry,) = assets["model_history"]
    kept = f"model_history/{entry['id']}/"
    assert lib.read_asset(npc_id, kept + "model_3d.glb") == b"GLB BYTES" and lib.read_asset(npc_id, kept + "model_3d_original.glb") == b"the first one, before its clean-up"
    assert entry["model"]["created"] == "2026-10-14T10:00:00" and entry["model"]["raw"] == "model_3d_original.glb"
    assert not lib.resolve(npc_id, "model_3d_original.glb").exists() and "raw" not in assets["model_3d"]      # (the new model has no original of its own)


def test_a_pixal3d_model_is_made_and_kept_by_the_same_step(bench, monkeypatch):
    """The end of the step clears the pictures-without-glasses of an earlier model for every four-picture generator, but only the
    Trellis2 branch knew whether glasses had been taken off: a Pixal3D model came back from the GPU and the step then stopped on a name
    that was never set (Nadia Ivanova, 2026-10-15), so the model was never recorded."""
    lib, npc_id, comfy, run = bench
    monkeypatch.setattr(three_d, "prepare_head_reference", lambda raw, size=1024: (b"HEAD:" + raw, 0.0))
    lib.write_asset(npc_id, f"{glassesoff.DIR}/front.png", b"a copy from an earlier model")
    assets = run(three_d_model="pixal3d")

    meta = assets["model_3d"]
    assert (meta["engine_id"], meta["textured"], meta["front_reference"]) == ("pixal3d", True, "turnaround")
    assert lib.read_asset(npc_id, "model_3d.glb") == b"GLB BYTES"
    assert not lib.resolve(npc_id, glassesoff.DIR).exists()


# ---- softening the eyes for the shape (npcgen/eyesoft.py) -----------------------------------------------------------------------------

def test_softening_the_eyes_is_in_the_steps_label_only_when_asked():
    assert Jobs.plan(_have(), {"model_3d": True}, {**TRELLIS, "three_d_soften_eyes": True})[-1]["label"] ==         "Trellis2 model from four pictures, eyes softened for the shape"
    assert Jobs.plan(_have(), {"model_3d": True}, TRELLIS)[-1]["label"] == "Trellis2 model from four pictures"


def test_the_option_is_off_by_default_and_must_be_a_boolean():
    cfg = copy.deepcopy(config.DEFAULTS)
    assert service.clean_options({}, cfg)["three_d_soften_eyes"] is False
    assert service.clean_options({"three_d_soften_eyes": True}, cfg)["three_d_soften_eyes"] is True
    with pytest.raises(ValueError, match="three_d_soften_eyes must be true or false"):
        service.clean_options({"three_d_soften_eyes": "yes"}, cfg)


class Softener:
    """eyesoft.soften with the SDPose round trip left out: every picture comes back with 'soft-' in front, front included."""

    def __init__(self, error=None):
        self.error, self.calls = error, []

    def __call__(self, comfy, pictures, npc_id, cancel=None):
        self.calls.append((dict(pictures), npc_id, cancel))
        if self.error:
            raise self.error
        out = {view: (b"soft-" + data if view != "back" else data) for view, data in pictures.items()}
        return out, {"eye_width_px": 16.8, "views": {"front": ["l", "r"], "side_left": ["l", "r"], "side_right": ["l", "r"]}}


@pytest.fixture()
def softening(bench, monkeypatch):
    soften = Softener()
    monkeypatch.setattr(eyesoft, "soften", soften)
    monkeypatch.setattr(posekeys, "availability", lambda comfy: {"model": "sdpose", "missing": []})
    return bench, soften


def test_with_the_option_the_shape_is_made_from_the_softened_pictures_and_the_model_says_so(softening):
    (lib, npc_id, comfy, run), soften = softening
    assets = run(three_d_soften_eyes=True)

    sent, soften_id, _cancel = soften.calls[0]
    assert sent == {v: v.encode() for v in VIEWS} and soften_id == npc_id                   # it was given the sheet's own pictures
    assert [(name, data) for name, data in comfy.uploads] == [
        (f"{npc_id}_trellis2_{v}.png", v.encode() if v == "back" else b"soft-" + v.encode()) for v in VIEWS]
    graph = comfy.graphs[0][0]
    assert graph == three_d.trellis2_graph({v: f"{npc_id}_trellis2_{v}.png [temp]" for v in VIEWS}, npc_id, "job1", 9, quality="medium")
    assert assets["model_3d"]["eyes_softened"]["views"]["front"] == ["l", "r"]
    assert lib.read_asset(npc_id, "sheet/front.png") == b"front"                           # the sheet itself, which the paint reads, is untouched


def test_without_the_option_nothing_is_softened(softening):
    (_lib, _npc_id, comfy, run), soften = softening
    assets = run()
    assert soften.calls == [] and "eyes_softened" not in assets["model_3d"]
    assert [data for _name, data in comfy.uploads] == [v.encode() for v in VIEWS]


def test_without_sdpose_the_option_stops_the_step_and_says_what_is_missing(bench, monkeypatch):
    _lib, _npc_id, comfy, run = bench
    soften = Softener()
    monkeypatch.setattr(eyesoft, "soften", soften)
    monkeypatch.setattr(posekeys, "availability", lambda comfy: {"model": None, "missing": ["SDPose in ComfyUI's models/checkpoints"]})
    with pytest.raises(ComfyError, match="Softening the eyes needs SDPose in ComfyUI's models/checkpoints"):
        run(three_d_soften_eyes=True)
    assert soften.calls == [] and comfy.graphs == [] and comfy.uploads == []


def test_eyes_that_cannot_be_found_stop_the_step_before_the_generator_runs(bench, monkeypatch):
    _lib, _npc_id, comfy, run = bench
    monkeypatch.setattr(eyesoft, "soften", Softener(error=eyesoft.SoftenError("SDPose found no face in the front model-sheet picture")))
    monkeypatch.setattr(posekeys, "availability", lambda comfy: {"model": "sdpose", "missing": []})
    with pytest.raises(ComfyError, match="no face in the front"):
        run(three_d_soften_eyes=True)
    assert comfy.graphs == []                                                               # the four minutes of generation were not spent


def test_inside_the_best_quality_chain_eyes_that_cannot_be_found_do_not_stop_it(bench, monkeypatch):
    _lib, _npc_id, comfy, run = bench
    monkeypatch.setattr(eyesoft, "soften", Softener(error=eyesoft.SoftenError("SDPose found no face in the front model-sheet picture")))
    monkeypatch.setattr(posekeys, "availability", lambda comfy: {"model": "sdpose", "missing": []})
    assets = run(three_d_soften_eyes=True, three_d_pictures_optional=True)
    assert [data for _name, data in comfy.uploads] == [v.encode() for v in VIEWS]           # made from the pictures as they are
    assert "no face in the front" in assets["model_3d"]["eyes_not_softened"] and "eyes_softened" not in assets["model_3d"]
    assert run.step["note"].startswith("Eyes not softened: SDPose found no face")


def test_a_character_with_glasses_is_made_from_the_pictures_as_they_are_and_the_job_says_why(softening):
    (lib, npc_id, comfy, run), soften = softening
    lib.update(npc_id, lambda n: n["appearance"].update(glasses="thin round glasses"))
    assets = run(three_d_soften_eyes=True)
    assert soften.calls == [] and [data for _name, data in comfy.uploads] == [v.encode() for v in VIEWS]
    assert "eyes_softened" not in assets["model_3d"] and "thin round glasses" in assets["model_3d"]["eyes_not_softened"]
    assert run.step["note"].startswith("Eyes not softened: this character wears glasses")


# ---- the glasses taken off the pictures (npcgen/glassesoff.py) ------------------------------------------------------------------------------

class Stripper:
    """glassesoff.strip with the edit model left out: every picture comes back with 'bare-' in front."""

    def __init__(self, error=None):
        self.error, self.calls = error, []

    def __call__(self, comfy, pictures, npc_id, seed, steps, cfg, engine=None, cancel=None):
        self.calls.append({"pictures": dict(pictures), "npc_id": npc_id, "engine": engine, "seed": seed})
        if self.error:
            raise self.error
        return ({view: b"bare-" + data for view, data in pictures.items()},
                {"engine": "Fake edit model", "views": {view: {"redrawn": 0.1, "replaced": 0.3} for view in pictures}})


@pytest.fixture()
def stripping(bench, monkeypatch):
    strip = Stripper()
    monkeypatch.setattr(glassesoff, "strip", strip)
    lib, npc_id, _comfy, _run = bench
    lib.update(npc_id, lambda n: n["appearance"].update(glasses="thin round glasses"))
    return bench, strip


def test_the_glasses_step_is_in_the_label_only_for_a_wearer_who_asked():
    wearer = {"assets": _have()["assets"], "appearance": {"glasses": "thin round glasses"}}
    assert Jobs.plan(wearer, {"model_3d": True}, {**TRELLIS, "three_d_glasses_off": True})[-1]["label"] ==         "Trellis2 model from four pictures, glasses taken off the pictures"
    assert Jobs.plan(wearer, {"model_3d": True}, TRELLIS)[-1]["label"] == "Trellis2 model from four pictures"
    assert Jobs.plan(_have(), {"model_3d": True}, {**TRELLIS, "three_d_glasses_off": True})[-1]["label"] == "Trellis2 model from four pictures"
    assert Jobs.plan(wearer, {"model_3d": True}, {**TRELLIS, "three_d_glasses_off": True, "three_d_soften_eyes": True})[-1]["label"] ==         "Trellis2 model from four pictures, glasses taken off the pictures, eyes softened for the shape"


def test_with_the_option_a_wearer_is_made_from_copies_without_the_glasses_which_are_kept(stripping):
    (lib, npc_id, comfy, run), strip = stripping
    assets = run(three_d_glasses_off=True, edit_model="qwen2511")

    call = strip.calls[0]
    assert call["pictures"] == {v: v.encode() for v in VIEWS} and call["engine"] == "qwen2511" and call["npc_id"] == npc_id
    assert [data for _name, data in comfy.uploads] == [b"bare-" + v.encode() for v in VIEWS]            # the shape is made from the copies
    for view in VIEWS:
        assert lib.read_asset(npc_id, f"sheet/no_glasses/{view}.png") == b"bare-" + view.encode()        # kept for the paint
        assert lib.read_asset(npc_id, f"sheet/{view}.png") == view.encode()                              # the sheet itself is untouched
    meta = assets["model_3d"]["glasses_off"]
    assert meta["worn"] == "thin round glasses" and meta["engine"] == "Fake edit model"
    assert meta["pictures"] == {v: f"sheet/no_glasses/{v}.png" for v in VIEWS} and "glasses_not_taken_off" not in assets["model_3d"]


def test_a_character_without_glasses_is_left_alone_and_old_copies_are_cleared(stripping):
    (lib, npc_id, comfy, run), strip = stripping
    lib.update(npc_id, lambda n: n["appearance"].update(glasses=""))
    lib.write_asset(npc_id, "sheet/no_glasses/front.png", b"from an earlier model")
    assets = run(three_d_glasses_off=True)
    assert strip.calls == [] and "glasses_off" not in assets["model_3d"]
    assert [data for _name, data in comfy.uploads] == [v.encode() for v in VIEWS]
    assert not lib.resolve(npc_id, "sheet/no_glasses").exists()


def test_without_the_option_nothing_is_taken_off_and_the_eyes_are_still_skipped(stripping):
    (_lib, _npc_id, comfy, run), strip = stripping
    assets = run(three_d_soften_eyes=True)
    assert strip.calls == [] and "glasses_off" not in assets["model_3d"] and "thin round glasses" in assets["model_3d"]["eyes_not_softened"]
    assert [data for _name, data in comfy.uploads] == [v.encode() for v in VIEWS]


def test_with_the_glasses_off_the_eyes_are_softened_like_anyone_elses(stripping, monkeypatch):
    (_lib, _npc_id, comfy, run), strip = stripping
    soften = Softener()
    monkeypatch.setattr(eyesoft, "soften", soften)
    monkeypatch.setattr(posekeys, "availability", lambda comfy: {"model": "sdpose", "missing": []})
    assets = run(three_d_glasses_off=True, three_d_soften_eyes=True)
    assert soften.calls[0][0] == {v: b"bare-" + v.encode() for v in VIEWS}                               # SDPose is given the copies without glasses
    assert "eyes_softened" in assets["model_3d"] and "eyes_not_softened" not in assets["model_3d"]
    assert [data for _name, data in comfy.uploads] == [(b"soft-" if v != "back" else b"") + b"bare-" + v.encode() for v in VIEWS]


def test_glasses_that_would_not_come_off_stop_the_step_alone_but_not_the_best_quality_chain(bench, monkeypatch):
    lib, npc_id, comfy, run = bench
    lib.update(npc_id, lambda n: n["appearance"].update(glasses="thin round glasses"))
    monkeypatch.setattr(glassesoff, "strip", Stripper(error=glassesoff.StripError("the edit model left the glasses on the front picture")))
    with pytest.raises(ComfyError, match="left the glasses on"):
        run(three_d_glasses_off=True)
    assert comfy.graphs == []
    assets = run(three_d_glasses_off=True, three_d_pictures_optional=True)
    assert run.step["note"].startswith("Glasses not taken off: the edit model left the glasses on")
    assert "left the glasses on" in assets["model_3d"]["glasses_not_taken_off"] and "glasses_off" not in assets["model_3d"]
    assert [data for _name, data in comfy.uploads][-4:] == [v.encode() for v in VIEWS]                  # made from the sheet's own pictures
