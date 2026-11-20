"""Rig and animate a textured humanoid GLB. Runs inside Blender, not the NPC Forge Python:

    blender -b --factory-startup --python rig_humanoid.py -- IN.glb OUT.glb [--height 1.7] [--preview DIR] [--report FILE]
        [--keypoints KEYS.json] [--skeleton low|medium] [--library "UAL1.glb|UAL2.glb"] [--hands HANDS.json]

The mesh is expected to stand upright in an A-pose (or arms clear of the body), feet down, facing -Y after import, as
Pixal3D and Hunyuan3D exports do. Joints are placed from pose keypoints (--keypoints, SDPose's points in three renders of
the model: npcgen/posekeys.py and rig_keypoints.py) where they are sure, and from the mesh's own silhouette otherwise;
skin weights are solved with Blender's bone-heat method on a clean low-poly proxy and copied back to the full textured
mesh, and a few keyframed clips are added: Idle, Walk and Wave made here, or with --library, the clips of Quaternius'
Universal Animation Library copied onto the skeleton (rig_library.py; its Idle and Walk replace ours). Bone names follow
the Unreal mannequin so Unreal's and Godot's retargeters recognise them.

Skeletons (--skeleton): "low" is 23 bones (root, pelvis, three spine, neck, head, and per side collarbone, upper arm,
forearm, hand, thigh, calf, foot and ball). "medium" adds 37, and needs the keypoints: three bones per finger (30),
a jaw, a twist bone in each upper arm and forearm, and a prop bone in each palm to attach a held object to. The low
skeleton's bones are the same in both, in the same places, so a clip made for one plays on the other.

--hands: the model has clean hands (npcgen/blender/hands.py; HANDS.json is model_3d.refined.hands, in the model's own
frame). Their 21 points replace SDPose's for the wrist, the hand's end and the finger bones, and their vertices go to
the hand bone (new_hand_weights).
"""
import json
import math
import os
import sys

import bmesh
import bpy
import numpy as np
from mathutils import Matrix, Quaternion, Vector

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rig_keypoints as rk  # noqa: E402
import rig_library  # noqa: E402

FPS = 30
ARM_DOWN = {"l": 15.0, "r": 15.0}      # degrees each arm is lowered from its rest A-pose in the clips; set from the mesh
SKELETONS = ("low", "medium")


def args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    opts = {"height": 1.7, "preview": None, "report": None, "debug": None, "joints_only": None, "poster": None,
            "shape": None, "keypoints": None, "skeleton": "low", "library": None, "hands": None}
    pos = []
    i = 0
    while i < len(argv):
        if argv[i].startswith("--"):
            opts[argv[i][2:]] = argv[i + 1]
            i += 2
        else:
            pos.append(argv[i])
            i += 1
    opts["in"], opts["out"] = pos[:2]
    opts["height"] = float(opts["height"])
    if opts["skeleton"] not in SKELETONS:
        raise SystemExit("Unknown skeleton: " + str(opts["skeleton"]))
    return opts


# ---------------------------------------------------------------- import and normalise

def load(path, height, normalise=None):
    """The model as one mesh object, scaled to `height`, feet on the ground and centred. Returns (object, the matrix
    used), so a second copy of the same model can be put in exactly the same place with `normalise`."""
    if normalise is None:
        bpy.ops.wm.read_factory_settings(use_empty=True)
    existing = set(bpy.context.scene.objects)
    bpy.ops.import_scene.gltf(filepath=path)
    added = [o for o in bpy.context.scene.objects if o not in existing]
    meshes = [o for o in added if o.type == "MESH"]
    if not meshes:
        raise SystemExit("No mesh in " + path)
    bpy.ops.object.select_all(action="DESELECT")
    for o in meshes:
        o.select_set(True)
    bpy.context.view_layer.objects.active = meshes[0]
    if len(meshes) > 1:
        bpy.ops.object.join()
    ob = bpy.context.view_layer.objects.active
    for o in added:
        if o is not ob and o.name in bpy.data.objects:
            bpy.data.objects.remove(o)
    ob.parent = None
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    if normalise is None:
        co = verts(ob)
        lo, hi = co.min(0), co.max(0)
        s = height / (hi[2] - lo[2])
        centre = Vector((-(lo[0] + hi[0]) / 2 * s, -(lo[1] + hi[1]) / 2 * s, -lo[2] * s))
        normalise = Matrix.Translation(centre) @ Matrix.Scale(s, 4)
    ob.data.transform(normalise)
    ob.data.update()
    ob.name = "Body" if len(existing) == 0 else "Shape"
    return ob, normalise


def verts(ob):
    co = np.empty(len(ob.data.vertices) * 3, dtype=np.float64)
    ob.data.vertices.foreach_get("co", co)
    return co.reshape(-1, 3)


# ---------------------------------------------------------------- joints from the silhouette

class Silhouette:
    """Front (x, z) occupancy of the vertices, and the solid runs along each row."""

    def __init__(self, co, height, rows=420):
        self.co, self.H = co, height
        self.cell = height / rows
        self.x0 = co[:, 0].min()
        cols = int((co[:, 0].max() - self.x0) / self.cell) + 2
        occ = np.zeros((rows + 2, cols), dtype=bool)
        r = np.clip((co[:, 2] / self.cell).astype(int), 0, rows + 1)
        c = ((co[:, 0] - self.x0) / self.cell).astype(int)
        occ[r, c] = True
        occ = occ | np.roll(occ, 1, 0) | np.roll(occ, -1, 0)      # close pinholes between vertices
        occ = occ | np.roll(occ, 1, 1) | np.roll(occ, -1, 1)
        self.occ = occ
        self.runs = [self._runs(row) for row in occ]

    @staticmethod
    def _runs(row):
        d = np.diff(np.concatenate(([0], row.astype(np.int8), [0])))
        starts, ends = np.flatnonzero(d == 1), np.flatnonzero(d == -1)
        return [(s, e) for s, e in zip(starts, ends) if e - s >= 2]

    def row(self, z):
        return int(math.floor(z / self.cell))        # the same binning as the occupancy grid; z(row) maps back

    def z(self, row):
        return (row + 0.5) * self.cell

    def x(self, col):
        return self.x0 + (col + 0.5) * self.cell

    def centre_run(self, r):
        runs = self.runs[r]
        if not runs:
            return None
        c0 = (0 - self.x0) / self.cell
        return min(runs, key=lambda run: 0 if run[0] <= c0 < run[1] else min(abs(run[0] - c0), abs(run[1] - c0)))


def leg_split(sil, H):
    """Height where the legs part, or None when a skirt or coat hides it.

    Going up from the knees, the last row with two separate leg runs and nothing across the middle."""
    mid = int(round((0 - sil.x0) / sil.cell))
    top = None
    for r in range(sil.row(0.25 * H), sil.row(0.62 * H)):
        runs = sil.runs[r]
        if any(a <= mid < b for a, b in runs):
            break
        legs = [q for q in runs if abs((q[0] + q[1]) / 2 - mid) < 0.15 * H / sil.cell]
        if len(legs) >= 2:
            top = r
    if top is None:
        return None
    # At a real crotch the gap between the inner thighs closes gradually; at a hem it is cut off while still wide.
    inner = [q for q in sil.runs[top] if abs((q[0] + q[1]) / 2 - mid) < 0.15 * H / sil.cell]
    left = max((q for q in inner if q[1] <= mid + 1), key=lambda q: q[1], default=None)
    right = min((q for q in inner if q[0] >= mid - 1), key=lambda q: q[0], default=None)
    if left and right and (right[0] - left[1]) * sil.cell > 0.015 * H:
        return None
    return sil.z(top)


def depth(co, x, z, r, xr=None):
    """Middle of the mesh front-to-back around (x, z): the joint sits inside the limb, not on its surface."""
    xr = xr or r
    m = (np.abs(co[:, 0] - x) < xr) & (np.abs(co[:, 2] - z) < r)
    if m.sum() < 8:
        return float(np.median(co[:, 1]))
    ys = co[m, 1]
    return float((np.percentile(ys, 5) + np.percentile(ys, 95)) / 2)


def find_joints(ob, H):
    co = verts(ob)
    sil = Silhouette(co, H)
    notes = []
    J = {}

    # Arms: from the top of the gap under each arm, follow the outermost run down to the fingertips.
    arm = {}
    for side, sign in (("l", 1), ("r", -1)):
        armpit = None
        for r in range(sil.row(0.85 * H), sil.row(0.45 * H), -1):
            cr = sil.centre_run(r)
            outer = [run for run in sil.runs[r] if cr and ((run[0] >= cr[1]) if sign > 0 else (run[1] <= cr[0]))]
            if outer:
                armpit = r
                break
        if armpit is None:
            raise SystemExit(f"No gap between the {side} arm and the body: the model needs an A-pose")
        track = []
        prev = None
        for r in range(armpit, 0, -1):
            cr = sil.centre_run(r)
            cands = [run for run in sil.runs[r] if run is not cr]
            cands = [run for run in cands if (run[0] >= cr[1] if sign > 0 else run[1] <= cr[0])] if cr else cands
            if not cands:
                break
            run = max(cands, key=lambda q: q[1]) if sign > 0 else min(cands, key=lambda q: q[0])
            if prev and (run[0] > prev[1] + 1 or run[1] < prev[0] - 1):
                break
            track.append((sil.z(r), sil.x((run[0] + run[1]) / 2), (run[1] - run[0]) * sil.cell))
            prev = run
        arm[side] = (armpit, track)

    # Neck: the narrowest body run between the chest and the top of the head. The shoulder joints sit a little
    # below it; the armpit gap alone lands too low on stylised figures whose arms hug the body.
    top = co[:, 2].max()
    neck_z = narrowest(sil, 0.70 * H, top - 0.06 * H)
    neck_z = 0.84 * H if neck_z is None else neck_z
    if not 0.74 * H <= neck_z <= 0.9 * H:
        notes.append(f"neck found at {neck_z / H:.2f} of the height; used 0.84 instead")
        neck_z = 0.84 * H
    armpit_z = max(sil.z(arm[s][0]) for s in arm)
    shoulder_z = max(neck_z - 0.045 * H, armpit_z + 0.02 * H)
    scale = neck_z / (0.84 * H)            # 1.0 for textbook proportions, lower for big-headed stylised figures

    for side, sign in (("l", 1), ("r", -1)):
        armpit, track = arm[side]
        width = float(np.median([t[2] for t in track[:max(3, len(track) // 4)]]))
        edge_run = sil.runs[sil.row(shoulder_z)]
        edge = sil.x(max(q[1] for q in edge_run) if sign > 0 else min(q[0] for q in edge_run))
        sh = Vector((edge - sign * 0.55 * width, 0, shoulder_z))
        tip_z = track[-1][0]
        expected_tip = shoulder_z - 0.4 * H * scale
        pts = [Vector((t[1], 0, t[0])) for t in track]
        if tip_z > expected_tip + 0.03 * H:
            notes.append(f"{side} hand merges with the body; its length was estimated")
            d = (pts[-1] - pts[0]).normalized() if len(pts) > 2 else Vector((sign * 0.4, 0, -1)).normalized()
            pts.append(pts[-1] + d * (tip_z - expected_tip) / max(0.3, -d.z))
        # the wrist is a hand's length back up the arm from the fingertip
        hand_len, run_len, wrist = 0.1 * H, 0.0, pts[0]
        for a, b in zip(reversed(pts), list(reversed(pts))[1:]):
            run_len += (a - b).length
            if run_len >= hand_len:
                wrist = b
                break
        elbow = sh.lerp(wrist, 0.52)
        near = min(pts, key=lambda p: abs(p.z - elbow.z))
        elbow.x = (elbow.x + near.x) / 2
        tip = pts[-1]
        r = 0.03 * H
        for name, p in (("upperarm", sh), ("lowerarm", elbow), ("hand", wrist), ("hand_end", tip)):
            p = p.copy()
            p.y = depth(co, p.x, p.z, r)
            J[f"{name}_{side}"] = p
        J[f"lowerarm_{side}"].y += 0.01 * H           # a hint of bend so an elbow folds the right way

    # Legs: proportions scaled from the shoulders (stylised figures shift them), x from the two leg runs.
    ankle_z = 0.05 * H
    hip_z = 0.51 * H * scale
    crotch = leg_split(sil, H)
    if crotch is not None and 0.36 * H < crotch < 0.56 * H:
        hip_z = crotch + 0.04 * H
    else:
        notes.append("legs hidden (a skirt, robe or coat); hip height estimated from proportions")
    knee_z = ankle_z + 0.52 * (hip_z - ankle_z)
    for side, sign in (("l", 1), ("r", -1)):
        xs = {}
        for key, z in (("knee", knee_z), ("ankle", ankle_z)):
            runs = [q for q in sil.runs[sil.row(z)] if (sil.x((q[0] + q[1]) / 2) > 0) == (sign > 0)]
            if runs:
                q = max(runs, key=lambda q: q[1] - q[0])
                xs[key] = sil.x((q[0] + q[1]) / 2)
            else:
                xs[key] = sign * 0.05 * H
                notes.append(f"{side} {key} run not found; used a default")
        hip_run = sil.centre_run(sil.row(hip_z))
        half = (hip_run[1] - hip_run[0]) * sil.cell / 2 if hip_run else 0.09 * H
        hip_x = sign * max(abs(xs["knee"]), 0.45 * half) if abs(xs["knee"]) < 0.9 * half else xs["knee"]
        foot = co[(co[:, 2] < 0.05 * H) & ((co[:, 0] > 0) == (sign > 0))]
        toe_y, heel_y = float(foot[:, 1].min()), float(foot[:, 1].max())
        r = 0.035 * H
        J[f"thigh_{side}"] = Vector((hip_x, depth(co, hip_x, hip_z, r, 0.05 * H), hip_z))
        J[f"calf_{side}"] = Vector((xs["knee"], depth(co, xs["knee"], knee_z, r) - 0.008 * H, knee_z))
        J[f"foot_{side}"] = Vector((xs["ankle"], heel_y + 0.3 * (toe_y - heel_y), ankle_z))
        J[f"ball_{side}"] = Vector((xs["ankle"], heel_y + 0.75 * (toe_y - heel_y), 0.02 * H))
        J[f"ball_end_{side}"] = Vector((xs["ankle"], toe_y, 0.02 * H))

    torso(co, J, H, hip_z, shoulder_z, neck_z, top)
    levels = {"shoulder_z": shoulder_z, "neck_z": neck_z, "armpit_z": armpit_z, "hip_z": hip_z, "knee_z": knee_z}
    return J, notes, levels, sil, arm


def narrowest(sil, lo, hi):
    """Height of the narrowest body run between heights lo and hi (the neck), or None."""
    widths = []
    for r in range(sil.row(lo), sil.row(hi)):
        cr = sil.centre_run(r)
        if cr:
            widths.append((cr[1] - cr[0], r))
    return sil.z(min(widths)[1]) if widths else None


def torso(co, J, H, hip_z, shoulder_z, neck_z, top):
    """Pelvis, spine, neck, head and collarbones on the midline, from the hip, shoulder and neck heights."""
    body = 0.08 * H
    spine = lambda z: Vector((0, depth(co, 0, z, 0.03 * H, body), z))
    J["pelvis"] = spine(hip_z + 0.02 * H)
    chest_top = shoulder_z - 0.02 * H
    J["spine_01"] = spine(J["pelvis"].z + 0.03 * H)
    J["spine_02"] = spine(J["spine_01"].z + (chest_top - J["spine_01"].z) * 0.4)
    J["spine_03"] = spine(J["spine_01"].z + (chest_top - J["spine_01"].z) * 0.75)
    J["neck_01"] = spine(chest_top)
    J["neck_01"].y = depth(co, 0, neck_z, 0.02 * H, 0.03 * H)
    J["head"] = Vector((0, J["neck_01"].y, neck_z + 0.02 * H))
    J["head_end"] = Vector((0, J["head"].y, top))
    for side, sign in (("l", 1), ("r", -1)):
        J[f"clavicle_{side}"] = Vector((sign * 0.02 * H, J["spine_03"].y - 0.01 * H, chest_top))


# ---------------------------------------------------------------- joints from pose keypoints

LIMBS = {"upperarm": "shoulder", "lowerarm": "elbow", "hand": "wrist", "thigh": "hip", "calf": "knee", "foot": "ankle"}


def keypoint_joints(K, J, levels, sil, tracks, co, H, notes):
    """Move the joints to where SDPose sees them (rig_keypoints.py): arms, legs and feet, the hand's end at the middle
    knuckle, and the hip, shoulder and neck heights the spine is built from. A limb whose points score low, or come out
    in the wrong order, keeps its outline joints. On Laura (a long-sleeved dress) the outline had the wrists at her
    elbows and the hips 18 cm low (the dress's hem taken for the crotch), and flat feet in high heels.
    Returns (hands, notes): hands[side] is SDPose's 21 points of that hand in 3D, for limbs taken from the keypoints;
    `notes` also drops the outline's own notes about the limbs and heights the keypoints replaced."""
    outline_notes = list(notes)
    notes = []
    s = H / 1.7
    found, scores = {}, {}
    for side in "lr":
        for bone, key in LIMBS.items():
            r = (0.035 if key in ("wrist", "ankle") else 0.05) * s
            found[f"{bone}_{side}"], scores[f"{bone}_{side}"] = K.body(f"{key}_{side}", side, r)
    used = set()
    for side in "lr":
        for limb, chain in (("arm", ("upperarm", "lowerarm", "hand")), ("leg", ("thigh", "calf", "foot"))):
            names = [f"{b}_{side}" for b in chain]
            zs = [found[n].z for n in names]
            if min(scores[n] for n in names) >= rk.MIN_SCORE and zs[0] > zs[1] > zs[2]:
                for n in names:
                    J[n] = found[n]
                used.add((side, limb))
            else:
                notes.append(f"{side} {limb}: the pose keypoints were unsure, so its joints come from the outline")
    hands = {}
    for side in "lr":
        if (side, "arm") in used:
            J[f"lowerarm_{side}"].y += 0.01 * H       # a hint of bend so an elbow folds the right way, as the outline rig
            hands[side] = pts = K.hand(side)
            knuckle, ks = pts[9]                       # the middle finger's knuckle ends the hand bone
            if ks >= rk.MIN_SCORE and (knuckle - J[f"hand_{side}"]).length > 0.02 * H:
                J[f"hand_end_{side}"] = knuckle
            else:
                J[f"hand_end_{side}"] = J[f"hand_{side}"] + (J[f"hand_{side}"] - J[f"lowerarm_{side}"]).normalized() * 0.05 * H
        if (side, "leg") in used:
            J[f"calf_{side}"].y -= 0.008 * H
            feet = [K.foot(f"{name}_{side}") for name in ("heel", "big_toe", "small_toe")]
            if min(sc for _, sc in feet) < rk.MIN_SCORE:
                notes.append(f"{side} foot: the pose keypoints were unsure, so its toes come from the outline")
                continue
            heel, big, small = (p for p, _ in feet)
            tip = Vector(((big.x + small.x) / 2, min(big.y, small.y), 0.0))
            ball = heel.lerp(tip, 0.72)
            under = co[(np.abs(co[:, 0] - ball.x) < 0.02 * s) & (np.abs(co[:, 1] - ball.y) < 0.02 * s)]
            z = (float(under[:, 2].min()) if len(under) else 0.0) + 0.012 * H      # the sole there, up a little
            J[f"ball_{side}"] = Vector((ball.x, ball.y, z))                        # (high heels: well below the ankle)
            J[f"ball_end_{side}"] = Vector((tip.x, tip.y, z))
    hip_z = (J["thigh_l"].z + J["thigh_r"].z) / 2
    shoulder_z = (J["upperarm_l"].z + J["upperarm_r"].z) / 2
    neck_z = levels["outline_neck_z"] = levels["neck_z"]
    chin, cs = K.face()[8]
    if cs >= rk.MIN_SCORE and chin.z > shoulder_z + 0.03 * H:    # the narrowest run between the shoulders and the chin
        neck_z = narrowest(sil, shoulder_z + 0.01 * H, chin.z) or neck_z
    torso(co, J, H, hip_z, shoulder_z, neck_z, float(co[:, 2].max()))
    levels.update(hip_z=hip_z, shoulder_z=shoulder_z, neck_z=neck_z, knee_z=(J["calf_l"].z + J["calf_r"].z) / 2)
    for side in hands:
        tracks[side] = (tracks[side][0], keypoint_track(sil, J, hands[side], tracks[side][0], side, H))

    def replaced(note):
        side = note[:2].strip()
        if note.startswith(("l hand ", "r hand ")):
            return (side, "arm") in used
        if note.startswith(("l knee ", "r knee ", "l ankle ", "r ankle ")):
            return (side, "leg") in used
        if note.startswith("legs hidden"):
            return ("l", "leg") in used and ("r", "leg") in used
        if note.startswith("neck found"):
            return neck_z != levels.get("outline_neck_z", neck_z)
        return False
    return hands, [n for n in outline_notes if not replaced(n)] + notes     # (the report says where the joints came from)


def keypoint_track(sil, J, hand, armpit, side, H):
    """The arm's centre and width on each outline row, from the armpit down to its lowest finger point, along the
    keypoint arm (shoulder, elbow, wrist, knuckle, fingertip): the outline's own track stopped at Laura's cuff, where
    her sleeve touches her dress. On rows where the arm's run is wider than an arm (merged with the body) the arm is
    taken as wide as it is, above or below the elbow, on the rows where it hangs free."""
    tips = [p for i, (p, sc) in enumerate(hand) if i in (4, 8, 12, 16, 20) and sc >= rk.MIN_SCORE]
    line = [J[f"upperarm_{side}"], J[f"lowerarm_{side}"], J[f"hand_{side}"], J[f"hand_end_{side}"]]
    if tips:
        line.append(min(tips, key=lambda p: p.z))
    zs = [p.z for p in line][::-1]
    xs = [p.x for p in line][::-1]
    elbow_z = J[f"lowerarm_{side}"].z
    rows = []
    for r in range(armpit, max(0, sil.row(line[-1].z) - 1), -1):
        z = sil.z(r)
        x = float(np.interp(z, zs, xs))
        c = (x - sil.x0) / sil.cell
        run = next((q for q in sil.runs[r] if q[0] - 1 <= c <= q[1] + 1), None)
        free = run is not None and (run[1] - run[0]) * sil.cell < 0.1 * H
        rows.append((z, x, run, free))
    widths = {}
    for upper in (True, False):
        ws = [(run[1] - run[0]) * sil.cell for z, x, run, free in rows if free and (z > elbow_z) == upper]
        widths[upper] = float(np.median(ws)) if ws else 0.06 * H
    track = []
    for z, x, run, free in rows:
        if free:
            track.append((z, sil.x((run[0] + run[1]) / 2), (run[1] - run[0]) * sil.cell))
        else:
            track.append((z, x, widths[z > elbow_z]))
    return track


def debug_image(sil, J, tracks, path):
    """Front silhouette with the arm tracks (blue) and joints (red) as a binary PPM, top row first."""
    occ = sil.occ
    img = np.where(occ[..., None], np.array([200, 200, 200], np.uint8), np.array([255, 255, 255], np.uint8))
    img = img.copy()
    col = lambda x: int(round((x - sil.x0) / sil.cell))
    for side in tracks:
        for z, x, _ in tracks[side][1]:
            img[sil.row(z), min(img.shape[1] - 1, max(0, col(x)))] = (40, 90, 255)
    for p in J.values():
        r, c = sil.row(p.z), col(p.x)
        img[max(0, r - 2):r + 3, max(0, c - 2):c + 3] = (230, 30, 30)
    img = img[::-1]
    with open(path, "wb") as fh:
        fh.write(b"P6 %d %d 255\n" % (img.shape[1], img.shape[0]))
        fh.write(np.ascontiguousarray(img).tobytes())


# ---------------------------------------------------------------- armature

CHAIN = [  # (bone, parent, head joint, tail joint)
    ("pelvis", "root", "pelvis", "spine_01"),
    ("spine_01", "pelvis", "spine_01", "spine_02"),
    ("spine_02", "spine_01", "spine_02", "spine_03"),
    ("spine_03", "spine_02", "spine_03", "neck_01"),
    ("neck_01", "spine_03", "neck_01", "head"),
    ("head", "neck_01", "head", "head_end"),
]
for s in "lr":
    CHAIN += [
        (f"clavicle_{s}", "spine_03", f"clavicle_{s}", f"upperarm_{s}"),
        (f"upperarm_{s}", f"clavicle_{s}", f"upperarm_{s}", f"lowerarm_{s}"),
        (f"lowerarm_{s}", f"upperarm_{s}", f"lowerarm_{s}", f"hand_{s}"),
        (f"hand_{s}", f"lowerarm_{s}", f"hand_{s}", f"hand_end_{s}"),
        (f"thigh_{s}", "pelvis", f"thigh_{s}", f"calf_{s}"),
        (f"calf_{s}", f"thigh_{s}", f"calf_{s}", f"foot_{s}"),
        (f"foot_{s}", f"calf_{s}", f"foot_{s}", f"ball_{s}"),
        (f"ball_{s}", f"foot_{s}", f"ball_{s}", f"ball_end_{s}"),
    ]


FINGERS = rk.FINGERS
MEDIUM_CHAIN = []      # (bone, parent, head joint, tail joint): the medium skeleton's extra bones
for s in "lr":
    for f in FINGERS:
        MEDIUM_CHAIN += [(f"{f}_0{j}_{s}", f"hand_{s}" if j == 1 else f"{f}_0{j - 1}_{s}", f"{f}_{j}_{s}", f"{f}_{j + 1}_{s}")
                         for j in (1, 2, 3)]
    MEDIUM_CHAIN += [(f"upperarm_twist_01_{s}", f"upperarm_{s}", f"upperarm_twist_{s}", f"upperarm_twist_end_{s}"),
                     (f"lowerarm_twist_01_{s}", f"lowerarm_{s}", f"lowerarm_twist_{s}", f"lowerarm_twist_end_{s}"),
                     (f"prop_{s}", f"hand_{s}", f"prop_{s}", f"prop_end_{s}")]
MEDIUM_CHAIN.append(("jaw", "head", "jaw", "jaw_end"))
PROPS = ("prop_l", "prop_r")                     # attachment points: no weights


def medium_joints(K, J, hands, co, H):
    """The medium skeleton's joints: fingers from SDPose's hand points, twist bones along the arms, a prop bone in each
    palm, and the jaw from the face points. Returns (palm normals {side: Vector towards the palm}, face points, notes)."""
    notes = []
    palms = {}
    for side in "lr":
        pts = hands.get(side)
        if not pts:
            raise SystemExit(f"The medium skeleton needs the fingers, and the pose keypoints of the {side} arm were unsure")
        for f in FINGERS:
            for j, (p, _sc) in enumerate(rk.finger_chain(pts, f)):
                J[f"{f}_{j + 1}_{side}"] = p
        unsure = sum(1 for _p, sc in pts if sc < rk.MIN_SCORE)
        if unsure:
            notes.append(f"{side} hand: {unsure} of its 21 finger points were unsure")
        wrist, knuckle = J[f"hand_{side}"], J[f"hand_end_{side}"]
        along = (knuckle - wrist).normalized()
        thumb = pts[2][0] - wrist
        thumb = (thumb - along * thumb.dot(along)).normalized()
        palms[side] = along.cross(thumb) if side == "l" else thumb.cross(along)      # the way the fingers curl
        shoulder, elbow = J[f"upperarm_{side}"], J[f"lowerarm_{side}"]
        J[f"upperarm_twist_{side}"], J[f"upperarm_twist_end_{side}"] = shoulder.lerp(elbow, 0.2), shoulder.lerp(elbow, 0.6)
        J[f"lowerarm_twist_{side}"], J[f"lowerarm_twist_end_{side}"] = elbow.lerp(wrist, 0.5), wrist.lerp(elbow, 0.02)
        centre = wrist.lerp(knuckle, 0.55) + palms[side] * 0.012 * H
        J[f"prop_{side}"] = centre
        J[f"prop_end_{side}"] = centre + (pts[5][0] - pts[17][0]).normalized() * 0.05 * H     # along the knuckles
    face = K.face()
    scores = [sc for _p, sc in face]
    pts = [p for p, _sc in face]
    chin = pts[8]
    top_z = (pts[0].z + pts[16].z) / 2                  # the jawline's upper ends, beside the ears
    pivot_z = top_z - 0.15 * (top_z - chin.z)
    pivot_y = (pts[0].y + pts[16].y) / 2 + 0.01 * H     # just inside the cheek there, in front of the ear
    pivot_y = min(max(pivot_y, chin.y + 0.03 * H), chin.y + 0.07 * H)
    J["jaw"] = Vector((0.0, pivot_y, pivot_z))
    J["jaw_end"] = Vector((0.0, chin.y + 0.006 * H, chin.z + 0.004 * H))
    if float(np.mean(scores)) < rk.MIN_SCORE:
        notes.append("face points unsure: the jaw bone has no weights")
        pts = None
    return palms, pts, notes


def mouth_line(face):
    """Where the lips meet, as points from one inner corner to the other (face points 60 and 64 and the middles of the
    three inner upper/lower lip pairs), sorted across."""
    p = [Vector(q) for q in face]
    line = [p[60], (p[61] + p[67]) / 2, (p[62] + p[66]) / 2, (p[63] + p[65]) / 2, p[64]]
    return sorted(line, key=lambda q: q.x)


def part_lips(body, face, jaw, H):
    """Cut the mesh along the line where the lips meet. A generated face is one closed surface there, so opening the jaw
    stretched the lips and chin into a smear (Laura, 16 degrees); with the cut, the lips part over a dark mouth
    (add_mouth). One straight cut from inner corner to inner corner, square to the line of sight and through the middle
    of the lips' slight curve (1.8 mm at most on Laura), on the face's front only: it cuts every layer of these hollow
    surfaces at once. Following the curve with a cut per segment left bridges of skin where the segments met.
    Returns (edges cut, the cut: (a point on it, its normal pointing up, and its ends across))."""
    s = H / 1.7
    line = mouth_line(face)
    a, b = line[0], line[-1]
    x0, x1 = a.x - 0.001 * s, b.x + 0.001 * s
    offsets = [q.z - (a.z + (q.x - a.x) / (b.x - a.x) * (b.z - a.z)) for q in line[1:-1]]
    through = (a + b) / 2 + Vector((0, 0, float(np.mean(offsets)) if offsets else 0.0))
    normal = (b - a).cross(Vector((0, 1, 0))).normalized()       # across the lips, square to the line of sight
    bm = bmesh.new()
    bm.from_mesh(body.data)
    geom = set()
    for f in bm.faces:
        v0 = f.verts[0].co
        if not (x0 - 0.01 <= v0.x <= x1 + 0.01 and abs((v0 - through).dot(normal)) < 0.012 * s and v0.y < jaw.y):
            continue
        c = f.calc_center_median()
        if x0 <= c.x <= x1 and abs((c - through).dot(normal)) < 0.008 * s and c.y < jaw.y:
            geom.add(f)
            geom.update(f.edges)
            geom.update(f.verts)
    cut = []
    if geom:
        done = bmesh.ops.bisect_plane(bm, geom=list(geom), dist=1e-7, plane_co=through, plane_no=normal)
        cut = [e for e in done["geom_cut"] if isinstance(e, bmesh.types.BMEdge) and e.is_valid
               and x0 <= (e.verts[0].co.x + e.verts[1].co.x) / 2 <= x1]
    if cut:
        bmesh.ops.split_edges(bm, edges=cut)
    bm.to_mesh(body.data)
    bm.free()
    body.data.update()
    return len(cut), (through, normal, x0, x1)


def add_mouth(body, face, jaw, H):
    """A dark mouth behind the parted lips: a flattened ball inside the head, its upper half on the head bone and its
    lower half on the jaw, in its own dark material (glTF: a second primitive of the same skinned mesh)."""
    s = H / 1.7
    line = mouth_line(face)
    centre = (line[0] + line[-1]) / 2
    half = (line[-1].x - line[0].x) / 2 + 0.004 * s
    mesh = bpy.data.meshes.new("Mouth")
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=16, v_segments=10, radius=1.0)
    for v in bm.verts:
        v.co = Vector((centre.x + v.co.x * half, centre.y + 0.014 * s + v.co.y * 0.014 * s, centre.z + v.co.z * 0.011 * s))
    bm.to_mesh(mesh)
    bm.free()
    mouth = bpy.data.objects.new("Mouth", mesh)
    bpy.context.scene.collection.objects.link(mouth)
    mat = bpy.data.materials.new("Mouth")
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (0.09, 0.025, 0.025, 1)
    bsdf.inputs["Roughness"].default_value = 0.7
    mesh.materials.append(mat)
    zs = np.array([v.co.z for v in mesh.vertices])
    lower = smoothstep(0.002 * s, -0.002 * s, zs - centre.z)            # 1 below the lips' line, 0 above
    for name, w in (("jaw", lower), ("head", 1 - lower)):
        g = mouth.vertex_groups.new(name=name)
        for i, value in enumerate(w):
            if value > 1e-4:
                g.add([i], float(value), "REPLACE")
    count = len(mesh.vertices)
    bpy.ops.object.select_all(action="DESELECT")
    mouth.select_set(True)
    body.select_set(True)
    bpy.context.view_layer.objects.active = body
    bpy.ops.object.join()
    return count


def build_armature(J, H, palms=None):
    arm_data = bpy.data.armatures.new("Skeleton")
    arm = bpy.data.objects.new("Armature", arm_data)
    bpy.context.scene.collection.objects.link(arm)
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode="EDIT")
    eb = arm_data.edit_bones
    root = eb.new("root")
    root.head, root.tail = Vector((0, 0, 0)), Vector((0, 0.1 * H, 0))
    root.align_roll(Vector((0, 0, 1)))
    for name, parent, a, b in CHAIN + (MEDIUM_CHAIN if palms else []):
        bone = eb.new(name)
        bone.head, bone.tail = J[a], J[b]
        bone.parent = eb[parent]
        bone.use_connect = False
        d = (bone.tail - bone.head).normalized()
        side = name[-1]
        if name == "jaw":
            bone.align_roll(Vector((0, 1, -1)))       # Z down and back: turning about +X opens the mouth
        elif palms and name.split("_")[0] in FINGERS + ("prop",):
            bone.align_roll(palms[side])              # Z towards the palm: turning about +X curls the finger
        else:
            bone.align_roll(Vector((0, 0, 1)) if abs(d.z) < 0.7 else Vector((0, -1, 0)))
        bone.use_deform = name not in PROPS
    root.use_deform = False
    bpy.ops.object.mode_set(mode="OBJECT")
    return arm


# ---------------------------------------------------------------- skin weights

# The bone-heat proxy's voxel size, as divisions of the model's height, in the order tried. Blender's bone heat is touchy about the exact
# surface it is given: on some models (Airi and Natalie, Trellis2 shapes, 2026-10-02) it failed for EVERY bone at height/260 yet worked at
# 200 and 320, and an unweighted rig looks fine in the report and does not move at all. The first size that gives most bones weights is used.
PROXY_DIVISIONS = (260, 200, 320, 230, 290)


def heat_proxy(source, arm, H, divisions):
    """A copy of `source` made into a closed, welded, single-piece voxel proxy (voxels of H / divisions, at most 60,000 faces) and given
    the armature's automatic weights. Returns (proxy, its faces, pieces dropped, the groups no vertex has any weight in)."""
    proxy = source.copy()
    proxy.data = source.data.copy()
    proxy.name = "Proxy"
    bpy.context.scene.collection.objects.link(proxy)
    bm = bmesh.new()
    bm.from_mesh(proxy.data)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-5 * H)
    bm.to_mesh(proxy.data)
    bm.free()
    rem = proxy.modifiers.new("remesh", "REMESH")
    rem.mode, rem.voxel_size = "VOXEL", H / divisions
    dec = proxy.modifiers.new("decimate", "DECIMATE")
    dec.ratio = 1.0
    bpy.context.view_layer.objects.active = proxy
    bpy.ops.object.select_all(action="DESELECT")
    proxy.select_set(True)
    bpy.ops.object.modifier_apply(modifier="remesh")
    dropped = keep_largest_piece(proxy)
    faces = len(proxy.data.polygons)
    dec.ratio = min(1.0, 60000 / max(1, faces))
    bpy.ops.object.modifier_apply(modifier="decimate")
    bm = bmesh.new()
    bm.from_mesh(proxy.data)
    bmesh.ops.dissolve_degenerate(bm, dist=1e-6 * H, edges=bm.edges)
    bm.to_mesh(proxy.data)
    bm.free()
    proxy_faces = len(proxy.data.polygons)

    bpy.ops.object.select_all(action="DESELECT")
    proxy.select_set(True)
    arm.select_set(True)
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.parent_set(type="ARMATURE_AUTO")
    empty = [g.name for g in proxy.vertex_groups
             if not any(g.index == ge.group and ge.weight > 0.01 for v in proxy.data.vertices for ge in v.groups)]
    return proxy, proxy_faces, dropped, empty


def weighted_proxy(source, arm, H, deform):
    """The first voxel size of PROXY_DIVISIONS that bone heat solves for at least half of the `deform` bones: (proxy, its faces, pieces
    dropped, the groups without weights, the sizes tried). Raises SystemExit when none does, rather than rig a model that cannot move."""
    tried = []
    for divisions in PROXY_DIVISIONS:
        proxy, faces, dropped, empty = heat_proxy(source, arm, H, divisions)
        tried.append(divisions)
        if len(empty) * 2 <= deform:
            return proxy, faces, dropped, empty, tried
        bpy.data.objects.remove(proxy)           # most bones got nothing: that voxel size defeated the solver, try the next
    raise SystemExit("Blender could not work out skin weights for this model (bone heat failed for most bones at every proxy size tried: "
                     f"height/{', height/'.join(str(d) for d in tried)}). Its surface may be damaged or too thin")


def idle_note(names):
    """The rig note for deforming bones nothing is weighted to (they move no part of the model), or None."""
    if not names:
        return None
    shown = ", ".join(names[:6]) + (f" and {len(names) - 6} more" if len(names) > 6 else "")
    return f"{len(names)} bone{'s' if len(names) != 1 else ''} move{'' if len(names) != 1 else 's'} nothing (no skin weights): {shown}"


def skin(body, arm, H, sil, tracks, shape=None, medium=None, hands=None, new_hands=None):
    """Bone-heat weights on a clean watertight proxy, copied back to the textured mesh by nearest surface.

    The proxy is a voxel remesh, which needs a closed surface: a cleaned-up game version has open edges where its hidden
    inner skin was removed, so its untouched original (`shape`, same place and shape) is remeshed instead.

    medium: (J, palms, face points) for the medium skeleton. Its extra bones sit out the bone heat (the proxy's voxels,
    height/260, fuse fingers that are closer than that) and share out the weights of the bones they belong to afterwards
    (medium_weights), so everything else is weighted exactly as on the low skeleton."""
    extra = [b for b in arm.data.bones if b.name in {name for name, *_ in MEDIUM_CHAIN}]
    for b in extra:
        b.use_deform = False
    source = shape or body
    proxy, proxy_faces, dropped, empty, attempts = weighted_proxy(source, arm, H, sum(1 for b in arm.data.bones if b.use_deform))

    for b in arm.data.bones:
        if b.use_deform:
            body.vertex_groups.new(name=b.name)
    dt = body.modifiers.new("weights", "DATA_TRANSFER")
    dt.object = proxy
    dt.use_vert_data = True
    dt.data_types_verts = {"VGROUP_WEIGHTS"}
    dt.vert_mapping = "POLYINTERP_NEAREST"
    dt.layers_vgroup_select_src = "ALL"
    dt.layers_vgroup_select_dst = "NAME"
    bpy.ops.object.select_all(action="DESELECT")
    body.select_set(True)
    bpy.context.view_layer.objects.active = body
    bpy.ops.object.modifier_apply(modifier="weights")
    given = new_hand_weights(body, new_hands, H) if new_hands else 0
    freed = keep_arms_off_body(body, arm, sil, tracks, H, hands, keep=clean_hand_vertices(body) if new_hands else None)
    if new_hands:
        freed += clothes_off_new_hands(body, arm, new_hands, H)
    apart, leg_cut = keep_legs_apart(body, arm, H)
    shared = {}
    if medium is not None:
        for b in extra:
            b.use_deform = b.name not in PROPS
            if b.use_deform:
                body.vertex_groups.new(name=b.name)
        shared = medium_weights(body, H, *medium)
    bpy.ops.object.vertex_group_limit_total(group_select_mode="ALL", limit=4)
    bpy.ops.object.vertex_group_normalize_all(group_select_mode="ALL", lock_active=False)
    cut = cut_arm_bridges(body, sil, tracks)
    unweighted = sum(1 for v in body.data.vertices if not any(g.weight > 0 for g in v.groups))
    group_names = {g.index: g.name for g in body.vertex_groups}
    used = {group_names[g.group] for v in body.data.vertices for g in v.groups if g.weight > 0.01}
    idle = sorted(b.name for b in arm.data.bones if b.use_deform and b.name != "root" and b.name not in used)
    bpy.data.objects.remove(proxy)

    body.parent = arm
    mod = body.modifiers.new("Armature", "ARMATURE")
    mod.object = arm
    return {"proxy_faces": proxy_faces, "proxy_divisions": attempts[-1], "proxy_attempts": len(attempts),
            "proxy_pieces_dropped": dropped, "bones_without_weights": empty, "bones_moving_nothing": idle,
            "unweighted_vertices": unweighted, "arm_weights_removed_from": freed, "hand_body_faces_cut": cut,
            "other_leg_weights_removed_from": apart, "leg_to_leg_faces_cut": leg_cut,
            **({"clean_hand_vertices": given} if new_hands else {}),
            **({"medium_weighted_vertices": shared} if shared else {})}


def replaced_hands(path, normalise):
    """The clean hands' points and wrist rings (npcgen/blender/hands.py) in the rig's frame: {side: {"points":
    [(Vector, 1.0)] * 21, "centre": Vector, "normal": Vector}}."""
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    turn = normalise.to_3x3()
    return {side: {"points": [(normalise @ Vector(p), 1.0) for p in data[side]["points"]],
                   "centre": normalise @ Vector(data[side]["ring"]["centre"]),
                   "normal": (turn @ Vector(data[side]["ring"]["normal"])).normalized()}
            for side in "lr" if data.get(side, {}).get("points")}


def new_hand_weights(body, new_hands, H):
    """The clean hands' vertices (material "npcforge_hand") go to their hand bone. The bone-heat proxy is made from the
    generator's own model, whose mitten the new fingers do not follow: a fingertip past the mitten's end could take the
    thigh beside it. Just past the wrist ring the weights the proxy gave fade out over 2 cm. Returns how many vertices."""
    me = body.data
    slots = {i for i, m in enumerate(me.materials) if m is not None and m.name.startswith("npcforge_hand")}
    if not slots:
        return 0
    idx = sorted({v for p in me.polygons if p.material_index in slots for v in p.vertices})
    co = verts(body)[idx]
    sides = list(new_hands)
    near = np.argmin(np.stack([np.linalg.norm(co - np.array(new_hands[s]["centre"]), axis=1) for s in sides]), 0)
    groups = body.vertex_groups
    for k, i in enumerate(idx):
        h = new_hands[sides[near[k]]]
        w = float(smoothstep(0.0, 0.02 * H / 1.7, (Vector(co[k]) - h["centre"]).dot(h["normal"])))
        hand = groups[f"hand_{sides[near[k]]}"]
        old = {ge.group: ge.weight for ge in me.vertices[i].groups if ge.weight > 0}
        total = sum(old.values())
        if total <= 0:
            w, total = 1.0, 1.0
        for g, x in old.items():
            groups[g].add([i], x / total * (1 - w), "REPLACE")
        hand.add([i], old.get(hand.index, 0.0) / total * (1 - w) + w, "REPLACE")
    return len(idx)


def clean_hand_vertices(body):
    """Which vertices are the clean hands' (material "npcforge_hand"), as a mask."""
    me = body.data
    slots = {i for i, m in enumerate(me.materials) if m is not None and m.name.startswith("npcforge_hand")}
    hand = np.zeros(len(me.vertices), bool)
    hand[sorted({v for p in me.polygons if p.material_index in slots for v in p.vertices})] = True
    return hand


CLOTHES_NEAR_HAND = 0.06   # metres (on a 1.7 m figure): clothes this near a clean hand's skeleton do not follow the arm


def clothes_off_new_hands(body, arm, new_hands, H):
    """Past a clean hand's wrist ring, only the hand itself may follow the arm there. A hand the generator fused to the
    clothes gave the clothes around it the hand's weights (the bone-heat proxy is made from the fused model), and a sheet
    of Clara's skirt closed with her fist: vertices past the ring within CLOTHES_NEAR_HAND of the new hand's skeleton that
    are not the new hand lose their arm weights, and those left with none go to the nearest body bone. Further off (a
    wide sleeve hanging past the wrist) they keep them. Returns how many vertices changed."""
    me = body.data
    hand = clean_hand_vertices(body)
    co = verts(body)
    groups = {g.name: g for g in body.vertex_groups}
    changed = []
    for side, h in new_hands.items():
        rel = co - np.array(h["centre"])
        past = (rel @ np.array(h["normal"]) > 0) & ~hand
        check = np.flatnonzero(past)
        past[check[~near_hand(co[check], h["points"], CLOTHES_NEAR_HAND * H / 1.7)]] = False
        idx = np.flatnonzero(past).tolist()
        arm_ids = {groups[f"{b}_{side}"].index for b in ARM_BONES if f"{b}_{side}" in groups}
        idx = [i for i in idx if any(ge.group in arm_ids and ge.weight > 0 for ge in me.vertices[i].groups)]
        for b in ARM_BONES:
            if idx and f"{b}_{side}" in groups:
                groups[f"{b}_{side}"].remove(idx)
        changed += idx
    to_body_bones(body, arm, [i for i in changed if not any(ge.weight > 0 for ge in me.vertices[i].groups)])
    return len(changed)


def to_body_bones(body, arm, orphan):
    """Give vertices with no weight at all to the closest of a few body bones."""
    if not orphan:
        return
    co = verts(body)
    groups = {g.name: g for g in body.vertex_groups}
    pts = co[orphan]
    best, best_d = None, None
    for name in BODY_BONES:
        b = arm.data.bones[name]
        a, c = np.array(b.head_local), np.array(b.tail_local)
        t = np.clip(((pts - a) @ (c - a)) / max(1e-9, (c - a) @ (c - a)), 0, 1)
        d = np.linalg.norm(pts - (a + t[:, None] * (c - a)), axis=1)
        if best is None:
            best, best_d = np.zeros(len(pts), dtype=int), d
        else:
            closer = d < best_d
            best[closer], best_d[closer] = BODY_BONES.index(name), d[closer]
    for k, name in enumerate(BODY_BONES):
        sel = [orphan[i] for i in np.flatnonzero(best == k).tolist()]
        if sel:
            groups[name].add(sel, 1.0, "REPLACE")


def cut_arm_bridges(body, sil, tracks):
    """Delete faces joining an arm to the body below the armpit.

    Image-to-3D models often weld a resting hand to the hip or skirt it touches. Those faces would stretch into a strand
    the moment the arm moves; nothing else below the armpit connects an arm to the torso."""
    arm_ids = {g.index for g in body.vertex_groups if g.name.rsplit("_", 1)[0] in ARM_BONES + ARM_EXTRAS}
    armpit_z = min(sil.z(tracks[side][0]) for side in tracks)
    n = len(body.data.vertices)
    arm_w = np.zeros(n)
    for v in body.data.vertices:
        arm_w[v.index] = sum(ge.weight for ge in v.groups if ge.group in arm_ids)
    bm = bmesh.new()
    bm.from_mesh(body.data)
    bm.verts.ensure_lookup_table()
    doomed = []
    for f in bm.faces:
        ids = [v.index for v in f.verts]
        w = arm_w[ids]
        if w.max() - w.min() > 0.5 and max(v.co.z for v in f.verts) < armpit_z:      # a hard edge left by the cleanup
            doomed.append(f)
    bmesh.ops.delete(bm, geom=doomed, context="FACES_ONLY")
    bm.to_mesh(body.data)
    bm.free()
    body.data.update()
    return len(doomed)


ARM_BONES = ("upperarm", "lowerarm", "hand")
ARM_EXTRAS = ("upperarm_twist_01", "lowerarm_twist_01") + tuple(f"{f}_0{j}" for f in FINGERS for j in (1, 2, 3))
BODY_BONES = ("pelvis", "spine_01", "spine_02", "thigh_l", "thigh_r")


# ---------------------------------------------------------------- the medium skeleton's weights

def smoothstep(a, b, x):
    t = np.clip((x - a) / (b - a), 0.0, 1.0)
    return t * t * (3 - 2 * t)


def group_weights(body, names):
    """{group name: weight of every vertex (numpy)} for the named vertex groups, in one pass over the vertices."""
    index = {body.vertex_groups[n].index: n for n in names if n in body.vertex_groups}
    out = {n: np.zeros(len(body.data.vertices)) for n in names}
    for v in body.data.vertices:
        for ge in v.groups:
            n = index.get(ge.group)
            if n is not None:
                out[n][v.index] = ge.weight
    return out


def set_weights(body, name, idx, w):
    """Set group `name` to weights w on vertices idx (0 removes the vertex), grouping equal weights into one call."""
    g = body.vertex_groups[name]
    zero = idx[w <= 1e-4]
    if len(zero):
        g.remove(zero.tolist())
    keep = w > 1e-4
    q = np.round(w[keep] * 1000).astype(int)
    for value in np.unique(q):
        g.add(idx[keep][q == value].tolist(), value / 1000.0, "REPLACE")


def face_side(body, through, up):
    """For every vertex, how far above the plane (through, up) the centres of the faces around it are on average:
    which side of a cut the vertex is on."""
    me = body.data
    centres = np.empty(len(me.polygons) * 3)
    me.polygons.foreach_get("center", centres)
    starts = np.empty(len(me.polygons), dtype=np.int64)
    totals = np.empty(len(me.polygons), dtype=np.int64)
    me.polygons.foreach_get("loop_start", starts)
    me.polygons.foreach_get("loop_total", totals)
    loop_vert = np.empty(len(me.loops), dtype=np.int64)
    me.loops.foreach_get("vertex_index", loop_vert)
    loop_poly = np.repeat(np.arange(len(me.polygons)), totals)
    height = (centres.reshape(-1, 3) - np.array(through)) @ np.array(up)
    count = np.bincount(loop_vert, minlength=len(me.vertices))
    total = np.bincount(loop_vert, weights=height[loop_poly], minlength=len(me.vertices))
    own = (verts(body) - np.array(through)) @ np.array(up)
    return np.where(count > 0, total / np.maximum(count, 1), own)


def medium_weights(body, H, J, palms, face, parted=False):
    """Share the low skeleton's weights with the medium skeleton's extra bones, by where each vertex is:
      - hand -> fingers: a vertex past a finger's knuckle (the thumb: past its base in the palm) and within 2-3 cm of
        that finger's line goes to it, and along the finger to its first, second or third bone, blended over a fifth of
        a bone at each joint; between two fingers that nearly touch, by distance (3 mm soft);
      - forearm -> its twist bone towards the wrist, upper arm -> its twist bone towards the shoulder;
      - head (and neck) -> jaw: below the line where the lips meet and in front of the jaw joint, fading out under the
        chin and towards the ears. With the lips parted (part_lips), each side of the cut goes wholly with its lip:
        which side, by the faces around the vertex against the cut itself (judged against the lips' curved line
        instead, the upper lip's edge near the corners went with the jaw and hung across the open mouth).
    Returns {bone: number of vertices given weight}."""
    s = H / 1.7
    co = verts(body)
    names = [f"{b}_{side}" for side in "lr" for b in ("hand", "lowerarm", "upperarm")] + ["head", "neck_01"]
    W = group_weights(body, names)
    counts = {}

    def give(bone, idx, w):
        set_weights(body, bone, idx, w)
        counts[bone] = int((w > 1e-4).sum())

    for side in "lr":
        # fingers
        idx = np.flatnonzero(W[f"hand_{side}"] > 0)
        p, wh = co[idx], W[f"hand_{side}"][idx]
        chains = {f: [np.array(J[f"{f}_{j}_{side}"]) for j in (1, 2, 3, 4)] for f in FINGERS}
        dist = np.full((5, len(idx)), np.inf)
        arc = np.zeros((5, len(idx)))
        past = np.zeros((5, len(idx)))
        for k, f in enumerate(FINGERS):
            P, run = list(chains[f]), 0.0
            tip = P[3] - P[2]
            if np.linalg.norm(tip) > 1e-6:          # SDPose's fingertip can fall short of the mesh's: the last bone reaches
                P.append(P[3] + tip / np.linalg.norm(tip) * 0.025 * s)   # 2.5 cm on, or the tip stayed with the hand
            for j in range(len(P) - 1):
                a, b = P[j], P[j + 1]
                ab = b - a
                L = float(np.linalg.norm(ab))
                t = np.clip((p - a) @ ab / max(L * L, 1e-12), 0, 1)
                d = np.linalg.norm(p - (a + t[:, None] * ab), axis=1)
                closer = d < dist[k]
                dist[k][closer], arc[k][closer] = d[closer], run + t[closer] * L
                run += L
            first = P[1] - P[0]
            past[k] = (p - P[0]) @ first / max(float(first @ first), 1e-12)
        nearest = dist.min(0)
        soft = 0.003 * s
        share = np.exp(-(dist - nearest) / soft) * (dist < nearest + 3 * soft)
        share /= share.sum(0)
        total = np.zeros(len(idx))
        for k, f in enumerate(FINGERS):
            on = (smoothstep(-0.1, 0.35, past[k]) if f == "thumb" else smoothstep(-0.25, 0.1, past[k]))
            on = on * (1 - smoothstep(0.02 * s, 0.03 * s, dist[k])) * share[k]
            total += on
            L = [float(np.linalg.norm(chains[f][j + 1] - chains[f][j])) for j in range(3)]
            b1, b2 = 0.2 * min(L[0], L[1]), 0.2 * min(L[1], L[2])
            s1 = smoothstep(L[0] - b1, L[0] + b1, arc[k])
            s2 = smoothstep(L[0] + L[1] - b2, L[0] + L[1] + b2, arc[k])
            for j, part in ((1, 1 - s1), (2, s1 * (1 - s2)), (3, s2)):
                give(f"{f}_0{j}_{side}", idx, wh * on * part)
        set_weights(body, f"hand_{side}", idx, wh * (1 - total))
        # twist bones
        shoulder, elbow, wrist = (np.array(J[f"{b}_{side}"]) for b in ("upperarm", "lowerarm", "hand"))
        for bone, a, b, share_of in ((f"lowerarm_{side}", elbow, wrist, lambda t: smoothstep(0.25, 0.9, t)),
                                     (f"upperarm_{side}", shoulder, elbow, lambda t: 1 - smoothstep(0.1, 0.55, t))):
            idx = np.flatnonzero(W[bone] > 0)
            w = W[bone][idx]
            t = (co[idx] - a) @ (b - a) / max(float((b - a) @ (b - a)), 1e-12)
            part = share_of(t)
            give(bone.replace("arm_", "arm_twist_01_"), idx, w * part)
            set_weights(body, bone, idx, w * (1 - part))
    # jaw
    if face is not None:
        pts = np.array([list(q) for q in face])
        jaw_joint = np.array(J["jaw"])
        mouth = [pts[60], (pts[61] + pts[67]) / 2, (pts[62] + pts[66]) / 2, (pts[63] + pts[65]) / 2, pts[64]]
        mouth.sort(key=lambda q: q[0])
        mx, mz = [q[0] for q in mouth], [q[2] for q in mouth]
        edge = max(abs(pts[0][0]), abs(pts[16][0]))              # the face's half-width at the jawline's top
        corner = max(abs(mx[0]), abs(mx[-1]))
        head_w = W["head"] + W["neck_01"]
        near = (np.abs(co[:, 0]) < edge + 0.01 * s) & (co[:, 2] > pts[8][2] - 0.05 * s) & (co[:, 2] < jaw_joint[2] + 0.01 * s)
        idx = np.flatnonzero((head_w > 0) & near)
        x, y, z = co[idx, 0], co[idx, 1], co[idx, 2]
        line = np.interp(x, mx, mz)                              # where the lips meet, between the mouth corners
        rise = np.clip((np.abs(x) - corner) / max(edge - corner, 1e-6), 0, 1)
        line = line + rise * (jaw_joint[2] - line)               # beyond the corners it rises to the jaw joint
        below_lips = smoothstep(-0.002 * s, 0.004 * s, line - z)
        if parted:                                               # each side of the cut with its own lip
            through, up, cx0, cx1 = parted
            side = face_side(body, through, up)[idx]
            on_cut = (x >= cx0) & (x <= cx1) & (np.abs(line - z) < 0.006 * s)
            below_lips = np.where(on_cut, (side < 0).astype(float), below_lips)
            # the lower lip pulls less towards the corners, so the mouth opens lens-shaped rather than as a box
            corner = smoothstep(0.0, 0.35 * (cx1 - cx0) / 2, np.minimum(x - cx0, cx1 - x))
            deep = smoothstep(0.003 * s, 0.012 * s, line - z)
            inside = (x >= cx0) & (x <= cx1)
            below_lips = np.where(inside, below_lips * (corner + (1 - corner) * deep), below_lips)
        chin_y, chin_z = pts[8][1], pts[8][2] - 0.004 * s
        angle_z = (pts[4][2] + pts[12][2]) / 2                   # the jaw's corners
        under = np.interp(y, [chin_y, jaw_joint[1]], [chin_z, angle_z])
        above_neck = smoothstep(-0.012 * s, 0.004 * s, z - under)
        in_front = smoothstep(-0.004 * s, 0.012 * s, jaw_joint[1] - y)
        part = below_lips * above_neck * in_front
        give("jaw", idx, head_w[idx] * part)
        for g in ("head", "neck_01"):
            set_weights(body, g, idx, W[g][idx] * (1 - part))
    return counts


def near_hand(p, hand, r):
    """Which of the points p are within r of a hand's skeleton (wrist to each knuckle, and each finger's joints), from
    SDPose's 21 hand points."""
    q = [np.array(v) for v, _s in hand]
    segments = [(q[0], q[i]) for i in (1, 5, 9, 13, 17)] + [(q[i], q[i + 1]) for k in range(5) for i in range(1 + 4 * k, 4 + 4 * k)]
    d = np.full(len(p), np.inf)
    for a, b in segments:
        ab = b - a
        t = np.clip((p - a) @ ab / max(float(ab @ ab), 1e-12), 0, 1)
        d = np.minimum(d, np.linalg.norm(p - (a + t[:, None] * ab), axis=1))
    return d < r


def keep_arms_off_body(body, arm, sil, tracks, H, hands=None, keep=None):
    """Below the armpit, arm bones may only move vertices inside that arm's own silhouette.

    A hand hanging against a hip pouch or skirt is one surface in the voxel proxy, so bone heat gives the pouch to the
    hand and a wave drags it along. Vertices that lose every weight go to the nearest body bone.
    hands: SDPose's hand points per side; vertices within 2 cm of that hand's skeleton always stay the arm's. On Airi's
    game version the fingers curl in front of her coat, outside the arm's outline band, and lost their arm weights; the
    welded-hand cut then took 155 faces out of her hands. keep: vertices that always stay the arm's (the clean hands:
    clean_hand_vertices), whatever the outline says."""
    co = verts(body)
    n = len(co)
    groups = {g.name: g for g in body.vertex_groups}
    rows = np.clip((co[:, 2] / sil.cell).astype(int), 0, len(sil.runs) - 1)
    margin = 2 * sil.cell
    strip = np.zeros(n, dtype=bool)
    for side, sign in (("l", 1), ("r", -1)):
        armpit, track = tracks[side]
        lo = np.full(len(sil.runs), np.nan)
        hi = np.full(len(sil.runs), np.nan)
        for z, x, w in track:
            r = sil.row(z)
            lo[r], hi[r] = x - w / 2 - margin, x + w / 2 + margin
        last_r = sil.row(track[-1][0]) if track else armpit
        inner = (lo[last_r] if sign > 0 else hi[last_r]) if track else 0.0
        below_armpit = co[:, 2] < sil.z(armpit)
        tracked = ~np.isnan(lo[rows])
        inside = tracked & (co[:, 0] >= np.nan_to_num(lo[rows], nan=np.inf)) & (co[:, 0] <= np.nan_to_num(hi[rows], nan=-np.inf))
        beyond = (rows < last_r) & (sign * co[:, 0] >= sign * inner - 0.01 * H)     # fingers may curl in a little
        allowed = ~below_armpit | inside | beyond
        if keep is not None:
            allowed |= keep
        if hands and side in hands:
            check = np.flatnonzero(below_armpit & ~allowed)
            allowed[check[near_hand(co[check], hands[side], 0.02 * H / 1.7)]] = True
        arm_ids = {groups[f"{b}_{side}"].index for b in ARM_BONES if f"{b}_{side}" in groups}
        candidates = np.flatnonzero(below_armpit & ~allowed).tolist()
        idx = [i for i in candidates if any(ge.group in arm_ids and ge.weight > 0 for ge in body.data.vertices[i].groups)]
        strip[idx] = True
        for b in ARM_BONES:
            g = groups.get(f"{b}_{side}")
            if g and idx:
                g.remove(idx)
    # vertices left with no weight at all go to the closest of a few body bones
    idx = np.flatnonzero(strip)
    to_body_bones(body, arm, [i for i in idx.tolist() if not any(ge.weight > 0 for ge in body.data.vertices[i].groups)])
    return int(strip.sum())


LEG_BONES = ("thigh", "calf", "foot", "ball")


def pieces_of(body, idx):
    """Connected pieces of the mesh among vertices idx (edges with both ends in idx): a piece number per vertex of idx."""
    n = len(body.data.vertices)
    edges = np.empty(len(body.data.edges) * 2, dtype=np.int64)
    body.data.edges.foreach_get("vertices", edges)
    edges = edges.reshape(-1, 2)
    inside = np.zeros(n, dtype=bool)
    inside[idx] = True
    edges = edges[inside[edges[:, 0]] & inside[edges[:, 1]]]
    parent = np.arange(n)

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for a, b in edges.tolist():
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)
    return np.array([find(i) for i in idx.tolist()])


def keep_legs_apart(body, arm, H):
    """Below the knees, a vertex of one leg keeps no weight from the other leg's bones.

    Feet or shoes standing together are one blob in the voxel proxy, so bone heat gives part of each shoe to the other
    foot, and any clip whose feet part tears strips off one shoe and drags them after the other (Laura: 4545 vertices
    below 60 cm had over a quarter of their leg weight from the other leg; the library's walk showed it). A vertex is
    of the leg whose bones it is nearer, when it is within 7 cm of them. Where the shoes touch that is a close call,
    so a vertex goes with the piece of mesh it belongs to when it can: a generated model's lower legs are hundreds of
    separate pieces, and a piece whose clearly placed vertices (a quarter nearer one leg) are nearly all of one leg goes
    wholly to it. Faces left joining the two legs are deleted (they would stretch into strands), as are hands' welds
    to the hips (cut_arm_bridges). The middle of a long skirt or robe, more than 7 cm from both legs, still stretches
    between them. Vertices left with no weight go to their own leg's nearest bone.
    Returns (vertices that lost weights, faces deleted)."""
    s = H / 1.7
    co = verts(body)
    bones = arm.data.bones
    knee = min(bones["calf_l"].head_local.z, bones["calf_r"].head_local.z)
    low = np.flatnonzero(co[:, 2] < knee)
    dist, nearest = {}, {}
    for side in "lr":
        ankle = bones[f"foot_{side}"].head_local
        chain = [(f"calf_{side}", bones[f"calf_{side}"].head_local, ankle),
                 (f"foot_{side}", ankle, bones[f"ball_{side}"].head_local),
                 (f"ball_{side}", bones[f"ball_{side}"].head_local, bones[f"ball_{side}"].tail_local),
                 (f"foot_{side}", ankle, ankle * Vector((1, 1, 0)))]          # down to the heel (a stiletto's too)
        best = np.full(len(low), np.inf)
        which = np.zeros(len(low), dtype=int)
        for k, (_name, a, b) in enumerate(chain):
            a, b = np.array(a), np.array(b)
            ab = b - a
            t = np.clip((co[low] - a) @ ab / max(float(ab @ ab), 1e-12), 0, 1)
            d = np.linalg.norm(co[low] - (a + t[:, None] * ab), axis=1)
            closer = d < best
            best[closer], which[closer] = d[closer], k
        dist[side], nearest[side] = best, [chain[k][0] for k in which]
    pairs = (("l", "r"), ("r", "l"))
    sure = {side: (dist[side] < 0.07 * s) & (dist[side] < 0.75 * dist[other]) for side, other in pairs}
    piece = pieces_of(body, low)
    ids, inverse = np.unique(piece, return_inverse=True)
    votes = {side: np.bincount(inverse, weights=sure[side].astype(float), minlength=len(ids)) for side in "lr"}
    total = votes["l"] + votes["r"]
    whole = {side: ((total >= 10) & (votes[side] >= 0.9 * total))[inverse] for side in "lr"}     # pieces of one leg only
    near = {side: (dist[side] < 0.07 * s) & (dist[side] <= dist[other]) for side, other in pairs}
    placed = {side: (near[side] & ~whole[other]) | whole[side] for side, other in pairs}
    groups = {g.name: g for g in body.vertex_groups}
    changed = 0
    for side, other in (("l", "r"), ("r", "l")):
        pick = np.flatnonzero(placed[side])
        ids = {groups[f"{b}_{other}"].index for b in LEG_BONES if f"{b}_{other}" in groups}
        idx = [int(low[i]) for i in pick if any(ge.group in ids and ge.weight > 0 for ge in body.data.vertices[int(low[i])].groups)]
        for b in LEG_BONES:
            g = groups.get(f"{b}_{other}")
            if g and idx:
                g.remove(idx)
        changed += len(idx)
        where = {int(low[i]): nearest[side][i] for i in pick}
        for i in idx:
            if not any(ge.weight > 0 for ge in body.data.vertices[i].groups):
                groups[where[i]].add([i], 1.0, "REPLACE")
    # faces with corners on both legs
    code = np.zeros(len(co), dtype=np.int8)
    code[low[placed["l"]]], code[low[placed["r"]]] = 1, 2
    me = body.data
    starts = np.empty(len(me.polygons), dtype=np.int64)
    me.polygons.foreach_get("loop_start", starts)
    loop_vert = np.empty(len(me.loops), dtype=np.int64)
    me.loops.foreach_get("vertex_index", loop_vert)
    corner = code[loop_vert]
    both = np.flatnonzero(np.maximum.reduceat(corner == 1, starts) & np.maximum.reduceat(corner == 2, starts))
    if len(both):
        bm = bmesh.new()
        bm.from_mesh(me)
        bm.faces.ensure_lookup_table()
        bmesh.ops.delete(bm, geom=[bm.faces[i] for i in both.tolist()], context="FACES_ONLY")
        bm.to_mesh(me)
        bm.free()
        me.update()
    return changed, len(both)


def keep_largest_piece(ob):
    """Delete everything but the biggest connected piece. Stray floating bits make bone-heat fail for EVERY bone."""
    bm = bmesh.new()
    bm.from_mesh(ob.data)
    seen, pieces = set(), []
    for v in bm.verts:
        if v.index in seen:
            continue
        stack, piece = [v], [v]
        seen.add(v.index)
        while stack:
            x = stack.pop()
            for e in x.link_edges:
                o = e.other_vert(x)
                if o.index not in seen:
                    seen.add(o.index)
                    stack.append(o)
                    piece.append(o)
        pieces.append(piece)
    pieces.sort(key=len, reverse=True)
    bmesh.ops.delete(bm, geom=[v for piece in pieces[1:] for v in piece], context="VERTS")
    bm.to_mesh(ob.data)
    bm.free()
    return len(pieces) - 1


# ---------------------------------------------------------------- clips

AX = {"x": Vector((1, 0, 0)), "y": Vector((0, 1, 0)), "z": Vector((0, 0, 1))}


def rot(axis, deg):
    return Quaternion(AX[axis], math.radians(deg))


def arms_down(side, deg):
    """Rotate an A-pose arm towards the body (character's left arm is on +X)."""
    return rot("y", deg if side == "l" else -deg)


def relaxed(side, extra=0.0):
    """The arm hanging close to the body, whatever angle the model's A-pose has."""
    return arms_down(side, ARM_DOWN[side] + extra)


def pose_frame(arm, pose, loc=None):
    """pose: {bone: armature-space rotation about the bone's own head}; loc: {bone: armature-space offset}."""
    loc = loc or {}
    for pb in arm.pose.bones:
        rest = pb.bone.matrix_local.to_quaternion()
        q = pose.get(pb.name, Quaternion())
        pb.rotation_mode = "QUATERNION"
        pb.rotation_quaternion = rest.inverted() @ q @ rest
        pb.location = rest.inverted() @ loc.get(pb.name, Vector())


def key_all(arm, frame):
    for pb in arm.pose.bones:
        pb.keyframe_insert("rotation_quaternion", frame=frame)
        pb.keyframe_insert("location", frame=frame)


def clip_idle(t, H):
    """Standing still: a slow breath in the chest and shoulders and a small drift of the head. No hip sway."""
    b = math.sin(2 * math.pi * t)
    s = math.sin(2 * math.pi * t + 0.8)
    pose = {
        "spine_02": rot("x", -1.0 * b), "spine_03": rot("x", -1.2 * b),
        "neck_01": rot("x", 0.9 * b), "head": rot("z", 1.2 * s) @ rot("x", 0.4 * b),
    }
    for side, sg in (("l", 1), ("r", -1)):
        pose[f"upperarm_{side}"] = rot("x", 0.8 * s) @ relaxed(side, 0.6 * b)
        pose[f"lowerarm_{side}"] = rot("x", -8 - 1.5 * b)
        pose[f"clavicle_{side}"] = rot("y", -sg * 1.5 * b)      # shoulders lift a little on the in-breath
    return pose, {}


def clip_walk(t, H):
    p = 2 * math.pi * t
    pose = {"pelvis": rot("z", 5 * math.sin(p)) @ rot("y", 2 * math.sin(p)),
            "spine_02": rot("z", -4 * math.sin(p)), "spine_03": rot("z", -3 * math.sin(p)) @ rot("x", -3),
            "head": rot("z", 2 * math.sin(p))}
    for side, ph in (("l", 0.0), ("r", math.pi)):
        a = p + ph
        swing = 24 * math.sin(a)                                  # + = leg forward
        knee = 8 + 28 * max(0.0, math.sin(a + 1.9)) + 8 * max(0.0, math.sin(a - 0.3))
        pose[f"thigh_{side}"] = rot("x", -swing)
        pose[f"calf_{side}"] = rot("x", knee)
        pose[f"foot_{side}"] = rot("x", -0.5 * knee + 0.4 * swing)
        pose[f"upperarm_{side}"] = rot("x", 20 * math.sin(a)) @ relaxed(side, 2)
        pose[f"lowerarm_{side}"] = rot("x", -12 - 10 * max(0.0, -math.sin(a)))
    bob = 0.012 * H * (math.cos(2 * p) - 1) / 2
    return pose, {"pelvis": Vector((0, 0, bob))}


REST_DIR = {}      # armature-space rest direction of the right arm bones, set once the skeleton is built


def _twist(q, axis):
    """Signed angle (degrees) of q's twist about axis (swing-twist decomposition)."""
    return math.degrees(2 * math.atan2(Vector((q.x, q.y, q.z)).dot(axis), q.w))


def wave_pose():
    """Right upper arm out to the side, forearm up, palm facing forward. Returns the upper arm's swing (rest direction to
    goal direction), its twist about the goal direction in degrees, and the forearm's world rotation (the wag goes on top)."""
    u0, f0 = REST_DIR["upperarm_r"], REST_DIR["lowerarm_r"]
    u_goal = Vector((-0.95, -0.25, 0.12)).normalized()     # out to her right, a little forward, just above the shoulder
    f_goal = Vector((-0.12, -0.12, 1.0)).normalized()      # forearm up
    fore = f0.rotation_difference(f_goal)
    palm = fore @ (Vector((1, 0, 0)) - f0 * f0.x).normalized()        # a hanging right palm faces the thigh (+X)
    front = Vector((0, -1, 0)) - f_goal * -f_goal.y
    angle = palm.angle(front)
    if f_goal.dot(palm.cross(front)) < 0:
        angle = -angle
    F = Quaternion(f_goal, angle) @ fore                   # forearm world rotation: pointing up, palm to the front
    base = u0.rotation_difference(u_goal)
    best = None
    for deg in range(-180, 180, 5):                        # twist the upper arm so the elbow only bends, never wrings
        U = Quaternion(u_goal, math.radians(deg)) @ base
        tw = abs(_twist(U.inverted() @ F, f0))
        if best is None or tw < best[0]:
            best = (tw, deg)
    return base, best[1], F


def clip_wave(t, H):
    pose, loc = clip_idle(t, H)
    raise_ = min(1.0, t / 0.15) if t < 0.8 else max(0.0, (1 - t) / 0.2)
    ease = raise_ * raise_ * (3 - 2 * raise_)
    swing, twist, F = wave_pose()
    U = Quaternion(swing @ REST_DIR["upperarm_r"], math.radians(twist)) @ swing
    wag = math.radians(20 * math.sin(2 * math.pi * t * 6)) * ease
    side_to_side = Quaternion(Vector((0, 1, 0)), wag)     # about the front-to-back axis: side to side as seen from the front
    F_wag = side_to_side @ F
    # Lift along the direct arc and add the twist gradually: one slerp of the whole rotation swung the arm out behind her.
    part = Quaternion().slerp(swing, ease)
    U_now = Quaternion(part @ REST_DIR["upperarm_r"], math.radians(twist * ease)) @ part
    pose["upperarm_r"] = U_now @ pose["upperarm_r"].slerp(Quaternion(), ease)
    pose["lowerarm_r"] = pose["lowerarm_r"].slerp(U.inverted() @ F_wag, ease)
    flick = Quaternion(Vector((0, 1, 0)), 0.4 * wag)
    pose["hand_r"] = F_wag.inverted() @ flick @ F_wag if ease > 0 else Quaternion()
    return pose, loc


CLIPS = [("Idle", clip_idle, 4.0), ("Walk", clip_walk, 1.0), ("Wave", clip_wave, 3.0)]

FINGER_REST = {}   # finger bone -> (armature-space curl axis, its bend at rest in degrees; + is curled towards the palm)
BONE_DIR = {}      # arm bone -> armature-space rest direction, for handing its roll to the twist bones


def medium_rest(arm):
    """Record the medium skeleton's rest finger bends and arm directions (the clips pose the fingers relative to them)."""
    for b in arm.data.bones:
        if b.name.split("_")[0] in FINGERS:
            axis = b.matrix_local.to_3x3().col[0].normalized()
            d = (b.tail_local - b.head_local).normalized()
            pd = (b.parent.tail_local - b.parent.head_local).normalized()
            FINGER_REST[b.name] = (axis, math.degrees(math.atan2(pd.cross(d).dot(axis), pd.dot(d))))
        elif b.name.split("_")[0] in ("upperarm", "lowerarm") and "twist" not in b.name:
            BONE_DIR[b.name] = (b.tail_local - b.head_local).normalized()


def fingers(pose, side, curl, flat=0.0):
    """Curl every joint of one hand's fingers `curl` degrees past the model's own pose, then straighten towards flat
    (0-1; the thumb's base, which sits in the palm, is left as it is)."""
    for f in FINGERS:
        for j in (1, 2, 3):
            name = f"{f}_0{j}_{side}"
            if name in FINGER_REST:
                axis, rest = FINGER_REST[name]
                c = curl * (0.5 if f == "thumb" else 1.0)
                straight = 0.0 if (f == "thumb" and j == 1) else -rest
                pose[name] = Quaternion(axis, math.radians(c + flat * (straight - c)))


def twist_part(q, axis):
    """The part of rotation q that turns about `axis` (swing-twist decomposition: q = swing @ twist)."""
    v = Vector((q.x, q.y, q.z))
    p = axis * v.dot(axis)
    t = Quaternion((q.w, p.x, p.y, p.z))
    return t.normalized() if t.magnitude > 1e-9 else Quaternion()


def medium_pose(name, t, pose):
    """The medium skeleton's extras on top of a clip's pose: relaxed fingers (a breath's worth of movement in the idle),
    the waving hand opening flat, and each forearm's roll about its own length handed on to the hand, half of it to the
    forearm's twist bone, so the forearm turns gradually from elbow to wrist rather than all at once at the elbow
    (and the upper arm's twist bone takes back half of the upper arm's roll near the shoulder)."""
    breath = math.sin(2 * math.pi * t)
    for side in "lr":
        fingers(pose, side, 8.0 if name == "Walk" else 4.0 + 2.0 * breath)
    if name == "Wave":
        raise_ = min(1.0, t / 0.15) if t < 0.8 else max(0.0, (1 - t) / 0.2)
        fingers(pose, "r", 4.0, raise_ * raise_ * (3 - 2 * raise_))
    for side in "lr":
        q = pose.get(f"lowerarm_{side}")
        if q is not None and f"lowerarm_{side}" in BONE_DIR:
            twist = twist_part(q, BONE_DIR[f"lowerarm_{side}"])
            pose[f"lowerarm_{side}"] = q @ twist.inverted()
            pose[f"hand_{side}"] = twist @ pose.get(f"hand_{side}", Quaternion())
            pose[f"lowerarm_twist_01_{side}"] = Quaternion().slerp(twist, 0.5)
        q = pose.get(f"upperarm_{side}")
        if q is not None and f"upperarm_{side}" in BONE_DIR:
            twist = twist_part(q, BONE_DIR[f"upperarm_{side}"])
            pose[f"upperarm_twist_01_{side}"] = Quaternion().slerp(twist.inverted(), 0.5)
    return pose


def animate(arm, H, medium=False, library=None):
    """The clips, one action each, stacked as muted NLA tracks (the glTF exporter writes every action as a clip).
    library: the Universal Animation Library's GLB files; their clips are copied on (rig_library.py) and replace our
    Idle and Walk. Returns [{name, seconds, loop, source}]."""
    bpy.context.scene.render.fps = FPS              # the glTF exporter times keyframes by the scene rate (default 24)
    arm.animation_data_create()
    made = []
    if library:
        for clip in rig_library.add_clips(arm, library, FPS):
            made.append({"name": clip["name"], "seconds": clip["seconds"], "loop": clip["loop"],
                         "source": f"{os.path.splitext(os.path.basename(clip['file']))[0]}: {clip['source']}",
                         **{k: clip[k] for k in ("moves", "speed_m_s") if k in clip}})
    have = {m["name"] for m in made}
    for name, fn, seconds in CLIPS:
        if name in have:
            continue
        act = bpy.data.actions.new(name)
        act.use_fake_user = True
        arm.animation_data.action = act
        frames = int(round(seconds * FPS))
        for f in sorted(set(range(0, frames, 2)) | {frames}):      # every other frame, and always the last
            pose, loc = fn(f / frames, H)
            if medium:
                pose = medium_pose(name, f / frames, pose)
            pose_frame(arm, pose, loc)
            key_all(arm, f)                             # from frame 0, so a loop's last key lands exactly one period later
        track = arm.animation_data.nla_tracks.new()
        track.name = name
        strip = track.strips.new(name, 0, act)
        strip.name = name
        track.mute = True
        arm.animation_data.action = None
        made.append({"name": name, "seconds": seconds, "loop": name != "Wave", "source": "made here"})
    pose_frame(arm, {})
    return made


# ---------------------------------------------------------------- preview and export

def render_setup(H, width=300, height=400):
    """A CPU Cycles camera looking at the character. Headless Blender cannot open an OpenGL context on some Windows GPU
    drivers (Workbench and EEVEE crash), so previews never use the GPU."""
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = 12
    scene.cycles.use_denoising = False
    scene.cycles.max_bounces = 2
    scene.render.resolution_x, scene.render.resolution_y = width, height
    world = bpy.data.worlds.new("w")
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs["Color"].default_value = (0.85, 0.85, 0.85, 1)
    world.node_tree.nodes["Background"].inputs["Strength"].default_value = 1.0
    scene.world = world
    sun = bpy.data.objects.new("sun", bpy.data.lights.new("sun", "SUN"))
    sun.data.energy = 3.0
    sun.rotation_euler = (math.radians(50), 0, math.radians(-30))
    scene.collection.objects.link(sun)
    cam_data = bpy.data.cameras.new("cam")
    cam_data.type = "ORTHO"
    cam_data.ortho_scale = H * 1.25
    cam = bpy.data.objects.new("cam", cam_data)
    scene.collection.objects.link(cam)
    scene.camera = cam
    return scene, cam


def aim(cam, H, dx, dy):
    cam.location = Vector((dx * 4 * H, dy * 4 * H, H * 0.52))
    cam.rotation_euler = (Vector((0, 0, H * 0.52)) - cam.location).to_track_quat("-Z", "Y").to_euler()


VIEWS = {"front": (0, -1), "three_quarter": (math.sin(math.radians(35)), -math.cos(math.radians(35)))}


def poster(arm, H, path):
    """One still for the app: the walk clip mid-stride, from three-quarters."""
    scene, cam = render_setup(H, 384, 512)
    arm.animation_data.action = bpy.data.actions["Walk"]
    aim(cam, H, *VIEWS["three_quarter"])
    scene.frame_set(int(round(FPS * 0.25)))
    scene.render.filepath = path
    bpy.ops.render.render(write_still=True)
    arm.animation_data.action = None


def preview(arm, body, H, out_dir, clips):
    scene, cam = render_setup(H)
    shots = []
    for c in clips:
        clip = c["name"]
        act = bpy.data.actions[clip]
        arm.animation_data.action = act
        frames = int(round(c["seconds"] * FPS))
        for vname, (dx, dy) in {**VIEWS, "side": (-1, 0)}.items():
            aim(cam, H, dx, dy)
            for i in range(6):
                f = int((i + 0.3) * frames / 6)              # off the beat, so a wave is caught mid-swing
                scene.frame_set(f)
                path = os.path.join(out_dir, f"{clip.lower()}_{vname}_{i:02d}.png")
                scene.render.filepath = path
                bpy.ops.render.render(write_still=True)
                shots.append(path)
    arm.animation_data.action = None
    return shots


def export(path):
    """The GLB with every action as a clip. The mesh's armature deform is switched off meanwhile: the exporter samples
    each clip frame by frame and would deform the whole mesh at every frame for nothing (Laura with the library's 85
    clips: 85 s against 40 s, and the file is byte for byte the same)."""
    deform = [m for o in bpy.data.objects if o.type == "MESH" for m in o.modifiers if m.type == "ARMATURE" and m.show_viewport]
    for m in deform:
        m.show_viewport = False
    try:
        bpy.ops.object.select_all(action="SELECT")
        bpy.ops.export_scene.gltf(filepath=path, export_format="GLB", use_selection=False, export_skins=True,
                                  export_animations=True, export_animation_mode="ACTIONS", export_yup=True,
                                  export_apply=False, export_force_sampling=True, export_frame_range=False)
    finally:
        for m in deform:
            m.show_viewport = True


def main():
    o = args()
    H = o["height"]
    body, normalise = load(o["in"], H)
    # --shape: the untouched model that `in` was made from (a cleaned-up, simplified game version). Its dense, closed
    # surface gives the silhouette and the weight proxy; the skeleton and weights still go on `in`.
    shape = load(o["shape"], H, normalise)[0] if o.get("shape") else None
    source = shape or body
    J, notes, levels, sil, tracks = find_joints(source, H)
    medium, hands, K = None, None, None
    new_hands = replaced_hands(o["hands"], normalise) if o.get("hands") else {}
    if o.get("keypoints"):
        with open(o["keypoints"], encoding="utf-8") as fh:
            keys = json.load(fh)
        co = verts(source)
        K = rk.Keypoints(keys, source, co, lambda x, z, r: depth(co, x, z, r))
        hands, notes = keypoint_joints(K, J, levels, sil, tracks, co, H, notes)
    elif o["skeleton"] == "medium":
        raise SystemExit("The medium skeleton needs the pose keypoints: its fingers and jaw are placed from them")
    # the clean hands' own joints: exact, not read off renders, so the outline rig (no keypoints) takes them too; there
    # the outline's joints came from the generator's mitten, and the new fingers past it lost their arm weights
    for side, h in new_hands.items():
        hands = hands if hands is not None else {}
        hands[side] = h["points"]
        J[f"hand_{side}"], J[f"hand_end_{side}"] = h["points"][0][0].copy(), h["points"][9][0].copy()
        tracks[side] = (tracks[side][0], keypoint_track(sil, J, hands[side], tracks[side][0], side, H))
    if new_hands:
        notes.append("hands: the clean hands' own joints")
    if K is not None and o["skeleton"] == "medium":
        palms, face, found = medium_joints(K, J, hands, co, H)
        notes += found
        medium = (J, palms, face)
    if o.get("debug"):
        debug_image(sil, J, tracks, o["debug"])
        print("RIG_JOINTS " + json.dumps({"levels": levels, "notes": notes}))
        if o.get("joints_only"):
            return
    for side in "lr":
        d = J[f"hand_{side}"] - J[f"upperarm_{side}"]
        angle = math.degrees(math.atan2(abs(d.x), -d.z))
        ARM_DOWN[side] = max(0.0, angle - 9.0)       # leave the hand just clear of the hip
    levels["arm_angles"] = {s: round(ARM_DOWN[s] + 9.0, 1) for s in "lr"}
    arm = build_armature(J, H, medium[1] if medium else None)
    for name in ("upperarm_r", "lowerarm_r"):
        bone = arm.data.bones[name]
        REST_DIR[name] = (bone.tail_local - bone.head_local).normalized()
    mouth = {}
    if medium:
        medium_rest(arm)
        if medium[2] is not None:
            mouth["edges_cut"], cut = part_lips(body, medium[2], J["jaw"], H)
            medium = medium + (cut if mouth["edges_cut"] else None,)
    weights = skin(body, arm, H, sil, tracks, shape, medium, hands, new_hands)
    if weights["proxy_attempts"] > 1:
        notes.append(f"skin weights worked out on a copy at height/{weights['proxy_divisions']} (the first size defeated Blender's bone heat)")
    notes += [n for n in (idle_note(weights["bones_moving_nothing"]),) if n]
    if medium and medium[2] is not None:
        mouth["inside_vertices"] = add_mouth(body, medium[2], J["jaw"], H)
    if shape is not None:
        bpy.data.objects.remove(shape)
    library = o["library"].split("|") if o.get("library") else None
    clips = animate(arm, H, medium is not None, library)
    report = {"height_m": H, "levels": levels, "joints": {k: [round(c, 4) for c in v] for k, v in J.items()},
              "notes": notes, "weights": weights, "clips": [c["name"] for c in clips], "clip_info": clips,
              "bones": len(arm.data.bones),
              "skeleton": o["skeleton"], "joints_from": "pose keypoints" if o.get("keypoints") else "outline",
              **({"mouth": mouth} if mouth else {}),
              "vertices": len(body.data.vertices),
              "faces": len(body.data.polygons)}
    export(o["out"])
    report["out"] = o["out"]
    write_report(o, report)
    if o["poster"]:
        poster(arm, H, o["poster"])
    if o["preview"]:
        report["preview"] = preview(arm, body, H, o["preview"], clips)
        write_report(o, report)


def write_report(o, report):
    text = json.dumps(report, indent=1)
    print("RIG_REPORT " + text)
    if o["report"]:
        with open(o["report"], "w", encoding="utf-8") as fh:
            fh.write(text)


if __name__ == "__main__":
    try:
        main()
    except BaseException as exc:                   # one readable line for npcgen/rig3d.py, and a failing exit code
        if isinstance(exc, SystemExit) and exc.code in (None, 0):
            raise
        import traceback
        traceback.print_exc()
        message = exc.code if isinstance(exc, SystemExit) else f"{type(exc).__name__}: {exc}"
        print(f"RIG_ERROR {message}", flush=True)
        sys.stdout.flush()
        os._exit(1)
