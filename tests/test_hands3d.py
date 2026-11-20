"""Clean hands (npcgen/hands3d.py, npcgen/blender/hand_shape.py, npcgen/blender/hands.py): the hand the Blender script
builds, the option's plumbing through the clean-up, and the rig using the new hands' own joints."""
import copy
import io
import json
import pathlib
import sys
import threading
import types

import numpy as np
import pytest
from PIL import Image

from npcgen import cleanup3d, config, hands3d, paint3d, posekeys, rig3d, roll, service
from npcgen.comfy import Cancelled, ComfyError
from npcgen.jobs import Jobs
from test_cleanup3d import box_with_uv, make_glb, png, world  # noqa: F401  (world is a fixture)

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "npcgen" / "blender"))
import hand_shape  # noqa: E402  (numpy only, like the Blender side that imports it)


# ---- the hand itself -------------------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def hand():
    return hand_shape.build()


def test_the_hand_is_one_closed_outward_facing_surface(hand):
    v, q, _kp = hand
    edges = np.sort(np.concatenate([q[:, [0, 1]], q[:, [1, 2]], q[:, [2, 3]], q[:, [3, 0]]]), 1)
    _pairs, uses = np.unique(edges, axis=0, return_counts=True)
    assert (uses == 1).sum() == 0                       # no holes (the wrist's end is closed too)
    assert (uses > 2).sum() <= 4                        # surface nets may touch two surfaces at a finger's root: the
    tri = np.concatenate([q[:, [0, 1, 2]], q[:, [0, 2, 3]]])   # Blender side remeshes those away
    a, b, c = v[tri[:, 0]], v[tri[:, 1]], v[tri[:, 2]]
    assert np.einsum("ij,ij->i", a, np.cross(b, c)).sum() / 6 > 0.03   # positive volume: faces point out


def test_the_hand_reaches_as_far_as_its_length_says(hand):
    v, _q, _kp = hand
    assert abs(v[:, 0].max() - hand_shape.REACH) < 0.02
    assert -0.26 < v[:, 0].min() < -0.2                  # the wrist runs back into the forearm, to be cut there


def test_the_keypoints_are_in_sdposes_order_with_the_thumb_on_its_own_side(hand):
    _v, _q, kp = hand
    assert kp.shape == (21, 3) and np.allclose(kp[0], 0)
    tips = {f: kp[4 + 4 * k] for k, f in enumerate(hand_shape.FINGERS)}
    knuckles = [kp[1 + 4 * k] for k in range(1, 5)]      # index, middle, ring, little finger
    assert [p[1] for p in knuckles] == sorted((p[1] for p in knuckles), reverse=True)   # from the thumb's side across
    assert max(tips, key=lambda f: tips[f][0]) == "middle" and min(tips, key=lambda f: tips[f][0]) == "thumb"
    assert tips["thumb"][1] > tips["index"][1]           # +Y is the thumb's side
    for k in range(5):                                   # each finger runs outwards, knuckle to tip
        chain = kp[1 + 4 * k:5 + 4 * k]
        assert all(np.linalg.norm(chain[j + 1] - chain[0]) > np.linalg.norm(chain[j] - chain[0]) for j in range(1, 3))


def test_relaxed_fingers_curl_towards_the_palm(hand):
    _v, _q, kp = hand
    for k in range(1, 5):
        knuckle, tip = kp[1 + 4 * k], kp[4 + 4 * k]
        assert tip[2] < knuckle[2] - 0.1                 # -Z is the palm's side


# ---- the Python side -------------------------------------------------------------------------------------------------

def _report():
    side = {"points": [[0.0, 0.0, 0.0]] * 21, "ring": {"centre": [0.1, 0, 0.5], "normal": [0, 0, -1]},
            "hand_length": 0.17, "notes": [], "cut_offset": 0.1, "faces_removed": 900}
    return {"l": dict(side), "r": {**side, "notes": ["its index and little finger were the wrong way round"]},
            "colour": {"from": "pictures", "skin_srgb": [0.7, 0.5, 0.4]}, "faces": 103_000, "seconds": 11.0}


def test_the_record_keeps_what_the_rig_needs():
    kept = hands3d.summary(_report())
    assert set(kept) == {"l", "r", "colour"}
    assert set(kept["l"]) == {"points", "ring", "length"} and kept["r"]["notes"]


def test_a_hand_that_cannot_be_made_leaves_the_model_as_it_was(monkeypatch):
    def fail(cfg, model, keys, cancel=None, skin=None):
        raise ComfyError("Clean hands: No arm was found at the left wrist")
    monkeypatch.setattr(hands3d, "replace", fail)
    assert hands3d.try_replace({}, b"game", None) == (b"game", None, {"error": "Clean hands: No arm was found at the left wrist"})

    def cancelled(cfg, model, keys, cancel=None, skin=None):
        raise Cancelled()
    monkeypatch.setattr(hands3d, "replace", cancelled)
    with pytest.raises(Cancelled):
        hands3d.try_replace({}, b"game", None)


class Scripts:
    """Stands in for subprocess.Popen: pose_views.py writes cams.json, hands.py the new model, poster and report."""

    def __init__(self):
        self.cmds = []

    def __call__(self, cmd, stdout=None, **kw):
        self.cmds.append(cmd)
        script = pathlib.Path(cmd[cmd.index("--python") + 1]).name
        args = cmd[cmd.index("--") + 1:]
        if script == "pose_views.py":
            pathlib.Path(args[1]).mkdir(parents=True, exist_ok=True)
            (pathlib.Path(args[1]) / "cams.json").write_text("{}", "utf-8")
        else:
            keys = json.loads(pathlib.Path(args[args.index("--keys") + 1]).read_text("utf-8"))
            open(args[1], "wb").write(b"with hands")
            open(args[args.index("--poster") + 1], "wb").write(b"png")
            open(args[args.index("--report") + 1], "w").write(json.dumps({**_report(), "keys_seen": sorted(keys["views"])}))

        class Proc:
            returncode = 0

            def poll(self):
                return 0

            def wait(self):
                return 0
        return Proc()


KEYS = {"views": {v: {"body": [[0, 0, 0.8]] * 18, "face": [[0, 0, 0.7]] * 68, "hand_l": [[0, 0, 0.9]] * 21,
                       "hand_r": [[0, 0, 0.9]] * 21} for v in posekeys.VIEWS}}


def test_the_model_is_rendered_read_by_sdpose_and_then_given_new_hands(monkeypatch):
    fake = Scripts()
    monkeypatch.setattr(rig3d, "find_blender", lambda cfg: "blender")
    monkeypatch.setattr(rig3d.subprocess, "Popen", fake)
    seen = []
    finder = types.SimpleNamespace(renders=lambda folder: seen.append((folder / "cams.json").exists()) or KEYS)
    keys = hands3d.find({}, b"model", finder)
    glb, poster, report = hands3d.replace({}, b"game", keys)
    scripts = [pathlib.Path(c[c.index("--python") + 1]).name for c in fake.cmds]
    assert scripts == ["pose_views.py", "hands.py"] and seen == [True] and (glb, poster) == (b"with hands", b"png")
    assert report["keys_seen"] == ["front", "left", "right"] and report["keypoint_scores"]["hand_l"] == 0.9
    height = [c[c.index("--height") + 1] for c in fake.cmds]
    assert height[0] == height[1]                        # the renders and the hands share one frame


def _plain_clean_up(monkeypatch):
    monkeypatch.setattr(cleanup3d, "remove_inner_skin", lambda model, report: (report.update(inner_skin={}), model)[1])
    monkeypatch.setattr(cleanup3d.unwrap, "needs_texture", lambda model: False)


def test_the_clean_up_finds_the_hands_first_paints_around_them_and_replaces_them_last(monkeypatch):
    _plain_clean_up(monkeypatch)
    calls = []
    points = {"l": np.zeros((22, 3)), "r": np.zeros((22, 3))}
    monkeypatch.setattr(hands3d, "find", lambda cfg, model, finder, cancel=None: calls.append(("find", model, finder)) or "keys")
    monkeypatch.setattr(hands3d, "points", lambda keys, positions: points)
    monkeypatch.setattr(hands3d, "picture_hands", lambda finder, views: ([0.6, 0.45, 0.35], {"front": "mask"}, {"pixels": 900}))
    monkeypatch.setattr(cleanup3d.paint3d, "all_triangles", lambda glb: (np.zeros((3, 3)), None))
    monkeypatch.setattr(cleanup3d.paint3d, "Glb", lambda data: data)

    def paint(model, views, report=None, closeups=None, blank=False, hair=None, hands=None, hand_pixels=None):
        calls.append(("paint", hands, hand_pixels))
        report["hands"] = {"side_left": 12}
        return model
    monkeypatch.setattr(cleanup3d.paint3d, "paint", paint)

    def lod(cfg, script, args, tmp, out, *a, **kw):
        calls.append("game version")
        pathlib.Path(out).write_bytes(b"game")
    monkeypatch.setattr(cleanup3d.rig3d, "run_script", lod)

    def replace(cfg, model, keys, cancel=None, skin=None):
        calls.append(("hands", model, keys, skin))
        return b"game with hands", b"poster", {"l": {}, "r": {}}
    monkeypatch.setattr(hands3d, "try_replace", replace)
    game, poster, report = cleanup3d.refine({}, b"generated", {"front": b"png"}, hands="finder")
    assert calls == [("find", b"generated", "finder"), ("paint", points, {"front": "mask"}), "game version",
                     ("hands", b"game", "keys", [0.6, 0.45, 0.35])]
    assert (game, poster) == (b"game with hands", b"poster") and "seconds" in report["hands"]
    assert report["paint"]["hands"] == {"side_left": 12}


def test_the_pictures_masks_are_not_used_where_the_models_own_forearm_is_unknown(monkeypatch):
    _plain_clean_up(monkeypatch)
    left = np.zeros((22, 3))
    left[21] = np.nan                                     # SDPose was unsure of the left elbow on the model
    monkeypatch.setattr(hands3d, "find", lambda cfg, model, finder, cancel=None: "keys")
    monkeypatch.setattr(hands3d, "points", lambda keys, positions: {"l": left, "r": np.zeros((22, 3))})
    monkeypatch.setattr(hands3d, "picture_hands", lambda finder, views: ([0.6, 0.45, 0.35], {"front": "mask"}, {"pixels": 900}))
    monkeypatch.setattr(cleanup3d.paint3d, "all_triangles", lambda glb: (np.zeros((3, 3)), None))
    monkeypatch.setattr(cleanup3d.paint3d, "Glb", lambda data: data)
    painted = {}

    def paint(model, views, report=None, closeups=None, blank=False, hair=None, hands=None, hand_pixels=None):
        painted.update(hands=hands, hand_pixels=hand_pixels)
        return model
    monkeypatch.setattr(cleanup3d.paint3d, "paint", paint)
    monkeypatch.setattr(hands3d, "try_replace", lambda cfg, model, keys, cancel=None, skin=None: (model, None, {}))
    monkeypatch.setattr(cleanup3d.rig3d, "run_script",
                        lambda cfg, script, args, tmp, out, *a, **kw: pathlib.Path(out).write_bytes(b"game"))
    _game, _poster, report = cleanup3d.refine({}, b"generated", {"front": b"png"}, hands="finder")
    assert painted["hand_pixels"] is None and painted["hands"]["l"] is left     # the 3D test still runs
    assert "left wrist or elbow" in report["hands_pictures"]["masks_unused"]
    assert hands3d.unprotected({"l": np.zeros((22, 3)), "r": np.zeros((22, 3))}) == []
    assert hands3d.unprotected({"l": np.zeros((21, 3))}) == ["l", "r"]


def test_hands_that_cannot_be_found_leave_the_rest_of_the_clean_up(monkeypatch):
    _plain_clean_up(monkeypatch)

    def find(cfg, model, finder, cancel=None):
        raise ComfyError("SDPose found no person in a render of the model")
    monkeypatch.setattr(hands3d, "find", find)
    monkeypatch.setattr(cleanup3d.rig3d, "run_script",
                        lambda cfg, script, args, tmp, out, *a, **kw: pathlib.Path(out).write_bytes(b"game"))
    game, _poster, report = cleanup3d.refine({}, b"generated", None, hands="finder")
    assert game == b"game" and report["hands"] == {"error": "SDPose found no person in a render of the model"}


def _pose_keys(points_blender):
    """keys as pose_views.py and posekeys.keypoints give them, with every point of both hands at the given 3D points
    (the pose views' frame), seen from the front and both sides (orthographic, 3:4)."""
    cams, views = {}, {}
    for name, forward in (("front", (0, 1, 0)), ("left", (-1, 0, 0)), ("right", (1, 0, 0))):
        f, up = np.array(forward, float), np.array([0, 0, 1.0])
        right = np.cross(f, up)
        cams[name] = {"size": [1536, 2048], "px": 0.001, "centre": [0, 0, 0.85], "right": right.tolist(),
                      "up": up.tolist(), "forward": f.tolist()}
        rel = np.asarray(points_blender) - np.array([0, 0, 0.85])
        uv = [[768 + p @ right / 0.001, 1024 - p @ up / 0.001, 0.95] for p in rel]
        views[name] = {"hand_l": uv, "hand_r": [list(q) for q in uv], "body": [list(uv[0])] * 18}
    return {"cams": cams, "views": views}


def test_pictures_that_cannot_be_read_leave_the_hands_as_they_were(monkeypatch):
    _plain_clean_up(monkeypatch)
    monkeypatch.setattr(hands3d, "find", lambda cfg, model, finder, cancel=None: "keys")
    monkeypatch.setattr(hands3d, "points", lambda keys, positions: {"l": "points"})
    monkeypatch.setattr(cleanup3d.paint3d, "all_triangles", lambda glb: (np.zeros((3, 3)), None))
    monkeypatch.setattr(cleanup3d.paint3d, "Glb", lambda data: data)

    def unreadable(finder, views):
        raise ComfyError("SDPose found no person in a render of the model")
    monkeypatch.setattr(hands3d, "picture_hands", unreadable)
    painted = {}

    def paint(model, views, report=None, closeups=None, blank=False, hair=None, hands=None, hand_pixels=None):
        painted.update(hands=hands, hand_pixels=hand_pixels)
        return model
    monkeypatch.setattr(cleanup3d.paint3d, "paint", paint)
    monkeypatch.setattr(hands3d, "try_replace", lambda *a, **k: pytest.fail("the hands must not be replaced"))
    monkeypatch.setattr(cleanup3d.rig3d, "run_script",
                        lambda cfg, script, args, tmp, out, *a, **kw: pathlib.Path(out).write_bytes(b"game"))
    game, _poster, report = cleanup3d.refine({}, b"generated", {"front": b"png"}, hands="finder")
    assert game == b"game" and report["hands"] == {"error": "SDPose found no person in a render of the model"}
    assert painted == {"hands": None, "hand_pixels": None}


def test_hands_coloured_from_the_old_paint_are_said_so(world, monkeypatch):  # noqa: F811
    lib, npc_id, _run = world
    monkeypatch.setattr(hands3d, "availability", lambda cfg, comfy: {"missing": []})
    monkeypatch.setattr(hands3d, "Finder", lambda comfy, npc_id, cancel=None: "finder")
    report = _report()
    report["colour"] = {"from": "old hand", "skin_srgb": [0.4, 0.3, 0.2]}
    monkeypatch.setattr(cleanup3d, "refine", _fake_refine(report, {}))
    step, refined = _refine(lib, npc_id)
    assert "old model's paint" in step["note"] and refined["hands"]["colour"]["from"] == "old hand"
    assert "not show the hands clearly" in step["note"] and refined["hands"]["colour_note"] in step["note"]


def test_the_colour_note_names_where_the_colour_came_from():
    assert hands3d.colour_note({"from": "pictures"}) is None
    assert "not show the hands clearly" in hands3d.colour_note({"from": "old hand"}, pictures=True)
    assert "no turnaround pictures" in hands3d.colour_note({"from": "old hand"}, pictures=False)
    plain = hands3d.colour_note({"from": "default"}, pictures=True)
    assert "plain skin tone" in plain and "clearly" not in plain
    report = _report()
    report["colour"] = {"from": "default", "skin_srgb": [0.62, 0.45, 0.36]}
    assert hands3d.summary(report)["colour_note"] == plain
    assert "colour_note" not in hands3d.summary(_report())


def test_the_hand_points_come_back_in_the_models_own_frame():
    positions = np.array([[2.5, 1.0, -1.5], [3.5, 3.0, -0.5]])   # 2 units tall on y = 1 (glTF: Y up), centred on x 3, z -1
    s = hands3d.HEIGHT_M / 2.0
    wanted = np.array([[3.2, 2.1, -0.8]] * 21)
    blender = np.stack([(wanted[:, 0] - 3) * s, -(wanted[:, 2] + 1) * s, (wanted[:, 1] - 1) * s], 1)
    got = hands3d.points(_pose_keys(blender), positions)
    assert got["l"].shape == (22, 3)                     # 21 hand points, then the elbow
    assert np.allclose(got["l"][:21], wanted, atol=1e-6) and np.allclose(got["r"][:21], wanted, atol=1e-6)
    assert np.allclose(got["l"][21], wanted[0], atol=1e-6)
    unsure = _pose_keys(blender)
    unsure["views"]["left"]["hand_l"][3][2] = 0.1
    assert np.isnan(hands3d.points(unsure, positions)["l"][3]).all()


def test_the_hands_in_the_pictures_give_their_colour_and_where_they_are():
    picture = Image.new("RGB", (200, 300), (255, 255, 255))
    picture.paste((150, 100, 80), (60, 100, 140, 200))          # a hand, on white, with a strip of sleeve in it
    picture.paste((40, 40, 40), (60, 100, 140, 110))
    buf = io.BytesIO()
    picture.save(buf, "PNG")
    ring = [[60 + 40 + 45 * np.cos(a), 150 + 55 * np.sin(a), 0.9] for a in np.linspace(0, 2 * np.pi, 21, endpoint=False)]
    finder = types.SimpleNamespace(pictures=lambda images: {v: {"hand_left_keypoints_2d": sum(ring, []),
                                                                 "hand_right_keypoints_2d": []} for v in images})
    colour, masks, report = hands3d.picture_hands(finder, {"front": buf.getvalue(), "back": buf.getvalue()})
    assert np.allclose(colour, np.array([150, 100, 80]) / 255, atol=0.01)
    assert report["hands_seen"] == 2 and set(masks) == {"front", "back"}
    m = masks["front"]
    # the hull of the ring runs from column 55 to 145 on row 150; grown by 1.2% of 300 pixels (4) all round
    assert m[150, 100] and m[150, 52] and m[150, 148] and not m[150, 45] and not m[20, 100]
    unsure = types.SimpleNamespace(pictures=lambda images: {v: {"hand_left_keypoints_2d": [c if i % 3 != 2 else 0.1
                                                                                          for i, c in enumerate(sum(ring, []))]}
                                                             for v in images})
    assert hands3d.picture_hands(unsure, {"front": buf.getvalue()})[:2] == (None, {})


def test_a_hand_in_front_of_the_body_is_not_painted_onto_it():
    pos, uv, tri = box_with_uv()                          # 1 x 2, facing +Z, its front face at z = 0.1
    model = make_glb(pos, tri, uv, png((255, 0, 255), (256, 256)))     # the generator's colour: magenta
    picture = Image.new("RGB", (200, 400), (255, 255, 255))
    picture.paste((20, 20, 200), (50, 100, 150, 300))    # the body, blue...
    picture.paste((220, 170, 140), (97, 170, 103, 215))  # ...with a finger hanging in front of it at x = 0, y 0.85-1.3
    buf = io.BytesIO()
    picture.save(buf, "PNG")
    hand = np.full((21, 3), np.nan)
    for i, y in ((0, 1.3), (5, 1.1), (9, 1.1), (17, 1.1), (10, 1.0), (11, 0.95), (12, 0.88)):
        hand[i] = [0.0, y, 0.16]                          # 6 cm in front of the body

    def texel(glb):                                       # the front face at x = 0, y = 1.05
        return np.asarray(paint3d.Glb(glb).image(0).convert("RGB")).astype(int)[121, 32]
    plain = texel(paint3d.paint(model, {"front": buf.getvalue()}))
    report = {}
    around = texel(paint3d.paint(model, {"front": buf.getvalue()}, report=report, hands={"l": hand}))
    assert plain[0] > 180 and plain[2] < 180             # without: the hand's skin, painted onto the body
    assert around[2] > 150 and around[0] < 80 and around[1] < 80    # with: the body's blue, not skin nor magenta
    assert report["hands"]["front"] > 0 and report["hands"]["filled"] > 0


def test_where_a_picture_draws_a_hand_its_colour_goes_to_the_models_hand_only():
    pos, uv, tri = box_with_uv()
    model = make_glb(pos, tri, uv, png((255, 0, 255), (256, 256)))
    picture = Image.new("RGB", (200, 400), (255, 255, 255))
    picture.paste((20, 20, 200), (50, 100, 150, 300))
    picture.paste((220, 170, 140), (97, 170, 103, 215))  # the picture draws a hand here, where the model has none
    buf = io.BytesIO()
    picture.save(buf, "PNG")
    mask = np.zeros((400, 200), bool)
    mask[165:220, 92:108] = True
    painted = paint3d.paint(model, {"front": buf.getvalue()}, hand_pixels={"front": mask})
    colour = np.asarray(paint3d.Glb(painted).image(0).convert("RGB")).astype(int)[121, 32]
    assert colour[2] > 150 and colour[0] < 80           # the body's blue, from around it


def test_the_models_own_hand_gives_no_colour_to_what_lies_behind_it(monkeypatch):
    pos, uv, tri = box_with_uv()
    uv = uv.copy()
    uv[:, 1] = 0.01 + (uv[:, 1] - 0.01) * 0.6           # the box in the texture's top 60%...
    plate = [[-0.02, 0.85, 0.16], [0.02, 0.85, 0.16], [0.02, 1.3, 0.16], [-0.02, 1.3, 0.16]]
    base = len(pos)                                       # ...and the model's own hand, 6 cm in front, its own island
    pos = np.concatenate([pos, plate])
    uv = np.concatenate([uv, [[0.02, 0.98], [0.2, 0.98], [0.2, 0.7], [0.02, 0.7]]])
    tri = np.concatenate([tri, [[base, base + 1, base + 2], [base, base + 2, base + 3]]])
    model = make_glb(pos, tri, uv, png((255, 0, 255), (256, 256)))
    picture = Image.new("RGB", (200, 400), (255, 255, 255))
    picture.paste((20, 20, 200), (50, 100, 150, 300))
    picture.paste((220, 170, 140), (98, 170, 102, 215))  # the hand, in front of the body
    buf = io.BytesIO()
    picture.save(buf, "PNG")
    hand = np.full((21, 3), np.nan)
    for i, y in ((0, 1.3), (5, 1.1), (9, 1.1), (17, 1.1), (10, 1.0), (11, 0.95), (12, 0.88)):
        hand[i] = [0.0, y, 0.16]
    sources, island_sources = [], []
    fill, island_fill = paint3d._fill_colour, paint3d._island_fill

    def spy_fill(points, known, *a, **k):
        sources.append(points[known])
        return fill(points, known, *a, **k)

    def spy_island(image, gap, island, known, *a, **k):
        island_sources.append(known.copy())
        return island_fill(image, gap, island, known, *a, **k)
    monkeypatch.setattr(paint3d, "_fill_colour", spy_fill)
    monkeypatch.setattr(paint3d, "_island_fill", spy_island)
    report = {}
    paint3d.paint(model, {"front": buf.getvalue()}, report=report, hands={"l": hand})
    assert report["hands"]["filled"] > 0 and sources and island_sources
    assert all(len(p) and (p[:, 2] < 0.15).all() for p in sources)     # the body's painted surface, not the hand's
    th, tw = island_sources[0].shape
    assert not island_sources[0][int(0.72 * th):int(0.96 * th), int(0.03 * tw):int(0.19 * tw)].any()


def test_uv_islands_are_the_triangles_joined_by_shared_vertices():
    tri = np.array([[0, 1, 2], [1, 2, 3], [4, 5, 6], [5, 6, 7], [7, 8, 9]])
    labels = paint3d.uv_islands(tri, 10)
    assert labels[0] == labels[1] and labels[2] == labels[3] == labels[4] and labels[0] != labels[2]


def test_a_gap_is_filled_from_its_own_surface_not_the_one_beside_it():
    image = np.zeros((20, 20, 3), np.float32)
    island = np.zeros((20, 20), np.int64)
    image[:, :10], island[:, :10] = (20, 20, 200), 1       # the thigh, blue, its own island...
    image[:, 10:], island[:, 10:] = (200, 120, 90), 2      # ...right next to the arm, skin
    gap = np.zeros((20, 20), bool)
    gap[5:15, 4:10] = True                                  # a hand-sized gap on the thigh, touching the arm
    image[gap] = (255, 0, 255)
    out = paint3d._island_fill(image, gap, island, ~gap)
    assert np.allclose(out[gap], (20, 20, 200), atol=1)    # the thigh's own blue all through
    assert np.allclose(out[:, 10:], (200, 120, 90))         # the arm untouched


def test_the_forearm_is_kept_off_the_body_behind_it_but_keeps_its_own_paint():
    hand = np.full((22, 3), np.nan)
    hand[0], hand[21] = [0.0, 1.0, 0.2], [0.0, 1.3, 0.2]     # wrist and elbow: a forearm 20 cm in front of the body
    bones, middles = paint3d._hand_bones({"l": hand})
    assert len(bones) == 1 and bones[0][2] == paint3d.FOREARM_REACH
    points = np.array([[0.0, 1.15, 0.0],                     # the body behind the forearm: refused
                       [0.0, 1.15, 0.24],                    # the forearm's own surface, 4 cm from its line: kept
                       [0.3, 1.15, 0.0]])                    # the body well to the side: kept
    assert paint3d._behind_hands(points, bones, middles, (0, 0, 1), 1.0).tolist() == [True, False, False]


def test_a_picture_hand_over_the_models_own_hand_still_paints_it():
    pos, uv, tri = box_with_uv()
    model = make_glb(pos, tri, uv, png((255, 0, 255), (256, 256)))
    picture = Image.new("RGB", (200, 400), (255, 255, 255))
    picture.paste((20, 20, 200), (50, 100, 150, 300))
    picture.paste((220, 170, 140), (97, 170, 103, 215))
    buf = io.BytesIO()
    picture.save(buf, "PNG")
    mask = np.zeros((400, 200), bool)
    mask[165:220, 92:108] = True
    hand = np.full((21, 3), np.nan)                       # the model's hand lies ON the box's front face here
    for i, y in ((0, 1.3), (9, 1.1), (10, 1.0), (11, 0.95), (12, 0.88)):
        hand[i] = [0.0, y, 0.1]
    painted = paint3d.paint(model, {"front": buf.getvalue()}, hands={"l": hand}, hand_pixels={"front": mask})
    colour = np.asarray(paint3d.Glb(painted).image(0).convert("RGB")).astype(int)[121, 32]
    assert colour[0] > 180 and colour[2] < 180           # the picture's hand paints the model's own hand


def test_the_painter_skips_what_a_picture_shows_behind_a_hand():
    hand = np.full((21, 3), np.nan)
    hand[0], hand[9], hand[10], hand[11], hand[12] = [0, 1, 0.2], [0, 0.9, 0.2], [0, 0.85, 0.2], [0, 0.8, 0.2], [0, 0.75, 0.2]
    bones, middles = paint3d._hand_bones({"l": hand})
    points = np.array([[0.0, 0.9, 0.0],                  # behind the hand: refused
                       [0.0, 0.9, 0.21],                 # the hand's own surface: kept
                       [0.2, 0.9, 0.0],                  # beside it, far across the picture: kept
                       [0.0, 0.9, 0.3],                  # in front of the hand: kept
                       [0.0, 0.70, 0.0]])                # behind the finger carried on past its tip: refused
    got = paint3d._behind_hands(points, bones, middles, (0, 0, 1), 1.0)   # the camera looks from +Z
    assert got.tolist() == [True, False, False, False, True]


def test_clothes_pressed_against_a_hand_are_not_the_hand():
    hand = np.full((21, 3), np.nan)
    hand[0], hand[9], hand[10], hand[11], hand[12] = [0, 1, 0.2], [0, 0.9, 0.2], [0, 0.85, 0.2], [0, 0.8, 0.2], [0, 0.75, 0.2]
    bones, middles = paint3d._hand_bones({"l": hand})
    points = np.array([[0.0, 0.9, 0.22],                  # the hand's own surface, 2 cm in front of its bone
                       [0.0, 0.9, 0.18]])                 # a skirt 2 cm behind the bone, pressed against the hand
    normals = np.array([[0, 0, 1.0], [0, 0, 1.0]])       # both face +Z: away from the bone, and towards it
    assert paint3d._near_bones(points, bones, 1.0).tolist() == [True, True]      # both lie in the hand's zone...
    assert paint3d._near_bones(points, bones, 1.0, normals=normals).tolist() == [True, False]   # ...one is the hand
    got = paint3d._behind_hands(points, bones, middles, (0, 0, 1), 1.0, normals)    # the camera looks from +Z
    assert got.tolist() == [False, True]                 # the skirt behind the hand gets no colour from this picture
    assert paint3d._behind_hands(points, bones, middles, (0, 0, 1), 1.0).tolist() == [False, False]   # (the zone alone)


def test_the_bone_tests_look_near_each_bone_only_and_agree_with_testing_every_point():
    rng = np.random.default_rng(7)
    hand = np.cumsum(rng.normal(0, 0.03, (22, 3)), 0) + [0.2, 0.9, 0.1]      # 21 hand points and an elbow
    bones, middles = paint3d._hand_bones({"l": hand})
    points = rng.uniform([-0.3, 0.4, -0.3], [0.7, 1.4, 0.5], (20000, 3))
    points[:500] = hand[rng.integers(0, 22, 500)] + rng.normal(0, 0.03, (500, 3))   # plenty near the bones too

    def every_point(reach=None):
        out = np.zeros(len(points), bool)
        for a, b, _across, zone in bones:
            ab = b - a
            u = np.clip((points - a).dot(ab) / ab.dot(ab), 0, 1)
            out |= np.linalg.norm(points - a - np.outer(u, ab), axis=1) < (zone if reach is None else reach)
        return out

    def behind_every_point(t):
        t = np.asarray(t, float)
        own, shown = np.zeros(len(points), bool), np.zeros(len(points), bool)
        for a, b, reach, zone in bones:
            ab, pa = b - a, points - a
            u = np.clip(pa.dot(ab) / ab.dot(ab), 0, 1)
            own |= np.linalg.norm(pa - np.outer(u, ab), axis=1) < zone
            pa2, ab2 = pa - np.outer(pa.dot(t), t), ab - t * ab.dot(t)
            u2 = np.clip(pa2.dot(ab2) / ab2.dot(ab2), 0, 1)
            across = np.linalg.norm(pa2 - np.outer(u2, ab2), axis=1)
            shown |= (across < reach) & ((a + np.outer(u2, ab)).dot(t) > points.dot(t) + 0.01)
        near = np.min([np.linalg.norm(points - m, axis=1) for m in middles], 0) < 0.5
        return shown & ~own & near

    assert paint3d._near_bones(points, bones, 1.0).tolist() == every_point().tolist()
    assert paint3d._near_bones(points, bones, 1.0, 0.15).tolist() == every_point(0.15).tolist()
    for _view, (_x, toward) in paint3d.VIEWS.items():
        want = behind_every_point(toward)
        assert want.any() and paint3d._behind_hands(points, bones, middles, toward, 1.0).tolist() == want.tolist()


# ---- the option in the app ------------------------------------------------------------------------------------------

def test_the_option_is_planned_only_when_asked():
    npc = {"assets": {"sprite": {"file": "sprite.png", "created": "t"}, "model_3d": {"file": "model_3d.glb", "textured": True}}}
    assert [s.get("key") for s in Jobs.plan(npc, {"clean_hands": True}, {})] == ["hands"]
    assert [s.get("key") for s in Jobs.plan(npc, {"match_hair": True, "clean_hands": True}, {})] == ["hair+hands"]
    assert "clean five-finger hands" in Jobs.plan(npc, {"clean_hands": True}, {})[0]["label"]
    assert service.clean_assets({"clean_hands": True}, roll.load_data())["clean_hands"] is True


def _refine(lib, npc_id):
    job = types.SimpleNamespace(npc_id=npc_id, cancel=threading.Event(), cache={}, ref=None, options={"quality": "standard", "seed": None})
    step = {"kind": "refine_3d", "key": "hands", "label": "x"}
    Jobs(copy.deepcopy(config.DEFAULTS), None, lib)._run_step(job, step)
    return step, lib.get(npc_id)["assets"]["model_3d"]["refined"]


def _fake_refine(hands_report, got):
    def fake(cfg, model, views, faces=None, cancel=None, closeups=None, eyes=None, hair=None, hands=None):
        got["hands"] = hands
        return b"game", None, {"inner_skin": {"faces_before": 10, "faces_removed": 2}, "game_version": {"faces_after": 8},
                               "paint": {"views": {}}, "hands": hands_report}
    return fake


def test_the_hands_step_passes_a_finder_and_keeps_the_new_hands_for_the_rig(world, monkeypatch):  # noqa: F811
    lib, npc_id, _run = world
    got = {}
    monkeypatch.setattr(hands3d, "availability", lambda cfg, comfy: {"missing": []})
    monkeypatch.setattr(hands3d, "Finder", lambda comfy, npc_id, cancel=None: "finder")
    monkeypatch.setattr(cleanup3d, "refine", _fake_refine(_report(), got))
    step, refined = _refine(lib, npc_id)
    assert got["hands"] == "finder" and "note" not in step
    assert refined["hands"]["l"]["ring"]["normal"] == [0, 0, -1] and refined["hands"]["pose_model"] == posekeys.CHECKPOINT


def test_hands_that_could_not_be_made_are_said_so_and_the_clean_up_is_kept(world, monkeypatch):  # noqa: F811
    lib, npc_id, _run = world
    monkeypatch.setattr(hands3d, "availability", lambda cfg, comfy: {"missing": []})
    monkeypatch.setattr(hands3d, "Finder", lambda comfy, npc_id, cancel=None: "finder")
    monkeypatch.setattr(cleanup3d, "refine", _fake_refine({"error": "Clean hands: No arm was found at the left wrist"}, {}))
    step, refined = _refine(lib, npc_id)
    assert refined["hands"] == {"error": "Clean hands: No arm was found at the left wrist"}
    assert "left as they were" in step["note"] and lib.read_asset(npc_id, "model_3d.glb") == b"game"


def test_without_sdpose_the_hands_step_stops_with_what_is_missing(world, monkeypatch):  # noqa: F811
    lib, npc_id, _run = world
    monkeypatch.setattr(hands3d, "availability", lambda cfg, comfy: {"missing": ["SDPose in ComfyUI"]})
    with pytest.raises(ComfyError, match="Clean hands needs SDPose in ComfyUI"):
        _refine(lib, npc_id)


def test_the_rig_is_given_the_clean_hands(world, monkeypatch):  # noqa: F811
    lib, npc_id, run = world
    hands = hands3d.summary(_report())
    lib.update(npc_id, lambda n: n["assets"]["model_3d"].update(refined={"hands": hands}))
    got = {}

    def fake_rig(cfg, model, cancel=None, shape_glb=None, skeleton="low", keypoints=None, clips="auto", hands=None):
        got["hands"] = hands
        return b"rigged", None, {"clips": ["Idle"]}
    monkeypatch.setattr(rig3d, "run", fake_rig)
    run("rig_3d")
    assert got["hands"]["l"]["points"] == hands["l"]["points"]
    lib.update(npc_id, lambda n: n["assets"]["model_3d"].update(refined={"hands": {"error": "no"}}))
    run("rig_3d")
    assert got["hands"] is None                          # a failed attempt left the generator's hands: nothing to use


def test_the_rig_script_is_handed_the_clean_hands(monkeypatch):
    seen = {}

    def fake_run_script(cfg, script, args, tmp, out, *a, **kw):
        if "--hands" in args:
            seen.update(json.loads(pathlib.Path(args[args.index("--hands") + 1]).read_text("utf-8")))
        pathlib.Path(out).write_bytes(b"rigged")
    monkeypatch.setattr(rig3d, "run_script", fake_run_script)
    rig3d.run({}, b"model", hands={"l": {"points": [[1, 2, 3]] * 21}})
    assert seen == {"l": {"points": [[1, 2, 3]] * 21}}
