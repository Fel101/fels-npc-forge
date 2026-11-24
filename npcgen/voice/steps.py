"""Queued Voice Lab auditions and dialogue, with Forge audio finishing and effects."""
import uuid

from ..library import now
from . import alignment, audio, authoring, describe, effects, engines, lab_voice, markup, record
from .lab_client import LabError


def _make(job, engine_id, request):
    wav, seconds, mouth = audio.finish(lab_voice.speak(job, request))
    voice = request.get("voice")
    model = voice["model"] if isinstance(voice, dict) else request.get("model", engine_id)
    name = voice["name"] if isinstance(voice, dict) else voice
    return wav, seconds, mouth, {"model_identity": {"source": "voice-lab", "model": model, "voice": name}}


def direction(engine_id, mood):
    """An instruction that steers a copied voice to a mood, for the engines that take one."""
    words = engines.MOODS.get(mood or "neutral") or ""
    if not words or not engines.ENGINES.get(engine_id, {}).get("caps", {}).get("direction"):
        return None
    return f"Speak {words}."


def speak_request(npc, v, text, mood, seed):
    """(engine id, request) that says `text` in the character's current voice."""
    if v.get("lab_voice"):
        voice = v["lab_voice"]
        engine = record.speaker_for(v)
        mood_text = engines.MOODS.get(mood) or ""
        acting = " ".join(filter(None, (f"Speak {mood_text}." if mood_text else None, v.get("acting_direction"))))
        return engine, {"cmd": "lab", "voice": voice, "text": markup.for_engine(text, engine),
                        "direction": acting if voice.get("takes_direction") else None,
                        "speed": (v.get("settings") or {}).get("speed")}
    speaker = record.speaker_for(v)
    ref = v.get("reference")
    language = v.get("language") or "English"
    params = v.get("settings") or {}
    if speaker in ("kokoro", "qwen_custom"):
        voice_id = v.get("stock_voice")
        if not voice_id:
            raise LabError(f"Pick a {engines.ENGINES[speaker]['label']} stock voice first.")
        return speaker, {"cmd": "stock", "model": engines.PRESET_MODELS[speaker], "text": markup.for_engine(text, speaker),
                         "voice": voice_id, "speed": params.get("speed"),
                         "direction": " ".join(filter(None, (direction(speaker, mood), v.get("acting_direction"))))}
    if ref:
        raise LabError("Make this voice in Voice Lab from the saved description or recording, then try again.")
    if v.get("mode") == "design" and v.get("engine") in engines.DESIGN_MODELS and (v.get("description") or "").strip():
        engine_id = v["engine"]
        return engine_id, {"cmd": "design", "model": engines.DESIGN_MODELS[engine_id], "text": markup.for_engine(text, engine_id),
                           "instruct": " ".join(filter(None, [describe.with_mood(record.instruction(v), mood), v.get("acting_direction")])), "language": language, "seed": seed,
                           "params": params}
    raise LabError("Choose or make a voice in the Voice Lab card first.")


def speak(jobs, job, npc, v, text, mood, seed, provenance=None, previous=None):
    engine_id, request = speak_request(npc, v, text, mood, seed)
    previous = previous or {}
    reuse = (provenance is not None and previous.get("original") and previous.get("synthesis_signature") == provenance.get("synthesis_signature")
             and previous.get("render_signature") != provenance.get("render_signature"))
    try:
        if reuse:
            wav = effects.read_original(jobs.lib, npc["id"], previous["original"])
            reply = {"model_identity": previous.get("model_identity")}
        else:
            wav, _, _, reply = _make(job, engine_id, request)
        original = effects.preserve(jobs.lib, npc["id"], wav)
        wav, receipt = effects.process(wav, v.get("delivery_effects", {}), job.cancel)
    except ValueError as e:
        raise LabError(str(e)) from None
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
        request = {"cmd": "stock", "model": engines.PRESET_MODELS[engine_id], "text": markup.plain(script), "voice": stock_voice,
                   "speed": (opts.get("settings") or {}).get("speed", 1.0), "language": language,
                   "params": {**(opts.get("settings") or v.get("settings") or {}), "seed": seed}}
        instruction = ""
    elif mode == "design":
        if engine_id not in engines.DESIGN_MODELS:
            raise LabError("Choose a current Voice Lab model to design a new voice.")
        instruction = opts.get("instruction") or record.instruction(v)
        if not instruction:
            raise LabError("Describe the voice first.")
        problem = describe.adult_problem(npc, instruction)
        if problem:
            raise LabError(problem)
        stock_voice = None
        request = {"cmd": "design", "model": engines.DESIGN_MODELS[engine_id], "text": markup.for_engine(script, engine_id), "instruct": instruction, "language": language,
                   "seed": seed, "params": opts.get("settings") or v["settings"]}
    else:
        raise LabError("Takes are made from a description or a stock voice; an uploaded or copied voice is used as it is.")
    wav, seconds, mouth, reply = _make(job, engine_id, request)
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
        raise LabError(problem)
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
        raise LabError(f"The dialogue line {line_id} no longer exists.")
    moods = (job.options.get("voice") or {}).get("moods") or {}
    mood = (frozen or {}).get("mood") or moods.get(line_id) or ((npc.get("voice_lines") or {}).get(line_id) or {}).get("mood") or "neutral"
    problem = describe.mood_problem(npc, mood)
    if problem:
        raise LabError(problem)
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


RUNNERS = {"voice_take": take, "voice_pick": pick, "voice_test": test, "voice_line": line, "voice_lab_create": lab_voice.create}


def run(jobs, job, step, npc):
    RUNNERS[step["kind"]](jobs, job, step, npc)
