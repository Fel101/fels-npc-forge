"""A relaxed five-finger hand, built from distance functions and meshed with surface nets. numpy only, so it runs inside
Blender (npcgen/blender/hands.py) and in the tests alike.

Why: image-to-3D generators read a picture in 16-pixel squares, and in a full-body view a finger is about 6 pixels wide,
so every model came out with mittens (two or three lumps for five fingers) although the turnaround pictures draw five
clean fingers (measured 2026-09-27 on nine library models). A hand made here replaces the mitten (hands.py).

Units are the hand's length (wrist crease to the middle fingertip, straight); the frame is +X towards the fingers,
+Y the thumb side, +Z the back of the hand, with the wrist crease at the origin. That is a right hand; a left hand is the
same numbers placed with a mirrored frame. The proportions are typical adult ones: palm about 0.53 of the length and 0.43
wide at the knuckles, the middle finger 0.49 from its knuckle, the index and ring a little shorter, the little finger
0.37; fingers gently spread and curled as a hand hangs relaxed.

The surface is a smooth union (polynomial smooth minimum) of rounded cones for the finger bones and the wrist, a rounded
box and ellipsoids for the palm and the balls of the thumb and little finger. Adjacent fingers are joined by a plain
minimum, so they stay apart, and each finger blends into the palm with a web. Surface nets can join two surfaces that
come within one grid cell of each other (between the fingers at their roots); the Blender side remeshes the result on
a voxel grid, which always gives a closed, clean surface.
"""
import math

import numpy as np

FINGERS = ("thumb", "index", "middle", "ring", "pinky")

# knuckle (MCP) centre, knuckle-to-tip length, share of each bone, radius at knuckle/PIP/DIP/tip, spread and curl (degrees)
DIGITS = {
    "index": {"mcp": (0.475, 0.135, 0.005), "length": 0.44, "shares": (0.46, 0.29, 0.25),
              "radii": (0.053, 0.046, 0.040, 0.036), "spread": 5.0, "curl": (10, 18, 10)},
    "middle": {"mcp": (0.490, 0.045, 0.012), "length": 0.49, "shares": (0.46, 0.30, 0.24),
               "radii": (0.055, 0.048, 0.041, 0.037), "spread": 0.0, "curl": (12, 22, 12)},
    "ring": {"mcp": (0.475, -0.045, 0.005), "length": 0.46, "shares": (0.46, 0.30, 0.24),
             "radii": (0.051, 0.044, 0.039, 0.035), "spread": -5.0, "curl": (15, 25, 13)},
    "pinky": {"mcp": (0.440, -0.130, -0.008), "length": 0.37, "shares": (0.44, 0.30, 0.26),
              "radii": (0.044, 0.039, 0.033, 0.030), "spread": -11.0, "curl": (18, 28, 15)},
}
# the thumb: its base in the palm, then its knuckle, joint and tip, resting beside the index finger
THUMB = {"joints": ((0.08, 0.10, -0.03), (0.26, 0.20, -0.08), (0.40, 0.23, -0.11), (0.52, 0.22, -0.14)),
         "radii": (0.069, 0.056, 0.050, 0.043)}
REACH = 0.95            # how far the relaxed hand reaches along +X from the wrist crease, in hand lengths


def rotation(axis, degrees):
    """The 3x3 matrix turning by `degrees` about the unit `axis`."""
    a = math.radians(degrees)
    c, s = math.cos(a), math.sin(a)
    x, y, z = axis
    return np.array([[c + x * x * (1 - c), x * y * (1 - c) - z * s, x * z * (1 - c) + y * s],
                     [y * x * (1 - c) + z * s, c + y * y * (1 - c), y * z * (1 - c) - x * s],
                     [z * x * (1 - c) - y * s, z * y * (1 - c) + x * s, c + z * z * (1 - c)]])


def joints():
    """{finger: [4 points]} from the knuckle (the thumb: its base in the palm) to the tip, and the wrist."""
    out = {"wrist": np.zeros(3), "thumb": [np.array(p, float) for p in THUMB["joints"]]}
    for f, d in DIGITS.items():
        turn = rotation((0, 0, 1), d["spread"])
        direction, side = turn @ np.array([1.0, 0, 0]), turn @ np.array([0, 1.0, 0])
        p = np.array(d["mcp"], float)
        pts, bend = [p], 0.0
        for share, curl in zip(d["shares"], d["curl"]):
            bend += curl
            p = p + (rotation(side, bend) @ direction) * share * d["length"]   # about +Y: +X turns towards -Z
            pts.append(p)
        out[f] = pts
    return out


def keypoints():
    """The 21 hand points in SDPose's order (npcgen/posekeys.py): wrist, then thumb, index, middle, ring and little
    finger, four each from the knuckle (the thumb: its base) out to the tip."""
    j = joints()
    return np.array([j["wrist"]] + [p for f in FINGERS for p in j[f]])


# ---- distance functions (negative inside) ---------------------------------------------------------------------------
def round_cone(P, a, b, r1, r2):
    """Exact distance to a capsule whose radius goes from r1 at a to r2 at b (after Inigo Quilez)."""
    a, b = np.asarray(a, float), np.asarray(b, float)
    ba = b - a
    l2 = float(ba @ ba)
    rr = r1 - r2
    a2 = l2 - rr * rr
    il2 = 1.0 / l2
    pa = P - a
    y = pa @ ba
    z = y - l2
    v = pa * l2 - np.outer(y, ba)
    x2 = np.einsum("ij,ij->i", v, v)
    y2, z2 = y * y * l2, z * z * l2
    k = math.copysign(1.0, rr) * rr * rr * x2 if rr != 0 else np.zeros_like(x2)
    d_end = np.sqrt(x2 + z2) * il2 - r2
    d_start = np.sqrt(x2 + y2) * il2 - r1
    d_side = (np.sqrt(np.maximum(x2 * a2 * il2, 0)) + y * rr) * il2 - r1
    return np.where(np.sign(z) * a2 * z2 > k, d_end, np.where(np.sign(y) * a2 * y2 < k, d_start, d_side))


def ellipsoid(P, c, r):
    """Approximate distance to an ellipsoid (exact on its axes)."""
    r = np.asarray(r, float)
    q = (P - np.asarray(c, float)) / r
    k0 = np.linalg.norm(q, axis=1)
    k1 = np.linalg.norm(q / r, axis=1)
    return k0 * (k0 - 1.0) / np.maximum(k1, 1e-12)


def round_box(P, c, half, r):
    q = np.abs(P - np.asarray(c, float)) - np.asarray(half, float)
    return np.linalg.norm(np.maximum(q, 0), axis=1) + np.minimum(q.max(1), 0) - r


def smin(a, b, k):
    """Smooth minimum: the two surfaces joined with a fillet about k wide."""
    h = np.maximum(k - np.abs(a - b), 0) / k
    return np.minimum(a, b) - h * h * k * 0.25


def hand_sdf(P, wrist_back=0.22):
    """Distance to the hand's surface; its wrist runs `wrist_back` hand lengths back into the forearm."""
    j = joints()
    flat = np.array([1.0, 0.70, 1.0])                     # the wrist is about 1.4 times wider than it is thick
    wrist = smin(round_cone(P * flat, (-wrist_back, 0, 0), (0.0, 0, 0), 0.106, 0.106),
                 round_cone(P * flat, (-0.02, 0, 0), (0.10, 0, -0.01), 0.104, 0.100), 0.06)
    palm = round_box(P, (0.27, -0.005, -0.006), (0.145, 0.112, 0.010), 0.056)
    back = ellipsoid(P, (0.30, -0.005, 0.0), (0.22, 0.17, 0.058))
    knuckles = round_cone(P, (0.455, -0.14, -0.006), (0.465, 0.14, 0.004), 0.050, 0.054)
    thumb_ball = ellipsoid(P, (0.17, 0.10, -0.035), (0.14, 0.075, 0.062))
    little_ball = ellipsoid(P, (0.22, -0.12, -0.028), (0.17, 0.058, 0.052))
    d = smin(smin(smin(smin(smin(wrist, palm, 0.07), back, 0.04), knuckles, 0.05), thumb_ball, 0.05), little_ball, 0.05)
    fingers, thumb = None, None
    for f in FINGERS:
        pts = j[f]
        radii = THUMB["radii"] if f == "thumb" else DIGITS[f]["radii"]
        fd = None
        for s in range(3):
            bone = round_cone(P, pts[s], pts[s + 1], radii[s], radii[s + 1])
            fd = bone if fd is None else smin(fd, bone, 0.012)
        if f == "thumb":
            thumb = fd
        else:
            fingers = fd if fingers is None else np.minimum(fingers, fd)
    return smin(smin(d, thumb, 0.06), fingers, 0.035)       # the thumb's web is wider than the fingers'


# ---- meshing --------------------------------------------------------------------------------------------------------
def surface_nets(field, origin, h):
    """Quads on the zero level of a sampled field (nx x ny x nz, negative inside): one vertex per cell the surface
    crosses, at the mean of its edges' crossings, and one quad per grid edge the surface crosses, facing out."""
    inside = field < 0
    nx, ny, nz = field.shape
    anyin = np.zeros((nx - 1, ny - 1, nz - 1), bool)
    allin = np.ones_like(anyin)
    for i in (0, 1):
        for j in (0, 1):
            for k in (0, 1):
                c = inside[i:nx - 1 + i, j:ny - 1 + j, k:nz - 1 + k]
                anyin |= c
                allin &= c
    cell = anyin & ~allin
    idx = np.full(cell.shape, -1, np.int64)
    ci = np.argwhere(cell)
    idx[cell] = np.arange(len(ci))
    acc, cnt = np.zeros((len(ci), 3)), np.zeros(len(ci))
    I, J, K = ci[:, 0], ci[:, 1], ci[:, 2]
    for a in ((0, 0, 0), (0, 1, 0), (0, 0, 1), (0, 1, 1), (1, 0, 0), (1, 1, 0), (1, 0, 1), (1, 1, 1)):
        for axis in range(3):
            if a[axis]:
                continue
            b = list(a)
            b[axis] = 1
            fa = field[I + a[0], J + a[1], K + a[2]]
            fb = field[I + b[0], J + b[1], K + b[2]]
            cross = (fa < 0) != (fb < 0)
            t = np.where(cross, fa / np.where(cross, fa - fb, 1), 0)
            p = np.stack([I + a[0] + t * (b[0] - a[0]), J + a[1] + t * (b[1] - a[1]), K + a[2] + t * (b[2] - a[2])], 1)
            acc[cross] += p[cross]
            cnt[cross] += 1
    verts = origin + acc / cnt[:, None] * h
    quads = []
    for axis in range(3):
        o1, o2 = [a for a in range(3) if a != axis]
        lo = [slice(None)] * 3
        hi = [slice(None)] * 3
        lo[axis], hi[axis] = slice(0, -1), slice(1, None)
        a0 = inside[tuple(lo)]
        e = np.argwhere(a0 != inside[tuple(hi)])
        ok = (e[:, o1] >= 1) & (e[:, o2] >= 1) & (e[:, o1] < field.shape[o1] - 1) & (e[:, o2] < field.shape[o2] - 1)
        e = e[ok]
        quad = []
        for d1, d2 in ((0, 0), (1, 0), (1, 1), (0, 1)):     # the four cells around the edge
            c = e.copy()
            c[:, o1] -= 1 - d1
            c[:, o2] -= 1 - d2
            quad.append(idx[c[:, 0], c[:, 1], c[:, 2]])
        quad = np.stack(quad, 1)
        # inside at the edge's lower end: the surface faces +axis; (o1, o2, axis) is left-handed for axis 1
        forward = a0[tuple(e.T)] if axis != 1 else ~a0[tuple(e.T)]
        quad[~forward] = quad[~forward][:, ::-1]
        quads.append(quad)
    quads = np.concatenate(quads)
    return verts, quads[(quads >= 0).all(1)]


def taubin(verts, quads, rounds=6, lam=0.5, mu=-0.53):
    """Smoothing that keeps the volume: each round shrinks towards the neighbours' mean, then inflates back."""
    e = np.concatenate([quads[:, [0, 1]], quads[:, [1, 2]], quads[:, [2, 3]], quads[:, [3, 0]]])
    e = np.concatenate([e, e[:, ::-1]])
    deg = np.maximum(np.bincount(e[:, 0], minlength=len(verts)), 1).astype(float)
    v = verts.copy()
    for _ in range(rounds):
        for f in (lam, mu):
            s = np.zeros_like(v)
            np.add.at(s, e[:, 0], v[e[:, 1]])
            v = v + f * (s / deg[:, None] - v)
    return v


def build(h=0.009, wrist_back=0.22):
    """(vertices, quads, 21 keypoints) of the hand in hand lengths, sampled every `h`. The forearm end is closed."""
    lo = np.array([-wrist_back - 0.02, -0.30, -0.36])
    hi = np.array([1.08, 0.40, 0.18])
    n = np.ceil((hi - lo) / h).astype(int) + 1
    grid = np.stack(np.meshgrid(*[lo[i] + np.arange(n[i]) * h for i in range(3)], indexing="ij"), -1).reshape(-1, 3)
    field = hand_sdf(grid, wrist_back).reshape(n)
    field[0] = np.abs(field[0]) + h                         # outside at the grid's first slice: a flat end on the wrist
    v, q = surface_nets(field, lo, h)
    return taubin(v, q), q, keypoints()
