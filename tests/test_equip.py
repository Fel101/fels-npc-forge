"""npcgen/equip.py: a held item or a hat put on a rigged character (made-up characters and items: tests/equip_support.py)."""
import math

import numpy as np
import pytest

import equip_support as es
from npcgen import equip, glbmerge
from npcgen.paint3d import Glb

SWORD, SHIELD, HAT, HELMET, STAFF = (es.spec("weapon", "sword"), es.spec("armor", "shield"), es.spec("headwear", "hat"),
                                     es.spec("headwear", "helmet"), es.spec("weapon", "staff"))


def worn_points(merged, slot):
    """The world-space vertices of what was equipped in `slot`, from the merged file (every node under the tagged root)."""
    glb = Glb(merged)
    doc = glb.json
    world = glbmerge.world_matrices(doc)
    points = []
    for i, node in enumerate(doc["nodes"]):
        if (node.get("extras") or {}).get("slot") == slot:
            stack = [i]
            while stack:
                j = stack.pop()
                if "mesh" in doc["nodes"][j]:
                    for prim in doc["meshes"][doc["nodes"][j]["mesh"]]["primitives"]:
                        p = glb.accessor(prim["attributes"]["POSITION"]).astype(float)
                        points.append((np.c_[p, np.ones(len(p))] @ world[j].T)[:, :3])
                stack.extend(doc["nodes"][j].get("children", []))
    return np.vstack(points)


def equipped(character, *things, **kw):
    """(merged GLB, report) for things = (glb, kind, spec, slot, nudge)."""
    wearing = [{"glb": glb, "kind": kind, "spec": spec, "slot": slot, "nudge": nudge, "item": kind}
               for glb, kind, spec, slot, nudge in things]
    return equip.equip(character, wearing)


# ---- what can be equipped ----------------------------------------------------------------------------------------------------------
def test_the_kinds_that_can_be_carried_or_worn_and_the_ones_that_cannot_yet():
    table = es.items.load()["families"]
    got = {kind: equip.support(kind, spec) for fam in table.values() for kind, spec in fam["kinds"].items()}
    assert got["sword"] == {"carry": "blade", "slot": "hand_r"} and got["axe"]["carry"] == "blade" and got["dagger"]["carry"] == "blade"
    assert got["staff"] == {"carry": "pole", "slot": "hand_r"} and got["torch"]["carry"] == "pole" and got["spear"]["carry"] == "pole"
    assert got["bow"] == {"carry": "bow", "slot": "hand_l"} and got["shield"] == {"carry": "shield", "slot": "hand_l"}
    assert [got[k]["carry"] for k in ("helmet", "hat", "cap", "crown")] == ["hat"] * 4 and got["hat"]["slot"] == "head"
    for not_yet in ("crossbow", "pistol", "rifle", "gauntlets", "lantern", "bottle", "book", "sack", "boots", "coat", "glasses", "barrel"):
        assert got[not_yet] is None, not_yet
    assert equip.support("other", {"attach": "hand_r", "pivot": "grip", "pose": "upright"}) == {"carry": "blade", "slot": "hand_r"}
    assert equip.support("other", {"attach": "", "pivot": "base", "pose": "single"}) is None
    assert equip.slots_for("hat") == ("head",) and equip.slots_for("shield") == ("hand_l",) and equip.slots_for("blade") == ("hand_r", "hand_l")


def test_the_nudge_is_numbers_within_limits():
    assert equip.clean_nudge(None) == equip.NUDGE_DEFAULT == equip.clean_nudge({})
    assert equip.clean_nudge({"up": "0.05", "turn": 90, "size": 1.5})["size"] == 1.5
    for bad in ({"up": 1.0}, {"size": 0.1}, {"turn": 400}, {"tilt": "much"}, {"forward": True}, [1], "x"):
        with pytest.raises(ValueError):
            equip.clean_nudge(bad)


# ---- the character's rest pose -----------------------------------------------------------------------------------------------------
def test_the_rest_pose_gives_the_head_the_palms_and_which_way_it_faces():
    rest = equip.read_rest(es.skinned_character())
    assert np.allclose(rest.forward, [0, 0, 1], atol=1e-6) and np.allclose(rest.right, [-1, 0, 0], atol=1e-6)
    assert rest.height == pytest.approx(1.75, abs=0.01)
    assert rest.head["crown"] == pytest.approx(1.75, abs=0.005) and rest.head["width"] == pytest.approx(0.156, abs=0.01)
    assert rest.head["height"] == pytest.approx(0.217, abs=0.02) and np.allclose(rest.head["centre"], [0, 0, 0], atol=0.01)
    assert np.allclose(rest.palms["hand_r"], [-0.40, 0.82, 0.05], atol=0.03) and rest.palms["hand_l"][0] > 0.35 and rest.notes == []


def test_a_rig_built_the_other_way_round_is_read_from_where_its_hands_are():
    rest = equip.read_rest(es.skinned_character(facing=-1))
    assert np.allclose(rest.forward, [0, 0, -1], atol=1e-6) and np.allclose(rest.right, [1, 0, 0], atol=1e-6)
    merged, report = equipped(es.skinned_character(facing=-1), (es.sword_glb(), "sword", SWORD, None, None))
    tip = worn_points(merged, "hand_r")[:, 1].argmax()
    blade = worn_points(merged, "hand_r")[tip] - rest.palms["hand_r"]
    assert blade[2] < -0.3 and blade[1] > 0.2                                   # the blade points to the character's front, which is -Z here


def test_a_mesh_called_head_does_not_take_the_heads_place_as_a_joint():
    character = es.skinned_character(mesh_called="head")
    assert equip.read_rest(character).head["crown"] == pytest.approx(1.75, abs=0.005)
    merged, report = equipped(character, (es.helmet_glb(), "helmet", HELMET, None, None))
    doc = Glb(merged).json
    parent = next(n for n in doc["nodes"] if any(doc["nodes"][c].get("extras", {}).get("slot") == "head" for c in n.get("children", [])))
    assert parent["name"] == "head" and "mesh" not in parent                     # the joint, not the mesh of the same name


def test_a_model_without_a_skeleton_or_without_hands_is_refused_by_name():
    with pytest.raises(equip.EquipError, match="no skeleton"):
        equip.read_rest(es.skinned_character(rigged=False))
    with pytest.raises(equip.EquipError, match="has no skeleton"):
        equipped(es.skinned_character(rigged=False), (es.sword_glb(), "sword", SWORD, None, None))


# ---- hats --------------------------------------------------------------------------------------------------------------------------
def test_a_helmet_is_sized_to_the_head_and_its_dome_rests_on_the_crown():
    merged, report = equipped(es.skinned_character(), (es.helmet_glb(), "helmet", HELMET, None, None))
    helmet = worn_points(merged, "head")
    assert helmet[:, 1].max() == pytest.approx(1.75 + 0.017, abs=0.006)           # the top of the dome, a hair above the crown (its pole sticks out a little)
    assert np.ptp(helmet[:, 0]) == pytest.approx(0.156 * 1.15 * 0.22 / 0.22, abs=0.02)      # its width: the head's, and a little more
    assert helmet[:, 0].mean() == pytest.approx(0.0, abs=0.005) and helmet[:, 2].mean() == pytest.approx(0.0, abs=0.01)
    item = report["items"][0]
    assert (item["slot"], item["bone"], item["attach"]["bone"]) == ("head", "head", "head")
    assert item["scale"] == pytest.approx(1.15 * 0.156 / 0.22, abs=0.03)


def test_a_bigger_head_gets_a_bigger_helmet_and_a_hat_is_anchored_by_its_band():
    small = equipped(es.skinned_character(), (es.helmet_glb(), "helmet", HELMET, None, None))[1]["items"][0]["scale"]
    large = equipped(es.skinned_character(head_scale=1.5), (es.helmet_glb(), "helmet", HELMET, None, None))[1]["items"][0]["scale"]
    assert large == pytest.approx(small * 1.5, rel=0.06)
    merged, _ = equipped(es.skinned_character(), (es.hat_glb(), "hat", HAT, None, None))
    hat = worn_points(merged, "head")
    head_height = 0.217
    assert hat[:, 1].min() == pytest.approx(1.75 - 0.27 * head_height, abs=0.01)  # the base: under the crown by a quarter of the head
    assert hat[:, 1].max() > 1.75                                                   # its crown stands above the head
    assert np.ptp(hat[:, 0]) > 0.3                                                  # a brim as wide as it was made


def test_the_head_nudge_moves_turns_and_scales_the_hat():
    base, _ = equipped(es.skinned_character(), (es.hat_glb(), "hat", HAT, None, None))
    moved, _ = equipped(es.skinned_character(), (es.hat_glb(), "hat", HAT, None, {"up": 0.05, "forward": 0.03, "side": -0.02, "size": 1.5}))
    a, b = worn_points(base, "head"), worn_points(moved, "head")
    assert b[:, 2].mean() - a[:, 2].mean() == pytest.approx(0.03, abs=0.004) and b[:, 0].mean() - a[:, 0].mean() == pytest.approx(0.02, abs=0.004)
    assert np.ptp(b[:, 0]) == pytest.approx(np.ptp(a[:, 0]) * 1.5, rel=0.02)
    turned, _ = equipped(es.skinned_character(), (es.helmet_glb(), "helmet", HELMET, None, {"turn": 90}))
    assert worn_points(turned, "head")[:, 1].max() == pytest.approx(1.767, abs=0.006)      # (a round helmet looks the same turned)


# ---- held items --------------------------------------------------------------------------------------------------------------------
def test_a_sword_is_gripped_in_the_palm_with_its_blade_forward_and_up():
    character = es.skinned_character()
    rest = equip.read_rest(character)
    merged, report = equipped(character, (es.sword_glb(), "sword", SWORD, None, None))
    points = worn_points(merged, "hand_r")
    top = points[np.argmax(points @ np.array([0.0, math.sin(math.radians(35)), math.cos(math.radians(35))]))]       # the tip
    blade = top - rest.palms["hand_r"]
    angle = math.degrees(math.atan2(blade[1], blade[2]))
    assert angle == pytest.approx(35, abs=4) and abs(blade[0]) < 0.08              # forward and up, not sideways
    assert np.linalg.norm(blade) == pytest.approx(0.9, abs=0.05)                  # 1 m long, the grip a tenth up: 0.9 m past the palm
    centre = points.mean(axis=0)                                                   # the sword's own line (its long axis), and the palm lies on it
    axis = np.linalg.svd(points - centre, full_matrices=False)[2][0]
    palm = rest.palms["hand_r"] - centre
    assert np.linalg.norm(palm - axis * float(palm @ axis)) < 0.012                # the grip passes through the palm
    item = report["items"][0]
    assert (item["slot"], item["bone"]) == ("hand_r", "hand_r") and item["scale"] == 1.0   # held items are not scaled to the hand


def test_a_sword_goes_in_the_left_hand_when_asked_and_a_second_item_in_the_same_hand_is_refused():
    character = es.skinned_character()
    merged, report = equipped(character, (es.sword_glb(), "sword", SWORD, "hand_l", None))
    assert report["items"][0]["slot"] == "hand_l" and worn_points(merged, "hand_l")[:, 0].mean() > 0.3
    with pytest.raises(equip.EquipError, match="already carries"):
        equipped(character, (es.sword_glb(), "sword", SWORD, None, None), (es.staff_glb(), "staff", STAFF, "hand_r", None))
    with pytest.raises(equip.EquipError, match="cannot go in the right hand"):
        equipped(character, (es.shield_glb(), "shield", SHIELD, "hand_r", None))
    with pytest.raises(equip.EquipError, match="cannot go in the head"):
        equipped(character, (es.sword_glb(), "sword", SWORD, "head", None))


def test_a_staff_stands_up_in_the_hand_and_a_shield_faces_forward_on_the_left_forearm():
    character = es.skinned_character()
    rest = equip.read_rest(character)
    merged, report = equipped(character, (es.staff_glb(), "staff", STAFF, None, None), (es.shield_glb(), "shield", SHIELD, None, None))
    staff = worn_points(merged, "hand_r")
    assert staff[:, 1].max() - staff[:, 1].min() > 1.6 and np.ptp(staff[:, 2]) < 0.4                # nearly vertical, leaning a little forward
    assert staff[staff[:, 1].argmax(), 2] > staff[staff[:, 1].argmin(), 2]
    shield = worn_points(merged, "hand_l")
    assert np.ptp(shield[:, 2]) < 0.08 and np.ptp(shield[:, 1]) == pytest.approx(0.7, abs=0.03)    # a disc facing forward, upright
    assert shield[:, 2].min() > rest.joints["hand_l"][2] - 0.02                                      # in front of the arm, not through it
    assert shield[:, 2].min() >= rest.hand_front["hand_l"] + 0.005 and shield[:, 2].min() <= rest.hand_front["hand_l"] + 0.03   # and just beyond the hand: held from behind
    assert abs(shield[:, 0].mean() - 0.35) < 0.08 and 0.8 < shield[:, 1].mean() < 1.2                # on the left forearm
    assert [(i["kind"], i["slot"]) for i in report["items"]] == [("staff", "hand_r"), ("shield", "hand_l")]


def test_the_hand_nudge_moves_turns_and_tilts_the_item():
    character = es.skinned_character()
    base = worn_points(equipped(character, (es.sword_glb(), "sword", SWORD, None, None))[0], "hand_r")
    moved = worn_points(equipped(character, (es.sword_glb(), "sword", SWORD, None, {"up": 0.1, "side": 0.05}))[0], "hand_r")
    assert (moved - base).mean(0) == pytest.approx([-0.05, 0.1, 0.0], abs=0.006)             # (right is -X)
    flat = worn_points(equipped(character, (es.sword_glb(), "sword", SWORD, None, {"tilt": -35}))[0], "hand_r")
    assert abs(flat[:, 1].max() - 0.82) < 0.2 and (flat[:, 2].max() > base[:, 2].max() - 0.2)       # the blade lowered towards level
    assert flat[:, 1].max() < base[:, 1].max()
    big = worn_points(equipped(character, (es.sword_glb(), "sword", SWORD, None, {"size": 2.0}))[0], "hand_r")
    assert np.ptp(big[:, 1]) + np.ptp(big[:, 2]) > (np.ptp(base[:, 1]) + np.ptp(base[:, 2])) * 1.6


# ---- the merged file -----------------------------------------------------------------------------------------------------------------
def test_the_items_hang_under_their_bones_and_the_attach_block_is_what_a_game_needs():
    character = es.skinned_character()
    merged, report = equipped(character, (es.sword_glb(), "sword", SWORD, None, None), (es.hat_glb(), "hat", HAT, None, None))
    doc = Glb(merged).json
    parents = glbmerge.parents(doc)
    for entry in report["items"]:
        root = next(i for i, n in enumerate(doc["nodes"]) if (n.get("extras") or {}).get("slot") == entry["slot"])
        assert doc["nodes"][parents[root]]["name"] == entry["bone"]
        assert doc["nodes"][root]["extras"] == {"npcforge": "equip", "slot": entry["slot"], "item": entry["kind"]}
        attach = entry["attach"]
        assert attach["bone"] == entry["bone"] and len(attach["translation"]) == 3 and len(attach["rotation"]) == 4
    original = Glb(character).json
    assert original["skins"] == doc["skins"]                                      # the character is untouched: same skeleton, same nodes ...
    for before, after in zip(original["nodes"], doc["nodes"]):
        assert {k: v for k, v in before.items() if k != "children"} == {k: v for k, v in after.items() if k != "children"}
        assert after.get("children", [])[:len(before.get("children", []))] == before.get("children", [])      # ... only a bone has children more
    assert len(doc["nodes"]) > len(original["nodes"])


def test_nothing_to_equip_and_an_unknown_kind_are_refused():
    character = es.skinned_character()
    with pytest.raises(equip.EquipError, match="Nothing to equip"):
        equip.equip(character, [])
    with pytest.raises(equip.EquipError, match="Pistol: equipping a pistol is not done yet"):
        equip.equip(character, [{"glb": es.sword_glb(), "kind": "pistol", "spec": es.spec("weapon", "pistol"), "name": "Pistol"}])


def test_a_character_that_is_found_a_little_wrong_says_so_in_the_notes():
    # no vertices carried by the head joint: its size is guessed, and the report says so
    glb = Glb(es.skinned_character())
    attrs = glb.json["meshes"][0]["primitives"][0]["attributes"]
    joints = glb.accessor(attrs["JOINTS_0"]).copy()
    joints[joints == list(es.JOINTS).index("head")] = list(es.JOINTS).index("neck_01")
    glb.set_accessor(attrs["JOINTS_0"], joints.astype(np.uint32))
    glb.json["accessors"][attrs["JOINTS_0"]]["componentType"] = 5125
    rest = equip.read_rest(glb)
    assert any("head joint" in note for note in rest.notes) and rest.head["width"] == pytest.approx(0.155 * rest.unit, rel=0.01)
