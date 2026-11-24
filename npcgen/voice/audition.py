"""Retained character auditions: frozen speech, measured flags, human listening reviews."""
import copy
import hashlib
import io
import json
import re
import uuid
import zipfile

import numpy as np

from ..library import now
from . import alignment, audio, authoring, engines, record, service, markup

ID = re.compile(r'^[a-f0-9]{12}$')
REVIEW_FIELDS = ('identity', 'pronunciation', 'delivery')


def cases(npc, names=''):
    v = record.voice_of(npc)
    if v.get('language', 'English') != 'English':
        raise ValueError('The built-in audition scripts currently support English')
    if not isinstance(names, str) or len(names) > 160 or any(c in names for c in '[]\n\r'):
        raise ValueError('Names to test must be plain text up to 160 characters')
    name = ' '.join(str((npc.get('profile') or {}).get(k) or '') for k in ('first_name', 'last_name')).strip()[:100] or 'the keeper'
    names = names.strip() or name
    shared = 'Keep your lantern close. The gate is open, and I will meet you at the harbor.'
    rows = [('greeting', 'Greeting', f'Hello, I am {name}. It is good to meet you.', 'neutral'),
            ('neutral', 'Neutral', shared, 'neutral'), ('angry', 'Angry', shared, 'angry'),
            ('whisper', 'Whispered', shared, 'whisper'), ('amused', 'Amused', shared, 'happy'),
            ('names', 'Names & pronunciation', f'I am looking for {names}. Please tell me where to find them.', 'neutral'),
            ('long', 'Longer speech', 'Before we set out, check the map and count our supplies. We need water, a warm coat, and enough food for the journey. The old bridge may be closed, so we will follow the river until we reach the next village. If the weather turns, we can wait at the inn. Take your time, keep the group together, and tell me if anything seems wrong.', 'neutral')]
    linked = v.get('lab_voice')
    directed = linked['takes_direction'] if linked else engines.can(record.speaker_for(v), 'direction')
    if record.speaker_for(v)=='drama3':
        rows.extend([
            ('drama-inline','Inline delivery change','[whispers] Stay close. [normal voice] The gate is open.','neutral'),
            ('drama-event','Short vocal event','[sighs] Fine. We will wait here.','neutral'),
            ('drama-identity','Same speaker across turns','<|speaker:0|> Keep the gate closed. <|speaker:0|> I will return at sunrise.','neutral')])
    return [{'id': key, 'label': label, 'text': text, 'mood': mood,
             'status': 'pending' if mood == 'neutral' or directed else 'skipped',
             'reason': None if mood == 'neutral' or directed else 'This speaking model does not support mood direction'}
            for key, label, text, mood in rows]


def request(lib, npc, raw):
    if not isinstance(raw, dict) or set(raw) - {'names'}:
        raise ValueError('An audition accepts names to test')
    v = record.voice_of(npc)
    if not (v.get('lab_voice') or v.get('reference') or (v.get('mode') == 'stock' and v.get('stock_voice'))):
        raise ValueError('Assign a voice before running a character audition')
    rows = cases(npc, raw.get('names', ''))
    # Run CPU word checks for these clips without changing the character's saved lip-sync settings.
    frozen = record.freeze_voice(lib, npc['id'], v)
    studio = frozen.setdefault('studio', {})
    studio['lip_sync'] = alignment.clean(studio.get('lip_sync'))
    studio['lip_sync']['enabled'] = True
    session_id = uuid.uuid4().hex[:12]
    tests = []
    for row in rows:
        row.update(key=f'audition_{session_id}_{row["id"]}', review={k: 'unreviewed' for k in REVIEW_FIELDS}, notes='')
        if row['status'] == 'skipped':
            continue
        prepared = service.test_request({**npc, 'voice': frozen}, row)['test']
        prepared.update(key=row['key'], label='Audition: ' + row['label'], seed=record.new_seed())
        tests.append(prepared)
    session = {'id': session_id, 'created': now(), 'source': frozen, 'cases': rows,
               'engine': record.speaker_for(v), 'identity': record.identity_id(v), 'job': None}
    def save(rec):
        rv = record.voice_of(rec)
        retained = rv.get('auditions') or []
        if len(retained) >= 12:
            raise ValueError('This character already has 12 retained audition suites')
        rv['auditions'] = retained + [session]
        rec['voice'] = rv
    lib.update(npc['id'], save)
    return session_id, {'tests': tests, 'quick': True, 'voice_snapshot': frozen, 'audition_id': session_id}


def session(v, session_id):
    if not isinstance(session_id, str) or not ID.fullmatch(session_id):
        raise ValueError('Invalid audition id')
    found = next((s for s in v.get('auditions', []) if s['id'] == session_id), None)
    if found is None:
        raise KeyError(session_id)
    return found


def attach_job(lib, npc_id, session_id, job=None, error=None):
    def save(rec):
        row = session(rec['voice'], session_id)
        row.update(job=job, queue_error=error)
    return lib.update(npc_id, save)


def quality(data, meta):
    samples, rate = audio.read_wav(data)
    seconds = len(samples) / rate
    flags = []
    if not len(samples) or not np.isfinite(samples).all() or float(np.max(np.abs(samples), initial=0)) < .001:
        flags.append({'code': 'silent', 'message': 'Delivered clip is silent or invalid; regenerate it'})
    if len(samples) and float(np.mean(np.abs(samples) >= .999)) > .001:
        flags.append({'code': 'clipping', 'message': 'Delivered audio has samples at full scale; listen for clipping'})
    written=meta.get('synthesis_text') or meta.get('text','')
    plain=markup.plain(written,meta.get('engine'))
    words = len(re.findall(r"\b[\w']+\b", plain))
    if re.search(r'\[(?:laughs|sighs|gasps|coughs)\]',written) and seconds>max(12,words*2):
        flags.append({'code':'extended_event','message':'A short event line produced unusually long audio; review the reaction and pauses'})
    recognized=str(((meta.get('lip_sync') or {}).get('validation') or {}).get('recognized_text') or '').casefold()
    prompt=str((meta.get('model_identity') or {}).get('prompt') or written)
    if any(cue.casefold() in recognized and cue.casefold() not in plain.casefold()
           for cue in re.findall(r'\[([^\[\]\r\n]{4,160})\]',prompt)):
        flags.append({'code':'spoken_cue','message':'Recognition may contain delivery instructions; listen for spoken cue text'})
    if seconds and words >= 8 and not .5 <= words / seconds <= 7:
        flags.append({'code': 'pace', 'message': 'Unusual speech duration; listen for missing words, long pauses or rushed delivery'})
    timing = meta.get('lip_sync') or {}
    if timing.get('status') not in ('aligned', 'word_aligned'):
        flags.append({'code': 'alignment', 'message': 'Word check unavailable or failed: ' + str(timing.get('reason') or 'No validated timing')})
    if ((timing.get('validation') or {}).get('transcript_match') is False
            or 'Recognized words disagree with the synthesis transcript' in str(timing.get('reason') or '')):
        flags.append({'code': 'word_match', 'message': 'Recognized words did not match the synthesis text; listen for omissions or substitutions'})
    if timing.get('status') != 'aligned':
        flags.append({'code': 'mouth_fallback', 'message': 'Phoneme timing unavailable; amplitude mouth track retained'})
    return {'sha256': hashlib.sha256(data).hexdigest(), 'seconds': round(seconds, 3), 'flags': flags,
            'word_check': timing.get('status', 'unavailable'), 'human_review_required': True,
            'recognized_text': (timing.get('validation') or {}).get('recognized_text')}


def complete(rec, session_id, key, data, meta):
    row = next(r for r in session(rec['voice'], session_id)['cases'] if r['key'] == key)
    row.update(status='ready', quality=quality(data, meta))


def annotate(npc):
    v = record.voice_of(npc)
    for suite in v.get('auditions', []):
        for row in suite['cases']:
            clip = v['tests'].get(row['key'])
            if not clip:
                continue
            try:
                _, _, current = authoring.prepare(v, row['text'], row['mood'])
                row['stale'] = current['render_signature'] != clip.get('render_signature')
            except (ValueError, KeyError, TypeError):
                row['stale'] = True
    return npc


def review(lib, npc_id, session_id, case_id, raw):
    if not isinstance(raw, dict) or set(raw) - {*REVIEW_FIELDS, 'notes', 'sha256'}:
        raise ValueError('Only listening judgments and notes can be changed')
    def save(rec):
        v = record.voice_of(rec)
        row = next((r for r in session(v, session_id)['cases'] if r['id'] == case_id), None)
        if not row or row['status'] != 'ready':
            raise ValueError('Choose a completed audition clip')
        sha = row['quality']['sha256']
        if raw.get('sha256') != sha or hashlib.sha256(lib.read_asset(npc_id, v['tests'][row['key']]['file'])).hexdigest() != sha:
            raise ValueError('Audition audio checksum changed; reload or restore its original clip')
        judgment = {key: raw.get(key, row['review'][key]) for key in REVIEW_FIELDS}
        if any(not isinstance(value, str) or value not in ('unreviewed', 'clear', 'issue') for value in judgment.values()):
            raise ValueError('Listening judgments must be unreviewed, clear or issue')
        notes = raw.get('notes', row['notes'])
        if not isinstance(notes, str) or len(notes) > 1000:
            raise ValueError('Listening notes must be text up to 1000 characters')
        row.update(review=judgment, notes=notes.strip(), reviewed=now())
    return annotate(lib.update(npc_id, save))


def pack(lib, npc, session_id):
    v = record.voice_of(annotate(copy.deepcopy(npc)))
    suite = copy.deepcopy(session(v, session_id))
    manifest = {'schema': 1, 'npc_id': npc['id'], 'audition': suite, 'clips': {},
                'review_note': 'Automatic flags are screening evidence. Voice identity, pronunciation and delivery require listening.'}
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as zipped:
        ref = suite['source'].get('reference')
        if ref:
            data = lib.read_asset(npc['id'], ref['file'])
            if hashlib.sha256(data).hexdigest() != ref['sha256']:
                raise ValueError('Pinned audition reference checksum changed')
            zipped.writestr('reference.wav', data)
        for row in suite['cases']:
            if row['status'] != 'ready':
                continue
            meta = v['tests'][row['key']]
            data = lib.read_asset(npc['id'], meta['file'])
            if hashlib.sha256(data).hexdigest() != row['quality']['sha256']:
                raise ValueError('Audition audio checksum changed')
            zipped.writestr(row['id'] + '.wav', data)
            manifest['clips'][row['id']] = meta
        zipped.writestr('audition.json', json.dumps(manifest, indent=2, ensure_ascii=False))
    return out.getvalue()
