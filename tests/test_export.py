import io
import json

import numpy as np
import pytest
from PIL import Image, ImageDraw

from npcgen import export
from npcgen.library import Library

DIRS = ("down", "left", "right", "up")


def frame_png(i, canvas=512):
    """A coloured figure that shifts a little with the frame number, on a transparent working-resolution canvas."""
    im = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    x = 200 + 10 * i
    d.rectangle([x, 60, x + 110, 300], fill=(30, 110, 60, 255))
    d.ellipse([x + 15, 20, x + 95, 100], fill=(200, 60, 40, 255))
    d.rectangle([x + 20, 300, x + 90, 480], fill=(40, 40, 120, 255))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def _walk_npc(tmp_path, style="pixel"):
    lib = Library(tmp_path / "library")
    npc = lib.create({"setting": "modern", "style": style, "seed": 1,
                      "profile": {"first_name": "Maya", "last_name": "Okafor", "age": 30, "gender": "female", "occupation": "barista"},
                      "appearance": {}})
    files = {d: [] for d in DIRS}
    for d in DIRS:
        for i in range(4):
            rel = f"animations/walk/{d}_{i}.png"
            lib.write_asset(npc["id"], rel, frame_png(i))
            files[d].append(rel)
    meta = {"kind": "generated", "label": "Walk cycle", "fps": 8, "loop": True, "frames": 4, "canvas": 512, "created": "2026-09-18",
            "order": list(DIRS), "files": files, "mirrored": {"left": "right"},
            "directions": {"down": {"seed": 11}, "right": {"seed": 22}, "up": {"seed": 33}}}
    lib.update(npc["id"], lambda n: n["assets"].update(animations={"walk": meta}))
    return lib, lib.get(npc["id"])


@pytest.fixture()
def world(tmp_path):
    return _walk_npc(tmp_path)


def test_grid4_preset_lays_out_rows_down_left_right_up(world):
    lib, npc = world
    files = export.build(lib, npc, "walk", "grid4")
    sheet = Image.open(io.BytesIO(files["hero-walk.png"]))
    assert sheet.size == (4 * 256, 4 * 256)                                            # 4 frames x 4 rows of 256px
    atlas = json.loads(files["hero-walk.json"])
    rows = [a["y"] // 256 for a in atlas["animations"].values()]
    assert list(atlas["animations"]) == ["walk_down", "walk_left", "walk_right", "walk_up"] and rows == [0, 1, 2, 3]
    assert atlas["frame"] == [256, 256] and atlas["anchor"] == [128, 240]
    alpha = np.asarray(sheet)[..., 3]
    for row in range(4):
        for col in range(4):
            assert alpha[row * 256:(row + 1) * 256, col * 256:(col + 1) * 256].any()      # every cell holds a figure


def test_grid4_needs_exactly_four_frames(world):
    lib, npc = world
    lib.update(npc["id"], lambda n: n["assets"]["animations"]["walk"].update(frames=8))
    with pytest.raises(ValueError, match="needs 4 frames"):
        export.build(lib, lib.get(npc["id"]), "walk", "grid4")


def test_atlas_preset_records_provenance_and_a_preview_page(world):
    lib, npc = world
    files = export.build(lib, npc, "walk", "atlas", {"frame": 128})
    assert set(files) == {"maya-okafor_walk.png", "maya-okafor_walk.json", "preview.html"}
    atlas = json.loads(files["maya-okafor_walk.json"])
    assert atlas["provenance"]["model"].startswith("Mage-Flow-Edit") and atlas["provenance"]["seeds"] == {"down": 11, "right": 22, "up": 33}
    assert atlas["animations"]["walk_right"]["rects"][2] == [2 * 128, 2 * 128, 128, 128]
    page = files["preview.html"].decode()
    assert "Maya Okafor" in page and "walk_up" in page and "maya-okafor_walk.png" in page


def test_godot_preset_writes_a_godot_4_spriteframes_resource(world):
    lib, npc = world
    files = export.build(lib, npc, "walk", "godot", {"frame": 48, "dirs": ["right"], "name": "maya", "res_path": "res://assets/units"})
    assert set(files) == {"maya_walk.png", "maya_walk.json", "maya.tres", "preview.html"}          # one direction: name is just 'walk'
    tres = files["maya.tres"].decode()
    assert tres.startswith('[gd_resource type="SpriteFrames" load_steps=6 format=3]')             # 1 texture + 4 regions + the resource
    assert '[ext_resource type="Texture2D" path="res://assets/units/maya_walk.png" id="tex_walk"]' in tres
    assert 'region = Rect2(96, 0, 48, 48)' in tres and '"name": &"walk"' in tres and '"speed": 8.0' in tres and '"loop": true' in tres
    assert Image.open(io.BytesIO(files["maya_walk.png"])).size == (4 * 48, 48)

    multi = export.build(lib, npc, "walk", "godot", {"frame": 32, "dirs": ["down", "up"], "name": "maya"})
    assert {"maya_walk_down.png", "maya_walk_up.png"} <= set(multi) and '&"walk_down"' in multi["maya.tres"].decode()


def test_unity_preset_has_atlas_rects_animation_order_timing_and_provenance(world):
    lib, npc = world
    files = export.build(lib, npc, "walk", "unity", {"frame": 64})
    metadata = json.loads(files["maya-okafor_walk.json"])
    assert metadata["format"] == "NPCForgeUnityAtlas"
    assert metadata["texture"] == "maya-okafor_walk.png" and metadata["frameSize"] == {"width": 64, "height": 64}
    assert metadata["textureSize"] == {"width": 256, "height": 256} and metadata["rectOrigin"] == "bottom-left"
    assert metadata["animations"][0] == {"name": "walk_down", "direction": "down",
                                        "frames": [f"walk_down_{i:03d}" for i in range(4)], "frameRate": 8, "loop": True}
    assert metadata["sprites"][4]["rect"] == {"x": 0, "y": 128, "width": 64, "height": 64}
    assert metadata["provenance"]["seeds"] == {"down": 11, "right": 22, "up": 33}
    assert Image.open(io.BytesIO(files["maya-okafor_walk.png"])).size == (4 * 64, 4 * 64)


def test_aseprite_preset_writes_hash_frames_tags_timing_and_provenance(world):
    lib, npc = world
    files = export.build(lib, npc, "walk", "aseprite", {"frame": 32})
    data = json.loads(files["maya-okafor_walk.json"])
    assert len(data["frames"]) == 16
    assert data["frames"]["frame_0000"]["frame"] == {"x": 0, "y": 0, "w": 32, "h": 32}
    assert data["frames"]["frame_0000"]["duration"] == 125
    assert [(tag["name"], tag["from"], tag["to"]) for tag in data["meta"]["frameTags"]] == [
        (f"walk_{direction}", i * 4, i * 4 + 3) for i, direction in enumerate(DIRS)]
    assert data["meta"]["size"] == {"w": 128, "h": 128}
    assert data["meta"]["npcforge"]["animations"][0] == {"name": "walk_down", "direction": "down", "frames": 4, "fps": 8, "loop": True}
    assert data["meta"]["npcforge"]["provenance"]["npc_id"] == npc["id"]
    assert Image.open(io.BytesIO(files["maya-okafor_walk.png"])).size == (128, 128)


def test_small_pixel_art_frames_are_snapped_to_a_palette_with_hard_alpha(world):
    lib, npc = world
    sheet = Image.open(io.BytesIO(export.build(lib, npc, "walk", "atlas", {"frame": 48, "colors": 6})["maya-okafor_walk.png"]))
    a = np.asarray(sheet)
    assert set(np.unique(a[..., 3])) <= {0, 255}
    assert len({tuple(p) for p in a[a[..., 3] == 255][:, :3]}) <= 6


@pytest.mark.parametrize("style,auto", [("pixel", True), ("pixel8", True), ("voxel", False), ("semi_real", False)])
def test_small_frames_auto_pixelate_only_for_the_pixel_art_styles(tmp_path, style, auto):
    """8-bit pixel art is as much a pixel-grid style as the 16-bit one and must get the same auto-pixelate export
    treatment; a blocky-but-not-pixel style like voxel must not."""
    lib, npc = _walk_npc(tmp_path, style)
    sheet = Image.open(io.BytesIO(export.build(lib, npc, "walk", "atlas", {"frame": 48, "colors": 6})["maya-okafor_walk.png"]))
    colours = len({tuple(p) for p in np.asarray(sheet)[np.asarray(sheet)[..., 3] == 255][:, :3]})
    assert (colours <= 6) == auto, (style, colours)


def test_pixelate_can_add_a_dark_outline_around_the_figure():
    im = Image.new("RGBA", (16, 16), (0, 0, 0, 0))
    ImageDraw.Draw(im).rectangle([5, 5, 10, 10], fill=(200, 200, 60, 255))
    plain, outlined = export.pixelate(im, 4), export.pixelate(im, 4, outline=True)
    assert (np.asarray(outlined)[..., 3] > 0).sum() > (np.asarray(plain)[..., 3] > 0).sum()


def test_clear_errors_for_unknown_presets_clips_and_directions(world):
    lib, npc = world
    with pytest.raises(ValueError, match="Unknown export preset"):
        export.build(lib, npc, "walk", "unrecognized")
    with pytest.raises(ValueError, match="no finished 'run'"):
        export.build(lib, npc, "run", "atlas")
    lib.update(npc["id"], lambda n: n["assets"]["animations"]["walk"]["files"].pop("up"))
    with pytest.raises(ValueError, match="no frames for: up"):
        export.build(lib, lib.get(npc["id"]), "walk", "grid4")


def test_zip_round_trip(world):
    import zipfile
    lib, npc = world
    files = export.build(lib, npc, "walk", "atlas")
    with zipfile.ZipFile(io.BytesIO(export.to_zip(files))) as z:
        assert set(z.namelist()) == set(files)


def test_export_names_cannot_escape_or_inject_preview_markup(world, tmp_path):
    lib, npc = world
    files = export.build(lib, npc, "walk", "atlas", {"name": "../</script><script>alert(1)</script>"})
    assert all("/" not in name and "\\" not in name and ".." not in name for name in files)
    assert b"</script><script>" not in files["preview.html"]
    with pytest.raises(ValueError, match="Unsafe export"):
        export.to_zip({"../escape.txt": b"nope"})
    with pytest.raises(ValueError, match="Unsafe export"):
        export.write_dir({"../escape.txt": b"nope"}, tmp_path / "out")
    assert not (tmp_path / "escape.txt").exists()


def test_godot_resource_path_cannot_inject_properties(world):
    lib, npc = world
    with pytest.raises(ValueError, match="safe res://"):
        export.build(lib, npc, "walk", "godot", {"res_path": 'res://sprites"\nscript = evil'})
