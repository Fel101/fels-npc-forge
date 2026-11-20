"""Draft an exact transcript for one voice reference from its audio.

Runs in the CPU alignment environment (local/.venv-align) with the pinned, checksummed Whisper model that
alignment_worker.py already uses. It prints one JSON object on stdout. The text is a draft: a person must
check every word against the region before the reference is saved with it.
"""
import hashlib
import importlib.metadata
import json
import os
import sys
from pathlib import Path

PINNED_VERSIONS = {'faster-whisper': '1.2.1'}


def digest(path):
    h = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def run(request):
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['TRANSFORMERS_OFFLINE'] = '1'
    os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'
    import numpy as np
    from faster_whisper import WhisperModel

    versions = {p: importlib.metadata.version(p) for p in ('faster-whisper', 'ctranslate2')}
    if any(versions[p] != v for p, v in PINNED_VERSIONS.items()):
        return {'status': 'error', 'reason': 'Transcript draft runtime differs from the verified pins', 'versions': versions}
    model_path = Path(request['whisper_model'])
    if digest(model_path / 'model.bin') != request['whisper_sha256']:
        return {'status': 'error', 'reason': 'Whisper model differs from the verified checksum', 'versions': versions}
    speech = np.frombuffer(Path(request['pcm']).read_bytes(), '<i2').astype(np.float32) / 32768
    if speech.size < 16000 // 4:
        return {'status': 'error', 'reason': 'The selected region is too short to draft a transcript'}
    model = WhisperModel(str(model_path), device='cpu', compute_type='int8', cpu_threads=2, local_files_only=True)
    segments, info = model.transcribe(speech, language=request.get('language_code'), vad_filter=False,
                                      condition_on_previous_text=False, beam_size=5, temperature=0)
    text = ' '.join(segment.text.strip() for segment in segments).strip()
    return {'status': 'ok', 'text': text, 'language': info.language,
            'language_probability': round(float(info.language_probability), 3),
            'versions': versions, 'whisper_sha256': request['whisper_sha256']}


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
