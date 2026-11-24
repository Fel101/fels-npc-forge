"""Voice library/history integrity, independent assignment, and frozen render identity."""
import base64
import copy
import hashlib
import sqlite3
from types import SimpleNamespace

import numpy as np
import pytest

from npcgen import roll
from npcgen.library import Library
from npcgen.voice import audio, record, service, steps
from npcgen.voice.voicelibrary import VoiceLibrary


def wav(seconds=6, frequency=220):
    rate = 16000
    samples = .3 * np.sin(2 * np.pi * frequency * np.arange(int(seconds * rate)) / rate)
    return audio.write_wav(samples, rate)


def upload(lib, npc_id, frequency=220):
    return service.apply_upload(lib, lib.get(npc_id), {
        "audio": base64.b64encode(wav(frequency=frequency)).decode(), "text": "This is the exact reference transcript.",
        "consent": True, "speaker": "qwen"})


@pytest.fixture
def studio(tmp_path):
    lib = Library(tmp_path / "library")
    a, b = [lib.create({**roll.roll_npc(roll.load_data(), "fantasy"), "style": "render3d"}) for _ in range(2)]
    upload(lib, a["id"])
    return lib, VoiceLibrary(lib), a["id"], b["id"]


def save(studio):
    lib, store, a, b = studio
    entry = store.from_npc(a, {"name": "Harbor captain", "tags": ["gravelly", "Fantasy", "fantasy"], "version_notes": "Recorded reference"})
    return entry, entry["versions"][0]


def test_named_library_search_favorites_tags_and_immutable_snapshot(studio):
    lib, store, a, b = studio
    entry, version = save(studio)
    assert entry["tags"] == ["gravelly", "Fantasy"] and not version["approved"]
    assert store.list("CAPTAIN", "fantasy")[0]["id"] == entry["id"]
    assert not store.list(favorites=True)
    store.update(entry["id"], {"favorite": True, "notes": "Reusable cast voice"})
    assert store.list(favorites=True)[0]["favorite"]
    original = store.read_audio(version["id"])
    upload(lib, a, 440)
    assert store.read_audio(version["id"]) == original
    assert version["provenance"]["source_reference"]["consent"] is True
    with sqlite3.connect(store.root / "voices.sqlite3") as db:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            db.execute("UPDATE versions SET snapshot='{}' WHERE id=?", (version["id"],))


def test_approval_and_bulk_assignment_preserve_independent_versions(studio):
    lib, store, a, b = studio
    entry, version = save(studio)
    with pytest.raises(ValueError, match="Approve"):
        store.assign(version["id"], [a, b])
    store.approve(version["id"], {"approved": True, "notes": "Audition approved"})
    assigned = store.assign(version["id"], [a, b])
    assert len(assigned) == 2
    for npc in assigned:
        ref = npc["voice"]["reference"]
        assert ref["id"] == version["id"] and ref["file"].startswith("voice/references/")
        assert npc["voice"]["library_binding"]["version_id"] == version["id"]
        assert lib.read_asset(npc["id"], ref["file"]) == store.read_audio(version["id"])
    upload(lib, a, 440)
    revised = store.from_npc(a, {"voice_id": entry["id"]})
    assert len(revised["versions"]) == 2
    assert lib.get(b)["voice"]["reference"]["id"] == version["id"]
    store.update(entry["id"], {"preferred_version": version["id"]})
    assert store.get(entry["id"])["preferred_version"] == version["id"]
    other = store.from_npc(b, {"name": "Other identity"})
    with pytest.raises(ValueError, match="this voice"):
        store.update(other["id"], {"preferred_version": version["id"]})


def test_bulk_assignment_validates_all_targets_before_writing(studio):
    lib, store, a, b = studio
    _, version = save(studio)
    store.approve(version["id"], {"approved": True})
    before = copy.deepcopy(lib.get(b))
    with pytest.raises(KeyError):
        store.assign(version["id"], [b, "missing-npc"])
    assert lib.get(b) == before


def test_trim_creates_unapproved_child_with_transcript_and_parent_provenance(studio):
    lib, store, a, b = studio
    _, version = save(studio)
    original = store.read_audio(version["id"])
    child = store.derive(version["id"], {"start_seconds": 1, "end_seconds": 5, "transcript": "The words in the trimmed region."})
    assert child["number"] == 2 and not child["approved"]
    assert child["provenance"]["parent_version"] == version["id"]
    assert child["quality"]["duration_seconds"] == 4
    assert child["reference"]["text"] == "The words in the trimmed region."
    assert child["reference"]["consent"] is True
    assert store.read_audio(version["id"]) == original
    assert store.get(child["voice_id"])["preferred_version"] == version["id"]
    with pytest.raises(ValueError, match="trim region"):
        store.derive(version["id"], {"start_seconds": float("nan"), "end_seconds": 5, "transcript": "Hello"})
    with pytest.raises(ValueError, match="transcript"):
        store.derive(version["id"], {"start_seconds": 1, "end_seconds": 5, "transcript": ""})


def test_media_checksum_and_invalid_id_are_checked(studio):
    lib, store, a, b = studio
    _, version = save(studio)
    path = store.root / version["reference"]["file"]
    path.write_bytes(wav(frequency=440))
    with pytest.raises(ValueError, match="checksum"):
        store.read_audio(version["id"])
    with pytest.raises(ValueError, match="Invalid"):
        store.get_version("../escape")


def test_reference_history_restores_prior_identity_without_modifying_prior_audio(studio):
    lib, store, a, b = studio
    first = lib.get(a)["voice"]["reference"]
    original = lib.read_asset(a, first["file"])
    upload(lib, a, 440)
    second = lib.get(a)["voice"]["reference"]
    assert second["id"] != first["id"]
    assert lib.read_asset(a, first["file"]) == original
    restored = record.restore_reference(lib, a, first["id"])
    assert restored["voice"]["reference"]["id"] == first["id"]
    assert lib.read_asset(a, second["file"]) != original
    assert lib.read_asset(a, record.REFERENCE) == original
    assert len(restored["voice"]["reference_history"]) == 2


def test_queued_dialogue_uses_frozen_lab_voice_after_assignment_changes(studio, monkeypatch):
    lib, store, a, b = studio
    lib.update(a, lambda n: n.update(dialogue={"lines": {"greeting": [{"id": "hello", "text": "Hello there."}]}}))
    selected = {"name": "Captain", "identity_id": "a" * 32, "model": "qwen3-tts-1.7b-base",
                "kind": "cast", "takes_direction": False}
    npc = lib.update(a, lambda rec: rec["voice"].update(lab_voice=selected))
    request = service.lines_request(npc, {"ids": ["hello"]})
    request["voice_snapshot"] = record.freeze_voice(lib, a, request["voice_snapshot"])
    replacement = {**selected, "identity_id": "b" * 32}
    lib.update(a, lambda rec: rec["voice"].update(lab_voice=replacement))
    seen = []
    def run(job, req):
        seen.append(req)
        return wav(seconds=1)
    monkeypatch.setattr("npcgen.voice.lab_voice.speak", run)
    jobs = SimpleNamespace(lib=lib)
    job = SimpleNamespace(options={"voice": request}, cancel=None)
    steps.line(jobs, job, {"key": "hello"}, lib.get(a))
    assert seen[0]["voice"] == selected
    latest = lib.get(a)
    assert latest["voice_lines"]["hello"]["reference"] == selected["identity_id"]
    assert record.line_is_stale(latest, "hello")
    restored = lib.update(a, lambda rec: rec["voice"].update(lab_voice=selected))
    assert not record.line_is_stale(restored, "hello")


def test_reference_length_quality_blocks_short_cloning_assignment(studio):
    _, store, _, _ = studio
    entry, version = save(studio)
    child = store.derive(version["id"], {"start_seconds": 0, "end_seconds": 1, "transcript": "Hi."})
    assert any("needs at least" in w for w in child["quality"]["warnings"])
    store.approve(child["id"], {"approved": True})
    with pytest.raises(ValueError, match="length"):
        store.assign(child["id"], [studio[3]])
