"""Exercise the audition API with real local speech in a copied, isolated library."""
import argparse
import copy
import hashlib
import io
import json
from pathlib import Path
import sys
import threading
import time
import urllib.request
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from npcgen import config
from npcgen.app import App
from npcgen.library import Library
from npcgen.voice import record
from server import create_server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--engine', default='dramabox')
    parser.add_argument('--serve', action='store_true', help='Keep isolated Forge available for browser review')
    args = parser.parse_args()
    prior = Library(ROOT / 'local/voice-library-real-20261012-132540-da2158/library')
    source = prior.get('voiceqa1-actor-13087a')
    ref = source['voice']['reference']
    data = prior.read_asset(source['id'], ref['file'])
    originals = [prior.resolve(source['id'], 'npc.json'), prior.resolve(source['id'], ref['file'])]
    hashes = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in originals}
    root = ROOT / 'local' / ('voice-audition-real-' + time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:6])
    root.mkdir()
    cfg = config.load(); cfg['library_path'] = root / 'library'
    print('ROOT ' + str(root), flush=True)
    with App(cfg) as app:
        npc = copy.deepcopy(source)
        for key in ('id', 'voice', 'assets', 'voice_lines', 'dialogue', 'created', 'updated'): npc.pop(key, None)
        npc['profile'].update(first_name='Clara', last_name='AuditionQA')
        npc = app.lib.create(npc)
        def install(rec):
            v = record.clean_settings({'engine': args.engine, 'speaker': args.engine, 'mode': 'upload', 'language': 'English'}, rec)
            record.save_settings(rec, v, app.lib)
            v = record.voice_of(rec)
            record.install_reference(app.lib, rec, v, data, {**ref, 'source': 'retained-approved-QA-reference'})
            v['studio'] = {'pronunciations': [{'term': 'Vael', 'spoken': 'Vay ell'}], 'presets': [], 'lip_sync': {'enabled': False}}
            rec['voice'] = v
        app.lib.update(npc['id'], install)
        httpd = create_server(('127.0.0.1', 0), app)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        base = 'http://127.0.0.1:' + str(httpd.server_port)
        def call(method, path, body=None, raw=False):
            payload = json.dumps(body).encode() if body is not None else None
            request = urllib.request.Request(base + path, data=payload, method=method, headers={'Content-Type': 'application/json'})
            with urllib.request.urlopen(request, timeout=180) as response:
                content = response.read()
            return content if raw else json.loads(content)
        try:
            endpoint = f'/api/npcs/{npc["id"]}/voice/auditions'
            result = call('POST', endpoint, {'names': 'Vael Harbor'})
            suite = result['npc']['voice']['auditions'][0]
            job_id = result['job']['id']
            print('JOB ' + job_id + ' ' + str(result['job']['total']) + ' cases', flush=True)
            deadline = time.monotonic() + 1200
            previous = None
            while time.monotonic() < deadline:
                job = next(row for row in app.jobs.snapshot() if row['id'] == job_id)
                progress = [(step['key'], step['status']) for step in job['steps']]
                if progress != previous:
                    print('PROGRESS ' + json.dumps(progress), flush=True); previous = progress
                if job['status'] not in ('queued', 'running'):
                    assert job['status'] == 'done', job
                    break
                time.sleep(1)
            else: raise AssertionError('Real audition timed out')
            current = call('GET', f'/api/npcs/{npc["id"]}')
            suite = current['voice']['auditions'][0]
            assert current['voice']['studio']['lip_sync']['enabled'] is False
            assert all(row['status'] in ('ready', 'skipped') for row in suite['cases'])
            assert all(not row.get('stale') for row in suite['cases'])
            zip_data = call('GET', endpoint + '/' + suite['id'] + '/pack.zip', raw=True)
            (root / 'audition.zip').write_bytes(zip_data)
            with zipfile.ZipFile(io.BytesIO(zip_data)) as archive:
                manifest = json.loads(archive.read('audition.json'))
                for row in suite['cases']:
                    if row['status'] != 'ready': continue
                    clip = current['voice']['tests'][row['key']]
                    assert hashlib.sha256(archive.read(row['id'] + '.wav')).hexdigest() == row['quality']['sha256']
                    assert clip['model_identity']['queue_binding']['source'] == 'queue'
                    assert clip['performance'] == manifest['clips'][row['id']]['performance']
                    assert set(row['review'].values()) == {'unreviewed'}
                names = manifest['clips']['names']
                assert 'Vay ell' in names['synthesis_text'] and 'Vael' in names['text']
                assert archive.read('reference.wav') == data
            assert hashes == {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in originals}
            (root / 'report.json').write_text(json.dumps({'status': 'passed', 'root': str(root), 'source': hashes,
                'npc_id': npc['id'], 'job': job, 'suite': suite, 'url': base,
                'human_review': 'Identity, pronunciation and delivery remain unreviewed; real speech and measured flags retained.'}, indent=2))
            print('PASS ' + str(root / 'report.json'), flush=True)
            if args.serve:
                print('URL ' + base + '/', flush=True)
                threading.Event().wait()
        finally:
            httpd.shutdown(); httpd.server_close()


if __name__ == '__main__': main()
