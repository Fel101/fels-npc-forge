"""Blender (5.x) script: attach a detailed head to a whole-body model (the patch idea of npcgen/blender/graft_math.py).

    blender -b --factory-startup --python head_graft.py -- BODY.glb HEAD.glb OUT.glb --views VIEWS.json --landmarks LANDMARKS.json
                                                           [--report FILE] [--poster PNG] [--flat 10] ...

VIEWS.json is face_views.py's, LANDMARKS.json is SDPose's 68 face points on its two pictures (npcgen/headgraft.py). The steps:

  1. FIT. The face points are lifted onto both models with rays along +y, and the head is fitted on the body: scale, turn in the
     front plane and shift from the nose, eyes and mouth (graft_math.fit_face).
  2. PATCH. The head model's faces inside a face-shaped outline (from the chin to about the hairline, cheek to cheek, in front of the
     ears' plane), cut along a SMOOTH curve: where the head model's paint shows hair (darker, and often more saturated) in the
     outline's outer ring, the curve is pulled in to the hairline (in every direction the radius from which most of what lies
     further out is hair, then eroded and smoothed, so a lone strand or a brow does not count and the curve never goes out past the
     hair) and a few millimetres in (graft_math.polar_cut). Faces that stand off the head like plates or float away from the body are
     dropped, and the outline is opened (thin strips and stair-steps taken off it, never the lids and lips). What is left in one
     piece with the nose is the patch.
  3. RIM. Every patch vertex's distance along the surface from the patch's outer edge. The edge's vertices are carried to the body's
     outer surface (the nearest outward-facing triangle: the inner layers of the body's hollow head face inwards), smoothed along the
     edge; the other vertices follow their nearest edge vertex, fading over --band. Within --flat of the edge the patch lies ON the
     body, --lift above it. Only where the body stands more than --hidden (8 mm) in front of an edge vertex, a hair lock over the
     forehead, does the patch stay as it is and go under it: with a limit of 0.5 mm, the old one, 35-55% of the rim stayed under the
     body's coarse faces and its jagged edge was the outline (a rim 3-4 mm off the surface on average, 6-9 mm at the 90th percentile;
     now 1.5-1.9 and about 2).
  4. BACKING. The face seen from the front, its deepest surface within --env, its holes filled: a smooth surface just behind the
     patch. The body's vertices in the face's area (further than --flat from the edge, within --move of the backing, and in the middle
     of the face up to --glasses in front of it, where a mask's glasses and nose stand out) are laid on it, and its inward-facing
     faces (the hollow head's inner skin) go. Nothing of the mask stands in front of the patch, and a hole in the patch shows the
     body's own skin (npcgen/headgraft.py then paints that skin in the patch's colour).
  5. JOIN. One mesh, the body's material(s) and "npcforge_head", exported as GLB (npcgen/headgraft.py then matches the patch's
     colour to the body's along its edge and over the whole face).

The report has the fit, what was dropped, and the counts. A failure ends with a GRAFT_ERROR line for the caller.
"""
import json
import os
import sys
import time

import bmesh
import bpy
import numpy as np
from mathutils import Matrix, Vector
from mathutils.bvhtree import BVHTree

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import game_lod  # noqa: E402
import graft_math as gm  # noqa: E402

MATERIAL = "npcforge_head"
DEFAULTS = {          # millimetres are of a 1.7 m person
    "flat": 10.0,     # the patch lies on the body's surface this far in from its edge (and the body's face is left alone there)
    "band": 26.0,     # ...and eases to its own shape over this far
    "lift": 0.35,     # above the body where it lies on it
    "under": 10.0, "over": 14.0, "reach": 25.0,   # how far the body's outer surface is looked for from an edge vertex
    "rim_smooth": 60, "rim_clamp": 1.0, "edge_smooth": 8, "sink": 1.6, "sink_width": 4.0, "hidden": 8.0, "inner_cos": -0.15, "eye_reach": 0.24,
    "chin_margin": 2.0, "hair_margin": 2.5, "cut_window": 8, "cut_share": 0.5, "open": 3, "plate_cos": 0.3, "float": 16.0, "poke_edge": 0.8,
    "env": gm.BACKING["env"], "back": gm.BACKING["back"], "move": 30.0, "glasses": 75.0, "fill_rounds": gm.BACKING["fill_rounds"],
    "pixel": gm.BACKING["pixel"],
}


def args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    opts, pos = dict(DEFAULTS), []
    i = 0
    while i < len(argv):
        if argv[i].startswith("--"):
            key = argv[i][2:]
            opts[key] = float(argv[i + 1]) if key in DEFAULTS else argv[i + 1]
            i += 2
        else:
            pos.append(argv[i])
            i += 1
    if len(pos) != 3 or "views" not in opts or "landmarks" not in opts:
        raise SystemExit("usage: head_graft.py BODY.glb HEAD.glb OUT.glb --views VIEWS.json --landmarks LANDMARKS.json "
                         "[--report FILE] [--poster PNG]")
    opts["body"], opts["head"], opts["out"] = pos
    return opts


def meshes():
    return [o for o in bpy.context.scene.objects if o.type == "MESH"]


def one_object(objs):
    bpy.ops.object.select_all(action="DESELECT")
    for o in objs:
        o.select_set(True)
    bpy.context.view_layer.objects.active = objs[0]
    if len(objs) > 1:
        bpy.ops.object.join()
    ob = bpy.context.view_layer.objects.active
    bpy.ops.object.select_all(action="DESELECT")
    return ob


def positions(obj):
    co = np.empty(len(obj.data.vertices) * 3)
    obj.data.vertices.foreach_get("co", co)
    m = np.array(obj.matrix_world)
    return co.reshape(-1, 3) @ m[:3, :3].T + m[:3, 3]


def triangles(obj):
    obj.data.calc_loop_triangles()
    return np.array([t.vertices[:] for t in obj.data.loop_triangles], np.int64)


def tree_of(pos, tri):
    return BVHTree.FromPolygons([tuple(v) for v in pos], [tuple(t) for t in tri], epsilon=0.0)


def lift(obj, view, points):
    """The 3D point each face point looks at: a ray from the front, along +y, through its pixel (None where it misses)."""
    tree = tree_of(positions(obj), triangles(obj))
    out = []
    for u, v, _score in points:
        x = view["centre"][0] + (u - view["size"][0] / 2) * view["px"]
        z = view["centre"][1] - (v - view["size"][1] / 2) * view["px"]
        loc, _n, _i, _d = tree.ray_cast(Vector((x, -1000.0, z)), Vector((0.0, 1.0, 0.0)))
        out.append(np.array(loc[:]) if loc is not None else None)
    return out


def base_colour_image(obj):
    mat = obj.data.materials[0] if obj.data.materials else None
    if mat is None or not mat.use_nodes:
        return None
    bsdf = next((n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf is None or not bsdf.inputs["Base Color"].links:
        return None
    return getattr(bsdf.inputs["Base Color"].links[0].from_node, "image", None)


def face_colours(obj, image):
    """The paint at the middle of every face of `obj` (triangles): (n, 3) RGB, 0..1."""
    me = obj.data
    w, h = image.size
    pixels = np.empty(w * h * 4, np.float32)
    image.pixels.foreach_get(pixels)
    pixels = pixels.reshape(h, w, 4)[:, :, :3]
    uvs = np.empty(len(me.loops) * 2, np.float32)
    me.uv_layers.active.data.foreach_get("uv", uvs)
    uvs = uvs.reshape(-1, 2)
    start = np.empty(len(me.polygons), np.int32)
    me.polygons.foreach_get("loop_start", start)
    mid = np.stack([uvs[start + k] for k in range(3)]).mean(0)
    return pixels[np.clip((mid[:, 1] * h).astype(int), 0, h - 1) % h, np.clip((mid[:, 0] * w).astype(int), 0, w - 1) % w]


def component_of(seed, allowed, bm):
    """The faces of `allowed` joined to face `seed` through shared edges."""
    seen, stack, out = {seed}, [seed], []
    while stack:
        i = stack.pop()
        out.append(i)
        for e in bm.faces[i].edges:
            for g in e.link_faces:
                j = g.index
                if j in allowed and j not in seen:
                    seen.add(j)
                    stack.append(j)
    return set(out)


def save_image(path, rgb):
    """A debugging picture: `rgb` is (h, w, 3) floats 0..1, top row first."""
    h, w = rgb.shape[:2]
    img = bpy.data.images.new("debug", w, h)
    img.colorspace_settings.name = "Non-Color"
    px = np.ones((h, w, 4), np.float32)
    px[..., :3] = rgb[::-1]
    img.pixels.foreach_set(px.ravel())
    img.filepath_raw = path
    img.file_format = "PNG"
    img.save()
    bpy.data.images.remove(img)


def debug_faces(path, dx, dz, layers, curve, centre=(0.0, 0.45)):
    """--debug: the head model's faces seen from the front in units of u, by kind: `layers` = [(mask, (r, g, b)), ...] drawn in order;
    and the cut curve in cyan."""
    width, height, x0, x1, z0, z1 = 640, 700, -1.3, 1.3, -1.5, 2.2
    picture = np.full((height, width, 3), 0.1, np.float32)

    def cells(x, z):
        px = ((x - x0) / (x1 - x0) * width).astype(int)
        pz = ((z1 - z) / (z1 - z0) * height).astype(int)
        keep = (px >= 1) & (px < width - 1) & (pz >= 1) & (pz < height - 1)
        layer = np.zeros((height, width), bool)
        layer[pz[keep], px[keep]] = True
        grown = layer.copy()
        for a in (-1, 0, 1):
            for b in (-1, 0, 1):
                grown |= np.roll(layer, (a, b), (0, 1))
        return grown

    for mask, colour in layers:
        picture[cells(dx[mask], dz[mask])] = colour
    angle = np.linspace(-np.pi, np.pi, 1440, endpoint=False)
    middle = (np.arange(len(curve)) + 0.5) / len(curve) * 2 * np.pi - np.pi
    radius = np.interp(angle, middle, curve, period=2 * np.pi)
    picture[cells(centre[0] + radius * np.cos(angle), centre[1] + radius * np.sin(angle))] = (0.0, 0.9, 1.0)
    save_image(path, picture)


def main():
    o = args()
    started = time.time()
    report = {}
    views = json.load(open(o["views"]))
    found = json.load(open(o["landmarks"]))

    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=o["body"])
    body = one_object(meshes())
    before = set(meshes())
    bpy.ops.import_scene.gltf(filepath=o["head"], merge_vertices=True)
    head = one_object([m for m in meshes() if m not in before])
    for ob in (body, head):                                  # bake each object's transform into its vertices
        ob.select_set(True)
        bpy.context.view_layer.objects.active = ob
        bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
        ob.select_set(False)
    body_pos = positions(body)
    height = float(body_pos[:, 2].max() - body_pos[:, 2].min())
    mm = height / 1700.0                                      # a millimetre of a 1.7 m person, in model units
    report["body_height"] = height

    # ---- 1. fit ---------------------------------------------------------------------------------------------------------------------
    b_pts = lift(body, views["body"], found["body"]["points"])
    h_pts = lift(head, views["head"], found["head"]["points"])
    scale, R, t, fit = gm.fit_face(h_pts, b_pts, [p[2] for p in found["head"]["points"]], [p[2] for p in found["body"]["points"]])
    fit["residual_mm_mean"] = fit.pop("residual_mean") / mm
    fit["residual_mm_max"] = fit.pop("residual_max") / mm
    report["fit"] = fit
    M = np.eye(4)
    M[:3, :3] = scale * R
    M[:3, 3] = t
    head.data.transform(Matrix(M.tolist()))
    head.data.update()
    aligned = {i: scale * (R @ np.asarray(h_pts[i])) + t for i in range(68) if h_pts[i] is not None}
    if gm.NOSE_TIP not in aligned or gm.CHIN not in aligned:
        raise SystemExit("The nose tip or the chin was not found on the head")
    nose = aligned[gm.NOSE_TIP]
    chin_z = float(aligned[gm.CHIN][2])
    u = float(nose[2] - chin_z)                               # nose tip to chin's bottom: the unit the face is measured in
    if u <= 0:
        raise SystemExit("The head's face points are upside down")
    report["u_mm"] = u / mm
    head_c = np.array([nose[0], nose[1] + 0.9 * u, nose[2] + 0.2 * u])       # about the middle of the head: outward is away from it
    plane_y = nose[1] + 1.0 * u                                # the patch's coronal plane: about the ears'

    # the body's OUTWARD-facing surface (the inner layers of its hollow head face inwards)
    b_tri = triangles(body)
    tri_n = np.cross(body_pos[b_tri[:, 1]] - body_pos[b_tri[:, 0]], body_pos[b_tri[:, 2]] - body_pos[b_tri[:, 0]])
    tri_n /= np.maximum(np.linalg.norm(tri_n, axis=1, keepdims=True), 1e-20)
    tri_c = body_pos[b_tri].mean(1)
    outward_tri = ((tri_c - head_c) * tri_n).sum(1) > 0
    in_zone = (np.abs(tri_c[:, 0] - nose[0]) < 3 * u) & (tri_c[:, 2] > chin_z - 2 * u) & (tri_c[:, 2] < nose[2] + 3 * u)
    outer_tree = tree_of(body_pos, b_tri[outward_tri & in_zone])

    # ---- 2. the patch ---------------------------------------------------------------------------------------------------------------
    head.data.calc_loop_triangles()
    n_poly = len(head.data.polygons)
    centres = np.empty(n_poly * 3)
    head.data.polygons.foreach_get("center", centres)
    centres = centres.reshape(-1, 3)
    normals = np.empty(n_poly * 3)
    head.data.polygons.foreach_get("normal", normals)
    normals = normals.reshape(-1, 3)
    dx, dz = (centres[:, 0] - nose[0]) / u, (centres[:, 2] - nose[2]) / u
    radial = centres - head_c
    radial /= np.maximum(np.linalg.norm(radial, axis=1, keepdims=True), 1e-9)
    # the head model is a hollow shell (Pixal3D's surface voxels): an outer skin and an inner one a millimetre or three behind it, that
    # faces inwards and is joined to the outer one through the lids and nostrils. Only the outer skin is wanted.
    skin = (normals * radial).sum(1) > o["inner_cos"]
    for ids in (range(36, 42), range(42, 48)):                # ...except round the eyes: the lids' undersides and the eyeball's back are seen
        found = [aligned[i] for i in ids if i in aligned]
        if found:
            eye = np.mean(found, axis=0)
            skin |= np.hypot(centres[:, 0] - eye[0], centres[:, 2] - eye[2]) < o["eye_reach"] * u
    candidate = ((np.abs(dx) < gm.half_width(dz)) & (dz > (chin_z - nose[2]) / u - o["chin_margin"] * mm / u) & (dz < gm.TOP)
                 & (centres[:, 1] < plane_y) & skin)
    outer = candidate & ((dz > gm.OUTER_TOP) | (np.abs(dx) > gm.OUTER_SIDE))
    bm = bmesh.new()
    bm.from_mesh(head.data)
    bm.faces.ensure_lookup_table()
    bm.verts.ensure_lookup_table()

    hair, hair_trimmed = np.zeros(n_poly, bool), False
    image = base_colour_image(head)
    if image is not None and image.size[0] > 0 and outer.sum() > 200:
        rgb = face_colours(head, image)
        lum = rgb @ np.array([0.2126, 0.7152, 0.0722], np.float32)
        ids = np.nonzero(outer)[0]
        labels, hair_report = gm.hair_split(lum[ids], rgb[ids])
        report["hair_test"] = hair_report
        if labels is not None:
            hair[ids[labels]] = True
            hair_trimmed = True
    if not hair_trimmed:                                     # hair could not be told from skin: stop mid-forehead, not in the head's hair
        candidate &= dz < gm.TOP_UNTRIMMED
    inside, curve = gm.polar_cut(dx, dz, candidate, hair, window=int(o["cut_window"]), margin=o["hair_margin"] * mm / u,
                                 share=o["cut_share"])
    brow_ids = [i for i in range(17, 27) if i in aligned]                 # the upper lids and brows overhang: no plate is taken off there
    brow_top = max(float(aligned[i][2]) for i in brow_ids) if brow_ids else nose[2] + 0.9 * u
    plates = (inside & outer & (np.abs((normals * radial).sum(1)) < o["plate_cos"])   # plates standing off the head, not lying on it
              & (centres[:, 2] > brow_top + 0.1 * u))
    inside &= ~plates
    ring = np.nonzero(inside & outer)[0]
    away = np.array([(lambda r: r[3] if r[0] is not None else 9.0)(outer_tree.find_nearest(Vector(centres[i]))) for i in ring])
    floating = ring[away > o["float"] * mm]
    inside[floating] = False
    report["dropped"] = {"hair": int((candidate & ~inside & hair).sum()), "plates": int(plates.sum()), "floating": int(len(floating)),
                         "inner_skin": int((~skin & (np.abs(dx) < gm.half_width(dz)) & (centres[:, 1] < plane_y) & (dz < gm.TOP)
                                            & (dz > (chin_z - nose[2]) / u - o["chin_margin"] * mm / u)).sum()), "hair_trimmed": hair_trimmed}
    report["cut"] = {"hair_bins": curve["hair_bins"], "bins": int(len(curve["cut"])), "radius_min_u": float(curve["cut"].min()),
                     "radius_max_u": float(curve["cut"].max()), "faces_outside": int((candidate & ~inside).sum())}
    selected = set(np.nonzero(inside)[0].tolist())
    if not selected:
        raise SystemExit("No part of the head model lies in the face's outline: the head does not fit the body")
    nose_face = int(np.argmin(np.where(inside, np.linalg.norm(centres - nose, axis=1), 9.0)))
    patch = component_of(nose_face, selected, bm)
    if int(o["open"]) > 0:
        neighbours = {i: [g.index if g is not None else -1 for e in bm.faces[i].edges
                          for g in ([x for x in e.link_faces if x.index != i] or [None])] for i in patch}
        opened = gm.opening(patch, set(np.nonzero(outer)[0].tolist()), neighbours, int(o["open"]))
        patch = component_of(nose_face, opened, bm)
    report["patch_faces"] = len(patch)
    if o.get("debug"):
        os.makedirs(o["debug"], exist_ok=True)
        in_patch = np.zeros(n_poly, bool)
        in_patch[list(patch)] = True
        floated = np.zeros(n_poly, bool)
        floated[floating] = True
        debug_faces(os.path.join(o["debug"], "faces.png"), dx, dz,
                    [(candidate, (0.45, 0.45, 0.45)), (candidate & hair, (0.9, 0.2, 0.2)), (floated, (0.9, 0.2, 0.9)),
                     (plates, (0.95, 0.85, 0.1)), (in_patch, (0.2, 0.75, 0.3))], curve["cut"])
    if len(patch) < 2000:
        raise SystemExit("Only %d faces of the head model make the face's patch: the head does not fit the body" % len(patch))
    bmesh.ops.delete(bm, geom=[f for f in bm.faces if f.index not in patch], context="FACES")
    bmesh.ops.delete(bm, geom=[v for v in bm.verts if not v.link_faces], context="VERTS")
    bm.verts.ensure_lookup_table()
    bm.faces.ensure_lookup_table()
    bm.verts.index_update()
    bm.edges.index_update()

    # ---- 3. the rim ---------------------------------------------------------------------------------------------------------------
    P = np.array([v.co[:] for v in bm.verts])
    edges = np.array([[e.verts[0].index, e.verts[1].index] for e in bm.edges])
    boundary = np.array([[e.verts[0].index, e.verts[1].index] for e in bm.edges if e.is_boundary])
    outer_edge = gm.outer_loop(P, gm.boundary_loops(boundary))
    neighbours_on_edge = {int(i): [] for i in outer_edge}
    for a, b in boundary:
        if int(a) in neighbours_on_edge and int(b) in neighbours_on_edge:
            neighbours_on_edge[int(a)].append(int(b))
            neighbours_on_edge[int(b)].append(int(a))
    for _ in range(int(o["edge_smooth"])):                   # the cut's stair-steps: every edge vertex towards the middle of its two neighbours
        new = P.copy()
        for i, near in neighbours_on_edge.items():
            if len(near) == 2:
                new[i] = 0.5 * P[i] + 0.25 * (P[near[0]] + P[near[1]])
        P = new
    dist, origin = gm.surface_distance(P, edges, outer_edge)
    flat, band, on_top = o["flat"] * mm, o["band"] * mm, o["lift"] * mm
    bm.normal_update()
    out_n = np.array([v.normal[:] for v in bm.verts])
    out_n[((out_n * (P - head_c)).sum(1)) < 0] *= -1
    delta = np.zeros((len(P), 3))
    matched = np.zeros(len(P), bool)
    hidden = 0
    for i in outer_edge:                                     # each edge vertex to the body's outer surface, nearest outward-facing triangle
        loc, _n, _idx, d = outer_tree.find_nearest(Vector(P[i]))
        if loc is not None and d < o["reach"] * mm:
            matched[i] = True
            if float(np.dot(np.array(loc[:]) - P[i], out_n[i])) > o["hidden"] * mm:
                hidden += 1                                  # the body stands well in front of the patch here (a hair lock over the forehead): the patch
                continue                                     # keeps its own shape and goes under it, instead of being pulled out to it
            delta[i] = np.array(loc[:]) + out_n[i] * on_top - P[i]
    target = delta.copy()
    for _ in range(int(o["rim_smooth"])):                    # along the edge, so it cannot spike
        new = delta.copy()
        for i in outer_edge:
            near = [j for j in neighbours_on_edge[int(i)] if matched[j]]
            if near:
                new[i] = 0.5 * delta[i] + 0.5 * delta[near].mean(0) if matched[i] else delta[near].mean(0)
        delta = new
    off = delta - target                                     # ...but never far from the surface it is meant to lie on
    off_length = np.linalg.norm(off, axis=1)
    keep = matched & (off_length > o["rim_clamp"] * mm)
    delta[keep] = target[keep] + off[keep] * (o["rim_clamp"] * mm / off_length[keep])[:, None]
    w = gm.smoothstep(1.0 - (dist - flat) / (band - flat))
    eased = P + w[:, None] * delta[np.where(origin >= 0, origin, 0)]
    if o["sink"] > 0:                                        # the very edge dips under the body's surface: its rim is where the two cross, not a step
        dip = gm.smoothstep(1.0 - dist / (o["sink_width"] * mm))
        eased = eased - out_n * ((on_top + o["sink"] * mm) * dip)[:, None]
    moved = np.linalg.norm(eased - P, axis=1)
    report["eased"] = {"edge_vertices": int(len(outer_edge)), "matched": int(matched[outer_edge].sum()), "under_the_body": hidden,
                       "vertices": int((w > 0).sum()),
                       "max_move_mm": float(moved.max() / mm)}
    if o.get("debug"):                                        # how far the rim ends up from the body's surface
        tree_all = tree_of(body_pos, b_tri)
        gaps = {}
        for name, tree in (("outward", outer_tree), ("any", tree_all)):
            d = np.array([tree.find_nearest(Vector(eased[i]))[3] for i in outer_edge]) / mm
            gaps[name] = {"mean": float(d.mean()), "p90": float(np.percentile(d, 90)), "max": float(d.max())}
        report["rim_gap_mm"] = gaps
    for v, p in zip(bm.verts, eased):
        v.co = Vector(p)
    p_tri = np.array([[v.index for v in f.verts] for f in bm.faces], np.int64)

    # ---- 4. the backing -----------------------------------------------------------------------------------------------------------
    pixel = o["pixel"] * mm
    maps = gm.backing_maps(eased, p_tri, pixel, max(1, int(round(o["env"] * mm / pixel))), int(o["fill_rounds"]))
    lo_x, hi_z = maps["origin"]
    gw, gh = maps["size"]
    hull, filled = maps["hull"], maps["filled"]
    inside_mm = gm.chamfer_inside(hull) * pixel / mm
    bm_body = bmesh.new()
    bm_body.from_mesh(body.data)
    bm_body.verts.ensure_lookup_table()
    bm_body.faces.ensure_lookup_table()
    co = np.array([v.co[:] for v in bm_body.verts])
    px = np.floor((co[:, 0] - lo_x) / pixel).astype(int)
    py = np.floor((hi_z - co[:, 2]) / pixel).astype(int)
    framed = (px >= 0) & (px < gw) & (py >= 0) & (py < gh)
    px, py = np.clip(px, 0, gw - 1), np.clip(py, 0, gh - 1)
    backing = filled[py, px] + o["back"] * mm
    central = (np.abs(co[:, 0] - nose[0]) < 0.75 * u) & (co[:, 2] < nose[2] + 0.6 * u) & (co[:, 2] > chin_z)
    reach = np.where(central, o["glasses"] * mm, o["move"] * mm)
    in_front = backing - co[:, 1]                             # positive: in front of the backing (-y is the front)
    lay = (framed & hull[py, px] & np.isfinite(backing) & (inside_mm[py, px] > o["flat"]) & (in_front <= reach)
           & (-in_front <= o["move"] * mm) & (co[:, 1] < plane_y + 0.3 * u))
    # within --flat of the edge the patch lies on the body's surface and follows its nearest edge vertex, not the body's bumps: where
    # the body stands in front of the patch there it is pushed back to the backing (never brought forward: it is the patch's ground)
    poke = (framed & hull[py, px] & np.isfinite(backing) & (inside_mm[py, px] > o["poke_edge"]) & (inside_mm[py, px] <= o["flat"])
            & (in_front > 0) & (in_front <= reach) & (co[:, 1] < plane_y + 0.3 * u))
    new_co = co.copy()
    new_co[lay, 1] = backing[lay]
    new_co[poke, 1] = backing[poke]
    for v, c in zip(bm_body.verts, new_co):
        v.co = Vector(c)
    # the inner skin of the hollow head (faces that face inwards) in the same area
    bm_body.normal_update()
    tri_v = np.array([[v.index for v in f.verts] for f in bm_body.faces], np.int64)
    fn = np.array([f.normal[:] for f in bm_body.faces])
    fc = new_co[tri_v].mean(1)
    fx = np.clip(np.floor((fc[:, 0] - lo_x) / pixel).astype(int), 0, gw - 1)
    fy = np.clip(np.floor((hi_z - fc[:, 2]) / pixel).astype(int), 0, gh - 1)
    f_framed = (fc[:, 0] >= lo_x) & (fc[:, 0] <= lo_x + gw * pixel) & (fc[:, 2] <= hi_z) & (fc[:, 2] >= hi_z - gh * pixel)
    inner = (((fc - head_c) * fn).sum(1) < 0) & f_framed & hull[fy, fx] & (inside_mm[fy, fx] > o["flat"]) \
        & (np.abs(fc[:, 1] - filled[fy, fx]) <= 2 * o["move"] * mm) & (fc[:, 1] < plane_y + 0.3 * u)
    bmesh.ops.delete(bm_body, geom=[bm_body.faces[int(i)] for i in np.nonzero(inner)[0]], context="FACES")
    bmesh.ops.delete(bm_body, geom=[v for v in bm_body.verts if not v.link_faces], context="VERTS")
    bm_body.to_mesh(body.data)
    bm_body.free()
    body.data.update()
    report["body"] = {"vertices_laid_on_backing": int(lay.sum()), "poked_back": int(poke.sum()), "inner_faces_deleted": int(inner.sum()),
                      "faces": int(len(tri_v))}

    # ---- 5. join and export ----------------------------------------------------------------------------------------------------
    patch_mesh = bpy.data.meshes.new("head_patch")
    bm.to_mesh(patch_mesh)
    patch_mesh.update()
    for mat in head.data.materials:
        mat.name = MATERIAL
        patch_mesh.materials.append(mat)
    bm.free()
    patch_obj = bpy.data.objects.new("head_patch", patch_mesh)
    bpy.context.scene.collection.objects.link(patch_obj)
    bpy.data.objects.remove(head, do_unlink=True)
    bpy.ops.object.select_all(action="DESELECT")
    body.select_set(True)
    patch_obj.select_set(True)
    bpy.context.view_layer.objects.active = body
    bpy.ops.object.join()
    joined = bpy.context.view_layer.objects.active
    if o.get("poster"):
        game_lod.poster(joined, o["poster"])
    bpy.ops.object.select_all(action="DESELECT")
    joined.select_set(True)
    bpy.ops.export_scene.gltf(filepath=o["out"], export_format="GLB", use_selection=True, export_yup=True, export_tangents=True,
                              export_apply=True)
    report["faces_after"] = len(joined.data.polygons)
    report["seconds"] = round(time.time() - started, 1)
    if o.get("report"):
        with open(o["report"], "w") as fh:
            json.dump(report, fh)
    print("GRAFT_REPORT " + json.dumps(report), flush=True)


if __name__ == "__main__":
    try:
        main()
    except BaseException as exc:                             # one readable line for the caller, and a failing exit code
        if isinstance(exc, SystemExit) and exc.code in (None, 0):
            raise
        import traceback
        traceback.print_exc()
        message = exc.code if isinstance(exc, SystemExit) else f"{type(exc).__name__}: {exc}"
        print(f"GRAFT_ERROR {message}", flush=True)
        sys.stdout.flush()
        os._exit(1)
