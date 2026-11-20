"""Reusable voices with immutable, checksummed reference versions.

Metadata lives beside the NPC library in .voice-library/. Each DB operation has
its own connection; model environments and the external Voice Lab are untouched.
"""
import copy
import hashlib
import json
import math
import re
import sqlite3
import threading
import uuid
from contextlib import contextmanager

import numpy as np

from ..library import now
from . import audio, engines, record, reference_clean, service

ID = re.compile(r"^[a-f0-9]{32}$")
SCHEMA = 1


def _text(value, label, limit, required=False):
    if not isinstance(value, str):
        raise ValueError(f"{label} must be text")
    value = value.strip()
    if len(value) > limit or (required and not value):
        raise ValueError(f"{label} must be {'1-' if required else 'at most '}{limit} characters")
    return value


def _tags(value):
    if not isinstance(value, list) or len(value) > 16:
        raise ValueError("Use at most 16 tags")
    result = []
    for item in value:
        tag = _text(item, "Tag", 40, True)
        if tag.casefold() not in {x.casefold() for x in result}:
            result.append(tag)
    return result


def reference_quality(data, speaker=None):
    """Measurements, not a claim about identity, background noise, or speech quality."""
    samples, rate = audio.read_wav(data)
    if rate < 8000 or not len(samples) or len(samples) / rate > 120:
        raise ValueError("Use a readable 8 kHz or higher reference, at most 120 seconds")
    rms = float(np.sqrt(np.mean(samples.astype(np.float64) ** 2)))
    if not math.isfinite(rms) or rms < 10 ** (-50 / 20):
        raise ValueError("The reference is silent or too quiet")
    duration = len(samples) / rate
    warnings = []
    minimum = 5.01 if speaker == "chatterbox" else 3.0
    if speaker == "kokoro":
        minimum = 0.25
    if duration < minimum:
        warnings.append(f"{engines.ENGINES.get(speaker, {}).get('label', speaker or 'Cloning')} needs at least {minimum:g} seconds")
    if duration > 40 and speaker != "kokoro":
        warnings.append("Trim the cloning reference to 40 seconds or less")
    clipped = float(np.mean(np.abs(samples) >= .995))
    if clipped > .001:
        warnings.append("The recording contains clipped samples")
    return {"duration_seconds": round(duration, 6), "sample_rate": rate, "rms_dbfs": round(20 * math.log10(rms), 2),
            "peak": round(float(np.max(np.abs(samples))), 6), "clipped_fraction": round(clipped, 6),
            "silence_fraction": round(float(np.mean(np.abs(samples) < .003)), 6), "warnings": warnings}


class VoiceLibrary:
    def __init__(self, lib):
        self.lib = lib
        self.root = lib.root / ".voice-library"
        self.lock = threading.RLock()
        self.initialized = False

    def _contained(self, rel):
        base = self.root.resolve()
        # A junction at the store root must not redirect writes outside the configured library.
        if not base.is_relative_to(self.lib.root.resolve()):
            raise ValueError("Voice library directory escapes the NPC library")
        path = (base / rel).resolve()
        if not path.is_relative_to(base):
            raise ValueError("Voice library path escapes its store")
        return path

    def _initialize(self):
        if self.initialized:
            return
        self._contained("versions").mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self._contained("voices.sqlite3"))
        try:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS voices (
                  id TEXT PRIMARY KEY, name TEXT NOT NULL, tags TEXT NOT NULL,
                  favorite INTEGER NOT NULL DEFAULT 0, notes TEXT NOT NULL,
                  preferred_version TEXT, created TEXT NOT NULL, updated TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS versions (
                  id TEXT PRIMARY KEY, voice_id TEXT NOT NULL REFERENCES voices(id),
                  number INTEGER NOT NULL, snapshot TEXT NOT NULL, reference TEXT NOT NULL,
                  sha256 TEXT NOT NULL, quality TEXT NOT NULL, provenance TEXT NOT NULL,
                  created TEXT NOT NULL, UNIQUE(voice_id, number));
                CREATE TABLE IF NOT EXISTS approvals (
                  sequence INTEGER PRIMARY KEY AUTOINCREMENT, version_id TEXT NOT NULL REFERENCES versions(id),
                  approved INTEGER NOT NULL, notes TEXT NOT NULL, created TEXT NOT NULL);
                CREATE TRIGGER IF NOT EXISTS immutable_version_update BEFORE UPDATE ON versions
                  BEGIN SELECT RAISE(ABORT, 'Voice versions are immutable'); END;
                CREATE TRIGGER IF NOT EXISTS immutable_version_delete BEFORE DELETE ON versions
                  BEGIN SELECT RAISE(ABORT, 'Voice versions are immutable'); END;
            """)
            db.execute(f"PRAGMA user_version={SCHEMA}")
            db.commit()
        finally:
            db.close()
        self.initialized = True

    @contextmanager
    def _db(self):
        with self.lock:
            self._initialize()
            db = sqlite3.connect(self._contained("voices.sqlite3"), timeout=15)
            try:
                db.row_factory = sqlite3.Row
                db.execute("PRAGMA foreign_keys=ON")
                with db:
                    yield db
            finally:
                db.close()

    @staticmethod
    def _id(value):
        if not isinstance(value, str) or not ID.fullmatch(value):
            raise ValueError("Invalid voice or version id")
        return value

    def _version(self, db, version_id):
        row = db.execute("SELECT * FROM versions WHERE id=?", (self._id(version_id),)).fetchone()
        if row is None:
            raise KeyError(version_id)
        out = dict(row)
        for key in ("snapshot", "reference", "quality", "provenance"):
            out[key] = json.loads(out[key])
        approval = db.execute("SELECT approved,notes,created FROM approvals WHERE version_id=? ORDER BY sequence DESC LIMIT 1",
                              (version_id,)).fetchone()
        out["approved"] = bool(approval and approval["approved"])
        out["approval"] = dict(approval) if approval else None
        out["audio_url"] = f"/api/voice/library/versions/{version_id}/audio.wav" if out["reference"].get("file") else None
        return out

    def get_version(self, version_id):
        with self._db() as db:
            return self._version(db, version_id)

    def _voice(self, db, voice_id):
        row = db.execute("SELECT * FROM voices WHERE id=?", (self._id(voice_id),)).fetchone()
        if row is None:
            raise KeyError(voice_id)
        out = dict(row)
        out["tags"] = json.loads(out["tags"])
        out["favorite"] = bool(out["favorite"])
        out["versions"] = [self._version(db, row["id"]) for row in db.execute(
            "SELECT id FROM versions WHERE voice_id=? ORDER BY number DESC", (voice_id,)).fetchall()]
        return out

    def get(self, voice_id):
        with self._db() as db:
            return self._voice(db, voice_id)

    def list(self, query="", tag="", favorites=False):
        with self._db() as db:
            rows = [self._voice(db, row["id"]) for row in db.execute("SELECT id FROM voices ORDER BY favorite DESC,name COLLATE NOCASE")]
        return [row for row in rows if (not favorites or row["favorite"])
                and (not tag or tag.casefold() in [x.casefold() for x in row["tags"]])
                and query.casefold() in (row["name"] + " " + " ".join(row["tags"]) + " " + row["notes"]).casefold()]

    def update(self, voice_id, body):
        with self._db() as db:
            row = self._voice(db, voice_id)
            name = _text(body.get("name", row["name"]), "Voice name", 100, True)
            notes = _text(body.get("notes", row["notes"]), "Notes", 2000)
            tags = _tags(body.get("tags", row["tags"]))
            favorite = body.get("favorite", row["favorite"])
            if not isinstance(favorite, bool):
                raise ValueError("favorite must be true or false")
            preferred = body.get("preferred_version", row["preferred_version"])
            if preferred != row["preferred_version"]:
                version = self._version(db, preferred)
                if version["voice_id"] != voice_id or not version["approved"]:
                    raise ValueError("Choose an approved version of this voice")
            db.execute("UPDATE voices SET name=?,tags=?,favorite=?,notes=?,preferred_version=?,updated=? WHERE id=?",
                       (name, json.dumps(tags), int(favorite), notes, preferred, now(), voice_id))
            return self._voice(db, voice_id)

    def _add(self, db, voice_id, data, snapshot, reference, provenance):
        preset = record.is_preset(snapshot)
        quality = (reference_quality(data, "kokoro" if preset else record.speaker_for(snapshot)) if data is not None else
                   {"kind": "model_preset", "duration_seconds": 0, "warnings": []})
        version_id = uuid.uuid4().hex
        number = db.execute("SELECT COALESCE(MAX(number),0)+1 FROM versions WHERE voice_id=?", (voice_id,)).fetchone()[0]
        reference = copy.deepcopy(reference)
        if preset:
            snapshot = copy.deepcopy(snapshot)
            snapshot["preset_identity"]["id"] = version_id
        digest = hashlib.sha256(data if data is not None else json.dumps(snapshot, sort_keys=True).encode()).hexdigest()
        if data is not None:
            path = self._contained(f"versions/{version_id}.wav")
            with path.open("xb") as stream:
                stream.write(data)
            reference.update(file=f"versions/{version_id}.wav", id=version_id, seconds=quality["duration_seconds"], sha256=digest)
        if preset:
            reference["kind"] = "audition" if data is not None else "model_preset"
        db.execute("INSERT INTO versions VALUES (?,?,?,?,?,?,?,?,?)", (version_id, voice_id, number,
                   json.dumps(snapshot, ensure_ascii=False), json.dumps(reference, ensure_ascii=False), digest,
                   json.dumps(quality), json.dumps(provenance, ensure_ascii=False), now()))
        db.execute("UPDATE voices SET preferred_version=COALESCE(preferred_version,?),updated=? WHERE id=?", (version_id, now(), voice_id))
        return self._version(db, version_id)

    def from_npc(self, npc_id, body):
        # Keep the asset read and record snapshot together with concurrent NPC writes.
        with self.lib.lock:
            npc = self.lib.get(npc_id)
            v = record.voice_of(npc)
            ref = v.get("reference")
            if not ref and not record.is_preset(v):
                raise ValueError("Assign a voice to this character before saving it to the voice library")
            ref = ref or {}
            data = self.lib.read_asset(npc_id, ref["file"]) if ref.get("file") else None
            if ref.get("sha256") and data is not None and hashlib.sha256(data).hexdigest() != ref["sha256"]:
                raise ValueError("Source reference checksum failed")
            snapshot = record.render_snapshot(v)
            snapshot.pop("reference", None)
        with self._db() as db:
            voice_id = body.get("voice_id")
            if voice_id:
                self._voice(db, voice_id)
            else:
                voice_id = uuid.uuid4().hex
                name = _text(body.get("name", ""), "Voice name", 100, True)
                tags = _tags(body.get("tags", []))
                notes = _text(body.get("notes", ""), "Notes", 2000)
                db.execute("INSERT INTO voices VALUES (?,?,?,0,?,NULL,?,?)", (voice_id, name, json.dumps(tags), notes, now(), now()))
            self._add(db, voice_id, data, snapshot, ref, {"source_npc": npc_id, "source_reference": copy.deepcopy(ref),
                "notes": _text(body.get("version_notes", ""), "Version notes", 2000)})
            return self._voice(db, voice_id)

    def read_audio(self, version_id):
        version = self.get_version(version_id)
        if not version["reference"].get("file"):
            raise FileNotFoundError("This preset identity has no audition clip")
        data = self._contained(version["reference"]["file"]).read_bytes()
        if hashlib.sha256(data).hexdigest() != version["sha256"]:
            raise ValueError("Stored reference checksum failed; restore this voice library from a backup")
        return data

    def approve(self, version_id, body):
        approved = body.get("approved")
        if not isinstance(approved, bool):
            raise ValueError("approved must be true or false")
        notes = _text(body.get("notes", ""), "Approval notes", 2000)
        with self._db() as db:
            self._version(db, version_id)
            db.execute("INSERT INTO approvals(version_id,approved,notes,created) VALUES (?,?,?,?)", (version_id, int(approved), notes, now()))
            return self._version(db, version_id)

    def derive(self, version_id, body):
        parent = self.get_version(version_id)
        if record.is_preset(parent["snapshot"]):
            raise ValueError("Trimming a preset audition does not change its speaker identity; save a new preset version instead")
        data = self.read_audio(version_id)
        samples, rate = audio.read_wav(data)
        try:
            start, end = float(body.get("start_seconds", 0)), float(body.get("end_seconds", len(samples) / rate))
        except (TypeError, ValueError):
            raise ValueError("Trim times must be numbers") from None
        if not (math.isfinite(start) and math.isfinite(end) and 0 <= start < end <= len(samples) / rate + .000001):
            raise ValueError("Choose a trim region inside the reference")
        transcript = _text(body.get("transcript", ""), "Exact transcript for the trimmed reference", 2000, True)
        normalize = body.get("normalize", False)
        if not isinstance(normalize, bool):
            raise ValueError("normalize must be true or false")
        trimmed = samples[round(start * rate):round(end * rate)]
        if normalize:
            trimmed = audio.normalize(trimmed, rate)
        result = audio.write_wav(trimmed, rate)
        ref = {**parent["reference"], "text": transcript, "source": f"derived:{version_id}"}
        provenance = {**parent["provenance"], "parent_version": version_id, "parent_sha256": parent["sha256"],
                      "edits": {"start_seconds": start, "end_seconds": end, "normalize": normalize},
                      "notes": _text(body.get("notes", ""), "Version notes", 2000)}
        with self._db() as db:
            return self._add(db, parent["voice_id"], result, parent["snapshot"], ref, provenance)

    def denoise(self, version_id, body):
        """A new unapproved draft version with background noise reduced. Words and timing are unchanged."""
        parent = self.get_version(version_id)
        if record.is_preset(parent["snapshot"]):
            raise ValueError("Cleaning a preset audition is not supported; save a new preset version instead")
        nf = body.get("nf", reference_clean.DEFAULT_NF)
        cleaned, measured = reference_clean.clean(self.read_audio(version_id), nf)
        # The words and their timing are unchanged, so the parent's reference text carries over.
        ref = {**parent["reference"], "source": f"denoised:{version_id}"}
        provenance = {**parent["provenance"], "parent_version": version_id, "parent_sha256": parent["sha256"],
                      "edits": {"denoise": measured},
                      "identity": {"status": "not_checked",
                                   "reason": "Cleaning removes noise and moves the speaker embedding by itself. "
                                             "A clean reference of the same speaker is needed to check the voice."}}
        with self._db() as db:
            return self._add(db, parent["voice_id"], cleaned, parent["snapshot"], ref, provenance)

    def recording(self, voice_id, body):
        entry = self.get(voice_id)
        if body.get("consent") is not True:
            raise ValueError("Confirm that you have permission to use this recording")
        transcript = _text(body.get("transcript", ""), "Exact recording transcript", 2000, True)
        if len(transcript) < 3:
            raise ValueError("Enter the exact words in the recording")
        original = service.decode_upload(body.get("audio"))
        # Validate the original but retain its exact bytes; normalization is an explicit edit.
        audio.check_upload(original)
        parent = entry["versions"][0]
        snapshot = copy.deepcopy(parent["snapshot"])
        speaker = body.get("speaker") or record.speaker_for(snapshot)
        if not engines.can(speaker, "clone"):
            raise ValueError("Choose a model that can clone a recording")
        if snapshot.get("language", "English") not in engines.ENGINES[speaker]["languages"]:
            raise ValueError("The selected speaking model does not support this voice's saved language")
        if record.is_preset(snapshot):
            snapshot["stock_voice"] = None
        snapshot.update(engine=speaker, speaker=speaker, mode="upload")
        snapshot.pop("preset_identity", None)
        snapshot.pop("library_binding", None)
        snapshot["commercial_ok"] = record.commercial_ok(snapshot)
        reference = {"text": transcript, "source": "recording", "engine": None, "language": snapshot.get("language", "English"),
                     "consent": True, "created": now()}
        with self._db() as db:
            return self._add(db, voice_id, original, snapshot, reference, {
                "origin": _text(body.get("origin", ""), "Recording origin", 500), "consent": True,
                "notes": _text(body.get("notes", ""), "Recording notes", 2000)})

    def assign(self, version_id, npc_ids):
        if not isinstance(npc_ids, list) or not 1 <= len(npc_ids) <= 100 or not all(isinstance(x, str) for x in npc_ids):
            raise ValueError("Choose 1-100 characters")
        version = self.get_version(version_id)
        if not version["approved"]:
            raise ValueError("Approve this reference version before assigning it")
        if version["quality"]["warnings"] and any("needs at least" in w or "Trim the cloning" in w for w in version["quality"]["warnings"]):
            raise ValueError("This version does not meet its speaking engine's reference length requirements")
        data = self.read_audio(version_id) if version["reference"].get("file") else None
        snapshot = version["snapshot"]
        if snapshot.get("engine") not in engines.ENGINES or record.speaker_for(snapshot) not in engines.ENGINES:
            raise ValueError("This voice's engine is not available in this Forge version")
        with self.lib.lock:
            targets = [self.lib.get(npc_id) for npc_id in dict.fromkeys(npc_ids)]
            # Validate every target before any assignment, including adult voice restrictions.
            for npc in targets:
                record.clean_settings(snapshot, npc)
            result = []
            for npc in targets:
                def apply(rec):
                    v = record.voice_of(rec)
                    v.update(copy.deepcopy(snapshot))
                    if not record.is_preset(snapshot):
                        v.pop("preset_identity", None)
                    v.update(review="picked", chosen_take=None)
                    v["library_binding"] = {"voice_id": version["voice_id"], "version_id": version_id,
                                            "number": version["number"], "sha256": version["sha256"], "approval": version["approval"]}
                    ref = {**version["reference"], "source": f"library:{version['voice_id']}:{version_id}"}
                    if data is not None:
                        record.install_reference(self.lib, rec, v, data, ref)
                    else:
                        old = record.voice_of(rec)
                        old_ref = old.get("reference")
                        history = copy.deepcopy(v.get("reference_history") or [])
                        old_id = record.identity_id(old)
                        if record.is_preset(old) and not any(item["id"] == old_id for item in history):
                            history.append({"id": old_id, "kind": "model_preset", "voice_snapshot": record.render_snapshot(old)})
                        if old_ref and not any(item["id"] == old_ref.get("id") for item in history):
                            pinned = record._immutable_reference(self.lib, rec["id"], self.lib.read_asset(rec["id"], old_ref["file"]), old_ref)
                            history.append({**pinned, "voice_snapshot": record.render_snapshot(old)})
                        v.pop("reference", None)
                        if not any(item["id"] == version_id for item in history):
                            history.append({"id": version_id, "kind": "model_preset", "voice_snapshot": record.render_snapshot(v)})
                        v["reference_history"] = history
                    v["commercial_ok"] = record.commercial_ok(v)
                    rec["voice"] = v
                result.append(self.lib.update(npc["id"], apply))
            return result
