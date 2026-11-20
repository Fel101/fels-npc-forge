"""Scene-owned WAV tracks with deterministic scheduling and speech ducking."""
import base64
import hashlib
import io
import math
import wave

import numpy as np

from .library import now


def upload(scenes, scene_id, raw):
    value = raw.get('audio') if isinstance(raw, dict) else None
    if not isinstance(value, str) or not value.startswith('data:audio/wav;base64,') or len(value) > 7_900_000:
        raise ValueError('Choose a WAV recording up to 60 seconds')
    try:
        data = base64.b64decode(value.split(',', 1)[1], validate=True)
        with wave.open(io.BytesIO(data), 'rb') as wav:
            seconds = wav.getnframes() / wav.getframerate()
            if wav.getnchannels() != 1 or wav.getsampwidth() != 2 or wav.getcomptype() != 'NONE' or not 8000 <= wav.getframerate() <= 48000 or not .1 <= seconds <= 60:
                raise ValueError('Sound must be mono 16-bit WAV, 0.1–60 seconds, 8–48 kHz')
            if len(wav.readframes(wav.getnframes())) != wav.getnframes() * 2:
                raise ValueError('The WAV recording is incomplete')
    except (wave.Error, EOFError, TypeError, base64.binascii.Error) as error:
        raise ValueError('Cannot read this WAV recording') from error
    sha = hashlib.sha256(data).hexdigest(); media_id = 'sound-' + sha[:20]
    name = raw.get('name', 'Sound')
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 100:
        raise ValueError('Sound name must contain 1–100 characters')
    with scenes.lock:
        scene = scenes.get(scene_id)
        if raw.get('revision') != scene['revision']:
            raise ValueError('Scene changed since you opened it; reload before adding sound')
        if len(scene.get('media', {})) >= 24 and media_id not in scene['media']:
            raise ValueError('This scene already has 24 sound files')
        meta = {'id': media_id, 'file': f'media/{sha}.wav', 'sha256': sha, 'seconds': seconds, 'name': name.strip(), 'created': now()}
        scenes.write_media(scene_id, meta['file'], data)
        scene.setdefault('media', {})[media_id] = meta
        scene['revision'] += 1; scene['updated'] = now(); scenes._write(scene)
        return scene


def describe(scene, scenes):
    tracks, warnings = [], []
    for track in scene.get('soundtracks', []):
        meta = scene.get('media', {}).get(track['media_id'])
        try:
            data = scenes.resolve(scene['id'], meta['file']).read_bytes() if meta else b''
            if not meta or hashlib.sha256(data).hexdigest() != meta['sha256']:
                raise ValueError('missing or changed sound file')
            if track['offset'] >= meta['seconds']:
                raise ValueError('offset starts past the recording')
            tracks.append({**track, 'media': meta})
        except (OSError, KeyError, ValueError, TypeError) as error:
            warnings.append(f"Sound track {track['id']}: {error}")
    return tracks, warnings


def duck_gain(time, beats, fps):
    """10 dB reduction during actual speech with 80 ms attack and 160 ms release."""
    gain = 1.
    for beat in beats:
        if not beat.get('voice'):
            continue
        start = beat['start'] / fps; end = start + min(beat['voice']['seconds'], beat['frames'] / fps)
        if start - .08 <= time < start:
            gain = min(gain, 1 - .7 * (time - start + .08) / .08)
        elif start <= time <= end:
            gain = min(gain, .3)
        elif end < time < end + .16:
            gain = min(gain, .3 + .7 * (time - end) / .16)
    return gain


def mix_tracks(scene, timeline, scenes, mixed, rate):
    tracks, warnings = describe(scene, scenes)
    if warnings:
        raise ValueError('; '.join(warnings))
    # One shared envelope for all tracks; no sample-by-sample Python loop.
    times = np.arange(len(mixed)) / rate; envelope = np.ones(len(mixed), dtype=np.float32)
    for beat in timeline['beats']:
        if not beat.get('voice'): continue
        start = beat['start'] / timeline['fps']; end = start + min(beat['voice']['seconds'], beat['frames'] / timeline['fps'])
        envelope = np.minimum(envelope, np.interp(times, [start - .08, start, end, end + .16], [1, .3, .3, 1]))
    for track in tracks:
        with wave.open(str(scenes.resolve(scene['id'], track['media']['file'])), 'rb') as wav:
            source_rate = wav.getframerate(); data = np.frombuffer(wav.readframes(wav.getnframes()), dtype='<i2').astype(np.float32)
        if source_rate != rate:
            data = np.interp(np.arange(math.ceil(len(data) * rate / source_rate)) * source_rate / rate, np.arange(len(data)), data)
        offset = round(track['offset'] * rate); data = data[offset:]
        start = round(track['start'] * rate); count = min(round(track['duration'] * rate), len(mixed) - start)
        if count <= 0 or not len(data): continue
        if track['loop']: data = np.resize(data, count)
        count = min(count, len(data)); gain = envelope[start:start + count] if track['duck'] else 1
        mixed[start:start + count] += data[:count] * track['volume'] * gain
