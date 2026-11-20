"""Softening the eyes in the model-sheet pictures for a shape-only generator (npcgen/eyesoft.py): the fill, the eye ellipses, and the
SDPose round trip with the detector stood in for."""
import io

import numpy as np
import pytest
from PIL import Image

from npcgen import eyesoft, posekeys
from npcgen.comfy import Cancelled

SKIN = (200, 160, 140)
EYE_W = 16.0
EYE_Y = 150.0
EYE_X = {"r": 70.0, "l": 130.0}


def points(score=0.9, scores=None):
    """68 face points with an eye at EYE_X on the character's right (36-41) and left (42-47), brows above them."""
    pts = np.zeros((68, 3))
    for side, first in (("r", 36), ("l", 42)):
        cx = EYE_X[side]
        shape = [(-0.5, 0), (-0.25, -0.2), (0.25, -0.2), (0.5, 0), (0.25, 0.2), (-0.25, 0.2)]
        for i, (dx, dy) in enumerate(shape):
            pts[first + i] = (cx + dx * EYE_W, EYE_Y + dy * EYE_W, (scores or {}).get(side, score))
    for first, side in ((17, "r"), (22, "l")):
        for i in range(5):
            pts[first + i] = (EYE_X[side] + (i - 2) * 7, EYE_Y - 0.8 * EYE_W, (scores or {}).get(side, score))
    return pts.tolist()


def picture(brow=False):
    """A white 200 x 300 picture with a skin-coloured head and a dark spot where each eye is (and a dark brow line, if asked)."""
    a = np.full((300, 200, 3), 255, np.uint8)
    a[60:260, 30:170] = SKIN
    for cx in EYE_X.values():
        a[int(EYE_Y) - 5:int(EYE_Y) + 5, int(cx) - 8:int(cx) + 8] = (30, 20, 20)
    if brow:
        a[int(EYE_Y - 0.8 * EYE_W) - 1:int(EYE_Y - 0.8 * EYE_W) + 2, 55:145] = (40, 30, 20)
    return a


def png(a):
    out = io.BytesIO()
    Image.fromarray(a).save(out, "PNG")
    return out.getvalue()


# ---- the fill and the ellipses -----------------------------------------------------------------------------------------------------

def test_the_fill_takes_its_colour_from_the_skin_and_never_from_the_white_background():
    a = picture()
    fg = eyesoft.foreground(a)
    mask = np.zeros(a.shape[:2], bool)
    mask[60:75, 30:60] = True                       # a hole at the head's top left corner: white background is next to it
    filled = eyesoft.fill(a, mask, fg)
    assert np.abs(filled[60:75, 35:60] - SKIN).max() < 12   # the edge pixels next to the white outline lean a little, no more
    assert filled[100:120, 100:120].tolist() == a[100:120, 100:120].tolist()    # nothing outside the hole moves
    assert eyesoft.fill(a, np.zeros(a.shape[:2], bool), fg).tolist() == a.tolist()


def test_both_eyes_are_filled_with_skin_and_nothing_else_changes():
    a = picture()
    soft, sides = eyesoft.soften_picture(Image.fromarray(a), points(), EYE_W)
    assert sides == ["l", "r"]
    for cx in EYE_X.values():
        assert np.abs(soft[int(EYE_Y) - 4:int(EYE_Y) + 4, int(cx) - 7:int(cx) + 7].astype(int) - SKIN).max() <= 3
    far = np.ones(a.shape[:2], bool)
    far[int(EYE_Y) - 20:int(EYE_Y) + 20, 40:160] = False                   # outside the eyes' neighbourhood
    assert (soft[far] == a[far]).all()
    assert (soft[:50] == 255).all()                                          # the white background is not touched


def test_an_eye_whose_points_are_uncertain_is_left_alone():
    a = picture()
    soft, sides = eyesoft.soften_picture(Image.fromarray(a), points(scores={"l": 0.1}), EYE_W)
    assert sides == ["r"]
    left = (slice(int(EYE_Y) - 4, int(EYE_Y) + 4), slice(int(EYE_X["l"]) - 7, int(EYE_X["l"]) + 7))
    assert (soft[left] == a[left]).all()


def test_the_eyebrow_stays_and_the_picture_has_nothing_to_soften_without_eyes():
    a = picture(brow=True)
    soft, sides = eyesoft.soften_picture(Image.fromarray(a), points(), EYE_W)
    brow_row = int(EYE_Y - 0.8 * EYE_W)
    assert (soft[brow_row, 70] == (40, 30, 20)).all()
    unchanged, none = eyesoft.soften_picture(Image.fromarray(a), points(score=0.0), EYE_W)
    assert none == [] and (unchanged == a).all()


def test_the_eye_width_is_the_mean_corner_to_corner_distance():
    assert eyesoft.eye_width(points()) == pytest.approx(EYE_W)


# ---- the SDPose round trip -------------------------------------------------------------------------------------------------------

class FakeSDPose:
    """posekeys.detect with a face in every picture; the close-up (key "face") reads the front face in its own crop."""

    def __init__(self, faces=None, error=None):
        self.faces, self.error, self.calls = faces or {}, error, []

    def __call__(self, comfy, pictures, npc_id, cancel=None):
        self.calls.append((list(pictures), npc_id))
        if self.error:
            raise self.error
        out = {}
        for key in pictures:
            pts = self.faces.get(key, points())
            if key == "face":                                  # relative to the crop the code asks for (box 10, 20)
                pts = [[u - 10, v - 20, s] for u, v, s in self.faces.get("front", points())]
            out[key] = {"face_keypoints_2d": pts}
        return out


@pytest.fixture
def sdpose(monkeypatch):
    fake = FakeSDPose()
    monkeypatch.setattr(eyesoft.posekeys, "detect", fake)
    monkeypatch.setattr(eyesoft.posekeys, "face_box", lambda pts, size, pad=2.2: (10.0, 20.0, 150.0, 200.0))
    return fake


def sheet():
    return {view: png(picture()) for view in ("front", "side_left", "back", "side_right")}


def test_every_picture_comes_back_and_the_report_says_where_the_eyes_were_softened(sdpose):
    pictures = sheet()
    out, report = eyesoft.soften(object(), pictures, "devi-1", cancel=None)
    assert set(out) == set(pictures)
    assert out["back"] == pictures["back"]                        # the back picture has no face: untouched, byte for byte
    for view in ("front", "side_left", "side_right"):
        assert out[view] != pictures[view]
        assert report["views"][view] == ["l", "r"]
    assert report["eye_width_px"] == pytest.approx(EYE_W, abs=0.1)
    assert sdpose.calls[0][0] == ["front", "side_left", "side_right"] and sdpose.calls[1][0] == ["face"]    # the close-up reads the front again
    assert all(npc_id == "devi-1_eyes" for _, npc_id in sdpose.calls)


def test_a_front_picture_with_no_sure_eyes_stops_the_job_with_a_message(monkeypatch, sdpose):
    sdpose.faces = {"front": points(score=0.1)}
    with pytest.raises(eyesoft.SoftenError, match="Untick 'Soften the eyes'"):
        eyesoft.soften(object(), sheet(), "devi-1")


def test_sdpose_finding_no_person_is_a_readable_error(monkeypatch):
    monkeypatch.setattr(eyesoft.posekeys, "detect", FakeSDPose(error=posekeys.PoseError("SDPose found no person in a render of the model")))
    with pytest.raises(eyesoft.SoftenError, match="needs SDPose to find the face.*no person"):
        eyesoft.soften(object(), sheet(), "devi-1")


def test_a_face_with_too_few_points_is_not_taken_for_one(monkeypatch):
    monkeypatch.setattr(eyesoft.posekeys, "detect", FakeSDPose(faces={"front": []}))
    monkeypatch.setattr(eyesoft.posekeys, "face_box", lambda pts, size, pad=2.2: None)
    with pytest.raises(eyesoft.SoftenError, match="no face in the front"):
        eyesoft.soften(object(), sheet(), "devi-1")


def test_cancelling_during_the_search_cancels_the_job(monkeypatch):
    monkeypatch.setattr(eyesoft.posekeys, "detect", FakeSDPose(error=Cancelled("cancelled")))
    with pytest.raises(Cancelled):
        eyesoft.soften(object(), sheet(), "devi-1")


def test_a_picture_with_transparency_is_flattened_on_white_before_it_is_read():
    rgba = np.zeros((20, 20, 4), np.uint8)
    rgba[5:15, 5:15] = (200, 160, 140, 255)
    flat = np.asarray(eyesoft._rgb(png(rgba)))
    assert flat[0, 0].tolist() == [255, 255, 255] and flat[10, 10].tolist() == [200, 160, 140]


# ---- characters whose eyes are left alone ---------------------------------------------------------------------------------------------

def test_a_character_with_glasses_is_left_alone_because_the_frames_would_come_out_as_solid_lenses():
    reason = eyesoft.skip_reason({"appearance": {"glasses": "thin round glasses"}})
    assert "wears glasses (thin round glasses)" in reason and "solid lenses" in reason
    assert eyesoft.skip_reason({"appearance": {"glasses": "", "extra": "mirrored sunglasses pushed up into the hair"}}) is not None
    assert eyesoft.skip_reason({"appearance": {"glasses": "", "accessory": "reading spectacles on a chain"}}) is not None


def test_a_character_without_glasses_is_not_skipped():
    assert eyesoft.skip_reason({"appearance": {"glasses": "", "accessory": "a silver locket", "extra": "", "distinguishing": "a scar"}}) is None
    assert eyesoft.skip_reason({"appearance": {"glasses": "none"}}) is None
    assert eyesoft.skip_reason({"appearance": {"extra": "a pair of glassblowing gloves"}}) is None        # a word that only contains "glass"
    assert eyesoft.skip_reason({}) is None


def test_what_a_character_wears_on_the_face_is_read_from_the_sheet():
    assert eyesoft.glasses_of({"appearance": {"glasses": "  cat-eye glasses "}}) == "cat-eye glasses"
    assert eyesoft.glasses_of({"appearance": {"glasses": "", "accessory": "aviator sunglasses tucked into a pocket"}}) == "aviator sunglasses tucked into a pocket"
    assert eyesoft.glasses_of({"appearance": {"glasses": "none"}}) is None
