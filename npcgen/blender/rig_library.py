"""Animation clips from Quaternius' Universal Animation Library 1 and 2 (CC0), copied onto a generated skeleton. Runs
inside Blender (rig_humanoid.py imports it); everything but add_action works on plain numpy, and the library's GLB files
are read here directly, not imported.

The library's rig uses Unreal mannequin names as ours does (pelvis, spine_01, thumb_01_l...), but it stands in a T-pose
and our models stand in an A-pose, with their own proportions. So a clip is copied bone by bone in world space: each
bone of ours turns exactly as the library's bone turns away from its rest pose. Arm, hand, finger, thigh and calf bones
are first swung (the least turn) to point where the library's point at rest, since there the two rest poses differ (arms
out or down, fingers straight or curled). The spine, head, collarbones and feet keep their own rest pose: those bones
point differently only because the two rigs place them differently (a collarbone that starts at the front of the chest,
a foot bone from ankle to ball in a heel), and swinging them would bend the body. The hips move as the library's do,
scaled by hip height, and then lifted or lowered so the lower foot keeps its height above the floor as it does in the
library's clip. Bones the library has no match for (the jaw, the twist bones, the prop bones) follow their parents; on
the medium skeleton each forearm's roll is spread over the hand and its twist bone, as in the clips made in
rig_humanoid.py.
"""
import json
import struct

import numpy as np

FINGERS = ("thumb", "index", "middle", "ring", "pinky")
SWUNG = ("upperarm", "lowerarm", "hand", "thigh", "calf") + FINGERS      # bones lined up with the library's rest pose
RENAME = {"Idle_Loop": "Idle", "Walk_Loop": "Walk"}   # the names the 3D stage (web/stage3d.js) and our clips use
SKIP = {"A_TPose"}                                      # the library's rest pose, not a clip


def _source_map():
    """Our bone -> (the library's joint, the joint its direction points to; None = straight up)."""
    m = {"pelvis": ("pelvis", "spine_01"), "spine_01": ("spine_01", "spine_02"), "spine_02": ("spine_02", "spine_03"),
         "spine_03": ("spine_03", "neck_01"), "neck_01": ("neck_01", "Head"), "head": ("Head", None)}
    for s in "lr":
        m.update({f"clavicle_{s}": (f"clavicle_{s}", f"upperarm_{s}"), f"upperarm_{s}": (f"upperarm_{s}", f"lowerarm_{s}"),
                  f"lowerarm_{s}": (f"lowerarm_{s}", f"hand_{s}"), f"hand_{s}": (f"hand_{s}", f"middle_01_{s}"),
                  f"thigh_{s}": (f"thigh_{s}", f"calf_{s}"), f"calf_{s}": (f"calf_{s}", f"foot_{s}"),
                  f"foot_{s}": (f"foot_{s}", f"ball_{s}"), f"ball_{s}": (f"ball_{s}", f"ball_leaf_{s}")})
        for f in FINGERS:
            for j in (1, 2, 3):
                m[f"{f}_0{j}_{s}"] = (f"{f}_0{j}_{s}", f"{f}_0{j + 1}_{s}" if j < 3 else f"{f}_04_leaf_{s}")
    return m


MAP = _source_map()


# ---------------------------------------------------------------- quaternions, (..., 4) arrays in x, y, z, w order

def qmul(a, b):
    ax, ay, az, aw = np.moveaxis(a, -1, 0)
    bx, by, bz, bw = np.moveaxis(b, -1, 0)
    return np.stack([aw * bx + ax * bw + ay * bz - az * by,
                     aw * by - ax * bz + ay * bw + az * bx,
                     aw * bz + ax * by - ay * bx + az * bw,
                     aw * bw - ax * bx - ay * by - az * bz], -1)


def qinv(q):
    return q * np.array([-1.0, -1.0, -1.0, 1.0])


def qrot(q, v):
    u, w = q[..., :3], q[..., 3:4]
    t = 2.0 * np.cross(u, v)
    return v + w * t + np.cross(u, t)


def qbetween(a, b):
    """The least rotation turning direction a onto direction b."""
    a, b = a / np.linalg.norm(a), b / np.linalg.norm(b)
    c = float(np.dot(a, b))
    if c < -0.999999:                                   # opposite: half a turn about any axis square to a
        axis = np.cross(a, [1.0, 0, 0]) if abs(a[0]) < 0.9 else np.cross(a, [0, 1.0, 0])
        return np.append(axis / np.linalg.norm(axis), 0.0)
    q = np.append(np.cross(a, b), 1.0 + c)
    return q / np.linalg.norm(q)


def qpow(q, k):
    """q turned k times as far about the same axis (q unit, w >= 0 after sign fix)."""
    q = np.where(q[..., 3:4] < 0, -q, q)
    angle = 2.0 * np.arccos(np.clip(q[..., 3], -1.0, 1.0))
    s = np.sin(angle / 2.0)
    axis = np.where(s[..., None] > 1e-9, q[..., :3] / np.maximum(s, 1e-9)[..., None], 0.0)
    half = angle * k / 2.0
    return np.concatenate([axis * np.sin(half)[..., None], np.cos(half)[..., None]], -1)


def twist(q, axis):
    """The part of rotation q that turns about axis (swing-twist decomposition, q = swing * twist)."""
    t = np.concatenate([axis * np.sum(q[..., :3] * axis, -1, keepdims=True), q[..., 3:4]], -1)
    n = np.linalg.norm(t, axis=-1, keepdims=True)
    return np.where(n > 1e-9, t / np.maximum(n, 1e-9), np.array([0, 0, 0, 1.0]))


def continuous(q):
    """Flip signs so each frame's quaternion is on the same side as the one before (for interpolation)."""
    flips = np.concatenate([[0], np.cumsum(np.sum(q[1:] * q[:-1], -1) < 0)])
    return np.where((flips % 2 == 1)[:, None], -q, q)


# ---------------------------------------------------------------- the library's files

def read_glb(path):
    with open(path, "rb") as fh:
        data = fh.read()
    js, binc, off = None, None, 12
    length = struct.unpack_from("<I", data, 8)[0]
    while off < length:
        size, kind = struct.unpack_from("<II", data, off)
        if kind == 0x4E4F534A:
            js = json.loads(data[off + 8: off + 8 + size])
        elif kind == 0x004E4942:
            binc = data[off + 8: off + 8 + size]
        off += 8 + size
    return js, binc


def accessor(js, binc, index):
    a = js["accessors"][index]
    if a["componentType"] != 5126:
        raise ValueError("only float accessors are read")
    n = {"SCALAR": 1, "VEC3": 3, "VEC4": 4}[a["type"]]
    view = js["bufferViews"][a["bufferView"]]
    start = view.get("byteOffset", 0) + a.get("byteOffset", 0)
    stride = view.get("byteStride") or 4 * n
    raw = np.frombuffer(binc, dtype=np.uint8, count=stride * (a["count"] - 1) + 4 * n, offset=start)
    rows = np.lib.stride_tricks.as_strided(raw, shape=(a["count"], 4 * n), strides=(stride, 1))
    return np.frombuffer(rows.tobytes(), dtype=np.float32).reshape(a["count"], n).astype(np.float64)


def to_blender(v):
    """glTF axes (Y up, faces +Z) to Blender's (Z up, faces -Y), for positions and quaternion vector parts."""
    return np.stack([v[..., 0], -v[..., 2], v[..., 1]], -1)


def to_blender_q(q):
    return np.concatenate([to_blender(q[..., :3]), q[..., 3:4]], -1)


class Library:
    """Every clip of one or more library GLB files, as world-space (Blender axes) joint rotations and hip positions.
    A later file's clip of the same name is skipped."""

    def __init__(self, paths):
        self.clips = []
        self.rest_rot, self.rest_pos = None, None
        for path in paths:
            self._read(path)

    def _read(self, path):
        js, binc = read_glb(path)
        nodes = js["nodes"]
        parent = {c: i for i, n in enumerate(nodes) for c in n.get("children", [])}
        order, seen = [], set()

        def visit(i):
            if i in seen:
                return
            if i in parent:
                visit(parent[i])
            seen.add(i)
            order.append(i)
        for i in range(len(nodes)):
            visit(i)
        rest_t = {i: np.array(n.get("translation", [0.0, 0, 0]), dtype=np.float64) for i, n in enumerate(nodes)}
        rest_r = {i: np.array(n.get("rotation", [0.0, 0, 0, 1]), dtype=np.float64) for i, n in enumerate(nodes)}
        name_of = {i: n.get("name") for i, n in enumerate(nodes)}
        wanted = {src for src, _tip in MAP.values()} | {tip for _s, tip in MAP.values() if tip}
        index = {name_of[i]: i for i in order if name_of[i] in wanted}
        missing = wanted - set(index)
        if missing:
            raise ValueError(f"{path}: not the Universal Animation Library rig (no joint {sorted(missing)[0]})")

        def world(local_t, local_r, n):
            gt, gr = {}, {}
            for i in order:
                t = local_t.get(i, np.broadcast_to(rest_t[i], (n, 3)))
                r = local_r.get(i, np.broadcast_to(rest_r[i], (n, 4)))
                if i in parent:
                    p = parent[i]
                    gt[i] = gt[p] + qrot(gr[p], t)
                    gr[i] = qmul(gr[p], r)
                else:
                    gt[i], gr[i] = t, r
            return ({k: to_blender(gt[i]) for k, i in index.items()},
                    {k: to_blender_q(gr[i]) for k, i in index.items()})

        rest_pos, rest_rot = world({}, {}, 1)
        rest_pos = {k: v[0] for k, v in rest_pos.items()}
        rest_rot = {k: v[0] for k, v in rest_rot.items()}
        if self.rest_rot is None:
            self.rest_pos, self.rest_rot = rest_pos, rest_rot
        known = {c["name"] for c in self.clips}
        for anim in js.get("animations", []):
            name = anim.get("name", "")
            if name in SKIP or name in known:
                continue
            times = None
            local_t, local_r = {}, {}
            for ch in anim["channels"]:
                smp = anim["samplers"][ch["sampler"]]
                if smp.get("interpolation", "LINEAR") != "LINEAR":
                    raise ValueError(f"{path}: {name} is not linearly keyed")
                t = accessor(js, binc, smp["input"])[:, 0]
                if times is None:
                    times = t
                elif len(t) != len(times) or np.abs(t - times).max() > 1e-4:
                    raise ValueError(f"{path}: {name} keys its joints at different times")
                path_ = ch["target"]["path"]
                if path_ in ("translation", "rotation"):
                    (local_t if path_ == "translation" else local_r)[ch["target"]["node"]] = accessor(js, binc, smp["output"])
            if times is None or len(times) < 2:
                continue
            pos, rot = world(local_t, local_r, len(times))
            self.clips.append({"name": name, "file": path, "times": times, "pos": pos, "rot": rot})
            known.add(name)

    def clip(self, name):
        return next(c for c in self.clips if c["name"] == name)


# ---------------------------------------------------------------- copying a clip onto our skeleton

def _xyzw(mq):
    return np.array([mq.x, mq.y, mq.z, mq.w])


class Target:
    """Our skeleton's rest pose, from a Blender armature object (armature space = world: the object is not moved)."""

    def __init__(self, arm, lib):
        self.arm = arm
        bones = arm.data.bones
        self.names = [b.name for b in bones]
        self.parent = {b.name: (b.parent.name if b.parent else None) for b in bones}
        self.rest = {b.name: _xyzw(b.matrix_local.to_quaternion()) for b in bones}
        self.head = {b.name: np.array(b.head_local[:]) for b in bones}
        depth = {}
        for b in bones:
            d, p = 0, b.parent
            while p:
                d, p = d + 1, p.parent
            depth[b.name] = d
        self.order = sorted(self.names, key=lambda n: depth[n])
        self.align = {}
        for name, (src, tip) in MAP.items():
            if name not in self.rest:
                continue
            if name.split("_")[0] in SWUNG:
                b = bones[name]
                ours = np.array((b.tail_local - b.head_local)[:])
                theirs = (lib.rest_pos[tip] - lib.rest_pos[src]) if tip else np.array([0.0, 0, 1])
                self.align[name] = qbetween(ours, theirs)
            else:
                self.align[name] = np.array([0.0, 0, 0, 1])
        self.hip_scale = float(self.head["pelvis"][2] / lib.rest_pos["pelvis"][2])
        self.medium = "lowerarm_twist_01_l" in self.rest


def retarget(target, lib, clip):
    """Our skeleton's pose in each frame of a library clip: ({bone: (n, 4) local rotations, x y z w}, (n, 3) hip
    offset in the hip bone's rest axes)."""
    n = len(clip["times"])
    world = {}
    for name in target.order:
        if name in MAP and name in target.align:
            src = MAP[name][0]
            delta = qmul(clip["rot"][src], qinv(lib.rest_rot[src])[None])
            world[name] = qmul(delta, qmul(target.align[name], target.rest[name])[None])
        elif target.parent[name] in world:                  # follows its parent
            p = target.parent[name]
            world[name] = qmul(world[p], qmul(qinv(target.rest[p]), target.rest[name])[None])
        else:
            world[name] = np.broadcast_to(target.rest[name], (n, 4))
    local = {}
    for name in target.order:
        p = target.parent[name]
        parent_now = world[p] if p else np.broadcast_to(np.array([0.0, 0, 0, 1]), (n, 4))
        parent_rest = target.rest[p] if p else np.array([0.0, 0, 0, 1])
        rel = qmul(qinv(target.rest[name]), parent_rest)
        local[name] = qmul(rel[None], qmul(qinv(parent_now), world[name]))
    if target.medium:
        spread_forearm_roll(target, local)
    hips = target.head["pelvis"] + target.hip_scale * (clip["pos"]["pelvis"] - lib.rest_pos["pelvis"])
    hips = hips + np.array([0, 0, 1.0]) * floor_fix(target, lib, clip, world, hips)[:, None]
    offset = qrot(qinv(target.rest["pelvis"])[None], hips - target.head["pelvis"])
    return {k: continuous(v) for k, v in local.items()}, offset


def _head_positions(target, world, hips, chain):
    """World positions, frame by frame, of the heads of `chain`: bones each the child of the one before, the first a
    child of the hips."""
    pos = {"pelvis": hips}
    for name in chain:
        p = target.parent[name]
        turn = qmul(world[p], qinv(target.rest[p])[None])
        pos[name] = pos[p] + qrot(turn, (target.head[name] - target.head[p])[None])
    return [pos[name] for name in chain]


def floor_fix(target, lib, clip, world, hips):
    """How far to lift (+) the hips in each frame so the lower ankle is as high above its rest height as the
    library's (scaled): the two rigs' legs differ in length and rest angles, so copying the hips alone leaves a foot in
    the floor or floating."""
    ours = []
    theirs = []
    for s in "lr":
        ankle = _head_positions(target, world, hips, [f"thigh_{s}", f"calf_{s}", f"foot_{s}"])[-1]
        ours.append(ankle[:, 2] - target.head[f"foot_{s}"][2])
        theirs.append(target.hip_scale * (clip["pos"][f"foot_{s}"][:, 2] - lib.rest_pos[f"foot_{s}"][2]))
    ours, theirs = np.stack(ours), np.stack(theirs)
    low = np.argmin(theirs, 0)                          # the library's lower foot in each frame
    cols = np.arange(ours.shape[1])
    return theirs[low, cols] - ours[low, cols]


def spread_forearm_roll(target, local):
    """Medium skeleton: each forearm keeps only its bend; its roll about its own length, with the hand's roll about
    the forearm, goes to the hand, and half of it to the forearm's twist bone, so the forearm turns gradually from elbow
    to wrist. The upper arm's twist bone takes back half of the upper arm's roll near the shoulder."""
    y = np.array([0.0, 1.0, 0.0])                        # a bone's own length is its local Y
    for s in "lr":
        fore, hand, tw = f"lowerarm_{s}", f"hand_{s}", f"lowerarm_twist_01_{s}"
        roll_fore = twist(local[fore], y)
        local[fore] = qmul(local[fore], qinv(roll_fore))
        rel_hand = qmul(qinv(target.rest[fore]), target.rest[hand])          # the hand's rest axes in the forearm's
        hand_in_fore = qmul(rel_hand[None], qmul(local[hand], qinv(rel_hand)[None]))
        roll_hand = twist(hand_in_fore, y)
        total = qmul(roll_fore, roll_hand)
        hand_in_fore = qmul(roll_fore, hand_in_fore)                          # the hand keeps its world turn
        local[hand] = qmul(qinv(rel_hand)[None], qmul(hand_in_fore, rel_hand[None]))
        if tw in local:
            rel = qmul(qinv(target.rest[fore]), target.rest[tw])
            local[tw] = qmul(qinv(rel)[None], qmul(qpow(total, 0.5), rel[None]))
        up, utw = f"upperarm_{s}", f"upperarm_twist_01_{s}"
        if utw in local:
            roll_up = twist(local[up], y)
            rel = qmul(qinv(target.rest[up]), target.rest[utw])
            local[utw] = qmul(qinv(rel)[None], qmul(qpow(qinv(roll_up), 0.5), rel[None]))


def add_action(arm, name, times, local, offset, fps):
    """A Blender action keying every bone at every library frame (rotation and location), linear, from frame 0."""
    import bpy                                       # here, so the rest of this file loads outside Blender (the tests)
    from bpy_extras import anim_utils
    act = bpy.data.actions.new(name)
    act.use_fake_user = True
    slot = act.slots.new(id_type="OBJECT", name=arm.name)
    bag = anim_utils.action_ensure_channelbag_for_slot(act, slot)
    frames = np.round(times * fps).astype(np.float64)
    frames -= frames[0]
    n = len(frames)
    for bone in arm.pose.bones:
        rot = local[bone.name]
        values = [rot[:, 3], rot[:, 0], rot[:, 1], rot[:, 2]]
        loc = offset if bone.name == "pelvis" else np.zeros((n, 3))
        channels = [("rotation_quaternion", i, values[i]) for i in range(4)] + [("location", i, loc[:, i]) for i in range(3)]
        for prop, i, v in channels:
            fc = bag.fcurves.new(f'pose.bones["{bone.name}"].{prop}', index=i, group_name=bone.name)
            fc.keyframe_points.add(n)
            fc.keyframe_points.foreach_set("co", np.column_stack([frames, v]).ravel())
            fc.keyframe_points.foreach_set("interpolation", [1] * n)       # LINEAR
            fc.update()
    return act, slot, int(frames[-1])


def root_motion(paths):
    """How far each clip carries the library's mannequin, as scripts/install_animations.py measured it from the packs'
    root-motion files (manifest.json beside the library's files): {clip: {forward_m, up_m, left_m, seconds}}."""
    import os
    for path in paths:
        manifest = os.path.join(os.path.dirname(path), "manifest.json")
        if os.path.exists(manifest):
            with open(manifest, encoding="utf-8") as fh:
                return json.load(fh).get("root_motion") or {}
    return {}


def movement(move, scale, loop):
    """A clip's movement for our character, whose legs are `scale` times the mannequin's: {moves: {forward_m, up_m,
    left_m}} and, for a loop, its speed (the clips are copied in place, so whatever moves the character should move it
    this fast, or the feet slide)."""
    if not move:
        return {}
    out = {"moves": {k: round(move[k] * scale, 3) for k in ("forward_m", "up_m", "left_m")}}
    if loop and move.get("seconds"):
        out["speed_m_s"] = round(float(np.hypot(move["forward_m"], move["left_m"])) * scale / move["seconds"], 3)
    return out


def add_clips(arm, paths, fps, names=None):
    """Copy the library's clips (or those named) onto the armature, one action each, stacked as muted NLA tracks as the
    clips of rig_humanoid.animate are. Returns [{name, source, file, seconds, loop, [moves, speed_m_s]}]."""
    lib = Library(paths)
    target = Target(arm, lib)
    moves = root_motion(paths)
    for pb in arm.pose.bones:
        pb.rotation_mode = "QUATERNION"
    arm.animation_data_create()
    made = []
    for clip in lib.clips:
        if names is not None and clip["name"] not in names:
            continue
        local, offset = retarget(target, lib, clip)
        name = RENAME.get(clip["name"], clip["name"])
        act, slot, last = add_action(arm, name, clip["times"], local, offset, fps)
        track = arm.animation_data.nla_tracks.new()
        track.name = name
        strip = track.strips.new(name, 0, act)
        strip.name = name
        track.mute = True
        loop = clip["name"].endswith("_Loop")
        made.append({"name": name, "source": clip["name"], "file": clip["file"], "seconds": round(last / fps, 3),
                     "loop": loop, **movement(moves.get(clip["name"]), target.hip_scale, loop)})
    arm.animation_data.action = None
    return made
