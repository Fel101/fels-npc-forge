"""Alignment trust boundaries, complete timing intervals, visemes and audio binding."""
import copy
import hashlib

import numpy as np
import pytest

from npcgen.library import Library
from npcgen.voice import alignment, audio, authoring
from npcgen.voice.alignment_worker import match_words


def valid():
    return {'status': 'aligned', 'words': [{'word': 'hi', 'start': .1, 'end': .5}],
        'phonemes': [{'phone': 'HH', 'start': .1, 'end': .2, 'word_index': 0}, {'phone': 'AY', 'start': .2, 'end': .5, 'word_index': 0}],
        'validation': {'transcript_match': True, 'mean_word_probability': .9, 'min_word_probability': .8, 'max_boundary_delta_seconds': .1}}


def test_explicit_contractions_share_boundaries_and_other_text_mismatches_fail():
    recognized = [{'word': "harbor's", 'start': .2, 'end': .8, 'probability': .9},
                  {'word': 'safe', 'start': .8, 'end': 1., 'probability': .9}]
    groups = match_words(['harbor', 'is', 'safe'], recognized, lambda a, b: a == b)
    assert groups[0]['expected_indices'] == [0, 1] and groups[0]['start'] == .2 and groups[0]['end'] == .8
    assert match_words(['harbor', 'guard', 'safe'], recognized, lambda a, b: a == b) is None
    split = [{'word': w, 'start': i * .2, 'end': (i + 1) * .2, 'probability': .9} for i, w in enumerate(['do', 'not', 'go'])]
    assert match_words(["don't", 'go'], split, lambda a, b: a == b)[0]['recognized_indices'] == [0, 1]


def test_alignment_rejects_unverified_nonfinite_overlapping_and_outside_audio_intervals():
    assert alignment.validate(valid(), 1.)['status'] == 'aligned'
    mutations = [lambda r: r['words'][0].update(start=-.1), lambda r: r['words'][0].update(end=2),
        lambda r: r['phonemes'][0].update(end=.3), lambda r: r['phonemes'][0].update(phone='guessed'),
        lambda r: r['phonemes'][1].update(end=.4), lambda r: r['validation'].update(transcript_match=False),
        lambda r: r['validation'].update(mean_word_probability=float('nan')), lambda r: r['validation'].update(max_boundary_delta_seconds=float('inf'))]
    for mutate in mutations:
        result = valid(); mutate(result)
        with pytest.raises(ValueError): alignment.validate(result, 1.)
    with pytest.raises(ValueError): alignment.clean({'fps': 500})
    with pytest.raises(ValueError): alignment.clean({'phoneme_overrides': {'bad': ['GUESS']}})


def test_mapping_and_transition_frames_cover_exact_audio_and_close_at_end():
    c = alignment.clean({'enabled': True, 'fps': 60, 'blend_ms': 50})
    track = alignment.visemes(valid()['phonemes'], 1., c)
    alignment.validate_visemes(track, 1.)
    assert track['frames'][0]['time'] == 0 and track['frames'][-1] == {'time': 1., 'weights': {'sil': 1.}}
    assert track['cues'][0]['value'] == 'sil' and track['cues'][1]['value'] == 'aa'
    assert all(abs(sum(f['weights'].values()) - 1) < .0001 for f in track['frames'])
    changed = copy.deepcopy(c); changed['mapping']['HH'] = 'FF'
    assert alignment.visemes(valid()['phonemes'], 1., changed)['cues'][1]['value'] == 'FF'
    assert alignment.signature(changed) != alignment.signature(c)
    assert alignment.signature({'fps': 120}) == alignment.signature({})


def test_disabled_missing_runtime_and_stale_alignment_export_explicit_fallback():
    data = audio.write_wav(.2 * np.sin(np.arange(16000) * 2 * np.pi * 220 / 16000), 16000)
    out = alignment.process(data, 'hello')
    assert out['status'] == 'amplitude_fallback' and out['audio_sha256'] == hashlib.sha256(data).hexdigest()
    missing = alignment.process(data, 'hello', settings={'enabled': True}, cfg={'voice': {'alignment': {'python': 'missing.exe'}}})
    assert missing['status'] == 'amplitude_fallback' and 'Install' in missing['reason']
    stored = {**valid(), 'audio_sha256': out['audio_sha256'], 'config_signature': alignment.signature({'enabled': True}),
              'text_sha256': hashlib.sha256(b'hi').hexdigest(), 'visemes': alignment.visemes(valid()['phonemes'], 1., {'enabled': True})}
    v = {'studio': {'lip_sync': {'enabled': True}}}
    assert alignment.for_delivery(data, {'lip_sync': stored, 'text': 'hi'}, v)['status'] == 'aligned'
    v['studio']['lip_sync']['fps'] = 120
    assert alignment.is_stale({'lip_sync': stored}, v)
    assert alignment.for_delivery(data, {'lip_sync': stored}, v)['status'] == 'amplitude_fallback'
    # Configuring lip animation cannot make the actual speech audio outdated.
    voice = {'engine': 'qwen', 'mode': 'design', 'description': 'A narrator.', 'studio': {}}
    before = authoring.prepare(voice, 'Hello')[2]['render_signature']
    voice['studio']['lip_sync'] = alignment.clean({'enabled': True})
    assert authoring.prepare(voice, 'Hello')[2]['render_signature'] == before


def test_preview_snapshots_delivered_audio_without_overwriting_voice_or_line(tmp_path):
    lib = Library(tmp_path / 'library')
    npc = lib.create({'profile': {'first_name': 'Timing', 'last_name': 'QA', 'age': 30}, 'assets': {}, 'voice': {},
        'voice_lines': {'hello': {'file': 'voice/hello.wav', 'text': 'Hello'}}})
    original = audio.write_wav(np.zeros(16000), 16000)
    lib.write_asset(npc['id'], 'voice/hello.wav', original)
    before = lib.get(npc['id'])
    result = alignment.analyze_source(lib, before, 'line:hello', settings={'enabled': False})
    assert lib.get(npc['id']) == before
    snapshot = result['audio_url'].split('/files/', 1)[1]
    lib.write_asset(npc['id'], 'voice/hello.wav', audio.write_wav(np.ones(16000) * .1, 16000))
    assert lib.read_asset(npc['id'], snapshot) == original
    with pytest.raises(ValueError): alignment.analyze_source(lib, before, '../../npc.json')
