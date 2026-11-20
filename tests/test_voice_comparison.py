import copy

import pytest

from npcgen.library import Library
from npcgen.voice import comparison, record


def test_comparisons_require_same_script_retain_candidates_and_keep_human_reviews(tmp_path):
    lib = Library(tmp_path / "library")
    npc = lib.create({"profile": {"first_name": "Actor", "age": 30}, "setting": "fantasy", "assets": {}})
    takes = []
    for index in range(18):
        take = {"id": f"{index:08x}", "text": "Same script." if index != 2 else "Different script.",
                "language": "English", "file": f"voice/takes/{index:08x}.wav", "engine": "qwen", "seed": index}
        lib.write_asset(npc["id"], take["file"], bytes([index]) * 10)
        takes.append(take)
    lib.update(npc["id"], lambda rec: rec.update(voice={"takes": takes}))
    with pytest.raises(ValueError, match="same exact"):
        comparison.create(lib, npc["id"], {"take_ids": [takes[0]["id"], takes[2]["id"]]})
    saved = comparison.create(lib, npc["id"], {"take_ids": [takes[0]["id"], takes[1]["id"]], "title": "Gatekeepers"})
    session = saved["voice"]["comparisons"][0]
    frozen = copy.deepcopy(session["candidates"])
    saved = comparison.review(lib, npc["id"], session["id"], takes[0]["id"], {"rating": 4, "favorite": True, "notes": "Clear words."})
    reviewed = saved["voice"]["comparisons"][0]["candidates"][0]
    assert reviewed["rating"] == 4 and reviewed["favorite"] and reviewed["sha256"] == frozen[0]["sha256"]
    with pytest.raises(ValueError, match="Only ratings"):
        comparison.review(lib, npc["id"], session["id"], takes[0]["id"], {"text": "Rewrite"})
    with pytest.raises(ValueError, match="Rating"):
        comparison.review(lib, npc["id"], session["id"], takes[0]["id"], {"rating": True})
    def prune(rec):
        record.prune_takes(lib, rec, keep=3)
    lib.update(npc["id"], prune)
    remaining = {t["id"] for t in lib.get(npc["id"])["voice"]["takes"]}
    assert takes[0]["id"] in remaining and takes[1]["id"] in remaining and len(remaining) == 5
    assert lib.read_asset(npc["id"], takes[0]["file"]) == bytes([0]) * 10
    assert "comparisons" not in record.render_snapshot(saved["voice"])
    lib.write_asset(npc["id"], takes[0]["file"], b"modified candidate")
    with pytest.raises(ValueError, match="checksum"):
        record.select_take(lib, npc["id"], takes[0]["id"])
