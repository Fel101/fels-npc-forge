"""The steps of a creature's job (npcgen/creatures.py makes the plan): the concept picture, the 3D model made from it, and the game model.

The concept is drawn with Qwen-Image 2512 (Apache-2.0) and the 3D model with TRELLIS.2-4B (MIT), both through ComfyUI, so the
one-model-at-a-time rule (npcgen/gpu.py) covers them the way it covers an item's steps. The game model is made by Blender in the
background (npcgen/blender/creature_prep.py): it is a separate process, so a cancel stops it.

Every step reads the creature fresh from the store, and writes what it makes back under the store's lock.
"""
import hashlib
import io
import json
import os
import subprocess
import time
from pathlib import Path

from PIL import Image

from . import creature_projects, creatures, engines, three_d, up_check
from .comfy import Cancelled, ComfyError
from .library import now

CONCEPT_ENGINE = "qwen2512"                          # Qwen-Image 2512 + Lightning (Apache-2.0); NOT Mage-Flow, which is research-only
CONCEPT_NEGATIVE = ("photograph, painting, flat colors, anime, sketch, blurry, low quality, deformed, extra limbs, extra heads, "
                    "watermark, text, logo, signature, cropped, multiple creatures, human, face, ground shadow, floor")
CONCEPT_SIZE = 1024
TRELLIS_MODEL = "microsoft/TRELLIS.2-4B"            # MIT (NOTICE.md). It reads DINOv3 features, rated "conditions" in data/licences.json (read 2026-10-13), so a creature rates "conditions".
THREE_D_ENGINE = "trellis2_single"                   # its id in data/licences.json, which rates the creature (npcgen/licences.py)
BG_MODEL = "birefnet.safetensors"
SAVE_NODE = "26"                                     # the SaveGLB node of the mesh tail below
SHAPE_TIMEOUT = 3600
PREP_TIMEOUT = 3600
PREP_SCRIPT = Path(__file__).resolve().parent / "blender" / "creature_prep.py"
DEFAULT_BLENDER = r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe"
TRIS = 20000                                         # ART_DIRECTION 5.4: a creature's LOD0 is at most 20,000 triangles


def blender_path():
    return os.environ.get("NPCFORGE_BLENDER", DEFAULT_BLENDER)


def trellis_graph(image_name, prefix, seed, quality):
    """The single-picture Trellis2 route: the picture is cut out, squared with a margin, sampled into shape, decoded by NPC
    Forge's decode node (which does not use the pack's hole fill that crashed the GPU on ROCm), remeshed and saved as a GLB."""
    settings = three_d.PIXAL_QUALITIES[three_d.clean_quality(quality)]
    g = {
        "1": {"class_type": "LoadBackgroundRemovalModel", "inputs": {"bg_removal_name": BG_MODEL}},
        "10": {"class_type": "LoadImage", "inputs": {"image": image_name}},
        "11": {"class_type": "RemoveBackground", "inputs": {"bg_removal_model": ["1", 0], "image": ["10", 0]}},
        "12": {"class_type": "ImageCropToMask", "inputs": {
            "images": ["10", 0], "masks": ["11", 0], "width": 1024, "height": 1024,
            "pad_factor": three_d.RIG_PAD, "grow_mask": 0, "background": "#000000"}},
        "13": {"class_type": "RemoveBackground", "inputs": {"bg_removal_model": ["1", 0], "image": ["12", 0]}},
        "14": {"class_type": "InvertMask", "inputs": {"mask": ["13", 0]}},
        "15": {"class_type": "JoinImageWithAlpha", "inputs": {"image": ["12", 0], "alpha": ["14", 0]}},
        "16": {"class_type": "Trellis2LoadModel", "inputs": {
            "modelname": TRELLIS_MODEL, "backend": "sdpa", "device": "cuda", "low_vram": True, "keep_models_loaded": False,
            "conv_backend": "flex_gemm", "sparse_backend": "sdpa", "use_reconviagen": False, "pixal3d_multiview": False}},
        "17": {"class_type": "Trellis2PreProcessImage", "inputs": {
            "image": ["15", 0], "padding": 5, "remove_background": False, "max_size": 1024}},
        "18": {"class_type": "Trellis2ImageCondGenerator", "inputs": {
            "pipeline": ["16", 0], "image": ["17", 0], "max_views": 1}},
        "19": {"class_type": "Trellis2SparseGenerator", "inputs": {
            "pipeline": ["18", 2], "image_cond": ["18", 0], "image": ["17", 0], "seed": seed,
            "sparse_structure_steps": 25, "sparse_structure_guidance_strength": 7.5,
            "sparse_structure_guidance_rescale": 0.7, "sparse_structure_rescale_t": 5,
            "sparse_structure_sampler": "heun", "sparse_structure_resolution": 32,
            "sparse_structure_guidance_interval_start": 0.6, "sparse_structure_guidance_interval_end": 1,
            "fill_holes": True, "hole_iterations": 1, "verbose": False, "dino_lock": 0.65, "dino_substeps": 2,
            "hole_fill_algorithm": "flood_fill", "dino_foundation_cap": 0.92, "keep_only_shell": True}},
        "20": {"class_type": "Trellis2ShapeGenerator", "inputs": {
            "pipeline": ["19", 2], "image_cond": ["18", 0], "image": ["17", 0], "coords": ["19", 0],
            "resolution": 512, "shape_steps": 12, "shape_guidance_strength": 7.5, "shape_guidance_rescale": 0.5,
            "shape_rescale_t": 3, "shape_sampler": "heun", "shape_guidance_interval_start": 0.6,
            "shape_guidance_interval_end": 1, "verbose": False, "dino_lock": 0.65, "dino_substeps": 2,
            "dino_foundation_cap": 0.92}},
        "21": {"class_type": "Trellis2ShapeCascadeGenerator", "inputs": {
            "pipeline": ["20", 2], "image_cond": ["18", 1], "image": ["17", 0], "shape_slat": ["20", 0],
            "from_resolution": ["20", 1], "to_resolution": 1024, "sparse_structure_resolution": ["19", 1],
            "max_num_tokens": 999999, "shape_steps": 12, "shape_guidance_strength": 7.5,
            "shape_guidance_rescale": 0.5, "shape_rescale_t": 3, "shape_sampler": "heun",
            "shape_guidance_interval_start": 0.6, "shape_guidance_interval_end": 1, "verbose": False,
            "dino_lock": 0.65, "dino_substeps": 2, "dino_foundation_cap": 0.92}},
        "22": {"class_type": three_d.DECODE_NODE, "inputs": {
            "pipeline": ["21", 2], "shape_slat": ["21", 0], "resolution": ["21", 1], "use_tiled_decoder": True}},
    }
    g.update(three_d.mesh_tail(["22", 1], settings, prefix, 23))
    return g


def run(worker, job, step):
    """Run one step of a creature job on the job worker's thread."""
    store = worker.creatures
    if store is None:
        raise ComfyError("The creature store is unavailable")
    try:
        creature = store.get(job.creature_id)
    except KeyError:
        raise ComfyError("This creature has been deleted") from None
    RUNNERS[step["kind"]](worker, job, step, creature)


def _note(job, text):
    job.beat(text)


def run_concept(worker, job, step, creature):
    if not creature.get("prompt"):
        raise ComfyError("This creature has no concept prompt yet: write one in the editor")
    grid = (job.options or {}).get("seeds")
    if grid:
        return _draw_grid(worker, job, creature, creatures.clean_seeds(grid))
    seed = int(creature["seed"])
    _note(job, "Drawing the concept picture")
    prompt, negative = _prompts(worker, creature)
    png, meta = engines.image(worker.comfy, CONCEPT_ENGINE, prompt, negative,
                              CONCEPT_SIZE, CONCEPT_SIZE, seed, 4, 1.0, cancel=job.cancel)
    store = worker.creatures
    store.write_file(creature["id"], "concept.png", png)
    store.write_file(creature["id"], "concept.json", json.dumps({
        "prompt": prompt, "negative": negative, "seed": seed, "engine": CONCEPT_ENGINE,
        "engine_meta": meta, "size": CONCEPT_SIZE, "made": now(), "sha256": hashlib.sha256(png).hexdigest()}, indent=2))
    def drawn(c):
        c.setdefault("made", {}).update(concept=now())
        c["concept_approved"] = False                    # a new picture has not been looked at yet
    store.update(creature["id"], drawn)


def _prompts(worker, creature):
    """The prompt and negative prompt a picture is drawn with: the creature's own, with its game's style (npcgen/creature_projects.py)."""
    return creature_projects.concept_text(worker.creatures.projects.get(creature.get("project")), creature["prompt"], CONCEPT_NEGATIVE)


def _draw_grid(worker, job, creature, seeds):
    """One picture per seed, kept as concepts/<seed>.png beside its record. The concept picture is not changed: someone picks
    one of them (creatures.choose_concept), and that one becomes the concept and is approved."""
    store = worker.creatures
    prompt, negative = _prompts(worker, creature)
    for n, seed in enumerate(seeds, 1):
        _note(job, f"Drawing picture {n} of {len(seeds)} (seed {seed})")
        png, meta = engines.image(worker.comfy, CONCEPT_ENGINE, prompt, negative,
                                  CONCEPT_SIZE, CONCEPT_SIZE, seed, 4, 1.0, cancel=job.cancel)
        store.write_file(creature["id"], f"{creatures.CANDIDATES}/{seed}.png", png)
        store.write_file(creature["id"], f"{creatures.CANDIDATES}/{seed}.json", json.dumps({
            "prompt": prompt, "negative": negative, "seed": seed, "engine": CONCEPT_ENGINE,
            "engine_meta": meta, "size": CONCEPT_SIZE, "made": now()}, indent=2))


def run_3d(worker, job, step, creature):
    store = worker.creatures
    try:
        concept = store.read_file(creature["id"], "concept.png")
    except OSError:
        raise ComfyError("Make the concept picture first") from None
    comfy = worker.comfy
    _note(job, "Uploading the concept picture")
    name = comfy.upload_image(concept, f"{creature['id']}_concept.png")
    _note(job, "Making the 3D model from the picture")
    prefix = f"creature/{creature['id']}"
    graph = trellis_graph(name, prefix, int(creature["seed"]), "medium")
    entry = comfy.run_entry(graph, cancel=job.cancel, timeout=SHAPE_TIMEOUT, unload_models=True)
    saved = three_d.saved_file(entry, "3d", "GLB model", node=SAVE_NODE)
    glb = comfy.fetch_file(saved["filename"], saved.get("subfolder", ""), saved.get("type", "output"))
    store.write_file(creature["id"], "raw/model.glb", glb)
    store.write_file(creature["id"], "raw/run.json", json.dumps({
        "engine": THREE_D_ENGINE, "model": TRELLIS_MODEL, "quality": "medium", "seed": int(creature["seed"]), "comfy_file": saved["filename"],
        "bytes": len(glb), "made": now(), "concept_sha256": hashlib.sha256(concept).hexdigest()}, indent=2))
    store.update(creature["id"], lambda c: c.setdefault("made", {}).update({"3d": now()}))


def prep_command(creature, raw, out, plan):
    """The Blender command for the game-model step: the creature's own length, segments, triangle budget and picture facing, and
    the body plan's shortest allowed length (data/creature_plans.json, min_elongation) when it has one."""
    # A record can hold null for these (the Road-hound's does): null means the default, as a missing key does.
    command = [blender_path(), "--background", "--factory-startup", "--python", str(PREP_SCRIPT), "--",
               "--input", str(raw), "--output", str(out), "--plan", plan, "--tris", str(creature.get("tris") or TRIS),
               "--length", str(creature["length_m"]), "--segments", str(creature["segments"]), "--key", creature["id"],
               "--facing", str(creature.get("facing") or "left")]
    floor = creatures.PLANS.get(creature.get("body_plan"), {}).get("min_elongation")
    if floor is not None:
        command += ["--min-elongation", str(floor)]
    return command


def matte_graph(image):
    """ComfyUI graph that returns BiRefNet's matte of a picture as a grey image: the same graph as head3d.mask_graph, for a body."""
    return {
        "1": {"class_type": "LoadBackgroundRemovalModel", "inputs": {"bg_removal_name": BG_MODEL}},
        "2": {"class_type": "LoadImage", "inputs": {"image": image}},
        "3": {"class_type": "RemoveBackground", "inputs": {"bg_removal_model": ["1", 0], "image": ["2", 0]}},
        "4": {"class_type": "MaskToImage", "inputs": {"mask": ["3", 0]}},
        "5": {"class_type": "PreviewImage", "inputs": {"images": ["4", 0]}},
    }


def up_check_for(worker, creature, model, cancel):
    """The up check (npcgen/up_check.py) of a static body's game model against its concept picture: the picture's creature is cut out with
    BiRefNet in ComfyUI. A picture that can not be cut out is not an error of the prep: the check says so, and the prep goes on."""
    try:
        picture = worker.creatures.read_file(creature["id"], "concept.png")
        name = worker.comfy.upload_image(picture, f"{creature['id']}_up_check.png")
        matte = Image.open(io.BytesIO(worker.comfy.run(matte_graph(name), cancel=cancel, timeout=300))).convert("L")
    except (OSError, ComfyError, ValueError) as error:       # ValueError: ComfyUI answered with something that is not JSON or not an image
        return {"verdict": None, "reason": f"the check could not run ({error})"}
    return up_check.check(up_check.mask_of(matte), model)


def run_prep(worker, job, step, creature):
    store = worker.creatures
    raw = store.resolve(creature["id"], "raw/model.glb")
    if not raw.is_file():
        raise ComfyError("Make the 3D model first")
    out = store.resolve(creature["id"], "model.glb")
    # Blender writes here and it becomes model.glb only when it succeeds. The name must end in .glb: Blender's exporter adds .glb to
    # a name that lacks it (model.glb.part would have become model.glb.part.glb).
    part = store.resolve(creature["id"], "model.part.glb")
    plan = creatures.PLANS.get(creature["body_plan"], {}).get("prep", "static")     # data/creature_plans.json
    log_path = store.resolve(creature["id"], "prep.log")
    # The game model made before this run was made from older settings, so it is not kept while this run goes: a refused or
    # cancelled run must not leave it for the pack to ship.
    for rel in ("model.glb", "prep.json", "model.part.glb"):
        store.resolve(creature["id"], rel).unlink(missing_ok=True)
    command = prep_command(creature, raw, part, plan)
    _note(job, "Preparing the game model in Blender")
    started = time.time()
    with open(log_path, "w", encoding="utf-8") as log:
        proc = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
        while proc.poll() is None:
            if job.cancel.is_set():
                proc.kill()
                proc.wait()
                raise Cancelled()
            if time.time() - started > PREP_TIMEOUT:
                proc.kill()
                proc.wait()
                raise ComfyError("Blender took longer than an hour to prepare the model")
            _note(job, "Preparing the game model in Blender")
            time.sleep(2)
    text = log_path.read_text("utf-8", errors="replace")
    refused = next((line[len("REFUSED:"):].strip() for line in text.splitlines() if line.startswith("REFUSED:")), None)
    if refused:
        raise ComfyError(refused)                        # the shape check's own reason (creature_prep.py), not a Blender crash
    report = next((line[len("REPORT:"):].strip() for line in text.splitlines() if line.startswith("REPORT:")), None)
    if proc.returncode != 0 or report is None or not part.is_file():
        tail = "\n".join(text.splitlines()[-6:])
        raise ComfyError(f"Blender could not prepare the model: {tail}")
    fields = json.loads(report)
    if plan == "static":                                 # the up direction, checked against the picture (npcgen/up_check.py)
        fields["up_check"] = up_check_for(worker, creature, part, job.cancel)
    os.replace(part, out)
    store.write_file(creature["id"], "prep.json", json.dumps({
        **fields, "minutes": round((time.time() - started) / 60.0, 1), "made": now(),
        "raw_sha256": hashlib.sha256(raw.read_bytes()).hexdigest(), "settings": creatures.prep_settings(creature)}, indent=2))
    store.update(creature["id"], lambda c: c.setdefault("made", {}).update({"prep": now()}))


RUNNERS = {"creature_concept": run_concept, "creature_3d": run_3d, "creature_prep": run_prep}
