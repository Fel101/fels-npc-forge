import io
import json
import zipfile

import pytest
from PIL import Image

from npcgen.library import Library, ethnicity, hair_color


@pytest.fixture()
def lib(tmp_path):
    return Library(tmp_path / "library")


def npc(first="Maya", last="Okafor"):
    return {"setting": "modern", "style": "pixel", "seed": 1, "profile": {"first_name": first, "last_name": last, "age": 30,
            "gender": "female", "occupation": "barista"}, "appearance": {}}


def png_bytes(size=(64, 64), colour=(200, 50, 50)):
    buf = io.BytesIO()
    Image.new("RGB", size, colour).save(buf, "PNG")
    return buf.getvalue()


def test_create_get_update_delete(lib):
    made = lib.create(npc())
    assert made["id"].startswith("maya-okafor-") and made["assets"] == {}
    assert lib.get(made["id"])["profile"]["first_name"] == "Maya"
    lib.update(made["id"], lambda n: n["profile"].update(age=31))
    assert lib.get(made["id"])["profile"]["age"] == 31
    lib.delete(made["id"])
    with pytest.raises(KeyError):
        lib.get(made["id"])


def test_a_deleted_character_waits_in_the_trash_and_can_be_brought_back(lib):
    """NF-14: deletion was immediate with no way back."""
    made = lib.create(npc())
    lib.write_asset(made["id"], "portrait.png", png_bytes())
    other = lib.create(npc("Joss", "Varle"))
    lib.delete(made["id"])
    assert [n["id"] for n in lib.list()] == [other["id"]] and not (lib.root / made["id"]).exists()
    with pytest.raises(KeyError):
        lib.get(made["id"])
    rows = lib.trashed()
    assert [(r["id"], r["name"]) for r in rows] == [(made["id"], "Maya Okafor")] and rows[0]["deleted"][:4].isdigit()
    assert [n["id"] for n in lib.refresh_index()] == [other["id"]]               # the trash is not a character, a rescan skips it
    back = lib.restore(made["id"])
    assert back["id"] == made["id"] and back["name"] == "Maya Okafor" and lib.trashed() == []
    assert lib.read_asset(made["id"], "portrait.png") == png_bytes()              # everything came back, pictures included
    assert {n["id"] for n in lib.list()} == {made["id"], other["id"]}
    with pytest.raises(KeyError):
        lib.restore(made["id"])                                                    # nothing left in the trash


def test_deleting_twice_keeps_both_copies_and_a_restore_never_overwrites_a_character(lib, tmp_path):
    import shutil
    made = lib.create(npc())
    shutil.copytree(lib.root / made["id"], tmp_path / "again")
    lib.delete(made["id"])
    shutil.copytree(tmp_path / "again", lib.root / made["id"])                    # the same id comes back (an import), and goes again
    lib.refresh_index()
    lib.delete(made["id"])
    assert len(lib.trashed()) == 2 and len({r["folder"] for r in lib.trashed()}) == 2      # two copies, in the same second too
    lib.restore(made["id"])
    assert len(lib.trashed()) == 1
    with pytest.raises(ValueError, match="already a character"):
        lib.restore(made["id"])                                                    # the live one stays
    assert lib.get(made["id"])["profile"]["first_name"] == "Maya"
    with pytest.raises(FileNotFoundError):
        lib.delete("no-such-npc-123456")


def test_a_character_made_a_moment_ago_by_a_request_that_failed_is_removed_not_kept(lib):
    made = lib.create(npc())
    lib.delete(made["id"], trash=False)
    assert lib.trashed() == [] and lib.list() == [] and not (lib.root / made["id"]).exists()
    with pytest.raises(FileNotFoundError):
        lib.delete(made["id"], trash=False)


def test_the_trash_gives_up_what_has_waited_long_enough_and_only_that(lib, tmp_path):
    old, recent = lib.create(npc("Old", "Timer")), lib.create(npc("Recent", "Loss"))
    lib.delete(old["id"])
    lib.delete(recent["id"])
    folder = next(r["folder"] for r in lib.trashed() if r["id"] == old["id"])
    (lib.root / ".trash" / folder).rename(lib.root / ".trash" / f'{old["id"]}--20200101-000000')       # deleted years ago
    assert lib.purge_trash(0) == 0 and len(lib.trashed()) == 2                    # 0 days: keep everything
    assert lib.purge_trash(30) == 1
    assert [r["id"] for r in lib.trashed()] == [recent["id"]]
    assert not (lib.root / ".trash" / f'{old["id"]}--20200101-000000').exists()
    assert lib.purge_trash(30) == 0


def test_a_folder_in_the_trash_without_a_character_in_it_is_ignored(lib):
    (lib.root / ".trash" / "junk--20260101-000000").mkdir(parents=True)
    (lib.root / ".trash" / "readme.txt").write_text("not a character")
    assert lib.trashed() == [] and lib.purge_trash(1) == 0


def test_cards_carry_the_project_and_test_tags_a_client_gave_its_characters(lib):
    lib.create({**npc("Ilsa", "Penrow"), "custom": {"project": "ember-hollow", "key": "ilsa-penrow"}})
    lib.create({**npc("Test", "Only"), "custom": {"project": "ember-hollow", "test": True}})
    lib.create(npc("No", "Tag"))
    cards = {c["name"]: c for c in lib.list()}
    assert (cards["Ilsa Penrow"]["project"], cards["Ilsa Penrow"]["test"]) == ("ember-hollow", False)
    assert (cards["Test Only"]["project"], cards["Test Only"]["test"]) == ("ember-hollow", True)
    assert (cards["No Tag"]["project"], cards["No Tag"]["test"]) == ("", False)


def test_ids_never_collide_for_the_same_name(lib):
    assert lib.create(npc())["id"] != lib.create(npc())["id"]


@pytest.mark.parametrize("bad", ["../x", "a/b", "..", "", "UPPER", "x" * 200])
def test_bad_ids_and_traversal_are_rejected(lib, bad):
    with pytest.raises(KeyError):
        lib.get(bad)


def test_asset_paths_cannot_escape_the_npc_folder(lib):
    made = lib.create(npc())
    for rel in ("../../secret.txt", "..\\..\\secret.txt", "/etc/passwd"):
        with pytest.raises(KeyError):
            lib.resolve(made["id"], rel)


def test_listing_thumbnails_and_asset_deletion(lib):
    made = lib.create(npc())
    lib.write_asset(made["id"], "portrait.png", png_bytes((512, 512)))
    lib.update(made["id"], lambda n: n["assets"].update(portrait={"file": "portrait.png"}))
    lib.make_thumb(made["id"])
    assert max(Image.open(lib.resolve(made["id"], "thumb.png")).size) == 256
    listed = lib.list()[0]
    assert listed["name"] == "Maya Okafor" and listed["has"]["portrait"] and listed["thumb"]

    lib.delete_asset(made["id"], "portrait")
    assert not lib.resolve(made["id"], "portrait.png").exists() and not lib.list()[0]["thumb"]
    with pytest.raises(KeyError):
        lib.delete_asset(made["id"], "sprite")


def test_export_zip_has_selected_pngs_and_an_index_but_not_raw_sources_or_candidates(lib):
    made = lib.create(npc())
    for rel in ("sprite.png", "sprite_raw.png", "expressions/happy.png", "candidates/sprite/abc123abc123.png"):
        lib.write_asset(made["id"], rel, png_bytes())
    lib.update(made["id"], lambda n: n["assets"].update(
        sprite={"file": "sprite.png", "raw": "sprite_raw.png", "candidate_id": "abc123abc123"}, expressions={"happy": {"file": "expressions/happy.png"}},
        candidates={"sprite": [{"id": "abc123abc123", "file": "candidates/sprite/abc123abc123.png"}]}))
    with zipfile.ZipFile(io.BytesIO(lib.export_zip([made["id"], "no-such-npc"]))) as z:
        names = set(z.namelist())
        index = json.loads(z.read("index.json"))
        exported_npc = json.loads(z.read(f"{made['id']}/npc.json"))
    assert {f"{made['id']}/npc.json", f"{made['id']}/sprite.png", f"{made['id']}/expressions/happy.png", "index.json"} <= names
    assert f"{made['id']}/sprite_raw.png" not in names and f"{made['id']}/candidates/sprite/abc123abc123.png" not in names
    assert "candidates" not in exported_npc["assets"] and "candidate_id" not in exported_npc["assets"]["sprite"] and len(index) == 1


def test_export_zip_carries_the_body_tabs_saved_recipe_byte_for_byte_and_only_when_there_is_one(lib):
    with_recipe, without = lib.create(npc("Rosa", "Diaz")), lib.create(npc("Sam", "Ito"))
    recipe = json.dumps({"version": 10, "base": "forge-anatomical-female-v7", "values": {"height": 0.25}}, indent=1).encode()          # (laid out as the Body tab writes it)
    lib.write_asset(with_recipe["id"], "body_design.json", recipe)
    with zipfile.ZipFile(io.BytesIO(lib.export_zip([with_recipe["id"], without["id"]]))) as z:
        names, index = set(z.namelist()), {entry["id"]: entry for entry in json.loads(z.read("index.json"))}
        saved = z.read(f"{with_recipe['id']}/body_design.json")
    assert saved == recipe and f"{without['id']}/body_design.json" not in names
    assert "body_design.json" in index[with_recipe["id"]]["files"] and "body_design.json" not in index[without["id"]]["files"]


@pytest.mark.parametrize("hair, family", [
    ("dark brown curly shoulder-length hair", "Brown"), ("black long hair in a sleek high ponytail", "Black"),
    ("strawberry blonde curly short cropped hair", "Blonde"), ("platinum blonde bob", "Blonde"), ("copper red wavy hair", "Red"),
    ("salt-and-pepper", "Gray / white"), ("platinum silver long hair", "Gray / white"), ("white hair", "Gray / white"),
    ("pastel pink chin-length bob", "Dyed / fantasy"), ("rose gold hair", "Dyed / fantasy"),
    ("black hair with blue tips", "Black"),                                        # the first colour named decides
    ("bald", "Bald"), ("layered hair", ""), ("", ""), (None, ""),
])
def test_hair_color_names_the_family_of_the_first_colour_in_a_description(hair, family):
    assert hair_color(hair) == family


@pytest.mark.parametrize("heritage, family", [
    ("Japanese", "East Asian"), ("Korean", "East Asian"), ("Chinese", "East Asian"), ("East Asian", "East Asian"),
    ("Southeast Asian", "Southeast Asian"),                                            # not "East Asian"
    ("Indian / South Asian", "South Asian"), ("South Asian", "South Asian"),
    ("Middle Eastern / North African", "Middle Eastern / North African"),               # not "Black / African"
    ("Black / African", "Black / African"),
    ("Latina", "Latina / Hispanic"), ("Hispanic / Latino", "Latina / Hispanic"), ("Brazilian", "Latina / Hispanic"),
    ("Caucasian", "White / European"), ("European", "White / European"), ("Eastern European", "White / European"),
    ("Scandinavian", "White / European"), ("Mediterranean", "White / European"), ("Mixed heritage", "Mixed"),
    ("Dwarf", ""), ("Half-orc", ""), ("Human", ""), ("", ""), (None, ""),
])
def test_ethnicity_groups_the_heritage_labels_of_every_setting(heritage, family):
    assert ethnicity(heritage) == family


def listed(lib, person):
    made = lib.create(person)
    return next(r for r in lib.list() if r["id"] == made["id"])


def test_list_rows_carry_what_the_library_filters_by(lib):
    person = npc()
    person["profile"]["heritage"] = "Japanese"
    person["appearance"] = {"hair": "black long hair in a high ponytail", "eyes": "dark brown", "build": "slim", "skin_tone": "light"}
    row = listed(lib, person)
    assert (row["heritage"], row["ethnicity"], row["hair_color"], row["eyes"], row["build"], row["skin_tone"]) == \
        ("Japanese", "East Asian", "Black", "dark brown", "slim", "light")
    assert row["hair"] == "black long hair in a high ponytail" and row["age"] == 30 and row["style"] == "pixel"

    elf = npc()
    elf["profile"]["race"] = "Elf"                                                # a fantasy character has a race and no heritage
    assert listed(lib, elf)["heritage"] == "Elf" and listed(lib, elf)["ethnicity"] == ""

    bare = listed(lib, npc())                                                     # nothing to filter by: empty, not missing
    assert (bare["heritage"], bare["ethnicity"], bare["hair"], bare["hair_color"], bare["eyes"], bare["build"], bare["skin_tone"]) == ("",) * 7


def test_list_uses_the_incremental_index_instead_of_reparsing_every_npc(lib, monkeypatch):
    made = lib.create(npc())
    assert lib.list()[0]["id"] == made["id"]

    def no_disk_reads(*args, **kwargs):
        raise AssertionError("library polling reparsed npc.json")

    monkeypatch.setattr(__import__("pathlib").Path, "read_text", no_disk_reads)
    assert lib.list()[0]["name"] == "Maya Okafor"


def test_refresh_index_discovers_an_externally_added_record(lib):
    external = npc("External", "Record")
    external.update(id="external-record", created="now", updated="now", assets={})
    folder = lib.root / external["id"]
    folder.mkdir()
    (folder / "npc.json").write_text(json.dumps(external), "utf-8")
    assert external["id"] not in {row["id"] for row in lib.list()}
    assert external["id"] in {row["id"] for row in lib.refresh_index()}


def test_refresh_index_skips_malformed_records_without_losing_valid_ones(lib):
    made = lib.create(npc())
    folder = lib.root / "bad-record"
    folder.mkdir()
    (folder / "npc.json").write_text('{"id": "bad-record", "profile": []}', "utf-8")
    assert [row["id"] for row in lib.refresh_index()] == [made["id"]]


# ---- the trash, as the page lists it ------------------------------------------------------------------------------------
def test_a_trashed_character_is_listed_with_what_the_page_shows_and_can_be_removed_for_good(lib):
    made = lib.create({**npc(), "assets": {"portrait": {"file": "portrait.png"}, "expressions": {"happy": {}, "sad": {}}}})
    lib.write_asset(made["id"], "thumb.png", png_bytes())
    keep = lib.create(npc("Joss", "Varle"))
    lib.delete(made["id"])
    lib.delete(keep["id"])
    row = next(r for r in lib.trashed() if r["id"] == made["id"])
    assert (row["name"], row["occupation"], row["setting"], row["style"], row["thumb"]) == ("Maya Okafor", "barista", "modern", "pixel", True)
    assert row["has"] == {"portrait": True, "sprite": False, "model_3d": False, "expressions": 2}
    assert next(r for r in lib.trashed() if r["id"] == keep["id"])["thumb"] is False
    assert lib.trash_file(row["folder"]).read_bytes() == png_bytes()                  # the small picture, nothing else of it
    for bad in ("../" + row["folder"], row["folder"] + "/../x", "", None, "no-such--20200101-000000"):
        with pytest.raises(KeyError):
            lib.trash_file(bad)
    with pytest.raises(KeyError):
        lib.trash_file(row["folder"], "npc.json")                                       # only the thumbnail is ever served
    lib.purge_trashed(row["folder"])
    assert [r["id"] for r in lib.trashed()] == [keep["id"]] and not (lib.root / ".trash" / row["folder"]).exists()
    for bad in (row["folder"], "../library", "x--1", None):
        with pytest.raises(KeyError):
            lib.purge_trashed(bad)
    assert lib.empty_trash() == 1 and lib.trashed() == [] and lib.empty_trash() == 0


def test_a_trashed_character_that_is_restored_leaves_the_trash_list(lib):
    made = lib.create(npc())
    lib.delete(made["id"])
    assert len(lib.trashed()) == 1
    lib.restore(made["id"])
    assert lib.trashed() == []


# ---- earlier versions of a picture --------------------------------------------------------------------------------------
def with_portrait(lib, colour=(200, 50, 50)):
    made = lib.create(npc())
    lib.write_asset(made["id"], "portrait.png", png_bytes(colour=colour))
    lib.write_asset(made["id"], "portrait_raw.png", png_bytes((70, 70), colour))
    lib.update(made["id"], lambda n: n["assets"].update(portrait={"file": "portrait.png", "raw": "portrait_raw.png", "seed": 1, "style": "pixel",
                                                                  "created": "first", "size": [64, 64], "stale": True}))
    return made["id"]


def test_a_picture_that_is_drawn_again_leaves_the_old_one_among_its_earlier_versions(lib):
    npc_id = with_portrait(lib)
    entry = lib.archive_picture(npc_id, "portrait")
    assert entry["file"] == f'candidates/portrait/{entry["id"]}.png' and entry["raw"] == f'candidates/portrait/{entry["id"]}_raw.png'
    assert lib.read_asset(npc_id, entry["file"]) == png_bytes() and lib.read_asset(npc_id, entry["raw"]) == png_bytes((70, 70))
    small = Image.open(io.BytesIO(lib.read_asset(npc_id, entry["thumb"])))
    assert small.mode == "RGBA" and max(small.size) <= 192                              # what the list shows it by
    listed = lib.get(npc_id)["assets"]["candidates"]["portrait"]
    assert [(c["seed"], c["created"], c["size"]) for c in listed] == [(1, "first", [64, 64])] and "replaced" in listed[0]
    assert "stale" not in listed[0] and "candidate_id" not in listed[0]                  # (stale describes the picture in use, not this one)
    assert lib.read_asset(npc_id, "portrait.png") == png_bytes()                          # archiving does not touch the picture in use


def test_nothing_is_kept_when_there_is_no_picture_or_the_history_is_off_or_the_name_is_not_a_picture(lib):
    made = lib.create(npc())
    assert lib.archive_picture(made["id"], "portrait") is None and "candidates" not in lib.get(made["id"])["assets"]
    npc_id = with_portrait(lib)
    assert lib.archive_picture(npc_id, "sprite") is None and lib.archive_picture(npc_id, "model_3d") is None
    assert lib.archive_picture(npc_id, "../portrait") is None and lib.archive_picture(npc_id, "expressions/") is None
    lib.history_keep = 0
    assert lib.archive_picture(npc_id, "portrait") is None and "candidates" not in lib.get(npc_id)["assets"]


def test_only_the_newest_earlier_versions_are_kept_and_the_ones_you_saved_yourself_never_go(lib):
    npc_id = with_portrait(lib)
    lib.history_keep = 3
    lib.write_asset(npc_id, "candidates/portrait/aaaaaaaaaaaa.png", png_bytes())
    lib.update(npc_id, lambda n: n["assets"].setdefault("candidates", {}).setdefault("portrait", []).append(
        {"id": "aaaaaaaaaaaa", "file": "candidates/portrait/aaaaaaaaaaaa.png", "seed": 99}))        # saved on purpose: no 'replaced'
    made = []
    for i in range(5):
        lib.update(npc_id, lambda n, i=i: n["assets"]["portrait"].update(seed=10 + i))
        made.append(lib.archive_picture(npc_id, "portrait"))
    listed = lib.get(npc_id)["assets"]["candidates"]["portrait"]
    assert [c["seed"] for c in listed if c.get("replaced")] == [12, 13, 14]            # the three newest of the five
    assert any(c["id"] == "aaaaaaaaaaaa" for c in listed)
    for gone in made[:2]:
        assert not lib.resolve(npc_id, gone["file"]).exists() and not lib.resolve(npc_id, gone["thumb"]).exists()
    assert lib.resolve(npc_id, "candidates/portrait/aaaaaaaaaaaa.png").exists()


def test_bringing_an_earlier_version_back_swaps_it_with_the_picture_in_use_and_loses_neither(lib):
    npc_id = with_portrait(lib, (200, 50, 50))
    lib.archive_picture(npc_id, "portrait")
    lib.write_asset(npc_id, "portrait.png", png_bytes(colour=(50, 50, 200)))                  # the redraw
    lib.write_asset(npc_id, "portrait_raw.png", png_bytes((70, 70), (50, 50, 200)))
    lib.update(npc_id, lambda n: n["assets"]["portrait"].update(seed=2, created="second"))
    first = lib.get(npc_id)["assets"]["candidates"]["portrait"][0]

    after = lib.select_asset_candidate(npc_id, "portrait", first["id"])
    assert lib.read_asset(npc_id, "portrait.png") == png_bytes() and lib.read_asset(npc_id, "portrait_raw.png") == png_bytes((70, 70))
    assert (after["assets"]["portrait"]["seed"], after["assets"]["portrait"]["candidate_id"]) == (1, first["id"])
    assert "thumb" not in after["assets"]["portrait"] and "replaced" not in after["assets"]["portrait"] and "id" not in after["assets"]["portrait"]
    kept = after["assets"]["candidates"]["portrait"]
    assert [c["seed"] for c in kept] == [1, 2]                                                   # the blue redraw is kept as an earlier version now
    assert lib.read_asset(npc_id, kept[1]["file"]) == png_bytes(colour=(50, 50, 200))
    # and back again: nothing piles up (the picture in use is a copy of a listed version)
    after = lib.select_asset_candidate(npc_id, "portrait", kept[1]["id"])
    assert lib.read_asset(npc_id, "portrait.png") == png_bytes(colour=(50, 50, 200)) and after["assets"]["portrait"]["seed"] == 2
    assert [c["seed"] for c in after["assets"]["candidates"]["portrait"]] == [1, 2]


def test_an_expression_and_a_sheet_view_keep_and_restore_their_earlier_versions_too(lib):
    made = lib.create(npc())
    npc_id = made["id"]
    lib.write_asset(npc_id, "expressions/happy.png", png_bytes(colour=(10, 200, 10)))
    lib.write_asset(npc_id, "sheet/front.png", png_bytes(colour=(10, 10, 200)))
    lib.write_asset(npc_id, "sheet/raw/front.png", png_bytes((80, 80), (10, 10, 200)))
    lib.update(npc_id, lambda n: n["assets"].update(
        expressions={"happy": {"file": "expressions/happy.png", "seed": 5}}, living={"file": "living/living.json"},
        sheet={"file": "sheet/sheet.png", "views": {"front": {"file": "sheet/front.png", "raw": "sheet/raw/front.png", "seed": 7}}},
        model_3d={"file": "model_3d.glb", "engine_id": "trellis2"}))
    happy, front = lib.archive_picture(npc_id, "expressions/happy"), lib.archive_picture(npc_id, "sheet/front")
    assert happy["file"].startswith("candidates/expressions_happy/") and front["file"].startswith("candidates/sheet_front/")
    lib.write_asset(npc_id, "expressions/happy.png", png_bytes(colour=(250, 250, 10)))
    lib.write_asset(npc_id, "sheet/front.png", png_bytes(colour=(250, 10, 250)))

    after = lib.select_asset_candidate(npc_id, "expressions/happy", happy["id"])
    assert lib.read_asset(npc_id, "expressions/happy.png") == png_bytes(colour=(10, 200, 10))
    assert after["assets"]["expressions"]["happy"]["seed"] == 5 and after["assets"]["living"]["stale"] is True
    assert "stale" not in after["assets"]["model_3d"] and "stale" not in after["assets"].get("sheet", {})
    after = lib.select_asset_candidate(npc_id, "sheet/front", front["id"])
    assert lib.read_asset(npc_id, "sheet/front.png") == png_bytes(colour=(10, 10, 200))
    assert lib.read_asset(npc_id, "sheet/raw/front.png") == png_bytes((80, 80), (10, 10, 200))
    assert after["assets"]["sheet"]["views"]["front"]["seed"] == 7 and after["assets"]["sheet"]["stale"] is True
    assert after["assets"]["model_3d"]["stale"] is True                                     # a four-view generator reads the front picture
    assert len(after["assets"]["candidates"]["expressions/happy"]) == 2 and len(after["assets"]["candidates"]["sheet/front"]) == 2
    for bad in ("expressions/", "sheet/../x", "model_3d", "portrait/extra"):
        with pytest.raises(KeyError):
            lib.select_asset_candidate(npc_id, bad, happy["id"])
    with pytest.raises(KeyError):
        lib.select_asset_candidate(npc_id, "portrait", happy["id"])                       # not a version of that picture


def test_a_picture_that_was_deleted_can_be_brought_back_from_its_earlier_versions(lib):
    npc_id = with_portrait(lib)
    entry = lib.archive_picture(npc_id, "portrait")
    lib.delete_asset(npc_id, "portrait")
    assert "portrait" not in lib.get(npc_id)["assets"] and lib.get(npc_id)["assets"]["candidates"]["portrait"][0]["id"] == entry["id"]
    after = lib.select_asset_candidate(npc_id, "portrait", entry["id"])
    assert lib.read_asset(npc_id, "portrait.png") == png_bytes() and after["assets"]["portrait"]["seed"] == 1
    assert len(after["assets"]["candidates"]["portrait"]) == 1                                # nothing was in use to swap out


def test_an_earlier_version_can_be_deleted_for_good_and_the_picture_in_use_stays(lib):
    npc_id = with_portrait(lib)                                                                # red, seed 1
    entry = lib.archive_picture(npc_id, "portrait")
    lib.write_asset(npc_id, "portrait.png", png_bytes(colour=(50, 50, 200)))                  # redrawn: blue, seed 2
    lib.update(npc_id, lambda n: n["assets"]["portrait"].update(seed=2))
    other = lib.archive_picture(npc_id, "portrait")
    lib.write_asset(npc_id, "portrait.png", png_bytes(colour=(50, 200, 50)))                  # redrawn again: green, seed 3
    lib.update(npc_id, lambda n: n["assets"]["portrait"].update(seed=3))
    lib.select_asset_candidate(npc_id, "portrait", entry["id"])                                # the red one is in use again (green is kept)
    after = lib.delete_asset_candidate(npc_id, "portrait", entry["id"])
    assert [c["seed"] for c in after["assets"]["candidates"]["portrait"]] == [2, 3]
    assert "candidate_id" not in after["assets"]["portrait"] and lib.read_asset(npc_id, "portrait.png") == png_bytes()
    for rel in (entry["file"], entry["raw"], entry["thumb"]):
        assert not lib.resolve(npc_id, rel).exists()
    assert lib.resolve(npc_id, other["file"]).exists()
    for c in list(after["assets"]["candidates"]["portrait"]):
        after = lib.delete_asset_candidate(npc_id, "portrait", c["id"])
    assert "candidates" not in after["assets"]                                                 # (an empty list is not left behind)
    with pytest.raises(KeyError):
        lib.delete_asset_candidate(npc_id, "portrait", other["id"])
    with pytest.raises(KeyError):
        lib.delete_asset_candidate(npc_id, "../portrait", other["id"])


def test_deleting_the_whole_model_sheet_takes_its_views_earlier_versions_with_it(lib):
    made = lib.create(npc())
    npc_id = made["id"]
    lib.write_asset(npc_id, "sheet/front.png", png_bytes())
    lib.write_asset(npc_id, "expressions/happy.png", png_bytes())
    lib.update(npc_id, lambda n: n["assets"].update(sheet={"views": {"front": {"file": "sheet/front.png"}}},
                                                    expressions={"happy": {"file": "expressions/happy.png"}}))
    view, expression = lib.archive_picture(npc_id, "sheet/front"), lib.archive_picture(npc_id, "expressions/happy")
    after = lib.delete_asset(npc_id, "sheet")
    assert list(after["assets"]["candidates"]) == ["expressions/happy"] and not lib.resolve(npc_id, view["file"]).exists()
    after = lib.delete_asset(npc_id, "expressions", "happy")                                  # a deleted expression keeps its earlier versions
    assert "happy" not in after["assets"]["expressions"] and lib.resolve(npc_id, expression["file"]).exists()
    assert list(after["assets"]["candidates"]) == ["expressions/happy"]



def test_a_character_is_rated_again_when_the_licence_list_is_edited(lib, tmp_path, monkeypatch):
    """The Library row carries the commercial-use rating, so editing data/licences.json must change it without a restart."""
    import os
    import shutil
    from npcgen import licences

    edited = tmp_path / "licences.json"
    shutil.copy(licences.DATA, edited)
    monkeypatch.setattr(licences, "DATA", edited)
    made = lib.create(npc())
    assert lib.list()[0]["commercial"] == "none"                                          # nothing made yet
    lib.update(made["id"], lambda n: n.setdefault("assets", {}).update(portrait={"file": "portrait.png", "engine": "Z-Image Turbo"}))
    assert lib.list()[0]["commercial"] == "yes"
    data = json.loads(edited.read_text("utf-8"))
    next(m for m in data["models"] if m["id"] == "zimage")["status"] = "no"
    edited.write_text(json.dumps(data), "utf-8")
    later = edited.stat().st_mtime_ns + 5_000_000
    os.utime(edited, ns=(later, later))                                                     # (a coarse clock must not hide the edit)
    assert lib.list()[0]["commercial"] == "no"
    assert lib.list()[0]["id"] == made["id"]


def test_a_licence_list_that_cannot_be_read_does_not_hide_a_character(lib, monkeypatch):
    from npcgen import licences
    made = lib.create(npc())
    monkeypatch.setattr(licences, "verdict", lambda npc: (_ for _ in ()).throw(ValueError("broken list")))
    rows = lib.refresh_index()
    assert [r["id"] for r in rows] == [made["id"]] and rows[0]["commercial"] == "unclear"
