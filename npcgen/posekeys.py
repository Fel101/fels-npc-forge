"""Pose keypoints from SDPose in ComfyUI, for placing a 3D model's joints (npcgen/rig3d.py, npcgen/blender/rig_keypoints.py).

The skeleton used to be placed from the model's front outline alone. That fails when the arms touch the clothes: on Laura
Foster (a long-sleeved dress) the arm could not be followed past the cuff, so the wrists landed at her elbows, and her
dress hem was taken for her crotch, putting her hips 18 cm low. SDPose (ComfyUI's own SDPoseKeypointExtractor, the
whole-body model: 17 body, 6 feet, 68 face and 2 x 21 hand points) finds the joints in three renders of the model
(front, left, right; npcgen/blender/pose_views.py) and in a close-up of the face, and the rig script turns them into 3D.

Measured 2026-09-26 on Laura, Airi, Clara, Imani and Devi: every joint found, scores 0.9-1.0 in the views that see it; on
Laura and Airi the elbows, wrists, hips, knees and ankles land where the outline rig missed them, on Clara and Imani both
agree. From the front the fingers overlap, from the side each finger is traced joint by joint. A close-up of a hand on its
own (no arm or body around it) gave scores of 0.2-0.4: the model needs the person around the hand, so the hands are read
from the whole-figure renders. The face close-up (about 5x) found jaw, lips, eyes and brows even behind sunglasses.

ComfyUI's node calls the hands the other way round (its "right hand" is the character's left, while its body points are
the right way round); hands here are given to the wrist they start at instead of by their label.
"""
import io
import json

import numpy as np
from PIL import Image

from .comfy import ComfyError

CHECKPOINT = "sdpose_wholebody_fp16.safetensors"   # Comfy-Org/SDPose on Hugging Face, in models/checkpoints
NODE = "SDPoseKeypointExtractor"
DOWNLOAD = "https://huggingface.co/Comfy-Org/SDPose/resolve/main/checkpoints/" + CHECKPOINT
LICENCE = "MIT (SDPose); its network started from Stable Diffusion 2 (CreativeML Open RAIL++-M, commercial use allowed)"
VIEWS = ("front", "left", "right")
FACE_PAD = 2.2                                      # the face close-up is this many times the size of the face points
PARTS = {"pose_keypoints_2d": "body", "foot_keypoints_2d": "feet", "face_keypoints_2d": "face"}


class PoseError(ComfyError):
    """Pose keypoints could not be found. The message is safe to show."""


def graph(images):
    """ComfyUI graph: SDPose's keypoints of each uploaded image in `images` (a list), each as text from a Preview Any."""
    g = {"m": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": CHECKPOINT}}}
    for i, name in enumerate(images):
        g[f"i{i}"] = {"class_type": "LoadImage", "inputs": {"image": name}}
        g[f"k{i}"] = {"class_type": NODE, "inputs": {"model": ["m", 0], "vae": ["m", 2], "image": [f"i{i}", 0],
                                                     "batch_size": 1}}
        g[f"o{i}"] = {"class_type": "PreviewAny", "inputs": {"source": [f"k{i}", 0]}}
    return g


def availability(comfy):
    """{"model": the checkpoint or None, "licence", "missing": [...]} for the page."""
    if comfy is None:
        return {"model": None, "licence": LICENCE, "missing": ["ComfyUI"]}
    missing = []
    try:
        if CHECKPOINT not in (comfy.get_json("/models/checkpoints", timeout=10) or []):
            missing.append(f"SDPose in ComfyUI's models/checkpoints ({CHECKPOINT}, from Comfy-Org/SDPose on Hugging Face)")
        for node in (NODE, "PreviewAny"):
            if node not in (comfy.get_json(f"/object_info/{node}", timeout=10) or {}):
                missing.append(f"a ComfyUI new enough to have the {node} node")
    except ComfyError:
        missing = ["ComfyUI"]
    return {"model": None if missing else CHECKPOINT, "licence": LICENCE, "missing": missing}


def _people(text):
    frames = json.loads(text)
    people = frames[0].get("people") if frames else None
    if not people:
        raise PoseError("SDPose found no person in a render of the model")
    return people[0]


def detect(comfy, pictures, npc_id, cancel=None):
    """{key: PIL image} -> {key: SDPose's person as {part: [[u, v, score], ...]}}, in one ComfyUI run."""
    keys = list(pictures)
    names = []
    for key in keys:
        buf = io.BytesIO()
        pictures[key].save(buf, "PNG")
        names.append(comfy.upload_image(buf.getvalue(), f"{npc_id}_pose_{key}.png"))
    entry = comfy.run_entry(graph(names), cancel=cancel, timeout=600)
    out = {}
    for i, key in enumerate(keys):
        texts = (entry.get("outputs", {}).get(f"o{i}") or {}).get("text") or []
        if not texts:
            raise PoseError("ComfyUI returned no keypoints from SDPose")
        person = _people(texts[0])
        out[key] = {part: np.asarray(person.get(part) or [], dtype=float).reshape(-1, 3).tolist()
                    for part in ("pose_keypoints_2d", "foot_keypoints_2d", "face_keypoints_2d",
                                 "hand_left_keypoints_2d", "hand_right_keypoints_2d")}
    return out


def hands_crossed(person):
    """True when the node's "left hand" is the character's right hand, judged in the front view: each hand is matched
    to the body wrist (left 7, right 4) it starts nearest. (In a side view both hands lie over the one hand that can be
    seen, so the front view's answer is used for all three.)"""
    body, a, b = person["pose_keypoints_2d"], person["hand_left_keypoints_2d"], person["hand_right_keypoints_2d"]
    if not a or not b:
        return False
    wl, wr = np.asarray(body[7][:2]), np.asarray(body[4][:2])
    straight = np.linalg.norm(np.asarray(a[0][:2]) - wl) + np.linalg.norm(np.asarray(b[0][:2]) - wr)
    crossed = np.linalg.norm(np.asarray(a[0][:2]) - wr) + np.linalg.norm(np.asarray(b[0][:2]) - wl)
    return crossed < straight


def face_box(points, size, pad=FACE_PAD):
    """A 3:4 box (x, y, w, h) around the face points that were found, `pad` times their size, or None."""
    pts = np.asarray([p for p in points if p[2] > 0.3])
    if len(pts) < 10:
        return None
    (x0, y0), (x1, y1) = pts[:, :2].min(0), pts[:, :2].max(0)
    h = max(y1 - y0, (x1 - x0) / 0.75) * pad
    return ((x0 + x1) / 2 - h * 0.375, (y0 + y1) / 2 - h / 2, h * 0.75, h)


def crop(picture, box):
    """The box cut from the picture, on light grey where it runs off the edge."""
    x, y, w, h = (int(round(v)) for v in box)
    out = Image.new("RGB", (w, h), (230, 230, 230))
    out.paste(picture.crop((x, y, x + w, y + h)), (0, 0))
    return out


def keypoints(comfy, views_dir, npc_id, cancel=None):
    """The keypoints of the renders in `views_dir` (npcgen/blender/pose_views.py), in the form rig_keypoints.py reads:
    {"cams": {view: camera}, "views": {view: {"body", "feet", "face", "hand_l", "hand_r"[, "face_zoom"]}}} with points
    as [u, v, score] in that view's pixels."""
    cams = json.loads((views_dir / "cams.json").read_text("utf-8"))["cams"]
    pictures = {v: Image.open(views_dir / cams[v]["file"]).convert("RGB") for v in VIEWS}
    found = detect(comfy, pictures, npc_id, cancel)
    crossed = hands_crossed(found["front"])
    views = {}
    for v in VIEWS:
        person = found[v]
        row = {name: person[part] for part, name in PARTS.items()}
        row["face"] = row["face"][:68]                  # the node adds the two eye points again at the end
        left, right = person["hand_left_keypoints_2d"], person["hand_right_keypoints_2d"]
        row["hand_l"], row["hand_r"] = (right, left) if crossed else (left, right)
        views[v] = row
    box = face_box(views["front"]["face"], pictures["front"].size)
    if box is not None:
        zoom = detect(comfy, {"face": crop(pictures["front"], box)}, npc_id, cancel)["face"]["face_keypoints_2d"][:68]
        views["front"]["face_zoom"] = [[u + box[0], v + box[1], s] for u, v, s in zoom]
    return {"cams": cams, "views": views}


def summary(data):
    """Mean scores per part in the views that see them, for the report."""
    v = data["views"]
    mean = lambda pts: round(float(np.mean([p[2] for p in pts])), 2) if pts else None
    return {"body": mean(v["front"]["body"]), "face": mean(v["front"].get("face_zoom") or v["front"]["face"]),
            "hand_l": mean(v["left"]["hand_l"]), "hand_r": mean(v["right"]["hand_r"])}


def finder(comfy, npc_id, cancel=None):
    """A function (views_dir) -> keypoints dict, for rig3d.run(keypoints=...)."""
    return lambda views_dir: keypoints(comfy, views_dir, npc_id, cancel)
