"""Encode exact browser-rendered 3D frames and scene voices as a local MP4."""
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import uuid

from .scene_render import ffmpeg_path, _audio_mix
from .scenes import compile_timeline


MAX_FRAMES_BODY = 80_000_000
MAGIC = b"NPC3D1\0\0"


def _frames(packet, count):
    if len(packet) > MAX_FRAMES_BODY or not packet.startswith(MAGIC) or len(packet) < 12:
        raise ValueError("The browser did not send a valid 3D frame package")
    actual = struct.unpack_from("<I", packet, 8)[0]
    if actual != count:
        raise ValueError(f"Expected {count} rendered frames, received {actual}")
    offset = 12
    for index in range(count):
        if offset + 4 > len(packet):
            raise ValueError(f"Frame {index + 1} is missing")
        size = struct.unpack_from("<I", packet, offset)[0]
        offset += 4
        if not 100 <= size <= 2_000_000 or offset + size > len(packet):
            raise ValueError(f"Frame {index + 1} is invalid")
        frame = packet[offset:offset + size]
        if not frame.startswith(b"\xff\xd8") or not frame.endswith(b"\xff\xd9"):
            raise ValueError(f"Frame {index + 1} is not a JPEG")
        yield frame
        offset += size
    if offset != len(packet):
        raise ValueError("Unexpected data after the rendered frames")


def _audio_args(scene, timeline, scenes):
    inputs, filters = [], []
    for beat in timeline["beats"]:
        voice = beat.get("voice")
        if not voice:
            continue
        path = (scenes.resolve(scene["id"], voice["file"]) if voice["source"] == "scene"
                else scenes.lib.resolve(beat["speaker_id"], voice["file"]))
        index = 1 + len(filters)
        inputs.extend(["-i", str(path)])
        filters.append(f"[{index}:a]adelay={round(beat['start'] * 1000 / timeline['fps'])}:all=1[a{index}]")
    if not filters:
        return ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"], [], "1:a"
    names = "".join(f"[a{index}]" for index in range(1, len(filters) + 1))
    return inputs, ["-filter_complex", ";".join(filters +
                     [f"{names}amix=inputs={len(filters)}:duration=longest:normalize=0,apad[mix]"])], "[mix]"


def publish(scenes, scene_id, revision, fingerprint, packet):
    scene = scenes.get(scene_id)
    if scene.get("mode") != "stage3d":
        raise ValueError("This scene does not use the 3D stage")
    if scene["revision"] != revision:
        raise ValueError("The scene changed during export; reload it and try again")
    for npc_id in scene["cast"]:
        npc = scenes.lib.get(npc_id)
        rig = (npc.get("assets") or {}).get("rig_3d") or {}
        if not rig.get("file") or not scenes.lib.resolve(npc_id, rig["file"]).is_file():
            raise ValueError("All cast members need rigged 3D models to export")
    ffmpeg = ffmpeg_path()
    if not ffmpeg:
        raise ValueError("Install FFmpeg to export MP4 video")
    timeline = compile_timeline(scene, scenes.lib, scenes)
    if timeline["fingerprint"] != fingerprint:
        raise ValueError("Scene assets or voices changed during export; preview and try again")
    token = uuid.uuid4().hex[:12]
    tmp = scenes.resolve(scene_id, f"render/{token}.tmp.mp4")
    output = scenes.resolve(scene_id, f"render/{token}.mp4")
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix="frames-", dir=output.parent) as folder:
            for index, jpeg in enumerate(_frames(packet, timeline["frames"])):
                (Path(folder) / f"{index:06d}.jpg").write_bytes(jpeg)
            audio_path = Path(folder) / 'mix.wav'
            _audio_mix(scene, timeline, scenes, audio_path)
            audio_inputs, audio_filter, audio_map = ['-i', str(audio_path)], [], '1:a'
            duration = timeline["frames"] / timeline["fps"]
            cmd = [ffmpeg, "-y", "-loglevel", "error", "-framerate", str(timeline["fps"]),
                   "-i", str(Path(folder) / "%06d.jpg"), *audio_inputs, *audio_filter,
                   "-map", "0:v", "-map", audio_map, "-frames:v", str(timeline["frames"]),
                   "-t", f"{duration:.3f}", "-c:v", "libx264", "-threads", "4", "-preset", "veryfast",
                   "-crf", "22", "-pix_fmt", "yuv420p", "-r", str(timeline["fps"]),
                   "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(tmp)]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300, check=False)
        if result.returncode or not tmp.is_file() or tmp.stat().st_size < 1024:
            raise ValueError("FFmpeg could not finish the 3D video: " + result.stderr.strip()[-400:])
        os.replace(tmp, output)
        scenes.set_render(scene_id, revision, f"render/{token}.mp4", timeline["fingerprint"])
        return scenes.get(scene_id)["render"]
    finally:
        tmp.unlink(missing_ok=True)
