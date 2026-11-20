"""The shape side of the game-model step, as the maker sees it: the Blender command's flags, the picture's facing on a creature, the
refusal's reason reaching the user, and no stale game model left for the pack. Nothing here runs Blender (test_prep_blender.py does)."""
import json
import threading
from pathlib import Path

import pytest

from npcgen import creaturemaker, creatures
from npcgen.comfy import ComfyError


class FakeLibrary:
    def __init__(self, root):
        self.root = Path(root)


class FakeJob:
    def __init__(self):
        self.cancel = threading.Event()
        self.beats = []

    def beat(self, text):
        self.beats.append(text)


class FakeWorker:
    def __init__(self, store):
        self.creatures = store


def fields(**changes):
    base = {"name": "Spitter", "body_plan": "serpentine", "length_m": 2.0, "segments": 12, "seed": 202, "prompt": "a worm",
            "shore": "", "key": "spitter-test"}
    base.update(changes)
    return base


def test_a_creature_faces_left_by_default_and_the_facing_is_checked():
    assert creatures.clean_creature(fields())["facing"] == "left"
    assert creatures.clean_creature(fields(facing="Right"))["facing"] == "right"
    with pytest.raises(ValueError, match="left or right"):
        creatures.clean_creature(fields(facing="front"))


def test_the_facing_is_editable_so_the_page_can_change_it():
    assert "facing" in creatures.EDITABLE


def test_the_step_passes_the_facing_and_the_serpents_shortest_length():
    raw, out = Path("raw.glb"), Path("out.glb")
    serpent = creaturemaker.prep_command({"id": "s-1", "length_m": 2.0, "segments": 12, "body_plan": "serpentine",
                                          "facing": "right"}, raw, out, "serpentine")
    assert serpent[serpent.index("--facing") + 1] == "right"
    assert serpent[serpent.index("--min-elongation") + 1] == "2.2"
    older = creaturemaker.prep_command({"id": "s-2", "length_m": 2.0, "segments": 12, "body_plan": "serpentine"}, raw, out, "serpentine")
    assert older[older.index("--facing") + 1] == "left"                              # records made before the facing existed


def test_a_record_holding_null_for_the_budget_or_facing_gets_the_defaults():
    # The Road-hound's record holds null for both (it was made before the fields existed). Blender would fail on the text "None".
    raw, out = Path("raw.glb"), Path("out.glb")
    command = creaturemaker.prep_command({"id": "h-2", "length_m": 2.5, "segments": 12, "body_plan": "quadruped",
                                          "tris": None, "facing": None}, raw, out, "static")
    assert command[command.index("--tris") + 1] == "20000"
    assert command[command.index("--facing") + 1] == "left"


def test_a_static_body_plan_has_no_elongation_floor():
    raw, out = Path("raw.glb"), Path("out.glb")
    command = creaturemaker.prep_command({"id": "h-1", "length_m": 2.5, "segments": 12, "body_plan": "six-leg"}, raw, out, "static")
    assert "--min-elongation" not in command and "--facing" in command


class FakeProcess:
    """Stands in for the Blender process. It writes its log, and on success the output file the command names (the way Blender
    does), then exits with `code`. `report` is the REPORT line a success prints."""
    text = ""
    code = 1
    report = ""

    def __init__(self, command, stdout=None, stderr=None):
        self.command = command
        stdout.write(self.text)
        self.returncode = self.code
        if self.code == 0:
            out = Path(command[command.index("--output") + 1])
            # Blender's exporter adds .glb to a name that lacks it, so the step must write to a name that already ends in .glb
            assert out.suffix == ".glb", f"the output name {out.name} would become {out.name}.glb in Blender"
            out.write_bytes(b"glb")
            stdout.write(self.report)

    def poll(self):
        return self.returncode

    def wait(self):
        return self.returncode

    def kill(self):
        pass


def staged_creature(tmp_path):
    store = creatures.CreatureStore(FakeLibrary(tmp_path))
    creature = store.create(creatures.clean_creature(fields()))
    store.write_file(creature["id"], "raw/model.glb", b"raw")
    return store, creature, FakeWorker(store)


def test_a_coil_is_refused_with_the_shapes_own_reason(tmp_path, monkeypatch):
    store, creature, worker = staged_creature(tmp_path)
    FakeProcess.code = 1
    FakeProcess.text = ("REFUSED: this picture is a coil, not a serpent: its body is 1.01 times as long as it is wide or high "
                        "(at least 2.2 is needed). Redraw it from the side, stretched out, and make the 3D model again.\n")
    monkeypatch.setattr(creaturemaker.subprocess, "Popen", FakeProcess)
    with pytest.raises(ComfyError) as caught:
        creaturemaker.run_prep(worker, FakeJob(), {"kind": "creature_prep"}, creature)
    # the message is the shape's reason itself, not the generic Blender failure that wraps a crash
    assert str(caught.value).startswith("this picture is a coil, not a serpent")
    assert "could not prepare" not in str(caught.value)


def test_a_refused_run_leaves_no_game_model_for_the_pack_to_ship(tmp_path, monkeypatch):
    store, creature, worker = staged_creature(tmp_path)
    cid = creature["id"]
    store.write_file(cid, "model.glb", b"glb-from-an-older-run")               # what a previous run left behind
    store.write_file(cid, "prep.json", json.dumps({"triangles_after": 5819}))
    FakeProcess.code = 1
    FakeProcess.text = "REFUSED: this picture is a coil, not a serpent: ...\n"
    monkeypatch.setattr(creaturemaker.subprocess, "Popen", FakeProcess)
    with pytest.raises(ComfyError):
        creaturemaker.run_prep(worker, FakeJob(), {"kind": "creature_prep"}, creature)
    assert not store.resolve(cid, "model.glb").exists()
    assert not store.resolve(cid, "prep.json").exists()
    assert not store.resolve(cid, "model.part.glb").exists()
    assert store.get(cid)["_on_disk"]["prep"] is False                              # the pack then says: make the game model first


def test_a_successful_run_replaces_the_game_model_and_records_the_drift(tmp_path, monkeypatch):
    store, creature, worker = staged_creature(tmp_path)
    cid = creature["id"]
    FakeProcess.code = 0
    FakeProcess.text = ""
    FakeProcess.report = ('REPORT: {"plan": "serpentine", "drift": {"x": 0.002, "y": 0.001, "z": 0.019}, "drift_limit": 0.04, '
                          '"elongation": 2.298, "triangles_after": 5819, "decimate": "collapse", "size_m": [0.751, 2.0, 0.763]}\n')
    monkeypatch.setattr(creaturemaker.subprocess, "Popen", FakeProcess)
    creaturemaker.run_prep(worker, FakeJob(), {"kind": "creature_prep"}, creature)
    assert store.resolve(cid, "model.glb").read_bytes() == b"glb"
    assert not store.resolve(cid, "model.part.glb").exists()
    saved = json.loads(store.read_file(cid, "prep.json"))
    assert saved["drift"] == {"x": 0.002, "y": 0.001, "z": 0.019} and saved["elongation"] == 2.298
    assert "output" not in saved


def test_a_crash_still_says_blender_could_not_prepare_the_model(tmp_path, monkeypatch):
    store, creature, worker = staged_creature(tmp_path)
    FakeProcess.code = 1
    FakeProcess.text = "Traceback (most recent call last):\nValueError: boom\n"
    monkeypatch.setattr(creaturemaker.subprocess, "Popen", FakeProcess)
    with pytest.raises(ComfyError, match="Blender could not prepare the model"):
        creaturemaker.run_prep(worker, FakeJob(), {"kind": "creature_prep"}, creature)


# ---- the up check, run when a static body is prepared (npcgen/up_check.py) ---------------------------------------------------------------

import io

from test_up_check import draw_matte, hound, triangles, write_glb


class FakeComfy:
    """The ComfyUI calls the up check makes: the upload of the concept picture, then the run that returns its matte (BiRefNet's)."""
    def __init__(self, matte=None, error=None):
        self.matte, self.error = matte, error

    def upload_image(self, png_bytes, filename, **_):
        return filename

    def run(self, graph, cancel=None, timeout=None):
        if self.error:
            raise self.error
        return self.matte


class PreparedBody(FakeProcess):
    """Stands in for Blender's prep of a body: it writes the body as a GLB to the output the command names, and reports as the step does."""
    body = None

    def __init__(self, command, stdout=None, stderr=None):
        self.command = command
        write_glb(Path(command[command.index("--output") + 1]), self.body)
        stdout.write('REPORT: {"plan": "static", "size_m": [1.0, 1.0, 1.0], "triangles_after": 4}\n')
        self.returncode = 0


def matte_png(parts):
    buffer = io.BytesIO()
    draw_matte(parts).save(buffer, "PNG")
    return buffer.getvalue()


def staged_body(tmp_path, body_plan):
    store = creatures.CreatureStore(FakeLibrary(tmp_path))
    creature = store.create(creatures.clean_creature(fields(name="Hound", body_plan=body_plan, key="hound-test")))
    store.write_file(creature["id"], "raw/model.glb", b"raw")
    store.write_file(creature["id"], "concept.png", b"picture")          # the bytes do not matter: ComfyUI cuts the creature out
    return store, creature


def test_a_static_body_is_checked_against_its_picture_when_it_is_prepared(tmp_path, monkeypatch):
    store, creature = staged_body(tmp_path, "six-leg")
    worker = FakeWorker(store)
    worker.comfy = FakeComfy(matte=matte_png(hound()))
    PreparedBody.body = triangles(hound(), turned_over=True)               # the model came out upside down
    monkeypatch.setattr(creaturemaker.subprocess, "Popen", PreparedBody)
    creaturemaker.run_prep(worker, FakeJob(), {"kind": "creature_prep"}, creature)
    saved = json.loads(store.read_file(creature["id"], "prep.json"))
    assert saved["up_check"]["verdict"] == "contradicted"


def test_a_static_body_whose_picture_can_not_be_cut_out_is_recorded_as_not_checked_and_still_prepared(tmp_path, monkeypatch):
    store, creature = staged_body(tmp_path, "prop")
    worker = FakeWorker(store)
    worker.comfy = FakeComfy(error=ComfyError("ComfyUI is not running"))
    PreparedBody.body = triangles(hound())
    monkeypatch.setattr(creaturemaker.subprocess, "Popen", PreparedBody)
    creaturemaker.run_prep(worker, FakeJob(), {"kind": "creature_prep"}, creature)
    cid = creature["id"]
    assert store.resolve(cid, "model.glb").is_file()                       # the game model is still made
    saved = json.loads(store.read_file(cid, "prep.json"))
    assert saved["up_check"]["verdict"] is None and "ComfyUI is not running" in saved["up_check"]["reason"]


def test_a_serpent_has_no_picture_check_because_its_elongation_is_its_check(tmp_path, monkeypatch):
    store, creature = staged_body(tmp_path, "serpentine")
    worker = FakeWorker(store)
    worker.comfy = FakeComfy(error=AssertionError("the picture check must not run for a serpent"))
    PreparedBody.body = triangles(hound())
    monkeypatch.setattr(creaturemaker.subprocess, "Popen", PreparedBody)
    creaturemaker.run_prep(worker, FakeJob(), {"kind": "creature_prep"}, creature)
    assert "up_check" not in json.loads(store.read_file(creature["id"], "prep.json"))


def test_a_picture_check_whose_reply_is_not_json_is_recorded_as_not_checked_and_the_prep_still_runs(tmp_path, monkeypatch):
    # ComfyUI can answer with text that is not JSON (json.JSONDecodeError is a ValueError): the prep must still finish.
    store, creature = staged_body(tmp_path, "prop")
    worker = FakeWorker(store)
    worker.comfy = FakeComfy(error=ValueError("Expecting value: line 1 column 1 (char 0)"))
    PreparedBody.body = triangles(hound())
    monkeypatch.setattr(creaturemaker.subprocess, "Popen", PreparedBody)
    creaturemaker.run_prep(worker, FakeJob(), {"kind": "creature_prep"}, creature)
    saved = json.loads(store.read_file(creature["id"], "prep.json"))
    assert saved["up_check"]["verdict"] is None and "Expecting value" in saved["up_check"]["reason"]
