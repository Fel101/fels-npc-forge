"""A designed body's clothes: which of the Body Designer's garments a character's outfit text names, in what colour, and the putting on of them.

The Body Designer makes a sleeveless shirt, fitted trousers, an open jacket, a bib apron, a knee-length skirt and a sleeveless knee-length dress (bodydesigner/garments.py: pure functions, a garment is fitted to the body it is given).
A sheet's outfit is free text ('a camel blazer over a white top, wide-leg trousers and heels'), so `read` finds the words that name a shirt, trousers, a jacket, an apron, a skirt or a dress, or
something the Designer cannot make yet (a coat, a kilt, a robe, armour) and the colour of each (data/outfit.json); `pieces` turns that into what to fit. What it does not know it leaves
out and says so, never guesses. Garments are worn together only in the stacks the Designer has checked them in (`tested_stacks` in its catalogue, read here, never copied): the jacket and
the apron go over a shirt and trousers and nothing else, and a body wears one of them at most; a skirt is worn with a shirt and takes the place of trousers, and a dress is worn on its own and takes the place of both. `stack_problem` says why
a set of garments cannot go on together, `kept` which of the garments a body wears stay on it when others are put on, and `wear` refuses what `stack_problem` says.
`wear` fits each garment to the body (underwear already on: the Designer's fit stands off it) and dresses the plain file and the animated one alike. It is slow (about half
a minute a garment), so `python -m npcgen.outfit` runs it in a process of its own, which Cancel can stop (npcgen/bodymaker.py). The Items tab's try-on (npcgen/dressing.py) uses the same
function and the same process for one garment on one file, with `replace`: a garment of its kind that the body already wears comes off (npcgen/glbprune.py) once the new one is known to fit.
"""
import argparse
import json
import re
import struct
import sys
import time
from functools import lru_cache
from pathlib import Path

from . import bodymatch, config as cfgmod, glbprune
from .paint3d import Glb

DATA_FILE = cfgmod.ROOT / "data" / "outfit.json"
HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
MODES = ("basic", "sheet", "none")                       # a shirt and trousers, in the sheet's colours where it names them (the default) / only the garments the sheet names / none
OUTERS = ("sheet", "none", "jacket", "apron")            # what goes over them: the one the sheet names (the default), nothing, or one of the two the Designer makes
GARMENTS = (("shirt", "top"), ("trousers", "bottom"))     # the garments an outfit starts from, and the part of an outfit each is
BASE = tuple(kind for kind, _ in GARMENTS)               # the garments that go on the body itself
OUTER = ("jacket", "apron")                              # the garments that go over them: a body wears one of them at most (data/outfit.json `makes` says which words put one on)
NODES = {"shirt": "ForgeShirt", "trousers": "ForgeTrousers", "skirt": "ForgeSkirt", "dress": "ForgeDress", "jacket": "ForgeJacket", "apron": "ForgeApron"}   # the name of the mesh (and node) the Designer gives each garment in a body it dresses
ORDER = ("shirt", "trousers", "skirt", "dress", "jacket", "apron")  # from the body outward: the order the garments are fitted and put on in (a skirt or a dress is worn instead of trousers, never with them)
SHIRT_VERSION = 3                                        # the combined shirt (bodydesigner/CLOTHING_PROPOSAL.md); a Body Designer that does not make it yet gets its catalogue's
SPLIT = re.compile(r",|;| and | over | under | with | beneath | atop | plus | then ")
CLASSES = ("whole", "cover", "outer", "wrap", "armour", "bottom", "top", "dress", "skirt")
MAKEABLE = ("outer", "wrap", "skirt", "dress")           # the classes of words of which data/outfit.json `makes` puts some on as the Designer's garments


@lru_cache(maxsize=1)
def data():
    return json.loads(DATA_FILE.read_text("utf-8"))


def _cloth(part):
    """(colour, explicit) for the colour a part of an outfit names ('a faded blue shirt': explicit), else that of the material it names ('canvas trousers': not), else (None, False)."""
    table = data()
    found = bodymatch.colour(part, "cloth", table["cloth"])
    if found:
        return found, True
    return bodymatch.colour(part, "materials", {"phrases": {}, "colours": table["materials"], "modifiers": table["cloth"]["modifiers"]}), False


def named_colour(text):
    """'#rrggbb' for the colour the words name (a colour word with its modifiers, or the colour of a material: 'dark red', 'canvas'), else None: how the Items tab's designer reads a description."""
    return _cloth(text)[0]


def read(text):
    """What an outfit text says about the Designer's garments: {"top": {"colour": '#rrggbb' or None, "under": the colour without a jacket's, "word"} or None, "bottom": {"colour", "word"} or None,
    "over": [{"kind": 'jacket' | 'apron', "word", "colour"}: the ones it names that the Designer makes, one of each kind, in the order the text names them], "instead": [{"kind": 'skirt' | 'dress',
    "word", "colour"}: the garments it names that the Designer makes and that take the place of trousers (a dress, of the shirt too), in the same way], "left": [the garment words it names that the Designer cannot
    make: coats, kilts, dresses, armour...]}. A shirt-like word is a top; a vest or a bodice covers the chest and gives it its colour; a jacket or a coat shows the shirt, and stands in
    for one only when none is named (`under` is the colour the shirt has when the jacket is put on, so not the jacket's); a cloak never does; trousers are a bottom (a skirt, a kilt or
    shorts are not); a uniform or coveralls are both, in its colour; a dress blocks any stand-in. A word of `makes` is put on as the Designer's garment of that kind (a jacket, a blazer
    or a cardigan as its jacket, an apron as its apron, a skirt as its skirt, a dress or a gown as its dress), but not when the same part of the text names a dress: 'a blazer dress'. The colour is the one named with
    the garment, an explicit colour before the colour a material implies."""
    table = data()
    makes = {word: kind for kind, words in table["makes"].items() for word in words}
    text = str(text or "").lower()
    for phrase in table["ignore"]:
        text = text.replace(phrase, " ")
    for phrase in sorted(table.get("rename", {}), key=len, reverse=True):         # 'dress shoes' are shoes, 'a dress uniform' a uniform
        text = re.sub(rf"(?<![a-z']){re.escape(phrase)}(?![a-z'])", table["rename"][phrase], text)
    found = []                                                    # (class, word, colour, explicit, made) in the order the text names them; `made`: put on as one of the Designer's garments (`makes`)
    for part in SPLIT.split(text):
        words = taken = bodymatch._words(part)
        hits = []
        for cls in CLASSES:
            for word in sorted(table[cls], key=lambda w: -len(w)):
                for form in (word, word + "s", word + "es"):
                    if f" {form} " in taken:                      # a word once: 'flight suit' is not also 'suit'
                        hits.append((words.find(f" {form} "), cls, word))
                        taken = taken.replace(f" {form} ", " " * (len(form) + 2))
        if any(cls != "whole" for _, cls, _ in hits):             # 'uniform shirt', 'suit trousers': beside another garment, 'uniform' and 'suit' say which kind, not that it is one piece
            hits = [hit for hit in hits if hit[1] != "whole"]
        if hits:
            colour, explicit = _cloth(part)
            dress = any(cls == "dress" for _, cls, _ in hits)     # 'a blazer dress': the blazer is the dress's
            found += [(cls, word, colour, explicit, cls in MAKEABLE and word in makes and (cls == "dress" or not dress)) for _, cls, word in sorted(hits)]

    if any(cls == "dress" for cls, *_ in found):                  # a skirt in an outfit that names a dress ('a ball gown with a full skirt') is the dress's own, not a garment of its own
        found = [(cls, word, c, e, made and cls != "skirt") for cls, word, c, e, made in found]
        if any(cls == "dress" and made for cls, _, _, _, made in found):          # (and with the dress made, there is nothing to say about its skirt)
            found = [g for g in found if g[0] != "skirt"]

    def of(*classes):
        return [g for g in found if g[0] in classes]

    def colour(*groups):
        for explicit in (True, False):
            for group in groups:
                for _, _, c, e, _made in group:
                    if c and e == explicit:
                        return c
        return None

    tops, covers, outers, wholes, bottoms = of("top"), of("cover"), of("outer"), of("whole"), of("bottom")
    chest, legs = bool(of("dress")), bool(of("dress", "skirt"))   # a dress or a skirt: what a stand-in would put there is not what the sheet says
    stand_in = [] if chest else outers + wholes
    under = [] if chest else [g for g in outers if not g[4]] + wholes
    left = list(dict.fromkeys(word for cls, word, _, _, made in found if cls in ("cover", "outer", "wrap", "armour", "dress", "skirt") and not made))
    over, instead = [], []
    for _cls, word, c, _explicit, made in found:
        group = over if made and makes[word] in OUTER else instead
        if made and makes[word] not in [o["kind"] for o in group]:
            group.append({"kind": makes[word], "word": word, "colour": c})
    legs_word = bottoms[0][1] if bottoms else (wholes[0][1] if wholes and not legs else None)  # (the word that names the legs' garment: said when a skirt takes its place)
    return {"top": {"colour": colour(covers, tops, stand_in), "under": colour(covers, tops, under), "word": tops[0][1] if tops else None} if tops or covers or stand_in else None,
            "bottom": {"colour": colour(bottoms, [] if legs else wholes), "word": legs_word} if bottoms or (wholes and not legs) else None, "over": over, "instead": instead, "left": left}


# ---- what goes over what ---------------------------------------------------------------------------------------------------------------------------
def _catalogue():
    """{kind: the Body Designer's catalogue entry} of the garments this module puts on that this Body Designer makes ({} without one)."""
    try:
        from bodydesigner import garments
        return {entry["id"]: entry for entry in garments.catalogue() if entry["id"] in NODES}
    except (ImportError, OSError, ValueError, KeyError, TypeError):
        return {}


def offered():
    """The kinds of garment this Body Designer makes among the ones that are put on here."""
    return [kind for kind in ORDER if kind in _catalogue()]


def _stacks(kind, catalogue):
    """The stacks the Body Designer has tested `kind` in (its `tested_stacks` that end with it): lists of kinds from the body outward."""
    return [list(stack) for stack in (catalogue.get(kind) or {}).get("tested_stacks") or [] if isinstance(stack, (list, tuple)) and stack and stack[-1] == kind]


def needs(kind, catalogue=None):
    """The garments `kind` is checked over, from the Body Designer's `tested_stacks`: the kinds under it in the first stack that ends with it (shirt and trousers for the jacket and the
    apron), [] for a garment that does not go over others (a shirt, trousers, a skirt, a dress), and None for one that is checked over others but lists no stack to say which."""
    if kind not in OUTER:
        return []
    stacks = _stacks(kind, catalogue if catalogue is not None else _catalogue())
    return stacks[0][:-1] if stacks else None


def worn_with(kind, catalogue=None):
    """The garments `kind` is checked with besides itself, from every stack it is listed in (a skirt: a shirt; a dress: nothing, it is worn on its own), None when it lists no stack."""
    stacks = _stacks(kind, catalogue if catalogue is not None else _catalogue())
    return list(dict.fromkeys(k for stack in stacks for k in stack if k != kind)) if stacks else None


def takes_off(kind, catalogue=None):
    """The garments of BASE that `kind` is worn without, because none of its tested stacks has them: a skirt takes trousers off, a dress would take both off. [] for the garments that go
    on the body itself and for the ones that go over them (the jacket and the apron), None for one that is none of these and lists no stack."""
    if kind in BASE or kind in OUTER:
        return []
    stacks = _stacks(kind, catalogue if catalogue is not None else _catalogue())
    return [base for base in BASE if all(base not in stack for stack in stacks)] if stacks else None


def together(a, b, catalogue=None):
    """Whether garments of the kinds `a` and `b` can be worn at once: both go on the body itself (a shirt and trousers), or the Body Designer has tested one of them in a stack that has the other."""
    cat = catalogue if catalogue is not None else _catalogue()
    if a == b:
        return False
    return (a in BASE and b in BASE) or any(b in stack for stack in _stacks(a, cat)) or any(a in stack for stack in _stacks(b, cat))


def kept(now, new, catalogue=None):
    """Which of the kinds `now` that a body wears stay on it when garments of the kinds `new` are put on in their place (the Items tab's try-on): the garment of the same kind is replaced,
    a body wears one outer garment, and a shirt, trousers, skirt or dress takes off what the Body Designer has not tested it with (a skirt takes trousers off, a dress everything)."""
    cat = catalogue if catalogue is not None else _catalogue()
    new = set(new)
    out = set()
    for kind in now:
        if kind in new or (kind in OUTER and new & set(OUTER)) or any(n not in OUTER and not together(kind, n, cat) for n in new):
            continue
        out.add(kind)
    return out


def _label(kind, catalogue):
    """The garment's name in a sentence ('open jacket'): the Body Designer's own label, in lower case."""
    return str((catalogue.get(kind) or {}).get("label") or kind).lower()


def _noun(kind):
    return {"shirt": "a shirt", "trousers": "trousers"}.get(kind, f"{'an' if kind[:1] in 'aeiou' else 'a'} {kind}")


def _and(items):
    return ", ".join(items[:-1]) + (" and " if len(items) > 1 else "") + items[-1]


def stack_problem(kinds, only=None, catalogue=None, who="body"):
    """Why this set of garments cannot be worn together, in a sentence, or None. The Body Designer has checked a jacket or an apron over a shirt and trousers and over nothing else, and
    a skirt with a shirt and without trousers and a dress on its own (their `tested_stacks`), and a body wears one outer garment at most, so a set that has a jacket or an apron without the garments it is checked
    over, both of them, or a garment with another it has not been tested with, is refused rather than guessed at. `only`: the kinds to judge (the others are not looked at); `who`:
    "body" (what a body wears) or "outfit" (what is about to be put on it), for the words."""
    cat = catalogue if catalogue is not None else _catalogue()
    kinds = set(kinds)
    judged = [k for k in ORDER if k in kinds and k not in BASE and (only is None or k in only)]
    if len([k for k in OUTER if k in kinds]) > 1 and any(k in OUTER for k in judged):
        return "A body wears one outer garment: the open jacket or the bib apron, not both"
    for kind in judged:
        name = _label(kind, cat)
        under = needs(kind, cat) if kind in OUTER else worn_with(kind, cat)
        if under is None:
            return f"The Body Designer lists no garments the {name} has been checked {'over' if kind in OUTER else 'with'}, so it is not put on"
        odd = [o for o in ORDER if o in kinds and o != kind and o not in under]               # (worn, and not what its stacks hold)
        missing = [u for u in under if u not in kinds] if kind in OUTER else []
        if not odd and not missing:
            continue
        if kind in OUTER:
            head = f"The {name} goes over {_and([_noun(u) for u in under])} only (nothing else has been checked with it)"
        else:
            head = (f"The {name} is worn with {_and([_noun(u) for u in under])} only" if under else f"The {name} is worn on its own") + " (nothing else has been checked with it)"
        if odd:
            what = _and([_noun(o) for o in odd])
            return f"{head}, and " + (f"this body wears {what}" if who == "body" else f"this outfit has {what}")
        none = "neither" if len(missing) == len(under) and len(under) > 1 else "no " + _and(missing)
        return f"{head}, and " + (f"this body wears {none}" if who == "body" else f"this outfit has {none}")
    return None


def margin_problem(kind, eases, catalogue=None):
    """Why an outer garment cannot go over a shirt or trousers this loose, or None. `eases`: {'shirt': looseness in mm, 'trousers': ...} of the garments under it that are known; the
    limit is the Body Designer's `layer_margins` for `kind`."""
    cat = catalogue if catalogue is not None else _catalogue()
    margins = (cat.get(kind) or {}).get("layer_margins") or {}
    for under, ease in eases.items():
        limit = margins.get(f"{under}_ease_mm")
        if limit is not None and isinstance(ease, (int, float)) and ease > limit:
            return f"The {_label(kind, cat)} has been checked over {under if under == 'trousers' else 'a ' + under} no looser than {limit:g} mm, and this one is {ease:g} mm"
    return None


def worn_kinds(blob):
    """The garments (of ORDER) a body's GLB wears, by the nodes it carries; none for a file that is not a GLB (the Body Designer says what is wrong with it when it fits)."""
    try:
        names = {node.get("name") for node in Glb(blob).json.get("nodes", [])}
    except (ValueError, KeyError, TypeError, AttributeError, struct.error):
        return []
    return [kind for kind in ORDER if NODES[kind] in names]


def clean_colours(raw):
    """{kind: '#rrggbb'} from what a request says (a colour for the garments of ORDER), refusing a colour that is not one."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("colours must be an object: a colour like #3f6ba8 for each garment")
    out = {}
    for kind, value in raw.items():
        if kind not in NODES:
            raise ValueError(f"colours.{kind}: not a garment (one of {', '.join(ORDER)})")
        if value is None or value == "":
            continue
        if not isinstance(value, str) or not HEX.match(value.strip()):
            raise ValueError(f"colours.{kind}: the colour must be like #3f6ba8")
        out[kind] = value.strip().lower()
    return out


def pieces(mode, appearance, outer="sheet", colours=None):
    """{"pieces": [{"kind": 'shirt' | 'trousers' | 'skirt' | 'dress' | 'jacket' | 'apron', "colour": '#rrggbb' (absent: the Designer's own colour)}] from the body outward, "left": [the garment
    words the sheet names that are not put on], "why": {word: why a garment the Designer makes is left off}, "problem": "" or why what was asked for cannot be done} for a body of a
    character whose sheet says `appearance`: `sheet` the shirt and trousers the outfit names, `basic` a shirt and trousers whatever it says, `none` neither (in both a skirt the outfit
    names takes the place of the trousers, and is worn with the shirt; a dress takes the place of both). `outer` is what goes over them: `sheet` the jacket or apron the outfit names (the first of them if it names
    both: a body wears one) when the Designer makes it and the garments it is checked over are there, else it is left off and `why` says so; `none` nothing; `jacket` or `apron` that one,
    in the sheet's colour for it if the sheet names one (an outer garment asked for that cannot be put on is a `problem`, not a silent omission). `colours`: {kind: '#rrggbb'} the
    colour of a garment that is put on, instead of the sheet's or the Designer's own."""
    if mode not in MODES:
        raise ValueError("clothes must be " + ", ".join(MODES))
    if outer not in OUTERS:
        raise ValueError("outer must be " + ", ".join(OUTERS))
    colours = clean_colours(colours)
    if mode == "none" and outer in ("sheet", "none"):
        return {"pieces": [], "left": [], "why": {}, "problem": ""}
    seen = read((appearance if isinstance(appearance, dict) else {}).get("outfit"))
    chosen = [(kind, seen[part]) for kind, part in GARMENTS if seen[part] or mode == "basic"] if mode != "none" else []
    catalogue = _catalogue()
    left, why, problem, picked, stand_in = list(seen["left"]), {}, "", None, False      # (`stand_in`: a jacket the sheet names could not be put on, so it is the colour of the shirt, as before the Designer made jackets)
    instead = None                                                # the skirt or dress the sheet names, when it is put on (in place of trousers, and of the shirt too for a dress)
    for g in seen["instead"] if mode != "none" else []:
        if instead:
            reason = f"A body wears one garment of the legs, and the {instead['kind']} came first"
        elif g["kind"] not in catalogue:
            reason = "This Body Designer does not make it"
        else:
            reason, instead = "", g
        if reason:
            left.append(g["word"])
            why[g["word"]] = reason
    if instead:
        gone = takes_off(instead["kind"], catalogue) or []
        for kind in gone:                                         # (a sheet that names trousers and a skirt, or a shirt and a dress: the skirt or the dress is the garment seen, the others are said)
            held = (seen["top" if kind == "shirt" else "bottom"] or {}).get("word")
            if held and held not in left:
                left.append(held)
                why[held] = f"A body in {_noun(instead['kind'])} wears no {kind} (nothing else has been checked with it)"
        chosen = [(kind, g) for kind, g in chosen if kind not in gone]
    kinds = [kind for kind, _ in chosen] + ([instead["kind"]] if instead else [])
    named = {o["kind"]: o for o in seen["over"]}
    if outer == "sheet":
        for o in seen["over"]:
            if picked:
                reason = f"A body wears one outer garment, and the {picked} came first"
            elif o["kind"] not in catalogue:
                reason = "This Body Designer does not make it"
                stand_in = stand_in or o["kind"] == "jacket"
            else:
                reason = stack_problem(kinds + [o["kind"]], only=[o["kind"]], catalogue=catalogue, who="outfit")
                stand_in = stand_in or bool(reason) and o["kind"] == "jacket"
            if reason:
                left.append(o["word"])
                why[o["word"]] = reason
            else:
                picked = o["kind"]
    elif outer != "none":
        picked = outer
        problem = ("This Body Designer does not make the " + outer) if outer not in catalogue else (stack_problem(kinds + [outer], only=[outer], catalogue=catalogue, who="outfit") or "")
        if problem:
            picked = None
    out = []
    for kind, g in chosen:
        colour = colours.get(kind) or (g.get("colour" if kind != "shirt" or stand_in else "under") if g else None)
        out.append({"kind": kind, **({"colour": colour} if colour else {})})
    if instead:
        colour = colours.get(instead["kind"]) or instead["colour"]
        out.append({"kind": instead["kind"], **({"colour": colour} if colour else {})})
    if picked:
        colour = colours.get(picked) or (named[picked]["colour"] if picked in named else None)
        out.append({"kind": picked, **({"colour": colour} if colour else {})})
    return {"pieces": out, "left": left, "why": why, "problem": problem}


def _recipe(garments, kind, colour, **more):
    """The Designer's recipe for a garment in this colour (and `more`: ease_mm, length, texture, as the Items tab's designer sets them); the shirt of the newest version this Body Designer knows."""
    recipe = {**next(e for e in garments.catalogue() if e["id"] == kind)["recipe"], **({"colour": colour} if colour else {}), **{k: v for k, v in more.items() if v is not None}}
    if kind == "shirt":
        try:
            return garments.clean_recipe({**recipe, "version": SHIRT_VERSION})
        except ValueError:
            pass                                                  # this Body Designer does not make that shirt (yet)
    return garments.clean_recipe(recipe)


def _why(exc):
    """What went wrong, in a line: the Designer's own refusals say it themselves, anything else names what it was."""
    return str(exc) if isinstance(exc, ValueError) else f"{type(exc).__name__}: {exc}"


def _dress(garments, bodies, fitted):
    """The bodies (a list, `None` for a file that is not there) each wearing the fitted garments."""
    return [None if body is None else garments.dress(body, fitted) for body in bodies]


def _gone(bodies, new):
    """The nodes a body must not carry once garments of the kinds `new` are put on (`kept`): the ones of the same kinds (a body made with its clothes on wears them), the other outer
    garment when one of these is a jacket or an apron (a body wears one), and what a shirt, trousers, skirt or dress is not worn with."""
    now = {kind for body in bodies if body is not None for kind in worn_kinds(body)}
    return [NODES[kind] for kind in ORDER if kind in now and kind not in kept(now, new)]


def wear(plain, animated, wanted, replace=False):
    """(plain, animated, report): the two files of a body, each wearing the garments in `wanted` ([{kind, colour, ease_mm, length, texture}], as `pieces` makes them: only the kind is
    needed), all fitted to the plain file with its underwear on, from the body outward (shirt, trousers, skirt, dress, jacket or apron). Either file may be None (one file only: the garments are
    fitted to the one that is there). A garment that cannot be fitted to this body is left off and said so: {"worn": [{kind, colour, version, ease_mm, length, texture, triangles, seconds,
    over_underwear_mm}], "failed": {kind: why}}. A jacket or an apron is put on only over a shirt and trousers and a skirt or a dress only with the garments it was checked with (`stack_problem`: the
    ones the body wears and the ones put on with it, as they come out), and a shirt or trousers only as loose as the Body Designer has checked them under what the body wears
    (`margin_problem`); one that is not allowed is left off and said so, and garments that are not worn together are refused. With `replace`, a garment of the same kind that the body
    already wears (a body made with its clothes on) is taken off first, once the new one is known to fit (the report's "replaced" names what came off), so a body is never left with two
    shirts or with none because the new one did not fit; a jacket or an apron also takes off the other one, a skirt takes off the trousers and a dress the shirt and the trousers (`kept`)."""
    try:
        from bodydesigner import garments
    except ImportError:
        raise ValueError("The Body Designer has no garments") from None
    if plain is None and animated is None:
        raise ValueError("There is no body to dress")
    unknown = [piece["kind"] for piece in wanted if piece["kind"] not in NODES]
    if unknown:
        raise ValueError(f"The garments here are {', '.join(ORDER)}, not {unknown[0]!r}")
    catalogue = _catalogue()
    if sum(piece["kind"] in OUTER for piece in wanted) > 1:
        raise ValueError(stack_problem([piece["kind"] for piece in wanted], catalogue=catalogue, who="outfit"))
    problem = stack_problem([piece["kind"] for piece in wanted if piece["kind"] not in OUTER], catalogue=catalogue, who="outfit")           # (garments that are not worn together: a skirt and trousers, a dress and a shirt)
    if problem:
        raise ValueError(problem)
    wanted = sorted(wanted, key=lambda piece: ORDER.index(piece["kind"]))
    now = worn_kinds(plain if plain is not None else animated)
    new = {piece["kind"] for piece in wanted}
    keep = kept(now, new, catalogue) if replace else set(now)       # (what the body keeps wearing)
    fitted, worn, failed = [], [], {}
    for piece in wanted:
        if piece["kind"] not in BASE:                             # (before the half minute it takes: an outer garment over a body that does not wear what it is checked over, a skirt beside trousers, a dress beside a shirt)
            problem = stack_problem(keep | new, only=[piece["kind"]], catalogue=catalogue)
            if problem:
                failed[piece["kind"]] = problem
                new.discard(piece["kind"])
    for piece in wanted:
        if piece["kind"] in failed:
            continue
        recipe = _recipe(garments, piece["kind"], piece.get("colour"), **{k: piece.get(k) for k in ("ease_mm", "length", "texture")})
        problem = next(filter(None, (margin_problem(over, {piece["kind"]: recipe["ease_mm"]}, catalogue) for over in ORDER if over != piece["kind"] and over in keep | new)), None) if piece["kind"] in BASE else None
        if problem:
            failed[piece["kind"]] = problem
            new.discard(piece["kind"])
            continue
        started = time.perf_counter()
        try:
            glb, info = garments.fit(plain if plain is not None else animated, recipe)
        except Exception as exc:                                  # the Designer refuses with a ValueError; its geometry can also assert on a shape it was not made for: either way this garment is left off
            failed[piece["kind"]] = _why(exc)
            new.discard(piece["kind"])
            continue
        fitted.append(glb)
        layer = info.get("support_layer") or {}                    # the Designer's own measure of how far the garment stands off the body where the underwear is
        worn.append({"kind": piece["kind"], "colour": recipe["colour"], "version": recipe["version"], "ease_mm": recipe["ease_mm"], "length": recipe["length"], "texture": recipe["texture"],
                     "triangles": info["triangles"], "seconds": round(time.perf_counter() - started, 1),
                     "over_underwear_mm": round(layer["min_body_clearance_under_layer_mm"], 2) if "min_body_clearance_under_layer_mm" in layer else None})
    for glb, entry in [pair for pair in zip(fitted, worn) if pair[1]["kind"] not in BASE]:    # (an outer garment whose shirt or trousers did not fit has nothing to go over)
        problem = stack_problem(keep | {e["kind"] for e in worn}, only=[entry["kind"]], catalogue=catalogue)
        if problem:
            fitted.remove(glb)
            worn.remove(entry)
            failed[entry["kind"]] = problem
    replaced = []
    if fitted:
        try:
            stripped, taken = _strip([plain, animated], _gone([plain, animated], {e["kind"] for e in worn}) if replace else [])
            plain_out, animated_out = _dress(garments, stripped, fitted)
            replaced = taken
        except Exception as exc:                                   # all or nothing would lose a shirt that fits for a pair of trousers that does not: try them one by one, each from the body as it was
            plain_out, animated_out, done = plain, animated, []
            for glb, entry in zip(fitted, worn):
                if entry["kind"] not in BASE:
                    problem = stack_problem(keep | {e["kind"] for e in done} | {entry["kind"]}, only=[entry["kind"]], catalogue=catalogue)          # (over what is on the body by now)
                    if problem:
                        failed[entry["kind"]] = problem
                        continue
                try:
                    stripped, taken = _strip([plain_out, animated_out], _gone([plain_out, animated_out], {entry["kind"]}) if replace else [])
                    plain_out, animated_out = _dress(garments, stripped, [glb])
                    done.append(entry)
                    replaced += taken
                except Exception as one:                           # (this garment's old one, if any, was not taken off: the body keeps it)
                    failed[entry["kind"]] = _why(one)
            worn = done
            if not done:
                failed.setdefault("clothes", _why(exc))
        plain, animated = plain_out, animated_out
    return plain, animated, {"worn": worn, "failed": failed, **({"replaced": replaced} if replace else {})}


def _strip(bodies, names):
    """(the bodies without the garment meshes called `names`, the names that were on them): what a body made with its clothes on wears of the kinds about to be put on."""
    if not names:
        return list(bodies), []
    out, found = [], []
    for body in bodies:
        if body is None:
            out.append(None)
            continue
        body, taken = glbprune.remove_nodes(body, names)
        out.append(body)
        found = sorted(set(found) | set(taken))
    return out, found


def main(argv=None):
    p = argparse.ArgumentParser(description="Dress a designed body (the plain file, the animated one, or both) in garments, each fitted to the plain one (else to the animated one).")
    p.add_argument("--plain", type=Path)
    p.add_argument("--animated", type=Path)
    p.add_argument("--pieces", type=Path, required=True, help="a JSON file: [{kind, colour, ease_mm, length, texture}] (only the kind is needed)")
    p.add_argument("--out-plain", type=Path)
    p.add_argument("--out-animated", type=Path)
    p.add_argument("--replace", action="store_true", help="take off a garment of the same kind that the body already wears, once the new one is known to fit")
    p.add_argument("--report", type=Path, required=True)
    args = p.parse_args(argv)
    for name in ("plain", "animated"):
        if (getattr(args, name) is None) != (getattr(args, "out_" + name) is None):
            p.error(f"--{name} and --out-{name} go together")
    if args.plain is None and args.animated is None:
        p.error("give a body: --plain with --out-plain, --animated with --out-animated, or both")
    try:
        wanted = json.loads(args.pieces.read_text("utf-8"))
        plain, animated, report = wear(None if args.plain is None else args.plain.read_bytes(), None if args.animated is None else args.animated.read_bytes(), wanted, replace=args.replace)
        if args.plain is not None:
            args.out_plain.write_bytes(plain)
        if args.animated is not None:
            args.out_animated.write_bytes(animated)
        args.report.write_text(json.dumps(report), "utf-8")
    except (ValueError, OSError, KeyError) as exc:
        p.error(str(exc))
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
