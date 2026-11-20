"""The engine side of a creature pack (Phase 5 of the creature maker): the loader files a game reads the pack with are made from the
engine's profile and the game's clip map, carry no class_name, and a pack installs into the game's pack folder without touching the
rest of it. Nothing here runs Godot: the headless test that reads a pack is run by hand (the Phase 5 result in the plan says how)."""
import importlib.util
import json
import pathlib
import re

import pytest

from npcgen import creature_export, creature_projects, creatures, creaturepack
from test_creature_pack_truth import chain
from test_creature_projects import pack_ready

EXPORT = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "export_creature_pack.py"
LOADER_FILES = {"loader/creature_pack.gd", "loader/creature_clips.gd", "loader/creature_pack_test.gd"}
DUST_MOON_MAP = {"idle": "Idle", "move": "Swim", "attack": "Bite", "hurt": "Hit", "die": "Death"}


def loaders(clip_map=DUST_MOON_MAP):
    return creature_export.loader_files("godot-4", clip_map)


def without_slug(project):
    return {key: value for key, value in project.items() if key != "slug"}


def dust_moon_ready(tmp_path):
    """The Burrower's game with its licence accepted, and a creature in it that is ready to pack."""
    store, cid = pack_ready(tmp_path, "dust-moon")
    game = without_slug(store.projects.get("dust-moon"))
    accepted = {"verdict": "conditions", "accepted_by": "Test Director", "date": "2026-10-17", "reason": "a test"}
    store.projects.put("dust-moon", {**game, "licence_acceptance": accepted}, creatures.BODY_PLANS)
    return store, cid


# ---- the loader files ------------------------------------------------------------------------------------------------------------------

def test_every_engine_a_game_can_name_has_its_loader_files():
    for engine in creature_projects.ENGINES:
        assert set(creature_export.loader_files(engine, {})) == LOADER_FILES


def test_no_loader_file_declares_a_class_name():
    # A pack sits inside its game's project, and a game can hold more than one pack: Godot quietly gives a shared class_name to one of them.
    for name, data in loaders().items():
        assert not re.search(r"^class_name\s", data.decode("utf-8"), re.MULTILINE), name


def test_the_clip_map_of_the_creatures_plan_becomes_the_loader_map():
    text = loaders()["loader/creature_clips.gd"].decode("utf-8")
    assert 'const MAP := {"attack": "Bite", "die": "Death", "hurt": "Hit", "idle": "Idle", "move": "Swim"}' in text


def test_a_creature_with_no_clips_gets_an_empty_map():
    assert "const MAP := {}" in loaders({})["loader/creature_clips.gd"].decode("utf-8")


@pytest.mark.parametrize("clip_map", [
    {"idle": 'Idle"; quit()'},      # a quote would end the string and run code in the game
    {"Idle": "Idle"},               # a state is lower case
    {"idle": ""},                   # a clip needs a name
    {"idle": "Idle\nbad"},          # a clip name is one line
    {"idle": "x" * 65},             # and at most 64 characters
])
def test_a_clip_name_that_can_not_be_written_into_the_script_is_refused(clip_map):
    with pytest.raises(ValueError):
        loaders(clip_map=clip_map)


def test_an_engine_with_no_profile_is_refused():
    with pytest.raises(ValueError, match="unity"):
        creature_export.loader_files("unity", {})


# ---- the folder a pack is installed into -----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("key, name, prefix, folder", [
    ("dustmoon-burrower", "Burrower", "dustmoon-", "burrower"),     # the Burrower's folder in Dust Moon's build/creatures
    ("eh-s01", "Reedcoil", "", "eh-s01-reedcoil"),                   # the Reedcoil's folder in Ember Hollow
    ("", "Reedcoil", "", "reedcoil"),                                # a creature with no bestiary key
])
def test_the_pack_folder_is_the_bestiary_key_without_the_game_prefix_and_with_the_name_when_it_lacks_it(key, name, prefix, folder):
    assert creature_export.install_folder(key, name, prefix) == folder


def test_a_pack_folder_given_by_name_is_used_only_when_it_is_a_safe_name():
    assert creature_export.install_folder("eh-s01", "Reedcoil", "", given="reedcoil-redo") == "reedcoil-redo"
    with pytest.raises(ValueError):
        creature_export.install_folder("eh-s01", "Reedcoil", "", given="../elsewhere")


# ---- installing ------------------------------------------------------------------------------------------------------------------------

def test_install_copies_the_pack_into_the_games_pack_folder_and_keeps_the_other_files(tmp_path):
    game_creatures = tmp_path / "creatures"
    (game_creatures / "reedcoil" / "pack").mkdir(parents=True)
    (game_creatures / "reedcoil" / "pack" / "notes.txt").write_text("hand-made", "utf-8")
    states = creature_export.install({"model.glb": b"glb", "loader/creature_pack.gd": b"gd"}, game_creatures, "reedcoil")
    assert dict(states) == {"model.glb": "new", "loader/creature_pack.gd": "new"}
    assert (game_creatures / "reedcoil" / "pack" / "loader" / "creature_pack.gd").read_bytes() == b"gd"
    assert (game_creatures / "reedcoil" / "pack" / "notes.txt").read_text("utf-8") == "hand-made"


def test_install_says_which_file_it_replaced_and_which_was_already_the_same(tmp_path):
    game_creatures = tmp_path / "creatures"
    game_creatures.mkdir()
    creature_export.install({"model.glb": b"one", "manifest.json": b"same"}, game_creatures, "x")
    states = dict(creature_export.install({"model.glb": b"two", "manifest.json": b"same"}, game_creatures, "x"))
    assert states == {"model.glb": "replaced", "manifest.json": "unchanged"}


def test_install_refuses_a_game_folder_that_is_not_there_and_creates_nothing(tmp_path):
    missing = tmp_path / "no-such-game"
    with pytest.raises(ValueError, match="pack folder"):
        creature_export.install({"model.glb": b"glb"}, missing, "reedcoil")
    assert not missing.exists()


def test_install_refuses_a_game_with_no_pack_folder_and_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)                                         # "" would otherwise mean this folder
    with pytest.raises(ValueError, match="no Pack folder"):
        creature_export.install({"model.glb": b"glb"}, "", "reedcoil")
    assert list(tmp_path.iterdir()) == []


def test_install_refuses_a_file_name_that_leaves_the_pack_folder(tmp_path):
    game_creatures = tmp_path / "creatures"
    game_creatures.mkdir()
    with pytest.raises(ValueError):
        creature_export.install({"../escape.glb": b"x"}, game_creatures, "reedcoil")
    assert not (game_creatures / "reedcoil" / "escape.glb").exists()


# ---- what a pack carries -----------------------------------------------------------------------------------------------------------------

def test_a_pack_carries_its_loader_its_engine_and_its_games_budget(tmp_path):
    store, cid = chain(tmp_path, "serpentine", elongation=2.3)               # the Ember Hollow game by default
    files = creaturepack.build(store, cid)
    manifest = json.loads(files["manifest.json"])
    assert LOADER_FILES <= set(files)
    assert LOADER_FILES <= set(manifest["files"])
    assert manifest["engine"] == "godot-4"
    assert manifest["tri_budget"] == 20000
    assert 'const MAP := {"attack": "Bite", "hurt": "Hit", "idle": "Idle", "move": "Swim"}' in files["loader/creature_clips.gd"].decode()


def test_a_dust_moon_pack_carries_its_own_budget_and_the_die_clip(tmp_path):
    store, cid = dust_moon_ready(tmp_path)
    files = creaturepack.build(store, cid)
    assert json.loads(files["manifest.json"])["tri_budget"] == 6000
    assert '"die": "Death"' in files["loader/creature_clips.gd"].decode()


def test_a_rigged_pack_says_so_when_its_game_names_no_clips_for_the_plan(tmp_path):
    store, cid = chain(tmp_path, "serpentine", elongation=2.3)
    game = without_slug(store.projects.get("ember-hollow"))
    store.projects.put("ember-hollow", {**game, "clip_map": {}}, creatures.BODY_PLANS)
    manifest = json.loads(creaturepack.build(store, cid)["manifest.json"])
    assert any("no clips" in warning for warning in manifest["warnings"])


# ---- the command line -------------------------------------------------------------------------------------------------------------------

def export_command():
    spec = importlib.util.spec_from_file_location("export_creature_pack", EXPORT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_command_line_writes_the_loader_and_installs_the_pack_into_the_games_folder(tmp_path):
    store, cid = chain(tmp_path, "serpentine", elongation=2.3)               # the key is hound-test, the name Hound
    game_creatures = tmp_path / "game-creatures"
    game_creatures.mkdir()
    game = without_slug(store.projects.get("ember-hollow"))
    store.projects.put("ember-hollow", {**game, "pack_dir": str(game_creatures)}, creatures.BODY_PLANS)
    out = tmp_path / "out"
    code = export_command().main([cid, str(out), "--library", str(tmp_path), "--install"])
    assert code == 0
    assert (out / "loader" / "creature_pack.gd").exists()
    assert (game_creatures / "hound-test" / "pack" / "model.glb").exists()
    assert (game_creatures / "hound-test" / "pack" / "loader" / "creature_clips.gd").exists()


def test_the_command_line_says_when_the_game_has_no_pack_folder_and_still_writes_the_pack(tmp_path, capsys):
    store, cid = chain(tmp_path, "serpentine", elongation=2.3)
    game = without_slug(store.projects.get("ember-hollow"))
    store.projects.put("ember-hollow", {**game, "pack_dir": ""}, creatures.BODY_PLANS)   # never the real game folder
    out = tmp_path / "out"
    code = export_command().main([cid, str(out), "--library", str(tmp_path), "--install"])
    assert code == 3
    assert "no Pack folder" in capsys.readouterr().err
    assert (out / "model.glb").exists()


def test_the_command_line_without_install_writes_only_the_folder_it_is_given(tmp_path):
    store, cid = chain(tmp_path, "serpentine", elongation=2.3)
    game_creatures = tmp_path / "game-creatures"
    game_creatures.mkdir()
    game = without_slug(store.projects.get("ember-hollow"))
    store.projects.put("ember-hollow", {**game, "pack_dir": str(game_creatures)}, creatures.BODY_PLANS)
    out = tmp_path / "out"
    assert export_command().main([cid, str(out), "--library", str(tmp_path)]) == 0
    assert (out / "loader" / "creature_pack_test.gd").exists()
    assert list(game_creatures.iterdir()) == []
