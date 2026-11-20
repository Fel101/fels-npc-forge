import pytest
from types import SimpleNamespace

from npcgen import cli, config, morphology, prompts, roll


@pytest.fixture(scope="module")
def data():
    return roll.load_data()


def test_morphology_aliases_and_table_backed_inference(data):
    assert morphology.MORPHOLOGIES["bipedal"]["validation"] == "gpu_sampled"
    assert morphology.clean("four-legged") == "quadruped"
    assert morphology.from_race(data, "anthro", "taur") == "taur"
    assert morphology.from_race(data, "anthro", "Dragontaur") == "taur"
    assert morphology.from_race(data, "anthro", "wolf") == "bipedal"


def test_roll_uses_race_body_plans_and_can_filter_them(data):
    for origin in ("taur", "dragontaur"):
        npc = roll.roll_npc(data, "anthro", {"origin": origin}, seed=3)
        assert npc["morphology"] == "taur"

    npc = roll.roll_npc(data, "anthro", {"morphology": "taur"}, seed=3)
    assert npc["morphology"] == "taur"
    assert npc["profile"]["race"] in {"Taur", "Dragontaur"}

    bipedal = [roll.roll_npc(data, "anthro", {"morphology": "bipedal"}, seed=s) for s in range(30)]
    assert all(npc["morphology"] == "bipedal" for npc in bipedal)


def test_conflicting_origin_and_morphology_are_rejected(data):
    with pytest.raises(ValueError, match="does not have a bipedal body plan"):
        roll.roll_npc(data, "anthro", {"origin": "taur", "morphology": "bipedal"})
    with pytest.raises(ValueError, match="does not have a taur body plan"):
        roll.roll_npc(data, "anthro", {"origin": "wolf", "morphology": "taur"})
    with pytest.raises(ValueError, match="has no characters with a taur body plan"):
        roll.roll_npc(data, "modern", {"morphology": "taur"})
    with pytest.raises(ValueError, match="locked race 'Wolf anthro'"):
        roll.roll_npc(data, "anthro", locked={"profile.race": "Wolf anthro", "morphology": "taur"})


def test_freeform_and_legacy_npcs_get_safe_body_plan_values(data):
    freeform = roll.roll_npc(data, "modern", freeform="a brass automaton")
    assert freeform["morphology"] == "unspecified"
    assert roll.roll_npc(data, "modern", {"morphology": "bipedal"}, freeform="a person")["morphology"] == "bipedal"
    assert roll.roll_npc(data, "modern", {"morphology": "taur"}, freeform="a four-legged centaur")["morphology"] == "taur"

    legacy_taur = {**roll.roll_npc(data, "anthro", {"origin": "taur"}), "style": "pixel"}
    legacy_taur.pop("morphology")
    assert roll.normalize(data, legacy_taur)["morphology"] == "taur"
    legacy_wolf = {**roll.roll_npc(data, "anthro", {"origin": "wolf"}), "style": "pixel"}
    legacy_wolf.pop("morphology")
    assert roll.normalize(data, legacy_wolf)["morphology"] == "bipedal"

    bad = {**legacy_wolf, "morphology": "winged"}
    with pytest.raises(ValueError, match="Body morphology"):
        roll.normalize(data, bad)


def test_full_body_support_boundary_preserves_portrait_generation():
    assert morphology.unsupported_reason("taur", {"portrait": True}) is None
    assert "drawn figure used as a guide" in morphology.unsupported_reason("taur", {"sprite": True})
    assert morphology.unsupported_reason("bipedal", {"sheet": {"views": ["front"]}}) is None
    assert "Choose a body plan" in morphology.unsupported_reason("unspecified", {"animations": {"clips": ["walk"]}})


def test_taur_prompt_names_the_quadruped_body_and_drops_human_limb_negative(data):
    npc = roll.roll_npc(data, "anthro", {"origin": "taur", "morphology": "taur"}, seed=19)
    prompt = prompts.sprite(npc, data["styles"]["semi_real"], data["settings"]["anthro"])
    assert "exactly four wolf legs total" in prompt["positive"]
    assert "human body ends at the waist" in prompt["positive"]
    assert "extra limbs" not in prompt["negative"]
    assert "second head" in prompt["negative"]


def test_cli_exposes_body_plan_constraints_and_rejects_taur_before_saving(data):
    parsed = cli.build_parser(data).parse_args(["generate", "--setting", "anthro", "--origin", "taur", "--morphology", "taur"])
    assert cli._constraints(parsed)["morphology"] == "taur"

    class NeverPersist:
        @staticmethod
        def create(_npc):
            pytest.fail("unsupported full-body NPC was saved before the CLI guard")

    parsed = cli.build_parser(data).parse_args(["generate", "--setting", "anthro", "--origin", "taur", "--sprite"])
    app = SimpleNamespace(cfg=config.DEFAULTS, lib=NeverPersist())
    with pytest.raises(SystemExit, match="stay blocked until a checked method is ready"):
        cli.cmd_generate(parsed, data, app)
