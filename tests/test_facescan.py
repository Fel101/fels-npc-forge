"""npcgen/facescan.py: finding a character's face (Blender and SDPose are stood in for), keeping the scan with the character, and the worker that does it."""
import json
import struct
import threading
import time

import pytest
from facefit_support import make_scan
from test_glbmerge import character

from npcgen import facescan, gpu
from npcgen.comfy import Cancelled, ComfyError
from npcgen.library import Library

PROFILE = {"first_name": "Face", "last_name": "Scan", "age": 30, "gender": "female", "occupation": "tester"}


@pytest.fixture
def lib(tmp_path):
    return Library(tmp_path / "library")


def make_npc(lib, kinds=("model_3d",), created="2026-10-01T10:00:00"):
    npc = lib.create({"setting": "modern", "style": "pixel", "seed": 1, "profile": PROFILE, "appearance": {}})
    glb = character()

    def apply(n):
        for kind in kinds:
            n.setdefault("assets", {})[kind] = {"file": f"{kind}.glb", "created": created, "size": len(glb)}
    for kind in kinds:
        lib.write_asset(npc["id"], f"{kind}.glb", glb)
    lib.update(npc["id"], apply)
    return npc["id"]


class Finder:
    """A stand-in for facescan.scan_model that counts its calls, can fail or wait."""

    def __init__(self, scan=None, error=None, wait=None):
        self.calls, self.scan, self.error, self.wait = [], scan, error, wait
        self.started = threading.Event()

    def __call__(self, cfg, comfy, glb, label, estimate=False, cancel=None, note=None):
        self.calls.append({"glb": len(glb), "label": label, "estimate": estimate})
        self.started.set()
        if note:
            note("Looking")
        if self.wait is not None:
            while not self.wait.is_set():
                if cancel is not None and cancel.is_set():
                    raise Cancelled()
                time.sleep(0.01)
        if self.error:
            raise self.error
        return json.loads(json.dumps(self.scan or make_scan()))


def faces_with(lib, finder, **more):
    return facescan.FaceScans({}, None, lib, find=finder, **more)


def finish(faces, npc_id, timeout=10):
    assert faces.wait_idle(timeout)
    return faces.status(npc_id)


# ---- what to scan -------------------------------------------------------------------------------------------------------------------------------
def test_the_rigged_model_is_the_one_a_fit_is_made_to_and_a_character_with_no_model_has_none():
    both = {"assets": {"model_3d": {"file": "a.glb"}, "rig_3d": {"file": "b.glb"}}}
    assert facescan.target(both)[:2] == ("rig_3d", "b.glb")
    assert facescan.target({"assets": {"model_3d": {"file": "a.glb"}}})[:2] == ("model_3d", "a.glb")
    assert facescan.target({"assets": {"rig_3d": {}}}) is None and facescan.target({}) is None


def test_a_character_with_no_model_cannot_be_scanned(lib):
    npc = lib.create({"setting": "modern", "style": "pixel", "seed": 1, "profile": PROFILE, "appearance": {}})
    faces = faces_with(lib, Finder())
    with pytest.raises(facescan.FaceScanError, match="no 3D model"):
        faces.request(npc["id"])
    assert faces.status(npc["id"])["model"] is None
    with pytest.raises(KeyError):
        faces.request("no-such-npc-123456")


# ---- scanning and keeping --------------------------------------------------------------------------------------------------------------------------
def test_a_scan_is_made_kept_with_the_character_and_not_made_again(lib):
    npc_id = make_npc(lib)
    finder = Finder()
    faces = faces_with(lib, finder)
    assert faces.status(npc_id)["state"] == "none" and faces.load(npc_id) is None
    first = faces.request(npc_id)
    assert first["state"] in ("queued", "running")
    done = finish(faces, npc_id)
    assert done["state"] == "done" and done["scan"]["source"] == "sdpose" and done["scan"]["ipd_mm"] == pytest.approx(63.0, abs=0.1)
    assert done["scan"]["model"]["kind"] == "model_3d" and done["model"]["file"] == "model_3d.glb"
    kept = json.loads(lib.read_asset(npc_id, facescan.CACHE_FILE))
    assert kept["target"] == facescan.stamp("model_3d", lib.get(npc_id)["assets"]["model_3d"]) and kept["created"]
    assert finder.calls == [{"glb": len(lib.read_asset(npc_id, "model_3d.glb")), "label": npc_id, "estimate": False}]
    again = faces.request(npc_id)                                                    # already there: nothing to do
    assert again["state"] == "done" and len(finder.calls) == 1
    assert faces.request(npc_id, force=True)["state"] in ("queued", "running", "done")
    finish(faces, npc_id)
    assert len(finder.calls) == 2
    faces.close()


def test_a_new_model_makes_the_old_scan_useless(lib):
    npc_id = make_npc(lib)
    faces = faces_with(lib, Finder())
    faces.request(npc_id)
    finish(faces, npc_id)
    assert faces.load(npc_id) is not None
    lib.update(npc_id, lambda n: n["assets"]["model_3d"].update(created="2026-10-02T09:00:00"))              # the model was made again
    assert faces.load(npc_id) is None
    status = faces.status(npc_id)
    assert status["scan"] is None and status["state"] == "none"
    faces.close()


def test_a_rigged_model_is_scanned_in_preference_and_a_new_rig_asks_for_a_new_scan(lib):
    npc_id = make_npc(lib, kinds=("model_3d", "rig_3d"))
    finder = Finder()
    faces = faces_with(lib, finder)
    faces.request(npc_id)
    status = finish(faces, npc_id)
    assert status["scan"]["model"]["kind"] == "rig_3d" and status["model"]["kind"] == "rig_3d"
    lib.update(npc_id, lambda n: n["assets"].pop("rig_3d"))                           # the rig was taken away: the plain model is what there is
    assert faces.load(npc_id) is None and faces.status(npc_id)["model"]["kind"] == "model_3d"
    faces.close()


def test_a_scan_asked_for_by_proportions_passes_that_on(lib):
    npc_id = make_npc(lib)
    finder = Finder(scan=make_scan(source="estimate"))
    faces = faces_with(lib, finder)
    faces.request(npc_id, estimate=True)
    status = finish(faces, npc_id)
    assert finder.calls[0]["estimate"] is True and status["scan"]["source"] == "estimate" and status["scan"]["score"] is None
    faces.close()


def test_the_page_hears_what_is_being_done_while_it_is_done(lib):
    npc_id = make_npc(lib)
    wait = threading.Event()
    faces = faces_with(lib, Finder(wait=wait))
    faces.request(npc_id)
    assert faces._idle.wait(0) is False
    deadline = time.monotonic() + 5
    while faces.status(npc_id)["message"] != "Looking" and time.monotonic() < deadline:
        time.sleep(0.01)
    status = faces.status(npc_id)
    assert status["state"] == "running" and status["message"] == "Looking"
    assert faces.request(npc_id)["state"] == "running"                                # asking again joins the one in progress
    wait.set()
    assert finish(faces, npc_id)["state"] == "done"
    faces.close()


def test_the_page_is_told_when_the_face_waits_for_a_job_that_has_comfyui(lib):
    npc_id = make_npc(lib)

    def find(cfg, comfy, glb, label, estimate=False, cancel=None, note=None):
        note("Finding the face")
        seen.append(faces.status(npc_id)["message"])
        note("Reading the shape of the face")
        seen.append(faces.status(npc_id)["message"])
        return make_scan()
    seen = []
    faces = facescan.FaceScans({}, None, lib, find=find, busy=lambda: True)
    faces.request(npc_id)
    assert faces.wait_idle(10)
    assert seen == ["Finding the face (ComfyUI is busy with a job: this waits for it)", "Reading the shape of the face"]
    faces.close()


# ---- failing, stopping, and going on ----------------------------------------------------------------------------------------------------------------
def test_a_failed_scan_says_why_and_the_next_one_still_runs(lib):
    bad, good = make_npc(lib), make_npc(lib)
    finder = Finder(error=ComfyError("Blender could not read the face: boom"))
    faces = faces_with(lib, finder)
    faces.request(bad)
    assert finish(faces, bad)["state"] == "error"
    assert "boom" in faces.status(bad)["message"] and faces.status(bad)["scan"] is None
    finder.error = None
    faces.request(good)
    assert finish(faces, good)["state"] == "done"
    faces.request(bad)                                                                # and the failed one can be asked for again
    assert finish(faces, bad)["state"] == "done"
    faces.close()


def test_a_running_scan_can_be_stopped_and_nothing_is_kept(lib):
    npc_id = make_npc(lib)
    wait = threading.Event()
    finder = Finder(wait=wait)
    faces = faces_with(lib, finder)
    faces.request(npc_id)
    assert finder.started.wait(5)
    faces.cancel(npc_id)
    status = finish(faces, npc_id)
    assert status["state"] == "cancelled" and status["scan"] is None
    with pytest.raises(FileNotFoundError):
        lib.read_asset(npc_id, facescan.CACHE_FILE)
    faces.close()


def test_a_scan_waiting_its_turn_can_be_taken_back(lib):
    first, second = make_npc(lib), make_npc(lib)
    wait = threading.Event()
    finder = Finder(wait=wait)
    faces = faces_with(lib, finder)
    faces.request(first)
    assert finder.started.wait(5)
    assert faces.request(second)["state"] == "queued"
    assert faces.cancel(second)["state"] == "cancelled"
    wait.set()
    finish(faces, first)
    assert [c["label"] for c in finder.calls] == [first]
    faces.close()


def test_a_character_deleted_while_it_waits_is_dropped_quietly(lib):
    first, second = make_npc(lib), make_npc(lib)
    wait = threading.Event()
    finder = Finder(wait=wait)
    faces = faces_with(lib, finder)
    faces.request(first)
    assert finder.started.wait(5)
    faces.request(second)
    lib.delete(second)
    wait.set()
    assert faces.wait_idle(10)
    assert [c["label"] for c in finder.calls] == [first]
    faces.close()


def test_scans_are_one_at_a_time(lib):
    ids = [make_npc(lib) for _ in range(3)]
    running, peak, lock = [0], [0], threading.Lock()

    def find(cfg, comfy, glb, label, estimate=False, cancel=None, note=None):
        with lock:
            running[0] += 1
            peak[0] = max(peak[0], running[0])
        time.sleep(0.05)
        with lock:
            running[0] -= 1
        return make_scan()
    faces = facescan.FaceScans({}, None, lib, find=find)
    for npc_id in ids:
        faces.request(npc_id)
    assert faces.wait_idle(20)
    assert peak[0] == 1 and all(faces.status(i)["state"] == "done" for i in ids)
    faces.close()


# ---- what the page is shown -----------------------------------------------------------------------------------------------------------------------
def test_the_summary_is_small_and_has_no_grid():
    scan = make_scan(scale=1.1, head=1.2)                                              # (a figure 1.1 times a 1.7 m adult, with a face 1.2 times an adult's)
    scan["notes"], scan["seconds"], scan["created"], scan["target"] = ["a note"], 38.2, "2026-10-06T12:00:00", {"kind": "rig_3d"}
    summary = facescan.summary(scan)
    assert summary == {"source": "sdpose", "created": "2026-10-06T12:00:00", "seconds": 38.2, "ipd_mm": 75.6, "face_width_mm": 162.0, "score": 0.9,
                       "notes": ["a note"], "model": {"kind": "rig_3d"}}                # (the face, as if the figure were 1.7 m tall)
    assert "grid" not in json.dumps(summary)


# ---- scan_model: Blender and SDPose stood in for ------------------------------------------------------------------------------------------------------
class Blender:
    """Stands in for rig3d.run_script: writes what each mode of face_scan.py writes, and remembers how it was called."""

    def __init__(self, scan=None):
        self.calls, self.scan = [], scan or make_scan()

    def __call__(self, cfg, script, args, tmp, out, doing, failed, cancel=None, timeout=0):
        self.calls.append(args)
        if "--eyes" in args:                                                         # (the file is gone when the call is over)
            self.eyes = json.loads(__import__("pathlib").Path(args[args.index("--eyes") + 1]).read_text("utf-8"))
        out = __import__("pathlib").Path(out)
        out.parent.mkdir(parents=True, exist_ok=True)
        if args[0] == "views":
            from PIL import Image
            Image.new("RGB", (64, 64), "white").save(out.parent / "front.png")
            out.write_text("{}", "utf-8")
        else:
            out.write_text(json.dumps(self.scan), "utf-8")
        return ""


def landmarked(**marks):
    """A GLB that says where its eyes are, the way a designed body does (JSON chunk only)."""
    raw = json.dumps({"asset": {"extras": {"landmarks": {"eye_l": [0.028, 1.486, 0.09], "eye_r": [-0.028, 1.486, 0.09], **marks}}}}).encode()
    raw += b" " * (-len(raw) % 4)
    return struct.pack("<4sII", b"glTF", 2, 20 + len(raw)) + struct.pack("<I4s", len(raw), b"JSON") + raw


def run_scan_model(monkeypatch, face=None, error=None, estimate=False, cancel=None, glb=b"glb"):
    blender = Blender()
    monkeypatch.setattr(facescan.rig3d, "run_script", blender)

    def face_points(comfy, picture, label, key, cancel=None):
        if error:
            raise error
        return face or [[10.0, 10.0, 0.9]] * 68
    monkeypatch.setattr(facescan.headgraft, "face_points", face_points)
    said = []
    scan = facescan.scan_model({}, object(), glb, "someone", estimate=estimate, cancel=cancel, note=said.append)
    return scan, blender, said


def test_the_face_is_found_in_a_picture_and_lifted_onto_the_model(monkeypatch):
    scan, blender, said = run_scan_model(monkeypatch)
    assert [c[0] for c in blender.calls] == ["views", "scan"]
    assert "--landmarks" in blender.calls[1] and "--views" in blender.calls[1] and scan["notes"] == [] and scan["seconds"] >= 0
    assert said == ["Drawing the head to look at it", "Finding the face", "Reading the shape of the face"]


def test_a_face_the_detector_is_unsure_of_is_said(monkeypatch):
    unsure = make_scan()
    unsure["scores"] = [0.4] * 68
    blender = Blender(scan=unsure)
    monkeypatch.setattr(facescan.rig3d, "run_script", blender)
    monkeypatch.setattr(facescan.headgraft, "face_points", lambda *args, **kwargs: [[10.0, 10.0, 0.4]] * 68)
    scan = facescan.scan_model({}, object(), b"glb", "someone")
    assert len(scan["notes"]) == 1 and "not sure" in scan["notes"][0] and "0.40" in scan["notes"][0]


def test_no_face_in_the_picture_means_proportions_with_a_note(monkeypatch):
    scan, blender, _ = run_scan_model(monkeypatch, error=facescan.headgraft.GraftError("SDPose found no face in the front"))
    assert "--landmarks" not in blender.calls[1]
    assert len(scan["notes"]) == 1 and "no face" in scan["notes"][0] and "not found" in scan["notes"][0]


def test_comfyui_not_running_is_not_a_failure_either(monkeypatch):
    scan, _blender, _ = run_scan_model(monkeypatch, error=ComfyError("Cannot reach ComfyUI at http://127.0.0.1:8188 (refused)"))
    assert len(scan["notes"]) == 1 and "Cannot reach ComfyUI" in scan["notes"][0]


def test_asking_for_proportions_draws_no_picture_and_uses_no_gpu(monkeypatch):
    scan, blender, said = run_scan_model(monkeypatch, estimate=True, error=AssertionError("SDPose must not be asked"))
    assert [c[0] for c in blender.calls] == ["scan"] and "--landmarks" not in blender.calls[0]
    assert said == ["Reading the shape of the face"] and scan["notes"] == ["Placed by proportions, as asked"]


def test_a_model_that_says_where_its_eyes_are_needs_no_picture_and_no_detector(monkeypatch):
    scan, blender, said = run_scan_model(monkeypatch, error=AssertionError("SDPose must not be asked"), glb=landmarked())
    assert [c[0] for c in blender.calls] == ["scan"] and "--landmarks" not in blender.calls[0] and "--views" not in blender.calls[0]
    assert blender.eyes == {"left": [0.028, 1.486, 0.09], "right": [-0.028, 1.486, 0.09]}                    # (as the model says them: its own space)
    assert said == ["Reading the shape of the face (the eyes are the model's own)"] and scan["notes"] == []


def test_proportions_asked_for_win_over_a_models_own_eyes(monkeypatch):
    scan, blender, said = run_scan_model(monkeypatch, estimate=True, error=AssertionError("SDPose must not be asked"), glb=landmarked())
    assert "--eyes" not in blender.calls[0] and scan["notes"] == ["Placed by proportions, as asked"] and said == ["Reading the shape of the face"]


def test_what_a_model_says_about_its_eyes_is_believed_only_when_it_makes_sense():
    assert facescan.own_eyes(landmarked()) == {"left": [0.028, 1.486, 0.09], "right": [-0.028, 1.486, 0.09]}
    assert facescan.own_eyes(landmarked(eye_l=["0.03", 1.5, 0.1])) == {"left": [0.03, 1.5, 0.1], "right": [-0.028, 1.486, 0.09]}
    for bad in (b"glb", b"", landmarked(eye_l=[0.028, 1.486]), landmarked(eye_l=[float("nan"), 1.486, 0.09]), landmarked(eye_r="front"),
                landmarked(eye_l=[-0.05, 1.486, 0.09]),                                       # (the wearer's left is +x: left and right swapped)
                landmarked(eye_l=[0.01, 1.486, 0.09], eye_r=[0.01, 1.486, 0.09]), landmarked(eye_l=None)):
        assert facescan.own_eyes(bad) is None, bad
    doc = struct.pack("<4sII", b"glTF", 2, 20) + struct.pack("<I4s", 2, b"JSON") + b"{}"
    assert facescan.own_eyes(doc) is None                                                    # (no landmarks at all)


def test_a_stopped_scan_is_not_turned_into_proportions(monkeypatch):
    with pytest.raises(Cancelled):
        run_scan_model(monkeypatch, error=Cancelled())


# ---- the GPU is left as it was found -----------------------------------------------------------------------------------------------------------------
class FakeLock:
    def __init__(self, busy=False):
        self.busy, self.held = busy, 0

    def acquire(self, cancel=None, timeout=None):
        if self.busy:
            raise facescan.LockBusy("busy")
        self.held += 1

    def release(self):
        self.held -= 1


class FakeComfy:
    def __init__(self, busy=False):
        self.operation_lock = FakeLock(busy)


def test_comfyui_is_only_unloaded_after_a_scan_that_used_it(lib, monkeypatch):
    released = []
    monkeypatch.setattr(gpu, "release_comfy", lambda comfy: released.append(1))
    npc_id = make_npc(lib)
    for source, unloaded in (("landmarks", 0), ("estimate", 0), ("sdpose", 1)):
        released.clear()
        faces = facescan.FaceScans({}, FakeComfy(), lib, find=Finder(scan=make_scan(source=source)))
        faces.request(npc_id, force=True)
        finish(faces, npc_id)
        faces.close()
        assert len(released) == unloaded, source
    released.clear()
    failing = facescan.FaceScans({}, FakeComfy(), lib, find=Finder(error=ComfyError("boom")))        # (a scan that failed may have had it loaded)
    failing.request(npc_id, force=True)
    finish(failing, npc_id)
    failing.close()
    assert len(released) == 1


def test_comfyui_is_unloaded_afterwards_only_when_nothing_else_is_using_it(lib, monkeypatch):
    released = []
    monkeypatch.setattr(gpu, "release_comfy", lambda comfy: released.append(comfy))
    comfy = FakeComfy()
    facescan.FaceScans({}, comfy, lib, find=Finder())._release_gpu()
    assert released == [comfy] and comfy.operation_lock.held == 0
    released.clear()
    facescan.FaceScans({}, comfy, lib, find=Finder(), busy=lambda: True)._release_gpu()             # the job worker has work: it will unload when it is done
    facescan.FaceScans({}, FakeComfy(busy=True), lib, find=Finder())._release_gpu()                 # a run is in progress
    facescan.FaceScans({}, None, lib, find=Finder())._release_gpu()
    assert released == []
