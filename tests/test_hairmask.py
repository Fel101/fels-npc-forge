"""Painting a model's hair only from hair in the pictures (npcgen/hairmask.py, paint3d.paint(hair=...))."""
import copy
import io
import threading
import types

import numpy as np
import pytest
from PIL import Image

from npcgen import cleanup3d, config, hairmask, paint3d, roll, service
from npcgen.comfy import ComfyError
from npcgen.jobs import Jobs
from test_cleanup3d import box_with_uv, make_glb, png, world  # noqa: F401  (world is a fixture)

HAIR, SKIN = (30, 20, 10), (200, 20, 20)


def picture():
    """The test box from the front (1 x 2, rows 100-300), its top fifth hair and the rest skin."""
    image = Image.new("RGB", (200, 400), (255, 255, 255))
    image.paste(SKIN, (50, 100, 150, 300))
    image.paste(HAIR, (50, 100, 150, 140))
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def model_with_hair():
    """The test box (1 x 2, facing +Z), its front's top two fifths (the "hair", from 1.2 up) leaning back, so the bare
    shape shows it lighter: it reaches lower than the picture's hair (a generator's longer fringe)."""
    pos, uv, tri = box_with_uv()
    pos = pos.astype(float)
    pos[(pos[:, 2] > 0) & (pos[:, 1] > 1.9), 2] = -0.05         # the front's top edge (and the sides' with it) leans back
    # the front face (corners 0-3: bottom-left, bottom-right, top-right, top-left) gets a fold at the hairline
    base = len(pos)
    pos = np.concatenate([pos, [[-0.5, 1.2, 0.1], [0.5, 1.2, 0.1]]])
    v = 0.99 - 0.6 * 0.98
    uv = np.concatenate([uv, [[0.01, v], [0.24, v]]])
    tri = np.concatenate([[[0, 1, base + 1], [0, base + 1, base], [base, base + 1, 2], [base, 2, 3]], tri[2:]])
    return make_glb(pos, tri, uv, png((128, 128, 128), (256, 256)))


def find(png_bytes, name, what="hair"):
    """A stand-in for SAM: in a picture, the dark pixels; on the bare model, the lighter leaning part (grey 185 against
    the upright front's 175). No ears."""
    rgb = np.asarray(Image.open(io.BytesIO(png_bytes)).convert("RGB")).astype(int)
    if what in ("ear", "eye"):
        return np.zeros(rgb.shape[:2], bool)
    if "model" in name:
        return (rgb[..., 0] >= 180) & (rgb[..., 0] < 250)
    return rgb.sum(-1) < 150


def band(tex):
    """Texels of the front face between the picture's hairline and the model's (model height 1.25-1.55)."""
    return tex[60:95, 10:54].reshape(-1, 3).mean(0)


def test_the_models_hair_takes_no_skin_from_a_picture_whose_hair_ends_higher():
    model = model_with_hair()
    plain = np.asarray(paint3d.Glb(paint3d.paint(model, {"front": picture()})).image(0).convert("RGB")).astype(int)
    report = {}
    matched = np.asarray(paint3d.Glb(paint3d.paint(model, {"front": picture()}, report=report, hair=find))
                         .image(0).convert("RGB")).astype(int)
    assert band(plain)[0] > 150                                   # without: the skin behind the model's hair
    assert band(matched).max() < 60                               # with: the hair's own colour
    below = matched[140:230, 10:54].reshape(-1, 3).mean(0)
    assert below[0] > 150 and below[1] < 60                       # the face below is still painted from the picture
    assert report["hair"]["refused"] > 0 and report["hair"]["views"]["front"]["closeup_zoom"] > 1


def test_skin_on_the_model_still_takes_hair_the_picture_has():
    # a short haircut the shape does not show: the model has no hair, the picture has; the picture's hair is still painted
    pos, uv, tri = box_with_uv()
    model = make_glb(pos, tri, uv, png((128, 128, 128), (256, 256)))
    bald = paint3d.paint(model, {"front": picture()}, hair=lambda png_bytes, name, what="hair": find(png_bytes, name, what) & ("model" not in name))
    top = np.asarray(paint3d.Glb(bald).image(0).convert("RGB")).astype(int)[8:40, 10:54].reshape(-1, 3).mean(0)
    assert top.max() < 60


def test_the_mask_graph_asks_sam_for_hair():
    graph = hairmask.graph("pic.png [temp]")
    assert graph["2"]["inputs"]["ckpt_name"] == hairmask.CHECKPOINT
    assert graph["3"]["inputs"]["text"] == "hair" and graph["4"]["class_type"] == "SAM3_Detect"
    assert hairmask.graph("pic.png [temp]", "ear")["3"]["inputs"]["text"] == "ear"


def test_an_ear_on_the_model_is_never_hair():
    # the whole leaning part is taken for hair, but it is "an ear": it is painted from the picture like any skin
    model = model_with_hair()
    painted = paint3d.paint(model, {"front": picture()},
                            hair=lambda png_bytes, name, what="hair": find(png_bytes, name, "hair" if "model" in name else what))
    tex = np.asarray(paint3d.Glb(painted).image(0).convert("RGB")).astype(int)
    assert band(tex)[0] > 150


def test_without_sam_the_option_says_what_is_missing():
    class Comfy:
        def get_json(self, path, timeout=None):
            return [] if path == "/models/checkpoints" else {}
    missing = hairmask.availability(Comfy())["missing"]
    assert any(hairmask.CHECKPOINT in m for m in missing) and any("SAM3_Detect" in m for m in missing)


def test_the_option_is_planned_only_when_asked():
    npc = {"assets": {"sprite": {"file": "sprite.png", "created": "t"}, "model_3d": {"file": "model_3d.glb", "textured": True}}}
    assert [s.get("key") for s in Jobs.plan(npc, {"refine_3d": True}, {})] == [None]
    assert [s.get("key") for s in Jobs.plan(npc, {"match_hair": True}, {})] == ["hair"]
    assert [s.get("key") for s in Jobs.plan(npc, {"sharp_face": True, "match_hair": True}, {})] == ["face+hair"]
    assert service.clean_assets({"match_hair": True}, roll.load_data())["match_hair"] is True


def _run(lib, npc_id):
    job = types.SimpleNamespace(npc_id=npc_id, cancel=threading.Event(), cache={}, ref=None, options={"quality": "standard", "seed": None})
    Jobs(copy.deepcopy(config.DEFAULTS), None, lib)._run_step(job, {"kind": "refine_3d", "key": "hair", "label": "x"})


def test_the_hair_step_passes_a_finder_to_the_clean_up_and_keeps_what_it_did(world, monkeypatch):  # noqa: F811
    lib, npc_id, _run_kind = world
    got = {}
    monkeypatch.setattr(hairmask, "availability", lambda comfy: {"model": hairmask.CHECKPOINT, "missing": []})
    monkeypatch.setattr(hairmask, "finder", lambda comfy, npc_id, cancel=None: "finder")

    def fake_refine(cfg, model, views, faces=None, cancel=None, closeups=None, eyes=None, hair=None, hands=None):
        got.update(hair=hair, views=sorted(views))
        return b"game", None, {"inner_skin": {"faces_before": 10, "faces_removed": 2}, "game_version": {"faces_after": 8},
                               "paint": {"views": {}, "hair": {"texels": 50, "refused": 7, "views": {}}}}
    monkeypatch.setattr(cleanup3d, "refine", fake_refine)
    _run(lib, npc_id)
    refined = lib.get(npc_id)["assets"]["model_3d"]["refined"]
    assert got == {"hair": "finder", "views": ["back", "front", "side_left", "side_right"]}
    assert refined["hair"]["model"] == hairmask.CHECKPOINT and refined["hair"]["texels_refused"] == 7


def test_without_sam_the_hair_step_stops_with_what_is_missing(world, monkeypatch):  # noqa: F811
    lib, npc_id, _run_kind = world
    monkeypatch.setattr(hairmask, "availability", lambda comfy: {"model": None, "missing": ["SAM 3.1"]})
    with pytest.raises(ComfyError, match="Matching the hair needs SAM 3.1"):
        _run(lib, npc_id)
