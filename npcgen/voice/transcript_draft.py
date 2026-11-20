"""Suggested exact transcript for a voice reference, drafted from its audio.

The draft comes from the same pinned Whisper model and CPU environment that word alignment uses. Nothing here saves
or approves anything: the caller shows the draft in the reference editor, and a person checks every word before the
trimmed reference is saved with it.
"""
import json
import os
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

from . import alignment, audio

WORKER = Path(__file__).with_name('transcript_draft_worker.py')
MAX_SECONDS = 120
LANGUAGE_CODES = {'English': 'en', 'Chinese': 'zh', 'Japanese': 'ja', 'Korean': 'ko', 'German': 'de', 'French': 'fr',
                  'Russian': 'ru', 'Portuguese': 'pt', 'Spanish': 'es', 'Italian': 'it'}
_draft_lock = threading.Lock()


def capabilities(cfg=None):
    conf = alignment.runtime_config(cfg)
    model_ready = all((Path(conf['whisper_model']) / name).is_file()
                      for name in ('model.bin', 'config.json', 'tokenizer.json', 'vocabulary.txt'))
    available = Path(conf['python']).is_file() and model_ready and shutil.which('ffmpeg') is not None
    return {'available': available, 'whisper_revision': alignment.WHISPER_REVISION,
            'reason': None if available else 'Needs the CPU alignment environment, its pinned Whisper model, and FFmpeg.'}


def draft(data, *, start=None, end=None, language='English', cfg=None):
    """Draft the words spoken in WAV bytes between start and end seconds (default: the whole clip)."""
    status = capabilities(cfg)
    if not status['available']:
        raise ValueError(status['reason'])
    samples, rate = audio.read_wav(data)
    seconds = len(samples) / rate
    start = 0.0 if start is None else start
    end = seconds if end is None else end
    if isinstance(start, bool) or isinstance(end, bool) or not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
        raise ValueError('Region times must be numbers')
    if not (0 <= start < end <= seconds + 1e-6):
        raise ValueError('Choose a region inside the reference')
    if end - start > MAX_SECONDS:
        raise ValueError(f'Draft at most {MAX_SECONDS} seconds at a time')
    region = audio.write_wav(samples[round(start * rate):round(end * rate)], rate)
    conf = alignment.runtime_config(cfg)
    env = {**os.environ, 'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1', 'HF_HUB_DISABLE_TELEMETRY': '1',
           'CUDA_VISIBLE_DEVICES': ''}
    with _draft_lock, tempfile.TemporaryDirectory(prefix='npcforge-draft-') as tmp:
        source = Path(tmp) / 'region.wav'
        source.write_bytes(region)
        pcm = Path(tmp) / 'speech.pcm'
        converted = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(source), '-ac', '1', '-ar', '16000',
                                    '-f', 's16le', str(pcm)], capture_output=True, text=True, check=False, timeout=30)
        if converted.returncode or not pcm.is_file():
            raise ValueError('The reference could not be prepared for drafting')
        request = {'pcm': str(pcm), 'language_code': LANGUAGE_CODES.get(language),
                   'whisper_model': conf['whisper_model'], 'whisper_sha256': alignment.WHISPER_SHA256}
        reply = subprocess.run([conf['python'], '-u', str(WORKER)], input=json.dumps(request), capture_output=True,
                               text=True, encoding='utf-8', env=env, timeout=180, check=False)
    if reply.returncode:
        raise ValueError('Transcript draft worker failed: ' + reply.stderr[-400:])
    try:
        result = json.loads(reply.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError):
        raise ValueError('Transcript draft returned no result') from None
    if result.get('status') != 'ok':
        raise ValueError(result.get('reason') or 'Transcript draft failed')
    return {'text': result['text'], 'detected_language': result.get('language'), 'draft': True,
            'seconds': round(end - start, 3), 'whisper_revision': alignment.WHISPER_REVISION}
