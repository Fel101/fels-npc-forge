"""Runtime configuration: defaults, overridden by an optional config.json and NPCGEN_* environment variables."""
import copy
import json
import os
import pathlib
import tempfile

from .fels_suite import APPS, PORTS

ROOT = pathlib.Path(__file__).resolve().parent.parent
APP_ID = "npc-forge"
LEGACY_PORT, = APPS[APP_ID]["old_ports"]
DEFAULT_PORT = PORTS[APP_ID]
SECOND_PORT = PORTS["npc-forge-second-copy"]

DEFAULTS = {
    "comfy_url": "http://127.0.0.1:8188",
    "host": "127.0.0.1",
    "port": DEFAULT_PORT,
    # Required when binding beyond loopback. The browser uses HTTP Basic authentication (user: npcforge).
    # Prefer NPCGEN_AUTH_TOKEN over storing it in config.json.
    "auth_token": "",
    "models": {},          # override any of unet_t2i / unet_edit / clip / vae (see comfy.DEFAULT_MODELS)
    "cfg": 5.0,            # text-to-image guidance (Mage-Flow base default)
    "edit_cfg": 3.5,       # lower than 5.0: keeps the reference's art style from drifting when editing
    "sheet_cfg": 3.5,      # guidance for the model-sheet views (see docs/model-sheet.md)
    "library_dir": "library",
    # A deleted character is moved to the library's .trash folder (GET /api/trash, POST /api/trash/<id>/restore) and removed for
    # good after trash_days days (0 keeps everything). A picture that is drawn again does not lose the one it replaces: the last
    # picture_history of them are kept next to it (0 keeps none), listed under it with Restore and Delete. A 3D model that is replaced or deleted is kept
    # the same way: the last model_history of them (0 keeps none; a picture-made model with its skeleton takes 60 to 140 MB).
    "library": {"trash_days": 30, "picture_history": 6, "model_history": 2},
    "voice": {},          # Forge-local audio tools, such as optional alignment. Speech uses Voice Lab.
    # One model on the GPU at a time (npcgen/gpu.py): before a different model loads (Mage-Flow to Mage-Flow-Edit, a picture
    # to a voice...), everything is unloaded and the card must read at most idle_vram_mb in use within wait_seconds.
    # Slower (models reload more often) but the GPU is never shared. false = the older, faster sharing rules.
    "gpu": {"one_model_at_a_time": True, "unload_external_models": False, "idle_vram_mb": 2048,
            "wait_seconds": 90, "unload_idle_seconds": 120},
    # What a request that does not name an option gets, over the built-in defaults (npcgen/service.py default_options). The built-in
    # defaults draw with Qwen-Image 2512 and edit with Qwen-Image-Edit 2511 (both Apache-2.0) and, since 2026-10-13, do not let Mage-Flow
    # draw a picture those cannot ("engine_fallback": false: it fails with the reason). A PC without the Qwen files, or one that wants the
    # lighter built-in engine back, sets them here once, e.g. {"image_model": "mage", "edit_model": "mage", "engine_fallback": true}:
    # Mage-Flow is the model whose licence is unclear (see NOTICE.md), so keep the fallback off for work that must be commercial-use.
    "options": {},
    # The job worker's watchdog (npcgen/jobs.py): a step with no sign of life for stall_minutes is stopped; one that was told to
    # stop and has not within cancel_grace_seconds is given up on and the queue moves on; a GPU hand-off that failed is tried again
    # transient_retries times, retry_pause_seconds apart.
    "jobs": {"stall_minutes": 45, "cancel_grace_seconds": 120, "transient_retries": 2, "retry_pause_seconds": 30},
    # "anim" is the size animation frames are generated at (2:3, like the sprite). Frames end up as small game
    # sprites, so it is deliberately smaller than "sprite": faster, and no visible loss after downscaling.
    # "sheet" is the size of ONE panel of a model sheet (1:2, multiples of 16); only 576x1152 has been tested at length.
    "quality": {
        "draft":    {"label": "Draft (fast)", "steps": 20, "portrait": [768, 768],   "sprite": [576, 864],   "edit_megapixels": 0.6,
                     "anim": [512, 768],   "anim_steps": 20, "sheet": [384, 768],   "sheet_steps": 20},
        "standard": {"label": "Standard",     "steps": 30, "portrait": [1024, 1024], "sprite": [768, 1152],  "edit_megapixels": 1.0,
                     "anim": [576, 864],   "anim_steps": 25, "sheet": [576, 1152],  "sheet_steps": 30},
        "high":     {"label": "High (slow)",  "steps": 30, "portrait": [1536, 1536], "sprite": [1024, 1536], "edit_megapixels": 2.25,
                     "anim": [768, 1152],  "anim_steps": 30, "sheet": [768, 1536],  "sheet_steps": 30},
    },
}


CONFIG_FILE = None          # tests point this at a file of their own; None is config.json beside server.py


def config_file():
    return pathlib.Path(CONFIG_FILE) if CONFIG_FILE else ROOT / "config.json"


def _write_user(path, user):
    """Replace config.json in one step, so a crash never leaves half a file. Raises OSError."""
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as output:
            temporary = pathlib.Path(output.name)
            output.write(json.dumps(user, indent=2, ensure_ascii=False) + "\n")
        temporary.replace(path)
        temporary = None
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def save_gpu_setting(name, value):
    """Save one "gpu" setting in config.json, as the page's switch does. Everything else in the file stays as it is. Raises
    OSError when the file cannot be written and ValueError when it is not a JSON object."""
    path = config_file()
    user = json.loads(path.read_text("utf-8")) if path.exists() else {}
    if not isinstance(user, dict):
        raise ValueError(f"{path.name} is not a JSON object")
    saved = user.get("gpu") if isinstance(user.get("gpu"), dict) else {}
    user["gpu"] = {**saved, name: value}
    _write_user(path, user)


def save_setting(name, value):
    """Save one top-level setting in config.json ("comfy_folder": the ComfyUI folder Set up ComfyUI uses when ComfyUI is not
    running); None removes it. Everything else in the file stays as it is. Raises OSError or ValueError, as save_gpu_setting."""
    path = config_file()
    user = json.loads(path.read_text("utf-8")) if path.exists() else {}
    if not isinstance(user, dict):
        raise ValueError(f"{path.name} is not a JSON object")
    if value is None:
        user.pop(name, None)
    else:
        user[name] = value
    _write_user(path, user)


def _migrate_saved_port(path, user):
    user["port"] = DEFAULT_PORT
    try:
        _write_user(path, user)
    except OSError:
        print(f"Could not update {path.name}. Using port {DEFAULT_PORT} for this start. "
              f"Set the saved port to {DEFAULT_PORT} to keep the change.", flush=True)
    else:
        print(f"Changed the saved NPC Forge port from {LEGACY_PORT} to {DEFAULT_PORT}. "
              f"Port {LEGACY_PORT} is reserved for Voice Lab.", flush=True)


def load(*, migrate_port=False):
    cfg = copy.deepcopy(DEFAULTS)
    path = config_file()
    port_override = os.environ.get("NPCGEN_PORT")
    if path.exists():
        user = json.loads(path.read_text("utf-8"))
        if migrate_port and not port_override and user.get("port") in (LEGACY_PORT, str(LEGACY_PORT)):
            _migrate_saved_port(path, user)
        for key, value in user.items():
            if isinstance(value, dict) and isinstance(cfg.get(key), dict):
                cfg[key].update(value)
            else:
                cfg[key] = value
    if os.environ.get("NPCGEN_COMFY_URL"):
        cfg["comfy_url"] = os.environ["NPCGEN_COMFY_URL"]
    if port_override:
        cfg["port"] = int(port_override)
    if os.environ.get("NPCGEN_LIBRARY"):
        cfg["library_dir"] = os.environ["NPCGEN_LIBRARY"]
    if os.environ.get("NPCGEN_AUTH_TOKEN"):
        cfg["auth_token"] = os.environ["NPCGEN_AUTH_TOKEN"]
    lib = pathlib.Path(cfg["library_dir"])
    cfg["library_path"] = lib if lib.is_absolute() else ROOT / lib
    return cfg
