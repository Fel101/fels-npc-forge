"""Measure what each picture pipeline needs, for docs/hardware.md and the "Your PC" tiers (npcgen/hardware.py).

    python scripts/measure_gpu.py --forge http://127.0.0.1:8775 [--only portrait-zimage,sheet] [--card-gb 16]
                                  [--ballast-python PATH] [--out local/hardware]

For every pipeline it empties the card (ComfyUI is told to unload everything), queues the job through the Forge at
--forge, and reads ComfyUI's process every half second from Windows' per-process counters, as NPC Forge's own GPU check
does: its dedicated graphics memory, the system memory it uses through the GPU (Windows' "shared" memory: a card that ran
out spills there), and its RAM (private bytes and working set). It writes one JSON file per run and prints a table.

Run it against a Forge with a library of its own (NPCGEN_LIBRARY), with nothing else on the GPU, with One model at a time
off (the script empties the card itself, and the helper below would count as another program), and with
NPCGEN_MEASURE=1, which skips the big engines' free-memory start check (what that check should ask for is what is being
measured).

--card-gb makes the card look smaller: a helper process (--ballast-python, a Python with PyTorch for this GPU, such as
ComfyUI's own) holds the rest of the card's memory before each run, leaving that size less DESKTOP_GB free, so ComfyUI
loads and offloads as it would on a card of that size (npcgen/hardware.py HEADROOM_MB). Windows only."""

import argparse
import ctypes
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from ctypes import wintypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from npcgen import gpumem  # noqa: E402

EXPRESSIONS = ["happy", "sad", "angry", "surprised"]
DESKTOP_GB = 1                  # what the desktop and the driver keep of a card (npcgen/hardware.py HEADROOM_MB)
PIPELINES = {
    # id: (what, assets, options)
    "portrait-mage": ("Portrait, Mage-Flow", {"portrait": True}, {"image_model": "mage"}),
    "portrait-zimage": ("Portrait, Z-Image Turbo", {"portrait": True}, {"image_model": "zimage"}),
    "portrait-qwen2512": ("Portrait, Qwen-Image 2512 + Lightning", {"portrait": True}, {"image_model": "qwen2512"}),
    "expressions-mage": ("Four expressions, Mage-Flow-Edit", {"expressions": EXPRESSIONS}, {"edit_model": "mage"}),
    "expressions-qwen2511": ("Four expressions, Qwen-Image-Edit 2511 + Lightning", {"expressions": EXPRESSIONS},
                             {"edit_model": "qwen2511"}),
    "sprite-qwen2511": ("Full-body sprite from the portrait, Qwen-Image-Edit 2511", {"sprite": True},
                        {"edit_model": "qwen2511", "sprite_mode": "match"}),
    "sheet-qwen2511": ("Model sheet, four views, Qwen-Image-Edit 2511", {"sheet": {"pose": "a_pose"}}, {"edit_model": "qwen2511"}),
    "walk-qwen2511": ("Walk cycle, four frames facing the camera, Qwen-Image-Edit 2511",
                      {"animations": {"clips": ["walk"], "dirs": ["down"], "frames": 4}}, {"edit_model": "qwen2511"}),
    "3d-hunyuan21": ("3D model, Hunyuan3D 2.1", {"model_3d": True}, {"three_d_model": "hunyuan21"}),
    "3d-trellis2": ("3D model, Trellis2", {"model_3d": True}, {"three_d_model": "trellis2"}),
    "3d-pixal3d": ("3D model, Pixal3D (medium)", {"model_3d": True}, {"three_d_model": "pixal3d", "three_d_quality": "medium"}),
}
# For data/hardware.json: the engine each pipeline measures, the pipelines that decide a tier's engines, and the ones a
# tier's list leaves out (Mage-Flow is not cleared for commercial use: the table in docs/hardware.md still shows it).
ENGINE_OF = {"portrait-mage": "image:mage", "portrait-zimage": "image:zimage", "portrait-qwen2512": "image:qwen2512",
             "expressions-mage": "edit:mage", "expressions-qwen2511": "edit:qwen2511", "sprite-qwen2511": "edit:qwen2511",
             "sheet-qwen2511": "edit:qwen2511", "walk-qwen2511": "edit:qwen2511", "3d-hunyuan21": "3d:hunyuan21",
             "3d-trellis2": "3d:trellis2", "3d-pixal3d": "3d:pixal3d"}
MAKES = {"portrait-zimage": "Portraits with Z-Image Turbo", "portrait-qwen2512": "Portraits with Qwen-Image 2512",
         "expressions-qwen2511": "Expressions with Qwen-Image-Edit 2511", "sprite-qwen2511": "Full-body sprites",
         "sheet-qwen2511": "Model sheets", "walk-qwen2511": "Walk cycles and other animation",
         "3d-hunyuan21": "3D models with Hunyuan3D 2.1", "3d-trellis2": "3D models with Trellis2",
         "3d-pixal3d": "3D models with Pixal3D"}
DECIDES = {"portrait-zimage", "portrait-qwen2512", "expressions-qwen2511"}
# What a pipeline is made from, for the tiers (npcgen/hardware.py fit): Trellis2 and Pixal3D from the model sheet's four views
# (npcgen/three_d.py FOUR_VIEW_GENERATORS), Hunyuan3D from the sprite.
MADE_FROM = {"3d-trellis2": ["sheet-qwen2511"], "3d-pixal3d": ["sheet-qwen2511"], "3d-hunyuan21": ["sprite-qwen2511"]}
UNLISTED = {"portrait-mage", "expressions-mage"}
# What each needs made first (the edits start from the portrait; 3D from the sprite or the model sheet).
NEEDS = {"expressions": ["portrait-qwen2512"], "sprite": ["portrait-qwen2512"], "sheet": ["portrait-qwen2512"],
         "walk": ["sprite-qwen2511"], "3d": ["sprite-qwen2511"]}

# The helper fills its share of the card and keeps using every block of it: Windows moves an allocation nobody uses out of
# the card when another program wants the room (measured: an idle helper lost all 9 GB to a Qwen picture), and a real smaller
# card has no such room to give.
BALLAST = r"""
import sys, threading, time, torch
keep = int(float(sys.argv[1]) * 2 ** 30)
free, total = torch.cuda.mem_get_info(0)
chunk = 256 * 2 ** 20
blocks = [torch.ones(chunk, dtype=torch.uint8, device="cuda:0") for _ in range(max(0, total - keep) // chunk)]
torch.cuda.synchronize()
print(f"holding {len(blocks) * chunk / 2 ** 30:.1f} GB of {total / 2 ** 30:.1f} GB", flush=True)
stop = threading.Event()

def use():
    while not stop.is_set():
        for block in blocks:
            block[:4096].add_(1)
        torch.cuda.synchronize()
        time.sleep(0.05)

threading.Thread(target=use, daemon=True).start()
sys.stdin.read()
stop.set()
"""


class _Counters(ctypes.Structure):          # PROCESS_MEMORY_COUNTERS_EX
    _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD), ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t), ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t), ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t), ("PrivateUsage", ctypes.c_size_t)]


def ram_mb(pid):
    """(private bytes, working set) of a process in MB, or (None, None)."""
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel.OpenProcess.restype = wintypes.HANDLE
    handle = kernel.OpenProcess(0x1000 | 0x0010, False, pid)     # PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_VM_READ
    if not handle:
        return None, None
    try:
        counters = _Counters()
        counters.cb = ctypes.sizeof(counters)
        if not psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
            return None, None
        return counters.PrivateUsage // 2 ** 20, counters.WorkingSetSize // 2 ** 20
    finally:
        kernel.CloseHandle(handle)


class Sampler(threading.Thread):
    """Peaks of one process: dedicated and shared GPU memory (on its busiest card) and RAM, every half second; and the
    dedicated memory every other program held on that card (`others_mb`, leaving out `skip`), to spot a disturbed run."""

    def __init__(self, pid, skip=()):
        super().__init__(daemon=True)
        self.pid, self.skip, self.stop, self.peak, self.samples = pid, set(skip), threading.Event(), {}, 0
        self.helper_mb = []                       # what the helper held at each reading
        self.card = None

    def run(self):
        while not self.stop.is_set():
            rows = gpumem.processes() or []
            dedicated = {row["adapter"]: row["mb"] for row in rows if row["pid"] == self.pid}
            both = {row["adapter"]: row["mb"] for row in gpumem.processes(shared=True) or [] if row["pid"] == self.pid}
            if dedicated:
                self.card = max(dedicated, key=dedicated.get)
            card = self.card
            private, working = ram_mb(self.pid)
            others = sum(row["mb"] for row in rows if row["adapter"] == card and row["pid"] not in self.skip | {self.pid})
            reading = {"vram_mb": dedicated.get(card, 0), "shared_mb": max(0, both.get(card, 0) - dedicated.get(card, 0)),
                       "ram_private_mb": private or 0, "ram_working_mb": working or 0, "others_mb": others}
            for key, value in reading.items():
                self.peak[key] = max(self.peak.get(key, 0), value)
            if dedicated and self.skip:
                self.helper_mb.append(self.helper(rows, card))
                self.peak["helper_min_mb"] = min(self.helper_mb)
            self.samples += 1
            self.stop.wait(0.5)

    def helper(self, rows, card):
        """What the smaller-card helper holds on the card now (0 without one)."""
        return sum(row["mb"] for row in rows if row["adapter"] == card and row["pid"] in self.skip)


def call(base, method, path, body=None, timeout=60):
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(base + path, data=data, method=method, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read() or b"{}")


def empty_card(comfy):
    call(comfy, "POST", "/free", {"unload_models": True, "free_memory": True})
    for _ in range(60):
        queue = call(comfy, "GET", "/queue")
        if not queue.get("queue_running") and not queue.get("queue_pending"):
            break
        time.sleep(1)
    time.sleep(8)                               # ComfyUI frees when idle, on its own timer


def run_job(forge, npc_id, assets, options, limit):
    answer = call(forge, "POST", "/api/generate", {"npc_id": npc_id, "assets": assets, "options": {**options, "reroll": True}})
    job_id = answer["jobs"][0]["id"]
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline:
        rows = {row["id"]: row for row in call(forge, "GET", "/api/jobs")}
        row = rows.get(job_id)
        if row and row["status"] not in ("queued", "running"):
            return row
        time.sleep(2)
    call(forge, "POST", f"/api/jobs/{job_id}/cancel")
    return {"status": "timeout", "error": f"stopped after {limit} s", "steps": []}


def build_table(folder):
    """data/hardware.json from the runs in `folder` (one <pipeline>-<card>gb.json each; a later run of the same pair wins).
    An invalid run (the helper lost some of what it held, so the card was bigger than said) is left out."""
    runs = {}
    for path in sorted(Path(folder).glob("*gb.json")):
        row = json.loads(path.read_text("utf-8"))
        if row["status"] == "invalid":
            continue
        runs.setdefault(row["pipeline"], {})[str(int(row["card_gb"]))] = {
            key: row.get(key) for key in ("status", "seconds", "step_seconds", "vram_mb", "shared_mb", "ram_private_mb",
                                          "ram_working_mb", "others_mb", "error")}
    measured = sorted({json.loads(p.read_text("utf-8"))["measured"] for p in Path(folder).glob("*gb.json")})
    devices = sorted({json.loads(p.read_text("utf-8"))["device"] for p in Path(folder).glob("*gb.json")})
    return {"measured": measured[-1] if measured else None,
            "card": ", ".join(name.replace("cuda:0 ", "").split(" : ")[0] for name in devices),
            "pipelines": [{"id": pipeline, "what": PIPELINES[pipeline][0], "makes": MAKES.get(pipeline), "engine": ENGINE_OF[pipeline],
                           "default": pipeline in DECIDES, "listed": pipeline not in UNLISTED,
                           **({"needs": MADE_FROM[pipeline]} if pipeline in MADE_FROM else {}), "runs": runs[pipeline]}
                          for pipeline in PIPELINES if pipeline in runs]}


def markdown(table):
    """The measured table for docs/hardware.md: one row a pipeline, one column a card size."""
    sizes = sorted({size for row in table["pipelines"] for size in row["runs"]}, key=int, reverse=True)

    def cell(run, full):
        if run is None:
            return "not run"
        steps = [str(seconds) for seconds in run.get("step_seconds") or []]
        crawled = run["status"] != "done" or bool(full and full.get("seconds") and run["seconds"] > 3 * full["seconds"])
        each = f" (steps: {', '.join(steps)} s)" if crawled and len(steps) > 1 else ""
        if run["status"] != "done":
            why = ("a picture took more than Forge's 420 s limit" if "took more than" in (run.get("error") or "")
                   else run.get("error") or run["status"])
            return f"stopped after {run['seconds']} s: {why}{each}"
        shared = run.get("shared_mb") or 0
        through = f" + {shared / 1024:.1f} GB shared" if shared > 1024 else ""
        return f"{run['seconds']} s{each}, {run['vram_mb'] / 1024:.1f} GB{through}, RAM {run['ram_private_mb'] / 1024:.1f} GB"

    lines = ["| What | " + " | ".join(f"{size} GB card" for size in sizes) + " |", "|---|" + "---|" * len(sizes)]
    lines += [f"| {row['what']} | " + " | ".join(cell(row["runs"].get(size), row["runs"].get("32")) for size in sizes) + " |"
              for row in table["pipelines"]]
    return "\n".join(lines)


def main():
    if "--table" in sys.argv:                       # measure_gpu.py --table <runs folder> <data/hardware.json>
        folder, target = sys.argv[sys.argv.index("--table") + 1:][:2]
        table = build_table(folder)
        Path(target).write_text(json.dumps(table, indent=2) + "\n", "utf-8")
        print(f"Wrote {target}\n\n" + markdown(table))
        return 0
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--forge", required=True)
    parser.add_argument("--comfy", default="http://127.0.0.1:8188")
    parser.add_argument("--only", default="", help="comma-separated pipeline ids (default: all): " + ", ".join(PIPELINES))
    parser.add_argument("--card-gb", type=float, help="make the card look this big (needs --ballast-python)")
    parser.add_argument("--ballast-python")
    parser.add_argument("--ballast-device", default="1", help="the GPU index ComfyUI draws on (its --cuda-device)")
    parser.add_argument("--limit", type=int, default=3600, help="seconds a pipeline may take")
    parser.add_argument("--out", default="local/hardware")
    args = parser.parse_args()
    forge, comfy = args.forge.rstrip("/"), args.comfy.rstrip("/")
    wanted = [p for p in args.only.split(",") if p] or list(PIPELINES)
    unknown = [p for p in wanted if p not in PIPELINES]
    if unknown:
        sys.exit("Unknown pipelines: " + ", ".join(unknown))
    pid = gpumem.listening_pid(int(comfy.rsplit(":", 1)[-1]))
    if pid is None:
        sys.exit(f"Nothing listens at {comfy}: start ComfyUI first.")
    stats = call(comfy, "GET", "/system_stats")
    device = stats["devices"][0]
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    draft = call(forge, "POST", "/api/roll", {"setting": "fantasy", "style": "semi_real", "seed": 9143101})
    npc_id = call(forge, "POST", "/api/npcs", draft)["id"]
    print(f"Character {npc_id}; ComfyUI pid {pid} on {device.get('name')}", flush=True)

    ballast = None
    if args.card_gb:
        if not args.ballast_python:
            sys.exit("--card-gb needs --ballast-python: a Python with PyTorch for this GPU.")
        env = {**os.environ, "HIP_VISIBLE_DEVICES": args.ballast_device, "CUDA_VISIBLE_DEVICES": args.ballast_device}
        keep = args.card_gb - DESKTOP_GB             # a real card of that size has the desktop's and driver's share taken
        ballast = subprocess.Popen([args.ballast_python, "-c", BALLAST, str(keep)], stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, text=True, env=env)
        said = ballast.stdout.readline().strip()
        print("Ballast:", said, flush=True)
        held_mb = float(said.split()[1]) * 1024 if said.startswith("holding") else 0

    made, rows = set(), []
    try:
        for pipeline in wanted:
            for first in NEEDS.get(pipeline.split("-")[0], []):
                if first not in made and first not in wanted[:wanted.index(pipeline)]:
                    what, assets, options = PIPELINES[first]
                    print(f"  first: {what}", flush=True)
                    run_job(forge, npc_id, assets, options, args.limit)
                    made.add(first)
            what, assets, options = PIPELINES[pipeline]
            empty_card(comfy)
            # (a venv's python.exe starts the real interpreter as its child: leave out the helper's whole tree)
            sampler = Sampler(pid, skip=({ballast.pid} | set(gpumem.descendants(ballast.pid) or ())) if ballast else ())
            started = time.monotonic()
            sampler.start()
            job = run_job(forge, npc_id, assets, options, args.limit)
            sampler.stop.set()
            sampler.join()
            seconds = round(time.monotonic() - started)
            made.add(pipeline)
            failed = [step for step in job.get("steps", []) if step.get("status") == "error"]
            status, error = job["status"], job.get("error") or (failed[0].get("error") if failed else None)
            # Windows may take some of the helper's memory for a moment when ComfyUI wants more (it is put back at the
            # helper's next touch); the card was the size said unless the helper was short for more than a tenth of the run.
            short = sum(1 for mb in sampler.helper_mb if mb < 0.9 * held_mb) / max(1, len(sampler.helper_mb))
            if ballast is not None:
                sampler.peak["helper_short_share"] = round(short, 3)
            if ballast is not None and (short > 0.1 or not sampler.helper_mb):
                status, error = "invalid", (f"Windows moved the helper's memory out of the card for {short:.0%} of the run "
                                            f"(at the least it held {sampler.peak.get('helper_min_mb', 0) / 1024:.1f} of "
                                            f"{held_mb / 1024:.1f} GB)")
            row = {"pipeline": pipeline, "what": what, "card_gb": int(args.card_gb or round(int(device["vram_total"]) / 2 ** 30)),
                   "status": status, "seconds": seconds, **sampler.peak, "error": error,
                   "notes": [step["note"] for step in job.get("steps", []) if step.get("note")],
                   "step_seconds": [round(step.get("seconds") or 0) for step in job.get("steps", [])],
                   "device": device.get("name"), "measured": time.strftime("%Y-%m-%d")}
            rows.append(row)
            (out / f"{pipeline}-{row['card_gb']}gb.json").write_text(json.dumps(row, indent=2), "utf-8")
            print(f"{pipeline:24} {row['status']:9} {seconds:5d} s  VRAM {row.get('vram_mb', 0) / 1024:5.1f} GB  "
                  f"shared {row.get('shared_mb', 0) / 1024:4.1f} GB  RAM {row.get('ram_private_mb', 0) / 1024:5.1f} GB  "
                  f"others {row.get('others_mb', 0) / 1024:4.1f} GB"
                  + (f"  ({row['error']})" if row["error"] else ""), flush=True)
    finally:
        if ballast is not None:
            ballast.stdin.close()
            ballast.wait(timeout=30)
    return 0


if __name__ == "__main__":
    sys.exit(main())
