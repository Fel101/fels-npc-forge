"""Filesystem library: one folder per NPC (npc.json, image and 3D assets), thumbnails and ZIP export."""
import copy
import datetime
import io
import json
import re
import secrets
import shutil
import threading
import time
import uuid
import zipfile

from PIL import Image

from . import atomic, licences, media

ID_RE = re.compile(r"^[a-z0-9][a-z0-9\-]{1,80}$")
TRASH = ".trash"          # where a deleted character waits, inside the library folder (not an NPC folder: it has no npc.json)
TRASH_FOLDER_RE = re.compile(r"^[a-z0-9][a-z0-9\-]{1,80}--\d{8}-\d{6}(?:-\d+)?$")      # <id>--<time>[-n], what `delete` makes

# The pictures that keep their earlier versions, named the way the API names them. A picture that is drawn again is not lost: the one it
# replaces is kept next to it (assets.candidates[slot], files under candidates/), and the page lists it with Restore and Delete.
SLOT_RE = re.compile(r"^(?:portrait|sprite|expressions/[a-z0-9_-]{1,40}|sheet/[a-z0-9_]{1,24})$")
HISTORY_KEEP = 6          # earlier versions kept per picture (config.json "library": {"picture_history": N}; 0 keeps none)
HISTORY_THUMB = 192       # px: the small copy the page shows in the list of earlier versions

# A 3D model that is replaced (by another generator's, an import, a designed body, a file the owner uploads) or deleted is not lost either: it is kept with its skeleton and
# everything else it owns in the character's folder, in model_history/<id>/ (assets.model_history), and the page lists it with Restore and Delete.
MODEL_PREFIX = "model_3d"           # what a 3D model owns in its character's folder starts with this: the model, its original, its picture, its skeleton file, its board, its head close-ups
MODEL_HISTORY = "model_history"
MODEL_HISTORY_KEEP = 2              # earlier 3D models kept per character (config.json "library": {"model_history": N}; 0 keeps none): a picture-made one takes 60 to 140 MB
MODEL_ENTRY_RE = re.compile(r"^[a-f0-9]{12}$")


def slot_files(slot):
    """Where the active picture of a slot is written: (the picture, its un-cut-out original), e.g. ("portrait.png", "portrait_raw.png")."""
    if slot in ("portrait", "sprite"):
        return f"{slot}.png", f"{slot}_raw.png"
    group, _, name = slot.partition("/")
    if group == "expressions":
        return f"expressions/{name}.png", f"expressions/{name}_raw.png"
    return f"sheet/{name}.png", f"sheet/raw/{name}.png"


def picture_meta(assets, slot):
    """The record of the picture at `slot` in an NPC's assets, or None."""
    group, _, name = slot.partition("/")
    if not name:
        return assets.get(group)
    if group == "expressions":
        return (assets.get("expressions") or {}).get(name)
    return ((assets.get("sheet") or {}).get("views") or {}).get(name)


def _export_pictures(assets):
    """Model labels for legacy pictures in a character ZIP."""
    files = {}
    for kind in ("portrait", "sprite"):
        if meta := assets.get(kind):
            files[meta["file"]] = [licences.picture_engine(meta)]
    for family in (assets.get("expressions", {}), (assets.get("head_3d") or {}).get("views", {})):
        for meta in family.values():
            if meta.get("file"):
                files[meta["file"]] = [licences.picture_engine(meta, edit=True)]
    sheet = assets.get("sheet") or {}
    sheet_models = []
    for meta in sheet.get("views", {}).values():
        engine = licences.picture_engine(meta, edit=True)
        files[meta["file"]] = [engine]
        sheet_models.append(engine)
    if sheet.get("file"):
        files[sheet["file"]] = [] if sheet.get("stale") else sheet_models
    for clip in assets.get("animations", {}).values():
        for direction, frames in clip.get("files", {}).items():
            for rel in frames:
                files[rel] = licences.animation_engines(clip, direction)
    return files


def set_picture_meta(assets, slot, meta):
    group, _, name = slot.partition("/")
    if not name:
        assets[group] = meta
    elif group == "expressions":
        assets.setdefault("expressions", {})[name] = meta
    else:
        assets.setdefault("sheet", {}).setdefault("views", {})[name] = meta


def history_folder(slot):
    """Where a slot's earlier versions are kept: candidates/portrait, candidates/expressions_happy, candidates/sheet_front..."""
    return "candidates/" + slot.replace("/", "_")

# Hair colour families for the Library filter. The first colour a description names decides ("black hair with blue tips" -> Black).
_HAIR_FAMILIES = (
    ("Blonde", ("blonde", "blond")),
    ("Brown", ("brown", "brunette", "chestnut", "chocolate")),
    ("Black", ("black",)),
    ("Red", ("red", "auburn", "ginger", "copper", "burgundy")),
    ("Gray / white", ("gray", "grey", "silver", "white", "salt-and-pepper", "salt and pepper")),
    ("Dyed / fantasy", ("pink", "blue", "green", "teal", "lavender", "purple", "violet", "turquoise", "rose gold")),
    ("Bald", ("bald", "shaved head")),
)
_HAIR_RES = [(family, re.compile(r"\b(?:" + "|".join(re.escape(w) for w in words) + r")\b", re.I)) for family, words in _HAIR_FAMILIES]


# Broad ethnicity families for the Library filter, from the heritage label. The order matters: "Southeast Asian" contains "east asian"
# and "Middle Eastern / North African" contains "african". A fantasy race (Dwarf, Half-orc) belongs to none.
_ETHNICITY_FAMILIES = (
    ("Southeast Asian", ("southeast asian", "south-east asian", "vietnamese", "thai", "filipino", "indonesian", "malay")),
    ("East Asian", ("east asian", "japanese", "korean", "chinese", "taiwanese", "mongolian")),
    ("South Asian", ("south asian", "indian", "pakistani", "bangladeshi", "sri lankan", "nepali")),
    ("Middle Eastern / North African", ("middle eastern", "north african", "arab", "persian", "turkish", "iranian")),
    ("Black / African", ("black", "african")),
    ("Latina / Hispanic", ("latina", "latino", "hispanic", "brazilian", "mexican")),
    ("White / European", ("caucasian", "european", "scandinavian", "mediterranean", "nordic", "slavic", "white")),
    ("Mixed", ("mixed", "biracial", "multiracial")),
)


def ethnicity(heritage):
    """The broad family of a heritage label ("Korean" -> "East Asian", "Latina" -> "Latina / Hispanic"), or "" (a fantasy race, unknown)."""
    text = (heritage or "").lower()
    for family, words in _ETHNICITY_FAMILIES:
        if any(w in text for w in words):
            return family
    return ""


def hair_color(text):
    """The colour family of a hair description ("dark brown wavy long hair" -> "Brown"), or "" when it names none."""
    best = None
    for family, pattern in _HAIR_RES:
        found = pattern.search(text or "")
        if found and (best is None or found.start() < best[0]):
            best = (found.start(), family)
    return best[1] if best else ""


def now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def voice_status(npc):
    """For the Library's Voice filter: "picked" (a voice you chose), "review" (takes waiting for you; the first take is used
    until then), or "none"."""
    v = npc.get("voice") or {}
    if v.get("lab_voice") or v.get("reference") or v.get("preset_identity"):
        return "review" if v.get("review") == "pending" else "picked"
    return "review" if v.get("takes") else "none"


class Library:
    def __init__(self, root):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self._index = {}
        self._ordered = None
        self._rated = None                        # the licence list's stamp the rows were rated against
        self.history_keep = HISTORY_KEEP         # (the app sets it from config.json)
        self.model_history_keep = MODEL_HISTORY_KEEP     # (and this one)
        self._trash_rows = {}                     # trash folder name -> its row: a folder in the trash never changes, so it is read once
        self.refresh_index()

    def _summary(self, npc):
        """The library-card projection of an NPC. Kept in memory so polling never reparses every npc.json."""
        p, a, look = npc.get("profile", {}), npc.get("assets", {}), npc.get("appearance", {})
        folder = self._dir(npc["id"])
        custom = npc.get("custom") if isinstance(npc.get("custom"), dict) else {}
        return {
            "id": npc["id"], "name": f'{p.get("first_name", "")} {p.get("last_name", "")}'.strip(),
            # a project (a game) tags what it makes with custom.project, and a scratch character with custom.test: the list
            # can then be asked for one project's characters, or without the tests (GET /api/npcs?project=...&test=0)
            "project": str(custom.get("project") or ""), "test": bool(custom.get("test")),
            "age": p.get("age"), "gender": p.get("gender"), "occupation": p.get("occupation"),
            "setting": npc.get("setting"), "style": npc.get("style"), "updated": npc.get("updated"),
            "heritage": p.get("heritage") or p.get("race") or "", "ethnicity": ethnicity(p.get("heritage")),
            "hair": look.get("hair") or "", "hair_color": hair_color(look.get("hair")), "eyes": look.get("eyes") or "",
            "build": look.get("build") or "", "skin_tone": look.get("skin_tone") or "",
            "has": {"portrait": "portrait" in a, "sprite": "sprite" in a, "model_3d": "model_3d" in a, "rig_3d": "rig_3d" in a,
                    "designed": bool((a.get("model_3d") or {}).get("designed")),
                    "head_3d": bool(a.get("head_3d", {}).get("file")),
                    "expressions": len(a.get("expressions", {})),
                    "sheet": "sheet" in a, "living": "living" in a and not a["living"].get("stale"),
                    "voice_lines": len(npc.get("voice_lines") or {})},
            "voice": voice_status(npc),
            # whether what made it may be used commercially (npcgen/licences.py): yes, conditions, unclear, no, or none (nothing made yet)
            "commercial": self._rating(npc),
            "thumb": (folder / "thumb.png").exists(),
        }

    @staticmethod
    def _rating(npc):
        """A character's commercial-use rating. A licence list that cannot be read rates it "unclear": it must not hide a character."""
        try:
            return licences.verdict(npc)
        except (OSError, ValueError, KeyError):
            return "unclear"

    def refresh_index(self):
        """Rescan disk explicitly (startup/import recovery). Normal writes update the index incrementally."""
        with self.lock:
            self._rated = licences.stamp()
            fresh = {}
            for folder in self.root.iterdir():
                path = folder / "npc.json"
                if not path.exists():
                    continue
                try:
                    npc = json.loads(atomic.read_text(path))
                    fresh[npc["id"]] = self._summary(npc)
                except (KeyError, ValueError, TypeError, AttributeError, UnicodeError, OSError):
                    continue
            self._index = fresh
            self._ordered = None
            return self.list()

    def _index_npc(self, npc):
        self._index[npc["id"]] = self._summary(npc)
        self._ordered = None

    # ---- paths ---------------------------------------------------------------------------------------------
    def _dir(self, npc_id):
        if not ID_RE.match(npc_id or ""):
            raise KeyError(npc_id)
        return self.root / npc_id

    def resolve(self, npc_id, rel):
        """Absolute path of a file inside an NPC folder; refuses anything that escapes it."""
        base = self._dir(npc_id).resolve()
        path = (base / str(rel).replace("\\", "/")).resolve()    # (a backslash is a separator on every system, as on Windows)
        if not path.is_relative_to(base):
            raise KeyError(rel)
        return path

    # ---- npc records -----------------------------------------------------------------------------------------
    def new_id(self, npc):
        p = npc["profile"]
        base = re.sub(r"[^a-z0-9]+", "-", f'{p.get("first_name", "")}-{p.get("last_name", "")}'.lower()).strip("-") or "npc"
        return f"{base[:40]}-{secrets.token_hex(3)}"

    def create(self, npc):
        with self.lock:
            npc = dict(npc)
            npc["id"] = self.new_id(npc)
            npc["created"] = npc["updated"] = now()
            npc.setdefault("assets", {})
            self._dir(npc["id"]).mkdir(parents=True)
            self._write(npc)
            self._index_npc(npc)
            return npc

    def get(self, npc_id):
        path = self._dir(npc_id) / "npc.json"
        if not path.exists():
            raise KeyError(npc_id)
        return json.loads(atomic.read_text(path))

    def save(self, npc):
        with self.lock:
            npc["updated"] = now()
            self._write(npc)
            self._index_npc(npc)
            return npc

    def update(self, npc_id, fn):
        """Read-modify-write under the lock, so the worker thread and HTTP threads never clobber each other."""
        with self.lock:
            npc = self.get(npc_id)
            fn(npc)
            return self.save(npc)

    def _write(self, npc):
        path = self._dir(npc["id"]) / "npc.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(npc, indent=2, ensure_ascii=False), "utf-8")
        atomic.replace(tmp, path)

    def delete(self, npc_id, trash=True):
        """Move a character into the library's trash (`.trash/<id>--<time>`): `restore` brings it back, `purge_trash` removes
        it for good once it has been there long enough. A rename on the same drive, so a character is never half deleted.
        `trash=False` removes it at once (for a character that was made a moment ago by a request that then failed)."""
        with self.lock:
            source = self._dir(npc_id)
            if not source.is_dir():
                raise FileNotFoundError(npc_id)
            if not trash:
                shutil.rmtree(source)
                self._index.pop(npc_id, None)
                self._ordered = None
                return
            trash = self.root / TRASH
            trash.mkdir(exist_ok=True)
            stamp = time.strftime("%Y%m%d-%H%M%S")
            target, n = trash / f"{npc_id}--{stamp}", 1
            while target.exists():
                target, n = trash / f"{npc_id}--{stamp}-{n}", n + 1
            atomic.replace(source, target)
            self._index.pop(npc_id, None)
            self._ordered = None

    @staticmethod
    def _trash_row(folder):
        npc_id, _, stamp = folder.name.partition("--")
        try:
            npc = json.loads(atomic.read_text(folder / "npc.json"))
            when = datetime.datetime.strptime(stamp[:15], "%Y%m%d-%H%M%S")
        except (OSError, ValueError, TypeError, UnicodeError):
            return None
        p, a = npc.get("profile") or {}, npc.get("assets") or {}
        return {"id": npc.get("id") or npc_id, "name": f'{p.get("first_name", "")} {p.get("last_name", "")}'.strip(),
                "deleted": when.isoformat(timespec="seconds"), "folder": folder.name,
                "thumb": (folder / "thumb.png").is_file(), "age": p.get("age"), "occupation": p.get("occupation"),
                "setting": npc.get("setting"), "style": npc.get("style"),
                "has": {"portrait": "portrait" in a, "sprite": "sprite" in a, "model_3d": "model_3d" in a,
                        "expressions": len(a.get("expressions") or {})}}

    def trashed(self):
        """The characters in the trash, newest first: [{id, name, deleted (ISO time), folder, thumb, age, occupation, setting, style, has}].
        A folder in the trash never changes, so each is read once and remembered by its name."""
        with self.lock:
            trash = self.root / TRASH
            rows, seen = [], set()
            for folder in (trash.iterdir() if trash.is_dir() else ()):
                row = self._trash_rows.get(folder.name)
                if row is None:
                    row = self._trash_row(folder)
                    if row is None:
                        continue
                    self._trash_rows[folder.name] = row
                seen.add(folder.name)
                rows.append(dict(row, has=dict(row["has"])))
            for gone in set(self._trash_rows) - seen:
                del self._trash_rows[gone]
            return sorted(rows, key=lambda r: (r["deleted"], r["folder"]), reverse=True)

    def trash_file(self, folder, rel="thumb.png"):
        """The path of a file inside a trashed character's folder (only its small picture is ever shown). KeyError: not there."""
        if not TRASH_FOLDER_RE.match(folder or "") or rel != "thumb.png":
            raise KeyError(folder)
        path = self.root / TRASH / folder / rel
        if not path.is_file():
            raise KeyError(folder)
        return path

    def restore(self, npc_id):
        """Bring the newest trashed copy of a character back, and return its library card. KeyError: none in the trash;
        ValueError: a character with that id is in the library."""
        with self.lock:
            copies = [row for row in self.trashed() if row["id"] == npc_id]
            if not copies:
                raise KeyError(npc_id)
            target = self._dir(npc_id)
            if target.exists():
                raise ValueError(f"There is already a character with the id {npc_id}")
            atomic.replace(self.root / TRASH / copies[0]["folder"], target)
            self._trash_rows.pop(copies[0]["folder"], None)
            self._index_npc(self.get(npc_id))
            return copy.deepcopy(self._index[npc_id])

    def purge_trash(self, days=30):
        """Remove what has been in the trash for more than `days` days (0 or less: keep everything). The count removed."""
        if days is None or days <= 0:
            return 0
        limit = datetime.datetime.now() - datetime.timedelta(days=days)
        removed = 0
        with self.lock:
            for row in self.trashed():
                if datetime.datetime.fromisoformat(row["deleted"]) < limit:
                    shutil.rmtree(self.root / TRASH / row["folder"], ignore_errors=True)
                    self._trash_rows.pop(row["folder"], None)
                    removed += 1
        return removed

    def purge_trashed(self, folder):
        """Remove one trashed character for good (the `folder` its row names). KeyError: it is not in the trash."""
        with self.lock:
            if not TRASH_FOLDER_RE.match(folder or "") or not (self.root / TRASH / folder).is_dir():
                raise KeyError(folder)
            shutil.rmtree(self.root / TRASH / folder, ignore_errors=True)
            self._trash_rows.pop(folder, None)

    def empty_trash(self):
        """Remove everything in the trash for good. The count of characters removed."""
        with self.lock:
            rows = self.trashed()
            for row in rows:
                shutil.rmtree(self.root / TRASH / row["folder"], ignore_errors=True)
                self._trash_rows.pop(row["folder"], None)
            return len(rows)

    def list(self):
        with self.lock:
            if self._rated != licences.stamp():       # the licence list was edited since: rate every character against the new one
                return self.refresh_index()
            if self._ordered is None:
                self._ordered = sorted(self._index.values(), key=lambda n: n["updated"] or "", reverse=True)
            return copy.deepcopy(self._ordered)

    # ---- assets --------------------------------------------------------------------------------------------------
    def write_asset(self, npc_id, rel, data):
        path = self.resolve(npc_id, rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes(data)
        atomic.replace(tmp, path)
        return rel

    def read_asset(self, npc_id, rel):
        return atomic.read_bytes(self.resolve(npc_id, rel))

    def remove_asset_files(self, npc_id, meta):
        for rel in (meta.get("file"), meta.get("raw"), meta.get("preview")):
            if rel:
                try:
                    self.resolve(npc_id, rel).unlink()
                except (FileNotFoundError, KeyError):
                    pass

    def delete_asset(self, npc_id, kind, key=None):
        def fn(npc):
            assets = npc.setdefault("assets", {})
            if kind == "animations":                       # a whole clip: its folder holds raw and aligned frames
                if assets.get("animations", {}).pop(key, None) is None:
                    raise KeyError(kind)
                shutil.rmtree(self.resolve(npc_id, f"animations/{key}"), ignore_errors=True)
                return
            if kind == "sheet":                            # all views: the folder holds the raw and aligned panels and the sheet
                if assets.pop("sheet", None) is None:
                    raise KeyError(kind)
                shutil.rmtree(self.resolve(npc_id, "sheet"), ignore_errors=True)
                self._drop_history(npc_id, assets, "sheet/")      # (a deleted sheet takes the views' earlier versions with it)
                return
            if kind == "head_3d":                          # the detailed head: its model, original, preview and the four close-ups
                if assets.pop("head_3d", None) is None:
                    raise KeyError(kind)
                for rel in ("head_3d.glb", "head_3d_original.glb", "head_3d.png"):
                    try:
                        self.resolve(npc_id, rel).unlink()
                    except (FileNotFoundError, KeyError):
                        pass
                shutil.rmtree(self.resolve(npc_id, "head_3d"), ignore_errors=True)
                return
            if kind == "living":                           # the assembled frames; the expressions they came from stay
                if assets.pop("living", None) is None:
                    raise KeyError(kind)
                shutil.rmtree(self.resolve(npc_id, "living"), ignore_errors=True)
                return
            if kind == "voice":                            # the voice, its takes and tests, and every line spoken in it
                if npc.pop("voice", None) is None:
                    raise KeyError(kind)
                npc.pop("voice_lines", None)
                shutil.rmtree(self.resolve(npc_id, "voice"), ignore_errors=True)
                return
            if kind == "voice_lines":                      # one voiced dialogue line
                meta = (npc.get("voice_lines") or {}).pop(key, None)
                if meta is None:
                    raise KeyError(key)
                self.remove_asset_files(npc_id, meta)
                return
            if kind == "model_3d" and assets.get("model_3d"):
                self.archive_model_into(npc, npc_id)         # (deleted by mistake, it can be brought back from the earlier models)
            meta = (assets.get("expressions", {}).pop(key, None) if kind == "expressions" else assets.pop(kind, None))
            if meta is None:
                raise KeyError(kind)
            self.remove_asset_files(npc_id, meta)
            if kind == "model_3d":                         # the sharper face's head close-ups (npcgen/headdetail.py)
                shutil.rmtree(self.resolve(npc_id, "model_3d_head"), ignore_errors=True)
            if kind == "model_3d" and "rig_3d" in assets:     # the skeleton and clips were made for this model
                self.remove_asset_files(npc_id, assets.pop("rig_3d"))
        npc = self.update(npc_id, fn)
        self.make_thumb(npc_id)
        return npc

    # ---- earlier versions of a picture ---------------------------------------------------------------------------------
    @staticmethod
    def _thumbnail(data, models=None):
        """The small picture the page lists an earlier version by (PNG bytes), or None when the image cannot be read."""
        try:
            with Image.open(io.BytesIO(data)) as im:
                im = im.convert("RGBA")
                im.thumbnail((HISTORY_THUMB, HISTORY_THUMB), Image.LANCZOS)
                return media.png(im, models=models, optimize=True)
        except (OSError, ValueError):
            return None

    def _remove_version_files(self, npc_id, entry):
        for rel in (entry.get("file"), entry.get("raw"), entry.get("thumb")):
            if rel:
                try:
                    self.resolve(npc_id, rel).unlink()
                except (FileNotFoundError, KeyError):
                    pass

    def _drop_history(self, npc_id, assets, prefix):
        """Remove the earlier versions (and their files) of every picture whose slot starts with `prefix`."""
        candidates = assets.get("candidates") or {}
        for slot in [s for s in candidates if s.startswith(prefix)]:
            for entry in candidates.pop(slot):
                self._remove_version_files(npc_id, entry)
        if not candidates:
            assets.pop("candidates", None)

    def _archive(self, npc, npc_id, slot, auto):
        """Keep the picture now at `slot` among its earlier versions, because something is about to take its place. `npc` is the record
        being updated (the caller holds the lock and saves it). `auto` is the redraw case: it honours history_keep and drops the oldest
        beyond it; a restore swaps the two pictures instead, so it always keeps. The new entry, or None when there is nothing to keep (no
        picture, its file is gone, or it already is a copy of an earlier version that is still listed)."""
        assets = npc.setdefault("assets", {})
        current = picture_meta(assets, slot)
        if not current or not current.get("file") or (auto and self.history_keep <= 0):
            return None
        entries = assets.get("candidates", {}).get(slot, [])
        if current.get("candidate_id") and any(c.get("id") == current["candidate_id"] for c in entries):
            return None
        try:
            data = self.read_asset(npc_id, current["file"])
        except (OSError, KeyError):
            return None
        entry_id, folder = uuid.uuid4().hex[:12], history_folder(slot)
        entry = {k: v for k, v in current.items() if k not in ("stale", "candidate_id", "id", "raw", "thumb", "replaced")}
        entry.update(id=entry_id, file=f"{folder}/{entry_id}.png", replaced=now())
        models = [licences.picture_engine(current, edit=slot not in ("portrait", "sprite"))]
        self.write_asset(npc_id, entry["file"], media.label(data, models))
        if current.get("raw"):
            try:
                raw = self.read_asset(npc_id, current["raw"])
            except (OSError, KeyError):
                raw = None
            if raw is not None:
                entry["raw"] = f"{folder}/{entry_id}_raw.png"
                self.write_asset(npc_id, entry["raw"], media.label(raw, models))
        small = self._thumbnail(data, models)
        if small:
            entry["thumb"] = f"{folder}/{entry_id}_t.png"
            self.write_asset(npc_id, entry["thumb"], small)
        entries = assets.setdefault("candidates", {}).setdefault(slot, [])
        entries.append(entry)
        if auto:                                           # (candidates a person chose to save have no 'replaced' and are never dropped)
            kept = [c for c in entries if c.get("replaced")]
            for old in kept[:max(len(kept) - self.history_keep, 0)]:
                entries.remove(old)
                self._remove_version_files(npc_id, old)
        return entry

    def archive_picture(self, npc_id, slot):
        """Call before a picture is drawn again: the one it replaces is kept, the page lists it under the new one with Restore and Delete.
        The new entry, or None (nothing to keep, or the history is off)."""
        if not SLOT_RE.match(slot or "") or self.history_keep <= 0:
            return None
        with self.lock:
            if not (picture_meta(self.get(npc_id).get("assets", {}), slot) or {}).get("file"):
                return None
            kept = []
            self.update(npc_id, lambda npc: kept.append(self._archive(npc, npc_id, slot, auto=True)))
            return kept[0]

    def select_asset_candidate(self, npc_id, kind, candidate_id):
        """Make an earlier version of a picture the active one (`kind` is its slot: portrait, sprite, expressions/<id>, sheet/<view>).
        The picture it replaces is kept in turn, so nothing is lost and either can be brought back."""
        if not SLOT_RE.match(kind or ""):
            raise KeyError(kind)

        def apply(npc):
            assets = npc.setdefault("assets", {})
            candidates = assets.get("candidates", {}).get(kind, [])
            candidate = next((c for c in candidates if c.get("id") == candidate_id), None)
            if candidate is None:
                raise KeyError(candidate_id)

            current = picture_meta(assets, kind)
            if current and current.get("candidate_id") != candidate_id:
                self._archive(npc, npc_id, kind, auto=False)       # (a picture that is itself a copy of a listed version is already kept)

            file, raw = (current or {}).get("file") or slot_files(kind)[0], slot_files(kind)[1]
            models = [licences.picture_engine(candidate, edit=kind not in ("portrait", "sprite"))]
            self.write_asset(npc_id, file, media.label(self.read_asset(npc_id, candidate["file"]), models))
            selected = {k: v for k, v in candidate.items() if k not in ("id", "thumb", "replaced")}
            selected.update(candidate_id=candidate_id, file=file)
            if candidate.get("raw"):
                raw = (current or {}).get("raw") or raw
                self.write_asset(npc_id, raw, media.label(self.read_asset(npc_id, candidate["raw"]), models))
                selected["raw"] = raw
            else:
                selected.pop("raw", None)
                self.resolve(npc_id, (current or {}).get("raw") or raw).unlink(missing_ok=True)
            set_picture_meta(assets, kind, selected)
            group = kind.partition("/")[0]

            style_changed = bool(candidate.get("style") and candidate["style"] != npc.get("style")) and group in ("portrait", "sprite")
            candidate_stale = bool(candidate.get("stale"))
            if group in ("portrait", "sprite") and candidate.get("style"):
                npc["style"] = candidate["style"]
            if style_changed:
                from .jobs import mark_assets_stale
                mark_assets_stale(npc)
                if not candidate_stale:
                    assets[kind].pop("stale", None)
            elif group == "portrait":
                if assets.get("sprite", {}).get("mode") == "match":
                    assets["sprite"]["stale"] = True
                    for kind3d in ("model_3d", "rig_3d"):
                        if kind3d in assets:
                            assets[kind3d]["stale"] = True
                if "head_3d" in assets:                     # its close-ups were drawn from the old portrait
                    assets["head_3d"]["stale"] = True
                for meta in assets.get("expressions", {}).values():
                    meta["stale"] = True
                if "sheet" in assets:
                    assets["sheet"]["stale"] = True
                    for view in assets["sheet"].get("views", {}).values():
                        view["stale"] = True
            elif group == "sprite":
                for meta in assets.get("animation_views", {}).values():
                    meta["stale"] = True
                for meta in assets.get("animations", {}).values():
                    meta["stale"] = True
                if "sheet" in assets:
                    assets["sheet"]["stale"] = True
                    for view in assets["sheet"].get("views", {}).values():
                        view["stale"] = True
                for kind3d in ("model_3d", "rig_3d"):
                    if kind3d in assets:
                        assets[kind3d]["stale"] = True
            elif group == "expressions":                     # the living portrait is assembled from the expressions
                if "living" in assets:
                    assets["living"]["stale"] = True
            else:                                            # a model-sheet view: the stitched sheet shows it, and the four-view 3D generators read it
                if "sheet" in assets:
                    assets["sheet"]["stale"] = True
                from . import three_d
                from .sheet import FULL_BODY
                if kind.partition("/")[2] in FULL_BODY and assets.get("model_3d", {}).get("engine_id") in three_d.FOUR_VIEW_GENERATORS:
                    assets["model_3d"]["stale"] = True

        npc = self.update(npc_id, apply)
        self.make_thumb(npc_id)
        return npc

    def delete_asset_candidate(self, npc_id, kind, candidate_id):
        """Remove one earlier version of a picture for good (its files too). The picture that is active now is not touched, even when it
        was made from this version."""
        if not SLOT_RE.match(kind or ""):
            raise KeyError(kind)

        def apply(npc):
            assets = npc.setdefault("assets", {})
            entries = assets.get("candidates", {}).get(kind, [])
            entry = next((c for c in entries if c.get("id") == candidate_id), None)
            if entry is None:
                raise KeyError(candidate_id)
            entries.remove(entry)
            if not entries:
                assets["candidates"].pop(kind, None)
            if not assets.get("candidates"):
                assets.pop("candidates", None)
            current = picture_meta(assets, kind)
            if current and current.get("candidate_id") == candidate_id:
                current.pop("candidate_id", None)
            self._remove_version_files(npc_id, entry)
        return self.update(npc_id, apply)

    # ---- earlier 3D models -------------------------------------------------------------------------------------------
    def _model_names(self, npc_id):
        """What a 3D model owns in its character's folder (names in the folder itself, a file or a folder): see MODEL_PREFIX."""
        folder = self._dir(npc_id)
        return sorted(p.name for p in folder.iterdir() if p.name.startswith(MODEL_PREFIX)) if folder.is_dir() else []

    @staticmethod
    def _model_stamp(model, rig):
        """What tells one state of a character's model from another: when it was made, how big it is, when it was cleaned up and when its skeleton was made."""
        return (model.get("created"), model.get("size"), (model.get("refined") or {}).get("created"), (rig or {}).get("created"))

    @staticmethod
    def _size_of(path):
        return path.stat().st_size if path.is_file() else sum(p.stat().st_size for p in path.rglob("*") if p.is_file())

    def archive_model_into(self, npc, npc_id, strict=False):
        """Keep the character's 3D model, with its skeleton and everything else it owns in the folder, among its earlier models, because something is about to replace it or delete it.
        `npc` is the record being updated (the caller holds the lock and saves it); call this before any file of the model is written over. The files are copied, so a replacement that
        then fails leaves the model as it was. Returns the new entry, or None when there is nothing to keep (no model, its file is gone, the history is off, it is already kept) or, unless
        `strict`, when the copy failed (no room, a file in use): the model is then replaced as it would have been without a history. `strict` raises OSError instead."""
        assets = npc.setdefault("assets", {})
        model = assets.get("model_3d")
        if not model or not model.get("file") or self.model_history_keep <= 0:
            return None
        folder = self._dir(npc_id)
        if not (folder / model["file"]).is_file():
            return None
        stamp = self._model_stamp(model, assets.get("rig_3d"))
        if any(self._model_stamp(e.get("model") or {}, e.get("rig")) == stamp for e in assets.get("model_history") or []):
            return None                                       # (kept already: a step that ran twice, or a model that was brought back and not changed since)
        entry_id = uuid.uuid4().hex[:12]
        target = folder / MODEL_HISTORY / entry_id
        names = self._model_names(npc_id)
        try:
            target.mkdir(parents=True)
            for name in names:
                if (folder / name).is_dir():
                    shutil.copytree(folder / name, target / name)
                else:
                    shutil.copy2(folder / name, target / name)
        except OSError:
            shutil.rmtree(target, ignore_errors=True)
            if strict:
                raise
            return None
        entry = {"id": entry_id, "replaced": now(), "model": copy.deepcopy(model), "names": names, "bytes": sum(self._size_of(target / name) for name in names)}
        if assets.get("rig_3d"):
            entry["rig"] = copy.deepcopy(assets["rig_3d"])
        entries = assets.setdefault("model_history", [])
        entries.append(entry)
        for old in entries[:max(len(entries) - self.model_history_keep, 0)]:       # (the oldest go first)
            entries.remove(old)
            shutil.rmtree(folder / MODEL_HISTORY / old["id"], ignore_errors=True)
        return entry

    def archive_model(self, npc_id):
        """Call before a new 3D model is written over the character's: the one it replaces is kept, and the page lists it under the new one with Restore and Delete.
        The new entry, or None (nothing to keep, or the history is off)."""
        if self.model_history_keep <= 0:
            return None
        with self.lock:
            if not (self.get(npc_id).get("assets", {}).get("model_3d") or {}).get("file"):
                return None
            kept = []
            self.update(npc_id, lambda npc: kept.append(self.archive_model_into(npc, npc_id)))
            return kept[0]

    def restore_model(self, npc_id, entry_id):
        """Bring an earlier 3D model back, with its skeleton and everything else it had. The model in use is kept in its place, so nothing is lost and either can be brought back.
        Returns the character. KeyError: no such earlier model; ValueError (in words): its files are gone, or the model in use could not be kept."""
        if not MODEL_ENTRY_RE.match(entry_id or ""):
            raise KeyError(entry_id)

        def apply(npc):
            assets = npc.setdefault("assets", {})
            entries = assets.get("model_history") or []
            entry = next((e for e in entries if e.get("id") == entry_id), None)
            folder = self._dir(npc_id)
            kept = folder / MODEL_HISTORY / entry_id
            if entry is None or not kept.is_dir():
                raise KeyError(entry_id)
            missing = [name for name in entry.get("names") or [] if not (kept / name).exists()]
            if missing or not (entry.get("model") or {}).get("file"):
                raise ValueError("The files of that earlier 3D model are gone, so it cannot be brought back. Delete it from the list.")
            entries.remove(entry)                              # (it is about to be the one in use)
            try:
                self.archive_model_into(npc, npc_id, strict=True)
            except OSError as exc:
                raise ValueError(f"The 3D model in use could not be kept first ({exc}), so nothing was changed") from None
            for name in self._model_names(npc_id):             # (a copy of every one is in the earlier models now)
                path = folder / name
                if path.is_dir():
                    shutil.rmtree(path, ignore_errors=True)
                else:
                    path.unlink(missing_ok=True)
            for name in entry["names"]:
                shutil.move(str(kept / name), str(folder / name))
            shutil.rmtree(kept, ignore_errors=True)
            model = entry["model"]
            sprite = assets.get("sprite") or {}
            if model.get("source_sprite_created") and model["source_sprite_created"] != sprite.get("created"):
                model["stale"] = True                          # (made from a sprite that has been drawn again since)
            model["restored"] = now()
            assets["model_3d"] = model
            if entry.get("rig"):
                assets["rig_3d"] = entry["rig"]
            else:
                assets.pop("rig_3d", None)
            if not assets.get("model_history"):
                assets.pop("model_history", None)
        return self.update(npc_id, apply)

    def delete_model_version(self, npc_id, entry_id):
        """Remove one earlier 3D model for good (its files too). The model in use is not touched. KeyError: no such earlier model."""
        if not MODEL_ENTRY_RE.match(entry_id or ""):
            raise KeyError(entry_id)

        def apply(npc):
            assets = npc.setdefault("assets", {})
            entries = assets.get("model_history") or []
            entry = next((e for e in entries if e.get("id") == entry_id), None)
            if entry is None:
                raise KeyError(entry_id)
            entries.remove(entry)
            if not entries:
                assets.pop("model_history", None)
            shutil.rmtree(self._dir(npc_id) / MODEL_HISTORY / entry_id, ignore_errors=True)
        return self.update(npc_id, apply)

    def make_thumb(self, npc_id):
        """256px preview for the library grid: the portrait if there is one, else the sprite."""
        with self.lock:
            npc = self.get(npc_id)
            slot = next((k for k in ("portrait", "sprite") if k in npc["assets"]), None)
            thumb = self.resolve(npc_id, "thumb.png")
            if not slot:
                thumb.unlink(missing_ok=True)
            else:
                meta = npc["assets"][slot]
                models = [licences.picture_engine(meta)]
                with Image.open(self.resolve(npc_id, meta["file"])) as im:
                    im = im.convert("RGBA")
                    im.thumbnail((256, 256), Image.LANCZOS)
                    media.png(im, thumb, models=models, optimize=True)
            self._index_npc(npc)

    # ---- export --------------------------------------------------------------------------------------------------
    def export_zip(self, ids):
        """ZIP with one folder per NPC (npc.json + final PNGs; *_raw.png cut-out sources are left out) and an index."""
        buf = io.BytesIO()
        index = []
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for npc_id in ids:
                try:
                    npc = self.get(npc_id)
                except KeyError:
                    continue
                a = npc.get("assets", {})
                # Candidate history belongs to the local curation workflow. Game exports contain the
                # selected images only, with no dangling references to hidden candidate files.
                a.pop("candidates", None)
                for kind in ("portrait", "sprite"):
                    if kind in a:
                        a[kind].pop("candidate_id", None)
                pictures = _export_pictures(a)
                model = a.get("model_3d") or {}
                shapes = {a[k]["file"]: (model if k == "rig_3d" else a[k], k == "head_3d")
                          for k in ("model_3d", "rig_3d", "head_3d") if a.get(k, {}).get("file")}
                files = [*pictures, *shapes]
                if self.resolve(npc_id, "body_design.json").is_file():   # what the Body tab saved for it, byte for byte: without it a restored archive has no settings to open the Body tab with
                    files.append("body_design.json")
                voice = npc.get("voice")
                if voice:                                  # the chosen voice and the voiced lines; takes and tests stay local
                    voice.pop("takes", None)
                    voice.pop("tests", None)
                    voice.pop("reference_history", None)
                    if voice.get("reference", {}).get("file"):
                        files.append(voice["reference"]["file"])
                        if voice["reference"]["file"] != "voice/reference.wav":
                            files.append("voice/reference.wav")  # compatibility copy for existing game importers
                files += [meta["file"] for meta in (npc.get("voice_lines") or {}).values() if meta.get("file")]
                files += list(dict.fromkeys(meta["voice_snapshot"]["reference"]["file"]
                    for meta in (npc.get("voice_lines") or {}).values()
                    if (meta.get("voice_snapshot") or {}).get("reference", {}).get("file")))
                files = list(dict.fromkeys(files))
                for rel in files:
                    path = self.resolve(npc_id, rel)
                    if path.exists():
                        if rel in pictures:
                            data = path.read_bytes()
                            z.writestr(f"{npc_id}/{rel}", media.label(data, media.source_models(data) or pictures[rel]))
                        elif rel in shapes:
                            meta, is_head = shapes[rel]
                            data = path.read_bytes()
                            models = licences.shape_models(meta, data)
                            if is_head and not models:
                                models = [licences.model_name("three_d:pixal3d")]
                            generated = media.read_label(data).get("ai-generated", bool(is_head or licences.known_shape_ai(meta)))
                            labelled = media.label(data, models, ai_generated=generated)
                            if labelled != data:
                                for shape in (a[k] for k in ("model_3d", "rig_3d", "head_3d") if k in a):
                                    if shape.get("file") == rel and "size" in shape:
                                        shape["size"] = len(labelled)
                            z.writestr(f"{npc_id}/{rel}", labelled)
                        else:
                            z.write(path, f"{npc_id}/{rel}")
                z.writestr(f"{npc_id}/npc.json", json.dumps(npc, indent=2, ensure_ascii=False))
                p = npc["profile"]
                index.append({"id": npc_id, "name": f'{p["first_name"]} {p["last_name"]}'.strip(), "age": p["age"],
                              "occupation": p["occupation"], "style": npc.get("style"), "files": ["npc.json", *files]})
            z.writestr("index.json", json.dumps(index, indent=2))
        return buf.getvalue()
