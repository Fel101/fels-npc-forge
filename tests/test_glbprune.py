"""npcgen/glbprune.py: nodes taken out of a GLB with everything only they used, and the rest left exactly as it was."""
import io
import json
import struct

import numpy as np
import pytest
from PIL import Image

from npcgen import glbprune
from npcgen.paint3d import Glb
from test_underwear import khronos


def _png(colour):
    out = io.BytesIO()
    Image.new("RGB", (4, 4), colour).save(out, "PNG")
    return out.getvalue()


def _glb(doc, blobs):
    """GLB bytes from a document whose bufferViews are still to be laid out: `blobs` are (their contents, their target or None) in order."""
    binary = bytearray()
    doc["bufferViews"] = []
    for blob, target in blobs:
        binary += b"\0" * (-len(binary) % 4)
        doc["bufferViews"].append({"buffer": 0, "byteOffset": len(binary), "byteLength": len(blob), **({"target": target} if target else {})})
        binary += blob
    binary += b"\0" * (-len(binary) % 4)
    doc["buffers"] = [{"byteLength": len(binary)}]
    text = json.dumps(doc, separators=(",", ":")).encode()
    text += b" " * (-len(text) % 4)
    return (struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(text) + 8 + len(binary)) + struct.pack("<II", len(text), 0x4E4F534A) + text
            + struct.pack("<II", len(binary), 0x004E4942) + bytes(binary))


def tiny(shared=True, unused=False):
    """A small rigged model: two joints (root, arm), three skinned mesh nodes (MeshA and MeshC share a material when `shared`), a picture on each material, one animation of the arm.
    With `unused` the binary also holds a buffer view nothing refers to (a file that is not as tidy as the pruner would leave it)."""
    blobs = []
    doc = {"asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": [0, 2, 3, 4]}],
           "nodes": [{"name": "root", "children": [1]}, {"name": "arm"}, {"name": "MeshA", "mesh": 0, "skin": 0}, {"name": "MeshB", "mesh": 1, "skin": 0}, {"name": "MeshC", "mesh": 2, "skin": 0}],
           "accessors": [], "meshes": [], "materials": [], "textures": [], "images": [], "samplers": [{"magFilter": 9729}, {"magFilter": 9728}], "skins": [], "animations": []}

    def accessor(array, kind, component, bounds=False, target=None):
        blobs.append((array.tobytes(), target))
        entry = {"bufferView": len(blobs) - 1, "componentType": component, "count": len(array), "type": kind}
        if bounds:
            entry["min"], entry["max"] = np.atleast_1d(array.min(0)).tolist(), np.atleast_1d(array.max(0)).tolist()
        doc["accessors"].append(entry)
        return len(doc["accessors"]) - 1

    for i, material in enumerate((0, 1, 0 if shared else 2)):
        points = np.array([[0, 0, 0], [1, 0, 0], [0, 1, i + 1]], np.float32)
        attributes = {"POSITION": accessor(points, "VEC3", 5126, True, 34962), "TEXCOORD_0": accessor(points[:, :2].copy(), "VEC2", 5126, False, 34962),
                      "JOINTS_0": accessor(np.array([[0, 1, 0, 0]] * 3, np.uint16), "VEC4", 5123, False, 34962),
                      "WEIGHTS_0": accessor(np.array([[0.5, 0.5, 0, 0]] * 3, np.float32), "VEC4", 5126, False, 34962)}
        doc["meshes"].append({"name": f"mesh{i}", "primitives": [{"attributes": attributes, "indices": accessor(np.array([0, 1, 2], np.uint16), "SCALAR", 5123, False, 34963),
                                                                  "material": material}]})
    for i, colour in enumerate(((200, 40, 40), (40, 40, 200), (40, 200, 40))[:2 if shared else 3]):
        blobs.append((_png(colour), None))
        doc["images"].append({"name": f"picture{i}", "bufferView": len(blobs) - 1, "mimeType": "image/png"})
        doc["textures"].append({"source": i, "sampler": i % 2})
        doc["materials"].append({"name": f"material{i}", "pbrMetallicRoughness": {"baseColorTexture": {"index": i}}})
    bind = accessor(np.tile(np.eye(4, dtype=np.float32).reshape(1, 16), (2, 1)), "MAT4", 5126)
    doc["skins"].append({"joints": [0, 1], "inverseBindMatrices": bind})
    times = accessor(np.array([0, 1], np.float32), "SCALAR", 5126, True)
    turns = accessor(np.array([[0, 0, 0, 1], [0, 0, 0.7071, 0.7071]], np.float32), "VEC4", 5126)
    doc["animations"].append({"name": "wave", "samplers": [{"input": times, "output": turns, "interpolation": "LINEAR"}], "channels": [{"sampler": 0, "target": {"node": 1, "path": "rotation"}}]})
    if unused:
        blobs.append((b"nothing refers to me", None))
    return _glb(doc, blobs)


def names(data):
    return [n.get("name") for n in Glb(data).json["nodes"]]


def mesh_arrays(glb, node_name):
    doc = glb.json
    node = next(n for n in doc["nodes"] if n.get("name") == node_name)
    primitive = doc["meshes"][node["mesh"]]["primitives"][0]
    return [glb.accessor(primitive["attributes"][key]) for key in sorted(primitive["attributes"])] + [glb.accessor(primitive["indices"])], doc["materials"][primitive["material"]]["name"]


def picture(glb, material_name):
    doc = glb.json
    material = next(m for m in doc["materials"] if m["name"] == material_name)
    image = doc["images"][doc["textures"][material["pbrMetallicRoughness"]["baseColorTexture"]["index"]]["source"]]
    return glb.view_bytes(image["bufferView"])


def test_a_node_goes_with_its_mesh_and_what_only_it_used_and_everything_else_is_as_it_was(tmp_path):
    data = tiny(shared=True)
    out, removed = glbprune.remove_nodes(data, ["MeshB"])
    before, after = Glb(data), Glb(out)
    assert removed == ["MeshB"] and names(out) == ["root", "arm", "MeshA", "MeshC"] and after.json["scenes"][0]["nodes"] == [0, 2, 3]
    assert [len(after.json[k]) for k in ("meshes", "materials", "textures", "images", "samplers")] == [2, 1, 1, 1, 1]       # (the second material, its picture and its sampler were MeshB's alone)
    assert len(after.json["accessors"]) == len(before.json["accessors"]) - 5 and len(after.bin) < len(before.bin)          # (MeshB's position, texture coordinates, joints, weights and indices; and nothing else)
    for name in ("MeshA", "MeshC"):                                                                                         # what stays is value for value what it was
        (new, new_material), (old, old_material) = mesh_arrays(after, name), mesh_arrays(before, name)
        assert new_material == old_material and all(np.array_equal(a, b) for a, b in zip(new, old)) and len(new) == len(old) == 5
    assert picture(after, "material0") == picture(before, "material0")
    assert after.json["skins"][0]["joints"] == [0, 1] and np.array_equal(after.accessor(after.json["skins"][0]["inverseBindMatrices"]), before.accessor(before.json["skins"][0]["inverseBindMatrices"]))
    animation = after.json["animations"][0]
    assert animation["name"] == "wave" and after.json["nodes"][animation["channels"][0]["target"]["node"]]["name"] == "arm"
    sampler = animation["samplers"][0]
    assert np.array_equal(after.accessor(sampler["output"]), before.accessor(before.json["animations"][0]["samplers"][0]["output"])) and after.accessor(sampler["input"]).ravel().tolist() == [0, 1]
    for name, blob in (("whole.glb", data), ("pruned.glb", out)):                                                           # (the Khronos validator is silent on the file and on what is left of it, where it is installed)
        (tmp_path / name).write_bytes(blob)
        assert khronos(tmp_path / name) in (None, (0, 0, 0, 0)), name


def test_a_material_another_node_still_uses_stays():
    out, _ = glbprune.remove_nodes(tiny(shared=True), ["MeshA"])                         # MeshC has material 0 too
    after = Glb(out)
    assert names(out) == ["root", "arm", "MeshB", "MeshC"] and [m["name"] for m in after.json["materials"]] == ["material0", "material1"]
    assert mesh_arrays(after, "MeshC")[1] == "material0" and mesh_arrays(after, "MeshB")[1] == "material1"
    assert len(after.json["images"]) == 2 and len(after.json["samplers"]) == 2


def test_several_nodes_at_once_and_the_names_that_were_taken_are_said():
    out, removed = glbprune.remove_nodes(tiny(shared=False), ["MeshC", "MeshA", "nothing"])
    after = Glb(out)
    assert removed == ["MeshA", "MeshC"] and names(out) == ["root", "arm", "MeshB"] and [m["name"] for m in after.json["materials"]] == ["material1"]
    assert len(after.json["meshes"]) == 1 and len(after.json["images"]) == 1


def test_a_name_that_is_not_there_gives_the_same_bytes_back():
    for data in (tiny(), tiny(unused=True)):                                     # (the second would come back smaller if the file were rewritten: it is not touched)
        assert glbprune.remove_nodes(data, ["nothing"]) == (data, []) and glbprune.remove_nodes(data, []) == (data, [])
    untidy = tiny(unused=True)
    pruned = glbprune.remove_nodes(untidy, ["MeshB"])[0]                          # (when it does remove something it also drops what nothing refers to: MeshB's five views, its picture and the stray one)
    assert len(pruned) < len(untidy) - 100 and len(Glb(pruned).json["bufferViews"]) == len(Glb(untidy).json["bufferViews"]) - 7


def test_every_mesh_can_go_and_the_skeleton_and_the_clip_stay(tmp_path):
    out, removed = glbprune.remove_nodes(tiny(), ["MeshA", "MeshB", "MeshC"])
    after = Glb(out)
    assert removed == ["MeshA", "MeshB", "MeshC"] and names(out) == ["root", "arm"] and not any(k in after.json for k in ("meshes", "materials", "textures", "images", "samplers"))
    assert after.accessor(after.json["animations"][0]["samplers"][0]["output"]).shape == (2, 4) and len(after.json["accessors"]) == 3       # (the matrices, the times, the turns)


def test_a_joint_cannot_be_taken_out_because_the_skeleton_would_break():
    data = tiny()
    for name in ("arm", "root"):
        with pytest.raises(ValueError, match="part of the skeleton"):
            glbprune.remove_nodes(data, [name])


def test_a_node_takes_its_children_with_it():
    glb = Glb(tiny())
    glb.json["nodes"][2]["children"] = [3]                                                 # MeshB hangs under MeshA
    glb.json["scenes"][0]["nodes"] = [0, 2, 4]
    out, removed = glbprune.remove_nodes(glb.dumps(), ["MeshA"])
    assert removed == ["MeshA", "MeshB"] and names(out) == ["root", "arm", "MeshC"]


def test_a_file_with_more_than_one_buffer_is_refused():
    glb = Glb(tiny())
    glb.json["buffers"].append({"byteLength": 4, "uri": "data:application/octet-stream;base64,AAAAAA=="})
    with pytest.raises(ValueError, match="one binary chunk"):
        glbprune.remove_nodes(glb.dumps(), ["MeshB"])
