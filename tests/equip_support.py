"""Made-up characters and items for the tests of npcgen/equip.py: a skinned figure with the animation library's bone names that can face either way,
and plain procedural things (a sword, a shield, a staff, a hat, a helmet) already scaled the way the Items tab scales them (npcgen/items.py fit_glb)."""
import numpy as np

from npcgen import items, procmesh as pm

# joint -> (parent, world position) of a 1.75 m figure standing on +Y and facing +Z, its right hand on -X
JOINTS = {
    "pelvis": (None, (0.0, 0.95, 0.0)), "spine_03": ("pelvis", (0.0, 1.30, 0.0)), "neck_01": ("spine_03", (0.0, 1.50, 0.0)),
    "head": ("neck_01", (0.0, 1.58, 0.0)),
    "upperarm_r": ("spine_03", (-0.18, 1.42, 0.0)), "lowerarm_r": ("upperarm_r", (-0.30, 1.14, 0.0)), "hand_r": ("lowerarm_r", (-0.40, 0.88, 0.02)),
    "upperarm_l": ("spine_03", (0.18, 1.42, 0.0)), "lowerarm_l": ("upperarm_l", (0.30, 1.14, 0.0)), "hand_l": ("lowerarm_l", (0.40, 0.88, 0.02)),
}
HEAD_CENTRE, HEAD_AXES = (0.0, 1.64, 0.0), (0.078, 0.11, 0.095)       # crown at 1.75 m, 156 mm wide


def ellipsoid(centre, radii, around=12, rings=6):
    """npcgen/procmesh.py's ellipsoid for three plain radii (it takes a matrix of semi-axes)."""
    return pm.ellipsoid(np.asarray(centre, float), np.diag(np.asarray(radii, float)), around, rings)


def skinned_character(facing=1, head_scale=1.0, rigged=True, mesh_called="head"):
    """A GLB of a skinned figure: head, torso, legs and two hands, weighted to the joints that carry them. `facing` -1 turns it to face -Z (its right hand
    on +X). `rigged=False` leaves out the skin. `mesh_called` names the body's mesh node (it can be called `head`, like a joint)."""
    g = pm.Gltf("test")
    skin_material = g.material("skin", (0.8, 0.6, 0.5), 0.0, 0.7)
    turn = np.array([facing, 1.0, facing])
    world = {name: np.array(pos) * turn for name, (_p, pos) in JOINTS.items()}
    index = {}

    def build(name):
        children = [build(child) for child, (parent, _pos) in JOINTS.items() if parent == name]
        parent = JOINTS[name][0]
        local = world[name] - (world[parent] if parent else np.zeros(3))
        index[name] = g.node(name, children=children, translation=tuple(local))
        return index[name]

    root = g.node("Armature", children=[build("pelvis")])
    parts, carried = [], []
    head_centre, head_axes = np.array(HEAD_CENTRE) * turn, np.array(HEAD_AXES) * head_scale
    head_centre[1] = 1.64 + (head_scale - 1.0) * 0.11
    pieces = [(ellipsoid(head_centre, head_axes, 16, 10), "head"),
              (pm.cylinder(np.array([0.0, 0.95, 0.0]), np.array([0.0, 1.50, 0.0]), 0.15, 12), "spine_03"),
              (pm.cylinder(np.array([0.0, 0.0, 0.0]), np.array([0.0, 0.95, 0.0]), 0.1, 12), "pelvis"),
              (ellipsoid(world["hand_r"] + np.array([0.0, -0.06, 0.03]) * turn, (0.04, 0.07, 0.04), 8, 6), "hand_r"),
              (ellipsoid(world["hand_l"] + np.array([0.0, -0.06, 0.03]) * turn, (0.04, 0.07, 0.04), 8, 6), "hand_l")]
    for mesh, joint in pieces:
        parts.append(mesh)
        carried += [list(JOINTS).index(joint)] * len(mesh.pos)
    body = pm.merge(parts)
    mesh_index = g.mesh("body", [(body, skin_material)])
    attrs = g.doc["meshes"][mesh_index]["primitives"][0]["attributes"]
    if rigged:
        joints = np.zeros((len(body.pos), 4), np.uint16)
        joints[:, 0] = carried
        weights = np.zeros((len(body.pos), 4), np.float32)
        weights[:, 0] = 1.0
        attrs["JOINTS_0"] = g._accessor(joints, "VEC4", 5123, 34962)
        attrs["WEIGHTS_0"] = g._accessor(weights, "VEC4", 5126, 34962)
        g.doc["skins"] = [{"joints": [index[name] for name in JOINTS]}]
    body_node = g.node(mesh_called, mesh_index)
    if rigged:
        g.doc["nodes"][body_node]["skin"] = 0
    return g.dumps([root, body_node])


def _glb(parts, size_m, pivot, grip_at=None, name="thing"):
    g = pm.Gltf("test")
    material = g.material("steel", (0.6, 0.6, 0.65), 0.8, 0.4)
    mesh = g.mesh(name, [(pm.merge(parts), material)])
    glb = g.dumps([g.node(name, mesh)])
    return items.fit_glb(glb, size_m, pivot, grip_at)[0]


def column(radius, y0, y1, around=16, steps=8):
    """A closed upright cylinder made of `steps` stacked pieces, so that it has vertices all the way up (a real model does)."""
    edges = np.linspace(y0, y1, steps + 1)
    return [pm.cylinder(np.array([0.0, a, 0.0]), np.array([0.0, b, 0.0]), radius, around) for a, b in zip(edges[:-1], edges[1:])]


def sword_glb():
    """A metre-long sword: blade up +Y, flat sides facing +Z, grip a fifth of the way up."""
    blade = pm.cylinder(np.array([0.0, 0.25, 0.0]), np.array([0.0, 1.0, 0.0]), 0.03, 4)
    guard = pm.cylinder(np.array([-0.08, 0.22, 0.0]), np.array([0.08, 0.22, 0.0]), 0.015, 6)
    grip = pm.cylinder(np.array([0.0, 0.0, 0.0]), np.array([0.0, 0.22, 0.0]), 0.015, 8)
    return _glb([blade, guard, grip], 1.0, "grip", 0.1, "sword")


def staff_glb():
    return _glb(column(0.02, 0.0, 1.8, 8, 12), 1.8, "grip", 0.4, "staff")


def shield_glb():
    """A round shield 70 cm across and 6 cm deep, its face toward +Z."""
    return _glb([pm.cylinder(np.array([0.0, 0.0, -0.03]), np.array([0.0, 0.0, 0.03]), 0.35, 20)], 0.7, "centre", None, "shield")


def hat_glb():
    """A wide-brimmed hat: a 38 cm brim at the bottom, a 17 cm crown above it, closed on top."""
    brim = pm.cylinder(np.array([0.0, 0.0, 0.0]), np.array([0.0, 0.012, 0.0]), 0.19, 20)
    crown = column(0.085, 0.0, 0.10)
    top = ellipsoid([0.0, 0.10, 0.0], (0.085, 0.03, 0.085), 16, 6)
    return _glb([brim, *crown, top], 0.38, "base", None, "hat")


def helmet_glb():
    """A closed helmet 22 cm wide and 26 cm tall: a dome on a cylinder."""
    dome = ellipsoid([0.0, 0.14, 0.0], (0.11, 0.12, 0.11), 20, 10)
    return _glb([*column(0.11, 0.0, 0.14, 20), dome], 0.26, "base", None, "helmet")


def spec(family, kind):
    """The kind's entry in data/items.json."""
    return items.load()["families"][family]["kinds"][kind]
