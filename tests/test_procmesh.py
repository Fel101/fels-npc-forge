"""The mesh kernel recipes are built with (npcgen/procmesh.py): closed outward-facing surfaces, exact normals, and a valid GLB."""
import io
import json
import math
import struct

import numpy as np
import pytest
from PIL import Image

from npcgen import procmesh as pm
from npcgen.paint3d import Glb


def _line(length=40.0, count=30):
    return np.stack([np.zeros(count), np.zeros(count), np.linspace(0, length, count)], 1)


def _torus(R=20.0, r=1.5, n=160, around=32):
    angle = np.arange(n) * 2 * np.pi / n
    path = np.stack([R * np.cos(angle), R * np.sin(angle), np.zeros(n)], 1)
    out = np.stack([np.cos(angle), np.sin(angle), np.zeros(n)], 1)
    prof, normals = pm.profile_ellipse(around)
    return pm.sweep(path, np.stack([out, np.tile([0.0, 0.0, 1.0], (n, 1))], 1), prof, normals, scales=np.full((n, 2), 2 * r), closed=True)


def test_a_closed_sweep_is_a_closed_surface_with_the_volume_of_a_torus():
    mesh = _torus()
    assert mesh.volume() == pytest.approx(2 * math.pi ** 2 * 20.0 * 1.5 ** 2, rel=0.02)         # (the volume is positive only if it faces out)
    assert mesh.facing() == 1.0 and np.isfinite(mesh.pos).all()


@pytest.mark.parametrize("caps", [("dome", "dome"), ("flat", "flat"), ("flat", "dome")])
def test_an_open_sweep_is_capped_at_both_ends_so_nothing_is_left_open(caps):
    path = _line()
    prof, normals = pm.profile_rounded_rect(0.3, 3)
    mesh = pm.sweep(path, pm.planar_frames(path, [1, 0, 0]), prof, normals, scales=np.tile([5.0, 3.0], (len(path), 1)), caps=caps)
    flat_volume = 5.0 * 3.0 * 40.0
    assert 0.9 * flat_volume < mesh.volume() < 1.1 * flat_volume and mesh.facing() == 1.0
    low, high = mesh.bounds()
    if caps == ("flat", "flat"):
        assert low[2] == pytest.approx(0.0) and high[2] == pytest.approx(40.0)              # cut square exactly at the ends of the path
    else:
        assert low[2] <= 0.0 and high[2] >= 40.0


def test_a_sweep_without_caps_is_left_open_when_asked():
    path = _line()
    prof, normals = pm.profile_ellipse(12)
    open_tube = pm.sweep(path, pm.round_frames(path), prof, normals, caps=("open", "open"))
    closed_tube = pm.sweep(path, pm.round_frames(path), prof, normals)
    assert open_tube.faces < closed_tube.faces


def test_a_taper_changes_the_size_along_the_path():
    path = _line(count=20)
    prof, normals = pm.profile_ellipse(16)
    scales = np.stack([np.linspace(4.0, 1.0, 20)] * 2, 1)
    mesh = pm.sweep(path, pm.round_frames(path), prof, normals, scales=scales, caps=("flat", "flat"))
    near, far = mesh.pos[mesh.pos[:, 2] < 1.0], mesh.pos[mesh.pos[:, 2] > 39.0]
    assert np.ptp(near[:, 0]) == pytest.approx(4.0, rel=0.05) and np.ptp(far[:, 0]) == pytest.approx(1.0, rel=0.05)


def test_profile_normals_are_unit_and_point_out_and_a_circle_is_a_circle():
    for prof, normals in (pm.profile_rounded_rect(0.3, 4), pm.profile_rounded_rect(1.0, 6), pm.profile_ellipse(12)):
        assert np.allclose(np.linalg.norm(normals, axis=1), 1.0)
        assert (np.einsum("ij,ij->i", prof, normals) > 0).all()                               # (each normal leaves the middle)
    circle, _n = pm.profile_rounded_rect(1.0, 8)
    assert np.allclose(np.linalg.norm(circle, axis=1), 0.5, atol=1e-9)


def test_planar_frames_are_square_to_the_path_and_to_each_other():
    angle = np.linspace(0, 1.2, 25)
    path = np.stack([np.zeros(25), -30 * (1 - np.cos(angle)), -30 * np.sin(angle)], 1)          # an arm bending down in a vertical plane
    frames = pm.planar_frames(path, [1, 0, 0])
    t = pm.tangents(path)
    u, v = frames[:, 0], frames[:, 1]
    assert np.allclose(np.einsum("ij,ij->i", u, t), 0, atol=1e-9) and np.allclose(np.einsum("ij,ij->i", v, t), 0, atol=1e-9)
    assert np.allclose(np.einsum("ij,ij->i", u, v), 0, atol=1e-9) and np.allclose(np.linalg.norm(v, axis=1), 1)


def test_round_frames_work_for_a_path_that_runs_straight_up_or_any_way():
    for direction in ([0, 1, 0], [0, 0, 1], [1, 1, 1]):
        path = np.outer(np.linspace(0, 10, 8), direction)
        f = pm.round_frames(path)
        t = pm.tangents(path)
        assert np.isfinite(f).all() and np.allclose(np.einsum("ij,ij->i", f[:, 0], t), 0, atol=1e-9)


def test_an_ellipsoid_and_a_cylinder_have_the_volumes_of_their_shapes():
    ball = pm.ellipsoid([1, 2, 3], np.diag([4.0, 2.0, 1.0]), around=24, rings=12)
    assert ball.volume() == pytest.approx(4 / 3 * math.pi * 8, rel=0.04) and ball.facing() == 1.0
    assert np.allclose(ball.bounds()[0], [-3, 0, 2], atol=0.05)
    rod = pm.cylinder([0, 0, 0], [0, 0, 10], 2.0, 32)
    assert rod.volume() == pytest.approx(math.pi * 4 * 10, rel=0.02) and rod.facing() == 1.0


def _lens(curve_radius=88.0, thickness=2.0, rings=4):
    t = np.linspace(0, 2 * np.pi, 96, endpoint=False)
    outline = np.stack([25 * np.cos(t), 21 * np.sin(t)], 1)

    def surface(x, y):
        s = np.sqrt(curve_radius ** 2 - x ** 2 - y ** 2)
        return s - curve_radius, np.stack([x, y, s], 1) / curve_radius
    return pm.lens_slab(outline, surface, thickness, rings=rings)


def test_a_lens_is_a_closed_slab_with_a_curve_and_a_thickness():
    lens = _lens()
    low, high = lens.bounds()
    sag = 88.0 - math.sqrt(88.0 ** 2 - 25 ** 2)
    assert high[2] == pytest.approx(0.0, abs=1e-9) and low[2] == pytest.approx(-sag - 2.0, abs=0.05)          # the middle is highest; the edge sags
    assert lens.volume() == pytest.approx(math.pi * 25 * 21 * 2.0, rel=0.02) and lens.facing() == 1.0
    assert np.allclose(np.linalg.norm(lens.nrm, axis=1), 1.0)
    front = lens.nrm[lens.pos[:, 2] > -0.5]
    assert (front[:, 2] > 0).all()                                                          # (the front surface faces forward)


def test_a_mirrored_part_still_faces_outward_and_a_transform_moves_and_scales_it():
    mesh = _torus(n=64, around=12).translated([5, 0, 0])
    mirrored = mesh.mirrored_x()
    assert mirrored.volume() > 0 and mirrored.facing() == 1.0 and np.allclose(mirrored.bounds()[1][0], -mesh.bounds()[0][0])
    big = mesh.transformed(np.diag([2.0, 2.0, 2.0, 1.0]))
    assert big.volume() == pytest.approx(8 * mesh.volume(), rel=1e-6)


def test_merge_keeps_every_part_and_gives_white_to_a_part_with_no_colour():
    a, b = pm.cylinder([0, 0, 0], [0, 0, 1], 1.0, 8), pm.cylinder([5, 0, 0], [5, 0, 1], 1.0, 8)
    both = pm.merge([a, b.with_colours(lambda p: np.tile([0.2, 0.4, 0.6, 0.8], (len(p), 1)))])
    assert both.faces == a.faces + b.faces and both.col.shape == (len(both.pos), 4)
    assert (both.col[: len(a.pos)] == 1.0).all() and both.col[-1][3] == pytest.approx(0.8)
    assert pm.merge([]).faces == 0 and pm.merge([None, a]).faces == a.faces


def _parse_glb(data):
    glb = Glb(data)
    assert struct.unpack_from("<I", data, 8)[0] == len(data) and len(data) % 4 == 0
    return glb


def test_the_glb_is_valid_with_bounds_normals_materials_and_a_node_tree():
    g = pm.Gltf()
    steel = g.material("steel", (0.8, 0.8, 0.82), metallic=1.0, roughness=0.2)
    glass = g.material("glass", (0.2, 0.3, 0.4), opacity=0.3)
    body = pm.cylinder([0, 0, 0], [0, 0, 0.05], 0.01, 12)
    part = g.mesh("part", [(body, steel), (pm.cylinder([0, 0, 0.1], [0, 0, 0.12], 0.01, 12), glass), (None, steel)])
    leaf = g.node("leaf", part, translation=[0.1, 0, 0], rotation=pm.yaw_quaternion(90))
    root = g.node("root", children=[leaf])
    glb = _parse_glb(g.dumps([root], extras={"recipe": {"a": 1}}))
    doc = glb.json
    assert [n["name"] for n in doc["nodes"]] == ["leaf", "root"] and doc["scenes"][0]["nodes"] == [1] and doc["nodes"][1]["children"] == [0]
    assert doc["asset"]["extras"] == {"recipe": {"a": 1}} and doc["nodes"][0]["rotation"][1] == pytest.approx(math.sin(math.pi / 4))
    prims = doc["meshes"][0]["primitives"]
    assert len(prims) == 2 and prims[0]["material"] == steel and prims[1]["material"] == glass          # the empty part was left out
    for prim in prims:
        pos = glb.accessor(prim["attributes"]["POSITION"])
        acc = doc["accessors"][prim["attributes"]["POSITION"]]
        assert np.allclose(acc["min"], pos.min(0)) and np.allclose(acc["max"], pos.max(0))              # (glTF requires them)
        nrm = glb.accessor(prim["attributes"]["NORMAL"])
        assert len(nrm) == len(pos) and np.allclose(np.linalg.norm(nrm, axis=1), 1.0, atol=1e-5)
        assert glb.accessor(prim["indices"]).max() < len(pos)
    assert doc["materials"][glass]["alphaMode"] == "BLEND" and "alphaMode" not in doc["materials"][steel]
    assert doc["materials"][steel]["pbrMetallicRoughness"]["metallicFactor"] == 1.0


def test_colours_are_converted_from_srgb_to_linear_as_glb_wants():
    g = pm.Gltf()
    g.material("a", (1.0, 0.5, 0.0))
    base = g.doc["materials"][0]["pbrMetallicRoughness"]["baseColorFactor"]
    assert base[0] == pytest.approx(1.0) and base[1] == pytest.approx(0.2140, abs=1e-3) and base[2] == 0.0 and base[3] == 1.0


def test_a_big_part_uses_32_bit_indices_and_a_small_one_16():
    g = pm.Gltf()
    m = g.material("m", (1, 1, 1))
    small = pm.cylinder([0, 0, 0], [0, 0, 1], 1.0, 8)
    count = 70000
    big = pm.Mesh(np.random.default_rng(1).random((count, 3)), np.tile([0.0, 0.0, 1.0], (count, 1)), [(0, 1, 2), (3, 4, count - 1)])
    g.mesh("small", [(small, m)])
    g.mesh("big", [(big, m)])
    kinds = [g.doc["accessors"][p["indices"]]["componentType"] for mesh in g.doc["meshes"] for p in mesh["primitives"]]
    assert kinds == [5123, 5125]


def test_texture_coordinates_survive_a_merge_a_transform_and_a_mirror_and_are_written_to_the_glb():
    a = pm.cylinder([0, 0, 0], [0, 0, 1], 1.0, 8).with_uv(lambda p: np.column_stack([np.full(len(p), 0.5), p[:, 2]]))
    b = pm.cylinder([5, 0, 0], [5, 0, 1], 1.0, 8)
    both = pm.merge([a, b])
    assert both.uv.shape == (len(both.pos), 2) and (both.uv[len(a.pos):] == 0).all() and both.uv[: len(a.pos), 0].min() == 0.5          # (no uv: zeros)
    moved = a.translated([1, 2, 3]).transformed(np.diag([2.0, 2.0, 2.0, 1.0])).mirrored_x()
    assert np.array_equal(moved.uv, a.uv) and moved.volume() > 0
    assert pm.merge([b, b]).uv is None
    g = pm.Gltf()
    png = io.BytesIO()
    Image.new("RGBA", (4, 8), (255, 255, 255, 128)).save(png, "PNG")
    texture = g.texture(png.getvalue())
    material = g.material("glass", (0.5, 0.5, 0.5), opacity=0.5, texture=texture)
    g.mesh("m", [(a, material)])
    glb = _parse_glb(g.dumps([g.node("n", 0)]))
    doc = glb.json
    assert doc["materials"][0]["pbrMetallicRoughness"]["baseColorTexture"] == {"index": 0, "texCoord": 0} and doc["materials"][0]["alphaMode"] == "BLEND"
    assert doc["textures"] == [{"source": 0, "sampler": 0}] and doc["samplers"][0]["wrapT"] == 33071 and "target" not in doc["bufferViews"][doc["images"][0]["bufferView"]]
    assert glb.image(0).size == (4, 8) and glb.image(0).convert("RGBA").getpixel((0, 0))[3] == 128
    prim = doc["meshes"][0]["primitives"][0]
    uv = glb.accessor(prim["attributes"]["TEXCOORD_0"])
    assert uv.shape == (len(a.pos), 2) and np.allclose(uv[:, 1], a.uv[:, 1], atol=1e-6)

