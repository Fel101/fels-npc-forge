"""Blender (5.x) script: a lighter game version of an image-to-3D model, with its detail kept in the normal map.

    blender -b --factory-startup --python game_lod.py -- IN.glb OUT.glb [--faces 100000] [--normal 2048] [--report FILE]
                                                                         [--poster PNG]

The mesh is simplified by edge collapse (UVs follow), then the full-detail surface - including its own normal map - is
baked into a new tangent-space normal map for the simple one, and its colour and occlusion/roughness maps are baked across
too (the simplified mesh keeps the same UV layout, but simplifying stretches it: reusing the maps as they were broke
detailed paint into triangles). Runs on the CPU (Cycles); no GPU context is needed.

A model with a head layout (npcgen/cleanup3d.head_layout: a 2:1 colour texture, its normal map still on the generator's
layout as a second UV set) gets a 2:1 normal map on the first layout, and loses the second.
"""
import json
import math
import os
import sys
import time

import bpy
from mathutils import Vector


def args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    opts, pos = {"faces": "100000", "normal": "2048"}, []
    i = 0
    while i < len(argv):
        if argv[i].startswith("--"):
            opts[argv[i][2:]] = argv[i + 1]
            i += 2
        else:
            pos.append(argv[i])
            i += 1
    if len(pos) != 2:
        raise SystemExit("usage: game_lod.py IN.glb OUT.glb [--faces N] [--normal PX] [--report FILE] [--poster PNG]")
    opts["in"], opts["out"] = pos
    return opts


def load(path):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    # merge_vertices joins the copies glTF makes along UV seams, so simplifying cannot open cracks there
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
    high = bpy.context.view_layer.objects.active
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.delete_loose()                     # vertices left behind when the inner skin was removed
    bpy.ops.object.mode_set(mode="OBJECT")
    return high


def simplify(high, faces):
    low = high.copy()
    low.data = high.data.copy()
    low.name = "lod"
    bpy.context.scene.collection.objects.link(low)
    low.data.materials.clear()
    for mat in high.data.materials:                 # its own material, so baking into it never touches the source
        low.data.materials.append(mat.copy())
    before = len(high.data.polygons)
    if faces < before:
        mod = low.modifiers.new("decimate", "DECIMATE")
        mod.decimate_type = "COLLAPSE"
        mod.ratio = faces / before
        mod.use_collapse_triangulate = True
        bpy.ops.object.select_all(action="DESELECT")
        low.select_set(True)
        bpy.context.view_layer.objects.active = low
        bpy.ops.object.modifier_apply(modifier=mod.name)
    return low, before


def _bake_setup(high, low):
    height = max(high.dimensions)
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = 1
    bake = scene.render.bake
    bake.use_selected_to_active = True
    bake.cage_extrusion = 0.004 * height
    bake.max_ray_distance = 0.012 * height
    bake.margin = 8
    layers = low.data.uv_layers
    layers.active = layers[0]
    layers[0].active_render = True
    bpy.ops.object.select_all(action="DESELECT")
    high.select_set(True)
    low.select_set(True)
    bpy.context.view_layer.objects.active = low


def _is_normal_source(node):
    return any(link.to_node.type == "NORMAL_MAP" for out in node.outputs for link in out.links)


def bake_maps(high, low):
    """Bake every map but the normal map (colour, occlusion/roughness) from the high mesh onto the low one, each texel
    taking the value on the high surface at that point. Reusing the high mesh's textures through the simplified layout
    shattered detailed paint into triangles (measured on Clara's eyes, 2026-09-26): simplifying stretches the layout."""
    _bake_setup(high, low)
    images = []
    for mat in low.data.materials:
        for node in mat.node_tree.nodes:
            if node.type == "TEX_IMAGE" and node.image is not None and not _is_normal_source(node) and node.image not in images:
                images.append(node.image)
    for image in images:
        target = bpy.data.images.new(image.name + "_lod", image.size[0], image.size[1], alpha=False,
                                     is_data=image.colorspace_settings.is_data)
        target.colorspace_settings.name = image.colorspace_settings.name
        added = []                                  # (node tree, node) to remove afterwards
        relink = []                                 # (from socket, to socket) to restore on the high mesh
        for mat in low.data.materials:
            node = mat.node_tree.nodes.new("ShaderNodeTexImage")
            node.image = target
            mat.node_tree.nodes.active = node
            added.append((mat.node_tree, node))
        for mat in high.data.materials:
            nodes, links = mat.node_tree.nodes, mat.node_tree.links
            source = next((n for n in nodes if n.type == "TEX_IMAGE" and n.image == image), None)
            output = next((n for n in nodes if n.type == "OUTPUT_MATERIAL" and n.is_active_output), None)
            if source is None or output is None:
                continue
            if output.inputs["Surface"].links:
                relink.append((output.inputs["Surface"].links[0].from_socket, output.inputs["Surface"]))
            emit = nodes.new("ShaderNodeEmission")
            links.new(source.outputs["Color"], emit.inputs["Color"])
            links.new(emit.outputs["Emission"], output.inputs["Surface"])
            added.append((mat.node_tree, emit))
        bpy.ops.object.bake(type="EMIT")
        target.pack()
        for tree, node in added:
            tree.nodes.remove(node)
        for from_socket, to_socket in relink:
            from_socket.id_data.links.new(from_socket, to_socket)
        for mat in low.data.materials:
            for node in mat.node_tree.nodes:
                if node.type == "TEX_IMAGE" and node.image == image:
                    node.image = target
    return len(images)


def _colour_aspect(obj):
    """Width over height of the largest image the mesh's materials read, other than a normal map (the colour texture: a
    model with a head layout (npcgen/cleanup3d.head_layout) has a 2:1 one)."""
    best = (0, 1.0)
    for mat in obj.data.materials:
        for node in mat.node_tree.nodes:
            normal = any(link.to_node.type == "NORMAL_MAP" for out in node.outputs for link in out.links)
            if node.type == "TEX_IMAGE" and node.image is not None and not normal and node.image.size[1]:
                w, h = node.image.size
                best = max(best, (w * h, w / h))
    return best[1]


def bake_normals(high, low, size):
    """Bake the high mesh's shading normals (geometry + its normal map) into a new map on the low mesh, on its first UV
    layout, at the colour texture's shape (`size` texels high). Any other UV layout goes: a model with a head layout keeps
    the generator's own layout only for its old normal map, which this replaces."""
    layers = low.data.uv_layers
    layers.active = layers[0]
    layers[0].active_render = True
    width = int(round(size * _colour_aspect(low)))
    image = bpy.data.images.new("normal_lod", width, size, alpha=False, float_buffer=False, is_data=True)
    image.colorspace_settings.name = "Non-Color"
    for mat in low.data.materials:
        nodes, links = mat.node_tree.nodes, mat.node_tree.links
        node = nodes.new("ShaderNodeTexImage")
        node.image = image
        nodes.active = node
        mat["_lod_bake_node"] = node.name
    height = max(high.dimensions)
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = 1
    bake = scene.render.bake
    bake.use_selected_to_active = True
    bake.cage_extrusion = 0.004 * height
    bake.max_ray_distance = 0.012 * height
    bake.normal_space = "TANGENT"
    bake.margin = 8
    bpy.ops.object.select_all(action="DESELECT")
    high.select_set(True)
    low.select_set(True)
    bpy.context.view_layer.objects.active = low
    bpy.ops.object.bake(type="NORMAL")
    # wire the baked map in place of the old normal map
    for mat in low.data.materials:
        nodes, links = mat.node_tree.nodes, mat.node_tree.links
        baked = nodes[mat["_lod_bake_node"]]
        del mat["_lod_bake_node"]
        normal_map = next((n for n in nodes if n.type == "NORMAL_MAP"), None)
        bsdf = next((n for n in nodes if n.type == "BSDF_PRINCIPLED"), None)
        if normal_map is None and bsdf is not None:
            normal_map = nodes.new("ShaderNodeNormalMap")
            links.new(normal_map.outputs["Normal"], bsdf.inputs["Normal"])
        if normal_map is not None:
            old = normal_map.inputs["Color"].links[0].from_node if normal_map.inputs["Color"].links else None
            links.new(baked.outputs["Color"], normal_map.inputs["Color"])
            normal_map.uv_map = layers[0].name
            if old is not None and old != baked:
                nodes.remove(old)
        for node in [n for n in nodes if n.type == "UVMAP" and not any(out.links for out in n.outputs)]:
            nodes.remove(node)                      # what read the old normal map through the generator's layout
    image.pack()
    for layer in list(layers)[1:]:
        layers.remove(layer)
    return image


def poster(obj, path, width=384, height=512):
    """A lit still from three-quarters for the app (Cycles on the CPU: headless Workbench and EEVEE crash on some Windows
    GPU drivers)."""
    corners = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    lo = Vector([min(c[i] for c in corners) for i in range(3)])
    hi = Vector([max(c[i] for c in corners) for i in range(3)])
    centre, size = (lo + hi) / 2, hi.z - lo.z
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = 16
    scene.cycles.use_denoising = False
    scene.render.resolution_x, scene.render.resolution_y = width, height
    scene.view_settings.view_transform = "Standard"
    world = bpy.data.worlds.new("w")
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs["Color"].default_value = (0.85, 0.85, 0.85, 1)
    scene.world = world
    sun = bpy.data.objects.new("sun", bpy.data.lights.new("sun", "SUN"))
    sun.data.energy = 3.0
    sun.rotation_euler = (math.radians(50), 0, math.radians(-30))
    scene.collection.objects.link(sun)
    cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
    cam.data.type = "ORTHO"
    cam.data.ortho_scale = size * 1.1
    scene.collection.objects.link(cam)
    scene.camera = cam
    turn = math.radians(35)                        # the model faces -Y after import
    cam.location = centre + Vector((math.sin(turn), -math.cos(turn), 0)) * size * 4
    cam.rotation_euler = (centre - cam.location).to_track_quat("-Z", "Y").to_euler()
    scene.render.filepath = path
    bpy.ops.render.render(write_still=True)


def main():
    o = args()
    started = time.time()
    high = load(o["in"])
    low, before = simplify(high, int(o["faces"]))
    maps = bake_maps(high, low)
    bake_normals(high, low, int(o["normal"]))
    bpy.data.objects.remove(high, do_unlink=True)
    if o.get("poster"):
        poster(low, o["poster"])
    bpy.ops.object.select_all(action="DESELECT")
    low.select_set(True)
    bpy.ops.export_scene.gltf(filepath=o["out"], export_format="GLB", use_selection=True, export_yup=True,
                              export_tangents=True, export_apply=True)
    report = {"faces_before": before, "faces_after": len(low.data.polygons), "vertices_after": len(low.data.vertices),
              "normal_map": int(o["normal"]), "maps_baked": maps, "seconds": round(time.time() - started, 1)}
    if o.get("report"):
        with open(o["report"], "w") as fh:
            json.dump(report, fh)
    print("LOD_REPORT " + json.dumps(report), flush=True)


if __name__ == "__main__":
    try:
        main()
    except BaseException as exc:                   # one readable line for the caller, and a failing exit code
        if isinstance(exc, SystemExit) and exc.code in (None, 0):
            raise
        import traceback
        traceback.print_exc()
        message = exc.code if isinstance(exc, SystemExit) else f"{type(exc).__name__}: {exc}"
        print(f"LOD_ERROR {message}", flush=True)
        sys.stdout.flush()
        os._exit(1)
