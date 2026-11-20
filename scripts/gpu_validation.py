"""Repeatable draft GPU samples for the visual-validation roadmap.

This uses the configured ComfyUI directly and writes only to the requested
output directory. Review the PNGs by eye; successful graph execution is not a
visual pass. Example: python scripts/gpu_validation.py matrix --out qa-output
"""
import argparse
import io
import json
import pathlib
import sys

from PIL import Image, ImageDraw

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from npcgen import anim, pose, prompts, roll  # noqa: E402
from npcgen.comfy import Comfy, edit_graph, t2i_graph  # noqa: E402
from npcgen.config import load  # noqa: E402


CASES = (
    ("modern-semi-real", "modern", "semi_real", 9142101, {}),
    ("cyberpunk-comic", "cyberpunk", "comic", 9142102, {}),
    ("anthro-wolf", "anthro", "semi_real", 9142103, {"origin": "wolf"}),
    ("pirates-pixel", "pirates", "pixel", 9142104, {}),
    ("zombies-watercolor", "zombies", "watercolor", 9142105, {}),
)


def save_png(raw, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)


def guide_taur(kind, size=(576, 864)):
    """Simple, deliberately side-on shape guide with one head and four animal legs."""
    w, h = size
    im = Image.new("RGB", size, (221, 38, 137))
    d = ImageDraw.Draw(im)
    fur = (125, 91, 72) if kind == "taur" else (40, 95, 112)
    dark = (86, 65, 56) if kind == "taur" else (29, 66, 78)
    skin, shirt, hair = (190, 139, 112), (30, 138, 150), (66, 47, 40)
    # Two far legs first, then a single horizontal animal body.
    d.line([(293, 585), (270, 720), (252, 776)], fill=dark, width=37)
    d.line([(441, 570), (437, 711), (458, 774)], fill=dark, width=35)
    d.ellipse((190, 480, 493, 622), fill=fur)
    d.polygon([(476, 531), (540, 550), (555, 588), (499, 571)], fill=fur)
    d.line([(232, 577), (214, 700), (194, 776)], fill=fur, width=47)
    d.line([(401, 579), (407, 700), (393, 779)], fill=fur, width=43)
    for x in (194, 252, 393, 458):
        d.ellipse((x - 20, 761, x + 23, 789), fill=dark)
    # The human torso rises directly from the animal shoulders, without hips or legs.
    d.polygon([(195, 506), (214, 344), (291, 342), (317, 510)], fill=shirt)
    d.line([(207, 383), (174, 487), (159, 546)], fill=skin, width=28)
    d.line([(289, 385), (331, 461), (349, 535)], fill=skin, width=28)
    d.ellipse((220, 246, 292, 346), fill=skin)
    d.pieslice((214, 236, 299, 326), 180, 360, fill=hair)
    d.line([(215, 347), (290, 347)], fill=skin, width=12)
    return im


def run_matrix(client, data, cfg, out):
    records = []
    q = cfg["quality"]["draft"]
    for label, setting_id, style_id, seed, constraints in CASES:
        npc = roll.roll_npc(data, setting_id, constraints=constraints, seed=seed)
        npc["style"] = style_id
        style, setting = data["styles"][style_id], data["settings"][setting_id]
        key = prompts.pick_key(npc)
        row = {"case": label, "seed": seed, "setting": setting_id, "style": style_id,
               "morphology": npc["morphology"], "profile": npc["profile"], "assets": {}}
        for asset, size, make_prompt in (
            ("portrait", q["portrait"], lambda: prompts.portrait(npc, style, setting)),
            ("sprite", q["sprite"], lambda: prompts.sprite(npc, style, setting, key)),
        ):
            pr = make_prompt()
            raw = client.run(t2i_graph(client.models, pr["positive"], pr["negative"],
                                       *size, seed, q["steps"], cfg["cfg"]))
            path = out / f"{label}-{asset}.png"
            save_png(raw, path)
            row["assets"][asset] = {"path": path.name, "prompt": pr, "size": size}
            print(f"saved {path}", flush=True)
        records.append(row)
        (out / "matrix.json").write_text(json.dumps(records, indent=2), "utf-8")


def run_taur(client, cfg, out):
    q = cfg["quality"]["draft"]
    for kind in ("taur", "dragontaur"):
        guide = guide_taur(kind, tuple(q["sprite"]))
        buf = io.BytesIO()
        guide.save(buf, "PNG")
        save_png(buf.getvalue(), out / f"{kind}-guide.png")
        uploaded = client.upload_image(buf.getvalue(), f"{kind}-guide.png")
        coat = "furred wolf-like" if kind == "taur" else "blue scaled dragon-like"
        positive = ("Repaint the simple colored shape in the first image as a detailed semi-realistic fantasy "
                    "character sprite. Follow its exact silhouette and placement. One human face and head, "
                    "a human torso in a teal tunic with exactly two human arms, joined seamlessly at the waist "
                    f"to a horizontal {coat} animal body with exactly four legs and a tail. "
                    "The animal body has no head or neck. The human torso has no human legs. "
                    "Show all four animal feet and the whole figure, side view facing right, on a flat magenta background.")
        negative = "second head, animal head, separate animal, rider, human legs, biped, missing animal legs"
        for seed in (9142201, 9142202, 9142203):
            raw = client.run(edit_graph(client.models, uploaded, positive, seed, q["steps"],
                                        cfg["edit_cfg"], size=tuple(q["sprite"]), negative=negative))
            path = out / f"{kind}-guided-{seed}.png"
            save_png(raw, path)
            print(f"saved {path}", flush=True)
        (out / f"{kind}-guided-prompt.json").write_text(json.dumps({"positive": positive,
            "negative": negative, "seeds": [9142201, 9142202, 9142203]}, indent=2), "utf-8")


def run_taur_detail(client, data, cfg, out):
    """Try the walk-cycle strategy: rough pose first, character portrait second."""
    q = cfg["quality"]["draft"]
    style, setting = data["styles"]["semi_real"], data["settings"]["anthro"]
    for kind in ("taur", "dragontaur"):
        npc = roll.roll_npc(data, "anthro", constraints={"origin": kind}, seed=9142501)
        npc["style"] = "semi_real"
        portrait = prompts.portrait(npc, style, setting)
        face_raw = client.run(t2i_graph(client.models, portrait["positive"], portrait["negative"],
                                        *q["portrait"], 9142501, q["steps"], cfg["cfg"]))
        save_png(face_raw, out / f"{kind}-detail-portrait.png")
        face_name = client.upload_image(face_raw, f"{kind}-detail-portrait.png")
        guide_file = out / f"{kind}-guide.png"
        if not guide_file.exists():
            buf = io.BytesIO()
            guide_taur(kind, tuple(q["sprite"])).save(buf, "PNG")
            save_png(buf.getvalue(), guide_file)
        guide_name = client.upload_image(guide_file.read_bytes(), f"{kind}-detail-guide.png")
        animal = "wolf-like furred" if kind == "taur" else "dragon-like scaled"
        positive = ("The first image is a rough placement guide, not final art. Repaint its exact side-view "
                    "silhouette as a detailed semi-realistic fantasy character illustration. The second image "
                    "shows the same character's face, hair, skin and clothing: match that identity and art style. "
                    f"A human upper torso rises from a horizontal {animal} animal body, with four animal legs, "
                    "four feet and a tail. Keep exactly one head, the human head from the second image; "
                    "the animal body has no head and the human torso has no human legs. Preserve the rough "
                    "guide's overall pose and limb placement while replacing every flat shape with detailed "
                    "painted anatomy. Whole figure visible, flat magenta background, no floor or shadow.")
        negative = "second head, animal head, separate animal, rider, human legs, biped, missing animal legs"
        for seed in (9142601, 9142602, 9142603):
            raw = client.run(edit_graph(client.models, [guide_name, face_name], positive, seed,
                                        q["steps"], cfg["edit_cfg"], size=tuple(q["sprite"]),
                                        negative=negative))
            path = out / f"{kind}-detail-{seed}.png"
            save_png(raw, path)
            print(f"saved {path}", flush=True)
        (out / f"{kind}-detail-prompt.json").write_text(json.dumps({"positive": positive,
            "negative": negative, "profile": npc["profile"], "seeds": [9142601, 9142602, 9142603]}, indent=2), "utf-8")


def run_actions(client, data, cfg, out):
    q = cfg["quality"]["draft"]
    source = out / "modern-semi-real-sprite.png"
    if not source.exists():
        raise SystemExit("Run the matrix phase first to create the reference sprite")
    style = data["styles"]["semi_real"]
    front = client.upload_image(source.read_bytes(), "gpu-validation-front.png")
    view_pr = anim.view_prompt(data["animations"], "right", style, "magenta")
    raw = client.run(edit_graph(client.models, front, view_pr["positive"], 9142300,
                                q["anim_steps"], cfg["edit_cfg"], size=tuple(q["anim"]),
                                negative=view_pr["negative"]))
    save_png(raw, out / "actions-side-reference.png")
    side = client.upload_image(raw, "gpu-validation-side.png")
    for clip_id in ("run", "attack", "hurt", "cast", "death"):
        for index in range(4):
            pr = anim.frame_prompt(data["animations"], clip_id, "right", index, 4,
                                   style, "magenta")
            seed = 9142400 + index
            raw = client.run(edit_graph(client.models, [side, front], pr["positive"], seed,
                                        q["anim_steps"], cfg["edit_cfg"], size=tuple(q["anim"]),
                                        negative=pr["negative"]))
            path = out / f"action-{clip_id}-{index}.png"
            save_png(raw, path)
            print(f"saved {path}", flush=True)


def run_turnaround(client, data, cfg, out):
    q = cfg["quality"]["draft"]
    source = out / "modern-semi-real-sprite.png"
    if not source.exists():
        raise SystemExit("Run the matrix phase first to create the reference sprite")
    front = client.upload_image(source.read_bytes(), "gpu-validation-turnaround-front.png")
    style = data["styles"]["semi_real"]
    for direction in ("right", "up"):
        pr = anim.view_prompt(data["animations"], direction, style, "magenta")
        raw = client.run(edit_graph(client.models, front, pr["positive"], 9142700,
                                    q["anim_steps"], cfg["edit_cfg"], size=tuple(q["anim"]),
                                    negative=pr["negative"]))
        path = out / f"turnaround-{direction}.png"
        save_png(raw, path)
        print(f"saved {path}", flush=True)


def run_sprite_fixes(client, data, cfg, out):
    q = cfg["quality"]["draft"]
    for label, setting_id, style_id, seed, constraints in CASES:
        if label not in {"cyberpunk-comic", "zombies-watercolor"}:
            continue
        npc = roll.roll_npc(data, setting_id, constraints=constraints, seed=seed)
        npc["style"] = style_id
        style, setting = data["styles"][style_id], data["settings"][setting_id]
        keys = ("magenta",) if label == "cyberpunk-comic" else ("green", "blue")
        for key in keys:
            pr = prompts.sprite(npc, style, setting, key)
            if label == "cyberpunk-comic":
                pr["positive"] = ("A single isolated character cutout against only a seamless, perfectly uniform "
                                  "flat magenta backdrop; no environment, objects, street or city visible. "
                                  + pr["positive"] + " The background is empty solid magenta across the entire canvas.")
                pr["negative"] += ", skyline, buildings, city, street, scenery, background objects, comic panels"
            else:
                pr["positive"] += f" The background is empty solid {key} across the entire canvas."
                pr["negative"] += ", watercolor wash on the background, foliage, scenery"
            suffix = "-blue" if key == "blue" else ""
            path = out / f"{label}-sprite-fix{suffix}.png"
            if path.exists():
                continue
            raw = client.run(t2i_graph(client.models, pr["positive"], pr["negative"],
                                       *q["sprite"], seed, q["steps"], cfg["cfg"]))
            save_png(raw, path)
            (out / f"{label}-sprite-fix{suffix}.json").write_text(json.dumps({"prompt": pr, "seed": seed,
                "key": key}, indent=2), "utf-8")
            print(f"saved {path}", flush=True)


def run_sprite_regression(client, data, cfg, out):
    """Fresh characters for the two sprite styles whose first GPU sample failed."""
    q = cfg["quality"]["draft"]
    rows = []
    for label, setting_id, style_id, _, constraints in CASES:
        if label not in {"cyberpunk-comic", "zombies-watercolor"}:
            continue
        for seed in (9143101, 9143102):
            npc = roll.roll_npc(data, setting_id, constraints=constraints, seed=seed)
            npc["style"] = style_id
            style, setting = data["styles"][style_id], data["settings"][setting_id]
            key = prompts.pick_key(npc, style)
            pr = prompts.sprite(npc, style, setting, key)
            raw = client.run(t2i_graph(client.models, pr["positive"], pr["negative"],
                                       *q["sprite"], seed, q["steps"], cfg["cfg"]))
            name = f"{label}-{seed}"
            save_png(raw, out / f"{name}.png")
            cut = anim.cutout_full(raw, style.get("hard_edges", False))
            if cut is not None:
                cut.save(out / f"{name}-cutout.png")
                preview = Image.new("RGBA", cut.size, (224, 224, 224, 255))
                preview.alpha_composite(cut)
                preview.convert("RGB").save(out / f"{name}-preview.png")
            rows.append({"case": label, "seed": seed, "key": key, "profile": npc["profile"],
                         "prompt": pr, "cutout_keyed": cut is not None})
            (out / "sprite-regression.json").write_text(json.dumps(rows, indent=2), "utf-8")
            print(f"saved {name} (keyed={cut is not None})", flush=True)


def run_guided_run(client, data, cfg, out, source=None, frames=None):
    """Research trial: drawn run poses plus the front sprite as identity reference."""
    q = cfg["quality"]["draft"]
    source = source or out / "modern-semi-real-sprite.png"
    front = client.upload_image(source.read_bytes(), "gpu-validation-run-front.png")
    cut = anim.cutout_full(source.read_bytes(), False)
    palette = pose.sample_palette(cut)
    palette["hair"] = (190, 190, 190)
    style = data["styles"]["semi_real"]
    # Landing and push-off need different silhouettes. Swapping the near and far
    # legs of one side-view pose made both generated contact frames look identical.
    landing = dict(right=(55, 35, -20), left=(-42, 65, 40),
                   arm_right=(-42, 55), arm_left=(40, 55))
    push_off = dict(right=(-53, 15, 48), left=(35, 72, -15),
                    arm_right=(35, 55), arm_left=(-42, 55))
    flight = dict(right=(65, 70, 20), left=(-28, 82, 30),
                  arm_right=(-58, 60), arm_left=(48, 60))
    swap = lambda p: dict(right=p["left"], left=p["right"],
                          arm_right=p["arm_left"], arm_left=p["arm_right"])
    poses = (landing, flight, push_off, swap(flight))
    for index, run_pose in enumerate(poses):
        if frames is not None and index not in frames:
            continue
        floor = 0.86 if index in (1, 3) else 0.93
        puppet = pose.paint(run_pose, "side", palette, tuple(q["anim"]), floor=floor)
        buf = io.BytesIO()
        puppet.save(buf, "PNG")
        save_png(buf.getvalue(), out / f"guided-run-puppet-{index}.png")
        puppet_name = client.upload_image(buf.getvalue(), f"guided-run-puppet-{index}.png")
        pr = anim.guided_frame_prompt(data["animations"], "run", "right", "the short silver-haired woman in a blue vest",
                                      style, "magenta", False)
        pr["positive"] += " Preserve her short pixie haircut; do not add a bun or ponytail."
        if index in (1, 3):
            pr["positive"] += " Both feet are entirely off the ground in this airborne frame."
        raw = client.run(edit_graph(client.models, [puppet_name, front], pr["positive"], 9142801,
                                    q["anim_steps"], cfg["edit_cfg"], size=tuple(q["anim"]),
                                    negative=pr["negative"]))
        path = out / f"guided-run-{index}.png"
        save_png(raw, path)
        print(f"saved {path}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("matrix", "taur", "taur-detail", "actions", "turnaround", "sprite-fixes", "sprite-regression", "guided-run"))
    parser.add_argument("--out", type=pathlib.Path, required=True)
    parser.add_argument("--sprite", type=pathlib.Path, help="source sprite for guided-run (defaults to the matrix sample in --out)")
    parser.add_argument("--frame", type=int, action="append", choices=range(4), help="only redraw selected guided-run frame (0-3)")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    cfg = load()
    client = Comfy(cfg["comfy_url"], cfg["models"])
    health = client.health()
    if not health["online"] or health["missing"]:
        raise SystemExit(f"ComfyUI unavailable or models missing: {health}")
    data = roll.load_data()
    if args.phase == "taur":
        run_taur(client, cfg, args.out)
    elif args.phase == "guided-run":
        run_guided_run(client, data, cfg, args.out, args.sprite, args.frame)
    else:
        {"matrix": run_matrix, "taur-detail": run_taur_detail, "actions": run_actions,
         "turnaround": run_turnaround, "sprite-fixes": run_sprite_fixes,
         "sprite-regression": run_sprite_regression}[args.phase](
            client, data, cfg, args.out)


if __name__ == "__main__":
    main()
