"""Scene-owned alternatives retain their delivery and mouth timing together."""
import copy
import hashlib

from .library import now
from .voice import authoring, effects


def take_id(meta):
    return hashlib.sha256(meta['file'].encode()).hexdigest()[:16]


def record(scene, beat_id, meta):
    groups = scene.setdefault('voice_takes', {})
    group = groups.setdefault(beat_id, {'takes': [], 'selected_id': None, 'approved_id': None})
    old = scene.get('audio', {}).get(beat_id)
    for clip in (old, meta):
        if clip and not any(row['id'] == take_id(clip) for row in group['takes']):
            group['takes'].append({'id': take_id(clip), 'meta': copy.deepcopy(clip)})
    # An approved performance stays selected when more alternatives finish generating.
    same_delivery = old and all(old.get(k) == meta.get(k) for k in
        ('speaker_id', 'text', 'mood', 'render_signature', 'voice_signature'))
    if not same_delivery or not group.get('approved_id') or group.get('selected_id') != group['approved_id']:
        group['selected_id'] = take_id(meta)
        group['approved_id'] = None
        scene.setdefault('audio', {})[beat_id] = copy.deepcopy(meta)
    return group


def matches(scene, beat, meta, lib):
    from .scenes import scene_voice_of, voice_signature
    if any(meta.get(k) != beat.get(k, 'neutral' if k == 'mood' else None) for k in ('speaker_id', 'text', 'mood')):
        return False
    npc = lib.get(beat['speaker_id'])
    override = scene.get('voice_overrides', {}).get(beat['speaker_id'])
    binding = scene.get('voice_bindings', {}).get(beat['speaker_id'])
    if meta.get('render_signature'):
        _, _, inputs = authoring.prepare(scene_voice_of(npc, override, binding), beat['text'], beat.get('mood', 'neutral'),
                                        preset_id=beat.get('performance_id'))
        return meta['render_signature'] == inputs['render_signature']
    return meta.get('voice_signature') == voice_signature(npc, override, binding)


def describe(scene, scenes):
    result = {}
    beats = {beat['id']: beat for beat in scene['beats'] if beat['kind'] == 'line'}
    for beat_id, group in scene.get('voice_takes', {}).items():
        rows = []
        for take in group.get('takes', []):
            current, reason = False, None
            try:
                meta = take['meta']; beat = beats.get(beat_id)
                if not beat or not matches(scene, beat, meta, scenes.lib):
                    raise ValueError('Earlier words or voice settings')
                data = effects.check_delivery(scenes.resolve(scene['id'], meta['file']).read_bytes(), meta)
                if meta.get('sha256') and hashlib.sha256(data).hexdigest() != meta['sha256']:
                    raise ValueError('Saved take checksum failed')
                current = True
            except (OSError, KeyError, TypeError, ValueError) as error:
                reason = str(error)
            rows.append({'id': take['id'], 'current': current, 'reason': reason,
                'selected': take['id'] == group.get('selected_id'),
                'approved': current and take['id'] == group.get('approved_id')})
        result[beat_id] = rows
    return result


def select(scenes, scene_id, beat_id, raw):
    if not isinstance(raw, dict) or not isinstance(raw.get('approve', False), bool):
        raise ValueError('Take approval must be true or false')
    with scenes.lock:
        scene = scenes.get(scene_id)
        if raw.get('revision') != scene['revision']:
            raise ValueError('Scene changed since you opened it; reload before selecting a take')
        beat = next((b for b in scene['beats'] if b['id'] == beat_id and b['kind'] == 'line'), None)
        group = scene.get('voice_takes', {}).get(beat_id)
        row = next((r for r in (group or {}).get('takes', []) if r['id'] == raw.get('take_id')), None)
        if not beat or not row:
            raise ValueError('This scene voice take is missing')
        if not matches(scene, beat, row['meta'], scenes.lib):
            raise ValueError('This take uses earlier words or voice settings; generate a current take')
        data = effects.check_delivery(scenes.resolve(scene_id, row['meta']['file']).read_bytes(), row['meta'])
        if row['meta'].get('sha256') and hashlib.sha256(data).hexdigest() != row['meta']['sha256']:
            raise ValueError('Saved take checksum failed; generate a new take')
        group['selected_id'] = row['id']
        group['approved_id'] = row['id'] if raw.get('approve') else None
        group['reviewed_at'] = now()
        scene.setdefault('audio', {})[beat_id] = copy.deepcopy(row['meta'])
        scene['revision'] += 1
        scene['updated'] = now()
        scenes._write(scene)
        return copy.deepcopy(scene)
