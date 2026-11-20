"""Attaching the detailed head to the body model (npcgen/headgraft.py, npcgen/blender/graft_math.py, npcgen/blender/head_graft.py):
the numpy parts of the geometry, the colour match, the two Blender runs with SDPose between them, and the option's plumbing."""
import copy
import io
import json
import pathlib
import struct
import sys
import threading
import types

import numpy as np
import pytest
from PIL import Image

from npcgen import cleanup3d, cli, config, headgraft, posekeys, rig3d, roll, service
from npcgen.comfy import Cancelled, ComfyError
from npcgen.jobs import Jobs
from test_cleanup3d import png, world  # noqa: F401  (world is a fixture)

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "npcgen" / "blender"))
import graft_math as gm  # noqa: E402  (numpy only, like the Blender script that imports it)


# ---- the geometry --------------------------------------------------------------------------------------------------------------

def test_the_similarity_in_a_plane_is_found():
    rng = np.random.default_rng(3)
    src = rng.normal(size=(40, 2))
    angle = np.radians(12.0)
    R = np.array([[np.cos(angle), -np.sin(angle)], [np.sin(angle), np.cos(angle)]])
    dst = 0.7 * (src @ R.T) + [0.3, -0.2]
    s, R2, t = gm.similarity_2d(src, dst)
    assert s == pytest.approx(0.7) and np.allclose(R2, R) and np.allclose(t, [0.3, -0.2])


def _face(rng):
    """68 face points on a body (x across, y depth, z up): the eyes stand in front of the skin, like a mask's glasses."""
    pts = rng.normal(0, 0.02, (68, 3)) + [0, 0.0, 1.6]
    pts[36:48, 1] -= 0.05
    return pts


def test_the_face_fit_reads_scale_turn_and_shift_from_all_points_and_depth_from_the_nose_and_mouth():
    rng = np.random.default_rng(5)
    body = _face(rng)
    angle = np.radians(6.0)
    inverse = np.array([[np.cos(angle), 0, np.sin(angle)], [0, 1, 0], [-np.sin(angle), 0, np.cos(angle)]])
    head = (body - [0.01, 0.02, 1.6]) @ inverse.T / 0.2
    head[36:48, 1] += rng.normal(0, 3.0, 12)              # the head's eye depth is garbage (glasses, a mask): must not matter
    body_seen = [tuple(p) for p in body]
    head_seen = [tuple(p) for p in head]
    scale, R, t, info = gm.fit_face(head_seen, body_seen, [0.9] * 68, [0.9] * 68)
    assert scale == pytest.approx(0.2, rel=0.02) and abs(info["turn_deg"] - 6.0) < 1.0
    moved = scale * (np.array(head_seen)[[30, 31, 48, 54]] @ R.T) + t
    assert np.abs(moved[:, [0, 2]] - body[[30, 31, 48, 54]][:, [0, 2]]).max() < 0.005      # across and up: right
    assert np.abs(moved[:, 1] - body[[30, 31, 48, 54]][:, 1]).max() < 0.02                  # depth: the nose and mouth's


def test_a_face_fit_needs_enough_points_seen_by_both():
    rng = np.random.default_rng(6)
    body = [tuple(p) for p in _face(rng)]
    head = [tuple(p) if i in (30, 31, 32) else None for i, p in enumerate(_face(rng))]
    with pytest.raises(ValueError, match="Too few face points"):
        gm.fit_face(head, body, [0.9] * 68, [0.9] * 68)
    low = [0.2] * 68                                        # ...and SDPose must have been sure of them
    with pytest.raises(ValueError, match="Too few face points"):
        gm.fit_face(body, body, low, low)


def test_hair_is_split_off_only_when_it_is_a_second_population():
    rng = np.random.default_rng(8)
    skin = np.clip(rng.normal([0.75, 0.55, 0.45], 0.04, (300, 3)), 0, 1)
    hair = np.clip(rng.normal([0.08, 0.05, 0.04], 0.02, (150, 3)), 0, 1)
    rgb = np.concatenate([skin, hair])
    lum = rgb @ [0.2126, 0.7152, 0.0722]
    labels, report = gm.hair_split(lum, rgb)
    assert labels is not None and labels[300:].all() and not labels[:300].any() and report["luminance"]["ok"]
    only_skin, report = gm.hair_split(lum[:300], rgb[:300])
    assert only_skin is None                                  # a bald head: no second population
    assert gm.hair_split(lum[:10], rgb[:10])[0] is None


def test_red_hair_on_pale_skin_is_found_by_its_colour_when_the_light_does_not_separate_them():
    rng = np.random.default_rng(9)
    skin = np.clip(rng.normal([0.72, 0.62, 0.58], 0.02, (300, 3)), 0, 1)       # pale, low saturation
    hair = np.clip(rng.normal([0.62, 0.30, 0.16], 0.02, (200, 3)), 0, 1)       # copper, about as bright as the skin's shade
    rgb = np.concatenate([skin, hair])
    lum = rgb @ [0.2126, 0.7152, 0.0722]
    labels, report = gm.hair_split(lum, rgb)
    assert labels is not None and labels[300:].mean() > 0.95 and labels[:300].mean() < 0.05


def test_a_front_view_keeps_the_nearest_surface_at_each_pixel():
    pos = np.array([[0, 0.0, 0], [4, 0, 0], [4, 0, -4], [0, 0, -4],            # a square at depth y = 0
                    [1, -1.0, -1], [3, -1, -1], [3, -1, -3], [1, -1, -3]], float)   # a smaller one in front of it (-y is the front)
    tri = np.array([[0, 1, 2], [0, 2, 3], [4, 5, 6], [4, 6, 7]])
    depth, triangle = gm.front_depth(pos, tri, (6, 6), (-1.0, 1.0), 1.0)
    depth, triangle = depth.reshape(6, 6), triangle.reshape(6, 6)
    assert depth[2, 2] == pytest.approx(-1.0) and triangle[2, 2] >= 2               # the front square hides the back one
    assert depth[1, 1] == pytest.approx(0.0) and triangle[1, 1] < 2                 # ...but only where it is
    assert np.isinf(depth[5, 5]) and triangle[5, 5] == -1                           # nothing there


def test_thin_things_in_front_of_the_skin_do_not_count_in_the_deepest_depth():
    depth = np.full((9, 9), 5.0)
    depth[4, :] = 2.0                                         # a wire across the face, in front of it
    depth[:, 0] = np.inf                                      # and a column with no surface
    deep = gm.deepest_within(depth, 2)
    assert deep[4, 4] == pytest.approx(5.0)                   # the wire's pixels take the skin's depth beside them
    assert np.isinf(deep[0, 0]) and deep[6, 4] == pytest.approx(5.0)


def test_holes_in_the_patch_are_filled_with_the_depth_round_them_and_the_hull_includes_them():
    depth = np.full((20, 20), np.inf)
    depth[3:17, 3:17] = 4.0
    depth[3:17, 3:10] = 2.0                                   # the left part is nearer than the right part
    depth[8:12, 8:12] = np.inf                                # a hole in the middle
    hull, filled = gm.fill_holes_and_depth(depth)
    assert hull[10, 10] and not hull[0, 0]                    # the hole is inside the hull; the frame is outside
    assert 2.0 <= filled[10, 10] <= 4.0 and filled[5, 5] == pytest.approx(2.0)
    inside = gm.chamfer_inside(hull)
    assert inside[10, 10] > inside[4, 4] > 0 and inside[0, 0] == 0


def test_a_patchs_outer_edge_is_the_widest_boundary_loop_and_distance_runs_from_it():
    n = 10
    xs, zs = np.meshgrid(np.arange(n + 1.0), np.arange(n + 1.0))
    pos = np.stack([xs.ravel(), np.zeros((n + 1) ** 2), zs.ravel()], 1)
    index = lambda i, j: j * (n + 1) + i
    tri, hole = [], {(4, 4), (5, 4), (4, 5), (5, 5)}
    for j in range(n):
        for i in range(n):
            if (i, j) in hole:
                continue
            tri += [[index(i, j), index(i + 1, j), index(i, j + 1)], [index(i + 1, j), index(i + 1, j + 1), index(i, j + 1)]]
    tri = np.array(tri)
    keys = np.sort(np.concatenate([tri[:, [0, 1]], tri[:, [1, 2]], tri[:, [2, 0]]]), axis=1)
    pairs, counts = np.unique(keys, axis=0, return_counts=True)
    loops = gm.boundary_loops(pairs[counts == 1])
    assert len(loops) == 2
    edge = gm.outer_loop(pos, loops)
    assert index(0, 0) in edge and index(4, 4) not in edge
    dist, origin = gm.surface_distance(pos, pairs, edge)
    assert dist[index(0, 5)] == 0 and dist[index(3, 3)] == pytest.approx(3.0)
    assert dist[index(4, 4)] == pytest.approx(4.0)             # beside the hole, and not 0: the hole's edge is no source
    assert origin[index(3, 3)] in edge


def test_an_opening_takes_off_a_thin_strip_but_never_a_protected_face():
    block = {(i, j) for i in range(8) for j in range(8)}
    strip = {(k, 3) for k in range(8, 14)}                     # a strip one face thick, sticking out of the block
    faces = block | strip
    number = {f: n for n, f in enumerate(sorted(faces))}
    def around(f):
        i, j = f
        return [number.get(g, -1) for g in ((i - 1, j), (i + 1, j), (i, j - 1), (i, j + 1))]
    neighbours = {number[f]: around(f) for f in faces}
    every = {number[f] for f in faces}
    kept = gm.opening(every, every, neighbours, 2)
    kept_faces = {f for f in faces if number[f] in kept}
    assert {(i, j) for i in range(2, 6) for j in range(2, 6)} <= kept_faces        # the block's middle stays
    assert not kept_faces & {(k, 3) for k in range(10, 14)}                       # the strip goes (its root is given back with the block)
    protected = {number[f] for f in strip}
    still = gm.opening(every, every - protected, neighbours, 2)
    assert protected <= still                                  # protected faces are never taken


# ---- the smooth outline ---------------------------------------------------------------------------------------------------------

CENTRE = (0.0, 0.45)


def _scatter(seed=5, n=20000, radius=1.2):
    """Face centres scattered evenly over a disc of `radius` (u) round the outline's centre: (dx, dz, radius, angle in degrees)."""
    rng = np.random.default_rng(seed)
    r = radius * np.sqrt(rng.random(n))
    a = rng.random(n) * 2 * np.pi
    dx, dz = CENTRE[0] + r * np.cos(a), CENTRE[1] + r * np.sin(a)
    return dx, dz, r, np.degrees(a)


def test_with_no_hair_the_outline_is_the_candidates_own_and_is_only_rounded_and_pulled_in_a_little():
    dx, dz, r, _a = _scatter()
    candidate = np.ones(len(dx), bool)
    within, info = gm.polar_cut(dx, dz, candidate, np.zeros(len(dx), bool))
    assert within[r < 1.1].all() and not within[r > 1.19].any()
    assert within.sum() > 0.9 * candidate.sum() and info["hair_bins"] == 0


def test_a_hairline_of_hair_seen_from_the_outside_in_makes_the_cut_stop_just_inside_it():
    dx, dz, r, a = _scatter()
    up = (a > 40) & (a < 140)
    hair = up & (r > 0.8)                                      # a mass of hair over the top, from radius 0.8 out
    within, info = gm.polar_cut(dx, dz, np.ones(len(dx), bool), hair)
    assert info["hair_bins"] > 10
    assert not (within & hair & (a > 60) & (a < 120)).any()    # none of the hair, well inside the sector
    assert within[up & (r < 0.72) & (a > 60) & (a < 120)].all()  # and the skin up to just short of it
    assert within[(a > 200) & (a < 340) & (r < 1.1)].all()      # the other directions keep their own outline
    assert np.abs(np.diff(np.append(info["cut"], info["cut"][0]))).max() < 0.06     # and the curve is smooth all round


def test_a_lone_strand_or_a_strand_with_skin_between_it_and_the_hair_does_not_pull_the_outline_in():
    dx, dz, r, a = _scatter()
    lone = (a > 88) & (a < 92) & (r > 0.55) & (r < 0.62)      # a strand across the forehead, on its own
    within, _info = gm.polar_cut(dx, dz, np.ones(len(dx), bool), lone)
    assert within[(a > 60) & (a < 120) & (r < 1.1)].all()
    mass = (a > 60) & (a < 120) & (r > 1.0)                   # a strand at 0.6, skin from 0.65 to 1.0, hair from there out
    within, _info = gm.polar_cut(dx, dz, np.ones(len(dx), bool), lone | mass)
    assert within[(a > 75) & (a < 105) & (r > 0.65) & (r < 0.9)].all()               # the skin band is kept, strand and all
    assert not within[(a > 75) & (a < 105) & (r > 1.05)].any()


def test_the_cut_stays_inside_the_candidates_and_the_number_of_bins_does_not_matter_much():
    dx, dz, r, a = _scatter(seed=9)
    candidate = r < 1.0
    hair = candidate & (a > 20) & (a < 80) & (r > 0.7)
    for bins in (72, 144, 288):
        within, info = gm.polar_cut(dx, dz, candidate, hair, bins=bins)
        assert not (within & ~candidate).any() and len(info["cut"]) == bins
        assert not (within & hair & (a > 35) & (a < 65)).any()


# ---- the colour maps ----------------------------------------------------------------------------------------------------------------

def test_a_texture_is_sampled_bilinearly_and_clamped_at_its_border():
    image = np.zeros((2, 2, 3))
    image[:, 1] = 100.0                                          # left column black, right column 100
    got = gm.sample_bilinear(image, np.array([0.25, 0.5, 0.75, 0.0, 1.0]), np.array([0.5, 0.5, 0.5, 0.5, 0.5]))
    assert got[:, 0] == pytest.approx([0.0, 50.0, 100.0, 0.0, 100.0])


def _quad(x0, x1, z0, z1, y):
    pos = np.array([[x0, y, z1], [x1, y, z1], [x1, y, z0], [x0, y, z0]], float)          # top left, top right, bottom right, bottom left
    return pos, np.array([[0, 0], [1, 0], [1, 1], [0, 1]], float), np.array([[0, 1, 2], [0, 2, 3]])


def test_the_colour_seen_from_the_front_is_the_nearest_surfaces_and_is_nothing_where_there_is_none():
    image = np.zeros((4, 4, 3))
    image[:, :2] = (200, 0, 0)                                   # the left half red, the right half blue
    image[:, 2:] = (0, 0, 200)
    back, back_uv, back_tri = _quad(0, 8, -8, 0, 0.0)
    front, front_uv, front_tri = _quad(2, 4, -4, -2, -1.0)       # a smaller quad in front of it (-y is the front), all blue
    pos = np.concatenate([back, front])
    uv = np.concatenate([back_uv, np.full((4, 2), 0.9)])
    tri = np.concatenate([back_tri, front_tri + 4])
    colour, covered = gm.front_colours(pos, uv, tri, image, (10, 10), (-1.0, 1.0), 1.0)
    assert covered[2, 2] and covered[7, 7] and not covered[9, 9]                 # pixel (row, column): the frame is (-1, 1) from the top left
    assert colour[4, 4] == pytest.approx([0, 0, 200])                              # the front quad's blue where it hides the back one
    assert colour[2, 2][0] > 150 and colour[2, 2][2] < 50                          # the back quad's left half is red
    assert colour[6, 6][2] > 150 and colour[6, 6][0] < 50                          # its right half is blue


def test_a_hole_in_a_colour_map_is_filled_from_the_colours_round_it_and_nothing_else_changes():
    values = np.zeros((20, 20, 3))
    values[:, :, 0] = np.linspace(0, 190, 20)[None, :]           # a ramp across
    known = np.ones((20, 20), bool)
    known[8:12, 8:12] = False                                    # a hole
    region = np.ones((20, 20), bool)
    out, filled = gm.spread_colour(values * known[..., None], known, region)
    assert filled.sum() == 16 and filled[10, 10] and not filled[0, 0]
    assert 60 < out[10, 10, 0] < 130                              # between the colours at the hole's sides
    assert (out[known] == (values * known[..., None])[known]).all()
    known[:3] = False                                            # and three rows at the top that are not wanted
    region[:3] = False
    _out, filled = gm.spread_colour(values * known[..., None], known, region)
    assert filled.sum() == 16 and not filled[:3].any()             # nothing spreads beyond the region


def test_the_backing_of_a_patch_with_a_hole_reaches_over_the_hole_and_the_map_says_where_the_patch_is():
    n = 8
    pos, uv, tri = _grid(0.0, 8.0, 0.0, 8.0, 0.0, n)
    keep = [t for t in range(len(tri)) if not (3 <= (t // 2) % n < 5 and 3 <= (t // 2) // n < 5)]         # two cells across and two down
    pos = pos[:, [0, 2, 1]]                                       # the grid's y is the height: the (x, y depth, z up) frame has it last
    maps = gm.backing_maps(pos, tri[keep], 0.25, 2, 100)
    assert maps["hull"].sum() > maps["covered"].sum() > 0
    hole = maps["hull"] & ~maps["covered"]
    assert hole.sum() > 20 and np.isfinite(maps["filled"][hole]).all()


# ---- the patch's distance from its edge, and the colour match -------------------------------------------------------------------

def _grid(x0, x1, y0, y1, z, n=12):
    xs, ys = np.meshgrid(np.linspace(x0, x1, n + 1), np.linspace(y0, y1, n + 1))
    pos = np.stack([xs.ravel(), ys.ravel(), np.full((n + 1) ** 2, z)], 1)
    uv = np.stack([np.tile(np.linspace(0, 1, n + 1), n + 1), np.repeat(np.linspace(1, 0, n + 1), n + 1)], 1)
    tri = []
    for j in range(n):
        for i in range(n):
            a = j * (n + 1) + i
            tri += [[a, a + 1, a + n + 1], [a + 1, a + n + 2, a + n + 1]]
    return pos, uv, np.array(tri)


def _image(colour_a, colour_b=None, size=64):
    ramp = np.linspace(0, 1, size)[None, :, None]
    a, b = np.array(colour_a, float), np.array(colour_b if colour_b is not None else colour_a, float)
    pixels = np.broadcast_to(a * (1 - ramp) + b * ramp, (size, size, 3))
    out = io.BytesIO()
    Image.fromarray(np.rint(pixels).astype(np.uint8)).save(out, "PNG")
    return out.getvalue()


def two_part_glb(parts):
    """A GLB with one primitive per part: (name, positions, uvs, triangles, PNG bytes)."""
    blob, views, accessors, images, textures, materials, prims = b"", [], [], [], [], [], []

    def add(data):
        nonlocal blob
        blob += b"\0" * (-len(blob) % 4)
        views.append({"buffer": 0, "byteOffset": len(blob), "byteLength": len(data)})
        blob += data
        return len(views) - 1
    for k, (name, pos, uv, tri, image) in enumerate(parts):
        pos, uv, tri = np.asarray(pos, np.float32), np.asarray(uv, np.float32), np.asarray(tri, np.uint32)
        accessors.append({"bufferView": add(pos.tobytes()), "componentType": 5126, "count": len(pos), "type": "VEC3",
                          "min": pos.min(0).tolist(), "max": pos.max(0).tolist()})
        accessors.append({"bufferView": add(uv.tobytes()), "componentType": 5126, "count": len(pos), "type": "VEC2"})
        accessors.append({"bufferView": add(tri.tobytes()), "componentType": 5125, "count": int(tri.size), "type": "SCALAR"})
        images.append({"bufferView": add(image), "mimeType": "image/png"})
        textures.append({"source": k})
        materials.append({"name": name, "pbrMetallicRoughness": {"baseColorTexture": {"index": k}}})
        prims.append({"attributes": {"POSITION": 3 * k, "TEXCOORD_0": 3 * k + 1}, "indices": 3 * k + 2, "material": k})
    blob += b"\0" * (-len(blob) % 4)
    doc = {"asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0}],
           "buffers": [{"byteLength": len(blob)}], "bufferViews": views, "accessors": accessors, "images": images,
           "textures": textures, "materials": materials, "meshes": [{"primitives": prims}]}
    text = json.dumps(doc).encode()
    text += b" " * (-len(text) % 4)
    return (struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(text) + 8 + len(blob)) + struct.pack("<II", len(text), 0x4E4F534A)
            + text + struct.pack("<II", len(blob), 0x004E4942) + blob)


BODY_SKIN, PATCH_LIGHT, PATCH_LIGHTER = (170, 120, 95), (225, 175, 145), (245, 190, 160)


def grafted_model():
    """A 1.7 m body (a wall of skin) with a patch of lighter skin a centimetre in front of it, near its top."""
    b_pos, b_uv, b_tri = _grid(-0.5, 0.5, 0.0, 1.7, 0.0, 16)
    p_pos, p_uv, p_tri = _grid(-0.1, 0.1, 1.45, 1.65, 0.01, 16)
    return two_part_glb([("body", b_pos, b_uv, b_tri, _image(BODY_SKIN)),
                         (headgraft.PATCH_MATERIAL, p_pos, p_uv, p_tri, _image(PATCH_LIGHT, PATCH_LIGHTER))])


def test_the_patchs_distance_is_from_its_outer_edge_and_texture_seams_do_not_split_it():
    pos, uv, tri = _grid(0, 1, 0, 1, 0, 8)
    seamed = np.concatenate([pos, pos[[0, 1, 2]]])             # a texture seam: copies of three vertices under other numbers
    tri = tri.copy()
    tri[0] = [len(pos), len(pos) + 1, len(pos) + 2]
    dist, weld = headgraft.patch_distance(seamed, tri)
    assert dist[weld[len(pos)]] == 0 and dist.max() == pytest.approx(0.5, abs=0.02)       # a copy has its original's distance
    assert dist[weld[4 * 9 + 4]] == pytest.approx(0.5, abs=0.02)                            # the middle of the patch


def test_the_patchs_paint_crosses_over_to_the_bodys_along_its_edge_and_takes_the_bodys_tone_all_over():
    out, info = headgraft.blend_colour(grafted_model())
    glb = headgraft.Glb(out)
    assert len(glb.json["images"]) == 2
    patch_image = np.asarray(glb.image(1).convert("RGB"), float)
    gain = np.array(BODY_SKIN) / np.array(PATCH_LIGHT)
    assert np.allclose(info["gain"], gain, atol=0.06)
    middle = patch_image[32, 32]
    raw_middle = np.array(PATCH_LIGHT) * 0.5 + np.array(PATCH_LIGHTER) * 0.5
    assert np.allclose(middle, raw_middle * np.array(info["gain"]), atol=6)                 # the tone, and the picture's own variation kept
    assert (middle < raw_middle - 20).all()                                                 # darker than it was: the body's tone
    edge = patch_image[0, 32]                                                               # the top row is the patch's edge
    assert np.abs(edge - np.array(BODY_SKIN)).max() < 12                                    # at the edge it is the body's colour
    assert (np.asarray(glb.image(0).convert("RGB")) == np.array(BODY_SKIN)).all()           # the body's own paint is not touched


def test_a_model_with_no_patch_comes_back_as_it_was():
    b_pos, b_uv, b_tri = _grid(-0.5, 0.5, 0.0, 1.7, 0.0)
    plain = two_part_glb([("body", b_pos, b_uv, b_tri, _image(BODY_SKIN))])
    out, info = headgraft.blend_colour(plain)
    assert out == plain and "error" in info


def _concat(*grids):
    pos, uv, tri, base = [], [], [], 0
    for p_, u_, t_ in grids:
        pos.append(p_)
        uv.append(u_)
        tri.append(t_ + base)
        base += len(p_)
    return np.concatenate(pos), np.concatenate(uv), np.concatenate(tri)


def holed_model():
    """A 1.7 m wall of skin, a patch of lighter skin a millimetre in front of it with a 50 mm hole in the middle, and a strip of the body's
    own (a lock of hair, say) 5 cm in front of the patch, as a primitive of its own."""
    wall = _grid(-0.5, 0.5, 0.0, 1.7, 0.009, 16)
    strip = _grid(0.02, 0.06, 1.5, 1.6, 0.06, 4)
    patch_pos, patch_uv, patch_tri = _grid(-0.1, 0.1, 1.45, 1.65, 0.01, 16)
    cells = np.arange(len(patch_tri)) // 2
    hole = (cells % 16 >= 6) & (cells % 16 < 10) & (cells // 16 >= 6) & (cells // 16 < 10)
    return two_part_glb([("body", *wall, _image(BODY_SKIN, size=256)),
                         ("hair", *strip, _image((120, 60, 30), size=64)),
                         (headgraft.PATCH_MATERIAL, patch_pos, patch_uv, patch_tri[~hole], _image(PATCH_LIGHT, PATCH_LIGHTER))])


def _body_colour_at(glb, x, y):
    """The body wall's paint at the texel for the point (x across, y up) of holed_model's wall."""
    image = np.asarray(glb.image(0).convert("RGB"), float)
    return image[int((1 - y / 1.7) * 255), int((x + 0.5) * 255)]


def test_the_bodys_skin_under_the_patch_takes_the_patchs_colour_including_behind_a_hole_and_hair_in_front_is_left_alone():
    out, info = headgraft.fill_backing_colour(holed_model())
    glb = headgraft.Glb(out)
    assert info["texels"] > 0 and info["hole_share"] > 0.04
    behind_hole = _body_colour_at(glb, 0.0, 1.55)
    assert (behind_hole > np.array(BODY_SKIN) + 20).all()                    # the patch's lighter skin, not the body's own tone
    behind_patch = _body_colour_at(glb, -0.06, 1.55)
    assert (behind_patch > np.array(BODY_SKIN) + 20).all()
    far = _body_colour_at(glb, -0.4, 0.5)
    assert np.abs(far - np.array(BODY_SKIN)).max() < 1                       # the rest of the body is as it was
    before = headgraft.Glb(holed_model())
    for kept in (1, 2):                                                      # the hair strip and the patch keep their paint
        assert np.array_equal(np.asarray(glb.image(kept).convert("RGB")), np.asarray(before.image(kept).convert("RGB")))


def test_no_patch_or_no_body_means_nothing_to_fill_under():
    b_pos, b_uv, b_tri = _grid(-0.5, 0.5, 0.0, 1.7, 0.0)
    plain = two_part_glb([("body", b_pos, b_uv, b_tri, _image(BODY_SKIN))])
    out, info = headgraft.fill_backing_colour(plain)
    assert out == plain and "error" in info


# ---- SDPose's face points, and the two Blender runs -------------------------------------------------------------------------------

def test_the_attach_needs_blender_and_sdpose(monkeypatch):
    monkeypatch.setattr(rig3d, "availability", lambda cfg: {"missing": ["Blender"]})
    monkeypatch.setattr(posekeys, "availability", lambda comfy: {"missing": ["SDPose in ComfyUI"]})
    assert headgraft.availability({}, object())["missing"] == ["Blender", "SDPose in ComfyUI"]
    assert "ComfyUI" in headgraft.availability({}, None)["missing"]


def test_the_faces_points_come_from_a_detection_and_then_a_close_up_of_the_face_in_the_pictures_pixels(monkeypatch):
    coarse = [[100 + i, 200 + 2 * i, 0.9] for i in range(68)]
    seen = []

    def detect(comfy, pictures, npc_id, cancel=None):
        key = next(iter(pictures))
        seen.append((key, pictures[key].size))
        person = {"face_keypoints_2d": coarse if key == "body" else [[10 + i, 20 + i, 0.8] for i in range(68)]}
        return {key: person}
    monkeypatch.setattr(posekeys, "detect", detect)
    picture = Image.new("RGB", (1024, 1024), "white")
    points = headgraft.face_points(None, picture, "npc1", "body")
    box = posekeys.face_box(coarse, picture.size)
    assert [k for k, _size in seen] == ["body", "body_zoom"] and seen[1][1] == (round(box[2]), round(box[3]))
    assert points[0] == [10 + box[0], 20 + box[1], 0.8] and len(points) == 68


def test_no_face_in_a_picture_is_a_readable_error(monkeypatch):
    monkeypatch.setattr(posekeys, "detect", lambda comfy, pictures, npc_id, cancel=None: {k: {"face_keypoints_2d": []} for k in pictures})
    with pytest.raises(headgraft.GraftError, match="no face in the head"):
        headgraft.face_points(None, Image.new("RGB", (64, 64)), "npc1", "head")
    weak = lambda comfy, pictures, npc_id, cancel=None: {k: {"face_keypoints_2d": [[10 + i, 10 + i, 0.5 if "zoom" not in k else 0.1]
                                                                                   for i in range(68)]} for k in pictures}
    monkeypatch.setattr(posekeys, "detect", weak)
    with pytest.raises(headgraft.GraftError, match="not sure"):
        headgraft.face_points(None, Image.new("RGB", (1024, 1024)), "npc1", "body")


def _fake_blender(calls):
    def run_script(cfg, script, script_args, tmp, out, doing, failed, cancel=None, timeout=0):
        calls.append((pathlib.Path(script).name, list(script_args), doing))
        if pathlib.Path(script).name == "face_views.py":
            folder = pathlib.Path(script_args[2])
            folder.mkdir()
            for name in ("body", "head"):
                Image.new("RGB", (64, 64), "white").save(folder / f"{name}_front.png")
            (folder / "views.json").write_text(json.dumps({"body": {"centre": [0, 1], "px": 0.001, "size": [64, 64]},
                                                             "head": {"centre": [0, 0], "px": 0.001, "size": [64, 64]}}))
        else:
            pathlib.Path(out).write_bytes(grafted_model())
            pathlib.Path(script_args[script_args.index("--report") + 1]).write_text(json.dumps({"patch_faces": 12345, "fit": {"points": 33}}))
            pathlib.Path(script_args[script_args.index("--poster") + 1]).write_bytes(b"POSTER")
    return run_script


def test_the_attach_renders_both_faces_finds_their_points_grafts_and_matches_the_colour(monkeypatch):
    calls = []
    monkeypatch.setattr(rig3d, "run_script", _fake_blender(calls))
    monkeypatch.setattr(headgraft, "face_points", lambda comfy, picture, npc_id, key, cancel=None: [[i, i, 0.9] for i in range(68)])
    attacher = headgraft.Attacher("comfy", "npc1", b"HEAD", "c1")
    glb, poster, info = headgraft.attach({}, b"BODY", attacher)
    assert [c[0] for c in calls] == ["face_views.py", "head_graft.py"]
    graft_args = calls[1][1]
    assert graft_args[graft_args.index("--views") + 1].endswith("views.json") and "--landmarks" in graft_args
    assert poster == b"POSTER" and info["patch_faces"] == 12345 and info["face_scores"] == {"body": 0.9, "head": 0.9}
    assert info["colour"]["gain"][0] < 1 and info["seconds"] >= 0
    assert glb != grafted_model()                                                            # the colour was matched
    kept = headgraft.summary(info)
    assert set(kept) <= {"fit", "u_mm", "dropped", "cut", "patch_faces", "eased", "body", "faces_after", "face_scores", "colour", "holes", "seconds"}


def test_a_head_that_cannot_be_attached_leaves_the_model_as_it_was(monkeypatch):
    def attach(cfg, body, attacher, cancel=None):
        raise headgraft.GraftError("SDPose found no face in the body")
    monkeypatch.setattr(headgraft, "attach", attach)
    assert headgraft.try_attach({}, b"BODY", None) == (b"BODY", None, {"error": "SDPose found no face in the body"})

    def cancelled(cfg, body, attacher, cancel=None):
        raise Cancelled()
    monkeypatch.setattr(headgraft, "attach", cancelled)
    with pytest.raises(Cancelled):
        headgraft.try_attach({}, b"BODY", None)


# ---- the option in the clean-up and the job ----------------------------------------------------------------------------------------

def _plain_clean_up(monkeypatch):
    monkeypatch.setattr(cleanup3d, "remove_inner_skin", lambda model, report: (report.update(inner_skin={}), model)[1])
    monkeypatch.setattr(cleanup3d.unwrap, "needs_texture", lambda model: False)
    monkeypatch.setattr(cleanup3d.rig3d, "run_script", lambda cfg, script, args, tmp, out, *a, **kw: pathlib.Path(out).write_bytes(b"game"))


def test_the_head_is_attached_last_of_all_to_the_game_version(monkeypatch):
    _plain_clean_up(monkeypatch)
    calls = []
    monkeypatch.setattr(headgraft, "try_attach", lambda cfg, model, attacher, cancel=None: calls.append((model, attacher))
                        or (b"game with a face", b"poster", {"fit": {"points": 30}}))
    game, poster, report = cleanup3d.refine({}, b"generated", None, head="attacher")
    assert calls == [(b"game", "attacher")] and (game, poster) == (b"game with a face", b"poster") and report["head"]["fit"]["points"] == 30
    game, poster, report = cleanup3d.refine({}, b"generated", None)
    assert game == b"game" and "head" not in report                         # never unless asked


def test_the_option_is_planned_only_when_asked_and_named_in_the_label():
    npc = {"assets": {"sprite": {"file": "sprite.png", "created": "t"}, "model_3d": {"file": "model_3d.glb", "textured": True}}}
    assert [s.get("key") for s in Jobs.plan(npc, {"attach_head": True}, {})] == ["head"]
    assert [s.get("key") for s in Jobs.plan(npc, {"clean_hands": True, "attach_head": True}, {})] == ["hands+head"]
    assert "the detailed head attached" in Jobs.plan(npc, {"attach_head": True}, {})[0]["label"]
    assert service.clean_assets({"attach_head": True}, roll.load_data())["attach_head"] is True
    assert service.clean_assets({"portrait": True}, roll.load_data())["attach_head"] is False
    parser = cli.build_parser(roll.load_data())
    assert parser.parse_args(["generate", "--ids", "x", "--attach-head"]).attach_head is True
    assert parser.parse_args(["generate", "--ids", "x", "--portrait"]).attach_head is False


def test_a_head_made_in_the_same_request_comes_before_the_clean_up_that_attaches_it():
    npc = {"assets": {"portrait": {"file": "portrait.png"}, "sprite": {"file": "sprite.png", "created": "t"},
                      "model_3d": {"file": "model_3d.glb", "textured": True}}}
    both = [s["kind"] for s in Jobs.plan(npc, {"head_3d": True, "refine_3d": True, "attach_head": True, "rig_3d": True}, {})]
    assert both == ["head_view"] * 4 + ["head_3d", "refine_3d", "rig_3d"]
    apart = [s["kind"] for s in Jobs.plan(npc, {"head_3d": True, "refine_3d": True, "rig_3d": True}, {})]
    assert apart == ["refine_3d", "rig_3d"] + ["head_view"] * 4 + ["head_3d"]                  # as before: the head stays last


def _give_a_head(lib, npc_id):
    lib.write_asset(npc_id, "head_3d.glb", b"HEAD")
    lib.update(npc_id, lambda n: n["assets"].update(head_3d={"file": "head_3d.glb", "created": "c9"}))


def _refine(lib, npc_id):
    job = types.SimpleNamespace(npc_id=npc_id, cancel=threading.Event(), cache={}, ref=None, options={"quality": "standard", "seed": None})
    step = {"kind": "refine_3d", "key": "head", "label": "x"}
    Jobs(copy.deepcopy(config.DEFAULTS), None, lib)._run_step(job, step)
    return step, lib.get(npc_id)["assets"]["model_3d"]["refined"]


def _fake_refine(head_report, got):
    def fake(cfg, model, views, faces=None, cancel=None, closeups=None, eyes=None, hair=None, hands=None, head=None):
        got["head"] = head
        return b"game", None, {"inner_skin": {"faces_before": 10, "faces_removed": 2}, "game_version": {"faces_after": 8},
                               "paint": {"views": {}}, "head": head_report}
    return fake


def test_the_step_hands_the_head_model_over_and_keeps_what_the_attach_found(world, monkeypatch):  # noqa: F811
    lib, npc_id, _run = world
    _give_a_head(lib, npc_id)
    got = {}
    monkeypatch.setattr(headgraft, "availability", lambda cfg, comfy: {"missing": []})
    monkeypatch.setattr(cleanup3d, "refine", _fake_refine({"fit": {"points": 31}, "patch_faces": 40000, "seconds": 90.0}, got))
    step, refined = _refine(lib, npc_id)
    assert isinstance(got["head"], headgraft.Attacher) and got["head"].head_glb == b"HEAD" and got["head"].head_created == "c9"
    assert refined["head"]["patch_faces"] == 40000 and refined["head"]["source_head_created"] == "c9" and "note" not in step
    assert refined["faces"] == 8                                             # (no faces_after in this report: the game version's count stands)


def test_the_models_face_count_is_the_game_version_and_the_patch_together(world, monkeypatch):  # noqa: F811
    lib, npc_id, _run = world
    _give_a_head(lib, npc_id)
    monkeypatch.setattr(headgraft, "availability", lambda cfg, comfy: {"missing": []})
    monkeypatch.setattr(cleanup3d, "refine", _fake_refine({"fit": {"points": 31}, "faces_after": 140000}, {}))
    _step, refined = _refine(lib, npc_id)
    assert refined["faces"] == 140000


def test_a_face_that_could_not_be_attached_is_said_so_and_the_clean_up_is_kept(world, monkeypatch):  # noqa: F811
    lib, npc_id, _run = world
    _give_a_head(lib, npc_id)
    monkeypatch.setattr(headgraft, "availability", lambda cfg, comfy: {"missing": []})
    monkeypatch.setattr(cleanup3d, "refine", _fake_refine({"error": "SDPose found no face in the body"}, {}))
    step, refined = _refine(lib, npc_id)
    assert refined["head"] == {"error": "SDPose found no face in the body"}
    assert "left as it was" in step["note"] and lib.read_asset(npc_id, "model_3d.glb") == b"game"


def test_without_the_detailed_head_or_sdpose_the_step_stops_with_what_is_missing(world, monkeypatch):  # noqa: F811
    lib, npc_id, _run = world
    monkeypatch.setattr(headgraft, "availability", lambda cfg, comfy: {"missing": []})
    with pytest.raises(ComfyError, match="make it first"):
        _refine(lib, npc_id)
    _give_a_head(lib, npc_id)
    monkeypatch.setattr(headgraft, "availability", lambda cfg, comfy: {"missing": ["SDPose in ComfyUI"]})
    with pytest.raises(ComfyError, match="Attaching the detailed head needs SDPose in ComfyUI"):
        _refine(lib, npc_id)


def test_a_character_with_glasses_is_told_the_frames_will_show_twice(world, monkeypatch):  # noqa: F811
    lib, npc_id, _run = world
    _give_a_head(lib, npc_id)
    lib.update(npc_id, lambda n: n["appearance"].update(glasses="thin round glasses"))
    monkeypatch.setattr(headgraft, "availability", lambda cfg, comfy: {"missing": []})
    monkeypatch.setattr(cleanup3d, "refine", _fake_refine({"fit": {"points": 31}, "patch_faces": 40000}, {}))
    step, refined = _refine(lib, npc_id)
    assert refined["head"]["glasses"] == "thin round glasses" and refined["head"]["patch_faces"] == 40000
    assert "wears glasses (thin round glasses)" in step["note"] and "both show" in step["note"]


def test_a_character_without_glasses_gets_no_such_note(world, monkeypatch):  # noqa: F811
    lib, npc_id, _run = world
    _give_a_head(lib, npc_id)
    lib.update(npc_id, lambda n: n["appearance"].update(glasses=""))
    monkeypatch.setattr(headgraft, "availability", lambda cfg, comfy: {"missing": []})
    monkeypatch.setattr(cleanup3d, "refine", _fake_refine({"fit": {"points": 31}, "patch_faces": 40000}, {}))
    step, refined = _refine(lib, npc_id)
    assert "glasses" not in refined["head"] and "note" not in step


def _bare_model_and_head(lib, npc_id, head_bare=True):
    """The model made from copies without the glasses (and the head drawn with or without them)."""
    for view in ("front", "side_left", "back", "side_right"):
        lib.write_asset(npc_id, f"sheet/no_glasses/{view}.png", b"bare-" + view.encode())

    def seed(n):
        n["appearance"]["glasses"] = "thin round glasses"
        n["assets"]["model_3d"]["glasses_off"] = {"worn": "thin round glasses", "pictures": {
            v: f"sheet/no_glasses/{v}.png" for v in ("front", "side_left", "back", "side_right")}}
        n["assets"]["head_3d"] = {"file": "head_3d.glb", "created": "c9", **({"glasses_off": True} if head_bare else {})}
    lib.write_asset(npc_id, "head_3d.glb", b"HEAD")
    lib.update(npc_id, seed)


def test_a_model_made_without_the_glasses_is_cleaned_up_and_painted_from_the_copies_and_not_warned(world, monkeypatch):  # noqa: F811
    lib, npc_id, _run = world
    _bare_model_and_head(lib, npc_id)
    seen = {}

    def fake(cfg, model, views, faces=None, cancel=None, closeups=None, eyes=None, hair=None, hands=None, head=None):
        seen["views"] = views
        return b"game", None, {"inner_skin": {"faces_before": 10, "faces_removed": 2}, "game_version": {"faces_after": 8},
                               "paint": {"views": {}}, "head": {"fit": {"points": 31}, "patch_faces": 40000}}
    monkeypatch.setattr(headgraft, "availability", lambda cfg, comfy: {"missing": []})
    monkeypatch.setattr(cleanup3d, "refine", fake)
    step, refined = _refine(lib, npc_id)
    assert seen["views"] == {v: b"bare-" + v.encode() for v in ("front", "side_left", "back", "side_right")}
    assert "note" not in step and refined["head"]["glasses"] == "thin round glasses"


def test_a_head_drawn_with_the_glasses_on_a_model_made_without_them_is_said_so(world, monkeypatch):  # noqa: F811
    lib, npc_id, _run = world
    _bare_model_and_head(lib, npc_id, head_bare=False)
    monkeypatch.setattr(headgraft, "availability", lambda cfg, comfy: {"missing": []})
    monkeypatch.setattr(cleanup3d, "refine", _fake_refine({"fit": {"points": 31}, "patch_faces": 40000}, {}))
    step, _refined = _refine(lib, npc_id)
    assert "made without them but the detailed head was drawn with them" in step["note"] and "Draw the head again" in step["note"]
