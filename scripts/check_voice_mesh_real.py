"""Read retained real speech/GLB in an isolated QA library; no TTS or production edits."""
import argparse
import hashlib
import io
import json
import shutil
import os
from pathlib import Path
import struct
import subprocess
import sys
import zipfile
import wave
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from npcgen.library import Library
from npcgen.scenes import Scenes, compile_timeline
from npcgen.voice import mesh_rig, pack
from npcgen import scene_render
from npcgen.scene_export import make_zip

GODOT=Path(os.environ.get('NPCGEN_GODOT') or shutil.which('godot') or 'godot')   # a Godot 4 console executable: set NPCGEN_GODOT to its path

CHECK='''extends SceneTree
var failed := false
func check(value: bool, message: String) -> void:
    if not value:
        failed = true
        push_error(message)
func find_skeleton(node: Node) -> Skeleton3D:
    if node is Skeleton3D:
        return node
    for child in node.get_children():
        var found := find_skeleton(child)
        if found:
            return found
    return null
func _initialize() -> void:
    call_deferred("run")
func run() -> void:
    var paths = JSON.parse_string(FileAccess.get_file_as_string("res://paths.json"))
    var document := GLTFDocument.new()
    var state := GLTFState.new()
    check(document.append_from_file(paths.model, state) == OK, "load actual exported GLB")
    var model := document.generate_scene(state)
    root.add_child(model)
    var skeleton := find_skeleton(model)
    check(skeleton != null, "actual model skeleton")
    var mouth = load(paths.mesh_helper).new()
    mouth.model_root = model
    skeleton.add_child(mouth)
    await process_frame
    var bound: int = mouth.load_rig(paths.rig, paths.model)
    check(bound == OK, "bind checked artist mapping; Error=" + str(bound))
    if bound != OK:
        quit(1)
        return
    var sound := AudioStreamPlayer.new()
    root.add_child(sound)
    var helper = load(paths.lip_helper).new()
    helper.audio_player = sound
    root.add_child(helper)
    helper.weights_changed.connect(mouth.set_weights)
    check(helper.load_clip(paths.clip, paths.audio) == OK, "load exact real delivered WAV")
    var jaw: int = skeleton.find_bone("jaw")
    var baseline := skeleton.get_bone_pose_rotation(jaw)
    var frames: int = 0
    for sample in helper.clip.frames:
        skeleton.set_bone_pose_rotation(jaw, baseline)
        helper.apply_time(sample.time)
        mouth._process_modification()
        var amount: float = 0
        for group in sample.weights:
            amount += float(sample.weights[group]) * float(mouth.config.jaw_amounts.get(group, 0))
        var expected := baseline * Quaternion(Vector3.RIGHT, clampf(amount, 0, 1) * float(mouth.config.jaw_radians))
        check(skeleton.get_bone_pose_rotation(jaw).is_equal_approx(expected), "real jaw follows stored sample")
        frames += 1
    helper.seek(1.02)
    check(helper.weights_at(1.02).get("oh", 0) > 0, "absolute audio seek")
    helper.play(1.0)
    await create_timer(0.2).timeout
    check(sound.get_playback_position() > 1.05, "real WAV audio clock advances")
    sound.stream_paused = true
    var paused: Dictionary = mouth.weights.duplicate()
    await create_timer(0.15).timeout
    check(mouth.weights == paused, "pause holds mouth weights")
    helper.stop()
    skeleton.set_bone_pose_rotation(jaw, baseline)
    mouth._process_modification()
    check(skeleton.get_bone_pose_rotation(jaw).is_equal_approx(baseline), "stop returns actual jaw to neutral")
    var report = {"status":"passed" if not failed else "failed", "samples":frames, "bone":"jaw", "real_glb":true,
        "audio_clock":true,"seek":true,"pause":true,"stop_rest":true,"bones":skeleton.get_bone_count()}
    FileAccess.open("res://godot-result.json", FileAccess.WRITE).store_string(JSON.stringify(report))
    model.queue_free();helper.queue_free();sound.queue_free()
    quit(1 if failed else 0)
'''


def weighted_vertices(data):
    """Count actual nonzero jaw skin weights, rather than trusting a joint name."""
    length=struct.unpack_from('<I',data,12)[0]
    doc=json.loads(data[20:20+length]);offset=20+length
    binary=data[offset+8:offset+8+struct.unpack_from('<I',data,offset)[0]]
    def accessor(index):
        item=doc['accessors'][index];view=doc['bufferViews'][item['bufferView']]
        formats={5121:'B',5123:'H',5126:'f'};fmt=formats[item['componentType']]
        size=struct.calcsize('<'+fmt*4);start=view.get('byteOffset',0)+item.get('byteOffset',0)
        stride=view.get('byteStride',size)
        return [struct.unpack_from('<'+fmt*4,binary,start+i*stride) for i in range(item['count'])]
    count=0
    for node in doc['nodes']:
        if 'skin' not in node or 'mesh' not in node:continue
        joints=doc['skins'][node['skin']]['joints']
        jaw={i for i,n in enumerate(joints) if doc['nodes'][n].get('name','').lower()=='jaw'}
        for primitive in doc['meshes'][node['mesh']]['primitives']:
            attributes=primitive['attributes']
            if 'JOINTS_0' not in attributes or 'WEIGHTS_0' not in attributes:continue
            count+=sum(any(j in jaw and w>0 for j,w in zip(js,ws)) for js,ws in zip(accessor(attributes['JOINTS_0']),accessor(attributes['WEIGHTS_0'])))
    return count


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--root',type=Path,required=True);args=parser.parse_args()
    root=args.root.resolve()
    assert root.is_relative_to(ROOT/'local'),'Use an isolated ignored local QA directory'
    lib=Library(root/'library');source=json.loads((root/'source.json').read_text())
    actors=[lib.get(p.parent.name) for p in sorted(lib.root.glob('*/npc.json'))]
    before={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for npc in actors for p in [lib.resolve(npc['id'],'npc.json'),lib.resolve(npc['id'],npc['assets']['rig_3d']['file']),lib.resolve(npc['id'],npc['voice_lines']['greeting_1']['file'])]}
    report={'status':'running','generation':'None; existing real speech and rigged GLB reused','source':source,'actors':[],
            'limits':'Jaw-only skin; no independent lip/tongue poses. Imported body differs from QA portrait/audio identity. Human phonetic and speaker quality review pending.'}
    try:
        for index,npc in enumerate(actors,1):
            rig=mesh_rig.describe(lib,npc);assert rig['status']=='ready'
            data=lib.read_asset(npc['id'],rig['source']['snapshot_file'])
            count=weighted_vertices(data);assert count>0
            blob,stem=pack.build(lib,npc);(root/f'actor-{index}-voice-pack.zip').write_bytes(blob)
            with zipfile.ZipFile(io.BytesIO(blob)) as archive:
                manifest=json.loads(archive.read(stem+'/lines.json'))
                desc=manifest['character_rigs']['mesh'];exported=json.loads(archive.read(stem+'/'+desc))
                assert hashlib.sha256(archive.read(stem+'/'+exported['model_file'])).hexdigest()==source['model_sha256']
                assert exported['config']==rig['config']
                if index==1:archive.extractall(root/'game')
            report['actors'].append({'npc_id':npc['id'],'rig_version':rig['version'],'jaw_weighted_vertices':count,'model_sha256':rig['source']['sha256']})
        scenes=Scenes(lib);scene=scenes.list()[0]
        if scene['mode']!='stage3d':
            scene=scenes.get(scene['id']);scene.update(mode='stage3d',background_prompt='Plain slate for mouth QA',title='3D aligned real speech QA')
            scene=scenes.update(scene['id'],scene)
        else:scene=scenes.get(scene['id'])
        timeline=compile_timeline(scene,lib,scenes)
        assert all(b['voice']['visual_method']=='viseme_mesh' for b in timeline['beats'] if b.get('voice'))
        report['scene']={'id':scene['id'],'timeline':timeline}
        if scene.get('render') and scene['render'].get('fingerprint')==timeline['fingerprint']:
            scene_blob=make_zip(scene,scenes,'Real copied jaw rig and retained speech; human perceptual review pending.')
            (root/'scene-package.zip').write_bytes(scene_blob)
            with zipfile.ZipFile(io.BytesIO(scene_blob)) as archive:
                sources=json.loads(archive.read('sources.json'));assert not sources['render_outdated']
                for member in sources['cast']:
                    descriptor=json.loads(archive.read(member['character_rigs']['mesh']))
                    assert hashlib.sha256(archive.read(descriptor['model_file'])).hexdigest()==descriptor['model_sha256']
                for voice in sources['voices']:
                    assert hashlib.sha256(archive.read(voice['file'])).hexdigest()==voice['game_playback']['audio_sha256']
            video=scenes.resolve(scene['id'],scene['render']['file'])
            probe=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-of','json',str(video)]))
            vs=next(s for s in probe['streams'] if s['codec_type']=='video')
            assert int(vs['nb_frames'])==timeline['frames']
            assert abs(float(vs['duration'])-timeline['frames']/timeline['fps'])<.001
            scene_render._audio_mix(scene,timeline,scenes,root/'expected-mix.wav')
            with wave.open(str(root/'expected-mix.wav')) as stream:expected=np.frombuffer(stream.readframes(stream.getnframes()),dtype='<i2').astype(float)
            decoded=np.frombuffer(subprocess.check_output(['ffmpeg','-v','error','-i',str(video),'-map','0:a:0','-f','s16le','-ac','1','-ar','48000','pipe:1']),dtype='<i2').astype(float)
            count=min(len(decoded),len(expected));correlation=float(np.corrcoef(expected[:count],decoded[:count])[0,1])
            assert correlation>.95,'Rendered audio does not match the planned beat times'
            subprocess.run(['ffmpeg','-v','error','-y','-ss','0.2','-i',str(video),'-frames:v','1',str(root/'scene-speaking.png')],check=True)
            report['scene'].update(video=str(video),package=str(root/'scene-package.zip'),video_frames=timeline['frames'],audio_zero_offset_correlation=correlation)
        base=root/'game'/next((root/'game').iterdir()).name
        manifest=json.loads((base/'lines.json').read_text());line=manifest['lines'][0]
        # Voice packs use a list of rows; find the game artifacts linked by this exact WAV.
        playback=line['game_playback'];rig_path=manifest['character_rigs']['mesh'];rig_data=json.loads((base/rig_path).read_text())
        paths={'model':str(base/rig_data['model_file']),'rig':str(base/rig_path),'clip':str(base/playback['clip']),
            'audio':str(base/line['file']),'mesh_helper':str(base/'game/godot/forge_mesh_mouth.gd'),'lip_helper':str(base/'game/godot/forge_lip_player.gd')}
        (root/'paths.json').write_text(json.dumps(paths))
        (root/'project.godot').write_text('[application]\nconfig/name="Forge real 3D mouth QA"\n[rendering]\nrenderer/rendering_method="gl_compatibility"\n')
        (root/'check.gd').write_text(CHECK)
        result=subprocess.run([str(GODOT),'--headless','--audio-driver','Dummy','--path',str(root),'--script',str(root/'check.gd'),'--quit-after','3600'],capture_output=True,text=True,timeout=90)
        (root/'godot.log').write_text(result.stdout+result.stderr)
        assert result.returncode==0,result.stderr
        report['godot']=json.loads((root/'godot-result.json').read_text());assert report['godot']['status']=='passed'
        assert all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==sha for p,sha in before.items()),'NPC/model/audio changed'
        report['source_qa_unchanged']=True;report['status']='passed'
    except Exception as error:
        report['status']='failed';report['error']=str(error);raise
    finally:(root/'report.json').write_text(json.dumps(report,indent=2))
    print(json.dumps({'status':report['status'],'root':str(root),'actors':report['actors'],'godot':report['godot'],'scene_id':report['scene']['id']}))

if __name__=='__main__':main()
