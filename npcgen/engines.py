"""Picture engines: which model draws a picture from text (an image engine) or changes an existing one (an edit engine).

  mage          Mage-Flow and Mage-Flow-Edit, built in (npcgen.comfy.t2i_graph / edit_graph). What every prompt in this app was tuned
                on, light on memory, and what a picture is drawn with when another engine cannot draw it AND the "engine_fallback" option
                is on. Not the default since 2026-10-13: its licence is unclear (docs/licences.md).
  zimage        Z-Image Turbo, text to image. 8 steps, the fastest (about 16 s a portrait on a 32 GB card). It pulls hard toward
                famous characters (2026-09-23): a 40-year-old Black woman pirate captain with long curly hair came out as Jack
                Sparrow, a man, in 17 of 17 draws (4 seeds, and rewording the hair, removing the hat, changing the heritage or
                stressing "woman" did not help; only hair tied up in a bun did). Mage-Flow, Qwen Image 2.1 and Qwen-Image 2512
                drew her correctly from the same prompt, 6 of 6. Kept as an option with that warning on it.
  qwen2512      Qwen-Image 2512 with the 4-step Lightning LoRA, text to image. About 30 s. The default image engine (Apache-2.0).
  qwen21        Qwen Image 2.1, text to image. No accelerator LoRA exists for it yet, so 25 steps; about 30 s.
  qwen2511      Qwen-Image-Edit 2511 with the 4-step Lightning LoRA, edit. Real whole bodies and natural poses; about 35 s. The default
                edit engine (Apache-2.0).
                Walk cycle, side by side with Mage-Flow-Edit on one character (2026-09-23, 4 directions x 4 frames): the character
                stayed identical in all 16 frames but the legs barely moved (leg movement 0.09 / 0.18 / 0.10 down / side / up;
                about 0.3 is a real walk), where Mage-Flow-Edit moved them more (0.15 / 0.64 / 0.15) but lost the coat in two
                side frames. The whole clip took about 14.5 min with the model kept loaded, and free RAM stayed at 38.8-39.1 GB.
  qwen21_edit   Qwen Image 2.1 Edit. Comparable to qwen2511 but about 2.2x slower.

The graphs are transcribed from ComfyUI workflows, where each one was measured (same nodes, samplers, shift and
LoRA). They are built in Python rather than filled in from JSON so an edit can take one to three references: animation frames
send the drawn pose, the front sprite and the side view together. Every non-Mage engine runs at cfg 1, so it ignores negative
prompts: the positive wording is all that steers it.

MEMORY. The Qwen models are about 20 GB plus an 8-9 GB text encoder: one picture peaks near 29 GB of VRAM and 30 GB of RAM on this
PC (32 GB card, 62 GB RAM). Left loaded between pictures while other things ran, the RAM crept up until Windows paged and the PC
froze (twice). So a big engine runs the careful way:
  - ComfyUI is told to unload everything whenever the weights change (Mage to Qwen, Qwen 2.1 to Qwen 2511, Qwen back to Mage...),
    and a big picture does not start until enough RAM and VRAM are free;
  - the same big engine drawing picture after picture (a walk cycle is 14 of them) stays loaded, but only while at least
    KEEP_LOADED_FREE_RAM_MB of RAM stays free; below that it is unloaded and the full start check runs again;
  - while a picture runs, a watchdog reads the free RAM, the VRAM and the time every 2 s: on the first sign of trouble the
    picture is interrupted, ComfyUI unloads everything, and that picture is drawn with Mage-Flow instead (with a note saying why)
    when the "engine_fallback" option is on; with it off (the default) the picture fails and says why;
  - when the job queue has stayed empty for gpu "unload_idle_seconds" (2 minutes), ComfyUI is told to unload (Jobs calls
    release());
  - local graphs acquire the suite GPU lock before checking the card and loading weights.
"""
import contextlib
import io
import os
import threading
import time

from PIL import Image

from . import gpu, hardware
from .comfy import Cancelled, ComfyError, beat, edit_graph, t2i_graph

START_FREE_RAM_MB = 36000       # a Qwen picture needs about 30 GB of RAM: it does not start with less than this free ...
START_FREE_VRAM_MB = 29000      # ... and the 32 GB card must be mostly free (an LLM or TTS left on it forces a dangerous spill)
KEEP_LOADED_FREE_RAM_MB = 16000  # the same big engine stays loaded for its next picture only while this much RAM is still free
ABORT_FREE_RAM_MB = 8000        # a running picture is cancelled if the free RAM falls below this
VRAM_FULL = 0.985               # VRAM this full for VRAM_FULL_SECONDS in a row = spilling into system memory = a crawl
VRAM_FULL_SECONDS = 30
MAX_SECONDS = 420               # a picture takes well under a minute (Qwen 2.1: about 100 s); this is only the stall limit
MAX_BIG_MEGAPIXELS = 1.1        # the sizes the big engines were measured at; larger requests are drawn at this and scaled up
FILES_CACHE_SECONDS = 30

# which ComfyUI model folder holds what a loader node names
_LOADERS = {"UNETLoader": ("diffusion_models", "unet_name"), "CLIPLoader": ("text_encoders", "clip_name"),
            "VAELoader": ("vae", "vae_name"), "LoraLoaderModelOnly": ("loras", "lora_name")}

_sleep, _clock = time.sleep, time.monotonic      # tests replace these
_LOADED = {}                                      # ComfyUI endpoint -> the weights it holds ("mage", an engine's weights, or None)
_FILES = {}                                       # (endpoint, folder) -> (time, [file names])
class EngineError(ComfyError):
    """This engine cannot make the picture now (files missing, not enough memory, stalled). The message is safe to show.
    `transient`: it may work a little later (memory not free yet, the GPU not empty yet), so a job tries the step again.
    `code`, `holders`, `device`: the machine-readable reason (npcgen/gpu.py GpuBusy, no_model, low_memory...), kept when
    another error is wrapped in this one."""

    def __init__(self, message, transient=False, code=None, holders=None, device=None):
        super().__init__(message)
        self.transient = transient
        self.code, self.holders, self.device = code, list(holders or []), device


def _wrapped(message, error, transient=None):
    """An EngineError saying `message` that keeps the reason (`code`, `holders`, `device`) of the error it replaces."""
    return EngineError(message, transient=getattr(error, "transient", False) if transient is None else transient,
                       code=getattr(error, "code", None), holders=getattr(error, "holders", None), device=getattr(error, "device", None))


_STRICT = threading.local()


@contextlib.contextmanager
def strict(on=True):
    """While on, a picture engine that cannot draw (not enough memory, a stall, missing files) fails with its reason instead
    of Mage-Flow drawing the picture in its place. Mage-Flow can be a licence the work may not use (its README says research
    only), so the fallback is off unless the "engine_fallback" option turns it on (the default since 2026-10-13)."""
    before = getattr(_STRICT, "on", False)
    _STRICT.on = bool(on)
    try:
        yield
    finally:
        _STRICT.on = before


def _refused(spec, error):
    """What a picture engine's failure becomes: in strict mode the error itself (the caller does not fall back), else None."""
    if getattr(_STRICT, "on", False):
        return _wrapped(f"{spec['label']} could not draw it: {error}", error)
    return None


# ---- graph builders ------------------------------------------------------------------------------------------------------------
def _node(cls, **inputs):
    return {"class_type": cls, "inputs": inputs}


def _refs_size(g, size, megapixels, first_ref):
    """(width, height) for the empty latent: the size asked for, or the first reference scaled to `megapixels` (like
    edit_graph does with size=None)."""
    if size is not None:
        return int(size[0]), int(size[1])
    g["90"] = _node("ImageScaleToTotalPixels", image=first_ref, upscale_method="lanczos",
                    megapixels=min(float(megapixels), MAX_BIG_MEGAPIXELS), resolution_steps=16)
    g["91"] = _node("GetImageSize", image=["90", 0])
    return ["91", 0], ["91", 1]


def zimage_t2i(prompt, width, height, seed, steps):
    return {
        "1": _node("UNETLoader", unet_name="z_image_turbo_bf16.safetensors", weight_dtype="default"),
        "2": _node("CLIPLoader", clip_name="qwen_3_4b.safetensors", type="lumina2", device="default"),
        "3": _node("VAELoader", vae_name="ae.safetensors"),
        "4": _node("ModelSamplingAuraFlow", model=["1", 0], shift=3.0),
        "5": _node("CLIPTextEncode", clip=["2", 0], text=prompt),
        "6": _node("ConditioningZeroOut", conditioning=["5", 0]),
        "7": _node("EmptySD3LatentImage", width=int(width), height=int(height), batch_size=1),
        "8": _node("KSampler", model=["4", 0], positive=["5", 0], negative=["6", 0], latent_image=["7", 0], seed=int(seed),
                   steps=int(steps), cfg=1.0, sampler_name="res_multistep", scheduler="simple", denoise=1),
        "9": _node("VAEDecode", samples=["8", 0], vae=["3", 0]),
        "10": _node("PreviewImage", images=["9", 0]),
    }


def qwen2512_t2i(prompt, width, height, seed, steps):
    return {
        "1": _node("UNETLoader", unet_name="qwen_image_2512_fp8_e4m3fn.safetensors", weight_dtype="default"),
        "2": _node("CLIPLoader", clip_name="qwen_2.5_vl_7b_fp8_scaled.safetensors", type="qwen_image", device="default"),
        "3": _node("VAELoader", vae_name="qwen_image_vae.safetensors"),
        "4": _node("LoraLoaderModelOnly", model=["1", 0], lora_name="Qwen-Image-Lightning-4steps-V2.0.safetensors", strength_model=1.0),
        "5": _node("ModelSamplingAuraFlow", model=["4", 0], shift=3.1),
        "6": _node("CLIPTextEncode", clip=["2", 0], text=prompt),
        "7": _node("ConditioningZeroOut", conditioning=["6", 0]),
        "8": _node("EmptySD3LatentImage", width=int(width), height=int(height), batch_size=1),
        "9": _node("KSampler", model=["5", 0], positive=["6", 0], negative=["7", 0], latent_image=["8", 0], seed=int(seed),
                   steps=int(steps), cfg=1.0, sampler_name="euler", scheduler="simple", denoise=1),
        "10": _node("VAEDecode", samples=["9", 0], vae=["3", 0]),
        "11": _node("PreviewImage", images=["10", 0]),
    }


_QWEN21 = {
    "1": _node("UNETLoader", unet_name="qwen_image_2.1_int8_convrot.safetensors", weight_dtype="default"),
    "2": _node("CLIPLoader", clip_name="qwen3vl_8b_int8_convrot.safetensors", type="qwen_image", device="default"),
    "3": _node("VAELoader", vae_name="qwen_image_2.1_vae_bf16.safetensors"),
}


def qwen21_t2i(prompt, width, height, seed, steps):
    g = {k: {**v, "inputs": dict(v["inputs"])} for k, v in _QWEN21.items()}
    g.update({
        "4": _node("TextEncodeQwenImage21", clip=["2", 0], prompt=prompt, negative_prompt="", resolution=1024),
        "5": _node("EmptyLatentImage", width=int(width), height=int(height), batch_size=1),
        "6": _node("KSampler", model=["1", 0], positive=["4", 0], negative=["4", 1], latent_image=["5", 0], seed=int(seed),
                   steps=int(steps), cfg=1.0, sampler_name="euler", scheduler="simple", denoise=1),
        "7": _node("VAEDecode", samples=["6", 0], vae=["3", 0]),
        "8": _node("PreviewImage", images=["7", 0]),
    })
    return g


def qwen2511_edit(refs, prompt, negative, seed, steps, size=None, megapixels=1.0):
    g = {
        "1": _node("UNETLoader", unet_name="Qwen-Image-Edit-2511-FP8_e4m3fn.safetensors", weight_dtype="default"),
        "2": _node("CLIPLoader", clip_name="qwen_2.5_vl_7b_fp8_scaled.safetensors", type="qwen_image", device="default"),
        "3": _node("VAELoader", vae_name="qwen_image_vae.safetensors"),
    }
    images = {}
    for j, name in enumerate(refs):
        load_id, scale_id = str(20 + 2 * j), str(21 + 2 * j)
        g[load_id] = _node("LoadImage", image=name)
        g[scale_id] = _node("FluxKontextImageScale", image=[load_id, 0])
        images[f"image{j + 1}"] = [scale_id, 0]
    width, height = _refs_size(g, size, megapixels, ["20", 0])
    g.update({
        "12": _node("TextEncodeQwenImageEditPlus", clip=["2", 0], prompt=prompt, vae=["3", 0], **images),
        "13": _node("TextEncodeQwenImageEditPlus", clip=["2", 0], prompt=negative, vae=["3", 0], **images),
        "14": _node("FluxKontextMultiReferenceLatentMethod", conditioning=["12", 0], reference_latents_method="index_timestep_zero"),
        "15": _node("FluxKontextMultiReferenceLatentMethod", conditioning=["13", 0], reference_latents_method="index_timestep_zero"),
        "16": _node("EmptySD3LatentImage", width=width, height=height, batch_size=1),
        "17": _node("ModelSamplingAuraFlow", model=["1", 0], shift=3.1),
        "18": _node("CFGNorm", model=["17", 0], strength=1.0),
        "19": _node("LoraLoaderModelOnly", model=["18", 0], lora_name="Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors",
                    strength_model=1.0),
        "30": _node("KSampler", model=["19", 0], positive=["14", 0], negative=["15", 0], latent_image=["16", 0], seed=int(seed),
                    steps=int(steps), cfg=1.0, sampler_name="euler", scheduler="simple", denoise=1),
        "31": _node("VAEDecode", samples=["30", 0], vae=["3", 0]),
        "32": _node("PreviewImage", images=["31", 0]),
    })
    return g


def qwen21_edit(refs, prompt, negative, seed, steps, size=None, megapixels=1.0):
    g = {k: {**v, "inputs": dict(v["inputs"])} for k, v in _QWEN21.items()}
    images = {}
    for j, name in enumerate(refs):
        g[str(20 + 2 * j)] = _node("LoadImage", image=name)
        images[f"images.image_{j + 1}"] = [str(20 + 2 * j), 0]
    width, height = _refs_size(g, size, megapixels, ["20", 0])
    g.update({
        "9": _node("QwenImage21Cache", model=["1", 0], device="auto", dtype="default"),
        "4": _node("TextEncodeQwenImage21", clip=["2", 0], vae=["3", 0], **images, prompt=prompt, negative_prompt=negative,
                   resolution=1024),
        "11": _node("EmptyLatentImage", width=width, height=height, batch_size=1),
        "6": _node("KSampler", model=["9", 0], positive=["4", 0], negative=["4", 1], latent_image=["11", 0], seed=int(seed),
                   steps=int(steps), cfg=1.0, sampler_name="euler", scheduler="simple", denoise=1),
        "7": _node("VAEDecode", samples=["6", 0], vae=["3", 0]),
        "8": _node("PreviewImage", images=["7", 0]),
    })
    return g


# ---- the engines ---------------------------------------------------------------------------------------------------------------
# weights: engines with the same weights share them in memory (switching between them needs no unload)
IMAGE_ENGINES = {
    "mage": {"label": "Mage-Flow (built in)", "note": "What the prompts were tuned on. About 20 s, light on memory."},
    "zimage": {"label": "Z-Image Turbo", "build": zimage_t2i, "steps": 8, "weights": "zimage",
               "min_free_ram_mb": 32000, "min_free_vram_mb": 21000,
               "note": "The fastest (about 16 s), natural, varied faces. Needs about 21 GB of free VRAM. Pulls hard toward famous "
                       "characters: a long-haired pirate captain came out as Jack Sparrow (and as a man) in 17 of 17 tries, so "
                       "check its pictures."},
    "qwen21": {"label": "Qwen Image 2.1", "build": qwen21_t2i, "steps": 25, "weights": "qwen21",
               "min_free_ram_mb": 32000, "min_free_vram_mb": 21000,
               "note": "Smooth, polished look, about 30 s. Needs about 21 GB of free VRAM. Research licence: non-commercial use "
                       "only (see NOTICE.md)."},
    "qwen2512": {"label": "Qwen-Image 2512 + Lightning", "build": qwen2512_t2i, "steps": 4, "weights": "qwen2512",
                 "min_free_ram_mb": START_FREE_RAM_MB, "min_free_vram_mb": START_FREE_VRAM_MB,
                 "note": "Filmic and natural, about 30 s. The heaviest: needs about 29 GB of free VRAM."},
}
EDIT_ENGINES = {
    "mage": {"label": "Mage-Flow-Edit (built in)", "note": "What the prompts, animation checks and model sheet were tuned on. Fast."},
    "qwen2511": {"label": "Qwen-Image-Edit 2511 + Lightning", "build": qwen2511_edit, "steps": 4, "weights": "qwen2511",
                 "min_free_ram_mb": START_FREE_RAM_MB, "min_free_vram_mb": START_FREE_VRAM_MB, "max_refs": 3,
                 "note": "Real whole bodies and natural poses, about 35 s a picture. Needs about 29 GB of free VRAM. In a walk it "
                         "keeps the character identical in every frame but barely moves the legs (Mage-Flow-Edit walks more but "
                         "drifts): check the clip."},
    "qwen21_edit": {"label": "Qwen Image 2.1 Edit", "build": qwen21_edit, "steps": 25, "weights": "qwen21",
                    "min_free_ram_mb": START_FREE_RAM_MB, "min_free_vram_mb": START_FREE_VRAM_MB, "max_refs": 3,
                    "note": "Keeps faces well but about 100 s a picture: slow for animation. Needs about 29 GB of free VRAM. "
                            "Research licence: non-commercial use only (see NOTICE.md)."},
}


def choices():
    """What the page lists: {"image": [{id, label, note}], "edit": [...]}."""
    def rows(table):
        return [{"id": k, "label": v["label"], "note": v["note"]} for k, v in table.items()]
    return {"image": rows(IMAGE_ENGINES), "edit": rows(EDIT_ENGINES)}


def clean(value, kind):
    """A known engine id of this kind, else 'mage' (like an unknown sprite mode falls back to 'auto')."""
    table = IMAGE_ENGINES if kind == "image" else EDIT_ENGINES
    return value if isinstance(value, str) and value in table else "mage"


def required_files(graph):
    """[(folder, file name)] the graph's loader nodes ask for."""
    out = []
    for node in graph.values():
        spec = _LOADERS.get(node.get("class_type"))
        name = node["inputs"].get(spec[1]) if spec else None
        if spec and isinstance(name, str) and (spec[0], name) not in out:
            out.append((spec[0], name))
    return out


def _sample_graph(spec):
    if "max_refs" in spec:
        return spec["build"](["ref.png"], "", "", 0, spec["steps"], size=(64, 64))
    return spec["build"]("", 64, 64, 0, spec["steps"])


def engine_files(spec):
    return required_files(_sample_graph(spec))


# ---- talking to ComfyUI ----------------------------------------------------------------------------------------------------------
def _endpoint(comfy):
    return getattr(comfy, "base", None) or id(comfy)


def _installed(comfy, folder, fresh=False):
    key = (_endpoint(comfy), folder)
    hit = _FILES.get(key)
    if hit and not fresh and _clock() - hit[0] < FILES_CACHE_SECONDS:
        return hit[1]
    try:
        have = comfy.get_json(f"/models/{folder}", timeout=10)
    except ComfyError:
        raise
    except Exception as e:
        raise EngineError(f"Could not ask ComfyUI which models it has ({type(e).__name__}).") from None
    _FILES[key] = (_clock(), have)
    return have


def missing_files(comfy, spec, fresh=False):
    return [f"{folder}/{name}" for folder, name in engine_files(spec) if name not in _installed(comfy, folder, fresh)]


def availability(comfy):
    """{engine id: [missing 'folder/file', ...]} for every engine that is not built in. Never raises: an engine whose files
    cannot be checked is left out (the page then shows it as usable, and a real run falls back with the reason)."""
    out = {}
    for table in (IMAGE_ENGINES, EDIT_ENGINES):
        for engine_id, spec in table.items():
            if "build" in spec:
                try:
                    out[engine_id] = missing_files(comfy, spec)
                except ComfyError:
                    pass
    return out


def _free(comfy):
    """Ask ComfyUI to unload every model and free memory. A request that fails must never hide the real problem."""
    try:
        comfy.free_memory()
    except Exception:
        pass


def _interrupt(comfy):
    try:
        comfy.interrupt()
    except Exception:
        pass


def _stats(comfy):
    """(VRAM used, VRAM total, free RAM, free VRAM) in MB."""
    d = comfy.get_json("/system_stats", timeout=8)
    dev = d["devices"][0]
    return ((dev["vram_total"] - dev["vram_free"]) // 2 ** 20, dev["vram_total"] // 2 ** 20,
            d["system"]["ram_free"] // 2 ** 20, dev["vram_free"] // 2 ** 20)


def _wait_idle(comfy, seconds=60):
    end = _clock() + seconds
    while _clock() < end:
        try:
            q = comfy.get_json("/queue", timeout=8)
            if not q["queue_running"] and not q["queue_pending"]:
                return True
        except Exception:
            pass
        _sleep(2)
    return False


def _unload(comfy):
    _free(comfy)
    _LOADED[_endpoint(comfy)] = None
    if not _wait_idle(comfy):
        raise EngineError("ComfyUI is busy or not answering: it was asked to free its memory, but did not go idle.", transient=True,
                          code="comfy_busy")


def release(comfy):
    """Unload a big engine if one is loaded (called when the job queue has stayed empty for a while). Never raises."""
    if _LOADED.get(_endpoint(comfy)) not in (None, "mage"):
        _free(comfy)
        _LOADED[_endpoint(comfy)] = None


def loaded(comfy):
    return _LOADED.get(_endpoint(comfy))


def _leave_big_engine(comfy):
    """Before a Mage-Flow picture: a big engine still in memory would leave no room for it."""
    if _LOADED.get(_endpoint(comfy)) not in (None, "mage"):
        _free(comfy)
        _LOADED[_endpoint(comfy)] = None
        _wait_idle(comfy)


def _run_big(comfy, spec, graph, cancel):
    lock = getattr(comfy, "operation_lock", None)
    with lock.hold(cancel) if lock is not None else contextlib.nullcontext():
        return _run_big_locked(comfy, spec, graph, cancel)


def start_needs(spec):
    """(free RAM MB, free VRAM MB) before a big engine starts: measured on the smallest card it ran on (npcgen/hardware.py,
    docs/hardware.md), else the engine's own figures."""
    if os.environ.get("NPCGEN_MEASURE") == "1":         # scripts/measure_gpu.py: what to ask for is what it measures
        return 0, 0
    kind, engine_id = next(((kind, key) for kind, table in (("image", IMAGE_ENGINES), ("edit", EDIT_ENGINES))
                            for key, value in table.items() if value is spec), (None, None))
    measured = hardware.start_needs(f"{kind}:{engine_id}") if kind else None
    return measured or (spec["min_free_ram_mb"], spec["min_free_vram_mb"])


def _run_big_locked(comfy, spec, graph, cancel):
    need_ram, need_vram = start_needs(spec)
    here = _endpoint(comfy)
    prepare = getattr(comfy, "prepare_picture", None)
    if prepare is not None:
        prepare(graph, cancel)
    if _LOADED.get(here) == spec["weights"]:
        try:
            free_ram = _stats(comfy)[2]
        except Exception:
            free_ram = 0
        if free_ram < KEEP_LOADED_FREE_RAM_MB:
            _unload(comfy)
    else:
        _unload(comfy)                                    # other weights (or unknown ones) in memory: start from an empty GPU
    if _LOADED.get(here) != spec["weights"]:
        deadline, free_ram, free_vram = _clock() + 60, 0, 0
        while True:
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            beat(cancel)
            try:
                _, _, free_ram, free_vram = _stats(comfy)
            except Exception:
                free_ram = free_vram = 0
            if (free_ram >= need_ram and free_vram >= need_vram) or _clock() > deadline:
                break
            from . import suite_gpu
            note = ("Waiting for free GPU memory. Ask Voice Lab to free the GPU."
                    if free_vram < need_vram and not gpu.GATE.strict and suite_gpu.can_ask_voice_lab()
                    else "Waiting for free memory")
            beat(cancel, note)
            _sleep(2)
        beat(cancel, None)
        if free_ram < need_ram or free_vram < need_vram:
            raise EngineError(f"not enough free memory ({free_ram // 1024} GB RAM and {free_vram // 1024} GB VRAM free; about "
                              f"{need_ram // 1024} GB RAM and {need_vram // 1024} GB VRAM needed). Close other programs that use "
                              "the GPU (LM Studio, TTS) or pick Mage-Flow.", transient=True, code="low_memory")
    _LOADED[here] = None                                  # unknown until this picture has finished cleanly
    try:
        try:
            prompt_id = comfy.submit(graph)
        except ComfyError as e:
            if str(e).startswith("Cannot reach"):
                raise
            raise _wrapped(str(e), e, transient=isinstance(e, gpu.GpuBusy)) from None
        start, full_since, misses = _clock(), None, 0
        while True:
            _sleep(2)
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            beat(cancel)
            elapsed = _clock() - start
            try:
                vram, vram_total, free, _ = _stats(comfy)
                misses = 0
            except Exception:
                misses += 1
                if misses >= 3:
                    raise EngineError("ComfyUI stopped answering while making the picture.") from None
                continue
            full_since = (full_since if full_since is not None else _clock()) if vram > vram_total * VRAM_FULL else None
            if free < ABORT_FREE_RAM_MB:
                raise EngineError(f"the PC ran short of memory ({free // 1024} GB of RAM free) after {elapsed:.0f} s, so the "
                                  "picture was stopped before it could freeze the PC.", transient=True, code="low_memory")
            if full_since is not None and _clock() - full_since > VRAM_FULL_SECONDS:
                raise EngineError("the GPU memory stayed full and the picture crawled, so it was stopped.", transient=True,
                                  code="low_memory")
            if elapsed > MAX_SECONDS:
                raise EngineError(f"the picture took more than {MAX_SECONDS} s, so it was stopped.")
            entry = comfy.get_json(f"/history/{prompt_id}", timeout=15).get(prompt_id)
            if entry and entry.get("status", {}).get("completed") is not None:
                if entry["status"].get("status_str") != "success":
                    raise EngineError("ComfyUI could not make the picture with this model (a node or file may be missing).")
                for node_out in entry.get("outputs", {}).values():
                    for img in node_out.get("images", []):
                        png = comfy.fetch_image(img["filename"], img.get("subfolder", ""), img.get("type", "temp"))
                        if _blank(png):
                            raise EngineError(BLANK, transient=True, code="low_memory")
                        _LOADED[here] = spec["weights"]
                        return png
                raise EngineError("ComfyUI finished but produced no picture.")
    except BaseException:
        _interrupt(comfy)
        _free(comfy)
        _LOADED[here] = None
        _wait_idle(comfy)
        raise


BLANK = ("the picture came out black, which happens when the GPU runs short of memory, so it was not kept. Free the GPU "
         "(One model at a time does that) and try again.")


def _blank(png):
    """Whether a picture is one flat colour. Measured on this PC (docs/hardware.md): with a 32 GB card made to look like 16 GB,
    Qwen-Image 2512 and Qwen-Image-Edit 2511 finished without an error and returned a black picture."""
    try:
        with Image.open(io.BytesIO(png)) as picture:
            return all(high - low <= 2 for low, high in picture.convert("RGB").getextrema())
    except Exception:                                 # not a picture this can read: leave it to the step that uses it
        return False


# ---- sizes ---------------------------------------------------------------------------------------------------------------------
def _capped(size, limit=MAX_BIG_MEGAPIXELS):
    """The size a big engine draws at: `size` itself, or the same shape at `limit` megapixels, in multiples of 16."""
    w, h = int(size[0]), int(size[1])
    if w * h <= limit * 1e6:
        return w, h
    scale = (limit * 1e6 / (w * h)) ** 0.5
    return max(16, int(w * scale) // 16 * 16), max(16, int(h * scale) // 16 * 16)


def scaled_size(like, megapixels):
    """`like` (w, h) scaled to `megapixels` in multiples of 16: the size edit_graph gives an edit made with size=None."""
    w, h = int(like[0]), int(like[1])
    scale = (float(megapixels) * 1e6 / (w * h)) ** 0.5
    return max(16, round(w * scale / 16) * 16), max(16, round(h * scale / 16) * 16)


def _resized(png, size):
    """The picture at exactly `size`, so everything downstream (cut-outs, sheets, frames) sees the size it asked for."""
    if size is None:
        return png
    im = Image.open(io.BytesIO(png))
    if im.size == tuple(size):
        return png
    out = io.BytesIO()
    im.convert("RGBA" if im.mode in ("RGBA", "LA") else "RGB").resize(tuple(size), Image.LANCZOS).save(out, "PNG")
    return out.getvalue()


# ---- the two entry points every step uses ------------------------------------------------------------------------------------------
def image(comfy, engine_id, prompt, negative, width, height, seed, steps, cfg, cancel=None):
    """Text to image. Returns (png, meta): meta is {} for Mage-Flow, {"engine": label} for another engine, and
    {"engine_note": why} when that engine could not draw it and Mage-Flow did instead."""
    spec = IMAGE_ENGINES.get(engine_id)
    if spec and "build" in spec:
        try:
            if missing_files(comfy, spec):
                raise EngineError("ComfyUI is missing " + ", ".join(missing_files(comfy, spec, fresh=True)), code="no_model")
            w, h = _capped((width, height))
            png = _run_big(comfy, spec, spec["build"](prompt, w, h, seed, spec["steps"]), cancel)
            return _resized(png, (width, height)), {"engine": spec["label"]}
        except EngineError as e:
            refused = _refused(spec, e)
            if refused:
                raise refused from None
            note = f"{spec['label']} could not draw it: {e}"
        png, _ = image(comfy, "mage", prompt, negative, width, height, seed, steps, cfg, cancel)
        return png, {"engine_note": note}
    _leave_big_engine(comfy)
    return comfy.run(t2i_graph(comfy.models, prompt, negative, width, height, seed, steps, cfg), cancel), {}


def edit(comfy, engine_id, refs, prompt, seed, steps, cfg, megapixels=1.0, size=None, negative="", like=None, cancel=None):
    """Instruction edit of one to three references (ComfyUI image names). size=(w, h) forces the output size; size=None keeps
    the first reference's shape at `megapixels` (`like` is that reference's pixel size, which the big engines need to match
    Mage-Flow's output size exactly). Returns (png, meta) like image()."""
    refs = [refs] if isinstance(refs, str) else list(refs)
    spec = EDIT_ENGINES.get(engine_id)
    if spec and "build" in spec:
        try:
            if len(refs) > spec["max_refs"]:
                raise EngineError(f"it takes at most {spec['max_refs']} reference pictures, and this needs {len(refs)}")
            if missing_files(comfy, spec):
                raise EngineError("ComfyUI is missing " + ", ".join(missing_files(comfy, spec, fresh=True)), code="no_model")
            target = tuple(size) if size is not None else (scaled_size(like, megapixels) if like else None)
            graph = spec["build"](refs, prompt, negative, seed, spec["steps"], size=_capped(target) if target else None,
                                  megapixels=megapixels)
            png = _run_big(comfy, spec, graph, cancel)
            return _resized(png, target), {"engine": spec["label"]}
        except EngineError as e:
            refused = _refused(spec, e)
            if refused:
                raise refused from None
            note = f"{spec['label']} could not draw it: {e}"
        png, _ = edit(comfy, "mage", refs, prompt, seed, steps, cfg, megapixels, size, negative, like, cancel)
        return png, {"engine_note": note}
    _leave_big_engine(comfy)
    graph = edit_graph(comfy.models, refs, prompt, seed, steps, cfg, megapixels=megapixels, size=size, negative=negative)
    return comfy.run(graph, cancel), {}
