"""Pose puppets: a stick-figure body drawn in the character's own colours, used to steer a walk cycle.

Why: asked in words for "the legs close together as one foot passes the other", Mage-Flow-Edit draws the same wide stride
every time (measured, see docs/animation.md), so a walk made from text never shows the mid-step poses and reads as a
character sliding. Given a drawing of the pose as the FIRST image and the character's sprite as the second, it repaints the
drawing as that character in that pose. The drawing has to be in the character's colours: a grey mannequin got painted onto
the character, and a mannequin as the second image was mostly ignored.

The body is a small 3D skeleton (hips, knees, ankles, shoulders, elbows) seen from the side, the front or the back, so one
set of poses serves all four directions of a sprite sheet. Nothing here talks to ComfyUI.
"""
import math

import numpy as np
from PIL import Image, ImageDraw

# proportions as fractions of the standing height
BODY = dict(head_r=0.062, neck=0.14, torso=0.30, thigh=0.245, shin=0.215, ankle=0.04, upper_arm=0.17, fore_arm=0.15,
            hand=0.05, toe=0.09, heel=0.03, hip_x=0.05, shoulder_x=0.115)

VIEW_OF = {"right": "side", "left": "side", "down": "front", "up": "back"}
# degrees the camera looks down from. A steeper camera makes a step toward or away from it show as a foot lower or higher on the page.
VIEW_PITCH = {"side": 0, "front": 24, "back": 24}

# A pose: (thigh swing, knee bend, foot pitch) per leg and (shoulder swing, elbow bend) per arm, in degrees. A thigh swings
# forward when positive, a knee bends the shin backward, a foot points its toe down when positive. "right" is the limb
# nearest the camera in a side view facing right.
_A = {
    "contact": dict(right=(27, 3, -18), left=(-18, 10, 32), arm_right=(-28, 15), arm_left=(28, 25)),     # heel about to land
    "down":    dict(right=(13, 14, -3), left=(-21, 34, 50), arm_right=(-18, 15), arm_left=(18, 25)),     # weight taken, body lowest
    "passing": dict(right=(2, 6, 0),    left=(22, 72, 25),  arm_right=(-4, 15),  arm_left=(6, 20)),      # swing leg passes the standing leg
    "up":      dict(right=(-14, 8, 15), left=(30, 28, -10), arm_right=(12, 20),  arm_left=(-12, 15)),    # rising, reaching forward
}
POSES = {}
for _name, _p in _A.items():
    POSES[f"{_name}_a"] = _p
    POSES[f"{_name}_b"] = dict(right=_p["left"], left=_p["right"], arm_right=_p["arm_left"], arm_left=_p["arm_right"])   # the other step

WALK_CYCLES = {
    4: ["contact_a", "passing_a", "contact_b", "passing_b"],
    8: ["contact_a", "down_a", "passing_a", "up_a", "contact_b", "down_b", "passing_b", "up_b"],
}

# Seen from the front or the back, the poses above hardly differ, and the model then copies its sprite and draws the same standing
# figure four times (measured). These are exaggerated for those views: a long step toward (or away from) the camera, and a raised
# knee with the foot off the ground.
_FB = {
    "step": dict(right=(30, 4, -10), left=(-24, 30, 55), arm_right=(-25, 12), arm_left=(25, 20)),
    "lift": dict(right=(0, 3, 0), left=(48, 85, 30), arm_right=(-6, 12), arm_left=(8, 18)),
}
FRONT_POSES = {}
for _name, _p in _FB.items():
    FRONT_POSES[f"{_name}_a"] = _p
    FRONT_POSES[f"{_name}_b"] = dict(right=_p["left"], left=_p["right"], arm_right=_p["arm_left"], arm_left=_p["arm_right"])


def blend(a, b, t=0.5):
    """A pose part-way from pose `a` to pose `b`."""
    return {k: tuple(x + (y - x) * t for x, y in zip(a[k], b[k])) for k in a}


FRONT_POSES.update(step_lift_a=blend(FRONT_POSES["step_a"], FRONT_POSES["lift_a"]), lift_step_b=blend(FRONT_POSES["lift_a"], FRONT_POSES["step_b"]),
                   step_lift_b=blend(FRONT_POSES["step_b"], FRONT_POSES["lift_b"]), lift_step_a=blend(FRONT_POSES["lift_b"], FRONT_POSES["step_a"]))
FRONT_CYCLES = {
    4: ["step_a", "lift_a", "step_b", "lift_b"],
    8: ["step_a", "step_lift_a", "lift_a", "lift_step_b", "step_b", "step_lift_b", "lift_b", "lift_step_a"],
}


def cycle(view, count):
    """The poses of a walk with `count` frames as seen from `view`: (name, pose) pairs."""
    table, names = (POSES, WALK_CYCLES) if view == "side" else (FRONT_POSES, FRONT_CYCLES)
    return [(n, table[n]) for n in names[count]]


# The kinds of pose that decide whether a seed suits a whole cycle: a seed that draws the stride well may draw the passing step as a
# stride again (measured), and the other way round.
PROBES = {"side": ("contact", "passing"), "front": ("step", "lift"), "back": ("step", "lift")}


def probe_indices(view, count):
    """The frames to try a seed on: the first frame of each kind of pose in PROBES."""
    names = [n for n, _ in cycle(view, count)]
    found = []
    for kind in PROBES[view]:
        for i, n in enumerate(names):
            if n.rsplit("_", 1)[0] == kind:
                found.append(i)
                break
    return found


# how far the body rides above or below its standing height at each key pose, as a fraction of the figure's height: it dips as
# a foot lands and is highest as the legs pass and the body rises over the standing leg
STEP_BOB = {"contact": -0.008, "down": -0.014, "passing": 0.0, "up": 0.010,
            "step": -0.008, "step_lift": -0.004, "lift": 0.0, "lift_step": -0.004}


def step_bob(count, view="side"):
    """One bob value per frame of a walk cycle with `count` frames."""
    return [STEP_BOB[name.rsplit("_", 1)[0]] for name, _ in cycle(view, count)]


# ---- the skeleton (x: the character's left, y: up, z: forward) -----------------------------------------------------------
def _leg(hip, theta, kappa, psi, F):
    t, k, p = math.radians(theta), math.radians(kappa), math.radians(psi)
    knee = (hip[0], hip[1] - BODY["thigh"] * F * math.cos(t), hip[2] + BODY["thigh"] * F * math.sin(t))
    s = t - k
    ankle = (hip[0], knee[1] - BODY["shin"] * F * math.cos(s), knee[2] + BODY["shin"] * F * math.sin(s))

    def foot(forward, down):                       # a point of the foot, which is pitched by psi about the ankle
        return (ankle[0], ankle[1] - (forward * math.sin(p) + down * math.cos(p)) * F,
                ankle[2] + (forward * math.cos(p) - down * math.sin(p)) * F)
    return dict(knee=knee, ankle=ankle, heel=foot(-BODY["heel"], BODY["ankle"]), toe=foot(BODY["toe"], BODY["ankle"]))


def _arm(shoulder, alpha, eps, F):
    a, e = math.radians(alpha), math.radians(eps)
    elbow = (shoulder[0], shoulder[1] - BODY["upper_arm"] * F * math.cos(a), shoulder[2] + BODY["upper_arm"] * F * math.sin(a))
    s = a + e                                      # elbows bend forward
    wrist = (shoulder[0], elbow[1] - BODY["fore_arm"] * F * math.cos(s), elbow[2] + BODY["fore_arm"] * F * math.sin(s))
    hand = (shoulder[0], wrist[1] - BODY["hand"] * F * math.cos(s), wrist[2] + BODY["hand"] * F * math.sin(s))
    return dict(elbow=elbow, wrist=wrist, hand=hand)


def skeleton(pose, lean=3.0):
    """3D joint positions for a pose, in units of the standing height, with the lowest foot on the floor (y = 0)."""
    F = 1.0
    legs = {s: _leg((sign * BODY["hip_x"], 0.0, 0.0), *pose[s], F) for s, sign in (("right", -1), ("left", 1))}
    drop = -min(min(leg["heel"][1], leg["toe"][1]) for leg in legs.values())  # how far the hips are above the lowest foot point
    out = {"hip": (0.0, drop, 0.0)}
    for s, sign in (("right", -1), ("left", 1)):
        out[s] = _leg((sign * BODY["hip_x"], drop, 0.0), *pose[s], F)
    ln = math.radians(lean)
    shoulder = (0.0, drop + BODY["torso"] * math.cos(ln), BODY["torso"] * math.sin(ln))
    out["shoulder"] = shoulder
    out["head"] = (0.0, shoulder[1] + BODY["neck"] * math.cos(ln), shoulder[2] + BODY["neck"] * math.sin(ln))
    for s, sign in (("right", -1), ("left", 1)):
        sh = (sign * BODY["shoulder_x"], shoulder[1], shoulder[2])
        out["arm_" + s] = dict(shoulder=sh, **_arm(sh, *pose["arm_" + s], 1.0))
    return out


def figure_height(pose):
    """The pose's height from the soles to the top of the head, as a fraction of standing height (contact poses are lower)."""
    sk = skeleton(pose)
    return sk["head"][1] + BODY["head_r"]


# ---- drawing --------------------------------------------------------------------------------------------------------------
def _project(pt, view, pitch):
    """(x, y, z) -> (page x, page y downward, depth toward the camera), in units of the standing height."""
    x, y, z = pt
    p = math.radians(pitch)
    if view == "side":
        return z, -y, -x
    if view == "front":
        return x, -y * math.cos(p) + z * math.sin(p), z
    return -x, -y * math.cos(p) - z * math.sin(p), -z                          # back


def _shade(colour, k):
    return tuple(int(c * k) for c in colour)


def _cap(d, a, b, width, fill):
    d.line([a, b], fill=fill, width=max(1, int(width)))
    r = max(1.0, width / 2)
    for c in (a, b):
        d.ellipse([c[0] - r, c[1] - r, c[0] + r, c[1] + r], fill=fill)


def paint(pose, view, palette, size=(576, 864), height=0.84, floor=0.93, scale=2, background=(255, 255, 255), pitch=None):
    """The pose as a puppet in the character's colours (`palette`: shirt, trousers, shoes, skin and, if known, hair), seen from `view`.

    The head carries the hair colour because the model reads the puppet's colours as the character's: with a skin-coloured head, a
    black-haired character came out with auburn hair in half the frames of a cycle."""
    W, H = size
    F = height * H * scale
    sk = skeleton(pose)
    pitch = VIEW_PITCH[view] if pitch is None else pitch
    feet = [_project(sk[s][k], view, pitch)[1] for s in ("right", "left") for k in ("heel", "toe")]
    # seen from a raised camera the foot that steps toward it lands lower on the page (or, from behind, the trailing one):
    # keep the lowest foot on the floor line so that no pose is cut off at the bottom
    ox, oy = W * scale / 2, floor * H * scale - max(feet) * F

    def px(pt):
        u, v, depth = _project(pt, view, pitch)
        return (ox + u * F, oy + v * F), depth

    im = Image.new("RGB", (W * scale, H * scale), background)
    d = ImageDraw.Draw(im)
    side = view == "side"
    parts = []                                                                   # (depth, draw function)

    def limb(a, b, width, colour, dim):
        (pa, da), (pb, db) = px(a), px(b)
        parts.append(((da + db) / 2, lambda: _cap(d, pa, pb, width * F, _shade(colour, dim))))

    for s in ("right", "left"):
        dim = 1.0 if (s == "right" or not side) else 0.72                        # the far side of a side view is darker
        L, A = sk[s], sk["arm_" + s]
        hip = (L["knee"][0], sk["hip"][1], 0.0)
        limb(hip, L["knee"], 0.062, palette["trousers"], dim)
        limb(L["knee"], L["ankle"], 0.046, palette["trousers"], dim)
        limb(L["heel"], L["toe"], 0.038, palette["shoes"], dim)
        limb(A["shoulder"], A["elbow"], 0.042, palette["shirt"], dim)
        limb(A["elbow"], A["wrist"], 0.036, palette["shirt"], dim)
        limb(A["wrist"], A["hand"], 0.034, palette["skin"], dim)
    (top, _), (base, _) = px(sk["shoulder"]), px(sk["hip"])
    parts.append((0.0, lambda: _cap(d, base, top, (0.11 if side else 0.2) * F, palette["shirt"])))
    (neck, _), (head, _) = px(sk["shoulder"]), px(sk["head"])
    parts.append((0.0, lambda: _cap(d, neck, head, 0.035 * F, palette["skin"])))
    r = BODY["head_r"] * F

    def disc(centre, radius, colour):
        return lambda: d.ellipse([centre[0] - radius, centre[1] - radius, centre[0] + radius, centre[1] + radius], fill=colour)
    hair = palette.get("hair")
    if hair is None:                                                             # bald, or the colour is not known
        parts.append((0.001, disc(head, r, palette["skin"])))
    else:                                                                        # hair all round, a face where the face would be
        parts.append((0.001, disc(head, r * 1.06, hair)))
        if view == "side":
            parts.append((0.002, disc((head[0] + 0.32 * r, head[1] + 0.08 * r), r * 0.8, palette["skin"])))
        elif view == "front":
            parts.append((0.002, disc((head[0], head[1] + 0.12 * r), r * 0.82, palette["skin"])))
    for _, draw in sorted(parts, key=lambda t: t[0]):                            # far parts first
        draw()
    return im.resize((W, H), Image.LANCZOS)


# hair colour words -> RGB. The first word found in the description wins, so the specific ones come first. None = no hair to draw.
HAIR_COLOURS = [
    ("bald", None), ("shaved", None),
    ("platinum", (205, 205, 210)), ("silver", (190, 190, 195)), ("white", (225, 225, 225)), ("gray", (160, 160, 160)),
    ("grey", (160, 160, 160)), ("salt", (140, 140, 140)),
    ("strawberry", (200, 130, 90)), ("dirty blond", (170, 140, 90)), ("blond", (215, 185, 110)),
    ("auburn", (120, 55, 35)), ("vivid red", (190, 30, 40)), ("ginger", (185, 90, 40)), ("red", (150, 45, 30)),
    ("pink", (235, 150, 190)), ("lavender", (185, 165, 220)), ("mint", (150, 220, 190)), ("teal", (40, 150, 150)),
    ("blue", (40, 100, 220)), ("purple", (120, 60, 160)), ("green", (60, 160, 80)),
    ("black", (25, 22, 22)), ("dark brown", (60, 40, 30)), ("light brown", (140, 100, 65)), ("brown", (95, 62, 40)),
]


def hair_colour(text):
    """The RGB of the hair a description names ('black long hair', 'pastel pink bob'), or None when it names none (bald, or unknown)."""
    text = (text or "").lower()
    for word, rgb in HAIR_COLOURS:
        if word in text:
            return rgb
    return None


# ---- colours of a character -----------------------------------------------------------------------------------------------
def sample_palette(cut):
    """Rough colours of a standing cut-out (RGBA): shirt, trousers, shoes and skin, each the median of a band of the figure."""
    a = np.asarray(cut.convert("RGBA"))
    ys, xs = np.where(a[..., 3] > 200)
    if not len(ys):
        return dict(shirt=(110, 110, 110), trousers=(70, 70, 70), shoes=(60, 50, 40), skin=(200, 160, 130))
    top, h = ys.min(), ys.max() + 1 - ys.min()

    def median(lo, hi, keep=None):
        band = a[top + int(h * lo): top + int(h * hi)]
        px = band[band[..., 3] > 200][:, :3].astype(float)
        if keep is not None and len(px):
            k = keep(px)
            px = px[k] if k.sum() > 20 else px
        return tuple(int(v) for v in np.median(px, axis=0)) if len(px) else (128, 128, 128)

    def skin_like(px):                             # warm hue, moderate saturation: skips khaki or olive hats, green and grey clothes
        hsv = np.asarray(Image.fromarray(px.astype(np.uint8).reshape(-1, 1, 3), "RGB").convert("HSV")).reshape(-1, 3).astype(float)
        return ((hsv[:, 0] <= 32) | (hsv[:, 0] >= 245)) & (hsv[:, 1] >= 40) & (hsv[:, 1] <= 190) & (hsv[:, 2] >= 70)
    return dict(shirt=median(0.22, 0.42), trousers=median(0.60, 0.85), shoes=median(0.955, 1.0), skin=median(0.02, 0.16, skin_like))
