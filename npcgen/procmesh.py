"""A small mesh kernel for things that are built from a recipe instead of drawn from a picture (npcgen/glasses.py).

Image-to-3D cannot make thin or clear things: a frame is a wire a millimetre and a half thick, a lens has no surface a camera can
find. These are built here instead, from numbers: a profile swept along a path (a rim, a bridge, an arm), a lens surface with
its curve, an ellipsoid or a cylinder for the small parts, and a glTF writer for the result. Everything is plain numpy and every
surface has analytic vertex normals, so a rim is smooth shaded with a few hundred triangles and the same input always gives the
same bytes.

Units are whatever the caller uses (npcgen/glasses.py works in millimetres and writes metres). The meshes are right handed, Y up.
"""
import json
import struct

import numpy as np

from . import media


def _unit(rows):
    rows = np.asarray(rows, float)
    return rows / np.maximum(np.linalg.norm(rows, axis=-1, keepdims=True), 1e-12)


def matmul(a, b):
    """a times b for small matrices (and a matrix times a vector), written out with einsum. NPC Forge does not use the @ operator (npcgen/anim.py: a threaded BLAS
    product corrupted whole bands of a picture now and then on the development machine; tests/test_guided.py forbids it), and 3x3 and 4x4 products are too small to need it."""
    a, b = np.asarray(a, float), np.asarray(b, float)
    return np.einsum("ij,jk->ik", a, b) if b.ndim == 2 else np.einsum("ij,j->i", a, b)


def transform(points, m):
    """The points (n x 3) turned by the 3x3 matrix m, which is points @ m.T, with the sum written out for the same reason."""
    p, m = np.asarray(points, float), np.asarray(m, float)
    return p[:, 0:1] * m[:, 0] + p[:, 1:2] * m[:, 1] + p[:, 2:3] * m[:, 2]


class Mesh:
    """Triangles with a normal per vertex, and a colour (RGBA, 0 to 1) and texture coordinates per vertex when the part has them."""
    __slots__ = ("pos", "nrm", "tri", "col", "uv")

    def __init__(self, pos, nrm, tri, col=None, uv=None):
        self.pos = np.asarray(pos, float).reshape(-1, 3)
        self.nrm = _unit(np.asarray(nrm, float).reshape(-1, 3))
        self.tri = np.asarray(tri, np.int64).reshape(-1, 3)
        self.col = None if col is None else np.asarray(col, float).reshape(-1, 4)
        self.uv = None if uv is None else np.asarray(uv, float).reshape(-1, 2)

    @property
    def faces(self):
        return len(self.tri)

    def bounds(self):
        return self.pos.min(0), self.pos.max(0)

    def volume(self):
        """The signed volume of a closed surface (positive when it faces outward): what the tests use to check winding."""
        a, b, c = self.pos[self.tri[:, 0]], self.pos[self.tri[:, 1]], self.pos[self.tri[:, 2]]
        return float(np.einsum("ij,ij->i", a, np.cross(b, c)).sum() / 6.0)

    def facing(self):
        """The share of faces whose winding agrees with the normals of their vertices (1.0: all of them point the same way)."""
        a, b, c = self.pos[self.tri[:, 0]], self.pos[self.tri[:, 1]], self.pos[self.tri[:, 2]]
        face = np.cross(b - a, c - a)
        mean = self.nrm[self.tri].sum(1)
        keep = np.linalg.norm(face, axis=1) > 1e-18
        return float((np.einsum("ij,ij->i", face[keep], mean[keep]) > 0).mean()) if keep.any() else 1.0

    def oriented(self):
        """The mesh with every face wound the way the normals of its vertices point (the normals are exact, the winding follows them)."""
        a, b, c = self.pos[self.tri[:, 0]], self.pos[self.tri[:, 1]], self.pos[self.tri[:, 2]]
        flip = np.einsum("ij,ij->i", np.cross(b - a, c - a), self.nrm[self.tri].sum(1)) < 0
        tri = self.tri.copy()
        tri[flip] = tri[flip][:, [0, 2, 1]]
        return Mesh(self.pos, self.nrm, tri, self.col, self.uv)

    def transformed(self, matrix):
        """The mesh under a 4x4 matrix (rotation, scale and shift; a mirror flips the winding back)."""
        m = np.asarray(matrix, float)
        linear = m[:3, :3]
        pos = transform(self.pos, linear) + m[:3, 3]
        nrm = _unit(transform(self.nrm, np.linalg.inv(linear).T))
        tri = self.tri[:, [0, 2, 1]] if np.linalg.det(linear) < 0 else self.tri
        return Mesh(pos, nrm, tri, self.col, self.uv)

    def translated(self, offset):
        return Mesh(self.pos + np.asarray(offset, float), self.nrm, self.tri, self.col, self.uv)

    def mirrored_x(self):
        return self.transformed(np.diag([-1.0, 1.0, 1.0, 1.0]))

    def with_colours(self, fn):
        """The same mesh with colours fn(positions) -> (N, 4)."""
        return Mesh(self.pos, self.nrm, self.tri, np.asarray(fn(self.pos), float), self.uv)

    def with_uv(self, fn):
        """The same mesh with texture coordinates fn(positions) -> (N, 2) (glTF's: v counts down from the top of the picture)."""
        return Mesh(self.pos, self.nrm, self.tri, self.col, np.asarray(fn(self.pos), float))


def merge(meshes):
    """One mesh from several (a part without colours gets white, and one without texture coordinates zeros, when another has them)."""
    meshes = [m for m in meshes if m is not None and m.faces]
    if not meshes:
        return Mesh(np.zeros((0, 3)), np.zeros((0, 3)), np.zeros((0, 3), np.int64))
    starts = np.cumsum([0] + [len(m.pos) for m in meshes[:-1]])
    coloured = any(m.col is not None for m in meshes)
    col = np.concatenate([m.col if m.col is not None else np.ones((len(m.pos), 4)) for m in meshes]) if coloured else None
    textured = any(m.uv is not None for m in meshes)
    uv = np.concatenate([m.uv if m.uv is not None else np.zeros((len(m.pos), 2)) for m in meshes]) if textured else None
    return Mesh(np.concatenate([m.pos for m in meshes]), np.concatenate([m.nrm for m in meshes]),
                np.concatenate([m.tri + s for m, s in zip(meshes, starts)]), col, uv)


# ---- profiles: the shape of a cut across a rim, a bridge or an arm -------------------------------------------------------------------
def profile_rounded_rect(corner=0.3, per_corner=3):
    """(points, normals) of a unit square (side 1) with rounded corners, counter-clockwise in (u, v). `corner` is the radius as a
    share of the half side (1 is a circle, 0.3 a softly rounded rectangle); each corner has `per_corner` points and the straight
    sides need none, so the normals are continuous and the shading smooth. Where two corners meet (a circle) the repeated point is dropped."""
    r = 0.5 * float(np.clip(corner, 0.05, 1.0))
    per_corner = max(int(per_corner), 2)
    points, normals = [], []
    for start, centre in ((0.0, (0.5 - r, 0.5 - r)), (90.0, (-0.5 + r, 0.5 - r)), (180.0, (-0.5 + r, -0.5 + r)), (270.0, (0.5 - r, -0.5 + r))):
        for k in range(per_corner):
            angle = np.radians(start + 90.0 * k / (per_corner - 1))
            n = np.array([np.cos(angle), np.sin(angle)])
            points.append(np.array(centre) + r * n)
            normals.append(n)
    points, normals = np.array(points), np.array(normals)
    keep = np.linalg.norm(points - np.roll(points, 1, 0), axis=1) > 1e-9          # (a full circle has each corner end where the next begins)
    return points[keep], normals[keep]


def profile_ellipse(count=10):
    """(points, normals) of a unit circle (diameter 1) with `count` points."""
    angle = np.arange(count) * 2 * np.pi / count
    n = np.stack([np.cos(angle), np.sin(angle)], 1)
    return 0.5 * n, n


# ---- sweeping a profile along a path ----------------------------------------------------------------------------------------------------
def planar_frames(path, plane_normal):
    """(P, 2, 3) frames (u, v) for a path that bends in one plane: u is the plane's normal everywhere and v lies in the plane, across the
    path, so a profile does not twist along an arm. Both are unit and square to the path's direction at every point."""
    path = np.asarray(path, float)
    t = tangents(path)
    u = _unit(np.broadcast_to(np.asarray(plane_normal, float), t.shape))
    v = _unit(np.cross(u, t))
    return np.stack([u, v], 1)


def round_frames(path):
    """(P, 2, 3) frames for a path with a round profile, where any two axes square to the path will do (the path may run any way)."""
    t = tangents(path)
    ref = np.where((np.abs(t[:, 1]) > 0.9)[:, None], np.array([1.0, 0.0, 0.0]), np.array([0.0, 1.0, 0.0]))
    u = _unit(np.cross(ref, t))
    return np.stack([u, np.cross(t, u)], 1)


def tangents(path, closed=False):
    """Unit direction of the path at each point (central differences; the ends use the first and last segment)."""
    path = np.asarray(path, float)
    if closed:
        return _unit(np.roll(path, -1, 0) - np.roll(path, 1, 0))
    t = np.empty_like(path)
    t[1:-1] = path[2:] - path[:-2]
    t[0], t[-1] = path[1] - path[0], path[-1] - path[-2]
    return _unit(t)


def sweep(path, frames, profile, profile_normals, scales=None, closed=False, dome=3, caps=("dome", "dome"), colour=None):
    """A tube: `profile` (K, 2 points, counter-clockwise, with `profile_normals`) placed at every point of `path` (P, 3) in that point's
    frame (u, v) from `frames` (P, 2, 3), scaled there by `scales` (P, 2). A closed path joins its ends (a rim). An open one has a cap
    at each end (`caps`: "dome", a rounded end `dome` rings deep; "flat", cut square, for two parts that meet; or "open"), so nothing
    is left open by default. Returns an outward facing Mesh."""
    path = np.asarray(path, float)
    frames = np.asarray(frames, float)
    profile, profile_normals = np.asarray(profile, float), np.asarray(profile_normals, float)
    count, k = len(path), len(profile)
    scales = np.ones((count, 2)) if scales is None else np.asarray(scales, float)
    su, sv = np.maximum(scales[:, 0], 1e-6), np.maximum(scales[:, 1], 1e-6)
    u, v = frames[:, 0], frames[:, 1]

    def ring(su_, sv_, centre, u_, v_):
        pos = (centre[:, None, :] + (su_[:, None] * profile[None, :, 0])[..., None] * u_[:, None, :]
               + (sv_[:, None] * profile[None, :, 1])[..., None] * v_[:, None, :])
        nrm = _unit((profile_normals[None, :, 0] / su_[:, None])[..., None] * u_[:, None, :]
                    + (profile_normals[None, :, 1] / sv_[:, None])[..., None] * v_[:, None, :])
        return pos, nrm

    pos, nrm = ring(su, sv, path, u, v)
    rings_p, rings_n = list(pos), list(nrm)
    poles, flats = [], []                                    # domed ends: (which end, pole); square ends: (which end, pole, direction)
    if not closed:
        t = tangents(path)
        extent = 0.5 * float(np.minimum(su, sv).mean() * np.ptp(profile, axis=0).min())
        for which, end, direction, cap in (("start", 0, -t[0], caps[0]), ("end", count - 1, t[-1], caps[1])):
            if cap == "flat":
                flats.append((which, path[end], direction))
            elif cap == "dome":
                sequence = []
                for j in range(1, int(dome) + 1):
                    phi = 0.5 * np.pi * j / (dome + 1)
                    p, n = ring(su[end:end + 1] * np.cos(phi), sv[end:end + 1] * np.cos(phi),
                                path[end:end + 1] + direction * extent * np.sin(phi), u[end:end + 1], v[end:end + 1])
                    sequence.append((p[0], _unit(n[0] * np.cos(phi) + direction * np.sin(phi))))
                if which == "start":
                    rings_p = [p for p, _n in reversed(sequence)] + rings_p
                    rings_n = [n for _p, n in reversed(sequence)] + rings_n
                else:
                    rings_p += [p for p, _n in sequence]
                    rings_n += [n for _p, n in sequence]
                poles.append((which, (path[end] + direction * extent, direction)))
    P = np.concatenate(rings_p)
    N = np.concatenate(rings_n)
    rings = len(rings_p)
    tri = []
    ids = np.arange(rings * k).reshape(rings, k)
    nxt = np.roll(np.arange(k), -1)
    for i in range(rings if closed else rings - 1):
        a, b = ids[i], ids[(i + 1) % rings]
        tri.append(np.stack([a, a[nxt], b[nxt]], 1))
        tri.append(np.stack([a, b[nxt], b], 1))
    for which, (pole_pos, pole_nrm) in poles:
        index = len(P)
        P = np.vstack([P, pole_pos])
        N = np.vstack([N, pole_nrm])
        ring_ids = ids[0] if which == "start" else ids[-1]
        tri.append(np.stack([ring_ids[nxt], ring_ids, np.full(k, index)], 1) if which == "start"
                   else np.stack([ring_ids, ring_ids[nxt], np.full(k, index)], 1))
    for which, centre, direction in flats:                   # a square end: its own vertices, so the edge stays sharp
        edge = pos[0] if which == "start" else pos[-1]
        first = len(P)
        P = np.vstack([P, edge, centre[None, :]])
        N = np.vstack([N, np.tile(direction, (k + 1, 1))])
        ring_ids = first + np.arange(k)
        tri.append(np.stack([ring_ids, ring_ids[nxt], np.full(k, first + k)], 1))
    mesh = Mesh(P, N, np.concatenate(tri), None if colour is None else np.tile(np.asarray(colour, float), (len(P), 1)))
    return mesh.oriented()


# ---- small parts ----------------------------------------------------------------------------------------------------------------------------
def ellipsoid(centre, axes, around=12, rings=6):
    """A UV sphere deformed by `axes` (3, 3: the three semi-axes as rows, in any rotation), e.g. a nose pad."""
    axes = np.asarray(axes, float)
    theta = np.linspace(0.0, np.pi, rings + 1)[1:-1]
    phi = np.arange(around) * 2 * np.pi / around
    direction = np.concatenate([
        np.array([[0.0, 0.0, 1.0]]),
        np.stack([np.outer(np.sin(theta), np.cos(phi)), np.outer(np.sin(theta), np.sin(phi)),
                  np.repeat(np.cos(theta)[:, None], around, 1)], -1).reshape(-1, 3),
        np.array([[0.0, 0.0, -1.0]])])
    pos = np.asarray(centre, float) + transform(direction, axes.T)
    nrm = _unit(transform(direction, np.linalg.inv(axes)))
    tri = []
    for i in range(around):
        j = (i + 1) % around
        tri.append([0, 1 + j, 1 + i])
        for r in range(len(theta) - 1):
            a, b = 1 + r * around + i, 1 + r * around + j
            c, d = 1 + (r + 1) * around + i, 1 + (r + 1) * around + j
            tri += [[a, b, d], [a, d, c]]
        last = 1 + (len(theta) - 1) * around
        tri.append([last + i, last + j, len(pos) - 1])
    return Mesh(pos, nrm, tri).oriented()


def cylinder(start, end, radius, around=10):
    """A closed cylinder (flat caps) between two points, for hinge pins and screws."""
    start, end = np.asarray(start, float), np.asarray(end, float)
    axis = _unit(end - start)
    ref = np.array([1.0, 0.0, 0.0]) if abs(axis[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    a = _unit(np.cross(axis, ref))
    b = np.cross(axis, a)
    angle = np.arange(around) * 2 * np.pi / around
    ring = np.cos(angle)[:, None] * a + np.sin(angle)[:, None] * b
    pos = np.concatenate([start + radius * ring, end + radius * ring, [start], [end], start + radius * ring, end + radius * ring])
    nrm = np.concatenate([ring, ring, [-axis], [axis], np.tile(-axis, (around, 1)), np.tile(axis, (around, 1))])
    cap0, cap1 = 2 * around, 2 * around + 1
    tri = []
    for i in range(around):
        j = (i + 1) % around
        tri += [[i, j, around + j], [i, around + j, around + i],
                [cap0, 2 * around + 2 + j, 2 * around + 2 + i], [cap1, 2 * around + 2 + around + i, 2 * around + 2 + around + j]]
    return Mesh(pos, nrm, tri).oriented()


def lens_slab(outline, surface, thickness, rings=4, rim_normal=None):
    """A lens: a front surface and a back one `thickness` behind it, and a wall round the edge. `outline` (n, 2) is counter-clockwise seen
    from the front; `surface(x, y)` gives (z, unit normal) of the front surface there (a sphere for a base curve, a plane for none).
    The surface is a polar grid from the middle of the outline out to its edge, so any star-shaped outline fills without holes."""
    outline = np.asarray(outline, float)
    n = len(outline)
    centre = outline.mean(0)
    scale = np.linspace(0.0, 1.0, rings + 1)
    grid = centre + scale[:, None, None] * (outline - centre)[None, :, :]          # (rings + 1, n, 2); ring 0 is the middle (n copies)
    flat = grid.reshape(-1, 2)
    z, normal = surface(flat[:, 0], flat[:, 1])
    front = np.column_stack([flat, z])
    # a single vertex for the middle
    keep = np.concatenate([[0], np.arange(n, (rings + 1) * n)])
    front_pos, front_nrm = front[keep], normal[keep]
    rings_n = rings

    def surface_tris(offset):
        tri = []
        ring = lambda r: offset + 1 + (r - 1) * n + np.arange(n)           # noqa: E731
        nxt = np.roll(np.arange(n), -1)
        first = ring(1)
        tri.append(np.stack([np.full(n, offset), first, first[nxt]], 1))
        for r in range(1, rings_n):
            a, b = ring(r), ring(r + 1)
            tri.append(np.stack([a, b, b[nxt]], 1))
            tri.append(np.stack([a, b[nxt], a[nxt]], 1))
        return np.concatenate(tri)

    front_mesh = Mesh(front_pos, front_nrm, surface_tris(0)).oriented()
    back_pos = front_pos - np.array([0.0, 0.0, thickness])
    back_mesh = Mesh(back_pos, -front_nrm, surface_tris(0)).oriented()
    edge_z = front_pos[-n:, 2]
    edge = outline
    t = _unit(np.roll(edge, -1, 0) - np.roll(edge, 1, 0))
    out = np.stack([t[:, 1], -t[:, 0], np.zeros(n)], 1)
    top = np.column_stack([edge, edge_z])
    bottom = np.column_stack([edge, edge_z - thickness])
    nxt = np.roll(np.arange(n), -1)
    wall = Mesh(np.concatenate([top, bottom]), np.concatenate([out, out]),
                np.concatenate([np.stack([np.arange(n), np.arange(n) + n, nxt + n], 1), np.stack([np.arange(n), nxt + n, nxt], 1)])).oriented()
    return merge([front_mesh, back_mesh, wall])


# ---- glTF ------------------------------------------------------------------------------------------------------------------------------------
class Gltf:
    """Builds a GLB: materials, meshes (one primitive per material) and a node tree, then `dumps`."""

    def __init__(self, generator="NPC Forge"):
        self.doc = {"asset": {"version": "2.0", "generator": generator}, "scene": 0, "scenes": [{"nodes": []}], "nodes": [], "meshes": [],
                    "materials": [], "accessors": [], "bufferViews": [], "buffers": [{"byteLength": 0}]}
        self.blob = bytearray()

    def _view(self, data, target=None):
        self.blob.extend(b"\0" * (-len(self.blob) % 4))
        view = {"buffer": 0, "byteOffset": len(self.blob), "byteLength": len(data)}
        if target is not None:
            view["target"] = target
        self.doc["bufferViews"].append(view)
        self.blob.extend(data)
        return len(self.doc["bufferViews"]) - 1

    def texture(self, png):
        """An embedded PNG as a texture (linear filtering, clamped at the edges); returns its index for `material(texture=...)`."""
        image = len(self.doc.setdefault("images", []))
        self.doc["images"].append({"bufferView": self._view(png), "mimeType": "image/png"})
        self.doc.setdefault("samplers", [{"magFilter": 9729, "minFilter": 9729, "wrapS": 33071, "wrapT": 33071}])
        self.doc.setdefault("textures", []).append({"source": image, "sampler": 0})
        return len(self.doc["textures"]) - 1

    def _accessor(self, array, kind, component, target, bounds=False):
        view = self._view(array.tobytes(), target)
        acc = {"bufferView": view, "componentType": component, "count": int(len(array)), "type": kind}
        if bounds:
            acc["min"], acc["max"] = array.min(0).astype(float).tolist(), array.max(0).astype(float).tolist()
        self.doc["accessors"].append(acc)
        return len(self.doc["accessors"]) - 1

    def material(self, name, colour, metallic=0.0, roughness=0.5, opacity=1.0, double_sided=False, texture=None):
        """A PBR material; `colour` is sRGB 0-1 (converted here, glTF wants linear). Under 1 opacity it blends. A `texture` (from
        `texture()`) multiplies the colour and the opacity, so it can carry a gradient in its alpha."""
        linear = [float(((c + 0.055) / 1.055) ** 2.4 if c > 0.04045 else c / 12.92) for c in colour[:3]]
        mat = {"name": name, "pbrMetallicRoughness": {"baseColorFactor": [*[round(c, 5) for c in linear], round(float(opacity), 4)],
                                                      "metallicFactor": float(metallic), "roughnessFactor": float(roughness)}}
        if texture is not None:
            mat["pbrMetallicRoughness"]["baseColorTexture"] = {"index": texture, "texCoord": 0}
        if opacity < 1.0 or texture is not None:
            mat["alphaMode"] = "BLEND"
        if double_sided:
            mat["doubleSided"] = True
        self.doc["materials"].append(mat)
        return len(self.doc["materials"]) - 1

    def mesh(self, name, parts):
        """A glTF mesh from [(Mesh, material index)]; parts with no triangles are left out. Returns the mesh index, or None if all were empty."""
        prims = []
        for part, material in parts:
            if part is None or not part.faces:
                continue
            attrs = {"POSITION": self._accessor(part.pos.astype(np.float32), "VEC3", 5126, 34962, bounds=True),
                     "NORMAL": self._accessor(part.nrm.astype(np.float32), "VEC3", 5126, 34962)}
            if part.col is not None:
                attrs["COLOR_0"] = self._accessor(part.col.astype(np.float32), "VEC4", 5126, 34962)
            if part.uv is not None:
                attrs["TEXCOORD_0"] = self._accessor(part.uv.astype(np.float32), "VEC2", 5126, 34962)
            wide = len(part.pos) > 65535
            idx = part.tri.astype(np.uint32 if wide else np.uint16).ravel()
            prims.append({"attributes": attrs, "indices": self._accessor(idx, "SCALAR", 5125 if wide else 5123, 34963),
                          "material": material, "mode": 4})
        if not prims:
            return None
        self.doc["meshes"].append({"name": name, "primitives": prims})
        return len(self.doc["meshes"]) - 1

    def node(self, name, mesh=None, children=None, translation=None, rotation=None, scale=None):
        node = {"name": name}
        if mesh is not None:
            node["mesh"] = mesh
        if children:
            node["children"] = list(children)
        if translation is not None:
            node["translation"] = [float(v) for v in translation]
        if rotation is not None:
            node["rotation"] = [float(v) for v in rotation]
        if scale is not None:
            node["scale"] = [float(v) for v in scale]
        self.doc["nodes"].append(node)
        return len(self.doc["nodes"]) - 1

    def dumps(self, roots, extras=None):
        doc = dict(self.doc)
        doc["scenes"] = [{"nodes": list(roots)}]
        if extras:
            doc["asset"] = {**doc["asset"], "extras": extras}
        doc["buffers"] = [{"byteLength": len(self.blob)}]
        blob = bytes(self.blob) + b"\0" * (-len(self.blob) % 4)
        doc["buffers"] = [{"byteLength": len(blob)}]
        text = json.dumps(doc, separators=(",", ":")).encode()
        text += b" " * (-len(text) % 4)
        total = 12 + 8 + len(text) + 8 + len(blob)
        data = (struct.pack("<III", 0x46546C67, 2, total) + struct.pack("<II", len(text), 0x4E4F534A) + text
                + struct.pack("<II", len(blob), 0x004E4942) + blob)
        return media.label(data, ai_generated=False)


def yaw_quaternion(degrees):
    """The glTF rotation (x, y, z, w) of `degrees` about the vertical axis."""
    half = np.radians(degrees) / 2
    return (0.0, float(np.sin(half)), 0.0, float(np.cos(half)))
