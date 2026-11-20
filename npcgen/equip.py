"""Put a held item or a hat on a rigged character, docs/equip.md.

A sword goes into a hand, a shield on a forearm, a hat or helmet on the head. The item (a model from the Items tab, already scaled to metres with
its origin at the grip, the middle or the base: npcgen/items.py `fit_glb`) is put inside the character's file under the bone that carries it
(npcgen/glbmerge.py), so it follows every animation clip, and the bone and the transform are returned too, for a game that would rather attach the
item itself. Nothing of the character is rewritten and nothing is kept on it: the result is a preview and a download, as an outfit tried on is.

Where the item goes is worked out from the character's rest pose alone (no GPU, no Blender): the skin weights say which vertices belong to the head
and to each hand, and the joints give the forearms. The character stands on +Y and faces +Z (its right hand is on -X); which way it faces is read
from where its hands are, so a rig built the other way round is not put wrong. Hats are scaled to the head (an adult's is 155 mm wide), held items
are not: a sword is a metre long whoever holds it. Every placement has a nudge (up, forward, side, turn, tilt, size) for what the rules get wrong.
"""
import math

import numpy as np

from . import glbmerge
from .paint3d import Glb, all_triangles
from .procmesh import matmul

SLOTS = {"hand_r": "right hand", "hand_l": "left hand", "head": "head"}
ADULT_HEAD_WIDTH_M = 0.155            # what the hat sizes in data/items.json assume
NUDGE_LIMITS = {"up": (-0.2, 0.2), "forward": (-0.2, 0.2), "side": (-0.2, 0.2), "turn": (-180.0, 180.0), "tilt": (-90.0, 90.0),
                "size": (0.4, 2.5)}
NUDGE_DEFAULT = {"up": 0.0, "forward": 0.0, "side": 0.0, "turn": 0.0, "tilt": 0.0, "size": 1.0}

# How each kind of item is carried. "blade": gripped across the palm with its long axis forward and up (a sword, an axe); "pole": gripped and
# standing up (a spear, a staff, a torch); "bow": the left hand holds it upright in its middle; "shield": strapped on the forearm, facing forward;
# "hat": on the head. Kinds not listed fall back on their shape (see support). A crossbow, a pistol and a rifle are drawn from the side and their muzzle
# could be at either end of the model, a lantern, a bottle or a book needs a grip of its own and gauntlets need fitting: none of those yet.
CARRY = {"sword": "blade", "cutlass": "blade", "dagger": "blade", "axe": "blade", "hammer": "blade", "spear": "pole", "staff": "pole",
         "torch": "pole", "bow": "bow", "shield": "shield", "helmet": "hat", "hat": "hat", "cap": "hat", "crown": "hat"}
BLADE_PITCH = 35.0                    # degrees above level
POLE_LEAN = 0.12                      # how far forward a held pole leans (tan of the angle)
# How headwear sits on the head. It is sized so that the width of the part that goes round the head (`fit`: its share of the head's width) matches the
# head, not by the size the item was made at: a helmet made 30 cm tall is a good deal more than a head holds. A helmet is anchored by its top (the dome
# sits on the skull, `lift` metres above the crown for an adult; the cheek and neck guards hang as far as they hang), a hat or a crown by its base (the
# band rests `drop` of the head's height below the crown, and the crown of the hat stands above the head as far as it stands).
HEADWEAR = {"helmet": {"anchor": "top", "lift": 0.012, "fit": 1.15}, "hat": {"anchor": "base", "drop": 0.27, "fit": 1.15},
            "cap": {"anchor": "base", "drop": 0.27, "fit": 1.12}, "crown": {"anchor": "base", "drop": 0.16, "fit": 1.05}}
MIN_VERTICES = 12                     # fewer head or hand vertices than this and the placement falls back on the joints


class EquipError(ValueError):
    """The item cannot be put on this character. The message is safe to show."""


# ---- what can be equipped ------------------------------------------------------------------------------------------------------
def support(kind_id, spec):
    """How a kind of item is carried: {"carry", "slot"}, or None when equipping it is not done yet. `spec` is the kind's entry in data/items.json."""
    attach = str(spec.get("attach") or "").lower()
    pivot, pose = spec.get("pivot"), spec.get("pose")
    carry = CARRY.get(kind_id)
    if carry is None:                                               # a kind of the table's own, or "Other": go by what it is
        if attach in ("hand_r", "hand_l") and pivot == "grip" and pose == "upright":
            carry = "blade"
        elif attach == "head" and pivot == "base" and pose == "single":
            carry = "hat"
    if carry is None or attach not in ("hand_r", "hand_l", "head"):
        return None
    return {"carry": carry, "slot": attach}


def slots_for(carry):
    """The slots an item that is carried this way can go in."""
    return ("head",) if carry == "hat" else ("hand_l",) if carry in ("shield", "bow") else ("hand_r", "hand_l")


def clean_nudge(raw):
    """The nudge as numbers within their limits, the neutral one for what is missing. ValueError for what is not a number or is out of range."""
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError("The nudge must be an object")
    out = dict(NUDGE_DEFAULT)
    for key, (low, high) in NUDGE_LIMITS.items():
        value = raw.get(key)
        if value in (None, ""):
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            raise ValueError(f"The nudge's {key} must be a number")
        try:
            number = float(value)
        except ValueError:
            raise ValueError(f"The nudge's {key} must be a number") from None
        if not low <= number <= high:
            raise ValueError(f"The nudge's {key} must be between {low:g} and {high:g}")
        out[key] = number
    return out


# ---- the character's rest pose ---------------------------------------------------------------------------------------------------
class Rest:
    """What placing things needs of a character: joints, the head's box, the palms, which way is forward."""

    def __init__(self, joints, head, palms, height, up, forward, right, notes, bones=None, hand_front=None):
        self.joints, self.head, self.palms, self.height = joints, head, palms, height
        self.up, self.forward, self.right, self.notes = up, forward, right, notes
        self.bones = bones or {}                 # {bone name: (its node's name in the file, its world matrix at rest)} for the bones things hang from
        self.hand_front = hand_front or {}       # {hand: how far forward (along `forward`) the hand's vertices reach}, so a shield can stand in front of it

    @property
    def unit(self):
        return self.height / 1.7


def _unit(v):
    n = float(np.linalg.norm(v))
    return np.asarray(v, float) / n if n > 1e-12 else np.asarray(v, float)


def _weights(glb, accessor_index):
    acc = glb.json["accessors"][accessor_index]
    values = glb.accessor(accessor_index).astype(np.float64)
    if acc.get("normalized"):
        values /= {5121: 255.0, 5123: 65535.0}.get(acc["componentType"], 1.0)
    return values


def read_rest(character_glb):
    """A `Rest` from a rigged character's GLB bytes. EquipError for a model without a skeleton or without a head and hands to put things on."""
    glb = Glb(character_glb) if isinstance(character_glb, (bytes, bytearray)) else character_glb
    doc = glb.json
    nodes = doc.get("nodes", [])
    if not doc.get("skins"):
        raise EquipError("This character's 3D model has no skeleton: make its skeleton first (the rig step), then equip it")
    world = glbmerge.world_matrices(doc)
    index = {}
    wanted = ("head", "hand_r", "hand_l", "lowerarm_r", "lowerarm_l", "pelvis", "neck_01")
    for name in wanted:
        found = glbmerge.find_node(doc, name)                      # (a joint of the skeleton before a mesh of the same name)
        if found is not None:
            index[name] = found
    joints = {name: world[i][:3, 3].copy() for name, i in index.items() if i in world}
    missing = [name for name in ("head", "hand_r", "hand_l") if name not in joints]
    if missing:
        raise EquipError("This character's skeleton has no " + " or ".join(missing) + " joint: equip needs the animation library's bone names")
    # the skinned vertices: where each one is at rest, and how much of it each of the head and the hands carries
    everything, picked = [], {name: [] for name in ("head", "hand_r", "hand_l")}
    for node in nodes:
        if "mesh" not in node or "skin" not in node:
            continue
        joint_nodes = np.array(doc["skins"][node["skin"]]["joints"], np.int64)
        for prim in doc["meshes"][node["mesh"]]["primitives"]:
            attrs = prim.get("attributes", {})
            if not {"POSITION", "JOINTS_0", "WEIGHTS_0"} <= set(attrs):
                continue
            pos = glb.accessor(attrs["POSITION"]).astype(np.float64)
            carried_by = joint_nodes[np.clip(glb.accessor(attrs["JOINTS_0"]).astype(np.int64), 0, len(joint_nodes) - 1)]
            weight = _weights(glb, attrs["WEIGHTS_0"])
            everything.append(pos)
            for name in picked:
                share = (weight * (carried_by == index[name])).sum(axis=1)
                picked[name].append(pos[share > 0.5])
    if not everything:
        raise EquipError("This character's model has no skinned mesh to measure")
    body = np.vstack(everything)
    height = float(body[:, 1].max() - body[:, 1].min())
    if not height > 0.05:
        raise EquipError("This character's model has no size")
    picked = {name: (np.vstack(parts) if parts else np.zeros((0, 3))) for name, parts in picked.items()}
    notes = []
    up = np.array([0.0, 1.0, 0.0])
    side = joints["hand_r"] - joints["hand_l"]
    side[1] = 0.0
    if np.linalg.norm(side) < 0.02 * height:
        right, forward = np.array([-1.0, 0.0, 0.0]), np.array([0.0, 0.0, 1.0])
        notes.append("the hands are too close to tell which way the character faces: it is taken to face +Z")
    else:
        right = _unit(side)
        forward = _unit(np.cross(up, right))
    # the head
    unit = height / 1.7
    top = joints["head"]
    pts = picked["head"]
    if len(pts) >= MIN_VERTICES:
        crown, bottom = float(np.percentile(pts[:, 1], 99.95)), float(np.percentile(pts[:, 1], 0.3))
        span = crown - bottom
        slices = []                                                  # the skull, from just under the crown to below the ears: its widest part is its width
        for share in np.linspace(0.12, 0.6, 9):
            band = pts[np.abs(pts[:, 1] - (crown - share * span)) < 0.03 * span]
            if len(band) >= MIN_VERTICES:
                (lo_x, hi_x), (lo_z, hi_z) = np.percentile(band[:, 0], [1, 99]), np.percentile(band[:, 2], [1, 99])
                slices.append((hi_x - lo_x, (lo_x + hi_x) / 2, lo_z, hi_z, share))
        if not slices:
            slices = [(float(np.ptp(pts[:, 0])), float(pts[:, 0].mean()), float(pts[:, 2].min()), float(pts[:, 2].max()), 0.3)]
        upper = [t for t in slices if t[4] <= 0.4] or slices          # (below that the nose and the chin pull the middle forward)
        head = {"crown": crown, "height": span, "width": float(max(t[0] for t in slices)), "depth": float(max(t[3] - t[2] for t in slices)),
                "centre": np.array([float(np.mean([t[1] for t in slices])), 0.0, float(np.mean([(t[2] + t[3]) / 2 for t in upper]))])}
    else:
        notes.append("few vertices belong to the head joint: its size is guessed from the character's height")
        head = {"crown": float(top[1] + 0.12 * unit), "height": 0.23 * unit, "width": ADULT_HEAD_WIDTH_M * unit, "depth": 0.19 * unit,
                "centre": np.array([top[0], 0.0, top[2]])}
    # the palms: the middle of what each hand joint carries, else a third of the way along the forearm past the wrist
    palms = {}
    for name, arm in (("hand_r", "lowerarm_r"), ("hand_l", "lowerarm_l")):
        pts = picked[name]
        if len(pts) >= MIN_VERTICES:
            palms[name] = pts.mean(axis=0)
        else:
            notes.append(f"few vertices belong to {name}: the palm is taken from the forearm")
            forearm = joints[name] - joints.get(arm, joints[name] - np.array([0.0, 0.25 * unit, 0.0]))
            palms[name] = joints[name] + 0.35 * forearm
    bones = {name: (doc["nodes"][index[name]].get("name"), world[index[name]].copy()) for name in ("head", "hand_r", "hand_l") if index[name] in world}
    reach = {name: float(np.dot(picked[name], forward).max()) for name in ("hand_r", "hand_l") if len(picked[name]) >= MIN_VERTICES}
    return Rest(joints, head, palms, height, up, forward, right, notes, bones, reach)


# ---- the item ------------------------------------------------------------------------------------------------------------------------
class Shape:
    """An item's geometry in its own space (metres, the origin where its kind puts it): its bounds, the width of the part that goes round a head, and
    the axis of its dome."""

    def __init__(self, points):
        points = np.asarray(points, float)
        if not len(points):
            raise EquipError("The item has no mesh")
        self.low, self.high = points.min(axis=0), points.max(axis=0)
        self.extent = self.high - self.low
        # the top and the bottom as most of the mesh sees them: a thin crest or a stray point does not decide where a hat stands
        self.top, self.bottom = float(np.percentile(points[:, 1], 99.5)), float(np.percentile(points[:, 1], 0.5))
        height = max(float(self.extent[1]), 1e-9)

        def band(share, half=0.04):
            return points[np.abs(points[:, 1] - (self.low[1] + share * height)) < half * height]

        widths = []
        for share in (0.1, 0.3, 0.5, 0.7):                          # a hat's brim is wider than the head and its crown is not: the middle of these is the crown
            rows = band(share)
            if len(rows) >= 8:
                widths.append(float(np.percentile(rows[:, 0], 99) - np.percentile(rows[:, 0], 1)))
        self.fit_width = float(np.median(widths)) if widths else float(self.extent[0])
        dome = points[(points[:, 1] >= self.low[1] + 0.5 * height) & (points[:, 1] <= self.low[1] + 0.8 * height)]
        both = dome if len(dome) >= 8 else points
        self.axis = np.array([float(both[:, 0].mean()), float(both[:, 2].mean())])


def item_shape(item_glb):
    """The `Shape` of an item's GLB (bytes, or a Glb): every mesh, in the item's space."""
    glb = Glb(item_glb) if isinstance(item_glb, (bytes, bytearray)) else item_glb
    points, _triangles = all_triangles(glb)
    return Shape(points)


# ---- placing -------------------------------------------------------------------------------------------------------------------------
def _frame(long_axis, face_hint):
    """A rotation whose y axis is `long_axis` and whose z axis is as near `face_hint` as that allows (columns x, y, z)."""
    y = _unit(long_axis)
    z = np.asarray(face_hint, float) - y * float(np.dot(face_hint, y))
    if np.linalg.norm(z) < 1e-6:                                    # the hint lies along the axis: any perpendicular will do
        z = np.cross(y, [1.0, 0.0, 0.0] if abs(y[0]) < 0.9 else [0.0, 0.0, 1.0])
    z = _unit(z)
    return np.column_stack([np.cross(y, z), y, z])


def _turned(vector, axis, degrees):
    """`vector` turned about `axis` by `degrees` (Rodrigues' formula)."""
    a, k = math.radians(degrees), _unit(axis)
    v = np.asarray(vector, float)
    return v * math.cos(a) + np.cross(k, v) * math.sin(a) + k * float(np.dot(k, v)) * (1 - math.cos(a))


def place(rest, carry, slot, kind_id, shape, nudge=None):
    """Where an item's origin stands in the character's world space: a 4x4 matrix (rotation and uniform scale, then the position) and the scale used.
    `shape` is the item's `Shape`, `nudge` is clean_nudge's."""
    nudge = clean_nudge(nudge)
    if slot not in SLOTS or slot not in slots_for(carry):
        raise EquipError(f"A {carry} cannot go in the {SLOTS.get(slot, slot)}")
    up, forward = rest.up, rest.forward
    outward = rest.right if slot == "hand_r" else -rest.right
    scale = nudge["size"]
    if carry == "hat":
        head, spec = rest.head, HEADWEAR.get(kind_id) or HEADWEAR["hat"]
        scale *= float(np.clip(spec["fit"] * head["width"] / max(shape.fit_width, 1e-6), 0.3, 4.0))
        face = _turned(forward, up, nudge["turn"])
        axis = _turned(up, rest.right, nudge["tilt"])
        rot = _frame(axis, face)
        height = (head["crown"] + spec["lift"] * rest.unit - shape.top * scale) if spec["anchor"] == "top" else                  (head["crown"] - spec["drop"] * head["height"] - shape.bottom * scale)
        dome = matmul(rot * scale, np.array([shape.axis[0], 0.0, shape.axis[1]]))       # the dome's axis, to be put over the middle of the skull
        origin = np.array([head["centre"][0] - dome[0], height, head["centre"][2] - dome[2]])
    elif carry == "shield":
        near, wrist = rest.joints.get("lowerarm_l"), rest.joints["hand_l"]
        middle = wrist if near is None else (near + wrist) / 2
        depth = float(shape.extent[2]) * scale
        shift = 0.5 * depth + 0.05 * rest.unit
        if "hand_l" in rest.hand_front:                              # the shield's back face stands just beyond the hand, as if the hand held it from behind
            shift = max(shift, rest.hand_front["hand_l"] + 0.01 * rest.unit + 0.5 * depth - float(np.dot(middle, forward)))
        origin = middle + forward * shift + outward * 0.03 * rest.unit
        face = _turned(forward, up, nudge["turn"])
        axis = _turned(up, outward, nudge["tilt"])
        rot = _frame(axis, face)
    else:
        origin = rest.palms[slot]
        if carry == "blade":
            pitch = math.radians(BLADE_PITCH + nudge["tilt"])
            axis = forward * math.cos(pitch) + up * math.sin(pitch)
        elif carry == "pole":
            axis = _turned(_unit(up + forward * POLE_LEAN), outward, -nudge["tilt"])
        else:                                                       # a bow
            axis = _turned(up, outward, -nudge["tilt"])
        face = _turned(forward if carry == "bow" else outward, axis, nudge["turn"])
        rot = _frame(axis, face)
    origin = origin + up * nudge["up"] * rest.unit + forward * nudge["forward"] * rest.unit + rest.right * nudge["side"] * rest.unit
    world = np.eye(4)
    world[:3, :3] = rot * scale
    world[:3, 3] = origin
    return world, {"scale": round(float(scale), 4)}


def attach_block(rest, slot, world):
    """What a game needs to hang something at `world` (4x4, the character's world space) from the bone of `slot`: {"bone", "translation", "rotation", "scale"} in that
    bone's own space (or {"bone", "matrix"} with a shear), the same numbers the merged file holds."""
    name, bone_world = rest.bones[slot if slot != "head" else "head"]
    local = matmul(np.linalg.inv(bone_world), np.asarray(world, float))
    out = {"bone": name}
    parts = glbmerge.decompose(local)
    if parts is None:
        out["matrix"] = [float(v) for v in local.T.reshape(16)]
    else:
        out["translation"], out["rotation"], out["scale"] = ([round(float(v), 6) for v in part] for part in parts)
    return out


def describe(rest, kind_id, spec, slot, shape, nudge=None):
    """Where an item goes, worked out without touching any file: {"item kind", "slot", "bone", "attach", "scale", "nudge"}. EquipError when it cannot go there."""
    how = support(kind_id, spec or {})
    if how is None:
        raise EquipError(f"Equipping a {kind_id or 'thing'} is not done yet")
    slot = slot or how["slot"]
    if slot not in slots_for(how["carry"]):
        raise EquipError(f"A {kind_id} cannot go in the {SLOTS.get(slot, slot)}")
    nudge = clean_nudge(nudge)
    world, info = place(rest, how["carry"], slot, kind_id, shape, nudge)
    attach = attach_block(rest, slot, world)
    return {"kind": kind_id, "slot": slot, "bone": attach["bone"], "attach": attach, "scale": info["scale"], "nudge": nudge, "world": world}


# ---- putting it together ------------------------------------------------------------------------------------------------------------
def equip(character_glb, wearing, rest=None):
    """The character's file with items put on it. `wearing` is a list of {"glb": the item's bytes, "kind": its kind id, "spec": the kind's entry in
    data/items.json, "slot": "hand_r", "hand_l" or "head" (None: the kind's usual one), "nudge": {...}, "item": its id (for the tag)}. Returns (GLB bytes,
    {"items": [{"item", "kind", "slot", "bone", "attach": {bone, translation, rotation, scale}, "scale", "nudge"}], "notes": [...]}). EquipError when something
    cannot go where it was asked, naming it."""
    if not wearing:
        raise EquipError("Nothing to equip")
    rest = rest or read_rest(character_glb)
    taken, reports, data = {}, [], character_glb
    for entry in wearing:
        spec = entry.get("spec") or {}
        how = support(entry.get("kind"), spec)
        label = entry.get("name") or entry.get("kind") or "The item"
        if how is None:
            raise EquipError(f"{label}: equipping a {entry.get('kind') or 'thing'} is not done yet")
        slot = entry.get("slot") or how["slot"]
        if slot not in slots_for(how["carry"]):
            raise EquipError(f"{label}: a {entry.get('kind')} cannot go in the {SLOTS.get(slot, slot)}")
        if slot in taken:
            raise EquipError(f"{label}: the {SLOTS[slot]} already carries {taken[slot]}")
        taken[slot] = label
        nudge = clean_nudge(entry.get("nudge"))
        world, info = place(rest, how["carry"], slot, entry.get("kind"), item_shape(entry["glb"]), nudge)
        bone = "head" if slot == "head" else slot
        data, where = glbmerge.merge_bytes(data, entry["glb"], bone, world, tag={"npcforge": "equip", "slot": slot, "item": entry.get("item") or ""})
        reports.append({"item": entry.get("item"), "kind": entry.get("kind"), "slot": slot, "bone": where["parent"], "attach": where["attach"],
                        "scale": info["scale"], "nudge": nudge})
    return data, {"items": reports, "notes": list(rest.notes)}
