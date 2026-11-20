import copy
import io
import json
import struct
import threading
import types

import numpy as np
import pytest
from PIL import Image

from npcgen import cleanup3d, config, paint3d, rig3d
from npcgen.jobs import Jobs
from npcgen.library import Library


# ---- a tiny GLB writer for test meshes ---------------------------------------------------------------------------------

def png(rgb, size=(64, 64)):
    out = io.BytesIO()
    Image.new("RGB", size, rgb).save(out, "PNG")
    return out.getvalue()


def make_glb(pos, tri, uv=None, texture=None):
    pos = np.asarray(pos, np.float32)
    tri = np.asarray(tri, np.uint32)
    uv = np.zeros((len(pos), 2), np.float32) if uv is None else np.asarray(uv, np.float32)
    parts = [pos.tobytes(), uv.tobytes(), tri.tobytes(), texture or png((128, 128, 128))]
    blob, views = b"", []
    for part in parts:
        blob += b"\0" * (-len(blob) % 4)
        views.append({"buffer": 0, "byteOffset": len(blob), "byteLength": len(part)})
        blob += part
    blob += b"\0" * (-len(blob) % 4)
    doc = {"asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0}],
           "buffers": [{"byteLength": len(blob)}], "bufferViews": views,
           "accessors": [{"bufferView": 0, "componentType": 5126, "count": len(pos), "type": "VEC3",
                          "min": pos.min(0).tolist(), "max": pos.max(0).tolist()},
                         {"bufferView": 1, "componentType": 5126, "count": len(pos), "type": "VEC2"},
                         {"bufferView": 2, "componentType": 5125, "count": int(tri.size), "type": "SCALAR"}],
           "images": [{"bufferView": 3, "mimeType": "image/png"}], "textures": [{"source": 0}],
           "materials": [{"pbrMetallicRoughness": {"baseColorTexture": {"index": 0}}}],
           "meshes": [{"primitives": [{"attributes": {"POSITION": 0, "TEXCOORD_0": 1}, "indices": 2, "material": 0}]}]}
    text = json.dumps(doc).encode()
    text += b" " * (-len(text) % 4)
    return (struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(text) + 8 + len(blob)) + struct.pack("<II", len(text), 0x4E4F534A)
            + text + struct.pack("<II", len(blob), 0x004E4942) + blob)


def sphere(radius, inward=False, centre=(0, 1, 0), rings=32, segments=64):
    """A closed UV sphere; `inward` flips the winding, like the inner skin of a hollow shell."""
    pos = []
    for i in range(rings + 1):
        phi = np.pi * i / rings
        for j in range(segments):
            th = 2 * np.pi * j / segments
            pos.append([radius * np.sin(phi) * np.cos(th), radius * np.cos(phi), radius * np.sin(phi) * np.sin(th)])
    pos = np.array(pos) + centre
    tri = []
    for i in range(rings):
        for j in range(segments):
            a, b = i * segments + j, i * segments + (j + 1) % segments
            c, d = a + segments, b + segments
            tri += [[a, b, c], [b, d, c]]              # outward winding for this layout
    tri = np.array(tri)
    return pos, (tri[:, ::-1] if inward else tri)


def combine(*meshes):
    pos, tri, offset = [], [], 0
    for p, t in meshes:
        pos.append(p)
        tri.append(t + offset)
        offset += len(p)
    return np.concatenate(pos), np.concatenate(tri)


# ---- the GLB reader/writer -------------------------------------------------------------------------------------------

def test_triangles_and_images_can_be_replaced_and_the_file_read_back():
    pos, tri = sphere(1.0, rings=4, segments=8)
    glb = paint3d.Glb(make_glb(pos, tri))
    prim = glb.json["meshes"][0]["primitives"][0]
    glb.set_triangles(prim, tri[:10])
    glb.replace_image(0, png((255, 0, 0), (8, 8)))
    again = paint3d.Glb(glb.dumps())
    assert again.accessor(again.json["meshes"][0]["primitives"][0]["indices"]).reshape(-1, 3).tolist() == tri[:10].tolist()
    assert np.allclose(again.accessor(0), pos, atol=1e-6)
    assert again.image(0).size == (8, 8) and again.image(0).convert("RGB").getpixel((0, 0)) == (255, 0, 0)


# ---- hidden inner skin -------------------------------------------------------------------------------------------------

def test_the_inner_skin_of_a_hollow_shell_goes_and_a_body_inside_it_stays():
    """Like a Pixal3D mesh: an outer skin with an inward-facing skin just inside it, and inside that a separate body
    (as under a vest) with its own inner skin. Only the skin right behind the visible surface may go."""
    outer, outer_inner = sphere(1.0), sphere(0.994, inward=True)
    body, body_inner = sphere(0.6), sphere(0.594, inward=True)
    pos, tri = combine(outer, outer_inner, body, body_inner)
    drop = cleanup3d.inner_skin(pos, tri, directions=32, resolution=256)
    n = len(outer[1])
    parts = [drop[k * n:(k + 1) * n] for k in range(4)]
    assert not parts[0].any()                       # the visible skin
    assert parts[1].mean() > 0.95                   # its inner skin
    assert not parts[2].any() and not parts[3].any()   # the body under it, and even the body's own inner skin, stay


def test_remove_inner_skin_reports_what_it_removed():
    pos, tri = combine(sphere(1.0, rings=16, segments=32), sphere(0.994, inward=True, rings=16, segments=32))
    report = {}
    out = paint3d.Glb(cleanup3d.remove_inner_skin(make_glb(pos, tri), report))
    left = out.accessor(out.json["meshes"][0]["primitives"][0]["indices"]).size // 3
    assert report["inner_skin"]["faces_before"] == len(tri)
    assert left == len(tri) - report["inner_skin"]["faces_removed"]
    assert report["inner_skin"]["share_removed"] > 0.45


# ---- painting from the pictures ----------------------------------------------------------------------------------------

def box_with_uv():
    """A 1 x 2 x 0.2 box standing on y=0, facing +Z; each face has its own quarter-strip of the texture."""
    x, y, z = 0.5, 2.0, 0.1
    faces = {  # corners in order (counter-clockwise seen from outside) and the texture strip
        "front": ([[-x, 0, z], [x, 0, z], [x, y, z], [-x, y, z]], 0),
        "back": ([[x, 0, -z], [-x, 0, -z], [-x, y, -z], [x, y, -z]], 1),
        "left": ([[-x, 0, -z], [-x, 0, z], [-x, y, z], [-x, y, -z]], 2),
        "right": ([[x, 0, z], [x, 0, -z], [x, y, -z], [x, y, z]], 3),
    }
    pos, uv, tri = [], [], []
    for corners, strip in faces.values():
        u0, u1 = strip * 0.25 + 0.01, strip * 0.25 + 0.24
        base = len(pos)
        pos += corners
        uv += [[u0, 0.99], [u1, 0.99], [u1, 0.01], [u0, 0.01]]
        tri += [[base, base + 1, base + 2], [base, base + 2, base + 3]]
    return np.array(pos), np.array(uv), np.array(tri)


def test_the_front_picture_is_painted_onto_the_front_of_the_model():
    pos, uv, tri = box_with_uv()
    model = make_glb(pos, tri, uv, png((128, 128, 128), (256, 256)))
    picture = Image.new("RGB", (200, 400), (255, 255, 255))
    picture.paste((200, 20, 20), (50, 100, 150, 300))          # the box seen from the front: 1 x 2, red
    out = io.BytesIO()
    picture.save(out, "PNG")
    report = {}
    painted = paint3d.Glb(paint3d.paint(model, {"front": out.getvalue()}, report=report))
    tex = np.asarray(painted.image(0).convert("RGB")).astype(int)
    front = tex[20:236, 10:54].reshape(-1, 3).mean(0)          # inside the front face's strip
    assert report["views"]["front"]["outline_match"] > 0.95
    assert front[0] > 180 and front[1] < 60 and front[2] < 60


# ---- views from a raised camera (the Items tab's high-angle pictures) --------------------------------------------------------

def cube_with_uv(h=0.5):
    """A closed unit-high cube standing on y=0 (x and z from -h to h), facing +Z; each face has its own sixth of a 6:1 texture."""
    faces = [  # front, top, back, bottom, left, right: corners counter-clockwise seen from outside
        [[-h, 0, h], [h, 0, h], [h, 1, h], [-h, 1, h]],
        [[-h, 1, h], [h, 1, h], [h, 1, -h], [-h, 1, -h]],
        [[h, 0, -h], [-h, 0, -h], [-h, 1, -h], [h, 1, -h]],
        [[-h, 0, -h], [h, 0, -h], [h, 0, h], [-h, 0, h]],
        [[-h, 0, -h], [-h, 0, h], [-h, 1, h], [-h, 1, -h]],
        [[h, 0, h], [h, 0, -h], [h, 1, -h], [h, 1, h]],
    ]
    pos, uv, tri = [], [], []
    for strip, corners in enumerate(faces):
        base = len(pos)
        u0, u1 = strip / 6 + 0.01, (strip + 1) / 6 - 0.01
        pos += corners
        uv += [[u0, 0.99], [u1, 0.99], [u1, 0.01], [u0, 0.01]]
        tri += [[base, base + 1, base + 2], [base, base + 2, base + 3]]
    return np.array(pos, float), np.array(uv), np.array(tri)


def raised_picture(side, h=0.5, scale=200.0):
    """The cube as a camera 45 degrees up sees it from the front (side=+1) or the back (-1), drawn by hand: the top face green, the near face red."""
    from PIL import ImageDraw
    up = (0.0, 0.7071067811865476, -0.7071067811865476 * side)       # the picture's up axis
    right = np.array([float(side), 0, 0])
    picture = Image.new("RGB", (400, 400), (255, 255, 255))
    draw = ImageDraw.Draw(picture)

    def at(point):
        return (200 + scale * float(np.dot(point, right)), 300 - scale * float(np.dot(point, up)))
    top = [(-h, 1, side * h), (h, 1, side * h), (h, 1, -side * h), (-h, 1, -side * h)]
    near = [(-h, 0, side * h), (h, 0, side * h), (h, 1, side * h), (-h, 1, side * h)]
    draw.polygon([at(np.array(c)) for c in top], fill=(30, 200, 30))
    draw.polygon([at(np.array(c)) for c in near], fill=(200, 30, 30))
    out = io.BytesIO()
    picture.save(out, "PNG")
    return out.getvalue()


@pytest.mark.parametrize("view, side, near_strip", [("front_high", 1, 0), ("back_high", -1, 2)])
def test_a_picture_from_a_raised_camera_paints_the_top_and_the_near_side(view, side, near_strip):
    pos, uv, tri = cube_with_uv()
    model = make_glb(pos, tri, uv, png((128, 128, 128), (600, 100)))
    report = {}
    painted = paint3d.Glb(paint3d.paint(model, {view: raised_picture(side)}, report=report))
    tex = np.asarray(painted.image(0).convert("RGB")).astype(int)
    strip = lambda i: tex[10:90, int(i * 100 + 12):int(i * 100 + 88)].reshape(-1, 3).mean(0)         # noqa: E731
    top, near, far = strip(1), strip(near_strip), strip(3)                                         # top, the near face, the bottom
    assert report["views"][view]["outline_match"] > 0.95 and report["views"][view]["head_fit_used"] is False
    assert top[1] > 170 and top[0] < 80 and top[2] < 80                       # green
    assert near[0] > 170 and near[1] < 80 and near[2] < 80                    # red
    assert 110 < far[0] < 145 and abs(far[0] - far[1]) < 6                    # the bottom no picture sees stays grey


def test_the_raised_views_are_right_handed_like_the_level_ones_and_the_level_ones_are_unchanged():
    for view, (right, toward) in paint3d.VIEWS.items():
        up = paint3d.up_axis(view)
        assert abs(np.linalg.norm(up) - 1) < 1e-9 and abs(np.dot(up, toward)) < 1e-9 and abs(np.dot(up, right)) < 1e-9
        assert np.allclose(np.cross(right, up), toward)                         # right x up = towards the camera, as for the four level views
    assert np.allclose(paint3d.up_axis("front"), [0, 1, 0]) and set(paint3d.UPS) == {"front_high", "back_high"}


# ---- the job steps -------------------------------------------------------------------------------------------------------

@pytest.fixture()
def world(tmp_path):
    lib = Library(tmp_path / "library")
    npc = lib.create({"setting": "modern", "style": "render3d", "seed": 3, "appearance": {},
                      "profile": {"first_name": "Mira", "last_name": "Vale", "age": 30, "gender": "female", "occupation": "tester"}})
    lib.write_asset(npc["id"], "model_3d.glb", b"generated")
    for view in ("front", "side_left", "back", "side_right"):
        lib.write_asset(npc["id"], f"sheet/{view}.png", view.encode())

    def seed(n):
        n["assets"].update(model_3d={"file": "model_3d.glb", "engine_id": "pixal3d", "textured": True, "created": "t"},
                           rig_3d={"file": "model_3d_rigged.glb"},
                           sheet={"views": {v: {"file": f"sheet/{v}.png"} for v in ("front", "side_left", "back", "side_right")}})
    lib.update(npc["id"], seed)
    jobs = Jobs(copy.deepcopy(config.DEFAULTS), None, lib)

    def run(kind, **options):
        job = types.SimpleNamespace(npc_id=npc["id"], cancel=threading.Event(), cache={}, ref=None,
                                    options={"quality": "standard", "seed": None, **options})
        jobs._run_step(job, {"kind": kind, "label": kind})
        return lib.get(npc["id"])["assets"]
    return lib, npc["id"], run


def _kinds(steps):
    return [s["kind"] for s in steps]


def test_a_new_pixal_model_is_not_cleaned_up_unless_asked():
    npc = {"assets": {"sprite": {"file": "sprite.png", "created": "t"}}}
    kinds = _kinds(Jobs.plan(npc, {"model_3d": True, "rig_3d": True}, {"three_d_model": "pixal3d"}))
    assert "refine_3d" not in kinds and kinds[-2:] == ["model_3d", "rig_3d"]


def test_when_asked_the_clean_up_comes_between_the_model_and_the_skeleton():
    npc = {"assets": {"sprite": {"file": "sprite.png", "created": "t"}}}
    kinds = _kinds(Jobs.plan(npc, {"model_3d": True, "refine_3d": True, "rig_3d": True}, {"three_d_model": "pixal3d"}))
    assert kinds[-3:] == ["model_3d", "refine_3d", "rig_3d"]


def test_a_hunyuan_model_has_no_clean_up_step():
    npc = {"assets": {"sprite": {"file": "sprite.png", "created": "t"}}}
    assert "refine_3d" not in _kinds(Jobs.plan(npc, {"model_3d": True}, {"three_d_model": "hunyuan21"}))


def test_an_existing_model_can_be_cleaned_up_on_its_own():
    npc = {"assets": {"sprite": {"file": "sprite.png", "created": "t"}, "model_3d": {"file": "model_3d.glb", "textured": True}}}
    assert _kinds(Jobs.plan(npc, {"refine_3d": True}, {})) == ["refine_3d"]


def test_clean_up_keeps_the_original_and_always_starts_from_it(world, monkeypatch):
    lib, npc_id, run = world
    seen = []

    def fake_refine(cfg, model, views, faces=None, cancel=None, closeups=None, eyes=None, hair=None, hands=None):
        seen.append((model, sorted(views)))
        return b"game version %d" % len(seen), b"poster", {
            "inner_skin": {"faces_before": 700_000, "faces_removed": 230_000},
            "game_version": {"faces_after": 100_000}, "paint": {"views": {}}}
    monkeypatch.setattr(cleanup3d, "refine", fake_refine)

    first = run("refine_3d")
    assert seen[0] == (b"generated", ["back", "front", "side_left", "side_right"])
    assert lib.read_asset(npc_id, "model_3d.glb") == b"game version 1"
    assert lib.read_asset(npc_id, cleanup3d.ORIGINAL_FILE) == b"generated"
    assert first["model_3d"]["raw"] == cleanup3d.ORIGINAL_FILE and first["model_3d"]["refined"]["faces"] == 100_000
    assert first["rig_3d"]["stale"] is True

    run("refine_3d")
    assert seen[1][0] == b"generated"                   # the original again, not the first game version


# ---- the game version's size, chosen per character ------------------------------------------------------------------------------

def test_the_game_version_is_made_at_the_size_the_job_asks_for_and_says_so(world, monkeypatch):
    lib, npc_id, run = world
    asked = []

    def fake_refine(cfg, model, views, faces=None, **kw):
        asked.append(faces)
        return b"game", None, {"inner_skin": {"faces_before": 700_000, "faces_removed": 230_000},
                               "game_version": {"faces_after": faces - 3}, "paint": {"views": {}}}
    monkeypatch.setattr(cleanup3d, "refine", fake_refine)

    refined = run("refine_3d", game_faces=50_000)["model_3d"]["refined"]
    assert asked == [50_000] and refined["faces_requested"] == 50_000 and refined["faces"] == 49_997
    refined = run("refine_3d")["model_3d"]["refined"]                      # no size named: the usual one
    assert asked[-1] == cleanup3d.GAME_FACES == 100_000 and refined["faces_requested"] == 100_000
    run("refine_3d", game_faces="light")                                   # a preset by name
    assert asked[-1] == 25_000


@pytest.mark.parametrize("given,faces", [(None, 100_000), ("", 100_000), ("light", 25_000), ("Standard", 50_000), ("detailed", 100_000),
                                         ("high", 200_000), (60_000, 60_000), ("75000", 75_000), (10_000, 10_000), (400_000, 400_000)])
def test_a_game_size_is_a_preset_or_a_number_of_faces(given, faces):
    assert cleanup3d.clean_faces(given) == faces


@pytest.mark.parametrize("given", ["huge", 9_999, 400_001, -5, 0, True, [50_000], {"faces": 5}, "5e9"])
def test_a_game_size_that_is_neither_is_refused_with_the_choices(given):
    with pytest.raises(ValueError, match="game_faces"):
        cleanup3d.clean_faces(given)


def test_the_page_is_offered_the_presets_with_their_face_counts():
    choices = cleanup3d.game_size_choices()
    assert [c["id"] for c in choices] == ["light", "standard", "detailed", "high"]
    assert [c["faces"] for c in choices] == sorted(c["faces"] for c in choices) and choices[2]["faces"] == cleanup3d.GAME_FACES
    assert "100,000 faces" in choices[2]["label"]


def test_the_clean_up_step_names_an_unusual_size_and_not_the_usual_one():
    npc = {"assets": {"sprite": {"file": "sprite.png", "created": "t"}, "model_3d": {"file": "model_3d.glb", "textured": True}}}
    usual = Jobs.plan(npc, {"refine_3d": True}, {"game_faces": 100_000})[0]["label"]
    small = Jobs.plan(npc, {"refine_3d": True}, {"game_faces": 25_000})[0]["label"]
    assert "faces" not in usual and small.endswith("game version of 25,000 faces")


def test_a_cleaned_up_model_is_rigged_with_its_original_as_the_shape(world, monkeypatch):
    lib, npc_id, run = world
    lib.write_asset(npc_id, cleanup3d.ORIGINAL_FILE, b"generated")
    lib.write_asset(npc_id, "model_3d.glb", b"game version")
    lib.update(npc_id, lambda n: n["assets"]["model_3d"].update(raw=cleanup3d.ORIGINAL_FILE))
    got = {}

    def fake_rig(cfg, model, cancel=None, shape_glb=None, **kw):
        got.update(model=model, shape=shape_glb)
        return b"rigged", None, {"clips": ["Idle"]}
    monkeypatch.setattr(rig3d, "run", fake_rig)
    run("rig_3d")
    assert got == {"model": b"game version", "shape": b"generated"}


def test_deleting_the_model_deletes_the_kept_original_too(world):
    lib, npc_id, run = world
    lib.write_asset(npc_id, cleanup3d.ORIGINAL_FILE, b"generated")
    lib.update(npc_id, lambda n: n["assets"]["model_3d"].update(raw=cleanup3d.ORIGINAL_FILE))
    lib.delete_asset(npc_id, "model_3d")
    assert not lib.resolve(npc_id, cleanup3d.ORIGINAL_FILE).exists()
