"""Render a model for the pose detector. Runs inside Blender, on the CPU:

    blender -b --factory-startup --python pose_views.py -- IN.glb OUT_DIR [--height 1.7] [--size 2048]

Puts the model where rig_humanoid.py puts it (the same `load`, so the two line up exactly) and renders it textured from
the front, its left and its right: orthographic, 3:4 (SDPose's own input is 768 x 1024, so nothing is stretched), on a
light grey background. Writes OUT_DIR/front.png, left.png, right.png and cams.json, the camera of each view: a pixel
(u, v) is the point centre + (u - w/2) * px * right + (h/2 - v) * px * up (npcgen/blender/rig_keypoints.py, View).

2048 pixels high puts a hand at about 120 pixels: SDPose then traced every finger joint from the side views on the five
models measured (2026-09-26). Three renders take about 10 s.
"""
import json
import math
import os
import sys

import bpy
from mathutils import Vector

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rig_humanoid as rh  # noqa: E402

VIEWS = (("front", Vector((0, 1, 0))), ("left", Vector((-1, 0, 0))), ("right", Vector((1, 0, 0))))   # direction looked in


def main():
    argv = sys.argv[sys.argv.index("--") + 1:]
    opts, pos, i = {"height": "1.7", "size": "2048"}, [], 0
    while i < len(argv):
        if argv[i].startswith("--"):
            opts[argv[i][2:]] = argv[i + 1]
            i += 2
        else:
            pos.append(argv[i])
            i += 1
    src, out_dir = pos[:2]
    height, size_h = float(opts["height"]), int(opts["size"])
    size_w = size_h * 3 // 4
    os.makedirs(out_dir, exist_ok=True)
    body, _ = rh.load(src, height)
    co = rh.verts(body)
    lo, hi = co.min(0), co.max(0)
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"                  # Workbench and EEVEE cannot open an OpenGL context headless here
    scene.cycles.device = "CPU"
    scene.cycles.samples = 16
    scene.cycles.use_denoising = False
    scene.cycles.max_bounces = 2
    scene.view_settings.view_transform = "Standard"
    scene.render.resolution_x, scene.render.resolution_y = size_w, size_h
    world = bpy.data.worlds.new("w")
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs["Color"].default_value = (0.9, 0.9, 0.9, 1)
    world.node_tree.nodes["Background"].inputs["Strength"].default_value = 1.0
    scene.world = world
    sun = bpy.data.objects.new("sun", bpy.data.lights.new("sun", "SUN"))
    sun.data.energy = 2.0
    sun.rotation_euler = (math.radians(40), 0, math.radians(-20))
    scene.collection.objects.link(sun)
    cam_data = bpy.data.cameras.new("cam")
    cam_data.type = "ORTHO"
    cam_data.sensor_fit = "VERTICAL"
    cam = bpy.data.objects.new("cam", cam_data)
    scene.collection.objects.link(cam)
    scene.camera = cam
    centre = Vector((0, 0, (lo[2] + hi[2]) / 2))
    cams = {}
    for name, forward in VIEWS:
        up = Vector((0, 0, 1))
        right = forward.cross(up)
        across = max(abs((Vector(c) - centre).dot(right)) for c in (lo, hi)) * 2
        frame_h = max(hi[2] - lo[2], across * 4 / 3) * 1.06
        cam_data.ortho_scale = frame_h
        cam.location = centre - forward * 5.0
        cam.rotation_euler = forward.to_track_quat("-Z", "Y").to_euler()
        scene.render.filepath = os.path.join(out_dir, f"{name}.png")
        bpy.ops.render.render(write_still=True)
        cams[name] = {"file": f"{name}.png", "size": [size_w, size_h], "px": frame_h / size_h, "centre": list(centre),
                      "right": list(right), "up": list(up), "forward": list(forward)}
    with open(os.path.join(out_dir, "cams.json"), "w", encoding="utf-8") as fh:
        json.dump({"height_m": height, "cams": cams}, fh, indent=1)


if __name__ == "__main__":
    try:
        main()
    except BaseException as exc:                   # one readable line for npcgen/rig3d.py, and a failing exit code
        if isinstance(exc, SystemExit) and exc.code in (None, 0):
            raise
        import traceback
        traceback.print_exc()
        message = exc.code if isinstance(exc, SystemExit) else f"{type(exc).__name__}: {exc}"
        print(f"POSE_ERROR {message}", flush=True)
        sys.stdout.flush()
        os._exit(1)
