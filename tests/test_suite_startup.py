import json
import os

import pytest

import server
from npcgen import config, fels_suite, mcp
from scripts import start


@pytest.fixture
def isolated_startup(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ROOT", tmp_path)
    monkeypatch.setattr(server, "RUNNING_FILE", tmp_path / "local" / "running.json")
    monkeypatch.setenv("FELS_SUITE_DIR", str(tmp_path / "suite"))
    monkeypatch.setenv("NPCGEN_LIBRARY", str(tmp_path / "library"))
    monkeypatch.delenv("NPCGEN_PORT", raising=False)
    monkeypatch.delenv("NPCFORGE_URL", raising=False)
    return tmp_path


def test_saved_port_migrates_once_and_keeps_other_config(isolated_startup, capsys):
    path = isolated_startup / "config.json"
    path.write_text('{"port": 8765, "options": {"quality": "draft"}}', encoding="utf-8")
    assert config.load(migrate_port=True)["port"] == 8766
    assert json.loads(path.read_text("utf-8")) == {"port": 8766, "options": {"quality": "draft"}}
    assert "from 8765 to 8766" in capsys.readouterr().out
    assert config.load(migrate_port=True)["port"] == 8766
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize(("saved", "override", "expected"), [(8775, None, 8775), (8765, "8765", 8765)])
def test_saved_custom_ports_and_environment_overrides_are_preserved(isolated_startup, monkeypatch, saved, override, expected):
    path = isolated_startup / "config.json"
    original = f'{{"port": {saved}}}'
    path.write_text(original, encoding="utf-8")
    if override is not None:
        monkeypatch.setenv("NPCGEN_PORT", override)
    assert config.load(migrate_port=True)["port"] == expected
    assert path.read_text("utf-8") == original


def test_server_announces_the_bound_port_and_removes_it_on_exit(isolated_startup, monkeypatch):
    monkeypatch.setattr(server.sys, "argv", ["server.py", "--port", "0"])
    monkeypatch.setattr(server, "load_extensions", lambda *args, **kwargs: [])

    def serve_once(httpd):
        entry = fels_suite.read_address("npc-forge")
        assert entry["url"] == f"http://127.0.0.1:{httpd.server_port}"
        assert entry["pid"] == os.getpid()
        assert entry["api"] == server.API_VERSION
        assert entry["instance"]

    monkeypatch.setattr(server.NPCServer, "serve_forever", serve_once)
    server.main()
    assert not fels_suite.address_path("npc-forge").exists()


@pytest.mark.parametrize(("old_health", "opens_old"), [
    ({"app": "Fel's Voice Lab", "lab_id": "lab"}, False),
    ({"online": False, "gpu_rule": {}}, True),
])
def test_launcher_distinguishes_voice_lab_from_old_forge(isolated_startup, monkeypatch, capsys, old_health, opens_old):
    old_url = "http://127.0.0.1:8765"
    monkeypatch.setattr(start, "ask_identity", lambda url, token: old_health if url == old_url else None)
    monkeypatch.setattr(start, "_listener", lambda port: None)
    opened, launched = [], []
    monkeypatch.setattr(start.webbrowser, "open", opened.append)
    monkeypatch.setattr(start.subprocess, "call", lambda command, **kwargs: launched.append(command) or 0)

    assert start.main() == 0
    if opens_old:
        assert opened == [old_url] and not launched
        assert "older NPC Forge" in capsys.readouterr().out
    else:
        assert not opened and launched[0][-1] == "--open"
        assert "Voice Lab is running on port 8765" in capsys.readouterr().out


def test_mcp_discovers_the_announced_copy_and_falls_back_to_older_forge(isolated_startup, monkeypatch, capsys):
    custom = "http://127.0.0.1:8775"
    legacy = "http://127.0.0.1:8765"
    assert fels_suite.write_address("npc-forge", custom, "1.0.0", 45, "forge-test")
    responses = {custom: {"fels": {"app": "npc-forge"}}, legacy: {"app": "Fel's Voice Lab", "lab_id": "lab"}}
    monkeypatch.setattr(mcp, "ask_identity", lambda url, token: responses.get(url))
    assert mcp.connect(given=legacy).url == custom
    assert "That address is Voice Lab" in capsys.readouterr().err

    assert fels_suite.remove_address("npc-forge")
    with pytest.raises(mcp.ToolError, match="NPC Forge is not running"):
        mcp.connect()
    responses[legacy] = {"online": False, "gpu_rule": {}}
    assert mcp.connect().url == legacy
