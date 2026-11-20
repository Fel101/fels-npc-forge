"""Give an untextured model a texture layout, so the turnaround pictures can paint it.

Hunyuan3D v2.1 (npcgen/three_d.py) makes shape only: a surface-net mesh (about 136k faces for a person at octree 256)
with no texture coordinates and no material. This unwraps it with xatlas in two parts laid side by side on one 2:1
texture, the shape a sharper face's head layout has (npcgen/headdetail.head_layout): the body on the left half and the
head (the top LAYOUT_HEAD_FROM of the height) on the right, so the face gets as many texels as the rest of the body and a
sharper face's close-ups paint straight onto it. The texture starts blank; npcgen/paint3d.paint(blank=True) then fills
what no picture sees from the nearest painted surface.

Measured on Clara (Hunyuan, 136k faces): head 6 s, body 40 s, 1.7k and 4.6k charts.
"""
import io
import json
import struct

import numpy as np
from PIL import Image

from .headdetail import ATLAS_PADDING, ATLAS_RESOLUTION, LAYOUT_HEAD_FROM, HeadDetailError, _density
from .paint3d import Glb, _vertex_normals, all_triangles, textured_primitives
from .atlasprocess import AtlasProcessError, generate_layout

SMOOTH_ANGLE = 60          # degrees: a corner whose vertex normal faces away averages only the faces this close to its own
BLANK = 128                 # the texture's starting grey (every texel a picture or the fill reaches is painted over)
ROUGHNESS = 0.85            # skin and cloth; the pictures carry the colour, not the shine


class UnwrapError(HeadDetailError):
    pass


def needs_texture(model_glb):
    """True for a model with no UV-mapped colour texture (Hunyuan's shapes): painting it needs unwrap() first."""
    return not textured_primitives(Glb(model_glb))


def corner_normals(pos, tri, angle=SMOOTH_ANGLE):
    """A normal for every triangle corner (N, 3, 3): the vertex's own smooth normal, except where that faces away from the
    corner's face; there, the area-weighted mean of the faces round the vertex within `angle` of that face. Hunyuan's
    surface nets join three or four faces along about a third of their edges (the thin parts: hair, hems), and plain
    vertex normals there cancel or point backwards (measured on Clara: 5.3k faces with a corner normal facing away,
    which render as black slivers). Averaging every corner over its own nearby faces instead showed the voxel steps as
    flat facets across the nose and cheeks, so it is kept to those corners."""
    face = np.cross(pos[tri[:, 1]] - pos[tri[:, 0]], pos[tri[:, 2]] - pos[tri[:, 0]])
    unit = face / np.maximum(np.linalg.norm(face, axis=1, keepdims=True), 1e-20)
    smooth = _vertex_normals(pos, tri)
    smooth /= np.maximum(np.linalg.norm(smooth, axis=1, keepdims=True), 1e-20)
    out = smooth[tri].reshape(-1, 3)
    vert = tri.ravel()
    owner = np.repeat(np.arange(len(tri)), 3)
    real = np.linalg.norm(face, axis=1)[owner] > 0            # a triangle with no area has no side to face
    bad = np.nonzero((np.einsum("nd,nd->n", out, unit[owner]) < np.cos(np.radians(85))) & real)[0]
    if len(bad):
        order = np.argsort(vert, kind="stable")
        counts = np.bincount(vert, minlength=len(pos))
        starts = np.concatenate([[0], np.cumsum(counts)[:-1]])
        ring = np.full((len(pos), counts.max()), -1)
        ring[vert[order], np.arange(len(vert)) - starts[vert[order]]] = owner[order]
        around, f = ring[vert[bad]], owner[bad]
        near = (around >= 0) & (np.einsum("nkd,nd->nk", unit[around], unit[f]) > np.cos(np.radians(angle)))
        local = np.einsum("nk,nkd->nd", near, face[around])
        length = np.linalg.norm(local, axis=1)
        out[bad] = np.where((length > 0)[:, None], local / np.maximum(length, 1e-20)[:, None], out[bad])
    return out.reshape(-1, 3, 3)


def _unwrap(pos, nrm, tri, cancel=None):
    """xatlas layout of one part: (original vertex per new vertex, triangles, uvs in 0..1 of a square)."""
    import xatlas
    used = np.unique(tri)
    local = np.full(len(pos), -1)
    local[used] = np.arange(len(used))
    try:
        layout = generate_layout(pos[used], local[tri], nrm[used], resolution=ATLAS_RESOLUTION,
                                 padding=ATLAS_PADDING, cancel=cancel)
    except AtlasProcessError as exc:
        raise UnwrapError(str(exc)) from None
    if int(layout["atlas_count"]) != 1:
        raise UnwrapError("The model did not fit one texture")
    vmap, new_tri, uv = layout["vmap"], layout["triangles"], layout["uv"]
    width, height = int(layout["width"]), int(layout["height"])
    uv = uv.astype(np.float64) * np.array([width, height]) / max(width, height)
    return used[vmap.astype(np.int64)], new_tri.astype(np.int64), uv, int(layout["chart_count"])


def _glb(pos, nrm, uv, tri, png):
    """A one-mesh GLB: positions, normals, uvs, 32-bit triangles and a base colour texture."""
    views, accessors = [], []
    blob = bytearray()

    def add(data):
        blob.extend(b"\0" * (-len(blob) % 4))
        views.append({"buffer": 0, "byteOffset": len(blob), "byteLength": len(data)})
        blob.extend(data)
        return len(views) - 1

    for rows, kind in ((pos, "VEC3"), (nrm, "VEC3"), (uv, "VEC2")):
        rows = np.ascontiguousarray(rows, np.float32)
        accessors.append({"bufferView": add(rows.tobytes()), "componentType": 5126, "count": len(rows), "type": kind})
    accessors[0].update(min=np.asarray(pos, np.float32).min(0).tolist(), max=np.asarray(pos, np.float32).max(0).tolist())
    accessors.append({"bufferView": add(np.ascontiguousarray(tri, np.uint32).tobytes()), "componentType": 5125,
                      "count": int(tri.size), "type": "SCALAR"})
    image_view = add(png)
    blob.extend(b"\0" * (-len(blob) % 4))
    doc = {
        "asset": {"version": "2.0", "generator": "NPC Forge"},
        "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0, "name": "model"}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0, "NORMAL": 1, "TEXCOORD_0": 2}, "indices": 3,
                                    "material": 0, "mode": 4}]}],
        "materials": [{"name": "painted", "pbrMetallicRoughness": {"baseColorTexture": {"index": 0},
                                                                   "metallicFactor": 0.0, "roughnessFactor": ROUGHNESS}}],
        "textures": [{"source": 0, "sampler": 0}],
        "samplers": [{"magFilter": 9729, "minFilter": 9987, "wrapS": 33071, "wrapT": 33071}],
        "images": [{"bufferView": image_view, "mimeType": "image/png"}],
        "accessors": accessors, "bufferViews": views, "buffers": [{"byteLength": len(blob)}],
    }
    text = json.dumps(doc, separators=(",", ":")).encode()
    text += b" " * (-len(text) % 4)
    total = 12 + 8 + len(text) + 8 + len(blob)
    return (struct.pack("<III", 0x46546C67, 2, total) + struct.pack("<II", len(text), 0x4E4F534A) + text
            + struct.pack("<II", len(blob), 0x004E4942) + bytes(blob))


def unwrap(model_glb, report=None, *, cancel=None):
    """The model as one mesh with a 2:1 texture layout (body left, head right) and a blank colour texture."""
    try:
        import xatlas  # noqa: F401
    except ImportError:
        raise UnwrapError("Painting an untextured model needs the xatlas package: run  pip install -r requirements.txt") from None
    pos, tri = all_triangles(Glb(model_glb))
    if not len(tri):
        raise UnwrapError("The model has no triangles")
    report = report if report is not None else {}
    v = pos[tri]
    turned = np.einsum("ij,ij->i", v[:, 0], np.cross(v[:, 1], v[:, 2])).sum() < 0
    if turned:                                   # inside out: the painter only paints surface that faces a picture
        tri = tri[:, [0, 2, 1]]
    nrm = _vertex_normals(pos, tri)
    nrm /= np.maximum(np.linalg.norm(nrm, axis=1, keepdims=True), 1e-12)
    corners = corner_normals(pos, tri)          # on the joined mesh: after unwrapping, every chart edge would be a crease

    top, height = pos[:, 1].max(), pos[:, 1].max() - pos[:, 1].min()
    head = pos[tri].mean(1)[:, 1] > top - LAYOUT_HEAD_FROM * height
    out_vert, out_tri, out_uv, out_head, out_corners, charts = [], [], [], [], [], {}
    count = 0
    for name, part, offset in (("body", ~head, 0.0), ("head", head, 0.5)):
        if not part.any():
            continue
        vert, t, uv, charts[name] = _unwrap(pos, nrm, tri[part], cancel=cancel)
        out_vert.append(vert)
        out_tri.append(t + count)
        out_uv.append(uv * [0.5, 1] + [offset, 0])
        out_head.append(np.full(len(t), name == "head"))
        out_corners.append(corners[part])        # xatlas keeps the triangles in the order they were given
        count += len(vert)
    vert, uv = np.concatenate(out_vert), np.concatenate(out_uv)
    new_tri, head_new = np.concatenate(out_tri), np.concatenate(out_head)
    # a vertex whose corners have different normals (a crease, or faces meeting along a shared edge) is split in two
    corners = np.concatenate(out_corners).reshape(-1, 3)
    key = np.concatenate([new_tri.ravel()[:, None], np.round(corners * 1e4)], 1)
    _key, first, inverse = np.unique(key, axis=0, return_index=True, return_inverse=True)
    vert, uv, normals = vert[new_tri.ravel()[first]], uv[new_tri.ravel()[first]], corners[first]
    new_tri = inverse.reshape(-1, 3)
    size = (2 * ATLAS_RESOLUTION, ATLAS_RESOLUTION)
    buf = io.BytesIO()
    Image.new("RGB", size, (BLANK, BLANK, BLANK)).save(buf, "PNG")

    def density(part):
        return round(_density(pos[vert], uv, new_tri[part], size)) if part.any() else None
    report["unwrap"] = {"faces": int(len(tri)), "turned_inside_out": bool(turned), "texture": list(size), "charts": charts,
                        "vertices": int(len(vert)),
                        "head_texels_per_unit": density(head_new), "body_texels_per_unit": density(~head_new)}
    return _glb(pos[vert], normals, uv, new_tri, buf.getvalue())
