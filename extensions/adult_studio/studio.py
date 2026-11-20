"""Adult studio: make and curate adult-level pictures of a character, and perfect the character with body notes.

It works only for adults (18+), only after an
"I am 18 or older" confirmation, and under the same hard rules as Altera (safety.py holds the same rules as Altera's core/safety.py).

What it does
  * Pictures are edits of the character's own portrait (Mage-Flow-Edit through ComfyUI), one per scene. The prompt is the one
    Altera uses, which was measured: "change the clothing completely" stops the portrait's clothes leaking into the scene.
  * You look at them (blurred until clicked), keep the good ones, redo or delete the rest. Only KEPT pictures are what Altera
    imports as ready-made pictures, and Altera age-checks every one again on import.
  * The body is chosen once per character: bust size, shape and areolas (body.py) plus free body notes ("athletic build, defined
    waist"). They are added to every prompt here and, through the import, to Altera's own pictures. Perfecting a character is:
    adjust them, redo, compare, repeat. (Measured: giving a nude picture as a second reference made bodies drift and exaggerate,
    and took half as long again, so the options are words only.)
  * Two picture engines (engines.py): Mage-Flow-Edit, and Qwen-Image-Edit 2511 with the Lightning LoRA, which draws far better
    whole bodies. Qwen is the default for suggestive and explicit; it needs its files in ComfyUI and about 30 GB of free RAM, and
    when it cannot run (or a picture goes wrong) the studio makes that picture with Mage-Flow and says so on the picture.
  * Suggestive and explicit pictures ask for a full-length frame. The portrait is a head-and-shoulders crop and the edit copies
    its framing (measured: without the sentence, "nude" pictures ended at the collarbone and a pose seen from behind was ignored).

Files, next to the character: <npc>/adult/items.json, studio.json, img/<item>.png. Altera reads those and nothing else.
"""
import base64
import datetime
import hashlib
import json
import os
import queue
import random
import re
import tempfile
import threading
import time
import traceback
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from npcgen.comfy import ComfyError, edit_graph

from . import body as bodies, engines, safety

LEVELS = ("off", "romantic", "suggestive", "explicit")
LEVEL_LABELS = {"off": "Everyday", "romantic": "Romantic", "suggestive": "Suggestive", "explicit": "Explicit"}
MOOD = {"off": "a natural, friendly expression", "romantic": "a warm, flirty, affectionate expression",
        "suggestive": "a confident, sultry expression", "explicit": "a confident, sultry expression"}
QUALITY = {"draft": (512, 704, 20), "standard": (768, 1024, 30), "high": (1024, 1344, 30)}   # width, height, steps
FULL_LENGTH = {"draft": (512, 768, 20), "standard": (832, 1216, 30), "high": (960, 1408, 30)}  # a taller picture for a whole-body frame
TRAIT_FIELDS = ("heritage", "ethnicity", "hair", "hair_color", "eyes", "build", "skin_tone")    # what the page filters the character list by
FRAMES = {"full": "Frame it as a full-length photograph taken from a few metres back, so the whole body is visible from head to "
                  "knees or further, with plenty of space around the figure."}
FRAME_LEVELS = ("suggestive", "explicit")               # the levels that get a full-length frame unless the scene says "auto"
STYLES = {
    "photoreal": {"edit_name": "photorealistic",
                  "look": "A candid, realistic smartphone photograph with natural light, sharp focus and natural skin texture."},
    "semi_real": {"edit_name": "soft painterly digital painting", "look": "A soft painterly digital painting."},
    "cartoon": {"edit_name": "flat vector cartoon", "look": "A flat vector cartoon illustration."},
    "pixel": {"edit_name": "16-bit pixel", "look": "A 16-bit pixel art illustration."},
}
BASE_NEGATIVE = ("blurry, low quality, deformed face, extra fingers, extra limbs, deformed hands, watermark, text, logo, "
                 "signature, cropped, multiple people, collage")
ITEM_ID = re.compile(r"^s-[0-9a-f]{8}$")
MAX_BATCH = 16
MAX_SCENE = 300
MAX_NOTES = 300

DEFAULT_SETTINGS = {
    "confirmed_at": None,
    "quality": "standard",
    "cfg": 4.0,
    "engines": {**engines.DEFAULT_ENGINES, "portrait": engines.DEFAULT_PORTRAIT_ENGINE},   # level -> engine id (engines.ENGINES);
                                                                                          # "portrait" -> a Models portrait engine
    # Optional. A vision model that checks each picture here. Blank = not checked in the studio (Altera still checks on import).
    "age_check": {"base_url": "http://127.0.0.1:1234/v1", "api_key": "lm-studio", "model": ""},
}


class StudioError(ValueError):
    """Something the user can fix: the message is safe to show."""


class NotFound(StudioError):
    pass


class Forbidden(StudioError):
    pass


def _now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return default


def normalize(text, limit) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().strip("\"'`")[:limit].strip(" .")


# ---- what the character looks like, and the prompt ----------------------------------------------------------------------

def look_of(appearance: dict) -> str:
    a = appearance or {}
    parts = []
    if a.get("build"):
        parts.append(f'{a["build"]} build')
    if a.get("skin_tone"):
        parts.append(f'{a["skin_tone"]} skin')
    if a.get("hair"):
        parts.append(a["hair"])
    if a.get("eyes"):
        parts.append(f'{a["eyes"]} eyes')
    for key in ("facial_hair", "glasses", "distinguishing", "extra"):
        if (a.get(key) or "").strip():
            parts.append(a[key].strip())
    return ", ".join(p for p in parts if p)


def card_of(npc: dict, notes: str = "", body: dict = None) -> dict:
    p, a = npc.get("profile") or {}, npc.get("appearance") or {}
    return {"name": (p.get("first_name") or "").strip(), "age": p.get("age"), "gender": (p.get("gender") or "").strip().lower(),
            "style": npc.get("style") or "photoreal", "look": look_of(a), "bio": (p.get("bio") or "").strip(),
            "occupation": (p.get("occupation") or "").strip(), "notes": notes, "body": bodies.clean(body)}


def frame_of(level: str, frame=None):
    """'full' when a picture asks for the whole body: suggestive and explicit unless the scene says frame 'auto'."""
    if frame in FRAMES:
        return frame
    return "full" if frame != "auto" and level in FRAME_LEVELS else None


def body_words(card: dict, level: str) -> str:
    """What the picture is told about the body: the chosen options (the areolas only at the explicit level) and the notes."""
    return "; ".join(x for x in (bodies.describe(card.get("body"), explicit=level == "explicit"), card.get("notes")) if x)


def build_prompt(card: dict, scene: str, level: str, frame=None) -> dict:
    """The edit prompt. frame: 'full' or None (see frame_of)."""
    who = {"female": "woman", "male": "man"}.get(card.get("gender"), "person")
    style = STYLES.get(card.get("style"), {"edit_name": card.get("style") or "same", "look": ""})
    # Explicit scenes: "everyday clothes for anything it does not mention" kept the reference outfit on in a mirror selfie (a
    # cardigan, a bodysuit), so there nothing is worn unless the scene says so (measured: the selfie came out nude, and the
    # bikini bottoms and the open shirt that scenes name stayed).
    dress = (f"Dress the {who} only as the description says; whatever it does not say the {who} wears, the {who} is not wearing."
             if level == "explicit" else
             f"Dress the {who} only as the description says, and in ordinary everyday clothes for anything it does not mention.")
    parts = [
        f"Show the same {who} from the reference image {scene}.",
        FRAMES.get(frame),
        f"Keep the exact same face, hair, skin tone and body build as in the reference. The {who} is an adult, "
        f"{card.get('age')} years old" + (f", with {card['look']}." if card.get("look") else "."),
        f"Change the clothing completely: nothing the {who} wears in the reference may appear. " + dress,
        style["look"],
        f"Keep the exact same {style['edit_name']} art style.",
    ]
    words = body_words(card, level)
    if words:
        parts.append(f"Body: {words}.")
    parts.append(f"The {who} has {MOOD.get(level, MOOD['off'])}.")
    return {"positive": " ".join(p for p in parts if p), "negative": f"{BASE_NEGATIVE}, {safety.YOUTH_NEGATIVE}"}


# ---- the optional age check (same question and the same pass mark as Altera's) ------------------------------------------------

AGE_PROMPT = (
    "You are an image safety reviewer. Look at the person in this image and estimate how old they appear, judging only by "
    "how they look (face, body, proportions). If there is more than one person, or you are unsure, give the age of the "
    "youngest-looking person. Reply with ONLY this JSON and nothing else: "
    '{"apparent_age": <whole number>, "adult": <true or false>}'
)
_THINK = re.compile(r"<think>.*?</think>", re.S | re.I)
_JSON_OBJECT = re.compile(r"\{[^{}]*\}")


def parse_reply(text):
    text = _THINK.sub("", text or "")
    for candidate in reversed(_JSON_OBJECT.findall(text)):
        try:
            data = json.loads(candidate)
        except ValueError:
            continue
        try:
            age = int(round(float(data.get("apparent_age"))))
        except (TypeError, ValueError):
            age = None
        adult = data.get("adult") if isinstance(data.get("adult"), bool) else None
        if age is not None or adult is not None:
            return age, adult
    return None, None


def judge(age, adult):
    if age is None:
        return False if adult is False else None
    return not (age < safety.MIN_APPARENT_AGE or adult is False)


def post_json(url, payload, api_key="", timeout=240):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key or 'none'}"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


# ---- the studio ---------------------------------------------------------------------------------------------------------------

class Studio:
    def __init__(self, host, root, sync=False, rng=None, post=None):
        """host: what NPC Forge hands an extension (lib, comfy, ...). sync=True runs jobs inline (tests)."""
        self.lib, self.comfy = host.lib, host.comfy
        self.settings_path = Path(root) / "local" / "adult_studio.json"
        self.scenes_path = Path(__file__).parent / "scenes.json"
        self.sync = sync
        self._rng = rng or random.Random()
        self._post = post or post_json
        self._lock = threading.RLock()                       # items.json is read-modify-write
        self._queue = queue.Queue()
        self._thread = None
        self._tlock = threading.Lock()
        self._missing_cache = {}
        self._recover()

    # ---- settings and the 18+ confirmation ----------------------------------
    def settings(self) -> dict:
        data = _read_json(self.settings_path, {}) or {}
        merged = {**DEFAULT_SETTINGS, **{k: v for k, v in data.items() if k in DEFAULT_SETTINGS}}
        merged["age_check"] = {**DEFAULT_SETTINGS["age_check"], **(data.get("age_check") or {})}
        chosen = data.get("engines") if isinstance(data.get("engines"), dict) else {}
        merged["engines"] = {lv: chosen.get(lv) if chosen.get(lv) in engines.ENGINES else engines.DEFAULT_ENGINES[lv] for lv in LEVELS}
        merged["engines"]["portrait"] = chosen.get("portrait") if chosen.get("portrait") in engines.PORTRAIT_ENGINES             else engines.DEFAULT_PORTRAIT_ENGINE
        return merged

    def _save_settings(self, data: dict) -> None:
        _write_json(self.settings_path, data)

    def confirmed(self) -> bool:
        return bool(self.settings().get("confirmed_at"))

    def require_confirmed(self) -> None:
        if not self.confirmed():
            raise Forbidden("Confirm that you are 18 or older first.")

    def confirm(self, confirm_adult) -> dict:
        if confirm_adult is not True:
            raise StudioError("Confirm that you are 18 or older.")
        settings = self.settings()
        settings["confirmed_at"] = settings["confirmed_at"] or _now()
        self._save_settings(settings)
        return self.state()

    def unconfirm(self) -> dict:
        """Switch the adult features off again (the studio tab and the Models setting are hidden). Nothing is deleted."""
        settings = self.settings()
        settings["confirmed_at"] = None
        self._save_settings(settings)
        return self.state()

    def update_settings(self, changes: dict) -> dict:
        self.require_confirmed()
        settings = self.settings()
        if "quality" in changes:
            if changes["quality"] not in QUALITY:
                raise StudioError(f"Unknown quality: {changes['quality']!r}.")
            settings["quality"] = changes["quality"]
        if "age_check_model" in changes:
            settings["age_check"]["model"] = str(changes["age_check_model"] or "").strip()[:120]
        if "engines" in changes:
            if not isinstance(changes["engines"], dict) or not changes["engines"]:
                raise StudioError("Choose an engine for at least one level.")
            for level, engine in changes["engines"].items():
                if level == "portrait":
                    if engine not in engines.PORTRAIT_ENGINES:
                        raise StudioError(f"Unknown main picture engine: {engine!r}.")
                elif level not in LEVELS:
                    raise StudioError(f"Unknown level: {level!r}.")
                elif engine not in engines.ENGINES:
                    raise StudioError(f"Unknown engine: {engine!r}.")
                settings["engines"][level] = engine
        self._save_settings(settings)
        return self.state()

    def scenes(self) -> list:
        data = _read_json(self.scenes_path, {}) or {}
        out = []
        for s in data.get("scenes", []):
            if isinstance(s, dict) and s.get("level") in LEVELS and str(s.get("text", "")).strip() and s.get("id"):
                out.append({"id": str(s["id"]), "level": s["level"], "label": str(s.get("label") or s["id"]),
                            "text": normalize(s["text"], MAX_SCENE), "frame": s.get("frame") if s.get("frame") in ("full", "auto") else None})
        return out

    def state(self) -> dict:
        settings = self.settings()
        return {"enabled": bool(settings["confirmed_at"]), "confirmed_at": settings["confirmed_at"],
                "quality": settings["quality"], "qualities": list(QUALITY),
                "levels": [{"id": lv, "label": LEVEL_LABELS[lv]} for lv in LEVELS],
                "scenes": self.scenes(), "age_check_model": settings["age_check"]["model"],
                "min_age": safety.MIN_AVATAR_AGE, "max_batch": MAX_BATCH, "body_choices": bodies.choices(),
                "engines": {"levels": settings["engines"], "choices": self.engine_choices(),
                            "portrait_choices": self.engine_choices(engines.PORTRAIT_ENGINES)}}

    def engine_choices(self, table=None) -> list:
        """[{id, label, missing}]: missing = the model files ComfyUI lacks for this engine (None = could not ask ComfyUI).
        `table` is engines.ENGINES (the default) or engines.PORTRAIT_ENGINES."""
        table = engines.ENGINES if table is None else table
        out = []
        for engine_id, spec in table.items():
            missing = [] if not spec["workflow"] else self._missing_files(spec["workflow"])
            out.append({"id": engine_id, "label": spec["label"], "missing": missing})
        return out

    def _missing_files(self, workflow, fresh=False):
        """The model files ComfyUI lacks for a workflow file. The page may see an answer up to 30 seconds old; a picture always
        asks fresh (files may just have been installed). None if ComfyUI cannot be asked."""
        cached = self._missing_cache.get(str(workflow))
        if cached and not fresh and time.monotonic() - cached[0] < 30:
            return cached[1]
        try:
            missing = engines.missing_models(self.comfy, engines.load_template(workflow))
        except ComfyError:
            missing = None
        self._missing_cache[str(workflow)] = (time.monotonic(), missing)
        return missing

    # ---- characters ---------------------------------------------------------------------------------------------------
    def _npc(self, npc_id) -> dict:
        try:
            return self.lib.get(npc_id)
        except KeyError:
            raise NotFound("That character was not found.") from None

    def _dir(self, npc_id) -> Path:
        try:
            return self.lib.resolve(npc_id, "adult")
        except KeyError:
            raise NotFound("That character was not found.") from None

    def _portrait_path(self, npc_id) -> Path:
        return self.lib.resolve(npc_id, "portrait.png")

    def problem(self, npc: dict, notes: str = "") -> str:
        """Why this character cannot be used in the studio ('' if it can)."""
        if not self._portrait_path(npc["id"]).is_file():
            return "No portrait yet. Generate one in NPC Forge first."
        return safety.card_problem(card_of(npc, notes)) or ""

    def characters(self) -> list:
        rows = []
        for row in self.lib.list():
            try:
                npc = self.lib.get(row["id"])
            except KeyError:
                continue
            items = self._read_items(row["id"])
            rows.append({"id": row["id"], "name": row["name"], "age": row["age"], "gender": row["gender"], "style": row["style"],
                         "setting": row["setting"], "occupation": row["occupation"], **{k: row.get(k, "") for k in TRAIT_FIELDS},
                         "thumb": row.get("thumb", False), "reason": self.problem(npc, self._notes(row["id"])),
                         "items": len(items), "kept": sum(1 for i in items if i.get("keep") and i.get("status") == "ready")})
            rows[-1]["eligible"] = not rows[-1]["reason"]
        return rows

    def _sigs(self, npc_id):
        try:
            portrait = hashlib.sha1(self._portrait_path(npc_id).read_bytes()).hexdigest()[:10]
        except OSError:
            portrait = ""
        return portrait, self._notes_sig(npc_id)

    @staticmethod
    def _notes_signature(notes, body, body_saved) -> str:
        text = notes + ("|" + bodies.describe(body, explicit=True) if body_saved else "")
        return hashlib.sha1(text.encode()).hexdigest()[:10]

    def _notes_sig(self, npc_id) -> str:
        """Changes when the notes or the body options change (a character nobody touched keeps its old value)."""
        data = self._studio(npc_id)
        notes = data.get("notes", "").strip() if isinstance(data.get("notes"), str) else ""
        body_saved = "body" in data
        body = bodies.clean(data.get("body"), bodies.defaults(npc_id, self._npc(npc_id).get("appearance")))
        return self._notes_signature(notes, body, body_saved)

    def record(self, npc_id) -> dict:
        self.require_confirmed()
        npc = self._npc(npc_id)
        notes = self._notes(npc_id)
        portrait_sig, notes_sig = self._sigs(npc_id)
        items = [self._public(i, portrait_sig, notes_sig) for i in self._read_items(npc_id)]
        p = npc.get("profile") or {}
        return {"npc": {"id": npc_id, "name": f'{p.get("first_name", "")} {p.get("last_name", "")}'.strip(), "age": p.get("age"),
                        "gender": p.get("gender"), "style": npc.get("style")},
                "reason": self.problem(npc, notes), "notes": notes, "items": items,
                "body": self.body_of(npc_id), "body_saved": "body" in self._studio(npc_id),
                "busy": sum(1 for i in items if i["status"] in ("pending", "running"))}

    # ---- body options and notes ---------------------------------------------------------------------------------------
    def _studio_path(self, npc_id) -> Path:
        return self._dir(npc_id) / "studio.json"

    def _studio(self, npc_id) -> dict:
        data = _read_json(self._studio_path(npc_id), {})
        return data if isinstance(data, dict) else {}

    def _notes(self, npc_id) -> str:
        notes = self._studio(npc_id).get("notes")
        return notes.strip() if isinstance(notes, str) else ""

    def body_of(self, npc_id) -> dict:
        """The character's body options: the saved ones, and for anything never chosen the character's own defaults."""
        npc = self._npc(npc_id)
        return bodies.clean(self._studio(npc_id).get("body"), bodies.defaults(npc_id, npc.get("appearance")))

    def _save_studio(self, npc_id, **changes) -> None:
        """Write studio.json: what the studio keeps for a character, plus the words Altera reads (body_text[_explicit])."""
        with self._lock:
            data = self._studio(npc_id)
            data.update(changes)
            data["notes"] = str(data.get("notes") or "").strip()
            chosen = bodies.clean(data.get("body"))
            if chosen:
                data["body_text"] = "; ".join(x for x in (bodies.describe(chosen), data["notes"]) if x)
                data["body_text_explicit"] = bodies.areolas(chosen)
            else:
                data.pop("body_text", None)
                data.pop("body_text_explicit", None)
            data["updated"] = _now()
            _write_json(self._studio_path(npc_id), data)

    def set_notes(self, npc_id, notes) -> dict:
        self.require_confirmed()
        self._npc(npc_id)
        notes = normalize(notes, MAX_NOTES)
        if notes:
            reason = safety.hard_problem(notes)
            if reason:
                raise StudioError(f"Not allowed: {reason[0].lower()}{reason[1:]}")
        self._save_studio(npc_id, notes=notes)
        return self.record(npc_id)

    def set_body(self, npc_id, changes) -> dict:
        """Choose body options ({'bust': 'large', ...}). Anything not named keeps its value."""
        self.require_confirmed()
        self._npc(npc_id)
        if not isinstance(changes, dict) or not changes:
            raise StudioError("Choose at least one option.")
        for field, value in changes.items():
            if field not in bodies.FIELDS:
                raise StudioError(f"Unknown body option: {field!r}.")
            if not bodies.valid(field, value):
                raise StudioError(f"Unknown {bodies.LABELS[field].lower()}: {value!r}.")
        self._save_studio(npc_id, body={**self.body_of(npc_id), **changes})
        return self.record(npc_id)

    def _freeze_body(self, npc_id) -> None:
        """The first picture saves the options the character starts with, so they never change under later pictures."""
        if "body" not in self._studio(npc_id):
            self._save_studio(npc_id, body=self.body_of(npc_id))

    # ---- items --------------------------------------------------------------------------------------------------------
    def _items_path(self, npc_id) -> Path:
        return self._dir(npc_id) / "items.json"

    def _read_items(self, npc_id) -> list:
        data = _read_json(self._items_path(npc_id), {}) or {}
        return [i for i in data.get("items", []) if isinstance(i, dict) and ITEM_ID.match(str(i.get("id", "")))]

    def _write_items(self, npc_id, items) -> None:
        _write_json(self._items_path(npc_id), {"items": items})

    def _update_item(self, npc_id, item_id, **changes):
        with self._lock:
            items = self._read_items(npc_id)
            item = next((i for i in items if i["id"] == item_id), None)
            if not item:
                raise NotFound("That picture was not found.")
            item.update(changes)
            self._write_items(npc_id, items)
            return item

    def _get_item(self, npc_id, item_id) -> dict:
        item = next((i for i in self._read_items(npc_id) if i["id"] == item_id), None)
        if not item:
            raise NotFound("That picture was not found.")
        return item

    @staticmethod
    def _public(item, portrait_sig, notes_sig) -> dict:
        outdated = []
        if item.get("status") == "ready":
            if item.get("portrait_sig") != portrait_sig:
                outdated.append("portrait")
            if item.get("notes_sig") != notes_sig:
                outdated.append("notes")
            if "frame" not in item and item.get("level") in FRAME_LEVELS:      # made before suggestive and explicit were full-length
                outdated.append("frame")
        return {**{k: item.get(k) for k in ("id", "scene", "label", "level", "status", "reason", "keep", "checked",
                                            "apparent_age", "created", "finished", "engine", "engine_note")}, "outdated": outdated}

    def add_items(self, npc_id, scene_ids=(), custom=()) -> list:
        self.require_confirmed()
        npc = self._npc(npc_id)
        problem = self.problem(npc, self._notes(npc_id))
        if problem:
            raise StudioError(problem)
        catalog = {s["id"]: s for s in self.scenes()}
        wanted = []
        for scene_id in scene_ids or []:
            scene = catalog.get(scene_id)
            if not scene:
                raise StudioError(f"Unknown scene: {scene_id}")
            wanted.append((scene["text"], scene["level"], scene["label"], scene["frame"]))
        for c in custom or []:
            text, level = normalize((c or {}).get("text"), MAX_SCENE), (c or {}).get("level")
            if not text:
                raise StudioError("A custom scene needs a description.")
            if level not in LEVELS:
                raise StudioError(f"Unknown level: {level!r}.")
            wanted.append((text, level, text[:40], None))
        if not wanted:
            raise StudioError("Pick at least one scene.")
        if len(wanted) > MAX_BATCH:
            raise StudioError(f"That is {len(wanted)} pictures at once; the most is {MAX_BATCH}.")
        for text, _, _, _ in wanted:
            reason = safety.hard_problem(text)
            if reason:
                raise StudioError(f"{text[:60]!r}: {reason}")

        created = []
        self._freeze_body(npc_id)
        with self._lock:
            items = self._read_items(npc_id)
            for text, level, label, frame in wanted:
                item = {"id": "s-" + uuid.uuid4().hex[:8], "scene": text, "label": label, "level": level, "frame": frame,
                        "status": "pending", "reason": None, "keep": False, "checked": False, "apparent_age": None,
                        "created": _now(), "finished": None, "seed": self._rng.randrange(2 ** 31)}
                items.append(item)
                created.append(item)
            self._write_items(npc_id, items)
        for item in created:
            self._enqueue(npc_id, item["id"])
        portrait_sig, notes_sig = self._sigs(npc_id)
        return [self._public(self._get_item(npc_id, i["id"]), portrait_sig, notes_sig) for i in created]

    def set_keep(self, npc_id, item_id, keep) -> dict:
        self.require_confirmed()
        item = self._get_item(npc_id, item_id)
        if keep and item.get("status") != "ready":
            raise StudioError("Only a finished picture can be kept.")
        self._update_item(npc_id, item_id, keep=bool(keep))
        return self._public_now(npc_id, item_id)

    def _public_now(self, npc_id, item_id):
        portrait_sig, notes_sig = self._sigs(npc_id)
        return self._public(self._get_item(npc_id, item_id), portrait_sig, notes_sig)

    def redo(self, npc_id, item_id) -> dict:
        self.require_confirmed()
        npc = self._npc(npc_id)
        item = self._get_item(npc_id, item_id)
        problem = self.problem(npc, self._notes(npc_id)) or safety.hard_problem(item["scene"])
        if problem:
            raise StudioError(problem)
        self._remove_image(npc_id, item_id)
        self._update_item(npc_id, item_id, status="pending", reason=None, keep=False, checked=False, apparent_age=None,
                          finished=None, frame=item.get("frame"), seed=self._rng.randrange(2 ** 31))
        self._enqueue(npc_id, item_id)
        return self._public_now(npc_id, item_id)

    def redo_outdated(self, npc_id) -> list:
        """Redo every finished picture that was made from an older portrait or older notes."""
        self.require_confirmed()
        return [self.redo(npc_id, i["id"]) for i in self.record(npc_id)["items"] if i["outdated"]]

    def delete_item(self, npc_id, item_id) -> None:
        self.require_confirmed()
        with self._lock:
            items = self._read_items(npc_id)
            if not any(i["id"] == item_id for i in items):
                raise NotFound("That picture was not found.")
            self._write_items(npc_id, [i for i in items if i["id"] != item_id])
        self._remove_image(npc_id, item_id)

    def _image(self, npc_id, item_id) -> Path:
        return self._dir(npc_id) / "img" / f"{item_id}.png"

    def _remove_image(self, npc_id, item_id) -> None:
        try:
            self._image(npc_id, item_id).unlink()
        except OSError:
            pass

    def image_path(self, npc_id, item_id):
        self.require_confirmed()
        if not ITEM_ID.match(item_id or ""):
            return None
        item = next((i for i in self._read_items(npc_id) if i["id"] == item_id), None)
        path = self._image(npc_id, item_id)
        return path if item and item.get("status") == "ready" and path.is_file() else None

    # ---- the job ------------------------------------------------------------------------------------------------------
    def _recover(self) -> None:
        """Jobs left running by a previous run can never finish."""
        try:
            folders = [d for d in self.lib.root.iterdir() if (d / "adult" / "items.json").is_file()]
        except OSError:
            return
        for folder in folders:
            with self._lock:
                items = self._read_items(folder.name)
                changed = False
                for i in items:
                    if i.get("status") in ("pending", "running"):
                        i.update(status="failed", reason="Interrupted when NPC Forge was restarted.", finished=_now())
                        changed = True
                if changed:
                    self._write_items(folder.name, items)

    def _enqueue(self, npc_id, item_id) -> None:
        if self.sync:
            return self._process(npc_id, item_id)
        with self._tlock:
            self._queue.put((npc_id, item_id))
            if self._thread is None:
                self._thread = threading.Thread(target=self._worker, name="adult-studio", daemon=True)
                self._thread.start()

    def _worker(self) -> None:
        while True:
            try:
                npc_id, item_id = self._queue.get(timeout=30)
            except queue.Empty:
                with self._tlock:
                    if self._queue.empty():
                        self._thread = None
                        return
                continue
            try:
                self._process(npc_id, item_id)
            except Exception:
                traceback.print_exc()

    def _finish(self, npc_id, item_id, status, reason=None, **extra):
        return self._update_item(npc_id, item_id, status=status, reason=reason, finished=_now(), **extra)

    def _process(self, npc_id, item_id) -> None:
        try:
            item = self._get_item(npc_id, item_id)
            if item.get("status") != "pending":
                return
            self._update_item(npc_id, item_id, status="running")
            npc = self._npc(npc_id)
            # Snapshot every prompt input before rendering. If the portrait, notes, or body changes while the GPU is busy,
            # the finished image keeps these signatures and is correctly shown as outdated.
            portrait = self._portrait_path(npc_id).read_bytes()
            portrait_sig = hashlib.sha1(portrait).hexdigest()[:10]
            with self._lock:
                studio_data = self._studio(npc_id)
                notes = studio_data.get("notes", "").strip() if isinstance(studio_data.get("notes"), str) else ""
                body_saved = "body" in studio_data
                body = bodies.clean(studio_data.get("body"), bodies.defaults(npc_id, npc.get("appearance")))
                notes_sig = self._notes_signature(notes, body, body_saved)
            problem = self.problem(npc, notes) or safety.hard_problem(item["scene"])
            if problem:
                return self._finish(npc_id, item_id, "blocked", problem)
            settings = self.settings()
            frame = frame_of(item["level"], item.get("frame"))
            width, height, steps = (FULL_LENGTH if frame else QUALITY).get(settings["quality"], QUALITY["standard"])
            prompt = build_prompt(card_of(npc, notes, body), item["scene"], item["level"], frame)
            engine, note = self._pick_engine(item["level"], settings)
            png = None
            if engine != "mage":
                try:
                    png = self._render_qwen(portrait, prompt, item["seed"], frame, settings, engine)
                except engines.EngineError as e:
                    engine, note = "mage", f"Made with Mage-Flow-Edit instead of {engines.ENGINES[engine]['short']}: {e}"
            if png is None:
                reference = self.comfy.upload_image(portrait, "adult_studio_ref.png")
                graph = edit_graph(self.comfy.models, reference, prompt["positive"], item["seed"], steps=steps,
                                   cfg=float(settings["cfg"]), megapixels=1.0, size=(width, height), negative=prompt["negative"])
                png = self.comfy.run(graph)
        except NotFound:
            return
        except ComfyError as e:
            return self._safe_finish(npc_id, item_id, "failed", str(e))
        except Exception:
            traceback.print_exc()
            return self._safe_finish(npc_id, item_id, "failed", "Something went wrong while making the picture.")

        try:
            check = self._age_check(png)
            if check["ok"] is False:
                return self._safe_finish(npc_id, item_id, "blocked", f"The age check rejected this picture (it reads as about "
                                                                     f"{check['apparent_age']}, under {safety.MIN_APPARENT_AGE}). Discarded.")
            self.lib.write_asset(npc_id, f"adult/img/{item_id}.png", png)
            self._safe_finish(npc_id, item_id, "ready", checked=check["ok"] is True, apparent_age=check.get("apparent_age"),
                              portrait_sig=portrait_sig, notes_sig=notes_sig, prompt=prompt["positive"], engine=engine, engine_note=note)
        except NotFound:
            self._remove_image(npc_id, item_id)
        except Exception:
            traceback.print_exc()
            self._remove_image(npc_id, item_id)
            return self._safe_finish(npc_id, item_id, "failed", "Something went wrong while saving or checking the picture.")

    def _pick_engine(self, level, settings):
        """(engine, note): the engine chosen for this level, or Mage-Flow with the reason when the chosen one cannot run."""
        choice = settings["engines"].get(level)
        spec = engines.ENGINES.get(choice)
        if not spec or not spec["workflow"]:
            return "mage", None
        missing = self._missing_files(spec["workflow"], fresh=True)
        if missing:
            return "mage", f"Made with Mage-Flow-Edit instead of {spec['short']}: ComfyUI lacks " + ", ".join(missing) + "."
        return choice, None

    def _render_qwen(self, portrait: bytes, prompt: dict, seed, frame, settings, engine="qwen") -> bytes:
        """One picture from a Qwen workflow, the careful way (engines.run_guarded). Raises engines.EngineError on trouble."""
        template = engines.load_template(engines.ENGINES[engine]["workflow"])
        sizes = FULL_LENGTH if frame else QUALITY
        width, height, steps = sizes.get(settings["quality"], sizes["standard"])
        if width * height > 1_100_000:                                                  # Qwen is measured at about one megapixel
            width, height, steps = sizes["standard"]
        reference = self.comfy.upload_image(portrait, "adult_studio_ref.png")
        graph = engines.fill_workflow(template, {"prompt": prompt["positive"], "negative": prompt["negative"], "seed": int(seed),
                                                 "width": width, "height": height, "steps": steps,
                                                 "reference": reference, "reference_2": reference})
        return engines.run_guarded(self.comfy, graph)

    def _safe_finish(self, npc_id, item_id, status, reason=None, **extra):
        try:
            return self._finish(npc_id, item_id, status, reason, **extra)
        except NotFound:                                     # deleted while it was being made
            self._remove_image(npc_id, item_id)

    def _age_check(self, png: bytes) -> dict:
        cfg = self.settings()["age_check"]
        model = (cfg.get("model") or "").strip()
        if not model:
            return {"ok": None, "apparent_age": None, "model": None, "error": "No age-check model is set in the studio."}
        payload = {"model": model, "temperature": 0.3, "max_tokens": 4096, "messages": [{"role": "user", "content": [
            {"type": "text", "text": AGE_PROMPT},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(png).decode("ascii")}}]}]}
        try:
            reply = self._post(cfg["base_url"].rstrip("/") + "/chat/completions", payload, cfg.get("api_key", ""))
            message = reply["choices"][0]["message"]
            text = message.get("content") or message.get("reasoning_content") or ""
        except Exception as e:
            return {"ok": None, "apparent_age": None, "model": model, "error": f"The vision model did not answer ({type(e).__name__})."}
        age, adult = parse_reply(text)
        ok = judge(age, adult)
        return {"ok": ok, "apparent_age": age, "model": model,
                "error": None if ok is not None else "The vision model gave no usable age estimate."}
