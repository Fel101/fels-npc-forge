"""Smoothing eye rings: the vision model's answer, the smoothing itself, and the job step."""
import copy
import threading
import types

import numpy as np
import pytest

from npcgen import cleanup3d, config, eyes, paint3d
from npcgen.jobs import Jobs
from test_cleanup3d import combine, make_glb, sphere, world  # noqa: F401  (world is a fixture)


# ---- the vision model's answer ------------------------------------------------------------------------------------------

def test_the_answer_gives_two_eye_boxes_left_to_right():
    text = ('<think>two eyes</think> ```json\n{"eyes": [{"side": "picture-right", "box": [563, 460, 670, 558]},'
            ' {"side": "picture-left", "box": [350, 458, 458, 556]}]}```')
    assert eyes.parse_boxes(text) == [[350, 458, 458, 556], [563, 460, 670, 558]]


@pytest.mark.parametrize("text", [
    "I cannot see any eyes.",
    '{"eyes": [{"box": [350, 458, 458, 556]}]}',                                              # one eye
    '{"eyes": [{"box": [350, 458, 458, 556]}, {"box": [360, 460, 470, 560]}]}',               # the same eye twice
    '{"eyes": [{"box": [350, 100, 458, 200]}, {"box": [563, 700, 670, 800]}]}',               # not level
    '{"eyes": [{"box": [100, 458, 458, 556]}, {"box": [563, 460, 600, 490]}]}',               # wildly different sizes
])
def test_an_answer_that_is_not_a_pair_of_eyes_is_refused(text):
    with pytest.raises(eyes.EyeError):
        eyes.parse_boxes(text)


def test_without_lm_studio_the_option_says_what_is_missing(monkeypatch):
    def refuse(*a, **k):
        raise eyes.EyeError("Cannot reach LM Studio")
    monkeypatch.setattr(eyes, "_call", refuse)
    eyes._CHECKED.clear()
    assert eyes.availability({})["missing"] == ["LM Studio, running with its local server on"]
    eyes._CHECKED.clear()
    monkeypatch.setattr(eyes, "_call", lambda cfg, path, body=None, timeout=30: {"models": [
        {"key": "google/gemma-4-e4b", "capabilities": {"vision": True}},
        {"key": "qwen/qwen3.8-27b", "capabilities": {"vision": True}}]})
    assert eyes.availability({}) == {"model": "qwen/qwen3.8-27b", "missing": []}    # the measured one, not the first
    eyes._CHECKED.clear()


# ---- smoothing ------------------------------------------------------------------------------------------------------------

def grid(x0, x1, y0, y1, z, n=80):
    xs, ys = np.meshgrid(np.linspace(x0, x1, n), np.linspace(y0, y1, n))
    pos = np.stack([xs.ravel(), ys.ravel(), np.full(xs.size, z)], 1)
    tri = []
    for i in range(n - 1):
        for j in range(n - 1):
            a, b, c, d = i * n + j, i * n + j + 1, (i + 1) * n + j, (i + 1) * n + j + 1
            tri += [[a, b, d], [a, d, c]]                 # facing +z
    return pos, np.array(tri)


def ring(cx, cy, cz, radius, tube, n=48, m=12):
    """A torus lying in the face's plane, standing out of it: the kind of loop the generator sculpts round an eye."""
    pos = []
    for i in range(n):
        u = 2 * np.pi * i / n
        for j in range(m):
            v = 2 * np.pi * j / m
            r = radius + tube * np.cos(v)
            pos.append([cx + r * np.cos(u), cy + r * np.sin(u), cz + tube * np.sin(v)])
    tri = []
    for i in range(n):
        for j in range(m):
            a, b = i * m + j, i * m + (j + 1) % m
            c, d = ((i + 1) % n) * m + j, ((i + 1) % n) * m + (j + 1) % m
            tri += [[a, c, d], [a, d, b]]
    return np.array(pos), np.array(tri)


def face_with_rings():
    face = grid(-0.2, 0.2, 0.75, 1.05, 0.3)
    body = sphere(0.35, centre=(0, 0.35, 0), rings=12, segments=24)
    left, right = ring(-0.05, 0.9, 0.305, 0.022, 0.004), ring(0.05, 0.9, 0.305, 0.022, 0.004)
    pos, tri = combine(face, body, left, right)
    return make_glb(pos, tri), len(face[0]) + len(body[0])


def test_rings_round_the_eyes_are_pressed_into_the_face_and_the_rest_is_left_alone():
    model, first_ring_vertex = face_with_rings()
    report = {}
    out = paint3d.Glb(eyes.smooth(model, [(-0.05, 0.9, 0.016, 0.024), (0.05, 0.9, 0.016, 0.024)], report))
    before = paint3d.Glb(model).accessor(0)
    after = out.accessor(out.json["meshes"][0]["primitives"][0]["attributes"]["POSITION"])
    rings = slice(first_ring_vertex, None)
    assert np.abs(before[rings, 2] - 0.3).max() > 0.008                   # the rings stood out of the face...
    assert np.abs(after[rings, 2] - 0.3).max() < 0.002                    # ...and now lie in it
    far = np.abs(before[:, 0]) > 0.15
    assert np.array_equal(after[far], before[far])                        # nothing far from the eyes moved
    assert report["eyes"]["vertices_moved"] > 0


# ---- the job step ---------------------------------------------------------------------------------------------------------

def test_the_options_are_planned_only_when_asked():
    npc = {"assets": {"sprite": {"file": "sprite.png", "created": "t"}, "model_3d": {"file": "model_3d.glb", "textured": True}}}
    assert [s.get("key") for s in Jobs.plan(npc, {"refine_3d": True}, {})] == [None]
    assert [s.get("key") for s in Jobs.plan(npc, {"refine_3d": True, "smooth_eyes": True}, {})] == ["eyes"]
    assert [s.get("key") for s in Jobs.plan(npc, {"sharp_face": True, "smooth_eyes": True}, {})] == ["face+eyes"]


def test_the_eye_step_finds_the_eyes_and_passes_them_to_the_clean_up(world, monkeypatch):  # noqa: F811
    lib, npc_id, run = world
    got = {}
    monkeypatch.setattr(eyes, "locate", lambda cfg, model, front, comfy=None, cancel=None:
                        ([(-0.02, 0.4, 0.01, 0.01), (0.02, 0.4, 0.01, 0.01)], {"boxes": [[1, 2, 3, 4]], "model": "qwen/qwen3.6-35b-a3b"}))

    def fake_refine(cfg, model, views, faces=None, cancel=None, closeups=None, eyes=None, hair=None, hands=None):
        got.update(eyes=eyes, closeups=closeups)
        return b"game", None, {"inner_skin": {"faces_before": 10, "faces_removed": 2}, "game_version": {"faces_after": 8},
                               "paint": {"views": {}}, "eyes": {"vertices_moved": 5, "faces_turned": 1, "seconds": 1}}
    monkeypatch.setattr(cleanup3d, "refine", fake_refine)
    job = types.SimpleNamespace(npc_id=npc_id, cancel=threading.Event(), cache={}, ref=None, options={"quality": "standard", "seed": None})
    Jobs(copy.deepcopy(config.DEFAULTS), None, lib)._run_step(job, {"kind": "refine_3d", "key": "eyes", "label": "x"})
    refined = lib.get(npc_id)["assets"]["model_3d"]["refined"]
    assert got["eyes"][0] == (-0.02, 0.4, 0.01, 0.01) and got["closeups"] is None
    assert refined["eyes"] == {"boxes": [[1, 2, 3, 4]], "model": "qwen/qwen3.6-35b-a3b", "vertices_moved": 5, "faces_turned": 1}
    assert "face" not in refined
