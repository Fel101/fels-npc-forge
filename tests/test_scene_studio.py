import copy
import io
import json
import shutil
import subprocess
import wave
from pathlib import Path

import pytest

from npcgen.library import Library
from npcgen.scenes import Scenes, clean_scene, compile_timeline, voice_signature
from npcgen.scene_takes import select


@pytest.fixture
def studio(tmp_path):
    lib = Library(tmp_path / 'library')
    cast = [lib.create({'profile': {'first_name': name, 'last_name': 'Test', 'age': 30}, 'setting': 'modern', 'assets': {}})['id']
            for name in ('Ada', 'Bo', 'Cy')]
    raw = {'title': 'Ensemble', 'cast': cast, 'mode': 'stage3d', 'background_prompt': 'A quiet room',
           'beats': [{'id': 'line-one', 'kind': 'line', 'speaker_id': cast[0], 'text': 'Hello.', 'mood': 'neutral'}]}
    return lib, Scenes(lib), raw


def test_ensemble_and_direction_contract_is_validated_and_legacy_positions_remain(studio):
    lib, scenes, raw = studio
    scene = scenes.create(raw)
    assert len(scene['cast']) == 3 and len(scene['actors']) == 3
    assert len(compile_timeline(scene, lib)['mesh_rigs']) == 3
    legacy = clean_scene({**raw, 'cast': raw['cast'][:2]}, lib)
    assert [row['position'] for row in legacy['actors'].values()] == [[-1.35, 0], [1.35, 0]]
    directed = copy.deepcopy(raw)
    directed['actors'] = {raw['cast'][0]: {'position': [0, 1], 'rotation': 90}}
    directed['beats'][0].update(camera_move='push', camera_transition=.6, acting={raw['cast'][1]: {'look_at': 'speaker', 'gesture': 'nod'}})
    clean = clean_scene(directed, lib)
    assert clean['beats'][0]['acting'][raw['cast'][1]]['look_at'] == 'speaker'
    for field, value in [('camera_strength', float('nan')), ('camera_transition', True), ('camera_move', []), ('acting', {'outsider': {}})]:
        bad = copy.deepcopy(raw); bad['beats'][0][field] = value
        with pytest.raises(ValueError): clean_scene(bad, lib)
    for field, value in [('actors', {raw['cast'][0]: {'position': [99, 0]}}), ('props', [{'id': '../bad'}]), ('soundtracks', [{'id': 'track-one', 'media_id': '../outside'}])]:
        with pytest.raises(ValueError): clean_scene({**raw, field: value}, lib)


def clip(lib, speaker, filename, text='Hello.'):
    stream = io.BytesIO()
    with wave.open(stream, 'wb') as wav:
        wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(24000); wav.writeframes(b'\0\0' * 12000)
    return stream.getvalue(), {'file': filename, 'speaker_id': speaker, 'text': text, 'mood': 'neutral',
        'voice_signature': voice_signature(lib.get(speaker)), 'seconds': .5, 'mouth': [.2] * 13}


def test_retained_takes_approval_and_duplicate_preserve_sources(studio):
    lib, scenes, raw = studio; scene = scenes.create(raw); npc_id = raw['cast'][0]
    for name in ('first', 'second'):
        data, meta = clip(lib, npc_id, f'audio/{name}.wav'); scenes.write_audio(scene['id'], meta['file'], data)
        assert scenes.set_audio(scene['id'], 'line-one', meta)
    current = scenes.get(scene['id']); group = current['voice_takes']['line-one']; first_id = group['takes'][0]['id']
    assert scenes.resolve(scene['id'], 'audio/first.wav').is_file()
    approved = select(scenes, scene['id'], 'line-one', {'revision': current['revision'], 'take_id': first_id, 'approve': True})
    data, meta = clip(lib, npc_id, 'audio/third.wav'); scenes.write_audio(scene['id'], meta['file'], data)
    scenes.set_audio(scene['id'], 'line-one', meta)
    final = scenes.get(scene['id']); assert final['audio']['line-one']['file'] == 'audio/first.wav'
    assert final['voice_takes']['line-one']['approved_id'] == first_id
    duplicate = scenes.duplicate(scene['id'])
    assert duplicate['id'] != final['id'] and duplicate['render'] is None
    assert duplicate['voice_takes'] == final['voice_takes']
    for filename in ('first', 'second', 'third'):
        assert scenes.resolve(duplicate['id'], f'audio/{filename}.wav').read_bytes() == data
    assert scenes.get(scene['id']) == final
    with pytest.raises(ValueError, match='changed since'):
        select(scenes, scene['id'], 'line-one', {'revision': current['revision'], 'take_id': first_id})
    changed = copy.deepcopy(final); changed['beats'][0]['text'] = 'Different words.'
    changed = scenes.update(scene['id'], changed)
    with pytest.raises(ValueError, match='earlier words'):
        select(scenes, scene['id'], 'line-one', {'revision': changed['revision'], 'take_id': first_id, 'approve': True})
    data, meta = clip(lib, npc_id, 'audio/new-words.wav', 'Different words.'); scenes.write_audio(scene['id'], meta['file'], data)
    scenes.set_audio(scene['id'], 'line-one', meta)
    assert scenes.get(scene['id'])['audio']['line-one']['text'] == 'Different words.'
    assert scenes.get(scene['id'])['voice_takes']['line-one']['approved_id'] is None


@pytest.mark.skipif(not shutil.which('node'), reason='Node required')
def test_history_recovery_and_server_metadata_boundaries():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(['node', '--test', str(root / 'scripts/check_scene_history.mjs')], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


def test_sound_upload_loop_offset_volume_and_actual_speech_ducking(studio):
    import base64
    import numpy as np
    from npcgen.scene_sound import upload, mix_tracks, duck_gain
    lib, scenes, raw = studio; scene = scenes.create(raw)
    stream = io.BytesIO()
    with wave.open(stream, 'wb') as wav:
        wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(24000); wav.writeframes(np.full(24000, 1000, dtype='<i2').tobytes())
    payload = {'revision': scene['revision'], 'name': 'Ambience', 'audio': 'data:audio/wav;base64,' + base64.b64encode(stream.getvalue()).decode()}
    uploaded = upload(scenes, scene['id'], payload); media_id = next(iter(uploaded['media']))
    assert uploaded['revision'] == 2
    with pytest.raises(ValueError, match='changed since'): upload(scenes, scene['id'], payload)
    scene = scenes.update(scene['id'], {**uploaded, 'soundtracks': [{'id': 'track-one', 'media_id': media_id,
        'start': 0, 'offset': .2, 'duration': 3, 'volume': .5, 'loop': True, 'duck': True}]})
    timeline = {'fps': 25, 'beats': [{'start': 25, 'frames': 50, 'voice': {'seconds': .4}}]}
    mixed = np.zeros(48000 * 4, dtype=np.float32); mix_tracks(scene, timeline, scenes, mixed, 48000)
    for time, expected in ((.5, 500), (1.1, 150), (1.6, 500), (2.8, 500), (3.2, 0)):
        assert abs(mixed[round(time * 48000)] - expected) < .01
    assert abs(duck_gain(.96, timeline['beats'], 25) - .65) < .000001
    invalid = copy.deepcopy(scene); invalid['media'] = {'evil': {'file': '../secret'}}
    assert clean_scene(invalid, lib, scene)['media'] == scene['media']
    assert compile_timeline(scene, lib, scenes)['soundtracks'][0]['media']['id'] == media_id
    scenes.resolve(scene['id'], scene['media'][media_id]['file']).write_bytes(b'changed')
    assert compile_timeline(scene, lib, scenes)['readiness']['export_blocked']


def test_props_are_pinned_and_existing_items_are_not_modified(studio):
    import struct
    from npcgen import media
    from npcgen.scene_props import describe
    lib, scenes, raw = studio; item_id = 'qa-crate-abcdef'
    folder = lib.root / '.npcforge-items' / item_id; folder.mkdir(parents=True)
    # Minimal self-contained GLB JSON chunk; native visual QA uses a proper rendered mesh.
    chunk = json.dumps({'asset': {'version': '2.0'}, 'scene': 0, 'scenes': [{'nodes': []}]}).encode(); chunk += b' ' * (-len(chunk) % 4)
    model = struct.pack('<III', 0x46546c67, 2, 20 + len(chunk)) + struct.pack('<II', len(chunk), 0x4e4f534a) + chunk
    (folder / 'model.glb').write_bytes(model)
    record = {'id': item_id, 'name': 'Crate', 'assets': {'model_3d': {'file': 'model.glb'}}}
    (folder / 'item.json').write_text(json.dumps(record))
    raw['props'] = [{'id': 'prop-one', 'item_id': item_id}]
    scene = scenes.create(raw); rows, warnings = describe(scene, scenes)
    assert not warnings and rows[0]['url'].startswith('/api/scenes/')
    (folder / 'model.glb').write_bytes(b'new item version')
    assert describe(scene, scenes)[0][0]['source']['sha256'] == rows[0]['source']['sha256']
    changed = scenes.update(scene['id'], {**scene, 'props': [{**scene['props'][0], 'rotation': 45}]})
    assert scenes.resolve(scene['id'], changed['prop_sources']['prop-one']['file']).read_bytes() == media.label(model)
    duplicate = scenes.duplicate(scene['id'])
    assert scenes.resolve(duplicate['id'], duplicate['prop_sources']['prop-one']['file']).read_bytes() == media.label(model)
    assert (folder / 'model.glb').read_bytes() == b'new item version'


def test_writing_proposal_uses_profiles_and_never_changes_the_saved_scene(studio):
    from npcgen.scene_writer import suggest
    lib, scenes, raw = studio; scene = scenes.create(raw)
    lib.update(raw['cast'][0], lambda rec: rec['profile'].update(demeanor='warm and friendly', goal='opening a bakery'))
    proposal = suggest({'scene': scene, 'objective': 'cross the gate safely', 'location': 'the old checkpoint', 'template': 'negotiation', 'tone': 'hopeful'}, lib, scene)
    assert proposal['generator'] == 'local-profile-templates-v1'
    script = ' '.join(beat['text'] for beat in proposal['beats'])
    assert all(text in script for text in ('opening a bakery', 'cross the gate safely', 'the old checkpoint'))
    assert any(beat['acting'] for beat in proposal['beats'])
    assert scenes.get(scene['id']) == scene
    assert len({beat['id'] for beat in proposal['beats']}) == len(proposal['beats'])


def test_take_approval_is_invalidated_by_voice_changes_and_corrupt_audio(studio):
    from npcgen.scene_takes import describe
    lib, scenes, raw = studio; scene = scenes.create(raw); speaker = raw['cast'][0]
    data, meta = clip(lib, speaker, 'audio/first.wav'); scenes.write_audio(scene['id'], meta['file'], data)
    scenes.set_audio(scene['id'], 'line-one', meta); saved = scenes.get(scene['id']); take_id = saved['voice_takes']['line-one']['takes'][0]['id']
    saved = select(scenes, scene['id'], 'line-one', {'revision': saved['revision'], 'take_id': take_id, 'approve': True})
    assert describe(saved, scenes)['line-one'][0]['approved']
    scenes.resolve(scene['id'], meta['file']).write_bytes(data[:-2] + b'\x01\x00')
    assert not describe(saved, scenes)['line-one'][0]['current']
    assert 'checksum' in describe(saved, scenes)['line-one'][0]['reason']
    with pytest.raises(ValueError, match='checksum'):
        select(scenes, scene['id'], 'line-one', {'revision': saved['revision'], 'take_id': take_id})
    scenes.resolve(scene['id'], meta['file']).write_bytes(data)
    lib.update(speaker, lambda npc: npc.setdefault('voice', {}).update(settings={'speed': 1.2}))
    assert not describe(saved, scenes)['line-one'][0]['approved']


def test_three_and_six_character_card_scenes_render_without_two_actor_assumptions(studio):
    from npcgen.scene_render import FrameMaker
    lib, scenes, raw = studio
    for count in (3, 6):
        cast = list(raw['cast'])
        for index in range(3, count):
            cast.append(lib.create({'profile': {'first_name': f'Extra {index}', 'last_name': 'Test', 'age': 30}, 'setting': 'modern', 'assets': {}})['id'])
        for aspect in ('landscape', 'portrait'):
            scene = scenes.create({**raw, 'cast': cast, 'mode': 'cards', 'aspect': aspect})
            timeline = compile_timeline(scene, lib, scenes)
            image = FrameMaker(scene, timeline, lib).frame(0)
            assert image.size == tuple(timeline['size'])


@pytest.mark.skipif(not shutil.which('node'), reason='Node required')
def test_camera_acting_ensemble_and_ducking_determinism():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(['node', '--test', str(root / 'scripts/check_scene_studio.mjs')], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(not shutil.which('node'), reason='Node required')
def test_real_animation_pose_stability_and_restrained_gestures():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(['node', '--test', str(root / 'scripts/check_scene_motion.mjs')], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
