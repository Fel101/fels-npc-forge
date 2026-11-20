import copy
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from npcgen.library import Library
from npcgen.scene_readiness import assess
from npcgen.scenes import Scenes, clean_scene, compile_timeline


def test_camera_contract_defaults_roundtrip_and_visual_edits_preserve_speech(tmp_path):
    lib = Library(tmp_path / 'library')
    cast = [lib.create({'profile': {'first_name': name, 'last_name': 'Test', 'age': 30}, 'assets': {}, 'setting': 'modern'})['id'] for name in ('A', 'B')]
    raw = {'title': 'Camera test', 'cast': cast, 'mode': 'stage3d', 'background_prompt': 'Empty stage',
           'beats': [{'id': 'pause-one', 'kind': 'pause', 'duration': 1},
                     {'id': 'reaction-two', 'kind': 'reaction', 'speaker_id': cast[1], 'camera_shot': 'shoulder', 'camera_side': 'right', 'duration': 1}]}
    scene = Scenes(lib).create(raw)
    assert scene['beats'][0]['camera_shot'] == 'wide'
    assert scene['beats'][0]['camera_target'] == cast[0]
    assert scene['beats'][1]['camera_target'] == cast[1]
    timeline = compile_timeline(scene, lib)
    assert timeline['beats'][1]['camera_shot'] == 'shoulder'
    assert timeline['readiness']['export_blocked']
    for bad in ({'camera_shot': []}, {'camera_side': 'front'}, {'camera_target': 'missing'}):
        changed = copy.deepcopy(raw); changed['beats'][0].update(bad)
        with pytest.raises(ValueError, match='camera'): clean_scene(changed, lib)
    changed = copy.deepcopy(scene); changed['beats'][0]['camera_shot'] = 'close'
    changed = Scenes(lib).update(scene['id'], changed)
    assert compile_timeline(changed, lib)['fingerprint'] != timeline['fingerprint']


def test_readiness_distinguishes_export_blockers_word_failure_and_valid_fallback():
    scene = {'cast': ['a', 'b'], 'mode': 'stage3d', 'revision': 2, 'render': {'revision': 1, 'fingerprint': 'old'}}
    npcs = {'a': {'profile': {'first_name': 'Ada'}, 'assets': {'rig_3d': {'file': 'rig.glb', 'stale': True}}},
            'b': {'profile': {'first_name': 'Bo'}, 'assets': {}}}
    timeline = {'fingerprint': 'new', 'beats': [
        {'id': 'one', 'kind': 'line', 'voice': None},
        {'id': 'two', 'kind': 'line', 'voice': {'lip_sync': {'status': 'amplitude_fallback', 'reason': 'Recognized words disagree with the synthesis transcript'}}},
        {'id': 'three', 'kind': 'line', 'voice': {'lip_sync': {'status': 'aligned', 'validation': {'transcript_match': True}}}}]}
    result = assess(scene, timeline, npcs, lambda id, meta: bool(meta.get('file')), False)
    assert result['export_blocked'] and result['speech_ready'] == 2 and result['speech_total'] == 3
    by_code = {row['code']: row for row in result['checks']}
    assert by_code['missing_visual']['npc_id'] == 'b' and by_code['missing_visual']['level'] == 'blocker'
    assert by_code['outdated_visual']['npc_id'] == 'a'
    assert by_code['word_mismatch']['beat_id'] == 'two'
    assert by_code['missing_speech']['action'] == 'voice' and by_code['background']['action'] == 'background'
    assert all(row.get('beat_id') != 'three' for row in result['checks'])
    npcs['b']['assets']['rig_3d'] = {'file': 'b.glb'}
    assert not assess(scene, timeline, npcs, lambda id, meta: bool(meta.get('file')))['export_blocked']


@pytest.mark.skipif(not shutil.which('node'), reason='Node required')
def test_camera_geometry_and_audio_clock():
    root = Path(__file__).resolve().parents[1]
    script = '''
    import assert from 'node:assert/strict';
    const {cameraPose}=await import(CAMERA);
    const {SceneClock}=await import(CLOCK);
    const positions={a:[-1.35,0],b:[1.35,0]};
    assert.deepEqual(cameraPose(positions,'a','wide').position,[0,1.65,7.2]);
    const close=cameraPose(positions,'b','close'), medium=cameraPose(positions,'b','medium');
    assert.ok(close.position[2]<medium.position[2]);
    assert.equal(close.target[0],1.35);
    const left=cameraPose(positions,'a','shoulder','left'),right=cameraPose(positions,'a','shoulder','right');
    assert.notDeepEqual(left.position,right.position);
    for(const shot of ['wide','medium','close','shoulder']) {
      const pose=cameraPose({a:[0,0],b:[0,0]},'a',shot,'left',9/16);
      assert.ok([...pose.position,...pose.target,pose.fov].every(Number.isFinite));
    }
    const clock=new SceneClock(25),beat={start:50,voice:{seconds:2}};
    clock.seek(60);clock.start(1000);
    const audio={readyState:0,paused:true,ended:false,currentTime:0,duration:2};
    assert.equal(clock.sample(8000,beat,audio),60); // buffering cannot skip speech
    audio.readyState=4;audio.paused=false;audio.currentTime=.8;
    assert.equal(clock.sample(8100,beat,audio),70);
    clock.pause(70);assert.equal(clock.sample(20000,beat,audio),70);
    clock.start(20000);audio.currentTime=1;assert.equal(clock.sample(20200,beat,audio),75);
    audio.ended=true;assert.equal(clock.sample(21000,beat,audio),100);
    assert.equal(clock.sample(21200,beat,audio),105); // tail advances after audio ends
    clock.attach(110,22000);assert.equal(clock.sample(22200,{kind:'pause'},null),115);
    '''.replace('CAMERA', json.dumps((root / 'web/scene-camera.js').as_uri())).replace('CLOCK', json.dumps((root / 'web/scene-clock.js').as_uri()))
    result = subprocess.run(['node', '--input-type=module', '-e', script], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(not shutil.which('node'), reason='Node required')
def test_real_scene_controller_without_a_browser_or_model():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(['node', '--test', str(root / 'scripts/check_scene_director.mjs')], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
