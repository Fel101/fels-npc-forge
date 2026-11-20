"""A designed body that looks like its character: what the sheet says about the build, the skin, the eyes and the hair, as pieces of a Body Designer recipe.

The tables are data (data/body_match.json); this module only reads words. A build is read word by word and the entries it names are added together
('tall and lanky' is `tall` plus `lanky`); a skin, eye or hair colour is a phrase from the table (the first one the text names, the longest there) or,
for skin and eyes, colour words with modifiers ('pale green-gray'). A word that is nowhere in the tables is left out and said so, never guessed: the
caller shows `skipped` beside what was `said`. Nothing here needs the Body Designer, a GPU or a file other than the table.
"""
import colorsys
import json
import re
from functools import lru_cache

from . import config as cfgmod

DATA_FILE = cfgmod.ROOT / "data" / "body_match.json"
FIELDS = {"build": "build", "skin_tone": "skin", "eyes": "eyes", "hair": "hair"}      # the sheet's field -> what it decides


@lru_cache(maxsize=1)
def data():
    return json.loads(DATA_FILE.read_text("utf-8"))


def _words(text):
    """The text as lowercase words between single spaces, with one at each end ('Dark-brown, wavy' -> ' dark brown wavy ')."""
    return " " + " ".join(re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).split()) + " "


def build_values(text):
    """({control: -1..1}, [the build words it understood]) for a build such as 'tall and lanky'. A longer word is taken first and its words are not read
    again ('broad shouldered' is one thing, not 'broad' twice), and an entry counts once however many of its words are there."""
    entries = data()["builds"]
    words = sorted(((w, i) for i, e in enumerate(entries) for w in e["words"]), key=lambda pair: -len(pair[0]))
    rest, used, heard, total = _words(text), set(), [], {}
    for word, index in words:
        if index in used or f" {word} " not in rest:
            continue
        used.add(index)
        heard.append(word)
        rest = rest.replace(f" {word} ", " " * (len(word) + 2))
        for control, value in entries[index]["values"].items():
            total[control] = total.get(control, 0.0) + value
    return {k: max(-1.0, min(1.0, round(v, 3))) for k, v in total.items() if v}, heard


def _rgb(hex_colour):
    return tuple(int(hex_colour[i:i + 2], 16) / 255 for i in (1, 3, 5))


def _hex(rgb):
    return "#" + "".join(f"{round(max(0.0, min(1.0, c)) * 255):02x}" for c in rgb)


def colour(text, section, table=None):
    """'#rrggbb' for the colour a text names in a table's section ('skin', 'eyes' or 'hair'; or in `table`, another table of the same shape: the cloth colours
    of npcgen/outfit.py), or None when it names none. The first phrase the text
    mentions wins (the longest where two start together), so 'dark brown wavy hair with a white streak' is dark brown. A phrase is not enough when the
    text has another colour word beside it ('pale sage green' is not 'pale' skin) and for skin and eyes the single colour words then decide: the last
    is the colour, an earlier colour beside it tints it a third ('gray green'), and modifiers lighten or darken."""
    table = table or data()[section]
    words = _words(text)
    if section == "hair":
        words = re.sub(r" no [a-z0-9]+(?= )", " ", words)                        # 'no pink' is what the character does not have
        if any(f" {w} " in words for w in table.get("ignore", ())):
            return None
    colours = table.get("colours") or {}
    tokens = words.split()
    found = [(words.find(f" {p} "), -len(p), p) for p in table["phrases"] if f" {p} " in words]
    if found:
        phrase = min(found)[2]
        if not [t for t in tokens if t in colours and t not in phrase.split()]:
            return table["phrases"][phrase]
    named = [t for t in tokens if t in colours]
    if not named:
        return table["phrases"][min(found)[2]] if found else None
    base = _rgb(colours[named[-1]])
    if len(named) > 1 and named[-2] != named[-1]:
        other = _rgb(colours[named[-2]])
        base = tuple(0.65 * a + 0.35 * b for a, b in zip(base, other))
    h, lightness, saturation = colorsys.rgb_to_hls(*base)
    for token in tokens:
        if token in table.get("modifiers", {}):
            dl, ds = table["modifiers"][token]
            lightness, saturation = lightness + dl, saturation + ds
    return _hex(colorsys.hls_to_rgb(h, max(0.04, min(0.96, lightness)), max(0.0, min(1.0, saturation))))


def _has(words, phrase):
    return f" {phrase} " in words


def _unshaved(text):
    """The hair text without 'bald' and 'shaved': they say there is little hair, not what colour it is ('black buzz cut with a shaved pattern')."""
    return re.sub(r"\b(bald|shaved|shaven)\b", " ", str(text), flags=re.I)


def _says(words, table, *keys):
    return any(_has(words, w) for key in keys for w in table[key])


def hair_style(text):
    """What a sheet's hair text is, among the Body Designer's game hair: 'crop', 'sweep' or 'long', 'none' for a bald head, or None when it names a style that has no game hair
    (braids, a bun, a bob...) or none at all. A style that has none wins over the rest ('short ponytail' is not a crop), a bald head over a cut that is not named, and a swept
    style over a short one ('short, swept to one side'). The Long is the Body Designer's one long style, straight and smooth: a text that says long and nothing against it is that
    ('wavy long hair' too: the curl is the one thing it lacks, and the page says so). Long hair that is also braided, tied back or in a ponytail is not exactly it (`hair_nearest`),
    and nor is long beside short, a bun or a bald head."""
    table, words = data()["hair_styles"], _words(text)
    if _says(words, table, "long"):
        return None if _says(words, table, "not_long", "crop", "none", "long_near") else "long"
    if _says(words, table, "other"):
        return None
    for style in ("sweep", "crop"):
        if _says(words, table, style):
            return style
    return "none" if _says(words, table, "none") else None


def hair_nearest(text):
    """'long' for a sheet that says long hair in a way the Body Designer's Long is not (braided, tied back, a ponytail, swept over a shoulder, curtain bangs): the nearest game hair it
    has, to be said so; None for everything else: the text that is the Long exactly (`hair_style` says 'long'), long hair that is up, short or bald, and every text with no long word."""
    table, words = data()["hair_styles"], _words(text)
    if hair_style(text) is not None or not _says(words, table, "long"):
        return None
    return None if _says(words, table, "not_long", "crop", "none") else "long"


def hair_colour(text):
    """'#rrggbb' for the colour of the hair itself, which unlike a brow may be dyed ('pastel pink'); else the natural colour the text names (`colour`), else None."""
    words = re.sub(r" no [a-z0-9]+(?= )", " ", _words(text))
    dyed = data()["dyed"]
    found = [(words.find(f" {p} "), -len(p), p) for p in dyed if f" {p} " in words]
    return dyed[min(found)[2]] if found else colour(_unshaved(text), "hair")


def match(appearance, controls=None):
    """What a sheet's `appearance` says about the body, as parts of a Body Designer recipe:
    {"values": {control: v}, "materials": {"skin_color", "iris_color"}, "brows": {"color"} or {}, "hair": {"style": 'crop'|'sweep'|'long'|'none'|None, "near": 'long' only when long hair is the nearest (`hair_nearest`), "color": ...} (the game hair
    the sheet's hair text asks for), "said": {field: the sheet's words}, "skipped": {field: ...}}.
    `controls` (the base's proportions) limits `values`. An empty or unknown field changes nothing: empty ones are in neither `said` nor `skipped`. The sheet's hair says two
    things: its colour (the brows' colour, as `hair`, and the hair's own, which may be dyed) and its style (`hair_style`)."""
    appearance = appearance if isinstance(appearance, dict) else {}
    out = {"values": {}, "materials": {}, "brows": {}, "hair": {}, "said": {}, "skipped": {}}
    for field in FIELDS:
        text = appearance.get(field)
        if not isinstance(text, str) or not text.strip():
            continue
        text = text.strip()
        if field == "hair":
            style, dyed, near = hair_style(text), hair_colour(text), hair_nearest(text)
            out["hair"] = {"style": style, **({"near": near} if near else {}), **({"color": dyed} if dyed else {})}
            (out["said"] if style else out["skipped"])["hair_style"] = text
            if style == "none":                                                   # a bald head has no colour to say
                continue
        if field == "build":
            values, heard = build_values(text)
            if controls is not None:
                values = {k: v for k, v in values.items() if k in controls}
            out["values"].update(values)
            known = bool(heard)
        else:
            value = colour(_unshaved(text) if field == "hair" else text, FIELDS[field])
            known = value is not None
            if known:
                if field == "skin_tone":
                    out["materials"]["skin_color"] = value
                elif field == "eyes":
                    out["materials"]["iris_color"] = value
                else:
                    out["brows"] = {"color": value}
        (out["said"] if known else out["skipped"])[field] = text
    return out
