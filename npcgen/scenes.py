"""Saved, CPU-composed scenes that refer to existing NPC library assets."""
import copy
import hashlib
import json
import math
import os
import re
import secrets
import shutil
import threading

from .library import ID_RE, now
from .voice import authoring as voice_authoring, describe as voice_describe, engines as voice_engines, record as voice_record
from .voice.record import line_is_stale
from .voice import alignment as voice_alignment, effects as voice_effects, character_rig
from .voice import mesh_rig
from . import scene_schema

SCENE_ID = re.compile(r"^scene-[a-f0-9]{10}$")
BEAT_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{1,40}$")
LINE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
ASPECTS = {"landscape": (1280, 720), "portrait": (720, 1280)}
LAYOUTS = {"split", "close", "sprite"}
MODES = {"cards", "stage3d"}
MOTIONS = {"stand", "walk", "wave"}
CAMERA_SHOTS = {"wide", "medium", "close", "shoulder"}
BACKDROPS = {"slate": "#15202b", "ember": "#39221f", "forest": "#142b25", "violet": "#29213b"}
EXPRESSIONS = {"", "happy", "sad", "angry", "surprised", "scared", "laughing", "tired", "smug", "embarrassed", "thinking", "flirty"}
FPS = 25
MAX_BEATS = 20
MAX_FRAMES = 60 * FPS


def _string(value, label, limit, required=False):
    if not isinstance(value, str):
        raise ValueError(f"{label} must be text")
    value = value.strip()
    if len(value) > limit or (required and not value):
        raise ValueError(f"{label} must be {'1-' if required else 'at most '}{limit} characters")
    return value


def clean_scene(raw, lib, previous=None):
    """Validate editor input; ignore client-controlled paths, timestamps and render metadata."""
    if not isinstance(raw, dict):
        raise ValueError("Scene must be an object")
    title = _string(raw.get("title", ""), "Title", 100, required=True)
    cast = raw.get("cast")
    if not isinstance(cast, list) or not 2 <= len(cast) <= 6 or len(set(map(str, cast))) != len(cast):
        raise ValueError("Choose at least two different NPCs, up to six")
    for npc_id in cast:
        if not isinstance(npc_id, str) or not ID_RE.fullmatch(npc_id):
            raise ValueError("Invalid NPC id")
        try:
            lib.get(npc_id)
        except KeyError:
            raise ValueError(f"NPC {npc_id} is missing") from None
    aspect = raw.get("aspect", "landscape")
    if not isinstance(aspect, str) or aspect not in ASPECTS:
        raise ValueError("Unknown scene aspect")
    backdrop = raw.get("backdrop", "slate")
    if not isinstance(backdrop, str) or backdrop not in BACKDROPS:
        raise ValueError("Unknown scene backdrop")
    mode = raw.get("mode", "cards")
    if not isinstance(mode, str) or mode not in MODES:
        raise ValueError("Unknown scene mode")
    background_prompt = _string(raw.get("background_prompt", ""), "Background prompt", 500)
    if mode == "stage3d" and not background_prompt:
        raise ValueError("Describe the background for the 3D stage")
    requested_voices = raw.get("voice_overrides", {})
    if requested_voices is None:
        requested_voices = {}
    stock_ids = {voice["id"] for voice in voice_engines.STOCK_VOICES["kokoro"]}
    if not isinstance(requested_voices, dict) or any(npc_id not in cast or voice_id not in stock_ids
                                                     for npc_id, voice_id in requested_voices.items()):
        raise ValueError("Choose a valid scene voice for a cast member")
    requested_bindings = raw.get("voice_bindings") or {}
    if not isinstance(requested_bindings, dict) or any(npc_id not in cast for npc_id in requested_bindings):
        raise ValueError("Scene voice pins must belong to cast members")
    bindings = {}
    for npc_id, binding in requested_bindings.items():
        ref_id = binding.get("reference_id") if isinstance(binding, dict) else binding
        if not isinstance(ref_id, str) or not re.fullmatch(r"[a-f0-9]{12,32}", ref_id):
            raise ValueError("Choose a saved character reference version to pin")
        if npc_id in requested_voices:
            raise ValueError("Choose a pinned reference or a stock scene override for a character")
        previous_binding = ((previous or {}).get("voice_bindings") or {}).get(npc_id)
        if previous_binding and previous_binding["reference_id"] == ref_id:
            bindings[npc_id] = copy.deepcopy(previous_binding)  # Client cannot rewrite a saved snapshot.
            continue
        npc = lib.get(npc_id)
        v = voice_record.voice_of(npc)
        if voice_record.identity_id(v) != ref_id:
            history = next((item for item in v.get("reference_history", []) if item["id"] == ref_id), None)
            if not history:
                raise ValueError("The selected character reference version is missing")
            v = copy.deepcopy(history["voice_snapshot"])
            if history.get("file"):
                v["reference"] = {key: value for key, value in history.items() if key != "voice_snapshot"}
            else:
                v.pop("reference", None)
        voice_record.clean_settings(v, npc)
        bindings[npc_id] = {"reference_id": ref_id, "snapshot": voice_record.freeze_voice(lib, npc_id, v)}
    beats = raw.get("beats")
    if not isinstance(beats, list) or not 1 <= len(beats) <= MAX_BEATS:
        raise ValueError(f"A scene needs 1-{MAX_BEATS} beats")
    out, ids = [], set()
    for index, beat in enumerate(beats, 1):
        if not isinstance(beat, dict):
            raise ValueError(f"Beat {index} must be an object")
        beat_id = beat.get("id") or f"beat-{secrets.token_hex(5)}"
        if not isinstance(beat_id, str) or not BEAT_ID.fullmatch(beat_id) or beat_id in ids:
            raise ValueError(f"Beat {index} has an invalid or repeated id")
        ids.add(beat_id)
        extras = scene_schema.beat_fields(beat, cast)
        kind = beat.get("kind", "line")
        if kind == "pause":
            duration = beat.get("duration", 1)
            if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not math.isfinite(duration) or not 0.2 <= duration <= 8:
                raise ValueError(f"Beat {index} pause must be 0.2-8 seconds")
            out.append({"id": beat_id, "kind": "pause", "duration": round(duration, 2), **_camera(beat, index, cast), **extras})
            continue
        if kind == "reaction":
            speaker = beat.get("speaker_id")
            if speaker not in cast:
                raise ValueError(f"Beat {index} speaker is not in the cast")
            duration = beat.get("duration", 1.5)
            if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not math.isfinite(duration) or not 0.2 <= duration <= 8:
                raise ValueError(f"Beat {index} reaction must be 0.2-8 seconds")
            layout = beat.get("layout", "close")
            expression = beat.get("expression", "")
            if not isinstance(layout, str) or layout not in LAYOUTS or not isinstance(expression, str) or expression not in EXPRESSIONS:
                raise ValueError(f"Beat {index} has an unknown shot or expression")
            out.append({"id": beat_id, "kind": "reaction", "speaker_id": speaker,
                        "duration": round(duration, 2), "layout": layout, "expression": expression,
                        **_stage_motion(beat, index), **_camera(beat, index, cast), **extras})
            continue
        if kind != "line":
            raise ValueError(f"Beat {index} has an unknown kind")
        speaker = beat.get("speaker_id")
        if speaker not in cast:
            raise ValueError(f"Beat {index} speaker is not in the cast")
        text = _string(beat.get("text", ""), f"Beat {index} text", 300, required=True)
        layout = beat.get("layout", "split")
        if not isinstance(layout, str) or layout not in LAYOUTS:
            raise ValueError(f"Beat {index} has an unknown layout")
        mood = beat.get("mood", "neutral")
        expression = beat.get("expression", "")
        if not isinstance(mood, str) or mood not in voice_engines.MOODS:
            raise ValueError(f"Beat {index} has an unknown mood")
        if not isinstance(expression, str) or expression not in EXPRESSIONS:
            raise ValueError(f"Beat {index} has an unknown expression")
        problem = voice_describe.mood_problem(lib.get(speaker), mood)
        if problem:
            raise ValueError(problem)
        line_id = beat.get("line_id") or ""
        if not isinstance(line_id, str) or (line_id and not LINE_ID.fullmatch(line_id)):
            raise ValueError(f"Beat {index} has an invalid dialogue line")
        performance_id = beat.get("performance_id")
        if performance_id is not None and not isinstance(performance_id, str):
            raise ValueError(f"Beat {index} performance must be a saved preset ID")
        v = scene_voice_of(lib.get(speaker), requested_voices.get(speaker), bindings.get(speaker))
        voice_authoring.prepare(v, text, mood, preset_id=performance_id)
        out.append({"id": beat_id, "kind": "line", "speaker_id": speaker, "text": text,
                    "layout": layout, "line_id": line_id, "mood": mood, "expression": expression,
                    "performance_id": performance_id,
                    **_stage_motion(beat, index), **_camera(beat, index, cast), **extras})
    result = {"version": 1, "id": previous["id"] if previous else f"scene-{secrets.token_hex(5)}",
              "title": title, "cast": cast, "aspect": aspect, "backdrop": backdrop, "beats": out,
              "mode": mode, "background_prompt": background_prompt,
              "background": previous.get("background") if previous else None,
              "voice_overrides": requested_voices,
              "voice_bindings": bindings,
              **scene_schema.scene_fields(raw, cast),
              "media": copy.deepcopy((previous or {}).get("media", {})),
              "prop_sources": copy.deepcopy((previous or {}).get("prop_sources", {})),
              "voice_takes": copy.deepcopy((previous or {}).get("voice_takes", {})),
              "revision": previous["revision"] + 1 if previous else 1,
              "created": previous["created"] if previous else now(), "updated": now(),
              "render": previous.get("render") if previous else None,
              "audio": previous.get("audio", {}) if previous else {}}
    # Check the estimated silent timing early. Voiced timing is checked by compile_timeline.
    if sum(_fallback_frames(b) for b in out) > MAX_FRAMES:
        raise ValueError("Scene exceeds 60 seconds")
    return result


def _camera(beat, index, cast):
    shot = beat.get('camera_shot', 'wide')
    side = beat.get('camera_side', 'left')
    target = beat.get('camera_target') or beat.get('speaker_id') or cast[0]
    if not isinstance(shot, str) or shot not in CAMERA_SHOTS or not isinstance(side, str) or side not in ('left', 'right'):
        raise ValueError(f'Beat {index} has an invalid camera shot or side')
    if not isinstance(target, str) or target not in cast:
        raise ValueError(f'Beat {index} camera target must belong to the cast')
    return {'camera_shot': shot, 'camera_target': target, 'camera_side': side}


def _stage_motion(beat, index):
    motion = beat.get("motion", "stand")
    if not isinstance(motion, str) or motion not in MOTIONS:
        raise ValueError(f"Beat {index} has an unknown 3D motion")
    target = beat.get("walk_to")
    if target is not None:
        if (not isinstance(target, list) or len(target) != 2 or
                any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
                    for v in target) or not -3 <= target[0] <= 3 or not -2 <= target[1] <= 2):
            raise ValueError(f"Beat {index} walk target must be on the stage")
        target = [round(float(v), 2) for v in target]
    if motion == "walk" and target is None:
        raise ValueError(f"Beat {index} needs a walk target")
    return {"motion": motion, "walk_to": target}


def _fallback_frames(beat):
    if beat["kind"] in ("pause", "reaction"):
        return round(beat["duration"] * FPS)
    return round(min(8, max(1.6, len(beat["text"].split()) / 2.5 + 0.8)) * FPS)


class Scenes:
    def __init__(self, lib):
        self.lib = lib
        self.root = lib.root / ".npcforge-scenes"
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()

    def resolve(self, scene_id, rel):
        if not isinstance(scene_id, str) or not SCENE_ID.fullmatch(scene_id):
            raise KeyError(scene_id)
        base = (self.root / scene_id).resolve()
        path = (base / rel).resolve()
        if not path.is_relative_to(base):
            raise KeyError(rel)
        return path

    def get(self, scene_id):
        path = self.resolve(scene_id, "scene.json")
        if not path.is_file():
            raise KeyError(scene_id)
        return json.loads(path.read_text("utf-8"))

    def list(self):
        with self.lock:
            scenes = []
            for path in self.root.glob("scene-*/scene.json"):
                try:
                    scenes.append(json.loads(path.read_text("utf-8")))
                except (OSError, ValueError, TypeError):
                    continue
            return sorted(scenes, key=lambda s: s.get("updated", ""), reverse=True)

    def _write(self, scene):
        path = self.resolve(scene["id"], "scene.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(scene, indent=2, ensure_ascii=False), "utf-8")
        os.replace(tmp, path)

    def create(self, raw):
        with self.lock:
            scene = clean_scene(raw, self.lib)
            from .scene_props import pin
            pin(scene, self)
            self._write(scene)
            return copy.deepcopy(scene)

    def update(self, scene_id, raw):
        with self.lock:
            previous = self.get(scene_id)
            if raw.get("revision") != previous["revision"]:
                raise ValueError("Scene changed since you opened it; reload before saving")
            scene = clean_scene(raw, self.lib, previous)
            from .scene_props import pin
            pin(scene, self, previous)
            self._write(scene)
            return copy.deepcopy(scene)

    def duplicate(self, scene_id):
        """Copy editable content and owned media, leaving the source scene untouched."""
        with self.lock:
            source = self.get(scene_id)
            raw = {**source, 'title': (source['title'][:90] + ' (copy)')}
            scene = clean_scene(raw, self.lib)
            src, dst = self.resolve(scene_id, '.'), self.resolve(scene['id'], '.')
            shutil.copytree(src, dst, ignore=shutil.ignore_patterns('scene.json', 'render', '.render-*', '*.tmp'))
            for field in ('audio', 'voice_takes', 'media', 'background', 'prop_sources'):
                if field in source:
                    scene[field] = copy.deepcopy(source[field])
            self._write(scene)
            return copy.deepcopy(scene)

    def set_render(self, scene_id, revision, filename, fingerprint=None):
        with self.lock:
            scene = self.get(scene_id)
            # A render of an older revision can remain downloadable, but is marked outdated.
            scene["render"] = {"file": filename, "revision": revision, "fingerprint": fingerprint, "created": now()}
            self._write(scene)

    def set_background(self, scene_id, prompt, filename, engine=None):
        with self.lock:
            scene = self.get(scene_id)
            if scene.get("mode") != "stage3d" or scene.get("background_prompt") != prompt:
                return False
            old = scene.get("background") or {}
            scene["background"] = {"file": filename, "prompt": prompt, "created": now(), "engine": engine}
            self._write(scene)
            if old.get("file") and old["file"] != filename:
                self.resolve(scene_id, old["file"]).unlink(missing_ok=True)
            return True

    def set_audio(self, scene_id, beat_id, meta):
        """Publish a clip only if the beat still says the words that were recorded."""
        with self.lock:
            scene = self.get(scene_id)
            beat = next((b for b in scene["beats"] if b["id"] == beat_id and b["kind"] == "line"), None)
            if not beat or any(beat.get(key, "neutral" if key == "mood" else None) != meta[key]
                               for key in ("speaker_id", "text", "mood")):
                return False
            from . import scene_takes
            meta = copy.deepcopy(meta)
            data = voice_effects.check_delivery(self.resolve(scene_id, meta['file']).read_bytes(), meta)
            meta['sha256'] = hashlib.sha256(data).hexdigest()
            scene_takes.record(scene, beat_id, meta)
            self._write(scene)
            return True

    def write_media(self, scene_id, rel, data):
        path = self.resolve(scene_id, rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)

    def write_audio(self, scene_id, rel, wav):
        self.write_media(scene_id, rel, wav)

    def delete(self, scene_id):
        with self.lock:
            self.get(scene_id)
            shutil.rmtree(self.resolve(scene_id, "."))


def scene_voice_of(npc, stock_voice=None, binding=None):
    v = copy.deepcopy(binding["snapshot"]) if binding else voice_record.voice_of(npc)
    if stock_voice:
        v.update(engine="kokoro", speaker="kokoro", mode="stock", stock_voice=stock_voice,
                 reference=None, language="English", studio={})
        v.pop("preset_identity", None)
    return v


def voice_signature(npc, stock_voice=None, binding=None):
    v = scene_voice_of(npc, stock_voice, binding)
    source = {"reference": (v.get("reference") or {}).get("id"), "stock_voice": v.get("stock_voice"),
              "speaker": voice_record.speaker_for(v), "engine": v.get("engine"), "settings": v.get("settings") or {},
              "language": v.get("language", "English"), "studio": v.get("studio") or {}}
    return hashlib.sha256(json.dumps(source, sort_keys=True).encode()).hexdigest()


def _file_stamp(path):
    try:
        stat = path.stat()
        return [stat.st_mtime_ns, stat.st_size]
    except OSError:
        return None


def compile_timeline(scene, lib, scenes=None):
    """Single frame clock for browser playback and CPU export. No generation or GPU calls."""
    def exists(npc_id, meta):
        try:
            return bool(meta.get("file") and lib.resolve(npc_id, meta["file"]).is_file())
        except (KeyError, TypeError):
            return False

    scenes = scenes or Scenes(lib)
    npcs = {}
    warnings = []
    ready = True
    for npc_id in scene["cast"]:
        try:
            npcs[npc_id] = lib.get(npc_id)
        except KeyError:
            warnings.append(f"NPC {npc_id} is missing")
            continue
        assets = npcs[npc_id].get("assets") or {}
        if scene.get("mode") == "stage3d":
            rig = assets.get("rig_3d") or {}
            if not exists(npc_id, rig):
                warnings.append(f"{npc_id} needs a rigged 3D model to appear on the stage")
            elif rig.get("stale"):
                warnings.append(f"{npc_id} has an outdated 3D rig; rerig after the model is ready")
        else:
            portrait = assets.get("portrait") or {}
            if not exists(npc_id, portrait):
                warnings.append(f"{npc_id} has no portrait; a placeholder will appear")
            elif portrait.get("stale"):
                warnings.append(f"{npc_id} portrait is outdated")
    if scene.get("mode") == "stage3d":
        background = scene.get("background") or {}
        try:
            ready = bool(scenes and background.get("file") and background.get("prompt") == scene.get("background_prompt")
                         and scenes.resolve(scene["id"], background["file"]).is_file())
        except (KeyError, TypeError):
            ready = False
        if not ready:
            warnings.append("Generate the stage background to replace the plain setting")
    beats, cursor = [], 0
    for beat in scene["beats"]:
        resolved = dict(beat)
        resolved["start"] = cursor
        if beat["kind"] == "line":
            npc = npcs.get(beat["speaker_id"])
            voice = None
            if npc:
                own = (scene.get("audio") or {}).get(beat["id"])
                override = (scene.get("voice_overrides") or {}).get(beat["speaker_id"])
                binding = (scene.get("voice_bindings") or {}).get(beat["speaker_id"])
                if own and own.get("render_signature"):
                    try:
                        _, _, inputs = voice_authoring.prepare(scene_voice_of(npc, override, binding), beat["text"],
                            beat.get("mood", "neutral"), preset_id=beat.get("performance_id"))
                        matching_voice = own["render_signature"] == inputs["render_signature"]
                    except (ValueError, KeyError, TypeError):
                        matching_voice = False
                else:
                    matching_voice = bool(own and own.get("voice_signature") == voice_signature(npc, override, binding))
                if own and all(own.get(key) == beat.get(key, "neutral" if key == "mood" else None)
                               for key in ("speaker_id", "text", "mood")) and matching_voice:
                    try:
                        if scenes.resolve(scene["id"], own["file"]).is_file():
                            voice = {"source": "scene", "file": own["file"], "seconds": own.get("seconds", 0),
                                     "mouth": own.get("mouth") or [], "created": own.get("created")}
                    except (KeyError, TypeError):
                        pass
                meta = (npc.get("voice_lines") or {}).get(beat["line_id"])
                if not voice and not override and not binding and not beat.get("performance_id") and meta and beat["text"] == meta.get("text") and not line_is_stale(npc, beat["line_id"]):
                    try:
                        if lib.resolve(npc["id"], meta["file"]).is_file():
                            voice = {"source": "npc", "file": meta["file"], "seconds": meta.get("seconds", 0),
                                     "mouth": meta.get("mouth") or [], "created": meta.get("created")}
                    except (KeyError, TypeError):
                        pass
                if (beat["line_id"] or own) and not voice:
                    warnings.append(f"Saved voice for beat {beat['id']} is missing or outdated")
                if voice:
                    selected = own if voice['source'] == 'scene' else meta
                    try:
                        path = scenes.resolve(scene['id'], voice['file']) if voice['source'] == 'scene' else lib.resolve(npc['id'], voice['file'])
                        data = voice_effects.check_delivery(path.read_bytes(), selected)
                        if selected.get('sha256') and hashlib.sha256(data).hexdigest() != selected['sha256']:
                            raise ValueError('Saved take checksum failed')
                        captured = scene_voice_of(npc, override, binding) if voice['source'] == 'scene' else npc.get('voice') or {}
                        voice['lip_sync'] = voice_alignment.for_delivery(data, selected, captured)
                    except (KeyError, OSError, ValueError, TypeError) as error:
                        warnings.append(f"Beat {beat['id']} audio/timing cannot be used: {error}")
                        voice = None
                if beat["layout"] == "sprite":
                    for cast_id, cast_npc in npcs.items():
                        sprite = (cast_npc.get("assets") or {}).get("sprite") or {}
                        if not exists(cast_id, sprite):
                            warnings.append(f"{cast_id} has no sprite; its portrait will appear")
            frames = max(_fallback_frames(beat), math.ceil((voice or {}).get("seconds", 0) * FPS) + 8)
            resolved["voice"] = voice
        else:
            frames = _fallback_frames(beat)
        resolved["frames"] = frames
        resolved["end"] = cursor + frames
        cursor += frames
        beats.append(resolved)
    if cursor > MAX_FRAMES:
        raise ValueError("Scene exceeds 60 seconds with the selected voice clips")
    rigs = {npc_id: {kind: character_rig.describe(lib, npc, kind) for kind in character_rig.KINDS} for npc_id,npc in npcs.items()}
    mesh_rigs={npc_id:mesh_rig.describe(lib,npc) for npc_id,npc in npcs.items()} if scene.get('mode')=='stage3d' else {}
    for beat in beats:
        if beat.get('voice'):
            kind = 'sprite' if beat.get('layout') == 'sprite' else 'portrait'
            rig = rigs.get(beat.get('speaker_id'), {}).get(kind, {})
            aligned = beat['voice']['lip_sync']['status'] == 'aligned'
            beat['voice']['visual_method'] = ('viseme_frames' if aligned else 'amplitude_frames') if rig.get('status') == 'ready' and scene.get('mode') != 'stage3d' else 'amplitude_fallback'
            if scene.get('mode')=='stage3d' and mesh_rigs.get(beat.get('speaker_id'),{}).get('status')=='ready':
                beat['voice']['visual_method']='viseme_mesh' if aligned else 'amplitude_mesh'
    fingerprint_inputs = {"rigs": [(npc_id,kind,value.get('version'),value['status']) for npc_id,kinds in rigs.items() for kind,value in kinds.items()],
                          "mesh_rigs":[(npc_id,rig.get('version'),rig['status']) for npc_id,rig in mesh_rigs.items()],
                          "lip_sync": [(b['id'],(b.get('voice') or {}).get('lip_sync')) for b in beats],
                          "direction": {key: scene.get(key) for key in ('actors', 'props', 'soundtracks')},
                          "revision": scene["revision"], "beats": [(b["id"], b.get("voice", {}).get("file") if b.get("voice") else None,
                           b.get("voice", {}).get("created") if b.get("voice") else None) for b in beats], "assets": []}
    for beat in beats:
        voice = beat.get("voice")
        if not voice:
            continue
        try:
            path = (scenes.resolve(scene["id"], voice["file"]) if voice["source"] == "scene"
                    else lib.resolve(beat["speaker_id"], voice["file"]))
            fingerprint_inputs["assets"].append((voice["source"], voice["file"], _file_stamp(path)))
        except KeyError:
            pass
    for npc_id, npc in npcs.items():
        assets = npc.get("assets") or {}
        if scene.get("mode") == "stage3d":
            rig = assets.get("rig_3d") or {}
            if rig.get("file"):
                fingerprint_inputs["assets"].append((npc_id, rig["file"], _file_stamp(lib.resolve(npc_id, rig["file"]))))
            continue
        for kind in ("portrait", "sprite", "living"):
            meta = assets.get(kind) or {}
            rels = [meta.get("file")] if kind != "living" else ["living/blink.png", "living/talking.png"]
            for rel in rels:
                if rel:
                    try:
                        fingerprint_inputs["assets"].append((npc_id, rel, _file_stamp(lib.resolve(npc_id, rel))))
                    except KeyError:
                        pass
        for expression in sorted(EXPRESSIONS - {""}):
            meta = (assets.get("expressions") or {}).get(expression) or {}
            rel = meta.get("file")
            if rel:
                try:
                    fingerprint_inputs["assets"].append((npc_id, rel, _file_stamp(lib.resolve(npc_id, rel))))
                except KeyError:
                    pass
            if assets.get("living"):
                rel = f"living/{expression}.png"
                try:
                    fingerprint_inputs["assets"].append((npc_id, rel, _file_stamp(lib.resolve(npc_id, rel))))
                except KeyError:
                    pass
    background = scene.get("background") or {}
    if scene.get("mode") == "stage3d" and background.get("file") and scenes:
        fingerprint_inputs["assets"].append(("background", background["file"],
                                              _file_stamp(scenes.resolve(scene["id"], background["file"]))))
    from .scene_props import describe as describe_props
    props, prop_warnings = describe_props(scene, scenes)
    fingerprint_inputs['props'] = [(row['id'], row['source']['sha256']) for row in props]
    fingerprint = hashlib.sha256(json.dumps(fingerprint_inputs, sort_keys=True).encode()).hexdigest()
    timeline = {"scene_id": scene["id"], "revision": scene["revision"], "fingerprint": fingerprint, "fps": FPS,
            "size": ASPECTS[scene["aspect"]], "frames": cursor, "beats": beats, "rigs": rigs, "mesh_rigs":mesh_rigs,
            "warnings": list(dict.fromkeys(warnings))}
    from .scene_readiness import assess
    timeline['readiness'] = assess(scene, timeline, npcs, exists, ready)
    timeline['props'] = props
    for warning in prop_warnings:
        timeline['readiness']['checks'].append({'code': 'prop', 'level': 'blocker', 'message': warning, 'action': None})
    timeline['readiness']['export_blocked'] |= bool(prop_warnings)
    from .scene_sound import describe as describe_sound
    timeline['soundtracks'], sound_warnings = describe_sound(scene, scenes)
    for warning in sound_warnings:
        timeline['readiness']['checks'].append({'code': 'sound', 'level': 'blocker', 'message': warning, 'action': None})
    timeline['readiness']['export_blocked'] |= bool(sound_warnings)
    from .scene_takes import describe as describe_takes
    timeline['takes'] = describe_takes(scene, scenes)
    return timeline
