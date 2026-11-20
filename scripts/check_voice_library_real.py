"""Clone a saved production reference in an isolated QA library using an installed engine.

Source NPC/audio are read-only. No downloads, package changes, or production writes.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from npcgen import config, dialogue, scene_render  # noqa: E402
from npcgen.scenes import compile_timeline  # noqa: E402
from npcgen.scene_export import make_zip  # noqa: E402
from npcgen.app import App  # noqa: E402
from npcgen.library import Library  # noqa: E402
from npcgen.voice import audio, authoring, pack, record, service  # noqa: E402


def wait_job(app, job_id):
    deadline = time.monotonic() + 360
    while time.monotonic() < deadline:
        job = next(j for j in app.jobs.snapshot() if j["id"] == job_id)
        if job["status"] not in {"queued", "running"}:
            if job["status"] != "done":
                raise RuntimeError(json.dumps(job, indent=2))
            return job
        time.sleep(.25)
    raise TimeoutError("Real voice generation exceeded six minutes")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-npc-id", required=True)
    parser.add_argument("--engine", default="qwen")
    parser.add_argument("--studio", action="store_true", help="Also check pronunciation/performance, pinned scenes and exports")
    args = parser.parse_args()
    cfg = config.load()
    source_lib = Library(Path(cfg["library_path"]))
    source = source_lib.get(args.source_npc_id)
    original_record = source_lib.resolve(source["id"], "npc.json").read_bytes()
    source_voice = record.render_snapshot(record.voice_of(source))
    reference = source_voice.pop("reference", None)
    if not reference:
        raise ValueError("Source NPC must have an assigned reference")
    original_audio = source_lib.read_asset(source["id"], reference["file"])
    run = ROOT / "local" / (("voice-studio-real-" if args.studio else "voice-library-real-") + time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6])
    run.mkdir(parents=True)
    cfg["library_path"] = run / "library"
    report = {"run": str(run), "engine": args.engine, "source_npc": source["id"],
              "source_reference_sha256": hashlib.sha256(original_audio).hexdigest(), "human_listening": "pending", "clips": []}
    try:
        with App(cfg) as app:
            targets = []
            for index in range(2):
                npc = copy.deepcopy(source)
                for key in ("id", "created", "updated", "voice", "voice_lines", "assets", "dialogue"):
                    npc.pop(key, None)
                npc["profile"].update(first_name=f"VoiceQA{index + 1}", last_name="Actor")
                npc["voice"] = {**source_voice, "speaker": args.engine}
                npc = app.lib.create(npc)
                portrait = (source.get("assets") or {}).get("portrait") or {}
                if portrait.get("file"):
                    app.lib.write_asset(npc["id"], "portrait.png", source_lib.read_asset(source["id"], portrait["file"]))
                    app.lib.update(npc["id"], lambda rec: rec.setdefault("assets", {}).update(portrait={**portrait, "file": "portrait.png"}))
                def install(rec):
                    v = record.voice_of(rec)
                    record.install_reference(app.lib, rec, v, original_audio, {**reference, "source": f"qa-import:{source['id']}"})
                    rec["voice"] = v
                app.lib.update(npc["id"], install)
                targets.append(npc["id"])
            identity = app.voice_library.from_npc(targets[0], {"name": "Real QA actor", "tags": ["QA", args.engine]})
            version = identity["versions"][0]
            app.voice_library.approve(version["id"], {"approved": True, "notes": "Existing user-selected source reference; QA reuse approval"})
            app.voice_library.assign(version["id"], targets)
            report["voice_id"], report["version_id"] = identity["id"], version["id"]
            for index, npc_id in enumerate(targets):
                def script(rec):
                    rec["dialogue"] = dialogue.generate(rec)
                    rec["dialogue"]["lines"]["greeting"][0]["text"] = [
                        "The harbor gates are open. Welcome to the forge.",
                        "Keep your lantern close. The night watch begins at sunset."][index]
                    if args.studio:
                        rec["dialogue"]["lines"]["greeting"][0]["text"] = [
                            "The Aether harbor gates are open. Welcome to the forge.",
                            "Aether watch begins at sunset. Keep your lantern close."][index]
                        params = {"temperature": .7} if "temperature" in authoring.capabilities(args.engine)["parameters"] else {}
                        if args.engine == "voxcpm2":
                            params = {"cfg_value": 2.5, "inference_timesteps": 8}
                        elif args.engine == "chatterbox_original":
                            params = {"temperature": .7, "exaggeration": .65, "cfg_weight": .45, "top_p": .9, "min_p": .03, "repetition_penalty": 1.25}
                        rec["voice"]["studio"] = authoring.clean_studio({"pronunciations": [{"term": "Aether", "spoken": "Ee ther"}],
                            "presets": [{"id": "qa-calm", "name": "QA calm", "parameters": params}], "default_performance": "qa-calm"})
                npc = app.lib.update(npc_id, script)
                request = service.lines_request(npc, {"ids": ["greeting_1"]})
                request["voice_snapshot"] = record.freeze_voice(app.lib, npc_id, request["voice_snapshot"])
                start = time.monotonic()
                job = app.jobs.submit(npc, {"voice": request}, {})
                wait_job(app, job["id"])
                npc = app.lib.get(npc_id)
                meta = npc["voice_lines"]["greeting_1"]
                path = app.lib.resolve(npc_id, meta["file"])
                pcm, rate = audio.read_wav(path.read_bytes())
                assert len(pcm) and meta["reference"] == version["id"] and not record.line_is_stale(npc, "greeting_1")
                report["clips"].append({"npc_id": npc_id, "path": str(path), "text": meta["text"], "sample_rate": rate,
                    "duration_seconds": len(pcm) / rate, "elapsed_seconds": round(time.monotonic() - start, 2),
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "reference_version": meta["reference"]})
                report["clips"][-1]["model_identity"] = meta.get("model_identity")
                if args.studio:
                    report["clips"][-1].update(synthesis_text=meta["synthesis_text"], performance=meta["performance"], render_signature=meta["render_signature"])
                    assert "Ee ther" in meta["synthesis_text"] and "Aether" in meta["text"]
                    zipped, stem = pack.build(app.lib, npc)
                    (run / f"actor-{index + 1}-voice.zip").write_bytes(zipped)
                print(f"Generated {path}", flush=True)
            if args.studio:
                scene = app.scenes.create({"title": "Pinned Aether watch", "cast": targets,
                    "voice_bindings": {npc_id: version["id"] for npc_id in targets}, "beats": [
                        {"id": "watch-one", "kind": "line", "speaker_id": targets[0], "text": "Aether harbor is safe tonight.", "performance_id": "qa-calm"},
                        {"id": "watch-two", "kind": "line", "speaker_id": targets[1], "text": "Keep the Aether lantern lit.", "performance_id": "qa-calm"}]})
                job = app.jobs.submit_scene_voice(scene["id"], {})
                wait_job(app, job["id"])
                def change(rec):
                    rec["voice"]["studio"]["pronunciations"][0]["spoken"] = "Ay ther"
                changed = app.lib.update(targets[0], change)
                assert record.line_is_stale(changed, "greeting_1")
                scene = app.scenes.get(scene["id"])
                timeline = compile_timeline(scene, app.lib, app.scenes)
                assert all(beat["voice"] for beat in timeline["beats"])
                filename = scene_render.render(scene, app.scenes, timeline=timeline)
                app.scenes.set_render(scene["id"], scene["revision"], filename, timeline["fingerprint"])
                scene = app.scenes.get(scene["id"])
                (run / "scene-package.zip").write_bytes(make_zip(scene, app.scenes, "Local QA scene; human listening pending."))
                report["scene"] = {"id": scene["id"], "video": str(app.scenes.resolve(scene["id"], filename)),
                                   "timeline": timeline, "audio": scene["audio"], "pin_survived_character_change": True}
                print(f"Rendered {report['scene']['video']}", flush=True)
        assert source_lib.resolve(source["id"], "npc.json").read_bytes() == original_record
        assert source_lib.read_asset(source["id"], reference["file"]) == original_audio
        report["status"] = "passed"
    except Exception as error:
        report.update(status="failed", error=str(error))
        raise
    finally:
        (run / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"Report: {run / 'report.json'}", flush=True)


if __name__ == "__main__":
    main()
