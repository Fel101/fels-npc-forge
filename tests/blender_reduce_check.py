"""Run in Blender by test_prep_blender.py: the serpent route up to the reduction, then reduce_shape alone, on one raw model.
It prints one line, REDUCE {...}, with the route that kept the shape, the triangles and the drift; or REFUSED, and exits.

  blender --background --factory-startup --python tests/blender_reduce_check.py -- <raw glb> <length m> <triangle budget>
"""
import importlib.util
import json
import sys
from pathlib import Path

import bpy

PREP = Path(__file__).resolve().parent.parent / "npcgen" / "blender" / "creature_prep.py"
spec = importlib.util.spec_from_file_location("creature_prep", PREP)
cp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cp)                  # loads the functions only: its main() runs when it is the script, not here

raw, length, budget = sys.argv[sys.argv.index("--") + 1:][:3]
length, budget = float(length), int(budget)
bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete()
bpy.ops.import_scene.gltf(filepath=raw)
meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
cp.select_only(meshes[0])
for m in meshes:
    m.select_set(True)
bpy.ops.object.join()
obj = bpy.context.view_layer.objects.active
bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
obj = cp.keep_largest_part(obj)
cp.orient(obj)
cp.slim_to_height(obj, 1.0)
cp.fit(obj, length)
cp.relax(obj)
obj, count, route, drift = cp.reduce_shape(obj, budget)
print("REDUCE " + json.dumps({"route": route, "triangles": count, "drift": drift}), flush=True)
