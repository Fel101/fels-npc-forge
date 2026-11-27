"""Step 8 end to end, through a real in-process server: a stand-in ComfyUI that reports a 16 or 32 GB card, and for One model
at a time a stand-in Voice Lab and fake LM Studio and Ollama servers, each holding a model. All of them answer over HTTP on
this computer, so what is tested is what NPC Forge sends and reads; only LM Studio's `lms` command is replaced (by a stand-in
that unloads the fake LM Studio's model). The measured table is a small synthetic one (tests/test_hardware.py has its rules)."""
import copy
import http.server
import importlib
import io
import json
import subprocess
import threading
import time
import urllib.error
import urllib.request

import pytest
from PIL import Image

from npcgen import config, engines, fels_suite, gpu, hardware, suite_gpu

GB = 2 ** 30
MB = 2 ** 20


def run(vram_gb, seconds=30):
    return {"status": "done", "vram_mb": int(vram_gb * 1024), "shared_mb": 0, "seconds": seconds, "ram_private_mb": 11000}


TABLE = {"pipelines": [
    {"id": "portrait-zimage", "what": "Portraits with Z-Image Turbo", "engine": "image:zimage", "default": True,
     "runs": {"32": run(28.5), "24": run(22.0), "16": run(14.8)}},
    {"id": "portrait-qwen2512", "what": "Portraits with Qwen-Image 2512", "engine": "image:qwen2512", "default": True,
     "runs": {"32": run(30.8), "24": run(22.5), "16": {"status": "error", "vram_mb": 15000, "seconds": 40}}},
    {"id": "expressions-qwen2511", "what": "Expressions with Qwen-Image-Edit 2511", "engine": "edit:qwen2511", "default": True,
     "runs": {"32": run(30.0), "24": run(23.0), "16": {"status": "error", "vram_mb": 15000, "seconds": 40}}},
]}


def png():
    out = io.BytesIO()
    picture = Image.new("RGB", (64, 64), (90, 120, 150))
    picture.paste((200, 60, 40), (0, 0, 32, 32))                  # not one flat colour: NPC Forge refuses those
    picture.save(out, "PNG")
    return out.getvalue()


def serve(answer):
    """A local HTTP server in a thread; `answer(method, path, body)` gives (status, JSON-able or bytes)."""
    class Handler(http.server.BaseHTTPRequestHandler):
        def _reply(self):
            length = int(self.headers.get("Content-Length") or 0)
            status, payload = answer(self.command, self.path, self.rfile.read(length) if length else b"")
            data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/octet-stream" if isinstance(payload, bytes) else "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        do_GET = do_POST = do_PUT = _reply

        def log_message(self, *args):
            pass
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    httpd.url = f"http://127.0.0.1:{httpd.server_address[1]}"
    return httpd


class StandInComfy:
    """ComfyUI on a card of `card_gb` with every model file. A picture runs until `finish` is set; `seen` keeps, for each
    graph, what `snapshot()` said when it arrived (what had been unloaded by then)."""

    def __init__(self, card_gb, snapshot=lambda: None):
        self.card_gb, self.snapshot = card_gb, snapshot
        self.graphs, self.seen, self.frees, self.loaded_gb = [], [], 0, 0
        self.finish, self.running = threading.Event(), threading.Event()
        self.finish.set()

    def __call__(self, method, path, body):
        route = path.split("?")[0]
        if route == "/system_stats":
            total = self.card_gb * GB - 300 * MB                     # a card sold as N GB reports a little less
            return 200, {"system": {"comfyui_version": "0.36.0", "ram_free": 60 * GB, "ram_total": 64 * GB},
                         "devices": [{"name": f"cuda:0 Stand-in {self.card_gb} GB card", "type": "cuda",
                                      "vram_total": total, "vram_free": total - self.loaded_gb * GB}]}
        if route.startswith("/models/"):
            folder = route.split("/")[2]
            return 200, [name for spec in (*engines.IMAGE_ENGINES.values(), *engines.EDIT_ENGINES.values()) if "build" in spec
                         for where, name in engines.engine_files(spec) if where == folder]
        if route == "/queue":
            return 200, {"queue_running": [], "queue_pending": []}
        if route == "/prompt":
            self.graphs.append(json.loads(body)["prompt"])
            self.seen.append(self.snapshot())
            self.loaded_gb = 6
            self.running.set()
            return 200, {"prompt_id": f"p{len(self.graphs)}"}
        if route.startswith("/history/"):
            prompt_id = route.rsplit("/", 1)[1]
            if not self.finish.is_set():
                return 200, {}
            return 200, {prompt_id: {"status": {"completed": True, "status_str": "success"},
                                     "outputs": {"9": {"images": [{"filename": "out.png", "subfolder": "", "type": "temp"}]}}}}
        if route == "/view":
            return 200, png()
        if route == "/free":
            self.frees += 1
            self.loaded_gb = 0
            return 200, {}
        if route in ("/interrupt", "/upload/image"):
            return 200, {"name": "ref.png", "subfolder": "npc_generator", "type": "temp"}
        if route.startswith("/object_info"):
            return 200, {}                                         # no extra nodes (SDPose and the like)
        return 404, {"error": {"message": f"no {route} here"}}


class StandInLab:
    """Voice Lab's side of contract 8: it holds the suite lock (shared) while Kokoro is loaded, unloads on POST /api/unload,
    and refuses a line (409) while another app holds the GPU alone."""

    def __init__(self):
        self.lock = fels_suite.GpuLock("voice-lab")
        self.unloads = 0

    def load(self):
        assert self.lock.acquire("Kokoro 82M", timeout=0)

    def __call__(self, method, path, body):
        if path == "/api/unload":
            self.lock.release()
            self.unloads += 1
            return 200, {"unloaded": True}
        if path == "/v1/audio/speech":
            if not self.lock.held and not self.lock.acquire("Kokoro 82M", timeout=0):
                return 409, {"code": "gpu_busy", "error": "Another app is using the GPU alone."}
            return 200, b"RIFF a line"
        return 404, {}


class FakeOutside:
    """LM Studio (/api/v0/models) and Ollama (/api/ps, keep_alive 0) on ports of their own, each holding one chat model."""

    def __init__(self):
        self.lm_studio, self.ollama = ["chat-model"], ["llama-small"]

    def lms(self, method, path, body):
        if path == "/api/v0/models":
            return 200, {"data": [{"id": "chat-model", "state": "loaded" if self.lm_studio else "not-loaded"}]}
        return 404, {}

    def ollama_api(self, method, path, body):
        if path == "/api/ps":
            return 200, {"models": [{"model": name} for name in self.ollama]}
        if path == "/api/generate" and json.loads(body).get("keep_alive") == 0:
            self.ollama.remove(json.loads(body)["model"])
            return 200, {"done": True}
        return 404, {}

    def run_lms(self, args, **kwargs):
        assert args[1:] == ["unload", "--all"]
        self.lm_studio.clear()
        return subprocess.CompletedProcess(args, 0, "", "")


def call(base, method, path, body=None):
    data = json.dumps(body).encode() if body is not None else (b"{}" if method != "GET" else None)
    headers = {"Content-Type": "application/json"} if method != "GET" else {}
    try:
        with urllib.request.urlopen(urllib.request.Request(base + path, data=data, method=method, headers=headers), timeout=30) as r:
            payload = r.read()
            return r.status, (json.loads(payload) if payload[:1] in (b"{", b"[") else payload)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


@pytest.fixture
def forge(tmp_path, monkeypatch):
    """start(card_gb, switch, snapshot) → (base URL, stand-in ComfyUI, app): an NPC Forge drawing on a stand-in ComfyUI."""
    server = importlib.import_module("server")
    monkeypatch.setenv("FELS_SUITE_DIR", str(tmp_path / "suite"))
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")         # never this checkout's own config.json
    monkeypatch.setattr(hardware, "load", lambda: TABLE)
    monkeypatch.setattr(hardware, "_cache", {})
    monkeypatch.setattr(gpu, "_processes", lambda: [])                             # never this PC's real GPU counters
    monkeypatch.setattr(gpu, "_adapters", lambda: None)
    monkeypatch.setattr(gpu, "_comfy_pid", lambda comfy: None)
    monkeypatch.setattr(engines, "_sleep", lambda seconds: time.sleep(0.02))
    monkeypatch.setattr(suite_gpu, "_TOLD", set())
    engines._LOADED.clear()
    before = copy.deepcopy(gpu.GATE.settings)
    started = []

    def start(card_gb, switch, snapshot=lambda: None):
        comfy = StandInComfy(card_gb, snapshot)
        comfy_server = serve(comfy)
        cfg = copy.deepcopy(config.load())
        cfg.update(library_path=str(tmp_path / "library"), comfy_url=comfy_server.url)
        cfg["gpu"] = {**cfg.get("gpu", {}), "one_model_at_a_time": switch, "wait_seconds": 5, "unload_idle_seconds": 0.5}
        app = server.App(cfg)
        httpd = server.create_server(("127.0.0.1", 0), app)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        started.append((httpd, comfy_server))
        return f"http://127.0.0.1:{httpd.server_address[1]}", comfy, app
    yield start
    for httpd, comfy_server in started:
        httpd.shutdown()
        httpd.server_close()
        comfy_server.shutdown()
        comfy_server.server_close()
    gpu.GATE.reset()
    gpu.GATE.settings = before
    engines._LOADED.clear()


def portrait(base, options=None):
    """Queue a portrait for a new character; returns (job id, character id)."""
    _, draft = call(base, "POST", "/api/roll", {"setting": "fantasy", "style": "semi_real", "seed": 77})
    _, npc = call(base, "POST", "/api/npcs", draft)
    body = {"npc_id": npc["id"], "assets": {"portrait": True}}
    if options is not None:
        body["options"] = options
    code, queued = call(base, "POST", "/api/generate", body)
    assert code == 200, queued
    return queued["jobs"][0]["id"], npc["id"]


def finished(base, job_id, timeout=30):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        job = next(row for row in call(base, "GET", "/api/jobs")[1] if row["id"] == job_id)
        if job["status"] not in ("queued", "running"):
            return job
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish")


def until(condition, timeout=10):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if condition():
            return True
        time.sleep(0.05)
    return False


@pytest.mark.parametrize("card_gb, tier, image_model", [(16, "16", "zimage"), (32, "32", "qwen2512")])
def test_the_your_pc_box_reads_the_card_and_a_portrait_uses_its_tier_defaults(forge, card_gb, tier, image_model):
    base, comfy, app = forge(card_gb, switch=False)
    code, cfg = call(base, "GET", "/api/config")                   # read before /api/health: the page asks for both at once
    assert code == 200 and cfg["defaults"]["options"]["image_model"] == image_model
    code, health = call(base, "GET", "/api/health")
    box = health["your_pc"]
    assert code == 200 and box["tier"] == tier and box["device"] == f"cuda:0 Stand-in {card_gb} GB card"
    assert box["total_mb"] == card_gb * 1024 - 300 and box["defaults"]["image_model"] == image_model
    assert ("Portraits with Qwen-Image 2512" in box["makes"]) is (card_gb == 32)

    job_id, npc_id = portrait(base)                                 # names no engine: the tier's
    job = finished(base, job_id)
    assert job["status"] == "done", job
    drawn = json.dumps(comfy.graphs)
    weights = [name for _, name in engines.engine_files(engines.IMAGE_ENGINES[image_model])]
    assert weights and all(name in drawn for name in weights)
    assert call(base, "GET", f"/api/npcs/{npc_id}")[1]["assets"]["portrait"]["engine"] == engines.IMAGE_ENGINES[image_model]["label"]

    chosen = "mage" if card_gb == 16 else "zimage"                     # the person can still pick anything
    job_id, npc_id = portrait(base, {"image_model": chosen})
    assert finished(base, job_id)["status"] == "done"
    drawn_with = call(base, "GET", f"/api/npcs/{npc_id}")[1]["assets"]["portrait"].get("engine")
    assert drawn_with == (None if chosen == "mage" else engines.IMAGE_ENGINES[chosen]["label"])    # (Mage-Flow says nothing)


def test_the_switch_on_unloads_the_lab_lm_studio_and_ollama_first_and_holds_the_gpu_alone(forge, monkeypatch):
    lab, outside = StandInLab(), FakeOutside()
    servers = [serve(lab), serve(outside.lms), serve(outside.ollama_api)]
    monkeypatch.setitem(fels_suite.PORTS, "lm-studio", servers[1].server_address[1])
    monkeypatch.setitem(fels_suite.PORTS, "ollama", servers[2].server_address[1])
    real_unload_outside = fels_suite.unload_outside
    monkeypatch.setattr(fels_suite, "unload_outside", lambda **kwargs: real_unload_outside(
        find_lms=lambda: "lms", run=outside.run_lms, **kwargs))
    try:
        assert fels_suite.write_address("voice-lab", servers[0].url, "1.0.0", 1, "stand-in")
        lab.load()
        base, comfy, app = forge(32, switch=True, snapshot=lambda: (lab.unloads, list(outside.lm_studio), list(outside.ollama)))
        settings = call(base, "GET", "/api/gpu?lock=1")[1]
        assert settings["one_model_at_a_time"] is True and settings["unload_external_models"] is True   # both on by default
        comfy.finish.clear()                                          # the picture runs until the checks below are done
        job_id, _ = portrait(base, {"image_model": "zimage"})
        assert comfy.running.wait(20)
        assert comfy.seen == [(1, [], [])]                            # all three unloaded before ComfyUI got the picture

        light = call(base, "GET", "/api/gpu?lock=1")[1]
        holder, = light["suite_lock"]["holders"]
        assert (holder["name"], holder["what"], holder["alone"]) == ("NPC Forge", "pictures", True) and light["alone"]
        assert call(servers[0].url, "POST", "/v1/audio/speech", {"input": "Not now."})[0] == 409

        comfy.finish.set()
        job = finished(base, job_id)
        assert job["status"] == "done", job
        assert suite_gpu.unloaded_sentence(["Voice Lab", "LM Studio", "Ollama"]) in job["steps"][0]["note"]
        # Forge lets go once its idle unload has emptied the card; then the Lab speaks again.
        assert until(lambda: not any(row["app"] == "npc-forge" for row in fels_suite.gpu_holders()))
        assert comfy.frees and call(servers[0].url, "POST", "/v1/audio/speech", {"input": "Now."})[0] == 200
    finally:
        lab.lock.release()
        fels_suite.remove_address("voice-lab")
        for one in servers:
            one.shutdown()
            one.server_close()


@pytest.mark.parametrize("external,cancelled", [(True, False), (False, False), (True, True)])
def test_altera_chat_finishes_before_external_unload(tmp_path, monkeypatch, external, cancelled):
    monkeypatch.setenv("FELS_SUITE_DIR", str(tmp_path))
    monkeypatch.setattr(suite_gpu, "ASK_EVERY", 0)
    monkeypatch.setattr(suite_gpu, "_TOLD", set())
    chat = fels_suite.GpuLock("altera")
    pictures = fels_suite.GpuLock("npc-forge")
    assert chat.acquire("a chat reply", timeout=0)
    busy = threading.Event()
    reply_finished = threading.Event()
    cancel = threading.Event()
    calls = []

    def contact(holder):
        assert holder["app"] == "altera"
        if reply_finished.is_set():
            chat.release()
            calls.append("released")
            return 200, {}
        calls.append("busy")
        busy.set()
        return 409, {"code": "busy", "doing": "generating an LM Studio reply"}

    def unload(**kwargs):
        calls.append("external unload")
        return []

    def acquire():
        try:
            suite_gpu.acquire_pictures(pictures, True, 2, cancel, allow_external_unload=lambda: external)
            calls.append("picture")
        except suite_gpu.Cancelled:
            calls.append("cancelled")
        finally:
            pictures.release()

    monkeypatch.setattr(suite_gpu, "unload_holder", contact)
    monkeypatch.setattr(fels_suite, "unload_outside", unload)
    thread = threading.Thread(target=acquire, daemon=True)
    thread.start()
    try:
        assert busy.wait(2)
        assert "external unload" not in calls
        assert fels_suite.GpuLock("voice-lab").alone_wanted()["app"] == "npc-forge"
        (cancel if cancelled else reply_finished).set()
        thread.join(5)
        assert not thread.is_alive()
        assert fels_suite.GpuLock("voice-lab").alone_wanted() is None
        after_busy = [call for call in calls if call != "busy"]
        if cancelled:
            assert after_busy == ["cancelled"] and chat.held
        else:
            assert after_busy == ["released", *(["external unload"] if external else []), "picture"]
    finally:
        cancel.set()
        thread.join(5)
        chat.release()


def test_the_switch_off_shares_the_card_and_unloads_nothing(forge, monkeypatch):
    lab, outside = StandInLab(), FakeOutside()
    servers = [serve(lab), serve(outside.lms), serve(outside.ollama_api)]
    monkeypatch.setitem(fels_suite.PORTS, "lm-studio", servers[1].server_address[1])
    monkeypatch.setitem(fels_suite.PORTS, "ollama", servers[2].server_address[1])
    monkeypatch.setattr(fels_suite, "_find_lms", lambda: pytest.fail("the switch off must not run lms"))
    try:
        assert fels_suite.write_address("voice-lab", servers[0].url, "1.0.0", 1, "stand-in")
        lab.load()
        base, comfy, app = forge(32, switch=False)
        comfy.finish.clear()
        job_id, _ = portrait(base, {"image_model": "zimage"})
        assert comfy.running.wait(20)
        holders = {(row["name"], row["alone"]) for row in call(base, "GET", "/api/gpu?lock=1")[1]["suite_lock"]["holders"]}
        assert holders == {("Voice Lab", False), ("NPC Forge", False)}
        assert call(servers[0].url, "POST", "/v1/audio/speech", {"input": "Side by side."})[0] == 200
        comfy.finish.set()
        job = finished(base, job_id)
        assert job["status"] == "done", job
        assert lab.unloads == 0 and lab.lock.held and outside.lm_studio == ["chat-model"] and outside.ollama == ["llama-small"]
        assert "Unloaded" not in (job["steps"][0].get("note") or "")
    finally:
        lab.lock.release()
        fels_suite.remove_address("voice-lab")
        for one in servers:
            one.shutdown()
            one.server_close()
