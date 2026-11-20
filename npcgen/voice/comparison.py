"""Same-script audition sessions with immutable candidates and mutable human ratings."""
import copy
import hashlib
import re
import uuid

from ..library import now
from . import record

ID = re.compile(r"^[a-f0-9]{12}$")


def create(lib, npc_id, body):
    raw = body.get("take_ids")
    if not isinstance(raw, list) or not 2 <= len(raw) <= 12 or not all(isinstance(i, str) for i in raw):
        raise ValueError("Choose 2-12 candidates with the same script")
    ids = list(dict.fromkeys(raw))
    if len(ids) < 2:
        raise ValueError("Choose at least two different candidates")
    title = body.get("title", "Same-script audition")
    if not isinstance(title, str) or not title.strip() or len(title) > 100:
        raise ValueError("Comparison name must be 1-100 characters")
    comparison_id = uuid.uuid4().hex[:12]
    def apply(npc):
        v = record.voice_of(npc)
        sessions = v.get("comparisons") or []
        if len(sessions) >= 30:
            raise ValueError("This character already has 30 retained comparison sessions")
        takes = {t["id"]: t for t in v["takes"]}
        if any(i not in takes for i in ids):
            raise ValueError("A selected candidate no longer exists")
        selected = [takes[i] for i in ids]
        if len({(t["text"], t.get("language", "English")) for t in selected}) != 1:
            raise ValueError("Compare candidates with the same exact script and language")
        candidates = [{**copy.deepcopy(t), "sha256": hashlib.sha256(lib.read_asset(npc_id, t["file"])).hexdigest(),
                       "rating": None, "favorite": False, "notes": ""} for t in selected]
        v["comparisons"] = sessions + [{"id": comparison_id, "title": title.strip(), "script": selected[0]["text"],
            "language": selected[0].get("language", "English"), "candidates": candidates, "created": now()}]
        npc["voice"] = v
    return lib.update(npc_id, apply)


def review(lib, npc_id, comparison_id, take_id, body):
    if not ID.fullmatch(comparison_id):
        raise ValueError("Invalid comparison id")
    if set(body) - {"rating", "favorite", "notes"}:
        raise ValueError("Only ratings, favorites and listening notes can be changed")
    def apply(npc):
        v = record.voice_of(npc)
        session = next((s for s in v.get("comparisons", []) if s["id"] == comparison_id), None)
        candidate = next((c for c in session["candidates"] if c["id"] == take_id), None) if session else None
        if not candidate:
            raise KeyError(take_id)
        rating = body.get("rating", candidate["rating"])
        favorite = body.get("favorite", candidate["favorite"])
        notes = body.get("notes", candidate["notes"])
        if rating is not None and (isinstance(rating, bool) or not isinstance(rating, int) or not 1 <= rating <= 5):
            raise ValueError("Rating must be 1-5 stars or null")
        if not isinstance(favorite, bool) or not isinstance(notes, str) or len(notes) > 1000:
            raise ValueError("Use a boolean favorite and notes up to 1000 characters")
        candidate.update(rating=rating, favorite=favorite, notes=notes.strip())
        npc["voice"] = v
    return lib.update(npc_id, apply)


def protected_take_ids(v):
    return {c["id"] for session in v.get("comparisons", []) for c in session["candidates"]}
