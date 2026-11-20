"""The game-model step run in Blender (npcgen/blender/creature_prep.py) on the live library's raw models. Each run takes about a
minute. They are skipped when Blender 5.2 or a raw model is not there, so the rest of the suite runs anywhere.

The reedcoil is the failing case: its picture is a coil, and its first reduction shrank it. The Spitter is the case that must pass.
The Road-hound is the static case: it was made stood on its end, and must now lie down with its length level."""
import json
import subprocess
from pathlib import Path

import pytest

from npcgen import creaturemaker

ROOT = Path(__file__).resolve().parent.parent
LIBRARY = ROOT / "library" / ".npcforge-creatures"
BLENDER = Path(creaturemaker.blender_path())
DRIVER = Path(__file__).resolve().parent / "blender_reduce_check.py"


def raw_model(creature_id):
    raw = LIBRARY / creature_id / "raw" / "model.glb"
    if not (BLENDER.exists() and raw.exists()):
        pytest.skip("needs Blender 5.2 and the library's raw model")
    return raw


def run_prep(raw, out, plan, length, tris=20000, facing="left", min_elongation=None):
    command = [str(BLENDER), "--background", "--factory-startup", "--python", str(creaturemaker.PREP_SCRIPT), "--",
               "--input", str(raw), "--output", str(out), "--plan", plan, "--tris", str(tris), "--length", str(length),
               "--segments", "12", "--key", "test", "--facing", facing]
    if min_elongation is not None:
        command += ["--min-elongation", str(min_elongation)]
    proc = subprocess.run(command, capture_output=True, text=True, timeout=900)
    return proc, proc.stdout + proc.stderr


def report_of(text):
    line = next(line for line in text.splitlines() if line.startswith("REPORT:"))
    return json.loads(line[len("REPORT:"):])


def test_a_coil_is_refused_with_the_reason_and_writes_nothing(tmp_path):
    raw = raw_model("reedcoil-8a3f7f")
    out = tmp_path / "model.glb"
    proc, text = run_prep(raw, out, "serpentine", 2.5, min_elongation=2.2)
    assert proc.returncode != 0
    assert "REFUSED: this picture is a coil, not a serpent" in text
    assert "REPORT:" not in text and not out.exists()


def test_the_reedcoil_reduces_only_by_a_route_that_keeps_its_shape(tmp_path):
    raw = raw_model("reedcoil-8a3f7f")
    proc = subprocess.run([str(BLENDER), "--background", "--factory-startup", "--python", str(DRIVER), "--",
                           str(raw), "2.5", "20000"], capture_output=True, text=True, timeout=900)
    text = proc.stdout + proc.stderr
    line = next(line for line in text.splitlines() if line.startswith("REDUCE "))
    result = json.loads(line[len("REDUCE "):])
    assert result["route"] == "remesh+collapse"                       # the plain collapse took its width down by about half
    assert max(result["drift"].values()) <= 0.04
    assert result["triangles"] <= 20000


def test_a_budget_the_shape_cannot_keep_is_refused_not_forced(tmp_path):
    raw = raw_model("reedcoil-8a3f7f")
    proc = subprocess.run([str(BLENDER), "--background", "--factory-startup", "--python", str(DRIVER), "--",
                           str(raw), "2.5", "2000"], capture_output=True, text=True, timeout=900)
    text = proc.stdout + proc.stderr
    assert proc.returncode != 0
    assert "REFUSED: could not reduce the model to 2000 triangles and keep its shape" in text
    assert "REDUCE " not in text                                                    # no model is returned for it


def test_the_spitter_keeps_its_shape_and_reports_it(tmp_path):
    raw = raw_model("spitter-8c15ab")
    out = tmp_path / "model.glb"
    proc, text = run_prep(raw, out, "serpentine", 2.0, tris=6000, min_elongation=2.2)
    assert proc.returncode == 0, text[-800:]
    report = report_of(text)
    assert out.exists()
    assert max(report["drift"].values()) <= 0.04 and report["drift_limit"] == 0.04
    assert report["elongation"] >= 2.2
    assert report["triangles_after"] <= 6000
    assert "output" not in report
    assert abs(report["size_m"][1] - 2.0) < 0.02                      # the length is 2.0 m, along Blender's Y


def test_the_road_hound_lies_down_with_its_length_level(tmp_path):
    raw = raw_model("road-hound-d72ec0")
    out = tmp_path / "model.glb"
    proc, text = run_prep(raw, out, "static", 2.5, facing="left")
    assert proc.returncode == 0, text[-800:]
    report = report_of(text)
    x, length, height = report["size_m"]
    assert length == pytest.approx(2.5, abs=0.02)                     # the length is on Blender's Y
    assert height < length                                            # it is no longer taller than it is long
    assert report["raw_up"] == [0, -1, 0]                             # its back is on -Y in the raw model (measured 2026-10-17)
    assert max(report["drift"].values()) <= 0.04


def test_the_sand_crab_comes_out_with_its_back_up(tmp_path):
    # The Sand-crab's raw model has its back on -Y. The prep once took +Y for up and turned it upside down; now the mass says which way is
    # up. The renders are the independent check (the report's raw_up is the same measure the choice came from).
    raw = raw_model("sand-crab-6a15c8")
    out = tmp_path / "model.glb"
    proc, text = run_prep(raw, out, "static", 2.5, facing="left")
    assert proc.returncode == 0, text[-800:]
    report = report_of(text)
    assert report["raw_up"] == [0, -1, 0]


def test_the_sand_crab_is_as_long_as_asked_along_its_forward_axis(tmp_path):
    # The length is the body's forward length (Blender's Y, where the head is). The prep once scaled the longer horizontal side instead,
    # which for a crab is its claw span: the crab came out 2.5 m across and 1.84 m from head to back.
    raw = raw_model("sand-crab-6a15c8")
    out = tmp_path / "model.glb"
    proc, text = run_prep(raw, out, "static", 2.5, facing="left")
    assert proc.returncode == 0, text[-800:]
    report = report_of(text)
    assert report["size_m"][1] == pytest.approx(2.5, abs=0.02)
