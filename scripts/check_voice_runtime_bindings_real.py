"""Installed offline adapters through the real queue in an isolated library.

Reads retained real reference audio; never installs, downloads, edits runtimes,
or writes production characters. Uses the normal ComfyUI/GPU/residency gates.
"""
import argparse
import copy
import hashlib
import io
import json
from pathlib import Path
import sys
import time
import uuid
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from npcgen import config,dialogue,scene_render
from npcgen.app import App
from npcgen.library import Library
from npcgen.scenes import compile_timeline
from npcgen.scene_export import make_zip
from npcgen.voice import audio,binding,pack,record,service
from check_voice_library_real import wait_job


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--engines',nargs='+',default=list(binding.PINS))
    parser.add_argument('--designs',action='store_true')
    args=parser.parse_args()
    prior=Library(ROOT/'local/voice-library-real-20261012-132540-da2158/library')
    source=prior.get('voiceqa1-actor-13087a');ref=source['voice']['reference']
    ref_audio=prior.read_asset(source['id'],ref['file'])
    source_files=[prior.resolve(source['id'],'npc.json'),prior.resolve(source['id'],ref['file'])]
    before={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in source_files}
    root=ROOT/'local'/('voice-runtime-real-'+time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:6]);root.mkdir()
    cfg=config.load();cfg['library_path']=root/'library'
    report={'status':'running','root':str(root),'source':before,'engines':[],
        'human_review':'Audio and model timing estimates retained; speaker quality, accent and phonetic accuracy not asserted'}
    print('ROOT '+str(root),flush=True)
    try:
        with App(cfg) as app:
            for engine in args.engines:
                print('START '+engine,flush=True)
                result={'engine':engine,'clips':[]};actors=[]
                for index in range(2):
                    npc=copy.deepcopy(source)
                    for key in ('id','created','updated','voice','voice_lines','assets','dialogue'):npc.pop(key,None)
                    npc['profile'].update(first_name=engine.replace('_','').title()+str(index+1),last_name='BindingQA')
                    npc=app.lib.create(npc);actors.append(npc['id'])
                    raw={'engine':engine,'speaker':None,'language':'English','settings':{},
                         'mode':'stock' if engine in ('kokoro','qwen_custom') else 'upload'}
                    if raw['mode']=='stock':raw['stock_voice']='af_heart' if engine=='kokoro' else 'Ryan'
                    else:raw['speaker']=engine
                    def install(rec):
                        v=record.clean_settings(raw,rec)
                        record.save_settings(rec,v,app.lib)
                        if raw['mode']!='stock':
                            v=record.voice_of(rec);record.install_reference(app.lib,rec,v,ref_audio,{**ref,'source':'retained-real-QA-reference'})
                            rec['voice']=v
                        if engine=='dramabox':
                            rec['voice']['studio']={'presets':[{'id':'drama-qa','name':'Quiet performance',
                                'direction':'Speak quietly and warmly.', 'parameters':{'cfg_scale':2.,'stg_scale':1.,'duration_multiplier':1.}}],
                                'default_performance':'drama-qa','lip_sync':{'enabled':True,'fps':60}}
                    app.lib.update(npc['id'],install)
                first=app.lib.get(actors[0])
                if engine=='kokoro':
                    request=service.takes_request(first,{'count':1},cfg)
                    job=app.jobs.submit(first,{'voice':request},{})
                    wait_job(app,job['id']);first=app.lib.get(first['id'])
                    first=record.select_take(app.lib,first['id'],first['voice']['takes'][-1]['id'])
                    app.jobs.voice.release()
                identity=app.voice_library.from_npc(first['id'],{'name':engine+' bound QA','tags':['runtime-qa',engine]})
                version=identity['versions'][0]
                app.voice_library.approve(version['id'],{'approved':True,'notes':'Isolated reuse of retained reference/model identity'})
                app.voice_library.assign(version['id'],actors)
                result.update(voice_id=identity['id'],version_id=version['id'])
                for index,npc_id in enumerate(actors):
                    def script(rec):
                        rec['dialogue']=dialogue.generate(rec)
                        rec['dialogue']['lines']['greeting'][0]['text']=['The forge gates are open.','Keep your lantern close.'][index]
                        rec['voice'].setdefault('studio',{})['lip_sync']={'enabled':True,'fps':60}
                    npc=app.lib.update(npc_id,script)
                    request=service.lines_request(npc,{'ids':['greeting_1']})
                    request['voice_snapshot']=record.freeze_voice(app.lib,npc_id,request['voice_snapshot'])
                    start=time.monotonic()
                    # Hold the actual queue's ComfyUI lock so worker start follows config mutation.
                    with app.comfy.operation_lock.hold():
                        job=app.jobs.submit(npc,{'voice':request},{})
                        frozen=copy.deepcopy(app.jobs.jobs[job['id']].options['voice_runtime'][engine])
                        assert frozen and not frozen.get('unavailable'),frozen
                        if index==0:
                            saved_cfg=copy.deepcopy(app.jobs.voice.cfg)
                            changed={'engines':{}}
                            if engine=='dramabox':changed['engines']['dramabox']={'python':'unavailable-after-enqueue',
                                'env':{'DRAMABOX_MODEL_DIR':'unavailable-after-enqueue','DRAMABOX_GEMMA_DIR':'unavailable-after-enqueue'}}
                            app.jobs.voice.cfg={**app.jobs.voice.cfg,'voice':changed}
                    try:wait_job(app,job['id'])
                    finally:
                        if index==0:app.jobs.voice.cfg=saved_cfg
                    current=app.lib.get(npc_id);meta=current['voice_lines']['greeting_1']
                    receipt=meta['model_identity'];assert receipt['queue_binding']['id']==frozen['id']
                    if engine=='dramabox':
                        assert receipt['auxiliary_models']['gemma']['revision']==binding.dramabox_runtime.GEMMA_REVISION
                        assert 'Gemma' in receipt['licence_conditions']
                        assert receipt['effective_controls']=={'cfg_scale':2.,'stg_scale':1.,'duration_multiplier':1.}
                        assert 'Speak quietly and warmly.' in receipt['performance_prompt']
                    assert receipt['queue_binding']['source']=='queue' and meta['reference']==version['id']
                    path=app.lib.resolve(npc_id,meta['file']);pcm,rate=audio.read_wav(path.read_bytes());assert len(pcm)>0
                    assert not record.line_is_stale(current,'greeting_1')
                    if engine not in ('kokoro','qwen_custom'):assert app.lib.read_asset(npc_id,current['voice']['reference']['file'])==ref_audio
                    zipped,stem=pack.build(app.lib,current);(root/f'{engine}-actor-{index+1}.zip').write_bytes(zipped)
                    with zipfile.ZipFile(io.BytesIO(zipped)) as archive:
                        manifest=json.loads(archive.read(stem+'/lines.json'));line=manifest['lines'][0]
                        assert line['model_identity']['queue_binding']['id']==frozen['id']
                        assert hashlib.sha256(archive.read(stem+'/'+line['file'])).hexdigest()==meta['lip_sync']['audio_sha256']
                    result['clips'].append({'npc_id':npc_id,'file':str(path),'seconds':len(pcm)/rate,'sample_rate':rate,
                        'elapsed_seconds':round(time.monotonic()-start,2),'model_identity':receipt,
                        'lip_sync_status':meta['lip_sync']['status'],'config_change_survived':index==0})
                    print('CLIP '+engine+' '+str(index+1)+' '+str(len(pcm)/rate)+'s',flush=True)
                scene=app.scenes.create({'title':engine+' pinned binding QA','cast':actors,
                    'voice_bindings':{npc_id:version['id'] for npc_id in actors},'beats':[
                    {'id':'binding-one','kind':'line','speaker_id':actors[0],'text':'The harbor is safe.'},
                    {'id':'binding-two','kind':'line','speaker_id':actors[1],'text':'Keep the lantern lit.'}]})
                job=app.jobs.submit_scene_voice(scene['id'],{});wait_job(app,job['id'])
                scene=app.scenes.get(scene['id']);timeline=compile_timeline(scene,app.lib,app.scenes)
                assert all(row['model_identity']['queue_binding']['source']=='queue' for row in scene['audio'].values())
                video=scene_render.render(scene,app.scenes,timeline=timeline)
                app.scenes.set_render(scene['id'],scene['revision'],video,timeline['fingerprint'])
                (root/f'{engine}-scene.zip').write_bytes(make_zip(app.scenes.get(scene['id']),app.scenes,'Runtime binding QA; human listening pending'))
                result['scene']={'id':scene['id'],'video':str(app.scenes.resolve(scene['id'],video)),
                                 'audio':scene['audio'],'timeline':timeline}
                if args.designs and engine in ('qwen','breeze','voxcpm2','dramabox','drama3'):
                    npc=app.lib.get(actors[0])
                    raw={'engine':engine,'mode':'design','speaker':None,'stock_voice':None,
                        'description':'An adult woman with a clear, warm speaking voice.','settings':{},
                        'script':'Welcome to the forge. Keep your lantern close.'}
                    npc=app.lib.update(npc['id'],lambda rec:record.save_settings(rec,record.clean_settings(raw,rec),app.lib))
                    request=service.takes_request(npc,{'count':1},cfg)
                    job=app.jobs.submit(npc,{'voice':request},{});wait_job(app,job['id'])
                    take=app.lib.get(npc['id'])['voice']['takes'][-1]
                    assert take['model_identity']['queue_binding']['operation']=='design'
                    result['design_take']=take
                report['engines'].append(result);(root/'report.json').write_text(json.dumps(report,indent=2))
                app.jobs.voice.release()
                print('PASS '+engine,flush=True)
        assert all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==sha for p,sha in before.items())
        report['source_unchanged']=True;report['status']='passed'
    except Exception as error:
        report.update(status='failed',error=str(error));raise
    finally:
        (root/'report.json').write_text(json.dumps(report,indent=2));print('REPORT '+str(root/'report.json'),flush=True)

if __name__=='__main__':main()
