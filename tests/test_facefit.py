"""npcgen/facefit.py: where a pair of glasses goes on a face, from the eyes, the width of the face and its front surface (a made-up face: tests/facefit_support.py)."""
import numpy as np
import pytest
from facefit_support import make_scan

from npcgen import facefit, glasses, glbmerge

DEG = np.pi / 180


@pytest.fixture(scope="module")
def pair():
    glb, _info = glasses.build({}, faces=10_000)
    return glb


@pytest.fixture(scope="module")
def parts(pair):
    return facefit.glasses_points(pair)


def fit(scan, pair, parts=None, **nudge):
    return facefit.fit(facefit.Scan(scan), pair, nudge or None, parts=parts)


def mid_of(scan):
    return (np.array(scan["eyes"]["left"]) + np.array(scan["eyes"]["right"])) / 2


# ---- the nudge ---------------------------------------------------------------------------------------------------------------------------------
def test_a_nudge_is_cleaned_filled_in_and_checked():
    assert facefit.clean_nudge(None) == {"up": 0.0, "forward": 0.0, "tilt": 0.0, "size": 1.0}
    assert facefit.clean_nudge({"up": 3, "size": 1.2}) == {"up": 3.0, "forward": 0.0, "tilt": 0.0, "size": 1.2}
    for bad, message in (({"sideways": 1}, "Unknown nudge"), ({"up": "3"}, "must be a number"), ({"up": True}, "must be a number"), ({"up": float("nan")}, "must be a number"),
                         ({"size": 3}, "between 0.5 and 2"), ({"tilt": -31}, "between -30 and 30"), ([1], "must be an object")):
        with pytest.raises(ValueError, match=message):
            facefit.clean_nudge(bad)


# ---- reading a scan ----------------------------------------------------------------------------------------------------------------------------
def test_a_scan_is_read_back_exactly_and_has_no_skin_outside_the_face():
    scan = facefit.Scan(make_scan(nose=0.0))
    mid = mid_of(make_scan(nose=0.0))
    centre = scan.surface(mid[0], mid[1])[0]
    assert centre == pytest.approx(mid[2] + 0.010, abs=1e-4)                       # the brow plane stands 10 mm in front of the eyes
    assert np.isnan(scan.surface(mid[0] + 0.15, mid[1])[0])                         # beyond the dome
    assert np.isnan(scan.surface(mid[0], mid[1] + 0.15)[0]) and np.isnan(scan.surface(mid[0], mid[1] - 0.2)[0])        # above and below the grid
    several = scan.surface([mid[0], mid[0] + 0.02], [mid[1], mid[1]])
    assert several.shape == (2,) and several[1] < several[0]                        # the dome falls away to the side


def test_a_hole_in_the_surface_is_filled_from_the_nearest_skin_only_at_its_edge():
    data = make_scan(nose=0.0, hole=(-0.004, 0.004, -0.004, 0.004))
    scan = facefit.Scan(data)
    mid = mid_of(data)
    assert np.isnan(scan.surface(mid[0], mid[1])[0])                                # inside the hole there is no skin
    assert np.isfinite(scan.surface(mid[0] + 0.0043, mid[1])[0])                    # at its edge the nearest cell with skin is used
    assert np.isfinite(scan.surface(mid[0] + 0.0105, mid[1])[0])


def test_something_that_is_not_a_scan_is_refused():
    with pytest.raises(ValueError, match="Not a face scan"):
        facefit.Scan({"version": 2})
    with pytest.raises(ValueError, match="Not a face scan"):
        facefit.Scan("no")


def test_the_pairs_points_are_sorted_by_part(parts):
    assert set(parts) == {"frame", "lens", "arms", "front"}
    assert len(parts["front"]) == len(parts["frame"]) + len(parts["lens"])
    assert parts["lens"][:, 2].max() < 0.001 and parts["front"][:, 2].max() < 0.0001       # nothing of the front stands in front of the bridge's origin
    assert parts["arms"][:, 2].min() < -0.1                                                  # the arms run back more than 100 mm
    assert abs(parts["front"][:, 0].max() + parts["front"][:, 0].min()) < 0.002              # made symmetric about the middle of the bridge


# ---- the turn ------------------------------------------------------------------------------------------------------------------------------------
def test_a_straight_face_needs_no_turn_and_a_normal_adult_gets_the_pair_as_made(pair, parts):
    scan = make_scan()
    placement = fit(scan, pair, parts)
    r = placement["report"]
    assert r["roll_deg"] == 0 and r["yaw_deg"] == 0
    assert r["scale"] == pytest.approx(1.0, abs=0.001) and r["scale_from_eyes"] == pytest.approx(1.0, abs=0.001) and r["scale_from_face"] == pytest.approx(1.0, abs=0.001)
    assert r["ipd_mm"] == pytest.approx(63.0, abs=0.1) and r["face_width_mm"] == pytest.approx(135.0, abs=0.1)
    assert placement["position"][:2] == pytest.approx(list(mid_of(scan)[:2]), abs=1e-5)         # on the middle of the eyes
    assert placement["rotation"] == pytest.approx([0, 0, 0, 1], abs=1e-5)
    assert placement["focus"] == pytest.approx(list(mid_of(scan)), abs=1e-4) and placement["height_m"] == pytest.approx(1.7)


@pytest.mark.parametrize("roll, expected", [(5, 5), (-7, -7), (0.5, 0), (20, 15), (-30, -15)])
def test_the_pair_is_rolled_with_the_eyes_inside_a_dead_band_and_a_limit(pair, parts, roll, expected):
    placement = fit(make_scan(roll=roll * DEG), pair, parts)
    assert placement["report"]["roll_deg"] == pytest.approx(expected, abs=0.1)
    axis = facefit.world_matrix(placement)[:3, 0]
    assert np.degrees(np.arcsin(axis[1] / np.linalg.norm(axis))) == pytest.approx(expected, abs=0.1)          # the pair's x axis follows the line between the eyes


@pytest.mark.parametrize("yaw, expected", [(4, 4), (-6, -6), (10, 10), (-20, -20), (0.5, 0), (45, 30), (-60, -30)])
def test_the_pair_is_turned_with_a_turned_head_inside_a_dead_band_and_a_limit(pair, parts, yaw, expected):
    scan = make_scan(yaw=yaw * DEG)
    placement = fit(scan, pair, parts)
    assert placement["report"]["yaw_deg"] == pytest.approx(expected, abs=0.1)
    if not 1 <= abs(yaw) <= 30:                                                       # (inside the dead band or past the limit the pair is turned less than the eyes are)
        return
    axis = facefit.world_matrix(placement)[:3, 0]
    eyes = np.array(scan["eyes"]["left"]) - np.array(scan["eyes"]["right"])
    assert axis / np.linalg.norm(axis) == pytest.approx(eyes / np.linalg.norm(eyes), abs=1e-3)


# ---- the size ------------------------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("scale", [0.8, 1.25, 1.6])
def test_the_pair_grows_with_the_figure(pair, parts, scale):
    r = fit(make_scan(scale=scale), pair, parts)["report"]
    assert r["scale"] == pytest.approx(scale, abs=0.01)
    assert r["ipd_mm"] == pytest.approx(63.0, abs=0.2) and r["face_width_mm"] == pytest.approx(135.0, abs=0.2)       # (as if it were 1.7 m tall: an adult's face)


def test_a_big_head_gets_a_big_pair_and_the_report_says_how_big_the_head_is(pair, parts):
    r = fit(make_scan(head=1.3), pair, parts)["report"]
    assert r["scale"] == pytest.approx(1.3, abs=0.01) and r["ipd_mm"] == pytest.approx(81.9, abs=0.3) and r["face_width_mm"] == pytest.approx(175.5, abs=0.3)
    small = fit(make_scan(scale=0.6, head=1.2), pair, parts)["report"]                  # a small figure with a big head: the pair is the figure's size times that
    assert small["scale"] == pytest.approx(0.72, abs=0.01) and small["ipd_mm"] == pytest.approx(75.6, abs=0.3)


def test_the_size_is_the_mean_of_the_eyes_and_the_face_and_the_eyes_alone_without_a_width(pair, parts):
    wide = make_scan(scale=1.0)
    wide["face_width"] = 0.135 * 1.5                                                  # a wide face with normal eyes
    r = fit(wide, pair, parts)["report"]
    assert r["scale_from_eyes"] == pytest.approx(1.0, abs=0.001) and r["scale_from_face"] == pytest.approx(1.5, abs=0.001)
    assert r["scale"] == pytest.approx(np.sqrt(1.5), abs=0.002)                       # (the geometric mean)
    alone = fit(make_scan(scale=1.2, face_width=False), pair, parts)["report"]
    assert alone["scale_from_face"] is None and alone["face_width_mm"] is None and alone["scale"] == pytest.approx(1.2, abs=0.01)


def test_an_absurd_face_is_kept_within_the_size_range(pair, parts):
    assert fit(make_scan(scale=8.0), pair, parts)["report"]["scale"] == facefit.SIZE_RANGE[1]
    assert fit(make_scan(scale=0.05), pair, parts)["report"]["scale"] == facefit.SIZE_RANGE[0]


# ---- the depth -----------------------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("nose", [0.0, 0.01, 0.022, 0.035])
@pytest.mark.parametrize("cheek", [0.0, 0.012])
def test_nothing_of_the_frame_or_the_lenses_is_inside_the_face(pair, parts, nose, cheek):
    r = fit(make_scan(nose=nose, cheek=cheek), pair, parts)["report"]
    assert r["behind_mm"] <= 0.01


def test_the_pair_rests_on_the_nose(pair, parts):
    r = fit(make_scan(nose=0.025), pair, parts)["report"]
    assert 0 <= r["nose_gap_mm"] <= 3.0                                               # a millimetre of room, give or take the grid's cell
    deeper = fit(make_scan(nose=0.02), pair, parts)
    shallower = fit(make_scan(nose=0.005), pair, parts)
    assert deeper["position"][2] > shallower["position"][2] + 0.003                    # a bigger nose holds the pair further forward


def test_a_wide_nose_is_pressed_into_rather_than_hold_the_pair_far_from_the_eyes(pair, parts):
    normal = fit(make_scan(nose=0.02), pair, parts)["report"]
    assert normal["nose_into_mm"] == 0 and not any("nose is wide" in note for note in normal["notes"])
    assert normal["vertex_distance_mm"] < facefit.MAX_REACH * 1000
    wide = fit(make_scan(nose=0.06), pair, parts)["report"]
    assert wide["vertex_distance_mm"] == pytest.approx(facefit.MAX_REACH * 1000, abs=0.6)       # held out no further than that
    assert wide["nose_into_mm"] > 2 and wide["behind_mm"] == 0                                     # the inner rims are in the nose, nothing else is in the face
    assert any("nose is wide" in note and "wider bridge" in note for note in wide["notes"])


def test_cheeks_and_brows_are_never_pressed_into_only_the_nose(pair, parts):
    r = fit(make_scan(nose=0.02, cheek=0.05), pair, parts)["report"]
    assert r["behind_mm"] <= 0.01 and r["vertex_distance_mm"] > facefit.MAX_REACH * 1000       # the cheeks hold the pair out as far as they must


def test_a_flat_nose_still_keeps_the_lenses_off_the_eyes(pair, parts):
    placement = fit(make_scan(nose=0.0), pair, parts)
    r = placement["report"]
    assert r["vertex_distance_mm"] >= facefit.EYE_ROOM * 1000 - 0.2
    assert r["behind_mm"] == 0


def test_a_cheek_bone_that_juts_pushes_the_pair_out_but_a_fringe_only_a_little(pair, parts):
    base = fit(make_scan(nose=0.02), pair, parts)
    cheeks = fit(make_scan(nose=0.02, cheek=0.02), pair, parts)
    assert cheeks["position"][2] > base["position"][2] + 0.003                         # below the middle of the lenses: it counts in full
    fringe = fit(make_scan(nose=0.02, fringe=0.03), pair, parts)
    r = fringe["report"]
    assert r["capped"] is True and any("hair or a brow" in note for note in r["notes"])
    assert fringe["position"][2] - base["position"][2] <= facefit.HAIR_CAP + 0.0005    # the upper rim is held back from a lock of hair


def test_hair_a_brow_or_a_hat_never_hold_the_pair_further_from_the_eyes_than_the_reach(pair, parts):
    near = fit(make_scan(nose=0.0), pair, parts)["report"]                                        # (a flat nose: the pair is 13.7 mm from the eyes)
    assert near["vertex_distance_mm"] < facefit.MAX_REACH * 1000 - 4
    hat = fit(make_scan(nose=0.0, fringe=0.08, temple=0.08), pair, parts)
    assert hat["report"]["capped"] is True
    assert hat["report"]["vertex_distance_mm"] <= near["vertex_distance_mm"] + facefit.HAIR_CAP * 1000 + 0.5          # (a few millimetres further out, not the hat's depth)
    nosed = fit(make_scan(nose=0.02, fringe=0.08, temple=0.08), pair, parts)["report"]            # the nose already holds it at 18.8 mm: hair adds only what is left to the reach
    assert nosed["vertex_distance_mm"] <= facefit.MAX_REACH * 1000 + 0.5


def test_hair_at_the_sides_of_the_face_does_not_float_the_pair(pair, parts):
    base = fit(make_scan(nose=0.02), pair, parts)
    hair = fit(make_scan(nose=0.02, temple=0.04), pair, parts)
    r = hair["report"]
    assert r["capped"] is True and any("outer corners" in note for note in r["notes"])
    assert hair["position"][2] - base["position"][2] <= facefit.HAIR_CAP + 0.0005          # the corners are held back like the upper rim
    assert r["hair_into_mm"] > 5 and r["behind_mm"] == 0                                     # they are in the hair, nothing is inside the face


def test_a_scan_with_no_skin_where_the_pair_goes_falls_back_on_the_eyes(pair, parts):
    data = make_scan(nose=0.0)
    data["grid"]["z"] = make_scan(nose=0.0, hole=(-1, 1, -1, 1))["grid"]["z"]            # all of it cut away
    placement = fit(data, pair, parts)
    assert any("no skin" in note for note in placement["report"]["notes"])
    assert placement["position"][2] > mid_of(data)[2]                                      # still in front of the eyes


# ---- the nudge, applied last, along the pair's own axes ------------------------------------------------------------------------------------------
def test_up_and_forward_move_the_pair_without_changing_how_it_sits(pair, parts):
    scan = make_scan(nose=0.02)
    base = fit(scan, pair, parts)
    moved = fit(scan, pair, parts, up=3, forward=2)
    assert np.array(moved["position"]) - np.array(base["position"]) == pytest.approx([0, 0.003, 0.002], abs=1e-6)
    assert moved["rotation"] == base["rotation"] and moved["scale"] == base["scale"]
    assert moved["report"]["nudge"] == {"up": 3.0, "forward": 2.0, "tilt": 0.0, "size": 1.0}


def test_the_nudge_goes_along_the_pairs_own_axes_when_the_pair_is_turned(pair, parts):
    scan = make_scan(nose=0.02, yaw=8 * DEG, roll=4 * DEG)
    base = fit(scan, pair, parts)
    moved = fit(scan, pair, parts, forward=10)
    axes = facefit.world_matrix(base)[:3, :3] / base["scale"]
    assert np.array(moved["position"]) - np.array(base["position"]) == pytest.approx(axes @ [0, 0, 0.010], abs=1e-6)


def test_tilt_tips_the_top_forward_and_the_pair_is_fitted_again_so_it_stays_out_of_the_face(pair, parts):
    scan = make_scan(nose=0.02)
    tilted = fit(scan, pair, parts, tilt=10)
    axes = facefit.world_matrix(tilted)[:3, :3] / tilted["scale"]
    assert axes @ [0, 1, 0] == pytest.approx([0, np.cos(10 * DEG), np.sin(10 * DEG)], abs=1e-4)      # the pair's up axis leans forward
    assert axes @ [1, 0, 0] == pytest.approx([1, 0, 0], abs=1e-4)                                   # and its line of lenses still follows the eyes
    assert tilted["report"]["behind_mm"] <= 0.01


def test_size_scales_the_pair_and_is_fitted_again(pair, parts):
    scan = make_scan(nose=0.02)
    base, big = fit(scan, pair, parts), fit(scan, pair, parts, size=1.25)
    assert big["scale"] == pytest.approx(base["scale"] * 1.25, abs=1e-4) and big["report"]["scale_auto"] == base["report"]["scale_auto"]
    assert big["report"]["behind_mm"] <= 0.01


def test_a_bad_nudge_is_refused_before_anything_is_done(pair, parts):
    with pytest.raises(ValueError):
        fit(make_scan(), pair, parts, up=500)


# ---- reports and the matrix ---------------------------------------------------------------------------------------------------------------------------
def test_a_scan_made_from_proportions_says_so(pair, parts):
    r = fit(make_scan(source="estimate"), pair, parts)["report"]
    assert r["source"] == "estimate" and "proportions" in r["notes"][0]


def test_a_pair_that_cannot_be_kept_out_of_the_face_says_how_far_in_it_is(pair, parts):
    data = make_scan(nose=0.0)
    placement = fit(data, pair, parts, forward=-15)
    r = placement["report"]
    assert r["behind_mm"] > 2 and any("inside the face" in note for note in r["notes"])


def test_the_matrix_is_the_rotation_times_the_scale_with_the_position(pair, parts):
    scan = make_scan(nose=0.02, yaw=5 * DEG)
    placement = fit(scan, pair, parts)
    m = facefit.world_matrix(placement)
    assert m.shape == (4, 4) and m[3].tolist() == [0, 0, 0, 1]
    assert m[:3, 3] == pytest.approx(placement["position"], abs=1e-6)
    assert np.linalg.norm(m[:3, 0]) == pytest.approx(placement["scale"], abs=1e-5)
    assert glbmerge.quaternion_matrix(placement["rotation"]) * placement["scale"] == pytest.approx(m[:3, :3], abs=1e-5)


def test_a_pair_is_fitted_from_its_glb_or_from_points_already_read(pair, parts):
    scan = facefit.Scan(make_scan(nose=0.02))
    assert facefit.fit(scan, pair)["position"] == facefit.fit(scan, None, parts=parts)["position"]
