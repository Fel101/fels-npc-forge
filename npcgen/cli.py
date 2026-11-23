"""Command line interface:  python -m npcgen <command> ...   (run with -h for each command)

  roll       print randomly rolled characters (no images, no ComfyUI needed)
  generate   roll / import / describe characters and generate their assets through ComfyUI
  export      write a finished animation as engine-ready files (sheet, atlas JSON, Godot .tres, preview page)
  export-sheet  write a finished model sheet: every view as its own PNG, the stitched sheet and a JSON record
  reassemble  redo alignment and the quality report from the stored raw frames (no ComfyUI needed)
  list       list the characters in the library
  health     check the ComfyUI connection and the Mage-Flow model files
  item       make things a character holds or wears (weapons, armour, hats, shoes, bags, props, garments, glasses, fitted clothes): list, make, fit, dress, export
"""
import argparse
import io
import json
import pathlib
import sys
import time
import zipfile

from . import __version__
from . import best3d, bodymaker, cast, config, clothing, engines, equip, export, facefit, glasses, head3d, import3d, items, morphology, outfit, prompts, rig3d, roll, service, sheet, three_d
from .app import App
from .comfy import ComfyError

DEFAULT_EXPRESSIONS = ["happy", "sad", "angry", "surprised"]


def _add_choices(p, data, defaults=True):
    p.add_argument("--setting", choices=list(data["settings"]), default=next(iter(data["settings"])))
    p.add_argument("--gender", choices=list(data["common"]["genders"]))
    p.add_argument("--age-group", choices=list(data["common"]["age_groups"]), help="child and teen are only rolled when asked for")
    p.add_argument("--occupation", help="an occupation id from the setting, e.g. nurse")
    p.add_argument("--origin", help="a heritage id (or a race id in the fantasy setting)")
    p.add_argument("--morphology", choices=list(morphology.MORPHOLOGIES), help="body plan to use when rolling characters")


def _constraints(a):
    return {"gender": a.gender, "age_group": a.age_group, "occupation": a.occupation,
            "origin": a.origin, "morphology": a.morphology}


def build_parser(data):
    p = argparse.ArgumentParser(prog="npcgen", description="NPC Forge: a character generator for games, drawn by models in your own ComfyUI.")
    p.add_argument("--version", action="version", version=f"NPC Forge {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("roll", help="print rolled characters")
    r.add_argument("-n", "--count", type=int, default=1)
    _add_choices(r, data)
    r.add_argument("--describe", metavar="TEXT", help="a freeform description instead of random looks")
    r.add_argument("--json", action="store_true", help="print full JSON, one character per line")

    g = sub.add_parser("generate", help="create characters and generate their art")
    src = g.add_mutually_exclusive_group()
    src.add_argument("-n", "--count", type=int, help="roll this many new characters (default 1)")
    src.add_argument("--cast", metavar="FILE", help="a JSON or CSV cast file")
    src.add_argument("--describe", metavar="TEXT", help="a freeform description of one character")
    src.add_argument("--ids", nargs="+", metavar="ID", help="existing library characters")
    _add_choices(g, data)
    g.add_argument("--style", choices=list(data["styles"]), help="art style (new characters default to the first style; existing ones keep theirs)")
    g.add_argument("--name", help="first and last name, when creating a single character")
    g.add_argument("--age", type=int, help="age, when creating a single character")
    g.add_argument("--portrait", action="store_true")
    g.add_argument("--sprite", action="store_true")
    g.add_argument("--model-3d", action="store_true", help="generate a 3D GLB in ComfyUI")
    g.add_argument("--import-3d", metavar="FILE",
                   help="use a 3D model made elsewhere (needs --ids with one character): a .glb on this computer (up to 100 MB), "
                        "or the name of one in ComfyUI's output or input folder, e.g. Elf_00001_.glb or \"3D/Elf_00001_.glb [output]\" "
                        "(the output folder is tried first); see --import-clean")
    g.add_argument("--import-clean", choices=["auto", "yes", "no"], default="auto",
                   help="close the imported mesh's holes and simplify it in ComfyUI (auto: yes for a dense shape with no colour, "
                        "which is what a raw Trellis2 or other generator output is)")
    g.add_argument("--import-detail", choices=list(three_d.PIXAL_QUALITIES), default="medium",
                   help="how many faces a cleaned import keeps: low 350k, medium 700k, high 1M (default medium)")
    g.add_argument("--rig-3d", action="store_true",
                   help="add a skeleton and Idle/Walk/Wave clips to the 3D model with Blender (makes the model first if needed)")
    g.add_argument("--head-3d", action="store_true",
                   help="a detailed head: four close-ups of the head drawn from the portrait and a separate, much finer "
                        "Pixal3D model of it (about 20 minutes; never done unless asked)")
    g.add_argument("--head-quality", choices=list(three_d.HEAD_QUALITIES), default="medium",
                   help="the detailed head's Pixal3D setting (default medium)")
    g.add_argument("--head-expression", choices=list(head3d.EXPRESSIONS), default="neutral",
                   help="the detailed head's face: neutral (best for animating; default) or as in the portrait")
    g.add_argument("--head-match-hair", action="store_true",
                   help="the detailed head's hair is painted only from hair (SAM 3.1 in ComfyUI): keeps skin off hair that "
                        "falls over the face, about 3 minutes more; it can leave grey patches")
    g.add_argument("--refine-3d", action="store_true",
                   help="clean up a Pixal3D model and paint it from the turnaround pictures (never done unless asked)")
    g.add_argument("--portrait-framing", choices=list(prompts.FRAMINGS), default=None,
                   help="how much of the character a portrait shows: bust (head and shoulders, the default), half (to the waist) or full")
    g.add_argument("--engine-fallback", action=argparse.BooleanOptionalAction, default=None,
                   help="--engine-fallback: a picture whose engine cannot draw it (not enough free memory, missing files) is drawn by the built-in Mage-Flow instead, and says so; "
                        "--no-engine-fallback: it fails with the reason (for work that may only use commercial-use engines). The default is config.json's options, else no fallback")
    g.add_argument("--game-size", metavar="SIZE", default=None,
                   help="how many faces the clean-up's game version keeps, for this character: light (25,000), standard (50,000), "
                        "detailed (100,000, the default), high (200,000) or any number from 10,000 to 400,000")
    g.add_argument("--sharp-face", action="store_true",
                   help="the same clean-up, with the head upscaled 4x in ComfyUI and given a finer texture of its own")
    g.add_argument("--check-3d", action="store_true",
                   help="after the clean-up, ask a vision model in LM Studio whether the cleaned-up model is better than the "
                        "generator's original, and what looks wrong (never done unless asked)")
    g.add_argument("--smooth-eyes", action="store_true",
                   help="the same clean-up, with rings sculpted around the eyes smoothed out (finds the eyes with LM Studio; "
                        "only for models that have them: it flattens normal eyes too)")
    g.add_argument("--match-hair", action="store_true",
                   help="the same clean-up, painting the model's hair only from hair in the pictures (finds the hair with "
                        "SAM 3.1 in ComfyUI; for a generator that made the hair bigger than the pictures have it)")
    g.add_argument("--clean-hands", action="store_true",
                   help="the same clean-up, with the generator's mitten-like hands replaced by clean five-finger ones (finds "
                        "the hands with SDPose in ComfyUI; not for paws or claws)")
    g.add_argument("--attach-head", action="store_true",
                   help="the same clean-up, with the detailed head's face laid over the model's own (needs the detailed head, made "
                        "with --head-3d first; finds the faces with SDPose in ComfyUI; not for characters without a human face)")
    g.add_argument("--best-3d", action="store_true",
                   help="the best 3D model so far, in one go: " + "; then ".join(best3d.STEPS) + ". It sets the generator and "
                        "settings itself (--three-d-model, --soften-eyes and the head options are ignored), reuses a fresh detailed "
                        "head and takes about 40 minutes for a character that has none (about 16 with one)")
    g.add_argument("--expressions", nargs="*", metavar="ID", help=f"expression ids; none given = {' '.join(DEFAULT_EXPRESSIONS)}")
    g.add_argument("--animations", nargs="*", metavar="CLIP", help="clip ids, e.g. walk idle; none given = walk")
    g.add_argument("--sheet", nargs="*", choices=list(sheet.VIEWS), metavar="VIEW",
                   help=f"a model sheet (portrait, front, both sides, back on white); optionally only these views: {' '.join(sheet.VIEWS)}")
    g.add_argument("--pose", choices=list(data["sheet"]["poses"]), default=data["sheet"]["default_pose"], help="pose of the model-sheet figure")
    g.add_argument("--dirs", nargs="+", choices=list(service.DIRECTION_ORDER), default=list(service.DIRECTION_ORDER))
    g.add_argument("--frames", type=int, default=4, help="frames per direction for generated clips (4 or 8)")
    g.add_argument("--best-of", type=int, default=1, help="candidates per animation frame; the best is kept (1-4)")
    g.add_argument("--no-mirror-left", action="store_true", help="generate the left view instead of mirroring the right one")
    g.add_argument("--quality", default="standard", help="draft, standard or high")
    g.add_argument("--sprite-mode", choices=["auto", "match", "independent"], default="auto",
                   help="auto: edit the portrait, except freeform characters which are drawn from their description")
    g.add_argument("--transparent-portrait", action="store_true")
    g.add_argument("--opaque-sprite", action="store_true", help="keep the sprite's backdrop instead of cutting it out")
    g.add_argument("--seed", type=int, help="fix the image seed")
    g.add_argument("--image-model", choices=list(engines.IMAGE_ENGINES), default=None,
                   help="the model that draws from text (portraits, independent sprites); the default is config.json's options, else Qwen-Image 2512")
    g.add_argument("--edit-model", choices=list(engines.EDIT_ENGINES), default=None,
                   help="the model that edits (photo portraits, matched sprites, expressions, model sheets, animation); the default is config.json's options, else Qwen-Image-Edit 2511")
    g.add_argument("--three-d-model", choices=list(three_d.GENERATORS), default="hunyuan21",
                   help="the local 3D generator to use (Hunyuan3D v2.1 or textured Pixal3D)")
    g.add_argument("--three-d-quality", choices=list(three_d.PIXAL_QUALITIES), default="medium",
                   help="Pixal3D mesh/texture detail (low, medium, high; default medium)")
    g.add_argument("--three-d-hunyuan-source", choices=list(three_d.HUNYUAN_SOURCES), default="sprite",
                   help="the picture Hunyuan3D is made from: the sprite, or the model sheet's front picture (best if you "
                        "will paint the model from the pictures)")
    g.add_argument("--three-d-hunyuan-detail", choices=list(three_d.HUNYUAN_DETAILS), default="standard",
                   help="how fine Hunyuan3D's shape is: standard, or finer for a smoother face (slower, bigger model)")
    g.add_argument("--three-d-face-source", choices=list(three_d.PIXAL_FACE_SOURCES), default="turnaround",
                   help="Pixal3D close-up face reference (turnaround or experimental portrait)")
    g.add_argument("--soften-eyes", action="store_true",
                   help="Trellis2 only: fill the eyes with skin in the pictures its shape is made from, so the shape has no slits or "
                        "rims round the eyes (the paint still uses the original pictures; needs SDPose in ComfyUI; not done for a character "
                        "with glasses, whose frames would come out as solid lenses)")
    g.add_argument("--glasses-off", action="store_true",
                   help="Trellis2 only: take the glasses off the model-sheet pictures the model is made and painted from, and off the detailed "
                        "head's close-ups (an edit model in ComfyUI does it; the character's own pictures keep them), so the 3D model "
                        "has none: no solid lenses, no doubled arms when the head is attached, and the eyes can be softened. Only "
                        "for a character whose sheet has glasses")
    g.add_argument("--skeleton", choices=list(rig3d.SKELETONS), default="low",
                   help="the skeleton --rig-3d gives the model: low (23 bones) or medium (60: three bones per finger, a jaw, "
                        "arm twist bones and a prop bone in each palm; needs SDPose in ComfyUI)")
    g.add_argument("--clips", choices=["auto", *rig3d.CLIP_SETS], default="auto",
                   help="the clips --rig-3d adds: basic (Idle, Walk, Wave) or library (Quaternius' animation library, "
                        "85 clips; install it with scripts/install_animations.py); auto = the library when installed")
    g.add_argument("--out", metavar="DIR", help="also write finished characters (PNGs + npc.json) to this folder")

    e = sub.add_parser("export", help="write a finished animation as engine-ready files")
    e.add_argument("id", help="library id, or a unique part of the name")
    e.add_argument("--clip", default="walk")
    e.add_argument("--preset", choices=list(export.PRESETS), default="atlas")
    e.add_argument("--out", required=True, metavar="DIR")
    e.add_argument("--frame", type=int, help="frame size in pixels (default depends on the preset)")
    e.add_argument("--dirs", nargs="+", choices=list(service.DIRECTION_ORDER))
    e.add_argument("--pixelate", dest="pixelate", action="store_true", default=None, help="snap to a small palette (default: auto for small pixel-art frames)")
    e.add_argument("--no-pixelate", dest="pixelate", action="store_false")
    e.add_argument("--colors", type=int, default=24)
    e.add_argument("--outline", action="store_true", help="add a 1px dark outline when pixelating")
    e.add_argument("--name", help="file name stem")
    e.add_argument("--res-path", help="Godot resource folder, e.g. res://assets/npcs (godot preset)")

    es = sub.add_parser("export-sheet", help="write a finished model sheet as PNGs plus a JSON record")
    es.add_argument("id", help="library id, or a unique part of the name")
    es.add_argument("--out", required=True, metavar="DIR")
    es.add_argument("--name", help="file name stem")

    ra = sub.add_parser("reassemble", help="redo alignment and the quality report from the stored raw frames (no ComfyUI needed)")
    ra.add_argument("id", help="library id, or a unique part of the name")
    ra.add_argument("--clip", default="walk")

    vo = sub.add_parser("voice", help="give characters a voice (and voice their dialogue): the voice their sheet suggests, "
                                      "takes, the first used until you pick one on the Review page")
    vo.add_argument("who", nargs="+", help="character ids or names, or 'all'")
    vo.add_argument("--engine", default="qwen", help="qwen, breeze or kokoro (default qwen)")
    vo.add_argument("--takes", type=int, default=3, help="takes per character, 1-4 (default 3)")
    vo.add_argument("--lines", action="store_true", help="also voice the dialogue pack (made first if missing)")
    vo.add_argument("--redo", action="store_true", help="new takes even for characters whose voice you already picked")
    it = sub.add_parser("item", help="make things a character holds or wears: weapons, armour, helmets and hats, shoes, bags, props, garments, glasses, fitted clothes")
    isub = it.add_subparsers(dest="item_cmd", required=True)
    isub.add_parser("list", help="list the items")
    im = isub.add_parser("make", help="make an item: a main picture, four views, a Trellis2 shape, then the shape painted and made into a "
                                      "game model (only what is missing or out of date)")
    im.add_argument("--id", help="an item that exists (its id, or a unique part of its id or name); without it a new one is made from the options below")
    im.add_argument("--family", choices=[f["id"] for f in items.catalogue()["families"]], default="weapon")
    im.add_argument("--kind", help="the kind in that family, e.g. cutlass, boots, helmet (default: the family's first)")
    im.add_argument("--name", help="its name (default: the kind's)")
    im.add_argument("--describe", default="", metavar="TEXT", help="what it is, what it is made of, how it looks (default: the kind's own description)")
    im.add_argument("--setting", default="", choices=list(data["settings"]), metavar="SETTING",
                    help="a world it is made for, one of the characters' settings: " + ", ".join(data["settings"]))
    im.add_argument("--look", default="realistic", choices=[look["id"] for look in items.catalogue()["looks"]])
    im.add_argument("--size", type=float, metavar="METRES", help="the longest side of the finished model (default: the kind's usual size)")
    im.add_argument("--faces", type=int, choices=list(items.FACES), default=items.DEFAULT_FACES, help="the game version's faces")
    im.add_argument("--high-views", choices=("on", "off"), help="two more pictures from 45 degrees up, drawn for the paint (on: the top of a helmet, a crate or a "
                                                                "boot's opening is painted from a real picture); default: on for what the kind of thing has a top to show")
    im.add_argument("--no-finish", action="store_true", help="stop at the grey shape: no painting, no game version")
    im.add_argument("--redo", nargs="+", choices=[name for _kind, name, _label in items.STEPS], default=[],
                    help="make these parts again, whatever state they are in")
    im.add_argument("--reroll", action="store_true", help="a new random seed for the parts made (a Redo in the Items tab does this)")
    im.add_argument("--seed", type=int, help="the seed to use")
    im.add_argument("--no-best", action="store_true", help="use the engines below instead of Z-Image Turbo (picture) and Qwen-Image-Edit 2511 (views)")
    im.add_argument("--image-model", choices=list(engines.IMAGE_ENGINES), default=None, help="with --no-best: the engine that draws the main picture (the default is config.json's options, else Qwen-Image 2512)")
    im.add_argument("--edit-model", choices=list(engines.EDIT_ENGINES), default=None, help="with --no-best: the engine that draws the views (the default is config.json's options, else Qwen-Image-Edit 2511)")
    im.add_argument("--three-d-quality", choices=list(three_d.PIXAL_QUALITIES), default="medium", help="Trellis2: the faces the shape keeps")
    im.add_argument("--style", choices=list(glasses.load()["presets"]), help="glasses (family eyewear): the style to start from")
    im.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                    help="glasses: one setting, e.g. lens_w=54, frame_color=#c8a050, lens_tint=green, pose=folded; fitted clothes (family clothing, kind shirt, trousers, skirt, dress, jacket or apron): colour=#a02020 "
                         "or colour=navy, ease_mm=8, length=0.95, texture=weave (repeat for more; the keys are in docs/glasses.md, and for fitted clothes in the Body Designer add-on)")
    ift = isub.add_parser("fit", help="fit a pair of glasses to a character's face (the face is found first, once, and kept): print where they go, "
                                      "keep the fit, write the character wearing them")
    ift.add_argument("id", help="the pair's id, or a unique part of its id or name")
    ift.add_argument("character", help="the character's id, or a unique part of its id or name (it needs a 3D model)")
    ift.add_argument("--up", type=float, metavar="MM", help="nudge: move the pair up this many millimetres (negative: down)")
    ift.add_argument("--forward", type=float, metavar="MM", help="nudge: move the pair forward, away from the face (negative: back)")
    ift.add_argument("--tilt", type=float, metavar="DEG", help="nudge: tip the top of the pair forward this many degrees")
    ift.add_argument("--size", type=float, metavar="TIMES", help="nudge: make the pair this many times bigger than the fit found (0.5 to 2)")
    ift.add_argument("--rescan", action="store_true", help="find the face again even if it was found for this model")
    ift.add_argument("--estimate", action="store_true", help="place the eyes by the usual proportions instead of finding them with SDPose (no GPU)")
    ift.add_argument("--save", action="store_true", help="keep the nudge as this pair's fit on this character (the fit shown in the Items tab)")
    ift.add_argument("--out", metavar="DIR", help="write the character wearing the pair, as <character>-<pair>.glb, into this folder")
    idr = isub.add_parser("dress", help="put fitted clothes (family clothing) on a designed body: fits them (about half a minute) and writes the character wearing them")
    idr.add_argument("id", help="the garment's id, or a unique part of its id or name")
    idr.add_argument("character", help="the character's id, or a unique part of its id or name (its 3D model must be a designed body)")
    idr.add_argument("--refit", action="store_true", help="fit it again even if a fit that is still good is kept")
    idr.add_argument("--out", metavar="DIR", help="write the character wearing it, as <character>-<garment>.glb, into this folder")
    ieq = isub.add_parser("equip", help="put held items and hats on a rigged character (a sword in the right hand, a shield on the left forearm, a helmet or hat on the head): "
                                        "prints the bone and transform for each, and writes the character holding them")
    ieq.add_argument("character", help="the character's id, or a unique part of its id or name (it needs a rigged 3D model)")
    ieq.add_argument("items", nargs="+", metavar="ITEM[=SLOT]",
                     help="each item's id (or a unique part of its id or name), then =hand_r, =hand_l or =head to choose where it goes (its kind's usual place when left out)")
    ieq.add_argument("--up", type=float, metavar="M", help="nudge (one item only): move it up this many metres (negative: down)")
    ieq.add_argument("--forward", type=float, metavar="M", help="nudge: move it forward, the way the character faces (negative: back)")
    ieq.add_argument("--side", type=float, metavar="M", help="nudge: move it toward the character's right (negative: left)")
    ieq.add_argument("--turn", type=float, metavar="DEG", help="nudge: turn it about its own long axis, or about the vertical for a hat")
    ieq.add_argument("--tilt", type=float, metavar="DEG", help="nudge: tip it up (positive) or down")
    ieq.add_argument("--size", type=float, metavar="TIMES", help="nudge: make it this many times bigger than placed (0.4 to 2.5)")
    ieq.add_argument("--save", action="store_true", help="keep the slot and nudge as this item's way of being carried by this character (nothing is put on the character)")
    ieq.add_argument("--out", metavar="DIR", help="write the character holding them, as <character>-equipped.glb, into this folder")
    ie = isub.add_parser("export", help="write an item as a ZIP: its record, pictures and 3D model")
    ie.add_argument("id", help="the item's id, or a unique part of its id or name")
    ie.add_argument("--out", required=True, metavar="DIR")
    bo = sub.add_parser("body", help="give a character a designed body as its 3D model: the Body Designer's adult body, rigged and animated, "
                                     "with no GPU")
    bsub = bo.add_subparsers(dest="body_cmd", required=True)
    bsub.add_parser("list", help="list the bodies the Body Designer can build, and what is missing")
    bc = bsub.add_parser("colours", help="put new colours on the garments a character's designed body wears, without making it again (a few seconds)")
    bc.add_argument("character", help="the character's id, or a unique part of its id or name (its 3D model must be a designed body)")
    bc.add_argument("--colour", action="append", default=[], required=True, metavar="GARMENT=#RRGGBB", help="a colour for a garment the body wears, e.g. shirt=#3f6ba8 (repeat for more)")
    bu = bsub.add_parser("use", help="build a body and make it a character's 3D model and rig (about a minute, CPU only)")
    bu.add_argument("character", help="the character's id, or a unique part of its id or name (a two-legged one)")
    bu.add_argument("--base", metavar="ID", help="the body (see: body list); the default suits the character's gender")
    bu.add_argument("--skeleton", choices=list(bodymaker.SKELETONS), default="low", help="low: 23 bones; medium: 60, with fingers, a jaw and twist bones")
    bu.add_argument("--face", choices=list(bodymaker.FACES), help="natural (the default for a new body) or classic; a saved body keeps the face it was saved with")
    bu.add_argument("--texture", type=int, choices=list(bodymaker.TEXTURES), default=max(bodymaker.TEXTURES),
                    help="the skin's longest edge in pixels: 2048 (the sharpest, the default), 1024 or 512")
    bu.add_argument("--set", action="append", default=[], metavar="PROPORTION=VALUE",
                    help="one proportion from -1 to 1, e.g. height=0.2, muscle=-0.3 (repeat for more; the Body Designer add-on lists the 21)")
    bu.add_argument("--saved", action="store_true", help="take the proportions the Body tab saved for the character")
    bu.add_argument("--no-match", action="store_true", help="do not give a new body the build, skin, eye and brow colours of the character's sheet (neutral body)")
    bu.add_argument("--clothes", choices=list(bodymaker.CLOTHES), default="basic",
                    help="basic: a shirt and trousers in the colours the sheet's outfit names (a skirt it names takes the place of the trousers, a dress the place of both), and a jacket or an apron over them when it names one (the default); sheet: only the ones it names; "
                         "none: underwear only")
    bu.add_argument("--outer", choices=list(outfit.OUTERS), default="sheet",
                    help="what goes over the shirt and trousers: sheet (the jacket or apron the sheet's outfit names, the default), none, jacket or apron")
    bu.add_argument("--colour", action="append", default=[], metavar="GARMENT=#RRGGBB",
                    help="a colour for one garment (shirt, trousers, skirt, dress, jacket or apron) instead of the sheet's or the Body Designer's own, e.g. jacket=#634c3e (repeat for more)")
    bu.add_argument("--hair", choices=list(bodymaker.HAIRS),
                    help="sheet: Crop, Sweep or Long like the sheet's hair, or none when it is bald (the default for a new body; a saved body keeps its hair); none, crop, sweep or long")
    bu.add_argument("--marks", choices=list(bodymaker.MARKS),
                    help="sheet: the skin marks the sheet names (freckles, vitiligo, moles, beauty marks, birthmarks, scars), painted into the skin for good (the default for a new body; a saved body keeps its marks); none: clear skin")
    bu.add_argument("--expressions", action=argparse.BooleanOptionalAction, default=None,
                    help="give the body the Body Designer's face shapes (a blink, happy, angry, sad, relaxed, surprised and five mouth shapes) and eyes that can look about; the Natural face only; off for a new body (a saved body keeps what it was saved with)")
    bu.add_argument("--swinging-hair", action=argparse.BooleanOptionalAction, default=None,
                    help="add 18 spring bones to the Long hair, for the game engine to swing (needs --hair long, or a sheet that says long hair); off unless asked; saying either swinging option leaves the other off")
    bu.add_argument("--swinging-cloth", action=argparse.BooleanOptionalAction, default=None,
                    help="add 24 spring bones to the hem of a skirt or a dress, for the game engine to swing (needs one of them on the body, with nothing over it); off unless asked")
    for step in bodymaker._active_steps().values():
        if step.cli:
            step.cli.add_arguments(bu)
    bu.add_argument("--recipe", metavar="FILE", help="a complete recipe as JSON (python -m bodydesigner recipe writes one: skin, eye and brow colours, finish); "
                                                      "--set is then not used")
    bu.add_argument("--replace", action="store_true", help="replace the character's 3D model and skeleton, if it has them (without it, it is refused)")
    sub.add_parser("list", help="list library characters")
    sub.add_parser("health", help="check ComfyUI and the model files")
    return p


# ---- helpers ---------------------------------------------------------------------------------------------------------
def summary(npc):
    p, a = npc["profile"], npc["appearance"]
    who = f'{(p["first_name"] + " " + p["last_name"]).strip()}, {p["age"]} {p["gender"]}'
    if npc.get("freeform"):
        return f'{who} | {a.get("extra", "")[:80]}'
    return f'{who}, {p["occupation"]} | {a["hair"]} | {a["outfit"]}'


def find_npc(app, key):
    key = key.lower()
    matches = [n for n in app.lib.list() if n["id"] == key or key in n["name"].lower() or n["id"].startswith(key)]
    exact = [n for n in matches if n["id"] == key]
    matches = exact or matches
    if not matches:
        raise SystemExit(f"No character matches '{key}'. Try: python -m npcgen list")
    if len(matches) > 1:
        raise SystemExit(f"'{key}' matches several characters: " + ", ".join(n["id"] for n in matches))
    return app.lib.get(matches[0]["id"])


def find_item(app, key):
    key = key.lower()
    matches = [c for c in app.items.list() if c["id"] == key or key in c["name"].lower() or c["id"].startswith(key)]
    matches = [c for c in matches if c["id"] == key] or matches
    if not matches:
        raise SystemExit(f"No item matches '{key}'. Try: python -m npcgen item list")
    if len(matches) > 1:
        raise SystemExit(f"'{key}' matches several items: " + ", ".join(c["id"] for c in matches))
    return app.items.get(matches[0]["id"])


def wait_for(app, submitted):
    """Print progress until every submitted job has finished. Returns the finished job dicts."""
    ids = {j["id"] for j in submitted}
    seen = {}
    try:
        while True:
            jobs = [j for j in app.jobs.snapshot() if j["id"] in ids]
            for j in jobs:
                for s in j["steps"]:
                    state = (s["status"], s.get("detail"))
                    if seen.get((j["id"], s["label"])) != state and s["status"] != "pending":
                        seen[(j["id"], s["label"])] = state
                        extra = f' ({s["detail"]})' if s.get("detail") and s["status"] == "running" else ""
                        secs = f' {s["seconds"]}s' if s.get("seconds") else ""
                        print(f'  [{j["npc_name"]}] {s["label"]}: {s["status"]}{extra}{secs}' + (f' - {s["error"]}' if s.get("error") else ""), flush=True)
            if all(j["status"] in ("done", "error", "cancelled") for j in jobs):
                return jobs
            time.sleep(1.0)
    except KeyboardInterrupt:
        print("\nCancelling...", file=sys.stderr)
        app.jobs.cancel()
        time.sleep(2)
        raise SystemExit(130)


# ---- commands ------------------------------------------------------------------------------------------------------------
def cmd_roll(a, data, app):
    for _ in range(max(1, a.count)):
        npc = roll.roll_npc(data, a.setting, _constraints(a), freeform=a.describe)
        print(json.dumps(npc) if a.json else summary(npc))
    return 0


def cmd_generate(a, data, app):
    assets = {"portrait": a.portrait, "sprite": a.sprite, "model_3d": a.model_3d, "rig_3d": a.rig_3d, "head_3d": a.head_3d,
              "refine_3d": a.refine_3d,
              "sharp_face": a.sharp_face, "smooth_eyes": a.smooth_eyes, "match_hair": a.match_hair, "clean_hands": a.clean_hands,
              "attach_head": a.attach_head,
              "check_3d": a.check_3d, "best_3d": a.best_3d,
              "expressions": list(a.expressions or []) or (DEFAULT_EXPRESSIONS if a.expressions is not None else [])}
    if a.import_3d:
        if not a.ids or len(a.ids) != 1:
            raise SystemExit("--import-3d goes into one existing character: give --ids with a single id")
        assets["import_3d"] = {"model_file": a.import_3d, "clean": {"auto": "auto", "yes": True, "no": False}[a.import_clean],
                               "detail": a.import_detail}
    if a.animations is not None:
        assets["animations"] = {"clips": a.animations or ["walk"], "dirs": a.dirs, "frames": a.frames, "candidates": a.best_of,
                                "mirror_left": not a.no_mirror_left}
    if a.sheet is not None:
        assets["sheet"] = {"views": a.sheet or list(sheet.VIEWS), "pose": a.pose}
    if not (assets["portrait"] or assets["sprite"] or assets["model_3d"] or assets.get("import_3d") or assets["rig_3d"] or assets["head_3d"] or assets["refine_3d"]
            or assets["sharp_face"] or assets["smooth_eyes"] or assets["match_hair"] or assets["clean_hands"] or assets["attach_head"] or assets["check_3d"] or assets["best_3d"] or assets["expressions"]
            or assets.get("animations") or assets.get("sheet")):
        assets["portrait"] = assets["sprite"] = True
    if assets.get("import_3d"):
        wanted = assets["import_3d"]["model_file"]
        try:
            if pathlib.Path(wanted).is_file():                       # a file on this computer: ComfyUI gets it first
                assets["import_3d"]["model_file"] = import3d.upload(app.comfy, wanted)
            elif not import3d.has_folder(wanted):                    # a name with no folder: ComfyUI's output folder, then its input
                assets["import_3d"]["model_file"] = import3d.resolve(app.comfy, wanted)[0]
        except (OSError, ValueError, ComfyError) as e:
            raise SystemExit(f"Cannot import {wanted}: {e}")
    try:
        assets = service.clean_assets(assets, data)
        if assets.get("import_3d"):
            assets["import_3d"] = import3d.prepare(app.comfy, assets["import_3d"])
    except ValueError as e:
        raise SystemExit(str(e))
    options = service.clean_options({"quality": a.quality, "sprite_mode": a.sprite_mode, "transparent_portrait": a.transparent_portrait,
                                     "transparent_sprite": not a.opaque_sprite, "seed": a.seed,
                                     "image_model": a.image_model, "edit_model": a.edit_model,
                                     "three_d_model": a.three_d_model, "three_d_quality": a.three_d_quality,
                                     "three_d_face_source": a.three_d_face_source, "three_d_soften_eyes": a.soften_eyes,
                                     "three_d_glasses_off": a.glasses_off,
                                     "three_d_hunyuan_source": a.three_d_hunyuan_source,
                                     "three_d_hunyuan_detail": a.three_d_hunyuan_detail,
                                     "rig_skeleton": a.skeleton, "rig_clips": a.clips,
                                     "head_quality": a.head_quality, "head_expression": a.head_expression,
                                     "head_match_hair": a.head_match_hair,
                                     # (only what was asked for: config.json's "options" decide the rest)
                                     **({"game_faces": a.game_size} if a.game_size is not None else {}),
                                     **({"portrait_framing": a.portrait_framing} if a.portrait_framing else {}),
                                     **({"engine_fallback": a.engine_fallback} if a.engine_fallback is not None else {})}, app.cfg)

    style = a.style or next(iter(data["styles"]))
    if (not a.ids and not a.cast and not a.describe and morphology.needs_full_body(assets)
            and "races" in data["settings"][a.setting] and not a.origin and not a.morphology):
        a.morphology = "bipedal"
        print("note: limiting this full-body batch to bipedal characters; use --origin or --morphology to choose another body plan", file=sys.stderr)
    if a.ids:
        npcs = [app.lib.get(i) for i in a.ids]
    elif a.cast:
        path = pathlib.Path(a.cast)
        try:
            entries = cast.parse_cast(path.read_text("utf-8-sig"), "csv" if path.suffix.lower() == ".csv" else "json")
            drafts, warnings = cast.build_drafts(data, entries, a.setting, style, _constraints(a))
        except (OSError, ValueError) as e:
            raise SystemExit(f"Could not read the cast file: {e}")
        for w in warnings:
            print("warning:", w, file=sys.stderr)
        npcs = [roll.normalize(data, d) for d in drafts]
    else:
        locked = {}
        if a.name:
            first, _, last = a.name.strip().rpartition(" ")
            locked["profile.first_name"], locked["profile.last_name"] = (first or last), (last if first else "")
        if a.age:
            locked["profile.age"] = a.age
        if a.gender:
            locked["profile.gender"] = a.gender
        count = 1 if a.describe else max(1, a.count or 1)
        npcs = []
        for _ in range(count):
            npc = roll.roll_npc(data, a.setting, _constraints(a), locked if count == 1 else None, freeform=a.describe)
            npc["style"], npc["custom"] = style, {}
            npcs.append(roll.normalize(data, npc))

    # Check the entire request before saving any new cast/batch members or queueing GPU work.
    for npc in npcs:
        body_plan = morphology.resolve(npc, data)
        reason = morphology.unsupported_reason(body_plan, assets)
        if reason:
            name = f'{npc.get("profile", {}).get("first_name", "NPC")} {npc.get("profile", {}).get("last_name", "")}'.strip()
            raise SystemExit(f"{name}: {reason}")
    npcs = [npc if npc.get("id") else app.lib.create(npc) for npc in npcs]

    for npc in npcs:
        if a.ids and a.style:                       # only when asked: otherwise an existing character keeps its style
            app.lib.update(npc["id"], lambda n: n.update(style=a.style))
        print(f'{npc["id"]}: {summary(npc)}')
    submitted = [app.jobs.submit(app.lib.get(n["id"]), assets, options) for n in npcs]
    print(f"Queued {sum(j['total'] for j in submitted)} steps for {len(npcs)} character(s). Ctrl-C cancels.")
    results = wait_for(app, submitted)
    failed = [j for j in results if j["status"] != "done"]
    if a.out:
        out = pathlib.Path(a.out)
        with zipfile.ZipFile(io.BytesIO(app.lib.export_zip([n["id"] for n in npcs]))) as z:
            z.extractall(out)
        print(f"Wrote {len(npcs)} character folder(s) to {out.resolve()}")
    print(f"Done: {len(results) - len(failed)} ok, {len(failed)} with problems. Library: {app.lib.root}")
    for j in failed:
        print(f'  {j["npc_name"]}: {j["error"]}', file=sys.stderr)
    return 1 if failed else 0


def cmd_export(a, data, app):
    npc = find_npc(app, a.id)
    options = {"frame": a.frame, "dirs": a.dirs, "pixelate": a.pixelate, "colors": a.colors, "outline": a.outline, "name": a.name, "res_path": a.res_path}
    try:
        files = export.build(app.lib, npc, a.clip, a.preset, options)
    except ValueError as e:
        raise SystemExit(str(e))
    written = export.write_dir(files, pathlib.Path(a.out))
    print(f"Wrote {len(written)} files to {pathlib.Path(a.out).resolve()}: " + ", ".join(written))
    return 0


def cmd_export_sheet(a, data, app):
    npc = find_npc(app, a.id)
    try:
        files = export.build_sheet(app.lib, npc, {"name": a.name})
    except ValueError as e:
        raise SystemExit(str(e))
    written = export.write_dir(files, pathlib.Path(a.out))
    print(f"Wrote {len(written)} files to {pathlib.Path(a.out).resolve()}: " + ", ".join(written))
    return 0


def cmd_reassemble(a, data, app):
    npc = find_npc(app, a.id)
    try:
        app.jobs.animator.reassemble(npc["id"], data, a.clip)
    except Exception as e:                       # ComfyError with a readable message, or a missing raw file
        raise SystemExit(str(e))
    report = app.lib.get(npc["id"])["assets"]["animations"][a.clip]["report"]
    for d, r in report["directions"].items():
        print(f'  {d:6s} legs {r["leg_variation"]:.2f}  head {r["head_stability"]:.2f}  palette {r["palette_coverage"]:.2f}')
    print("flags:", "; ".join(report["flags"]) or "none")
    return 0


def cmd_list(a, data, app):
    for n in app.lib.list():
        h = n["has"]
        print(f'{n["id"]:32s} {n["name"]:26s} {n["style"] or "-":9s} portrait={int(h["portrait"])} sprite={int(h["sprite"])} expr={h["expressions"]} sheet={int(h["sheet"])}')
    return 0


def cmd_voice(a, data, app):
    from .voice import batch
    try:
        req = batch.clean_request({"engine": a.engine, "takes": a.takes, "lines": a.lines, "redo": a.redo})
    except ValueError as e:
        raise SystemExit(str(e)) from None
    npcs = [app.lib.get(n["id"]) for n in app.lib.list()] if a.who == ["all"] else [find_npc(app, key) for key in a.who]
    submitted = []
    for npc in npcs:
        assets = batch.job_assets(app.lib, npc, req,app.cfg)
        if assets:
            submitted.append(app.jobs.submit(app.lib.get(npc["id"]), {"voice": assets}, {}))
    print(f"Voicing {len(submitted)} character(s); {len(npcs) - len(submitted)} had nothing to do.")
    if not submitted:
        return 0
    results = wait_for(app, submitted)
    failed = [j for j in results if j["status"] != "done"]
    print(f"Done: {len(results) - len(failed)} voiced, {len(failed)} with errors. Pick takes on the Library's Review voices page.")
    return 1 if failed else 0


def recipe_settings(pairs):
    """{key: value} from --set KEY=VALUE arguments (numbers and true/false become numbers and booleans)."""
    out = {}
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep or not key.strip():
            raise SystemExit(f"--set wants KEY=VALUE, not '{pair}'")
        low = value.strip().lower()
        try:
            out[key.strip()] = True if low == "true" else False if low == "false" else float(value)
        except ValueError:
            out[key.strip()] = value.strip()
    return out


def make_recipe_item(a, app, existing):
    """Glasses and fitted clothes: built from their settings on the spot (no queue, no GPU); the picture for the list is drawn by Blender a few seconds later."""
    store = app.items
    try:
        sets = recipe_settings(a.set)
        if a.style and items.recipe_maker(existing["family"] if existing else a.family) is not glasses:
            raise ValueError("Only glasses have styles: fitted clothes are changed with --set (colour, ease_mm, length, texture)")
        if existing is None:
            item = store.create(items.clean_item({"family": a.family, "kind": a.kind, "name": a.name, "description": a.describe, "faces": a.faces,
                                                  "recipe": {**({"preset": a.style} if a.style else {}), **sets}}))
        elif a.style or sets:
            merged = {**existing["recipe"], **(glasses.load()["presets"][a.style]["recipe"] if a.style else {}), **({"preset": a.style} if a.style else {}), **sets}
            keep = {k: existing.get(k) for k in ("name", "family", "kind", "description", "faces")}
            item = store.edit(existing["id"], items.clean_item({**keep, "recipe": merged}))
        else:
            item = store.build_recipe(existing["id"])
    except ValueError as e:
        raise SystemExit(str(e)) from None
    model = item["assets"]["model_3d"]
    print(f'{item["id"]}: {item["name"]}, built: {model["triangles"]:,} triangles, '
          + (f'{model["frame_width_mm"]} mm wide, ' if "frame_width_mm" in model else "the pattern on its own (not fitted to a body), ") + f'{model["size"] / 1024:.0f} KB')
    for note in (clothing.notes(item["recipe"]) if items.maker(item["family"]) == "garment" else []):
        print("Note: " + note)
    if store.posters is not None:
        print("Drawing the picture (a few seconds)...")
        store.posters.wait_idle(240)
        error = (store.get(item["id"])["assets"]["model_3d"]).get("preview_error")
        if error:
            print("No picture: " + error)
    print(f"Item folder: {store.root / item['id']}")
    return 0


def cmd_item_fit(a, app):
    """Glasses on a character's face: the face is found first (once: it is kept with the character), then the pair is fitted to it."""
    item, npc = find_item(app, a.id), find_npc(app, a.character)
    faces = app.faces
    try:
        status = faces.request(npc["id"], force=a.rescan, estimate=a.estimate)
        if status["state"] in ("queued", "running"):
            print("Finding the face (about 40 seconds)...")
            faces.wait_idle(1800)
            status = faces.status(npc["id"])
    except (ValueError, KeyError, RuntimeError) as e:
        raise SystemExit(str(e)) from None
    if status["state"] in ("error", "cancelled") or status["scan"] is None:
        raise SystemExit(status["message"] or "The face could not be scanned")
    scan = facefit.Scan(faces.load(npc["id"]))
    given = {"up": a.up, "forward": a.forward, "tilt": a.tilt, "size": a.size}
    nudge = {k: v for k, v in given.items() if v is not None} or None          # none given: the one saved for this character
    try:
        placement = app.items.fit(item["id"], npc["id"], scan, nudge)
        report = placement["report"]
        found = status["scan"]
        print(f'{npc["id"]}: the face was found by {"SDPose" if found["source"] == "sdpose" else "proportions"}'
              + (f' (score {found["score"]})' if found["score"] is not None else "")
              + f', eyes {found["ipd_mm"]} mm apart' + (f', face {found["face_width_mm"]} mm wide' if found["face_width_mm"] else ""))
        print(f'{item["id"]}: scale {report["scale"]}, roll {report["roll_deg"]} deg, yaw {report["yaw_deg"]} deg, lenses {report["vertex_distance_mm"]} mm from the eyes, '
              f'nose gap {report["nose_gap_mm"]} mm, nudge {report["nudge"]}')
        for line in [*found["notes"], *report["notes"]]:
            print("  note: " + line)
        if a.save:
            app.items.save_fit(item["id"], npc["id"], report["nudge"], found["source"])
            print("Fit kept.")
        if a.out:
            glb, info = app.items.worn(item["id"], npc["id"], scan, report["nudge"])
            target = pathlib.Path(a.out) / f'{npc["id"]}-{item["id"]}.glb'
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(glb)
            print(f'{target} ({len(glb) / 1048576:.1f} MB; the pair is under the bone "{info["bone"]}": {info["attach"]})')
    except ValueError as e:
        raise SystemExit(str(e)) from None
    return 0


def cmd_item_dress(a, app):
    """Fitted clothes on a designed body: the fit takes about half a minute (in a process of its own); the character wearing them is written with --out."""
    item, npc = find_item(app, a.id), find_npc(app, a.character)
    dressings = app.dressings
    try:
        status = dressings.request(item["id"], npc["id"], force=a.refit)
        if status["state"] in ("queued", "running"):
            print("Fitting it to the body (about half a minute)...")
            dressings.wait_idle(1800)
            status = dressings.status(item["id"], npc["id"])
    except (ValueError, KeyError) as e:
        raise SystemExit(str(e)) from None
    if status["state"] in ("error", "cancelled") or status["fit"] is None:
        raise SystemExit(status["message"] or "It could not be fitted")
    fit = status["fit"]
    print(f'{npc["id"]}: the {fit["kind"]} (cut {fit["cut"]}) {"fit" if fit["kind"] == "trousers" else "fits"}: {fit["triangles"]:,} triangles, ' + (f'{fit["over_underwear_mm"]} mm off the underwear, ' if fit["over_underwear_mm"] is not None else '') + f'fitted in {fit["seconds"]} s'
          + (f', in place of the {" and ".join(name.replace("Forge", "").lower() for name in fit["replaced"])} it wore' if fit["replaced"] else ""))
    if a.out:
        glb, _info = dressings.worn(item["id"], npc["id"])
        target = pathlib.Path(a.out) / f'{npc["id"]}-{item["id"]}.glb'
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(glb)
        print(f"{target} ({len(glb) / 1048576:.1f} MB)")
    return 0


def cmd_item_equip(a, app):
    """Held items and hats on a rigged character: where each goes is worked out from the character's rest pose (npcgen/equip.py), and the character holding them is written
    with --out. The bone and the transform are printed for a game that would rather attach the items itself."""
    npc = find_npc(app, a.character)
    parts = []
    for token in a.items:
        ref, _, slot = token.partition("=")
        parts.append({"item": find_item(app, ref)["id"], "slot": slot or None})
    given = {"up": a.up, "forward": a.forward, "side": a.side, "turn": a.turn, "tilt": a.tilt, "size": a.size}
    nudge = {k: v for k, v in given.items() if v is not None}
    if nudge:
        if len(parts) != 1:
            raise SystemExit("A nudge is for one item at a time: name just that item")
        parts[0]["nudge"] = nudge
    try:
        glb, report = app.items.equipped(npc["id"], parts)
        for entry in report["items"]:
            attach = entry["attach"]
            where = f'position {attach["translation"]}, rotation {attach["rotation"]}, scale {attach["scale"]}' if "translation" in attach else "a matrix (see the file)"
            print(f'{entry["item"]}: the {entry["kind"]} goes in the {equip.SLOTS[entry["slot"]]}, on the bone "{entry["bone"]}": {where}')
        for line in report["notes"]:
            print("  note: " + line)
        if a.save:
            for entry in report["items"]:
                app.items.save_equip(entry["item"], npc["id"], entry["slot"], entry["nudge"])
            print("Kept as the way " + ("this is" if len(parts) == 1 else "these are") + f' carried by {npc["id"]}.')
        if a.out:
            target = pathlib.Path(a.out) / f'{npc["id"]}-equipped.glb'
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(glb)
            print(f"{target} ({len(glb) / 1048576:.1f} MB; each item hangs under its bone, so it follows every animation clip)")
    except (ValueError, KeyError) as e:
        raise SystemExit(str(e)) from None
    return 0


def cmd_item(a, data, app):
    store = app.items
    if a.item_cmd == "list":
        for card in store.list():
            has = card["has"]
            print(f'{card["id"]:34s} {card["name"]:24s} {card["kind_label"]:16s} picture={int(has["hero"])} views={has["views"]} '
                  f'model={int(has["model_3d"])} finished={int(has["finished"])}' + ("  (out of date)" if card["stale"] else ""))
        return 0
    if a.item_cmd == "export":
        item = find_item(app, a.id)
        out = pathlib.Path(a.out)
        out.mkdir(parents=True, exist_ok=True)
        target = out / f'{item["id"]}.zip'
        target.write_bytes(store.export_zip(item["id"]))
        print(target)
        return 0
    if a.item_cmd == "fit":
        return cmd_item_fit(a, app)
    if a.item_cmd == "dress":
        return cmd_item_dress(a, app)
    if a.item_cmd == "equip":
        return cmd_item_equip(a, app)
    existing = find_item(app, a.id) if a.id else None
    if items.is_recipe({"family": existing["family"] if existing else a.family}):
        return make_recipe_item(a, app, existing)
    try:
        if a.id:
            item = existing
        else:
            item = store.create(items.clean_item({"family": a.family, "kind": a.kind, "name": a.name, "description": a.describe, "setting": a.setting,
                                                  "look": a.look, "size_m": a.size, "faces": a.faces,
                                                  "high_views": None if a.high_views is None else a.high_views == "on"}))
        options = items.clean_options({"best": not a.no_best, "image_model": a.image_model, "edit_model": a.edit_model, "reroll": a.reroll,
                                       "three_d_quality": a.three_d_quality, "seed": a.seed}, app.cfg)
        job = app.jobs.submit_item(item["id"], {"make": True, "finish": not a.no_finish, "redo": a.redo}, options)
    except ValueError as e:
        raise SystemExit(str(e)) from None
    print(f'{item["id"]}: {item["name"]}, {len(job["steps"])} step(s) queued. Ctrl-C cancels.')
    results = wait_for(app, [job])
    failed = [j for j in results if j["status"] != "done"]
    print(f'Done: {len(results) - len(failed)} ok, {len(failed)} with problems. Item folder: {store.root / item["id"]}')
    return 1 if failed else 0


def garment_colours(texts):
    """{garment: '#rrggbb'} from --colour GARMENT=#RRGGBB arguments (the garments and the colours are checked by what uses them)."""
    out = {}
    for text in texts:
        kind, found, value = text.partition("=")
        if not found or not kind.strip() or not value.strip():
            raise SystemExit(f"--colour wants GARMENT=#RRGGBB (for example shirt=#3f6ba8), not {text!r}")
        out[kind.strip()] = value.strip()
    return out


def cmd_body(a, data, app):
    if a.body_cmd == "colours":
        npc = find_npc(app, a.character)
        try:
            done = bodymaker.recolour(app.cfg, app.lib, npc["id"], garment_colours(a.colour))
        except (ValueError, bodymaker.BodyError) as e:
            raise SystemExit(str(e)) from None
        print(f'{npc["id"]}: ' + (", ".join(f"{kind} {colour}" for kind, colour in done["changed"].items()) or "already those colours") + (" (the card's picture drawn again)" if done["preview"] else ""))
        return 0
    if a.body_cmd == "list":
        found = bodymaker.availability(app.cfg)
        for base in found["bases"]:
            print(f'{base["id"]:34s} {base["label"]}')
        if found["missing"]:
            print("missing: " + "; ".join(found["missing"]), file=sys.stderr)
        return 0 if found["installed"] else 1
    npc = find_npc(app, a.character)
    values = {}
    for text in a.set:
        key, found, value = text.partition("=")
        try:
            if not found:
                raise ValueError
            values[key.strip()] = float(value)
        except ValueError:
            raise SystemExit(f"--set wants PROPORTION=VALUE (a number from -1 to 1), not {text!r}") from None
    raw = {"base": a.base, "skeleton": a.skeleton, "values": values or None, "saved": a.saved, "replace": a.replace, "face": a.face, "texture": a.texture,
           "match": not a.no_match, "clothes": a.clothes, "outer": a.outer, "colours": garment_colours(a.colour), "hair": a.hair,
           "marks": a.marks, "expressions": a.expressions,
           "secondary": {"hair": bool(a.swinging_hair), "cloth": bool(a.swinging_cloth)} if a.swinging_hair is not None or a.swinging_cloth is not None else None}
    steps = bodymaker._active_steps()
    raw.update({name: step.cli.value(a) for name, step in steps.items() if step.cli})
    if a.recipe:
        try:
            raw["recipe"] = json.loads(pathlib.Path(a.recipe).read_text("utf-8-sig"))
        except (OSError, ValueError) as e:
            raise SystemExit(f"Could not read the recipe {a.recipe}: {e}") from None
        if values:
            raise SystemExit("--set changes the proportions of a body made from --base; a recipe file carries its own")
    try:
        assets = service.clean_assets({"body_3d": raw}, data)
        assets["body_3d"] = bodymaker.prepare(app.lib, npc, assets["body_3d"])
        job = app.jobs.submit(npc, assets, service.clean_options({}, app.cfg))
    except ValueError as e:
        raise SystemExit(str(e)) from None
    body = assets["body_3d"]
    hair = (body["recipe"].get("hair") or {}).get("style")
    wears = " and ".join(piece["kind"] for piece in body["pieces"]) or "underwear only"
    print(f'{npc["id"]}: {body["base_label"]}, {body["face"]} face, {hair + " hair" if hair else "no hair"}, {wears}, {a.skeleton} skeleton, queued. Ctrl-C cancels.')
    if body["clothes"] != "none":
        for word in body["left"]:
            why = body.get("why", {}).get(word)
            print(f'  the sheet also names {word}, ' + (f"which is not put on: {why}" if why else "which the Body Designer does not make yet"), file=sys.stderr)
    said = body.get("marks_said") or {}
    if said.get("text"):
        print(f'  skin marks: {said["text"]}' + (f' (the sheet names {" and ".join(said["assumed"])} but not where, so the places are the usual ones)' if said.get("assumed") else ""))
    for word in said.get("skipped") or []:
        print(f"  the sheet also names {word}, which is not painted", file=sys.stderr)
    if body.get("expressions"):
        print("  with face shapes and eyes that can look about")
    if body.get("secondary"):
        print("  with spring bones on its " + " and ".join(part for part in bodymaker.SWINGING_PARTS if body["secondary"].get(part)).replace("cloth", "skirt or dress") + " for the game engine to swing")
    for name, step in steps.items():
        if step.cli and body.get(name):
            print("  " + step.cli.describe(body[name]))
    if body.get("body"):
        print(f'  with the body shape of the Body tab (chest form {body["body"]["form"]}, bust size {body["body"]["bust"]:+.2f})')
    results = wait_for(app, [job])
    failed = [j for j in results if j["status"] != "done"]
    print(f'Done: {len(results) - len(failed)} ok, {len(failed)} with problems. Library: {app.lib.root}')
    for j in failed:
        print(f'  {j["npc_name"]}: {j["error"]}', file=sys.stderr)
    return 1 if failed else 0


def cmd_health(a, data, app):
    h = app.comfy.health()
    print(json.dumps(h, indent=2))
    return 0 if h.get("online") and not h.get("missing") else 1


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    extension_dir = config.ROOT / "local" / "extensions"
    app = None
    try:
        if argv[:2] == ["body", "use"] and extension_dir.is_dir():
            from server import load_extensions
            app = App()
            load_extensions(extension_dir, app=app)
        data = roll.load_data()
        args = build_parser(data).parse_args(argv)
        if app is None and args.cmd != "roll":
            app = App()
        return {"roll": cmd_roll, "generate": cmd_generate, "export": cmd_export, "export-sheet": cmd_export_sheet, "reassemble": cmd_reassemble,
                "list": cmd_list, "health": cmd_health, "voice": cmd_voice, "item": cmd_item, "body": cmd_body}[args.cmd](args, data, app)
    finally:
        if app is not None:
            app.close()      # the job worker lets go of what it left on the GPU: a command that ends must not leave 30 GB loaded in ComfyUI
