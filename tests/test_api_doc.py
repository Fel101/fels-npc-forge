"""docs/api.md lists every route server.py serves (scripts/make_api_doc.py writes it)."""
import importlib.util
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _maker():
    spec = importlib.util.spec_from_file_location("make_api_doc", ROOT / "scripts" / "make_api_doc.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_api_page_is_current():
    assert _maker().main(["--check"]) == 0, "docs/api.md is out of date: run python scripts/make_api_doc.py"


def test_a_route_pattern_reads_as_a_path_with_named_parts():
    readable = _maker().readable
    assert readable("/api/npcs/([a-z0-9-]+)/files/(.+)") == "/api/npcs/{npc}/files/{path}"
    assert readable("/api/npcs/([a-z0-9-]+)/voice/rig/(portrait|sprite)") == "/api/npcs/{npc}/voice/rig/{portrait|sprite}"
    assert readable("/api/trash/([a-z0-9][a-z0-9-]+--[0-9]{8}(?:-[0-9]+)?)/restore") == "/api/trash/{folder}/restore"
    assert readable("/api/npcs/([a-z0-9-]+)/assets/(portrait|sprite|voice|sheet|living)(?:/([a-z0-9_-]+))?") == \
        "/api/npcs/{npc}/assets/{asset}[/{id}]"
    assert readable("/api/npcs/([a-z0-9-]+)/equipped\\.glb") == "/api/npcs/{npc}/equipped.glb"
