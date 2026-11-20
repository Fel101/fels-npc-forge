import numpy as np
import pytest
from PIL import Image, ImageDraw

from npcgen import anim, roll, service
from npcgen.jobs import Jobs


@pytest.fixture(scope="module")
def data():
    return roll.load_data()


def figure(height=400, legs="together", head=(60, 50), width=200, x_shift=0, torso_x=100):
    """A stick-ish person on a transparent canvas: head, torso, arms and two legs (together or apart)."""
    im = Image.new("RGBA", (width, height + 20), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    cx = torso_x + x_shift
    hw, hh = head
    d.ellipse([cx - hw // 2, 5, cx + hw // 2, 5 + hh], fill=(90, 50, 30, 255))                      # head
    d.rectangle([cx - 35, 5 + hh, cx + 35, 5 + hh + 140], fill=(30, 110, 60, 255))                  # torso
    d.rectangle([cx - 55, 5 + hh + 10, cx - 35, 5 + hh + 120], fill=(200, 160, 120, 255))           # arms
    d.rectangle([cx + 35, 5 + hh + 10, cx + 55, 5 + hh + 120], fill=(200, 160, 120, 255))
    top, bottom = 5 + hh + 140, height
    if legs == "together":
        d.rectangle([cx - 25, top, cx + 25, bottom], fill=(40, 40, 80, 255))
    else:                                                                                             # a wide stride
        d.polygon([(cx - 20, top), (cx + 20, top), (cx + 20 + 60, bottom), (cx - 20 + 60, bottom)], fill=(40, 40, 80, 255))
        d.polygon([(cx - 20, top), (cx + 20, top), (cx + 20 - 60, bottom), (cx - 20 - 60, bottom)], fill=(40, 40, 80, 255))
    return im


def test_aligned_frames_share_a_height_a_baseline_and_a_centre_line():
    # the last frame is ~30% smaller than the others, like the back-view frames of a real generated walk (555px vs 770px)
    frames = {"right": [figure(360, "apart", torso_x=70), figure(400, "together", torso_x=120), figure(380, "apart", torso_x=100),
                        figure(270, "together", torso_x=110)]}
    out = anim.align(frames)["right"]
    boxes = [anim.figure_box(f) for f in out]
    assert all(f.size == (anim.CANVAS, anim.CANVAS) for f in out)
    assert all(abs(b[3] - anim.BASELINE) <= 1 for b in boxes)                                        # feet on the baseline
    heights = [b[3] - b[1] for b in boxes]
    assert max(heights) - min(heights) <= 3                                                          # sizes fully normalised
    centres = [(anim._torso_x(f)) for f in out]
    assert all(abs(c - anim.CANVAS / 2) <= 2 for c in centres)                                       # torso on the centre line


def test_leg_variation_tells_a_static_walk_from_a_real_one():
    static = [figure(legs="together") for _ in range(4)]
    walking = [figure(legs="apart"), figure(legs="together"), figure(legs="apart"), figure(legs="together")]
    assert anim.leg_variation(static) < 0.02 < anim.STATIC_LEG_VARIATION < 0.2 < anim.leg_variation(walking)


def test_head_stability_notices_a_changed_head():
    same = [figure(head=(60, 50)) for _ in range(3)]
    changed = [figure(head=(60, 50)), figure(head=(110, 30)), figure(head=(30, 60))]
    assert anim.head_stability(same) > 0.99 and anim.head_stability(changed) < anim.HEAD_DRIFT_LIMIT


def test_candidate_score_prefers_the_right_head_and_a_real_step():
    canonical = figure(legs="together")
    good = figure(legs="apart")                                     # same head, legs moved
    static = figure(legs="together")                                # same head, nothing moved
    wrong_head = figure(legs="apart", head=(120, 25))
    assert anim.candidate_score(good, canonical)[0] > anim.candidate_score(static, canonical)[0]
    assert anim.candidate_score(good, canonical)[0] > anim.candidate_score(wrong_head, canonical)[0]


def test_palette_coverage_flags_a_colour_change():
    ref = figure()
    pal = anim.palette(ref)
    assert anim.palette_coverage(ref, pal) > 0.95
    recoloured = ref.copy()
    px = recoloured.load()
    for y in range(recoloured.height):
        for x in range(recoloured.width):
            r, g, b, a = px[x, y]
            if a:
                px[x, y] = (255 - r, 255 - g, 255 - b, a)
    assert anim.palette_coverage(recoloured, pal) < 0.2


def test_procedural_idle_breathes_in_the_upper_body_only():
    still = anim.align({"down": [figure()]})["down"][0]              # what assemble() hands over: already on the canvas
    frames = anim.procedural_idle(still, 8, lift=0.012, chest=0.016)
    assert len(frames) == 8 and all(f.size == still.size for f in frames)
    assert frames[0].tobytes() == still.tobytes()                    # every loop starts and ends at rest
    boxes = [anim.figure_box(f) for f in frames]
    assert len({b[3] for b in boxes}) == 1                           # the feet never move
    rise = [boxes[0][1] - b[1] for b in boxes]
    assert rise[0] == 0 and rise[4] == max(rise) >= 4                # the head rises to the top of the breath, at half time
    assert rise[1] == rise[7] and rise[2] == rise[6] and rise[3] == rise[5]      # and settles back the same way
    assert max(rise) <= 0.02 * (boxes[0][3] - boxes[0][1])           # a breath, not a bounce: under 2% of the figure's height
    below_hips = slice(int(anim.BASELINE - 0.40 * anim.FIGURE_H), None)
    still_legs = np.asarray(still)[below_hips].astype(int)
    assert all(np.abs(np.asarray(f)[below_hips].astype(int) - still_legs).max() <= 1 for f in frames)   # the legs are untouched


def test_procedural_idle_survives_an_empty_frame():
    assert len(anim.procedural_idle(Image.new("RGBA", (64, 64)), 4, lift=0.01)) == 4


def test_a_clip_keeps_its_cycle_length_whatever_the_frame_count():
    walk = {"fps": 8, "cycle": 0.8}
    assert anim.clip_fps(walk, 4) == 5.0 and anim.clip_fps(walk, 8) == 10.0
    assert anim.clip_fps({"fps": 12}, 4) == 12                       # no cycle given: the fixed rate


@pytest.mark.parametrize("dirs,mirror,expected", [
    (["down", "left", "right", "up"], True, ["down", "right", "up"]),
    (["left"], True, ["right"]),
    (["down", "left"], False, ["down", "left"]),
    (["up"], True, ["up"]),
])
def test_left_is_a_mirror_of_right_unless_asked_otherwise(dirs, mirror, expected):
    assert anim.generated_directions(dirs, mirror) == expected


def test_animation_prompts_use_the_clips_verb_and_pose(data):
    style = data["styles"]["pixel"]
    run = anim.frame_prompt(data["animations"], "run", "right", 0, 4, style, "magenta")["positive"]
    assert "running in a side view facing right" in run and "front knee high" in run and "16-bit pixel art style" in run
    front = anim.frame_prompt(data["animations"], "run", "down", 1, 4, style, "green")["positive"]
    assert "toward the camera" in front and "#00FF00" in front
    walk = anim.frame_prompt(data["animations"], "walk", "right", 1, 4, style, "magenta")["positive"]        # the fallback for flat-shaded styles
    assert "walking in a side view facing right" in walk and "legs close together as one foot passes the other" in walk
    attack = anim.frame_prompt(data["animations"], "attack", "right", 2, 4, style, "magenta")["positive"]
    assert "attacking in a side view facing right" in attack and "arm fully extended in a punch" in attack


def test_the_animation_request_is_validated(data):
    ok = service.clean_animations({"clips": ["walk", "idle", "nonsense"], "dirs": ["up", "down"], "frames": 8, "candidates": 9}, data)
    assert ok["clips"] == ["walk", "idle"] and ok["dirs"] == ["down", "up"] and ok["candidates"] == 4
    assert ok["kinds"] == {"walk": "generated", "idle": "procedural"}
    actions = service.clean_animations({"clips": ["run", "attack", "hurt", "cast", "death"], "frames": 4}, data)
    assert actions["kinds"] == {clip: "generated" for clip in ("run", "attack", "hurt", "cast", "death")}
    assert all(data["animations"]["clips"][clip]["experimental"] for clip in actions["clips"])
    for bad, message in [({"clips": ["nope"]}, "at least one animation"), ({"clips": ["walk"], "frames": 5}, "no 5-frame"),
                         ({"clips": ["walk"], "dirs": ["sideways"]}, "at least one direction"), ({"clips": ["walk"], "frames": "x"}, "numbers")]:
        with pytest.raises(ValueError, match=message):
            service.clean_animations(bad, data)


def test_an_empty_request_is_rejected_and_animations_alone_are_enough(data):
    with pytest.raises(ValueError, match="at least one asset"):
        service.clean_assets({}, data)
    assert service.clean_assets({"animations": {"clips": ["walk"]}}, data)["animations"]["dirs"] == ["down", "left", "right", "up"]


def test_generation_request_deduplicates_gpu_work_and_rejects_wrong_shapes(data):
    clean = service.clean_assets({"expressions": ["happy", "happy"], "animations": {
        "clips": ["walk", "walk"], "dirs": ["up", "up", "down"]}}, data)
    assert clean["expressions"] == ["happy"]
    assert clean["animations"]["clips"] == ["walk"]
    assert clean["animations"]["dirs"] == ["down", "up"]
    for bad in (["portrait"], {"expressions": "happy"}, {"portrait": 1}, {"animations": []}):
        with pytest.raises(ValueError):
            service.clean_assets(bad, data)


def test_the_planner_adds_the_sprite_views_frames_and_assembly_in_order(data):
    request = service.clean_assets({"animations": {"clips": ["walk", "idle"], "dirs": ["down", "left", "right", "up"]}}, data)
    steps = Jobs.plan({"assets": {}}, request)
    assert [(s["kind"], s["key"]) for s in steps] == [
        ("sprite", None), ("view", "right"), ("view", "up"),
        ("anim", "walk:down"), ("anim", "walk:right"), ("anim", "walk:up"), ("assemble", "walk"), ("assemble", "idle")]
    assert "needed for animation" in steps[0]["label"]
    have_sprite = Jobs.plan({"assets": {"sprite": {}}}, request)
    assert have_sprite[0]["kind"] == "view"
