"""A designed body as a character's 3D model: the Body Designer's adult body, built from a recipe, rigged and animated.

The Body Designer (the bodydesigner folder next to npcgen; it is optional and is not part of NPC Forge proper) turns a recipe, a base body and
21 proportions from -1 to 1, into a lean game GLB: body and eyes, 43k triangles, the Unreal mannequin's bone names (23 or 60 joints), four skin
weights per vertex, no morph targets, textures embedded. This module makes that body a character's 3D model, in one job step that needs no GPU:

  1. `python -m bodydesigner game` builds the GLB (about 20 s);
     An optional body document shapes the model before clothing is fitted.
  2. scripts/animate_body_designer_game.py has Blender copy the animation library's 84 clips and our Wave onto its skeleton, and appends them to the
     GLB without touching the body's own data (about 35 s);
  3. when the recipe asks for swinging Long hair or a swinging skirt or dress (its `secondary` field), the Body Designer's finalizer adds the chains of spring
     bones to the animated file, last of all (bodydesigner/secondary.py, under 2 s): the game engine moves them, nothing here does;
  4. optional local build steps may run before dressing or after the rig is complete;
  5. a picture of the model is drawn for the card.

Each is a process of its own: its memory stays out of the server's, and Cancel kills the whole tree (Blender too). Nothing here runs unless it is
asked for, and the body replaces a character's 3D model only when the request says so (`replace`). Humanoid, two-legged characters only.
"""
import json
import math
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import bodymatch, check3d, cleanup3d, config as cfgmod, itemposter, marks, morphology, outfit, rig3d, roll, underwear
from . import media
from .comfy import Cancelled, ComfyError, beat
from .library import now

COPIER = cfgmod.ROOT / "scripts" / "animate_body_designer_game.py"
MODEL_FILE, RIG_FILE, PREVIEW_FILE = "model_3d.glb", "model_3d_rigged.glb", "model_3d_preview.png"
SAVED_FILE = "body_design.json"                      # what the Body tab keeps for a character (bodydesigner/extension.py)
BUILD_SECONDS, COPY_SECONDS, DRESS_SECONDS = 600, 900, 900     # far more than they take: they only stop a hung process
SKELETONS = {
    "low": {"label": "Low (23 bones)", "bones": 23,
            "note": "Body, arms, legs and head: walking, standing and gestures. The hands move as one piece and the face is still."},
    "medium": {"label": "Medium (60 bones)", "bones": 60,
               "note": "Adds three bones per finger, a jaw for talking, twist bones in the arms and a prop bone in each palm to hold things by."},
}
FACES = {
    "natural": {"label": "Natural", "note": "Two separate brows, eyelashes, and eyes with a detailed iris set in their sockets. The default."},
    "classic": {"label": "Classic", "note": "The first face: faint brows joined by a band, no eyelashes, a simpler eye."},
}
TEXTURES = {
    2048: {"label": "2048 px", "note": "The sharpest skin: about 16 MB of video memory for each character, before the engine compresses it."},
    1024: {"label": "1024 px", "note": "A quarter of the memory (about 4 MB) and a softer skin."},
    512: {"label": "512 px", "note": "The smallest (about 1 MB); the brows and the lips blur."},
}
CLOTHES = {
    "basic": {"label": "Shirt and trousers", "note": "A sleeveless shirt and fitted trousers, in the colours the sheet's outfit names (the Body Designer's plain ones where it names none), and an open jacket or a bib apron over them when the sheet's outfit names one. A skirt the sheet names takes the place of the trousers, and a dress the place of the shirt and the trousers. The default."},
    "sheet": {"label": "Only what the sheet names", "note": "The shirt, the trousers or skirt, and the jacket or apron the sheet's outfit names, in its colours: a character in a skirt gets no trousers, a character in a dress gets neither shirt nor trousers, and a jacket or an apron is put on only over a shirt and trousers."},
    "none": {"label": "Underwear only", "note": "No clothes: the body in its underwear, to dress in a game or in the Items tab."},
}
EXTRA_CLOTHES = {}                                   # more choices for the Clothes list, added by a local extension (`add_clothes_choice`): {id: {label, note, kinds, visible, check}}
OUTERS = {
    "sheet": {"label": "Like the sheet", "note": "The jacket (a jacket, a blazer or a cardigan) or the apron the sheet's outfit names, if it names one and the body has a shirt and trousers under it. A body wears one outer garment: the first the sheet names."},
    "none": {"label": "None", "note": "Nothing over the shirt and trousers."},
    "jacket": {"label": "Open jacket", "note": "A short jacket, open at the front, over the shirt and trousers: in the sheet's colour for a jacket where it names one, else the Body Designer's brown."},
    "apron": {"label": "Bib apron", "note": "A bib apron over the shirt and trousers: in the sheet's colour for an apron where it names one, else the Body Designer's tan."},
}
HAIRS = {
    "sheet": {"label": "Like the sheet", "note": "Crop or Sweep when the sheet's hair is short, Long when it says long, none when it is bald, in the sheet's colour. For a style the Body Designer has no hair for (a bun, a bob, braids) it puts a Crop as a stand-in, and for long hair that is braided, tied back or in a ponytail the Long as the nearest, and says so."},
    "none": {"label": "None", "note": "A bare head."},
    "crop": {"label": "Crop", "note": "Short and neat, a cap of hair with the ears free."},
    "sweep": {"label": "Sweep", "note": "Short, with a fringe swept to one side."},
    "long": {"label": "Long", "note": "Long, straight and smooth, falling to the upper back, with a fringe swept to one side."},
}
MARKS = {
    "sheet": {"label": "Like the sheet", "note": "The skin marks the sheet's distinguishing features name (freckles, vitiligo, moles, beauty marks, birthmarks and scars), painted into the skin where it says they are; freckles and vitiligo go where such skin has them when it names no place. "
                                                 "A tattoo, a burn, dimples or a place the Body Designer has no equivalent for is left out, and the card says so. They are baked into the skin when the body is made and stay: to change them, make the body again."},
    "none": {"label": "None", "note": "Clear skin."},
}
SWINGING_PARTS = ("hair", "cloth")                   # the two things that can swing (bodydesigner/secondary.py): Long hair, and a skirt or a dress
SWINGING_CLOTH = ("skirt", "dress")
SWINGING_OVER = ("jacket", "apron")                  # what the swinging cloth is not made for: anything worn over the skirt or dress
REQUEST_FIELDS = {"base", "skeleton", "values", "saved", "replace", "recipe", "face", "texture", "match", "clothes", "outer", "colours", "hair", "marks", "expressions", "secondary", "soft", "bust", "body"}
FACE_FIELDS = ("eye_profile", "skin_detail_profile", "brow_profile")        # the three profile fields that make a face; set together, never mixed
BODY_DOCUMENT = "npcforge.body-design"               # the format of the Body Designer tab's body document: {format, version, recipe, skeleton, seed, body}; `body` is the block that shapes the body, or null


class BodyError(Exception):
    """The body could not be made; the message says why, in words for the person using the page."""


@dataclass(frozen=True)
class BuildStepCLI:
    add_arguments: Callable
    value: Callable
    describe: Callable


@dataclass(frozen=True)
class BuildStep:
    clean: Callable
    info: Callable
    apply: Callable
    validate: Callable
    check: Callable
    face_shapes: Callable | None = None
    cli: BuildStepCLI | None = None
    normalize_rig: Callable | None = None


BUILD_STEPS = {"before_clothes": {}, "body_finished": {}}
LEGACY_BUILD_FIELDS = {"soft": {}, "bust": 0.0}
EXTENSION_REQUIRED = "This model uses motion or shape options from an add-on. Install and enable the add-on to use those options."


def add_build_step(stage, name, step, *, visible=None):
    if stage not in BUILD_STEPS:
        raise ValueError(f"Unknown body build stage: {stage!r}")
    if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", name) or not isinstance(step, BuildStep):
        raise ValueError("A body build step needs a field name and a BuildStep.")
    if name in REQUEST_FIELDS - LEGACY_BUILD_FIELDS.keys() or any(name in steps for steps in BUILD_STEPS.values()):
        raise ValueError(f"Body build field is already registered: {name!r}")
    BUILD_STEPS[stage][name] = (step, visible)


def _build_steps(stage):
    return {name: step for name, (step, visible) in BUILD_STEPS[stage].items() if roll._is_visible(visible)}


def _active_steps():
    return {**_build_steps("before_clothes"), **_build_steps("body_finished")}


def extension_notice(npc):
    designed = ((npc.get("assets") or {}).get("model_3d") or {}).get("designed") or {}
    active = _active_steps()
    choice = designed.get("clothes_choice")
    if (any(designed.get(key) and key not in active for key in LEGACY_BUILD_FIELDS)
            or choice and choice not in CLOTHES and not _offered(choice)):
        return EXTENSION_REQUIRED
    return None


def _api():
    try:
        from bodydesigner import api
    except ImportError:
        raise BodyError("The Body Designer is not installed (it is the bodydesigner folder next to npcgen)") from None
    return api


def bases():
    """[{id, label, minimum_age, controls}] of the bodies the Body Designer can build, or [] when it is not installed."""
    try:
        return _api().bases()
    except BodyError:
        return []
    except (OSError, ValueError, KeyError):                      # a damaged install must not stop the page from loading
        return []


def natural_profiles():
    """The Body Designer's three Natural-face profile fields (bodydesigner/API_INTEGRATION.md: set together), with the lip colour when it has one (its own skin finish, accepted
    on 2026-10-08), or None when this Body Designer has no Natural face (an older one: its bodies keep the classic face)."""
    try:
        from bodydesigner.face_finish import PROFILES
    except ImportError:
        return None
    try:
        from bodydesigner.lip_finish import PROFILES as WITH_LIPS
    except ImportError:
        WITH_LIPS = PROFILES
    return {k: WITH_LIPS[k] for k in FACE_FIELDS}


def _natural_skins():
    """The skin profiles a Natural face may have: with the lip colour, or without it (a body saved before the lips)."""
    try:
        from bodydesigner.face_finish import PROFILES
    except ImportError:
        return set()
    return {PROFILES["skin_detail_profile"], (natural_profiles() or PROFILES)["skin_detail_profile"]}


def offers(part):
    """True when this Body Designer makes `part`: 'garments' (bodydesigner/garments.py), 'hair' (bodydesigner/game_hair.py), 'expressions' (bodydesigner/expressions.py, and gaze.py beside it),
    'secondary' (swinging hair and cloth: bodydesigner/secondary.py and the three modules it uses) or 'body_shape' (the body document and its pure body step: bodydesigner/body_design.py)."""
    try:
        if part == "garments":
            from bodydesigner import garments  # noqa: F401
        elif part == "expressions":
            from bodydesigner import expressions, gaze  # noqa: F401
        elif part == "secondary":
            from bodydesigner import secondary, secondary_cloth, secondary_core, secondary_hair  # noqa: F401
        elif part == "body_shape":
            from bodydesigner import body_design  # noqa: F401
        else:
            from bodydesigner import game_hair  # noqa: F401
    except ImportError:
        return False
    return True


def expressions_info():
    """For the card: {label, note, presets} when this Body Designer makes face expressions (bodydesigner/expressions.py: the shapes are read from its own catalogue), else None."""
    if not offers("expressions"):
        return None
    try:
        from bodydesigner import expressions
        presets = [str(name) for name in expressions.catalogue()["presets"]]
    except (ImportError, OSError, ValueError, KeyError, TypeError):
        return None
    return {"label": "Face expressions", "presets": presets,
            "note": f"Adds {len(presets)} face shapes the engine can blend from 0 to 1 (a blink, and each eye on its own, happy, angry, sad, relaxed, surprised and five mouth shapes for speech), on the body and its lashes, and makes each eye a part of its own that can look about "
                    "(20 degrees sideways, 15 up or down). The Natural face only; off unless you choose it, and about half a megabyte more for each character."}


def swinging_info():
    """For the card: {hair, cloth, note, joints} when this Body Designer makes swinging hair and cloth (bodydesigner/secondary.py: the labels, the chains and the bone counts are read from its own
    catalogue), else None. `hair` and `cloth` are {label, bones, note}: how many bones the part adds to the skeleton, in words for the card; `joints` is {skeleton: {nothing, hair, cloth, both}}, the
    number of bones the skeleton has for each choice."""
    if not offers("secondary"):
        return None
    try:
        data = _api().secondary_catalogue()
        chains, per = data["chains_per_part"], data["bones_per_chain"]
        label = {part: str(data["controls"][part]["label"]) for part in SWINGING_PARTS}
        joints = {tier: {"nothing": row["base"], "hair": row["hair"], "cloth": row["cloth"], "both": row["both"]} for tier, row in data["joint_counts"].items()}
    except (BodyError, OSError, ValueError, KeyError, TypeError, AttributeError):
        return None
    return {"hair": {"label": label["hair"], "bones": chains["hair"] * per,
                     "note": f"Adds {chains['hair'] * per} bones to the skeleton, in {chains['hair']} chains through the Long hair, that the game engine swings as the character moves. It needs the Long hair."},
            "cloth": {"label": label["cloth"], "bones": chains["cloth"] * per,
                      "note": f"Adds {chains['cloth'] * per} bones to the skeleton, in {chains['cloth']} chains down the skirt or the dress, that the game engine swings as the character moves. It needs a skirt or a dress with nothing worn over it."},
            "joints": joints,
            "note": "Off unless you choose it. The game engine does the swinging (in Godot, examples/godot/forge_secondary_motion.gd sets it up from the file), so this page's viewer does not show it. The swing is gentle, and when it swings far "
                    "the hair or the hem can pass a little into the body: nothing stops it. Clothes tried on from the Items tab then take about a minute, not half a minute: the body is built again to fit them."}


def hair_styles():
    """The game hair this Body Designer makes, read from its own catalogue ('crop' and 'sweep', and 'long' when it has it): [] when it makes none. A Body Designer that has game hair
    but cannot say which keeps the two it always had."""
    try:
        from bodydesigner import game_hair
        return [style["id"] for style in game_hair.catalogue()["styles"]]
    except ImportError:
        return []
    except (OSError, ValueError, KeyError, TypeError):
        return ["crop", "sweep"]


def body_shape_api():
    """The Body Designer's body document (bodydesigner/body_design.py: the strict document, its pure body step, the upper-edge repair and the catalogue), or None when this Body Designer has none (an
    older one): every body is then made as it always was, and a request or a saved body that carries a body block is refused in words."""
    try:
        from bodydesigner import body_design
    except ImportError:
        return None
    return body_design


def is_document(raw):
    """Whether `raw` is a body document (what the Body tab saves for a body that has a body block) and not a bare recipe."""
    return isinstance(raw, dict) and raw.get("format") == BODY_DOCUMENT


def _open_document(raw, what):
    """(recipe, body block or None) of a body document, checked by the Body Designer's own strict rule (unknown fields refused, nothing clamped): ValueError in words, `what` naming whose document it is."""
    api = body_shape_api()
    if api is None:
        raise ValueError(f"{what} is a body document (a body with a body shape), and this Body Designer cannot read one. Install its newer package.")
    try:
        document = api.clean_document(raw)
    except ValueError as exc:
        raise ValueError(f"{what} cannot be used ({exc})") from None
    return document["recipe"], document["body"]


def _shape_of(recipe, block, skeleton):
    """The body block as the Body Designer cleans it for this recipe and this rig (its own strict rule: unknown fields refused, a form the body cannot take refused, nothing clamped or dropped), or ValueError in
    words. The block's bust size is the body's one bust size, from -1 to 3."""
    api = body_shape_api()
    if api is None:
        raise ValueError("This Body Designer has no body shapes (a body document with a body block). Install its newer package, or leave the body block out.")
    try:
        document = api.clean_document({"format": api.FORMAT, "version": api.VERSION, "recipe": recipe, "skeleton": skeleton, "seed": 0, "body": block})
    except ValueError as exc:
        raise ValueError(f"The body shape is not valid: {exc}") from None
    return document["body"]


def saved_shape(lib, npc_id):
    """What the card needs of the body block in the body the Body tab saved for this character, {base, form, bust, body} (`body` is the block as the Body Designer cleans it, for the card to send back
    with an edit of its bust size), or None: nothing saved, a saved body without a body block, or this Body Designer cannot read a body document."""
    raw = saved_recipe(lib, npc_id)
    if not is_document(raw) or body_shape_api() is None:
        return None
    try:
        recipe, block = _open_document(raw, "The Body tab's saved body")
    except ValueError:
        return None
    return None if block is None else {"base": recipe["base"], "form": block["form"], "bust": block["bust"], "body": block}


def availability(cfg):
    """Expose the body tools and the active extension controls that can run on this PC."""
    found = bases()
    missing = [] if found else ["the Body Designer (the bodydesigner folder next to npcgen)"]
    if found and not COPIER.is_file():
        missing.append("scripts/animate_body_designer_game.py")
    if not rig3d.find_blender(cfg):
        missing += rig3d.availability(cfg)["missing"]
    if not rig3d.library_files(cfg):
        missing += rig3d.library_availability(cfg)["missing"]
    natural = natural_profiles() is not None
    return {"installed": not missing, "missing": missing, "bases": [{"id": b["id"], "label": b["label"]} for b in found],
            "skeletons": [{"id": k, **v} for k, v in SKELETONS.items()],
            "faces": [{"id": k, **v} for k, v in FACES.items() if natural or k == "classic"],
            "textures": [{"id": k, **v} for k, v in TEXTURES.items()],
            "clothes": clothes_choices() if found and offers("garments") else [],
            "outers": [{"id": k, **v} for k, v in OUTERS.items() if k in ("sheet", "none") or k in outfit.offered()] if found and offers("garments") else [],
            "hairs": [{"id": k, **v} for k, v in HAIRS.items() if k in ("sheet", "none") or k in hair_styles()] if found and offers("hair") else [],
            "marks": [{"id": k, **v} for k, v in MARKS.items()] if found and marks.offered() else [], "expressions": expressions_info() if found else None,
            "swinging": swinging_info() if found else None, "clips": 85,
            **({key: value for step in _active_steps().values() for key, value in step.info().items()} if found else {})}


def clean_request(raw):
    """Validate a designed-body request before prepare settles the recipe.

    A full body document is split into its recipe, skeleton and body block. Installed build steps validate their own fields.
    """
    if raw is None or raw is False or raw == {}:
        return None
    if raw is True:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError("body_3d must be an object")
    steps = _active_steps()
    unknown = sorted(set(raw) - REQUEST_FIELDS - steps.keys())
    if unknown:
        raise ValueError(f"body_3d.{unknown[0]}: unknown field")
    skeleton = raw.get("skeleton", "low")
    if skeleton not in SKELETONS:
        raise ValueError("body_3d.skeleton must be low or medium")
    base = raw.get("base")
    if base is not None and not isinstance(base, str):
        raise ValueError("body_3d.base must be a body id")
    values = raw.get("values")
    if values is not None:
        if not isinstance(values, dict):
            raise ValueError("body_3d.values must be an object")
        for key, value in values.items():
            if type(value) not in (int, float) or not math.isfinite(value) or not -1 <= value <= 1:
                raise ValueError(f"body_3d.values.{key}: expected a number from -1 to 1")
        values = {str(k): float(v) for k, v in values.items()}
    recipe = raw.get("recipe")
    if recipe is not None and not isinstance(recipe, dict):
        raise ValueError("body_3d.recipe must be an object")
    body = raw.get("body")
    if body is not None and not isinstance(body, dict):
        raise ValueError("body_3d.body must be an object: the body block of a body document")
    if is_document(recipe):                                       # (a whole body document, as the Body tab saves one: its recipe, its rig and its body block, taken apart here)
        if body is not None:
            raise ValueError("body_3d.recipe is a whole body document, which has its body block in it: do not give body_3d.body beside it")
        if not isinstance(recipe.get("recipe"), dict):
            raise ValueError("body_3d.recipe.recipe must be an object")
        if "skeleton" in raw and raw["skeleton"] != recipe.get("skeleton"):
            raise ValueError("body_3d.skeleton is not the rig this body document says it is built for")
        skeleton, body, recipe = recipe.get("skeleton", skeleton), recipe.get("body"), recipe["recipe"]
        if skeleton not in SKELETONS:
            raise ValueError("body_3d.skeleton must be low or medium")
        if body is not None and not isinstance(body, dict):
            raise ValueError("body_3d.recipe.body must be an object or null")
    face = raw.get("face")
    if face is not None and face not in FACES:
        raise ValueError("body_3d.face must be " + " or ".join(FACES))
    texture = raw.get("texture", max(TEXTURES))
    if type(texture) is not int or texture not in TEXTURES:
        raise ValueError("body_3d.texture must be " + ", ".join(str(k) for k in TEXTURES))
    match = raw.get("match", True)
    if type(match) is not bool:
        raise ValueError("body_3d.match must be true or false")
    clothes = raw.get("clothes", "none")
    if not isinstance(clothes, str) or (clothes not in CLOTHES and not _offered(clothes)):
        raise ValueError("body_3d.clothes must be " + ", ".join(choice["id"] for choice in clothes_choices()))
    outer = raw.get("outer", "sheet")
    if outer not in outfit.OUTERS:
        raise ValueError("body_3d.outer must be " + ", ".join(outfit.OUTERS))
    try:
        colours = outfit.clean_colours(raw.get("colours"))
    except ValueError as exc:
        raise ValueError(f"body_3d.{exc}") from None
    hair = raw.get("hair")
    if hair is not None and hair not in HAIRS:
        raise ValueError("body_3d.hair must be " + ", ".join(HAIRS))
    skin = raw.get("marks")
    if skin is not None and skin not in MARKS:
        raise ValueError("body_3d.marks must be " + " or ".join(MARKS))
    shapes = raw.get("expressions")
    if shapes is not None and type(shapes) is not bool:
        raise ValueError("body_3d.expressions must be true or false")
    swing = raw.get("secondary")
    if swing is False:
        swing = {}
    if swing is not None:
        if not isinstance(swing, dict) or set(swing) - set(SWINGING_PARTS):
            raise ValueError("body_3d.secondary must be {hair, cloth}, each true or false")
        for part, on in swing.items():
            if type(on) is not bool:
                raise ValueError(f"body_3d.secondary.{part} must be true or false")
        swing = {part: swing.get(part, False) for part in SWINGING_PARTS}
    if any(raw.get(name) and name not in steps for name in LEGACY_BUILD_FIELDS):
        raise ValueError(EXTENSION_REQUIRED)
    additions = {**LEGACY_BUILD_FIELDS, **{key: step.clean(raw.get(key), f"body_3d.{key}") for key, step in steps.items()}}
    return {"base": base, "skeleton": skeleton, "values": values, "saved": raw.get("saved") is True, "replace": raw.get("replace") is True,
            "face": face, "texture": texture, "match": match, "clothes": clothes, "outer": outer, "colours": colours, "hair": hair, "marks": skin, "expressions": shapes, "secondary": swing,
            **additions, **({"recipe": recipe} if recipe else {}), **({"body": body} if body is not None else {})}


def _sex(base_id):
    return "female" if "female" in base_id else "male" if "male" in base_id else None


def default_dressing(recipe, npc=None):
    """What a new body wears when nothing says otherwise: the plain underwear of its sex (npcgen/underwear.py)."""
    return underwear.kinds_for(_sex(recipe["base"]))


_dressing = default_dressing


def set_dressing(policy=None):
    """Let a local extension decide what a designed body wears: `policy(recipe, npc)` (the recipe it is built from, and the character it is for, a record, or None when it is
    built for nobody) returns the pieces of data/underwear.json to put on ([] for none), and None puts the default back. The pages, the API and the command line have no control
    that takes the underwear off: this is the one place that can."""
    global _dressing
    _dressing = policy or default_dressing


def _known_pieces(kinds):
    """`kinds` as a list, every piece of it one of data/underwear.json (BodyError, naming the first that is not)."""
    kinds = list(kinds or [])
    unknown = [k for k in kinds if k not in underwear.data()["kinds"]]
    if unknown:
        raise BodyError(f"The dressing asked for {unknown[0]!r}, which is not a piece of underwear")
    return kinds


def dressing(recipe, npc=None):
    """The pieces the body of this recipe wears, for this character, checked."""
    return _known_pieces(_dressing(recipe, npc))


def add_clothes_choice(choice_id, label, note, kinds=(), visible=None, check=None):
    """Let a local extension add one choice to the card's Clothes list. NPC Forge itself offers CLOTHES and nothing else (the pages, the API and the command line cannot take the underwear
    off): this is how a private extension adds its own, and it only takes effect while the extension says the choice is shown. Choosing it makes a body with no garments (like "Underwear only")
    that wears exactly the pieces `kinds` of data/underwear.json instead of what `dressing` decides (() for none). `visible()` says whether the page lists the choice and the API takes it now
    (None: always; a call that raises says no) and `check(npc)` returns why this character cannot have it, in words, or None. ValueError for an id that is not a plain lower-case word, one of
    CLOTHES, or a piece that data/underwear.json does not have."""
    if not isinstance(choice_id, str) or not re.fullmatch(r"[a-z][a-z0-9_]{1,23}", choice_id) or choice_id in CLOTHES:
        raise ValueError(f"{choice_id!r} cannot be the id of a choice of clothes")
    try:
        kinds = _known_pieces(kinds)
    except BodyError as exc:
        raise ValueError(str(exc)) from None
    EXTRA_CLOTHES[choice_id] = {"label": str(label), "note": str(note), "kinds": kinds, "visible": visible, "check": check}


def _offered(choice_id):
    """The extra choice of clothes with this id, when a local extension has added it and says it is shown now, else None."""
    extra = EXTRA_CLOTHES.get(choice_id) if isinstance(choice_id, str) else None
    if extra is None:
        return None
    try:
        return extra if extra["visible"] is None or extra["visible"]() else None
    except Exception:                                             # a broken switch must never show what it guards
        return None


def clothes_choices():
    """The Clothes list for the card: [{id, label, note}] of CLOTHES, then those a local extension has added that it shows now ({id, label, note, extra: True, underwear: [the pieces it wears]})."""
    return [{"id": k, **v} for k, v in CLOTHES.items()] + [{"id": k, "label": e["label"], "note": e["note"], "extra": True, "underwear": list(e["kinds"])}
                                                           for k, e in EXTRA_CLOTHES.items() if _offered(k)]


def _extra_problem(extra, npc):
    """Why this character cannot have the extra choice of clothes, in words, or None (a check that cannot run says no)."""
    if not extra["check"]:
        return None
    try:
        return extra["check"](npc) or None
    except Exception as exc:
        return f"A check on this choice of clothes could not run ({exc}), so nothing was made."


def default_base(npc, found=None):
    """The base that suits the character's gender: the female body for a woman, the male body for a man, else None (choose)."""
    gender = str((npc.get("profile") or {}).get("gender") or "").lower()
    sex = "female" if gender in ("female", "woman", "girl", "f") else "male" if gender in ("male", "man", "boy", "m") else None
    for base in found if found is not None else bases():
        if sex and _sex(base["id"]) == sex:
            return base["id"]
    return None


def saved_recipe(lib, npc_id):
    """The recipe the Body tab saved for this character (its raw JSON), or None."""
    try:
        path = lib.resolve(npc_id, SAVED_FILE)
        return json.loads(path.read_text("utf-8")) if path.is_file() else None
    except (OSError, ValueError, KeyError):
        return None


def status(lib, npc):
    """For the card: {saved (the Body tab has saved a body for this character), base (the one that suits its gender), designed (what its 3D model
    was made from, or None), match ({said, skipped, hair}: what the sheet says that a new body would take, what it says that the tables do not know, and the game hair
    its hair text is), outfit ({basic, sheet}: for each choice of what goes over them ({sheet, none, jacket, apron}: the garments each choice of clothes would put on, the garment words of the sheet that are not put on and
    why, and a problem when what was asked for cannot be done), marks (npcgen/marks.py `summary`: the skin marks the sheet names, in words, and what it names that is not painted; None when this Body Designer paints none),
    shape (`saved_shape`: the body block of the saved body, or None)}."""
    found = bodymatch.match(npc.get("appearance"))
    return {"saved": saved_recipe(lib, npc["id"]) is not None, "base": default_base(npc),
            "designed": ((npc.get("assets") or {}).get("model_3d") or {}).get("designed"),
            "match": {"said": found["said"], "skipped": found["skipped"], "hair": found["hair"]},
            "outfit": {mode: {outer: outfit.pieces(mode, npc.get("appearance"), outer=outer) for outer in outfit.OUTERS} for mode in ("basic", "sheet")},
            "marks": marks.summary(npc.get("appearance"), npc["id"]), "shape": saved_shape(lib, npc["id"])}


CLEAN_UP = ("rig_3d", "refine_3d", "sharp_face", "smooth_eyes", "match_hair", "clean_hands", "attach_head", "check_3d")
NEW_MODEL = ("model_3d", "import_3d", "body_3d", "best_3d")


def refuses(npc, assets):
    """Why a request cannot be made on a character whose 3D model is a designed body, or None. The clean-up, the paint and the rig are made for a model
    that was generated from pictures: this one is already rigged and animated, and its textures are its own."""
    designed = ((npc.get("assets") or {}).get("model_3d") or {}).get("designed")
    if designed and any(assets.get(k) for k in CLEAN_UP) and not any(assets.get(k) for k in NEW_MODEL):
        return ("This character's 3D model is a designed body, which is already rigged and animated: the clean-up, paint and rig steps are for models "
                "generated from pictures. Make a new 3D model first, or change the body and use it again.")
    return None


def prepare(lib, npc, request):
    """Settle a request into the exact recipe the body will be built from, checked by the Body Designer's own validator, so a mistake is
    refused now (ValueError, naming the field) and a recipe saved in the Body tab is the one used even if it changes before the job runs.
    Returns the request with `recipe` and `base_label` added."""
    reason = rig3d.unsupported_reason(morphology.resolve(npc, roll.load_data()))
    if reason:
        raise ValueError(reason)
    try:
        api = _api()
        found = api.bases()
    except BodyError as exc:
        raise ValueError(str(exc)) from None
    ids = {b["id"]: b for b in found}
    saved = saved_recipe(lib, npc["id"]) if request["saved"] else None
    if request["saved"] and saved is None:
        raise ValueError("The Body tab has not saved a body for this character")
    block, held = request.get("body"), None                           # the body block of a body document: the request's own, else the one in the saved body's document
    if is_document(saved):                                            # (the Body tab saves a bare recipe, or a whole body document when the body has a body block)
        saved, held = _open_document(saved, "The Body tab's saved body")
        if block is None:
            block = held
    fresh, matched = False, None                                      # made from its base's own defaults: the Natural face, the sheet's looks and its hair are the default for it
    if request.get("recipe"):
        recipe = request["recipe"]
    else:
        carried = None
        if saved is not None:
            try:                                                  # the Body Designer's own rule for a saved body: a current recipe stays exactly as it
                carried = api.recipe_from_authoring(saved)        # is (colours, finish, face), an older Body-tab recipe becomes a body of its sex
            except ValueError as exc:                             # with the proportions it shares, and anything else is refused, naming the field
                raise ValueError(f"The Body tab's saved body cannot be used ({exc}). Open it in the Body tab and use Convert saved proportions.") from None
        base = request["base"] or (carried["base"] if carried else None) or default_base(npc, found)
        if base not in ids:
            raise ValueError("Choose a body: " + ", ".join(ids))
        if held is not None and block is held and carried and carried["base"] != base:
            raise ValueError("The body shape saved in the Body tab belongs to its own body, and another body was asked for. Use the saved body, or clear its body shape in the Body tab.")
        if carried and carried["base"] == base:                   # its own body: all of it, the face finish and the locks too
            recipe = carried
        else:
            recipe, fresh = api.default_recipe(base), True
            if request.get("match", True):                        # a new body looks like its character: build, skin, eyes, brows (what is asked for below is applied over it)
                found = bodymatch.match(npc.get("appearance"), ids[base]["controls"])
                recipe["values"].update(found["values"])
                recipe["materials"].update(found["materials"])
                recipe["brows"].update(found["brows"])
                matched = {"said": found["said"], "skipped": found["skipped"]} if found["said"] or found["skipped"] else None
            if carried:                                           # another body: the 21 proportions are the same in every body, the rest is that body's own
                recipe["values"].update({k: v for k, v in carried["values"].items() if k in ids[base]["controls"]})
        if request["values"]:
            unknown = sorted(set(request["values"]) - set(ids[base]["controls"]))
            if unknown:
                raise ValueError(f"body_3d.values.{unknown[0]}: not a proportion of this body")
            recipe["values"].update(request["values"])
    try:
        recipe = api.clean_recipe(recipe)
    except ValueError as exc:
        raise ValueError(f"The body recipe is not valid: {exc}") from None
    face = request.get("face") or ("natural" if fresh else None)    # a saved body or a whole recipe keeps its own face unless the request says one
    if face:
        recipe = api.clean_recipe(with_face(api, recipe, face, explicit=bool(request.get("face"))))
    hair = request.get("hair") or ("sheet" if fresh and request.get("match", True) else None)    # (and its own hair, unless the request says one)
    hair_said = None
    if hair:
        sheet = bodymatch.match(npc.get("appearance"))["hair"]
        if not (fresh and request.get("match", True)):                # somebody's own design is not coloured by the sheet
            sheet = {k: v for k, v in sheet.items() if k != "color"}
        recipe, hair_said = with_hair(recipe, hair, sheet, explicit=bool(request.get("hair")))
        recipe = api.clean_recipe(recipe)
    skin = request.get("marks") or ("sheet" if fresh and request.get("match", True) else None)    # (and its own skin marks, unless the request says)
    marks_said = None
    if skin:
        recipe, marks_said = with_marks(recipe, skin, npc, explicit=bool(request.get("marks")))
        recipe = api.clean_recipe(recipe)
    if request.get("expressions") is not None:                    # (a saved body or a whole recipe keeps its own face shapes unless the request says)
        recipe = api.clean_recipe(with_expressions(recipe, request["expressions"]))
    if request.get("secondary") is not None:                      # (and its own swinging hair and cloth)
        recipe = api.clean_recipe(with_secondary(recipe, request["secondary"]))
    clothes = request.get("clothes", "none")
    extra = _offered(clothes)                                     # (a choice a local extension added: no garments, and the underwear it names)
    if extra:
        problem = _extra_problem(extra, npc)
        if problem:
            raise ValueError(problem)
        if request.get("outer", "sheet") not in ("sheet", "none"):
            raise ValueError("Nothing is worn over this choice of clothes: set what goes over them to None")
        if not extra["kinds"] and recipe.get("secondary"):
            raise ValueError("Swinging hair and cloth are added to a body in its underwear, and this choice of clothes leaves it off. Turn swinging off, or choose other clothes.")
    elif clothes != "none" and not offers("garments"):
        raise ValueError("This Body Designer has no garments")
    wears = outfit.pieces("none" if extra else clothes, npc.get("appearance"), outer="none" if extra else request.get("outer", "sheet"), colours=None if extra else request.get("colours"))
    if wears["problem"]:
        raise ValueError(wears["problem"])
    if wears["pieces"] and not offers("garments"):
        raise ValueError("This Body Designer has no garments")
    problem = swing_problem(recipe, wears["pieces"])
    if problem:
        raise ValueError(problem)
    if extension_notice(npc):
        raise ValueError(EXTENSION_REQUIRED)
    for key, step in _active_steps().items():
        if request.get(key):
            if problem := roll.adult_problem(npc):
                raise ValueError(problem)
        step.validate(request.get(key), recipe, block)
    shape = _shape_of(recipe, block, request["skeleton"]) if block is not None else None
    have_model = bool(((npc.get("assets") or {}).get("model_3d") or {}).get("file"))
    if have_model and not request["replace"]:
        raise ValueError("This character already has a 3D model, and a designed body replaces it (with its skeleton). Confirm to replace it.")
    return {**request, "recipe": recipe, "base": recipe["base"], "base_label": ids[recipe["base"]]["label"], "face": face_of(recipe), "matched": matched,
            "saved": saved is not None and not request.get("recipe"), "clothes": clothes, "pieces": wears["pieces"], "left": wears["left"], "why": wears["why"], "hair_said": hair_said,
            "marks_said": marks_said, "expressions": bool(recipe.get("expressions")), "secondary": recipe.get("secondary"), "soft": request.get("soft") or {}, "bust": request.get("bust") or 0.0,
            **({"body": shape} if shape is not None else {}),         # (`body`: the body block as the Designer cleaned it)
            **({"wear": list(extra["kinds"])} if extra else {})}          # (`wear`: the underwear of a choice a local extension added, kept with the request so that the job needs no extension to run)


def polish_face(recipe):
    """Whether a recipe has the Body Designer's Polish face (recipe 11: ten finer shape controls, brow arch and thickness, a saved variation and eyes centred on their openings), and this Body
    Designer knows it. It is a Natural face with an eye profile of its own, so the three profile fields belong to it: `with_face` never sets them, and `face_of` calls it natural."""
    try:
        from bodydesigner.face_identity import PROFILES
    except ImportError:
        return False
    return (isinstance(recipe, dict) and recipe.get("version") == 11 and isinstance(recipe.get("identity"), dict)
            and all(recipe.get(k) == PROFILES[k] for k in ("deformation_profile", "eye_profile")))


def face_of(recipe):
    """"natural" or "classic": the face a recipe has (the Natural face with or without the lip colour is the Natural face; so is the Polish face)."""
    natural = natural_profiles()
    if natural and polish_face(recipe):
        return "natural" if recipe.get("brow_profile") == natural["brow_profile"] and recipe.get("skin_detail_profile") in _natural_skins() else "classic"
    return "natural" if natural and all(recipe.get(k) == natural[k] for k in ("eye_profile", "brow_profile")) and recipe.get("skin_detail_profile") in _natural_skins() else "classic"


def with_hair(recipe, mode, sheet, explicit=True):
    """(recipe with the game hair `mode` asks for, what it did): `sheet` is the hair the sheet's text is (bodymatch.match(...)["hair"]: its style, 'crop', 'sweep', 'long', 'none' for a bald
    head or None for a style the Body Designer has no hair for (then `near`, 'long', when long hair is the nearest it has), and its colour, if it is to be taken). 'sheet' puts the sheet's style on; the colour is the sheet's (else the brows'); a style that has no game
    hair gets the nearest the sheet names (`near`) or else a Crop as a stand-in, said so ({"style", "stand_in": True}), because a bare head is not what a sheet with hair says either. A Body Designer without game hair leaves the recipe
    as it is when the hair was only the default, and refuses when it was asked for."""
    if not offers("hair"):
        if explicit:
            raise ValueError("This Body Designer has no game hair")
        return recipe, None
    bare = {k: v for k, v in recipe.items() if k != "hair"}
    offered = hair_styles()
    style, stand_in = mode, False
    if mode == "sheet":
        style = sheet.get("style")
        if style is None and sheet.get("near") in offered:
            style, stand_in = sheet["near"], True
        elif style is None or (style != "none" and style not in offered):
            style, stand_in = "crop", True
    elif mode != "none" and mode not in offered:
        raise ValueError(f"This Body Designer has no {HAIRS[mode]['label']} hair")
    if style == "none":
        return bare, {"style": "none", "stand_in": False}
    colour = sheet.get("color") or (recipe.get("brows") or {}).get("color") or "#463121"
    return {**bare, "hair": {"version": 1, "style": style, "color": colour}}, {"style": style, "stand_in": stand_in}


def with_face(api, recipe, face, explicit=True):
    """The recipe with the face asked for: all three of the Body Designer's profile fields, because they are never mixed. A Body Designer that has no
    Natural face leaves the recipe as it is when the Natural face was only the default, and refuses when it was asked for. A Polish face is a Natural face with eye and
    deformation profiles of its own: "natural" leaves it as it is and "classic" is refused, since turning it into a classic one would take its finer shapes away without saying so."""
    if polish_face(recipe):
        if face == "natural":
            return recipe
        raise ValueError("A body with the Polish face (from the Body Designer tab) has no classic face. Make a body without the Polish face to have the classic one.")
    if face == "natural":
        natural = natural_profiles()
        if natural is None:
            if explicit:
                raise ValueError("This Body Designer has no Natural face")
            return recipe
        return {**recipe, **natural}
    classic = api.default_recipe(recipe["base"])
    return {**recipe, **{k: classic[k] for k in FACE_FIELDS}}


def with_marks(recipe, mode, npc, explicit=True):
    """(recipe with the skin marks `mode` asks for, what it did): 'sheet' puts the marks the sheet's distinguishing features name (npcgen/marks.py) on the skin, in place of any the recipe had; 'none' clears them. What it did is
    {"text": the marks in words, "skipped": what the sheet names that is not painted, "assumed": the kinds whose places the sheet does not say, "dropped"} for 'sheet' (None for 'none'). A Body Designer that paints no marks
    leaves the recipe as it is when the marks were only the default, and refuses when they were asked for."""
    if not marks.offered():
        if explicit:
            raise ValueError("This Body Designer paints no skin marks")
        return recipe, None
    bare = {k: v for k, v in recipe.items() if k != "appearance"}
    if mode == "none":
        return bare, None
    found = marks.read(npc.get("appearance"), npc.get("id") or "")
    said = {"text": marks.describe(found["marks"]), "skipped": found["skipped"], "assumed": found["assumed"], "dropped": found["dropped"]}
    return ({**bare, "appearance": {"marks": found["marks"]}} if found["marks"] else bare), said


def with_expressions(recipe, wanted):
    """The recipe with the Body Designer's face expressions on (`wanted` true: the Natural face only) or off. A Body Designer without them refuses them and has nothing to switch off."""
    if wanted and not offers("expressions"):
        raise ValueError("This Body Designer has no face expressions")
    if not wanted:
        return {k: v for k, v in recipe.items() if k != "expressions"}
    if face_of(recipe) != "natural":
        raise ValueError("Face expressions need the Natural face")
    return {**recipe, "expressions": True}


def with_secondary(recipe, wanted):
    """The recipe with the swinging parts `wanted` ({"hair": bool, "cloth": bool}) asks for, or with none: the recipe keeps the field only while a part swings, as the Body Designer cleans it. A Body Designer
    without swinging hair and cloth refuses them and has nothing to switch off."""
    bare = {k: v for k, v in recipe.items() if k != "secondary"}
    chosen = {part: bool((wanted or {}).get(part)) for part in SWINGING_PARTS}
    if not any(chosen.values()):
        return bare
    if not offers("secondary"):
        raise ValueError("This Body Designer has no swinging hair or cloth")
    return {**bare, "secondary": chosen}


def swing_problem(recipe, pieces):
    """Why the swinging hair or cloth a recipe asks for cannot be made with the garments it will wear (`pieces`: [{kind, ...}] as npcgen/outfit.py gives them), in words for the person using the page, or None.
    The Body Designer says no to these too, but only at the end of a minute's work."""
    swing = recipe.get("secondary")
    if not swing:
        return None
    kinds = [piece["kind"] for piece in pieces]
    if swing.get("hair") and (recipe.get("hair") or {}).get("style") != "long":
        return "Swinging Long hair needs the Long hair. Choose Long hair, or turn swinging hair off."
    if swing.get("cloth"):
        if len([k for k in kinds if k in SWINGING_CLOTH]) != 1:
            return ("Swinging skirt or dress needs a skirt or a dress on the body. Choose clothes that include one (for “Like the sheet”, the sheet's outfit decides), or turn swinging cloth off.")
        if any(k in SWINGING_OVER for k in kinds):
            return "Swinging skirt or dress is made for a skirt or a dress with nothing worn over it. Put no jacket or apron over it, or turn swinging cloth off."
    return None


# ---- the build ---------------------------------------------------------------------------------------------------------------------
def _kill(proc):
    """Stop a process and everything it started (animate_body_designer_game.py runs Blender)."""
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, check=False)
    else:
        proc.kill()
    try:
        proc.wait(10)
    except subprocess.TimeoutExpired:
        pass


def _run(command, tmp, name, doing, timeout, cancel=None):
    """Run a command, stoppable by `cancel`; returns its log. Raises BodyError with the last useful line of the log when it fails."""
    log_path = Path(tmp) / f"{name}.log"
    with open(log_path, "wb") as log:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        proc = subprocess.Popen(command, cwd=str(cfgmod.ROOT), stdout=log, stderr=subprocess.STDOUT, creationflags=flags)
        started = time.monotonic()
        while proc.poll() is None:
            if cancel is not None and cancel.is_set():
                _kill(proc)
                raise Cancelled()
            beat(cancel)                                    # (still running: the watchdog's clock)
            if time.monotonic() - started > timeout:
                _kill(proc)
                raise BodyError(f"{doing} took longer than {timeout // 60} minutes and was stopped")
            time.sleep(0.25)
    text = log_path.read_text("utf-8", errors="replace")
    if proc.returncode != 0:
        raise BodyError(f"{doing} failed: " + _failure(text))
    return text


def _failure(text):
    """The reason a command gave: its `error:` line (argparse), else the last line."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for line in reversed(lines):
        found = re.search(r"error: (.*)", line)
        if found:
            return found.group(1)
    return lines[-1] if lines else "no output"


def glb_json(data):
    """The JSON chunk of a GLB, or BodyError."""
    if len(data) < 20 or data[:4] != b"glTF":
        raise BodyError("The body is not a GLB file")
    length = struct.unpack_from("<I", data, 12)[0]
    return json.loads(data[20:20 + length])


def _dress(plain, animated, wanted, cancel=None):
    """(plain, animated, {"worn", "failed"}): the two files in the garments of `wanted` ([{kind, colour}], npcgen/outfit.py), fitted in a process of its own (about half a minute a
    garment; Cancel stops it). `animated` None dresses the plain file only (a body with face shapes is dressed before the clips are copied on). A garment that does not fit this body is left
    off and in "failed"; a process that fails is a BodyError."""
    with tempfile.TemporaryDirectory(prefix="npcforge-outfit-") as tmp:
        tmp = Path(tmp)
        (tmp / "plain.glb").write_bytes(plain)
        (tmp / "pieces.json").write_text(json.dumps(wanted), "utf-8")
        command = [sys.executable, "-m", "npcgen.outfit", "--plain", str(tmp / "plain.glb"), "--pieces", str(tmp / "pieces.json"), "--out-plain", str(tmp / "plain_out.glb"), "--report", str(tmp / "report.json")]
        if animated is not None:
            (tmp / "animated.glb").write_bytes(animated)
            command += ["--animated", str(tmp / "animated.glb"), "--out-animated", str(tmp / "animated_out.glb")]
        _run(command, tmp, "outfit", "Fitting the clothes", DRESS_SECONDS, cancel)
        return (tmp / "plain_out.glb").read_bytes(), (tmp / "animated_out.glb").read_bytes() if animated is not None else None, json.loads((tmp / "report.json").read_text("utf-8"))


def _copy(blender, library, plain, tmp, cancel=None):
    """(animated GLB bytes, the copier's report): the body with the animation library's clips and our Wave on its skeleton, copied by Blender in a process of its own (about 10 to 35 s; Cancel kills it)."""
    (tmp / "plain.glb").write_bytes(plain)
    _run([sys.executable, str(COPIER), "--source", str(tmp / "plain.glb"), "--out", str(tmp / "animated.glb"), "--report", str(tmp / "copy.json"),
          "--blender", blender, "--library", str(Path(library[0]).parent), "--work", str(tmp / "work")],
         tmp, "copy", "Copying the animation clips", COPY_SECONDS, cancel)
    return (tmp / "animated.glb").read_bytes(), json.loads((tmp / "copy.json").read_text("utf-8"))


def _shapes(plain, recipe):
    """The dressed neutral body with the Body Designer's face shapes and eye pivots on it (bodydesigner/expressions.py, in this process: about a third of a second). BodyError when it refuses."""
    try:
        from bodydesigner import expressions
        return expressions.apply(plain, recipe)
    except ImportError:
        raise BodyError("This Body Designer has no face expressions") from None
    except ValueError as exc:
        raise BodyError(f"The face shapes could not be added: {exc}") from None


def _face_shapes(doc):
    """The names of the face shapes (morph targets) the body mesh of a body's glTF carries, [] for a body without them."""
    return [str(name) for name in ((doc["meshes"][0].get("extras") or {}).get("targetNames") or [])] if doc.get("meshes") else []


def _swing(animated, recipe, worn):
    """(the animated body with the chains of spring bones the recipe's `secondary` asks for, a report {made: {hair, cloth}, left_off: {part: why}, parts, joints, core_joints, chains, triangles, file_bytes, sha256,
    seconds}): the Body Designer's finalizer (bodydesigner/secondary.py), the last step of a build, after the underwear, the clothes, the face shapes and the copy of the clips (under 2 s). The file it makes must
    never go into a fitter or a copier again: a body that swings is built again from its recipe. `worn` is the kinds of garment that were put on: the cloth swings on the skirt or the dress, and when that did not
    fit this body there is nothing to swing on, so the cloth is left off and said, and the rest is made (as for a garment that does not fit); so is the cloth the Designer itself will not swing (it swings
    only the original closed fabric of a skirt or a dress, and a dress that was adjusted to fit a body shape's bust is not that). BodyError when the Body Designer refuses what is left."""
    wanted = {part: bool((recipe.get("secondary") or {}).get(part)) for part in SWINGING_PARTS}
    left_off = {}
    if wanted["cloth"] and not worn & set(SWINGING_CLOTH):
        wanted["cloth"], left_off["cloth"] = False, "the skirt or dress it was to swing on did not fit this body"
    if not any(wanted.values()):
        return animated, {"made": wanted, "left_off": left_off}
    started = time.monotonic()
    try:
        from bodydesigner import secondary
        while True:
            try:
                final = secondary.apply(animated, {**recipe, "secondary": wanted})
                found = secondary.describe(final)
                break
            except ValueError as exc:
                if wanted["cloth"] and str(exc).startswith("secondary.cloth"):      # (the dress is on the body, and the Designer will not swing it: the hair is still made)
                    wanted["cloth"], left_off["cloth"] = False, f"the Body Designer would not make this skirt or dress swing ({exc})"
                    if not any(wanted.values()):
                        return animated, {"made": wanted, "left_off": left_off}
                    continue
                raise BodyError(f"The swinging {' and '.join(part for part in SWINGING_PARTS if wanted[part])} could not be added: {exc}") from None
    except ImportError:
        raise BodyError("This Body Designer has no swinging hair or cloth") from None
    said = found.get("secondary") or {}
    return final, {"made": wanted, "left_off": left_off, "parts": list(said.get("parts") or []), "joints": found["joints"], "core_joints": said.get("core_joints"),
                   "chains": list(said.get("chains") or []), "triangles": found["triangles"], "file_bytes": found["file_bytes"], "sha256": found["sha256"],
                   "seconds": round(time.monotonic() - started, 1)}


def _body_shape(plain, recipe, block, skeleton):
    """Apply the Designer's saved body shape and retain its provenance."""
    started = time.monotonic()
    api = body_shape_api()
    if api is None:
        raise BodyError("This Body Designer has no body shapes")
    neutral = {k: v for k, v in recipe.items() if k not in ("expressions", "secondary")}               # (the recipe the neutral body was built from: the shape step checks the file against it)
    try:
        made, info = api.apply(plain, {"format": api.FORMAT, "version": api.VERSION, "recipe": neutral, "skeleton": skeleton, "seed": 0, "body": block})
        chest = info.get("chest") or {}
        before, after = (chest.get("anchor_before_m") or [None])[0], (chest.get("anchor_after_m") or [None])[0]       # (the tip of the left breast, metres: before the shape and after it)
        known = bool(before and after)                            # (the Designer's Pectoral form of the male body says no place for a bust: it has none to find)
        reach, shift = (after[2] - before[2], after[1] - before[1]) if known else (None, None)
    except ValueError as exc:
        raise BodyError(f"The body shape could not be made: {exc}") from None
    return made, {"form": block["form"], "bust": block["bust"], "moved": chest.get("moved_vertices"), "biggest_mm": chest.get("maximum_mm"),
                  "reach_mm": round(reach * 1000, 1) + 0.0 if known else None,
                  "drop_mm": round(-shift * 1000, 1) + 0.0 if known else None, "body_shape": info["body_shape"],
                  "integrity": info.get("integrity"), "chest_delta": (shift, reach), "seconds": round(time.monotonic() - started, 2)}


def _edge(plain):
    """The body in its underwear with the Body Designer's pure upper-edge repair done (bodydesigner/body_design.py `repair_upper_edge`: the edge of the fresh 1.8 mm layer is turned out of the chest where a
    shaped bust would pinch it; the same object comes back when nothing needs it). Called once, right after the underwear is fitted to a body that has a body shape. BodyError, in words, when it refuses."""
    api = body_shape_api()
    if api is None:
        raise BodyError("This Body Designer has no body shapes")
    try:
        return api.repair_upper_edge(plain)
    except ValueError as exc:
        raise BodyError(f"The underwear could not be fitted to this body shape: {exc}") from None


def _shapes_on_body(dressed, designed, recipe):
    """`_shapes` for a body that has a body shape: the Body Designer's face shapes are worked out only on the body exactly as it made it, so it does that on the native positions and normals (`designed`, the neutral
    file before the shape) and puts the shaped body's own back (bodydesigner/body_design.py `apply_face_shapes`: no bust size copier is used). The garments are on the file all the time, fitted to the shaped body."""
    api = body_shape_api()
    if api is None:
        raise BodyError("This Body Designer has no body shapes")
    try:
        return api.apply_face_shapes(designed, dressed, {k: v for k, v in recipe.items() if k != "secondary"})
    except ImportError:
        raise BodyError("This Body Designer has no face expressions") from None
    except ValueError as exc:
        raise BodyError(f"The face shapes could not be added to a body with a body shape: {exc}") from None


def build(cfg, recipe, skeleton, cancel=None, note=None, texture=2048, npc=None, clothes=(), soft=None, bust=0.0, wear=None, body=None, **features):
    """Build, dress, rig and animate a body. Return the two GLBs and the job reports."""
    note = note or (lambda text: None)
    features.update(soft=soft, bust=bust)
    before_steps = _build_steps("before_clothes")
    final_steps = _build_steps("body_finished")
    active = {**before_steps, **final_steps}
    if any(value and key not in active for key, value in features.items()):
        raise BodyError(EXTENSION_REQUIRED)
    if npc is not None and any(features.values()) and (problem := roll.adult_problem(npc)):
        raise BodyError(problem)
    for name, step in active.items():
        try:
            step.validate(features.get(name), recipe, body)
        except ValueError as exc:
            raise BodyError(str(exc)) from None
    blender = rig3d.find_blender(cfg)
    library = rig3d.library_files(cfg)
    if not blender:
        raise BodyError("Blender was not found. Install it from blender.org, or set \"blender\" in config.json.")
    if not library:
        raise BodyError("The animation library is not installed: run python scripts/install_animations.py")
    if not COPIER.is_file():
        raise BodyError("scripts/animate_body_designer_game.py is missing")
    kinds = dressing(recipe, npc) if wear is None else _known_pieces(wear)
    if recipe.get("secondary") and not kinds:
        raise BodyError("Swinging hair and cloth are added to a body that wears the plain underwear layer, and this one is set to wear none")
    started = time.monotonic()
    shapes = bool(recipe.get("expressions"))
    with tempfile.TemporaryDirectory(prefix="npcforge-body-") as tmp:
        tmp = Path(tmp)
        (tmp / "recipe.json").write_text(json.dumps({k: v for k, v in recipe.items() if k not in ("expressions", "secondary")}), "utf-8")
        note("Building the body from its recipe")
        _run([sys.executable, "-m", "bodydesigner", "game", "--recipe", str(tmp / "recipe.json"), "--out", str(tmp / "plain.glb"),
              "--skeleton", skeleton, "--texture", str(texture), "--report", str(tmp / "build.json")], tmp, "build", "Building the body", BUILD_SECONDS, cancel)
        plain, animated = (tmp / "plain.glb").read_bytes(), None
        reports = {"build": json.loads((tmp / "build.json").read_text("utf-8"))}
        designed = plain                                          # (the body exactly as the Body Designer made it: the face shapes are worked out on that)
        shaped = body is not None
        if shaped:
            note("Shaping its body")
            plain, reports["body_shape"] = _body_shape(plain, recipe, body, skeleton)
        context = {"original": designed, "recipe": recipe, "shape": body, "reports": reports, "note": note}
        for name, step in before_steps.items():
            plain, report = step.apply(plain, features.get(name), **context)
            if report:
                reports[name] = report
        if shaped:
            reports["body_shape"].pop("chest_delta", None)
        late = shapes or shaped                                   # (the clips are copied on last, onto the dressed body: it is the one that is skinned)
        if not late:
            note("Copying the animation clips onto its skeleton")
            animated, reports["copy"] = _copy(blender, library, plain, tmp, cancel)
        if kinds:
            note("Putting its underwear on")
            try:
                plain, worn = underwear.dress(plain, kinds)
                if not late:
                    animated, _ = underwear.dress(animated, kinds)
            except ValueError as exc:
                raise BodyError(f"The body could not be dressed: {exc}") from None
            reports["dressing"] = worn
            if shaped:
                note("Fitting its underwear to the body shape")
                plain = _edge(plain)
        if clothes:
            names = [piece["kind"] for piece in clothes]
            note("Fitting its " + (", ".join(names[:-1]) + " and " + names[-1] if len(names) > 1 else names[0]))
            plain, dressed, reports["clothes"] = _dress(plain, animated, list(clothes), cancel)
            animated = dressed
        if shapes:
            note("Adding its face shapes")
            fit_shapes = next((step.face_shapes for name, step in before_steps.items() if name in reports and step.face_shapes), None)
            if shaped:
                plain = _shapes_on_body(plain, designed, recipe)
            elif fit_shapes:
                plain = fit_shapes(plain, designed, recipe)
            else:
                plain = _shapes(plain, {k: v for k, v in recipe.items() if k != "secondary"})
            reports["build"] = {**reports["build"], "morph_targets": len(_face_shapes(glb_json(plain)))}
        if late:
            note("Copying the animation clips onto its skeleton")
            animated, reports["copy"] = _copy(blender, library, plain, tmp, cancel)
        if recipe.get("secondary"):
            note("Adding the spring bones that swing")
            animated, reports["swing"] = _swing(animated, recipe, {w["kind"] for w in (reports.get("clothes") or {}).get("worn", [])})
        for name, step in final_steps.items():
            animated, report = step.apply(animated, features.get(name), **context)
            if report:
                reports[name] = report
    reports["seconds"] = round(time.monotonic() - started, 1)
    return plain, animated, reports


def _chains_problem(body, rig, core, parts):
    """Why the animated body is not the plain body with the chains of spring bones of `parts` (a set of "hair" and "cloth") added to it, in words, or None. The animated skeleton starts with the body's own
    `core` bones and goes on with the chains (18 bones for the hair, 24 for the cloth, named secondary_...), every other node is the body's own (a chain is hung on a bone as one more child of it) and the
    file says what it added."""
    wrong = "The animated body has not got the swinging chains that were asked for"
    joints, own = rig["skins"][0]["joints"], body["skins"][0]["joints"]
    count = core + (18 if "hair" in parts else 0) + (24 if "cloth" in parts else 0)
    said = ((rig.get("asset") or {}).get("extras") or {}).get("secondary") or {}
    names = [str(rig["nodes"][i].get("name", "")) for i in joints[core:]]
    if (len(joints) != count or joints[:core] != own or said.get("joints") != count or said.get("core_joints") != core or names != said.get("bone_names")
            or len(set(names)) != len(names) or not all(name.startswith("secondary_") for name in names)):
        return wrong
    first = len(body["nodes"])
    if len(rig["nodes"]) != first + count - core:
        return wrong
    for mine, theirs in zip(body["nodes"], rig["nodes"]):
        kept, children = mine.get("children", []), theirs.get("children", [])
        if {k: v for k, v in mine.items() if k != "children"} != {k: v for k, v in theirs.items() if k != "children"} or children[:len(kept)] != kept or any(c < first for c in children[len(kept):]):
            return "The animated body's skeleton is not the body's own"
    return None


def _shape_problem(data, block, bust):
    """Check that the supplied body shape is recorded in the GLB."""
    extras = (glb_json(data).get("asset") or {}).get("extras") or {}
    if block is None:
        return "The body has a body shape that was not asked for" if "body_shape" in extras else None
    api = body_shape_api()
    if api is None:
        return "This Body Designer has no body shapes"
    try:
        expected = api.provenance(block)
    except (ValueError, KeyError, TypeError):
        return "The body shape that was asked for cannot be described"
    if extras.get("body_shape") != expected:
        return "The body has not got the body shape that was asked for"
    return None


def check(plain, animated, skeleton, expressions=False, swinging=None, soft=None, bust=0.0, shape=None, **features):
    """Check age, rig, animations and any installed build steps before using the files."""
    body, rig = glb_json(plain), glb_json(animated)
    extras = (body.get("asset") or {}).get("extras") or {}
    if extras.get("export_tier") != "game":
        raise BodyError("The Body Designer did not make a game body")
    for doc in (body, rig):
        minimum_age = ((doc.get("asset") or {}).get("extras") or {}).get("minimum_age")
        if type(minimum_age) is not int or minimum_age < roll.ADULT_AGE:
            raise BodyError("The body does not say it is an adult's, so it was not used")
    if extras.get("skeleton") != skeleton or len(body["skins"][0]["joints"]) != SKELETONS[skeleton]["bones"]:
        raise BodyError(f"The body has not got the {skeleton} skeleton that was asked for")
    clips = []
    for animation in rig.get("animations") or []:
        said = animation.get("extras") or {}
        clips.append({"name": animation["name"], **{k: said[k] for k in ("seconds", "loop", "source", "moves", "speed_m_s") if k in said}})
    if len(clips) != 85 or not {"Idle", "Walk", "Wave"} <= {c["name"] for c in clips}:
        raise BodyError(f"The animated body has {len(clips)} clips, not the 85 of the library with Wave")
    for data in (plain, animated):
        problem = _shape_problem(data, shape, bust)
        if problem:
            raise BodyError(problem)
    features.update(soft=soft, bust=bust)
    active = _active_steps()
    if any(value and key not in active for key, value in features.items()):
        raise BodyError(EXTENSION_REQUIRED)
    chains = {part for part, on in (swinging or {}).items() if on}
    before = rig
    for name, step in active.items():
        if step.normalize_rig:
            before = step.normalize_rig(before, features.get(name))
    if not chains:
        if before["skins"][0]["joints"] != body["skins"][0]["joints"] or before["nodes"] != body["nodes"]:
            raise BodyError("The animated body's skeleton is not the body's own")
    else:
        problem = _chains_problem(body, before, SKELETONS[skeleton]["bones"], chains)
        if problem:
            raise BodyError(problem)
    for name, step in active.items():
        step.check(plain, animated, before, features.get(name), shape)
    if expressions:
        wanted = (expressions_info() or {}).get("presets")
        for doc in (body, rig):
            eyes = [n for n in doc["nodes"] if str(n.get("name", "")).startswith("ForgeEye") and (n.get("extras") or {}).get("gaze")]
            if not wanted or _face_shapes(doc) != wanted or len(eyes) != 2:
                raise BodyError("The body has not got the face shapes and eye pivots that were asked for")
    return clips


def preview(cfg, plain):
    """A picture of the body for the card (PNG bytes), or None: a picture that cannot be drawn must not undo a body that was made."""
    try:
        return itemposter.render(cfg, plain, view="three_quarter", size=(480, 720), samples=24)
    except (ComfyError, OSError):
        return None


def store(lib, npc_id, plain, animated, clips, request, reports, picture=None):
    """Make the body the character's 3D model and rig: the two files, the picture, the records. Whatever the old model left (its original, its
    clean-up board, its head close-ups) is removed once the new one is in place. Raises BodyError, having written nothing, when a model appeared
    meanwhile and `replace` was not allowed."""
    recipe, skeleton = request["recipe"], request["skeleton"]
    swing = reports.get("swing") or {}
    if "swing" in reports:                                        # (the recipe says what swings, which is not always what was asked for: a skirt that did not fit leaves its cloth off)
        recipe = with_secondary(recipe, swing["made"])
    swung = [part for part in SWINGING_PARTS if (swing.get("made") or {}).get(part)]
    soft = reports.get("soft") or {}
    soft_parts = list(soft.get("made") or {})
    bust = reports.get("bust") or {}
    shape = reports.get("body_shape") or {}                      # (a body that has a body shape: the body block it was made from is in the request)
    made, build_report = now(), reports["build"]
    wears = reports.get("dressing") or {}                         # the underwear it was dressed in (npcgen/underwear.py)
    dressed = reports.get("clothes") or {}                        # and the garments over it (npcgen/outfit.py)
    clothes = dressed.get("worn") or []
    triangles = (build_report.get("triangles") or 0) + wears.get("triangles", 0) + sum(c.get("triangles", 0) for c in clothes)
    old = {}
    plain = media.label(plain, ai_generated=False)
    animated = media.label(animated, ai_generated=False)

    def apply(npc):
        assets = npc.setdefault("assets", {})
        have = (assets.get("model_3d") or {}).get("file")
        if have and not request["replace"]:
            raise BodyError("This character got a 3D model while the body was being made, so it was not replaced")
        lib.archive_model_into(npc, npc_id)                       # (the model this body replaces, with its skeleton, is kept among the earlier ones)
        old.update({k: assets[k] for k in ("model_3d", "rig_3d") if k in assets})
        lib.write_asset(npc_id, MODEL_FILE, plain)                # (inside the update: the check above, the files and the records are one step)
        lib.write_asset(npc_id, RIG_FILE, animated)
        if picture:
            lib.write_asset(npc_id, PREVIEW_FILE, media.label(picture, ai_generated=False))
        height = (build_report.get("size_m") or [0, 0, 0])[1]
        model = {"file": MODEL_FILE, "created": made, "size": len(plain), "format": "glb", "textured": True,
                 "engine": "Body Designer: " + request["base_label"], "engine_id": "bodydesigner", "faces": triangles,
                 "designed": {"base": recipe["base"], "label": request["base_label"], "skeleton": skeleton, "recipe": recipe, "face": face_of(recipe),
                              "texture": request.get("texture", max(TEXTURES)), "height_m": round(height, 3), "triangles": build_report.get("triangles"),
                              "sha256": build_report.get("sha256"), "from_saved": request["saved"], "seconds": reports["seconds"],
                              **({"matched": request["matched"]} if request.get("matched") else {}), **({"wears": wears} if wears.get("kinds") else {}),
                              **({"clothes_choice": request["clothes"], "underwear": list(request["wear"])} if request.get("wear") is not None else {}),
                              **({"clothes": [{k: c[k] for k in ("kind", "colour", "version", "ease_mm", "length", "texture") if k in c} for c in clothes]} if clothes else {}),
                              **({"clothes_failed": dressed["failed"]} if dressed.get("failed") else {}),
                              **({"clothes_left": request["left"]} if request.get("left") else {}),
                              **({"clothes_why": request["why"]} if request.get("why") else {}),
                              **({"hair": request["hair_said"]} if request.get("hair_said") else {}),
                              **({"marks": request["marks_said"]} if request.get("marks_said") else {}),
                              **({"expressions": True} if recipe.get("expressions") else {}),
                              **({"swinging": {"parts": swing["parts"], "joints": swing["joints"], "core_joints": swing["core_joints"], "chains": swing["chains"]}} if swung else {}),
                              **({"swinging_left_off": swing["left_off"]} if swing.get("left_off") else {}),
                              **({"soft": {"request": request["soft"], "made": soft["made"], "bones": soft["bones"], "joints": soft["joints"], "chains": soft["chains"]}} if soft_parts else {}),
                              **({"bust": {"request": bust["size"], "reach_mm": bust["reach_mm"], "moved": bust["moved"]}} if bust else {}),
                              **({"body": request["body"], "body_shape": {"form": shape["form"], "bust": shape["bust"], "profile": shape["body_shape"]["profile"], "sha256": shape["body_shape"]["body_sha256"],
                                                                           "reach_mm": shape["reach_mm"], "drop_mm": shape["drop_mm"], "moved": shape["moved"]}} if shape else {})}}
        if picture:
            model["preview"] = PREVIEW_FILE
        rig = {"file": RIG_FILE, "created": made, "size": len(animated), "format": "glb", "source_model_created": made, "clips": [c["name"] for c in clips],
               "clip_info": clips, "height_m": round(height, 3), "bones": soft["joints"] if soft_parts else swing["joints"] if swung else SKELETONS[skeleton]["bones"], "faces": triangles,
               "skeleton": skeleton, "joints_from": "designed body", "clip_set": "library", "animation_library": rig3d.LIBRARY,
               **({"core_bones": SKELETONS[skeleton]["bones"]} if swung or soft_parts else {}), **({"swinging": swing["parts"]} if swung else {}), **({"soft": soft_parts} if soft_parts else {}),
               "notes": [_what_it_has(recipe, wears, clothes, swing["parts"] if swung else (), soft_parts, bust.get("size", 0.0), shape)]}
        assets["model_3d"], assets["rig_3d"] = model, rig

    lib.update(npc_id, apply)
    reused = {MODEL_FILE, RIG_FILE, PREVIEW_FILE if picture else None}
    leftovers = [rel for meta in old.values() for rel in (meta.get("file"), meta.get("raw"), meta.get("preview"))]
    leftovers += [cleanup3d.ORIGINAL_FILE, check3d.BOARD_FILE, "model_3d_rigged.png"] + ([] if picture else [PREVIEW_FILE])
    for rel in leftovers:                                         # what the old model left that is not this body's (its original, clean-up board...)
        if rel and rel not in reused:
            try:
                lib.resolve(npc_id, rel).unlink()
            except (FileNotFoundError, KeyError, OSError):
                pass
    shutil.rmtree(lib.resolve(npc_id, "model_3d_head"), ignore_errors=True)    # and its sharper face's close-ups


def recolour(cfg, lib, npc_id, colours):
    """Put new colours on the garments a designed body wears, without fitting anything again: `colours` is {kind: '#rrggbb'} for garments the body wears. A garment's colour is only its cloth
    texture, so both of the character's files get the texture the Body Designer makes for that colour (npcgen/clothing.py `recolour`), the record of the design says the new colours, and the
    card's picture is drawn again (`cfg` is the configuration for Blender; None leaves the picture as it was). Returns {"changed": {kind: colour}, "clothes": [...], "preview": whether the
    picture was drawn again}. Raises ValueError, having changed nothing, for a body that is not a designed body, a garment it does not wear or a colour that is not one. Anything the
    Items tab kept for this body is out of date afterwards (the body file is a new one), as it is when the body is made again."""
    from . import clothing
    wanted = outfit.clean_colours(colours)
    if not wanted:
        raise ValueError("Say which garment to recolour: a colour like #3f6ba8 for each")
    assets = lib.get(npc_id).get("assets") or {}
    model, rig = assets.get("model_3d") or {}, assets.get("rig_3d") or {}
    designed = model.get("designed")
    if not designed or not model.get("file"):
        raise ValueError("This character's 3D model is not a designed body")
    worn = {c["kind"]: c for c in designed.get("clothes") or []}
    for kind in wanted:
        if kind not in worn:
            raise ValueError(f"This body wears no {kind}" + (f" (it wears {', '.join(worn)})" if worn else ": it wears no clothes"))
    changed = {kind: colour for kind, colour in wanted.items() if colour != worn[kind].get("colour")}
    if not changed:
        return {"changed": {}, "clothes": designed["clothes"], "preview": False}
    files = [model["file"]] + ([rig["file"]] if rig.get("file") and rig.get("joints_from") == "designed body" else [])
    blobs = {rel: lib.read_asset(npc_id, rel) for rel in files}
    entries = clothing._entries()
    for kind, colour in changed.items():
        recipe = {"kind": kind, "colour": colour, "texture": worn[kind].get("texture") or entries[kind]["recipe"]["texture"]}
        blobs = {rel: clothing.recolour(blob, recipe) for rel, blob in blobs.items()}
    blobs = {rel: media.label(blob, ai_generated=False) for rel, blob in blobs.items()}
    picture = preview(cfg, blobs[model["file"]]) if cfg is not None else None
    made, stamp = now(), model.get("created")

    def apply(npc):
        mine = npc.setdefault("assets", {})
        if (mine.get("model_3d") or {}).get("created") != stamp:
            raise BodyError("This character's 3D model changed while its colours were being changed, so nothing was changed")
        for rel, blob in blobs.items():
            lib.write_asset(npc_id, rel, blob)
        if picture:
            lib.write_asset(npc_id, PREVIEW_FILE, media.label(picture, ai_generated=False))
        made_model = mine["model_3d"]
        made_model.update({"created": made, "size": len(blobs[model["file"]])})
        made_model["designed"]["clothes"] = [{**c, **({"colour": changed[c["kind"]]} if c["kind"] in changed else {})} for c in made_model["designed"].get("clothes") or []]
        if len(blobs) > 1:
            mine["rig_3d"].update({"created": made, "size": len(blobs[rig["file"]]), "source_model_created": made})

    lib.update(npc_id, apply)
    return {"changed": changed, "clothes": lib.get(npc_id)["assets"]["model_3d"]["designed"]["clothes"], "preview": bool(picture)}


def needs_rebuild(designed):
    """The reason a designed body cannot be dressed in place, in words ("face shapes", "swinging hair or cloth", "soft-body motion", or two or three of them), or "" when it can. A body with face shapes is refused
    by the Body Designer's fit (morph targets), and a body with chains of spring bones must never go into a fitter or the clip copier again (bodydesigner/secondary.py, npcgen/softbody.py): the Items tab's
    try-on builds any of them again from its recipe (`refit`)."""
    recipe = (designed or {}).get("recipe") or {}
    return " and ".join(w for w, on in (("face shapes", recipe.get("expressions")), ("swinging hair or cloth", recipe.get("secondary")), ("soft-body motion", (designed or {}).get("soft"))) if on)


def refit(cfg, npc, pieces, cancel=None, note=None):
    """(the character's animated body wearing `pieces` ([{kind, colour, ease_mm, length, texture}]) in place of what it wears of those kinds, the report {"worn", "failed", "replaced", ["swinging_left_off"]}): for a
    body that `needs_rebuild` (face shapes, or chains of spring bones), which cannot be dressed in place, so the Items tab's try-on builds the body again from the recipe the character was made from, with the garments
    it was made wearing that stay on it (`outfit.kept`) and these, gives it its face shapes and spring bones and copies the clips on (`build`: about two minutes, against half a minute for a fit). The swinging hair stays
    (it does not depend on the clothes); the swinging cloth stays only while the body still wears a skirt or a dress with nothing over it, else it is left off in this try-on and the report says so
    (`swinging_left_off`). The soft-body motion stays as it was asked for (its request is in `designed["soft"]`): the new garment gets the skin weights of the skin under it. So does the bust size (`designed["bust"]`), and the
    underwear of a choice of clothes a local extension added (`designed["underwear"]`). BodyError when the body's design was not kept, when a garment it wears does not fit again or when the build fails; nothing of the character is changed here."""
    designed = ((npc.get("assets") or {}).get("model_3d") or {}).get("designed") or {}
    if extension_notice(npc):
        raise BodyError(EXTENSION_REQUIRED)
    recipe = designed.get("recipe")
    if not isinstance(recipe, dict) or designed.get("skeleton") not in SKELETONS:
        raise BodyError("This body's design was not kept, so it cannot be built again with this garment")
    kinds = {piece["kind"] for piece in pieces}
    wears = [c for c in designed.get("clothes") or [] if isinstance(c, dict) and c.get("kind") in outfit.NODES]
    keep = outfit.kept({c["kind"] for c in wears}, kinds)
    swing, left_off = recipe.get("secondary") or {}, {}
    after = keep | kinds                                          # (what the body will wear)
    if swing.get("cloth") and (len(after & set(SWINGING_CLOTH)) != 1 or after & set(SWINGING_OVER)):
        recipe = with_secondary(recipe, {**swing, "cloth": False})
        left_off["cloth"] = "this try-on has no skirt or dress with nothing over it to swing on"
    wanted = [{k: c[k] for k in ("kind", "colour", "ease_mm", "length", "texture") if c.get(k) is not None} for c in wears if c["kind"] in keep] + [dict(piece) for piece in pieces]
    soft = (designed.get("soft") or {}).get("request")
    bust = (designed.get("bust") or {}).get("request")
    wear = designed.get("underwear") if isinstance(designed.get("underwear"), list) else None      # (a body made with a choice of clothes a local extension added keeps the underwear it had)
    shape = designed.get("body") if isinstance(designed.get("body"), dict) else None          # (a body made with a body shape is built again with it: the block the design kept)
    _plain, animated, reports = build(cfg, recipe, designed["skeleton"], cancel, note, texture=designed.get("texture") or max(TEXTURES), npc=npc,
                                      clothes=sorted(wanted, key=lambda piece: outfit.ORDER.index(piece["kind"])), **({"soft": soft} if soft else {}), **({"bust": bust} if bust else {}),
                                      **({"wear": wear} if wear is not None else {}), **({"body": shape} if shape else {}))
    made = reports.get("clothes") or {"worn": [], "failed": {}}
    lost = sorted(keep - {w["kind"] for w in made["worn"]})
    if lost:
        raise BodyError(f"The body could not be built again with the {lost[0]} it wears: {made['failed'].get(lost[0]) or 'it did not fit'}")
    left_off.update((reports.get("swing") or {}).get("left_off") or {})
    return animated, {"worn": [w for w in made["worn"] if w["kind"] in kinds], "failed": {k: v for k, v in made["failed"].items() if k in kinds},
                      "replaced": sorted({c["kind"] for c in wears} - keep), **({"swinging_left_off": left_off} if left_off else {})}


WORN_AS = {"shirt": "a sleeveless shirt", "trousers": "fitted trousers", "skirt": "a knee-length skirt", "dress": "a sleeveless knee-length dress", "jacket": "an open jacket", "apron": "a bib apron"}


def joined(words):
    """Words as a list in a sentence: "a", "a and b", "a, b and c"."""
    words = list(words)
    return ", ".join(words[:-1]) + (" and " if len(words) > 1 else "") + words[-1] if words else ""


def _what_it_has(recipe, wears, clothes, swinging=(), soft=(), bust=0.0, shape=None):
    """One sentence for the rig's notes: what the body is, and what is a layer of its own. `swinging` is the parts that have spring bones ("hair", "skirt", "dress"), `soft` the parts with soft-body bones, `bust` the bust size that was made (0: none) and `shape` the report of a body shape (`_body_shape`) if it has one."""
    hair = (recipe.get("hair") or {}).get("style")
    skin = (recipe.get("appearance") or {}).get("marks") or []
    has = "an adult body with eyes" + (f" and {hair.capitalize()} hair" if hair else "")
    has += f", {len(skin)} skin mark{'' if len(skin) == 1 else 's'} painted into its skin" if skin else ""
    has += ", face shapes the engine can blend (a blink, a smile, mouth shapes) and eyes that can look about" if recipe.get("expressions") else ""
    layers = ["plain underwear (a layer of its own: the mesh ForgeUnderwear)"] if wears.get("kinds") else []
    layers += [WORN_AS.get(c["kind"], c["kind"]) for c in clothes]
    if layers:
        text = has + ", wearing " + ", ".join(layers[:-1]) + (" and " if len(layers) > 1 else "") + layers[-1] + (" (layers of their own)" if clothes else "")
    else:
        text = has + (": no clothes or garments are part of it yet" if hair else ": no hair, clothes or garments are part of it yet")
    if bust:
        text += f"; its bust is {'larger' if bust > 0 else 'smaller'} than the Body Designer made it (bust size {bust * 100:+.0f} %, made in the body itself)"
    if shape:
        text += f"; its body has the Body Designer's body shape (chest form {shape['form']}, bust size {shape['bust']:+.2f}), made in the body itself"
    if swinging:
        words = ["its " + ("Long hair" if part == "hair" else part) for part in swinging]
        text += f"; {' and '.join(words)} {'has' if len(words) == 1 else 'have'} spring bones (secondary_...) that the game engine swings; no clip moves them"
    if soft:
        text += f"; its {joined(soft)} {'has' if len(soft) == 1 else 'have'} soft-body spring bones (soft_...) that the game engine moves; no clip moves them"
    return text


def _body(jobs, job, step, npc):
    """The job step: build the body, check it, store it."""
    request = job.options.get("body_3d") or {}
    if not request.get("recipe"):
        raise ComfyError("The designed body has no recipe")

    def note(text):                                               # (the queue shows it beside the step while it runs)
        with jobs.lock:
            step["detail"] = text
        job.touch()
    try:
        if request.get("wear") is not None and not _offered(request.get("clothes")):
            raise BodyError(EXTENSION_REQUIRED)
        additions = {field: request[field] for field in set(LEGACY_BUILD_FIELDS) | _active_steps().keys() if request.get(field)}
        plain, animated, reports = build(jobs.cfg, request["recipe"], request["skeleton"], job.cancel, note, texture=request.get("texture", max(TEXTURES)), npc=npc,
                                         clothes=request.get("pieces") or (), **additions,
                                         **({"wear": request["wear"]} if request.get("wear") is not None else {}), **({"body": request["body"]} if request.get("body") else {}))
        swing = reports.get("swing") or {}
        clips = check(plain, animated, request["skeleton"], expressions=bool(request["recipe"].get("expressions")), swinging=swing.get("made"), soft=(reports.get("soft") or {}).get("made"),
                      bust=(reports.get("bust") or {}).get("size", 0.0), shape=request.get("body") or None,
                      **{field: value for field, value in additions.items() if field not in LEGACY_BUILD_FIELDS})
        note("Drawing a picture of it")
        picture = preview(jobs.cfg, plain)
        store(jobs.lib, job.npc_id, plain, animated, clips, request, reports, picture)
    except BodyError as exc:
        raise ComfyError(str(exc)) from None
    with jobs.lock:
        step.pop("detail", None)
        spring = (f', spring bones on its {" and ".join(("hair" if part == "hair" else part) for part in swing["parts"])}' if swing.get("parts") else "") + (
            f' ({"; ".join(f"no swinging {part}: {why}" for part, why in swing["left_off"].items())})' if swing.get("left_off") else "") + (
            f', soft-body bones on its {joined(reports["soft"]["made"])}' if reports.get("soft") else "") + (
            f', bust size {reports["bust"]["size"] * 100:+.0f} %' if reports.get("bust") else "") + (
            f', body shape (bust size {reports["body_shape"]["bust"]:+.2f})' if reports.get("body_shape") else "")
        step["note"] = f'{request["base_label"]}, {SKELETONS[request["skeleton"]]["label"].lower()}, {len(clips)} clips{spring}, made in {reports["seconds"]:.0f} s.'


RUNNERS = {"body_3d": _body}


def run(jobs, job, step, npc):
    RUNNERS[step["kind"]](jobs, job, step, npc)
