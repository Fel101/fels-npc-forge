"""A before/after quality check of a cleaned-up 3D model, by a vision model in LM Studio.

Blender renders the generator's original and the cleaned-up game version from the front (whole figure and face), and
they are set beside the model sheet's front picture. The vision model is asked which of the two shows the character more
faithfully and cleanly, and what is wrong with each. It is asked twice, with the two models swapped, and a verdict only
stands when both answers agree; otherwise the check says there is no clear difference.

Measured on ten boards (2026-09-26): Imani made from her sprite (a doubled face) against made from the model sheet, a
Hunyuan model before and after painting, Clara's and Imani's Pixal3D models before and after the clean-up, and Clara at
two Hunyuan detail levels. qwen3.8-27b picked the right one of the four clear pairs in both orders and named the real
faults (the doubled face, the black rings round Clara's eyes, brown streaks in Imani's hair, dark specks in the hair);
on the two detail levels, where there is little to choose, it picked whichever stood first both times, which the
two-way asking turns into "no clear difference". qwen3.6-35b-a3b also picked the clear pairs right, but once ran out of
room before answering and missed the hair streaks. Its problem lists are the model's opinion, not measurements: they
point at what to look at.

Muse Glimmer 30B, asked the same ten boards afterwards, picked the right one in both orders every time, the detail
levels included (the Fine face, by a small margin), so it is asked first where it is installed.

Only when asked ("Check with LM Studio" in the Game version section, or --check-3d): a future generator may not need it.
"""
import io
import json
import pathlib
import re
import tempfile
import time

from PIL import Image, ImageDraw, ImageFont

from . import eyes, headdetail, rig3d

SCRIPT = pathlib.Path(__file__).resolve().parent / "blender" / "check_render.py"
# measured, best first ("vision_model" in config.json overrides)
MODELS = ("local/muse-glimmer-30b", "qwen/qwen3.8-27b", "qwen/qwen3.6-35b-a3b")
CONTEXT_LENGTH = 12288      # a board is about 1,500 tokens and the Qwen models think at length before answering
MAX_TOKENS = 8000
RENDER_TIMEOUT = 600
BOARD_FILE = "model_3d_check.png"
MAX_PROBLEMS = 6

PROMPT = ('The picture has three columns. "Reference" is the drawing of a character. "A" and "B" are renders of two 3D '
          "models of that character, the whole body on top and the face below.\n\n"
          "Judge how faithfully and cleanly each 3D model shows the character in the reference: the same face, hair, clothes "
          "and colours, and no defects such as a doubled or ghost face, extra eyes or noses, smeared or blurred paint, "
          "patches of the wrong colour, black or grey patches, holes or specks. Ignore the lighting and the grey background.\n\n"
          "Answer with JSON only, no other text:\n"
          '{"better": "A" or "B" or "same", "A": {"score": 1 to 10, "problems": ["short phrase", ...]}, '
          '"B": {"score": 1 to 10, "problems": ["short phrase", ...]}}')


class CheckError(eyes.EyeError):
    """The check could not be made. The message is safe to show."""


def availability(cfg):
    """{"model": key or None, "missing": [...]} for the page."""
    return eyes.availability(cfg, MODELS)


def render(cfg, models, cancel=None):
    """{name: {"body": PNG bytes, "face": PNG bytes}} for {name: GLB bytes}, rendered by Blender."""
    with tempfile.TemporaryDirectory(prefix="npcforge-check-") as tmp:
        tmp = pathlib.Path(tmp)
        jobs = []
        for name, glb in models.items():
            (tmp / f"{name}.glb").write_bytes(glb)
            jobs.append(f"{name}={tmp / f'{name}.glb'}")
        last = tmp / f"{list(models)[-1]}_face.png"
        rig3d.run_script(cfg, SCRIPT, [str(tmp), *jobs], tmp, last, "Rendering the models for the check",
                         "Blender could not render the models for the check", cancel=cancel, timeout=RENDER_TIMEOUT)
        return {name: {shot: (tmp / f"{name}_{shot}.png").read_bytes() for shot in ("body", "face")} for name in models}


def _font(size):
    try:
        return ImageFont.truetype("arial.ttf", size)
    except OSError:
        return ImageFont.load_default(size)


def _fit(png, w, h, background):
    image = Image.open(io.BytesIO(png)).convert("RGB")
    image.thumbnail((w, h), Image.LANCZOS)
    out = Image.new("RGB", (w, h), background)
    out.paste(image, ((w - image.width) // 2, (h - image.height) // 2))
    return out


def board(reference_png, columns):
    """PNG bytes: the reference picture (whole and its head) and then each (label, {"body", "face"}) in `columns`, the
    whole figure above the face."""
    head = headdetail.crop(reference_png, headdetail.head_box(reference_png))
    cells = [("Reference", _fit(reference_png, 360, 640, (255, 255, 255)), _fit(head, 360, 360, (255, 255, 255)))]
    cells += [(label, _fit(shots["body"], 360, 640, (217, 217, 217)), _fit(shots["face"], 360, 360, (217, 217, 217)))
              for label, shots in columns]
    image = Image.new("RGB", (10 + 370 * len(cells), 50 + 640 + 10 + 360), "white")
    draw, font = ImageDraw.Draw(image), _font(28)
    for i, (label, body, face) in enumerate(cells):
        x = 10 + i * 370
        draw.text((x + 180, 28), label, fill="black", font=font, anchor="mm")
        image.paste(body, (x, 50))
        image.paste(face, (x, 700))
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def parse(text):
    """The answer as {"better": "A" | "B" | "same", "A": {"score", "problems"}, "B": {...}}; None if it makes no sense."""
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S)
    match = re.search(r"\{.*\}", text, re.S)
    try:
        answer = json.loads(match.group(0))
        better = str(answer["better"]).strip().upper()
        sides = {}
        for side in ("A", "B"):
            score = float(answer[side]["score"])
            problems = [str(p).strip() for p in answer[side].get("problems") or [] if str(p).strip()]
            sides[side] = {"score": min(10.0, max(1.0, score)), "problems": problems}
    except (AttributeError, KeyError, TypeError, ValueError):
        return None
    return {"better": "same" if better not in ("A", "B") else better, **sides}


def _same_problem(a, b):
    """Whether two problem phrases say much the same (the two answers often word one fault twice): half or more of their
    longer words shared."""
    words = [set(w for w in re.findall(r"[a-z]+", s.lower()) if len(w) > 3) for s in (a, b)]
    union = words[0] | words[1]
    return bool(union) and len(words[0] & words[1]) / len(union) >= 0.4


def verdict(first, second):
    """Combine the two answers (`first` with the original as A, `second` with it as B) into
    {"verdict": "before" | "after" | "unclear", "agreed", "before": {"score", "problems"}, "after": {...}}."""
    asked = [(a, {"A": "before", "B": "after"}) for a in (first,) if a] + [(a, {"A": "after", "B": "before"}) for a in (second,) if a]
    if not asked:
        raise CheckError("The vision model did not answer the check in the expected form")
    votes = [sides.get(answer["better"], "same") for answer, sides in asked]
    agreed = len(votes) == 2 and votes[0] == votes[1] and votes[0] != "same"
    out = {"verdict": votes[0] if agreed else "unclear", "agreed": agreed}
    for name in ("before", "after"):
        scores, problems = [], []
        for answer, sides in asked:
            letter = next(k for k, v in sides.items() if v == name)
            scores.append(answer[letter]["score"])
            for p in answer[letter]["problems"]:
                if not any(_same_problem(p, q) for q in problems):
                    problems.append(p)
        out[name] = {"score": round(sum(scores) / len(scores), 1), "problems": problems[:MAX_PROBLEMS]}
    return out


def check(cfg, before_glb, after_glb, reference_png, comfy=None, cancel=None):
    """(result, PNG bytes of the board to show). `before_glb` is the generator's original, `after_glb` the cleaned-up model,
    `reference_png` the model sheet's front picture."""
    started = time.monotonic()
    shots = render(cfg, {"before": before_glb, "after": after_glb}, cancel)
    boards = [board(reference_png, [("A", shots["before"]), ("B", shots["after"])]),
              board(reference_png, [("A", shots["after"]), ("B", shots["before"])])]
    answers, model = eyes.converse(cfg, [(png, PROMPT) for png in boards], comfy, cancel, choices=MODELS,
                                   context=CONTEXT_LENGTH, max_tokens=MAX_TOKENS, needed_for="The quality check")
    result = verdict(*[parse(a) for a in answers])
    result.update(model=model, seconds=round(time.monotonic() - started))
    shown = board(reference_png, [("Generated", shots["before"]), ("Cleaned up", shots["after"])])
    return result, shown
