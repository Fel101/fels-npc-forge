"""Scene storage, timeline and CPU export with tiny local fixtures; no ComfyUI calls."""
import io
import json
import copy
import math
import os
import struct
import subprocess
import sys
import threading
import time
import urllib.request
import wave
import zipfile

import pytest
from PIL import Image

from npcgen.library import Library
from npcgen.scene_render import FrameMaker, ffmpeg_path, render
from npcgen.scene_export import make_zip
from npcgen.scene_recording import MAGIC, publish
from npcgen.scene_voice import requests as voice_requests
from npcgen.scenes import Scenes, clean_scene, compile_timeline


def _png(color):
    out = io.BytesIO()
    Image.new("RGB", (120, 120), color).save(out, format="PNG")
    return out.getvalue()


def _wav():
    out = io.BytesIO()
    with wave.open(out, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(24000)
        wav.writeframes(b"\0\0" * 12000)
    return out.getvalue()


def _tone():
    out = io.BytesIO()
    with wave.open(out, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(24000)
        samples = (int(10000 * math.sin(2 * math.pi * 220 * index / 24000)) for index in range(12000))
        wav.writeframes(b"".join(struct.pack("<h", sample) for sample in samples))
    return out.getvalue()


@pytest.fixture
def sample(tmp_path):
    lib = Library(tmp_path / "library")
    ids = []
    for first, color in [("Ada", "#b87268"), ("Bo", "#4679a6")]:
        npc = lib.create({"profile": {"first_name": first, "last_name": "Test", "age": 30},
                          "appearance": {}, "setting": "modern", "style": "pixel", "assets": {}})
        lib.write_asset(npc["id"], "portrait.png", _png(color))
        lib.update(npc["id"], lambda rec: rec["assets"].update(portrait={"file": "portrait.png", "created": "now"}))
        ids.append(npc["id"])
    raw = {"title": "At the gate", "cast": ids, "aspect": "landscape",
           "beats": [{"id": "beat-one", "kind": "line", "speaker_id": ids[0], "text": "Hello there.",
                      "layout": "split", "line_id": "greeting-one"}]}
    return lib, Scenes(lib), raw


def test_scene_store_revisions_and_validation(sample):
    lib, scenes, raw = sample
    scene = scenes.create(raw)
    assert scenes.get(scene["id"])["beats"][0]["text"] == "Hello there."
    assert len(scenes.list()) == 1
    changed = scenes.update(scene["id"], {**scene, "title": "The gate opens"})
    assert changed["revision"] == 2
    with pytest.raises(ValueError, match="changed since"):
        scenes.update(scene["id"], scene)
    with pytest.raises(ValueError, match="two different"):
        clean_scene({**raw, "cast": [raw["cast"][0]] * 2}, lib)
    with pytest.raises(ValueError, match="unknown layout"):
        clean_scene({**raw, "beats": [{**raw["beats"][0], "layout": "../escape"}]}, lib)
    with pytest.raises(KeyError):
        scenes.resolve(scene["id"], "../other-scene/scene.json")
    scenes.delete(scene["id"])
    assert scenes.list() == []


def test_existing_voice_is_used_only_while_fresh(sample):
    lib, scenes, raw = sample
    npc_id = raw["cast"][0]
    lib.write_asset(npc_id, "voice/lines/greeting-one.wav", _wav())
    def add_voice(npc):
        npc["dialogue"] = {"lines": {"greeting": [{"id": "greeting-one", "text": "Hello there."}]}}
        npc["voice"] = {"reference": {"id": "voice-1"}}
        npc["voice_lines"] = {"greeting-one": {"file": "voice/lines/greeting-one.wav", "text": "Hello there.",
                                                "reference": "voice-1", "seconds": 0.5, "mouth": [0.9] * 13}}
    lib.update(npc_id, add_voice)
    scene = scenes.create(raw)
    timeline = compile_timeline(scene, lib)
    assert timeline["beats"][0]["voice"]["mouth"] == [0.9] * 13
    assert timeline["frames"] >= 40
    assert FrameMaker(scene, timeline, lib).frame(0).size == (1280, 720)
    lib.update(npc_id, lambda npc: npc["voice"]["reference"].update(id="voice-2"))
    stale = compile_timeline(scene, lib)
    assert stale["beats"][0]["voice"] is None
    assert any("outdated" in warning for warning in stale["warnings"])


def test_close_shot_frames_top_of_tall_portrait(sample):
    lib, scenes, raw = sample
    portrait = Image.new("RGB", (120, 400), "#4679a6")
    portrait.paste("#b87268", (0, 0, 120, 100))
    output = io.BytesIO()
    portrait.save(output, format="PNG")
    lib.write_asset(raw["cast"][0], "portrait.png", output.getvalue())
    raw["beats"][0]["layout"] = "close"
    scene = scenes.create(raw)
    frame = FrameMaker(scene, compile_timeline(scene, lib), lib).frame(0)
    assert frame.getpixel((400, 350)) == (184, 114, 104)


def test_scene_voice_job_uses_scene_storage_and_marks_changes_stale(sample, monkeypatch):
    from npcgen.jobs import Jobs
    from npcgen.voice.runtime import VoiceRuntime

    class DummyComfy:
        models = {}

    lib, scenes, raw = sample
    npc_id = raw["cast"][0]
    lib.update(npc_id, lambda npc: npc.update(voice={"engine": "kokoro", "mode": "stock",
                                                    "speaker": "kokoro", "stock_voice": "af_heart"}))
    scene = scenes.create(raw)
    def fake_run(self, engine_id, request, output, cancel=None):
        assert engine_id == "kokoro" and request["text"] == "Hello there."
        output.write_bytes(_tone())
        return {}
    monkeypatch.setattr(VoiceRuntime, "run", fake_run)
    monkeypatch.setattr("npcgen.jobs.engines.release", lambda comfy: None)
    monkeypatch.setattr("npcgen.jobs.gpu.release_comfy", lambda comfy: None)
    jobs = Jobs({}, DummyComfy(), lib, scenes=scenes)
    try:
        queued = jobs.submit_scene_voice(scene["id"], {"ids": ["beat-one"]})
        for _ in range(300):
            result = next(row for row in jobs.snapshot() if row["id"] == queued["id"])
            if result["status"] not in ("queued", "running"):
                break
            time.sleep(0.01)
        assert result["status"] == "done", result["error"]
        saved = scenes.get(scene["id"])
        assert saved["audio"]["beat-one"]["file"].startswith("audio/beat-one-")
        assert compile_timeline(saved, lib, scenes)["beats"][0]["voice"]["source"] == "scene"
        assert not lib.get(npc_id).get("voice_lines")
        changed = scenes.update(scene["id"], {**saved, "beats": [{**saved["beats"][0], "text": "Different words."}]})
        assert compile_timeline(changed, lib, scenes)["beats"][0]["voice"] is None
    finally:
        jobs.close()


def test_scene_voice_retry_uses_current_line(sample, monkeypatch):
    from npcgen.jobs import Jobs
    from npcgen.voice.runtime import VoiceError, VoiceRuntime

    class DummyComfy:
        models = {}

    lib, scenes, raw = sample
    lib.update(raw["cast"][0], lambda npc: npc.update(voice={"engine": "kokoro", "mode": "stock",
                                                          "speaker": "kokoro", "stock_voice": "af_heart"}))
    scene = scenes.create(raw)
    spoken = []

    def fake_run(self, engine_id, request, output, cancel=None):
        spoken.append(request["text"])
        if len(spoken) == 1:
            raise VoiceError("Temporary voice failure")
        output.write_bytes(_tone())
        return {}

    monkeypatch.setattr(VoiceRuntime, "run", fake_run)
    monkeypatch.setattr("npcgen.jobs.engines.release", lambda comfy: None)
    monkeypatch.setattr("npcgen.jobs.gpu.release_comfy", lambda comfy: None)
    jobs = Jobs({}, DummyComfy(), lib, scenes=scenes)
    try:
        queued = jobs.submit_scene_voice(scene["id"], {"ids": ["beat-one"]})
        for _ in range(300):
            state = next(row for row in jobs.snapshot() if row["id"] == queued["id"])
            if state["status"] not in ("queued", "running"):
                break
            time.sleep(0.01)
        assert state["status"] == "error"
        scenes.update(scene["id"], {**scene, "beats": [{**scene["beats"][0], "text": "A newer line."}]})
        jobs.retry(queued["id"])
        for _ in range(300):
            state = next(row for row in jobs.snapshot() if row["id"] == queued["id"])
            if state["status"] not in ("queued", "running"):
                break
            time.sleep(0.01)
        assert state["status"] == "done", state["error"]
        assert spoken == ["Hello there.", "A newer line."]
        assert scenes.get(scene["id"])["audio"]["beat-one"]["text"] == "A newer line."
    finally:
        jobs.close()


def test_reaction_and_backdrop_are_validated(sample):
    lib, scenes, raw = sample
    raw["backdrop"] = "forest"
    raw["beats"].append({"id": "beat-react", "kind": "reaction", "speaker_id": raw["cast"][1],
                         "duration": 1.5, "layout": "close", "expression": "surprised"})
    scene = scenes.create(raw)
    timeline = compile_timeline(scene, lib, scenes)
    assert timeline["beats"][1]["frames"] == 38
    assert FrameMaker(scene, timeline, lib).frame(timeline["beats"][1]["start"]).size == (1280, 720)


def test_3d_stage_keeps_background_and_blocking(sample):
    lib, scenes, raw = sample
    raw.update(mode="stage3d", background_prompt="An empty forest gate")
    raw["beats"][0].update(motion="walk", walk_to=[0.4, -0.2])
    scene = scenes.create(raw)
    assert scene["beats"][0]["walk_to"] == [0.4, -0.2]
    assert "needs a rigged 3D model" in " ".join(compile_timeline(scene, lib, scenes)["warnings"])
    scenes.write_media(scene["id"], "background/0123456789.png", _png("#123456"))
    assert scenes.set_background(scene["id"], raw["background_prompt"], "background/0123456789.png")
    with_background = scenes.get(scene["id"])
    before = compile_timeline(with_background, lib, scenes)["fingerprint"]
    scenes.write_media(scene["id"], "background/0123456789.png", _png("#abcdef"))
    assert compile_timeline(with_background, lib, scenes)["fingerprint"] != before
    with pytest.raises(ValueError, match="walk target"):
        clean_scene({**raw, "beats": [{**raw["beats"][0], "walk_to": [100, 0]}]}, lib)


def test_scene_stock_voice_does_not_change_npc(sample):
    lib, scenes, raw = sample
    raw.update(mode="stage3d", background_prompt="An empty station",
               voice_overrides={raw["cast"][0]: "af_heart"})
    scene = scenes.create(raw)
    request = voice_requests(scene, scenes, {"ids": ["beat-one"]})["beat-one"]
    assert request["stock_voice"] == "af_heart"
    assert not lib.get(raw["cast"][0]).get("voice")
    with pytest.raises(ValueError, match="valid scene voice"):
        clean_scene({**raw, "voice_overrides": {raw["cast"][0]: "not-a-voice"}}, lib)


@pytest.mark.skipif(not ffmpeg_path(), reason="FFmpeg is optional")
def test_browser_3d_frames_are_published_at_exact_frame_rate(sample):
    lib, scenes, raw = sample
    for npc_id in raw["cast"]:
        lib.write_asset(npc_id, "rig.glb", b"test rig")
        lib.update(npc_id, lambda npc: npc["assets"].update(rig_3d={"file": "rig.glb"}))
    raw.update(mode="stage3d", background_prompt="A quiet street")
    scene = scenes.create(raw)
    scenes.write_media(scene["id"], "background/0123456789.png", _png("#abcdef"))
    scenes.set_background(scene["id"], raw["background_prompt"], "background/0123456789.png")
    scene = scenes.get(scene["id"])
    timeline = compile_timeline(scene, lib, scenes)
    jpeg = io.BytesIO()
    Image.new("RGB", timeline["size"], "#123456").save(jpeg, format="JPEG")
    frame = struct.pack("<I", len(jpeg.getvalue())) + jpeg.getvalue()
    packet = MAGIC + struct.pack("<I", timeline["frames"]) + frame * timeline["frames"]
    with pytest.raises(ValueError, match="Expected"):
        publish(scenes, scene["id"], scene["revision"], timeline["fingerprint"], packet[:8] +
                struct.pack("<I", timeline["frames"] - 1) + packet[12:])
    result = publish(scenes, scene["id"], scene["revision"], timeline["fingerprint"], packet)
    assert b"ftyp" in scenes.resolve(scene["id"], result["file"]).read_bytes()[:100]
    assert result["fingerprint"] == timeline["fingerprint"]
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                            "-show_entries", "stream=nb_frames,avg_frame_rate", "-of", "json",
                            str(scenes.resolve(scene["id"], result["file"]))],
                           capture_output=True, text=True, check=True)
    stream = json.loads(probe.stdout)["streams"][0]
    assert int(stream["nb_frames"]) == timeline["frames"]
    assert stream["avg_frame_rate"] == "25/1"
    archive = zipfile.ZipFile(io.BytesIO(make_zip(scenes.get(scene["id"]), scenes, "Local notice")))
    assert "background/0123456789.png" in archive.namelist()


def test_expression_file_change_marks_render_outdated(sample):
    lib, scenes, raw = sample
    npc_id = raw["cast"][0]
    lib.write_asset(npc_id, "expressions/happy.png", _png("#ffeb00"))
    lib.update(npc_id, lambda rec: rec["assets"].update(expressions={
        "happy": {"file": "expressions/happy.png", "created": "now"}}))
    scene = scenes.create({**raw, "beats": [{**raw["beats"][0], "expression": "happy"}]})
    before = compile_timeline(scene, lib, scenes)["fingerprint"]
    lib.write_asset(npc_id, "expressions/happy.png", _png("#00ebff"))
    assert compile_timeline(scene, lib, scenes)["fingerprint"] != before


def test_character_mouth_frames_render_fallback_and_invalidate_only_scene_video(sample):
    from npcgen.voice import character_rig
    lib,scenes,raw=sample
    npc_id=raw['cast'][0]
    lib.write_asset(npc_id,'voice/lines/greeting-one.wav',_wav())
    def setup(npc):
        npc['dialogue']={'lines':{'greeting':[{'id':'greeting-one','text':'Hello there.'}]}}
        npc['voice']={'reference':{'id':'voice-1'}}
        npc['voice_lines']={'greeting-one':{'file':'voice/lines/greeting-one.wav','text':'Hello there.','reference':'voice-1','seconds':.5,'mouth':[.9]*13}}
    lib.update(npc_id,setup)
    character_rig.save(lib,npc_id,'portrait',{})
    import base64
    character_rig.upload(lib,npc_id,'portrait',{'name':'open','image':base64.b64encode(_png('red')).decode()})
    rig=lib.get(npc_id)['voice_rigs']['portrait']
    character_rig.save(lib,npc_id,'portrait',{'amplitude_open':'open'})
    scene=scenes.create(raw); timeline=compile_timeline(scene,lib,scenes)
    assert timeline['beats'][0]['voice']['visual_method']=='amplitude_frames'
    maker=FrameMaker(scene,timeline,lib)
    assert maker.frame(1).getpixel((250,250))[0]>200
    before=timeline['fingerprint']; metadata=lib.get(npc_id)['voice_lines']
    character_rig.save(lib,npc_id,'portrait',{'enabled':False})
    updated=compile_timeline(scene,lib,scenes)
    assert updated['fingerprint']!=before and updated['beats'][0]['voice']
    assert updated['beats'][0]['voice']['visual_method']=='amplitude_fallback'
    assert lib.get(npc_id)['voice_lines']==metadata


def test_scene_fingerprint_is_stable_across_python_hash_seeds(sample):
    lib,scenes,raw=sample
    lib.update(raw['cast'][0],lambda npc:npc['assets'].update(living={'file':'living/living.json'}))
    scene=scenes.create(raw)
    script="""import sys
from pathlib import Path
from npcgen.library import Library
from npcgen.scenes import Scenes,compile_timeline
lib=Library(Path(sys.argv[1]));scenes=Scenes(lib)
print(compile_timeline(scenes.get(sys.argv[2]),lib,scenes)['fingerprint'])
"""
    values=[]
    for seed in ('3','17'):
        result=subprocess.run([sys.executable,'-c',script,str(lib.root),scene['id']],env={**os.environ,'PYTHONHASHSEED':seed},
            capture_output=True,text=True,check=True,timeout=30)
        values.append(result.stdout.strip())
    assert values[0]==values[1]


@pytest.mark.skipif(not ffmpeg_path(), reason="FFmpeg is optional")
def test_cpu_mp4_export(sample):
    lib, scenes, raw = sample
    scene = scenes.create(raw)
    filename = render(scene, scenes)
    path = scenes.resolve(scene["id"], filename)
    assert path.is_file() and path.stat().st_size > 1000
    assert b"ftyp" in path.read_bytes()[:100]


@pytest.mark.skipif(not ffmpeg_path(), reason="FFmpeg is optional")
def test_vertical_export_package(sample):
    lib, scenes, raw = sample
    scene = scenes.create({**raw, "aspect": "portrait"})
    timeline = compile_timeline(scene, lib, scenes)
    filename = render(scene, scenes, timeline=timeline)
    scenes.set_render(scene["id"], scene["revision"], filename, timeline["fingerprint"])
    archive = zipfile.ZipFile(io.BytesIO(make_zip(scenes.get(scene["id"]), scenes, "Local notice")))
    assert set(archive.namelist()) == {"scene.mp4", "scene.json", "sources.json", "NOTICE.md",
        "game/README.md", "game/targets.json", "game/godot/forge_lip_player.gd", "game/godot/forge_mesh_mouth.gd", "game/unreal/import_curves.py"}
    assert b"ftyp" in archive.read("scene.mp4")[:100]
    assert json.loads(archive.read("sources.json"))["render_outdated"] is False
    assert json.loads(archive.read("scene.json"))["aspect"] == "portrait"
    assert archive.read("NOTICE.md") == b"Local notice"


def test_scene_api_create_preview_and_render_job(sample):
    import server
    from npcgen.config import DEFAULTS

    lib, scenes, raw = sample
    cfg = copy.deepcopy(DEFAULTS)
    cfg.update(library_path=lib.root, comfy_url="http://127.0.0.1:9")
    app = server.App(cfg)
    httpd = server.create_server(("127.0.0.1", 0), app)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"

    def request(method, path, payload=None):
        data = json.dumps(payload).encode() if payload is not None else (b"{}" if method != "GET" else None)
        req = urllib.request.Request(base + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"} if data is not None else {})
        with urllib.request.urlopen(req, timeout=20) as response:
            return response.read()

    try:
        assert "mp4" in json.loads(request("GET", "/api/scenes/capabilities"))
        preview = json.loads(request("POST", "/api/scenes/preview", raw))
        assert preview["beats"][0]["text"] == "Hello there."
        assert json.loads(request("GET", "/api/scenes")) == []
        scene = json.loads(request("POST", "/api/scenes", raw))
        timeline = json.loads(request("GET", f"/api/scenes/{scene['id']}/timeline"))
        assert timeline["beats"][0]["text"] == "Hello there."
        assert len(json.loads(request("GET", "/api/scenes"))) == 1
        revised_preview = json.loads(request("POST", "/api/scenes/preview",
                                             {**scene, "beats": [{**scene["beats"][0], "text": "Unsaved words."}]}))
        assert revised_preview["beats"][0]["text"] == "Unsaved words."
        assert json.loads(request("GET", f"/api/scenes/{scene['id']}"))["revision"] == scene["revision"]
        if ffmpeg_path():
            job = json.loads(request("POST", f"/api/scenes/{scene['id']}/render"))["job"]
            for _ in range(100):
                job = json.loads(request("GET", f"/api/scenes/jobs/{job['id']}"))
                if job["status"] not in ("queued", "running"):
                    break
                time.sleep(0.1)
            assert job["status"] == "done", job.get("error")
            video = request("GET", f"/api/scenes/{scene['id']}/files/{job['file']}")
            assert b"ftyp" in video[:100]
            package = zipfile.ZipFile(io.BytesIO(request("GET", f"/api/scenes/{scene['id']}/export.zip")))
            assert "scene.mp4" in package.namelist()
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
