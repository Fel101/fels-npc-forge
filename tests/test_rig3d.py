import importlib.util
import json
import pathlib
import sys
import threading
from unittest import mock

import pytest

from npcgen import rig3d, service, roll
from npcgen.comfy import Cancelled, ComfyError
from npcgen.jobs import Jobs, _replace_model_3d
from npcgen.library import Library


def _npc(library, **extra):
    return library.create({"setting": "modern", "style": "render3d", "seed": 3, "appearance": {},
                           "profile": {"first_name": "Mira", "last_name": "Vale", "age": 30, "gender": "female",
                                       "occupation": "tester"}, **extra})


def _kinds(steps):
    return [s["kind"] for s in steps]


def test_a_skeleton_request_makes_the_model_first_when_there_is_none():
    steps = Jobs.plan({"assets": {"sprite": {"file": "sprite.png", "created": "t"}}}, {"rig_3d": True},
                      {"three_d_model": "hunyuan21"})
    assert _kinds(steps)[-2:] == ["model_3d", "rig_3d"]


def test_a_skeleton_request_reuses_an_existing_model():
    npc = {"assets": {"sprite": {"file": "sprite.png", "created": "t"}, "model_3d": {"file": "model_3d.glb"}}}
    assert _kinds(Jobs.plan(npc, {"rig_3d": True}, {})) == ["rig_3d"]


def test_the_request_validator_accepts_a_skeleton_on_its_own():
    assets = service.clean_assets({"rig_3d": True}, roll.load_data())
    assert assets["rig_3d"] is True and not assets["model_3d"]


def test_only_two_legged_bodies_get_the_humanoid_skeleton():
    assert rig3d.unsupported_reason("bipedal") is None
    assert "two-legged" in rig3d.unsupported_reason("taur")


def test_a_new_model_marks_the_old_skeleton_outdated():
    npc = {"assets": {"model_3d": {"file": "old.glb"}, "rig_3d": {"file": "model_3d_rigged.glb"}}}
    _replace_model_3d(npc, {"file": "model_3d.glb"})
    assert npc["assets"]["model_3d"]["file"] == "model_3d.glb"
    assert npc["assets"]["rig_3d"]["stale"] is True


def test_deleting_the_model_deletes_its_skeleton_too(tmp_path):
    library = Library(tmp_path / "library")
    npc = _npc(library)
    library.write_asset(npc["id"], "model_3d.glb", b"model")
    library.write_asset(npc["id"], "model_3d_rigged.glb", b"rigged")
    library.update(npc["id"], lambda n: n.setdefault("assets", {}).update(
        model_3d={"file": "model_3d.glb"}, rig_3d={"file": "model_3d_rigged.glb"}))

    after = library.delete_asset(npc["id"], "model_3d")
    assert "rig_3d" not in after["assets"]
    assert not library.resolve(npc["id"], "model_3d_rigged.glb").exists()


def test_blender_from_config_wins(tmp_path, monkeypatch):
    exe = tmp_path / "blender.exe"
    exe.write_bytes(b"")
    monkeypatch.delenv("NPCGEN_BLENDER", raising=False)
    assert rig3d.find_blender({"blender": str(exe)}) == str(exe)


def test_the_newest_installed_blender_is_used(tmp_path, monkeypatch):
    for version in ("4.5", "5.2", "5.10"):
        folder = tmp_path / "Blender Foundation" / f"Blender {version}"
        folder.mkdir(parents=True)
        (folder / "blender.exe").write_bytes(b"")
    monkeypatch.delenv("NPCGEN_BLENDER", raising=False)
    monkeypatch.setenv("ProgramFiles", str(tmp_path))
    monkeypatch.setenv("ProgramW6432", str(tmp_path))
    assert rig3d.find_blender({}).endswith("Blender 5.10\\blender.exe") or rig3d.find_blender({}).endswith("Blender 5.10/blender.exe")


def test_missing_blender_is_a_readable_job_error(monkeypatch):
    monkeypatch.setattr(rig3d, "find_blender", lambda cfg: None)
    with pytest.raises(ComfyError, match="Blender was not found"):
        rig3d.run({}, b"glb")


class _FakeBlender:
    """Stands in for subprocess.Popen: writes what the Blender script would, or fails the way it would."""

    def __init__(self, fail=None, hang=False):
        self.fail, self.hang, self.cmd = fail, hang, None

    def __call__(self, cmd, stdout=None, **kw):
        self.cmd = cmd
        args = cmd[cmd.index("--") + 1:]
        out, report, poster = args[1], args[args.index("--report") + 1], args[args.index("--poster") + 1]
        if self.fail:
            stdout.write(("Traceback...\nRIG_ERROR " + self.fail + "\n").encode())
        elif not self.hang:
            open(out, "wb").write(b"rigged")
            open(poster, "wb").write(b"png")
            open(report, "w").write(json.dumps({"clips": ["Idle", "Walk", "Wave"], "notes": []}))
        fake = self

        class Proc:
            returncode = 1 if fake.fail else 0

            def poll(self):
                return None if fake.hang else self.returncode

            def kill(self):
                fake.hang = False

            def wait(self):
                return self.returncode
        return Proc()


def test_a_rig_run_returns_the_model_poster_and_report(monkeypatch):
    fake = _FakeBlender()
    monkeypatch.setattr(rig3d, "find_blender", lambda cfg: "blender")
    monkeypatch.setattr(rig3d.subprocess, "Popen", fake)
    glb, poster, report = rig3d.run({}, b"model", height_m=1.8)
    assert (glb, poster, report["clips"]) == (b"rigged", b"png", ["Idle", "Walk", "Wave"])
    assert fake.cmd[fake.cmd.index("--height") + 1] == "1.800"


def test_a_failed_rig_run_reports_the_scripts_own_message(monkeypatch):
    monkeypatch.setattr(rig3d, "find_blender", lambda cfg: "blender")
    monkeypatch.setattr(rig3d.subprocess, "Popen", _FakeBlender(fail="No gap between the l arm and the body: the model needs an A-pose"))
    with pytest.raises(ComfyError, match="needs an A-pose") as failed:
        rig3d.run({}, b"model")
    assert "Make the model again from a model sheet in an A-pose" in str(failed.value)       # (and it says how to get one)


def test_cancelling_stops_blender(monkeypatch):
    monkeypatch.setattr(rig3d, "find_blender", lambda cfg: "blender")
    monkeypatch.setattr(rig3d.subprocess, "Popen", _FakeBlender(hang=True))
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(Cancelled):
        rig3d.run({}, b"model", cancel=cancel)


def test_a_running_blender_tells_the_job_it_is_still_alive(monkeypatch):
    """The watchdog stops a step that shows no sign of life for 45 minutes: a long Blender run must not look like one."""
    monkeypatch.setattr(rig3d, "find_blender", lambda cfg: "blender")
    monkeypatch.setattr(rig3d.subprocess, "Popen", _FakeBlender(hang=True))

    class Flag(threading.Event):
        beats = 0

        def beat(self, note=None):
            Flag.beats += 1
            if Flag.beats >= 3:
                self.set()
    with pytest.raises(Cancelled):
        rig3d.run({}, b"model", cancel=Flag())
    assert Flag.beats >= 3


# ---- the Blender script's own decisions (its Blender modules stood in for) ------------------------------------------------------------

@pytest.fixture
def rig_script():
    """npcgen/blender/rig_humanoid.py imported with stand-ins for Blender's modules (it only runs inside Blender): enough to test the
    plain-Python decisions it makes. Everything the import adds to sys.modules is taken out again afterwards."""
    path = pathlib.Path(__file__).resolve().parents[1] / "npcgen" / "blender" / "rig_humanoid.py"
    stand_ins = {name: mock.MagicMock() for name in ("bpy", "bmesh", "mathutils", "mathutils.bvhtree")}
    saved_path = list(sys.path)
    with mock.patch.dict(sys.modules, stand_ins):
        spec = importlib.util.spec_from_file_location("rig_humanoid_under_test", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        yield module
    sys.path[:] = saved_path


def test_bone_heat_tries_the_usual_voxel_size_first(rig_script):
    made = []

    def heat_proxy(source, arm, H, divisions):
        made.append(divisions)
        return "proxy", 50000, 0, ["a"]             # one bone of six without weights: fine

    rig_script.heat_proxy = heat_proxy
    proxy, faces, dropped, empty, tried = rig_script.weighted_proxy("shape", "arm", 1.7, 6)
    assert (made, tried, proxy, faces) == ([260], [260], "proxy", 50000)
    rig_script.bpy.data.objects.remove.assert_not_called()


def test_bone_heat_is_retried_at_another_voxel_size_when_it_fails_for_most_bones(rig_script):
    """Airi's and Natalie's Trellis2 models (2026-10-02): at height/260 not one bone got a weight, yet the rig step said it was done."""
    def heat_proxy(source, arm, H, divisions):
        return f"proxy{divisions}", 50000, 0, (["a", "b", "c", "d"] if divisions == 260 else ["a"])

    rig_script.heat_proxy = heat_proxy
    proxy, faces, dropped, empty, tried = rig_script.weighted_proxy("shape", "arm", 1.7, 6)
    assert (proxy, tried) == ("proxy200", [260, 200])
    assert rig_script.bpy.data.objects.remove.call_args_list == [mock.call("proxy260")]       # the failed one is thrown away


def test_a_rig_that_cannot_be_skinned_at_any_size_stops_with_the_sizes_tried(rig_script):
    rig_script.heat_proxy = lambda source, arm, H, divisions: (f"proxy{divisions}", 50000, 0, ["a", "b", "c", "d", "e", "f"])
    with pytest.raises(SystemExit, match=r"could not work out skin weights.*height/260, height/200, height/320"):
        rig_script.weighted_proxy("shape", "arm", 1.7, 6)
    assert rig_script.bpy.data.objects.remove.call_count == len(rig_script.PROXY_DIVISIONS)


def test_the_proxy_sizes_start_with_the_one_every_earlier_rig_used(rig_script):
    assert rig_script.PROXY_DIVISIONS[0] == 260 and len(set(rig_script.PROXY_DIVISIONS)) == len(rig_script.PROXY_DIVISIONS)


def test_bones_nothing_is_weighted_to_are_named_in_the_rig_notes(rig_script):
    assert rig_script.idle_note([]) is None
    assert rig_script.idle_note(["ring_02_l"]) == "1 bone moves nothing (no skin weights): ring_02_l"
    note = rig_script.idle_note([f"bone_{i}" for i in range(9)])
    assert note.startswith("9 bones move nothing") and "bone_5" in note and "bone_6" not in note and note.endswith("and 3 more")
