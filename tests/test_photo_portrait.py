"""Uploading a real photo replaces the from-scratch portrait roll with an edit of that photo, keeping the person's
likeness. Nothing here needs a GPU: service.clean_photo is pure, and the job step is driven by a FakeComfy."""
import base64
import copy
import io
import threading
import types

import pytest
from PIL import Image

from npcgen import config, jobs as jobsmod, prompts, roll, service
from npcgen.comfy import DEFAULT_MODELS
from npcgen.jobs import DEFAULT_OPTIONS, Jobs
from npcgen.library import Library


def png_bytes(colour=(120, 90, 60), size=(64, 96)):
    out = io.BytesIO()
    Image.new("RGB", size, colour).save(out, "PNG")
    return out.getvalue()


def data_url(raw, mime="image/png"):
    return f"data:{mime};base64," + base64.b64encode(raw).decode("ascii")


# ---- service.clean_photo ---------------------------------------------------------------------------------------------
def test_a_data_url_round_trips_to_png_bytes_and_reports_its_size():
    png, size = service.clean_photo(data_url(png_bytes(size=(64, 96))))
    assert Image.open(io.BytesIO(png)).format == "PNG" and size == [64, 96]


def test_bare_base64_without_a_data_url_prefix_also_works():
    png, size = service.clean_photo(base64.b64encode(png_bytes()).decode("ascii"))
    assert size == [64, 96]


def test_a_jpeg_photo_is_accepted_and_converted_to_png():
    out = io.BytesIO()
    Image.new("RGB", (80, 80), (10, 20, 30)).save(out, "JPEG")
    png, size = service.clean_photo(data_url(out.getvalue(), "image/jpeg"))
    assert Image.open(io.BytesIO(png)).format == "PNG" and size == [80, 80]


def test_exif_rotation_is_baked_in_so_the_edit_model_sees_the_photo_upright():
    """A phone can write an EXIF Orientation tag instead of physically rotating the pixels; PIL does not apply it on
    Image.open alone, so a naive save would hand the editor an upside-down person. Using a 180-degree flip (rather
    than a 90-degree turn) sidesteps any ambiguity about rotation direction -- 180 is its own inverse, so this can't
    pass for the wrong reason by getting clockwise/counterclockwise backwards."""
    marked = Image.new("RGB", (40, 80), (255, 255, 255))
    for x in range(10):
        for y in range(10):
            marked.putpixel((x, y), (255, 0, 0))          # a red patch in the top-left corner only
    stored = marked.rotate(180)                            # what a phone that thinks it's upside down actually saves
    exif = Image.Exif()
    exif[0x0112] = 3                                        # Orientation: "rotate 180 degrees to display correctly"
    out = io.BytesIO()
    stored.save(out, "JPEG", exif=exif)
    png, _ = service.clean_photo(data_url(out.getvalue(), "image/jpeg"))
    fixed = Image.open(io.BytesIO(png))
    r, g, b = fixed.getpixel((2, 2))
    assert r > 200 and b < 100, "the red corner should be back at the top-left once the orientation is corrected"


def test_an_oversized_photo_is_downscaled():
    png, size = service.clean_photo(data_url(png_bytes(size=(3000, 4000))))
    assert max(size) == service.MAX_PHOTO_DIMENSION


@pytest.mark.parametrize("bad", [None, "", "   ", "not base64 at all!!", "data:image/png;base64,not-valid-base64!!",
                                 data_url(b"this is not an image")])
def test_garbage_input_raises_value_error_not_some_other_crash(bad):
    with pytest.raises(ValueError):
        service.clean_photo(bad)


# ---- prompts.portrait_from_photo -------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def data():
    return roll.load_data()


def _npc(data, setting="pirates", **extra):
    npc = roll.roll_npc(data, setting, seed=7)
    npc["appearance"].update(extra)
    return npc


def test_the_photo_edit_prompt_says_to_keep_identity_and_change_the_rest(data):
    npc = _npc(data)
    style = data["styles"]["render3d"]
    pr = prompts.portrait_from_photo(npc, style, data["settings"]["pirates"])
    assert "identity" in pr["positive"] and "reference photo" in pr["positive"]
    assert style["edit_name"] in pr["positive"]


def test_the_photo_edit_prompt_weaves_in_the_settings_context(data):
    npc = _npc(data, setting="pirates")
    pr = prompts.portrait_from_photo(npc, data["styles"]["render3d"], data["settings"]["pirates"])
    assert data["settings"]["pirates"]["context"] in pr["positive"]


def test_the_photo_edit_prompt_carries_the_rolled_outfit_and_distinguishing_feature(data):
    npc = _npc(data, outfit="a tricorn hat and a weathered coat", distinguishing="a jagged scar across one eyebrow")
    pr = prompts.portrait_from_photo(npc, data["styles"]["render3d"], data["settings"]["pirates"])
    assert "a tricorn hat and a weathered coat" in pr["positive"]
    assert "a jagged scar across one eyebrow" in pr["positive"]


def test_the_photo_edit_prompt_uses_the_edit_negative_like_sprite_and_expression_edits(data):
    npc = _npc(data)
    style = data["styles"]["render3d"]
    setting = data["settings"]["pirates"]
    pr = prompts.portrait_from_photo(npc, style, setting)
    assert pr["negative"] == prompts._edit_negative(style, setting)


def test_the_photo_edit_prompt_carries_a_settings_own_negative_terms_too(data):
    """Unlike sprite_edit/expression_edit (which only ever touch an already-styled portrait), this is the one edit
    prompt establishing a setting's look for the first time, so it needs that setting's own negative terms too --
    e.g. superhero.json's trademark guard, added after a real GPU render came back with a Superman shield on it."""
    npc = _npc(data, setting="superhero")
    style = data["styles"]["chibi"]
    setting = data["settings"]["superhero"]
    assert setting.get("negative"), "this test needs superhero.json to actually have a negative field"
    pr = prompts.portrait_from_photo(npc, style, setting)
    assert setting["negative"] in pr["negative"]


# ---- Jobs._run_step, kind "portrait" -----------------------------------------------------------------------------------
def img(colour):
    out = io.BytesIO()
    Image.new("RGB", (64, 64), colour).save(out, "PNG")
    return out.getvalue()


FROM_SCRATCH, FROM_PHOTO = img((200, 30, 30)), img((30, 200, 30))


class FakeComfy:
    models = DEFAULT_MODELS

    def __init__(self):
        self.uploads, self.graphs = [], []

    def upload_image(self, data, filename):
        self.uploads.append((filename, data))
        return f"npc_generator/{filename} [temp]"

    def run(self, graph, cancel=None):
        self.graphs.append(graph)
        return FROM_PHOTO if any(u[0].endswith("_source_photo.png") for u in self.uploads) else FROM_SCRATCH


@pytest.fixture()
def world(tmp_path):
    jobsmod.PORTRAIT_ENGINES.clear()
    lib = Library(tmp_path / "library")
    npc = lib.create({"setting": "pirates", "style": "render3d", "seed": 11, "freeform": False,
                      "profile": {"first_name": "Mara", "last_name": "Test", "age": 30, "gender": "female", "occupation": "ship's cook"},
                      "appearance": {"outfit": "a tricorn hat"}})
    comfy = FakeComfy()
    jobs = Jobs(copy.deepcopy(config.DEFAULTS), comfy, lib)

    def draw(job=None):
        job = job or types.SimpleNamespace(npc_id=npc["id"], cancel=threading.Event(), cache={}, ref=None, options={**DEFAULT_OPTIONS})
        step = {"kind": "portrait", "label": "Portrait"}
        jobs._run_step(job, step)
        return job, step, lib.get(npc["id"])["assets"]["portrait"], lib.resolve(npc["id"], "portrait.png").read_bytes()
    yield lib, npc["id"], comfy, draw
    jobsmod.PORTRAIT_ENGINES.clear()


def test_with_no_source_photo_the_portrait_step_is_unchanged(world):
    """Confirms the photo branch is additive: PORTRAIT_ENGINES hooks still fire exactly as test_portrait_engines.py
    already verifies, and nothing about the plain from-scratch path regressed."""
    lib, npc_id, comfy, draw = world
    seen = {}
    jobsmod.add_portrait_engine(lambda **ctx: seen.update(ctx) or None)     # takes nothing, just observes
    job, step, meta, data_bytes = draw()
    assert data_bytes == FROM_SCRATCH and "source" not in meta and comfy.uploads == []
    assert seen["npc"]["profile"]["first_name"] == "Mara"


def test_a_source_photo_is_edited_instead_of_generated_from_scratch(world):
    lib, npc_id, comfy, draw = world
    lib.write_asset(npc_id, "source_photo.png", png_bytes())
    lib.update(npc_id, lambda n: n.setdefault("assets", {}).update(source_photo={"file": "source_photo.png", "created": "t1"}))
    seen = {}
    jobsmod.add_portrait_engine(lambda **ctx: seen.update(ctx) or None)
    job, step, meta, data_bytes = draw()
    assert data_bytes == FROM_PHOTO and meta["source"] == "photo"
    assert seen == {}, "PORTRAIT_ENGINES must not be consulted for a photo-sourced portrait"
    assert len(comfy.uploads) == 1 and comfy.uploads[0][0] == f"{npc_id}_source_photo.png"
    w, h = config.DEFAULTS["quality"]["standard"]["portrait"]
    assert (comfy.graphs[-1]["4"]["inputs"]["width"], comfy.graphs[-1]["4"]["inputs"]["height"]) == (w, h)
    uploaded_name = f"npc_generator/{npc_id}_source_photo.png [temp]"
    assert comfy.graphs[-1]["20"]["inputs"]["image"] == uploaded_name, "the graph must reference the uploaded PHOTO, not the (nonexistent) portrait"


def test_the_uploaded_photo_is_cached_for_the_rest_of_the_job_not_re_uploaded(world):
    lib, npc_id, comfy, draw = world
    lib.write_asset(npc_id, "source_photo.png", png_bytes())
    lib.update(npc_id, lambda n: n.setdefault("assets", {}).update(source_photo={"file": "source_photo.png", "created": "t1"}))
    job = types.SimpleNamespace(npc_id=npc_id, cancel=threading.Event(), cache={}, ref=None, options={**DEFAULT_OPTIONS})
    draw(job)
    draw(job)                                       # a second portrait step in the same job (e.g. a re-roll retry)
    assert len(comfy.uploads) == 1, "the photo should be uploaded once per job, cached by job.cache['photo_ref']"