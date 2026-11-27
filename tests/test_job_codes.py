"""Why a step failed, for programs (API 44, npcgen/jobs.py): a `code` on each failed step and on the job, the programs holding the
GPU, a job that stops after a GPU refusal instead of letting every later step wait and fail the same way, and an assembly
that is skipped when nothing was made for it.

A companion app's run of 2026-11-10 is what this is for: a living portrait's ten expressions each waited three times for a GPU another
program held (an hour), then "Assemble the living portrait" failed with a message of its own. Nothing here needs ComfyUI: the
steps are stand-ins that fail on cue."""
import io
import threading
import time

import pytest
from PIL import Image

from npcgen import engines, gpu
from npcgen.comfy import Cancelled, ComfyError, ComfyUnreachable, LockBusy
from npcgen.jobs import NOT_RUN, Job, Jobs, failure_code
from npcgen.library import Library

LLAMA = [{"name": "llama-server.exe", "mb": 25190}]
CARD = "AMD Radeon AI PRO R9700"


class DummyComfy:
    models = {}


def make_jobs(tmp_path, monkeypatch, run_step, **jobs_config):
    monkeypatch.setattr(Jobs, "_run_step", run_step)
    cfg = {"jobs": {"retry_pause_seconds": 0, "cancel_grace_seconds": 0.3, "watch_seconds": 0.05, **jobs_config},
           "quality": {"standard": {}, "draft": {}}}
    return Jobs(cfg, DummyComfy(), Library(tmp_path / "library"), state_path=tmp_path / "jobs.json")


def queue_job(jobs, *steps, npc_id="mira-vale-123456"):
    """steps: (kind, key) pairs."""
    rows = [{"kind": kind, "key": key, "label": f"{kind}: {key}", "status": "pending", "error": None} for kind, key in steps]
    job = Job(npc_id, "Mira Vale", rows, {"quality": "draft"})
    job.on_change, job.lock = jobs._persist, jobs.lock
    with jobs.lock:
        jobs.jobs[job.id] = job
    jobs.queue.put(job)
    return job


def finish(jobs, job, seconds=10.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        row = next(r for r in jobs.snapshot() if r["id"] == job.id)
        if row["status"] in ("done", "error", "cancelled"):
            return row
        time.sleep(0.01)
    raise AssertionError("the job did not finish")


def gpu_busy():
    return gpu.GpuBusy(f"Another program is using the GPU: llama-server.exe (24.6 GB) on the {CARD}. ComfyUI (x) was not started "
                       "(one model on the GPU at a time). Close it or unload its model and try again.",
                       code="gpu_busy", holders=LLAMA, device=CARD)


# ---- the code of each failure -------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("exc, code", [
    (gpu_busy(), "gpu_busy"),
    (gpu.GpuBusy("ComfyUI still holds 4.6 GB of GPU memory ...", code="gpu_comfy_held"), "gpu_comfy_held"),
    (gpu.GpuBusy("ComfyUI is still busy with another job, so x was not started (one model on the GPU at a time).",
                 code="comfy_busy"), "comfy_busy"),
    (ComfyUnreachable("Cannot reach ComfyUI at http://127.0.0.1:8188"), "comfy_down"),
    (ComfyUnreachable("ComfyUI is busy or not answering", busy=True), "comfy_busy"),
    (LockBusy("ComfyUI is being used by another program"), "comfy_busy"),
    (engines.EngineError("Qwen could not draw it: not enough free memory", transient=True, code="low_memory"), "low_memory"),
    (engines.EngineError("ComfyUI is missing diffusion_models/x.safetensors", code="no_model"), "no_model"),
    (ComfyError("something broke"), "error"),
])
def test_every_failure_has_a_code(exc, code):
    assert failure_code(exc) == code


@pytest.mark.parametrize("text, code", [
    ("Another program is using the GPU: LM Studio.exe (19.5 GB). x was not started (one model on the GPU at a time).", "gpu_busy"),
    ("ComfyUI still holds 6.4 GB of GPU memory 90 s after it was told to unload everything, so x was not started (one model on "
     "the GPU at a time).", "gpu_comfy_held"),
    ("ComfyUI is still busy with another job, so x was not started (one model on the GPU at a time).", "comfy_busy"),
    ("Cannot reach ComfyUI at http://127.0.0.1:8188", "comfy_down"),
    ("Qwen could not draw it: ComfyUI is missing diffusion_models/x.safetensors", "no_model"),
    ("Prompt outputs failed validation: Value not in list: unet_name", "no_model"),
])
def test_an_error_without_a_code_is_read_from_its_words(text, code):
    assert failure_code(ComfyError(text)) == code


def test_an_engine_error_that_wraps_a_gpu_refusal_keeps_its_code_and_holders():
    wrapped = engines._wrapped("Qwen could not draw it: " + str(gpu_busy()), gpu_busy(), transient=True)
    assert (wrapped.code, wrapped.holders, wrapped.device, wrapped.transient) == ("gpu_busy", LLAMA, CARD, True)
    assert failure_code(wrapped) == "gpu_busy"
    plain = engines._wrapped("Qwen could not draw it: boom", ValueError("boom"))
    assert plain.code is None and plain.holders == [] and failure_code(plain) == "error"


# ---- a GPU another program holds stops the job -----------------------------------------------------------------------------------
def test_a_gpu_another_program_holds_is_not_tried_again_and_stops_the_job(tmp_path, monkeypatch):
    calls = []

    def run_step(self, job, step):
        calls.append(step["key"])
        if step["key"] == "happy":
            raise gpu_busy()

    jobs = make_jobs(tmp_path, monkeypatch, run_step)
    try:
        job = queue_job(jobs, ("portrait", None), ("expression", "happy"), ("expression", "sad"), ("living", None))
        row = finish(jobs, job)
        assert calls == [None, "happy"]                         # tried once (the gate already waited 90 s), the rest not run
        portrait, happy, sad, assemble = row["steps"]
        assert portrait["status"] == "done" and "code" not in portrait
        assert happy["status"] == "error" and happy["code"] == "gpu_busy" and "attempts" not in happy
        assert happy["holders"] == LLAMA and happy["device"] == CARD
        assert "one model on the GPU at a time" in happy["error"]          # the words a person (and an older client) reads
        for step in (sad, assemble):
            assert step["status"] == "cancelled" and step["reason"] == NOT_RUN["gpu_busy"] and "code" not in step
        assert row["status"] == "error" and row["code"] == "gpu_busy" and row["error"] == happy["error"]
    finally:
        jobs.close()


@pytest.mark.parametrize("code", ["gpu_comfy_held", "comfy_busy"])
def test_comfyuis_own_memory_or_queue_is_tried_again_then_stops_the_job(tmp_path, monkeypatch, code):
    calls = []

    def run_step(self, job, step):
        calls.append(step["key"])
        raise gpu.GpuBusy("ComfyUI still holds 4.6 GB (one model on the GPU at a time)", code=code, device=CARD)

    jobs = make_jobs(tmp_path, monkeypatch, run_step, transient_retries=2)
    try:
        job = queue_job(jobs, ("expression", "happy"), ("expression", "sad"))
        row = finish(jobs, job)
        assert calls == ["happy"] * 3                            # the first try and two more, then the job stops
        assert row["steps"][0]["code"] == code and row["steps"][0]["attempts"] == 3 and row["steps"][0]["device"] == CARD
        assert row["steps"][1]["status"] == "cancelled" and row["steps"][1]["reason"] == NOT_RUN[code]
        assert row["code"] == code
    finally:
        jobs.close()


def test_too_little_memory_fails_one_step_and_the_job_goes_on(tmp_path, monkeypatch):
    calls = []

    def run_step(self, job, step):
        calls.append(step["key"])
        if step["key"] == "happy":
            raise engines.EngineError("Qwen could not draw it: not enough free memory", transient=True, code="low_memory")

    jobs = make_jobs(tmp_path, monkeypatch, run_step, transient_retries=1)
    try:
        job = queue_job(jobs, ("expression", "happy"), ("expression", "sad"))
        row = finish(jobs, job)
        assert calls == ["happy", "happy", "sad"]
        assert row["steps"][0]["code"] == "low_memory" and row["steps"][1]["status"] == "done"
        assert row["status"] == "error" and row["code"] == "low_memory"
    finally:
        jobs.close()


def test_comfyui_that_cannot_be_reached_stops_the_job(tmp_path, monkeypatch):
    def run_step(self, job, step):
        raise ComfyUnreachable("Cannot reach ComfyUI at http://127.0.0.1:8188. Is it running?")

    jobs = make_jobs(tmp_path, monkeypatch, run_step)
    try:
        job = queue_job(jobs, ("portrait", None), ("expression", "happy"))
        row = finish(jobs, job)
        assert row["steps"][0]["code"] == "comfy_down" and row["steps"][1]["reason"] == NOT_RUN["comfy_down"]
        assert row["code"] == "comfy_down"
    finally:
        jobs.close()


def test_codes_are_saved_and_a_retry_forgets_them(tmp_path, monkeypatch):
    tries = []

    def run_step(self, job, step):
        tries.append(step["key"])
        if len(tries) == 1:
            raise gpu_busy()

    jobs = make_jobs(tmp_path, monkeypatch, run_step)
    try:
        job = queue_job(jobs, ("expression", "happy"), ("expression", "sad"))
        finish(jobs, job)
        saved = jobs.jobs[job.id].to_record()
        assert saved["code"] == "gpu_busy" and saved["steps"][0]["holders"] == LLAMA
        assert Job.from_record(saved).code == "gpu_busy"
        retried = jobs.retry(job.id)
        assert retried["code"] is None
        for step in retried["steps"]:
            assert not {"code", "holders", "device", "reason", "attempts"} & set(step)
        row = finish(jobs, job)
        assert row["status"] == "done" and row["code"] is None and tries == ["happy", "happy", "sad"]
    finally:
        jobs.close()


def test_a_cancelled_job_says_so(tmp_path, monkeypatch):
    busy, release, ran = threading.Event(), threading.Event(), []

    def run_step(self, job, step):
        ran.append(step["key"])
        busy.set()
        release.wait(10)

    jobs = make_jobs(tmp_path, monkeypatch, run_step)
    try:
        first = queue_job(jobs, ("expression", "first"))
        assert busy.wait(5)                                     # the worker is busy with the first job
        waiting = queue_job(jobs, ("expression", "happy"))
        jobs.cancel(waiting.id)                                 # cancelled while it waits in the queue
        release.set()
        finish(jobs, first)
        row = finish(jobs, waiting)
        assert row["status"] == "cancelled" and row["code"] == "cancelled" and ran == ["first"]
    finally:
        release.set()
        jobs.close()


def test_a_silent_step_says_it_stalled(tmp_path, monkeypatch):
    def run_step(self, job, step):
        if job.cancel.wait(10):                                 # polls the flag but never shows a sign of life
            raise Cancelled()

    jobs = make_jobs(tmp_path, monkeypatch, run_step, stall_minutes=0.01)        # 0.6 s
    try:
        job = queue_job(jobs, ("expression", "happy"), ("expression", "sad"))
        row = finish(jobs, job)
        assert row["steps"][0]["code"] == "stalled" and row["code"] == "stalled"
    finally:
        jobs.close()


def test_a_step_the_watchdog_gives_up_on_says_it_stalled(tmp_path, monkeypatch):
    release = threading.Event()

    def run_step(self, job, step):
        release.wait(20)                                        # does not even look at the stop flag

    jobs = make_jobs(tmp_path, monkeypatch, run_step, stall_minutes=0.01)
    try:
        job = queue_job(jobs, ("expression", "happy"))
        row = finish(jobs, job)
        assert row["status"] == "error" and row["steps"][0]["code"] == "stalled" and row["code"] == "stalled"
    finally:
        release.set()
        jobs.close()


# ---- nothing to assemble: the assembly is skipped, not a new error -------------------------------------------------------------------
def _character(lib):
    npc = lib.create({"setting": "modern", "style": "photoreal", "seed": 3,
                      "profile": {"first_name": "Ada", "last_name": "Vale", "age": 30, "gender": "female", "occupation": "tester"},
                      "appearance": {}, "assets": {"portrait": {"file": "portrait.png", "created": "p1"}}})
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), (120, 100, 90)).save(buf, "PNG")
    lib.write_asset(npc["id"], "portrait.png", buf.getvalue())
    return npc


def test_the_living_portrait_is_skipped_when_no_expression_was_made(tmp_path, monkeypatch):
    real = Jobs._run_step

    def run_step(self, job, step):
        if step["kind"] == "living":
            return real(self, job, step)                         # the real assembly, which finds no expression
        raise engines.EngineError("Qwen-Image-Edit 2511 could not draw it: ComfyUI is missing diffusion_models/edit.safetensors",
                                  code="no_model")

    jobs = make_jobs(tmp_path, monkeypatch, run_step)
    try:
        npc = _character(jobs.lib)
        job = queue_job(jobs, ("expression", "blink"), ("expression", "happy"), ("living", None), npc_id=npc["id"])
        row = finish(jobs, job)
        blink, happy, assemble = row["steps"]
        assert blink["code"] == happy["code"] == "no_model"     # a missing model does not stop the job: each step says so
        assert assemble["status"] == "cancelled" and assemble.get("error") is None
        assert assemble["reason"] == "no expression was made: " + blink["error"]
        assert row["status"] == "error" and row["code"] == "no_model" and row["error"] == blink["error"]
    finally:
        jobs.close()


def test_a_walk_is_not_drawn_from_a_reference_view_that_failed(tmp_path, monkeypatch):
    real = Jobs._run_step
    ran = []

    def run_step(self, job, step):
        if step["kind"] == "view" and step["key"] == "up":
            raise engines.EngineError("Qwen-Image-Edit 2511 could not draw it: ComfyUI ran out of memory", code="low_memory")
        if step["kind"] in ("anim", "assemble"):
            return real(self, job, step)                         # the real checks, before any frame is drawn
        ran.append(step["key"])

    jobs = make_jobs(tmp_path, monkeypatch, run_step)
    monkeypatch.setattr(jobs.animator, "make_direction", lambda job, step, npc, data, clip, d, seed: ran.append(f"{clip}:{d}"))
    monkeypatch.setattr(jobs.animator, "assemble", lambda *a: ran.append("assembled"))
    try:
        npc = _character(jobs.lib)
        job = queue_job(jobs, ("view", "right"), ("view", "up"), ("anim", "walk:down"), ("anim", "walk:right"),
                        ("anim", "walk:up"), ("assemble", "walk"), npc_id=npc["id"])
        row = finish(jobs, job)
        up_view, up_walk, assemble = row["steps"][1], row["steps"][4], row["steps"][5]
        assert ran == ["right", "walk:down", "walk:right"]       # the up walk and the assembly were not tried
        assert up_view["code"] == "low_memory"
        assert up_walk["status"] == "cancelled" and up_walk.get("error") is None
        assert up_walk["reason"] == "no up reference view was made: " + up_view["error"]
        assert assemble["status"] == "cancelled" and assemble["reason"] == "not every direction was made: " + up_view["error"]
        assert row["status"] == "error" and row["code"] == "low_memory"
    finally:
        jobs.close()


def test_the_living_portrait_still_fails_on_its_own_when_its_steps_were_fine(tmp_path, monkeypatch):
    real = Jobs._run_step

    def run_step(self, job, step):
        return real(self, job, step) if step["kind"] == "living" else None

    jobs = make_jobs(tmp_path, monkeypatch, run_step)
    try:
        npc = _character(jobs.lib)
        job = queue_job(jobs, ("expression", "blink"), ("living", None), npc_id=npc["id"])
        row = finish(jobs, job)
        assert row["steps"][1]["status"] == "error" and "None of the living-portrait expressions" in row["steps"][1]["error"]
        assert row["code"] == "error"
    finally:
        jobs.close()
