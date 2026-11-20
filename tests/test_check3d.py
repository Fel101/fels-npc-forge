"""The optional before/after check of a cleaned-up model by a vision model in LM Studio (npcgen/check3d.py)."""
import io
import json

import pytest
from PIL import Image

from npcgen import check3d, eyes, service, roll
from npcgen.jobs import ComfyError, Jobs
from test_cleanup3d import world  # noqa: F401  (a fixture)


def answer(better, a_score=3, b_score=8, a=("doubled face",), b=("specks in the hair",)):
    return "<think>looking</think>" + json.dumps({"better": better, "A": {"score": a_score, "problems": list(a)},
                                                  "B": {"score": b_score, "problems": list(b)}})


def png(colour, size=(200, 400)):
    buf = io.BytesIO()
    image = Image.new("RGB", size, (255, 255, 255))
    image.paste(colour, (60, 40, 140, 380))
    image.save(buf, "PNG")
    return buf.getvalue()


def test_an_answer_is_read_through_the_thinking_and_kept_in_range():
    got = check3d.parse(answer("b", a_score=0, b_score=14))
    assert got["better"] == "B" and got["A"]["score"] == 1 and got["B"]["score"] == 10
    assert check3d.parse('{"better": "neither", "A": {"score": 5}, "B": {"score": 5}}')["better"] == "same"
    assert check3d.parse("I think B looks better") is None


def test_a_verdict_stands_only_when_both_orders_agree():
    # first board: the original is A; second: the original is B
    both = check3d.verdict(check3d.parse(answer("B")), check3d.parse(answer("A", a_score=9, b_score=2,
                                                                              a=("spcks in hair",), b=("a doubled face",))))
    assert both["verdict"] == "after" and both["agreed"]
    assert both["before"]["score"] == 2.5 and both["after"]["score"] == 8.5
    assert both["before"]["problems"] == ["doubled face"]            # the same fault worded twice is listed once
    # the model picked whichever stood first both times: no clear difference
    first = check3d.verdict(check3d.parse(answer("A")), check3d.parse(answer("A")))
    assert first["verdict"] == "unclear" and not first["agreed"]
    one = check3d.verdict(None, check3d.parse(answer("A")))
    assert one["verdict"] == "unclear" and one["after"]["problems"] == ["doubled face"]
    with pytest.raises(check3d.CheckError):
        check3d.verdict(None, None)


def test_the_check_asks_twice_with_the_models_swapped_and_shows_one_board(monkeypatch):
    shots = {"body": png((200, 0, 0)), "face": png((200, 0, 0), (100, 100))}
    monkeypatch.setattr(check3d, "render", lambda cfg, models, cancel=None: {name: shots for name in models})
    asked = []

    def converse(cfg, questions, comfy=None, cancel=None, **kw):
        asked.append((len(questions), kw["choices"]))
        return [answer("B"), answer("A")], "qwen/qwen3.8-27b"
    monkeypatch.setattr(eyes, "converse", converse)
    result, board = check3d.check({}, b"before", b"after", png((90, 60, 40)))
    assert asked == [(2, check3d.MODELS)]
    assert result["verdict"] == "after" and result["model"] == "qwen/qwen3.8-27b"
    assert Image.open(io.BytesIO(board)).size == (10 + 370 * 3, 1060)


def test_the_check_is_its_own_step_after_the_clean_up_and_only_when_asked():
    npc = {"assets": {"sprite": {"file": "sprite.png", "created": "t"}, "model_3d": {"file": "model_3d.glb", "textured": True}}}
    kinds = [s["kind"] for s in Jobs.plan(npc, {"refine_3d": True, "check_3d": True, "rig_3d": True}, {})]
    assert kinds == ["refine_3d", "check_3d", "rig_3d"]
    assert "check_3d" not in [s["kind"] for s in Jobs.plan(npc, {"refine_3d": True}, {})]
    assert service.clean_assets({"check_3d": True}, roll.load_data())["check_3d"] is True


def test_a_model_not_cleaned_up_is_not_checked(world):  # noqa: F811
    _lib, _npc_id, run = world
    with pytest.raises(ComfyError, match="clean the model up first"):
        run("check_3d")


def test_the_result_is_kept_with_the_clean_up(world, monkeypatch):  # noqa: F811
    lib, npc_id, run = world
    lib.write_asset(npc_id, "model_3d_original.glb", b"original")
    lib.update(npc_id, lambda n: n["assets"]["model_3d"].update(raw="model_3d_original.glb", refined={"faces": 10}))
    seen = {}

    def check(cfg, before, after, reference, comfy=None, cancel=None):
        seen.update(before=before, after=after, reference=reference)
        return {"verdict": "after", "agreed": True, "before": {"score": 3, "problems": []},
                "after": {"score": 8, "problems": ["specks"]}, "model": "m", "seconds": 1}, b"board"
    monkeypatch.setattr(check3d, "check", check)
    refined = run("check_3d")["model_3d"]["refined"]
    assert seen == {"before": b"original", "after": b"generated", "reference": b"front"}
    assert refined["faces"] == 10 and refined["check"]["verdict"] == "after" and refined["check"]["board"] == check3d.BOARD_FILE
    assert lib.read_asset(npc_id, check3d.BOARD_FILE) == b"board"
