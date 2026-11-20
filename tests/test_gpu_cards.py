"""The GPU gate on a PC with two GPUs, ComfyUI's dynamic VRAM cache, and GET /api/gpu's reading (npcgen/gpu.py, gpumem.py; API 44).

What this is for, measured 2026-11-10/11 on this PC (a Radeon 780M built into the processor and an R9700): ComfyUI ran on the
780M by mistake, a 24.6 GB LM Studio model on the other card refused pictures it was not in the way of, and ComfyUI 0.42 kept
4.6 GB of its last model after every unload (its dynamic VRAM cache), which refused every switch to another model. Nothing here
reads this PC's real counters: the cards, the processes and ComfyUI are stand-ins."""
from types import SimpleNamespace

import pytest

from npcgen import gpu, gpumem

GB = 2 ** 30
IGPU = {"luid": "0x00000000_0x00011a88", "name": "AMD Radeon 780M Graphics", "dedicated_mb": 1900, "integrated": True}
CARD = {"luid": "0x00000000_0x0000f97f", "name": "AMD Radeon AI PRO R9700", "dedicated_mb": 32624, "integrated": False}
COMFY, LLAMA, WORKER = 111, 222, 555
NAMES = {LLAMA: "llama-server.exe", WORKER: "python.exe", 333: "Discord.exe"}


class Comfy:
    """ComfyUI's /system_stats and /queue: the card it names, its own reading (GB in use of 32), its packages and start options."""
    base = "http://127.0.0.1:8188"

    def __init__(self, used=0.5, card=CARD, aimdo=False, argv=(), busy=False, up=True):
        self.used, self.card, self.aimdo, self.argv, self.busy, self.up, self.frees = used, card, aimdo, list(argv), busy, up, 0

    def get_json(self, path, timeout=30):
        if not self.up:
            raise OSError("connection refused")
        if path == "/queue":
            return {"queue_running": [["x"]] if self.busy else [], "queue_pending": []}
        if path == "/system_stats":
            packages = [{"name": "comfy-aimdo", "installed": "0.5.6"}] if self.aimdo else []
            return {"system": {"argv": ["ComfyUI\\main.py", *self.argv], "comfy_package_versions": packages},
                    "devices": [{"name": f"cuda:0 {self.card['name']} : native", "vram_total": 32 * GB,
                                 "vram_free": int((32 - self.used) * GB)}]}
        raise AssertionError(path)

    def free_memory(self):
        self.frees += 1


@pytest.fixture
def pc(monkeypatch):
    """A fresh gate on a fake PC: `pc.rows` are the processes' GPU memory, `pc.cards` its GPUs, `pc.asked` the `shared`
    flag of every reading, `pc.clock` the time the gate has waited."""
    state = SimpleNamespace(rows=[], cards=[IGPU, CARD], asked=[], clock=[0.0], own=set())
    gate = gpu.Gate()
    gate.configure({})
    state.gate = gate

    def processes(shared=False):
        state.asked.append(shared)
        return [dict(r) for r in state.rows]
    monkeypatch.setattr(gpu, "_clock", lambda: state.clock[0])
    monkeypatch.setattr(gpu, "_sleep", lambda s: state.clock.__setitem__(0, state.clock[0] + s))
    monkeypatch.setattr(gpu, "_processes", processes)
    monkeypatch.setattr(gpu, "_adapters", lambda: [dict(c) for c in state.cards] if state.cards is not None else None)
    monkeypatch.setattr(gpu, "_comfy_pid", lambda comfy: COMFY)
    monkeypatch.setattr(gpu, "_process_name", lambda pid: NAMES[pid])
    monkeypatch.setattr(gpu, "_own_processes", lambda: set(state.own))
    return state


def row(pid, card, mb):
    return {"pid": pid, "adapter": card["luid"], "mb": mb}


PICTURE = ("comfy", ("qwen_image_edit_2511.safetensors",))


# ---- only the card ComfyUI draws on counts ----------------------------------------------------------------------------------------------
def test_a_program_on_the_other_card_is_not_in_the_way(pc):
    pc.rows = [row(COMFY, CARD, 300), row(LLAMA, IGPU, 25190)]
    pc.gate.claim(PICTURE, comfy=Comfy())
    assert pc.gate.holder() == PICTURE and pc.clock[0] < 2                  # no wait at all


def test_a_program_on_comfyuis_card_is_named_with_the_card(pc):
    pc.rows = [row(COMFY, CARD, 300), row(LLAMA, CARD, 25190), row(333, CARD, 700)]          # Discord under the limit
    with pytest.raises(gpu.GpuBusy) as refused:
        pc.gate.claim(PICTURE, comfy=Comfy())
    e = refused.value
    assert (e.code, e.holders, e.device) == ("gpu_busy", [{"name": "llama-server.exe", "mb": 25190}], CARD["name"])
    assert "llama-server.exe (24.6 GB) on the AMD Radeon AI PRO R9700" in str(e) and "one model on the GPU at a time" in str(e)
    assert "built into the processor" not in str(e)


def test_the_card_is_found_from_comfyuis_own_memory_when_its_name_is_unknown(pc):
    renamed = {**CARD, "name": "Radeon (driver name)"}
    pc.rows = [row(COMFY, CARD, 900), row(COMFY, IGPU, 20), row(LLAMA, IGPU, 25190)]
    pc.gate.claim(PICTURE, comfy=Comfy(card=renamed))                     # ComfyUI's name matches no card: its memory says which
    assert pc.gate.holder() == PICTURE
    assert pc.gate.check(Comfy(card=renamed))["device"] == CARD["name"]


def test_without_the_list_of_cards_comfyuis_own_memory_still_tells_its_card(pc):
    pc.cards = None
    pc.rows = [row(COMFY, CARD, 300), row(LLAMA, IGPU, 25190)]
    pc.gate.claim(PICTURE, comfy=Comfy())                              # the counters' card ids are enough
    assert pc.gate.holder() == PICTURE


def test_when_nothing_tells_the_card_every_program_counts_as_before(pc):
    pc.cards = None
    pc.rows = [row(LLAMA, IGPU, 25190)]                                # no list of cards, and ComfyUI holds nothing yet
    with pytest.raises(gpu.GpuBusy) as refused:
        pc.gate.claim(PICTURE, comfy=Comfy())
    assert refused.value.code == "gpu_busy" and refused.value.device == CARD["name"]     # the name ComfyUI reports


def test_comfyui_on_the_built_in_gpu_counts_its_shared_memory_and_names_the_other_card(pc):
    pc.rows = [row(COMFY, IGPU, 300), row(LLAMA, IGPU, 25190)]
    with pytest.raises(gpu.GpuBusy) as refused:
        pc.gate.claim(PICTURE, comfy=Comfy(card=IGPU))
    text = str(refused.value)
    assert pc.asked and all(pc.asked)                                   # shared memory read: it is the iGPU's real memory
    assert "built into the processor" in text and "AMD Radeon AI PRO R9700" in text and "--cuda-device" in text
    pc.rows = [row(COMFY, IGPU, 300)]
    report = pc.gate.check(Comfy(card=IGPU))
    assert report["ready"] and report["integrated"] is True and "--cuda-device" in report["warning"]
    assert report["message"].endswith(report["warning"])


# ---- ComfyUI's dynamic VRAM cache ------------------------------------------------------------------------------------------------------
def test_comfyuis_dynamic_vram_cache_does_not_stop_its_own_next_model(pc):
    """Measured on the R9700: ComfyUI's own count 0.5 GB, its process 4.6 GB, for as long as it was watched."""
    pc.rows = [row(COMFY, CARD, 4703)]
    comfy = Comfy(used=0.5, aimdo=True)
    pc.gate.claim(PICTURE, comfy=comfy)
    assert pc.gate.holder() == PICTURE and pc.clock[0] < 2 and comfy.frees == 1
    assert pc.gate.last_switch["used_mb"] == 512                      # judged by ComfyUI's own count


def test_a_voice_model_is_still_refused_beside_that_cache(pc):
    pc.rows = [row(COMFY, CARD, 4703)]
    with pytest.raises(gpu.GpuBusy) as refused:
        pc.gate.claim(("voice", "qwen"), lambda: None, comfy=Comfy(used=0.5, aimdo=True))
    e = refused.value
    assert (e.code, e.comfy_mb, e.device) == ("gpu_comfy_held", 4703, CARD["name"])
    assert "dynamic VRAM cache" in str(e) and "--disable-dynamic-vram" in str(e) and "ComfyUI still holds 4.6 GB" in str(e)


@pytest.mark.parametrize("comfy", [Comfy(used=0.5, aimdo=True, argv=["--disable-dynamic-vram"]), Comfy(used=0.5)],
                         ids=["dynamic-vram-off", "no-aimdo"])
def test_a_comfyui_without_that_cache_must_still_give_its_memory_back(pc, comfy):
    pc.rows = [row(COMFY, CARD, 4703)]
    with pytest.raises(gpu.GpuBusy) as refused:
        pc.gate.claim(PICTURE, comfy=comfy)
    assert refused.value.code == "gpu_comfy_held" and pc.clock[0] > 90
    assert "dynamic VRAM" not in str(refused.value) and "Restart ComfyUI" in str(refused.value)


def test_a_cache_bigger_than_comfyuis_own_count_allows_is_not_waved_through(pc):
    pc.rows = [row(COMFY, CARD, 20000)]
    with pytest.raises(gpu.GpuBusy):                                  # ComfyUI itself counts 19.5 GB: a model it really holds
        pc.gate.claim(PICTURE, comfy=Comfy(used=19.5, aimdo=True))


# ---- GET /api/gpu: the same reading, without unloading anything -----------------------------------------------------------------------
def test_check_reads_the_card_and_unloads_nothing(pc):
    pc.rows = [row(COMFY, CARD, 300), row(LLAMA, IGPU, 25190)]
    comfy = Comfy(used=0.5)
    report = pc.gate.check(comfy)
    assert report == {"ready": True, "code": None, "message": "Ready: ComfyUI draws on the AMD Radeon AI PRO R9700 (31.5 GB free).",
                      "device": CARD["name"], "integrated": False, "total_mb": 32768, "free_mb": 32256, "comfy_mb": 300,
                      "forge_loaded": None, "holders": [], "other_gpus": [IGPU["name"]], "warning": None,
                      "one_model_at_a_time": True}
    assert comfy.frees == 0 and pc.clock[0] == 0


def test_check_names_a_program_in_the_way(pc):
    pc.rows = [row(COMFY, CARD, 300), row(LLAMA, CARD, 25190)]
    report = pc.gate.check(Comfy())
    assert not report["ready"] and report["code"] == "gpu_busy" and report["holders"] == [{"name": "llama-server.exe", "mb": 25190}]
    assert report["message"].startswith("llama-server.exe holds 24.6 GB of the AMD Radeon AI PRO R9700")
    assert pc.gate.check(Comfy(), forge_busy=True)["ready"]           # NPC Forge's own job: a new one only waits its turn
    pc.gate.configure({"gpu": {"one_model_at_a_time": False}})
    assert pc.gate.check(Comfy())["ready"]                            # the rule switched off: nothing is refused for it


def test_check_says_when_comfyui_is_down_or_busy(pc):
    down = pc.gate.check(Comfy(up=False))
    assert not down["ready"] and down["code"] == "comfy_down" and "http://127.0.0.1:8188" in down["message"]
    pc.rows = [row(COMFY, CARD, 300)]
    busy = pc.gate.check(Comfy(busy=True))
    assert not busy["ready"] and busy["code"] == "comfy_busy"


def test_check_tells_comfyuis_own_models_from_a_model_loaded_by_hand(pc):
    pc.rows = [row(COMFY, CARD, 20480)]
    by_hand = pc.gate.check(Comfy(used=20.0))
    assert not by_hand["ready"] and by_hand["code"] == "gpu_comfy_held" and "nothing loaded there" in by_hand["message"]
    pc.gate.claim(PICTURE)                                             # NPC Forge's own models are what ComfyUI holds
    mine = pc.gate.check(Comfy(used=20.0))
    assert mine["ready"] and mine["forge_loaded"].startswith("ComfyUI (qwen_image_edit_2511")


def test_check_reports_the_dynamic_vram_cache_as_ready(pc):
    pc.rows = [row(COMFY, CARD, 4703)]
    report = pc.gate.check(Comfy(used=0.5, aimdo=True))
    assert report["ready"] and report["comfy_mb"] == 4703 and "dynamic VRAM cache" in report["message"]


def test_check_leaves_out_the_voice_worker_npc_forge_started(pc):
    pc.gate.claim(("voice", "qwen"), lambda: None)                     # NPC Forge's own voice model: the next claim stops it
    pc.rows = [row(COMFY, CARD, 300), row(WORKER, CARD, 6800)]
    pc.own = {WORKER}
    assert pc.gate.check(Comfy())["ready"]
    pc.gate.reset()                                                     # not NPC Forge's (any more): then it is in the way
    report = pc.gate.check(Comfy())
    assert report["code"] == "gpu_busy" and report["holders"][0]["name"] == "python.exe"


# ---- the pieces ---------------------------------------------------------------------------------------------------------------------
def test_comfyuis_device_name_and_its_dynamic_vram():
    assert gpu._device_name("cuda:0 AMD Radeon AI PRO R9700 : native") == "AMD Radeon AI PRO R9700"
    assert gpu._device_name(None) is None
    stats = Comfy(aimdo=True).get_json("/system_stats")
    assert gpu._dynamic_vram(stats)
    assert not gpu._dynamic_vram(Comfy(aimdo=True, argv=["--disable-dynamic-vram"]).get_json("/system_stats"))
    assert not gpu._dynamic_vram(Comfy().get_json("/system_stats"))
    assert not gpu._dynamic_vram({"system": {"comfy_package_versions": "nonsense"}})


def test_a_refusal_carries_its_reason():
    plain = gpu.GpuBusy("busy")
    assert (plain.code, plain.holders, plain.device, plain.comfy_mb) == (None, [], None, None)


def test_windows_counters_are_read_per_process_and_card(monkeypatch):
    dedicated = {"pid_111_luid_0x00000000_0x0000F97F_phys_0": 4703 * 2 ** 20, "pid_111_luid_0x00000000_0x00011A88_phys_0": 0,
                 "pid_222_luid_0x00000000_0x0000f97f_phys_0": 2 * GB, "_Total": 9 * GB, "pid_x_luid_y": 5}
    shared = {"pid_111_luid_0x00000000_0x0000f97f_phys_0": 274 * 2 ** 20}
    monkeypatch.setattr(gpumem, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(gpumem, "_counter", lambda path: dedicated if "Dedicated" in path else shared)
    assert sorted(gpumem.processes(), key=lambda r: r["pid"]) == [
        {"pid": 111, "adapter": "0x00000000_0x0000f97f", "mb": 4703}, {"pid": 222, "adapter": "0x00000000_0x0000f97f", "mb": 2048}]
    assert {r["pid"]: r["mb"] for r in gpumem.processes(shared=True)} == {111: 4977, 222: 2048}
    monkeypatch.setattr(gpumem, "os", SimpleNamespace(name="posix"))
    assert gpumem.processes() is None and gpumem.adapters() is None and gpumem.descendants(1) == set()


def test_the_processes_npc_forge_started_are_its_children_and_theirs():
    parents = {10: 1, 11: 10, 12: 11, 20: 1, 30: 10, 40: 10, 50: 50}
    created = {1: 0, 10: 100, 11: 200, 12: 300, 20: 150, 30: 50, 40: None, 50: 10}
    # 30 names 10 as its parent but is older than it: 10 is a newer process that took the pid of 30's ended parent
    assert gpumem._tree(parents, 10, created.get) == {11, 12}
    assert gpumem._tree(parents, 99, created.get) == set()
