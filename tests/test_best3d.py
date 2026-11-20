"""The best-quality button: one request that ticks the chain that came out best, for each character (npcgen/best3d.py)."""
import threading
import types

import pytest

from npcgen import best3d, cli, head3d, morphology, roll, service
from npcgen.jobs import Jobs

VIEWS = ("front", "side_left", "back", "side_right")
SPRITE = {"file": "sprite.png", "created": "t"}
SHEET = {"views": {v: {"file": f"sheet/{v}.png", "source_sprite_created": "t"} for v in VIEWS}}
HEAD_PICTURES = {v: {"file": f"head_3d/{v}.png"} for v in head3d.VIEWS}


def _npc(**assets):
    return {"id": "mira", "profile": {"first_name": "Mira", "last_name": "Vale"},
            "assets": {"portrait": {"file": "portrait.png"}, "sprite": SPRITE, "sheet": SHEET, **assets}}


def _kinds(steps):
    return [s["kind"] for s in steps]


def _plan(npc, request=None, options=None):
    assets, options = best3d.expand(npc, request or {"best_3d": True}, options or {})
    return Jobs.plan(npc, assets, options), assets, options


def test_a_character_with_no_head_gets_the_whole_chain_in_order():
    steps, assets, options = _plan(_npc())
    assert _kinds(steps) == ["model_3d"] + ["head_view"] * 4 + ["head_3d", "refine_3d"]      # the head comes before the clean-up that attaches it
    assert steps[0]["label"] == "Trellis2 model from four pictures, eyes softened for the shape"
    assert steps[-1]["key"] == "face+hair+hands+head" and "best_3d" not in assets
    assert [flag for flag in best3d.CLEANUP if assets[flag]] == list(best3d.CLEANUP)


def test_a_head_that_is_already_made_is_used_as_it_is():
    npc = _npc(head_3d={"file": "head_3d.glb", "views": HEAD_PICTURES})
    steps, assets, _ = _plan(npc)
    assert assets["head_3d"] is False and _kinds(steps) == ["model_3d", "refine_3d"]


def test_an_out_of_date_head_is_built_again_from_its_close_ups_or_from_scratch():
    stale = _npc(head_3d={"file": "head_3d.glb", "stale": True, "views": HEAD_PICTURES})
    assert best3d.head_request(stale) == {"views": [], "model": True}
    assert _kinds(_plan(stale)[0]) == ["model_3d", "head_3d", "refine_3d"]
    unfinished = _npc(head_3d={"views": {"front": {"file": "head_3d/front.png"}}})
    assert best3d.head_request(unfinished) is True and best3d.head_request(_npc()) is True


def test_what_else_the_request_asks_for_stays_and_a_head_it_asks_for_itself_is_kept():
    npc = _npc(head_3d={"file": "head_3d.glb", "views": HEAD_PICTURES})
    steps, assets, _ = _plan(npc, {"best_3d": True, "rig_3d": True, "expressions": ["happy"], "head_3d": True})
    assert assets["rig_3d"] and assets["expressions"] == ["happy"] and assets["head_3d"] is True
    kinds = _kinds(steps)
    assert kinds.index("refine_3d") < kinds.index("rig_3d")                                 # the skeleton goes into the cleaned-up model


def test_the_recipe_cannot_turn_the_chain_down_but_keeps_the_rest():
    recipe = {"quality": "draft", "three_d_model": "pixal3d", "three_d_soften_eyes": False, "three_d_quality": "high",
              "head_quality": "high", "head_expression": "keep"}
    _, options = best3d.expand(_npc(), {"best_3d": True}, recipe)
    assert options == {**recipe, **best3d.OPTIONS, **best3d.JOB_ONLY} and options["quality"] == "draft"
    assert options["three_d_model"] == "trellis2" and options["head_expression"] == "neutral" and options["three_d_soften_eyes"] is True


def test_every_part_of_the_chain_is_an_option_that_exists():
    from npcgen import cleanup3d
    from npcgen.jobs import DEFAULT_OPTIONS
    assert set(best3d.CLEANUP) <= {flag for _key, flag in cleanup3d.OPTIONS}
    assert set(best3d.OPTIONS) <= set(DEFAULT_OPTIONS)
    assert set(best3d.ASSETS) <= set(service.clean_assets({"portrait": True}, roll.load_data()))
    assert best3d.STEPS and all(isinstance(text, str) and text for text in best3d.STEPS)


def test_submit_expands_the_request_for_each_character():
    class Recorder(Jobs):
        def __init__(self):
            self.queue = types.SimpleNamespace(put=lambda job, priority: None)
            self.jobs, self.lock, self.closed = {}, threading.RLock(), False

        def _persist(self):
            pass

    jobs = Recorder()
    jobs.submit(_npc(), {"best_3d": True}, {"quality": "draft", "three_d_model": "hunyuan21"})
    jobs.submit(_npc(head_3d={"file": "head_3d.glb", "views": HEAD_PICTURES}), {"best_3d": True}, {})
    bare, made = jobs.jobs.values()
    assert _kinds(bare.steps) == ["model_3d"] + ["head_view"] * 4 + ["head_3d", "refine_3d"]
    assert _kinds(made.steps) == ["model_3d", "refine_3d"]
    assert bare.options["three_d_model"] == "trellis2" and bare.options["three_d_soften_eyes"] is True and bare.options["quality"] == "draft"
    assert bare.options["three_d_pictures_optional"] is True


def test_the_request_is_cleaned_and_checked_like_any_3d_model():
    data = roll.load_data()
    assert service.clean_assets({"best_3d": True}, data)["best_3d"] is True
    assert service.clean_assets({"portrait": True}, data)["best_3d"] is False
    with pytest.raises(ValueError, match="not both"):
        service.clean_assets({"best_3d": True, "import_3d": {"model_file": "3d/a.glb"}}, data)
    assert morphology.unsupported_reason("taur", {"best_3d": True})                          # full-body models only
    assert morphology.unsupported_reason("bipedal", {"best_3d": True}) is None
    parser = cli.build_parser(data)
    assert parser.parse_args(["generate", "--ids", "x", "--best-3d"]).best_3d is True
    assert parser.parse_args(["generate", "--ids", "x", "--portrait"]).best_3d is False


def test_the_button_needs_everything_the_chain_needs_and_names_each_missing_part_once():
    parts = ("head_3d", "sharp_face", "match_hair", "clean_hands", "attach_head")
    ready = {"online": True, "three_d_missing": {"trellis2": []}, **{key: {"missing": []} for key in parts}}
    assert best3d.availability(ready) == {"missing": []}
    lacking = {**ready, "three_d_missing": {"trellis2": ["Trellis2 nodes"]}, "head_3d": {"missing": ["Pixal3D"]},
               "clean_hands": {"missing": ["Blender", "SDPose"]}, "attach_head": {"missing": ["Blender", "SDPose"]}}
    assert best3d.availability(lacking) == {"missing": ["Trellis2 nodes", "Pixal3D", "Blender", "SDPose"]}
    offline = {"online": False, **{key: {"missing": ["ComfyUI"]} for key in parts}}
    assert best3d.availability(offline) == {"missing": ["ComfyUI"]}
    assert best3d.availability({}) == {"missing": ["ComfyUI"]}
