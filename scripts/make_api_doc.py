"""Write docs/api.md: every HTTP route server.py serves, grouped by what it works on, from the @route table.

    python scripts/make_api_doc.py            # writes docs/api.md
    python scripts/make_api_doc.py --check    # exits 1 when docs/api.md is out of date (tests/test_api_doc.py runs this)

Nothing is imported or started: server.py is only read.
"""
import argparse
import ast
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SERVER = ROOT / "server.py"
DOC = ROOT / "docs" / "api.md"

INTRO = """# The HTTP API

Every route `server.py` serves, written by `scripts/make_api_doc.py` from the server's own route table (run it again after
adding a route; a test fails while this page is out of date). The page itself uses exactly these routes, so the quickest way
to see a request's body is to do the thing in the page with the browser's network panel open.

The rules every request follows:

- The server listens on `http://127.0.0.1:8766` by default. A request whose `Host` is not a loopback name is refused, and so is
  one whose `Origin` header names another site. A script that sends no `Origin` is accepted.
- `POST`, `PUT` and `DELETE` bodies are JSON (`Content-Type: application/json`), at most 2 MB; the two uploads below
  (a model's `.glb`, a scene's frames) are the exceptions and say their own type.
- Errors come back as `{"error": "what went wrong"}` with a 4xx or 5xx status. A queued job and each of its steps carry a
  `code` when they fail ([game-clients.md](game-clients.md) lists them).
- `GET /api/config` → `api_version` is raised whenever a field the page needs is added; `version` is NPC Forge's own version.

How to drive it from a game's own scripts, with the options that matter for a bulk run: [game-clients.md](game-clients.md).

`GET /api/gpu` includes `suite_lock` alongside its picture-job readiness and per-process memory readings.
`GET /api/gpu?lock=1` returns just `suite_lock` without contacting ComfyUI. `suite_lock.holders` lists the apps holding the
lock, with `app`, `name`, `pid`, `what`, `since` and `alone`. `suite_lock.alone` gives the request for exclusive use, with the
same identity and timing fields, or `null`.

## Changes

- **API 47.** Voice Lab provides new speech through `GET /api/voice/lab` and the character's `/voice/lab` routes.
  Forge's local speech workers, model discovery and Drama studio routes are retired. Old voice records and packs remain readable.
- **API 46.** Optional body build controls are served by their local extension. Existing characters keep their saved settings.
- **API 45.** `GET /api/health` adds a `fels` object with `contract`, `app`, `version` and `api`. Use
  `GET /api/health?identity=1` to read just that object without waiting for the ComfyUI and tool checks.

"""

GROUPS = [  # (title, path prefixes after /api/), first match wins
    ("Server, settings and the queue", ("health", "config", "gpu", "jobs", "licences", "choices", "manner")),
    ("Cast packs", ("cast-packs",)),
    ("Characters", ("roll", "preview", "cast", "npcs", "trash", "generate", "describe", "photo", "export.zip")),
    ("Items", ("items", "glasses", "clothing", "equip")),
    ("3D import", ("import-3d",)),
    ("Creatures", ("creatures", "creature-projects")),
    ("Voices", ("voice", "voices", "drama")),
    ("Scenes", ("scenes", "scene")),
]


def routes(source=None):
    """[(method, pattern, handler name, first docstring sentence)] in the order server.py declares them."""
    tree = ast.parse(source if source is not None else SERVER.read_text("utf-8"))
    consts = {target.id: node.value.value for node in tree.body if isinstance(node, ast.Assign)
              and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)
              for target in node.targets if isinstance(target, ast.Name)}

    def text(arg):                          # a plain string, or an f-string made of the module's own string constants
        if isinstance(arg, ast.JoinedStr):
            return "".join(v.value if isinstance(v, ast.Constant) else consts[v.value.id] for v in arg.values)
        return ast.literal_eval(arg)
    found = []
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        for deco in node.decorator_list:
            if isinstance(deco, ast.Call) and getattr(deco.func, "id", "") == "route" and len(deco.args) == 2:
                method, pattern = (text(arg) for arg in deco.args)
                doc = (ast.get_docstring(node) or "").strip()
                first = re.split(r"(?<=[.!?])\s", " ".join(doc.split()), maxsplit=1)[0] if doc else ""
                found.append((method, pattern, node.name, first))
    return found


# What a placeholder is called after the path word before it (otherwise that word without its plural s).
NAMES = {"npcs": "npc", "cast-packs": "pack", "trash": "folder", "dress": "npc", "worn": "npc", "fits": "npc", "restore": "hash",
         "cases": "case", "library": "voice", "export": "preset", "files": "path", "creature-projects": "game", "model-history": "model",
         "rig": "picture", "equips": "equip"}


def _groups(pattern):
    """[(start, end)] of each top-level ( ... ) group in a regex, a trailing ? or + included."""
    spans, depth, start, i = [], 0, None, 0
    while i < len(pattern):
        c = pattern[i]
        if c == "\\":
            i += 2
            continue
        if c == "(":
            depth += 1
            start = i if depth == 1 else start
        elif c == ")":
            depth -= 1
            if depth == 0:
                end = i + 1 + (pattern[i + 1:i + 2] in ("?", "+", "*"))
                spans.append((start, end))
        i += 1
    return spans


def readable(pattern):
    """The pattern with each regex group as a named placeholder: /api/npcs/([a-z0-9-]+)/files/(.+) -> /api/npcs/{npc}/files/{path}."""
    out, at = [], 0
    for start, end in _groups(pattern):
        before = pattern[at:start]
        out.append(before)
        inner = pattern[start + 1:pattern.rindex(")", start, end)]
        word = "".join(out).rstrip("/").split("/")[-1]
        if inner.startswith("?:"):                                            # (?:/x)? : an optional part
            optional = pattern[end - 1:end] == "?"
            part = readable(inner[2:])
            out.append(f"[{part}]" if optional else part)
        elif re.fullmatch(r"[a-z0-9_]+(\|[a-z0-9_]+){1,3}", inner):            # a short choice of plain words: (portrait|sprite)
            out.append("{" + inner + "}")
        elif inner in (".+", ".*"):
            out.append("{path}")
        else:
            out.append("{" + NAMES.get(word, word[:-1] if word.endswith("s") else word or "id") + "}")
        at = end
    out.append(pattern[at:])
    return "".join(out).replace(chr(92) + ".", ".")


def group_of(pattern):
    head = pattern[len("/api/"):].split("/")[0] if pattern.startswith("/api/") else ""
    for title, prefixes in GROUPS:
        if head in prefixes or any(head.startswith(p + "-") or head == p for p in prefixes):
            return title
    return "Other" if pattern.startswith("/api/") else "Pages and files"


DESCRIBED = {
    "health": "Whether ComfyUI answers, which model files and nodes each feature lacks, the GPU and the voice engines.",
    "config": "Everything the page needs to draw itself: settings, styles, engines, qualities, licences, defaults, `version`.",
    "api_roll": "Roll a character: body {setting, locks, seed, ...}; nothing is saved.",
    "api_preview": "The prompts a character would send, without drawing anything.",
    "list_jobs": "The queue: every job with its steps, status and `code`.",
    "cancel_all": "Stop every queued and running job.",
    "cancel_job": "Stop one job.",
    "retry_job": "Run a failed or stopped job again from its first unfinished step.",
    "create_npc": "Save a character (a rolled one, or one written by hand).",
    "get_npc": "One character: its sheet and every asset's record.",
    "update_npc": "Change a character's sheet; what was made from it is marked out of date unless `keep_fresh` names it.",
    "delete_npc": "Move a character to the trash.",
    "generate": "Queue pictures, animations, 3D and voice for a character: body {npc_id, assets, options}.",
    "list_npcs": "The library, one row per character (filters: `project`, `test`).",
}


def humanise(name):
    words = name.replace("api_", "").replace("_", " ").strip()
    return words[:1].upper() + words[1:] + "."


def render(found=None):
    found = routes() if found is None else found
    grouped = {}
    for method, pattern, name, first in found:
        grouped.setdefault(group_of(pattern), []).append((method, pattern, name, first))
    order = [title for title, _ in GROUPS] + ["Other", "Pages and files"]
    lines = [INTRO, f"{len(found)} routes.\n"]
    for title in order:
        rows = grouped.get(title)
        if not rows:
            continue
        lines.append(f"\n## {title}\n\n| Method | Path | What it does |\n|---|---|---|")
        for method, pattern, name, first in rows:
            what = (first or DESCRIBED.get(name) or humanise(name)).replace("|", "\\|")
            lines.append(f"| `{method}` | `{readable(pattern)}` | {what} |")
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="exit 1 when docs/api.md is not what this would write")
    args = parser.parse_args(argv)
    text = render()
    if args.check:
        current = DOC.read_text("utf-8") if DOC.exists() else ""
        if current.replace("\r\n", "\n") != text:
            print("docs/api.md is out of date: run python scripts/make_api_doc.py")
            return 1
        return 0
    DOC.write_bytes(text.encode("utf-8"))
    print(f"Wrote {DOC.relative_to(ROOT)} ({len(routes())} routes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
