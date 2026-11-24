"""Background worker: turns generate requests into ComfyUI runs, one step at a time (there is one GPU)."""
import collections
import copy
import heapq
import io
import json
import os
import pathlib
import queue
import random
import re
import shutil
import sys
import threading
import time
import traceback
import uuid

from PIL import Image

from . import best3d, bodymaker, check3d, cleanup3d, creatures, creaturemaker, engines, eyes, eyesoft, glassesoff, gpu, hairmask, hands3d, head3d, headdetail, headgraft, import3d, itemmaker, items, living, morphology, posekeys, prompts, rig3d, roll, scene_background, scene_voice, three_d
from .anim import Animator, generated_directions
from .voice import steps as voice_steps
from .voice import binding as voice_binding
from .voice.runtime import VoiceRuntime
from .comfy import Cancelled, ComfyError
from .keying import grey_png, remove_background
from .library import now
from .sheet import Sheeter

DEFAULT_OPTIONS = {
    "quality": "standard",
    "sprite_mode": "auto",           # "match": edit the portrait into a full-body sprite; "independent": text-to-image; "auto": see below
    "transparent_sprite": True,
    "transparent_portrait": False,
    "save_candidates": False,         # save portrait/sprite rerolls beside the active image for later selection
    "sprite_view": "front",
    "seed": None,
    "reroll": False,                 # regenerating an existing asset: use a fresh random seed
    "image_model": "qwen2512",       # the engine that draws from text: portraits, independent sprites (see npcgen/engines.py; Qwen-Image 2512 is Apache-2.0, Mage-Flow's licence is unclear)
    "edit_model": "qwen2511",        # the engine that edits: photo portraits, matched sprites, expressions, sheets, animation (Qwen-Image-Edit 2511, Apache-2.0)
    "three_d_model": "hunyuan21",    # local ComfyUI 3D generator (Hunyuan3D v2.1, Pixal3D or Trellis2)
    "three_d_quality": "medium",     # Pixal3D mesh/texture balance (low, medium, high); Trellis2: the faces kept; ignored by Hunyuan
    "three_d_face_source": "turnaround",  # Pixal3D close-up face-texture reference
    "three_d_soften_eyes": False,    # Trellis2: the eyes filled with skin in the pictures its SHAPE is made from (npcgen/eyesoft.py)
    "three_d_glasses_off": False,    # Trellis2: the glasses taken off the pictures the model is made and painted from (npcgen/glassesoff.py)
    "three_d_hunyuan_source": "sprite",   # Hunyuan3D's picture: the sprite, or the model sheet's front (best for painting)
    "three_d_hunyuan_detail": "standard",  # Hunyuan3D's shape grid: standard (256) or finer (slower, smoother faces)
    "rig_skeleton": "low",           # the skeleton a rig gets: low (23 bones) or medium (60: fingers, jaw...; npcgen/rig3d.py)
    "rig_clips": "auto",             # its clips: basic (Idle, Walk, Wave) or library (Quaternius'; auto = library when installed)
    "head_quality": "medium",        # the detailed head's Pixal3D setting: medium or high (npcgen/head3d.py)
    "head_expression": "neutral",    # its face: neutral (best for animating) or as in the portrait
    "head_match_hair": False,        # paint its hair only from hair (SAM 3.1, npcgen/hairmask.py): only when ticked
    "engine_fallback": False,        # False (since 2026-10-13): a picture engine that cannot draw (not enough free memory, files missing) fails the step with the
                                     # reason instead of Mage-Flow drawing it, so no picture is quietly drawn by a model whose licence is unclear; True: Mage-Flow draws it and says so
    "portrait_framing": "bust",      # how much of the character the portrait shows: bust, half (to the waist) or full (npcgen/prompts.py)
    "game_faces": cleanup3d.GAME_FACES,   # the clean-up's game version: how many faces (a preset name or a number; npcgen/cleanup3d.py)
}
ENGINE_NOTE = "Drawn with the built-in engine instead: "
STALLED = ("Nothing happened in this step for {minutes:g} minutes, so it was stopped. ComfyUI, Blender or another program it waits "
           "for may be stuck: look at what is running, then retry the job.")

# Why a step failed, for programs that read the job list (GET /api/jobs: steps[].code and the job's own code) instead of the
# English messages, which stay as they were: gpu_busy (another program holds the GPU; the step also names it in `holders`),
# gpu_comfy_held (ComfyUI did not give its GPU memory back), comfy_busy (ComfyUI busy with something else, or another program
# holding it), comfy_down (ComfyUI not answering), no_model (a model file ComfyUI does not have), low_memory (too little free
# RAM or VRAM for a big engine), stalled (no sign of life), cancelled, error (anything else).
GPU_REFUSALS = ("gpu_busy", "gpu_comfy_held", "comfy_busy")      # the GPU or ComfyUI is taken: every later step would wait and fail the same way
NOT_RUN = {                                                      # the reason a step that was never started gives, after one of these stopped its job
    "gpu_busy": "not run: another program holds the GPU",
    "gpu_comfy_held": "not run: ComfyUI did not give its GPU memory back",
    "comfy_busy": "not run: ComfyUI was busy with something else",
    "comfy_down": "not run: ComfyUI could not be reached",
}
STEP_FIELDS = ("detail", "started_at", "last_progress_at", "attempts", "code", "holders", "device", "reason")   # what a run leaves on a step


class StepSkipped(Exception):
    """A step with nothing to work on because an earlier step of the same job failed (no expression made, nothing to assemble):
    it is not run and it is not an error of its own. The message says why."""


def failure_code(exc):
    """The machine-readable reason of a step's failure (see GPU_REFUSALS above): the error's own `code` when it has one, else
    read from its words (an older error, or a stand-in in a test)."""
    code = getattr(exc, "code", None)
    if code:
        return code
    text = str(exc)
    if isinstance(exc, gpu.GpuBusy) or "one model on the GPU at a time" in text:
        if "Another program is using the GPU" in text:
            return "gpu_busy"
        if "still busy with another job" in text:
            return "comfy_busy"
        return "gpu_comfy_held"
    if "Cannot reach ComfyUI" in text:
        return "comfy_down"
    if "ComfyUI is missing" in text or "Value not in list" in text:
        return "no_model"
    if getattr(exc, "transient", False):
        return "low_memory"
    return "error"

# A local extension can draw portraits with its own engine (another text-to-image model). Every hook is called for every portrait:
#   hook(npc=, setting=, prompt=, size=, seed=, comfy=, cancel=)  ->  None (not mine) | {"png": bytes, "engine": "name"}
# It raises PortraitFallback(reason) when the portrait is its own but it cannot run right now: the built-in engine draws it
# instead and the reason is stored with the picture (and shown on the job).
PORTRAIT_ENGINES = []


def _replace_model_3d(npc, info):
    """A new model: any skeleton made for the old one no longer fits it."""
    assets = npc.setdefault("assets", {})
    assets["model_3d"] = info
    if "rig_3d" in assets:
        assets["rig_3d"]["stale"] = True


def use_uploaded_model(lib, npc_id, data, name):
    """Make a .glb file from the owner's computer the character's 3D model exactly as it is, and return the character with what was found in the file ({"upload": {faces, textured,
    warnings}}). Nothing is closed, simplified or painted (importing a file from ComfyUI's folders can do the first two, the clean-up under the model the others). The model it replaces
    is kept among the earlier ones. ValueError, in words, for a file that cannot be used; nothing is changed then."""
    try:
        info = import3d.inspect(data)
    except import3d.Incomplete:
        raise ValueError("That file is shorter than its own header says: it is damaged, or did not arrive whole") from None
    if info["bytes"] != len(data):
        raise ValueError(f"That file is incomplete: its header says {info['bytes']:,} bytes and {len(data):,} arrived")
    label = re.sub(r"[^A-Za-z0-9_. -]", "_", pathlib.PurePath(str(name or "model.glb").replace("\\", "/")).name)[:80] or "model.glb"
    lib.archive_model(npc_id)                                   # the model this one replaces is kept among the earlier ones
    lib.write_asset(npc_id, "model_3d.glb", data)
    for rel in ("model_3d_preview.png", cleanup3d.ORIGINAL_FILE):                    # what the old model left is not this one's
        try:
            lib.resolve(npc_id, rel).unlink()
        except FileNotFoundError:
            pass
    shutil.rmtree(lib.resolve(npc_id, "model_3d_head"), ignore_errors=True)
    meta = {"file": "model_3d.glb", "created": now(), "size": len(data), "format": "glb", "engine": f"Uploaded: {label}", "engine_id": "import",
            "textured": bool(info["textured"]),
            "imported": {"name": label, "folder": "upload", "faces_in": info["faces"], "faces": info["faces"], "cleaned": False, "seconds": 0}}
    npc = lib.update(npc_id, lambda n: _replace_model_3d(n, meta))
    return {**npc, "upload": {"faces": info["faces"], "textured": bool(info["textured"]), "warnings": info["warnings"]}}


STALE_KINDS = ("portrait", "sprite", "sheet", "living", "model_3d", "rig_3d", "head_3d", "expressions", "animation_views", "animations")


def mark_assets_stale(npc, keep_fresh=()):
    """Mark every generated result stale after prompt-driving character data changes, except the kinds in `keep_fresh`: an edit
    the caller knows does not change them (a portrait's framing is fixed without touching the sprite or the 3D model)."""
    assets = npc.get("assets", {})
    keep = set(keep_fresh)
    for kind in ("portrait", "sprite", "sheet", "living", "model_3d", "rig_3d", "head_3d"):
        if kind in assets and kind not in keep:
            assets[kind]["stale"] = True
    if "sheet" not in keep:
        for view in assets.get("sheet", {}).get("views", {}).values():
            view["stale"] = True
    for collection in ("expressions", "animation_views", "animations"):
        if collection in keep:
            continue
        for meta in assets.get(collection, {}).values():
            meta["stale"] = True


def clear_stale(npc, kinds):
    """The opposite of mark_assets_stale: say that these kinds are still right after an edit, so their 'stale' flag goes (the files are not touched)."""
    assets, keep = npc.get("assets", {}), set(kinds)
    for kind in ("portrait", "sprite", "sheet", "living", "model_3d", "rig_3d", "head_3d"):
        if kind in keep and kind in assets:
            assets[kind].pop("stale", None)
    if "sheet" in keep:
        for view in assets.get("sheet", {}).get("views", {}).values():
            view.pop("stale", None)
    for collection in ("expressions", "animation_views", "animations"):
        if collection in keep:
            for meta in assets.get(collection, {}).values():
                meta.pop("stale", None)


class PortraitFallback(Exception):
    """An extension's portrait engine applies to this character but cannot run; the built-in engine draws it instead."""


def add_portrait_engine(fn):
    PORTRAIT_ENGINES.append(fn)


def resolve_sprite_mode(option, freeform, has_portrait):
    """Which way the full-body sprite is made. 'auto' edits the portrait for ordinary characters (measured to keep the face
    and outfit in all three styles) but draws freeform ones from their description: in a test, editing a robot's
    shoulders-up portrait into a full body lost its teapot-shaped head, while the description kept it."""
    if not has_portrait:
        return "independent"
    if option == "auto":
        return "independent" if freeform else "match"
    return option


def _png_size(png):
    with Image.open(io.BytesIO(png)) as im:
        return list(im.size)


def _setting(conf, name, default):
    """A non-negative number of config.json's "jobs" section, else the default."""
    try:
        value = float(conf.get(name, default))
    except (TypeError, ValueError):
        return default
    return value if value >= 0 else default


class JobQueue:
    """FIFO with a quick lane: quick jobs (the voice designer's takes and test lines) run before ordinary ones, but always
    between jobs, never inside one, so a long animation never waits for a preview and a preview never waits for it to end.
    None (the shutdown sentinel) goes first of all. Same get / get_nowait / put / empty as queue.Queue."""
    QUICK, NORMAL = 1, 2

    def __init__(self):
        self._items = []
        self._seq = 0
        self._cond = threading.Condition()

    def put(self, job, priority=NORMAL):
        with self._cond:
            self._seq += 1
            heapq.heappush(self._items, (0 if job is None else priority, self._seq, job))
            self._cond.notify()

    def get(self, block=True, timeout=None):
        with self._cond:
            end = None if timeout is None else time.monotonic() + timeout
            while not self._items:
                left = None if end is None else end - time.monotonic()
                if not block or (left is not None and left <= 0):
                    raise queue.Empty
                self._cond.wait(left)
            return heapq.heappop(self._items)[2]

    def get_nowait(self):
        return self.get(block=False)

    def empty(self):
        with self._cond:
            return not self._items


class JobSignal(threading.Event):
    """A job's cancel flag. The long waits (ComfyUI, the GPU hand-off, the ComfyUI lock, Blender) also call beat() on it
    (npcgen.comfy.beat): that is how a job learns that its step is alive, which is the clock of the watchdog, and what it is
    waiting for, which the queue shows beside the step."""

    def __init__(self, job):
        super().__init__()
        self.job = job

    def beat(self, note=None):
        self.job.beat(note)


class Job:
    def __init__(self, npc_id, npc_name, steps, options, target_type="npc", scene_id=None, item_id=None, creature_id=None):
        self.id = uuid.uuid4().hex[:10]
        self.npc_id, self.npc_name, self.options = npc_id, npc_name, options
        self.target_type, self.scene_id, self.item_id = target_type, scene_id, item_id
        self.creature_id = creature_id
        self.steps = steps
        self.status = "queued"       # queued | running | done | error | cancelled
        self.error = None
        self.code = None             # why it failed or stopped, machine-readable (failure_code; "cancelled"); None while all is well
        self.stop_note = None        # set when a failed step stopped the job: the reason the steps after it give for not running
        self.created = now()
        self.started = self.finished = None
        self.cancel = JobSignal(self)
        self.cancel_reason = None    # user | shutdown | stalled
        self.cancel_at = None        # when the running job was asked to stop (monotonic clock), for the watchdog
        self.alive_at = time.monotonic()   # the last sign of life of its running step (monotonic clock)
        self.running_step = None
        self.waiting_for = None      # the text beat() put in the running step's detail, so only that text is taken out again
        self.resume_on_start = False
        self.version = 0
        self.ref = None              # (ComfyUI image name, portrait stamp) of the uploaded reference
        self.cache = {}              # per-job scratch space for the animation steps (uploaded references, cut-outs)
        self.on_change = None
        self.lock = threading.RLock()

    def new_cancel(self):
        """A fresh cancel flag, for a job that is queued again."""
        self.cancel = JobSignal(self)
        self.cancel_reason = self.cancel_at = None

    def touch(self):
        self.version += 1
        if self.on_change:
            self.on_change()

    def beat(self, note=None):
        """A sign of life from the running step: it restarts the watchdog's clock and stamps the step. `note` says what the step
        waits for (None: it waits for nothing now); the queue shows it beside the step."""
        changed = False
        with self.lock:
            self.alive_at = time.monotonic()
            step = self.running_step
            if step is None:
                return
            step["last_progress_at"] = now()
            if note != self.waiting_for:
                if note:
                    step["detail"] = note
                elif step.get("detail") == self.waiting_for:
                    step.pop("detail", None)
                self.waiting_for = note
                changed = True
        if changed:
            self.touch()

    def to_record(self):
        with self.lock:
            return {
                "id": self.id, "npc_id": self.npc_id, "npc_name": self.npc_name, "options": copy.deepcopy(self.options),
                "target_type": self.target_type, "scene_id": self.scene_id, "item_id": self.item_id, "creature_id": self.creature_id,
                "steps": copy.deepcopy(self.steps), "status": self.status, "error": self.error, "code": self.code, "created": self.created,
                "started": self.started, "finished": self.finished,
                "cancel_reason": self.cancel_reason, "resume_on_start": self.resume_on_start, "version": self.version,
            }

    @classmethod
    def from_record(cls, record):
        job = cls(record["npc_id"], record["npc_name"], record["steps"], record["options"],
                  record.get("target_type", "npc"), record.get("scene_id"), record.get("item_id"), record.get("creature_id"))
        job.id = record["id"]
        job.status = record["status"]
        job.error = record.get("error")
        job.code = record.get("code")
        job.created = record["created"]
        job.started, job.finished = record.get("started"), record.get("finished")
        job.cancel_reason = record.get("cancel_reason")
        job.resume_on_start = bool(record.get("resume_on_start"))
        job.version = int(record.get("version", 0))
        return job

    def to_dict(self):
        with self.lock:
            return {
                "id": self.id, "npc_id": self.npc_id, "npc_name": self.npc_name, "status": self.status, "error": self.error,
                "code": self.code, "target_type": self.target_type, "scene_id": self.scene_id, "item_id": self.item_id, "creature_id": self.creature_id,
                "created": self.created, "started_at": self.started, "finished_at": self.finished,
                "version": self.version, "steps": [dict(s) for s in self.steps],
                "done": sum(1 for s in self.steps if s["status"] == "done"), "total": len(self.steps),
            }


class Jobs:
    def __init__(self, cfg, comfy, library, state_path=None, scenes=None, items=None, creature_store=None):
        self.cfg, self.comfy, self.lib = cfg, comfy, library
        self.scenes = scenes
        self.items = items
        self.creatures = creature_store
        gpu.configure(cfg)
        self.animator = Animator(comfy, library, cfg)
        self.sheeter = Sheeter(comfy, library, cfg)
        self.headmaker = head3d.Headmaker(comfy, library, cfg)
        self.voice = VoiceRuntime(cfg, library.root / ".voice-logs", comfy)    # starts nothing until a voice step runs
        self.queue = JobQueue()
        self.jobs = collections.OrderedDict()
        self.lock = threading.RLock()
        self.persist_lock = threading.Lock()
        self.state_path = pathlib.Path(state_path) if state_path else library.root / ".npcforge-jobs.json"
        self.closed = False
        conf = (cfg or {}).get("jobs") or {}
        self.stall_seconds = _setting(conf, "stall_minutes", 45) * 60          # a step silent this long is stopped (see _watch)
        self.cancel_grace = _setting(conf, "cancel_grace_seconds", 120)        # a step asked to stop gets this long to do so
        self.retries = int(_setting(conf, "transient_retries", 2))             # a GPU hand-off that failed is tried again this often
        self.retry_pause = _setting(conf, "retry_pause_seconds", 30)
        self.watch_every = _setting(conf, "watch_seconds", 5) or 5.0           # how often the watchdog looks
        self.current = None             # (job, step) the worker is running, for the watchdog
        self._epoch = 0                 # raised when the watchdog gives up on a stuck worker: that thread then leaves everything alone
        self._restore()
        self.thread = threading.Thread(target=self._worker, daemon=True, name="npc-worker")
        self.thread.start()
        threading.Thread(target=self._watch, daemon=True, name="npc-watchdog").start()

    def _restore(self):
        try:
            records = json.loads(self.state_path.read_text("utf-8"))
        except FileNotFoundError:
            return
        except (OSError, ValueError, TypeError) as exc:
            print(f"Could not read saved NPC Forge jobs from {self.state_path}: {exc}", file=sys.stderr)
            return
        if not isinstance(records, list):
            print(f"Saved NPC Forge jobs in {self.state_path} are not a list; ignoring them.", file=sys.stderr)
            return
        recovered = False
        for record in records:
            try:
                self._expand_bindings(record)
                job = Job.from_record(record)
                if job.status not in ("queued", "running", "done", "error", "cancelled"):
                    continue
            except (KeyError, TypeError, ValueError):
                continue
            if job.cancel_reason == "user" and job.status in ("queued", "running"):
                job.status, job.code = "cancelled", "cancelled"
                for step in job.steps:
                    if step.get("status") in ("pending", "running"):
                        step["status"] = "cancelled"
                recovered = True
            elif job.status in ("queued", "running") or (job.status == "cancelled" and job.resume_on_start):
                for step in job.steps:
                    if step.get("status") in ("running", "cancelled", "error"):
                        step["status"] = "pending"
                        step["error"] = None
                        for stale in STEP_FIELDS:
                            step.pop(stale, None)
                job.status, job.error, job.code = "queued", None, None
                job.resume_on_start = False
                job.new_cancel()
                self.queue.put(job, self._priority(job))
                recovered = True
            job.on_change = self._persist
            job.lock = self.lock
            self.jobs[job.id] = job
        if recovered:
            self._persist()

    # A voice job freezes the files its engine runs from (npcgen/voice/binding.capture): thousands of paths and checksums, the same
    # for every job of that engine. Saved inside each job record they made the job file megabytes long, rewritten on every step, so
    # each binding is kept once, in a file named by its own checksum beside the job file, and the record holds {"stored": <checksum>}.
    @property
    def bindings_dir(self):
        return self.state_path.parent / ".npcforge-voice-bindings"

    def _shrink_bindings(self, row):
        """`row` (a to_record copy) with each frozen voice binding written to bindings_dir once and replaced by a reference."""
        runtime = (row.get("options") or {}).get("voice_runtime")
        if not isinstance(runtime, dict):
            return row
        slim = {}
        for engine, value in runtime.items():
            key = value.get("id") if isinstance(value, dict) else None
            if isinstance(key, str) and re.fullmatch(r"[0-9a-f]{64}", key):
                path = self.bindings_dir / f"{key}.json"
                if not path.exists():
                    path.parent.mkdir(parents=True, exist_ok=True)
                    tmp = path.with_suffix(".tmp")
                    tmp.write_text(json.dumps(value, ensure_ascii=False), "utf-8")
                    os.replace(tmp, path)
                slim[engine] = {"stored": key}
            else:
                slim[engine] = value                       # {"unavailable": reason} and anything unknown stay as they are
        row["options"]["voice_runtime"] = slim
        return row

    def _expand_bindings(self, record):
        """Put each stored binding back into a saved record (older records that carry the whole binding are left alone). A binding
        whose file is gone becomes unavailable, so its voice step fails with a reason instead of running unchecked."""
        runtime = (record.get("options") or {}).get("voice_runtime") if isinstance(record, dict) else None
        if not isinstance(runtime, dict):
            return record
        for engine, value in list(runtime.items()):
            key = value.get("stored") if isinstance(value, dict) else None
            if not isinstance(key, str) or not re.fullmatch(r"[0-9a-f]{64}", key):
                continue
            try:
                runtime[engine] = json.loads((self.bindings_dir / f"{key}.json").read_text("utf-8"))
            except (OSError, ValueError):
                runtime[engine] = {"unavailable": "The saved record of this voice engine's files is missing; submit the job again."}
        return record

    def _sweep_bindings(self, rows):
        """Remove binding files no saved job refers to any more (jobs past the last 30 finished ones are dropped)."""
        wanted = {value["stored"] for row in rows for value in ((row.get("options") or {}).get("voice_runtime") or {}).values()
                  if isinstance(value, dict) and isinstance(value.get("stored"), str)}
        try:
            for path in self.bindings_dir.glob("*.json"):
                if path.stem not in wanted:
                    path.unlink(missing_ok=True)
        except OSError:
            pass

    def _persist(self):
        with self.persist_lock:
            with self.lock:
                rows = [job.to_record() for job in self.jobs.values()]
            tmp = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
            try:
                self.state_path.parent.mkdir(parents=True, exist_ok=True)
                rows = [self._shrink_bindings(row) for row in rows]
                tmp.write_text(json.dumps(rows, indent=2, ensure_ascii=False), "utf-8")
                os.replace(tmp, self.state_path)
                self._sweep_bindings(rows)
            except (OSError, TypeError, ValueError):
                traceback.print_exc()

    def close(self, timeout=None):
        """Stop this worker, preserving queued and interrupted work for the next launch."""
        changed = []
        with self.lock:
            if self.closed:
                return
            self.closed = True
            for job in self.jobs.values():
                if job.status == "running":
                    if job.cancel_reason is None:
                        job.cancel_reason = "shutdown"
                        job.resume_on_start = True
                    job.cancel.set()
                    changed.append(job)
        for job in changed:
            job.touch()
        # Pending jobs are already durable. Drop them from the in-memory queue so shutdown only
        # waits for the current ComfyUI operation to observe cancellation before the sentinel.
        while True:
            try:
                self.queue.get_nowait()
            except queue.Empty:
                break
        self.queue.put(None)
        with self.lock:
            worker = self.thread                            # (the watchdog may have put a new one in the place of a stuck worker)
        worker.join(timeout)
        self.voice.close()
        self._persist()

    # ---- public API --------------------------------------------------------------------------------------------
    def submit(self, npc, assets, options):
        if assets.get("best_3d"):                          # the chain ticked for this character (npcgen/best3d.py)
            assets, options = best3d.expand(npc, assets, options)
        body_plan = morphology.resolve(npc, roll.load_data())
        reason = morphology.unsupported_reason(body_plan, assets) or bodymaker.refuses(npc, assets)
        if reason:
            raise ValueError(reason)
        steps = self.plan(npc, assets, options)
        p = npc["profile"]
        for extra in ("animations", "sheet", "voice", "import_3d", "body_3d"):
            if assets.get(extra):
                options = {**options, extra: assets[extra]}
        if any(step['kind'].startswith('voice_') for step in steps):
            options={**options,'voice_runtime':voice_binding.for_npc_job(self.cfg,npc,options,steps)}
        job = Job(npc["id"], f'{p["first_name"]} {p["last_name"]}'.strip(), steps, options)
        job.on_change = self._persist
        job.lock = self.lock
        with self.lock:
            if self.closed:
                raise RuntimeError("The job queue has been closed")
            self.jobs[job.id] = job
            finished = [k for k, j in self.jobs.items() if j.status in ("done", "error", "cancelled")]
            for k in finished[:-30]:
                del self.jobs[k]
            self.queue.put(job, self._priority(job))
        self._persist()
        return job.to_dict()

    def submit_scene_voice(self, scene_id, raw):
        if self.scenes is None:
            raise RuntimeError("Scene store is unavailable")
        scene = self.scenes.get(scene_id)
        beats = scene_voice.requests(scene, self.scenes, raw)
        steps = [{"kind": "scene_voice", "key": beat_id, "label": f"Voice scene line: {beat_id}",
                  "status": "pending", "error": None} for beat_id in beats]
        job = Job(None, scene["title"], steps, {"scene_voice": {"beats": beats, "quick": len(beats) == 1},
                  "voice_runtime":voice_binding.for_scene_job(self.cfg,self.lib,beats)},
                  target_type="scene", scene_id=scene_id)
        job.on_change = self._persist
        job.lock = self.lock
        with self.lock:
            if self.closed:
                raise RuntimeError("The job queue has been closed")
            self.jobs[job.id] = job
            finished = [k for k, j in self.jobs.items() if j.status in ("done", "error", "cancelled")]
            for k in finished[:-30]:
                del self.jobs[k]
            self.queue.put(job, self._priority(job))
        self._persist()
        return job.to_dict()

    def submit_scene_background(self, scene_id, options=None):
        """`options` (service.clean_options) names the picture engine and whether Mage-Flow may draw the plate when that engine cannot;
        a request that names none gets the app's defaults (npcgen/scene_background.py)."""
        if self.scenes is None:
            raise RuntimeError("Scene store is unavailable")
        scene = self.scenes.get(scene_id)
        if scene.get("mode") != "stage3d" or not scene.get("background_prompt"):
            raise ValueError("Save a 3D scene with a background description first")
        steps = [{"kind": "scene_background", "key": None, "label": "Generate 3D stage background",
                  "status": "pending", "error": None}]
        engine = {key: options[key] for key in ("image_model", "engine_fallback") if options and key in options}
        job = Job(None, scene["title"], steps, {"scene_background": {"prompt": scene["background_prompt"]}, **engine},
                  target_type="scene", scene_id=scene_id)
        job.on_change = self._persist
        job.lock = self.lock
        with self.lock:
            if self.closed:
                raise RuntimeError("The job queue has been closed")
            self.jobs[job.id] = job
            self.queue.put(job, self._priority(job))
        self._persist()
        return job.to_dict()

    def submit_item(self, item_id, request, options):
        """Queue the steps an item needs (npcgen/items.py plan): `request` says which, `options` is items.clean_options."""
        if self.items is None:
            raise RuntimeError("Item store is unavailable")
        item = self.items.get(item_id)
        steps = items.plan(item, request)
        options = {**(options or {}), "engine_fallback": False}      # an item's pictures are never drawn by Mage-Flow (see DEFAULTS)
        job = Job(None, item["name"], steps, options, target_type="item", item_id=item_id)
        job.on_change = self._persist
        job.lock = self.lock
        with self.lock:
            if self.closed:
                raise RuntimeError("The job queue has been closed")
            self.jobs[job.id] = job
            finished = [k for k, j in self.jobs.items() if j.status in ("done", "error", "cancelled")]
            for k in finished[:-30]:
                del self.jobs[k]
            self.queue.put(job, self._priority(job))
        self._persist()
        return job.to_dict()

    def busy_item(self, item_id):
        with self.lock:
            return any(j.target_type == "item" and j.item_id == item_id and j.status in ("queued", "running")
                       for j in self.jobs.values())

    def submit_creature(self, creature_id, request, options):
        """Queue the steps a creature needs (npcgen/creatures.py plan): `request` says which, `options` is the job's options."""
        if self.creatures is None:
            raise RuntimeError("Creature store is unavailable")
        creature = self.creatures.get(creature_id)
        steps = creatures.plan(creature, request)
        if not steps:
            raise ValueError("Those steps are already made. Choose them again to redo them.")
        options = {**(options or {}), "engine_fallback": False}      # a creature's concept is never drawn by Mage-Flow (see DEFAULTS)
        job = Job(None, creature["name"], steps, options, target_type="creature", creature_id=creature_id)
        job.on_change = self._persist
        job.lock = self.lock
        with self.lock:
            if self.closed:
                raise RuntimeError("The job queue has been closed")
            self.jobs[job.id] = job
            finished = [k for k, j in self.jobs.items() if j.status in ("done", "error", "cancelled")]
            for k in finished[:-30]:
                del self.jobs[k]
            self.queue.put(job, self._priority(job))
        self._persist()
        return job.to_dict()

    def busy_creature(self, creature_id):
        with self.lock:
            return any(j.target_type == "creature" and j.creature_id == creature_id and j.status in ("queued", "running")
                       for j in self.jobs.values())

    def busy_npc(self, npc_id):
        """Whether a job for this character is waiting or running (its pictures are about to change)."""
        with self.lock:
            return any(j.target_type == "npc" and j.npc_id == npc_id and j.status in ("queued", "running")
                       for j in self.jobs.values())

    def busy_scene(self, scene_id):
        with self.lock:
            return any(j.target_type == "scene" and j.scene_id == scene_id and j.status in ("queued", "running")
                       for j in self.jobs.values())

    def busy(self):
        """Whether any job is waiting or running (something else that uses ComfyUI leaves it alone then)."""
        with self.lock:
            return any(j.status in ("queued", "running") for j in self.jobs.values())

    @staticmethod
    def _priority(job):
        """The voice designer's short jobs (takes, a test line) use the quick lane; everything else waits its turn."""
        return JobQueue.QUICK if ((job.options.get("voice") or {}).get("quick")
                                  or (job.options.get("scene_voice") or {}).get("quick")) else JobQueue.NORMAL

    def snapshot(self):
        with self.lock:
            return [j.to_dict() for j in self.jobs.values()]

    def cancel(self, job_id=None):
        changed = []
        with self.lock:
            targets = [self.jobs[job_id]] if job_id else list(self.jobs.values())
            for job in targets:
                if job.status == "queued":              # not started: cancel right away so the UI updates at once
                    job.cancel.set()
                    job.cancel_reason = "user"
                    job.status, job.code = "cancelled", "cancelled"
                    for step in job.steps:
                        step["status"] = "cancelled"
                    changed.append(job)
                elif job.status == "running":
                    job.cancel.set()
                    job.cancel_reason = "user"
                    job.cancel_at = time.monotonic()
                    changed.append(job)
        for job in changed:
            job.touch()

    def retry(self, job_id):
        with self.lock:
            job = self.jobs.get(job_id)
            if job is None:
                raise KeyError(job_id)
            if job.status not in ("error", "cancelled"):
                raise ValueError("Only a failed or cancelled job can be retried")
            if job.target_type == "scene":
                pending = [step["key"] for step in job.steps if step["status"] in ("running", "error", "cancelled")]
                if pending and job.options.get("scene_voice"):
                    scene = self.scenes.get(job.scene_id)
                    fresh = scene_voice.requests(scene, self.scenes, {"ids": pending, "redo": True})
                    job.options["scene_voice"]["beats"].update(fresh)
                if job.options.get("scene_background"):
                    scene = self.scenes.get(job.scene_id)
                    job.options["scene_background"]["prompt"] = scene["background_prompt"]
            for step in job.steps:
                if step["status"] in ("running", "error", "cancelled"):
                    step["status"] = "pending"
                    step["error"] = None
                    for stale in STEP_FIELDS:
                        step.pop(stale, None)
            job.status, job.error, job.code, job.stop_note = "queued", None, None, None
            job.finished = None
            job.resume_on_start = False
            job.new_cancel()
            self.queue.put(job, self._priority(job))
            result = job.to_dict()
        job.touch()
        return result

    @staticmethod
    def plan(npc, assets, options=None):
        """Ordered steps for a request. Expressions need a portrait; animations, model sheets and 3D models need a sprite,
        so dependencies are added when an NPC does not have a fresh one."""
        def step(kind, key, label):
            return {"kind": kind, "key": key, "label": label, "status": "pending", "error": None}

        steps = []
        exprs = list(assets.get("expressions") or [])
        anim, sheet = assets.get("animations"), assets.get("sheet")
        options = options or {}
        have = npc.get("assets", {})
        imported = assets.get("import_3d")                 # a model made elsewhere: {model_file, clean, detail, textured, ...}
        if assets.get("rig_3d") and "model_3d" not in have and not imported:
            assets = {**assets, "model_3d": True}          # a skeleton needs a model to go in
        engine = three_d.clean(options.get("three_d_model"))
        pixal_3d = assets.get("model_3d") and engine in three_d.FOUR_VIEW_GENERATORS     # made from the sheet's four pictures
        model_3d_needs_sprite = assets.get("model_3d") and (
            "sprite" not in have or have["sprite"].get("stale") or
            (assets.get("portrait") and have["sprite"].get("mode") == "match"))
        # Painting a model with no colour of its own (Hunyuan's, an imported one) needs the turnaround pictures: any missing
        # come first, and a character with no sprite yet (a model imported into a bare character) gets one to draw them from.
        paint_wanted = assets.get("refine_3d") or any(assets.get(flag) for _key, flag in cleanup3d.OPTIONS)
        blank_model = (not three_d.GENERATORS[engine]["textured"] if assets.get("model_3d")
                       else not imported.get("textured") if imported
                       else "model_3d" in have and not have["model_3d"].get("textured"))
        paint_views = bool(paint_wanted and blank_model)
        paint_needs_sprite = bool(paint_views and "sprite" not in have)
        sprite_will_change = bool(assets.get("sprite") or model_3d_needs_sprite or paint_needs_sprite)
        hunyuan_front = bool(assets.get("model_3d") and not pixal_3d
                             and three_d.clean_hunyuan_source(options.get("three_d_hunyuan_source")) == "turnaround")
        model_views = three_d.PIXAL_SHEET_VIEWS if pixal_3d or paint_views else ("front",) if hunyuan_front else ()
        pixal_views = []
        if model_views:
            saved_views = have.get("sheet", {}).get("views", {})
            sprite_created = have.get("sprite", {}).get("created")
            for view in model_views:
                meta = saved_views.get(view, {})
                if (sprite_will_change or not meta or meta.get("stale") or meta.get("source_sprite_created") != sprite_created or
                        have.get("sprite", {}).get("stale")):
                    pixal_views.append(view)
        if assets.get("living"):
            # a living portrait: the expressions it is made of that are missing or out of date, then the assembly. A new
            # portrait in this same request makes every one of them out of date.
            fresh = {} if assets.get("portrait") else have.get("expressions", {})
            exprs += [e for e in assets["living"] if e not in exprs and (e not in fresh or fresh[e].get("stale"))]
        head = assets.get("head_3d")
        head_views = () if not head else head3d.VIEWS if head is True else tuple(head["views"])
        if "front" in head_views:                      # the side and back pictures are drawn from the front one: a new one, new sides
            head_views = head3d.VIEWS
        elif head_views and not (have.get("head_3d", {}).get("views", {}).get("front") or {}).get("file"):
            head_views = ("front",) + head_views       # and a side or back picture with no front one to draw it from gets one first
        head_model = bool(head) and (head is True or head["model"])
        head_needs_portrait = bool("front" in head_views and "portrait" not in have
                                   and not have.get("sheet", {}).get("views", {}).get("front", {}).get("file"))
        if imported:                                   # first: it needs nothing made here, and a file that cannot be used stops the job at once
            steps.append(step("import_3d", None, "Import the 3D model"))
        if assets.get("body_3d"):                      # a designed body (npcgen/bodymaker.py): built, rigged and animated in this one step
            steps.append(step("body_3d", None, "Build the designed body, rig it and add its animations"))
        if assets.get("portrait") or ((exprs or assets.get("living")) and "portrait" not in have) or head_needs_portrait:
            steps.append(step("portrait", None, "Portrait" if assets.get("portrait") else "Portrait (needed for the detailed head)"
                              if head_needs_portrait and not (exprs or assets.get("living")) else "Portrait (needed for expressions)"))
        if (assets.get("sprite") or ((anim or sheet) and ("sprite" not in have or have["sprite"].get("stale"))) or model_3d_needs_sprite
                or paint_needs_sprite):
            needed_for = "animation" if anim else "the model sheet" if sheet else "the 3D model"
            steps.append(step("sprite", None, "Full-body sprite" if assets.get("sprite") else f"Full-body sprite (needed for {needed_for})"))
        sheet_views = set(sheet.get("views", ())) if sheet else set()
        if model_views:
            sheet_views.update(pixal_views)
            # Pixal3D consumes the existing turnaround view files, so make those views before queuing its model step.
            for view in ("portrait", *three_d.PIXAL_SHEET_VIEWS):
                if view in sheet_views:
                    label = (f"{three_d.GENERATORS[engine]['short']} view: {view.replace('_', ' ')}" if pixal_3d and view in pixal_views
                             else f"Sheet: {view.replace('_', ' ')} (for the 3D model)" if view in pixal_views
                             and view not in (sheet or {}).get("views", ()) else f"Sheet: {view.replace('_', ' ')}")
                    steps.append(step("sheet", view, label))
        if assets.get("model_3d"):
            label = {"pixal3d": "Pixal3D textured model", "trellis2": "Trellis2 model from four pictures"}.get(engine, "Hunyuan3D v2.1 model")
            if engine == "trellis2" and options.get("three_d_glasses_off") and eyesoft.glasses_of(npc):
                label += ", glasses taken off the pictures"
            if engine == "trellis2" and options.get("three_d_soften_eyes"):
                label += ", eyes softened for the shape"
            steps.append(step("model_3d", None, label))
        extras = [key for key, flag in cleanup3d.OPTIONS if assets.get(flag)]
        head_steps = []
        if head:                                       # only when asked: close-ups of the head, then the model made from them
            head_steps = [step("head_view", v, f"Head picture: {head3d.view_label(v)}") for v in head_views]
            if head_model:
                head_steps.append(step("head_3d", None, "Detailed head model"))
        if "head" in extras:                           # attaching it needs it: made in this request, it comes before the clean-up
            steps.extend(head_steps)
            head_steps = []
        if assets.get("refine_3d") or extras:           # only when asked: a future generator may not need it
            label = "Clean up the 3D model and paint it from the pictures"
            if extras:
                label += ", with " + " and ".join(cleanup3d.OPTION_LABELS[key] for key in extras)
            if options.get("game_faces") not in (None, "", cleanup3d.GAME_FACES):       # (the usual size is not worth saying)
                label += f", game version of {cleanup3d.clean_faces(options['game_faces']):,} faces"
            steps.append(step("refine_3d", "+".join(extras) or None, label))
        if assets.get("check_3d"):                     # only when asked: the vision model's before/after opinion
            steps.append(step("check_3d", None, "Check the clean-up with LM Studio"))
        if assets.get("rig_3d"):
            steps.append(step("rig_3d", None, "Skeleton and animation clips"))
        steps.extend(head_steps)
        for e in exprs:
            steps.append(step("expression", e, f"Expression: {e}"))
        if assets.get("living"):
            steps.append(step("living", None, "Assemble the living portrait"))
        if sheet:
            if not model_views:
                steps.extend(step("sheet", v, f"Sheet: {v.replace('_', ' ')}") for v in sheet["views"])
            steps.append(step("sheet_stitch", None, "Stitch the sheet"))
        if anim:
            generated = generated_directions(anim["dirs"], anim["mirror_left"])
            for d in generated:
                if d != "down":                       # 'down' is the sprite itself
                    steps.append(step("view", d, f"Reference view: {d}"))
            for clip in anim["clips"]:
                if anim["kinds"][clip] == "generated":
                    steps.extend(step("anim", f"{clip}:{d}", f"{clip.capitalize()}: {d}") for d in generated)
                steps.append(step("assemble", clip, f"Assemble {clip}"))
        voice = assets.get("voice")
        if voice:                                     # see npcgen/voice/steps.py
            if voice.get("lab_create"):
                steps.append(step("voice_lab_create", None, "Make this voice in Voice Lab"))
            steps.extend(step("voice_take", str(n), f"Voice take {n}") for n in range(1, int(voice.get("takes") or 0) + 1))
            if voice.get("takes") and voice.get("auto_pick"):
                steps.append(step("voice_pick", None, "Use the first take until you pick one"))
            if voice.get("test"):
                steps.append(step("voice_test", voice["test"]["key"], voice["test"].get("label") or "Try a line"))
            steps.extend(step("voice_test", row["key"], row["label"]) for row in voice.get("tests") or [])
            steps.extend(step("voice_line", line_id, f"Voice line: {line_id.replace('_', ' ')}")
                         for line_id in voice.get("lines") or [])
        return steps

    # ---- worker -------------------------------------------------------------------------------------------------------
    def _worker(self):
        epoch = self._epoch                                 # a worker the watchdog replaced leaves everything alone (see _abandon)
        threading.current_thread().epoch = epoch
        unload_at = None                                    # when the GPU is unloaded unless another job comes first
        while True:
            try:
                job = self.queue.get(timeout=None if unload_at is None else max(0.0, unload_at - time.monotonic()))
            except queue.Empty:
                self._unload_idle()
                unload_at = None
                continue
            if job is None:
                if unload_at is not None:
                    self._unload_idle()                     # shutting down: leave nothing of NPC Forge's on the card
                return
            unload_at = None
            try:
                self._run_job(job)
            except Exception as exc:                        # never let the worker thread die
                traceback.print_exc()
                if epoch != self._epoch:
                    return
                with self.lock:
                    job.status, job.error = "error", f"Unexpected error: {exc}"
                job.touch()
            if epoch != self._epoch:
                return
            if self.queue.empty():
                # Nothing waiting. Unload after a while rather than at once: a job started meanwhile with the same models
                # finds them loaded (a different model still unloads them first, in gpu.claim).
                unload_at = time.monotonic() + gpu.GATE.settings.get("unload_idle_seconds", 0)

    def _unload_idle(self):
        engines.release(self.comfy)                         # a big engine left loaded would sit on 30 GB
        gpu.release_comfy(self.comfy)                       # one model at a time: nothing of ComfyUI's stays on the card between jobs

    def _run_job(self, job):
        epoch = getattr(threading.current_thread(), "epoch", self._epoch)       # (set by _worker; a test may call this on its own)
        if job.target_type in ("scene", "item", "creature"):
            npc = None
        else:
            try:
                npc = self.lib.get(job.npc_id)
            except KeyError:  # Keep retry/recovery independent of the worker when its record was already removed.
                npc = None
        if npc:
            requested_assets = {
                "sprite": any(step.get("kind") == "sprite" for step in job.steps),
                "animations": job.options.get("animations"),
                "sheet": job.options.get("sheet"),
                "model_3d": any(step.get("kind") == "model_3d" for step in job.steps),
                "body_3d": any(step.get("kind") == "body_3d" for step in job.steps),
            }
            body_plan = morphology.resolve(npc, roll.load_data())
            reason = morphology.unsupported_reason(body_plan, requested_assets)
            if not reason and any(step.get("kind") == "rig_3d" for step in job.steps):
                reason = rig3d.unsupported_reason(body_plan)
            if reason:
                with self.lock:
                    job.status, job.error = "error", reason
                job.touch()
                return
        with self.lock:
            if job.status == "cancelled" or self.closed:
                return
            job.status = "running"
            job.started = job.started or now()
            job.finished = None
        job.touch()
        for step in job.steps:
            with self.lock:
                if step["status"] == "done":
                    continue
                cancelled = job.cancel.is_set()
                if cancelled:
                    step["status"] = "cancelled"
                    if job.stop_note:
                        step["reason"] = job.stop_note
                else:
                    step["status"] = "running"
                    step["started_at"] = step["last_progress_at"] = now()
                    job.running_step, job.alive_at, job.waiting_for = step, time.monotonic(), None
                    self.current = (job, step)
            if cancelled:
                job.touch()
                continue
            job.touch()
            started = time.time()
            outcome, tries = self._attempt(job, step, epoch)
            with self.lock:
                if epoch != self._epoch:
                    return                                  # the watchdog gave up on this step and a new worker has the queue
                self.current = None
                job.running_step = None
                if outcome[0] == "done":
                    step["status"] = "done"
                elif outcome[0] == "cancelled":
                    step["status"] = "cancelled"
                    job.cancel.set()
                elif outcome[0] == "skipped":               # nothing to work on after an earlier failure: not an error of its own
                    step["status"], step["reason"] = "cancelled", outcome[1]
                else:
                    step["status"], step["error"], step["code"] = "error", outcome[1], outcome[3]
                    step.update(outcome[4])                 # a GPU refusal also names the programs holding the card, and the card
                    if outcome[2]:                          # ComfyUI is down or taken, the GPU is taken, or there is no model to import:
                        job.stop_note = NOT_RUN.get(outcome[3], "not run: an earlier step stopped the job")
                        job.cancel.set()                    # no point trying the rest
                step["seconds"] = round(time.time() - started, 1)
                if tries:
                    step["attempts"] = tries + 1
            job.touch()
        with self.lock:
            states = {s["status"] for s in job.steps}
            job.status = "error" if "error" in states else "cancelled" if "cancelled" in states else "done"
            failed = [s for s in job.steps if s.get("error")]
            job.error = failed[0]["error"] if failed else None
            job.code = (failed[0].get("code") or "error") if job.status == "error" else "cancelled" if job.status == "cancelled" else None
            job.finished = now()
        job.touch()

    def _attempt(self, job, step, epoch):
        """Run one step, trying it again when the GPU hand-off failed for a reason that passes (another model was still on
        the card): (("done",) | ("cancelled",) | ("skipped", why) | ("error", message, stop_the_job, code, extra fields),
        the tries made after the first). A GPU that another program holds is not tried again (the gate already waited for
        it, and such a program does not let go within minutes); a refusal that outlasts its tries stops the whole job, like
        a ComfyUI that cannot be reached, so the later steps do not each wait and fail the same way."""
        tries = 0
        while True:
            try:
                with engines.strict(job.options.get("engine_fallback") is False):
                    self._run_step(job, step)
                return ("done",), tries
            except Cancelled:
                if job.cancel_reason == "stalled":
                    return ("error", STALLED.format(minutes=self.stall_seconds / 60), False, "stalled", {}), tries
                return ("cancelled",), tries
            except StepSkipped as exc:
                return ("skipped", str(exc)), tries
            except ComfyError as exc:
                code = failure_code(exc)
                if (self._transient(exc) and code != "gpu_busy" and tries < self.retries and not job.cancel.is_set()
                        and epoch == self._epoch):
                    tries += 1
                    if self._pause(job, f"{self._why(exc)}; trying again ({tries} of {self.retries})", self.retry_pause):
                        continue
                    return ("cancelled",), tries
                stop = code == "comfy_down" or code in GPU_REFUSALS or str(exc).startswith("Cannot reach") or step["kind"] == "import_3d"
                extra = {name: getattr(exc, name) for name in ("holders", "device") if getattr(exc, name, None)}
                return ("error", str(exc), stop, code, extra), tries
            except Exception as exc:
                traceback.print_exc()
                return ("error", f"{type(exc).__name__}: {exc}", False, "error", {}), tries

    @staticmethod
    def _transient(exc):
        """The GPU was not empty yet, or there was not enough free memory for a big engine: both pass, a second try often works."""
        return isinstance(exc, gpu.GpuBusy) or bool(getattr(exc, "transient", False))

    @staticmethod
    def _why(exc):
        return "waiting for the GPU to be free" if failure_code(exc) in GPU_REFUSALS else "waiting for memory to be free"

    def _pause(self, job, note, seconds):
        """Wait `seconds` showing `note` on the running step; False when the job was cancelled meanwhile (at once: the wait is on the
        cancel flag itself, so the watchdog never takes a step that is only waiting for a stuck one)."""
        end = time.monotonic() + seconds
        cancel = job.cancel
        job.beat(note)
        while time.monotonic() < end:
            if cancel.wait(min(0.5, max(0.0, end - time.monotonic()))):
                job.beat(None)
                return False
            job.beat(note)
        job.beat(None)
        return not cancel.is_set()

    # ---- the watchdog ---------------------------------------------------------------------------------------------------
    def _watch(self):
        """Free the queue when a running step cannot be stopped. The waits a step makes poll the cancel flag, but a step can be
        stuck where nothing polls it (a program that never answers): the worker then holds the queue for ever, which a restart
        used to be the only cure for. A step that makes no sign of life for `stall_minutes` is asked to stop, and one that was
        asked to stop (by the user or by this) and has not done so within `cancel_grace_seconds` is given up on: its job ends,
        and a new worker takes the queue. The old thread is left to finish whatever it is stuck in, and then leaves."""
        while True:
            time.sleep(self.watch_every)
            if self.closed:
                return
            with self.lock:
                current = self.current
            if current is None:
                continue
            job, step = current
            clock = time.monotonic()
            if job.cancel.is_set():
                if job.cancel_at is not None and clock - job.cancel_at > self.cancel_grace:
                    self._abandon(job, step)
            elif clock - job.alive_at > self.stall_seconds:
                with self.lock:
                    if self.current is current and not job.cancel.is_set():
                        job.cancel_reason, job.cancel_at = "stalled", clock
                        job.cancel.set()
                job.touch()

    def _abandon(self, job, step):
        with self.lock:
            if self.current is None or self.current[1] is not step:
                return                                      # the step ended while the watchdog was deciding
            stalled = job.cancel_reason == "stalled"
            self._epoch += 1
            self.current = None
            job.running_step = None
            step["status"], step["error"] = ("error", STALLED.format(minutes=self.stall_seconds / 60)) if stalled else ("cancelled", None)
            if stalled:
                step["code"] = "stalled"
            for other in job.steps:
                if other["status"] in ("pending", "running"):
                    other["status"] = "cancelled"
            job.status = "error" if stalled else "cancelled"
            job.error = step["error"]
            job.code = "stalled" if stalled else "cancelled"
            job.finished = now()
            if not self.closed:                             # (shutting down: close() has already put the end-of-work mark in the queue)
                worker = threading.Thread(target=self._worker, daemon=True, name="npc-worker")
                worker.start()                              # started before it is published, and published under the lock close() reads it under
                self.thread = worker
        job.touch()

    def _import_model(self, job, step, request):
        """Make the file an import request names this character's 3D model: closed and simplified by ComfyUI's mesh nodes
        (`clean`), or taken as it is. The file stays where it was; ComfyUI reads it from its own folder."""
        comfy, lib = self.comfy, self.lib
        try:
            model_file = import3d.canonical(request.get("model_file"))
            folder, sub, name = import3d.locate(model_file)
            info = import3d.describe(comfy, model_file)
        except ValueError as exc:
            raise ComfyError(str(exc)) from None
        clean = request.get("clean", "auto")
        clean = import3d.default_clean(info) if clean == "auto" else bool(clean)
        detail = three_d.clean_quality(request.get("detail"))
        started = time.monotonic()
        preview = None
        if clean:
            entry = comfy.run_entry(three_d.import_graph(model_file, job.npc_id, job.id, detail), cancel=job.cancel,
                                    timeout=1800, unload_models=True)
            saved = three_d.saved_file(entry, "3d", "GLB model", node=three_d.IMPORT_OUTPUTS[0])
            shot = three_d.saved_file(entry, "images", "3D preview", node=three_d.IMPORT_OUTPUTS[1])
            model = comfy.fetch_file(saved["filename"], saved.get("subfolder", ""), saved.get("type", "output"))
            preview = comfy.fetch_file(shot["filename"], shot.get("subfolder", ""), shot.get("type", "output"))
        else:
            model = comfy.fetch_file(name, sub, folder)
        lib.archive_model(job.npc_id)                           # the model this one replaces is kept among the earlier ones
        lib.write_asset(job.npc_id, "model_3d.glb", model)
        if preview:
            lib.write_asset(job.npc_id, "model_3d_preview.png", preview)
        else:
            try:
                lib.resolve(job.npc_id, "model_3d_preview.png").unlink()      # the old model's picture is not this one's
            except FileNotFoundError:
                pass
        try:
            lib.resolve(job.npc_id, cleanup3d.ORIGINAL_FILE).unlink()         # kept from the previous model's clean-up
        except FileNotFoundError:
            pass
        shutil.rmtree(lib.resolve(job.npc_id, "model_3d_head"), ignore_errors=True)    # its sharper face's close-ups
        try:
            faces = import3d.inspect(model[:import3d.HEAD_BYTES])["faces"] if clean else info["faces"]
        except ValueError:
            faces = None
        meta = {"file": "model_3d.glb", "created": now(), "size": len(model), "format": "glb",
                "engine": f"Imported: {name}", "engine_id": "import", "textured": bool(info["textured"] and not clean),
                "imported": {"name": name, "folder": folder, "faces_in": info["faces"], "faces": faces, "cleaned": clean,
                             "seconds": round(time.monotonic() - started)}}
        if preview:
            meta["preview"] = "model_3d_preview.png"
        if clean:
            meta["quality"] = detail
            meta["quality_label"] = three_d.PIXAL_QUALITIES[detail]["label"].split(" · ", 1)[0]
        if info["warnings"]:
            note = "; ".join(info["warnings"])
            step["note"] = note[:1].upper() + note[1:] + "."
        lib.update(job.npc_id, lambda n: _replace_model_3d(n, meta))

    # ---- one step -------------------------------------------------------------------------------------------------------
    def _run_step(self, job, step):
        cfg, comfy, lib, opt = self.cfg, self.comfy, self.lib, job.options
        if step["kind"] == "scene_voice":
            scene_voice.run(self, job, step)
            return
        if step["kind"] == "scene_background":
            scene_background.run(self, job, step)
            return
        if step["kind"] in creaturemaker.RUNNERS:
            creaturemaker.run(self, job, step)
            return
        if step["kind"] in itemmaker.RUNNERS:
            itemmaker.run(self, job, step)
            return
        npc = lib.get(job.npc_id)
        if step["kind"] in voice_steps.RUNNERS:
            voice_steps.run(self, job, step, npc)
            return
        if step["kind"] in bodymaker.RUNNERS:
            bodymaker.run(self, job, step, npc)
            return
        data = roll.load_data()
        style = data["styles"][npc["style"]]
        setting = data["settings"].get(npc["setting"], {})
        quality = cfg["quality"].get(opt["quality"]) or cfg["quality"]["standard"]
        seed = int(opt["seed"]) if opt.get("seed") is not None else (random.randrange(2 ** 32) if opt.get("reroll") else npc["seed"])
        key = prompts.pick_key(npc, style)
        kind = step["kind"]

        if kind == "portrait":
            transparent = bool(opt["transparent_portrait"])
            w, h = quality["portrait"]
            photo_meta = npc.get("assets", {}).get("source_photo")
            if photo_meta:
                pr = prompts.portrait_from_photo(npc, style, setting, key if transparent else None)
                photo_name = self._reference_photo(job, photo_meta)
                png, used = engines.edit(comfy, opt.get("edit_model"), photo_name, pr["positive"], seed, quality["steps"],
                                         cfg["edit_cfg"], size=(w, h), negative=pr["negative"], cancel=job.cancel)
                engine = {"source": "photo", **used}
            else:
                pr = prompts.portrait(npc, style, setting, key if transparent else None, framing=opt.get("portrait_framing") or "bust")
                png, engine = self._portrait_from_hooks(job, npc, setting, pr, (w, h), seed)
                if png is None:
                    png, used = engines.image(comfy, opt.get("image_model"), pr["positive"], pr["negative"], w, h, seed,
                                              quality["steps"], cfg["cfg"], job.cancel)
                    engine = {**engine, **used}
            if engine.get("engine_note"):
                step["note"] = ENGINE_NOTE + engine["engine_note"]
            replaced = self._store(job, "portrait", "portrait", png, transparent, style, key, trim=False, prompt=pr, seed=seed,
                                   quality=opt["quality"], style_id=npc["style"], extra=engine)
            job.ref = None
            if replaced:                                  # (a reroll saved beside the portrait changes nothing made from it)
                self._mark_derived_stale(job.npc_id)
            return

        if kind == "sprite":
            transparent = bool(opt["transparent_sprite"])
            view = opt.get("sprite_view", "front")
            w, h = quality["sprite"]
            portrait_meta = lib.get(job.npc_id)["assets"].get("portrait")
            mode = resolve_sprite_mode(opt.get("sprite_mode", "auto"), npc.get("freeform", False), bool(portrait_meta))
            if mode == "match":
                pr = prompts.sprite_edit(npc, style, key, view)
                png, used = engines.edit(comfy, opt.get("edit_model"), self._reference(job, portrait_meta), pr["positive"], seed,
                                         quality["steps"], cfg["edit_cfg"], size=(w, h), negative=pr["negative"], cancel=job.cancel)
            else:
                pr = prompts.sprite(npc, style, setting, key, view)
                png, used = engines.image(comfy, opt.get("image_model"), pr["positive"], pr["negative"], w, h, seed,
                                          quality["steps"], cfg["cfg"], job.cancel)
            self._engine_note(step, used)
            replaced = self._store(job, "sprite", "sprite", png, transparent, style, key, trim=True, prompt=pr, seed=seed,
                                   quality=opt["quality"], style_id=npc["style"], extra={"mode": mode, "view": view, **self._engine_meta(used)})
            if replaced:
                job.cache.pop("refs", None)
                job.cache.pop("canon", None)
                job.cache["sprite_regenerated"] = True      # views and frames made from the old sprite no longer match it
                self._mark_animations_stale(job.npc_id)
            return

        if kind == "model_3d":
            sprite_meta = npc.get("assets", {}).get("sprite")
            if not sprite_meta:
                raise ComfyError("There is no full-body sprite to use for 3D generation")
            engine = three_d.clean(opt.get("three_d_model"))
            stripped = None                                  # (only the Trellis2 branch takes glasses off; the end of the step asks every generator)
            if engine == "trellis2":
                quality = three_d.clean_quality(opt.get("three_d_quality"))
                views = npc.get("assets", {}).get("sheet", {}).get("views", {})
                pictures = {}
                for view in three_d.PIXAL_SHEET_VIEWS:
                    meta = views.get(view) or {}
                    if not meta.get("file") or meta.get("stale") or meta.get("source_sprite_created") != sprite_meta.get("created"):
                        raise ComfyError(f"Trellis2 needs a current '{view.replace('_', ' ')}' model-sheet view. Regenerate the 3D model to make it.")
                    pictures[view] = lib.read_asset(job.npc_id, meta["file"])
                stripped = unstripped = None
                worn = eyesoft.glasses_of(npc)
                if opt.get("three_d_glasses_off") and worn:  # only when asked: model, paint and head are made from copies without the glasses
                    try:
                        sheet_quality = cfg["quality"].get(opt["quality"]) or cfg["quality"]["standard"]
                        pictures, stripped = glassesoff.strip(comfy, pictures, job.npc_id, seed, sheet_quality["sheet_steps"], cfg["sheet_cfg"],
                                                              engine=opt.get("edit_model"), cancel=job.cancel)
                        for view, png in pictures.items():
                            lib.write_asset(job.npc_id, f"{glassesoff.DIR}/{view}.png", png)
                    except glassesoff.StripError as exc:
                        if not opt.get("three_d_pictures_optional"):    # asked for on its own: stop rather than make a model with glasses
                            raise
                        unstripped = str(exc)                          # inside the best-quality chain: made from the pictures as they are
                        step["note"] = "Glasses not taken off: " + unstripped
                softened = skipped = None
                if opt.get("three_d_soften_eyes"):          # only when asked: the shape is made from copies with skin where the eyes were
                    skipped = eyesoft.skip_reason(npc, glasses_off=bool(stripped))
                    if skipped:                              # glasses: filled in, the frames come out as solid lenses
                        step["note"] = "Eyes not softened: " + skipped
                    else:
                        poses = posekeys.availability(comfy)
                        if poses["missing"]:
                            raise ComfyError("Softening the eyes needs " + "; ".join(poses["missing"]) + " (it finds the eyes with SDPose)")
                        try:
                            pictures, softened = eyesoft.soften(comfy, pictures, job.npc_id, cancel=job.cancel)
                        except eyesoft.SoftenError as exc:
                            if not opt.get("three_d_pictures_optional"):      # asked for on its own: stop rather than make an unchanged model
                                raise
                            skipped = str(exc)                              # inside the best-quality chain: made from the pictures as they are
                            step["note"] = ((step.get("note") or "") + " Eyes not softened: " + skipped).strip()
                image_names = {view: comfy.upload_image(png, f"{job.npc_id}_trellis2_{view}.png") for view, png in pictures.items()}
                graph = three_d.trellis2_graph(image_names, job.npc_id, job.id, seed, quality=quality)
                timeout = 3600
                saved_nodes = three_d.TRELLIS2_OUTPUTS
            elif engine == "pixal3d":
                quality = three_d.clean_quality(opt.get("three_d_quality"))
                views = npc.get("assets", {}).get("sheet", {}).get("views", {})
                image_names = {}
                head_image_names = {}
                head_target_up = None
                for view in three_d.PIXAL_SHEET_VIEWS:
                    meta = views.get(view) or {}
                    if not meta.get("file") or meta.get("stale") or meta.get("source_sprite_created") != sprite_meta.get("created"):
                        raise ComfyError(f"Pixal3D needs a current '{view.replace('_', ' ')}' model-sheet view. Regenerate the 3D model to make it.")
                    source_view = lib.read_asset(job.npc_id, meta["file"])
                    image_names[view] = comfy.upload_image(source_view,
                                                           f"{job.npc_id}_pixal_{view}.png")
                    head_view, target_up = three_d.prepare_head_reference(source_view)
                    head_image_names[view] = comfy.upload_image(head_view, f"{job.npc_id}_pixal_head_{view}.png")
                    if view == "front":
                        head_target_up = target_up
                portrait_meta = npc.get("assets", {}).get("portrait") or {}
                front_reference = "turnaround"
                requested_front_reference = three_d.clean_face_source(opt.get("three_d_face_source"))
                if requested_front_reference == "portrait" and portrait_meta.get("file") and not portrait_meta.get("stale"):
                    portrait = lib.read_asset(job.npc_id, portrait_meta["file"])
                    portrait_head = three_d.prepare_portrait_head_reference(portrait)
                    head_image_names["front"] = comfy.upload_image(
                        portrait_head, f"{job.npc_id}_pixal_portrait_head.png")
                    front_reference = "portrait"
                graph = three_d.pixal3d_graph(image_names, head_image_names, head_target_up,
                                              job.npc_id, job.id, seed, quality=quality)
                timeout = 5400 if quality == "high" else 3600
                saved_nodes = (None, None)
            else:
                source = lib.read_asset(job.npc_id, sprite_meta["file"])
                hunyuan_source = three_d.clean_hunyuan_source(opt.get("three_d_hunyuan_source"))
                if hunyuan_source == "turnaround":
                    meta = npc.get("assets", {}).get("sheet", {}).get("views", {}).get("front") or {}
                    if not meta.get("file") or meta.get("stale") or meta.get("source_sprite_created") != sprite_meta.get("created"):
                        raise ComfyError("Hunyuan3D from the model sheet needs a current front picture. Regenerate the 3D model to make it.")
                    source = three_d.cut_out(lib.read_asset(job.npc_id, meta["file"]))
                prepared = three_d.prepare_reference(source)
                image_name = comfy.upload_image(prepared, f"{job.npc_id}_3d_input.png")
                detail = three_d.clean_hunyuan_detail(opt.get("three_d_hunyuan_detail"))
                graph = three_d.graph(image_name, job.npc_id, job.id, seed, detail=detail)
                timeout = 1800
                saved_nodes = (None, None)
            generation_started = time.monotonic()
            entry = comfy.run_entry(graph, cancel=job.cancel, timeout=timeout, unload_models=True)
            model_file = three_d.saved_file(entry, "3d", "GLB model", node=saved_nodes[0])
            preview_file = three_d.saved_file(entry, "images", "3D preview", node=saved_nodes[1])
            model = comfy.fetch_file(model_file["filename"], model_file.get("subfolder", ""), model_file.get("type", "output"))
            preview = comfy.fetch_file(preview_file["filename"], preview_file.get("subfolder", ""), preview_file.get("type", "output"))
            lib.archive_model(job.npc_id)                       # the model this one replaces is kept among the earlier ones
            lib.write_asset(job.npc_id, "model_3d.glb", model)
            lib.write_asset(job.npc_id, "model_3d_preview.png", preview)
            try:
                lib.resolve(job.npc_id, cleanup3d.ORIGINAL_FILE).unlink()   # kept from the previous model's clean-up
            except FileNotFoundError:
                pass
            shutil.rmtree(lib.resolve(job.npc_id, "model_3d_head"), ignore_errors=True)   # its sharper face's close-ups
            info = {"file": "model_3d.glb", "preview": "model_3d_preview.png", "created": now(), "seed": seed,
                    "engine": three_d.GENERATORS[engine]["label"], "engine_id": engine,
                    "source_sprite_created": sprite_meta.get("created"), "size": len(model), "format": "glb",
                    "textured": three_d.GENERATORS[engine]["textured"],
                    "generation_seconds": round(time.monotonic() - generation_started)}
            if engine in three_d.FOUR_VIEW_GENERATORS:
                info["quality"] = quality
                info["quality_label"] = three_d.PIXAL_QUALITIES[quality]["label"].split(" · ", 1)[0]
                info["source_sheet_views"] = {view: views[view].get("created") for view in three_d.PIXAL_SHEET_VIEWS}
                if engine == "trellis2" and softened:
                    info["eyes_softened"] = softened       # {"eye_width_px", "views": {view: [sides]}}: the paint still reads the original pictures
                elif engine == "trellis2" and skipped:
                    info["eyes_not_softened"] = skipped
                if engine == "trellis2" and stripped:
                    info["glasses_off"] = {"worn": worn, "engine": stripped.get("engine"), "views": stripped["views"],
                                           "pictures": {view: f"{glassesoff.DIR}/{view}.png" for view in stripped["views"]}}
                elif engine == "trellis2" and unstripped:
                    info["glasses_not_taken_off"] = unstripped
                if not stripped:                           # copies of an earlier model are of no use to this one
                    shutil.rmtree(lib.resolve(job.npc_id, glassesoff.DIR), ignore_errors=True)
                if engine == "pixal3d":
                    info["front_reference"] = front_reference
            else:
                info["source_picture"] = hunyuan_source
                info["quality"] = detail
                info["quality_label"] = three_d.HUNYUAN_DETAILS[detail]["label"].split(" · ", 1)[0]
            lib.update(job.npc_id, lambda n: _replace_model_3d(n, info))
            return

        if kind == "import_3d":
            self._import_model(job, step, opt.get("import_3d") or {})
            return

        if kind == "refine_3d":
            model_meta = npc.get("assets", {}).get("model_3d")
            if not model_meta:
                raise ComfyError("There is no 3D model to clean up")
            # always start from the generator's own model, so doing it again never stacks one clean-up on another
            original = lib.read_asset(job.npc_id, model_meta.get("raw") or model_meta["file"])
            views = npc.get("assets", {}).get("sheet", {}).get("views", {})
            pictures = glassesoff.pictures_for(lambda path: lib.read_asset(job.npc_id, path), model_meta, views)    # without the glasses, if so made
            missing = [v.replace("_", " ") for v in three_d.PIXAL_SHEET_VIEWS if v not in pictures]
            if not model_meta.get("textured") and missing:
                raise ComfyError(f"This model has no colour of its own, so it is painted from the model sheet: make its "
                                 f"{', '.join(missing)} {'picture' if len(missing) == 1 else 'pictures'} first")
            extras = set((step.get("key") or "").split("+"))
            closeups, face, located, eye_info, hair = None, None, None, None, None
            shutil.rmtree(lib.resolve(job.npc_id, "model_3d_head"), ignore_errors=True)
            if "eyes" in extras:
                if "front" not in pictures:
                    raise ComfyError("Smoothing the eye rings needs the front turnaround picture: make the model sheet first")
                located, eye_info = eyes.locate(cfg, original, pictures["front"], comfy, cancel=job.cancel)
            if "face" in extras:
                if not pictures:
                    raise ComfyError("The sharper face is painted from the turnaround pictures: make the model sheet first")
                upscaler = headdetail.pick_upscaler(comfy, cfg.get("face_upscaler"))
                closeups = headdetail.closeups(comfy, pictures, job.npc_id, upscaler, cancel=job.cancel)
                for view, close in closeups.items():
                    lib.write_asset(job.npc_id, f"model_3d_head/{view}.png", close["png"])
                face = {"upscaler": upscaler, "licence": headdetail.LICENCES.get(upscaler),
                        "closeups": {v: f"model_3d_head/{v}.png" for v in closeups}}
            if "hair" in extras:
                if not pictures:
                    raise ComfyError("Matching the hair compares the model with the turnaround pictures: make the model sheet first")
                missing_sam = hairmask.availability(comfy)["missing"]
                if missing_sam:
                    raise ComfyError(f"Matching the hair needs {' and '.join(missing_sam)}")
                hair = hairmask.finder(comfy, job.npc_id, cancel=job.cancel)
            attacher = None
            if "head" in extras:
                head_meta = npc.get("assets", {}).get("head_3d") or {}
                if not head_meta.get("file"):
                    raise ComfyError("Attaching the detailed head needs the detailed head: make it first")
                missing_face = headgraft.availability(cfg, comfy)["missing"]
                if missing_face:
                    raise ComfyError(f"Attaching the detailed head needs {' and '.join(missing_face)}")
                attacher = headgraft.Attacher(comfy, job.npc_id, lib.read_asset(job.npc_id, head_meta["file"]), head_meta.get("created"))
            hands = None
            if "hands" in extras:
                missing_pose = hands3d.availability(cfg, comfy)["missing"]
                if missing_pose:
                    raise ComfyError(f"Clean hands needs {' and '.join(missing_pose)}")
                hands = hands3d.Finder(comfy, job.npc_id, cancel=job.cancel)
            game_faces = cleanup3d.clean_faces(opt.get("game_faces"))
            refined, poster, report = cleanup3d.refine(cfg, original, pictures or None, faces=game_faces, cancel=job.cancel, closeups=closeups,
                                                       eyes=located, hair=hair, hands=hands,
                                                       **({"head": attacher} if attacher is not None else {}))
            if eye_info:
                eye_info.update({k: v for k, v in report.get("eyes", {}).items() if k != "seconds"})
            if face:
                layout = report.get("head_layout") or report.get("unwrap") or {}
                face.update(texels_before=layout.get("head_texels_per_unit_before"), texels=layout.get("head_texels_per_unit"),
                            texture=layout.get("texture"))
            if not model_meta.get("raw"):
                lib.write_asset(job.npc_id, cleanup3d.ORIGINAL_FILE, original)
            lib.write_asset(job.npc_id, "model_3d.glb", refined)
            if poster:
                lib.write_asset(job.npc_id, "model_3d_preview.png", poster)
            game = report.get("game_version", {})
            refined_info = {"created": now(), "faces": game.get("faces_after"), "faces_requested": game_faces,
                            "faces_generated": report["inner_skin"]["faces_before"],
                            "inner_skin_removed": report["inner_skin"]["faces_removed"], "painted_from": sorted(pictures),
                            "unwrapped": "unwrap" in report,
                            "paint": {v: {k: r.get(k) for k in ("outline_match", "trust")}
                                      for v, r in report.get("paint", {}).get("views", {}).items()},
                            **({"face": face} if face else {}), **({"eyes": eye_info} if eye_info else {})}
            hair_report = report.get("paint", {}).get("hair")
            if hair_report:
                refined_info["hair"] = {"model": hairmask.CHECKPOINT, "licence": hairmask.LICENCE,
                                        "hair_texels": hair_report.get("texels", 0), "texels_refused": hair_report.get("refused", 0)}
            hands_report = report.get("hands")
            if hands_report is not None:
                if "error" in hands_report:       # the rest of the clean-up is kept; the model keeps its own hands
                    refined_info["hands"] = {"error": hands_report["error"]}
                    step["note"] = f"The hands were left as they were: {hands_report['error']}"
                else:
                    refined_info["hands"] = {**hands3d.summary(hands_report, pictures=bool(pictures)),
                                             "pose_model": posekeys.CHECKPOINT}
                    if refined_info["hands"].get("colour_note"):
                        step["note"] = f"The new hands were {refined_info['hands']['colour_note']}"

            head_report = report.get("head")
            if head_report is not None:
                if "error" in head_report:        # the rest of the clean-up is kept; the model keeps its own face
                    refined_info["head"] = {"error": head_report["error"]}
                    step["note"] = f"The face was left as it was: {head_report['error']}"
                else:
                    refined_info["head"] = {**headgraft.summary(head_report), "source_head_created": attacher.head_created}
                    refined_info["faces"] = head_report.get("faces_after") or refined_info["faces"]     # the game version and the patch
                    worn = eyesoft.glasses_of(npc)
                    if worn:                        # the arms and lens corners lie beyond the patch's edge: the body's frames show there too
                        refined_info["head"]["glasses"] = worn
                        said = glassesoff.attach_note(worn, bool(model_meta.get("glasses_off")), bool(head_meta.get("glasses_off")))
                        if said:
                            step["note"] = ((step.get("note") or "") + " " + said).strip()

            def apply(n):
                meta = n["assets"]["model_3d"]
                meta.update(file="model_3d.glb", raw=cleanup3d.ORIGINAL_FILE, size=len(refined), refined=refined_info)
                if poster:
                    meta["preview"] = "model_3d_preview.png"
                if "rig_3d" in n["assets"]:
                    n["assets"]["rig_3d"]["stale"] = True
            lib.update(job.npc_id, apply)
            return

        if kind == "check_3d":
            model_meta = npc.get("assets", {}).get("model_3d") or {}
            if not model_meta.get("refined") or not model_meta.get("raw"):
                raise ComfyError("The check compares the cleaned-up model with the generator's original: clean the model up first")
            front = npc.get("assets", {}).get("sheet", {}).get("views", {}).get("front") or {}
            if not front.get("file"):
                raise ComfyError("The check compares the model with the model sheet's front picture: make the model sheet first")
            result, shown = check3d.check(cfg, lib.read_asset(job.npc_id, model_meta["raw"]),
                                          lib.read_asset(job.npc_id, model_meta["file"]), lib.read_asset(job.npc_id, front["file"]),
                                          comfy, cancel=job.cancel)
            lib.write_asset(job.npc_id, check3d.BOARD_FILE, shown)
            result.update(created=now(), board=check3d.BOARD_FILE)

            def apply(n):
                refined = n["assets"].get("model_3d", {}).get("refined")
                if refined is not None:                 # (a clean-up done meanwhile has no check yet)
                    refined["check"] = result
            lib.update(job.npc_id, apply)
            return

        if kind == "rig_3d":
            model_meta = npc.get("assets", {}).get("model_3d")
            if not model_meta:
                raise ComfyError("There is no 3D model to rig")
            model = lib.read_asset(job.npc_id, model_meta["file"])
            # a cleaned-up model is rigged with its untouched original as the shape to work out joints and weights on
            shape = lib.read_asset(job.npc_id, model_meta["raw"]) if model_meta.get("raw") else None
            # the joints come from SDPose's keypoints whenever it is installed, else from the model's outline
            skeleton = rig3d.clean_skeleton(opt.get("rig_skeleton"))
            poses = posekeys.availability(comfy)
            finder = None if poses["missing"] else posekeys.finder(comfy, job.npc_id, cancel=job.cancel)
            if skeleton == "medium" and finder is None:
                raise ComfyError("The medium skeleton needs " + "; ".join(poses["missing"]) +
                                 " (it finds the fingers and the face with it)")
            hands = (model_meta.get("refined") or {}).get("hands") or {}
            hands = hands if hands.get("l") or hands.get("r") else None   # clean hands: the rig uses their own joints
            rigged, poster, report = rig3d.run(cfg, model, cancel=job.cancel, shape_glb=shape, skeleton=skeleton,
                                               keypoints=finder, clips=opt.get("rig_clips"), hands=hands)
            lib.write_asset(job.npc_id, "model_3d_rigged.glb", rigged)
            notes = report.get("notes") or []
            if finder is None:
                notes = notes + ["joints from the model's outline (SDPose could not be used; missing: " + "; ".join(poses["missing"]) + ")"]
            info = {"file": "model_3d_rigged.glb", "created": now(), "size": len(rigged), "format": "glb",
                    "source_model_created": model_meta.get("created"), "clips": report.get("clips") or list(rig3d.CLIPS),
                    "height_m": report.get("height_m"), "notes": notes,
                    "bones": report.get("bones"), "faces": report.get("faces"), "skeleton": report.get("skeleton", skeleton),
                    "joints_from": report.get("joints_from") or ("pose keypoints" if finder else "outline"),
                    "clip_set": report.get("clip_set", "basic")}
            if report.get("clip_info"):
                info["clip_info"] = report["clip_info"]         # [{name, seconds, loop, source, [moves, speed_m_s]}]
            if info["clip_set"] == "library":
                info["animation_library"] = rig3d.LIBRARY
            if report.get("keypoint_scores"):
                info["keypoint_scores"] = report["keypoint_scores"]
                info["pose_model"] = {"model": posekeys.CHECKPOINT, "licence": posekeys.LICENCE}
            if poster:
                lib.write_asset(job.npc_id, "model_3d_rigged.png", poster)
                info["preview"] = "model_3d_rigged.png"
            lib.update(job.npc_id, lambda n: n.setdefault("assets", {}).update(rig_3d=info))
            return

        if kind in ("head_view", "head_3d"):
            if kind == "head_view":
                self.headmaker.make_view(job, npc, data, step["key"], seed)
                self._engine_note(step, {"engine_note": job.cache.pop("engine_note", None)})
            else:
                self.headmaker.make_model(job, npc, seed)
            return

        if kind in ("sheet", "sheet_stitch"):
            if kind == "sheet":
                self.sheeter.make_view(job, npc, data, step["key"], seed)
                self._engine_note(step, {"engine_note": job.cache.pop("engine_note", None)})
            else:
                self.sheeter.stitch(job, npc, data)
            return

        if kind == "living":
            try:
                living.assemble(lib, job.npc_id, data)
            except ValueError as e:
                failed = [s for s in job.steps if s.get("status") == "error" and s.get("kind") in ("portrait", "expression")]
                if failed:                                 # what it needs was not made: say why, not a new error of its own
                    what = "portrait" if str(e).startswith("There is no portrait") else "expression"
                    raise StepSkipped(f"no {what} was made: {failed[0].get('error')}") from None
                raise ComfyError(str(e)) from None
            return

        if kind in ("view", "anim", "assemble"):
            if kind == "view":
                self.animator.make_view(job, npc, data, step["key"], seed)
            elif kind == "anim":
                clip_id, direction = step["key"].split(":")
                self.animator.make_direction(job, step, npc, data, clip_id, direction, seed)
            else:
                self.animator.assemble(job, npc, data, step["key"])
            self._engine_note(step, {"engine_note": job.cache.pop("engine_note", None)})
            return

        # expression: edit the portrait, changing only the face
        expr = next(e for e in data["expressions"] if e["id"] == step["key"])
        portrait_meta = lib.get(job.npc_id)["assets"].get("portrait")
        if not portrait_meta:
            raise ComfyError("There is no portrait to edit yet: generate the portrait first")
        pr = prompts.expression_edit(expr["phrase"], style)
        png, used = engines.edit(comfy, opt.get("edit_model"), self._reference(job, portrait_meta), pr["positive"], seed,
                                 quality["steps"], cfg["edit_cfg"], megapixels=quality["edit_megapixels"], negative=pr["negative"],
                                 like=portrait_meta.get("size"), cancel=job.cancel)
        self._engine_note(step, used)
        self._store(job, ("expressions", expr["id"]), f"expressions/{expr['id']}", png, bool(portrait_meta.get("transparent")),
                    style, key, trim=False, prompt=pr, seed=seed, quality=opt["quality"], style_id=npc["style"],
                    extra=self._engine_meta(used))

    @staticmethod
    def _engine_note(step, used):
        """Say on the step when the chosen engine could not draw it and Mage-Flow did instead."""
        if used.get("engine_note"):
            step["note"] = ENGINE_NOTE + used["engine_note"]

    @staticmethod
    def _engine_meta(used):
        """What an asset records about its engine: the name of one that is not built in, or why Mage-Flow drew it instead."""
        return {k: used[k] for k in ("engine", "engine_note") if used.get(k)}

    def _reference(self, job, portrait_meta):
        """Upload the portrait (its un-cut-out original, if there is one) to ComfyUI once per portrait version."""
        stamp = portrait_meta.get("created")
        if job.ref and job.ref[1] == stamp:
            return job.ref[0]
        raw = self.lib.read_asset(job.npc_id, portrait_meta.get("raw") or portrait_meta["file"])
        name = self.comfy.upload_image(raw, f"{job.npc_id}_portrait.png")
        job.ref = (name, stamp)
        return name

    def _reference_photo(self, job, photo_meta):
        """Upload the source photo to ComfyUI once per photo version. A separate cache slot from _reference's
        job.ref: a photo-sourced portrait step uploads the photo, not the (possibly stale, possibly not-yet-made)
        portrait, so the two must not overwrite each other."""
        stamp = photo_meta.get("created")
        cached = job.cache.get("photo_ref")
        if cached and cached[1] == stamp:
            return cached[0]
        raw = self.lib.read_asset(job.npc_id, photo_meta["file"])
        name = self.comfy.upload_image(raw, f"{job.npc_id}_source_photo.png")
        job.cache["photo_ref"] = (name, stamp)
        return name

    def _portrait_from_hooks(self, job, npc, setting, prompt, size, seed):
        """(png, meta) from the first extension engine that takes this portrait, or (None, meta) for the built-in engine."""
        meta = {}
        for hook in PORTRAIT_ENGINES:
            try:
                made = hook(npc=npc, setting=setting, prompt=prompt, size=size, seed=seed, comfy=self.comfy, cancel=job.cancel)
            except PortraitFallback as e:
                meta["engine_note"] = str(e)
                print(f"Portrait engine skipped, using the built-in one: {e}", file=sys.stderr)
                continue
            if made:
                return made["png"], {"engine": made.get("engine", "extension")}
        return None, meta

    def _store(self, job, slot, base, png, keyed, style, key, trim, prompt, seed, quality, style_id, extra=None):
        """Write the image (cutting out the backdrop if asked), record its metadata in npc.json, refresh the thumbnail. The picture it
        replaces stays among the earlier versions (Library.archive_picture). True when this picture is now the active one, False when it was
        saved beside the active one instead (the save_candidates option)."""
        lib, npc_id = self.lib, job.npc_id
        meta = {"seed": seed, "prompt": prompt["positive"], "negative": prompt["negative"], "quality": quality,
                "style": style_id, "created": now(), **(extra or {})}
        if slot in ("portrait", "sprite") and job.options.get("save_candidates") and lib.get(npc_id).get("assets", {}).get(slot):
            self._store_candidate(job, slot, png, keyed, style, key, trim, meta)
            return False
        lib.archive_picture(npc_id, "/".join(slot) if isinstance(slot, tuple) else slot)
        info = {"keyed": False}
        if keyed:
            cut, info = remove_background(png, hard_edges=style.get("hard_edges", False), trim=trim)
        grey = style.get("monochrome", False)          # the raw file keeps its colour: re-cutting it needs the coloured backdrop
        if keyed and info["keyed"]:
            lib.write_asset(npc_id, f"{base}_raw.png", png)
            lib.write_asset(npc_id, f"{base}.png", grey_png(cut) if grey else cut)
            meta.update(file=f"{base}.png", raw=f"{base}_raw.png", transparent=True, size=info["size"], key=key)
        else:
            lib.write_asset(npc_id, f"{base}.png", grey_png(png) if grey else png)
            try:
                lib.resolve(npc_id, f"{base}_raw.png").unlink()
            except FileNotFoundError:
                pass
            meta.update(file=f"{base}.png", transparent=False, size=_png_size(png))
            if keyed:
                meta["key_error"] = info.get("reason", "background removal failed")

        def apply(npc):
            npc["style"] = style_id
            assets = npc.setdefault("assets", {})
            if isinstance(slot, tuple):
                assets.setdefault(slot[0], {})[slot[1]] = meta
            else:
                assets[slot] = meta
        lib.update(npc_id, apply)
        if not isinstance(slot, tuple):
            lib.make_thumb(npc_id)
        return True

    def _store_candidate(self, job, kind, png, keyed, style, key, trim, meta):
        """Store an opt-in portrait/sprite reroll without changing the active asset or its dependents."""
        lib, npc_id = self.lib, job.npc_id
        candidate_id = uuid.uuid4().hex[:12]
        file = f"candidates/{kind}/{candidate_id}.png"
        info = {"keyed": False}
        if keyed:
            cut, info = remove_background(png, hard_edges=style.get("hard_edges", False), trim=trim)
        grey = style.get("monochrome", False)
        if keyed and info["keyed"]:
            raw = f"candidates/{kind}/{candidate_id}_raw.png"
            lib.write_asset(npc_id, raw, png)
            lib.write_asset(npc_id, file, grey_png(cut) if grey else cut)
            meta.update(file=file, raw=raw, transparent=True, size=info["size"], key=key)
        else:
            lib.write_asset(npc_id, file, grey_png(png) if grey else png)
            meta.update(file=file, transparent=False, size=_png_size(png))
            if keyed:
                meta["key_error"] = info.get("reason", "background removal failed")
        candidate = {"id": candidate_id, **meta}

        def apply(npc):
            assets = npc.setdefault("assets", {})
            assets.setdefault("candidates", {}).setdefault(kind, []).append(candidate)
        lib.update(npc_id, apply)

    def _mark_animations_stale(self, npc_id):
        """A new sprite means the reference views, animation frames and model sheet made from the old one no longer match."""
        def apply(npc):
            a = npc.get("assets", {})
            for view in a.get("animation_views", {}).values():
                view["stale"] = True
            for clip in a.get("animations", {}).values():
                clip["stale"] = True
            if "sheet" in a:
                a["sheet"]["stale"] = True
                for view in a["sheet"].get("views", {}).values():
                    view["stale"] = True
            if "model_3d" in a:
                a["model_3d"]["stale"] = True
        self.lib.update(npc_id, apply)

    def _mark_derived_stale(self, npc_id):
        """A new portrait means the sprite, expressions and model sheet made from the old one no longer match."""
        def apply(npc):
            a = npc.get("assets", {})
            if a.get("sprite", {}).get("mode") == "match":
                a["sprite"]["stale"] = True
                if "model_3d" in a:
                    a["model_3d"]["stale"] = True
            for e in a.get("expressions", {}).values():
                e["stale"] = True
            if "sheet" in a:
                a["sheet"]["stale"] = True
            if "living" in a:
                a["living"]["stale"] = True
            if "head_3d" in a:
                a["head_3d"]["stale"] = True
        self.lib.update(npc_id, apply)
