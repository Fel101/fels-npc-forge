"""Bounded CPU renderer for saved scenes. FFmpeg encodes frames; ComfyUI is never called."""
import copy
import math
import os
import pathlib
import queue
import shutil
import subprocess
import tempfile
import threading
import uuid
import wave

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps

from .scenes import BACKDROPS, FPS, compile_timeline
from .voice import character_rig


class RenderCancelled(Exception):
    pass


def ffmpeg_path():
    return shutil.which("ffmpeg")


def _font(size, bold=False):
    names = (["C:/Windows/Fonts/segoeuib.ttf", "DejaVuSans-Bold.ttf"] if bold
             else ["C:/Windows/Fonts/segoeui.ttf", "DejaVuSans.ttf"])
    for name in names:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _fit(path, size, contain=False, top=False):
    with Image.open(path) as source:
        return _fit_image(source, size, contain, top)


def _fit_image(source, size, contain=False, top=False):
    source = source.convert('RGBA')
    source = (ImageOps.contain(source, size, method=Image.Resampling.LANCZOS) if contain else
              ImageOps.fit(source, size, method=Image.Resampling.LANCZOS, centering=(.5, 0. if top else .5)))
    panel = Image.new('RGBA', size, '#314153')
    panel.alpha_composite(source, ((size[0] - source.width)//2, (size[1] - source.height)//2))
    return panel.convert('RGB')


def _text_lines(draw, text, font, width):
    lines, current = [], ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if current and draw.textlength(candidate, font=font) > width:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines[:4]


class FrameMaker:
    def __init__(self, scene, timeline, lib):
        self.scene, self.timeline, self.lib = scene, timeline, lib
        self.size = tuple(timeline["size"])
        self.npcs = {}
        self.images = {}
        self.rig_pixels = {}
        for npc_id in scene["cast"]:
            try:
                self.npcs[npc_id] = lib.get(npc_id)
            except KeyError:
                continue

    def _source(self, npc_id, key):
        npc = self.npcs.get(npc_id)
        if not npc:
            return None
        assets = npc.get("assets") or {}
        rel = None
        if key == "sprite":
            rel = (assets.get("sprite") or {}).get("file")
        elif key == "talking" and assets.get("living") and not assets["living"].get("stale"):
            rel = "living/talking.png"
        elif key == "blink" and assets.get("living") and not assets["living"].get("stale"):
            rel = "living/blink.png"
        elif key.startswith("expression:"):
            expression = key.split(":", 1)[1]
            living = assets.get("living") or {}
            if living and not living.get("stale") and self.lib.resolve(npc_id, f"living/{expression}.png").is_file():
                rel = f"living/{expression}.png"
            else:
                rel = ((assets.get("expressions") or {}).get(expression) or {}).get("file")
        else:
            rel = (assets.get("portrait") or {}).get("file")
        try:
            path = self.lib.resolve(npc_id, rel) if rel else None
            return path if path and path.is_file() else None
        except KeyError:
            return None

    def _image(self, npc_id, key, size, top=False):
        cache_key = (npc_id, key, size, top)
        if cache_key not in self.images:
            path = self._source(npc_id, key) or self._source(npc_id, "portrait")
            self.images[cache_key] = _fit(path, size, contain=key == "sprite", top=top) if path else None
        return self.images[cache_key]

    def frame(self, frame_number):
        w, h = self.size
        vertical = h > w
        scale = min(w / 1280, h / 720) if not vertical else w / 720
        image = Image.new("RGB", (w, h), BACKDROPS.get(self.scene.get("backdrop"), BACKDROPS["slate"]))
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, w, int(h * 0.16)), fill="#202e3c")
        title_font = _font(round(36 * scale), True)
        name_font = _font(round(23 * scale), True)
        sub_font = _font(round(31 * scale), True)
        draw.text((round(40 * scale), round(30 * scale)), self.scene["title"], font=title_font, fill="#f4ede1")
        beat = next((b for b in self.timeline["beats"] if b["start"] <= frame_number < b["end"]), self.timeline["beats"][-1])
        active = beat.get("speaker_id")
        elapsed = frame_number - beat["start"]
        talking = bool(beat.get("voice") and active and elapsed < len(beat["voice"]["mouth"])
                       and beat["voice"]["mouth"][elapsed] > 0.28)
        blink = elapsed % (FPS * 4) in (FPS * 3, FPS * 3 + 1)
        ids = self.scene["cast"]
        if vertical:
            margin, gap = 24, 12
            top, bottom = 190, 970
            pw = (w - 2 * margin - gap) // 2
            panels = [(margin, top, pw, bottom - top), (margin + pw + gap, top, pw, bottom - top)]
        else:
            margin, gap = 42, 20
            top, bottom = 130, 595
            pw = (w - 2 * margin - gap) // 2
            panels = [(margin, top, pw, bottom - top), (margin + pw + gap, top, pw, bottom - top)]
        if len(ids) > 2:
            columns = 2 if vertical else min(3, len(ids)); rows = math.ceil(len(ids) / columns)
            pw = (w - 2 * margin - gap * (columns - 1)) // columns
            ph = (bottom - top - gap * (rows - 1)) // rows
            panels = [(margin + (index % columns) * (pw + gap), top + (index // columns) * (ph + gap), pw, ph)
                      for index in range(len(ids))]
        if beat.get("layout") == "close" and active in ids and len(ids) == 2:
            first = ids.index(active)
            if vertical:
                panels[first] = (24, 190, w - 48, 780)
                panels[1 - first] = (w - 235, 710, 200, 240)
            else:
                panels[first] = (42, 130, w - 84, 465)
                panels[1 - first] = (w - 265, 390, 215, 185)
        for index, npc_id in enumerate(ids):
            x, y, pw, ph = panels[index]
            key = ("sprite" if beat.get("layout") == "sprite" else "talking" if npc_id == active and talking
                   else f"expression:{beat['expression']}" if npc_id == active and beat.get("expression")
                   else "blink" if blink else "portrait")
            portrait = self._image(npc_id, key, (pw, ph), top=beat.get("layout") == "close" and npc_id == active)
            kind = 'sprite' if beat.get('layout') == 'sprite' else 'portrait'
            rig = (self.timeline.get('rigs') or {}).get(npc_id, {}).get(kind)
            if npc_id == active and beat.get('voice') and rig and rig.get('status') == 'ready':
                weights = character_rig.weights_at(beat['voice']['lip_sync'], beat['voice']['mouth'], elapsed/FPS)
                portrait = _fit_image(character_rig.image(self.lib, npc_id, rig, weights, self.rig_pixels), (pw,ph), kind=='sprite',
                                      top=beat.get('layout')=='close')
            draw.rounded_rectangle((x - 4, y - 4, x + pw + 4, y + ph + 4), radius=12,
                                   fill="#d5a65c" if npc_id == active else "#445466")
            if portrait:
                image.paste(portrait, (x, y))
            else:
                draw.rectangle((x, y, x + pw, y + ph), fill="#314153")
            npc = self.npcs.get(npc_id) or {}
            profile = npc.get("profile") or {}
            name = f"{profile.get('first_name', '')} {profile.get('last_name', '')}".strip() or npc_id
            draw.rounded_rectangle((x + 8, y + ph - 50, x + min(pw - 8, 24 + draw.textlength(name, font=name_font)), y + ph - 8),
                                   radius=8, fill="#15202b")
            draw.text((x + 17, y + ph - 46), name, font=name_font, fill="#ffffff")
        if beat["kind"] == "line":
            bar_top = h - (245 if vertical else 115)
            draw.rounded_rectangle((24, bar_top, w - 24, h - 25), radius=14, fill="#071019")
            profile = self.npcs.get(active, {}).get("profile") or {}
            name = f"{profile.get('first_name', '')} {profile.get('last_name', '')}".strip()
            label = f"{name}: {beat['text']}" if name else beat["text"]
            lines = _text_lines(draw, label, sub_font, w - 95)
            line_h = round(42 * scale)
            y = bar_top + max(12, (h - 25 - bar_top - len(lines) * line_h) // 2)
            for line in lines:
                draw.text(((w - draw.textlength(line, font=sub_font)) / 2, y), line, font=sub_font, fill="#ffffff")
                y += line_h
        return image


def _audio_mix(scene, timeline, scenes, path):
    rate = 48000
    mixed = np.zeros(math.ceil(timeline["frames"] * rate / FPS), dtype=np.float32)
    for beat in timeline["beats"]:
        voice = beat.get("voice")
        if not voice:
            continue
        wav_path = (scenes.resolve(scene["id"], voice["file"]) if voice.get("source") == "scene"
                    else scenes.lib.resolve(beat["speaker_id"], voice["file"]))
        with wave.open(str(wav_path), "rb") as wav:
            if wav.getsampwidth() != 2 or wav.getcomptype() != "NONE":
                raise ValueError("Scene voice clip must be uncompressed 16-bit WAV")
            channels, source_rate = wav.getnchannels(), wav.getframerate()
            data = np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2").astype(np.float32)
        if channels > 1:
            data = data.reshape(-1, channels).mean(axis=1)
        if source_rate != rate and len(data):
            data = np.interp(np.arange(math.ceil(len(data) * rate / source_rate)) * source_rate / rate,
                             np.arange(len(data)), data).astype(np.float32)
        start = beat["start"] * rate // FPS
        count = min(len(data), len(mixed) - start, beat["frames"] * rate // FPS)
        if count > 0:
            mixed[start:start + count] += data[:count]
    from .scene_sound import mix_tracks
    mix_tracks(scene, timeline, scenes, mixed, rate)
    samples = np.clip(mixed, -32768, 32767).astype("<i2")
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(samples.tobytes())


def render(scene, scenes, cancel=None, progress=None, timeline=None):
    """Render a captured scene revision. Return scene-relative MP4 path after atomic publication."""
    encoder = ffmpeg_path()
    if not encoder:
        raise RuntimeError("FFmpeg is required for MP4 export. Install it and restart NPC Forge.")
    timeline = timeline or compile_timeline(scene, scenes.lib, scenes)
    if any(warning.startswith("NPC ") and warning.endswith(" is missing") for warning in timeline["warnings"]):
        raise ValueError("A scene cast member is missing")
    frames = FrameMaker(scene, timeline, scenes.lib)
    scene_dir = scenes.resolve(scene["id"], ".")
    render_dir = scenes.resolve(scene["id"], "render")
    render_dir.mkdir(parents=True, exist_ok=True)
    name = f"{uuid.uuid4().hex[:12]}.mp4"
    with tempfile.TemporaryDirectory(prefix=".render-", dir=scene_dir) as tmp_name:
        tmp = pathlib.Path(tmp_name)
        audio_path, output_path, log_path = tmp / "mix.wav", tmp / "output.mp4", tmp / "ffmpeg.log"
        _audio_mix(scene, timeline, scenes, audio_path)
        w, h = timeline["size"]
        args = [encoder, "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
                "-s", f"{w}x{h}", "-r", str(FPS), "-i", "pipe:0", "-i", str(audio_path), "-c:v", "libx264",
                "-preset", "veryfast", "-threads", "2", "-crf", "23", "-pix_fmt", "yuv420p", "-c:a", "aac",
                "-b:a", "128k", "-movflags", "+faststart", "-shortest", str(output_path)]
        with log_path.open("wb") as log:
            proc = subprocess.Popen(args, stdin=subprocess.PIPE, stderr=log,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            try:
                for index in range(timeline["frames"]):
                    if cancel is not None and cancel.is_set():
                        raise RenderCancelled()
                    proc.stdin.write(frames.frame(index).tobytes())
                    if progress and (index % FPS == 0 or index == timeline["frames"] - 1):
                        progress(index + 1, timeline["frames"])
                proc.stdin.close()
                if proc.wait() != 0:
                    raise RuntimeError(log_path.read_text("utf-8", errors="replace")[-2000:] or "FFmpeg failed")
            except BaseException:
                proc.kill()
                proc.wait()
                raise
        if cancel is not None and cancel.is_set():
            raise RenderCancelled()
        os.replace(output_path, render_dir / name)
    return f"render/{name}"


class SceneRenderJobs:
    """A small CPU-only queue, independent of ComfyUI and NPC generation jobs."""
    def __init__(self, scenes):
        self.scenes = scenes
        self.jobs = {}
        self.lock = threading.RLock()
        self.pending = queue.Queue()
        self.thread = threading.Thread(target=self._worker, daemon=True, name="scene-render")
        self.thread.start()

    def submit(self, scene_id):
        scene = self.scenes.get(scene_id)
        compile_timeline(scene, self.scenes.lib, self.scenes)
        job_id = uuid.uuid4().hex[:10]
        job = {"id": job_id, "scene_id": scene_id, "revision": scene["revision"], "status": "queued",
               "done": 0, "total": 1, "error": None, "file": None}
        with self.lock:
            self.jobs[job_id] = {"public": job, "scene": copy.deepcopy(scene), "cancel": threading.Event()}
            finished = [key for key, item in self.jobs.items()
                        if item["public"]["status"] in ("done", "error", "cancelled")]
            for key in finished[:-30]:
                del self.jobs[key]
        self.pending.put(job_id)
        return dict(job)

    def get(self, job_id):
        with self.lock:
            return dict(self.jobs[job_id]["public"])

    def busy(self, scene_id):
        with self.lock:
            return any(item["public"]["scene_id"] == scene_id and item["public"]["status"] in ("queued", "running")
                       for item in self.jobs.values())

    def cancel(self, job_id):
        with self.lock:
            item = self.jobs[job_id]
            item["cancel"].set()
            if item["public"]["status"] == "queued":
                item["public"]["status"] = "cancelled"
            return dict(item["public"])

    def _worker(self):
        while True:
            job_id = self.pending.get()
            if job_id is None:
                return
            with self.lock:
                item = self.jobs[job_id]
                job = item["public"]
                if job["status"] == "cancelled":
                    continue
                job["status"] = "running"
            def progress(done, total):
                with self.lock:
                    job["done"], job["total"] = done, total
            try:
                timeline = compile_timeline(item["scene"], self.scenes.lib, self.scenes)
                filename = render(item["scene"], self.scenes, item["cancel"], progress, timeline)
                self.scenes.set_render(job["scene_id"], job["revision"], filename, timeline["fingerprint"])
                with self.lock:
                    job["status"], job["file"] = "done", filename
            except RenderCancelled:
                with self.lock:
                    job["status"] = "cancelled"
            except Exception as exc:
                with self.lock:
                    job["status"], job["error"] = "error", str(exc)

    def close(self):
        with self.lock:
            for item in self.jobs.values():
                if item["public"]["status"] in ("queued", "running"):
                    item["cancel"].set()
        self.pending.put(None)
        self.thread.join()
