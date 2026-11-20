"""Scene-owned speech through the same voice worker and GPU queue as NPC dialogue."""
import hashlib
import uuid

from .comfy import ComfyError
from .library import now
from .scenes import compile_timeline, scene_voice_of, voice_signature
from .voice import authoring, record, steps
from .voice.runtime import VoiceError


def requests(scene, scenes, raw):
    """Validate selected beat IDs and freeze each line's words and voice signature for a durable job."""
    if not isinstance(raw, dict):
        raise ValueError("Voice request must be an object")
    ids = raw.get("ids")
    beats = {beat["id"]: beat for beat in scene["beats"] if beat["kind"] == "line"}
    if ids is None:
        ids = list(beats)
    if not isinstance(ids, list) or not ids or not all(isinstance(item, str) for item in ids):
        raise ValueError("Choose one or more scene lines")
    if any(item not in beats for item in ids):
        raise ValueError("A selected scene line no longer exists")
    redo = raw.get("redo", False)
    if not isinstance(redo, bool):
        raise ValueError("redo must be true or false")
    timeline = compile_timeline(scene, scenes.lib, scenes)
    active = {beat["id"]: beat for beat in timeline["beats"]}
    result = {}
    for beat_id in dict.fromkeys(ids):
        beat = beats[beat_id]
        if active[beat_id].get("voice") and not redo:
            continue
        npc = scenes.lib.get(beat["speaker_id"])
        override = (scene.get("voice_overrides") or {}).get(beat["speaker_id"])
        binding = (scene.get("voice_bindings") or {}).get(beat["speaker_id"])
        v = scene_voice_of(npc, override, binding)
        effective, spoken, details = authoring.prepare(v, beat["text"], beat.get("mood", "neutral"), preset_id=beat.get("performance_id"))
        seed = record.new_seed()
        try:
            steps.speak_request(npc, effective, spoken, beat.get("mood", "neutral"), seed)
        except VoiceError as e:
            raise ValueError(f"{npc['profile'].get('first_name', 'This NPC')}: {e}") from None
        result[beat_id] = {"speaker_id": beat["speaker_id"], "text": beat["text"], "mood": beat.get("mood", "neutral"),
                           "text_sha256": hashlib.sha256(beat["text"].encode()).hexdigest(),
                           "voice_signature": voice_signature(npc, override, binding), "seed": seed,
                           "stock_voice": override, "voice_snapshot": record.freeze_voice(scenes.lib, npc["id"], v),
                           "performance_id": beat.get("performance_id"), **details}
    if not result:
        raise ValueError("Every selected line already has up-to-date audio")
    return result


def run(jobs, job, step):
    scene_id, beat_id = job.scene_id, step["key"]
    request = job.options["scene_voice"]["beats"][beat_id]
    npc = jobs.lib.get(request["speaker_id"])
    scene = jobs.scenes.get(scene_id)
    override = (scene.get("voice_overrides") or {}).get(request["speaker_id"])
    binding = (scene.get("voice_bindings") or {}).get(request["speaker_id"])
    if not request.get("voice_snapshot") and voice_signature(npc, override, binding) != request["voice_signature"]:
        raise ComfyError("This scene voice changed while the line was queued; retry the job")
    v = request.get("voice_snapshot") or scene_voice_of(npc, override, binding)
    effective, spoken, details = authoring.prepare(v, request["text"], request["mood"], override=request.get("performance"))
    try:
        engine, wav, seconds, mouth = steps.speak(jobs, job, npc, effective, spoken, request["mood"], request["seed"], details,
                                                (scene.get("audio") or {}).get(beat_id))
    except VoiceError as e:
        raise ComfyError(str(e)) from None
    rel = f"audio/{beat_id}-{uuid.uuid4().hex[:10]}.wav"
    jobs.scenes.write_audio(scene_id, rel, wav)
    meta = {**request, **details, "file": rel, "engine": engine, "seconds": seconds, "mouth": mouth,
            "commercial_ok": record.commercial_ok(v), "created": now()}
    if not jobs.scenes.set_audio(scene_id, beat_id, meta):
        jobs.scenes.resolve(scene_id, rel).unlink(missing_ok=True)
        raise ComfyError("The scene line changed while it was being voiced; retry the job")
