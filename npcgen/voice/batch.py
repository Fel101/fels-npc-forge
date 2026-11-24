"""Queue Cast voices and optional dialogue for multiple characters."""
from .. import dialogue
from . import describe, engines, lab_voice, record

MAX_TAKES = 4


def clean_request(raw):
    """{engine, takes, lines, redo} from a request body; raises ValueError."""
    raw = raw if isinstance(raw, dict) else {}
    engine = raw.get("engine") or engines.DEFAULT_ENGINE
    if engine not in engines.DESIGN_MODELS and engine not in engines.PRESET_MODELS:
        raise ValueError(f"Unknown voice model: {engine}")
    takes = raw.get("takes", 3)
    if isinstance(takes, bool) or not isinstance(takes, int) or not 1 <= takes <= MAX_TAKES:
        raise ValueError(f"takes must be 1 to {MAX_TAKES}")
    return {"engine": engine, "takes": takes, "lines": bool(raw.get("lines")), "redo": bool(raw.get("redo"))}


def job_assets(lib, npc, req, cfg=None):
    """Freeze a saved link or queue its creation before the requested dialogue lines."""
    v = record.voice_of(npc)
    out = {"batch": True, "voice_snapshot": record.render_snapshot(v)}
    needs_voice = req["redo"] or not v.get("lab_voice")
    if needs_voice:
        engine = req["engine"]
        if engine in engines.PRESET_MODELS:
            order = describe.stock_match(npc, engine)
            voice_id = (v.get("stock_voice") if v["engine"] == engine else None) or next(iter(order), None)
            if voice_id is None:
                raise ValueError("Choose a Voice Lab preset on the character's Voice card before queuing dialogue.")
            request = {"source": "preset", "model": engines.PRESET_MODELS[engine], "voice_id": voice_id,
                       "existing": v.get("lab_voice")}
        else:
            request = lab_voice.request(npc, {"source": "description"})
            request["engine"] = engines.DESIGN_MODELS[engine]
        out["lab_create"] = request
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
    return out if (out.get("lab_create") or out.get("lines")) else None


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
