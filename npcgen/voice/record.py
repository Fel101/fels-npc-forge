"""A character's voice as stored in npc.json ("voice", and "voice_lines" for voiced dialogue), and the checks on what the
designer sends.

  voice = {
    "engine": "qwen",            the model that makes the voice (designs it, or supplies the stock voice)
    "speaker": "qwen",           the model that speaks lines in the saved voice (None: chosen from engine and mode)
    "mode": "design",            design | stock | upload | copy
    "description": "...",        what the design model reads (editable); "auto_description" is the last suggestion
    "edited": false,             the description was changed by hand
    "voice_type": "valley_girl", "chips": {"pitch": "high", ...},     the designer's presets (see data/voices.json)
    "language": "English", "stock_voice": "af_heart", "settings": {"temperature", "cfg", "speed"}, "script": "...",
    "reference": {"file": "voice/reference.wav", "text", "seconds", "source": "take:<id>|upload|copy:<npc id>", "engine",
                  "created", "id"},    the chosen voice: every line is spoken by copying it
    "takes": [{"id", "n", "file", "engine", "mode", "seed", "stock_voice", "instruction", "text", "language", "seconds",
               "created"}],
    "chosen_take": "<id>", "review": "pending" | "picked",
    "tests": {key: {"file", "text", "mood", "engine", "seconds", "created"}},
    "commercial_ok": true, "sheet_changed": false, "updated": "..."
  }
  voice_lines = {line id: {"file", "text", "category", "mood", "engine", "seconds", "mouth": [...], "reference" (the voice's id), "created"}}
"""
import copy
import hashlib
import random
import re
import uuid

from ..library import now
from . import describe, engines

REFERENCE = "voice/reference.wav"
MODES = ("design", "stock", "upload", "copy")
MAX_TAKES_KEPT = 12
MAX_DESCRIPTION_CHARS = 800
MAX_LINE_CHARS = 500
LANG_RE = re.compile(r"^[A-Za-z]+$")


def voice_of(npc):
    """The character's voice record with every field present (nothing is written)."""
    v = dict(npc.get("voice") or {})
    v.setdefault("engine", engines.DEFAULT_ENGINE)
    v.setdefault("mode", "stock" if engines.can(v["engine"], "stock") else "design")
    v.setdefault("speaker", None)
    v.setdefault("description", "")
    v.setdefault("edited", False)
    v.setdefault("voice_type", None)
    v.setdefault("chips", {})
    v.setdefault("language", "English")
    v.setdefault("stock_voice", None)
    v.setdefault("settings", {})
    v.setdefault("takes", [])
    v.setdefault("tests", {})
    v.setdefault("review", None)
    return v


def speaker_for(v):
    """Which engine speaks lines in this voice."""
    speaker = v.get("speaker")
    if is_preset(v):
        return "qwen_custom"
    if speaker in engines.ENGINES and (engines.can(speaker, "clone") or (engines.can(speaker, "stock") and v.get("stock_voice"))):
        return speaker
    if v.get("mode") == "stock" and v.get("engine") == "kokoro":
        return "kokoro"
    return v.get("engine") if engines.can(v.get("engine"), "clone") else engines.DEFAULT_ENGINE


def is_preset(v):
    return v.get("engine") == "qwen_custom" and v.get("mode") == "stock" and bool(v.get("stock_voice"))


def identity_id(v):
    return (v.get("preset_identity") or {}).get("id") if is_preset(v) else (v.get("reference") or {}).get("id")


def instruction(v):
    return describe.compose(v.get("description"), v.get("voice_type"), v.get("chips"))


def commercial_ok(v):
    """False if a non-commercial model made or speaks this voice."""
    used = {v.get("engine"), speaker_for(v), (v.get("reference") or {}).get("engine")}
    return all((engines.spec(e) or {}).get("commercial_ok", True) for e in used if e)


def _number(raw, key, lo, hi, default):
    value = raw.get(key, default)
    if isinstance(value, bool):
        raise ValueError(f"{key} must be a number")
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{key} must be a number") from None
    return max(lo, min(hi, value))


def clean_settings(raw, npc):
    """Validate what the designer saves. Returns the fields to store; raises ValueError with a readable message."""
    if not isinstance(raw, dict):
        raise ValueError("The voice settings must be an object")
    old = voice_of(npc)
    engine = raw.get("engine", old["engine"])
    if engine not in engines.ENGINES:
        raise ValueError(f"Unknown voice model: {engine}")
    mode = raw.get("mode", old["mode"])
    if mode not in MODES:
        raise ValueError(f"Unknown voice source: {mode}")
    if mode == "design" and not engines.can(engine, "design"):
        raise ValueError(f"{engines.ENGINES[engine]['label']} cannot make a voice from a description: choose a model with voice design")
    if mode == "stock" and not engines.can(engine, "stock"):
        raise ValueError(f"{engines.ENGINES[engine]['label']} has no stock voices: pick Kokoro")
    speaker = raw.get("speaker", old.get("speaker"))
    if speaker is not None and (speaker not in engines.ENGINES or not (engines.can(speaker, "clone") or engines.can(speaker, "stock"))):
        raise ValueError(f"{speaker} cannot speak lines in a saved voice")
    description = str(raw.get("description", old["description"]) or "").strip()
    if len(description) > MAX_DESCRIPTION_CHARS:
        raise ValueError(f"Keep the description under {MAX_DESCRIPTION_CHARS} characters")
    data = describe.load_data()
    voice_type = raw.get("voice_type", old["voice_type"]) or None
    if voice_type is not None and voice_type not in {v["id"] for v in data["voice_types"]}:
        raise ValueError(f"Unknown voice type: {voice_type}")
    chips_raw = raw.get("chips", old["chips"]) or {}
    if not isinstance(chips_raw, dict):
        raise ValueError("chips must be an object")
    chips = {}
    for group in data["chip_groups"]:
        pick = chips_raw.get(group["id"])
        if pick:
            if pick not in {c["id"] for c in group["chips"]}:
                raise ValueError(f"Unknown {group['label'].lower()} choice: {pick}")
            chips[group["id"]] = pick
    language = str(raw.get("language", old["language"]) or "English")
    if not LANG_RE.match(language) or language not in engines.ENGINES[engine]["languages"]:
        raise ValueError(f"{engines.ENGINES[engine]['label']} does not speak {language}")
    stock_voice = raw.get("stock_voice", old["stock_voice"])
    stock_engine = engine if engines.can(engine, "stock") else "kokoro"
    if stock_voice is not None and stock_voice not in {s["id"] for s in engines.stock_voices(stock_engine)}:
        raise ValueError(f"Unknown stock voice: {stock_voice}")
    settings_raw = raw.get("settings", old["settings"]) or {}
    if not isinstance(settings_raw, dict):
        raise ValueError("settings must be an object")
    settings = {"temperature": _number(settings_raw, "temperature", 0.3, 1.3, 0.9),
                "cfg": _number(settings_raw, "cfg", 1.0, 8.0, 4.0),
                "speed": _number(settings_raw, "speed", 0.5, 2.0, 1.0)}
    script = str(raw.get("script", old.get("script") or "") or "").strip()
    if len(script) > 400:
        raise ValueError("Keep the take script under 400 characters")
    out = {"engine": engine, "mode": mode, "speaker": speaker, "description": description, "voice_type": voice_type,
           "chips": chips, "language": language, "stock_voice": stock_voice, "settings": settings, "script": script}
    if speaker == "qwen_custom" and not is_preset(out):
        raise ValueError("CustomVoice requires a native preset identity; select it as the voice model")
    if is_preset(out):
        if speaker not in (None, "qwen_custom"):
            raise ValueError("A CustomVoice preset renders directly through CustomVoice; choose a cloning source for another speaking model")
        previous = old.get("preset_identity") or {}
        preset_id = previous.get("id") if previous.get("speaker") == stock_voice else new_reference_id()
        out["preset_identity"] = {"id": preset_id, "kind": "model_preset", "engine": "qwen_custom", "speaker": stock_voice,
            "checkpoint": engines.CUSTOM_REPO, "checkpoint_revision": engines.CUSTOM_REVISION, "reference_required": False,
            "languages": list(engines.ENGINES["qwen_custom"]["languages"])}
    if "edited" in raw:
        out["edited"] = bool(raw["edited"])
    if "auto_description" in raw:
        out["auto_description"] = str(raw["auto_description"] or "")[:MAX_DESCRIPTION_CHARS]
    problem = describe.adult_problem(npc, describe.compose(description, voice_type, chips) + " " + script)
    if problem:
        raise ValueError(problem)
    return out


def save_settings(npc, clean, lib=None):
    v = voice_of(npc)
    old_id = identity_id(v)
    previous = render_snapshot(v)
    v.update(clean)
    if old_id != identity_id(v):
        v.pop("library_binding", None)
        if is_preset(v):
            v["chosen_take"] = None
    if is_preset(v):
        history = copy.deepcopy(v.get("reference_history") or [])
        if not is_preset(previous) or old_id != identity_id(v):
            old_ref = previous.get("reference")
            if old_ref and lib is not None and not any(item["id"] == old_ref.get("id") for item in history):
                pinned = _immutable_reference(lib, npc["id"], lib.read_asset(npc["id"], old_ref["file"]), old_ref)
                previous["reference"] = pinned
                history.append({**pinned, "voice_snapshot": previous})
            v.pop("reference", None)
        if old_id and not any(item["id"] == old_id for item in history):
            if is_preset(previous):
                history.append({"id": old_id, "kind": "model_preset", "voice_snapshot": previous})
        current = {"id": v["preset_identity"]["id"], "kind": "model_preset", "voice_snapshot": render_snapshot(v)}
        if not any(item["id"] == current["id"] for item in history):
            history.append(current)
        v["reference_history"] = history
        v["review"] = "picked"
    else:
        v.pop("preset_identity", None)
    v["commercial_ok"] = commercial_ok(v)
    v["updated"] = now()
    npc["voice"] = v
    return v


def new_reference_id():
    """Every chosen voice gets its own id: lines spoken in an older one are then out of date (a timestamp can repeat)."""
    return uuid.uuid4().hex[:12]


def new_seed():
    return random.randrange(2 ** 31)


def render_snapshot(v):
    """A self-contained identity/settings snapshot without growing take/test/history lists."""
    snapshot = copy.deepcopy({key: value for key, value in v.items() if key not in {"takes", "tests", "reference_history", "comparisons", "auditions"}})
    snapshot.setdefault("studio", {"pronunciations": [], "presets": [], "default_performance": "", "line_performances": {}})
    return snapshot


def _immutable_reference(lib, npc_id, data, reference):
    ref = copy.deepcopy(reference)
    ref.pop("voice_snapshot", None)
    digest = hashlib.sha256(data).hexdigest()
    if ref.get("sha256") and ref["sha256"] != digest:
        raise ValueError("Reference checksum failed")
    ref_id = ref.get("id")
    if not isinstance(ref_id, str) or not re.fullmatch(r"[a-f0-9]{12,32}", ref_id):
        ref_id = hashlib.sha256((str(ref_id) + digest).encode()).hexdigest()[:24]
    rel = f"voice/references/{ref_id}.wav"
    path = lib.resolve(npc_id, rel)
    if not path.is_relative_to(lib.root.resolve()):
        raise ValueError("Reference directory escapes the NPC library")
    if path.is_file():
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError("An immutable reference version has conflicting audio")
    else:
        lib.write_asset(npc_id, rel, data)
    ref.update(file=rel, id=ref_id, sha256=digest)
    return ref


def install_reference(lib, npc, v, data, reference):
    """Append identity/reference history before replacing the active reference.

    Retain reference.wav for existing external consumers; model jobs use the
    immutable file in reference metadata instead.
    """
    history = copy.deepcopy(v.get("reference_history") or [])
    previous = voice_of(npc)
    old = previous.get("reference")
    if old and old.get("file") and not any(item["id"] == old.get("id") for item in history):
        old = _immutable_reference(lib, npc["id"], lib.read_asset(npc["id"], old["file"]), old)
        history.append({**old, "voice_snapshot": render_snapshot(previous)})
    ref = _immutable_reference(lib, npc["id"], data, reference)
    v["reference"] = ref
    if not any(item["id"] == ref["id"] for item in history):
        history.append({**ref, "voice_snapshot": render_snapshot(v)})
    v["reference_history"] = history
    v["updated"] = now()
    lib.write_asset(npc["id"], REFERENCE, data)
    return ref


def freeze_voice(lib, npc_id, v):
    """Also pin legacy mutable references when submitting a durable render job."""
    snapshot = render_snapshot(v)
    if is_preset(v):
        return snapshot  # Any audition WAV is display media, never generation conditioning.
    ref = snapshot.get("reference")
    if ref and ref.get("file"):
        pinned = _immutable_reference(lib, npc_id, lib.read_asset(npc_id, ref["file"]), ref)
        # Preserve the legacy identity key so existing line-staleness checks stay valid.
        pinned["id"] = ref.get("id") or pinned["id"]
        snapshot["reference"] = pinned
    return snapshot


def restore_reference(lib, npc_id, reference_id):
    def apply(npc):
        v = voice_of(npc)
        saved = next((item for item in v.get("reference_history", []) if item["id"] == reference_id), None)
        if saved is None:
            raise KeyError(reference_id)
        snapshot = saved["voice_snapshot"]
        clean_settings(snapshot, npc)
        if saved.get("kind") == "model_preset":
            v.update(copy.deepcopy(snapshot))
            if "reference" not in snapshot:
                v.pop("reference", None)
            if "library_binding" not in snapshot:
                v.pop("library_binding", None)
            v["review"] = "picked"
            v["commercial_ok"] = commercial_ok(v)
            v["updated"] = now()
            npc["voice"] = v
            return
        data = lib.read_asset(npc_id, saved["file"])
        if hashlib.sha256(data).hexdigest() != saved["sha256"]:
            raise ValueError("Reference checksum failed")
        v.update(copy.deepcopy(snapshot))
        if "library_binding" not in snapshot:
            v.pop("library_binding", None)
        if "preset_identity" not in snapshot:
            v.pop("preset_identity", None)
        if "studio" not in snapshot:
            v.pop("studio", None)
        install_reference(lib, npc, v, data, saved)
        v["review"] = "picked"
        v["commercial_ok"] = commercial_ok(v)
        npc["voice"] = v
    return lib.update(npc_id, apply)


def select_take(lib, npc_id, take_id, review="picked"):
    """Make a take the character's voice: its clip becomes voice/reference.wav. Lines made from an older voice go stale."""
    def apply(npc):
        v = voice_of(npc)
        take = next((t for t in v["takes"] if t.get("id") == take_id), None)
        if take is None:
            raise KeyError(take_id)
        data = lib.read_asset(npc["id"], take["file"])
        retained = [candidate for session in v.get("comparisons", []) for candidate in session["candidates"] if candidate["id"] == take_id]
        if any(candidate["sha256"] != hashlib.sha256(data).hexdigest() for candidate in retained):
            raise ValueError("Retained comparison candidate checksum failed; restore its original audio before assignment")
        if take.get("voice_snapshot"):
            v.update(copy.deepcopy(take["voice_snapshot"]))
        ref = {"text": take["text"], "seconds": take.get("seconds"), "source": f"take:{take_id}",
               "engine": take["engine"], "language": take.get("language", "English"), "created": now(), "id": new_reference_id()}
        if take["engine"] == "qwen_custom":
            ref["kind"] = "audition"
        v["chosen_take"] = take_id
        v["review"] = review
        # the voice is now this take: its model and source too, so lines are spoken by what made it (a Qwen take chosen after
        # trying Kokoro stock takes must not be read by Kokoro)
        v["engine"], v["mode"] = take["engine"], take.get("mode") or v["mode"]
        if not is_preset({**v, "stock_voice": take.get("stock_voice") or v.get("stock_voice")}):
            v.pop("preset_identity", None)
        if take.get("mode") == "stock" and take.get("stock_voice"):
            v["stock_voice"] = take["stock_voice"]
        v.pop("library_binding", None)
        install_reference(lib, npc, v, data, ref)
        v["commercial_ok"] = commercial_ok(v)
        npc["voice"] = v
    return lib.update(npc_id, apply)


def prune_takes(lib, npc, keep=MAX_TAKES_KEPT):
    """Keep the newest `keep` takes (and the chosen one); delete the files of the rest. Mutates npc."""
    v = npc.get("voice") or {}
    takes = v.get("takes") or []
    if len(takes) <= keep:
        return
    chosen = v.get("chosen_take")
    from .comparison import protected_take_ids
    protected = protected_take_ids(v)
    survivors = takes[-keep:]
    if chosen and not any(t["id"] == chosen for t in survivors):
        survivors = [t for t in takes if t["id"] == chosen] + survivors[1:]
    survivors = [t for t in takes if t["id"] in protected and t not in survivors] + survivors
    for t in takes:
        if t not in survivors:
            try:
                lib.resolve(npc["id"], t["file"]).unlink()
            except (FileNotFoundError, KeyError):
                pass
    v["takes"] = survivors


def line_is_stale(npc, line_id):
    """A voiced line is out of date when its text changed or the character's voice changed after it was made."""
    meta = (npc.get("voice_lines") or {}).get(line_id)
    if not meta:
        return False
    text, current_row = None, None
    for rows in ((npc.get("dialogue") or {}).get("lines") or {}).values():
        for row in rows:
            if row.get("id") == line_id:
                text = row.get("text")
                current_row = row
    ref = identity_id(npc.get("voice") or {})
    from . import authoring
    return (text != meta.get("text") or (ref is not None and meta.get("reference") != ref)
            or (current_row is not None and authoring.line_stale(npc, line_id, current_row, meta)))
