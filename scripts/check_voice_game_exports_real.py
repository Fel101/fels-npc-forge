"""Verify exported real speech in isolated installed Godot/Unreal projects (no TTS)."""
import argparse
import copy
import datetime
import hashlib
import io
import json
import shutil
import os
from pathlib import Path
import subprocess
import sys
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from npcgen.library import Library
from npcgen.scenes import Scenes
from npcgen.scene_export import make_zip
from npcgen.voice import pack

GODOT = Path(os.environ.get('NPCGEN_GODOT') or shutil.which('godot') or 'godot')   # a Godot 4 console executable: set NPCGEN_GODOT to its path
UNREAL = Path(r'C:\Program Files\Epic Games\UE_5.8\Engine\Binaries\Win64\UnrealEditor-Cmd.exe')

GODOT_CHECK = '''extends SceneTree
var failed := false
func check(value: bool, message: String) -> void:
    if not value:
        failed = true
        push_error(message)
func _initialize() -> void:
    call_deferred("run")
func run() -> void:
    var cases = JSON.parse_string(FileAccess.get_file_as_string("res://cases.json"))
    var results: Array = []
    for item in cases:
        var player_script = load(item.script)
        var helper = player_script.new()
        var sound := AudioStreamPlayer.new()
        root.add_child(sound)
        helper.audio_player = sound
        var mesh := ArrayMesh.new()
        var shape_names: Array = []
        for name in helper.targets.values():
            if not str(name).is_empty() and not shape_names.has(name):
                shape_names.append(name)
                mesh.add_blend_shape(name)
        var arrays: Array = []
        arrays.resize(Mesh.ARRAY_MAX)
        arrays[Mesh.ARRAY_VERTEX] = PackedVector3Array([Vector3.ZERO, Vector3.RIGHT, Vector3.UP])
        var blends: Array[Array] = []
        for _name in shape_names:
            blends.append(arrays.duplicate())
        mesh.add_surface_from_arrays(Mesh.PRIMITIVE_TRIANGLES, arrays, blends)
        var face := MeshInstance3D.new()
        face.mesh = mesh
        root.add_child(face)
        helper.face_mesh = face
        var sprite := AnimatedSprite2D.new()
        sprite.sprite_frames = SpriteFrames.new()
        sprite.sprite_frames.add_animation("mouth")
        for index in range(15):
            var image := Image.create(8, 8, false, Image.FORMAT_RGBA8)
            image.fill(Color(float(index) / 15, 0.5, 0.5))
            sprite.sprite_frames.add_frame("mouth", ImageTexture.create_from_image(image))
        root.add_child(sprite)
        helper.mouth_sprite = sprite
        root.add_child(helper)
        check(helper.load_clip(item.clip, item.audio) == OK, "load checked real WAV")
        var data = helper.clip
        for frame in data.frames:
            var time: float = frame.time
            helper.apply_time(time)
            var weights = helper.weights_at(time)
            for group in data.groups:
                check(absf(float(weights.get(group, 0)) - float(frame.weights[group])) < 0.00001, "weights at exported sample")
                var target: String = helper.targets[group]
                if not target.is_empty():
                    var index := face.find_blend_shape_by_name(target)
                    check(absf(face.get_blend_shape_value(index) - float(frame.weights[group])) < 0.00001, "mesh shape applied")
            var dominant: String = "sil"
            for group in weights:
                if float(weights[group]) > float(weights.get(dominant, 0)):
                    dominant = group
            check(sprite.frame == int(helper.sprite_frames[dominant]), "sprite frame applied")
        helper.play()
        await create_timer(0.12).timeout
        var clock: float = helper._last_time
        check(sound.playing and clock > 0, "real audio playback clock advances")
        var seek_to: float = minf(1.02, float(data.seconds) / 2)
        helper.seek(seek_to)
        check(absf(helper._last_time - seek_to) < 0.00001, "seek uses absolute clip seconds")
        sound.stream_paused = true
        var held: float = helper._last_time
        var held_shapes: Array = []
        for index in range(face.get_blend_shape_count()):
            held_shapes.append(face.get_blend_shape_value(index))
        var held_sprite: int = sprite.frame
        await create_timer(0.05).timeout
        check(helper._last_time == held, "pause holds mouth time")
        for index in range(face.get_blend_shape_count()):
            check(face.get_blend_shape_value(index) == held_shapes[index], "pause holds shape weights")
        check(sprite.frame == held_sprite, "pause holds sprite frame")
        helper.stop()
        for index in range(face.get_blend_shape_count()):
            check(face.get_blend_shape_value(index) == 0, "stop clears every shape")
        check(sprite.frame == 0, "stop restores rest sprite")
        var bytes = FileAccess.get_file_as_bytes(item.audio)
        bytes[bytes.size() - 1] ^= 1
        var corrupt := FileAccess.open("user://corrupt.wav", FileAccess.WRITE)
        corrupt.store_buffer(bytes)
        corrupt.close()
        check(helper.load_clip(item.clip, "user://corrupt.wav") == ERR_FILE_CORRUPT, "hash mismatch refused")
        results.append({"clip": item.clip, "samples_checked": data.frames.size(), "audio_clock": clock,
            "mesh_targets": shape_names.size(), "sprite_frames": 15, "pause_seek_stop": true})
        helper.queue_free()
        sound.queue_free()
        face.queue_free()
        sprite.queue_free()
    var output := FileAccess.open("res://godot-report.json", FileAccess.WRITE)
    output.store_string(JSON.stringify({"status": "failed" if failed else "passed", "clips": results}, "  "))
    output.close()
    quit(1 if failed else 0)
'''

UE_CHECK = '''import importlib.util, json, pathlib, traceback, unreal
root = pathlib.Path(__file__).resolve().parent
report = {'status': 'failed', 'engine': unreal.SystemLibrary.get_engine_version(), 'clips': []}
try:
    for i, case in enumerate(json.loads((root/'cases.json').read_text())):
        spec = importlib.util.spec_from_file_location('forge_import_'+str(i), case['importer'])
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        table, data = module.import_clip(case['clip'], case['pack_root'], '/Game/ForgeVoices', 'Clip_'+str(i))
        audio_task = unreal.AssetImportTask()
        for key, value in {'filename':str(pathlib.Path(case['pack_root'])/data['audio_file']),
            'destination_path':'/Game/ForgeVoices','destination_name':'Audio_'+str(i),
            'automated':True,'replace_existing':False,'save':True}.items():
            audio_task.set_editor_property(key,value)
        unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([audio_task])
        sounds = audio_task.get_objects()
        assert len(sounds)==1 and isinstance(sounds[0],unreal.SoundWave)
        duration = sounds[0].get_editor_property('duration')
        assert abs(duration-data['seconds']) < 1/data['sample_rate']
        samples = 0
        for frame in data['frames']:
            for group, expected in frame['weights'].items():
                result, value = unreal.DataTableFunctionLibrary.evaluate_curve_table_row(table, group, frame['time'], 'Forge QA')
                assert result == unreal.EvaluateCurveTableResult.ROW_FOUND, str(result)
                assert abs(value-expected) < .00001, (group,frame['time'],value,expected)
                samples += 1
        for first, second in zip(data['frames'], data['frames'][1:]):
            at = (first['time']+second['time'])/2
            for group, expected in first['weights'].items():
                result, value = unreal.DataTableFunctionLibrary.evaluate_curve_table_row(table, group, at, 'Forge QA constant')
                assert abs(value-expected) < .00001, (group,at,value,expected)
        try: module.import_clip(case['clip'], case['pack_root'], '/Game/ForgeVoices', 'Clip_'+str(i))
        except ValueError: pass
        else: raise AssertionError('must refuse overwriting imported asset')
        report['clips'].append({'asset': table.get_path_name(), 'groups': len(data['groups']),
            'samples_checked': samples, 'method': data['method'], 'constant_between_samples': True,
            'sound_wave':sounds[0].get_path_name(),'sound_seconds':duration})
    report['status'] = 'passed'
except Exception:
    report['error'] = traceback.format_exc()
finally:
    (root/'unreal-report.json').write_text(json.dumps(report,indent=2))
assert report['status']=='passed', report.get('error')
'''


def run():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, default=ROOT/'local/voice-alignment-real-20261012-180714-874a83/report.json')
    args = parser.parse_args()
    source = json.loads(args.source.read_text())
    lib = Library(args.source.parent/'library')
    out = ROOT/'local'/('voice-game-export-real-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:6])
    out.mkdir(parents=True)
    cases, ue_cases, source_hashes = [], [], {}
    source_before = {str(file): hashlib.sha256(file.read_bytes()).hexdigest()
        for file in (args.source.parent/'library').rglob('*') if file.is_file() and file.suffix in ('.json','.wav')}
    def add_package(data, stem, folder, entries):
        archive = zipfile.ZipFile(io.BytesIO(data))
        archive.extractall(out/'packs'/folder)
        (out/f'{folder}.zip').write_bytes(data)
        base = out/'packs'/folder/stem
        for line in entries:
            clip = base/line['game_playback']['clip']
            audio = base/line['file']
            source_hashes[str(audio)] = hashlib.sha256(audio.read_bytes()).hexdigest()
            cases.append({'clip': str(clip).replace('\\','/'), 'audio': str(audio).replace('\\','/'),
                          'script': str(base/'game/godot/forge_lip_player.gd').replace('\\','/')})
            ue_cases.append({'clip': str(clip), 'pack_root': str(base), 'importer': str(base/'game/unreal/import_curves.py')})
    for index, record in enumerate(source['clips']):
        npc = copy.deepcopy(lib.get(record['npc_id']))
        # Match this verified clip's recorded settings, without modifying the source NPC.
        npc['voice'].setdefault('studio', {})['lip_sync'] = record['lip_sync']['settings']
        data, stem = pack.build(lib, npc)
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = json.loads(archive.read(stem+'/lines.json'))['lines']
        add_package(data, stem, f'actor-{index+1}', entries)
        if index == 0:
            npc['voice']['studio']['lip_sync'] = {'enabled':False}
            data, stem = pack.build(lib,npc)
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                entries = json.loads(archive.read(stem+'/lines.json'))['lines']
                assert entries[0]['game_playback']['method']=='amplitude_fallback'
            add_package(data,stem,'amplitude-fallback',entries)
    scenes = Scenes(lib)
    scene_data = make_zip(scenes.get(source['scene']['id']), scenes, 'Checked game export; character scene video is amplitude fallback.')
    with zipfile.ZipFile(io.BytesIO(scene_data)) as archive:
        entries = json.loads(archive.read('sources.json'))['voices']
    add_package(scene_data, '', 'scene', entries)
    (out/'project.godot').write_text('[application]\nconfig/name="Forge lip QA"\n[rendering]\nrenderer/rendering_method="gl_compatibility"\n')
    (out/'cases.json').write_text(json.dumps(cases))
    (out/'check.gd').write_text(GODOT_CHECK)
    with (out/'godot.log').open('w') as log:
        result = subprocess.run([str(GODOT), '--headless', '--audio-driver', 'Dummy', '--path', str(out), '--script', str(out/'check.gd')], stdout=log, stderr=subprocess.STDOUT, timeout=120)
    godot = json.loads((out/'godot-report.json').read_text()) if (out/'godot-report.json').exists() else {'status':'failed','exit':result.returncode}
    ue = out/'unreal'; ue.mkdir()
    (ue/'ForgeLipQA.uproject').write_text(json.dumps({'FileVersion':3,'EngineAssociation':'5.8',
        'Plugins':[{'Name':'PythonScriptPlugin','Enabled':True},{'Name':'EditorScriptingUtilities','Enabled':True}]}))
    (ue/'cases.json').write_text(json.dumps(ue_cases))
    (ue/'check.py').write_text(UE_CHECK)
    with (ue/'launch.log').open('w') as log:
        result = subprocess.run([str(UNREAL), str(ue/'ForgeLipQA.uproject'), '-Unattended', '-NullRHI', '-NoSound',
            '-NoSplash', '-run=pythonscript', '-script='+str(ue/'check.py'), '-stdout', '-FullStdOutLogOutput'], stdout=log, stderr=subprocess.STDOUT, timeout=300)
    unreal_report = json.loads((ue/'unreal-report.json').read_text()) if (ue/'unreal-report.json').exists() else {'status':'failed','exit':result.returncode}
    unchanged = all(hashlib.sha256(Path(file).read_bytes()).hexdigest()==value for file,value in source_hashes.items())
    sources_preserved = all(hashlib.sha256(Path(file).read_bytes()).hexdigest()==value for file,value in source_before.items())
    report = {'status':'passed' if godot['status']==unreal_report['status']=='passed' and unchanged and sources_preserved else 'failed',
        'source_report':str(args.source),'generation':'none; retained real TTS/alignment reused',
        'godot':godot,'unreal':unreal_report,'delivered_export_audio_unchanged':unchanged,
        'source_records_and_audio_unchanged':sources_preserved,
        'character_scope':'Synthetic shape-key mesh and sprite atlas prove application API; human character rigs still need authoring/visual QA'}
    (out/'report.json').write_text(json.dumps(report,indent=2))
    print(json.dumps({'report':str(out/'report.json'),'status':report['status'],'godot':godot['status'],'unreal':unreal_report['status']}),flush=True)
    return 0 if report['status']=='passed' else 1


if __name__ == '__main__':
    raise SystemExit(run())
