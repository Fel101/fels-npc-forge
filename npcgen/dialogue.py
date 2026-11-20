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


def generate(npc):
    profile = npc.get("profile", {})
    name = " ".join(part for part in (profile.get("first_name"), profile.get("last_name")) if part).strip() or "friend"
    occupation = (profile.get("occupation") or "local").strip()
    goal = (profile.get("goal") or "make something of this life").strip().rstrip(".")
    quirk = (profile.get("quirk") or "").strip().rstrip(".")
    hobbies = profile.get("hobbies") or []
    if not isinstance(hobbies, list):
        hobbies = []
    hobby = (hobbies[0] if hobbies else "taking a quiet moment").strip().rstrip(".")
    pet_peeve = (profile.get("peeve") or "").strip().rstrip(".")
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
        lines["idle"].append(f"{quirk.capitalize()} — that's just how I am.")
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
