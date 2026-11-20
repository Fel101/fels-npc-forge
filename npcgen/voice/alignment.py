"""Validated final-audio timing and configurable visemes; explicit amplitude fallback."""
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from . import audio, markup

ROOT = Path(__file__).resolve().parents[2]
WHISPER_REVISION = 'd90ca5fe260221311c53c58e660288d3deb8d356'
WHISPER_SHA256 = 'dcb76c6586fc06cbdac6dd21f14cfd129cc4cdd9dce19bf4ffa62e59cbe6e6d1'
PHONES = 'AA AE AH AO AW AY B CH D DH EH ER EY F G HH IH IY JH K L M N NG OW OY P R S SH T TH UH UW V W Y Z ZH SIL'.split()
VISEMES = ['sil', 'PP', 'FF', 'TH', 'DD', 'kk', 'CH', 'SS', 'nn', 'RR', 'aa', 'E', 'ih', 'oh', 'ou']
GROUPS = {'sil': 'SIL', 'PP': 'P B M', 'FF': 'F V', 'TH': 'TH DH', 'DD': 'T D N', 'kk': 'K G NG', 'CH': 'CH JH SH ZH',
          'SS': 'S Z', 'nn': 'L', 'RR': 'R ER', 'aa': 'AA AE AH HH AY', 'E': 'EH EY', 'ih': 'IH IY Y', 'oh': 'AO OW OY', 'ou': 'UH UW W AW'}
MAPPING = {phone: shape for shape, phones in GROUPS.items() for phone in phones.split()}
VERSION = 1


def clean(raw=None):
    raw = {} if raw is None else raw
    if not isinstance(raw, dict) or set(raw) - {'enabled', 'fps', 'blend_ms', 'mapping', 'phoneme_overrides'}:
        raise ValueError('Lip sync accepts enabled, fps, blend_ms, mapping and phoneme_overrides')
    c = {'enabled': False, 'fps': 60, 'blend_ms': 50, 'mapping': dict(MAPPING), 'phoneme_overrides': {}, **raw}
    if not isinstance(c['enabled'], bool):
        raise ValueError('Lip sync enabled must be true or false')
    for key, lo, hi in [('fps', 24, 120), ('blend_ms', 0, 120)]:
        value = c[key]
        if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
            raise ValueError(f'Lip sync {key} must be a whole number from {lo} to {hi}')
    if not isinstance(c['mapping'], dict) or set(c['mapping']) != set(PHONES) or any(v not in VISEMES for v in c['mapping'].values()):
        raise ValueError('Map every supported ARPABET phone to a known viseme')
    overrides = c['phoneme_overrides']
    if not isinstance(overrides, dict) or len(overrides) > 200:
        raise ValueError('Use at most 200 phoneme overrides')
    for word, phones in overrides.items():
        if not isinstance(word, str) or not re.fullmatch(r"[a-z]+(?:'[a-z]+)?", word) or len(word) > 60 or not isinstance(phones, list) or not 1 <= len(phones) <= 30 or any(p not in PHONES or p == 'SIL' for p in phones):
            raise ValueError('Phoneme overrides need a lowercase spoken token and 1-30 ARPABET phones')
    return c


def signature(raw=None):
    c = clean(raw)
    return hashlib.sha256(json.dumps(c if c['enabled'] else {'enabled': False}, sort_keys=True).encode()).hexdigest()


def runtime_config(cfg=None):
    override = ((cfg or {}).get('voice') or {}).get('alignment') or {}
    return {'python': str(override.get('python') or ROOT / ('local/.venv-align/Scripts/python.exe' if os.name == 'nt' else 'local/.venv-align/bin/python')),
            'whisper_model': str(override.get('whisper_model') or Path.home() / f'.cache/huggingface/hub/models--Systran--faster-whisper-tiny/snapshots/{WHISPER_REVISION}')}


def capabilities(cfg=None):
    conf = runtime_config(cfg)
    available = Path(conf['python']).is_file() and all((Path(conf['whisper_model']) / f).is_file() for f in ('model.bin', 'config.json', 'tokenizer.json', 'vocabulary.txt'))
    return {**conf, 'available': available, 'schema': VERSION, 'phoneme_languages': ['English'],
            'verified_languages': ['English'], 'word_timings_other_languages': 'experimental; must pass transcript/confidence checks',
            'phones': PHONES, 'visemes': VISEMES, 'defaults': clean(),
            'reason': None if available else 'Install the separate CPU alignment environment and supply the pinned cached Whisper model'}


def validate(result, seconds):
    if result.get('status') not in ('aligned', 'word_aligned'):
        raise ValueError(result.get('reason') or 'Alignment failed')
    words, phones = result.get('words'), result.get('phonemes')
    if not isinstance(words, list) or not words or len(words) > 500 or not isinstance(phones, list) or len(phones) > 6000:
        raise ValueError('Invalid alignment interval count')
    for rows, kind in ((words, 'word'), (phones, 'phone')):
        previous = 0.
        for row in rows:
            start, end = row.get('start'), row.get('end')
            if isinstance(start, bool) or isinstance(end, bool) or not isinstance(start, (int, float)) or not isinstance(end, (int, float)) or not math.isfinite(start + end) or not 0 <= start < end <= seconds + 1e-6 or start < previous - 1e-6:
                raise ValueError(f'{kind} intervals must be finite, ordered, positive and inside the final audio')
            previous = end
            if kind == 'word' and (not isinstance(row.get('word'), str) or not row['word'].strip()):
                raise ValueError('Word intervals need their spoken token')
            if kind == 'phone':
                index = row.get('word_index')
                if row.get('phone') not in PHONES or isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(words) or start < words[index]['start'] - 1e-6 or end > words[index]['end'] + 1e-6:
                    raise ValueError('Phonemes must use supported phones and remain inside their word interval')
    if result['status'] == 'aligned' and not phones:
        raise ValueError('Phoneme-aligned speech needs actual phone intervals')
    verification = result.get('validation') or {}
    def probability(key, minimum):
        value = verification.get(key)
        return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value) and minimum <= value <= 1
    if verification.get('transcript_match') is not True or not probability('mean_word_probability', .5) or not probability('min_word_probability', .15):
        raise ValueError('Alignment did not pass independent transcript/confidence checks')
    if result['status'] == 'aligned':
        delta = verification.get('max_boundary_delta_seconds')
        if isinstance(delta, bool) or not isinstance(delta, (int, float)) or not math.isfinite(delta) or not 0 <= delta <= .35:
            raise ValueError('Alignment did not pass the independent word-boundary check')
        for index, word in enumerate(words):
            contained = [p for p in phones if p['word_index'] == index]
            if not contained or abs(contained[0]['start'] - word['start']) > 1e-6 or abs(contained[-1]['end'] - word['end']) > 1e-6:
                raise ValueError('Phoneme timing must cover each aligned word')
    return result


def visemes(phones, seconds, settings):
    c = clean(settings)
    cues = []; cursor = 0.
    for phone in phones:
        if phone['start'] > cursor:
            cues.append({'start': cursor, 'end': phone['start'], 'value': c['mapping']['SIL']})
        cues.append({'start': phone['start'], 'end': phone['end'], 'value': c['mapping'][phone['phone']], 'phone': phone['phone']})
        cursor = phone['end']
    if cursor < seconds:
        cues.append({'start': cursor, 'end': seconds, 'value': c['mapping']['SIL']})
    frames = []; cue_index = 0; blend = c['blend_ms'] / 1000
    for frame in range(math.ceil(seconds * c['fps']) + 1):
        t = min(frame / c['fps'], seconds)
        while cue_index + 1 < len(cues) and t >= cues[cue_index]['end']:
            cue_index += 1
        cue = cues[cue_index]
        weights = {cue['value']: 1.}
        window = min(blend, (cue['end'] - cue['start']) / 2)
        if window and cue_index > 0 and t - cue['start'] < window:
            a = min(1., max(0., (t - cue['start']) / window))
            old = cues[cue_index - 1]['value']
            if old != cue['value']:
                weights = {old: round(1 - a, 4), cue['value']: round(a, 4)}
        if t == seconds:
            weights = {c['mapping']['SIL']: 1.}
        frames.append({'time': round(t, 6), 'weights': weights})
    return {'fps': c['fps'], 'blend_ms': c['blend_ms'], 'mapping': c['mapping'], 'cues': cues, 'frames': frames}


def validate_visemes(track, seconds):
    if not isinstance(track, dict) or isinstance(track.get('fps'), bool) or not isinstance(track.get('fps'), int) or not 24 <= track['fps'] <= 120:
        raise ValueError('Invalid viseme frame rate')
    frames, cues = track.get('frames'), track.get('cues')
    if not isinstance(frames, list) or len(frames) != math.ceil(seconds * track['fps']) + 1 or not isinstance(cues, list) or not 1 <= len(cues) <= 12001:
        raise ValueError('Viseme frames must cover the exact audio duration')
    for index, row in enumerate(frames):
        t = row.get('time'); weights = row.get('weights')
        if isinstance(t, bool) or not isinstance(t, (int, float)) or not math.isfinite(t) or abs(t - min(index / track['fps'], seconds)) > 1e-6:
            raise ValueError('Viseme frame times must follow the selected frame rate')
        if not isinstance(weights, dict) or not weights or any(shape not in VISEMES or isinstance(weight, bool) or not isinstance(weight, (int, float)) or not math.isfinite(weight) or not 0 <= weight <= 1 for shape, weight in weights.items()) or abs(sum(weights.values()) - 1) > .001:
            raise ValueError('Viseme weights must be finite and sum to one')
    end = 0.
    for cue in cues:
        start, finish = cue.get('start'), cue.get('end')
        if isinstance(start, bool) or isinstance(finish, bool) or not isinstance(start, (int, float)) or not isinstance(finish, (int, float)) or not math.isfinite(start + finish) or abs(start - end) > 1e-6 or not start < finish <= seconds + 1e-6 or cue.get('value') not in VISEMES:
            raise ValueError('Viseme cues must continuously cover the audio')
        end = finish
    if abs(end - seconds) > 1e-6:
        raise ValueError('Viseme cues must reach the exact audio end')
    return track


def process(data, text, language='English', settings=None, cfg=None, cancel=None):
    c = clean(settings); samples, rate = audio.read_wav(data); seconds = len(samples) / rate
    base = {'schema': VERSION, 'audio_sha256': hashlib.sha256(data).hexdigest(), 'sample_rate': rate, 'samples': len(samples),
            'seconds': seconds, 'mouth_fps': 1000 // audio.MOUTH_FRAME_MS, 'config_signature': signature(c),
            'text_sha256': hashlib.sha256(text.encode()).hexdigest(), 'timing_basis': 'final_delivered_audio', 'settings': c}
    def fallback(reason, result=None):
        out = {**base, 'status': 'amplitude_fallback', 'reason': reason, 'words': [], 'phonemes': [], 'visemes': None}
        if result:
            out.update(backend=result.get('backend'), validation=result.get('validation'))
        return out
    if not c['enabled']:
        return fallback('Alignment is disabled for this voice')
    cap = capabilities(cfg)
    if not cap['available']:
        return fallback(cap['reason'])
    if not seconds or seconds > 120 or not text.strip() or len(text) > 1000 or not shutil.which('ffmpeg'):
        return fallback('Alignment needs nonempty speech/text, at most 120 seconds, and FFmpeg')
    if cancel is not None and cancel.is_set():
        return fallback('Alignment cancelled')
    try:
        with tempfile.TemporaryDirectory(prefix='npcforge-align-') as tmp:
            converted = subprocess.run(['ffmpeg', '-v', 'error', '-f', 'wav', '-i', 'pipe:0', '-ac', '1', '-ar', '16000', '-f', 's16le', 'pipe:1'],
                                       input=data, capture_output=True, check=True, timeout=20)
            pcm = Path(tmp) / 'delivered.pcm'; pcm.write_bytes(converted.stdout)
            request = {'pcm': str(pcm), 'text': markup.plain(text), 'language': language, 'whisper_model': cap['whisper_model'],
                       'phoneme_overrides': c['phoneme_overrides'], 'whisper_revision': WHISPER_REVISION, 'whisper_sha256': WHISPER_SHA256}
            env = {**os.environ, 'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1', 'HF_HUB_DISABLE_TELEMETRY': '1', 'CUDA_VISIBLE_DEVICES': ''}
            reply = subprocess.run([cap['python'], '-u', str(Path(__file__).with_name('alignment_worker.py'))], input=json.dumps(request),
                                   capture_output=True, text=True, encoding='utf-8', env=env, timeout=60)
            if reply.returncode:
                return fallback('CPU alignment worker failed: ' + reply.stderr[-500:])
            result = json.loads(reply.stdout)
            validate(result, seconds)
            if cancel is not None and cancel.is_set():
                return fallback('Alignment cancelled')
            track = visemes(result['phonemes'], seconds, c) if result['phonemes'] else None
            if track: validate_visemes(track, seconds)
            return {**base, **result, 'visemes': track, 'mouth_method': 'visemes' if track else 'amplitude_fallback',
                    'phoneme_timing_supported': bool(track), 'amplitude_track_retained': True}
    except (OSError, ValueError, subprocess.SubprocessError, KeyError, TypeError) as e:
        return fallback(str(e), locals().get('result'))


def is_stale(meta, voice):
    settings = (voice.get('studio') or {}).get('lip_sync') or {}
    actual = (meta.get('lip_sync') or {}).get('config_signature')
    return (actual != signature(settings)) if actual else clean(settings)['enabled']


def for_delivery(data, meta, voice):
    stored = meta.get('lip_sync') or {}
    if stored.get('audio_sha256') == hashlib.sha256(data).hexdigest() and not is_stale(meta, voice):
        try:
            if stored.get('status') in ('aligned', 'word_aligned'):
                samples, rate = audio.read_wav(data)
                validate(stored, len(samples) / rate)
                text = meta.get('synthesis_text') or meta.get('text') or ''
                if stored.get('text_sha256') != hashlib.sha256(text.encode()).hexdigest():
                    raise ValueError('Alignment transcript changed')
                if stored.get('status') == 'aligned':
                    validate_visemes(stored.get('visemes'), len(samples) / rate)
            return stored
        except (ValueError, KeyError, TypeError):
            pass
    fallback = process(data, meta.get('synthesis_text') or meta.get('text') or '', settings={'enabled': False})
    fallback['reason'] = 'Alignment is missing or outdated for this delivered audio/configuration'
    fallback['settings'] = clean((voice.get('studio') or {}).get('lip_sync'))
    fallback['config_signature'] = signature(fallback['settings'])
    fallback['needs_update'] = fallback['settings']['enabled']
    return fallback


def source(npc, key):
    v = npc.get('voice') or {}
    if key == 'reference':
        meta = v.get('reference')
    elif isinstance(key, str) and key.startswith('line:'):
        meta = (npc.get('voice_lines') or {}).get(key[5:])
    elif isinstance(key, str) and key.startswith('test:'):
        meta = (v.get('tests') or {}).get(key[5:])
    elif isinstance(key, str) and key.startswith('take:'):
        meta = next((t for t in v.get('takes', []) if t['id'] == key[5:]), None)
    else:
        meta = None
    if not meta or not meta.get('file'):
        raise ValueError('Choose an existing speech clip')
    return meta


def analyze_source(lib, npc, key, settings=None, cfg=None):
    from . import effects
    meta = source(npc, key)
    data = effects.check_delivery(lib.read_asset(npc['id'], meta['file']), meta)
    text = meta.get('synthesis_text') or meta.get('text') or ''
    language = (meta.get('voice_snapshot') or {}).get('language') or meta.get('language') or (npc.get('voice') or {}).get('language', 'English')
    result = process(data, text, language, settings, cfg)
    file = f"voice/alignment/audio/{result['audio_sha256']}.wav"
    path = lib.resolve(npc['id'], file)
    path.parent.mkdir(parents=True, exist_ok=True)
    with lib.lock:
        try:
            with path.open('xb') as stream:
                stream.write(data)
        except FileExistsError:
            if hashlib.sha256(path.read_bytes()).hexdigest() != result['audio_sha256']:
                raise ValueError('Alignment preview snapshot checksum failed')
    return {'lip_sync': result, 'source': key, 'authored_text': meta.get('text'), 'synthesis_text': text,
            'audio_url': f"/api/npcs/{npc['id']}/files/{file}", 'mouth': audio.mouth_track(*audio.read_wav(data))}
