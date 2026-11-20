"""One model on the GPU at a time.

NPC Forge has two kinds of GPU users: ComfyUI (Mage-Flow, Mage-Flow-Edit, the big picture engines, Hunyuan 3D) and the voice
workers (npcgen/voice). Left to themselves they share the card: ComfyUI keeps Mage-Flow and Mage-Flow-Edit loaded side by side,
and a voice model could load next to whatever ComfyUI holds. With "gpu" -> "one_model_at_a_time" on (the default), every user
claims the GPU first, naming the models it is about to use:

  - ComfyUI claims ("comfy", the model files its graph loads) in Comfy.submit, so every graph is covered, whoever builds it;
  - a voice worker claims ("voice", engine id) before each clip (VoiceRuntime.run).

When the claim names different models from what is loaded (Mage-Flow to Mage-Flow-Edit, a picture to a voice, Qwen3-TTS to
Kokoro...), the gate unloads first: the previous holder's release runs (a voice worker is stopped, its process ends), ComfyUI is
told to unload everything and free its memory, and nothing new starts until the card reads empty again:
  - ComfyUI's own reading (/system_stats) shows at most `idle_vram_mb` in use, and
  - on Windows, Windows' per-process counters (npcgen/gpumem.py) show ComfyUI's process holding at most `idle_vram_mb` and no
    other process holding more than `other_process_mb`. ComfyUI's reading alone is not enough, measured on an AMD card
    under Windows (2026-09-24): it cannot see other processes (0.8 GB "in use" while a voice worker held 6.8 GB), and after
    an unload it reads 0.76 GB within a second while its process really keeps 4.8 GB for another 12-22 s (once, with a voice
    model loading next to it, for 5 minutes). /free is sent again every 20 s while waiting.
If it does not empty within `wait_seconds`, something outside NPC Forge holds the GPU (LM Studio, a model loaded in ComfyUI by
hand, another NPC Forge, another app) and the step fails with GpuBusy, naming it, rather than loading on top of it. Its `code`
says which (gpu_busy, with the programs in `holders`; gpu_comfy_held; comfy_busy) for programs that read the job list.

Only the card ComfyUI draws on counts (2026-11-11). A PC with two GPUs (here a Radeon 780M built into the processor and an
R9700) has Windows counters for both: a program on the other card is not in ComfyUI's way, and counting it refused pictures
for nothing (a 24.6 GB LM Studio model). The card is found by the name ComfyUI reports, else by where its process holds memory;
when neither works every card counts, as before. On a GPU built into the processor its memory is mostly shared system memory,
so that is counted too, and the message says so when a separate card sits unused. check() (GET /api/gpu) reads all this
without unloading anything, for a program that wants to know before it asks for a picture.

ComfyUI 0.4x keeps the last model's weights in its dynamic VRAM cache after /free (4.6 GB on the R9700, measured 2026-11-11):
its own reading drops, its process does not, ever. ComfyUI hands that memory to its own next model, so a ComfyUI claim goes
ahead on ComfyUI's own reading then (before, every switch to another model was refused after 90 s, portrait to expression
included). A voice worker is another process and cannot use it: that claim is still refused, and the message suggests a
ComfyUI restart or its --disable-dynamic-vram start option.

Claims are made while holding the ComfyUI endpoint's operation lock (Comfy.run / run_entry / the big-engine runner hold it, and
VoiceRuntime.run takes it too), so a voice clip can never start in the middle of a picture. The same models drawing picture
after picture, or the same voice making clip after clip, stay loaded: nothing is reloaded between them.

When the job queue runs empty, ComfyUI is unloaded once it has stayed empty for `unload_idle_seconds` (120; the job worker
calls release_comfy). A job started in that time with the same models finds them still loaded: unloading at once made the
next job reload the same weights (Qwen Image 2.1 for a sheet started 43 s after its sprite, 2026-09-26) and, started
straight away, wait 12-22 s for the card to empty first. 0 unloads as soon as the queue is empty.

Off, the older rules still apply: the big picture engines unload around themselves (npcgen/engines.py) and a voice worker only
checks there is enough free memory before it starts (npcgen/voice/runtime.py).
"""
import os
import re
import threading
import time
import urllib.parse

from . import gpumem
from .comfy import Cancelled, ComfyError

DEFAULTS = {"one_model_at_a_time": True, "idle_vram_mb": 2048, "other_process_mb": 1536, "wait_seconds": 90,
            "unload_idle_seconds": 120}
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
        self.last_switch = None                  # {"from", "to", "seconds", "used_mb"} of the latest unload, for the UI

    def configure(self, cfg):
        """Read config "gpu" (see DEFAULTS)."""
        conf = (cfg or {}).get("gpu") or {}
        s = dict(DEFAULTS)
        s["one_model_at_a_time"] = bool(conf.get("one_model_at_a_time", DEFAULTS["one_model_at_a_time"]))
        for name in ("idle_vram_mb", "other_process_mb", "wait_seconds", "unload_idle_seconds"):
            try:
                s[name] = max(0, float(conf.get(name, DEFAULTS[name])))
            except (TypeError, ValueError):
                pass
        self.settings = s

    @property
    def strict(self):
        return self.settings["one_model_at_a_time"]

    def holder(self):
        with self._state:
            return self._holder[0] if self._holder else None

    def status(self):
        return {"one_model_at_a_time": self.strict, "loaded": describe(self.holder()) if self.holder() else None,
                "last_switch": self.last_switch, "unload_idle_seconds": self.settings["unload_idle_seconds"]}

    def reset(self):
        with self._state:
            self._holder = None
        self.last_switch = None

    # ---- claiming ----------------------------------------------------------------------------------------------------------
    def claim(self, key, release=None, comfy=None, cancel=None):
        """Make `key` the only thing on the GPU. The caller holds the ComfyUI operation lock (or has no ComfyUI). `release()`
        is how the gate unloads this holder when the next claim names something else; `comfy` is used to unload ComfyUI and
        to read the GPU memory. Does nothing when the rule is off or `key` already holds the GPU."""
        if not self.strict:
            return
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
        """`key` unloaded itself (an idle voice worker, ComfyUI freed when the queue ran empty)."""
        with self._state:
            if self._holder and self._holder[0] == key:
                self._holder = None

    def release_comfy(self, comfy):
        """When the job queue has stayed empty for `unload_idle_seconds`: if ComfyUI holds the GPU, unload it so nothing sits
        on the card between jobs."""
        if not self.strict:
            return
        with self._state:
            current = self._holder
            if not current or current[0][0] != "comfy":
                return
            self._holder = None
        _free(comfy)

    # ---- emptying the card ------------------------------------------------------------------------------------------------
    def _empty(self, comfy, key, cancel):
        """Unload everything in ComfyUI and wait until the GPU reads empty. Returns the VRAM in use (MB) at the end, or None
        when it cannot be read (ComfyUI not running: then nothing of ComfyUI's is on the GPU, and a stopped voice worker's
        memory went back when its process ended)."""
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
            look = self._look(comfy, comfy_pid)
            used, others = look["used_mb"], look["others"]
            if key and key[0] == "comfy" and _cache_only(look, limit):
                used = look["reading_mb"]          # ComfyUI's dynamic VRAM cache: its own next model reuses that memory
            if (used is None or used <= limit) and not others:
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
                           "another NPC Forge...) and try again." + _card_note(look), code="gpu_busy",
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
                "name": _device_name(dev.get("name")), "dynamic_vram": _dynamic_vram(d)}
    except Exception:
        return None


def _used_mb(comfy):
    """VRAM in use on ComfyUI's GPU in MB (the whole device: other processes count too), or None if it cannot be read."""
    stats = _stats(comfy)
    return stats["used_mb"] if stats else None


GATE = Gate()
configure, claim, drop, release_comfy, status = GATE.configure, GATE.claim, GATE.drop, GATE.release_comfy, GATE.status
check = GATE.check
