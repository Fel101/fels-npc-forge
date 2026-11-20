"""Retained real NPC artwork + prior real speech, copied into an isolated visual QA library."""
import copy
import hashlib
import io
import json
import os
import math
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from npcgen.config import load
from npcgen.library import Library
from npcgen.scenes import Scenes, compile_timeline
from npcgen import scene_render
from npcgen.scene_export import make_zip
from npcgen.voice import alignment, character_rig, pack


def preview(lib,npc_id,rig,lip,mouth,audio_path,output):
    fps=lip.get('visemes',{}).get('fps',25) if lip.get('visemes') else 25
    width,height=rig['source']['size']; frames=math.ceil(lip['seconds']*fps)
    cache={}
    with output.with_suffix('.log').open('wb') as log:
        proc=subprocess.Popen(['ffmpeg','-n','-v','error','-f','rawvideo','-pix_fmt','rgba','-s',f'{width}x{height}',
            '-r',str(fps),'-i','pipe:0','-i',str(audio_path),'-map','0:v','-map','1:a','-c:v','libx264','-preset','fast',
            '-pix_fmt','yuv420p','-c:a','aac','-shortest',str(output)],stdin=subprocess.PIPE,stderr=log)
        try:
            for index in range(frames):
                weights=character_rig.weights_at(lip,mouth,index/fps)
                proc.stdin.write(character_rig.image(lib,npc_id,rig,weights,cache).tobytes())
            proc.stdin.close(); assert proc.wait(timeout=30)==0
        finally:
            if proc.poll() is None:proc.terminate();proc.wait(timeout=10)
    return {'file':str(output),'frames':frames,'fps':fps,'source_audio_sha256':lip['audio_sha256']}


def main():
    source=ROOT/'local/voice-alignment-real-20261012-180714-874a83'
    prior=json.loads((source/'report.json').read_text())
    production=Library(load()['library_path'])
    candidates=[]
    for path in production.root.glob('*/npc.json'):
        npc=json.loads(path.read_text()); assets=npc.get('assets') or {}
        if not assets.get('portrait') or not assets.get('living'):continue
        try:
            manifest=json.loads(production.read_asset(npc['id'],'living/living.json'))
            data=production.read_asset(npc['id'],assets['portrait']['file'])
            if hashlib.sha256(data).hexdigest()==manifest['portrait']['sha256'] and 'talking' in manifest['frames']:
                production.read_asset(npc['id'],'living/talking.png'); candidates.append(npc)
        except (KeyError,OSError,ValueError):continue
        if len(candidates)==2:break
    assert len(candidates)==2,'Need two existing living portraits for real visual QA'
    root=ROOT/'local'/('voice-character-rig-real-'+time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:6]);root.mkdir(parents=True)
    lib=Library(root/'library'); before={}
    for path in source.joinpath('library').rglob('*'):
        if path.is_file() and path.suffix in ('.json','.wav'):before[str(path)]=hashlib.sha256(path.read_bytes()).hexdigest()
    report={'status':'running','root':str(root),'actors':[],'generation':'None; existing NPC artwork and real speech reused',
        'visual_limits':'Living talk/rest images are a two-frame bridge, not fifteen independently authored phonetic poses; timings remain model estimates'}
    try:
        for index,(clip,art) in enumerate(zip(prior['clips'],candidates)):
            npc_id=clip['npc_id'];shutil.copytree(source/'library'/npc_id,lib.root/npc_id)
            files=[art['assets']['portrait']['file'],'living/living.json','living/talking.png']
            for file in files:
                original=production.resolve(art['id'],file)
                before[str(original)]=hashlib.sha256(original.read_bytes()).hexdigest()
                lib.write_asset(npc_id,file,original.read_bytes())
            original=production.resolve(art['id'],'npc.json');before[str(original)]=hashlib.sha256(original.read_bytes()).hexdigest()
            def apply(npc):
                npc['assets']={'portrait':copy.deepcopy(art['assets']['portrait']),'living':copy.deepcopy(art['assets']['living'])}
                npc['profile']=copy.deepcopy(art['profile']);npc['style']=art.get('style','render3d')
                npc['voice'].setdefault('studio',{})['lip_sync']=copy.deepcopy(clip['lip_sync']['settings'])
            npc=lib.update(npc_id,apply)
            voice=copy.deepcopy(npc['voice']);audio_before=lib.read_asset(npc_id,npc['voice_lines']['greeting_1']['file'])
            saved=character_rig.save(lib,npc_id,'portrait',{'enabled':True})
            rig=character_rig.describe(lib,saved,'portrait');assert rig['status']=='ready' and 'talking' in rig['frames']
            assert saved['voice']==voice and lib.read_asset(npc_id,npc['voice_lines']['greeting_1']['file'])==audio_before
            meta=saved['voice_lines']['greeting_1'];lip=alignment.for_delivery(audio_before,meta,saved['voice'])
            assert lip['status']=='aligned'
            cache={};rest=character_rig.image(lib,npc_id,rig,{'sil':1},cache);opened=character_rig.image(lib,npc_id,rig,{'aa':1},cache)
            assert rest.tobytes()!=opened.tobytes()
            rest.save(root/f'actor-{index+1}-rest.png');opened.save(root/f'actor-{index+1}-open.png')
            video=preview(lib,npc_id,rig,lip,meta['mouth'],lib.resolve(npc_id,meta['file']),root/f'actor-{index+1}-character-preview.mp4')
            blob,stem=pack.build(lib,saved);(root/f'actor-{index+1}-voice-pack.zip').write_bytes(blob)
            with zipfile.ZipFile(io.BytesIO(blob)) as archive:
                manifest=json.loads(archive.read(stem+'/lines.json'))
                packed=json.loads(archive.read(stem+'/'+manifest['character_rigs']['portrait']))
                assert packed['mapping']==rig['mapping']
                for frame in packed['frames'].values():assert hashlib.sha256(archive.read(stem+'/'+frame['file'])).hexdigest()==frame['sha256']
                archive.extractall(root/f'actor-{index+1}-game')
            report['actors'].append({'npc_id':npc_id,'art_source_id':art['id'],'rig_version':rig['version'],'frame_count':len(rig['frames']),
                'audio_sha256':lip['audio_sha256'],'preview':video,'lip_sync_status':lip['status']})
        shutil.copytree(source/'library/.npcforge-scenes',lib.root/'.npcforge-scenes',dirs_exist_ok=True)
        scenes=Scenes(lib);scene=scenes.get(prior['scene']['id']);timeline=compile_timeline(scene,lib,scenes)
        assert all(beat['voice']['visual_method']=='viseme_frames' for beat in timeline['beats'] if beat.get('voice'))
        filename=scene_render.render(scene,scenes,timeline=timeline)
        scenes.set_render(scene['id'],scene['revision'],filename,timeline['fingerprint'])
        archive=make_zip(scenes.get(scene['id']),scenes,'Copied NPC mouth artwork; two-frame bridge; checked model timing estimates.')
        (root/'scene-package.zip').write_bytes(archive)
        maker=scene_render.FrameMaker(scene,timeline,lib);maker.frame(0).save(root/'scene-rest.png');maker.frame(5).save(root/'scene-speaking.png')
        changed=character_rig.save(lib,report['actors'][0]['npc_id'],'portrait',{'enabled':False})
        updated=compile_timeline(scene,lib,scenes)
        assert updated['fingerprint']!=timeline['fingerprint'] and all(b.get('voice') for b in updated['beats'])
        character_rig.restore(lib,report['actors'][0]['npc_id'],'portrait',report['actors'][0]['rig_version'])
        assert compile_timeline(scene,lib,scenes)['fingerprint']==timeline['fingerprint']
        report['scene']={'id':scene['id'],'video':str(scenes.resolve(scene['id'],filename)),'timeline':timeline,
            'rig_edit_only_invalidates_video':True,'pinned_audio_preserved':True}
        game=root/'actor-1-game'/next((root/'actor-1-game').iterdir()).name
        (root/'project.godot').write_text('[application]\nconfig/name="Forge character frames QA"\n[rendering]\nrenderer/rendering_method="gl_compatibility"\n')
        gd='''extends SceneTree
func _initialize() -> void:
    var helper = load("HELPER").new()
    var sprite := AnimatedSprite2D.new()
    var sound := AudioStreamPlayer.new()
    root.add_child(sprite);root.add_child(sound)
    helper.mouth_sprite = sprite;helper.audio_player = sound;root.add_child(helper)
    assert(helper.load_sprite_rig("RIG", "PACK") == OK)
    assert(sprite.sprite_frames.get_frame_count("mouth") == 2)
    assert(helper.load_clip("CLIP", "AUDIO") == OK)
    helper.apply_time(1.02)
    assert(sprite.frame == helper.sprite_frames["oh"])
    var before = helper.clip.frames[0].weights
    helper.clip.frames[0].weights = {"sil":0.4,"aa":0.3,"E":0.3}
    helper.apply_time(0)
    assert(sprite.frame == helper.sprite_frames["aa"])
    helper.clip.frames[0].weights = before
    helper.stop()
    assert(sprite.frame == helper.sprite_frames["sil"])
    var output := FileAccess.open("res://godot-character-report.json",FileAccess.WRITE)
    output.store_string(JSON.stringify({"status":"passed","frames":2,"alias_weights_combined":true,"source":"actual exported NPC PNG frames"}))
    output.close();quit()
'''
        for key,value in {'HELPER':game/'game/godot/forge_lip_player.gd','RIG':game/'game/character-rigs/portrait/rig.json',
            'PACK':game,'CLIP':game/'game/clips/greeting_1.json','AUDIO':game/'lines/greeting_1.wav'}.items():gd=gd.replace(key,str(value).replace('\\','/'))
        (root/'godot-character-check.gd').write_text(gd)
        godot=Path(os.environ.get('NPCGEN_GODOT') or shutil.which('godot') or 'godot')   # a Godot 4 console executable: set NPCGEN_GODOT to its path
        with (root/'godot-character.log').open('w') as log:
            result=subprocess.run([str(godot),'--headless','--path',str(root),'--script',str(root/'godot-character-check.gd')],stdout=log,stderr=subprocess.STDOUT,timeout=120)
        assert result.returncode==0
        report['godot_character_frames']=json.loads((root/'godot-character-report.json').read_text())
        assert all(hashlib.sha256(Path(file).read_bytes()).hexdigest()==digest for file,digest in before.items())
        report['source_artwork_records_audio_unchanged']=True;report['status']='passed'
    except Exception as error:
        report['status']='failed';report['error']=str(error);raise
    finally:
        (root/'report.json').write_text(json.dumps(report,indent=2));print('Report: '+str(root/'report.json'),flush=True)


if __name__=='__main__':main()
