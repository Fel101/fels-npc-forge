"""What your PC can make (npcgen/hardware.py): the tiers from the measured table, and the engines each tier starts with."""
import pytest

from npcgen import hardware, service

GB = 1024


def run(vram_gb, status="done", shared_gb=0.0, seconds=30):
    return {"status": status, "vram_mb": int(vram_gb * GB), "shared_mb": int(shared_gb * GB), "seconds": seconds}


TABLE = {"pipelines": [
    {"id": "portrait-zimage", "what": "Portraits with Z-Image Turbo", "engine": "image:zimage", "default": True,
     "runs": {"32": run(28.5), "24": run(22.0), "16": run(14.8)}},
    {"id": "portrait-qwen2512", "what": "Portraits with Qwen-Image 2512", "engine": "image:qwen2512", "default": True,
     "runs": {"32": run(30.8), "24": run(22.5, shared_gb=4.7, seconds=42), "16": run(15.0, shared_gb=6.0, seconds=200)}},
    {"id": "expressions-qwen2511", "what": "Expressions with Qwen-Image-Edit 2511", "engine": "edit:qwen2511", "default": True,
     "runs": {"32": run(30.0), "24": run(23.0), "16": run(0, status="error")}},
    {"id": "sheet-qwen2511", "what": "Model sheets", "engine": "edit:qwen2511",
     "runs": {"32": run(30.0, seconds=100), "24": run(23.0, seconds=400)}},
    {"id": "3d-hunyuan21", "what": "3D models with Hunyuan3D 2.1", "engine": "3d:hunyuan21", "runs": {"32": run(12.0)}},
]}


@pytest.fixture(autouse=True)
def measured(monkeypatch):
    monkeypatch.setattr(hardware, "load", lambda: TABLE)
    monkeypatch.setattr(hardware, "_cache", {})


@pytest.mark.parametrize("total_mb, tier", [(32624, "32"), (31500, "32"), (30000, "24"), (24564, "24"), (16368, "16"), (15000, "16"),
                                            (12288, "none"), (0, None), (None, None)])
def test_a_card_is_in_the_tier_of_its_size(total_mb, tier):
    assert hardware.tier_for(total_mb) == tier


def test_a_pipeline_fits_where_it_ran_or_where_its_full_card_peak_fits_and_is_slow_where_it_crawled():
    zimage, qwen, edit, sheet, hunyuan = TABLE["pipelines"]
    assert hardware.fit(zimage, "16") == "yes" and hardware.fit(qwen, "24") == "yes"     # 42 s against 30 s is fine
    assert hardware.fit(sheet, "24") == "slow" and hardware.slower(sheet, "24") == 4.0   # it finished, four times slower
    assert hardware.fit(qwen, "16") == "slow"                 # 200 s against 30 s
    assert hardware.fit(edit, "16") is None                   # it failed there
    assert hardware.fit(hunyuan, "16") == "yes"               # never run at 16 GB: its 12 GB peak fits
    assert hardware.fit(zimage, "none") is None
    assert hardware.fits(zimage, "16") and not hardware.fits(qwen, "16") and not hardware.fits(edit, "16")


def test_each_tier_starts_with_the_best_engines_that_fit_it():
    assert hardware.tier_summary("32")["defaults"] == {"image_model": "qwen2512", "edit_model": "qwen2511"}
    assert hardware.tier_summary("24")["defaults"] == {"image_model": "qwen2512", "edit_model": "qwen2511"}
    small = hardware.tier_summary("16")
    assert small["defaults"] == {"image_model": "zimage"}
    assert "Portraits with Z-Image Turbo" in small["makes"] and "Expressions with Qwen-Image-Edit 2511" not in small["makes"]
    assert small["slow"] == [{"what": "Portraits with Qwen-Image 2512", "times": 6.7}]
    assert hardware.tier_summary("24")["slow"] == [{"what": "Model sheets", "times": 4.0}]
    assert small["makes"][:len(hardware.NO_CARD)] == hardware.NO_CARD
    assert hardware.tier_summary("32")["slow"] == []
    nothing = hardware.tier_summary("none")
    assert nothing["makes"] == hardware.NO_CARD and nothing["defaults"] == {} and nothing["needs_switch"] is None


def test_a_3d_model_runs_no_better_than_the_pictures_it_is_made_from(monkeypatch):
    zimage, qwen, edit, sheet, hunyuan = TABLE["pipelines"]
    trellis = {"id": "3d-trellis2", "what": "3D models with Trellis2", "engine": "3d:trellis2", "needs": ["sheet-qwen2511"],
               "runs": {"32": run(16.6)}}                                    # its own peak fits a 24 GB card
    rows = [zimage, qwen, edit, {**sheet, "id": "sheet-qwen2511"}, hunyuan, trellis]
    monkeypatch.setattr(hardware, "load", lambda: {"pipelines": rows})
    assert hardware.fit(trellis, "32") == "yes"
    assert hardware.fit(trellis, "24") == "slow"                              # the sheet it starts from crawls there
    assert hardware.fit(trellis, "16") is None                                # and is not made at all
    assert {"what": "3D models with Trellis2", "times": 4.0} in hardware.tier_summary("24")["slow"]


def test_where_nothing_runs_at_its_usual_speed_the_best_slow_engine_is_the_default(monkeypatch):
    zimage, qwen, *rest = TABLE["pipelines"]
    failed = {**zimage, "runs": {**zimage["runs"], "16": run(0, status="error")}}
    monkeypatch.setattr(hardware, "load", lambda: {"pipelines": [failed, qwen, *rest]})
    assert hardware.tier_summary("16")["defaults"] == {"image_model": "qwen2512"}         # slow, but it finishes


def test_the_box_says_when_the_switch_is_likely_needed():
    full = hardware.tier_summary("32")                   # Qwen ran on a 24 GB card: 23 GB + a voice and a chat model fill 32
    assert full["needs_switch"] is True and full["need_mb"] == 24 * GB - hardware.HEADROOM_MB
    small = hardware.tier_summary("16")                  # Z-Image on a 16 GB card: no room for anything beside it
    assert small["needs_switch"] is True and small["need_mb"] == 16 * GB - hardware.HEADROOM_MB
    assert small["need_engine"] == "image:zimage" and small["whole_card"] and full["whole_card"] is False


def test_the_switch_line_names_the_engine_that_needs_the_most_room(monkeypatch):
    zimage, qwen, edit, *rest = TABLE["pipelines"]
    slow_edit = {**edit, "runs": {**edit["runs"], "24": run(23.0, seconds=200)}}          # it crawls on a 24 GB card
    monkeypatch.setattr(hardware, "load", lambda: {"pipelines": [zimage, qwen, slow_edit, *rest]})
    full = hardware.tier_summary("32")
    assert full["need_engine"] == "edit:qwen2511" and full["need_mb"] == 32 * GB - hardware.HEADROOM_MB


def test_a_big_engine_starts_with_what_the_smallest_card_it_ran_on_leaves_free(monkeypatch):
    from npcgen import engines

    TABLE["pipelines"][0]["runs"]["16"]["ram_private_mb"] = 11000
    assert hardware.start_needs("image:zimage") == (11000 + hardware.RAM_MARGIN_MB, 16 * GB - hardware.HEADROOM_MB - hardware.VRAM_MARGIN_MB)
    assert hardware.start_needs("image:qwen2512")[1] == 16 * GB - hardware.HEADROOM_MB - hardware.VRAM_MARGIN_MB   # slow there
    assert hardware.start_needs("edit:qwen2511")[1] == 24 * GB - hardware.HEADROOM_MB - hardware.VRAM_MARGIN_MB
    assert hardware.start_needs("image:qwen21") is None
    assert engines.start_needs(engines.IMAGE_ENGINES["zimage"])[1] == 16 * GB - hardware.HEADROOM_MB - hardware.VRAM_MARGIN_MB
    qwen21 = engines.IMAGE_ENGINES["qwen21"]
    assert engines.start_needs(qwen21) == (qwen21["min_free_ram_mb"], qwen21["min_free_vram_mb"])   # not measured: its own
    monkeypatch.setenv("NPCGEN_MEASURE", "1")
    assert engines.start_needs(qwen21) == (0, 0)


def test_a_request_that_names_no_engine_gets_the_tier_of_the_card_comfyui_reported():
    assert service.default_options({})["image_model"] == "qwen2512"          # no card read yet: the built-in defaults
    hardware.remember(16368, "AMD Radeon RX 9060 XT")
    assert hardware.your_pc()["tier"] == "16"
    options = service.default_options({})
    assert options["image_model"] == "zimage" and options["edit_model"] == "qwen2511"
    assert service.default_options({"options": {"image_model": "mage"}})["image_model"] == "mage"   # config.json still wins


def test_the_shipped_table_starts_a_16_gb_card_with_z_image_and_a_32_gb_card_with_the_qwen_pair():
    import json
    shipped = json.loads(hardware.DATA.read_text("utf-8"))
    assert hardware.tier_summary("16", shipped)["defaults"]["image_model"] == "zimage"
    assert hardware.tier_summary("32", shipped)["defaults"] == {"image_model": "qwen2512", "edit_model": "qwen2511"}
    assert "Portraits with Z-Image Turbo" in hardware.tier_summary("16", shipped)["makes"]
