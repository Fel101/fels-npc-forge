"""The body options of a character: bust size, shape and areolas.

Each option is a label the studio shows and the words the picture model is given: measured,
"a small bust with small, pale rose areolas" and "a large, heavy bust with wide, dark brown areolas" draw visibly different
figures from the same portrait, while an empty description gives every character the same default one.

A character's options are chosen once (by you, or from the character's own id so that every character starts different), saved
in <character>/adult/studio.json and written into every picture, in NPC Forge and in Altera. Bust size and shape go into every
picture; the areolas only into the explicit level, where they can be seen.
"""
import hashlib
import random

OPTIONS = {
    "bust": [
        {"id": "very_small", "label": "Very small", "text": "a very small, slight bust", "w": 3},
        {"id": "small", "label": "Small", "text": "a small bust", "w": 20},
        {"id": "medium", "label": "Medium", "text": "a medium, natural bust", "w": 32},
        {"id": "full", "label": "Full", "text": "a full bust", "w": 27},
        {"id": "large", "label": "Large", "text": "a large, heavy bust", "w": 15},
        {"id": "very_large", "label": "Very large", "text": "a very large, heavy bust", "w": 3},
    ],
    "shape": [
        {"id": "round", "label": "Round", "text": "round", "w": 3},
        {"id": "teardrop", "label": "Teardrop", "text": "teardrop-shaped", "w": 3},
        {"id": "perky", "label": "High and firm", "text": "high and firm", "w": 2},
        {"id": "soft", "label": "Soft and natural", "text": "soft and natural", "w": 3},
        {"id": "athletic", "label": "Athletic", "text": "firm and athletic", "w": 1.5},
    ],
    "areola_size": [
        {"id": "small", "label": "Small", "text": "small", "w": 25},
        {"id": "medium", "label": "Medium", "text": "medium-sized", "w": 45},
        {"id": "large", "label": "Large", "text": "large", "w": 25},
        {"id": "wide", "label": "Very wide", "text": "very wide", "w": 5},
    ],
    "areola_color": [
        {"id": "pale_pink", "label": "Pale pink", "text": "pale pink"},
        {"id": "rose", "label": "Rose", "text": "rose"},
        {"id": "rose_brown", "label": "Rose-brown", "text": "rose-brown"},
        {"id": "light_brown", "label": "Light brown", "text": "light brown"},
        {"id": "brown", "label": "Brown", "text": "brown"},
        {"id": "dark_brown", "label": "Dark brown", "text": "dark brown"},
        {"id": "deep_brown", "label": "Deep brown", "text": "deep dark brown"},
    ],
}
FIELDS = tuple(OPTIONS)
LABELS = {"bust": "Bust size", "shape": "Shape", "areola_size": "Areola size", "areola_color": "Areola colour"}

_BIGGER = ("curvy", "hourglass", "full-figured", "full figured", "voluptuous")
_SMALLER = ("slim", "slender", "lean")


def _option(field, value):
    return next((o for o in OPTIONS[field] if o["id"] == value), None)


def areola_color_for(skin) -> str:
    """The areola colour that goes with a skin tone, unless one is chosen."""
    s = (skin or "").lower()
    if "deep" in s or "dark" in s or "ebony" in s:
        return "dark_brown" if "deep" not in s else "deep_brown"
    if "brown" in s:
        return "light_brown" if "light" in s else "brown"
    if "olive" in s or "tan" in s or "golden" in s or "bronze" in s:
        return "rose_brown"
    if "very fair" in s or "pale" in s or "porcelain" in s:
        return "pale_pink"
    if "fair" in s or "light" in s:
        return "rose"
    return "rose_brown"


def defaults(npc_id, appearance=None) -> dict:
    """The options a character starts with: different for every character, the same every time for the same one."""
    rng = random.Random(int.from_bytes(hashlib.sha1(str(npc_id).encode()).digest()[:8], "big"))
    build = ((appearance or {}).get("build") or "").lower()
    out = {}
    for field in ("bust", "shape", "areola_size"):
        weights = []
        for o in OPTIONS[field]:
            w = o["w"]
            if field == "bust":
                if any(b in build for b in _BIGGER) and o["id"] in ("full", "large", "very_large"):
                    w *= 2.5
                if any(b in build for b in _SMALLER) and o["id"] in ("very_small", "small", "medium"):
                    w *= 2
            weights.append(w)
        out[field] = rng.choices(OPTIONS[field], weights=weights)[0]["id"]
    out["areola_color"] = areola_color_for((appearance or {}).get("skin_tone"))
    return out


def clean(body, base=None) -> dict:
    """Only known options survive; anything missing comes from `base` (the defaults)."""
    body = body if isinstance(body, dict) else {}
    out = dict(base or {})
    for field in FIELDS:
        if _option(field, body.get(field)):
            out[field] = body[field]
    return {f: out[f] for f in FIELDS if f in out}


def valid(field, value) -> bool:
    return field in OPTIONS and _option(field, value) is not None


def areolas(body) -> str:
    """'medium-sized rose-brown areolas' ('' if neither size nor colour is set)."""
    b = clean(body)
    size, colour = _option("areola_size", b.get("areola_size")), _option("areola_color", b.get("areola_color"))
    words = [o["text"] for o in (size, colour) if o]
    return " ".join(words + ["areolas"]) if words else ""


def describe(body, explicit=False) -> str:
    """The words for the picture model. explicit=True adds the areolas."""
    b = clean(body)
    bust, shape = _option("bust", b.get("bust")), _option("shape", b.get("shape"))
    if not bust:
        return ""
    text = bust["text"] + (f", {shape['text']}" if shape else "")
    if explicit and areolas(b):
        text += ", with " + areolas(b)
    return text


def choices() -> list:
    """What the studio page shows: [{field, label, options: [{id, label}]}]."""
    return [{"field": f, "label": LABELS[f], "options": [{"id": o["id"], "label": o["label"]} for o in OPTIONS[f]]} for f in FIELDS]
