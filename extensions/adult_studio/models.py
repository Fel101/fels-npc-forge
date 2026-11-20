"""The rules of the Models setting, checked by NPC Forge before any Models character is saved, rolled or drawn.

Models are for the Altera companion app, so they follow Altera's hard rules (safety.py is a copy of Altera's): 18 or older, no minor or
childlike detail, no real person's likeness, nothing non-consensual. The setting file already keeps rolled characters inside
that (min_age, female only, its own tables), but a person can edit any field or describe a character in their own words, and
the words must pass too.
"""
from . import safety

SETTING = "models"


def problem(npc):
    """Why this character cannot be a Model, or None. Characters of other settings are not this module's business."""
    if not isinstance(npc, dict) or npc.get("setting") != SETTING:
        return None
    profile, appearance = npc.get("profile") or {}, npc.get("appearance") or {}
    return safety.card_problem({"age": profile.get("age"),
                                "profile": {k: v for k, v in profile.items() if k != "age"}, "appearance": appearance})
