"""The detailed head: its close-up prompts, the framing, the Pixal3D head graph, planning and the job steps."""
import copy
import io
import json
import threading
import types
import zipfile

import numpy as np
import pytest
from PIL import Image, ImageDraw

from npcgen import cleanup3d, config, engines, hairmask, head3d, headdetail, headpaint, roll, service, sheet, three_d
from npcgen.comfy import ComfyError
from npcgen.jobs import Jobs, mark_assets_stale
from npcgen.library import Library


@pytest.fixture(scope="module")
def data():
    return roll.load_data()


def png(im):
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def npc_record(**appearance):
    return {"freeform": False, "style": "photoreal", "profile": {"gender": "female", "age": 40},
            "appearance": {"outfit": "a cream blouse", **appearance}}


def head_picture(size=(1024, 1024), width=500, height=700, top=100, x=None, backdrop=(235, 235, 235), colour=(90, 50, 30),
                 shoulders=True):
    """A head-and-shoulders-like picture: a tall block (the head) on a plain backdrop, with a wide block (the shoulders) under it."""
    im = Image.new("RGB", size, backdrop)
    x = size[0] // 2 - width // 2 if x is None else x
    d = ImageDraw.Draw(im)
    d.rectangle([x, top, x + width - 1, top + height - 1], fill=colour)
    if shoulders:
        d.rectangle([60, top + height, size[0] - 60, size[1]], fill=(20, 20, 20))
    return im


def whole_body_picture():
    im = Image.new("RGB", (1024, 1024), (235, 235, 235))
    ImageDraw.Draw(im).rectangle([430, 60, 590, 990], fill=(60, 60, 90))
    return png(im)


# ---- prompts ------------------------------------------------------------------------------------------------------------
def test_each_close_up_asks_for_its_own_direction_and_keeps_the_art_style(data):
    style, npc = data["styles"]["photoreal"], npc_record()
    prompt = {v: head3d.view_prompt(data["head3d"], v, style, npc)["positive"] for v in head3d.VIEWS}
    assert "facing right" in prompt["side_right"] and "facing left" in prompt["side_left"]
    assert "directly behind" in prompt["back"] and "face is not visible" in prompt["back"]
    assert "straight from the front" in prompt["front"]
    for text in prompt.values():
        assert style["edit_name"] in text and "head and neck only" in text and "white background" in text and "woman" in text
        assert "full body" not in text and "feet" not in text


def test_the_face_is_neutral_by_default_and_the_back_view_has_no_face_to_ask_about(data):
    style, npc = data["styles"]["photoreal"], npc_record()
    text = {v: head3d.view_prompt(data["head3d"], v, style, npc, "neutral")["positive"] for v in head3d.VIEWS}
    for view in ("front", "side_left", "side_right"):
        assert "relaxed neutral expression" in text[view]
    assert "neutral expression" not in text["back"]
    kept = {v: head3d.view_prompt(data["head3d"], v, style, npc, "keep")["positive"] for v in head3d.VIEWS}
    assert all("neutral" not in t for t in kept.values())
    assert head3d.view_prompt(data["head3d"], "front", style, npc, "no such thing")["positive"] == text["front"]   # falls back to neutral


def test_a_neutral_face_rules_out_a_smile_and_an_open_mouth_but_the_portrait_expression_does_not(data):
    style, npc = data["styles"]["photoreal"], npc_record()
    neutral = head3d.view_prompt(data["head3d"], "front", style, npc, "neutral")["negative"]
    kept = head3d.view_prompt(data["head3d"], "front", style, npc, "keep")["negative"]
    assert "open mouth" in neutral and "smile" in neutral
    assert "open mouth" not in kept and "smile" not in kept
    terms = [t.strip() for t in neutral.split(",")]
    assert len(terms) == len(set(terms))                                    # each term once


def test_the_back_close_up_states_the_real_hair_and_keeps_the_hat(data):
    style = data["styles"]["photoreal"]
    back = lambda **kw: head3d.view_prompt(data["head3d"], "back", style, npc_record(**kw))["positive"]
    assert "the hair is black wavy long hair" in back(hair="black wavy long hair") and "same hat or other headwear" in back()
    assert "the head is bald" in back(hair="bald")


def test_freeform_characters_are_not_described_as_people(data):
    npc = {**npc_record(hair="red hair"), "freeform": True}
    for view in head3d.VIEWS:
        text = head3d.view_prompt(data["head3d"], view, data["styles"]["cartoon"], npc)["positive"]
        assert "character" in text and "woman" not in text and "red hair" not in text and "the hair" not in text


def test_the_choices_are_cleaned():
    assert head3d.clean_expression("keep") == "keep" and head3d.clean_expression("smile") == "neutral"
    assert head3d.clean_expression(None) == "neutral"
    assert head3d.clean_quality("high") == "high" and head3d.clean_quality("ultra") == "medium" and head3d.clean_quality(3) == "medium"
    assert [q["id"] for q in head3d.quality_choices()] == ["medium", "high"]


# ---- the reference picture ----------------------------------------------------------------------------------------------
def test_a_standing_figure_is_a_whole_body_picture_and_a_head_and_shoulders_one_is_not():
    mask = lambda data: (np.abs(np.asarray(Image.open(io.BytesIO(data)).convert("RGB"), np.int16) - 235).sum(2) > 30).astype(np.float32)
    assert head3d.is_whole_body(mask(whole_body_picture()))
    assert not head3d.is_whole_body(mask(png(head_picture())))
    assert not head3d.is_whole_body(np.zeros((512, 512), np.float32))                       # no figure at all: leave it alone
    small = np.zeros((1000, 1000), np.float32)
    small[300:700, 400:600] = 1                                                             # a tall narrow figure, but small in the frame
    assert not head3d.is_whole_body(small)


# ---- framing ------------------------------------------------------------------------------------------------------------
def masks_for(pictures):
    return {v: (np.abs(np.asarray(im, np.int16) - 235).sum(2) > 30).astype(np.float32) for v, im in pictures.items()}


def figure_box(raw):
    arr = np.asarray(Image.open(io.BytesIO(raw)).convert("RGB"))
    return arr, sheet.mask_box(arr.max(2) > 45)          # half the block's brightest channel: the resize's soft edge does not count


def test_the_four_views_are_framed_with_one_scale_and_the_figure_on_black():
    # the same head height in every view, but a different width (the front is wider than a profile) and position
    pictures = {"front": head_picture(width=500, top=80, shoulders=False), "side_left": head_picture(width=340, top=120, x=200, shoulders=False),
                "back": head_picture(width=480, top=150, shoulders=False), "side_right": head_picture(width=340, top=90, x=520, shoulders=False)}
    framed, info = head3d.frame(pictures, masks_for(pictures))
    assert set(framed) == set(head3d.VIEWS) and info["height"] == 700 and info["spread"] == 0
    for view, raw in framed.items():
        arr, box = figure_box(raw)
        assert arr.shape == (1024, 1024, 3) and tuple(arr[0, 0]) == (0, 0, 0)
        assert abs((box[3] - box[1]) - 700 * 1024 / info["side"]) <= 3        # one scale: 700 source pixels are the same length everywhere
        assert abs((box[0] + box[2]) / 2 - 512) <= 3 and abs((box[1] + box[3]) / 2 - 512) <= 3     # centred in its own view


def test_views_drawn_at_different_sizes_are_reported_not_stretched_to_match():
    pictures = {"front": head_picture(height=700, shoulders=False), "side_left": head_picture(height=700, shoulders=False),
                "back": head_picture(height=700, shoulders=False), "side_right": head_picture(height=420, shoulders=False)}
    framed, info = head3d.frame(pictures, masks_for(pictures))
    assert info["spread"] > 0.3 and info["views"]["side_right"]["head_height"] == 420
    small, big = figure_box(framed["side_right"])[1], figure_box(framed["front"])[1]
    assert abs((small[3] - small[1]) / (big[3] - big[1]) - 420 / 700) < 0.02   # the shared scale keeps the smaller head smaller


def test_only_the_top_of_each_close_up_is_kept_when_asked_so_the_head_fills_the_frame():
    pictures = {v: head_picture(width=200, top=100, height=500, shoulders=False) for v in head3d.VIEWS}
    whole, info_whole = head3d.frame(pictures, masks_for(pictures))
    cut, info_cut = head3d.frame(pictures, masks_for(pictures), keep=0.5)
    assert info_whole["keep"] == 1.0 and info_cut["keep"] == 0.5 and info_cut["height"] == 250 and info_whole["height"] == 500
    _, box_whole = figure_box(whole["front"])
    _, box_cut = figure_box(cut["front"])
    assert abs((box_cut[3] - box_cut[1]) - (box_whole[3] - box_whole[1])) <= 3        # either way the figure fills the frame's height...
    assert abs((box_cut[2] - box_cut[0]) / (box_whole[2] - box_whole[0]) - 2.0) < 0.05   # ...so what is kept is twice the size in it


def test_the_pipeline_keeps_the_head_and_a_little_neck():
    assert 0.6 <= head3d.KEEP < 1.0


def test_a_view_with_no_head_says_which_one():
    pictures = {v: head_picture() for v in head3d.VIEWS}
    masks = masks_for(pictures)
    masks["back"] = np.zeros_like(masks["back"])
    with pytest.raises(head3d.HeadError, match="back picture"):
        head3d.frame(pictures, masks)


def test_the_mask_graph_uses_birefnet_and_returns_a_picture():
    g = head3d.mask_graph("x.png")
    kinds = {n["class_type"] for n in g.values()}
    assert {"LoadBackgroundRemovalModel", "RemoveBackground", "MaskToImage", "PreviewImage"} <= kinds
    assert g["1"]["inputs"]["bg_removal_name"] == "birefnet.safetensors"


# ---- the Pixal3D graphs -------------------------------------------------------------------------------------------------
IMAGES = {v: f"framed_{v}.png" for v in three_d.PIXAL_SHEET_VIEWS}


def test_the_head_graph_gives_the_framed_pictures_straight_to_the_conditioning():
    g = three_d.pixal3d_head_graph(IMAGES, "npc", "job", 5, quality="medium")
    kinds = [n["class_type"] for n in g.values()]
    for gone in ("RemoveBackground", "ImageCropToMask", "LoadBackgroundRemovalModel", three_d.HEAD_TEXTURE_NODE):
        assert gone not in kinds                                             # framed already; no per-picture crop, no full-body node
    assert kinds.count("LoadImage") == 4 and kinds.count("Pixal3DMultiViewConditioning") == 1
    cond = g["40"]["inputs"]
    assert set(cond) >= {"front", "left", "back", "right"} and cond["fov"] == 20.0
    assert {g[cond[s][0]]["inputs"]["image"] for s in ("front", "left", "back", "right")} == set(IMAGES.values())
    assert g["45"]["inputs"]["positive"] == ["40", 0] and g["54"]["inputs"]["positive"] == ["51", 0]
    assert g["55"]["inputs"]["positive"] == ["54", 0]                        # the texture sampler takes the texture stage's own
    assert g["51"]["inputs"]["target_resolution"] == three_d.HEAD_QUALITIES["medium"]["upsample"]
    assert g["62"]["inputs"]["texture_size"] == three_d.HEAD_QUALITIES["medium"]["texture"]
    assert g["67"]["inputs"]["filename_prefix"] == "npcforge/head-npc-job"


def test_the_high_head_setting_is_finer_and_an_unknown_one_is_medium():
    high = three_d.pixal3d_head_graph(IMAGES, "npc", "job", 5, quality="high")
    medium = three_d.pixal3d_head_graph(IMAGES, "npc", "job", 5, quality="nonsense")
    assert high["51"]["inputs"]["target_resolution"] > medium["51"]["inputs"]["target_resolution"]
    assert high["58"]["inputs"]["resolution"] > medium["58"]["inputs"]["resolution"]
    assert high["62"]["inputs"]["texture_size"] > medium["62"]["inputs"]["texture_size"]
    assert three_d.head_timeout("high") > three_d.head_timeout("medium")


def test_separate_shape_pictures_give_the_shape_its_own_conditioning():
    shape = {v: f"soft_{v}.png" for v in three_d.PIXAL_SHEET_VIEWS}
    g = three_d.pixal3d_head_graph(IMAGES, "npc", "job", 5, shape_images=shape)
    assert g["45"]["inputs"]["positive"] == ["40", 0] and g["54"]["inputs"]["positive"] == ["39", 0]
    loads = lambda node: {g[g[node]["inputs"][s][0]]["inputs"]["image"] for s in ("front", "left", "back", "right")}
    assert loads("40") == set(shape.values()) and loads("39") == set(IMAGES.values())


def test_the_whole_body_graph_keeps_its_own_conditioning_and_head_texture_node():
    heads = {v: f"head_{v}.png" for v in three_d.PIXAL_SHEET_VIEWS}
    g = three_d.pixal3d_graph(IMAGES, heads, 0.3, "npc", "job", 5, quality="medium")
    assert g["40"]["inputs"]["front"] == ["30", 0] and g["30"]["class_type"] == "ImageCropToMask"
    assert g["78"]["class_type"] == three_d.HEAD_TEXTURE_NODE and g["55"]["inputs"]["positive"] == ["78", 0]
    assert g["45"]["inputs"]["positive"] == ["40", 0] and g["51"]["inputs"]["target_resolution"] == 1536


def test_the_detailed_head_needs_pixal3d_and_birefnet_but_not_the_head_texture_node():
    have = {"diffusion_models": ["pixal3d_multiview_int8_convrot.safetensors"], "clip_vision": ["dino_v3_L_naf_fp32.safetensors"],
            "vae": ["trellis_2_shape_vae_bf16.safetensors", "trellis_2_texture_vae_bf16.safetensors"],
            "background_removal": ["birefnet.safetensors"]}

    class Comfy:
        base = "http://test-head-availability"

        def __init__(self, nodes):
            self.nodes = nodes

        def get_json(self, path, timeout=30):
            if path == "/object_info":
                return {n: {} for n in self.nodes}
            return have[path.rsplit("/", 1)[1]]

    nodes = set(three_d.GENERATORS["pixal3d"]["nodes"]) - {three_d.HEAD_TEXTURE_NODE}
    three_d._FILES.clear()
    three_d._NODES.clear()
    assert head3d.availability(Comfy(nodes)) == {"missing": []}              # a ComfyUI that was not restarted still works
    three_d._NODES.clear()
    assert head3d.availability(Comfy(nodes - {"RemeshMesh"}))["missing"] == ["nodes/RemeshMesh"]
    three_d._FILES.clear()
    three_d._NODES.clear()


# ---- clean-up -----------------------------------------------------------------------------------------------------------
def test_loose_specks_go_and_real_separate_parts_stay():
    from test_cleanup3d import combine, make_glb, sphere
    from npcgen.paint3d import Glb
    big = sphere(1.0, rings=16, segments=32, centre=(0, 0, 0))                 # 1024 faces: the head
    part = sphere(0.1, rings=8, segments=16, centre=(3, 0, 0))                 # 256 faces: a separate part (glasses, an eyeball)
    speck = sphere(0.05, rings=4, segments=8, centre=(0, 3, 0))                # 64 faces: a fragment of hair
    pos, tri = combine(big, part, speck)
    report = {}
    out = Glb(head3d.remove_specks(make_glb(pos, tri), report=report))
    kept = out.accessor(out.json["meshes"][0]["primitives"][0]["indices"]).reshape(-1, 3)
    assert len(kept) == 1024 + 256 and report["specks"] == {"faces_before": 1344, "faces_removed": 64, "pieces_removed": 1}
    assert kept.max() < len(big[0]) + len(part[0])                             # the speck's triangles are gone, the vertices stay


def test_a_texture_seam_does_not_make_two_pieces():
    """The textured model's vertices are split along texture seams: the two halves share no vertex numbers, only places."""
    from test_cleanup3d import make_glb, sphere
    from npcgen.paint3d import Glb
    pos, tri = sphere(1.0, rings=8, segments=16, centre=(0, 0, 0))            # 256 faces
    west = pos[tri].mean(1)[:, 0] < 0
    two = np.concatenate([pos, pos])
    seamed = tri.copy()
    seamed[west] += len(pos)                                                    # the west half now uses copies of the vertices
    assert not (set(seamed[west].ravel()) & set(seamed[~west].ravel()))        # by number they are two separate pieces
    report = {}
    out = Glb(head3d.remove_specks(make_glb(two, seamed), min_faces=200, report=report))
    assert report["specks"]["faces_removed"] == 0                               # by place they are one, and 256 is not a speck
    assert len(out.accessor(out.json["meshes"][0]["primitives"][0]["indices"]).reshape(-1, 3)) == len(tri)


def test_a_model_with_no_specks_is_left_as_it_is():
    from test_cleanup3d import make_glb, sphere
    pos, tri = sphere(1.0, rings=8, segments=16)
    report = {}
    head3d.remove_specks(make_glb(pos, tri), report=report)
    assert report["specks"]["faces_removed"] == 0 and report["specks"]["pieces_removed"] == 0


# ---- planning -----------------------------------------------------------------------------------------------------------
def _kinds(steps):
    return [s["kind"] for s in steps]


def test_the_detailed_head_is_never_planned_unless_asked():
    npc = {"assets": {"portrait": {"file": "portrait.png"}, "model_3d": {"file": "model_3d.glb", "textured": True}}}
    assert "head_3d" not in _kinds(Jobs.plan(npc, {"model_3d": True, "refine_3d": True, "rig_3d": True}, {"three_d_model": "pixal3d"}))


def test_the_detailed_head_is_four_close_ups_then_the_model():
    npc = {"assets": {"portrait": {"file": "portrait.png"}}}
    steps = Jobs.plan(npc, {"head_3d": True}, {})
    assert [(s["kind"], s.get("key")) for s in steps] == [("head_view", v) for v in head3d.VIEWS] + [("head_3d", None)]
    assert [s["label"] for s in steps][-1] == "Detailed head model" and steps[0]["label"] == "Head picture: front"


def test_a_character_with_no_portrait_gets_one_first_unless_it_has_a_model_sheet_front():
    steps = Jobs.plan({"assets": {}}, {"head_3d": True}, {})
    assert _kinds(steps)[0] == "portrait" and steps[0]["label"] == "Portrait (needed for the detailed head)"
    with_sheet = {"assets": {"sheet": {"views": {"front": {"file": "sheet/front.png"}}}}}
    assert "portrait" not in _kinds(Jobs.plan(with_sheet, {"head_3d": True}, {}))
    assert Jobs.plan({"assets": {}}, {"head_3d": True, "expressions": ["happy"]}, {})[0]["label"] == "Portrait (needed for expressions)"


def test_the_detailed_head_comes_after_the_body_model_and_its_skeleton():
    npc = {"assets": {"portrait": {"file": "portrait.png"}, "sprite": {"file": "sprite.png", "created": "t"}}}
    kinds = _kinds(Jobs.plan(npc, {"model_3d": True, "rig_3d": True, "head_3d": True}, {"three_d_model": "pixal3d"}))
    assert kinds.index("rig_3d") < kinds.index("head_view") < kinds.index("head_3d")


# ---- requests -----------------------------------------------------------------------------------------------------------
def test_a_request_can_ask_for_the_detailed_head_and_its_options_are_cleaned(data):
    assets = service.clean_assets({"head_3d": True}, data)
    assert assets["head_3d"] is True
    with pytest.raises(ValueError):
        service.clean_assets({"head_3d": False}, data)                        # nothing to make
    cfg = copy.deepcopy(config.DEFAULTS)
    options = service.clean_options({"head_quality": "high", "head_expression": "keep"}, cfg)
    assert options["head_quality"] == "high" and options["head_expression"] == "keep"
    junk = service.clean_options({"head_quality": "x", "head_expression": 7}, cfg)
    assert junk["head_quality"] == "medium" and junk["head_expression"] == "neutral" and junk["head_match_hair"] is False
    assert service.clean_options({"head_match_hair": True}, cfg)["head_match_hair"] is True
    with pytest.raises(ValueError):
        service.clean_options({"head_match_hair": "yes"}, cfg)


def test_one_close_up_can_be_drawn_again_or_the_model_rebuilt_from_the_pictures_it_has(data):
    assert service.clean_head(True) is True and service.clean_head(False) is False and service.clean_head(None) is False
    assert service.clean_head({"views": ["back"], "model": False}) == {"views": ["back"], "model": False}
    assert service.clean_head({"views": [], "model": True}) == {"views": [], "model": True}
    assert service.clean_head({"views": ["side_left", "front", "nonsense"]}) == {"views": ["front", "side_left"], "model": True}
    with pytest.raises(ValueError):
        service.clean_head({"views": [], "model": False})                        # nothing to do
    with pytest.raises(ValueError):
        service.clean_head({"model": "yes"})
    assert service.clean_assets({"head_3d": {"views": ["back"], "model": False}}, data)["head_3d"] == {"views": ["back"], "model": False}


def test_a_part_of_the_detailed_head_is_planned_as_just_that_part():
    npc = {"assets": {"portrait": {"file": "portrait.png"}, "head_3d": {"views": {"front": {"file": "head_3d/front.png"}}}}}
    redo = Jobs.plan(npc, {"head_3d": {"views": ["back"], "model": False}}, {})
    assert [(s["kind"], s.get("key")) for s in redo] == [("head_view", "back")]          # drawn from the front picture it has
    rebuild = Jobs.plan({"assets": {}}, {"head_3d": {"views": [], "model": True}}, {})
    assert [s["kind"] for s in rebuild] == ["head_3d"]                          # from the pictures it has: no portrait needed
    everything = [("head_view", v) for v in head3d.VIEWS] + [("head_3d", None)]
    both = Jobs.plan(npc, {"head_3d": {"views": ["front", "back"], "model": True}}, {})
    assert [(s["kind"], s.get("key")) for s in both] == everything              # a new front picture: new sides and back too


def test_a_new_front_picture_means_new_side_and_back_pictures_and_a_side_needs_a_front_one():
    npc = {"assets": {"portrait": {"file": "portrait.png"}, "head_3d": {"views": {"front": {"file": "head_3d/front.png"}}}}}
    front = Jobs.plan(npc, {"head_3d": {"views": ["front"], "model": False}}, {})
    assert [(s["kind"], s.get("key")) for s in front] == [("head_view", v) for v in head3d.VIEWS]
    bare = {"assets": {"portrait": {"file": "portrait.png"}}}                   # a side picture with no front one to draw it from
    side = Jobs.plan(bare, {"head_3d": {"views": ["side_left"], "model": False}}, {})
    assert [(s["kind"], s.get("key")) for s in side] == [("head_view", "front"), ("head_view", "side_left")]
    nothing = Jobs.plan({"assets": {}}, {"head_3d": {"views": ["back"], "model": False}}, {})
    assert _kinds(nothing)[0] == "portrait"                                     # the front picture it now needs comes from the portrait


# ---- the job steps ------------------------------------------------------------------------------------------------------
class FakeComfy:
    """Records what is sent; a mask is whatever differs from the backdrop of the picture that was uploaded."""

    def __init__(self):
        self.uploads, self.graphs, self.files, self.upload_log = {}, [], {}, []

    def upload_image(self, data, name, image_type="temp", subfolder=None):
        self.uploads[name] = data
        self.upload_log.append(name)
        return f"up/{name}"

    def run(self, graph, cancel=None, timeout=900):
        name = next(n["inputs"]["image"] for n in graph.values() if n["class_type"] == "LoadImage")[3:]
        rgb = np.asarray(Image.open(io.BytesIO(self.uploads[name])).convert("RGB"), np.int16)
        return png(Image.fromarray(((np.abs(rgb - 235).sum(2) > 30) * 255).astype(np.uint8), "L"))

    def run_entry(self, graph, cancel=None, timeout=900, unload_models=False):
        self.graphs.append((graph, timeout))
        return {"outputs": {"67": {"3d": [{"filename": "head.glb", "subfolder": "npcforge", "type": "output"}]},
                            "69": {"images": [{"filename": "head.png", "subfolder": "npcforge", "type": "output"}]}}}

    def fetch_file(self, filename, subfolder="", image_type="temp"):
        return b"GLB original" if filename.endswith(".glb") else png(Image.new("RGB", (8, 8), "white"))


@pytest.fixture()
def head_world(tmp_path, monkeypatch):
    lib = Library(tmp_path / "library")
    npc = lib.create({"setting": "modern", "style": "photoreal", "seed": 3, "appearance": {"hair": "black wavy long hair"},
                      "profile": {"first_name": "Mira", "last_name": "Vale", "age": 30, "gender": "female", "occupation": "tester"}})
    npc_id = npc["id"]
    lib.write_asset(npc_id, "portrait.png", png(head_picture()))

    def seed(n):
        n["assets"]["portrait"] = {"file": "portrait.png", "created": "p1", "size": [1024, 1024]}
    lib.update(npc_id, seed)
    comfy = FakeComfy()
    drawn = []

    def fake_edit(comfy_, engine, refs, prompt, seed, steps, cfg, megapixels=1.0, size=None, negative="", like=None, cancel=None):
        drawn.append({"engine": engine, "refs": refs, "prompt": prompt, "seed": seed, "size": size, "negative": negative,
                      "megapixels": megapixels})
        return png(head_picture(width=300 + 20 * len(drawn))), {}
    monkeypatch.setattr(engines, "edit", fake_edit)

    def fake_inner_skin(model, report=None):
        report["inner_skin"] = {"faces_before": 1000, "faces_removed": 400, "share_removed": 0.4}
        return b"GLB clean"
    monkeypatch.setattr(cleanup3d, "remove_inner_skin", fake_inner_skin)

    def fake_specks(model, min_faces=head3d.SPECK_FACES, report=None):
        report["specks"] = {"faces_before": 600, "faces_removed": 30, "pieces_removed": 5}
        return model
    monkeypatch.setattr(head3d, "remove_specks", fake_specks)
    painted = []

    def fake_paint(model, pictures, masks, report=None, find=None):
        painted.append({"views": sorted(pictures), "shapes": {v: (pictures[v].shape, masks[v].shape) for v in pictures}, "find": find})
        report["views"] = {v: {"outline_match": 0.93, "trust": 1.0} for v in pictures}
        report["textures"] = [{"size": [64, 64], "painted_share": 0.6}]
        return model + b" painted"
    monkeypatch.setattr(headpaint, "paint", fake_paint)
    monkeypatch.setattr(hairmask, "availability", lambda comfy: {"model": "sam", "licence": "x", "missing": []})
    monkeypatch.setattr(hairmask, "finder", lambda comfy, npc_id, cancel=None: "the hair finder")
    cfg = copy.deepcopy(config.DEFAULTS)
    jobs = Jobs(cfg, comfy, lib)

    def job(**options):
        return types.SimpleNamespace(npc_id=npc_id, id="j1", cancel=threading.Event(), cache={}, ref=None,
                                     options={"quality": "standard", "seed": None, "edit_model": "mage", **options})

    def step(kind, key=None, **options):
        j = job(**options)
        jobs._run_step(j, {"kind": kind, "key": key, "label": kind})
        return j
    return types.SimpleNamespace(lib=lib, npc_id=npc_id, comfy=comfy, drawn=drawn, jobs=jobs, job=job, step=step, painted=painted)


def make_all_views(w, **options):
    for view in head3d.VIEWS:
        w.step("head_view", view, **options)


def test_the_front_close_up_is_drawn_from_the_portrait_and_stored_with_what_made_it(head_world):
    w = head_world
    w.step("head_view", "front")
    assert len(w.drawn) == 1 and w.drawn[0]["size"] == (1024, 1024) and "straight from the front" in w.drawn[0]["prompt"]
    assert "relaxed neutral expression" in w.drawn[0]["prompt"] and "open mouth" in w.drawn[0]["negative"]
    assert w.drawn[0]["engine"] == "mage" and w.comfy.uploads["%s_head_reference.png" % w.npc_id]
    meta = w.lib.get(w.npc_id)["assets"]["head_3d"]
    view = meta["views"]["front"]
    assert view["file"] == "head_3d/front.png" and view["from"] == "portrait" and view["expression"] == "neutral"
    assert w.lib.read_asset(w.npc_id, "head_3d/front.png")
    assert meta["source_portrait_created"] == "p1" and "file" not in meta       # pictures only: no model yet


def test_the_side_and_back_close_ups_are_drawn_from_the_front_one_so_all_four_agree(head_world):
    w = head_world
    w.step("head_view", "front")
    j = w.job()
    for view in ("side_left", "back"):
        w.jobs._run_step(j, {"kind": "head_view", "key": view, "label": view})
    assert len(w.drawn) == 3
    assert w.drawn[0]["refs"] == ["up/%s_head_reference.png" % w.npc_id]                                # the front one: the portrait
    assert w.drawn[1]["refs"] == w.drawn[2]["refs"] == ["up/%s_head_front_reference.png" % w.npc_id]  # the others: the front picture
    assert "facing left" in w.drawn[1]["prompt"] and "directly behind" in w.drawn[2]["prompt"]
    assert w.comfy.upload_log.count("%s_head_front_reference.png" % w.npc_id) == 1                     # uploaded once per job
    views = w.lib.get(w.npc_id)["assets"]["head_3d"]["views"]
    assert views["front"]["from"] == "portrait" and views["side_left"]["from"] == views["back"]["from"] == "front close-up"


def test_a_new_front_picture_is_what_the_next_side_picture_is_drawn_from(head_world):
    w = head_world
    j = w.job()
    for view in ("front", "side_left", "front", "back"):
        w.jobs._run_step(j, {"kind": "head_view", "key": view, "label": view})
    assert w.comfy.upload_log.count("%s_head_front_reference.png" % w.npc_id) == 2                     # again after the second front


def test_a_side_picture_needs_the_front_one_to_be_drawn_from(head_world):
    w = head_world
    with pytest.raises(head3d.HeadError, match="front picture first"):
        w.step("head_view", "side_left")


def test_the_portraits_expression_can_be_kept(head_world):
    w = head_world
    w.step("head_view", "front", head_expression="keep")
    assert "neutral" not in w.drawn[0]["prompt"]
    assert w.lib.get(w.npc_id)["assets"]["head_3d"]["views"]["front"]["expression"] == "keep"


def test_the_close_ups_are_drawn_by_the_engine_the_job_chose(head_world):
    w = head_world
    w.step("head_view", "front", edit_model="qwen2511")
    assert w.drawn[0]["engine"] == "qwen2511"


def test_a_portrait_of_the_whole_body_makes_the_model_sheet_front_the_reference(head_world, monkeypatch):
    w = head_world
    w.lib.write_asset(w.npc_id, "portrait.png", whole_body_picture())
    w.lib.write_asset(w.npc_id, "sheet/front.png", b"sheet front")
    w.lib.update(w.npc_id, lambda n: n["assets"].update(sheet={"views": {"front": {"file": "sheet/front.png"}}}))
    seen = {}
    monkeypatch.setattr(headdetail, "pick_upscaler", lambda comfy, wanted=None: "RealESRGAN_x4plus.pth")

    def closeups(comfy, views, npc_id, model, cancel=None):
        seen["views"] = views
        return {"front": {"png": png(head_picture()), "box": [0, 0, 10, 10]}}
    monkeypatch.setattr(headdetail, "closeups", closeups)
    w.step("head_view", "front")
    assert seen["views"] == {"front": b"sheet front"}
    assert "model sheet front" in w.lib.get(w.npc_id)["assets"]["head_3d"]["views"]["front"]["from"]


def test_a_portrait_of_the_whole_body_with_no_sheet_still_works_and_says_so(head_world):
    w = head_world
    w.lib.write_asset(w.npc_id, "portrait.png", whole_body_picture())
    w.step("head_view", "front")
    assert "whole body" in w.lib.get(w.npc_id)["assets"]["head_3d"]["views"]["front"]["from"]


def test_with_no_portrait_and_no_sheet_the_head_says_what_it_needs(head_world):
    w = head_world
    w.lib.update(w.npc_id, lambda n: n["assets"].pop("portrait"))
    with pytest.raises(head3d.HeadError, match="portrait"):
        w.step("head_view", "front")


def test_the_model_needs_all_four_pictures(head_world):
    w = head_world
    w.step("head_view", "front")
    with pytest.raises(head3d.HeadError, match="side left picture"):
        w.step("head_3d")


def test_the_model_step_frames_the_pictures_runs_pixal3d_and_stores_the_head(head_world):
    w = head_world
    make_all_views(w)
    w.step("head_3d", head_quality="medium")
    graph, timeout = w.comfy.graphs[0]
    assert timeout == three_d.head_timeout("medium")
    assert sum(1 for n in graph.values() if n["class_type"] == "LoadImage") == 4
    assert all(name.startswith("up/%s_head_framed_" % w.npc_id) for name in
               (n["inputs"]["image"] for n in graph.values() if n["class_type"] == "LoadImage"))
    for view in head3d.VIEWS:                                                   # what Pixal3D saw is kept
        assert Image.open(io.BytesIO(w.lib.read_asset(w.npc_id, f"head_3d/framed/{view}.png"))).size == (1024, 1024)
    assert w.lib.read_asset(w.npc_id, "head_3d.glb") == b"GLB clean painted"                    # cleaned, then painted from the pictures
    assert w.lib.read_asset(w.npc_id, "head_3d_original.glb") == b"GLB original"                # the generator's own file, as it was
    assert w.lib.read_asset(w.npc_id, "head_3d.png")
    meta = w.lib.get(w.npc_id)["assets"]["head_3d"]
    assert meta["file"] == "head_3d.glb" and meta["raw"] == "head_3d_original.glb" and meta["preview"] == "head_3d.png"
    assert meta["faces_generated"] == 1000 and meta["faces"] == 570 and meta["textured"] is True
    assert meta["inner_skin_removed"] == 400 and meta["specks_removed"] == 30
    assert meta["quality"] == "medium" and meta["quality_label"] == "Medium" and meta["expression"] == "neutral"
    assert meta["reference"] == "portrait" and set(meta["views"]) == set(head3d.VIEWS) and "stale" not in meta
    assert meta["framing"]["height"] > 0 and meta["size"] == len(b"GLB clean painted")
    assert w.painted[0]["views"] == sorted(head3d.VIEWS)                                       # painted from all four framed pictures
    assert w.painted[0]["find"] is None and meta["notes"] == []                                # the hair check is only done when asked
    assert all(shapes == ((1024, 1024, 3), (1024, 1024)) for shapes in w.painted[0]["shapes"].values())
    assert meta["painted"]["views"]["front"]["outline_match"] == 0.93
    assert w.lib.get(w.npc_id)["assets"]["portrait"]["created"] == "p1"          # the portrait itself is untouched


def test_a_head_whose_pictures_do_not_line_up_keeps_pixal3ds_own_colours_and_says_so(head_world, monkeypatch):
    w = head_world

    def refuse(model, pictures, masks, report=None, find=None):
        raise ValueError("None of the four pictures lines up with the head (the outlines overlap by less than 60%)")
    monkeypatch.setattr(headpaint, "paint", refuse)
    make_all_views(w)
    w.step("head_3d")
    meta = w.lib.get(w.npc_id)["assets"]["head_3d"]
    assert w.lib.read_asset(w.npc_id, "head_3d.glb") == b"GLB clean" and meta["painted"] is None
    assert any("Pixal3D's own colours" in n and "lines up" in n for n in meta["notes"])


def test_the_hair_check_is_done_when_asked_and_keeps_skin_off_the_hair(head_world):
    w = head_world
    make_all_views(w)
    w.step("head_3d", head_match_hair=True)
    assert w.painted[0]["find"] == "the hair finder" and w.lib.get(w.npc_id)["assets"]["head_3d"]["notes"] == []


def test_without_sam_the_head_is_painted_anyway_and_says_the_hair_was_not_matched(head_world, monkeypatch):
    w = head_world
    monkeypatch.setattr(hairmask, "availability", lambda comfy: {"model": None, "licence": "x", "missing": ["SAM 3.1 in ComfyUI"]})
    make_all_views(w)
    w.step("head_3d", head_match_hair=True)
    meta = w.lib.get(w.npc_id)["assets"]["head_3d"]
    assert w.painted[0]["find"] is None and w.lib.read_asset(w.npc_id, "head_3d.glb") == b"GLB clean painted"
    assert any("hair was not matched" in n and "SAM 3.1" in n for n in meta["notes"])


def test_the_high_setting_reaches_the_graph(head_world):
    w = head_world
    make_all_views(w)
    w.step("head_3d", head_quality="high")
    graph, timeout = w.comfy.graphs[0]
    assert graph["51"]["inputs"]["target_resolution"] == three_d.HEAD_QUALITIES["high"]["upsample"] and timeout == three_d.head_timeout("high")
    assert w.lib.get(w.npc_id)["assets"]["head_3d"]["quality"] == "high"


def test_pictures_drawn_at_very_different_sizes_leave_a_note(head_world, monkeypatch):
    w = head_world
    sizes = iter([700, 700, 700, 350])

    def fake_edit(*args, **kwargs):
        return png(head_picture(height=next(sizes), shoulders=False)), {}
    monkeypatch.setattr(engines, "edit", fake_edit)
    make_all_views(w)
    w.step("head_3d")
    assert any("different sizes" in n for n in w.lib.get(w.npc_id)["assets"]["head_3d"]["notes"])


def test_new_close_ups_make_an_existing_head_outdated_until_it_is_made_again(head_world):
    w = head_world
    make_all_views(w)
    w.step("head_3d")
    assert "stale" not in w.lib.get(w.npc_id)["assets"]["head_3d"]
    w.step("head_view", "front")
    assert w.lib.get(w.npc_id)["assets"]["head_3d"]["stale"] is True
    w.step("head_3d")
    assert "stale" not in w.lib.get(w.npc_id)["assets"]["head_3d"]


def test_a_new_portrait_or_a_changed_character_outdates_the_head(head_world):
    w = head_world
    make_all_views(w)
    w.step("head_3d")
    w.jobs._mark_derived_stale(w.npc_id)
    assert w.lib.get(w.npc_id)["assets"]["head_3d"]["stale"] is True
    w.step("head_3d")
    npc = w.lib.get(w.npc_id)
    mark_assets_stale(npc)
    assert npc["assets"]["head_3d"]["stale"] is True


def test_deleting_the_head_deletes_its_model_pictures_and_folder(head_world):
    w = head_world
    make_all_views(w)
    w.step("head_3d")
    npc = w.lib.delete_asset(w.npc_id, "head_3d")
    assert "head_3d" not in npc["assets"] and "portrait" in npc["assets"]
    for rel in ("head_3d.glb", "head_3d_original.glb", "head_3d.png", "head_3d"):
        assert not w.lib.resolve(w.npc_id, rel).exists()
    with pytest.raises(KeyError):
        w.lib.delete_asset(w.npc_id, "head_3d")


def test_the_library_says_whether_a_character_has_a_head_model(head_world):
    w = head_world
    make_all_views(w)
    listed = lambda: next(n for n in w.lib.list() if n["id"] == w.npc_id)["has"]["head_3d"]
    assert listed() is False                                                    # pictures alone are not a model
    w.step("head_3d")
    assert listed() is True


def test_an_export_carries_the_head_model_and_its_pictures(head_world):
    w = head_world
    make_all_views(w)
    w.step("head_3d")
    with zipfile.ZipFile(io.BytesIO(w.lib.export_zip([w.npc_id]))) as z:
        names = set(z.namelist())
    assert f"{w.npc_id}/head_3d.glb" in names
    assert all(f"{w.npc_id}/head_3d/{v}.png" in names for v in head3d.VIEWS)
    assert f"{w.npc_id}/head_3d_original.glb" not in names                      # the raw one stays local, like the body model's


def test_the_head_step_only_runs_when_the_job_has_it(head_world):
    w = head_world
    steps = Jobs.plan(w.lib.get(w.npc_id), {"head_3d": True}, {})
    assert [s["kind"] for s in steps][:4] == ["head_view"] * 4
    assert json.dumps(steps)                                                    # plain data: the queue stores and shows it
