"""Apply effects to verified real speech in a fresh QA library; no model inference or production writes."""
import argparse
import copy
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
from types import SimpleNamespace
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from npcgen import scene_render, scene_voice
from npcgen.library import Library
from npcgen.scenes import Scenes, compile_timeline
from npcgen.scene_export import make_zip
from npcgen.voice import audio, authoring, effects, pack, record, service, steps


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-report', type=Path, default=ROOT / 'local/voice-studio-real-20261012-160625-5bca7f/report.json')
    args = parser.parse_args()
    source_root = args.source_report.resolve().parent
    source = json.loads(args.source_report.read_text('utf-8'))
    if source.get('status') != 'passed' or len(source.get('clips', [])) != 2:
        raise ValueError('Choose a passed two-character real speech report')
    root = ROOT / 'local' / ('voice-effects-real-' + time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:6])
    root.mkdir(parents=True)
    lib, original_lib = Library(root / 'library'), Library(source_root / 'library')
    scenes, original_scenes = Scenes(lib), Scenes(original_lib)
    def no_generation(*args, **kwargs):
        raise AssertionError('An effect-only edit must not resynthesize this verified speech')
    jobs = SimpleNamespace(lib=lib, scenes=scenes, voice=SimpleNamespace(run=no_generation))
    report = {'status': 'running', 'source_report': str(args.source_report.resolve()), 'inference': 'none; verified prior real speech reused',
              'clips': [], 'human_listening': 'pending', 'lip_sync': 'amplitude fallback; no word/phoneme alignment'}
    before = {}
    try:
        ids = [clip['npc_id'] for clip in source['clips']]
        for i, npc_id in enumerate(ids):
            source_actor = source_root / 'library' / npc_id
            before[npc_id] = hashlib.sha256((source_actor / 'npc.json').read_bytes()).hexdigest()
            shutil.copytree(source_actor, lib.root / npc_id)
            npc = lib.get(npc_id); meta = copy.deepcopy(npc['voice_lines']['greeting_1'])
            text = meta['text']; snapshot = copy.deepcopy(meta['voice_snapshot'])
            dry = original_lib.read_asset(npc_id, meta['file'])
            assert hashlib.sha256(dry).hexdigest() == source['clips'][i]['sha256']
            meta['original'] = effects.preserve(lib, npc_id, dry)
            baseline = authoring.prepare(snapshot, text, meta.get('mood', 'neutral'), 'greeting_1')[2]
            assert baseline['synthesis_signature'] == meta['render_signature']
            meta['synthesis_signature'] = baseline['synthesis_signature']
            preset = ['robot', 'creature'][i]
            snapshot.setdefault('studio', {})['effects'] = effects.PRESETS[preset]
            def assign(rec):
                rec['voice'] = snapshot
                rec['voice_lines']['greeting_1'] = meta
            lib.update(npc_id, assign)
            npc = lib.get(npc_id)
            opts = {'voice': service.lines_request(npc, {'ids': ['greeting_1']})}
            steps.line(jobs, SimpleNamespace(options=opts, cancel=None), {'key': 'greeting_1'}, npc)
            npc = lib.get(npc_id); delivered = npc['voice_lines']['greeting_1']
            assert delivered['original_reused'] and not record.line_is_stale(npc, 'greeting_1')
            wet = lib.read_asset(npc_id, delivered['file'])
            x, rate = audio.read_wav(wet); dry_x, dry_rate = audio.read_wav(dry)
            assert len(x) == len(dry_x) and rate == dry_rate
            # Restore dry byte-for-byte, then repeat the same effect from that original.
            lib.update(npc_id, lambda rec: rec['voice']['studio'].update(effects=effects.PRESETS['dry']))
            current = lib.get(npc_id)
            steps.line(jobs, SimpleNamespace(options={'voice': service.lines_request(current, {'ids': ['greeting_1']})}, cancel=None), {'key': 'greeting_1'}, current)
            assert lib.read_asset(npc_id, current['voice_lines']['greeting_1']['file']) == dry
            lib.update(npc_id, lambda rec: rec['voice']['studio'].update(effects=effects.PRESETS[preset]))
            current = lib.get(npc_id)
            steps.line(jobs, SimpleNamespace(options={'voice': service.lines_request(current, {'ids': ['greeting_1']})}, cancel=None), {'key': 'greeting_1'}, current)
            assert lib.read_asset(npc_id, current['voice_lines']['greeting_1']['file']) == wet
            payload, _ = pack.build(lib, lib.get(npc_id)); (root / f'actor-{i + 1}-voice.zip').write_bytes(payload)
            with zipfile.ZipFile(io.BytesIO(payload)) as z:
                manifest = json.loads(z.read(next(k for k in z.namelist() if k.endswith('/lines.json'))))
                assert manifest['lines'][0]['postprocessing']['output_samples'] == len(dry_x)
            report['clips'].append({'npc_id': npc_id, 'preset': preset, 'path': str(lib.resolve(npc_id, delivered['file'])),
                'original': delivered['original'], 'postprocessing': delivered['postprocessing'], 'sample_rate': rate,
                'samples': len(x), 'seconds': len(x) / rate, 'restored_exactly': True, 'repeat_matches': True})
        previous = original_scenes.get(source['scene']['id'])
        scene = scenes.create({'title': 'Robot and creature real speech effects', 'cast': ids,
            'voice_bindings': {npc_id: record.identity_id(lib.get(npc_id)['voice']) for npc_id in ids}, 'beats': previous['beats']})
        requests = scene_voice.requests(scene, scenes, {})
        for beat_id, request in requests.items():
            old = copy.deepcopy(previous['audio'][beat_id]); npc_id = request['speaker_id']
            dry = original_scenes.resolve(previous['id'], old['file']).read_bytes()
            old['original'] = effects.preserve(lib, npc_id, dry)
            old['synthesis_signature'] = authoring.prepare(old['voice_snapshot'], old['text'], old['mood'], override=old['performance'])[2]['synthesis_signature']
            assert old['synthesis_signature'] == request['synthesis_signature']
            scenes.write_audio(scene['id'], old['file'], dry)
            assert scenes.set_audio(scene['id'], beat_id, old)
            scene_voice.run(jobs, SimpleNamespace(scene_id=scene['id'], options={'scene_voice': {'beats': requests}}, cancel=None), {'key': beat_id})
        scene = scenes.get(scene['id']); timeline = compile_timeline(scene, lib, scenes)
        assert all(b['voice'] for b in timeline['beats'])
        previous_effects = copy.deepcopy(lib.get(ids[0])['voice']['studio']['effects'])
        lib.update(ids[0], lambda rec: rec['voice']['studio'].update(effects=effects.PRESETS['dry']))
        assert compile_timeline(scene, lib, scenes)['fingerprint'] == timeline['fingerprint']
        assert all(b['voice'] for b in compile_timeline(scene, lib, scenes)['beats'])
        lib.update(ids[0], lambda rec: rec['voice']['studio'].update(effects=previous_effects))
        filename = scene_render.render(scene, scenes, timeline=timeline)
        scenes.set_render(scene['id'], scene['revision'], filename, timeline['fingerprint'])
        (root / 'scene-package.zip').write_bytes(make_zip(scenes.get(scene['id']), scenes, 'Real prior speech with CPU effects; human listening pending.'))
        report['scene'] = {'id': scene['id'], 'video': str(scenes.resolve(scene['id'], filename)), 'timeline': timeline,
                           'audio': scenes.get(scene['id'])['audio'], 'pins_survived_effect_change': True,
                           'portraits': 'placeholder cards; not aligned facial animation'}
        probe = subprocess.run(['ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', report['scene']['video']],
                               capture_output=True, text=True, check=True, timeout=30)
        info = json.loads(probe.stdout)
        video = next(s for s in info['streams'] if s['codec_type'] == 'video')
        sound = next(s for s in info['streams'] if s['codec_type'] == 'audio')
        assert int(video['nb_frames']) == timeline['frames'] and video['avg_frame_rate'] == '25/1'
        assert abs(float(sound['duration']) - timeline['frames'] / 25) < .05
        report['scene']['media_probe'] = {'frames': int(video['nb_frames']), 'fps': video['avg_frame_rate'],
            'audio_duration': float(sound['duration']), 'width': video['width'], 'height': video['height']}
        for npc_id, digest in before.items():
            assert hashlib.sha256(original_lib.resolve(npc_id, 'npc.json').read_bytes()).hexdigest() == digest
        report['status'] = 'passed'
    except Exception as e:
        report.update(status='failed', error=str(e)); raise
    finally:
        (root / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(f'Report: {root / "report.json"}', flush=True)


if __name__ == '__main__':
    main()
