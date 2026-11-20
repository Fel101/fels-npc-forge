"""Build Mage-Flow prompts for each NPC asset from the NPC record and an art-style preset."""
import re

from . import morphology as morphology_mod

KEY_PHRASES = {
    "magenta": "a solid flat pure magenta (#FF00FF) background",
    "green": "a solid flat pure green (#00FF00) background",
    "blue": "a solid flat pure blue (#0000FF) background",
}
VIEWS = {
    "front": "front view, facing the camera",
    "three_quarter": "three-quarter view, slightly turned",
    "side": "side profile view",
}
PINKISH = ("pink", "magenta", "purple", "violet", "fuchsia", "rose", "lilac", "mauve", "lavender")
GREENISH = ("green", "teal", "lime", "mint", "olive", "emerald", "turquoise")
BLUEISH = ("blue", "navy", "cobalt", "indigo", "cyan", "azure")
# occupations that say nothing about how someone looks, so they stay out of the image prompt
SILENT_OCCUPATIONS = {"job seeker", "stay-at-home parent", "retiree", "retired settler", "wandering traveler"}
# The shared builds, worded by what a head-and-shoulders picture can show and put before the age. "stocky build" in the middle
# of the list drew the same slim man as no build at all (Qwen Image 2.1 and Mage-Flow, 2026-09-23); "a stocky, broad-shouldered,
# thick-necked 24-year-old man" drew him visibly broader, and heavyset and slim came out different again. Other builds (a
# setting's own, or typed in) keep the old "<build> build" wording further on.
BUILD_LOOKS = {"slim": "slim, narrow-shouldered", "athletic": "athletic, fit", "stocky": "stocky, broad-shouldered, thick-necked",
               "heavyset": "heavyset, round-faced, thick-set", "muscular": "muscular, broad-chested", "curvy": "curvy",
               "petite": "petite, small-framed", "tall and lanky": "tall and lanky, long-necked"}


def pick_key(npc, style=None):
    """Backdrop colour for cut-outs: magenta unless the character wears something pink, then green (or blue)."""
    a = npc["appearance"]
    # what covers a lot of the figure: clothing, hair, skin (green-skinned orcs) and the free-text description
    text = " ".join(str(a.get(k, "")) for k in ("outfit", "accessory", "hair", "skin_tone", "extra")).lower()
    if (style or {}).get("sprite_key_preference") == "blue":
        if not any(w in text for w in BLUEISH):
            return "blue"
        return "green" if not any(w in text for w in GREENISH) else "magenta"
    if any(w in text for w in PINKISH):
        return "blue" if any(w in text for w in GREENISH) else "green"
    return "magenta"


def person_word(profile):
    age, g = profile["age"], profile["gender"]
    if age < 13:
        return {"female": "girl", "male": "boy"}.get(g, "child")
    if age < 18:
        return {"female": "teenage girl", "male": "teenage boy"}.get(g, "teenager")
    return {"female": "woman", "male": "man"}.get(g, "androgynous person")


def _aging(age):
    if age >= 75:
        return "an elderly face with deep wrinkles"
    if age >= 60:
        return "a weathered face with wrinkles"
    if age >= 45:
        return "some fine lines and laugh lines"
    return ""


# Singular too: the zombie outfits say "one shoe missing" and "a single scuffed boot", which a portrait draws at the bottom of the frame.
# Measured 2026-10-13 on the 744 outfits of the data tables: skirts (26), trainers, oxfords, breeches, stockings, socks, waders, spats and
# bare feet had slipped through, and a gown "with a slit skirt" pulled the picture down to the hips.
_FOOTWEAR = r"sneakers?|shoes?|boots?|heels?|loafers?|clogs?|sandals?|flats|brogues?|pumps|slippers?|oxfords|trainers|spats|socks|stockings|waders|gaiters"
# a skirt SUIT stays: it is the jacket
_LOWER_BODY = re.compile(rf"\b(jeans|trousers|pants|slacks|shorts|joggers|leggings|chinos|khakis|tights|breeches|bare feet|skirts?(?!\s+suits?)|{_FOOTWEAR})\b", re.I)
# the footwear at the end and the "with"/"and" right before it, never an earlier one: "scrubs with a stethoscope and white clogs"
# keeps the stethoscope, "a robe, a shawl and a staff with leather shoes" keeps the staff
_WITH_FOOTWEAR = re.compile(rf"(?:,?\s+with|\s+and)\s+(?:(?!\b(?:with|and)\b)[^,])*\b({_FOOTWEAR})\b[^,]*$", re.I)
# the same for a skirt inside a part: "a fitted gown with a slit skirt" keeps the gown, "a long apron over a plain skirt" keeps the apron
# (a part that IS a skirt, "a long skirt", goes whole)
_WITH_SKIRT = re.compile(r"\s+(?:with|over)\s+(?:(?!\b(?:with|over)\b)[^,])*\bskirts?\b(?!\s+suits?)[^,]*$", re.I)


def upper_body(outfit):
    """The parts of an outfit visible in a head-and-shoulders portrait.

    Outfits list footwear so full-body sprites are not barefoot, but naming shoes in a portrait prompt makes the
    model draw them at the bottom of the frame.
    """
    trimmed = _WITH_FOOTWEAR.sub("", outfit)          # "a dark suit with polished leather shoes" -> "a dark suit"
    parts = re.split(r",\s*|\s+and\s+", trimmed)
    shortened = [_WITH_SKIRT.sub("", p) for p in parts]
    kept = [p for p in shortened if p.strip() and not _LOWER_BODY.search(p)]
    if kept == parts:                                 # nothing to change: keep the words as written ("a white and gold robe")
        return trimmed
    return ", ".join(kept) or outfit


# What a portrait shows. "bust" is what the prompts were tuned on; "half" cuts at the waist and "full" shows the whole body.
FRAMINGS = {"bust": "Head and shoulders portrait of", "half": "Waist-up portrait of", "full": "Full-length portrait of"}

# Where the frame ends, said once more after the look and the pose: "Head and shoulders portrait of" at the start is not enough by itself. Measured 2026-10-13 (six characters, four picture engines, body points
# found with SDPose) this sentence took more of the arms and hips out of the picture on top of what the wider outfit filter did (elbows showing: Mage-Flow 3 in 6 -> 2, Qwen-Image 2512 1 -> 0,
# Qwen Image 2.1 5 -> 3 with its wrists 3 -> 0 and hips 2 -> 1; Z-Image no change), with both shoulders still in and the face at 24-30% of the height, which the head step's centred 61% crop needs.
# It says what is in the picture and never that the picture "cuts" the body (a wording that did made Qwen draw a seam across the chest).
FRAMING_ENDS = {"bust": "Framed from the top of the head to just below the collarbones; nothing below the chest is in the picture."}

# The clauses of a description that tell the picture how the character stands or how much of them shows. A description made
# for a full-body sprite ("... standing in a relaxed A-pose with the arms held slightly away from the body, both hands empty
# and open") pulled every portrait made from it to full length (a game's bulk run, 2026-10-11: 164 of 164), because the
# portrait prompt used a free description as it stood. Hands "stained with ink" or a coat "standing open" stay: only a clause
# that begins with a stance, or names a pose or a whole body, goes.
_POSE_CLAUSE = re.compile(
    r"^\s*(?:and\s+)?(?:standing|stands|stood|posed|posing)\b"
    r"|\b[at][- ]pose\b"
    r"|\b(?:full|whole)[- ](?:body|length|figure)\b|\bhead[- ]to[- ](?:toe|foot)\b|\bfrom head to (?:toe|foot)\b"
    r"|\b(?:arms?|hands?|palms?|legs?|feet)\b[^,;.]*\b(?:away from the (?:body|torso)|out to the sides?|at (?:his|her|their|the) sides?"
    r"|akimbo|empty and open|open and empty|held (?:out|wide|apart)|spread (?:apart|wide)|crossed|folded|clasped"
    r"|behind (?:his|her|their) back|on (?:his|her|their) hips)\b"
    r"|\b(?:both )?hands (?:are )?(?:empty|open)\b", re.I)


def clean_framing(value):
    """A known portrait framing, else ValueError (a misspelt one used to be quietly ignored)."""
    if value in (None, ""):
        return "bust"
    if not isinstance(value, str) or value not in FRAMINGS:
        raise ValueError(f"Unknown portrait framing {value!r}: portrait_framing takes one of {', '.join(FRAMINGS)}")
    return value


def _without_lower_body(clause):
    """The clause without the garments and footwear below the waist ("a red jacket and black trousers" -> "a red jacket"),
    '' when nothing is left of it ("dark trousers tucked into black boots"). Clauses that name none come back unchanged."""
    if not _LOWER_BODY.search(clause):
        return clause
    trimmed = _WITH_FOOTWEAR.sub("", clause)
    parts = [_WITH_SKIRT.sub("", p) for p in re.split(r",\s*|\s+and\s+", trimmed)]
    return ", ".join(p for p in parts if p.strip() and not _LOWER_BODY.search(p))


def portrait_text(text, framing="bust"):
    """A free description as a portrait can use it: without its stance and pose clauses and, unless the portrait shows the
    whole body, without what is worn below the waist (a portrait that names boots draws them at the bottom of the frame, which
    upper_body() prevents for an outfit). Text with nothing to drop comes back exactly as written."""
    if framing == "full":
        return text
    sentences, changed = [], False
    for sentence in re.split(r"(?<=[.!?])\s+", text.strip()):
        end = sentence[-1] if sentence[-1:] in (".", "!", "?") else ""
        kept, edited = [], False
        for clause in (c.strip() for c in re.split(r"\s*[,;]\s*", sentence.rstrip(".!?"))):
            if not clause:
                continue
            lean = "" if _POSE_CLAUSE.search(clause) else _without_lower_body(clause)
            edited = edited or lean != clause
            if lean:
                kept.append(lean)
        if edited:
            changed = True
            sentence = (", ".join(kept) + end) if kept else ""
        if sentence:
            sentences.append(sentence)
    return " ".join(sentences) if changed else text


def _who(npc):
    """How the edit prompts refer to the character: 'woman', 'teenage boy'... or just 'character' for freeform ones."""
    return "character" if npc.get("freeform") else person_word(npc["profile"])


def keep_phrase(freeform=False):
    """What an edit must preserve. Freeform characters may not be human at all (a robot, a creature), so the wording for
    them must not talk about 'hair' and 'skin tone': that steers the model toward a human body."""
    if freeform:
        return ("Keep everything about the character exactly the same as the reference: the head, face, colors, materials, "
                "clothing, accessories and every distinctive feature")
    return "Keep the face, hair, skin tone and clothing exactly the same as the reference"


# What a free description already says about the sex of the character. The description is the owner's own words and wins: the Who card's gender
# leads it only when none of these is in it. (A described character used to be drawn from its words alone, so a silent description drew whichever
# sex the picture model fancied: Kessa Lark, 2026-10-15, a woman on the card and a man in the picture.) A word here that is not about the
# character ("mother-of-pearl") only keeps the description exactly as written, which is what it did before.
_FEMALE_WORDS = (
    r"wom[ae]n|girls?|girlish|lad(?:y|ies)|gals?|females?|femme|feminine|she|her|hers|herself|maidens?|madam|ma'am|miss|mrs|ms|widow"
    r"|mothers?|mum|mom|mama|grandmother|grandma|aunt|aunts|sisters?|daughters?|nieces?|wife|wives|brides?"
    r"|queens?|princess(?:es)?|duchess(?:es)?|countess(?:es)?|baroness(?:es)?|empress(?:es)?|goddess(?:es)?"
    r"|priestess|sorceress|enchantress|huntress|seamstress|waitress|actress|hostess|stewardess|governess|mistress|heroine|temptress"
    r"|pregnant|lipstick|breasts?|bosom|cleavage")
_MALE_WORDS = (
    r"m[ae]n|boys?|boyish|guys?|dudes?|lads?|gentlem[ae]n|males?|masculine|he|him|his|himself|widower"
    r"|fathers?|dad|papa|grandfather|grandpa|uncles?|brothers?|sons?|nephews?|husbands?|grooms?"
    r"|kings?|princes?|dukes?|earls?|barons?|sir|lord|mr"
    r"|beard(?:ed|s)?|moustaches?|mustaches?|goatee|stubble|sideburns")
_NONBINARY_WORDS = r"androgyn\w*|non-?binary|genderless|agender"
_SEX_WORDS = re.compile(rf"\b(?:{_FEMALE_WORDS}|{_MALE_WORDS}|{_NONBINARY_WORDS})\b", re.I)
_WHICH_SEX = re.compile(rf"(?P<female>\b(?:{_FEMALE_WORDS})\b)|(?P<male>\b(?:{_MALE_WORDS})\b)|(?P<nonbinary>\b(?:{_NONBINARY_WORDS})\b)", re.I)
# A description of something that is not a person (a robot, a creature) is left alone: "female" in front of it steers the picture toward a human body.
_NOT_A_PERSON = re.compile(
    r"\b(?:robots?|droids?|golems?|mechs?|machines?|automatons?|constructs?|creatures?|monsters?|beasts?|animals?|dragons?|drakes?|wyrms?"
    r"|slimes?|oozes?|blobs?|elementals?|ghosts?|spirits?|wraiths?|skeletons?|statues?|plants?|trees?|mushrooms?"
    r"|wol(?:f|ves)|dogs?|cats?|fox(?:es)?|horses?|birds?|owls?|bears?|rabbits?|rats?|mice|mouse|lizards?|snakes?|frogs?|toads?|spiders?|insects?)\b", re.I)
_SUBJECT_END = re.compile(r"[,;:.()—]|\s-\s|\b(?:with|who|which|that|in|on|at|wearing|holding|carrying|riding|having|has|and|made|of|from|for)\b", re.I)


def _names_a_thing(text):
    """Whether what a description is about (its first two clauses, each up to its first 'with', 'in', 'wearing'...) is not a person:
    'A rusty robot with glowing eyes', 'Mira, a clockwork fox'. 'A ranger with a pet fox' is about the ranger."""
    clauses = [c for c in re.split(r"\s*[,;]\s*", text.strip()) if c][:2]
    return any(_NOT_A_PERSON.search(_SUBJECT_END.split(c, 1)[0]) for c in clauses)


def described_gender(text):
    """The sex a free description gives its character ('female', 'male' or 'nonbinary', the data sheet's genders), or None when
    its words name none. The first such word wins: 'a woman and her son' is about the woman. A word inside a hyphenated
    compound does not count ('mother-of-pearl', 'man-made', 'a she-wolf')."""
    text = str(text or "")
    for m in _WHICH_SEX.finditer(text):
        before = text[m.start() - 1] if m.start() else " "
        after = text[m.end()] if m.end() < len(text) else " "
        if "-" not in (before, after):
            return m.lastgroup
    return None


def adult_lead(profile, text):
    """A free description led by 'a grown adult woman', 'a grown adult man' or 'a grown adult', for a character made with the
    roll's `adult` option: a few words in a cartoon style could otherwise come out looking like a child (a companion app's age check
    read one as about 11, 2026-11-10). The sex is the description's own when its words give one, else the Who card's. A
    description of a thing (a robot, a creature) is left alone, and one that already starts with 'a grown adult' is not
    led twice."""
    if _names_a_thing(text) or re.match(r"\s*(?:an?\s+)?grown[- ]adult\b", text, re.I):
        return text
    noun = {"female": "woman", "male": "man"}.get(described_gender(text) or (profile or {}).get("gender"))
    lead = f"a grown adult {noun}" if noun else "a grown adult"
    if text == "a character":
        return lead
    return f"{lead}, " + re.sub(r"^(A|An|The|She|He|They)(?=\s)", lambda m: m.group(1).lower(), text)


def card_gender_lead(profile, text):
    """A free description led by the Who card's gender ('a female character, a wiry caravan runner...') when the description does not
    say one itself and is about a person; otherwise exactly as written. Only 'female' and 'male' lead: the card's age is not used (it is
    rolled for the data sheet and may not suit the words: a kid, an old sage), and 'female' fits a girl as it fits a woman."""
    word = {"female": "female", "male": "male"}.get((profile or {}).get("gender"))
    if not word or _SEX_WORDS.search(text) or _names_a_thing(text):
        return text
    if text == "a character":
        return f"a {word} character"
    return f"a {word} character, " + re.sub(r"^(A|An|The)(?=\s)", lambda m: m.group(1).lower(), text)


def describe(npc, style, setting=None, portrait=False, framing="bust"):
    p, a = npc["profile"], npc["appearance"]
    if npc.get("freeform"):                      # the user's own words are the whole description
        text = (a.get("extra") or "").strip() or "a character"
        if portrait:
            text = portrait_text(text, framing) or "a character"
        # ...led by the Who card's gender where the words are silent about it, or as a grown adult when that was asked for
        text = adult_lead(p, text) if npc.get("adult") else card_gender_lead(p, text)
        context = (setting or {}).get("context")
        return f"{context}: {text}" if context else text
    build = (a.get("build") or "").strip()
    looks = BUILD_LOOKS.get(build.lower())
    who = f'{"an" if looks and looks[0] in "aeiou" else "a"} {looks + " " if looks else ""}{p["age"]}-year-old '
    race = (p.get("race") or "").strip()
    if race and race.lower() != "human":
        who += race.lower() + " "
    who += person_word(p)
    heritage = (p.get("heritage") or "").strip()
    if heritage and style.get("use_heritage"):
        who += " of mixed heritage" if heritage.lower().startswith("mixed") else f" of {heritage} descent"
    parts = [who]
    occ = (p.get("occupation") or "").strip()
    if occ and occ.lower() not in SILENT_OCCUPATIONS:
        parts.append(occ)
    if build and not looks:
        parts.append(f"{build} build")
    if a.get("skin_tone"):
        parts.append(f'{a["skin_tone"]} skin')
    if a.get("hair"):
        parts.append(a["hair"])
    if a.get("eyes"):
        parts.append(f'{a["eyes"]} eyes')
    for k in ("facial_hair", "glasses", "distinguishing"):
        if (a.get(k) or "").strip():
            parts.append(a[k].strip())
    aging = _aging(p["age"])
    if aging:
        parts.append(aging)
    outfit = upper_body(a["outfit"]) if portrait and framing != "full" and a.get("outfit") else a.get("outfit")
    wearing = f"wearing {outfit}" if outfit else ""
    if (a.get("accessory") or "").strip():
        wearing += f', with {a["accessory"].strip()}'
    if wearing:
        parts.append(wearing)
    if (a.get("extra") or "").strip():
        extra = a["extra"].strip()
        if portrait:
            extra = portrait_text(extra, framing)
        if extra:
            parts.append(extra)
    text = ", ".join(parts)
    context = (setting or {}).get("context")
    return f"{context}: {text}" if context else text


def _expression(npc):
    return (npc["appearance"].get("expression") or "a calm, pleasant, neutral expression").strip()


def _edit_negative(style, setting=None):
    """setting is optional and unused by sprite_edit/expression_edit (they only ever touch an already-styled
    portrait), but portrait_from_photo passes it through: it's the one edit prompt establishing a setting's look
    for the first time, so it needs that setting's own negative terms (e.g. superhero.json's trademark guard) too."""
    extra = (setting or {}).get("negative", "")
    return ", ".join(p for p in (style["negative"], extra, "signature, watermark, text, floor shadow, extra people") if p)


def _negative(style, setting=None, extra=""):
    """The style's negative prompt, then the setting's own (`negative` in its file), then anything the asset adds."""
    return ", ".join(p for p in (style["negative"], (setting or {}).get("negative", ""), extra) if p)


def portrait(npc, style, setting=None, key=None, framing="bust"):
    """Text-to-image prompt for the portrait: head and shoulders unless `framing` (FRAMINGS) says more of the character shows.
    key=None uses the style's backdrop."""
    framing = clean_framing(framing)
    bg = KEY_PHRASES[key] + ", no shadow" if key else style["backdrop"]
    noun = "character" if npc.get("freeform") else "person"
    look = ((setting or {}).get("style_text") or {}).get(npc.get("style")) or style["style"]      # a setting may word a style its own way
    ends = FRAMING_ENDS.get(framing)
    pos = (f'{look}. {FRAMINGS[framing]} {describe(npc, style, setting, portrait=True, framing=framing)}. '
           f'The {noun} has {_expression(npc)}, looking at the camera. {ends + " " if ends else ""}{bg}.')
    return {"positive": pos, "negative": _negative(style, setting)}


def _taur_sprite(npc, style, setting=None, key="magenta", view="front"):
    """Independent prompt for a taur: define the human torso and four-legged lower body separately and explicitly."""
    profile, appearance = npc["profile"], npc["appearance"]
    race = str(profile.get("race") or "").casefold()
    extra = str(appearance.get("extra") or "")
    cues = f"{race} {extra}".casefold()
    if "dragon" in cues:
        animal, coat, feet = "dragon", "covered in scales", "clawed feet"
    elif "horse" in cues or "centaur" in cues:
        animal, coat, feet = "horse", "with a short coat", "hooves"
    else:
        animal, coat, feet = "wolf", "covered in fur", "paws"

    details = [f'{profile.get("age", "adult")}-year-old {person_word(profile)}']
    for key_name, suffix in (("skin_tone", "skin"), ("hair", ""), ("eyes", "eyes"),
                             ("glasses", ""), ("facial_hair", "")):
        value = str(appearance.get(key_name) or "").strip()
        if value:
            details.append(f"{value} {suffix}".strip())
    race_features = next((str(race_data.get("features") or "") for race_data in (setting or {}).get("races", {}).values()
                          if str(race_data.get("label") or "").casefold() == race), "")
    distinguishing = str(appearance.get("distinguishing") or "").strip()
    if race_features:
        distinguishing = distinguishing.replace(race_features, "").strip(" ,")
    if distinguishing:
        details.append(distinguishing)
    head_detail = "The human head is the creature's only head."
    if "horn" in race_features.casefold():
        head_detail = "The human head is the creature's only head, with two small horns growing from it."
    outfit = upper_body(str(appearance.get("outfit") or "").strip())
    if outfit:
        details.append(f"wearing {outfit}")
    accessory = str(appearance.get("accessory") or "").strip()
    if accessory:
        details.append(f"with {accessory}")
    if npc.get("freeform") and extra:
        details.append(extra)

    noun = "character" if npc.get("freeform") else f'{animal}-taur'
    positive = (
        f'{style["style"]}. Full-body {VIEWS[view]}, centered, entire figure visible, '
        f'{KEY_PHRASES[key]}, no floor, no shadow. A {noun}, a centaur-like taur with one coherent body: '
        f'a human {person_word(profile)} torso, two human arms and one human head seamlessly attached at the waist '
        f'to one large horizontal {animal} torso. The human body ends at the waist; it has no human legs. '
        f'The {animal} body stands on all fours and has exactly four {animal} legs total: two front legs beneath its '
        f'shoulders and two hind legs beneath its hips. All four {animal} {feet} are clearly visible touching the ground. '
        f'The quadruped lower body is {coat}; it has no separate head or neck. {head_detail} '
        f'It is not bipedal and is not a rider on a separate animal. '
        f'The human upper body has {", ".join(details)}.'
    )
    # "extra limbs" in generic human negatives conflicts with the six intentional limbs of a taur.
    negative = _negative(style, setting, "bipedal animal, human legs, extra human lower body, upright animal, separate rider, separate mount, missing legs, second head, animal head, separate lower-body head")
    negative = re.sub(r"\bextra limbs\b,?\s*", "", negative, flags=re.I)
    negative = re.sub(r",\s*,+", ", ", negative).strip(" ,")
    return {"positive": positive, "negative": negative}


def sprite(npc, style, setting=None, key="magenta", view="front"):
    """Independent text-to-image prompt for the full-body sprite."""
    if morphology_mod.clean(npc.get("morphology")) == "taur":
        return _taur_sprite(npc, style, setting, key, view)
    noun = "character" if npc.get("freeform") else "person"
    isolation = (f'A single isolated character cutout against only a seamless, perfectly uniform flat {key} backdrop; '
                 'no environment, objects, street or city visible. ') if style.get("sprite_isolated") else ""
    flat_end = f' The background is empty solid {key} across the entire canvas.' if style.get("sprite_flat_background") else ""
    pos = (f'{isolation}{style["style"]}. Full-body standing character sprite, {VIEWS[view]}, entire figure visible from head to toe '
           f'including shoes, centered, of {describe(npc, style, setting)}. The {noun} has {_expression(npc)}. '
           f'{KEY_PHRASES[key]}, no floor, no shadow.{flat_end}')
    return {"positive": pos, "negative": _negative(style, setting,
            ", ".join(p for p in ("cut-off feet, ground shadow", style.get("sprite_negative")) if p))}


def complete_outfit(npc):
    """The whole outfit and what the character carries, in the character's own words ('' for freeform characters)."""
    a = npc["appearance"]
    outfit = (a.get("outfit") or "").strip()
    if npc.get("freeform") or not outfit:
        return ""
    carried = (a.get("accessory") or "").strip()
    return f"{outfit}, with {carried}" if carried else outfit


def sprite_edit(npc, style, key="magenta", view="front"):
    """Edit prompt that turns the portrait into a matching full-body sprite.

    A head-and-shoulders portrait shows nothing below the chest, so the outfit is stated again: without it the model
    invents the skirt length and the shoes (a shopkeeper whose outfit said brown boots came out in sandals).
    """
    outfit = complete_outfit(npc)
    below = f" The complete outfit, everything below the shoulders included, is: {outfit}." if outfit else ""
    pos = (f'Show the same {_who(npc)} in a full-body standing view, head to toe including shoes, {VIEWS[view]}, '
           f'on {KEY_PHRASES[key]} with no floor and no shadow. {keep_phrase(npc.get("freeform"))}, '
           f'and keep the exact same {style["edit_name"]} art style, line style and color palette.{below}')
    return {"positive": pos, "negative": _edit_negative(style)}


def expression_edit(phrase, style):
    """Edit prompt that changes only the facial expression of the portrait."""
    pos = (f"Keep the person's face shape, identity, hair, clothing, pose, framing, background and {style['edit_name']} art style "
           f"exactly unchanged. Change only the facial expression to {phrase}.")
    return {"positive": pos, "negative": _edit_negative(style)}


def portrait_from_photo(npc, style, setting=None, key=None):
    """Edit prompt that restyles an uploaded photo into the chosen setting/style while keeping the person's real
    likeness -- the opposite emphasis from sprite_edit/expression_edit, which keep the art style and change a pose
    or expression. Here identity is what's kept; the art style, outfit and scene are what change. Unlike those two,
    this also weaves in the setting's context: they only ever touch an *already* correctly-styled portrait, but a
    photo has no setting context on it yet, so this is the one edit prompt that has to establish it from scratch.
    """
    bg = KEY_PHRASES[key] + ", no shadow" if key else style["backdrop"]
    context = (setting or {}).get("context")
    scene = f" Reimagine the scene as a {context}." if context else ""
    outfit = complete_outfit(npc)
    dressed = f" Dress {_who(npc)} in {outfit}." if outfit else ""
    distinguishing = (npc["appearance"].get("distinguishing") or "").strip()
    marked = f" This {_who(npc)} has {distinguishing}." if distinguishing else ""
    pos = (f"Keep this exact {_who(npc)}'s face, facial features, identity, expression and skin tone exactly as in "
           f"the reference photo. Change everything else: redraw the whole image in {style['edit_name']} art style, "
           f"as a head and shoulders portrait, looking at the camera.{scene}{dressed}{marked} "
           f"Replace the background entirely with {bg}.")
    return {"positive": pos, "negative": _edit_negative(style, setting)}


def preview(npc, style, setting, expressions, options):
    """Every prompt the generator would send for this NPC (shown in the UI so nothing is hidden)."""
    key = pick_key(npc, style)
    view = options.get("sprite_view", "front")
    bg_key = key if options.get("transparent_portrait") else None
    photo = (npc.get("assets") or {}).get("source_photo")
    out = {
        "portrait": portrait_from_photo(npc, style, setting, bg_key) if photo else portrait(npc, style, setting, bg_key),
        "sprite_match": sprite_edit(npc, style, key, view),
        "sprite_independent": sprite(npc, style, setting, key, view),
    }
    sample = next((e for e in expressions if e["id"] == "happy"), expressions[0])
    out["expression_example"] = {"label": sample["label"], **expression_edit(sample["phrase"], style)}
    out["key_colour"] = key
    return out
