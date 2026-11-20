"""Plain underwear for a designed body: a thin shell of the body's own triangles whose edges are the alpha of its own texture.

The Body Designer's bodies are bare. This puts a skin-tight layer on one (plain GLB or the animated one) and touches nothing the body already has:
  - the shell is the triangles of the body that the garment reaches, pushed `offset_mm` out along their normals (seam copies welded, so no seam opens), with the body's own UVs,
    joints and weights: it is skinned exactly like the body and cannot sink into it more than the body does (the body's 80-pose clearance audit passes with it);
  - where the garment ends is decided per texel of its own texture by smooth terms over the body's rest surface: a plane for a waistband, the contour of the leg bones' skin weight for a
    leg opening (it follows the hip crease), a band of heights between the arm bones for a bandeau. So the edges are smooth however coarse the mesh is, and the mesh has no cut to scallop;
  - one skinned mesh `ForgeUnderwear` (one material, glTF alphaMode MASK, one texture of `texture` px) is added to the file, so any consumer can drop it, and the skeleton, the
    clips and every other mesh are left as they were.
The tables are in data/underwear.json (kinds, who wears them, where their edges are). Nothing here needs the Body Designer package, Blender or a GPU: a body is a GLB.
"""
import io
import json
import re
import struct
from functools import lru_cache

import numpy as np
from PIL import Image

from . import config as cfgmod
from .paint3d import Glb

DATA_FILE = cfgmod.ROOT / "data" / "underwear.json"
NAME = "ForgeUnderwear"
ARMS = ("upperarm", "lowerarm", "hand", "thumb", "index", "middle", "ring", "pinky")
LEGS = ("thigh", "calf", "foot", "ball")


@lru_cache(maxsize=1)
def data():
    return json.loads(DATA_FILE.read_text("utf-8"))


def kinds_for(sex):
    """The pieces a body of this sex wears: ['briefs', 'bandeau'] for the female body, ['briefs'] for the male one, [] for anything else."""
    return [kind for kind, spec in data()["kinds"].items() if sex in spec["sexes"]]


def _open(blob):
    try:
        return Glb(blob)
    except (ValueError, struct.error, KeyError):                 # (a JSON error is a ValueError too)
        raise ValueError("The body is not a GLB file") from None


def worn(blob):
    """True when this GLB already has its underwear on."""
    return any(m.get("name") == NAME for m in _open(blob).json.get("meshes", []))


def _welded(points, triangles):
    """(group of each vertex, area-weighted normal of each vertex): vertices at one place (the UV seams' copies) are one group and share a normal."""
    groups = np.unique(np.rint(points * 1e6).astype(np.int64), axis=0, return_inverse=True)[1].ravel()
    normals = np.zeros((int(groups.max()) + 1, 3))
    faces = points[triangles]
    area = np.cross(faces[:, 1] - faces[:, 0], faces[:, 2] - faces[:, 0])
    for corner in range(3):
        np.add.at(normals, groups[triangles[:, corner]], area)
    normals /= np.maximum(np.linalg.norm(normals, axis=1)[:, None], 1e-30)
    return groups, normals[groups]


def _smooth(values, groups, triangles, iterations=6):
    """A per-vertex field smoothed over the welded mesh, so that a contour of it has no wobble the rig's weights happen to have."""
    count = int(groups.max()) + 1
    field = np.zeros(count)
    np.add.at(field, groups, values)
    field /= np.bincount(groups, minlength=count)
    edges = np.unique(np.sort(groups[triangles[:, [0, 1, 1, 2, 2, 0]]].reshape(-1, 2), axis=1), axis=0)
    edges = edges[edges[:, 0] != edges[:, 1]]
    degree = np.maximum(np.bincount(edges.ravel(), minlength=count), 1)
    for _ in range(iterations):
        total = np.zeros(count)
        np.add.at(total, edges[:, 0], field[edges[:, 1]])
        np.add.at(total, edges[:, 1], field[edges[:, 0]])
        field = 0.5 * field + 0.5 * total / degree
    return field[groups]


def _per_metre(values, points, triangles):
    """How fast a weight field changes along the surface (its median change per metre where it is changing): turns a weight difference into about how many metres away a contour is."""
    a, b = triangles[:, [0, 1, 2]].ravel(), triangles[:, [1, 2, 0]].ravel()
    low, high = np.minimum(values[a], values[b]), np.maximum(values[a], values[b])
    steep = (high - low > 1e-3) & (low < 0.7) & (high > 0.2)
    rate = (high - low)[steep] / np.maximum(np.linalg.norm(points[a] - points[b], axis=1)[steep], 1e-6)
    return float(np.median(rate)) if rate.size else 1.0


def _read(glb):
    """The body's arrays and joint measures, or ValueError saying what is wrong with it."""
    doc = glb.json
    if len(doc.get("skins", [])) != 1 or not doc.get("meshes"):
        raise ValueError("The body has no single skeleton and mesh to put underwear on")
    prim = doc["meshes"][0]["primitives"][0]
    attrs = prim["attributes"]
    for need in ("POSITION", "TEXCOORD_0", "JOINTS_0", "WEIGHTS_0"):
        if need not in attrs:
            raise ValueError(f"The body has no {need}")
    if "targets" in prim or "JOINTS_1" in attrs:
        raise ValueError("A body with morph targets or more than four weights cannot wear underwear")
    skin = doc["skins"][0]
    names = [doc["nodes"][j].get("name", "") for j in skin["joints"]]
    inverse = glb.accessor(skin["inverseBindMatrices"]).astype(float).reshape(-1, 4, 4).transpose(0, 2, 1)
    heads = np.linalg.inv(inverse)[:, :3, 3]
    for need in ("spine_01", "spine_03"):
        if need not in names:
            raise ValueError(f"The body's skeleton has no {need} (the Unreal mannequin's bone names are expected)")
    points = glb.accessor(attrs["POSITION"]).astype(float)
    triangles = glb.accessor(prim["indices"]).astype(np.int64).reshape(-1, 3)
    joints = glb.accessor(attrs["JOINTS_0"]).astype(np.int64)
    weights = glb.accessor(attrs["WEIGHTS_0"]).astype(float)
    uv = glb.accessor(attrs["TEXCOORD_0"]).astype(float)
    if np.max(np.abs(weights.sum(1) - 1)) > 1e-4:
        raise ValueError("The body's skin weights do not sum to one")
    return {"points": points, "triangles": triangles, "joints": joints, "weights": weights, "uv": uv, "names": names, "heads": heads}


def _terms(spec, kind, point, fields, heights):
    """The smooth distances (metres, positive on the garment) of a piece's edges at the surface points given: the smallest decides where the garment is."""
    arm = {side: (spec["arm_weight"] - fields["arm_" + side]) / fields["per_metre"] for side in "lr"}
    if kind == "briefs":
        return {"waist": heights["spine1"] + spec["above_waist_m"] - point[..., 1], "leg_l": (spec["leg_weight"] - fields["leg_l"]) / fields["per_metre"],
                "leg_r": (spec["leg_weight"] - fields["leg_r"]) / fields["per_metre"], "arm_l": arm["l"], "arm_r": arm["r"]}
    return {"lower": point[..., 1] - heights["apex"] + spec["below_apex_m"], "upper": heights["apex"] + spec["above_apex_m"] - point[..., 1], "arm_l": arm["l"], "arm_r": arm["r"]}


def _mix(bary, rows):
    """The barycentric mix of the three rows (points or numbers) of a triangle at each of the `bary` (n, 3) weights, written out by hand: not `@`, which the tests ban in npcgen (a threaded matrix
    product corrupted a picture now and then; see anim._apply)."""
    rows = np.asarray(rows, float)
    shape = (-1,) + (1,) * (rows.ndim - 1)
    return sum(bary[:, i].reshape(shape) * rows[i] for i in range(3))


def _colour(text):
    if not isinstance(text, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", text):
        raise ValueError("underwear.colour: expected #rrggbb")
    return np.array([int(text[i:i + 2], 16) for i in (1, 3, 5)], float)


def dress(blob, kinds, colour=None):
    """(the GLB with the pieces on, {kinds, colour, triangles, vertices, offset_mm, texture, layer, bytes_added}). `kinds` are pieces of data/underwear.json; none gives the body back as it was.
    ValueError, saying why, for a body that cannot wear them (already dressed, no skeleton of the expected bones, morph targets) or a piece nobody has heard of."""
    spec = data()
    kinds = list(dict.fromkeys(kinds))
    for kind in kinds:
        if kind not in spec["kinds"]:
            raise ValueError(f"underwear.kinds: {kind!r} is not one of " + ", ".join(spec["kinds"]))
    if not kinds:
        return blob, {"kinds": [], "triangles": 0, "vertices": 0, "bytes_added": 0}
    rgb = _colour(colour or spec["colour"])
    glb = _open(blob)
    doc = glb.json
    if any(m.get("name") == NAME for m in doc.get("meshes", [])):
        raise ValueError("The body already wears its underwear")
    body = _read(glb)
    P, T, J, W, UV, names, heads = (body[k] for k in ("points", "triangles", "joints", "weights", "uv", "names", "heads"))
    groups, normals = _welded(P, T)
    dense = np.zeros((len(P), len(names)))
    for slot in range(4):
        np.add.at(dense, (np.arange(len(P)), J[:, slot]), W[:, slot])

    def share(side, prefixes):
        columns = [i for i, n in enumerate(names) if n.endswith("_" + side) and n.split("_")[0] in prefixes]
        return dense[:, columns].sum(1)

    fields = {f"{kind}_{side}": _smooth(share(side, prefixes), groups, T) for kind, prefixes in (("leg", LEGS), ("arm", ARMS)) for side in "lr"}
    fields["per_metre"] = _per_metre(fields["leg_l"], P, T)
    spine1, spine3 = heads[names.index("spine_01")][1], heads[names.index("spine_03")][1]
    chest = P[(P[:, 1] > spine1 + 0.15) & (P[:, 1] < spine3 + 0.12) & (np.abs(P[:, 0]) < 0.14)]
    heights = {"spine1": spine1, "apex": float(chest[np.argmax(chest[:, 2]), 1]) if len(chest) else spine3}
    ramp, margin = spec["ramp_mm"] / 1000.0, spec["reach_mm"] / 1000.0

    size = int(spec["texture"])
    rgba = np.zeros((size, size, 4), np.uint8)
    painted = np.zeros((size, size), bool)
    reach = np.zeros(len(P), bool)
    for kind in kinds:
        reach |= np.min(np.stack(list(_terms(spec["kinds"][kind], kind, P, fields, heights).values())), axis=0) > -margin
    selected = np.flatnonzero(reach[T].any(1))
    pixels = UV * size
    for t in selected:                                   # every texel of every selected triangle: how much garment shows at the surface point it is
        ids = T[t]
        a, b, c = pixels[ids]
        det = (b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1])
        if abs(det) < 1e-12:
            continue
        x0, x1 = max(int(np.floor(min(a[0], b[0], c[0]))) - 1, 0), min(int(np.ceil(max(a[0], b[0], c[0]))) + 1, size)
        y0, y1 = max(int(np.floor(min(a[1], b[1], c[1]))) - 1, 0), min(int(np.ceil(max(a[1], b[1], c[1]))) + 1, size)
        xs, ys = np.meshgrid(np.arange(x0, x1) + 0.5, np.arange(y0, y1) + 0.5)
        l0 = ((b[1] - c[1]) * (xs - c[0]) + (c[0] - b[0]) * (ys - c[1])) / det
        l1 = ((c[1] - a[1]) * (xs - c[0]) + (a[0] - c[0]) * (ys - c[1])) / det
        inside = (l0 >= -0.02) & (l1 >= -0.02) & (1 - l0 - l1 >= -0.02)
        if not inside.any():
            continue
        bary = np.stack([l0, l1, 1 - l0 - l1], axis=-1)[inside]
        point = _mix(bary, P[ids])
        local = {k: (_mix(bary, v[ids]) if isinstance(v, np.ndarray) else v) for k, v in fields.items()}
        alpha = np.zeros(len(point))
        best = np.full(len(point), -np.inf)
        for kind in kinds:
            inner = np.stack(list(_terms(spec["kinds"][kind], kind, point, local, heights).values())).min(0)
            alpha = np.maximum(alpha, np.clip(inner / ramp + 0.5, 0.0, 1.0))
            best = np.maximum(best, inner)                 # how far inside the piece it is most inside of: negative outside them all
        # The elastic band is the edge and everything beyond it, so that a filtered edge never mixes the light cloth of one texel into the dark rim of the next.
        band = np.clip((spec["edge_mm"] - best * 1000.0) / spec["edge_blend_mm"] + 0.5, 0.0, 1.0)
        shade = 1.0 - (1.0 - spec["edge_shade"]) * band
        out = np.zeros((len(point), 4))
        out[:, :3] = rgb[None, :] * shade[:, None]
        out[:, 3] = alpha * 255
        rows, cols = np.nonzero(inside)
        rgba[rows + y0, cols + x0] = np.clip(np.rint(out), 0, 255).astype(np.uint8)
        painted[rows + y0, cols + x0] = True
    for _ in range(5):                                   # a gutter of the nearest painted texels, so that a UV seam does not show a hair-line between two islands
        grown = painted.copy()
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            shifted = np.roll(np.roll(rgba, dy, 0), dx, 1)
            fill = np.roll(np.roll(painted, dy, 0), dx, 1) & ~grown
            rgba[fill] = shifted[fill]
            grown |= fill
        painted = grown

    used = np.unique(T[selected])
    remap = -np.ones(len(P), np.int64)
    remap[used] = np.arange(len(used))
    faces = remap[T[selected]]
    positions = (P + normals * spec["offset_mm"] / 1000.0)[used]
    png = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(png, "PNG", optimize=True)
    before = len(blob)

    binary = bytearray(glb.bin)

    def view(raw, target=None):
        binary.extend(b"\0" * (-len(binary) % 4))
        entry = {"buffer": 0, "byteOffset": len(binary), "byteLength": len(raw)}
        if target:
            entry["target"] = target
        binary.extend(raw)
        doc["bufferViews"].append(entry)
        return len(doc["bufferViews"]) - 1

    def accessor(values, kind, component, target, bounds=False):
        dtype = {5126: "<f4", 5123: "<u2", 5125: "<u4"}[component]
        values = np.ascontiguousarray(values, dtype=dtype)
        entry = {"bufferView": view(values.tobytes(), target), "componentType": component, "count": len(values), "type": kind}
        if bounds:
            entry.update(min=values.min(0).astype(float).tolist(), max=values.max(0).astype(float).tolist())
        doc["accessors"].append(entry)
        return len(doc["accessors"]) - 1

    sampler = {"magFilter": 9729, "minFilter": 9987, "wrapS": 33071, "wrapT": 33071}
    samplers = doc.setdefault("samplers", [])
    if sampler not in samplers:
        samplers.append(sampler)
    doc.setdefault("images", []).append({"name": NAME, "bufferView": view(png.getvalue()), "mimeType": "image/png"})
    doc.setdefault("textures", []).append({"source": len(doc["images"]) - 1, "sampler": samplers.index(sampler)})
    doc.setdefault("materials", []).append({"name": NAME, "alphaMode": "MASK", "alphaCutoff": 0.5, "doubleSided": False,
                                            "pbrMetallicRoughness": {"baseColorTexture": {"index": len(doc["textures"]) - 1}, "metallicFactor": 0, "roughnessFactor": 0.85}})
    attributes = {"POSITION": accessor(positions, "VEC3", 5126, 34962, bounds=True), "NORMAL": accessor(normals[used], "VEC3", 5126, 34962),
                  "TEXCOORD_0": accessor(UV[used], "VEC2", 5126, 34962), "JOINTS_0": accessor(np.where(W[used] == 0, 0, J[used]), "VEC4", 5123, 34962),
                  "WEIGHTS_0": accessor(W[used], "VEC4", 5126, 34962)}
    indices = accessor(faces.ravel(), "SCALAR", 5123 if len(used) < 65536 else 5125, 34963)
    doc["meshes"].append({"name": NAME, "primitives": [{"attributes": attributes, "indices": indices, "material": len(doc["materials"]) - 1}]})
    doc["scenes"][doc.get("scene", 0)]["nodes"].append(len(doc["nodes"]))
    doc["nodes"].append({"name": NAME, "mesh": len(doc["meshes"]) - 1, "skin": 0})
    binary.extend(b"\0" * (-len(binary) % 4))                                    # the binary chunk of a GLB is a whole number of 4-byte words, whatever the last view was
    glb.bin = bytes(binary)
    doc["buffers"] = [{"byteLength": len(glb.bin)}]
    out = glb.dumps()
    return out, {"kinds": kinds, "colour": (colour or spec["colour"]).lower(), "triangles": int(len(faces)), "vertices": int(len(used)), "offset_mm": spec["offset_mm"],
                 "texture": size, "layer": "underwear", "bytes_added": len(out) - before}
