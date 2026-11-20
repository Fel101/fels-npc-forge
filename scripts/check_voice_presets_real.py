"""Exercise a real reference-free CustomVoice library/dialogue/scene workflow in isolation."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from npcgen import config, dialogue, roll, scene_render
from npcgen.app import App
from npcgen.scenes import compile_timeline
from npcgen.scene_export import make_zip
from npcgen.voice import audio, authoring, engines, pack, record, service
from check_voice_library_real import wait_job


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--speaker', default='Ryan')
    args = parser.parse_args()
    cfg = config.load()
    if args.speaker not in {v['id'] for v in engines.stock_voices('qwen_custom', cfg)}:
        raise ValueError('Choose a speaker from the pinned cached configuration')
    root = ROOT / 'local' / ('customvoice-real-' + time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:6])
    root.mkdir(parents=True)
    cfg['library_path'] = root / 'library'
    report = {'status': 'running', 'speaker': args.speaker, 'reference_free': True, 'clips': [], 'human_listening': 'pending'}
    try:
        with App(cfg) as app:
            targets = []
            for i in range(2):
                raw = roll.roll_npc(roll.load_data(), 'fantasy')
                raw['profile'].update(first_name=f'PresetQA{i + 1}', last_name='Actor', age=30)
                npc = app.lib.create(raw)
                def select(rec):
                    record.save_settings(rec, record.clean_settings({'engine': 'qwen_custom', 'mode': 'stock', 'stock_voice': args.speaker}, rec), app.lib)
                    rec['voice']['studio'] = authoring.clean_studio({'presets': [{'id': 'guard', 'name': 'Guard',
                        'direction': 'Speak calmly and firmly.', 'parameters': {'temperature': .7, 'top_k': 35, 'top_p': .9, 'repetition_penalty': 1.05}}],
                        'default_performance': 'guard'})
                    rec['dialogue'] = dialogue.generate(rec)
                    rec['dialogue']['lines']['greeting'][0]['text'] = ['The forge gates are open. Welcome, traveler.',
                        'Keep your lantern close. The night watch begins.'][i]
                app.lib.update(npc['id'], select)
                targets.append(npc['id'])
            entry = app.voice_library.from_npc(targets[0], {'name': f'{args.speaker} native identity', 'tags': ['QA', 'reference-free']})
            version = entry['versions'][0]
            assert version['audio_url'] is None
            app.voice_library.approve(version['id'], {'approved': True, 'notes': 'QA preset identity reuse'})
            app.voice_library.assign(version['id'], targets)
            report['version_id'] = version['id']
            for i, npc_id in enumerate(targets):
                npc = app.lib.get(npc_id)
                assert not npc['voice'].get('reference')
                request = service.lines_request(npc, {'ids': ['greeting_1']})
                start = time.monotonic()
                job = app.jobs.submit(npc, {'voice': request}, {})
                wait_job(app, job['id'])
                npc = app.lib.get(npc_id); meta = npc['voice_lines']['greeting_1']
                path = app.lib.resolve(npc_id, meta['file']); samples, rate = audio.read_wav(path.read_bytes())
                assert not record.line_is_stale(npc, 'greeting_1') and meta['reference'] == version['id']
                assert meta['model_identity']['reference_required'] is False
                assert not app.lib.resolve(npc_id, 'voice/reference.wav').exists()
                report['clips'].append({'path': str(path), 'text': meta['text'], 'duration_seconds': len(samples) / rate,
                    'sample_rate': rate, 'elapsed_seconds': round(time.monotonic() - start, 2), 'model_identity': meta['model_identity'],
                    'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
                zipped, _ = pack.build(app.lib, npc); (root / f'actor-{i + 1}-voice.zip').write_bytes(zipped)
                print(f'Generated reference-free {path}', flush=True)
            scene = app.scenes.create({'title': 'Reference-free guard scene', 'cast': targets,
                'voice_bindings': {key: version['id'] for key in targets}, 'beats': [
                    {'id': 'native-one', 'kind': 'line', 'speaker_id': targets[0], 'text': 'The harbor is safe tonight.', 'performance_id': 'guard'},
                    {'id': 'native-two', 'kind': 'line', 'speaker_id': targets[1], 'text': 'Keep the lantern lit.', 'performance_id': 'guard'}]})
            job = app.jobs.submit_scene_voice(scene['id'], {}); wait_job(app, job['id'])
            app.lib.update(targets[0], lambda rec: record.save_settings(rec, record.clean_settings({'stock_voice': 'Aiden' if args.speaker != 'Aiden' else 'Ryan'}, rec), app.lib))
            scene = app.scenes.get(scene['id']); timeline = compile_timeline(scene, app.lib, app.scenes)
            assert all(b['voice'] for b in timeline['beats'])
            filename = scene_render.render(scene, app.scenes, timeline=timeline)
            app.scenes.set_render(scene['id'], scene['revision'], filename, timeline['fingerprint'])
            (root / 'scene-package.zip').write_bytes(make_zip(app.scenes.get(scene['id']), app.scenes, 'Reference-free QA scene; human listening pending.'))
            report['scene'] = {'id': scene['id'], 'video': str(app.scenes.resolve(scene['id'], filename)), 'timeline': timeline,
                'audio': scene['audio'], 'pin_survived_preset_change': True, 'portraits': 'placeholder cards; not facial lip-sync proof'}
        report['status'] = 'passed'
    except Exception as error:
        report.update(status='failed', error=str(error)); raise
    finally:
        (root / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(f'Report: {root / "report.json"}', flush=True)


if __name__ == '__main__':
    main()
