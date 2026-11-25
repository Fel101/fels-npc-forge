"""Paint a 3D model's colour texture from the turnaround pictures it was made from.

Pixal3D rebuilds colour through a coarse texture grid, so a face that is sharp in the pictures comes out smudged on the
model. This projects the pictures' own pixels back onto the mesh: every texel takes its colour from the views that
look at it most directly, and keeps the generator's colour where no picture sees it (under the arms, the top of the
head). NumPy and Pillow only; the GLB's mesh, UVs and other maps are left as they are.
"""
import io
import json
import struct
from collections import namedtuple

import numpy as np
from PIL import Image

from . import media

# For each turnaround view: the model axis that runs left-to-right across the picture, and the axis pointing at the
# camera. glTF coordinates: the model stands on +Y, faces +Z, and its own left is +X. "side_left" shows the
# character's own left side, facing the left of the picture (measured: the outline matches 0.87 this way, 0.69 mirrored).
VIEWS = {
    "front": ((1, 0, 0), (0, 0, 1)),
    "side_left": ((0, 0, -1), (1, 0, 0)),
    "back": ((-1, 0, 0), (0, 0, -1)),
    "side_right": ((0, 0, 1), (-1, 0, 0)),
}
# Two more views for things whose top the level pictures cannot see (a helmet's dome, a crate's lid: npcgen/items.py draws them): the camera raised 45
# degrees, in front of the model (front_high) or behind it (back_high). The picture's up is then not the model's +Y, so a view can have its own up axis.
_C45 = 0.7071067811865476
VIEWS["front_high"] = ((1, 0, 0), (0, _C45, _C45))
VIEWS["back_high"] = ((-1, 0, 0), (0, _C45, -_C45))
UPS = {"front_high": (0.0, _C45, -_C45), "back_high": (0.0, _C45, _C45)}
LEVEL_UP = (0.0, 1.0, 0.0)

DEPTH_SCALE = 2          # depth buffers at twice the picture resolution
VISIBLE_EPS = 0.006      # of the model height: how far behind the nearest surface still counts as visible
OCCLUSION_JUMP = 0.03    # of the model height: a nearer surface this close in the picture means an occluding edge
MASK_ERODE = 2           # picture pixels: stay off the outline, where the background bleeds in
FULL_WEIGHT = 0.25       # summed view weight at which the pictures fully replace the generator's colour
TRUST_DROP = 8.0         # a view whose outline matches 0.06 worse than the best view counts half as much
MIN_TRUST = 0.25
HEAD_FROM = 0.16         # of the height, down from the top: the head gets its own fit above this...
NECK_TO = 0.22           # ...blended into the body fit by this depth
HEAD_SCALE_LIMIT = 0.06  # a head fit that changes the size by more than this share is not used
SEEN_FLOOR = 0.01        # view weight for a texel the picture sees only edge-on
SEEN_ALPHA = 0.85        # least share of the pictures' colour on any texel at least one picture sees
CLOSEUP_WEIGHT = 20.0    # a close-up outweighs its own view this much where it sees (it is the same picture, sharper)
CLOSEUP_FADE = 0.08      # of the close-up's width: its weight fades to nothing over this distance from its edges
SEAM_DILATE = 6          # texels: carry the change past UV island edges so filtering never mixes in old colour
HAIR_CLOSEUP = 1008      # pixels: the longer side of the close-up the hair is found in again (SAM 3.1's own input size)
HAIR_MAX_ZOOM = 6.0
HAIR_OPENING = 0.004     # of the close-up's size: loose bits of the model's hair mask narrower than about this go
HAIR_SOFT = 0.002        # of the height: the refused share is smoothed over cells this size (about 3 mm)
HAIR_REFUSED = 0.05      # a texel changes where more than this share of its paint came from behind the model's hair
HAIR_SHARE = 0.5         # a texel is hair where at least this share of the views that see it find hair on the model
HAND_ZONE = 0.03         # metres (on a 1.7 m figure): surface this close to a hand's bones is the hand itself
HAND_REACH = 0.035       # metres: across a picture, how far from the model's hand bones the picture's fingers may be drawn
HAND_NEAR = 0.15         # metres: unseen surface this near a hand takes the painted colour around it, not the generator's
FOREARM_ZONE = 0.045     # metres: surface this close to the elbow-to-wrist line is the forearm itself (it is thicker)
FOREARM_REACH = 0.045    # metres: across a picture, how far from that line the picture's forearm may be drawn
HAND_FACING = -0.3       # within a bone's zone, surface is the hand (or forearm) itself only where it faces away from
                         # the bone (the cosine between its normal and the way out from the bone above this): clothes
                         # the generator pressed against a hand lie within its zone too, but face it
HAND_BONES = [(0, i) for i in (1, 5, 9, 13, 17)] + [(i, i + 1) for k in range(5) for i in range(1 + 4 * k, 4 + 4 * k)]


def up_axis(view):
    """The model axis that runs up a view's picture: +Y, except for the raised views (UPS)."""
    return np.asarray(UPS.get(view, LEVEL_UP), float)


# One picture's placement: the whole-body fit (scale, x offset, y offset), the head's own fit or None, the heights
# between which colour lookups move from the body fit to the head fit, and how much this view is trusted.
View_fit = namedtuple("View_fit", "body head neck trust")


# ---------------------------------------------------------------- GLB

class Glb:
    def __init__(self, data):
        magic, _version, _length = struct.unpack_from("<III", data, 0)
        if magic != 0x46546C67:
            raise ValueError("Not a GLB file")
        offset, self.json, self.bin = 12, None, b""
        while offset < len(data):
            size, kind = struct.unpack_from("<II", data, offset)
            chunk = data[offset + 8:offset + 8 + size]
            if kind == 0x4E4F534A:
                self.json = json.loads(chunk)
            elif kind == 0x004E4942:
                self.bin = bytes(chunk)
            offset += 8 + size

    def view_bytes(self, index):
        view = self.json["bufferViews"][index]
        start = view.get("byteOffset", 0)
        return self.bin[start:start + view["byteLength"]]

    def accessor(self, index):
        acc = self.json["accessors"][index]
        view = self.json["bufferViews"][acc["bufferView"]]
        dtype = {5120: np.int8, 5121: np.uint8, 5122: np.int16, 5123: np.uint16, 5125: np.uint32, 5126: np.float32}[acc["componentType"]]
        width = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}[acc["type"]]
        item = np.dtype(dtype).itemsize * width
        stride = view.get("byteStride") or item
        start = view.get("byteOffset", 0) + acc.get("byteOffset", 0)
        raw = np.frombuffer(self.bin, np.uint8, count=stride * (acc["count"] - 1) + item, offset=start)
        rows = np.lib.stride_tricks.as_strided(raw, (acc["count"], item), (stride, 1))
        return np.ascontiguousarray(rows).view(dtype).reshape(acc["count"], width)

    def image(self, index):
        return Image.open(io.BytesIO(self.view_bytes(self.json["images"][index]["bufferView"])))

    def replace_image(self, index, png):
        """Swap one embedded image's bytes and lay the binary chunk out again."""
        self.replace_views({self.json["images"][index]["bufferView"]: png})
        self.json["images"][index].pop("uri", None)

    def set_triangles(self, primitive, triangles):
        """Replace a primitive's triangle list (32-bit indices)."""
        acc = self.json["accessors"][primitive["indices"]]
        data = np.ascontiguousarray(triangles, np.uint32).ravel()
        acc.update(componentType=5125, count=int(data.size), byteOffset=0)
        acc.pop("min", None)
        acc.pop("max", None)
        self.json["bufferViews"][acc["bufferView"]].pop("byteStride", None)
        self.replace_views({acc["bufferView"]: data.tobytes()})

    def set_accessor(self, index, array):
        """Give an accessor new contents (float32 or uint32 rows) in a buffer view of its own."""
        array = np.ascontiguousarray(array)
        width = 1 if array.ndim == 1 else array.shape[1]
        acc = self.json["accessors"][index]
        acc.update(bufferView=self._add_view(array.tobytes()), byteOffset=0, count=int(len(array)),
                   componentType={np.dtype(np.float32): 5126, np.dtype(np.uint32): 5125}[array.dtype],
                   type={1: "SCALAR", 2: "VEC2", 3: "VEC3", 4: "VEC4"}[width])
        acc.pop("normalized", None)
        acc.pop("sparse", None)
        if "min" in acc or "max" in acc:
            rows = array.reshape(len(array), width)
            acc["min"], acc["max"] = rows.min(0).tolist(), rows.max(0).tolist()
        return index

    def add_accessor(self, array):
        self.json["accessors"].append({})
        return self.set_accessor(len(self.json["accessors"]) - 1, array)

    def _add_view(self, data):
        self.json["bufferViews"].append({"buffer": 0, "byteOffset": 0, "byteLength": 0})
        index = len(self.json["bufferViews"]) - 1
        self._pending = {**getattr(self, "_pending", {}), index: data}
        return index

    def _commit(self):
        """Write out views added by set_accessor, and drop the buffer views nothing uses any more."""
        pending = getattr(self, "_pending", {})
        if not pending:
            return
        views = self.json["bufferViews"]
        used = sorted({a["bufferView"] for a in self.json.get("accessors", []) if "bufferView" in a}
                      | {im["bufferView"] for im in self.json.get("images", []) if "bufferView" in im})
        data = [pending[i] if i in pending else self.view_bytes(i) for i in used]
        new = {old: i for i, old in enumerate(used)}
        self.json["bufferViews"] = [views[i] for i in used]
        for item in self.json.get("accessors", []) + self.json.get("images", []):
            if "bufferView" in item:
                item["bufferView"] = new[item["bufferView"]]
        self._pending = {}
        self.replace_views(dict(enumerate(data)))

    def replace_views(self, replacements):
        """Swap the bytes of some buffer views ({view index: bytes}) and lay the binary chunk out again."""
        parts = []
        for i, view in enumerate(self.json["bufferViews"]):
            parts.append(replacements[i] if i in replacements else self.view_bytes(i))
        blob = bytearray()
        for view, part in zip(self.json["bufferViews"], parts):
            blob += b"\0" * (-len(blob) % 4)
            view["byteOffset"], view["byteLength"] = len(blob), len(part)
            blob += part
        blob += b"\0" * (-len(blob) % 4)
        self.bin = bytes(blob)
        self.json["buffers"] = [{"byteLength": len(self.bin)}]

    def dumps(self):
        self._commit()
        text = json.dumps(self.json, separators=(",", ":")).encode()
        text += b" " * (-len(text) % 4)
        total = 12 + 8 + len(text) + 8 + len(self.bin)
        return (struct.pack("<III", 0x46546C67, 2, total) + struct.pack("<II", len(text), 0x4E4F534A) + text
                + struct.pack("<II", len(self.bin), 0x004E4942) + self.bin)


def dot3(rows, v):
    """rows (N, 3) dotted with one 3-vector, written out by hand. Not `rows @ v`: see anim._apply (numpy's threaded BLAS gave
    a different answer for the same large product now and then on the machine this was built on)."""
    return rows[:, 0] * v[0] + rows[:, 1] * v[1] + rows[:, 2] * v[2]


def _transform(rows, m):
    """rows (N, 3) times the 3x3 matrix m (as column vectors: m @ row), by hand for the same reason."""
    return np.stack([dot3(rows, m[i]) for i in range(3)], 1)


def _node_matrix(node):
    if "matrix" in node:
        return np.array(node["matrix"], float).reshape(4, 4).T
    t = node.get("translation", (0, 0, 0))
    x, y, z, w = node.get("rotation", (0, 0, 0, 1))
    s = node.get("scale", (1, 1, 1))
    r = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                  [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                  [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])
    m = np.eye(4)
    m[:3, :3] = r * np.array(s, float)
    m[:3, 3] = t
    return m


def _scene_primitives(glb):
    """(primitive, world matrix) for every triangle primitive the scene shows."""
    out = []
    nodes = glb.json.get("nodes", [])
    scene = glb.json.get("scenes", [{}])[glb.json.get("scene", 0)] if glb.json.get("scenes") else {"nodes": range(len(nodes))}

    def walk(index, parent):
        node = nodes[index]
        world = np.einsum("ij,jk->ik", parent, _node_matrix(node))
        if "mesh" in node:
            out.extend((prim, world) for prim in glb.json["meshes"][node["mesh"]]["primitives"] if prim.get("mode", 4) == 4)
        for child in node.get("children", ()):
            walk(child, world)

    for index in scene["nodes"]:
        walk(index, np.eye(4))
    return out


def _primitive_mesh(glb, prim, world):
    """World-space positions, unit normals and triangles of one primitive."""
    attrs = prim["attributes"]
    pos = glb.accessor(attrs["POSITION"]).astype(np.float64)
    pos = _transform(pos, world[:3, :3]) + world[:3, 3]
    tri = (glb.accessor(prim["indices"]).reshape(-1, 3) if "indices" in prim
           else np.arange(len(pos)).reshape(-1, 3)).astype(np.int64)
    if "NORMAL" in attrs:
        nrm = _transform(glb.accessor(attrs["NORMAL"]).astype(np.float64), np.linalg.inv(world[:3, :3]).T)
    else:
        nrm = _vertex_normals(pos, tri)
    nrm /= np.maximum(np.linalg.norm(nrm, axis=1, keepdims=True), 1e-12)
    return pos, nrm, tri


def textured_primitives(glb, with_primitive=False):
    """(positions, normals, uvs, triangles, base colour image index) for each primitive, in world space; with_primitive
    adds the primitive's own glTF entry at the end."""
    out = []
    for prim, world in _scene_primitives(glb):
        attrs = prim["attributes"]
        material = glb.json.get("materials", [{}])[prim["material"]] if "material" in prim else {}
        tex = material.get("pbrMetallicRoughness", {}).get("baseColorTexture")
        if tex is None or "TEXCOORD_%d" % tex.get("texCoord", 0) not in attrs:
            continue
        pos, nrm, tri = _primitive_mesh(glb, prim, world)
        uv = glb.accessor(attrs["TEXCOORD_%d" % tex.get("texCoord", 0)]).astype(np.float64)
        out.append((pos, nrm, uv, tri, glb.json["textures"][tex["index"]]["source"]) + ((prim,) if with_primitive else ()))
    return out


def all_triangles(glb):
    """World-space positions and triangles of every triangle primitive, textured or not, as one mesh."""
    parts = [_primitive_mesh(glb, prim, world) for prim, world in _scene_primitives(glb)]
    if not parts:
        return np.zeros((0, 3)), np.zeros((0, 3), np.int64)
    starts = np.cumsum([0] + [len(p[0]) for p in parts[:-1]])
    return np.concatenate([p[0] for p in parts]), np.concatenate([p[2] + s for p, s in zip(parts, starts)])


def _vertex_normals(pos, tri):
    face = np.cross(pos[tri[:, 1]] - pos[tri[:, 0]], pos[tri[:, 2]] - pos[tri[:, 0]])
    nrm = np.zeros_like(pos)
    for k in range(3):
        np.add.at(nrm, tri[:, k], face)
    return nrm


# ---------------------------------------------------------------- rasterising

def rasterize(xy, tri, width, height, chunk=4_000_000):
    """Every pixel centre inside every triangle: (pixel index, triangle index, barycentric weights).

    xy are vertex positions in pixels (x right, y down). Coverage only; depth is left to the caller.
    """
    a, b, c = xy[tri[:, 0]], xy[tri[:, 1]], xy[tri[:, 2]]
    lo = np.floor(np.minimum(np.minimum(a, b), c) - 0.5).astype(np.int64) + 1
    hi = np.floor(np.maximum(np.maximum(a, b), c) - 0.5).astype(np.int64)
    lo = np.maximum(lo, 0)
    hi[:, 0] = np.minimum(hi[:, 0], width - 1)
    hi[:, 1] = np.minimum(hi[:, 1], height - 1)
    span = np.maximum(hi - lo + 1, 0)
    count = span[:, 0] * span[:, 1]
    area = (b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (b[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0])
    keep = np.nonzero((count > 0) & (np.abs(area) > 1e-12))[0]
    pixels, tris, barys = [], [], []
    start = 0
    ends = np.cumsum(count[keep])
    while start < len(keep):
        stop = int(np.searchsorted(ends, (ends[start - 1] if start else 0) + chunk, side="right"))
        stop = max(stop, start + 1)
        ids = keep[start:stop]
        n = count[ids]
        t = np.repeat(ids, n)
        first = np.repeat(np.cumsum(n) - n, n)
        local = np.arange(len(t)) - first
        w = span[t, 0]
        px = lo[t, 0] + local % w
        py = lo[t, 1] + local // w
        cx, cy = px + 0.5, py + 0.5
        ax, ay, bx, by, qx, qy = a[t, 0], a[t, 1], b[t, 0], b[t, 1], c[t, 0], c[t, 1]
        ar = area[t]
        w0 = ((bx - cx) * (qy - cy) - (by - cy) * (qx - cx)) / ar
        w1 = ((qx - cx) * (ay - cy) - (qy - cy) * (ax - cx)) / ar
        w2 = 1.0 - w0 - w1
        inside = (w0 >= -1e-9) & (w1 >= -1e-9) & (w2 >= -1e-9)
        pixels.append((py * width + px)[inside])
        tris.append(t[inside])
        barys.append(np.stack([w0, w1, w2], 1)[inside])
        start = stop
    if not pixels:
        return np.zeros(0, np.int64), np.zeros(0, np.int64), np.zeros((0, 3))
    return np.concatenate(pixels), np.concatenate(tris), np.concatenate(barys)


def _shift_reduce(img, radius, fn):
    """Square min/max filter by shifting (NumPy only)."""
    out = img.copy()
    for axis in (0, 1):
        src = out.copy()
        for d in range(1, radius + 1):
            for sgn in (1, -1):
                shifted = np.roll(src, sgn * d, axis=axis)
                edge = [slice(None)] * 2
                edge[axis] = slice(0, d) if sgn > 0 else slice(-d, None)
                shifted[tuple(edge)] = src[tuple(edge)]
                out = fn(out, shifted)
    return out


def _bilinear(img, x, y):
    h, w = img.shape[:2]
    x = np.clip(x - 0.5, 0, w - 1.001)
    y = np.clip(y - 0.5, 0, h - 1.001)
    x0, y0 = x.astype(np.int64), y.astype(np.int64)
    fx, fy = (x - x0)[:, None], (y - y0)[:, None]
    top = img[y0, x0] * (1 - fx) + img[y0, x0 + 1] * fx
    bottom = img[y0 + 1, x0] * (1 - fx) + img[y0 + 1, x0 + 1] * fx
    return top * (1 - fy) + bottom * fy


# ---------------------------------------------------------------- lining the pictures up with the model

def foreground_mask(rgb):
    """The character in a turnaround picture: whatever differs clearly from the plain border colour."""
    border = np.concatenate([rgb[:4].reshape(-1, 3), rgb[-4:].reshape(-1, 3), rgb[:, :4].reshape(-1, 3), rgb[:, -4:].reshape(-1, 3)])
    bg = np.median(border, 0)
    diff = np.abs(rgb.astype(np.float64) - bg).max(-1)
    mask = diff > 28
    # drop specks: keep rows/columns of the main blob by a 3x3 opening
    mask = _shift_reduce(_shift_reduce(mask.astype(np.uint8), 1, np.minimum), 1, np.maximum).astype(bool)
    return mask


def _mesh_silhouette(pos, tri, ax_x, fit, width, height, ax_y=LEVEL_UP):
    s, tx, ty = fit
    xy = np.stack([dot3(pos, ax_x) * s + tx, ty - dot3(pos, ax_y) * s], 1)
    pix, _t, _b = rasterize(xy, tri, width, height)
    sil = np.zeros(width * height, bool)
    sil[pix] = True
    return sil.reshape(height, width)


def fit_view(pos, tri, ax_x, mask, start=None, rows=None, ax_y=LEVEL_UP):
    """Scale and offset that put the model's outline on the picture's: from the bounding boxes, then searched.

    With `start`, only nudges that fit, scoring the picture rows above `rows` and the triangles given: this lines
    the head up on its own (in the measured characters it sat up to 9 picture pixels off the whole-body fit).
    """
    height, width = mask.shape
    used = pos[np.unique(tri)]
    u = dot3(used, ax_x)
    up = dot3(used, ax_y)
    y_lo, y_hi = up.min(), up.max()
    if start is None:
        ys, xs = np.nonzero(mask)
        top, bottom = ys.min(), ys.max() + 1
        s = (bottom - top) / (y_hi - y_lo)
        ty = top + s * y_hi
        tx = xs.mean() - s * u.mean()
        # the mean over vertices is not the mean over the silhouette: centre the rendered outline on the picture's
        sil = _mesh_silhouette(pos, tri, ax_x, (s, tx, ty), width, height, ax_y)
        tx += xs.mean() - np.nonzero(sil)[1].mean()
        steps = ((0.01, 3.0), (0.004, 1.0), (0.0015, 0.4))
    else:
        s, tx, ty = start
        steps = ((0.004, 1.0), (0.0015, 0.4))
    rows = height if rows is None else max(1, min(height, int(rows)))
    target = mask[:rows]

    # Render the outline once, at twice the size, and move that picture around for each candidate fit.
    s0, tx0, ty0 = s, tx, ty
    base = Image.fromarray(_mesh_silhouette(pos, tri, ax_x, (2 * s0, 2 * tx0, 2 * ty0), 2 * width, 2 * height, ax_y)
                           .astype(np.uint8) * 255)

    def score(fit):
        fs, ftx, fty = fit
        r = s0 / fs
        warped = base.transform((width, height), Image.AFFINE,
                                (2 * r, 0, 2 * (tx0 - r * ftx), 0, 2 * r, 2 * (ty0 - r * fty)),
                                Image.BILINEAR)
        sil = np.asarray(warped)[:rows] >= 128
        return (sil & target).sum() / max((sil | target).sum(), 1)

    mid_u, mid_y = (u.min() + u.max()) / 2, (y_lo + y_hi) / 2
    best = (s, tx, ty)
    best_score = score(best)
    for step_s, step_px in steps:
        improved = True
        while improved:
            improved = False
            bs, bx, by = best
            for cand in ((bs * (1 + step_s), bx, by), (bs * (1 - step_s), bx, by), (bs, bx + step_px, by),
                         (bs, bx - step_px, by), (bs, bx, by + step_px), (bs, bx, by - step_px)):
                # scale about the model's middle so a scale change does not also slide the outline
                if cand[0] != bs:
                    cand = (cand[0], bx + (bs - cand[0]) * mid_u, by - (bs - cand[0]) * mid_y)
                sc = score(cand)
                if sc > best_score + 1e-5:
                    best, best_score, improved = cand, sc, True
    return best, best_score


def view_fit(pos, tri, view, rgb):
    """(View_fit, outline match, head match) placing the model on one turnaround picture (RGB array): the whole body,
    then the head on its own (the top HEAD_FROM of the height, blended into the body fit down the neck)."""
    height = float(pos[:, 1].max() - pos[:, 1].min())
    top = float(pos[:, 1].max())
    head_tri = tri[(pos[tri, 1] > top - (HEAD_FROM + 0.04) * height).all(1)]
    neck = (top - NECK_TO * height, top - HEAD_FROM * height)
    mask = foreground_mask(rgb)
    body, iou = fit_view(pos, tri, VIEWS[view][0], mask, ax_y=up_axis(view))
    if view in UPS:
        return View_fit(body, None, neck, 1.0), iou, 0.0
    chin_row = body[2] - neck[1] * body[0]
    head, head_iou = (fit_view(pos, head_tri, VIEWS[view][0], mask, start=body, rows=chin_row)
                      if len(head_tri) else (None, 0.0))
    if head is not None and abs(head[0] / body[0] - 1) > HEAD_SCALE_LIMIT:
        head = None
    return View_fit(body, head, neck, 1.0), iou, head_iou


# ---------------------------------------------------------------- painting

def texel_samples(pos, nrm, uv, tri, size, with_triangles=False):
    """World position and normal at the centre of every texel the UV layout covers (and with_triangles: the triangle
    each texel lies in)."""
    width, height = size
    xy = np.stack([uv[:, 0] * width, uv[:, 1] * height], 1)
    pix, t, bary = rasterize(xy, tri, width, height)
    # Sliver triangles can hold no texel centre at all, and would show whatever the atlas has next to them: give the
    # texels under each triangle's centre and corners that triangle's surface point too (after the proper samples).
    every = np.arange(len(tri))
    for corner in (None, 0, 1, 2):
        b = np.full((len(tri), 3), 1 / 3) if corner is None else np.eye(3)[[corner] * len(tri)] * 0.8 + 0.2 / 3
        at = np.einsum("nk,nkd->nd", b, xy[tri])
        x = np.clip(at[:, 0].astype(np.int64), 0, width - 1)
        y = np.clip(at[:, 1].astype(np.int64), 0, height - 1)
        pix, t, bary = np.concatenate([pix, y * width + x]), np.concatenate([t, every]), np.concatenate([bary, b])
    # a texel on a shared edge is found twice, and most are also found above; keep the first
    pix, first = np.unique(pix, return_index=True)
    t, bary = t[first], bary[first]
    corners = tri[t]
    p = np.einsum("nk,nkd->nd", bary, pos[corners])
    n = np.einsum("nk,nkd->nd", bary, nrm[corners])
    n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)
    return (pix, p, n, t) if with_triangles else (pix, p, n)


def uv_islands(tri, count):
    """A label per triangle: triangles sharing a vertex share a label. glTF gives a vertex its own copy on each side of
    a UV seam, so a label is a UV island: one piece of surface, laid out in one piece of the texture."""
    label = np.arange(count)
    while True:
        low = label[tri].min(1)
        before = label.copy()
        for k in range(3):
            np.minimum.at(label, tri[:, k], low)
        label = label[label]                          # shortcut chains of labels
        if np.array_equal(label, before):
            return label[tri[:, 0]]


def _island_fill(image, fill, island, known, rounds=120, smooth=12):
    """Colours for the texels `fill` of an image (th x tw x 3, float), run on from the `known` texels of the same UV
    island (`island`: a label per texel, -1 where none): peeled in from the edge of the gap a texel at a time, then
    smoothed. Texture-space neighbours in one island are surface neighbours, so the gap continues the surface it is in
    (behind Imani's hand the thigh's colour), not the nearest surface in space (her forearm, a few centimetres away).
    Texels whose island has nothing known near them keep their colour. Only the gap's own texels are worked on (the
    gaps lie all over the texture, and working on the whole box around them took 45 s on Natalie; this takes a second
    or two)."""
    th, tw = fill.shape
    gap = np.flatnonzero(fill)
    if not len(gap):
        return image
    V = image.reshape(th * tw, -1).astype(np.float32)
    L = island.ravel()
    have = known.ravel() & ~fill.ravel() & (L >= 0)
    y, x = np.divmod(gap, tw)
    steps = []                                       # each gap texel's 4 neighbours, and whether each is in its island
    for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        ny, nx = y + dy, x + dx
        inside = (ny >= 0) & (ny < th) & (nx >= 0) & (nx < tw)
        n = np.where(inside, ny * tw + nx, gap)
        steps.append((n, inside & (L[n] == L[gap])))

    def neighbours(sel):
        """The summed colour and the count of the `have` neighbours in the same island of the gap texels `sel`."""
        acc, cnt = np.zeros((len(sel), V.shape[1]), np.float32), np.zeros(len(sel), np.float32)
        for n, same in steps:
            ok = same[sel] & have[n[sel]]
            acc += V[n[sel]] * ok[:, None]
            cnt += ok
        return acc, cnt
    todo = np.ones(len(gap), bool)
    for _ in range(rounds):
        sel = np.flatnonzero(todo)
        if not len(sel):
            break
        acc, cnt = neighbours(sel)
        ready = cnt > 0
        if not ready.any():
            break
        V[gap[sel[ready]]] = acc[ready] / cnt[ready][:, None]
        have[gap[sel[ready]]] = True
        todo[sel[ready]] = False
    done = np.flatnonzero(~todo)
    for _ in range(smooth):                          # soften the peeling's streaks, the gap's edge held still
        acc, cnt = neighbours(done)
        V[gap[done]] = np.where(cnt[:, None] > 0, acc / np.maximum(cnt, 1)[:, None], V[gap[done]])
    out = image.copy()
    out.reshape(th * tw, -1)[gap[done]] = V[gap[done]]
    return out


def view_xy(points, view, fit):
    """Where points fall in a picture: the whole-body fit, with the head's own fit blended in down the neck."""
    ax_x = np.asarray(VIEWS[view][0], float)
    s, tx, ty = fit.body
    px = dot3(points, ax_x) * s + tx
    py = ty - dot3(points, up_axis(view)) * s
    if fit.head is not None:
        hs, htx, hty = fit.head
        blend = np.clip((points[:, 1] - fit.neck[0]) / (fit.neck[1] - fit.neck[0]), 0, 1)
        px = px * (1 - blend) + (dot3(points, ax_x) * hs + htx) * blend
        py = py * (1 - blend) + (hty - points[:, 1] * hs) * blend
    return px, py


def clay_view(pos, tri, view, fit, size, box=(0, 0), zoom=1.0):
    """RGB array: the bare shape, shaded grey on white, drawn where `view_xy` puts it in a picture, so a mask found on it
    lines up with the picture pixel for pixel: `size` (w, h) pixels from the picture's point `box` (x0, y0) on, `zoom`
    times the picture's scale. (Hunyuan's shapes have no colour of their own, and measured on Imani's Pixal3D model, a
    segmenter found the same hair on the grey shape as on the coloured one.)"""
    w, h = size
    toward = np.asarray(VIEWS[view][1], float)
    px, py = view_xy(pos, view, fit)
    pix, t, bary = rasterize(np.stack([(px - box[0]) * zoom, (py - box[1]) * zoom], 1), tri, w, h)
    depth = np.einsum("nk,nk->n", bary, dot3(pos, toward)[tri[t]])
    order = np.lexsort((-depth, pix))           # the nearest surface first at each pixel
    pix, first = np.unique(pix[order], return_index=True)
    t = t[order][first]
    face = np.cross(pos[tri[t, 1]] - pos[tri[t, 0]], pos[tri[t, 2]] - pos[tri[t, 0]])
    face /= np.maximum(np.linalg.norm(face, axis=1, keepdims=True), 1e-12)
    light = toward * 0.8 + np.array([0.3, 0.5, 0.0])
    shade = 0.35 + 0.65 * np.abs(dot3(face, light / np.linalg.norm(light)))
    image = np.full((h * w, 3), 255.0)
    image[pix] = 200.0 * shade[:, None]
    return np.clip(image, 0, 255).astype(np.uint8).reshape(h, w, 3)


# A mask over part of a picture: `array` covers the picture from (x0, y0) on, at `zoom` times the picture's scale.
Mask = namedtuple("Mask", "array x0 y0 zoom")


def _png(rgb):
    buf = io.BytesIO()
    Image.fromarray(rgb).save(buf, "PNG")
    return buf.getvalue()


def _found(find, png, name, size, prompt="hair"):
    mask = np.asarray(find(png, name, prompt), bool)
    if mask.shape != (size[1], size[0]):
        mask = np.asarray(Image.fromarray(mask.astype(np.uint8) * 255).resize(size, Image.NEAREST)) > 127
    return mask


def _hair_masks(find, views, pictures, pos, tri, report):
    """{view: (the picture's hair, the model's hair)} as Masks, from `find` (see paint).

    The pictures show the whole figure, so a head is only about a hundred pixels wide in them: the hair is first found
    in the whole picture and the whole model, and then again in a close-up of the part holding either one's hair,
    upscaled to HAIR_CLOSEUP pixels (the model drawn at that scale), so the masks follow single strands. The model's ears
    and eyes (asked for on the close-up) are taken out of its hair."""
    out = {}
    report["hair"] = {"views": {}}
    for view, (rgb, fit, _iou) in pictures.items():
        h, w = rgb.shape[:2]
        picture = _found(find, views[view], f"{view}_picture", (w, h))
        model = _found(find, _png(clay_view(pos, tri, view, fit, (w, h))), f"{view}_model", (w, h))
        either = picture | model
        if either.any():
            ys, xs = np.nonzero(either)
            pad = 0.1 * max(xs.max() - xs.min(), ys.max() - ys.min()) + 4
            x0, y0 = max(0, int(xs.min() - pad)), max(0, int(ys.min() - pad))
            x1, y1 = min(w, int(xs.max() + pad) + 1), min(h, int(ys.max() + pad) + 1)
            zoom = min(HAIR_CLOSEUP / max(x1 - x0, y1 - y0), HAIR_MAX_ZOOM)
            size = (max(1, round((x1 - x0) * zoom)), max(1, round((y1 - y0) * zoom)))
            close = Image.fromarray(rgb[y0:y1, x0:x1]).resize(size, Image.LANCZOS)
            clay = _png(clay_view(pos, tri, view, fit, size, (x0, y0), zoom))
            # an ear or an eye on the model is never hair: Pixal3D paints hair colour over sculpted ears, and where a
            # Hunyuan model's hair runs into its face, the segmenter's hair reached over an eye
            model_close = (_found(find, clay, f"{view}_model_close", size)
                           & ~_found(find, clay, f"{view}_model_close_ear", size, "ear")
                           & ~_found(find, clay, f"{view}_model_close_eye", size, "eye"))
            # the loose bits at the edge of the model's hair (a grey shape gives the segmenter less to go on than a
            # picture) are dropped: an opening a few close-up pixels wide
            r = max(1, round(HAIR_OPENING * max(size)))
            model_close = _shift_reduce(_shift_reduce(model_close.astype(np.uint8), r, np.minimum), r, np.maximum).astype(bool)
            masks = (Mask(_found(find, _png(np.asarray(close)), f"{view}_picture_close", size), x0, y0, zoom),
                     Mask(model_close, x0, y0, zoom))
        else:
            masks = (Mask(picture, 0, 0, 1.0), Mask(model, 0, 0, 1.0))
        out[view] = masks
        report["hair"]["views"][view] = {"picture_hair": int(picture.sum()), "model_hair": int(model.sum()),
                                         "model_hair_not_in_picture": int((model & ~picture).sum()),
                                         "closeup_zoom": round(float(masks[0].zoom), 2)}
    return out


def _mask_at(mask, px, py):
    """The Mask's value where points fall in the picture (px, py); False outside it."""
    x, y = (px - mask.x0) * mask.zoom, (py - mask.y0) * mask.zoom
    h, w = mask.array.shape
    inside = (x >= 0) & (y >= 0) & (x < w) & (y < h)
    return inside & mask.array[np.clip(y.astype(np.int64), 0, h - 1), np.clip(x.astype(np.int64), 0, w - 1)]


def project(points, normals, pos, tri, view, rgb, fit, height_m, zoom=1):
    """Colour and weight of each point as seen in one view; weight 0 where the view cannot see it.

    `fit` is a View_fit: the whole-body fit decides what the camera can see; the colour is looked up with the head's
    own fit above the neck, blended into the body fit down the neck. `zoom` > 1 is a close-up: a part of the view
    upscaled that many times (npcgen/headdetail.py), whose weight also fades out towards its edges.
    """
    ax_x, toward, ax_y = np.asarray(VIEWS[view][0], float), np.asarray(VIEWS[view][1], float), up_axis(view)
    h, w = rgb.shape[:2]
    s, tx, ty = fit.body
    # depth buffer at a finer scale than the view: larger "toward" = nearer the camera
    k = max(1, round(DEPTH_SCALE / zoom))
    xy = np.stack([(dot3(pos, ax_x) * s + tx) * k, (ty - dot3(pos, ax_y) * s) * k], 1)
    pix, t, bary = rasterize(xy, tri, w * k, h * k)
    depth = np.einsum("nk,nk->n", bary, dot3(pos, toward)[tri[t]])
    zbuf = np.full(w * k * h * k, -np.inf)
    np.maximum.at(zbuf, pix, depth)
    zbuf = zbuf.reshape(h * k, w * k)
    near = _shift_reduce(zbuf, int(round(2 * k * zoom)), np.maximum)    # 2 view pixels

    px = dot3(points, ax_x) * s + tx
    py = ty - dot3(points, ax_y) * s
    ix = np.clip((px * k).astype(np.int64), 0, w * k - 1)
    iy = np.clip((py * k).astype(np.int64), 0, h * k - 1)
    d = dot3(points, toward)
    visible = (d >= zbuf[iy, ix] - VISIBLE_EPS * height_m) & (d >= near[iy, ix] - OCCLUSION_JUMP * height_m)

    px, py = view_xy(points, view, fit)
    mask = foreground_mask(rgb)
    inner = _shift_reduce(mask.astype(np.uint8), int(round(MASK_ERODE * zoom)), np.minimum).astype(bool)
    in_pic = inner[np.clip(py.astype(np.int64), 0, h - 1), np.clip(px.astype(np.int64), 0, w - 1)]

    facing = dot3(normals, toward)
    # A small floor keeps the picture's colour on surfaces it sees only edge-on (the sides of ridges and folds the
    # generator sculpted), instead of the generator's own colour; a view facing them properly outweighs it.
    weight = np.maximum(np.clip((facing - 0.05) / 0.95, 0, 1) ** 1.5, SEEN_FLOOR) * visible * in_pic
    weight *= fit.trust
    if zoom > 1:
        edge = np.minimum(np.minimum(px, w - px), np.minimum(py, h - py))
        weight *= np.clip(edge / (CLOSEUP_FADE * w), 0, 1)
    colour = _bilinear(rgb.astype(np.float32), px, py)

    return colour, weight


def _hand_bones(hands):
    """The bones of each hand whose two ends are known, as (a, b, reach, zone) with reach and zone in metres (HAND_REACH
    and HAND_ZONE; a 22nd point, the elbow, adds the forearm with FOREARM_REACH and FOREARM_ZONE), each finger carried
    on 40% past its tip (the pictures draw longer fingers than the generator's mitten has), and each hand's middle."""
    bones, middles = [], []
    for pts in hands.values():
        pts = np.asarray(pts, float)
        known = np.isfinite(pts).all(1)
        if known[:21].any():
            middles.append(pts[:21][known[:21]].mean(0))
        bones += [(pts[i], pts[j], HAND_REACH, HAND_ZONE) for i, j in HAND_BONES if known[i] and known[j]]
        bones += [(pts[t], pts[t] + (pts[t] - pts[t - 1]) * 0.4, HAND_REACH, HAND_ZONE)
                  for t in (4, 8, 12, 16, 20) if known[t] and known[t - 1]]
        if len(pts) > 21 and known[0] and known[21]:
            bones.append((pts[21], pts[0], FOREARM_REACH, FOREARM_ZONE))
    return bones, middles


def _in_box(coords, order, keyed, lo, hi):
    """Indices of the rows of `coords` (N x k) within the box lo..hi on every axis. order sorts the rows by their first
    column and keyed is that column sorted, so a box costs a binary search and its own rows, not a pass over all N."""
    idx = order[np.searchsorted(keyed, lo[0], "left"):np.searchsorted(keyed, hi[0], "right")]
    c = coords[idx]
    return idx[np.all((c >= lo) & (c <= hi), axis=1)]


def _near_bones(points, bones, scale, reach=None, normals=None):
    """Which points lie within `reach` metres (default: each bone's own zone) of any of the bones. scale: model units per
    metre. With `normals` (one per point), only points whose surface faces away from that bone count (HAND_FACING): the
    hand's own surface, not the clothes against it. Each bone looks only at the points in a box around it (a point
    outside it is further than that)."""
    out = np.zeros(len(points), bool)
    if not len(points) or not bones:
        return out
    order = np.argsort(points[:, 0], kind="stable")
    keyed = points[order, 0]
    for a, b, _across, zone in bones:
        r = (zone if reach is None else reach) * scale
        pad = r * 1.001 + 1e-9
        idx = _in_box(points, order, keyed, np.minimum(a, b) - pad, np.maximum(a, b) + pad)
        if not len(idx):
            continue
        p = points[idx]
        ab = b - a
        u = np.clip(dot3(p - a, ab) / max(float(np.dot(ab, ab)), 1e-12), 0, 1)
        away = p - a - np.outer(u, ab)
        d = np.linalg.norm(away, axis=1)
        hit = d < r
        if normals is not None:
            hit &= (normals[idx] * away).sum(1) > HAND_FACING * d
        out[idx] |= hit
    return out


def _behind_hands(points, bones, middles, toward, scale, normals=None):
    """Which points a picture shows a hand (or forearm) in front of: within a bone's reach across the picture of it and
    at least 1 cm nearer the camera, and not part of the hand or arm itself (outside every bone's zone, or with
    `normals`, not facing away from the bone within it: see _near_bones). scale: model units per metre."""
    out = np.zeros(len(points), bool)
    if not bones:
        return out
    near = np.flatnonzero(np.min([np.linalg.norm(points - m, axis=1) for m in middles], 0) < 0.5 * scale)
    if not len(near):
        return out
    p = points[near]
    t = np.asarray(toward, float)
    # each bone looks only at the points in a box around it across the picture: `across` is measured across the picture
    # and `own` in space, which is never less, so a point outside the box is neither (50 s on Natalie without it)
    e1 = np.cross(t, [0.0, 1.0, 0.0] if abs(t[1]) < 0.9 else [1.0, 0.0, 0.0])
    e1 /= np.linalg.norm(e1)
    e2 = np.cross(t / np.linalg.norm(t), e1)
    q = np.stack([dot3(p, e1), dot3(p, e2)], 1)
    order = np.argsort(q[:, 0], kind="stable")
    keyed = q[order, 0]
    own = np.zeros(len(p), bool)
    shown = np.zeros(len(p), bool)
    for a, b, reach, zone in bones:
        pad = max(reach, zone) * scale * 1.001 + 1e-9
        qa, qb = np.array([np.dot(a, e1), np.dot(a, e2)]), np.array([np.dot(b, e1), np.dot(b, e2)])
        idx = _in_box(q, order, keyed, np.minimum(qa, qb) - pad, np.maximum(qa, qb) + pad)
        if not len(idx):
            continue
        ab = b - a
        pa = p[idx] - a
        u = np.clip(dot3(pa, ab) / max(float(np.dot(ab, ab)), 1e-12), 0, 1)
        away = pa - np.outer(u, ab)
        d = np.linalg.norm(away, axis=1)
        mine = d < zone * scale
        if normals is not None:
            mine &= (normals[near[idx]] * away).sum(1) > HAND_FACING * d
        own[idx] |= mine
        pa2, ab2 = pa - np.outer(dot3(pa, t), t), ab - t * float(np.dot(ab, t))
        u2 = np.clip(dot3(pa2, ab2) / max(float(np.dot(ab2, ab2)), 1e-12), 0, 1)
        across = np.linalg.norm(pa2 - np.outer(u2, ab2), axis=1)
        nearer = dot3(a + np.outer(u2, ab), t) > dot3(p[idx], t) + 0.01 * scale
        shown[idx] |= (across < reach * scale) & nearer
    out[near] = shown & ~own
    return out


def paint(model_glb, views, report=None, debug=None, closeups=None, blank=False, hair=None, hands=None,
          hand_pixels=None):
    """Return the GLB with its base colour texture painted from `views` ({view name: PNG bytes}).

    `closeups` ({view name: {"png": bytes, "box": [x0, y0, x1, y1]}}) are parts of those views upscaled
    (npcgen/headdetail.py): each is placed with its view's fit, scaled, and outweighs the view where it sees.
    `blank`: the texture holds no colour of its own (npcgen/unwrap.py), so surface no picture sees takes the colour of
    the nearest painted surface instead.
    `hair` (npcgen/hairmask.py): a function giving where a picture shows a thing ("hair" or "ear") as a boolean array
    (PNG bytes, name, thing). It is asked about each picture and about the bare model drawn as that picture sees it
    (clay_view); where the model has hair, a picture that shows no hair there (a generator's fringe hanging lower than the
    picture's) gives it no colour, and hair left unpainted takes the colour of the painted hair nearby. Model skin is left
    alone, so a painted-on hairline (short hair the shape does not show) still comes from the pictures.
    `hands` ({side: 21 points in the model's frame, NaN where unsure}, npcgen/hands3d.points): the hands are about to be
    replaced, and a picture gives no colour to surface it shows a hand in front of (_behind_hands): the pictures' fingers
    are longer than the generator's mitten, and landed on the clothes behind it as finger-shaped skin.
    `hand_pixels` ({view: boolean mask of the picture}, npcgen/hands3d.picture_hands): where each picture itself shows a
    hand; that picture gives no colour there either, but to the model's own hand (within HAND_ZONE of its bones and
    facing away from them, HAND_FACING)."""
    glb = Glb(model_glb)
    prims = textured_primitives(glb)
    if not prims:
        raise ValueError("The model has no UV-mapped colour texture to paint")
    images = {}
    report = report if report is not None else {}
    report["views"] = {}
    all_pos = np.concatenate([p[0] for p in prims])
    height = float(all_pos[:, 1].max() - all_pos[:, 1].min())
    tri_all = np.concatenate([p[3] + sum(len(q[0]) for q in prims[:i]) for i, p in enumerate(prims)])

    pictures = {}
    for view, raw in views.items():
        rgb = np.asarray(Image.open(io.BytesIO(raw)).convert("RGB"))
        fit, iou, head_iou = view_fit(all_pos, tri_all, view, rgb)
        pictures[view] = [rgb, fit, iou]
        report["views"][view] = {"outline_match": round(float(iou), 4), "head_match": round(float(head_iou), 4),
                                 "head_fit_used": fit.head is not None, "scale": round(float(fit.body[0]), 2)}
    best = max(v[2] for v in pictures.values())
    for view, entry in pictures.items():
        trust = float(np.clip(1 - (best - entry[2]) * TRUST_DROP, MIN_TRUST, 1))
        entry[1] = entry[1]._replace(trust=trust)
        report["views"][view]["trust"] = round(trust, 2)
    hair_masks = _hair_masks(hair, views, pictures, all_pos, tri_all, report) if hair is not None else {}
    bones, middles = _hand_bones(hands) if hands else ([], [])
    hand_only = _hand_bones({side: np.asarray(p, float)[:21] for side, p in hands.items()})[0] if hands else []
    hand_pixels = {v: Mask(m, 0, 0, 1.0) for v, m in (hand_pixels or {}).items() if v in pictures}
    if bones or hand_pixels:
        report["hands"] = {}                         # texels each picture gave no colour, for a hand in front of them
    layers = [(view, rgb, fit, 1) for view, (rgb, fit, _iou) in pictures.items()]
    for view, close in (closeups or {}).items():
        if view not in pictures:
            continue
        rgb = np.asarray(Image.open(io.BytesIO(close["png"])).convert("RGB"))
        x0, y0, x1, _y1 = close["box"]
        zoom = rgb.shape[1] / (x1 - x0)
        fit = pictures[view][1]

        def scaled(f):
            return None if f is None else (f[0] * zoom, (f[1] - x0) * zoom, (f[2] - y0) * zoom)
        layers.append((view, rgb, fit._replace(body=scaled(fit.body), head=scaled(fit.head), trust=fit.trust * CLOSEUP_WEIGHT), zoom))
        report.setdefault("closeups", {})[view] = {"zoom": round(float(zoom), 2)}

    for pos, nrm, uv, tri, image_index in prims:
        if image_index not in images:
            base = glb.image(image_index)
            images[image_index] = {"mode": base.mode, "pixels": np.asarray(base.convert("RGB")).astype(np.float32),
                                   "sum": None, "weight": None}
        entry = images[image_index]
        th, tw = entry["pixels"].shape[:2]
        if entry["sum"] is None:
            entry["sum"] = np.zeros((th * tw, 3), np.float64)
            entry["weight"] = np.zeros(th * tw, np.float64)
        if bones or hand_pixels:
            pix, points, normals, t = texel_samples(pos, nrm, uv, tri, (tw, th), with_triangles=True)
            base = entry.get("islands_seen", 0)
            entry.setdefault("island", []).append(uv_islands(tri, len(pos))[t] + base)
            entry["islands_seen"] = base + len(pos)
        else:
            pix, points, normals = texel_samples(pos, nrm, uv, tri, (tw, th))
        normals = _smooth_normals(points, normals, height)
        seen = [(layer, *project(points, normals, all_pos, tri_all, *layer[:3], height, layer[3])) for layer in layers]
        if bones or hand_pixels:
            lost = np.zeros(len(points))
            for (view, _rgb, fit, zoom), _colour, weight in seen:
                if zoom == 1:
                    behind = _behind_hands(points, bones, middles, VIEWS[view][1], height / 1.7, normals)
                    if view in hand_pixels:
                        shown = np.flatnonzero(_mask_at(hand_pixels[view], *view_xy(points, view, fit)) & (weight > 0))
                        own = (_near_bones(points[shown], bones, height / 1.7, normals=normals[shown]) if bones
                               else np.zeros(len(shown), bool))
                        behind[shown[~own]] = True
                    behind &= weight > 0
                    lost += np.where(behind, weight, 0)
                    weight[behind] = 0
                    report["hands"][view] = report["hands"].get(view, 0) + int(behind.sum())
            np.add.at(entry.setdefault("behind_hand", np.zeros(len(entry["weight"]))), pix, lost)
        hairy, known, refused = np.zeros(len(points), bool), np.zeros(len(points), bool), np.zeros(len(points))
        if hair_masks:
            # A picture's colour is refused where the model, drawn as that picture sees it, has hair and the picture has
            # none (the picture shows what is behind the model's hair there). Judged per picture, so where the pictures
            # disagree among themselves (a temple one shows as hair and another as skin) their colours still blend. The
            # refused share of a texel's weight later turns its colour towards the hair's (see below).
            at = {view: view_xy(points, view, pictures[view][1]) for view in hair_masks}
            model_hair = {view: _mask_at(masks[1], *at[view]) for view, masks in hair_masks.items()}
            picture_hair = {view: _mask_at(masks[0], *at[view]) for view, masks in hair_masks.items()}
            share, total = np.zeros(len(points)), np.zeros(len(points))
            for (view, _rgb, _fit, zoom), colour, weight in seen:
                if view not in hair_masks:
                    continue
                if zoom == 1:
                    share += weight * model_hair[view]
                    total += weight
                behind = model_hair[view] & ~picture_hair[view]
                refused += weight * behind
                kept = weight * ~behind
                np.add.at(entry.setdefault("kept_sum", np.zeros_like(entry["sum"])), pix, colour * kept[:, None])
                np.add.at(entry.setdefault("kept_weight", np.zeros_like(entry["weight"])), pix, kept)
            known = total > 0
            hairy = known & (share >= HAIR_SHARE * np.maximum(total, 1e-12))
            np.add.at(entry.setdefault("refused", np.zeros(len(entry["weight"]))), pix, refused)
        for (view, rgb, fit, zoom), colour, weight in seen:
            np.add.at(entry["sum"], pix, colour * weight[:, None])
            np.add.at(entry["weight"], pix, weight)
            counted = report["views"][view] if zoom == 1 else report["closeups"][view]
            counted["texels"] = counted.get("texels", 0) + int((weight > 0).sum())
        entry.setdefault("covered", []).append(pix)
        entry.setdefault("points", []).append(points)
        if bones:
            entry.setdefault("normals", []).append(normals)
        entry.setdefault("hair", []).append(hairy)
        entry.setdefault("hair_known", []).append(known)

    for image_index, entry in images.items():
        th, tw = entry["pixels"].shape[:2]
        old = entry["pixels"].reshape(-1, 3)
        pix = np.concatenate(entry["covered"])
        points = np.concatenate(entry["points"])
        pix, first = np.unique(pix, return_index=True)
        points = points[first]
        normals = np.concatenate(entry["normals"])[first] if "normals" in entry else None
        islands = np.concatenate(entry["island"])[first] if "island" in entry else None
        hairy = np.concatenate(entry["hair"])[first]
        known = np.concatenate(entry["hair_known"])[first]
        if hairy.any() and not known.all():
            # surface no picture sees (the underside of a fringe) is hair where most of the seen surface around it is
            share, found = _near_colour(points, known, np.repeat(hairy[:, None], 3, 1).astype(np.float64), height)
            hairy |= ~known & found & (share[:, 0] >= HAIR_SHARE)
        wsum = entry["weight"][pix]
        projected = entry["sum"][pix] / np.maximum(wsum, 1e-9)[:, None]
        alpha = np.where(wsum > 0, np.maximum(np.clip(wsum / FULL_WEIGHT, 0, 1), SEEN_ALPHA), 0)[:, None]
        if blank:
            hidden = _fill_colour(points, alpha[:, 0] >= SEEN_ALPHA, projected, height)
        else:
            # Where no picture sees the surface, keep the generator's texture but shift it to the pictures' local tone.
            gain = _gain_field(points, alpha[:, 0], projected, old[pix], height)
            hidden = np.clip(old[pix] * gain, 0, 255)
            # ...unless painted surface lies within a few millimetres (the inside of a groove, behind a strand of hair)
            near, found = _near_colour(points, alpha[:, 0] >= SEEN_ALPHA, projected, height)
            hidden[found] = near[found]
        if "behind_hand" in entry:
            # Surface a picture showed a hand in front of, which no other picture sees properly, takes the colour of the
            # painted surface around it, and so does surface near a hand that no picture sees: the generator's own colour
            # there has the hand in it too (pink blocks on Devi's trousers, orange ones on Mabel's dress, from its coarse
            # colour grid), and a picture that sees the surface only edge-on gave Laura's coat a dark patch behind each
            # hand from its outline.
            lost = entry["behind_hand"][pix]
            scale = height / 1.7
            near = np.zeros(len(pix), bool)
            # the model's own hand is painted, but gives no colour to what lies behind it: it is 1 or 2 cm in front, the
            # nearest painted surface in space, and its skin came through (the forearm, which stays, may)
            own = np.zeros(len(pix), bool)
            if bones:
                close = np.flatnonzero(np.min([np.linalg.norm(points - m, axis=1) for m in middles], 0) < 0.5 * scale)
                near[close] = (_near_bones(points[close], bones, scale, HAND_NEAR)
                               & ~_near_bones(points[close], bones, scale, normals=normals[close]))
                own[close] = _near_bones(points[close], hand_only, scale, normals=normals[close])
            hid = (lost > 0) & (wsum < FULL_WEIGHT)
            covered = hid | (near & (alpha[:, 0] < 1))
            if covered.any():
                fill = _fill_colour(points, (alpha[:, 0] >= SEEN_ALPHA) & (lost == 0) & ~own, projected, height,
                                    smooth=True)
                hidden[covered] = fill[covered]
                alpha[hid] = 0
                report["hands"]["filled"] = report["hands"].get("filled", 0) + int(covered.sum())
        colour = alpha * projected + (1 - alpha) * hidden
        if hair_masks:
            # Texels a picture paints with what lies behind the model's hair (and hair no picture sees at all) take the
            # colour of the pictures that were not refused there, or where none is left, of the well-painted hair nearby.
            # Where that happens is smoothed over a few millimetres of surface, so the change has no hard, stepped edge
            # (a picture that sees the surface edge-on is refused texel by texel).
            refused_weight = entry["refused"][pix]
            changed = np.where(wsum > 0, refused_weight > HAIR_REFUSED * wsum, hairy).astype(np.float64)
            changed, _found = _near_colour(points, np.ones(len(points), bool), np.repeat(changed[:, None], 3, 1), height,
                                           cell=HAIR_SOFT, reach=0, smooth=True)
            changed = np.clip(changed[:, 0], 0, 1)
            source = hairy & (alpha[:, 0] >= SEEN_ALPHA) & (refused_weight <= HAIR_REFUSED * wsum)
            turn = changed > 0.01
            if turn.any() and source.any():
                near = np.flatnonzero(turn | source)
                hair_colour = np.zeros_like(colour)
                hair_colour[near] = _fill_colour(points[near], source[near], projected[near], height, smooth=True)
                kept_weight = entry["kept_weight"][pix]
                kept = entry["kept_sum"][pix] / np.maximum(kept_weight, 1e-9)[:, None]
                still = np.clip(kept_weight / FULL_WEIGHT, 0, 1)[:, None]
                instead = still * kept + (1 - still) * hair_colour
                colour[turn] = (1 - changed[turn, None]) * colour[turn] + changed[turn, None] * instead[turn]
            report["hair"]["texels"] = report["hair"].get("texels", 0) + int(hairy.sum())
            report["hair"]["refused"] = report["hair"].get("refused", 0) + int((changed > 0.5).sum())
        new = old.copy()
        new[pix] = colour
        if islands is not None and "behind_hand" in entry and covered.any():
            # the texels a hand hid run on from the rest of their own surface (_island_fill); the fill above is only
            # what an island with nothing else painted keeps
            grid = np.full(th * tw, -1, np.int64)
            grid[pix] = islands
            gap, good = np.zeros(th * tw, bool), np.zeros(th * tw, bool)
            gap[pix[covered]] = True
            good[pix[(alpha[:, 0] >= SEEN_ALPHA) & ~covered & ~own]] = True
            new = _island_fill(new.reshape(th, tw, 3), gap.reshape(th, tw), grid.reshape(th, tw),
                               good.reshape(th, tw)).reshape(-1, 3)
        covered = np.zeros(th * tw, bool)
        covered[pix] = True
        # UV padding takes the new colour of the island next to it, so texture filtering never pulls in the old one
        new = _dilate_into(new.reshape(th, tw, 3), covered.reshape(th, tw), SEAM_DILATE)
        new = np.clip(new, 0, 255).astype(np.uint8)
        buf = io.BytesIO()
        Image.fromarray(new, "RGB").save(buf, "PNG", optimize=False, compress_level=6)
        glb.replace_image(image_index, buf.getvalue())
        if debug is not None:
            amap = np.full(th * tw, -1.0)
            amap[pix] = alpha[:, 0]
            debug[f"alpha_{image_index}"] = amap.reshape(th, tw)
        report.setdefault("textures", []).append({
            "size": [tw, th],
            "painted_share": round(float((alpha > 0.99).mean()), 3),
            "touched_share": round(float((alpha > 0).mean()), 3)})
    return glb.dumps()


def _trilinear(points, lo, size):
    """Grid corners around each point and their trilinear weights."""
    f = (points - lo) / size - 0.5
    i0 = np.floor(f).astype(int)
    t = f - i0
    for corner in range(8):
        o = np.array([(corner >> k) & 1 for k in range(3)])
        yield i0 + o, np.prod(np.where(o, t, 1 - t), axis=1)


def _smooth_normals(points, normals, height, cell=0.01):
    """Surface direction at the scale the pictures resolve (about a centimetre): the sides of small ridges and folds
    take the direction of the surface they sit on. Where opposite sides cancel out (a thin sheet such as a strand of
    hair or a hem), a texel keeps its own normal."""
    size = cell * height
    lo = points.min(0) - 2 * size
    dims = np.ceil((points.max(0) - lo) / size).astype(int) + 2
    total = np.zeros((np.prod(dims), 3))
    count = np.zeros(np.prod(dims))
    for corner, w in _trilinear(points, lo, size):
        key = np.ravel_multi_index(corner.T, dims)
        np.add.at(total, key, normals * w[:, None])
        np.add.at(count, key, w)
    smooth = np.zeros_like(normals)
    density = np.zeros(len(points))
    for corner, w in _trilinear(points, lo, size):
        key = np.ravel_multi_index(corner.T, dims)
        smooth += total[key] * w[:, None]
        density += count[key] * w
    length = np.linalg.norm(smooth, axis=1)
    coherent = (length > 0.5 * density) & (np.einsum("ij,ij->i", smooth, normals) > 0)
    return np.where(coherent[:, None], smooth / np.maximum(length, 1e-12)[:, None], normals)


def _colour_grid(points, painted, colour, height, cell, reach):
    """The mean painted colour in each cell of a 3D grid, spread `reach` cells into unpainted space: (sorted cell keys,
    their colours, grid origin, cell size, grid shape)."""
    size = cell * height
    lo = points.min(0) - size
    dims = np.ceil((points.max(0) - lo) / size).astype(int) + 2
    key = np.ravel_multi_index(((points - lo) / size).astype(int).T, dims)
    total = np.zeros((np.prod(dims), 3))
    count = np.zeros(np.prod(dims))
    np.add.at(total, key[painted], colour[painted])
    np.add.at(count, key[painted], 1)
    known_keys = np.nonzero(count)[0]
    known_rgb = total[known_keys] / count[known_keys][:, None]
    coords = np.stack(np.unravel_index(known_keys, dims), 1)
    for _ in range(reach):          # grow the painted cells outwards by one cell each round, sparsely
        grown_keys, grown_rgb = [], []
        for axis in range(3):
            for step in (1, -1):
                c = coords.copy()
                c[:, axis] += step
                ok = (c[:, axis] >= 0) & (c[:, axis] < dims[axis])
                grown_keys.append(np.ravel_multi_index(c[ok].T, dims))
                grown_rgb.append(known_rgb[ok])
        gk, gr = np.concatenate(grown_keys), np.concatenate(grown_rgb)
        new = ~np.isin(gk, known_keys)
        gk, gr = gk[new], gr[new]
        uk, inv = np.unique(gk, return_inverse=True)
        sums = np.zeros((len(uk), 3))
        np.add.at(sums, inv, gr)
        n = np.bincount(inv, minlength=len(uk))
        known_keys = np.concatenate([known_keys, uk])
        known_rgb = np.concatenate([known_rgb, sums / n[:, None]])
        coords = np.stack(np.unravel_index(known_keys, dims), 1)
    order = np.argsort(known_keys)
    return known_keys[order], known_rgb[order], lo, size, dims


def _grid_read(grid, cells):
    """(colours, found) of the grid cells at integer coordinates `cells` (N, 3); not found outside the grid."""
    keys, rgb, _lo, _size, dims = grid
    inside = ((cells >= 0) & (cells < dims)).all(1)
    key = np.ravel_multi_index(np.where(inside[:, None], cells, 0).T, dims)
    at = np.clip(np.searchsorted(keys, key), 0, len(keys) - 1)
    found = inside & (keys[at] == key)
    return rgb[at], found


def _near_colour(points, painted, colour, height, cell=0.005, reach=3, smooth=False):
    """Mean painted colour in a fine 3D grid around each point, spread a few cells into unpainted space. `smooth` reads
    the grid trilinearly, so the colour runs on smoothly instead of in cell-sized blocks."""
    grid = _colour_grid(points, painted, colour, height, cell, reach)
    _keys, _rgb, lo, size, _dims = grid
    if not smooth:
        return _grid_read(grid, ((points - lo) / size).astype(int))
    f = (points - lo) / size - 0.5
    i0 = np.floor(f).astype(int)
    t = f - i0
    total, weight = np.zeros((len(points), 3)), np.zeros(len(points))
    for corner in range(8):
        o = np.array([(corner >> k) & 1 for k in range(3)])
        w = np.prod(np.where(o, t, 1 - t), axis=1)
        value, found = _grid_read(grid, i0 + o)
        total += value * (w * found)[:, None]
        weight += w * found
    return total / np.maximum(weight, 1e-12)[:, None], weight > 0


def _fill_colour(points, painted, colour, height, smooth=False):
    """Colour for surface no picture sees, on a model with no colour of its own: the mean painted colour within a few
    millimetres, or failing that a few centimetres, or failing that the nearest painted colour further out."""
    if not painted.any():
        return np.full((len(points), 3), 128.0)
    out, found = _near_colour(points, painted, colour, height, smooth=smooth)
    for cell, reach in ((0.02, 3), (0.05, 40)):
        if found.all():
            break
        near, got = _near_colour(points, painted, colour, height, cell=cell, reach=reach, smooth=smooth)
        take = got & ~found
        out[take] = near[take]
        found |= got
    out[~found] = colour[painted].mean(0)
    return out


def _gain_field(points, alpha, projected, old, height, cell=0.04, reach=6):
    """Per-channel colour gain from the generator's texture to the pictures, smooth over the body.

    Measured on well-painted texels in a coarse 3D grid, spread into empty cells, and read back trilinearly.
    """
    size = cell * height
    lo = points.min(0) - size
    dims = np.ceil((points.max(0) - lo) / size).astype(int) + 2
    idx = ((points - lo) / size).astype(int)
    key = np.ravel_multi_index(idx.T, dims)
    good = alpha > 0.9
    num = np.zeros((np.prod(dims), 3))
    den = np.zeros((np.prod(dims), 3))
    np.add.at(num, key[good], projected[good])
    np.add.at(den, key[good], old[good])
    count = np.zeros(np.prod(dims))
    np.add.at(count, key[good], 1)
    known = count >= 20
    gain = np.ones((np.prod(dims), 3))
    gain[known] = np.clip((num[known] + 20) / (den[known] + 20), 0.5, 2.0)
    gain, known = gain.reshape(*dims, 3), known.reshape(dims)
    for _ in range(reach):          # spread into neighbouring empty cells
        total = np.zeros_like(gain)
        n = np.zeros(dims)
        for axis in range(3):
            for step in (1, -1):
                total += np.roll(gain * known[..., None], step, axis)
                n += np.roll(known, step, axis)
        grow = ~known & (n > 0)
        gain[grow] = total[grow] / n[grow][:, None]
        known |= grow
    gain[~known] = 1.0
    f = (points - lo) / size - 0.5
    i0 = np.clip(np.floor(f).astype(int), 0, dims - 2)
    t = np.clip(f - i0, 0, 1)
    out = np.zeros((len(points), 3))
    for corner in range(8):
        o = np.array([(corner >> k) & 1 for k in range(3)])
        w = np.prod(np.where(o, t, 1 - t), axis=1)
        c = i0 + o
        out += w[:, None] * gain[c[:, 0], c[:, 1], c[:, 2]]
    return out


def _dilate_into(values, known, steps):
    """Spread values from known texels into unknown neighbours, a ring at a time (keeps UV padding in step)."""
    values = values.copy()
    known = known.copy()
    for _ in range(steps):
        total = np.zeros_like(values)
        count = np.zeros(known.shape, np.float32)
        for dy, dx in ((0, 1), (0, -1), (1, 0), (-1, 0)):
            shifted_known = np.roll(known, (dy, dx), (0, 1))
            total += np.roll(values, (dy, dx), (0, 1)) * shifted_known[..., None]
            count += shifted_known
        grow = ~known & (count > 0)
        values[grow] = total[grow] / count[grow][:, None]
        known |= grow
    return values


if __name__ == "__main__":
    import argparse
    import pathlib
    import time

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("model")
    ap.add_argument("sheet_dir", help="folder with front.png, side_left.png, back.png, side_right.png")
    ap.add_argument("out")
    ap.add_argument("--debug", help="folder for the paint-coverage map")
    args = ap.parse_args()
    started = time.time()
    folder = pathlib.Path(args.sheet_dir)
    pics = {v: (folder / f"{v}.png").read_bytes() for v in VIEWS if (folder / f"{v}.png").exists()}
    info, maps = {}, {}
    model = pathlib.Path(args.model).read_bytes()
    painted = paint(model, pics, report=info, debug=maps)
    pathlib.Path(args.out).write_bytes(media.label(painted, media.source_models(model, *pics.values())))
    if args.debug:
        pathlib.Path(args.debug).mkdir(parents=True, exist_ok=True)
        for name, alpha in maps.items():
            rgb = np.zeros(alpha.shape + (3,), np.uint8)
            rgb[alpha >= 0] = (np.clip(alpha[alpha >= 0], 0, 1)[:, None] * [0, 255, 0] + (1 - np.clip(alpha[alpha >= 0], 0, 1))[:, None] * [255, 0, 0]).astype(np.uint8)
            media.png(Image.fromarray(rgb), pathlib.Path(args.debug) / f"{name}.png", models=media.source_models(*pics.values()))
    info["seconds"] = round(time.time() - started, 1)
    print(json.dumps(info, indent=1))
