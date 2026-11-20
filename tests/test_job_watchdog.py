"""The job worker's timing, retry and watchdog (npcgen/jobs.py): every step says when it started and when it last showed a sign
of life, a GPU hand-off that failed is tried again, and a step nothing can stop (or one that went silent) no longer holds the
queue for ever.

Nothing here needs ComfyUI: the steps are stand-ins that block, beat or fail on cue."""
import threading
import time
from types import SimpleNamespace

import pytest

from npcgen import engines, gpu
from npcgen.comfy import Cancelled, ComfyError
from npcgen.jobs import STALLED, Job, Jobs
from npcgen.library import Library


class DummyComfy:
    models = {}


def make_jobs(tmp_path, monkeypatch, run_step, **jobs_config):
    """A worker whose steps are `run_step(self, job, step)`, with fast timings."""
    monkeypatch.setattr(Jobs, "_run_step", run_step)
    cfg = {"jobs": {"retry_pause_seconds": 0, "cancel_grace_seconds": 0.3, "watch_seconds": 0.05, **jobs_config}}
    return Jobs(cfg, DummyComfy(), Library(tmp_path / "library"), state_path=tmp_path / "jobs.json")


def queue_job(jobs, *keys, options=None):
    steps = [{"kind": "test", "key": key, "label": key, "status": "pending", "error": None} for key in keys]
    job = Job("mira-vale-123456", "Mira Vale", steps, options or {"quality": "draft"})
    job.on_change, job.lock = jobs._persist, jobs.lock
    with jobs.lock:
        jobs.jobs[job.id] = job
    jobs.queue.put(job)
    return job


def wait_for(condition, seconds=5.0, what="the condition"):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if condition():
            return
        time.sleep(0.01)
    raise AssertionError(f"timed out waiting for {what}")


def row(jobs, job):
    return next(r for r in jobs.snapshot() if r["id"] == job.id)


def finished(jobs, job):
    return lambda: row(jobs, job)["status"] in ("done", "error", "cancelled")


# ---- NF-11: when each step started and when it last made progress -------------------------------------------------------------
def test_steps_and_jobs_say_when_they_started_and_when_they_last_made_progress(tmp_path, monkeypatch):
    seen = {}

    def run_step(self, job, step):
        job.cancel.beat()                                      # what a ComfyUI poll does
        seen[step["key"]] = dict(row(self, job)["steps"][0 if step["key"] == "one" else 1])

    jobs = make_jobs(tmp_path, monkeypatch, run_step)
    try:
        job = queue_job(jobs, "one", "two")
        wait_for(finished(jobs, job), what="the job")
        result = row(jobs, job)
        assert result["started_at"] and result["finished_at"] and result["created"] <= result["started_at"] <= result["finished_at"]
        for step in result["steps"]:
            assert step["started_at"] and step["last_progress_at"] >= step["started_at"] and step["status"] == "done"
        assert seen["one"]["status"] == "running" and seen["one"]["started_at"]       # visible while it runs
        saved = jobs.jobs[job.id].to_record()                                            # and kept in the saved file
        assert saved["started"] == result["started_at"] and saved["steps"][0]["started_at"]
    finally:
        jobs.close()


def test_a_wait_names_what_it_waits_for_beside_the_running_step_and_takes_the_words_down_again(tmp_path, monkeypatch):
    shown = []

    def run_step(self, job, step):
        job.cancel.beat("waiting for ComfyUI: another job is using it")
        shown.append(row(self, job)["steps"][0].get("detail"))
        job.cancel.beat(None)
        shown.append(row(self, job)["steps"][0].get("detail"))
        step["detail"] = "frame 2/4"                               # a step's own detail is not for beat() to remove
        job.cancel.beat(None)
        shown.append(row(self, job)["steps"][0].get("detail"))

    jobs = make_jobs(tmp_path, monkeypatch, run_step)
    try:
        job = queue_job(jobs, "one")
        wait_for(finished(jobs, job))
        assert shown == ["waiting for ComfyUI: another job is using it", None, "frame 2/4"]
    finally:
        jobs.close()


def test_a_job_put_back_in_the_queue_forgets_its_old_step_times(tmp_path, monkeypatch):
    attempts = []

    def run_step(self, job, step):
        attempts.append(1)
        if len(attempts) == 1:
            raise ComfyError("something broke")

    jobs = make_jobs(tmp_path, monkeypatch, run_step)
    try:
        job = queue_job(jobs, "one")
        wait_for(finished(jobs, job))
        assert row(jobs, job)["status"] == "error" and row(jobs, job)["steps"][0]["started_at"]
        retried = jobs.retry(job.id)                              # taken under the lock, before the worker can start the step again
        assert retried["steps"][0]["status"] == "pending" and retried["finished_at"] is None
        assert not {"started_at", "last_progress_at", "attempts", "detail"} & set(retried["steps"][0])
        wait_for(finished(jobs, job))
        assert row(jobs, job)["status"] == "done" and row(jobs, job)["steps"][0]["started_at"]
    finally:
        jobs.close()


# ---- NF-09: a GPU hand-off that failed is tried again, a real fault is not ----------------------------------------------------------
def test_a_gpu_that_was_not_empty_yet_is_tried_again_and_the_job_succeeds(tmp_path, monkeypatch):
    calls = []

    def run_step(self, job, step):
        calls.append(time.monotonic())
        if len(calls) < 3:
            raise gpu.GpuBusy("ComfyUI still holds 4.6 GB of GPU memory 90 s after it was told to unload everything")

    jobs = make_jobs(tmp_path, monkeypatch, run_step)
    try:
        job = queue_job(jobs, "one", "two")
        wait_for(finished(jobs, job))
        result = row(jobs, job)
        assert result["status"] == "done" and result["error"] is None and len(calls) == 4          # 3 tries of step one, 1 of step two
        assert result["steps"][0]["attempts"] == 3 and "attempts" not in result["steps"][1]
    finally:
        jobs.close()


def test_the_tries_stop_at_the_limit_and_the_error_is_kept(tmp_path, monkeypatch):
    calls = []

    def run_step(self, job, step):
        calls.append(1)
        raise gpu.GpuBusy("ComfyUI still holds 4.6 GB of GPU memory")

    jobs = make_jobs(tmp_path, monkeypatch, run_step, transient_retries=2)
    try:
        job = queue_job(jobs, "one")
        wait_for(finished(jobs, job))
        result = row(jobs, job)
        assert result["status"] == "error" and "still holds 4.6 GB" in result["error"] and len(calls) == 3
        assert result["steps"][0]["attempts"] == 3
    finally:
        jobs.close()


def test_a_big_engine_that_found_too_little_memory_is_tried_again_but_missing_files_are_not(tmp_path, monkeypatch):
    calls = []

    def run_step(self, job, step):
        calls.append(step["key"])
        if step["key"] == "memory" and calls.count("memory") < 2:
            raise engines.EngineError("Qwen could not draw it: not enough free memory", transient=True)
        if step["key"] == "files":
            raise engines.EngineError("Qwen could not draw it: ComfyUI is missing diffusion_models/x.safetensors")

    jobs = make_jobs(tmp_path, monkeypatch, run_step)
    try:
        job = queue_job(jobs, "memory", "files")
        wait_for(finished(jobs, job))
        assert calls == ["memory", "memory", "files"]
        steps = row(jobs, job)["steps"]
        assert [s["status"] for s in steps] == ["done", "error"] and "missing" in steps[1]["error"]
    finally:
        jobs.close()


def test_a_job_cancelled_while_it_waits_to_try_again_stops_at_once(tmp_path, monkeypatch):
    calls = []

    def run_step(self, job, step):
        calls.append(1)
        raise gpu.GpuBusy("ComfyUI still holds 4.6 GB")

    jobs = make_jobs(tmp_path, monkeypatch, run_step, retry_pause_seconds=30)
    try:
        job = queue_job(jobs, "one")
        wait_for(lambda: (row(jobs, job)["steps"][0].get("detail") or "").startswith("waiting for the GPU"), what="the pause")
        assert "trying again (1 of 2)" in row(jobs, job)["steps"][0]["detail"]
        jobs.cancel(job.id)
        wait_for(finished(jobs, job), seconds=3)
        assert row(jobs, job)["status"] == "cancelled" and len(calls) == 1
    finally:
        jobs.close()


# ---- NF-02 and NF-01: a step nothing can stop no longer holds the queue --------------------------------------------------------
def test_cancelling_a_step_that_ignores_the_cancel_flag_ends_the_job_and_the_next_job_runs(tmp_path, monkeypatch):
    stuck, release, ran = threading.Event(), threading.Event(), []

    def run_step(self, job, step):
        ran.append(step["key"])
        if step["key"] == "stuck":
            stuck.set()
            release.wait(20)                                    # never looks at job.cancel: a program that does not answer
            ran.append("stuck woke")

    jobs = make_jobs(tmp_path, monkeypatch, run_step)
    try:
        first = queue_job(jobs, "stuck", "after")
        assert stuck.wait(3)
        second = queue_job(jobs, "next")
        time.sleep(0.2)
        assert row(jobs, second)["status"] == "queued"          # the single worker is stuck
        jobs.cancel(first.id)
        wait_for(finished(jobs, first), seconds=5, what="the abandoned job to end")
        result = row(jobs, first)
        assert result["status"] == "cancelled" and [s["status"] for s in result["steps"]] == ["cancelled", "cancelled"]
        wait_for(finished(jobs, second), seconds=5, what="the next job to run on a new worker")
        assert row(jobs, second)["status"] == "done" and ran == ["stuck", "next"]
        release.set()                                           # the old thread finally returns ...
        wait_for(lambda: "stuck woke" in ran)
        time.sleep(0.3)
        assert row(jobs, first)["status"] == "cancelled" and ran == ["stuck", "next", "stuck woke"]    # ... and touches nothing
        assert jobs._epoch == 1
    finally:
        release.set()
        jobs.close()


def test_a_step_that_stops_when_cancelled_is_not_given_up_on(tmp_path, monkeypatch):
    started = threading.Event()

    def run_step(self, job, step):
        started.set()
        if job.cancel.wait(10):
            raise Cancelled()

    jobs = make_jobs(tmp_path, monkeypatch, run_step)
    try:
        job = queue_job(jobs, "polite")
        assert started.wait(3)
        jobs.cancel(job.id)
        wait_for(finished(jobs, job), seconds=3)
        time.sleep(0.5)                                         # longer than the grace period
        assert row(jobs, job)["status"] == "cancelled" and jobs._epoch == 0
    finally:
        jobs.close()


def test_a_silent_step_is_asked_to_stop_and_the_job_says_why(tmp_path, monkeypatch):
    def run_step(self, job, step):
        if job.cancel.wait(10):                                 # polls the flag but never shows a sign of life
            raise Cancelled()

    jobs = make_jobs(tmp_path, monkeypatch, run_step, stall_minutes=0.01)       # 0.6 s
    try:
        job = queue_job(jobs, "silent", "after")
        wait_for(finished(jobs, job), seconds=5, what="the stall limit")
        result = row(jobs, job)
        assert result["status"] == "error" and result["error"] == STALLED.format(minutes=0.01)
        assert [s["status"] for s in result["steps"]] == ["error", "cancelled"] and jobs._epoch == 0
    finally:
        jobs.close()


def test_a_silent_step_that_also_ignores_the_stop_is_given_up_on(tmp_path, monkeypatch):
    release = threading.Event()

    def run_step(self, job, step):
        release.wait(20)

    jobs = make_jobs(tmp_path, monkeypatch, run_step, stall_minutes=0.01)
    try:
        job = queue_job(jobs, "hung")
        wait_for(finished(jobs, job), seconds=6, what="the watchdog to give up")
        assert row(jobs, job)["status"] == "error" and "Nothing happened in this step" in row(jobs, job)["error"]
        assert jobs._epoch == 1
        assert jobs.thread.is_alive()                           # a new worker has the queue
    finally:
        release.set()
        jobs.close()


def test_a_step_that_keeps_showing_signs_of_life_is_never_called_stalled(tmp_path, monkeypatch):
    def run_step(self, job, step):
        end = time.monotonic() + 1.5                            # longer than the stall limit
        while time.monotonic() < end:
            job.cancel.beat()
            time.sleep(0.05)

    jobs = make_jobs(tmp_path, monkeypatch, run_step, stall_minutes=0.01)
    try:
        job = queue_job(jobs, "busy")
        wait_for(finished(jobs, job), seconds=6)
        assert row(jobs, job)["status"] == "done" and jobs._epoch == 0
    finally:
        jobs.close()


# ---- NF-04 and NF-05: the engine fallback can be switched off for a job ---------------------------------------------------------
@pytest.mark.parametrize("options,strict", [({"quality": "draft"}, False), ({"quality": "draft", "engine_fallback": True}, False),
                                            ({"quality": "draft", "engine_fallback": False}, True)])
def test_a_job_without_the_engine_fallback_runs_its_steps_in_strict_mode(tmp_path, monkeypatch, options, strict):
    seen = []

    def run_step(self, job, step):
        seen.append(getattr(engines._STRICT, "on", False))

    jobs = make_jobs(tmp_path, monkeypatch, run_step)
    try:
        job = queue_job(jobs, "one", options=options)
        wait_for(finished(jobs, job))
        assert seen == [strict]
    finally:
        jobs.close()


def test_a_restart_keeps_what_was_done_and_runs_the_unfinished_step_again(tmp_path, monkeypatch):
    """NF-10 says a restart drops the queue and the finished steps. It does not: the job file is written at every step."""
    monkeypatch.setattr(Jobs, "_worker", lambda self: None)
    jobs = Jobs({}, DummyComfy(), Library(tmp_path / "library"), state_path=tmp_path / "jobs.json")
    job = queue_job(jobs, "portrait", "sprite", "expressions")
    with jobs.lock:
        job.steps[0]["status"] = "done"
        job.steps[1]["status"] = "running"
        job.status = "running"
    job.touch()                                                  # what the worker does at each step
    jobs.close()                                                 # the process ends here
    again = Jobs({}, DummyComfy(), Library(tmp_path / "library"), state_path=tmp_path / "jobs.json")
    try:
        row_ = next(r for r in again.snapshot() if r["id"] == job.id)
        assert row_["status"] == "queued" and [s["status"] for s in row_["steps"]] == ["done", "pending", "pending"]
        assert again.queue.get_nowait().id == job.id
    finally:
        again.close()
