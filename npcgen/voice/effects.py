"""Repeatable CPU delivery effects. Identity references and dry masters stay intact."""
import functools
import hashlib
import math
from pathlib import Path
import shutil
import subprocess

import numpy as np

from . import audio

VERSION = 1
DEFAULTS = {"enabled": False, "pitch_semitones": 0., "formants": "preserve", "highpass_hz": 0., "lowpass_hz": 0.,
            "distortion": 0., "ring_mod_hz": 0., "layer_mix": 0., "layer_pitch_semitones": -7., "layer_delay_ms": 0.}
BOUNDS = {"pitch_semitones": (-12, 12), "highpass_hz": (0, 2000), "lowpass_hz": (0, 20000), "distortion": (0, 1),
          "ring_mod_hz": (0, 200), "layer_mix": (0, 1), "layer_pitch_semitones": (-12, 12), "layer_delay_ms": (0, 100)}
PRESETS = {"dry": dict(DEFAULTS), "creature": {**DEFAULTS, "enabled": True, "pitch_semitones": -5., "formants": "shift",
          "layer_mix": .25, "layer_pitch_semitones": -7., "layer_delay_ms": 20.},
          "robot": {**DEFAULTS, "enabled": True, "highpass_hz": 250., "lowpass_hz": 4500., "distortion": .25, "ring_mod_hz": 65.}}


def clean(raw):
    if not isinstance(raw, dict) or set(raw) - set(DEFAULTS):
        raise ValueError("Effects must contain only the supported audio controls")
    out = {**DEFAULTS, **raw}
    if not isinstance(out["enabled"], bool) or out["formants"] not in ("preserve", "shift"):
        raise ValueError("Choose enabled/disabled effects and preserved or pitch-linked formants")
    for key, (lo, hi) in BOUNDS.items():
        value = out[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not lo <= value <= hi:
            raise ValueError(f"Effect {key} must be a finite number from {lo} to {hi}")
        out[key] = float(value)
    if out["highpass_hz"] and out["lowpass_hz"] and out["highpass_hz"] >= out["lowpass_hz"]:
        raise ValueError("Low-pass frequency must be above the high-pass frequency")
    return out


def effective(raw):
    c = clean({} if raw is None else raw)
    if not c["enabled"]:
        return {}
    active = {key: c[key] for key in ("pitch_semitones", "highpass_hz", "lowpass_hz", "distortion", "ring_mod_hz", "layer_mix") if c[key]}
    if c["pitch_semitones"]:
        active["formants"] = c["formants"]
    if c["layer_mix"]:
        active.update(layer_pitch_semitones=c["layer_pitch_semitones"], layer_delay_ms=c["layer_delay_ms"])
    return active


@functools.lru_cache(maxsize=4)
def _backend(path, stamp):
    result = subprocess.run([path, "-hide_banner", "-filters"], capture_output=True, text=True, timeout=10)
    version = subprocess.run([path, "-version"], capture_output=True, text=True, timeout=10)
    required = ("rubberband", "highpass", "lowpass", "asoftclip", "amix", "aeval")
    missing = [name for name in required if name not in result.stdout]
    return {"available": not missing and result.returncode == 0, "missing_filters": missing,
            "ffmpeg_version": version.stdout.splitlines()[0] if version.stdout else "unknown", "path": path}


def capabilities():
    path = shutil.which("ffmpeg")
    if not path:
        return {"available": False, "reason": "FFmpeg is not available on PATH", "presets": PRESETS, "bounds": BOUNDS}
    try:
        info = _backend(path, Path(path).stat().st_mtime_ns)
        return {**info, "presets": PRESETS, "bounds": BOUNDS, "schema": VERSION,
                "formants": "preserve or shift with pitch; independent formant scaling is not supported"}
    except (OSError, subprocess.SubprocessError) as e:
        return {"available": False, "reason": str(e), "presets": PRESETS, "bounds": BOUNDS}


def preserve(lib, npc_id, data):
    digest = hashlib.sha256(data).hexdigest()
    file = f"voice/originals/{digest}.wav"
    path = lib.resolve(npc_id, file)
    path.parent.mkdir(parents=True, exist_ok=True)
    with lib.lock:
        try:
            with path.open("xb") as stream:
                stream.write(data)
        except FileExistsError:
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise ValueError("Preserved original checksum failed; restore the original before processing it")
    samples, rate = audio.read_wav(data)
    return {"file": file, "sha256": digest, "sample_rate": rate, "samples": len(samples), "seconds": len(samples) / rate}


def read_original(lib, npc_id, original):
    data = lib.read_asset(npc_id, original["file"])
    if hashlib.sha256(data).hexdigest() != original["sha256"]:
        raise ValueError("Preserved original checksum failed; restore it before reapplying effects")
    return data


def check_delivery(data, meta):
    expected = (meta.get("postprocessing") or {}).get("output_sha256")
    if expected and hashlib.sha256(data).hexdigest() != expected:
        raise ValueError("Delivered audio checksum failed; regenerate it before exporting")
    return data


def preview(lib, npc, source, raw):
    v = npc.get("voice") or {}
    if source == "reference":
        meta = v.get("reference")
    elif isinstance(source, str) and source.startswith("line:"):
        meta = (npc.get("voice_lines") or {}).get(source[5:])
    elif isinstance(source, str) and source.startswith("test:"):
        meta = (v.get("tests") or {}).get(source[5:])
    elif isinstance(source, str) and source.startswith("take:"):
        meta = next((t for t in v.get("takes", []) if t["id"] == source[5:]), None)
    else:
        meta = None
    if not meta or not meta.get("file"):
        raise ValueError("Choose an existing reference, take, test or dialogue clip for the effect preview")
    data = read_original(lib, npc["id"], meta["original"]) if meta.get("original") else lib.read_asset(npc["id"], meta["file"])
    if not meta.get("original") and meta.get("sha256") and hashlib.sha256(data).hexdigest() != meta["sha256"]:
        raise ValueError("Preview source checksum failed")
    original = preserve(lib, npc["id"], data)
    processed, receipt = process(data, raw)
    file = f"voice/effects/{receipt['output_sha256']}.wav"
    lib.write_asset(npc["id"], file, processed)
    return {"file": file, "original": original, "postprocessing": receipt, "source": source,
            "source_role": "preserved_dry_master" if meta.get("original") else "existing_clip",
            "seconds": original["seconds"], "audio_url": f"/api/npcs/{npc['id']}/files/{file}",
            "original_url": f"/api/npcs/{npc['id']}/files/{original['file']}"}


def _pitch(semitones, formants="preserve"):
    return f"rubberband=tempo=1:pitch={2 ** (semitones / 12):.10g}:formant={'preserved' if formants == 'preserve' else 'shifted'}"


def process(data, raw, cancel=None):
    c = effective(raw)
    x, rate = audio.read_wav(data)
    if not len(x) or not 8000 <= rate <= 192000 or (c and len(x) / rate > 120) or not np.isfinite(x).all():
        raise ValueError("Effects need a finite, nonempty WAV of at most 120 seconds at 8-192 kHz")
    receipt = {"schema": VERSION, "settings": dict(c), "requested_settings": c, "source_sha256": hashlib.sha256(data).hexdigest(),
               "sample_rate": rate, "source_samples": len(x), "duration_policy": "same_sample_count", "time_scale": 1.0}
    if not c:
        return data, {**receipt, "backend": "bypass", "output_sha256": receipt["source_sha256"], "output_samples": len(x)}
    cap = capabilities()
    if not cap["available"]:
        raise ValueError("Voice effects are unavailable: " + cap.get("reason", "required FFmpeg filters are missing"))
    if cancel is not None and cancel.is_set():
        raise ValueError("Voice effects cancelled")
    chain = []
    if "pitch_semitones" in c:
        chain.append(_pitch(c["pitch_semitones"], c["formants"]))
    for key, filter_name in (("highpass_hz", "highpass"), ("lowpass_hz", "lowpass")):
        if key in c:
            cutoff = min(c[key], rate * .45)
            receipt["settings"][key] = cutoff
            chain.append(f"{filter_name}=f={cutoff:.8g}")
    if "distortion" in c:
        chain += [f"volume={1 + c['distortion'] * 30:.8g}", "asoftclip=type=tanh:oversample=2"]
    if "ring_mod_hz" in c:
        chain.append(f"aeval=val(0)*sin(2*PI*{c['ring_mod_hz']:.8g}*t)")
    main = ",".join(chain) or "anull"
    if "layer_mix" in c:
        delay = round(c["layer_delay_ms"] * rate / 1000)
        graph = f"[0:a]asplit=2[main][layer];[main]{main}[wet];[layer]{_pitch(c['layer_pitch_semitones'])},adelay={delay}S[low];[wet][low]amix=inputs=2:weights='1 {c['layer_mix']:.8g}':normalize=0:duration=first[out]"
    else:
        graph = f"[0:a]{main}[out]"
    try:
        result = subprocess.run([cap["path"], "-nostdin", "-hide_banner", "-loglevel", "error", "-threads", "1",
            "-f", "wav", "-i", "pipe:0", "-filter_complex", graph, "-map", "[out]", "-ac", "1", "-ar", str(rate),
            "-c:a", "pcm_s16le", "-f", "wav", "pipe:1"], input=data, capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as e:
        raise ValueError(f"Voice effects failed: {e}") from None
    if result.returncode:
        raise ValueError("Voice effects failed: " + result.stderr.decode("utf-8", "replace")[-1000:])
    if cancel is not None and cancel.is_set():
        raise ValueError("Voice effects cancelled")
    processed, out_rate = audio.read_wav(result.stdout)
    if out_rate != rate or not len(processed) or not np.isfinite(processed).all():
        raise ValueError("Voice effects returned invalid audio")
    output_frames = len(processed)
    processed = np.pad(processed[:len(x)], (0, max(0, len(x) - len(processed))))
    delivered = audio.write_wav(audio.normalize(processed, rate), rate)  # No retrimming or duration changes.
    return delivered, {**receipt, "backend": "ffmpeg", "ffmpeg_version": cap["ffmpeg_version"], "filter_graph": graph,
        "output_samples": len(x), "backend_samples": output_frames, "output_sha256": hashlib.sha256(delivered).hexdigest()}
