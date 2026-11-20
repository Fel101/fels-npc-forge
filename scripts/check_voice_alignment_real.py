"""Validate real saved speech, final-audio timing, scene pins and exports in isolation."""
import argparse
import copy
import hashlib
import io
import json
from pathlib import Path
import shutil
import sys
import time
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from npcgen.library import Library
from npcgen.scenes import Scenes, compile_timeline
from npcgen import scene_render
from npcgen.scene_export import make_zip
from npcgen.voice import alignment, audio, lip_preview, pack, record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-report', type=Path, default=ROOT / 'local/customvoice-real-20261012-160341-f95fb0/report.json')
    args = parser.parse_args()
    source = json.loads(args.source_report.read_text('utf-8'))
    source_root = args.source_report.resolve().parent
    root = ROOT / 'local' / ('voice-alignment-real-' + time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:6])
    root.mkdir(parents=True)
    lib, source_lib = Library(root / 'library'), Library(source_root / 'library')
    scenes, source_scenes = Scenes(lib), Scenes(source_lib)
    report = {'status': 'running', 'source_report': str(args.source_report.resolve()), 'inference': 'CPU alignment only; prior real TTS reused',
              'clips': [], 'human_timing_review': 'pending', 'character_rig_integration': 'pending; current MP4 retains amplitude mouth rendering'}
    before = {}
    try:
        ids = []
        for i, clip in enumerate(source['clips']):
            npc_id = Path(clip['path']).parents[2].name
            ids.append(npc_id)
            before[npc_id] = hashlib.sha256(source_lib.resolve(npc_id, 'npc.json').read_bytes()).hexdigest()
            shutil.copytree(source_root / 'library' / npc_id, lib.root / npc_id)
            npc = lib.get(npc_id)
            snapshot = copy.deepcopy(npc['voice_lines']['greeting_1']['voice_snapshot'])
            snapshot.setdefault('studio', {})['lip_sync'] = alignment.clean({'enabled': True})
            lib.update(npc_id, lambda rec: rec.update(voice=snapshot))
            npc = lib.get(npc_id)
            result = alignment.analyze_source(lib, npc, 'line:greeting_1', snapshot['studio']['lip_sync'])
            lip = result['lip_sync']
            assert lip['status'] == 'aligned', lip['reason']
            assert lip['audio_sha256'] == clip['sha256']
            alignment.validate(lip, lip['seconds']); alignment.validate_visemes(lip['visemes'], lip['seconds'])
            lib.update(npc_id, lambda rec: rec['voice_lines']['greeting_1'].update(lip_sync=lip))
            npc = lib.get(npc_id); payload, _ = pack.build(lib, npc)
            (root / f'actor-{i + 1}-voice.zip').write_bytes(payload)
            with zipfile.ZipFile(io.BytesIO(payload)) as z:
                key = next(k for k in z.namelist() if k.endswith('/lines.json'))
                stem = key.rsplit('/', 1)[0]; manifest = json.loads(z.read(key))
                line = manifest['lines'][0]
                stored = json.loads(z.read(stem + '/' + line['lip_sync_file']))
                assert stored['status'] == 'aligned' and stored['audio_sha256'] == hashlib.sha256(z.read(stem + '/' + line['file'])).hexdigest()
            report['clips'].append({'npc_id': npc_id, 'path': str(lib.resolve(npc_id, npc['voice_lines']['greeting_1']['file'])), 'lip_sync': lip})
            report['clips'][-1]['timing_video'] = lip_preview.render(report['clips'][-1]['path'], lip, root / f'actor-{i + 1}-timing-reference.mp4')
        previous = source_scenes.get(source['scene']['id'])
        scene = scenes.create({'title': 'Aligned real speech data', 'cast': ids,
            'voice_bindings': {npc_id: record.identity_id(lib.get(npc_id)['voice']) for npc_id in ids}, 'beats': previous['beats']})
        for beat_id, old in previous['audio'].items():
            meta = copy.deepcopy(old)
            data = source_scenes.resolve(previous['id'], meta['file']).read_bytes()
            meta['lip_sync'] = alignment.process(data, meta['synthesis_text'], meta['voice_snapshot']['language'], {'enabled': True})
            assert meta['lip_sync']['status'] == 'aligned', meta['lip_sync']['reason']
            scenes.write_audio(scene['id'], meta['file'], data)
            assert scenes.set_audio(scene['id'], beat_id, meta)
        scene = scenes.get(scene['id']); timeline = compile_timeline(scene, lib, scenes)
        assert all(b['voice'] for b in timeline['beats'])
        # Changing a character mapping cannot alter the scene's captured settings.
        changed = alignment.clean({'enabled': True, 'fps': 30})
        lib.update(ids[0], lambda rec: rec['voice']['studio'].update(lip_sync=changed))
        assert all(b['voice'] for b in compile_timeline(scene, lib, scenes)['beats'])
        filename = scene_render.render(scene, scenes, timeline=timeline)
        scenes.set_render(scene['id'], scene['revision'], filename, timeline['fingerprint'])
        packed = make_zip(scenes.get(scene['id']), scenes, 'Model-estimated timing data; current character video remains amplitude-based.')
        (root / 'scene-package.zip').write_bytes(packed)
        with zipfile.ZipFile(io.BytesIO(packed)) as z:
            manifest = json.loads(z.read('sources.json'))
            for voice in manifest['voices']:
                lip = json.loads(z.read(voice['lip_sync_file']))
                assert lip['status'] == 'aligned' and lip['visemes']['fps'] == 60
                assert lip['audio_sha256'] == hashlib.sha256(z.read(voice['file'])).hexdigest()
        report['scene'] = {'id': scene['id'], 'video': str(scenes.resolve(scene['id'], filename)),
            'audio': scenes.get(scene['id'])['audio'], 'timeline': timeline, 'pin_survived_mapping_change': True}
        wrong = alignment.process(Path(report['clips'][0]['path']).read_bytes(), 'A completely different sentence.', settings={'enabled': True})
        assert wrong['status'] == 'amplitude_fallback'
        report['wrong_transcript'] = wrong
        customized = alignment.process(Path(report['clips'][0]['path']).read_bytes(), source['clips'][0]['text'],
            settings={'enabled': True, 'phoneme_overrides': {'forge': ['F', 'AO', 'R', 'JH']}})
        assert customized['status'] == 'aligned' and customized['backend']['effective_dictionary_sha256'] != customized['backend']['dictionary_sha256']
        report['phoneme_override'] = customized
        effects_report = ROOT / 'local/voice-effects-real-20261012-164751-cd27a1/report.json'
        if effects_report.is_file():
            report['processed_audio_probes'] = []
            for clip in json.loads(effects_report.read_text('utf-8'))['clips']:
                actor = json.loads((Path(clip['path']).parents[2] / 'npc.json').read_text('utf-8'))
                text = actor['voice_lines']['greeting_1']['synthesis_text']
                checked = alignment.process(Path(clip['path']).read_bytes(), text, 'English',
                    {'enabled': True, 'phoneme_overrides': {'ee': ['IY'], 'ther': ['DH', 'EH', 'R']}})
                assert checked['audio_sha256'] == clip['postprocessing']['output_sha256']
                report['processed_audio_probes'].append({'preset': clip['preset'], 'lip_sync': checked})
        for npc_id, digest in before.items():
            assert hashlib.sha256(source_lib.resolve(npc_id, 'npc.json').read_bytes()).hexdigest() == digest
        report['status'] = 'passed'
    except Exception as e:
        report.update(status='failed', error=str(e)); raise
    finally:
        (root / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(f'Report: {root / "report.json"}', flush=True)


if __name__ == '__main__':
    main()
