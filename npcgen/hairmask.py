"""Hair masks from SAM 3.1 in ComfyUI, for painting a model's hair only from hair (npcgen/paint3d.py, `hair`).

A generator often makes the hair bigger than the pictures have it: Imani's fringe hangs lower than in her turnaround
pictures, Devi's side locks fall over her cheeks. Painted from the pictures, those strands took the skin behind them,
as skin-coloured streaks. SAM 3.1 (ComfyUI's own SAM3_Detect node, asked for "hair") finds the hair in each turnaround
picture and on the bare model drawn as that picture sees it (and the model's ears and eyes, which are never hair), and
the painter keeps skin off the model's hair.

Measured 2026-09-26 on five models (Imani, Devi and Clara from Pixal3D, Imani and Clara from Hunyuan3D): the streaks on
Imani's fringe, the skin and a ghost profile on Devi's locks, and the skin patches on the Hunyuan models' temple hair
all became hair, with faces unchanged; about a minute more. See docs/model-cleanup.md.

Only when asked ("Match the hair" in the Game version section, or --match-hair): a future generator may not need it.
"""
import io

import numpy as np
from PIL import Image

from .comfy import ComfyError

CHECKPOINT = "sam3.1_multiplex_fp16.safetensors"   # Comfy-Org/sam3.1 on Hugging Face (SAM Licence), in models/checkpoints
NODE = "SAM3_Detect"
PROMPT = "hair"
THRESHOLD = 0.5
DOWNLOAD = "https://huggingface.co/Comfy-Org/sam3.1/resolve/main/checkpoints/" + CHECKPOINT
LICENCE = "SAM Licence (commercial use allowed)"


class HairMaskError(ComfyError):
    """Hair masks could not be made. The message is safe to show."""


def graph(image, prompt=PROMPT, threshold=THRESHOLD):
    """ComfyUI graph: SAM 3.1's mask of `prompt` in the uploaded `image`, as a black and white picture."""
    return {"1": {"class_type": "LoadImage", "inputs": {"image": image}},
            "2": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": CHECKPOINT}},
            "3": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["2", 1]}},
            "4": {"class_type": NODE, "inputs": {"model": ["2", 0], "image": ["1", 0], "conditioning": ["3", 0],
                                                 "threshold": threshold, "refine_iterations": 2, "individual_masks": False}},
            "5": {"class_type": "MaskToImage", "inputs": {"mask": ["4", 0]}},
            "6": {"class_type": "PreviewImage", "inputs": {"images": ["5", 0]}}}


def availability(comfy):
    """{"model": the checkpoint or None, "licence", "missing": [...]} for the page."""
    missing = []
    try:
        if CHECKPOINT not in (comfy.get_json("/models/checkpoints", timeout=10) or []):
            missing.append(f"SAM 3.1 in ComfyUI's models/checkpoints ({CHECKPOINT}, from Comfy-Org/sam3.1 on Hugging Face)")
        if NODE not in (comfy.get_json(f"/object_info/{NODE}", timeout=10) or {}):
            missing.append(f"a ComfyUI new enough to have the {NODE} node")
    except ComfyError:
        missing = ["ComfyUI"]
    return {"model": None if missing else CHECKPOINT, "licence": LICENCE, "missing": missing}


def finder(comfy, npc_id, cancel=None):
    """A function (PNG bytes, name, what="hair") -> boolean mask of `what`, for paint3d.paint(hair=...)."""
    def find(png, name, what=PROMPT):
        uploaded = comfy.upload_image(png, f"{npc_id}_hair_{name}.png")
        out = comfy.run(graph(uploaded, what), cancel=cancel, timeout=300)
        return np.asarray(Image.open(io.BytesIO(out)).convert("L")) > 127
    return find
