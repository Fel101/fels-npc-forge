"""Voice Lab updates in NPC Forge: reference transcript drafts, noise cleanup, and proxy voice match.

The unit tests need no model. Tests that run a real worker skip when the alignment environment, Voice Lab's speaker
encoder, FFmpeg, or the private library clip is not present.
"""
import base64
import os
import shutil
from pathlib import Path

import numpy as np
import pytest

from npcgen import roll
from npcgen.library import Library
from npcgen.voice import alignment, audio, reference_clean, service, transcript_draft
from npcgen.voice.voicelibrary import VoiceLibrary

ROOT = Path(__file__).resolve().parents[1]
LAB = Path(os.environ.get('NPCGEN_VOICE_LAB') or ROOT / 'voice-lab')     # the real check runs only where a Voice Lab is set
REAL_CLIP = ROOT / 'library' / 'clara-kowalski-da313a' / 'voice' / 'reference.wav'
HAS_FFMPEG = shutil.which('ffmpeg') is not None


def wav(seconds=6, frequency=220):
    rate = 16000
    samples = .3 * np.sin(2 * np.pi * frequency * np.arange(int(seconds * rate)) / rate)
    return audio.write_wav(samples, rate)


def upload(lib, npc_id):
    return service.apply_upload(lib, lib.get(npc_id), {
        'audio': base64.b64encode(wav()).decode(), 'text': 'This is the exact reference transcript.',
        'consent': True, 'speaker': 'qwen'})


@pytest.fixture
def studio(tmp_path):
    lib = Library(tmp_path / 'library')
    a, _b = [lib.create({**roll.roll_npc(roll.load_data(), 'fantasy'), 'style': 'render3d'}) for _ in range(2)]
    upload(lib, a['id'])
    return lib, VoiceLibrary(lib), a['id']


# ---- transcript drafts ----------------------------------------------------------------------------------------------

def test_transcript_draft_is_unavailable_without_the_alignment_environment(tmp_path):
    cfg = {'voice': {'alignment': {'python': str(tmp_path / 'missing' / 'python.exe')}}}
    status = transcript_draft.capabilities(cfg)
    assert status['available'] is False
    with pytest.raises(ValueError, match='alignment environment'):
        transcript_draft.draft(wav(), cfg=cfg)


def test_transcript_draft_rejects_regions_outside_the_clip_before_running_anything(tmp_path):
    if not transcript_draft.capabilities()['available']:
        pytest.skip('the CPU alignment environment is not installed')
    with pytest.raises(ValueError, match='inside the reference'):
        transcript_draft.draft(wav(seconds=3), start=2.0, end=9.0)
    with pytest.raises(ValueError, match='numbers'):
        transcript_draft.draft(wav(seconds=3), start=True, end=2.0)


@pytest.mark.skipif(not REAL_CLIP.is_file() or not transcript_draft.capabilities()['available'],
                    reason='needs the private reference clip and the alignment environment')
def test_transcript_draft_of_a_real_reference_is_a_draft_not_a_saved_transcript():
    result = transcript_draft.draft(REAL_CLIP.read_bytes(), language='English')
    assert result['draft'] is True
    assert result['text'].strip()
    assert result['whisper_revision'] == alignment.WHISPER_REVISION


# ---- noise cleanup -------------------------------------------------------------------------------------------------

@pytest.mark.parametrize('nf', [-10, -85, 'loud', True, float('nan')])
def test_noise_floor_must_be_a_number_inside_afftdn_range(nf):
    with pytest.raises(ValueError):
        reference_clean.validate_nf(nf)


@pytest.mark.skipif(not HAS_FFMPEG, reason='FFmpeg is not on PATH')
def test_cleaning_keeps_the_length_and_reports_the_quiet_floor_change():
    noisy = wav(seconds=4)
    cleaned, measured = reference_clean.clean(noisy, -25)
    assert cleaned[:4] == b'RIFF'
    assert audio.read_wav(cleaned)[0].shape == audio.read_wav(noisy)[0].shape
    assert measured['filter'] == 'afftdn=nf=-25'
    assert set(measured['quiet_floor_dbfs']) == {'before', 'after', 'reduction_db'}


@pytest.mark.skipif(not HAS_FFMPEG, reason='FFmpeg is not on PATH')
def test_denoise_creates_an_unapproved_draft_that_keeps_the_words_and_says_identity_is_not_checked(studio):
    lib, store, npc_id = studio
    entry = store.from_npc(npc_id, {'name': 'Harbor captain', 'tags': [], 'version_notes': ''})
    parent = entry['versions'][0]
    result = store.denoise(parent['id'], {})
    assert result['id'] != parent['id']
    assert result['approved'] is False
    assert result['reference']['text'] == parent['reference']['text']
    assert result['provenance']['parent_version'] == parent['id']
    assert result['provenance']['identity']['status'] == 'not_checked'
    assert 'denoise' in result['provenance']['edits']


def test_denoise_refuses_out_of_range_noise_floor_before_writing_a_version(studio):
    lib, store, npc_id = studio
    voice_id = store.from_npc(npc_id, {'name': 'Harbor captain', 'tags': [], 'version_notes': ''})['id']
    parent = store.get(voice_id)['versions'][0]
    with pytest.raises(ValueError, match='between -80 and -20'):
        store.denoise(parent['id'], {'nf': -5})
    assert len(store.get(voice_id)['versions']) == 1


# ---- proxy voice match ---------------------------------------------------------------------------------------------
