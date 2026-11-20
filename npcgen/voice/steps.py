"""The voice steps of a job (npcgen/jobs.py hands them here):

  voice_take  (key = take number)  a candidate voice: the design model reads the take script with a new seed, or Kokoro reads
                                   it in the next best stock voice
  voice_pick                        use this job's first take until you pick one (batch voicing), unless you already picked
  voice_test  (key = test name)    a line or a mood preview in the current voice, kept in voice/tests/
  voice_line  (key = line id)      a dialogue line in the saved voice, kept in voice/lines/

What a step speaks with follows the voice: a saved voice is copied (cloned) by the speaking engine, so a character sounds the
same in every line; before a voice is saved, a design engine reads the description directly; a stock voice is read by Kokoro.
Every clip is trimmed, loudness-matched and given a mouth track (audio.finish) before it is stored.
"""
import hashlib
import uuid

from ..library import now
from . import alignment, audio, authoring, describe, effects, engines, markup, record
from .runtime import VoiceError


def _work_path(lib, npc_id):
    return lib.resolve(npc_id, f"voice/.work/{uuid.uuid4().hex[:10]}.wav")


def _make(jobs, job, npc, engine_id, request):
    """Run one request in the engine's worker and return (final WAV bytes, seconds, mouth track, worker reply)."""
    tmp = _work_path(jobs.lib, npc["id"])
    tmp.parent.mkdir(parents=True, exist_ok=True)
    try:
        frozen=job.options.get('voice_runtime',{}).get(engine_id)
        reply = jobs.voice.run(engine_id, {**request,**({'_runtime_binding':frozen} if frozen else {})}, tmp, cancel=job.cancel)
        wav, seconds, mouth = audio.finish(tmp.read_bytes())
    finally:
        tmp.unlink(missing_ok=True)
    return wav, seconds, mouth, reply


def direction(engine_id, mood):
    """An instruction that steers a copied voice to a mood, for the engines that take one."""
    words = engines.MOODS.get(mood or "neutral") or ""
    if not words or not engines.ENGINES.get(engine_id, {}).get("caps", {}).get("direction"):
        return None
    return f"Speak {words}."


def speak_request(npc, v, text, mood, seed):
    """(engine id, request) that says `text` in the character's current voice."""
    speaker = record.speaker_for(v)
    ref = v.get("reference")
    language = v.get("language") or "English"
    params = v.get("settings") or {}
    if speaker in ("kokoro", "qwen_custom"):
        voice_id = v.get("stock_voice")
        if not voice_id:
            raise VoiceError(f"Pick a {engines.ENGINES[speaker]['label']} stock voice first.")
        return speaker, {"cmd": "stock", "text": markup.for_engine(text, speaker), "voice": voice_id,
                          "speed": params.get("speed", 1.0), "language": language,
                          "params": {**params, "seed": seed, "direction": " ".join(filter(None, [direction(speaker, mood), v.get("acting_direction")]))}}
    if ref and engines.can(speaker, "clone"):
        ref_path = None  # filled in by the caller (it knows the library)
        return speaker, {"cmd": "clone", "text": markup.for_engine(text, speaker), "ref": ref_path, "ref_text": ref.get("text") or "",
                         "language": language, "seed": seed,
                         "instruct": " ".join(filter(None, [direction(speaker, mood), v.get("acting_direction")])) or None, "params": params}
    if v.get("mode") == "design" and engines.can(v.get("engine"), "design") and (v.get("description") or "").strip():
        engine_id = v["engine"]
        return engine_id, {"cmd": "design", "text": markup.for_engine(text, engine_id),
                           "instruct": " ".join(filter(None, [describe.with_mood(record.instruction(v), mood), v.get("acting_direction")])), "language": language, "seed": seed,
                           "params": params}
    raise VoiceError("This character has no voice yet: make takes and pick one, pick a stock voice, upload a recording or "
                     "copy another character's voice.")


def speak(jobs, job, npc, v, text, mood, seed, provenance=None, previous=None):
    engine_id, request = speak_request(npc, v, text, mood, seed)
    if request["cmd"] == "clone":
        request["ref"] = str(jobs.lib.resolve(npc["id"], v["reference"]["file"]))
        if v["reference"].get("sha256"):
            if hashlib.sha256(jobs.lib.read_asset(npc["id"], v["reference"]["file"])).hexdigest() != v["reference"]["sha256"]:
                raise VoiceError("Reference checksum failed; restore the pinned reference from a backup")
    previous = previous or {}
    reuse = (provenance is not None and previous.get("original") and previous.get("synthesis_signature") == provenance.get("synthesis_signature")
             and previous.get("render_signature") != provenance.get("render_signature"))
    try:
        if reuse:
            wav = effects.read_original(jobs.lib, npc["id"], previous["original"])
            reply = {"model_identity": previous.get("model_identity")}
        else:
            wav, _, _, reply = _make(jobs, job, npc, engine_id, request)
        original = effects.preserve(jobs.lib, npc["id"], wav)
        wav, receipt = effects.process(wav, v.get("delivery_effects", {}), job.cancel)
    except ValueError as e:
        raise VoiceError(str(e)) from None
    samples, rate = audio.read_wav(wav)
    seconds, mouth = round(len(samples) / rate, 2), audio.mouth_track(samples, rate)
    if provenance is not None:
        provenance["model_identity"] = reply.get("model_identity")
        provenance.update(original=original, postprocessing=receipt, original_reused=bool(reuse))
        alignment_text=provenance.get('synthesis_text') or text
        if engine_id=='drama3':alignment_text=markup.plain(alignment_text,'drama3')
        provenance["lip_sync"] = alignment.process(wav, alignment_text, v.get('language') or 'English',
            (v.get('studio') or {}).get('lip_sync'), getattr(jobs, 'cfg', None), job.cancel)
        if reuse and previous.get("seed") is not None:
            provenance["seed"] = previous["seed"]
    return engine_id, wav, seconds, mouth


def take(jobs, job, step, npc):
    opts = job.options.get("voice") or {}
    v = opts.get("voice_snapshot") or record.voice_of(npc)
    n = int(step["key"])
    language = opts.get("language") or v["language"]
    script = opts.get("script") or v.get("script") or describe.take_script(npc, language)
    seed = (int(opts.get("seed_base") or 0) + n - 1) % (2 ** 31)
    mode, engine_id = opts.get("mode") or v["mode"], opts.get("engine") or v["engine"]
    if mode == "stock":
        order = opts.get("stock_order") or describe.stock_match(npc, engine_id)
        stock_voice = order[(n - 1) % len(order)]
        request = {"cmd": "stock", "text": markup.plain(script), "voice": stock_voice,
                   "speed": (opts.get("settings") or {}).get("speed", 1.0), "language": language,
                   "params": {**(opts.get("settings") or v.get("settings") or {}), "seed": seed}}
        instruction = ""
    elif mode == "design":
        instruction = opts.get("instruction") or record.instruction(v)
        if not instruction:
            raise VoiceError("Describe the voice first.")
        problem = describe.adult_problem(npc, instruction)
        if problem:
            raise VoiceError(problem)
        stock_voice = None
        request = {"cmd": "design", "text": markup.for_engine(script, engine_id), "instruct": instruction, "language": language,
                   "seed": seed, "params": opts.get("settings") or v["settings"]}
    else:
        raise VoiceError("Takes are made from a description or a stock voice; an uploaded or copied voice is used as it is.")
    wav, seconds, mouth, reply = _make(jobs, job, npc, engine_id, request)
    take_id = uuid.uuid4().hex[:8]
    rel = f"voice/takes/{take_id}.wav"
    jobs.lib.write_asset(npc["id"], rel, wav)
    meta = {"id": take_id, "n": n, "file": rel, "engine": engine_id, "mode": mode, "seed": seed, "stock_voice": stock_voice,
            "instruction": instruction, "text": script, "language": language, "seconds": seconds, "created": now(),
            "job": job.id, "make_seconds": reply.get("seconds")}
    meta["model_identity"] = reply.get("model_identity")
    snapshot = record.render_snapshot(v)
    snapshot.update(engine=engine_id, mode=mode, language=language, stock_voice=stock_voice,
                    settings=opts.get("settings") or v.get("settings") or {})
    for key in ("reference", "library_binding", "chosen_take", "review"):
        snapshot.pop(key, None)
    meta["voice_snapshot"] = snapshot

    def apply(rec):
        rv = record.voice_of(rec)
        rv["takes"].append(meta)
        if rv.get("review") != "picked":
            rv["review"] = "pending"
        rec["voice"] = rv
        record.prune_takes(jobs.lib, rec)
    jobs.lib.update(npc["id"], apply)
    step["detail"] = f"{seconds:.1f} s of speech"


def pick(jobs, job, step, npc):
    """Use this job's first take as the voice while it waits for your review (never over a take you picked)."""
    v = record.voice_of(npc)
    if v.get("review") == "picked" and v.get("reference"):
        job.options["voice"]["voice_snapshot"] = record.freeze_voice(jobs.lib, npc["id"], v)
        return
    mine = [t for t in v["takes"] if t.get("job") == job.id]
    if mine:
        picked = record.select_take(jobs.lib, npc["id"], mine[0]["id"], review="pending")
        job.options["voice"]["voice_snapshot"] = record.render_snapshot(picked["voice"])


def test(jobs, job, step, npc):
    voice_opts = job.options.get("voice") or {}
    opts = next((row for row in voice_opts.get("tests", []) if row["key"] == step["key"]), None)
    opts = opts or voice_opts.get("test") or {}
    text, mood, key = opts.get("text") or "", opts.get("mood") or "neutral", step["key"]
    problem = describe.mood_problem(npc, mood)
    if problem:
        raise VoiceError(problem)
    v = (job.options.get("voice") or {}).get("voice_snapshot") or record.voice_of(npc)
    effective, spoken, details = authoring.prepare(v, text, mood, override=opts.get("performance"))
    engine_id, wav, seconds, mouth = speak(jobs, job, npc, effective, spoken, mood, int(opts.get("seed") or record.new_seed()), details,
                                        (record.voice_of(npc).get("tests") or {}).get(key))
    rel = f"voice/tests/{key}.wav"
    jobs.lib.write_asset(npc["id"], rel, wav)

    def apply(rec):
        rv = record.voice_of(rec)
        rv["tests"][key] = {"file": rel, "text": text, "mood": mood, "engine": engine_id, "seconds": seconds, "mouth": mouth,
                            "created": now(), **details}
        rec["voice"] = rv
        if voice_opts.get("audition_id"):
            from . import audition
            audition.complete(rec, voice_opts["audition_id"], key, wav, rv["tests"][key])
    jobs.lib.update(npc["id"], apply)


def line(jobs, job, step, npc):
    line_id = step["key"]
    row, category = None, None
    for cat, rows in ((npc.get("dialogue") or {}).get("lines") or {}).items():
        for r in rows:
            if r.get("id") == line_id:
                row, category = r, cat
    frozen = (job.options.get("voice") or {}).get("line_inputs", {}).get(line_id)
    if frozen:
        row, category = {"id": line_id, "text": frozen["text"]}, frozen["category"]
    if row is None:
        raise VoiceError(f"The dialogue line {line_id} no longer exists.")
    moods = (job.options.get("voice") or {}).get("moods") or {}
    mood = (frozen or {}).get("mood") or moods.get(line_id) or ((npc.get("voice_lines") or {}).get(line_id) or {}).get("mood") or "neutral"
    problem = describe.mood_problem(npc, mood)
    if problem:
        raise VoiceError(problem)
    v = (job.options.get("voice") or {}).get("voice_snapshot") or record.voice_of(npc)
    seed = record.new_seed()
    effective, spoken, details = authoring.prepare(v, row["text"], mood, line_id)
    engine_id, wav, seconds, mouth = speak(jobs, job, npc, effective, spoken, mood, seed, details, (npc.get("voice_lines") or {}).get(line_id))
    rel = f"voice/lines/{line_id}.wav"
    jobs.lib.write_asset(npc["id"], rel, wav)
    ref_id = record.identity_id(v)

    def apply(rec):
        rec.setdefault("voice_lines", {})[line_id] = {
            "file": rel, "text": row["text"], "category": category, "mood": mood, "engine": engine_id, "seed": seed,
            "seconds": seconds, "mouth": mouth, "reference": ref_id, "created": now(), **details}
        rec["voice_lines"][line_id]["voice_snapshot"] = record.render_snapshot(v)
    jobs.lib.update(npc["id"], apply)


RUNNERS = {"voice_take": take, "voice_pick": pick, "voice_test": test, "voice_line": line}


def run(jobs, job, step, npc):
    try:
        RUNNERS[step["kind"]](jobs, job, step, npc)
    except VoiceError as e:
        from ..comfy import ComfyError
        raise ComfyError(str(e)) from None                 # the job shows it on the step, like a picture that failed
