"""Check what ComfyUI can fit on its GPU, including Windows per-process VRAM.

Local picture graphs use the suite lock while their models stay resident. With
One model at a time on, Forge also waits for ComfyUI and other programs to free
the card before switching models. ComfyUI's dynamic cache can still be used by
its next graph. Remote ComfyUI and CPU graphs do not take the local lock.
"""
import os
import re
import threading
import time
import urllib.parse

from . import fels_suite, gpumem, suite_gpu
from .comfy import Cancelled, ComfyError, beat

DEFAULTS = {"one_model_at_a_time": True, "unload_external_models": True, "idle_vram_mb": 2048, "other_process_mb": 1536,
            "wait_seconds": 90, "unload_idle_seconds": 120}
# The programs that hold a local chat model (LM Studio, its llama-server, Ollama): the ones unload_outside() frees.
CHAT_PROGRAMS = re.compile(r"llama-server|lm ?studio|^lms(\.exe)?$|ollama", re.I)
MODEL_SUFFIXES = (".safetensors", ".ckpt", ".pt", ".pth", ".bin", ".gguf", ".sft")
LOADER_INPUTS = {"Trellis2LoadModel": ("modelname",)}    # loaders that name their model without a file suffix (their pack finds the files)

_sleep, _clock = time.sleep, time.monotonic      # tests replace these
_UNREAD = object()


class GpuBusy(ComfyError):
    """The GPU did not empty after everything NPC Forge had loaded was unloaded. The message is safe to show. `code` says why,
    for programs that read the job list (gpu_busy: another program holds the card, named in `holders` [{"name", "mb"}];
    gpu_comfy_held: ComfyUI kept its memory, `comfy_mb` of it; comfy_busy: ComfyUI's queue stayed busy with someone else's
    job). `device` is the card ComfyUI draws on, when known."""

    def __init__(self, message, code=None, holders=None, device=None, comfy_mb=None):
        super().__init__(message)
        self.code, self.holders, self.device, self.comfy_mb = code, list(holders or []), device, comfy_mb


def graph_models(graph):
    """The model files a ComfyUI graph loads (every string input of a *Loader* node that names a model file), as a sorted
    tuple: two graphs with the same tuple use the same weights."""
    out = set()
    for node in (graph or {}).values():
        if not isinstance(node, dict):
            continue
        kind = str(node.get("class_type") or "")
        named = LOADER_INPUTS.get(kind, ())
        if "Loader" not in kind and not named:
            continue
        for key, value in (node.get("inputs") or {}).items():
            if isinstance(value, str) and (value.lower().endswith(MODEL_SUFFIXES) or key in named):
                out.add(value)
    return tuple(sorted(out))


def describe(key):
    """A short name for a holder, for messages and the status line."""
    if not key:
        return "nothing"
    kind, what = key[0], key[1]
    if kind == "voice":
        return f"the {what} voice model"
    names = [str(f).rsplit("/", 1)[-1].rsplit(".", 1)[0] for f in (what or ())]
    return "ComfyUI (" + (", ".join(names[:3]) + (" ..." if len(names) > 3 else "") if names else "a graph") + ")"


class Gate:
    def __init__(self):
        self.settings = dict(DEFAULTS)
        self._state = threading.Lock()           # guards the holder only; never held while calling out
        self._holder = None                      # (key, release) of what is on the GPU now, or None
        self._picture_lock = None
        self._picture_endpoint = None
        self._next = None                        # One model at a time as switched while a job ran: applies when the next starts
        self.last_switch = None                  # {"from", "to", "seconds", "used_mb"} of the latest unload, for the UI

    def configure(self, cfg):
        """Read config "gpu" (see DEFAULTS)."""
        conf = (cfg or {}).get("gpu") or {}
        s = dict(DEFAULTS)
        s["one_model_at_a_time"] = bool(conf.get("one_model_at_a_time", DEFAULTS["one_model_at_a_time"]))
        # On unless config.json says false: Altera leaves freeing its chat model to a suite Forge (contract 8).
        s["unload_external_models"] = conf.get("unload_external_models", DEFAULTS["unload_external_models"]) is not False
        for name in ("idle_vram_mb", "other_process_mb", "wait_seconds", "unload_idle_seconds"):
            try:
                s[name] = max(0, float(conf.get(name, DEFAULTS[name])))
            except (TypeError, ValueError):
                pass
        self.settings = s
        self._next = None

    @property
    def strict(self):
        return self.settings["one_model_at_a_time"]

    def switch(self, on, busy):
        """The page's One model at a time switch. A running job finishes as it started (`busy`): the change then applies
        when the next job starts (start_job). True when it applied at once."""
        if busy:
            self._next = bool(on)
            return False
        self._next = None
        self.settings["one_model_at_a_time"] = bool(on)
        return True

    def start_job(self):
        """A job starts: a switch changed while the last one ran applies now."""
        if self._next is not None:
            self.settings["one_model_at_a_time"], self._next = self._next, None

    def held_the_other_way(self):
        """Whether Forge holds the suite lock for pictures the way the switch no longer says (alone with the switch off, or
        shared with it on): the next picture lets go and takes it again, and an idle Forge can let go at once."""
        lock = self._picture_lock
        return lock is not None and lock.alone != self.strict

    def holder(self):
        with self._state:
            return self._holder[0] if self._holder else None

    def status(self):
        lock = self._picture_lock
        return {"one_model_at_a_time": self.strict, "switch": self.strict if self._next is None else self._next,
                "unload_external_models": self.settings.get("unload_external_models", True),
                "alone": bool(lock is not None and lock.alone),
                "loaded": describe(self.holder()) if self.holder() else None,
                "last_switch": self.last_switch, "unload_idle_seconds": self.settings["unload_idle_seconds"]}

    def reset(self):
        with self._state:
            self._holder = None
        self.last_switch = None
        self._release_picture_lock()

    def _release_picture_lock(self):
        if self._picture_lock is not None:
            self._picture_lock.release()
            self._picture_lock = self._picture_endpoint = None

    def claim_picture(self, comfy, graph, cancel):
        """Take the suite lock before the ComfyUI graph's models load. The endpoint operation lock is already held."""
        address = suite_gpu.local_origin(comfy.base)
        if address is None:
            return
        new = self._picture_lock is None
        if not new and comfy.operation_lock is not self._picture_endpoint:
            raise GpuBusy("Another local ComfyUI still holds Forge's model. Wait for it to unload and retry.", code="gpu_busy")
        if not new and self.held_the_other_way():
            self._release_comfy_locked(comfy)    # the switch changed since the lock was taken: let go, then take it the new way
            new = True
        if new:
            stats = _stats(comfy)
            if stats is None or stats.get("cpu"):
                return
            lock = fels_suite.GpuLock("npc-forge")
            suite_gpu.acquire_pictures(lock, self.strict, self.settings["wait_seconds"], cancel,
                                       allow_external_unload=lambda: self.settings.get("unload_external_models", True))
            self._picture_lock, self._picture_endpoint = lock, comfy.operation_lock
        try:
            self.claim(("comfy", graph_models(graph)), comfy=comfy, cancel=cancel)
        except BaseException:
            if new:
                self._release_picture_lock()
            raise

    # ---- claiming ----------------------------------------------------------------------------------------------------------
    def claim(self, key, release=None, comfy=None, cancel=None):
        """Make `key` the only thing on the GPU. The caller holds the ComfyUI operation lock (or has no ComfyUI). `release()`
        is how the gate unloads this holder when the next claim names something else; `comfy` is used to unload ComfyUI and
        to read the GPU memory. Does nothing when the rule is off or `key` already holds the GPU."""
        if not self.strict:
            return
        if key[0] != "comfy" and self._picture_lock is not None:
            self.release_comfy(comfy)
        with self._state:
            current = self._holder
            if current and current[0] == key:
                self._holder = (key, release or current[1])
                return
            self._holder = None
        started = _clock()
        if current and current[1] is not None:
            try:
                current[1]()
            except Exception:
                pass
        used = self._empty(comfy, key, cancel)
        with self._state:
            self._holder = (key, release)
        self.last_switch = {"from": describe(current[0]) if current else None, "to": describe(key),
                            "seconds": round(_clock() - started, 1), "used_mb": used}

    def drop(self, key):
        """`key` unloaded its model."""
        with self._state:
            if self._holder and self._holder[0] == key:
                self._holder = None

    def release_comfy(self, comfy):
        """Unload ComfyUI and release its suite lease after idle or before asking another app to work."""
        lock = getattr(comfy, "operation_lock", None)
        if lock is not None:
            with lock:
                return self._release_comfy_locked(comfy)
        return self._release_comfy_locked(comfy)

    def _release_comfy_locked(self, comfy):
        suite_held = self._picture_lock is not None and self._picture_endpoint is getattr(comfy, "operation_lock", None)
        with self._state:
            current = self._holder
            if not suite_held and (not current or current[0][0] != "comfy"):
                return
            if current and current[0][0] == "comfy":
                self._holder = None
        try:
            if suite_held:
                _wait_idle(comfy, self.settings["wait_seconds"])
            _free(comfy)
        finally:
            if suite_held:
                self._release_picture_lock()

    # ---- emptying the card ------------------------------------------------------------------------------------------------
    def _empty(self, comfy, key, cancel):
        """Unload everything in ComfyUI and wait until the GPU reads empty. Returns the VRAM in use (MB) at the end, or None
        when it cannot be read."""
        if comfy is None:
            return None
        limit, wait = self.settings["idle_vram_mb"], self.settings["wait_seconds"]
        if not _wait_idle(comfy, wait, cancel):
            raise GpuBusy(f"ComfyUI is still busy with another job, so {describe(key)} was not started (one model on the "
                          "GPU at a time). Let that job finish, or stop it in ComfyUI, and try again.", code="comfy_busy")
        _free(comfy)
        comfy_pid = _comfy_pid(comfy)
        deadline = asked = _clock()
        deadline += wait
        while True:
            beat(cancel, "Waiting for ComfyUI to free GPU memory")
            look = self._look(comfy, comfy_pid)
            used, others = look["used_mb"], look["others"]
            if key and key[0] == "comfy" and _cache_only(look, limit):
                used = look["reading_mb"]          # ComfyUI's dynamic VRAM cache: its own next model reuses that memory
            if (used is None or used <= limit) and not others:
                beat(cancel)
                return used
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            if _clock() > deadline:
                raise self._refusal(key, look)
            if _clock() - asked >= 20:
                _free(comfy)                      # ComfyUI frees only when idle and on its own timer: ask again
                asked = _clock()
            _sleep(1)

    def _look(self, comfy, comfy_pid, stats=_UNREAD, skip=()):
        """One reading of the card ComfyUI draws on, the way the gate judges it: {stats (ComfyUI's own /system_stats reading,
        or None), card (the card's id in Windows' counters, or None: then every card counts, as before cards were told
        apart), device (its name), integrated, comfy_mb (what ComfyUI's process holds there, by Windows' counters), used_mb
        (the larger of that and ComfyUI's own reading), others [(name, MB)] (other programs over other_process_mb there,
        leaving out the pids in `skip`)}."""
        stats = _stats(comfy) if stats is _UNREAD else stats
        reading = stats["used_mb"] if stats else None
        device = stats["name"] if stats else None
        card, info = _card_by_name(device)
        rows = None
        if comfy_pid is not None or reading is None:
            # (ComfyUI running but its process not found: the counters could not tell its memory from another program's)
            rows = _processes(shared=True) if info and info["integrated"] else _processes()
        if rows is not None and card is None and comfy_pid is not None:
            card, info = _card_by_process(rows, comfy_pid)
        if rows is not None and card is not None:
            rows = [r for r in rows if r["adapter"] == card]        # only programs on ComfyUI's own card can be in its way
        comfy_mb = None
        used = reading
        if rows is not None and comfy_pid is not None:
            comfy_mb = sum(r["mb"] for r in rows if r["pid"] == comfy_pid)
            used = comfy_mb if used is None else max(used, comfy_mb)
        return {"stats": stats, "card": card, "device": (info or {}).get("name") or device,
                "integrated": (info or {}).get("integrated"), "comfy_mb": comfy_mb, "reading_mb": reading, "used_mb": used,
                "others": self._others(comfy_pid, rows, skip)}

    def _refusal(self, key, look):
        """The GpuBusy for a card that did not empty in time: another program on it, or ComfyUI's own memory."""
        wait, others, device = self.settings["wait_seconds"], look["others"], look["device"]
        on = f" on the {device}" if device else ""
        if others:
            held = ", ".join(f"{name} ({mb / 1024:.1f} GB)" for name, mb in others)
            return GpuBusy(f"Another program is using the GPU: {held}{on}. {describe(key).capitalize()} was not started "
                           "(one model on the GPU at a time). Close it or unload its model (LM Studio, a voice app, "
                           "another NPC Forge...) and try again." + _unload_note(self.settings, others) + _card_note(look),
                           code="gpu_busy",
                           holders=[{"name": name, "mb": mb} for name, mb in others], device=device)
        used = look["used_mb"] or 0
        return GpuBusy(f"ComfyUI still holds {used / 1024:.1f} GB of GPU memory{on} {wait:.0f} s after it was told to unload "
                       f"everything, so {describe(key)} was not started (one model on the GPU at a time). "
                       + _held_advice(look, self.settings["idle_vram_mb"]) + _card_note(look),
                       code="gpu_comfy_held", device=device, comfy_mb=used)

    def check(self, comfy, forge_busy=False):
        """What the GPU looks like for NPC Forge's next job, read the way the gate reads it before a picture but without
        unloading anything or waiting (GET /api/gpu). `forge_busy`: NPC Forge itself is running a job, so ComfyUI being busy
        or full is its own doing and a new job only waits its turn. Returns {ready, code, message, device, integrated,
        total_mb, free_mb, comfy_mb, forge_loaded, holders [{name, mb}], other_gpus [names], warning, one_model_at_a_time}."""
        holder, limit = self.holder(), self.settings["idle_vram_mb"]
        out = {"ready": False, "code": None, "message": "", "device": None, "integrated": None, "total_mb": None,
               "free_mb": None, "comfy_mb": None, "forge_loaded": describe(holder) if holder else None, "holders": [],
               "other_gpus": [], "warning": None, "one_model_at_a_time": self.strict}
        stats = _stats(comfy) if comfy is not None else None
        if stats is None:
            where = f" at {comfy.base}" if getattr(comfy, "base", None) else ""
            out.update(code="comfy_down", message=f"ComfyUI is not answering{where}. Start ComfyUI first.")
            return out
        # NPC Forge's own voice worker (and the Python a venv's python.exe starts) is not in the way: the next claim stops it
        own = _own_processes() if holder is not None and holder[0] == "voice" else ()
        look = self._look(comfy, _comfy_pid(comfy), stats=stats, skip=own)
        others = [{"name": name, "mb": mb} for name, mb in look["others"]]
        gpus = _adapters() or []
        out.update(device=look["device"], integrated=look["integrated"], total_mb=stats["total_mb"], free_mb=stats["free_mb"],
                   comfy_mb=look["comfy_mb"] if look["comfy_mb"] is not None else stats["used_mb"], holders=others,
                   other_gpus=[g["name"] for g in gpus if g["luid"] != look["card"]] if look["card"] else [],
                   warning=_card_note(look).strip() or None)
        device = look["device"] or "GPU"
        own = holder is not None and holder[0] == "comfy"           # ComfyUI holds NPC Forge's own models: it unloads them itself
        if forge_busy:
            out.update(ready=True, message="NPC Forge is working on a job: a new one waits its turn in the queue.")
        elif others and self.strict:
            first = others[0]
            out.update(code="gpu_busy", message=f"{first['name']} holds {first['mb'] / 1024:.1f} GB of the {device}: unload its "
                       "model or close it first (one model on the GPU at a time).")
        elif _queue_busy(comfy):
            out.update(code="comfy_busy", message="ComfyUI is busy with a job that is not NPC Forge's: let it finish, or stop it "
                                                  "in ComfyUI.")
        elif self.strict and not own and (look["used_mb"] or 0) > limit and not _cache_only(look, limit):
            out.update(code="gpu_comfy_held", message=f"ComfyUI holds {(look['used_mb'] or 0) / 1024:.1f} GB of the {device} "
                       "although NPC Forge has nothing loaded there. " + _held_advice(look, limit))
        else:
            free = f" ({stats['free_mb'] / 1024:.1f} GB free)" if stats["free_mb"] is not None else ""
            out.update(ready=True, message=f"Ready: ComfyUI draws on the {device}{free}.")
            if _cache_only(look, limit):
                out["message"] += (f" ComfyUI keeps {look['comfy_mb'] / 1024:.1f} GB of its last model in its dynamic VRAM cache, "
                                   "which its next picture reuses.")
        if out["warning"]:
            out["message"] += " " + out["warning"]
        return out

    def _others(self, comfy_pid, rows, skip=()):
        """[(name, MB)] of processes other than ComfyUI, this one and `skip` holding more than other_process_mb of GPU memory."""
        if not rows:
            return []
        limit, mine = self.settings["other_process_mb"], os.getpid()
        big = {}
        for row in rows:
            if row["pid"] not in (comfy_pid, mine) and row["pid"] not in skip:
                big[row["pid"]] = big.get(row["pid"], 0) + row["mb"]
        return [(_process_name(pid), mb) for pid, mb in sorted(big.items(), key=lambda kv: -kv[1]) if mb > limit]


def _comfy_pid(comfy):
    try:
        parsed = urllib.parse.urlsplit(getattr(comfy, "base", "") or "")
        if parsed.hostname not in ("127.0.0.1", "localhost", "::1"):
            return None                           # ComfyUI on another machine: its GPU is not this one
        return gpumem.listening_pid(parsed.port or 80)
    except Exception:
        return None


def _processes(shared=False):
    try:
        return gpumem.processes(shared=shared)
    except Exception:
        return None


def _own_processes():
    """The processes NPC Forge started (its voice workers and what they started), an empty set where they cannot be read."""
    try:
        return gpumem.descendants(os.getpid())
    except Exception:
        return set()


def _adapters():
    """This PC's GPUs from Windows ([{"luid", "name", "dedicated_mb", "integrated"}]), or None where they cannot be read."""
    try:
        return gpumem.adapters()
    except Exception:
        return None


def _device_name(raw):
    """'cuda:0 AMD Radeon AI PRO R9700 : native' (ComfyUI's /system_stats) -> 'AMD Radeon AI PRO R9700'."""
    name = re.sub(r"^\s*\w+:\d+\s+", "", str(raw or "")).split(" : ")[0].strip()
    return name or None


def _card_by_name(name):
    """(Windows' id of the card ComfyUI names, its adapters() row), or (None, None) when no single card has that name."""
    gpus = _adapters() if name else None
    if not gpus:
        return None, None
    want = name.casefold()
    hits = [g for g in gpus if g["name"].casefold() == want] or \
           [g for g in gpus if g["name"].casefold() in want or want in g["name"].casefold()]
    return (hits[0]["luid"], hits[0]) if len(hits) == 1 else (None, None)


def _card_by_process(rows, comfy_pid):
    """The card ComfyUI's process holds the most memory on (its id, its adapters() row or None), or (None, None)."""
    held = {}
    for row in rows or ():
        if row["pid"] == comfy_pid:
            held[row["adapter"]] = held.get(row["adapter"], 0) + row["mb"]
    if not held:
        return None, None
    card = max(held, key=held.get)
    return card, next((g for g in _adapters() or () if g["luid"] == card), None)


def _card_note(look):
    """' ComfyUI is drawing on the GPU built into the processor although ...' when another, separate card is in this PC; ''."""
    if not look.get("integrated"):
        return ""
    others = [g for g in _adapters() or () if not g["integrated"] and g["luid"] != look.get("card")]
    if not others:
        return ""
    return (f" ComfyUI is drawing on the {look.get('device') or 'GPU'}, the GPU built into the processor, although this PC also "
            f"has the {others[0]['name']}: pictures are much slower there and share the PC's memory. Start ComfyUI on that card "
            "instead (its --cuda-device start option).")


def _cache_only(look, limit):
    """Whether what ComfyUI's process holds beyond its own count is its dynamic VRAM cache: ComfyUI 0.4x (its comfy-aimdo
    package, not started with --disable-dynamic-vram) keeps the last model's weights after an unload, counts that memory as
    free and hands it to its own next model; /free does not empty it (measured 2026-11-11 on the R9700: 4.6 GB for as long as
    it was watched, which refused every switch to another model). A voice worker, being another process, cannot use it."""
    reading, held = look.get("reading_mb"), look.get("comfy_mb")
    return bool((look.get("stats") or {}).get("dynamic_vram")) and reading is not None and held is not None \
        and reading <= limit < held


def _unload_note(settings, others):
    """When a chat model holds the card and unloading LM Studio and Ollama is turned off: the switch that would free it."""
    if not settings.get("unload_external_models", True) and any(CHAT_PROGRAMS.search(name) for name, _mb in others):
        return (" To let NPC Forge unload LM Studio's and Ollama's models itself, turn on Allow unloading LM Studio and "
                "Ollama models on the Models card.")
    return ""


def _held_advice(look, limit):
    """What to do about memory ComfyUI keeps after an unload."""
    if _cache_only(look, limit):
        return (f"ComfyUI itself counts only {look['reading_mb'] / 1024:.1f} GB in use: the rest is its dynamic VRAM cache, "
                "which an unload does not empty and only ComfyUI's own pictures can reuse. Restart ComfyUI, or start it with "
                "--disable-dynamic-vram so that this does not happen again, and try again.")
    return "Restart ComfyUI (or, if a model was loaded in ComfyUI by hand, unload it there) and try again."


def _dynamic_vram(system_stats):
    """Whether ComfyUI can keep a dynamic VRAM cache: its comfy-aimdo package is installed and it was not started with
    --disable-dynamic-vram (from /system_stats)."""
    try:
        system = system_stats.get("system") or {}
        names = {str(p.get("name")) for p in system.get("comfy_package_versions") or () if isinstance(p, dict)}
        return "comfy-aimdo" in names and "--disable-dynamic-vram" not in [str(a) for a in system.get("argv") or ()]
    except Exception:
        return False


def _queue_busy(comfy):
    """Whether ComfyUI's own queue has a job running or waiting (False when it cannot be asked)."""
    try:
        q = comfy.get_json("/queue", timeout=8)
        return bool(q.get("queue_running") or q.get("queue_pending"))
    except Exception:
        return False


def _process_name(pid):
    return gpumem.process_name(pid)


def _free(comfy):
    try:
        comfy.free_memory()
    except Exception:
        pass


def _wait_idle(comfy, seconds, cancel=None):
    """True once ComfyUI's queue is empty (or ComfyUI cannot be asked); False if it stays busy for `seconds`."""
    end = _clock() + seconds
    while True:
        try:
            q = comfy.get_json("/queue", timeout=8)
            if not q.get("queue_running") and not q.get("queue_pending"):
                return True
        except Exception:
            return True
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        if _clock() > end:
            return False
        _sleep(1)


def _stats(comfy):
    """ComfyUI's own reading of the GPU it draws on: {used_mb, total_mb, free_mb, name, dynamic_vram (see _dynamic_vram)}, or
    None if it cannot be read. used_mb is the whole device's (other processes count too, where the driver lets it see them)."""
    try:
        d = comfy.get_json("/system_stats", timeout=8)
        dev = d["devices"][0]
        total, free = int(dev["vram_total"]), int(dev["vram_free"])
        return {"used_mb": int((total - free) // 2 ** 20), "total_mb": int(total // 2 ** 20), "free_mb": int(free // 2 ** 20),
                "name": _device_name(dev.get("name")), "dynamic_vram": _dynamic_vram(d), "cpu": dev.get("type") == "cpu"}
    except Exception:
        return None


def _used_mb(comfy):
    """VRAM in use on ComfyUI's GPU in MB (the whole device: other processes count too), or None if it cannot be read."""
    stats = _stats(comfy)
    return stats["used_mb"] if stats else None


GATE = Gate()
configure, claim, drop, release_comfy, status = GATE.configure, GATE.claim, GATE.drop, GATE.release_comfy, GATE.status
check = GATE.check
