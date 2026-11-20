import json
import multiprocessing
import threading
import time
import uuid

import pytest

from npcgen import comfy as comfy_mod
from npcgen.comfy import Cancelled, Comfy, ComfyError, LockBusy, _operation_lock


def _lock_from_process(endpoint, entered, release, active, overlapped):
    with _operation_lock(endpoint):
        with active.get_lock():
            if active.value:
                overlapped.value = 1
            active.value += 1
        entered.set()
        release.wait(5)
        with active.get_lock():
            active.value -= 1


def test_complete_comfy_runs_are_serialized_across_clients():
    """Two app queues must never overlap graph runs on the one 32 GB GPU."""
    first_client = Comfy("http://127.0.0.1:9")
    second_client = Comfy("http://127.0.0.1:9/")
    first_entered = threading.Event()
    release_first = threading.Event()
    submitted = []

    def submit(graph):
        submitted.append(graph["id"])
        if graph["id"] == "first":
            first_entered.set()
            assert release_first.wait(2)
        return graph["id"]

    for client in (first_client, second_client):
        client.submit = submit
        client.wait = lambda prompt_id, cancel=None, timeout=900: {
            "outputs": {"1": {"images": [{"filename": prompt_id + ".png"}]}}}
        client.fetch_image = lambda filename, subfolder="", image_type="temp": filename.encode()

    results = []
    one = threading.Thread(target=lambda: results.append(first_client.run({"id": "first"})))
    two = threading.Thread(target=lambda: results.append(second_client.run({"id": "second"})))
    one.start()
    assert first_entered.wait(2)
    two.start()
    time.sleep(0.05)
    assert submitted == ["first"]
    release_first.set()
    one.join(2)
    two.join(2)
    assert submitted == ["first", "second"]
    assert sorted(results) == [b"first.png", b"second.png"]


def test_comfy_graph_runs_are_serialized_across_processes():
    """The OS lock protects ComfyUI's global queue and interrupt API across server processes."""
    ctx = multiprocessing.get_context("spawn")
    endpoint = f"http://npcforge-lock-{uuid.uuid4().hex}.invalid"
    first_entered, second_entered = ctx.Event(), ctx.Event()
    release = ctx.Event()
    active, overlapped = ctx.Value("i", 0), ctx.Value("i", 0)
    first = ctx.Process(target=_lock_from_process, args=(endpoint, first_entered, release, active, overlapped))
    second = ctx.Process(target=_lock_from_process, args=(endpoint, second_entered, release, active, overlapped))
    try:
        first.start()
        assert first_entered.wait(5)
        second.start()
        assert not second_entered.wait(0.2), "the second process entered while the first held the endpoint lock"
        release.set()
        assert second_entered.wait(5)
        first.join(5)
        second.join(5)
        assert first.exitcode == 0 and second.exitcode == 0
        assert overlapped.value == 0
    finally:
        release.set()
        if first.is_alive():
            first.terminate()
            first.join(2)
        if second.is_alive():
            second.terminate()
            second.join(2)


def test_cancelled_comfy_lock_waiter_does_not_enter_or_interrupt_owner():
    lock = Comfy("http://127.0.0.1:8188").operation_lock
    entered_owner, release_owner, entered_waiter = threading.Event(), threading.Event(), threading.Event()
    cancelled = threading.Event()
    errors = []

    def owner():
        with lock:
            entered_owner.set()
            release_owner.wait(2)

    def waiter():
        try:
            with lock.hold(cancelled):
                entered_waiter.set()
        except Cancelled:
            errors.append("cancelled")

    first = threading.Thread(target=owner)
    second = threading.Thread(target=waiter)
    first.start()
    assert entered_owner.wait(2)
    second.start()
    cancelled.set()
    second.join(2)
    release_owner.set()
    first.join(2)
    assert not entered_waiter.is_set()
    assert errors == ["cancelled"]


def _owner_of(lock, name="npc-worker"):
    """A thread that holds `lock` until released; returns (release event, thread)."""
    held, release = threading.Event(), threading.Event()

    def own():
        with lock:
            held.set()
            release.wait(10)

    thread = threading.Thread(target=own, name=name)
    thread.start()
    assert held.wait(2)
    return release, thread


def test_waiting_for_comfyui_ends_with_a_message_that_names_who_holds_it():
    """NF-01: a step waited for ever for the lock, 'running' while ComfyUI was idle, and nothing said who held it."""
    assert issubclass(LockBusy, ComfyError) and issubclass(LockBusy, RuntimeError)
    lock = _operation_lock(f"http://npcforge-wait-{uuid.uuid4().hex}.invalid")
    release, owner = _owner_of(lock)
    try:
        started = time.monotonic()
        with pytest.raises(LockBusy, match=r"another job, another NPC Forge or another program is still using it\. "
                                            r"It is held by process \d+ \(npc-worker in .+\) since \d\d:\d\d:\d\d\."):
            with lock.hold(timeout=0.3):
                pass
        assert 0.25 < time.monotonic() - started < 3
    finally:
        release.set()
        owner.join(3)
    assert not lock.holder_path.exists()                        # the note goes when the lock is let go
    with lock.hold(timeout=0.3):                                # and the lock is usable again
        assert lock.holder_path.exists()


def test_a_hold_with_no_limit_given_ends_after_the_default_wait(monkeypatch):
    assert comfy_mod.LOCK_WAIT_SECONDS == 30 * 60               # half an hour: something has gone wrong after that
    monkeypatch.setattr(comfy_mod, "LOCK_WAIT_SECONDS", 0.3)
    lock = _operation_lock(f"http://npcforge-default-{uuid.uuid4().hex}.invalid")
    release, owner = _owner_of(lock)
    try:
        started = time.monotonic()
        with pytest.raises(LockBusy):
            with lock.hold(threading.Event()):                  # a cancel flag that is never set changes nothing
                pass
        assert 0.25 < time.monotonic() - started < 3
    finally:
        release.set()
        owner.join(3)


def test_a_wait_for_the_lock_tells_the_job_it_is_alive_and_what_for():
    notes = []

    class Flag(threading.Event):
        def beat(self, note=None):
            notes.append(note)

    lock = _operation_lock(f"http://npcforge-beat-{uuid.uuid4().hex}.invalid")
    release, owner = _owner_of(lock)
    flag, result = Flag(), []

    def wait_then_hold():
        with lock.hold(flag, timeout=5):
            result.append("held")

    waiter = threading.Thread(target=wait_then_hold)
    waiter.start()
    time.sleep(0.4)
    release.set()
    owner.join(3)
    waiter.join(3)
    assert result == ["held"]
    assert any(n and n.startswith("waiting for ComfyUI") for n in notes) and notes[-1] is None      # the words come down on entry


def test_comfy_wait_beats_on_every_poll_and_a_run_hands_its_cancel_flag_to_the_gpu_claim(monkeypatch):
    from npcgen import gpu
    beats, claims, polls = [], [], []

    class Flag(threading.Event):
        def beat(self, note=None):
            beats.append(note)

    client = Comfy("http://127.0.0.1:9")

    def get_json(path, timeout=30):
        polls.append(path)
        if len(polls) < 3:
            return {}
        return {"p1": {"status": {"completed": True, "status_str": "success"}, "outputs": {"1": {"images": [{"filename": "a.png"}]}}}}

    client.get_json = get_json
    client.fetch_image = lambda filename, subfolder="", image_type="temp": b"png"
    client._request = lambda method, path, data=None, headers=None, timeout=30: json.dumps({"prompt_id": "p1"}).encode()
    monkeypatch.setattr(comfy_mod.time, "sleep", lambda s: None)
    monkeypatch.setattr(gpu, "claim", lambda key, release=None, comfy=None, cancel=None: claims.append(cancel))
    flag = Flag()
    assert client.run({"1": {"class_type": "KSampler", "inputs": {}}}, cancel=flag) == b"png"
    assert len(beats) >= 3 and claims == [flag]                 # a cancel during the GPU hand-off now reaches it
    assert getattr(client._claiming, "cancel", None) is None    # and is not left behind for the next run


def test_default_upload_folders_are_unique_per_comfy_client():
    clients = [Comfy("http://127.0.0.1:8188"), Comfy("http://127.0.0.1:8188")]
    requests = []
    for client in clients:
        def request(method, path, data=None, headers=None, timeout=30, client=client):
            requests.append((client, data))
            return json.dumps({"name": "ref.png", "subfolder": f"npc_generator/{client.client_id}"}).encode()
        client._request = request
        client.upload_image(b"png", "ref.png")
    folders = [f"npc_generator/{client.client_id}" for client in clients]
    assert folders[0] != folders[1]
    assert all(folder.encode() in data for (_, data), folder in zip(requests, folders))


# ---- a ComfyUI that stops answering while it works ---------------------------------------------------------------------------------------------
class SilentClock:
    """A clock that only moves when the code sleeps or a request 'times out', so that minutes of silence cost nothing."""
    def __init__(self):
        self.now = 1000.0

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


DONE = {"p1": {"status": {"completed": True, "status_str": "success"}, "outputs": {}}}


def silent_client(monkeypatch, answers):
    """A client whose polls of /history/p1 come from `answers` in turn: a dict is the answer, an exception is raised after the 15 seconds a poll waits; returns (client, clock, beats)."""
    clock, beats, polls = SilentClock(), [], []
    monkeypatch.setattr(comfy_mod.time, "time", clock.time)
    monkeypatch.setattr(comfy_mod.time, "sleep", clock.sleep)

    class Flag(threading.Event):
        def beat(self, note=None):
            beats.append(note)

    client = Comfy("http://127.0.0.1:9")

    def get_json(path, timeout=30):
        answer = answers[min(len(polls), len(answers) - 1)]
        polls.append(path)
        if isinstance(answer, Exception):
            clock.now += timeout
            raise answer
        return answer

    client.get_json = get_json
    client.polls = polls
    client.flag = Flag()
    return client, clock, beats


def test_a_poll_that_gets_no_answer_while_comfyui_is_busy_is_tried_again_until_the_graph_finishes(monkeypatch):
    busy = comfy_mod.ComfyUnreachable("Cannot reach ComfyUI at http://127.0.0.1:8188. Is ComfyUI running? (timed out)", busy=True)
    client, clock, beats = silent_client(monkeypatch, [busy] * 20 + [{}, DONE])                    # (five minutes of silence, then a poll that says 'not yet', then done)
    entry = client.wait("p1", cancel=client.flag, timeout=10_000)
    assert entry is DONE["p1"] and len(client.polls) == 22
    assert "ComfyUI is busy and not answering: still waiting for its graph" in beats                # (the job is told what it waits for)
    assert clock.now - 1000.0 > 300


def test_a_silence_that_is_over_resets_its_clock(monkeypatch):
    busy = comfy_mod.ComfyUnreachable("timed out", busy=True)
    minutes = lambda n: [busy] * int(n * 60 / 16)                                                  # (a poll takes 15 s and the wait sleeps 1 s)
    client, _clock, _beats = silent_client(monkeypatch, minutes(14) + [{}] + minutes(14) + [DONE])
    assert client.wait("p1", timeout=10_000) is DONE["p1"]                                         # (two silences of 14 minutes are not one of 28)


def test_a_busy_comfyui_that_stays_silent_too_long_ends_the_wait_with_the_error(monkeypatch):
    busy = comfy_mod.ComfyUnreachable("Cannot reach ComfyUI at http://127.0.0.1:8188. Is ComfyUI running? (timed out)", busy=True)
    client, clock, _beats = silent_client(monkeypatch, [busy])
    with pytest.raises(comfy_mod.ComfyUnreachable, match="timed out"):
        client.wait("p1", timeout=100_000)
    assert comfy_mod.BUSY_SILENCE_SECONDS < clock.now - 1000.0 < comfy_mod.BUSY_SILENCE_SECONDS + 60


def test_a_comfyui_that_refuses_the_connection_is_given_only_a_short_time(monkeypatch):
    closed = comfy_mod.ComfyUnreachable("Cannot reach ComfyUI at http://127.0.0.1:8188. Is ComfyUI running? ([WinError 10061])", busy=False)
    client, clock, beats = silent_client(monkeypatch, [closed] * 2 + [DONE])                       # (a blip: the third poll is answered)
    assert client.wait("p1", cancel=client.flag, timeout=10_000) is DONE["p1"]
    assert "ComfyUI is not answering: waiting for it" in beats
    client, clock, _beats = silent_client(monkeypatch, [closed])
    with pytest.raises(comfy_mod.ComfyUnreachable, match="Cannot reach"):
        client.wait("p1", timeout=10_000)
    assert comfy_mod.DOWN_SILENCE_SECONDS < clock.now - 1000.0 < comfy_mod.DOWN_SILENCE_SECONDS + 60


def test_other_errors_of_a_poll_still_end_the_wait_at_once(monkeypatch):
    client, _clock, _beats = silent_client(monkeypatch, [comfy_mod.ComfyError("GET /history/p1 -> HTTP 500: boom")])
    with pytest.raises(comfy_mod.ComfyError, match="HTTP 500") as caught:
        client.wait("p1", timeout=10_000)
    assert not isinstance(caught.value, comfy_mod.ComfyUnreachable) and len(client.polls) == 1


def test_cancel_works_while_comfyui_is_silent(monkeypatch):
    busy = comfy_mod.ComfyUnreachable("timed out", busy=True)
    client, _clock, _beats = silent_client(monkeypatch, [busy])
    client._is_running = lambda prompt_id: False                                                   # (nothing can be asked of it: the prompt is taken off the queue if it can be)
    client._dequeue = lambda prompt_id: client.polls.append("dequeued")
    seen = []

    def get_json(path, timeout=30):
        seen.append(path)
        if len(seen) == 3:
            client.flag.set()
        raise busy

    client.get_json = get_json
    with pytest.raises(comfy_mod.Cancelled):
        client.wait("p1", cancel=client.flag, timeout=10_000)
    assert len(seen) == 3 and client.polls == ["dequeued"]


def test_a_request_that_gets_no_answer_says_whether_comfyui_is_busy_or_closed(monkeypatch):
    import urllib.error
    client = Comfy("http://127.0.0.1:9")

    def failing(error):
        def urlopen(request, timeout=None):
            raise error
        return urlopen

    cases = [(urllib.error.URLError(TimeoutError("timed out")), True, "Cannot reach ComfyUI"), (urllib.error.URLError(ConnectionRefusedError(10061, "refused")), False, "Cannot reach ComfyUI"),
             (TimeoutError("timed out"), True, "Timed out talking to ComfyUI")]
    for error, busy, words in cases:
        monkeypatch.setattr(comfy_mod.urllib.request, "urlopen", failing(error))
        with pytest.raises(comfy_mod.ComfyUnreachable, match=words) as caught:
            client._request("GET", "/history/p1")
        assert caught.value.busy is busy and isinstance(caught.value, comfy_mod.ComfyError)
