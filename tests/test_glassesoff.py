"""Taking the glasses off the pictures a 3D model is made from (npcgen/glassesoff.py)."""
import io

import numpy as np
import pytest
from PIL import Image

from npcgen import engines, glassesoff, three_d
from npcgen.comfy import ComfyError

SKIN = (206, 160, 140)
VIEWS = three_d.PIXAL_SHEET_VIEWS


def _png(array):
    out = io.BytesIO()
    Image.fromarray(array.astype(np.uint8)).save(out, "PNG")
    return out.getvalue()


def _ring(size, centre, radius, width=2):
    yy, xx = np.mgrid[:size, :size]
    distance = np.hypot(yy - centre[0], xx - centre[1])
    return np.abs(distance - radius) <= width / 2


def _crop_with_glasses(size=160):
    """A skin-coloured square with a dark lens rim, and a pale second arc 13 pixels outside it (what an edit leaves of a rim)."""
    picture = np.zeros((size, size, 3), float) + SKIN
    picture[_ring(size, (80, 80), 24)] = (60, 60, 60)
    picture[_ring(size, (80, 80), 37)] = np.array(SKIN) - 9
    return picture


def test_the_glasses_and_the_faint_rim_round_them_go_and_the_rest_of_the_picture_is_untouched():
    original = _crop_with_glasses()
    far = original.copy()
    far[3:6, 3:6] = np.array(SKIN) - 10                                # a mark far from the glasses: not the edit's business
    edited = np.zeros_like(far) + SKIN
    picture, redrawn, replaced = glassesoff.blend(Image.fromarray(far.astype(np.uint8)), Image.fromarray(edited.astype(np.uint8)))
    got = np.asarray(picture, int)
    assert (np.abs(got[_ring(160, (80, 80), 24)] - SKIN).max(1) <= 1).all()          # the rim is gone
    assert (np.abs(got[_ring(160, (80, 80), 37)] - SKIN).max(1) <= 1).all()          # and the faint one outside it
    assert (got[3:6, 3:6] == np.asarray(far[3:6, 3:6], int)).all()                  # the far mark stays as it was
    assert 0 < redrawn < replaced < 1


def test_a_plain_threshold_would_have_left_the_faint_rim():
    original = _crop_with_glasses()
    delta = np.abs(original - np.array(SKIN)).max(-1)
    faint = _ring(160, (80, 80), 37)
    assert delta[faint].max() <= glassesoff.THRESHOLD < delta[_ring(160, (80, 80), 24)].min()    # the case the wider area is for


def _figure(width=576, height=1152, head_x=300):
    """A white picture with a dark figure: a head (an ellipse) on a body, the head's centre at head_x."""
    picture = np.full((height, width, 3), 255, np.uint8)
    yy, xx = np.mgrid[:height, :width]
    picture[((yy - 130) / 90.0) ** 2 + ((xx - head_x) / 60.0) ** 2 <= 1] = (120, 70, 50)
    picture[(yy > 200) & (yy < 1100) & (np.abs(xx - 288) < 110)] = (40, 40, 60)
    return picture


def test_the_head_box_is_a_square_round_the_top_of_the_figure():
    x0, y0, x1, y1 = glassesoff.head_box(Image.fromarray(_figure(head_x=300)))
    assert x1 - x0 == y1 - y0 and abs((x0 + x1) / 2 - 300) <= 2 and y0 <= 40 + 2     # the head's top is at about y=40
    with pytest.raises(glassesoff.StripError, match="no figure"):
        glassesoff.head_box(Image.fromarray(np.full((200, 100, 3), 255, np.uint8)))


class FakeComfy:
    def __init__(self):
        self.uploads = []

    def upload_image(self, png, name):
        self.uploads.append((name, Image.open(io.BytesIO(png)).size))
        return f"{name} [temp]"


def _erase_glasses(monkeypatch, erase=True):
    """engines.edit as an edit model that paints the dark rims in the head crop out (or hands it back as it was)."""
    calls = []

    def edit(comfy, engine_id, refs, prompt, seed, steps, cfg, megapixels=1.0, size=None, negative="", like=None, cancel=None):
        calls.append({"engine": engine_id, "refs": refs, "prompt": prompt, "negative": negative, "size": size, "seed": seed})
        name = refs[0]
        upload = next(sz for n, sz in comfy.uploads if name.startswith(n))
        picture = comfy.last.copy()
        if erase:
            picture[(picture.max(-1) < 90)] = SKIN
        return _png(np.asarray(Image.fromarray(picture.astype(np.uint8)).resize(upload, Image.LANCZOS))), {"engine": "Fake edit model"}

    monkeypatch.setattr(engines, "edit", edit)
    return calls


def _figure_with_glasses(width=576, height=1152):
    picture = _figure(width, height).astype(float)
    ring = _ring(max(width, height), (130, 300), 40)[:height, :width]
    picture[ring] = (50, 50, 50)
    return picture


def test_one_picture_has_only_its_head_redrawn_at_three_times_the_size(monkeypatch):
    comfy = FakeComfy()
    picture = _figure_with_glasses()
    calls = _erase_glasses(monkeypatch)
    original_crop = glassesoff.head_box(Image.fromarray(picture.astype(np.uint8)))
    side = original_crop[2] - original_crop[0]
    big = np.asarray(Image.fromarray(picture.astype(np.uint8)).crop(original_crop).resize((side * 3, side * 3), Image.LANCZOS), float)
    comfy.last = big
    png, info = glassesoff.strip_picture(comfy, _png(picture), "x_noglasses_front.png", 7, 4, 3.5)
    assert comfy.uploads == [("x_noglasses_front.png", (side * 3, side * 3))]                  # the crop was tripled
    assert calls[0]["engine"] == glassesoff.ENGINE == "mage" and "Take the glasses off" in calls[0]["prompt"] and "glasses" in calls[0]["negative"]
    glassesoff.strip_picture(comfy, _png(picture), "y.png", 7, 4, 3.5, engine="qwen2511")
    assert calls[1]["engine"] == "qwen2511"                                                    # the job's own Edit model, when it names one
    got = np.asarray(Image.open(io.BytesIO(png)).convert("RGB"), int)
    x0, y0, x1, y1 = original_crop
    outside = np.ones(got.shape[:2], bool)
    outside[max(0, y0 - 2):y1 + 2, max(0, x0 - 2):x1 + 2] = False
    assert (got[outside] == np.asarray(picture, int)[outside]).all()                           # nothing outside the head crop changed
    assert info["redrawn"] > glassesoff.MIN_REDRAWN and info["engine"] == "Fake edit model"


def test_a_model_that_leaves_the_glasses_on_stops_the_step(monkeypatch):
    comfy = FakeComfy()
    picture = _figure_with_glasses()
    _erase_glasses(monkeypatch, erase=False)
    box = glassesoff.head_box(Image.fromarray(picture.astype(np.uint8)))
    side = box[2] - box[0]
    comfy.last = np.asarray(Image.fromarray(picture.astype(np.uint8)).crop(box).resize((side * 3, side * 3), Image.LANCZOS), float)
    with pytest.raises(ComfyError, match="left the glasses on the front picture"):
        glassesoff.strip(comfy, {v: _png(picture) for v in VIEWS}, "x", 7, 4, 3.5)


def test_all_four_pictures_are_given_back_and_the_engine_is_named(monkeypatch):
    comfy = FakeComfy()
    picture = _figure_with_glasses()
    _erase_glasses(monkeypatch)
    box = glassesoff.head_box(Image.fromarray(picture.astype(np.uint8)))
    side = box[2] - box[0]
    comfy.last = np.asarray(Image.fromarray(picture.astype(np.uint8)).crop(box).resize((side * 3, side * 3), Image.LANCZOS), float)
    out, report = glassesoff.strip(comfy, {v: _png(picture) for v in VIEWS}, "x", 7, 4, 3.5)
    assert list(out) == list(VIEWS) and report["engine"] == "Fake edit model" and list(report["views"]) == list(VIEWS)
    assert [name for name, _size in comfy.uploads] == [f"x_noglasses_{v}.png" for v in VIEWS]


def test_the_copies_are_what_a_model_made_from_them_is_painted_from():
    sheet = {v: {"file": f"sheet/{v}.png"} for v in VIEWS}
    files = {"sheet/front.png": b"sheet-front", "sheet/side_left.png": b"sheet-left", "sheet/back.png": b"sheet-back",
             "sheet/side_right.png": b"sheet-right", "sheet/no_glasses/front.png": b"bare-front", "sheet/no_glasses/side_left.png": b"bare-left"}

    def read(path):
        try:
            return files[path]
        except KeyError:
            raise FileNotFoundError(path) from None

    plain = glassesoff.pictures_for(read, {"file": "model_3d.glb"}, sheet)
    assert plain == {"front": b"sheet-front", "side_left": b"sheet-left", "back": b"sheet-back", "side_right": b"sheet-right"}
    made = {"glasses_off": {"pictures": {v: f"sheet/no_glasses/{v}.png" for v in VIEWS}}}
    mixed = glassesoff.pictures_for(read, made, sheet)
    assert mixed["front"] == b"bare-front" and mixed["side_left"] == b"bare-left"                 # a copy that is gone: the sheet's own picture
    assert mixed["back"] == b"sheet-back" and mixed["side_right"] == b"sheet-right"
