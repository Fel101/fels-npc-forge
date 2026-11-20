"""One click for the best 3D model NPC Forge makes so far.

It ticks options that already exist and adds nothing of its own: the chain that came out best when the same characters were made
every way (docs/model-import.md, docs/detailed-head.md). Each part stays an option of its own, on the Models card and on the 3D
card, because a future generator or LoRA may not need some of them; this is only a shortcut for ticking them together.

`expand` turns the request into one character's own and Jobs.submit calls it first: a detailed head that is already made and
fresh is used as it is (it takes the longest), the rest is made again.
"""
from . import eyesoft, head3d

# Turned on whatever the recipe says: the generator and the settings it was measured with. The faces kept (700k), the detailed
# head's detail (medium: high was twice the time and not a better face) and its neutral face are the same ones the chain ran with.
OPTIONS = {"three_d_model": "trellis2", "three_d_quality": "medium", "three_d_soften_eyes": True, "three_d_glasses_off": True,
           "head_quality": "medium", "head_expression": "neutral"}
# Not a recipe option, only ever on the job: the glasses are taken off and the eyes softened if that can be done, and a face the tools
# cannot read (a muzzle, a helmet) must not stop the whole chain, as it does when either is asked for on its own
JOB_ONLY = {"three_d_pictures_optional": True}

# Asked for: the shape from the four pictures, then the clean-up and paint with these options (cleanup3d.OPTIONS). The
# detailed head they attach is added by `expand` when the character has none.
CLEANUP = ("sharp_face", "match_hair", "clean_hands", "attach_head")
ASSETS = {"model_3d": True, "refine_3d": True, **{flag: True for flag in CLEANUP}}

# What the page and `--help` tell the user, in the order the job runs it
STEPS = ("a Trellis2 shape from the model sheet's four pictures (copies without the glasses for a character who wears them, and the eyes "
         "filled with skin where the shape is made)",
         "a detailed head, if there is none yet",
         "the clean-up and paint from the pictures, with a sharper face, the hair matched to the pictures, clean five-finger hands "
         "and the detailed head attached")

# What the page says about the time; measured on the R9700 (docs/model-import.md)
ESTIMATE = "About 40 minutes for a character with no detailed head yet, about 16 minutes with one."


def head_request(npc):
    """The detailed head this character still needs, as clean_head spells it: False when it has a fresh one, only the model when
    its four close-ups are there (the head is out of date), the close-ups and the model otherwise. A character with glasses needs
    close-ups drawn without them (three_d_glasses_off): a head made with them is drawn again."""
    head = (npc.get("assets") or {}).get("head_3d") or {}
    bare = not eyesoft.glasses_of(npc) or head.get("glasses_off")
    if head.get("file") and not head.get("stale") and bare:
        return False
    views = head.get("views") or {}
    if bare and all((views.get(view) or {}).get("file") for view in head3d.VIEWS):
        return {"views": [], "model": True}
    return True


def expand(npc, assets, options):
    """(assets, options) for this character with the chain ticked. Whatever else the request asks for (a skeleton, expressions)
    stays; a detailed head the request itself asks for is kept as asked."""
    assets = {key: value for key, value in assets.items() if key != "best_3d"}
    assets.update(ASSETS)
    if not assets.get("head_3d"):
        assets["head_3d"] = head_request(npc)
    return assets, {**options, **OPTIONS, **JOB_ONLY}


def availability(status):
    """{"missing": [...]} for the whole chain, from the health report's own entries (server.py): the Trellis2 nodes and models,
    SDPose, the detailed head's models, the upscaler, SAM 3.1 and Blender, each named once."""
    missing = []
    if not status.get("online"):
        missing.append("ComfyUI")
    parts = [(status.get("three_d_missing") or {}).get("trellis2")]
    parts += [(status.get(key) or {}).get("missing") for key in ("head_3d", "sharp_face", "match_hair", "clean_hands", "attach_head")]
    for part in parts:
        missing += [item for item in part or [] if item not in missing]
    return {"missing": missing}
