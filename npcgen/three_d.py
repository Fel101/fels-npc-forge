"""Local ComfyUI workflows for Hunyuan3D v2.1 and textured Pixal3D."""
import io
import secrets
import time

import numpy as np
from PIL import Image

from .comfy import ComfyError

CHECKPOINT = "hunyuan_3d_v2.1.safetensors"
PIXAL_SHEET_VIEWS = ("front", "side_left", "back", "side_right")
PIXAL_INPUTS = {"front": "front", "left": "side_left", "back": "back", "right": "side_right"}
PIXAL_HEAD_CROP_FRACTION = 0.32
PIXAL_HEAD_CROP_TOP_FRACTION = -0.02
HEAD_TEXTURE_NODE = "NPCForgePixal3DHeadTextureRefine"     # NPC Forge's own ComfyUI node (npcgen/comfy_extensions)
RIG_FOV = 20.0    # degrees: the field of view of Pixal3D's orbit rig, which reads a picture's framing as its camera
RIG_PAD = 1.1     # the model's unit cube fills 1/RIG_PAD of a rig picture
PIXAL_FACE_SOURCES = {
    "turnaround": {
        "label": "Turnaround crop · recommended",
        "summary": "Keeps the face color and scale consistent with the full-body views. Clara’s side-by-side trial matched better with this source.",
    },
    "portrait": {
        "label": "High-res portrait crop · experimental",
        "summary": "Uses more original face pixels, but the trial shifted Clara’s complexion and softened cheek details. Results may vary by character.",
    },
}

# The picture Hunyuan3D makes its shape from. The model sheet's front picture is the straight-on pose the side and back
# pictures share, so a model made from it lines up with all of them when it is painted (npcgen/unwrap.py). Measured on
# Imani, whose sprite stands slightly turned: outline match with the sheet's front picture 0.807 from the sprite, 0.934
# from the sheet's front, and painting the sprite-made model put copies of her face on her cheeks.
HUNYUAN_SOURCES = {
    "sprite": {
        "label": "Full-body sprite",
        "summary": "The character's sprite, as drawn. Fine for a shape you will not paint from the pictures.",
    },
    "turnaround": {
        "label": "Model sheet front · best for painting",
        "summary": "The model sheet's straight-on front picture (made first if missing). The shape then lines up with the "
                   "side and back pictures, so painting it from them works much better.",
    },
}

# How fine a grid Hunyuan3D's shape is read from (VAEDecodeHunyuan3D's octree resolution). At 256 a person's voxel is
# about 7 mm, so a nose is a few voxels wide and a painted face shows the steps. Measured on Clara (seed 7):
# generating took 57 s at 256, 107 s at 384 and 195 s at 512; the paint + clean-up (inner skin, unwrap, paint, 100k game
# version) took 128 s, 234 s and 320 s (the unwrap grows with the faces: 127k, 280k, 484k). 384 gave a visibly smoother
# nose that the 100k game version keeps; 512 looked much the same as 384 there, so it is not offered.
HUNYUAN_DETAILS = {
    "standard": {
        "label": "Standard · about 1 min",
        "summary": "A 256 grid (voxels about 7 mm across a person): fine for the body, but a nose or lips show steps "
                   "once painted. Measured: 1 minute to generate, 2 more to paint and clean up.",
        "octree": 256,
    },
    "fine": {
        "label": "Fine · about 2 min · smoother face",
        "summary": "A 384 grid (voxels about 4.5 mm): a visibly smoother nose and face, kept in the game version. "
                   "Measured: 2 minutes to generate, 4 more to paint and clean up.",
        "octree": 384,
    },
}

# Quality levels are intentionally separate from the image-generation quality selector: they affect the Pixal3D
# mesh and baked maps, and have a large effect on the time each local ComfyUI run takes.
PIXAL_QUALITIES = {
    "low": {
        "label": "Low · 1K textures · ~10–20 min",
        "summary": "Lower polygon and texture resolution. Usually the quickest option; runtime varies with ComfyUI and other queued work.",
        "estimate": "About 10–20 minutes",
        "upsample": 1024, "shape_steps": (8, 14, 8, 8), "remesh": 512, "faces": 350_000,
        "uv": 1024, "texture": 1024, "normal": 1024, "ao": 512, "ao_samples": 32,
    },
    "medium": {
        "label": "Medium · 2K textures · ~15–30 min",
        "summary": "Balanced mesh and texture detail. Recent runs on this PC took about 14 and 26 minutes.",
        "estimate": "About 15–30 minutes; recent runs took 14 and 26 minutes",
        "upsample": 1536, "shape_steps": (12, 20, 12, 12), "remesh": 768, "faces": 700_000,
        "uv": 2048, "texture": 2048, "normal": 2048, "ao": 1024, "ao_samples": 64,
    },
    "high": {
        "label": "High · 4K textures · ~40–80+ min",
        "summary": "Highest mesh and texture detail. Can take over an hour and uses substantially more VRAM; an out-of-memory error is possible.",
        "estimate": "About 40–80+ minutes; may take longer",
        "upsample": 2048, "shape_steps": (16, 28, 16, 16), "remesh": 1024, "faces": 1_000_000,
        "uv": 4096, "texture": 4096, "normal": 4096, "ao": 2048, "ao_samples": 96,
    },
}

# The detailed head (npcgen/head3d.py): Pixal3D given only a head, so the head fills the cube. Its voxels are 3 to 5 times finer
# than the whole-body run's at the same setting, which is why a smaller upsample does the work of a bigger one, and why the
# settings differ from PIXAL_QUALITIES. Measured on Natalie, Devi and Laura (docs/detailed-head.md).
HEAD_QUALITIES = {
    "medium": {
        "label": "Medium · about 15 min",
        "summary": "A 1024 grid over the head and a little neck (voxels about 0.25 mm), 2K textures. Measured: 15 minutes for the "
                   "model, a few more to clean it up and paint it from the pictures.",
        "estimate": "About 15-25 minutes",
        "upsample": 1024, "shape_steps": (12, 20, 12, 12), "remesh": 768, "faces": 500_000,
        "uv": 2048, "texture": 2048, "normal": 2048, "ao": 1024, "ao_samples": 64, "timeout": 3600,
    },
    "high": {
        "label": "High · about 35 min",
        "summary": "A 1536 grid, more sampling steps and 4K textures. Measured on one character: twice Medium's time (35 minutes) and "
                   "not a better face: the finer grid pulled each eyelid apart into several stacked plates, where Medium has one. "
                   "Worth a try only if Medium's shape is too coarse.",
        "estimate": "About 30-45 minutes",
        "upsample": 1536, "shape_steps": (16, 28, 16, 16), "remesh": 1024, "faces": 800_000,
        "uv": 4096, "texture": 4096, "normal": 4096, "ao": 2048, "ao_samples": 96, "timeout": 7200,
    },
}

DECODE_NODE = "NPCForgeTrellis2ShapeDecode"      # NPC Forge's own ComfyUI node (npcgen/comfy_extensions): a shape decode with no CuMesh
TRELLIS2_MODEL = "TencentARC/Pixal3D"            # the Trellis2 node pack's name for Pixal3D, whose multi-view weights it loads
FOUR_VIEW_GENERATORS = ("pixal3d", "trellis2")   # made from the model sheet's front, left, back and right pictures

GENERATORS = {
    "hunyuan21": {
        "label": "Hunyuan3D v2.1",
        "short": "Hunyuan3D",
        "note": "Local ComfyUI shape generation from the full-body sprite; exports a GLB.",
        "textured": False,
        "files": [("checkpoints", CHECKPOINT)],
        "nodes": ("ImageOnlyCheckpointLoader", "LoadImage", "ModelSamplingAuraFlow", "EmptyLatentHunyuan3Dv2",
                  "Hunyuan3Dv2Conditioning", "KSampler", "VAEDecodeHunyuan3D", "VoxelToMesh", "SaveGLB",
                  "RenderMesh", "SaveImage", "CLIPVisionEncode"),
    },
    "pixal3d": {
        "label": "Pixal3D multi-view",
        "short": "Pixal3D",
        "note": "Local ComfyUI shape generation from four full-body views, with head-focused texture detail; exports a textured PBR GLB.",
        "textured": True,
        "files": [("diffusion_models", "pixal3d_multiview_int8_convrot.safetensors"),
                  ("clip_vision", "dino_v3_L_naf_fp32.safetensors"),
                  ("vae", "trellis_2_shape_vae_bf16.safetensors"),
                  ("vae", "trellis_2_texture_vae_bf16.safetensors"),
                  ("background_removal", "birefnet.safetensors")],
        "nodes": ("UNETLoader", "CLIPVisionLoader", "VAELoader", "LoadBackgroundRemovalModel", "LoadImage",
                  "RemoveBackground", "ImageCropToMask", "Pixal3DMultiViewConditioning", "CFGOverride",
                  "RescaleCFG", "ModelSamplingSD3", "EmptyTrellis2LatentStructure", "KSampler",
                  "VaeDecodeStructureTrellis2", "Trellis2ShapeStage", "Trellis2UpsampleStage",
                  "VaeDecodeShapeTrellis", "Trellis2TextureStage", "VaeDecodeTextureTrellis", "GetMeshInfo",
                  "RemeshMesh", "DecimateMesh", "MeshSmoothNormals", "UnwrapMesh", "BakeTextureFromVoxel",
                  "BakeNormalMapFromMesh", "BakeAmbientOcclusion", "ApplyTextureToMesh", "SaveGLB", "RenderMesh",
                  "SaveImage", "NPCForgePixal3DHeadTextureRefine"),
    },
    "trellis2": {
        "label": "Trellis2 four pictures (shape only)",
        "short": "Trellis2",
        "note": "Local ComfyUI shape from the four model-sheet pictures with the Trellis2 node pack and Pixal3D's multi-view model, closed "
                "and simplified by ComfyUI's mesh nodes (about 4 minutes). No colour of its own: paint it with the clean-up afterwards. "
                "The sharpest shape measured; needs the Trellis2 node pack and its models (docs/model-import.md).",
        "textured": False,
        "files": [("background_removal", "birefnet.safetensors")],
        "nodes": ("LoadBackgroundRemovalModel", "LoadImage", "RemoveBackground", "ImageCropToMask", "InvertMask", "JoinImageWithAlpha",
                  "BatchImagesNode", "Trellis2LoadModel", "Trellis2Pixal3DMultiViewConfig", "Trellis2PreProcessImage",
                  "Trellis2ImageCondGenerator", "Trellis2SparseGenerator", "Trellis2ShapeGenerator", "Trellis2ShapeCascadeGenerator",
                  DECODE_NODE, "RemeshMesh", "DecimateMesh", "MeshSmoothNormals", "SaveGLB", "RenderMesh", "SaveImage"),
        # what only a node's own description can tell: a model name in a node's list, and a setting an older pack does not have
        "choices": [("Trellis2LoadModel", "modelname", TRELLIS2_MODEL)],
        "inputs": [("Trellis2LoadModel", "pixal3d_multiview")],
        "outputs": [(DECODE_NODE, "MESH")],
    },
}

# ComfyUI's own mesh nodes (nothing to download) that bring a model made elsewhere through the same clean-up (import_graph)
IMPORT_NODES = ("Load3DAdvanced", "Get3DComponents", "RemeshMesh", "DecimateMesh", "MeshSmoothNormals", "SaveGLB", "RenderMesh", "SaveImage")

FILES_CACHE_SECONDS = 30
_FILES = {}
_NODES = {}


def prepare_reference(raw, size=1024):
    """Composite a full-body sprite on white and pad it square for CLIP Vision's center crop."""
    try:
        with Image.open(io.BytesIO(raw)) as opened:
            source = opened.convert("RGBA")
    except Exception as exc:
        raise ComfyError(f"Could not read the character sprite for 3D generation: {exc}") from None

    alpha = source.getchannel("A")
    if alpha.getextrema() != (255, 255):
        bounds = alpha.getbbox()
        if bounds:
            source = source.crop(bounds)
    source.thumbnail((int(size * 0.72), int(size * 0.88)), Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (size, size), (255, 255, 255, 255))
    canvas.alpha_composite(source, ((size - source.width) // 2, (size - source.height) // 2))
    output = io.BytesIO()
    canvas.convert("RGB").save(output, "PNG", optimize=True)
    return output.getvalue()


def prepare_head_reference(raw, size=1024):
    """Crop the upper head/neck from a full-body turnaround without changing its source scale.

    Returns (PNG bytes, target-up offset). The crop is a fixed fraction of the figure height so its camera projection
    can be aligned with the same world-space area on the generated mesh.
    """
    try:
        with Image.open(io.BytesIO(raw)) as opened:
            source = opened.convert("RGB")
    except Exception as exc:
        raise ComfyError(f"Could not read a Pixal3D turnaround view for head texturing: {exc}") from None

    pixels = np.asarray(source)
    ring = np.concatenate([pixels[:4].reshape(-1, 3), pixels[-4:].reshape(-1, 3),
                           pixels[:, :4].reshape(-1, 3), pixels[:, -4:].reshape(-1, 3)])
    background = np.median(ring, axis=0).astype(np.int16)
    mask = np.abs(pixels.astype(np.int16) - background).max(axis=2) > 22
    rows = np.where(mask.sum(axis=1) >= 3)[0]
    cols = np.where(mask.sum(axis=0) >= 3)[0]
    if not len(rows) or not len(cols):
        raise ComfyError("Could not find the figure in a Pixal3D turnaround view, so the head texture crop could not be made")

    x0, x1 = int(cols[0]), int(cols[-1]) + 1
    y0, y1 = int(rows[0]), int(rows[-1]) + 1
    figure_height = y1 - y0
    side = max(8, round(figure_height * PIXAL_HEAD_CROP_FRACTION))
    center_x = (x0 + x1) / 2.0
    center_y = y0 + figure_height * (PIXAL_HEAD_CROP_TOP_FRACTION + PIXAL_HEAD_CROP_FRACTION / 2.0)
    left, top = round(center_x - side / 2), round(center_y - side / 2)

    # Pad at image edges rather than shrinking the crop; the crop's physical scale is part of the camera mapping.
    crop = Image.new("RGB", (side, side), tuple(int(c) for c in background))
    source_box = (max(0, left), max(0, top), min(source.width, left + side), min(source.height, top + side))
    if source_box[2] > source_box[0] and source_box[3] > source_box[1]:
        patch = source.crop(source_box)
        crop.paste(patch, (source_box[0] - left, source_box[1] - top))
    crop = crop.resize((int(size), int(size)), Image.Resampling.LANCZOS)

    output = io.BytesIO()
    crop.save(output, "PNG", optimize=True)
    target_up = 0.5 - (center_y - y0) / figure_height
    return output.getvalue(), float(target_up)


def prepare_portrait_head_reference(raw, size=1024):
    """Make a front head-texture view from the native portrait instead of an upscaled sheet crop.

    NPC portraits are square, head-and-shoulders images. A centered crop covering about 61% of the source keeps
    the whole hairstyle, face, ears, and upper neck while retaining considerably more facial pixels than the
    low-resolution model-sheet views. The crop is upscaled to the same 1024-square input used by Pixal3D.
    """
    try:
        with Image.open(io.BytesIO(raw)) as opened:
            source = opened.convert("RGB")
    except Exception as exc:
        raise ComfyError(f"Could not read the NPC portrait for Pixal3D face texturing: {exc}") from None

    side = max(8, round(min(source.size) * 0.61))
    left = round((source.width - side) / 2)
    top = 0
    background = tuple(int(c) for c in np.asarray(source).reshape(-1, 3)[0])
    crop = Image.new("RGB", (side, side), background)
    box = (max(0, left), max(0, top), min(source.width, left + side), min(source.height, top + side))
    if box[2] > box[0] and box[3] > box[1]:
        crop.paste(source.crop(box), (box[0] - left, box[1] - top))
    crop = crop.resize((int(size), int(size)), Image.Resampling.LANCZOS)
    output = io.BytesIO()
    crop.save(output, "PNG", optimize=True)
    return output.getvalue()


def quality_choices():
    """Descriptions and estimates for Pixal3D's per-run quality selector."""
    return [{"id": key, "label": value["label"], "summary": value["summary"], "estimate": value["estimate"]}
            for key, value in PIXAL_QUALITIES.items()]


def clean_quality(value):
    """Return a supported Pixal3D detail preset, defaulting existing recipes to the tested baseline."""
    return value if isinstance(value, str) and value in PIXAL_QUALITIES else "medium"


def face_source_choices():
    """Descriptions for Pixal3D's front-face texture reference selector."""
    return [{"id": key, **value} for key, value in PIXAL_FACE_SOURCES.items()]


def hunyuan_source_choices():
    """Descriptions for the picture Hunyuan3D is made from."""
    return [{"id": key, **value} for key, value in HUNYUAN_SOURCES.items()]


def clean_hunyuan_source(value):
    """Return a supported Hunyuan3D source picture, defaulting existing recipes to the sprite."""
    return value if isinstance(value, str) and value in HUNYUAN_SOURCES else "sprite"


def hunyuan_detail_choices():
    """Descriptions for how fine Hunyuan3D's shape is."""
    return [{"id": key, "label": value["label"], "summary": value["summary"]} for key, value in HUNYUAN_DETAILS.items()]


def clean_hunyuan_detail(value):
    """Return a supported Hunyuan3D shape detail, defaulting existing recipes to the tested 256 grid."""
    return value if isinstance(value, str) and value in HUNYUAN_DETAILS else "standard"


def cut_out(picture):
    """A model-sheet picture (plain background) as RGBA with the background transparent, for prepare_reference."""
    from .paint3d import foreground_mask
    rgb = np.asarray(Image.open(io.BytesIO(picture)).convert("RGB"))
    rgba = np.dstack([rgb, foreground_mask(rgb).astype(np.uint8) * 255])
    out = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(out, "PNG")
    return out.getvalue()


def clean_face_source(value):
    """Return a supported Pixal3D front-face source, defaulting to the consistent turnaround crop."""
    return value if isinstance(value, str) and value in PIXAL_FACE_SOURCES else "turnaround"


def graph(image, npc_id, job_id, seed, detail="standard"):
    """The same core Hunyuan3D v2.1 shape graph used in ComfyUI, with a saved GLB and a small preview render."""
    octree = HUNYUAN_DETAILS[clean_hunyuan_detail(detail)]["octree"]
    prefix = f"npcforge/{npc_id}-{job_id}"
    return {
        "1": {"class_type": "ImageOnlyCheckpointLoader", "inputs": {"ckpt_name": CHECKPOINT}},
        "2": {"class_type": "LoadImage", "inputs": {"image": image}},
        "3": {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": ["1", 0], "shift": 1.0, "sampling": "flow"}},
        "4": {"class_type": "EmptyLatentHunyuan3Dv2", "inputs": {"resolution": 4096, "batch_size": 1}},
        "6": {"class_type": "Hunyuan3Dv2Conditioning", "inputs": {"clip_vision_output": ["13", 0]}},
        "7": {"class_type": "KSampler", "inputs": {
            "model": ["3", 0], "positive": ["6", 0], "negative": ["6", 1], "latent_image": ["4", 0],
            "seed": int(seed if seed is not None else secrets.randbits(64)), "steps": 30, "cfg": 5.0,
            "sampler_name": "euler", "scheduler": "normal", "denoise": 1.0}},
        "8": {"class_type": "VAEDecodeHunyuan3D", "inputs": {
            "samples": ["7", 0], "vae": ["1", 2], "num_chunks": 8000, "octree_resolution": octree}},
        "9": {"class_type": "VoxelToMesh", "inputs": {"voxel": ["8", 0], "algorithm": "surface net", "threshold": 0.6}},
        "10": {"class_type": "SaveGLB", "inputs": {"mesh": ["9", 0], "filename_prefix": prefix}},
        "11": {"class_type": "RenderMesh", "inputs": {
            "mesh": ["9", 0], "mode": "auto", "width": 512, "height": 512, "background": "#F5F5F5"}},
        "12": {"class_type": "SaveImage", "inputs": {"images": ["11", 0], "filename_prefix": prefix + "-preview"}},
        "13": {"class_type": "CLIPVisionEncode", "inputs": {
            "clip_vision": ["1", 1], "image": ["2", 0], "crop": "center"}},
    }


def choices():
    """What the UI offers in the local 3D generator selector."""
    return [{"id": key, "label": value["label"], "note": value["note"]} for key, value in GENERATORS.items()]


def clean(value):
    """Return a known local 3D generator, defaulting old jobs and recipes to Hunyuan."""
    return value if isinstance(value, str) and value in GENERATORS else "hunyuan21"


def _endpoint(comfy):
    return getattr(comfy, "base", None) or id(comfy)


def _missing_nodes(comfy, wanted):
    """The node names of `wanted` this ComfyUI does not have (none when it cannot be asked), from a short-lived cache."""
    endpoint, now = _endpoint(comfy), time.monotonic()
    node_cache = _NODES.get(endpoint)
    try:
        if not node_cache or now - node_cache[0] >= FILES_CACHE_SECONDS:
            node_cache = (now, set(comfy.get_json("/object_info", timeout=20)))
            _NODES[endpoint] = node_cache
        return [f"nodes/{name}" for name in wanted if name not in node_cache[1]]
    except Exception:
        return []


_UNKNOWN = object()
_INFO = {}


def _node_info(comfy, node):
    """One node's description from ComfyUI (small, unlike the list of every node): None when ComfyUI has no such node, _UNKNOWN
    when it cannot be asked."""
    key, now = (_endpoint(comfy), node), time.monotonic()
    cached = _INFO.get(key)
    if not cached or now - cached[0] >= FILES_CACHE_SECONDS:
        try:
            cached = (now, (comfy.get_json(f"/object_info/{node}", timeout=10) or {}).get(node))
        except Exception:
            cached = (now, _UNKNOWN)
        _INFO[key] = cached
    return cached[1]


def _missing_details(comfy, spec):
    """What a generator needs that only a node's own description tells: `choices` (node, input, option) whose list lacks the option
    (a model the pack has not got), `inputs` (node, input) the node lacks (an older pack) and `outputs` (node, type) it does not
    give (an older copy of NPC Forge's own node, which ComfyUI keeps until it is restarted). A node that is missing altogether is
    already reported by the node list."""
    missing = []
    for node, name, wanted in spec.get("choices", ()):
        info = _node_info(comfy, node)
        if info is None or info is _UNKNOWN:
            continue
        entry = {**(info.get("input", {}).get("required") or {}), **(info.get("input", {}).get("optional") or {})}.get(name)
        if not entry:
            continue
        options = entry[1].get("options") if len(entry) > 1 and isinstance(entry[1], dict) and "options" in entry[1] else entry[0]
        if isinstance(options, list) and wanted not in options:
            missing.append(f"models/{wanted}")
    for node, name in spec.get("inputs", ()):
        info = _node_info(comfy, node)
        if info is None or info is _UNKNOWN:
            continue
        have = {**(info.get("input", {}).get("required") or {}), **(info.get("input", {}).get("optional") or {})}
        if name not in have:
            missing.append(f"nodes/{node}.{name}")
    for node, kind in spec.get("outputs", ()):
        info = _node_info(comfy, node)
        if info is None or info is _UNKNOWN:
            continue
        if kind not in (info.get("output") or []):
            missing.append(f"nodes/{node} (an older copy: install the current one with scripts/install_pixal_head_node.py --trellis2, then restart ComfyUI)")
    return missing


def availability(comfy):
    """{generator id: [missing model paths or ComfyUI node names]} for the Models card."""
    endpoint = _endpoint(comfy)
    now = time.monotonic()
    out = {}
    for key, spec in GENERATORS.items():
        missing = []
        for folder, filename in spec["files"]:
            cache_key = (endpoint, folder)
            cached = _FILES.get(cache_key)
            try:
                if not cached or now - cached[0] >= FILES_CACHE_SECONDS:
                    cached = (now, comfy.get_json(f"/models/{folder}", timeout=10))
                    _FILES[cache_key] = cached
                if filename not in cached[1]:
                    missing.append(f"{folder}/{filename}")
            except Exception:
                pass
        missing.extend(_missing_nodes(comfy, spec["nodes"]))
        missing.extend(_missing_details(comfy, spec))
        out[key] = missing
    return out


def import_availability(comfy):
    """What importing a 3D model needs that this ComfyUI lacks: {"missing": [node names]} (ComfyUI's own mesh nodes)."""
    return {"missing": _missing_nodes(comfy, IMPORT_NODES)}


def _pixal_shape(cond, settings, seed):
    """Nodes 41-54: the structure, shape and upsample samplers and the texture stage, conditioned by the multi-view
    conditioning node `cond` (a node id) and the four Pixal3D model loaders 0-3."""
    return {
        "41": {"class_type": "CFGOverride", "inputs": {"model": ["0", 0], "cfg": 1.0, "start_percent": 0.667, "end_percent": 1.0}},
        "42": {"class_type": "RescaleCFG", "inputs": {"model": ["41", 0], "multiplier": 0.7}},
        "43": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["42", 0], "shift": 5.0}},
        "44": {"class_type": "EmptyTrellis2LatentStructure", "inputs": {"batch_size": 1}},
        "45": {"class_type": "KSampler", "inputs": {
            "model": ["43", 0], "positive": [cond, 0], "negative": [cond, 1], "latent_image": ["44", 0],
            "seed": seed, "steps": settings["shape_steps"][0], "cfg": 7.5, "sampler_name": "euler", "scheduler": "normal", "denoise": 1.0}},
        "46": {"class_type": "VaeDecodeStructureTrellis2", "inputs": {"samples": ["45", 0], "vae": ["2", 0], "resolution": "32"}},
        "47": {"class_type": "Trellis2ShapeStage", "inputs": {"positive": [cond, 0], "negative": [cond, 1], "voxel": ["46", 0]}},
        "48": {"class_type": "CFGOverride", "inputs": {"model": ["0", 0], "cfg": 1.0, "start_percent": 0.769, "end_percent": 1.0}},
        "49": {"class_type": "RescaleCFG", "inputs": {"model": ["48", 0], "multiplier": 0.5}},
        "50": {"class_type": "KSampler", "inputs": {
            "model": ["49", 0], "positive": ["47", 0], "negative": ["47", 1], "latent_image": ["47", 2],
            "seed": (seed + 1) % (2 ** 64), "steps": settings["shape_steps"][1], "cfg": 7.5, "sampler_name": "euler", "scheduler": "normal", "denoise": 1.0}},
        "51": {"class_type": "Trellis2UpsampleStage", "inputs": {
            "positive": ["47", 0], "negative": ["47", 1], "shape_latent": ["50", 0], "vae": ["2", 0],
            "target_resolution": settings["upsample"]}},
        "52": {"class_type": "KSampler", "inputs": {
            "model": ["49", 0], "positive": ["51", 0], "negative": ["51", 1], "latent_image": ["51", 2],
            "seed": (seed + 2) % (2 ** 64), "steps": settings["shape_steps"][2], "cfg": 7.5, "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0}},
        "53": {"class_type": "VaeDecodeShapeTrellis", "inputs": {"samples": ["52", 0], "vae": ["2", 0]}},
        "54": {"class_type": "Trellis2TextureStage", "inputs": {"positive": ["51", 0], "negative": ["51", 1], "shape_latent": ["52", 0]}},
    }


def remesh_inputs(settings):
    """RemeshMesh's inputs but the mesh: it rebuilds the surface as one closed, evenly spaced skin, which closes the holes and
    smooths the voxel steps a generator leaves. The same settings finish a Pixal3D model and an imported one."""
    return {
        "resolution": settings["remesh"],
        # ComfyUI V3 DynamicCombo prompt inputs are flattened by dotted path, then rebuilt for execute().
        "sign_mode": "udf", "sign_mode.qef": False, "sign_mode.drop_inverted_components": False,
        "sign_mode.drop_enclosed_components": False,
        "band": 1.0, "project_back": 0.0, "fix_poles": False, "smooth_iters": 20,
        "drop_small_components": 0.01, "precluster_max_verts": 20_000_000}


def mesh_tail(mesh, settings, prefix, base):
    """Six nodes numbered from `base`: remesh `mesh` (a [node id, output] pair), simplify it to the setting's face count,
    smooth its normals, save the GLB and a small render of it. Returns the nodes; the GLB is node base+3."""
    ids = [str(base + i) for i in range(6)]
    return {
        ids[0]: {"class_type": "RemeshMesh", "inputs": {"mesh": mesh, **remesh_inputs(settings)}},
        ids[1]: {"class_type": "DecimateMesh", "inputs": {"mesh": [ids[0], 0], "target_face_count": settings["faces"],
                                                           "placement_mode": "midpoint"}},
        ids[2]: {"class_type": "MeshSmoothNormals", "inputs": {"mesh": [ids[1], 0], "crease_angle": 180.0}},
        ids[3]: {"class_type": "SaveGLB", "inputs": {"mesh": [ids[2], 0], "filename_prefix": prefix}},
        ids[4]: {"class_type": "RenderMesh", "inputs": {"mesh": [ids[2], 0], "mode": "auto", "width": 512, "height": 512,
                                                         "background": "#F5F5F5"}},
        ids[5]: {"class_type": "SaveImage", "inputs": {"images": [ids[4], 0], "filename_prefix": prefix + "-preview"}},
    }


def import_detail_choices():
    """How many faces a cleaned import keeps: the remesh grid and face count of Pixal3D's Low, Medium and High (the textures and
    the time those names also stand for do not apply: nothing is generated or baked)."""
    def count(faces):
        return f"{faces // 1_000_000}M" if faces % 1_000_000 == 0 else f"{faces // 1000}k"

    return [{"id": key, "label": f"{key.capitalize()} · about {count(value['faces'])} faces",
             "summary": f"Closed on a {value['remesh']} grid, then simplified to {value['faces']:,} faces."}
            for key, value in PIXAL_QUALITIES.items()]


def import_graph(model_file, npc_id, job_id, quality="medium"):
    """A 3D model made elsewhere (a GLB that is already in one of ComfyUI's folders; `model_file` is the value ComfyUI's
    Load 3D node takes: `name.glb [output]`, `name.glb [temp]` or `3d/name.glb`) through the mesh clean-up a Pixal3D model
    ends with. Measured on a raw Trellis2 shape of 10 million faces: 25 seconds to a closed 700k-face mesh."""
    settings = PIXAL_QUALITIES[clean_quality(quality)]
    g = {"1": {"class_type": "Load3DAdvanced", "inputs": {"model_file": model_file, "viewport_state": {}, "width": 1024, "height": 1024}},
         "2": {"class_type": "Get3DComponents", "inputs": {"model_3d": ["1", 0]}}}
    g.update(mesh_tail(["2", 0], settings, f"npcforge/import-{npc_id}-{job_id}", 3))
    return g


IMPORT_OUTPUTS = ("6", "8")           # import_graph's saved GLB and its preview picture (mesh_tail's base + 3 and + 5)
TRELLIS2_OUTPUTS = ("82", "84")       # the same two in trellis2_graph


def trellis2_graph(images, npc_id, job_id, seed, quality="medium"):
    """The Trellis2 node pack's multi-view route (its example MeshOnly_MultiView_Pixal3D, with its sampler settings) from the model
    sheet's four pictures, ending in the same mesh clean-up as an import. `images` is {sheet view: ComfyUI image name}.

    Each picture is cut out, cropped square with a 10% margin and given an alpha channel, as for Pixal3D; azimuths 0, 90, 180
    and 270 degrees are the sheet's front, left, back and right (ComfyUI's own camera convention, which the pack shares). The
    pack's own decode node is NOT used: it ends with a CuMesh hole fill that crashed the GPU on ROCm for Windows (the driver
    reset, ComfyUI aborted), so NPC Forge's DECODE_NODE does the same decode without it and hands the mesh on to ComfyUI's
    remesh, which closes the holes. Measured on Airi (R9700): 232 s for 7.5 million faces, then 25 s to 695k."""
    settings = PIXAL_QUALITIES[clean_quality(quality)]
    seed = int(seed if seed is not None else secrets.randbits(31)) % (2 ** 31)
    g = {"1": {"class_type": "LoadBackgroundRemovalModel", "inputs": {"bg_removal_name": "birefnet.safetensors"}}}
    views = []
    for index, view in enumerate(PIXAL_INPUTS.values()):
        load, matte, crop, matte_crop, invert, join = (str(base + index) for base in (10, 20, 30, 40, 50, 60))
        g[load] = {"class_type": "LoadImage", "inputs": {"image": images[view]}}
        g[matte] = {"class_type": "RemoveBackground", "inputs": {"bg_removal_model": ["1", 0], "image": [load, 0]}}
        g[crop] = {"class_type": "ImageCropToMask", "inputs": {
            "images": [load, 0], "masks": [matte, 0], "width": 1024, "height": 1024,
            "pad_factor": RIG_PAD, "grow_mask": 0, "background": "#000000"}}
        g[matte_crop] = {"class_type": "RemoveBackground", "inputs": {"bg_removal_model": ["1", 0], "image": [crop, 0]}}
        g[invert] = {"class_type": "InvertMask", "inputs": {"mask": [matte_crop, 0]}}
        g[join] = {"class_type": "JoinImageWithAlpha", "inputs": {"image": [crop, 0], "alpha": [invert, 0]}}
        views.append(join)
    g["70"] = {"class_type": "BatchImagesNode", "inputs": {f"images.image{i}": [join, 0] for i, join in enumerate(views)}}
    g["71"] = {"class_type": "Trellis2Pixal3DMultiViewConfig", "inputs": {
        "images": ["70", 0], "azimuths": "0,90,180,270", "elevations": "0,0,0,0", "fov": RIG_FOV, "fov_unit": "deg",
        "mesh_scale": 1.0, "distance": 0.0, "remove_background": False, "invert_mask": False, "framing": "auto"}}
    g["72"] = {"class_type": "Trellis2LoadModel", "inputs": {
        "modelname": TRELLIS2_MODEL, "backend": "sdpa", "device": "cuda", "low_vram": True, "keep_models_loaded": False,
        "conv_backend": "flex_gemm", "sparse_backend": "sdpa", "use_reconviagen": False, "pixal3d_multiview": True}}
    g["73"] = {"class_type": "Trellis2PreProcessImage", "inputs": {"image": [views[0], 0], "padding": 5, "remove_background": False,
                                                                   "max_size": 1024}}
    g["74"] = {"class_type": "Trellis2ImageCondGenerator", "inputs": {"pipeline": ["72", 0], "image": ["73", 0], "max_views": 16}}
    views_in = {"image": ["73", 0], "moge_camera_config": ["71", 1], "pixal3d_mv_views": ["71", 0]}
    g["75"] = {"class_type": "Trellis2SparseGenerator", "inputs": {
        "pipeline": ["74", 2], "image_cond": ["74", 0], "seed": seed, "sparse_structure_steps": 25,
        "sparse_structure_guidance_strength": 7.5, "sparse_structure_guidance_rescale": 0.7, "sparse_structure_rescale_t": 5,
        "sparse_structure_sampler": "heun", "sparse_structure_resolution": 32, "sparse_structure_guidance_interval_start": 0.6,
        "sparse_structure_guidance_interval_end": 1, "fill_holes": True, "hole_iterations": 1, "verbose": False, "dino_lock": 0.65,
        "dino_substeps": 2, "hole_fill_algorithm": "flood_fill", "dino_foundation_cap": 0.92, "keep_only_shell": True, **views_in}}
    g["76"] = {"class_type": "Trellis2ShapeGenerator", "inputs": {
        "pipeline": ["75", 2], "image_cond": ["74", 0], "coords": ["75", 0], "resolution": 512, "shape_steps": 12,
        "shape_guidance_strength": 7.5, "shape_guidance_rescale": 0.5, "shape_rescale_t": 3, "shape_sampler": "heun",
        "shape_guidance_interval_start": 0.6, "shape_guidance_interval_end": 1, "verbose": False, "dino_lock": 0.65,
        "dino_substeps": 2, "dino_foundation_cap": 0.92, **views_in}}
    g["77"] = {"class_type": "Trellis2ShapeCascadeGenerator", "inputs": {
        "pipeline": ["76", 2], "image_cond": ["74", 1], "shape_slat": ["76", 0], "from_resolution": ["76", 1], "to_resolution": 1024,
        "sparse_structure_resolution": ["75", 1], "max_num_tokens": 999999, "shape_steps": 12, "shape_guidance_strength": 7.5,
        "shape_guidance_rescale": 0.5, "shape_rescale_t": 3, "shape_sampler": "heun", "shape_guidance_interval_start": 0.6,
        "shape_guidance_interval_end": 1, "verbose": False, "dino_lock": 0.65, "dino_substeps": 2, "dino_foundation_cap": 0.92,
        **views_in}}
    g["78"] = {"class_type": DECODE_NODE, "inputs": {"pipeline": ["77", 2], "shape_slat": ["77", 0], "resolution": ["77", 1],
                                                    "use_tiled_decoder": True}}
    g.update(mesh_tail(["78", 1], settings, f"npcforge/{npc_id}-{job_id}", 79))
    return g


def _pixal_finish(positive, negative, settings, seed, prefix):
    """Nodes 55-69: the texture sampler (conditioned by `positive`/`negative`, [node id, output] pairs), the mesh, UVs, baked
    maps and the saved GLB with its preview render."""
    return {
        "55": {"class_type": "KSampler", "inputs": {
            "model": ["0", 0], "positive": positive, "negative": negative, "latent_image": ["54", 2],
            "seed": (seed + 3) % (2 ** 64), "steps": settings["shape_steps"][3], "cfg": 1.0, "sampler_name": "euler", "scheduler": "normal", "denoise": 1.0}},
        "56": {"class_type": "VaeDecodeTextureTrellis", "inputs": {"samples": ["55", 0], "vae": ["3", 0], "shape_subdivides": ["53", 1]}},
        "57": {"class_type": "GetMeshInfo", "inputs": {"mesh": ["53", 0]}},
        "58": {"class_type": "RemeshMesh", "inputs": {"mesh": ["57", 0], **remesh_inputs(settings)}},
        "59": {"class_type": "DecimateMesh", "inputs": {"mesh": ["58", 0], "target_face_count": settings["faces"],
                                                               "placement_mode": "midpoint"}},
        "60": {"class_type": "MeshSmoothNormals", "inputs": {"mesh": ["59", 0], "crease_angle": 180.0}},
        "61": {"class_type": "UnwrapMesh", "inputs": {"mesh": ["60", 0], "segmenter": "pec", "resolution": settings["uv"],
                                                              "padding": 1, "weld_distance": 0.0002}},
        "62": {"class_type": "BakeTextureFromVoxel", "inputs": {"mesh": ["61", 0], "voxel_colors": ["56", 0],
                                                                     "reference_mesh": ["58", 0], "texture_size": settings["texture"]}},
        "63": {"class_type": "BakeNormalMapFromMesh", "inputs": {"low_poly": ["61", 0], "high_poly": ["58", 0],
                                                                      "resolution": settings["normal"], "cage_distance": 0.05,
                                                                      "ignore_backfaces": True}},
        "64": {"class_type": "BakeAmbientOcclusion", "inputs": {"low_poly": ["61", 0], "high_poly": ["58", 0],
                                                                    "resolution": settings["ao"], "samples": settings["ao_samples"], "max_distance": 0.71,
                                                                    "strength": 1.0, "bias": 0.01}},
        "65": {"class_type": "ApplyTextureToMesh", "inputs": {"mesh": ["61", 0], "base_color": ["62", 0],
                                                                   "metallic": ["62", 1], "roughness": ["62", 2],
                                                                   "occlusion": ["64", 0], "normal_map": ["63", 0]}},
        "66": {"class_type": "MeshSmoothNormals", "inputs": {"mesh": ["65", 0], "crease_angle": 180.0}},
        "67": {"class_type": "SaveGLB", "inputs": {"mesh": ["66", 0], "filename_prefix": prefix}},
        "68": {"class_type": "RenderMesh", "inputs": {"mesh": ["66", 0], "mode": "auto", "width": 512, "height": 512,
                                                            "background": "#F5F5F5"}},
        "69": {"class_type": "SaveImage", "inputs": {"images": ["68", 0], "filename_prefix": prefix + "-preview"}},
    }


def _pixal_loaders(background_removal=True):
    g = {
        "0": {"class_type": "UNETLoader", "inputs": {"unet_name": "pixal3d_multiview_int8_convrot.safetensors", "weight_dtype": "default"}},
        "1": {"class_type": "CLIPVisionLoader", "inputs": {"clip_name": "dino_v3_L_naf_fp32.safetensors"}},
        "2": {"class_type": "VAELoader", "inputs": {"vae_name": "trellis_2_shape_vae_bf16.safetensors"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": "trellis_2_texture_vae_bf16.safetensors"}},
    }
    if background_removal:
        g["4"] = {"class_type": "LoadBackgroundRemovalModel", "inputs": {"bg_removal_name": "birefnet.safetensors"}}
    return g


def pixal3d_graph(images, head_images, head_target_up, npc_id, job_id, seed, quality="medium"):
    """Build the shape, UV, PBR-texture and GLB graph from the supplied four view images.

    Shape uses the installed full-body Pixal3D workflow. Texturing keeps its full-body conditioning and replaces
    projection features only inside four head-sized camera frusta, using the local NPC Forge ComfyUI extension.
    """
    settings = PIXAL_QUALITIES[clean_quality(quality)]
    prefix = f"npcforge/{npc_id}-{job_id}"
    seed = int(seed if seed is not None else secrets.randbits(32)) % (2 ** 64)
    g = _pixal_loaders()
    cropped = {}
    for index, (socket, view) in enumerate(PIXAL_INPUTS.items()):
        load, remove, crop = str(10 + index), str(20 + index), str(30 + index)
        g[load] = {"class_type": "LoadImage", "inputs": {"image": images[view]}}
        g[remove] = {"class_type": "RemoveBackground", "inputs": {"bg_removal_model": ["4", 0], "image": [load, 0]}}
        g[crop] = {"class_type": "ImageCropToMask", "inputs": {
            "images": [load, 0], "masks": [remove, 0], "width": 1024, "height": 1024,
            "pad_factor": 1.1, "grow_mask": 0, "background": "#000000"}}
        cropped[socket] = [crop, 0]

    head_inputs = {}
    for index, (socket, view) in enumerate(PIXAL_INPUTS.items()):
        load, remove = str(70 + index), str(74 + index)
        g[load] = {"class_type": "LoadImage", "inputs": {"image": head_images[view]}}
        g[remove] = {"class_type": "RemoveBackground", "inputs": {"bg_removal_model": ["4", 0], "image": [load, 0]}}
        head_inputs[f"{socket}_image"] = [load, 0]
        head_inputs[f"{socket}_mask"] = [remove, 0]

    g["40"] = {"class_type": "Pixal3DMultiViewConditioning", "inputs": {
        "clip_vision_model": ["1", 0], "fov": 20.0,
        **{socket: cropped[socket] for socket in ("front", "left", "back", "right")}}}
    g.update(_pixal_shape("40", settings, seed))
    g["78"] = {"class_type": HEAD_TEXTURE_NODE, "inputs": {
        "clip_vision_model": ["1", 0], "body_positive": ["54", 0], "body_negative": ["54", 1],
        "shape_latent": ["54", 2], **head_inputs,
        "head_target_up": float(head_target_up), "head_crop_fraction": PIXAL_HEAD_CROP_FRACTION,
        "body_fov": 20.0}}
    g.update(_pixal_finish(["78", 0], ["78", 1], settings, seed, prefix))
    return g


def clean_head_quality(value):
    """Return a supported detailed-head setting, defaulting to the tested one."""
    return value if isinstance(value, str) and value in HEAD_QUALITIES else "medium"


def head_quality_choices():
    return [{"id": key, "label": value["label"], "summary": value["summary"], "estimate": value["estimate"]}
            for key, value in HEAD_QUALITIES.items()]


def head_timeout(quality):
    return HEAD_QUALITIES[clean_head_quality(quality)]["timeout"]


def pixal3d_head_graph(images, npc_id, job_id, seed, quality="medium", shape_images=None):
    """The detailed head: Pixal3D multi-view shape, texture and GLB from four head pictures that head3d.frame() has already cut
    out and framed like the rig (one scale for all, the figure on black), so they go to the conditioning as they are.

    `images` {view: ComfyUI image name} give the texture; `shape_images`, when given, give the shape instead (pictures with
    something taken out of the way of the shape stages, like eye make-up read as sculpted rims); the texture stage then has
    a conditioning of its own."""
    settings = HEAD_QUALITIES[clean_head_quality(quality)]
    prefix = f"npcforge/head-{npc_id}-{job_id}"
    seed = int(seed if seed is not None else secrets.randbits(32)) % (2 ** 64)
    g = _pixal_loaders(background_removal=False)

    def conditioning(node, names, base):
        for index, (socket, view) in enumerate(PIXAL_INPUTS.items()):
            g[str(base + index)] = {"class_type": "LoadImage", "inputs": {"image": names[view]}}
        g[node] = {"class_type": "Pixal3DMultiViewConditioning", "inputs": {
            "clip_vision_model": ["1", 0], "fov": RIG_FOV,
            **{socket: [str(base + index), 0] for index, socket in enumerate(PIXAL_INPUTS)}}}

    conditioning("40", shape_images or images, 10)
    g.update(_pixal_shape("40", settings, seed))
    if shape_images:
        conditioning("39", images, 20)
        g["54"]["inputs"]["positive"], g["54"]["inputs"]["negative"] = ["39", 0], ["39", 1]
    g.update(_pixal_finish(["54", 0], ["54", 1], settings, seed, prefix))
    return g


def saved_file(entry, output_key, label, node=None):
    """Return the first saved file record produced by a ComfyUI output node; `node` names the one to read when a graph has other
    nodes (a custom pack's) that also report pictures or files."""
    outputs = entry.get("outputs", {})
    for node_out in ([outputs.get(node) or {}] if node is not None else outputs.values()):
        files = node_out.get(output_key) or []
        if files:
            return files[0]
    raise ComfyError(f"ComfyUI finished the job but produced no {label}")
