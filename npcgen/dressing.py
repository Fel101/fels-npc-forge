"""A garment of the Items tab on a designed body: fitted in a process of its own, kept with the item.

Trying a shirt on a character is the Body Designer's fit (bodydesigner/garments.py), which takes about half a minute and a lot of numpy, so it is not done in the server's own process:
`python -m npcgen.outfit` does it (npcgen/outfit.py), Cancel stops it, and `Dressings` runs one at a time on a thread of its own, the way npcgen/facescan.py scans faces, so a request
never waits for it. The result is the character's animated model with this garment on it, in place of the one of the same kind it was made with (npcgen/glbprune.py takes that off), kept in
the item's folder as worn/<character>.glb with a record of what it was fitted to (worn/<character>.json): looseness, length and the body's files. That is what decides whether a fit is
still good. The colour and the cloth are not part of it: a colour chosen after the fit is put on the finished file when it is asked for (npcgen/clothing.py `recolour`).
"""
import json
import sys
import tempfile
import threading
from pathlib import Path

from . import bodymaker, clothing, items
from .comfy import Cancelled
from .library import now

WORN_DIR = "worn"
SECONDS = 900                       # far more than a fit takes: it only stops a hung process
RECORD_VERSION = 1


class DressingError(Exception):
    """A garment could not be fitted. The message is safe to show."""


class _Gone(Exception):
    """The item or the character was deleted while it waited."""


def fit_process(body, pieces, cancel=None):
    """(the body's bytes wearing `pieces` in place of what it wears of those kinds, the report {worn, failed, replaced}): the fit is made by `python -m npcgen.outfit`, in a process of its
    own that `cancel` (an Event) can stop. Raises DressingError (what the Body Designer said, or that the process failed) and Cancelled."""
    with tempfile.TemporaryDirectory(prefix="npcforge-dress-") as tmp:
        tmp = Path(tmp)
        (tmp / "body.glb").write_bytes(body)
        (tmp / "pieces.json").write_text(json.dumps(pieces), "utf-8")
        try:
            bodymaker._run([sys.executable, "-m", "npcgen.outfit", "--animated", str(tmp / "body.glb"), "--out-animated", str(tmp / "worn.glb"), "--pieces", str(tmp / "pieces.json"),
                            "--replace", "--report", str(tmp / "report.json")], tmp, "dress", "Fitting the clothes", SECONDS, cancel)
        except bodymaker.BodyError as exc:
            raise DressingError(str(exc)) from None
        return (tmp / "worn.glb").read_bytes(), json.loads((tmp / "report.json").read_text("utf-8"))


def rebuild_process(cfg, npc, pieces, cancel=None, say=None):
    """(the animated body wearing `pieces` in place of what it wears of those kinds, the report {worn, failed, replaced, [swinging_left_off]}) for a body that has face shapes or spring bones (`bodymaker.needs_rebuild`):
    it cannot be fitted in place (the Body Designer's fit refuses a body with morph targets, and a file with spring bones never goes into a fitter), so it is built again from its recipe with the clothes it keeps
    and these (npcgen/bodymaker.py `refit`: about two minutes). Raises DressingError (what went wrong, in words) and Cancelled."""
    try:
        return bodymaker.refit(cfg, npc, pieces, cancel, say)
    except bodymaker.BodyError as exc:
        raise DressingError(str(exc)) from None


class Dressings:
    """Fits garments of the Items tab to characters' bodies one at a time and keeps each result with its item. `fit(body bytes, pieces, cancel) -> (bytes, report)` is `fit_process`, or a
    stand-in in tests; `rebuild(npc, pieces, cancel, say) -> (bytes, report)` is `rebuild_process` with the configuration (a character whose body has face shapes or spring bones is built again instead of fitted),
    or a stand-in; `busy()` is accepted for symmetry with the other background workers and is not used (a fit takes no GPU)."""

    def __init__(self, store, library, fit=None, busy=None, cfg=None, rebuild=None):
        self.store, self.lib = store, library
        self.fit = fit or fit_process
        self.rebuild = rebuild or (lambda npc, pieces, cancel, say: rebuild_process(cfg, npc, pieces, cancel, say))
        self._state = {}                    # (item id, npc id) -> {"state": queued | running | error | cancelled, "message"}
        self._wanted = []                   # [(item id, npc id)]
        self._cancels = {}
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._idle = threading.Event()
        self._idle.set()
        self._stop = False
        self._thread = None

    # ---- what is kept -------------------------------------------------------------------------------------------------------------
    @staticmethod
    def _rel(npc_id, suffix):
        return f"{WORN_DIR}/{npc_id}.{suffix}"

    def _record(self, item_id, npc_id):
        """The record of the fit kept for this item on this character, or None."""
        try:
            path = self.store.resolve(item_id, self._rel(npc_id, "json"))
            record = json.loads(path.read_text("utf-8"))
        except (KeyError, OSError, ValueError):
            return None
        return record if isinstance(record, dict) and record.get("version") == RECORD_VERSION and self.store.resolve(item_id, self._rel(npc_id, "glb")).is_file() else None

    @staticmethod
    def _summary(record):
        report = (record.get("report") or {})
        worn = (report.get("worn") or [{}])[0]
        return {"created": record.get("created"), "seconds": record.get("seconds"), "kind": worn.get("kind"), "cut": worn.get("version"), "triangles": worn.get("triangles"),
                "over_underwear_mm": worn.get("over_underwear_mm"), "replaced": report.get("replaced") or [], "swinging_left_off": report.get("swinging_left_off") or {}, "recipe": record.get("recipe")}

    def status(self, item_id, npc_id):
        """{"item", "npc", "eligible", "why", "state": none | queued | running | done | error | cancelled, "message", "fit": what is kept or None, "stale", "recoloured"}. `stale` says the fit
        kept is not for this item's looseness and length or for this character's body any more; `recoloured` that the colour or the cloth was changed since (the file served has the new one).
        Raises KeyError for an unknown item or character."""
        item = self.store.get(item_id)
        npc = self.lib.get(npc_id)
        eligible, why = clothing.can_wear(npc, item.get("recipe") or {}, kind=item.get("kind"))
        record = self._record(item_id, npc_id)
        stale = recoloured = False
        current = None                                           # what a fit made now would depend on
        recipe = item.get("recipe") or {}
        if items.maker(item.get("family")) == "garment":
            try:
                current = clothing.fit_key(recipe, npc)
            except ValueError:
                current = None
        if record is not None and items.maker(item.get("family")) == "garment":
            stale = current is None or record.get("key") != current
            fitted = record.get("recipe") or {}
            recoloured = any(fitted.get(k) != recipe.get(k) for k in ("colour", "texture"))
        with self._lock:
            row = dict(self._state.get((item_id, npc_id)) or {})
        if row.get("state") in ("error", "cancelled") and row.get("key") is not None and row["key"] != current:
            row = {}                                             # a failure or a stop is news about the garment it was tried with: not about this one (another looseness, another length, another body)
        state = row.get("state") or ("done" if record else "none")
        if state == "done" and not record:
            state = "none"
        return {"item": item_id, "npc": npc_id, "eligible": eligible, "why": why, "state": state, "message": row.get("message", ""),
                "fit": None if record is None else self._summary(record), "stale": stale, "recoloured": recoloured}

    def worn(self, item_id, npc_id):
        """(the character's model with this garment on, {"stale", "recoloured", "fit"}): the file kept, with the item's colour and cloth if they changed since the fit. Raises KeyError
        for an unknown item or character and ValueError when no fit is kept."""
        status = self.status(item_id, npc_id)
        if status["fit"] is None:
            raise ValueError("This garment has not been fitted to this character yet")
        glb = self.store.read_asset(item_id, self._rel(npc_id, "glb"))
        if status["recoloured"]:
            glb = clothing.recolour(glb, self.store.get(item_id)["recipe"])
        return glb, {"stale": status["stale"], "recoloured": status["recoloured"], "fit": status["fit"]}

    # ---- asking -----------------------------------------------------------------------------------------------------------------------
    def request(self, item_id, npc_id, force=False):
        """Fit this garment to this character soon. With no `force` a fit that is kept and still good for the garment's looseness and length and for the body is left as it is. Returns status().
        Raises KeyError (no such item or character) and ValueError (not a garment, or a body that cannot wear it)."""
        item = self.store.get(item_id)
        npc = self.lib.get(npc_id)
        if items.maker(item.get("family")) != "garment":
            raise ValueError("Only fitted clothes are put on a body")
        eligible, why = clothing.can_wear(npc, item.get("recipe") or {}, kind=item.get("kind"))
        if not eligible:
            raise ValueError(why)
        with self._lock:
            running = (self._state.get((item_id, npc_id)) or {}).get("state") in ("queued", "running")
        if running:
            return self.status(item_id, npc_id)
        if not force:
            status = self.status(item_id, npc_id)
            if status["fit"] is not None and not status["stale"]:
                return status
        with self._lock:
            self._state[(item_id, npc_id)] = {"state": "queued", "message": "Waiting its turn"}
            self._wanted.append((item_id, npc_id))
            self._idle.clear()
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._loop, name="dressings", daemon=True)
                self._thread.start()
        self._wake.set()
        return self.status(item_id, npc_id)

    def cancel(self, item_id, npc_id):
        """Stop this fit, whether it is waiting or running. Returns status()."""
        key = (item_id, npc_id)
        with self._lock:
            self._wanted = [w for w in self._wanted if w != key]
            event = self._cancels.get(key)
            row = self._state.get(key)
            if row and row["state"] == "queued":
                self._state[key] = {"state": "cancelled", "message": "Stopped"}
        if event is not None:
            event.set()
        return self.status(item_id, npc_id)

    def forget_item(self, item_id):
        """Drop what is remembered of an item (it was deleted: its folder, and the fits in it, went with it)."""
        with self._lock:
            for key in [k for k in self._state if k[0] == item_id]:
                del self._state[key]

    def forget_npc(self, npc_id):
        """A character was deleted: the fits kept for it go, in every item."""
        with self._lock:
            for key in [k for k in self._state if k[1] == npc_id]:
                del self._state[key]
        for card in self.store.list():
            for suffix in ("glb", "json"):
                try:
                    self.store.resolve(card["id"], self._rel(npc_id, suffix)).unlink(missing_ok=True)
                except (KeyError, OSError):
                    pass

    def wait_idle(self, timeout=30.0):
        """Block until nothing is waiting or being fitted (for tests and the command line)."""
        return self._idle.wait(timeout)

    def close(self, timeout=5.0):
        self._stop = True
        with self._lock:
            events = list(self._cancels.values())
        for event in events:
            event.set()
        self._wake.set()
        thread = self._thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout)

    # ---- the worker ----------------------------------------------------------------------------------------------------------------
    def _loop(self):
        while not self._stop:
            with self._lock:
                wanted = self._wanted.pop(0) if self._wanted else None
                if wanted is None:
                    self._idle.set()
            if wanted is None:
                self._wake.wait(1.0)
                self._wake.clear()
                continue
            event = threading.Event()
            with self._lock:
                self._cancels[wanted] = event
                self._state[wanted] = {"state": "running", "message": "Starting"}
            row = None
            try:
                self._fit_one(*wanted, event)
            except Cancelled:
                row = {"state": "cancelled", "message": "Stopped"}
            except _Gone:
                row = False
            except DressingError as exc:                         # (the Body Designer said no, or the process failed: said as it was told)
                row = {"state": "error", "message": str(exc)}
            except Exception as exc:                             # (one fit failing must not stop the next)
                why = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
                row = {"state": "error", "message": f"The fit stopped unexpectedly ({why}); the character was not changed."}
            with self._lock:
                if row:
                    row["key"] = (self._state.get(wanted) or {}).get("key")        # (what it was tried with: a failure only counts while that is still what the garment and the body are)
                self._cancels.pop(wanted, None)
                if row is None:
                    self._state.pop(wanted, None)
                elif row is False:
                    self._state.pop(wanted, None)
                else:
                    self._state[wanted] = row

    def _say(self, key, text):
        with self._lock:
            row = self._state.get(key)
            if row and row["state"] == "running":
                row["message"] = text

    def _fit_one(self, item_id, npc_id, cancel):
        try:
            item = self.store.get(item_id)
            npc = self.lib.get(npc_id)
        except KeyError:
            raise _Gone() from None
        recipe = clothing.clean_recipe(item.get("recipe"), kind=item.get("kind"))
        plain_rel, animated_rel, designed = clothing.body_of(npc)
        key = clothing.fit_key(recipe, npc)
        with self._lock:
            if (item_id, npc_id) in self._state:
                self._state[(item_id, npc_id)]["key"] = key
        started = now()
        rebuilt = bodymaker.needs_rebuild(designed)
        if rebuilt:                                               # (its face shapes cannot be dressed, and a file with spring bones never goes into the fitter: the body is built again with the garment)
            self._say((item_id, npc_id), f"Building its body again to fit the {recipe['kind']} (about a minute: it has {rebuilt})")
            glb, report = self.rebuild(npc, [recipe], cancel, lambda text: self._say((item_id, npc_id), text))
        else:
            body = self.lib.read_asset(npc_id, animated_rel or plain_rel)
            self._say((item_id, npc_id), f"Fitting the {recipe['kind']} to its body (about half a minute)")
            glb, report = self.fit(body, [recipe], cancel)
        if cancel.is_set():
            raise Cancelled()
        if not report.get("worn"):
            why = (report.get("failed") or {}).get(recipe["kind"]) or "; ".join(report.get("failed", {}).values()) or "it was not put on"
            raise DressingError(f"This {recipe['kind']} does not fit this body: {clothing.explain(why)}")
        record = {"version": RECORD_VERSION, "item": item_id, "npc": npc_id, "key": key, "recipe": recipe, "report": report, "created": now(), "started": started,
                  "seconds": round(sum(w.get("seconds") or 0 for w in report["worn"]), 1)}
        with self.store.lock:                                    # (under the store's lock: a deleted item's folder is not made again for a late fit)
            self.store.get(item_id)
            self.store.write_asset(item_id, self._rel(npc_id, "glb"), glb)
            self.store.write_asset(item_id, self._rel(npc_id, "json"), json.dumps(record, indent=1).encode("utf-8"))
