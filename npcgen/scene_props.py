"""Resolve Items-tab props and pin their GLB bytes when a scene is saved."""
import hashlib
import json
import struct

from . import licences


def item_model(lib, item_id):
    base = (lib.root / '.npcforge-items' / item_id).resolve()
    record = json.loads((base / 'item.json').read_text('utf-8'))
    meta = (record.get('assets') or {}).get('model_3d') or {}
    rel = meta.get('file')
    if not isinstance(rel, str):
        raise ValueError('The item needs a 3D model')
    path = (base / rel).resolve()
    if not path.is_relative_to(base):
        raise ValueError('Invalid item model path')
    data = path.read_bytes()
    if len(data) < 20 or data[:4] != b'glTF' or struct.unpack_from('<I', data, 4)[0] != 2 or struct.unpack_from('<I', data, 8)[0] != len(data):
        raise ValueError('The item does not have a valid GLB model')
    return record, meta, data


def pin(scene, scenes, previous=None):
    sources = {}
    for prop in scene.get('props', []):
        old = (previous or {}).get('prop_sources', {}).get(prop['id'])
        if old and old.get('item_id') == prop['item_id']:
            sources[prop['id']] = old
            continue
        try:
            item, meta, data = item_model(scenes.lib, prop['item_id'])
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise ValueError(f"Cannot place prop {prop['item_id']}: {error}") from error
        data = licences.label_shape(data, meta)
        sha = hashlib.sha256(data).hexdigest(); filename = f'props/{sha}.glb'
        scenes.write_media(scene['id'], filename, data)
        sources[prop['id']] = {'item_id': prop['item_id'], 'name': item.get('name', prop['item_id']),
            'file': filename, 'sha256': sha, 'engine': meta.get('engine'), 'created': meta.get('created')}
    scene['prop_sources'] = sources


def describe(scene, scenes):
    rows, warnings = [], []
    for prop in scene.get('props', []):
        source = scene.get('prop_sources', {}).get(prop['id'])
        try:
            if source and source.get('item_id') == prop['item_id']:
                data = scenes.resolve(scene['id'], source['file']).read_bytes()
                if hashlib.sha256(data).hexdigest() != source['sha256']:
                    raise ValueError('the saved prop model changed')
                url = f"/api/scenes/{scene['id']}/files/{source['file']}"
            else:
                item, meta, data = item_model(scenes.lib, prop['item_id'])
                source = {'item_id': prop['item_id'], 'name': item.get('name'), 'sha256': hashlib.sha256(data).hexdigest()}
                url = f"/api/items/{prop['item_id']}/files/{meta['file']}"
            rows.append({**prop, 'source': source, 'url': url})
        except (OSError, ValueError, KeyError, TypeError) as error:
            warnings.append(f"Prop {prop['id']}: {error}")
    return rows, warnings
