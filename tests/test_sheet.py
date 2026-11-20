import copy
import io
import threading
import types

import numpy as np
import pytest
from PIL import Image, ImageDraw

from npcgen import anim, config, export, roll, service, sheet
from npcgen.comfy import DEFAULT_MODELS, ComfyError
from npcgen.jobs import Jobs
from npcgen.library import Library

PANEL = (576, 1152)


@pytest.fixture(scope="module")
def data():
    return roll.load_data()


def png(im):
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def figure_on(bg, height, x=150, width=150, colour=(30, 60, 120), size=PANEL):
    """A dark block 'person' on a flat backdrop, with its feet at 87% of the height (not where align_panel wants them)."""
    im = Image.new("RGB", size, bg)
    feet = int(size[1] * 0.87)
    ImageDraw.Draw(im).rectangle([x, feet - height, x + width, feet], fill=colour)
    return im


def sprite_png():
    im = Image.new("RGBA", (300, 900), (0, 0, 0, 0))
    ImageDraw.Draw(im).rectangle([20, 10, 280, 890], fill=(200, 80, 40, 255))
    return png(im)


def npc_record(**appearance):
    return {"freeform": False, "style": "semi_real", "profile": {"gender": "female", "age": 40},
            "appearance": {"outfit": "a cream blouse, an olive skirt and boots", **appearance}}


# ---- prompts ------------------------------------------------------------------------------------------------------------
def test_each_view_asks_for_its_own_direction_and_keeps_the_art_style(data):
    style, npc = data["styles"]["semi_real"], npc_record()
    prompt = {v: sheet.view_prompt(data["sheet"], v, style, npc, "a_pose")["positive"] for v in sheet.VIEWS}
    assert "facing right" in prompt["side_right"] and "facing left" in prompt["side_left"]
    assert "directly behind" in prompt["back"] and "face is not visible" in prompt["back"]
    assert "head-and-shoulders" in prompt["portrait"] and "facing the camera" in prompt["front"]
    for text in prompt.values():
        assert style["edit_name"] in text and "white background" in text and "woman" in text


def test_the_back_prompt_gives_no_hairstyle_examples_to_copy(data):
    """A first wording said 'a bun, braid or ponytail stays as it is'; the model then drew a bun on every character."""
    back = sheet.view_prompt(data["sheet"], "back", data["styles"]["semi_real"], npc_record(), "a_pose")["positive"].lower()
    assert not any(word in back for word in ("bun", "braid", "ponytail"))


def test_the_back_prompt_states_the_characters_real_hair_and_keeps_the_hat(data):
    style = data["styles"]["semi_real"]
    back = lambda **kw: sheet.view_prompt(data["sheet"], "back", style, npc_record(**kw), "a_pose")["positive"]
    assert "the hair is black wavy long hair" in back(hair="black wavy long hair") and "same hat or other headwear" in back()
    assert "the head is bald" in back(hair="bald")
    assert "Seen from behind" not in back()                                               # nothing invented when the data has no hair
    freeform = sheet.view_prompt(data["sheet"], "back", style, {**npc_record(hair="red hair"), "freeform": True}, "a_pose")["positive"]
    assert "red hair" not in freeform and "Seen from behind" not in freeform


def test_the_pose_changes_what_the_arms_do(data):
    style, npc = data["styles"]["semi_real"], npc_record()
    a = sheet.view_prompt(data["sheet"], "front", style, npc, "a_pose")["positive"]
    relaxed = sheet.view_prompt(data["sheet"], "front", style, npc, "relaxed")["positive"]
    assert "A-pose" in a and "A-pose" not in relaxed and "relaxed by the sides" in relaxed


def test_freeform_characters_are_not_described_as_people(data):
    npc = {**npc_record(), "freeform": True}
    for view in sheet.VIEWS:
        text = sheet.view_prompt(data["sheet"], view, data["styles"]["cartoon"], npc, "a_pose")["positive"]
        assert "character" in text and "woman" not in text and "hair" not in text.replace("hairs", "")


def test_bare_feet_are_only_ruled_out_for_characters_who_wear_shoes(data):
    style = data["styles"]["semi_real"]
    shod = sheet.view_prompt(data["sheet"], "front", style, npc_record(), "a_pose")["negative"]
    sandals = sheet.view_prompt(data["sheet"], "front", style, npc_record(outfit="a sundress and sandals"), "a_pose")["negative"]
    assert "barefoot" in shod and "barefoot" not in sandals


def test_the_negative_prompt_lists_each_term_once(data):
    terms = [t.strip() for t in sheet.view_prompt(data["sheet"], "back", data["styles"]["pixel"], npc_record(), "a_pose")["negative"].split(",")]
    assert len(terms) == len(set(terms))


def test_every_view_gets_its_own_seed_and_the_seed_is_stable():
    seeds = {v: sheet.view_seed(7, v) for v in sheet.VIEWS}
    assert len(set(seeds.values())) == len(sheet.VIEWS) and seeds == {v: sheet.view_seed(7, v) for v in sheet.VIEWS}
    assert all(0 <= s < 2 ** 32 for s in seeds.values())


# ---- image work -----------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("height", [900, 1100])
def test_a_view_is_scaled_to_the_common_height_and_stands_on_the_baseline(height):
    out, info = sheet.align_panel(png(figure_on((250, 250, 250), height)), PANEL)
    img = Image.open(io.BytesIO(out))
    arr = np.asarray(img)
    box = sheet.mask_box(sheet.figure_mask(arr, sheet.border_colour(arr)))
    assert img.size == PANEL and info["aligned"]
    assert abs((box[3] - box[1]) - PANEL[1] * sheet.PANEL_FILL) <= 3
    assert abs(box[3] - PANEL[1] * sheet.BASELINE) <= 3
    assert abs((box[0] + box[2]) / 2 - PANEL[0] / 2) <= 3
    assert tuple(arr[2, 2]) == (250, 250, 250) and tuple(arr[-3, -3]) == (250, 250, 250)      # an off-white backdrop shows no seam


def test_a_picture_without_a_figure_is_left_alone():
    out, info = sheet.align_panel(png(Image.new("RGB", PANEL, "white")), PANEL)
    assert info["aligned"] is False and Image.open(io.BytesIO(out)).size == PANEL


def test_a_cut_out_sprite_is_placed_on_white_at_the_common_height():
    fitted = Image.open(io.BytesIO(sheet.fit_reference(sprite_png(), PANEL)))
    arr = np.asarray(fitted)
    box = sheet.mask_box(sheet.figure_mask(arr, np.array([255, 255, 255])))
    assert fitted.size == PANEL and fitted.mode == "RGB"
    assert abs((box[3] - box[1]) - PANEL[1] * sheet.PANEL_FILL) <= 3 and abs(box[3] - PANEL[1] * sheet.BASELINE) <= 3
    assert tuple(arr[0, 0]) == (255, 255, 255)


def test_a_portrait_is_fitted_whole_with_white_above_and_below():
    fitted = np.asarray(Image.open(io.BytesIO(sheet.fit_reference(png(Image.new("RGB", (1024, 1024), (120, 120, 120))), PANEL))))
    assert tuple(fitted[5, 5]) == (255, 255, 255) and tuple(fitted[PANEL[1] // 2, PANEL[0] // 2]) == (120, 120, 120)


def test_panels_are_stitched_side_by_side_with_a_white_gutter():
    panels = [Image.new("RGB", (100, 200), c) for c in ((255, 0, 0), (0, 255, 0), (0, 0, 255))]
    out = sheet.stitch(panels, gap=10)
    assert out.size == (3 * 100 + 2 * 10, 200)
    assert out.getpixel((50, 5)) == (255, 0, 0) and out.getpixel((105, 5)) == (255, 255, 255) and out.getpixel((160, 5)) == (0, 255, 0)


def test_colour_match_notices_a_wholesale_colour_change():
    pal = anim.palette(Image.open(io.BytesIO(sprite_png())))
    same = png(figure_on("white", 800, colour=(200, 80, 40)))
    changed = png(figure_on("white", 800, colour=(55, 175, 215)))
    assert sheet.colour_match(same, pal) > 0.9 > sheet.MIN_COLOUR_MATCH > sheet.colour_match(changed, pal)


# ---- planning and validation ------------------------------------------------------------------------------------------------
def test_a_sheet_request_is_validated(data):
    ok = service.clean_assets({"sheet": {"views": ["back", "front", "nonsense"], "pose": "relaxed"}}, data)["sheet"]
    assert ok == {"views": ["front", "back"], "pose": "relaxed"}                            # canonical order, unknown views dropped
    with pytest.raises(ValueError, match="at least one asset"):                            # an empty dict is "no sheet requested"
        service.clean_assets({"sheet": {}}, data)
    assert service.clean_sheet({}, data) == {"views": list(sheet.VIEWS), "pose": data["sheet"]["default_pose"]}
    for bad, message in [({"views": ["nope"]}, "at least one model-sheet view"), ({"pose": "cartwheel"}, "Unknown pose")]:
        with pytest.raises(ValueError, match=message):
            service.clean_sheet(bad, data)


def test_the_planner_adds_the_sprite_then_one_step_per_view_then_the_stitch(data):
    request = service.clean_assets({"sheet": {"views": ["front", "back"]}}, data)
    steps = Jobs.plan({"assets": {}}, request)
    assert [(s["kind"], s["key"]) for s in steps] == [("sprite", None), ("sheet", "front"), ("sheet", "back"), ("sheet_stitch", None)]
    assert "needed for the model sheet" in steps[0]["label"]
    assert Jobs.plan({"assets": {"sprite": {}}}, request)[0]["kind"] == "sheet"


def test_a_sheet_comes_before_the_animation_steps(data):
    request = service.clean_assets({"sheet": {"pose": "a_pose"}, "animations": {"clips": ["idle"]}}, data)
    kinds = [s["kind"] for s in Jobs.plan({"assets": {"sprite": {}}}, request)]
    assert kinds.index("sheet_stitch") < kinds.index("view")


# ---- generation with a stand-in for ComfyUI ---------------------------------------------------------------------------------------
class FakeComfy:
    """Answers every graph with a white picture holding a figure of a different size per call, and records what it was asked."""
    models = DEFAULT_MODELS

    def __init__(self):
        self.uploads, self.graphs = [], []

    def upload_image(self, data, filename):
        self.uploads.append(filename)
        return f"npc_generator/{filename} [temp]"

    def run(self, graph, cancel=None):
        self.graphs.append(graph)
        size = (graph["4"]["inputs"]["width"], graph["4"]["inputs"]["height"])          # ComfyUI draws what it is asked for
        return png(figure_on("white", int(size[1] * 0.55) + 40 * len(self.graphs), x=size[0] // 4, width=size[0] // 4, colour=(200, 80, 40), size=size))


@pytest.fixture()
def world(tmp_path):
    lib = Library(tmp_path / "library")
    npc = lib.create({"setting": "modern", "style": "semi_real", "seed": 5, "freeform": False,
                      "profile": {"first_name": "Mara", "last_name": "Test", "age": 45, "gender": "female", "occupation": "shopkeeper"},
                      "appearance": {"outfit": "a cream blouse and an olive skirt"}})
    lib.write_asset(npc["id"], "sprite.png", sprite_png())
    lib.update(npc["id"], lambda n: n["assets"].update(sprite={"file": "sprite.png", "created": "t1", "transparent": True}))
    fake = FakeComfy()
    sheeter = sheet.Sheeter(fake, lib, copy.deepcopy(config.DEFAULTS))

    def job(views=sheet.VIEWS, quality="draft"):
        return types.SimpleNamespace(npc_id=npc["id"], cancel=threading.Event(), cache={},
                                     options={"quality": quality, "sheet": {"views": list(views), "pose": "a_pose"}})
    return lib, npc["id"], fake, sheeter, job


def test_a_full_sheet_is_drawn_aligned_stitched_and_recorded(world, data):
    lib, npc_id, fake, sheeter, job = world
    j = job()
    for seed_view in sheet.VIEWS:
        sheeter.make_view(j, lib.get(npc_id), data, seed_view, 5)
    sheeter.stitch(j, lib.get(npc_id), data)
    npc = lib.get(npc_id)
    meta = npc["assets"]["sheet"]
    w, h = config.DEFAULTS["quality"]["draft"]["sheet"]
    assert meta["order"] == list(sheet.VIEWS) and meta["panel"] == [w, h] and meta["size"] == [5 * w + 4 * sheet.GAP, h]
    assert Image.open(lib.resolve(npc_id, "sheet/sheet.png")).size == (5 * w + 4 * sheet.GAP, h)
    assert all(lib.resolve(npc_id, f"sheet/{v}.png").exists() and lib.resolve(npc_id, f"sheet/raw/{v}.png").exists() for v in sheet.VIEWS)
    assert len(fake.uploads) == 1                                                       # the sprite went up once for all five views
    assert len({g["5"]["inputs"]["seed"] for g in fake.graphs}) == 5                    # every view has its own seed
    heights = [meta["views"][v]["height"] for v in sheet.FULL_BODY]
    assert len(set(heights)) > 1                                                        # the stand-in drew them different sizes...
    for v in sheet.FULL_BODY:                                                           # ...and the aligned files are all the same height
        arr = np.asarray(Image.open(lib.resolve(npc_id, f"sheet/{v}.png")))
        box = sheet.mask_box(sheet.figure_mask(arr, sheet.border_colour(arr)))
        assert abs((box[3] - box[1]) - h * sheet.PANEL_FILL) <= 3
    assert meta["report"]["ok"] and set(meta["report"]["views"]) == set(sheet.FULL_BODY)


def test_the_portrait_view_is_drawn_from_the_portrait_when_there_is_one(world, data):
    lib, npc_id, fake, sheeter, job = world
    lib.write_asset(npc_id, "portrait.png", png(Image.new("RGB", (512, 512), (90, 90, 90))))
    lib.update(npc_id, lambda n: n["assets"].update(portrait={"file": "portrait.png", "created": "t0"}))
    j = job()
    sheeter.make_view(j, lib.get(npc_id), data, "portrait", 5)
    sheeter.make_view(j, lib.get(npc_id), data, "front", 5)
    assert lib.get(npc_id)["assets"]["sheet"]["views"]["portrait"]["from"] == "portrait"
    assert lib.get(npc_id)["assets"]["sheet"]["views"]["front"]["from"] == "sprite"
    assert len(fake.uploads) == 2


def test_a_new_sprite_is_uploaded_again_within_the_same_job(world, data):
    lib, npc_id, fake, sheeter, job = world
    j = job()
    sheeter.make_view(j, lib.get(npc_id), data, "front", 5)
    lib.update(npc_id, lambda n: n["assets"]["sprite"].update(created="t2"))
    sheeter.make_view(j, lib.get(npc_id), data, "back", 5)
    assert len(fake.uploads) == 2


def test_a_view_needs_a_sprite(world, data):
    lib, npc_id, fake, sheeter, job = world
    lib.update(npc_id, lambda n: n["assets"].pop("sprite"))
    with pytest.raises(ComfyError, match="generate the sprite first"):
        sheeter.make_view(job(), lib.get(npc_id), data, "front", 5)


def test_a_flat_recolour_is_flagged_in_the_report(world, data):
    lib, npc_id, fake, sheeter, job = world
    fake.run = lambda graph, cancel=None: png(figure_on("white", 800, colour=(55, 175, 215)))
    j = job(views=["front"])
    sheeter.make_view(j, lib.get(npc_id), data, "front", 5)
    sheeter.stitch(j, lib.get(npc_id), data)
    report = lib.get(npc_id)["assets"]["sheet"]["report"]
    assert not report["ok"] and "Front" in report["flags"][0]


def test_only_a_complete_redraw_clears_the_outdated_mark(world, data):
    lib, npc_id, fake, sheeter, job = world
    partial = job(views=["back"])
    sheeter.make_view(partial, lib.get(npc_id), data, "back", 5)
    lib.update(npc_id, lambda n: n["assets"]["sheet"].update(stale=True))
    sheeter.stitch(partial, lib.get(npc_id), data)
    assert lib.get(npc_id)["assets"]["sheet"].get("stale") is True                       # one new view does not make the others current
    full = job()
    for v in sheet.VIEWS:
        sheeter.make_view(full, lib.get(npc_id), data, v, 5)
    sheeter.stitch(full, lib.get(npc_id), data)
    assert "stale" not in lib.get(npc_id)["assets"]["sheet"]


# ---- library and export -------------------------------------------------------------------------------------------------------------
def finished_sheet(world, data):
    lib, npc_id, fake, sheeter, job = world
    j = job()
    for v in sheet.VIEWS:
        sheeter.make_view(j, lib.get(npc_id), data, v, 5)
    sheeter.stitch(j, lib.get(npc_id), data)
    return lib, npc_id


def test_the_export_holds_every_view_the_sheet_and_a_record(world, data):
    lib, npc_id = finished_sheet(world, data)
    files = export.build_sheet(lib, lib.get(npc_id))
    assert set(files) == {f"mara-test_{v}.png" for v in sheet.VIEWS} | {"mara-test_sheet.png", "mara-test_sheet.json"}
    record = __import__("json").loads(files["mara-test_sheet.json"])
    assert record["order"] == list(sheet.VIEWS) and record["pose"] == "a_pose" and record["provenance"]["generator"] == "NPC Forge"
    assert record["views"]["back"]["seed"] == sheet.view_seed(5, "back")
    with pytest.raises(ValueError, match="no model sheet"):
        export.build_sheet(lib, {**lib.get(npc_id), "assets": {}})


def test_the_library_zip_carries_the_sheet_and_deleting_it_removes_the_folder(world, data):
    import zipfile
    lib, npc_id = finished_sheet(world, data)
    with zipfile.ZipFile(io.BytesIO(lib.export_zip([npc_id]))) as z:
        names = set(z.namelist())
    assert f"{npc_id}/sheet/sheet.png" in names and f"{npc_id}/sheet/front.png" in names
    assert not any("/raw/" in n for n in names)                                          # raw panels are working files
    lib.delete_asset(npc_id, "sheet")
    assert "sheet" not in lib.get(npc_id)["assets"] and not lib.resolve(npc_id, "sheet").exists()
    with pytest.raises(KeyError):
        lib.delete_asset(npc_id, "sheet")


def test_regenerating_the_sprite_or_the_portrait_marks_the_sheet_outdated(world, data):
    lib, npc_id = finished_sheet(world, data)
    jobs = Jobs(copy.deepcopy(config.DEFAULTS), world[2], lib)
    jobs._mark_animations_stale(npc_id)                                                  # what a new sprite triggers
    assert lib.get(npc_id)["assets"]["sheet"]["stale"] is True
    lib.update(npc_id, lambda n: n["assets"]["sheet"].pop("stale"))
    jobs._mark_derived_stale(npc_id)                                                     # what a new portrait triggers
    assert lib.get(npc_id)["assets"]["sheet"]["stale"] is True


def test_a_view_that_is_drawn_again_keeps_the_old_one_among_its_earlier_versions(world, data):
    lib, npc_id, fake, sheeter, job = world
    j = job()
    sheeter.make_view(j, lib.get(npc_id), data, "front", 5)
    assert "candidates" not in lib.get(npc_id)["assets"]                                 # the first drawing has nothing to keep
    first = lib.get(npc_id)["assets"]["sheet"]["views"]["front"]
    first_bytes, first_raw = lib.read_asset(npc_id, first["file"]), lib.read_asset(npc_id, first["raw"])
    sheeter.make_view(j, lib.get(npc_id), data, "front", 6)
    sheeter.make_view(j, lib.get(npc_id), data, "back", 5)
    npc = lib.get(npc_id)
    (kept,) = npc["assets"]["candidates"]["sheet/front"]
    assert kept["file"].startswith("candidates/sheet_front/") and kept["seed"] == first["seed"]
    assert lib.read_asset(npc_id, kept["file"]) == first_bytes and lib.read_asset(npc_id, kept["raw"]) == first_raw
    assert lib.read_asset(npc_id, "sheet/front.png") != first_bytes                      # and the new drawing is the one in use
    assert "sheet/back" not in npc["assets"]["candidates"]
    restored = lib.select_asset_candidate(npc_id, "sheet/front", kept["id"])
    assert lib.read_asset(npc_id, "sheet/front.png") == first_bytes and restored["assets"]["sheet"]["views"]["front"]["seed"] == first["seed"]
