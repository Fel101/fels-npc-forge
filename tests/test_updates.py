"""Check for updates and update.bat (npcgen/updates.py, scripts/update.py, the /api/update routes). No network: GitHub's
answers and downloads are stand-ins, and every update runs on a folder in tmp_path."""
import importlib.util
import io
import json
import os
import subprocess
import sys
import types
import urllib.error
import zipfile
from pathlib import Path

import pytest

from npcgen import __version__, updates

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("npcforge_update_script", ROOT / "scripts" / "update.py")
upd = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(upd)


def release(tag="v9.0.0", notes="Better things."):
    return {"tag_name": tag, "name": f"NPC Forge {tag[1:]}", "html_url": f"https://github.com/{updates.REPOSITORY}/releases/tag/{tag}",
            "published_at": "2026-12-01T10:00:00Z", "body": notes}


# ---------------------------------------------------------------- asking GitHub

def test_versions_compare_as_numbers():
    assert updates.parse_version("v1.2") == (1, 2, 0) and updates.parse_version("1.10.0") > updates.parse_version("1.9.9")
    assert updates.parse_version("v1.0-beta") is None and updates.parse_version("") is None


def test_check_says_newer_same_older_and_none_published():
    newer = updates.check(current="1.0.0", fetch=lambda url: release("v1.0.1"))
    assert newer["newer"] and newer["latest"] == "1.0.1" and "1.0.1 is available" in newer["message"]
    assert not updates.check(current="1.0.1", fetch=lambda url: release("v1.0.1"))["newer"]
    assert "newer than the newest release" in updates.check(current="2.0.0", fetch=lambda url: release("v1.0.1"))["message"]

    def none(url):
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
    assert updates.check(current="1.0.0", fetch=none) == {"current": "1.0.0", "latest": None, "newer": False, "release": None,
                                                         "message": "No release of NPC Forge has been published on GitHub yet. This is version 1.0.0."}


def test_the_request_names_only_the_version_and_goes_only_to_github():
    assert updates.USER_AGENT == f"npc-forge/{__version__}"
    assert updates.check_url("https://api.github.com/repos/x/y") and updates.source_zip_url("v1.0.1").startswith("https://codeload.github.com/")
    for bad in ("http://api.github.com/x", "https://example.com/x", "file:///c:/x"):
        with pytest.raises(updates.UpdateError):
            updates.check_url(bad)


def test_a_rate_limit_is_said_plainly_and_the_last_answer_is_kept(tmp_path):
    def limited(url):
        raise urllib.error.HTTPError(url, 403, "Forbidden", {}, None)
    with pytest.raises(updates.UpdateError, match="60 times an hour"):
        updates.check(fetch=limited)
    checks = updates.UpdateChecks(tmp_path, run=lambda: updates.check(fetch=lambda url: release("v9.0.0")))
    assert checks.last() is None
    answer = checks.check_now()
    assert answer["newer"] and checks.last()["latest"] == "9.0.0" and checks.last()["checked_at"]


# ---------------------------------------------------------------- what an update may touch

def test_the_library_settings_extensions_and_environments_are_never_touched():
    for path in ("library/a/npc.json", "local/adult_studio.json", "local/extensions/x/__init__.py", "config.json", ".venv/x",
                 ".git/HEAD", "animations/clip.glb"):
        assert upd.protected(path), path
    for path in ("server.py", "npcgen/updates.py", "extensions/adult_studio/studio.py", "config.example.json", "web/app.js"):
        assert not upd.protected(path), path


def zip_of(files, top="npc-forge-9.0.0"):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in files.items():
            archive.writestr(f"{top}/{name}", data)
    return buffer.getvalue()


def test_a_release_zip_with_a_path_that_leaves_its_folder_is_refused(tmp_path):
    bad = tmp_path / "bad.zip"
    bad.write_bytes(zip_of({"../evil.py": b"x"}))
    with pytest.raises(upd.UpdateStopped, match="will not write"):
        upd.read_archive(bad)
    good = tmp_path / "good.zip"
    good.write_bytes(zip_of({"npcgen/__init__.py": b'__version__ = "9.0.0"\n'}))
    assert upd.archive_version(upd.read_archive(good)) == "9.0.0"


# ---------------------------------------------------------------- a whole update from ZIP files, and its undo

OLD = {"server.py": b"old server", "npcgen/__init__.py": f'__version__ = "{__version__}"\n'.encode(), "web/gone.js": b"old",
       "web/edited.js": b"as shipped", "requirements.txt": b"numpy\n", "library/demo/npc.json": b"{}"}
NEW = {"server.py": b"new server", "npcgen/__init__.py": b'__version__ = "9.0.0"\n', "web/new.js": b"new",
       "web/edited.js": b"new edition", "requirements.txt": b"numpy\npillow\n", "library/demo/npc.json": b'{"release": true}'}


def folder_from(tmp_path, files):
    root = tmp_path / "forge"
    for name, data in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_bytes(data)
    (root / "web" / "edited.js").write_bytes(b"changed by me")         # a shipped file the owner changed
    (root / "config.json").write_bytes(b'{"port": 9999}')
    (root / "local").mkdir()
    (root / "local" / "adult_studio.json").write_bytes(b'{"confirmed_at": "x"}')
    return root


def fake_fetch(url, destination):
    data = zip_of(NEW) if "v9.0.0" in url else zip_of(OLD, top="npc-forge-old")
    Path(destination).parent.mkdir(parents=True, exist_ok=True)
    Path(destination).write_bytes(data)
    return Path(destination)


def args(*argv):
    return upd.build_parser().parse_args(list(argv))


def test_an_update_from_zip_files_replaces_what_shipped_keeps_what_is_yours_and_undo_puts_it_back(tmp_path):
    root = folder_from(tmp_path, OLD)
    finished = []
    runner = lambda command, cwd=None, **kw: (finished.append(command), types.SimpleNamespace(returncode=0))[1]  # noqa: E731
    said = []
    status = upd.update(root, args=args("--yes"), check=lambda: updates.check(current=__version__, fetch=lambda url: release()),
                        runner=runner, processes=lambda r: [], fetch=fake_fetch, say=said.append, version=__version__)
    assert status == 0 and finished and finished[0][-1] == "--finish"
    assert (root / "server.py").read_bytes() == b"new server" and (root / "web" / "new.js").is_file() and not (root / "web" / "gone.js").exists()
    assert (root / "web" / "edited.js").read_bytes() == b"new edition"
    assert (root / "library" / "demo" / "npc.json").read_bytes() == b"{}"                     # the library is never written
    assert (root / "config.json").read_bytes() == b'{"port": 9999}' and (root / "local" / "adult_studio.json").is_file()
    record = upd.read_record(root)
    assert record["state"] == "applied" and record["yours"] == ["web/edited.js"] and "requirements.txt" in record["changed"]
    backup = root / record["backup"] / "files"
    assert (backup / "web" / "edited.js").read_bytes() == b"changed by me" and (backup / "web" / "gone.js").is_file()

    assert upd.undo(root, args=args("--undo", "--yes"), runner=runner, processes=lambda r: []) == 0
    assert (root / "server.py").read_bytes() == b"old server" and not (root / "web" / "new.js").exists()
    assert (root / "web" / "edited.js").read_bytes() == b"changed by me" and (root / "web" / "gone.js").read_bytes() == b"old"
    assert upd.read_record(root)["state"] == "undone"


def test_finish_installs_the_packages_again_when_requirements_changed_and_leaves_a_note(tmp_path):
    root = tmp_path / "forge"
    python = root / ".venv" / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"")
    upd.save_record(root, {"schema": 1, "state": "applied", "from": "1.0.0", "to": "9.0.0", "tag": "v9.0.0", "changed": ["requirements.txt"]})
    ran = []
    assert upd.finish(root, runner=lambda command, cwd=None: (ran.append(command), types.SimpleNamespace(returncode=0))[1], say=lambda t="": None) == 0
    # scripts/setup_core.py brings .venv up to requirements.txt (tests/test_setup_kit.py); finish only starts it.
    assert ran == [[sys.executable, str(root / "scripts" / "setup_core.py")]]
    notice = json.loads(upd.notice_path(root).read_text("utf-8"))
    assert notice["state"] == "updated" and notice["to"] == "9.0.0" and notice["problems"] == [] and notice["notes"] == []


def test_a_dry_run_changes_nothing_and_reports_the_plan_for_the_page(tmp_path):
    root = folder_from(tmp_path, OLD)
    report = upd.plan_report(root, args=args("--dry-run", "--json"), check=lambda: updates.check(current=__version__, fetch=lambda url: release()),
                             processes=lambda r: [], fetch=fake_fetch, version=__version__)
    assert report["ok"] and report["newer"] and report["to"] == "9.0.0" and report["method"] == "zip"
    assert "local" in report["steps"][0] and any("edited.js" in note for note in report["notes"])
    assert (root / "server.py").read_bytes() == b"old server" and upd.read_record(root) is None


def test_it_refuses_while_npc_forge_runs_from_the_folder(tmp_path):
    root = tmp_path / "forge"
    (root / "local").mkdir(parents=True)
    (root / "local" / "running.json").write_text(json.dumps({"pid": 4242, "port": 8765}), "utf-8")
    running = upd.app_processes(root, images={}, own={os.getpid(), 4242}, alive=lambda pid: pid == 4242)
    assert running == [(4242, "NPC Forge's server on port 8765")]           # the server is never "own", even as the parent
    with pytest.raises(upd.UpdateStopped, match="NPC Forge is running from this folder"):
        upd.update(root, args=args("--yes"), check=lambda: {}, processes=lambda r: running)
    assert upd.app_processes(root, images={}, alive=lambda pid: False) == []  # a stale file from a crash does not block it


def test_the_scripts_are_there_with_the_right_line_endings():
    bat, sh = (ROOT / "update.bat").read_bytes(), (ROOT / "update.sh").read_bytes()
    assert b"\r\n" in bat and b"\n" not in bat.replace(b"\r\n", b"") and b"scripts\\update.py %*" in bat
    assert b"\r" not in sh and b"scripts/update.py" in sh


# ---------------------------------------------------------------- the page's side

@pytest.fixture
def server_module(tmp_path, monkeypatch):
    monkeypatch.setenv("NPCGEN_LIBRARY", str(tmp_path / "library"))
    monkeypatch.setenv("NPCGEN_COMFY_URL", "http://127.0.0.1:9")
    server = importlib.import_module("server")
    monkeypatch.setattr(server, "UPDATE_DIR", tmp_path / "update")
    monkeypatch.setitem(server._UPDATE, "checks", updates.UpdateChecks(tmp_path / "update", run=lambda: updates.check(current="1.0.0", fetch=lambda url: release())))
    monkeypatch.setitem(server._UPDATE, "started", False)
    monkeypatch.setitem(server._UPDATE, "server", None)
    return server


def call(server, method, path, body=None):
    for route_method, pattern, fn in server.ROUTES:
        match = pattern.fullmatch(path) if hasattr(pattern, "fullmatch") else None
        if route_method == method and match:
            return fn({}, body or {}, *match.groups())
    raise AssertionError(f"no route {method} {path}")


def test_the_routes_check_plan_and_start_the_update(server_module, monkeypatch):
    server = server_module
    jobs = types.SimpleNamespace(busy=lambda: False, cancel=lambda *a: None)
    monkeypatch.setattr(server, "current_app", lambda: types.SimpleNamespace(jobs=jobs))
    state = call(server, "GET", "/api/update")
    assert state["version"] == __version__ and state["last"] is None and state["notice"] is None
    assert call(server, "POST", "/api/update/check")["newer"] is True
    plan = {"ok": True, "newer": True, "from": "1.0.0", "to": "9.0.0", "steps": ["a"], "notes": []}
    monkeypatch.setitem(server._UPDATE, "runner", lambda command, **kw: types.SimpleNamespace(returncode=0, stdout="noise\n" + json.dumps(plan) + "\n", stderr=""))
    assert call(server, "POST", "/api/update/plan") == plan
    started = []
    monkeypatch.setitem(server._UPDATE, "starter", lambda command, **kw: started.append(command))
    if os.name != "nt":
        with pytest.raises(server.ApiError):
            call(server, "POST", "/api/update/start")
        return
    assert call(server, "POST", "/api/update/start") == {"started": True}
    assert started and started[0][1:] == [str(server.UPDATER), "--from-app", "--reopen"]
    with pytest.raises(server.ApiError, match="already started"):
        call(server, "POST", "/api/update/start")


def test_update_now_waits_for_the_queue_unless_told_to_stop_it(server_module, monkeypatch):
    server = server_module
    if os.name != "nt":
        pytest.skip("Update now is for Windows")
    stopped = []
    jobs = types.SimpleNamespace(busy=lambda: not stopped, cancel=lambda *a: stopped.append(True))
    monkeypatch.setattr(server, "current_app", lambda: types.SimpleNamespace(jobs=jobs))
    monkeypatch.setitem(server._UPDATE, "starter", lambda command, **kw: None)
    with pytest.raises(server.ApiError, match="Jobs are waiting or running"):
        call(server, "POST", "/api/update/start")
    assert call(server, "POST", "/api/update/start", {"stop_jobs": True}) == {"started": True} and stopped


def test_the_server_names_itself_while_it_runs(server_module, tmp_path, monkeypatch):
    server = server_module
    monkeypatch.setattr(server, "RUNNING_FILE", tmp_path / "local" / "running.json")
    server._mark_running(8765)
    assert json.loads(server.RUNNING_FILE.read_text("utf-8")) == {"pid": os.getpid(), "port": 8765}
    server._unmark_running()
    assert not server.RUNNING_FILE.exists()


def test_update_py_runs_on_its_own_and_says_its_options():
    done = subprocess.run([sys.executable, str(ROOT / "scripts" / "update.py"), "--help"], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0 and "--undo" in done.stdout and "--check" in done.stdout
