"""The clothing designer of the Items tab: a shirt, trousers, a knee-length skirt, a sleeveless dress, an open jacket or a bib apron from a few settings (colour, looseness, length, cloth), built on the spot and tried on a designed body.

The garments are the Body Designer's (bodydesigner/garments.py): original parametric patterns that it fits to the body it is given, every time, so a garment is only ever
worn by a body. This module is what the Items tab needs around that, the way npcgen/glasses.py is for glasses:

  - the recipe (`clean_recipe`, `from_description`, `catalogue`): the Designer's own recipe, checked with the Designer's own ranges, plus a few words that set the controls;
  - the model (`build`): the pattern on its own, ready in a few hundredths of a second, which the designer's live preview and the Items list show. It is not fitted to anybody:
    it stands where it would on an adult 1.7 m tall, the origin on the floor between the feet;
  - the body (`body_of`, `fit_key`, `recolour`): which characters can wear it (a designed body: the Body Designer's own files), what a fit depends on, and how a colour changed after
    the fit is put on the finished file. The fitting itself is slow (about half a minute) and is done by npcgen/dressing.py in a process of its own.

Without the Body Designer (it is the bodydesigner folder next to npcgen, and optional) there are no fitted clothes: the family is not offered and nothing here raises on import.
"""
import json
import re
import time

from . import config as cfgmod, outfit
from .paint3d import Glb, all_triangles

DATA_FILE = cfgmod.ROOT / "data" / "clothing.json"
HEX = re.compile(r"^#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{3})$")
NAMES = dict(outfit.NODES)                                                 # the Designer's name for each garment's mesh (shirt, trousers, skirt, dress, jacket, apron), and (with Fabric) its material
STANDING = "origin on the floor between the feet of an adult 1.7 m tall, where the garment would be on one; not fitted to a body"


def load():
    """The clothing tables, read fresh so edits to data/clothing.json apply without restarting the server."""
    return json.loads(DATA_FILE.read_text("utf-8"))


def _garments():
    try:
        from bodydesigner import garments
    except ImportError:
        raise ValueError("Fitted clothes need the Body Designer (the bodydesigner folder next to npcgen), which is not installed here") from None
    return garments


def available():
    """True when this Body Designer makes garments."""
    try:
        _garments()
    except ValueError:
        return False
    return True


def _entries():
    """The Body Designer's garments that this module can dress (NAMES). One it adds later is offered once it is wired in: its mesh name, what it replaces and what it goes over."""
    return {entry["id"]: entry for entry in _garments().catalogue() if entry["id"] in NAMES}


def catalogue():
    """What the editor needs, for /api/config: whether there are garments at all, the labels, units and steps of the number fields, the cloth list and the swatches, and for each kind
    the recipe it starts as, the range the Body Designer offers for each control (a kind's own: a shirt's length is not the trousers') and, where it gives one, its reason (`notes`),
    what it goes over (`over`: the jacket and the apron are checked over a shirt and trousers and over nothing else), what it is worn with (`with`: a skirt, with a shirt; a dress, nothing) and instead of
    (`instead_of`: a skirt is worn without trousers, a dress without a shirt or trousers), and what the Designer says about the layers (`layer_notes`)."""
    data = load()
    try:
        entries = _entries()
    except ValueError as exc:
        return {"available": False, "why": str(exc), "kinds": {}, "fields": {}, "choices": data["choices"], "colours": data["colours"]}
    textures = sorted({t for entry in entries.values() for t in entry["textures"]})
    kinds = {}
    for kind, entry in entries.items():
        kinds[kind] = {"label": entry["label"], "recipe": {k: v for k, v in entry["recipe"].items() if k != "version"},
                       "controls": {key: [entry["controls"][key][0], entry["controls"][key][1]] for key in data["fields"]},
                       "notes": {key: note for key, note in (entry.get("control_notes") or {}).items() if key in data["fields"] and note},
                       "over": outfit.needs(kind, entries) or [], "with": (outfit.worn_with(kind, entries) or []) if kind not in outfit.OUTER else [],
                       "instead_of": outfit.takes_off(kind, entries) or [], "layer_notes": entry.get("layer_notes") or ""}
    return {"available": True, "why": "", "colours": data["colours"], "fields": data["fields"], "kinds": kinds,
            "choices": {"texture": {t: data["choices"]["texture"].get(t, t.capitalize()) for t in textures}}}


# ---- the recipe ----------------------------------------------------------------------------------------------------------------------------------
def _colour(value, data):
    """'#rrggbb' from '#rgb', '#rrggbb' or the name of a swatch, lower case."""
    if isinstance(value, str):
        text = value.strip()
        if HEX.match(text):
            if len(text) == 4:
                text = "#" + "".join(c * 2 for c in text[1:])
            return text.lower()
        names = {c["id"]: c["hex"] for c in data["colours"]}
        if text.lower() in names:
            return names[text.lower()]
    raise ValueError("The colour must be like #3f6ba8 or the name of a swatch")


def _still_read(entry, key, number):
    """True when the Body Designer still reads this value of a control although it no longer offers it: a design saved when it offered more keeps its value and is fitted as it is,
    never rescaled (the page says so under the slider, `notes`). Only the Body Designer says what it still reads."""
    try:
        _garments().clean_recipe({**entry["recipe"], key: number})
    except ValueError:
        return False
    return True


def notes(recipe):
    """What the Body Designer says (its own words) about the controls of this recipe that are outside the range it offers for the garment: [] for a recipe inside it."""
    if not isinstance(recipe, dict):
        return []
    try:
        entry = _entries().get(recipe.get("kind") or "shirt")
    except ValueError:
        return []
    out = []
    for key, note in ((entry or {}).get("control_notes") or {}).items():
        value = recipe.get(key)
        if note and key in entry["controls"] and isinstance(value, (int, float)) and not isinstance(value, bool) and not entry["controls"][key][0] <= value <= entry["controls"][key][1]:
            out.append(note)
    return out


def clean_recipe(raw, kind=None):
    """The complete recipe of a garment from what a caller sent (any part of it): {kind, colour, ease_mm, length, texture}. `kind` (else the recipe's own, else a shirt) says which
    garment; the Body Designer's starting values for it fill what is not given. Raises ValueError saying which setting is wrong: a value the Body Designer does not offer for this
    garment and does not read either (a shirt's length is offered from 0.9 to 1.0 and read from 0.8 to 1.2, so an older design keeps its length). The Body Designer's recipe version is
    not a setting: the shirt is fitted with the newest cut it knows (npcgen/outfit.py), and a `version` sent is dropped."""
    data = load()
    entries = _entries()
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError("The clothing settings must be an object")
    kind = kind or raw.get("kind") or "shirt"
    if kind not in entries:
        raise ValueError(f"Unknown garment: {kind} (one of {', '.join(entries)})")
    entry = entries[kind]
    allowed = {k for k in entry["recipe"] if k != "version"}
    unknown = sorted(set(raw) - allowed - {"version"})
    if unknown:
        raise ValueError("Unknown clothing setting: " + ", ".join(unknown))
    out = {**{k: v for k, v in entry["recipe"].items() if k != "version"}, **{k: v for k, v in raw.items() if k in allowed and v is not None and v != ""}, "kind": kind}
    for key, spec in data["fields"].items():
        value = out[key]
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            raise ValueError(f"{spec['label']} must be a number")
        try:
            number = float(value)
        except ValueError:
            raise ValueError(f"{spec['label']} must be a number") from None
        low, high = entry["controls"][key]
        if not low <= number <= high and not _still_read(entry, key, number):          # (also false for NaN)
            raise ValueError(f"{spec['label']} must be between {low:g} and {high:g}")
        out[key] = round(number, 4)
    if out["texture"] not in entry["textures"]:
        raise ValueError(f"Unknown cloth: {out['texture']} (one of {', '.join(entry['textures'])})")
    out["colour"] = _colour(out["colour"], data)
    return {key: out[key] for key in ("kind", "colour", "ease_mm", "length", "texture")}


def for_item(raw, kind, spec=None):
    """The recipe of an item of this kind (items.clean_item): the item's kind is the garment's."""
    return clean_recipe(raw, kind=kind)


def _words(text):
    """The text as the vocabulary is matched against: lower case, punctuation gone, 'long sleeves' and 'short sleeves' out of the way (they say nothing about the hem of a sleeveless shirt)."""
    t = " " + " ".join(re.sub(r"[^a-z0-9' ]", " ", str(text or "").lower().replace("-", " ")).split()) + " "
    return re.sub(r"(?<![a-z'])(?:long|short|three quarter|half)(?: length)? sleeve(?:s|d)?(?![a-z'])", " ", t)


def from_description(text, recipe=None):
    """What a few words say about a garment: ({setting: value}, ["what was understood", ...]) to lay over `recipe` (the current one; the kind's starting one when None). It reads a
    colour the way a character's outfit is read (npcgen/outfit.py: 'dark red', 'navy', 'canvas') and the vocabulary in data/clothing.json (snug, loose, baggy, cropped, long, woven, plain...),
    so the same words always give the same answer and nothing is guessed: a word it does not know changes nothing. The longest phrase wins ('very loose' is baggy, not loose)."""
    data = load()
    base = clean_recipe(recipe)
    t = _words(text)
    out, said = {}, []

    def best(groups):
        found = []
        for group in groups:
            for phrase in group["phrases"]:
                if re.search(rf"(?<![a-z']){re.escape(phrase)}(?![a-z'])", t):
                    found.append((len(phrase), group))
        return max(found, key=lambda f: f[0])[1] if found else None

    colour = outfit.named_colour(t)
    if colour and colour != base["colour"]:
        out["colour"] = colour
        said.append(f"the colour {colour}")
    ease = best(data["words"]["ease"])
    if ease is not None:
        low, high = _entries()[base["kind"]]["controls"]["ease_mm"]
        out["ease_mm"] = round(min(max(ease["mm"], low), high), 4)
        limit = "" if out["ease_mm"] == ease["mm"] else f", the {'tightest' if ease['mm'] < low else 'loosest'} the Body Designer offers for a {base['kind']}"
        said.append(f"{ease['say']} ({out['ease_mm']:g} mm looser than the body{limit})")
    length = best(data["words"]["length"])
    if length is not None:
        low, high = _entries()[base["kind"]]["controls"]["length"]
        out["length"] = round(min(max(length["length"], low), high), 4)
        limit = "" if out["length"] == length["length"] else f", the {'shortest' if length['length'] < low else 'longest'} the Body Designer offers for a {base['kind']}"
        said.append(f"{length['say']} (length ×{out['length']:g}{limit})")
    cloth = best(data["words"]["cloth"])
    if cloth is not None and cloth["texture"] in _entries()[base["kind"]]["textures"] and cloth["texture"] != base["texture"]:
        out["texture"] = cloth["texture"]
        said.append(cloth["say"])
    return out, said


def explain(why):
    """A refusal of the Body Designer's fit in words a person can act on, with the Designer's own words after it (so it can be passed on as it is). For a shirt at a length it does not
    offer those words already say what to try (its guidance for a length that failed), so nothing is added here: the page shows its note under the slider, where the length is chosen."""
    plain = next((entry["plain"] for entry in load()["refusals"] if str(why).startswith(entry["code"])), None)
    return f"{plain} ({why})" if plain else str(why)


# ---- the model: the pattern on its own ------------------------------------------------------------------------------------------------------------
def _recipe_of(recipe):
    """The Body Designer's recipe (with its version) for one of ours."""
    entries = _entries()
    clean = clean_recipe(recipe)
    return _garments().clean_recipe({**entries[clean["kind"]]["recipe"], **clean})


def build(recipe, faces=None):
    """(GLB bytes, info) of the garment's pattern on its own: a few hundredths of a second, no body. `faces` is only there because every recipe maker takes it (the pattern's
    triangles are the Body Designer's, 3 to 5 thousand). info: {size_m [x, y, z], origin, triangles, kind}."""
    started = time.perf_counter()
    full = _recipe_of(recipe)
    glb, made = _garments().build_pattern(full)
    pos, _tri = all_triangles(Glb(glb))
    size = [round(float(v), 4) for v in pos.max(0) - pos.min(0)]
    return glb, {"size_m": size, "origin": "floor", "triangles": made["triangles"], "kind": full["kind"], "seconds": round(time.perf_counter() - started, 3)}


def model_meta(info):
    """What an item's model record says about how it was made."""
    return {"engine": "Clothing designer (Body Designer garments)", "engine_id": "garment", "textured": True, "triangles": info["triangles"], "kind": info["kind"], "standing": STANDING}


# ---- the body ----------------------------------------------------------------------------------------------------------------------------------------
def body_of(npc):
    """(plain file, animated file or None, the record of the design) of a character whose 3D model is a designed body (npcgen/bodymaker.py), which are the files the Body Designer's
    garments fit. Raises ValueError saying why a character cannot wear fitted clothes."""
    assets = npc.get("assets") or {}
    model = assets.get("model_3d") or {}
    if not model.get("file"):
        raise ValueError("This character has no 3D model yet")
    if not model.get("designed"):
        raise ValueError("This character's 3D model was not made by the Body Designer: only a designed body can wear fitted clothes (the Body page, then Use this body)")
    rig = assets.get("rig_3d") or {}
    return model["file"], (rig["file"] if rig.get("file") and rig.get("joints_from") == "designed body" else None), model["designed"]


def worn_by(npc):
    """[{kind, colour, version, ease_mm, length, texture}] the garments a character's designed body was made wearing (the record of the design: the Items tab's try-on never changes it), or
    []. A body made before the record kept looseness and length has only {kind, colour, version}."""
    designed = ((npc.get("assets") or {}).get("model_3d") or {}).get("designed") or {}
    return [c for c in designed.get("clothes") or [] if isinstance(c, dict) and c.get("kind") in NAMES]


def can_wear(npc, recipe, kind=None):
    """(True, "") when this garment can be put on this character, else (False, why in a sentence): a designed body (`body_of`) and, for a garment that goes over others (the jacket, the
    apron), the shirt and trousers it is checked over and nothing looser than the Body Designer checked them with (outfit.stack_problem, outfit.margin_problem), judged on what the body
    was made wearing, with what the new garment takes off (the garment of its kind, the other outer garment, the trousers for a skirt, the shirt and trousers for a dress: outfit.kept) taken off. The fit decides again from
    the file when it is made (outfit.wear): this is what the page can say before then."""
    try:
        body_of(npc)
    except ValueError as exc:
        return False, str(exc)
    try:
        clean = clean_recipe(recipe, kind=kind)
    except ValueError:
        return True, ""                                          # (a recipe that is wrong is the fit's to say: what the body wears is not the reason)
    wears = worn_by(npc)
    after = outfit.kept({c["kind"] for c in wears}, {clean["kind"]}) | {clean["kind"]}
    problem = outfit.stack_problem(after, only=[clean["kind"]])
    if problem:
        return False, problem
    # (the loosest shirt and trousers it was checked over, and a shirt or trousers under what the body wears: no looser than that was checked over)
    known = {c["kind"]: c["ease_mm"] for c in wears if c["kind"] in outfit.BASE and c["kind"] in after and c["kind"] != clean["kind"] and isinstance(c.get("ease_mm"), (int, float))}
    problem = outfit.margin_problem(clean["kind"], known)
    if not problem and clean["kind"] in outfit.BASE:
        problem = next(filter(None, (outfit.margin_problem(c["kind"], {clean["kind"]: clean["ease_mm"]}) for c in wears if c["kind"] in after and c["kind"] != clean["kind"])), None)
    return (False, problem) if problem else (True, "")


def body_stamp(npc):
    """Which files of a character a fit was made for: their names, dates and sizes."""
    assets = npc.get("assets") or {}
    return [{k: (assets.get(kind) or {}).get(k) for k in ("file", "created", "size")} for kind in ("model_3d", "rig_3d")]


def fitter_revision():
    """The number the Body Designer gives its garment fitters (`bodydesigner.garments.FIT_REVISION`; 0 when it names none). A change to the fitters that alters a fit which exists already
    raises it, and a fit kept from before is then out of date (the Items tab says so and fits it again); a change that only makes new fits possible leaves it alone."""
    try:
        value = getattr(_garments(), "FIT_REVISION", 0)
    except ValueError:
        return 0
    return value if type(value) is int and value > 0 else 0


def fit_key(recipe, npc):
    """What a fit depends on: the garment, how loose and how long it is, the cut the Body Designer makes of it, the body, and (when the Body Designer names one) its fitter revision. The colour
    and the cloth are not part of it: they are put on the finished file (`recolour`), so changing them never means fitting again."""
    clean = clean_recipe(recipe)
    key = {"kind": clean["kind"], "ease_mm": clean["ease_mm"], "length": clean["length"], "cut": outfit.SHIRT_VERSION if clean["kind"] == "shirt" else _recipe_of(clean)["version"],
           "body": body_stamp(npc)}
    revision = fitter_revision()
    if revision:                                                               # (no key changes while the Body Designer names none: every fit kept so far stays good)
        key["fitter"] = revision
    return key


def recolour(worn, recipe):
    """The dressed body (GLB bytes) with this garment's cloth in the recipe's colour and texture. The fit does not depend on either, so a colour chosen after the fit goes onto the
    finished file: the garment's texture is swapped for the one the Body Designer makes for this recipe (so it is always the Designer's own cloth, never a copy of it)."""
    full = _recipe_of(recipe)
    pattern, _ = _garments().build_pattern(full)
    source = Glb(pattern)
    png = source.view_bytes(source.json["images"][0]["bufferView"])
    glb = Glb(worn)
    name = NAMES[full["kind"]] + "Fabric"
    material = next((m for m in glb.json.get("materials", []) if m.get("name") == name), None)
    if material is None:
        raise ValueError(f"The body does not wear this {full['kind']}")
    texture = material["pbrMetallicRoughness"]["baseColorTexture"]["index"]
    glb.replace_image(glb.json["textures"][texture]["source"], png)
    return glb.dumps()
