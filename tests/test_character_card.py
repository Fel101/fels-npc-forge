"""Round-trip PNG with Altera's released reader and resolve CHARX by Suite Contract 1."""

import base64
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import zipfile

from PIL import Image, PngImagePlugin
import pytest

from npcgen import __version__, character_card, engines, roll
from npcgen.library import Library


# Verbatim MIT-licensed Altera 447fa11 core/charcard.py. See the root LICENSE (same copyright holder).
_spec = importlib.util.spec_from_file_location("altera_charcard", Path(__file__).parent / "fixtures" / "altera_charcard.py")
altera_reader = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(altera_reader)


def png(colour):
    output = io.BytesIO()
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("private", "original PNG text")
    Image.new("RGB", (12, 16), colour).save(output, "PNG", pnginfo=metadata)
    return output.getvalue()


@pytest.fixture
def character(tmp_path):
    lib = Library(tmp_path / "library")
    draft = roll.roll_npc(roll.load_data(), "modern", seed=42)
    draft.update(custom={"secret": "private extension data"}, project="private project")
    draft["profile"].update(first_name="Léa", last_name="Kaur", occupation="mechanic", bio="She fixes old clocks.",
                            speech="Clipped and precise", demeanor="Wry but patient")
    npc = lib.create(draft)
    assets = npc["assets"]
    assets["portrait"] = {"file": "portrait.png", "engine": engines.IMAGE_ENGINES["zimage"]["label"], "created": "portrait-version"}
    assets["sprite"] = {"file": "sprite.png", "engine": "Unlisted model"}
    assets["expressions"] = {key: {"file": f"expressions/{key}.png", "created": key,
                                  "engine": engines.EDIT_ENGINES["qwen2511"]["label"]} for key in ("happy", "sad", "blink", "talking")}
    assets["expressions"]["sad"]["stale"] = True
    assets["photo"] = {"file": "personal-photo.png"}
    assets["living"] = {"file": "living/living.json", "frames": ["blink", "talking"]}
    npc["dialogue"] = {"lines": {"greeting": [{"text": "[laugh] Come in, mind the gears."}], "idle": [{"text": "One clock at a time."}]}}
    red = png("red")
    lib.write_asset(npc["id"], "portrait.png", red)
    lib.write_asset(npc["id"], "sprite.png", png("pink"))
    lib.write_asset(npc["id"], "expressions/happy.png", png("blue"))
    lib.write_asset(npc["id"], "personal-photo.png", png("black"))
    frames = {key: {"file": f"living/{key}.png", "source_created": key} for key in ("blink", "talking")}
    for frame in frames.values():
        lib.write_asset(npc["id"], frame["file"], png("blue"))
    manifest = {"version": 1, "size": [12, 16], "portrait": {"sha256": hashlib.sha256(red).hexdigest()}, "frames": frames}
    lib.write_asset(npc["id"], "living/living.json", json.dumps(manifest).encode())
    return lib, npc


def _read_charx(data):
    assert len(data) <= 100 * 1024 * 1024
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        rows = archive.infolist()
        assert len(rows) <= 500 and all(row.file_size <= 20 * 1024 * 1024 for row in rows)
        assert len({row.filename for row in rows}) == len(rows)
        assert all(row.filename.isascii() and ".." not in row.filename.split("/") and not row.flag_bits & 1 for row in rows)
        card = json.loads(archive.read("card.json"))
        assert card["spec"] == "chara_card_v3" and card["spec_version"] == "3.0"
        assets = card["data"]["assets"]
        assert all(asset["uri"].startswith("embeded://assets/") for asset in assets)
        members = {asset["uri"][10:]: archive.read(asset["uri"][10:]) for asset in assets}
        assert set(members) == set(archive.namelist()) - {"card.json"}
        assert all(asset["uri"].endswith("." + asset["ext"]) for asset in assets)
    return card["data"], members


def test_png_is_v3_with_v2_fallback_in_alteras_reader(character):
    lib, npc = character
    npc["voice"] = {"engine": "kokoro", "mode": "stock", "stock_voice": "af_heart"}
    data = character_card.build(lib, npc, "png")
    normalized, clean = altera_reader.parse_png(data)
    assert normalized["name"] == "Léa Kaur" and normalized["spec"] == "v3"
    assert normalized["first_mes"] == "Come in, mind the gears."
    assert "Clipped and precise" in normalized["personality"] and "Wry but patient" in normalized["personality"]
    assert "{{char}}: One clock at a time." in normalized["mes_example"]
    assert Image.open(io.BytesIO(clean)).getpixel((0, 0)) == (255, 0, 0)
    chunks = list(altera_reader._chunks(data))
    texts = {body.split(b"\0", 1)[0]: body.split(b"\0", 1)[1] for kind, body, _ in chunks if kind == b"tEXt"}
    assert set(texts) == {b"ccv3", b"chara"}
    v3 = json.loads(base64.b64decode(texts[b"ccv3"]))["data"]
    fels = v3["extensions"]["fels"]
    assert fels["sheet"] is None and fels["living_portrait"] is None
    assert fels["voice"] == {"model": "kokoro", "name": "af_heart", "preset": "af_heart", "file": None, "cloned_from_person": False}
    assert fels["licence"]["rating"] == "commercial_ok"
    fallback = b"\x89PNG\r\n\x1a\n" + b"".join(whole for kind, body, whole in chunks
                                            if not (kind == b"tEXt" and body.startswith(b"ccv3\0")))
    old, _ = altera_reader.parse_png(fallback)
    assert old["spec"] == "v2" and "V2 copy" in old["creator_notes"] and old["name"] == normalized["name"]


def test_charx_lists_every_asset_and_links_living_frames_to_exact_portrait(character):
    lib, npc = character
    card, files = _read_charx(character_card.build(lib, npc, "charx"))
    assert [(a["type"], a["name"]) for a in card["assets"] if a["type"] != "other"] == [("icon", "main"), ("emotion", "joy")]
    fels = card["extensions"]["fels"]
    assert fels["ai_generated"] and fels["contract"] == 1
    assert fels["made_by"] == {"app": "npc-forge", "id": npc["id"], "version": __version__}
    assert fels["licence"]["rating"] == "not_cleared" and "Unlisted model" in fels["licence"]["unknown"][0]
    assert any("living portrait" in model["used_for"] for model in fels["licence"]["models"])
    assert "Made with Fel's NPC Forge" in card["creator_notes"]
    assert files["assets/other/images/sprite.png"] == lib.read_asset(npc["id"], "sprite.png")
    sheet = json.loads(files[fels["sheet"][10:]])
    assert sheet["profile"]["occupation"] == "mechanic" and set(sheet) <= {"id", "setting", "style", "freeform", "morphology", "profile", "appearance"}
    assert all("personal-photo" not in path for path in files)
    manifest = json.loads(files[fels["living_portrait"]["manifest"][10:]])
    assert manifest["portrait"]["sha256"] == fels["living_portrait"]["portrait_sha256"] == hashlib.sha256(files["assets/icon/images/main.png"]).hexdigest()
    assert all(frame["file"] in files for frame in manifest["frames"].values())
    lib.write_asset(npc["id"], "portrait.png", png("green"))
    card, files = _read_charx(character_card.build(lib, npc, "charx"))
    assert card["extensions"]["fels"]["living_portrait"] is None and not any("/living/" in path for path in files)


def test_legacy_and_registered_adult_settings_cannot_export(character, tmp_path, monkeypatch):
    lib, npc = character
    (tmp_path / "adult-extra.json").write_text("{}")
    monkeypatch.setattr(roll, "EXTRA_SETTINGS", [(tmp_path, lambda: False, True)])
    for setting in ("models", "adult-extra"):
        with pytest.raises(ValueError, match="core settings"):
            character_card.build(lib, {**npc, "setting": setting}, "png")


@pytest.mark.parametrize("source", ["designed", "recording"])
def test_persona_is_the_lab_file_and_real_recordings_need_explicit_permission(character, monkeypatch, source):
    lib, npc = character
    identity = "a" * 32
    selected = {"name": "Léa", "kind": "cast", "model": "turbo", "identity_id": identity, "lab_id": "lab",
                "cloned_from_person": source == "recording"}
    npc["voice"] = {"lab_voice": selected}
    lab_calls = []
    output = io.BytesIO()
    voice = {"kind": "reference", "id": identity, "sample": {"source_type": source}, "audition": {"asset": "audition.wav"}}
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("manifest.json", json.dumps({"schema": "fels-voice-portable-v1", "kind": "persona", "contents": {"voice": voice}}))
    persona = output.getvalue()

    class Lab:
        health = {"lab_id": "lab"}

        def voice(self, identity_id):
            assert identity_id == identity
            return {"name": "Léa on Voice Lab", "licence": {"rating": "conditions", "reason": "Lab's real terms"}}

        def voice_file(self, identity_id, text, *, permission):
            lab_calls.append((identity_id, text, permission))
            return persona

    monkeypatch.setattr(character_card.LabClient, "discover", Lab)
    character_card.build(lib, npc, "png")
    assert not lab_calls
    if source == "recording":
        card, files = _read_charx(character_card.build(lib, npc, "charx"))
        assert card["extensions"]["fels"]["voice"] is None and character_card.PERSONA not in files and not lab_calls
        # Even if the sheet believed this was a design, the archive's provenance takes precedence.
        selected["cloned_from_person"] = False
        with pytest.raises(character_card.LabError, match="permission|Confirm"):
            character_card.build(lib, npc, "charx")
        lab_calls.clear()
    card, files = _read_charx(character_card.build(lib, npc, "charx", permission=source == "recording"))
    assert lab_calls == [(identity, "Come in, mind the gears.", source == "recording")]
    fels = card["extensions"]["fels"]
    assert fels["voice"]["name"] == "Léa on Voice Lab" and fels["voice"]["cloned_from_person"] == (source == "recording")
    assert files[fels["voice"]["file"][10:]] == persona
    assert any(row.get("reason") == "Lab's real terms" for row in fels["licence"]["models"])
