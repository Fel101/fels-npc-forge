"""Joints from pose keypoints: SDPose's 2D points in three orthographic views of the model, turned into 3D. Runs inside
Blender, imported by rig_humanoid.py.

npcgen/posekeys.py renders the normalised model from the front and from each side (npcgen/blender/pose_views.py), asks
SDPose (ComfyUI) for its 133 whole-body points in each view and for the face again on a close-up, and hands this module
{"cams": {view: camera}, "views": {view: {"body", "feet", "face", "hand_l", "hand_r"}}} with points as [u, v, score].

  - Body joints (shoulders, elbows, wrists, hips, knees, ankles): across from the front view, depth from the side view
    that sees that side of the character, height the mean of the two. A side view sees the far arm only as a guess.
  - Fingers: the side view, where the fingers are spread (from the front they overlap), and across the hand the middle
    of the mesh's vertices on the line through each point, in the outermost 5 cm (the hand hangs outside the thigh or
    skirt). Casting a ray and taking the first piece of mesh it passes did not work: these models' surfaces are thin
    shells (a finger is a hollow tube of skin 5 mm thick), so the "piece" was only the near skin.
  - Face: the front close-up, depth from the first surface a ray meets.

Measured 2026-09-26 on five models (Laura, Airi, Clara, Imani, Devi): SDPose found every joint with scores 0.9-1.0 in
the views that see them; front and side views agreed on the height of the hand points to 5-7 mm on three models and
11-14 mm on Clara and Imani, whose curled fingers overlap from the front.
"""
import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree

BODY = {"nose": 0, "neck": 1, "shoulder_r": 2, "elbow_r": 3, "wrist_r": 4, "shoulder_l": 5, "elbow_l": 6, "wrist_l": 7,
        "hip_r": 8, "knee_r": 9, "ankle_r": 10, "hip_l": 11, "knee_l": 12, "ankle_l": 13, "eye_r": 14, "eye_l": 15,
        "ear_r": 16, "ear_l": 17}
FEET = {"big_toe_l": 0, "small_toe_l": 1, "heel_l": 2, "big_toe_r": 3, "small_toe_r": 4, "heel_r": 5}
SIDE_VIEW = {"l": "left", "r": "right"}         # the view that sees each side of the character (its left is +X)
FINGERS = ("thumb", "index", "middle", "ring", "pinky")
MIN_SCORE = 0.3


class View:
    """An orthographic render: pixel (u, v) <-> a point on the plane through the camera centre."""

    def __init__(self, cam):
        self.w, self.h = cam["size"]
        self.px = cam["px"]
        self.c, self.fwd = Vector(cam["centre"]), Vector(cam["forward"])
        self.right, self.up = Vector(cam["right"]), Vector(cam["up"])

    def plane(self, u, v):
        return self.c + self.right * ((u - self.w / 2) * self.px) + self.up * ((self.h / 2 - v) * self.px)

    def pixel(self, p):
        d = Vector(p) - self.c
        return self.w / 2 + d.dot(self.right) / self.px, self.h / 2 - d.dot(self.up) / self.px


class Keypoints:
    def __init__(self, data, ob, co, depth):
        """co: the mesh's vertices (numpy, n x 3). depth(x, z, r): the middle, front to back, of the mesh within r of
        (x, z) as the front view sees it."""
        self.views = {name: View(cam) for name, cam in data["cams"].items()}
        self.kp = data["views"]
        self.bvh = BVHTree.FromObject(ob, _depsgraph())
        self.co = co
        self.depth = depth

    # ---- points ----------------------------------------------------------------------------------------------------
    def point(self, view, part, i):
        """(plane point, score) of point i of `part` in `view`."""
        u, v, s = self.kp[view][part][i]
        return self.views[view].plane(u, v), s

    def body(self, name, side=None, r=0.05):
        """A body joint inside the limb: across from the front view, height the mean of the front and side views, and
        depth the middle of the mesh around it (as the outline rig does). The side views' own depth is not used: clothes
        are thin shells in these models, and on Laura's dress and Imani's trousers it put the two hips on opposite faces
        of the cloth. Returns (Vector, score), the lower of the views' scores."""
        front, fs = self.point("front", "body", BODY[name])
        if side is None:                                   # on the midline: both side views
            (a, sa), (b, sb) = self.point("left", "body", BODY[name]), self.point("right", "body", BODY[name])
            z, s = (front.z + a.z + b.z) / 3, min(fs, sa, sb)
        else:
            p, ss = self.point(SIDE_VIEW[side], "body", BODY[name])
            z, s = (front.z + p.z) / 2, min(fs, ss)
        return Vector((front.x, self.depth(front.x, z, r), z)), s

    def foot(self, name):
        """A foot point: across from the front, depth and height from the side view that sees it."""
        side = name[-1]
        front, fs = self.point("front", "feet", FEET[name])
        p, ss = self.point(SIDE_VIEW[side], "feet", FEET[name])
        return Vector((front.x, p.y, (front.z + p.z) / 2)), min(fs, ss)

    def across(self, y, z, r, outward, window=0.05):
        """The middle across (x) of the mesh's vertices within r of the line (y, z), in their outermost `window` on the
        side `outward` (+1: the character's left, +X). None when no vertex is there."""
        m = (np.abs(self.co[:, 1] - y) < r) & (np.abs(self.co[:, 2] - z) < r)
        xs = self.co[m, 0] * outward
        if len(xs) < 3:
            return None
        xs = xs[xs > xs.max() - window]
        return outward * float((np.percentile(xs, 5) + np.percentile(xs, 95)) / 2)

    def hand(self, side, r=0.004):
        """The 21 hand points of one hand as [(Vector, score)], from the side view that sees the hand: height and depth
        from the picture, across from the middle of the hand's vertices there (across()). A point with no vertices near
        it (just past a fingertip), or whose vertices lie more than 3.5 cm across from the hand's middle (the median of
        its points: its line missed the finger and met the trousers behind, giving 11-15 cm "finger bones" on Imani),
        takes the hand's middle across, and its score drops to 0.2. (Judged against the point before instead, fingers
        that flare 2-4 cm out from the wrist, as Laura's do, were rejected.)"""
        view_name = SIDE_VIEW[side]
        outward = 1 if side == "l" else -1
        pts = [self.point(view_name, f"hand_{side}", i) for i in range(21)]
        xs = [self.across(p.y, p.z, r, outward) for p, _s in pts]
        found = [x for x in xs if x is not None]
        middle = float(np.median(found)) if found else self.body(f"wrist_{side}", side)[0].x
        out = []
        for (p, s), x in zip(pts, xs):
            if x is None or abs(x - middle) > 0.035:
                out.append((Vector((middle, p.y, p.z)), min(s, 0.2)))
            else:
                out.append((Vector((x, p.y, p.z)), s))
        return self.fit_fingers(out)

    def fit_fingers(self, out, step=0.002, reach=0.06, r=0.005):
        """Fingers SDPose drew longer than the mesh's: on Devi's game version the fingertip points hung 3 cm below the
        lowest vertex of her hand (short, curled fingers), so the last bones had no mesh to move. After a finger's last
        sure joint (from the knuckle out), the finger is followed on in its own direction to where the mesh ends (no
        vertex within 5 mm), and its remaining joints are spaced evenly up to there (score 0.25)."""
        for k in range(5):
            chain = [1 + 4 * k + j for j in range(4)]
            sure = 0
            while sure < 4 and out[chain[sure]][1] >= MIN_SCORE:
                sure += 1
            if sure in (0, 4):
                continue
            anchor = out[chain[sure - 1]][0]
            before = out[chain[sure - 2]][0] if sure >= 2 else out[0][0]
            d = anchor - before
            if d.length < 1e-6:
                continue
            d.normalize()
            a = np.array(anchor)
            near = self.co[np.linalg.norm(self.co - a, axis=1) < reach + r]
            end = 0.0
            for t in np.arange(step, reach + 1e-9, step):
                q = a + np.array(d) * t
                if len(near) and (np.linalg.norm(near - q, axis=1) < r).any():
                    end = t
            end = max(end, 0.01)
            left = 4 - sure
            for m in range(1, left + 1):
                out[chain[sure - 1 + m]] = (anchor + d * (end * m / left), 0.25)
        return out

    def face(self):
        """The 68 face points as [(Vector on the surface, score)], from the front close-up when there is one."""
        view = self.views["front"]
        part = "face_zoom" if "face_zoom" in self.kp["front"] else "face"
        out = []
        for i in range(68):
            p, s = self.point("front", part, i)
            hit = self.bvh.ray_cast(p - view.fwd * 3.0, view.fwd, 6.0)[0]
            out.append((hit if hit is not None else p, s))
        return out


def _depsgraph():
    import bpy
    return bpy.context.evaluated_depsgraph_get()


def finger_chain(points, finger):
    """The four points of a finger (from the wrist outwards), from SDPose's 21 hand points."""
    k = FINGERS.index(finger)
    return [points[1 + 4 * k + j] for j in range(4)]
