"""Body-plan classification and the generation support boundary."""

MORPHOLOGIES = {
    "bipedal": {
        "label": "Bipedal",
        "full_body_supported": True,
        "validation": "gpu_sampled",
        "description": "The current full-body, animation, and model-sheet pipeline targets two-legged bodies. Representative GPU samples exist, but quality varies by style and setting; individual combinations still need review.",
    },
    "taur": {
        "label": "Taur",
        "full_body_supported": False,
        "validation": "known_failure",
        "description": "Test runs on the GPU gave human legs or an extra animal head. A drawn figure used as a guide made the leg count better, but still gave stiff anatomy and clothes on animal legs. Full-body sprites, animations, model sheets and 3D models stay blocked until a checked method is ready for production.",
    },
    "quadruped": {
        "label": "Quadruped",
        "full_body_supported": False,
        "validation": "unsupported",
        "description": "There is no checked method for full-body characters on four legs yet.",
    },
    "merfolk": {
        "label": "Merfolk",
        "full_body_supported": False,
        "validation": "unsupported",
        "description": "A fish tail in place of legs: there is no walk to animate, and no method for full-body characters has been checked for it yet.",
    },
    "serpentine": {
        "label": "Serpentine",
        "full_body_supported": False,
        "validation": "unsupported",
        "description": "There is no checked method for full-body serpentine characters yet.",
    },
    "other": {
        "label": "Other",
        "full_body_supported": False,
        "validation": "unsupported",
        "description": "There is no checked method for full-body characters with this body plan yet.",
    },
    "unspecified": {
        "label": "Unspecified",
        "full_body_supported": False,
        "validation": "needs_review",
        "description": "Choose a body plan before requesting full-body assets.",
    },
}

_ALIASES = {
    "biped": "bipedal", "two-legged": "bipedal", "humanoid": "bipedal",
    "quadrupedal": "quadruped", "four-legged": "quadruped", "centaur": "taur",
    "mermaid": "merfolk", "merman": "merfolk", "fish-tail": "merfolk",
    "unknown": "unspecified", "": "unspecified",
}


def clean(value):
    """Return a canonical morphology ID, or None when the value is unknown."""
    if not isinstance(value, str):
        return None
    value = value.strip().casefold().replace("_", "-")
    value = _ALIASES.get(value, value)
    return value if value in MORPHOLOGIES else None


def from_race(data, setting_id, race_value):
    """Classify a table-backed race by id, label, or phrase; unannotated races default to bipedal."""
    setting = data.get("settings", {}).get(setting_id, {})
    races = setting.get("races") or {}
    race_text = str(race_value or "").strip().casefold()
    if not race_text:
        return None
    for race_id, race in races.items():
        if race_text in {race_id.casefold(), str(race.get("label", "")).casefold(), str(race.get("phrase", "")).casefold()}:
            value = clean(race.get("morphology", "bipedal"))
            return value or "unspecified"
    return None


def infer(npc, data):
    """Infer a safe body plan for legacy records that have no explicit morphology field."""
    if npc.get("freeform"):
        return "unspecified"
    profile = npc.get("profile") or {}
    setting_id = npc.get("setting")
    race = from_race(data, setting_id, profile.get("race"))
    if race:
        return race
    setting = data.get("settings", {}).get(setting_id, {})
    if setting.get("races"):
        return "unspecified" if profile.get("race") else "bipedal"
    return "bipedal"


def resolve(npc, data):
    """Return a stored canonical value when valid, otherwise infer it from the profile and setting tables."""
    return clean(npc.get("morphology")) or infer(npc, data)


def needs_full_body(assets):
    return bool(assets.get("sprite") or assets.get("animations") or assets.get("sheet") or assets.get("model_3d") or assets.get("best_3d")
                or assets.get("body_3d"))


def unsupported_reason(value, assets):
    if not needs_full_body(assets):
        return None
    value = clean(value) or "unspecified"
    info = MORPHOLOGIES[value]
    if info["full_body_supported"]:
        return None
    return f'{info["label"]} morphology: {info["description"]} Portraits and expressions are still available.'
