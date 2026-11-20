"""Where a pair of glasses goes on a character's face: position, turn and size from the eyes and the shape of the face.

Input is a face scan (npcgen/blender/face_scan.py, npcgen/facescan.py): the middle of each eye, the face's width, and the face's front
surface as a grid of depths. Output is where the pair's origin (the middle of its bridge) stands in the character's own space and how it
is turned and scaled, plus a report. Pure numpy, so it is quick and testable; nothing here touches Blender or the GPU.

How the fit is made (all in the model's space: metres, +Y up, the face looking toward +Z, the wearer's left at +X; docs/glasses.md has the long form):
  * Turn: the pair's line of lenses is made parallel to the line between the eyes (its roll about the front-to-back axis, up to MAX_ROLL, its
    yaw about the vertical one, up to MAX_YAW). It is never pitched; the tilt is a nudge.
  * Size: a pair is made in a real adult's measurements (a 63 mm distance between the pupils, a 135 mm face at the jaw's two ends), so
    the size it needs on a character is how much bigger or smaller that character's face is: the eyes' distance and the face's width
    each give a ratio and the pair is scaled by their mean. (The pair's own `scale` is already in the file and is not touched.)
  * Up and across: the pair's line of lens centres goes through the middle of the eyes.
  * Depth: the pair slides forward until nothing of the frame or the lenses is inside the skin (a millimetre of room), which is where a
    real pair rests. Three refinements, each from a character that looked wrong without it: the lenses stay at least EYE_ROOM in front of
    the eyes (a flat nose would let them touch the lashes); the upper rim and the outer corners, where what is in front of the face is hair,
    a brow or a hat, may push the pair out by only HAIR_CAP more than the rest of it, and never past MAX_REACH from the eyes; and the nose
    is a soft contact, never allowed to hold the lenses further than MAX_REACH from the eyes: the inner rims are pressed into it instead.
  * A nudge (up, forward, tilt, size) is applied last, along the pair's own axes.
  * Lengths below are metres at the pair's own size: they are multiplied by the pair's scale, so they mean the same on a big head and on a model that is not in metres.
"""
import base64

import numpy as np

from . import glbmerge, procmesh as pm
from .paint3d import Glb

REF_IPD = 0.063                  # metres between an adult's pupils: the size the pair is made for
REF_FACE = 0.135                 # ... and the width of the face at the jaw's two ends
ROOM = 0.001                     # kept between the frame and the skin
EYE_ROOM = 0.010                 # the least distance from the back of a lens to the eye in front of it
HAIR_CAP = 0.004                 # how much further forward the upper rim and the outer corners may push the pair than the rest of it
UPPER = 0.012                    # a frame point this far above the line of lens centres belongs to the upper rim
NOSE_X = 0.024                   # a frame point nearer than this to the middle, and no higher than NOSE_TOP above the line of lens centres, is in the nose's zone:
NOSE_TOP = 0.006                 # the inner rims, the pads and the bridge below the nasion, which the nose may be pressed into
OUTER_X = 0.034                  # a frame point further out than this from the middle is in the outer zone (the corners, the hinges): a face recedes there, so what is
                                 # in front of it is hair, and the zone is held like the upper rim
MAX_REACH = 0.020                # the farthest the nose, hair, a brow or a hat may hold the lenses from the eyes: a wider nose than the bridge is made for is pressed into
MAX_ROLL = 15.0                  # degrees: a bigger tilt of the line between the eyes is a mistake in finding them
MAX_YAW = 30.0                   # ... and a bigger turn (three of the twelve first characters were turned 14 to 20 degrees in their own front view)
DEADBAND = 1.0                   # degrees: a smaller one is the picture's noise
SIZE_RANGE = (0.3, 4.0)
NUDGE_LIMITS = {"up": (-40.0, 40.0), "forward": (-40.0, 40.0), "tilt": (-30.0, 30.0), "size": (0.5, 2.0)}
NUDGE_DEFAULT = {"up": 0.0, "forward": 0.0, "tilt": 0.0, "size": 1.0}


def clean_nudge(raw):
    """A nudge from a request: {"up": mm, "forward": mm, "tilt": degrees (top forward), "size": a multiplier}, missing parts neutral. Raises ValueError."""
    raw = raw or {}
    if not isinstance(raw, dict):
        raise ValueError("The nudge must be an object")
    unknown = sorted(set(raw) - set(NUDGE_DEFAULT))
    if unknown:
        raise ValueError(f"Unknown nudge: {', '.join(unknown)}")
    out = {}
    for key, default in NUDGE_DEFAULT.items():
        value = raw.get(key, default)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value:
            raise ValueError(f"The nudge {key} must be a number")
        lo, hi = NUDGE_LIMITS[key]
        if not lo <= value <= hi:
            raise ValueError(f"The nudge {key} must be between {lo:g} and {hi:g}")
        out[key] = float(value)
    return out


class Scan:
    """A face scan from face_scan.py, with its surface grid decoded."""

    def __init__(self, data):
        if not isinstance(data, dict) or data.get("version") != 1:
            raise ValueError("Not a face scan")
        self.data = data
        grid = data["grid"]
        self.x0, self.y0, self.step = float(grid["x0"]), float(grid["y0"]), float(grid["step"])
        self.nx, self.ny = int(grid["nx"]), int(grid["ny"])
        self.z = np.frombuffer(base64.b64decode(grid["z"]), "<f4").reshape(self.ny, self.nx).astype(np.float64)
        self.source = data.get("source", "estimate")
        self.left = np.asarray(data["eyes"]["left"], float)
        self.right = np.asarray(data["eyes"]["right"], float)
        self.face_width = data.get("face_width")
        self.height = (data.get("model") or {}).get("height")

    def surface(self, x, y):
        """The depth of the face's front surface at (x, y) (arrays or numbers): bilinear inside the grid, NaN where the grid has no skin."""
        x, y = np.atleast_1d(np.asarray(x, float)), np.atleast_1d(np.asarray(y, float))
        fx, fy = (x - self.x0) / self.step - 0.5, (y - self.y0) / self.step - 0.5
        inside = (fx >= 0) & (fy >= 0) & (fx <= self.nx - 1) & (fy <= self.ny - 1)
        ix, iy = np.clip(np.floor(fx).astype(int), 0, self.nx - 2), np.clip(np.floor(fy).astype(int), 0, self.ny - 2)
        tx, ty = np.clip(fx - ix, 0, 1), np.clip(fy - iy, 0, 1)
        z00, z10, z01, z11 = self.z[iy, ix], self.z[iy, ix + 1], self.z[iy + 1, ix], self.z[iy + 1, ix + 1]
        out = z00 * (1 - tx) * (1 - ty) + z10 * tx * (1 - ty) + z01 * (1 - tx) * ty + z11 * tx * ty        # NaN if any corner has none
        nearest = self.z[np.clip(np.rint(fy).astype(int), 0, self.ny - 1), np.clip(np.rint(fx).astype(int), 0, self.nx - 1)]
        out = np.where(np.isfinite(out), out, nearest)
        return np.where(inside, out, np.nan)


def glasses_points(glb):
    """The pair's vertices by part, in its own space (metres, origin at the bridge): {"front": the frame and the lenses, "lens", "arms"}."""
    g = Glb(glb)
    doc, world = g.json, glbmerge.world_matrices(g.json)
    parts = {"frame": [], "lens": [], "arms": []}
    for i, node in enumerate(doc["nodes"]):
        if "mesh" not in node or i not in world:
            continue
        name = node.get("name") or ""
        key = "lens" if name == "lenses" else "arms" if name.startswith("temple") else "frame"
        m = world[i]
        for prim in doc["meshes"][node["mesh"]]["primitives"]:
            pos = g.accessor(prim["attributes"]["POSITION"]).astype(float)
            parts[key].append(pm.transform(pos, m[:3, :3]) + m[:3, 3])
    out = {k: (np.concatenate(v) if v else np.zeros((0, 3))) for k, v in parts.items()}
    out["front"] = np.concatenate([out["frame"], out["lens"]])
    return out


def rotation(roll, yaw, tilt=0.0):
    """The matrix Ry(yaw) . Rz(roll) . Rx(tilt), radians. The pair's x axis keeps the eye line's direction whatever the tilt."""
    cr, sr, cy, sy, ct, st = np.cos(roll), np.sin(roll), np.cos(yaw), np.sin(yaw), np.cos(tilt), np.sin(tilt)
    ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    rz = np.array([[cr, -sr, 0], [sr, cr, 0], [0, 0, 1]])
    rx = np.array([[1, 0, 0], [0, ct, -st], [0, st, ct]])
    return pm.matmul(pm.matmul(ry, rz), rx)


def _turn(scan):
    """(roll, yaw) in radians from the line between the eyes: the wearer's left eye is at +x."""
    v = scan.left - scan.right
    roll = np.arcsin(np.clip(v[1] / np.linalg.norm(v), -1, 1))
    yaw = -np.arctan2(v[2], v[0])
    out = []
    for angle, limit in ((roll, MAX_ROLL), (yaw, MAX_YAW)):
        degrees = np.degrees(angle)
        out.append(0.0 if abs(degrees) < DEADBAND else float(np.radians(np.clip(degrees, -limit, limit))))
    return out[0], out[1]


def character_scale(scan, yaw=0.0):
    """(scale, from eyes, from face width or None): how much bigger the character's face is than an adult's."""
    ipd = float(np.linalg.norm(scan.left - scan.right))
    from_eyes = ipd / REF_IPD
    from_face = None
    if scan.face_width:
        from_face = float(scan.face_width) / np.cos(yaw) / REF_FACE
    ratios = [r for r in (from_eyes, from_face) if r is not None]
    scale = float(np.exp(np.mean(np.log(ratios))))
    return float(np.clip(scale, *SIZE_RANGE)), from_eyes, from_face


def _eye_limits(scan, lens, rot, s, p_xy):
    """(z_eyes, z_reach): the depth of the pair's origin at which the lenses are EYE_ROOM in front of the eyes (the least) and MAX_REACH in front of them
    (the most the nose is allowed to hold them), at the pair's size. None when no lens is near an eye."""
    least, most = [], []
    for eye in (scan.left, scan.right):
        local = pm.matmul(rot.T, np.append(eye[:2] - p_xy, 0.0)) / s            # where the eye is across and up, in the pair's own space
        near = lens[np.hypot(lens[:, 0] - local[0], lens[:, 1] - local[1]) < 0.006]
        if len(near) == 0:
            continue
        back = s * pm.matmul(rot, np.array([local[0], local[1], near[:, 2].min()]))[2]
        least.append(eye[2] + EYE_ROOM * s - back)
        most.append(eye[2] + MAX_REACH * s - back)
    if not least:
        return None, None
    return max(least), max(float(np.mean(most)), max(least))


def fit(scan, glasses_glb, nudge=None, parts=None):
    """Place a pair on a face. `scan` is a Scan, `glasses_glb` the pair's GLB bytes (or `parts`, glasses_points() of it).
    Returns {"position": [x, y, z], "rotation": [x, y, z, w], "scale", "matrix": 4x4 rows, "report": {...}}: the pair's origin in the
    model's space, ready for glbmerge. Raises ValueError for a bad nudge. The report's millimetres are the pair's own (a real adult's), so they mean
    the same on a model that is not in metres."""
    nudge = clean_nudge(nudge)
    parts = parts or glasses_points(glasses_glb)
    front, lens = parts["front"], parts["lens"]
    roll, yaw = _turn(scan)
    auto, from_eyes, from_face = character_scale(scan, yaw)
    s = float(np.clip(auto * nudge["size"], *SIZE_RANGE))
    unit = (scan.height or 1.7) / 1.7                                  # how big the model is next to a 1.7 m adult
    rot = rotation(roll, yaw, np.radians(nudge["tilt"]))
    mid = (scan.left + scan.right) / 2
    p_xy = mid[:2].copy()
    notes = []

    # the depth: forward until nothing is inside the skin ...
    offsets = s * pm.transform(front, rot)                       # each vertex's offset from the pair's origin in the model's space
    skin = scan.surface(p_xy[0] + offsets[:, 0], p_xy[1] + offsets[:, 1])
    have = np.isfinite(skin)
    need = skin + ROOM * s - offsets[:, 2]
    soft = have & (np.abs(front[:, 0]) < NOSE_X) & (front[:, 1] <= NOSE_TOP)         # the nose's flanks, where the inner rims and the bridge meet it
    upper = front[:, 1] > UPPER
    z_eyes, z_reach = _eye_limits(scan, lens, rot, s, p_xy)
    z_nose, pressed = None, 0.0                                        # ... except that the nose gives way rather than hold the lenses too far out
    if soft.any():
        z_nose = float(need[soft].max())
        if z_reach is not None and z_nose > z_reach + 1e-9:
            pressed, z_nose = z_nose - z_reach, z_reach
    outer = np.abs(front[:, 0]) >= OUTER_X
    low = have & ~soft & ~upper & ~outer
    lows = ([float(need[low].max())] if low.any() else []) + ([z_nose] if z_nose is not None else [])
    z_low = max(lows) if lows else None
    top = have & ~soft & (upper | outer)                                # (the upper rim and the outer corners: what is in front of them is hair, a brow or a hat)
    z_top = float(need[top].max()) if top.any() else None
    capped = False
    if z_top is None:
        z_front = z_low
    else:                                  # (hair, a brow or a hat holds the pair out by little more than the face does, and never past MAX_REACH from the eyes)
        allowed = z_top if z_reach is None else min(z_top, z_reach)
        if z_low is not None:
            allowed = min(allowed, z_low + HAIR_CAP * s)
        z_front = allowed if z_low is None else max(z_low, allowed)
        capped = z_top > z_front + 1e-9
    if z_front is None:
        notes.append("The scan has no skin where the frame goes, so the depth comes from the eyes alone")
    # ... and the lenses stay off the eyes
    candidates = [z for z in (z_front, z_eyes) if z is not None]
    z_o = max(candidates) if candidates else float(mid[2])
    if z_front is not None and z_eyes is not None and z_eyes > z_front + 1e-9:
        notes.append("The nose is flat or low: the pair rests further forward than the skin needs, to keep the lenses off the eyes")
    if capped:
        notes.append("The upper rim or the outer corners meet hair or a brow (or a hat) in front of the face; the pair is not pushed out past it")
    position = np.array([p_xy[0], p_xy[1], z_o])
    position = position + pm.matmul(rot, np.array([0.0, nudge["up"] / 1000.0, nudge["forward"] / 1000.0]))

    # how it came out
    final = position + offsets
    skin = scan.surface(final[:, 0], final[:, 1])
    have = np.isfinite(skin)
    soft = have & (np.abs(front[:, 0]) < NOSE_X) & (front[:, 1] <= NOSE_TOP)
    gap = final[:, 2] - skin
    mm = 1000.0 / s                                                    # metres in the model -> millimetres at the pair's own size
    near_nose = have & (np.abs(front[:, 0]) < 0.012) & (front[:, 1] > -0.02) & (front[:, 1] < 0.01)
    distances = []
    for eye in (scan.left, scan.right):
        local = pm.matmul(rot.T, eye - position) / s
        near = lens[np.hypot(lens[:, 0] - local[0], lens[:, 1] - local[1]) < 0.006]
        if len(near):
            distances.append(float(eye[2] - (position + s * pm.matmul(rot, np.array([0, 0, near[:, 2].min()])))[2]) * -mm)
    face = have & ~soft & (front[:, 1] <= UPPER) & (np.abs(front[:, 0]) < OUTER_X)       # what is inside the face, what is in hair, a brow or a hat, what is in the nose
    hair = have & ~soft & ((front[:, 1] > UPPER) | (np.abs(front[:, 0]) >= OUTER_X))
    hard_in = float(max(0.0, -gap[face].min())) * mm if face.any() else 0.0
    hair_in = float(max(0.0, -gap[hair].min())) * mm if hair.any() else 0.0
    nose_in = float(max(0.0, -gap[soft].min())) * mm if soft.any() else 0.0
    report = {"source": scan.source, "scale": round(s, 4), "scale_auto": round(auto, 4),
              "scale_from_eyes": round(from_eyes, 4), "scale_from_face": None if from_face is None else round(from_face, 4),
              "ipd_mm": round(float(np.linalg.norm(scan.left - scan.right)) / unit * 1000.0, 1),                 # (these two as if the character were 1.7 m tall)
              "face_width_mm": None if not scan.face_width else round(float(scan.face_width) / unit * 1000.0, 1),
              "roll_deg": round(float(np.degrees(roll)), 1), "yaw_deg": round(float(np.degrees(yaw)), 1),
              "vertex_distance_mm": round(float(np.mean(distances)), 1) if distances else None,
              "nose_gap_mm": round(float(gap[near_nose].min()) * mm, 1) if near_nose.any() else None,
              "behind_mm": round(hard_in, 1), "nose_into_mm": round(nose_in, 1), "hair_into_mm": round(hair_in, 1),
              "capped": bool(capped), "nudge": nudge, "notes": notes}
    if scan.source == "estimate":
        report["notes"] = ["The eyes are placed by the usual proportions, not found: check the fit and nudge it into place"] + notes
    if pressed * mm > 4.0:                                             # (a few millimetres of press into the nose cannot be seen: no note)
        report["notes"].append(f"The nose is wide for this pair's bridge: to keep the lenses within {MAX_REACH * 1000:.0f} mm of the eyes its inner rims are pressed "
                               f"about {pressed * mm:.0f} mm into the nose. A wider bridge in the design (a few millimetres is often enough) would press less")
    if hard_in > 2.0:
        report["notes"].append(f"Part of the frame is {hard_in:.0f} mm inside the face: move it forward or make it smaller")
    matrix = np.eye(4)
    matrix[:3, :3] = rot * s
    matrix[:3, 3] = position
    return {"position": [round(float(v), 6) for v in position], "rotation": [round(float(v), 6) for v in glbmerge.matrix_quaternion(rot)],
            "scale": round(s, 6), "matrix": [[round(float(v), 7) for v in row] for row in matrix], "report": report,
            "focus": [round(float(v), 5) for v in mid], "height_m": scan.height}


def world_matrix(placement):
    """The 4x4 matrix of a fit() result."""
    return np.array(placement["matrix"], float)
