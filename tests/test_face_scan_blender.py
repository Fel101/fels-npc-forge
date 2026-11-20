"""npcgen/blender/face_scan.py run for real in Blender (skipped where Blender is not installed): both modes on a stand-in character, so a change in Blender's
Python API or in the script's own space handling shows up here and not on somebody's first scan. Everything about SDPose and the fit is tested without Blender
(tests/test_facescan.py, tests/test_facefit.py)."""
import json
import pathlib

import pytest
from test_glbmerge import character

from npcgen import config, facefit, rig3d
from npcgen.facescan import SCRIPT

BLENDER = rig3d.find_blender(config.load())
pytestmark = pytest.mark.skipif(not BLENDER, reason="Blender is not installed")


def run(tmp_path, *args):
    out = pathlib.Path(args[2]) if args[0] == "scan" else pathlib.Path(args[2]) / "views.json"
    rig3d.run_script({"blender": BLENDER}, SCRIPT, [str(a) for a in args], tmp_path, out, "Reading the face", "Blender could not read the face", timeout=300)
    return out


def test_the_scan_by_proportions_is_made_in_the_models_own_space(tmp_path):
    model = tmp_path / "model.glb"
    model.write_bytes(character())                                  # a 1.7 m cylinder with a few nodes: no face, so the eyes go where the library's characters have them
    out = run(tmp_path, "scan", model, tmp_path / "scan.json")
    data = json.loads(out.read_text("utf-8"))
    assert data["version"] == 1 and data["source"] == "estimate" and data["points"] is None and data["face_width"] is None
    left, right = data["eyes"]["left"], data["eyes"]["right"]
    assert left[0] - right[0] == pytest.approx(0.065, abs=0.002)                       # 65 mm apart, the wearer's left at +x
    assert left[1] == pytest.approx(0.911 * 1.7, abs=0.005) and right[1] == pytest.approx(left[1], abs=1e-6)       # at the library's usual eye level
    assert left[2] == pytest.approx(0.2, abs=0.01)                                     # on the front of the cylinder (+z is the front in glTF)
    assert data["model"]["height"] == pytest.approx(1.7, abs=0.01)
    scan = facefit.Scan(data)                                                          # and the grid reads back: skin at the middle of the face, none off the side
    mid = (scan.left + scan.right) / 2
    assert scan.surface(mid[0], mid[1])[0] == pytest.approx(0.2, abs=0.005)
    assert scan.nx == 320 and scan.ny == 190 and scan.step == pytest.approx(0.001, abs=1e-6)


def test_the_eyes_a_model_gives_are_used_as_they_are_and_only_the_surface_is_read(tmp_path):
    model = tmp_path / "model.glb"
    model.write_bytes(character())
    eyes = tmp_path / "eyes.json"
    eyes.write_text(json.dumps({"left": [0.031, 1.52, 0.2], "right": [-0.031, 1.52, 0.2]}), "utf-8")           # glTF space, on the cylinder's front
    data = json.loads(run(tmp_path, "scan", model, tmp_path / "scan.json", "--eyes", eyes).read_text("utf-8"))
    assert data["source"] == "landmarks" and data["points"] is None and data["scores"] is None and data["face_width"] is None
    assert data["eyes"]["left"] == pytest.approx([0.031, 1.52, 0.2], abs=1e-6) and data["eyes"]["right"] == pytest.approx([-0.031, 1.52, 0.2], abs=1e-6)
    scan = facefit.Scan(data)
    mid = (scan.left + scan.right) / 2
    assert mid[1] == pytest.approx(1.52, abs=1e-6) and scan.surface(mid[0], mid[1])[0] == pytest.approx(0.2, abs=0.005)      # the grid is around THESE eyes


def test_the_head_is_drawn_from_the_front_for_the_face_detector(tmp_path):
    model = tmp_path / "model.glb"
    model.write_bytes(character())
    out = run(tmp_path, "views", model, tmp_path / "views", "--size", "256", "--samples", "4")
    info = json.loads(out.read_text("utf-8"))
    assert info["size"] == [256, 256] and info["height"] == pytest.approx(1.7, abs=0.01)
    assert info["px"] == pytest.approx(0.22 * 1.7 / 256, rel=1e-3)                    # the head, the neck and the tops of the shoulders fill the picture
    from PIL import Image
    assert Image.open(tmp_path / "views" / "front.png").size == (256, 256)
