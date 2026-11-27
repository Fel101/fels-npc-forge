"""NPC generator: a local web app in front of ComfyUI + Mage-Flow. Standard library only (plus Pillow/numpy).

Run:  python server.py [--open]        then browse to http://127.0.0.1:8766
"""
import base64
import contextvars
import copy
import hashlib
import hmac
import importlib.util
import ipaddress
import io
import json
import mimetypes
import os
import pathlib
import re
import signal
import subprocess
import sys
import threading
import traceback
import types
import urllib.parse
import uuid
import webbrowser
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import npcgen
from npcgen import atomic
from npcgen import config as cfgmod
from npcgen import character_card, comfy_setup, fels_suite
from npcgen import suite_gpu
from npcgen import anim, best3d, bodymaker, cast, check3d, cleanup3d, dialogue, engines, equip, export, eyes, facefit, facescan, gpu, hairmask, hands3d, hardware, head3d, headdetail, headgraft, import3d, items, licences, posekeys, prompts, rig3d, roll, service, sheet, three_d
from npcgen import morphology, scene_export, scene_recording, scene_takes, scene_sound, scene_writer
from npcgen import creaturepack
from npcgen import updates as updatesmod
from npcgen import creatures as creature_records
from npcgen import creature_projects
from npcgen.scene_render import ffmpeg_path
from npcgen.scenes import clean_scene, compile_timeline
from npcgen.app import App
from npcgen.comfy import ComfyError
from npcgen.forge_endpoint import local_url
from npcgen import jobs as jobsmod
from npcgen.library import now
from npcgen.voice import describe as voice_describe, engines as voice_engines_mod, record as voice_record
from npcgen.voice import batch as voice_batch, pack as voice_pack_mod, service as voice_service
from npcgen.voice import authoring as voice_authoring
from npcgen.voice import effects as voice_effects
from npcgen.voice import alignment as voice_alignment
from npcgen.voice import character_rig as voice_character_rig
from npcgen.voice import mesh_rig as voice_mesh_rig
from npcgen.voice import comparison as voice_comparison
from npcgen.voice import audition as voice_audition
from npcgen.voice import transcript_draft as voice_transcript_draft
from npcgen.voice import lab_voice
from npcgen.voice import audio as voice_audio
from npcgen.voice.lab_client import LabClient, LabError

_CURRENT_APP = contextvars.ContextVar("npcforge_app", default=None)
WEB = cfgmod.ROOT / "web"
# On Windows mimetypes reads the registry, which may not know these (the style samples, the 3D viewer's decoders).
mimetypes.add_type("image/webp", ".webp")
mimetypes.add_type("application/wasm", ".wasm")
mimetypes.add_type("image/svg+xml", ".svg")

# Bump when web/app.js starts needing something an older server does not provide; the page compares it with its own
# REQUIRED_API and switches the newer features off (with a banner) instead of crashing. A test keeps the two in sync.
API_VERSION = 51             # 51: Connected apps, /api/suite/apps (docs/api.md)

DEFAULT_ASSETS = {"portrait": True, "sprite": True, "model_3d": False, "expressions": ["happy", "sad", "angry", "surprised"]}


class ApiError(Exception):
    def __init__(self, status, message, headers=None):
        super().__init__(message)
        self.status, self.message, self.headers = status, message, headers or {}


class Raw:
    """A non-JSON response (static file, image, zip). `etag` enables 304 replies for repeat requests."""
    def __init__(self, body, ctype, headers=None, status=200, etag=None):
        self.body, self.ctype, self.headers, self.status, self.etag = body, ctype, headers or {}, status, etag


ROUTES = []


def route(method, pattern):
    def deco(fn):
        ROUTES.append((method, re.compile("^" + pattern + "$"), fn))
        return fn
    return deco


# ---- extensions -------------------------------------------------------------------------------------------------------
# A folder extensions/<name>/ (shipped) or local/extensions/<name>/ (git-ignored, so it stays on this machine) whose __init__.py
# defines register(host) can add API routes, pages, a script for the main page, extra settings and a safety check. Nothing is
# loaded at import time (so tests never pick up an extension): main() calls load_extensions() for both folders, the shipped one
# first. A local folder with the name of a shipped one is skipped.
BUILTIN_EXT_DIR = cfgmod.ROOT / "extensions"
EXT_DIR = cfgmod.ROOT / "local" / "extensions"
EXT_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,30}$")
EXT_FILE = re.compile(r"^[A-Za-z0-9_.-]+\.(js|css)$")
EXTENSIONS = []                     # [{"id", "label", "url"}] links in the tab bar
MOUNTS = {}                         # "/ext/<id>/" -> directory served as static files
GATES = {}                          # ext id -> function that says whether the page may be shown right now
UI = []                             # [{"id", "script", "style"?}] loaded into the main page once it has started
GUARDS = []                         # functions(npc) -> None, or the reason this character must not be made


def _ext_id(ext_id):
    if not EXT_ID.match(ext_id or ""):
        raise ValueError(f"Bad extension id: {ext_id!r}")
    return f"/ext/{ext_id}/"


def _shown(ext_id):
    gate = GATES.get(ext_id)
    if gate is None:
        return True
    try:
        return bool(gate())
    except Exception:                                    # a broken switch must never show what it guards
        return False


def add_page(ext_id, label, directory, visible=None):
    """Serve `directory` at /ext/<ext_id>/ and add a link to it in the tab bar.

    `visible`, if given, is called on every request: while it returns false the link is not listed and the page answers 404.
    """
    prefix = _ext_id(ext_id)
    MOUNTS[prefix] = pathlib.Path(directory).resolve()
    EXTENSIONS[:] = [e for e in EXTENSIONS if e["id"] != ext_id] + [{"id": ext_id, "label": label, "url": prefix}]
    if visible is None:
        GATES.pop(ext_id, None)
    else:
        GATES[ext_id] = visible


def add_ui(ext_id, directory, script="ui.js", style=None):
    """Serve `directory` at /ext/<ext_id>/ and load its script (and stylesheet) into the main page once it has started.

    The script finds window.NPCForge (see web/app.js). Use an id that no add_page call uses: both mount a folder at /ext/<id>/.
    """
    prefix = _ext_id(ext_id)
    root = pathlib.Path(directory).resolve()
    for name in (script, style):
        if name and not (EXT_FILE.match(name) and (root / name).is_file()):
            raise ValueError(f"{name!r} is not a .js or .css file in {root}")
    MOUNTS[prefix] = root
    UI[:] = [u for u in UI if u["id"] != ext_id] + [{"id": ext_id, "script": prefix + script, **({"style": prefix + style} if style else {})}]


def add_guard(fn):
    """Register fn(npc) -> None or a reason. It runs before any character is saved, rolled or drawn; a reason refuses it."""
    GUARDS.append(fn)


def reset_extensions():
    """Forget everything an extension registered (for tests). Routes are not removed."""
    for registry in (EXTENSIONS, UI, GUARDS, roll.EXTRA_SETTINGS, jobsmod.PORTRAIT_ENGINES, bodymaker.EXTRA_CLOTHES):
        registry.clear()
    MOUNTS.clear()
    GATES.clear()
    for steps in bodymaker.BUILD_STEPS.values():
        steps.clear()


def check_npc(npc, *, require_adult=False):
    """Refuse a character that belongs to a switched-off section or that an extension's guard rules out. Returns it."""
    if npc.get("setting") in roll.hidden_setting_ids():
        raise ApiError(403, "That character belongs to a section that is switched off.")
    if require_adult or npc.get("adult") is True or npc.get("setting") in roll.extension_adult_settings():
        problem = roll.adult_problem(npc)
        if problem:
            raise ApiError(400, problem)
    for guard in GUARDS:
        try:
            problem = guard(copy.deepcopy(npc))
        except Exception:                                # fail closed: a check that cannot run must not wave anything through
            traceback.print_exc()
            problem = "A safety check could not run, so nothing was made."
        if problem:
            raise ApiError(400, str(problem))
    return npc


def _extension_route(method, pattern):
    if not pattern.startswith("/api/ext/"):
        raise ValueError("Extension routes must be under /api/ext/. Core routes cannot be replaced.")
    return route(method, f"(?=/api/ext/)(?:{pattern})")


def _extension_portrait(fn, *, setting=None):
    if setting not in roll.extension_adult_settings():
        raise ValueError("An extension portrait engine needs a registered extension setting with the core age check.")

    def checked(**context):
        npc = context["npc"]
        if context["setting"] != setting:
            return None
        try:
            check_npc(npc, require_adult=True)
            result = fn(**{**context, "npc": copy.deepcopy(npc), "prompt": copy.deepcopy(context["prompt"])})
            check_npc(npc, require_adult=True)
        except ApiError as error:
            raise ValueError(error.message) from None
        return result

    jobsmod.add_portrait_engine(checked)


def current_app():
    app = _CURRENT_APP.get()
    if app is None:
        raise RuntimeError("No NPC Forge application is active")
    return app


def make_host(root=None, app=None):
    """What register(host) receives. `root` is where an extension keeps its own files (tests pass a temp folder)."""
    app = app or current_app()
    return types.SimpleNamespace(route=_extension_route, ApiError=ApiError, Raw=Raw, add_page=add_page, add_ui=add_ui, add_guard=add_guard,
                                 add_settings=lambda directory, visible=None: roll.add_settings_dir(directory, visible, adult_only=True),
                                 add_portrait_engine=_extension_portrait, add_clothes_choice=bodymaker.add_clothes_choice, check_npc=check_npc,
                                 add_body_build_step=bodymaker.add_build_step,
                                 PortraitFallback=jobsmod.PortraitFallback, app=app, cfg=app.cfg, comfy=app.comfy, lib=app.lib,
                                 jobs=app.jobs,
                                 root=pathlib.Path(root) if root else cfgmod.ROOT)


def load_extensions(directory=None, app=None, skip=()):
    """Load every <directory>/<name>/__init__.py that defines register(host), except the names in `skip`. Returns the names
    that loaded.

    A broken extension is reported on the console and skipped: it must never stop NPC Forge from starting.
    """
    directory = pathlib.Path(directory) if directory else EXT_DIR
    loaded = []
    if not directory.is_dir():
        return loaded
    host = make_host(app=app)
    for folder in sorted(p for p in directory.iterdir() if (p / "__init__.py").is_file() and not p.name.startswith("_")):
        if folder.name in skip:
            print(f"Extension {folder.name!r} in {directory} was skipped: NPC Forge ships one of that name.", file=sys.stderr)
            continue
        name = f"npcgen_ext_{re.sub(r'[^a-z0-9_]', '_', folder.name.lower())}"
        # Registration is a transaction across every host registry. A broken extension must not leave a route, page, guard,
        # setting, portrait hook or choice of clothes behind while being reported as skipped.
        before = (list(ROUTES), list(EXTENSIONS), dict(MOUNTS), dict(GATES), list(UI), list(GUARDS),
                  list(roll.EXTRA_SETTINGS), list(jobsmod.PORTRAIT_ENGINES), dict(bodymaker.EXTRA_CLOTHES),
                  {stage: dict(steps) for stage, steps in bodymaker.BUILD_STEPS.items()})
        try:
            spec = importlib.util.spec_from_file_location(name, folder / "__init__.py", submodule_search_locations=[str(folder)])
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            spec.loader.exec_module(module)
            module.register(host)
            loaded.append(folder.name)
        except Exception:
            routes, extensions, mounts, gates, ui, guards, settings, portraits, choices, build_steps = before
            ROUTES[:] = routes
            EXTENSIONS[:] = extensions
            MOUNTS.clear()
            MOUNTS.update(mounts)
            GATES.clear()
            GATES.update(gates)
            UI[:] = ui
            GUARDS[:] = guards
            roll.EXTRA_SETTINGS[:] = settings
            jobsmod.PORTRAIT_ENGINES[:] = portraits
            bodymaker.EXTRA_CLOTHES.clear()
            bodymaker.EXTRA_CLOTHES.update(choices)
            for stage, steps in bodymaker.BUILD_STEPS.items():
                steps.clear()
                steps.update(build_steps[stage])
            sys.modules.pop(name, None)
            traceback.print_exc()
            print(f"Extension {folder.name!r} failed to load and was skipped.", file=sys.stderr)
    return loaded


# ---- helpers ---------------------------------------------------------------------------------------------------
def load_npc(npc_id):
    try:
        npc = current_app().lib.get(npc_id)
    except KeyError:
        raise ApiError(404, f"No NPC with id {npc_id}") from None
    if morphology.clean(npc.get("morphology")) is None:
        npc["morphology"] = morphology.infer(npc, roll.load_data())
    if notice := bodymaker.extension_notice(npc):
        npc["body_extension_notice"] = notice
    return voice_authoring.annotate(npc)


def clean_assets(raw, data):
    try:
        return service.clean_assets(raw, data)
    except ValueError as e:
        raise ApiError(400, str(e)) from None


def clean_options(raw, cfg):
    try:
        return service.clean_options(raw, cfg)
    except ValueError as e:
        raise ApiError(400, str(e)) from None


def validated(data, npc):
    try:
        clean = roll.normalize(data, npc)
    except ValueError as e:
        raise ApiError(400, str(e)) from None
    return check_npc(clean)


def submit_all(npcs, assets, options):
    """Queue a job for each character, but only if every one of them passes the checks: otherwise nothing is queued.

    A "voice" request is built per character (npcgen/voice/batch.py: the voice its sheet suggests, takes, its dialogue), and a
    character with nothing left to make (a voice already picked, no pictures asked for) gets no job."""
    for npc in npcs:
        check_npc(npc)
        reason = morphology.unsupported_reason(npc.get("morphology"), assets) or bodymaker.refuses(npc, assets)
        if reason:
            raise ApiError(400, reason)
    if not assets.get("voice"):
        return [current_app().jobs.submit(n, assets, options) for n in npcs]
    lib, out = current_app().lib, []
    for n in npcs:
        mine = {k: v for k, v in assets.items() if k != "voice"}
        try:
            voice = voice_batch.job_assets(lib, lib.get(n["id"]), assets["voice"],current_app().cfg)
        except ValueError as e:
            raise ApiError(400, f'{n["profile"].get("first_name", "NPC")}: {e}') from None
        if voice:
            mine["voice"] = voice
        if any(mine.get(k) for k in ("portrait", "sprite", "model_3d", "import_3d", "body_3d", "rig_3d", "head_3d", "refine_3d", "sharp_face", "smooth_eyes", "match_hair", "clean_hands", "attach_head", "check_3d", "best_3d", "expressions",
                                     "animations", "sheet", "living", "voice")):
            out.append(current_app().jobs.submit(lib.get(n["id"]), mine, options))
    return out


def visible_library():
    """The library without the characters of a section that is switched off."""
    hidden = roll.hidden_setting_ids()
    library = current_app().lib.list()
    return [n for n in library if n.get("setting") not in hidden] if hidden else library


# ---- API -----------------------------------------------------------------------------------------------------------
@route("GET", "/api/health")
def health(q, body):
    identity = fels_suite.health_block(cfgmod.APP_ID, npcgen.__version__, API_VERSION)
    if q.get("identity") == ["1"]:
        return {"fels": identity}
    app = current_app()
    comfy = app.comfy
    status = comfy.health()
    if status.get("online"):
        hardware.remember(status.get("vram_total_mb"), status.get("gpu"))
    status["your_pc"] = hardware.your_pc()                          # the tier of ComfyUI's card, for the Models card's Your PC box
    if status.get("online"):
        status["engines_missing"] = engines.availability(comfy)       # {engine id: [missing files]}, for the Models card
        status["three_d_missing"] = three_d.availability(comfy)       # {generator id: [missing files/nodes]}
    status["voice"] = {"provider": "voice-lab"}
    status["gpu_rule"] = gpu.status()           # "gpu" is already the card's name
    status["rig_3d"] = rig3d.availability(app.cfg)                   # Blender, for skeletons and animation clips
    status["rig_library"] = rig3d.library_availability(app.cfg)      # the animation library's clips (Quaternius, CC0)
    status["rig_pose"] = (posekeys.availability(comfy) if status.get("online")   # SDPose in ComfyUI, to find the joints
                          else {"model": None, "licence": None, "missing": ["ComfyUI"]})
    status["sharp_face"] = (headdetail.availability(comfy, app.cfg.get("face_upscaler")) if status.get("online")
                            else {"upscaler": None, "licence": None, "missing": ["ComfyUI"]})
    status["smooth_eyes"] = eyes.availability(app.cfg)             # LM Studio with a vision model, to find the eyes
    status["match_hair"] = (hairmask.availability(comfy) if status.get("online")   # SAM 3.1 in ComfyUI, to find the hair
                            else {"model": None, "licence": None, "missing": ["ComfyUI"]})
    status["clean_hands"] = hands3d.availability(app.cfg, comfy if status.get("online") else None)   # Blender and SDPose
    status["attach_head"] = headgraft.availability(app.cfg, comfy if status.get("online") else None)   # Blender and SDPose
    status["check_3d"] = check3d.availability(app.cfg)             # ...and one to compare a model before and after its clean-up
    status["head_3d"] = (head3d.availability(comfy) if status.get("online")   # Pixal3D and BiRefNet, for the detailed head
                         else {"missing": ["ComfyUI"]})
    status["best_3d"] = best3d.availability(status)                  # all of the above, for the best-quality button
    status["items"] = items.availability(status)                     # Trellis2 and Blender, for the Items tab
    status["import_3d"] = (three_d.import_availability(comfy) if status.get("online")   # ComfyUI's mesh nodes, to import a model
                           else {"missing": ["ComfyUI"]})
    status["body_3d"] = bodymaker.availability(app.cfg)              # the Body Designer, Blender and the animation library, for a designed body
    status["fels"] = identity
    return status


@route("GET", "/api/config")
def config(q, body):
    app = current_app()
    cfg = app.cfg
    if hardware.your_pc()["total_mb"] is None:         # the first page load: read the card once, for the tier's engines
        reading = app.comfy.health()
        if reading.get("online"):
            hardware.remember(reading.get("vram_total_mb"), reading.get("gpu"))
    data = roll.load_data()
    settings = {}
    for sid, s in data["settings"].items():
        if "races" in s:
            origins = [{"id": k, "label": v["label"],
                        "morphology": morphology.clean(v.get("morphology", "bipedal")) or "unspecified"}
                       for k, v in s["races"].items()]
        else:
            origins = [{"id": k, "label": v["label"]} for k, v in roll.setting_common(data, sid)["heritages"].items()]
        common = roll.setting_common(data, sid)
        settings[sid] = {
            "label": s["label"], "short": s.get("short", s["label"].split(" ")[0]), "blurb": s.get("blurb", ""),
            "origin_label": s.get("origin_label", "Heritage"), "has_races": "races" in s,
            "hair_colors": roll.hair_color_choices(data, sid),
            "occupations": [{"id": o["id"], "label": o["label"]} for o in s["occupations"]], "origins": origins,
            # what this setting allows: its own genders and age groups, and the youngest age it accepts
            "genders": [{"id": k, "label": v["label"]} for k, v in common["genders"].items() if not s.get("genders") or k in s["genders"]],
            "age_groups": [{"id": k, "label": v["label"]} for k, v in common["age_groups"].items() if v["max"] >= int(s.get("min_age") or 1)],
            "min_age": int(s.get("min_age") or 1),
            **({"default_style": s["default_style"]} if s.get("default_style") in data["styles"] else {}),
        }
    return {
        "api_version": API_VERSION,
        "version": npcgen.__version__,
        "settings": settings,
        "morphologies": morphology.MORPHOLOGIES,
        "styles": {k: {"label": v["label"], "blurb": v.get("blurb", "")} for k, v in data["styles"].items()},
        "genders": [{"id": k, "label": v["label"]} for k, v in data["common"]["genders"].items()],
        "age_groups": [{"id": k, "label": v["label"]} for k, v in data["common"]["age_groups"].items()],
        "expressions": [{"id": e["id"], "label": e["label"], "default": e.get("default", False), "living": e.get("living", False)}
                        for e in data["expressions"]],
        "quality": {k: {"label": v["label"], "steps": v["steps"], "portrait": v["portrait"], "sprite": v["sprite"]}
                    for k, v in cfg["quality"].items()},
        "views": list(prompts.VIEWS),
        "engines": {**engines.choices(), "three_d": three_d.choices()},
        "three_d_qualities": three_d.quality_choices(),
        "three_d_face_sources": three_d.face_source_choices(),
        "three_d_hunyuan_sources": three_d.hunyuan_source_choices(),
        "three_d_hunyuan_details": three_d.hunyuan_detail_choices(),
        "three_d_import_details": three_d.import_detail_choices(),
        "game_sizes": cleanup3d.game_size_choices(),
        # what each model is licensed for and whether it may be used commercially (data/licences.json), with the selectors that reach
        # each ("image:zimage", "voice:qwen"...): the page puts a badge on every model it offers and lists them all
        "licences": licences.catalogue(),
        # how long a deleted character waits in the trash, and how many earlier versions of a redrawn picture are kept
        "library": {"trash_days": (cfg.get("library") or {}).get("trash_days", 30), "picture_history": app.lib.history_keep, "model_history": app.lib.model_history_keep},
        "head_qualities": head3d.quality_choices(),
        "head_expressions": head3d.expression_choices(data["head3d"]),
        "rig_skeletons": rig3d.skeleton_choices(),
        "rig_clip_sets": rig3d.clip_choices(),
        "best_3d": {"steps": list(best3d.STEPS), "estimate": best3d.ESTIMATE},
        "items": items.catalogue(),
        "animations": {
            "clips": [{"id": k, "label": v["label"], "kind": v["kind"], "fps": anim.clip_fps(v, anim.frame_counts(v)[0]),
                       "experimental": v.get("experimental", False), "quality_note": v.get("quality_note", ""),
                       "frames": anim.frame_counts(v)}
                      for k, v in data["animations"]["clips"].items()],
            "directions": [{"id": k, "label": data["animations"]["directions"][k]["label"]} for k in service.DIRECTION_ORDER],
            "presets": [{"id": k, "label": v["label"], "frame": v["frame"], "frames": v["frames"]} for k, v in export.PRESETS.items()],
        },
        "sheet": {
            "views": [{"id": v, "label": data["sheet"]["views"][v]["label"]} for v in sheet.VIEWS],
            "poses": [{"id": k, "label": p["label"]} for k, p in data["sheet"]["poses"].items()],
            "default_pose": data["sheet"]["default_pose"],
        },
        "defaults": {"setting": next(iter(data["settings"])), "style": next(iter(data["styles"])),
                     "assets": DEFAULT_ASSETS, "options": service.default_options(cfg)},
        "comfy_url": cfg["comfy_url"],
        "extensions": [e for e in EXTENSIONS if _shown(e["id"])],
        "ui": UI,
    }


@route("POST", "/api/roll")
def api_roll(q, body):
    data = roll.load_data()
    setting = body.get("setting")
    if not isinstance(setting, str) or setting not in data["settings"]:
        raise ApiError(400, "Unknown setting")
    style_value = body.get("style")
    style = style_value if isinstance(style_value, str) and style_value in data["styles"] else next(iter(data["styles"]))
    freeform = body.get("freeform")
    if freeform is not None and not str(freeform).strip():
        raise ApiError(400, "Describe the character first")
    try:
        npc = roll.roll_npc(data, setting, body.get("constraints"), body.get("locked"),
                            freeform=str(freeform).strip()[:1500] if freeform is not None else None, adult=body.get("adult") is True)
    except (ValueError, TypeError) as e:
        raise ApiError(400, f"Could not roll with those settings: {e}") from None
    npc["style"] = style
    npc["custom"] = {}
    return check_npc(npc)


@route("GET", "/api/choices")
def api_choices(q, body):
    """What each character-sheet field can be in a setting, for the Advanced options popup (roll.field_choices)."""
    data = roll.load_data()
    setting = q.get("setting", [""])[0]
    if setting not in data["settings"]:
        raise ApiError(404, "Unknown setting")
    return roll.field_choices(data, setting)


@route("POST", "/api/manner")
def api_manner(q, body):
    """Suggest how a character talks and comes across, from their traits: {setting, traits: [...]} -> {speech, demeanor}."""
    data = roll.load_data()
    setting = body.get("setting")
    if not isinstance(setting, str) or setting not in data["settings"]:
        raise ApiError(400, "Unknown setting")
    raw_traits = body.get("traits")
    if not isinstance(raw_traits, (type(None), str, list, tuple)):
        raise ApiError(400, "Traits must be a list or comma-separated text")
    traits = [t[:60] for t in roll.as_list(raw_traits)][:8]
    return roll.roll_manner(data, setting, traits)


def _import_cast_records(body, make_pack=False):
    """Import cast entries. The optional pack path adds a manifest without changing NPC records."""
    data = roll.load_data()
    setting_value, style_value = body.get("setting"), body.get("style")
    setting = setting_value if isinstance(setting_value, str) and setting_value in data["settings"] else next(iter(data["settings"]))
    style = style_value if isinstance(style_value, str) and style_value in data["styles"] else next(iter(data["styles"]))
    text, fmt = body.get("text"), body.get("format")
    envelope = {}
    if make_pack and isinstance(text, str):
        try:
            candidate = json.loads(text.lstrip("\ufeff"))
            if isinstance(candidate, dict):
                envelope = candidate
        except ValueError:
            pass
    try:
        entries = cast.parse_cast(text, fmt)
        drafts, warnings = cast.build_drafts(data, entries, setting, style, body.get("constraints"))
    except (ValueError, TypeError) as e:
        raise ApiError(400, str(e)) from None
    clean_drafts = [validated(data, d) for d in drafts]
    assets = options = None
    if body.get("generate"):
        assets = clean_assets(body.get("assets"), data)
        options = clean_options(body.get("options"), current_app().cfg)
        for draft in clean_drafts:
            reason = morphology.unsupported_reason(draft.get("morphology"), assets)
            if reason:
                raise ApiError(400, reason)
    npcs = []
    pack = None
    try:
        for draft in clean_drafts:
            npcs.append(current_app().lib.create(draft))
        if make_pack:
            supplied = body.get("pack") if isinstance(body.get("pack"), dict) else {}
            embedded = envelope.get("pack") if isinstance(envelope.get("pack"), dict) else {}
            pack_data = {**supplied, **embedded}
            pack_data.setdefault("name", "Imported cast")
            raw_edges = body.get("relationships", envelope.get("relationships", pack_data.get("relationships", [])))
            if not isinstance(raw_edges, list):
                raise ValueError("Cast pack relationships must be a list")

            names = {}
            for index, npc in enumerate(npcs, 1):
                profile = npc["profile"]
                full_name = f'{profile.get("first_name", "")} {profile.get("last_name", "")}'.strip().casefold()
                names.setdefault(full_name, []).append(index - 1)

            def resolve_member(reference, label):
                if isinstance(reference, int) and not isinstance(reference, bool):
                    index = reference - 1  # Relationship references in imported manifests are one-based.
                elif isinstance(reference, str):
                    key = reference.strip().casefold()
                    if key in names:
                        matches = names[key]
                        if len(matches) != 1:
                            raise ValueError(f"{label} name '{reference}' is ambiguous; use its number in the cast, counting from 1")
                        index = matches[0]
                    elif key.isdigit():
                        index = int(key) - 1
                    else:
                        raise ValueError(f"{label} '{reference}' must match an imported character's name or its number in the cast, counting from 1")
                else:
                    raise ValueError(f"{label} must be a character's name or its number in the cast, counting from 1")
                if index < 0 or index >= len(npcs):
                    raise ValueError(f"{label} refers to member {index + 1}, outside this cast")
                return npcs[index]["id"]

            relationships = []
            for pos, edge in enumerate(raw_edges, 1):
                if not isinstance(edge, dict):
                    raise ValueError(f"Relationship {pos} must be an object")
                source = edge.get("from", edge.get("source_id", edge.get("from_index")))
                target = edge.get("to", edge.get("target_id", edge.get("to_index")))
                relationships.append({"from": resolve_member(source, f"Relationship {pos} source"),
                                      "to": resolve_member(target, f"Relationship {pos} target"),
                                      "type": edge.get("type", ""), "notes": edge.get("notes", "")})

            members = []
            for index, (entry, npc) in enumerate(zip(entries, npcs), 1):
                _, control, _, _ = cast.entry_fields(entry, index)
                role = control.get("cast_role", control.get("member_role", control.get("pack_role", "")))
                members.append({"npc_id": npc["id"], "role": str(role).strip(), "notes": ""})
            pack_data.update(members=members, relationships=relationships)
            pack = current_app().cast_packs.create(pack_data, {n["id"] for n in current_app().lib.list()})
    except Exception as e:
        for npc in npcs:
            try:
                current_app().lib.delete(npc["id"], trash=False)      # (made a moment ago by this request: nothing to bring back)
            except Exception:
                pass
        # User-correctable pack shape/reference errors should be ordinary API validation responses.
        if isinstance(e, ValueError):
            raise ApiError(400, str(e)) from None
        raise
    result = {"count": len(npcs), "npc_ids": [n["id"] for n in npcs], "warnings": warnings}
    if pack:
        result["pack"] = pack
    if body.get("generate"):
        result["jobs"] = submit_all(npcs, assets, options)
    return result


@route("POST", "/api/cast")
def import_cast(q, body):
    """Create NPCs from a JSON/CSV cast file; optionally queue image generation for all of them."""
    return _import_cast_records(body)


@route("POST", "/api/cast-packs/import")
def import_cast_pack(q, body):
    """Import a JSON/CSV cast and create a pack. JSON envelopes may also include pack metadata and relationships."""
    return _import_cast_records(body, make_pack=True)


@route("POST", "/api/preview")
def api_preview(q, body):
    data = roll.load_data()
    npc = validated(data, body.get("npc") or {})
    if isinstance(body.get("npc_id"), str):
        try:
            npc["assets"] = {"source_photo": current_app().lib.get(body["npc_id"])["assets"].get("source_photo")}
        except KeyError:
            pass
    style = data["styles"][npc["style"]]
    return prompts.preview(npc, style, data["settings"][npc["setting"]], data["expressions"],
                           clean_options(body.get("options"), current_app().cfg))


@route("GET", "/api/npcs")
def list_npcs(q, body):
    """The library. `?project=name` keeps one project's characters (the `custom.project` it tagged them with), `?test=0` leaves
    out scratch characters (`custom.test`), `?test=1` keeps only those."""
    cards = visible_library()
    project = q.get("project", [""])[0]
    if project:
        cards = [n for n in cards if n.get("project") == project]
    test = q.get("test", [""])[0]
    if test in ("0", "1"):
        cards = [n for n in cards if n.get("test") == (test == "1")]
    return cards


def _card_npc(npc_id):
    npc = check_npc(load_npc(npc_id))
    if not character_card.allowed(npc):
        raise ApiError(400, "Cards export characters from Forge's core settings. Open a character from a core setting and try again.")
    if not (npc.get("assets", {}).get("portrait") or {}).get("file"):
        raise ApiError(400, "Make a portrait for this character, then export its card.")
    return npc


@route("GET", "/api/npcs/([a-z0-9-]+)/card-options")
def card_options(q, body, npc_id):
    """Options for the card dialog: inbox availability and the saved Cast voice's sharing requirements."""
    npc = _card_npc(npc_id)
    selection = character_card.voice_selection(npc) or {}
    cast_voice = None
    if selection.get("identity_id"):
        consent = character_card.consent_for(selection) if character_card.needs_permission(selection) else None
        cast_voice = {"name": selection["name"], "consent_for": consent}
    return {"altera_inbox": character_card.inbox_ready(), "cast_voice": cast_voice}


def _free_gpu_for_voice_lab():
    """A CHARX card's Cast voice carries a line Voice Lab speaks for it. With One model at a time, NPC Forge keeps ComfyUI's
    models, and the GPU to itself, for gpu.unload_idle_seconds after a picture, and Voice Lab will not load a model meanwhile.
    So when no job is running NPC Forge lets go first, as it does before it sends speech to Voice Lab (docs/models.md)."""
    app = current_app()
    with app.jobs.lock:
        if app.jobs.current is not None or not app.jobs.queue.empty():
            return
    engines.release(app.comfy)
    gpu.release_comfy(app.comfy)


def _card_bytes(npc, form, *, include_voice=True, permission=False):
    if form == "charx" and include_voice and (character_card.voice_selection(npc) or {}).get("identity_id"):
        _free_gpu_for_voice_lab()
    try:
        return character_card.build(current_app().lib, npc, form, include_voice=include_voice, permission=permission)
    except LabError as error:
        raise ApiError(502, str(error)) from None
    except ValueError as error:
        raise ApiError(400, str(error)) from None


def _card_download(data, npc_id, form):
    mime = "image/png" if form == "png" else "application/zip"
    return Raw(data, mime, {"Cache-Control": "no-store", "Content-Disposition": f'attachment; filename="{npc_id}.{form}"'})


@route("GET", r"/api/npcs/([a-z0-9-]+)/card\.(charx|png)")
def download_card(q, body, npc_id, form):
    """Download a CCv3 CHARX or PNG. Set voice=0 to omit the voice. GET never grants permission to include a person's recording."""
    npc = _card_npc(npc_id)
    return _card_download(_card_bytes(npc, form, include_voice=q.get("voice") != ["0"]), npc_id, form)


@route("POST", r"/api/npcs/([a-z0-9-]+)/card\.charx")
def export_card(q, body, npc_id):
    """Download CHARX or send it to Altera (send: true). include_voice defaults to true. permission: true requires consent_for from card-options."""
    npc = _card_npc(npc_id)
    for field in ("send", "include_voice", "permission"):
        if field in body and type(body[field]) is not bool:
            raise ApiError(400, f"{field} must be true or false.")
    send = body.get("send", False)
    if send and not character_card.inbox_ready():
        raise ApiError(409, "Altera's inbox is unavailable. Start Altera or download the CHARX card.")
    permission = body.get("permission", False)
    if permission:
        selection = character_card.voice_selection(npc)
        if not selection or body.get("consent_for") != character_card.consent_for(selection):
            raise ApiError(409, "The saved voice changed. Reopen Export character card and review the voice before sharing.")
    data = _card_bytes(npc, "charx", include_voice=body.get("include_voice", True), permission=permission)
    if not send:
        return _card_download(data, npc_id, "charx")
    try:
        if not character_card.inbox_ready():
            raise FileNotFoundError
        fels_suite.inbox_write("altera", cfgmod.APP_ID, f"{npc_id}.charx", data)
    except OSError:
        raise ApiError(409, "Altera's inbox could not accept the card. Start Altera or download the CHARX card.") from None
    return {"sent": True}


@route("GET", "/api/npcs/([a-z0-9-]+)/licences")
def npc_licences(q, body, npc_id):
    """Which models made this character's pictures, 3D model and voice, what each is licensed for, and the worst of them as the verdict."""
    return licences.check(load_npc(npc_id))


@route("GET", "/api/trash")
def list_trash(q, body):
    """The characters deleted from the library, newest first. They can be brought back for 30 days."""
    return current_app().lib.trashed()


@route("POST", "/api/trash/([a-z0-9-]+)/restore")
def restore_npc(q, body, npc_id):
    try:
        return current_app().lib.restore(npc_id)
    except KeyError:
        raise ApiError(404, "That character is not in the trash") from None
    except ValueError as e:
        raise ApiError(409, str(e)) from None


TRASH_FOLDER = "([a-z0-9][a-z0-9-]+--[0-9]{8}-[0-9]{6}(?:-[0-9]+)?)"      # what GET /api/trash names a trashed character's "folder"


@route("GET", f"/api/trash/{TRASH_FOLDER}/thumb\\.png")
def trash_thumb(q, body, folder):
    try:
        path = current_app().lib.trash_file(folder)
    except KeyError:
        raise ApiError(404, "Not found") from None
    stat = path.stat()
    return Raw(atomic.read_bytes(path), "image/png", {"Cache-Control": "no-cache"}, etag=f'"{stat.st_mtime_ns:x}-{stat.st_size:x}"')


@route("DELETE", f"/api/trash/{TRASH_FOLDER}")
def remove_trashed_npc(q, body, folder):
    """Remove one trashed character for good (it cannot be brought back after this)."""
    try:
        current_app().lib.purge_trashed(folder)
    except KeyError:
        raise ApiError(404, "That character is not in the trash") from None
    return {"removed": folder}


@route("POST", "/api/trash/empty")
def empty_trash(q, body):
    """Remove everything in the trash for good."""
    return {"removed": current_app().lib.empty_trash()}


def _cast_pack_npc_ids():
    return {npc["id"] for npc in current_app().lib.list()}


@route("GET", "/api/cast-packs")
def list_cast_packs(q, body):
    return current_app().cast_packs.list()


# ---- scene maker ---------------------------------------------------------------------------------------------------
@route("GET", "/api/scenes/capabilities")
def scene_capabilities(q, body):
    return {"mp4": bool(ffmpeg_path()), "voice_studio_schema": 1, "director_schema": 1, "studio_schema": 1,
            "stock_voices":voice_engines_mod.stock_voices('kokoro')}


@route("GET", "/api/scenes")
def list_scenes(q, body):
    return current_app().scenes.list()


@route("POST", "/api/scenes")
def create_scene(q, body):
    try:
        return current_app().scenes.create(body)
    except ValueError as e:
        raise ApiError(400, str(e)) from None


@route("POST", "/api/scenes/preview")
def preview_scene(q, body):
    app = current_app()
    previous = app.scenes.get(body["id"]) if isinstance(body, dict) and body.get("id") else None
    try:
        draft = clean_scene(body, app.lib, previous)
        return compile_timeline(draft, app.lib, app.scenes)
    except ValueError as e:
        raise ApiError(400, str(e)) from None


@route("POST", "/api/scenes/suggest")
def suggest_scene(q, body):
    app = current_app()
    raw_scene = body.get('scene') if isinstance(body, dict) else None
    previous = app.scenes.get(raw_scene['id']) if isinstance(raw_scene, dict) and raw_scene.get('id') else None
    try:
        return scene_writer.suggest(body, app.lib, previous)
    except ValueError as e:
        raise ApiError(400, str(e)) from None


@route("GET", "/api/scenes/([a-z0-9-]+)")
def get_scene(q, body, scene_id):
    return current_app().scenes.get(scene_id)


@route("POST", "/api/scenes/([a-z0-9-]+)/duplicate")
def duplicate_scene(q, body, scene_id):
    try:
        return current_app().scenes.duplicate(scene_id)
    except ValueError as e:
        raise ApiError(400, str(e)) from None


@route("POST", "/api/scenes/([a-z0-9-]+)/media")
def upload_scene_sound(q, body, scene_id):
    try:
        return scene_sound.upload(current_app().scenes, scene_id, body)
    except ValueError as e:
        raise ApiError(409 if 'changed since' in str(e) else 400, str(e)) from None


@route("POST", "/api/scenes/([a-z0-9-]+)/takes/([a-z0-9_-]+)/select")
def select_scene_take(q, body, scene_id, beat_id):
    try:
        return scene_takes.select(current_app().scenes, scene_id, beat_id, body)
    except ValueError as e:
        raise ApiError(409 if 'changed since' in str(e) else 400, str(e)) from None


@route("PUT", "/api/scenes/([a-z0-9-]+)")
def update_scene(q, body, scene_id):
    try:
        return current_app().scenes.update(scene_id, body)
    except ValueError as e:
        raise ApiError(409 if "changed since" in str(e) else 400, str(e)) from None


@route("DELETE", "/api/scenes/([a-z0-9-]+)")
def delete_scene(q, body, scene_id):
    app = current_app()
    if app.scene_jobs.busy(scene_id) or app.jobs.busy_scene(scene_id):
        raise ApiError(409, "Cancel scene jobs before deleting this scene")
    app.scenes.delete(scene_id)
    return {"deleted": scene_id}


@route("GET", "/api/scenes/([a-z0-9-]+)/timeline")
def scene_timeline(q, body, scene_id):
    try:
        scene = current_app().scenes.get(scene_id)
        return compile_timeline(scene, current_app().lib, current_app().scenes)
    except ValueError as e:
        raise ApiError(400, str(e)) from None


@route("POST", "/api/scenes/([a-z0-9-]+)/render")
def render_scene(q, body, scene_id):
    if current_app().scenes.get(scene_id).get("mode") == "stage3d":
        raise ApiError(400, "Use the 3D stage recorder for this scene")
    if not ffmpeg_path():
        raise ApiError(400, "Install FFmpeg to export MP4 video")
    try:
        return {"job": current_app().scene_jobs.submit(scene_id)}
    except ValueError as e:
        raise ApiError(400, str(e)) from None


@route("POST", "/api/scenes/([a-z0-9-]+)/voice")
def voice_scene(q, body, scene_id):
    try:
        return {"job": current_app().jobs.submit_scene_voice(scene_id, body)}
    except ValueError as e:
        raise ApiError(400, str(e)) from None


@route("POST", "/api/scenes/([a-z0-9-]+)/background")
def generate_scene_background(q, body, scene_id):
    options = clean_options(body.get("options"), current_app().cfg)     # the page sends its Models choice; none sent = the app's defaults
    try:
        return {"job": current_app().jobs.submit_scene_background(scene_id, options)}
    except ValueError as e:
        raise ApiError(400, str(e)) from None


@route("POST", "/api/scenes/([a-z0-9-]+)/frames")
def upload_scene_frames(q, body, scene_id):
    try:
        return {"render": scene_recording.publish(current_app().scenes, scene_id, body["revision"],
                                                  body["fingerprint"], body["frames"])}
    except ValueError as e:
        raise ApiError(400, str(e)) from None


@route("GET", "/api/scenes/jobs/([a-f0-9]+)")
def scene_job(q, body, job_id):
    return current_app().scene_jobs.get(job_id)


@route("POST", "/api/scenes/jobs/([a-f0-9]+)/cancel")
def cancel_scene_job(q, body, job_id):
    return current_app().scene_jobs.cancel(job_id)


@route("GET", "/api/scenes/([a-z0-9-]+)/files/(render/[a-f0-9]+\\.mp4)")
def scene_file(q, body, scene_id, rel):
    path = current_app().scenes.resolve(scene_id, rel)
    if not path.is_file():
        raise ApiError(404, "Scene video not found")
    return Raw(atomic.read_bytes(path), "video/mp4", {"Cache-Control": "no-cache"})


@route("GET", "/api/scenes/([a-z0-9-]+)/files/(audio/[a-z0-9_-]+-[a-f0-9]{10}\\.wav)")
def scene_audio_file(q, body, scene_id, rel):
    path = current_app().scenes.resolve(scene_id, rel)
    if not path.is_file():
        raise ApiError(404, "Scene audio not found")
    return Raw(atomic.read_bytes(path), "audio/wav", {"Cache-Control": "no-cache"})


@route("GET", "/api/scenes/([a-z0-9-]+)/files/(background/[a-f0-9]{10}\\.png)")
def scene_background_file(q, body, scene_id, rel):
    scene = current_app().scenes.get(scene_id)
    if (scene.get("background") or {}).get("file") != rel:
        raise ApiError(404, "Scene background not found")
    path = current_app().scenes.resolve(scene_id, rel)
    if not path.is_file():
        raise ApiError(404, "Scene background not found")
    return Raw(atomic.read_bytes(path), "image/png", {"Cache-Control": "no-cache"})


@route('GET', r'/api/scenes/([a-z0-9-]+)/files/((?:media/[a-f0-9]{64}\.wav|props/[a-f0-9]{64}\.glb))')
def scene_studio_media_file(q, body, scene_id, rel):
    scenes = current_app().scenes; scene = scenes.get(scene_id)
    manifest = [*scene.get('media', {}).values(), *scene.get('prop_sources', {}).values()]
    meta = next((row for row in manifest if row.get('file') == rel), None)
    if not meta:
        raise ApiError(404, 'Scene media not found')
    data = atomic.read_bytes(scenes.resolve(scene_id, rel))
    if hashlib.sha256(data).hexdigest() != meta.get('sha256'):
        raise ApiError(409, 'Scene media changed; review it before use')
    return Raw(data, 'audio/wav' if rel.endswith('.wav') else 'model/gltf-binary', {'Cache-Control': 'no-cache'})


@route("GET", "/api/scenes/([a-z0-9-]+)/export\\.zip")
def export_scene(q, body, scene_id):
    app = current_app()
    scene = app.scenes.get(scene_id)
    try:
        data = scene_export.make_zip(scene, app.scenes, (cfgmod.ROOT / "NOTICE.md").read_text("utf-8"))
    except ValueError as e:
        raise ApiError(400, str(e)) from None
    stem = re.sub(r"[^a-z0-9]+", "-", scene["title"].lower()).strip("-")[:60] or "scene"
    return Raw(data, "application/zip", {"Content-Disposition": f'attachment; filename="{stem}-scene.zip"'})


@route("POST", "/api/cast-packs")
def create_cast_pack(q, body):
    try:
        return current_app().cast_packs.create(body, _cast_pack_npc_ids())
    except ValueError as e:
        raise ApiError(400, str(e)) from None


@route("GET", "/api/cast-packs/([a-z0-9-]+)/export\\.zip")
def export_cast_pack(q, body, pack_id):
    try:
        pack = current_app().cast_packs.get(pack_id)
    except KeyError:
        raise ApiError(404, f"No cast pack with id {pack_id}") from None
    npc_ids = [member["npc_id"] for member in pack["members"]]
    member_zip = current_app().lib.export_zip(npc_ids)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("cast-pack.json", json.dumps(pack, indent=2, ensure_ascii=False))
        with zipfile.ZipFile(io.BytesIO(member_zip), "r") as source:
            for name in source.namelist():
                if name == "index.json":
                    continue
                archive.writestr(f"members/{name}", source.read(name))
    stem = export.slugify(pack["name"]) or "cast-pack"
    return Raw(output.getvalue(), "application/zip", {"Content-Disposition": f'attachment; filename="{stem}-cast-pack.zip"'})


@route("GET", "/api/cast-packs/([a-z0-9-]+)")
def get_cast_pack(q, body, pack_id):
    try:
        return current_app().cast_packs.get(pack_id)
    except KeyError:
        raise ApiError(404, f"No cast pack with id {pack_id}") from None


@route("PUT", "/api/cast-packs/([a-z0-9-]+)")
def update_cast_pack(q, body, pack_id):
    try:
        return current_app().cast_packs.update(pack_id, body, _cast_pack_npc_ids())
    except KeyError:
        raise ApiError(404, f"No cast pack with id {pack_id}") from None
    except ValueError as e:
        raise ApiError(400, str(e)) from None


@route("DELETE", "/api/cast-packs/([a-z0-9-]+)")
def delete_cast_pack(q, body, pack_id):
    try:
        current_app().cast_packs.delete(pack_id)
    except KeyError:
        raise ApiError(404, f"No cast pack with id {pack_id}") from None
    return {"deleted": pack_id}


@route("POST", "/api/npcs")
def create_npc(q, body):
    data = roll.load_data()
    return current_app().lib.create(validated(data, body))


@route("GET", "/api/npcs/([a-z0-9-]+)")
def get_npc(q, body, npc_id):
    return load_npc(npc_id)


@route("POST", "/api/npcs/([a-z0-9-]+)/dialogue/generate")
def generate_dialogue(q, body, npc_id):
    npc = load_npc(npc_id)
    pack = dialogue.generate(npc)
    updated = current_app().lib.update(npc_id, lambda record: record.update(dialogue=pack))
    return {"dialogue": updated["dialogue"]}


@route("PUT", "/api/npcs/([a-z0-9-]+)/dialogue")
def save_dialogue(q, body, npc_id):
    load_npc(npc_id)
    try:
        payload = body.get("dialogue", body) if isinstance(body, dict) else body
        pack = dialogue.validate(payload)
    except ValueError as e:
        raise ApiError(400, str(e)) from None
    pack["npc_id"] = npc_id
    updated = current_app().lib.update(npc_id, lambda record: record.update(dialogue=pack))
    return {"dialogue": updated["dialogue"]}


@route("GET", "/api/npcs/([a-z0-9-]+)/dialogue/export/(json|ink|yarn)")
def export_dialogue(q, body, npc_id, format_id):
    npc = load_npc(npc_id)
    if not npc.get("dialogue"):
        raise ApiError(404, "This NPC has no dialogue pack yet")
    voiced = {line_id: meta["file"] for line_id, _, _, meta in voice_pack_mod.voiced_lines(npc)}    # up-to-date voiced lines
    content, content_type, extension = dialogue.render(npc["dialogue"], format_id, voiced)
    stem = export.slugify(f'{npc["profile"]["first_name"]}-{npc["profile"]["last_name"]}')
    return Raw(content.encode("utf-8"), content_type,
               {"Content-Disposition": f'attachment; filename="{stem}-dialogue.{extension}"'})


# ---- voices (npcgen/voice) ------------------------------------------------------------------------------------------------
@route("GET", "/api/voice/engines")
def voice_engines(q, body):
    """Voice authoring options and compatibility metadata for saved records. See /api/voice/lab for available voices."""
    app = current_app()
    return {"engines": voice_engines_mod.choices(), "default": voice_engines_mod.DEFAULT_ENGINE, "lab_api": 1,
            "stock": {e: voice_engines_mod.stock_voices(e) for e in voice_engines_mod.ENGINES if voice_engines_mod.can(e, "stock")}, "presets": voice_describe.presets(), "library_schema": 1,
            "studio_schema": 1, "effects_schema": 1, "effects_caps": voice_effects.capabilities(),
            "audition_schema": 1,
            "alignment_schema": 1, "alignment_caps": voice_alignment.capabilities(app.cfg), "game_export_schema": 1, "character_rig_schema": 1, "mesh_rig_schema": 1,
            "performance_caps": {e: voice_authoring.capabilities(e, directed=True) for e in voice_engines_mod.ENGINES}}


def _voice_library_call(fn):
    try:
        return fn(current_app().voice_library)
    except (ValueError, TypeError) as e:
        raise ApiError(400, str(e)) from None
    except (KeyError, FileNotFoundError):
        raise ApiError(404, "Voice, reference version, or character does not exist") from None


@route("GET", "/api/voice/library")
def voice_library_list(q, body):
    return _voice_library_call(lambda store: {"voices": store.list(q.get("q", [""])[0], q.get("tag", [""])[0],
                                                               q.get("favorites", [""])[0] == "1"), "schema": 1})


@route("POST", "/api/voice/library")
def voice_library_save(q, body):
    npc = _voice_npc(body.get("npc_id"))
    return _voice_library_call(lambda store: {"voice": store.from_npc(npc["id"], body)})


@route("GET", "/api/voice/library/([a-f0-9]{32})")
def voice_library_get(q, body, voice_id):
    return _voice_library_call(lambda store: {"voice": store.get(voice_id)})


@route("PUT", "/api/voice/library/([a-f0-9]{32})")
def voice_library_update(q, body, voice_id):
    return _voice_library_call(lambda store: {"voice": store.update(voice_id, body)})


@route("GET", "/api/voice/library/versions/([a-f0-9]{32})/audio\\.wav")
def voice_library_audio(q, body, version_id):
    return _voice_library_call(lambda store: Raw(store.read_audio(version_id), "audio/wav"))


@route("POST", "/api/voice/library/([a-f0-9]{32})/recordings")
def voice_library_recording(q, body, voice_id):
    return _voice_library_call(lambda store: {"version": store.recording(voice_id, body)})


@route("POST", "/api/voice/library/versions/([a-f0-9]{32})/approval")
def voice_library_approve(q, body, version_id):
    return _voice_library_call(lambda store: {"version": store.approve(version_id, body)})


@route("POST", "/api/voice/library/versions/([a-f0-9]{32})/derive")
def voice_library_derive(q, body, version_id):
    return _voice_library_call(lambda store: {"version": store.derive(version_id, body)})


@route("POST", "/api/voice/library/versions/([a-f0-9]{32})/transcript-draft")
def voice_library_transcript_draft(q, body, version_id):
    """A suggested transcript for the region (default: the whole reference). Nothing is saved; the editor checks it."""
    def draft(store):
        version = store.get_version(version_id)
        language = (version.get("reference") or {}).get("language") or "English"
        return {"draft": voice_transcript_draft.draft(store.read_audio(version_id), start=body.get("start_seconds"),
                                                      end=body.get("end_seconds"), language=language, cfg=current_app().cfg)}
    return _voice_library_call(draft)


@route("POST", "/api/voice/library/versions/([a-f0-9]{32})/denoise")
def voice_library_denoise(q, body, version_id):
    """A new unapproved draft with background noise reduced. Words and timing are unchanged; identity is not checked."""
    return _voice_library_call(lambda store: {"version": store.denoise(version_id, body)})


@route("POST", "/api/voice/library/versions/([a-f0-9]{32})/assign")
def voice_library_assign(q, body, version_id):
    ids = body.get("npc_ids")
    if isinstance(ids, list):
        for npc_id in ids:
            _voice_npc(npc_id)  # Honor extension visibility/adult guards for every selected character.
    return _voice_library_call(lambda store: {"npcs": [voice_authoring.annotate(npc) for npc in store.assign(version_id, ids)]})


@route("GET", "/api/npcs/([a-z0-9-]+)/voice/references")
def voice_reference_history(q, body, npc_id):
    v = voice_record.voice_of(_voice_npc(npc_id))
    return {"references": v.get("reference_history", []), "active_id": voice_record.identity_id(v)}


@route("POST", "/api/npcs/([a-z0-9-]+)/voice/references/([a-f0-9]{12,32})/select")
def voice_reference_restore(q, body, npc_id, reference_id):
    _voice_npc(npc_id)
    return _voice_library_call(lambda store: voice_authoring.annotate(voice_record.restore_reference(store.lib, npc_id, reference_id)))


def _voice_npc(npc_id):
    return check_npc(load_npc(npc_id))


@route("POST", "/api/voice/batch")
def voice_batch_route(q, body):
    """Voice many characters: {npc_ids, engine, takes 1-4, lines: bool, redo: bool}. Each gets the voice its sheet suggests
    (unless it has voice settings), `takes` takes with the first used until you pick one on the Review page, and optionally
    its dialogue voiced in the same job. A character whose voice you picked keeps it unless redo is set."""
    ids = body.get("npc_ids")
    if not isinstance(ids, list) or not ids or not all(isinstance(i, str) for i in ids):
        raise ApiError(400, "npc_ids must be a non-empty list")
    try:
        request = voice_batch.clean_request(body)
    except ValueError as e:
        raise ApiError(400, str(e)) from None
    npcs = [check_npc(load_npc(npc_id)) for npc_id in list(dict.fromkeys(ids))[:1000]]
    jobs = submit_all(npcs, {"voice": request}, clean_options({}, current_app().cfg))
    return {"jobs": jobs, "skipped": len(npcs) - len(jobs)}


@route("GET", "/api/voice/review")
def voice_review(q, body):
    """Characters whose voice waits for you (batch takes, first take in use), with their takes."""
    return voice_batch.review_rows(current_app().lib, [n["id"] for n in visible_library()])


@route("POST", "/api/npcs/([a-z0-9-]+)/voice/suggest")
def voice_suggest(q, body, npc_id):
    """The voice the character sheet suggests: a description, the best stock voices, and what a take reads."""
    npc = load_npc(npc_id)
    if isinstance(body.get("npc"), dict):                   # the unsaved sheet in the editor, if the page sends it
        npc = {**npc, **{k: body["npc"][k] for k in ("profile", "setting", "style", "morphology") if k in body["npc"]}}
    v = voice_record.voice_of(npc)
    return {"description": voice_describe.describe(npc), "stock": voice_describe.stock_match(npc)[:6],
            "script": voice_describe.take_script(npc, v["language"]), "adult": voice_describe.is_adult(npc)}


@route("GET", "/api/voice/lab")
def voice_lab(q, body):
    """Find Voice Lab and list the named preset and Cast voices it offers."""
    try:
        lab = LabClient.discover()
        return {"connected": True, "voices": lab.voices(), "url": lab.url}
    except LabError as error:
        return {"connected": False, "voices": [], "message": str(error)}


@route("GET", "/api/suite/apps")
def suite_apps(q, body):
    """Connected apps (suite contract 14): Voice Lab and Altera where they answer, with their versions and addresses, or why
    they were not found; for Voice Lab, how many of its voices are ready; and whether Send to Altera has an inbox to use."""
    apps = {}
    for app in ("voice-lab", "altera"):
        found = fels_suite.find_app(app)
        block = (found["health"] or {}).get("fels")
        # "is not running" is said by the page in its own words; the other notes say what was tried.
        apps[app] = {"url": found["url"], "version": block.get("version") if isinstance(block, dict) else None,
                     "notes": found["notes"] if found["url"] else found["notes"][:-1]}
    lab = apps["voice-lab"]
    if lab["url"]:
        try:
            lab["voices_ready"] = sum(1 for voice in LabClient.discover(lab["url"]).voices() if voice.get("ready"))
        except LabError as error:
            lab["voices_ready"] = None
            lab["notes"].append(str(error))
    apps["altera"]["inbox"] = character_card.inbox_ready()
    return {"apps": apps, "suite_problem": fels_suite.suite_dir_problem()}


@route("PUT", "/api/npcs/([a-z0-9-]+)/voice/lab")
def voice_lab_assign(q, body, npc_id):
    """Use a named Voice Lab voice for new speech while keeping the character's saved WAV files."""
    _voice_npc(npc_id)
    name = body.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ApiError(400, "Choose a voice from Voice Lab's list.")
    try:
        selected = lab_voice.assign(LabClient.discover(), name)
    except LabError as error:
        raise ApiError(400 if error.code in ("voice_missing", "voice_not_ready") else 503, str(error)) from None
    return voice_authoring.annotate(current_app().lib.update(npc_id, lambda rec: lab_voice.save(rec, selected)))


@route("POST", "/api/npcs/([a-z0-9-]+)/voice/lab")
def voice_lab_create(q, body, npc_id):
    """Queue a new Voice Lab Cast voice from a description or an uploaded or saved reference recording."""
    npc = _voice_npc(npc_id)
    lib = current_app().lib
    try:
        if body.get("source") == "recording" and "audio" in body:
            transcript = str(body.get("transcript") or "").strip()
            if not 3 <= len(transcript) <= 600:
                raise ValueError("Enter the exact words the recording says (3 to 600 characters).")
            if body.get("consent") is not True:
                raise ValueError("Confirm that you may use the recording in Voice Lab.")
            wav, _ = voice_audio.check_upload(voice_service.decode_upload(body["audio"]))
            digest = hashlib.sha256(wav).hexdigest()
            ref = {"file": f"voice/references/{digest}.wav", "sha256": digest, "text": transcript}
            request = lab_voice.request(npc, body, uploaded=ref)
            lib.write_asset(npc_id, ref["file"], wav)
        else:
            request = lab_voice.request(npc, body)
        if request.get("reference") and "sha256" not in request["reference"]:
            frozen = voice_record.freeze_voice(lib, npc_id, {"reference": request["reference"]})
            request["reference"] = frozen["reference"]
    except (ValueError, KeyError, OSError) as error:
        raise ApiError(400, str(error)) from None
    return {"job": _voice_job(npc_id, {"lab_create": request, "quick": True})}


@route("PUT", "/api/npcs/([a-z0-9-]+)/voice")
def voice_save(q, body, npc_id):
    npc = _voice_npc(npc_id)
    try:
        clean = voice_record.clean_settings(body, npc)
    except ValueError as e:
        raise ApiError(400, str(e)) from None
    return voice_authoring.annotate(current_app().lib.update(npc_id, lambda rec: voice_record.save_settings(rec, clean, current_app().lib)))


@route("PUT", "/api/npcs/([a-z0-9-]+)/voice/studio")
def voice_studio_save(q, body, npc_id):
    npc = _voice_npc(npc_id)
    try:
        studio = voice_authoring.clean_studio(body)
        voice = voice_record.voice_of(npc)
        speaker = voice_record.speaker_for(voice)
        for preset in studio["presets"]:
            voice_authoring.validate_performance({k: preset[k] for k in ("direction", "parameters", "effects") if k in preset},
                                                 speaker, lab_voice=voice.get("lab_voice"))
            problem = voice_describe.adult_problem(npc, preset["direction"])
            if problem:
                raise ValueError(problem)
    except ValueError as e:
        raise ApiError(400, str(e)) from None
    def apply(rec):
        v = voice_record.voice_of(rec)
        v["studio"] = studio
        v["updated"] = now()
        rec["voice"] = v
    return voice_authoring.annotate(current_app().lib.update(npc_id, apply))


@route("POST", "/api/npcs/([a-z0-9-]+)/voice/studio/preview")
def voice_studio_preview(q, body, npc_id):
    npc = _voice_npc(npc_id)
    try:
        text = str(body.get("text") or "")
        if not text or len(text) > 500:
            raise ValueError("Preview needs 1-500 characters")
        v = voice_record.voice_of(npc)
        if "studio" in body:
            v["studio"] = voice_authoring.clean_studio(body["studio"])
        _, _, details = voice_authoring.prepare(v, text, preset_id=body.get("performance_id"))
        return {"authored_text": text, **details}
    except ValueError as e:
        raise ApiError(400, str(e)) from None


@route("POST", "/api/npcs/([a-z0-9-]+)/voice/effects/preview")
def voice_effects_preview(q, body, npc_id):
    npc = _voice_npc(npc_id)
    try:
        return voice_effects.preview(current_app().lib, npc, body.get("source"), body.get("effects", {}))
    except (ValueError, KeyError, FileNotFoundError) as e:
        raise ApiError(400, str(e)) from None


@route("POST", "/api/npcs/([a-z0-9-]+)/voice/lip-sync/preview")
def voice_alignment_preview(q, body, npc_id):
    npc = _voice_npc(npc_id)
    try:
        return voice_alignment.analyze_source(current_app().lib, npc, body.get('source'),
            body.get('settings', (voice_record.voice_of(npc).get('studio') or {}).get('lip_sync')), current_app().cfg)
    except (ValueError, KeyError, FileNotFoundError) as e:
        raise ApiError(400, str(e)) from None


@route('GET', '/api/npcs/([a-z0-9-]+)/voice/rigs')
def voice_rigs(q, body, npc_id):
    npc = _voice_npc(npc_id)
    return {kind: voice_character_rig.describe(current_app().lib, npc, kind) for kind in voice_character_rig.KINDS}


@route('GET','/api/npcs/([a-z0-9-]+)/voice/mesh-rig')
def voice_mesh_get(q,body,npc_id):
    return voice_mesh_rig.describe(current_app().lib,_voice_npc(npc_id))


@route('PUT','/api/npcs/([a-z0-9-]+)/voice/mesh-rig')
def voice_mesh_save(q,body,npc_id):
    _voice_npc(npc_id)
    return _voice_library_call(lambda store:voice_authoring.annotate(voice_mesh_rig.save(store.lib,npc_id,body)))


@route('PUT', '/api/npcs/([a-z0-9-]+)/voice/rig/(portrait|sprite)')
def voice_rig_save(q, body, npc_id, kind):
    _voice_npc(npc_id)
    return _voice_library_call(lambda store: voice_authoring.annotate(voice_character_rig.save(store.lib, npc_id, kind, body)))


@route('POST', '/api/npcs/([a-z0-9-]+)/voice/rig/(portrait|sprite)/frames')
def voice_rig_frame(q, body, npc_id, kind):
    _voice_npc(npc_id)
    return _voice_library_call(lambda store: voice_authoring.annotate(voice_character_rig.upload(store.lib, npc_id, kind, body)))


@route('POST', '/api/npcs/([a-z0-9-]+)/voice/rig/(portrait|sprite)/restore/([a-f0-9]{64})')
def voice_rig_restore(q, body, npc_id, kind, version):
    _voice_npc(npc_id)
    return _voice_library_call(lambda store: voice_authoring.annotate(voice_character_rig.restore(store.lib,npc_id,kind,version)))


@route("POST", "/api/npcs/([a-z0-9-]+)/voice/lip-sync")
def voice_alignment_save(q, body, npc_id):
    npc = _voice_npc(npc_id)
    key = body.get('source')
    if not isinstance(key, str) or not key.startswith(('line:', 'test:')):
        raise ApiError(400, 'Choose a dialogue line or speech test to update its alignment')
    try:
        app = current_app()
        result = voice_alignment.analyze_source(app.lib, npc, key, (voice_record.voice_of(npc).get('studio') or {}).get('lip_sync'), app.cfg)
        captured = voice_alignment.source(npc, key)
        def apply(rec):
            current = voice_alignment.source(rec, key)
            if current.get('file') != captured.get('file') or hashlib.sha256(app.lib.read_asset(npc_id, current['file'])).hexdigest() != result['lip_sync']['audio_sha256']:
                raise ValueError('The speech clip changed while it was being aligned; try again')
            if voice_alignment.signature((voice_record.voice_of(rec).get('studio') or {}).get('lip_sync')) != result['lip_sync']['config_signature']:
                raise ValueError('Lip-sync settings changed while the clip was being aligned; try again')
            current['lip_sync'] = result['lip_sync']
        return voice_authoring.annotate(app.lib.update(npc_id, apply))
    except (ValueError, KeyError, FileNotFoundError) as e:
        raise ApiError(400, str(e)) from None


@route("POST", "/api/npcs/([a-z0-9-]+)/voice/comparisons")
def voice_comparison_create(q, body, npc_id):
    _voice_npc(npc_id)
    return _voice_library_call(lambda store: voice_comparison.create(store.lib, npc_id, body))


@route("PUT", "/api/npcs/([a-z0-9-]+)/voice/comparisons/([a-f0-9]{12})/candidates/([a-f0-9]{8})")
def voice_comparison_review(q, body, npc_id, comparison_id, take_id):
    _voice_npc(npc_id)
    return _voice_library_call(lambda store: voice_comparison.review(store.lib, npc_id, comparison_id, take_id, body))


def _voice_job(npc_id, request):
    npc = _voice_npc(npc_id)
    if request.get("voice_snapshot"):
        try:
            request["voice_snapshot"] = voice_record.freeze_voice(current_app().lib, npc_id, request["voice_snapshot"])
        except ValueError as e:
            raise ApiError(400, str(e)) from None
    return current_app().jobs.submit(npc, {"voice": request}, clean_options({}, current_app().cfg))


@route("POST", "/api/npcs/([a-z0-9-]+)/voice/takes")
def voice_takes(q, body, npc_id):
    """Queue 1-4 candidate voices (the quick lane: they run before waiting picture jobs, never in the middle of one)."""
    npc = _voice_npc(npc_id)
    if isinstance(body.get("settings"), dict):              # save what the designer shows first, so the takes match it
        try:
            clean = voice_record.clean_settings(body["settings"], npc)
        except ValueError as e:
            raise ApiError(400, str(e)) from None
        npc = current_app().lib.update(npc_id, lambda rec: voice_record.save_settings(rec, clean, current_app().lib))
    try:
        request = voice_service.takes_request(npc, body,current_app().cfg)
    except ValueError as e:
        raise ApiError(400, str(e)) from None
    return {"job": _voice_job(npc_id, request)}


@route("POST", "/api/npcs/([a-z0-9-]+)/voice/takes/([a-f0-9]+)/select")
def voice_select(q, body, npc_id, take_id):
    _voice_npc(npc_id)
    try:
        return voice_authoring.annotate(voice_record.select_take(current_app().lib, npc_id, take_id))
    except KeyError:
        raise ApiError(404, "That take does not exist") from None
    except ValueError as e:
        raise ApiError(400, str(e)) from None


@route("DELETE", "/api/npcs/([a-z0-9-]+)/voice/takes/([a-f0-9]+)")
def voice_delete_take(q, body, npc_id, take_id):
    npc = load_npc(npc_id)
    if take_id in voice_comparison.protected_take_ids(voice_record.voice_of(npc)):
        raise ApiError(400, "This candidate is retained by a comparison session")
    lib = current_app().lib

    def apply(rec):
        v = voice_record.voice_of(rec)
        take = next((t for t in v["takes"] if t["id"] == take_id), None)
        if take is None:
            raise KeyError(take_id)
        v["takes"] = [t for t in v["takes"] if t["id"] != take_id]
        lib.remove_asset_files(npc_id, take)
        rec["voice"] = v
    try:
        return lib.update(npc_id, apply)
    except KeyError:
        raise ApiError(404, "That take does not exist") from None


@route("POST", "/api/npcs/([a-z0-9-]+)/voice/test")
def voice_test(q, body, npc_id):
    """Say a line (or preview a mood) in the current voice: a quick job, its clip lands in voice/tests/."""
    npc = _voice_npc(npc_id)
    try:
        request = voice_service.test_request(npc, body)
    except ValueError as e:
        raise ApiError(400, str(e)) from None
    return {"job": _voice_job(npc_id, request)}


@route("POST", "/api/npcs/([a-z0-9-]+)/voice/auditions")
def voice_audition_create(q, body, npc_id):
    npc = _voice_npc(npc_id)
    session_id, request = _voice_library_call(lambda store: voice_audition.request(store.lib, npc, body))
    try:
        job = _voice_job(npc_id, request)
    except Exception as error:
        voice_audition.attach_job(current_app().lib, npc_id, session_id, error=str(error))
        raise
    saved = voice_audition.attach_job(current_app().lib, npc_id, session_id, job=job['id'])
    return {"job": job, "npc": voice_authoring.annotate(saved)}


@route("PUT", "/api/npcs/([a-z0-9-]+)/voice/auditions/([a-f0-9]{12})/cases/([a-z]+)")
def voice_audition_review(q, body, npc_id, session_id, case_id):
    _voice_npc(npc_id)
    return _voice_library_call(lambda store: voice_audition.review(store.lib, npc_id, session_id, case_id, body))


@route("GET", "/api/npcs/([a-z0-9-]+)/voice/auditions/([a-f0-9]{12})/pack\\.zip")
def voice_audition_pack(q, body, npc_id, session_id):
    npc = _voice_npc(npc_id)
    data = _voice_library_call(lambda store: voice_audition.pack(store.lib, npc, session_id))
    return Raw(data, 'application/zip', {'Content-Disposition': f'attachment; filename="{npc_id}-audition-{session_id}.zip"'})


@route("POST", "/api/npcs/([a-z0-9-]+)/voice/lines")
def voice_lines(q, body, npc_id):
    """Voice the dialogue lines named in `ids` (every line when none are named) in the saved voice."""
    npc = _voice_npc(npc_id)
    try:
        request = voice_service.lines_request(npc, body)
    except ValueError as e:
        raise ApiError(400, str(e)) from None
    return {"job": _voice_job(npc_id, request)}


@route("GET", "/api/npcs/([a-z0-9-]+)/voice-pack\\.zip")
def voice_pack(q, body, npc_id):
    """The voice and every up-to-date voiced line, with lip-sync tracks and the dialogue files (npcgen/voice/pack.py)."""
    try:
        data, stem = voice_pack_mod.build(current_app().lib, load_npc(npc_id))
    except ValueError as e:
        raise ApiError(400, str(e)) from None
    return Raw(data, "application/zip", {"Content-Disposition": f'attachment; filename="{stem}-voice-pack.zip"'})


@route("POST", "/api/npcs/([a-z0-9-]+)/voice/upload")
def voice_upload(q, body, npc_id):
    """A recording to copy: {audio: WAV data URL, text, consent: true}. The page converts any clip to 16 kHz mono WAV so 40 s
    fits the 2 MB request limit; that is plenty for the models to copy a voice from."""
    npc = _voice_npc(npc_id)
    try:
        return voice_authoring.annotate(voice_service.apply_upload(current_app().lib, npc, body))
    except ValueError as e:
        raise ApiError(400, str(e)) from None


@route("POST", "/api/npcs/([a-z0-9-]+)/voice/copy")
def voice_copy(q, body, npc_id):
    npc = _voice_npc(npc_id)
    source_id = body.get("from")
    if not isinstance(source_id, str) or source_id == npc_id:
        raise ApiError(400, "Name another character to copy the voice from")
    try:
        return voice_authoring.annotate(voice_service.apply_copy(current_app().lib, npc, load_npc(source_id)))
    except ValueError as e:
        raise ApiError(400, str(e)) from None


@route("PUT", "/api/npcs/([a-z0-9-]+)")
def update_npc(q, body, npc_id):
    existing = load_npc(npc_id)
    clean = validated(roll.load_data(), body)
    before = existing.get("profile") or {}
    if clean["profile"]["age"] != before.get("age"):
        clean["profile"].pop("age_source", None)            # an age set by hand is no longer a rolled one
    elif before.get("age_source") and "age_source" not in clean["profile"]:
        clean["profile"]["age_source"] = before["age_source"]   # (a caller that does not know the field changes nothing)
    if existing.get("adult") is True and clean["profile"]["age"] < roll.ADULT_AGE:
        raise ApiError(400, f"This character was made as a grown adult, so the age must be {roll.ADULT_AGE} or more")
    fields = ("setting", "style", "freeform", "morphology", "profile", "appearance")
    # An edit changes what every picture and model was made from, so each is marked stale. `keep_fresh` names the kinds the
    # caller knows this edit does not touch, which stay as they are (a description fixed for the portrait only, say).
    keep_fresh = body.get("keep_fresh") or []
    if not isinstance(keep_fresh, list) or any(k not in jobsmod.STALE_KINDS for k in keep_fresh):
        raise ApiError(400, "keep_fresh must be a list of: " + ", ".join(jobsmod.STALE_KINDS))

    def apply(npc):
        profile_changed = npc.get("profile") != clean.get("profile")
        changed = any(npc.get(k) != clean.get(k) for k in fields)
        for k in fields:
            npc[k] = clean[k]
        if changed:
            jobsmod.mark_assets_stale(npc, keep_fresh)
        if npc.get("dialogue") and profile_changed:
            npc["dialogue"]["stale"] = True
        if npc.get("voice") and profile_changed:           # only a hint: a voice you picked is never replaced by itself
            npc["voice"]["sheet_changed"] = True
    return current_app().lib.update(npc_id, apply)


@route("POST", "/api/npcs/([a-z0-9-]+)/fresh")
def mark_fresh(q, body, npc_id):
    """Say that some pictures and models are still right after an edit, which takes their 'out of date' flag off without making them again: body {"kinds": [...]} (the same kinds as
    `keep_fresh`). The files are not touched. Returns the character."""
    load_npc(npc_id)
    kinds = (body or {}).get("kinds")
    if not isinstance(kinds, list) or not kinds or any(k not in jobsmod.STALE_KINDS for k in kinds):
        raise ApiError(400, "kinds must be a list of: " + ", ".join(jobsmod.STALE_KINDS))
    return current_app().lib.update(npc_id, lambda npc: jobsmod.clear_stale(npc, kinds))


@route("DELETE", "/api/npcs/([a-z0-9-]+)")
def delete_npc(q, body, npc_id):
    load_npc(npc_id)
    current_app().lib.delete(npc_id)
    current_app().cast_packs.remove_npc(npc_id)
    current_app().faces.forget(npc_id)
    current_app().dressings.forget_npc(npc_id)
    return {"deleted": npc_id}


@route("DELETE", "/api/npcs/([a-z0-9-]+)/assets/(portrait|sprite|model_3d|rig_3d|head_3d|expressions|animations|sheet|living|source_photo|voice|voice_lines)(?:/([a-zA-Z0-9_-]+))?")
def delete_asset(q, body, npc_id, kind, key):
    load_npc(npc_id)
    if kind in ("expressions", "animations", "voice_lines") and not key:
        raise ApiError(400, f"Name the {kind[:-1]} to delete")
    if kind == "voice" and key:
        raise ApiError(400, "A voice is deleted as a whole; delete a single take or line instead")
    if kind == "sheet" and key:
        raise ApiError(400, "A model sheet is deleted as a whole; re-roll a single view instead")
    if kind == "living" and key:
        raise ApiError(400, "A living portrait is deleted as a whole; redo an expression to change one frame")
    try:
        return current_app().lib.delete_asset(npc_id, kind, key)
    except KeyError:
        raise ApiError(404, "That asset does not exist") from None


PICTURE_SLOT = "(portrait|sprite|expressions/[a-z0-9_-]+|sheet/[a-z0-9_]+)"        # the pictures that keep their earlier versions (library.SLOT_RE)


def _refuse_while_drawing(npc_id):
    if current_app().jobs.busy_npc(npc_id):
        raise ApiError(409, "This character has pictures queued or being drawn. Wait for the queue to finish with it, then try again.")


@route("POST", f"/api/npcs/([a-z0-9-]+)/assets/{PICTURE_SLOT}/candidates/([a-f0-9]+)/select")
def select_asset_candidate(q, body, npc_id, kind, candidate_id):
    """Bring back an earlier version of a picture (or a saved variant): the picture it replaces is kept in turn."""
    _refuse_while_drawing(npc_id)
    try:
        return current_app().lib.select_asset_candidate(npc_id, kind, candidate_id)
    except KeyError:
        raise ApiError(404, "That earlier version does not exist") from None


@route("DELETE", f"/api/npcs/([a-z0-9-]+)/assets/{PICTURE_SLOT}/candidates/([a-f0-9]+)")
def delete_asset_candidate(q, body, npc_id, kind, candidate_id):
    """Remove one earlier version of a picture for good. The picture in use is not touched."""
    _refuse_while_drawing(npc_id)
    try:
        return current_app().lib.delete_asset_candidate(npc_id, kind, candidate_id)
    except KeyError:
        raise ApiError(404, "That earlier version does not exist") from None


def _refuse_while_working(npc_id):
    if current_app().jobs.busy_npc(npc_id):
        raise ApiError(409, "This character has work queued or running. Wait for the queue to finish with it, then try again.")


@route("POST", "/api/npcs/([a-z0-9-]+)/model-history/([a-f0-9]{12})/restore")
def restore_model_version(q, body, npc_id, entry_id):
    """Bring back an earlier 3D model, with its skeleton and everything else it had: the model in use is kept in its place."""
    load_npc(npc_id)
    _refuse_while_working(npc_id)
    try:
        return current_app().lib.restore_model(npc_id, entry_id)
    except KeyError:
        raise ApiError(404, "That earlier 3D model does not exist") from None
    except ValueError as exc:
        raise ApiError(409, str(exc)) from None


@route("DELETE", "/api/npcs/([a-z0-9-]+)/model-history/([a-f0-9]{12})")
def delete_model_version(q, body, npc_id, entry_id):
    """Remove one earlier 3D model for good. The model in use is not touched."""
    load_npc(npc_id)
    _refuse_while_working(npc_id)
    try:
        return current_app().lib.delete_model_version(npc_id, entry_id)
    except KeyError:
        raise ApiError(404, "That earlier 3D model does not exist") from None


@route("POST", "/api/npcs/([a-z0-9-]+)/model-upload")
def upload_model(q, body, npc_id):
    """Use a .glb from the owner's computer as this character's 3D model, exactly as it is (the raw bytes, `?name=` says what the file is called). The model it replaces is kept among the earlier ones."""
    check_npc(load_npc(npc_id))
    _refuse_while_working(npc_id)
    data = body.get("glb")
    if not isinstance(data, bytes) or not data:
        raise ApiError(400, "Send the .glb file itself, with the content type " + MODEL_UPLOAD_TYPE)
    try:
        return jobsmod.use_uploaded_model(current_app().lib, npc_id, data, (q.get("name") or [""])[0])
    except ValueError as exc:
        raise ApiError(400, str(exc)) from None


@route("POST", "/api/npcs/([a-z0-9-]+)/photo")
def upload_photo(q, body, npc_id):
    """Attach (or replace) the real photo a portrait will be edited from. The existing portrait, if any, is kept
    but marked stale -- it was made without this photo, so a re-roll should not silently start reusing it."""
    check_npc(load_npc(npc_id))
    try:
        png, size = service.clean_photo(body.get("image"))
    except ValueError as e:
        raise ApiError(400, str(e)) from None
    lib = current_app().lib
    lib.write_asset(npc_id, "source_photo.png", png)

    def apply(npc):
        assets = npc.setdefault("assets", {})
        assets["source_photo"] = {"file": "source_photo.png", "created": now(), "size": size}
        if "portrait" in assets:
            assets["portrait"]["stale"] = True
    return lib.update(npc_id, apply)


FILE_TYPES = {".png": "image/png", ".json": "application/json", ".wav": "audio/wav", ".ogg": "audio/ogg", ".glb": "model/gltf-binary"}


@route("GET", "/api/npcs/([a-z0-9-]+)/files/(.+)")
def npc_file(q, body, npc_id, rel):
    try:
        path = current_app().lib.resolve(npc_id, rel)
    except KeyError:
        raise ApiError(404, "Not found") from None
    if not path.is_file() or path.suffix.lower() not in FILE_TYPES:
        raise ApiError(404, "Not found")
    stat = path.stat()
    ctype = FILE_TYPES[path.suffix.lower()]
    return Raw(atomic.read_bytes(path), ctype, {"Cache-Control": "no-cache"}, etag=f'"{stat.st_mtime_ns:x}-{stat.st_size:x}"')


# ---- creatures (the Creatures tab; npcgen/creatures.py keeps them, npcgen/creaturemaker.py makes them) -----------------------------

def load_creature(creature_id):
    try:
        return current_app().creatures.get(creature_id)
    except KeyError:
        raise ApiError(404, f"No creature with id {creature_id}") from None


@route("GET", "/api/creatures")
def list_creatures(q, body):
    raw = q.get("project", [None])[0]                           # ?project=<game> keeps one game's creatures (a name or a slug)
    project = None if raw is None else creature_records.project_slug(raw)
    return {"creatures": current_app().creatures.list(project), "briefs": creature_records.load_briefs(),
            "body_plans": list(creature_records.BODY_PLANS), "rig_built": list(creature_records.RIG_BUILT),
            "plans": creature_records.PLANS, "projects": current_app().creatures.projects.list(),
            "engines": list(creature_projects.ENGINES),
            "stages": [{"id": stage, "label": creature_records.STAGE_LABEL[stage]} for stage in creature_records.STAGES]}


@route("POST", "/api/creatures")
def create_creature(q, body):
    store = current_app().creatures
    if body.get("seed_bestiary"):
        game = creature_records.project_slug(body.get("project"))   # a game's brief creatures, or every game's when none is named
        return store.seed(game) if game else store.seed_all()
    try:
        created = store.create(creature_records.clean_creature(body, store.game(body.get("project"))))
    except ValueError as e:
        raise ApiError(400, str(e)) from None
    return creature_view(created["id"], created)


def creature_view(creature_id, creature):
    """A creature as the editor reads it: what is on disk (`has`), what the game-model step measured (`report`) and the
    licence rating of the engines that made it (`licence`)."""
    view = dict(creature)
    view["has"] = view.pop("_on_disk", {})
    view["report"] = current_app().creatures.report(creature_id)
    view["licence"] = current_app().creatures.licence(creature_id)
    view["candidates"] = current_app().creatures.candidates(creature_id)
    view["freshness"] = current_app().creatures.freshness(creature_id, creature)     # what each stage was made from
    return view


@route("GET", "/api/creatures/([a-z0-9-]+)")
def get_creature(q, body, creature_id):
    return creature_view(creature_id, load_creature(creature_id))


@route("PUT", "/api/creatures/([a-z0-9-]+)")
def update_creature(q, body, creature_id):
    current = load_creature(creature_id)
    merged = {key: current.get(key) for key in creature_records.EDITABLE}     # a partial edit keeps what it does not name
    merged.update(body)
    try:
        fields = creature_records.clean_creature(merged, current_app().creatures.game(merged.get("project")))
    except ValueError as e:
        raise ApiError(400, str(e)) from None
    def apply(creature):
        creature.update(fields)
    return creature_view(creature_id, current_app().creatures.update(creature_id, apply))


@route("DELETE", "/api/creatures/([a-z0-9-]+)")
def delete_creature(q, body, creature_id):
    load_creature(creature_id)
    if current_app().jobs.busy_creature(creature_id):
        raise ApiError(409, "This creature is being made: wait for its job to finish first")
    current_app().creatures.delete(creature_id)
    return {"deleted": creature_id}


@route("GET", "/api/creature-projects")
def list_creature_projects(q, body):
    """The games creatures are made for (npcgen/creature_projects.py), and the engines a pack can be installed for."""
    return {"projects": current_app().creatures.projects.list(), "engines": list(creature_projects.ENGINES),
            "body_plans": list(creature_records.BODY_PLANS)}


@route("GET", "/api/creature-projects/([a-z0-9-]+)")
def get_creature_project(q, body, slug):
    project = current_app().creatures.projects.get(slug)
    if project is None:
        raise ApiError(404, f"No game with the slug {slug}")
    return project


@route("POST", "/api/creature-projects")
def create_creature_project(q, body):
    """A new game, from the Projects panel. A slug a game already has is refused (409): an existing game is changed with PUT."""
    try:
        return current_app().creatures.projects.create(str(body.get("slug") or ""), body, creature_records.BODY_PLANS)
    except creature_projects.ProjectExists as e:
        raise ApiError(409, str(e)) from None
    except ValueError as e:
        raise ApiError(400, str(e)) from None


@route("PUT", "/api/creature-projects/([a-z0-9-]+)")
def put_creature_project(q, body, slug):
    """Create a game, or change one: its triangle budget, style, licence rule and clip names. A licence acceptance is only the
    director's own record, sent from the Projects panel."""
    try:
        return current_app().creatures.projects.put(slug, body, creature_records.BODY_PLANS)
    except ValueError as e:
        raise ApiError(400, str(e)) from None


@route("POST", "/api/creatures/([a-z0-9-]+)/make")
def make_creature(q, body, creature_id):
    """Queue the steps: body {"request": {"steps": ["concept", "3d", "prep"], "redo": [...]}, "options": {}}."""
    load_creature(creature_id)
    app = current_app()
    if app.jobs.busy_creature(creature_id):
        raise ApiError(409, "This creature is already being made")
    options = dict(body.get("options") or {})
    try:
        if body.get("preview"):                                 # what the request would make, and what it leaves out of date (nothing is queued)
            creature = app.creatures.get(creature_id)
            steps = creature_records.plan(creature, body.get("request"))
            return {"steps": steps, "out_of_date": creature_records.out_of_date(creature_records.exists(creature),
                                                                              [step["key"] for step in steps])}
        if "seeds" in options:                                  # a picture grid: its seeds are checked before the job is queued
            options["seeds"] = creature_records.clean_seeds(options["seeds"])
        return {"job": app.jobs.submit_creature(creature_id, body.get("request"), options)}
    except ValueError as e:
        raise ApiError(400, str(e)) from None


@route("POST", "/api/creatures/([a-z0-9-]+)/concept/choose")
def choose_creature_concept(q, body, creature_id):
    """Make one picture of the last grid the concept picture (and approve it): body {"seed": n}."""
    load_creature(creature_id)
    if current_app().jobs.busy_creature(creature_id):
        raise ApiError(409, "This creature is being made: choose a picture when the job finishes")
    try:
        seed = int(body.get("seed"))
    except (TypeError, ValueError):
        raise ApiError(400, "Choose a picture by its seed") from None
    try:
        return creature_view(creature_id, current_app().creatures.choose_concept(creature_id, seed))
    except ValueError as e:
        raise ApiError(400, str(e)) from None


@route("POST", "/api/creatures/([a-z0-9-]+)/approve")
def approve_creature(q, body, creature_id):
    """Approve the concept picture, so its 3D model can be made."""
    load_creature(creature_id)
    try:
        return creature_view(creature_id, current_app().creatures.approve_concept(creature_id))
    except ValueError as e:
        raise ApiError(400, str(e)) from None


@route("GET", "/api/creatures/([a-z0-9-]+)/files/(.+)")
def creature_file(q, body, creature_id, rel):
    try:
        path = current_app().creatures.resolve(creature_id, rel)
    except KeyError:
        raise ApiError(404, "Not found") from None
    suffix = path.suffix.lower()
    if not path.is_file() or suffix not in (*FILE_TYPES, ".log") or path.name in ("creature.json",):
        raise ApiError(404, "Not found")
    stat = path.stat()
    kind = FILE_TYPES.get(suffix, "text/plain; charset=utf-8")
    return Raw(atomic.read_bytes(path), kind, {"Cache-Control": "no-cache"}, etag=f'"{stat.st_mtime_ns:x}-{stat.st_size:x}"')


@route("GET", "/api/creatures/([a-z0-9-]+)/pack\\.zip")
def creature_pack(q, body, creature_id):
    """The creature's pack (npcgen/creaturepack.py): the game model, its concept picture and a manifest, as one ZIP."""
    try:
        files = creaturepack.build(current_app().creatures, creature_id)
    except KeyError:
        raise ApiError(404, f"No creature with id {creature_id}") from None
    except ValueError as e:
        raise ApiError(409, str(e)) from None
    name = creature_id + "-pack.zip"
    return Raw(creaturepack.zip_bytes(files), "application/zip", {"Content-Disposition": f'attachment; filename="{name}"'})


@route("GET", "/api/export.zip")
def export_zip(q, body):
    ids = q.get("ids", [""])[0]
    wanted = [i["id"] for i in visible_library()] if ids == "all" else [i for i in ids.split(",") if i]
    if not wanted:
        raise ApiError(400, "Nothing to export")
    return Raw(current_app().lib.export_zip(wanted), "application/zip", {"Content-Disposition": 'attachment; filename="npcs.zip"'})


@route("GET", "/api/npcs/([a-z0-9-]+)/export/([a-z0-9_]+)/([a-z0-9_]+)\\.zip")
def export_animation(q, body, npc_id, clip_id, preset_id):
    """A finished animation as an engine-ready ZIP (sheet(s), atlas JSON, engine resource, preview page)."""
    npc = load_npc(npc_id)

    def one(name, default=None):
        return q.get(name, [default])[0]
    options = {"dirs": [d for d in one("dirs", "").split(",") if d], "name": one("name"), "res_path": one("res_path")}
    try:
        for key in ("frame", "colors"):
            if one(key):
                options[key] = int(one(key))
        if one("pixelate") in ("0", "1"):
            options["pixelate"] = one("pixelate") == "1"
        options["outline"] = one("outline") == "1"
        files = export.build(current_app().lib, npc, clip_id, preset_id, options)
    except ValueError as e:
        raise ApiError(400, str(e)) from None
    stem = export.slugify(f'{npc["profile"]["first_name"]}-{npc["profile"]["last_name"]}')
    return Raw(export.to_zip(files), "application/zip", {"Content-Disposition": f'attachment; filename="{stem}-{clip_id}-{preset_id}.zip"'})


@route("GET", "/api/npcs/([a-z0-9-]+)/sheet\\.zip")
def export_sheet(q, body, npc_id):
    """A finished model sheet as a ZIP: one PNG per view, the stitched sheet and a JSON record."""
    npc = load_npc(npc_id)
    try:
        files = export.build_sheet(current_app().lib, npc, {"name": q.get("name", [None])[0]})
    except ValueError as e:
        raise ApiError(400, str(e)) from None
    stem = export.slugify(f'{npc["profile"]["first_name"]}-{npc["profile"]["last_name"]}')
    return Raw(export.to_zip(files), "application/zip", {"Content-Disposition": f'attachment; filename="{stem}-model-sheet.zip"'})


@route("POST", "/api/generate")
def generate(q, body):
    data = roll.load_data()
    assets = clean_assets(body.get("assets"), data)
    app = current_app()
    if assets.get("import_3d"):                              # look inside the file now: a bad one is refused before anything is queued
        if not body.get("npc_id"):
            raise ApiError(400, "A 3D model is imported into one existing character")
        try:
            assets["import_3d"] = import3d.prepare(app.comfy, assets["import_3d"])
        except ValueError as e:
            raise ApiError(400, str(e)) from None
    if assets.get("body_3d"):                                # settle the recipe now: a wrong one is refused before anything is queued
        if not body.get("npc_id"):
            raise ApiError(400, "A designed body is made for one existing character")
        try:
            assets["body_3d"] = bodymaker.prepare(app.lib, load_npc(body["npc_id"]), assets["body_3d"])
        except ValueError as e:
            raise ApiError(400, str(e)) from None
    options = clean_options(body.get("options"), app.cfg)
    style_value = body.get("style")
    style = style_value if isinstance(style_value, str) and style_value in data["styles"] else None
    npcs = []
    morphology_notice = None
    if body.get("npc_id"):
        existing = check_npc(load_npc(body["npc_id"]))     # 404 if it does not exist
        reason = morphology.unsupported_reason(existing.get("morphology"), assets)
        if reason:
            raise ApiError(400, reason)

        def apply(npc):
            npc["morphology"] = existing["morphology"]
            if style and style != npc.get("style"):
                npc["style"] = style
                jobsmod.mark_assets_stale(npc)
        npcs.append(app.lib.update(body["npc_id"], apply))
    elif body.get("npc_ids"):                                 # several existing NPCs, e.g. the library selection
        if not isinstance(body["npc_ids"], list):
            raise ApiError(400, "npc_ids must be a list")
        ids = list(dict.fromkeys(i for i in body["npc_ids"] if isinstance(i, str)))[:200]
        existing = [check_npc(load_npc(npc_id)) for npc_id in ids]   # validate every member before changing records
        for npc in existing:
            reason = morphology.unsupported_reason(npc.get("morphology"), assets)
            if reason:
                raise ApiError(400, f'{npc["profile"].get("first_name", "NPC")}: {reason}')
        for npc_id, known in zip(ids, existing):

            def apply(npc):
                npc["morphology"] = known["morphology"]
                if style and style != npc.get("style"):
                    npc["style"] = style
                    jobsmod.mark_assets_stale(npc)
            npcs.append(app.lib.update(npc_id, apply))
    elif body.get("npc"):
        if not isinstance(body["npc"], dict):
            raise ApiError(400, "npc must be an object")
        draft = dict(body["npc"])
        draft["style"] = style or draft.get("style")
        draft = validated(data, draft)
        reason = morphology.unsupported_reason(draft.get("morphology"), assets)
        if reason:
            raise ApiError(400, reason)
        npcs.append(app.lib.create(draft))
    else:
        setting = body.get("setting")
        if not isinstance(setting, str) or setting not in data["settings"]:
            raise ApiError(400, "Unknown setting")
        constraints = body.get("constraints") or {}
        if not isinstance(constraints, dict):
            raise ApiError(400, "constraints must be an object")
        locked = body.get("locked") or {}                     # the Advanced options pre-sets: every NPC of the batch gets them
        if not isinstance(locked, dict):
            raise ApiError(400, "locked must be an object")
        if (morphology.needs_full_body(assets) and "races" in data["settings"][setting]
                and not constraints.get("morphology") and not constraints.get("origin")
                and not locked.get("profile.race") and not locked.get("morphology")):
            # A random full-body batch is kept on the supported path; users can still request taur portraits by
            # choosing a species, or explicitly select a body plan and receive the support warning below.
            constraints = {**constraints, "morphology": "bipedal"}
            morphology_notice = "This batch is limited to two-legged characters because the other body plans have not been checked for full-body characters yet."
        try:
            rolled = [roll.roll_npc(data, setting, constraints, locked) for _ in range(max(1, min(int(body.get("count") or 1), 50)))]
        except (ValueError, TypeError) as e:
            raise ApiError(400, f"Could not roll with those settings: {e}") from None
        for npc in rolled:
            npc["style"] = style or next(iter(data["styles"]))
            reason = morphology.unsupported_reason(npc.get("morphology"), assets)
            if reason:
                raise ApiError(400, reason)
            npcs.append(app.lib.create(check_npc(npc)))
    response = {"npc_ids": [n["id"] for n in npcs], "jobs": submit_all(npcs, assets, options)}
    if morphology_notice:
        response["warnings"] = [morphology_notice]
    return response


# ---- items --------------------------------------------------------------------------------------------------------------------------
def load_item(item_id):
    try:
        return current_app().items.get(item_id)
    except KeyError:
        raise ApiError(404, f"No item with id {item_id}") from None


@route("GET", "/api/items")
def list_items(q, body):
    return current_app().items.list()


@route("POST", "/api/items")
def create_item(q, body):
    try:
        fields = items.clean_item(body)
    except ValueError as e:
        raise ApiError(400, str(e)) from None
    return current_app().items.create(fields)


@route("GET", "/api/items/([a-z0-9-]+)")
def get_item(q, body, item_id):
    return load_item(item_id)


@route("PUT", "/api/items/([a-z0-9-]+)")
def update_item(q, body, item_id):
    load_item(item_id)
    try:
        fields = items.clean_item(body)
        return current_app().items.edit(item_id, fields)
    except ValueError as e:
        raise ApiError(400, str(e)) from None


@route("DELETE", "/api/items/([a-z0-9-]+)")
def delete_item(q, body, item_id):
    load_item(item_id)
    app = current_app()
    if app.jobs.busy_item(item_id):
        raise ApiError(409, "This item is being made: cancel its job first")
    app.items.delete(item_id)
    app.dressings.forget_item(item_id)
    return {"deleted": item_id}


@route("DELETE", "/api/items/([a-z0-9-]+)/assets/(hero|views|high|model_3d)")
def delete_item_asset(q, body, item_id, kind):
    load_item(item_id)
    app = current_app()
    if app.jobs.busy_item(item_id):
        raise ApiError(409, "This item is being made: cancel its job first")
    try:
        return app.items.delete_asset(item_id, kind)
    except KeyError:
        raise ApiError(404, "That part has not been made") from None


@route("POST", "/api/items/recipe-preview")
def preview_recipe(q, body):
    """The GLB of glasses or of a garment's pattern from a recipe, without saving anything (the editor's live view): body {"family", "kind", "recipe", "faces"}."""
    try:
        fields = items.clean_item({**(body or {}), "family": (body or {}).get("family") or "eyewear"})
        made = items.recipe_maker(fields["family"])
        if made is None:
            raise ValueError("Only glasses and fitted clothes are built from a recipe")
        glb, info = made.build(fields["recipe"], faces=fields["faces"])
    except ValueError as e:
        raise ApiError(400, str(e)) from None
    return Raw(glb, "model/gltf-binary", {"Cache-Control": "no-store", "X-Item-Info": json.dumps(info, separators=(",", ":"))})


@route("POST", "/api/items/recipe-from-text")
def recipe_from_text(q, body):
    """The designer's controls set from a few words (glasses or fitted clothes): body {"text", "family", "kind", "recipe"} -> {"recipe": the whole recipe, "changed": [fields], "understood": [...]}."""
    body = body or {}
    text = body.get("text")
    if not isinstance(text, str) or len(text) > items.DESCRIPTION_LIMIT:
        raise ApiError(400, f"Give the words as text of at most {items.DESCRIPTION_LIMIT} characters")
    try:
        current = items.clean_item({"family": body.get("family") or "eyewear", "kind": body.get("kind"), "recipe": body.get("recipe")}).get("recipe")
        made = items.recipe_maker(body.get("family") or "eyewear")
        if current is None or made is None:
            raise ValueError("Only glasses and fitted clothes are built from a recipe")
        changes, said = made.from_description(text, current)
        merged = made.clean_recipe({**current, **changes})
    except ValueError as e:
        raise ApiError(400, str(e)) from None
    return {"recipe": merged, "changed": sorted(changes), "understood": said}


def _scanned_face(npc_id):
    """The facefit.Scan of a character's face, or the API's answer that there is none yet."""
    try:
        scan = current_app().faces.load(npc_id)
    except KeyError:
        raise ApiError(404, f"No NPC with id {npc_id}") from None
    if scan is None:
        raise ApiError(409, "This character's face has not been scanned yet: find the face first")
    return facefit.Scan(scan)


@route("GET", "/api/npcs/([a-z0-9-]+)/body-3d")
def body_status(q, body, npc_id):
    """What the designed-body card needs for this character: whether the Body tab saved a body for it, the base that suits its gender, and what its
    3D model was designed from (or null)."""
    return bodymaker.status(current_app().lib, load_npc(npc_id))


@route("POST", "/api/npcs/([a-z0-9-]+)/body-3d/colours")
def body_colours(q, body, npc_id):
    """Put new colours on the garments a designed body wears without making the body again: body {"colours": {"shirt": "#3f6ba8", ...}} (only garments it wears). Both of the character's
    files get the new cloth and the card's picture is drawn again (a few seconds). Returns {"changed", "clothes", "preview"}. What the Items tab kept for this body is out of date afterwards."""
    load_npc(npc_id)
    app = current_app()
    if any(job["npc_id"] == npc_id and job["status"] in ("queued", "running") for job in app.jobs.snapshot()):
        raise ApiError(409, "This character has work waiting or running: change the colours when it is done")
    try:
        return bodymaker.recolour(app.cfg, app.lib, npc_id, (body or {}).get("colours"))
    except (ValueError, bodymaker.BodyError) as e:
        raise ApiError(400, str(e)) from None


@route("GET", "/api/npcs/([a-z0-9-]+)/face")
def face_status(q, body, npc_id):
    """Whether this character's face has been scanned (to fit glasses to it), what is happening now, and a summary of the scan."""
    load_npc(npc_id)
    return current_app().faces.status(npc_id)


@route("POST", "/api/npcs/([a-z0-9-]+)/face/scan")
def face_scan(q, body, npc_id):
    """Scan this character's face on a thread of its own (about 40 seconds): body {"force": bool, "estimate": bool}. With no force a scan
    that is already there for the character's model is kept. Returns the status; the page asks for it again until it is done."""
    load_npc(npc_id)
    try:
        return current_app().faces.request(npc_id, force=bool(body.get("force")), estimate=bool(body.get("estimate")))
    except facescan.FaceScanError as e:
        raise ApiError(400, str(e)) from None


@route("DELETE", "/api/npcs/([a-z0-9-]+)/face/scan")
def face_scan_cancel(q, body, npc_id):
    """Stop this character's face scan."""
    load_npc(npc_id)
    return current_app().faces.cancel(npc_id)


@route("POST", "/api/items/([a-z0-9-]+)/fit")
def fit_item(q, body, item_id):
    """Where a pair of glasses goes on a character's face: body {"npc": id, "nudge": {up, forward, tilt, size}}; the saved fit's nudge when none is
    sent. Returns the pair's position, rotation and scale in the character's own space, a report, and the saved fit if there is one."""
    load_item(item_id)
    npc_id = body.get("npc")
    if not isinstance(npc_id, str):
        raise ApiError(400, "Say which character to fit them to: {\"npc\": its id}")
    load_npc(npc_id)
    scan = _scanned_face(npc_id)
    try:
        return {"npc": npc_id, **current_app().items.fit(item_id, npc_id, scan, body.get("nudge"))}
    except ValueError as e:
        raise ApiError(400, str(e)) from None


@route("PUT", "/api/items/([a-z0-9-]+)/fits/([a-z0-9-]+)")
def save_item_fit(q, body, item_id, npc_id):
    """Keep a nudge as this pair's fit on a character: body {"nudge": {...}}. Returns the item."""
    load_item(item_id)
    load_npc(npc_id)
    try:
        return current_app().items.save_fit(item_id, npc_id, body.get("nudge"), (current_app().faces.load(npc_id) or {}).get("source"))
    except ValueError as e:
        raise ApiError(400, str(e)) from None


@route("DELETE", "/api/items/([a-z0-9-]+)/fits/([a-z0-9-]+)")
def forget_item_fit(q, body, item_id, npc_id):
    load_item(item_id)
    try:
        return current_app().items.forget_fit(item_id, npc_id)
    except KeyError:
        raise ApiError(404, "No fit is saved for that character") from None


@route("POST", "/api/items/([a-z0-9-]+)/dress")
def dress_item(q, body, item_id):
    """Put fitted clothes on a designed body: body {"npc": id, "force": bool}. The fit takes about half a minute, so it runs on a thread of its own and this returns its status at
    once; the page asks for the status again until it is done. With no force, a fit that is kept and still good for this garment and this body is left as it is."""
    load_item(item_id)
    npc_id = (body or {}).get("npc")
    if not isinstance(npc_id, str):
        raise ApiError(400, "Say which character to put it on: {\"npc\": its id}")
    load_npc(npc_id)
    try:
        return current_app().dressings.request(item_id, npc_id, force=bool(body.get("force")))
    except ValueError as e:
        raise ApiError(400, str(e)) from None


@route("GET", "/api/items/([a-z0-9-]+)/dress/([a-z0-9-]+)")
def dress_status(q, body, item_id, npc_id):
    """Where the fit of this garment to this character is: none, queued, running, done (with what was kept), error or cancelled."""
    load_item(item_id)
    load_npc(npc_id)
    return current_app().dressings.status(item_id, npc_id)


@route("DELETE", "/api/items/([a-z0-9-]+)/dress/([a-z0-9-]+)")
def dress_cancel(q, body, item_id, npc_id):
    """Stop this fit, whether it is waiting or running."""
    load_item(item_id)
    load_npc(npc_id)
    return current_app().dressings.cancel(item_id, npc_id)


@route("GET", "/api/items/([a-z0-9-]+)/worn/([a-z0-9-]+)\\.glb")
def worn_item(q, body, item_id, npc_id):
    """The character's model with the item on it as a GLB. Glasses: under the head joint, at the saved fit, or at the nudge in the query (up and forward in millimetres, tilt in degrees,
    size as a multiplier) when any is given. Fitted clothes: the fit kept for this character (409 when there is none yet), with the item's colour and cloth if they were changed after it."""
    item = load_item(item_id)
    load_npc(npc_id)
    if items.maker(item.get("family")) == "garment":
        try:
            glb, info = current_app().dressings.worn(item_id, npc_id)
        except ValueError as e:
            raise ApiError(409, str(e)) from None
        return Raw(glb, "model/gltf-binary", {"Content-Disposition": f'attachment; filename="{npc_id}-{item_id}.glb"', "Cache-Control": "no-store",
                                              "X-Dress": json.dumps({"stale": info["stale"], "recoloured": info["recoloured"]}, separators=(",", ":"))})
    if current_app().items.equip_how(item):
        return _equipped_response(item_id, npc_id, q)
    scan = _scanned_face(npc_id)
    nudge = {}
    for key in facefit.NUDGE_DEFAULT:
        if key in q:
            try:
                nudge[key] = float(q[key][0])
            except ValueError:
                raise ApiError(400, f"The nudge {key} must be a number") from None
    try:
        glb, info = current_app().items.worn(item_id, npc_id, scan, nudge or None)
    except ValueError as e:
        raise ApiError(400, str(e)) from None
    return Raw(glb, "model/gltf-binary", {"Content-Disposition": f'attachment; filename="{npc_id}-{item_id}.glb"', "Cache-Control": "no-store",
                                          "X-Fit": json.dumps(info["attach"], separators=(",", ":"))})


def _equip_query(q):
    """The slot and the nudge a request names in its query (`slot`, and `up`, `forward` and `side` in metres, `turn` and `tilt` in degrees, `size` as a multiplier):
    (slot or None, nudge dict or None when it names none)."""
    nudge = {}
    for key in equip.NUDGE_DEFAULT:
        if key in q:
            try:
                nudge[key] = float(q[key][0])
            except ValueError:
                raise ApiError(400, f"The nudge {key} must be a number") from None
    return (q.get("slot", [""])[0] or None), (nudge or None)


def _equip_header(report):
    """What a game needs of an equip, as the X-Equip header: for each item its slot, bone, scale and the attach block."""
    return json.dumps({"items": [{k: v for k, v in entry.items() if k != "nudge"} for entry in report["items"]], "notes": report["notes"], "model": report["model"]},
                      separators=(",", ":"))


def _equipped_response(item_id, npc_id, q):
    slot, nudge = _equip_query(q)
    try:
        glb, report = current_app().items.equipped(npc_id, [{"item": item_id, "slot": slot, "nudge": nudge}])
    except ValueError as e:
        raise ApiError(400, str(e)) from None
    return Raw(glb, "model/gltf-binary", {"Content-Disposition": f'attachment; filename="{npc_id}-{item_id}.glb"', "Cache-Control": "no-store",
                                          "X-Equip": _equip_header(report)})


@route("POST", "/api/items/([a-z0-9-]+)/equip")
def equip_item(q, body, item_id):
    """Where a held item or a hat goes on a character, worked out without building a file: body {"npc": id, "slot": "hand_r" | "hand_l" | "head", "nudge": {...}}; slot and nudge
    default to what is kept for that character, else the kind's usual slot and the neutral nudge. Answers {"slot", "bone", "attach": {bone, translation, rotation, scale}, "scale",
    "nudge", "saved", "notes", "model"}: the attach block is the transform of the item's origin in the bone's own space, which is all a game needs to hang the item itself."""
    load_item(item_id)
    npc_id = (body or {}).get("npc")
    if not isinstance(npc_id, str):
        raise ApiError(400, "Say which character: {\"npc\": its id}")
    load_npc(npc_id)
    try:
        return current_app().items.equip_placement(item_id, npc_id, body.get("slot") or None, body.get("nudge"))
    except ValueError as e:
        raise ApiError(400, str(e)) from None


@route("PUT", "/api/items/([a-z0-9-]+)/equips/([a-z0-9-]+)")
def save_item_equip(q, body, item_id, npc_id):
    """Keep a slot and nudge as this item's way of being carried or worn by a character: body {"slot", "nudge"}. Nothing is put on the character; returns the item."""
    load_item(item_id)
    load_npc(npc_id)
    try:
        return current_app().items.save_equip(item_id, npc_id, (body or {}).get("slot") or None, (body or {}).get("nudge"))
    except ValueError as e:
        raise ApiError(400, str(e)) from None


@route("DELETE", "/api/items/([a-z0-9-]+)/equips/([a-z0-9-]+)")
def forget_item_equip(q, body, item_id, npc_id):
    load_item(item_id)
    try:
        return current_app().items.forget_equip(item_id, npc_id)
    except KeyError:
        raise ApiError(404, "Nothing is kept for that character") from None


@route("GET", "/api/npcs/([a-z0-9-]+)/equipped\\.glb")
def equipped_npc(q, body, npc_id):
    """The character's model with several items on it, each at what is kept for this character: ?items=sword,shield:hand_l,hat (an item's id, then optionally :slot). A slot
    can carry one item: two for the same slot are refused (409 is for a character without a skeleton)."""
    load_npc(npc_id)
    parts = []
    for token in (q.get("items", [""])[0] or "").split(","):
        token = token.strip()
        if not token:
            continue
        item_id, _, slot = token.partition(":")
        if not re.fullmatch(r"[a-z0-9-]+", item_id):
            raise ApiError(400, f"Not an item id: {item_id}")
        load_item(item_id)
        parts.append({"item": item_id, "slot": slot or None})
    if not parts:
        raise ApiError(400, "Name the items: ?items=<id>[:slot],<id>[:slot]")
    try:
        glb, report = current_app().items.equipped(npc_id, parts)
    except equip.EquipError as e:
        raise ApiError(409 if "skeleton" in str(e) else 400, str(e)) from None
    except ValueError as e:
        raise ApiError(400, str(e)) from None
    return Raw(glb, "model/gltf-binary", {"Content-Disposition": f'attachment; filename="{npc_id}-equipped.glb"', "Cache-Control": "no-store", "X-Equip": _equip_header(report)})


@route("POST", "/api/items/([a-z0-9-]+)/make")
def make_item(q, body, item_id):
    """Queue the steps an item needs: body {"request": {"make", "finish", "redo"}, "options": {...}} (npcgen/items.py). An item built from a
    recipe (glasses) has no steps: its model is built again on the spot and its picture drawn again, and the item comes back."""
    item = load_item(item_id)
    app = current_app()
    if items.is_recipe(item):
        return {"job": None, "item": app.items.build_recipe(item_id)}
    if app.jobs.busy_item(item_id):
        raise ApiError(409, "This item is already being made")
    try:
        options = items.clean_options(body.get("options"), app.cfg)
        return {"job": app.jobs.submit_item(item_id, body.get("request"), options)}
    except ValueError as e:
        raise ApiError(400, str(e)) from None


@route("GET", "/api/items/([a-z0-9-]+)/files/(.+)")
def item_file(q, body, item_id, rel):
    try:
        path = current_app().items.resolve(item_id, rel)
    except KeyError:
        raise ApiError(404, "Not found") from None
    if not path.is_file() or path.suffix.lower() not in FILE_TYPES or path.name == "item.json":
        raise ApiError(404, "Not found")
    stat = path.stat()
    return Raw(atomic.read_bytes(path), FILE_TYPES[path.suffix.lower()], {"Cache-Control": "no-cache"},
               etag=f'"{stat.st_mtime_ns:x}-{stat.st_size:x}"')


@route("GET", "/api/items/([a-z0-9-]+)/export\\.zip")
def export_item(q, body, item_id):
    """One item as a ZIP: its record, pictures and 3D model."""
    load_item(item_id)
    return Raw(current_app().items.export_zip(item_id), "application/zip", {"Content-Disposition": f'attachment; filename="{item_id}.zip"'})


@route("GET", "/api/import-3d/sources")
def import_sources(q, body):
    """The GLB files ComfyUI can see (its output and input folders), newest first, for the Import a 3D model list."""
    comfy = current_app().comfy
    if not comfy.health().get("online"):
        raise ApiError(502, "ComfyUI is not running, so its folders cannot be listed")
    return {"sources": import3d.list_sources(comfy), "missing": three_d.import_availability(comfy)["missing"]}


@route("GET", "/api/import-3d/inspect")
def import_inspect(q, body):
    """What is inside one of those files, read from its first bytes: faces, whether it has colour, whether it stands up,
    and whether it would be closed and simplified by default."""
    try:
        model_file, info = import3d.resolve(current_app().comfy, q.get("model_file", [""])[0])
    except ValueError as e:
        raise ApiError(400, str(e)) from None
    return {**info, "model_file": model_file, "clean": import3d.default_clean(info)}


@route("GET", "/api/jobs")
def list_jobs(q, body):
    return current_app().jobs.snapshot()


@route("GET", "/api/gpu")
def gpu_check(q, body):
    """Picture-job readiness, One model at a time and the suite GPU lock. Use `?lock=1` to read just the lock and the switch."""
    suite_lock = fels_suite.gpu_status()
    for entry in [*suite_lock["holders"], suite_lock["alone"]]:
        if entry is not None:
            entry["name"] = fels_suite.short_name(entry["app"])
    if q.get("lock") == ["1"]:
        return {"suite_lock": suite_lock, **_switch_state()}
    app = current_app()
    with app.jobs.lock:
        busy = app.jobs.current is not None
    return {**gpu.check(app.comfy, forge_busy=busy), **_switch_state(), "suite_lock": suite_lock}


def _switch_state():
    """One model at a time as the page shows it: the switch, and whether Forge holds the GPU alone now."""
    rule = gpu.status()
    return {"one_model_at_a_time": rule["switch"], "alone": rule["alone"],
            "unload_external_models": rule["unload_external_models"],
            "switch_applies": "now" if rule["switch"] == rule["one_model_at_a_time"] else "next_job"}


@route("PUT", "/api/gpu/settings")
def gpu_settings(q, body):
    """Save the suite coordination switch or the separate permission to unload external programs."""
    key = next(iter(body)) if isinstance(body, dict) and len(body) == 1 else None
    if key not in {"one_model_at_a_time", "unload_external_models"}:
        raise ApiError(400, "Choose one GPU setting to change.")
    value = body[key]
    if type(value) is not bool:
        raise ApiError(400, f"{key} must be true or false.")
    app = current_app()
    try:
        with app.jobs.lock:
            cfgmod.save_gpu_setting(key, value)
            app.cfg.setdefault("gpu", {})[key] = value
            if key == "unload_external_models":
                gpu.GATE.settings[key] = value
            else:
                busy = app.jobs.current is not None or not app.jobs.queue.empty()
                if gpu.GATE.switch(value, busy) and gpu.GATE.held_the_other_way():
                    threading.Thread(target=gpu.release_comfy, args=(app.comfy,), name="GPU switch", daemon=True).start()
    except (OSError, ValueError) as error:
        raise ApiError(500, f"NPC Forge could not save the switch in config.json ({error}). Check that the NPC Forge folder "
                            "can be written to, then try again.") from None
    return _switch_state()


# ---- Set up ComfyUI (npcgen/comfy_setup.py, docs/comfyui-setup.md) -----------------------------------------------------------
def _setup_place(app):
    return comfy_setup.locate(app.comfy, app.cfg.get("comfy_folder"))


def _setup_error(error):
    return ApiError(error.status, str(error))


@route("GET", "/api/comfy-setup")
def comfy_setup_status(q, body):
    """Where ComfyUI is, what each tier needs, what is there already and the install in progress. `?progress=1`: just the
    install, for polling while it runs."""
    app = current_app()
    if q.get("progress") == ["1"]:
        return {"install": app.comfy_setup.snapshot()}
    return comfy_setup.status(app.comfy, _setup_place(app), app.comfy.models, hardware.your_pc()["tier"], app.comfy_setup)


@route("POST", "/api/comfy-setup/folder")
def comfy_setup_folder(q, body):
    """The ComfyUI folder Set up ComfyUI uses when ComfyUI is not running: {"folder": "D:/ComfyUI"}, {"pick": true} to choose it
    with this computer's own folder picker (refused when NPC Forge needs a password, as the picker would open on the server's
    screen), or {"folder": null} to forget it. Saved in config.json as "comfy_folder"."""
    app = current_app()
    folder = body.get("folder")
    if body.get("pick") is True:
        if getattr(app, "password_needed", False):
            raise ApiError(403, "The folder picker opens on the computer NPC Forge runs on, so it is not offered over the network. "
                                "Type the folder under Details instead.")
        try:
            folder = comfy_setup.pick_folder()
        except comfy_setup.SetupError as error:
            raise _setup_error(error) from None
        if folder is None:
            return {"folder": app.cfg.get("comfy_folder"), "changed": False}
    elif folder is not None and not isinstance(folder, str):
        raise ApiError(400, "folder must be the ComfyUI folder, or null to forget it.")
    try:
        value = None if folder is None else str(comfy_setup.comfy_folder(folder))
        cfgmod.save_setting("comfy_folder", value)
    except comfy_setup.SetupError as error:
        raise _setup_error(error) from None
    except (OSError, ValueError) as error:
        raise ApiError(500, f"NPC Forge could not save the folder in config.json ({error}). Check that the NPC Forge folder can be "
                            "written to, then try again.") from None
    if value is None:
        app.cfg.pop("comfy_folder", None)
    else:
        app.cfg["comfy_folder"] = value
    return {"folder": value, "changed": True}


@route("POST", "/api/comfy-setup/install")
def comfy_setup_install(q, body):
    """Install the parts in {"parts": ["image:zimage", ...], "yes": true}: check the files already there against their pinned
    SHA-256, download what is missing from its pinned Hugging Face commit, and copy NPC Forge's own nodes into custom_nodes.
    Nothing happens without "yes": true. Answers the install as it starts; GET /api/comfy-setup?progress=1 follows it."""
    parts = body.get("parts")
    if not isinstance(parts, list) or not all(isinstance(p, str) for p in parts):
        raise ApiError(400, "parts must be a list of part ids, as GET /api/comfy-setup lists them.")
    app = current_app()
    try:
        return {"install": app.comfy_setup.start(_setup_place(app), parts, app.comfy.models, app.comfy, yes=body.get("yes"))}
    except comfy_setup.SetupError as error:
        raise _setup_error(error) from None


@route("POST", "/api/comfy-setup/stop")
def comfy_setup_stop(q, body):
    """Stop the install. What arrived of a download is kept, and the next install carries on from there."""
    return {"install": current_app().comfy_setup.stop()}


@route("POST", "/api/gpu/free-voice-lab")
def free_voice_lab(q, body):
    """Ask the current Voice Lab GPU holder to unload after the user requests it in the picture queue."""
    if gpu.GATE.strict:
        raise ApiError(409, "NPC Forge makes room for pictures automatically with One model at a time on.")
    status, answer = suite_gpu.ask_voice_lab()
    if status != 200:
        raise ApiError(status, answer["message"])
    return answer


@route("POST", "/api/jobs/cancel-all")
def cancel_all(q, body):
    current_app().jobs.cancel()
    return {"ok": True}


@route("POST", "/api/jobs/([a-f0-9]+)/cancel")
def cancel_job(q, body, job_id):
    current_app().jobs.cancel(job_id)
    return {"ok": True}


@route("POST", "/api/jobs/([a-f0-9]+)/retry")
def retry_job(q, body, job_id):
    try:
        return current_app().jobs.retry(job_id)
    except KeyError:
        raise ApiError(404, "No job with that id") from None
    except ValueError as e:
        raise ApiError(409, str(e)) from None


# ---- static files ------------------------------------------------------------------------------------------------------
def _serve(root, rel):
    root = pathlib.Path(root).resolve()
    target = (root / rel).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        raise ApiError(404, "Not found")
    ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    return Raw(target.read_bytes(), ctype, {"Cache-Control": "no-store"})


def static_response(path):
    for prefix, root in MOUNTS.items():                       # pages added by local extensions
        if path.startswith(prefix):
            if not _shown(prefix.split("/")[2]):
                raise ApiError(404, "Not found")
            return _serve(root, path[len(prefix):] or "index.html")
    return _serve(WEB, "index.html" if path in ("/", "") else path.lstrip("/"))


# ---- HTTP plumbing ---------------------------------------------------------------------------------------------------------
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _loopback(host):
    if str(host).lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _auth_header(token):
    encoded = base64.b64encode(f"npcforge:{token}".encode("ascii")).decode("ascii")
    return "Basic " + encoded


class NPCServer(ThreadingHTTPServer):
    """An HTTP server owns exactly one App and shuts its worker down with the socket."""
    daemon_threads = True

    def __init__(self, address, app, require_auth=False):
        self.npc_app = app
        self.require_auth = require_auth
        app.password_needed = bool(require_auth)        # what only the computer NPC Forge runs on may do (its own folder picker)
        self._app_closed = False
        super().__init__(address, Handler)

    def server_close(self):
        try:
            super().server_close()
        finally:
            if not self._app_closed:
                self._app_closed = True
                self.npc_app.close()


def create_server(address, app=None, require_auth=None):
    """Application factory: build an isolated App + HTTP server, with no import-time worker or library singleton."""
    app = app or App()
    external = not _loopback(address[0])
    # The caller may require auth on loopback (e.g. tests), but may never disable it on a network bind.
    protected = external or bool(require_auth)
    token = app.cfg.get("auth_token")
    if protected:
        if not isinstance(token, str) or len(token) < 16:
            app.close()
            raise ValueError("Non-loopback binding requires NPCGEN_AUTH_TOKEN with at least 16 characters")
        try:
            token.encode("ascii")
        except UnicodeEncodeError:
            app.close()
            raise ValueError("NPCGEN_AUTH_TOKEN must contain ASCII characters only") from None
    try:
        return NPCServer(address, app, protected)
    except Exception:
        app.close()
        raise


MAX_BODY = 2_000_000       # largest request body the API reads
MODEL_UPLOAD = re.compile(r"/api/npcs/[a-z0-9-]+/model-upload")
MODEL_UPLOAD_TYPE = "application/x-npcforge-glb"
MAX_MODEL_BODY = 320 << 20  # a .glb the owner uploads as a character's model (a raw generator shape is bigger: it comes in through ComfyUI's folders, npcgen/import3d.py)
MAX_VIDEO_BODY = scene_recording.MAX_FRAMES_BODY
MAX_DRAIN = 16_000_000     # largest refused body read and thrown away so the error reply arrives (Handler._drain_body)


class Handler(BaseHTTPRequestHandler):
    server_version = "NPCForge/1.0"

    def log_request(self, code="-", size="-"):
        if code == 200 and self.command == "GET" and self.path == "/api/gpu?lock=1":
            return
        super().log_request(code, size)

    def log_message(self, fmt, *args):
        line = fmt % args                                    # args are not always strings (send_error passes an HTTPStatus)
        if "/api/jobs" not in line:                          # the UI polls the queue constantly; keep the log readable
            sys.stderr.write("%s - %s\n" % (self.address_string(), line))

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PUT(self):
        self._dispatch("PUT")

    def do_DELETE(self):
        self._dispatch("DELETE")

    def _guard(self, method):
        """This server can spend GPU time and delete files, so refuse requests that did not come from our own page."""
        host_header = self.headers.get("Host") or ""
        try:
            hostname = urllib.parse.urlsplit("//" + host_header).hostname or ""
        except ValueError:
            hostname = ""
        if self.server.require_auth:
            supplied = self.headers.get("Authorization") or ""
            expected = _auth_header(self.server.npc_app.cfg["auth_token"])
            if not hmac.compare_digest(supplied, expected):
                raise ApiError(401, "Authentication required", {"WWW-Authenticate": 'Basic realm="NPC Forge", charset="UTF-8"'})
        elif hostname not in LOCAL_HOSTS:
            raise ApiError(403, "Forbidden host")
        origin = self.headers.get("Origin")
        if origin and urllib.parse.urlparse(origin).netloc != host_header:
            raise ApiError(403, "Cross-origin requests are not allowed")
        media = method == "POST" and re.fullmatch(r"/api/scenes/scene-[a-f0-9]{10}/frames", self.path)
        model = method == "POST" and MODEL_UPLOAD.fullmatch(urllib.parse.urlsplit(self.path).path)
        if method != "GET" and not ("application/json" in (self.headers.get("Content-Type") or "")
                                    or (media and "application/x-npcforge-frames" in (self.headers.get("Content-Type") or ""))
                                    or (model and MODEL_UPLOAD_TYPE in (self.headers.get("Content-Type") or ""))):
            raise ApiError(415, "Content-Type must be application/json")

    def _dispatch(self, method):
        token = _CURRENT_APP.set(self.server.npc_app)
        self._body_read = False              # until the body is read; _send drains it otherwise
        try:
            self._guard(method)
            parsed = urllib.parse.urlparse(self.path)
            path = urllib.parse.unquote(parsed.path)
            query = urllib.parse.parse_qs(parsed.query)
            body = {}
            if method in ("POST", "PUT"):
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                except ValueError:
                    raise ApiError(400, "Content-Length must be a non-negative number") from None
                if length < 0:
                    raise ApiError(400, "Content-Length must be a non-negative number")
                media = method == "POST" and re.fullmatch(r"/api/scenes/scene-[a-f0-9]{10}/frames", path)
                sound_upload = method == 'POST' and re.fullmatch(r'/api/scenes/scene-[a-f0-9]{10}/media', path)
                model_upload = method == "POST" and MODEL_UPLOAD.fullmatch(path)
                if length > (MAX_VIDEO_BODY if media else MAX_MODEL_BODY if model_upload else 8_000_000 if sound_upload else MAX_BODY):
                    raise ApiError(413, "Request too large")
                raw = self.rfile.read(length) if length else b""
                self._body_read = True
                if media:
                    try:
                        revision = int(self.headers.get("X-Scene-Revision", ""))
                    except ValueError:
                        raise ApiError(400, "Missing scene revision") from None
                    fingerprint = self.headers.get("X-Scene-Fingerprint") or ""
                    if not re.fullmatch(r"[a-f0-9]{64}", fingerprint):
                        raise ApiError(400, "Missing scene fingerprint")
                    body = {"revision": revision, "fingerprint": fingerprint, "frames": raw}
                elif model_upload and MODEL_UPLOAD_TYPE in (self.headers.get("Content-Type") or ""):
                    body = {"glb": raw}
                else:
                    try:
                        body = json.loads(raw) if raw else {}
                    except ValueError:
                        raise ApiError(400, "Body is not valid JSON") from None
                if not isinstance(body, dict):
                    raise ApiError(400, "Body must be a JSON object")
            if not path.startswith("/api/"):
                if method != "GET":
                    raise ApiError(405, "Method not allowed")
                if path == "/favicon.ico":                      # the page names its icon (/icon.svg); stop browsers asking for this one
                    return self._send(Raw(b"", "image/x-icon", status=204))
                return self._send(static_response(path))
            for m, rx, fn in ROUTES:
                match = rx.match(path)
                if match and m == method:
                    if fn is download_card and self.headers.get("Sec-Fetch-Site") == "cross-site":
                        raise ApiError(403, "Open NPC Forge directly to export a character card.")
                    return self._send(fn(query, body, *match.groups()))
            allowed = any(rx.match(path) for _, rx, _ in ROUTES)
            raise ApiError(405 if allowed else 404, "Method not allowed" if allowed else "Unknown API endpoint")
        except ApiError as e:
            self._send_json({"error": e.message}, e.status, e.headers)
        except ComfyError as e:
            self._send_json({"error": str(e)}, 502)
        except KeyError as e:
            self._send_json({"error": f"Not found: {e.args[0] if e.args else e}"}, 404)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:                                  # a bug: report it to the UI and the console
            traceback.print_exc()
            self._send_json({"error": f"Server error: {type(e).__name__}: {e}"}, 500)
        finally:
            _CURRENT_APP.reset(token)

    def _send_json(self, payload, status=200, headers=None):
        self._send(Raw(json.dumps(payload).encode(), "application/json", headers=headers, status=status))

    def _drain_body(self):
        """Read and throw away the body of a request refused before its body was read (wrong content type, too large, a
        forbidden host). The connection closes after every reply, and closing a socket with unread data in it resets the
        connection on Windows: the client then saw a dropped connection instead of our error, 2 times in 30 for a 1 MB body
        (measured 2026-09-25). Only what fits under MAX_DRAIN is read, a chunk at a time, so nothing big is buffered."""
        if getattr(self, "_body_read", True):
            return
        self._body_read = True
        try:
            left = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return
        if not 0 < left <= MAX_DRAIN:
            return
        before = self.connection.gettimeout()
        try:
            self.connection.settimeout(5)            # a client that never sends the body it announced cannot hold the thread
            while left > 0:
                chunk = self.rfile.read(min(left, 65536))
                if not chunk:
                    break
                left -= len(chunk)
        except OSError:
            pass
        finally:
            self.connection.settimeout(before)

    def _send(self, result):
        self._drain_body()
        if not isinstance(result, Raw):
            result = Raw(json.dumps(result).encode(), "application/json")
        if result.etag and self.headers.get("If-None-Match") == result.etag:
            result = Raw(b"", result.ctype, {"Cache-Control": "no-cache"}, status=304, etag=result.etag)
        if result.etag:
            result.headers["ETag"] = result.etag
        if result.status == 200 and result.ctype.startswith(('audio/', 'video/')) and self.command == 'GET':
            result.headers['Accept-Ranges'] = 'bytes'
            requested = self.headers.get('Range')
            if_range = self.headers.get('If-Range')
            if requested and (not if_range or if_range == result.etag):
                size = len(result.body)
                match = re.fullmatch(r'bytes=(\d{0,20})-(\d{0,20})', requested)
                start, end = -1, -1
                if match and any(match.groups()) and size:
                    first, last = match.groups()
                    if first:
                        start = int(first); end = min(int(last), size - 1) if last else size - 1
                    elif int(last) > 0:
                        start, end = max(0, size - int(last)), size - 1
                if not 0 <= start <= end < size:
                    result.status = 416; result.body = b''
                    result.headers['Content-Range'] = f'bytes */{size}'
                else:
                    result.status = 206; result.body = result.body[start:end + 1]
                    result.headers['Content-Range'] = f'bytes {start}-{end}/{size}'
        self.send_response(result.status)
        self.send_header("Content-Type", result.ctype)
        self.send_header("Content-Length", str(len(result.body)))
        for k, v in result.headers.items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(result.body)


def _option(name, default):
    """Value of a `--name value` command line option, or the default."""
    if name in sys.argv and sys.argv.index(name) + 1 < len(sys.argv):
        return sys.argv[sys.argv.index(name) + 1]
    return default


# ---- updates: About > Check for updates and Update now ---------------------------------------------------------------
# The check asks GitHub (npcgen/updates.py). The update itself is scripts/update.py's, the same as update.bat's: the page
# shows its plan (--dry-run --json), and Update now starts it in a window of its own (--from-app), then this server stops;
# the updater waits for it to end (local/running.json names it), updates, and starts NPC Forge again with start.bat.
UPDATE_DIR = cfgmod.ROOT / "local" / "update"
RUNNING_FILE = cfgmod.ROOT / "local" / "running.json"
UPDATER = cfgmod.ROOT / "scripts" / "update.py"
UPDATE_PLAN_SECONDS = 300
_UPDATE = {"checks": None, "server": None, "started": False, "runner": subprocess.run, "starter": subprocess.Popen}
_UPDATE_LOCK = threading.Lock()


def _update_checks():
    if _UPDATE["checks"] is None:
        _UPDATE["checks"] = updatesmod.UpdateChecks(UPDATE_DIR)
    return _UPDATE["checks"]


def _update_notice():
    try:
        notice = json.loads((UPDATE_DIR / "notice.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return notice if isinstance(notice, dict) and notice.get("schema") == 1 and notice.get("state") else None


@route("GET", "/api/update")
def update_state(q, body):
    """This version, the last answer from GitHub (kept across restarts) and what the last update left to say."""
    return {"version": npcgen.__version__, "releases": f"https://github.com/{updatesmod.REPOSITORY}/releases",
            "last": _update_checks().last(), "notice": _update_notice(), "update_now": os.name == "nt"}


@route("POST", "/api/update/check")
def update_check(q, body):
    """Ask GitHub now whether there is a newer release: {"current", "latest", "newer", "message", "release", "checked_at"}."""
    try:
        return _update_checks().check_now()
    except updatesmod.UpdateError as exc:
        raise ApiError(503, str(exc)) from None


@route("POST", "/api/update/plan")
def update_plan(q, body):
    """What Update now would do, as update.bat shows it: {"ok", "newer", "message", "from", "to", "release", "steps", "notes",
    "method"} or {"ok": false, "problem"}."""
    try:
        done = _UPDATE["runner"]([sys.executable, str(UPDATER), "--dry-run", "--json"], cwd=str(cfgmod.ROOT), capture_output=True,
                                 text=True, encoding="utf-8", errors="replace", timeout=UPDATE_PLAN_SECONDS,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        raise ApiError(504, f"Reading the update took longer than {UPDATE_PLAN_SECONDS // 60} minutes. Try again later.") from None
    except OSError as exc:
        raise ApiError(500, f"The updater could not start ({exc}). Run update.bat in NPC Forge's folder instead.") from None
    lines = [line for line in (done.stdout or "").splitlines() if line.strip()]
    try:
        report = json.loads(lines[-1])
    except (IndexError, ValueError):
        tail = " ".join((done.stderr or "").strip().splitlines()[-3:])
        raise ApiError(500, f"The updater did not say what it would do (exit code {done.returncode}). {tail} "
                            "Run update.bat in NPC Forge's folder to see the whole message.") from None
    if not isinstance(report, dict) or "ok" not in report:
        raise ApiError(500, "The updater's answer could not be read. Run update.bat in NPC Forge's folder instead.")
    return report


@route("POST", "/api/update/start")
def update_start(q, body):
    """Update now: start the updater in a window of its own and stop this server a moment later (after this answer)."""
    if os.name != "nt":
        raise ApiError(400, "Update now works on Windows. Stop NPC Forge and run ./update.sh in its folder.")
    jobs = current_app().jobs
    if jobs.busy() and body.get("stop_jobs") is not True:
        raise ApiError(409, "Jobs are waiting or running. Let them finish, or stop them, before you update.")
    with _UPDATE_LOCK:
        if _UPDATE["started"]:
            raise ApiError(409, "The update has already started. NPC Forge is closing.")
        if jobs.busy():
            jobs.cancel()                                               # the person chose to stop them for the update
        try:
            _UPDATE["starter"]([sys.executable, str(UPDATER), "--from-app", "--reopen"], cwd=str(cfgmod.ROOT), close_fds=True,
                               creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
        except OSError as exc:
            raise ApiError(500, f"The updater could not start ({exc}). Close NPC Forge and run update.bat in its folder.") from None
        _UPDATE["started"] = True
    server = _UPDATE["server"]
    if server is not None:
        timer = threading.Timer(0.8, server.shutdown)
        timer.daemon = True
        timer.start()
    return {"started": True}


@route("DELETE", "/api/update/notice")
def update_notice_seen(q, body):
    try:
        (UPDATE_DIR / "notice.json").unlink()
    except FileNotFoundError:
        pass
    return {"ok": True}


def _mark_running(port):
    """local/running.json names this server while it runs, so update.bat can tell NPC Forge is open."""
    try:
        RUNNING_FILE.parent.mkdir(parents=True, exist_ok=True)
        RUNNING_FILE.write_text(json.dumps({"pid": os.getpid(), "port": port}), encoding="utf-8")
    except OSError:
        pass


def _unmark_running():
    try:
        if json.loads(RUNNING_FILE.read_text(encoding="utf-8")).get("pid") == os.getpid():
            RUNNING_FILE.unlink()
    except (OSError, ValueError, AttributeError):
        pass


def main():
    port_override = _option("--port", None)
    cfg = cfgmod.load(migrate_port=port_override is None)
    try:
        app = App(cfg)
    except RuntimeError as exc:
        if str(exc).startswith("Another NPC Forge"):
            sys.exit(f"{exc}\nClose the existing NPC Forge process, then run start.bat again.")
        raise
    host = _option("--host", cfg["host"])
    port = int(cfg["port"] if port_override is None else port_override)
    try:
        server = create_server((host, port), app)
    except (OSError, ValueError) as e:
        app.close()
        if isinstance(e, ValueError):
            sys.exit(str(e))
        second = (f"For a second copy, use a separate NPCGEN_LIBRARY and run python server.py --port {cfgmod.SECOND_PORT}."
                  if port != cfgmod.SECOND_PORT else "Choose a free port with --port and start NPC Forge again.")
        sys.exit(f"Could not start NPC Forge on {host}:{port} ({e.strerror or e}).\n"
                 f"Close the app using that port and try again. {second}")
    announced = False
    browser = None
    try:
        shipped = load_extensions(BUILTIN_EXT_DIR, app=app)
        extensions = shipped + load_extensions(app=app, skip=[p.name for p in BUILTIN_EXT_DIR.glob("*") if p.is_dir()])
        if extensions:
            print("Extensions loaded: " + ", ".join(extensions))
        url = local_url(host, server.server_port)
        problem = fels_suite.suite_dir_problem()
        if problem:
            print(problem)
        try:
            announced = fels_suite.write_address(cfgmod.APP_ID, url, npcgen.__version__, API_VERSION, uuid.uuid4().hex)
        except OSError as error:
            print(f"Could not announce NPC Forge to the other apps ({error}). You can connect using {url}.")
        auth = " · HTTP Basic authentication enabled (user: npcforge)" if server.require_auth else ""
        print(f"NPC Forge running at {url}  (ComfyUI: {cfg['comfy_url']}, library: {cfg['library_path']}){auth}", flush=True)
        if "--open" in sys.argv:
            # --setup-comfyui (Setup.exe's last page, through start.bat) opens the page at Set up ComfyUI.
            page = url + ("#setup-comfyui" if "--setup-comfyui" in sys.argv else "")
            browser = threading.Timer(0.8, lambda: webbrowser.open(page))
            browser.daemon = True
            browser.start()
        _UPDATE["server"] = server
        _mark_running(server.server_port)
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        if browser is not None:
            browser.cancel()
        try:
            if announced:
                fels_suite.remove_address(cfgmod.APP_ID)
        finally:
            try:
                server.server_close()
            finally:
                _UPDATE["server"] = None
                _unmark_running()
        if _UPDATE["started"]:
            print("NPC Forge closed for Update now. The update runs in a window of its own and starts NPC Forge again.")


def _stop(signum, frame):
    raise SystemExit(0)


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, _stop)
    main()
