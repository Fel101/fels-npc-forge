"""Game playback artifacts derived only from checked delivered-audio metadata."""
import csv
import io
import json
import math
from pathlib import Path

from . import alignment

TEMPLATES = Path(__file__).with_name('game_templates')
TARGETS = {shape: ('' if shape == 'sil' else f'viseme_{shape}') for shape in alignment.VISEMES}
TARGETS['mouth_open'] = 'jawOpen'


def clip(lip_sync, mouth, audio_file):
    """Dense weights plus exact audio binding; no inferred phonemes for fallback."""
    seconds = lip_sync['seconds']
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError('Game lip-sync audio needs a positive duration')
    track = lip_sync.get('visemes') if lip_sync.get('status') == 'aligned' else None
    if track:
        alignment.validate(lip_sync, seconds)
        alignment.validate_visemes(track, seconds)
        fps, frames, method = track['fps'], track['frames'], 'visemes'
        groups = alignment.VISEMES
    else:
        fps, method, groups = lip_sync.get('mouth_fps', 25), 'amplitude_fallback', ['sil', 'mouth_open']
        if isinstance(fps, bool) or not isinstance(fps, int) or not 1 <= fps <= 120:
            raise ValueError('Invalid mouth frame rate')
        if not isinstance(mouth, list) or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not 0 <= v <= 1 for v in mouth):
            raise ValueError('Amplitude mouth values must be finite values from zero to one')
        frames = []
        for index in range(math.ceil(seconds * fps)):
            value = mouth[index] if index < len(mouth) else 0.
            frames.append({'time': index / fps, 'weights': {'sil': 1 - value, 'mouth_open': value}})
        frames.append({'time': seconds, 'weights': {'sil': 1., 'mouth_open': 0.}})
    return {'schema': 1, 'audio_file': audio_file, 'audio_sha256': lip_sync['audio_sha256'],
            'seconds': seconds, 'sample_rate': lip_sync['sample_rate'], 'samples': lip_sync['samples'],
            'status': lip_sync['status'], 'reason': lip_sync.get('reason'), 'method': method,
            'fps': fps, 'interpolation': 'constant', 'groups': groups,
            'frames': [{'time': frame['time'], 'weights': {g: frame['weights'].get(g, 0.) for g in groups}} for frame in frames]}


def curves(data):
    """Epic CurveTable CSV: X=seconds, Y=normalized weight, one row per group."""
    out = io.StringIO(newline='')
    writer = csv.writer(out, lineterminator='\n')
    writer.writerow(['Name', *[format(frame['time'], '.9g') for frame in data['frames']]])
    for group in data['groups']:
        writer.writerow([group, *[format(frame['weights'][group], '.9g') for frame in data['frames']]])
    return out.getvalue()


def write_clip(archive, root, name, lip_sync, mouth, audio_file):
    data = clip(lip_sync, mouth, audio_file)
    base = f'{root}game/clips/{name}'
    archive.writestr(base + '.json', json.dumps(data, indent=2, ensure_ascii=False))
    archive.writestr(base + '.curves.csv', curves(data))
    return {'clip': f'game/clips/{name}.json', 'unreal_curves': f'game/clips/{name}.curves.csv',
            'method': data['method'], 'audio_sha256': data['audio_sha256']}


def write_support(archive, root=''):
    archive.writestr(root + 'game/targets.json', json.dumps({'schema': 1, 'targets': TARGETS,
        'note': 'Edit target names to match your character. Empty sil means neutral rest. No rig is generated.'}, indent=2))
    for file in ('godot/forge_lip_player.gd', 'godot/forge_mesh_mouth.gd', 'unreal/import_curves.py', 'README.md'):
        archive.writestr(root + 'game/' + file, (TEMPLATES / file).read_bytes())
