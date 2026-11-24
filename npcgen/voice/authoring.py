"""Saved performances and pronunciation, independent of authored dialogue/captions.

The control table describes the installed Forge adapters, not every theoretical
model feature. Hash only the effective synthesis inputs so unrelated dictionary
edits do not invalidate an otherwise identical line.
"""
import copy
import hashlib
import json
import math
import re

from . import alignment, effects, engines, markup

KEY = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
LIMITS = {"temperature": (.3, 1.3, .9), "cfg": (1., 8., 4.), "speed": (.5, 2., 1.),
          "cfg_value": (.1, 10., 2.), "inference_timesteps": (1, 100, 10),
          "exaggeration": (0., 2., .5), "cfg_weight": (0., 1., .5), "top_p": (.05, 1., 1.),
          "min_p": (0., 1., .05), "repetition_penalty": (.5, 2., 1.2), "top_k": (0, 200, 50), "max_new_tokens": (8, 2048, 512),
          "cfg_scale": (1., 5., 2.5), "stg_scale": (0., 3., 1.5), "duration_multiplier": (.8, 1.5, 1.1),
          'max_seconds':(.5,95.1,47.6)}
CONTROLS = {"qwen": ["temperature"], "breeze": ["temperature", "cfg"], "kokoro": ["speed"], "chatterbox": [],
            "voxcpm2": ["cfg_value", "inference_timesteps"],
            "qwen_custom": ["temperature", "top_k", "top_p", "repetition_penalty", "max_new_tokens"],
            "chatterbox_original": ["temperature", "exaggeration", "cfg_weight", "top_p", "min_p", "repetition_penalty"],
            "dramabox": ["cfg_scale", "stg_scale", "duration_multiplier"],
            'drama3':['temperature','top_k','top_p','max_seconds']}


def capabilities(engine_id, directed=False):
    keys = list(CONTROLS.get(engine_id, ["temperature"] if engine_id == "fake" else []))
    if engine_id == "breeze" and not directed:
        keys.remove("cfg")  # The plain clone always uses CFG 1.
    limits = {key: list(LIMITS[key]) for key in keys}
    if engine_id == 'drama3':
        limits['temperature']=[.05,2.,1.]
        limits['top_k']=[1,4097,30]
        limits['top_p']=[.05,1.,.9]
    if engine_id in ("qwen_custom", "chatterbox_original"):
        limits["temperature"] = [.05, 2., .9 if engine_id == "qwen_custom" else .8]
    return {"direction": engine_id in ("breeze", "fake", "qwen_custom", "dramabox",'drama3'), "parameters": keys,
            "limits": limits, "integer_parameters": [k for k in keys if k in ("inference_timesteps", "top_k", "max_new_tokens")]}


def _text(value, label, limit, required=False):
    if not isinstance(value, str) or len(value) > limit or (required and not value.strip()):
        raise ValueError(f"{label} must be {'nonempty ' if required else ''}text, at most {limit} characters")
    return value.strip()


def performance(raw):
    if not isinstance(raw, dict) or set(raw) - {"direction", "parameters", "effects"}:
        raise ValueError("Performance accepts direction, parameters and delivery effects only")
    direction = _text(raw.get("direction", ""), "Acting direction", 300)
    params = raw.get("parameters", {})
    if not isinstance(params, dict) or set(params) - set(LIMITS):
        raise ValueError("Unknown performance parameter")
    clean = {}
    for key, value in params.items():
        lo, hi, _ = LIMITS[key]
        if key == "temperature":
            lo, hi = .05, 2.
        if key == 'top_k':
            hi=4097
        if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or not lo <= value <= hi:
            raise ValueError(f"{key} must be a finite number from {lo:g} to {hi:g}")
        if key in ("inference_timesteps", "top_k", "max_new_tokens") and int(value) != value:
            raise ValueError(f"{key} must be a whole number")
        clean[key] = float(value)
    return {"direction": direction, "parameters": clean, **({"effects": effects.clean(raw["effects"])} if "effects" in raw else {})}


def validate_performance(raw, engine_id, *, lab_voice=None):
    clean = performance(raw)
    caps = capabilities(engine_id, bool(clean["direction"]))
    if lab_voice:
        caps = {"direction": lab_voice["takes_direction"], "parameters": ["speed"], "limits": {"speed": LIMITS["speed"]}}
    if clean["direction"] and not caps["direction"]:
        raise ValueError(f"{engine_id} does not support acting direction for this voice")
    unsupported = set(clean["parameters"]) - set(caps["parameters"])
    if unsupported:
        raise ValueError(f"{engine_id} does not apply {', '.join(sorted(unsupported))} for this performance")
    for key, value in clean["parameters"].items():
        lo, hi, _ = caps["limits"][key]
        if not lo <= value <= hi:
            raise ValueError(f"{key} must be between {lo:g} and {hi:g} for {engine_id}")
    return clean


def clean_studio(raw):
    if not isinstance(raw, dict) or set(raw) - {"pronunciations", "presets", "default_performance", "line_performances", "effects", "lip_sync"}:
        raise ValueError("Studio settings must contain pronunciations, presets, default_performance or line_performances")
    entries = raw.get("pronunciations", [])
    if not isinstance(entries, list) or len(entries) > 200:
        raise ValueError("Use at most 200 pronunciation rules")
    rules, known = [], set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("Pronunciation rules must be objects")
        term = _text(entry.get("term"), "Written term", 100, True)
        spoken = _text(entry.get("spoken"), "Spoken spelling", 150, True)
        if any(c in term + spoken for c in "[]()\n\r") or term.casefold() in known:
            raise ValueError("Use unique written terms and plain spoken spellings without sound tags or newlines")
        known.add(term.casefold())
        rules.append({"term": term, "spoken": spoken})
    presets = raw.get("presets", [])
    if not isinstance(presets, list) or len(presets) > 30:
        raise ValueError("Use at most 30 performance presets")
    out, ids = [], set()
    for preset in presets:
        if not isinstance(preset, dict) or not isinstance(preset.get("id"), str) or not KEY.fullmatch(preset["id"]) or preset["id"] in ids:
            raise ValueError("Performance preset IDs must be unique letters, numbers, underscores or hyphens")
        ids.add(preset["id"])
        out.append({"id": preset["id"], "name": _text(preset.get("name"), "Preset name", 80, True),
                    **performance({k: preset[k] for k in ("direction", "parameters", "effects") if k in preset})})
    default = raw.get("default_performance", "")
    lines = raw.get("line_performances", {})
    if not isinstance(default, str) or (default and default not in ids):
        raise ValueError("Choose a saved default performance")
    if not isinstance(lines, dict) or len(lines) > 1000 or any(not isinstance(key, str) or not KEY.fullmatch(key)
            or not isinstance(value, str) or (value and value not in ids) for key, value in lines.items()):
        raise ValueError("Choose saved performances for valid dialogue line IDs")
    return {"pronunciations": rules, "presets": out, "default_performance": default, "line_performances": copy.deepcopy(lines),
            **({"effects": effects.clean(raw["effects"])} if "effects" in raw else {}),
            **({"lip_sync": alignment.clean(raw["lip_sync"])} if "lip_sync" in raw else {})}


def apply_pronunciation(text, v):
    rules = (v.get("studio") or {}).get("pronunciations") or []
    if not rules:
        return text, []
    # One substitution pass, longest phrases first. Never rewrite a sound tag.
    rules = sorted(rules, key=lambda rule: len(rule["term"]), reverse=True)
    pattern = re.compile(r"(?<!\w)(?:" + "|".join(f"(?P<rule{i}>{re.escape(rule['term'])})" for i, rule in enumerate(rules)) + r")(?!\w)", re.IGNORECASE)
    used = []
    def substitute(match):
        rule = rules[int(match.lastgroup[4:])]
        if rule not in used:
            used.append(rule)
        return rule["spoken"]
    from . import record
    result = markup.transform_words(text, lambda part: pattern.sub(substitute, part),record.speaker_for(v))
    return result, copy.deepcopy(used)


def resolve_performance(v, line_id=None, preset_id=None, override=None):
    studio = v.get("studio") or {}
    if preset_id is None:
        preset_id = (studio.get("line_performances") or {}).get(line_id, studio.get("default_performance", ""))
    found = next((p for p in studio.get("presets", []) if p["id"] == preset_id), None)
    if preset_id and not found:
        raise ValueError("The saved performance no longer exists")
    clean = {k: found[k] for k in ("direction", "parameters", "effects") if k in found} if found else {"direction": "", "parameters": {}}
    if override is not None:
        clean = performance(override)
    return copy.deepcopy(clean)


def prepare(v, text, mood="neutral", line_id=None, preset_id=None, override=None):
    from . import record
    speaker = record.speaker_for(v)
    perf = resolve_performance(v, line_id, preset_id, override)
    lab_voice = v.get("lab_voice")
    if lab_voice:
        perf["parameters"] = {key: value for key, value in perf.get("parameters", {}).items() if key == "speed"}
        if not lab_voice["takes_direction"]:
            perf["direction"] = ""
    perf = validate_performance(perf, speaker, lab_voice=lab_voice)
    effective = copy.deepcopy(v)
    caps = capabilities(speaker, bool(perf["direction"] or (mood != "neutral" and speaker == "breeze")))
    effective["settings"] = {key: (v.get("settings") or {}).get(key, caps["limits"][key][2]) for key in caps["parameters"]}
    if lab_voice:
        effective["settings"] = {"speed": v["settings"]["speed"]} if "speed" in v.get("settings", {}) else {}
    effective["settings"].update(perf["parameters"])
    if perf["direction"]:
        effective["acting_direction"] = perf["direction"]
    else:
        effective.pop("acting_direction", None)
    spoken, applied = apply_pronunciation(text, v)
    ref = {} if record.is_preset(v) else v.get("reference") or {}
    source = {"speaker": speaker, "engine": v.get("engine"), "mode": v.get("mode"), "language": v.get("language", "English"),
              "stock_voice": v.get("stock_voice"), "reference": {k: ref.get(k) for k in ("id", "text")},
              "parameters": effective["settings"], "direction": perf["direction"],
              "mood": mood if engines.can(speaker, "direction") else "neutral", "synthesis_text": markup.for_engine(spoken, speaker)}
    if record.is_preset(v):
        source["preset_identity"] = v.get("preset_identity")
    if lab_voice:
        source = {key: source[key] for key in ("parameters", "direction", "synthesis_text")}
        source.update(voice=record.identity_id(v), model=lab_voice["model"], lab_id=lab_voice.get("lab_id"),
                      mood=mood if lab_voice["takes_direction"] else "neutral")
    elif not ref and v.get("mode") == "design":
        source["design_instruction"] = record.instruction(v)
    signature = hashlib.sha256(json.dumps(source, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    delivery = effects.clean(perf.get("effects", (v.get("studio") or {}).get("effects", {})))
    effective["delivery_effects"] = delivery
    active = effects.effective(delivery)
    render_signature = signature
    if active:
        render_signature = hashlib.sha256(json.dumps({"synthesis_signature": signature, "effects": active, "effects_schema": effects.VERSION}, sort_keys=True).encode()).hexdigest()
    return effective, spoken, {"synthesis_text": source["synthesis_text"], "pronunciations_applied": applied,
                              "performance": perf, "render_signature": render_signature, "synthesis_signature": signature, "effects": active}


def line_stale(npc, line_id, row, meta):
    from . import record
    try:
        _, _, current = prepare(record.voice_of(npc), row["text"], meta.get("mood", "neutral"), line_id)
        previous = meta.get("render_signature")
        if not previous and meta.get("voice_snapshot"):
            _, _, original = prepare(meta["voice_snapshot"], meta["text"], meta.get("mood", "neutral"), line_id)
            previous = original["render_signature"]
        if previous:
            return current["render_signature"] != previous
        # Older clips have no settings snapshot. Preserve the existing freshness
        # rules, but never present their unpronounced/unperformed audio as new work.
        return (current["synthesis_text"] != markup.for_engine(meta["text"], record.speaker_for(record.voice_of(npc)))
                or bool(current["performance"]["direction"] or current["performance"]["parameters"] or current["effects"]))
    except (ValueError, KeyError, TypeError):
        return True


def annotate(npc):
    """Client freshness flag, derived on read; never rewrite legacy stored metadata."""
    from . import record
    for line_id, meta in (npc.get("voice_lines") or {}).items():
        meta["stale"] = record.line_is_stale(npc, line_id)
        meta["lip_sync_stale"] = alignment.is_stale(meta, npc.get('voice') or {})
    from . import audition
    return audition.annotate(npc)
