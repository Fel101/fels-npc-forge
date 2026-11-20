"""Picture engines of the studio: what turns a prompt and the character's portrait into a picture.

  mage      the built-in Mage-Flow-Edit graph (npcgen.comfy.edit_graph). Small, fast, weak at whole bodies and hands.
  qwen      Qwen-Image-Edit 2511 with the 4-step Lightning LoRA, run from a ComfyUI workflow file (API format with {{placeholders}},
            the same format Altera's `imagegen.workflows` takes). Measured against Mage-Flow on the same characters and scenes: real
            whole bodies, natural poses, detailed rooms, about 35 seconds a picture.
  qwen21    Qwen Image 2.1 Edit, no accelerator LoRA yet released for it. Measured against qwen (2026-09-21, one character, a
            full-length garden scene, 832x1216): comparable whole-body coherence and at least as good identity preservation, but
            about 2.2x slower (101 s vs 46 s) and it tended to have the character face the camera even when the scene said to
            look away, in both runs tried. Worth having as an option, not a default: see docs note below.

Qwen (either version) is a 20 GB model plus an 8-9 GB text encoder: on this PC (32 GB VRAM, 62 GB RAM) one picture peaks at about
29 GB of VRAM and 30 GB of RAM. Left loaded between pictures, the RAM crept up until Windows paged and the whole PC froze (measured
twice), so: ComfyUI is flushed (models unloaded) before and after every Qwen picture, a picture does not start without enough free
RAM, and while it runs the time, the VRAM and the free RAM are watched: on the first sign of trouble it is interrupted and freed,
and the studio makes the picture with Mage-Flow instead. A picture is never left running.

The MAIN picture of a Models character (its portrait, which every other picture starts from) has its own engines, PORTRAIT_ENGINES,
chosen in the same card. Measured on six adult characters (blonde Caucasian, Japanese, Korean, Black, Latina, redhead), same prompt
and seed, 1024x1024 (2026-09-20):
  Z-Image Turbo                 the most "model" faces, varied, natural skin, the fastest (16 s, 20 GB VRAM). Measured with the glossy
                                wording its faces read 19-23 to Altera's age check, so a portrait can read under the minimum age and then
                                Altera refuses to import it: draw it again or pick another engine.
  Qwen-Image 2512 + Lightning   filmic, natural, adult-looking, varied faces; about 30 s; the same heavy footprint as Qwen-Image-Edit
                                and the same text encoder and VAE, so it runs the careful way too. Reads 27-32 to the check.
  Mage-Flow (built in)          glossy glamour, but the faces look alike; about 20 s. The fallback for every engine above.
  Krea 2 Turbo                  photographic but plain and older-looking even with fashion wording. Not offered.
  FLUX.2 dev                    33.8 GB of weights plus a 33.9 GB text encoder does not fit this PC. Not tried.
Z-Image (cfg 1) ignores negative prompts, so the youth words of the setting's negative prompt do nothing there: the adult wording in the
positive prompt and the age check are what hold.

Qwen Image 2.1 (2026-09-21, no accelerator LoRA): first measured on one character (30-40 s, comparable quality, no deformities across
2 draws; gemma-4-e4b read it 38-40, qwen3.8-27b read it 30-35, both older than every other engine on that one face) -- promising
enough to justify a head-to-head against Z-Image using the ORIGINAL SIX-CHARACTER METHOD (same six characters as the 2026-09-20 table
above, same prompt
and seed per character, both engines, 1024x1024, 2026-09-21): image quality was a genuine tie -- zero deformities on either engine
across all 12 portraits, both gave six distinct, varied faces (neither repeated a look), and the two just have a different flavour
(Z-Image: more textured/filmic, closer to a candid photo; Qwen 2.1: smoother/softer, closer to a retouched editorial shot; Qwen 2.1's
outfit choices converged a little more across characters, e.g. two of six got a cream/beige blazer). The tie-break was the AGE CHECK,
re-run on all twelve: Qwen 2.1 read as old as or older than Z-Image on every one of the six characters, on both calibration vision
models (gemma-4-e4b and qwen/qwen3.8-27b), with real margin from 18 -- the opposite of Z-Image's thin 19-23 margin above. THE DEFAULT
(2026-09-21, chosen on this basis, the author's call): Qwen Image 2.1 (`qwen21`, 30 s at 25 steps -- 1.6 s slower than Z-Image, not
worth trading the safety margin for). Z-Image, Qwen 2512 and Mage-Flow stay selectable.
"""
import copy
import contextlib
import json
import re
import time
from pathlib import Path

from npcgen.comfy import Cancelled, ComfyError

HERE = Path(__file__).parent

ENGINES = {
    "mage": {"label": "Mage-Flow-Edit (built in)", "short": "Mage-Flow", "workflow": None},
    "qwen": {"label": "Qwen-Image-Edit 2511 + Lightning", "short": "Qwen", "workflow": HERE / "workflows" / "qwen_edit_2511_lightning.json"},
    # the same plus the Ultra-Realistic-Portrait LoRA (prithivMLmods, Apache 2.0) at 0.5: more skin texture, light and photographic
    # depth; at 0.7 the frame pulled closer and cropped the feet, at 0.5 it did not (measured on two scenes, one character)
    "qwen_real": {"label": "Qwen-Image-Edit 2511 + Lightning + realism LoRA", "short": "Qwen (realism)",
                  "workflow": HERE / "workflows" / "qwen_edit_2511_lightning_realism.json"},
    # Qwen Image 2.1's own edit node (no accelerator LoRA released yet): comparable quality to "qwen" but about 2.2x slower and it
    # tended to face the camera even when the scene said to look away (measured 2026-09-21, one character, one scene). An option,
    # not the default.
    "qwen21": {"label": "Qwen Image 2.1 Edit", "short": "Qwen 2.1", "workflow": HERE / "workflows" / "qwen_image_2_1_edit.json"},
}
DEFAULT_ENGINES = {"off": "mage", "romantic": "mage", "suggestive": "qwen", "explicit": "qwen"}

START_FREE_RAM_MB = 36000       # a Qwen picture needs about 30 GB of RAM: it does not start with less than this free
START_FREE_VRAM_MB = 29000      # the 32 GB card must be mostly free; LLM/TTS residency otherwise forces a dangerous spill

# min_free_ram_mb: what must be free before it starts (it needs about 20 GB of RAM; Qwen 2512 needs more). The order is the order of the list.
PORTRAIT_ENGINES = {
    "zimage": {"label": "Z-Image Turbo", "workflow": HERE / "workflows" / "zimage_turbo_portrait.json",
               "min_free_ram_mb": 32000, "min_free_vram_mb": 21000},
    "qwen2512": {"label": "Qwen-Image 2512 + Lightning", "workflow": HERE / "workflows" / "qwen_image_2512_portrait.json",
                 "min_free_ram_mb": START_FREE_RAM_MB, "min_free_vram_mb": START_FREE_VRAM_MB},
    # no accelerator LoRA released yet, so 25 steps (hardcoded in the workflow) instead of Lightning's 4; still about 30 s on this
    # PC, and it read as old as or older than every engine above on both age-check vision models (see the module note)
    "qwen21": {"label": "Qwen Image 2.1", "workflow": HERE / "workflows" / "qwen_image_2_1_portrait.json",
               "min_free_ram_mb": 32000, "min_free_vram_mb": 21000},
    "mage": {"label": "Mage-Flow (built in)", "workflow": None},
}
DEFAULT_PORTRAIT_ENGINE = "qwen21"      # was zimage until the 2026-09-21 six-character comparison (see the module note above)
PORTRAIT_SETTINGS = ("models",)          # the settings whose portraits these engines draw; every other setting keeps the built-in engine

ABORT_FREE_RAM_MB = 8000        # ... and is cancelled if the free RAM falls below this while it runs
MAX_SECONDS = 420               # a picture takes about 40 seconds; this is only the stall limit
VRAM_FULL = 0.985               # VRAM full for VRAM_FULL_SECONDS in a row = spilling into system memory = a crawl
VRAM_FULL_SECONDS = 30

_PLACEHOLDER = re.compile(r"\{\{\s*(\w+)\s*\}\}")
# which ComfyUI model folder holds what a loader node names
_LOADERS = {"UNETLoader": ("diffusion_models", "unet_name"), "CLIPLoader": ("text_encoders", "clip_name"),
            "VAELoader": ("vae", "vae_name"), "LoraLoaderModelOnly": ("loras", "lora_name"), "LoraLoader": ("loras", "lora_name"),
            "CheckpointLoaderSimple": ("checkpoints", "ckpt_name")}


class EngineError(ComfyError):
    """The engine cannot make this picture now (files missing, not enough memory, stalled). The message is safe to show."""


def load_template(path) -> dict:
    try:
        return json.loads(Path(path).read_text("utf-8"))
    except (OSError, ValueError) as e:
        raise EngineError(f"Cannot read the workflow {Path(path).name}: {e}") from None


def _placeholders(node):
    if isinstance(node, dict):
        for v in node.values():
            yield from _placeholders(v)
    elif isinstance(node, list):
        for v in node:
            yield from _placeholders(v)
    elif isinstance(node, str):
        yield from _PLACEHOLDER.findall(node)


def fill_workflow(template, values):
    """Copy of a workflow with {{name}} placeholders replaced. A string that is only a placeholder keeps the value's type
    (so {{seed}} becomes a number); a placeholder inside longer text is substituted as text. Same rules as Altera's."""
    unknown = sorted({m for m in _placeholders(template) if m not in values})
    if unknown:
        raise EngineError("The workflow uses unknown placeholders: " + ", ".join("{{" + u + "}}" for u in unknown))

    def walk(node):
        if isinstance(node, dict):
            return {k: walk(v) for k, v in node.items()}
        if isinstance(node, list):
            return [walk(v) for v in node]
        if isinstance(node, str):
            whole = _PLACEHOLDER.fullmatch(node.strip())
            if whole:
                return values[whole.group(1)]
            return _PLACEHOLDER.sub(lambda m: str(values[m.group(1)]), node)
        return node

    return walk(copy.deepcopy(template))


def required_models(template) -> list:
    """[(folder, file name)] the workflow's loader nodes ask for."""
    out = []
    for node in template.values():
        spec = _LOADERS.get(node.get("class_type")) if isinstance(node, dict) else None
        name = ((node.get("inputs") or {}).get(spec[1]) if spec else None)
        if spec and isinstance(name, str) and (spec[0], name) not in out:
            out.append((spec[0], name))
    return out


def missing_models(comfy, template) -> list:
    """The model files the workflow needs that ComfyUI does not have, as 'folder/name' ([] = all there)."""
    missing = []
    for folder, name in required_models(template):
        try:
            have = comfy.get_json(f"/models/{folder}", timeout=10)
        except ComfyError:
            raise
        except Exception as e:
            raise EngineError(f"Could not ask ComfyUI which models it has ({type(e).__name__}).") from None
        if name not in have:
            missing.append(f"{folder}/{name}")
    return missing


# ---- memory and the guarded run -----------------------------------------------------------------------------------------------

def _post(comfy, path, body=None):
    try:
        comfy._request("POST", path, data=json.dumps(body).encode() if body is not None else None,
                       headers={"Content-Type": "application/json"} if body is not None else None, timeout=10)
    except Exception:                                        # a flush that cannot be sent must never hide the real problem
        pass


def free_memory(comfy):
    """Ask ComfyUI to unload every model and free memory (it does so as soon as it is idle)."""
    _post(comfy, "/free", {"unload_models": True, "free_memory": True})


def cancel_and_free(comfy):
    _post(comfy, "/interrupt")
    free_memory(comfy)


def _stats(comfy):
    d = comfy.get_json("/system_stats", timeout=8)
    dev = d["devices"][0]
    return ((dev["vram_total"] - dev["vram_free"]) // 2 ** 20, dev["vram_total"] // 2 ** 20,
            d["system"]["ram_free"] // 2 ** 20, dev["vram_free"] // 2 ** 20)


def wait_idle(comfy, seconds=60, sleep=None, clock=None):
    sleep, clock = sleep or time.sleep, clock or time.monotonic
    end = clock() + seconds
    while clock() < end:
        try:
            q = comfy.get_json("/queue", timeout=8)
            if not q["queue_running"] and not q["queue_pending"]:
                return True
        except Exception:
            pass
        sleep(2)
    return False


def run_guarded(comfy, graph, sleep=None, clock=None, cancel=None, start_free_ram_mb=None, start_free_vram_mb=None):
    """Run a graph the careful way (see the module note). Returns the PNG bytes; raises EngineError on any trouble, after
    interrupting the job and freeing memory, and Cancelled when `cancel` (a threading.Event) is set. Models are unloaded again
    when it is done. The start thresholds reserve enough physical RAM and VRAM for the selected model; this matters while the
    same 32 GB GPU / 64 GB host also serves the LLM and TTS."""
    lock = getattr(comfy, "operation_lock", None)
    with lock if lock is not None else contextlib.nullcontext():
        return _run_guarded(comfy, graph, sleep, clock, cancel, start_free_ram_mb, start_free_vram_mb)


def _run_guarded(comfy, graph, sleep=None, clock=None, cancel=None, start_free_ram_mb=None, start_free_vram_mb=None):
    """Implementation under Comfy.operation_lock; split out so fake Comfy clients used by tests remain supported."""
    sleep, clock = sleep or time.sleep, clock or time.monotonic
    need_ram = start_free_ram_mb or START_FREE_RAM_MB
    need_vram = start_free_vram_mb or START_FREE_VRAM_MB
    cancel_and_free(comfy)                                   # nothing else may be running, and start from an empty GPU
    if not wait_idle(comfy, 60, sleep, clock):
        raise EngineError("ComfyUI is busy or not answering: it was asked to stop and free its memory, but did not go idle.")
    deadline, free_ram, free_vram = clock() + 60, 0, 0
    while True:
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        try:
            _, _, free_ram, free_vram = _stats(comfy)
        except Exception:
            free_ram = free_vram = 0
        if (free_ram >= need_ram and free_vram >= need_vram) or clock() > deadline:
            break
        sleep(2)
    if free_ram < need_ram or free_vram < need_vram:
        raise EngineError(f"Not enough free memory for this picture ({free_ram // 1024} GB RAM and {free_vram // 1024} GB VRAM "
                          f"free; about {need_ram // 1024} GB RAM and {need_vram // 1024} GB VRAM needed). Close programs or "
                          "unload models in LM Studio/TTS, or use the Mage-Flow engine.")
    try:
        prompt_id = comfy.submit(graph)
        start, full_since, misses = clock(), None, 0
        while True:
            sleep(2)
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            elapsed = clock() - start
            try:
                vram, vram_total, free, _ = _stats(comfy)
                misses = 0
            except Exception:
                misses += 1
                if misses >= 3:
                    raise EngineError("ComfyUI stopped answering while making the picture.") from None
                continue
            full_since = (full_since if full_since is not None else clock()) if vram > vram_total * VRAM_FULL else None
            if free < ABORT_FREE_RAM_MB:
                raise EngineError(f"The PC ran short of memory ({free // 1024} GB of RAM free) after {elapsed:.0f} s, so the picture "
                                  "was cancelled before it could freeze the PC.")
            if full_since is not None and clock() - full_since > VRAM_FULL_SECONDS:
                raise EngineError("The GPU memory stayed full and the picture crawled, so it was cancelled.")
            if elapsed > MAX_SECONDS:
                raise EngineError(f"The picture took more than {MAX_SECONDS} s, so it was cancelled.")
            entry = comfy.get_json(f"/history/{prompt_id}", timeout=15).get(prompt_id)
            if entry and entry.get("status", {}).get("completed") is not None:
                if entry["status"].get("status_str") != "success":
                    raise EngineError("ComfyUI could not make the picture with this workflow.")
                for node_out in entry.get("outputs", {}).values():
                    for img in node_out.get("images", []):
                        return comfy.fetch_image(img["filename"], img.get("subfolder", ""), img.get("type", "temp"))
                raise EngineError("ComfyUI finished but produced no picture.")
    except BaseException:
        cancel_and_free(comfy)
        wait_idle(comfy, 60, sleep, clock)
        raise
    finally:
        free_memory(comfy)                                   # models out of memory after every picture


# ---- the main picture of a Models character ---------------------------------------------------------------------------------------

def make_portrait_engine(get_engine, fallback):
    """The hook for jobs.add_portrait_engine. `get_engine()` says which PORTRAIT_ENGINES entry is chosen right now; `fallback` is
    the exception class (jobs.PortraitFallback) that makes the built-in engine draw the picture instead, with the reason kept."""
    def hook(*, npc, setting, prompt, size, seed, comfy, cancel):
        if npc.get("setting") not in PORTRAIT_SETTINGS:
            return None
        spec = PORTRAIT_ENGINES.get(get_engine())
        if not spec or not spec["workflow"]:
            return None                                       # the built-in engine
        try:
            template = load_template(spec["workflow"])
            missing = missing_models(comfy, template)
            if missing:
                raise EngineError("ComfyUI is missing " + ", ".join(missing))
            graph = fill_workflow(template, {"prompt": prompt["positive"], "seed": int(seed), "width": int(size[0]), "height": int(size[1])})
            png = run_guarded(comfy, graph, cancel=cancel, start_free_ram_mb=spec.get("min_free_ram_mb"),
                              start_free_vram_mb=spec.get("min_free_vram_mb"))
        except EngineError as e:
            raise fallback(f"{spec['label']} could not draw it: {e}") from None
        return {"png": png, "engine": spec["label"]}
    return hook
