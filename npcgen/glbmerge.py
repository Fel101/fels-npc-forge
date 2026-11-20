"""Put one GLB inside another: glasses (npcgen/glasses.py) on a character's head (npcgen/facefit.py), docs/glasses.md.

The second file's nodes, meshes, materials, textures and binary data are appended to the first file, and its root node becomes a child
of a node of the first (the character's `head` joint, so the pair follows the head in every animation) or of its scene. Nothing of the
first file is rewritten: its buffers, accessors, skins, animations and node numbers stay as they are, so a rigged character still plays
every clip. The new root node is given a transform in its parent's own space (`local_to`), so the pair stays where it was placed in the
world however the skeleton is posed at rest.

Matrices here are numpy 4x4 arrays used as `world = matmul(M, local)` (glTF stores them column by column).
"""
import copy

import numpy as np

from .paint3d import Glb
from .procmesh import matmul      # (no @ operator in NPC Forge: see procmesh.matmul)

TEXTURE_SLOTS = ("baseColorTexture", "metallicRoughnessTexture", "normalTexture", "occlusionTexture", "emissiveTexture")


def quaternion_matrix(q):
    x, y, z, w = (float(v) for v in q)
    n = x * x + y * y + z * z + w * w
    if n < 1e-12:
        return np.eye(3)
    s = 2.0 / n
    xx, yy, zz = x * x * s, y * y * s, z * z * s
    xy, xz, yz = x * y * s, x * z * s, y * z * s
    wx, wy, wz = w * x * s, w * y * s, w * z * s
    return np.array([[1 - yy - zz, xy - wz, xz + wy], [xy + wz, 1 - xx - zz, yz - wx], [xz - wy, yz + wx, 1 - xx - yy]])


def matrix_quaternion(r):
    """The glTF rotation (x, y, z, w) of a 3x3 rotation matrix."""
    r = np.asarray(r, float)
    t = np.trace(r)
    if t > 0:
        s = 0.5 / np.sqrt(t + 1.0)
        q = [(r[2, 1] - r[1, 2]) * s, (r[0, 2] - r[2, 0]) * s, (r[1, 0] - r[0, 1]) * s, 0.25 / s]
    elif r[0, 0] > r[1, 1] and r[0, 0] > r[2, 2]:
        s = 2.0 * np.sqrt(1.0 + r[0, 0] - r[1, 1] - r[2, 2])
        q = [0.25 * s, (r[0, 1] + r[1, 0]) / s, (r[0, 2] + r[2, 0]) / s, (r[2, 1] - r[1, 2]) / s]
    elif r[1, 1] > r[2, 2]:
        s = 2.0 * np.sqrt(1.0 + r[1, 1] - r[0, 0] - r[2, 2])
        q = [(r[0, 1] + r[1, 0]) / s, 0.25 * s, (r[1, 2] + r[2, 1]) / s, (r[0, 2] - r[2, 0]) / s]
    else:
        s = 2.0 * np.sqrt(1.0 + r[2, 2] - r[0, 0] - r[1, 1])
        q = [(r[0, 2] + r[2, 0]) / s, (r[1, 2] + r[2, 1]) / s, 0.25 * s, (r[1, 0] - r[0, 1]) / s]
    q = np.array(q, float)
    q /= np.linalg.norm(q)
    return q if q[3] >= 0 else -q


def trs_matrix(translation=(0, 0, 0), rotation=(0, 0, 0, 1), scale=(1, 1, 1)):
    m = np.eye(4)
    m[:3, :3] = quaternion_matrix(rotation) * np.asarray(scale, float)[None, :]
    m[:3, 3] = translation
    return m


def local_matrix(node):
    """A node's own transform."""
    if "matrix" in node:
        return np.array(node["matrix"], float).reshape(4, 4).T
    return trs_matrix(node.get("translation", (0, 0, 0)), node.get("rotation", (0, 0, 0, 1)), node.get("scale", (1, 1, 1)))


def decompose(m, tolerance=1e-4):
    """(translation, rotation quaternion, scale) of a matrix made of those, or None when it has shear or a mirror."""
    m = np.asarray(m, float)
    scale = np.linalg.norm(m[:3, :3], axis=0)
    if (scale < 1e-9).any():
        return None
    rot = m[:3, :3] / scale[None, :]
    if np.abs(matmul(rot.T, rot) - np.eye(3)).max() > tolerance or np.linalg.det(rot) < 0:
        return None
    return m[:3, 3].copy(), matrix_quaternion(rot), scale


def parents(doc):
    """{node index: its parent's index} for every node that has one."""
    return {child: i for i, node in enumerate(doc.get("nodes", [])) for child in node.get("children", [])}


def world_matrices(doc):
    """{node index: world matrix} for every node reachable from the default scene."""
    nodes = doc.get("nodes", [])
    scenes = doc.get("scenes") or [{"nodes": list(range(len(nodes)))}]
    roots = scenes[doc.get("scene", 0)].get("nodes", [])
    out, stack = {}, [(r, np.eye(4)) for r in roots]
    while stack:
        i, parent = stack.pop()
        world = matmul(parent, local_matrix(nodes[i]))
        out[i] = world
        stack.extend((c, world) for c in nodes[i].get("children", []))
    return out


def find_node(doc, name):
    """The index of the first node called `name` (any capitals), or None. The pipeline's own rigs call the head bone `head` and the animation
    library calls it `Head`. A joint of a skin is preferred to another node of the same name (a mesh called `head`)."""
    wanted = (name or "").lower()
    nodes = doc.get("nodes", [])
    joints = sorted({j for skin in doc.get("skins", []) for j in skin.get("joints", []) if 0 <= j < len(nodes)})
    for i in joints + [i for i in range(len(nodes)) if i not in set(joints)]:
        if (nodes[i].get("name") or "").lower() == wanted:
            return i
    return None


def joint_names(doc):
    """The names of the nodes that are joints of a skin."""
    nodes = doc.get("nodes", [])
    return sorted({nodes[j].get("name") or f"node {j}" for skin in doc.get("skins", []) for j in skin.get("joints", [])})


def local_to(doc, parent, world):
    """The local transform a new child of `parent` (a node index, or None for the scene) needs to stand at `world`."""
    if parent is None:
        return np.asarray(world, float)
    return matmul(np.linalg.inv(world_matrices(doc)[parent]), world)


def _shift_textures(material, textures):
    pbr = material.get("pbrMetallicRoughness") or {}
    for holder in (pbr, material):
        for slot in TEXTURE_SLOTS:
            if isinstance(holder.get(slot), dict) and "index" in holder[slot]:
                holder[slot]["index"] += textures


def add(target, extra, parent=None, matrix=None, tag=None):
    """Append `extra`'s default scene to `target` (both `Glb`, changed in place; `target` is the one whose bytes are kept). The root node(s)
    of the scene go under node `parent` (an index in `target`) or, with None, into the target's scene; `matrix` (4x4, in the parent's
    space) is applied on top of each root's own transform. `tag` is written into the roots' extras. Returns the new root node indexes.
    Raises ValueError when `extra` holds something this does not carry over (skins, animations, cameras)."""
    doc, new = target.json, copy.deepcopy(extra.json)
    for key in ("skins", "animations", "cameras"):
        if new.get(key):
            raise ValueError(f"A GLB with {key} cannot be put inside another")
    bin_ = bytearray(target.bin)
    bin_ += b"\0" * (-len(bin_) % 4)
    base = len(bin_)
    bin_ += extra.bin
    bin_ += b"\0" * (-len(bin_) % 4)
    counts = {k: len(doc.get(k, [])) for k in ("bufferViews", "accessors", "images", "samplers", "textures", "materials", "meshes", "nodes")}
    for view in new.get("bufferViews", []):
        view["buffer"] = 0
        view["byteOffset"] = view.get("byteOffset", 0) + base
    for acc in new.get("accessors", []):
        if "bufferView" in acc:
            acc["bufferView"] += counts["bufferViews"]
    for image in new.get("images", []):
        if "bufferView" in image:
            image["bufferView"] += counts["bufferViews"]
    for texture in new.get("textures", []):
        if "source" in texture:
            texture["source"] += counts["images"]
        if "sampler" in texture:
            texture["sampler"] += counts["samplers"]
    taken = {m.get("name") for m in doc.get("materials", []) if m.get("name")}
    for material in new.get("materials", []):
        _shift_textures(material, counts["textures"])
        if material.get("name") and material["name"] in taken:     # the character has a material with this name already
            material["name"] = f"{material['name']} ({(tag or {}).get('npcforge') or 'glasses'})"
    for mesh in new.get("meshes", []):
        for prim in mesh.get("primitives", []):
            prim["attributes"] = {k: v + counts["accessors"] for k, v in prim["attributes"].items()}
            if "indices" in prim:
                prim["indices"] += counts["accessors"]
            if "material" in prim:
                prim["material"] += counts["materials"]
    for node in new.get("nodes", []):
        if "mesh" in node:
            node["mesh"] += counts["meshes"]
        if "children" in node:
            node["children"] = [c + counts["nodes"] for c in node["children"]]
    scene = (new.get("scenes") or [{"nodes": [0]}])[new.get("scene", 0)]
    roots = [r + counts["nodes"] for r in scene.get("nodes", [])]
    for key in ("bufferViews", "accessors", "images", "samplers", "textures", "materials", "meshes", "nodes"):
        if new.get(key):
            doc.setdefault(key, []).extend(new[key])
    for key in ("extensionsUsed", "extensionsRequired"):
        if new.get(key):
            doc[key] = sorted(set(doc.get(key, [])) | set(new[key]))
    doc["buffers"] = [{"byteLength": len(bin_)}]
    target.bin = bytes(bin_)
    for r in roots:
        node = doc["nodes"][r]
        if matrix is not None:
            combined = matmul(matrix, local_matrix(node))
            parts = decompose(combined)
            for key in ("matrix", "translation", "rotation", "scale"):
                node.pop(key, None)
            if parts is None:
                node["matrix"] = [float(v) for v in combined.T.reshape(16)]
            else:
                node["translation"], node["rotation"], node["scale"] = ([float(v) for v in p] for p in parts)
        if tag:
            node["extras"] = {**node.get("extras", {}), **tag}
    if parent is None:
        doc.setdefault("scenes", [{"nodes": []}])[doc.get("scene", 0)].setdefault("nodes", []).extend(roots)
    else:
        doc["nodes"][parent].setdefault("children", []).extend(roots)
    return roots


def _attach(doc, parent, local):
    """The attach block of a transform in a node's own space (see `attachment`)."""
    out = {"bone": doc["nodes"][parent].get("name") if parent is not None else None}
    parts = decompose(local)
    if parts is None:
        out["matrix"] = [float(v) for v in np.asarray(local, float).T.reshape(16)]
    else:
        out["translation"], out["rotation"], out["scale"] = ([round(float(v), 6) for v in p] for p in parts)
    return out


def merge_bytes(target_glb, extra_glb, parent_name=None, world=None, tag=None):
    """GLB bytes with `extra_glb` put inside `target_glb`. `world` (4x4) is where the extra's origin stands in the target's world space; it goes
    under the node called `parent_name` (any capitals) when there is one, else into the scene. Returns (bytes, {"parent", "node", "attach"}),
    "attach" being `attachment`'s block (None without a `world`)."""
    target, extra = Glb(target_glb), Glb(extra_glb)
    parent = find_node(target.json, parent_name) if parent_name else None
    local = None if world is None else local_to(target.json, parent, world)
    attach = None if local is None else _attach(target.json, parent, local)
    roots = add(target, extra, parent=parent, matrix=local, tag=tag)
    where = (target.json["nodes"][parent].get("name") if parent is not None else None)
    return target.dumps(), {"parent": where, "node": target.json["nodes"][roots[0]].get("name") if roots else None, "attach": attach}


def attachment(target_glb, parent_name, world):
    """Where something standing at `world` (4x4, the target's world space) is in the space of the node called `parent_name` (any capitals): what
    a game needs to attach the thing itself to that bone instead of using a merged file. Returns {"bone": the node's name, or None when there is
    no such node and the numbers are in world space, "translation": [x, y, z], "rotation": [x, y, z, w], "scale": [x, y, z]}; with a shear
    (a bone scaled unevenly and turned) it is {"bone", "matrix": 16 numbers, column by column} instead. The bone's own rest pose is part of its space."""
    doc = Glb(target_glb).json
    parent = find_node(doc, parent_name) if parent_name else None
    return _attach(doc, parent, local_to(doc, parent, world))
