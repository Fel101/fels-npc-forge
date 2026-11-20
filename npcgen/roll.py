"""Roll a random NPC (profile + appearance) from the JSON tables in data/."""
import json
import pathlib
import random
import re

from . import morphology as morphology_mod
from . import prompts

DATA_DIR = pathlib.Path(__file__).resolve().parent.parent / "data"
ADULT_AGE = 18
# How a described (freeform) character's age came about, kept as profile.age_source: its words do not give one, so the data
# sheet must not pass a random number off as part of the description. "locked": the caller gave it; "chosen": the caller
# picked the age group; "rolled": an adult age at random (NPC Forge's page drops it when the age is edited by hand).
AGE_SOURCES = ("locked", "chosen", "rolled")
# shared tables in common.json that a setting may replace with its own (a period setting has no pastel-dyed hair or undercuts)
TABLE_OVERRIDES = ("hair_styles", "dyed_hair_colors", "facial_hair", "glasses", "distinguishing", "builds", "age_groups", "traits",
                   "heritages", "default_expression", "speech_styles", "demeanors", "bio_templates")
# Optional rules a setting file may state (used by roll_npc and normalize, and shown by the UI):
#   "genders": ["female"]   only these genders are rolled or accepted
#   "min_age": 21           nobody younger is rolled or accepted
#   "negative": "..."       added to the negative prompt of every text-to-image prompt
#   "default_style": "photoreal"   the art style picked when the user switches to this setting
#   "heritages": {...}      its own heritage list (same format as common.json), e.g. Japanese and Korean instead of East Asian
#   "hair_color_choices": ["blonde", ...]   the hair colours the Who panel offers (default: every colour in the heritage tables)
#   "style_text": {"photoreal": "..."}      words the portrait prompt uses for that art style instead of the style's own sentence
#   "speech_styles": [{"t": "...", "for": [traits], "not": [traits]}]   how the character talks (see roll_manner); "demeanors" is
#                                           the same for how they come across. Both fall back to the shared tables in common.json.
#   "dye_chance": 0.4       how likely a character under 40 has dyed hair, drawn from "dyed_hair_colors" (default 0.06)
#   "bio_templates": [...]  its own bio sentence templates (same format as common.json, see make_bio) instead of the shared ones,
#                                           e.g. the zombie setting's are past tense ("{first} was {occ_a}...", not "is")
# A race (a setting with "races") may state "hair_colors": {"silver": 3, "white": 1} to roll its hair colour from there instead of
# from the heritage tables, like "skin" does for skin, and "eyes": ["three golden", ...] for its eyes. A race that is not human
# at all may also state fixed text for "hair" ("a smooth hairless head", "a crown of living snakes in place of hair": used unless
# the user picks a hair colour or style) and "facial_hair" ("" = never a beard).

# More settings, read from other folders. A local extension registers one with add_settings_dir; `visible` is called on
# every load so that a folder can be switched off again without restarting.
EXTRA_SETTINGS = []


def add_settings_dir(directory, visible=None):
    """Read settings (same format as data/settings/*.json) from `directory` too, while visible() is true."""
    EXTRA_SETTINGS.append((pathlib.Path(directory), visible))


def _is_visible(visible):
    try:
        return True if visible is None else bool(visible())
    except Exception:                                    # a broken switch must never expose what it guards
        return False


def _read_json(path):
    return json.loads(path.read_text("utf-8"))


def hidden_setting_ids():
    """Ids of the settings that exist in a registered folder that is switched off right now."""
    hidden = set()
    for directory, visible in EXTRA_SETTINGS:
        if not _is_visible(visible) and directory.is_dir():
            hidden |= {p.stem for p in directory.glob("*.json")}
    return hidden


def load_data():
    """Read every table fresh, so edits to data/*.json apply without restarting the server."""
    read = _read_json
    settings = {p.stem: read(p) for p in (DATA_DIR / "settings").glob("*.json")}
    built_in = set(settings)
    for directory, visible in EXTRA_SETTINGS:
        if directory.is_dir() and _is_visible(visible):
            for p in directory.glob("*.json"):
                settings.setdefault(p.stem, read(p))         # a built-in setting is never replaced
    styles = {k: v for k, v in read(DATA_DIR / "styles.json").items() if not k.startswith("_")}
    return {
        "common": read(DATA_DIR / "common.json"),
        # the built-in settings first, an extension's after them, each group by its own "order"
        "settings": dict(sorted(settings.items(), key=lambda kv: (kv[0] not in built_in, kv[1].get("order", 99)))),
        "styles": dict(sorted(styles.items(), key=lambda kv: kv[1].get("order", 99))),
        "expressions": read(DATA_DIR / "expressions.json")["expressions"],
        "animations": read(DATA_DIR / "animations.json"),
        "sheet": read(DATA_DIR / "sheet.json"),
        "head3d": read(DATA_DIR / "head3d.json"),
    }


# ---- small helpers -------------------------------------------------------------------------------------------
def wchoice(rng, weights):
    """weights: {item: weight} -> item."""
    items = [(k, w) for k, w in weights.items() if w > 0]
    total = sum(w for _, w in items)
    r = rng.random() * total
    for k, w in items:
        r -= w
        if r <= 0:
            return k
    return items[-1][0]


def wlist(rng, options, gender=None, key="w"):
    """Pick from [{label, w, g?}] honouring the optional gender restriction."""
    pool = {o["label"]: o[key] for o in options if not o.get("g") or gender in o["g"]}
    return wchoice(rng, pool)


def as_list(value):
    if value is None:
        return []
    if isinstance(value, str):
        return [s.strip() for s in value.split(",") if s.strip()]
    return [str(s).strip() for s in value if str(s).strip()]


def article(noun):
    n = noun.strip().lower()
    if n.startswith(("uni", "use", "eu", "one")):
        return "a"
    return "an" if n[:1] in "aeiou" or n.startswith(("hour", "honest")) else "a"


def age_group_for(common, age):
    groups = common["age_groups"]
    for gid, g in groups.items():
        if g["min"] <= age <= g["max"]:
            return gid
    return "senior" if age > 60 else "young_adult"


def _pooled(heritages, field):
    """Merge one weighted table across all non-mixed heritages (used for 'mixed heritage')."""
    out = {}
    for h in heritages.values():
        for k, w in (h.get(field) or {}).items():
            out[k] = out.get(k, 0) + w
    return out


def heritage_table(common, hid, field):
    h = common["heritages"][hid]
    if h.get("mixed"):
        return _pooled(common["heritages"], field)
    return h[field]


def find_id(table, text, label_key="label"):
    """Match free text against ids or labels of a {id: {label}} table (case-insensitive)."""
    if not text:
        return None
    t = str(text).strip().lower()
    for k, v in table.items():
        if t == k.lower() or t == v.get(label_key, "").lower():
            return k
    return None


# ---- validation of client-edited NPCs ------------------------------------------------------------------------------
PROFILE_TEXT = ("first_name", "last_name", "heritage", "race", "occupation", "quirk", "goal", "peeve", "bio", "speech", "demeanor")
APPEARANCE_TEXT = ("build", "skin_tone", "hair", "eyes", "facial_hair", "glasses", "distinguishing", "outfit", "accessory",
                   "expression", "extra")


def _text(value, limit=800):
    return str(value or "").strip()[:limit]


def clean_custom(value):
    """User data carried through untouched from a cast file: JSON-serialisable and reasonably small."""
    if not isinstance(value, dict):
        return {}
    try:
        text = json.dumps(value)
    except (TypeError, ValueError):
        return {}
    return json.loads(text) if len(text) <= 20000 else {}


def setting_common(data, setting_id):
    """The shared tables with the replacements this setting states for itself."""
    setting = data["settings"][setting_id]
    return {**data["common"], **{k: setting[k] for k in TABLE_OVERRIDES if k in setting}}


def hair_color_choices(data, setting_id):
    """The hair colours the Who panel offers: the setting's own list, else every colour its heritage tables can roll."""
    setting = data["settings"][setting_id]
    listed = [str(c).strip() for c in setting.get("hair_color_choices") or [] if str(c).strip()]
    if listed:
        return listed
    totals = {}
    for h in setting_common(data, setting_id)["heritages"].values():
        for color, weight in (h.get("hair_colors") or {}).items():
            totals[color] = totals.get(color, 0) + weight
    return sorted(totals, key=lambda c: -totals[c])


# pronouns for showing a table entry without knowing the character yet: "{poss} own jokes" reads "their own jokes"
_THEY = {"_plural": True, "subj": "they", "Subj": "They", "obj": "them", "poss": "their"}


def _labels(options, skip=("none", "clean-shaven")):
    return [o["label"] for o in options if o["label"] not in skip]


def _ranked(weights):
    return sorted(weights, key=lambda k: -weights[k])


def field_choices(data, setting_id):
    """Everything the tables of this setting can roll, per character-sheet field, for the page's Advanced options popup.

    A value is exactly what a locked field (roll_npc's `locked`) takes. Entries written with pronoun placeholders ("{poss} own
    jokes") keep them in `value`, so the roll can still word them for the character's gender, and are shown with "their" in
    `label`. Lists that depend on another field are keyed by it: skin and eyes by origin id (plus "all"), outfits by occupation
    id (each {"t", "g"}: g is the one gender it is for, or null). `optional` lists the fields where "none" is a real answer."""
    setting = data["settings"][setting_id]
    common = setting_common(data, setting_id)
    heritages = {k: v for k, v in common["heritages"].items() if not v.get("mixed")}

    def shown(texts):
        return [{"value": t, "label": _fmt(t, _THEY)} for t in texts]

    def union(tables):
        totals = {}
        for table in tables:
            for k, w in table.items():
                totals[k] = totals.get(k, 0) + w
        return _ranked(totals)

    races = setting.get("races") or {}
    skin = {"all": union([h.get("skin") or {} for h in heritages.values()] + [{s: 1 for s in r.get("skin") or []} for r in races.values()])}
    eyes = {"all": union([h.get("eyes") or {} for h in heritages.values()] + [{e: 1 for e in r.get("eyes") or []} for r in races.values()])}
    for rid, race in races.items():
        if race.get("skin"):
            skin[rid] = list(race["skin"])
        if race.get("eyes"):
            eyes[rid] = list(race["eyes"])
    if not races:
        for hid, her in heritages.items():
            skin[hid], eyes[hid] = _ranked(her.get("skin") or {}), _ranked(her.get("eyes") or {})
    looks = list(dict.fromkeys(list(common.get("trait_looks", {}).values()) + [common["default_expression"]]))

    def outfits(raw):
        return [{"t": o, "g": None} if isinstance(o, str) else {"t": o["t"], "g": o.get("g")} for o in raw or []]

    return {
        "builds": [{"label": b["label"], "g": b.get("g")} for b in common["builds"]],
        "skin": skin, "eyes": eyes,
        "hair_colors": hair_color_choices(data, setting_id),
        "hair_styles": [s["label"] for s in common["hair_styles"]],
        "facial_hair": _labels(common["facial_hair"]),
        "glasses": _labels(common["glasses"]),
        "distinguishing": _labels(common["distinguishing"]),
        "accessories": list(setting.get("accessories") or []),
        "expressions": looks,
        "outfits": {o["id"]: outfits(o.get("outfits")) for o in setting["occupations"]},
        "default_outfits": outfits(setting.get("default_outfits")),
        "traits": list(common["traits"]),
        "trait_exclusive": [list(pair) for pair in common.get("trait_exclusive", [])],
        "hobbies": shown(setting.get("hobbies") or []),
        "quirks": shown(setting.get("quirks") or []),
        "goals": shown(setting.get("goals") or []),
        "peeves": shown(setting.get("peeves") or []),
        "speech": shown(dict.fromkeys(str(o.get("t", "")).strip() for o in common.get("speech_styles") or [] if o.get("t"))),
        "demeanor": shown(dict.fromkeys(str(o.get("t", "")).strip() for o in common.get("demeanors") or [] if o.get("t"))),
        "optional": ["appearance.facial_hair", "appearance.glasses", "appearance.distinguishing", "appearance.accessory"],
    }


def _join(words):
    words = list(words)
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + " and " + words[-1]


def _check_setting_limits(setting, gender, age):
    """The setting's own rules (`genders`, `min_age`). Raises ValueError with a message the user can act on."""
    allowed = setting.get("genders")
    if allowed and gender not in allowed:
        raise ValueError(f'{setting["label"]} only has {_join(allowed)} characters')
    if age is not None and age < int(setting.get("min_age") or 1):
        raise ValueError(f'Characters in {setting["label"]} must be {int(setting["min_age"])} or older')


def normalize(data, npc):
    """Validate and clean an NPC draft coming from the UI. Assets are never taken from the client."""
    if not isinstance(npc, dict):
        raise ValueError("NPC must be an object")
    if not isinstance(npc.get("setting"), str) or npc["setting"] not in data["settings"]:
        raise ValueError("Unknown setting")
    setting = data["settings"][npc["setting"]]
    common = setting_common(data, npc["setting"])
    if not isinstance(npc.get("style"), str) or npc["style"] not in data["styles"]:
        raise ValueError("Unknown art style")
    p_in, a_in = npc.get("profile") or {}, npc.get("appearance") or {}
    if not isinstance(p_in, dict):
        raise ValueError("Profile must be an object")
    if not isinstance(a_in, dict):
        raise ValueError("Appearance must be an object")
    profile = {k: _text(p_in.get(k)) for k in PROFILE_TEXT}
    if not profile["first_name"]:
        raise ValueError("The NPC needs a first name")
    if not isinstance(p_in.get("gender"), str) or p_in["gender"] not in common["genders"]:
        raise ValueError("Gender must be one of: " + ", ".join(common["genders"]))
    profile["gender"] = p_in["gender"]
    try:
        profile["age"] = max(1, min(120, int(p_in.get("age"))))
    except (TypeError, ValueError):
        raise ValueError("Age must be a number") from None
    _check_setting_limits(setting, profile["gender"], profile["age"])
    profile["age_group"] = age_group_for(common, profile["age"])
    if p_in.get("age_source") in AGE_SOURCES:                  # how a described character's age came about (roll_npc)
        profile["age_source"] = p_in["age_source"]
    grown = {"adult": True} if npc.get("adult") is True else {}
    if grown and profile["age"] < ADULT_AGE:
        raise ValueError(f"This character was made as a grown adult, so the age must be {ADULT_AGE} or more")
    for field in ("personality", "hobbies"):
        if not isinstance(p_in.get(field), (type(None), str, list, tuple)):
            raise ValueError(f"{field.capitalize()} must be a list or comma-separated text")
    profile["personality"] = [_text(x, 60) for x in as_list(p_in.get("personality"))][:8]
    profile["hobbies"] = [_text(x, 100) for x in as_list(p_in.get("hobbies"))][:6]
    try:
        seed = int(npc.get("seed"))
    except (TypeError, ValueError):
        seed = random.randrange(2 ** 32)
    raw_morphology = npc.get("morphology")
    if raw_morphology in (None, ""):
        body_plan = morphology_mod.infer(npc, data)
    else:
        body_plan = morphology_mod.clean(raw_morphology)
        if body_plan is None:
            raise ValueError("Body morphology must be one of: " + ", ".join(morphology_mod.MORPHOLOGIES))
    return {"setting": npc["setting"], "style": npc["style"], "seed": seed % (2 ** 32),
            "freeform": bool(npc.get("freeform")), "custom": clean_custom(npc.get("custom")), "morphology": body_plan,
            "profile": profile, "appearance": {k: _text(a_in.get(k), 1500 if k == "extra" else 800) for k in APPEARANCE_TEXT},
            **grown}


# ---- manner: how a character talks and comes across -------------------------------------------------------------------
def _pick_manner(rng, options, traits):
    """One entry of a manner table ([{"t": text, "for": [traits it suits], "not": [traits it clashes with]}]). An entry that
    clashes with any of the traits is never picked. One that suits a trait weighs 4 (7 for two, 10 for three), one that suits
    none weighs 0.1, so the same three traits usually give a consistent person, but not always the same one."""
    have = {str(t).strip().lower() for t in traits or []}
    weights = {}
    for option in options or []:
        text = str(option.get("t") or "").strip()
        if not text or have & {str(t).lower() for t in option.get("not") or []}:
            continue
        hits = len(have & {str(t).lower() for t in option.get("for") or []})
        weights[text] = weights.get(text, 0) + float(option.get("w", 1)) * (0.1 if not hits else 4 + 3 * (hits - 1))
    return wchoice(rng, weights) if weights else ""


def roll_manner(data, setting_id, traits, rng=None):
    """{'speech': how they talk, 'demeanor': how they come across}, suited to these traits. Rolled from the setting's own tables
    when it has them, else the shared ones (an empty string where a table is missing)."""
    rng = rng or random.Random()
    common = setting_common(data, setting_id)
    return {"speech": _pick_manner(rng, common.get("speech_styles"), traits),
            "demeanor": _pick_manner(rng, common.get("demeanors"), traits)}


# ---- bio -------------------------------------------------------------------------------------------------------
def _fmt(template, ctx):
    plural = ctx["_plural"]

    def verb(m):
        base = m.group(1)
        if base == "have":
            return "have" if plural else "has"
        if base == "do":
            return "do" if plural else "does"
        return base if plural else (base + ("es" if base.endswith(("s", "sh", "ch")) else "s"))

    out = re.sub(r"\{v:(\w+)\}", verb, template)
    out = out.replace("{be}", "are" if plural else "is")
    out = re.sub(r"\{(\w+)\}", lambda m: str(ctx.get(m.group(1), m.group(0))), out)
    return out


def make_bio(rng, common, setting, profile, occ_label):
    pron = common["genders"][profile["gender"]]["pronouns"]
    ctx = {
        "_plural": profile["gender"] == "nonbinary",
        "first": profile["first_name"], "age": profile["age"], "occ": occ_label, "occ_a": f"{article(occ_label)} {occ_label}",
        "subj": pron[0], "Subj": pron[0].capitalize(), "obj": pron[1], "poss": pron[2],
        "place": setting.get("place", "town"),
    }
    traits = (profile["personality"] + ["quiet", "kind", "thoughtful"])[:3]
    ctx.update(t1=traits[0], t2=traits[1], t3=traits[2], a_t1=f"{article(traits[0])} {traits[0]}")
    hobbies = (profile["hobbies"] + ["reading", "walking"])[:2]
    ctx.update(h1=_fmt(hobbies[0], ctx), h2=_fmt(hobbies[1], ctx))
    ctx.update(goal=_fmt(profile["goal"], ctx), peeve=profile["peeve"], quirk=_fmt(profile["quirk"], ctx))
    text = _fmt(rng.choice(common["bio_templates"]), ctx)
    return text[:1].upper() + text[1:]


# ---- the roller --------------------------------------------------------------------------------------------------
def roll_npc(data, setting_id, constraints=None, locked=None, seed=None, freeform=None, adult=False):
    """Return {'setting', 'seed', 'freeform', 'profile', 'appearance'} (and 'adult': True when asked for).

    constraints: {gender, age_group, occupation (id), origin (heritage/race id), hair_color, hair_style} narrow the random
                 choices. hair_color must be one of hair_color_choices() and hair_style a hair_styles label; anything else is
                 ignored, like an unknown origin. Both replace the rolled hair after every draw, so the rest of the roll is the same.
    locked: {'profile.first_name': 'Maya', ...} values that win over both. A locked empty last name means no surname.
    freeform: a text description. The art is then driven by that text alone, so only name, age and gender are rolled
              (for the data sheet); job, looks and personality stay blank unless locked. The gender is the description's
              own when its words give one (and none is locked); the age, which the words do not give, is an adult one at
              random unless locked or picked, and profile.age_source says which (AGE_SOURCES).
    adult: the character is a grown adult: no child or teen age is rolled, picked or locked, and a described character's
           picture prompt starts "a grown adult woman" / "a grown adult man" / "a grown adult" (prompts.adult_lead).
    """
    rng = random.Random(seed)
    cons = {k: v for k, v in (constraints or {}).items() if v}
    lock = {k: v for k, v in (locked or {}).items() if v is not None}
    setting = data["settings"][setting_id]
    common = {**data["common"], **{k: setting[k] for k in TABLE_OVERRIDES if k in setting}}
    fantasy = "races" in setting
    requested_morphology = None
    if cons.get("morphology"):
        requested_morphology = morphology_mod.clean(str(cons["morphology"]))
        if requested_morphology is None:
            raise ValueError(f"Unknown body morphology: {cons['morphology']}")
        if not fantasy and requested_morphology != "bipedal" and freeform is None:
            label = morphology_mod.MORPHOLOGIES[requested_morphology]["label"].lower()
            raise ValueError(f'{setting["label"]} has no characters with a {label} body plan')
    locked_morphology = None
    if lock.get("morphology") not in (None, ""):
        locked_morphology = morphology_mod.clean(str(lock["morphology"]))
        if locked_morphology is None:
            raise ValueError(f"Unknown body morphology: {lock['morphology']}")

    def L(path):
        """Locked value, or None. An empty string counts as 'not locked' (names, occupation...)."""
        return lock.get(path) or None

    def optional(path, roll):
        """Fields where 'nothing' is a real answer (no glasses, no beard): a locked empty value sticks."""
        return lock[path] if path in lock else roll()

    # gender: a setting may allow only some ("genders"). A locked value it does not allow is an error, not a quiet swap.
    allowed = setting.get("genders")
    pool = {g: d["weight"] for g, d in common["genders"].items() if not allowed or g in allowed}
    # A described character's gender is the one its words give (the picture follows them), unless one is locked.
    gender = L("profile.gender")
    described = prompts.described_gender(freeform) if freeform is not None else None
    if gender in common["genders"]:
        _check_setting_limits(setting, gender, None)
    elif described in pool:
        gender = described
    else:
        gender = cons.get("gender") if cons.get("gender") in pool else wchoice(rng, pool)

    # age: a setting may state a minimum ("min_age"); groups that end below it are never rolled. A described character, or one
    # asked to be an adult, is never given a child's or a teen's age at random.
    min_age = int(setting.get("min_age") or 1)
    if L("profile.age") is not None:
        age = max(1, min(120, int(L("profile.age"))))
        _check_setting_limits(setting, gender, age)
        age_source = "locked"
    else:
        groups = {k: v for k, v in common["age_groups"].items() if v["max"] >= min_age}
        gid = cons.get("age_group") if cons.get("age_group") in groups else None
        age_source = "chosen" if gid else "rolled"
        if gid is None:
            adults = {k: v for k, v in groups.items() if v["min"] >= ADULT_AGE} if (adult or freeform is not None) else {}
            gid = wchoice(rng, {k: v["weight"] for k, v in (adults or groups).items()})
        age = rng.randint(max(min_age, groups[gid]["min"]), groups[gid]["max"])
    if adult and age < ADULT_AGE:
        raise ValueError(f"an adult was asked for, but the age is {age}")

    # origin: heritage (drives looks + names) and, in fantasy, race (drives features)
    hers = common["heritages"]
    her_id = None if fantasy else find_id(hers, L("profile.heritage"))
    if her_id is None and not fantasy and cons.get("origin") in hers:
        her_id = cons["origin"]
    if her_id is None:
        her_id = wchoice(rng, {k: v["weight"] for k, v in hers.items()})
    race, race_label, race_morphology = None, "", None
    if fantasy:
        races = setting["races"]
        race_id = find_id(races, L("profile.race"))
        if race_id is None and L("profile.race"):
            race_label = str(L("profile.race")).strip()      # a race that is not in the tables: keep the text as given
            race_morphology = requested_morphology or locked_morphology or "unspecified"
        else:
            selected_morphology = requested_morphology or locked_morphology
            candidates = {k: v for k, v in races.items()
                          if selected_morphology is None or morphology_mod.clean(v.get("morphology", "bipedal")) == selected_morphology}
            if race_id and selected_morphology and morphology_mod.clean(races[race_id].get("morphology", "bipedal")) != selected_morphology:
                label = morphology_mod.MORPHOLOGIES[selected_morphology]["label"].lower()
                raise ValueError(f"The locked race '{races[race_id]['label']}' does not have a {label} body plan")
            if cons.get("origin") in races and selected_morphology and cons["origin"] not in candidates:
                label = morphology_mod.MORPHOLOGIES[selected_morphology]["label"].lower()
                raise ValueError(f"The selected race '{races[cons['origin']]['label']}' does not have a {label} body plan")
            if not candidates:
                if selected_morphology:
                    label = morphology_mod.MORPHOLOGIES[selected_morphology]["label"].lower()
                    raise ValueError(f'No races in {setting["label"]} use the {label} body plan')
                raise ValueError(f'{setting["label"]} has no race entries to roll')
            race_id = race_id or (cons.get("origin") if cons.get("origin") in candidates else None) \
                or wchoice(rng, {k: v["weight"] for k, v in candidates.items()})
            race, race_label = races[race_id], races[race_id]["label"]
            race_morphology = morphology_mod.clean(race.get("morphology", "bipedal")) or "unspecified"

    # name
    names = setting.get("names")
    if names:
        first_pool = names.get(gender) or names["neutral"]
        surnames = names["surnames"]
    else:
        her = hers[her_id]
        pool_src = list(hers.values()) if her.get("mixed") else [her]
        female = [n for h in pool_src for n in h.get("names", {}).get("female", [])]
        male = [n for h in pool_src for n in h.get("names", {}).get("male", [])]
        surnames = [n for h in pool_src for n in h.get("surnames", [])]
        first_pool = female if gender == "female" else male if gender == "male" else \
            common["neutral_names"] + (rng.choice([female, male]) if rng.random() < 0.3 else [])
    first = L("profile.first_name") or rng.choice(first_pool)
    # a locked last name wins even when it is empty: "Wren Ashby" has no surname (a full name locked as the first name)
    last = str(lock["profile.last_name"]).strip() if "profile.last_name" in lock else rng.choice(surnames)
    grown = {"adult": True} if adult else {}

    if freeform is not None:
        profile = {
            "first_name": first, "last_name": last, "gender": gender, "age": age,
            "heritage": L("profile.heritage") or "", "race": L("profile.race") or "",
            "occupation": L("profile.occupation") or "", "personality": as_list(L("profile.personality")),
            "hobbies": as_list(L("profile.hobbies")), "quirk": L("profile.quirk") or "", "goal": L("profile.goal") or "",
            "peeve": L("profile.peeve") or "", "bio": L("profile.bio") or "", "age_group": age_group_for(common, age),
            "age_source": age_source, "speech": L("profile.speech") or "", "demeanor": L("profile.demeanor") or ""}
        ap = {k: str(lock.get(f"appearance.{k}") or "") for k in APPEARANCE_TEXT}
        ap["extra"] = str(freeform).strip()
        return {"setting": setting_id, "seed": rng.randrange(2 ** 32), "freeform": True,
                "morphology": locked_morphology or requested_morphology or "unspecified", "profile": profile, "appearance": ap, **grown}

    # occupation
    occs = setting["occupations"]
    occ = None
    if L("profile.occupation"):
        want = str(L("profile.occupation")).strip().lower()
        occ = next((o for o in occs if o["id"] == want or o["label"].lower() == want), None)
        occ_label = str(L("profile.occupation")).strip()
    else:
        by_id = {o["id"]: o for o in occs}
        if cons.get("occupation") in by_id:
            occ = by_id[cons["occupation"]]
        else:
            eligible = {o["id"]: o.get("weight", 1) for o in occs if o.get("min_age", 18) <= age <= o.get("max_age", 70)}
            occ = by_id[wchoice(rng, eligible or {occs[0]["id"]: 1})]
        occ_label = occ["label"]

    # personality
    traits = as_list(L("profile.personality"))
    banned = {t for pair in common["trait_exclusive"] for t in pair if any(x in traits for x in pair if x != t)}
    while len(traits) < 3:
        pick = rng.choice([t for t in common["traits"] if t not in traits and t not in banned])
        traits.append(pick)
        banned |= {b for pair in common["trait_exclusive"] if pick in pair for b in pair if b != pick}
    hobbies = as_list(L("profile.hobbies")) or rng.sample(setting.get("hobbies") or [], 2)
    quirk = L("profile.quirk") or rng.choice(setting["quirks"])
    goal = L("profile.goal") or rng.choice(setting["goals"])
    peeve = L("profile.peeve") or rng.choice(setting["peeves"])
    # The tables write "{poss} own jokes" so one entry suits everyone: the character's own words carry the real pronoun
    # (only the bio used to, so the Who panel showed "opening a place of {poss} own").
    pron = common["genders"][gender]["pronouns"]
    say = {"_plural": gender == "nonbinary", "subj": pron[0], "Subj": pron[0].capitalize(), "obj": pron[1], "poss": pron[2]}
    hobbies, quirk, goal, peeve = [_fmt(h, say) for h in hobbies], _fmt(quirk, say), _fmt(goal, say), _fmt(peeve, say)

    # appearance
    ap = {}
    ap["build"] = L("appearance.build") or _pick_build(rng, common, gender, race)
    ap["skin_tone"] = L("appearance.skin_tone") or (
        rng.choice(race["skin"]) if race and race.get("skin") else wchoice(rng, heritage_table(common, her_id, "skin")))
    hair_color = next((c for c in hair_color_choices(data, setting_id) if c.lower() == str(cons.get("hair_color", "")).strip().lower()), None)
    hair_style = next((s for s in common["hair_styles"] if s["label"].lower() == str(cons.get("hair_style", "")).strip().lower()), None)
    race = race or {}
    ap["hair"] = L("appearance.hair") or (race["hair"] if race.get("hair") and not (hair_color or hair_style) else
                                          _roll_hair(rng, common, her_id, gender, age, hair_color, race, float(setting.get("dye_chance", 0.06)),
                                                     hair_style))
    ap["eyes"] = L("appearance.eyes") or (rng.choice(race["eyes"]) if race.get("eyes") else wchoice(rng, heritage_table(common, her_id, "eyes")))
    ap["facial_hair"] = optional("appearance.facial_hair", lambda: (
        race["facial_hair"] if "facial_hair" in race else _roll_facial_hair(rng, common, gender, age)))
    ap["glasses"] = optional("appearance.glasses", lambda: _roll_glasses(rng, common, gender, age))
    ap["distinguishing"] = optional("appearance.distinguishing", lambda: _roll_distinguishing(rng, common, race))
    ap["outfit"] = L("appearance.outfit") or rng.choice(_outfits_for(occ, setting, gender))
    ap["accessory"] = optional("appearance.accessory", lambda: (
        rng.choice(setting["accessories"]) if age >= 13 and rng.random() < 0.4 else ""))
    looks = [common["trait_looks"][t] for t in traits if t in common["trait_looks"]]
    ap["expression"] = L("appearance.expression") or (looks[0] if looks else common["default_expression"])
    ap["extra"] = str(L("appearance.extra") or "")

    profile = {
        "first_name": first, "last_name": last, "gender": gender, "age": age,
        "heritage": "" if fantasy else (L("profile.heritage") or hers[her_id]["label"]),
        "race": race_label,
        "occupation": occ_label, "personality": traits, "hobbies": hobbies,
        "quirk": quirk, "goal": goal, "peeve": peeve,
    }
    profile["age_group"] = age_group_for(common, age)
    profile["bio"] = L("profile.bio") or make_bio(rng, common, setting, profile, occ_label)
    npc_seed = rng.randrange(2 ** 32)
    # The manner is rolled from the NPC's own seed, not from `rng`: nothing rolled before it changes, so a seed keeps giving the
    # same person it always did, now with a speech style and a demeanor on top.
    manner = roll_manner(data, setting_id, traits, random.Random(npc_seed))
    profile["speech"] = L("profile.speech") or manner["speech"]
    profile["demeanor"] = L("profile.demeanor") or manner["demeanor"]
    return {"setting": setting_id, "seed": npc_seed, "freeform": False,
            "morphology": locked_morphology or requested_morphology or race_morphology or "bipedal",
            "profile": profile, "appearance": ap, **grown}


def _pick_build(rng, common, gender, race):
    options = common["builds"]
    if race and race.get("builds"):
        options = [b for b in options if b["label"] in race["builds"]] or options
    return wlist(rng, options, gender)


def _outfits_for(occ, setting, gender):
    raw = (occ or {}).get("outfits") or setting["default_outfits"]
    ok = [o if isinstance(o, str) else o["t"] for o in raw
          if isinstance(o, str) or o.get("g") is None or o["g"] == gender or gender == "nonbinary"]
    return ok or [o if isinstance(o, str) else o["t"] for o in raw]


def _roll_hair(rng, common, her_id, gender, age, color_choice=None, race=None, dye_chance=0.06, style_choice=None):
    """color_choice and style_choice (the user's picks: a colour, a hair_styles entry) replace the rolled colour and style AFTER
    every random draw, so the rest of the roll is unchanged."""
    options = common["hair_styles"]
    texture = wchoice(rng, heritage_table(common, her_id, "hair_textures"))
    fits = [s for s in options if not s.get("only") or texture in s["only"]]
    weights = {}
    for s in fits:
        w = s["w"][["female", "male", "nonbinary"].index(gender)]
        if s.get("bald"):
            w *= 1 + max(0, age - 35) / 8 if gender == "male" else 1
            if age < 18:
                w = 0
        weights[s["label"]] = w
    label = wchoice(rng, weights)
    style = next(s for s in options if s["label"] == label)
    if style.get("bald"):
        if not style_choice or style_choice.get("bald"):
            return "bald"
        # the roll stops at 'bald' before the colour draws, but the chosen style needs a colour: draw it from a copy of the
        # generator, so every later draw (eyes, glasses...) is still exactly the one this seed always gave
        side = random.Random()
        side.setstate(rng.getstate())
        rng = side
    color = wchoice(rng, (race or {}).get("hair_colors") or heritage_table(common, her_id, "hair_colors"))
    if age < 40 and common["dyed_hair_colors"] and rng.random() < dye_chance:
        color = rng.choice(common["dyed_hair_colors"])
    elif age >= 40:
        p = min(0.95, (age - 38) / 45)
        if rng.random() < p:
            color = "salt-and-pepper" if age < 58 else ("white" if age >= 70 and rng.random() < 0.5 else "gray")
    if style_choice:
        if style_choice.get("bald"):
            return "bald"
        style = style_choice
        if style.get("only") and texture not in style["only"]:
            texture = style["only"][0]
    parts = [color_choice or color]
    if style.get("tex") and texture != "straight":
        parts.append(texture)
    parts.append(style["label"])
    text = " ".join(parts)
    return text if "hair" in text else text + " hairstyle"


def _roll_facial_hair(rng, common, gender, age):
    if gender == "female" or age < 16 or (gender == "nonbinary" and rng.random() < 0.75):
        return ""
    label = wlist(rng, common["facial_hair"])
    return "" if label == "clean-shaven" else label


def _roll_glasses(rng, common, gender, age):
    """Glasses get likelier with age; 'none' is stored as an empty string."""
    options = [{**g, "w": g["w"] * (1 if g["label"] == "none" else 1 + max(0, age - 40) / 25)} for g in common["glasses"]]
    label = wlist(rng, options, gender)
    return "" if label == "none" else label


def _roll_distinguishing(rng, common, race):
    parts = []
    if race and race.get("features"):
        parts.append(race["features"])
    label = wlist(rng, common["distinguishing"])
    if label != "none":
        parts.append(label)
    return ", ".join(parts)
