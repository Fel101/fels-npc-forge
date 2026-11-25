"""Character-owned, immutable 2D mouth frames; independent of voice identity/audio."""
import base64
import bisect
import hashlib
import io
import json
import math
import re

import numpy as np
from PIL import Image, ImageOps

from .. import licences, media
from ..library import now
from .alignment import VISEMES

KINDS = ('portrait', 'sprite')
FRAME_ID = re.compile(r'^[a-z][a-z0-9_-]{0,31}$')


def _kind(kind):
    if kind not in KINDS:
        raise ValueError('Choose portrait or sprite mouth artwork')


def _decode_png(data, size=None):
    with Image.open(io.BytesIO(data)) as original:
        if original.width > 4096 or original.height > 4096 or getattr(original, 'n_frames', 1) != 1:
            raise ValueError('Use a single image no larger than 4096 pixels per side')
        image = ImageOps.exif_transpose(original).convert('RGBA')
        if size and image.size != tuple(size):
            raise ValueError('Mouth frames must match the saved preview dimensions')
        if not size:
            image.thumbnail((512, 512), Image.Resampling.LANCZOS)
        out = io.BytesIO(); image.save(out, 'PNG')   # normalized pixels, labelled with their source at _store
        return out.getvalue(), list(image.size)


def _png(data, size=None):
    try: return _decode_png(data, size)
    except (OSError, Image.DecompressionBombError) as error:
        raise ValueError('Cannot read this mouth-artwork image') from error


def _source(lib, npc, kind):
    _kind(kind)
    file = ((npc.get('assets') or {}).get(kind) or {}).get('file')
    if not file:
        raise ValueError(f'Generate or import a {kind} first')
    data = lib.read_asset(npc['id'], file)
    png, size = _png(data)
    return {'kind': kind, 'file': file, 'sha256': hashlib.sha256(data).hexdigest(), 'size': size}, png


def _store(lib, npc_id, kind, data, size, *, models=None, ai_generated=True):
    data = media.label(data, models, ai_generated=ai_generated)
    digest = hashlib.sha256(data).hexdigest()
    file = f'voice/visual-rigs/{kind}/frames/{digest}.png'
    path = lib.resolve(npc_id, file); path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open('xb') as stream: stream.write(data)
    except FileExistsError:
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError('Immutable mouth-frame checksum failed')
    return {'file': file, 'sha256': digest, 'size': size}


def _commit(lib, npc, kind, rig):
    rig['created'] = now()
    data = json.dumps(rig, sort_keys=True).encode()
    version = hashlib.sha256(data).hexdigest()
    file = f'voice/visual-rigs/{kind}/versions/{version}.json'
    path = lib.resolve(npc['id'], file); path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open('xb') as stream: stream.write(data)
    except FileExistsError:
        if path.read_bytes() != data: raise ValueError('Mouth-rig version checksum failed')
    saved = {**rig, 'version': version, 'version_file': file}
    def apply(record):
        record.setdefault('voice_rigs', {})[kind] = saved
        history = record.setdefault('voice_rig_versions', {}).setdefault(kind, [])
        if not any(item['version'] == version for item in history):
            history.append({'version': version, 'file': file, 'created': rig['created']})
    return lib.update(npc['id'], apply)


def save(lib, npc_id, kind, body):
    _kind(kind)
    if not isinstance(body, dict) or set(body) - {'enabled', 'mapping', 'amplitude_open', 'reset'}:
        raise ValueError('Mouth artwork accepts enabled, mapping, amplitude_open and reset')
    with lib.lock:
        npc = lib.get(npc_id); source, png = _source(lib, npc, kind)
        previous = (npc.get('voice_rigs') or {}).get(kind)
        if previous and not body.get('reset'):
            if previous['source']['sha256'] != source['sha256']:
                raise ValueError('The character artwork changed; rebind its mouth frames first')
            frames = previous['frames']
        else:
            portrait_model = licences.picture_engine(npc['assets'][kind])
            frames = {'rest': _store(lib, npc_id, kind, png, source['size'], models=[portrait_model])}
            if kind == 'portrait':
                try:
                    living = json.loads(lib.read_asset(npc_id, 'living/living.json'))
                    if living.get('portrait', {}).get('sha256') == source['sha256'] and 'talking' in living.get('frames', {}):
                        talking = lib.read_asset(npc_id, 'living/talking.png')
                        data, size = _png(talking)
                        models = media.source_models(talking) or [portrait_model, licences.picture_engine(
                            (npc['assets'].get('expressions') or {}).get('talking'), edit=True)]
                        if size == source['size']: frames['talking'] = _store(lib, npc_id, kind, data, size, models=models)
                except (FileNotFoundError, KeyError, ValueError): pass
        opened = 'talking' if 'talking' in frames else 'rest'
        mapping = body.get('mapping') or (previous.get('mapping') if previous and not body.get('reset') else None) or {
            shape: 'rest' if shape in ('sil', 'PP') else opened for shape in VISEMES}
        amplitude = body.get('amplitude_open', previous.get('amplitude_open', opened) if previous and not body.get('reset') else opened)
        enabled = body.get('enabled', True)
        if not isinstance(enabled, bool) or not isinstance(body.get('reset', False), bool):
            raise ValueError('Mouth artwork enabled/reset must be true or false')
        if not isinstance(mapping, dict) or set(mapping) != set(VISEMES) or any(value not in frames for value in mapping.values()) or amplitude not in frames:
            raise ValueError('Map every viseme and the amplitude-open pose to a saved mouth frame')
        return _commit(lib, npc, kind, {'schema': 1, 'enabled': enabled, 'source': source, 'frames': frames,
            'mapping': mapping, 'amplitude_open': amplitude, 'interpolation': 'dominant' if kind == 'sprite' else 'weighted'})


def upload(lib, npc_id, kind, body):
    _kind(kind)
    name = body.get('name')
    if not isinstance(name, str) or not FRAME_ID.fullmatch(name) or name in ('rest', 'talking'):
        raise ValueError('Use a short frame name; rest and talking are preserved source frames')
    try:
        data = base64.b64decode(body.get('image', '').split(',')[-1], validate=True)
    except (ValueError, AttributeError): raise ValueError('Choose a PNG mouth frame') from None
    if len(data) > 1_450_000: raise ValueError('The preview PNG must be smaller than 1.45 MB')
    with lib.lock:
        npc = lib.get(npc_id); source, _ = _source(lib, npc, kind)
        rig = (npc.get('voice_rigs') or {}).get(kind)
        if not rig or rig['source']['sha256'] != source['sha256']:
            raise ValueError('Save or rebind mouth artwork before importing frames')
        if len(rig['frames']) >= 32 and name not in rig['frames']:
            raise ValueError('Use at most 32 frames per character visual')
        png, size = _png(data, source['size'])
        # The approved source art is part of the editor's canvas. The uploaded pixels can also contain artist edits.
        models = media.source_models(data) or [licences.picture_engine(npc['assets'][kind])]
        frames = {**rig['frames'], name: _store(lib, npc_id, kind, png, size, models=models)}
        return _commit(lib, npc, kind, {k: v for k, v in {**rig, 'frames': frames}.items() if k not in ('version', 'version_file')})


def restore(lib, npc_id, kind, version):
    _kind(kind)
    with lib.lock:
        npc=lib.get(npc_id)
        if not re.fullmatch('[a-f0-9]{64}',version) or not any(row['version']==version for row in (npc.get('voice_rig_versions') or {}).get(kind,[])):
            raise ValueError('Choose a saved mouth-artwork version from this character')
        file=f'voice/visual-rigs/{kind}/versions/{version}.json';data=lib.read_asset(npc_id,file)
        if hashlib.sha256(data).hexdigest()!=version:raise ValueError('Mouth-artwork version checksum failed')
        rig={**json.loads(data),'version':version,'version_file':file}
        candidate={**npc,'voice_rigs':{**npc.get('voice_rigs',{}),kind:rig}}
        checked=describe(lib,candidate,kind)
        if checked['status'] not in ('ready','disabled'):raise ValueError(checked['reason'])
        return lib.update(npc_id,lambda rec:rec.setdefault('voice_rigs',{}).update({kind:rig}))


def describe(lib, npc, kind):
    _kind(kind)
    rig = (npc.get('voice_rigs') or {}).get(kind)
    try:
        source, _ = _source(lib, npc, kind)
        if not rig: return {'status': 'unconfigured', 'source': source, 'reason': 'Save mouth artwork to create immutable preview frames'}
        if source['sha256'] != rig['source']['sha256']:
            raise ValueError('The character artwork changed; rebind its mouth frames')
        snapshot = lib.read_asset(npc['id'], rig['version_file'])
        if hashlib.sha256(snapshot).hexdigest() != rig['version'] or json.loads(snapshot) != {k:v for k,v in rig.items() if k not in ('version','version_file')}:
            raise ValueError('The mouth-artwork configuration changed outside its saved version')
        if set(rig['mapping']) != set(VISEMES) or any(name not in rig['frames'] for name in rig['mapping'].values()) or rig['amplitude_open'] not in rig['frames']:
            raise ValueError('A mouth-artwork mapping is invalid')
        for frame in rig['frames'].values():
            if not re.fullmatch(f'voice/visual-rigs/{kind}/frames/[a-f0-9]{{64}}\\.png', frame['file']) or hashlib.sha256(lib.read_asset(npc['id'], frame['file'])).hexdigest() != frame['sha256']:
                raise ValueError('A mouth frame is missing or changed')
        return {**rig, 'status': 'ready' if rig['enabled'] else 'disabled', 'reason': None,
            'versions': (npc.get('voice_rig_versions') or {}).get(kind, []),
            'frames': {name: {**frame, 'url': f"/api/npcs/{npc['id']}/files/{frame['file']}"} for name, frame in rig['frames'].items()}}
    except (KeyError, FileNotFoundError, ValueError) as error:
        return {**(rig or {}), 'status': 'stale' if rig else 'missing', 'reason': str(error)}


def weights_at(lip, mouth, seconds):
    if seconds < 0 or seconds >= lip.get('seconds', 0): return {'sil': 1.}
    frames = (lip.get('visemes') or {}).get('frames')
    if lip.get('status') == 'aligned' and frames:
        index = max(0, bisect.bisect_right([f['time'] for f in frames], seconds) - 1)
        return frames[index]['weights']
    index = int(seconds * lip.get('mouth_fps', 25))
    value = mouth[index] if index < len(mouth) else 0.
    value = min(1., max(0., value)) if isinstance(value, (int, float)) and math.isfinite(value) else 0.
    return {'sil': 1 - value, 'mouth_open': value}


def frame_weights(rig, weights):
    values = {}
    for group, weight in weights.items():
        name = rig['amplitude_open'] if group == 'mouth_open' else rig['mapping'].get(group, rig['mapping']['sil'])
        values[name] = values.get(name, 0.) + weight
    if rig['interpolation'] == 'dominant':
        name = max(values, key=values.get) if values else rig['mapping']['sil']
        return {name: 1.}
    return values or {rig['mapping']['sil']: 1.}


def image(lib, npc_id, rig, weights, cache=None):
    cache = {} if cache is None else cache
    values = frame_weights(rig, weights)
    result = None
    for name, weight in values.items():
        if weight <= 0: continue
        frame = rig['frames'][name]
        if frame['sha256'] not in cache:
            with Image.open(lib.resolve(npc_id, frame['file'])) as original:
                rgba = np.asarray(original.convert('RGBA'), dtype=np.float32) / 255
                rgba[:, :, :3] *= rgba[:, :, 3:4]
                cache[frame['sha256']] = rgba
        pixels = cache[frame['sha256']] * weight
        result = pixels.copy() if result is None else result + pixels
    result[:, :, :3] /= np.maximum(result[:, :, 3:4], 1e-8)
    return Image.fromarray(np.uint8(np.clip(np.rint(result * 255), 0, 255)), 'RGBA')


def write_exports(archive, lib, npc, root='', folder='game/character-rigs'):
    """Copy verified current frames and artist maps; original full-size art stays local."""
    exported = {}
    for kind in KINDS:
        rig = describe(lib, npc, kind)
        if rig.get('status') != 'ready': continue
        frames, written = {}, set()
        for name, frame in rig['frames'].items():
            original = lib.read_asset(npc['id'], frame['file'])
            models = media.source_models(original) or [licences.picture_engine(npc['assets'][kind])]
            data = media.label(original, models)
            sha256 = hashlib.sha256(data).hexdigest()
            file = f"{folder}/{kind}/frames/{sha256}.png"
            if file not in written:
                archive.writestr(root+file, data)
                written.add(file)
            frames[name] = {'file': file, 'sha256': sha256, 'size': frame['size']}
        file = f'{folder}/{kind}/rig.json'
        data = {'schema':1, 'kind':kind, 'forge_version':rig['version'], 'source_sha256':rig['source']['sha256'],
            'size':rig['source']['size'], 'frames':frames, 'mapping':rig['mapping'], 'amplitude_open':rig['amplitude_open'],
            'interpolation':rig['interpolation']}
        archive.writestr(root+file, json.dumps(data, indent=2)); exported[kind] = file
    return exported
