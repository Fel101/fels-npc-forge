"""The live jiggle preview of the Designed body card: the file the page plays (bodymaker.soft_preview, GET /api/npcs/<id>/body-3d/soft-preview.glb), taking the soft-body bones out of a
body again (softbody.undo), and the page's JavaScript solver (web/soft-solver.js) against recorded runs of Godot 4.7.2 (scripts/check_soft_preview.mjs, run here under node). The real
bodies skip where the Body Designer is not installed; the solver check skips where node is not."""
import copy
import importlib
import json
import shutil
import subprocess
import threading
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np
import pytest

import soft_support as ss
from npcgen import bodymaker, config, softbody
from npcgen.library import Library
from npcgen.paint3d import Glb

ROOT = Path(__file__).resolve().parent.parent
needs_designer = pytest.mark.skipif(not bodymaker.bases(), reason="the Body Designer is not installed")
needs_node = pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
ALL = {"chest": 1.0, "butt": 1.0, "belly": 1.0, "thighs": 1.0}
PROFILE = {"first_name": "Soft", "last_name": "Preview", "age": 30, "gender": "female", "occupation": "tester"}
TRACE = ROOT / "tests" / "data" / "soft_godot_trace.json"


# ---- the solver in the page -----------------------------------------------------------------------------------------------------------------
@needs_node
def test_the_javascript_solver_follows_recorded_runs_of_the_engine_and_the_maths_around_it_holds():
    done = subprocess.run(["node", "--test", str(ROOT / "scripts" / "check_soft_preview.mjs")], capture_output=True, text=True, timeout=120, encoding="utf-8")
    assert done.returncode == 0, done.stdout[-3000:] + done.stderr[-1500:]
    assert "fail 0" in done.stdout


def test_the_recorded_runs_are_runs_of_the_four_parts_with_the_settings_block_the_file_carries():
    trace = json.loads(TRACE.read_text("utf-8"))
    assert {run["fps"] for run in trace["runs"].values()} == {30, 60} and len(trace["runs"]) >= 4
    assert {chain["block"]["kind"] for chain in trace["chains"].values()} == set(softbody.PARTS)
    for name, run in trace["runs"].items():
        for chain, data in run["chains"].items():
            assert len(data["parent"]) == len(data["bone1"]) == len(data["bone2"]) == run["frames"] and chain in trace["chains"], name
            assert all(len(pose) == 7 and all(isinstance(v, float) for v in pose) for pose in data["parent"][:3] + data["bone2"][:3])


@needs_designer
def test_the_block_the_page_reads_has_the_keys_the_recorded_runs_were_made_with():
    """web/soft-solver.js `chainSpec` reads these keys off the first bone of each chain; a change in the file's block must come with a change there (and a new recording)."""
    trace = json.loads(TRACE.read_text("utf-8"))
    data, _report = softbody.apply(ss.game_body()[0], ALL)
    doc = Glb(data).json
    blocks = {n["extras"]["forge_secondary"]["chain"]: n["extras"]["forge_secondary"] for n in doc["nodes"] if "forge_secondary" in (n.get("extras") or {})}
    assert set(blocks) >= set(trace["chains"])
    for name, chain in trace["chains"].items():
        assert set(blocks[name]) == set(chain["block"]), name
        assert blocks[name].get("center_bone") == chain["block"].get("center_bone") and blocks[name]["bones"] == chain["block"]["bones"], name
        node = next(n for n in doc["nodes"] if n.get("name") == chain["block"]["bones"][0])
        assert len(node["translation"]) == 3 and len(node["rotation"]) == 4                                          # (the two bones' rest transforms the page reads are in the nodes)


# ---- real bodies ------------------------------------------------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def body():
    """The ordinary designed female game body (about 10 s at a 512 px skin)."""
    return ss.game_body(values={"chest": 0.4, "hips": 0.3})[0]


@pytest.fixture(scope="module")
def full(body):
    """(the body with all four parts at their full share, the report)."""
    return softbody.apply(body, ALL)


@pytest.fixture(scope="module")
def partly(body):
    """The body as a card could have built it: the chest and the butt only, at less than a full share."""
    return softbody.apply(body, {"chest": 0.7, "butt": 0.35}, softness=0.6)[0]


def dense(glb, mesh):
    """{column: weight} per vertex of a mesh as an (N, columns) matrix."""
    primitive = softbody._skinned(glb.json)[mesh]
    jn = glb.accessor(primitive["attributes"]["JOINTS_0"]).astype(int)
    wt = glb.accessor(primitive["attributes"]["WEIGHTS_0"]).astype(float)
    out = np.zeros((len(jn), int(jn.max()) + 1))
    for slot in range(4):
        np.add.at(out, (np.arange(len(jn)), jn[:, slot]), wt[:, slot])
    return out


@needs_designer
def test_the_spring_bones_can_be_taken_out_again_and_the_skin_goes_back_to_the_bones_they_hung_on(body, full):
    back = softbody.undo(full[0])
    before, after = Glb(body), Glb(back)
    assert after.json["nodes"] == before.json["nodes"] and after.json["skins"][0]["joints"] == before.json["skins"][0]["joints"]
    assert "forge_soft" not in after.json["asset"]["extras"] and after.json["asset"]["extras"] == before.json["asset"]["extras"]
    assert len(after.json["bufferViews"]) == len(before.json["bufferViews"])                                      # (the weights of the spring bones' views are not left behind)
    assert np.allclose(after.accessor(after.json["skins"][0]["inverseBindMatrices"]), before.accessor(before.json["skins"][0]["inverseBindMatrices"]))
    for mesh in softbody._skinned(before.json):
        was, now = dense(before, mesh), dense(after, mesh)
        assert now.shape == was.shape and np.abs(now.sum(axis=1) - 1.0).max() < 1e-3                                # (no spring bone is named, every vertex still adds up)
        differing = np.abs(now - was).max(axis=1) > 1e-4
        assert differing.mean() < 0.10 and np.abs(now - was).max() < 0.2, mesh                                      # (only where a fifth bone's share was merged to make room)


@needs_designer
def test_taken_out_the_skin_moves_as_the_body_did_before_the_bones_were_added(body, full):
    original, back = ss.Rig(body), ss.Rig(softbody.undo(full[0]))
    def worst(turns):
        a, b = original.world(turns), back.world(turns)
        return max(np.linalg.norm(original.skinned(m, a) - back.skinned(m, b), axis=1).max() for m in original.meshes) * 1000
    assert worst(None) < 1e-3
    rng = np.random.default_rng(5)
    bones = ["clavicle_l", "upperarm_r", "thigh_l", "thigh_r", "spine_01", "spine_02", "pelvis", "calf_l"]
    assert max(worst(ss.random_turns(rng, bones, 0.5)) for _ in range(8)) < 6.0                                      # (what DROP_COST allowed the step to cost)


@needs_designer
def test_a_body_that_has_none_comes_back_as_it_is_and_one_taken_back_can_be_given_all_the_parts_again(body, full, partly):
    assert softbody.undo(body) is body
    again, report = softbody.apply(softbody.undo(partly), ALL)
    assert softbody.check_file(again, set(softbody.PARTS)) is None and report["bones"] == 14
    assert set(Glb(again).json["asset"]["extras"]["forge_soft"]["parts"]) == set(softbody.PARTS)
    assert Glb(again).json["asset"]["extras"]["forge_soft"]["parts"] == {part: 1.0 for part in softbody.PARTS}
    assert softbody.undo(softbody.undo(full[0])) == softbody.undo(full[0])                                           # (taking them out twice is taking them out once)


@needs_designer
def test_it_only_takes_out_bones_this_program_laid(body, full):
    def renamed(doc):
        doc["nodes"][doc["skins"][0]["joints"][-1]]["name"] = "soft_chest_l_03"
    glb = Glb(full[0])
    renamed(glb.json)
    with pytest.raises(softbody.SoftBodyError, match="not the ones this program lays"):
        softbody.undo(glb.dumps())
    glb = Glb(full[0])
    glb.json["asset"]["extras"]["forge_soft"]["joints_before"] = 20
    with pytest.raises(softbody.SoftBodyError, match="not the ones this program lays"):
        softbody.undo(glb.dumps())


# ---- the file the preview plays -----------------------------------------------------------------------------------------------------------------
def designed_character(lib, data, soft=None):
    """A character whose 3D model is a designed body and whose skeleton file is `data`."""
    npc = lib.create({"setting": "modern", "style": "pixel", "seed": 1, "profile": PROFILE, "appearance": {}})
    lib.write_asset(npc["id"], "model_3d_rigged.glb", data)

    def put(record):
        record["assets"] = {"model_3d": {"file": "model_3d.glb", "designed": {"base": "forge-anatomical-female-v7", **({"soft": soft} if soft else {})}},
                            "rig_3d": {"file": "model_3d_rigged.glb", "joints_from": "designed body"}}
    return lib.update(npc["id"], put)


@pytest.fixture()
def lib(tmp_path):
    return Library(tmp_path / "library")


@needs_designer
def test_the_preview_offers_all_four_parts_whether_the_body_was_built_with_none_some_or_all(lib, body, full, partly):
    for name, data, soft in (("none", body, None), ("some", partly, {"request": {"chest": 1.0}, "made": {"chest": 0.7, "butt": 0.35}}), ("all", full[0], {"request": {}, "made": dict(ALL)})):
        npc = designed_character(lib, data, soft)
        made, report = bodymaker.soft_preview(lib, npc)
        assert softbody.check_file(made, set(softbody.PARTS)) is None, name
        info = Glb(made).json["asset"]["extras"]["forge_soft"]
        assert info["parts"] == {part: 1.0 for part in softbody.PARTS} and info["softness"] == 0.5 and report["bones"] == 14, name
        assert report["had"] == (soft or {}).get("request") and report["seconds"] >= 0
        blocks = [n["extras"]["forge_secondary"] for n in Glb(made).json["nodes"] if "forge_secondary" in (n.get("extras") or {})]
        assert {b["kind"] for b in blocks} == set(softbody.PARTS) and all(b["amount"] == 1.0 for b in blocks), name
        assert [b["stiffness"] for b in blocks if b["kind"] == "chest"] == [softbody.SETTINGS["chest"]["stiffness"]] * 2                      # (as suggested: softness 0.5)


@needs_designer
def test_the_preview_does_not_change_what_the_character_keeps(lib, partly):
    npc = designed_character(lib, partly, {"request": {"chest": 1.0}, "made": {"chest": 0.7}})
    before = lib.read_asset(npc["id"], "model_3d_rigged.glb")
    bodymaker.soft_preview(lib, npc)
    assert lib.read_asset(npc["id"], "model_3d_rigged.glb") == before and lib.get(npc["id"])["assets"] == npc["assets"]


@needs_designer
def test_the_preview_says_in_words_when_it_cannot_be_made(lib, body):
    bare = lib.create({"setting": "modern", "style": "pixel", "seed": 1, "profile": PROFILE, "appearance": {}})
    with pytest.raises(ValueError, match="moves a designed body"):
        bodymaker.soft_preview(lib, bare)
    other = designed_character(lib, body)
    other["assets"]["rig_3d"]["joints_from"] = "pose keypoints"                                                   # (a skeleton made from pictures is not the designed body's own)
    with pytest.raises(ValueError, match="moves a designed body"):
        bodymaker.soft_preview(lib, other)
    gone = designed_character(lib, body)
    lib.resolve(gone["id"], "model_3d_rigged.glb").unlink()
    with pytest.raises(ValueError, match="skeleton file is missing"):
        bodymaker.soft_preview(lib, gone)
    male = Glb(body)
    male.json["asset"]["extras"]["body_design"]["base"] = "forge-anatomical-male-v7"
    with pytest.raises(ValueError, match="cannot be previewed: .*female body"):
        bodymaker.soft_preview(lib, designed_character(lib, male.dumps()))


# ---- the route ---------------------------------------------------------------------------------------------------------------------------------------
@pytest.fixture()
def served(tmp_path):
    server = importlib.import_module("server")
    cfg = copy.deepcopy(config.DEFAULTS)
    cfg.update(library_path=tmp_path / "library", comfy_url="http://127.0.0.1:9", auth_token="")
    app = server.App(cfg)
    httpd = server.create_server(("127.0.0.1", 0), app)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"

    def get(path):
        try:
            with urllib.request.urlopen(base + path, timeout=60) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    get.app = app
    get.server = server
    yield get
    httpd.shutdown()
    httpd.server_close()
    app.close()


@needs_designer
def test_the_route_serves_the_preview_file_and_says_what_the_body_was_built_with(served, body, partly):
    npc = designed_character(served.app.lib, partly, {"request": {"chest": 0.7}, "made": {"chest": 0.7, "butt": 0.35}})
    status, headers, data = served(f"/api/npcs/{npc['id']}/body-3d/soft-preview.glb")
    assert status == 200 and headers["Content-Type"] == "model/gltf-binary" and headers["Cache-Control"] == "no-store"
    assert softbody.check_file(data, set(softbody.PARTS)) is None
    said = json.loads(headers["X-Soft-Preview"])
    assert said["had"] == {"chest": 0.7} and said["bones"] == 14
    assert served.app.lib.read_asset(npc["id"], "model_3d_rigged.glb") == partly                                       # (nothing was stored or changed)


def test_the_route_refuses_in_words_a_character_that_is_not_there_or_has_no_designed_body(served):
    assert served("/api/npcs/nobody-0000/body-3d/soft-preview.glb")[0] == 404
    npc = served.app.lib.create({"setting": "modern", "style": "pixel", "seed": 1, "profile": PROFILE, "appearance": {}})
    status, _headers, data = served(f"/api/npcs/{npc['id']}/body-3d/soft-preview.glb")
    assert status == 400 and "designed body" in json.loads(data)["error"]


def test_the_page_is_told_to_look_for_the_new_route_by_a_newer_api_version(served):
    assert served.server.API_VERSION >= 40
    page = (ROOT / "web" / "app.js").read_text("utf-8")
    assert f"const REQUIRED_API = {served.server.API_VERSION};" in page
