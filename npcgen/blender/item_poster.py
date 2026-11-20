"""Render still pictures of a small model (glasses, a ring, any item) from a few directions, for the Items tab.

    blender -b --factory-startup --python item_poster.py -- IN.glb OUT.png [--view three_quarter] [--size 640x480] [--samples 48]
    blender -b --factory-startup --python item_poster.py -- IN.glb PREFIX --views front:0:0,side:90:0,top:0:80

With --views several pictures are rendered in one run, to PREFIX_<name>.png (each view is name:yaw:pitch in degrees: yaw 0 looks at
the front and a positive yaw moves the camera toward the model's left side, pitch positive looks down on it). --focus x,y,z,span frames a cube of
`span` metres around a point (glTF space) instead of the whole model, e.g. a face; --pose CLIP:SECONDS shows a rigged model at that instant of one of
its clips instead of at rest.

A studio set: a soft grey world for light (the camera sees a plain light background), a big key light, a fill and a rim light, so
glass shows its tint and metal has something to reflect. Cycles on the CPU, denoised: headless EEVEE and Workbench crash on some
Windows GPU drivers (the same choice as game_lod.py's poster), and a picture this small takes a few seconds.
"""
import math
import os
import sys

import bpy
from mathutils import Vector

NAMED = {"three_quarter": (32.0, 14.0), "front": (0.0, 0.0), "side": (90.0, 0.0), "back": (180.0, 0.0), "top": (0.0, 80.0)}


def args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    if len(argv) < 2:
        raise SystemExit("usage: item_poster.py IN.glb OUT.png [--view NAME] [--views name:yaw:pitch,...] [--size WxH] [--samples N] [--focus x,y,z,span] [--pose CLIP:SECONDS]")
    o = {"in": argv[0], "out": argv[1], "views": [("three_quarter", *NAMED["three_quarter"])], "size": (640, 480), "samples": 48,
         "multi": False, "focus": None, "pose": None}
    i = 2
    while i < len(argv):
        key = argv[i]
        if key == "--view":
            name = argv[i + 1]
            o["views"] = [(name, *NAMED[name])]
        elif key == "--views":
            o["multi"] = True
            o["views"] = []
            for part in argv[i + 1].split(","):
                bits = part.split(":")
                o["views"].append((bits[0], float(bits[1]) if len(bits) > 1 else NAMED[bits[0]][0],
                                   float(bits[2]) if len(bits) > 2 else NAMED[bits[0]][1]))
        elif key == "--size":
            o["size"] = tuple(int(v) for v in argv[i + 1].lower().split("x"))
        elif key == "--samples":
            o["samples"] = int(argv[i + 1])
        elif key == "--pose":                                       # a rigged model seen at this instant of one of its clips (it is drawn at rest otherwise)
            name, _, seconds = argv[i + 1].partition(":")
            o["pose"] = (name, float(seconds or 0.0))
        elif key == "--focus":                                      # x,y,z (glTF metres) and the width of the cube to frame, e.g. a face
            o["focus"] = [float(v) for v in argv[i + 1].split(",")]
        else:
            raise SystemExit(f"ITEM_POSTER_ERROR unknown option {key}")
        i += 2
    return o


def shown_meshes():
    """The model's meshes. A rigged model brings the importer's 42-vertex sphere (the bones' display shape, 2 m wide, in a collection that is
    not exported): it is not part of the model, and it made a character look three times as big."""
    shapes = {b.custom_shape for o in bpy.context.scene.objects if o.type == "ARMATURE" for b in o.pose.bones if b.custom_shape}
    return [o for o in bpy.context.scene.objects if o.type == "MESH" and o not in shapes
            and not any("not_exported" in c.name for c in o.users_collection)]


def bounds():
    lo, hi = Vector((1e9,) * 3), Vector((-1e9,) * 3)
    seen = False
    for obj in shown_meshes():
        for corner in obj.bound_box:
            p = obj.matrix_world @ Vector(corner)
            lo = Vector([min(lo[i], p[i]) for i in range(3)])
            hi = Vector([max(hi[i], p[i]) for i in range(3)])
            seen = True
    if not seen:
        raise SystemExit("ITEM_POSTER_ERROR the model has no mesh")
    return lo, hi


def studio(size):
    """The world and the lights, sized to the model (`size`: its longest side)."""
    scene = bpy.context.scene
    world = bpy.data.worlds.new("studio")
    world.use_nodes = True
    nodes, links = world.node_tree.nodes, world.node_tree.links
    nodes.clear()
    light = nodes.new("ShaderNodeBackground")
    light.inputs["Color"].default_value = (0.62, 0.62, 0.64, 1)
    light.inputs["Strength"].default_value = 0.35
    plain = nodes.new("ShaderNodeBackground")
    plain.inputs["Color"].default_value = (0.94, 0.94, 0.95, 1)
    path = nodes.new("ShaderNodeLightPath")
    mix = nodes.new("ShaderNodeMixShader")
    out = nodes.new("ShaderNodeOutputWorld")
    links.new(path.outputs["Is Camera Ray"], mix.inputs["Fac"])
    links.new(light.outputs["Background"], mix.inputs[1])
    links.new(plain.outputs["Background"], mix.inputs[2])
    links.new(mix.outputs["Shader"], out.inputs["Surface"])
    scene.world = world
    return world


def lamp(name, location, target, irradiance, width, height):
    """An area light at `location` aimed at `target`, bright enough to give a surface facing it `irradiance` W/m² (power = E·π·r²)."""
    location = Vector(location)
    data = bpy.data.lights.new(name, "AREA")
    data.shape = "RECTANGLE"
    data.size, data.size_y = width, height
    data.energy = irradiance * math.pi * (target - location).length ** 2
    obj = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(obj)
    obj.location = location
    obj.rotation_euler = (target - location).to_track_quat("-Z", "Y").to_euler()


def fit_distance(centre, corners, direction, up_hint, tan_h, tan_v, margin=1.1):
    """How far along `direction` from `centre` a camera has to stand to see all the bounding box corners with `margin` to spare."""
    forward = -direction
    right = forward.cross(up_hint).normalized()
    up = right.cross(forward).normalized()
    need = 0.0
    for c in corners:
        rel = c - centre
        need = max(need, rel.dot(direction) + abs(rel.dot(right)) * margin / tan_h, rel.dot(direction) + abs(rel.dot(up)) * margin / tan_v)
    return need


def main():
    o = args()
    bpy.ops.wm.read_factory_settings(use_empty=True)             # (--factory-startup brings its own cube, camera and light)
    bpy.ops.import_scene.gltf(filepath=o["in"])
    for obj in bpy.context.scene.objects:
        if obj.type == "ARMATURE":
            obj.data.pose_position = "REST"                           # the model as built, not frame 1 of whichever clip came first
            if o["pose"]:
                action = bpy.data.actions.get(o["pose"][0])
                if action is None:
                    raise SystemExit("ITEM_POSTER_ERROR the model has no clip called " + o["pose"][0])
                obj.data.pose_position = "POSE"
                obj.animation_data.action = action                    # (Blender picks the slot named after the armature)
                bpy.context.scene.frame_set(int(round(o["pose"][1] * bpy.context.scene.render.fps)))
    keep = set(shown_meshes())
    for obj in [o for o in bpy.context.scene.objects if o.type == "MESH" and o not in keep]:
        bpy.data.objects.remove(obj, do_unlink=True)                  # (the render would show the bone sphere too)
    bpy.context.view_layer.update()
    lo, hi = bounds()
    if o["focus"]:
        fx, fy, fz, span = o["focus"]
        middle = Vector((fx, -fz, fy))                                # glTF (x, y, z) is Blender (x, -z, y)
        lo, hi = middle - Vector((span / 2,) * 3), middle + Vector((span / 2,) * 3)
    centre, extent = (lo + hi) / 2, hi - lo
    size = max(extent)
    corners = [Vector((x, y, z)) for x in (lo.x, hi.x) for y in (lo.y, hi.y) for z in (lo.z, hi.z)]
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.render.threads_mode = "FIXED"                          # half the cores: a picture is not worth slowing a job that is running
    scene.render.threads = max(2, (os.cpu_count() or 4) // 2)
    scene.cycles.samples = o["samples"]
    scene.cycles.use_denoising = True
    scene.cycles.denoiser = "OPENIMAGEDENOISE"
    scene.cycles.max_bounces = 8
    scene.cycles.transparent_max_bounces = 16
    scene.render.resolution_x, scene.render.resolution_y = o["size"]
    scene.render.resolution_percentage = 100
    scene.view_settings.view_transform = "Standard"
    studio(size)
    d = size * 3.0                                   # lights in front of and around the model (the model faces -Y after import)
    lamp("key", centre + Vector((-d * 0.9, -d * 1.1, d * 0.9)), centre, 2.6, size * 3.2, size * 2.4)
    lamp("fill", centre + Vector((d * 1.2, -d * 0.7, d * 0.2)), centre, 0.9, size * 3.0, size * 2.0)
    lamp("rim", centre + Vector((d * 0.2, d * 1.2, d * 0.9)), centre, 1.6, size * 3.0, size * 1.6)
    lamp("top", centre + Vector((0, -d * 0.1, d * 1.4)), centre, 0.7, size * 3.0, size * 3.0)
    cam_data = bpy.data.cameras.new("cam")
    cam_data.lens = 85.0
    cam_data.sensor_width = 36.0
    cam_data.sensor_fit = "HORIZONTAL"                               # (AUTO would put the 36 mm on the longer side of a tall picture)
    cam = bpy.data.objects.new("cam", cam_data)
    scene.collection.objects.link(cam)
    scene.camera = cam
    aspect = o["size"][0] / o["size"][1]
    tan_h = 18.0 / 85.0
    tan_v = tan_h / aspect
    for name, yaw, pitch in o["views"]:
        y, p = math.radians(yaw), math.radians(pitch)
        direction = Vector((math.sin(y) * math.cos(p), -math.cos(y) * math.cos(p), math.sin(p)))
        distance = fit_distance(centre, corners, direction, Vector((0, 0, 1)), tan_h, tan_v)
        cam.location = centre + direction * distance
        cam.rotation_euler = (centre - cam.location).to_track_quat("-Z", "Y").to_euler()
        scene.render.filepath = f"{o['out']}_{name}.png" if o["multi"] else o["out"]
        bpy.ops.render.render(write_still=True)


try:
    main()
except SystemExit:
    raise
except Exception as exc:                                # (so the app can show the reason)
    print(f"ITEM_POSTER_ERROR {type(exc).__name__}: {exc}")
    raise
