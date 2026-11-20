"""Painting a model with no colour of its own (Hunyuan's shapes): the texture layout, the normals, the fill and the plan."""
import copy
import io
import json
import struct
import threading
import types

import numpy as np
import pytest
from PIL import Image

from npcgen import cleanup3d, config, paint3d, unwrap
from npcgen.jobs import ComfyError, Jobs
from test_cleanup3d import combine, sphere, world  # noqa: F401  (world is a fixture)


def bare_glb(pos, tri):
    """A GLB with positions and triangles only, like Hunyuan's SaveGLB output."""
    pos, tri = np.asarray(pos, np.float32), np.asarray(tri, np.uint32)
    blob = pos.tobytes() + tri.tobytes()
    doc = {"asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0}],
           "buffers": [{"byteLength": len(blob)}],
           "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": pos.nbytes},
                           {"buffer": 0, "byteOffset": pos.nbytes, "byteLength": tri.nbytes}],
           "accessors": [{"bufferView": 0, "componentType": 5126, "count": len(pos), "type": "VEC3",
                          "min": pos.min(0).tolist(), "max": pos.max(0).tolist()},
                         {"bufferView": 1, "componentType": 5125, "count": int(tri.size), "type": "SCALAR"}],
           "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "indices": 1}]}]}
    text = json.dumps(doc).encode()
    text += b" " * (-len(text) % 4)
    return (struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(text) + 8 + len(blob)) + struct.pack("<II", len(text), 0x4E4F534A)
            + text + struct.pack("<II", len(blob), 0x004E4942) + blob)


def figure():
    """A body and a head: two spheres standing on y=0."""
    return combine(sphere(0.3, centre=(0, 0.6, 0), rings=16, segments=32), sphere(0.15, centre=(0, 1.2, 0), rings=16, segments=32))


def test_an_untextured_model_gets_a_texture_layout_with_the_head_on_its_own_half():
    pos, tri = figure()
    model = bare_glb(pos, tri)
    assert unwrap.needs_texture(model)
    report = {}
    out = unwrap.unwrap(model, report)
    assert not unwrap.needs_texture(out)
    (p, nrm, uv, t, _image), = paint3d.textured_primitives(paint3d.Glb(out))
    assert uv.min() >= 0 and uv.max() <= 1
    top, height = p[:, 1].max(), np.ptp(p[:, 1])
    head = p[t].mean(1)[:, 1] > top - unwrap.LAYOUT_HEAD_FROM * height
    assert (uv[t[head]][..., 0] >= 0.5).all() and (uv[t[~head]][..., 0] <= 0.5).all()
    assert paint3d.Glb(out).image(0).size == (2 * unwrap.ATLAS_RESOLUTION, unwrap.ATLAS_RESOLUTION)
    centre = np.where(p[:, 1:2] > 1.0, [0, 1.2, 0], [0, 0.6, 0])
    assert (np.einsum("ij,ij->i", nrm, p - centre) > 0).all()              # normals point out of each sphere
    assert report["unwrap"]["head_texels_per_unit"] > report["unwrap"]["body_texels_per_unit"]


def test_an_inside_out_model_is_turned_the_right_way():
    pos, tri = figure()
    report = {}
    out = unwrap.unwrap(bare_glb(pos, tri[:, ::-1]), report)
    (p, nrm, _uv, t, _image), = paint3d.textured_primitives(paint3d.Glb(out))
    face = np.cross(p[t[:, 1]] - p[t[:, 0]], p[t[:, 2]] - p[t[:, 0]])
    assert report["unwrap"]["turned_inside_out"]
    outward = np.einsum("ij,ij->i", face, p[t].mean(1) - np.where(p[t].mean(1)[:, 1:2] > 1.0, [0, 1.2, 0], [0, 0.6, 0]))
    assert (outward >= 0).all() and (outward > 0).mean() > 0.9           # (the spheres' pole triangles have no area)


def test_where_two_sheets_fold_back_on_each_other_no_corner_normal_faces_away():
    # a big face and a small one sharing an edge, the small one folded back over the big one: the shared vertices' plain
    # normal follows the big face and faces away from the small one (the black slivers on Hunyuan's hair)
    a = np.radians(10)
    pos = np.array([[0, 0, 0], [0, 1, 0], [3, 0.5, 0], [np.cos(a) * 0.3, 0.5, np.sin(a) * 0.3]])
    tri = np.array([[0, 2, 1], [0, 1, 3]])
    plain = paint3d._vertex_normals(pos, tri)
    face = np.cross(pos[tri[:, 1]] - pos[tri[:, 0]], pos[tri[:, 2]] - pos[tri[:, 0]])
    assert (np.einsum("kd,d->k", plain[tri[1]], face[1]) < 0).any()
    corners = unwrap.corner_normals(pos, tri)
    assert (np.einsum("nkd,nd->nk", corners, face) > 0).all()


def test_what_no_picture_sees_takes_the_painted_colour_not_the_blank_grey():
    pos, tri = sphere(0.5, centre=(0, 0.5, 0), rings=24, segments=48)
    model = unwrap.unwrap(bare_glb(pos, tri))
    pictures = {}
    for view, colour in (("front", (200, 30, 30)), ("back", (30, 30, 200))):
        pic = Image.new("RGB", (300, 300), (255, 255, 255))
        pic_draw = np.asarray(pic).copy()
        yy, xx = np.mgrid[:300, :300]
        pic_draw[(xx - 150) ** 2 + (yy - 150) ** 2 < 100 ** 2] = colour
        buf = io.BytesIO()
        Image.fromarray(pic_draw).save(buf, "PNG")
        pictures[view] = buf.getvalue()
    painted = paint3d.Glb(paint3d.paint(model, pictures, blank=True))
    (p, _n, uv, t, _image), = paint3d.textured_primitives(painted)
    tex = np.asarray(painted.image(0).convert("RGB")).astype(int)
    at = uv[t].mean(1) * [tex.shape[1], tex.shape[0]]
    colour = tex[at[:, 1].astype(int), at[:, 0].astype(int)]
    assert (np.abs(colour - unwrap.BLANK).max(1) > 40).all()               # every face painted or filled
    side = np.abs(p[t].mean(1)[:, 0]) > 0.45                                # the sides, edge-on to both pictures
    assert side.any() and ((colour[side, 0] > 150) | (colour[side, 2] > 150)).all()


# ---- the plan and the job step --------------------------------------------------------------------------------------------

def _steps(npc, assets, options=None):
    return [(s["kind"], s.get("key")) for s in Jobs.plan(npc, assets, options or {})]


def test_painting_a_hunyuan_model_makes_its_missing_sheet_pictures_first():
    npc = {"assets": {"sprite": {"file": "sprite.png", "created": "t"}, "model_3d": {"file": "model_3d.glb", "engine_id": "hunyuan21"},
                      "sheet": {"views": {"front": {"file": "sheet/front.png", "source_sprite_created": "t"}}}}}
    assert _steps(npc, {"refine_3d": True}) == [("sheet", "side_left"), ("sheet", "back"), ("sheet", "side_right"), ("refine_3d", None)]


def test_a_textured_model_is_cleaned_up_without_new_sheet_pictures():
    npc = {"assets": {"sprite": {"file": "sprite.png", "created": "t"}, "model_3d": {"file": "model_3d.glb", "textured": True}}}
    assert _steps(npc, {"refine_3d": True}) == [("refine_3d", None)]


def test_a_new_hunyuan_model_painted_in_the_same_request_gets_its_pictures_before_the_model():
    npc = {"assets": {"sprite": {"file": "sprite.png", "created": "t"}}}
    steps = _steps(npc, {"model_3d": True, "refine_3d": True}, {"three_d_model": "hunyuan21"})
    assert steps[-2:] == [("model_3d", None), ("refine_3d", None)]
    assert [k for kind, k in steps if kind == "sheet"] == ["front", "side_left", "back", "side_right"]


def test_without_its_pictures_an_untextured_model_is_not_cleaned_up(world, monkeypatch):  # noqa: F811
    lib, npc_id, run = world
    lib.update(npc_id, lambda n: (n["assets"]["model_3d"].update(textured=False, engine_id="hunyuan21"),
                                  n["assets"]["sheet"]["views"].pop("back")))
    monkeypatch.setattr(cleanup3d, "refine", lambda *a, **k: pytest.fail("should not clean up"))
    with pytest.raises(ComfyError, match="back picture"):
        run("refine_3d")


def test_an_untextured_model_is_marked_as_given_its_own_layout(world, monkeypatch):  # noqa: F811
    lib, npc_id, run = world
    lib.update(npc_id, lambda n: n["assets"]["model_3d"].update(textured=False, engine_id="hunyuan21"))
    monkeypatch.setattr(cleanup3d, "refine", lambda cfg, model, views, faces=None, cancel=None, closeups=None, eyes=None, hair=None, hands=None: (
        b"game", None, {"inner_skin": {"faces_before": 10, "faces_removed": 0}, "unwrap": {"head_texels_per_unit": 2000},
                        "game_version": {"faces_after": 10}, "paint": {"views": {}}}))
    refined = run("refine_3d")["model_3d"]["refined"]
    assert refined["unwrapped"] is True and refined["painted_from"] == ["back", "front", "side_left", "side_right"]


def test_refine_unwraps_a_bare_model_and_paints_it_from_blank(monkeypatch):
    calls = []
    monkeypatch.setattr(unwrap, "unwrap", lambda model, report: (calls.append("unwrap"), report.update(unwrap={}), b"unwrapped")[2])
    monkeypatch.setattr(unwrap, "needs_texture", lambda model: True)
    monkeypatch.setattr(paint3d, "paint", lambda model, views, report=None, closeups=None, blank=False, hair=None, hands=None, hand_pixels=None: (
        calls.append(("paint", model, blank)), b"painted")[1])
    monkeypatch.setattr(cleanup3d, "remove_inner_skin", lambda model, report: (report.update(inner_skin={}), model)[1])

    def lod(cfg, script, args, tmp, out, *a, **k):
        out.write_bytes(b"game")
    monkeypatch.setattr(cleanup3d.rig3d, "run_script", lod)
    glb, _poster, _report = cleanup3d.refine({}, b"bare", {"front": b"x"})
    assert calls == ["unwrap", ("paint", b"unwrapped", True)] and glb == b"game"
    with pytest.raises(unwrap.UnwrapError):
        cleanup3d.refine({}, b"bare", None)


def test_hunyuan_from_the_model_sheet_makes_the_front_picture_first():
    npc = {"assets": {"sprite": {"file": "sprite.png", "created": "t"}}}
    steps = _steps(npc, {"model_3d": True}, {"three_d_model": "hunyuan21", "three_d_hunyuan_source": "turnaround"})
    assert steps == [("sheet", "front"), ("model_3d", None)]
    assert _steps(npc, {"model_3d": True}, {"three_d_model": "hunyuan21"}) == [("model_3d", None)]


def test_a_sheet_picture_is_cut_out_for_hunyuan():
    from npcgen import three_d
    pic = Image.new("RGB", (100, 200), (255, 255, 255))
    pic.paste((90, 60, 40), (30, 40, 70, 180))
    buf = io.BytesIO()
    pic.save(buf, "PNG")
    alpha = np.asarray(Image.open(io.BytesIO(three_d.cut_out(buf.getvalue()))).getchannel("A"))
    assert alpha[100, 50] == 255 and alpha[5, 5] == 0


def test_hunyuan_detail_sets_the_shape_grid_and_old_recipes_keep_the_tested_one():
    from npcgen import service, three_d
    octree = lambda detail: three_d.graph("in.png", "npc", "job", 1, detail=detail)["8"]["inputs"]["octree_resolution"]
    assert octree("standard") == 256 and octree("fine") == 384 and octree("nonsense") == 256
    assert three_d.clean_hunyuan_detail(None) == "standard"
    assert service.clean_options({}, config.load())["three_d_hunyuan_detail"] == "standard"
    assert service.clean_options({"three_d_hunyuan_detail": "fine"}, config.load())["three_d_hunyuan_detail"] == "fine"
