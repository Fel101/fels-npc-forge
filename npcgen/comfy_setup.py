"""The ComfyUI helper (Set up ComfyUI on the Models card, docs/comfyui-setup.md): where ComfyUI keeps its models, what each tier
needs (npcgen/hardware.py), what is there already, and, after a yes, putting in what is missing.

Nothing is downloaded without a yes (`Installer.start(..., yes=True)`). Every file comes from the Hugging Face commit pinned in
data/comfy_models.json (scripts/pin_comfy_models.py) and is checked against its size and SHA-256 before it is put in place. A
download that is stopped or broken off keeps what arrived beside the file (`<name>.npcforge-part`, which ComfyUI does not list),
and the next Install carries on from there. HF_ENDPOINT, as Hugging Face's own tools read it, points the downloads at a mirror.

Where ComfyUI is:
- a ComfyUI running on this computer at the configured address says where its folders are (GET /internal/folder_paths, the
  folders of extra_model_paths.yaml included). A new file goes into the first folder it lists with the kind's own name
  (models/diffusion_models, where docs/requirements.md says, rather than the older models/unet), and a file is there when it is in any
  folder it lists for that kind;
- when none answers, the ComfyUI folder the person chose (config.json "comfy_folder"): its models\\<kind> folders.
A ComfyUI on another computer gets nothing from here: its files must be put there.

What is there is judged by name and size (a file of the right name but another size is "a different copy": used as it is,
never replaced). Install checks the SHA-256 of a file already there before it counts it, and replaces a damaged copy only once
the pinned one has arrived whole. NPC Forge's own ComfyUI nodes (npcgen/comfy_extensions) are copied into custom_nodes; ComfyUI
loads them when it next starts. Node packs of other people are named with their page, never installed."""
import hashlib
import ipaddress
import json
import os
import pathlib
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from . import comfy as comfymod
from . import engines, hardware, licences, three_d

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "comfy_models.json"
EXTENSIONS = ROOT / "npcgen" / "comfy_extensions"
HUB = "https://huggingface.co"
USER_AGENT = "NPC-Forge (ComfyUI helper)"
GET_COMFYUI = "https://www.comfy.org/download"
PART = ".npcforge-part"            # what has arrived of a download, beside the file it becomes
CHUNK = 1 << 20
MARGIN = 1 << 30                   # free space left on a drive after the downloads
RETRIES = 4                        # a download that breaks off is taken up again this many times before it gives up
TIMEOUT = 60
ALIASES = {"diffusion_models": ("diffusion_models", "unet"), "text_encoders": ("text_encoders", "clip")}  # ComfyUI's older names

# What the helper offers, in the order the dialog lists it. `id` is the engine in data/hardware.json ("image:zimage"), so the
# tiers say which parts they need; `licence` is the selector in data/licences.json.
PARTS = (
    {"id": "image:zimage", "what": "Portraits with Z-Image Turbo", "licence": "image:zimage",
     "note": "Z-Image Turbo pulls toward famous characters: check that its pictures look like yours."},
    {"id": "image:qwen2512", "what": "Portraits with Qwen-Image 2512", "licence": "image:qwen2512"},
    {"id": "edit:qwen2511", "what": "Expressions, sprites, model sheets and animation with Qwen-Image-Edit 2511",
     "licence": "edit:qwen2511"},
    {"id": "3d:hunyuan21", "what": "3D models with Hunyuan3D 2.1", "licence": "three_d:hunyuan21"},
    {"id": "3d:pixal3d", "what": "3D models with Pixal3D", "licence": "three_d:pixal3d", "nodes": ("head",)},
    {"id": "3d:trellis2", "what": "3D models with Trellis2", "licence": "three_d:trellis2", "nodes": ("trellis2",),
     "packs": ("trellis2",)},
    {"id": "mage", "what": "Mage-Flow, the engine NPC Forge's prompts were first tuned on", "licence": "image:mage",
     "note": "Microsoft released Mage-Flow for research, not for products or services, so NPC Forge never picks it for you."},
)
NODES = {      # NPC Forge's own ComfyUI nodes (scripts/install_pixal_head_node.py installs them by hand)
    "head": {"name": "NPC Forge Pixal3D head-texture node", "source": EXTENSIONS / "npcforge_pixal_head_texture.py",
             "folder": "npcforge_pixal_head_texture", "node": "NPCForgePixal3DHeadTextureRefine",
             "marker": '"""ComfyUI node that adds head-resolution Pixal3D projection features to NPC Forge textures."""'},
    "trellis2": {"name": "NPC Forge Trellis2 shape-decode node", "source": EXTENSIONS / "npcforge_trellis2_decode.py",
                 "folder": "npcforge_trellis2_decode", "node": three_d.DECODE_NODE,
                 "marker": '"""ComfyUI node that decodes a Trellis2 shape latent to a mesh without the CuMesh hole fill, '
                           'which crashes the GPU on ROCm for Windows."""'},
}
PACKS = {      # other people's node packs a part needs: named with their page, never installed from here
    "trellis2": {"name": "ComfyUI-Trellis2", "url": "https://github.com/visualbruno/ComfyUI-Trellis2", "node": "Trellis2LoadModel",
                 "also": "with its Pixal3D models"},
}


class SetupError(Exception):
    """Something the person can act on. `status` is the HTTP status the API answers with."""

    def __init__(self, message, status=409):
        super().__init__(message)
        self.status = status


# ---- what is pinned and what each part needs --------------------------------------------------------------------------------------
_lock = threading.Lock()
_cache = {}


def manifest():
    """data/comfy_models.json: {"checked", "files": [{"folder", "name", "repo", "revision", "path", "size", "sha256"}]}."""
    with _lock:
        stamp = DATA.stat().st_mtime_ns if DATA.exists() else None
        if _cache.get("stamp") != stamp or "table" not in _cache:
            try:
                table = json.loads(DATA.read_text("utf-8"))
            except (OSError, ValueError):
                table = {"files": []}
            _cache.update(stamp=stamp, table=table, pins={(f["folder"], f["name"]): f for f in table.get("files") or []})
        return _cache["table"]


def pin(folder, name):
    manifest()
    return _cache["pins"].get((folder, name))


def part(part_id):
    found = next((p for p in PARTS if p["id"] == part_id), None)
    if found is None:
        raise SetupError(f"NPC Forge cannot set up {part_id!r}.", 400)
    return found


def part_files(part_id, models=None):
    """[(ComfyUI folder, file name)] a part's engine loads, read from the engine itself (npcgen/engines.py, npcgen/three_d.py)."""
    if part_id == "mage":
        names = {**comfymod.DEFAULT_MODELS, **(models or {})}
        return [("diffusion_models", names["unet_t2i"]), ("diffusion_models", names["unet_edit"]),
                ("text_encoders", names["clip"]), ("vae", names["vae"])]
    kind, key = part_id.split(":", 1)
    if kind == "3d":
        return list(three_d.GENERATORS[key]["files"])
    return engines.engine_files((engines.IMAGE_ENGINES if kind == "image" else engines.EDIT_ENGINES)[key])


def tier_parts(tier):
    """The parts a tier needs: the engines of every measured pipeline that runs on a card of that size, slowly or not."""
    rows = hardware.load().get("pipelines") or []
    wanted = {row.get("engine") for row in rows if row.get("listed") and tier and hardware.fit(row, tier, rows)}
    return [p["id"] for p in PARTS if p["id"] in wanted]


def _tiers_of(part_id):
    return [tier for tier, _ in reversed(hardware.TIERS) if part_id in tier_parts(tier)]


# ---- where ComfyUI is -------------------------------------------------------------------------------------------------------------
def _loopback(url):
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _listed(value):
    """A /internal/folder_paths entry: a list of folders, or [folders, extensions] in some versions."""
    if isinstance(value, list) and len(value) == 2 and isinstance(value[0], list):
        value = value[0]
    return [str(p) for p in value if isinstance(p, str)] if isinstance(value, list) else []


def comfy_folder(path):
    """The ComfyUI folder at `path` (the portable ZIP's outer folder leads to the ComfyUI inside it). Raises SetupError."""
    raw = str(path or "").strip().strip('"')
    if not raw:
        raise SetupError("Choose the folder ComfyUI is installed in.", 400)
    folder = pathlib.Path(os.path.expandvars(raw)).expanduser()
    if not folder.is_absolute():
        raise SetupError("Give the whole folder, starting with its drive (for example D:\\ComfyUI).", 400)
    if not folder.is_dir():
        raise SetupError(f"There is no folder {folder}.", 400)
    if not (folder / "models").is_dir() and (folder / "ComfyUI" / "models").is_dir():
        folder = folder / "ComfyUI"
    if not (folder / "models").is_dir():
        raise SetupError(f"{folder} has no models folder, so it is not a ComfyUI folder. Choose the folder that has ComfyUI's "
                         "models and custom_nodes folders in it.", 400)
    return folder.resolve()


def _folder_place(base):
    folders = {}
    for kind in {folder for p in PARTS for folder, _ in _safe_files(p["id"])}:
        names = ALIASES.get(kind, (kind,))
        found = [str(base / "models" / name) for name in names if (base / "models" / name).is_dir()]
        folders[kind] = found or [str(base / "models" / kind)]
    return {"how": "folder", "base": str(base), "local": True, "folders": folders, "custom_nodes": [str(base / "custom_nodes")]}


def _safe_files(part_id, models=None):
    try:
        return part_files(part_id, models)
    except Exception:
        return []


def locate(comfy, chosen=None):
    """Where new files go: {"how": "running" | "folder" | None, "url", "version", "base", "local", "folders": {kind: [dirs]},
    "custom_nodes": [dirs], "problem": what stops the helper, or None}. Never raises."""
    url = getattr(comfy, "base", "")
    try:
        stats = comfy.get_json("/system_stats", timeout=5)
    except Exception:
        stats = None
    if stats is not None:
        device = (stats.get("devices") or [{}])[0] or {}
        try:
            vram = int(device.get("vram_total") or 0) // 2 ** 20 if device.get("type") != "cpu" else 0
        except (TypeError, ValueError):
            vram = 0
        place = {"how": "running", "url": url, "version": (stats.get("system") or {}).get("comfyui_version"), "base": None,
                 "local": _loopback(url), "folders": {}, "custom_nodes": [], "problem": None, "vram_mb": vram or None}
        if not place["local"]:
            place["problem"] = (f"ComfyUI runs on another computer ({url}), so NPC Forge cannot put files there. Set it up on that "
                                "computer: docs/comfyui-setup.md lists the files and where each comes from.")
            return place
        try:
            listed = comfy.get_json("/internal/folder_paths", timeout=10)
        except Exception:
            listed = None
        if not isinstance(listed, dict):
            place["problem"] = ("This ComfyUI does not say where its folders are (it is older than NPC Forge needs). Update "
                                "ComfyUI, or close it and choose its folder here.")
            return place
        place["folders"] = {kind: _listed(value) for kind, value in listed.items() if kind != "custom_nodes"}
        place["custom_nodes"] = _listed(listed.get("custom_nodes"))
        if not any(os.path.isdir(d) for dirs in place["folders"].values() for d in dirs):
            place["local"] = False
            place["problem"] = (f"The ComfyUI at {url} keeps its models in folders this computer does not have, so it runs "
                                "somewhere else. Set it up where it runs.")
        return place
    if chosen:
        try:
            return {**_folder_place(comfy_folder(chosen)), "url": url, "version": None, "problem": None}
        except SetupError as error:
            return {"how": None, "url": url, "local": False, "folders": {}, "custom_nodes": [], "chosen": str(chosen),
                    "problem": f"The ComfyUI folder chosen before cannot be used: {error}"}
    return {"how": None, "url": url, "local": False, "folders": {}, "custom_nodes": [],
            "problem": f"ComfyUI is not answering at {url}. Start it and press Look again, or choose the folder it is installed in."}


def _where(place, folder, name):
    """(path, size) of a file where ComfyUI would find it (any folder it lists for that kind), or (None, None)."""
    for directory in place["folders"].get(folder) or []:
        path = os.path.join(directory, name)
        if os.path.isfile(path):
            return path, os.path.getsize(path)
    return None, None


def _target(place, folder):
    """The folder a new file of this kind goes into: of those ComfyUI lists for it, the first with the kind's own name
    (models/diffusion_models rather than the older models/unet ComfyUI lists before it), else the first; None when it has none."""
    dirs = place["folders"].get(folder) or []
    if place.get("how") == "folder":
        dirs = [d for d in dirs if os.path.isdir(d)] or dirs
    named = [d for d in dirs if os.path.basename(os.path.normpath(d)).casefold() == folder.casefold()]
    return (named or dirs or [None])[0]


def _existing(directory):
    """The folder itself, or the nearest folder above it that exists (a models folder may not be made yet)."""
    probe = pathlib.Path(directory)
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    return probe


def _free(directory):
    try:
        return shutil.disk_usage(_existing(directory)).free
    except OSError:
        return None


def forget_lists():
    """After an install: forget the model and node lists Forge keeps for a minute, so the Models card sees the new files now."""
    for cache in (engines._FILES, three_d._FILES, three_d._NODES, three_d._INFO):
        cache.clear()


# ---- what is there ----------------------------------------------------------------------------------------------------------------
def file_state(place, folder, name):
    """One file: {"folder", "name", "size" (pinned), "state", "have", "where", "source"}. States: ready (there, of the pinned
    size, or with no pin to compare); other (there, another size: a different copy, used as it is); partial (part of a download
    is kept); missing; no_source (missing and not pinned); no_folder (this ComfyUI has no folder of that kind); unknown (no
    ComfyUI found)."""
    entry = pin(folder, name)
    row = {"folder": folder, "name": name, "size": entry and entry["size"], "have": None, "where": None,
           "source": entry and {"repo": entry["repo"], "url": f"{HUB}/{entry['repo']}", "revision": entry["revision"]}}
    if not place.get("local") or place.get("problem"):
        return {**row, "state": "unknown"}
    path, size = _where(place, folder, name)
    if path:
        return {**row, "state": "ready" if not entry or size == entry["size"] else "other", "have": size,
                "where": os.path.dirname(path)}
    target = _target(place, folder)
    if target is None:
        return {**row, "state": "no_folder"}
    kept = os.path.join(target, name + PART)
    if entry and os.path.isfile(kept):
        return {**row, "state": "partial", "have": os.path.getsize(kept), "where": target}
    return {**row, "state": "missing" if entry else "no_source", "where": target}


def _node_info(comfy, node):
    try:
        return (comfy.get_json(f"/object_info/{node}", timeout=10) or {}).get(node)
    except Exception:
        return None


def node_state(place, comfy, key, running=None):
    """One of NPC Forge's nodes: ready (copied and loaded), restart (copied, ComfyUI not restarted since), older (an older copy:
    Install replaces it), conflict (something else has its folder), missing, unknown."""
    node = NODES[key]
    row = {"key": key, "name": node["name"]}
    if not place.get("local") or place.get("problem") or not place.get("custom_nodes"):
        return {**row, "state": "unknown"}
    target = pathlib.Path(place["custom_nodes"][0]) / node["folder"] / "__init__.py"
    content = node["source"].read_text("utf-8")
    try:
        existing = target.read_text("utf-8") if target.exists() else None
    except OSError:
        existing = ""
    if existing is None:
        return {**row, "state": "missing"}
    if existing != content:
        return {**row, "state": "older" if node["marker"] in existing else "conflict", "where": str(target.parent)}
    if running is None:
        running = place.get("how") == "running"
    loaded = running and _node_info(comfy, node["node"]) is not None
    return {**row, "state": "ready" if loaded or not running else "restart", "where": str(target.parent)}


def pack_state(place, comfy, key):
    pack = PACKS[key]
    row = {"key": key, "name": pack["name"], "url": pack["url"], "also": pack.get("also")}
    if place.get("how") != "running" or place.get("problem"):
        return {**row, "state": "unknown"}
    return {**row, "state": "ready" if _node_info(comfy, pack["node"]) is not None else "missing"}


def status(comfy, place, models=None, tier=None, installer=None):
    """What the Setup dialog shows: where ComfyUI is, the tiers and what each part needs and has. `tier`: the card's, when
    ComfyUI is not running to say (the last one read)."""
    rows, catalogue = [], licences.load()
    if place.get("vram_mb"):
        tier = hardware.tier_for(place["vram_mb"])
    tier = tier if tier in dict(hardware.TIERS) else None
    for p in PARTS:
        files = [file_state(place, folder, name) for folder, name in _safe_files(p["id"], models)]
        nodes = [node_state(place, comfy, key) for key in p.get("nodes", ())]
        packs = [pack_state(place, comfy, key) for key in p.get("packs", ())]
        model = catalogue["by_id"].get(catalogue["by_selector"].get(p["licence"]))
        download = sum(f["size"] - (f["have"] if f["state"] == "partial" else 0)
                       for f in files if f["state"] in ("missing", "partial"))
        installable = all(f["state"] in ("ready", "other", "missing", "partial") for f in files) \
            and all(n["state"] in ("ready", "restart", "missing", "older") for n in nodes)
        ready = all(f["state"] in ("ready", "other") for f in files) and all(n["state"] == "ready" for n in nodes) \
            and all(k["state"] == "ready" for k in packs)
        rows.append({"id": p["id"], "what": p["what"], "note": p.get("note"), "tiers": _tiers_of(p["id"]), "files": files,
                     "nodes": nodes, "packs": packs, "download_bytes": download, "ready": ready, "installable": installable,
                     "licence": model and {"selector": p["licence"], "name": model["name"], "licence": model["licence"],
                                           "short": model["short"], "status": licences.effective(model["id"])}})
    root = _models_root(place)
    return {"comfy": {key: place.get(key) for key in ("how", "url", "version", "base", "local", "problem", "chosen")},
            "models_folder": root, "free_bytes": root and _free(root),
            "tier": tier, "tiers": {t: tier_parts(t) for t, _ in reversed(hardware.TIERS)},
            "parts": rows, "checked": manifest().get("checked"), "get_comfyui": GET_COMFYUI,
            "install": installer.snapshot() if installer else None}


def _models_root(place):
    """The folder most new files go into, for the dialog's one line ("into D:\\ComfyUI\\models")."""
    target = _target(place, "diffusion_models") if place.get("local") and not place.get("problem") else None
    return str(pathlib.Path(target).parent) if target else None


# ---- copying NPC Forge's nodes -------------------------------------------------------------------------------------------------
def install_node(custom_nodes, key):
    """Copy one of NPC Forge's nodes into custom_nodes. True when something was written; SetupError when another node has its
    folder."""
    node = NODES[key]
    target_dir = pathlib.Path(custom_nodes) / node["folder"]
    target = target_dir / "__init__.py"
    content = node["source"].read_text("utf-8")
    if target.exists():
        existing = target.read_text("utf-8")
        if existing == content:
            return False
        if node["marker"] not in existing:
            raise SetupError(f"{target_dir} holds another node, so NPC Forge left it alone. Move it, then press Install again.")
    target_dir.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8", newline="\n")
    return True


# ---- choosing a folder ---------------------------------------------------------------------------------------------------------
PICK_WINDOWS = r"""
Add-Type -AssemblyName System.Windows.Forms
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$owner = New-Object System.Windows.Forms.Form -Property @{TopMost = $true; ShowInTaskbar = $false}
$dialog = New-Object System.Windows.Forms.FolderBrowserDialog
$dialog.Description = 'Choose the folder ComfyUI is installed in (it has the models and custom_nodes folders)'
$dialog.ShowNewFolderButton = $false
if ($dialog.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK) { [Console]::Out.Write($dialog.SelectedPath) }
$owner.Dispose()
"""
PICK_TK = ("import tkinter, tkinter.filedialog as f; r = tkinter.Tk(); r.withdraw(); r.attributes('-topmost', True); "
           "print(f.askdirectory(title='Choose the folder ComfyUI is installed in') or '', end='')")
_run = subprocess.run              # tests replace this


def pick_folder():
    """Open this computer's own folder picker and return the folder chosen, or None when it was closed without one. Raises
    SetupError when this computer has no picker NPC Forge can open."""
    if sys.platform == "win32":
        commands = [["powershell", "-NoProfile", "-NonInteractive", "-STA", "-Command", PICK_WINDOWS]]
    else:
        commands = [["zenity", "--file-selection", "--directory", "--title=Choose the folder ComfyUI is installed in"],
                    [sys.executable, "-c", PICK_TK]]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    for command in commands:
        try:
            done = _run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900,
                        creationflags=flags)
        except (OSError, subprocess.SubprocessError):
            continue
        chosen = (done.stdout or "").strip()
        if done.returncode in (0, 1):         # zenity answers 1 when closed without a folder
            return chosen or None
    raise SetupError("This computer has no folder picker NPC Forge can open. Type the folder under Details instead.", 501)


# ---- installing ------------------------------------------------------------------------------------------------------------------
class Stopped(Exception):
    pass


def _url(entry):
    hub = (os.environ.get("HF_ENDPOINT") or HUB).rstrip("/")
    return f"{hub}/{entry['repo']}/resolve/{entry['revision']}/{urllib.parse.quote(entry['path'])}"


def _gb(size):
    return f"{size / 1e9:.1f} GB" if size >= 1e9 else f"{max(1, round(size / 1e6))} MB"


def plan(place, part_ids, models=None, comfy=None):
    """What Install would do for these parts: {"steps": [...], "download_bytes", "check_bytes", "drives": {...}}. Raises
    SetupError when it cannot be done."""
    if place.get("problem") or not place.get("local"):
        raise SetupError(place.get("problem") or "Find ComfyUI first.")
    if not part_ids:
        raise SetupError("Tick what to install first.", 400)
    steps, seen, needs = [], set(), {}
    for part_id in part_ids:
        p = part(part_id)
        for folder, name in part_files(part_id, models):
            if (folder, name) in seen:
                continue
            seen.add((folder, name))
            row = file_state(place, folder, name)
            if row["state"] == "no_source":
                raise SetupError(f"NPC Forge has no download for {folder}/{name}: put it in ComfyUI's {folder} folder "
                                 "yourself, or untick that part.", 400)
            if row["state"] == "no_folder":
                raise SetupError(f"This ComfyUI has no {folder} folder, so it is too old for {p['what'].lower()}. Update "
                                 "ComfyUI, then press Install again.")
            if row["state"] == "ready" and row["size"]:
                steps.append({"action": "check", "folder": folder, "name": name, "size": row["size"], "where": row["where"]})
            elif row["state"] in ("missing", "partial"):
                steps.append({"action": "download", "folder": folder, "name": name, "size": row["size"], "where": row["where"],
                              "kept": row["have"] or 0})
                needs[row["where"]] = needs.get(row["where"], 0) + row["size"] - (row["have"] or 0)
        for key in p.get("nodes", ()):
            if ("node", key) not in seen:
                seen.add(("node", key))
                state = node_state(place, comfy, key)["state"]
                if state == "conflict":
                    raise SetupError(f"Another node is in custom_nodes\\{NODES[key]['folder']}, so NPC Forge left it alone. Move it, "
                                     "then press Install again.")
                if state in ("missing", "older"):
                    steps.append({"action": "node", "key": key, "name": NODES[key]["name"], "size": 0,
                                  "where": place["custom_nodes"][0]})
    drives = {}                                           # the downloads that land on each drive, and its free space
    for directory, size in needs.items():
        try:
            key = os.stat(_existing(directory)).st_dev
        except OSError:
            key = directory
        slot = drives.setdefault(key, {"need": 0, "free": _free(directory), "folder": directory})
        slot["need"] += size
    for slot in drives.values():
        if slot["free"] is not None and slot["need"] + MARGIN > slot["free"]:
            raise SetupError(f"The downloads need {_gb(slot['need'])} beside {slot['folder']}, and that drive has {_gb(slot['free'])} "
                             f"free (NPC Forge leaves {_gb(MARGIN)} spare). Make room, or tick fewer parts.")
    return {"steps": steps, "download_bytes": sum(needs.values()),
            "check_bytes": sum(s["size"] for s in steps if s["action"] == "check"), "drives": list(drives.values())}


class Installer:
    """One install at a time, in a thread of its own; `snapshot()` is what the dialog shows while it runs."""

    def __init__(self):
        self.lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self.state = {"state": "idle", "steps": [], "message": None}
        self.opener = urllib.request.build_opener()       # tests may replace it
        self.wait = self._stop.wait                         # between attempts at a download that broke off
        self.finished = forget_lists                        # called after each install that put something in

    def snapshot(self):
        with self.lock:
            snap = json.loads(json.dumps(self.state))
        steps = snap.get("steps") or []
        snap["bytes_total"] = sum(s["size"] for s in steps)
        snap["bytes_done"] = sum(s.get("done", 0) for s in steps)
        return snap

    def running(self):
        return self._thread is not None and self._thread.is_alive()

    def start(self, place, part_ids, models=None, comfy=None, yes=False):
        if yes is not True:
            raise SetupError("Nothing is downloaded without a yes: send \"yes\": true once the person has agreed.", 400)
        busy = SetupError("An install is already running. Stop it or wait for it to finish.")
        if self.running():
            raise busy
        work = plan(place, part_ids, models, comfy)        # (it may ask ComfyUI: not while holding what the progress reads)
        steps = [{**step, "state": "waiting", "done": 0, "message": None} for step in work["steps"]]
        with self.lock:
            if self.running():
                raise busy
            self._stop.clear()
            self.state = {"state": "running", "parts": list(part_ids), "steps": steps, "started": time.time(), "finished": None,
                          "speed": None, "eta": None, "restart_comfy": False,
                          "message": "Nothing to do: everything these parts need is there." if not steps else None}
            if not steps:
                self.state.update(state="done", finished=time.time())
                return self.snapshot_unlocked()
            self._thread = threading.Thread(target=self._run, args=(place,), name="ComfyUI setup", daemon=True)
            self._thread.start()
            return self.snapshot_unlocked()

    def snapshot_unlocked(self):
        snap = json.loads(json.dumps(self.state))
        snap["bytes_total"] = sum(s["size"] for s in snap["steps"])
        snap["bytes_done"] = sum(s.get("done", 0) for s in snap["steps"])
        return snap

    def stop(self):
        with self.lock:
            if self.running():
                self._stop.set()
                self.state["state"] = "stopping"
        return self.snapshot()

    def join(self, timeout=None):
        if self._thread is not None:
            self._thread.join(timeout)

    # -- the work --
    def _set(self, step, **changes):
        with self.lock:
            step.update(changes)

    def _run(self, place):
        changed, failure = False, None
        self._samples = []
        try:
            for step in self.state["steps"]:
                if self._stop.is_set():
                    raise Stopped()
                self._set(step, state="working")
                if step["action"] == "node":
                    wrote = install_node(step["where"], step["key"])
                    self._set(step, state="done", message="copied" if wrote else "already there")
                    with self.lock:
                        self.state["restart_comfy"] = self.state["restart_comfy"] or wrote
                    changed = changed or wrote
                elif step["action"] == "check":
                    self._check(step)
                else:
                    self._download(step)
                    changed = True
        except Stopped:
            failure = ("stopped", "Stopped. What arrived is kept beside each file: Install carries on from there.")
        except SetupError as error:
            failure = ("failed", str(error))
        except Exception as error:                        # never leave the dialog saying "running"
            failure = ("failed", f"The install stopped with an error ({type(error).__name__}: {error}). Press Install to try again.")
        finally:
            with self.lock:
                for step in self.state["steps"]:
                    if step["state"] == "working":
                        step["state"] = "stopped" if failure and failure[0] == "stopped" else "failed"
                if failure:
                    self.state.update(state=failure[0], message=failure[1])
                else:
                    restart = self.state["restart_comfy"]
                    self.state.update(state="done", message="Installed. " + (
                        "Restart ComfyUI once to load NPC Forge's nodes." if restart else "ComfyUI can use the files now."))
                self.state.update(finished=time.time(), speed=None, eta=None)
            if changed and self.finished:
                try:
                    self.finished()
                except Exception:
                    pass

    def _progress(self, step, done):
        now = time.monotonic()
        with self.lock:
            step["done"] = done
            self._samples.append((now, sum(s.get("done", 0) for s in self.state["steps"])))
            self._samples = [s for s in self._samples if now - s[0] <= 8] or self._samples[-1:]
            first, last = self._samples[0], self._samples[-1]
            speed = (last[1] - first[1]) / (last[0] - first[0]) if last[0] - first[0] >= 1 else None
            left = sum(s["size"] - s.get("done", 0) for s in self.state["steps"] if s["action"] == "download")
            self.state["speed"] = speed
            self.state["eta"] = left / speed if speed else None

    def _hash_file(self, path, step, start=0):
        digest = hashlib.sha256()
        done = start
        with open(path, "rb") as source:
            while True:
                if self._stop.is_set():
                    raise Stopped()
                block = source.read(CHUNK * 8)
                if not block:
                    break
                digest.update(block)
                done += len(block)
                self._progress(step, done if step["action"] == "check" else 0)
        return digest

    def _check(self, step):
        entry = pin(step["folder"], step["name"])
        path = os.path.join(step["where"], step["name"])
        self._set(step, message="checking")
        if self._hash_file(path, step).hexdigest() == entry["sha256"]:
            self._set(step, state="done", message="there already, checked")
            return
        # the pinned size but not the pinned file: a damaged copy. The pinned one is fetched beside it and replaces it whole.
        self._set(step, action="download", done=0, kept=0, message="damaged: getting the pinned file")
        self._download(step, replacing=True)

    def _download(self, step, replacing=False):
        entry = pin(step["folder"], step["name"])
        os.makedirs(step["where"], exist_ok=True)
        final = os.path.join(step["where"], step["name"])
        part_path = final + PART
        size = entry["size"]
        have = os.path.getsize(part_path) if os.path.exists(part_path) else 0
        if have > size:
            os.remove(part_path)
            have = 0
        digest = hashlib.sha256()
        if have:
            self._set(step, message="checking what was kept")
            digest = self._hash_file(part_path, step)
        self._set(step, message="downloading", done=have)
        with self.lock:
            self._samples = []                            # what was kept is not speed: measure from here
        self._progress(step, have)
        broke = 0
        while have < size:
            if self._stop.is_set():
                raise Stopped()
            headers = {"User-Agent": USER_AGENT, **({"Range": f"bytes={have}-"} if have else {})}
            try:
                with self.opener.open(urllib.request.Request(_url(entry), headers=headers), timeout=TIMEOUT) as answer:
                    if have and getattr(answer, "status", 200) != 206:     # the server sent the whole file: start again
                        have, digest = 0, hashlib.sha256()
                    with open(part_path, "ab" if have else "wb") as out:
                        while have < size:
                            if self._stop.is_set():
                                raise Stopped()
                            block = answer.read(CHUNK)
                            if not block:
                                break
                            try:
                                out.write(block)
                            except OSError as error:
                                raise SetupError(f"NPC Forge could not write {part_path} ({error.strerror or error}). Check the "
                                                 "free space on that drive, then press Install to carry on.") from None
                            digest.update(block)
                            have += len(block)
                            self._progress(step, have)
                if have < size:
                    raise ConnectionError("the download ended early")
            except urllib.error.HTTPError as error:
                if error.code == 416:                     # what was kept is no use to the server: start again
                    if os.path.exists(part_path):
                        os.remove(part_path)
                    have, digest = 0, hashlib.sha256()
                    broke += 1
                elif error.code in (401, 403, 404, 410):
                    raise SetupError(f"Hugging Face refused {entry['repo']}/{entry['path']} (HTTP {error.code}). It may have "
                                     "moved: check for an NPC Forge update.") from None
                else:
                    broke += 1
                if broke > RETRIES:
                    raise SetupError(f"Hugging Face kept answering HTTP {error.code} for {step['name']}. Press Install to try "
                                     f"again later; the {_gb(have)} that arrived is kept.") from None
            except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as error:
                broke += 1
                if broke > RETRIES:
                    why = getattr(error, "reason", None) or error
                    raise SetupError(f"The download of {step['name']} broke off {broke} times ({why}). Check the internet "
                                     f"connection and press Install: it carries on from the {_gb(have)} that arrived.") from None
            if have < size and broke:
                self._set(step, message=f"broke off, trying again ({broke} of {RETRIES})")
                if self.wait(min(30, 2 ** broke)):
                    raise Stopped()
                self._set(step, message="downloading")
        if have != size or digest.hexdigest() != entry["sha256"]:
            os.remove(part_path)
            raise SetupError(f"{step['name']} did not match its pinned SHA-256, so it was deleted. Press Install to download it "
                             "again; if it fails twice, check for an NPC Forge update.")
        try:
            os.replace(part_path, final)
        except OSError as error:
            raise SetupError(f"{step['name']} arrived whole but could not be put in place ({error.strerror or error}): "
                             f"{'ComfyUI may have the old copy open. Restart ComfyUI and ' if replacing else ''}press Install "
                             "to try again (the download is kept).") from None
        self._set(step, state="done", done=size, message="replaced the damaged copy" if replacing else "downloaded and checked")
