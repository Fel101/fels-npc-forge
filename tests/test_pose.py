import numpy as np
import pytest
from PIL import Image, ImageDraw

from npcgen import anim, pose

PALETTE = dict(shirt=(60, 120, 85), trousers=(30, 80, 60), shoes=(110, 100, 75), skin=(200, 150, 120))


def mask(im):
    return np.asarray(im.convert("RGB")).min(axis=2) < 235


def test_every_pose_stands_on_the_floor_at_about_the_standing_height():
    for name, p in pose.POSES.items():
        sk = pose.skeleton(p)
        lowest = min(min(sk[s]["heel"][1], sk[s]["toe"][1]) for s in ("right", "left"))
        assert abs(lowest) < 1e-9, name                                           # the lowest foot point is on the floor
        assert 0.97 < pose.figure_height(p) < 1.03, name                          # nobody grows or shrinks between poses


def test_a_stride_is_wide_when_a_foot_lands_and_narrow_when_the_legs_pass():
    spread = lambda name: anim.foot_spread(mask(pose.paint(pose.POSES[name], "side", PALETTE)))
    assert spread("contact_a") > 2.5 * spread("passing_a")
    assert spread("contact_a") == pytest.approx(spread("contact_b"), abs=0.02)   # the other step is the same shape
    assert spread("contact_a") > spread("down_a") > spread("passing_a")


def test_the_b_steps_swap_the_legs_and_the_arms():
    a, b = pose.POSES["contact_a"], pose.POSES["contact_b"]
    assert b["right"] == a["left"] and b["left"] == a["right"] and b["arm_right"] == a["arm_left"] and b["arm_left"] == a["arm_right"]


@pytest.mark.parametrize("view", ["side", "front", "back"])
def test_puppets_are_drawn_from_every_side_at_the_asked_size_and_fit_the_picture(view):
    for name in ("contact_a", "passing_b"):
        im = pose.paint(pose.POSES[name], view, PALETTE, size=(288, 432))
        assert im.size == (288, 432)
        box = Image.fromarray((mask(im) * 255).astype(np.uint8)).getbbox()
        assert 0.78 < (box[3] - box[1]) / 432 < 0.92                              # about 84% of the picture high, plus the head
        assert box[0] > 0 and box[2] < 288 and box[3] < 432                       # nothing cut off


def test_the_puppet_wears_the_characters_colours_and_the_far_side_is_darker():
    im = pose.paint(pose.POSES["contact_a"], "side", PALETTE)
    found = {tuple(c) for c in np.unique(np.asarray(im).reshape(-1, 3), axis=0)}
    for colour in PALETTE.values():
        assert colour in found                                                    # near limbs and the torso are in the exact colours
    assert any(c[0] < 30 and c[1] < 60 for c in found)                            # the far leg is a darker shade of the trousers


def test_a_walk_cycle_has_a_pose_and_a_bob_for_every_frame():
    for count, names in pose.WALK_CYCLES.items():
        assert len(names) == count and all(n in pose.POSES for n in names)
        bob = pose.step_bob(count)
        assert len(bob) == count and min(bob) < 0 <= max(bob)
    eight = pose.step_bob(8)
    assert eight[1] == min(eight) and eight[3] == max(eight)                      # lowest just after a foot lands, highest as the body rises
    assert pose.step_bob(4) == [eight[0], eight[2], eight[4], eight[6]]


def test_the_colours_of_a_character_are_read_from_a_standing_cut_out():
    fig = Image.new("RGBA", (100, 400), (0, 0, 0, 0))
    d = ImageDraw.Draw(fig)
    d.rectangle([20, 0, 80, 50], fill=(210, 160, 130, 255))          # head
    d.rectangle([10, 60, 90, 200], fill=(40, 90, 160, 255))          # shirt
    d.rectangle([20, 200, 80, 380], fill=(60, 60, 60, 255))          # trousers
    d.rectangle([15, 385, 85, 399], fill=(110, 70, 40, 255))         # shoes
    got = pose.sample_palette(fig)
    assert got == dict(shirt=(40, 90, 160), trousers=(60, 60, 60), shoes=(110, 70, 40), skin=(210, 160, 130))
    assert set(pose.sample_palette(Image.new("RGBA", (10, 10)))) == {"shirt", "trousers", "shoes", "skin"}     # an empty picture still answers


def test_hair_colour_comes_from_the_words_and_reaches_the_puppet():
    assert pose.hair_colour("black long hair") == (25, 22, 22) and pose.hair_colour("pastel pink bob")[0] > 200
    assert pose.hair_colour("dark brown wavy hair") != pose.hair_colour("light brown hair") != pose.hair_colour("brown hair in a bun")
    assert pose.hair_colour("bald") is None and pose.hair_colour("") is None and pose.hair_colour(None) is None
    found = lambda im: {tuple(c) for c in np.unique(np.asarray(im).reshape(-1, 3), axis=0)}
    head_zone = lambda im: np.asarray(im)[: int(0.19 * im.height)]                 # above the neck; hands are skin-coloured too
    for view in ("side", "front", "back"):
        image = pose.paint(pose.POSES["passing_a"] if view == "side" else pose.FRONT_POSES["step_a"], view, dict(PALETTE, hair=(25, 22, 22)))
        assert (25, 22, 22) in found(image)
        assert (PALETTE["skin"] in found(head_zone(image))) == (view != "back")       # a face, except from behind
    bald = found(pose.paint(pose.POSES["passing_a"], "side", dict(PALETTE, hair=None)))
    assert (25, 22, 22) not in bald and PALETTE["skin"] in bald                     # no hair colour known: a plain skin-coloured head
