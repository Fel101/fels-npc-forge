"""The glasses maker (npcgen/glasses.py): the recipe's checks, the lens outlines, and the GLB it builds."""
import math

import numpy as np
import pytest

from npcgen import glasses, procmesh as pm
from npcgen.paint3d import Glb, _node_matrix

DATA = glasses.load()
SHAPES = list(DATA["choices"]["shape"])
PRESETS = list(DATA["presets"])


# ---- reading a built GLB back ---------------------------------------------------------------------------------------------------------

def _nodes(glb):
    return {n["name"]: n for n in glb.json["nodes"]}


def _primitives(glb, node_name):
    """[(material name, positions in the glasses' own space (metres, arms under their node transform), triangles, attributes)]"""
    node = _nodes(glb)[node_name]
    world = _node_matrix(node)
    out = []
    for prim in glb.json["meshes"][node["mesh"]]["primitives"]:
        pos = glb.accessor(prim["attributes"]["POSITION"]).astype(float)
        pos = pos @ world[:3, :3].T + world[:3, 3]
        out.append((glb.json["materials"][prim["material"]]["name"], pos, glb.accessor(prim["indices"]).reshape(-1, 3).astype(np.int64), prim["attributes"]))
    return out


def _all_points(glb):
    return np.concatenate([p[1] for name in ("frame", "lenses", "temple_left", "temple_right") for p in _primitives(glb, name)])


def _build(**recipe):
    faces = recipe.pop("faces", 30000)
    data, info = glasses.build(recipe, faces=faces)
    return Glb(data), info


# ---- the data file -------------------------------------------------------------------------------------------------------------------

def test_the_tables_are_complete_and_every_preset_is_a_valid_recipe_inside_the_ranges():
    assert set(DATA["defaults"]) >= set(DATA["fields"]) and DATA["defaults"]["preset"] in DATA["presets"]
    for key, field in DATA["fields"].items():
        assert field["label"] and field["unit"] is not None and field["group"] and field["min"] < field["max"] and field["step"] > 0, key
        assert field["min"] <= DATA["defaults"][key] <= field["max"], key
    for key, options in DATA["choices"].items():
        assert options and all(options.values()), key
    assert set(DATA["materials"]) == set(DATA["choices"]["frame_material"])
    for group in DATA["colours"].values():
        assert all(c["label"] and glasses.HEX.match(c["hex"]) for c in group)
    assert all(0 <= t["opacity"] <= 1 and glasses.HEX.match(t["hex"]) for t in DATA["tints"].values()) and "clear" in DATA["tints"] and "custom" in DATA["tints"]
    for pid, preset in DATA["presets"].items():
        assert preset["label"] and set(preset["recipe"]) <= set(DATA["defaults"]), pid
        clean = glasses.clean_recipe({"preset": pid})
        for key, value in preset["recipe"].items():
            assert clean[key] == (value.lower() if isinstance(value, str) else pytest.approx(value)), (pid, key)
    assert {p["recipe"]["shape"] for p in DATA["presets"].values()} <= set(SHAPES)


def test_the_catalogue_gives_the_editor_what_it_needs():
    cat = glasses.catalogue()
    assert set(cat) == {"defaults", "fields", "choices", "colours", "tints", "mirrors", "material_looks", "presets"}
    assert set(cat["material_looks"]) == set(DATA["choices"]["frame_material"]) and all(set(v) <= set(DATA["defaults"]) for v in cat["material_looks"].values())
    assert [p["id"] for p in cat["presets"]] == PRESETS and cat["presets"][0]["label"] and "shape" in cat["presets"][0]["recipe"]


# ---- the recipe ---------------------------------------------------------------------------------------------------------------------------

def test_an_empty_recipe_is_the_default_preset_and_a_preset_replaces_the_style_but_not_the_lenses():
    plain = glasses.clean_recipe(None)
    assert plain["preset"] == DATA["defaults"]["preset"] and plain["shape"] == "classic" and plain["lens_tint"] == "clear"
    aviator = glasses.clean_recipe({"preset": "aviator", "lens_tint": "green"})
    assert (aviator["shape"], aviator["frame_material"], aviator["pads"], aviator["preset"]) == ("aviator", "metal", True, "aviator")
    assert aviator["lens_tint"] == "green" and aviator["lens_color"] == DATA["tints"]["green"]["hex"] and aviator["lens_opacity"] == DATA["tints"]["green"]["opacity"]
    assert glasses.clean_recipe({"preset": "aviator"})["lens_tint"] == "clear"                  # (a style does not choose the lenses)


def test_what_a_kind_starts_with_comes_between_the_preset_and_the_fields_given():
    base = {"lens_tint": "grey"}
    assert glasses.clean_recipe({}, preset="aviator", base=base)["lens_tint"] == "grey"
    assert glasses.clean_recipe({}, preset="aviator", base=base)["lens_opacity"] == DATA["tints"]["grey"]["opacity"]
    assert glasses.clean_recipe({"lens_tint": "brown"}, preset="aviator", base=base)["lens_tint"] == "brown"
    assert glasses.clean_recipe({"preset": "cat_eye"}, preset="aviator")["shape"] == "cat_eye"      # (the recipe's own preset wins over the kind's)
    assert glasses.clean_recipe({"lens_w": 55.5}, preset="aviator")["lens_w"] == 55.5


def test_a_complete_recipe_is_kept_exactly_and_cleaning_is_idempotent():
    once = glasses.clean_recipe({"preset": "hexagon", "lens_w": 50.5, "frame_color": "#ABC", "lens_gradient": True, "pose": "folded"})
    assert once["frame_color"] == "#aabbcc" and once["lens_gradient"] is True and once["pose"] == "folded"
    assert glasses.clean_recipe(once) == once
    assert list(once) == list(DATA["defaults"])                                                 # (always every field, in one order)


@pytest.mark.parametrize("raw, message", [
    ("not a dict", "must be an object"),
    ({"lenz_w": 3}, "Unknown glasses setting: lenz_w"),
    ({"preset": "nope"}, "Unknown glasses style"),
    ({"lens_w": 10}, "Lens width must be between 36 and 66"),
    ({"lens_w": "wide"}, "Lens width must be a number"),
    ({"lens_w": True}, "Lens width must be a number"),
    ({"lens_w": float("nan")}, "Lens width must be between"),
    ({"temple_drop": 200}, "Bend behind the ear must be between 0 and 85"),
    ({"shape": "star"}, "Unknown shape: star"),
    ({"rim": "gold"}, "Unknown rim"),
    ({"frame_material": "wood"}, "Unknown frame material"),
    ({"pose": "hanging"}, "Unknown pose"),
    ({"lens_tint": "invisible"}, "Unknown lens tint"),
    ({"pads": "yes"}, "pads must be true or false"),
    ({"lens_gradient": 1}, "lens gradient must be true or false"),
    ({"frame_color": "gold-ish"}, "Frame color must be a colour"),
    ({"frame_color": "#12345"}, "Frame color must be a colour"),
    ({"lens_color": 5}, "Lens color must be a colour"),
])
def test_a_bad_recipe_is_refused_with_the_field_that_is_wrong(raw, message):
    with pytest.raises(ValueError, match=message):
        glasses.clean_recipe(raw)


def test_a_colour_can_be_a_swatch_name_and_empty_values_are_left_as_they_were():
    r = glasses.clean_recipe({"frame_color": "burgundy", "lens_color": "mirror_blue", "lens_w": "", "shape": None})
    assert r["frame_color"] == "#6a1f2f" and r["lens_color"] == DATA["mirrors"][1]["hex"] and r["lens_w"] == DATA["defaults"]["lens_w"] and r["shape"] == "classic"
    assert glasses.clean_recipe({"frame_color": "gold"})["frame_color"] == "#f2c75c"


def test_a_custom_tint_keeps_the_colour_it_was_given():
    r = glasses.clean_recipe({"lens_tint": "custom", "lens_color": "#336699", "lens_opacity": 0.4})
    assert (r["lens_tint"], r["lens_color"], r["lens_opacity"]) == ("custom", "#336699", 0.4)


def test_the_detail_follows_the_game_versions_size():
    assert [glasses.detail_for(n) for n in (10_000, 30_000, 100_000, 5_000, 40_000)] == ["light", "standard", "fine", "light", "fine"]


# ---- lens outlines -------------------------------------------------------------------------------------------------------------------------

def _simple(poly):
    """Whether no two edges of a closed polygon cross."""
    a, b = poly, np.roll(poly, -1, 0)
    n = len(poly)

    def side(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
    for i in range(n):
        for j in range(i + 2, n):
            if i == 0 and j == n - 1:
                continue
            if side(a[i], b[i], a[j]) * side(a[i], b[i], b[j]) < 0 and side(a[j], b[j], a[i]) * side(a[j], b[j], b[i]) < 0:
                return False
    return True


@pytest.mark.parametrize("shape", SHAPES)
def test_every_lens_outline_fills_its_box_exactly_is_counter_clockwise_and_does_not_cross_itself(shape):
    pts = glasses.lens_outline(shape, 52.0, 42.0, 96)
    assert pts.shape == (96, 2) and np.isfinite(pts).all()
    assert np.ptp(pts[:, 0]) == pytest.approx(52.0, abs=0.3) and np.ptp(pts[:, 1]) == pytest.approx(42.0, abs=0.3)      # (resampled, so within a hair)
    assert pts.min(0) + pts.max(0) == pytest.approx([0, 0], abs=0.3)                                                   # centred on the lens's middle
    area = 0.5 * np.sum(pts[:, 0] * np.roll(pts[:, 1], -1) - np.roll(pts[:, 0], -1) * pts[:, 1])
    assert area > 0.5 * 52 * 42 * (0.45 if shape == "half_moon" else 0.7) and area < 52 * 42
    assert _simple(pts)


def test_the_lens_shapes_lean_the_way_their_names_say():
    cat = glasses.lens_outline("cat_eye", 54, 43, 200)
    assert cat[np.argmax(cat[:, 1])][0] > 5                                                    # the highest point is on the temple side (+x)
    aviator = glasses.lens_outline("aviator", 58, 52, 200)
    assert aviator[np.argmin(aviator[:, 1])][0] < 5 and np.ptp(aviator[aviator[:, 1] > 10][:, 0]) > np.ptp(aviator[aviator[:, 1] < -15][:, 0])   # wider on top
    moon = glasses.lens_outline("half_moon", 48, 28, 200)
    assert (moon[:, 1] > 13.9).sum() >= 8 and moon[np.argmin(moon[:, 1])][0] == pytest.approx(0, abs=1.0)        # a flat top, the point of the bowl in the middle
    classic = glasses.lens_outline("classic", 52, 42, 200)
    assert classic[np.argmax(classic[:, 1])][0] > 0 and np.ptp(classic[classic[:, 1] > 15][:, 0]) > np.ptp(classic[classic[:, 1] < -15][:, 0])
    assert np.ptp(glasses.lens_outline("round", 46, 46, 120)[:, 0]) == pytest.approx(46, abs=0.3)


def test_an_unknown_shape_is_refused():
    with pytest.raises(ValueError, match="Unknown lens shape"):
        glasses.lens_outline("star", 50, 40, 40)


def test_rounding_corners_leaves_a_dense_curve_alone_and_never_crosses_short_edges():
    square = glasses._rounded_polygon([(-10, -10), (10, -10), (10, 10), (-10, 10)], 4.0)
    assert np.ptp(square[:, 0]) == pytest.approx(20) and _simple(square) and len(square) == 4 * 16
    big = glasses._rounded_polygon([(-10, -10), (10, -10), (10, 10), (-10, 10)], 400.0)       # a radius too big is cut down to the edges
    big = big[np.r_[True, np.linalg.norm(np.diff(big, axis=0), axis=1) > 1e-9]]               # (the arcs meet at the same point: drop the repeats)
    big = big[:-1] if np.linalg.norm(big[0] - big[-1]) < 1e-9 else big
    assert _simple(big) and np.ptp(big[:, 0]) <= 20.0001
    ring = glasses._ellipse_unit(2.0, 200) * 10
    assert len(glasses._rounded_polygon(ring, 3.0)) == 200


def test_the_rim_is_thick_where_the_style_says():
    r = glasses.clean_recipe({"preset": "browline"})
    outline = glasses.lens_outline(r["shape"], r["lens_w"], r["lens_h"], 96)
    w, d = glasses._rim_sizes(r, outline)
    assert w[outline[:, 1] > 15].max() == pytest.approx(r["brow_w"]) and w[outline[:, 1] < -15].max() < 1.8 and (w >= r["rim_w"] - 1e-9).all()
    half = glasses.clean_recipe({"preset": "half_rim"})
    w, d = glasses._rim_sizes(half, glasses.lens_outline(half["shape"], half["lens_w"], half["lens_h"], 96))
    assert w.max() == pytest.approx(half["rim_w"]) and w.min() == pytest.approx(0.5) and d.min() == pytest.approx(0.5)
    assert glasses._rim_sizes(glasses.clean_recipe({"preset": "rimless"}), outline) is None
    full = glasses.clean_recipe({"preset": "classic"})
    assert np.ptp(glasses._rim_sizes(full, outline)[0]) == 0


# ---- the build ---------------------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("preset", PRESETS)
def test_every_preset_builds_a_real_pair_of_glasses(preset):
    glb, info = _build(preset=preset)
    points = _all_points(glb) * 1000
    width, height, depth = np.ptp(points, axis=0)
    assert 100 < width < 175 and 28 < height < 70 and 120 < depth < 200, (width, height, depth)           # an adult's glasses, in millimetres
    assert info["size_m"] == pytest.approx([width / 1000, height / 1000, depth / 1000], abs=1e-4) and info["origin"] == "bridge"
    assert np.isfinite(points).all()
    assert points[:, 0].max() + points[:, 0].min() == pytest.approx(0, abs=0.3)                          # symmetric about the middle of the bridge
    for name in ("frame", "lenses", "temple_left", "temple_right"):
        for material, pos, tri, attrs in _primitives(glb, name):
            mesh = pm.Mesh(pos, np.tile([0.0, 0.0, 1.0], (len(pos), 1)), tri)
            assert mesh.volume() > 0, (preset, name, material)                                         # every part is a closed surface facing out


@pytest.mark.parametrize("faces, detail", [(10_000, "light"), (30_000, "standard"), (100_000, "fine")])
def test_the_detail_picks_how_fine_the_curves_are_and_stays_inside_the_size_asked_for(faces, detail):
    counts = []
    for preset in PRESETS:
        _glb, info = glasses.build({"preset": preset}, faces=faces)
        assert info["detail"] == detail and info["triangles"] <= faces, (preset, info["triangles"])
        counts.append(info["triangles"])
    assert min(counts) > 1000


def test_finer_detail_means_more_triangles_and_the_same_size():
    sizes = {faces: glasses.build({"preset": "classic"}, faces=faces)[1] for faces in (10_000, 30_000, 100_000)}
    assert sizes[10_000]["triangles"] < sizes[30_000]["triangles"] < sizes[100_000]["triangles"]
    assert sizes[10_000]["size_m"] == pytest.approx(sizes[100_000]["size_m"], abs=0.002)


def test_the_same_recipe_always_gives_the_same_bytes():
    assert glasses.build({"preset": "cat_eye", "lens_tint": "rose"})[0] == glasses.build({"preset": "cat_eye", "lens_tint": "rose"})[0]
    assert glasses.build({"preset": "cat_eye"})[0] != glasses.build({"preset": "classic"})[0]


def test_it_is_built_as_nodes_a_game_can_use_with_the_arms_turning_about_their_hinges():
    glb, info = _build(preset="classic")
    nodes = _nodes(glb)
    assert set(nodes) == {"glasses", "frame", "lenses", "temple_left", "temple_right"}
    assert glb.json["scenes"][0]["nodes"] == [glb.json["nodes"].index(nodes["glasses"])] and len(nodes["glasses"]["children"]) == 4
    left, right = nodes["temple_left"]["translation"], nodes["temple_right"]["translation"]
    assert left[0] > 0.05 and right[0] == pytest.approx(-left[0]) and right[1:] == pytest.approx(left[1:]) and left[2] < -0.003      # the hinges, behind the front
    for side in ("temple_left", "temple_right"):                                                   # an arm's own origin is its hinge: it runs back from there
        local = np.concatenate([p[1] for p in _primitives(glb, side)]) - np.array(nodes[side]["translation"])
        assert local[:, 2].max() < 0.002 and local[:, 2].min() < -0.13 and abs(local[:, 0]).max() < 0.02
    assert glb.json["asset"]["extras"]["recipe"] == glasses.clean_recipe({"preset": "classic"}) and glb.json["asset"]["extras"]["attach"] == "Head"
    assert [m["name"] for m in glb.json["materials"]] == ["frame", "hardware", "pads", "lens"]


def test_the_arm_is_as_long_as_asked_and_bends_down_behind_the_ear():
    for length in (120, 145, 165):
        glb, _ = _build(preset="classic", temple_len=length, temple_drop=0)
        local = np.concatenate([p[1] for p in _primitives(glb, "temple_left")]) - np.array(_nodes(glb)["temple_left"]["translation"])
        assert -local[:, 2].min() * 1000 == pytest.approx(length, abs=length * 0.04)             # (hinge to tip along the path)
        assert np.ptp(local[:, 1]) * 1000 < 12
    bent, _ = _build(preset="classic", temple_drop=40)
    local = np.concatenate([p[1] for p in _primitives(bent, "temple_left")]) - np.array(_nodes(bent)["temple_left"]["translation"])
    assert local[:, 1].min() * 1000 < -8                                                          # the tip hangs lower than the hinge


def test_the_pair_wraps_round_the_face_by_the_angle_asked():
    flat, _ = _build(preset="classic", wrap=0)
    bent, _ = _build(preset="classic", wrap=14)
    hinge_flat, hinge_bent = _nodes(flat)["temple_left"]["translation"], _nodes(bent)["temple_left"]["translation"]
    assert hinge_bent[2] < hinge_flat[2] - 0.004 and hinge_bent[0] < hinge_flat[0]               # the sides swing back and in
    lens_flat, lens_bent = _primitives(flat, "lenses")[0][1], _primitives(bent, "lenses")[0][1]
    assert np.ptp(lens_bent[:, 2]) > np.ptp(lens_flat[:, 2]) + 0.008


def test_the_overall_scale_scales_everything():
    one, a = glasses.build({"preset": "round_wire"})
    two, b = glasses.build({"preset": "round_wire", "scale": 2.0})
    assert b["size_m"] == pytest.approx([2 * v for v in a["size_m"]], rel=0.01) and b["triangles"] == a["triangles"]


def test_the_lens_is_clear_tinted_or_a_mirror_and_blends_so_the_two_surfaces_give_the_darkness_asked_for():
    def lens_material(**r):
        glb, _ = _build(**r)
        return next(m for m in glb.json["materials"] if m["name"] == "lens")
    clear = lens_material(preset="classic")
    per_surface = clear["pbrMetallicRoughness"]["baseColorFactor"][3]
    assert clear["alphaMode"] == "BLEND" and 1 - (1 - per_surface) ** 2 == pytest.approx(0.07, abs=0.002)    # (a lens has a front and a back)
    dark = lens_material(preset="classic", lens_tint="dark")
    assert 1 - (1 - dark["pbrMetallicRoughness"]["baseColorFactor"][3]) ** 2 == pytest.approx(0.9, abs=0.002)
    custom = lens_material(preset="classic", lens_tint="custom", lens_color="#ff0000", lens_opacity=0.5)
    assert custom["pbrMetallicRoughness"]["baseColorFactor"][:3] == pytest.approx([1.0, 0.0, 0.0])
    mirror = lens_material(preset="sport", lens_finish="mirror")
    assert mirror["pbrMetallicRoughness"]["metallicFactor"] == 1.0 and mirror["pbrMetallicRoughness"]["baseColorFactor"][3] == 1.0 and "alphaMode" not in mirror


def test_a_gradient_lens_is_darker_at_the_top_through_a_small_texture_every_engine_reads():
    glb, _ = _build(preset="aviator", lens_tint="brown", lens_gradient=True)
    (_name, pos, _tri, attrs), = _primitives(glb, "lenses")
    assert "COLOR_0" not in attrs and "TEXCOORD_0" in attrs                                       # (Godot leaves vertex colours off unless told to use them)
    lens = next(m for m in glb.json["materials"] if m["name"] == "lens")
    index = lens["pbrMetallicRoughness"]["baseColorTexture"]["index"]
    alpha = np.asarray(glb.image(glb.json["textures"][index]["source"]).convert("RGBA"))[:, 0, 3].astype(float) / 255
    assert alpha[0] == pytest.approx(1.0) and alpha[-1] < 0.5 and (np.diff(alpha) <= 1e-9).all()  # darkest at the top row, smoothly lighter down
    uv = glb.accessor(attrs["TEXCOORD_0"])
    assert np.corrcoef(pos[:, 1], uv[:, 1])[0, 1] < -0.99 and uv.min() >= -1e-6 and uv.max() <= 1 + 1e-6      # the top of the lens is the top of the picture
    factor = lens["pbrMetallicRoughness"]["baseColorFactor"][3]
    flat = glasses.build({"preset": "aviator", "lens_tint": "brown"})[0]
    flat_factor = next(m for m in Glb(flat).json["materials"] if m["name"] == "lens")["pbrMetallicRoughness"]["baseColorFactor"][3]
    assert 1 - (1 - factor) ** 2 > 1 - (1 - flat_factor) ** 2 > 1 - (1 - factor * alpha[-1]) ** 2   # top darker than the flat tint, bottom lighter
    plain, _ = _build(preset="aviator", lens_tint="brown")
    assert "TEXCOORD_0" not in _primitives(plain, "lenses")[0][3] and "textures" not in plain.json


def test_the_frames_material_follows_the_recipe():
    def frame(**r):
        glb, _ = _build(**r)
        return {m["name"]: m["pbrMetallicRoughness"] for m in glb.json["materials"]}
    gold = frame(preset="round_wire")
    assert gold["frame"]["metallicFactor"] == 1.0 and gold["hardware"]["baseColorFactor"] == gold["frame"]["baseColorFactor"]
    plastic = frame(preset="classic")
    assert plastic["frame"]["metallicFactor"] == 0.0 and plastic["hardware"]["metallicFactor"] == 1.0 and plastic["hardware"]["baseColorFactor"] != plastic["frame"]["baseColorFactor"]
    matte = frame(preset="sport")
    assert matte["frame"]["roughnessFactor"] > 0.6
    red = frame(preset="classic", frame_color="#ff0000")
    assert red["frame"]["baseColorFactor"][:3] == pytest.approx([1.0, 0.0, 0.0])


def test_nose_pads_are_there_only_when_asked_for():
    with_pads, _ = _build(preset="classic", pads=True)
    without, _ = _build(preset="classic", pads=False)
    names = lambda glb: [m for m, *_ in _primitives(glb, "frame")]                              # noqa: E731
    assert "pads" in names(with_pads) and "pads" not in names(without)
    pad = next(p for p in _primitives(with_pads, "frame") if p[0] == "pads")[1] * 1000
    assert (pad[:, 0] > 0).any() and (pad[:, 0] < 0).any() and pad[:, 2].max() < -3 and abs(pad[:, 0]).max() < 14       # one each side of the nose, behind the lenses


def test_rimless_glasses_have_no_rim_but_screws_through_the_lenses():
    rimless, info = glasses.build({"preset": "rimless"})
    ringed, ring_info = glasses.build({"preset": "rimless", "rim": "full"})
    assert info["triangles"] < ring_info["triangles"] - 2000
    glb = Glb(rimless)
    hardware = next(p for p in _primitives(glb, "frame") if p[0] == "hardware")[1] * 1000
    assert hardware[:, 2].min() < -2.5                                                           # (a screw runs through a lens's thickness)


def test_folded_arms_lie_across_the_back_one_behind_the_other_without_touching():
    openn, a = _build(preset="classic")
    folded, b = _build(preset="classic", pose="folded")
    assert b["size_m"][2] < 0.4 * a["size_m"][2] and b["size_m"][0] == pytest.approx(a["size_m"][0], rel=0.15)
    left = np.concatenate([p[1] for p in _primitives(folded, "temple_left")])
    right = np.concatenate([p[1] for p in _primitives(folded, "temple_right")])
    mid_left, mid_right = left[abs(left[:, 0]) < 0.02], right[abs(right[:, 0]) < 0.02]
    assert len(mid_left) and len(mid_right)
    assert mid_right[:, 2].max() < mid_left[:, 2].min() or mid_left[:, 2].max() < mid_right[:, 2].min()   # (in the middle, one arm passes behind the other)
    assert left[:, 0].min() < -0.05 and right[:, 0].max() > 0.05                                    # each reaches across to the other side


def test_each_part_is_built_with_unit_normals_and_nothing_degenerate():
    glb, _ = _build(preset="aviator", lens_gradient=True)
    for name in ("frame", "lenses", "temple_left", "temple_right"):
        for material, pos, tri, attrs in _primitives(glb, name):
            nrm = glb.accessor(attrs["NORMAL"])
            assert np.allclose(np.linalg.norm(nrm, axis=1), 1.0, atol=1e-4), (name, material)
            area = np.linalg.norm(np.cross(pos[tri[:, 1]] - pos[tri[:, 0]], pos[tri[:, 2]] - pos[tri[:, 0]]), axis=1)
            assert (area > 0).mean() > 0.995 and tri.max() < len(pos)


def test_the_highest_hinge_and_bridge_on_the_shortest_lens_still_build_for_every_shape():
    for shape in SHAPES:
        glb, _ = glasses.build({"shape": shape, "lens_h": 24, "hinge_y": 0.45, "bridge_height": 0.4})
        assert glb[:4] == b"glTF"


def test_a_spread_of_recipes_across_the_ranges_all_build_cleanly():
    rng = np.random.default_rng(20261006)
    fields, choices = DATA["fields"], DATA["choices"]
    for _ in range(60):
        recipe = {key: float(rng.uniform(f["min"], f["max"])) for key, f in fields.items()}
        recipe["lens_opacity"] = float(rng.uniform(0, 1))
        for key in ("shape", "rim", "bridge_style", "frame_material", "lens_finish", "pose"):
            recipe[key] = str(rng.choice(list(choices[key])))
        recipe.update(pads=bool(rng.integers(2)), lens_gradient=bool(rng.integers(2)), lens_tint=str(rng.choice(list(DATA["tints"]))),
                      frame_color="#%06x" % int(rng.integers(0, 0xFFFFFF)), lens_color="#%06x" % int(rng.integers(0, 0xFFFFFF)))
        glb, info = glasses.build(recipe, faces=int(rng.choice([10_000, 30_000, 100_000])))
        parsed = Glb(glb)
        points = _all_points(parsed)
        assert np.isfinite(points).all() and info["triangles"] > 500, recipe
        assert np.ptp(points[:, 0]) > 0.03 and np.ptp(points, axis=0).max() < 0.7, (recipe, np.ptp(points, axis=0))


# ---- reading a description -----------------------------------------------------------------------------------------------------------------

def _described(text, **recipe):
    current = glasses.clean_recipe(recipe or None)
    changes, said = glasses.from_description(text, current)
    return glasses.clean_recipe({**current, **changes}), changes, said


def test_a_description_names_a_style_wire_colours_and_the_lenses():
    r, changes, said = _described("round gold wire glasses with green lenses")
    assert (r["preset"], r["shape"], r["frame_material"], r["frame_color"], r["lens_tint"]) == ("round_wire", "round", "metal", "#f2c75c", "green")
    assert r["lens_color"] == DATA["tints"]["green"]["hex"] and r["lens_opacity"] == DATA["tints"]["green"]["opacity"] and r["pads"] is True
    assert "green lenses" in said and "gold frame" in said and any("round wire" in s for s in said)
    r, _c, said = _described("aviator sunglasses")
    assert (r["preset"], r["shape"], r["lens_tint"], r["bridge_style"]) == ("aviator", "aviator", "grey", "double")
    r, _c, _s = _described("thick black acetate cat-eye glasses with rose-tinted lenses")
    assert (r["shape"], r["frame_material"], r["frame_color"], r["lens_tint"]) == ("cat_eye", "plastic", "#17181b", "rose") and r["rim_w"] == pytest.approx(6.6)


def test_a_colour_next_to_a_lens_word_is_the_lenses_and_any_other_is_the_frame():
    r, _c, _s = _described("black glasses with green lenses")
    assert r["frame_color"] == "#17181b" and r["lens_tint"] == "green"
    r, _c, _s = _described("black lenses in a red frame")
    assert r["lens_tint"] == "dark" and r["frame_color"] == DATA["colours"]["plastic"][11]["hex"]                                   # (the red swatch)
    r, _c, _s = _described("blue tinted glasses")
    assert r["lens_tint"] == "blue" and r["frame_color"] == "#17181b"                                                                # (the frame is left alone)


def test_mirrored_gradient_and_sunglass_words_set_the_lenses():
    r, _c, said = _described("mirrored sports glasses")
    assert (r["preset"], r["lens_finish"], r["lens_tint"], r["frame_material"]) == ("sport", "mirror", "custom", "matte") and r["lens_color"] == DATA["mirrors"][0]["hex"]
    assert "mirrored" in " ".join(said)
    r, _c, _s = _described("blue mirror sunglasses")
    assert r["lens_finish"] == "mirror" and r["lens_color"] == DATA["mirrors"][1]["hex"]
    r, _c, _s = _described("gradient brown lenses")
    assert r["lens_gradient"] is True and r["lens_tint"] == "brown"
    r, _c, _s = _described("clear lenses", lens_tint="grey")
    assert r["lens_tint"] == "clear" and r["lens_opacity"] == DATA["tints"]["clear"]["opacity"]
    assert _described("shades")[0]["lens_tint"] == "grey"


def test_size_words_work_from_the_current_glasses_and_stay_in_range():
    r, _c, _s = _described("oversized glasses", lens_w=50, lens_h=40)
    assert (r["lens_w"], r["lens_h"]) == (56, 45)
    r, _c, _s = _described("big glasses", lens_w=65, lens_h=59)
    assert (r["lens_w"], r["lens_h"]) == (66, 60)                                                                                    # (never past the range)
    assert _described("tiny glasses", lens_w=50, lens_h=40)[0]["lens_w"] == 44 and _described("small glasses", lens_w=38)[0]["lens_w"] == 36
    assert _described("long arms", temple_len=140)[0]["temple_len"] == 150 and _described("short arms", temple_len=112)[0]["temple_len"] == 110
    thin = _described("thin wire frames", rim_w=5.2)[0]
    assert thin["frame_material"] == "metal" and thin["rim_w"] == pytest.approx(1.05) and thin["pads"] is True
    assert _described("chunky glasses")[0]["rim_w"] == 8.2                                                                           # (the chunky style, not thicker still)


def test_changing_to_wire_or_plastic_brings_the_rim_arms_and_pads_that_go_with_it():
    wire = _described("silver wire glasses", preset="classic")[0]
    assert (wire["frame_material"], wire["rim_w"], wire["temple_h"], wire["pads"], wire["frame_color"]) == ("metal", 1.5, 2.0, True, "#dcdfe2")
    plastic = _described("plastic glasses", preset="round_wire")[0]
    assert (plastic["frame_material"], plastic["rim_w"], plastic["pads"]) == ("plastic", 5.0, False)
    same = _described("plastic glasses")[0]                                                                                          # (already plastic: nothing else moves)
    assert same == glasses.clean_recipe(None)


def test_pads_pose_rims_and_hyphens_and_case_are_understood():
    assert _described("no nose pads", preset="round_wire")[0]["pads"] is False and _described("with nose pads")[0]["pads"] is True
    assert _described("padless", preset="aviator")[0]["pads"] is False
    r, _c, said = _described("FOLDED Half-Rim glasses with nose pads")
    assert (r["preset"], r["pose"], r["pads"]) == ("half_rim", "folded", True) and "folded" in said
    assert _described("a rimless pair")[0]["rim"] == "rimless" and _described("browline")[0]["rim"] == "browline"
    assert _described("hexagonal")[0]["shape"] == "hexagon" and _described("square frames")[0]["shape"] == "square"
    assert _described("a squared off look")[0]["shape"] == "classic"                                                                 # ("squared" is not "square")


def test_words_it_does_not_know_change_nothing_and_an_empty_description_is_fine():
    for text in ("", None, "asdf qwerty", "a very nice pair for my grandmother"):
        changes, said = glasses.from_description(text)
        assert changes == {} and said == []
    changes, said = glasses.from_description("aviator", {"lens_w": 60, "lens_tint": "brown"})
    assert changes["lens_w"] == 58 and "lens_tint" not in changes                                                                    # (a style leaves the lenses alone)


def test_everything_a_description_says_is_a_valid_recipe():
    for text in ("round gold wire glasses with green lenses", "thick black cat-eye", "huge chunky pink frames with mirrored lenses", "tiny thin silver half-rim",
                 "big oversized huge", "folded rimless shades", "oval brass wire frames, amber lenses, long arms", "octagonal gunmetal glasses, tinted purple"):
        r, changes, said = _described(text)
        assert said and glasses.clean_recipe(r) == r, text
        glasses.build(r)
