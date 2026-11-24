import copy
import hashlib
import io
import json
from types import SimpleNamespace
import zipfile

import numpy as np
import pytest

from npcgen.jobs import Jobs
from npcgen.library import Library
from npcgen.voice import audition, audio, authoring, lab_voice, record, steps


@pytest.fixture
def actor(tmp_path):
    lib = Library(tmp_path / 'library')
    npc = lib.create({'profile': {'first_name': 'Clara', 'last_name': 'Test', 'age': 30}, 'setting': 'modern', 'assets': {}})
    data = audio.write_wav(.2 * np.sin(2 * np.pi * 220 * np.arange(48000) / 16000), 16000)
    def save(rec):
        v = {'engine': 'dramabox', 'mode': 'upload', 'language': 'English', 'settings': {},
             'studio': {'pronunciations': [{'term': 'Vael', 'spoken': 'Vay ell'}], 'presets': [], 'lip_sync': {'enabled': False}}}
        record.install_reference(lib, rec, v, data, {'id': 'a' * 12, 'text': 'Reference voice.', 'language': 'English'})
        rec['voice'] = v
    lib.update(npc['id'], save)
    return lib, npc['id'], data


def test_suite_freezes_identity_and_pronunciation_without_changing_saved_settings(actor):
    lib, npc_id, _ = actor
    original = lib.get(npc_id)
    suite_id, req = audition.request(lib, original, {'names': 'Vael Harbor'})
    assert len(req['tests']) == 7
    assert len(Jobs.plan(original, {'voice': req}, {})) == 7
    assert req['voice_snapshot']['studio']['lip_sync']['enabled'] is True
    assert lib.get(npc_id)['voice']['studio'] == original['voice']['studio']
    assert req['voice_snapshot']['reference']['sha256'] == original['voice']['reference']['sha256']
    assert 'auditions' not in record.render_snapshot(lib.get(npc_id)['voice'])
    # Same exact script makes emotional identity comparisons meaningful.
    suite = audition.session(lib.get(npc_id)['voice'], suite_id)
    assert len({row['text'] for row in suite['cases'] if row['id'] in ('neutral', 'angry', 'whisper', 'amused')}) == 1
    lib.update(npc_id, lambda rec: rec['voice']['studio']['pronunciations'][0].update(spoken='Vale'))
    row = next(row for row in req['tests'] if row['key'].endswith('_names'))
    _, spoken, _ = authoring.prepare(req['voice_snapshot'], row['text'], override=row['performance'])
    assert 'Vay ell' in spoken and 'Vale' not in spoken


def test_unsupported_moods_skipped_and_bad_requests_leave_no_session(actor):
    lib, npc_id, _ = actor
    lib.update(npc_id, lambda rec: rec['voice'].update(engine='qwen'))
    _, req = audition.request(lib, lib.get(npc_id), {})
    assert len(req['tests']) == 4
    assert all(row['mood'] == 'neutral' for row in req['tests'])
    before = lib.get(npc_id)
    for body in ({'names': []}, {'names': '[laugh]'}, {'script': 'replaced'}, {'names': 'x' * 161}):
        with pytest.raises(ValueError): audition.request(lib, before, body)
    assert lib.get(npc_id) == before
    with pytest.raises(ValueError, match='English'): audition.cases({'voice': {'language': 'French'}})
    with pytest.raises(ValueError, match='Assign'): audition.request(lib, {'id': npc_id}, {})


def test_batch_step_selects_each_input_retains_flags_reviews_and_checked_export(actor, monkeypatch):
    lib, npc_id, data = actor
    lib.update(npc_id, lambda rec: lab_voice.save(rec, {
        'name': 'Clara Test', 'kind': 'cast', 'identity_id': 'b' * 32,
        'model': 'qwen3-tts-1.7b-base', 'takes_direction': True}))
    suite_id, req = audition.request(lib, lib.get(npc_id), {'names': 'Vael'})
    seen = []
    def run(job, request):
        seen.append(copy.deepcopy(request))
        return data
    monkeypatch.setattr(lab_voice, 'speak', run)
    monkeypatch.setattr(steps.alignment, 'process', lambda *a, **k: {'status': 'amplitude_fallback',
        'reason': 'Recognized words differ', 'validation': {'transcript_match': False}})
    jobs = SimpleNamespace(lib=lib)
    job = SimpleNamespace(options={'voice': req}, cancel=None)
    selected = [req['tests'][0], req['tests'][5]]
    for item in selected: steps.test(jobs, job, {'key': item['key']}, lib.get(npc_id))
    assert seen[0]['text'] != seen[1]['text'] and 'Vay ell' in seen[1]['text']
    saved = audition.annotate(lib.get(npc_id))
    suite = audition.session(saved['voice'], suite_id)
    row = suite['cases'][0]
    assert row['status'] == 'ready' and not row['stale']
    assert {'word_match', 'alignment', 'mouth_fallback'} <= {flag['code'] for flag in row['quality']['flags']}
    reviewed = audition.review(lib, npc_id, suite_id, row['id'], {'sha256': row['quality']['sha256'], 'identity': 'issue', 'notes': 'Different voice.'})
    assert reviewed['voice']['auditions'][0]['cases'][0]['review']['identity'] == 'issue'
    with pytest.raises(ValueError, match='Only listening'): audition.review(lib, npc_id, suite_id, row['id'], {'text': 'edit'})
    with pytest.raises(ValueError, match='checksum'): audition.review(lib, npc_id, suite_id, row['id'], {'sha256': 'old'})
    packed = audition.pack(lib, saved, suite_id)
    with zipfile.ZipFile(io.BytesIO(packed)) as archive:
        assert {'reference.wav', 'greeting.wav', 'names.wav', 'audition.json'} == set(archive.namelist())
        manifest = json.loads(archive.read('audition.json'))
        assert hashlib.sha256(archive.read('greeting.wav')).hexdigest() == row['quality']['sha256']
        assert manifest['clips']['greeting']['model_identity']['source'] == 'voice-lab'
    lib.update(npc_id, lambda rec: rec['voice']['settings'].update(speed=1.1))
    assert audition.annotate(lib.get(npc_id))['voice']['auditions'][0]['cases'][0]['stale']
    meta = saved['voice']['tests'][row['key']]
    lib.write_asset(npc_id, meta['file'], b'changed')
    with pytest.raises(ValueError, match='checksum'): audition.pack(lib, saved, suite_id)


def test_quality_does_not_approve_silent_clips_or_infer_identity():
    result = audition.quality(audio.write_wav(np.zeros(16000), 16000), {'text': 'Hello.'})
    assert 'silent' in {f['code'] for f in result['flags']} and result['human_review_required']
    assert 'identity' not in result and 'approved' not in result
