"""Voice descriptions, authored audio and queued Voice Lab previews."""
import copy
import io
import threading
import time
import wave

import numpy as np
import pytest

from npcgen import roll
from npcgen.jobs import JobQueue, Jobs
from npcgen.library import Library
from npcgen.voice import audio, describe, engines, markup, lab_voice, record, service


# ---- helpers -------------------------------------------------------------------------------------------------------------
def _npc(setting="modern", style="photoreal", **profile):
    data = roll.load_data()
    npc = roll.roll_npc(data, setting, {"gender": profile.pop("gender", "female")}, {})
    npc["style"] = style
    npc["profile"].update(profile)
    return npc


def _wav(seconds=1.0, rate=24000, amp=0.3, silence=0.3):
    t = np.arange(int(rate * seconds)) / rate
    tone = (amp * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
    pad = np.zeros(int(rate * silence), dtype=np.float32)
    return audio.write_wav(np.concatenate([pad, tone, pad]), rate)


def _wait(jobs, job_id, seconds=60):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        row = next(j for j in jobs.snapshot() if j["id"] == job_id)
        if row["status"] not in ("queued", "running"):
            return row
        time.sleep(0.05)
    raise AssertionError("the job did not finish")


# ---- describe ------------------------------------------------------------------------------------------------------------
def _adult_job(data, setting):
    jobs = [o for o in _occupations(data["settings"][setting]) if o["min_age"] <= 24 <= o["max_age"]]
    return jobs[0]["label"] if jobs else ""


def _occupations(node):
    if isinstance(node, dict):
        if "label" in node and "min_age" in node and "max_age" in node:
            yield node
        for v in node.values():
            yield from _occupations(v)
    elif isinstance(node, list):
        for v in node:
            yield from _occupations(v)


def test_every_setting_and_style_gets_a_short_honest_adult_description():
    data = roll.load_data()
    child_words = describe.load_data()["adult_only_words"]
    for setting in data["settings"]:
        for style in data["styles"]:
            if style.startswith("_"):
                continue
            npc = _npc(setting, style, age=24)
            npc["profile"]["occupation"] = _adult_job(data, setting)   # rolled jobs can be a teen's: pin one valid at 24
            text = describe.describe(npc)
            assert text.startswith("A 24-year-old ") or text.startswith("An 24")  # "A 24-year-old woman"
            assert len(text) <= describe.MAX_DESCRIPTION
            assert "{" not in text and "}" not in text
            assert describe.adult_problem(npc, text) is None, (setting, style, text)
            assert not any(f" {w} " in f" {text.lower()} " for w in child_words), (setting, style, text)


def test_an_adult_whose_job_names_youth_is_not_refused_for_it_but_typed_words_still_are():
    data = roll.load_data()
    for setting in data["settings"]:
        for job in _occupations(data["settings"][setting]):
            if job["max_age"] < 18:
                continue
            npc = _npc(setting, "photoreal", age=max(job["min_age"], 18), occupation=job["label"])
            assert describe.adult_problem(npc, describe.describe(npc)) is None, (setting, job["label"])
    runner = _npc("cyberpunk", age=19, occupation="street kid runner")
    assert describe.adult_problem(runner, "A 19-year-old woman, a street kid runner.") is None
    assert describe.adult_problem(runner, "A 19-year-old woman, a street kid runner. A kid voice.")


def test_a_chibi_adult_is_small_and_cute_but_said_to_be_an_adult():
    text = describe.describe(_npc("modern", "chibi", age=27))
    assert "cute" in text and "clearly an adult" in text


def test_a_child_character_is_described_at_their_real_age_and_cannot_be_flirty():
    child = _npc("modern", "chibi", age=9, gender="male")
    text = describe.describe(child)
    assert text.startswith("A 9-year-old boy") and "adult" not in text
    assert describe.mood_problem(child, "flirty") and describe.mood_problem(_npc(age=30), "flirty") is None


def test_race_setting_and_style_cues_reach_the_description():
    wolf = _npc("anthro", "semi_real", age=30)
    wolf["profile"]["race"] = "Wolf anthro"
    assert "growl" in describe.describe(wolf)
    assert "drawl" in describe.describe(_npc("western", "photoreal", age=30))


def test_an_adult_voice_described_as_a_child_is_refused():
    adult = _npc(age=25)
    assert describe.adult_problem(adult, "A cute little girl voice")
    assert describe.adult_problem(adult, "sounds like a 12-year-old")
    assert describe.adult_problem(adult, "Valley-girl accent, bubbly, a minor rasp, 25 years old") is None
    assert describe.adult_problem(_npc(age=10), "a little girl") is None      # a child character may sound like one


def _chip(group, chip_id):
    data = describe.load_data()
    return next(c["text"] for g in data["chip_groups"] if g["id"] == group for c in g["chips"] if c["id"] == chip_id)


def test_compose_adds_the_voice_type_and_chips_in_order():
    text = describe.compose("A calm woman.", "valley_girl", {"pitch": "high"})
    assert text == "A calm woman. Valley-girl accent: bubbly California upspeak with a little vocal fry. High-pitched voice."
    assert describe.with_mood("Base.", "sad") == "Base. Sounds sad and quiet, close to tears."


def test_an_accent_chip_leads_and_nothing_contradicts_it():
    """Clara's 12 takes (2026-09-24): Valley Girl + British/Japanese chip, the accent last after 90 words, no accent heard."""
    british = _chip("accent", "british")
    text = describe.compose("A calm woman.", "valley_girl", {"pitch": "high", "accent": "british"})
    assert text.startswith(british + " A calm woman.")
    assert "California" not in text and "Valley-girl accent" not in text and "upspeak" in text    # the preset keeps its manner
    assert text.endswith("High-pitched voice.")
    pirate = describe.compose("A sailor.", "pirate", {"accent": "irish"})
    assert "West Country" not in pirate and pirate.startswith(_chip("accent", "irish"))
    sheriff = describe.describe(_npc("western", age=40, gender="male"))
    assert "Old West drawl" in sheriff
    assert "Old West drawl" not in describe.compose(sheriff, None, {"accent": "scottish"})
    assert "Old West drawl" in describe.compose(sheriff, None, {"pitch": "low"})           # no accent chip: the cue stays
    long_desc = describe.compose("A long description. " * 20, "valley_girl", {"accent": "japanese", "pitch": "high"})
    assert len(long_desc) <= describe.MAX_INSTRUCTION and long_desc.startswith(_chip("accent", "japanese"))
    assert long_desc.endswith(".")                                                          # never cut mid-sentence
    assert not engines.can("qwen", "accents") and engines.can("breeze", "accents")          # measured, see engines.py
    for vt in describe.load_data()["voice_types"]:                                          # every preset naming an accent
        if "accent" in vt["text"].lower() or any(w in vt["text"] for w in ("British", "Southern", "burr", "California")):
            assert "accent_free" in vt, vt["id"]


def test_articles_and_stock_matches():
    assert describe._a("18-year-old woman").startswith("An ") and describe._a("24-year-old woman").startswith("A ")
    assert describe._a("engineer") == "An engineer" and describe._a("university lecturer").startswith("A ")
    man = _npc(age=40, gender="male")
    assert describe.stock_match(man)[0].startswith("am_")
    posh = _npc("steampunk", age=40)
    assert describe.stock_match(posh)[0].startswith("bf_")


# ---- sound tags ------------------------------------------------------------------------------------------------------------
def test_sound_tags_are_rewritten_for_each_engine_or_dropped():
    line = "[laugh] You did not! (sighs) Fine. [chuckle]"
    assert markup.for_engine(line, "breeze") == "(laugh) You did not! (sigh) Fine. (laugh)"
    assert markup.for_engine(line, "chatterbox") == "[laugh] You did not! Fine. [chuckle]"
    assert markup.for_engine(line, "qwen") == "You did not! Fine."
    assert markup.plain(line) == "You did not! Fine."
    assert markup.tags_in(line) == ["laugh", "sigh", "chuckle"]


# ---- audio ---------------------------------------------------------------------------------------------------------------------
def test_finish_trims_silence_levels_loudness_and_tracks_the_mouth():
    wav, seconds, mouth = audio.finish(_wav(1.0, amp=0.05))
    x, rate = audio.read_wav(wav)
    assert 1.0 <= seconds <= 1.2                                    # 0.3 s of silence cut at each end, 0.08 s kept
    rms = 20 * np.log10(np.sqrt(np.mean(x[int(0.1 * rate):-int(0.1 * rate)] ** 2)))
    assert abs(rms - audio.TARGET_RMS_DB) < 1.5
    assert len(mouth) == pytest.approx(seconds * 25, abs=2) and max(mouth) > 0.8 and min(mouth) < 0.3


def test_read_wav_handles_stereo_and_24_bit():
    rate = 16000
    t = np.arange(rate) / rate
    stereo = np.stack([np.sin(2 * np.pi * 300 * t), np.sin(2 * np.pi * 300 * t)], axis=1)
    raw = (stereo * 0.5 * (2 ** 23 - 1)).astype(np.int32)
    b = raw.reshape(-1)
    packed = np.stack([b & 0xFF, (b >> 8) & 0xFF, (b >> 16) & 0xFF], axis=1).astype(np.uint8).tobytes()
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(3)
        w.setframerate(rate)
        w.writeframes(packed)
    x, got = audio.read_wav(out.getvalue())
    assert got == rate and len(x) == rate and 0.45 < np.max(np.abs(x)) < 0.55


def test_an_upload_must_be_a_real_sample_of_speech():
    assert audio.check_upload(_wav(4.0))[1] > 4
    for bad, why in ((_wav(1.0), "too short"), (_wav(45.0, rate=8000), "too long"), (b"not a wav", "readable"),
                     (_wav(5.0, amp=0.0), "silent")):
        with pytest.raises(ValueError, match=why):
            audio.check_upload(bad)


# ---- settings ------------------------------------------------------------------------------------------------------------------
def test_settings_are_checked_against_what_the_model_can_do():
    npc = _npc(age=30)
    clean = record.clean_settings({"engine": "qwen", "mode": "design", "description": "A warm voice.", "language": "Japanese",
                                   "voice_type": "anime", "chips": {"pitch": "high"}}, npc)
    assert clean["language"] == "Japanese" and clean["chips"] == {"pitch": "high"}
    for raw, why in (({"engine": "kokoro", "mode": "design"}, "cannot make a voice"),
                     ({"engine": "qwen", "mode": "stock"}, "no stock voices"),
                     ({"engine": "breeze", "mode": "design", "language": "Japanese"}, "does not speak"),
                     ({"engine": "qwen", "chips": {"pitch": "ultrasonic"}}, "Unknown pitch"),
                     ({"engine": "nope"}, "Unknown voice model"),
                     ({"engine": "qwen", "description": "a little girl"}, "adult"),
                     ({"engine": "qwen", "speaker": "kokoro", "mode": "design"}, None)):
        if why is None:
            continue
        with pytest.raises(ValueError, match=why):
            record.clean_settings(raw, npc)


def test_a_non_commercial_model_marks_the_voice():
    v = record.voice_of({"voice": {"engine": "breeze", "mode": "design"}})
    assert record.commercial_ok(v) is False
    assert record.commercial_ok(record.voice_of({"voice": {"engine": "qwen"}})) is True
    assert record.speaker_for(record.voice_of({"voice": {"engine": "kokoro", "mode": "stock", "stock_voice": "af_heart"}})) == "kokoro"
    assert record.speaker_for(record.voice_of({"voice": {"engine": "breeze", "speaker": "qwen"}})) == "qwen"


# ---- the job queue's quick lane ---------------------------------------------------------------------------------------------------
def test_the_quick_lane_goes_first_but_never_ahead_of_shutdown():
    q = JobQueue()
    q.put("slow-1")
    q.put("slow-2")
    q.put("quick", JobQueue.QUICK)
    q.put(None)
    assert [q.get_nowait() for _ in range(4)] == [None, "quick", "slow-1", "slow-2"]
    assert q.empty()


class _NoComfy:
    models = {}


def test_takes_pick_test_and_lines_run_through_the_job_queue(tmp_path, monkeypatch):
    monkeypatch.setattr(lab_voice, "speak", lambda job, request: _wav(3.0))
    lib = Library(tmp_path / "library")
    jobs = Jobs({}, _NoComfy(), lib)
    try:
        npc = lib.create(_npc(age=26, first_name="Nong"))
        clean = record.clean_settings({"engine": "qwen", "mode": "design", "description": "A bright, quick voice.",
                                       "voice_type": "anime"}, npc)
        npc = lib.update(npc["id"], lambda rec: record.save_settings(rec, clean))

        job = jobs.submit(npc, {"voice": service.takes_request(npc, {"count": 3, "auto_pick": True})}, {})
        row = _wait(jobs, job["id"])
        assert row["status"] == "done", row
        v = lib.get(npc["id"])["voice"]
        assert len(v["takes"]) == 3 and len({t["seed"] for t in v["takes"]}) == 3
        assert all(lib.resolve(npc["id"], t["file"]).is_file() for t in v["takes"])
        assert v["reference"]["source"] == f"take:{v['takes'][0]['id']}" and v["review"] == "pending"
        assert "Nong" in v["takes"][0]["text"]

        picked = record.select_take(lib, npc["id"], v["takes"][2]["id"])["voice"]
        assert picked["review"] == "picked" and picked["chosen_take"] == v["takes"][2]["id"]

        npc = lib.update(npc["id"], lambda rec: lab_voice.save(rec, {
            "identity_id": "1" * 32, "name": "Nong", "kind": "cast", "model": "qwen3-tts-1.7b-base", "takes_direction": True}))
        row = _wait(jobs, jobs.submit(npc, {"voice": service.test_request(npc, {"text": "[laugh] Try me.", "mood": "happy"})}, {})["id"])
        assert row["status"] == "done", row
        assert lib.get(npc["id"])["voice"]["tests"]["try"]["mood"] == "happy"

        from npcgen import dialogue
        npc = lib.update(npc["id"], lambda rec: rec.update(dialogue=dialogue.generate(rec)))
        request = service.lines_request(npc, {"ids": ["greeting_1", "farewell_1"], "moods": {"greeting_1": "excited"}})
        row = _wait(jobs, jobs.submit(npc, {"voice": request}, {})["id"])
        assert row["status"] == "done", row
        npc = lib.get(npc["id"])
        lines = npc["voice_lines"]
        assert set(lines) == {"greeting_1", "farewell_1"} and lines["greeting_1"]["mood"] == "excited"
        assert lines["greeting_1"]["mouth"] and not record.line_is_stale(npc, "greeting_1")
        npc["dialogue"]["lines"]["greeting"][0]["text"] = "Something new."
        assert record.line_is_stale(npc, "greeting_1") and not record.line_is_stale(npc, "farewell_1")
        lib.update(npc["id"], lambda rec: rec["voice"]["lab_voice"].update(identity_id="2" * 32))
        assert record.line_is_stale(lib.get(npc["id"]), "farewell_1")       # a new voice: every line is out of date
    finally:
        jobs.close()


def test_choosing_a_take_makes_its_model_speak_the_lines(tmp_path):
    lib = Library(tmp_path / "library")
    npc = lib.create(_npc(age=30))
    lib.write_asset(npc["id"], "voice/takes/aa.wav", _wav(1.0))
    lib.write_asset(npc["id"], "voice/takes/bb.wav", _wav(1.0))
    takes = [{"id": "aa", "n": 1, "file": "voice/takes/aa.wav", "engine": "qwen", "mode": "design", "text": "Hi."},
             {"id": "bb", "n": 1, "file": "voice/takes/bb.wav", "engine": "kokoro", "mode": "stock", "stock_voice": "bf_emma",
              "text": "Hi."}]
    lib.update(npc["id"], lambda rec: rec.update(voice={"engine": "kokoro", "mode": "stock", "stock_voice": "af_heart",
                                                        "takes": takes}))
    v = record.select_take(lib, npc["id"], "aa")["voice"]            # a Qwen take, picked while the settings said Kokoro
    assert (v["engine"], v["mode"], record.speaker_for(v)) == ("qwen", "design", "qwen")
    v = record.select_take(lib, npc["id"], "bb")["voice"]
    assert (v["engine"], v["mode"], v["stock_voice"], record.speaker_for(v)) == ("kokoro", "stock", "bf_emma", "kokoro")


def test_a_stock_voice_takes_the_best_matches_in_turn():
    npc = _npc(age=40, gender="female")
    npc["voice"] = {"engine": "kokoro", "mode": "stock", "stock_voice": "af_sky"}
    req = service.takes_request(npc, {"count": 3})
    assert req["stock_order"][0] == "af_sky" and len(req["stock_order"]) == 3 and req["quick"]


def test_requests_that_cannot_work_are_refused_before_queueing():
    npc = _npc(age=30)
    npc["voice"] = {"engine": "qwen", "mode": "upload"}
    with pytest.raises(ValueError, match="used as it is"):
        service.takes_request(npc, {})
    with pytest.raises(ValueError, match="no dialogue"):
        service.lines_request(npc, {})
    with pytest.raises(ValueError, match="flirty"):
        service.test_request(_npc(age=12), {"mood": "flirty"})


def test_an_upload_needs_consent_and_the_words(tmp_path):
    lib = Library(tmp_path / "library")
    npc = lib.create(_npc(age=30))
    body = {"audio": "data:audio/wav;base64," + __import__("base64").b64encode(_wav(4.0, rate=16000)).decode(), "text": "Hello there."}
    with pytest.raises(ValueError, match="right to use"):
        service.apply_upload(lib, npc, body)
    with pytest.raises(ValueError, match="exact words"):
        service.apply_upload(lib, npc, {**body, "consent": True, "text": ""})
    done = service.apply_upload(lib, npc, {**body, "consent": True})
    assert done["voice"]["mode"] == "upload" and done["voice"]["reference"]["consent"] is True
    other = lib.create(_npc(age=31))
    copied = service.apply_copy(lib, other, lib.get(npc["id"]))
    assert copied["voice"]["reference"]["source"] == f"copy:{npc['id']}"
    assert lib.read_asset(other["id"], record.REFERENCE) == lib.read_asset(npc["id"], record.REFERENCE)
