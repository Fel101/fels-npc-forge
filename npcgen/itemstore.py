"""Items on disk: one folder per item (item.json, its pictures and its 3D files), the way npcgen/library.py keeps characters.

The folders live in the library folder, in a hidden one (.npcforge-items) that the character library never reads, so an item can
not turn up as a character and a character export never carries one. Everything is read and written under one lock, because the
job worker and the web server's threads both touch an item.

An item of a family built from a recipe (glasses, fitted clothes) has no pictures and no job: creating or editing one builds its model on the spot
(`build_recipe`), and its preview picture is drawn in the background (npcgen/itemposter.py).
"""
import copy
import io
import json
import os
import re
import shutil
import threading
import zipfile

from PIL import Image

from . import equip, facefit, facescan, glbmerge, itemposter, items
from . import atomic, licences, media
from .library import ID_RE, now

PROMPT_FIELDS = ("family", "kind", "description", "setting", "look")      # what the pictures are drawn from
ASSET_KINDS = ("hero", "views", "high", "model_3d")


def _slug(text):
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


class ItemStore:
    def __init__(self, lib, cfg=None, draw=None):
        self.lib = lib
        self.root = lib.root / ".npcforge-items"
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self._rests, self._shapes = {}, {}               # what equip measured of a character's model and of an item's, while the files stay the same
        self._index = {}
        self.posters = itemposter.Posters(self, cfg, **({"draw": draw} if draw else {})) if cfg is not None else None
        self.refresh_index()

    def close(self):
        if self.posters is not None:
            self.posters.close()

    # ---- the list ---------------------------------------------------------------------------------------------------------------
    def summary(self, item):
        """The card of an item in the Items list: what it is, what has been made, whether any of it is out of date."""
        assets = item.get("assets") or {}
        views = assets.get("views") or {}
        high = assets.get("high") or {}
        model = assets.get("model_3d") or {}
        spec = items.kind_spec(item)
        metas = [assets.get("hero") or {}, *views.values(), *high.values(), model]
        return {"id": item["id"], "name": item.get("name", ""), "family": item.get("family"), "kind": item.get("kind"),
                "maker": items.maker(item.get("family")), "kind_label": spec["label"], "description": item.get("description", ""), "setting": item.get("setting", ""),
                "look": item.get("look", items.LOOK_DEFAULT), "size_m": item.get("size_m"), "updated": item.get("updated"),
                "has": {"hero": bool((assets.get("hero") or {}).get("file")), "views": sum(1 for v in views.values() if v.get("file")),
                        "high": sum(1 for v in high.values() if v.get("file")),
                        "model_3d": bool(model.get("file")), "finished": bool((model.get("refined") or {}).get("created"))},
                "stale": any(meta.get("stale") or meta.get("game_stale") for meta in metas),
                "thumb": (self._dir(item["id"]) / "thumb.png").exists()}

    def refresh_index(self):
        with self.lock:
            fresh = {}
            for folder in self.root.iterdir():
                path = folder / "item.json"
                if not path.exists():
                    continue
                try:
                    item = json.loads(atomic.read_text(path))
                    fresh[item["id"]] = self.summary(item)
                except (KeyError, ValueError, TypeError, AttributeError, UnicodeError, OSError):
                    continue
            self._index = fresh
            return self.list()

    def list(self):
        with self.lock:
            return copy.deepcopy(sorted(self._index.values(), key=lambda s: s["updated"] or "", reverse=True))

    # ---- paths --------------------------------------------------------------------------------------------------------------------
    def _dir(self, item_id):
        if not ID_RE.match(item_id or ""):
            raise KeyError(item_id)
        return self.root / item_id

    def resolve(self, item_id, rel):
        """Absolute path of a file inside an item's folder; refuses anything that escapes it."""
        base = self._dir(item_id).resolve()
        path = (base / str(rel).replace("\\", "/")).resolve()    # (a backslash is a separator on every system, as on Windows)
        if not path.is_relative_to(base):
            raise KeyError(rel)
        return path

    # ---- records --------------------------------------------------------------------------------------------------------------------
    def new_id(self, fields):
        for _attempt in range(20):
            candidate = f"{(_slug(fields.get('name')) or 'item')[:40]}-{os.urandom(3).hex()}"
            if not (self.root / candidate).exists():
                return candidate
        raise RuntimeError("Could not find a free item id")

    def create(self, fields):
        """A new item from validated fields (items.clean_item)."""
        with self.lock:
            item = {**fields, "id": self.new_id(fields), "seed": items.new_seed(), "created": now(), "updated": now(), "assets": {}}
            self._dir(item["id"]).mkdir(parents=True)
            self._write(item)
            self._index[item["id"]] = self.summary(item)
            if items.is_recipe(item):
                item = self.build_recipe(item["id"])
            return item

    def get(self, item_id):
        path = self._dir(item_id) / "item.json"
        if not path.exists():
            raise KeyError(item_id)
        return json.loads(atomic.read_text(path))

    def save(self, item):
        with self.lock:
            item["updated"] = now()
            self._write(item)
            self._index[item["id"]] = self.summary(item)
            return item

    def update(self, item_id, fn):
        """Read-modify-write under the lock, so the worker thread and the web server's threads never clobber each other."""
        with self.lock:
            item = self.get(item_id)
            fn(item)
            return self.save(item)

    def _write(self, item):
        path = self._dir(item["id"]) / "item.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(item, indent=2, ensure_ascii=False), "utf-8")
        atomic.replace(tmp, path)

    def delete(self, item_id):
        with self.lock:
            shutil.rmtree(self._dir(item_id))
            self._index.pop(item_id, None)

    # ---- editing ----------------------------------------------------------------------------------------------------------------------
    def edit(self, item_id, fields):
        """Change an item's fields (items.clean_item). What the pictures are drawn from changes: the pictures and the model made from
        them are marked out of date (they stay, so they can be compared or kept). A new size is applied to the model at once;
        a new game-version size waits for the finish step."""
        current = self.get(item_id)
        if items.maker(fields["family"]) != items.maker(current.get("family")):
            label = items.load()["families"][fields["family"] if items.is_recipe({"family": fields["family"]}) else current["family"]]["label"]
            raise ValueError(f"{label} cannot become another sort of item, or the other way round: make a new item")
        if items.is_recipe(fields):
            return self._edit_recipe(item_id, fields)

        def apply(item):
            before = {key: item.get(key) for key in (*PROMPT_FIELDS, "size_m", "faces", "name")}
            item.update(fields)
            drawn_from = [key for key in PROMPT_FIELDS if before[key] != item.get(key)]
            if item.get("kind") == "other" and before["name"] != item.get("name"):
                drawn_from.append("name")               # an "other" is named by its name
            assets = item.setdefault("assets", {})
            if drawn_from:
                self.mark_stale(item)
            model = assets.get("model_3d")
            if model and before["faces"] != item.get("faces") and (model.get("refined") or {}).get("created"):
                model["game_stale"] = True
            if model and before["size_m"] != item.get("size_m"):
                model["fit_stale"] = True
        item = self.update(item_id, apply)
        if (item.get("assets", {}).get("model_3d") or {}).get("fit_stale"):
            self.refit(item_id)
            item = self.get(item_id)
        self.make_thumb(item_id)
        return item

    def _edit_recipe(self, item_id, fields):
        """Change an item that is built from a recipe: a changed recipe or detail rebuilds the model at once. The size is the model's own
        (it comes from the measurements), so the one the editor sent is ignored."""
        fields = {key: value for key, value in fields.items() if key != "size_m"}
        changed = {}

        def apply(item):
            changed["rebuild"] = (item.get("recipe") != fields.get("recipe") or item.get("faces") != fields.get("faces")
                                  or not (item.get("assets", {}).get("model_3d") or {}).get("file"))
            item.update(fields)
        item = self.update(item_id, apply)
        return self.build_recipe(item_id) if changed["rebuild"] else item

    def build_recipe(self, item_id):
        """Build the model of an item that is made from a recipe (npcgen/glasses.py, npcgen/clothing.py): a few hundredths of a second, no GPU. Writes
        model_3d.glb, sets the item's size from the measurements, and asks for a new preview picture (the old one stays until then)."""
        with self.lock:
            item = self.get(item_id)
            made = items.recipe_maker(item.get("family"))
            if made is None:
                raise ValueError("This item is made from pictures, not from a recipe")
            glb, info = made.build(item["recipe"], faces=item["faces"])
            self.write_asset(item_id, "model_3d.glb", glb)
            stamp = now()
            build = os.urandom(4).hex()                    # (a build's own id: two builds in the same second have the same `created`)

            def apply(n):
                n["size_m"] = round(max(max(info["size_m"]), 0.02), 4)
                old = (n.setdefault("assets", {}).get("model_3d") or {})
                n["assets"]["model_3d"] = {
                    **{k: old[k] for k in ("preview", "preview_of") if k in old},
                    "file": "model_3d.glb", "created": stamp, "build": build, "size": len(glb), "format": "glb", **made.model_meta(info),
                    "fit": {"size_m": info["size_m"], "scale": 1.0, "origin": info["origin"], "requested_m": n["size_m"]},
                    "refined": {"created": stamp, "faces": info["triangles"], "faces_requested": n["faces"]}}
            item = self.update(item_id, apply)
        self.make_thumb(item_id)
        if self.posters is not None:
            self.posters.request(item_id)
        return item

    @staticmethod
    def mark_stale(item, hero=True):
        """The picture and what was made from it no longer match the description."""
        assets = item.get("assets") or {}
        if hero and assets.get("hero"):
            assets["hero"]["stale"] = True
        for view in (*(assets.get("views") or {}).values(), *(assets.get("high") or {}).values()):
            view["stale"] = True
        if assets.get("model_3d"):
            assets["model_3d"]["stale"] = True

    def refit(self, item_id):
        """Scale the model to the item's size again (a quick change of its wrapper node, no GPU)."""
        with self.lock:
            item = self.get(item_id)
            model = (item.get("assets") or {}).get("model_3d") or {}
            if not model.get("file"):
                return item
            spec = items.kind_spec(item)
            try:
                fitted, fit = items.fit_glb(self.read_asset(item_id, model["file"]), item["size_m"], spec["pivot"], spec.get("grip_at"))
            except ValueError as exc:
                message = str(exc)                     # (the name `exc` is cleared when the except block ends)

                def fail(n):
                    n["assets"]["model_3d"]["fit_error"] = message
                return self.update(item_id, fail)
            fitted = licences.label_shape(fitted, model)
            self.write_asset(item_id, model["file"], fitted)

            def apply(n):
                meta = n["assets"]["model_3d"]
                meta.pop("fit_stale", None)
                meta.pop("fit_error", None)
                meta.update(fit={**fit, "requested_m": item["size_m"]}, size=len(fitted))
            return self.update(item_id, apply)

    # ---- glasses on a face ---------------------------------------------------------------------------------------------------------------------
    def _pair(self, item_id):
        """(item, the pair's GLB bytes) of an item that is a pair of glasses with a model."""
        item = self.get(item_id)
        if items.maker(item.get("family")) != "glasses":
            raise ValueError("Only glasses are fitted to a face so far")
        model = (item.get("assets") or {}).get("model_3d") or {}
        if not model.get("file"):
            raise ValueError("This pair has no model yet: save it first")
        return item, self.read_asset(item_id, model["file"])

    @staticmethod
    def saved_nudge(item, npc_id):
        """The nudge saved for this character: the neutral one when there is none."""
        try:
            return facefit.clean_nudge(((item.get("fits") or {}).get(npc_id) or {}).get("nudge"))
        except ValueError:
            return facefit.clean_nudge(None)

    def fit(self, item_id, npc_id, scan, nudge=None):
        """Where this pair goes on a character's face (npcgen/facefit.py on the pair's own model). `scan` is a facefit.Scan; no nudge means the saved
        one. Returns facefit.fit's placement with "saved": what is saved for this character (its nudge, source and time) or None."""
        item, glb = self._pair(item_id)
        saved = (item.get("fits") or {}).get(npc_id)
        placement = facefit.fit(scan, glb, self.saved_nudge(item, npc_id) if nudge is None else nudge)
        placement["saved"] = None if saved is None else {"nudge": self.saved_nudge(item, npc_id), "source": saved.get("source"), "created": saved.get("created")}
        return placement

    def worn(self, item_id, npc_id, scan, nudge=None):
        """The character's model with this pair on it, under its head joint so it follows every clip: (GLB bytes, {"placement", "attach", "bone", "model"}).
        Raises KeyError for an unknown character and ValueError for what cannot be fitted."""
        npc = self.lib.get(npc_id)
        found = facescan.target(npc)
        if found is None:
            raise ValueError("This character has no 3D model")
        _item, glb = self._pair(item_id)
        body = licences.label_shape(self.lib.read_asset(npc_id, found[1]), npc["assets"].get("model_3d") or {})
        placement = self.fit(item_id, npc_id, scan, nudge)
        merged, where = glbmerge.merge_bytes(body, media.label(glb, ai_generated=False), facescan.HEAD_BONE, facefit.world_matrix(placement),
                                             tag={"npcforge": "glasses"})
        return merged, {"placement": placement, "attach": where["attach"], "bone": where["parent"], "model": found[0]}

    def save_fit(self, item_id, npc_id, nudge, source=None):
        """Keep a nudge as this pair's fit on this character (the scan itself belongs to the character, and the pair is fitted again to it
        whenever either changes: only the nudge is kept)."""
        if not ID_RE.match(npc_id or ""):
            raise ValueError("Not a character id")
        nudge = facefit.clean_nudge(nudge)
        self._pair(item_id)

        def apply(item):
            item.setdefault("fits", {})[npc_id] = {"nudge": nudge, "source": source, "created": now()}
        return self.update(item_id, apply)

    def forget_fit(self, item_id, npc_id):
        """Drop the saved fit on one character. Raises KeyError when there is none."""
        def apply(item):
            if npc_id not in (item.get("fits") or {}):
                raise KeyError(npc_id)
            del item["fits"][npc_id]
            if not item["fits"]:
                del item["fits"]
        return self.update(item_id, apply)

    # ---- held items and hats on a character ---------------------------------------------------------------------------------------------
    @staticmethod
    def equip_how(item):
        """How an item is carried or worn: {"carry", "slot", "slots"}, or None when equipping it is not done yet (npcgen/equip.py). Glasses and fitted clothes have
        their own ways onto a character."""
        if items.maker(item.get("family")) != "photo":
            return None
        how = equip.support(item.get("kind"), items.kind_spec(item))
        return None if how is None else {**how, "slots": list(equip.slots_for(how["carry"]))}

    def saved_equip(self, item, npc_id):
        """The slot and nudge kept for this character, else the kind's usual slot and the neutral nudge: {"slot", "nudge", "saved": {...} or None}."""
        how = self.equip_how(item) or {}
        kept = (item.get("equips") or {}).get(npc_id)
        slot = (kept or {}).get("slot")
        try:
            nudge = equip.clean_nudge((kept or {}).get("nudge"))
        except ValueError:
            nudge = equip.clean_nudge(None)
        return {"slot": slot if slot in how.get("slots", ()) else how.get("slot"), "nudge": nudge,
                "saved": None if kept is None else {"slot": slot, "nudge": nudge, "created": kept.get("created")}}

    def _equip_entry(self, item_id, npc_id, slot=None, nudge=None):
        """What equip.equip needs of one item: its model, its kind, and where and how it goes (what was asked, else what is kept for this character)."""
        item = self.get(item_id)
        if self.equip_how(item) is None:
            raise ValueError(f"Equipping a {items.kind_spec(item).get('label', item.get('kind'))} is not done yet")
        model = (item.get("assets") or {}).get("model_3d") or {}
        if not model.get("file"):
            raise ValueError(f"{item.get('name') or 'This item'} has no 3D model yet: make it first")
        kept = self.saved_equip(item, npc_id)
        return {"glb": self.read_asset(item_id, model["file"]), "model": model,
                "kind": item["kind"], "spec": items.kind_spec(item), "item": item_id, "name": item.get("name"),
                "slot": slot or kept["slot"], "nudge": kept["nudge"] if nudge is None else nudge,
                "_key": (item_id, model.get("file"), model.get("build") or model.get("created"), model.get("size"))}

    def _character(self, npc_id):
        """(which of its files, the file's path, the rest pose read from it) of the model a character's items go on: the rigged one when there is one. The rest pose
        is kept while the file stays the same, so moving a slider does not read a 50 MB file again."""
        found = facescan.target(self.lib.get(npc_id))
        if found is None:
            raise ValueError("This character has no 3D model")
        path = self.lib.resolve(npc_id, found[1])
        stat = path.stat()
        stamp = (str(path), stat.st_mtime_ns, stat.st_size)
        with self.lock:
            cached = self._rests.get(npc_id)
        if cached and cached[0] == stamp:
            return found[0], path, cached[1]
        rest = equip.read_rest(path.read_bytes())
        with self.lock:
            self._rests = {**{k: v for k, v in list(self._rests.items())[-2:] if k != npc_id}, npc_id: (stamp, rest)}
        return found[0], path, rest

    def equip_placement(self, item_id, npc_id, slot=None, nudge=None):
        """Where an item goes on a character, worked out without building a file: equip.describe's answer (slot, bone, the attach block a game needs, scale, the nudge
        used), plus "saved" (what is kept for this character, or None) and "notes" (what was guessed about this character) and "model" (which of its files)."""
        entry = self._equip_entry(item_id, npc_id, slot, nudge)
        model, _path, rest = self._character(npc_id)
        with self.lock:
            shape = self._shapes.get(entry["_key"])
        if shape is None:
            shape = equip.item_shape(entry["glb"])
            with self.lock:
                self._shapes = {entry["_key"]: shape, **{k: v for k, v in list(self._shapes.items())[-8:] if k != entry["_key"]}}
        placed = equip.describe(rest, entry["kind"], entry["spec"], entry["slot"], shape, entry["nudge"])
        placed.pop("world")
        return {**placed, "saved": self.saved_equip(self.get(item_id), npc_id)["saved"], "notes": list(rest.notes), "model": model}

    def equipped(self, npc_id, parts):
        """The character's model with items on it: (GLB bytes, equip's report plus "model"). `parts` is [{"item", "slot", "nudge"}]; a missing slot or nudge is what is
        kept for this character, else the kind's usual slot and the neutral nudge. Raises KeyError for an unknown character or item and ValueError for what cannot be put on."""
        model, path, rest = self._character(npc_id)
        entries = [self._equip_entry(part["item"], npc_id, part.get("slot"), part.get("nudge")) for part in parts]
        for entry in entries:
            entry["glb"] = licences.label_shape(entry["glb"], entry["model"])
        assets = self.lib.get(npc_id).get("assets") or {}
        body = licences.label_shape(path.read_bytes(), assets.get("model_3d") or {})
        merged, report = equip.equip(body, entries, rest)
        return merged, {**report, "model": model}

    def save_equip(self, item_id, npc_id, slot, nudge):
        """Keep a slot and nudge as this item's way of being carried or worn by this character (nothing is put on the character: the numbers are all that is kept)."""
        if not ID_RE.match(npc_id or ""):
            raise ValueError("Not a character id")
        how = self.equip_how(self.get(item_id))
        if how is None:
            raise ValueError("Equipping this kind of item is not done yet")
        slot = slot or how["slot"]
        if slot not in how["slots"]:
            raise ValueError(f"This cannot go in the {equip.SLOTS.get(slot, slot)}")
        nudge = equip.clean_nudge(nudge)

        def apply(item):
            item.setdefault("equips", {})[npc_id] = {"slot": slot, "nudge": nudge, "created": now()}
        return self.update(item_id, apply)

    def forget_equip(self, item_id, npc_id):
        """Drop what is kept for one character. Raises KeyError when there is nothing."""
        def apply(item):
            if npc_id not in (item.get("equips") or {}):
                raise KeyError(npc_id)
            del item["equips"][npc_id]
            if not item["equips"]:
                del item["equips"]
        return self.update(item_id, apply)

    # ---- assets ------------------------------------------------------------------------------------------------------------------------
    def write_asset(self, item_id, rel, data):
        path = self.resolve(item_id, rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes(data)
        atomic.replace(tmp, path)
        return rel

    def read_asset(self, item_id, rel):
        return atomic.read_bytes(self.resolve(item_id, rel))

    def delete_asset(self, item_id, kind):
        """Remove the main picture, the four views, the high-angle views or the 3D model with its files. What was made from it is marked out of date."""
        if kind not in ASSET_KINDS:
            raise KeyError(kind)

        def apply(item):
            assets = item.setdefault("assets", {})
            if assets.pop(kind, None) is None:
                raise KeyError(kind)
            if kind == "hero":
                self.resolve(item_id, "hero.png").unlink(missing_ok=True)
                for view in (*(assets.get("views") or {}).values(), *(assets.get("high") or {}).values()):
                    view["stale"] = True
            elif kind == "views":
                shutil.rmtree(self.resolve(item_id, "views"), ignore_errors=True)
            elif kind == "high":
                shutil.rmtree(self.resolve(item_id, "high"), ignore_errors=True)
            else:
                for rel in ("model_3d.glb", "model_3d_original.glb", "model_3d_preview.png"):
                    self.resolve(item_id, rel).unlink(missing_ok=True)
            if kind in ("hero", "views") and assets.get("model_3d"):       # (the shape was made from them; the paint just uses the high ones)
                assets["model_3d"]["stale"] = True
        item = self.update(item_id, apply)
        self.make_thumb(item_id)
        return item

    def make_thumb(self, item_id):
        """256 px card picture for the Items list: the main picture if there is one, else the model's preview."""
        with self.lock:
            item = self.get(item_id)
            assets = item.get("assets") or {}
            hero, model = assets.get("hero") or {}, assets.get("model_3d") or {}
            src = hero.get("file") or model.get("preview")
            thumb = self.resolve(item_id, "thumb.png")
            if not src or not self.resolve(item_id, src).exists():
                thumb.unlink(missing_ok=True)
            else:
                data = self.read_asset(item_id, src)
                names = [licences.picture_engine(hero)] if hero.get("file") else licences.shape_models(model, data)
                generated = media.read_label(data).get("ai-generated", bool(hero.get("file") or licences.known_shape_ai(model)))
                with Image.open(io.BytesIO(data)) as im:
                    im = im.convert("RGB")
                    im.thumbnail((256, 256), Image.LANCZOS)
                    media.png(im, thumb, models=names, ai_generated=generated, optimize=True)
            self._index[item_id] = self.summary(item)

    # ---- export ----------------------------------------------------------------------------------------------------------------------------
    def export_zip(self, item_id):
        """A ZIP of one item: item.json, the main picture, the views and the 3D model (the game version, scaled to its size)."""
        item = self.get(item_id)
        assets = item.get("assets") or {}
        pictures = {}
        if hero := assets.get("hero"):
            pictures[hero["file"]] = licences.picture_engine(hero)
        for view in (*(assets.get("views") or {}).values(), *(assets.get("high") or {}).values()):
            if view.get("file"):
                pictures[view["file"]] = licences.picture_engine(view, edit=True)
        model = assets.get("model_3d") or {}
        files = [*pictures, *(model[k] for k in ("file", "preview") if model.get(k))]
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for rel in files:
                path = self.resolve(item_id, rel)
                if not path.exists():
                    continue
                data = path.read_bytes()
                if rel in pictures:
                    data = media.label(data, [pictures[rel]])
                else:
                    data = licences.label_shape(data, model)
                    if rel == model.get("file") and "size" in model:
                        model["size"] = len(data)
                z.writestr(f"{item_id}/{rel}", data)
            z.writestr(f"{item_id}/item.json", json.dumps(item, indent=2, ensure_ascii=False))
        return buf.getvalue()
