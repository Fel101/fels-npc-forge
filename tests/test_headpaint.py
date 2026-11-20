"""Painting a detailed head from the four pictures it was made from, seen by the rig's own cameras."""
import io

import numpy as np
import pytest
from PIL import Image

from npcgen import headpaint, three_d
from npcgen.paint3d import Glb, texel_samples, textured_primitives
from test_cleanup3d import make_glb, png


# ---- the cameras --------------------------------------------------------------------------------------------------------
def test_the_middle_of_the_model_is_the_middle_of_the_picture_and_the_unit_cube_fills_a_tenth_less_than_the_frame():
    camera = headpaint.Camera("front")
    x, y, depth = camera.project(np.array([[0.0, 0.0, 0.0], [0.5, 0.0, 0.0], [0.0, 0.0, 0.5]]))
    assert abs(x[0] - 512) < 1e-6 and abs(y[0] - 512) < 1e-6
    half = 512 / three_d.RIG_PAD                                  # the cube's half width, in pixels, at its middle depth
    assert abs((x[1] - 512) - half) < 1.0 and abs((512 - y[2]) - half) < 1.0     # x to the right, z up (picture y down)
    assert depth[0] > 0 and np.isclose(depth[0], np.linalg.norm(camera.origin))


def test_the_four_cameras_sit_on_the_four_sides_of_the_model():
    at = {view: headpaint.Camera(view).origin for view in headpaint.AZIMUTH}
    assert at["front"][1] < 0 and abs(at["front"][0]) < 1e-9                  # the face looks toward -y: its camera is on that side
    assert at["side_left"][0] > 0 and at["back"][1] > 0 and at["side_right"][0] < 0
    for view, origin in at.items():
        cam = headpaint.Camera(view)
        assert np.isclose(np.linalg.norm(cam.right), 1) and np.isclose(np.linalg.norm(cam.up), 1)
        assert abs(np.dot(cam.right, cam.up)) < 1e-9 and abs(np.dot(cam.right, cam.back)) < 1e-9 and cam.up[2] > 0.99


def test_gltf_axes_become_the_rigs():
    assert to_list(headpaint.to_rig(np.array([[1.0, 2.0, 3.0]]))) == [[1.0, -3.0, 2.0]]     # up stays up, the front (+z) faces -y


def to_list(a):
    return np.asarray(a).tolist()


# ---- lining a picture up with the model ------------------------------------------------------------------------------------
def disc(cx, cy, r, size=1024):
    yy, xx = np.mgrid[0:size, 0:size]
    return (xx - cx) ** 2 + (yy - cy) ** 2 <= r * r


def test_a_known_scale_and_shift_are_found():
    cover = disc(512, 512, 300)
    picture = disc(532, 500, 315)                                # 5% bigger, 20 right and 12 up
    s, dx, dy, overlap = headpaint.align(cover, picture)
    assert abs(s - 1.05) < 0.012 and abs(dx - 20) < 5 and abs(dy + 12) < 5 and overlap > 0.95


def test_a_braid_the_model_does_not_have_does_not_pull_the_fit_off():
    cover = disc(512, 512, 300)
    picture = disc(512, 512, 300)
    picture[560:1000, 100:260] = True                            # a fall of hair down one side, in the picture only
    s, dx, dy, _ = headpaint.align(cover, picture)
    assert abs(s - 1.0) < 0.02 and abs(dx) < 6 and abs(dy) < 6


def test_the_distance_to_an_edge_is_measured_in_pixels():
    edge = np.zeros((64, 64), bool)
    edge[32, 32] = True
    d = headpaint._distance(edge)
    assert d[32, 32] == 0 and abs(d[32, 40] - 8) < 0.5 and abs(d[40, 40] - 8 * 2 ** 0.5) < 1.0


def test_the_outline_of_a_shape_is_its_boundary_pixels():
    square = np.zeros((10, 10), bool)
    square[2:8, 2:8] = True
    edge = headpaint._edges(square)
    assert edge.sum() == 20 and not edge[4, 4] and edge[2, 4]


# ---- painting: a sphere painted from pictures of it ----------------------------------------------------------------------
RADIUS = 0.4


def uv_sphere(rings=32, segments=64):
    """A UV sphere in glTF axes (y up, the front toward +z) with the seam's vertices doubled, as a texture unwrap has them."""
    pos, uv = [], []
    for i in range(rings + 1):
        phi = np.pi * i / rings
        for j in range(segments + 1):
            th = 2 * np.pi * j / segments
            pos.append([RADIUS * np.sin(phi) * np.sin(th), RADIUS * np.cos(phi), RADIUS * np.sin(phi) * np.cos(th)])
            uv.append([j / segments, i / rings])
    tri = []
    for i in range(rings):
        for j in range(segments):
            a, b = i * (segments + 1) + j, i * (segments + 1) + j + 1
            c, d = a + segments + 1, b + segments + 1
            tri += [[a, c, b], [b, c, d]]
    return np.array(pos), np.array(tri), np.array(uv)


def colour_of(rig):
    """A colour that depends on where in the rig's world a point is: red with x, green with height, blue toward the front."""
    r = 128 + 127 * rig[:, 0] / RADIUS
    g = 128 + 127 * rig[:, 2] / RADIUS
    b = 128 + 127 * (-rig[:, 1]) / RADIUS
    return np.clip(np.stack([r, g, b], 1), 0, 255)


def picture_of_sphere(view, colour=None, with_points=False):
    """What the rig's camera sees of the sphere, worked out by intersecting each pixel's ray with it (independent of the mesh)."""
    colour = colour or colour_of
    cam = headpaint.Camera(view)
    yy, xx = np.mgrid[0:headpaint.SIZE, 0:headpaint.SIZE]
    d = (-cam.back[None, None, :] * cam.focal + cam.right[None, None, :] * (xx[..., None] + 0.5 - 512)
         - cam.up[None, None, :] * (yy[..., None] + 0.5 - 512))
    d /= np.linalg.norm(d, axis=2, keepdims=True)
    o = cam.origin
    b = 2 * (d * o).sum(2)
    c = (o * o).sum() - RADIUS ** 2
    disc_ = b * b - 4 * c
    hit = disc_ > 0
    t = np.where(hit, (-b - np.sqrt(np.where(hit, disc_, 0))) / 2, 0)
    point = o + d * t[..., None]
    rgb = np.zeros((headpaint.SIZE, headpaint.SIZE, 3), np.uint8)
    rgb[hit] = colour(point[hit]).astype(np.uint8)
    if with_points:
        return rgb, hit.astype(np.float32), point
    return rgb, hit.astype(np.float32)


@pytest.fixture(scope="module")
def painted_sphere():
    pos, tri, uv = uv_sphere()
    grey = png((90, 90, 90), (128, 128))
    model = make_glb(pos, tri, uv, texture=grey)
    pictures, masks = {}, {}
    for view in headpaint.AZIMUTH:
        pictures[view], masks[view] = picture_of_sphere(view)
    report = {}
    return model, headpaint.paint(model, pictures, masks, report), report, (pos, tri, uv)


def test_a_sphere_is_painted_from_pictures_of_it_in_the_colours_of_where_each_texel_is(painted_sphere):
    model, out, report, (pos, tri, uv) = painted_sphere
    glb = Glb(out)
    texture = np.asarray(glb.image(0).convert("RGB")).reshape(-1, 3).astype(float)
    prims = textured_primitives(Glb(model))
    pix, points, normals = texel_samples(prims[0][0], prims[0][1], prims[0][2], prims[0][3], (128, 128))
    expected = colour_of(headpaint.to_rig(points))
    # where some camera sees the texel well: the top and bottom of a sphere are seen only edge-on by the four
    rig = headpaint.to_rig(points)
    facing = np.max([np.einsum("ij,ij->i", rig / RADIUS, (headpaint.Camera(v).origin - rig) / np.linalg.norm(headpaint.Camera(v).origin - rig, axis=1, keepdims=True))
                     for v in headpaint.AZIMUTH], axis=0)
    inside = facing > 0.65
    assert inside.sum() > 3000
    error = np.abs(texture[pix[inside]] - expected[inside]).mean(1)
    assert np.median(error) < 3 and np.percentile(error, 90) < 10
    unseen = facing < 0.1                                                    # ...and what no camera sees keeps the generator's grey
    assert unseen.sum() > 100 and np.abs(texture[pix[unseen]] - 90).mean() < 25
    assert set(report["views"]) == set(headpaint.AZIMUTH)
    for view in report["views"].values():
        assert view["outline_match"] > 0.97 and view["trust"] == 1.0 and abs(view["scale"] - 1) < 0.02


def test_the_painted_head_keeps_its_geometry_and_uvs(painted_sphere):
    model, out, _report, (pos, tri, uv) = painted_sphere
    before, after = Glb(model), Glb(out)
    prim_before, prim_after = before.json["meshes"][0]["primitives"][0], after.json["meshes"][0]["primitives"][0]
    assert np.array_equal(after.accessor(prim_after["indices"]), before.accessor(prim_before["indices"]))
    assert np.allclose(after.accessor(prim_after["attributes"]["POSITION"]), before.accessor(prim_before["attributes"]["POSITION"]))
    assert np.allclose(after.accessor(prim_after["attributes"]["TEXCOORD_0"]), before.accessor(prim_before["attributes"]["TEXCOORD_0"]))
    assert after.image(0).size == (128, 128)


def test_a_picture_that_does_not_show_the_head_is_not_used_and_no_picture_at_all_is_an_error():
    pos, tri, uv = uv_sphere(rings=12, segments=24)
    model = make_glb(pos, tri, uv, texture=png((90, 90, 90), (64, 64)))
    pictures, masks = {}, {}
    for view in ("front", "side_left"):
        pictures[view], masks[view] = picture_of_sphere(view)
    pictures["back"] = np.zeros_like(pictures["front"])
    masks["back"] = np.zeros_like(masks["front"])
    masks["back"][50:150, 50:150] = 1                            # a picture whose head is somewhere else entirely
    report = {}
    headpaint.paint(model, pictures, masks, report)
    assert report["views"]["front"]["trust"] == 1.0 and report["views"]["back"]["trust"] < 0.5
    with pytest.raises(ValueError, match="lines up"):
        headpaint.paint(model, {"back": pictures["back"]}, {"back": masks["back"]}, {})


# ---- the hair check ------------------------------------------------------------------------------------------------------
HAIR, SKIN = np.array([60.0, 40.0, 30.0]), np.array([200.0, 160.0, 140.0])


def hair_and_skin(rig):
    """Dark hair on the upper half of the sphere, pale skin below it."""
    return np.where((rig[:, 2] > 0)[:, None], HAIR, SKIN)


def flap_finder(masks, lock=0.25):
    """A stand-in for SAM. The pictures show hair on the upper half only; the bare model, though, has hair down to `lock`
    of the radius below the equator (a lock that hangs lower than the pictures have it), and no ears or eyes."""
    def find(png, name, what):
        view = next(v for v in headpaint.AZIMUTH if name.startswith(v))
        kind = name[len(view) + 1:]
        if what != "hair" or kind not in ("picture", "model"):
            return np.zeros((headpaint.SIZE, headpaint.SIZE), bool)
        if kind == "picture":
            grey = np.asarray(Image.open(io.BytesIO(png)).convert("L"))
            return (grey < 110) & (masks[view] > 0.5)
        _rgb, hit, point = picture_of_sphere(view, with_points=True)
        return (hit > 0) & (point[..., 2] > -lock * RADIUS)              # picture_of_sphere's points are in the rig's world already
    return find


def test_hair_the_pictures_do_not_show_is_painted_as_hair_not_as_the_skin_behind_it():
    pos, tri, uv = uv_sphere()
    model = make_glb(pos, tri, uv, texture=png((90, 90, 90), (128, 128)))
    pictures, masks = {}, {}
    for view in headpaint.AZIMUTH:
        pictures[view], masks[view] = picture_of_sphere(view, hair_and_skin)
    plain = Glb(headpaint.paint(model, pictures, masks, {}))
    report = {}
    matched = Glb(headpaint.paint(model, pictures, masks, report, find=flap_finder(masks)))

    prims = textured_primitives(Glb(model))
    pix, points, _n = texel_samples(prims[0][0], prims[0][1], prims[0][2], prims[0][3], (128, 128))
    z = headpaint.to_rig(points)[:, 2]
    rig = headpaint.to_rig(points)
    band = (z < -0.05 * RADIUS) & (z > -0.2 * RADIUS) & (np.abs(rig[:, 1] / RADIUS) > 0.6)      # the lock's texels, on the front and back
    colours = lambda glb: np.asarray(glb.image(0).convert("RGB")).reshape(-1, 3).astype(float)[pix[band]]
    assert band.sum() > 100
    assert np.abs(colours(plain) - SKIN).mean() < 25                        # painted as the pictures show it: skin
    assert np.abs(colours(matched) - HAIR).mean() < 30                      # painted as the model's hair: the hair's colour
    skin_only = (z < -0.4 * RADIUS) & (np.abs(rig[:, 1] / RADIUS) > 0.6)
    assert np.abs(np.asarray(matched.image(0).convert("RGB")).reshape(-1, 3).astype(float)[pix[skin_only]] - SKIN).mean() < 20   # skin stays skin
    assert report["hair"]["filled_texels"] > 0 and report["hair"]["refused_texels"] > 0
    assert all(v["model_hair_not_in_picture"] > 0 for v in report["hair"]["views"].values())


def test_without_a_finder_nothing_about_hair_is_asked_or_reported():
    pos, tri, uv = uv_sphere(rings=12, segments=24)
    model = make_glb(pos, tri, uv, texture=png((90, 90, 90), (64, 64)))
    pictures, masks = {}, {}
    for view in ("front", "side_left"):
        pictures[view], masks[view] = picture_of_sphere(view)
    report = {}
    headpaint.paint(model, pictures, masks, report)
    assert "hair" not in report
