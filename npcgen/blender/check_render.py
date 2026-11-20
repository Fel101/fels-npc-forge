"""Blender (5.x) script: lit front renders of 3D models for the quality check (npcgen/check3d.py).

    blender -b --factory-startup --python check_render.py -- OUTDIR NAME=IN.glb [NAME=IN.glb ...]

For each model it writes OUTDIR/NAME_body.png (the whole figure, 384 x 768) and OUTDIR/NAME_face.png (the head, 512 x 512),
with the same orthographic front camera, light and grey background, so two models can be set side by side. Runs on the
CPU (Cycles); no GPU context is needed.
"""
import math
import os
import sys

import bpy
from mathutils import Vector

HEAD_CENTRE = 0.905      # of the height, from the feet: where the face shot is centred
HEAD_SPAN = 0.17         # of the height: how much the face shot takes in


def render(name, path, outdir):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=path)
    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    if not meshes:
        raise SystemExit(f"{name}: the model has no mesh")
    corners = [o.matrix_world @ Vector(c) for o in meshes for c in o.bound_box]
    lo = Vector([min(c[i] for c in corners) for i in range(3)])
    hi = Vector([max(c[i] for c in corners) for i in range(3)])
    centre, size = (lo + hi) / 2, hi.z - lo.z
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = 16
    scene.cycles.use_denoising = False
    scene.view_settings.view_transform = "Standard"
    world = bpy.data.worlds.new("w")
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs["Color"].default_value = (0.85, 0.85, 0.85, 1)
    world.node_tree.nodes["Background"].inputs["Strength"].default_value = 0.8
    scene.world = world
    sun = bpy.data.objects.new("sun", bpy.data.lights.new("sun", "SUN"))
    sun.data.energy = 2.5
    sun.rotation_euler = (math.radians(50), 0, math.radians(-30))
    scene.collection.objects.link(sun)
    cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
    cam.data.type = "ORTHO"
    scene.collection.objects.link(cam)
    scene.camera = cam
    for shot in ("body", "face"):
        if shot == "face":
            target = Vector((centre.x, centre.y, lo.z + size * HEAD_CENTRE))
            cam.data.ortho_scale = size * HEAD_SPAN
            scene.render.resolution_x = scene.render.resolution_y = 512
        else:
            target = centre
            cam.data.ortho_scale = size * 1.05
            scene.render.resolution_x, scene.render.resolution_y = 384, 768
        cam.location = target + Vector((0, -1, 0)) * size * 4       # the model faces -Y after import
        cam.rotation_euler = (target - cam.location).to_track_quat("-Z", "Y").to_euler()
        scene.render.filepath = os.path.join(outdir, f"{name}_{shot}.png")
        bpy.ops.render.render(write_still=True)


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    if len(argv) < 2:
        raise SystemExit("usage: check_render.py -- OUTDIR NAME=IN.glb [NAME=IN.glb ...]")
    for job in argv[1:]:
        name, path = job.split("=", 1)
        render(name, path, argv[0])
    print("CHECK_RENDERS done", flush=True)


if __name__ == "__main__":
    try:
        main()
    except BaseException as exc:                   # one readable line for the caller, and a failing exit code
        if isinstance(exc, SystemExit) and exc.code in (None, 0):
            raise
        import traceback
        traceback.print_exc()
        message = exc.code if isinstance(exc, SystemExit) else f"{type(exc).__name__}: {exc}"
        print(f"CHECK_ERROR {message}", flush=True)
        sys.stdout.flush()
        os._exit(1)
