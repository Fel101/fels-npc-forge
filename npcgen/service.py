"""Validation of generation requests, shared by the web server and the CLI. Raises ValueError with readable messages."""
import base64
import binascii
import io
import re

from PIL import Image, ImageOps

from . import anim as animation
from . import bodymaker
from . import cleanup3d
from . import engines
from . import head3d
from . import import3d
from . import living
from . import media
from . import prompts
from . import rig3d
from . import sheet as sheet_module
from . import three_d
from .jobs import DEFAULT_OPTIONS
from .voice import batch as voice_batch

DIRECTION_ORDER = ("down", "left", "right", "up")
MAX_PHOTO_DIMENSION = 2048          # a safety net; the browser already crops/downscales before it ever uploads
_DATA_URL_RE = re.compile(r"^data:image/[a-zA-Z0-9.+-]+;base64,")


def _object(value, label):
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _list(value, label):
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{label} must be a list")
    return value


def _boolean(raw, key, default=False):
    if key not in raw:
        return default
    if not isinstance(raw[key], bool):
        raise ValueError(f"{key} must be true or false")
    return raw[key]


def _unique_known(values, known):
    """Keep known values in request order, once. Duplicate requests must not duplicate expensive GPU work."""
    out = []
    for value in values:
        if isinstance(value, str) and value in known and value not in out:
            out.append(value)
    return out


def clean_animations(raw, data):
    """Validate an animation request and add what the job planner needs (the kind of each clip)."""
    raw = _object(raw, "animations")
    spec = data["animations"]
    clips = _unique_known(_list(raw.get("clips"), "clips"), spec["clips"])
    if not clips:
        raise ValueError("Pick at least one animation clip")
    requested = raw.get("dirs")
    requested = _list(requested, "dirs") if requested is not None else DIRECTION_ORDER
    dirs = [d for d in DIRECTION_ORDER if d in requested]
    if not dirs:
        raise ValueError("Pick at least one direction")
    try:
        if isinstance(raw.get("frames"), bool) or isinstance(raw.get("candidates"), bool):
            raise ValueError
        frames = int(raw.get("frames") or 4)
        candidates = max(1, min(4, int(raw.get("candidates") or 1)))
    except (TypeError, ValueError):
        raise ValueError("Frames and candidates must be numbers") from None
    mirror = _boolean(raw, "mirror_left", True)
    generated = animation.generated_directions(dirs, mirror)
    for clip_id in clips:
        clip = spec["clips"][clip_id]
        if clip.get("guide"):                            # its poses are drawn, not worded: the same counts serve every direction
            if frames not in animation.frame_counts(clip):
                raise ValueError(f"'{clip_id}' has no {frames}-frame version (available: {', '.join(map(str, animation.frame_counts(clip)))})")
            continue
        if clip["kind"] == "generated":
            for group in {spec["directions"][d]["group"] for d in generated}:
                if str(frames) not in clip["poses"].get(group, {}):
                    have = sorted(clip["poses"].get(group, {}), key=int)
                    raise ValueError(f"'{clip_id}' has no {frames}-frame version for {group} views (available: {', '.join(have) or 'none'})")
    return {"clips": clips, "dirs": dirs, "frames": frames, "candidates": candidates, "mirror_left": mirror,
            "kinds": {c: spec["clips"][c]["kind"] for c in clips}}


def clean_sheet(raw, data):
    """Validate a model-sheet request: which views to (re)draw, and the pose the full-body views stand in."""
    raw = _object(raw, "sheet")
    requested = raw.get("views")
    requested = _list(requested, "views") if requested is not None else sheet_module.VIEWS
    views = [v for v in sheet_module.VIEWS if v in requested]
    if not views:
        raise ValueError("Pick at least one model-sheet view")
    spec = data["sheet"]
    pose = raw.get("pose") or spec["default_pose"]
    if not isinstance(pose, str) or pose not in spec["poses"]:
        raise ValueError("Unknown pose: " + str(pose))
    return {"views": views, "pose": pose}


def clean_head(raw):
    """A detailed-head request: false (none), true (all four close-ups, then the model) or {"views": [...], "model": bool}
    for a part of it: one close-up drawn again, or the model rebuilt from the close-ups the head already has."""
    if raw is None or raw is False or raw == {}:
        return False
    if raw is True:
        return True
    raw = _object(raw, "head_3d")
    requested = raw.get("views")
    views = [v for v in head3d.VIEWS if v in (_list(requested, "views") if requested is not None else head3d.VIEWS)]
    model = _boolean(raw, "model", True)
    if not views and not model:
        raise ValueError("Pick a close-up to draw, or the head model")
    return {"views": views, "model": model}


def clean_assets(raw, data):
    raw = _object(raw, "assets")
    known = {e["id"] for e in data["expressions"]}
    assets = {
        "portrait": _boolean(raw, "portrait"),
        "sprite": _boolean(raw, "sprite"),
        "model_3d": _boolean(raw, "model_3d"),
        "rig_3d": _boolean(raw, "rig_3d"),
        "import_3d": clean_import(raw.get("import_3d")),  # a model made elsewhere, from one of ComfyUI's folders (npcgen/import3d.py)
        "body_3d": bodymaker.clean_request(raw.get("body_3d")),   # a designed body from the Body Designer, rigged and animated (npcgen/bodymaker.py)
        "head_3d": clean_head(raw.get("head_3d")),        # a detailed head: four close-ups and a separate model (npcgen/head3d.py)
        "refine_3d": _boolean(raw, "refine_3d"),
        "sharp_face": _boolean(raw, "sharp_face"),        # the clean-up with head close-ups (npcgen/headdetail.py)
        "smooth_eyes": _boolean(raw, "smooth_eyes"),      # the clean-up with the eye rings smoothed (npcgen/eyes.py)
        "match_hair": _boolean(raw, "match_hair"),        # the clean-up painting hair only from hair (npcgen/hairmask.py)
        "clean_hands": _boolean(raw, "clean_hands"),      # the clean-up with five-finger hands (npcgen/hands3d.py)
        "attach_head": _boolean(raw, "attach_head"),      # the clean-up with the detailed head's face laid over the model's (npcgen/headgraft.py)
        "check_3d": _boolean(raw, "check_3d"),            # a before/after check of the clean-up (npcgen/check3d.py)
        "best_3d": _boolean(raw, "best_3d"),              # the whole chain that came out best, ticked for each character (npcgen/best3d.py)
        "expressions": _unique_known(_list(raw.get("expressions"), "expressions"), known),
    }
    animations = raw.get("animations")
    if animations is not None and animations is not False and animations != {}:
        assets["animations"] = clean_animations(animations, data)
    sheet = raw.get("sheet")
    if sheet is not None and sheet is not False and sheet != {}:
        assets["sheet"] = clean_sheet(sheet, data)
    if _boolean(raw, "living"):
        assets["living"] = living.living_ids(data)       # the expressions it needs; the job adds only the missing ones
    voice = raw.get("voice")
    if voice is not None and voice is not False and voice != {}:
        assets["voice"] = voice_batch.clean_request(voice)    # each character's own voice job is built at submit time
    if assets["import_3d"] and (assets["model_3d"] or assets["best_3d"]):
        raise ValueError("Import a 3D model or generate one, not both")
    if assets["body_3d"] and (assets["model_3d"] or assets["best_3d"] or assets["import_3d"] or assets["rig_3d"] or assets["refine_3d"]):
        raise ValueError("A designed body is a 3D model with its own rig: ask for it on its own")
    if not (assets["portrait"] or assets["sprite"] or assets["model_3d"] or assets["import_3d"] or assets["body_3d"] or assets["rig_3d"] or assets["head_3d"]
            or assets["refine_3d"]
            or assets["sharp_face"] or assets["smooth_eyes"] or assets["match_hair"] or assets["clean_hands"] or assets["attach_head"] or assets["check_3d"] or assets["best_3d"] or assets["expressions"]
            or assets.get("animations") or assets.get("sheet") or assets.get("living") or assets.get("voice")):
        raise ValueError("Pick at least one asset to generate")
    return assets


def clean_import(raw):
    """A request to import a 3D model: {"model_file": a GLB in one of ComfyUI's folders, "clean": true | false | "auto" (close
    its holes and simplify it in ComfyUI), "detail": low | medium | high (how many faces that leaves)}, or None for no import.
    `clean` is settled by import3d.prepare once the file has been looked at."""
    if raw is None or raw is False or raw == {}:
        return None
    if not isinstance(raw, dict):
        raise ValueError("import_3d must be an object")
    clean = raw.get("clean", "auto")
    if clean not in (True, False, "auto"):
        raise ValueError("import_3d.clean must be true, false or \"auto\"")
    return {"model_file": import3d.canonical(raw.get("model_file")), "clean": clean, "detail": three_d.clean_quality(raw.get("detail"))}


def clean_photo(raw):
    """A client-submitted data URL (or bare base64), decoded into normalised, EXIF-corrected PNG bytes.

    Raises ValueError on anything that is not a real, decodable image -- the same contract every other clean_*
    helper in this module has, so server.py can turn it into a 400 the same way it does for the rest.
    """
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("No image data was sent")
    text = _DATA_URL_RE.sub("", raw.strip())
    try:
        data = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("Photo data is not valid base64") from None
    if not data:
        raise ValueError("No image data was sent")
    try:
        image = Image.open(io.BytesIO(data))
        image = ImageOps.exif_transpose(image)      # phone photos carry EXIF rotation; PIL does not auto-apply it
        image.load()
    except Exception:
        raise ValueError("That does not look like a valid image") from None
    image = image.convert("RGB")
    if max(image.size) > MAX_PHOTO_DIMENSION:
        image.thumbnail((MAX_PHOTO_DIMENSION, MAX_PHOTO_DIMENSION), Image.LANCZOS)
    return media.png(image, ai_generated=False), list(image.size)


def default_options(cfg):
    """The options a request that does not name them gets: the built-in ones, with whatever config.json's "options" section sets
    over them. A PC that has not the Qwen models, or wants the built-in Mage-Flow back, names its engines there once, instead of in every
    request (the built-in defaults are Qwen-Image 2512 and Qwen-Image-Edit 2511 with no quiet Mage-Flow fallback: Mage-Flow's licence is unclear)."""
    options = dict(DEFAULT_OPTIONS)
    for key, value in ((cfg or {}).get("options") or {}).items():
        if key in options:
            options[key] = value
    return options


def _engine(raw, base, key, kind):
    """The engine a request names for `key`, the configured default when it names none. An id that is not an engine of this
    kind is an error: it used to become Mage-Flow without a word, which a typo in a commercial project turned into pictures
    it could not use."""
    table, other = (engines.IMAGE_ENGINES, engines.EDIT_ENGINES) if kind == "image" else (engines.EDIT_ENGINES, engines.IMAGE_ENGINES)
    given = raw.get(key)
    value = base[key] if given is None or given == "" else given
    if isinstance(value, str) and value in table:
        return value
    if isinstance(value, str) and value in other:
        raise ValueError(f"{key}: '{value}' is {'an edit' if kind == 'image' else 'an image'} engine, not {'an image' if kind == 'image' else 'an edit'} "
                         f"engine. {key} takes one of: {', '.join(table)}")
    raise ValueError(f"{key}: unknown {kind} engine {value!r}. {key} takes one of: {', '.join(table)}")


def clean_options(raw, cfg):
    raw = _object(raw, "options")
    base = default_options(cfg)
    opt = {**base, **{k: v for k, v in raw.items() if k in base}}
    for key in ("transparent_sprite", "transparent_portrait", "save_candidates", "reroll", "engine_fallback"):
        opt[key] = _boolean(raw, key, bool(base[key]))
    if not isinstance(opt["quality"], str) or opt["quality"] not in cfg["quality"]:
        opt["quality"] = "standard"
    if opt["sprite_mode"] not in ("auto", "match", "independent"):
        opt["sprite_mode"] = "auto"
    if opt["sprite_view"] not in prompts.VIEWS:
        opt["sprite_view"] = "front"
    opt["image_model"] = _engine(raw, base, "image_model", "image")
    opt["edit_model"] = _engine(raw, base, "edit_model", "edit")
    opt["portrait_framing"] = prompts.clean_framing(opt["portrait_framing"])
    opt["game_faces"] = cleanup3d.clean_faces(opt["game_faces"])
    opt["three_d_model"] = three_d.clean(opt["three_d_model"])
    opt["three_d_quality"] = three_d.clean_quality(opt["three_d_quality"])
    opt["three_d_face_source"] = three_d.clean_face_source(opt["three_d_face_source"])
    opt["three_d_soften_eyes"] = _boolean(raw, "three_d_soften_eyes", False)
    opt["three_d_glasses_off"] = _boolean(raw, "three_d_glasses_off", False)
    opt["three_d_hunyuan_source"] = three_d.clean_hunyuan_source(opt["three_d_hunyuan_source"])
    opt["three_d_hunyuan_detail"] = three_d.clean_hunyuan_detail(opt["three_d_hunyuan_detail"])
    opt["rig_skeleton"] = rig3d.clean_skeleton(opt["rig_skeleton"])
    opt["rig_clips"] = rig3d.clean_clips(opt["rig_clips"])
    opt["head_quality"] = head3d.clean_quality(opt["head_quality"])
    opt["head_expression"] = head3d.clean_expression(opt["head_expression"])
    opt["head_match_hair"] = _boolean(raw, "head_match_hair", False)
    if opt["seed"] in (None, ""):
        opt["seed"] = None
    else:
        try:
            if isinstance(opt["seed"], bool):
                raise ValueError
            opt["seed"] = int(opt["seed"]) % (2 ** 32)
        except (TypeError, ValueError):
            raise ValueError("Seed must be a whole number") from None
    return opt
