"""Real CPU DSP, preserved speech, delivery invalidation and pinned scene effects."""
import copy
import hashlib
import io
import json
from types import SimpleNamespace
import zipfile

import numpy as np
import pytest

from npcgen.library import Library
from npcgen.scenes import Scenes, compile_timeline
from npcgen import scene_voice
from npcgen.scene_export import make_zip
from npcgen.voice import alignment, audio, authoring, effects, lab_voice, pack, record, service, steps


def wave(hz=440, seconds=2, rate=24000):
    return audio.write_wav(.2 * np.sin(2 * np.pi * hz * np.arange(int(seconds * rate)) / rate), rate)


def test_effect_validation_and_effective_input_invalidation():
    for invalid in ({"pitch_semitones": float('nan')}, {"layer_mix": True}, {"ring_mod_hz": "65"},
                    {"lowpass_hz": 100, "highpass_hz": 200}, {"formants": "independent"}, {"unknown": 1}):
        with pytest.raises(ValueError):
            effects.clean(invalid)
    assert not effects.effective({"enabled": True, "layer_pitch_semitones": -12})
    v = {"engine": "qwen", "mode": "upload", "settings": {}, "reference": {"id": "a" * 12}, "studio": {}}
    dry = authoring.prepare(v, "Hello")[2]
    v['studio']['effects'] = {"enabled": True, "pitch_semitones": -5}
    wet = authoring.prepare(v, "Hello")[2]
    assert wet['render_signature'] != dry['render_signature']
    assert wet['synthesis_signature'] == dry['render_signature'] == dry['synthesis_signature']
    v['studio'] = authoring.clean_studio({"effects": effects.PRESETS['robot'], "presets": [
        {"id": "dry", "name": "Normal", "effects": effects.PRESETS['dry']}], 'line_performances': {'hello': 'dry'}})
    assert not authoring.prepare(v, "Hello", line_id='hello')[2]['effects']
    assert authoring.prepare(v, "Hello", line_id='other')[2]['effects']['ring_mod_hz'] == 65


@pytest.mark.skipif(not effects.capabilities()['available'], reason='Verified FFmpeg effect filters unavailable')
def test_real_pitch_robot_and_layer_filters_preserve_duration_and_source():
    source = wave()
    source_hash = hashlib.sha256(source).hexdigest()
    shifted, receipt = effects.process(source, {"enabled": True, "pitch_semitones": -12})
    y, rate = audio.read_wav(shifted)
    frequencies = np.fft.rfftfreq(len(y), 1 / rate)
    spectrum = np.abs(np.fft.rfft(y))
    assert abs(frequencies[spectrum.argmax()] - 220) < 2
    assert len(y) == 48000 and receipt['output_samples'] == receipt['source_samples']
    robot, _ = effects.process(source, {"enabled": True, "ring_mod_hz": 65})
    z, _ = audio.read_wav(robot)
    bins = np.abs(np.fft.rfft(z))
    assert bins[np.argmin(abs(frequencies - 375))] > bins[np.argmin(abs(frequencies - 440))] * 10
    layered, _ = effects.process(source, {"enabled": True, "layer_mix": .5, "layer_pitch_semitones": -12, 'layer_delay_ms': 20})
    spectrum = np.abs(np.fft.rfft(audio.read_wav(layered)[0]))
    # Formant-preserving resynthesis spreads the layer's sine energy over
    # neighboring bins; a distinct lower component must still be present.
    assert spectrum[(frequencies > 217) & (frequencies < 223)].max() > spectrum.max() * .05
    assert hashlib.sha256(source).hexdigest() == source_hash
    assert effects.process(source, {"enabled": False})[0] == source
    _, clipped_cutoff = effects.process(source, {'enabled': True, 'lowpass_hz': 20000})
    assert clipped_cutoff['settings']['lowpass_hz'] == 10800 and clipped_cutoff['requested_settings']['lowpass_hz'] == 20000


def test_missing_effect_backend_fails_clearly_but_dry_audio_remains_available(monkeypatch):
    monkeypatch.setattr(effects, 'capabilities', lambda: {'available': False, 'reason': 'missing runtime'})
    assert effects.process(wave(), {})[0] == wave()
    long_dry = wave(seconds=121)
    assert effects.process(long_dry, {})[0] == long_dry
    with pytest.raises(ValueError, match='unavailable'):
        effects.process(wave(), effects.PRESETS['robot'])


@pytest.fixture
def local(tmp_path, monkeypatch):
    lib = Library(tmp_path / 'library')
    npc = lib.create({'profile': {'first_name': 'Effect', 'last_name': 'QA', 'age': 30}, 'assets': {}, 'setting': 'fantasy'})
    wav = wave(seconds=6)
    def assign(rec):
        v = record.voice_of(rec)
        v.update(engine='qwen', mode='upload', speaker='qwen', studio={},
                 lab_voice={'identity_id': 'a' * 12, 'name': 'Effect QA', 'kind': 'cast',
                            'model': 'qwen3-tts-1.7b-base', 'takes_direction': True})
        record.install_reference(lib, rec, v, wav, {'id': 'a' * 12, 'text': 'A source reference.'})
        rec.update(voice=v, dialogue={'lines': {'greeting': [{'id': 'hello', 'text': 'The lantern is lit.'}]}})
    lib.update(npc['id'], assign)
    seen = []
    def generate(job, request):
        seen.append(request)
        return wav
    monkeypatch.setattr(lab_voice, 'speak', generate)
    jobs = SimpleNamespace(lib=lib, scenes=Scenes(lib))
    return lib, npc['id'], jobs, seen


@pytest.mark.skipif(not effects.capabilities()['available'], reason='Verified FFmpeg effect filters unavailable')
def test_effect_only_edit_reuses_verified_dry_master_and_exports_both_versions(local, monkeypatch):
    lib, npc_id, jobs, seen = local
    submitted = []
    original_align = alignment.process
    def capture(data, *args, **kwargs):
        submitted.append(hashlib.sha256(data).hexdigest())
        return original_align(data, *args, **kwargs)
    monkeypatch.setattr(alignment, 'process', capture)
    def generate():
        npc = lib.get(npc_id)
        opts = {'voice': service.lines_request(npc, {'ids': ['hello']})}
        steps.line(jobs, SimpleNamespace(options=opts, cancel=None), {'key': 'hello'}, npc)
        return lib.get(npc_id)
    npc = generate(); dry = copy.deepcopy(npc['voice_lines']['hello']); ref = copy.deepcopy(npc['voice']['reference'])
    original = effects.read_original(lib, npc_id, dry['original'])
    lib.update(npc_id, lambda rec: rec['voice']['studio'].update(effects=effects.PRESETS['creature']))
    assert record.line_is_stale(lib.get(npc_id), 'hello')
    npc = generate(); wet = npc['voice_lines']['hello']
    assert len(seen) == 1 and wet['original_reused'] and wet['seed'] == dry['seed']
    assert wet['model_identity'] == dry['model_identity'] and npc['voice']['reference'] == ref
    assert wet['postprocessing']['output_samples'] == dry['original']['samples']
    assert submitted[-1] == wet['postprocessing']['output_sha256'] != wet['original']['sha256']
    assert wet['lip_sync']['audio_sha256'] == submitted[-1]
    payload, _ = pack.build(lib, npc)
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        manifest = json.loads(archive.read(next(x for x in archive.namelist() if x.endswith('/lines.json'))))
        stem = next(x for x in archive.namelist() if x.endswith('/lines.json')).rsplit('/', 1)[0]
        assert archive.read(stem + '/' + manifest['lines'][0]['original']['file']) == original
        assert hashlib.sha256(archive.read(stem + '/lines/hello.wav')).hexdigest() == wet['postprocessing']['output_sha256']
    lib.update(npc_id, lambda rec: rec['voice']['studio'].update(effects=effects.PRESETS['dry']))
    npc = generate()
    assert len(seen) == 1 and lib.read_asset(npc_id, npc['voice_lines']['hello']['file']) == original
    # Pronunciation changes require new synthesis, rather than recycling wrong words.
    lib.update(npc_id, lambda rec: rec['voice']['studio'].update(pronunciations=[{'term': 'lantern', 'spoken': 'lamp'}]))
    generate()
    assert len(seen) == 2


@pytest.mark.skipif(not effects.capabilities()['available'], reason='Verified FFmpeg effect filters unavailable')
def test_scene_effects_are_frozen_and_preview_does_not_change_assignment(local):
    lib, npc_id, jobs, seen = local
    lib.update(npc_id, lambda rec: rec['voice']['studio'].update(effects=effects.PRESETS['robot']))
    npc = lib.get(npc_id)
    preview = effects.preview(lib, npc, 'reference', effects.PRESETS['creature'])
    assert lib.get(npc_id) == npc and lib.read_asset(npc_id, npc['voice']['reference']['file']) == effects.read_original(lib, npc_id, preview['original'])
    with pytest.raises(ValueError, match='Choose an existing'):
        effects.preview(lib, npc, '../../npc.json', {})
    other = lib.create({'profile': {'first_name': 'Other', 'last_name': 'QA', 'age': 30}, 'assets': {}, 'setting': 'fantasy'})
    scene = jobs.scenes.create({'title': 'Effect pin QA', 'cast': [npc_id, other['id']], 'voice_bindings': {npc_id: record.identity_id(npc['voice'])},
        'beats': [{'id': 'one', 'kind': 'line', 'speaker_id': npc_id, 'text': 'The forge is open.'}]})
    request = scene_voice.requests(scene, jobs.scenes, {})
    lib.update(npc_id, lambda rec: rec['voice']['studio'].update(effects=effects.PRESETS['dry']))
    scene_voice.run(jobs, SimpleNamespace(scene_id=scene['id'], options={'scene_voice': {'beats': request}}, cancel=None), {'key': 'one'})
    scene = jobs.scenes.get(scene['id']); meta = scene['audio']['one']
    assert meta['effects']['ring_mod_hz'] == 65 and compile_timeline(scene, lib, jobs.scenes)['beats'][0]['voice']
    jobs.scenes.write_media(scene['id'], 'render/0123456789.mp4', b'fixture video; real MP4 proof is separate')
    timeline = compile_timeline(scene, lib, jobs.scenes)
    jobs.scenes.set_render(scene['id'], scene['revision'], 'render/0123456789.mp4', timeline['fingerprint'])
    with zipfile.ZipFile(io.BytesIO(make_zip(jobs.scenes.get(scene['id']), jobs.scenes, 'QA'))) as archive:
        manifest = json.loads(archive.read('sources.json')); packed = manifest['voices'][0]
        assert archive.read(packed['file']) == jobs.scenes.resolve(scene['id'], meta['file']).read_bytes()
        assert hashlib.sha256(archive.read(packed['original']['file'])).hexdigest() == meta['original']['sha256']
    original = meta['original']; lib.write_asset(npc_id, original['file'], b'corrupted test fixture')
    with pytest.raises(ValueError, match='checksum'):
        effects.read_original(lib, npc_id, original)
