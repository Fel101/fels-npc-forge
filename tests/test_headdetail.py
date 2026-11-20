"""The sharper face: head close-ups, the head's own texture layout, painting from close-ups, and the job step."""
import io

import numpy as np
import pytest
from PIL import Image

from npcgen import cleanup3d, headdetail, paint3d
from npcgen.jobs import Jobs
from test_cleanup3d import box_with_uv, combine, make_glb, sphere, world  # noqa: F401  (world is a fixture)


def picture_png(im):
    out = io.BytesIO()
    im.save(out, "PNG")
    return out.getvalue()


def figure():
    """A turnaround-like view: a white panel with a 40-pixel-wide head on top of a wider body, 800 pixels tall."""
    im = Image.new("RGB", (576, 1152), (255, 255, 255))
    im.paste((90, 60, 40), (268, 100, 308, 220))        # head
    im.paste((40, 60, 120), (218, 220, 358, 900))       # body
    return picture_png(im)


# ---- close-ups --------------------------------------------------------------------------------------------------------

def test_the_head_box_is_a_square_around_the_top_of_the_figure():
    x0, y0, x1, y1 = headdetail.head_box(figure())
    assert x1 - x0 == y1 - y0 == 192                     # a quarter of the 800-pixel figure, in steps of 16
    assert y0 < 100 < y1 and x0 < 268 and 308 < x1       # the whole head, with a margin above it
    assert abs((x0 + x1) / 2 - 288) <= 1                 # centred on the head, not on the wider body


def test_a_crop_past_the_edge_of_the_picture_is_filled_with_the_backdrop():
    im = Image.open(io.BytesIO(headdetail.crop(figure(), (-20, -20, 60, 60)))).convert("RGB")
    assert im.size == (80, 80) and im.getpixel((0, 0)) == (255, 255, 255)


def test_without_an_upscale_model_the_sharper_face_says_what_is_missing():
    class Comfy:
        def get_json(self, path, timeout=30):
            return ["some-2x-model.pth"]
    assert headdetail.availability(Comfy())["upscaler"] is None
    assert any("upscale model" in m for m in headdetail.availability(Comfy())["missing"])
    with pytest.raises(headdetail.HeadDetailError):
        headdetail.pick_upscaler(Comfy())
    assert headdetail.pick_upscaler(Comfy(), "some-2x-model.pth") == "some-2x-model.pth"   # config.json's choice


# ---- the head's own texture layout --------------------------------------------------------------------------------------

def gradient(size=(128, 128)):
    y, x = np.mgrid[0:size[1], 0:size[0]]
    rgb = np.stack([x * 255 // size[0], y * 255 // size[1], np.full_like(x, 90)], -1).astype(np.uint8)
    return picture_png(Image.fromarray(rgb, "RGB"))


def body_and_head():
    """A tall body with a round head on top; the generator's layout is a front projection into the whole texture."""
    pos, tri = combine(sphere(0.3, centre=(0, 0.8, 0), rings=16, segments=32), sphere(0.12, centre=(0, 1.3, 0), rings=16, segments=32))
    uv = np.stack([(pos[:, 0] + 0.4) / 0.8, (1.5 - pos[:, 1]) / 1.1], 1)
    return make_glb(pos, tri, uv, gradient())


xatlas = pytest.importorskip("xatlas")


def test_the_head_gets_the_right_half_of_a_twice_as_wide_texture():
    model = body_and_head()
    report = {}
    glb = paint3d.Glb(headdetail.head_layout(model, report))
    pos, _nrm, uv, tri, image = paint3d.textured_primitives(glb)[0]
    prim = glb.json["meshes"][0]["primitives"][0]
    old_uv = glb.accessor(prim["attributes"]["TEXCOORD_1"])
    top = pos[:, 1].max()
    head = pos[tri].mean(1)[:, 1] > top - headdetail.LAYOUT_HEAD_FROM * (top - pos[:, 1].min())
    assert (uv[tri[head]][..., 0] >= 0.5 - 1e-6).all() and (uv[tri[~head]][..., 0] <= 0.5 + 1e-6).all()
    new_tex = glb.image(image)
    assert new_tex.size == (256, 128)
    original = np.asarray(paint3d.Glb(model).image(0).convert("RGB"))
    assert (np.asarray(new_tex.convert("RGB"))[:, :128] == original).all()        # the body's texture is untouched
    # each head vertex reads, in its new place, the colour the generator's layout gave that point
    verts = np.unique(tri[head])
    new = paint3d._bilinear(np.asarray(new_tex.convert("RGB")).astype(float), uv[verts, 0] * 256, uv[verts, 1] * 128)
    old = paint3d._bilinear(original.astype(float), old_uv[verts, 0] * 128, old_uv[verts, 1] * 128)
    assert np.abs(new - old).mean() < 6
    layout = report["head_layout"]
    assert layout["head_texels_per_unit"] > 1.5 * layout["head_texels_per_unit_before"]


def test_a_model_with_a_head_layout_can_be_painted_and_laid_out_only_once():
    laid_out = headdetail.head_layout(body_and_head())
    painted = paint3d.paint(laid_out, {"front": figure()})
    assert paint3d.Glb(painted).image(0).size == (256, 128)
    with pytest.raises(headdetail.HeadDetailError):
        headdetail.head_layout(laid_out)


# ---- painting from close-ups ----------------------------------------------------------------------------------------------

def test_a_close_up_outweighs_its_own_view_where_it_sees():
    pos, uv, tri = box_with_uv()
    model = make_glb(pos, tri, uv, picture_png(Image.new("RGB", (256, 256), (128, 128, 128))))
    view = Image.new("RGB", (200, 400), (255, 255, 255))
    view.paste((200, 20, 20), (50, 100, 150, 300))          # the box seen from the front: 1 x 2, red
    close = view.crop((25, 75, 125, 175)).resize((400, 400), Image.NEAREST)
    close.paste((20, 20, 200), (100, 100, 400, 400))        # the close-up shows the same place in blue
    report = {}
    painted = paint3d.Glb(paint3d.paint(model, {"front": picture_png(view)}, report=report,
                                        closeups={"front": {"png": picture_png(close), "box": [25, 75, 125, 175]}}))
    tex = np.asarray(painted.image(0).convert("RGB")).astype(int)
    assert report["closeups"]["front"]["zoom"] == 4 and report["closeups"]["front"]["texels"] > 0
    upper = tex[50:56, 18:22].reshape(-1, 3).mean(0)        # x -0.2, y 1.6: inside the close-up
    lower = tex[188:194, 18:22].reshape(-1, 3).mean(0)      # y 0.5: only the view sees it
    assert upper[2] > 150 and upper[0] < 80
    assert lower[0] > 150 and lower[2] < 80


# ---- the job step ---------------------------------------------------------------------------------------------------------

def test_the_sharper_face_is_never_planned_unless_asked():
    npc = {"assets": {"sprite": {"file": "sprite.png", "created": "t"}, "model_3d": {"file": "model_3d.glb", "textured": True}}}
    assert [s.get("key") for s in Jobs.plan(npc, {"refine_3d": True}, {})] == [None]
    assert [(s["kind"], s.get("key")) for s in Jobs.plan(npc, {"sharp_face": True}, {})] == [("refine_3d", "face")]


def test_the_sharper_face_step_makes_close_ups_and_paints_with_them(world, monkeypatch):  # noqa: F811
    lib, npc_id, run = world
    got = {}
    monkeypatch.setattr(headdetail, "pick_upscaler", lambda comfy, wanted=None: "4x-UltraSharp.pth")
    monkeypatch.setattr(headdetail, "closeups", lambda comfy, views, npc_id, model, cancel=None:
                        {v: {"png": b"close " + v.encode(), "box": [0, 0, 10, 10]} for v in views})

    def fake_refine(cfg, model, views, faces=None, cancel=None, closeups=None, eyes=None, hair=None, hands=None):
        got["closeups"] = closeups
        return b"game", None, {"inner_skin": {"faces_before": 10, "faces_removed": 2}, "game_version": {"faces_after": 8},
                               "paint": {"views": {}}, "head_layout": {"head_texels_per_unit_before": 1300, "head_texels_per_unit": 3100}}
    monkeypatch.setattr(cleanup3d, "refine", fake_refine)
    job_step = {"kind": "refine_3d", "key": "face", "label": "x"}
    import threading
    import types
    job = types.SimpleNamespace(npc_id=npc_id, cancel=threading.Event(), cache={}, ref=None, options={"quality": "standard", "seed": None})
    from npcgen import config
    import copy
    Jobs(copy.deepcopy(config.DEFAULTS), None, lib)._run_step(job, job_step)
    face = lib.get(npc_id)["assets"]["model_3d"]["refined"]["face"]
    assert sorted(got["closeups"]) == ["back", "front", "side_left", "side_right"]
    assert lib.read_asset(npc_id, "model_3d_head/front.png") == b"close front"
    assert face["upscaler"] == "4x-UltraSharp.pth" and "non-commercial" in face["licence"] and face["texels"] == 3100

    run("refine_3d")                                         # done again without it: the close-ups go
    assert not lib.resolve(npc_id, "model_3d_head").exists()
    assert "face" not in lib.get(npc_id)["assets"]["model_3d"]["refined"]


def test_deleting_the_model_deletes_its_close_ups(world):  # noqa: F811
    lib, npc_id, run = world
    lib.write_asset(npc_id, "model_3d_head/front.png", b"close")
    lib.delete_asset(npc_id, "model_3d")
    assert not lib.resolve(npc_id, "model_3d_head").exists()
