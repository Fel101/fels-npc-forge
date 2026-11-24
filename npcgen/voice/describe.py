"""The voice a character should have, written as the plain-English description the design models read.

describe(npc) builds it from the character sheet: who they are (real age, gender, job), their traits, how they talk and come
across (profile.speech / profile.demeanor), and cues from the setting (a western drawl), the race (a wolf's faint growl) and
the art style (a chibi's small, bright voice). compose() adds the voice-type preset and chips picked in the designer.

The phrases are written without pronouns ("Speaks...", "Comes across as..."), so they read right for anyone. An adult is always
described as an adult: art-style cues for chibi or anime adults say so outright, and adult_problem() refuses an edited
description that would make an adult's voice a child's. A child character (the general settings allow them) is described at
their real age.
"""
import json
import re

from .. import config as cfgmod
from . import engines

MAX_DESCRIPTION = 420            # the design models read an instruction, not an essay; Breeze counts it against its context
MAX_INSTRUCTION = 700            # description + preset + chips
_DATA = None


def load_data():
    global _DATA
    if _DATA is None:
        _DATA = json.loads((cfgmod.ROOT / "data" / "voices.json").read_text("utf-8"))
    return _DATA


def _age(npc):
    try:
        return int((npc.get("profile") or {}).get("age"))
    except (TypeError, ValueError):
        return None


def is_adult(npc):
    """18 or older. A sheet without a usable age counts as an adult only if its age group is an adult one."""
    age = _age(npc)
    if age is not None:
        return age >= 18
    return (npc.get("profile") or {}).get("age_group") not in ("child", "teen")


def _noun(gender, age):
    if age is not None and age < 13:
        return {"female": "girl", "male": "boy"}.get(gender, "child")
    if age is not None and age < 18:
        return {"female": "teenage girl", "male": "teenage boy"}.get(gender, "teenager")
    return {"female": "woman", "male": "man"}.get(gender, "adult")


def _a(phrase):
    """"A ..." or "An ..." for a phrase (8-year-old and 18-year-old say "an")."""
    phrase = phrase.strip()
    vowel = re.match(r"(?i)(8|11|18|80|8\d)\b|[aeiou](?!n[ei])|hour|honest", phrase)
    return ("An " if vowel else "A ") + phrase


def _join(items):
    items = [i for i in items if i]
    if len(items) < 2:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _sentence(text):
    text = re.sub(r"\s+", " ", str(text or "")).strip().rstrip(".")
    return text[:1].upper() + text[1:] + "." if text else ""


def _race_phrase(npc, data):
    p = npc.get("profile") or {}
    words = " ".join(str(x or "") for x in (p.get("race"), npc.get("morphology"))).lower().replace("-", "_")
    tokens = set(re.split(r"[^a-z_]+", words)) | set(re.split(r"[^a-z]+", words))
    for keys, phrase in data["race_voice"]:
        if any(k in tokens for k in keys):
            return phrase
    return ""


def describe(npc):
    """The suggested voice description for a character (at most MAX_DESCRIPTION characters)."""
    data = load_data()
    p = npc.get("profile") or {}
    age, adult = _age(npc), is_adult(npc)
    who = _a(f"{age}-year-old {_noun(p.get('gender'), age)}") if age is not None else _a(_noun(p.get("gender"), 30))
    job = str(p.get("occupation") or "").strip()
    if job:
        who += ", " + (job if re.match(r"(?i)^(a|an|the)\s", job) else _a(job)[:1].lower() + _a(job)[1:])
    traits = [str(t).strip() for t in (p.get("personality") or []) if str(t).strip()][:3]
    style = (data["style_voice"].get(npc.get("style") or "") or "").replace("{adult}", ", still clearly an adult" if adult else "")
    parts = [                                             # in order of importance: the tail is dropped first when it runs long
        _sentence(who),
        style,
        _race_phrase(npc, data),
        data["setting_voice"].get(npc.get("setting") or "", ""),
        _sentence(_join(traits)) if traits else "",
        _sentence("Speaks " + p["speech"]) if p.get("speech") else "",
        _sentence("Comes across as " + p["demeanor"]) if p.get("demeanor") else "",
    ]
    out = ""
    for part in parts:
        if part and len(out) + len(part) + 1 <= MAX_DESCRIPTION:
            out = f"{out} {part}".strip()
    return out


def presets():
    """What the designer offers: voice types and chip groups."""
    data = load_data()
    return {"voice_types": data["voice_types"], "chip_groups": data["chip_groups"], "moods": list(engines.MOODS),
            "tags": engines.TAGS, "setting_accents": data.get("setting_accents", []),
            "setting_voice": {k: data["setting_voice"][k] for k in data.get("setting_accents", []) if k in data["setting_voice"]}}


def compose(description, voice_type=None, chips=None):
    """The instruction a design model reads: the accent chip first, then the description, the voice type and the other chips.

    The accent leads because Qwen3-TTS ignored "British accent." as the last sentence of a long description (2026-09-24, 12
    takes of one character, no accent in any). And with an accent chip picked, nothing may contradict it: a preset's accent
    (Valley Girl's "California upspeak", Pirate's "West Country burr") gives way to its accent_free text, and a setting's
    accent cue ("A light Old West drawl.") is taken out of the description."""
    data = load_data()
    chips = chips or {}
    picked = []
    for group in data["chip_groups"]:
        chip = next((c for c in group["chips"] if c["id"] == chips.get(group["id"])), None)
        if chip:
            picked.append((group["id"], chip["text"]))
    accent = next((text for gid, text in picked if gid == "accent"), "")
    desc = re.sub(r"\s+", " ", str(description or "")).strip()
    if accent:
        for setting in data.get("setting_accents", []):
            cue = data["setting_voice"].get(setting, "")
            if cue:
                desc = re.sub(r"\s+", " ", desc.replace(cue, "")).strip()
    parts = [accent, desc]
    vt = next((v for v in data["voice_types"] if v["id"] == voice_type), None)
    if vt:
        parts.append(vt.get("accent_free", vt["text"]) if accent else vt["text"])
    parts.extend(text for gid, text in picked if gid != "accent")
    out = ""
    for part in (p for p in parts if p):                  # whole sentences only: what does not fit is left out, not cut
        if out and len(out) + 1 + len(part) > MAX_INSTRUCTION:
            continue
        out = f"{out} {part}".strip()
    return out[:MAX_INSTRUCTION].strip()


def with_mood(instruction, mood):
    """A design instruction with a mood for one line."""
    words = engines.MOODS.get(mood or "neutral") or ""
    return f"{instruction} Sounds {words}." if words else instruction


def adult_problem(npc, text):
    """A reason to refuse `text` as an adult character's voice (it describes a child), or None."""
    if not is_adult(npc):
        return None
    lowered = str(text or "").lower()
    job = str((npc.get("profile") or {}).get("occupation") or "").strip().lower()
    if job:                                               # the job comes from the sheet ("street kid runner" at 19), not from what was typed
        lowered = lowered.replace(job, " ")
    for word in load_data()["adult_only_words"]:
        if re.search(r"(?<![a-z])" + re.escape(word) + r"(?![a-z])", lowered):
            return (f"This character is an adult, so the voice is described as an adult's: remove \"{word}\". For a small, cute "
                    "voice, words like small, bright, high or squeaky work.")
    for match in re.finditer(r"(\d{1,2})[\s-]*(?:years?|yrs?)[\s-]*old", lowered):
        if int(match.group(1)) < 18:
            return f"This character is an adult, so the voice is described as an adult's, not as {match.group(0)}."
    return None


def mood_problem(npc, mood):
    if mood == "flirty" and not is_adult(npc):
        return "The flirty mood is only for adult characters."
    if mood and mood not in engines.MOODS:
        return f"Unknown mood: {mood}"
    return None


def take_script(npc, language="English"):
    """What a take reads: a short, friendly self-introduction in the voice's language (about 8-10 s)."""
    scripts = load_data()["take_scripts"]
    name = ((npc.get("profile") or {}).get("first_name") or "").strip() or "Sam"
    return scripts.get(language, scripts["English"]).replace("{name}", name)


def stock_match(npc, engine_id="kokoro", cfg=None):
    """Stock voices ordered best match first: the character's gender, then British for a British-sounding sheet, then grade."""
    voices = engines.stock_voices(engine_id)
    p = npc.get("profile") or {}
    gender = p.get("gender")
    british = bool(re.search(r"(?i)british|english|scottish|welsh|irish", str(p.get("heritage") or ""))) or \
        npc.get("setting") == "steampunk"
    grades = ["A", "A-", "B+", "B", "B-", "C+", "C", "C-", "D+", "D", "D-", "F+", "F"]

    def score(v):
        return ((0 if v["gender"] == gender or gender not in ("female", "male") else 1),
                (0 if (v["accent"] == "British") == british else 1),
                grades.index(v["grade"]) if v["grade"] in grades else 99)
    return [v["id"] for v in sorted(voices, key=score)]
