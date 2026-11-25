"""Importing a 3D model made elsewhere: reading the GLB's description, the ComfyUI graph, the request, the plan and the job step."""
import copy
import io
import json
import struct
import threading
import types

import pytest
from PIL import Image

from npcgen import config, import3d, media, roll, service, three_d
from npcgen.comfy import ComfyError
from npcgen.jobs import Jobs
from npcgen.library import Library
from test_cleanup3d import make_glb, sphere

VIEWS = ("front", "side_left", "back", "side_right")


def glb(doc, binary=b"\0\0\0\0"):
    """A GLB around a JSON description; the geometry itself is never read, so a stub binary chunk will do."""
    text = json.dumps(doc).encode()
    text += b" " * (-len(text) % 4)
    return (struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(text) + 8 + len(binary)) + struct.pack("<II", len(text), 0x4E4F534A)
            + text + struct.pack("<II", len(binary), 0x004E4942) + binary)


def mesh_doc(faces=12, vertices=8, low=(0, 0, 0), high=(0.4, 1.0, 0.2), **extra):
    doc = {"asset": {"version": "2.0", "generator": "test"},
           "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "indices": 1}]}],
           "accessors": [{"componentType": 5126, "count": vertices, "type": "VEC3", "min": list(low), "max": list(high)},
                         {"componentType": 5125, "count": faces * 3, "type": "SCALAR"}]}
    doc.update(extra)
    return doc


# ---- what a GLB holds -----------------------------------------------------------------------------------------------------

def test_the_description_gives_faces_size_and_colour_without_reading_the_geometry():
    info = import3d.inspect(glb(mesh_doc(faces=7_500_000, vertices=3_000_000)))
    assert (info["faces"], info["vertices"], info["meshes"]) == (7_500_000, 3_000_000, 1)
    assert info["extent"] == [0.4, 1.0, 0.2] and info["textured"] is False and info["warnings"] == []
    assert info["generator"] == "test" and info["bytes"] == len(glb(mesh_doc(faces=7_500_000, vertices=3_000_000)))


def test_a_model_with_its_own_texture_is_told_so():
    pos, tri = sphere(0.5, rings=6, segments=8, centre=(0, 0.5, 0))
    info = import3d.inspect(make_glb(pos, tri))
    assert info["textured"] is True and info["faces"] == len(tri) and import3d.default_clean(info) is False


def test_a_dense_shape_with_no_colour_is_cleaned_by_default_and_nothing_else_is():
    dense = import3d.inspect(glb(mesh_doc(faces=1_000_001)))
    assert import3d.default_clean(dense) is True
    assert import3d.default_clean(import3d.inspect(glb(mesh_doc(faces=1_000_000)))) is False       # a sensible size already
    assert import3d.default_clean({**dense, "textured": True}) is False                              # cleaning would drop its texture


@pytest.mark.parametrize("data, message", [
    (b"", "too short"),
    (b"not a glb file at all, just text", "not a GLB"),
    (struct.pack("<III", 0x46546C67, 1, 40) + b"\0" * 28, "version 1"),
    (struct.pack("<III", 0x46546C67, 2, 40) + struct.pack("<II", 4, 0x004E4942) + b"\0" * 8, "damaged"),
    (glb(mesh_doc(extensionsRequired=["KHR_draco_mesh_compression"])), "Draco-compressed"),
    (glb(mesh_doc(extensionsRequired=["EXT_meshopt_compression"])), "meshopt-compressed"),
    (glb(mesh_doc(extensionsRequired=["KHR_mesh_quantization", "KHR_materials_unlit"])), "requires glTF extensions ComfyUI cannot read.*KHR_mesh_quantization"),
    (glb({"asset": {"version": "2.0"}, "meshes": []}), "no triangle mesh"),
    (glb({"asset": {"version": "2.0"}, "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "mode": 1}]}],
          "accessors": [{"count": 8}]}), "no triangle mesh"),
    (glb({"asset": {"version": "2.0"}, "meshes": [{"primitives": [{"attributes": {}}]}]}), "damaged"),
])
def test_files_that_cannot_be_used_are_refused_with_a_reason(data, message):
    with pytest.raises(ValueError, match=message):
        import3d.inspect(data)


def test_extensions_that_are_only_used_or_that_comfyui_supports_do_not_matter():
    """A compressed mesh with an uncompressed fallback lists its extension as used, not required: ComfyUI reads the fallback."""
    ok = mesh_doc(extensionsUsed=["KHR_draco_mesh_compression", "KHR_materials_unlit"], extensionsRequired=["KHR_materials_unlit", "EXT_texture_webp"])
    assert import3d.inspect(glb(ok))["faces"] == 12


def test_a_broken_description_is_refused():
    head = struct.pack("<III", 0x46546C67, 2, 40) + struct.pack("<II", 8, 0x4E4F534A) + b"{not json"
    with pytest.raises(ValueError, match="cannot be read"):
        import3d.inspect(head)


def test_a_model_lying_down_or_carrying_a_skeleton_gets_a_warning_not_a_refusal():
    lying = import3d.inspect(glb(mesh_doc(high=(1.0, 0.2, 0.4))))
    assert any("Y axis" in w for w in lying["warnings"])
    rigged = import3d.inspect(glb(mesh_doc(skins=[{}], animations=[{}])))
    assert rigged["skinned"] and rigged["animated"] and any("skeleton" in w for w in rigged["warnings"])
    lines = glb(mesh_doc(meshes=[{"primitives": [{"attributes": {"POSITION": 0}, "indices": 1},
                                                 {"attributes": {"POSITION": 0}, "mode": 1}]}]))
    assert any("lines or points" in w for w in import3d.inspect(lines)["warnings"])


def test_a_description_longer_than_the_bytes_read_says_how_much_more_it_needs():
    data = glb(mesh_doc())
    size = struct.unpack_from("<I", data, 12)[0]
    with pytest.raises(import3d.Incomplete) as caught:
        import3d.inspect(data[:30])
    assert caught.value.needed == 20 + size


# ---- reading it from ComfyUI --------------------------------------------------------------------------------------------------

class Folders:
    """A ComfyUI that serves files from memory, ranges included, and lists its folders."""

    def __init__(self, files=None, listing=None, combo=None):
        self.files, self.listing, self.combo, self.asked, self.uploads = files or {}, listing or {}, combo, [], []

    def fetch_file(self, filename, subfolder="", image_type="temp", byte_range=None):
        self.asked.append((filename, subfolder, image_type, byte_range))
        if (filename, subfolder, image_type) not in self.files:
            raise ComfyError(f"GET /view -> HTTP 404: {filename} not found")
        data = self.files[(filename, subfolder, image_type)]
        return data if byte_range is None else data[byte_range[0]:byte_range[1] + 1]

    def list_files(self, folder):
        return self.listing.get(folder, [])

    def get_json(self, path, timeout=30):
        if path == "/object_info/Load3DAdvanced" and self.combo is not None:
            return {"Load3DAdvanced": {"input": {"required": {"model_file": ["COMBO", {"options": self.combo}]}}}}
        raise ComfyError("no such endpoint")

    def upload_image(self, data, filename, image_type="temp", subfolder=None):
        self.uploads.append((filename, image_type, len(data)))
        return f"npc_generator/npc-generator-ab12/{filename} [{image_type}]"


def test_a_file_is_described_from_its_first_bytes_only():
    data = glb(mesh_doc(faces=900_000)) + b"x" * 5_000_000
    comfy = Folders({("shape.glb", "", "output"): data})
    info = import3d.describe(comfy, "shape.glb [output]")
    assert info["faces"] == 900_000
    assert comfy.asked == [("shape.glb", "", "output", (0, import3d.HEAD_BYTES - 1))]


def test_a_description_longer_than_the_first_read_is_read_again_in_full(monkeypatch):
    monkeypatch.setattr(import3d, "HEAD_BYTES", 40)
    comfy = Folders({("long.glb", "3d", "input"): glb(mesh_doc(faces=5))})
    assert import3d.describe(comfy, "3d/long.glb")["faces"] == 5
    assert [call[3] for call in comfy.asked][0] == (0, 39) and len(comfy.asked) == 2


def test_a_file_comfyui_cannot_give_is_a_readable_error():
    with pytest.raises(ValueError, match="could not give gone.glb from its output folder"):
        import3d.describe(Folders(), "gone.glb [output]")


@pytest.mark.parametrize("value, expected", [
    ("shape.glb [output]", ("output", "", "shape.glb")),
    ("3d/shape.glb", ("input", "3d", "shape.glb")),
    ("shape.glb [input]", ("input", "", "shape.glb")),
    ("npc_generator/npc-generator-ab12/c.GLB [temp]", ("temp", "npc_generator/npc-generator-ab12", "c.GLB")),
    ("sub\\shape.glb [output]", ("output", "sub", "shape.glb")),
])
def test_a_file_is_named_the_way_comfyuis_load_3d_node_names_it(value, expected):
    assert import3d.locate(value) == expected


@pytest.mark.parametrize("value", ["", None, 5, "..\\..\\x.glb", "../x.glb [output]", "sub/../x.glb", "/etc/x.glb", "C:/x.glb",
                                   "model.obj", "model.glb.txt", "a/b/c/d/e.glb", "dir//x.glb"])
def test_nothing_outside_comfyuis_folders_and_nothing_but_glb_is_accepted(value):
    with pytest.raises(ValueError):
        import3d.locate(value)


def test_every_name_is_stored_with_its_folder():
    assert import3d.canonical("3d/a.glb") == "3d/a.glb [input]" and import3d.canonical("a.glb [output]") == "a.glb [output]"


def test_the_sources_are_the_glb_files_of_comfyuis_folders_newest_first_without_repeats():
    comfy = Folders(listing={"output": ["new.glb [output]", "pic.png [output]", "old.GLB [output]"],
                             "input": ["face.png [input]", "mine.glb [input]"]},
                    combo=["none", "3d/dropped.glb", "3d/notes.txt", "mine.glb"])
    got = import3d.list_sources(comfy)
    assert [s["model_file"] for s in got] == ["new.glb [output]", "old.GLB [output]", "mine.glb [input]", "3d/dropped.glb [input]"]
    assert got[3] == {"model_file": "3d/dropped.glb [input]", "name": "3d/dropped.glb", "folder": "input"}


def test_a_comfyui_without_the_file_listing_or_the_loader_just_has_no_sources():
    assert import3d.list_sources(Folders()) == []


def test_the_list_of_sources_is_capped(monkeypatch):
    monkeypatch.setattr(import3d, "MAX_SOURCES", 3)
    comfy = Folders(listing={"output": [f"m{i}.glb [output]" for i in range(10)]})
    assert len(import3d.list_sources(comfy)) == 3


def test_a_local_file_goes_to_comfyuis_temp_folder_and_comes_back_as_a_load_3d_value(tmp_path):
    path = tmp_path / "My Model (1).glb"
    path.write_bytes(glb(mesh_doc()))
    comfy = Folders()
    value = import3d.upload(comfy, path)
    assert value == "npc_generator/npc-generator-ab12/My_Model__1_.glb [temp]"
    assert comfy.uploads == [("My_Model__1_.glb", "temp", path.stat().st_size)]


def test_a_local_file_too_big_for_comfyuis_upload_limit_says_where_to_put_it(tmp_path, monkeypatch):
    monkeypatch.setattr(import3d, "UPLOAD_LIMIT", 10)
    path = tmp_path / "big.glb"
    path.write_bytes(glb(mesh_doc()))
    with pytest.raises(ValueError, match=r"input\\3d folder"):
        import3d.upload(Folders(), path)


def test_a_local_file_that_is_not_a_glb_is_refused_before_it_is_sent(tmp_path):
    (tmp_path / "x.glb").write_bytes(b"this is not a glb file, not even close")
    (tmp_path / "x.obj").write_bytes(b"v 0 0 0")
    comfy = Folders()
    for name in ("x.glb", "x.obj", "missing.glb"):
        with pytest.raises(ValueError):
            import3d.upload(comfy, tmp_path / name)
    assert comfy.uploads == []


def test_preparing_a_request_settles_cleaning_and_whether_the_result_has_colour():
    textured = make_glb(*sphere(0.5, rings=4, segments=8, centre=(0, 0.5, 0)))
    comfy = Folders({("shape.glb", "", "output"): glb(mesh_doc(faces=5_000_000)), ("skin.glb", "", "output"): textured})
    shape = import3d.prepare(comfy, {"model_file": "shape.glb [output]", "clean": "auto", "detail": "medium"})
    assert shape["clean"] is True and shape["faces"] == 5_000_000 and shape["textured"] is False
    skin = import3d.prepare(comfy, {"model_file": "skin.glb [output]", "clean": "auto", "detail": "medium"})
    assert skin["clean"] is False and skin["textured"] is True                       # kept as it is, with its colour
    forced = import3d.prepare(comfy, {"model_file": "skin.glb [output]", "clean": True, "detail": "low"})
    assert forced["clean"] is True and forced["textured"] is False                   # cleaning drops the texture


# ---- the ComfyUI graph --------------------------------------------------------------------------------------------------------

def test_the_import_graph_loads_the_file_and_ends_in_a_saved_model_and_a_picture():
    g = three_d.import_graph("shape.glb [output]", "mira", "job1", "medium")
    assert g["1"]["inputs"]["model_file"] == "shape.glb [output]" and g["2"]["inputs"]["model_3d"] == ["1", 0]
    assert [g[k]["class_type"] for k in ("3", "4", "5", "6", "7", "8")] == [
        "RemeshMesh", "DecimateMesh", "MeshSmoothNormals", "SaveGLB", "RenderMesh", "SaveImage"]
    assert g["3"]["inputs"]["mesh"] == ["2", 0] and g["6"]["inputs"]["filename_prefix"] == "npcforge/import-mira-job1"
    assert g["8"]["inputs"]["filename_prefix"] == "npcforge/import-mira-job1-preview"
    assert {n["class_type"] for n in g.values()} <= set(three_d.IMPORT_NODES)


def test_the_import_uses_the_same_remesh_as_a_pixal3d_model_and_its_detail_picks_the_face_count():
    medium = three_d.PIXAL_QUALITIES["medium"]
    pixal = three_d.pixal3d_graph({v: f"{v}.png" for v in VIEWS}, {v: f"h{v}.png" for v in VIEWS}, 0.3, "npc", "job", 5)
    g = three_d.import_graph("a.glb [output]", "npc", "job", "medium")
    assert {k: v for k, v in g["3"]["inputs"].items() if k != "mesh"} == {k: v for k, v in pixal["58"]["inputs"].items() if k != "mesh"}
    assert g["3"]["inputs"]["resolution"] == medium["remesh"] and g["4"]["inputs"]["target_face_count"] == medium["faces"]
    for detail in ("low", "high"):
        other = three_d.import_graph("a.glb [output]", "npc", "job", detail)
        assert other["4"]["inputs"]["target_face_count"] == three_d.PIXAL_QUALITIES[detail]["faces"]
    assert three_d.import_graph("a.glb [output]", "npc", "job", "nonsense")["4"]["inputs"]["target_face_count"] == medium["faces"]


def test_the_choices_offered_for_the_detail_say_faces_not_textures():
    choices = three_d.import_detail_choices()
    assert [c["id"] for c in choices] == ["low", "medium", "high"]
    assert "700k faces" in choices[1]["label"] and "texture" not in json.dumps(choices).lower()


def test_importing_needs_only_comfyuis_own_mesh_nodes():
    class Comfy:
        base = "http://test-import-availability"

        def __init__(self, nodes):
            self.nodes = nodes

        def get_json(self, path, timeout=30):
            return {name: {} for name in self.nodes}

    three_d._NODES.clear()
    assert three_d.import_availability(Comfy(three_d.IMPORT_NODES)) == {"missing": []}
    three_d._NODES.clear()
    assert three_d.import_availability(Comfy(set(three_d.IMPORT_NODES) - {"Load3DAdvanced"})) == {"missing": ["nodes/Load3DAdvanced"]}
    three_d._NODES.clear()


# ---- the request ----------------------------------------------------------------------------------------------------------------

DATA = roll.load_data()


def test_a_request_to_import_is_cleaned_to_a_name_a_cleaning_choice_and_a_detail():
    assets = service.clean_assets({"import_3d": {"model_file": "3d/a.glb", "detail": "high"}}, DATA)
    assert assets["import_3d"] == {"model_file": "3d/a.glb [input]", "clean": "auto", "detail": "high"}
    again = service.clean_assets({"import_3d": {"model_file": "a.glb [output]", "clean": False, "detail": "bogus"}}, DATA)
    assert again["import_3d"] == {"model_file": "a.glb [output]", "clean": False, "detail": "medium"}


@pytest.mark.parametrize("raw, message", [
    ({"model_file": "x.obj"}, "Only .glb"),
    ({"model_file": "..\\x.glb"}, "inside one of ComfyUI"),
    ({}, None),
    ({"model_file": "a.glb", "clean": "yes"}, "clean must be"),
    (True, "must be an object"),
    ("a.glb", "must be an object"),
])
def test_a_bad_import_request_is_refused(raw, message):
    if message is None:                    # an empty request is no request: nothing else asked, so there is nothing to do
        with pytest.raises(ValueError, match="at least one"):
            service.clean_assets({"import_3d": raw}, DATA)
        return
    with pytest.raises(ValueError, match=message):
        service.clean_assets({"import_3d": raw}, DATA)


def test_importing_and_generating_a_model_in_one_request_is_refused():
    with pytest.raises(ValueError, match="not both"):
        service.clean_assets({"model_3d": True, "import_3d": {"model_file": "a.glb [output]"}}, DATA)


# ---- the plan ----------------------------------------------------------------------------------------------------------------------

def _request(**more):
    return {"model_file": "shape.glb [output]", "clean": True, "detail": "medium", **more}


def _have(views=True, sprite=True, **assets):
    have = dict(assets)
    if sprite:
        have["sprite"] = {"file": "sprite.png", "created": "t"}
    if views:
        have["sheet"] = {"views": {v: {"file": f"sheet/{v}.png", "source_sprite_created": "t"} for v in VIEWS}}
    return {"assets": have}


def _kinds(steps):
    return [s["kind"] for s in steps]


def test_an_import_alone_is_one_step_and_asks_for_no_pictures():
    assert _kinds(Jobs.plan({"assets": {}}, {"import_3d": _request()}, {})) == ["import_3d"]


def test_importing_into_a_character_with_a_model_sheet_then_painting_it_needs_nothing_else():
    assert _kinds(Jobs.plan(_have(), {"import_3d": _request(textured=False), "refine_3d": True}, {})) == ["import_3d", "refine_3d"]


def test_an_import_painted_in_the_same_request_gets_the_missing_pictures_first_and_the_import_goes_before_them_all():
    kinds = _kinds(Jobs.plan(_have(views=False), {"import_3d": _request(), "refine_3d": True}, {}))
    assert kinds == ["import_3d", "sheet", "sheet", "sheet", "sheet", "refine_3d"]


def test_a_bare_character_gets_a_sprite_to_draw_the_pictures_from():
    kinds = _kinds(Jobs.plan(_have(views=False, sprite=False), {"import_3d": _request(), "refine_3d": True}, {}))
    assert kinds[:2] == ["import_3d", "sprite"] and kinds[-1] == "refine_3d" and kinds.count("sheet") == 4


def test_an_import_that_keeps_its_own_texture_is_not_painted_from_pictures_that_do_not_exist():
    kinds = _kinds(Jobs.plan(_have(views=False, sprite=False), {"import_3d": _request(textured=True), "refine_3d": True}, {}))
    assert kinds == ["import_3d", "refine_3d"]


def test_a_skeleton_asked_with_an_import_does_not_make_a_model_as_well():
    kinds = _kinds(Jobs.plan(_have(), {"import_3d": _request(), "rig_3d": True}, {}))
    assert kinds == ["import_3d", "rig_3d"]


def test_painting_an_existing_blank_model_of_a_character_with_no_sprite_asks_for_one():
    npc = {"assets": {"model_3d": {"file": "model_3d.glb", "textured": False}}}
    kinds = _kinds(Jobs.plan(npc, {"refine_3d": True}, {}))
    assert kinds[0] == "sprite" and kinds.count("sheet") == 4 and kinds[-1] == "refine_3d"


def test_the_request_travels_with_the_job_to_its_step():
    class Recorder(Jobs):
        def __init__(self):
            self.queue = types.SimpleNamespace(put=lambda job, priority: None)
            self.jobs, self.lock, self.closed = {}, threading.RLock(), False

        def _persist(self):
            pass

    npc = {"id": "mira", "profile": {"first_name": "Mira", "last_name": "Vale"}, "assets": {}}
    jobs = Recorder()
    jobs.submit(npc, {"import_3d": _request()}, {"quality": "draft"})
    (job,) = jobs.jobs.values()
    assert job.options["import_3d"] == _request()


# ---- the step ------------------------------------------------------------------------------------------------------------------------

_preview = io.BytesIO()
Image.new("RGB", (2, 2)).save(_preview, "PNG")
PNG = _preview.getvalue()


class FakeComfy:
    """ComfyUI for the import step: serves the source file, runs the graph and serves what it 'saved'."""

    def __init__(self, source, result=None):
        self.source, self.result, self.graphs, self.fetched = source, result, [], []

    def fetch_file(self, filename, subfolder="", image_type="temp", byte_range=None):
        self.fetched.append((filename, subfolder, image_type))
        if filename.endswith(".png"):
            return PNG
        data = self.result if filename.startswith("import-") else self.source
        return data if byte_range is None else data[byte_range[0]:byte_range[1] + 1]

    def run_entry(self, graph, cancel=None, timeout=900, unload_models=False):
        self.graphs.append((graph, unload_models))
        return {"outputs": {"6": {"3d": [{"filename": "import-mira.glb", "subfolder": "npcforge", "type": "output"}]},
                            "8": {"images": [{"filename": "import-mira-preview.png", "subfolder": "npcforge", "type": "output"}]}}}


@pytest.fixture()
def bench(tmp_path):
    lib = Library(tmp_path / "library")
    npc = lib.create({"setting": "modern", "style": "render3d", "seed": 3, "appearance": {},
                      "profile": {"first_name": "Mira", "last_name": "Vale", "age": 30, "gender": "female", "occupation": "tester"}})
    lib.write_asset(npc["id"], "model_3d.glb", b"the old model")
    lib.write_asset(npc["id"], "model_3d_preview.png", b"old preview")
    lib.write_asset(npc["id"], "model_3d_original.glb", b"the old model, before its clean-up")
    lib.write_asset(npc["id"], "model_3d_head/front.png", b"close-up")
    lib.update(npc["id"], lambda n: n["assets"].update(model_3d={"file": "model_3d.glb", "preview": "model_3d_preview.png", "textured": True,
                                                                 "raw": "model_3d_original.glb", "created": "old"},
                                                       rig_3d={"file": "model_3d_rigged.glb"}))
    jobs = Jobs(copy.deepcopy(config.DEFAULTS), None, lib)

    def run(comfy, request):
        jobs.comfy = comfy
        job = types.SimpleNamespace(id="job1", npc_id=npc["id"], cancel=threading.Event(), cache={}, ref=None,
                                    options={"quality": "standard", "seed": None, "import_3d": request})
        step = {"kind": "import_3d", "label": "Import the 3D model"}
        jobs._run_step(job, step)
        return lib.get(npc["id"])["assets"], step
    yield lib, npc["id"], run
    jobs.close()


def test_the_step_closes_and_simplifies_the_file_and_makes_the_result_the_model(bench):
    lib, npc_id, run = bench
    shape = glb(mesh_doc(faces=7_500_000)) + b"\0" * 1000
    result = glb(mesh_doc(faces=694_000))
    comfy = FakeComfy(shape, result)
    assets, _step = run(comfy, _request(detail="high"))

    graph, unload = comfy.graphs[0]
    assert graph == three_d.import_graph("shape.glb [output]", npc_id, "job1", "high") and unload is True
    labelled = lib.read_asset(npc_id, "model_3d.glb")
    assert labelled == media.label(result, ai_generated=False)
    assert lib.read_asset(npc_id, "model_3d_preview.png") == media.label(PNG, ai_generated=False)
    meta = assets["model_3d"]
    assert (meta["engine"], meta["engine_id"], meta["textured"], meta["preview"]) == ("Imported: shape.glb", "import", False, "model_3d_preview.png")
    assert meta["imported"]["faces_in"] == 7_500_000 and meta["imported"]["faces"] == 694_000 and meta["imported"]["cleaned"] is True
    assert meta["quality"] == "high" and meta["quality_label"] == "High" and meta["size"] == len(labelled)
    assert "raw" not in meta and "refined" not in meta                    # the old model's clean-up is gone with it
    assert assets["rig_3d"]["stale"] is True                              # and its skeleton no longer fits
    assert not lib.resolve(npc_id, "model_3d_original.glb").exists() and not lib.resolve(npc_id, "model_3d_head").exists()


def test_the_model_an_import_replaces_is_kept_among_the_earlier_ones(bench):
    lib, npc_id, run = bench
    skin = make_glb(*sphere(0.5, rings=4, segments=8, centre=(0, 0.5, 0)))
    assets, _step = run(FakeComfy(skin), _request(clean=False))
    (entry,) = assets["model_history"]
    kept = f"model_history/{entry['id']}/"
    assert lib.read_asset(npc_id, kept + "model_3d.glb") == b"the old model"
    assert lib.read_asset(npc_id, kept + "model_3d_original.glb") == b"the old model, before its clean-up"
    assert lib.read_asset(npc_id, kept + "model_3d_head/front.png") == b"close-up" and lib.read_asset(npc_id, kept + "model_3d_preview.png") == b"old preview"
    assert entry["model"]["raw"] == "model_3d_original.glb" and entry["rig"] == {"file": "model_3d_rigged.glb"}
    assert lib.read_asset(npc_id, "model_3d.glb") == skin                                  # (and the new one is in use)


def test_without_cleaning_the_file_is_taken_as_it_is(bench):
    lib, npc_id, run = bench
    skin = make_glb(*sphere(0.5, rings=4, segments=8, centre=(0, 0.5, 0)))
    comfy = FakeComfy(skin)
    assets, step = run(comfy, _request(clean=False))
    assert comfy.graphs == [] and lib.read_asset(npc_id, "model_3d.glb") == skin
    meta = assets["model_3d"]
    assert meta["textured"] is True and meta["imported"]["cleaned"] is False and "preview" not in meta and "quality" not in meta
    assert not lib.resolve(npc_id, "model_3d_preview.png").exists()       # the old model's picture is not this one's
    assert "note" not in step


def test_an_automatic_choice_cleans_a_dense_shape_but_not_a_small_one(bench):
    lib, npc_id, run = bench
    small = glb(mesh_doc(faces=300_000))
    comfy = FakeComfy(small)
    run(comfy, {"model_file": "small.glb [output]", "clean": "auto", "detail": "medium"})
    assert comfy.graphs == []
    dense = FakeComfy(glb(mesh_doc(faces=3_000_000)), glb(mesh_doc(faces=690_000)))
    run(dense, {"model_file": "dense.glb [output]", "clean": "auto", "detail": "medium"})
    assert len(dense.graphs) == 1


def test_what_looks_wrong_with_the_file_is_noted_on_the_step(bench):
    _lib, _npc, run = bench
    _assets, step = run(FakeComfy(glb(mesh_doc(faces=10, high=(1.0, 0.2, 0.4)))), _request(clean=False))
    assert "Y axis" in step["note"] and step["note"][0].isupper() and step["note"].endswith(".")


def test_a_file_that_cannot_be_used_fails_the_step_in_words_and_changes_nothing(bench):
    lib, npc_id, run = bench
    for request, comfy, message in ((_request(), FakeComfy(b"not a glb at all, sorry about that"), "not a GLB"),
                                    (_request(model_file="..\\x.glb"), FakeComfy(b""), "inside one of ComfyUI")):
        with pytest.raises(ComfyError, match=message):
            run(comfy, request)
    assert lib.read_asset(npc_id, "model_3d.glb") == b"the old model"


def test_a_failed_import_ends_the_job_instead_of_painting_a_model_that_is_not_there(bench, monkeypatch):
    lib, npc_id, _run = bench
    ran = []

    def run_step(self, job, step):
        ran.append(step["kind"])
        if step["kind"] == "import_3d":
            raise ComfyError("ComfyUI could not give shape.glb")

    monkeypatch.setattr(Jobs, "_run_step", run_step)
    jobs = Jobs(copy.deepcopy(config.DEFAULTS), None, lib)
    try:
        steps = [{"kind": k, "key": None, "label": k, "status": "pending", "error": None} for k in ("import_3d", "sprite", "refine_3d")]
        from npcgen.jobs import Job
        job = Job(npc_id, "Mira Vale", steps, {"quality": "draft"})
        jobs._run_job(job)
    finally:
        jobs.close()
    assert ran == ["import_3d"] and [s["status"] for s in job.steps] == ["error", "cancelled", "cancelled"]
    assert job.status == "error" and "could not give" in job.error


# ---- a name typed without its folder -------------------------------------------------------------------------------------------

def test_a_name_without_a_folder_is_looked_for_in_the_output_folder_first_then_the_input_folder():
    shape = glb(mesh_doc(faces=5))
    both = Folders({("shape.glb", "", "output"): shape, ("shape.glb", "", "input"): glb(mesh_doc(faces=9))})
    model_file, info = import3d.resolve(both, "shape.glb")
    assert (model_file, info["faces"]) == ("shape.glb [output]", 5)
    only_input = Folders({("shape.glb", "", "input"): shape})
    assert import3d.resolve(only_input, "shape.glb")[0] == "shape.glb [input]"
    assert import3d.resolve(Folders({("x.glb", "3D", "output"): shape}), "3D/x.glb")[0] == "3D/x.glb [output]"


def test_a_name_with_its_folder_is_only_looked_for_there():
    comfy = Folders({("shape.glb", "", "input"): glb(mesh_doc())})
    with pytest.raises(import3d.NotFound, match="could not give shape.glb from its output folder"):
        import3d.resolve(comfy, "shape.glb [output]")
    assert comfy.asked == [("shape.glb", "", "output", (0, import3d.HEAD_BYTES - 1))]


def test_a_file_that_is_there_but_unusable_is_not_hidden_behind_a_search_of_the_other_folder():
    comfy = Folders({("bad.glb", "", "output"): b"this is not a glb at all, sorry about that", ("bad.glb", "", "input"): glb(mesh_doc())})
    with pytest.raises(ValueError, match="not a GLB"):
        import3d.resolve(comfy, "bad.glb")
    assert len(comfy.asked) == 1


def test_a_name_in_neither_folder_says_how_to_name_a_sub_folder():
    with pytest.raises(import3d.NotFound, match=r"no gone.glb in its output or input folder.*3D/gone.glb \[output\]"):
        import3d.resolve(Folders(), "gone.glb")


def test_a_typed_name_is_still_only_a_glb_inside_comfyuis_folders():
    for bad in ("../x.glb", "C:/x.glb", "x.obj", ""):
        with pytest.raises(ValueError):
            import3d.resolve(Folders(), bad)


# ---- the web routes -------------------------------------------------------------------------------------------------------------

class ServerComfy(Folders):
    def __init__(self, *args, online=True, **kw):
        super().__init__(*args, **kw)
        self.online = online
        self.base = "http://test-import-routes"

    def health(self):
        return {"online": self.online, "missing": []}

    def get_json(self, path, timeout=30):
        if path == "/object_info":
            return {name: {} for name in three_d.IMPORT_NODES}
        return super().get_json(path, timeout)


@pytest.fixture()
def routes(tmp_path, monkeypatch):
    import importlib
    import json as jsonlib
    import urllib.error
    import urllib.request

    monkeypatch.setenv("NPCGEN_LIBRARY", str(tmp_path / "library"))
    monkeypatch.setenv("NPCGEN_COMFY_URL", "http://127.0.0.1:9")
    server = importlib.import_module("server")
    app = server.App()
    httpd = server.create_server(("127.0.0.1", 0), app)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"

    def call(method, path, body=None):
        headers = {"Content-Type": "application/json"} if method != "GET" else {}
        data = jsonlib.dumps(body).encode() if body is not None else (b"{}" if method != "GET" else None)
        try:
            with urllib.request.urlopen(urllib.request.Request(base + path, data=data, method=method, headers=headers), timeout=30) as r:
                return r.status, jsonlib.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, jsonlib.loads(e.read() or b"{}")

    call.app = app
    yield call
    httpd.shutdown()
    httpd.server_close()
    app.close()
    three_d._NODES.clear()


def test_the_sources_route_lists_the_glb_files_comfyui_has(routes):
    routes.app.comfy = ServerComfy(listing={"output": ["shape.glb [output]", "a.png [output]"]}, combo=["none", "3d/b.glb"])
    status, body = routes("GET", "/api/import-3d/sources")
    assert status == 200 and body["missing"] == []
    assert [s["model_file"] for s in body["sources"]] == ["shape.glb [output]", "3d/b.glb [input]"]


def test_the_sources_route_says_so_when_comfyui_is_not_running(routes):
    routes.app.comfy = ServerComfy(online=False)
    status, body = routes("GET", "/api/import-3d/sources")
    assert status == 502 and "ComfyUI is not running" in body["error"]


def test_the_inspect_route_reads_the_file_and_names_its_folder(routes):
    routes.app.comfy = ServerComfy({("shape.glb", "", "output"): glb(mesh_doc(faces=2_000_000))})
    status, body = routes("GET", "/api/import-3d/inspect?model_file=shape.glb")
    assert status == 200 and body["model_file"] == "shape.glb [output]" and body["faces"] == 2_000_000 and body["clean"] is True
    status, body = routes("GET", "/api/import-3d/inspect?model_file=" + "..%2Fx.glb")
    assert status == 400 and "inside one of ComfyUI" in body["error"]
    status, body = routes("GET", "/api/import-3d/inspect?model_file=nothing.glb")
    assert status == 400 and "no nothing.glb" in body["error"]


def test_a_request_to_import_looks_inside_the_file_before_anything_is_queued(routes):
    comfy = ServerComfy({("shape.glb", "", "output"): glb(mesh_doc(faces=3_000_000)), ("bad.glb", "", "output"): b"not a glb, not at all, no"})
    routes.app.comfy = comfy
    queued = []
    routes.app.jobs.submit = lambda npc, assets, options: queued.append(assets) or {"id": "j", "total": 1}
    npc = routes.app.lib.create({"setting": "modern", "style": "render3d", "seed": 3, "appearance": {},
                                 "profile": {"first_name": "Mira", "last_name": "Vale", "age": 30, "gender": "female", "occupation": "tester"}})
    ask = lambda name, **more: routes("POST", "/api/generate", {"npc_id": npc["id"], "assets": {"import_3d": {"model_file": name, **more}}})

    status, body = ask("shape.glb [output]")
    assert status == 200 and queued[0]["import_3d"] == {"model_file": "shape.glb [output]", "clean": True, "detail": "medium", "faces": 3_000_000,
                                                      "textured": False, "warnings": []}
    status, body = ask("bad.glb [output]")
    assert status == 400 and "not a GLB" in body["error"] and len(queued) == 1
    status, body = routes("POST", "/api/generate", {"assets": {"import_3d": {"model_file": "shape.glb [output]"}}, "setting": "modern"})
    assert status == 400 and "one existing character" in body["error"] and len(queued) == 1
    status, body = routes("POST", "/api/generate", {"npc_id": npc["id"], "assets": {"import_3d": {"model_file": "shape.glb [output]"}, "model_3d": True}})
    assert status == 400 and "not both" in body["error"] and len(queued) == 1


def test_the_health_and_config_routes_know_about_the_import(routes):
    routes.app.comfy = ServerComfy()
    status, health = routes("GET", "/api/health")
    assert status == 200 and health["import_3d"] == {"missing": []}
    status, config = routes("GET", "/api/config")
    assert [c["id"] for c in config["three_d_import_details"]] == ["low", "medium", "high"] and config["api_version"] >= 21
