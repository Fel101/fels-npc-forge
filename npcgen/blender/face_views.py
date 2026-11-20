"""Blender (5.x) script: two front views for the face detector, for attaching a detailed head to a whole-body model.

    blender -b --factory-startup --python face_views.py -- BODY.glb HEAD.glb OUTDIR [--size 1024] [--samples 24]

Two textured, orthographic front views on white: the top of the whole-body model (its head, neck and shoulders' tops) and the
head-only model. Writes OUTDIR/body_front.png, OUTDIR/head_front.png and OUTDIR/views.json:

    {"body": {"centre": [x, z], "px": model units per pixel, "size": [w, h]}, "head": {...}}

in each model's own frame after Blender's import (x right, y away from the front camera, z up). Pixel (u, v) looks at the point
x = centre_x + (u - w/2) * px, z = centre_z - (v - h/2) * px, along +y: npcgen/headgraft.py finds SDPose's face points in the two
pictures and head_graft.py lifts them onto the models with those rays.
"""
import json
import os
import pathlib
import sys

import bpy
import numpy as np
from mathutils import Vector


def args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    opts, pos = {"size": "1024", "samples": "24"}, []
    i = 0
    while i < len(argv):
        if argv[i].startswith("--"):
            opts[argv[i][2:]] = argv[i + 1]
            i += 2
        else:
            pos.append(argv[i])
            i += 1
    if len(pos) != 3:
        raise SystemExit("usage: face_views.py BODY.glb HEAD.glb OUTDIR [--size PX] [--samples N]")
    opts["body"], opts["head"], opts["out"] = pos
    return opts


def points(meshes):
    rows = []
    for o in meshes:
        co = np.empty(len(o.data.vertices) * 3)
        o.data.vertices.foreach_get("co", co)
        m = np.array(o.matrix_world)
        rows.append(co.reshape(-1, 3) @ m[:3, :3].T + m[:3, 3])
    return np.concatenate(rows)


def render(path, centre, frame, size, samples):
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = samples
    scene.cycles.use_denoising = False
    scene.view_settings.view_transform = "Standard"
    world = bpy.data.worlds.new("w")
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs["Color"].default_value = (1, 1, 1, 1)
    world.node_tree.nodes["Background"].inputs["Strength"].default_value = 1.0
    scene.world = world
    sun = bpy.data.objects.new("sun", bpy.data.lights.new("sun", "SUN"))
    sun.data.energy = 1.5
    sun.rotation_euler = (0.9, 0, -0.5)
    scene.collection.objects.link(sun)
    cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
    cam.data.type = "ORTHO"
    cam.data.ortho_scale = frame
    cam.data.clip_start, cam.data.clip_end = 0.001, 1000.0
    scene.collection.objects.link(cam)
    scene.camera = cam
    cam.location = Vector((centre[0], centre[1] - 50.0, centre[2]))
    cam.rotation_euler = (np.pi / 2, 0, 0)                   # looks along +y with z up
    scene.render.resolution_x = scene.render.resolution_y = size
    scene.render.filepath = str(path)
    bpy.ops.render.render(write_still=True)


def main():
    o = args()
    size, samples = int(o["size"]), int(o["samples"])
    out = pathlib.Path(o["out"])
    out.mkdir(parents=True, exist_ok=True)
    info = {}
    for name in ("body", "head"):
        bpy.ops.wm.read_factory_settings(use_empty=True)
        bpy.ops.import_scene.gltf(filepath=o[name])
        meshes = [ob for ob in bpy.context.scene.objects if ob.type == "MESH"]
        if not meshes:
            raise SystemExit(f"The {name} model has no mesh")
        pts = points(meshes)
        lo, hi = pts.min(0), pts.max(0)
        if name == "body":
            height = hi[2] - lo[2]
            head = pts[pts[:, 2] > hi[2] - 0.17 * height]
            centre = (float(np.median(head[:, 0])), float(np.median(head[:, 1])), float(hi[2] - 0.09 * height))
            frame = 0.22 * height                            # the head, the neck and the tops of the shoulders
        else:
            centre = tuple(float(v) for v in (lo + hi) / 2)
            frame = 1.08 * float(max(hi[0] - lo[0], hi[2] - lo[2]))
        render(out / f"{name}_front.png", centre, frame, size, samples)
        info[name] = {"centre": [centre[0], centre[2]], "px": frame / size, "size": [size, size]}
    (out / "views.json").write_text(json.dumps(info), "utf-8")
    print("VIEWS " + json.dumps(info), flush=True)


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
