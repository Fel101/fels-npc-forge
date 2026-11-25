"""What this PC can make: the tiers measured in data/hardware.json (docs/hardware.md, scripts/measure_gpu.py), which tier
the card ComfyUI draws on is, and the picture engines each tier starts with.

A tier is a size of graphics card: 16, 24 or 32 GB, or "none" for a PC without one big enough (and without ComfyUI).
What a tier makes is measured, not guessed: a pipeline is in a tier when it ran on a card of that size (the measuring
script makes the R9700 look smaller), or, where it was not run at that size, when its peak on the full card fits. One that
finished there but took more than SLOW times as long as on the full card is listed as slow."""

import json
import pathlib
import threading

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "hardware.json"
TIERS = (("32", 32), ("24", 24), ("16", 16))         # largest first
SLACK_MB = 1536            # a card sold as N GB reports a little less (a 16 GB card: about 16,300 MB)
HEADROOM_MB = 1024         # the desktop and the driver keep about this much of the card
SLOW = 3                   # slower than this many times its time on the full card: it crawled, and does not count
                           # (memory used through the card from the PC's own is shown in the table, but decides nothing: a
                           # Qwen portrait on a 24 GB card used 4.7 GB of it and took 36 s against 26 s on the full card)
SHARED_ROOM_MB = 8192      # what a small voice model and a chat model take beside the pictures (the owner's Kokoro + LM Studio)
NO_CARD = ["Characters: their details, personalities and backstories", "Dialogue packs (JSON, Ink, Yarn Spinner)",
           "Cast packs", "Scenes' scripts", "The MCP server for AI agents"]
IMAGE_ORDER = ("qwen2512", "zimage")                  # the image engines a tier may start with, best first (commercially clear)
EDIT_ORDER = ("qwen2511",)

_lock = threading.Lock()
_cache = {}


def load():
    """The measured table: {"measured", "card", "pipelines": [{"id", "what", "engine", "runs": {"32": {...}, ...}}]}."""
    with _lock:
        if "table" not in _cache:
            try:
                _cache["table"] = json.loads(DATA.read_text("utf-8"))
            except (OSError, ValueError):
                _cache["table"] = {"pipelines": []}
        return _cache["table"]


def tier_for(total_mb):
    """The tier of a card with `total_mb` of memory: "32", "24", "16" or "none"; None when it is not known."""
    if not total_mb:
        return None
    for tier, gb in TIERS:
        if total_mb >= gb * 1024 - SLACK_MB:
            return tier
    return "none"


def _gb(tier):
    return dict(TIERS).get(tier, 0)


_RANK = {None: 0, "slow": 1, "yes": 2}


def fit(row, tier, rows=None):
    """How a measured pipeline runs on a card of this tier: "yes", "slow" (it finished there, but took more than SLOW times
    as long as on the full card), or None (it failed there; or, not run there, its peak on the full card does not fit).
    A pipeline made from another's pictures (row["needs"]: a 3D model from the model sheet) runs no better than that one."""
    own = _own_fit(row, tier)
    for need in row.get("needs") or []:
        other = next((r for r in (rows if rows is not None else load().get("pipelines") or []) if r.get("id") == need), None)
        if other is not None:
            own = min(own, fit(other, tier, rows), key=_RANK.get)
    return own


def _own_fit(row, tier):
    gb = _gb(tier)
    if not gb:
        return None
    runs = row.get("runs") or {}
    run = runs.get(tier)
    full = runs.get("32")
    if run is not None:
        if run.get("status") != "done":
            return None
        return "slow" if slower(row, tier) > SLOW else "yes"
    return "yes" if full and full.get("status") == "done" and (full.get("vram_mb") or 0) <= gb * 1024 - HEADROOM_MB else None


def slower(row, tier):
    """How many times as long the pipeline took on this tier's card as on the full card (1.0 when not measured there)."""
    runs = row.get("runs") or {}
    run, full = runs.get(tier), runs.get("32")
    if not run or not full or run is full or not full.get("seconds"):
        return 1.0
    return (run.get("seconds") or 0) / full["seconds"]


def fits(row, tier, rows=None):
    """Whether a measured pipeline runs on a card of this tier at about its usual speed."""
    return fit(row, tier, rows) == "yes"


def _times(row, tier, rows):
    """How many times as long as on the full card, counting the pictures it is made from."""
    needed = [r for r in rows if r.get("id") in (row.get("needs") or [])]
    return max([slower(row, tier)] + [_times(r, tier, rows) for r in needed])


def _first(kind, tier, rows):
    """The best engine of this kind whose deciding pipelines all run on the tier: at their usual speed if one does, else slowly."""
    order = IMAGE_ORDER if kind == "image" else EDIT_ORDER
    for wanted in (("yes",), ("yes", "slow")):
        for engine in order:
            deciding = [row for row in rows if row.get("engine") == f"{kind}:{engine}" and row.get("default")]
            if deciding and all(fit(row, tier, rows) in wanted for row in deciding):
                return engine
    return None


def tier_summary(tier, table=None):
    """{"tier", "makes", "defaults", "busy_line"} for one tier: what it makes from the table, the engines it starts with,
    and whether One model at a time is likely needed there."""
    table = table or load()
    rows = table.get("pipelines") or []
    if tier in (None, "none"):
        return {"tier": tier, "makes": list(NO_CARD), "slow": [], "defaults": {}, "needs_switch": None, "need_mb": None,
                "need_engine": None, "whole_card": False}
    makes = [row.get("makes") or row["what"] for row in rows if fits(row, tier, rows) and row.get("listed", True)]
    slow = [{"what": row.get("makes") or row["what"], "times": round(_times(row, tier, rows), 1)}
            for row in rows if fit(row, tier, rows) == "slow" and row.get("listed", True)]
    defaults = {key: engine for key, engine in (("image_model", _first("image", tier, rows)),
                                                ("edit_model", _first("edit", tier, rows))) if engine}
    # What each chosen engine needs to run at its usual speed: the room of the smallest card its deciding pipelines all ran
    # on that way (ComfyUI fills a bigger card with what it may keep, so a peak on the full card says little), else its peak
    # on this tier's card. The switch line names the engine that needs the most.
    need, need_engine = 0, None
    for kind, engine in (("image", defaults.get("image_model")), ("edit", defaults.get("edit_model"))):
        deciding = [row for row in rows if row.get("default") and row.get("engine") == f"{kind}:{engine}"]
        if not engine or not deciding:
            continue
        room = next((gb * 1024 - HEADROOM_MB for small, gb in reversed(TIERS) if gb <= _gb(tier)
                     and all(row["runs"].get(small) and fits(row, small, rows) for row in deciding)), None)
        if room is None:
            room = max((row["runs"].get(tier) or row["runs"].get("32") or {}).get("vram_mb") or 0 for row in deciding)
        if room > need:
            need, need_engine = room, f"{kind}:{engine}"
    room = _gb(tier) * 1024 - HEADROOM_MB
    needs = bool(need and need + SHARED_ROOM_MB >= room)      # (only just fits: keep it on)
    return {"tier": tier, "makes": list(NO_CARD) + makes, "slow": slow, "defaults": defaults, "needs_switch": needs,
            "need_mb": need or None, "need_engine": need_engine, "whole_card": bool(need and need >= room)}


RAM_MARGIN_MB = 4096       # over the RAM ComfyUI's process used at that card size
VRAM_MARGIN_MB = 512       # under what a card of that size leaves free


def start_needs(engine):
    """(free RAM MB, free VRAM MB) a big picture engine ("image:zimage") needs before it starts, from the smallest card size
    its pipelines all finished on (slowly or not: a slow picture beats a refused one); None when the table has no such run
    (the engine's own figures stand)."""
    rows = [row for row in load().get("pipelines") or [] if row.get("engine") == engine and row.get("default")]
    for tier, gb in reversed(TIERS):                      # smallest first
        runs = [row.get("runs", {}).get(tier) for row in rows]
        if rows and all(run is not None for run in runs) and all(fit(row, tier) for row in rows):
            ram = max(run.get("ram_private_mb") or 0 for run in runs)
            return ram + RAM_MARGIN_MB, gb * 1024 - HEADROOM_MB - VRAM_MARGIN_MB
    return None


def remember(total_mb, device=None):
    """What /api/health last read of ComfyUI's card, for the defaults of a request that names no engine."""
    with _lock:
        _cache["card"] = {"total_mb": total_mb, "device": device}


def your_pc(total_mb=None, device=None):
    """The "Your PC" box: {"device", "total_mb", "tier", "summary"}; from the last reading when none is given."""
    if total_mb is None:
        with _lock:
            card = dict(_cache.get("card") or {})
        total_mb, device = card.get("total_mb"), card.get("device")
    tier = tier_for(total_mb)
    return {"device": device, "total_mb": total_mb, "tier": tier, **tier_summary(tier)}


def default_engines():
    """The picture engines the tier of the last-read card starts with ({} while no card has been read)."""
    with _lock:
        card = dict(_cache.get("card") or {})
    tier = tier_for(card.get("total_mb"))
    return tier_summary(tier)["defaults"] if tier else {}
