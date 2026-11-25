"""Projects: the games creatures are made for, and what each game needs of its creatures (Phase 4 of the creature maker).

A project is one game. It sets the triangle budget a new creature gets, the picture style its concepts are drawn in, whether the
game is sold (commercial), the engine it runs, where its packs are installed, the clip names of each body plan, and the acceptance
of any licence conditions the director has signed for it. Only a licence rated "conditions" is accepted: "yes" needs nothing, and
the others are not cleared.

The default list is data/projects.json. The first time a library is opened, that list is copied to library/.npcforge-projects.json,
and from then on the Projects panel edits the copy. A creature belongs to the project whose slug is its `project`.
"""
import copy
import datetime
import json
import re
import threading
from pathlib import Path

from . import atomic

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "data" / "projects.json"
LIVE_FILE = ".npcforge-projects.json"                 # beside the creature store, in the library folder
SCHEMA = "npcforge.projects/1"
ENGINES = ("godot-4",)                                # the engines a pack is installed for (Phase 5 adds a profile for each)
TRI_RANGE = (500, 20000)                              # the same range as a creature's own budget (npcgen/creatures.py)
ACCEPTABLE = "conditions"                             # the only licence rating a project can accept
SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
TEXT_LIMITS = {"style_prefix": ("The style", 400), "negative_extra": ("The extra negative words", 400),
               "background": ("The background", 200), "pack_dir": ("The pack folder", 300)}


class ProjectExists(ValueError):
    """A new game was asked for with a slug that a game already has."""


def whole_number(value, label):
    """An integer from a number or a text. 6000.7 is refused, not cut to 6000, and an infinite or missing value is refused too."""
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(f"{label} must be a whole number") from None
    if isinstance(value, float) and value != number:
        raise ValueError(f"{label} must be a whole number")
    return number


def clean_project(slug, fields, plans):
    """A project as it is saved: every field checked, in one fixed order. `plans` are the body plans the clip map may name.
    Raises ValueError with the reason."""
    if not isinstance(slug, str) or len(slug) > 40 or not SLUG.fullmatch(slug):
        raise ValueError("A game's slug is lower-case letters, digits and dashes, up to 40 characters (for example ember-hollow)")
    name = str(fields.get("name") or "").strip()
    if not 1 <= len(name) <= 80:
        raise ValueError("A game needs a name of 1 to 80 characters")
    budget = whole_number(fields.get("tri_budget"), "The triangle budget")
    if not TRI_RANGE[0] <= budget <= TRI_RANGE[1]:
        raise ValueError(f"The triangle budget must be between {TRI_RANGE[0]} and {TRI_RANGE[1]}")
    commercial = fields.get("commercial")
    if not isinstance(commercial, bool):
        raise ValueError("Say whether the game is sold commercially (yes or no)")
    engine = str(fields.get("engine") or "")
    if engine not in ENGINES:
        raise ValueError(f"The engine must be one of: {', '.join(ENGINES)}")
    text = {}
    for key, (label, limit) in TEXT_LIMITS.items():
        text[key] = str(fields.get(key) or "").strip()
        if len(text[key]) > limit:
            raise ValueError(f"{label} is longer than {limit} characters")
    key_prefix = str(fields.get("key_prefix") or "").strip()
    if not re.fullmatch(r"[a-z0-9-]{0,20}", key_prefix):
        raise ValueError("The key prefix is lower-case letters, digits and dashes, up to 20 characters")
    return {"slug": slug, "name": name, "tri_budget": budget, **text, "commercial": commercial, "engine": engine,
            "key_prefix": key_prefix, "clip_map": _clean_clip_map(fields.get("clip_map") or {}, plans),
            "licence_acceptance": _clean_acceptance(fields.get("licence_acceptance"))}


def _clean_clip_map(value, plans):
    """Per body plan, the unit's states and the clip each plays, e.g. {"serpentine": {"idle": "Idle", "moving": "Swim"}}."""
    if not isinstance(value, dict):
        raise ValueError("The clip names are a record per body plan: {\"serpentine\": {\"idle\": \"Idle\"}}")
    out = {}
    for plan, states in value.items():
        if plan not in plans:
            raise ValueError(f"The clip names name a body plan the maker does not know: {plan}")
        if not isinstance(states, dict) or not all(isinstance(k, str) and isinstance(v, str) and 1 <= len(k) <= 20
                                                   and 1 <= len(v.strip()) <= 40 for k, v in states.items()):
            raise ValueError(f"The clip names for {plan} are state names and clip names of 1 to 40 characters")
        out[plan] = {k: v.strip() for k, v in states.items()}
    return out


def _clean_acceptance(value):
    """None, or the director's acceptance of a "conditions" licence: who accepted it, the date, and why."""
    if value in (None, ""):
        return None
    if not isinstance(value, dict):
        raise ValueError("The licence acceptance is a record: its verdict, who accepted it, the date and the reason")
    if value.get("verdict") != ACCEPTABLE:
        raise ValueError("Only a licence rated \"conditions\" is accepted by a game: a \"yes\" needs no acceptance, and the others are not cleared")
    accepted_by = str(value.get("accepted_by") or "").strip()
    if not 1 <= len(accepted_by) <= 80:
        raise ValueError("Say who accepted the conditions (1 to 80 characters)")
    try:
        day = datetime.date.fromisoformat(str(value.get("date") or ""))
    except ValueError:
        raise ValueError("The date of the acceptance is YYYY-MM-DD") from None
    reason = str(value.get("reason") or "").strip()
    if not 1 <= len(reason) <= 400:
        raise ValueError("Say why the conditions are accepted (1 to 400 characters)")
    return {"verdict": ACCEPTABLE, "accepted_by": accepted_by, "date": day.isoformat(), "reason": reason}


class ProjectRegistry:
    """The projects of one library: read from its live list, and written to it when the Projects panel saves a game."""

    def __init__(self, library_root):
        self.path = Path(library_root) / LIVE_FILE
        self.lock = threading.RLock()

    def _load(self):
        if not self.path.exists():                        # the first time: the default list becomes the library's own
            self._save(json.loads(DEFAULT_PATH.read_text("utf-8"))["projects"])
        return json.loads(atomic.read_text(self.path))["projects"]

    def _save(self, projects):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"schema": SCHEMA, "projects": projects}, indent=2, ensure_ascii=False) + "\n", "utf-8")
        atomic.replace(tmp, self.path)

    def list(self):
        with self.lock:
            return copy.deepcopy(self._load())

    def get(self, slug):
        """The project with this slug, or None (a creature whose game is not on the list has none)."""
        with self.lock:
            return next((copy.deepcopy(p) for p in self._load() if p.get("slug") == slug), None)

    def put(self, slug, fields, plans):
        """Change a game, or save one that does not exist yet (create is for a new game). A licence acceptance left out of
        `fields` is kept: only an explicit null removes it. Returns the saved project."""
        with self.lock:
            projects = self._load()
            current = next((p for p in projects if p.get("slug") == slug), None)
            if current is not None and "licence_acceptance" not in fields:
                fields = {**fields, "licence_acceptance": current.get("licence_acceptance")}
            project = clean_project(slug, fields, plans)
            if current is not None:
                projects = [project if p.get("slug") == slug else p for p in projects]
            else:
                projects.append(project)
            self._save(projects)
        return project

    def create(self, slug, fields, plans):
        """A new game. A slug a game already has is refused: an existing game is changed with put, never replaced."""
        project = clean_project(slug, fields, plans)
        with self.lock:
            projects = self._load()
            if any(p.get("slug") == slug for p in projects):
                raise ProjectExists(f"A game with the slug {slug} already exists: open it on the Projects panel to change it")
            projects.append(project)
            self._save(projects)
        return project


def concept_text(project, prompt, negative):
    """The prompt and the negative prompt a concept is drawn with. The game's style goes in front of the creature's own prompt,
    unless the prompt already starts with it; the game's background is asked for, unless the prompt already names one; and the
    game's extra negative words are added, unless they are there. With no project, or where the prompt already has them, the
    picture is drawn exactly as it was written."""
    if not project:
        return prompt, negative
    style = (project.get("style_prefix") or "").strip()
    text = prompt
    if style and not text.lower().startswith(style.lower()):
        text = f"{style} {text}"
    background = (project.get("background") or "").strip()
    if background and "background" not in text.lower():
        body = text.rstrip()
        text = f"{body if body.endswith('.') else body + '.'} {background}"
    extra = (project.get("negative_extra") or "").strip()
    if extra:
        known = {term.strip().lower() for term in negative.split(",")}          # whole terms: "limb" is not "extra limbs"
        added = []
        for term in (part.strip() for part in extra.split(",")):
            if term and term.lower() not in known:
                known.add(term.lower())
                added.append(term)
        if added:
            negative = ", ".join([negative, *added])
    return text, negative


def licence_refusal(verdict, project, label=""):
    """Why a creature rated `verdict` may not be packed for `project`, or None when it may. A game that is sold takes "yes", and
    "conditions" once its director has accepted them. A game that is not sold takes any rating that was made (not "none")."""
    if project is None:
        return "This creature's game is not on the project list: add it on the Projects panel before making a pack"
    if verdict == "none":
        return "Nothing has been made yet that its licence can be rated from"
    if not project.get("commercial") or verdict == "yes":
        return None
    if verdict == ACCEPTABLE:
        if project.get("licence_acceptance"):
            return None
        return ("Its licence is allowed with conditions, and this game has not accepted them yet. Read the conditions and accept them "
                "on the Projects panel before making a pack")
    return f"This pack is not cleared for commercial work: {label or verdict}. Open the licence list for the reason."


def accepted_conditions(verdict, project):
    """The acceptance a pack carries: the game's record of its licence conditions, when the rating is "conditions" and it has one."""
    if verdict != ACCEPTABLE or not project:
        return None
    return copy.deepcopy(project.get("licence_acceptance"))
