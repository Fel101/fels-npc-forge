"""Pose-guided animation frames (the walk): prompts, what counts as a usable draw, the step bob, and a whole clip made with a
stand-in for ComfyUI."""
import ast
import copy
import io
import pathlib
import threading
import types

import numpy as np
import pytest
from PIL import Image, ImageDraw

from npcgen import anim, config, pose, roll, service
from npcgen.comfy import DEFAULT_MODELS
from npcgen.library import Library

PALETTE = dict(shirt=(60, 120, 85), trousers=(30, 80, 60), shoes=(110, 100, 75), skin=(200, 150, 120))
MAGENTA = (255, 0, 255)


@pytest.fixture(scope="module")
def data():
    return roll.load_data()


def png(im):
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


STAND = dict(right=(0, 0, 0), left=(0, 0, 0), arm_right=(0, 0), arm_left=(0, 0))


def puppet_and_cut(name, view="side", size=(288, 432)):
    """The puppet for a pose (a name from pose.POSES, or a pose), and the same figure as a cut-out on the full canvas (a stand-in
    for a perfect draw)."""
    puppet = pose.paint(pose.POSES[name] if isinstance(name, str) else name, view, PALETTE, size=size)
    rgba = puppet.convert("RGBA")
    rgba.putalpha(Image.fromarray(((np.asarray(puppet).min(axis=2) < 235) * 255).astype(np.uint8)))
    return puppet, rgba


def zoomed(im, factor):
    w, h = im.size
    big = im.resize((round(w * factor), round(h * factor)), Image.LANCZOS)
    left, top = (big.width - w) // 2, (big.height - h) // 2
    return big.crop((left, top, left + w, top + h))


def on_magenta(im):
    """A puppet (white page) as a 'draw' on the flat backdrop the sprite pipeline uses."""
    a = np.asarray(im.convert("RGB")).copy()
    a[a.min(axis=2) >= 235] = MAGENTA
    return Image.fromarray(a)


def standing(size, colours=PALETTE):
    """A front view of a standing character on magenta, with the four bands the palette sampler reads."""
    w, h = size
    im = Image.new("RGB", size, MAGENTA)
    d = ImageDraw.Draw(im)
    top, foot = int(h * 0.06), int(h * 0.94)
    fh = foot - top
    x0, x1 = w // 2 - int(w * 0.13), w // 2 + int(w * 0.13)
    d.ellipse([x0 + 10, top, x1 - 10, top + int(fh * 0.13)], fill=colours["skin"])
    d.rectangle([x0 - 15, top + int(fh * 0.14), x1 + 15, top + int(fh * 0.50)], fill=colours["shirt"])
    d.rectangle([x0, top + int(fh * 0.50), x1, top + int(fh * 0.93)], fill=colours["trousers"])
    d.rectangle([x0 - 5, top + int(fh * 0.93), x1 + 5, foot], fill=colours["shoes"])
    return im


# ---- prompts and configuration --------------------------------------------------------------------------------------------
def test_a_guided_prompt_repaints_the_puppet_as_the_character(data):
    style = data["styles"]["semi_real"]
    side = anim.guided_frame_prompt(data["animations"], "walk", "right", "woman", style, "magenta")["positive"]
    assert side.startswith("Repaint the rough figure in the first image") and "the woman in the second image" in side
    assert "walking in a side view facing right" in side and "#FF00FF" in side and style["edit_name"] in side
    assert "fits inside the picture" in side                          # without this sentence the figure came out zoomed in and cropped
    behind = anim.guided_frame_prompt(data["animations"], "walk", "up", "man", style, "magenta")["positive"]
    assert "seen from directly behind" in behind
    robot = anim.guided_frame_prompt(data["animations"], "walk", "right", "character", style, "green", freeform=True)["positive"]
    assert "skin tone" not in robot and "#00FF00" in robot            # a freeform character is not told to keep human hair and skin


def test_only_the_front_view_gets_the_extra_pose_wording(data):
    """Its pose barely differs from the standing sprite, so the model needs telling that the pose changes."""
    style = data["styles"]["semi_real"]
    text = lambda d: anim.guided_frame_prompt(data["animations"], "walk", d, "woman", style, "magenta")["positive"]
    assert "caught in mid-step" in text("down") and "The pose changes from the standing picture" in text("down")
    for d in ("right", "left", "up"):
        assert "caught in mid-step" not in text(d) and "The pose changes" not in text(d)


def test_the_passing_step_gets_its_own_wording_in_side_views(data):
    style = data["styles"]["semi_real"]
    text = lambda d, kind: anim.guided_frame_prompt(data["animations"], "walk", d, "woman", style, "magenta", kind=kind)["positive"]
    for d in ("right", "left"):
        assert "The legs pass each other" in text(d, "passing")
        assert "The legs pass each other" not in text(d, "contact") and "The legs pass each other" not in text(d, None)
    assert "The legs pass each other" not in text("up", "passing")
    assert "caught in mid-step" in text("down", "step") and "The legs pass each other" not in text("down", "lift")


def test_the_walk_is_guided_and_says_how_many_frames_it_can_have(data):
    walk = data["animations"]["clips"]["walk"]
    assert walk.get("guide") and anim.frame_counts(walk) == [4, 8] and set(pose.WALK_CYCLES) == set(anim.frame_counts(walk))
    assert anim.frame_counts(data["animations"]["clips"]["run"]) == [4]                 # a text-driven clip: counted from its poses
    assert anim.frame_counts(data["animations"]["clips"]["idle"]) == [4, 6, 8]
    assert set(data["animations"]["guided_strategy"]) - {"_note"} == {"down", "left", "right", "up"}


def test_the_frame_rate_follows_the_frame_count(data):
    walk = data["animations"]["clips"]["walk"]
    assert anim.clip_fps(walk, 4) == 5.0 and anim.clip_fps(walk, 8) == 10.0             # one step cycle lasts 0.8 s either way
    assert anim.clip_fps(data["animations"]["clips"]["run"], 4) == 12                    # a clip without a cycle keeps its fixed rate


def test_a_guided_clip_accepts_only_its_own_frame_counts(data):
    assert service.clean_animations({"clips": ["walk"], "frames": 8}, data)["frames"] == 8
    with pytest.raises(ValueError, match="no 5-frame version .*available: 4, 8"):
        service.clean_animations({"clips": ["walk"], "frames": 5}, data)


# ---- what makes a draw usable ---------------------------------------------------------------------------------------------
def test_a_frame_that_matches_its_puppet_is_accepted():
    puppet, cut = puppet_and_cut("contact_a")
    score, detail = anim.guided_score(cut, puppet, "side", cut)
    assert detail["ok"] and detail["fits"] and detail["facing"] and detail["stride_error"] == 0 and score > 3.0


def test_a_frame_drawn_zoomed_in_is_rejected():
    """Measured: some draws filled the whole picture with the figure and brought the feet together."""
    puppet, cut = puppet_and_cut("contact_a")
    score, detail = anim.guided_score(zoomed(cut, 1.3), puppet, "side", cut)
    assert not detail["fits"] and not detail["ok"] and score < anim.guided_score(cut, puppet, "side", cut)[0]


def test_a_stride_where_the_puppet_has_the_legs_together_is_rejected():
    puppet, _ = puppet_and_cut("contact_a")
    _, passing_cut = puppet_and_cut("passing_a")
    _, detail = anim.guided_score(passing_cut, puppet, "side", passing_cut)
    assert detail["fits"] and detail["stride_error"] > 0.2 and not detail["ok"]


def test_a_side_view_that_turned_to_face_the_camera_is_rejected():
    """Measured: one draw came out facing the camera with its legs spread. The feet were where the puppet's were, so every other check
    passed; its chest was 1.47x as wide as the character's own side view."""
    puppet, cut = puppet_and_cut("contact_a")
    wide = cut.copy()
    box = anim.figure_box(cut)
    h, cx = box[3] - box[1], (box[0] + box[2]) // 2
    ImageDraw.Draw(wide).rectangle([cx - int(0.3 * h), box[1] + int(0.27 * h), cx + int(0.3 * h), box[1] + int(0.43 * h)], fill=(60, 120, 85, 255))
    _, detail = anim.guided_score(wide, puppet, "side", cut)
    assert detail["fits"] and detail["stride_error"] == 0 and not detail["facing"] and not detail["ok"]
    assert anim.guided_score(cut, puppet, "side", cut)[1]["facing"]


def test_a_front_step_that_is_only_the_standing_pose_again_is_rejected():
    """Measured: the model copied the standing sprite (the second image) four times; the legs differed from it by 0.02-0.05."""
    puppet, step = puppet_and_cut(pose.FRONT_POSES["step_a"], "front")
    _, stand = puppet_and_cut(STAND, "front")
    _, moved = anim.guided_score(step, puppet, "front", stand)
    assert moved["ok"] and moved["legs"] >= anim.MIN_MOVED
    _, copied = anim.guided_score(stand, puppet, "front", stand)
    assert not copied["ok"] and copied["fits"] and copied["legs"] < 0.02


def test_a_back_view_is_held_to_the_same_movement_check_but_has_no_stride_to_compare():
    puppet, step = puppet_and_cut(pose.FRONT_POSES["lift_a"], "back")
    _, stand = puppet_and_cut(STAND, "back")
    detail = anim.guided_score(step, puppet, "back", stand)[1]
    assert detail["ok"] and detail["stride_error"] == 0


def test_an_empty_draw_scores_below_everything():
    puppet, _ = puppet_and_cut("contact_a")
    score, detail = anim.guided_score(Image.new("RGBA", puppet.size), puppet, "side", Image.new("RGBA", puppet.size))
    assert score < 0 and not detail["ok"]


def test_stride_contrast_separates_a_walk_from_a_slide():
    walking = [puppet_and_cut(n)[1] for n in pose.WALK_CYCLES[4]]
    sliding = [puppet_and_cut("contact_a")[1]] * 4
    assert anim.stride_contrast(walking) > 2.5 > anim.STRIDE_CONTRAST_LIMIT > anim.stride_contrast(sliding) == pytest.approx(1.0)


def test_run_phase_similarity_catches_a_repeated_two_pose_cycle():
    contact = puppet_and_cut("contact_a")[1]
    passing = puppet_and_cut("passing_a")[1]
    assert anim.run_phase_similarity([contact, passing, contact, passing]) == (1.0, 1.0)
    assert anim.run_phase_similarity([contact, passing, passing, contact])[0] < anim.RUN_PHASE_SIMILARITY_LIMIT
    assert anim.run_phase_similarity([contact, passing]) is None


def test_a_repeated_run_is_flagged_in_the_clip_report(world, data):
    lib, npc_id, make = world
    _, animator, job = make([])
    animator.make_direction(job, {}, lib.get(npc_id), data, "run", "right", 100)
    animator.assemble(job, lib.get(npc_id), data, "run")
    report = lib.get(npc_id)["assets"]["animations"]["run"]["report"]
    assert report["directions"]["right"]["phase_similarity"] == [1.0, 1.0]
    assert any("two repeated frames" in flag for flag in report["flags"])


def test_the_step_bob_dips_the_body_and_leaves_the_feet_alone():
    still = anim.align({"right": [puppet_and_cut("passing_a", size=(300, 450))[1]]})["right"][0]
    frames = anim.add_step_bob([still] * 4, pose.step_bob(4))                    # contact, passing, contact, passing
    boxes = [anim.figure_box(f) for f in frames]
    assert len({b[3] for b in boxes}) == 1                                       # the feet stay on the baseline
    assert boxes[0][1] > boxes[1][1] and boxes[0][1] == boxes[2][1]              # the head is lower as a foot lands than as the legs pass
    assert boxes[0][1] - boxes[1][1] <= 0.02 * anim.FIGURE_H                     # a step, not a bounce


# ---- a whole clip with a stand-in for ComfyUI -----------------------------------------------------------------------------
class FakeComfy:
    """Answers each edit by 'painting' the puppet it was given as the first reference. `plan` lists what to do per call:
    'good' returns the puppet as it is, 'zoomed' returns it blown up until it fills the picture."""
    models = DEFAULT_MODELS

    def __init__(self, plan):
        self.plan, self.files, self.graphs = list(plan), {}, []

    def upload_image(self, data, filename):
        name = f"npc_generator/{filename} [temp]"
        self.files[name] = data
        return name

    def run(self, graph, cancel=None):
        self.graphs.append(graph)
        kind = self.plan.pop(0) if self.plan else "good"
        puppet = Image.open(io.BytesIO(self.files[graph["20"]["inputs"]["image"]])).convert("RGB")
        draw = on_magenta(puppet)
        return png(zoomed(draw, 1.4) if kind == "zoomed" else draw)


@pytest.fixture()
def world(tmp_path, data):
    lib = Library(tmp_path / "library")
    npc = lib.create({"setting": "modern", "style": "semi_real", "seed": 5, "freeform": False,
                      "profile": {"first_name": "Mara", "last_name": "Test", "age": 30, "gender": "female", "occupation": "clerk"},
                      "appearance": {"outfit": "a green shirt and dark trousers", "hair": "black long hair"}})
    size = config.DEFAULTS["quality"]["draft"]["anim"]
    lib.write_asset(npc["id"], "sprite_raw.png", png(standing((768, 1152))))
    lib.write_asset(npc["id"], "sprite.png", png(standing((768, 1152))))
    side_view = on_magenta(pose.paint(pose.POSES["contact_a"], "side", PALETTE, size=tuple(size), height=anim.GUIDE_HEIGHT, floor=anim.GUIDE_FLOOR))
    lib.write_asset(npc["id"], "animations/views/right.png", png(side_view))
    lib.update(npc["id"], lambda n: n["assets"].update(
        sprite={"file": "sprite.png", "raw": "sprite_raw.png", "key": "magenta", "created": "t"},
        animation_views={"right": {"file": "animations/views/right.png", "size": list(size)}}))

    def make(plan, dirs=("right",), frames=4, candidates=1):
        fake = FakeComfy(plan)
        animator = anim.Animator(fake, lib, copy.deepcopy(config.DEFAULTS))
        job = types.SimpleNamespace(npc_id=npc["id"], cancel=threading.Event(), cache={}, touch=lambda: None,
                                    options={"quality": "draft", "animations": {"frames": frames, "candidates": candidates, "dirs": list(dirs),
                                                                                 "mirror_left": True, "clips": ["walk"]}})
        return fake, animator, job
    return lib, npc["id"], make


def test_each_frame_is_repainted_from_its_puppet_and_the_sprite(world, data):
    lib, npc_id, make = world
    fake, animator, job = make([])
    animator.make_direction(job, {}, lib.get(npc_id), data, "walk", "right", 100)
    assert len(fake.graphs) == 4                                                  # one draw per frame: each was good
    first = fake.graphs[0]
    assert "puppet_right_0" in first["20"]["inputs"]["image"] and "anim_front" in first["22"]["inputs"]["image"]
    assert "Repaint the rough figure" in first["4"]["inputs"]["prompt"] and first["5"]["inputs"]["seed"] == 100
    assert len({g["5"]["inputs"]["seed"] for g in fake.graphs}) == 1              # one seed for the whole clip keeps the character the same
    scores = lib.get(npc_id)["assets"]["animations"]["walk"]["directions"]["right"]["scores"]
    assert [s["frame"] for s in scores] == [0, 1, 2, 3] and all(s["ok"] and s["candidate"] == 0 for s in scores)
    puppets = [Image.open(io.BytesIO(fake.files[g["20"]["inputs"]["image"]])) for g in fake.graphs]
    assert anim.foot_spread(np.asarray(puppets[0].convert("RGB")).min(axis=2) < 235) > 2 * anim.foot_spread(np.asarray(puppets[1].convert("RGB")).min(axis=2) < 235)


def test_the_puppet_gets_the_characters_hair_colour_from_the_data(world, data):
    lib, npc_id, make = world
    fake, animator, job = make([])
    animator.make_direction(job, {}, lib.get(npc_id), data, "walk", "right", 100)
    assert job.cache["palette"]["hair"] == pose.hair_colour("black long hair")
    puppet = np.asarray(Image.open(io.BytesIO(fake.files[fake.graphs[0]["20"]["inputs"]["image"]])).convert("RGB")).astype(int)
    assert (np.abs(puppet - np.array(pose.hair_colour("black long hair"))).sum(axis=2) < 15).any()      # it is in the drawing


def test_a_seed_that_fails_on_the_first_probe_is_dropped_for_the_whole_clip(world, data):
    lib, npc_id, make = world
    fake, animator, job = make(["zoomed", "good"])                                # the first seed's stride comes out zoomed in
    animator.make_direction(job, {}, lib.get(npc_id), data, "walk", "right", 100)
    seeds = [g["5"]["inputs"]["seed"] for g in fake.graphs]
    assert seeds == [100] + [100 + 7919] * 4                                       # one draw wasted, then every frame with the second seed
    meta = lib.get(npc_id)["assets"]["animations"]["walk"]["directions"]["right"]
    assert meta["seed"] == 100 + 7919 and all(s["ok"] for s in meta["scores"])


def test_both_probe_frames_must_pass_before_a_seed_is_kept(world, data):
    """A seed that draws the stride well may draw the passing step as a stride again (measured), so the passing step is a probe too."""
    lib, npc_id, make = world
    fake, animator, job = make(["good", "zoomed", "good", "good"])                # stride fine, passing step zoomed in: try the next seed
    animator.make_direction(job, {}, lib.get(npc_id), data, "walk", "right", 100)
    seeds = [g["5"]["inputs"]["seed"] for g in fake.graphs]
    assert seeds == [100, 100] + [100 + 7919] * 4
    assert lib.get(npc_id)["assets"]["animations"]["walk"]["directions"]["right"]["seed"] == 100 + 7919


def test_a_later_frame_that_fails_is_redrawn_without_changing_the_clips_seed(world, data):
    lib, npc_id, make = world
    fake, animator, job = make(["good", "good", "zoomed", "good"])                # both probes pass; frame 2 is zoomed in once
    animator.make_direction(job, {}, lib.get(npc_id), data, "walk", "right", 100)
    seeds = [g["5"]["inputs"]["seed"] for g in fake.graphs]
    assert seeds == [100, 100, 100, 100 + 7919, 100]                               # only frame 2 borrowed another seed, once
    meta = lib.get(npc_id)["assets"]["animations"]["walk"]["directions"]["right"]
    assert meta["seed"] == 100 and meta["scores"][2]["candidate"] == 1 and meta["scores"][2]["ok"]


def test_a_frame_that_never_comes_out_right_keeps_its_best_draw_and_is_flagged(world, data):
    lib, npc_id, make = world
    fake, animator, job = make(["zoomed"] * anim.GUIDE_SEEDS)                      # no seed draws the stride: every probe fails
    animator.make_direction(job, {}, lib.get(npc_id), data, "walk", "right", 100)
    assert len(fake.graphs) == anim.GUIDE_SEEDS + 3                                # the failed probes, then the other three frames
    meta = lib.get(npc_id)["assets"]["animations"]["walk"]["directions"]["right"]
    assert meta["seed"] == 100                                                     # nothing was better, so the first seed stays
    assert meta["scores"][0]["ok"] is False and all(s["ok"] for s in meta["scores"][1:])
    animator.assemble(job, lib.get(npc_id), data, "walk")
    flags = lib.get(npc_id)["assets"]["animations"]["walk"]["report"]["flags"]
    assert any("frame 1 did not come out in the pose drawn for it" in f for f in flags)


def test_more_candidates_means_every_draw_is_made_and_the_best_kept(world, data):
    lib, npc_id, make = world
    fake, animator, job = make(["zoomed", "good", "good"] + ["good"] * 9, candidates=2)
    animator.make_direction(job, {}, lib.get(npc_id), data, "walk", "right", 100)
    assert len(fake.graphs) == 8                                                  # 4 frames x 2 draws, no early stop
    assert lib.get(npc_id)["assets"]["animations"]["walk"]["directions"]["right"]["scores"][0]["candidate"] == 1


def test_an_assembled_walk_has_a_step_bob_a_cycle_rate_and_a_healthy_report(world, data):
    lib, npc_id, make = world
    fake, animator, job = make([], dirs=("right", "left"))
    animator.make_direction(job, {}, lib.get(npc_id), data, "walk", "right", 100)
    animator.assemble(job, lib.get(npc_id), data, "walk")
    meta = lib.get(npc_id)["assets"]["animations"]["walk"]
    assert meta["fps"] == 5.0 and meta["frames"] == 4 and meta["order"] == ["left", "right"] and meta["mirrored"] == {"left": "right"}
    report = meta["report"]["directions"]["right"]
    assert report["stride_contrast"] > anim.STRIDE_CONTRAST_LIMIT and meta["report"]["ok"], meta["report"]["flags"]
    boxes = [anim.figure_box(Image.open(lib.resolve(npc_id, rel)).convert("RGBA")) for rel in meta["files"]["right"]]
    assert len({b[3] for b in boxes}) == 1 and boxes[0][1] > boxes[1][1]         # feet planted; the head lower on contact than on passing
    assert meta["directions"]["right"]["guided"] is True and meta["directions"]["right"]["refs"] == ["front"]


def test_a_walk_that_slides_is_flagged_even_when_every_frame_fits(world, data):
    """All four frames drawn as the same wide stride: each one fits its picture, but together they are a slide, not a walk."""
    lib, npc_id, make = world
    fake, animator, job = make([])
    animator.make_direction(job, {}, lib.get(npc_id), data, "walk", "right", 100)
    npc = lib.get(npc_id)
    contact = puppet_and_cut("contact_a", size=tuple(config.DEFAULTS["quality"]["draft"]["anim"]))[0]
    for i in range(4):
        lib.write_asset(npc_id, f"animations/walk/raw/right_{i}.png", png(on_magenta(contact)))
    animator.assemble(job, lib.get(npc_id), data, "walk")
    flags = lib.get(npc_id)["assets"]["animations"]["walk"]["report"]["flags"]
    assert any("never come together and apart" in f for f in flags)


def test_the_stride_band_accepts_what_looked_right_and_rejects_what_did_not():
    """Feet spreads measured on real draws against their puppets (contact 0.48-0.53, passing 0.14-0.16): the ones judged good by eye
    had 0.42-0.60 and 0.15-0.27; the failure, a stride drawn with the feet together, 0.28."""
    for spread, target in [(0.42, 0.53), (0.60, 0.53), (0.27, 0.158), (0.15, 0.158), (0.255, 0.158), (0.12, 0.158), (0.145, 0.207), (0.278, 0.157)]:
        assert anim.stride_error(spread, target) == 0, (spread, target)
    assert anim.stride_error(0.283, 0.53) > 0.1                                   # a stride drawn with the feet together
    assert anim.stride_error(0.55, 0.158) > 0.2                                   # a wide stride where the legs should pass


def test_match_colours_pulls_a_frame_toward_the_characters_own_colours():
    """A frame redrawn with another seed can come back in a paler shirt and darker trousers; it should not flicker against the rest."""
    def figure(shirt, trousers):
        im = Image.new("RGBA", (120, 300), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        d.ellipse([40, 10, 80, 50], fill=(200, 150, 120, 255))
        d.rectangle([25, 55, 95, 150], fill=shirt + (255,))
        d.rectangle([35, 150, 85, 290], fill=trousers + (255,))
        return im
    reference = figure((70, 140, 90), (30, 90, 70))
    drifted = figure((150, 175, 155), (10, 40, 35))                               # a paler shirt, darker trousers
    mean = lambda im: np.asarray(im)[np.asarray(im)[..., 3] > 200][:, :3].mean(axis=0)
    matched = anim.match_colours(drifted, reference)
    assert np.abs(mean(matched) - mean(reference)).max() < 6 < np.abs(mean(drifted) - mean(reference)).max()
    assert np.array_equal(np.asarray(matched)[..., 3], np.asarray(drifted)[..., 3])             # the silhouette is untouched
    assert np.abs(np.asarray(anim.match_colours(reference, reference)).astype(int) - np.asarray(reference).astype(int)).max() <= 2   # a frame that already matches stays
    assert anim.match_colours(Image.new("RGBA", (50, 50)), reference).getbbox() is None         # nothing to match: an empty frame is returned as it is


def test_an_assembled_guided_walk_has_its_frames_matched_to_the_standing_view(world, data):
    lib, npc_id, make = world
    fake, animator, job = make([])
    animator.make_direction(job, {}, lib.get(npc_id), data, "walk", "right", 100)
    for i in range(4):                                                             # frame 1 comes back in a much darker outfit
        if i == 1:
            dark = Image.open(io.BytesIO(lib.read_asset(npc_id, f"animations/walk/raw/right_{i}.png"))).convert("RGB")
            arr = np.asarray(dark).astype(float)
            arr[arr.min(axis=2) < 200] *= 0.45                                     # the figure, not the magenta backdrop
            lib.write_asset(npc_id, f"animations/walk/raw/right_{i}.png", png(Image.fromarray(arr.astype(np.uint8))))
    animator.assemble(job, lib.get(npc_id), data, "walk")
    files = lib.get(npc_id)["assets"]["animations"]["walk"]["files"]["right"]
    means = []
    for rel in files:
        a = np.asarray(Image.open(lib.resolve(npc_id, rel)).convert("RGBA")).astype(float)
        means.append(a[a[..., 3] > 200][:, :3].mean())
    assert max(means) - min(means) < 14                                            # without the match frame 1 would be about 60 darker


def test_each_frame_is_drawn_with_the_wording_for_its_kind_of_pose(world, data):
    lib, npc_id, make = world
    fake, animator, job = make([])
    animator.make_direction(job, {}, lib.get(npc_id), data, "walk", "right", 100)
    passing = ["The legs pass each other" in g["4"]["inputs"]["prompt"] for g in fake.graphs]
    assert passing == [False, True, False, True]                                   # contact, passing, contact, passing


def test_the_colour_match_gives_the_same_picture_every_time():
    """Found on a real run: re-assembling the same frames twice gave different pictures, with bands of noise across the legs. numpy's
    BLAS returned a different answer for the same 130,000-row product in about 1 run in 50 on the development machine."""
    im = Image.new("RGBA", (400, 600), (0, 0, 0, 0))
    ImageDraw.Draw(im).rectangle([50, 20, 350, 580], fill=(70, 140, 90, 255))             # about 168,000 opaque pixels
    ref = Image.new("RGBA", (400, 600), (0, 0, 0, 0))
    ImageDraw.Draw(ref).rectangle([50, 20, 350, 580], fill=(150, 175, 155, 255))
    first = np.asarray(anim.match_colours(im, ref)).copy()
    assert all(np.array_equal(first, np.asarray(anim.match_colours(im, ref))) for _ in range(12))
    lab = anim._rgb_to_lab(np.array([[70.0, 140.0, 90.0], [255.0, 255.0, 255.0], [0.0, 0.0, 0.0]]))
    assert lab[1] == pytest.approx([100, 0, 0], abs=0.05) and lab[2] == pytest.approx([0, 0, 0], abs=0.05)   # white and black are where Lab puts them


def test_no_pixel_code_uses_the_matrix_multiply_operator():
    """See anim._apply: a threaded matrix product corrupted whole bands of a picture now and then. Write small colour maths out by hand."""
    for path in sorted((pathlib.Path(__file__).resolve().parent.parent / "npcgen").glob("*.py")):
        uses = [n.lineno for n in ast.walk(ast.parse(path.read_text("utf-8"))) if isinstance(n, (ast.BinOp, ast.AugAssign)) and isinstance(n.op, ast.MatMult)]
        assert not uses, f"{path.name} uses @ on line {uses}"


def test_only_flat_shaded_styles_switch_the_guided_walk_off(data):
    """Measured on pixel art: the model hands the puppet back as it is (flat capsules already look like pixel art). Cartoon is flat too
    and was not verified, so it is off as well; the painterly and photographic styles are on."""
    styles = data["styles"]
    assert styles["pixel"]["guided_walk"] is False and styles["cartoon"]["guided_walk"] is False
    assert styles["semi_real"].get("guided_walk", True) and styles["photoreal"].get("guided_walk", True)


def test_a_flat_shaded_style_draws_the_walk_from_words_and_the_canonical_view(world, data):
    lib, npc_id, make = world
    lib.update(npc_id, lambda n: n.update(style="pixel"))
    fake, animator, job = make([])
    animator.make_direction(job, {}, lib.get(npc_id), data, "walk", "right", 100)
    assert len(fake.graphs) == 4                                                   # one draw per frame: no probing, no retries
    first = fake.graphs[0]
    assert "anim_view_right" in first["20"]["inputs"]["image"] and "anim_front" in first["22"]["inputs"]["image"]
    prompt = first["4"]["inputs"]["prompt"]
    assert prompt.startswith("Show the same character walking in a side view facing right") and "left leg forward" in prompt
    assert not any("puppet" in name for name in fake.files)                        # nothing was drawn
    meta = lib.get(npc_id)["assets"]["animations"]["walk"]["directions"]["right"]
    assert meta["guided"] is False and meta["refs"] == ["canonical", "front"] and meta["seed"] == 100


def test_a_walk_made_of_words_is_still_bobbed_paced_and_flagged_when_it_slides(world, data):
    """The fake returns the same picture for every frame: four copies of one stride, which is what the words alone used to give."""
    lib, npc_id, make = world
    lib.update(npc_id, lambda n: n.update(style="pixel"))
    fake, animator, job = make([], dirs=("right",))
    animator.make_direction(job, {}, lib.get(npc_id), data, "walk", "right", 100)
    animator.assemble(job, lib.get(npc_id), data, "walk")
    meta = lib.get(npc_id)["assets"]["animations"]["walk"]
    assert meta["fps"] == 5.0
    boxes = [anim.figure_box(Image.open(lib.resolve(npc_id, rel)).convert("RGBA")) for rel in meta["files"]["right"]]
    assert len({b[3] for b in boxes}) == 1 and boxes[0][1] > boxes[1][1]           # the step's up-and-down comes back for every walk
    assert any("never come together and apart" in f for f in meta["report"]["flags"])
    assert not any("did not come out in the pose" in f for f in meta["report"]["flags"])      # words have no pose to miss


def test_an_eight_frame_walk_goes_through_the_whole_path(world, data):
    """The 8-frame cycle (contact, down, passing, up, twice) has not been run on a GPU; this checks its plumbing with the stand-in."""
    lib, npc_id, make = world
    fake, animator, job = make([], dirs=("right", "left"), frames=8)
    animator.make_direction(job, {}, lib.get(npc_id), data, "walk", "right", 100)
    assert len(fake.graphs) == 8
    passing = ["The legs pass each other" in g["4"]["inputs"]["prompt"] for g in fake.graphs]
    assert passing == [False, True, False, False, False, False, True, False]        # drawn in the order 0, 2 (the probes), 1, 3, 4...: frames 2 and 6 are the passing steps
    assert sum(passing) == 2
    animator.assemble(job, lib.get(npc_id), data, "walk")
    meta = lib.get(npc_id)["assets"]["animations"]["walk"]
    assert meta["frames"] == 8 and meta["fps"] == 10.0 and len(meta["files"]["right"]) == 8 and len(meta["files"]["left"]) == 8
    boxes = [anim.figure_box(Image.open(lib.resolve(npc_id, rel)).convert("RGBA")) for rel in meta["files"]["right"]]
    heads = [b[1] for b in boxes]
    assert heads[1] == max(heads) and heads[3] == min(heads)                       # lowest just after a foot lands, highest as the body rises
    assert meta["report"]["directions"]["right"]["stride_contrast"] > anim.STRIDE_CONTRAST_LIMIT
