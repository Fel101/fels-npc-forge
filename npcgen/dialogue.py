"""Small, editable dialogue packs built locally from saved NPC profile data."""
import json
import re

from .library import now

CATEGORIES = ("greeting", "idle", "trade", "quest", "farewell")
CATEGORY_LABELS = {"greeting": "Greetings", "idle": "Idle", "trade": "Trade", "quest": "Quest", "farewell": "Farewells"}


def _gerund(phrase):
    """True for an "-ing" phrase ("opening a saloon"), which takes "My aim is ...", not "My aim is to ..."."""
    first = (phrase.split() or [""])[0].lower()
    return first.endswith("ing") and first not in ("bring", "sing", "string", "swing", "wring", "cling", "fling", "sling", "sting")


# The character's own pronouns, by gender, as the roll tables fill them in ("opening a place of {poss} own"). A line the
# character speaks turns them into "my", "me" and "I": "My aim is opening a place of her own" read as if she meant someone
# else. Other people's pronouns are left alone, as are those of a gender other than the character's.
OWN = {"female": ("she", "her", "her", "hers", "herself"), "male": ("he", "him", "his", "his", "himself"),
       "nonbinary": ("they", "them", "their", "theirs", "themselves")}
# Words that follow an object pronoun ("finds her before"), not the noun a possessive is followed by ("her name").
# "her back", "her home world" and "her first quest" are possessives, so back, home and first are not on the list.
_NOT_A_NOUN = {"a", "an", "the", "to", "for", "from", "with", "without", "by", "at", "in", "on", "of", "off", "out", "up",
               "down", "away", "over", "about", "and", "or", "but", "if", "when", "before", "after", "again", "too",
               "once", "as", "than", "that", "this", "into", "onto", "alone", "go", "free"}
_ADVERBS = {"always", "never", "still", "just", "finally", "really", "only", "ever", "often", "once", "even", "already"}
_TO_BE = {"is": "am", "are": "am", "was": "was", "were": "was", "has": "have", "does": "do", "isn't": "am not",
          "aren't": "am not", "wasn't": "wasn't", "weren't": "wasn't", "hasn't": "haven't", "doesn't": "don't"}
_CONTRACTED = {"'s": "'m", "'re": "'m", "'ve": "'ve", "'d": "'d", "'ll": "'ll"}


def _base_verb(word):
    """The verb after "I" for one written after "she" or "he": owns -> own, watches -> watch, tries -> try."""
    low = word.lower()
    if low in _TO_BE:
        return _TO_BE[low]
    if not low.endswith("s") or low.endswith(("ss", "us", "is")) or len(low) < 3:
        return word
    if low.endswith("ies") and len(low) > 4:
        return word[:-3] + "y"
    if low.endswith(("ches", "shes", "sses", "xes", "zes", "oes")):
        return word[:-2]
    return word[:-1]


def first_person(text, gender):
    """`text`, written about the character, as the character says it: "clearing her name" -> "clearing my name"."""
    own = OWN.get(gender)
    if not text or not own:
        return text
    subj, obj, poss, poss_alone, reflexive = own
    tokens = re.findall(r"[A-Za-z]+(?:['’][A-Za-z]+)?|[^A-Za-z]+", text)
    words = [i for i, t in enumerate(tokens) if t[:1].isalpha()]
    out = list(tokens)
    for n, i in enumerate(words):
        word = tokens[i]
        low = word.lower().replace("’", "'")
        after = tokens[words[n + 1]].lower() if n + 1 < len(words) and tokens[i + 1].strip() == "" else None
        new = None
        if low == reflexive or (gender == "nonbinary" and low == "themself"):
            new = "myself"
        elif low == subj or (low.startswith(subj + "'") and low[len(subj):] in _CONTRACTED):
            if low == subj:
                new = "I"
                if after is not None:                          # the verb agrees: "she owns" -> "I own", "they are" -> "I am"
                    at = n + 1 + (after in _ADVERBS and n + 2 < len(words))
                    out[words[at]] = _base_verb(tokens[words[at]])  # (some table entries say "they owns": that too)
            else:
                rest = low[len(subj):]
                new = "I" + ("'ve" if rest == "'s" and after in ("been", "got", "had", "done", "seen", "lost", "never") else _CONTRACTED[rest])
        elif low == poss and low == obj:                       # "her": "her name" -> "my name", "finds her" -> "finds me"
            new = "my" if after is not None and after not in _NOT_A_NOUN else "me"
        elif low == poss:                                      # "his": "his own" -> "my own", "the choice is his" -> "mine"
            new = "my" if after is not None and after not in _NOT_A_NOUN else "mine"
        elif low == obj:
            new = "me"
        elif low == poss_alone:
            new = "mine"
        if new is not None:
            out[i] = new if not word[:1].isupper() or new == "I" or new.startswith("I'") else new.capitalize()
    return "".join(out).replace("I am not", "I'm not")


def _occupation(text):
    """A typed occupation as it reads in a sentence: "the Crook" -> "the crook". A capital inside it is kept ("AI systems
    wrangler", "Ministry clerk"), as the tables write them."""
    words = text.split()
    if len(words) == 1 and words[0][:1].isupper() and words[0][1:].islower():
        return words[0].lower()
    return text


def generate(npc):
    profile = npc.get("profile", {})
    gender = profile.get("gender")
    name = " ".join(part for part in (profile.get("first_name"), profile.get("last_name")) if part).strip() or "friend"
    occupation = _occupation((profile.get("occupation") or "local").strip())
    goal = first_person((profile.get("goal") or "make something of this life").strip().rstrip("."), gender)
    quirk = first_person((profile.get("quirk") or "").strip().rstrip("."), gender)
    hobbies = profile.get("hobbies") or []
    if not isinstance(hobbies, list):
        hobbies = []
    hobby = first_person((hobbies[0] if hobbies else "taking a quiet moment").strip().rstrip("."), gender)
    pet_peeve = first_person((profile.get("peeve") or "").strip().rstrip("."), gender)
    lines = {
        "greeting": [f"Hello. I'm {name}, the {occupation}. What brings you here?", f"Good to meet you. How can this {occupation} help?"] ,
        "idle": [f"When work is done, I like {hobby}.", f"I keep thinking about my goal: {goal}."] ,
        "trade": [f"Tell me what you need; I'll see what a {occupation} can do.", f"Let's make this worthwhile. What are you looking for?"],
        # the tables write goals as "-ing" phrases ("opening a saloon"), a typed one may be "restore the old observatory":
        # "My aim is to opening a saloon" read wrong on paper and sounded wrong once the lines were voiced
        "quest": ([f"My aim is {goal}. If you can help, I won't forget it.",
                   f"There's more to life than this work. I've set my heart on {goal}."] if _gerund(goal) else
                  [f"My aim is to {goal}. If you can help, I won't forget it.",
                   f"There's more to life than this work. I mean to {goal}."]),
        "farewell": [f"Take care. I'll be here if you need a {occupation}.", "Until next time. Keep your wits about you."],
    }
    if quirk:
        lines["idle"].append(f"{quirk[:1].upper()}{quirk[1:]} — that's just how I am.")   # not capitalize(): it lowers "I" and names
    if pet_peeve:
        lines["trade"].append(f"One thing: {pet_peeve} gets on my nerves, so let's keep this simple.")
    return {
        "version": 1, "generator": "npcforge-profile-templates", "created": now(), "npc_id": npc.get("id"),
        "voice": {"speech": profile.get("speech", ""), "demeanor": profile.get("demeanor", "")},
        "lines": {category: [{"id": f"{category}_{i + 1}", "text": text} for i, text in enumerate(values)]
                  for category, values in lines.items()},
    }


def validate(pack):
    if not isinstance(pack, dict) or not isinstance(pack.get("lines"), dict):
        raise ValueError("Dialogue pack must contain a lines object")
    lines = {}
    for category in CATEGORIES:
        raw = pack["lines"].get(category, [])
        if not isinstance(raw, list) or len(raw) > 20:
            raise ValueError(f"{CATEGORY_LABELS[category]} must be a list of at most 20 lines")
        clean = []
        for i, item in enumerate(raw):
            if isinstance(item, str):
                text = item
                line_id = f"{category}_{i + 1}"
            elif isinstance(item, dict):
                text = item.get("text")
                line_id = item.get("id") or f"{category}_{i + 1}"
            else:
                raise ValueError("Each dialogue line must be text")
            if not isinstance(text, str) or not text.strip() or len(text) > 500:
                raise ValueError("Dialogue lines must contain 1 to 500 characters")
            clean.append({"id": re.sub(r"[^a-zA-Z0-9_-]", "_", str(line_id))[:64], "text": text.strip()})
        lines[category] = clean
    return {"version": 1, "generator": pack.get("generator", "user-edited"), "created": pack.get("created", now()),
            "npc_id": pack.get("npc_id"), "voice": pack.get("voice", {}), "stale": bool(pack.get("stale")), "lines": lines}


def render(pack, format_id, audio=None):
    """The pack as JSON, Ink or Yarn. `audio` ({line id: file}) adds the voiced lines: JSON gets an "audio" path per line and
    Yarn a #line:<id> tag, the line id Yarn Spinner matches voice-over files by. Ink is left as it is (the JSON carries the
    mapping)."""
    pack = validate(pack)
    audio = audio or {}
    if format_id == "json":
        for rows in pack["lines"].values():
            for line in rows:
                if line["id"] in audio:
                    line["audio"] = audio[line["id"]]
        return json.dumps(pack, indent=2, ensure_ascii=False), "application/json; charset=utf-8", "json"
    if format_id == "ink":
        out = ["=== start ==="]
        out.extend(f"+ [{CATEGORY_LABELS[category]}] -> {category}" for category in CATEGORIES if pack["lines"][category])
        out.extend(["-> END", ""])
        for category in CATEGORIES:
            rows = pack["lines"][category]
            if not rows:
                continue
            out.append(f"=== {category} ===")
            out.extend(f'+ "{_escape_script(line["text"])}" -> END' for line in rows)
            out.extend(["-> END", ""])
        return "\n".join(out), "text/plain; charset=utf-8", "ink"
    if format_id == "yarn":
        out = []
        for category in CATEGORIES:
            for i, line in enumerate(pack["lines"][category], 1):
                text = _escape_script(line["text"]) + (f" #line:{line['id']}" if line["id"] in audio else "")
                out.extend([f"title: npc_{category}_{i}", "---", text, "<<stop>>", "===", ""])
        return "\n".join(out), "text/plain; charset=utf-8", "yarn"
    raise ValueError(f"Unknown dialogue format '{format_id}'")


def _escape_script(text):
    """Keep user-edited lines on one safe data line in simple text-based exports."""
    return (re.sub(r"[\r\n]+", " ", text).replace("\\", "\\\\").replace('"', '\\"')
            .replace("{", "(").replace("}", ")").replace("<<", "‹‹").replace(">>", "››"))
