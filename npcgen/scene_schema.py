"""Validated authoring fields shared by legacy scenes and the Scene Studio editor."""
import math
import re

KEY = re.compile(r'^[a-z0-9][a-z0-9_-]{1,64}$')
EXPRESSIONS = {'', 'happy', 'sad', 'angry', 'surprised', 'scared', 'laughing', 'tired', 'smug', 'embarrassed', 'thinking', 'flirty'}
GESTURES = {'none', 'wave', 'nod', 'shake', 'point'}
CAMERA_MOVES = {'static', 'follow', 'push', 'pan_left', 'pan_right', 'track'}


def number(value, label, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f'{label} must be a number from {low} to {high}')
    return round(float(value), 3)


def vector(value, label, bounds):
    if not isinstance(value, list) or len(value) != len(bounds):
        raise ValueError(f'{label} needs {len(bounds)} coordinates')
    return [number(v, label, *limits) for v, limits in zip(value, bounds)]


def choice(value, values, label):
    if not isinstance(value, str) or value not in values:
        raise ValueError(f'Invalid {label}')
    return value


def key(value, label):
    if not isinstance(value, str) or not KEY.fullmatch(value):
        raise ValueError(f'Invalid {label}')
    return value


def default_position(index, count):
    if count == 2:
        return [-1.35 if index == 0 else 1.35, 0]
    return [round(-2.7 + 5.4 * index / max(1, count - 1), 3), 0]


def scene_fields(raw, cast):
    actors = raw.get('actors') or {}
    if not isinstance(actors, dict) or set(actors) - set(cast):
        raise ValueError('Stage actors must belong to the cast')
    clean_actors = {}
    for index, npc_id in enumerate(cast):
        actor = actors.get(npc_id, {})
        if not isinstance(actor, dict):
            raise ValueError('Stage actor must be an object')
        clean_actors[npc_id] = {
            'position': vector(actor.get('position', default_position(index, len(cast))), 'Actor position', [(-3, 3), (-2, 2)]),
            'rotation': number(actor.get('rotation', 0), 'Actor rotation', -180, 180)}
    props = raw.get('props') or []
    if not isinstance(props, list) or len(props) > 16:
        raise ValueError('Choose at most 16 stage props')
    clean_props, ids = [], set()
    for prop in props:
        if not isinstance(prop, dict):
            raise ValueError('Stage prop must be an object')
        prop_id = key(prop.get('id'), 'prop id')
        if prop_id in ids:
            raise ValueError('Repeated prop id')
        ids.add(prop_id)
        clean_props.append({'id': prop_id, 'item_id': key(prop.get('item_id'), 'item id'),
            'position': vector(prop.get('position', [0, 0, 0]), 'Prop position', [(-3, 3), (0, 3), (-2, 2)]),
            'rotation': number(prop.get('rotation', 0), 'Prop rotation', -180, 180),
            'scale': number(prop.get('scale', 1), 'Prop scale', .05, 10)})
    tracks = raw.get('soundtracks') or []
    if not isinstance(tracks, list) or len(tracks) > 12:
        raise ValueError('Choose at most 12 sound tracks')
    clean_tracks, ids = [], set()
    for track in tracks:
        if not isinstance(track, dict):
            raise ValueError('Sound track must be an object')
        track_id = key(track.get('id'), 'track id')
        if track_id in ids:
            raise ValueError('Repeated track id')
        ids.add(track_id)
        for flag in ('loop', 'duck'):
            if not isinstance(track.get(flag, flag == 'duck'), bool):
                raise ValueError(f'Track {flag} must be true or false')
        clean_tracks.append({'id': track_id, 'media_id': key(track.get('media_id'), 'sound media id'),
            'kind': choice(track.get('kind', 'ambience'), {'ambience', 'music', 'effect'}, 'sound track kind'),
            'start': number(track.get('start', 0), 'Track start', 0, 60),
            'offset': number(track.get('offset', 0), 'Track offset', 0, 600),
            'duration': number(track.get('duration', 10), 'Track duration', .1, 60),
            'volume': number(track.get('volume', .5), 'Track volume', 0, 1),
            'loop': track.get('loop', False), 'duck': track.get('duck', True)})
    return {'actors': clean_actors, 'props': clean_props, 'soundtracks': clean_tracks}


def beat_fields(beat, cast):
    acting = beat.get('acting') or {}
    if not isinstance(acting, dict) or set(acting) - set(cast):
        raise ValueError('Acting targets must belong to the cast')
    clean = {}
    for npc_id, actor in acting.items():
        if not isinstance(actor, dict):
            raise ValueError('Acting direction must be an object')
        clean[npc_id] = {
            'facing': choice(actor.get('facing', 'rotation'), {'rotation', 'speaker', 'camera', *cast}, 'facing target'),
            'rotation': number(actor.get('rotation', 0), 'Facing rotation', -180, 180),
            'look_at': choice(actor.get('look_at', ''), {'', 'speaker', 'camera', *cast}, 'look target'),
            'gesture': choice(actor.get('gesture', 'none'), GESTURES, 'gesture'),
            'expression': choice(actor.get('expression', ''), EXPRESSIONS, 'acting expression')}
    return {'acting': clean,
        'camera_move': choice(beat.get('camera_move', 'follow'), CAMERA_MOVES, 'camera movement'),
        'camera_strength': number(beat.get('camera_strength', .3), 'Camera movement amount', 0, 1),
        'camera_transition': number(beat.get('camera_transition', 0), 'Camera transition', 0, 3)}
