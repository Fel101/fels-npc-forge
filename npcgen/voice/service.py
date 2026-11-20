"""Turn the voice designer's requests into job requests (the "voice" entry of a job's assets; see Jobs.plan) and handle the
two voices that need no model: an uploaded recording and a copy of another character's voice. Raises ValueError with a
readable message, like npcgen/service.py.
"""
import base64
import binascii
import copy
import re

from ..library import now
from . import audio, authoring, describe, engines, record

MAX_TAKES = 4
MAX_TEST_CHARS = 400
_DATA_URL = re.compile(r"^data:audio/[a-zA-Z0-9.+-]+;base64,")


def _count(raw, key, lo, hi, default):
    value = raw.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError(f"{key} must be a number")
    try:
        value = int(value)
    except ValueError:
        raise ValueError(f"{key} must be a number") from None
    return max(lo, min(hi, value))


def takes_request(npc, raw, cfg=None):
    """{"takes": n, ...} for Jobs.plan: `n` candidate voices from the saved description or stock voices."""
    raw = raw if isinstance(raw, dict) else {}
    v = record.voice_of(npc)
    n = _count(raw, "count", 1, MAX_TAKES, 3)
    if v["mode"] not in ("design", "stock"):
        raise ValueError("Takes come from a description or a stock voice. An uploaded or copied voice is used as it is.")
    req = {"takes": n, "engine": v["engine"], "mode": v["mode"], "language": v["language"], "settings": v["settings"],
           "script": v.get("script") or describe.take_script(npc, v["language"]), "seed_base": record.new_seed(),
           "auto_pick": bool(raw.get("auto_pick")), "quick": not raw.get("batch")}
    req["voice_snapshot"] = record.render_snapshot(v)
    if v["mode"] == "design":
        req["instruction"] = record.instruction(v)
        if not req["instruction"]:
            raise ValueError("Describe the voice first (or press Suggest).")
        problem = describe.adult_problem(npc, req["instruction"])
        if problem:
            raise ValueError(problem)
    else:
        if v["engine"] == "qwen_custom":
            if not v.get("stock_voice"):
                raise ValueError("Choose a CustomVoice preset speaker")
            req["stock_order"] = [v["stock_voice"]] * n
            return req
        order = describe.stock_match(npc, v["engine"],cfg)
        if cfg is not None and v.get('stock_voice') and v['stock_voice'] not in order:
            raise ValueError('The selected stock voice has no embedding in the cached checkpoint')
        if v.get("stock_voice") in order:            # the voice you picked first, then the next best matches
            order.remove(v["stock_voice"])
            order.insert(0, v["stock_voice"])
        req["stock_order"] = order[:n]
    return req


def test_request(npc, raw):
    """{"test": {...}} for Jobs.plan: one line (or a mood preview) in the current voice."""
    raw = raw if isinstance(raw, dict) else {}
    mood = raw.get("mood") or "neutral"
    problem = describe.mood_problem(npc, mood)
    if problem:
        raise ValueError(problem)
    text = str(raw.get("text") or "").strip()
    if not text:
        text = describe.take_script(npc, record.voice_of(npc)["language"])
    if len(text) > MAX_TEST_CHARS:
        raise ValueError(f"Keep a test line under {MAX_TEST_CHARS} characters")
    key = "try" if not raw.get("preview") else f"mood_{mood}"
    label = "Try a line" if key == "try" else f"Mood preview: {mood}"
    v = record.voice_of(npc)
    perf = authoring.resolve_performance(v, preset_id=raw.get("performance_id"), override=raw.get("performance"))
    authoring.prepare(v, text, mood, override=perf)
    return {"test": {"key": key, "text": text, "mood": mood, "label": label, "performance": perf}, "quick": True,
            "voice_snapshot": record.render_snapshot(record.voice_of(npc))}


def lines_request(npc, raw):
    """{"lines": [ids], "moods": {id: mood}} for Jobs.plan: voice these dialogue lines (all of them when none are named)."""
    raw = raw if isinstance(raw, dict) else {}
    pack = npc.get("dialogue") or {}
    known = [row["id"] for rows in (pack.get("lines") or {}).values() for row in rows]
    if not known:
        raise ValueError("This character has no dialogue lines yet: generate the dialogue pack first.")
    ids = raw.get("ids")
    if ids is None:
        ids = known
    if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
        raise ValueError("ids must be a list of line ids")
    missing = [i for i in ids if i not in known]
    if missing:
        raise ValueError(f"No such dialogue line: {', '.join(missing[:3])}")
    moods = raw.get("moods") or {}
    if not isinstance(moods, dict):
        raise ValueError("moods must be an object")
    for line_id, mood in moods.items():
        problem = describe.mood_problem(npc, mood)
        if problem:
            raise ValueError(problem)
    v = record.voice_of(npc)
    if not v.get("reference") and not (v["mode"] == "stock" and v.get("stock_voice")):
        raise ValueError("Pick a voice for this character first (make takes and choose one).")
    inputs = {}
    for category, rows in (pack.get("lines") or {}).items():
        for row in rows:
            if row["id"] not in ids:
                continue
            mood = moods.get(row["id"]) or (npc.get("voice_lines") or {}).get(row["id"], {}).get("mood") or "neutral"
            authoring.prepare(v, row["text"], mood, row["id"])
            inputs[row["id"]] = {"text": row["text"], "category": category, "mood": mood}
    return {"lines": list(dict.fromkeys(ids)), "moods": {k: v for k, v in moods.items() if k in ids},
            "voice_snapshot": record.render_snapshot(v), "line_inputs": inputs}


def decode_upload(raw_audio):
    """Browser-converted WAV (data URL or bare base64) -> WAV bytes."""
    if not isinstance(raw_audio, str) or not raw_audio.strip():
        raise ValueError("No recording was sent")
    try:
        data = base64.b64decode(_DATA_URL.sub("", raw_audio.strip()), validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("The recording is not valid base64") from None
    return data


def apply_upload(lib, npc, body):
    """Use an uploaded recording as the character's voice. Needs the words it says and your confirmation of consent."""
    if body.get("consent") is not True:
        raise ValueError("Confirm that you have the right to use this voice (the speaker agreed, or it is your own).")
    text = str(body.get("text") or "").strip()
    if len(text) < 3:
        raise ValueError("Type the exact words the recording says: the models copy a voice far better with them.")
    if len(text) > 600:
        raise ValueError("Keep the transcript under 600 characters (a clip of 40 seconds or less)")
    wav, seconds = audio.check_upload(decode_upload(body.get("audio")))
    speaker = body.get("speaker") or engines.DEFAULT_ENGINE
    if not engines.can(speaker, "clone"):
        raise ValueError(f"{speaker} cannot speak in a copied voice")

    def apply(rec):
        v = record.voice_of(rec)
        if record.is_preset(v):
            v["stock_voice"] = None
        v.update(mode="upload", engine=speaker, speaker=speaker, review="picked", chosen_take=None)
        v.pop("preset_identity", None)
        v.pop("library_binding", None)
        record.install_reference(lib, rec, v, wav, {"text": text, "seconds": seconds, "source": "upload",
            "engine": None, "language": v.get("language") or "English", "created": now(), "consent": True,
            "id": record.new_reference_id()})
        v["commercial_ok"] = record.commercial_ok(v)
        rec["voice"] = v
    return lib.update(npc["id"], apply)


def apply_copy(lib, npc, source):
    """Give the character another character's voice (the same reference clip and speaking engine)."""
    sv = record.voice_of(source)
    if record.is_preset(sv):
        snapshot = record.render_snapshot(sv)
        def apply_preset(rec):
            clean = record.clean_settings(snapshot, rec)
            v = record.save_settings(rec, clean, lib)
            v["studio"] = copy.deepcopy(sv.get("studio") or {})
            v["copy_source"] = source["id"]
            v.pop("library_binding", None)
        return lib.update(npc["id"], apply_preset)
    ref = sv.get("reference")
    if not ref:
        raise ValueError("That character has no voice to copy yet.")
    data = lib.read_asset(source["id"], ref["file"])

    def apply(rec):
        v = record.voice_of(rec)
        for key in ("engine", "speaker", "description", "voice_type", "chips", "language", "stock_voice", "settings"):
            v[key] = sv.get(key)
        v.update(mode="copy", review="picked", chosen_take=None)
        v.pop("preset_identity", None)
        v.pop("library_binding", None)
        record.install_reference(lib, rec, v, data, {**ref, "source": f"copy:{source['id']}", "created": now(),
                                 "id": record.new_reference_id()})
        v["commercial_ok"] = record.commercial_ok(v)
        rec["voice"] = v
    return lib.update(npc["id"], apply)
