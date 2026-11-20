"""A skeleton, skin weights and animation clips for a character's 3D model, made by Blender running headless.

The Blender side is npcgen/blender/rig_humanoid.py, on the CPU. Blender is found from config.json "blender",
NPCGEN_BLENDER, the standard install folders, or PATH.

The joints are placed from pose keypoints when a `keypoints` finder is given (npcgen/posekeys.py: SDPose in ComfyUI, the one
GPU step): Blender renders the model from the front and both sides (npcgen/blender/pose_views.py), SDPose finds the joints
in the renders, and the rig script turns them into 3D. Without it they come from the model's front outline, as before,
which misses arms that touch the clothes (see posekeys.py).

Skeletons (SKELETONS): "low" is 23 bones; "medium" is 60, adding fingers, a jaw, arm twist bones and a prop bone in each
palm, and needs the keypoints. The low skeleton's bones are the same in both.

Clips (CLIP_SETS): "basic" is Idle, Walk and Wave, keyframed in the rig script; "library" copies every clip of Quaternius'
Universal Animation Library 1 and 2 (CC0, installed by scripts/install_animations.py into animations/quaternius/) onto the
skeleton (npcgen/blender/rig_library.py), with our Wave kept. "auto" (the default) is the library when it is installed.
"""
import glob
import json
import os
import pathlib
import re
import shutil
import subprocess
import tempfile
import time

from . import posekeys
from .comfy import Cancelled, ComfyError, beat

SCRIPT = pathlib.Path(__file__).resolve().parent / "blender" / "rig_humanoid.py"
VIEWS_SCRIPT = pathlib.Path(__file__).resolve().parent / "blender" / "pose_views.py"
CLIPS = ("Idle", "Walk", "Wave")
SKELETONS = {
    "low": {"label": "Low (23 bones)", "bones": 23,
            "note": "Body, arms, legs and head: walking, standing and gestures. The hands move as one piece and the face "
                    "is still."},
    "medium": {"label": "Medium (60 bones)", "bones": 60,
               "note": "Adds three bones per finger, a jaw for talking, twist bones in the arms and a prop bone in each palm "
                       "to hold things by. Needs SDPose in ComfyUI to find the fingers and the face."},
}


def clean_skeleton(value):
    """A skeleton id from a request ("low" when missing or unknown)."""
    return value if value in SKELETONS else "low"


def skeleton_choices():
    """What the page lists: [{id, label, bones, note}]."""
    return [{"id": k, **v} for k, v in SKELETONS.items()]


LIBRARY_DIR = pathlib.Path(__file__).resolve().parents[1] / "animations" / "quaternius"
LIBRARY_FILES = ("UAL1_Standard.glb", "UAL2_Standard.glb")
LIBRARY = {"name": "Universal Animation Library 1 and 2 (Standard), by Quaternius", "licence": "CC0 1.0",
           "url": "https://quaternius.com/packs/universalanimationlibrary.html"}
CLIP_SETS = {
    "basic": {"label": "Basic: Idle, Walk, Wave",
              "note": "Three clips keyframed here: standing, walking on the spot and a wave."},
    "library": {"label": "Animation library (85 clips)",
                "note": "Quaternius' Universal Animation Library (CC0) copied onto this skeleton: idle, walk, jog, sprint, "
                        "sit down, sit and talk, stand up, talk, phone, fold arms, drink, pick up, push, farming, dance, "
                        "swim, jumps, sword, pistol and spell moves, and more, with our Wave. Idle and Walk keep those "
                        "names. Adds about 5 MB and 30 seconds."},
}


def library_files(cfg):
    """The animation library's GLB files, or [] when it is not installed (config.json "animation_library" can name
    another folder)."""
    folder = pathlib.Path(cfg.get("animation_library") or LIBRARY_DIR)
    files = [folder / name for name in LIBRARY_FILES]
    return [str(f) for f in files] if all(f.is_file() for f in files) else []


def library_availability(cfg):
    """{"installed", "missing": [...], name, licence, url} for the rig card."""
    installed = bool(library_files(cfg))
    return {"installed": installed, **LIBRARY,
            "missing": [] if installed else ["the animation library (run: python scripts/install_animations.py)"]}


def clean_clips(value):
    """A clip set choice from a request: basic, library, or auto (the library when installed)."""
    return value if value in ("auto", *CLIP_SETS) else "auto"


def clip_set(value, cfg):
    """The clip set a rig gets: auto becomes the library when it is installed, else basic."""
    value = clean_clips(value)
    if value == "auto":
        return "library" if library_files(cfg) else "basic"
    return value


def clip_choices():
    """What the page lists: [{id, label, note}]."""
    return [{"id": k, **v} for k, v in CLIP_SETS.items()]


DEFAULT_HEIGHT_M = 1.7
TIMEOUT_SECONDS = 1200
# Only two-legged bodies fit the humanoid skeleton; the others need their own skeleton templates.
SUPPORTED_MORPHOLOGIES = ("bipedal",)


def _version(path):
    m = re.search(r"Blender (\d+)\.(\d+)", str(path))
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


def find_blender(cfg):
    """The blender executable to use, or None."""
    for configured in (cfg.get("blender"), os.environ.get("NPCGEN_BLENDER")):
        if configured and pathlib.Path(configured).is_file():
            return str(configured)
    installed = []
    for root in {os.environ.get("ProgramFiles"), os.environ.get("ProgramW6432"), r"C:\Program Files"} - {None}:
        installed += glob.glob(os.path.join(root, "Blender Foundation", "Blender *", "blender.exe"))
    if installed:
        return max(installed, key=_version)
    return shutil.which("blender")


def availability(cfg):
    """{"blender": path or None, "missing": [...]} for the Models card."""
    path = find_blender(cfg)
    return {"blender": path, "missing": [] if path else ["Blender (install it from blender.org, or set \"blender\" in config.json)"]}


def unsupported_reason(morphology):
    if morphology in SUPPORTED_MORPHOLOGIES:
        return None
    return (f"Skeletons and animations are made for two-legged characters only so far; this character's body plan is "
            f"'{morphology}'.")


def run(cfg, model_glb, height_m=DEFAULT_HEIGHT_M, cancel=None, timeout=TIMEOUT_SECONDS, shape_glb=None,
        skeleton="low", keypoints=None, clips="auto", hands=None):
    """Rig model_glb (bytes). Returns (rigged GLB bytes, poster PNG bytes, report dict).

    shape_glb: the untouched generator model a cleaned-up game version was made from. Its closed, dense surface is what
    the joints and skin weights are worked out on (the game version has open edges where its inner skin was removed).
    keypoints: a function (folder of renders) -> keypoints (posekeys.finder), or None to place the joints from the
    outline. skeleton: "low" or "medium" (which needs `keypoints`). clips: "basic", "library" or "auto" (CLIP_SETS).
    hands: the model's clean hands (model_3d.refined.hands, npcgen/hands3d.py): their own joints place the wrists and
    finger bones, and their vertices go to the hand bones."""
    skeleton = clean_skeleton(skeleton)
    if skeleton == "medium" and keypoints is None:
        raise ComfyError("The medium skeleton needs SDPose in ComfyUI to find the fingers and the face")
    chosen = clip_set(clips, cfg)
    library = library_files(cfg) if chosen == "library" else []
    if chosen == "library" and not library:
        raise ComfyError("The animation library is not installed: run python scripts/install_animations.py, "
                         "or choose the basic clips")
    with tempfile.TemporaryDirectory(prefix="npcforge-rig-") as tmp:
        tmp = pathlib.Path(tmp)
        src, out, poster, report = tmp / "model.glb", tmp / "rigged.glb", tmp / "poster.png", tmp / "report.json"
        src.write_bytes(model_glb)
        extra = ["--skeleton", skeleton]
        if library:
            extra += ["--library", "|".join(library)]
        if shape_glb is not None:
            (tmp / "shape.glb").write_bytes(shape_glb)
            extra += ["--shape", str(tmp / "shape.glb")]
        if hands:
            (tmp / "hands.json").write_text(json.dumps(hands), "utf-8")
            extra += ["--hands", str(tmp / "hands.json")]
        keys_summary = None
        if keypoints is not None:
            views = tmp / "views"
            run_script(cfg, VIEWS_SCRIPT, [str(src), str(views), "--height", f"{float(height_m):.3f}"], tmp,
                       views / "cams.json", "Rendering the model for the pose detector",
                       "Blender could not render the model for the pose detector", cancel=cancel, timeout=timeout)
            keys = keypoints(views)
            (tmp / "keys.json").write_text(json.dumps(keys), "utf-8")
            extra += ["--keypoints", str(tmp / "keys.json")]
            keys_summary = posekeys.summary(keys)
        run_script(cfg, SCRIPT, [str(src), str(out), "--height", f"{float(height_m):.3f}", "--report", str(report),
                                 "--poster", str(poster), *extra], tmp, out, "Rigging", "Blender could not rig this model",
                   cancel=cancel, timeout=timeout)
        info = json.loads(report.read_text("utf-8")) if report.exists() else {}
        info["clip_set"] = chosen
        if keys_summary:
            info["keypoint_scores"] = keys_summary
        return out.read_bytes(), poster.read_bytes() if poster.exists() else None, info


def run_script(cfg, script, script_args, tmp, out, doing, failed, cancel=None, timeout=TIMEOUT_SECONDS):
    """Run a Blender script headless, stoppable by `cancel`. Raises ComfyError("<failed>: <reason>") unless it exits
    cleanly and writes `out`."""
    blender = find_blender(cfg)
    if not blender:
        raise ComfyError("Blender was not found. Install it from blender.org, or set \"blender\" in config.json to blender.exe.")
    cmd = [blender, "-b", "--factory-startup", "--python", str(script), "--", *script_args]
    log_path = pathlib.Path(tmp) / "blender.log"
    with open(log_path, "wb") as log:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, creationflags=flags)
        started = time.monotonic()
        while proc.poll() is None:
            if cancel is not None and cancel.is_set():
                proc.kill()
                proc.wait()
                raise Cancelled()
            beat(cancel)                                    # (still running: the watchdog's clock)
            if time.monotonic() - started > timeout:
                proc.kill()
                proc.wait()
                raise ComfyError(f"{doing} took longer than {timeout // 60} minutes and was stopped")
            time.sleep(0.5)
    log_text = log_path.read_text("utf-8", errors="replace")
    if proc.returncode != 0 or not pathlib.Path(out).exists():
        reason = _failure(log_text)
        if "needs an A-pose" in reason:             # (the joints are found from the gap under each arm: say how to get one)
            reason += ". Make the model again from a model sheet in an A-pose (the sheet's pose option), or import one whose arms are away from its body"
        raise ComfyError(f"{failed}: " + reason)
    return log_text


def _failure(log_text):
    """The useful last line of a failed Blender run (the script's XXX_ERROR line, a SystemExit message or the exception)."""
    lines = [l.strip() for l in log_text.splitlines() if l.strip()]
    for line in reversed(lines):
        tagged = re.match(r"^[A-Z]+_ERROR (.*)", line)
        if tagged:
            return tagged.group(1)
        if line.startswith("SystemExit") or re.match(r"^\w+(Error|Exception):", line):
            return line
    return lines[-1] if lines else "no output"
