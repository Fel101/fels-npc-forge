import pytest

from npcgen.cast_packs import CastPacks, clean_pack


def test_pack_validation_covers_members_links_and_shared_content(tmp_path):
    store = CastPacks(tmp_path)
    pack = store.create({
        "name": "Lanternwatch",
        "template": "guild",
        "shared_context": "A night patrol in the old harbor.",
        "uniqueness_constraints": ["Only one captain"],
        "rumors": ["A bell rings below the water."],
        "plot_hooks": ["The patrol finds a door in the sea wall."],
        "members": [{"npc_id": "ada-123abc", "role": "Captain"}, {"npc_id": "bo-123abc", "role": "Scout"}],
        "relationships": [{"from": "ada-123abc", "to": "bo-123abc", "type": "mentor", "notes": "Former rivals"}],
    }, {"ada-123abc", "bo-123abc"})
    assert pack["members"][0]["role"] == "Captain"
    assert pack["relationships"][0]["type"] == "mentor"
    assert pack["rumors"] == ["A bell rings below the water."]

    with pytest.raises(ValueError, match="more than once"):
        clean_pack({"name": "Bad", "members": [{"npc_id": "ada-123abc"}, {"npc_id": "ada-123abc"}]})
    with pytest.raises(ValueError, match="connect NPCs in this pack"):
        clean_pack({"name": "Bad", "members": [{"npc_id": "ada-123abc"}],
                    "relationships": [{"from": "ada-123abc", "to": "missing-123abc", "type": "enemy"}]})
    with pytest.raises(ValueError, match="Unknown cast pack template"):
        clean_pack({"name": "Bad", "template": "spaceship", "members": [{"npc_id": "ada-123abc"}]})


def test_pack_store_updates_reloads_and_removes_deleted_members(tmp_path):
    store = CastPacks(tmp_path)
    pack = store.create({"name": "Harbor party", "members": [
        {"npc_id": "ada-123abc", "role": "Captain"}, {"npc_id": "bo-123abc", "role": "Scout"}],
        "relationships": [{"from": "ada-123abc", "to": "bo-123abc", "type": "ally"}]},
        {"ada-123abc", "bo-123abc"})
    updated = store.update(pack["id"], {**pack, "name": "Harbor watch", "shared_context": "The port is under curfew."},
                           {"ada-123abc", "bo-123abc"})
    assert updated["created"] == pack["created"] and updated["name"] == "Harbor watch"

    reloaded = CastPacks(tmp_path)
    assert reloaded.get(pack["id"])["shared_context"] == "The port is under curfew."
    reloaded.remove_npc("ada-123abc")
    remaining = reloaded.get(pack["id"])
    assert [member["npc_id"] for member in remaining["members"]] == ["bo-123abc"]
    assert remaining["relationships"] == []
    reloaded.remove_npc("bo-123abc")
    assert reloaded.list() == []


def test_pack_store_rejects_missing_npcs_without_writing(tmp_path):
    store = CastPacks(tmp_path)
    with pytest.raises(ValueError, match="No NPC with id missing-123abc"):
        store.create({"name": "Broken", "members": [{"npc_id": "missing-123abc"}]}, set())
    assert store.list() == [] and not store.path.exists()


def test_unique_roles_rejects_duplicate_named_roles_but_allows_unassigned_members(tmp_path):
    store = CastPacks(tmp_path)
    base = {"name": "Ship crew", "unique_roles": True, "members": [
        {"npc_id": "ada-123abc", "role": "Captain"},
        {"npc_id": "bo-123abc", "role": "captain"}]}
    with pytest.raises(ValueError, match="appears more than once"):
        store.create(base, {"ada-123abc", "bo-123abc"})
    assert store.list() == []
    created = store.create({**base, "members": [base["members"][0],
        {"npc_id": "bo-123abc", "role": ""}]}, {"ada-123abc", "bo-123abc"})
    assert created["unique_roles"] is True
    assert CastPacks(tmp_path).get(created["id"])["unique_roles"] is True
