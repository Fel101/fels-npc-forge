"""Isolated Scene Studio UI QA using retained real actors/speech plus local sound and an Items prop."""
import argparse
import base64
import copy
import hashlib
import io
import json
from pathlib import Path
import shutil
import struct
import sys
import time
import uuid
import wave

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from npcgen import config, items, scene_sound
from npcgen.app import App
from npcgen.library import Library
from npcgen.scenes import compile_timeline
from server import create_server


def cube():
    points = np.array([(x, y, z) for x in (-.35, .35) for y in (0, .7) for z in (-.3, .3)], dtype='<f4')
    faces = []
    for a, b, c, d in ((0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)):
        faces.extend((a, b, c, a, c, d))
    indices = np.array(faces, dtype='<u4'); blob = points.tobytes() + indices.tobytes()
    doc = {'asset': {'version': '2.0'}, 'scene': 0, 'scenes': [{'nodes': [0]}], 'nodes': [{'mesh': 0}],
        'meshes': [{'primitives': [{'attributes': {'POSITION': 0}, 'indices': 1, 'material': 0}]}],
        'materials': [{'pbrMetallicRoughness': {'baseColorFactor': [.5, .22, .06, 1], 'metallicFactor': 0, 'roughnessFactor': .85}}],
        'buffers': [{'byteLength': len(blob)}],
        'bufferViews': [{'buffer': 0, 'byteOffset': 0, 'byteLength': points.nbytes}, {'buffer': 0, 'byteOffset': points.nbytes, 'byteLength': indices.nbytes}],
        'accessors': [{'bufferView': 0, 'componentType': 5126, 'count': len(points), 'type': 'VEC3', 'min': points.min(0).tolist(), 'max': points.max(0).tolist()},
                      {'bufferView': 1, 'componentType': 5125, 'count': len(indices), 'type': 'SCALAR'}]}
    chunk = json.dumps(doc).encode(); chunk += b' ' * (-len(chunk) % 4)
    return struct.pack('<III', 0x46546c67, 2, 28 + len(chunk) + len(blob)) + struct.pack('<II', len(chunk), 0x4e4f534a) + chunk + struct.pack('<II', len(blob), 0x004e4942) + blob


def tone(frequency, seconds=2, amplitude=1200):
    out = io.BytesIO(); rate = 24000
    with wave.open(out, 'wb') as wav:
        wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(rate)
        wav.writeframes((np.sin(np.arange(round(seconds * rate)) * 2 * np.pi * frequency / rate) * amplitude).astype('<i2').tobytes())
    return out.getvalue()


def prepare():
    prior = ROOT / 'local/voice-mesh-real-20261012-231710-14476b/library'
    old = json.loads((prior / '.npcforge-scenes/scene-dcde4c20bf/scene.json').read_text('utf-8'))
    source = Library(prior); hashes = {}
    qa = ROOT / 'local' / ('scene-studio-real-' + time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:6]); qa.mkdir()
    cfg = config.load(); cfg['library_path'] = qa / 'library'
    with App(cfg) as app:
        for id in old['cast']:
            src = source.resolve(id, 'npc.json').parent
            for path in src.rglob('*'):
                if path.is_file(): hashes[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
            shutil.copytree(src, app.lib.root / id); app.lib.save(app.lib.get(id))
        third = 'casey-stage-qa-abcdef'; shutil.copytree(app.lib.root / old['cast'][0], app.lib.root / third)
        record = app.lib.get(old['cast'][0]); record['id'] = third; record['profile'].update(first_name='Casey', last_name='Stage', demeanor='warm and friendly', goal='keeping the harbor safe'); app.lib.save(record)
        prop = app.items.create(items.clean_item({'family': 'prop', 'kind': 'other', 'name': 'QA wooden crate', 'description': 'A simple local test crate'}))
        app.items.write_asset(prop['id'], 'model.glb', cube())
        app.items.update(prop['id'], lambda row: row['assets'].update(model_3d={'file': 'model.glb', 'engine': 'local-test-mesh'}))
        first, second = old['beats'][:2]; a, b = old['cast']
        raw = {**old, 'title': 'Scene Studio QA', 'cast': [a, b, third], 'background_prompt': 'Plain slate for scene direction QA',
            'actors': {a: {'position': [-1.4, 0], 'rotation': 0}, b: {'position': [1.4, 0], 'rotation': 0}, third: {'position': [0, -.8], 'rotation': 0}},
            'props': [{'id': 'crate-one', 'item_id': prop['id'], 'position': [0, 0, .6], 'rotation': 20, 'scale': 1}],
            'beats': [{'id': 'opening-pause', 'kind': 'pause', 'duration': .4, 'camera_shot': 'wide'},
                {**first, 'motion': 'walk', 'walk_to': [-.8, 0], 'camera_shot': 'medium', 'camera_move': 'push', 'camera_strength': .25,
                 'acting': {b: {'facing': 'speaker', 'look_at': 'speaker', 'gesture': 'nod'}}},
                {**second, 'motion': 'stand', 'camera_shot': 'close', 'camera_transition': .4, 'acting': {a: {'facing': 'speaker', 'look_at': 'speaker'}}},
                {'id': 'pan-left', 'kind': 'reaction', 'speaker_id': third, 'duration': 1, 'camera_shot': 'wide', 'camera_move': 'pan_left'},
                {'id': 'pan-right', 'kind': 'reaction', 'speaker_id': third, 'duration': 1, 'camera_shot': 'shoulder', 'camera_move': 'pan_right', 'acting': {third: {'gesture': 'point'}}},
                {'id': 'tracking', 'kind': 'reaction', 'speaker_id': third, 'duration': 1, 'motion': 'walk', 'walk_to': [.5, -.8], 'camera_shot': 'medium', 'camera_move': 'track'}]}
        scene = app.scenes.create(raw)
        for beat in (first, second):
            meta = copy.deepcopy(old['audio'][beat['id']]); path = prior / '.npcforge-scenes' / old['id'] / meta['file']; data = path.read_bytes()
            hashes[str(path)] = hashlib.sha256(data).hexdigest(); app.scenes.write_audio(scene['id'], meta['file'], data); assert app.scenes.set_audio(scene['id'], beat['id'], meta)
        scene = app.scenes.get(scene['id'])
        for title, frequency, kind, volume in [('Ambience', 220, 'ambience', .2), ('Music', 330, 'music', .15)]:
            wav = tone(frequency); (qa / (title.lower() + '.wav')).write_bytes(wav)
            scene = scene_sound.upload(app.scenes, scene['id'], {'revision': scene['revision'], 'name': title, 'audio': 'data:audio/wav;base64,' + base64.b64encode(wav).decode()})
            meta = next(row for row in scene['media'].values() if row['name'] == title)
            scene['soundtracks'].append({'id': 'track-' + title.lower(), 'media_id': meta['id'], 'kind': kind, 'start': 0, 'offset': 0, 'duration': 15, 'volume': volume, 'loop': True, 'duck': True})
            scene = app.scenes.update(scene['id'], scene)
        (qa / 'effect.wav').write_bytes(tone(1100, .2, 3000))
        timeline = compile_timeline(scene, app.lib, app.scenes)
        assert sum(bool(beat.get('voice')) for beat in timeline['beats']) == 2
        (qa / 'source-hashes.json').write_text(json.dumps(hashes, indent=2), encoding='utf-8')
        (qa / 'fixture.json').write_text(json.dumps({'scene_id': scene['id'], 'timeline': timeline, 'item_id': prop['id']}, indent=2), encoding='utf-8')
        httpd = create_server(('127.0.0.1', 0), app)
        print('ROOT ' + str(qa), flush=True); print('SCENE ' + scene['id'], flush=True); print('URL http://127.0.0.1:' + str(httpd.server_port) + '/', flush=True)
        httpd.serve_forever()


def verify(qa):
    from check_scene_director_real import verify as verify_director
    verify_director(qa)
    from npcgen.scenes import Scenes
    import zipfile
    qa = Path(qa); lib = Library(qa / 'library'); scenes = Scenes(lib)
    scene = scenes.get(json.loads((qa / 'fixture.json').read_text('utf-8'))['scene_id'])
    assert len(scene['cast']) >= 3 and scene['props'] and scene['soundtracks']
    with zipfile.ZipFile(qa / 'scene-package.zip') as archive:
        manifest = json.loads(archive.read('sources.json'))
        assert manifest['props'] and len(manifest['soundtracks']) >= 2
        for meta in [*scene['media'].values(), *scene['prop_sources'].values()]:
            assert hashlib.sha256(archive.read(meta['file'])).hexdigest() == meta['sha256']
    report = json.loads((qa / 'report.json').read_text('utf-8')); report['studio_package'] = 'passed'
    report['studio_limits'] = 'Retained real actors/speech, generated test tones and local cube prop; no body/voice identity claim.'
    (qa / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print('PASS scene studio package, ensemble, sound and prop hashes')


def serve(qa, port):
    qa = Path(qa).resolve()
    if not qa.is_relative_to(ROOT / 'local') or not (qa / 'fixture.json').is_file():
        raise ValueError('Choose an existing isolated scene QA folder under local')
    cfg = config.load(); cfg['library_path'] = qa / 'library'
    with App(cfg) as app:
        server = create_server(('127.0.0.1', port), app)
        print('URL http://127.0.0.1:' + str(server.server_port) + '/', flush=True); server.serve_forever()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument('--verify', metavar='QA_ROOT')
    parser.add_argument('--serve', metavar='QA_ROOT'); parser.add_argument('--port', type=int, default=0); args = parser.parse_args()
    if args.verify: verify(args.verify)
    elif args.serve: serve(args.serve, args.port)
    else: prepare()
