"""Blender (5.x) script: replace a model's generated hands with clean five-finger ones (npcgen/blender/hand_shape.py).

    blender -b --factory-startup --python hands.py -- IN.glb OUT.glb --keys KEYS.json [--height 1.7] [--skin R,G,B]
                                                                       [--report FILE] [--poster PNG]

KEYS.json is SDPose's keypoints of the model's pose views (npcgen/posekeys.keypoints of npcgen/blender/pose_views.py
renders at the same --height), so the model is put in the same place as those renders (rig_humanoid.load's rule).

For each hand:
  1. Its frame from the keypoints (npcgen/blender/rig_keypoints.py): the wrist, the forearm's line from the elbow, the
     hand's line to its knuckles, and the thumb side (the index-to-little-finger knuckle line, turned so the thumb is on
     its side).
  2. The cut: the arm's narrowest cross-section from just above the wrist joint to a quarter of a hand past it, found on
     the mesh's edges. Searching past the wrist keeps it off a sleeve: Devi's sleeve is narrower above its own cuff, and
     a search further up cut into the sleeve and gave her a skin-coloured forearm.
  3. What hangs together with the cut past it (within 1.4 hand lengths of the wrist) is the old hand. It is kept aside
     for its paint and deleted. A hand welded to a skirt floods beyond that: then only what lies near the hand's line goes,
     and the holes it leaves in the skirt are closed.
  4. The new hand: its length from how far the old one reached, its wrist ring squeezed to the arm's cut, bent from the
     forearm's line to the hand's, remeshed on a voxel grid (a closed, clean surface) and simplified to about 3,000
     triangles, and cut a little past the arm's cut; a band of triangles joins the two rings.
  5. Its colour texture: the skin colour (--skin: the pictures' hands, npcgen/hands3d.picture_hands; without it, the old
     hand's paint, the median of the samples whose tint is typical), shaded by the hand's own ambient occlusion, with a
     lighter palm and nails when the colour looks like skin, and a bare arm's colour run a few centimetres past the join.
     The old paint itself is not copied: it is the pictures' fingers smeared over a mitten, and on Devi it came across as
     dark smudges on the new fingers.
The hands get a material of their own ("npcforge_hand", one 512 x 512 texture for both).

The report has, per hand, the 21 points of the new hand (SDPose's order, in the model's own frame) and its wrist ring,
for the rig (npcgen/blender/rig_humanoid.py places the finger bones on them and gives the new hand to the hand bone).
"""
import json
import math
import os
import sys
import time

import bmesh
import bpy
import numpy as np
from mathutils import Matrix, Vector
from mathutils.kdtree import KDTree

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import game_lod  # noqa: E402
import hand_shape as hs  # noqa: E402
import rig_keypoints as rk  # noqa: E402

MATERIAL = "npcforge_hand"
GAP = 0.03              # hand lengths between the arm's cut and the new hand's ring, bridged
SEARCH = (-0.05, 0.25)  # where the cut is looked for, in typical hand lengths along the forearm from the wrist joint
FURTHER = 0.5           # ...and on to here while the arm still narrows at SEARCH's end (a sleeve over the wrist)
BEND_LIMIT = 20.0       # degrees: a relaxed hand turns at most this far from the forearm's line
PALM_REACH = 0.22       # typical hand lengths: a welded hand is what lies this near its palm's bones...
FINGER_REACH = 0.14     # ...or this near its fingers'
LOOSE_FACES = 400       # pieces of mesh this small left near a hand after the old one goes are its leftovers
TRIANGLES = 3000        # per hand
TEXTURE = 512
TYPICAL = 0.105         # a hand's length over the body's height
THUMB_WEB = 0.30        # hand lengths past its wrist joint: the new hand is cut no further out than this. Its thumb joins
                        # the palm only behind about 0.37, and cuts past a sleeve over the wrist (Laura's at 0.33 and
                        # 0.39) left the thumbs as loose pieces, open at their base
ROUND_ARM = 120.0       # degrees: a piece of the arm's cut goes round the arm's line if it leaves no wider gap than this


def args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    opts, pos, i = {"height": "1.7"}, [], 0
    while i < len(argv):
        if argv[i].startswith("--"):
            opts[argv[i][2:]] = argv[i + 1]
            i += 2
        else:
            pos.append(argv[i])
            i += 1
    if len(pos) != 2 or not opts.get("keys"):
        raise SystemExit("usage: hands.py IN.glb OUT.glb --keys KEYS.json [--height H] [--skin R,G,B] [--report FILE] "
                         "[--poster PNG]")
    opts["in"], opts["out"] = pos
    return opts


def smoothstep(a, b, x):
    t = np.clip((x - a) / (b - a), 0, 1)
    return t * t * (3 - 2 * t)


def verts(ob):
    co = np.empty(len(ob.data.vertices) * 3)
    ob.data.vertices.foreach_get("co", co)
    return co.reshape(-1, 3)


def load(path, height):
    """The model as one mesh (seams merged, so the arm is one surface), scaled to `height` with its feet on the ground and
    centred, as rig_humanoid.load puts it. Returns (object, the matrix used)."""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=path, merge_vertices=True)
    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    if not meshes:
        raise SystemExit("The model has no mesh")
    bpy.ops.object.select_all(action="DESELECT")
    for o in meshes:
        o.select_set(True)
    bpy.context.view_layer.objects.active = meshes[0]
    if len(meshes) > 1:
        bpy.ops.object.join()
    ob = bpy.context.view_layer.objects.active
    for o in list(bpy.context.scene.objects):
        if o is not ob:
            bpy.data.objects.remove(o)
    ob.parent = None
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    co = verts(ob)
    lo, hi = co.min(0), co.max(0)
    s = height / (hi[2] - lo[2])
    normalise = (Matrix.Translation(Vector((-(lo[0] + hi[0]) / 2 * s, -(lo[1] + hi[1]) / 2 * s, -lo[2] * s)))
                 @ Matrix.Scale(s, 4))
    ob.data.transform(normalise)
    ob.data.update()
    return ob, normalise


def depth(co, x, z, r):
    """The middle of the mesh front to back around (x, z), as rig_humanoid.depth."""
    m = (np.abs(co[:, 0] - x) < r) & (np.abs(co[:, 2] - z) < r)
    if m.sum() < 8:
        return float(np.median(co[:, 1]))
    ys = co[m, 1]
    return float((np.percentile(ys, 5) + np.percentile(ys, 95)) / 2)


# ---- where the hand is ----------------------------------------------------------------------------------------------
def hand_frame(K, side):
    """The wrist W, the forearm's direction F (from the elbow), the hand's direction D (to its knuckles) and the thumb
    side R (unit, across the hand), with SDPose's scores."""
    W, sw = K.body(f"wrist_{side}", side)
    E, se = K.body(f"elbow_{side}", side)
    pts = K.hand(side)
    notes = []
    F = (W - E).normalized()
    D = (sum((pts[i][0] for i in (5, 9, 13, 17)), Vector()) / 4 - W).normalized()
    if D.angle(F) > math.radians(50):
        notes.append("the hand's own direction was unsure: the forearm's was used")
        D = F.copy()
    R = pts[5][0] - pts[17][0]
    R = (R - D * R.dot(D)).normalized()
    thumb = sum((pts[i][0] for i in (2, 3, 4)), Vector()) / 3 - W
    thumb -= D * thumb.dot(D)
    if thumb.length > 1e-6 and R.dot(thumb) < 0:
        notes.append("its index and little finger were the wrong way round (judged by the thumb)")
        R = -R
    if R.dot(Vector((0, 1, 0))) > 0.5:               # the model faces -Y: a thumb pointing back is a hand read backwards
        notes.append("its thumb pointed backwards: turned round")
        R = -R
    if D.angle(F) > math.radians(BEND_LIMIT):
        notes.append(f"its bend from the forearm ({math.degrees(D.angle(F)):.0f} degrees) was limited to {BEND_LIMIT:.0f}")
        D = F.slerp(D, BEND_LIMIT / math.degrees(D.angle(F))).normalized()
        R = (R - D * R.dot(D)).normalized()
    return {"side": side, "W": W, "F": F, "D": D, "R": R, "score": min(sw, se), "points": [p for p, _s in pts],
            "hand_score": float(np.mean([s for _p, s in pts])), "notes": notes}


def hull_area(pts):
    """The area of the convex hull of 2D points (monotone chain)."""
    p = sorted(set(map(tuple, np.round(pts, 7))))
    if len(p) < 3:
        return 0.0

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lower, upper = [], []
    for q in p:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], q) <= 0:
            lower.pop()
        lower.append(q)
    for q in reversed(p):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], q) <= 0:
            upper.pop()
        upper.append(q)
    h = lower[:-1] + upper[:-1]
    return 0.5 * abs(sum(h[i][0] * h[(i + 1) % len(h)][1] - h[(i + 1) % len(h)][0] * h[i][1] for i in range(len(h))))


def section(co, edges, Q, F, e1, e2, radius):
    """Where the mesh's edges cross the plane through Q across F, within `radius` of Q, in (e1, e2) coordinates."""
    d = (co - Q) @ F
    a, b = edges[:, 0], edges[:, 1]
    crossing = (d[a] > 0) != (d[b] > 0)
    a, b = a[crossing], b[crossing]
    t = d[a] / (d[a] - d[b])
    p = co[a] + (co[b] - co[a]) * t[:, None] - Q
    x, y = p @ e1, p @ e2
    keep = x * x + y * y < radius * radius
    return np.stack([x[keep], y[keep]], 1)


def find_cut(co, edges, fr, L0):
    """The arm's narrowest cross-section in SEARCH: its offset from the wrist joint (in typical hand lengths), centre,
    half-sizes across the hand and through it, and the section's axes."""
    W, F = np.array(fr["W"]), np.array(fr["F"])
    e1 = np.array(fr["R"]) - F * (np.array(fr["R"]) @ F)
    e1 /= np.linalg.norm(e1)
    e2 = np.cross(F, e1)
    rows = []
    for s in np.arange(SEARCH[0], FURTHER + 1e-9, 0.02):
        pts = section(co, edges, W + F * s * L0, F, e1, e2, 0.30 * L0)
        rows.append((float(s), hull_area(pts) if len(pts) >= 3 else 0.0, pts))
    areas = np.array([r[1] for r in rows])
    smooth = areas.copy()
    for i in range(1, len(areas) - 1):
        smooth[i] = np.median(areas[i - 1:i + 2])
    smooth[areas <= 0] = np.inf
    window = int(np.sum([r[0] <= SEARCH[1] + 1e-9 for r in rows]))
    if not np.isfinite(smooth[:window]).any():
        return None
    k = int(np.argmin(smooth[:window]))
    if k == window - 1:          # still narrowing at the window's end: Airi's coat sleeve covers her wrist
        while k + 1 < len(rows) and smooth[k + 1] <= smooth[k]:
            k += 1
    s, _area, pts = rows[k]
    mid, half = (pts.max(0) + pts.min(0)) / 2, (pts.max(0) - pts.min(0)) / 2
    return {"offset": s, "centre": W + F * s * L0 + e1 * mid[0] + e2 * mid[1], "half": (float(half[0]), float(half[1])),
            "e1": e1, "e2": e2}


# ---- taking the old hand off ----------------------------------------------------------------------------------------
def cut_arm(bm, fr, cut, L0, mark):
    """Bisect the arm at the cut and mark the old hand past it with `mark`. Returns (the vertices of the cut's loop,
    report, texture positions on the arm just above the cut)."""
    F, W, D = fr["F"], fr["W"], fr["D"]
    Q = Vector(cut["centre"])
    reach = 0.35 * L0
    near = [f for f in bm.faces if any((v.co - Q).length < reach for v in f.verts)]
    geom = list({e for f in near for e in f.edges}) + near + list({v for f in near for v in f.verts})
    bmesh.ops.bisect_plane(bm, geom=geom, dist=1e-7, plane_co=Q, plane_no=F)

    def past(f):
        return (f.calc_center_median() - Q).dot(F) > 0

    on_plane = [v for v in bm.verts if abs((v.co - Q).dot(F)) < 1e-6 and (v.co - Q).length < reach]
    region, stopped = set(), 0
    pieces = cut_pieces(on_plane, W + F * (Q - W).dot(F), F, cut["e1"])
    arm_found = any(round_arm for _piece, round_arm in pieces)
    for piece, round_arm in pieces:
        seeds = {f for v in piece for f in v.link_faces if past(f) and f not in region}
        got, todo, far = set(seeds), list(seeds), 0
        while todo:
            f = todo.pop()
            for e in f.edges:
                for g in e.link_faces:
                    if g in got or g in region or not past(g):
                        continue
                    if (g.calc_center_median() - W).length > 1.4 * L0:
                        far += 1
                        continue
                    got.add(g)
                    todo.append(g)
        if far and not round_arm and arm_found:     # a skirt or thigh beside the wrist, running on down the leg
            continue
        region |= got
        stopped += far
    welded = stopped > 0
    if welded:           # the hand runs into the clothes: take only what lies near its own bones
        pts = [np.array(p) for p in fr["points"]]
        bones = [(pts[0], pts[i], PALM_REACH) for i in (1, 5, 9, 13, 17)]
        bones += [(pts[i], pts[i + 1], FINGER_REACH) for k in range(5) for i in range(1 + 4 * k, 4 + 4 * k)]
        bones.append((np.array(Q), pts[0], PALM_REACH))

        def near_bones(f):
            c = np.array(f.calc_center_median())
            for a, b, reach in bones:
                ab = b - a
                t = np.clip((c - a) @ ab / max(float(ab @ ab), 1e-12), 0, 1)
                if np.linalg.norm(c - (a + t * ab)) < reach * L0:
                    return True
            return False
        region = {f for f in region if near_bones(f)}
    for f in region:
        f[mark] = 1
    wrist = Q - F * (Q - W).dot(F)                   # the wrist joint, on the line through the cut's centre
    reached = max(((v.co - wrist).dot(D) for f in region for v in f.verts), default=0.0)
    loop = [v for v in on_plane if any(f in region for f in v.link_faces) and any(f not in region for f in v.link_faces)]
    uv = bm.loops.layers.uv.active
    band = []
    if uv is not None:                               # the arm from 0.5 to 4.5 cm above the cut
        for f in near:
            if f.is_valid and f not in region:
                c = f.calc_center_median() - Q
                along = c.dot(F)
                if -0.25 * L0 < along < -0.03 * L0 and (c - F * along).length < 0.3 * L0:
                    band += [tuple(l[uv].uv) for l in f.loops]
    return loop, {"faces_removed": len(region), "reached": round(float(reached), 4), "welded": welded}, band


def cut_pieces(on_plane, axis, F, e1):
    """The pieces of the arm's cut (its vertices on the plane, joined by edges), each with whether it goes round the
    arm's line (`axis`: where it crosses the plane; no gap wider than ROUND_ARM), those that do first. The plane also
    cuts a thigh or skirt lying beside the wrist, along one side of the arm only: flooding on from such a piece runs down
    the leg, and a hand that only hung near the clothes was taken for one fused to them (Naomi's, Clara's: skirt cut out
    around them). A wide sleeve cut within reach only as arcs (Airi's coat) does not go round either, but ends near the
    wrist. A hand that is fused to the clothes joins them to the arm's own piece, which does go round. When no piece goes
    round (Briar's rags), the arm cannot be told from the clothes, and every piece counts."""
    e1 = Vector(tuple(e1))
    e2 = F.cross(e1)
    todo, out = set(on_plane), []
    while todo:
        v0 = todo.pop()
        piece, stack = [v0], [v0]
        while stack:
            v = stack.pop()
            for e in v.link_edges:
                w = e.other_vert(v)
                if w in todo:
                    todo.discard(w)
                    piece.append(w)
                    stack.append(w)
        th = sorted(math.atan2((v.co - axis).dot(e2), (v.co - axis).dot(e1)) for v in piece)
        gaps = [b - a for a, b in zip(th, th[1:])] + [th[0] + 2 * math.pi - th[-1]]
        out.append((piece, max(gaps) < math.radians(ROUND_ARM)))
    return sorted(out, key=lambda p: not p[1])


def boundary_loops(candidates):
    """Chains of open edges through the candidate vertices, each followed until it closes or leaves them."""
    cand = set(candidates)
    loops, seen = [], set()
    for v0 in cand:
        if v0 in seen:
            continue
        chain, v, prev = [v0], v0, None
        seen.add(v0)
        while True:
            nxt = next((e.other_vert(v) for e in v.link_edges if len(e.link_faces) == 1
                        and e.other_vert(v) is not prev and e.other_vert(v) in cand), None)
            if nxt is None or nxt is v0 or nxt in seen:
                break
            chain.append(nxt)
            seen.add(nxt)
            prev, v = v, nxt
        if len(chain) > 2:
            loops.append(chain)
    return loops


HOLE_SIDES = 150       # a hole a fused hand left has fewer sides than this; a hem or a sleeve's opening has more


def close_holes(bm, mark_verts, px=None, hand_slot=None):
    """Close the holes a fused hand left in the clothes, once the new hands are joined (so the arm's cut is no longer
    open): every open edge between `mark_verts` (the vertices of the faces that went) goes to Blender's hole filler,
    which finds the loops itself; loops of HOLE_SIDES or more are left open. Closing them before the join missed
    Naomi's, which run into the arm's cut. Each patch takes one texel beside it: of the texels round its border (px:
    the body's texture, texels(); not the new hands', material slot `hand_slot`), the one most like the others. The
    first one found was the arm's skin for Naomi's right hole, 40% of whose border is her arm: a skin-coloured patch
    in her black skirt."""
    uv = bm.loops.layers.uv.active
    closed = 0
    mine = set(mark_verts)
    edges = list({e for v in mine for e in v.link_edges if len(e.link_faces) == 1 and e.other_vert(v) in mine})
    for patch in [bmesh.ops.holes_fill(bm, edges=edges, sides=HOLE_SIDES)["faces"]] if edges else []:
        new = set(patch)
        for f in patch:
            closed += 1
            # the whole patch takes one texel beside it: the atlas between its corners is other parts of the model
            beside = [l for v in f.verts for l in v.link_loops if l.face not in new and l.face.material_index != hand_slot]
            if uv is None or not beside:
                continue
            pick = beside[0]
            if px is not None and len(beside) > 1:
                h, w = px.shape[:2]
                at = np.array([tuple(l[uv].uv) for l in beside])
                rgb = px[np.clip((at[:, 1] % 1) * h, 0, h - 1).astype(int), np.clip((at[:, 0] % 1) * w, 0, w - 1).astype(int)]
                pick = beside[int(np.argmin(np.linalg.norm(rgb[:, None] - rgb[None], axis=2).sum(1)))]
            f.material_index = pick.face.material_index
            for loop in f.loops:
                loop[uv].uv = pick[uv].uv
        bmesh.ops.triangulate(bm, faces=patch, quad_method="BEAUTY", ngon_method="BEAUTY")
    return closed


def drop_leftovers(bm, old_surface, reach):
    """Delete small pieces of mesh (fewer than LOOSE_FACES faces) lying wholly within `reach` of the old hands' surface
    (a KDTree of their face centres): bits of the old hand not joined to it, which it used to hide (floating specks by
    Airi's and Imani's new fingers). Returns how many faces went."""
    label, pieces = {}, []
    for f0 in bm.faces:
        if f0 in label:
            continue
        piece, todo = [f0], [f0]
        label[f0] = len(pieces)
        while todo:
            f = todo.pop()
            for e in f.edges:
                for g in e.link_faces:
                    if g not in label:
                        label[g] = label[f0]
                        piece.append(g)
                        todo.append(g)
        pieces.append(piece)
    gone = [f for piece in pieces if len(piece) < LOOSE_FACES
            and all(old_surface.find(g.calc_center_median())[2] < reach for g in piece) for f in piece]
    bmesh.ops.delete(bm, geom=gone, context="FACES")
    return len(gone)


def angles(points, centre, F, e1):
    e2 = F.cross(e1)
    return [math.atan2((p - centre).dot(e2), (p - centre).dot(e1)) for p in points]


def orient(loop, centre, F, e1):
    """The loop running anticlockwise about F, starting at its smallest angle."""
    th = angles([v.co for v in loop], centre, F, e1)
    n = len(loop)
    if sum(((th[(i + 1) % n] - th[i] + math.pi) % (2 * math.pi) - math.pi) for i in range(n)) < 0:
        loop, th = loop[::-1], th[::-1]
    k = int(np.argmin(th))
    return loop[k:] + loop[:k]


def zipper(A, B, centre, F, e1):
    """Triangles joining two anticlockwise loops (A the arm's, B the hand's), always advancing on the one behind."""
    ta = angles([v.co for v in A], centre, F, e1)
    tb = angles([v.co for v in B], centre, F, e1)
    k = int(np.argmin([abs((t - ta[0] + math.pi) % (2 * math.pi) - math.pi) for t in tb]))
    B, tb = B[k:] + B[:k], tb[k:] + tb[:k]

    def unwrap(t, start):
        out, last = [], start
        for x in t:
            last = last + ((x - last + math.pi) % (2 * math.pi) - math.pi)
            out.append(last)
        return out
    a = unwrap(ta, ta[0])
    b = unwrap(tb, ta[0] + ((tb[0] - ta[0] + math.pi) % (2 * math.pi) - math.pi))
    a.append(a[0] + 2 * math.pi)
    b.append(b[0] + 2 * math.pi)
    nA, nB = len(A), len(B)
    i = j = 0
    tris = []
    while i < nA or j < nB:
        if j >= nB or (i < nA and a[i + 1] <= b[j + 1]):
            tris.append((A[i % nA], A[(i + 1) % nA], B[j % nB]))
            i += 1
        else:
            tris.append((A[i % nA], B[(j + 1) % nB], B[j % nB]))
            j += 1
    return tris


# ---- the new hand ---------------------------------------------------------------------------------------------------
def build_hand(fr, cut, L, L0, name):
    """The new hand as an object (closed, simplified, its wrist cut off at the ring), its 21 points and its ring's
    centre. Its frame: X along the forearm, Y the thumb side, Z the back of the hand (mirrored for a left hand)."""
    F, D = np.array(fr["F"]), np.array(fr["D"])
    Y = np.array(cut["e1"])
    Z = np.cross(F, Y) * (1 if fr["side"] == "r" else -1)
    M = np.stack([F, Y, Z], 1)
    s0 = cut["offset"] * L0 / L                        # the cut, in this hand's lengths from the wrist joint
    out = max(0.0, s0 - THUMB_WEB)                     # cut past the thumb's web: the hand comes further out of the sleeve
    s0 -= out
    v, q, kp = hs.build(wrist_back=max(0.22, -s0 + 0.12))
    ring_x = s0 + GAP
    # squeeze the ring to the arm's cut (its half-sizes across and through, centred on it), fading out over 0.15
    band = np.abs(v[:, 0] - ring_x) < 0.01
    a_t, b_t = (v[band, 1].max() - v[band, 1].min()) / 2, (v[band, 2].max() - v[band, 2].min()) / 2
    yc, zc = (v[band, 1].max() + v[band, 1].min()) / 2, (v[band, 2].max() + v[band, 2].min()) / 2
    a_m, b_m = cut["half"][0] / L, cut["half"][1] / L
    sy, sz = float(np.clip(a_m / a_t, 0.75, 1.4)), float(np.clip(b_m / b_t, 0.75, 1.4))
    w = 1 - smoothstep(ring_x, ring_x + 0.15, v[:, 0])
    v = v.copy()
    v[:, 1] = (v[:, 1] - w * yc) * (1 + w * (sy - 1))
    v[:, 2] = (v[:, 2] - w * zc) * (1 + w * (sz - 1))
    # bend from the forearm's line to the hand's, just past the ring
    d = np.array([D @ F, D @ Y, D @ Z])
    axis = np.cross([1.0, 0, 0], d)
    bend = float(np.degrees(np.arcsin(min(1.0, np.linalg.norm(axis)))))
    if bend > 0.5:
        axis /= np.linalg.norm(axis)
        start = max(ring_x + 0.01, -0.05)

        def turn(p):
            t = np.round(smoothstep(start, start + 0.16, p[:, 0]), 3)
            out = p.copy()
            for level in np.unique(t[t > 0]):
                m = t == level
                out[m] = p[m] @ hs.rotation(axis, bend * level).T
            return out
        v, kp = turn(v), turn(kp)
    origin = np.array(cut["centre"]) - F * (cut["offset"] * L0 - out * L)   # the wrist joint, on the cut's centre line
    world, kp_world = origin + (v * L) @ M.T, origin + (kp * L) @ M.T
    me = bpy.data.meshes.new(name)
    me.from_pydata([tuple(p) for p in world], [], [tuple(f) for f in q])
    me.update()
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    bpy.ops.object.select_all(action="DESELECT")
    ob.select_set(True)
    bpy.context.view_layer.objects.active = ob
    rem = ob.modifiers.new("remesh", "REMESH")
    rem.mode, rem.voxel_size = "VOXEL", 0.008 * L
    bpy.ops.object.modifier_apply(modifier="remesh")
    bm = bmesh.new()
    bm.from_mesh(me)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    bmesh.ops.triangulate(bm, faces=bm.faces[:])
    bm.to_mesh(me)
    bm.free()
    dec = ob.modifiers.new("simplify", "DECIMATE")
    dec.ratio = min(1.0, TRIANGLES / max(1, len(me.polygons)))
    dec.use_collapse_triangulate = True
    bpy.ops.object.modifier_apply(modifier="simplify")
    ring = origin + F * ring_x * L                     # cut after simplifying, so the ring lies exactly on the plane
    bm = bmesh.new()
    bm.from_mesh(me)
    bmesh.ops.bisect_plane(bm, geom=bm.verts[:] + bm.edges[:] + bm.faces[:], dist=1e-7, plane_co=Vector(ring),
                           plane_no=Vector(F), clear_inner=True)
    bm.to_mesh(me)
    bm.free()
    for p in me.polygons:
        p.use_smooth = True
    return ob, kp_world, ring, {"ring_squeeze": [round(sy, 3), round(sz, 3)], "bend_degrees": round(bend, 1),
                                **({"moved_out": round(out * L, 4)} if out > 0 else {})}


# ---- its colour -----------------------------------------------------------------------------------------------------
def srgb_to_linear(c):
    c = np.asarray(c, float)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def linear_to_srgb(c):
    c = np.clip(np.asarray(c, float), 0, 1)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * c ** (1 / 2.4) - 0.055)


def image_of(ob):
    """The base colour image of an object's first material, or None."""
    mat = ob.data.materials[0] if ob.data.materials else None
    for node in (mat.node_tree.nodes if mat and mat.node_tree else []):
        if node.type == "BSDF_PRINCIPLED" and node.inputs["Base Color"].links:
            src = node.inputs["Base Color"].links[0].from_node
            return src.image if src.type == "TEX_IMAGE" else None
    return None


def texels(img):
    """An image's pixels as an (h, w, 3) array (row 0 at the bottom, as texture positions count), or None."""
    if img is None or not img.size[0]:
        return None
    w, h = img.size
    px = np.empty(w * h * img.channels, np.float32)
    img.pixels.foreach_get(px)
    return px.reshape(h, w, img.channels)[..., :3]


def typical_at(img, uv):
    """The typical colour (sRGB) of an image at texture positions uv (n x 2), or None when too few are dark enough to
    be paint."""
    if img is None or len(uv) < 20:
        return None
    w, h = img.size
    px = texels(img)
    uv = np.asarray(uv, float)
    rgb = px[np.clip((uv[:, 1] % 1) * h, 0, h - 1).astype(int), np.clip((uv[:, 0] % 1) * w, 0, w - 1).astype(int)]
    lum = rgb @ np.array([0.2126, 0.7152, 0.0722])
    ok = lum > 0.03
    if ok.sum() < 20:
        return None
    tint = rgb[ok] / lum[ok, None]
    typical = np.linalg.norm(tint - np.median(tint, 0), axis=1) < 0.25
    if not typical.any():
        typical[:] = True
    return np.median(rgb[ok][typical], 0)


def same_skin(a, b):
    """Two colours (sRGB) of the same skin: a similar tint, and neither more than about twice as bright."""
    if a is None or b is None or not (looks_like_skin(a) and looks_like_skin(b)):
        return False
    la, lb = (float(srgb_to_linear(c) @ np.array([0.2126, 0.7152, 0.0722])) for c in (a, b))
    tint = lambda c: srgb_to_linear(c) / max(float(srgb_to_linear(c) @ np.array([0.2126, 0.7152, 0.0722])), 1e-4)
    return float(np.linalg.norm(tint(a) - tint(b))) < 0.45 and 0.35 < la / max(lb, 1e-4) < 2.8


def old_colour(old):
    """The skin colour (sRGB) of the old hand's paint: the median of samples (corners and points inside its faces) whose
    tint is typical, so sleeve and trouser paint that landed on the mitten does not count."""
    img = image_of(old)
    if img is None or not old.data.uv_layers or not len(old.data.polygons):
        return np.array([0.62, 0.45, 0.36]), "default"
    w, h = img.size
    px = texels(img)
    uv = np.empty(len(old.data.loops) * 2)
    old.data.uv_layers.active.data.foreach_get("uv", uv)
    uv = uv.reshape(-1, 2)
    tri = np.array([list(p.loop_indices) for p in old.data.polygons if p.loop_total == 3])
    samples = [uv]
    for wts in ((1 / 3, 1 / 3, 1 / 3), (0.6, 0.2, 0.2), (0.2, 0.6, 0.2), (0.2, 0.2, 0.6)) if len(tri) else ():
        samples.append(uv[tri[:, 0]] * wts[0] + uv[tri[:, 1]] * wts[1] + uv[tri[:, 2]] * wts[2])
    uv = np.concatenate(samples)
    rgb = px[np.clip((uv[:, 1] % 1) * h, 0, h - 1).astype(int), np.clip((uv[:, 0] % 1) * w, 0, w - 1).astype(int)]
    lum = rgb @ np.array([0.2126, 0.7152, 0.0722])
    ok = lum > 0.03
    if ok.sum() < 20:
        return np.array([0.62, 0.45, 0.36]), "default"
    tint = rgb[ok] / lum[ok, None]
    typical = np.linalg.norm(tint - np.median(tint, 0), axis=1) < 0.25
    if not typical.any():
        typical[:] = True
    return np.median(rgb[ok][typical], 0), "old hand"


def looks_like_skin(c):
    """Red over green over blue, not grey: skin rather than a glove or a zombie's green."""
    r, g, b = c
    return r >= g >= b * 0.85 and 0.04 < r - b < 0.6


def bake(ob, kind, hide):
    """The object's own position or normal (the Geometry node, baked as emission) or its ambient occlusion, as a
    TEXTURE x TEXTURE x 3 float array."""
    img = bpy.data.images.new(f"_bake_{kind}", TEXTURE, TEXTURE, alpha=False, float_buffer=True)
    mat = bpy.data.materials.new(f"_bake_{kind}")
    mat.use_nodes = True
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    tex = nodes.new("ShaderNodeTexImage")
    tex.image = img
    nodes.active = tex
    if kind != "ao":
        geo, emit = nodes.new("ShaderNodeNewGeometry"), nodes.new("ShaderNodeEmission")
        links.new(geo.outputs["Position" if kind == "position" else "Normal"], emit.inputs["Color"])
        links.new(emit.outputs["Emission"], next(n for n in nodes if n.type == "OUTPUT_MATERIAL").inputs["Surface"])
    saved = list(ob.data.materials)
    ob.data.materials.clear()
    ob.data.materials.append(mat)
    for o in hide:
        o.hide_render = True
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = 64 if kind == "ao" else 1
    scene.render.bake.use_selected_to_active = False
    scene.render.bake.margin = 4
    bpy.ops.object.select_all(action="DESELECT")
    ob.select_set(True)
    bpy.context.view_layer.objects.active = ob
    bpy.ops.object.bake(type="AO" if kind == "ao" else "EMIT")
    for o in hide:
        o.hide_render = False
    ob.data.materials.clear()
    for m in saved:
        ob.data.materials.append(m)
    px = np.array(img.pixels[:], dtype=np.float32).reshape(TEXTURE, TEXTURE, 4)[..., :3]
    bpy.data.images.remove(img)
    bpy.data.materials.remove(mat)
    return px


def paint(hands, points, skin, hide, L0, wrists=None):
    """The hands' material: `skin` (sRGB) darkened in their own creases, lighter on the palms (more so on darker skin)
    and with nails, the last two only when it looks like skin. points: {side: 21 points} in the scene's frame. wrists:
    {side: (ring centre, forearm direction, the arm's colour just above the cut or None)}: a bare arm's own colour runs
    on past the join and fades into the hand's over 3 cm, so the join shows no step (Naomi's painted forearm is darker
    than her hands in the pictures)."""
    world = bpy.data.worlds.new("_bake_world")
    world.light_settings.distance = 0.10 * L0                # the creases between fingers, not the whole hand
    bpy.context.scene.world = world
    P = bake(hands, "position", hide).reshape(-1, 3)
    N = bake(hands, "normal", hide).reshape(-1, 3)
    ao = bake(hands, "ao", hide)[..., 0].reshape(-1)
    used = np.linalg.norm(N, axis=1) > 0.5                  # texels the hands' layout covers
    N = N / np.maximum(np.linalg.norm(N, axis=1, keepdims=True), 1e-9)
    base = np.tile(srgb_to_linear(skin), (len(P), 1))
    info = {"skin_srgb": [round(float(c), 3) for c in skin], "looks_like_skin": bool(looks_like_skin(skin))}
    for side, (centre, forearm, arm) in (wrists or {}).items():
        if same_skin(arm, skin):
            past = (P - np.asarray(centre)) @ np.asarray(forearm)
            mine = used & (np.linalg.norm(P - np.asarray(points[side][9]), axis=1) < 1.2 * L0)
            blend = (1 - smoothstep(0.0, 0.17 * L0, past)) * mine
            base += (srgb_to_linear(arm) - base) * blend[:, None]
            info.setdefault("arm_blend", {})[side] = [round(float(c), 3) for c in arm]
    if info["looks_like_skin"]:
        lum = float(srgb_to_linear(skin) @ np.array([0.2126, 0.7152, 0.0722]))
        lighter = float(np.clip(0.10 + 0.35 * (0.25 - lum) / 0.25, 0.08, 0.40))
        pale = srgb_to_linear(np.clip(skin + (np.array([0.93, 0.78, 0.72]) - skin) * lighter, 0, 1))
        nail_colour = srgb_to_linear(np.clip(skin * 0.55 + np.array([0.92, 0.76, 0.74]) * 0.45, 0, 1))
        for side, kp in points.items():
            kp = np.asarray(kp, float)
            mine = used & (np.linalg.norm(P - kp[9], axis=1) < 1.2 * L0)
            across = (kp[5] - kp[17]) / np.linalg.norm(kp[5] - kp[17])
            along = kp[9] - kp[0]
            back = np.cross(along, across) if side == "r" else np.cross(across, along)
            back /= np.linalg.norm(back)
            palm = np.clip((N @ -back - 0.2) / 0.5, 0, 1) * mine
            base += (pale - base) * palm[:, None]
            for k in range(5):
                a, b = kp[1 + 4 * k + 2], kp[1 + 4 * k + 3]        # the last bone: to the tip
                seg = b - a
                length = float(np.linalg.norm(seg))
                d = seg / length
                if k == 0:                                         # the thumb's nail faces away from the palm
                    up = (kp[2] - kp[9]) / np.linalg.norm(kp[2] - kp[9]) * 0.7 + back * 0.3
                else:
                    up = back.copy()
                up -= d * (up @ d)
                up /= np.linalg.norm(up)
                t = (P - a) @ d / length
                close = np.linalg.norm(P - (a + np.outer(np.clip(t, 0, 1), seg)), axis=1) < 0.09 * L0
                nail = (smoothstep(0.30, 0.45, t) * (1 - smoothstep(1.02, 1.12, t)) * smoothstep(0.50, 0.72, N @ up)
                        * close * mine)
                base += (nail_colour - base) * (nail * 0.85)[:, None]
        info["palm_lighter"] = round(lighter, 3)
    colour = linear_to_srgb(base * (0.70 + 0.30 * np.clip(ao, 0, 1))[:, None])
    colour[~used] = skin
    pixels = np.ones((TEXTURE * TEXTURE, 4), np.float32)
    pixels[:, :3] = colour
    img = bpy.data.images.new(MATERIAL, TEXTURE, TEXTURE, alpha=False)
    img.pixels[:] = pixels.ravel()
    img.pack()
    mat = bpy.data.materials.new(MATERIAL)
    mat.use_nodes = True
    tex = mat.node_tree.nodes.new("ShaderNodeTexImage")
    tex.image = img
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    mat.node_tree.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
    bsdf.inputs["Roughness"].default_value = 0.6
    hands.data.materials.clear()
    hands.data.materials.append(mat)
    return mat, info


# ---- all together ---------------------------------------------------------------------------------------------------
def replace(body, keys, H, skin=None):
    """Replace both hands of `body` (in the keypoints' frame). skin: the hands' colour (sRGB, 0-1) as the turnaround
    pictures show them (npcgen/hands3d.picture_hands), else it comes from the old hand's paint. Returns the report (points
    in this frame)."""
    co = verts(body)
    e = np.empty(len(body.data.edges) * 2, np.int64)
    body.data.edges.foreach_get("vertices", e)
    edges = e.reshape(-1, 2)
    K = rk.Keypoints(keys, body, co, lambda x, z, r: depth(co, x, z, r))
    L0 = TYPICAL * H
    report = {}
    frames, cuts = {}, {}
    for side in "lr":
        fr = frames[side] = hand_frame(K, side)
        if fr["score"] < rk.MIN_SCORE:
            raise SystemExit(f"SDPose was unsure where the {'left' if side == 'l' else 'right'} wrist and elbow are "
                             f"(score {fr['score']:.2f})")
        cuts[side] = find_cut(co, edges, fr, L0)
        if cuts[side] is None:
            raise SystemExit(f"No arm was found at the {'left' if side == 'l' else 'right'} wrist")
        report[side] = {"notes": fr["notes"], "hand_score": round(fr["hand_score"], 2), "cut_offset": round(cuts[side]["offset"], 2)}
    bm = bmesh.new()
    bm.from_mesh(body.data)
    mark = bm.faces.layers.int.new("npcforge_old_hand")
    loops, bands, touched = {}, {}, []
    for side in "lr":
        loop, rep, bands[side] = cut_arm(bm, frames[side], cuts[side], L0, mark)
        loops[side] = [v.co.copy() for v in loop]
        report[side].update(rep)
    bm.to_mesh(body.data)
    bm.free()
    old = body.copy()                                   # the old hands, for their paint
    old.data = body.data.copy()
    bpy.context.scene.collection.objects.link(old)
    for ob, keep in ((old, 1), (body, 0)):
        bm = bmesh.new()
        bm.from_mesh(ob.data)
        layer = bm.faces.layers.int["npcforge_old_hand"]
        gone = [f for f in bm.faces if f[layer] != keep]
        edge_verts = {v for f in gone for v in f.verts}
        bmesh.ops.delete(bm, geom=gone, context="FACES")
        if ob is old:                                  # where the old hands were, for their leftovers below
            old_surface = KDTree(max(1, len(bm.faces)))
            for i, f in enumerate(bm.faces):
                old_surface.insert(f.calc_center_median(), i)
            old_surface.balance()
        if ob is body and any(report[s]["welded"] for s in "lr"):
            touched = [v.co.copy() for v in edge_verts if v.is_valid]     # closed once the new hands are joined
        if ob is body:
            report["leftover_faces_removed"] = drop_leftovers(bm, old_surface, 0.12 * L0)
        bm.faces.layers.int.remove(layer)
        bm.to_mesh(ob.data)
        bm.free()
    hands, rings = [], {}
    for side in "lr":
        # a curled mitten reaches less than a relaxed hand: a typical length unless the old hand reached further
        L = float(np.clip(max(0.100 * H, report[side]["reached"] / hs.REACH), 0.085 * H, 0.118 * H))
        ob, kp, ring, fit = build_hand(frames[side], cuts[side], L, L0, f"hand_{side}")
        report[side].update({"hand_length": round(L, 4), "points": kp.tolist(), **fit})
        if fit.get("moved_out"):
            report[side]["notes"].append(f"its arm was cut past the thumb's root (a sleeve over the wrist): the hand was "
                                         f"moved {fit['moved_out'] * 100:.1f} cm further out, to keep its thumb whole")
        rings[side] = ring
        hands.append(ob)
    bpy.ops.object.select_all(action="DESELECT")
    for ob in hands:
        ob.select_set(True)
    bpy.context.view_layer.objects.active = hands[0]
    bpy.ops.object.join()
    both = bpy.context.view_layer.objects.active
    both.data.uv_layers.new(name=body.data.uv_layers.active.name if body.data.uv_layers else "UVMap")
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.smart_project(angle_limit=math.radians(66), island_margin=0.02)
    bpy.ops.object.mode_set(mode="OBJECT")
    if skin is not None:
        skin, source = np.asarray(skin, float), "pictures"
    else:
        skin, source = old_colour(old)
    img = image_of(body)
    wrists = {s: (rings[s], np.array(frames[s]["F"]), typical_at(img, bands[s])) for s in "lr"}
    mat, colour = paint(both, {s: report[s]["points"] for s in "lr"}, skin, [body, old], L0, wrists)
    report["colour"] = {"from": source, **colour}
    bpy.data.objects.remove(old)
    body.data.materials.append(mat)
    slot = len(body.data.materials) - 1
    bpy.ops.object.select_all(action="DESELECT")
    both.select_set(True)
    body.select_set(True)
    bpy.context.view_layer.objects.active = body
    bpy.ops.object.join()
    bm = bmesh.new()
    bm.from_mesh(body.data)
    bm.verts.ensure_lookup_table()
    uv = bm.loops.layers.uv.active
    kd = KDTree(len(bm.verts))
    for v in bm.verts:
        kd.insert(v.co, v.index)
    kd.balance()
    touched = [bm.verts[kd.find(c)[1]] for c in touched]
    for side in "lr":
        F, e1 = frames[side]["F"], Vector(cuts[side]["e1"])
        arm = [bm.verts[kd.find(c)[1]] for c in loops[side]]
        arm_loops = boundary_loops([v for v in arm if any(len(e.link_faces) == 1 for e in v.link_edges)])
        ring_pt = Vector(rings[side])
        ring = [v for v in bm.verts if abs((v.co - ring_pt).dot(F)) < 1e-5 and (v.co - ring_pt).length < 0.5 * L0
                and any(len(e.link_faces) == 1 for e in v.link_edges) and all(f.material_index == slot for f in v.link_faces)]
        hand_loops = boundary_loops(ring)
        if not arm_loops or not hand_loops:
            raise SystemExit(f"The new {'left' if side == 'l' else 'right'} hand could not be joined to the arm")
        if sum(len(loop) >= 6 for loop in hand_loops) > 1:          # its wrist cut through the thumb (THUMB_WEB)
            raise SystemExit(f"The new {'left' if side == 'l' else 'right'} hand came apart at its wrist")
        A, B = max(arm_loops, key=len), max(hand_loops, key=len)
        centre = sum((v.co for v in A), Vector()) / len(A)
        A, B = orient(A, centre, F, e1), orient(B, centre, F, e1)
        ring_uv = {v: next(l[uv].uv.copy() for l in v.link_loops) for v in B}
        made = 0
        for tri in zipper(A, B, centre, F, e1):
            try:
                f = bm.faces.new(tri)
            except ValueError:                           # already there
                continue
            f.material_index, f.smooth = slot, True
            anchor = next(v for v in tri if v in ring_uv)
            for loop in f.loops:
                loop[uv].uv = ring_uv.get(loop.vert, ring_uv[anchor])
            made += 1
        report[side].update({"bridge_faces": made, "arm_loop": len(A), "hand_ring": len(B),
                             "ring": {"centre": list(ring_pt), "normal": list(F)}})
    if touched:
        report["holes_closed"] = close_holes(bm, touched, texels(img), slot)
    bm.to_mesh(body.data)
    bm.free()
    return report


def main():
    o = args()
    started = time.time()
    H = float(o["height"])
    body, normalise = load(o["in"], H)
    with open(o["keys"], encoding="utf-8") as fh:
        keys = json.load(fh)
    skin = [float(c) for c in o["skin"].split(",")] if o.get("skin") else None
    report = replace(body, keys, H, skin)
    back = normalise.inverted()
    body.data.transform(back)
    body.data.update()
    bpy.context.view_layer.update()                     # the poster frames the model by its (refreshed) bounding box
    for side in "lr":                                    # the points, rings and lengths in the model's own frame
        r = report[side]
        r["points"] = [[round(c, 5) for c in back @ Vector(p)] for p in r["points"]]
        for key in ("hand_length", "reached", "moved_out"):
            if key in r:
                r[key] = round(r[key] * back.to_scale()[0], 5)
        r["ring"] = {"centre": [round(c, 5) for c in back @ Vector(r["ring"]["centre"])],
                     "normal": [round(c, 5) for c in (back.to_3x3() @ Vector(r["ring"]["normal"])).normalized()]}
    if o.get("poster"):
        game_lod.poster(body, o["poster"])
    bpy.ops.object.select_all(action="DESELECT")
    body.select_set(True)
    bpy.context.view_layer.objects.active = body
    bpy.ops.export_scene.gltf(filepath=o["out"], export_format="GLB", use_selection=True, export_yup=True,
                              export_tangents=True, export_apply=True)
    report.update({"faces": len(body.data.polygons), "seconds": round(time.time() - started, 1)})
    if o.get("report"):
        with open(o["report"], "w", encoding="utf-8") as fh:
            json.dump(report, fh)
    print("HANDS_REPORT " + json.dumps({k: ({kk: vv for kk, vv in v.items() if kk != "points"} if isinstance(v, dict) else v)
                                        for k, v in report.items()}), flush=True)


if __name__ == "__main__":
    try:
        main()
    except BaseException as exc:                   # one readable line for the caller, and a failing exit code
        if isinstance(exc, SystemExit) and exc.code in (None, 0):
            raise
        import traceback
        traceback.print_exc()
        message = exc.code if isinstance(exc, SystemExit) else f"{type(exc).__name__}: {exc}"
        print(f"HANDS_ERROR {message}", flush=True)
        sys.stdout.flush()
        os._exit(1)
