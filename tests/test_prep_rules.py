"""The shape rules of the game-model step (npcgen/blender/prep_rules.py). Plain Python: no Blender is needed."""
import pytest

from npcgen.blender import prep_rules as rules


def test_drift_is_how_far_each_axis_moved_as_a_fraction_of_its_size():
    assert rules.drift((1.0, 2.0, 4.0), (0.5, 2.0, 4.1)) == {"x": 0.5, "y": 0.0, "z": 0.025}
    assert rules.worst_drift({"x": 0.5, "y": 0.0, "z": 0.025}) == 0.5


def test_a_flat_body_has_no_drift_to_measure():
    with pytest.raises(ValueError):
        rules.drift((0.0, 1.0, 1.0), (0.0, 1.0, 1.0))


def test_the_drift_limit_is_four_percent():
    assert rules.DRIFT_LIMIT == 0.04


def test_the_drift_numbers_measured_on_the_reedcoil_and_the_spitter_fall_on_either_side_of_the_limit():
    # Measured in Blender on 2026-10-16 (numbers typed in, not re-run here): the Reedcoil after one collapse to the budget (its width
    # fell by 56%), and the Spitter's collapse (its height grew by 2%). The reduction itself is tested in test_prep_blender.py.
    eel = rules.drift((1.385, 2.503, 1.385), (0.604, 1.968, 1.407))
    assert rules.worst_drift(eel) > rules.DRIFT_LIMIT
    spitter = rules.drift((0.749, 2.000, 0.749), (0.750, 1.995, 0.764))
    assert rules.worst_drift(spitter) <= rules.DRIFT_LIMIT


def test_elongation_is_the_length_over_the_larger_cross_size():
    assert rules.elongation(1.002, 0.436, 0.375) == pytest.approx(2.297, abs=0.01)     # the Spitter's picture: a serpent
    assert rules.elongation(0.995, 0.986, 0.551) < 2.2                                  # the Reedcoil's picture: a coil
    with pytest.raises(ValueError):
        rules.elongation(1.0, 0.0, 0.0)


def test_the_left_facing_head_is_at_raw_minus_z_and_right_is_the_mirror():
    assert rules.FACING_HEAD["left"] == (0.0, 0.0, -1.0)
    assert rules.FACING_HEAD["right"] == (0.0, 0.0, 1.0)


MASS_ON_MINUS_Y_SANDCRAB = -0.046      # the Sand-crab's raw model: its mass sits this far on -Y of its centre, so its back is on -Y (2026-10-17)


def test_the_raw_up_is_the_side_the_mass_sits_on():
    assert rules.raw_up(MASS_ON_MINUS_Y_SANDCRAB) == (0.0, -1.0, 0.0)
    assert rules.raw_up(0.02) == (0.0, 1.0, 0.0)


@pytest.mark.parametrize("facing", ["left", "right"])
def test_the_static_rotation_puts_the_head_at_minus_y_and_the_back_up(facing):
    # The bug this guards: the height was taken as +Y whatever the body was, so the Sand-crab and the Road-hound came out upside down.
    up = rules.raw_up(MASS_ON_MINUS_Y_SANDCRAB)
    rows = rules.static_rotation(facing, up)
    assert rules.apply(rows, rules.FACING_HEAD[facing]) == pytest.approx((0.0, -1.0, 0.0))
    assert rules.apply(rows, up) == pytest.approx((0.0, 0.0, 1.0))
    assert rules.determinant(rows) == pytest.approx(1.0)                                # a turn, not a mirror
    for i in range(3):                                                                  # orthonormal: R R^T = I
        for j in range(3):
            dot = sum(rows[i][k] * rows[j][k] for k in range(3))
            assert dot == pytest.approx(1.0 if i == j else 0.0)


def test_the_static_rotation_lays_a_hound_down_length_first():
    # The Road-hound's raw model: length 1.00 along Z, height 0.715 along Y, its back on -Y (2026-10-17). Laid down, its length is on Y.
    up = rules.raw_up(-0.034)
    rows = rules.static_rotation("left", up)
    length = rules.apply(rows, (0.0, 0.0, 1.0))
    assert abs(length[1]) == pytest.approx(1.0) and length[2] == pytest.approx(0.0)
    assert rules.apply(rows, up) == pytest.approx((0.0, 0.0, 1.0))


def test_a_facing_the_maker_has_not_measured_is_refused():
    with pytest.raises(ValueError):
        rules.static_rotation("front", (0.0, -1.0, 0.0))


def test_a_reduction_over_the_budget_is_refused_as_a_budget_failure_not_a_shape_one():
    message = rules.refusal(2000, 2100, {"x": 0.01, "y": 0.0, "z": 0.0})
    assert "could not reduce the model to 2000 triangles (it has 2100)" in message
    assert "keep its shape" not in message                                  # the shape was within the limit: say so by not blaming it


def test_a_reduction_that_moves_an_axis_too_far_names_that_axis():
    message = rules.refusal(2000, 2000, {"x": 0.05, "y": 0.01, "z": 0.0})
    assert "the x axis changed by 5% (the limit is 4%)" in message


def test_a_reduction_that_keeps_budget_and_shape_has_no_refusal():
    assert rules.refusal(20000, 19400, {"x": 0.007, "y": 0.004, "z": 0.009}) is None
