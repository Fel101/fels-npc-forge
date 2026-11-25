"""Set up ComfyUI (npcgen/comfy_setup.py, step 9): where ComfyUI keeps its models, what each tier needs, and installing it after a
yes. A stand-in ComfyUI and a stand-in Hugging Face answer over HTTP on this computer (the downloads go through HF_ENDPOINT, as
a mirror would), so what is tested is what NPC Forge sends, reads and writes. The pinned files are small synthetic ones; the real
pins (data/comfy_models.json) are checked for shape only, as nothing here downloads a model."""
import copy
import hashlib
import http.server
import importlib
import json
import os
import pathlib
import re
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import pytest

from npcgen import comfy_setup, config, engines, hardware, three_d

GB = 2 ** 30
ZIMAGE = comfy_setup.part_files("image:zimage")            # (folder, name) of Z-Image Turbo's three files, as its graph loads them
QWEN_EDIT = comfy_setup.part_files("edit:qwen2511")
QWEN_IMAGE = comfy_setup.part_files("image:qwen2512")


# ---- the real pins and the tiers --------------------------------------------------------------------------------------------
def test_every_file_each_part_loads_is_pinned_to_a_commit_with_its_size_and_hash():
    comfy_setup._cache.clear()
    for part in comfy_setup.PARTS:
        files = comfy_setup.part_files(part["id"])
        assert files, part["id"]
        for folder, name in files:
            entry = comfy_setup.pin(folder, name)
            assert entry, f"{folder}/{name} ({part['id']}) has no pin in data/comfy_models.json"
            assert re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]) and re.fullmatch(r"[0-9a-f]{40}", entry["revision"])
            assert entry["size"] > 1_000_000 and "/" in entry["repo"] and entry["path"].endswith(name.rsplit("_", 1)[-1])


def test_each_part_has_a_licence_entry_and_says_what_it_makes():
    from npcgen import licences
    by_selector = licences.load()["by_selector"]
    for part in comfy_setup.PARTS:
        assert part["licence"] in by_selector, part["id"]
        assert not part["what"][0].islower() and not part["what"].endswith(".")


def test_the_tiers_need_what_the_measurements_allow():
    assert comfy_setup.tier_parts("16") == ["image:zimage", "edit:qwen2511", "3d:hunyuan21"]
    assert comfy_setup.tier_parts("24") == ["image:zimage", "image:qwen2512", "edit:qwen2511", "3d:hunyuan21"]
    assert comfy_setup.tier_parts("32") == ["image:zimage", "image:qwen2512", "edit:qwen2511", "3d:hunyuan21", "3d:pixal3d",
                                            "3d:trellis2"]
    assert not any("mage" in comfy_setup.tier_parts(t) for t in ("16", "24", "32"))      # research only: never picked for you
    assert comfy_setup.tier_parts(None) == comfy_setup.tier_parts("none") == []


def test_the_node_install_script_and_the_helper_copy_the_same_nodes():
    for node in comfy_setup.NODES.values():
        assert node["source"].read_text("utf-8").splitlines()[0] == node["marker"]
    assert comfy_setup.NODES["trellis2"]["node"] == three_d.DECODE_NODE
    assert "NPCForgePixal3DHeadTextureRefine" in three_d.GENERATORS["pixal3d"]["nodes"]


# ---- stand-ins --------------------------------------------------------------------------------------------------------------
def serve(handler_class):
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler_class)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    httpd.url = f"http://127.0.0.1:{httpd.server_address[1]}"
    return httpd


class Mirror:
    """Hugging Face's /{repo}/resolve/{revision}/{path}, with Range. `gate`: hold the rest of a file after `hold_at` bytes until
    it is set; `break_after`: the first answer ends after that many bytes; `ignore_range`: always the whole file;
    `wrong`: names served with other bytes of the same size."""

    def __init__(self):
        self.files, self.requests = {}, []
        self.gate, self.hold_at, self.break_after, self.ignore_range, self.wrong = None, None, None, False, set()

    def handler(self):
        mirror = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self):
                mirror.requests.append({"path": self.path, "range": self.headers.get("Range"), "agent": self.headers.get("User-Agent")})
                match = re.fullmatch(r"/(.+?/.+?)/resolve/([0-9a-f]+)/(.+)", self.path)
                data = mirror.files.get(match and urllib.parse.unquote(match.group(3)))
                if data is None:
                    self.send_response(404)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                name = match.group(3).rsplit("/", 1)[-1]
                if name in mirror.wrong:
                    data = bytes(255 - b for b in data)
                start = 0
                wanted = self.headers.get("Range")
                if wanted and not mirror.ignore_range:
                    start = int(re.fullmatch(r"bytes=(\d+)-", wanted).group(1))
                    self.send_response(206)
                    self.send_header("Content-Range", f"bytes {start}-{len(data) - 1}/{len(data)}")
                else:
                    self.send_response(200)
                body = data[start:]
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if mirror.break_after is not None:
                    cut, mirror.break_after = mirror.break_after, None
                    self.wfile.write(body[:cut])
                    self.wfile.flush()
                    self.close_connection = True
                    return
                if mirror.gate is not None and mirror.hold_at is not None and start < mirror.hold_at:
                    first = mirror.hold_at - start
                    self.wfile.write(body[:first])
                    self.wfile.flush()
                    mirror.gate.wait(60)
                    body = body[first:]
                try:
                    self.wfile.write(body)
                except OSError:
                    pass

            def log_message(self, *args):
                pass
        return Handler


class StandInComfy:
    """ComfyUI on this computer: /system_stats (a card of `card_gb`), /internal/folder_paths (folders under `root`, the old
    name `clip` listed first for text encoders, as an extra_model_paths.yaml can make it), /object_info/<node> and /models."""

    def __init__(self, root, card_gb=16, nodes=()):
        self.root, self.card_gb, self.nodes = pathlib.Path(root), card_gb, set(nodes)
        models = self.root / "models"
        self.folders = {"diffusion_models": [models / "diffusion_models", models / "unet"],
                        "text_encoders": [models / "clip", models / "text_encoders"], "vae": [models / "vae"],
                        "loras": [models / "loras"], "checkpoints": [models / "checkpoints"],
                        "clip_vision": [models / "clip_vision"], "background_removal": [models / "background_removal"]}
        for dirs in self.folders.values():
            dirs[0].mkdir(parents=True, exist_ok=True)
        (self.root / "custom_nodes").mkdir(parents=True, exist_ok=True)

    def handler(self):
        comfy = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                route = self.path.split("?")[0]
                status, payload = 404, {"error": {"message": "no such route"}}
                if route == "/system_stats":
                    status, payload = 200, {"system": {"comfyui_version": "0.37.0"},
                                            "devices": [{"name": "cuda:0 Stand-in", "type": "cuda",
                                                         "vram_total": comfy.card_gb * GB - 300 * 2 ** 20}]}
                elif route == "/internal/folder_paths":
                    status, payload = 200, {**{k: [str(d) for d in v] for k, v in comfy.folders.items()},
                                            "custom_nodes": [str(comfy.root / "custom_nodes")]}
                elif route.startswith("/object_info/"):
                    node = route.rsplit("/", 1)[1]
                    status, payload = 200, ({node: {"input": {}, "output": []}} if node in comfy.nodes else {})
                elif route.startswith("/models/"):
                    kind = route.split("/")[2]
                    status, payload = 200, sorted(p.name for d in comfy.folders.get(kind, []) if d.is_dir() for p in d.iterdir())
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass
        return Handler


class Client:
    """What comfy_setup asks of NPC Forge's ComfyUI client: `base` and `get_json`."""

    def __init__(self, base):
        self.base = base

    def get_json(self, path, timeout=30):
        with urllib.request.urlopen(self.base + path, timeout=timeout) as answer:
            return json.load(answer)


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A stand-in ComfyUI, a stand-in Hugging Face with the files of three parts, and data/comfy_models.json pinning them."""
    mirror = Mirror()
    rows = []
    for index, (folder, name) in enumerate(dict.fromkeys(ZIMAGE + QWEN_EDIT + QWEN_IMAGE)):
        data = os.urandom(120_000 + 20_000 * index)
        path = f"split_files/{folder}/{name}"
        mirror.files[path] = data
        rows.append({"folder": folder, "name": name, "repo": "Test/models", "revision": "ab" * 20, "path": path,
                     "size": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    pins = tmp_path / "comfy_models.json"
    pins.write_text(json.dumps({"checked": "2026-11-25", "files": rows}), "utf-8")
    monkeypatch.setattr(comfy_setup, "DATA", pins)
    monkeypatch.setattr(comfy_setup, "_cache", {})
    monkeypatch.setattr(comfy_setup, "CHUNK", 16 * 1024)
    hub = serve(mirror.handler())
    monkeypatch.setenv("HF_ENDPOINT", hub.url)
    comfy = StandInComfy(tmp_path / "ComfyUI")
    comfy_server = serve(comfy.handler())
    client = Client(comfy_server.url)
    yield type("World", (), {"mirror": mirror, "comfy": comfy, "client": client, "tmp": tmp_path,
                             "data": {(r["folder"], r["name"]): mirror.files[r["path"]] for r in rows},
                             "place": lambda chosen=None: comfy_setup.locate(client, chosen)})
    if mirror.gate is not None:
        mirror.gate.set()
    for httpd in (hub, comfy_server):
        httpd.shutdown()
        httpd.server_close()


def installer():
    work = comfy_setup.Installer()
    work.wait = lambda seconds: work._stop.is_set()        # no pause between attempts in a test
    return work


def finish(work, timeout=20):
    work.join(timeout)
    assert not work.running()
    return work.snapshot()


def where(world, folder, name):
    return next((d / name for d in world.comfy.folders[folder] if (d / name).exists()), None)


# ---- where ComfyUI is -------------------------------------------------------------------------------------------------------
def test_a_running_comfyui_on_this_computer_says_where_its_files_go_and_which_card_it_has(world):
    place = world.place()
    assert place["how"] == "running" and place["local"] and not place["problem"] and place["vram_mb"] < 16 * 1024
    status = comfy_setup.status(world.client, place)
    assert status["tier"] == "16" and status["models_folder"] == str(world.comfy.root / "models")
    zimage = next(p for p in status["parts"] if p["id"] == "image:zimage")
    assert [f["state"] for f in zimage["files"]] == ["missing"] * 3
    assert zimage["download_bytes"] == sum(len(world.data[f]) for f in ZIMAGE)
    text_encoder = next(f for f in zimage["files"] if f["folder"] == "text_encoders")
    assert text_encoder["where"] == str(world.comfy.root / "models" / "text_encoders")  # the folder of its own name, not the older clip
    assert zimage["licence"]["status"] == "yes" and zimage["tiers"] == ["16", "24", "32"]


def test_a_comfyui_on_another_computer_gets_nothing():
    class Remote:
        base = "http://192.168.1.20:8188"

        def get_json(self, path, timeout=30):
            return {"system": {"comfyui_version": "0.37.0"}, "devices": []}
    place = comfy_setup.locate(Remote())
    assert place["how"] == "running" and not place["local"] and "another computer" in place["problem"]
    with pytest.raises(comfy_setup.SetupError, match="another computer"):
        comfy_setup.plan(place, ["image:zimage"])


class Offline:
    base = "http://127.0.0.1:9"

    def get_json(self, path, timeout=30):
        raise OSError("refused")


def test_without_a_running_comfyui_the_chosen_folder_is_used_with_its_old_folder_names(tmp_path):
    root = tmp_path / "ComfyUI_windows_portable" / "ComfyUI"
    for name in ("models/unet", "models/clip", "models/vae", "custom_nodes"):
        (root / name).mkdir(parents=True)
    assert "Start it" in comfy_setup.locate(Offline())["problem"]
    place = comfy_setup.locate(Offline(), str(tmp_path / "ComfyUI_windows_portable"))       # the portable ZIP's outer folder
    assert place["how"] == "folder" and place["base"] == str(root.resolve()) and not place["problem"]
    rows = {f["folder"]: f for f in comfy_setup.status(Offline(), place)["parts"][0]["files"]}
    assert rows["diffusion_models"]["where"].endswith("unet") and rows["text_encoders"]["where"].endswith("clip")
    assert rows["vae"]["state"] == "missing"


def test_a_folder_that_is_not_comfyui_is_refused_and_says_what_to_choose(tmp_path):
    with pytest.raises(comfy_setup.SetupError, match="not a ComfyUI folder"):
        comfy_setup.comfy_folder(tmp_path)
    with pytest.raises(comfy_setup.SetupError, match="no folder"):
        comfy_setup.comfy_folder(tmp_path / "nowhere")
    with pytest.raises(comfy_setup.SetupError, match="whole folder"):
        comfy_setup.comfy_folder("ComfyUI")
    place = comfy_setup.locate(Offline(), str(tmp_path))
    assert place["how"] is None and "cannot be used" in place["problem"]


def test_the_folder_picker_answers_what_was_chosen_or_nothing(monkeypatch):
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "D:\\ComfyUI\n", "")
    monkeypatch.setattr(comfy_setup, "_run", run)
    assert comfy_setup.pick_folder() == "D:\\ComfyUI"
    monkeypatch.setattr(comfy_setup, "_run", lambda command, **kwargs: subprocess.CompletedProcess(command, 0, "", ""))
    assert comfy_setup.pick_folder() is None

    def missing(command, **kwargs):
        raise FileNotFoundError(command[0])
    monkeypatch.setattr(comfy_setup, "_run", missing)
    with pytest.raises(comfy_setup.SetupError, match="Details") as refused:
        comfy_setup.pick_folder()
    assert refused.value.status == 501


# ---- installing -------------------------------------------------------------------------------------------------------------
def test_nothing_is_downloaded_without_a_yes(world):
    work = installer()
    for answer in (None, False, "yes", 1):
        with pytest.raises(comfy_setup.SetupError, match="without a yes"):
            work.start(world.place(), ["image:zimage"], yes=answer)
    assert world.mirror.requests == [] and not work.running()


def test_install_downloads_each_file_from_its_pinned_commit_checks_it_and_puts_it_where_comfyui_looks(world):
    work = installer()
    work.start(world.place(), ["image:zimage"], yes=True)
    done = finish(work)
    assert done["state"] == "done" and "ComfyUI can use the files now" in done["message"]
    for folder, name in ZIMAGE:
        assert where(world, folder, name).read_bytes() == world.data[(folder, name)]
        assert not list(where(world, folder, name).parent.glob("*" + comfy_setup.PART))
    assert {r["agent"] for r in world.mirror.requests} == {comfy_setup.USER_AGENT}
    assert all("/Test/models/resolve/" + "ab" * 20 + "/split_files/" in r["path"] for r in world.mirror.requests)
    assert done["bytes_done"] == done["bytes_total"] == sum(len(world.data[f]) for f in ZIMAGE)
    status = comfy_setup.status(world.client, world.place())
    assert next(p for p in status["parts"] if p["id"] == "image:zimage")["ready"]
    assert sorted(world.client.get_json("/models/vae")) == ["ae.safetensors"]     # ComfyUI lists it: a portrait can find it


def test_files_two_parts_share_are_fetched_once_and_files_already_there_are_checked_not_fetched(world):
    folder, name = QWEN_EDIT[1]                                        # the text encoder Qwen's two engines share
    target = world.comfy.folders[folder][0] / name
    target.write_bytes(world.data[(folder, name)])
    work = installer()
    started = work.start(world.place(), ["image:qwen2512", "edit:qwen2511"], yes=True)
    names = [(s["action"], s["name"]) for s in started["steps"]]
    assert names.count(("check", name)) == 1 and ("download", name) not in names
    assert len(names) == len(set(QWEN_EDIT + QWEN_IMAGE))
    done = finish(work)
    assert done["state"] == "done"
    assert next(s for s in done["steps"] if s["name"] == name)["message"] == "there already, checked"
    assert not any(name in r["path"] for r in world.mirror.requests)


def test_a_stopped_download_keeps_what_arrived_and_the_next_install_carries_on_from_there(world):
    folder, name = ZIMAGE[0]
    size = len(world.data[(folder, name)])
    half = (size // 2) // comfy_setup.CHUNK * comfy_setup.CHUNK      # whole chunks: a read waits for a full one
    world.mirror.gate, world.mirror.hold_at = threading.Event(), half
    work = installer()
    work.start(world.place(), ["image:zimage"], yes=True)
    end = time.monotonic() + 30
    while work.snapshot()["steps"][0]["done"] < half and time.monotonic() < end:
        time.sleep(0.02)
    assert work.snapshot()["steps"][0]["done"] == half, "the first half never arrived"
    stopping = work.stop()
    assert stopping["state"] == "stopping"
    world.mirror.gate.set()
    stopped = finish(work)
    assert stopped["state"] == "stopped" and "carries on" in stopped["message"]
    kept = world.comfy.folders[folder][0] / (name + comfy_setup.PART)
    # what arrived is kept: the half, and the chunk a read was waiting for when Stop came
    assert half <= kept.stat().st_size <= half + comfy_setup.CHUNK < size and not (world.comfy.folders[folder][0] / name).exists()
    row = next(f for f in comfy_setup.status(world.client, world.place())["parts"][0]["files"] if f["name"] == name)
    assert row["state"] == "partial" and row["have"] == kept.stat().st_size

    world.mirror.gate = None
    world.mirror.requests.clear()
    work.start(world.place(), ["image:zimage"], yes=True)
    assert finish(work)["state"] == "done"
    first = next(r for r in world.mirror.requests if name in r["path"])
    assert first["range"] == f"bytes={row['have']}-"
    assert (world.comfy.folders[folder][0] / name).read_bytes() == world.data[(folder, name)] and not kept.exists()


def test_a_download_that_breaks_off_is_taken_up_again_where_it_stopped(world):
    world.mirror.break_after = 50_000
    work = installer()
    work.start(world.place(), ["image:zimage"], yes=True)
    assert finish(work)["state"] == "done"
    folder, name = ZIMAGE[0]
    asked = [r["range"] for r in world.mirror.requests if name in r["path"]]
    assert asked == [None, "bytes=50000-"]
    assert where(world, folder, name).read_bytes() == world.data[(folder, name)]


def test_a_server_that_ignores_the_range_starts_the_file_again(world):
    folder, name = ZIMAGE[2]
    data = world.data[(folder, name)]
    (world.comfy.folders[folder][0] / (name + comfy_setup.PART)).write_bytes(data[:30_000])
    world.mirror.ignore_range = True
    work = installer()
    work.start(world.place(), ["image:zimage"], yes=True)
    assert finish(work)["state"] == "done"
    assert where(world, folder, name).read_bytes() == data


def test_a_file_that_does_not_match_its_pinned_hash_is_deleted_and_never_put_in_place(world):
    folder, name = ZIMAGE[2]
    world.mirror.wrong.add(name)
    work = installer()
    work.start(world.place(), ["image:zimage"], yes=True)
    done = finish(work)
    assert done["state"] == "failed" and "did not match its pinned SHA-256" in done["message"]
    target = world.comfy.folders[folder][0]
    assert not (target / name).exists() and not (target / (name + comfy_setup.PART)).exists()


def test_a_different_copy_is_used_as_it_is_and_never_replaced(world):
    folder, name = ZIMAGE[0]
    mine = world.comfy.folders[folder][1]                             # another folder ComfyUI lists (unet)
    mine.mkdir(parents=True)
    (mine / name).write_bytes(b"my own variant" * 100)
    status = comfy_setup.status(world.client, world.place())
    row = next(f for f in status["parts"][0]["files"] if f["name"] == name)
    assert row["state"] == "other" and row["where"] == str(mine)
    work = installer()
    started = work.start(world.place(), ["image:zimage"], yes=True)
    assert name not in [s["name"] for s in started["steps"]]
    assert finish(work)["state"] == "done" and (mine / name).read_bytes() == b"my own variant" * 100
    assert not (world.comfy.folders[folder][0] / name).exists()


def test_a_damaged_copy_of_the_pinned_size_is_replaced_once_the_pinned_file_has_arrived(world):
    folder, name = ZIMAGE[1]
    data = world.data[(folder, name)]
    damaged = world.comfy.folders[folder][0] / name
    damaged.write_bytes(bytes(len(data)))
    work = installer()
    work.start(world.place(), ["image:zimage"], yes=True)
    done = finish(work)
    assert done["state"] == "done" and damaged.read_bytes() == data
    assert next(s for s in done["steps"] if s["name"] == name)["message"] == "replaced the damaged copy"


def test_not_enough_room_is_said_before_anything_is_downloaded(world, monkeypatch):
    monkeypatch.setattr(comfy_setup, "_free", lambda directory: 10_000)
    with pytest.raises(comfy_setup.SetupError, match="free"):
        installer().start(world.place(), ["image:zimage"], yes=True)
    assert world.mirror.requests == []


def test_only_one_install_runs_at_a_time(world):
    world.mirror.gate, world.mirror.hold_at = threading.Event(), 10_000
    work = installer()
    work.start(world.place(), ["image:zimage"], yes=True)
    with pytest.raises(comfy_setup.SetupError, match="already running"):
        work.start(world.place(), ["image:zimage"], yes=True)
    work.stop()
    world.mirror.gate.set()
    assert finish(work)["state"] == "stopped"


def files_there(world, part_id):
    """Pin a part's files and put them where ComfyUI looks, so only its nodes are left to install."""
    rows = [{"folder": f, "name": n, "repo": "Test/models", "revision": "cd" * 20, "path": f"x/{n}", "size": 4,
             "sha256": hashlib.sha256(b"data").hexdigest()} for f, n in comfy_setup.part_files(part_id)]
    comfy_setup.DATA.write_text(json.dumps({"files": rows}), "utf-8")
    comfy_setup._cache.clear()
    for folder, name in comfy_setup.part_files(part_id):
        (world.comfy.folders[folder][0] / name).write_bytes(b"data")


def test_forge_nodes_are_copied_into_custom_nodes_and_comfyui_is_asked_to_restart(world):
    files_there(world, "3d:pixal3d")
    before = {p["id"]: p for p in comfy_setup.status(world.client, world.place())["parts"]}["3d:pixal3d"]
    assert before["nodes"][0]["state"] == "missing" and not before["ready"]
    work = installer()
    work.start(world.place(), ["3d:pixal3d"], yes=True)
    done = finish(work)
    assert done["state"] == "done" and done["restart_comfy"] and "Restart ComfyUI" in done["message"]
    node = comfy_setup.NODES["head"]
    assert (world.comfy.root / "custom_nodes" / node["folder"] / "__init__.py").read_text("utf-8") == node["source"].read_text("utf-8")
    after = {p["id"]: p for p in comfy_setup.status(world.client, world.place())["parts"]}["3d:pixal3d"]
    assert after["nodes"][0]["state"] == "restart"                     # copied; ComfyUI loads it when it next starts
    world.comfy.nodes.add(node["node"])
    assert {p["id"]: p for p in comfy_setup.status(world.client, world.place())["parts"]}["3d:pixal3d"]["ready"]


def test_another_node_in_forges_folder_is_left_alone(world):
    files_there(world, "3d:pixal3d")
    other = world.comfy.root / "custom_nodes" / comfy_setup.NODES["head"]["folder"] / "__init__.py"
    other.parent.mkdir(parents=True)
    other.write_text("# somebody else's node\n", "utf-8")
    part = {p["id"]: p for p in comfy_setup.status(world.client, world.place())["parts"]}["3d:pixal3d"]
    assert part["nodes"][0]["state"] == "conflict" and not part["installable"]
    with pytest.raises(comfy_setup.SetupError, match="left it alone"):
        comfy_setup.plan(world.place(), ["3d:pixal3d"])
    assert other.read_text("utf-8") == "# somebody else's node\n"


def test_the_trellis2_node_pack_is_named_with_its_page_never_installed(world):
    part = {p["id"]: p for p in comfy_setup.status(world.client, world.place())["parts"]}["3d:trellis2"]
    pack = part["packs"][0]
    assert pack["state"] == "missing" and pack["url"].startswith("https://github.com/") and not part["ready"]
    world.comfy.nodes.add(comfy_setup.PACKS["trellis2"]["node"])
    part = {p["id"]: p for p in comfy_setup.status(world.client, world.place())["parts"]}["3d:trellis2"]
    assert part["packs"][0]["state"] == "ready"


def test_the_models_card_sees_new_files_at_once(world):
    engines._FILES[("somewhere", "vae")] = (time.monotonic(), [])
    three_d._FILES[("somewhere", "checkpoints")] = (time.monotonic(), [])
    work = installer()
    work.start(world.place(), ["image:zimage"], yes=True)
    finish(work)
    assert ("somewhere", "vae") not in engines._FILES and ("somewhere", "checkpoints") not in three_d._FILES


# ---- through NPC Forge's own API ------------------------------------------------------------------------------------------
def call(base, method, path, body=None):
    data = json.dumps(body).encode() if body is not None else (b"{}" if method != "GET" else None)
    headers = {"Content-Type": "application/json"} if method != "GET" else {}
    try:
        with urllib.request.urlopen(urllib.request.Request(base + path, data=data, method=method, headers=headers), timeout=30) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


@pytest.fixture
def forge(world, tmp_path, monkeypatch):
    server = importlib.import_module("server")
    monkeypatch.setenv("FELS_SUITE_DIR", str(tmp_path / "suite"))
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(hardware, "_cache", {})
    started = []

    def start(comfy_url, require_auth=False):
        cfg = copy.deepcopy(config.load())
        cfg.update(library_path=str(tmp_path / f"library{len(started)}"), comfy_url=comfy_url, auth_token="a" * 20)
        app = server.App(cfg)
        app.comfy_setup.wait = lambda seconds: app.comfy_setup._stop.is_set()
        httpd = server.create_server(("127.0.0.1", 0), app, require_auth=require_auth)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        started.append(httpd)
        return f"http://127.0.0.1:{httpd.server_address[1]}", app
    yield start
    for httpd in started:
        httpd.npc_app.comfy_setup.join(10)
        httpd.shutdown()
        httpd.server_close()


def test_the_api_lists_the_parts_and_installs_only_after_a_yes(world, forge):
    base, app = forge(world.client.base)
    code, status = call(base, "GET", "/api/comfy-setup")
    assert code == 200 and status["comfy"]["how"] == "running" and status["tier"] == "16"
    assert status["tiers"]["16"] == ["image:zimage", "edit:qwen2511", "3d:hunyuan21"]
    assert call(base, "POST", "/api/comfy-setup/install", {"parts": ["image:zimage"]})[0] == 400
    assert call(base, "POST", "/api/comfy-setup/install", {"parts": "image:zimage", "yes": True})[0] == 400
    assert call(base, "POST", "/api/comfy-setup/install", {"parts": ["nothing"], "yes": True})[0] == 400
    assert world.mirror.requests == []
    code, answer = call(base, "POST", "/api/comfy-setup/install", {"parts": ["image:zimage"], "yes": True})
    assert code == 200 and answer["install"]["state"] in ("running", "done")
    app.comfy_setup.join(20)
    assert call(base, "GET", "/api/comfy-setup?progress=1")[1]["install"]["state"] == "done"
    parts = {p["id"]: p for p in call(base, "GET", "/api/comfy-setup")[1]["parts"]}
    assert parts["image:zimage"]["ready"] and not parts["edit:qwen2511"]["ready"]


def test_the_api_saves_the_comfyui_folder_for_when_comfyui_is_not_running(world, forge, tmp_path, monkeypatch):
    base, app = forge("http://127.0.0.1:9")
    code, status = call(base, "GET", "/api/comfy-setup")
    assert status["comfy"]["how"] is None and "Start it" in status["comfy"]["problem"]
    assert call(base, "POST", "/api/comfy-setup/install", {"parts": ["image:zimage"], "yes": True})[0] == 409
    (tmp_path / "plain").mkdir()
    code, refused = call(base, "POST", "/api/comfy-setup/folder", {"folder": str(tmp_path / "plain")})
    assert code == 400 and "not a ComfyUI folder" in refused["error"]
    code, saved = call(base, "POST", "/api/comfy-setup/folder", {"folder": str(world.comfy.root)})
    assert code == 200 and saved["folder"] == str(world.comfy.root.resolve())
    assert json.loads((tmp_path / "config.json").read_text("utf-8"))["comfy_folder"] == str(world.comfy.root.resolve())
    status = call(base, "GET", "/api/comfy-setup")[1]
    assert status["comfy"]["how"] == "folder" and status["models_folder"] == str(world.comfy.root.resolve() / "models")
    monkeypatch.setattr(comfy_setup, "_run", lambda command, **kwargs: subprocess.CompletedProcess(command, 0, "", ""))
    assert call(base, "POST", "/api/comfy-setup/folder", {"pick": True})[1] == {"folder": str(world.comfy.root.resolve()),
                                                                              "changed": False}
    assert call(base, "POST", "/api/comfy-setup/folder", {"folder": None})[1]["folder"] is None
    assert "comfy_folder" not in json.loads((tmp_path / "config.json").read_text("utf-8"))


def test_the_folder_picker_is_not_offered_when_forge_needs_a_password(world, forge, monkeypatch):
    base, app = forge(world.client.base, require_auth=True)
    monkeypatch.setattr(comfy_setup, "_run", lambda *a, **k: pytest.fail("the picker must not open"))
    import base64
    request = urllib.request.Request(base + "/api/comfy-setup/folder", data=b'{"pick": true}', method="POST",
                                     headers={"Content-Type": "application/json",
                                              "Authorization": "Basic " + base64.b64encode(b"npcforge:" + b"a" * 20).decode()})
    with pytest.raises(urllib.error.HTTPError) as refused:
        urllib.request.urlopen(request, timeout=10)
    assert refused.value.code == 403 and "network" in json.loads(refused.value.read())["error"]
