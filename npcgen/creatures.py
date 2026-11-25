"""Creatures on disk: one folder per creature (creature.json, its concept picture, its 3D files and its game model), kept the way
npcgen/itemstore.py keeps items: in a hidden folder next to the character library, so a creature can not turn up as a character.

The list the Creatures tab starts from is one file per game in data/briefs/ (the examples that ship) or local/briefs/ (your own games,
git-ignored; a game's own tool can write its bestiary there). A creature is made in three steps, and each step
reads the one before it from disk:

  concept   a picture of the creature (Qwen-Image 2512, Apache-2.0). Someone approves it before the next step.
  3d        a TRELLIS.2-4B model made from the approved picture (MIT)
  prep      the game model: scale, decimation, one matte colour, and a 12-bone rig for a serpentine (the other body plans are
            not rigged yet, so they come out as a static model)

A creature belongs to one game (its project, a slug such as ember-hollow), and the game's settings are its project
(npcgen/creature_projects.py). Its engines are rated in data/licences.json (licence()), and npcgen/creaturepack.py packs it for the
game. The body plans are in data/creature_plans.json.

Everything is read and written under one lock, because the job worker and the web server's threads both touch a creature.
"""
import copy
import hashlib
import json
import math
import os
import random
import re
import shutil
import threading
import time
from pathlib import Path

from . import creature_projects, licences, media
from .library import ID_RE, now

BRIEFS_DIR = Path(__file__).resolve().parent.parent / "data" / "briefs"     # one file per game: data/briefs/<project>.json (the examples)
LOCAL_BRIEFS_DIR = Path(__file__).resolve().parent.parent / "local" / "briefs"  # your own games' briefs, git-ignored; one wins over the other


def _brief_files():
    """{project slug: path} of every game's brief file; a file in local/briefs/ replaces the example of the same name."""
    return {path.stem: path for folder in (BRIEFS_DIR, LOCAL_BRIEFS_DIR) for path in sorted(folder.glob("*.json"))}
PLANS_PATH = Path(__file__).resolve().parent.parent / "data" / "creature_plans.json"
PLANS = json.loads(PLANS_PATH.read_text("utf-8"))["plans"]    # the body plans the maker knows (data/creature_plans.json)
BODY_PLANS = tuple(PLANS)
RIG_BUILT = tuple(plan for plan, info in PLANS.items() if info["status"] == "rigged")   # the rest are static models
STAGES = ("concept", "3d", "prep")
STAGE_KIND = {"concept": "creature_concept", "3d": "creature_3d", "prep": "creature_prep"}
STAGE_LABEL = {"concept": "Concept picture", "3d": "3D model (TRELLIS.2)", "prep": "Game model (prep and rig)"}
STAGE_FILE = {"concept": "concept.png", "3d": "raw/model.glb", "prep": "model.glb"}
THREE_D_ENGINE_BY_MODEL = {"microsoft/TRELLIS.2-4B": "trellis2_single"}     # for records made before run.json named the engine
EDITABLE = ("name", "project", "body_plan", "length_m", "segments", "seed", "tris", "prompt", "shore", "form", "tier", "route", "facing",
            "key", "notes")
FACINGS = ("left", "right")              # the side the head is on in the picture; "front" is not measured yet (npcgen/blender/prep_rules.py)
TRIS_DEFAULT = 20000                    # ART_DIRECTION 5.4: a creature's LOD0 is at most 20,000 triangles
TRIS_RANGE = creature_projects.TRI_RANGE    # a game may ask for fewer (a flying unit at 6,000, for one)
LENGTH_RANGE = (0.3, 6.0)
SEGMENT_RANGE = (4, 24)
SEED_MAX = 2 ** 31 - 1
CANDIDATES = "concepts"                 # a picture grid: one picture per seed; the one chosen becomes concept.png
GRID_SIZE = 4
RACY_NS = 2_000_000_000                 # a file changed within this long before it was hashed is not cached (see CreatureStore.sha256)


def load_briefs(project=None):
    """The brief creatures of one game (its file in data/briefs/), or of every game when none is named. A brief is a creature
    the game needs and the maker can start from. The bestiary's own "rigged" wording is kept as rig_intent; `rigged` is what
    the maker builds, from the body plan (22 of the 29 bestiary rigs were not built)."""
    if project is not None and not (isinstance(project, str) and creature_projects.SLUG.fullmatch(project)):
        return []
    found = _brief_files()
    files = [found[project]] if project in found else [] if project else [found[name] for name in sorted(found)]
    briefs = []
    for path in files:
        if path.exists():
            briefs.extend(json.loads(path.read_text("utf-8")))
    for brief in briefs:
        brief["rig_intent"] = brief.pop("rigged", None)
        brief["rigged"] = brief.get("body_plan") in RIG_BUILT
    return briefs


def clean_seeds(value):
    """The seeds of a picture grid: 1 to GRID_SIZE different whole numbers. Raises ValueError with the reason."""
    if not isinstance(value, list) or not 1 <= len(value) <= GRID_SIZE:
        raise ValueError(f"A picture grid takes 1 to {GRID_SIZE} seeds")
    seeds = []
    for item in value:
        try:
            seed = int(item)
        except (TypeError, ValueError):
            raise ValueError("The seeds must be whole numbers") from None
        if not 0 <= seed <= SEED_MAX or seed in seeds:
            raise ValueError("Each seed must be a different number from 0 to 2147483647")
        seeds.append(seed)
    return seeds


def clean_creature(fields, game=None):
    """Validated fields for a creature (a new one, or an edit). `game` is the game's record (npcgen/creature_projects.py): a
    creature given no triangle budget takes the game's. Raises ValueError with the reason."""
    name = str(fields.get("name") or "").strip()
    if not 1 <= len(name) <= 80:
        raise ValueError("A creature needs a name of 1 to 80 characters")
    project = project_slug(fields.get("project"))                # the game it is for ("" = not assigned yet)
    plan = str(fields.get("body_plan") or "").strip()
    if plan not in BODY_PLANS:
        raise ValueError(f"The body plan must be one of: {', '.join(BODY_PLANS)}")
    try:
        length = float(fields.get("length_m", 2.5))
        segments = int(fields.get("segments", 12))
        seed = int(fields["seed"]) if fields.get("seed") not in (None, "") else random.randrange(SEED_MAX)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("Length, segments and seed must be numbers") from None
    tris = creature_projects.whole_number(fields.get("tris") or (game or {}).get("tri_budget") or TRIS_DEFAULT, "The triangle budget")
    if not LENGTH_RANGE[0] <= length <= LENGTH_RANGE[1]:
        raise ValueError(f"The length must be between {LENGTH_RANGE[0]} and {LENGTH_RANGE[1]} metres")
    if not SEGMENT_RANGE[0] <= segments <= SEGMENT_RANGE[1]:
        raise ValueError(f"The segments must be between {SEGMENT_RANGE[0]} and {SEGMENT_RANGE[1]}")
    if not TRIS_RANGE[0] <= tris <= TRIS_RANGE[1]:
        raise ValueError(f"The triangle budget must be between {TRIS_RANGE[0]} and {TRIS_RANGE[1]}")
    if not 0 <= seed <= SEED_MAX:
        raise ValueError("The seed must be between 0 and 2147483647")
    prompt = str(fields.get("prompt") or "").strip()
    if len(prompt) > 1500:
        raise ValueError("The concept prompt is longer than 1500 characters")
    shore = str(fields.get("shore") or "").strip()[:12].upper()
    facing = str(fields.get("facing") or "left").strip().lower()
    if facing not in FACINGS:
        raise ValueError("The picture's facing must be left or right (the side the head is on)")
    return {"name": name, "project": project, "body_plan": plan, "length_m": round(length, 3), "segments": segments, "seed": seed,
            "tris": tris, "prompt": prompt, "shore": shore, "facing": facing, "form": str(fields.get("form") or "")[:200],
            "tier": str(fields.get("tier") or "")[:8], "route": str(fields.get("route") or "")[:200],
            "key": str(fields.get("key") or "")[:40], "notes": str(fields.get("notes") or "")[:2000]}


def _slug(text):
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


def project_slug(value):
    """A game as the store keeps it: the slug of its name ("Dust Moon" is dust-moon)."""
    return _slug(str(value or ""))[:40]


def rigged(creature):
    return creature.get("body_plan") in RIG_BUILT


def plan(creature, request):
    """The steps to run, in order. `request` names the stages to make (request["steps"]) and the ones to make again
    (request["redo"]). A stage that is missing brings the stages before it along: the 3D model needs the concept, the game
    model needs the 3D model."""
    if creature.get("body_plan") not in BODY_PLANS:
        raise ValueError("This creature has no body plan the pipeline makes")
    wanted_names = [s for s in (request or {}).get("steps", []) if s in STAGES]
    if not wanted_names:
        raise ValueError("Choose at least one step to make")
    redo = {s for s in (request or {}).get("redo", []) if s in STAGES}
    have = exists(creature)
    wanted = {stage: stage in wanted_names and (stage in redo or not have[stage]) for stage in STAGES}
    for after, before in (("prep", "3d"), ("3d", "concept"), ("prep", "concept")):
        if wanted[after] and not have[before]:
            wanted[before] = True
    makes = PLANS[creature["body_plan"]].get("make", list(STAGES))
    if (wanted["3d"] or wanted["prep"]) and "3d" not in makes:
        raise ValueError(f"A {PLANS[creature['body_plan']]['label'].lower()} creature is a concept picture only, for now. "
                         + PLANS[creature["body_plan"]]["note"])
    if wanted["3d"] and wanted["concept"]:
        # The 3D model is made from the picture someone has looked at. A picture drawn in this same request has not been looked
        # at yet, so it is made first, approved, and then the 3D model is asked for.
        raise ValueError("Make the concept picture on its own first. Approve it, then make the 3D model.")
    if wanted["3d"] and not creature.get("concept_approved"):
        raise ValueError("Approve the concept picture before its 3D model is made.")
    return [{"kind": STAGE_KIND[stage], "key": stage, "label": STAGE_LABEL[stage], "status": "pending", "error": None}
            for stage in STAGES if wanted[stage]]


def exists(creature):
    """Which stages' files are on disk. The store fills this in from the creature's folder."""
    return {stage: bool(creature.get("_on_disk", {}).get(stage)) for stage in STAGES}


def prep_settings(creature):
    """What the game-model step is made from, as prep.json records it. A serpent's head is found from its shape, so its facing is
    not part of it: a serpent's facing can change without its model changing."""
    plan = PLANS.get(creature.get("body_plan"), {}).get("prep", "static")
    return {"tris": int(creature.get("tris") or TRIS_DEFAULT), "length_m": creature.get("length_m"), "segments": creature.get("segments"),
            "plan": plan, "facing": (creature.get("facing") or "left") if plan == "static" else None}


def out_of_date(on_disk, making):
    """The stages a request leaves out of date: one that is on disk, is not being made, and comes after a stage that is."""
    return [stage for i, stage in enumerate(STAGES)
            if on_disk.get(stage) and stage not in making and any(earlier in making for earlier in STAGES[:i])]


def _is_number(value):
    # NaN and infinity are numbers to isinstance, but they are not a measurement: a size that is NaN is not measured.
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def shape_checks(body_plan, report):
    """The shape checks that apply to a body plan, measured on its game model's report (prep.json). A ground body must not stand
    taller than it is long: the deformed-hound case. A serpent must be long enough to be a serpent (its elongation, measured before
    the fit). A static body (six-leg, quadruped, flying, prop) must be the way up its picture shows (the up check, npcgen/up_check.py,
    which the prep records). Other plans have none. `blocking` names the checks that fail: the pack refuses on any of them. `warnings`
    says each failure, then each check that can not be measured yet (a model made before the check existed)."""
    info = PLANS.get(body_plan, {})
    out = {"applies": [], "blocking": [], "warnings": []}
    size = report.get("size_m")                               # Blender's axes: x is the depth, y the length, z the height
    measured = isinstance(size, list) and len(size) == 3 and all(_is_number(v) for v in size)
    if info.get("ground"):
        out["applies"].append("taller_than_long")
        if not measured:
            out["taller_than_long"] = None
            out["warnings"].append("Its size was not measured, so it is not known whether it stands taller than it is long: "
                                   "make the game model again.")
        else:
            out["taller_than_long"] = size[2] > size[1]
            if out["taller_than_long"]:
                out["blocking"].append("taller_than_long")
                out["warnings"].append("Taller than it is long: the 3D shape may be deformed. Check the picture and the model.")
    if "min_elongation" in info:
        out["applies"].append("elongation")
        elongation = report.get("elongation") if _is_number(report.get("elongation")) else None
        out["elongation"] = elongation
        if elongation is None:
            out["warnings"].append("Its elongation was not measured (the game model was made before the shape check): make the game model again.")
        elif elongation < info["min_elongation"]:
            out["blocking"].append("elongation")
            out["warnings"].append(f"Too short to be a serpent: its body is {elongation:.2f} times as long as it is wide or high "
                                   f"(at least {info['min_elongation']:g} is needed). Redraw the picture from the side, stretched out, "
                                   "and make the 3D model again.")
    if info.get("prep") == "static":
        out["applies"].append("upright_picture")
        check = report.get("up_check") if isinstance(report.get("up_check"), dict) else None
        verdict = check.get("verdict") if check else None
        out["upright_picture"] = verdict
        if check is None:
            out["warnings"].append("Its up direction was not checked against its picture (made before the check existed): "
                                   "make the game model again.")
        elif verdict == "contradicted":
            out["blocking"].append("upright_picture")
            out["warnings"].append(f"Turned over, by its picture: the model matches the picture better upside down (standing "
                                   f"{check['standing']:.2f}, turned over {check['turned_over']:.2f}). Check its side view, and the "
                                   "picture, then make the game model again.")
        elif verdict == "unclear":
            out["warnings"].append("Its up direction is not clear from the picture: the model matches it about as well either way. "
                                   "Check its side view.")
        elif verdict is None:
            out["warnings"].append("Its up direction could not be checked against its picture: "
                                   f"{check.get('reason') or 'no reason was given'}.")
    return out


class CreatureStore:
    def __init__(self, lib):
        self.lib = lib
        self.root = lib.root / ".npcforge-creatures"
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.projects = creature_projects.ProjectRegistry(lib.root)       # the games: their budgets, styles and licence acceptance
        self._hashes = {}                                       # (path, mtime, size) -> SHA-256, so a list does not read the models again

    # ---- paths and files -------------------------------------------------------------------------------------------------------
    def _dir(self, creature_id):
        if not ID_RE.match(creature_id or ""):
            raise KeyError(creature_id)
        return self.root / creature_id

    def resolve(self, creature_id, rel):
        """Absolute path of a file inside a creature's folder. Refuses anything that escapes it."""
        base = self._dir(creature_id).resolve()
        path = (base / rel).resolve()
        if not path.is_relative_to(base):
            raise KeyError(rel)
        return path

    def write_file(self, creature_id, rel, data):
        path = self.resolve(creature_id, rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
        os.replace(tmp, path)
        return path

    def read_file(self, creature_id, rel):
        return self.resolve(creature_id, rel).read_bytes()

    def report(self, creature_id):
        """What the game-model step measured (its prep.json), or None before that step is made. `checks` is what the shape checks
        of the creature's body plan say about those measurements (shape_checks)."""
        try:
            report = json.loads(self.read_file(creature_id, "prep.json"))
            body_plan = self.get(creature_id)["body_plan"]
        except (OSError, ValueError, KeyError):
            return None
        report["checks"] = shape_checks(body_plan, report)
        return report

    def _on_disk(self, creature_id):
        return {stage: self.resolve(creature_id, rel).is_file() for stage, rel in STAGE_FILE.items()}

    # ---- records --------------------------------------------------------------------------------------------------------------
    def new_id(self, name):
        for _attempt in range(20):
            candidate = f"{(_slug(name) or 'creature')[:40]}-{os.urandom(3).hex()}"
            if not (self.root / candidate).exists():
                return candidate
        raise RuntimeError("Could not find a free creature id")

    def get(self, creature_id):
        path = self._dir(creature_id) / "creature.json"
        if not path.exists():
            raise KeyError(creature_id)
        creature = json.loads(path.read_text("utf-8"))
        creature["_on_disk"] = self._on_disk(creature_id)
        creature.setdefault("concept_approved", creature["_on_disk"]["concept"])    # a record from before approval keeps its picture
        if not creature.get("tris"):                        # a record from before the game budgets takes its game's budget
            creature["tris"] = int((self.projects.get(creature.get("project")) or {}).get("tri_budget") or TRIS_DEFAULT)
        return creature

    def record(self, creature_id, rel):
        """A JSON record the pipeline wrote beside a creature ({} when it is missing or unreadable)."""
        try:
            return json.loads(self.read_file(creature_id, rel))
        except (OSError, ValueError, KeyError):
            return {}

    def licence(self, creature_id):
        """The licence report for the engines that made the creature's concept picture and its 3D model (npcgen/licences.py)."""
        on_disk = self._on_disk(creature_id)
        stages = {}
        if on_disk["concept"]:
            stages["concept"] = self.record(creature_id, "concept.json").get("engine")
        if on_disk["3d"]:
            run = self.record(creature_id, "raw/run.json")
            stages["3d"] = run.get("engine") or THREE_D_ENGINE_BY_MODEL.get(run.get("model"))
        return licences.creature(stages)

    def sha256(self, creature_id, rel):
        """The SHA-256 of a file in the creature's folder. Cached by the file's size and time: a list of creatures reads each model once."""
        path = self.resolve(creature_id, rel)
        stat = path.stat()
        key = (str(path), stat.st_mtime_ns, stat.st_size)
        with self.lock:
            if key in self._hashes:
                return self._hashes[key]
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        # The racy rule (as git uses it): a file changed within the last RACY_NS may change again in the same clock tick with the
        # same size, so only a file that was already old when it was read is kept.
        if time.time_ns() - stat.st_mtime_ns > RACY_NS:
            with self.lock:
                self._hashes = {k: v for k, v in self._hashes.items() if k[0] != key[0]}     # one entry per file
                self._hashes[key] = digest
        return digest

    def freshness(self, creature_id, creature=None):
        """Whether each stage is still the one the stage above it was made from. Each stage is {"state", "reason"}:
          missing   not made yet
          fresh     made from what is there now
          stale     something above it changed after it was made (the reason says what)
          unknown   made before this check existed, so nothing says what it was made from (it is allowed, with a warning)
        The game model is out of date when its 3D model is, and when its settings or the 3D model's file have changed."""
        creature = self.get(creature_id) if creature is None else creature
        on_disk = self._on_disk(creature_id)
        out = {"concept": {"state": "fresh" if on_disk["concept"] else "missing", "reason": ""}}

        if not on_disk["3d"]:
            out["3d"] = {"state": "missing", "reason": ""}
        else:
            run = self.record(creature_id, "raw/run.json")
            recorded = run.get("concept_sha256")
            seed_changed = "seed" in run and run["seed"] != creature.get("seed")     # the seed drives the model, hashed or not
            if recorded is None and seed_changed:
                out["3d"] = {"state": "stale", "reason": "the seed changed after this model was made"}
            elif recorded is None:
                out["3d"] = {"state": "unknown", "reason": "made before the freshness check: nothing says which picture it came from"}
            elif not on_disk["concept"]:
                out["3d"] = {"state": "stale", "reason": "the concept picture it was made from is gone"}
            elif recorded != self.sha256(creature_id, "concept.png"):
                out["3d"] = {"state": "stale", "reason": "the concept picture changed after this model was made"}
            elif seed_changed:
                out["3d"] = {"state": "stale", "reason": "the seed changed after this model was made"}
            else:
                out["3d"] = {"state": "fresh", "reason": ""}

        if not on_disk["prep"]:
            out["prep"] = {"state": "missing", "reason": ""}
        elif not on_disk["3d"]:
            out["prep"] = {"state": "stale", "reason": "its 3D model is gone"}
        elif out["3d"]["state"] == "stale":
            out["prep"] = {"state": "stale", "reason": "the 3D model it was made from is out of date"}
        else:
            prep = self.record(creature_id, "prep.json")
            recorded, settings = prep.get("raw_sha256"), prep.get("settings")
            now_settings = prep_settings(creature)
            if recorded is None or settings is None:
                out["prep"] = {"state": "unknown", "reason": "made before the freshness check: nothing says what it was made from"}
            elif recorded != self.sha256(creature_id, "raw/model.glb"):
                out["prep"] = {"state": "stale", "reason": "the 3D model changed after this game model was made"}
            elif settings != now_settings:
                changed = sorted(k for k in set(settings) | set(now_settings) if settings.get(k) != now_settings.get(k))
                out["prep"] = {"state": "stale", "reason": f"the settings changed after this game model was made: {', '.join(changed)}"}
            else:
                out["prep"] = {"state": "fresh", "reason": ""}
        return out

    def summary(self, creature):
        on_disk = self._on_disk(creature["id"])
        return {"id": creature["id"], "name": creature.get("name", ""), "project": creature.get("project", ""),
                "key": creature.get("key", ""), "shore": creature.get("shore", ""), "body_plan": creature.get("body_plan", ""),
                "tier": creature.get("tier", ""), "form": creature.get("form", ""), "rigged": rigged(creature),
                "rig_built": rigged(creature), "length_m": creature.get("length_m"), "updated": creature.get("updated"),
                "has": on_disk, "report": self.report(creature["id"]), "licence": self.licence(creature["id"])["verdict"],
                "freshness": self.freshness(creature["id"], creature)}

    def list(self, project=None):
        """Every creature's summary, by shore and name. `project` keeps only one game's creatures."""
        with self.lock:
            out = []
            for folder in self.root.iterdir():
                path = folder / "creature.json"
                if not path.exists():
                    continue
                try:
                    out.append(self.summary(json.loads(path.read_text("utf-8"))))
                except (KeyError, ValueError, TypeError, OSError):
                    continue
            if project is not None:
                out = [s for s in out if s["project"] == project]
            return copy.deepcopy(sorted(out, key=lambda s: (s.get("shore") or "", s["name"])))

    def create(self, fields):
        """A new creature from validated fields (clean_creature)."""
        with self.lock:
            creature = {**fields, "id": self.new_id(fields["name"]), "created": now(), "updated": now()}
            self._dir(creature["id"]).mkdir(parents=True)
            self._write(creature)
            return self.get(creature["id"])

    def save(self, creature):
        with self.lock:
            creature["updated"] = now()
            self._write(creature)
            return self.get(creature["id"])

    def update(self, creature_id, fn):
        """Read-modify-write under the lock, so the worker thread and the web server's threads never clobber each other."""
        with self.lock:
            creature = self.get(creature_id)
            creature.pop("_on_disk", None)
            fn(creature)
            return self.save(creature)

    def approve_concept(self, creature_id):
        """Approve the concept picture: the 3D model is then made from it."""
        with self.lock:
            if not self._on_disk(creature_id)["concept"]:
                raise ValueError("There is no concept picture to approve yet: make it first")
            return self.update(creature_id, lambda c: c.update(concept_approved=True))

    def candidates(self, creature_id):
        """The pictures of the last picture grid, by seed: [{"seed", "file"}]. Empty when no grid was drawn."""
        folder = self._dir(creature_id) / CANDIDATES
        if not folder.is_dir():
            return []
        found = [{"seed": int(path.stem), "file": f"{CANDIDATES}/{path.name}"}
                 for path in folder.glob("*.png") if path.stem.isdigit()]
        return sorted(found, key=lambda c: c["seed"])

    def choose_concept(self, creature_id, seed):
        """Make the grid's picture of `seed` the concept picture, with its record, and approve it. The creature's seed becomes
        that seed, so the 3D model is made from it."""
        with self.lock:
            if not any(c["seed"] == seed for c in self.candidates(creature_id)):
                raise ValueError(f"There is no picture with seed {seed} in the grid")
            picture = self.read_file(creature_id, f"{CANDIDATES}/{seed}.png")
            record = self.record(creature_id, f"{CANDIDATES}/{seed}.json") or {"seed": seed}
            names = media.source_models(picture) or [licences.model_name(f"image:{record.get('engine')}")]
            picture = media.label(picture, names)
            record = {**record, "sha256": hashlib.sha256(picture).hexdigest()}      # the picture the 3D model will be made from
            self.write_file(creature_id, "concept.png", picture)
            self.write_file(creature_id, "concept.json", json.dumps(record, indent=2))

            def chosen(c):
                c.update(seed=seed, concept_approved=True)
                c.setdefault("made", {}).update(concept=now())
            return self.update(creature_id, chosen)

    def _write(self, creature):
        path = self._dir(creature["id"]) / "creature.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(creature, indent=2, ensure_ascii=False), "utf-8")
        os.replace(tmp, path)

    def delete(self, creature_id):
        with self.lock:
            shutil.rmtree(self._dir(creature_id))

    def game(self, project):
        """The record of the game a name or slug names (None when it is not on the list)."""
        return self.projects.get(project_slug(project))

    def adopt_projects(self, briefs=None, write=True):
        """Give each creature its game's project, once: a bestiary creature made before projects existed has none. Idempotent.
        Before a creature.json is first changed, its old text is kept beside it as creature.json.v1; nothing is deleted.
        With write=False it only counts what would change. Returns the number of creatures changed (or that would change)."""
        briefs = load_briefs() if briefs is None else briefs
        project_of = {b["key"]: b["project"] for b in briefs if b.get("key") and b.get("project")}
        adopted = 0
        with self.lock:
            for folder in self.root.iterdir():
                path = folder / "creature.json"
                try:
                    creature = json.loads(path.read_text("utf-8"))
                except (OSError, ValueError):
                    continue
                wanted = project_of.get(creature.get("key"))
                if wanted and not creature.get("project"):
                    adopted += 1
                    if write:
                        backup = folder / "creature.json.v1"
                        if not backup.exists():
                            shutil.copyfile(path, backup)
                        creature["project"] = wanted
                        self._write(creature)
        return adopted

    def seed(self, project, briefs=None):
        """Add one game's brief creatures that are not in the list yet. A creature is listed once per (game, key): a brief
        whose key another game already uses is still added for this one. Existing creatures are left alone."""
        briefs = load_briefs(project) if briefs is None else briefs
        game = self.projects.get(project)
        with self.lock:
            present = {(c.get("project"), c.get("key")) for c in (self.get(s["id"]) for s in self.list())}
            added = 0
            for brief in briefs:
                if not brief.get("makeable") or (project, brief["key"]) in present:
                    continue
                fields = clean_creature({"name": brief["name"], "project": project,
                                         "body_plan": brief["body_plan"], "prompt": brief["prompt"],
                                         "length_m": brief.get("length_m", 2.5), "segments": 12,
                                         "shore": brief.get("shore", ""), "form": brief.get("form", ""),
                                         "tier": brief.get("tier") or "", "route": brief.get("route", ""),
                                         "key": brief["key"]}, game)
                self.create(fields)
                present.add((project, brief["key"]))             # a key listed twice in one file is added once
                added += 1
            return {"added": added, "listed": len(briefs)}

    def seed_all(self):
        """Seed every game that has a brief file (see seed)."""
        totals = {"added": 0, "listed": 0}
        for name in sorted(_brief_files()):
            result = self.seed(name)
            totals["added"] += result["added"]
            totals["listed"] += result["listed"]
        return totals
