"""Voicing many characters at once: the Library's "Voice for N selected", the Create tab's "Voice" / "Voice the dialogue"
checkboxes and the CLI all come here.

Each character gets the voice its sheet suggests (describe.describe, or the best stock voice when the model has no voice design),
unless it already has voice settings; then 1-4 takes are made, the first is used until you pick one on the Review page, and the
dialogue can be voiced right after, in the same job (the steps run in order: takes, pick, lines). A character whose voice you
already picked keeps it unless `redo` is asked for; its dialogue lines are voiced only where missing or out of date.
Batch jobs use the ordinary queue lane, so the voice designer's quick jobs still go first.
"""
from .. import dialogue
from . import describe, engines, record

MAX_TAKES = 4


def clean_request(raw):
    """{engine, takes, lines, redo} from a request body; raises ValueError."""
    raw = raw if isinstance(raw, dict) else {}
    engine = raw.get("engine") or engines.DEFAULT_ENGINE
    if engine not in engines.ENGINES:
        raise ValueError(f"Unknown voice model: {engine}")
    if not (engines.can(engine, "design") or engines.can(engine, "stock")):
        raise ValueError(f"{engines.ENGINES[engine]['label']} cannot make a voice on its own (it copies one): pick Qwen3-TTS, "
                         "Breeze TTS 2 or Kokoro")
    takes = raw.get("takes", 3)
    if isinstance(takes, bool) or not isinstance(takes, int) or not 1 <= takes <= MAX_TAKES:
        raise ValueError(f"takes must be 1 to {MAX_TAKES}")
    return {"engine": engine, "takes": takes, "lines": bool(raw.get("lines")), "redo": bool(raw.get("redo"))}


def prepare(lib, npc, engine, redo=False, cfg=None):
    """Give a character without voice settings (or every character, with redo) the voice its sheet suggests. Returns the npc."""
    v = npc.get("voice") or {}
    if v and not redo and (v.get("description") or v.get("stock_voice") or v.get("reference")):
        return npc
    if engines.can(engine, "design"):
        suggested = describe.describe(npc)
        raw = {"engine": engine, "mode": "design", "description": suggested, "auto_description": suggested, "edited": False,
               "voice_type": None, "chips": {}}
    else:
        voices=engines.stock_voices(engine,cfg) if engine=='qwen_custom' else describe.stock_match(npc,engine,cfg)
        if not voices:raise ValueError(f'{engine} has no cached stock voices available')
        raw = {"engine": engine, "mode": "stock", "stock_voice": voices[0]['id'] if engine=='qwen_custom' else voices[0]}
    if engine == "qwen_custom" or record.is_preset(v):
        raw["speaker"] = None
    if record.is_preset(v) and not engines.can(engine, "stock"):
        raw["stock_voice"] = None
    if (v.get("language") or "English") not in engines.ENGINES[engine]["languages"]:
        raw["language"] = "English"
    clean = record.clean_settings(raw, npc)
    return lib.update(npc["id"], lambda rec: record.save_settings(rec, clean, lib))


def job_assets(lib, npc, req, cfg=None):
    """The "voice" job request for one character (None when there is nothing to do)."""
    npc = prepare(lib, npc, req["engine"], req["redo"],cfg)
    v = record.voice_of(npc)
    out = {"batch": True, "voice_snapshot": record.freeze_voice(lib, npc["id"], v)}
    needs_voice = req["redo"] or not (v.get("reference") or record.is_preset(v))
    if needs_voice:
        if v["mode"] not in ("design", "stock"):
            return None                                  # an uploaded or copied voice is kept as it is
        out.update(takes=req["takes"], auto_pick=True, engine=v["engine"], mode=v["mode"], language=v["language"],
                   settings=v["settings"], script=v.get("script") or describe.take_script(npc, v["language"]),
                   seed_base=record.new_seed())
        if v["mode"] == "design":
            out["instruction"] = record.instruction(v)
        else:
            order = [v["stock_voice"]] * req["takes"] if v["engine"] == "qwen_custom" else describe.stock_match(npc, v["engine"],cfg)
            if cfg is not None and v.get('stock_voice') not in order:raise ValueError('The selected stock voice is not cached locally')
            if v.get("stock_voice") in order:
                order.remove(v["stock_voice"])
                order.insert(0, v["stock_voice"])
            out["stock_order"] = order[:req["takes"]]
    if req["lines"]:
        if not npc.get("dialogue"):
            pack = dialogue.generate(npc)
            npc = lib.update(npc["id"], lambda rec: rec.update(dialogue=pack))
        rows = [row for rows in npc["dialogue"]["lines"].values() for row in rows]
        todo = [row["id"] for row in rows if needs_voice or not (npc.get("voice_lines") or {}).get(row["id"])
                or record.line_is_stale(npc, row["id"])]
        if todo:
            out["lines"] = todo
            out["line_inputs"] = {row["id"]: {"text": row["text"], "category": category,
                "mood": ((npc.get("voice_lines") or {}).get(row["id"]) or {}).get("mood", "neutral")}
                for category, lines in npc["dialogue"]["lines"].items() for row in lines if row["id"] in todo}
    return out if (out.get("takes") or out.get("lines")) else None


def review_rows(lib, visible_ids):
    """The Review page: every character whose voice still waits for you, with its takes."""
    rows = []
    for npc_id in visible_ids:
        try:
            npc = lib.get(npc_id)
        except KeyError:
            continue
        v = npc.get("voice") or {}
        if v.get("review") != "pending" or not v.get("takes"):
            continue
        p = npc.get("profile") or {}
        rows.append({"id": npc["id"], "name": f'{p.get("first_name", "")} {p.get("last_name", "")}'.strip(),
                     "age": p.get("age"), "occupation": p.get("occupation"), "style": npc.get("style"),
                     "setting": npc.get("setting"), "engine": v.get("engine"), "mode": v.get("mode"),
                     "description": record.instruction(v) if v.get("mode") == "design" else v.get("stock_voice"),
                     "chosen_take": v.get("chosen_take"),
                     "takes": [{k: t.get(k) for k in ("id", "n", "file", "seconds", "stock_voice", "engine", "created")}
                               for t in v["takes"][-4:]]})
    return rows
