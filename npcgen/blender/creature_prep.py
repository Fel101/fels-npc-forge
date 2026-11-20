"""Game prep for one creature, run by Blender 5.2 in the background (npcgen/creaturemaker.py starts it):

  blender.exe --background --factory-startup --python creature_prep.py -- --input raw/model.glb --output model.glb
      [--plan serpentine] [--tris 20000] [--length 2.5] [--segments 12] [--width-to-height 1.0] [--key <id>]

Steps (BESTIARY Section 7, the route for every creature): keep the largest piece and drop the specks the reconstruction leaves;
put the back up along +Z and the longest axis along Y; narrow the width to the height (one picture cannot show depth, and the
reconstruction guesses it too wide); scale to the length; put the head at -Y, the wider end of the finished body; relax the surface;
decimate to the triangle budget without changing the shape (reduce_shape, checked by prep_rules.py); one matte colour. A serpentine is then rigged
with one bone per body segment (weights from each vertex's distance to the bones, the top four kept) and given five clips
(Idle, Swim, Bite, Hit, Death). Any other body plan comes out as a static model: its rig is not built yet.

glTF turns Blender's Z-up into Y-up, so the head lands on +Z, which is forward in Godot.
Prints one line `REPORT: {...}` with the numbers.
"""
import json
import math
import os
import sys

import bpy
from mathutils import Matrix, Vector

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import prep_rules as rules  # noqa: E402  (next to this script; it has no bpy, so the rules are tested without Blender)

OPTS = {"tris": 20000, "length": 2.5, "segments": 12, "key": "creature", "plan": "static", "width_to_height": 1.0,
        "facing": "left", "min_elongation": 0.0}
MATTE = (0.15, 0.14, 0.12, 1.0)


def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    opts = dict(OPTS)
    flags = {"--input": "input", "--output": "output", "--plan": "plan", "--key": "key", "--facing": "facing"}
    numbers = {"--tris": ("tris", int), "--length": ("length", float), "--segments": ("segments", int),
               "--width-to-height": ("width_to_height", float), "--min-elongation": ("min_elongation", float)}
    i = 0
    while i < len(argv):
        flag = argv[i]
        value = argv[i + 1] if i + 1 < len(argv) else None
        if flag in flags:
            opts[flags[flag]] = value
        elif flag in numbers:
            name, cast = numbers[flag]
            opts[name] = cast(value)
        i += 2
    if "input" not in opts or "output" not in opts:
        raise SystemExit("usage: -- --input <raw glb> --output <glb> [--plan serpentine] [--tris N] [--length M]")
    if opts["plan"] == "serpentine":
        opts["plan"] = "serpentine"
    else:
        opts["plan"] = "static"
    if opts["facing"] not in rules.FACING_HEAD:
        raise SystemExit("--facing must be one of: " + ", ".join(rules.FACING_HEAD))
    return opts


def select_only(obj):
    """Select one object. The view layer is refreshed first, because in background mode a transform set on the object is not
    in its world matrix until then."""
    bpy.context.view_layer.update()
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def triangles(obj):
    obj.data.calc_loop_triangles()
    return len(obj.data.loop_triangles)


def extents(obj):
    """The size along each axis, from the vertices. obj.dimensions can lag a transform in background mode."""
    xs = [v.co.x for v in obj.data.vertices]
    ys = [v.co.y for v in obj.data.vertices]
    zs = [v.co.z for v in obj.data.vertices]
    return Vector((max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs)))


def bake(obj, matrix):
    """Move the mesh's own vertices by `matrix` (not the object's transform, which is stale in background mode)."""
    obj.data.transform(matrix)
    obj.data.update()


def keep_largest_part(obj):
    """Split the mesh into its loose pieces and keep the largest. The reconstruction leaves specks around the body."""
    select_only(obj)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.separate(type="LOOSE")
    bpy.ops.object.mode_set(mode="OBJECT")
    parts = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    biggest = max(parts, key=lambda o: len(o.data.vertices))
    for part in parts:
        if part is not biggest:
            bpy.data.objects.remove(part, do_unlink=True)
    return biggest


def end_spread(obj, end_sign):
    """How wide the body is in the last 8 % at one end of its length (Y). The head is the wider end."""
    verts = [v.co for v in obj.data.vertices]
    ys = [v.y for v in verts]
    lo, hi = min(ys), max(ys)
    band = (hi - lo) * 0.08
    ring = [v for v in verts if (v.y >= hi - band if end_sign > 0 else v.y <= lo + band)]
    if not ring:
        return 0.0
    cx = sum(v.x for v in ring) / len(ring)
    cz = sum(v.z for v in ring) / len(ring)
    return math.sqrt(sum((v.x - cx) ** 2 + (v.z - cz) ** 2 for v in ring) / len(ring))


def orient(obj):
    """Longest axis to Y, the thinner cross axis to Z (up)."""
    dims = extents(obj)
    longest = max(range(3), key=lambda a: dims[a])
    if longest == 0:
        bake(obj, Matrix.Rotation(math.radians(90.0), 4, "Z"))
    elif longest == 2:
        bake(obj, Matrix.Rotation(math.radians(90.0), 4, "X"))
    dims = extents(obj)
    if dims.x < dims.z:
        bake(obj, Matrix.Rotation(math.radians(90.0), 4, "Y"))


def slim_to_height(obj, ratio):
    """A single picture cannot show depth, and the reconstruction guesses it too wide. Narrow the width to `ratio` times the
    height, so the body reads as the picture does."""
    dims = extents(obj)
    if dims.x <= dims.z * ratio:
        return 1.0
    factor = dims.z * ratio / dims.x
    bake(obj, Matrix.Scale(factor, 4, Vector((1.0, 0.0, 0.0))))
    return factor


def fit(obj, length):
    """Scale to the body length along Y, then stand it on Z = 0 with the centre on the origin."""
    scale = length / max(extents(obj).y, 1e-6)
    bake(obj, Matrix.Scale(scale, 4))
    verts = [v.co for v in obj.data.vertices]
    cx = (min(v.x for v in verts) + max(v.x for v in verts)) / 2.0
    cy = (min(v.y for v in verts) + max(v.y for v in verts)) / 2.0
    minz = min(v.z for v in verts)
    bake(obj, Matrix.Translation((-cx, -cy, -minz)))


def collapse(obj, ratio):
    modifier = obj.modifiers.new("Decimate", "DECIMATE")
    modifier.decimate_type = "COLLAPSE"
    modifier.ratio = max(0.01, ratio)
    select_only(obj)
    bpy.ops.object.modifier_apply(modifier=modifier.name)


REMESH_TRIANGLES_PER_AREA = 1.2     # a voxel remesh gives about this many triangles per unit of area / voxel^2 (measured on the Reedcoil)
REMESH_BUDGET_FACTOR = 3.0          # the remesh is sized to about three times the budget, then collapsed gently
ROUTES = (("remesh+collapse", True), ("collapse", False))     # data/creature_plans.json's order: (a) remesh first, then (b) collapse


def orient_static(obj, facing):
    """A body that is not a serpent, laid the way its picture shows it (prep_rules.static_rotation): its length along Y, its head
    at -Y and its back up Z. Which way is up is the side the mass sits on (prep_rules.raw_up), so it is read from the mesh. Returns the
    raw up it used, and the mass offset after the turn. That offset is the measure the choice came from, so it records the choice; it
    does not check it. A body with a tall crest or raised wings can fool the choice, so look at the side render before it packs."""
    ys = [v.co.y for v in obj.data.vertices]
    up = rules.raw_up(sum(ys) / len(ys) - (max(ys) + min(ys)) / 2)
    bake(obj, Matrix(rules.static_rotation(facing, up)).to_4x4())
    zs = [v.co.z for v in obj.data.vertices]
    return up, round(sum(zs) / len(zs) - (max(zs) + min(zs)) / 2, 4)


def copy_object(obj):
    select_only(obj)
    bpy.ops.object.duplicate()
    return bpy.context.view_layer.objects.active


def discard(obj):
    mesh = obj.data
    bpy.data.objects.remove(obj, do_unlink=True)
    if mesh.users == 0:
        bpy.data.meshes.remove(mesh)


def gentle_collapse(obj, budget):
    """Collapse passes that keep at least half the triangles each, until the budget is reached. One pass to a tiny ratio is what
    took the Reedcoil's width down by half, so no pass cuts more than half."""
    for _ in range(40):
        count = triangles(obj)
        if count <= budget:
            return count
        collapse(obj, max(0.5, budget / count * 0.97))
    return triangles(obj)


def remesh(obj, budget):
    """A voxel remesh sized to about REMESH_BUDGET_FACTOR times the budget. It closes the reconstruction's open holes, which the
    collapse otherwise eats through (the thin parts go first)."""
    area = sum(p.area for p in obj.data.polygons)
    modifier = obj.modifiers.new("Remesh", "REMESH")
    modifier.mode = "VOXEL"
    modifier.voxel_size = math.sqrt(area * REMESH_TRIANGLES_PER_AREA / (REMESH_BUDGET_FACTOR * budget))
    select_only(obj)
    bpy.ops.object.modifier_apply(modifier=modifier.name)


def reduce_shape(obj, budget, limit=rules.DRIFT_LIMIT):
    """Reduce the mesh to its triangle budget without changing its shape. Each route runs on a copy of the relaxed mesh, and the
    first that keeps the triangles within the budget and every axis within `limit` wins. Returns (object, triangles, route, drift).
    When neither does, it prints the REFUSED reason and exits. Not done: the spec's boundary-keeping collapse (option b) needs a
    vertex-group weight that is not built; the drift check is what guards the result."""
    reference = tuple(extents(obj))
    attempts = []
    for route, with_remesh in ROUTES:
        candidate = copy_object(obj)
        if with_remesh:
            remesh(candidate, budget)
        count = gentle_collapse(candidate, budget)
        drift_now = rules.drift(reference, tuple(extents(candidate)))
        if count <= budget and rules.worst_drift(drift_now) <= limit:
            discard(obj)
            return candidate, count, route, drift_now
        attempts.append((count, drift_now))
        discard(candidate)
    # the attempt that came closest to the shape explains the refusal
    count, drift_now = min(attempts, key=lambda attempt: rules.worst_drift(attempt[1]))
    reason = rules.refusal(budget, count, drift_now, limit) or "could not reduce the model to the budget"
    print("REFUSED: " + reason, flush=True)
    raise SystemExit(1)


def relax(obj, iterations=8, factor=0.04):
    """Relax the surface a little before decimating, so the reconstruction's spikes do not survive the cut."""
    modifier = obj.modifiers.new("Relax", "LAPLACIANSMOOTH")
    modifier.iterations = iterations
    modifier.lambda_factor = factor
    modifier.use_volume_preserve = True
    select_only(obj)
    bpy.ops.object.modifier_apply(modifier=modifier.name)


def matte_material(obj):
    material = bpy.data.materials.new("Creature")
    material.use_nodes = True
    bsdf = material.node_tree.nodes.get("Principled BSDF")
    bsdf.inputs["Base Color"].default_value = MATTE
    bsdf.inputs["Roughness"].default_value = 0.8
    obj.data.materials.clear()
    obj.data.materials.append(material)


def build_armature(obj, segments):
    verts = [v.co for v in obj.data.vertices]
    y0 = min(v.y for v in verts)
    y1 = max(v.y for v in verts)
    z_mid = (min(v.z for v in verts) + max(v.z for v in verts)) / 2.0
    step = (y1 - y0) / segments
    arm_data = bpy.data.armatures.new("CreatureRig")
    arm_obj = bpy.data.objects.new("CreatureRig", arm_data)
    bpy.context.scene.collection.objects.link(arm_obj)
    select_only(arm_obj)
    bpy.ops.object.mode_set(mode="EDIT")
    previous = None
    names = []
    for i in range(segments):
        bone = arm_data.edit_bones.new(f"seg_{i + 1:02d}")
        bone.head = Vector((0.0, y0 + i * step, z_mid))
        bone.tail = Vector((0.0, y0 + (i + 1) * step, z_mid))
        if previous is not None:
            bone.parent = previous
            bone.use_connect = True
        previous = bone
        names.append(bone.name)
    bpy.ops.object.mode_set(mode="OBJECT")
    return arm_obj, names, step


def segment_distance(p, a, b):
    ab = b - a
    t = max(0.0, min(1.0, (p - a).dot(ab) / max(ab.length_squared, 1e-9)))
    return (p - (a + ab * t)).length


def skin(obj, arm_obj, names, step):
    """Each vertex follows the bones near it: a Gaussian of its distance to each bone, the top four kept and normalised."""
    bones = arm_obj.data.bones
    segments = [(bones[n].head_local, bones[n].tail_local) for n in names]
    sigma = step * 0.9
    groups = {n: obj.vertex_groups.new(name=n) for n in names}
    for v in obj.data.vertices:
        weights = sorted(((math.exp(-(segment_distance(v.co, a, b) / sigma) ** 2), n)
                          for (a, b), n in zip(segments, names)), reverse=True)[:4]
        total = sum(w for w, _ in weights) or 1.0
        for w, n in weights:
            groups[n].add([v.index], w / total, "REPLACE")
    modifier = obj.modifiers.new("Armature", "ARMATURE")
    modifier.object = arm_obj
    obj.parent = arm_obj


def clip(arm_obj, name, frames, pose):
    """A looping clip: `pose(bone index, count, k, frames)` gives (rx, ry, rz) for frame k."""
    action = bpy.data.actions.new(name)
    action.use_fake_user = True
    arm_obj.animation_data_create()
    arm_obj.animation_data.action = action
    count = len(arm_obj.pose.bones)
    for k in range(frames):
        for i, bone in enumerate(arm_obj.pose.bones):
            bone.rotation_mode = "XYZ"
            bone.rotation_euler = pose(i, count, k, frames)
            bone.keyframe_insert("rotation_euler", frame=k + 1)
    return action


def serpent_clips(arm_obj):
    def idle(i, n, k, frames):
        return (0.0, 0.0, 0.09 * math.sin(2 * math.pi * k / frames - i * 0.5))

    def swim(i, n, k, frames):
        return (0.0, 0.0, 0.25 * math.sin(2 * math.pi * k / frames - i * 0.7))

    def bite(i, n, k, frames):
        t = k / frames
        lunge = max(0.0, math.sin(2 * math.pi * t))
        return (-0.4 * lunge if i < 3 else 0.0, 0.0, 0.05 * math.sin(2 * math.pi * t + i * 0.4))

    def hit(i, n, k, frames):
        t = k / frames
        strength = 0.3 * math.exp(-3.0 * t) * math.sin(2 * math.pi * t * 2)
        return (0.0, 0.0, strength * (1.0 if abs(i - 5) <= 1 else 0.3 if abs(i - 5) <= 3 else 0.0))

    def death(i, n, k, frames):
        t = min(k / (frames * 0.6), 1.0)
        return (0.0, 0.0, 0.5 * t * math.sin(i * 0.4))

    clip(arm_obj, "Idle", 72, idle)
    clip(arm_obj, "Swim", 28, swim)
    clip(arm_obj, "Bite", 24, bite)
    clip(arm_obj, "Hit", 16, hit)
    clip(arm_obj, "Death", 48, death)
    return ["Idle", "Swim", "Bite", "Hit", "Death"]


def main():
    opts = parse_args()
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()
    bpy.ops.import_scene.gltf(filepath=opts["input"])
    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    if not meshes:
        raise SystemExit("no mesh in the input")
    select_only(meshes[0])
    for m in meshes:
        m.select_set(True)
    bpy.ops.object.join()
    obj = bpy.context.view_layer.objects.active
    for other in [o for o in bpy.context.scene.objects if o.type != "MESH" or o is not obj]:
        bpy.data.objects.remove(other, do_unlink=True)
    select_only(obj)
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    obj = keep_largest_part(obj)
    pieces_kept = len(obj.data.vertices)

    raw_up = upright = None
    if opts["plan"] == "serpentine":
        orient(obj)
    else:
        raw_up, upright = orient_static(obj, opts["facing"])
    # the shape before any fit: a serpent that is about as long as it is wide or high is a coil, and is refused
    dims = extents(obj)
    elong = rules.elongation(dims.y, dims.x, dims.z)
    if opts["plan"] == "serpentine":
        if elong < opts["min_elongation"]:
            print(f"REFUSED: this picture is a coil, not a serpent: its body is {elong:.2f} times as long as it is wide or high "
                  f"(at least {opts['min_elongation']:g} is needed). Redraw it from the side, stretched out, and make the 3D model again.",
                  flush=True)
            raise SystemExit(1)
        slim_factor = slim_to_height(obj, opts["width_to_height"])
        fit(obj, opts["length"])
        # The head is the wider end of the finished body. It is decided here, not before the slimming, when the two ends are
        # about as wide as each other.
        head_flipped = end_spread(obj, +1) > end_spread(obj, -1)
        if head_flipped:
            bake(obj, Matrix.Rotation(math.radians(180.0), 4, "Z"))
    else:
        slim_factor = 1.0
        head_flipped = False
        fit(obj, opts["length"])
    relax(obj)
    before = triangles(obj)
    obj, after, route, drift_now = reduce_shape(obj, opts["tris"])
    # the length is set again from the finished surface (a uniform scale: the shape has already passed the drift check)
    if opts["plan"] == "serpentine":
        fit(obj, opts["length"])
    else:
        fit(obj, opts["length"])
    for poly in obj.data.polygons:
        poly.use_smooth = True
    matte_material(obj)

    clips = []
    if opts["plan"] == "serpentine":
        arm_obj, names, step = build_armature(obj, opts["segments"])
        skin(obj, arm_obj, names, step)
        clips = serpent_clips(arm_obj)
        select_only(arm_obj)
        obj.select_set(True)
        bpy.context.view_layer.objects.active = arm_obj
    else:
        select_only(obj)
    bpy.ops.export_scene.gltf(
        filepath=opts["output"], export_format="GLB", use_selection=True, export_apply=False,
        export_skins=True, export_animations=bool(clips), export_animation_mode="ACTIONS", export_yup=True)

    dims = extents(obj)
    report = {"key": opts["key"], "plan": opts["plan"], "facing": opts["facing"] if opts["plan"] != "serpentine" else None, "triangles_before": before,
              "triangles_after": after, "decimate": route, "drift": drift_now, "drift_limit": rules.DRIFT_LIMIT,
              "elongation": elong, "vertices_kept": pieces_kept, "width_factor_applied": round(slim_factor, 3),
              "width_to_height": opts["width_to_height"], "head_flipped": head_flipped,
              "bones": opts["segments"] if clips else 0, "clips": clips,
              "size_m": [round(dims.x, 3), round(dims.y, 3), round(dims.z, 3)],
              "raw_up": list(raw_up) if raw_up else None, "upright": upright}
    print("REPORT:", json.dumps(report), flush=True)


if __name__ == "__main__":
    main()
