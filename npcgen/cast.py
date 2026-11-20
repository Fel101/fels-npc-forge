"""Import a cast of characters from JSON or CSV and turn each entry into an NPC draft.

Every entry may give any subset of fields (see FIELD_ALIASES for the accepted column names). Whatever is missing is
rolled (write `none` in accessory, beard, glasses or distinguishing to rule a random one out), except for
description-only entries, where the description alone drives the art. Columns we do not recognise
are kept untouched under npc["custom"], so game-specific data (schedules, stats, ids) travels with the character.
"""
import csv
import io
import json
import re

from . import morphology, roll

MAX_CAST = 500

# target field -> accepted column names (case-insensitive; spaces and hyphens count as underscores)
FIELD_ALIASES = {
    "profile.first_name": ["first_name", "firstname", "first", "given_name", "forename"],
    "profile.last_name": ["last_name", "lastname", "last", "surname", "family_name"],
    "profile.gender": ["gender", "sex"],
    "profile.age": ["age"],
    "profile.occupation": ["occupation", "job", "career", "role", "profession", "class"],
    "profile.heritage": ["heritage", "ethnicity", "origin"],
    "profile.race": ["race", "species"],
    "profile.personality": ["personality", "traits", "trait"],
    "profile.hobbies": ["hobbies", "hobby", "interests"],
    "profile.quirk": ["quirk"],
    "profile.goal": ["goal", "dream", "motivation", "ambition"],
    "profile.peeve": ["peeve", "pet_peeve", "dislikes"],
    "profile.speech": ["speech", "speech_style", "voice", "way_of_speaking", "how_they_talk"],
    "profile.demeanor": ["demeanor", "demeanour", "manner", "attitude", "how_they_come_across"],
    "profile.bio": ["bio", "biography", "backstory", "background", "summary"],
    "appearance.build": ["build", "body", "body_type"],
    "appearance.skin_tone": ["skin", "skin_tone", "complexion"],
    "appearance.hair": ["hair", "hairstyle"],
    "appearance.eyes": ["eyes", "eye_color", "eye_colour"],
    "appearance.facial_hair": ["facial_hair", "beard"],
    "appearance.glasses": ["glasses", "eyewear"],
    "appearance.distinguishing": ["distinguishing", "features", "marks", "distinguishing_features"],
    "appearance.outfit": ["outfit", "clothing", "clothes", "costume", "attire", "wardrobe"],
    "appearance.accessory": ["accessory", "accessories", "props", "gear"],
    "appearance.expression": ["expression", "mood", "default_expression"],
    "appearance.extra": ["description", "notes", "appearance", "visual", "look", "prompt", "extra"],
}
NAME_KEYS = {"name", "full_name", "fullname", "character", "npc"}
# An empty cell means "not given, roll it". For fields where nothing is a real answer, these words lock "nothing" instead,
# so a roster can rule out a random hunting knife or beard.
OPTIONAL_PATHS = ("appearance.facial_hair", "appearance.glasses", "appearance.distinguishing", "appearance.accessory")
NOTHING_WORDS = {"none", "no", "nothing", "n/a", "na", "-", "null"}
CONTROL_KEYS = {"setting", "style", "seed", "mode", "cast_role", "member_role", "pack_role", "morphology"}
_LOOKUP = {alias: target for target, aliases in FIELD_ALIASES.items() for alias in aliases}

_GENDERS = {
    "f": "female", "female": "female", "woman": "female", "girl": "female", "she": "female", "w": "female",
    "m": "male", "male": "male", "man": "male", "boy": "male", "he": "male",
    "nb": "nonbinary", "nonbinary": "nonbinary", "non-binary": "nonbinary", "non binary": "nonbinary", "enby": "nonbinary",
    "they": "nonbinary", "other": "nonbinary", "x": "nonbinary",
}


def parse_cast(text, fmt=None):
    """Text of a JSON or CSV cast file -> list of raw entry dicts. Raises ValueError with a readable message."""
    text = (text or "").lstrip("﻿").strip()
    if not text:
        raise ValueError("The cast file is empty")
    fmt = (fmt or ("json" if text[0] in "[{" else "csv")).lower()
    if fmt == "json":
        try:
            data = json.loads(text)
        except ValueError as e:
            raise ValueError(f"The cast file is not valid JSON: {e}") from None
        if isinstance(data, dict):
            found = next((data[k] for k in ("characters", "cast", "npcs", "people") if isinstance(data.get(k), list)), None)
            data = found if found is not None else [data]
        if not isinstance(data, list) or not all(isinstance(e, dict) for e in data):
            raise ValueError("A JSON cast must be a list of objects, or an object with a 'characters' list")
        entries = data
    elif fmt == "csv":
        entries = [dict(row) for row in csv.DictReader(io.StringIO(text))]
    else:
        raise ValueError(f"Unknown cast format '{fmt}' (use json or csv)")
    if not entries:
        raise ValueError("The cast file has no characters")
    if len(entries) > MAX_CAST:
        raise ValueError(f"A cast file can hold up to {MAX_CAST} characters (this one has {len(entries)})")
    return entries


def _split_name(value):
    parts = str(value).split()
    if len(parts) == 1:
        return parts[0], ""
    return " ".join(parts[:-1]), parts[-1]


def entry_fields(entry, position=1):
    """Split one raw entry into (locked paths, control settings, custom data, warnings)."""
    locked, control, custom, warnings = {}, {}, {}, []
    label = f"Entry {position}"
    for raw_key, value in entry.items():
        if value is None or (isinstance(value, str) and not value.strip()):
            continue
        key = re.sub(r"[\s\-]+", "_", str(raw_key).strip().lower())
        if key in NAME_KEYS:
            first, last = _split_name(value)
            locked.setdefault("profile.first_name", first)
            if last:
                locked.setdefault("profile.last_name", last)
        elif key in CONTROL_KEYS:
            control[key] = value
        elif key in _LOOKUP:
            locked[_LOOKUP[key]] = value
        else:
            custom[str(raw_key)] = value

    if "profile.gender" in locked:
        gender = _GENDERS.get(str(locked["profile.gender"]).strip().lower())
        if gender:
            locked["profile.gender"] = gender
        else:
            warnings.append(f"{label}: gender '{locked.pop('profile.gender')}' not recognised, rolled instead")
    if "profile.age" in locked:
        digits = re.search(r"\d+", str(locked["profile.age"]))
        if digits:
            locked["profile.age"] = int(digits.group())
        else:
            warnings.append(f"{label}: age '{locked.pop('profile.age')}' is not a number, rolled instead")
    for path in ("profile.personality", "profile.hobbies"):
        if path in locked:
            v = locked[path]
            locked[path] = [s.strip() for s in re.split(r"[;|,]", v) if s.strip()] if isinstance(v, str) else roll.as_list(v)
    for path, value in list(locked.items()):
        if path in ("profile.personality", "profile.hobbies", "profile.age"):
            continue
        if path in OPTIONAL_PATHS and str(value).strip().lower() in NOTHING_WORDS:
            locked[path] = ""
        elif isinstance(value, dict):
            locked[path] = json.dumps(value)
        elif isinstance(value, list):
            locked[path] = ", ".join(str(v) for v in value)
        else:
            locked[path] = str(value).strip()
    return locked, control, custom, warnings


def build_drafts(data, entries, setting_id, style_id, constraints=None):
    """Turn raw entries into NPC drafts. Returns (drafts, warnings)."""
    drafts, warnings = [], []
    for position, entry in enumerate(entries, 1):
        locked, control, custom, warn = entry_fields(entry, position)
        warnings += warn
        sid = control.get("setting")
        if sid is not None and sid not in data["settings"]:
            warnings.append(f"Entry {position}: unknown setting '{sid}', using {setting_id}")
        sid = sid if sid in data["settings"] else setting_id
        style = control.get("style")
        if style is not None and style not in data["styles"]:
            warnings.append(f"Entry {position}: unknown style '{style}', using {style_id}")
        style = style if style in data["styles"] else style_id

        extra = locked.pop("appearance.extra", None)
        has_looks = any(path.startswith("appearance.") for path in locked)
        mode = str(control.get("mode", "")).strip().lower()
        freeform = mode == "freeform" or (mode != "structured" and bool(extra) and not has_looks)
        if mode == "freeform" and not extra:
            warnings.append(f"Entry {position}: mode 'freeform' needs a description; rolled a normal NPC instead")
            freeform = False
        npc = roll.roll_npc(data, sid, constraints, locked, freeform=extra if freeform else None)
        requested_body_plan = control.get("morphology")
        if requested_body_plan is not None:
            body_plan = morphology.clean(str(requested_body_plan))
            if body_plan:
                npc["morphology"] = body_plan
            else:
                warnings.append(f"Entry {position}: unknown body plan '{requested_body_plan}', inferred from its species")
        if extra and not freeform:
            npc["appearance"]["extra"] = str(extra).strip()
        npc["style"] = style
        if control.get("seed") not in (None, ""):
            try:
                npc["seed"] = int(control["seed"]) % (2 ** 32)
            except (TypeError, ValueError):
                warnings.append(f"Entry {position}: seed '{control['seed']}' is not a number, ignored")
        npc["custom"] = roll.clean_custom(custom)
        drafts.append(npc)
    return drafts, warnings
