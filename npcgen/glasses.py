"""Glasses built from a recipe: measurements an optician would give, not a picture.

Image-to-3D (the Items tab's other makers) cannot make a frame a millimetre and a half thick, and has no surface to find on a clear
lens. So glasses are built here: a lens outline, a rim swept round it, a bridge, end pieces and arms swept along paths, nose pads,
lenses with a base curve, and the whole pair bent round the face (npcgen/procmesh.py is the kernel). The result is a small GLB (a
few thousand triangles, four flat-colour PBR materials, no textures) in real metres, facing +Z with its origin at the middle of the
bridge, built as separate nodes (`frame`, `lenses`, `temple_left`, `temple_right`) so a game can fold the arms or change the lens
material at run time.

The numbers are the ones eyewear is sold by (the boxing system): `lens_w` and `lens_h` are the box a lens fits in, `bridge` the gap
between the two boxes, `temple_len` the arm from hinge to tip. data/glasses.json holds the shapes' names, the presets, the swatches
and the ranges, and is read fresh on every request.
"""
import io
import json
import math
import re

import numpy as np
from PIL import Image

from . import config as cfgmod, procmesh as pm

DATA_FILE = cfgmod.ROOT / "data" / "glasses.json"
# How finely the curves are built, by the game version's size the item asks for (the Items tab's "Light/Standard/Detailed").
DETAIL = {"light": {"n": 56, "corner": 2, "arm": 22, "rings": 3, "around": 8},
          "standard": {"n": 96, "corner": 3, "arm": 38, "rings": 4, "around": 12},
          "fine": {"n": 160, "corner": 5, "arm": 64, "rings": 6, "around": 18}}
FACES_DETAIL = ((10_000, "light"), (30_000, "standard"))          # a size up to these; bigger is "fine"
LENS_THICKNESS = 2.0                                               # mm (a little less in a thin wire rim)
LIP = 0.4                                                          # mm: how far a rim reaches over the edge of its lens
EYE_LEVEL_NOTE = "origin at the middle of the bridge, on the line through the middle of the lenses"
BOOLEANS = ("pads", "lens_gradient")
CHOICES = ("shape", "rim", "bridge_style", "frame_material", "lens_finish", "pose")
COLOURS = ("frame_color", "lens_color")
HEX = re.compile(r"^#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{3})$")


def load():
    """The glasses tables, read fresh so edits to data/glasses.json apply without restarting the server."""
    return json.loads(DATA_FILE.read_text("utf-8"))


def catalogue():
    """What the editor needs, for /api/config: the number fields with their ranges, the lists, the swatches and the presets."""
    data = load()
    looks = {k: v for k, v in data["material_looks"].items() if not k.startswith("_")}
    return {"defaults": data["defaults"], "fields": data["fields"], "choices": data["choices"], "colours": data["colours"],
            "tints": data["tints"], "mirrors": data["mirrors"], "material_looks": looks,
            "presets": [{"id": pid, "label": p["label"], "recipe": p["recipe"]} for pid, p in data["presets"].items()]}


def detail_for(faces):
    for limit, name in FACES_DETAIL:
        if faces <= limit:
            return name
    return "fine"


# ---- the recipe ---------------------------------------------------------------------------------------------------------------------------------
def _colour(value, label, data):
    """'#rrggbb' from '#rgb', '#rrggbb' or the name of a swatch (frame or lens), lower case."""
    if isinstance(value, str):
        text = value.strip()
        if HEX.match(text):
            if len(text) == 4:
                text = "#" + "".join(c * 2 for c in text[1:])
            return text.lower()
        names = {c["id"]: c["hex"] for group in data["colours"].values() for c in group}
        names.update({k: t["hex"] for k, t in data["tints"].items() if k != "custom"})
        names.update({f"mirror_{m['id']}": m["hex"] for m in data["mirrors"]})
        if text.lower() in names:
            return names[text.lower()]
    raise ValueError(f"{label} must be a colour like #c8a050 or the name of a swatch")


def clean_recipe(raw, preset=None, base=None):
    """The complete recipe from what a caller sent (any part of it): the defaults, then a preset (`raw["preset"]`, else `preset`), then
    `base` (what the kind of glasses starts with, e.g. dark lenses for sunglasses), then the fields given. Raises ValueError saying which
    field is wrong. A lens tint given without its colour or darkness brings its own."""
    data = load()
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError("The glasses settings must be an object")
    unknown = sorted(set(raw) - set(data["defaults"]))
    if unknown:
        raise ValueError("Unknown glasses setting: " + ", ".join(unknown))
    named = raw.get("preset") or preset or data["defaults"]["preset"]
    if named not in data["presets"]:
        if raw.get("preset") and raw["preset"] != "custom":
            raise ValueError("Unknown glasses style: " + str(raw["preset"]))
        named = preset if preset in data["presets"] else data["defaults"]["preset"]
    out = {**data["defaults"], **data["presets"][named]["recipe"], **(base or {}), "preset": raw.get("preset") or named}
    for key, value in raw.items():
        if key == "preset" or value is None or value == "":
            continue
        out[key] = value
    for key, field in data["fields"].items():
        value = out[key]
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            raise ValueError(f"{field['label']} must be a number")
        try:
            number = float(value)
        except ValueError:
            raise ValueError(f"{field['label']} must be a number") from None
        if not field["min"] <= number <= field["max"]:
            raise ValueError(f"{field['label']} must be between {field['min']:g} and {field['max']:g}")
        out[key] = round(number, 4)
    for key in CHOICES:
        if out[key] not in data["choices"][key]:
            raise ValueError(f"Unknown {key.replace('_', ' ')}: {out[key]} (one of {', '.join(data['choices'][key])})")
    if out["lens_tint"] not in data["tints"]:
        raise ValueError(f"Unknown lens tint: {out['lens_tint']} (one of {', '.join(data['tints'])})")
    for key in BOOLEANS:
        if not isinstance(out[key], bool):
            raise ValueError(f"{key.replace('_', ' ')} must be true or false")
    tint = data["tints"][out["lens_tint"]]
    if "lens_color" not in raw and out["lens_tint"] != "custom":
        out["lens_color"] = tint["hex"]
    if "lens_opacity" not in raw and "lens_opacity" not in (base or {}):
        out["lens_opacity"] = tint["opacity"]
    for key in COLOURS:
        out[key] = _colour(out[key], key.replace("_", " ").capitalize(), data)
    return {key: out[key] for key in data["defaults"]}


def for_item(raw, kind, spec=None):
    """The recipe of an item of this kind (items.clean_item): the kind's style and starting changes (sunglasses have dark lenses) under what the caller sent."""
    spec = spec or {}
    return clean_recipe(raw, preset=spec.get("preset"), base=spec.get("recipe"))


def model_meta(info):
    """What an item's model record says about how it was made (the parts of `build`'s info that belong to the record)."""
    return {"engine": "Glasses maker", "engine_id": "glasses", "textured": False, "triangles": info["triangles"], "detail": info["detail"], "frame_width_mm": info["frame_width_mm"]}


# ---- reading a description ------------------------------------------------------------------------------------------------------------------------
def _clamp(data, key, value):
    field = data["fields"][key]
    return round(min(max(value, field["min"]), field["max"]), 4)


def from_description(text, recipe=None):
    """What a few words say about a pair of glasses: ({field: value}, ["what was understood", ...]) to lay over `recipe` (the current one;
    the plain default when None). It reads the vocabulary in data/glasses.json (a style or a shape, wire or plastic, colours, "green
    lenses", sunglasses, mirrored, bigger, thicker, folded...), so the same words always give the same answer and nothing is guessed:
    a word it does not know changes nothing. The changes are complete enough to apply as they are (a style brings all of its fields)."""
    data = load()
    words = data["words"]
    base = clean_recipe(recipe)
    t = " " + " ".join(re.sub(r"[^a-z0-9' ]", " ", str(text or "").lower().replace("-", " ")).split()) + " "

    def find(phrases, source=None):
        source = t if source is None else source
        hits = [p for p in phrases if re.search(rf"(?<![a-z']){re.escape(p)}(?![a-z'])", source)]
        return max(hits, key=len) if hits else None

    def best(groups, source=None):
        """The key whose phrase matches longest (so 'round wire' beats 'round')."""
        found = [(len(p), key) for key, phrases in groups.items() if (p := find(phrases, source))]
        return max(found)[1] if found else None

    out, said = {}, []
    current = dict(base)

    def put(**fields):
        for key, value in fields.items():
            out[key] = current[key] = value

    # the lens: a colour next to a lens word belongs to the lenses, and is taken out before the frame's colour is looked for
    lens_tint, rest = None, t
    lens = "|".join(re.escape(w) for w in words["lens_words"])
    for key, phrases in words["tints"].items():
        for phrase in sorted(phrases, key=len, reverse=True):
            p = re.escape(phrase)
            pattern = rf"(?<![a-z'])(?:{p}(?: tinted)?(?= (?:{lens})(?![a-z']))|(?:tinted|tint|lens|lenses)(?: (?:are|is|in|of|colou?red))? {p}(?![a-z']))"
            if lens_tint is None and re.search(pattern, rest):
                lens_tint = key
                rest = re.sub(pattern, " ", rest, count=1)
    if find(words["mirror_words"]) is not None:
        mirror = next((m["id"] for m in data["mirrors"] if re.search(rf"(?<![a-z']){m['id']} mirror", t)), "silver")
        put(lens_finish="mirror", lens_tint="custom", lens_color=next(m["hex"] for m in data["mirrors"] if m["id"] == mirror), lens_opacity=1.0)
        said.append(f"{mirror} mirrored lenses")
    elif lens_tint is not None or find(words["sunglass_words"]) is not None or re.search(r"(?<![a-z'])tinted(?![a-z'])", t):
        tint = lens_tint or "grey"
        put(lens_finish="glass", lens_tint=tint, lens_color=data["tints"][tint]["hex"], lens_opacity=data["tints"][tint]["opacity"])
        said.append(f"{data['tints'][tint]['label'].lower()} lenses")
    if find(words["gradient_words"]) is not None and current["lens_finish"] != "mirror":
        put(lens_gradient=True)
        said.append("graduated lenses")

    # the style, or the shape on its own; wire and plastic
    style = best(words["styles"])
    shape = best(words["shapes"])
    material = best({k: v for k, v in words["materials"].items()})
    colour = best(words["colours"], rest)
    if material is None and colour in words["metal_colours"]:
        material = "metal"
    if style is None and shape in ("round", "oval") and material == "metal":
        style, shape = {"round": "round_wire", "oval": "oval_wire"}[shape], None
    if style is not None:
        preset = data["presets"][style]
        put(**preset["recipe"], preset=style)
        said.append(f"the {preset['label'].lower()} style")
    elif shape is not None:
        put(shape=shape)
        said.append(f"{data['choices']['shape'][shape].split(' (')[0].lower()} lenses")
    rim = best(words["rims"])
    if rim is not None and not (style is not None and data["presets"][style]["recipe"]["rim"] == rim):
        put(rim=rim)
        said.append(data["choices"]["rim"][rim].split(" (")[0].lower())
    if material is not None and current["frame_material"] != material:
        put(frame_material=material, **data["material_looks"][material])
        said.append(f"{material if material != 'matte' else 'matte plastic'} frame")
    if colour is not None:
        hexes = {c["id"]: c["hex"] for group in data["colours"].values() for c in group}
        put(frame_color=hexes[colour])
        said.append(f"{colour.replace('_', ' ')} frame")

    # sizes, arms, pads, pose
    if find(words["bigger"]):
        put(lens_w=_clamp(data, "lens_w", current["lens_w"] + 6), lens_h=_clamp(data, "lens_h", current["lens_h"] + 5))
        said.append("bigger lenses")
    elif find(words["smaller"]):
        put(lens_w=_clamp(data, "lens_w", current["lens_w"] - 6), lens_h=_clamp(data, "lens_h", current["lens_h"] - 5))
        said.append("smaller lenses")
    if find(words["wider"]):
        put(lens_w=_clamp(data, "lens_w", current["lens_w"] + 4))
        said.append("wider lenses")
    if find(words["thicker"]) and style != "chunky":
        put(rim_w=_clamp(data, "rim_w", current["rim_w"] * 1.5), rim_d=_clamp(data, "rim_d", current["rim_d"] * 1.3),
            temple_h=_clamp(data, "temple_h", current["temple_h"] * 1.25))
        said.append("a thicker frame")
    elif find(words["thinner"]):
        put(rim_w=_clamp(data, "rim_w", current["rim_w"] * 0.7), rim_d=_clamp(data, "rim_d", current["rim_d"] * 0.8),
            temple_h=_clamp(data, "temple_h", current["temple_h"] * 0.8))
        said.append("a thinner frame")
    if find(words["longer_arms"]):
        put(temple_len=_clamp(data, "temple_len", current["temple_len"] + 10))
        said.append("longer arms")
    elif find(words["shorter_arms"]):
        put(temple_len=_clamp(data, "temple_len", current["temple_len"] - 10))
        said.append("shorter arms")
    if re.search(r"(?<![a-z'])(?:no|without) nose pads|padless", t):
        put(pads=False)
        said.append("no nose pads")
    elif re.search(r"(?<![a-z'])nose pads?(?![a-z'])", t):
        put(pads=True)
        said.append("nose pads")
    if re.search(r"(?<![a-z'])folded(?![a-z'])", t):
        put(pose="folded")
        said.append("folded")
    return out, said


# ---- lens outlines ------------------------------------------------------------------------------------------------------------------------------
def _smooth(x, a, b):
    t = np.clip((np.asarray(x, float) - a) / (b - a), 0.0, 1.0)
    return t * t * (3 - 2 * t)


def _ellipse_unit(power=2.0, count=720):
    """A superellipse in the box [-1, 1]²: power 2 is an ellipse, 4 a squarish rounded shape."""
    t = np.linspace(0, 2 * np.pi, count, endpoint=False)
    c, s = np.cos(t), np.sin(t)
    e = 2.0 / power
    return np.stack([np.sign(c) * np.abs(c) ** e, np.sign(s) * np.abs(s) ** e], 1)


def _rounded_polygon(vertices, radius, arc_points=16, min_turn=20.0):
    """The polygon with every corner turning by more than `min_turn` degrees rounded off by a circle of `radius` (one number, or one per
    vertex; smaller where the edges are short). A dense curve given as vertices keeps its shape, since its tiny turns are left alone."""
    v = np.asarray(vertices, float)
    radii = np.broadcast_to(np.asarray(radius, float), (len(v),))
    out = []
    for i in range(len(v)):
        radius = radii[i]
        p0, p1, p2 = v[i - 1], v[i], v[(i + 1) % len(v)]
        a, b = p0 - p1, p2 - p1
        la, lb = np.linalg.norm(a), np.linalg.norm(b)
        a, b = a / la, b / lb
        angle = math.acos(float(np.clip(np.sum(a * b), -1, 1)))       # the angle inside the corner
        if radius <= 0 or math.degrees(math.pi - angle) < min_turn:
            out.append(p1)
            continue
        d = min(radius / math.tan(angle / 2), 0.5 * la, 0.5 * lb)
        r = d * math.tan(angle / 2)
        bisector = (a + b) / np.linalg.norm(a + b)
        centre = p1 + bisector * (r / math.sin(angle / 2))
        a0 = math.atan2(*(p1 + a * d - centre)[::-1])
        a1 = math.atan2(*(p1 + b * d - centre)[::-1])
        sweep = (a1 - a0 + math.pi) % (2 * math.pi) - math.pi
        angles = a0 + sweep * np.linspace(0, 1, arc_points)
        out.extend(centre + r * np.stack([np.cos(angles), np.sin(angles)], 1))
    return np.array(out)


def _spline(points, per_segment=40):
    """A smooth closed curve through the control points (uniform Catmull-Rom)."""
    p = np.asarray(points, float)
    n = len(p)
    t = np.linspace(0, 1, per_segment, endpoint=False)[:, None]
    out = []
    for i in range(n):
        p0, p1, p2, p3 = p[i - 1], p[i], p[(i + 1) % n], p[(i + 2) % n]
        out.append(0.5 * ((2 * p1) + (-p0 + p2) * t + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t ** 2 + (-p0 + 3 * p1 - 3 * p2 + p3) * t ** 3))
    return np.concatenate(out)


def _fit_box(points, width, height):
    low, high = points.min(0), points.max(0)
    return (points - (low + high) / 2) * np.array([width / (high[0] - low[0]), height / (high[1] - low[1])])


def _resample(points, count):
    """`count` points spaced evenly along a closed outline, counter-clockwise."""
    closed = np.vstack([points, points[:1]])
    length = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(closed, axis=0), axis=1))])
    at = np.linspace(0, length[-1], count, endpoint=False)
    out = np.stack([np.interp(at, length, closed[:, 0]), np.interp(at, length, closed[:, 1])], 1)
    area = 0.5 * np.sum(out[:, 0] * np.roll(out[:, 1], -1) - np.roll(out[:, 0], -1) * out[:, 1])
    return out if area > 0 else out[::-1].copy()


def lens_outline(shape, width, height, count):
    """The outline of one lens, `width` × `height` mm, centred on (0, 0), counter-clockwise seen from the front, with its nose side at
    -x and its temple side at +x. (The other lens is this one mirrored.)"""
    w, h = width / 2, height / 2
    box = np.array([w, h])
    if shape in ("round", "oval"):
        pts = _ellipse_unit(2.0) * box
    elif shape in ("rectangle", "square"):
        radius = (0.24 if shape == "rectangle" else 0.22) * height
        pts = _rounded_polygon([(-w, -h), (w, -h), (w, h), (-w, h)], radius)
    elif shape == "classic":                       # a squared trapezoid: the temple corner high and sharp, the nose corner low and round
        corners = np.array([(-0.70 * w, -h), (0.86 * w, -0.86 * h), (w, h), (-w, 0.76 * h)])
        pts = _rounded_polygon(corners, np.array([0.36, 0.30, 0.13, 0.30]) * height)
    elif shape == "aviator":                       # a teardrop: widest near the top, drawn in toward a rounded point low and near the nose
        ctrl = [(-0.74, 0.96), (-0.05, 1.0), (0.62, 0.9), (0.97, 0.52), (1.0, 0.0), (0.84, -0.46), (0.46, -0.84), (0.02, -1.0),
                (-0.42, -0.9), (-0.8, -0.58), (-0.98, -0.1), (-1.0, 0.5)]
        pts = _spline(np.array(ctrl) * box)
    elif shape == "cat_eye":                       # the temple corner swept up into a point, the nose side low and round
        ctrl = [(-0.92, 0.42), (-0.62, 0.74), (-0.16, 0.86), (0.4, 0.96), (0.94, 1.0), (1.0, 0.72), (0.9, 0.2), (0.62, -0.42),
                (0.16, -0.9), (-0.38, -1.0), (-0.82, -0.78), (-1.0, -0.2)]
        pts = _spline(np.array(ctrl) * box)
    elif shape == "hexagon":
        pts = _rounded_polygon([(w, 0), (w / 2, h), (-w / 2, h), (-w, 0), (-w / 2, -h), (w / 2, -h)], 0.14 * height)
    elif shape == "octagon":
        angle = np.radians(22.5 + 45 * np.arange(8))
        pts = _rounded_polygon(np.stack([np.cos(angle) * w / math.cos(math.radians(22.5)),
                                         np.sin(angle) * h / math.cos(math.radians(22.5))], 1), 0.1 * height)
    elif shape == "half_moon":                     # a flat top and a deep rounded bottom (reading glasses)
        ell = 3.5                                  # mm: how far the top corners are rounded off along each side
        t0 = ell / height
        t = np.linspace(-t0, -np.pi + t0, 120)
        arc = np.stack([w * np.cos(t), h + height * np.sin(t)], 1)
        right = _bezier([(w - ell, h), (w, h), arc[0]], 12)
        left = _bezier([arc[-1], (-w, h), (-w + ell, h)], 12)
        pts = np.vstack([right[:-1], arc, left[1:]])        # (the flat top closes the outline from the last point to the first)
    else:
        raise ValueError(f"Unknown lens shape: {shape}")
    return _resample(_fit_box(pts, width, height), count)


# ---- geometry helpers ---------------------------------------------------------------------------------------------------------------------------
def _unit(v):
    v = np.asarray(v, float)
    return v / np.maximum(np.linalg.norm(v, axis=-1, keepdims=True), 1e-12)


def _outward(outline):
    """Outward unit normals of a counter-clockwise outline (n, 2)."""
    t = _unit(np.roll(outline, -1, 0) - np.roll(outline, 1, 0))
    return np.stack([t[:, 1], -t[:, 0]], 1)


def _surface(cx, cy, curve):
    """surface(x, y) -> (z, unit normal) of a lens front: a sphere of the base curve's radius (530 / base mm) whose middle (cx, cy) sits
    at z = 0, or a plane with no curve."""
    if curve < 0.05:
        return lambda x, y: (np.zeros_like(x), np.tile([0.0, 0.0, 1.0], (len(x), 1)))
    radius = 530.0 / curve

    def surface(x, y):
        dx, dy = x - cx, y - cy
        s = np.sqrt(np.maximum(radius ** 2 - dx ** 2 - dy ** 2, 1e-6))
        return s - radius, np.stack([dx, dy, s], 1) / radius
    return surface


def _crossing(curve, y, side):
    """Where a closed 3D polyline crosses height `y`, on its nose side ("min" x) or temple side ("max" x): (point, segment, fraction)."""
    a, b = curve, np.roll(curve, -1, 0)
    hit = np.nonzero(((a[:, 1] - y) * (b[:, 1] - y) <= 0) & (a[:, 1] != b[:, 1]))[0]
    if not len(hit):
        raise ValueError("The lens is too short for the bridge or hinge height asked for")
    f = (y - a[hit, 1]) / (b[hit, 1] - a[hit, 1])
    points = a[hit] + f[:, None] * (b[hit] - a[hit])
    pick = int(np.argmin(points[:, 0]) if side == "min" else np.argmax(points[:, 0]))
    return points[pick], int(hit[pick]), float(f[pick])


def _at(values, index, fraction):
    return float(values[index] + fraction * (values[(index + 1) % len(values)] - values[index]))


def _bezier(points, count):
    p = np.asarray(points, float)
    t = np.linspace(0, 1, count)[:, None]
    return (1 - t) ** 2 * p[0] + 2 * (1 - t) * t * p[1] + t ** 2 * p[2]


def _rotation_y(degrees):
    c, s = math.cos(math.radians(degrees)), math.sin(math.radians(degrees))
    return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])


def _about(degrees, pivot):
    """4x4: a turn of `degrees` about the vertical line through x = pivot (positive swings +x backward, -z)."""
    m = np.eye(4)
    m[:3, :3] = _rotation_y(degrees)
    m[:3, 3] = np.array([pivot, 0, 0]) - pm.matmul(m[:3, :3], np.array([pivot, 0, 0]))
    return m


def _frames_planar(path, normal):
    return pm.planar_frames(path, normal)


# ---- the build ------------------------------------------------------------------------------------------------------------------------------------
def _rim_sizes(r, outline):
    """(width, depth) of the rim at every point of the lens outline, in mm; None for rimless."""
    if r["rim"] == "rimless":
        return None
    height = r["lens_h"]
    ny = outline[:, 1] / (height / 2)                 # -1 (bottom) .. 1 (top) on the lens
    full_w, full_d = np.full(len(outline), r["rim_w"]), np.full(len(outline), r["rim_d"])
    if r["rim"] == "browline":                        # a heavy bar over the top, a thin wire round the rest
        s = _smooth(ny, -0.12, 0.38)
        wire_w, wire_d = min(r["rim_w"], 1.6), min(r["rim_d"], 1.7)
        return wire_w + (r["brow_w"] - wire_w) * s, wire_d + (r["rim_d"] - wire_d) * s
    if r["rim"] == "half":                            # a rim over the top half, a thread under the lens
        s = _smooth(ny, -0.45, -0.02)
        return 0.5 + (r["rim_w"] - 0.5) * s, 0.5 + (r["rim_d"] - 0.5) * s
    return full_w, full_d


def build(recipe, faces=30_000):
    """(GLB bytes, info) for a recipe (validated here). `faces` is the game version's size the item asks for: it picks how finely the
    curves are built. info: {"triangles", "detail", "size_m": [x, y, z], "frame_width_mm", "arm_mm", "origin"}."""
    data = load()
    r = clean_recipe(recipe)
    det = DETAIL[detail_for(int(faces))]
    material = data["materials"][r["frame_material"]]
    n = det["n"]
    width, height, gap = r["lens_w"], r["lens_h"], r["bridge"]
    x0 = gap / 2                                       # where the lens boxes begin: the nose side of the lens
    cx = x0 + width / 2                                # the middle of the lens on the wearer's left (+x); the right one is its mirror
    prof, prof_n = pm.profile_rounded_rect(material["corner"], det["corner"])
    round_prof, round_n = pm.profile_rounded_rect(1.0, det["corner"] + 2)           # a wire's cross-section: 12, 16 or 24 points

    # ---- the lens opening and the rim round it
    aperture = lens_outline(r["shape"], width, height, n) + np.array([cx, 0.0])
    normal = _outward(aperture)
    sizes = _rim_sizes(r, aperture)
    thickness = LENS_THICKNESS if sizes is None else float(np.clip(0.9 * sizes[1].min(), 1.2, LENS_THICKNESS))
    if sizes is None:
        thickness = LENS_THICKNESS + 0.2
    surface = _surface(cx, 0.0, r["base_curve"])
    mid = lambda x, y: surface(x, y)[0] - thickness / 2                      # noqa: E731  (the lens's middle surface)
    parts = {"frame": [], "hardware": [], "pad": [], "lens": []}
    rim_curve = rim_w = rim_d = None
    if sizes is not None:
        rim_w, rim_d = sizes
        lip = np.minimum(LIP, 0.3 * rim_w)
        centre = aperture + normal * (rim_w / 2 - lip)[:, None]
        z = mid(centre[:, 0], centre[:, 1])
        rim_curve = np.column_stack([centre, z])
        tangent = pm.tangents(rim_curve, closed=True)
        surface_normal = surface(centre[:, 0], centre[:, 1])[1]
        out_dir = _unit(np.cross(tangent, surface_normal))
        up_dir = _unit(np.cross(out_dir, tangent))
        parts["frame"].append(pm.sweep(rim_curve, np.stack([out_dir, up_dir], 1), prof, prof_n,
                                       scales=np.stack([rim_w, rim_d], 1), closed=True))
        overshoot = np.clip(rim_w - lip - 0.05, 0.0, 0.6)
    else:
        overshoot = np.zeros(n)
    lens = pm.lens_slab(aperture + normal * overshoot[:, None], surface, thickness, rings=det["rings"])
    parts["lens"].append(lens)

    # ---- where the bridge and the arm meet the lens
    hinge_h, bridge_h = r["hinge_y"] * height, r["bridge_height"] * height
    arm_h, arm_w = r["temple_h"], r["temple_w"]
    arm_prof, arm_prof_n = (round_prof, round_n) if r["frame_material"] == "metal" else (prof, prof_n)
    if rim_curve is not None:
        hinge_at, i, f = _crossing(rim_curve, hinge_h, "max")
        near_at, j, g = _crossing(rim_curve, bridge_h, "min")
        hinge_rim_w, near_rim_w, near_rim_d = _at(rim_w, i, f), _at(rim_w, j, g), _at(rim_d, j, g)
    else:                                              # rimless: the bridge and the arm are screwed to the lens
        edge = np.column_stack([aperture - normal * 1.8, mid(aperture[:, 0] - normal[:, 0] * 1.8, aperture[:, 1] - normal[:, 1] * 1.8)])
        hinge_at, _i, _f = _crossing(edge, hinge_h, "max")
        near_at, _j, _g = _crossing(edge, bridge_h, "min")
        hinge_rim_w = near_rim_w = r["rim_w"]
        near_rim_d = r["rim_d"]
    back = 3.0 + 1.1 * (r["rim_d"] if rim_curve is not None else 1.4)           # how far behind the lens the hinge sits, mm
    reach = hinge_rim_w / 2 + 0.8
    hinge = hinge_at + np.array([reach, 0.0, -back])
    path = _bezier([hinge_at - np.array([0.3 * hinge_rim_w, 0, 0]), hinge_at + np.array([reach, 0, 0]), hinge], 10)
    scales = np.tile([arm_h, arm_w], (len(path), 1))
    parts["frame"].append(pm.sweep(path, _frames_planar(path, [0, 1, 0]), arm_prof, arm_prof_n, scales=scales, caps=("dome", "flat")))
    pin = max(0.55, 0.28 * arm_w)
    parts["hardware"].append(pm.cylinder(hinge + [0, -(arm_h / 2 + 0.4), 0], hinge + [0, arm_h / 2 + 0.4, 0], pin, det["around"]))
    if sizes is None:                                  # screws through the lens at the two joints
        for at in (hinge_at, near_at):
            parts["hardware"].append(pm.cylinder(at + [0, 0, 0.8], at + [0, 0, -thickness - 0.8], 0.8, det["around"]))

    # ---- nose pads on thin arms (metal frames)
    pad_points = None
    if r["pads"]:
        top_at, _k, _h = _crossing(rim_curve if rim_curve is not None else edge, 0.06 * height, "min")
        pad_mid = np.array([x0 - 0.3, 0.06 * height - 10.0, -9.0])
        arm = _bezier([top_at, top_at + [-0.4, -3.5, -3.0], pad_mid + [0.3, 1.5, 0.0]], 12)
        wire = 0.5
        parts["hardware"].append(pm.sweep(arm, pm.round_frames(arm), round_prof, round_n,
                                          scales=np.full((len(arm), 2), 2 * wire)))
        facing = _unit([-1.0, 0.0, -0.4])
        long_axis = _unit([-math.sin(math.radians(12)), math.cos(math.radians(12)), 0.0])
        long_axis = _unit(long_axis - facing * np.sum(long_axis * facing))
        axes = np.stack([long_axis * 6.2, np.cross(facing, long_axis) * 3.4, facing * 0.9])
        parts["pad"].append(pm.ellipsoid(pad_mid, axes, around=det["around"], rings=max(4, det["around"] // 2)))
        pad_points = pad_mid

    # ---- the wearer's left half bent round the face, the right one its mirror
    wrap = _about(r["wrap"], x0)
    half = {key: [m.transformed(wrap) for m in meshes] for key, meshes in parts.items()}
    hinge_left = pm.matmul(wrap, np.append(hinge, 1))[:3]
    near_left = pm.matmul(wrap, np.append(near_at, 1))[:3]
    frame_meshes = list(half["frame"]) + [m.mirrored_x() for m in half["frame"]]
    hardware_meshes = list(half["hardware"]) + [m.mirrored_x() for m in half["hardware"]]
    pad_meshes = list(half["pad"]) + [m.mirrored_x() for m in half["pad"]]
    lens_meshes = list(half["lens"]) + [m.mirrored_x() for m in half["lens"]]

    # ---- the bridge: from one rim to the other, an arch (or two bars)
    bridge_w, bridge_d = near_rim_w * (0.9 if rim_curve is not None else 1.0), near_rim_d
    style = r["bridge_style"]
    rise = {"arch": 3.0, "keyhole": 5.5, "double": 1.6}[style]
    t = np.linspace(-1, 1, 24)
    power = 2.6 if style == "keyhole" else 2.0
    ends = np.array([abs(near_left[0]), near_left[1], near_left[2]])
    arch = np.stack([ends[0] * t, ends[1] + rise * (1 - np.abs(t) ** power), np.full(len(t), ends[2])], 1)
    bridges = [(arch, bridge_w, bridge_d)]
    if style == "double" and rim_curve is not None:
        bar_at, _a, _b = _crossing(rim_curve, 0.4 * height, "min")
        bar = pm.matmul(wrap, np.append(bar_at, 1))[:3]
        bar_path = np.stack([abs(bar[0]) * t, bar[1] + 0.8 * (1 - t ** 2), np.full(len(t), bar[2])], 1)
        bridges.append((bar_path, bridge_w * 0.9, bridge_d * 0.9))
    for path_, w_, d_ in bridges:
        scales = np.tile([d_, w_], (len(path_), 1))
        frame_meshes.append(pm.sweep(path_, _frames_planar(path_, [0, 0, 1]), arm_prof, arm_prof_n, scales=scales, caps=("dome", "dome")))

    # ---- the arms, each its own node with its origin at the hinge
    arm_mesh = _arm(r, det, arm_prof, arm_prof_n)
    arm_left = arm_mesh
    arm_right = arm_mesh.mirrored_x()

    # ---- materials and nodes (millimetres to metres)
    k = 0.001 * r["scale"]
    scale_up = np.diag([k, k, k, 1.0])
    gltf = pm.Gltf()
    frame_rgb = _rgb(r["frame_color"])
    if r["frame_material"] == "plastic":
        hardware_rgb, hardware_metal, hardware_rough = (0.78, 0.8, 0.82), 1.0, 0.3
    elif r["frame_material"] == "matte":
        hardware_rgb, hardware_metal, hardware_rough = (0.23, 0.25, 0.27), 1.0, 0.45
    else:
        hardware_rgb, hardware_metal, hardware_rough = frame_rgb, 1.0, material["roughness"]
    m_frame = gltf.material("frame", frame_rgb, material["metallic"], material["roughness"])
    m_hardware = gltf.material("hardware", hardware_rgb, hardware_metal, hardware_rough)
    m_pad = gltf.material("pads", (0.9, 0.93, 0.9), 0.0, 0.3, opacity=0.55)
    mirror = r["lens_finish"] == "mirror"
    opacity = 1.0 if mirror else float(r["lens_opacity"])
    per_surface = 1.0 if (mirror or opacity >= 0.999) else 1 - math.sqrt(1 - opacity)         # the lens has a front and a back surface
    lens_all = pm.merge(lens_meshes)
    texture, lens_alpha = None, per_surface
    if r["lens_gradient"] and not mirror:              # darker at the top, lighter at the bottom: the alpha of a tiny texture, which every engine reads
        top_alpha = min(1.0, per_surface * 1.3)        # (vertex colours did the same in the viewer, but Godot leaves them off unless told)
        texture, lens_alpha = gltf.texture(_gradient_png(per_surface * 0.3 / top_alpha)), top_alpha
        low, span = lens_all.pos[:, 1].min(), max(np.ptp(lens_all.pos[:, 1]), 1e-9)
        lens_all = lens_all.with_uv(lambda q: np.column_stack([np.full(len(q), 0.5), 1.0 - (q[:, 1] - low) / span]))
    m_lens = gltf.material("lens", _rgb(r["lens_color"]), 1.0 if mirror else 0.0, 0.06 if mirror else 0.04, opacity=lens_alpha, texture=texture)
    scaled = lambda m: m.transformed(scale_up)                                  # noqa: E731
    mesh_frame = gltf.mesh("frame", [(scaled(pm.merge(frame_meshes)), m_frame), (scaled(pm.merge(hardware_meshes)), m_hardware),
                                     (scaled(pm.merge(pad_meshes)), m_pad)])
    mesh_lens = gltf.mesh("lenses", [(scaled(lens_all), m_lens)])
    mesh_arm_l = gltf.mesh("temple_left", [(scaled(arm_left), m_frame)])
    mesh_arm_r = gltf.mesh("temple_right", [(scaled(arm_right), m_frame)])
    folded = r["pose"] == "folded"
    turn = 90.0 + (0.0)
    left_hinge, right_hinge = hinge_left * k, hinge_left * np.array([-1, 1, 1]) * k
    kids = [gltf.node("frame", mesh_frame), gltf.node("lenses", mesh_lens),
            gltf.node("temple_left", mesh_arm_l, translation=left_hinge, rotation=pm.yaw_quaternion(turn) if folded else None),
            gltf.node("temple_right", mesh_arm_r, translation=right_hinge, rotation=pm.yaw_quaternion(-(turn - 5.0)) if folded else None)]
    root = gltf.node("glasses", children=kids)
    extras = {"generator": "NPC Forge glasses maker", "recipe": r, "units": "metres", "origin": EYE_LEVEL_NOTE,
              "attach": "Head", "front": "+Z", "nodes": "temple_left and temple_right turn about their own origin (the hinge)"}
    glb = gltf.dumps([root], extras)
    low, high = _bounds(gltf, kids, folded)
    info = {"triangles": int(sum(m.faces for m in (pm.merge(frame_meshes), pm.merge(hardware_meshes), pm.merge(pad_meshes), lens_all,
                                                   arm_left, arm_right))),
            "detail": detail_for(int(faces)), "size_m": [round(float(v), 4) for v in high - low],
            "frame_width_mm": round(float(np.ptp(pm.merge(frame_meshes + lens_meshes).pos[:, 0])), 1),
            "arm_mm": r["temple_len"], "origin": "bridge"}
    return glb, info


def _rgb(hex_colour):
    return tuple(int(hex_colour[i:i + 2], 16) / 255 for i in (1, 3, 5))


def _gradient_png(bottom, width=4, height=256):
    """A white picture whose alpha runs from 1 at the top to `bottom` at the bottom (smoothly): a gradient lens's darkness."""
    t = np.linspace(0.0, 1.0, height)
    ease = t * t * (3 - 2 * t)
    alpha = np.clip(np.round(255 * (1.0 - (1.0 - bottom) * ease)), 0, 255).astype(np.uint8)
    pixels = np.full((height, width, 4), 255, np.uint8)
    pixels[:, :, 3] = alpha[:, None]
    out = io.BytesIO()
    Image.fromarray(pixels, "RGBA").save(out, "PNG", optimize=True)
    return out.getvalue()


def _arm(r, det, prof, prof_n):
    """One arm (the wearer's left, +x) in millimetres with its origin at the hinge: straight back along the splay, then bending down
    behind the ear, tapering toward a rounded tip."""
    length, bend = r["temple_len"], r["temple_bend"]
    s = math.radians(r["splay"])
    along = np.array([math.sin(s), 0.0, -math.cos(s)])
    down = np.array([0.0, -1.0, 0.0])
    count = det["arm"]
    drop = math.radians(r["temple_drop"])
    straight = bend * length
    if drop < math.radians(0.5):
        path = np.array([along * d for d in np.linspace(0, length, count)])
    else:
        rest = length - straight
        arc_len = 0.62 * rest
        radius = arc_len / drop
        tail = rest - arc_len
        m1, m2 = max(4, int(count * 0.45)), max(4, int(count * 0.35))
        m3 = max(3, count - m1 - m2)
        part1 = [along * d for d in np.linspace(0, straight, m1, endpoint=False)]
        a = np.linspace(0, drop, m2, endpoint=False)
        part2 = [along * (straight + radius * math.sin(x)) + down * radius * (1 - math.cos(x)) for x in a]
        end = along * (straight + radius * math.sin(drop)) + down * radius * (1 - math.cos(drop))
        slope = along * math.cos(drop) + down * math.sin(drop)
        part3 = [end + slope * d for d in np.linspace(0, tail, m3)]
        path = np.array(part1 + part2 + part3)
    run = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(path, axis=0), axis=1))])
    t = run / run[-1]
    side = _unit(np.cross(down, along))
    thick = r["temple_w"] * (1 - 0.25 * t)
    tall = r["temple_h"] * (1 - 0.5 * _smooth(t, 0.35, 1.0))
    return pm.sweep(path, _frames_planar(path, side), prof, prof_n, scales=np.stack([thick, tall], 1), caps=("flat", "dome"))


def _bounds(gltf, kids, folded):
    """The pair's bounding box in metres (the arms under their node transforms)."""
    lows, highs = [], []
    for index in kids:
        node = gltf.doc["nodes"][index]
        if "mesh" not in node:
            continue
        for prim in gltf.doc["meshes"][node["mesh"]]["primitives"]:
            acc = gltf.doc["accessors"][prim["attributes"]["POSITION"]]
            low, high = np.array(acc["min"]), np.array(acc["max"])
            corners = np.array([[x, y, z] for x in (low[0], high[0]) for y in (low[1], high[1]) for z in (low[2], high[2])])
            if "rotation" in node:
                q = node["rotation"]
                angle = 2 * math.atan2(q[1], q[3])
                corners = pm.transform(corners, _rotation_y(math.degrees(angle)))
            corners = corners + np.array(node.get("translation", (0, 0, 0)))
            lows.append(corners.min(0))
            highs.append(corners.max(0))
    return np.min(lows, 0), np.max(highs, 0)
