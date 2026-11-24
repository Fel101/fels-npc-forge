"""Reference-free identities, immutable library versions, scene pins and delivery."""
import base64
import hashlib
import io
import json
from types import SimpleNamespace
import zipfile

import numpy as np
import pytest

from npcgen.library import Library
from npcgen.scenes import Scenes, compile_timeline
from npcgen import scene_voice
from npcgen.voice import audio, authoring, engines, lab_voice, pack, record, service, steps
from npcgen.voice.voicelibrary import VoiceLibrary


@pytest.fixture
def actors(tmp_path, monkeypatch):
    original = engines.stock_voices
    monkeypatch.setattr(engines, "stock_voices", lambda engine_id, cfg=None:
        [{"id": "Ryan"}, {"id": "Aiden"}] if engine_id == "qwen_custom" else original(engine_id))
    lib = Library(tmp_path / "library")
    ids = [lib.create({"profile": {"first_name": name, "last_name": "QA", "age": 30}, "setting": "fantasy", "assets": {}})["id"]
           for name in ("One", "Two")]
    def select(npc):
        record.save_settings(npc, record.clean_settings({"engine": "qwen_custom", "mode": "stock", "stock_voice": "Ryan"}, npc), lib)
        npc["dialogue"] = {"lines": {"greeting": [{"id": "hello", "text": "The forge is open."}]}}
    lib.update(ids[0], select)
    return lib, VoiceLibrary(lib), ids


def test_preset_identity_requires_no_reference_and_rejects_cloner_or_unknown_speaker(actors):
    lib, store, ids = actors
    npc = lib.get(ids[0])
    assert not npc["voice"].get("reference") and record.speaker_for(npc["voice"]) == "qwen_custom"
    request = service.lines_request(npc, {"ids": ["hello"]})
    frozen = record.freeze_voice(lib, ids[0], request["voice_snapshot"])
    effective, text, meta = authoring.prepare(frozen, "Hello", override={"direction": "Calm and firm", "parameters": {"top_k": 30}})
    engine, submitted = steps.speak_request(npc, effective, text, "neutral", 123)
    assert engine == "qwen_custom" and submitted["cmd"] == "stock" and submitted["voice"] == "Ryan"
    assert not {"ref", "ref_text"}.intersection(submitted) and submitted["direction"] == "Calm and firm"
    with pytest.raises(ValueError, match="Unknown stock"):
        record.clean_settings({"stock_voice": "invented"}, npc)
    with pytest.raises(ValueError, match="renders directly"):
        record.clean_settings({"speaker": "qwen"}, npc)
    assert authoring.capabilities("chatterbox_original")["direction"] is False
    with pytest.raises(ValueError, match="does not apply"):
        authoring.validate_performance({"parameters": {"speed": 1.2}}, "chatterbox_original")


def test_reference_free_library_assignment_restore_and_voice_pack(actors):
    lib, store, ids = actors
    entry = store.from_npc(ids[0], {"name": "Ryan identity"})
    version = entry["versions"][0]
    assert version["audio_url"] is None and not version["reference"].get("file")
    assert version["sha256"] == hashlib.sha256(json.dumps(version["snapshot"], sort_keys=True).encode()).hexdigest()
    store.approve(version["id"], {"approved": True})
    assigned = store.assign(version["id"], ids)
    assert all(record.identity_id(npc["voice"]) == version["id"] and not npc["voice"].get("reference") for npc in assigned)
    lib.update(ids[1], lambda rec: record.save_settings(rec, record.clean_settings({"stock_voice": "Aiden"}, rec), lib))
    assert not lib.get(ids[1])["voice"].get("library_binding")
    restored = record.restore_reference(lib, ids[1], version["id"])
    assert restored["voice"]["stock_voice"] == "Ryan" and record.identity_id(restored["voice"]) == version["id"]
    payload, _ = pack.build(lib, restored)
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        assert not any(name.endswith('/voice.wav') for name in archive.namelist())
        manifest = json.loads(archive.read(next(name for name in archive.namelist() if name.endswith('/lines.json'))))
        assert manifest["voice"]["preset_identity"]["reference_required"] is False
    with pytest.raises(ValueError, match="Trimming a preset"):
        store.derive(version["id"], {"start_seconds": 0, "end_seconds": 1, "transcript": "Hello"})
    copied = service.apply_copy(lib, lib.get(ids[1]), lib.get(ids[0]))
    assert record.is_preset(copied["voice"]) and not copied["voice"].get("reference")


def test_recording_can_replace_preset_without_leaving_an_invalid_stock_speaker(actors):
    lib, store, ids = actors
    entry = store.from_npc(ids[0], {"name": "Native identity"})
    version_id = entry["versions"][0]["id"]
    store.approve(version_id, {"approved": True})
    store.assign(version_id, ids)
    wave = audio.write_wav(.2 * np.sin(2 * np.pi * 220 * np.arange(96000) / 16000), 16000)
    body = {"audio": base64.b64encode(wave).decode(), "consent": True,
            "text": "These are the exact recording words.", "speaker": "qwen"}
    uploaded = service.apply_upload(lib, lib.get(ids[0]), body)
    assert uploaded["voice"]["stock_voice"] is None
    assert not uploaded["voice"].get("preset_identity")
    assert record.clean_settings(uploaded["voice"], uploaded)["mode"] == "upload"
    recorded = store.recording(entry["id"], {**body, "transcript": body["text"]})
    assert recorded["snapshot"]["stock_voice"] is None
    store.approve(recorded["id"], {"approved": True})
    assigned = store.assign(recorded["id"], [ids[1]])[0]
    assert not record.is_preset(assigned["voice"])
    restored = record.restore_reference(lib, ids[0], version_id)
    assert record.identity_id(restored["voice"]) == version_id
    assert not restored["voice"].get("reference")


def test_preset_audition_is_optional_media_and_never_conditioning(actors):
    lib, store, ids = actors
    npc = lib.get(ids[0])
    version_id = record.identity_id(npc["voice"])
    wave = audio.write_wav(.2 * np.sin(2 * np.pi * 220 * np.arange(32000) / 16000), 16000)
    lib.write_asset(ids[0], "voice/takes/audition.wav", wave)
    take = {"id": "audition", "file": "voice/takes/audition.wav", "engine": "qwen_custom", "mode": "stock",
            "stock_voice": "Ryan", "text": "A short audition.", "language": "English", "seconds": 2,
            "voice_snapshot": record.render_snapshot(npc["voice"])}
    lib.update(ids[0], lambda rec: rec["voice"].update(takes=[take]))
    selected = record.select_take(lib, ids[0], "audition")
    v = selected["voice"]
    assert record.identity_id(v) == version_id and v["reference"]["kind"] == "audition"
    engine, request = steps.speak_request(selected, record.freeze_voice(lib, ids[0], v), "Hello", "neutral", 123)
    assert engine == "qwen_custom" and "ref" not in request
    payload, _ = pack.build(lib, selected)
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        manifest = json.loads(archive.read(next(name for name in archive.namelist() if name.endswith('/lines.json'))))
        assert manifest["voice"]["file_role"] == "audition"
    entry = store.from_npc(ids[0], {"name": "Preset with audition"})
    assert entry["versions"][0]["reference"]["kind"] == "audition"


def test_preset_dialogue_and_scene_generation_keep_direct_identity_after_change(actors, monkeypatch):
    lib, store, ids = actors
    scenes, seen = Scenes(lib), []
    def run(job, request):
        seen.append(request)
        return audio.write_wav(.2 * np.sin(2 * np.pi * 220 * np.arange(32000) / 16000), 16000)
    monkeypatch.setattr(lab_voice, "speak", run)
    jobs = SimpleNamespace(lib=lib, scenes=scenes)
    npc = lib.get(ids[0]); request = service.lines_request(npc, {})
    steps.line(jobs, SimpleNamespace(options={"voice": request}, cancel=None), {"key": "hello"}, npc)
    assert not record.line_is_stale(lib.get(ids[0]), "hello")
    active_id = record.identity_id(npc["voice"])
    scene = scenes.create({"title": "Native preset", "cast": ids, "voice_bindings": {ids[0]: active_id},
        "beats": [{"id": "native-line", "kind": "line", "speaker_id": ids[0], "text": "The lantern is lit."}]})
    queued = scene_voice.requests(scene, scenes, {})
    lib.update(ids[0], lambda rec: record.save_settings(rec, record.clean_settings({"stock_voice": "Aiden"}, rec), lib))
    scene_voice.run(jobs, SimpleNamespace(scene_id=scene["id"], options={"scene_voice": {"beats": queued}}, cancel=None), {"key": "native-line"})
    assert seen[-1]["voice"] == "Ryan" and "ref" not in seen[-1]
    assert compile_timeline(scenes.get(scene["id"]), lib, scenes)["beats"][0]["voice"]
    assert record.line_is_stale(lib.get(ids[0]), "hello")
