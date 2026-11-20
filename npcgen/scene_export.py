"""Portable scene export with its finished video, script, and source notes."""
import io
import json
import zipfile

from .scenes import compile_timeline, scene_voice_of
from .voice import alignment as voice_alignment, character_rig, effects as voice_effects, engines as voice_engines, game_exports, record as voice_record
from .voice import mesh_rig


def make_zip(scene, scenes, notice):
    render = scene.get("render") or {}
    filename = render.get("file")
    if not filename or not filename.startswith("render/") or not filename.endswith(".mp4"):
        raise ValueError("Render a scene video before downloading its package")
    video = scenes.resolve(scene["id"], filename)
    if not video.is_file():
        raise ValueError("The scene video is missing; render it again")
    timeline = compile_timeline(scene, scenes.lib, scenes)
    stale = render.get("revision") != scene["revision"] or render.get("fingerprint") != timeline["fingerprint"]
    cast = []
    for npc_id in scene["cast"]:
        try:
            npc = scenes.lib.get(npc_id)
        except KeyError:
            cast.append({"id": npc_id, "missing": True})
            continue
        profile = npc.get("profile") or {}
        assets = npc.get("assets") or {}
        sources = []
        for kind in ("portrait", "sprite", "rig_3d"):
            meta = assets.get(kind) or {}
            if meta.get("file"):
                sources.append({"kind": kind, "file": meta["file"], "engine": meta.get("engine"),
                                "created": meta.get("created")})
        for kind, meta in (assets.get("expressions") or {}).items():
            if isinstance(meta, dict) and meta.get("file"):
                sources.append({"kind": f"expression:{kind}", "file": meta["file"],
                                "engine": meta.get("engine"), "created": meta.get("created")})
        living = assets.get("living") or {}
        if living:
            sources.append({"kind": "living", "engine": living.get("engine"), "created": living.get("created")})
        cast.append({"id": npc_id, "name": " ".join(filter(None, [profile.get("first_name"), profile.get("last_name")])),
                     "sources": sources})
    voices, audio_files, originals, alignments = [], [], {}, {}
    for beat in timeline["beats"]:
        voice = beat.get("voice")
        if not voice:
            continue
        meta = ((scene.get("audio") or {}).get(beat["id"]) if voice["source"] == "scene" else
                ((scenes.lib.get(beat["speaker_id"]).get("voice_lines") or {}).get(beat.get("line_id")))) or {}
        engine = meta.get("engine")
        spec = voice_engines.spec(engine) or {}
        delivery_file = f"audio/{beat['id']}.wav"
        path = (scenes.resolve(scene["id"], voice["file"]) if voice["source"] == "scene" else scenes.lib.resolve(beat["speaker_id"], voice["file"]))
        data = voice_effects.check_delivery(path.read_bytes(), meta)
        audio_files.append((data, delivery_file))
        actor = scenes.lib.get(beat['speaker_id'])
        snapshot = scene_voice_of(actor, (scene.get('voice_overrides') or {}).get(beat['speaker_id']),
                                  (scene.get('voice_bindings') or {}).get(beat['speaker_id']))
        lip_sync = voice_alignment.for_delivery(data, meta, snapshot)
        lip_file = f"lip-sync/{beat['id']}.json"
        alignments[lip_file] = lip_sync
        original = meta.get("original")
        if original:
            original_file = f"originals/{original['sha256']}.wav"
            originals[original_file] = voice_effects.read_original(scenes.lib, beat["speaker_id"], original)
            original = {**original, "file": original_file}
        voices.append({"beat_id": beat["id"], "npc_id": beat["speaker_id"], "source": voice["source"],
                       "file": delivery_file, "original": original, "postprocessing": meta.get("postprocessing"),
                       "lip_sync": lip_sync, "lip_sync_file": lip_file,
                       "mouth": meta.get('mouth') or [],
                       "visual_method": voice.get('visual_method', 'amplitude_fallback'),
                       "effects": meta.get("effects"), "synthesis_signature": meta.get("synthesis_signature"),
                       "engine": engine, "licence": spec.get("licence"),
                       "synthesis_text": meta.get("synthesis_text"), "performance": meta.get("performance"),
                       "reference_version": ((meta.get("voice_snapshot") or {}).get("reference") or {}).get("id"),
                       "render_signature": meta.get("render_signature"), "voice_snapshot": meta.get("voice_snapshot"),
                       "model_identity": meta.get("model_identity"),
                       "identity_version": voice_record.identity_id(meta.get("voice_snapshot") or {}),
                       "commercial_ok": meta.get("commercial_ok", spec.get("commercial_ok"))})
    manifest = {"scene_id": scene["id"], "title": scene["title"], "render": render,
                "props": timeline.get('props', []), "soundtracks": timeline.get('soundtracks', []),
                "voice_takes": timeline.get('takes', {}),
                "voice_take_selections": {key: {k: value.get(k) for k in ('selected_id', 'approved_id', 'reviewed_at')}
                                          for key, value in scene.get('voice_takes', {}).items()},
                "background": scene.get("background"),
                "current_revision": scene["revision"], "current_fingerprint": timeline["fingerprint"],
                "render_outdated": stale, "cast": cast, "voices": voices,
                "character_lip_animation": "checked portrait/sprite frames or 3D morph/jaw mappings; amplitude fallback when alignment or artist mappings are unavailable",
                "note": "Source details describe the current library. If render_outdated is true, they may differ from the video."}
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.write(video, "scene.mp4")
        retained = set()
        for meta in [*scene.get('media', {}).values(), *scene.get('prop_sources', {}).values(),
                     *(row['meta'] for group in scene.get('voice_takes', {}).values() for row in group['takes'])]:
            filename = meta['file']
            if filename in retained: continue
            data = scenes.resolve(scene['id'], filename).read_bytes()
            if filename.startswith('audio/'):
                voice_effects.check_delivery(data, meta)
            if meta.get('sha256'):
                import hashlib
                if hashlib.sha256(data).hexdigest() != meta['sha256']:
                    raise ValueError('A retained scene asset changed; check the scene before exporting')
            archive.writestr(filename, data); retained.add(filename)
        for data, file in audio_files:
            if file not in retained:
                archive.writestr(file, data); retained.add(file)
        for file, data in originals.items():
            archive.writestr(file, data)
        for file, data in alignments.items():
            archive.writestr(file, json.dumps(data, indent=2, ensure_ascii=False))
        for voice in voices:
            voice['game_playback'] = game_exports.write_clip(archive, '', voice['beat_id'], voice['lip_sync'], voice['mouth'], voice['file'])
        game_exports.write_support(archive)
        for member in cast:
            if not member.get('missing'):
                member['character_rigs'] = character_rig.write_exports(archive, scenes.lib, scenes.lib.get(member['id']),
                    folder=f"game/characters/{member['id']}/rigs")
                mesh=mesh_rig.write_export(archive,scenes.lib,scenes.lib.get(member['id']),folder=f"game/characters/{member['id']}/rigs/mesh")
                if mesh:member['character_rigs']['mesh']=mesh
        background_file = (scene.get("background") or {}).get("file")
        if background_file:
            background = scenes.resolve(scene["id"], background_file)
            if background.is_file():
                archive.write(background, background_file)
        archive.writestr("scene.json", json.dumps(scene, indent=2, ensure_ascii=False))
        archive.writestr("sources.json", json.dumps(manifest, indent=2, ensure_ascii=False))
        archive.writestr("NOTICE.md", notice)
    return output.getvalue()
