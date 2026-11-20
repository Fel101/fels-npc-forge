"""Joints from SDPose's keypoints (npcgen/posekeys.py) and the skeleton choice (npcgen/rig3d.py, the rig_3d step)."""
import copy
import json
import pathlib
import threading
import types

import pytest
from PIL import Image

from npcgen import config, posekeys, rig3d, roll, service
from npcgen.comfy import ComfyError
from npcgen.jobs import Jobs
from test_cleanup3d import world  # noqa: F401  (a fixture)


def person(wrist_l=(300, 500), wrist_r=(100, 500), hand_label_crossed=True, face_at=(200, 100)):
    """An SDPose person (ComfyUI's node output) with the body wrists where given and the two hands starting at them; the
    node's own hand labels the other way round when hand_label_crossed, as ComfyUI's does."""
    body = [[0, 0, 0.9]] * 18
    body[7], body[4] = [*wrist_l, 0.9], [*wrist_r, 0.9]
    left = [[wrist_l[0], wrist_l[1] + i, 0.9] for i in range(21)]
    right = [[wrist_r[0], wrist_r[1] + i, 0.9] for i in range(21)]
    face = [[face_at[0] + (i % 10) * 4, face_at[1] + (i // 10) * 4, 0.9] for i in range(70)]
    return {"pose_keypoints_2d": sum(body, []), "foot_keypoints_2d": [0, 0, 0.9] * 6, "face_keypoints_2d": sum(face, []),
            "hand_left_keypoints_2d": sum(right if hand_label_crossed else left, []),
            "hand_right_keypoints_2d": sum(left if hand_label_crossed else right, [])}


class FakeComfy:
    """Uploads pictures and answers each SDPose graph with the person for that picture."""

    def __init__(self, people):
        self.people, self.uploaded, self.graphs = people, [], []

    def upload_image(self, png, name):
        self.uploaded.append(name)
        return name

    def run_entry(self, graph, cancel=None, timeout=None):
        self.graphs.append(graph)
        outputs = {}
        for key, node in graph.items():
            if node["class_type"] == "PreviewAny":
                image = graph[graph[node["inputs"]["source"][0]]["inputs"]["image"][0]]["inputs"]["image"]
                view = next(v for v in self.people if f"_pose_{v}." in image)
                outputs[key] = {"text": [json.dumps([{"canvas_width": 300, "canvas_height": 400, "people": [self.people[view]]}])]}
        return {"outputs": outputs}


def views(tmp_path):
    folder = tmp_path / "views"
    folder.mkdir()
    cams = {}
    for v in posekeys.VIEWS:
        Image.new("RGB", (300, 400), (230, 230, 230)).save(folder / f"{v}.png")
        cams[v] = {"file": f"{v}.png", "size": [300, 400], "px": 0.005, "centre": [0, 0, 1], "right": [1, 0, 0],
                   "up": [0, 0, 1], "forward": [0, 1, 0]}
    (folder / "cams.json").write_text(json.dumps({"height_m": 1.7, "cams": cams}), "utf-8")
    return folder


def test_the_graph_reads_every_picture_with_one_sdpose_model():
    g = posekeys.graph(["a.png", "b.png"])
    assert [n["class_type"] for n in g.values()].count("CheckpointLoaderSimple") == 1
    assert g["k1"]["inputs"]["image"] == ["i1", 0] and g["k1"]["inputs"]["model"] == ["m", 0] and g["k1"]["inputs"]["vae"] == ["m", 2]
    assert g["o0"] == {"class_type": "PreviewAny", "inputs": {"source": ["k0", 0]}}
    assert g["m"]["inputs"]["ckpt_name"] == posekeys.CHECKPOINT


def test_without_sdpose_the_skeleton_says_what_is_missing():
    class Comfy:
        def get_json(self, path, timeout=None):
            return [] if path == "/models/checkpoints" else {}
    missing = posekeys.availability(Comfy())["missing"]
    assert any(posekeys.CHECKPOINT in m for m in missing) and any(posekeys.NODE in m for m in missing)
    assert posekeys.availability(None)["missing"] == ["ComfyUI"]


def test_hands_go_to_the_wrist_they_start_at_in_every_view(tmp_path):
    # ComfyUI's node labels the hands the other way round; in a side view both hands lie over the one that shows, so
    # the front view decides for all three
    front = person(hand_label_crossed=True)
    side = person(wrist_l=(150, 500), wrist_r=(152, 500), hand_label_crossed=True)
    comfy = FakeComfy({"front": front, "left": side, "right": side, "face": person()})
    keys = posekeys.keypoints(comfy, views(tmp_path), "mira")
    assert keys["views"]["front"]["hand_l"][0][:2] == [300, 500] and keys["views"]["front"]["hand_r"][0][:2] == [100, 500]
    assert keys["views"]["left"]["hand_l"][0][:2] == [150, 500]        # the node's "right hand", as in the front view
    grouped = lambda p: {part: [flat[i:i + 3] for i in range(0, len(flat), 3)] for part, flat in p.items()}
    assert posekeys.hands_crossed(grouped(front)) and not posekeys.hands_crossed(grouped(person(hand_label_crossed=False)))


def test_the_face_is_read_again_from_a_close_up_and_put_back_in_place(tmp_path):
    comfy = FakeComfy({"front": person(), "left": person(), "right": person(), "face": person(face_at=(10, 20))})
    keys = posekeys.keypoints(comfy, views(tmp_path), "mira")
    face = keys["views"]["front"]["face"]
    box = posekeys.face_box(face, (300, 400))
    assert len(face) == 68 and len(keys["views"]["front"]["face_zoom"]) == 68       # the node's two extra eye points dropped
    assert abs(box[2] / box[3] - 0.75) < 1e-9                                        # 3:4, SDPose's own shape
    assert keys["views"]["front"]["face_zoom"][0][:2] == pytest.approx([10 + box[0], 20 + box[1]])
    assert len(comfy.graphs) == 2 and posekeys.summary(keys)["hand_l"] == 0.9


def test_a_picture_with_nobody_in_it_is_a_readable_error(tmp_path):
    class Empty(FakeComfy):
        def run_entry(self, graph, cancel=None, timeout=None):
            return {"outputs": {k: {"text": [json.dumps([{"people": []}])]} for k, n in graph.items() if n["class_type"] == "PreviewAny"}}
    with pytest.raises(posekeys.PoseError, match="no person"):
        posekeys.keypoints(Empty({}), views(tmp_path), "mira")


# ---- the skeleton choice --------------------------------------------------------------------------------------------------

def test_skeletons_are_low_and_medium_and_anything_else_is_low():
    assert [k["id"] for k in rig3d.skeleton_choices()] == ["low", "medium"]
    assert rig3d.SKELETONS["low"]["bones"] == 23 and rig3d.SKELETONS["medium"]["bones"] == 60
    assert rig3d.clean_skeleton("medium") == "medium" and rig3d.clean_skeleton("high") == "low"
    assert service.clean_options({"rig_skeleton": "medium"}, config.DEFAULTS)["rig_skeleton"] == "medium"
    assert service.clean_options({"rig_skeleton": "huge"}, config.DEFAULTS)["rig_skeleton"] == "low"
    assert service.clean_options({}, config.DEFAULTS)["rig_skeleton"] == "low"


def test_the_medium_skeleton_needs_the_keypoints(monkeypatch):
    monkeypatch.setattr(rig3d, "find_blender", lambda cfg: "blender")
    with pytest.raises(ComfyError, match="medium skeleton needs SDPose"):
        rig3d.run({}, b"model", skeleton="medium")


class TwoScripts:
    """Stands in for subprocess.Popen: pose_views.py writes cams.json, rig_humanoid.py the rigged model and report."""

    def __init__(self):
        self.cmds = []

    def __call__(self, cmd, stdout=None, **kw):
        self.cmds.append(cmd)
        script = pathlib.Path(cmd[cmd.index("--python") + 1]).name
        args = cmd[cmd.index("--") + 1:]
        if script == "pose_views.py":
            out = pathlib.Path(args[1])
            out.mkdir(parents=True, exist_ok=True)
            (out / "cams.json").write_text("{}", "utf-8")
        else:
            open(args[1], "wb").write(b"rigged")
            open(args[args.index("--poster") + 1], "wb").write(b"png")
            keys = json.loads(pathlib.Path(args[args.index("--keypoints") + 1]).read_text("utf-8"))
            report = {"clips": ["Idle"], "skeleton": args[args.index("--skeleton") + 1], "joints_from": "pose keypoints",
                      "keys_seen": sorted(keys["views"])}
            open(args[args.index("--report") + 1], "w").write(json.dumps(report))

        class Proc:
            returncode = 0

            def poll(self):
                return 0

            def wait(self):
                return 0
        return Proc()


def test_with_keypoints_the_model_is_rendered_read_and_then_rigged(monkeypatch):
    fake = TwoScripts()
    monkeypatch.setattr(rig3d, "find_blender", lambda cfg: "blender")
    monkeypatch.setattr(rig3d.subprocess, "Popen", fake)
    seen = []
    points = {"views": {v: {"body": [[0, 0, 0.8]] * 18, "face": [[0, 0, 0.7]] * 68, "hand_l": [[0, 0, 0.9]] * 21,
                            "hand_r": [[0, 0, 0.9]] * 21} for v in posekeys.VIEWS}}

    def finder(folder):
        seen.append((folder / "cams.json").exists())
        return points
    glb, poster, report = rig3d.run({}, b"model", skeleton="medium", keypoints=finder)
    scripts = [pathlib.Path(c[c.index("--python") + 1]).name for c in fake.cmds]
    assert scripts == ["pose_views.py", "rig_humanoid.py"] and seen == [True]
    assert report["skeleton"] == "medium" and report["keys_seen"] == ["front", "left", "right"]
    assert report["keypoint_scores"] == {"body": 0.8, "face": 0.7, "hand_l": 0.9, "hand_r": 0.9}


def _rig(lib, npc_id, skeleton):
    job = types.SimpleNamespace(npc_id=npc_id, cancel=threading.Event(), cache={}, ref=None,
                                options={"quality": "standard", "seed": None, "rig_skeleton": skeleton})
    Jobs(copy.deepcopy(config.DEFAULTS), None, lib)._run_step(job, {"kind": "rig_3d", "label": "x"})
    return lib.get(npc_id)["assets"]["rig_3d"]


def test_the_rig_step_finds_the_joints_with_sdpose_when_it_is_there(world, monkeypatch):  # noqa: F811
    lib, npc_id, _run = world
    got = {}
    monkeypatch.setattr(posekeys, "availability", lambda comfy: {"model": posekeys.CHECKPOINT, "missing": []})
    monkeypatch.setattr(posekeys, "finder", lambda comfy, npc_id, cancel=None: "finder")

    def fake_rig(cfg, model, cancel=None, shape_glb=None, skeleton="low", keypoints=None, clips="auto", hands=None):
        got.update(skeleton=skeleton, keypoints=keypoints)
        return b"rigged", None, {"clips": ["Idle"], "bones": 60, "skeleton": skeleton, "joints_from": "pose keypoints",
                                 "keypoint_scores": {"body": 0.9}}
    monkeypatch.setattr(rig3d, "run", fake_rig)
    rig = _rig(lib, npc_id, "medium")
    assert got == {"skeleton": "medium", "keypoints": "finder"}
    assert (rig["skeleton"], rig["joints_from"], rig["bones"]) == ("medium", "pose keypoints", 60)
    assert rig["pose_model"]["model"] == posekeys.CHECKPOINT


def test_without_sdpose_a_low_skeleton_uses_the_outline_and_a_medium_one_stops(world, monkeypatch):  # noqa: F811
    lib, npc_id, _run = world
    monkeypatch.setattr(posekeys, "availability", lambda comfy: {"model": None, "missing": ["SDPose"]})
    monkeypatch.setattr(rig3d, "run", lambda cfg, model, cancel=None, shape_glb=None, skeleton="low", keypoints=None, clips="auto", hands=None:
                        (b"rigged", None, {"clips": ["Idle"], "bones": 23, "skeleton": skeleton,
                                           "joints_from": "pose keypoints" if keypoints else "outline"}))
    rig = _rig(lib, npc_id, "low")
    assert rig["joints_from"] == "outline" and any("SDPose could not be used" in n for n in rig["notes"])
    with pytest.raises(ComfyError, match="medium skeleton needs SDPose"):
        _rig(lib, npc_id, "medium")


def test_the_request_validator_accepts_a_skeleton_with_the_rig():
    assert service.clean_assets({"rig_3d": True}, roll.load_data())["rig_3d"] is True
