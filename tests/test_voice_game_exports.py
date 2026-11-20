"""Game artifacts preserve measured timing, final WAV identity and fallback semantics."""
import csv
import hashlib
import io
import json
import zipfile

import numpy as np
import pytest

from npcgen.voice import alignment, audio, game_exports
from tests.test_voice_alignment import valid


def test_curve_table_matches_every_checked_viseme_and_exact_endpoint():
    lip = valid()
    lip.update(status='aligned', seconds=1., audio_sha256='a' * 64, samples=16000, sample_rate=16000)
    lip['visemes'] = alignment.visemes(lip['phonemes'], 1., {'enabled': True, 'fps': 60})
    data = game_exports.clip(lip, [], 'lines/hello.wav')
    rows = list(csv.reader(io.StringIO(game_exports.curves(data))))
    assert data['method'] == 'visemes' and data['audio_sha256'] == lip['audio_sha256']
    assert [r[0] for r in rows[1:]] == alignment.VISEMES
    times = list(map(float, rows[0][1:]))
    assert len(times) == 61 and times[-1] == 1 and all(a < b for a, b in zip(times, times[1:]))
    for row in rows[1:]:
        assert list(map(float, row[1:])) == pytest.approx([f['weights'][row[0]] for f in data['frames']])
    assert data['frames'][-1]['weights']['sil'] == 1
    assert sum(data['frames'][-1]['weights'][g] for g in alignment.VISEMES if g != 'sil') == 0


def test_amplitude_export_is_explicit_and_game_helpers_are_in_package():
    wav = audio.write_wav(np.zeros(16480), 16000)
    lip = alignment.process(wav, 'hello', settings={'enabled': False})
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as archive:
        info = game_exports.write_clip(archive, 'actor/', 'hello', lip, [.2, .8], 'lines/hello.wav')
        game_exports.write_support(archive, 'actor/')
    with zipfile.ZipFile(io.BytesIO(out.getvalue())) as archive:
        data = json.loads(archive.read('actor/' + info['clip']))
        assert data['audio_sha256'] == hashlib.sha256(wav).hexdigest()
        assert data['method'] == 'amplitude_fallback' and data['reason']
        assert data['groups'] == ['sil', 'mouth_open']
        assert data['frames'][1]['weights'] == {'sil': pytest.approx(.2), 'mouth_open': .8}
        assert data['frames'][-1] == {'time': 1.03, 'weights': {'sil': 1., 'mouth_open': 0.}}
        assert len(data['frames']) == 27
        assert 'actor/game/godot/forge_lip_player.gd' in archive.namelist()
        assert 'actor/game/godot/forge_mesh_mouth.gd' in archive.namelist()
        assert 'actor/game/unreal/import_curves.py' in archive.namelist()
        assert json.loads(archive.read('actor/game/targets.json'))['targets']['mouth_open'] == 'jawOpen'
    for bad in ([float('nan')], [-.1], [True], [1.1]):
        with pytest.raises(ValueError): game_exports.clip(lip, bad, 'lines/hello.wav')
