"""The up check (Phase 6 follow-up of the creature maker): a static creature's game model is compared with its concept picture's creature
(a matte: BiRefNet's, made in ComfyUI), standing and turned over, and the way that matches better is its way up. Nothing here runs Blender
or ComfyUI: each body is one side outline, drawn as a matte and as triangles, and a GLB is written by hand for the reader test."""
import json
import struct

import numpy as np
import pytest
from PIL import Image, ImageDraw

from npcgen import up_check

SCALE = 100          # matte pixels per unit of the outline


def ellipse(center, radii, n=24):
    cu, cv = center
    ru, rv = radii
    return [(cu + ru * np.cos(t), cv + rv * np.sin(t)) for t in np.linspace(0, 2 * np.pi, n, endpoint=False)]


def rect(u0, v0, u1, v1):
    return [(u0, v0), (u1, v0), (u1, v1), (u0, v1)]


def hound(crest=False):
    """A body on four legs, its head at the front, and optionally a tall thin crest on its back: convex parts only."""
    parts = [ellipse((0.0, 0.6), (1.0, 0.3)), ellipse((1.15, 0.8), (0.25, 0.18))]
    parts += [rect(x - 0.05, 0.0, x + 0.05, 0.45) for x in (-0.7, -0.3, 0.3, 0.7)]
    if crest:
        parts.append([(-0.3, 0.85), (-0.1, 0.85), (-0.2, 1.7)])
    return parts


WINGS = [(-1.2, 0.8), (1.2, 0.8), (0.0, 1.9)]        # a raised pair above the back, drawn as one wide shape


def draw_matte(parts, path=None):
    """The creature as a BiRefNet-style matte: white where the creature is, black elsewhere, upright, as the concept picture is."""
    matte = Image.new("L", (400, 320), 0)
    draw = ImageDraw.Draw(matte)
    for poly in parts:
        draw.polygon([(200 + u * SCALE, 300 - v * SCALE) for u, v in poly], fill=255)
    if path is not None:
        matte.save(path)
    return matte


def triangles(parts, turned_over=False, scale=1.0, offset=(0.0, 0.0, 0.0)):
    """The outline as glTF triangles (x, y, z): height is y, length is z, and turned over means the height is negative."""
    out = []
    for poly in parts:
        for i in range(1, len(poly) - 1):
            tri = []
            for u, v in (poly[0], poly[i], poly[i + 1]):
                y = -v if turned_over else v
                tri.append([offset[0], scale * y + offset[1], scale * u + offset[2]])
            out.append(tri)
    return np.array(out, dtype=float)


def write_glb(path, tris):
    positions = np.asarray(tris, dtype=np.float32).reshape(-1, 3)
    indices = np.arange(len(positions), dtype=np.uint32)
    binary = positions.tobytes() + indices.tobytes()
    gltf = {"asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0}],
            "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "indices": 1}]}],
            "accessors": [{"bufferView": 0, "componentType": 5126, "count": len(positions), "type": "VEC3"},
                          {"bufferView": 1, "componentType": 5125, "count": len(indices), "type": "SCALAR"}],
            "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": positions.nbytes},
                            {"buffer": 0, "byteOffset": positions.nbytes, "byteLength": indices.nbytes}],
            "buffers": [{"byteLength": len(binary)}]}
    text = json.dumps(gltf).encode("utf-8")
    text += b" " * (-len(text) % 4)
    binary += b"\0" * (-len(binary) % 4)
    body = struct.pack("<I4s", len(text), b"JSON") + text + struct.pack("<I4s", len(binary), b"BIN\0") + binary
    path.write_bytes(struct.pack("<4sII", b"glTF", 2, 12 + len(body)) + body)
    return path


def verdict(parts, model_parts=None, turned_over=False):
    """The verdict for the picture of `parts` against a model of `model_parts` (the same body unless given)."""
    mask = up_check.mask_of(draw_matte(parts))
    return up_check.compare(mask, triangles(model_parts or parts, turned_over=turned_over))["verdict"]


def test_a_body_standing_on_its_legs_matches_its_picture_the_right_way_up():
    result = up_check.compare(up_check.mask_of(draw_matte(hound(crest=True))), triangles(hound(crest=True)))
    assert result["verdict"] == "confirmed"
    assert result["standing"] > result["turned_over"] + up_check.MARGIN


def test_the_same_body_turned_over_matches_its_picture_the_wrong_way():
    assert verdict(hound(crest=True), turned_over=True) == "contradicted"


@pytest.mark.parametrize("parts", [hound(crest=True), hound() + [WINGS]], ids=["crest", "raised-wings"])
def test_a_tall_crest_or_raised_wings_do_not_turn_the_check_over(parts):
    # Heavy above the legs, as a raised crest or wings are: the picture shows them too, so the standing body still matches it.
    assert verdict(parts) == "confirmed"
    assert verdict(parts, turned_over=True) == "contradicted"


def test_a_body_that_looks_the_same_both_ways_is_unclear():
    assert verdict([ellipse((0.0, 0.6), (1.0, 0.3))]) == "unclear"


def test_the_size_and_place_of_the_model_do_not_change_the_verdict():
    mask = up_check.mask_of(draw_matte(hound()))
    moved = triangles(hound(), scale=3.0, offset=(5.0, -2.0, 1.0))
    assert up_check.compare(mask, moved)["verdict"] == "confirmed"


def test_a_matte_with_no_creature_in_it_is_not_checked(tmp_path):
    empty = Image.new("L", (64, 64), 0)
    assert up_check.mask_of(empty) is None
    result = up_check.check(None, write_glb(tmp_path / "model.glb", triangles(hound())))
    assert result["verdict"] is None and "no creature" in result["reason"]


def test_a_matte_is_read_with_its_grey_levels_and_the_creature_is_white_enough():
    matte = Image.new("L", (4, 1), 0)
    matte.putdata([0, 120, 200, 255])                      # 120 is below half and so not the creature; 200 is
    assert up_check.mask_of(matte).tolist() == [[False, False, True, True]]


def test_the_model_is_read_from_its_glb_and_checked_against_the_picture(tmp_path):
    mask = up_check.mask_of(draw_matte(hound(crest=True)))
    standing = write_glb(tmp_path / "standing.glb", triangles(hound(crest=True)))
    upside = write_glb(tmp_path / "upside.glb", triangles(hound(crest=True), turned_over=True))
    assert np.allclose(up_check.triangles_of(standing), triangles(hound(crest=True)), atol=1e-5)
    assert up_check.check(mask, standing)["verdict"] == "confirmed"
    assert up_check.check(mask, upside)["verdict"] == "contradicted"


def test_a_file_that_is_not_a_glb_is_not_checked_and_says_why(tmp_path):
    not_glb = tmp_path / "model.glb"
    not_glb.write_bytes(b"glb")
    result = up_check.check(up_check.mask_of(draw_matte(hound())), not_glb)
    assert result["verdict"] is None and "GLB" in result["reason"]


def test_a_model_that_is_not_there_is_not_checked_and_says_why(tmp_path):
    result = up_check.check(up_check.mask_of(draw_matte(hound())), tmp_path / "gone.glb")
    assert result["verdict"] is None and "could not be read" in result["reason"]


# ---- stray pixels and geometry (review of the Phase 6 follow-up: a speck or a floater must not decide the verdict) -------------------------

def test_a_speck_far_from_the_creature_does_not_change_the_verdict(tmp_path):
    matte = draw_matte(hound())
    matte.paste(255, (2, 2, 8, 8))                       # a 6 by 6 speck in the corner, away from the creature
    mask = up_check.mask_of(matte)
    assert up_check.compare(mask, triangles(hound()))["verdict"] == "confirmed"
    assert up_check.compare(mask, triangles(hound(), turned_over=True))["verdict"] == "contradicted"


def test_a_stray_triangle_far_from_the_body_does_not_change_the_verdict():
    stray = np.array([[[0.0, 0.5, -6.0], [0.0, 0.52, -6.0], [0.0, 0.5, -5.98]]])
    mask = up_check.mask_of(draw_matte(hound()))
    assert up_check.compare(mask, np.concatenate([triangles(hound()), stray]))["verdict"] == "confirmed"


def test_a_model_with_a_coordinate_that_is_not_a_number_is_not_checked(tmp_path):
    tris = triangles(hound())
    tris[5, 1, 1] = np.nan
    result = up_check.check(up_check.mask_of(draw_matte(hound())), write_glb(tmp_path / "nan.glb", tris))
    assert result["verdict"] is None and "not numbers" in result["reason"]


def test_a_model_with_no_triangles_is_not_checked(tmp_path):
    result = up_check.check(up_check.mask_of(draw_matte(hound())), write_glb(tmp_path / "empty.glb", np.zeros((0, 3, 3))))
    assert result["verdict"] is None and "no triangles" in result["reason"]


def test_a_glb_whose_json_is_not_an_object_is_not_checked(tmp_path):
    text = b"[]"
    body = struct.pack("<I4s", len(text), b"JSON") + text + struct.pack("<I4s", 0, b"BIN\0")
    path = tmp_path / "list.glb"
    path.write_bytes(struct.pack("<4sII", b"glTF", 2, 12 + len(body)) + body)
    result = up_check.check(up_check.mask_of(draw_matte(hound())), path)
    assert result["verdict"] is None and "not an object" in result["reason"]
