"""Effective controls, spoken-text separation, targeted invalidation and queued inputs."""
import copy
from types import SimpleNamespace

import numpy as np
import pytest

from npcgen.library import Library
from npcgen.scenes import Scenes, compile_timeline
from npcgen import scene_voice
from npcgen.voice import audio, authoring, lab_voice, record, service, steps


def studio(**updates):
    return authoring.clean_studio({"pronunciations": [{"term": "Vael", "spoken": "Vay ell"}],
        "presets": [{"id": "calm", "name": "Calm", "parameters": {"temperature": .65}}],
        "default_performance": "", "line_performances": {"hello": "calm"}, **updates})


def voice(**updates):
    return {"engine": "qwen", "speaker": "qwen", "mode": "upload", "language": "English",
            "settings": {"temperature": .9, "cfg": 4, "speed": 1}, "studio": studio(),
            "reference": {"id": "a" * 12, "text": "A reference voice", "sha256": "sample"}, **updates}


def test_pronunciation_matches_phrases_once_without_corrupting_tags_or_captions():
    v = voice(studio=studio(pronunciations=[{"term": "Vael Harbor", "spoken": "Vay ell Harbour"},
        {"term": "Vael", "spoken": "Bob"}, {"term": "Bob", "spoken": "Robert"}, {"term": "laugh", "spoken": "chuckle"}]))
    authored = "VAEL HARBOR, Vael and Vaelin meet Bob. [laugh] (laugh) (Vael)"
    spoken, used = authoring.apply_pronunciation(authored, v)
    assert spoken == "Vay ell Harbour, Bob and Vaelin meet Robert. [laugh] (laugh) (Bob)"
    assert len(used) == 3 and authored.startswith("VAEL HARBOR")
    assert authoring.apply_pronunciation("İris", voice(studio=studio(pronunciations=[{"term": "Iris", "spoken": "Eye ris"}])))[0] == "Eye ris"
    with pytest.raises(ValueError, match="unique"):
        studio(pronunciations=[{"term": "Vael", "spoken": "Vay ell"}, {"term": "vael", "spoken": "Bob"}])
    with pytest.raises(ValueError, match="sound tags"):
        studio(pronunciations=[{"term": "Vael", "spoken": "[laugh]"}])


def test_performance_applies_only_adapter_controls_and_keeps_reference_identity():
    original = voice()
    effective, spoken, details = authoring.prepare(original, "Vael is here.", line_id="hello")
    assert effective["reference"] == original["reference"]
    assert original["settings"]["temperature"] == .9 and effective["settings"] == {"temperature": .65}
    assert spoken == "Vay ell is here." and details["synthesis_text"] == spoken
    for engine_id, perf in [("qwen", {"direction": "whisper"}), ("chatterbox", {"parameters": {"cfg": 4}}),
                            ("breeze", {"parameters": {"cfg": 4}})]:
        with pytest.raises(ValueError, match="does not"):
            authoring.validate_performance(perf, engine_id)
    assert authoring.validate_performance({"direction": "quietly", "parameters": {"cfg": 5}}, "breeze")["parameters"]["cfg"] == 5
    for invalid in (float("nan"), float("inf"), True, 9):
        with pytest.raises(ValueError, match="finite"):
            authoring.performance({"parameters": {"temperature": invalid}})


def test_freshness_tracks_used_rules_language_effective_settings_and_performance():
    v = voice()
    row = {"id": "hello", "text": "Vael is here."}
    _, _, details = authoring.prepare(v, row["text"], line_id="hello")
    npc = {"voice": v, "dialogue": {"lines": {"greeting": [row]}},
           "voice_lines": {"hello": {"text": row["text"], "reference": v["reference"]["id"], **details}}}
    assert not record.line_is_stale(npc, "hello")
    v["studio"]["pronunciations"].append({"term": "Unused", "spoken": "Youzed"})
    assert not record.line_is_stale(npc, "hello")
    v["settings"].update(cfg=8, speed=1.5)  # Qwen does not apply these.
    assert not record.line_is_stale(npc, "hello")
    v["studio"]["pronunciations"][0]["spoken"] = "Vale"
    assert record.line_is_stale(npc, "hello")
    v["studio"]["pronunciations"][0]["spoken"] = "Vay ell"
    v["language"] = "French"
    assert record.line_is_stale(npc, "hello")
    v["language"] = "English"
    v["studio"]["presets"][0]["parameters"]["temperature"] = .7
    assert record.line_is_stale(npc, "hello")
    assert authoring.annotate(copy.deepcopy(npc))["voice_lines"]["hello"]["stale"]


def test_legacy_audio_invalidates_for_pronunciation_and_frozen_checksum_does_not_make_it_stale():
    v = voice(studio=studio(pronunciations=[], presets=[], line_performances={}))
    v["reference"].pop("sha256")
    row = {"id": "hello", "text": "Vael is here."}
    frozen = copy.deepcopy(v)
    frozen["reference"]["sha256"] = "verified-on-queue"
    _, _, details = authoring.prepare(frozen, row["text"], line_id="hello")
    npc = {"voice": v, "dialogue": {"lines": {"greeting": [row]}},
           "voice_lines": {"hello": {"text": row["text"], "reference": v["reference"]["id"], **details}}}
    assert not record.line_is_stale(npc, "hello")
    npc["voice_lines"]["hello"].pop("render_signature")
    assert not record.line_is_stale(npc, "hello")
    v["studio"]["pronunciations"] = [{"term": "Vael", "spoken": "Vay ell"}]
    assert record.line_is_stale(npc, "hello")


@pytest.fixture
def local(tmp_path, monkeypatch):
    lib = Library(tmp_path / "library")
    npc = lib.create({"profile": {"first_name": "Actor", "last_name": "Test", "age": 30}, "setting": "fantasy", "assets": {}})
    other = lib.create({"profile": {"first_name": "Other", "last_name": "Test", "age": 30}, "setting": "fantasy", "assets": {}})
    samples = .2 * np.sin(2 * np.pi * 220 * np.arange(96000) / 16000)
    wav = audio.write_wav(samples, 16000)
    def apply(rec):
        v = voice(reference=None, studio=studio(presets=[{"id": "calm", "name": "Calm", "parameters": {"speed": 1.15}}]),
                  lab_voice={"identity_id": "a" * 12, "model": "qwen3-tts-1.7b-base", "name": "Actor Test",
                             "kind": "cast", "takes_direction": False})
        record.install_reference(lib, rec, v, wav, {"id": "a" * 12, "text": "A reference voice", "language": "English"})
        rec.update(voice=v, dialogue={"lines": {"greeting": [{"id": "hello", "text": "Vael is here."}]}})
    lib.update(npc["id"], apply)
    seen = []
    def run(job, req):
        seen.append(copy.deepcopy(req))
        return wav
    monkeypatch.setattr(lab_voice, "speak", run)
    jobs = SimpleNamespace(lib=lib, scenes=Scenes(lib))
    return lib, jobs, npc["id"], other["id"], seen


def test_queued_dialogue_keeps_authored_text_dictionary_and_per_line_performance(local):
    lib, jobs, npc_id, _, seen = local
    request = service.lines_request(lib.get(npc_id), {"ids": ["hello"]})
    request["voice_snapshot"] = record.freeze_voice(lib, npc_id, request["voice_snapshot"])
    def change(rec):
        rec["dialogue"]["lines"]["greeting"][0]["text"] = "The gate is closed."
        rec["voice"]["studio"]["pronunciations"][0]["spoken"] = "Vale"
    lib.update(npc_id, change)
    steps.line(jobs, SimpleNamespace(options={"voice": request}, cancel=None), {"key": "hello"}, lib.get(npc_id))
    saved = lib.get(npc_id)
    assert seen[0]["text"] == "Vay ell is here." and seen[0]["speed"] == 1.15
    assert saved["voice_lines"]["hello"]["text"] == "Vael is here."
    assert saved["dialogue"]["lines"]["greeting"][0]["text"] == "The gate is closed."
    assert record.line_is_stale(saved, "hello")


def test_scene_pin_preserves_settings_and_pronunciation_after_character_changes(local):
    lib, jobs, npc_id, other_id, seen = local
    scene = jobs.scenes.create({"title": "Pinned performance", "cast": [npc_id, other_id],
        "voice_bindings": {npc_id: "a" * 12}, "beats": [{"id": "beat-one", "kind": "line", "speaker_id": npc_id,
            "text": "Vael is here.", "performance_id": "calm"}]})
    request = scene_voice.requests(scene, jobs.scenes, {})
    def change(rec):
        rec["voice"]["studio"]["pronunciations"][0]["spoken"] = "Vale"
        rec["voice"]["settings"]["temperature"] = 1.2
    lib.update(npc_id, change)
    scene_voice.run(jobs, SimpleNamespace(scene_id=scene["id"], options={"scene_voice": {"beats": request}}, cancel=None), {"key": "beat-one"})
    saved = jobs.scenes.get(scene["id"])
    assert seen[0]["text"] == "Vay ell is here." and seen[0]["speed"] == 1.15
    assert compile_timeline(saved, lib, jobs.scenes)["beats"][0]["voice"]["source"] == "scene"
    forged = copy.deepcopy(saved)
    forged["voice_bindings"][npc_id]["snapshot"]["settings"]["temperature"] = .3
    assert jobs.scenes.update(scene["id"], forged)["voice_bindings"] == saved["voice_bindings"]
    saved = jobs.scenes.get(scene["id"])
    jobs.scenes.update(scene["id"], {**saved, "voice_bindings": {}, "beats": [{**saved["beats"][0], "performance_id": None}]})
    assert compile_timeline(jobs.scenes.get(scene["id"]), lib, jobs.scenes)["beats"][0]["voice"] is None
