"""npcgen/glbmerge.py: one GLB put inside another (glasses on a character's head)."""
import numpy as np
import pytest

from npcgen import glasses, glbmerge, procmesh as pm
from npcgen.paint3d import Glb


def rotation(axis, degrees):
    a = np.radians(degrees)
    c, s = np.cos(a), np.sin(a)
    return {"x": np.array([[1, 0, 0], [0, c, -s], [0, s, c]]), "y": np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]]),
            "z": np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])}[axis]


def world_of(rot, move, scale=1.0):
    m = np.eye(4)
    m[:3, :3] = rot * scale
    m[:3, 3] = move
    return m


def character(head_scale=1.0, with_clash=False):
    """A tiny stand-in for a rigged character: a body mesh and a skeleton of plain nodes, armature > hips > spine > head."""
    g = pm.Gltf("test")
    body = g.material("lens" if with_clash else "skin", (0.8, 0.6, 0.5), 0.0, 0.7)
    mesh = g.mesh("body", [(pm.cylinder(np.array([0, 0.0, 0]), np.array([0, 1.7, 0]), 0.2, 12), body)])
    head = g.node("head", translation=(0, 0.3, 0.01), scale=(head_scale,) * 3)
    spine = g.node("spine_03", children=[head], translation=(0, 0.5, 0), rotation=pm.yaw_quaternion(20))
    hips = g.node("pelvis", children=[spine], translation=(0, 0.9, 0))
    arm = g.node("Armature", children=[hips], translation=(0.1, 0, 0))
    body_node = g.node("body", mesh)
    return g.dumps([arm, body_node])


def glasses_glb(**recipe):
    glb, _info = glasses.build(recipe, faces=10_000)
    return glb


def test_matrix_helpers_round_trip():
    for axis in "xyz":
        for degrees in (0, 30, 90, 179, -120):
            r = rotation(axis, degrees)
            q = glbmerge.matrix_quaternion(r)
            assert np.allclose(glbmerge.quaternion_matrix(q), r, atol=1e-9), (axis, degrees)
    generic = rotation("y", 37) @ rotation("x", -50) @ rotation("z", 111)
    q = glbmerge.matrix_quaternion(generic)
    assert abs(np.linalg.norm(q) - 1) < 1e-9 and q[3] >= 0
    t, q2, s = glbmerge.decompose(glbmerge.trs_matrix((1, 2, 3), q, (0.5, 0.5, 0.5)))
    assert np.allclose(t, (1, 2, 3)) and np.allclose(s, 0.5) and np.allclose(glbmerge.quaternion_matrix(q2), generic)


def test_decompose_refuses_shear_and_mirrors():
    shear = np.eye(4)
    shear[0, 1] = 0.5
    assert glbmerge.decompose(shear) is None
    mirror = np.diag([-1.0, 1, 1, 1])
    assert glbmerge.decompose(mirror) is None


def test_world_matrices_and_find_node():
    doc = Glb(character()).json
    head = glbmerge.find_node(doc, "Head")                  # any capitals: the library's name for the bone
    assert head is not None and glbmerge.find_node(doc, "HEAD") == head
    assert glbmerge.find_node(doc, "no such bone") is None
    world = glbmerge.world_matrices(doc)[head]
    chain = (glbmerge.trs_matrix((0.1, 0, 0)) @ glbmerge.trs_matrix((0, 0.9, 0)) @ glbmerge.trs_matrix((0, 0.5, 0), pm.yaw_quaternion(20))
             @ glbmerge.trs_matrix((0, 0.3, 0.01)))
    assert np.allclose(world, chain)
    assert glbmerge.parents(doc)[head] == glbmerge.find_node(doc, "spine_03")


@pytest.mark.parametrize("scale", [1.0, 1.25])
def test_added_glasses_stand_where_they_were_placed_and_the_character_is_untouched(scale):
    target_bytes, extra_bytes = character(head_scale=scale), glasses_glb()
    before = Glb(target_bytes)
    world = world_of(rotation("y", 4) @ rotation("z", -3), (0.12, 1.62, 0.09), 1.1)
    merged_bytes, where = glbmerge.merge_bytes(target_bytes, extra_bytes, "head", world, tag={"npcforge": "glasses"})
    after, extra = Glb(merged_bytes), Glb(extra_bytes)
    assert where["parent"] == "head" and where["node"] == "glasses"
    # the character's own data is where it was
    for i in range(len(before.json["accessors"])):
        assert np.array_equal(before.accessor(i), after.accessor(i))
    kept = len(before.json["nodes"])
    assert [n["name"] for n in after.json["nodes"][:kept]] == [n["name"] for n in before.json["nodes"]]
    assert after.json["scenes"][0]["nodes"] == before.json["scenes"][0]["nodes"]
    # the new nodes are the glasses', renumbered, under the head
    head = glbmerge.find_node(after.json, "head")
    new_root = after.json["nodes"][head]["children"][-1]
    assert after.json["nodes"][new_root]["name"] == "glasses" and after.json["nodes"][new_root]["extras"] == {"npcforge": "glasses"}
    assert [after.json["nodes"][c]["name"] for c in after.json["nodes"][new_root]["children"]] == ["frame", "lenses", "temple_left", "temple_right"]
    assert np.allclose(glbmerge.world_matrices(after.json)[new_root], world, atol=1e-6)
    # their meshes read the same numbers, through accessors that moved
    first = len(before.json["meshes"])
    for k, mesh in enumerate(extra.json["meshes"]):
        for p, prim in enumerate(mesh["primitives"]):
            mine = after.json["meshes"][first + k]["primitives"][p]
            assert np.array_equal(extra.accessor(prim["attributes"]["POSITION"]), after.accessor(mine["attributes"]["POSITION"]))
            assert np.array_equal(extra.accessor(prim["indices"]), after.accessor(mine["indices"]))
            assert after.json["materials"][mine["material"]]["name"] == extra.json["materials"][prim["material"]]["name"]
    # the file is whole: every buffer view lies inside the binary chunk and the chunk is aligned
    assert len(after.bin) % 4 == 0 and after.json["buffers"][0]["byteLength"] == len(after.bin)
    for view in after.json["bufferViews"]:
        assert view["byteOffset"] + view["byteLength"] <= len(after.bin)


def test_without_a_parent_the_glasses_go_into_the_scene():
    world = world_of(np.eye(3), (0, 1.6, 0.1))
    merged, where = glbmerge.merge_bytes(character(), glasses_glb(), None, world)
    doc = Glb(merged).json
    assert where["parent"] is None
    root = doc["scenes"][0]["nodes"][-1]
    assert doc["nodes"][root]["name"] == "glasses" and np.allclose(glbmerge.world_matrices(doc)[root], world)


def test_a_gradient_lens_brings_its_texture_along():
    merged = Glb(glbmerge.merge_bytes(character(), glasses_glb(lens_gradient=True, lens_tint="grey"), "head", np.eye(4))[0])
    extra = Glb(glasses_glb(lens_gradient=True, lens_tint="grey"))
    assert len(merged.json["images"]) == 1 and len(merged.json["textures"]) == 1
    lens = next(m for m in merged.json["materials"] if m["name"] == "lens")
    texture = lens["pbrMetallicRoughness"]["baseColorTexture"]["index"]
    image = merged.json["textures"][texture]["source"]
    assert merged.view_bytes(merged.json["images"][image]["bufferView"]) == extra.view_bytes(extra.json["images"][0]["bufferView"])


def test_a_character_texture_and_the_glasses_texture_do_not_swap():
    """The character already has a texture: the glasses' index must move past it."""
    g = pm.Gltf("t")
    tex = g.texture(b"\x89PNG\r\n\x1a\n" + b"x" * 32)
    mat = g.material("skin", (1, 1, 1), 0.0, 0.6, texture=tex)
    mesh = g.mesh("body", [(pm.cylinder(np.array([0, 0.0, 0]), np.array([0, 1.7, 0]), 0.2, 12), mat)])
    target = g.dumps([g.node("head"), g.node("body", mesh)])
    merged = Glb(glbmerge.merge_bytes(target, glasses_glb(lens_gradient=True, lens_tint="grey"), "head", np.eye(4))[0])
    assert len(merged.json["textures"]) == 2
    skin = next(m for m in merged.json["materials"] if m["name"] == "skin")
    lens = next(m for m in merged.json["materials"] if m["name"] == "lens")
    assert skin["pbrMetallicRoughness"]["baseColorTexture"]["index"] == 0
    assert lens["pbrMetallicRoughness"]["baseColorTexture"]["index"] == 1
    assert merged.view_bytes(merged.json["images"][0]["bufferView"]).endswith(b"x" * 32)


def test_a_material_name_the_character_has_is_not_reused():
    merged = Glb(glbmerge.merge_bytes(character(with_clash=True), glasses_glb(), "head", np.eye(4))[0])
    names = [m["name"] for m in merged.json["materials"]]
    assert names.count("lens") == 1 and "lens (glasses)" in names


def test_things_that_cannot_be_carried_over_are_refused():
    target, extra = Glb(character()), Glb(glasses_glb())
    extra.json["skins"] = [{"joints": [0]}]
    with pytest.raises(ValueError, match="skins"):
        glbmerge.add(target, extra)


def test_a_missing_parent_falls_back_to_the_scene():
    merged, where = glbmerge.merge_bytes(character(), glasses_glb(), "no_such_bone", np.eye(4))
    assert where["parent"] is None and Glb(merged).json["nodes"][-1]["name"] in ("temple_right", "glasses")


def test_joint_names_lists_the_skins_joints():
    g = pm.Gltf("t")
    a, b = g.node("root"), g.node("head")
    doc = Glb(g.dumps([a, b])).json
    doc["skins"] = [{"joints": [a, b]}]
    assert glbmerge.joint_names(doc) == ["head", "root"]
