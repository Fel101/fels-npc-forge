"""NPC Forge as an MCP server, for Claude Code, Codex, opencode and any other MCP client.

    python -m npcgen.mcp                      # discovers the running NPC Forge
    python -m npcgen.mcp --url http://127.0.0.1:8775

NPC Forge must be running (start.bat): every tool is a call to its HTTP API, so the work goes through the same queue and the
same one-model-at-a-time GPU guard as the page, and shows in the page's queue. The tools make characters and their pictures,
wait for the queue, read characters back and export them for a game (docs/agents.md says how to add the server to each client).

Optional settings that require personal confirmation stay in the app. NPCGEN_AUTH_TOKEN is sent when NPC Forge asks for it (HTTP Basic, user
npcforge). Standard library only; the protocol is JSON-RPC 2.0, one message per line on stdin and stdout (logs go to stderr).
"""
import argparse
import base64
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from functools import partial
from pathlib import Path

try:
    from . import __version__, config, fels_suite
    from .forge_endpoint import ask_identity, local_url
except ImportError:                                 # run as a file (python C:\...\npcgen\mcp.py), as an MCP client's config does
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from npcgen import __version__, config, fels_suite
    from npcgen.forge_endpoint import ask_identity, local_url

DEFAULT_URL = local_url("127.0.0.1", config.DEFAULT_PORT)
PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")
ADULT_SETTINGS = {"models"}                       # the add-on setting requires personal confirmation
WAIT_LIMIT = 900
POLL_SECONDS = 5
ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,120}$")
ASSETS = ("portrait", "sprite", "expressions", "sheet", "walk_cycle", "model_3d", "best_3d", "living")
FORMATS = ("zip", "sheet", "godot", "unity", "aseprite", "atlas", "grid4", "dialogue_json", "dialogue_ink", "dialogue_yarn")

INSTRUCTIONS = (
    "NPC Forge makes game characters on this PC with a local ComfyUI. Start with forge_status (is it ready, what is missing) and "
    "forge_options (the settings, styles, ages, expressions and export formats it knows). make_characters rolls or describes "
    "characters and queues their pictures; pictures take about 30 s each on the GPU and run one at a time, so call "
    "wait_for_jobs afterwards. Then get_character or export_characters (ZIPs for Godot, Unity, Aseprite or a plain atlas, and "
    "dialogue). Use export_card for a CHARX or PNG character card for chat apps. Nothing here makes adult content.")


class ToolError(Exception):
    """A tool that could not do what it was asked; the message is for the agent, and usually for the person."""


# ---------------------------------------------------------------- talking to NPC Forge

class Forge:
    def __init__(self, url=DEFAULT_URL, token=None, opener=None):
        self.url = url.rstrip("/")
        self.token = token
        self.opener = opener or urllib.request.build_opener()

    def request(self, method, path, body=None, *, raw=False, timeout=60):
        if path.startswith("/api/ext/") or path.startswith("/ext/"):
            raise ToolError("The extensions' routes are not offered through MCP.")
        data = None
        headers = {"Accept": "application/json"}
        if method != "GET":
            data = json.dumps(body if body is not None else {}).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if self.token:
            headers["Authorization"] = "Basic " + base64.b64encode(f"npcforge:{self.token}".encode()).decode("ascii")
        req = urllib.request.Request(self.url + path, data=data, method=method, headers=headers)
        try:
            with self.opener.open(req, timeout=timeout) as response:
                payload = response.read()
        except urllib.error.HTTPError as exc:
            try:
                message = json.loads(exc.read().decode("utf-8")).get("error")
            except (ValueError, AttributeError, UnicodeDecodeError):
                message = None
            raise ToolError(message or f"NPC Forge answered {exc.code} {exc.reason} for {method} {path}.") from None
        except (urllib.error.URLError, ConnectionError, TimeoutError) as exc:
            raise ToolError(f"NPC Forge is not answering at {self.url} ({getattr(exc, 'reason', exc)}). Start it with start.bat "
                            "(and ComfyUI), or pass --url if it runs on another port.") from None
        if raw:
            return payload
        try:
            return json.loads(payload.decode("utf-8")) if payload else {}
        except (ValueError, UnicodeDecodeError):
            raise ToolError(f"NPC Forge's answer to {method} {path} could not be read.") from None


def connect(given=None, token=None):
    found = fels_suite.find_app(config.APP_ID, partial(ask_identity, token=token), given=given)
    if not found["url"]:
        raise ToolError(" ".join(found["notes"]) + " Start it with start.bat or python server.py. "
                        "If it asks for a password, set NPCGEN_AUTH_TOKEN for the MCP server.")
    for note in found["notes"]:
        print(note, file=sys.stderr)
    return Forge(found["url"], token=token)


# ---------------------------------------------------------------- the tools

def _ids(value, name="npc_ids"):
    ids = value if isinstance(value, list) else [value] if isinstance(value, str) else []
    ids = [i for i in ids if isinstance(i, str) and ID.match(i)]
    if not ids:
        raise ToolError(f"{name} must name at least one character id (from make_characters or list_characters).")
    return list(dict.fromkeys(ids))[:200]


def _setting(forge, setting):
    if setting in ADULT_SETTINGS:
        raise ToolError("That setting is for the adult features, which are not offered through MCP.")
    known = forge.request("GET", "/api/config").get("settings") or {}
    if setting not in known:
        raise ToolError(f"Unknown setting {setting!r}. forge_options lists them: {', '.join(s for s in known if s not in ADULT_SETTINGS)}.")
    return setting


def _assets(make, config):
    """forge's assets request from the tool's simple `make` object."""
    make = make or {}
    if not isinstance(make, dict):
        raise ToolError("make must be an object, for example {\"portrait\": true, \"sprite\": true}.")
    unknown = sorted(set(make) - set(ASSETS))
    if unknown:
        raise ToolError(f"make does not know {', '.join(unknown)}; it takes {', '.join(ASSETS)}.")
    assets = {"portrait": bool(make.get("portrait")), "sprite": bool(make.get("sprite"))}
    expressions = make.get("expressions")
    if expressions is True:
        expressions = [e["id"] for e in config.get("expressions", []) if e.get("default")]
    if expressions:
        assets["expressions"] = [e for e in expressions if isinstance(e, str)]
    if make.get("sheet"):
        assets["sheet"] = {"views": [v["id"] for v in (config.get("sheet") or {}).get("views", [])]}
    if make.get("walk_cycle"):
        assets["animations"] = {"clips": ["walk"]}
    for key in ("model_3d", "best_3d", "living"):
        if make.get(key):
            assets[key] = True
    if not any(v for v in assets.values()):
        raise ToolError("make asks for nothing: tick at least one of " + ", ".join(ASSETS) + ".")
    return assets


def _options(args):
    options = {}
    if args.get("quality"):
        options["quality"] = args["quality"]
    for key in ("image_model", "edit_model"):
        if args.get(key):
            options[key] = args[key]
    return options


def forge_status(forge, args):
    health = forge.request("GET", "/api/health")
    config = forge.request("GET", "/api/config")
    jobs = forge.request("GET", "/api/jobs")
    active = [j for j in jobs if j.get("status") in ("queued", "running")]
    features = {k: v for k, v in health.items() if k not in ("online", "version", "gpu", "missing", "engines_missing", "voice", "gpu_rule", "fels")}
    return {"version": config.get("version"), "url": forge.url, "comfyui_online": health.get("online"), "comfyui_version": health.get("version"),
            "gpu": health.get("gpu"), "missing_for_pictures": health.get("missing"), "engines_missing": health.get("engines_missing"),
            "features": features, "queue": {"waiting_or_running": len(active), "jobs": len(jobs)},
            "gpu_lock": forge.request("GET", "/api/gpu?lock=1")}


def forge_options(forge, args):
    config = forge.request("GET", "/api/config")
    settings = {k: {"label": v.get("label"), "blurb": v.get("blurb")} for k, v in (config.get("settings") or {}).items() if k not in ADULT_SETTINGS}
    return {
        "settings": settings,
        "styles": {k: v.get("label") for k, v in (config.get("styles") or {}).items()},
        "genders": [g["id"] for g in config.get("genders", [])],
        "age_groups": [g["id"] for g in config.get("age_groups", [])],
        "expressions": [e["id"] for e in config.get("expressions", [])],
        "qualities": list((config.get("quality") or {}).keys()),
        "engines": {kind: [e["id"] for e in rows] for kind, rows in (config.get("engines") or {}).items()},
        "make": list(ASSETS),
        "export_formats": list(FORMATS),
        "defaults": {"setting": (config.get("defaults") or {}).get("setting"), "style": (config.get("defaults") or {}).get("style")},
        "constraints": ["gender", "age_group", "occupation", "origin", "hair_color", "hair_style"],
    }


def make_characters(forge, args):
    config = forge.request("GET", "/api/config")
    setting = _setting(forge, args.get("setting") or (config.get("defaults") or {}).get("setting") or "modern")
    count = max(1, min(int(args.get("count") or 1), 50))
    style = args.get("style") or None
    constraints = {k: v for k, v in (args.get("constraints") or {}).items() if isinstance(v, str) and v}
    make = args.get("make")
    assets = _assets(make, config) if make else None
    description = str(args.get("description") or "").strip()
    if description:                                                 # described: roll each from the text, save, then draw
        ids, jobs = [], []
        for _ in range(count):
            draft = forge.request("POST", "/api/roll", {"setting": setting, "style": style, "constraints": constraints, "freeform": description[:1000]})
            saved = forge.request("POST", "/api/npcs", draft)
            ids.append(saved["id"])
        if assets:
            answer = forge.request("POST", "/api/generate", {"npc_ids": ids, "assets": assets, "options": _options(args)})
            jobs = [j.get("id") for j in answer.get("jobs", [])]
        return {"npc_ids": ids, "job_ids": jobs, "next": "wait_for_jobs" if jobs else "get_character"}
    if not assets:                                                  # rolled, nothing drawn
        ids = []
        for _ in range(count):
            draft = forge.request("POST", "/api/roll", {"setting": setting, "style": style, "constraints": constraints})
            ids.append(forge.request("POST", "/api/npcs", draft)["id"])
        return {"npc_ids": ids, "job_ids": [], "next": "get_character, or generate_assets to draw them"}
    body = {"setting": setting, "count": count, "constraints": constraints, "assets": assets, "options": _options(args)}
    if style:
        body["style"] = style
    answer = forge.request("POST", "/api/generate", body)
    return {"npc_ids": answer.get("npc_ids", []), "job_ids": [j.get("id") for j in answer.get("jobs", [])],
            "warnings": answer.get("warnings", []), "next": "wait_for_jobs"}


def generate_assets(forge, args):
    config = forge.request("GET", "/api/config")
    body = {"npc_ids": _ids(args.get("npc_ids")), "assets": _assets(args.get("make"), config), "options": _options(args)}
    if args.get("style"):
        body["style"] = args["style"]
    answer = forge.request("POST", "/api/generate", body)
    return {"npc_ids": answer.get("npc_ids", []), "job_ids": [j.get("id") for j in answer.get("jobs", [])], "next": "wait_for_jobs"}


def _job_row(job):
    failed = next((s for s in job.get("steps") or [] if s.get("status") == "error"), None)
    return {"id": job.get("id"), "npc_id": job.get("npc_id"), "name": job.get("npc_name"), "status": job.get("status"),
            "code": job.get("code") or (failed or {}).get("code"), "error": job.get("error") or (failed or {}).get("error"),
            "steps": f"{sum(1 for s in job.get('steps') or [] if s.get('status') == 'done')}/{len(job.get('steps') or [])}"}


def wait_for_jobs(forge, args, *, sleep=time.sleep, clock=time.monotonic):
    wanted = set(args.get("job_ids") or [])
    limit = max(5, min(int(args.get("timeout_seconds") or 300), WAIT_LIMIT))
    deadline = clock() + limit
    while True:
        jobs = [j for j in forge.request("GET", "/api/jobs") if not wanted or j.get("id") in wanted]
        open_jobs = [j for j in jobs if j.get("status") in ("queued", "running")]
        if not open_jobs or clock() >= deadline:
            break
        sleep(POLL_SECONDS)
    rows = [_job_row(j) for j in jobs]
    missing = sorted(wanted - {r["id"] for r in rows})
    return {"finished": not open_jobs, "jobs": rows, "unknown_job_ids": missing,
            "note": "" if not open_jobs else f"Still working after {limit} s: call wait_for_jobs again (pictures run one at a time)."}


def list_characters(forge, args):
    rows = forge.request("GET", "/api/npcs")
    rows = rows if isinstance(rows, list) else rows.get("npcs", [])
    setting, search = args.get("setting"), str(args.get("search") or "").lower()
    out = []
    for npc in rows:
        if npc.get("setting") in ADULT_SETTINGS or (setting and npc.get("setting") != setting):
            continue
        has = npc.get("has") or {}
        row = {"id": npc.get("id"), "name": npc.get("name"), "setting": npc.get("setting"), "occupation": npc.get("occupation"),
               "age": npc.get("age"), "gender": npc.get("gender"), "style": npc.get("style"),
               "has": sorted(k for k, v in has.items() if v), "commercial_use": npc.get("commercial")}
        if search and search not in json.dumps(row).lower():
            continue
        out.append(row)
    limit = max(1, min(int(args.get("limit") or 50), 500))
    return {"count": len(out), "characters": out[:limit]}


def get_character(forge, args):
    npc_id = _ids(args.get("npc_id"), "npc_id")[0]
    npc = forge.request("GET", f"/api/npcs/{npc_id}")
    if npc.get("setting") in ADULT_SETTINGS:
        raise ToolError("That character belongs to the adult features, which are not offered through MCP.")
    files = {}
    for key, value in (npc.get("assets") or {}).items():
        if isinstance(value, dict) and value.get("file"):
            files[key] = f"{forge.url}/api/npcs/{npc_id}/files/{value['file']}"
        elif isinstance(value, dict):
            files.update({f"{key}.{k}": f"{forge.url}/api/npcs/{npc_id}/files/{v['file']}" for k, v in value.items()
                          if isinstance(v, dict) and v.get("file")})
    keep = ("id", "setting", "style", "profile", "personality", "appearance", "bio", "dialogue")
    return {**{k: npc[k] for k in keep if k in npc}, "files": files}


def export_characters(forge, args):
    ids = _ids(args.get("npc_ids"))
    fmt = args.get("format") or "zip"
    if fmt not in FORMATS:
        raise ToolError(f"format must be one of {', '.join(FORMATS)}.")
    folder = Path(args.get("output_dir") or ".").expanduser().resolve()
    folder.mkdir(parents=True, exist_ok=True)
    written = []

    def save(name, data):
        path = folder / name
        path.write_bytes(data)
        written.append(str(path))

    for npc_id in ids:
        npc = forge.request("GET", f"/api/npcs/{npc_id}")
        if npc.get("setting") in ADULT_SETTINGS:
            raise ToolError(f"{npc_id} belongs to the adult features, which are not offered through MCP.")
    if fmt == "zip":
        save("npcs.zip", forge.request("GET", "/api/export.zip?ids=" + urllib.parse.quote(",".join(ids)), raw=True))
    for npc_id in ids if fmt != "zip" else []:
        if fmt == "sheet":
            save(f"{npc_id}-sheet.zip", forge.request("GET", f"/api/npcs/{npc_id}/sheet.zip", raw=True))
        elif fmt.startswith("dialogue_"):
            kind = fmt.split("_", 1)[1]
            save(f"{npc_id}-dialogue.{ 'json' if kind == 'json' else kind}", forge.request("GET", f"/api/npcs/{npc_id}/dialogue/export/{kind}", raw=True))
        else:
            clip = args.get("clip") or "walk"
            if not re.fullmatch(r"[a-z0-9_]+", clip):
                raise ToolError("clip must be an animation id such as walk.")
            query = f"?frame={int(args['frame'])}" if args.get("frame") else ""
            save(f"{npc_id}-{clip}-{fmt}.zip", forge.request("GET", f"/api/npcs/{npc_id}/export/{clip}/{fmt}.zip{query}", raw=True))
    return {"files": written}


def export_card(forge, args):
    npc_id = args.get("npc_id")
    if not isinstance(npc_id, str) or not ID.fullmatch(npc_id):
        raise ToolError("Use a saved character id from list_characters.")
    form = args.get("format", "charx")
    if form not in ("charx", "png"):
        raise ToolError("Choose charx or png for the card format.")
    folder = Path(args.get("output_dir") or ".").expanduser().resolve()
    path = folder / f"{npc_id}.{form}"
    query = "?voice=0" if args.get("include_voice") is False else ""
    data = forge.request("GET", f"/api/npcs/{npc_id}/card.{form}{query}", raw=True, timeout=2400 if form == "charx" else 60)
    folder.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return {"files": [str(path)]}


def make_item(forge, args):
    config = forge.request("GET", "/api/config")
    families = {f["id"]: f for f in (config.get("items") or {}).get("families", [])}
    family = args.get("family") or "weapon"
    if family not in families or families[family].get("maker") != "photo":
        made = sorted(k for k, f in families.items() if f.get("maker") == "photo")
        raise ToolError(f"family must be one of {', '.join(made)} (the others are designed in the Items tab).")
    fields = {"family": family, "kind": args.get("kind") or None, "name": args.get("name") or "", "description": args.get("description") or "",
              "setting": "" if args.get("setting") in ADULT_SETTINGS else (args.get("setting") or "")}
    if args.get("size_m"):
        fields["size_m"] = args["size_m"]
    item = forge.request("POST", "/api/items", {k: v for k, v in fields.items() if v not in (None,)})
    answer = forge.request("POST", f"/api/items/{item['id']}/make", {"request": {"make": True, "finish": args.get("finish", True) is not False},
                                                                      "options": {}})
    job = answer.get("job") or {}
    return {"item_id": item["id"], "job_ids": [job.get("id")] if job.get("id") else [], "next": "wait_for_jobs (an item takes about 15-20 minutes)"}


def cancel_jobs(forge, args):
    ids = args.get("job_ids")
    if ids:
        for job_id in ids:
            if isinstance(job_id, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", job_id):
                forge.request("POST", f"/api/jobs/{job_id}/cancel")
        return {"cancelled": ids}
    if args.get("all") is True:
        forge.request("POST", "/api/jobs/cancel-all")
        return {"cancelled": "all"}
    raise ToolError("Name job_ids, or pass all: true to stop every waiting and running job.")


def _schema(properties, required=()):
    return {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}


MAKE = {"type": "object", "description": "What to draw: portrait, sprite (transparent full body), expressions (true for the usual four, or a list), "
        "sheet (model sheet), walk_cycle (4-direction walk), model_3d, best_3d (the best 3D chain, slow), living (living portrait). "
        "Each picture is about 30 s on the GPU; 3D takes 5-30 minutes.",
        "properties": {"portrait": {"type": "boolean"}, "sprite": {"type": "boolean"},
                       "expressions": {"oneOf": [{"type": "boolean"}, {"type": "array", "items": {"type": "string"}}]},
                       "sheet": {"type": "boolean"}, "walk_cycle": {"type": "boolean"}, "model_3d": {"type": "boolean"},
                       "best_3d": {"type": "boolean"}, "living": {"type": "boolean"}}, "additionalProperties": False}
QUALITY = {"type": "string", "description": "draft, standard or high (forge_options lists them)."}
IDS = {"type": "array", "items": {"type": "string"}, "description": "Character ids."}

TOOLS = [
    ("forge_status", "Is NPC Forge ready: its version, whether ComfyUI answers, which model files each feature lacks, the queue, and the GPU: the suite lock and One model at a time (on: pictures first unload Voice Lab, Altera, LM Studio and Ollama).",
     _schema({}), forge_status),
    ("forge_options", "What NPC Forge knows: settings, art styles, genders, age groups, expressions, qualities, engines, export formats.",
     _schema({}), forge_options),
    ("make_characters", "Make 1-50 characters for a game: rolled from a setting (narrowed by constraints) or from a description, and "
     "queue their pictures. Returns their ids and the jobs to wait for.",
     _schema({"setting": {"type": "string", "description": "A setting id from forge_options, e.g. fantasy, scifi, western."},
              "count": {"type": "integer", "minimum": 1, "maximum": 50},
              "description": {"type": "string", "description": "Optional: who to make, in words ('a grumpy dwarf blacksmith with a braided beard')."},
              "style": {"type": "string", "description": "An art style id from forge_options (pixel, semi_real, ...)."},
              "constraints": {"type": "object", "description": "Optional narrowing: gender, age_group, occupation, origin, hair_color, hair_style (ids).",
                              "additionalProperties": {"type": "string"}},
              "make": MAKE, "quality": QUALITY,
              "image_model": {"type": "string"}, "edit_model": {"type": "string"}}), make_characters),
    ("generate_assets", "Queue more pictures (or 3D, a walk cycle, a model sheet) for characters that already exist.",
     _schema({"npc_ids": IDS, "make": MAKE, "style": {"type": "string"}, "quality": QUALITY,
              "image_model": {"type": "string"}, "edit_model": {"type": "string"}}, ["npc_ids", "make"]), generate_assets),
    ("wait_for_jobs", "Wait until the named jobs (or the whole queue) finish, up to timeout_seconds (default 300, at most 900). Says each "
     "job's status, and the reason and code of any that failed.",
     _schema({"job_ids": {"type": "array", "items": {"type": "string"}}, "timeout_seconds": {"type": "integer", "minimum": 5, "maximum": WAIT_LIMIT}}),
     wait_for_jobs),
    ("list_characters", "The characters in the library (id, name, setting, occupation, age, what pictures they have).",
     _schema({"setting": {"type": "string"},
              "search": {"type": "string", "description": "Words found in the name, occupation, setting, age or gender (a described "
                         "character keeps its description on its sheet, not here: use get_character)."},
              "limit": {"type": "integer", "minimum": 1, "maximum": 500}}),
     list_characters),
    ("get_character", "One character: profile, personality, looks, bio, dialogue, and the address of every picture and model file.",
     _schema({"npc_id": {"type": "string"}}, ["npc_id"]), get_character),
    ("export_characters", "Save characters to a folder for a game: zip (everything), sheet (model sheet), godot / unity / aseprite / atlas / "
     "grid4 (a finished animation, clip walk by default), or dialogue_json / dialogue_ink / dialogue_yarn.",
     _schema({"npc_ids": IDS, "format": {"type": "string", "enum": list(FORMATS)},
              "output_dir": {"type": "string", "description": "Where to write the files (made if missing)."},
              "clip": {"type": "string"}, "frame": {"type": "integer"}}, ["npc_ids", "output_dir"]), export_characters),
    ("export_card", "Save a character card. PNG contains the portrait and character text. CHARX also carries the available art "
     "and designed Voice Lab voice. Ask the person to use Forge's export dialog if the card needs a recorded voice.",
     _schema({"npc_id": {"type": "string"}, "format": {"type": "string", "enum": ["charx", "png"]},
              "output_dir": {"type": "string"}, "include_voice": {"type": "boolean"}}, ["npc_id", "output_dir"]), export_card),
    ("make_item", "Make a game-ready 3D item (weapon, armour, hat, shoes, bag or prop) from a description: about 15-20 minutes.",
     _schema({"family": {"type": "string"}, "kind": {"type": "string"}, "name": {"type": "string"}, "description": {"type": "string"},
              "setting": {"type": "string"}, "size_m": {"type": "number"}, "finish": {"type": "boolean"}}), make_item),
    ("cancel_jobs", "Stop the named jobs, or every waiting and running job with all: true.",
     _schema({"job_ids": {"type": "array", "items": {"type": "string"}}, "all": {"type": "boolean"}}), cancel_jobs),
]
HANDLERS = {name: fn for name, _, _, fn in TOOLS}


# ---------------------------------------------------------------- the protocol

def handle(message, forge):
    """Handle a JSON-RPC message. Resolve a Forge connection only when a tool needs it."""
    if not isinstance(message, dict):
        return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid request"}}
    method, mid, params = message.get("method"), message.get("id"), message.get("params") or {}
    if "id" not in message:                                         # a notification: nothing is answered
        return None
    if method == "initialize":
        asked = params.get("protocolVersion")
        result = {"protocolVersion": asked if asked in PROTOCOLS else PROTOCOLS[0], "capabilities": {"tools": {"listChanged": False}},
                  "serverInfo": {"name": "npc-forge", "title": "NPC Forge", "version": __version__}, "instructions": INSTRUCTIONS}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": [{"name": name, "description": text, "inputSchema": schema} for name, text, schema, _ in TOOLS]}
    elif method == "tools/call":
        name, arguments = params.get("name"), params.get("arguments") or {}
        if name not in HANDLERS:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": f"Unknown tool: {name}"}}
        try:
            data = HANDLERS[name](forge() if callable(forge) else forge, arguments)
            result = {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False)}], "structuredContent": data, "isError": False}
        except ToolError as exc:
            result = {"content": [{"type": "text", "text": str(exc)}], "isError": True}
        except (KeyError, TypeError, ValueError) as exc:
            result = {"content": [{"type": "text", "text": f"{name} could not use its arguments: {exc}"}], "isError": True}
    else:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"Method not found: {method}"}}
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def serve(forge, stdin=None, stdout=None):
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            answer = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
        else:
            answer = [a for a in (handle(m, forge) for m in message) if a] if isinstance(message, list) else handle(message, forge)
        if answer:
            stdout.write(json.dumps(answer, ensure_ascii=False) + "\n")
            stdout.flush()


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m npcgen.mcp", description="NPC Forge as an MCP server (stdio).")
    parser.add_argument("--url", help=f"NPC Forge's address. Otherwise, discover it (usual address {DEFAULT_URL}).")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", newline="\n")
        sys.stdin.reconfigure(encoding="utf-8")
    print(f"NPC Forge MCP server {__version__}", file=sys.stderr)
    serve(partial(connect, given=args.url, token=os.environ.get("NPCGEN_AUTH_TOKEN")))


if __name__ == "__main__":
    main()
