"""A designed body's skin marks: which of the Body Designer's permanent skin marks a character's sheet names, and where.

The Body Designer paints six kinds of skin mark (freckles, vitiligo, moles, beauty marks, birthmarks, scars) in eleven places (bodydesigner/skin_marks.py). A recipe's `appearance.marks` is a list of at most 16,
each a kind, a place, a size in millimetres, a strength, a seed and, optionally, a side and (for a scar) a direction; the marks are painted into the skin when the body is made and stay for good:
changing them means making the body again. A sheet's `distinguishing` and `extra` fields are free text ('a dueling scar on one cheek', 'freckles across the nose', 'vitiligo', or a whole description of the
character), so `read` finds the words that name a kind, the words that name a place close to them and the words that change a mark ('small', 'faded'), with the tables of data/marks.json. What the Designer does not
paint (a tattoo, a burn, dimples) or has no place for (the chin, the lip) is left out and said, never guessed; freckles and vitiligo, which are spread over the skin, get the places such a skin usually has when the
sheet names none, and a scar, a mole or a birthmark that names no place is left out and said. `describe` says it in words for the card. Nothing here needs the Body Designer except `catalogue`, the Designer's own list
of kinds, places and size ranges, which `read` takes as it is: a Body Designer that paints no marks gives none.
"""
import json
import re
import zlib
from functools import lru_cache

from . import config as cfgmod

DATA_FILE = cfgmod.ROOT / "data" / "marks.json"
FIELDS = ("distinguishing", "extra")                         # the sheet's appearance fields that can name a mark
PAIRED = ("cheeks", "shoulders", "arms", "hands", "legs")    # the places the Designer paints on both sides of the body (its `side` applies to every place, but only these have a second one)
SEPARATORS = (",", "and", "with", "but")                     # a list of places goes on through them; anything else after them is about something else
KIND_WORDS = {"freckles": ("freckles", "freckles"), "vitiligo": ("vitiligo", "vitiligo"), "mole": ("a mole", "moles"), "beauty_mark": ("a beauty mark", "beauty marks"),
              "birthmark": ("a birthmark", "birthmarks"), "scar": ("a scar", "scars")}
PLACE_WORDS = {"face": "the face", "nose": "the nose", "forehead": "the forehead", "neck": "the neck", "chest": "the chest", "back": "the back",
               "cheeks": "cheek", "shoulders": "shoulder", "arms": "arm", "hands": "hand", "legs": "leg"}


@lru_cache(maxsize=1)
def data():
    return json.loads(DATA_FILE.read_text("utf-8"))


def catalogue():
    """The Body Designer's own skin-marks catalogue (bodydesigner/skin_marks.py: kinds with their size ranges in millimetres, places, the most marks), or {} when this Body Designer paints none."""
    try:
        from bodydesigner import skin_marks
        found = skin_marks.catalogue()
    except (ImportError, AttributeError, OSError, ValueError, KeyError, TypeError):
        return {}
    return found if isinstance(found, dict) and found.get("kinds") and found.get("places") else {}


def offered():
    """True when this Body Designer paints skin marks."""
    return bool(catalogue())


# ---- reading the sheet ---------------------------------------------------------------------------------------------------------------------------
def _sections(text):
    """[[word, ...], ...]: the words of a text in lower case (a comma is a word of its own), one list to each sentence (a full stop, a semicolon or a new line ends one)."""
    parts = re.split(r"[;.\n]+", str(text or "").lower().replace("’", "'"))
    return [words for words in (re.findall(r"[a-z]+|,", part) for part in parts) if words]


def _mentions(words, table):
    """[(start, end, "kind" | "unmade", value)] in the order of the section: the words that name a kind of mark and the ones that name something the Designer does not paint, a word once, a longer name first.
    A burn that is followed at once by a scar ('a burn scar') is one thing, and it is not painted."""
    names = [(tuple(name.split()), "kind", kind) for kind, listed in table["kinds"].items() for name in listed]
    names += [(tuple(name.split()), "unmade", name) for name in table["unmade"]]
    names.sort(key=lambda entry: -len(entry[0]))
    taken, found = set(), []
    for tokens, type_, value in names:
        for i in range(len(words) - len(tokens) + 1):
            if tuple(words[i:i + len(tokens)]) == tokens and not taken & set(range(i, i + len(tokens))):
                taken |= set(range(i, i + len(tokens)))
                found.append((i, i + len(tokens), type_, value))
    found.sort()
    return [m for j, m in enumerate(found) if not (m[2] == "kind" and j and found[j - 1][2] == "unmade" and found[j - 1][1] == m[0])]


def _inside(mentions, p):
    return any(start <= p < end for start, end, *_ in mentions)


def _claims(words, mentions, known, reach):
    """{index of a place word: index of the mention it belongs to}: a place word right before a mark word ('a cheek scar') belongs to it, and the place words after a mark word belong to it for at most `reach`
    words, up to the next mark word, through a list of places ('on the nose, cheeks and shoulders': a comma, 'and' or 'with' goes on only when a place follows within two words, so 'a scarred jaw, large cracked hands' stops)."""
    def back_of(p):                                               # 'the back of his hand' is not the back
        return words[p] == "back" and "of" in words[p + 1:p + 3]

    claimed = {}
    for i, (start, *_rest) in enumerate(mentions):
        p = start - 1
        if p >= 0 and words[p] in known and not _inside(mentions, p) and not back_of(p):
            claimed[p] = i
    for i, (_start, end, *_rest) in enumerate(mentions):
        stop = min(mentions[i + 1][0] if i + 1 < len(mentions) else len(words), end + reach)
        for p in range(end, stop):
            if words[p] in known:
                if p not in claimed and not back_of(p):
                    claimed[p] = i
            elif words[p] in SEPARATORS and not any(w in known for w in words[p + 1:p + 3]):
                break
    return claimed


def _side_word(words, p, mentions):
    """'left', 'right', 'both' or 'one' when one of the three words before the place word at `p` says so ('the left cheek', 'both hands', 'one eyebrow'), else None."""
    for j in range(p - 1, max(p - 4, -1), -1):
        if _inside(mentions, j) or words[j] == ",":
            break
        if words[j] in ("left", "right"):
            return words[j]
        if words[j] in ("both", "each", "either"):
            return "both"
        if words[j] == "one":
            return "one"
    return None


def _factors(words, start, mentions, table):
    """(size factor, strength factor) from the words that change a mark in the three words before it ('a small mole', 'faint scars')."""
    size = strength = 1.0
    for j in range(start - 1, max(start - 4, -1), -1):
        if _inside(mentions, j) or words[j] == ",":
            break
        change = table["modifiers"].get(words[j])
        if change:
            size *= change.get("size", 1.0)
            strength *= change.get("strength", 1.0)
    return size, strength


def _phrase(words, mentions, i, upto, table):
    """The sheet's own words for a mark that is not painted, as a short phrase ('a tattoo on the forearm')."""
    start, end = mentions[i][0], mentions[i][1]
    first, starts = start, set(table["phrase_starts"])
    while first > 0 and words[first - 1] in starts and not _inside(mentions, first - 1):
        first -= 1
    return " ".join(w for w in words[first:max(end, upto + 1)] if w != ",")[:60]


def _seed(ident, kind, place, side, n):
    return zlib.crc32(f"{ident}|{kind}|{place}|{side}|{n}".encode()) & 0x7FFFFFFF


def _tone(seed, dark):
    """The Designer paints a scar pale when its seed is even and dark when it is odd: `dark` says which the sheet's words want (None: the seed as it is)."""
    return seed if dark is None else (seed & ~1) | (1 if dark else 0)


def _read_section(words, table, ranges, allowed, ident, out):
    mentions = _mentions(words, table)
    if not mentions:
        return
    known = set(table["places"]) | set(table["no_place"])
    owned = {i: [] for i in range(len(mentions))}               # mention -> [(index of the word, the Designer's place or None, the word)]
    for p, i in sorted(_claims(words, mentions, known, int(table.get("reach") or 8)).items()):
        place = table["places"].get(words[p])
        owned[i].append((p, place if place in allowed else None, words[p]))
    for i, (start, end, type_, value) in enumerate(mentions):
        found = owned[i]
        upto = max([end - 1] + [p for p, *_ in found])
        if type_ == "unmade" or value not in ranges:
            out["skipped"].append(_phrase(words, mentions, i, upto, table))
            continue
        kind, label = value, KIND_WORDS[value][0]
        default = table["defaults"][kind]
        made = [entry for entry in found if entry[1]]
        for _p, place, word in found:
            if not place:
                out["skipped"].append(f"{label} on the {word}")
        if found and not made:
            continue
        if not found and default.get("place_required"):
            out["skipped"].append(f"{label} (no place named)")
            continue
        if not found:
            out["assumed"].append(kind)
        size, strength = _factors(words, start, mentions, table)
        tone = None
        around = words[max(0, start - 3):start] + words[end:end + 2]
        if kind == "scar":
            tone = True if any(w in table["scar"]["dark"] for w in around) else False if any(w in table["scar"]["pale"] for w in around) else None
        across = kind == "scar" and "across" in words[max(0, start - 3):upto + 1]
        for position, place, word in (made or [(None, entry["place"], None) for entry in default["places"]]):
            if position is None:
                entry = next(e for e in default["places"] if e["place"] == place)
                spec, plural, base = entry.get("side"), False, entry.get("size", default["size"])
            else:
                spec, plural, base = _side_word(words, position, mentions), word.endswith("s"), default["size"]
            n = sum(1 for m in out["marks"] if m["kind"] == kind and m["place"] == place)
            seed = _seed(ident, kind, place, spec or ("both" if plural else "one"), n)
            side = spec if spec in ("left", "right") else "both" if spec == "both" or (plural and spec is None) else None
            if side is None and (spec == "one" or place in PAIRED):
                side = "left" if seed % 2 == 0 else "right"       # one cheek, a mole on the cheek: either, the same one every time for this character
            if place == "nose":
                side = None
            low, high = ranges[kind]
            mark = {"kind": kind, "place": place, "size": float(min(high, max(low, round(base * size, 1)))), "strength": round(min(1.0, max(0.0, default["strength"] * strength)), 2), "seed": seed}
            if side in ("left", "right"):
                mark["side"] = side
            if kind == "scar":
                direction = float((table["scar"]["across_direction"] if across else table["scar"]["direction"]).get(place, 0))
                mark["direction"] = round((360 - direction) % 360 if side == "right" else direction, 1)
                mark["seed"] = _tone(seed, tone)
            if not any((m["kind"], m["place"], m.get("side")) == (kind, place, mark.get("side")) for m in out["marks"]):
                out["marks"].append(mark)


def read(appearance, ident="", cat=None):
    """{"marks": [the Designer's recipe marks the sheet names], "skipped": [the sheet's own words for what is not painted], "assumed": [the kinds the sheet names with no place, which are put where
    such a skin has them], "dropped": how many marks were over the Designer's limit} for a character whose sheet says `appearance`. `ident` (the character's id) is what the marks' seeds are made from: the same
    character always gets the same marks, and two characters with the same words do not get the same freckles. `cat` is the Designer's catalogue (`catalogue()`: None asks for it); without one nothing is made."""
    cat = catalogue() if cat is None else cat
    result = {"marks": [], "skipped": [], "assumed": [], "dropped": 0}
    if not cat:
        return result
    table = data()
    ranges = {kind["id"]: tuple(kind["size_mm"]) for kind in cat["kinds"]}
    allowed = set(cat["places"])
    ap = appearance if isinstance(appearance, dict) else {}
    for field in FIELDS:
        for words in _sections(ap.get(field)):
            _read_section(words, table, ranges, allowed, str(ident or ""), result)
    limit = int(cat.get("maximum_marks") or 16)
    result["dropped"] = max(0, len(result["marks"]) - limit)
    result["marks"] = result["marks"][:limit]
    result["skipped"] = list(dict.fromkeys(result["skipped"]))
    result["assumed"] = [kind for kind in dict.fromkeys(result["assumed"]) if any(m["kind"] == kind for m in result["marks"])]
    return result


# ---- saying it ------------------------------------------------------------------------------------------------------------------------------------
def _where(mark):
    place, side = mark["place"], mark.get("side")
    noun = PLACE_WORDS.get(place, "the " + place)
    if place in PAIRED:
        return f"both {noun}s" if side in (None, "both") else f"the {side} {noun}"
    return noun if side is None or place == "nose" else f"the {side} side of {noun}"


def _and(items):
    return ", ".join(items[:-1]) + (" and " if len(items) > 1 else "") + items[-1]


def describe(marks):
    """The marks in words for the card: 'freckles on the nose and both cheeks; a scar on the left cheek'. An empty list is an empty string."""
    groups = {}
    for mark in marks or []:
        groups.setdefault(mark["kind"], []).append(_where(mark))
    out = []
    for kind, places in groups.items():
        single, plural = KIND_WORDS[kind]
        out.append(f"{single if len(places) == 1 else plural} on {_and(list(dict.fromkeys(places)))}")
    return "; ".join(out)


def summary(appearance, ident=""):
    """For the 3D model card: None when this Body Designer paints no marks, else {"marks": the recipe marks the sheet names, "text": them in words, "skipped": the sheet's words for what is not painted,
    "assumed": the kinds whose places the sheet does not say, "dropped"}."""
    cat = catalogue()
    if not cat:
        return None
    found = read(appearance, ident, cat)
    return {**found, "text": describe(found["marks"])}
