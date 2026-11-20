"""Blender (5.x) script: look at a character's face, to fit glasses (and anything else worn on the face) to it. Runs headless, on the CPU.

    blender -b --factory-startup --python face_scan.py -- views MODEL.glb OUTDIR [--size 1024] [--samples 24]
    blender -b --factory-startup --python face_scan.py -- scan  MODEL.glb OUT.json [--views VIEWS.json --landmarks LANDMARKS.json | --eyes EYES.json]

`views` renders the head, neck and shoulders from the front, on white, the way npcgen/blender/face_views.py does for the head graft, and
writes OUTDIR/front.png and OUTDIR/views.json ({"centre": [x, z], "px": model units per pixel, "size": [w, h], "height"}). SDPose (ComfyUI)
finds the 68 face points in that picture (npcgen/headgraft.py face_points); npcgen/facescan.py hands them back here as LANDMARKS.json.

`scan` writes everything the fit needs, in the model's OWN space (glTF's: metres, +Y up, the face looking toward +Z; Blender's import turns
that into x right, y away from the front camera, z up, and this script turns it back):

    {"version": 1, "source": "sdpose" | "landmarks" | "estimate", "model": {"min", "max", "height"},
     "eyes": {"left": [x, y, z], "right": [x, y, z]},                 the middle of each eye (the mean of its six face points)
     "points": 68 x [x, y, z] or null, "scores": 68 x score,          the face points lifted onto the surface (SDPose only)
     "face_width": the distance across the face at the jaw's two ends (SDPose only),
     "grid": {"x0", "y0", "step", "nx", "ny", "z": base64 float32}}   the face's front surface: for each (x, y) the z of the first thing a
                                                                     ray from the front meets (NaN: nothing), rows going up from y0

With `--eyes` (the model's own landmarks in its own space: {"left": [x, y, z], "right": [x, y, z]}, which a designed body carries) nothing is detected or guessed:
the eyes are those, and only the surface is read. Without landmarks (`estimate`) the eyes are put where this library's characters have them, by proportion (0.911 of the height, 65 mm apart at 1.7 m,
on the middle of the head: the medians over the eleven faces SDPose found, 2026-10-06), and the surface is still read exactly. A failure ends with a FACE_ERROR line for the caller.
"""
import base64
import json
import os
import sys

import bpy
import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import face_views as fv  # noqa: E402  (its main() only runs when it is the script)

EYE_RIGHT, EYE_LEFT = range(36, 42), range(42, 48)       # the 68-point layout: the eye on the picture's left (the wearer's right), then the other
JAW_ENDS = (0, 16)
EYE_LEVEL = 0.911                                         # of the height: the median of eleven characters (0.881 to 0.925; a textbook adult's is 0.934)
EYE_APART = 0.065                                         # metres, at a height of 1.7 m (the median again)
REACH_X, BELOW, ABOVE, STEP = 0.16, 0.10, 0.09, 0.001     # the surface grid, in metres of a 1.7 m adult: half its width, below and above the eyes, the cell


def args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    opts, pos, i = {"size": "1024", "samples": "24"}, [], 0
    while i < len(argv):
        if argv[i].startswith("--"):
            opts[argv[i][2:]] = argv[i + 1]
            i += 2
        else:
            pos.append(argv[i])
            i += 1
    if len(pos) != 3 or pos[0] not in ("views", "scan"):
        raise SystemExit("usage: face_scan.py views MODEL.glb OUTDIR | face_scan.py scan MODEL.glb OUT.json [--views V.json --landmarks L.json]")
    opts["mode"], opts["model"], opts["out"] = pos
    return opts


def load(path):
    """The model's meshes in Blender: (world positions n x 3, triangles m x 3)."""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=path)
    for o in bpy.context.scene.objects:
        if o.type == "ARMATURE":
            o.data.pose_position = "REST"                       # the bind pose the vertices are in, not frame 1 of whichever clip came first
    bpy.context.view_layer.update()
    shapes = {b.custom_shape for o in bpy.context.scene.objects if o.type == "ARMATURE" for b in o.pose.bones if b.custom_shape}
    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH" and o not in shapes       # (the importer adds a 42-vertex sphere, 2 m wide, for the bones' display)
              and not any("not_exported" in c.name for c in o.users_collection)]
    if not meshes:
        raise SystemExit("No mesh in " + path)
    positions, triangles, base = [], [], 0
    for ob in meshes:
        mesh = ob.data
        mesh.calc_loop_triangles()
        co = np.empty(len(mesh.vertices) * 3)
        mesh.vertices.foreach_get("co", co)
        m = np.array(ob.matrix_world)                       # (the skin modifier does nothing at rest)
        positions.append(co.reshape(-1, 3) @ m[:3, :3].T + m[:3, 3])
        tri = np.empty(len(mesh.loop_triangles) * 3, dtype=np.int32)
        mesh.loop_triangles.foreach_get("vertices", tri)
        triangles.append(tri.reshape(-1, 3) + base)
        base += len(mesh.vertices)
    return np.concatenate(positions), np.concatenate(triangles), meshes


def to_gltf(p):
    """A point in Blender's import space as glTF's: (x, y, z) -> (x, z, -y)."""
    return [float(p[0]), float(p[2]), float(-p[1])]


def views(o):
    pos, _tri, _meshes = load(o["model"])
    lo, hi = pos.min(0), pos.max(0)
    height = float(hi[2] - lo[2])
    head = pos[pos[:, 2] > hi[2] - 0.17 * height]
    centre = (float(np.median(head[:, 0])), float(np.median(head[:, 1])), float(hi[2] - 0.09 * height))
    frame = 0.22 * height                                   # the head, the neck and the tops of the shoulders
    out = o["out"]
    os.makedirs(out, exist_ok=True)
    size = int(o["size"])
    fv.render(os.path.join(out, "front.png"), centre, frame, size, int(o["samples"]))
    info = {"centre": [centre[0], centre[2]], "px": frame / size, "size": [size, size], "height": height}
    with open(os.path.join(out, "views.json"), "w", encoding="utf-8") as fh:
        json.dump(info, fh)
    print("VIEWS " + json.dumps(info), flush=True)


def lift(tree, view, points):
    """Each face point's 3D point (Blender space): a ray from the front, along +y, through its pixel; None where it misses."""
    out = []
    for u, v, _score in points:
        x = view["centre"][0] + (u - view["size"][0] / 2) * view["px"]
        z = view["centre"][1] - (v - view["size"][1] / 2) * view["px"]
        loc, _n, _i, _d = tree.ray_cast(Vector((x, -1000.0, z)), Vector((0.0, 1.0, 0.0)))
        out.append(np.array(loc[:]) if loc is not None else None)
    return out


def mean_point(points, ids):
    found = [points[i] for i in ids if points[i] is not None]
    if len(found) < 4:
        return None
    return np.mean(found, axis=0)


def surface(tree, x0, y0, step, nx, ny, y_front):
    """The front surface on a grid in glTF space: z of the first hit of a ray from the front through each (x, y), NaN where there is none.
    x0, y0 and step are glTF's x and y, which are Blender's x and z."""
    grid = np.full((ny, nx), np.nan, np.float32)
    direction = Vector((0.0, 1.0, 0.0))
    for row in range(ny):
        z = y0 + (row + 0.5) * step
        for col in range(nx):
            loc, _n, _i, _d = tree.ray_cast(Vector((x0 + (col + 0.5) * step, y_front, z)), direction)
            if loc is not None:
                grid[row, col] = -loc[1]
    return grid


def scan(o):
    pos, tri, _meshes = load(o["model"])
    lo, hi = pos.min(0), pos.max(0)
    height = float(hi[2] - lo[2])
    unit = height / 1.7
    tree = BVHTree.FromPolygons(pos.tolist(), tri.tolist(), epsilon=0.0)
    source, points, scores, face_width, eyes = "estimate", None, None, None, None
    if "eyes" in o:                                         # the model's own landmarks (glTF space): no detector, no guess
        with open(o["eyes"], encoding="utf-8") as fh:
            given = json.load(fh)
        eyes = {side: np.array([given[side][0], -given[side][2], given[side][1]], float) for side in ("left", "right")}      # glTF (x, y, z) is Blender (x, -z, y)
        source = "landmarks"
    if eyes is None and "landmarks" in o and "views" in o:
        with open(o["views"], encoding="utf-8") as fh:
            view = json.load(fh)
        with open(o["landmarks"], encoding="utf-8") as fh:
            marks = json.load(fh)["points"]
        lifted = lift(tree, view, marks)
        right, left = mean_point(lifted, EYE_RIGHT), mean_point(lifted, EYE_LEFT)
        if right is not None and left is not None:
            if right[0] > left[0]:                          # the wearer's left is +x: the points 36-41 are on the picture's left, so normally the wearer's right
                right, left = left, right
            source, eyes = "sdpose", {"left": left, "right": right}
            points = [None if p is None else to_gltf(p) for p in lifted]
            scores = [float(m[2]) for m in marks]
            u0, u1 = marks[JAW_ENDS[0]][0], marks[JAW_ENDS[1]][0]
            face_width = float(abs(u1 - u0) * view["px"])
    if eyes is None:                                        # by proportion: on the middle of the head, at the usual eye level
        head = pos[pos[:, 2] > hi[2] - 0.10 * height]
        x_mid, y_mid = float(np.median(head[:, 0])), float(np.median(head[:, 1]))
        z_eye = float(lo[2] + EYE_LEVEL * height)
        half = EYE_APART * unit / 2
        eyes = {}
        for side, x in (("left", x_mid + half), ("right", x_mid - half)):
            loc, _n, _i, _d = tree.ray_cast(Vector((x, -1000.0, z_eye)), Vector((0.0, 1.0, 0.0)))
            eyes[side] = np.array(loc[:]) if loc is not None else np.array([x, y_mid, z_eye])
    mid = (eyes["left"] + eyes["right"]) / 2
    x0, y0 = float(mid[0] - REACH_X * unit), float(mid[2] - BELOW * unit)
    step = STEP * unit
    nx, ny = round(2 * REACH_X * unit / step), round((BELOW + ABOVE) * unit / step)             # (320 x 190 cells whatever the float rounding of the unit)
    grid = surface(tree, x0, y0, step, nx, ny, float(lo[1] - 10.0))
    info = {"version": 1, "source": source,
            "model": {"min": to_gltf((lo[0], hi[1], lo[2])), "max": to_gltf((hi[0], lo[1], hi[2])), "height": height,
                      "vertices": int(len(pos)), "triangles": int(len(tri))},
            "eyes": {k: to_gltf(v) for k, v in eyes.items()}, "points": points, "scores": scores, "face_width": face_width,
            "grid": {"x0": x0, "y0": y0, "step": step, "nx": nx, "ny": ny, "z": base64.b64encode(grid.astype("<f4").tobytes()).decode("ascii")}}
    with open(o["out"], "w", encoding="utf-8") as fh:
        json.dump(info, fh)
    print(f"SCAN {source} eyes {info['eyes']} hits {int(np.isfinite(grid).sum())}/{grid.size}", flush=True)


def main():
    o = args()
    (views if o["mode"] == "views" else scan)(o)


if __name__ == "__main__":
    try:
        main()
    except BaseException as exc:                             # one readable line for the caller, and a failing exit code
        if isinstance(exc, SystemExit) and exc.code in (None, 0):
            raise
        import traceback
        traceback.print_exc()
        message = exc.code if isinstance(exc, SystemExit) else f"{type(exc).__name__}: {exc}"
        print(f"FACE_ERROR {message}", flush=True)
        sys.stdout.flush()
        os._exit(1)
