"""Soft-body motion for a designed female body: spring bones for the chest, the butt, the belly and the thighs (lead, 2026-10-14).

The owner's wish ("jiggle physics", as Skyrim calls it): a tick box when the character is made, and sliders for how strong it is, overall and for each part. This module is the
file half of it. It takes the finished, animated game body (npcgen/bodymaker.py builds it) and adds, for each part that is on, one chain of two spring bones per side (the belly: one in the
middle), hung on a bone the body already has: the chest on spine_02 (the bone that carries most of the skin at the bust), the butt on the pelvis, the belly on spine_01, each thigh on its
own thigh bone. It then gives the skin around the bust apex, the seat, the belly and the outer thighs a share of its weight to those bones (the underwear and every garment too, so the
clothes move with the body under them). The game engine does the swinging (Godot: examples/godot/forge_secondary_motion.gd reads the settings put on the first bone of each chain, as it
does for the hair and the cloth of the Body Designer's pass 162), so nothing in the file moves by itself.

What it never does: touch a body that is not a designed adult female body, move the clips (the new bones are keyed by none), or change the way the body moves while the engine leaves
the spring bones alone (to within a few millimetres: see DROP_COST). The share handed to a spring bone is taken from the bone it hangs on (the chest: spine_02; the butt: pelvis; the belly:
spine_01; a thigh: that thigh bone) and from no other, so the skin follows the same bones as before until the engine moves the spring bones. At most four bones carry a vertex (the glTF rule the
Body Designer keeps): see HAND_ALL and DROP_COST for how a vertex that already has four makes room, and what the step leaves alone. The Body Designer's own files and recipes are never
changed: this step runs last, on the finished file, and a body that has it is built again from its recipe when it needs another fit, as one with swinging hair or cloth is.
"""
import copy
import math

import numpy as np

from .paint3d import Glb

PARTS = ("chest", "butt", "belly", "thighs")
LABELS = {"chest": "Chest", "butt": "Butt", "belly": "Belly", "thighs": "Thighs"}
SIDES = {"l": 1.0, "r": -1.0, "c": 1.0}  # a chain's tag -> the sign of x on its side of the body ("c": the one chain in the middle of the body, whose apex is at x = 0)
BONES_PER_CHAIN = 2
REFERENCE_HEIGHT = 1.59                  # the Body Designer's default body is this tall (landmarks.height_m); the sizes below are for it and grow or shrink with height_m
NOT_SKINNED_HERE = ("ForgeEye_l", "ForgeEye_r", "ForgeLashes", "ForgeHair")
REQUIRED_BONES = ("pelvis", "spine_01", "spine_02", "spine_03")

# For each part: the bone the chains hang on, the bones whose skin share a spring bone may take (never any other), how big the region is, how far in front of or behind the apex the body's
# wall lies (the skin on the other side of the body is left alone), where the apex is looked for (metres at the reference height, x is the character's left, y is relative to a bone) and
# how the spring bones are laid: how far behind the surface the first one sits, how long the second bone and the free end are. The rest are for the parts that are not a chain on each side
# facing front or back: `sides` (default ("l", "r"); the belly has the one chain "c" in the middle), `axis` (2, the default, is a part that sticks out to the front or the back and whose wall
# is a plane across z; 0 is one that sticks out sideways, the thighs: the region is round in z and y and the wall is a plane across x), and a bone name with {} in it is made for each side
# (the thigh bone of that side).
SHAPES = {
    "chest": {"parent": "spine_02", "family": ("spine_02",), "radius": 0.082, "depth": 0.075, "fade": 0.03, "pivot_back": 0.085, "link": 0.05, "end": 0.04, "toward": 1,
              "near": ("spine", "pelvis", "clavicle", "neck"), "window": {"x": (0.05, 0.16), "y": (-0.14, 0.02), "reference": "spine_03", "z": (0.06, 1.0)}},
    "butt": {"parent": "pelvis", "family": ("pelvis",), "radius": 0.095, "depth": 0.075, "fade": 0.03, "pivot_back": 0.085, "link": 0.05, "end": 0.04, "toward": -1,
             "near": ("spine", "pelvis", "thigh"), "midline": 0.04, "window": {"x": (0.02, 0.15), "y": (-0.12, 0.08), "reference": "pelvis", "z": (-1.0, -0.04)}},
    "belly": {"parent": "spine_01", "family": ("spine_01",), "radius": 0.085, "depth": 0.075, "fade": 0.03, "pivot_back": 0.085, "link": 0.045, "end": 0.035, "toward": 1, "sides": ("c",),
              "near": ("spine", "pelvis", "thigh"), "window": {"x": (-0.06, 0.06), "y": (-0.03, 0.05), "reference": "spine_01", "z": (0.06, 1.0)}},
    "thighs": {"parent": "thigh_{}", "family": ("thigh_{}",), "radius": 0.10, "depth": 0.075, "fade": 0.03, "pivot_back": 0.085, "link": 0.05, "end": 0.04, "toward": 1, "axis": 0,
               "near": ("spine", "pelvis", "thigh", "calf"), "window": {"x": (0.10, 0.30), "y": (-0.20, -0.02), "reference": "thigh_l", "z": (-0.15, 0.15)}},
}

# A vertex that already has four bones cannot take a fifth. Two ways out, both nearly exact: when what is left of the parent bone's share after the hand-over is HAND_ALL or less, the spring bone
# takes all of it (the spring bone moves like the parent until the engine moves it, so nothing changes); otherwise the entry whose loss moves the vertex least is merged into the bone above it
# (cost = its weight x its distance from its bone x how far that bone typically turns, SWING), when that cost is DROP_COST metres or less, else the vertex is left alone. A vertex that the
# arm pulls on (the armpit) is eased out of the soft body: its share is scaled by 1 - smooth(foreign / FOREIGN_FADE), `foreign` being the weight on bones that are not 'near' (SHAPES).
# Measured on the three test bodies (2026-10-14), with the spring bones held at rest: DROP_COST 3 mm leaves a dozen vertices at the edge of the bust without their share (a crease at 30 degrees,
# edge strain up to 73 %) and moves the skin by at most 3.8 mm against the original in the 85 clips; 5 mm leaves none out (strain 47 %, the same as with no limit at all: what is left is the
# falloff itself) and moves it by at most 6.4 mm in the wildest clip (median 2 mm). FOREIGN_FADE 0.10 had a step where the upper arm starts to count; 0.30 is gentle.
HAND_ALL = 0.12
DROP_COST = 0.005
FOREIGN_FADE = 0.30
BUST_MARK = "forge_bust"                 # what npcgen/bustsize.py leaves in the file's extras: the chest follows the bust where it now is (a larger bust: a wider region, a deeper wall, a longer lever)
BUST_GROW = {"larger": 0.15, "smaller": 0.10}
SWING = {"spine": 0.5, "pelvis": 0.5, "clavicle": 0.4, "neck": 0.5, "upperarm": 1.5, "thigh": 1.2}

# What the engine is asked to do (Godot's SpringBoneSimulator3D settings; suggested starting values, to be tuned in the game).
SETTINGS = {
    "chest": {"stiffness": 4.5, "drag": 0.2, "gravity": 0.1, "radius_m": 0.01, "max_angle_deg": 32.0},
    "butt": {"stiffness": 2.5, "drag": 0.2, "gravity": 0.1, "radius_m": 0.01, "max_angle_deg": 22.0},
    "belly": {"stiffness": 4.5, "drag": 0.2, "gravity": 0.1, "radius_m": 0.01, "max_angle_deg": 16.0, "center_bone": "root"},
    "thighs": {"stiffness": 8.0, "drag": 0.2, "gravity": 0.1, "radius_m": 0.01, "max_angle_deg": 14.0, "center_bone": "root"},
}
# The belly's and the thighs' stiffness and limit were chosen (2026-10-14/15) by how far the skin at the apex of a part moves, not how far its bone does (the bone's end is half way to the apex, so the
# same bone travel can mean twice the skin travel): in a jog the chest's apex moves 35 mm up and down, and these two are set to move about as far in a jog and a sprint (belly 32 and 26 mm, a
# thigh 25 mm) and a few millimetres in a walk, with the limit holding the worst of the 85 clips to 12.5 and 12.2 mm at the end bone. See the Body Designer add-on's notes.
# `center_bone` (the belly and the thighs; the chest and the butt have none and the engine takes the bone a chain hangs on): the bone whose pose Godot's simulator takes as the centre of the
# movement. The helper (examples/godot/forge_secondary_motion.gd) uses a chain's parent bone when the block names no other. In Godot 4.7 a bone centre is applied to the bones' positions as the
# bone's own pose, not its inverse (scene/3d/spring_bone_simulator_3d.cpp: `cached_center = get_bone_global_pose(center_bone)`, used as `center.xform(point)`), which multiplies the turn of the centre
# bone by the distance of the point from the skeleton's origin: harmless for a spine bone (and what the chest and the butt were tuned in), but a thigh turns through 43 degrees in a walk and 72 in a jog
# and the chain on it flapped through 50 to 73 degrees in a jog. The skeleton's `root` bone never moves in the animation library's 85 clips (measured: no travel, no turn), so with it as the centre the chains
# move only by what the animation does to the body, which is what a centre is for, and the character's walk through the game world does not leak in.


class SoftBodyError(ValueError):
    """The file cannot take soft-body bones, with the reason in words."""


# ---- reading the file ---------------------------------------------------------------------------------------------------------------------
def _matrix(node):
    """A node's own transform (4 x 4, column vectors)."""
    if "matrix" in node:
        return np.array(node["matrix"], float).reshape(4, 4).T
    x, y, z, w = node.get("rotation", (0.0, 0.0, 0.0, 1.0))
    rot = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                    [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                    [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])
    out = np.eye(4)
    out[:3, :3] = rot * np.array(node.get("scale", (1.0, 1.0, 1.0)))[None, :]
    out[:3, 3] = node.get("translation", (0.0, 0.0, 0.0))
    return out


def _mul(a, b):
    """A matrix times a matrix or a vector, written out with einsum: the repository keeps the @ operator (a threaded matrix product) out of npcgen (tests/test_guided.py)."""
    return np.einsum("ij,jk->ik", a, b) if np.ndim(b) == 2 else np.einsum("ij,j->i", a, b)


def _worlds(doc):
    """The world transform of every node."""
    nodes = doc["nodes"]
    parent = {child: i for i, node in enumerate(nodes) for child in node.get("children", [])}
    world = {}

    def get(i):
        if i not in world:
            local = _matrix(nodes[i])
            world[i] = _mul(get(parent[i]), local) if i in parent else local
        return world[i]
    for i in range(len(nodes)):
        get(i)
    return world


def _skinned(doc):
    """{mesh name: primitive} for the meshes that hang on the body's skin and carry bone weights."""
    out = {}
    for node in doc["nodes"]:
        if node.get("mesh") is not None and node.get("skin") is not None:
            mesh = doc["meshes"][node["mesh"]]
            if mesh.get("name") not in NOT_SKINNED_HERE and mesh["primitives"]:
                attributes = mesh["primitives"][0]["attributes"]
                if "JOINTS_0" in attributes and "WEIGHTS_0" in attributes:
                    out[mesh["name"]] = mesh["primitives"][0]
    return out


def problem(extras):
    """Why a file cannot take soft-body bones, in words, or None. `extras` is the asset's extras: the body must be one the Body Designer made as an adult game body, with the female foundation."""
    extras = extras or {}
    design = extras.get("body_design") or {}
    if extras.get("export_tier") != "game" or not design:
        return "Soft-body motion is added to a body the Body Designer made."
    if type(extras.get("minimum_age")) is not int or extras["minimum_age"] < 18:
        return "The body does not say it is an adult's, so soft-body motion is not added."
    if "female" not in str(design.get("base", "")):
        return "Soft-body motion is made for the female body."
    return None


# ---- the shape of a part on this body -----------------------------------------------------------------------------------------------------
def sides_of(part):
    """The chains of a part as ((tag, side), ...): (("l", 1.0), ("r", -1.0)) for a part on both sides, (("c", 1.0),) for the belly's one chain in the middle."""
    return tuple((tag, SIDES[tag]) for tag in SHAPES[part].get("sides", ("l", "r")))


def parent_of(part, tag):
    """The name of the bone the chain `tag` of `part` hangs on (the thigh bone of its own side for the thighs)."""
    return SHAPES[part]["parent"].format(tag)


def family_of(part, tag):
    """The names of the bones whose skin share the chain `tag` of `part` may take."""
    return tuple(name.format(tag) for name in SHAPES[part]["family"])


def family_names(part):
    """Every bone a part takes a share from, each once, in order (the card's notes and the file's mark say them)."""
    out = []
    for tag, _ in sides_of(part):
        out += [name for name in family_of(part, tag) if name not in out]
    return out


def _smooth(t):
    t = np.clip(t, 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def window_shift(mark):
    """How far (metres, negative: down) the chest's window moves for a body whose bust the Body Designer's body shape moved: the mark `forge_bust` of source "body_shape" says (npcgen/bustsize.py
    `mark_shape`: the bust of such a body can hang a hand's breadth below where the window of the Body Designer's default body is). 0 for every other body, the bust size's own marks included."""
    if isinstance(mark, dict) and mark.get("source") == "body_shape":
        value = mark.get("window_shift_m")
        if type(value) in (int, float) and math.isfinite(value):
            return float(value)
    return 0.0


def _apex(positions, bones, part, scale, shift=0.0):
    """Where the part sticks out the most on the character's left side (the belly: in the middle, at x = 0): the middle of the vertices within 6 mm (scaled) of the extreme, along z for a part
    that sticks out to the front or the back, along x for one that sticks out sideways (the thighs). `shift` (metres, up is positive) moves the window up or down: the chest of a body whose bust
    hangs lower than the Designer's default one (`window_shift`)."""
    shape = SHAPES[part]
    window = shape["window"]
    reference = bones[window["reference"]][1] + shift
    lo, hi = window["y"]
    mask = ((positions[:, 0] > window["x"][0] * scale) & (positions[:, 0] < window["x"][1] * scale)
            & (positions[:, 1] > reference + lo * scale) & (positions[:, 1] < reference + hi * scale)
            & (positions[:, 2] > window["z"][0] * scale) & (positions[:, 2] < window["z"][1] * scale))
    if int(mask.sum()) < 10:
        raise SoftBodyError(f"The {part} could not be found on this body")
    points = positions[mask]
    sign, axis = shape["toward"], shape.get("axis", 2)
    extreme = (points[:, axis] * sign).max()
    apex = points[(points[:, axis] * sign) > extreme - 0.006 * scale].mean(axis=0)
    if shape.get("sides") == ("c",):
        apex[0] = 0.0                                                # (the middle of the body: the window is symmetric, the mean is a hair off it)
    return apex


def weight_of(points, part, apex, scale, side, grow=1.0, deeper=0.0):
    """How much of a spring bone's share (0 to 1) each point gets: 1 at the apex, 0 at the radius, 0 behind the wall of the body. It depends on where the point is across the front (or
    the back, or the side) of the body, not on how far out it lies, so the underwear and the clothes over the skin get the weight of the skin under them. `grow` makes the radius that many
    times as big and `deeper` (metres) puts the wall that much farther behind the apex: a bust the bust size (npcgen/bustsize.py) made larger or smaller has both."""
    shape = SHAPES[part]
    radius = shape["radius"] * scale * grow
    toward = shape["toward"]
    if shape.get("axis", 2) == 2:
        lateral = np.hypot(points[:, 0] - side * apex[0], points[:, 1] - apex[1]) / radius
        gate = _smooth(((points[:, 2] - (apex[2] - toward * (shape["depth"] * scale + deeper))) * toward) / (shape["fade"] * scale))
    else:                                                            # sticks out sideways: round across z and y, the wall is a plane across x (the outer side of the limb)
        lateral = np.hypot(points[:, 2] - apex[2], points[:, 1] - apex[1]) / radius
        outward = side * toward
        gate = _smooth(((points[:, 0] - (side * apex[0] - outward * (shape["depth"] * scale + deeper))) * outward) / (shape["fade"] * scale))
    f = 0.5 * (1.0 + np.cos(math.pi * np.clip(lateral, 0.0, 1.0)))
    out = f * gate
    if "midline" in shape:                                           # the cleft between the two sides stays where it is
        out = out * _smooth(side * points[:, 0] / (shape["midline"] * scale))
    return out


def _foreign_weight(entries, family, names, near):
    """The weight a vertex has on bones that are not the part's own and not near it ([[column, weight], ...]; `names` are the skin's own bones, so a spring bone an earlier part gave this vertex, which
    comes after them, is not foreign)."""
    return sum(w for j, w in entries if j not in family and j < len(names) and not names[j].startswith(near))


def _quaternion_y_to(direction):
    """The rotation (x, y, z, w) that turns +Y to `direction` by the short way."""
    d = direction / np.linalg.norm(direction)
    c = float(d[1])
    if c < -0.999999:
        return (1.0, 0.0, 0.0, 0.0)
    q = np.array([d[2], 0.0, -d[0], 1.0 + c])                       # cross((0, 1, 0), d) = (d.z, 0, -d.x)
    q /= np.linalg.norm(q)
    return tuple(float(v) for v in q)


# ---- the binary chunk -------------------------------------------------------------------------------------------------------------------
def _put(glb, accessor_index, array, component_type):
    """Give an accessor new contents in a buffer view of its own, added at the end of the binary chunk (tightly packed)."""
    acc = glb.json["accessors"][accessor_index]
    old = glb.json["bufferViews"][acc["bufferView"]]
    raw = np.ascontiguousarray(array).tobytes()
    glb.bin = glb.bin + bytes(-len(glb.bin) % 4)
    view = {"buffer": 0, "byteOffset": len(glb.bin), "byteLength": len(raw)}
    glb.bin = glb.bin + raw
    if "target" in old:
        view["target"] = old["target"]
    glb.json["bufferViews"].append(view)
    acc.update(bufferView=len(glb.json["bufferViews"]) - 1, byteOffset=0, componentType=component_type, count=int(len(array)))
    for key in ("sparse", "min", "max"):
        acc.pop(key, None)


def _view_references(value, found):
    """Every buffer view number anything in the file points at (accessors, their sparse parts, images, extensions: a key called bufferView)."""
    if isinstance(value, dict):
        for key, inner in value.items():
            if key == "bufferView" and isinstance(inner, int):
                found.add(inner)
            else:
                _view_references(inner, found)
    elif isinstance(value, list):
        for inner in value:
            _view_references(inner, found)


def _renumber(value, new):
    if isinstance(value, dict):
        for key, inner in value.items():
            if key == "bufferView" and isinstance(inner, int):
                value[key] = new[inner]
            else:
                _renumber(inner, new)
    elif isinstance(value, list):
        for inner in value:
            _renumber(inner, new)


def _drop_unused_views(glb):
    """Take out the buffer views nothing points at any more (the skin weights and bind matrices that were replaced) and lay the binary chunk out again, keeping everything else exactly
    (sparse morph targets included: Glb.dumps would drop their views)."""
    doc = glb.json
    rest = {key: value for key, value in doc.items() if key != "bufferViews"}
    found = set()
    _view_references(rest, found)
    keep = [i for i in range(len(doc["bufferViews"])) if i in found]
    if len(keep) == len(doc["bufferViews"]):
        return
    new = {old: i for i, old in enumerate(keep)}
    blob = bytearray()
    views = []
    for old in keep:
        view = dict(doc["bufferViews"][old])
        start = view.get("byteOffset", 0)
        part = glb.bin[start:start + view["byteLength"]]
        blob += bytes(-len(blob) % 4)
        view["buffer"], view["byteOffset"] = 0, len(blob)
        views.append(view)
        blob += part
    blob += bytes(-len(blob) % 4)
    _renumber(rest, new)
    doc["bufferViews"] = views
    doc["buffers"] = [{"byteLength": len(blob)}]
    glb.bin = bytes(blob)


# ---- what is asked for ---------------------------------------------------------------------------------------------------------------
DEFAULTS = {"intensity": 1.0, "chest": 1.0, "butt": 1.0, "belly": 0.0, "thighs": 0.0, "softness": 0.5}


def clean(raw, where="soft"):
    """A request for soft-body motion, checked for its shape: {} for none (None, False, {} or every part at nothing), else {"intensity", "chest", "butt", "belly", "thighs", "softness"}, each a
    number from 0 to 1 (True, or a key left out, takes the default of DEFAULTS: the chest and the butt on, the belly and the thighs off until asked for). `intensity` and each part's own number
    multiply: the share of the skin's weight a part may hand to its spring bones, at the apex; `softness` makes the springs firmer (0) or softer (1) than suggested. ValueError naming `where.field`."""
    if raw is None or raw is False or raw == {}:
        return {}
    if raw is True:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError(f"{where} must be true, false or an object")
    unknown = sorted(set(raw) - set(DEFAULTS))
    if unknown:
        raise ValueError(f"{where}.{unknown[0]}: unknown field")
    out = {}
    for key, default in DEFAULTS.items():
        value = raw.get(key, default)
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError(f"{where}.{key}: expected a number from 0 to 1")
        out[key] = float(value)
    return out if amounts(out) else {}


def amounts(request):
    """{part: how much of the skin's weight on the parent bone it may hand over (0 to 1)} for the parts a cleaned request makes: the intensity times the part's own number, parts at nothing left out."""
    if not request:
        return {}
    made = {part: round(float(request.get(part, 0.0)) * float(request.get("intensity", 1.0)), 4) for part in PARTS}
    return {part: value for part, value in made.items() if value > 0.0}


def bones_by_part():
    """{part: how many bones it adds to the skeleton}: a chain of two on each side (the belly: the one in the middle)."""
    return {part: len(sides_of(part)) * BONES_PER_CHAIN for part in PARTS}


def bones_per_part():
    """How many bones a part on both sides adds to the skeleton (the chest, the butt, the thighs): a chain on each side."""
    return 2 * BONES_PER_CHAIN


def info():
    """For the card: {label, parts, bones (a part on both sides), bones_by_part, defaults, note}: the words and the numbers its tick box and sliders need."""
    return {"label": "Soft-body motion", "parts": dict(LABELS), "bones": bones_per_part(), "bones_by_part": bones_by_part(), "defaults": dict(DEFAULTS),
            "note": f"Jiggle, as Skyrim calls it. Off unless you choose it. Adds bones to the skeleton for each part chosen ({bones_per_part()} for the chest, the butt and the thighs: two on each side; "
                    f"{bones_by_part()['belly']} for the belly) and lets the skin around them follow, so the game engine can make them move a little as the character walks, runs or dances (in Godot, "
                    "examples/godot/forge_secondary_motion.gd sets it up from the file). Intensity is how much of the skin follows the bones; Softness how loosely they swing (firm: small and quick; "
                    "soft: bigger and slower). For the female body only. Once a body is made, Preview the jiggle shows it moving with these sliders live. Clothes tried on from the Items tab then take about a minute, not half a minute: the body is built again to fit them."}


# ---- the new bones --------------------------------------------------------------------------------------------------------------------
def firmness(softness):
    """The factor on every spring's stiffness for a softness from 0 (firm: 2.8 times as stiff) through 0.5 (as suggested) to 1 (very soft: 0.35 times)."""
    return 2.0 ** (1.5 * (1.0 - 2.0 * min(1.0, max(0.0, float(softness)))))


def apply(data, amounts, settings=None, softness=0.5):
    """(the file with soft-body spring bones, a report). `amounts` is {"chest": 0..1, "butt": 0..1, "belly": 0..1, "thighs": 0..1}: the share of the skin's weight on the parent bone that each part
    may hand to its spring bones at the apex (0 or missing: that part is not made). `softness` (0 to 1) makes the springs softer or firmer than suggested (`firmness`); `settings` overrides
    SETTINGS for a part ({"chest": {"stiffness": 1.0}}) and is not scaled by it. SoftBodyError when the file cannot take them."""
    wanted = {part: max(0.0, min(1.0, float(amounts.get(part) or 0.0))) for part in PARTS}
    if not any(wanted.values()):
        return data, {"made": {}, "bones": 0}
    glb = Glb(data)
    doc = glb.json
    extras = (doc.get("asset") or {}).get("extras")
    why = problem(extras)
    if why:
        raise SoftBodyError(why)
    if "forge_soft" in extras:
        raise SoftBodyError("This body already has soft-body bones: build it again from its recipe to change them.")
    if len(doc.get("skins", [])) != 1:
        raise SoftBodyError("Soft-body bones need a body with one skin.")
    skin = doc["skins"][0]
    joints = list(skin["joints"])
    names = [doc["nodes"][j]["name"] for j in joints]
    if any(name not in names for name in REQUIRED_BONES) or any(parent_of(part, tag) not in names for part in PARTS if wanted[part] > 0.0 for tag, _ in sides_of(part)):
        raise SoftBodyError("Soft-body bones need the body's own spine, pelvis and thigh bones.")
    for part in PARTS:                                                           # (the bone a part's springs move around, when it names one: asked for by name, or SETTINGS')
        centre = ({**SETTINGS[part], **((settings or {}).get(part) or {})}).get("center_bone")
        if wanted[part] > 0.0 and centre and centre not in names:
            raise SoftBodyError(f"Soft-body bones for the {part} need the body's own {centre} bone, the centre of their movement.")
    index_of = {name: i for i, name in enumerate(names)}
    above_of = {}                                                                # skin column -> the column of the joint above it in the skeleton (when that joint is in the skin)
    for child, node_index in enumerate(joints):
        for column, other in enumerate(joints):
            if node_index in doc["nodes"][other].get("children", []):
                above_of[child] = column
    world = _worlds(doc)
    bones = {name: world[j][:3, 3] for name, j in zip(names, joints)}
    scale = float((extras.get("landmarks") or {}).get("height_m") or REFERENCE_HEIGHT) / REFERENCE_HEIGHT
    meshes = _skinned(doc)
    if "ForgeBody" not in meshes:
        raise SoftBodyError("Soft-body bones need the body's own skin.")
    body = glb.accessor(meshes["ForgeBody"]["attributes"]["POSITION"]).astype(float)
    bust = extras.get(BUST_MARK) or {}                                           # (a bust made larger or smaller: the chest follows it where it is now)
    bust_size, bust_reach = float(bust.get("size") or 0.0), float(bust.get("reach_m") or 0.0)
    first_new_node = len(doc["nodes"])
    new_nodes, new_binds, chains, bone_names, plan, apexes = [], [], [], [], [], {}
    for part in PARTS:
        if wanted[part] <= 0.0:
            continue
        shape = SHAPES[part]
        apex = _apex(body, bones, part, scale, window_shift(bust) if part == "chest" else 0.0)       # (a bust the body shape hung lower is looked for lower)
        apexes[part] = [round(float(v), 4) for v in apex]
        grow = 1.0 + (BUST_GROW["larger"] if bust_size > 0 else BUST_GROW["smaller"]) * bust_size if part == "chest" else 1.0
        deeper = bust_reach if part == "chest" else 0.0
        tweak = {**SETTINGS[part], "stiffness": round(SETTINGS[part]["stiffness"] * firmness(softness), 4), **((settings or {}).get(part) or {})}
        for tag, side in sides_of(part):
            tip = np.array([side * apex[0], apex[1], apex[2]])
            if shape.get("axis", 2) == 2:
                pivot = tip - np.array([0.0, 0.0, shape["toward"] * (shape["pivot_back"] * scale + deeper)])      # (the pivot stays where it was inside the body when the bust is made larger)
                pivot[0] = side * apex[0] * 0.8                                    # the spring leans a little outward from the middle of the body
            else:
                pivot = tip - np.array([side * shape["toward"] * (shape["pivot_back"] * scale + deeper), 0.0, 0.0])      # (sticks out sideways: the pivot is in the limb, on its bone)
            aim = tip - pivot
            parent_name = parent_of(part, tag)
            parent_node = joints[index_of[parent_name]]
            parent_world = world[parent_node]
            to_local = np.linalg.inv(parent_world[:3, :3])
            first = first_new_node + len(new_nodes)
            name1, name2 = f"soft_{part}_{tag}_01", f"soft_{part}_{tag}_02"
            block = {"version": 1, "kind": part, "side": tag, "chain": f"{part}_{tag}", "bones": [name1, name2], "parent": parent_name,
                     "stiffness": tweak["stiffness"], "drag": tweak["drag"], "gravity": tweak["gravity"], "gravity_direction": [0.0, -1.0, 0.0],
                     "radius_m": tweak["radius_m"], "end_length_m": round(shape["end"] * scale, 5), "tail_axis": "+Y", "max_angle_deg": tweak["max_angle_deg"], "amount": round(wanted[part], 4),
                     "suggested_settings_not_engine_acceptance": True, **({"center_bone": tweak["center_bone"]} if tweak.get("center_bone") else {})}
            node1 = {"name": name1, "translation": [float(v) for v in _mul(to_local, pivot - parent_world[:3, 3])],
                     "rotation": list(_quaternion_y_to(_mul(to_local, aim))), "children": [first + 1], "extras": {"forge_secondary": block}}
            node2 = {"name": name2, "translation": [0.0, round(shape["link"] * scale, 6), 0.0]}
            new_nodes += [node1, node2]
            w1 = _mul(parent_world, _matrix(node1))
            w2 = _mul(w1, _matrix(node2))
            new_binds += [np.linalg.inv(w).T.reshape(16).astype(np.float32) for w in (w1, w2)]
            chains.append(block["chain"])
            bone_names += [name1, name2]
            plan.append({"part": part, "side": side, "column": len(joints) + len(bone_names) - 1, "apex": apex, "family": [index_of[b] for b in family_of(part, tag)],
                         "near": shape["near"], "parent_node": parent_node, "first_node": first, "grow": grow, "deeper": deeper})
    # ---- the skin: a share of the weight on the parent bones goes to the spring bone ---------------------------------------------------------------
    report = {"made": {part: round(wanted[part], 4) for part in PARTS if wanted[part] > 0.0}, "bones": len(bone_names), "meshes": {}, "scale": round(scale, 4), "apex": apexes}
    for name, primitive in meshes.items():
        attributes = primitive["attributes"]
        jn_accessor, wt_accessor = doc["accessors"][attributes["JOINTS_0"]], doc["accessors"][attributes["WEIGHTS_0"]]
        if wt_accessor["componentType"] != 5126 or jn_accessor["componentType"] not in (5121, 5123):
            raise SoftBodyError(f"The skin of {name} is stored in a form soft-body bones do not handle.")
        if jn_accessor["componentType"] == 5121 and len(joints) + len(bone_names) > 255:
            raise SoftBodyError("The body has too many bones for its skin to carry the new ones.")
        positions = glb.accessor(attributes["POSITION"]).astype(float)
        jn = glb.accessor(attributes["JOINTS_0"]).astype(np.int64)
        wt = glb.accessor(attributes["WEIGHTS_0"]).astype(np.float64)
        touched = skipped = 0
        biggest = 0.0
        for item in plan:
            fraction = wanted[item["part"]] * weight_of(positions, item["part"], item["apex"], scale, item["side"], item["grow"], item["deeper"])
            family = item["family"]
            for r in np.flatnonzero(fraction > 1e-3):
                entries = [[int(j), float(w)] for j, w in zip(jn[r], wt[r]) if w > 0.0]
                share = sum(w for j, w in entries if j in family)
                if share < 1e-3:
                    continue
                foreign = _foreign_weight(entries, family, names, item["near"])
                ease = 1.0 - float(_smooth(foreign / FOREIGN_FADE))
                move = share * min(float(fraction[r]), 1.0) * ease
                if move < 1e-3:
                    continue
                kept = [[j, w * (1.0 - move / share) if j in family else w] for j, w in entries]
                kept = [e for e in kept if e[1] > 1e-9]
                kept.append([item["column"], move])
                if len(kept) > 4 and share - move <= HAND_ALL:
                    kept = [[j, w] for j, w in entries if j not in family] + [[item["column"], share]]
                    move = share
                if len(kept) > 4:
                    others = [e for e in kept if e[0] < len(joints)]

                    def cost(e, at=positions[r]):
                        bone = names[e[0]]
                        return e[1] * float(np.linalg.norm(at - bones[bone])) * next((v for k, v in SWING.items() if bone.startswith(k)), 1.0)
                    cheapest = min(others, key=cost)
                    if cost(cheapest) > DROP_COST:
                        skipped += 1
                        continue
                    kept.remove(cheapest)
                    above = next((e for e in kept if e[0] == above_of.get(cheapest[0])), None) or max((e for e in kept if e[0] != item["column"]), key=lambda e: e[1])
                    above[1] += cheapest[1]
                kept.sort(key=lambda e: -e[1])
                jn[r] = [e[0] for e in kept] + [0] * (4 - len(kept))
                wt[r] = [e[1] for e in kept] + [0.0] * (4 - len(kept))
                touched += 1
                biggest = max(biggest, move)
        if touched or skipped:
            report["meshes"][name] = {"touched": touched, "skipped": skipped, "biggest_share": round(biggest, 3)}
        if touched:
            _put(glb, attributes["JOINTS_0"], jn.astype(np.uint8 if jn_accessor["componentType"] == 5121 else np.uint16), jn_accessor["componentType"])
            _put(glb, attributes["WEIGHTS_0"], wt.astype(np.float32), 5126)
    # ---- the skeleton: the nodes, the joints, the bind matrices, and what the file says it holds ---------------------------------------------------
    for item in plan:
        doc["nodes"][item["parent_node"]].setdefault("children", []).append(item["first_node"])
    doc["nodes"].extend(new_nodes)
    skin["joints"] = joints + [first_new_node + i for i in range(len(new_nodes))]
    binds = glb.accessor(skin["inverseBindMatrices"]).astype(np.float32)
    _put(glb, skin["inverseBindMatrices"], np.vstack([binds, np.array(new_binds, np.float32)]), 5126)
    extras["forge_soft"] = {"version": 1, "parts": {part: round(wanted[part], 4) for part in PARTS if wanted[part] > 0.0}, "softness": round(float(softness), 4), "chains": chains, "bone_names": bone_names,
                            "joints_before": len(joints), "joints": len(joints) + len(bone_names), "families": {part: family_names(part) for part in PARTS if wanted[part] > 0.0},
                            **({"bust_size": round(bust_size, 4)} if bust_size else {})}
    _drop_unused_views(glb)
    return glb.dumps(), report


# ---- looking at a file with soft-body bones -----------------------------------------------------------------------------------------------
def _expected_names(parts):
    return [f"soft_{part}_{tag}_{n}" for part in PARTS if part in parts for tag, _ in sides_of(part) for n in ("01", "02")]


def without(doc):
    """A copy of a file's glTF JSON as it was before the soft-body step: the new nodes and joints and the mark in the extras taken out (for checks that compare the file with the body's own, or
    with the file the Body Designer's finalizer made; the binary chunk is not looked at). A file without the mark comes back as a plain copy."""
    out = copy.deepcopy(doc)
    extras = (out.get("asset") or {}).get("extras") or {}
    soft = extras.pop("forge_soft", None)
    if not isinstance(soft, dict):
        return out
    skin = out["skins"][0]
    keep = int(soft["joints_before"])
    gone = set(skin["joints"][keep:])
    skin["joints"] = skin["joints"][:keep]
    out["nodes"] = out["nodes"][:min(gone)]
    for node in out["nodes"]:
        if "children" in node:
            left = [c for c in node["children"] if c not in gone]
            if left:
                node["children"] = left
            else:
                del node["children"]
    return out


def undo(data):
    """The GLB as it was before the soft-body step, give or take the step's own tolerance (`DROP_COST`): the spring bones, their nodes and their bind matrices are taken out, the skin weight each
    one held goes back to the bone it hangs on, and the mark `forge_soft` is removed. A file that has none comes back as it is. The page's jiggle preview uses this to start from the same
    body whatever it was built with (it adds all four parts again at full strength: `apply`). A vertex that gave up the share of a fifth bone to make room (see `DROP_COST`) does not get it
    back, so the skin of the file that comes out can differ from the one that went in by what that cost (at most 5 mm in the 85 clips). SoftBodyError for a file whose soft-body bones are not
    the ones this module lays."""
    glb = Glb(data)
    doc = glb.json
    soft = ((doc.get("asset") or {}).get("extras") or {}).get("forge_soft")
    if not isinstance(soft, dict):
        return data
    if len(doc.get("skins", [])) != 1:
        raise SoftBodyError("Soft-body bones are taken out of a body with one skin.")
    skin = doc["skins"][0]
    joints = list(skin["joints"])
    names = [str(doc["nodes"][j].get("name", "")) for j in joints]
    keep = soft.get("joints_before")
    chains = {f"{part}_{tag}": parent_of(part, tag) for part in PARTS for tag, _ in sides_of(part)}
    if type(keep) is not int or keep + len(soft.get("bone_names") or []) != len(joints) or names[keep:] != soft.get("bone_names"):
        raise SoftBodyError("The soft-body bones of this body are not the ones this program lays: build the body again.")
    back = {}                                                                    # a spring bone's column -> the column of the bone it hangs on
    for column in range(keep, len(joints)):
        chain = names[column][len("soft_"):].rsplit("_", 1)[0]
        if not names[column].startswith("soft_") or chains.get(chain) not in names[:keep]:
            raise SoftBodyError("The soft-body bones of this body are not the ones this program lays: build the body again.")
        back[column] = names.index(chains[chain])
    for name, primitive in _skinned(doc).items():
        attributes = primitive["attributes"]
        jn_accessor = doc["accessors"][attributes["JOINTS_0"]]
        jn = glb.accessor(attributes["JOINTS_0"]).astype(np.int64)
        wt = glb.accessor(attributes["WEIGHTS_0"]).astype(np.float64)
        held = (jn >= keep) & (wt > 0.0)
        for r in np.flatnonzero(held.any(axis=1)):
            merged = {}
            for j, w in zip(jn[r], wt[r]):
                if w > 0.0:
                    column = back.get(int(j), int(j))
                    merged[column] = merged.get(column, 0.0) + float(w)
            ordered = sorted(merged.items(), key=lambda e: -e[1])
            jn[r] = [j for j, _ in ordered] + [0] * (4 - len(ordered))
            wt[r] = [w for _, w in ordered] + [0.0] * (4 - len(ordered))
        if held.any():
            jn[(jn >= keep) & (wt <= 0.0)] = 0
            _put(glb, attributes["JOINTS_0"], jn.astype(np.uint8 if jn_accessor["componentType"] == 5121 else np.uint16), jn_accessor["componentType"])
            _put(glb, attributes["WEIGHTS_0"], wt.astype(np.float32), 5126)
    binds = glb.accessor(skin["inverseBindMatrices"]).astype(np.float32)
    _put(glb, skin["inverseBindMatrices"], binds[:keep], 5126)
    glb.json = without(glb.json)
    _drop_unused_views(glb)
    return glb.dumps()


def check(doc, parts):
    """Why a file's glTF JSON is not a body with the soft-body bones of `parts` (a set of "chest", "butt", "belly" and "thighs") added, in words, or None: they must be the last joints of the
    skin, in the order this module lays them, each chain's first bone hung on the bone it is made for with the settings the engine reads, its second bone below it, and moved by no clip."""
    wrong = "The animated body has not got the soft-body bones that were asked for"
    extras = (doc.get("asset") or {}).get("extras") or {}
    soft = extras.get("forge_soft")
    parts = set(parts)
    if not isinstance(soft, dict) or set(soft.get("parts") or {}) != parts or not parts <= set(PARTS):
        return wrong
    skin = doc["skins"][0]
    nodes = doc["nodes"]
    joints = skin["joints"]
    names = [str(nodes[j].get("name", "")) for j in joints]
    expected = _expected_names(parts)
    before = soft.get("joints_before")
    if (soft.get("bone_names") != expected or type(before) is not int or before + len(expected) != len(joints) or names[before:] != expected or soft.get("joints") != len(joints)
            or len(set(names)) != len(names) or sorted(soft.get("chains") or []) != sorted(f"{part}_{tag}" for part in PARTS if part in parts for tag, _ in sides_of(part))):
        return wrong
    above = {child: i for i, node in enumerate(nodes) for child in node.get("children", [])}
    for part in PARTS:
        if part not in parts:
            continue
        for tag, _ in sides_of(part):
            first, second = (joints[names.index(f"soft_{part}_{tag}_{n}")] for n in ("01", "02"))
            block = (nodes[first].get("extras") or {}).get("forge_secondary") or {}
            hung_on = nodes[above[first]].get("name") if first in above else None
            if hung_on != parent_of(part, tag) or above.get(second) != first:
                return f"The soft-body bones of the {part} are not hung on the {parent_of(part, tag)} as they should be"
            if block.get("chain") != f"{part}_{tag}" or block.get("kind") != part or block.get("bones") != [f"soft_{part}_{tag}_01", f"soft_{part}_{tag}_02"]:
                return f"The soft-body bones of the {part} do not carry their settings"
            if block.get("center_bone") is not None and block["center_bone"] not in names[:before]:
                return f"The soft-body bones of the {part} name a centre bone the body has not got"
    new = set(joints[before:])
    for animation in doc.get("animations") or []:
        if any((channel.get("target") or {}).get("node") in new for channel in animation.get("channels") or []):
            return "A clip moves the soft-body bones, which only the game engine should"
    return None


def check_file(data, parts):
    """Why a GLB is not a body with the soft-body bones of `parts` (`check`, and the numbers behind the bones: each bone's bind matrix puts it at rest where it hangs, every skin that follows one has
    weights that add up to 1, and some of the body's skin does follow each part), in words, or None."""
    glb = Glb(data)
    doc = glb.json
    why = check(doc, parts)
    if why:
        return why
    skin = doc["skins"][0]
    joints = skin["joints"]
    soft = doc["asset"]["extras"]["forge_soft"]
    first = soft["joints_before"]
    world = _worlds(doc)
    binds = glb.accessor(skin["inverseBindMatrices"]).astype(float).reshape(-1, 4, 4).transpose(0, 2, 1)
    if len(binds) != len(joints):
        return "The soft-body bones have no bind matrices of their own"
    for column in range(first, len(joints)):
        if not np.allclose(_mul(world[joints[column]], binds[column]), np.eye(4), atol=1e-3):
            return "The soft-body bones' bind matrices do not put them at rest where they hang"
    names = [str(doc["nodes"][j].get("name", "")) for j in joints]
    for name, primitive in _skinned(doc).items():
        jn = glb.accessor(primitive["attributes"]["JOINTS_0"]).astype(np.int64)
        wt = glb.accessor(primitive["attributes"]["WEIGHTS_0"]).astype(np.float64)
        if jn.max() >= len(joints) or wt.min() < -1e-6:
            return f"The skin of {name} points at a bone that is not there"
        soft_rows = np.isin(jn, np.arange(first, len(joints))) & (wt > 0.0)
        rows = soft_rows.any(axis=1)
        if rows.any() and np.abs(wt[rows].sum(axis=1) - 1.0).max() > 2e-3:
            return f"The skin weights of {name} no longer add up to 1"
        if name == "ForgeBody":
            for part in parts:
                columns = [i for i, n in enumerate(names) if n.startswith(f"soft_{part}_")]
                if not (np.isin(jn, columns) & (wt > 0.0)).any():
                    return f"No skin of the body follows the soft-body bones of the {part}"
    return None
