"""The MCP server (npcgen/mcp.py): the protocol, the tools against a stand-in NPC Forge, and that the adult features stay out."""
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

from npcgen import __version__, mcp

ROOT = Path(__file__).resolve().parents[1]
CONFIG = {
    "version": __version__,
    "settings": {"fantasy": {"label": "Medieval fantasy", "blurb": "A village."}, "models": {"label": "Models", "blurb": "adult"}},
    "styles": {"pixel": {"label": "Pixel art"}}, "genders": [{"id": "female"}], "age_groups": [{"id": "adult"}],
    "expressions": [{"id": "happy", "default": True}, {"id": "sad", "default": True}, {"id": "neutral", "default": False}],
    "quality": {"draft": {}, "standard": {}}, "engines": {"image": [{"id": "qwen2512"}]},
    "sheet": {"views": [{"id": "portrait"}, {"id": "front"}]}, "defaults": {"setting": "fantasy", "style": "pixel"},
    "items": {"families": [{"id": "weapon", "maker": "photo"}, {"id": "glasses", "maker": "recipe"}]},
}


class FakeForge:
    url = "http://127.0.0.1:8765"

    def __init__(self):
        self.calls = []
        self.jobs = [{"id": "j1", "npc_id": "a-1", "npc_name": "Ann", "status": "done", "steps": [{"status": "done"}]}]

    def request(self, method, path, body=None, *, raw=False, timeout=60):
        self.calls.append((method, path, body))
        if path.startswith("/api/ext/"):
            raise mcp.ToolError("The extensions' routes are not offered through MCP.")
        if path == "/api/config":
            return CONFIG
        if path == "/api/health":
            return {"online": True, "version": "0.37.0", "gpu": "R9700", "missing": [], "engines_missing": {}, "items": {"ok": True}}
        if path == "/api/jobs":
            return self.jobs
        if path == "/api/generate":
            ids = body.get("npc_ids") or [f"npc-{i}" for i in range(body.get("count", 1))]
            return {"npc_ids": ids, "jobs": [{"id": f"job-{i}"} for i in ids]}
        if path == "/api/roll":
            return {"setting": body["setting"], "profile": {"first_name": "Rolled"}}
        if path == "/api/npcs" and method == "POST":
            return {"id": f"saved-{len([c for c in self.calls if c[1] == '/api/npcs'])}"}
        if path == "/api/npcs":
            return [{"id": "a-1", "name": "Ann", "setting": "fantasy", "has": {"portrait": True, "sprite": False}},
                    {"id": "m-1", "name": "Mia", "setting": "models", "has": {"portrait": True}}]
        if path.startswith("/api/npcs/m-1"):
            return {"id": "m-1", "setting": "models"}
        if path.startswith("/api/npcs/") and not raw:
            return {"id": "a-1", "setting": "fantasy", "profile": {"first_name": "Ann"},
                    "assets": {"portrait": {"file": "portrait.png"}, "expressions": {"happy": {"file": "expressions/happy.png"}}}}
        if raw:
            return b"PK zip bytes"
        if path == "/api/items":
            return {"id": "sword-1"}
        if path.endswith("/make"):
            return {"job": {"id": "item-job"}}
        return {}


def call(name, arguments, forge=None):
    forge = forge or FakeForge()
    answer = mcp.handle({"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": name, "arguments": arguments}}, forge)
    return answer["result"], forge


def test_initialize_lists_eleven_tools_each_with_a_schema():
    init = mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}}, FakeForge())
    assert init["result"]["protocolVersion"] == "2024-11-05" and init["result"]["serverInfo"]["version"] == __version__
    assert mcp.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}, FakeForge()) is None
    tools = mcp.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, FakeForge())["result"]["tools"]
    assert len(tools) == 11 and all(t["inputSchema"]["type"] == "object" and t["description"] for t in tools)
    assert mcp.handle({"jsonrpc": "2.0", "id": 3, "method": "nope"}, FakeForge())["error"]["code"] == -32601


def test_export_card_uses_card_endpoints_and_writes_the_reply(tmp_path):
    result, forge = call("export_card", {"npc_id": "a-1", "format": "png", "output_dir": str(tmp_path)})
    assert not result["isError"]
    assert forge.calls == [("GET", "/api/npcs/a-1/card.png", None)]
    assert (tmp_path / "a-1.png").read_bytes() == b"PK zip bytes"
    result, forge = call("export_card", {"npc_id": "a-1", "include_voice": False, "output_dir": str(tmp_path)})
    assert not result["isError"] and forge.calls == [("GET", "/api/npcs/a-1/card.charx?voice=0", None)]
    result, forge = call("export_card", {"npc_id": "../escape", "output_dir": str(tmp_path)})
    assert result["isError"] and forge.calls == []


def test_make_characters_rolls_a_batch_with_its_pictures_in_one_request():
    result, forge = call("make_characters", {"setting": "fantasy", "count": 3, "make": {"portrait": True, "expressions": True, "sheet": True},
                                             "constraints": {"gender": "female"}, "quality": "draft"})
    data = result["structuredContent"]
    assert not result["isError"] and data["npc_ids"] == ["npc-0", "npc-1", "npc-2"] and len(data["job_ids"]) == 3
    body = [c for c in forge.calls if c[1] == "/api/generate"][0][2]
    assert body["assets"]["expressions"] == ["happy", "sad"] and body["assets"]["sheet"] == {"views": ["portrait", "front"]}
    assert body["constraints"] == {"gender": "female"} and body["options"] == {"quality": "draft"}


def test_a_described_character_is_rolled_from_the_words_saved_then_drawn():
    result, forge = call("make_characters", {"setting": "fantasy", "description": "a grumpy dwarf smith", "make": {"portrait": True}})
    roll = [c for c in forge.calls if c[1] == "/api/roll"][0][2]
    assert roll["freeform"] == "a grumpy dwarf smith" and result["structuredContent"]["npc_ids"] == ["saved-1"]
    assert [c for c in forge.calls if c[1] == "/api/generate"][0][2]["npc_ids"] == ["saved-1"]


def test_the_adult_features_stay_out():
    result, _ = call("make_characters", {"setting": "models"})
    assert result["isError"] and "adult" in result["content"][0]["text"]
    assert "models" not in call("forge_options", {})[0]["structuredContent"]["settings"]
    listed = call("list_characters", {})[0]["structuredContent"]
    assert [c["id"] for c in listed["characters"]] == ["a-1"]
    assert call("get_character", {"npc_id": "m-1"})[0]["isError"]
    assert call("export_characters", {"npc_ids": ["m-1"], "output_dir": "."})[0]["isError"]
    with pytest.raises(mcp.ToolError):
        mcp.Forge("http://127.0.0.1:9").request("GET", "/api/ext/adult/state")


def test_wait_reports_failures_with_their_reason_and_code():
    forge = FakeForge()
    forge.jobs = [{"id": "j2", "status": "error", "code": "low_memory", "steps": [{"status": "error", "error": "not enough free memory", "code": "low_memory"}]}]
    data = call("wait_for_jobs", {"job_ids": ["j2", "gone"]}, forge)[0]["structuredContent"]
    assert data["finished"] and data["jobs"][0]["code"] == "low_memory" and data["jobs"][0]["error"] == "not enough free memory"
    assert data["unknown_job_ids"] == ["gone"]


def test_wait_stops_at_its_time_limit():
    forge = FakeForge()
    forge.jobs = [{"id": "j3", "status": "running", "steps": []}]
    ticks = iter(range(0, 10000, 100))
    data = mcp.wait_for_jobs(forge, {"timeout_seconds": 5}, sleep=lambda s: None, clock=lambda: next(ticks))
    assert not data["finished"] and "call wait_for_jobs again" in data["note"]


def test_get_character_gives_the_address_of_each_file_and_export_writes_them(tmp_path):
    data = call("get_character", {"npc_id": "a-1"})[0]["structuredContent"]
    assert data["files"] == {"portrait": "http://127.0.0.1:8765/api/npcs/a-1/files/portrait.png",
                             "expressions.happy": "http://127.0.0.1:8765/api/npcs/a-1/files/expressions/happy.png"}
    out = call("export_characters", {"npc_ids": ["a-1"], "format": "godot", "output_dir": str(tmp_path)})[0]["structuredContent"]
    assert out["files"] == [str(tmp_path / "a-1-walk-godot.zip")] and (tmp_path / "a-1-walk-godot.zip").read_bytes() == b"PK zip bytes"


def test_bad_arguments_are_said_not_raised():
    assert call("generate_assets", {"npc_ids": [], "make": {"portrait": True}})[0]["isError"]
    assert "nothing" in call("generate_assets", {"npc_ids": ["a-1"], "make": {}})[0]["content"][0]["text"]
    assert call("make_item", {"family": "glasses"})[0]["isError"]
    assert call("cancel_jobs", {})[0]["isError"]
    assert call("make_item", {"family": "weapon", "kind": "sword", "description": "a ranger's longsword"})[0]["structuredContent"]["job_ids"] == ["item-job"]


def test_the_stdio_loop_answers_one_line_per_request():
    out = io.StringIO()
    lines = "\n".join(json.dumps(m) for m in ({"jsonrpc": "2.0", "id": 1, "method": "ping"}, {"jsonrpc": "2.0", "method": "notifications/initialized"},
                                             {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})) + "\nnot json\n"
    mcp.serve(FakeForge(), stdin=io.StringIO(lines), stdout=out)
    answers = [json.loads(line) for line in out.getvalue().splitlines()]
    assert [a.get("id") for a in answers] == [1, 2, None] and answers[2]["error"]["code"] == -32700


def test_it_runs_as_a_file_from_any_folder(tmp_path):
    done = subprocess.run([sys.executable, str(ROOT / "npcgen" / "mcp.py"), "--url", "http://127.0.0.1:9"], cwd=tmp_path, capture_output=True,
                          text=True, input=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}) + "\n", timeout=60)
    assert done.returncode == 0 and json.loads(done.stdout) == {"jsonrpc": "2.0", "id": 1, "result": {}}
