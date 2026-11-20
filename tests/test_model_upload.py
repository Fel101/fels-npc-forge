"""A .glb from the owner's computer becomes a character's 3D model exactly as it is (npcgen/jobs.py use_uploaded_model); the model it replaces is kept."""
import pytest

from npcgen.jobs import use_uploaded_model
from npcgen.library import Library
from test_import3d import glb, mesh_doc


@pytest.fixture()
def bench(tmp_path):
    lib = Library(tmp_path / "library")
    npc_id = lib.create({"setting": "modern", "style": "pixel", "seed": 1, "appearance": {},
                         "profile": {"first_name": "Kessa", "last_name": "Lark", "age": 22, "gender": "female", "occupation": "runner"}})["id"]
    lib.write_asset(npc_id, "model_3d.glb", b"the old model")
    lib.write_asset(npc_id, "model_3d_preview.png", b"old picture")
    lib.write_asset(npc_id, "model_3d_original.glb", b"the old model, before its clean-up")
    lib.write_asset(npc_id, "model_3d_head/front.png", b"close-up")
    lib.write_asset(npc_id, "model_3d_rigged.glb", b"old rig")
    lib.update(npc_id, lambda n: n["assets"].update(
        model_3d={"file": "model_3d.glb", "preview": "model_3d_preview.png", "raw": "model_3d_original.glb", "created": "2026-10-14T10:00:00", "size": 13, "engine": "Pixal3D multi-view"},
        rig_3d={"file": "model_3d_rigged.glb", "created": "2026-10-14T10:05:00"}))
    return lib, npc_id


def test_an_uploaded_file_becomes_the_model_exactly_as_it_is_and_the_old_one_is_kept(bench):
    lib, npc_id = bench
    data = glb(mesh_doc(faces=300))
    npc = use_uploaded_model(lib, npc_id, data, "Kessa final.glb")

    assert lib.read_asset(npc_id, "model_3d.glb") == data                                       # (not a byte changed)
    meta = npc["assets"]["model_3d"]
    assert (meta["engine"], meta["engine_id"], meta["textured"], meta["size"], meta["format"]) == ("Uploaded: Kessa final.glb", "import", False, len(data), "glb")
    assert meta["imported"] == {"name": "Kessa final.glb", "folder": "upload", "faces_in": 300, "faces": 300, "cleaned": False, "seconds": 0}
    assert "raw" not in meta and "preview" not in meta and "refined" not in meta
    assert npc["upload"]["faces"] == 300 and npc["upload"]["textured"] is False and npc["upload"]["warnings"] == []
    assert npc["assets"]["rig_3d"]["stale"] is True                                             # (its skeleton was made for the old model)
    for rel in ("model_3d_preview.png", "model_3d_original.glb", "model_3d_head"):               # (what the old model left is not this one's)
        assert not lib.resolve(npc_id, rel).exists(), rel

    (entry,) = npc["assets"]["model_history"]
    kept = f"model_history/{entry['id']}/"
    assert entry["model"]["engine"] == "Pixal3D multi-view" and entry["rig"]["file"] == "model_3d_rigged.glb"
    assert [lib.read_asset(npc_id, kept + name) for name in ("model_3d.glb", "model_3d_original.glb", "model_3d_preview.png", "model_3d_rigged.glb", "model_3d_head/front.png")] == \
        [b"the old model", b"the old model, before its clean-up", b"old picture", b"old rig", b"close-up"]
    back = lib.restore_model(npc_id, entry["id"])                                               # and the old model can come back, the upload kept in turn
    assert back["assets"]["model_3d"]["engine"] == "Pixal3D multi-view" and lib.read_asset(npc_id, "model_3d.glb") == b"the old model"
    assert back["assets"]["model_history"][0]["model"]["engine"] == "Uploaded: Kessa final.glb"


def test_a_character_with_no_model_just_gets_one(tmp_path):
    lib = Library(tmp_path / "library")
    npc_id = lib.create({"setting": "modern", "style": "pixel", "seed": 1, "appearance": {},
                         "profile": {"first_name": "Nadia", "last_name": "Ivanova", "age": 30, "gender": "female", "occupation": "tester"}})["id"]
    data = glb(mesh_doc(faces=12))
    npc = use_uploaded_model(lib, npc_id, data, "n.glb")
    assert npc["assets"]["model_3d"]["file"] == "model_3d.glb" and lib.read_asset(npc_id, "model_3d.glb") == data
    assert "model_history" not in npc["assets"] and "rig_3d" not in npc["assets"]


def test_a_file_that_cannot_be_used_is_refused_in_words_and_nothing_changes(bench):
    lib, npc_id = bench
    good = glb(mesh_doc(faces=300))
    before = lib.get(npc_id)
    for data, words in ((b"PK\x03\x04 a zip file, not a model" * 4, "not a GLB"), (b"", "too short"), (good[:-1], "incomplete"), (good + b"\0\0\0\0", "incomplete"),
                        (glb(mesh_doc(faces=0)), "no triangle mesh")):
        with pytest.raises(ValueError, match=words):
            use_uploaded_model(lib, npc_id, data, "x.glb")
    assert lib.get(npc_id) == before and lib.read_asset(npc_id, "model_3d.glb") == b"the old model"
    assert not (lib.root / npc_id / "model_history").exists()


def test_what_looks_wrong_with_the_file_is_reported_with_the_upload(bench):
    lib, npc_id = bench
    npc = use_uploaded_model(lib, npc_id, glb(mesh_doc(faces=300, skins=[{"joints": [0]}])), "rigged.glb")
    assert any("skeleton" in warning for warning in npc["upload"]["warnings"])
    assert npc["assets"]["model_3d"]["file"] == "model_3d.glb"                                  # (still used: the warning is for the person to read)


def test_the_name_the_file_is_called_cannot_name_a_place_or_break_the_record(bench):
    lib, npc_id = bench
    npc = use_uploaded_model(lib, npc_id, glb(mesh_doc(faces=300)), r"C:\Users\me\Models\My Model (v2) <final>.glb")
    assert npc["assets"]["model_3d"]["engine"] == "Uploaded: My Model _v2_ _final_.glb"
    npc = use_uploaded_model(lib, npc_id, glb(mesh_doc(faces=300)), "../../../etc/passwd")
    assert npc["assets"]["model_3d"]["engine"] == "Uploaded: passwd"
    npc = use_uploaded_model(lib, npc_id, glb(mesh_doc(faces=300)), "")
    assert npc["assets"]["model_3d"]["engine"] == "Uploaded: model.glb"
