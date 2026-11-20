"""A 3D model that is replaced or deleted is kept among the character's earlier models, with its skeleton and everything else it owns, and can be brought back."""
import io

import pytest
from PIL import Image

from npcgen import library as library_module
from npcgen.library import Library


@pytest.fixture()
def lib(tmp_path):
    return Library(tmp_path / "library")


def new_npc(lib):
    return lib.create({"setting": "modern", "style": "pixel", "seed": 1, "appearance": {},
                       "profile": {"first_name": "Kessa", "last_name": "Lark", "age": 22, "gender": "female", "occupation": "runner"}})["id"]


def make_model(lib, npc_id, tag, created="2026-10-15T10:00:00", original=True, head=True, rig=True, sprite="S1"):
    """A character's model as the pipeline leaves it: the model, its original, its picture, its skeleton file, a clean-up board and the face close-ups."""
    names = {"model_3d.glb": f"model {tag}", "model_3d_preview.png": f"picture {tag}"}
    model = {"file": "model_3d.glb", "preview": "model_3d_preview.png", "created": created, "size": len(names["model_3d.glb"]), "engine": f"Engine {tag}",
             "engine_id": "pixal3d", "source_sprite_created": sprite}
    if original:
        names["model_3d_original.glb"] = f"original {tag}"
        model["raw"] = "model_3d_original.glb"
    if head:
        names["model_3d_head/front.png"] = f"close-up {tag}"
    if rig:
        names["model_3d_rigged.glb"] = f"rig {tag}"
    for rel, text in names.items():
        lib.write_asset(npc_id, rel, text.encode())

    def apply(npc):
        assets = npc.setdefault("assets", {})
        assets["model_3d"] = model
        if rig:
            assets["rig_3d"] = {"file": "model_3d_rigged.glb", "created": created, "clips": ["Idle"]}
        else:
            assets.pop("rig_3d", None)
        assets.setdefault("sprite", {"file": "sprite.png", "created": "S1"})
    if not (lib.root / npc_id / "sprite.png").exists():
        picture = io.BytesIO()
        Image.new("RGBA", (8, 8), (200, 50, 50, 255)).save(picture, "PNG")
        lib.write_asset(npc_id, "sprite.png", picture.getvalue())                           # (deleting a model draws the character's thumbnail again)
    lib.update(npc_id, apply)


def replace_with(lib, npc_id, tag, **more):
    """What a generator does: keep the old one, then write the new files and remove what the old one left that is not this one's."""
    lib.archive_model(npc_id)
    for name in lib._model_names(npc_id):
        path = lib.resolve(npc_id, name)
        if path.is_dir():
            import shutil
            shutil.rmtree(path)
        else:
            path.unlink()
    make_model(lib, npc_id, tag, **more)


def live(lib, npc_id, rel):
    return lib.read_asset(npc_id, rel).decode()


def test_a_model_that_is_replaced_is_kept_with_everything_it_owns(lib):
    npc_id = new_npc(lib)
    make_model(lib, npc_id, "A", created="2026-10-14T10:00:00")
    replace_with(lib, npc_id, "B", created="2026-10-15T10:00:00", original=False, head=False, rig=False)

    assets = lib.get(npc_id)["assets"]
    (entry,) = assets["model_history"]
    assert entry["model"]["engine"] == "Engine A" and entry["rig"]["clips"] == ["Idle"] and entry["bytes"] > 0
    assert entry["names"] == ["model_3d.glb", "model_3d_head", "model_3d_original.glb", "model_3d_preview.png", "model_3d_rigged.glb"]
    kept = f"model_history/{entry['id']}/"
    assert [live(lib, npc_id, kept + name) for name in ("model_3d.glb", "model_3d_original.glb", "model_3d_preview.png", "model_3d_rigged.glb", "model_3d_head/front.png")] == \
        ["model A", "original A", "picture A", "rig A", "close-up A"]
    assert live(lib, npc_id, "model_3d.glb") == "model B" and assets["model_3d"]["engine"] == "Engine B" and "rig_3d" not in assets
    assert not lib.resolve(npc_id, "model_3d_original.glb").exists()                       # (the new one has none)


def test_bringing_an_earlier_model_back_swaps_it_with_the_one_in_use_and_loses_neither(lib):
    npc_id = new_npc(lib)
    make_model(lib, npc_id, "A", created="2026-10-14T10:00:00")
    replace_with(lib, npc_id, "B", created="2026-10-15T10:00:00", original=False, head=False, rig=False)
    (entry,) = lib.get(npc_id)["assets"]["model_history"]

    after = lib.restore_model(npc_id, entry["id"])

    assets = after["assets"]
    assert assets["model_3d"]["engine"] == "Engine A" and assets["rig_3d"]["clips"] == ["Idle"] and assets["model_3d"]["restored"]
    assert [live(lib, npc_id, name) for name in ("model_3d.glb", "model_3d_original.glb", "model_3d_preview.png", "model_3d_rigged.glb", "model_3d_head/front.png")] == \
        ["model A", "original A", "picture A", "rig A", "close-up A"]
    (other,) = assets["model_history"]                                                      # the one that was in use is the earlier one now
    assert other["id"] != entry["id"] and other["model"]["engine"] == "Engine B" and "rig" not in other
    assert live(lib, npc_id, f"model_history/{other['id']}/model_3d.glb") == "model B"
    assert not lib.resolve(npc_id, f"model_history/{entry['id']}").exists()                 # (moved into place, not copied: no second copy of it)

    again = lib.restore_model(npc_id, other["id"])                                          # and back again
    assert again["assets"]["model_3d"]["engine"] == "Engine B" and "rig_3d" not in again["assets"]
    assert not lib.resolve(npc_id, "model_3d_rigged.glb").exists() and not lib.resolve(npc_id, "model_3d_head").exists()
    (a_again,) = again["assets"]["model_history"]
    assert a_again["model"]["engine"] == "Engine A" and a_again["rig"]["clips"] == ["Idle"]


def test_only_the_newest_earlier_models_are_kept(lib):
    npc_id = new_npc(lib)
    lib.model_history_keep = 2
    make_model(lib, npc_id, "A", created="2026-10-11T10:00:00")
    for tag, day in (("B", 12), ("C", 13), ("D", 14)):
        replace_with(lib, npc_id, tag, created=f"2026-10-{day}T10:00:00")
    kept = lib.get(npc_id)["assets"]["model_history"]
    assert [e["model"]["engine"] for e in kept] == ["Engine B", "Engine C"]                 # A, the oldest, went
    folders = sorted(p.name for p in (lib.root / npc_id / "model_history").iterdir())
    assert folders == sorted(e["id"] for e in kept)


def test_nothing_is_kept_when_there_is_no_model_or_the_history_is_off(lib):
    npc_id = new_npc(lib)
    assert lib.archive_model(npc_id) is None and not (lib.root / npc_id / "model_history").exists()
    make_model(lib, npc_id, "A")
    lib.model_history_keep = 0
    assert lib.archive_model(npc_id) is None and "model_history" not in lib.get(npc_id)["assets"]
    lib.model_history_keep = 2
    lib.resolve(npc_id, "model_3d.glb").unlink()                                             # a record whose file is gone
    assert lib.archive_model(npc_id) is None


def test_a_model_that_is_kept_already_is_not_kept_twice_but_one_that_changed_is(lib):
    npc_id = new_npc(lib)
    make_model(lib, npc_id, "A", rig=False)
    first = lib.archive_model(npc_id)
    assert first and lib.archive_model(npc_id) is None                                       # (a step that ran twice)
    lib.update(npc_id, lambda npc: npc["assets"].update(rig_3d={"file": "model_3d_rigged.glb", "created": "2026-10-15T12:00:00", "clips": []}))
    lib.write_asset(npc_id, "model_3d_rigged.glb", b"a skeleton made since")
    second = lib.archive_model(npc_id)
    assert second and len(lib.get(npc_id)["assets"]["model_history"]) == 2                    # (it has a skeleton now: that is a different state)
    assert live(lib, npc_id, f"model_history/{second['id']}/model_3d_rigged.glb") == "a skeleton made since"


def test_an_earlier_model_whose_files_are_gone_is_refused_and_nothing_changes(lib):
    npc_id = new_npc(lib)
    make_model(lib, npc_id, "A")
    replace_with(lib, npc_id, "B")
    (entry,) = lib.get(npc_id)["assets"]["model_history"]
    lib.resolve(npc_id, f"model_history/{entry['id']}/model_3d_rigged.glb").unlink()
    before = lib.get(npc_id)
    with pytest.raises(ValueError, match="are gone"):
        lib.restore_model(npc_id, entry["id"])
    assert lib.get(npc_id)["assets"] == before["assets"] and live(lib, npc_id, "model_3d.glb") == "model B"
    with pytest.raises(KeyError):
        lib.restore_model(npc_id, "ffffffffffff")
    with pytest.raises(KeyError):
        lib.restore_model(npc_id, "../../x")                                                  # (a name that is not an id never reaches the disk)


def test_a_model_in_use_that_cannot_be_kept_is_not_given_up_for_a_restore(lib, monkeypatch):
    npc_id = new_npc(lib)
    make_model(lib, npc_id, "A")
    replace_with(lib, npc_id, "B")
    (entry,) = lib.get(npc_id)["assets"]["model_history"]

    def no_room(*args, **kwargs):
        raise OSError("no space left on the device")
    monkeypatch.setattr(library_module.shutil, "copy2", no_room)
    with pytest.raises(ValueError, match="could not be kept first"):
        lib.restore_model(npc_id, entry["id"])
    assert live(lib, npc_id, "model_3d.glb") == "model B" and lib.get(npc_id)["assets"]["model_history"][0]["id"] == entry["id"]
    assert sorted(p.name for p in (lib.root / npc_id / "model_history").iterdir()) == [entry["id"]]      # (no half-made copy is left)
    assert lib.archive_model(npc_id) is None                                                  # a replacement goes ahead as it did before there was a history


def test_an_earlier_model_can_be_deleted_for_good_and_the_one_in_use_stays(lib):
    npc_id = new_npc(lib)
    make_model(lib, npc_id, "A")
    replace_with(lib, npc_id, "B")
    (entry,) = lib.get(npc_id)["assets"]["model_history"]
    after = lib.delete_model_version(npc_id, entry["id"])
    assert "model_history" not in after["assets"] and not lib.resolve(npc_id, f"model_history/{entry['id']}").exists()
    assert live(lib, npc_id, "model_3d.glb") == "model B"
    with pytest.raises(KeyError):
        lib.delete_model_version(npc_id, entry["id"])


def test_a_model_deleted_by_hand_can_be_brought_back(lib):
    npc_id = new_npc(lib)
    make_model(lib, npc_id, "A")
    after = lib.delete_asset(npc_id, "model_3d")
    assert "model_3d" not in after["assets"] and "rig_3d" not in after["assets"]
    (entry,) = after["assets"]["model_history"]
    assert entry["model"]["engine"] == "Engine A" and live(lib, npc_id, f"model_history/{entry['id']}/model_3d_rigged.glb") == "rig A"
    back = lib.restore_model(npc_id, entry["id"])
    assert back["assets"]["model_3d"]["engine"] == "Engine A" and back["assets"]["rig_3d"]["clips"] == ["Idle"] and "model_history" not in back["assets"]
    assert live(lib, npc_id, "model_3d.glb") == "model A" and live(lib, npc_id, "model_3d_head/front.png") == "close-up A"


def test_a_model_made_from_a_sprite_that_was_drawn_again_since_says_it_is_out_of_date(lib):
    npc_id = new_npc(lib)
    make_model(lib, npc_id, "A", sprite="S1")
    replace_with(lib, npc_id, "B", sprite="S2")
    lib.update(npc_id, lambda npc: npc["assets"].update(sprite={"file": "sprite.png", "created": "S2"}))
    (entry,) = lib.get(npc_id)["assets"]["model_history"]
    assert lib.restore_model(npc_id, entry["id"])["assets"]["model_3d"]["stale"] is True
    (other,) = lib.get(npc_id)["assets"]["model_history"]
    assert "stale" not in lib.restore_model(npc_id, other["id"])["assets"]["model_3d"]       # (B was made from the sprite it has now)


def test_a_deleted_character_takes_its_earlier_models_with_it_and_a_restored_one_gets_them_back(lib):
    npc_id = new_npc(lib)
    make_model(lib, npc_id, "A")
    replace_with(lib, npc_id, "B")
    lib.delete(npc_id)
    lib.restore(npc_id)
    (entry,) = lib.get(npc_id)["assets"]["model_history"]
    assert live(lib, npc_id, f"model_history/{entry['id']}/model_3d.glb") == "model A"
