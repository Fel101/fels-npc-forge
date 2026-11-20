"""npcgen/softbody.py: soft-body spring bones for the chest, the butt, the belly and the thighs of a designed female body. The request and what it asks for, the shape of a part's falloff, and, on real bodies built by the
Body Designer (these tests skip where it is not installed): the bones and their settings, the skin weights (they add up, at most four a vertex, nothing far from the part is touched, no vertex that
should follow is left out, no step in the falloff), what the step does to the way the body moves while the engine leaves the bones alone (nothing at rest, a few millimetres at most), what survives it
(the face shapes' sparse data, the underwear that follows the skin), and what it refuses. Made-up poses are evaluated in numpy (tests/soft_support.py); no engine is needed."""
import copy
import math

import numpy as np
import pytest

import soft_support as ss
from npcgen import bodymaker, softbody, underwear
from npcgen.paint3d import Glb
from npcgen.softbody import SoftBodyError

needs_designer = pytest.mark.skipif(not bodymaker.bases(), reason="the Body Designer is not installed")
BOTH = {"chest": 1.0, "butt": 1.0}
ALL = {"chest": 1.0, "butt": 1.0, "belly": 1.0, "thighs": 1.0}


# ---- the request ---------------------------------------------------------------------------------------------------------------------------
def test_a_request_for_soft_body_motion_is_checked_for_its_shape_and_nothing_else():
    full = {"intensity": 1.0, "chest": 1.0, "butt": 1.0, "belly": 0.0, "thighs": 0.0, "softness": 0.5}                    # (the belly and the thighs are off until asked for)
    assert softbody.clean(True) == full and softbody.clean({"chest": 0.5}) == {**full, "chest": 0.5}                    # (what is left out takes its default)
    assert softbody.clean({"intensity": 0, "softness": 1}) == {}                                                          # (nothing left to make: no request)
    for none in (None, False, {}):
        assert softbody.clean(none) == {}
    assert softbody.clean({"chest": 0, "butt": 0}) == {} and softbody.clean({"butt": 0}) == {**full, "butt": 0.0}
    assert softbody.clean({"chest": 0, "butt": 0, "belly": 0.4}) == {**full, "chest": 0.0, "butt": 0.0, "belly": 0.4}      # (the belly alone is a request)
    assert all(type(v) is float for v in softbody.clean({"chest": 1, "butt": 0, "intensity": 1, "softness": 0, "belly": 1, "thighs": 0}).values())
    for odd, field in (("yes", ""), ([], ""), (0, ""), ({"jiggle": 1}, ".jiggle: unknown field"), ({"chest": 1.5}, ".chest"), ({"butt": -0.1}, ".butt"), ({"intensity": "high"}, ".intensity"),
                       ({"softness": None}, ".softness"), ({"chest": True}, ".chest"), ({"chest": math.nan}, ".chest"), ({"butt": math.inf}, ".butt"), ({"belly": 1.5}, ".belly"),
                       ({"thighs": -0.1}, ".thighs"), ({"belly": "some"}, ".belly")):
        with pytest.raises(ValueError, match="body_3d.soft" + field.replace(".", "[.]")):
            softbody.clean(odd, "body_3d.soft")


def test_the_intensity_and_each_part_multiply_and_a_part_at_nothing_is_not_made():
    assert softbody.amounts({}) == {} and softbody.amounts(None) == {}
    assert softbody.amounts(softbody.clean(True)) == {"chest": 1.0, "butt": 1.0}
    assert softbody.amounts(softbody.clean({"intensity": 0.5, "chest": 0.8, "butt": 0.5})) == {"chest": 0.4, "butt": 0.25}
    assert softbody.amounts(softbody.clean({"butt": 0})) == {"chest": 1.0}
    assert softbody.amounts({"chest": 0.00004, "butt": 0.5, "intensity": 1.0}) == {"butt": 0.5} and softbody.clean({"chest": 0.00004, "butt": 0}) == {}      # (a share that rounds to nothing is not a part)
    assert softbody.amounts(softbody.clean({"intensity": 0.5, "belly": 0.5, "thighs": 1.0})) == {"chest": 0.5, "butt": 0.5, "belly": 0.25, "thighs": 0.5}
    assert softbody.amounts(softbody.clean({"chest": 0, "butt": 0, "thighs": 0.3})) == {"thighs": 0.3}


def test_softness_moves_the_stiffness_by_a_factor_of_two_and_a_bit_each_way_and_is_one_at_the_middle():
    assert softbody.firmness(0.5) == 1.0
    assert softbody.firmness(0.0) == pytest.approx(2 ** 1.5) and softbody.firmness(1.0) == pytest.approx(2 ** -1.5)
    assert softbody.firmness(-1) == softbody.firmness(0) and softbody.firmness(9) == softbody.firmness(1)
    values = [softbody.firmness(i / 10) for i in range(11)]
    assert values == sorted(values, reverse=True)


def test_the_card_is_told_the_parts_the_defaults_and_how_many_bones_each_part_adds():
    info = softbody.info()
    assert info["parts"] == {"chest": "Chest", "butt": "Butt", "belly": "Belly", "thighs": "Thighs"} and info["bones"] == 4 and info["defaults"] == softbody.DEFAULTS and info["label"] == "Soft-body motion"
    assert info["bones_by_part"] == {"chest": 4, "butt": 4, "belly": 2, "thighs": 4} and list(info["parts"]) == list(softbody.PARTS)
    assert "female body only" in info["note"] and "Off unless you choose it" in info["note"] and "Items tab" in info["note"] and "belly" in info["note"]


# ---- the falloff of a part ---------------------------------------------------------------------------------------------------------------------
def test_the_weight_is_one_at_the_apex_nothing_beyond_the_radius_and_nothing_behind_the_wall_of_the_body():
    apex = np.array([0.095, 1.16, 0.13])
    radius, depth = softbody.SHAPES["chest"]["radius"], softbody.SHAPES["chest"]["depth"]
    at = lambda *p: softbody.weight_of(np.array([p], float), "chest", apex, 1.0, 1.0)[0]
    assert at(*apex) == pytest.approx(1.0)
    assert at(apex[0] + radius, apex[1], apex[2]) == pytest.approx(0.0, abs=1e-12) and at(apex[0], apex[1] + 2 * radius, apex[2]) == 0.0
    assert at(apex[0] + radius / 2, apex[1], apex[2]) == pytest.approx(0.5)                                               # (the cosine: half way, half the weight)
    assert at(apex[0], apex[1], apex[2] - depth - 0.05) == 0.0                                                            # (the skin on the other side of the body)
    assert 0.0 < at(apex[0], apex[1], apex[2] - depth + 0.01) < 1.0                                                       # (the wall fades, it does not cut)
    assert at(apex[0], apex[1], apex[2] + 0.02) == pytest.approx(1.0)                                                     # (the clothes over the skin get the skin's weight: how far out does not matter)


def test_the_weight_is_the_same_on_both_sides_and_grows_with_the_height_of_the_body():
    apex = np.array([0.095, 1.16, 0.13])
    points = np.array([[0.1, 1.18, 0.12], [0.06, 1.15, 0.11], [0.13, 1.2, 0.1]])
    mirrored = points * np.array([-1.0, 1.0, 1.0])
    assert softbody.weight_of(points, "chest", apex, 1.0, 1.0) == pytest.approx(softbody.weight_of(mirrored, "chest", apex, 1.0, -1.0))
    far = np.array([[apex[0] + 1.5 * softbody.SHAPES["chest"]["radius"], apex[1], apex[2]]])
    assert softbody.weight_of(far, "chest", apex, 1.0, 1.0)[0] == 0.0 and softbody.weight_of(far, "chest", apex, 2.0, 1.0)[0] > 0.0      # (a taller body, a bigger part)


def test_the_cleft_of_the_butt_stays_where_it_is():
    apex = np.array([0.055, 0.81, -0.12])
    cleft, cheek = np.array([[0.0, 0.81, -0.12]]), np.array([[0.055, 0.81, -0.12]])
    assert softbody.weight_of(cleft, "butt", apex, 1.0, 1.0)[0] == 0.0 and softbody.weight_of(cleft, "butt", apex, 1.0, -1.0)[0] == 0.0
    assert softbody.weight_of(cheek, "butt", apex, 1.0, 1.0)[0] > 0.9 and softbody.weight_of(cheek, "butt", apex, 1.0, -1.0)[0] == 0.0     # (a cheek belongs to its own side)


def test_a_quaternion_turns_y_to_the_direction_by_the_short_way():
    for direction in ([0, 1, 0], [0, 0, 1], [1, 0, 0], [0.3, -0.4, 0.8], [0, -1, 0]):
        x, y, z, w = softbody._quaternion_y_to(np.array(direction, float))
        rot = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)], [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])
        want = np.array(direction, float) / np.linalg.norm(direction)
        assert rot @ np.array([0.0, 1.0, 0.0]) == pytest.approx(want, abs=1e-9) and (x * x + y * y + z * z + w * w) == pytest.approx(1.0)


# ---- real bodies ------------------------------------------------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def made():
    """{name: (the GLB, its recipe)} of two real female bodies: an ordinary one and a full one (about 10 s each at a 512 px skin)."""
    return {"ordinary": ss.game_body(values={"chest": 0.4, "hips": 0.3}), "full": ss.game_body(values={"chest": 1.0, "hips": 1.0, "fat": 1.0})}


@pytest.fixture(scope="module")
def body(made):
    return made["ordinary"][0]


@pytest.fixture(scope="module")
def soft(body):
    """(the ordinary body with both parts at their full share, the report)."""
    return softbody.apply(body, BOTH)


def names_of(doc):
    return [str(doc["nodes"][j]["name"]) for j in doc["skins"][0]["joints"]]


@needs_designer
def test_each_part_adds_a_chain_of_two_bones_on_each_side_hung_on_the_bone_it_is_made_for(body, soft):
    data, report = soft
    before, after = Glb(body).json, Glb(data).json
    added = [f"soft_{part}_{side}_{n}" for part in ("chest", "butt") for side in "lr" for n in ("01", "02")]
    assert names_of(after) == names_of(before) + added and report["bones"] == 8 and report["made"] == BOTH
    assert len(after["nodes"]) == len(before["nodes"]) + 8 and Glb(data).accessor(after["skins"][0]["inverseBindMatrices"]).shape == (len(names_of(before)) + 8, 16)
    nodes = after["nodes"]
    parent = {c: i for i, n in enumerate(nodes) for c in n.get("children", [])}
    by_name = {n.get("name"): i for i, n in enumerate(nodes)}
    for part, bone in (("chest", "spine_02"), ("butt", "pelvis")):
        for side in "lr":
            first, second = by_name[f"soft_{part}_{side}_01"], by_name[f"soft_{part}_{side}_02"]
            assert nodes[parent[first]]["name"] == bone and parent[second] == first and "mesh" not in nodes[first] and "mesh" not in nodes[second]
            block = nodes[first]["extras"]["forge_secondary"]
            assert block["chain"] == f"{part}_{side}" and block["kind"] == part and block["bones"] == [f"soft_{part}_{side}_01", f"soft_{part}_{side}_02"] and block["parent"] == bone
            assert block["max_angle_deg"] == softbody.SETTINGS[part]["max_angle_deg"] and block["stiffness"] == softbody.SETTINGS[part]["stiffness"] and block["amount"] == 1.0
            assert block["tail_axis"] == "+Y" and block["gravity_direction"] == [0.0, -1.0, 0.0] and "extras" not in nodes[second]
    marked = after["asset"]["extras"]["forge_soft"]
    assert marked["parts"] == BOTH and marked["bone_names"] == added and marked["joints_before"] == len(names_of(before)) and marked["joints"] == len(names_of(after)) and marked["softness"] == 0.5
    assert sorted(marked["chains"]) == ["butt_l", "butt_r", "chest_l", "chest_r"] and marked["families"] == {"chest": ["spine_02"], "butt": ["pelvis"]}
    assert softbody.check_file(data, {"chest", "butt"}) is None


@needs_designer
def test_a_part_that_is_off_has_no_bones_and_is_not_in_the_file_at_all(body):
    for parts, bones in (({"chest": 1.0}, ["chest"]), ({"butt": 0.6}, ["butt"]), ({"chest": 0.0, "butt": 1.0}, ["butt"])):
        data, report = softbody.apply(body, parts)
        doc = Glb(data).json
        assert set(report["made"]) == set(bones) and report["bones"] == 4 and all(n.startswith(f"soft_{bones[0]}_") for n in names_of(doc)[-4:])
        assert not any(n.startswith("soft_" + ("butt" if bones == ["chest"] else "chest")) for n in names_of(doc))
        assert softbody.check_file(data, set(bones)) is None and softbody.check_file(data, {"chest", "butt"}) is not None
    same, report = softbody.apply(body, {})
    assert same is body and report == {"made": {}, "bones": 0}
    assert softbody.apply(body, {"chest": 0, "butt": 0.0})[0] is body


@needs_designer
def test_nothing_else_of_the_file_is_changed(body, soft):
    before, after = Glb(body), Glb(soft[0])
    view = softbody.without(after.json)
    for key in ("nodes", "scenes", "meshes", "materials", "textures", "samplers", "animations", "extensionsUsed"):
        assert view.get(key) == before.json.get(key), key
    assert view["skins"][0]["joints"] == before.json["skins"][0]["joints"] and view["asset"] == before.json["asset"]            # (the mark taken out, the extras are the body's own)
    for name, primitive in softbody._skinned(before.json).items():
        other = softbody._skinned(after.json)[name]
        for key in primitive["attributes"]:
            if key in ("JOINTS_0", "WEIGHTS_0"):
                continue
            assert np.array_equal(before.accessor(primitive["attributes"][key]), after.accessor(other["attributes"][key])), (name, key)
        assert np.array_equal(before.accessor(primitive["indices"]), after.accessor(other["indices"]))
    for i, image in enumerate(before.json.get("images") or []):
        assert before.view_bytes(image["bufferView"]) == after.view_bytes(after.json["images"][i]["bufferView"])
    first = len(before.json["skins"][0]["joints"])
    assert np.array_equal(before.accessor(before.json["skins"][0]["inverseBindMatrices"]), after.accessor(after.json["skins"][0]["inverseBindMatrices"])[:first])
    assert len(after.bin) < len(before.bin) + 200000                                                                       # (the replaced weights are not kept beside the new ones)


@needs_designer
def test_the_weights_add_up_and_nothing_far_from_a_part_is_touched(made, soft):
    data, _report = soft
    original, changed = ss.Rig(made["ordinary"][0]), ss.Rig(data)
    for name, m in changed.meshes.items():
        assert m["jn"].max() < len(changed.names) and m["wt"].min() >= 0.0
        assert np.abs(m["wt"].sum(axis=1) - 1.0).max() < 1e-5, name
        assert (m["wt"] > 0).sum(axis=1).max() <= 4
    m, o = changed.meshes["ForgeBody"], original.meshes["ForgeBody"]
    touched = np.flatnonzero((m["wt"] != o["wt"]).any(axis=1) | (m["jn"] != o["jn"]).any(axis=1))
    share = changed.soft_share("ForgeBody")
    assert 250 < len(touched) < 700 and share[touched].min() >= 0.0 and share.max() <= 0.81
    world = original.world()
    rig_bones = {n: world[j][:3, 3] for n, j in zip(original.names, original.joints)}
    for part in ("chest", "butt"):
        apex = softbody._apex(o["pos"], rig_bones, part, 1.0)
        wanted = np.maximum(softbody.weight_of(o["pos"], part, apex, 1.0, 1.0), softbody.weight_of(o["pos"], part, apex, 1.0, -1.0))
        assert (changed.soft_share("ForgeBody", part)[wanted <= 0.0] == 0.0).all()                                          # (outside the part's reach: no share)
    outside = np.setdiff1d(np.arange(len(m["pos"])), touched)
    assert np.array_equal(m["wt"][outside], o["wt"][outside]) and np.array_equal(m["jn"][outside], o["jn"][outside])


@needs_designer
def test_the_share_a_spring_bone_takes_comes_from_the_bone_it_hangs_on_and_from_no_other(made, soft):
    original, changed = ss.Rig(made["ordinary"][0]), ss.Rig(soft[0])
    m, o = changed.meshes["ForgeBody"], original.meshes["ForgeBody"]
    for part, parent in (("chest", "spine_02"), ("butt", "pelvis")):
        mine = changed.soft_share("ForgeBody", part)
        column = original.names.index(parent)
        parent_before = np.where(o["jn"] == column, o["wt"], 0.0).sum(axis=1)
        rows = np.flatnonzero(mine > 0.0)
        assert len(rows) > 90 and (mine[rows] <= parent_before[rows] + 1e-6).all()                                          # (never more than the bone had)
    touched = np.flatnonzero((m["wt"] != o["wt"]).any(axis=1) | (m["jn"] != o["jn"]).any(axis=1))
    families = {"spine_02", "pelvis"}
    for row in touched:
        before, after = {}, {}
        for names, jn, wt, into in ((original.names, o["jn"][row], o["wt"][row], before), (changed.names, m["jn"][row], m["wt"][row], after)):
            for j, w in zip(jn, wt):
                if w > 0:
                    into[names[j]] = into.get(names[j], 0.0) + float(w)
        change = {bone: after.get(bone, 0.0) - before.get(bone, 0.0) for bone in set(before) | set(after)}
        others = [d for bone, d in change.items() if abs(d) > 1e-6 and not bone.startswith("soft_") and bone not in families]
        assert len([d for d in others if d < 0]) <= 1 and len([d for d in others if d > 0]) <= 1, (row, change)             # (apart from the family bones, at most one entry was merged into one other)


@needs_designer
def test_no_vertex_that_should_follow_the_part_is_left_out_and_the_falloff_has_no_steps_in_it(made):
    for name, (data, _recipe) in made.items():
        out, report = softbody.apply(data, BOTH)
        assert report["meshes"]["ForgeBody"]["skipped"] == 0, name
        rig = ss.Rig(out)
        m = rig.meshes["ForgeBody"]
        tris = m["tris"]
        edges = np.unique(np.sort(np.concatenate([tris[:, [0, 1]], tris[:, [1, 2]], tris[:, [2, 0]]]), axis=1), axis=0)
        length = np.maximum(np.linalg.norm(m["pos"][edges[:, 0]] - m["pos"][edges[:, 1]], axis=1), 1e-6)
        for part in ("chest", "butt"):
            share = rig.soft_share("ForgeBody", part)
            near = (share[edges[:, 0]] > 0.01) | (share[edges[:, 1]] > 0.01)
            slope = (np.abs(share[edges[:, 0]] - share[edges[:, 1]]) / length)[near]
            assert slope.max() < 20.0, (name, part, slope.max())             # (per metre: the cosine alone reaches 15; a step where the arm starts to count, or a vertex left out, made it 25 to 35)


@needs_designer
def test_with_the_spring_bones_at_rest_the_body_moves_as_it_did_in_any_pose(made, soft):
    original, changed = ss.Rig(made["ordinary"][0]), ss.Rig(soft[0])
    def worst(turns):
        before, after = original.world(turns), changed.world(turns)
        return max(np.linalg.norm(original.skinned(m, before) - changed.skinned(m, after), axis=1).max() for m in original.meshes) * 1000
    assert worst(None) < 1e-3                                                                                              # (millimetres: at rest nothing at all)
    assert worst({"pelvis": ([0.2, 1.0, 0.4], 0.5)}) < 1e-3                                                                # (the whole body turning with its root: every bone below moves as one)
    assert worst({"spine_02": ([1.0, 0.3, 0.2], 0.5)}) < softbody.DROP_COST * 1000                                         # (a bone that carries a spring bone turning: only what was merged upward shows)
    rng = np.random.default_rng(7)
    bones = ["clavicle_l", "clavicle_r", "upperarm_l", "upperarm_r", "thigh_l", "thigh_r", "spine_01", "spine_03", "neck_01"]
    assert max(worst(ss.random_turns(rng, bones, 0.5)) for _ in range(8)) < 6.0                                            # (a dozen bones all at once, up to half a radian: a few millimetres)


@needs_designer
def test_turning_a_spring_bone_moves_the_skin_around_its_part_and_nothing_else(made, soft):
    changed = ss.Rig(soft[0])
    rest = {m: changed.skinned(m, changed.world()) for m in changed.meshes}
    for part, side in (("chest", "l"), ("butt", "r")):
        turns = {f"soft_{part}_{side}_01": ([1.0, 0.0, 0.0], 0.35)}
        moved = changed.world(turns)
        for mesh in changed.meshes:
            shift = np.linalg.norm(changed.skinned(mesh, moved) - rest[mesh], axis=1)
            mine = changed.soft_share(mesh, part)
            chain = [changed.names.index(f"soft_{part}_{side}_01"), changed.names.index(f"soft_{part}_{side}_02")]
            own = (np.isin(changed.meshes[mesh]["jn"], chain) & (changed.meshes[mesh]["wt"] > 0)).any(axis=1)
            assert (shift[~own] < 1e-9).all(), (part, mesh)                                                                # (a vertex with no weight on this chain does not move)
            assert shift[own].max() > 0.004 and shift.max() < 0.07 and mine.max() > 0.4                                     # (the followers move, by at most their share of the bone's swing)


@needs_designer
def test_the_underwear_follows_the_skin_under_it_and_so_do_clothes(body, made):
    recipe = made["ordinary"][1]
    dressed, _worn = underwear.dress(body, bodymaker.dressing(recipe))
    data, report = softbody.apply(dressed, BOTH)
    assert report["meshes"]["ForgeUnderwear"]["touched"] > 100 and report["meshes"]["ForgeUnderwear"]["skipped"] == 0 and softbody.check_file(data, {"chest", "butt"}) is None
    rig = ss.Rig(data)
    turns = {"soft_chest_l_01": ([1.0, 0.0, 0.0], 0.3), "soft_butt_l_01": ([1.0, 0.0, 0.0], 0.3)}
    moved, rest = rig.world(turns), rig.world()
    shift = {m: rig.skinned(m, moved) - rig.skinned(m, rest) for m in ("ForgeBody", "ForgeUnderwear")}
    shell = rig.meshes["ForgeUnderwear"]["pos"]
    followers = np.flatnonzero(np.linalg.norm(shift["ForgeUnderwear"], axis=1) > 0.003)
    assert len(followers) > 60
    skin = rig.meshes["ForgeBody"]["pos"]
    for row in followers[::7]:
        nearest = np.argmin(np.linalg.norm(skin - shell[row], axis=1))
        assert np.linalg.norm(shift["ForgeUnderwear"][row] - shift["ForgeBody"][nearest]) < 0.002                      # (within 2 mm of the skin it lies on)


@needs_designer
def test_the_face_shapes_of_a_body_come_through_with_their_sparse_data_untouched(made):
    if not bodymaker.offers("expressions"):
        pytest.skip("this Body Designer has no face expressions")
    data, recipe = made["ordinary"]
    shaped = bodymaker._shapes(data, bodymaker._api().clean_recipe(bodymaker.with_expressions(recipe, True)))
    out, _report = softbody.apply(shaped, BOTH)
    before, after = Glb(shaped), Glb(out)
    sparse = [i for i, a in enumerate(before.json["accessors"]) if "sparse" in a]
    assert len(sparse) > 10 and sparse == [i for i, a in enumerate(after.json["accessors"]) if "sparse" in a]
    for i in sparse:
        b, a = before.json["accessors"][i]["sparse"], after.json["accessors"][i]["sparse"]
        assert before.view_bytes(b["indices"]["bufferView"]) == after.view_bytes(a["indices"]["bufferView"]) and before.view_bytes(b["values"]["bufferView"]) == after.view_bytes(a["values"]["bufferView"])
        assert {k: v for k, v in before.json["accessors"][i].items() if k != "sparse"} == {k: v for k, v in after.json["accessors"][i].items() if k != "sparse"}
    assert [m.get("extras") for m in before.json["meshes"]] == [m.get("extras") for m in after.json["meshes"]]
    assert softbody.check_file(out, {"chest", "butt"}) is None and len(out) < len(shaped) + 20000


def block_of(data, name):
    return next(n for n in Glb(data).json["nodes"] if n.get("name") == name)["extras"]["forge_secondary"]


@needs_designer
def test_the_settings_the_engine_reads_follow_softness_and_what_is_asked_for_by_name(body):
    stiff = softbody.SETTINGS["chest"]["stiffness"]
    for softness, factor in ((0.0, 2 ** 1.5), (0.5, 1.0), (1.0, 2 ** -1.5)):
        data, _report = softbody.apply(body, {"chest": 0.7}, softness=softness)
        assert block_of(data, "soft_chest_l_01")["stiffness"] == pytest.approx(stiff * factor, abs=1e-3) and block_of(data, "soft_chest_r_01")["amount"] == 0.7
        assert Glb(data).json["asset"]["extras"]["forge_soft"]["softness"] == softness
    data, _report = softbody.apply(body, BOTH, settings={"chest": {"stiffness": 1.0, "drag": 0.5}}, softness=1.0)
    assert block_of(data, "soft_chest_l_01")["stiffness"] == 1.0 and block_of(data, "soft_chest_l_01")["drag"] == 0.5          # (named settings are used as given, not scaled)
    assert block_of(data, "soft_butt_l_01")["stiffness"] == pytest.approx(softbody.SETTINGS["butt"]["stiffness"] * 2 ** -1.5, abs=1e-3)


@needs_designer
def test_the_same_body_and_request_give_the_same_file_every_time(body, soft):
    assert softbody.apply(body, BOTH)[0] == soft[0]
    assert softbody.apply(body, {"chest": 0.5, "butt": 0.25}, softness=0.8)[0] == softbody.apply(body, {"chest": 0.5, "butt": 0.25}, softness=0.8)[0]


@needs_designer
def test_a_smaller_amount_hands_over_a_smaller_share(body):
    shares = []
    for amount in (0.25, 0.5, 1.0):
        rig = ss.Rig(softbody.apply(body, {"chest": amount})[0])
        shares.append(rig.soft_share("ForgeBody", "chest").max())
    assert shares[0] < shares[1] < shares[2] and shares[0] == pytest.approx(shares[2] * 0.25, rel=0.05) and shares[1] == pytest.approx(shares[2] * 0.5, rel=0.05)


# ---- what it refuses, in words -----------------------------------------------------------------------------------------------------------------
def altered(data, edit):
    glb = Glb(data)
    edit(glb.json)
    return glb.dumps()


@needs_designer
def test_it_refuses_a_file_that_is_not_an_adult_female_designed_game_body(body):
    with pytest.raises(SoftBodyError, match="female body"):
        softbody.apply(altered(body, lambda d: d["asset"]["extras"]["body_design"].update(base=ss.MALE)), BOTH)
    with pytest.raises(SoftBodyError, match="adult"):
        softbody.apply(altered(body, lambda d: d["asset"]["extras"].update(minimum_age=17)), BOTH)
    with pytest.raises(SoftBodyError, match="adult"):
        softbody.apply(altered(body, lambda d: d["asset"]["extras"].pop("minimum_age")), BOTH)
    with pytest.raises(SoftBodyError, match="Body Designer made"):
        softbody.apply(altered(body, lambda d: d["asset"]["extras"].update(export_tier="full")), BOTH)
    with pytest.raises(SoftBodyError, match="Body Designer made"):
        softbody.apply(altered(body, lambda d: d["asset"]["extras"].pop("body_design")), BOTH)
    with pytest.raises(SoftBodyError, match="one skin"):
        softbody.apply(altered(body, lambda d: d["skins"].append(copy.deepcopy(d["skins"][0]))), BOTH)
    with pytest.raises(SoftBodyError, match="spine, pelvis and thigh"):
        softbody.apply(altered(body, lambda d: [n.update(name="renamed") for n in d["nodes"] if n.get("name") == "spine_02"]), BOTH)
    no_thigh = altered(body, lambda d: [n.update(name="renamed") for n in d["nodes"] if n.get("name") == "thigh_r"])
    with pytest.raises(SoftBodyError, match="spine, pelvis and thigh"):
        softbody.apply(no_thigh, {"thighs": 1.0})                                                                              # (a thigh to hang each thigh chain on)
    assert softbody.apply(no_thigh, BOTH)[1]["bones"] == 8                                                                     # (the chest and the butt need none)
    with pytest.raises(SoftBodyError, match="own skin"):
        softbody.apply(altered(body, lambda d: [m.update(name="Other") for m in d["meshes"] if m.get("name") == "ForgeBody"]), BOTH)
    with pytest.raises(ValueError):
        softbody.apply(b"not a glb file", BOTH)


@needs_designer
def test_a_body_that_has_the_bones_is_not_given_them_again(soft):
    with pytest.raises(SoftBodyError, match="already has soft-body bones: build it again from its recipe"):
        softbody.apply(soft[0], {"chest": 1.0})


# ---- looking at a file: the checks that stand behind a build -----------------------------------------------------------------------------------------
@needs_designer
def test_a_file_that_is_not_the_body_with_exactly_those_bones_is_refused_in_words(body, soft):
    data = soft[0]
    clean = lambda: copy.deepcopy(Glb(data).json)
    assert softbody.check(clean(), {"chest", "butt"}) is None and softbody.check_file(body, {"chest"}) is not None            # (a body without them)
    assert softbody.check(clean(), {"chest"}) and softbody.check(clean(), {"chest", "butt", "elbow"}) and softbody.check(clean(), set())
    doc = clean()
    doc["asset"]["extras"]["forge_soft"]["bone_names"][0] = "soft_chest_l_09"
    assert "soft-body bones that were asked for" in softbody.check(doc, {"chest", "butt"})
    doc = clean()
    doc["asset"]["extras"]["forge_soft"]["joints_before"] -= 1
    assert softbody.check(doc, {"chest", "butt"})
    doc = clean()
    doc["skins"][0]["joints"].reverse()
    assert softbody.check(doc, {"chest", "butt"})
    doc = clean()
    names = {n.get("name"): i for i, n in enumerate(doc["nodes"])}
    doc["nodes"][names["pelvis"]]["children"].remove(names["soft_butt_l_01"])
    doc["nodes"][names["spine_03"]].setdefault("children", []).append(names["soft_butt_l_01"])
    assert "butt are not hung on the pelvis" in softbody.check(doc, {"chest", "butt"})
    doc = clean()
    names = {n.get("name"): i for i, n in enumerate(doc["nodes"])}
    doc["nodes"][names["soft_chest_r_01"]]["extras"]["forge_secondary"]["kind"] = "hair"
    assert "chest do not carry their settings" in softbody.check(doc, {"chest", "butt"})
    doc = clean()
    names = {n.get("name"): i for i, n in enumerate(doc["nodes"])}
    doc["animations"] = [{"name": "Idle", "channels": [{"sampler": 0, "target": {"node": names["soft_butt_r_02"], "path": "rotation"}}], "samplers": []}]
    assert "A clip moves the soft-body bones" in softbody.check(doc, {"chest", "butt"})


@needs_designer
def test_the_numbers_behind_the_bones_are_checked_too(soft):
    data = soft[0]
    glb = Glb(data)
    first = glb.json["asset"]["extras"]["forge_soft"]["joints_before"]
    bad = copy.deepcopy(glb)
    accessor = bad.json["accessors"][bad.json["skins"][0]["inverseBindMatrices"]]
    start = bad.json["bufferViews"][accessor["bufferView"]]["byteOffset"] + first * 64
    raw = bytearray(bad.bin)
    raw[start + 48:start + 52] = np.float32(0.5).tobytes()                                                                   # (a bind matrix that does not put the bone at rest)
    bad.bin = bytes(raw)
    assert "bind matrices do not put them at rest" in softbody.check_file(bad.dumps(), {"chest", "butt"})
    skewed = copy.deepcopy(glb)
    weights = skewed.json["meshes"][[m["name"] for m in skewed.json["meshes"]].index("ForgeBody")]["primitives"][0]["attributes"]["WEIGHTS_0"]
    view = skewed.json["bufferViews"][skewed.json["accessors"][weights]["bufferView"]]
    raw = bytearray(skewed.bin)
    arr = np.frombuffer(bytes(raw[view["byteOffset"]:view["byteOffset"] + view["byteLength"]]), np.float32).reshape(-1, 4).copy()
    row = int(np.argmax(ss.Rig(data).soft_share("ForgeBody")))
    arr[row] *= 1.2
    raw[view["byteOffset"]:view["byteOffset"] + view["byteLength"]] = arr.tobytes()
    skewed.bin = bytes(raw)
    assert "no longer add up to 1" in softbody.check_file(skewed.dumps(), {"chest", "butt"})


@needs_designer
def test_the_file_as_it_was_before_the_step_is_given_back_for_the_checks_that_compare_it(body, soft):
    after = Glb(soft[0]).json
    view = softbody.without(after)
    assert view["nodes"] == Glb(body).json["nodes"] and view["skins"][0]["joints"] == Glb(body).json["skins"][0]["joints"] and "forge_soft" not in view["asset"]["extras"]
    assert "forge_soft" in after["asset"]["extras"] and len(after["nodes"]) == len(view["nodes"]) + 8                       # (the original is not changed by looking)
    plain = Glb(body).json
    assert softbody.without(plain) == plain and softbody.without(plain) is not plain


# ---- what a build checks before it uses the file ------------------------------------------------------------------------------------------------
def with_clips(data, moving=None):
    """The GLB with the records of the library's 85 clips in its JSON (a stand-in for the copier's: `bodymaker.check` counts and names them; nothing is played here). `moving` is a node index one
    clip also keys."""
    glb = Glb(data)
    names = [f"Clip{i:02d}" for i in range(82)] + ["Idle", "Walk", "Wave"]
    glb.json["animations"] = [{"name": n, "extras": {"seconds": 1.0, "loop": n != "Wave", "source": "test"},
                               "channels": [{"sampler": 0, "target": {"node": moving, "path": "rotation"}}] if moving is not None and n == "Walk" else [], "samplers": []} for n in names]
    return glb.dumps()


@needs_designer
def test_a_build_is_checked_against_the_body_with_the_soft_bones_taken_away(body, soft):
    animated = with_clips(soft[0])
    assert len(bodymaker.check(body, animated, "low", soft={"chest": 1.0, "butt": 1.0})) == 85
    assert len(bodymaker.check(body, with_clips(body), "low")) == 85 == len(bodymaker.check(body, with_clips(body), "low", soft={}))             # (no soft bones asked for, none there)
    with pytest.raises(bodymaker.BodyError, match="skeleton is not the body's own"):
        bodymaker.check(body, animated, "low")                                                               # (they were not asked for)
    with pytest.raises(bodymaker.BodyError, match="soft-body bones that were asked for"):
        bodymaker.check(body, with_clips(body), "low", soft={"chest": 1.0})                                  # (asked for and not there)
    with pytest.raises(bodymaker.BodyError, match="soft-body bones that were asked for"):
        bodymaker.check(body, animated, "low", soft={"chest": 1.0})                                          # (not the parts that are there)
    moving = Glb(soft[0]).json["skins"][0]["joints"][-1]
    with pytest.raises(bodymaker.BodyError, match="A clip moves the soft-body bones"):
        bodymaker.check(body, with_clips(soft[0], moving=moving), "low", soft={"chest": 1.0, "butt": 1.0})
    with pytest.raises(bodymaker.BodyError, match="skeleton is not the body's own"):
        bodymaker.check(body, with_clips(altered(soft[0], lambda d: d["nodes"][1].update(name="moved"))), "low", soft={"chest": 1.0, "butt": 1.0})      # (anything else different is still caught)


# ---- the belly and the thighs ---------------------------------------------------------------------------------------------------------------------
def test_the_belly_has_one_chain_in_the_middle_and_the_thighs_have_one_on_each_thigh():
    assert softbody.sides_of("belly") == (("c", 1.0),) and softbody.sides_of("thighs") == (("l", 1.0), ("r", -1.0)) and softbody.sides_of("chest") == (("l", 1.0), ("r", -1.0))
    assert softbody.parent_of("belly", "c") == "spine_01" and softbody.parent_of("thighs", "l") == "thigh_l" and softbody.parent_of("thighs", "r") == "thigh_r"
    assert softbody.parent_of("chest", "r") == "spine_02" and softbody.parent_of("butt", "l") == "pelvis"
    assert softbody.family_of("thighs", "r") == ("thigh_r",) and softbody.family_names("thighs") == ["thigh_l", "thigh_r"]
    assert softbody.family_names("belly") == ["spine_01"] and softbody.family_names("chest") == ["spine_02"] and softbody.family_names("butt") == ["pelvis"]
    assert softbody.bones_by_part() == {"chest": 4, "butt": 4, "belly": 2, "thighs": 4} and softbody.bones_per_part() == 4
    assert softbody._expected_names({"belly"}) == ["soft_belly_c_01", "soft_belly_c_02"]
    assert softbody._expected_names({"thighs", "chest"}) == ["soft_chest_l_01", "soft_chest_l_02", "soft_chest_r_01", "soft_chest_r_02",
                                                             "soft_thighs_l_01", "soft_thighs_l_02", "soft_thighs_r_01", "soft_thighs_r_02"]


def test_the_belly_weight_is_one_at_its_apex_on_the_midline_and_the_same_on_both_sides_of_it():
    apex = np.array([0.0, 0.86, 0.097])
    radius, depth = softbody.SHAPES["belly"]["radius"], softbody.SHAPES["belly"]["depth"]
    at = lambda *p: softbody.weight_of(np.array([p], float), "belly", apex, 1.0, 1.0)[0]
    assert at(*apex) == pytest.approx(1.0) and at(apex[0], apex[1] + radius, apex[2]) == pytest.approx(0.0, abs=1e-12)
    assert at(0.03, 0.9, 0.09) == pytest.approx(at(-0.03, 0.9, 0.09)) and at(0.03, 0.9, 0.09) > 0.0                        # (no cleft: the two sides of the middle are alike)
    assert at(apex[0], apex[1] + radius / 2, apex[2]) == pytest.approx(0.5)
    assert at(apex[0], apex[1], apex[2] - depth - 0.05) == 0.0 and at(apex[0], apex[1], apex[2] + 0.02) == pytest.approx(1.0)  # (the back is left alone; clothes out from the skin follow it)


def test_the_thigh_weight_is_round_across_the_thigh_and_its_wall_is_the_inner_side_of_the_leg():
    apex = np.array([0.182, 0.7136, 0.004])
    radius, depth = softbody.SHAPES["thighs"]["radius"], softbody.SHAPES["thighs"]["depth"]
    at = lambda side, *p: softbody.weight_of(np.array([p], float), "thighs", apex, 1.0, side)[0]
    assert at(1.0, *apex) == pytest.approx(1.0) and at(-1.0, -apex[0], apex[1], apex[2]) == pytest.approx(1.0)              # (each leg has its own side of the body)
    assert at(1.0, apex[0], apex[1] + radius, apex[2]) == pytest.approx(0.0, abs=1e-12) and at(1.0, apex[0], apex[1], apex[2] + radius) == pytest.approx(0.0, abs=1e-12)
    assert at(1.0, apex[0], apex[1] + radius / 2, apex[2]) == pytest.approx(0.5) and at(1.0, apex[0], apex[1], apex[2] - radius / 2) == pytest.approx(0.5)   # (round: z counts as y does)
    assert at(1.0, apex[0] - depth - 0.05, apex[1], apex[2]) == 0.0 and 0.0 < at(1.0, apex[0] - depth + 0.01, apex[1], apex[2]) < 1.0         # (the inner side of the thigh is left alone; the wall fades)
    assert at(1.0, apex[0] + 0.03, apex[1], apex[2]) == pytest.approx(1.0)                                                  # (the leg of a pair of trousers, out from the skin, follows it)
    assert at(1.0, -apex[0], apex[1], apex[2]) == 0.0 and at(-1.0, apex[0], apex[1], apex[2]) == 0.0                         # (never the other leg)
    far = np.array([[apex[0], apex[1], apex[2] + 1.5 * radius]])
    assert softbody.weight_of(far, "thighs", apex, 1.0, 1.0)[0] == 0.0 and softbody.weight_of(far, "thighs", apex, 2.0, 1.0)[0] > 0.0   # (a taller body, a bigger part)


@pytest.fixture(scope="module")
def soft_all(body):
    """(the ordinary body with all four parts at their full share, the report)."""
    return softbody.apply(body, ALL)


@needs_designer
def test_the_belly_and_the_thighs_add_their_chains_hung_on_their_own_bones(body, soft_all):
    data, report = soft_all
    before, after = Glb(body).json, Glb(data).json
    added = [f"soft_{part}_{tag}_{n}" for part, tags in (("chest", "lr"), ("butt", "lr"), ("belly", "c"), ("thighs", "lr")) for tag in tags for n in ("01", "02")]
    assert names_of(after) == names_of(before) + added and report["bones"] == 14 and report["made"] == ALL and len(after["nodes"]) == len(before["nodes"]) + 14
    nodes = after["nodes"]
    parent = {c: i for i, n in enumerate(nodes) for c in n.get("children", [])}
    by_name = {n.get("name"): i for i, n in enumerate(nodes)}
    for part, tag, bone in (("belly", "c", "spine_01"), ("thighs", "l", "thigh_l"), ("thighs", "r", "thigh_r")):
        first, second = by_name[f"soft_{part}_{tag}_01"], by_name[f"soft_{part}_{tag}_02"]
        assert nodes[parent[first]]["name"] == bone and parent[second] == first and "mesh" not in nodes[first]
        block = nodes[first]["extras"]["forge_secondary"]
        assert block["chain"] == f"{part}_{tag}" and block["kind"] == part and block["side"] == tag and block["parent"] == bone and block["bones"] == [f"soft_{part}_{tag}_01", f"soft_{part}_{tag}_02"]
        assert block["max_angle_deg"] == softbody.SETTINGS[part]["max_angle_deg"] and block["stiffness"] == softbody.SETTINGS[part]["stiffness"] and block["amount"] == 1.0
        assert block["tail_axis"] == "+Y" and block["gravity_direction"] == [0.0, -1.0, 0.0] and block["end_length_m"] == pytest.approx(softbody.SHAPES[part]["end"], abs=1e-4)
    marked = after["asset"]["extras"]["forge_soft"]
    assert marked["parts"] == ALL and marked["bone_names"] == added and marked["joints"] == len(names_of(after))
    assert sorted(marked["chains"]) == ["belly_c", "butt_l", "butt_r", "chest_l", "chest_r", "thighs_l", "thighs_r"]
    assert marked["families"] == {"chest": ["spine_02"], "butt": ["pelvis"], "belly": ["spine_01"], "thighs": ["thigh_l", "thigh_r"]}
    assert softbody.check_file(data, set(ALL)) is None and softbody.check_file(data, {"chest", "butt"}) is not None


@needs_designer
def test_a_new_part_alone_makes_only_its_bones_and_is_found_by_name(body):
    for parts, count, first in (({"belly": 1.0}, 2, "soft_belly_c_01"), ({"thighs": 0.5}, 4, "soft_thighs_l_01")):
        data, report = softbody.apply(body, parts)
        names = names_of(Glb(data).json)
        assert report["bones"] == count and set(report["made"]) == set(parts) and names[-count] == first and softbody.check_file(data, set(parts)) is None
        assert softbody.check_file(data, {"chest"}) and softbody.check_file(data, set(ALL))                                  # (not the parts that are there)


@needs_designer
def test_the_apex_of_each_new_part_is_where_the_body_shows_it_and_the_belly_is_on_the_midline(made, soft_all):
    report = soft_all[1]["apex"]
    assert report["belly"][0] == 0.0 and 0.84 < report["belly"][1] < 0.9 and 0.085 < report["belly"][2] < 0.115                # (the front of the abdomen, a little above the pelvis)
    assert 0.17 < report["thighs"][0] < 0.2 and 0.66 < report["thighs"][1] < 0.76 and abs(report["thighs"][2]) < 0.03          # (the outer side of the upper thigh, left)
    for name, (data, _recipe) in made.items():
        rig = ss.Rig(data)
        world = rig.world()
        bones = {n: world[j][:3, 3] for n, j in zip(rig.names, rig.joints)}
        pos = rig.meshes["ForgeBody"]["pos"]
        belly, thigh = softbody._apex(pos, bones, "belly", 1.0), softbody._apex(pos, bones, "thighs", 1.0)
        top = pos[(np.abs(pos[:, 0]) < 0.04) & (np.abs(pos[:, 1] - belly[1]) < 0.01) & (pos[:, 2] > 0.0)][:, 2].max()
        assert belly[0] == 0.0 and belly[2] > top - 0.008, name                                                                # (the front of the belly at that height)
        outer = pos[(pos[:, 0] > 0.1) & (np.abs(pos[:, 1] - thigh[1]) < 0.01) & (pos[:, 1] < bones["thigh_l"][1] - 0.02)][:, 0].max()
        assert thigh[0] > outer - 0.008, name                                                                                  # (the outside of the thigh at that height)


@needs_designer
def test_the_new_parts_take_their_share_from_their_own_bones_and_from_no_other(made, soft_all):
    original, changed = ss.Rig(made["ordinary"][0]), ss.Rig(soft_all[0])
    m, o = changed.meshes["ForgeBody"], original.meshes["ForgeBody"]
    for part, parents in (("belly", ["spine_01"]), ("thighs", ["thigh_l", "thigh_r"])):
        mine = changed.soft_share("ForgeBody", part)
        columns = [original.names.index(p) for p in parents]
        parent_before = np.where(np.isin(o["jn"], columns), o["wt"], 0.0).sum(axis=1)
        rows = np.flatnonzero(mine > 0.0)
        assert len(rows) > 40 and (mine[rows] <= parent_before[rows] + 1e-6).all(), part                                       # (never more than the bone had)
    for tag in ("l", "r"):                                                                                                     # (a thigh chain follows the outer side of its own leg)
        column = [changed.names.index(f"soft_thighs_{tag}_01"), changed.names.index(f"soft_thighs_{tag}_02")]
        followers = np.flatnonzero((np.isin(m["jn"], column) & (m["wt"] > 0)).any(axis=1))
        assert len(followers) > 40 and (m["pos"][followers, 0] * (1.0 if tag == "l" else -1.0) > 0.08).all(), tag              # (on the outer side of its own leg)
    for mesh, data in changed.meshes.items():
        assert data["jn"].max() < len(changed.names) and data["wt"].min() >= 0.0 and np.abs(data["wt"].sum(axis=1) - 1.0).max() < 1e-5 and (data["wt"] > 0).sum(axis=1).max() <= 4, mesh


@needs_designer
def test_no_vertex_that_should_follow_a_new_part_is_left_out_and_the_falloff_has_no_steps_in_it(made):
    for name, (data, _recipe) in made.items():
        out, report = softbody.apply(data, ALL)
        assert report["meshes"]["ForgeBody"]["skipped"] <= 3, (name, report["meshes"])                                          # (the four-bone rule left at most a handful at the edge of the region)
        rig = ss.Rig(out)
        m = rig.meshes["ForgeBody"]
        tris = m["tris"]
        edges = np.unique(np.sort(np.concatenate([tris[:, [0, 1]], tris[:, [1, 2]], tris[:, [2, 0]]]), axis=1), axis=0)
        length = np.maximum(np.linalg.norm(m["pos"][edges[:, 0]] - m["pos"][edges[:, 1]], axis=1), 1e-6)
        for part in ("belly", "thighs"):
            share = rig.soft_share("ForgeBody", part)
            near = (share[edges[:, 0]] > 0.01) | (share[edges[:, 1]] > 0.01)
            slope = (np.abs(share[edges[:, 0]] - share[edges[:, 1]]) / length)[near]
            assert slope.max() < (25.0 if part == "belly" else 22.0), (name, part, slope.max())               # (the belly lies between the pelvis's and the chest's own weights, so its share has steps of theirs in it)


@needs_designer
def test_with_all_four_parts_at_rest_the_body_moves_as_it_did_in_any_pose(made, soft_all):
    original, changed = ss.Rig(made["ordinary"][0]), ss.Rig(soft_all[0])
    def worst(turns):
        before, after = original.world(turns), changed.world(turns)
        return max(np.linalg.norm(original.skinned(m, before) - changed.skinned(m, after), axis=1).max() for m in original.meshes) * 1000
    assert worst(None) < 1e-3 and worst({"pelvis": ([0.2, 1.0, 0.4], 0.5)}) < 1e-3
    assert worst({"spine_01": ([1.0, 0.3, 0.2], 0.5)}) < softbody.DROP_COST * 1000 and worst({"thigh_l": ([1.0, 0.3, 0.2], 0.5)}) < softbody.DROP_COST * 1000
    rng = np.random.default_rng(11)
    bones = ["clavicle_l", "clavicle_r", "upperarm_l", "upperarm_r", "thigh_l", "thigh_r", "spine_01", "spine_03", "neck_01", "calf_l", "calf_r"]
    assert max(worst(ss.random_turns(rng, bones, 0.5)) for _ in range(8)) < 6.0


@needs_designer
def test_turning_a_new_spring_bone_moves_the_skin_around_its_part_and_nothing_else(soft_all):
    changed = ss.Rig(soft_all[0])
    rest = {m: changed.skinned(m, changed.world()) for m in changed.meshes}
    for part, side in (("belly", "c"), ("thighs", "l"), ("thighs", "r")):
        moved = changed.world({f"soft_{part}_{side}_01": ([1.0, 0.0, 0.0], 0.35)})
        for mesh in changed.meshes:
            shift = np.linalg.norm(changed.skinned(mesh, moved) - rest[mesh], axis=1)
            chain = [changed.names.index(f"soft_{part}_{side}_01"), changed.names.index(f"soft_{part}_{side}_02")]
            own = (np.isin(changed.meshes[mesh]["jn"], chain) & (changed.meshes[mesh]["wt"] > 0)).any(axis=1)
            assert (shift[~own] < 1e-9).all(), (part, side, mesh)                                                              # (a vertex with no weight on this chain does not move)
            assert shift[own].max() > 0.004 and shift.max() < 0.07, (part, side, mesh)


@needs_designer
def test_the_underwear_follows_the_belly_and_the_thighs_too(body, made):
    recipe = made["ordinary"][1]
    dressed, _worn = underwear.dress(body, bodymaker.dressing(recipe))
    data, report = softbody.apply(dressed, ALL)
    assert report["meshes"]["ForgeUnderwear"]["touched"] > 100 and softbody.check_file(data, set(ALL)) is None
    rig = ss.Rig(data)
    skin, shell = rig.meshes["ForgeBody"]["pos"], rig.meshes["ForgeUnderwear"]["pos"]
    moved, rest = rig.world({"soft_belly_c_01": ([1.0, 0.0, 0.0], 0.3), "soft_thighs_l_01": ([1.0, 0.0, 0.0], 0.3)}), rig.world()
    shift = {m: rig.skinned(m, moved) - rig.skinned(m, rest) for m in ("ForgeBody", "ForgeUnderwear")}
    followers = np.flatnonzero(np.linalg.norm(shift["ForgeUnderwear"], axis=1) > 0.003)
    assert len(followers) > 20
    for row in followers[::5]:
        nearest = np.argmin(np.linalg.norm(skin - shell[row], axis=1))
        assert np.linalg.norm(shift["ForgeUnderwear"][row] - shift["ForgeBody"][nearest]) < 0.003                              # (within 3 mm of the skin it lies on)


@needs_designer
def test_the_checks_know_the_new_parts_and_say_what_is_wrong_in_words(body, soft_all):
    data = soft_all[0]
    clean = lambda: copy.deepcopy(Glb(data).json)
    assert softbody.check(clean(), set(ALL)) is None and softbody.check(clean(), {"belly"}) and softbody.check(clean(), set(ALL) | {"knees"})
    doc = clean()
    names = {n.get("name"): i for i, n in enumerate(doc["nodes"])}
    doc["nodes"][names["thigh_l"]]["children"].remove(names["soft_thighs_l_01"])
    doc["nodes"][names["thigh_r"]].setdefault("children", []).append(names["soft_thighs_l_01"])
    assert "thighs are not hung on the thigh_l" in softbody.check(doc, set(ALL))
    doc = clean()
    names = {n.get("name"): i for i, n in enumerate(doc["nodes"])}
    doc["nodes"][names["spine_01"]]["children"].remove(names["soft_belly_c_01"])
    doc["nodes"][names["spine_02"]].setdefault("children", []).append(names["soft_belly_c_01"])
    assert "belly are not hung on the spine_01" in softbody.check(doc, set(ALL))
    doc = clean()
    names = {n.get("name"): i for i, n in enumerate(doc["nodes"])}
    doc["nodes"][names["soft_belly_c_01"]]["extras"]["forge_secondary"]["kind"] = "chest"
    assert "belly do not carry their settings" in softbody.check(doc, set(ALL))
    doc = clean()
    names = {n.get("name"): i for i, n in enumerate(doc["nodes"])}
    doc["nodes"][names["soft_thighs_r_01"]]["extras"]["forge_secondary"]["center_bone"] = "nowhere"
    assert "thighs name a centre bone the body has not got" in softbody.check(doc, set(ALL))
    doc["nodes"][names["soft_thighs_r_01"]]["extras"]["forge_secondary"]["center_bone"] = "spine_01"                              # (any bone the body has will do for the file)
    assert softbody.check(doc, set(ALL)) is None


@needs_designer
def test_a_build_is_checked_for_the_new_parts_too(body, soft_all):
    animated = with_clips(soft_all[0])
    assert len(bodymaker.check(body, animated, "low", soft=ALL)) == 85
    with pytest.raises(bodymaker.BodyError, match="soft-body bones that were asked for"):
        bodymaker.check(body, animated, "low", soft=BOTH)                                                                      # (more bones there than were asked for)
    with pytest.raises(bodymaker.BodyError, match="soft-body bones that were asked for"):
        bodymaker.check(body, with_clips(softbody.apply(body, BOTH)[0]), "low", soft=ALL)                                      # (asked for and not there)


@needs_designer
def test_the_same_body_and_request_give_the_same_file_with_the_new_parts(body, soft_all):
    assert softbody.apply(body, ALL)[0] == soft_all[0]
    a = softbody.apply(body, {"belly": 0.4, "thighs": 0.7}, softness=0.2)[0]
    assert a == softbody.apply(body, {"belly": 0.4, "thighs": 0.7}, softness=0.2)[0]
    assert block_of(a, "soft_belly_c_01")["stiffness"] == pytest.approx(softbody.SETTINGS["belly"]["stiffness"] * softbody.firmness(0.2), abs=1e-3) and block_of(a, "soft_thighs_r_01")["amount"] == 0.7


@needs_designer
def test_the_belly_and_the_thighs_name_the_root_as_the_centre_of_their_movement_and_the_chest_and_the_butt_name_none(soft_all):
    doc = Glb(soft_all[0]).json
    by_name = {n.get("name"): n for n in doc["nodes"]}
    for name in ("soft_belly_c_01", "soft_thighs_l_01", "soft_thighs_r_01"):
        assert by_name[name]["extras"]["forge_secondary"]["center_bone"] == "root"
    for name in ("soft_chest_l_01", "soft_chest_r_01", "soft_butt_l_01", "soft_butt_r_01"):
        assert "center_bone" not in by_name[name]["extras"]["forge_secondary"]                                                 # (the engine takes the bone they hang on)
    assert "root" in names_of(doc)                                                                                              # (the body has the bone they name)


@needs_designer
def test_the_centre_can_be_named_or_left_out_by_the_settings_asked_for_and_the_body_must_have_it(body):
    data, _report = softbody.apply(body, {"belly": 1.0, "thighs": 1.0}, settings={"belly": {"center_bone": "spine_01"}, "thighs": {"center_bone": None}})
    assert block_of(data, "soft_belly_c_01")["center_bone"] == "spine_01" and "center_bone" not in block_of(data, "soft_thighs_l_01")
    assert softbody.check_file(data, {"belly", "thighs"}) is None
    no_root = altered(body, lambda d: [n.update(name="renamed") for n in d["nodes"] if n.get("name") == "root"])
    assert softbody.check_file(softbody.apply(no_root, BOTH)[0], {"chest", "butt"}) is None                                    # (the chest and the butt need no centre of their own)
    for parts in ({"belly": 1.0}, {"thighs": 0.3}, ALL):
        with pytest.raises(SoftBodyError, match="own root bone, the centre of their movement"):
            softbody.apply(no_root, parts)
    with pytest.raises(SoftBodyError, match="belly need the body's own nowhere bone"):
        softbody.apply(body, {"belly": 1.0}, settings={"belly": {"center_bone": "nowhere"}})


def test_the_apex_of_the_belly_is_on_the_midline_however_lopsided_the_vertices_around_it_are():
    rng = np.random.default_rng(3)
    points = np.column_stack([rng.uniform(-0.05, 0.06, 400), rng.uniform(0.85, 0.91, 400), rng.uniform(0.07, 0.11, 400)])
    bones = {"spine_01": np.array([0.0, 0.87, 0.0])}
    apex = softbody._apex(points, bones, "belly", 1.0)
    assert apex[0] == 0.0 and 0.84 < apex[1] < 0.92 and 0.09 < apex[2] < 0.11                                                   # (the front of the cloud, on the middle)
    assert points[points[:, 2] > points[:, 2].max() - 0.006][:, 0].mean() != 0.0                                              # (which the plain mean of the vertices there would not be)


@needs_designer
def test_the_two_chains_of_a_part_are_mirror_images_and_each_leans_the_way_the_part_sticks_out(soft_all):
    doc = Glb(soft_all[0]).json
    by_name = {n.get("name"): i for i, n in enumerate(doc["nodes"])}
    world = softbody._worlds(doc)
    at = lambda name: world[by_name[name]][:3, 3]
    for part in ("chest", "butt", "thighs"):
        for n in ("01", "02"):
            left, right = at(f"soft_{part}_l_{n}"), at(f"soft_{part}_r_{n}")
            assert right == pytest.approx(left * np.array([-1.0, 1.0, 1.0]), abs=2e-4), (part, n)                               # (one body, two sides)
    for part, axis, sign in (("chest", 2, 1.0), ("butt", 2, -1.0), ("belly", 2, 1.0), ("thighs", 0, 1.0)):
        for tag, side in softbody.sides_of(part):
            first, second = at(f"soft_{part}_{tag}_01"), at(f"soft_{part}_{tag}_02")
            lean = (second - first)[axis] * sign * (side if axis == 0 else 1.0)
            assert lean > 0.03, (part, tag, lean)                                                                              # (the second bone is farther out than the first, in the direction the part sticks out)


def test_the_weight_on_bones_far_from_a_part_counts_against_it_and_a_spring_bone_of_an_earlier_part_does_not():
    names = ["root", "pelvis", "spine_01", "thigh_l", "upperarm_l", "calf_l"]
    near = ("spine", "pelvis", "thigh", "calf")
    entries = [[names.index("thigh_l"), 0.5], [names.index("upperarm_l"), 0.2], [names.index("pelvis"), 0.1], [len(names), 0.15], [len(names) + 3, 0.05]]       # (the last two are spring bones)
    assert softbody._foreign_weight(entries, {names.index("thigh_l")}, names, near) == pytest.approx(0.2)                       # (only the arm is far from a thigh; the pelvis is near; spring bones are nobody's)
    assert softbody._foreign_weight(entries, {names.index("thigh_l")}, names, ("spine",)) == pytest.approx(0.3)                 # (what is near depends on the part)
    assert softbody._foreign_weight([[len(names) + 1, 1.0]], set(), names, near) == 0.0


def test_the_window_of_the_chest_moves_with_the_bust_a_body_shape_hung_lower_and_no_other_mark_moves_it():
    assert softbody.window_shift({"version": 2, "source": "body_shape", "window_shift_m": -0.05}) == -0.05
    for odd in (None, {}, {"version": 1, "size": 0.5}, {"source": "body_shape"}, {"source": "body_shape", "window_shift_m": "far"}, {"source": "body_shape", "window_shift_m": float("nan")},
                {"source": "other", "window_shift_m": -0.05}, "mark"):
        assert softbody.window_shift(odd) == 0.0                                                                                # (the bust size's own mark, and anything damaged, move nothing)
    rng = np.random.default_rng(5)
    points = np.column_stack([rng.uniform(0.06, 0.12, 400), rng.uniform(1.00, 1.04, 400), rng.uniform(0.08, 0.13, 400)])      # a bust that lies a hand's breadth below where the window of the default body is
    bones = {"spine_03": np.array([0.0, 1.22, 0.0])}
    with pytest.raises(SoftBodyError, match="chest could not be found"):
        softbody._apex(points, bones, "chest", 1.0)
    low = softbody._apex(points, bones, "chest", 1.0, -0.12)
    assert 1.00 < low[1] < 1.04 and 0.06 < low[0] < 0.12 and low[2] > 0.12                                                    # (the front of the cloud, found once the window has moved with it)
    assert softbody._apex(points, bones, "chest", 1.0, -0.12 + 1e-12) == pytest.approx(low)                                  # (and the same shift always gives the same place)
