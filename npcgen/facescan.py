"""Look at a character's face, so glasses (npcgen/facefit.py) and anything else worn on the face can be fitted to it.

Blender renders the character's head from the front (npcgen/blender/face_scan.py `views`), SDPose in ComfyUI finds the 68 face points in that
picture (npcgen/headgraft.py), and Blender lifts them onto the model and reads the front surface of the face (`scan`): the eyes, the width of the
face and a grid of depths. About 40 seconds, one GPU step (SDPose, a few seconds of it). The scan is kept in the character's folder
(face_scan.json) with the model it was made from, so a new model asks for a new scan and an old one is never used on the wrong face.

A model that says where its own eyes are (a designed body, npcgen/bodymaker.py, carries `asset.extras.landmarks`) needs no detector: the eyes are those, the surface is
read exactly, and no GPU is used. When ComfyUI is not running, or no face is found (a helmet, a mask, an animal's face), the eyes are placed by the usual proportions
instead (Blender only, no GPU) and the scan says so: the fit is a starting point to nudge.

`FaceScans` does this one character at a time on a thread of its own, the way npcgen/itemposter.py draws pictures, so a request never waits for
the GPU or for another job's ComfyUI run.
"""
import json
import math
import pathlib
import struct
import tempfile
import threading
import time

import numpy as np
from PIL import Image

from . import gpu, headgraft, rig3d
from .comfy import Cancelled, ComfyError, LockBusy
from .library import now

SCRIPT = pathlib.Path(__file__).resolve().parent / "blender" / "face_scan.py"
CACHE_FILE = "face_scan.json"
TIMEOUT_SECONDS = 600
HEAD_BONE = "head"                  # (any capitals: the animation library calls it Head)
LOW_SCORE = 0.6                     # a mean SDPose score under this is said: a face that is not quite one (a mask, a helmet, an animal) can still be "found"


class FaceScanError(ComfyError):
    """A face could not be scanned. The message is safe to show."""


class _Gone(Exception):
    """The character was deleted while it waited."""


def target(npc):
    """The model a fit is made to: the rigged one when there is one (the head joint then carries the glasses through every clip), else the plain
    model. (kind, file, meta) or None when the character has no 3D model."""
    assets = npc.get("assets") or {}
    for kind in ("rig_3d", "model_3d"):
        meta = assets.get(kind) or {}
        if meta.get("file"):
            return kind, meta["file"], meta
    return None


def stamp(kind, meta):
    """Which model a scan is of."""
    return {"kind": kind, "file": meta.get("file"), "created": meta.get("created"), "size": meta.get("size")}


def summary(scan):
    """What the page shows of a scan (not its grid). The eyes' distance and the face's width are given as if the character were 1.7 m tall, so they
    mean the same whatever units its model is in (63 and 135 mm are an adult's)."""
    scores = scan.get("scores") or []
    face = scan.get("face_width")
    unit = float((scan.get("model") or {}).get("height") or 1.7) / 1.7
    left, right = np.asarray(scan["eyes"]["left"], float), np.asarray(scan["eyes"]["right"], float)
    return {"source": scan.get("source"), "created": scan.get("created"), "seconds": scan.get("seconds"),
            "ipd_mm": round(float(np.linalg.norm(left - right)) / unit * 1000.0, 1),
            "face_width_mm": None if not face else round(float(face) / unit * 1000.0, 1),
            "score": round(float(np.mean(scores)), 2) if scores else None, "notes": list(scan.get("notes") or []),
            "model": scan.get("target")}


def own_eyes(glb):
    """The eyes a model says it has: {"left": [x, y, z], "right": [x, y, z]} in its own (glTF) space from `asset.extras.landmarks` (`eye_l`, `eye_r`:
    the front of each eyeball, as a designed body carries them), or None when it says nothing usable. The left one is the wearer's left (+x)."""
    try:
        length = struct.unpack_from("<I", glb, 12)[0]
        marks = json.loads(glb[20:20 + length])["asset"]["extras"]["landmarks"]
        left, right = [[float(v) for v in marks[key]] for key in ("eye_l", "eye_r")]
    except (struct.error, ValueError, KeyError, TypeError, IndexError):
        return None
    if len(left) != 3 or len(right) != 3 or not all(math.isfinite(v) for v in left + right) or left[0] <= right[0]:
        return None
    return {"left": left, "right": right}


def scan_model(cfg, comfy, model_glb, label, estimate=False, cancel=None, note=None):
    """The face scan (a dict, see npcgen/blender/face_scan.py) of a model (GLB bytes). `label` names the pictures sent to ComfyUI. With
    `estimate`, or when the face cannot be found, the eyes are placed by proportion. `note(text)` hears what is being done. Raises ComfyError
    (Blender missing or failing) and Cancelled. A model with its own eye landmarks is not sent to the detector unless `estimate` asks for the proportions."""
    started = time.monotonic()
    say = note or (lambda text: None)
    notes = []
    with tempfile.TemporaryDirectory(prefix="npcforge-face-") as tmp:
        tmp = pathlib.Path(tmp)
        model, views, out, marks = tmp / "model.glb", tmp / "views", tmp / "scan.json", tmp / "landmarks.json"
        model.write_bytes(model_glb)
        args = ["scan", str(model), str(out)]
        own = None if estimate else own_eyes(model_glb)
        if own:
            (tmp / "eyes.json").write_text(json.dumps(own), "utf-8")
            args += ["--eyes", str(tmp / "eyes.json")]
        elif not estimate:
            say("Drawing the head to look at it")
            rig3d.run_script(cfg, SCRIPT, ["views", str(model), str(views)], tmp, views / "views.json", "Drawing the head",
                             "Blender could not draw the head", cancel=cancel, timeout=TIMEOUT_SECONDS)
            say("Finding the face")
            try:
                picture = Image.open(views / "front.png").convert("RGB")
                points = headgraft.face_points(comfy, picture, label, "front", cancel)
                marks.write_text(json.dumps({"points": points}), "utf-8")
                args += ["--views", str(views / "views.json"), "--landmarks", str(marks)]
            except Cancelled:
                raise
            except ComfyError as exc:
                notes.append(f"The face was not found in the picture ({exc})")
        else:
            notes.append("Placed by proportions, as asked")
        say("Reading the shape of the face" if not own else "Reading the shape of the face (the eyes are the model's own)")
        rig3d.run_script(cfg, SCRIPT, args, tmp, out, "Reading the face", "Blender could not read the face", cancel=cancel,
                         timeout=TIMEOUT_SECONDS)
        scan = json.loads(out.read_text("utf-8"))
    scores = scan.get("scores") or []
    if scores and float(np.mean(scores)) < LOW_SCORE:
        notes.append(f"The face detector was not sure of this face (score {float(np.mean(scores)):.2f}): check the fit, or place the glasses by proportions")
    scan["notes"] = notes
    scan["seconds"] = round(time.monotonic() - started, 1)
    return scan


class FaceScans:
    """Scans characters' faces one at a time on a thread of its own and keeps each result with its character.
    `find(cfg, comfy, glb, label, estimate=, cancel=, note=) -> scan dict` is `scan_model`, or a stand-in in tests; `busy()` says the job worker has
    work (ComfyUI is then left alone afterwards: the worker unloads it when it is done)."""

    def __init__(self, cfg, comfy, library, find=None, busy=None):
        self.cfg, self.comfy, self.lib = cfg, comfy, library
        self.find, self.busy = find or scan_model, busy
        self._state = {}                    # npc id -> {"state": queued | running | error | cancelled | done, "message"}
        self._wanted = []                   # [(npc id, estimate)]
        self._cancels = {}
        self._memo = {}                     # npc id -> ((mtime, size), scan dict)
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._idle = threading.Event()
        self._idle.set()
        self._stop = False
        self._thread = None

    # ---- what is known ------------------------------------------------------------------------------------------------------
    def _read(self, npc_id):
        """The scan file of a character, parsed (and remembered while the file does not change), or None."""
        try:
            path = self.lib.resolve(npc_id, CACHE_FILE)
            stat = path.stat()
        except (KeyError, OSError):
            return None
        key = (stat.st_mtime_ns, stat.st_size)
        with self._lock:
            kept = self._memo.get(npc_id)
        if kept and kept[0] == key:
            return kept[1]
        try:
            scan = json.loads(path.read_text("utf-8"))
        except (OSError, ValueError):
            return None
        with self._lock:
            self._memo[npc_id] = (key, scan)
        return scan

    def load(self, npc_id, npc=None):
        """The character's scan if there is one made from the model it has now, else None. Raises KeyError for an unknown character."""
        npc = npc or self.lib.get(npc_id)
        found = target(npc)
        scan = self._read(npc_id)
        if found is None or scan is None or scan.get("version") != 1 or scan.get("target") != stamp(found[0], found[2]):
            return None
        return scan

    def status(self, npc_id):
        """{"npc", "model": {kind, file, created} or None, "state": none | queued | running | done | error | cancelled, "message", "scan": summary or None}.
        Raises KeyError for an unknown character."""
        npc = self.lib.get(npc_id)
        found = target(npc)
        scan = self.load(npc_id, npc)
        with self._lock:
            row = dict(self._state.get(npc_id) or {})
        state = row.get("state") or ("done" if scan else "none")
        if state == "done" and not scan:
            state = "none"
        return {"npc": npc_id, "model": None if found is None else {"kind": found[0], "file": found[1], "created": found[2].get("created")},
                "state": state, "message": row.get("message", ""), "scan": None if scan is None else summary(scan)}

    # ---- asking ----------------------------------------------------------------------------------------------------------------
    def request(self, npc_id, force=False, estimate=False):
        """Scan this character's face soon. With no `force` a scan that is already there for the model is left as it is. Returns status().
        Raises KeyError (no such character) and FaceScanError (nothing to scan)."""
        npc = self.lib.get(npc_id)
        if target(npc) is None:
            raise FaceScanError("This character has no 3D model yet: make one (or import one) first")
        with self._lock:
            running = (self._state.get(npc_id) or {}).get("state") in ("queued", "running")
        if running or (not force and self.load(npc_id, npc) is not None):
            return self.status(npc_id)
        with self._lock:
            self._state[npc_id] = {"state": "queued", "message": "Waiting its turn"}
            self._wanted.append((npc_id, bool(estimate)))
            self._idle.clear()
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._loop, name="face-scans", daemon=True)
                self._thread.start()
        self._wake.set()
        return self.status(npc_id)

    def cancel(self, npc_id):
        """Stop this character's scan, whether it is waiting or running. Returns status()."""
        with self._lock:
            self._wanted = [w for w in self._wanted if w[0] != npc_id]
            event = self._cancels.get(npc_id)
            row = self._state.get(npc_id)
            if row and row["state"] == "queued":
                self._state[npc_id] = {"state": "cancelled", "message": "Stopped"}
        if event is not None:
            event.set()
        return self.status(npc_id)

    def forget(self, npc_id):
        """Drop what is remembered of a character (it was deleted, or its model was)."""
        with self._lock:
            self._memo.pop(npc_id, None)
            self._state.pop(npc_id, None)

    def wait_idle(self, timeout=30.0):
        """Block until nothing is waiting or being scanned (for tests and the command line)."""
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

    # ---- the worker -----------------------------------------------------------------------------------------------------------
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
            npc_id, estimate = wanted
            event = threading.Event()
            with self._lock:
                self._cancels[npc_id] = event
                self._state[npc_id] = {"state": "running", "message": "Starting"}
            used_comfy = True                                    # (until a scan is known to have had no use for it)
            try:
                used_comfy = self._scan_one(npc_id, estimate, event)
                row = {"state": "done", "message": ""}
            except Cancelled:
                row = {"state": "cancelled", "message": "Stopped"}
            except _Gone:
                row = None
            except Exception as exc:                             # (one scan failing must not stop the next)
                why = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
                row = {"state": "error", "message": f"The face scan stopped unexpectedly ({why})."}
            with self._lock:
                self._cancels.pop(npc_id, None)
                if row is None:
                    self._state.pop(npc_id, None)
                else:
                    self._state[npc_id] = row
            if used_comfy:
                self._release_gpu()

    def _worded(self, text):
        """What the page is told: asking ComfyUI for the face waits for the job that is using it, and the page should know that is why it is slow."""
        if text == "Finding the face" and self.busy is not None and self.busy():
            return text + " (ComfyUI is busy with a job: this waits for it)"
        return text

    def _say(self, npc_id, text):
        with self._lock:
            row = self._state.get(npc_id)
            if row and row["state"] == "running":
                row["message"] = text

    def _scan_one(self, npc_id, estimate, cancel):
        try:
            npc = self.lib.get(npc_id)
        except KeyError:
            raise _Gone() from None
        found = target(npc)
        if found is None:
            raise FaceScanError("This character has no 3D model")
        kind, rel, meta = found
        glb = self.lib.read_asset(npc_id, rel)
        scan = self.find(self.cfg, self.comfy, glb, npc_id, estimate=estimate, cancel=cancel, note=lambda text: self._say(npc_id, self._worded(text)))
        scan["target"] = stamp(kind, meta)
        scan["created"] = now()
        if cancel.is_set():
            raise Cancelled()
        self.lib.write_asset(npc_id, CACHE_FILE, json.dumps(scan).encode("utf-8"))
        return scan.get("source") == "sdpose"                   # whether ComfyUI was used (a scan from landmarks or proportions did not touch it)

    def _release_gpu(self):
        """SDPose was the last thing ComfyUI ran: when the job worker has nothing to do it would otherwise leave it on the card."""
        lock = getattr(self.comfy, "operation_lock", None)
        if lock is None or (self.busy is not None and self.busy()):
            return
        try:
            lock.acquire(timeout=0)
        except LockBusy:
            return
        try:
            gpu.release_comfy(self.comfy)
        except Exception:
            pass
        finally:
            lock.release()
