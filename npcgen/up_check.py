"""Is a static creature standing the right way up? (Phase 6 follow-up of the creature maker.)

The prep turns a static body by where its mass sits (blender/prep_rules.py raw_up). A crest or raised wings can fool that rule, so the
picture decides instead. The concept picture is drawn from the side, standing, so the game model's side outline should match the
picture's creature better standing than turned over. The picture's creature is BiRefNet's mask of it (made in ComfyUI by
creaturemaker.py); the model's outline comes from its GLB. Both are scaled into the same square. Each is also compared with its horizontal
flip, because the head side is not in question here, only up and down. The verdict says which way matches better by a clear margin, or
that it is not clear.

Pure numpy and Pillow: it runs in the server after the prep, and in the tests without Blender or ComfyUI.
"""
import json
import struct
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

SIZE = 160                  # the side of the square both outlines are compared in
RASTER = 512                # the model's outline is drawn this big, then fitted to SIZE
PAD = 4                     # empty pixels kept round the fitted outline
MARGIN = 0.05               # how much better one way up must match, in overlap (IoU), before the check says so
MATTE_LEVEL = 0.5           # a matte pixel above this (0..1) is the creature
PIECE_SHARE = 0.02          # a separate piece of an outline with fewer pixels than this share of the largest piece is a speck, not the creature
GLB_CHUNK_JSON, GLB_CHUNK_BIN = b"JSON", b"BIN\x00"
COMPONENTS = {5123: np.uint16, 5125: np.uint32, 5126: np.float32}      # glTF component types the reader knows
WIDTHS = {"SCALAR": 1, "VEC2": 2, "VEC3": 3}


def mask_of(matte):
    """The creature in a matte (a picture of it: white is the creature, black is not), as a mask. None when the matte holds no creature."""
    grey = np.asarray(matte.convert("L"), dtype=float) / 255.0
    mask = grey > MATTE_LEVEL
    return mask if mask.any() else None


def triangles_of(glb_path):
    """The positions of a GLB's triangles as an (n, 3, 3) array, in glTF axes (y up, z forward). Reads what the prep exports: meshes at
    the root with no transform. Anything else raises ValueError, so the check says it could not read the model."""
    data = Path(glb_path).read_bytes()
    if len(data) < 20 or data[:4] != b"glTF" or struct.unpack_from("<I", data, 4)[0] != 2:
        raise ValueError("the model is not a GLB file")
    json_length, json_type = struct.unpack_from("<I4s", data, 12)
    if json_type != GLB_CHUNK_JSON:
        raise ValueError("the GLB has no JSON chunk")
    gltf = json.loads(data[20:20 + json_length].decode("utf-8"))
    if not isinstance(gltf, dict):
        raise ValueError("the GLB's JSON is not an object")
    bin_at = 20 + json_length
    bin_length, bin_type = struct.unpack_from("<I4s", data, bin_at)
    if bin_type != GLB_CHUNK_BIN:
        raise ValueError("the GLB has no binary chunk")
    binary = data[bin_at + 8:bin_at + 8 + bin_length]
    nodes = gltf.get("nodes", [])
    if not all(isinstance(node, dict) for node in nodes):
        raise ValueError("the GLB's nodes are not objects")
    if any(set(node) - {"mesh", "name"} for node in nodes):
        raise ValueError("the model has a transform or a hierarchy, which this check does not read")
    parts = []
    for node in nodes:
        mesh = gltf["meshes"][node["mesh"]] if "mesh" in node else None
        if mesh is not None and not isinstance(mesh, dict):
            raise ValueError("the GLB's meshes are not objects")
        for primitive in (mesh or {}).get("primitives", []):
            if not isinstance(primitive, dict) or primitive.get("mode", 4) != 4:
                raise ValueError("the model has parts that are not triangles")
            positions = _accessor(gltf, binary, primitive["attributes"]["POSITION"]).reshape(-1, 3)
            if "indices" in primitive:
                positions = positions[_accessor(gltf, binary, primitive["indices"]).reshape(-1)]
            parts.append(positions.reshape(-1, 3, 3))
    if not parts:
        raise ValueError("the model has no mesh")
    triangles = np.concatenate(parts)
    if triangles.size == 0:
        raise ValueError("the model has no triangles")
    if not np.isfinite(triangles).all():
        raise ValueError("the model has coordinates that are not numbers")
    return triangles


def _accessor(gltf, binary, index):
    """The values of an accessor as floats (positions) or integers (indices), one row per item."""
    accessor = gltf["accessors"][index]
    view = gltf["bufferViews"][accessor["bufferView"]]
    dtype, width = COMPONENTS[accessor["componentType"]], WIDTHS[accessor["type"]]
    if view.get("byteStride", 0) not in (0, width * np.dtype(dtype).itemsize):
        raise ValueError("the model's vertex data is interleaved, which this check does not read")
    start = view.get("byteOffset", 0) + accessor.get("byteOffset", 0)
    values = np.frombuffer(binary, dtype=dtype, count=accessor["count"] * width, offset=start)
    return values.astype(float) if dtype is np.float32 else values.astype(np.int64)


def silhouette(triangles):
    """The side outline of triangles (n, 3, 3) in glTF axes: length (z) across, height (y) up. A mask with the top of the body at the top
    row."""
    side = triangles[:, :, [2, 1]]                           # (length, height) of each corner
    flat = side.reshape(-1, 2)
    low, high = flat.min(axis=0), flat.max(axis=0)
    span = float((high - low).max()) or 1.0
    first = _raster(side, low, span)
    # A triangle far from the body (a floater) would make the body small. The first drawing finds the body's largest pieces, and the outline
    # is drawn again within their box only: spikes stay (they are pieces too), floaters fall outside the box or are dropped as specks.
    ys, xs = np.nonzero(_largest_pieces(first))
    if ys.size == 0:
        return first
    scale = (RASTER - 1) / span
    u0, u1 = low[0] + xs.min() / scale, low[0] + xs.max() / scale
    v0, v1 = low[1] + (RASTER - 1 - ys.max()) / scale, low[1] + (RASTER - 1 - ys.min()) / scale
    box = max(u1 - u0, v1 - v0)
    margin = 0.02 * box
    return _raster(side, np.array([u0 - margin, v0 - margin]), (box + 2 * margin) or 1.0)


def _raster(side, low, span):
    """The corners' (length, height) drawn into a RASTER square whose origin is `low` and whose side is `span`: the top of the body at the top
    row. A triangle that falls off the square is clipped by the drawing."""
    scale = (RASTER - 1) / span
    canvas = Image.new("L", (RASTER, RASTER), 0)
    draw = ImageDraw.Draw(canvas)
    for tri in side:
        draw.polygon([((u - low[0]) * scale, (RASTER - 1) - (v - low[1]) * scale) for u, v in tri], fill=255)
    return np.asarray(canvas) > 0


def _largest_pieces(mask):
    """The mask without its small separate pieces: a piece of fewer pixels than PIECE_SHARE of the largest piece is a speck, not the
    creature. Pieces are joined across neighbouring rows when their runs of pixels overlap (4-connected)."""
    parent, runs, rows = [], [], []
    for y in range(mask.shape[0]):
        padded = np.concatenate(([False], mask[y], [False]))
        changes = np.flatnonzero(padded[1:] != padded[:-1])
        current = []
        for x0, x1 in zip(changes[0::2], changes[1::2]):      # a run of pixels is [x0, x1)
            index = len(parent)
            parent.append(index)
            runs.append((y, int(x0), int(x1), index))
            for p0, p1, previous in rows[-1] if rows else []:
                if p0 < x1 and x0 < p1:
                    _union(parent, previous, index)
            current.append((int(x0), int(x1), index))
        rows.append(current)
    sizes = {}
    for y, x0, x1, index in runs:
        root = _find(parent, index)
        sizes[root] = sizes.get(root, 0) + (x1 - x0)
    if not sizes:
        return mask
    threshold = PIECE_SHARE * max(sizes.values())
    keep = np.zeros_like(mask)
    for y, x0, x1, index in runs:
        if sizes[_find(parent, index)] >= threshold:
            keep[y, x0:x1] = True
    return keep


def _find(parent, index):
    while parent[index] != index:
        parent[index] = parent[parent[index]]
        index = parent[index]
    return index


def _union(parent, a, b):
    root_a, root_b = _find(parent, a), _find(parent, b)
    if root_a != root_b:
        parent[root_b] = root_a


def _fit(mask):
    """The creature's pieces that matter (see _largest_pieces), scaled (keeping its shape) into a SIZE square and centred there."""
    mask = _largest_pieces(mask)
    ys, xs = np.nonzero(mask)
    out = np.zeros((SIZE, SIZE), dtype=bool)
    if ys.size == 0:
        return out
    crop = mask[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    scale = (SIZE - 2 * PAD) / max(crop.shape)
    height, width = max(1, round(crop.shape[0] * scale)), max(1, round(crop.shape[1] * scale))
    small = np.asarray(Image.fromarray((crop * 255).astype(np.uint8)).resize((width, height), Image.BILINEAR)) > 127
    top, left = (SIZE - height) // 2, (SIZE - width) // 2
    out[top:top + height, left:left + width] = small
    return out


def _overlap(a, b):
    union = np.logical_or(a, b).sum()
    return float(np.logical_and(a, b).sum() / union) if union else 0.0


def compare(picture, triangles):
    """The verdict for a picture's creature (a mask from mask_of) and a model's triangles. `standing` and `turned_over` are the best
    overlap of the model the way it stands and turned over, each with or without a horizontal flip."""
    picture = _fit(picture)
    model = _fit(silhouette(triangles))
    standing = max(_overlap(picture, model), _overlap(picture, model[:, ::-1]))
    turned_over = max(_overlap(picture, model[::-1]), _overlap(picture, model[::-1, ::-1]))
    if standing - turned_over >= MARGIN:
        verdict = "confirmed"
    elif turned_over - standing >= MARGIN:
        verdict = "contradicted"
    else:
        verdict = "unclear"
    return {"verdict": verdict, "standing": round(standing, 3), "turned_over": round(turned_over, 3), "margin": MARGIN}


def check(mask, model_path):
    """The check for a picture's creature (a mask, or None when none was found) and a game model file. Never raises: a picture or a model
    that can not be read gives a verdict of None with the reason, so a pack says it was not checked instead of failing."""
    if mask is None:
        return {"verdict": None, "reason": "no creature was found in its concept picture"}
    try:
        return compare(mask, triangles_of(model_path))
    except (OSError, ValueError, KeyError, IndexError, TypeError, AttributeError, struct.error) as error:
        return {"verdict": None, "reason": f"the model could not be read ({error})"}
