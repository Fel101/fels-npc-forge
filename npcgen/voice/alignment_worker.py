"""Isolated, offline CPU speech verification and word/phone alignment."""
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import sys
import wave

def tokens(text):
    return re.findall(r"[^\W_]+(?:'[^\W_]+)*", text.casefold().replace('’', "'"), re.UNICODE)


def canonical(word):
    stem, apostrophe, tail = word.partition("'")
    stem = {'traveller': 'traveler', 'harbour': 'harbor', 'colour': 'color', 'favourite': 'favorite'}.get(stem, stem)
    return stem + apostrophe + tail


def expansions(word):
    fixed = {"can't": ['can', 'not'], "won't": ['will', 'not'], "don't": ['do', 'not'], "doesn't": ['does', 'not'],
             "didn't": ['did', 'not'], "isn't": ['is', 'not'], "aren't": ['are', 'not'], "wasn't": ['was', 'not'],
             "weren't": ['were', 'not'], "haven't": ['have', 'not'], "hasn't": ['has', 'not'], "hadn't": ['had', 'not'],
             "shouldn't": ['should', 'not'], "wouldn't": ['would', 'not'], "couldn't": ['could', 'not']}
    if word in fixed:
        return [fixed[word]]
    for suffix, choices in {"'s": ['is', 'has'], "'d": ['would', 'had'], "'re": ['are'], "'ve": ['have'], "'ll": ['will'], "'m": ['am']}.items():
        if word.endswith(suffix) and len(word) > len(suffix):
            return [[word[:-len(suffix)], choice] for choice in choices]
    return []


def match_words(expected, recognized, equivalent):
    """Only exact/phonetic matches and explicit contractions; never fuzzy edits."""
    groups = []; left = right = 0
    while left < len(expected) and right < len(recognized):
        a, b = expected[left], recognized[right]['word']
        sizes = None
        if equivalent(a, b):
            sizes = (1, 1)
        else:
            for expanded in expansions(b):
                if expected[left:left + len(expanded)] == expanded:
                    sizes = (len(expanded), 1); break
            if sizes is None:
                for expanded in expansions(a):
                    if [r['word'] for r in recognized[right:right + len(expanded)]] == expanded:
                        sizes = (1, len(expanded)); break
        if sizes is None:
            return None
        nleft, nright = sizes
        chosen = recognized[right:right + nright]
        groups.append({'expected_indices': list(range(left, left + nleft)), 'recognized_indices': list(range(right, right + nright)),
                       'start': chosen[0]['start'], 'end': chosen[-1]['end'], 'probability': min(r['probability'] for r in chosen)})
        left += nleft; right += nright
    return groups if left == len(expected) and right == len(recognized) else None


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run(request):
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['TRANSFORMERS_OFFLINE'] = '1'
    os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'
    import numpy as np
    from faster_whisper import WhisperModel
    from pocketsphinx import Decoder, get_model_path
    pcm = Path(request['pcm']).read_bytes()
    speech = np.frombuffer(pcm, '<i2').astype(np.float32) / 32768
    seconds = len(speech) / 16000
    expected = [canonical(w) for w in tokens(request['text'])]
    model_path = Path(request['whisper_model'])
    receipt = {'backend': 'faster-whisper+pocketsphinx', 'device': 'cpu', 'compute_type': 'int8',
        'versions': {p: importlib.metadata.version(p) for p in ('faster-whisper', 'pocketsphinx', 'ctranslate2')},
        'whisper_checkpoint': 'Systran/faster-whisper-tiny', 'whisper_revision': request['whisper_revision'],
        'whisper_model_sha256': digest(model_path / 'model.bin'),
        'dictionary_sha256': digest(Path(get_model_path()) / 'en-us/cmudict-en-us.dict'),
        'acoustic_model_sha256': digest(Path(get_model_path()) / 'en-us/en-us/mdef'),
        'frame_rate': 100, 'worker_sha256': digest(__file__)}
    if receipt['whisper_model_sha256'] != request['whisper_sha256'] or receipt['versions'] != {'faster-whisper': '1.2.1', 'pocketsphinx': '5.0.4', 'ctranslate2': '4.8.2'}:
        return {'status': 'error', 'reason': 'Alignment runtime/model differs from the verified pins', 'backend': receipt}
    acoustic = Path(get_model_path()) / 'en-us/en-us'
    receipt['acoustic_files'] = {p.name: digest(p) for p in sorted(acoustic.iterdir()) if p.is_file()}
    pins = json.loads(Path(__file__).with_name('alignment-models.json').read_text('utf-8'))
    if receipt['dictionary_sha256'] != pins['dictionary_sha256'] or receipt['acoustic_files'] != pins['acoustic_files'] or any(
            digest(model_path / name) != sha for name, sha in pins['whisper_files'].items()):
        return {'status': 'error', 'reason': 'Alignment model assets differ from the verified checksums', 'backend': receipt}
    # Unprompted recognition is an independent guard against forcing invented
    # text onto noise, silence, mismatched speech or a strongly altered effect.
    model = WhisperModel(str(model_path), device='cpu', compute_type='int8', cpu_threads=2, local_files_only=True)
    language = request.get('language', 'English')
    code = {'English': 'en', 'Chinese': 'zh', 'Japanese': 'ja', 'Korean': 'ko', 'German': 'de', 'French': 'fr',
            'Russian': 'ru', 'Portuguese': 'pt', 'Spanish': 'es', 'Italian': 'it'}.get(language)
    segments, info = model.transcribe(speech, language=code, word_timestamps=True, vad_filter=True,
        condition_on_previous_text=False, beam_size=5, temperature=0, initial_prompt=None)
    recognized = []
    for segment in segments:
        for word in segment.words or []:
            parts = tokens(word.word)
            if len(parts) != 1:
                return {'status': 'rejected', 'reason': 'Recognition did not return one token per word interval', 'backend': receipt}
            recognized.append({'word': canonical(parts[0]), 'start': float(word.start), 'end': float(word.end),
                               'probability': float(word.probability)})
    diagnostic = {'recognized_text': ' '.join(w['word'] for w in recognized), 'language': info.language,
                  'word_probabilities': [w['probability'] for w in recognized]}
    if not recognized or not expected:
        return {'status': 'rejected', 'reason': 'No recognizable matching speech', 'validation': diagnostic, 'backend': receipt}
    dictionary = Path(get_model_path()) / 'en-us/cmudict-en-us.dict'
    overrides = request.get('phoneme_overrides') or {}
    if info.language == 'en' and overrides:
        # PocketSphinx cannot replace a dictionary entry via add_word. Build a
        # private dictionary, retaining the pinned base file untouched.
        lines = [line for line in dictionary.read_text('utf-8').splitlines()
                 if line.split(' ', 1)[0].split('(', 1)[0] not in overrides]
        lines += [word + ' ' + ' '.join(phones) for word, phones in overrides.items()]
        dictionary = Path(request['pcm']).with_name('alignment.dict')
        dictionary.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    receipt['effective_dictionary_sha256'] = digest(dictionary)
    receipt['phoneme_overrides'] = overrides
    decoder = Decoder(lm=None, dict=str(dictionary), logfn=os.devnull, samprate=16000) if info.language == 'en' else None
    def equivalent(a, b):
        if canonical(a) == canonical(b):
            return True
        return bool(decoder and decoder.lookup_word(a) and decoder.lookup_word(a) == decoder.lookup_word(b))
    groups = match_words(expected, recognized, equivalent)
    if not groups:
        return {'status': 'rejected', 'reason': 'Recognized words disagree with the synthesis transcript', 'validation': diagnostic, 'backend': receipt}
    mean_probability = sum(w['probability'] for w in recognized) / len(recognized)
    if mean_probability < .5 or min(w['probability'] for w in recognized) < .15:
        return {'status': 'rejected', 'reason': 'Recognition confidence is too low', 'validation': diagnostic, 'backend': receipt}
    diagnostic.update(transcript_match=True, mean_word_probability=mean_probability,
                      min_word_probability=min(w['probability'] for w in recognized), max_boundary_delta_seconds=None,
                      recognition_groups=groups, word_boundary_check_basis='recognition_groups; contractions share independent boundaries')
    if not decoder:
        return {'status': 'word_aligned', 'words': recognized, 'phonemes': [], 'backend': receipt, 'validation': diagnostic,
                'reason': 'Phoneme timing is supported by this acoustic model for English only'}
    unknown = [w for w in expected if not decoder.lookup_word(w)]
    if unknown:
        return {'status': 'word_aligned', 'words': recognized, 'phonemes': [], 'backend': receipt, 'validation': diagnostic,
                'reason': 'Unknown English tokens need explicit ARPABET phonemes: ' + ', '.join(unknown)}
    try:
        decoder.set_align_text(' '.join(expected))
        decoder.start_utt(); decoder.process_raw(pcm, full_utt=True); decoder.end_utt()
        decoder.set_alignment()
        decoder.start_utt(); decoder.process_raw(pcm, full_utt=True); decoder.end_utt()
    except RuntimeError as e:
        return {'status': 'rejected', 'reason': 'Forced word/phoneme timing could not fit the delivered speech',
                'worker_error': str(e), 'validation': diagnostic, 'backend': receipt}
    aligned, phonemes = [], []
    for entry in decoder.get_alignment() or []:
        name = re.sub(r'\(\d+\)$', '', entry.name)
        if name.startswith('<'):
            continue
        index = len(aligned)
        group_index = next((i for i, group in enumerate(groups) if index in group['expected_indices']), None)
        row = {'word': name, 'start': entry.start / 100, 'end': (entry.start + entry.duration) / 100,
               'probability': groups[group_index]['probability'] if group_index is not None else 0,
               'recognition_group': group_index}
        aligned.append(row)
        for phone in entry:
            if phone.name != 'SIL':
                phonemes.append({'phone': phone.name, 'start': phone.start / 100,
                    'end': (phone.start + phone.duration) / 100, 'word_index': index, 'acoustic_score': phone.score})
    if len(aligned) != len(expected) or [w['word'] for w in aligned] != expected:
        return {'status': 'rejected', 'reason': 'Forced alignment did not cover the transcript exactly', 'validation': diagnostic, 'backend': receipt}
    delta = max(max(abs(aligned[g['expected_indices'][0]]['start'] - g['start']),
                    abs(aligned[g['expected_indices'][-1]]['end'] - g['end'])) for g in groups)
    diagnostic['max_boundary_delta_seconds'] = delta
    if delta > .35:
        return {'status': 'rejected', 'reason': 'Independent word boundaries disagree by more than 350 ms', 'validation': diagnostic, 'backend': receipt}
    return {'status': 'aligned', 'words': aligned, 'phonemes': phonemes, 'backend': receipt, 'validation': diagnostic,
            'reason': 'Transcript, confidence and independent word-boundary checks passed; timings remain model estimates'}


if __name__ == '__main__':
    protocol_fd = os.dup(1)
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    with os.fdopen(protocol_fd, 'w', encoding='utf-8') as output:
        try:
            answer = run(json.loads(sys.stdin.readline()))
        except Exception as e:
            answer = {'status': 'error', 'reason': f'{type(e).__name__}: {e}'}
        output.write(json.dumps(answer, ensure_ascii=False, allow_nan=False) + '\n')
