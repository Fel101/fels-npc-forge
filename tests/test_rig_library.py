"""The animation library: the clip choice (npcgen/rig3d.py and the rig_3d job step), its installer
(scripts/install_animations.py), and the maths that copies its clips onto our skeletons (npcgen/blender/rig_library.py),
checked against the real library files when they are installed."""
import copy
import importlib.util
import io
import json
import pathlib
import struct
import sys
import threading
import types
import zipfile

import numpy as np
import pytest

from npcgen import config, posekeys, rig3d, service
from npcgen.comfy import ComfyError
from npcgen.jobs import Jobs
from test_cleanup3d import world  # noqa: F401  (a fixture)
from test_rig3d import _FakeBlender

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "npcgen" / "blender"))
import rig_library as lib  # noqa: E402


def _installer():
    spec = importlib.util.spec_from_file_location("install_animations", ROOT / "scripts" / "install_animations.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _library_folder(tmp_path):
    for name in rig3d.LIBRARY_FILES:
        (tmp_path / name).write_bytes(b"glb")
    return {"animation_library": str(tmp_path)}


def test_the_clip_choice_is_the_library_when_it_is_installed(tmp_path):
    assert [c["id"] for c in rig3d.clip_choices()] == ["basic", "library"]
    assert rig3d.clean_clips("library") == "library" and rig3d.clean_clips("everything") == "auto"
    empty = {"animation_library": str(tmp_path)}
    assert rig3d.library_files(empty) == [] and rig3d.clip_set("auto", empty) == "basic"
    assert "install_animations.py" in rig3d.library_availability(empty)["missing"][0]
    cfg = _library_folder(tmp_path)
    assert len(rig3d.library_files(cfg)) == 2 and rig3d.clip_set("auto", cfg) == "library"
    assert rig3d.clip_set("basic", cfg) == "basic" and rig3d.library_availability(cfg)["installed"]
    assert service.clean_options({"rig_clips": "library"}, config.DEFAULTS)["rig_clips"] == "library"
    assert service.clean_options({"rig_clips": "all"}, config.DEFAULTS)["rig_clips"] == "auto"
    assert service.clean_options({}, config.DEFAULTS)["rig_clips"] == "auto"


def test_a_library_rig_hands_blender_the_library_files(tmp_path, monkeypatch):
    fake = _FakeBlender()
    monkeypatch.setattr(rig3d, "find_blender", lambda cfg: "blender")
    monkeypatch.setattr(rig3d.subprocess, "Popen", fake)
    cfg = _library_folder(tmp_path)
    _glb, _poster, report = rig3d.run(cfg, b"model")
    args = fake.cmd[fake.cmd.index("--") + 1:]
    assert args[args.index("--library") + 1].split("|") == [str(tmp_path / n) for n in rig3d.LIBRARY_FILES]
    assert report["clip_set"] == "library"
    _glb, _poster, report = rig3d.run(cfg, b"model", clips="basic")
    assert "--library" not in fake.cmd and report["clip_set"] == "basic"


def test_choosing_the_library_before_installing_it_says_how_to(tmp_path, monkeypatch):
    monkeypatch.setattr(rig3d, "find_blender", lambda cfg: "blender")
    with pytest.raises(ComfyError, match="install_animations.py"):
        rig3d.run({"animation_library": str(tmp_path)}, b"model", clips="library")


def test_the_rig_step_records_where_the_clips_came_from(world, monkeypatch):  # noqa: F811
    library, npc_id, _run = world
    monkeypatch.setattr(posekeys, "availability", lambda comfy: {"model": None, "missing": ["SDPose"]})
    got = {}

    def fake_rig(cfg, model, cancel=None, shape_glb=None, skeleton="low", keypoints=None, clips="auto", hands=None):
        got["clips"] = clips
        return b"rigged", None, {"clips": ["Idle", "Walk", "Wave"], "clip_set": "library", "bones": 23,
                                 "clip_info": [{"name": "Idle", "seconds": 2.5, "loop": True,
                                                "source": "UAL1_Standard: Idle_Loop"}]}
    monkeypatch.setattr(rig3d, "run", fake_rig)
    job = types.SimpleNamespace(npc_id=npc_id, cancel=threading.Event(), cache={}, ref=None,
                                options={"quality": "standard", "seed": None, "rig_skeleton": "low", "rig_clips": "library"})
    Jobs(copy.deepcopy(config.DEFAULTS), None, library)._run_step(job, {"kind": "rig_3d", "label": "x"})
    rig = library.get(npc_id)["assets"]["rig_3d"]
    assert got["clips"] == "library" and rig["clips"] == ["Idle", "Walk", "Wave"]
    assert rig["clip_set"] == "library" and rig["clip_info"][0]["source"].endswith("Idle_Loop")
    assert rig["animation_library"]["licence"] == "CC0 1.0"


def _fake_glb(names):
    body = json.dumps({"asset": {"version": "2.0"}, "nodes": [{"name": n} for n in names]}).encode()
    body += b" " * (-len(body) % 4)
    return b"glTF" + (2).to_bytes(4, "little") + (20 + len(body)).to_bytes(4, "little") + \
        len(body).to_bytes(4, "little") + b"JSON" + body


def test_the_installer_keeps_the_two_clip_files_from_the_zips(tmp_path, monkeypatch):
    inst = _installer()
    monkeypatch.setattr(inst, "DEST", tmp_path / "animations")
    zips = []
    for pack in inst.PACKS:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr(pack["glb"], _fake_glb(inst.JOINTS))
            z.writestr(pack["licence"], "CC0 1.0 Universal")
            z.writestr(pack["glb"].replace("Unreal-Godot", "Unity").replace(".glb", ".fbx"), "not wanted")
        path = tmp_path / pack["zip"]
        path.write_bytes(buf.getvalue())
        zips.append(str(path))
    inst.install(zips)
    kept = sorted(p.name for p in (tmp_path / "animations").iterdir())
    assert kept == ["License_UAL1.txt", "License_UAL2.txt", "UAL1_Standard.glb", "UAL2_Standard.glb", "manifest.json"]
    manifest = json.loads((tmp_path / "animations" / "manifest.json").read_text("utf-8"))
    assert manifest["licence"] == "CC0 1.0" and set(manifest["files"]) == set(rig3d.LIBRARY_FILES)


def _moving_glb(forward, up, seconds):
    """A GLB whose root node moves (forward = +Z, up = +Y in glTF axes) over one clip, as the packs' *_RM files do."""
    data = struct.pack("<2f", 0.0, seconds) + struct.pack("<6f", 0, 0, 0, 0, up, forward)
    js = {"asset": {"version": "2.0"}, "nodes": [{"name": "Armature", "children": [1]}, {"name": "root"}],
          "buffers": [{"byteLength": len(data)}],
          "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": 8}, {"buffer": 0, "byteOffset": 8, "byteLength": 24}],
          "accessors": [{"bufferView": 0, "componentType": 5126, "count": 2, "type": "SCALAR"},
                        {"bufferView": 1, "componentType": 5126, "count": 2, "type": "VEC3"}],
          "animations": [{"name": "Walk_Loop", "samplers": [{"input": 0, "output": 1}],
                          "channels": [{"sampler": 0, "target": {"node": 1, "path": "translation"}}]},
                         {"name": "Idle_Loop", "samplers": [{"input": 0, "output": 0}], "channels": []}]}
    body = json.dumps(js).encode()
    body += b" " * (-len(body) % 4)
    total = 12 + 8 + len(body) + 8 + len(data)
    return (b"glTF" + struct.pack("<II", 2, total) + struct.pack("<I", len(body)) + b"JSON" + body +
            struct.pack("<I", len(data)) + b"BIN\x00" + data)


def test_the_installer_measures_how_far_each_clip_moves_the_mannequin():
    moves = _installer().root_motion(_moving_glb(1.3, 0.0, 1.333))
    assert moves == {"Walk_Loop": {"forward_m": 1.3, "up_m": 0.0, "left_m": 0.0, "seconds": 1.333}}


def test_a_characters_clips_carry_their_speed_scaled_to_its_legs():
    walk = {"forward_m": 1.3, "up_m": 0.0, "left_m": 0.0, "seconds": 1.3}
    assert lib.movement(walk, 0.9, True) == {"moves": {"forward_m": 1.17, "up_m": 0.0, "left_m": 0.0}, "speed_m_s": 0.9}
    assert "speed_m_s" not in lib.movement({**walk, "up_m": 1.0}, 1.0, False)
    assert lib.movement(None, 1.0, True) == {}


def test_the_installer_refuses_a_file_without_the_librarys_rig(tmp_path, monkeypatch):
    inst = _installer()
    monkeypatch.setattr(inst, "DEST", tmp_path / "animations")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(inst.PACKS[0]["glb"], _fake_glb(["Bone", "Bone.001"]))
        z.writestr(inst.PACKS[0]["licence"], "x")
    (tmp_path / "a.zip").write_bytes(buf.getvalue())
    with pytest.raises(RuntimeError, match="rig"):
        inst.install([str(tmp_path / "a.zip"), str(tmp_path / "a.zip")])


# ---------------------------------------------------------------- the maths, on the library's own rig

def test_quaternion_helpers():
    q = lib.qbetween(np.array([1.0, 0, 0]), np.array([0, 1.0, 0]))
    assert np.allclose(lib.qrot(q, np.array([1.0, 0, 0])), [0, 1, 0])
    assert np.allclose(lib.qmul(q, lib.qinv(q)), [0, 0, 0, 1])
    half = lib.qpow(q, 0.5)
    assert np.allclose(lib.qmul(half, half), q)
    tw = lib.twist(lib.qmul(q, np.array([0, np.sin(0.3), 0, np.cos(0.3)])), np.array([0, 1.0, 0]))
    assert np.allclose(np.abs(tw[1]), np.sin(0.3), atol=0.2)            # mostly the turn about Y
    flipped = np.array([[0, 0, 0, 1.0], [0, 0, 0, -1.0], [0, 0, 0, 1.0]])
    assert (lib.continuous(flipped)[:, 3] > 0).all()


PARENT = {"pelvis": "root", "spine_01": "pelvis", "spine_02": "spine_01", "spine_03": "spine_02", "neck_01": "spine_03",
          "head": "neck_01"}
for _s in "lr":
    PARENT.update({f"clavicle_{_s}": "spine_03", f"upperarm_{_s}": f"clavicle_{_s}", f"lowerarm_{_s}": f"upperarm_{_s}",
                   f"hand_{_s}": f"lowerarm_{_s}", f"thigh_{_s}": "pelvis", f"calf_{_s}": f"thigh_{_s}",
                   f"foot_{_s}": f"calf_{_s}", f"ball_{_s}": f"foot_{_s}"})
    for _f in lib.FINGERS:
        PARENT.update({f"{_f}_01_{_s}": f"hand_{_s}", f"{_f}_02_{_s}": f"{_f}_01_{_s}", f"{_f}_03_{_s}": f"{_f}_02_{_s}"})


class _Bone:
    """What rig_library.Target reads of a Blender bone."""

    def __init__(self, name, parent, head, tail, rot):
        self.name, self.parent = name, parent
        self.head_local, self.tail_local = np.array(head, dtype=float), np.array(tail, dtype=float)
        q = types.SimpleNamespace(x=rot[0], y=rot[1], z=rot[2], w=rot[3])
        self.matrix_local = types.SimpleNamespace(to_quaternion=lambda: q)


class _Bones(list):
    def __getitem__(self, key):
        return next(b for b in self if b.name == key) if isinstance(key, str) else list.__getitem__(self, key)


def _library_rig(library, twist_bones=False):
    """A skeleton of ours standing exactly as the library's rig does."""
    bones = _Bones([_Bone("root", None, [0, 0, 0], [0, 0.1, 0], [0, 0, 0, 1.0])])
    names = list(PARENT)
    if twist_bones:                                  # the medium skeleton's, lying along their parents
        names += [f"{b}_twist_01_{s}" for s in "lr" for b in ("upperarm", "lowerarm")]
    for name in names:
        parent = PARENT.get(name) or name.replace("_twist_01", "")
        src, tip = lib.MAP[name] if name in lib.MAP else lib.MAP[parent]
        head = library.rest_pos[src]
        tail = library.rest_pos[tip] if tip else head + np.array([0, 0, 0.1])
        bones.append(_Bone(name, bones[parent], head, tail, library.rest_rot[src]))
    return types.SimpleNamespace(data=types.SimpleNamespace(bones=bones))


def _world(target, local):
    """Our bones' world turns rebuilt from the local turns, as Blender poses them."""
    out = {}
    for name in target.order:
        p = target.parent[name]
        if p is None:
            out[name] = lib.qmul(target.rest[name][None], local[name])
        else:
            rel = lib.qmul(lib.qinv(target.rest[p]), target.rest[name])
            out[name] = lib.qmul(out[p], lib.qmul(rel[None], local[name]))
    return out


def _same_turn(a, b):
    return np.abs(np.sum(a * b, -1)).min() > 0.9999


@pytest.fixture(scope="module")
def real_library():
    files = rig3d.library_files({})
    if not files:
        pytest.skip("the animation library is not installed (scripts/install_animations.py)")
    return lib.Library(files)


def test_the_library_is_read_with_every_clip(real_library):
    names = [c["name"] for c in real_library.clips]
    assert len(names) == len(set(names)) and "A_TPose" not in names
    assert {"Idle_Loop", "Walk_Loop", "Sitting_Idle_Loop", "Consume", "Idle_TalkingPhone_Loop"} <= set(names)
    rest = real_library.rest_pos
    assert rest["Head"][2] > rest["pelvis"][2] > rest["foot_l"][2] > 0          # standing, Z up
    assert rest["hand_l"][0] > 0.5 and rest["ball_l"][1] < rest["foot_l"][1]     # T-pose, left on +X, facing -Y


def test_a_clip_copied_onto_the_librarys_own_rig_turns_every_bone_as_the_library_does(real_library):
    target = lib.Target(_library_rig(real_library), real_library)
    assert abs(target.hip_scale - 1) < 1e-9
    for name in ("Walk_Loop", "Sitting_Enter", "Idle_TalkingPhone_Loop"):
        clip = real_library.clip(name)
        local, hips = lib.retarget(target, real_library, clip)
        world = _world(target, local)
        for bone, (src, _tip) in lib.MAP.items():
            assert _same_turn(world[bone], clip["rot"][src]), (name, bone)
        moved = clip["pos"]["pelvis"] - real_library.rest_pos["pelvis"]
        assert np.allclose(lib.qrot(target.rest["pelvis"][None], hips), moved, atol=1e-4), name


def test_the_forearm_roll_moves_to_the_hand_and_twist_bone_without_moving_the_hand(real_library):
    target = lib.Target(_library_rig(real_library, twist_bones=True), real_library)
    assert target.medium
    clip = real_library.clip("Idle_TalkingPhone_Loop")
    local, _hips = lib.retarget(target, real_library, clip)
    world = _world(target, local)
    for s in "lr":
        assert _same_turn(world[f"hand_{s}"], clip["rot"][f"hand_{s}"])
        roll = lib.twist(local[f"lowerarm_{s}"], np.array([0, 1.0, 0]))
        assert np.allclose(np.abs(roll[:, 3]), 1, atol=1e-6)            # the forearm itself only bends
