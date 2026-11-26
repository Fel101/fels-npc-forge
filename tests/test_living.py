import io
import json

import numpy as np
from PIL import Image, ImageDraw

from npcgen import living, roll
from npcgen.jobs import Jobs
from npcgen.library import Library
from npcgen.service import clean_assets


def _portrait(size=512):
    """A stand-in portrait: a gradient background, a 'face' disc with two eyes and a mouth."""
    y, x = np.mgrid[0:size, 0:size]
    rgb = np.stack([60 + x * 120 // size, 80 + y * 100 // size, np.full_like(x, 140)], axis=2).astype(np.uint8)
    img = Image.fromarray(rgb, "RGB")
    d = ImageDraw.Draw(img)
    d.ellipse((160, 120, 352, 360), fill=(224, 180, 150))
    for ex in (205, 275):
        d.ellipse((ex, 200, ex + 32, 222), fill=(40, 30, 30))
    d.rectangle((225, 300, 287, 310), fill=(150, 60, 60))
    return img


def _expression(base, shift=(0, 0), noise=6, seed=1):
    """The same picture redrawn the way an edit does it: the eyes closed, every pixel a little off, maybe moved."""
    img = base.copy()
    d = ImageDraw.Draw(img)
    for ex in (205, 275):
        d.ellipse((ex, 200, ex + 32, 222), fill=(224, 180, 150))
        d.line((ex, 211, ex + 32, 211), fill=(40, 30, 30), width=3)
    arr = np.asarray(img, dtype=np.int16) + np.random.default_rng(seed).integers(-noise, noise + 1, (*img.size[::-1], 3))
    img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB")
    if shift != (0, 0):
        moved = Image.new("RGB", img.size, (0, 0, 0))
        moved.paste(img, (shift[1], shift[0]))
        img = moved
    return img


def _png(img):
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def test_the_living_set_is_marked_in_the_expression_table():
    ids = living.living_ids(roll.load_data())
    assert {"blink", "talking", "happy", "laughing"} <= set(ids)
    assert "angry" not in ids


def test_a_frame_keeps_everything_but_the_changed_face_pixel_identical():
    base = _portrait()
    out, info = living.composite(base, _expression(base))
    a, b = np.asarray(base.convert("RGB"), np.int16), np.asarray(out.convert("RGB"), np.int16)
    assert np.abs(a[:100] - b[:100]).max() == 0                 # the top of the picture (hair, background) did not move
    assert np.abs(a[:, :100] - b[:, :100]).max() == 0
    assert np.abs(a[205:218, 210:232] - b[205:218, 210:232]).mean() > 60     # the eye really closed
    assert 0 < info["coverage"] < 0.25


def test_a_shifted_expression_is_lined_up_before_it_is_blended():
    base = _portrait()
    assert living.estimate_shift(base, _expression(base, shift=(-6, 10))) == (6, -10)
    out, info = living.composite(base, _expression(base, shift=(-6, 10)))
    assert info["shift"] == [6, -10]
    eye = np.asarray(out.convert("RGB"), np.int16)[209:214, 212:230]
    assert eye.mean() < 150                                       # the closed-eye line landed where the eye is


def _closed_eye(out):
    return np.asarray(out.convert("RGB"), np.int16)[209:214, 212:230].mean() < 150


def test_an_expression_that_came_back_as_the_middle_of_the_portrait_is_put_back_where_it_was():
    # Qwen-Image-Edit 2511 at standard quality: a 1024 portrait comes back as its middle 992 pixels, at the same scale
    base = _portrait()
    middle = _expression(base).crop((8, 8, 504, 504))
    out, info = living.composite(base, middle)
    assert info["shift"] == [0, 0] and _closed_eye(out)
    frame, _ = living.align(base, middle)
    a, b = np.asarray(base.convert("RGB"), np.int16), np.asarray(frame.convert("RGB"), np.int16)
    assert np.abs(a[100:180, 100:400] - b[100:180, 100:400]).mean() < 8      # lined up: only the edit's noise differs


def test_an_expression_that_came_back_as_the_whole_picture_at_another_size_is_scaled_back():
    # Mage-Flow: the whole portrait, drawn at the edit's own size
    base = _portrait()
    smaller = _expression(base).resize((480, 480), Image.LANCZOS)
    out, info = living.composite(base, smaller)
    assert info["shift"] == [0, 0] and _closed_eye(out)


def test_an_unchanged_frame_is_the_portrait():
    base = _portrait()
    out, info = living.composite(base, base.copy())
    assert info["coverage"] == 0
    assert np.array_equal(np.asarray(out.convert("RGB")), np.asarray(base.convert("RGB")))


def test_assemble_writes_the_frames_and_a_manifest_tied_to_the_portrait(tmp_path):
    lib = Library(tmp_path / "library")
    npc = lib.create({"setting": "modern", "style": "photoreal", "seed": 3,
                      "profile": {"first_name": "Ada", "last_name": "Vale", "age": 30, "gender": "female", "occupation": "tester"},
                      "appearance": {}, "assets": {
                          "portrait": {"file": "portrait.png", "created": "p1"},
                          "expressions": {"blink": {"file": "expressions/blink.png", "created": "e1"},
                                          "happy": {"file": "expressions/happy.png", "created": "e2", "stale": True}}}})
    base = _portrait(256)
    lib.write_asset(npc["id"], "portrait.png", _png(base))
    lib.write_asset(npc["id"], "expressions/blink.png", _png(_expression(base)))
    lib.write_asset(npc["id"], "expressions/happy.png", _png(base))
    manifest = living.assemble(lib, npc["id"], roll.load_data())
    assert set(manifest["frames"]) == {"blink"}                   # a stale expression is not used
    assert "happy" in manifest["missing"]
    on_disk = json.loads(lib.read_asset(npc["id"], "living/living.json"))
    assert on_disk["portrait"]["created"] == "p1" and len(on_disk["portrait"]["sha256"]) == 64
    assert lib.read_asset(npc["id"], "living/blink.png")
    assert lib.get(npc["id"])["assets"]["living"]["frames"] == ["blink"]


def test_a_living_request_plans_only_the_missing_expressions_then_the_assembly():
    data = roll.load_data()
    assets = clean_assets({"living": True}, data)
    npc = {"assets": {"portrait": {"file": "portrait.png"}, "expressions": {"blink": {"file": "x"}, "happy": {"file": "y", "stale": True}}}}
    steps = Jobs.plan(npc, assets)
    kinds = [(s["kind"], s["key"]) for s in steps]
    assert ("expression", "blink") not in kinds
    assert ("expression", "happy") in kinds
    assert kinds[-1] == ("living", None)
    assert kinds[0] != ("portrait", None)
    no_portrait = Jobs.plan({"assets": {}}, assets)
    assert no_portrait[0]["kind"] == "portrait"


def test_a_new_portrait_makes_the_living_portrait_outdated(tmp_path):
    lib = Library(tmp_path / "library")
    npc = lib.create({"setting": "modern", "style": "photoreal", "seed": 3,
                      "profile": {"first_name": "Ada", "last_name": "Vale", "age": 30, "gender": "female", "occupation": "tester"},
                      "appearance": {}, "assets": {"living": {"file": "living/living.json", "frames": ["blink"]}}})
    jobs = Jobs.__new__(Jobs)
    jobs.lib = lib
    jobs._mark_derived_stale(npc["id"])
    assert lib.get(npc["id"])["assets"]["living"]["stale"] is True


def test_a_redrawn_dress_that_changes_more_than_the_face_does_not_pull_the_frame_off_the_face():
    """Measured on a character in satin: the edit redrew the dress more than it closed her eyes, and the first version of the
    mask followed the dress. The face is found first now, so the dress stays the portrait's."""
    base = _portrait()
    frame = _expression(base)
    d = ImageDraw.Draw(frame)
    for y in range(400, 512, 8):                                  # the "dress": a big, strong redraw below the face
        d.rectangle((40, y, 470, y + 4), fill=(255, 255, 255) if (y // 8) % 2 else (0, 0, 0))
    out, _ = living.composite(base, frame)
    a, b = np.asarray(base.convert("RGB"), np.int16), np.asarray(out.convert("RGB"), np.int16)
    assert np.abs(a[420:] - b[420:]).mean() < 2                   # the dress is the portrait's
    assert np.abs(a[205:218, 210:232] - b[205:218, 210:232]).mean() > 60     # the eyes still closed


def test_a_changed_feature_is_replaced_whole_not_half_blended():
    """Measured: a wide-smile portrait under a closed-lips expression kept a faint row of teeth where the mask was only
    partly on. Inside the change the frame must be taken fully."""
    base = _portrait()
    ImageDraw.Draw(base).rectangle((222, 296, 290, 314), fill=(250, 250, 250))       # "teeth" in the portrait
    frame = base.copy()
    ImageDraw.Draw(frame).rectangle((222, 296, 290, 314), fill=(170, 80, 80))        # closed lips in the expression
    out, _ = living.composite(base, frame)
    lips = np.asarray(out.convert("RGB"), np.int16)[300:311, 230:283]
    assert np.abs(lips - np.array([170, 80, 80])).max() <= 3


def test_a_colour_cast_the_edit_added_is_taken_out():
    """A painterly edit came back a shade more orange all over: blinking with it would flash. The frame's colours are
    matched to the portrait around the change."""
    base = _portrait()
    arr = np.asarray(_expression(base, noise=0), np.int16) + np.array([18, 6, -10])
    out, _ = living.composite(base, Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8)))
    lid = np.asarray(out.convert("RGB"), np.int16)[202:207, 212:230]   # the closed eyelid: taken from the frame
    assert np.abs(lid.mean(axis=(0, 1)) - np.array([224, 180, 150])).max() < 5      # in the portrait's skin tone, not the cast


def _talking(base):
    """The mouth opened: a dark cavity where the lips were."""
    img = base.copy()
    ImageDraw.Draw(img).ellipse((230, 294, 282, 322), fill=(40, 15, 20))
    return img


def test_the_eyes_are_found_from_the_blink_and_the_mouth_below_them():
    base = _portrait()
    centre = living.face_centre(base, [_expression(base, noise=0)])
    eyes = living.find_eyes(base, _expression(base, noise=0), centre)
    assert len(eyes) == 2                                          # both eyes, not a wink
    for (y, x), ex in zip(eyes, (205, 275)):
        assert abs(y * 2 - 211) <= 8 and ex - 6 <= x * 2 <= ex + 38
    y, x = living.find_mouth(base, _talking(base), centre, eyes)
    assert abs(y * 2 - 308) <= 12 and abs(x * 2 - 256) <= 20


def test_a_blink_and_a_word_take_only_the_eyes_and_the_mouth(tmp_path):
    """Measured on a painterly character: the edit re-painted her whole face, and a blink that took all of it flickered.
    Here the blink and talking frames also 're-paint' a cheek and darken a patch of hair by the far eye (the thing a turned
    head did): neither may come through."""
    lib = Library(tmp_path / "library")
    npc = lib.create({"setting": "modern", "style": "semi_real", "seed": 3,
                      "profile": {"first_name": "Ada", "last_name": "Vale", "age": 30, "gender": "female", "occupation": "tester"},
                      "appearance": {}, "assets": {"portrait": {"file": "portrait.png", "created": "p1"}, "expressions": {
                          "blink": {"file": "expressions/blink.png", "created": "e1"},
                          "talking": {"file": "expressions/talking.png", "created": "e2"}}}})
    base = _portrait()

    def repainted(img):
        d = ImageDraw.Draw(img)
        d.ellipse((180, 240, 215, 275), fill=(200, 120, 90))       # a re-painted cheek
        d.rectangle((330, 180, 350, 215), fill=(10, 10, 10))       # a darkened strand beside the far eye
        return img
    lib.write_asset(npc["id"], "portrait.png", _png(base))
    lib.write_asset(npc["id"], "expressions/blink.png", _png(repainted(_expression(base, noise=0))))
    lib.write_asset(npc["id"], "expressions/talking.png", _png(repainted(_talking(base))))
    manifest = living.assemble(lib, npc["id"], roll.load_data())
    assert len(manifest["eyes"]) == 2 and manifest["mouth"]
    a = np.asarray(base.convert("RGB"), np.int16)
    for frame in ("blink", "talking"):
        b = np.asarray(Image.open(io.BytesIO(lib.read_asset(npc["id"], f"living/{frame}.png"))).convert("RGB"), np.int16)
        assert np.abs(a[245:270, 185:210] - b[245:270, 185:210]).mean() < 3, frame      # the cheek is the portrait's
        assert np.abs(a[182:213, 332:348] - b[182:213, 332:348]).mean() < 3, frame      # so is the hair
    blink = np.asarray(Image.open(io.BytesIO(lib.read_asset(npc["id"], "living/blink.png"))).convert("RGB"), np.int16)
    talk = np.asarray(Image.open(io.BytesIO(lib.read_asset(npc["id"], "living/talking.png"))).convert("RGB"), np.int16)
    assert np.abs(a[205:218, 210:232] - blink[205:218, 210:232]).mean() > 60        # the eyes closed
    assert np.abs(a[298:318, 240:272] - talk[298:318, 240:272]).mean() > 60         # the mouth opened
