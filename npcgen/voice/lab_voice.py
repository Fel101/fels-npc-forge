"""Link a Forge character to a Voice Lab Cast voice or a named preset."""

import hashlib

from ..library import now
from . import describe, record
from .lab_client import LabClient, LabError

CLONERS = {"qwen3-tts-1.7b-base", "qwen3-tts-0.6b-base", "turbo", "orig", "voxcpm2", "breeze-tts-2", "drama-3"}


def link(client, voice, *, recording=None):
    """Fields saved in npc.json and frozen into queued jobs."""
    return {
        "name": voice["name"], "kind": voice["kind"], "model": voice["model"],
        "label": voice.get("model_name") or voice["model"], "identity_id": voice.get("identity_id"),
        "voice_id": voice.get("voice_id"), "lab_id": client.health.get("lab_id"),
        "takes_direction": bool(voice.get("takes_direction")), "cloned_from_person": recording,
        "licence": voice.get("licence"),
    }


def assign(client, name):
    matches = [voice for voice in client.voices() if voice["name"] == name]
    if len(matches) != 1:
        raise LabError("Choose a unique voice from the Voice Lab list. For duplicate names, rename the voice in Voice Lab.",
                       code="voice_missing")
    voice = matches[0]
    if not voice["ready"]:
        raise LabError(voice.get("why_not") or "Open Voice Lab to check why this voice is unavailable.", code="voice_not_ready")
    return link(client, voice)


def save(npc, selected):
    voice = record.voice_of(npc)
    voice.update(lab_voice=selected, review="picked", updated=now())
    voice["commercial_ok"] = record.commercial_ok(voice)
    npc["voice"] = voice


def request(npc, data, *, uploaded=None):
    """Validate and freeze the source used by a queued Cast voice creation."""
    voice = record.voice_of(npc)
    name = " ".join(str(npc["profile"].get(key) or "") for key in ("first_name", "last_name")).strip()
    existing = voice.get("lab_voice")
    if data.get("source") == "recording":
        if data.get("consent") is not True:
            raise ValueError("Confirm that you may use the recording in Voice Lab.")
        ref = uploaded or voice.get("reference") or {}
        if not ref.get("file") or not ref.get("text"):
            raise ValueError("Choose a recording and enter the exact words it says.")
        origin = str(data.get("origin") or "").strip()
        allowed_use = str(data.get("allowed_use") or "").strip()
        if not origin or not allowed_use:
            raise ValueError("Enter who made the recording and how it may be used.")
        engine = data.get("engine") or "qwen3-tts-1.7b-base"
        if engine not in CLONERS:
            raise ValueError("Choose a Voice Lab model that can copy a recording.")
        return {"source": "recording", "name": name, "existing": existing, "engine": engine, "origin": origin, "allowed_use": allowed_use,
                "reference": ref, "consent": True}
    if data.get("source") != "description":
        raise ValueError("Choose a description or a recording for this voice.")
    description = str(data.get("description") or record.instruction(voice) or describe.describe(npc)).strip()
    if not description or len(description) > 1000:
        raise ValueError("Write a voice description of up to 1,000 characters.")
    problem = describe.adult_problem(npc, description)
    if problem:
        raise ValueError(problem)
    language = data.get("language") or voice["language"]
    if not isinstance(language, str) or not record.LANG_RE.fullmatch(language) or len(language) > 30:
        raise ValueError("Choose a language for the voice.")
    text = str(data.get("text") or voice.get("script") or describe.take_script(npc, language)).strip()
    if len(text) > 400:
        raise ValueError("Keep the voice sample under 400 characters.")
    return {"source": "description", "name": name, "existing": existing, "description": description, "language": language,
            "text": text}


def create(jobs, job, step, npc):
    request = job.options["voice"]["lab_create"]
    selected = request.get("created")
    if selected is None:
        selected = create_in_lab(jobs, job, npc, request)
        request["created"] = selected
        job.touch()

    def apply(rec):
        current = record.voice_of(rec).get("lab_voice")
        if current == selected:
            return
        if current != request.get("existing"):
            raise LabError(f"Voice Lab made {selected['name']}. Select it in the Voice Lab list to replace the current voice.")
        save(rec, selected)

    saved = jobs.lib.update(npc["id"], apply)
    frozen = job.options["voice"].setdefault("voice_snapshot", record.render_snapshot(saved["voice"]))
    frozen["lab_voice"] = selected
    step["detail"] = f"Using {selected['name']} from Voice Lab"


def create_in_lab(jobs, job, npc, request):
    client = job_client(job)
    if request["source"] == "recording":
        ref = request["reference"]
        recording = jobs.lib.read_asset(npc["id"], ref["file"])
        if hashlib.sha256(recording).hexdigest() != ref["sha256"]:
            raise LabError("The saved recording changed. Restore it or pick a new one and try again.")
        created = client.clone(request["name"], request["engine"], recording, transcript=ref["text"],
                               origin=request["origin"], allowed_use=request["allowed_use"],
                               consent=request["consent"], cancel=job.cancel, checkpoint=request.setdefault("checkpoint", {}),
                               save_checkpoint=job.touch)
    elif request["source"] == "preset":
        name = preset_name(job, client, request["model"], request["voice_id"])
        created = next(row for row in client.voices() if row["name"] == name)
    else:
        created = client.design(request["name"], request["description"], text=request["text"],
                                language=request["language"], engine=request.get("engine"), cancel=job.cancel)
        # Design's response has fewer fields than the dedicated voice list.
        created = client.voice(created["identity_id"])
    return link(client, created, recording=request["source"] == "recording")


def speak(job, command):
    client = job_client(job)
    if command["cmd"] == "design":
        return client.preview_design(command["instruct"], command["text"], engine=command["model"],
                                     language=command["language"], seed=command["seed"], cancel=job.cancel)
    if command["cmd"] == "stock":
        name = preset_name(job, client, command["model"], command["voice"])
        return client.speak(name, command["text"], direction=command.get("direction"), speed=command.get("speed"),
                            cancel=job.cancel)
    return speak_linked(job, client, command)


def job_client(job):
    client = job.cache.get("voice_lab")
    if client is None:
        client = job.cache["voice_lab"] = LabClient.discover()
    return client


def preset_name(job, client, model, voice_id):
    key = ("lab_preset", model, voice_id)
    if key not in job.cache:
        preset = next((row for row in client.voices()
                       if row["model"] == model and (row.get("voice_id") or "").casefold() == voice_id.casefold()
                       and row["kind"] == "preset"), None)
        if not preset or not preset["ready"]:
            reason = preset.get("why_not") if preset else None
            raise LabError(reason or "Choose an available preset in Voice Lab and try again.", code="voice_not_ready")
        job.cache[key] = preset["name"]
    return job.cache[key]


def speak_linked(job, client, command):
    selected = command["voice"]
    identity = selected.get("identity_id")
    name = selected["name"]
    if identity:
        if client.health.get("lab_id") != selected["lab_id"]:
            raise LabError("Start the Voice Lab library where this Cast voice was made, or pick another voice in Forge.", code="lab_changed")
        key = ("lab_voice", identity)
        if key not in job.cache:
            job.cache[key] = client.voice(identity)
        name = job.cache[key]["name"]
    elif selected.get("voice_id"):
        name = preset_name(job, client, selected["model"], selected["voice_id"])
    return client.speak(name, command["text"], direction=command.get("direction"), speed=command.get("speed"), cancel=job.cancel)
