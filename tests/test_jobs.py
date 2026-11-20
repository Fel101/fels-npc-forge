import json
import threading
import time
import io
from types import SimpleNamespace

from PIL import Image
import pytest

from npcgen.jobs import Job, Jobs
from npcgen.library import Library


class DummyComfy:
    models = {}


def _record(status, steps, *, job_id="job-recovery"):
    job = Job("mira-vale-123456", "Mira Vale", steps, {"quality": "draft"})
    job.id = job_id
    job.status = status
    return job.to_record()


def _step(key, status):
    return {"kind": "test", "key": key, "label": key, "status": status, "error": None}


def test_startup_recovers_interrupted_steps_and_keeps_user_cancellation_terminal(tmp_path, monkeypatch):
    state_path = tmp_path / "jobs.json"
    state_path.write_text(json.dumps([
        _record("running", [_step("done", "done"), _step("interrupted", "running"), _step("pending", "pending")]),
        _record("running", [_step("cancelled", "running"), _step("not-started", "pending")],
                job_id="job-user-cancelled") | {"cancel_reason": "user"},
    ]), encoding="utf-8")
    monkeypatch.setattr(Jobs, "_worker", lambda self: None)

    jobs = Jobs({}, DummyComfy(), Library(tmp_path / "library"), state_path=state_path)
    rows = {row["id"]: row for row in jobs.snapshot()}
    recovered = rows["job-recovery"]
    user_cancelled = rows["job-user-cancelled"]

    assert recovered["status"] == "queued"
    assert [step["status"] for step in recovered["steps"]] == ["done", "pending", "pending"]
    assert user_cancelled["status"] == "cancelled"
    assert [step["status"] for step in user_cancelled["steps"]] == ["cancelled", "cancelled"]
    assert jobs.queue.get_nowait().id == "job-recovery"
    jobs.close()


def test_retry_keeps_successful_steps_and_runs_only_unfinished_steps(tmp_path, monkeypatch):
    state_path = tmp_path / "jobs.json"
    state_path.write_text(json.dumps([
        _record("error", [_step("already-done", "done"), _step("retry-me", "error"), _step("after", "pending")])
    ]), encoding="utf-8")
    attempted = []
    finished = threading.Event()

    def run_step(self, job, step):
        attempted.append(step["key"])
        if step["key"] == "after":
            finished.set()

    monkeypatch.setattr(Jobs, "_run_step", run_step)
    jobs = Jobs({}, DummyComfy(), Library(tmp_path / "library"), state_path=state_path)
    try:
        retried = jobs.retry("job-recovery")
        assert retried["status"] == "queued"
        assert finished.wait(3), "worker did not finish retry steps"
        deadline = time.monotonic() + 3
        while jobs.snapshot()[0]["status"] in ("queued", "running") and time.monotonic() < deadline:
            time.sleep(0.01)

        result = jobs.snapshot()[0]
        assert result["status"] == "done"
        assert attempted == ["retry-me", "after"]
        assert [step["status"] for step in result["steps"]] == ["done", "done", "done"]
    finally:
        jobs.close()


def _binding(engine, files):
    from npcgen.voice.binding import signature
    value = {"schema": 1, "engine": engine, "source": "queue", "python": "python.exe", "env": {}, "parts": {},
             "runtime_files": files, "runtime_sha256": signature(files)}
    value["id"] = signature(value)
    return value


def test_a_voice_jobs_frozen_files_are_saved_once_beside_the_job_file_and_come_back_whole(tmp_path, monkeypatch):
    """The binding of a voice engine is thousands of files; it used to be copied into every job record (5 MB for 32 jobs)."""
    big = _binding("drama3", {f"C:/lab/file{n}.py": "0" * 64 for n in range(2000)})
    old = _record("done", [_step("line", "done")], job_id="job-old")
    old["options"]["voice_runtime"] = {"drama3": big, "kokoro": {"unavailable": "not installed"}}
    state_path = tmp_path / "jobs.json"
    state_path.write_text(json.dumps([old]), encoding="utf-8")         # an older file: the whole binding inside the record
    monkeypatch.setattr(Jobs, "_worker", lambda self: None)

    jobs = Jobs({}, DummyComfy(), Library(tmp_path / "library"), state_path=state_path)
    assert jobs.jobs["job-old"].options["voice_runtime"]["drama3"] == big
    jobs._persist()
    saved = json.loads(state_path.read_text("utf-8"))[0]["options"]["voice_runtime"]
    assert saved == {"drama3": {"stored": big["id"]}, "kokoro": {"unavailable": "not installed"}}
    assert json.loads((jobs.bindings_dir / f"{big['id']}.json").read_text("utf-8")) == big
    assert state_path.stat().st_size < 5000
    jobs.close()

    again = Jobs({}, DummyComfy(), Library(tmp_path / "library"), state_path=state_path)
    assert again.jobs["job-old"].options["voice_runtime"]["drama3"] == big     # read back whole, so its checksum still verifies
    again.close()


def test_a_missing_binding_file_makes_the_voice_engine_unavailable_and_unused_files_are_removed(tmp_path, monkeypatch):
    big = _binding("qwen", {"C:/lab/a.py": "1" * 64})
    row = _record("done", [_step("line", "done")], job_id="job-gone")
    row["options"]["voice_runtime"] = {"qwen": {"stored": big["id"]}}
    state_path = tmp_path / "jobs.json"
    state_path.write_text(json.dumps([row]), encoding="utf-8")
    monkeypatch.setattr(Jobs, "_worker", lambda self: None)
    stray = tmp_path / ".npcforge-voice-bindings" / ("f" * 64 + ".json")
    stray.parent.mkdir()
    stray.write_text("{}", encoding="utf-8")

    jobs = Jobs({}, DummyComfy(), Library(tmp_path / "library"), state_path=state_path)
    assert "unavailable" in jobs.jobs["job-gone"].options["voice_runtime"]["qwen"]
    jobs._persist()
    assert not stray.exists()
    jobs.close()


def _wait_for(check, seconds=5):
    deadline = time.monotonic() + seconds
    while not check() and time.monotonic() < deadline:
        time.sleep(0.01)
    return check()


def _two_failed_jobs(tmp_path):
    state_path = tmp_path / "jobs.json"
    state_path.write_text(json.dumps([_record("error", [_step("first", "error")]),
                                      _record("error", [_step("second", "error")], job_id="job-second")]), encoding="utf-8")
    return state_path


def test_a_job_started_soon_after_the_last_one_finds_the_gpu_still_loaded(tmp_path, monkeypatch):
    released, ran = [], []
    monkeypatch.setattr("npcgen.jobs.engines.release", lambda comfy: released.append("engine"))
    monkeypatch.setattr("npcgen.jobs.gpu.release_comfy", lambda comfy: released.append("comfy"))
    monkeypatch.setattr(Jobs, "_run_step", lambda self, job, step: ran.append(step["key"]))
    jobs = Jobs({"gpu": {"unload_idle_seconds": 30}}, DummyComfy(), Library(tmp_path / "library"),
                state_path=_two_failed_jobs(tmp_path))
    try:
        jobs.retry("job-recovery")
        assert _wait_for(lambda: jobs.jobs["job-recovery"].status == "done")
        jobs.retry("job-second")                       # the queue ran empty, but the next job came within the 30 s
        assert _wait_for(lambda: jobs.jobs["job-second"].status == "done")
        assert ran == ["first", "second"] and released == []
    finally:
        jobs.close()
    assert released == ["engine", "comfy"]             # closing leaves nothing of NPC Forge's on the card


def test_the_gpu_is_unloaded_once_the_queue_has_stayed_empty(tmp_path, monkeypatch):
    finished, released = [], []
    monkeypatch.setattr("npcgen.jobs.engines.release", lambda comfy: None)
    monkeypatch.setattr("npcgen.jobs.gpu.release_comfy", lambda comfy: released.append(time.monotonic()))
    monkeypatch.setattr(Jobs, "_run_step", lambda self, job, step: finished.append(time.monotonic()))
    jobs = Jobs({"gpu": {"unload_idle_seconds": 0.3}}, DummyComfy(), Library(tmp_path / "library"),
                state_path=_two_failed_jobs(tmp_path))
    try:
        jobs.retry("job-recovery")
        assert _wait_for(lambda: len(released) == 1)
        assert released[0] - finished[0] >= 0.29          # not at once: only after the queue stayed empty for 0.3 s
    finally:
        jobs.close()
    assert len(released) == 1                              # already unloaded: nothing more at shutdown


def test_job_journal_is_written_atomically_on_submit(tmp_path, monkeypatch):
    monkeypatch.setattr(Jobs, "_worker", lambda self: None)
    library = Library(tmp_path / "library")
    jobs = Jobs({}, DummyComfy(), library)
    npc = {"id": "mira-vale-123456", "profile": {"first_name": "Mira", "last_name": "Vale"}}
    try:
        created = jobs.submit(npc, {"portrait": True}, {"quality": "draft"})
        rows = json.loads((library.root / ".npcforge-jobs.json").read_text("utf-8"))
        assert rows[0]["id"] == created["id"] and rows[0]["status"] == "queued"
        assert not (library.root / ".npcforge-jobs.json.tmp").exists()
    finally:
        jobs.close()


def test_unsupported_body_plans_are_rejected_before_a_job_is_queued(tmp_path, monkeypatch):
    monkeypatch.setattr(Jobs, "_worker", lambda self: None)
    library = Library(tmp_path / "library")
    jobs = Jobs({}, DummyComfy(), library)
    npc = {"id": "taur-npc-123456", "setting": "anthro", "profile": {
        "first_name": "Mira", "last_name": "Vale", "race": "Taur"}}
    try:
        with pytest.raises(ValueError, match="stay blocked until a checked method is ready"):
            jobs.submit(npc, {"sprite": True}, {"quality": "draft"})
        assert jobs.snapshot() == []
    finally:
        jobs.close()


def _png(color):
    out = io.BytesIO()
    Image.new("RGBA", (8, 8), color).save(out, "PNG")
    return out.getvalue()


def test_a_monochrome_style_stores_grey_pictures_and_keeps_the_raw_in_colour(tmp_path):
    library = Library(tmp_path / "library")
    npc = library.create({"setting": "modern", "style": "manga", "seed": 11, "appearance": {},
                          "profile": {"first_name": "Mira", "last_name": "Vale", "age": 30, "gender": "female", "occupation": "tester"}})
    drawn = Image.new("RGB", (120, 180), (235, 27, 156))                  # a red figure on the magenta key
    drawn.paste((200, 30, 30), (40, 30, 80, 160))
    out = io.BytesIO(); drawn.save(out, "PNG")
    jobs = Jobs.__new__(Jobs)
    jobs.lib = library
    job = SimpleNamespace(npc_id=npc["id"], options={})
    jobs._store(job, "sprite", "sprite", out.getvalue(), True, {"monochrome": True}, "magenta", False,
                {"positive": "p", "negative": ""}, 1, "draft", "manga")

    stored = library.get(npc["id"])["assets"]["sprite"]
    assert stored["transparent"] is True
    sprite = Image.open(io.BytesIO(library.read_asset(npc["id"], stored["file"]))).convert("RGBA")
    r, g, b, a = sprite.getpixel((60, 90))
    assert a == 255 and r == g == b                                      # the red figure is now grey
    raw = Image.open(io.BytesIO(library.read_asset(npc["id"], stored["raw"]))).convert("RGB")
    assert raw.getpixel((60, 90)) == (200, 30, 30)                       # the raw keeps its colour for re-cutting


def test_reroll_candidates_leave_the_active_asset_untouched_until_selected(tmp_path):
    library = Library(tmp_path / "library")
    npc = library.create({
        "setting": "modern", "style": "semi_real", "seed": 11,
        "profile": {"first_name": "Mira", "last_name": "Vale", "age": 30, "gender": "female", "occupation": "tester"},
        "appearance": {},
        "assets": {
            "portrait": {"file": "portrait.png", "style": "semi_real", "created": "original"},
            "sprite": {"file": "sprite.png", "mode": "match"},
            "expressions": {"happy": {"file": "expressions/happy.png"}},
        },
    })
    original = _png((210, 20, 20, 255))
    candidate_png = _png((20, 20, 210, 255))
    library.write_asset(npc["id"], "portrait.png", original)
    jobs = Jobs.__new__(Jobs)
    jobs.lib = library
    job = SimpleNamespace(npc_id=npc["id"], options={"save_candidates": True})
    prompt = {"positive": "new portrait", "negative": ""}

    jobs._store(job, "portrait", "portrait", candidate_png, False, {}, "blue", False, prompt,
                42, "draft", "pixel")

    after_generate = library.get(npc["id"])
    candidate = after_generate["assets"]["candidates"]["portrait"][0]
    assert library.read_asset(npc["id"], "portrait.png") == original
    assert library.read_asset(npc["id"], candidate["file"]) == candidate_png
    assert after_generate["assets"]["portrait"]["created"] == "original"

    selected = library.select_asset_candidate(npc["id"], "portrait", candidate["id"])
    assert library.read_asset(npc["id"], "portrait.png") == candidate_png
    assert selected["assets"]["portrait"]["candidate_id"] == candidate["id"]
    assert selected["style"] == "pixel"
    assert selected["assets"]["sprite"]["stale"] is True
    assert selected["assets"]["expressions"]["happy"]["stale"] is True
    assert len(selected["assets"]["candidates"]["portrait"]) == 2


def _redraw_world(tmp_path):
    library = Library(tmp_path / "library")
    npc = library.create({"setting": "modern", "style": "semi_real", "seed": 11, "appearance": {},
                          "profile": {"first_name": "Mira", "last_name": "Vale", "age": 30, "gender": "female", "occupation": "tester"},
                          "assets": {"portrait": {"file": "portrait.png", "style": "semi_real", "created": "original", "seed": 3},
                                     "expressions": {"happy": {"file": "expressions/happy.png", "created": "first-happy", "seed": 4}}}})
    library.write_asset(npc["id"], "portrait.png", _png((210, 20, 20, 255)))
    library.write_asset(npc["id"], "expressions/happy.png", _png((20, 210, 20, 255)))
    jobs = Jobs.__new__(Jobs)
    jobs.lib = library
    return library, npc["id"], jobs


def test_a_redrawn_picture_keeps_the_one_it_replaces_among_its_earlier_versions(tmp_path):
    library, npc_id, jobs = _redraw_world(tmp_path)
    job = SimpleNamespace(npc_id=npc_id, options={})
    prompt = {"positive": "new portrait", "negative": ""}
    assert jobs._store(job, "portrait", "portrait", _png((20, 20, 210, 255)), False, {}, "blue", False, prompt, 42, "draft", "semi_real") is True
    assert jobs._store(job, ("expressions", "happy"), "expressions/happy", _png((250, 250, 20, 255)), False, {}, "blue", False, prompt,
                       43, "draft", "semi_real") is True

    after = library.get(npc_id)["assets"]
    assert after["portrait"]["seed"] == 42 and library.read_asset(npc_id, "portrait.png") == _png((20, 20, 210, 255))
    (old_portrait,) = after["candidates"]["portrait"]
    assert (old_portrait["seed"], old_portrait["created"]) == (3, "original") and library.read_asset(npc_id, old_portrait["file"]) == _png((210, 20, 20, 255))
    (old_happy,) = after["candidates"]["expressions/happy"]
    assert (old_happy["seed"], old_happy["created"]) == (4, "first-happy") and library.read_asset(npc_id, old_happy["file"]) == _png((20, 210, 20, 255))
    # the first picture of a kind has nothing to keep
    assert jobs._store(job, ("expressions", "sad"), "expressions/sad", _png((1, 2, 3, 255)), False, {}, "blue", False, prompt, 44, "draft", "semi_real") is True
    assert "expressions/sad" not in library.get(npc_id)["assets"]["candidates"]


def test_a_reroll_saved_beside_the_picture_says_it_did_not_replace_it_and_keeps_nothing_extra(tmp_path):
    library, npc_id, jobs = _redraw_world(tmp_path)
    job = SimpleNamespace(npc_id=npc_id, options={"save_candidates": True})
    assert jobs._store(job, "portrait", "portrait", _png((20, 20, 210, 255)), False, {}, "blue", False,
                       {"positive": "p", "negative": ""}, 42, "draft", "semi_real") is False
    candidates = library.get(npc_id)["assets"]["candidates"]["portrait"]
    assert len(candidates) == 1 and "replaced" not in candidates[0]            # the saved variant only: the one in use was not archived


def test_the_history_can_be_turned_off(tmp_path):
    library, npc_id, jobs = _redraw_world(tmp_path)
    library.history_keep = 0
    job = SimpleNamespace(npc_id=npc_id, options={})
    assert jobs._store(job, "portrait", "portrait", _png((20, 20, 210, 255)), False, {}, "blue", False,
                       {"positive": "p", "negative": ""}, 42, "draft", "semi_real") is True
    assert "candidates" not in library.get(npc_id)["assets"]
