"""npcgen/bustsize.py: the bust size of a designed female body, -1 (smaller) through 0 (as the Body Designer made it) to +1 (larger). The request and the numbers it makes, the shape of the field it moves the
skin by (one apex, an oval around it, smooth to nothing at the edge, nothing behind the wall of the body, nothing where the arm pulls), and, on a real body built by the Body Designer (these tests skip
where it is not installed): that only the positions and normals of the body's own mesh change, that the move is what was asked for and is the same on both sides, that no triangle folds, what it refuses,
the exact way back to the Designer's own positions that the face shapes need, and that what comes after it (the underwear, the soft-body bones) follows the new bust. No engine is needed."""
import json
import math
import struct

import numpy as np
import pytest

import soft_support as ss
from npcgen import bodymaker, bustsize, softbody, underwear
from npcgen.bustsize import BustError
from npcgen.paint3d import Glb

needs_designer = pytest.mark.skipif(not bodymaker.bases(), reason="the Body Designer is not installed")
APEX = np.array([0.095, 1.16, 0.13])


# ---- the request ---------------------------------------------------------------------------------------------------------------------------
def test_a_bust_size_is_a_number_from_minus_one_to_one_and_nothing_means_no_change():
    for none in (None, False, 0, 0.0):
        assert bustsize.clean(none) == 0.0
    assert bustsize.clean(1) == 1.0 and type(bustsize.clean(1)) is float and bustsize.clean(-0.25) == -0.25 and bustsize.clean(-1) == -1.0
    for odd in (True, "big", [], {}, 1.01, -1.5, math.nan, math.inf):
        with pytest.raises(ValueError, match="body_3d.bust: expected a number from -1 to 1"):
            bustsize.clean(odd, "body_3d.bust")


def test_the_apex_moves_farther_forward_than_back_and_in_step_with_the_height_of_the_body():
    assert bustsize.reach(1.0) == pytest.approx(0.042) and bustsize.reach(-1.0) == pytest.approx(-0.012) and bustsize.reach(0.0) == 0.0
    assert bustsize.reach(0.5) == pytest.approx(0.021) and bustsize.reach(-0.5) == pytest.approx(-0.006)
    assert bustsize.reach(1.0, 1.1) == pytest.approx(0.042 * 1.1) and bustsize.reach(-0.5, 0.9) == pytest.approx(-0.006 * 0.9)
    with pytest.raises(ValueError):
        bustsize.reach(2.0)


def test_the_card_is_told_the_label_the_range_and_who_it_is_for():
    info = bustsize.info()
    assert info["label"] == "Bust size" and (info["min"], info["max"], info["default"]) == (-1.0, 1.0, 0.0)
    assert "female body only" in info["note"] and "making the body again" in info["note"] and "Soft-body motion" in info["note"] and "42 mm" in info["note"] and "12 mm" in info["note"]


def test_a_file_that_is_not_an_adult_female_designed_game_body_is_refused_in_words():
    good = {"export_tier": "game", "minimum_age": 18, "body_design": {"base": ss.FEMALE}}
    assert bustsize.problem(good) is None
    assert "made on a body the Body Designer made" in bustsize.problem(None) and "made on a body the Body Designer made" in bustsize.problem({**good, "export_tier": "preview"})
    assert "made on a body the Body Designer made" in bustsize.problem({k: v for k, v in good.items() if k != "body_design"})
    assert "adult" in bustsize.problem({**good, "minimum_age": 17}) and "adult" in bustsize.problem({**good, "minimum_age": "18"}) and "adult" in bustsize.problem({k: v for k, v in good.items() if k != "minimum_age"})
    assert "female body" in bustsize.problem({**good, "body_design": {"base": ss.MALE}})


# ---- the field ---------------------------------------------------------------------------------------------------------------------------------
def shift_at(x, y, z, arm=0.0, move=0.04, scale=1.0):
    """The move (3 numbers, metres) of the point (x, y, z) for a bust whose apex is APEX, whose apex moves by `move`, on a body `scale` times the default height, with `arm` of the arm's pull on the point."""
    return bustsize.displacement(np.array([[x, y, z]], float), np.array([arm]), APEX, move, scale)[0]


def test_the_move_is_all_of_it_at_the_apex_forward_and_a_fifth_of_it_down_and_the_same_on_both_sides():
    at_apex = shift_at(*APEX)
    assert at_apex == pytest.approx([0.0, -0.2 * 0.04, 0.04])
    assert shift_at(-APEX[0], APEX[1], APEX[2]) == pytest.approx(at_apex) and shift_at(APEX[0], APEX[1], APEX[2] + 0.03) == pytest.approx(at_apex)      # (the clothes over the skin get it too)
    assert shift_at(APEX[0] + 0.03, APEX[1] + 0.02, APEX[2]) == pytest.approx(shift_at(-APEX[0] - 0.03, APEX[1] + 0.02, APEX[2]))


def test_the_move_falls_smoothly_to_nothing_at_the_edge_of_an_oval_that_reaches_farther_below_than_above():
    f = bustsize.FOOTPRINT
    for point, share in (((APEX[0] + f["out"], APEX[1], APEX[2]), 0.0), ((APEX[0] - f["in"], APEX[1], APEX[2]), 0.0), ((APEX[0], APEX[1] + f["up"], APEX[2]), 0.0), ((APEX[0], APEX[1] - f["down"], APEX[2]), 0.0),
                         ((APEX[0] + f["out"] / 2, APEX[1], APEX[2]), 0.5), ((APEX[0], APEX[1] + f["up"] / 2, APEX[2]), 0.5), ((APEX[0], APEX[1] - f["down"] / 2, APEX[2]), 0.5),
                         ((APEX[0] - f["in"] / 2, APEX[1], APEX[2]), 0.5)):
        assert shift_at(*point)[2] == pytest.approx(0.04 * share, abs=1e-12)
    assert f["down"] > f["up"] and shift_at(APEX[0], APEX[1] - 0.09, APEX[2])[2] > 0 and shift_at(APEX[0], APEX[1] + 0.09, APEX[2])[2] == 0.0
    run = [shift_at(APEX[0] + t / 100, APEX[1], APEX[2])[2] for t in range(0, 10)]
    assert run == sorted(run, reverse=True) and run[0] == pytest.approx(0.04) and run[-1] == 0.0                # (down all the way: no step, no second hill)
    assert shift_at(APEX[0], APEX[1] + 0.2, APEX[2]).tolist() == [0.0, 0.0, 0.0]                                  # (farther away than the oval: not touched)


def test_nothing_behind_the_wall_of_the_body_moves_and_the_edge_of_it_fades():
    depth, fade = bustsize.WALL["depth"], bustsize.WALL["fade"]
    assert shift_at(APEX[0], APEX[1], APEX[2] - depth - 0.001).tolist() == [0.0, 0.0, 0.0]                        # (the skin of the back, the other side of the body)
    assert shift_at(APEX[0], APEX[1], APEX[2] - depth + fade / 2)[2] == pytest.approx(0.04 * 0.5)                 # (half way across the fade: half)
    assert shift_at(APEX[0], APEX[1], APEX[2] - depth + fade)[2] == pytest.approx(0.04)


def test_where_the_arm_pulls_on_a_vertex_the_move_is_eased_out_so_the_armpit_does_not_drag():
    fade = bustsize.FOREIGN_FADE
    assert shift_at(*APEX, arm=fade).tolist() == [0.0, 0.0, 0.0] and shift_at(*APEX, arm=0.9).tolist() == [0.0, 0.0, 0.0]
    assert shift_at(*APEX, arm=fade / 2)[2] == pytest.approx(0.04 * 0.5) and shift_at(*APEX, arm=0.0)[2] == pytest.approx(0.04)


def test_a_smaller_bust_moves_back_and_does_not_hang_lower_and_a_bigger_body_has_a_bigger_oval():
    small = shift_at(*APEX, move=-0.012)
    assert small[2] == pytest.approx(-0.012) and small[1] == 0.0 and small[0] == 0.0
    beyond = (APEX[0] + bustsize.FOOTPRINT["out"] * 1.05, APEX[1], APEX[2])
    assert shift_at(*beyond, scale=1.0)[2] == 0.0 and shift_at(*beyond, scale=1.1)[2] > 0.0
    wall = (APEX[0], APEX[1], APEX[2] - bustsize.WALL["depth"] * 1.02)
    assert shift_at(*wall, scale=1.0)[2] == 0.0 and shift_at(*wall, scale=1.1)[2] > 0.0                           # (the wall is deeper on a taller body too)


def test_a_triangle_the_move_turned_over_or_flattened_is_counted_and_the_others_are_not():
    tri = np.array([[0, 1, 2]])
    before = np.array([[0.0, 0, 0], [1, 0, 0], [0, 1, 0]])
    assert bustsize._flipped(before, before + np.array([[0, 0, 0.01], [0, 0, 0.01], [0, 0, 0.02]]), tri) == 0
    assert bustsize._flipped(before, np.array([[0.0, 0, 0], [1, 0, 0], [0, -1, 0]]), tri) == 1                       # (turned over)
    assert bustsize._flipped(before, np.array([[0.0, 0, 0], [1, 0, 0], [2, 0, 0]]), tri) == 1                        # (flattened to a line)


def test_the_two_sides_of_a_seam_share_one_normal_so_the_shading_has_no_crease_there():
    positions = np.array([[0.0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 0], [0, 0, 1], [1, 0, 0]])         # (a floor and a wall meeting along the x axis, each with its own copy of the ridge)
    tri = np.array([[0, 1, 2], [3, 4, 5]])
    key = np.round(positions * 1e6).astype(np.int64)
    _, weld = np.unique(key, axis=0, return_inverse=True)
    normals = bustsize._vertex_normals(positions, tri, weld.reshape(-1))
    ridge = np.array([0.0, 1.0, 1.0]) / math.sqrt(2)
    assert normals[0] == pytest.approx(ridge) and normals[3] == pytest.approx(ridge) and normals[1] == pytest.approx(ridge) and normals[5] == pytest.approx(ridge)
    assert normals[2] == pytest.approx([0.0, 0.0, 1.0]) and normals[4] == pytest.approx([0.0, 1.0, 0.0])


# ---- writing rows into the file ----------------------------------------------------------------------------------------------------------------
def interleaved(positions, normals, bounds=True):
    """A GLB of one buffer view whose POSITION and NORMAL rows lie between each other (24 bytes a vertex): a form another exporter may write (the Body Designer's own keep one attribute to a view)."""
    blob = np.concatenate([np.asarray(positions, np.float32), np.asarray(normals, np.float32)], axis=1).tobytes()
    box = {"min": np.min(positions, axis=0).astype(float).tolist(), "max": np.max(positions, axis=0).astype(float).tolist()} if bounds else {}
    doc = {"asset": {"version": "2.0"}, "buffers": [{"byteLength": len(blob)}], "bufferViews": [{"buffer": 0, "byteLength": len(blob), "byteStride": 24}],
           "accessors": [{"bufferView": 0, "byteOffset": 0, "componentType": 5126, "count": len(positions), "type": "VEC3", **box},
                         {"bufferView": 0, "byteOffset": 12, "componentType": 5126, "count": len(positions), "type": "VEC3"}]}
    raw = json.dumps(doc).encode()
    raw += b" " * (-len(raw) % 4)
    return (struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(raw) + 8 + len(blob)) + struct.pack("<II", len(raw), 0x4E4F534A) + raw + struct.pack("<II", len(blob), 0x004E4942) + blob)


def test_rows_are_written_where_they_lie_between_the_other_attributes_and_the_bounds_follow_them():
    positions = np.array([[0, 0, 0], [1, 2, 3], [-1, 5, 2]], np.float32)
    normals = np.array([[0, 0, 1], [0, 1, 0], [1, 0, 0]], np.float32)
    glb = Glb(interleaved(positions, normals))
    new = positions + np.array([[0, 0, 0.5], [0, 0, -0.25], [4, 0, 9]], np.float32)
    bustsize._write(glb, 0, new)
    again = Glb(glb.dumps())
    assert np.array_equal(again.accessor(0), new) and np.array_equal(again.accessor(1), normals)                      # (the normals lying between the rows are not touched)
    assert again.json["accessors"][0]["min"] == new.min(axis=0).tolist() and again.json["accessors"][0]["max"] == new.max(axis=0).tolist()
    assert "min" not in again.json["accessors"][1] and len(again.bin) == len(glb.bin)
    bare = Glb(interleaved(positions, normals, bounds=False))
    bustsize._write(bare, 0, new)
    assert "min" not in bare.json["accessors"][0] and np.array_equal(Glb(bare.dumps()).accessor(0), new)               # (an accessor that had no bounds is not given any)


def test_rows_that_do_not_fit_or_are_stored_another_way_are_refused_in_words():
    positions = np.zeros((3, 3), np.float32)
    glb = Glb(interleaved(positions, positions))
    with pytest.raises(BustError, match="changed size"):
        bustsize._write(glb, 0, positions[:2])
    glb.json["accessors"][0]["sparse"] = {"count": 1}
    with pytest.raises(BustError, match="stored in a form"):
        bustsize._write(glb, 0, positions)
    del glb.json["accessors"][0]["sparse"]
    glb.json["accessors"][0]["componentType"] = 5123
    with pytest.raises(BustError, match="stored in a form"):
        bustsize._write(glb, 0, positions)


# ---- real bodies -------------------------------------------------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def made():
    """The Body Designer's own default female game body (a 512 px skin: about 8 s), its recipe, and the body with a bust size of +1 and of -1 (a few hundredths of a second each)."""
    body, recipe = ss.game_body()
    return {"body": body, "recipe": recipe, "larger": bustsize.apply(body, 1.0), "smaller": bustsize.apply(body, -1.0)}


def arrays(data):
    """{pos, normal, tris, jn, wt} of the body's own mesh."""
    glb = Glb(data)
    primitive = softbody._skinned(glb.json)["ForgeBody"]
    a = primitive["attributes"]
    return {"pos": glb.accessor(a["POSITION"]).astype(float), "normal": glb.accessor(a["NORMAL"]).astype(float), "tris": glb.accessor(primitive["indices"]).reshape(-1, 3).astype(np.int64),
            "jn": glb.accessor(a["JOINTS_0"]).astype(np.int64), "wt": glb.accessor(a["WEIGHTS_0"]).astype(float)}


def arm_weight(data, rows):
    doc = Glb(data).json
    names = [doc["nodes"][j]["name"] for j in doc["skins"][0]["joints"]]
    arm = np.array([name.startswith(bustsize.ARM_BONES) for name in names])
    return (rows["wt"] * arm[rows["jn"]]).sum(axis=1)


def faces(positions, tris):
    return np.cross(positions[tris[:, 1]] - positions[tris[:, 0]], positions[tris[:, 2]] - positions[tris[:, 0]])


def edges_of(tris):
    return np.unique(np.sort(np.concatenate([tris[:, [0, 1]], tris[:, [1, 2]], tris[:, [2, 0]]]), axis=1), axis=0)


def altered(data, edit):
    glb = Glb(data)
    edit(glb.json)
    return glb.dumps()


@needs_designer
def test_a_size_of_nothing_is_the_designers_file_byte_for_byte_and_the_same_size_gives_the_same_file(made):
    body = made["body"]
    same, report = bustsize.apply(body, 0.0)
    assert same is body and report == {"size": 0.0} and bustsize.apply(body, None)[0] is body and bustsize.apply(body, False)[0] is body
    assert bustsize.apply(body, 1.0)[0] == made["larger"][0] and bustsize.apply(body, -1.0)[0] == made["smaller"][0]


@needs_designer
def test_only_the_positions_and_normals_of_the_body_mesh_change_and_the_file_keeps_its_size(made):
    before, after = Glb(made["body"]), Glb(made["larger"][0])
    primitive = softbody._skinned(before.json)["ForgeBody"]
    moved = {primitive["attributes"]["POSITION"], primitive["attributes"]["NORMAL"]}
    assert len(before.bin) == len(after.bin) and before.json["bufferViews"] == after.json["bufferViews"] and len(before.json["accessors"]) == len(after.json["accessors"])
    for i, (a, b) in enumerate(zip(before.json["accessors"], after.json["accessors"])):
        if i in moved:
            assert {k: v for k, v in a.items() if k not in ("min", "max")} == {k: v for k, v in b.items() if k not in ("min", "max")}, i
        else:
            assert a == b and ("sparse" in a or np.array_equal(before.accessor(i), after.accessor(i))), i
    position = after.json["accessors"][primitive["attributes"]["POSITION"]]
    now = after.accessor(primitive["attributes"]["POSITION"])
    assert position["min"] == pytest.approx(now.min(axis=0).tolist()) and position["max"] == pytest.approx(now.max(axis=0).tolist())      # (the bounds say where the skin is now)
    for key in before.json:
        if key not in ("accessors", "asset"):
            assert before.json[key] == after.json[key], key
    assert {k: v for k, v in after.json["asset"].items() if k != "extras"} == {k: v for k, v in before.json["asset"].items() if k != "extras"}
    kept, now = before.json["asset"]["extras"], after.json["asset"]["extras"]
    assert "forge_bust" not in kept and {k: v for k, v in now.items() if k != "forge_bust"} == kept


@needs_designer
def test_the_apex_moves_by_the_reach_and_a_larger_bust_hangs_a_little_lower_and_both_sides_alike(made):
    original = arrays(made["body"])
    for key, size in (("larger", 1.0), ("smaller", -1.0)):
        data, report = made[key]
        reach = bustsize.reach(size, report["scale"])
        shift = arrays(data)["pos"] - original["pos"]
        assert report["size"] == size and report["reach_mm"] == pytest.approx(reach * 1000, abs=0.06) and report["biggest_mm"] == pytest.approx(abs(reach) * 1000, abs=1.0)
        assert report["apex_after"][2] - report["apex"][2] == pytest.approx(reach, abs=0.003)                      # (the most forward vertex: the surface is found again, within a few mm)
        left, right = original["pos"][:, 0] > 0, original["pos"][:, 0] < 0
        assert abs(shift[left, 2].max() - shift[right, 2].max()) < 1e-4 and abs(shift[left, 2].min() - shift[right, 2].min()) < 1e-4
        assert abs(shift[left, 1].min() - shift[right, 1].min()) < 1e-4
        if size > 0:
            assert shift[:, 2].max() == pytest.approx(reach, abs=0.0005) and shift[:, 1].min() == pytest.approx(-bustsize.DROOP * reach, abs=0.0003) and shift[:, 2].min() == 0.0
        else:
            assert shift[:, 2].min() == pytest.approx(reach, abs=0.0005) and (shift[:, 1] == 0).all() and shift[:, 2].max() == 0.0          # (a smaller bust goes back, it does not lift)


@needs_designer
def test_the_size_scales_the_move_in_a_straight_line(made):
    original, full = arrays(made["body"])["pos"], arrays(made["larger"][0])["pos"]
    half = arrays(bustsize.apply(made["body"], 0.5)[0])["pos"]
    assert np.abs((half - original) - 0.5 * (full - original)).max() < 5e-7


@needs_designer
def test_only_the_front_of_the_chest_inside_the_oval_moves_not_the_back_the_arms_or_anything_far_away(made):
    original = arrays(made["body"])
    on_arm = arm_weight(made["body"], original)
    for key in ("larger", "smaller"):
        data, report = made[key]
        shift = arrays(data)["pos"] - original["pos"]
        moved = np.abs(shift).max(axis=1) > 1e-7
        apex, scale, f = np.array(report["apex"]), report["scale"], bustsize.FOOTPRINT
        assert 250 < moved.sum() < 450 and moved.sum() == report["moved"]
        pos = original["pos"][moved]
        across, up = np.abs(pos[:, 0]) - apex[0], pos[:, 1] - apex[1]
        assert (pos[:, 2] > apex[2] - bustsize.WALL["depth"] * scale).all()                                          # (nothing behind the wall: no back, no far side)
        assert (across < f["out"] * scale).all() and (across > -f["in"] * scale).all() and (up < f["up"] * scale).all() and (up > -f["down"] * scale).all()
        assert on_arm[moved].max() < bustsize.FOREIGN_FADE and not moved[on_arm >= bustsize.FOREIGN_FADE].any()      # (the arm and the shoulder stay where they were)
        assert not moved[original["pos"][:, 1] < 1.0].any() and not moved[original["pos"][:, 1] > 1.28].any()     # (below the ribs, above the collar: untouched)


@needs_designer
def test_the_surface_changes_smoothly_with_no_step_anywhere_and_no_triangle_is_folded_or_stretched_into_a_spike(made):
    original = arrays(made["body"])
    edges = edges_of(original["tris"])
    length = np.maximum(np.linalg.norm(original["pos"][edges[:, 0]] - original["pos"][edges[:, 1]], axis=1), 1e-9)
    for key, size in (("larger", 1.0), ("smaller", -1.0)):
        data, report = made[key]
        after = arrays(data)
        shift = after["pos"] - original["pos"]
        reach = abs(bustsize.reach(size, report["scale"]))
        steep = np.linalg.norm(shift[edges[:, 0]] - shift[edges[:, 1]], axis=1) / length
        assert steep.max() < 36 * reach                                                                             # (measured 31.5 x the reach: a step would be hundreds)
        assert bustsize._flipped(original["pos"], after["pos"], original["tris"]) == 0
        old, new = faces(original["pos"], original["tris"]), faces(after["pos"], original["tris"])
        real = np.linalg.norm(old, axis=1) > 1e-9
        turn = (old * new).sum(axis=1)[real] / (np.linalg.norm(old, axis=1)[real] * np.linalg.norm(new, axis=1)[real])
        grown = np.linalg.norm(new, axis=1)[real] / np.linalg.norm(old, axis=1)[real]
        assert turn.min() > 0.6 and 0.5 < grown.min() and grown.max() < 2.6                                       # (measured: no face turns more than 42 degrees, none grows past 2.2 x or shrinks below 0.66 x)


@needs_designer
def test_the_normals_stay_unit_follow_the_new_surface_and_change_only_next_to_what_moved(made):
    original = arrays(made["body"])
    data, _report = made["larger"]
    after = arrays(data)
    assert np.abs(np.linalg.norm(after["normal"], axis=1) - 1.0).max() < 1e-5
    moved = np.flatnonzero(np.abs(after["pos"] - original["pos"]).max(axis=1) > 1e-7)
    near = np.unique(original["tris"][np.isin(original["tris"], moved).any(axis=1)])
    far = np.setdiff1d(np.arange(len(original["pos"])), near)
    assert np.abs(after["normal"][far] - original["normal"][far]).max() < 1e-6                                       # (only the 1-ring of what moved has other shading)
    turned = np.degrees(np.arccos(np.clip((after["normal"] * original["normal"]).sum(axis=1), -1.0, 1.0)))
    assert 15 < turned.max() < 50 and (turned[near] > 1.0).sum() > 100
    key = np.round(after["pos"] * 1e6).astype(np.int64)
    _, weld = np.unique(key, axis=0, return_inverse=True)
    expected = bustsize._vertex_normals(after["pos"], original["tris"], weld.reshape(-1))                            # (the Designer's own normals are these, worked out from the surface: so are the new ones)
    assert np.sum(expected * after["normal"], axis=1).min() > 0.999


@needs_designer
def test_the_file_says_it_has_a_bust_size_and_the_check_looks_for_it_where_it_says_it_is(made):
    body = made["body"]
    larger, report = made["larger"]
    mark = Glb(larger).json["asset"]["extras"]["forge_bust"]
    assert mark["size"] == 1.0 and mark["apex_after"] == pytest.approx(report["apex_after"], abs=1e-4) and mark["apex_before"] == pytest.approx(report["apex"], abs=1e-4) and mark["moved"] == report["moved"]
    assert bustsize.check_file(larger, 1.0) is None and bustsize.check_file(made["smaller"][0], -1.0) is None and bustsize.check_file(body, 0.0) is None and bustsize.check_file(body, None) is None
    assert "has not got the bust size that was asked for" in bustsize.check_file(larger, 0.5) and "has not got the bust size that was asked for" in bustsize.check_file(body, 1.0)
    assert "bust size that was not asked for" in bustsize.check_file(larger, 0.0)
    moved = altered(larger, lambda d: d["asset"]["extras"]["forge_bust"].update(apex_after=[0.0984, 1.1505, 0.1718]))                  # (5 mm farther than it is)
    assert "not where its bust size says it is" in bustsize.check_file(moved, 1.0)
    blind = altered(larger, lambda d: d["asset"]["extras"]["forge_bust"].pop("scale"))
    assert "could not be looked at" in bustsize.check_file(blind, 1.0)


@needs_designer
def test_it_refuses_a_file_that_is_not_an_adult_female_designed_game_body_with_a_skin_and_says_why(made):
    body = made["body"]
    with pytest.raises(BustError, match="female body"):
        bustsize.apply(altered(body, lambda d: d["asset"]["extras"]["body_design"].update(base=ss.MALE)), 1.0)
    with pytest.raises(BustError, match="adult"):
        bustsize.apply(altered(body, lambda d: d["asset"]["extras"].update(minimum_age=17)), 1.0)
    with pytest.raises(BustError, match="made on a body the Body Designer made"):
        bustsize.apply(altered(body, lambda d: d["asset"]["extras"].update(export_tier="preview")), 1.0)
    with pytest.raises(BustError, match="one skin"):
        bustsize.apply(altered(body, lambda d: d.pop("skins")), 1.0)
    with pytest.raises(BustError, match="own spine and pelvis bones"):
        bustsize.apply(altered(body, lambda d: next(n for n in d["nodes"] if n.get("name") == "spine_02").update(name="spine_x")), 1.0)
    with pytest.raises(BustError, match="own skin"):
        bustsize.apply(altered(body, lambda d: d["meshes"][0].update(name="Other")), 1.0)
    with pytest.raises(ValueError, match="expected a number from -1 to 1"):
        bustsize.apply(body, 2.0)
    with pytest.raises(BustError, match="already has a bust size"):
        bustsize.apply(made["larger"][0], 0.5)


@needs_designer
def test_a_size_that_would_fold_the_surface_is_refused_before_anything_is_written(made, monkeypatch):
    monkeypatch.setattr(bustsize, "_flipped", lambda before, after, tri: 7)
    with pytest.raises(BustError, match="would fold 7 triangles"):
        bustsize.apply(made["body"], 1.0)


@needs_designer
def test_the_designers_own_positions_can_be_put_back_and_the_bust_put_on_again_exactly(made):
    body, larger = made["body"], made["larger"][0]
    back = bustsize.copy_body(body, larger)                                                                          # (the way the face shapes need the file: the Designer's positions and normals)
    mine, theirs = arrays(back), arrays(body)
    assert np.array_equal(mine["pos"], theirs["pos"]) and np.array_equal(mine["normal"], theirs["normal"])
    assert len(back) == len(larger) and "forge_bust" in Glb(back).json["asset"]["extras"]                            # (nothing else of the file: the mark and the rest stay)
    assert bustsize.copy_body(larger, back) == larger                                                                # (and the bust put on again is the same file down to the byte)
    with pytest.raises(BustError, match="own skin"):
        bustsize.copy_body(altered(body, lambda d: d["meshes"][0].update(name="Other")), larger)
    with pytest.raises(BustError, match="own skin"):
        bustsize.copy_body(body, altered(larger, lambda d: d["meshes"][0].update(name="Other")))


@needs_designer
def test_the_face_shapes_are_the_same_with_a_bust_size_and_without_one(made):
    if not bodymaker.offers("expressions"):
        pytest.skip("this Body Designer has no face expressions")
    body, larger, recipe = made["body"], made["larger"][0], made["recipe"]
    wanted = bodymaker._api().clean_recipe(bodymaker.with_expressions(recipe, True))
    plain = bodymaker._shapes(body, wanted)
    both = bodymaker._shapes_on_bust(larger, body, wanted)
    a, b = Glb(plain), Glb(both)
    sparse = [i for i, acc in enumerate(a.json["accessors"]) if "sparse" in acc]
    assert len(sparse) > 10 and sparse == [i for i, acc in enumerate(b.json["accessors"]) if "sparse" in acc]
    for i in sparse:
        s, t = a.json["accessors"][i]["sparse"], b.json["accessors"][i]["sparse"]
        assert a.view_bytes(s["indices"]["bufferView"]) == b.view_bytes(t["indices"]["bufferView"]) and a.view_bytes(s["values"]["bufferView"]) == b.view_bytes(t["values"]["bufferView"])
    assert bodymaker._face_shapes(a.json) == bodymaker._face_shapes(b.json) and len(bodymaker._face_shapes(b.json)) == 13
    assert np.array_equal(arrays(both)["pos"], arrays(larger)["pos"]) and np.array_equal(arrays(both)["normal"], arrays(larger)["normal"])        # (the bust is back on the shaped file)
    assert bustsize.check_file(both, 1.0) is None and "forge_bust" in b.json["asset"]["extras"]


@needs_designer
def test_the_underwear_made_for_a_larger_bust_lies_on_the_new_skin_as_closely_as_on_the_old(made):
    body, larger, recipe = made["body"], made["larger"][0], made["recipe"]
    kinds = bodymaker.dressing(recipe)
    gaps, forward = {}, {}
    for name, data in (("original", body), ("larger", larger)):
        dressed, _worn = underwear.dress(data, kinds)
        glb = Glb(dressed)
        meshes = softbody._skinned(glb.json)
        skin = glb.accessor(meshes["ForgeBody"]["attributes"]["POSITION"]).astype(float)
        shell = glb.accessor(meshes["ForgeUnderwear"]["attributes"]["POSITION"]).astype(float)
        gaps[name] = np.array([np.linalg.norm(skin - v, axis=1).min() for v in shell[::5]])
        forward[name] = shell[:, 2].max()
    assert 0.0009 < gaps["larger"].min() and gaps["larger"].max() < 0.00185 and gaps["original"].max() < 0.00185                      # (the shell stands 1.8 mm out of the skin it is made from)
    assert forward["larger"] - forward["original"] == pytest.approx(0.041, abs=0.003)                                            # (and the bra went forward with the bust)


@needs_designer
def test_the_soft_body_bones_are_hung_on_the_bust_where_it_is_now_and_remember_the_size(made):
    body, larger = made["body"], made["larger"][0]
    out, report = softbody.apply(larger, {"chest": 1.0})
    plain, plain_report = softbody.apply(body, {"chest": 1.0})
    assert report["apex"]["chest"] == pytest.approx(made["larger"][1]["apex_after"], abs=1e-4) and plain_report["apex"]["chest"] == pytest.approx(made["larger"][1]["apex"], abs=1e-4)
    assert Glb(out).json["asset"]["extras"]["forge_soft"]["bust_size"] == 1.0 and "bust_size" not in Glb(plain).json["asset"]["extras"]["forge_soft"]
    rig, rest = ss.Rig(out), ss.Rig(plain)
    world, was = rig.world(), rest.world()

    def at(r, w, name):
        return w[r.joints[r.names.index(name)]][:3, 3]

    assert at(rig, world, "soft_chest_l_01")[1] == pytest.approx(report["apex"]["chest"][1], abs=1e-4)                  # (the bones follow the bust down: the same height as the apex)
    assert abs(at(rig, world, "soft_chest_l_01")[2] - at(rest, was, "soft_chest_l_01")[2]) < 0.003                      # (and the pivot stays in the chest wall where it was: the lever is longer)
    near = np.argmin(np.linalg.norm(rig.meshes["ForgeBody"]["pos"] - np.array(report["apex"]["chest"]), axis=1))
    assert rig.soft_share("ForgeBody", "chest")[near] > 0.75 and (rig.soft_share("ForgeBody", "chest") > 0).sum() > (rest.soft_share("ForgeBody", "chest") > 0).sum()
    smaller = made["smaller"][0]
    assert (ss.Rig(softbody.apply(smaller, {"chest": 1.0})[0]).soft_share("ForgeBody", "chest") > 0).sum() < (rest.soft_share("ForgeBody", "chest") > 0).sum()          # (a smaller bust: a smaller patch)
    assert softbody.check_file(out, {"chest"}) is None


# ---- a body the Body Designer's body shape (bodydesigner/body_design.py) made: the bust is marked where it now is --------------------------------------------------
def shape_made(made):
    """(the bust size's larger body with its mark taken away: a stand-in for a body another step has shaped, the neutral body, how far the tip of the bust moved: shift (down is negative) and reach (forward), metres)."""
    report = made["larger"][1]
    before, after = np.array(report["apex"]), np.array(report["apex_after"])
    return altered(made["larger"][0], lambda d: d["asset"]["extras"].pop("forge_bust")), made["body"], float(after[1] - before[1]), float(after[2] - before[2])


@needs_designer
def test_the_bust_of_a_body_shape_is_marked_where_the_tip_of_it_now_is_and_the_check_looks_there(made):
    shaped, body, shift, reach = shape_made(made)
    marked, said = bustsize.mark_shape(shaped, body, 1.0, shift, reach, 330)
    mark = Glb(marked).json["asset"]["extras"]["forge_bust"]
    assert mark["version"] == 2 and mark["source"] == "body_shape" and mark["size"] == 1.0 and mark["moved"] == 330 and mark["scale"] == pytest.approx(made["larger"][1]["scale"], abs=1e-4)
    assert mark["window_shift_m"] == round(shift, 5) and mark["reach_m"] == round(reach, 5) and mark["apex_before"] == pytest.approx(made["larger"][1]["apex"], abs=1e-4)
    assert mark["apex_after"] == pytest.approx(made["larger"][1]["apex_after"], abs=0.005)                       # (found in the window moved with the bust: the same tip of it, give or take a few millimetres)
    assert said == {"size": 1.0, "reach_mm": pytest.approx(reach * 1000, abs=0.1), "drop_mm": pytest.approx(-shift * 1000, abs=0.1), "apex": pytest.approx(made["larger"][1]["apex"], abs=1e-4),
                    "apex_after": pytest.approx(mark["apex_after"], abs=1e-4)}
    assert Glb(marked).bin == Glb(shaped).bin                                                                  # (nothing of the body moves here: only the file's extras say where the bust is)
    assert bustsize.check_file(marked, 1.0, shaped=True) is None
    assert "body shape's bust that was asked for" in bustsize.check_file(marked, 0.5, shaped=True)              # (another size)
    assert "bust size that was not asked for" in bustsize.check_file(marked, 1.0) and "bust size that was not asked for" in bustsize.check_file(marked, 0.0)      # (a body shape's mark is not a bust size of the old kind)
    assert "body shape's bust that was asked for" in bustsize.check_file(made["larger"][0], 1.0, shaped=True)   # (and a bust size of the old kind is not a body shape's)
    assert "body shape's bust that was asked for" in bustsize.check_file(body, 0.0, shaped=True)                # (a body shape of size 0 has its mark too: its other controls move the bust)
    farther = altered(marked, lambda d: d["asset"]["extras"]["forge_bust"].update(apex_after=[0.0984, 1.1505, 0.1718]))
    assert "not where its bust size says it is" in bustsize.check_file(farther, 1.0, shaped=True)
    big = altered(marked, lambda d: d["asset"]["extras"]["forge_bust"].update(size=3.0))                         # (a body shape takes a bust size up to 3; the bust size of the old kind stops at 1)
    assert bustsize.check_file(big, 3.0, shaped=True) is None
    with pytest.raises(ValueError, match="expected a number from -1 to 1"):
        bustsize.check_file(big, 3.0)
    with pytest.raises(ValueError, match="expected a number from -1 to 3"):
        bustsize.check_file(big, 3.5, shaped=True)


@needs_designer
def test_a_body_shape_mark_is_refused_in_words_for_a_file_that_cannot_take_it(made):
    shaped, body, shift, reach = shape_made(made)
    with pytest.raises(BustError, match="already has a bust size"):
        bustsize.mark_shape(made["larger"][0], body, 1.0, shift, reach)
    for bad in (float("nan"), "far", None):
        with pytest.raises(BustError, match="shift is not a number"):
            bustsize.mark_shape(shaped, body, 1.0, bad, reach)
        with pytest.raises(BustError, match="reach is not a number"):
            bustsize.mark_shape(shaped, body, 1.0, shift, bad)
    with pytest.raises(ValueError, match="expected a number from -1 to 3"):
        bustsize.mark_shape(shaped, body, 3.5, shift, reach)
    with pytest.raises(BustError, match="female body"):
        bustsize.mark_shape(altered(shaped, lambda d: d["asset"]["extras"]["body_design"].update(base=ss.MALE)), body, 1.0, shift, reach)
    with pytest.raises(BustError, match="one skin"):
        bustsize.mark_shape(altered(shaped, lambda d: d.pop("skins")), body, 1.0, shift, reach)
    with pytest.raises(BustError, match="own skin"):
        bustsize.mark_shape(altered(shaped, lambda d: d["meshes"][0].update(name="Other")), body, 1.0, shift, reach)
    with pytest.raises(BustError, match="own skin"):
        bustsize.mark_shape(shaped, altered(body, lambda d: d["meshes"][0].update(name="Other")), 1.0, shift, reach)
    with pytest.raises(BustError, match="own spine and pelvis bones"):
        bustsize.mark_shape(altered(shaped, lambda d: next(n for n in d["nodes"] if n.get("name") == "spine_02").update(name="spine_x")), body, 1.0, shift, reach)


@needs_designer
def test_the_soft_body_bones_are_hung_on_the_bust_of_a_body_shape_where_its_mark_says(made):
    shaped, body, shift, reach = shape_made(made)
    marked, _said = bustsize.mark_shape(shaped, body, 1.0, shift, reach, 330)
    out, report = softbody.apply(marked, {"chest": 1.0})
    mark = Glb(marked).json["asset"]["extras"]["forge_bust"]
    assert report["apex"]["chest"] == pytest.approx(mark["apex_after"], abs=1e-4)                              # (found where the mark says: the window moved with the bust)
    assert Glb(out).json["asset"]["extras"]["forge_soft"]["bust_size"] == 1.0 and softbody.check_file(out, {"chest"}) is None
    assert bustsize.check_file(out, 1.0, shaped=True) is None                                                 # (the soft bones leave the mark and the bust as they were)


needs_shape = pytest.mark.skipif(not bodymaker.bases() or bodymaker.body_shape_api() is None, reason="this Body Designer has no body document")


@needs_shape
def test_a_bust_that_hangs_below_the_window_of_the_default_body_is_found_where_it_is_by_the_shape_mark(made):
    from bodydesigner import body_design
    block = body_design.default_body(ss.FEMALE)
    block["bust"] = 3.0
    block["breast"]["sag"] = 1.0                                                                                  # (the lowest the Designer's controls hang it: the tip lies 73 mm below the floor of the default window)
    marked, report = bodymaker._body_shape(made["body"], made["recipe"], block, "low")
    glb = Glb(marked)
    mark = glb.json["asset"]["extras"]["forge_bust"]
    assert mark["source"] == "body_shape" and mark["size"] == 3.0 and mark["window_shift_m"] < -0.1 and report["drop_mm"] > 100
    doc = glb.json
    world = softbody._worlds(doc)
    bones = {doc["nodes"][j]["name"]: world[j][:3, 3] for j in doc["skins"][0]["joints"]}
    points = glb.accessor(softbody._skinned(doc)["ForgeBody"]["attributes"]["POSITION"]).astype(float)
    scale, tip = float(mark["scale"]), np.array(mark["apex_after"])
    assert np.linalg.norm(softbody._apex(points, bones, "chest", scale) - tip) > 0.05                              # (the window of the default body finds something else, 5 cm or more away)
    assert np.linalg.norm(softbody._apex(points, bones, "chest", scale, softbody.window_shift(mark)) - tip) < 1e-3
    assert bustsize.check_file(marked, 3.0, shaped=True) is None                                                  # (the check looks where the mark says)
    out, soft = softbody.apply(marked, {"chest": 1.0})
    assert soft["apex"]["chest"] == pytest.approx(mark["apex_after"], abs=1e-3)                                    # (and so do the soft-body bones)
    assert softbody.check_file(out, {"chest"}) is None and bustsize.check_file(out, 3.0, shaped=True) is None


@needs_shape
def test_a_body_shape_that_moves_nothing_of_the_chest_is_marked_where_the_default_body_has_the_bust(made):
    from bodydesigner import body_design
    block = body_design.default_body(ss.FEMALE)                                                                   # (a bust size of 0 and every other control at 0: the Designer moves no vertex of the chest and gives no place for it)
    marked, report = bodymaker._body_shape(made["body"], made["recipe"], block, "low")
    assert report["bust"] == 0.0 and report["moved"] == 0 and report["reach_mm"] == 0.0 and report["drop_mm"] == 0.0
    mark = Glb(marked).json["asset"]["extras"]["forge_bust"]
    assert mark["source"] == "body_shape" and mark["size"] == 0.0 and mark["window_shift_m"] == 0.0 and mark["moved"] == 0 and mark["apex_after"] == mark["apex_before"]
    assert bustsize.check_file(marked, 0.0, shaped=True) is None
    out, soft = softbody.apply(marked, {"chest": 1.0})
    assert soft["apex"]["chest"] == pytest.approx(mark["apex_after"], abs=1e-3) and softbody.check_file(out, {"chest"}) is None
