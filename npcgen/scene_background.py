"""Generate an empty environment plate for a 3D scene through the normal GPU queue.

The picture engine is the one the page's Models panel names, the app's default when none is named: Qwen-Image 2512 (Apache-2.0) since
2026-10-14, Mage-Flow before. Mage-Flow takes a negative prompt that keeps people out of the plate; every other engine runs at cfg 1 and
ignores it, so there the emptiness is in the positive wording alone (see `wording`). Measured with Qwen-Image 2512: a market square with
fruit stalls, the hardest setting because there are stalls to stand behind, six seeds looked at full size: no vendor in any (two have a
dark patch at an edge that might be one); a forest gate, a tavern, an Old West street and a spaceship corridor, three seeds each, looked
at as thumbnails: nobody. Black letterbox bars were drawn once in 102 test plates (a "wide cinematic" picture); they are cut off here
whatever engine drew the plate.
"""
import io
import random
import uuid

from PIL import Image, ImageOps

from . import engines

NEGATIVE = "people, person, character, humanoid, silhouette, portrait, lettering, caption, watermark"
BARS_CUT = "The picture engine drew black bars at the top and bottom; they were cut off."
MIN_BARS = 8           # rows at EACH edge: letterbox bars come in a pair (65 and 66 rows measured), a dark ceiling alone is not bars
MAX_BARS = 0.5         # of the height, both together: more is a mostly black picture, not bars
DARK = 20              # a row is black when its brightest pixel is below this (of 255)


def wording(prompt, engine_id):
    """(positive, negative) prompt of the plate. Mage-Flow keeps the wording it was tuned with, and its negative prompt keeps people out.
    Every other engine ignores the negative prompt, so the emptiness is said in the positive wording, as a place that is deserted and
    unattended: "empty scene with no people" still drew vendors behind the stalls of a market in 7 of 12 pictures, this wording in none
    of 6 (a variant that did not name the stalls and counters drew them again, in at least 2 of 6). The cost is furniture: with nobody
    there, the engine fills the plate with unattended counters, benches and booths. "Cinematic" is left out because it is what draws
    black bars. The negative prompt is always the same, because the Mage-Flow fallback may end up using it."""
    if engine_id == "mage":
        positive = (f"Wide cinematic environment background plate: {prompt}. Empty scene with no people, "
                    "no characters, no figures, no text. A clear, open walkable foreground with visible ground "
                    "and a distant horizon. Eye-level camera, coherent perspective, detailed environment, 16:9.")
    else:
        positive = (f"Wide environment background plate that fills the whole 16:9 frame edge to edge: {prompt}. A deserted, quiet place with "
                    "nobody in it, as if everyone has just gone home: the stalls, counters, seats and doorways are unattended and nothing "
                    "alive is in sight. A clear, open walkable foreground with visible ground and a distant horizon. Eye-level camera, "
                    "coherent perspective, detailed environment.")
    return positive, NEGATIVE


def bars(image):
    """(rows at the top, rows at the bottom) that are black all the way across: the letterbox bars an engine draws after "cinematic"."""
    grey = image.convert("L")
    width, height = grey.size

    def black(y):
        return grey.crop((0, y, width, y + 1)).getextrema()[1] < DARK

    limit = int(height * MAX_BARS)
    top = 0
    while top < limit and black(top):
        top += 1
    bottom = 0
    while bottom < limit and black(height - 1 - bottom):
        bottom += 1
    return (top, bottom) if min(top, bottom) >= MIN_BARS and top + bottom <= limit else (0, 0)


def engine_of(worker, job):
    """(engine id, whether Mage-Flow may draw the plate when that engine cannot): what the job names, else the app's defaults."""
    from .service import default_options        # (late: service imports jobs, and jobs imports this module)
    base = default_options(worker.cfg)
    fallback = job.options.get("engine_fallback")
    return engines.clean(job.options.get("image_model") or base["image_model"], "image"), bool(base["engine_fallback"] if fallback is None else fallback)


def run(worker, job, step):
    from .jobs import ENGINE_NOTE               # (late: jobs imports this module)
    prompt = job.options["scene_background"]["prompt"]
    engine, fallback = engine_of(worker, job)
    positive, negative = wording(prompt, engine)
    quality = worker.cfg["quality"]["standard"]
    with engines.strict(not fallback):          # a plate its engine cannot draw fails with the reason instead of Mage-Flow drawing it
        png, meta = engines.image(worker.comfy, engine, positive, negative, 1280, 720,
                                  random.randrange(2 ** 32), quality["steps"], worker.cfg["cfg"], job.cancel)
    notes = [ENGINE_NOTE + meta["engine_note"]] if meta.get("engine_note") else []
    with Image.open(io.BytesIO(png)) as source:
        picture = source.convert("RGB")
    top, bottom = bars(picture)
    if top or bottom:
        picture = picture.crop((0, top, picture.width, picture.height - bottom))
        notes.append(BARS_CUT)
    output = io.BytesIO()
    ImageOps.fit(picture, (1280, 720), method=Image.Resampling.LANCZOS).save(output, "PNG")
    filename = f"background/{uuid.uuid4().hex[:10]}.png"
    worker.scenes.write_media(job.scene_id, filename, output.getvalue())
    if not worker.scenes.set_background(job.scene_id, prompt, filename, meta.get("engine", "Mage-Flow")):
        worker.scenes.resolve(job.scene_id, filename).unlink(missing_ok=True)
        notes.append("The prompt changed before generation finished; save and generate again.")
    if notes:
        step["note"] = " ".join(notes)
