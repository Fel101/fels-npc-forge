"""Forge voice endpoints with a Voice Lab test double."""
import base64
import importlib
import io
import json
import os
import threading
import time
import urllib.error
import urllib.request
import uuid
import zipfile

import numpy as np
import pytest

from npcgen.voice import audio, engines, lab_client
from npcgen.voice import effects


@pytest.fixture(scope="module")
def api(tmp_path_factory):
    os.environ["NPCGEN_LIBRARY"] = str(tmp_path_factory.mktemp("voice-library"))
    os.environ["NPCGEN_COMFY_URL"] = "http://127.0.0.1:9"
    samples = .2 * np.sin(2 * np.pi * 220 * np.arange(64000) / 16000)
    wav = audio.write_wav(samples, 16000)

    class Lab:
        health = {"lab_id": "voice-api-fixture"}

        def __init__(self):
            self.cast = {}

        def design(self, name, description, **kwargs):
            identity = uuid.uuid4().hex
            voice = {"identity_id": identity, "kind": "cast", "name": name, "model": "qwen3-tts-1.7b-base",
                     "ready": True, "takes_direction": True}
            self.cast[identity] = voice
            return voice

        def voice(self, identity):
            return self.cast[identity]

        def voices(self):
            return list(self.cast.values())

        def preview_design(self, *args, **kwargs):
            return wav

        def speak(self, *args, **kwargs):
            return wav

    lab = Lab()
    changes = pytest.MonkeyPatch()
    changes.setattr(lab_client.LabClient, "discover", lambda: lab)
    server = importlib.import_module("server")
    app = server.App()
    httpd = server.create_server(("127.0.0.1", 0), app)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"

    def call(method, path, body=None, raw=False, headers=None):
        h = {"Content-Type": "application/json"} if method != "GET" else {}
        h.update(headers or {})
        data = json.dumps(body).encode() if body is not None else (b"{}" if method != "GET" else None)
        try:
            with urllib.request.urlopen(urllib.request.Request(base + path, data=data, method=method, headers=h), timeout=60) as r:
                payload = r.read()
                return r.status, (payload if raw else json.loads(payload)), dict(r.headers)
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}"), {}

    call.app = app
    yield call
    httpd.shutdown()
    httpd.server_close()
    changes.undo()
    app.close()


def _new_npc(api, age=28):
    status, npc, _ = api("POST", "/api/roll", {"setting": "modern", "style": "anime"})
    npc["profile"]["age"] = age
    status, saved, _ = api("POST", "/api/npcs", npc)
    assert status == 200, saved
    return saved


def _wait(api, job_id, seconds=60):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        row = next(j for j in api("GET", "/api/jobs")[1] if j["id"] == job_id)
        if row["status"] not in ("queued", "running"):
            return row
        time.sleep(0.1)
    raise AssertionError("the job did not finish")


def test_studio_pronunciation_performance_comparison_and_scene_pins(api):
    npc, partner = _new_npc(api), _new_npc(api)
    root = f"/api/npcs/{npc['id']}/voice"
    settings = {"engine": "qwen", "mode": "design", "description": "An adult harbor guard with a calm clear voice.",
                "script": "Vael is here. The gates are open."}
    assert api("PUT", root, settings)[0] == 200
    status, result, _ = api("POST", root + "/takes", {"count": 2})
    assert status == 200 and _wait(api, result["job"]["id"])["status"] == "done"
    rec = api("GET", f"/api/npcs/{npc['id']}")[1]
    ids = [t["id"] for t in rec["voice"]["takes"]]
    assert api("POST", root + f"/takes/{ids[0]}/select")[0] == 200
    status, rec, _ = api("POST", root + "/comparisons", {"take_ids": ids, "title": "Harbor audition"})
    assert status == 200 and rec["voice"]["comparisons"][0]["script"] == settings["script"]
    comparison = rec["voice"]["comparisons"][0]
    status, rec, _ = api("PUT", root + f"/comparisons/{comparison['id']}/candidates/{ids[1]}", {"rating": 5, "favorite": True})
    assert status == 200 and rec["voice"]["comparisons"][0]["candidates"][1]["rating"] == 5
    assert api("DELETE", root + f"/takes/{ids[1]}")[0] == 400
    status, linked, _ = api("POST", root + "/lab", {"source": "description"})
    assert status == 200 and _wait(api, linked["job"]["id"])["status"] == "done"
    studio = {"pronunciations": [{"term": "Vael", "spoken": "Vay ell"}],
              "presets": [{"id": "guard", "name": "Guard", "direction": "firmly but calmly", "parameters": {"speed": 1.1}}],
              "default_performance": "guard", "line_performances": {}}
    assert api("PUT", root + "/studio", studio)[0] == 200
    status, preview, _ = api("POST", root + "/studio/preview", {"text": "Vael is here."})
    assert status == 200 and preview["authored_text"] == "Vael is here." and preview["synthesis_text"] == "Vay ell is here."
    api.app.lib.update(npc["id"], lambda rec: rec.update(dialogue={"lines": {"greeting": [{"id": "hello", "text": "Vael is here."}]}}))
    status, job, _ = api("POST", root + "/lines", {"ids": ["hello"]})
    assert status == 200 and _wait(api, job["job"]["id"])["status"] == "done"
    rec = api("GET", f"/api/npcs/{npc['id']}")[1]
    assert not rec["voice_lines"]["hello"]["stale"]
    status, scene, _ = api("POST", "/api/scenes", {"title": "Pinned guard", "cast": [npc["id"], partner["id"]],
        "voice_bindings": {npc["id"]: rec["voice"]["lab_voice"]["identity_id"]},
        "beats": [{"id": "guard-line", "kind": "line", "speaker_id": npc["id"], "text": "Vael is here.", "performance_id": "guard"}]})
    assert status == 200, scene
    status, job, _ = api("POST", f"/api/scenes/{scene['id']}/voice", {})
    assert status == 200 and _wait(api, job["job"]["id"])["status"] == "done"
    studio["pronunciations"].append({"term": "Unused", "spoken": "Youzed"})
    assert api("PUT", root + "/studio", studio)[1]["voice_lines"]["hello"]["stale"] is False
    studio["pronunciations"][0]["spoken"] = "Vale"
    assert api("PUT", root + "/studio", studio)[1]["voice_lines"]["hello"]["stale"] is True
    timeline = api("GET", f"/api/scenes/{scene['id']}/timeline")[1]
    assert timeline["beats"][0]["voice"]["source"] == "scene"
    status, payload, _ = api("GET", f"/api/npcs/{npc['id']}/voice-pack.zip", raw=True)
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        manifest = json.loads(archive.read(next(name for name in archive.namelist() if name.endswith('/lines.json'))))
        assert not manifest["lines"]  # Changed pronunciation excludes old audio from the delivery pack.


def test_engines_are_listed_with_what_they_can_do(api):
    status, body, _ = api("GET", "/api/voice/engines")
    ids = [e["id"] for e in body["engines"]]
    assert status == 200 and ids[:4] == ["qwen", "breeze", "kokoro", "chatterbox"] and body["default"] == "qwen"
    breeze = next(e for e in body["engines"] if e["id"] == "breeze")
    assert breeze["commercial_ok"] is False and breeze["caps"]["direction"] is True
    assert body["stock"]["kokoro"] == engines.stock_voices('kokoro')
    assert body["presets"]["voice_types"][0]["id"] == "valley_girl"
    assert api("GET", "/api/health")[1]["voice"]["provider"] == "voice-lab"


def test_a_voice_is_suggested_saved_taken_picked_tried_and_spoken(api):
    npc = _new_npc(api)
    status, sug, _ = api("POST", f"/api/npcs/{npc['id']}/voice/suggest")
    assert status == 200 and sug["description"].startswith(f"A {npc['profile']['age']}-year-old") and sug["adult"] is True
    assert "anime" in sug["description"]

    bad = api("PUT", f"/api/npcs/{npc['id']}/voice", {"engine": "qwen", "mode": "design", "description": "a little girl's voice"})
    assert bad[0] == 400 and "adult" in bad[1]["error"]
    status, saved, _ = api("PUT", f"/api/npcs/{npc['id']}/voice", {"engine": "qwen", "mode": "design", "description": sug["description"],
                                                                   "voice_type": "anime", "chips": {"pitch": "high"}})
    assert status == 200 and saved["voice"]["voice_type"] == "anime"

    status, res, _ = api("POST", f"/api/npcs/{npc['id']}/voice/takes", {"count": 2})
    assert status == 200 and [s["kind"] for s in res["job"]["steps"]] == ["voice_take", "voice_take"]
    assert _wait(api, res["job"]["id"])["status"] == "done"
    rec = api("GET", f"/api/npcs/{npc['id']}")[1]
    takes = rec["voice"]["takes"]
    assert len(takes) == 2 and rec["voice"]["review"] == "pending"

    status, wav, headers = api("GET", f"/api/npcs/{npc['id']}/files/{takes[0]['file']}", raw=True)
    assert status == 200 and headers["Content-Type"] == "audio/wav" and audio.duration(wav) > 1

    picked = api("POST", f"/api/npcs/{npc['id']}/voice/takes/{takes[1]['id']}/select")[1]
    assert picked["voice"]["chosen_take"] == takes[1]["id"] and picked["voice"]["review"] == "picked"
    assert api("GET", "/api/npcs")[1][0]["voice"] in ("picked", "review")

    root = f"/api/npcs/{npc['id']}/voice"
    status, archived, _ = api("GET", f"/api/npcs/{npc['id']}/voice-pack.zip", raw=True)
    assert status == 200 and any(name.endswith("/voice.wav") for name in zipfile.ZipFile(io.BytesIO(archived)).namelist())
    status, linked, _ = api("POST", root + "/lab", {"source": "description"})
    assert status == 200 and _wait(api, linked["job"]["id"])["status"] == "done"
    status, res, _ = api("POST", f"/api/npcs/{npc['id']}/voice/test", {"text": "Try [laugh] this.", "mood": "happy"})
    assert status == 200 and _wait(api, res["job"]["id"])["status"] == "done"
    assert api("GET", f"/api/npcs/{npc['id']}")[1]["voice"]["tests"]["try"]["mood"] == "happy"

    assert api("POST", f"/api/npcs/{npc['id']}/voice/lines")[0] == 400                    # no dialogue pack yet
    api("POST", f"/api/npcs/{npc['id']}/dialogue/generate")
    status, res, _ = api("POST", f"/api/npcs/{npc['id']}/voice/lines", {"ids": ["greeting_1"]})
    assert status == 200 and _wait(api, res["job"]["id"])["status"] == "done"
    rec = api("GET", f"/api/npcs/{npc['id']}")[1]
    assert rec["voice_lines"]["greeting_1"]["file"] == "voice/lines/greeting_1.wav"

    status, blob, headers = api("GET", f"/api/npcs/{npc['id']}/voice-pack.zip", raw=True)
    pack = zipfile.ZipFile(io.BytesIO(blob))
    stem = pack.namelist()[0].split("/")[0]
    assert status == 200 and {f"{stem}/lines/greeting_1.wav", f"{stem}/lines.json", f"{stem}/README.txt",
                              f"{stem}/dialogue.yarn"} <= set(pack.namelist())
    manifest = json.loads(pack.read(f"{stem}/lines.json"))
    game = manifest['lines'][0]['game_playback']
    playback = json.loads(pack.read(stem + '/' + game['clip']))
    assert playback['method'] == 'amplitude_fallback' and playback['status'] == 'amplitude_fallback'
    assert playback['audio_file'] == manifest['lines'][0]['file']
    assert f'{stem}/game/godot/forge_lip_player.gd' in pack.namelist()
    assert f'{stem}/game/unreal/import_curves.py' in pack.namelist()
    assert api('GET', '/api/voice/engines')[1]['game_export_schema'] == 1
    assert manifest["mouth_fps"] == 25 and manifest["lines"][0]["id"] == "greeting_1" and manifest["lines"][0]["mouth"]
    assert manifest["voice"]["commercial_ok"] is True and "#line:greeting_1" in pack.read(f"{stem}/dialogue.yarn").decode()
    exported = json.loads(api("GET", f"/api/npcs/{npc['id']}/dialogue/export/json", raw=True)[1])
    assert exported["lines"]["greeting"][0]["audio"] == "voice/lines/greeting_1.wav"

    status, blob, _ = api("GET", "/api/export.zip?ids=" + npc["id"], raw=True)
    names = zipfile.ZipFile(io.BytesIO(blob)).namelist()
    assert f"{npc['id']}/voice/reference.wav" in names and f"{npc['id']}/voice/lines/greeting_1.wav" in names
    assert not any("/voice/takes/" in n for n in names)

    assert api("DELETE", f"/api/npcs/{npc['id']}/assets/voice_lines/greeting_1")[0] == 200
    assert api("DELETE", f"/api/npcs/{npc['id']}/voice/takes/{takes[0]['id']}")[0] == 200
    gone = api("DELETE", f"/api/npcs/{npc['id']}/assets/voice")[1]
    assert "voice" not in gone and not gone.get("voice_lines")


def test_uploads_need_consent_and_copies_need_a_source_voice(api):
    npc, other = _new_npc(api), _new_npc(api)
    rate = 16000
    t = np.arange(rate * 4) / rate
    clip = audio.write_wav((0.3 * np.sin(2 * np.pi * 200 * t)).astype(np.float32), rate)
    body = {"audio": "data:audio/wav;base64," + base64.b64encode(clip).decode(), "text": "Hello, this is me.", "speaker": "qwen"}
    assert api("POST", f"/api/npcs/{npc['id']}/voice/upload", body)[0] == 400
    status, rec, _ = api("POST", f"/api/npcs/{npc['id']}/voice/upload", {**body, "consent": True})
    assert status == 200 and rec["voice"]["mode"] == "upload"
    assert api("POST", f"/api/npcs/{other['id']}/voice/copy", {"from": other["id"]})[0] == 400
    status, copied, _ = api("POST", f"/api/npcs/{other['id']}/voice/copy", {"from": npc["id"]})
    assert status == 200 and copied["voice"]["reference"]["source"] == f"copy:{npc['id']}"


def test_changing_the_sheet_flags_the_voice_but_keeps_it(api):
    npc = _new_npc(api)
    api("PUT", f"/api/npcs/{npc['id']}/voice", {"engine": "qwen", "mode": "design", "description": "A calm voice."})
    npc = api("GET", f"/api/npcs/{npc['id']}")[1]
    npc["profile"]["personality"] = ["shy"]
    status, updated, _ = api("PUT", f"/api/npcs/{npc['id']}", npc)
    assert status == 200 and updated["voice"]["sheet_changed"] is True and updated["voice"]["description"] == "A calm voice."


def test_batch_voices_characters_and_skips_dialogue_that_is_up_to_date(api):
    actors = [_new_npc(api), _new_npc(api)]
    ids = [actor["id"] for actor in actors]
    bad = api("POST", "/api/voice/batch", {"npc_ids": ids, "engine": "chatterbox"})
    assert bad[0] == 400
    status, res, _ = api("POST", "/api/voice/batch", {"npc_ids": ids, "lines": True})
    assert status == 200 and len(res["jobs"]) == 2 and res["skipped"] == 0
    kinds = [step["kind"] for step in res["jobs"][0]["steps"]]
    assert kinds[0] == "voice_lab_create" and kinds.count("voice_line") >= 10
    for job in res["jobs"]:
        result = _wait(api, job["id"], 120)
        assert result["status"] == "done", result
    for npc_id in ids:
        rec = api("GET", f"/api/npcs/{npc_id}")[1]
        assert rec["voice"]["lab_voice"] and rec["voice"]["review"] == "picked"
        assert len(rec["voice_lines"]) == sum(len(rows) for rows in rec["dialogue"]["lines"].values())
    status, res, _ = api("POST", "/api/voice/batch", {"npc_ids": ids, "lines": True})
    assert status == 200 and res["jobs"] == [] and res["skipped"] == 2


def test_generate_can_give_new_characters_a_voice(api):
    status, res, _ = api("POST", "/api/generate", {"setting": "modern", "count": 1,
                                                   "assets": {"voice": {"engine": "qwen", "takes": 1}}})
    assert status == 200 and [s["kind"] for s in res["jobs"][0]["steps"]] == ["voice_lab_create"]
    assert _wait(api, res["jobs"][0]["id"], 120)["status"] == "done"


def test_reusable_voice_library_approval_assignment_editing_and_export(api):
    source, target = _new_npc(api), _new_npc(api)
    status, _, _ = api("PUT", f"/api/npcs/{source['id']}/voice", {
        "engine": "qwen", "mode": "design", "description": "An adult storyteller.",
        "script": "This reference is long enough for a reusable local character voice."})
    assert status == 200
    _, job, _ = api("POST", f"/api/npcs/{source['id']}/voice/takes", {"count": 1})
    assert _wait(api, job["job"]["id"])["status"] == "done"
    take = api("GET", f"/api/npcs/{source['id']}")[1]["voice"]["takes"][0]
    api("POST", f"/api/npcs/{source['id']}/voice/takes/{take['id']}/select")
    status, saved, _ = api("POST", "/api/voice/library", {"npc_id": source["id"], "name": "Harbor Captain", "tags": ["fantasy", "guard"]})
    assert status == 200, saved
    entry, version = saved["voice"], saved["voice"]["versions"][0]
    assert not version["approved"]
    assert api("POST", f"/api/voice/library/versions/{version['id']}/assign", {"npc_ids": [target["id"]]})[0] == 400
    assert api("PUT", f"/api/voice/library/{entry['id']}", {"favorite": True})[0] == 200
    listed = api("GET", "/api/voice/library?q=CAPTAIN&tag=guard&favorites=1")[1]["voices"]
    assert entry["id"] in {v["id"] for v in listed}
    _, original, headers = api("GET", version["audio_url"], raw=True)
    assert headers["Content-Type"] == "audio/wav"
    assert api("POST", f"/api/voice/library/versions/{version['id']}/approval", {"approved": True})[0] == 200
    status, assigned, _ = api("POST", f"/api/voice/library/versions/{version['id']}/assign", {"npc_ids": [source["id"], target["id"]]})
    assert status == 200 and len(assigned["npcs"]) == 2
    target_record = api("GET", f"/api/npcs/{target['id']}")[1]
    assert target_record["voice"]["library_binding"]["version_id"] == version["id"]
    api("POST", f"/api/npcs/{target['id']}/dialogue/generate")
    status, body, _ = api("POST", f"/api/npcs/{target['id']}/voice/lines", {"ids": ["greeting_1"]})
    assert status == 400 and "Voice Lab" in body["error"]
    _, blob, _ = api("GET", f"/api/npcs/{target['id']}/voice-pack.zip", raw=True)
    with zipfile.ZipFile(io.BytesIO(blob)) as pack:
        name = next(n for n in pack.namelist() if n.endswith("lines.json"))
        manifest = json.loads(pack.read(name))
    assert manifest["voice"]["reference_version"] == version["id"]
    assert manifest["lines"] == []
    status, edited, _ = api("POST", f"/api/voice/library/versions/{version['id']}/derive", {
        "start_seconds": 0, "end_seconds": 3.2, "transcript": "The exact words in this selected region."})
    assert status == 200 and not edited["version"]["approved"]
    assert api("GET", version["audio_url"], raw=True)[1] == original
    assert api("GET", f"/api/npcs/{target['id']}")[1]["voice"]["reference"]["id"] == version["id"]


def test_recording_version_preserves_original_bytes_and_needs_consent(api):
    source = _new_npc(api)
    x = .3 * np.sin(np.arange(6 * 16000) * 2 * np.pi * 220 / 16000)
    raw = audio.write_wav(x, 16000)
    recording = base64.b64encode(raw).decode()
    api("POST", f"/api/npcs/{source['id']}/voice/upload", {"audio": recording, "text": "Exact source words.", "consent": True, "speaker": "qwen"})
    entry = api("POST", "/api/voice/library", {"npc_id": source["id"], "name": "Recorded Actor"})[1]["voice"]
    path = f"/api/voice/library/{entry['id']}/recordings"
    body = {"audio": recording, "transcript": "Exact new recording words.", "speaker": "qwen", "origin": "QA reference"}
    assert api("POST", path, body)[0] == 400
    status, result, _ = api("POST", path, {**body, "consent": True})
    assert status == 200 and result["version"]["reference"]["consent"] is True
    assert api("GET", result["version"]["audio_url"], raw=True)[1] == raw
    assert result["version"]["provenance"]["origin"] == "QA reference"


@pytest.mark.skipif(not effects.capabilities()['available'], reason='Verified local effects runtime unavailable')
def test_cpu_effect_preview_and_studio_defaults_do_not_replace_identity(api):
    source = _new_npc(api)
    wav = audio.write_wav(.2 * np.sin(np.arange(6 * 16000) * 2 * np.pi * 220 / 16000), 16000)
    api('POST', f"/api/npcs/{source['id']}/voice/upload", {'audio': base64.b64encode(wav).decode(),
        'text': 'Exact source words.', 'consent': True, 'speaker': 'qwen'})
    npc = api('GET', f"/api/npcs/{source['id']}")[1]
    endpoint = f"/api/npcs/{source['id']}/voice/effects/preview"
    assert api('POST', endpoint, {'source': '../../npc.json', 'effects': {}})[0] == 400
    status, preview, _ = api('POST', endpoint, {'source': 'reference', 'effects': effects.PRESETS['robot']})
    assert status == 200, preview
    dry = api('GET', preview['original_url'], raw=True)[1]
    wet = api('GET', preview['audio_url'], raw=True)[1]
    assert audio.read_wav(dry)[0].shape == audio.read_wav(wet)[0].shape
    assert dry != wet and api('GET', f"/api/npcs/{source['id']}")[1]['voice'] == npc['voice']
    status, saved, _ = api('PUT', f"/api/npcs/{source['id']}/voice/studio", {'effects': effects.PRESETS['creature'],
        'presets': [{'id': 'normal', 'name': 'Normal delivery', 'effects': effects.PRESETS['dry']}]})
    assert status == 200 and saved['voice']['reference'] == npc['voice']['reference']
    assert saved['voice']['studio']['effects']['pitch_semitones'] == -5
    assert saved['voice']['studio']['presets'][0]['effects']['enabled'] is False


def test_lip_sync_preview_and_disabled_clip_update_preserve_audio_and_voice(api):
    source = _new_npc(api)
    wav = audio.write_wav(.2 * np.sin(np.arange(6 * 16000) * 2 * np.pi * 220 / 16000), 16000)
    api('POST', f"/api/npcs/{source['id']}/voice/upload", {'audio': base64.b64encode(wav).decode(),
        'text': 'A source reference.', 'consent': True, 'speaker': 'qwen'})
    npc = api('GET', f"/api/npcs/{source['id']}")[1]
    endpoint = f"/api/npcs/{source['id']}/voice/lip-sync"
    status, result, _ = api('POST', endpoint + '/preview', {'source': 'reference', 'settings': {'enabled': False}})
    assert status == 200 and result['lip_sync']['status'] == 'amplitude_fallback'
    assert api('GET', result['audio_url'], raw=True)[0] == 200
    assert api('GET', f"/api/npcs/{source['id']}")[1]['voice'] == npc['voice']
    assert api('POST', endpoint + '/preview', {'source': '../../npc.json'})[0] == 400
    status, linked, _ = api('POST', f"/api/npcs/{source['id']}/voice/lab", {'source': 'description'})
    assert status == 200 and _wait(api, linked['job']['id'])['status'] == 'done'
    _, pending, _ = api('POST', f"/api/npcs/{source['id']}/voice/test", {'text': 'The lantern is lit.'})
    assert _wait(api, pending['job']['id'])['status'] == 'done'
    before = api('GET', f"/api/npcs/{source['id']}")[1]
    status, updated, _ = api('POST', endpoint, {'source': 'test:try'})
    assert status == 200 and updated['voice']['tests']['try']['lip_sync']['status'] == 'amplitude_fallback'
    assert updated['voice']['tests']['try']['file'] == before['voice']['tests']['try']['file']
    assert updated['voice']['reference'] == before['voice']['reference']


def test_character_rig_api_preserves_source_voice_and_rejects_invalid_artwork(api):
    from PIL import Image
    npc = _new_npc(api)
    image = io.BytesIO(); Image.new('RGBA',(128,128),'#447788').save(image,'PNG'); data=image.getvalue()
    api.app.lib.write_asset(npc['id'],'portrait.png',data)
    api.app.lib.update(npc['id'],lambda rec:rec['assets'].update(portrait={'file':'portrait.png'}))
    assert api('GET',f"/api/npcs/{npc['id']}/voice/rigs")[1]['portrait']['status']=='unconfigured'
    path=f"/api/npcs/{npc['id']}/voice/rig/portrait"
    before=api('GET',f"/api/npcs/{npc['id']}")[1]
    status,saved,_=api('PUT',path,{'enabled':True})
    assert status==200 and saved.get('voice')==before.get('voice')
    descriptor=api('GET',f"/api/npcs/{npc['id']}/voice/rigs")[1]['portrait']
    assert descriptor['status']=='ready'
    assert api('GET',descriptor['frames']['rest']['url'],raw=True)[0]==200
    status,uploaded,_=api('POST',path+'/frames',{'name':'open','image':base64.b64encode(data).decode()})
    assert status==200 and len(uploaded['voice_rig_versions']['portrait'])==2
    assert api('POST',path+'/frames',{'name':'bad','image':base64.b64encode(b'not an image').decode()})[0]==400
    assert api('PUT',path,{'mapping':{'sil':'../../portrait.png'}})[0]==400
    assert api.app.lib.read_asset(npc['id'],'portrait.png')==data
    assert api('GET','/api/voice/engines')[1]['character_rig_schema']==1


def test_mesh_mapping_api_preserves_model_and_voice_and_rejects_unknown_targets(api):
    import struct
    npc=_new_npc(api)
    document={'asset':{'version':'2.0'},'nodes':[{'name':'jaw'}],'skins':[{'joints':[0]}],
              'meshes':[{'extras':{'targetNames':['viseme_aa']}}]}
    header=json.dumps(document).encode();header+=b' '*((-len(header))%4)
    model=struct.pack('<III',0x46546c67,2,len(header)+20)+struct.pack('<II',len(header),0x4e4f534a)+header
    api.app.lib.write_asset(npc['id'],'rig.glb',model)
    api.app.lib.update(npc['id'],lambda rec:rec['assets'].update(rig_3d={'file':'rig.glb'}))
    path=f"/api/npcs/{npc['id']}/voice/mesh-rig"
    assert api('GET',path)[1]['status']=='unconfigured'
    before=api('GET',f"/api/npcs/{npc['id']}")[1]
    status,saved,_=api('PUT',path,{'jaw_radians':.3})
    assert status==200 and saved.get('voice')==before.get('voice')
    rig=api('GET',path)[1]
    assert rig['status']=='ready' and rig['config']['mapping']['aa']=='viseme_aa'
    assert api('GET',rig['source']['model_url'],raw=True)[1]==model
    assert api('PUT',path,{'jaw_bone':'missing'})[0]==400
    assert api.app.lib.read_asset(npc['id'],'rig.glb')==model
    assert api('GET','/api/voice/engines')[1]['mesh_rig_schema']==1


def test_audio_supports_byte_ranges_for_real_browser_seeking(api):
    source = _new_npc(api)
    wav = audio.write_wav(.2 * np.sin(np.arange(6 * 16000) * 2 * np.pi * 220 / 16000), 16000)
    _, npc, _ = api('POST', f"/api/npcs/{source['id']}/voice/upload", {'audio': base64.b64encode(wav).decode(),
        'text': 'An audio seeking test.', 'consent': True, 'speaker': 'qwen'})
    url = f"/api/npcs/{source['id']}/files/{npc['voice']['reference']['file']}"
    _, full, head = api('GET', url, raw=True)
    status, part, ranged = api('GET', url, raw=True, headers={'Range': 'bytes=0-31'})
    assert status == 206 and part == full[:32] and ranged['Content-Range'] == f'bytes 0-31/{len(full)}'
    assert head['Accept-Ranges'] == 'bytes'
    assert api('GET', url, raw=True, headers={'Range': 'bytes=-8'})[1] == full[-8:]
    assert api('GET', url, raw=True, headers={'Range': 'bytes=0-31', 'If-Range': 'old-etag'})[1] == full
    assert api('GET', url, raw=True, headers={'Range': f'bytes={len(full)}-'})[0] == 416
    assert api('GET', url, raw=True, headers={'Range': 'bytes=0-1,4-8'})[0] == 416
