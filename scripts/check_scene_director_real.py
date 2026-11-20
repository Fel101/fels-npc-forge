"""Prepare isolated real assets for Scene Director UI/export QA, or verify its MP4."""
from pathlib import Path
import copy,json,hashlib,shutil,sys,time,uuid
root=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root))
from npcgen import config
from npcgen.app import App
from npcgen.library import Library
from npcgen.scenes import compile_timeline
from server import create_server

def prepare():
    prior=root/'local/voice-mesh-real-20261012-231710-14476b/library'
    source=Library(prior)
    old=json.loads((prior/'.npcforge-scenes/scene-dcde4c20bf/scene.json').read_text('utf-8'))
    qa=root/'local'/('scene-director-real-'+time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:6]);qa.mkdir()
    cfg=config.load();cfg['library_path']=qa/'library'
    hashes={}
    with App(cfg) as app:
     for npc_id in old['cast']:
      src=source.resolve(npc_id,'npc.json').parent
      for path in src.rglob('*'):
       if path.is_file():hashes[str(path)]=hashlib.sha256(path.read_bytes()).hexdigest()
      shutil.copytree(src,app.lib.root/npc_id)
      app.lib.save(app.lib.get(npc_id))
     first,second=old['beats'][:2]
     raw={**old,'title':'Scene Director QA','beats':[
      {'id':'wide-pause','kind':'pause','duration':1,'camera_shot':'wide'},
      {**first,'camera_shot':'medium','camera_target':old['cast'][0]},
      {**second,'camera_shot':'close','camera_target':old['cast'][1]},
      {'id':'shoulder-reaction','kind':'reaction','speaker_id':old['cast'][0],'duration':1.5,'motion':'stand','camera_shot':'shoulder'},
      {'id':'ending-pause','kind':'pause','duration':.5,'camera_shot':'wide'}]}
     scene=app.scenes.create(raw)
     for beat in (first,second):
      meta=copy.deepcopy(old['audio'][beat['id']]); data=(prior/'.npcforge-scenes'/old['id']/meta['file']).read_bytes()
      hashes[str(prior/'.npcforge-scenes'/old['id']/meta['file'])]=hashlib.sha256(data).hexdigest()
      app.scenes.write_audio(scene['id'],meta['file'],data)
      assert app.scenes.set_audio(scene['id'],beat['id'],meta)
     scene=app.scenes.get(scene['id']);timeline=compile_timeline(scene,app.lib,app.scenes)
     assert sum(bool(beat.get('voice')) for beat in timeline['beats'])==2
     (qa/'source-hashes.json').write_text(json.dumps(hashes,indent=2))
     (qa/'fixture.json').write_text(json.dumps({'scene_id':scene['id'],'timeline':timeline},indent=2))
     httpd=create_server(('127.0.0.1',0),app)
     print('ROOT '+str(qa),flush=True);print('SCENE '+scene['id'],flush=True);print('URL http://127.0.0.1:'+str(httpd.server_port)+'/',flush=True)
     httpd.serve_forever()

def verify(qa):
    import io,subprocess,zipfile
    import numpy as np
    from npcgen.scenes import Scenes
    from npcgen import scene_render
    from npcgen.scene_export import make_zip
    from npcgen.voice import audio
    qa=Path(qa).resolve()
    fixture=json.loads((qa/'fixture.json').read_text('utf-8'))
    lib=Library(qa/'library'); scenes=Scenes(lib); scene=scenes.get(fixture['scene_id'])
    timeline=compile_timeline(scene,lib,scenes)
    render=scene.get('render') or {}
    assert render.get('fingerprint')==timeline['fingerprint'],'Export current scene from the browser first'
    video=scenes.resolve(scene['id'],render['file'])
    probe=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_entries','format=duration:stream=codec_type,r_frame_rate,width,height,nb_frames','-of','json',str(video)]))
    stream=next(row for row in probe['streams'] if row['codec_type']=='video')
    assert int(stream['nb_frames'])==timeline['frames']
    assert (stream['width'],stream['height'])==tuple(timeline['size'])
    assert stream['r_frame_rate']=='25/1'
    samples={}
    for shot in ('wide','medium','close','shoulder'):
        beat=next(row for row in timeline['beats'] if row.get('camera_shot')==shot)
        frame=beat['start']+min(10,beat['frames']//2)
        path=qa/('frame-'+shot+'.png')
        subprocess.run(['ffmpeg','-v','error','-y','-i',str(video),'-vf','select=eq(n\\,'+str(frame)+')','-frames:v','1',str(path)],check=True)
        samples[shot]={'frame':frame,'file':str(path)}
    scene_render._audio_mix(scene,timeline,scenes,qa/'expected-mix.wav')
    decoded=subprocess.check_output(['ffmpeg','-v','error','-i',str(video),'-vn','-ac','1','-ar','48000','-f','f32le','pipe:1'])
    actual=np.frombuffer(decoded,dtype='<f4'); expected,rate=audio.read_wav((qa/'expected-mix.wav').read_bytes())
    assert rate==48000
    n=min(len(actual),len(expected)); correlation=float(np.corrcoef(actual[:n],expected[:n])[0,1]);assert correlation>.98
    packed=make_zip(scene,scenes,'Copied real QA assets; body/portrait/voice identity is not asserted.')
    (qa/'scene-package.zip').write_bytes(packed)
    with zipfile.ZipFile(io.BytesIO(packed)) as archive:
        manifest=json.loads(archive.read('sources.json'))
        assert not manifest['render_outdated']
        assert len(manifest['voices'])==2
        for voice in manifest['voices']:
            assert hashlib.sha256(archive.read(voice['file'])).hexdigest()==voice['game_playback']['audio_sha256']
        saved=json.loads(archive.read('scene.json'))
        assert {row['camera_shot'] for row in saved['beats']}=={'wide','medium','close','shoulder'}
    hashes=json.loads((qa/'source-hashes.json').read_text('utf-8'))
    assert all(hashlib.sha256(Path(path).read_bytes()).hexdigest()==sha for path,sha in hashes.items())
    report={'status':'passed','root':str(qa),'scene_id':scene['id'],'timeline':timeline,'probe':probe,'samples':samples,
            'audio_zero_offset_correlation':correlation,'source_files_unchanged':len(hashes),
            'limits':'Retained real speech and GLB reused. Body, portrait and voice identity/phonetic quality are not asserted.'}
    (qa/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print('PASS '+str(qa/'report.json'))

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify',metavar='QA_ROOT',help='Verify a native browser MP4 and retain sampled frames/report')
    args=parser.parse_args()
    if args.verify:verify(args.verify)
    else:prepare()
