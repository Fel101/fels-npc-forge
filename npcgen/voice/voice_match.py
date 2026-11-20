"""Proxy voice-match score for a take against its voice's reference.

The score comes from Voice Lab's speaker encoder (Chatterbox, CPU) and its calibrated bands. Voice Lab measured
those bands on one target voice, so they are a rough guide, not a verdict. Read the label with that in mind:
it says whether the take sounds closer to the reference than other voices do, not whether a listener would
call it the same person.
"""
import json
import os
import subprocess
import tempfile
from pathlib import Path

from . import engines

WORKER = Path(__file__).with_name('voice_match_worker.py')
ENCODER_WEIGHTS = Path('hf-cache/hub/models--ResembleAI--chatterbox-turbo/snapshots/'
                       '749d1c1a46eb10492095d68fbcf55691ccf137cd/ve.safetensors')
LABELS = {'likely_same': 'closer to the reference voice than other voices are',
          'uncertain': 'between the reference and other voices',
          'likely_different': 'closer to other voices than to the reference'}
CALIBRATION_NOTE = 'Calibrated by Voice Lab on one target voice; a proxy, not a listening test.'


def _lab_python(lab):
    return lab / ('.venv/Scripts/python.exe' if os.name == 'nt' else '.venv/bin/python')


def capabilities(cfg=None):
    lab = engines.lab_root(cfg)
    if lab is None:
        return {'available': False, 'reason': 'Set voice.lab_path to the Voice Lab installation to score voice matches.'}
    missing = [str(path) for path in (_lab_python(lab), lab / 'speaker_similarity.py',
                                      lab / 'out/speaker-similarity/calibration.json', lab / ENCODER_WEIGHTS)
               if not path.is_file()]
    if missing:
        return {'available': False, 'reason': 'Voice Lab speaker encoder or calibration is missing: ' + ', '.join(missing)}
    return {'available': True, 'lab': str(lab), 'reason': None, 'note': CALIBRATION_NOTE}


def score(cfg, reference_bytes, take_bytes):
    """Proxy similarity between two WAV clips, read against Voice Lab's calibrated bands."""
    status = capabilities(cfg)
    if not status['available']:
        raise ValueError(status['reason'])
    lab = Path(status['lab'])
    env = {**os.environ, 'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1', 'HF_HUB_DISABLE_TELEMETRY': '1',
           'CUDA_VISIBLE_DEVICES': ''}
    with tempfile.TemporaryDirectory(prefix='npcforge-voice-match-') as tmp:
        reference, take = Path(tmp) / 'reference.wav', Path(tmp) / 'take.wav'
        reference.write_bytes(reference_bytes)
        take.write_bytes(take_bytes)
        reply = subprocess.run([str(_lab_python(lab)), '-I', str(WORKER), '--lab', str(lab),
                                '--reference', str(reference), '--take', str(take)],
                               capture_output=True, text=True, encoding='utf-8', env=env, timeout=300, check=False)
    if reply.returncode:
        raise ValueError('Voice match worker failed: ' + reply.stderr.strip()[-300:])
    try:
        result = json.loads(reply.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError):
        raise ValueError('Voice match returned no result') from None
    if result.get('status') != 'ok':
        raise ValueError('Voice match failed')
    return {'available': True, 'score': result['score'], 'band': result['band'],
            'meaning': LABELS.get(result['band'], result['band']), 'proxy': True, 'note': CALIBRATION_NOTE}


def for_take(lib, npc_id, voice, take_id, cfg=None):
    """Score one take of an NPC's voice against that voice's reference. Unavailable when there is no reference."""
    take = next((t for t in voice.get('takes') or [] if t.get('id') == take_id), None)
    if take is None:
        raise KeyError(take_id)
    reference = (voice.get('reference') or {}).get('file')
    if not reference:
        return {'available': False, 'reason': 'This voice has no reference clip to compare against (a stock voice).'}
    status = capabilities(cfg)
    if not status['available']:
        return {'available': False, 'reason': status['reason']}
    return {**score(cfg, lib.read_asset(npc_id, reference), lib.read_asset(npc_id, take['file'])),
            'take_id': take_id, 'reference': reference}
