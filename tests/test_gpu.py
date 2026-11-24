"""One model on the GPU at a time (npcgen/gpu.py): what counts as a different model, the unload before it, the wait for an
empty card, and the voice runtime and ComfyUI client both going through it."""
import json
import threading
import os

import pytest

from npcgen import comfy as comfy_mod
from npcgen import fels_suite, gpu, suite_gpu
from npcgen.comfy import Comfy, edit_graph, t2i_graph

GB = 2 ** 30


class FakeCard:
    """ComfyUI's /queue, /system_stats and /free on a 32 GB card. `used` lists the VRAM readings (GB) returned in turn,
    the last one repeating; each /free is counted."""

    def __init__(self, used=(0.6,), busy=0):
        self.used, self.busy, self.frees, self.log = list(used), busy, 0, []

    def get_json(self, path, timeout=30):
        if path == "/queue":
            if self.busy > 0:
                self.busy -= 1
                return {"queue_running": [["x"]], "queue_pending": []}
            return {"queue_running": [], "queue_pending": []}
        if path == "/system_stats":
            used = self.used.pop(0) if len(self.used) > 1 else self.used[0]
            return {"system": {"ram_free": 40 * GB}, "devices": [{"vram_total": 32 * GB, "vram_free": int((32 - used) * GB)}]}
        raise AssertionError(path)

    def free_memory(self):
        self.frees += 1
        self.log.append("free")


@pytest.fixture
def gate(monkeypatch):
    g = gpu.Gate()
    g.configure({})
    clock = [0.0]
    monkeypatch.setattr(gpu, "_clock", lambda: clock[0])
    monkeypatch.setattr(gpu, "_sleep", lambda s: clock.__setitem__(0, clock[0] + s))
    monkeypatch.setattr(gpu, "_processes", lambda: [])          # never this PC's real counters
    monkeypatch.setattr(gpu, "_adapters", lambda: None)         # ...nor its real GPUs
    monkeypatch.setattr(gpu, "_comfy_pid", lambda comfy: None)
    return g


def test_the_models_a_graph_loads_decide_what_counts_as_a_different_model():
    models = comfy_mod.DEFAULT_MODELS
    portrait = gpu.graph_models(t2i_graph(models, "a knight", "", 1024, 1024, 1))
    another = gpu.graph_models(t2i_graph(models, "a witch", "blurry", 768, 768, 2))
    sprite = gpu.graph_models(edit_graph(models, ["ref.png"], "full body", 3))
    assert portrait == another                                  # same weights: no reload between pictures
    assert portrait != sprite                                   # Mage-Flow and Mage-Flow-Edit are different models
    assert models["unet_t2i"] in portrait and models["unet_edit"] in sprite
    assert "ref.png" not in sprite                              # a LoadImage input is not a model


def test_the_same_model_again_unloads_nothing(gate):
    card, released = FakeCard(), []
    gate.claim(("comfy", ("a.safetensors",)), comfy=card)
    frees = card.frees
    gate.claim(("comfy", ("a.safetensors",)), comfy=card)
    gate.claim(("comfy", ("a.safetensors",)), comfy=card)
    assert card.frees == frees and released == []


def test_a_different_model_unloads_the_last_one_and_waits_for_an_empty_card(gate):
    card, stopped = FakeCard(used=(0.6,)), []
    gate.claim(("voice", "qwen"), lambda: stopped.append("qwen"), comfy=card)
    card.used = [9.0, 9.0, 5.0, 1.2]                           # the worker's memory draining after it was stopped
    card.frees = 0
    gate.claim(("comfy", ("mage.safetensors",)), comfy=card)
    assert stopped == ["qwen"]
    assert card.frees >= 1                                      # ComfyUI was told to unload everything too
    assert card.used == [1.2]                                   # it waited until the card read empty
    assert gate.holder() == ("comfy", ("mage.safetensors",))
    assert gate.last_switch["from"] == "the qwen voice model" and gate.last_switch["used_mb"] == int(1.2 * 1024)


def test_a_card_that_never_empties_refuses_to_load_on_top(gate):
    card = FakeCard(used=(20.0,))                               # a model loaded in ComfyUI by hand, say
    with pytest.raises(gpu.GpuBusy, match="ComfyUI still holds 20.0 GB"):
        gate.claim(("voice", "qwen"), lambda: None, comfy=card)
    assert gate.holder() is None


def test_another_program_on_the_card_is_waited_for_then_named(gate, monkeypatch):
    """ComfyUI's own reading cannot see other processes on an AMD card under Windows (0.8 GB while a voice worker held
    6.8 GB), so Windows' per-process counters decide."""
    polls = []

    def processes():
        polls.append(1)
        rows = [{"pid": 333, "adapter": "a", "mb": 760}]                     # a desktop window: under the limit
        if len(polls) < 4:
            rows.append({"pid": 222, "adapter": "a", "mb": 6900})           # a voice worker still exiting
        return rows

    monkeypatch.setattr(gpu, "_comfy_pid", lambda comfy: 111)
    monkeypatch.setattr(gpu, "_processes", processes)
    monkeypatch.setattr(gpu, "_process_name", lambda pid: {222: "python.exe", 333: "Comfy Desktop.exe"}[pid])
    gate.claim(("comfy", ("a.safetensors",)), comfy=FakeCard())
    assert len(polls) == 4 and gate.holder() == ("comfy", ("a.safetensors",))

    # Windows can reuse PID 444 for this pytest process; the simulated foreign
    # owner must remain distinct from the process being tested.
    foreign_pid = os.getpid() + 100000
    monkeypatch.setattr(gpu, "_processes", lambda: [{"pid": foreign_pid, "adapter": "a", "mb": 20000}])
    monkeypatch.setattr(gpu, "_process_name", lambda pid: "LM Studio.exe")
    with pytest.raises(gpu.GpuBusy, match=r"Another program is using the GPU: LM Studio.exe \(19.5 GB\)"):
        gate.claim(("voice", "qwen"), lambda: None, comfy=FakeCard())


def test_comfyuis_own_process_must_really_give_its_memory_back(gate, monkeypatch):
    """Measured: after /free ComfyUI reads 0.76 GB within a second, but its process keeps 4.8 GB for another 12-22 s."""
    polls = []

    def processes():
        polls.append(1)
        return [{"pid": 111, "adapter": "a", "mb": 4900 if len(polls) < 25 else 0}]

    monkeypatch.setattr(gpu, "_comfy_pid", lambda comfy: 111)
    monkeypatch.setattr(gpu, "_processes", processes)
    card = FakeCard(used=(0.76,))
    gate.claim(("comfy", ("a.safetensors",)), comfy=card)
    assert len(polls) == 25 and gate.holder() == ("comfy", ("a.safetensors",))
    assert card.frees == 2                                      # asked once more after 20 s of waiting
    monkeypatch.setattr(gpu, "_processes", lambda: [{"pid": 111, "adapter": "a", "mb": 4900}])
    with pytest.raises(gpu.GpuBusy, match="ComfyUI still holds 4.8 GB"):
        gate.claim(("voice", "qwen"), lambda: None, comfy=card)


def test_comfyui_running_without_a_known_process_is_never_counted_against_itself(gate, monkeypatch):
    monkeypatch.setattr(gpu, "_processes", lambda: [{"pid": 111, "adapter": "a", "mb": 4900}])
    monkeypatch.setattr(gpu, "_process_name", lambda pid: "python.exe")
    gate.claim(("comfy", ("a.safetensors",)), comfy=FakeCard())               # pid unknown, ComfyUI answering: no counters
    assert gate.holder() == ("comfy", ("a.safetensors",))


def test_a_comfyui_busy_with_someone_elses_job_is_waited_for_then_refused(gate):
    card = FakeCard(busy=10 ** 6)
    with pytest.raises(gpu.GpuBusy, match="still busy"):
        gate.claim(("comfy", ("a.safetensors",)), comfy=card)
    card = FakeCard(busy=3)                                     # a short job finishes: the claim goes ahead
    gate.claim(("comfy", ("a.safetensors",)), comfy=card)
    assert gate.holder() == ("comfy", ("a.safetensors",))


def test_without_comfyui_the_gate_still_stops_the_last_holder(gate):
    stopped = []
    gate.claim(("voice", "qwen"), lambda: stopped.append("qwen"))
    gate.claim(("voice", "kokoro"), lambda: stopped.append("kokoro"))
    assert stopped == ["qwen"] and gate.holder() == ("voice", "kokoro")


def test_switched_off_the_gate_does_nothing(gate):
    gate.configure({"gpu": {"one_model_at_a_time": False}})
    card, stopped = FakeCard(used=(25.0,)), []
    gate.claim(("voice", "qwen"), lambda: stopped.append("qwen"), comfy=card)
    gate.claim(("comfy", ("a.safetensors",)), comfy=card)
    assert stopped == [] and card.frees == 0 and gate.holder() is None


def test_a_holder_that_unloads_itself_is_forgotten_and_comfyui_is_freed_between_jobs(gate):
    card = FakeCard()
    gate.claim(("voice", "qwen"), lambda: None, comfy=card)
    gate.drop(("voice", "kokoro"))                              # someone else's key: ignored
    assert gate.holder() == ("voice", "qwen")
    gate.drop(("voice", "qwen"))
    assert gate.holder() is None
    gate.release_comfy(card)                                    # the queue ran empty but ComfyUI holds nothing: no call
    frees = card.frees
    gate.release_comfy(card)
    assert card.frees == frees
    gate.claim(("comfy", ("a.safetensors",)), comfy=card)
    gate.release_comfy(card)
    assert card.frees == frees + 2 and gate.holder() is None    # one free for the claim, one when the queue ran empty


def test_every_comfyui_graph_claims_the_gpu_on_submit(monkeypatch):
    claims = []
    monkeypatch.setattr(gpu.GATE, "claim_picture", lambda comfy, graph, cancel: claims.append((("comfy", gpu.graph_models(graph)), comfy)))
    client = Comfy("http://127.0.0.1:9")
    monkeypatch.setattr(client, "_request", lambda *a, **k: json.dumps({"prompt_id": "p1"}).encode())
    graph = t2i_graph(client.models, "x", "", 64, 64, 1)
    assert client.submit(graph) == "p1"
    assert claims == [(("comfy", gpu.graph_models(graph)), client)]


@pytest.mark.parametrize("exclusive", [False, True])
def test_picture_residency_shares_or_waits_for_voice_lab(tmp_path, monkeypatch, gate, exclusive):
    monkeypatch.setenv("FELS_SUITE_DIR", str(tmp_path))
    gate.configure({"gpu": {"one_model_at_a_time": exclusive, "wait_seconds": 4}})
    comfy = Comfy("http://127.0.0.1:9")
    card = FakeCard()
    monkeypatch.setattr(comfy, "get_json", card.get_json)
    monkeypatch.setattr(comfy, "free_memory", card.free_memory)
    lab = fels_suite.GpuLock("voice-lab")
    assert lab.acquire("Kokoro 82M", timeout=0)
    contacts = []
    outside = []
    messages = []
    cancel = threading.Event()
    cancel.beat = messages.append

    def unload(holder):
        assert fels_suite.gpu_status()["alone"]["app"] == "npc-forge"
        assert holder["app"] == "voice-lab"
        contacts.append(holder)
        if len(contacts) == 1:
            return 409, {"code": "busy", "doing": "speaking a scene"}
        lab.release()
        return 200, {}

    monkeypatch.setattr(suite_gpu, "unload_holder", unload)
    monkeypatch.setattr(fels_suite, "unload_outside", lambda **kwargs: outside.append(kwargs))
    try:
        with comfy.operation_lock:
            gate.claim_picture(comfy, {}, cancel)
            holders = {row["app"]: row["alone"] for row in fels_suite.gpu_holders()}
            if exclusive:
                assert holders == {"npc-forge": True}
                assert len(contacts) == 2 and len(outside) == 1
                assert any("Voice Lab is speaking a scene" in (message or "") for message in messages)
            else:
                assert holders == {"npc-forge": False, "voice-lab": False}
                assert not contacts and not outside
        gate.release_comfy(comfy)
        assert not any(row["app"] == "npc-forge" for row in fels_suite.gpu_holders())
        assert not fels_suite.gpu_status()["alone"]
        assert card.frees
    finally:
        gate.reset()
        lab.release()


def test_cancel_during_busy_unload_clears_forge_intent(tmp_path, monkeypatch):
    monkeypatch.setenv("FELS_SUITE_DIR", str(tmp_path))
    cancel = threading.Event()
    lab = fels_suite.GpuLock("voice-lab")
    forge = fels_suite.GpuLock("npc-forge")
    assert lab.acquire("Kokoro 82M", timeout=0)

    def busy(holder):
        cancel.set()
        return 409, {"code": "busy", "doing": "speaking"}

    monkeypatch.setattr(suite_gpu, "unload_holder", busy)
    try:
        with pytest.raises(comfy_mod.Cancelled):
            suite_gpu.acquire_pictures(forge, True, 5, cancel)
        assert not forge.held and fels_suite.gpu_status()["alone"] is None
        assert lab.held
    finally:
        lab.release()


def test_unload_only_contacts_the_holder_with_a_matching_address(tmp_path, monkeypatch):
    monkeypatch.setenv("FELS_SUITE_DIR", str(tmp_path))
    holder = {"app": "voice-lab", "pid": 43210}
    monkeypatch.setattr(fels_suite, "read_address", lambda app: {"url": "http://127.0.0.1:9", "pid": 43211})
    assert suite_gpu.unload_holder(holder) == (None, {})
