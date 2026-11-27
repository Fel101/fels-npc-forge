import io

import numpy as np
from PIL import Image, ImageDraw

from npcgen import keying
from npcgen.keying import grey_png, remove_background

MAGENTA = (235, 27, 156)


def png(im):
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def figure(bg=MAGENTA, hole=None, speck=False):
    """A green 'body' with skin-coloured arms on a flat backdrop; optionally a key-coloured hole (a gap) in the body."""
    im = Image.new("RGB", (240, 360), bg)
    d = ImageDraw.Draw(im)
    d.rectangle([80, 40, 160, 300], fill=(20, 120, 60))
    d.rectangle([50, 90, 80, 220], fill=(200, 160, 120))
    d.rectangle([160, 90, 190, 220], fill=(200, 160, 120))
    if hole:
        d.rectangle(hole, fill=bg)
    if speck:
        d.rectangle([118, 60, 120, 62], fill=bg)                      # 9px of key colour: a detail, not a gap
    return im


def load(data):
    return np.asarray(Image.open(io.BytesIO(data)).convert("RGBA"))


def test_the_backdrop_becomes_transparent_and_the_figure_stays_opaque():
    out, info = remove_background(png(figure()), trim=False)
    a = load(out)
    assert info["keyed"] and 300 <= info["key_hue"] <= 340
    assert a[2, 2, 3] == 0 and a[350, 230, 3] == 0                    # corners
    assert a[150, 120, 3] == 255 and a[150, 60, 3] == 255             # body and an arm


def test_trimming_crops_to_the_figure_plus_padding():
    out, info = remove_background(png(figure()), trim=True, pad=6)
    w, h = info["size"]
    assert 130 <= w <= 160 and 260 <= h <= 290                        # figure is ~140x260
    assert load(out).shape[:2] == (h, w)


def test_an_enclosed_gap_of_backdrop_colour_is_removed_but_a_tiny_detail_is_kept():
    out, _ = remove_background(png(figure(hole=[105, 120, 115, 180], speck=True)), trim=False)
    a = load(out)
    assert a[150, 110, 3] == 0                                        # the 10x60 gap between arm and torso, say
    assert a[61, 119, 3] == 255                                       # the 3x3 speck stays part of the figure


def test_green_backdrops_work_too():
    out, info = remove_background(png(figure(bg=(20, 235, 40))), trim=False)
    assert info["keyed"] and load(out)[2, 2, 3] == 0


def test_same_hue_blue_clothing_is_not_flooded_with_the_blue_backdrop():
    im = Image.new("RGB", (240, 360), (75, 135, 240))
    d = ImageDraw.Draw(im)
    d.rectangle([75, 45, 165, 310], fill=(50, 80, 150))
    out, info = remove_background(png(im), trim=False)
    a = load(out)
    assert info["keyed"] and a[2, 2, 3] == 0
    assert a[150, 120, 3] == 255 and a[280, 120, 3] == 255


def test_the_soft_edge_takes_the_figures_colour_not_the_backdrops():
    """The softened alpha reaches a pixel or two into the backdrop; those pixels were left magenta, a fringe round the figure."""
    a = load(remove_background(png(figure()), trim=False)[0]).astype(int)
    soft = (a[..., 3] > 0) & (a[..., 3] < 255)
    assert soft.sum() > 100                                            # the edge is still soft
    r, g, b = a[..., 0][soft], a[..., 1][soft], a[..., 2][soft]
    assert not ((r > g + 25) & (b > g + 25)).any()                     # no magenta in it
    assert a[150, 49, 3] < 255 and abs(a[150, 49, :3] - (200, 160, 120)).max() < 20   # just outside an arm: the arm's colour


def test_the_backdrops_own_soft_shadow_goes_but_a_figure_of_its_colour_stays():
    """A shadow the model drew on the backdrop (between the feet) goes; a body of the backdrop's hue, shaded as smoothly but
    darker (a blue alien on a blue backdrop), and a patch of that colour with a hard edge stay."""
    colour = np.array([40, 90, 240], np.float32)
    y, x = np.mgrid[0:200, 0:200]
    factor = np.ones((200, 200), np.float32)

    def inside(y0, y1, x0, x1):
        return (y >= y0) & (y < y1) & (x >= x0) & (x < x1), np.minimum.reduce([y - y0, y1 - 1 - y, x - x0, x1 - 1 - x])

    shadow, depth = inside(130, 196, 20, 110)
    factor[shadow] = 1 - 0.2 * np.minimum(1, depth[shadow] / 20)        # fades to 80% over 20 px
    body, depth = inside(10, 196, 120, 196)
    factor[body] = 1 - 0.55 * np.minimum(1, depth[body] / 25)           # fades as smoothly, to 45%
    patch, _ = inside(10, 80, 10, 90)
    factor[patch] = 0.85                                                # a hard edge
    rgb = np.clip(colour * factor[..., None], 0, 255).astype(np.uint8)
    h, s, v = keying._hsv(rgb)
    bg = factor > 0.97
    found = keying._cast_shadow(rgb, bg, h, s, v, float(np.median(h[bg])), colour, 24.0)
    assert found[shadow & ~bg].all()
    assert not found[body].any() and not found[patch].any()


def test_hard_edges_keep_alpha_binary_for_pixel_art():
    out, _ = remove_background(png(figure()), hard_edges=True, trim=False)
    assert set(np.unique(load(out)[..., 3])) <= {0, 255}


def test_grey_keeps_the_cut_out_and_drops_every_colour():
    cut, _ = remove_background(png(figure()), trim=False)
    before, after = load(cut), load(grey_png(cut))
    assert after.shape == before.shape
    assert (after[..., 3] == before[..., 3]).all()                       # transparency untouched
    assert (after[..., 0] == after[..., 1]).all() and (after[..., 1] == after[..., 2]).all()
    opaque = Image.open(io.BytesIO(grey_png(png(figure()))))
    assert opaque.mode == "RGB"                                         # an opaque picture stays opaque


def test_a_grey_backdrop_is_left_alone_with_a_reason():
    im = Image.new("RGB", (100, 100), (128, 128, 128))
    original = png(im)
    out, info = remove_background(original)
    assert not info["keyed"] and "reason" in info and out == original
