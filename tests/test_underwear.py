"""npcgen/underwear.py: plain underwear as a thin shell of the body's own triangles whose edges are the alpha of its own texture. What it leaves alone, how it is skinned,
where it ends (looked up in its own texture), who wears what, what it refuses, and that it goes on every shape of body. Needs the Body Designer's package for its bodies."""
import io
import json
import shutil
import struct
import subprocess

import numpy as np
import pytest
from PIL import Image

from npcgen import config, underwear
from npcgen.paint3d import Glb

FEMALE, MALE = "forge-anatomical-female-v7", "forge-anatomical-male-v7"


def designer():
    try:
        from bodydesigner import api
    except ImportError:
        return None
    return api


needs_designer = pytest.mark.skipif(designer() is None, reason="the Body Designer is not installed")


def build(base, **values):
    api = designer()
    recipe = api.default_recipe(base)
    recipe["values"].update(values)
    glb, _ = api.build_game_glb(api.clean_recipe(recipe), skeleton="low", faces=100000, texture=512)
    return glb


@pytest.fixture(scope="module")
def bodies():
    return {"female": build(FEMALE), "male": build(MALE)}


def mesh(blob, name):
    """The arrays of a named mesh of a GLB, and its material."""
    glb = Glb(blob)
    doc = glb.json
    index = next(i for i, m in enumerate(doc["meshes"]) if m["name"] == name)
    prim = doc["meshes"][index]["primitives"][0]
    a = prim["attributes"]
    return {"points": glb.accessor(a["POSITION"]).astype(float), "uv": glb.accessor(a["TEXCOORD_0"]).astype(float), "joints": glb.accessor(a["JOINTS_0"]).astype(int),
            "weights": glb.accessor(a["WEIGHTS_0"]).astype(float), "triangles": glb.accessor(prim["indices"]).astype(int).reshape(-1, 3),
            "material": doc["materials"][prim["material"]], "glb": glb, "doc": doc}


def heads(blob):
    glb = Glb(blob)
    skin = glb.json["skins"][0]
    names = [glb.json["nodes"][j]["name"] for j in skin["joints"]]
    inverse = glb.accessor(skin["inverseBindMatrices"]).astype(float).reshape(-1, 4, 4).transpose(0, 2, 1)
    return dict(zip(names, np.linalg.inv(inverse)[:, :3, 3]))


def texture(dressed):
    glb = Glb(dressed)
    image = next(im for im in glb.json["images"] if im["name"] == underwear.NAME)
    return np.asarray(Image.open(io.BytesIO(glb.view_bytes(image["bufferView"]))).convert("RGBA"))


def alpha_at(body, rgba, x, y, front=True):
    """The shell texture's alpha where the body's surface is at (x, y) on its front (or back): looked up at the UV of the front-most (or back-most) of the 16 body vertices nearest (x, y)."""
    pts = body["points"]
    near = np.argsort(np.hypot(pts[:, 0] - x, pts[:, 1] - y))[:16]
    k = near[np.argmax(pts[near, 2]) if front else np.argmin(pts[near, 2])]
    size = rgba.shape[0]
    u, v = body["uv"][k]
    return int(rgba[min(int(v * size), size - 1), min(int(u * size), size - 1), 3])


# ---- who wears what, and what is refused ------------------------------------------------------------------------------------------------------
def test_the_pieces_go_by_the_bodys_sex():
    assert underwear.kinds_for("female") == ["briefs", "bandeau"] and underwear.kinds_for("male") == ["briefs"]
    assert underwear.kinds_for(None) == [] and underwear.kinds_for("robot") == []
    spec = underwear.data()
    assert 0.5 <= spec["offset_mm"] <= 4 and spec["texture"] in (512, 1024, 2048) and spec["ramp_mm"] < spec["edge_mm"]
    for kind, entry in spec["kinds"].items():
        assert entry["sexes"] and set(entry["sexes"]) <= {"female", "male"} and entry["label"], kind


@needs_designer
def test_nothing_to_wear_gives_the_body_back_and_what_cannot_wear_it_is_refused(bodies):
    body = bodies["female"]
    assert underwear.dress(body, []) == (body, {"kinds": [], "triangles": 0, "vertices": 0, "bytes_added": 0}) and underwear.dress(body, [])[0] is body
    with pytest.raises(ValueError, match="'thong' is not one of briefs, bandeau"):
        underwear.dress(body, ["briefs", "thong"])
    with pytest.raises(ValueError, match="underwear.colour"):
        underwear.dress(body, ["briefs"], colour="red")
    dressed, _ = underwear.dress(body, ["briefs"])
    assert underwear.worn(dressed) and not underwear.worn(body)
    with pytest.raises(ValueError, match="already wears"):
        underwear.dress(dressed, ["briefs"])
    with pytest.raises(ValueError, match="not a GLB"):
        underwear.dress(b"nope", ["briefs"])
    glb = Glb(body)                                                                                             # a body with no skeleton, and one with a morph target
    glb.json["skins"] = []
    with pytest.raises(ValueError, match="single skeleton"):
        underwear.dress(glb.dumps(), ["briefs"])
    glb = Glb(body)
    glb.json["meshes"][0]["primitives"][0]["targets"] = [{"POSITION": 0}]
    with pytest.raises(ValueError, match="morph targets"):
        underwear.dress(glb.dumps(), ["briefs"])
    glb = Glb(body)
    next(n for n in glb.json["nodes"] if n.get("name") == "spine_03")["name"] = "chest"
    with pytest.raises(ValueError, match="no spine_03"):
        underwear.dress(glb.dumps(), ["briefs"])


# ---- what it leaves alone, how it is skinned -------------------------------------------------------------------------------------------------
@needs_designer
def test_a_body_is_dressed_and_everything_it_had_is_left_exactly_as_it_was(bodies):
    body = bodies["female"]
    dressed, info = underwear.dress(body, ["briefs", "bandeau"])
    before, after = Glb(body), Glb(dressed)
    assert after.json["asset"] == before.json["asset"] and after.json["skins"] == before.json["skins"]
    assert after.json["nodes"][:len(before.json["nodes"])] == before.json["nodes"] and len(after.json["nodes"]) == len(before.json["nodes"]) + 1
    assert [m["name"] for m in after.json["meshes"]] == [m["name"] for m in before.json["meshes"]] + [underwear.NAME]
    for index, original in enumerate(before.json["meshes"]):                                                    # every attribute of every mesh it had, value for value
        for key, accessor in original["primitives"][0]["attributes"].items():
            assert np.array_equal(before.accessor(accessor), after.accessor(after.json["meshes"][index]["primitives"][0]["attributes"][key])), (original["name"], key)
        assert np.array_equal(before.accessor(original["primitives"][0]["indices"]), after.accessor(after.json["meshes"][index]["primitives"][0]["indices"]))
    assert after.json["scenes"] == [{"nodes": before.json["scenes"][0]["nodes"] + [len(before.json["nodes"])]}]
    node = after.json["nodes"][-1]
    assert node == {"name": underwear.NAME, "mesh": len(before.json["meshes"]), "skin": 0}
    assert info["layer"] == "underwear" and info["kinds"] == ["briefs", "bandeau"] and info["bytes_added"] == len(dressed) - len(body)
    assert 1000 < info["triangles"] < 3000 and info["bytes_added"] < 150_000                                     # a small layer, not a second body


@needs_designer
def test_the_shell_is_skinned_exactly_like_the_body_and_stands_the_offset_off_it(bodies):
    body = mesh(bodies["female"], "ForgeBody")
    dressed, info = underwear.dress(bodies["female"], ["briefs", "bandeau"])
    shell = mesh(dressed, underwear.NAME)
    offset = underwear.data()["offset_mm"] / 1000
    assert len(shell["points"]) == info["vertices"] and len(shell["triangles"]) == info["triangles"] and shell["triangles"].max() < len(shell["points"])
    near = []
    for start in range(0, len(shell["points"]), 400):                                                           # every shell vertex against every body vertex
        d = np.linalg.norm(shell["points"][start:start + 400, None, :] - body["points"][None, :, :], axis=2)
        for row, within in zip(range(start, start + len(d)), d <= offset + 1e-5):
            near.append(np.flatnonzero(within))
    assert all(len(n) for n in near)                                                                            # none stands further off the body than the offset
    distance = np.array([np.linalg.norm(shell["points"][i] - body["points"][n], axis=1).max() for i, n in enumerate(near)])
    assert np.median(distance) == pytest.approx(offset, abs=2e-5)                                               # and it stands on it
    source = []
    for i, candidates in enumerate(near):                                                                       # the body's own joints, weights and UV: some vertex within the offset has them all
        same = [k for k in candidates if np.array_equal(shell["weights"][i], body["weights"][k]) and np.array_equal(shell["joints"][i], np.where(body["weights"][k] == 0, 0, body["joints"][k]))
                and np.allclose(shell["uv"][i], body["uv"][k])]
        assert same, i
        source.append(min(same, key=lambda k: abs(np.linalg.norm(shell["points"][i] - body["points"][k]) - offset)))
    source = np.array(source)
    radial = body["points"][source] - np.array([0.0, 0.0, heads(bodies["female"])["spine_01"][2]])
    radial[:, 1] = 0.0                                                                                          # out is away from the body's own vertical middle: true of most of a shell on a torso
    assert (((shell["points"] - body["points"][source]) * radial).sum(1) > 0).mean() > 0.85
    distinct = lambda a: len(np.unique(np.rint(a * 1e6).astype(np.int64), axis=0))
    assert distinct(shell["points"]) == distinct(body["points"][source])                                        # the copies of a vertex at a UV seam stay together: no seam opens
    assert shell["material"]["alphaMode"] == "MASK" and shell["material"]["alphaCutoff"] == 0.5 and shell["material"]["doubleSided"] is False
    rgba = texture(dressed)
    assert rgba.shape == (underwear.data()["texture"],) * 2 + (4,) and 0.01 < (rgba[..., 3] > 127).mean() < 0.25


# ---- where it ends ----------------------------------------------------------------------------------------------------------------------------
def points_to_check(blob, sex):
    """(covered, uncovered) lists of (x, y, front) on the body, from its own joints: where a pair of briefs and a bandeau must and must not be."""
    h = heads(blob)
    hip, waist, chest = h["thigh_l"], h["spine_01"], h["spine_03"]
    arm = (h["upperarm_l"] + h["lowerarm_l"]) / 2                                                               # the middle of the upper arm, wherever the pose holds it
    thigh = (h["thigh_l"] + h["calf_l"]) / 2                                                                    # and of the thigh
    covered = [(0.0, hip[1] - 0.06, True), (0.04, hip[1] - 0.02, False), (0.0, hip[1] - 0.06, False)]            # the front and the back of the briefs
    low = h["thigh_l"] + (h["calf_l"] - h["thigh_l"]) * 0.3                                                      # a hand's breadth below the crease, on the thigh
    uncovered = [(0.0, waist[1] + 0.13, True), (0.0, waist[1] + 0.13, False), (thigh[0], thigh[1], True), (thigh[0], thigh[1], False),         # the belly, the thigh
                 (low[0], low[1], True), (low[0], low[1], False),
                 (arm[0], arm[1], True), (0.0, chest[1] + 0.22, True)]                                                                               # the arm, the neck
    if sex == "female":
        covered += [(0.05, chest[1] - 0.04, True), (0.0, chest[1] - 0.04, False)]                              # the bust, and the back at its height
    else:
        uncovered += [(0.05, chest[1] - 0.04, True), (0.0, chest[1] - 0.04, False)]                            # the male chest is bare
    return covered, uncovered


def chunks(blob):
    """(JSON chunk length, BIN chunk length) of a GLB."""
    json_length = struct.unpack_from("<I", blob, 12)[0]
    return json_length, struct.unpack_from("<I", blob, 20 + json_length)[0]


def validator():
    node, module = shutil.which("node"), config.ROOT / "local" / "body-designer-qa" / "node_modules" / "gltf-validator"
    return (node, module) if node and module.exists() else None


def khronos(path):
    """The Khronos glTF validator's (errors, warnings, infos, hints) for a file, or None where it is not installed."""
    if validator() is None:
        return None
    node, module = validator()
    script = "const v=require(process.argv[1]),fs=require('fs');v.validateBytes(new Uint8Array(fs.readFileSync(process.argv[2])),{maxIssues:1000}).then(r=>console.log(JSON.stringify(r.issues)))"
    issues = json.loads(subprocess.run([node, "-e", script, str(module), str(path)], capture_output=True, text=True).stdout)
    return tuple(issues[k] for k in ("numErrors", "numWarnings", "numInfos", "numHints"))


def check_coverage(blob, sex, tmp_path=None):
    dressed, _ = underwear.dress(blob, underwear.kinds_for(sex))
    assert chunks(dressed)[1] % 4 == 0 and chunks(blob)[1] % 4 == 0                                            # (a triangle count that is odd must not leave the binary chunk unaligned)
    if tmp_path is not None and validator() is not None:
        path = tmp_path / f"{sex}-{len(dressed)}.glb"
        path.write_bytes(dressed)
        assert khronos(path) == (0, 0, 0, 0), (sex, khronos(path))
    body, rgba = mesh(blob, "ForgeBody"), texture(dressed)
    covered, uncovered = points_to_check(blob, sex)
    assert all(alpha_at(body, rgba, *p) >= 250 for p in covered), [(p, alpha_at(body, rgba, *p)) for p in covered]
    assert all(alpha_at(body, rgba, *p) <= 5 for p in uncovered), [(p, alpha_at(body, rgba, *p)) for p in uncovered]


@needs_designer
def test_the_garment_covers_what_it_should_and_nothing_else_looked_up_in_its_own_texture(bodies):
    check_coverage(bodies["female"], "female")
    check_coverage(bodies["male"], "male")
    dressed, _ = underwear.dress(bodies["female"], ["briefs"])                                                  # a piece alone covers only itself
    body, rgba = mesh(bodies["female"], "ForgeBody"), texture(dressed)
    covered, _ = points_to_check(bodies["female"], "female")
    assert alpha_at(body, rgba, *covered[0]) >= 250 and alpha_at(body, rgba, *covered[-2]) <= 5


@needs_designer
def test_it_goes_on_every_shape_of_body_and_the_colour_asked_for_is_the_colour(bodies, tmp_path):
    shapes = [("female", build(FEMALE, height=1, fat=1, hips=1, chest=1, waist=1, limbs=1)), ("female", build(FEMALE, height=-1, fat=-1, chest=-1, hips=-1, limbs=-1)),
              ("female", build(FEMALE, muscle=1, shoulders=1, chest=1, limbs=1, legs=1)), ("male", build(MALE, height=1, fat=1, waist=1, hips=1, limbs=1)),
              ("male", build(MALE, height=-1, fat=-1, shoulders=-1, limbs=-1))]
    for sex, blob in shapes:
        check_coverage(blob, sex, tmp_path)
        assert 300 < underwear.dress(blob, underwear.kinds_for(sex))[1]["triangles"] < 3000
    dressed, info = underwear.dress(bodies["male"], ["briefs"], colour="#aa5533")
    rgba = texture(dressed)
    solid = rgba[rgba[..., 3] == 255][:, :3]
    assert info["colour"] == "#aa5533" and len(solid) > 1000
    middle = np.median(solid, axis=0)
    assert abs(middle[0] - 0xaa) < 3 and abs(middle[1] - 0x55) < 3 and abs(middle[2] - 0x33) < 3                  # (the elastic edge is darker, the rest is the colour)
    assert solid.max(0).tolist() == [0xaa, 0x55, 0x33] and solid.min(0)[0] < 0xaa * 0.9                              # (an elastic edge, darker than the cloth)
    assert underwear.dress(bodies["male"], ["briefs", "briefs"]) == underwear.dress(bodies["male"], ["briefs"])


@needs_designer
def test_the_alpha_rises_over_a_few_texels_so_that_the_edges_are_not_stair_steps(bodies):
    for sex in ("female", "male"):
        dressed, _ = underwear.dress(bodies[sex], underwear.kinds_for(sex))
        alpha = texture(dressed)[..., 3].astype(int)
        partial, opaque = ((alpha > 10) & (alpha < 245)).sum(), (alpha >= 245).sum()
        assert partial > 0.04 * opaque, (sex, partial, opaque)                  # an edge that rises in less than a texel gives about 0.015 of that, and the cutoff shows the texels as steps


@needs_designer
def test_the_same_body_always_gets_the_same_file_and_it_works_on_the_animated_one_too(bodies):
    one, two = underwear.dress(bodies["female"], ["briefs", "bandeau"]), underwear.dress(bodies["female"], ["briefs", "bandeau"])
    assert one[0] == two[0] and one[1] == two[1]
    glb = Glb(bodies["female"])                                                                                 # a body with clips: they come through, and the nodes are the same
    glb.json["animations"] = [{"name": "Idle", "channels": [], "samplers": []}]
    animated = glb.dumps()
    dressed, _ = underwear.dress(animated, ["briefs"])
    after = Glb(dressed)
    assert [a["name"] for a in after.json["animations"]] == ["Idle"] and underwear.worn(dressed)
    assert after.json["nodes"][:-1] == Glb(animated).json["nodes"]
