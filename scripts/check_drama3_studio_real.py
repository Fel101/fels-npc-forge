"""Real Forge Drama studio HTTP/stream/export checks in a copied isolated library."""
import argparse
import base64
import copy
import hashlib
import io
import json
from pathlib import Path
import sys
import threading
import time
import urllib.request
import wave
import zipfile
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import server
from npcgen import config
from npcgen.app import App
from npcgen.library import Library
from npcgen.voice import record, engines


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--serve',action='store_true');args=parser.parse_args()
    run=ROOT/'local'/('drama3-studio-real-'+time.strftime('%Y%m%d-%H%M%S'));run.mkdir()
    cfg=config.load();cfg['library_path']=run/'library';app=App(cfg)
    prior=Library(ROOT/'local/voice-library-real-20261012-132540-da2158/library');source=prior.get('voiceqa1-actor-13087a')
    original_ref=source['voice']['reference'];raw=prior.read_asset(source['id'],original_ref['file'])
    originals=[prior.resolve(source['id'],'npc.json'),prior.resolve(source['id'],original_ref['file'])]
    second=engines.lab_root(cfg)/'out/drama3/acceptance-20261018-152113/neutral.wav'
    originals.append(second)
    fixtures=[(raw,original_ref),(second.read_bytes(),{'text':'Keep the gate closed. I will return before sunset.',
        'source':'Retained synthetic Drama 3 neutral acceptance clip'})]
    before={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in originals};ids=[]
    for i in range(2):
        reference_audio,reference_meta=fixtures[i]
        npc=copy.deepcopy(source)
        for key in ('id','created','updated','voice','voice_lines','assets','dialogue'):npc.pop(key,None)
        npc['profile'].update(first_name='Studio'+str(i),last_name='IsolatedQA');npc=app.lib.create(npc)
        def install(rec):
            v=record.clean_settings({'engine':'drama3','mode':'upload','speaker':'drama3','language':'English'},rec)
            record.save_settings(rec,v,app.lib);v=record.voice_of(rec)
            record.install_reference(app.lib,rec,v,reference_audio,{**reference_meta,'source':'retained-studio-QA'});rec['voice']=v
        app.lib.update(npc['id'],install)
        voice=app.voice_library.from_npc(npc['id'],{'name':'Studio isolated '+str(i)})
        version=voice['versions'][0];app.voice_library.approve(version['id'],{'approved':True,'notes':'Isolated synthetic fixture, not production quality approval'});ids.append(version['id'])
    http=server.create_server(('127.0.0.1',0),app);thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
    base=f'http://127.0.0.1:{http.server_port}';report={'status':'running','base':base,'run':str(run)}
    def save():(run/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    def api(path,body=None):
        req=urllib.request.Request(base+'/api/voice/drama3'+path,data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json'})
        with urllib.request.urlopen(req,timeout=180) as response:return json.load(response)
    print('RUN '+str(run)+' URL '+base,flush=True);save()
    try:
        info=api('/info');assert len(info['languages'])==84 and len(info['sources'])==2
        row=api('/jobs',{'text':'<|speaker:0|> [whispers] Keep the gate closed. <|speaker:1|> I will wait here.',
            'cast':[{'speaker':i,'source_id':key} for i,key in enumerate(ids)],'settings':{'seed':0,'max_seconds':20}})['job']
        offset=0;pcm=bytearray();streamed=False;deadline=time.monotonic()+900
        while time.monotonic()<deadline:
            result=api('/jobs/'+row['id']+'?offset='+str(offset));row=result['job'];offset=result['offset'];raw=base64.b64decode(result['pcm']);pcm.extend(raw)
            if raw and row['status']=='running':streamed=True
            if row['status'] in ('complete','failed','cancelled') and offset>=result['available_bytes']:break
            time.sleep(.15)
        assert row['status']=='complete',row
        assert streamed and row['model']['device']=='cpu'
        assert row['model']['queue_binding']['operation']=='clone'
        assert len(row['model']['references'])==2
        with urllib.request.urlopen(base+'/api/voice/drama3/jobs/'+row['id']+'/package') as response:data=response.read()
        (run/'cast-take.zip').write_bytes(data)
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            with wave.open(io.BytesIO(archive.read('take.wav'))) as wav:assert wav.readframes(wav.getnframes())==bytes(pcm)
            assert '[whispers]' not in archive.read('transcript.txt').decode()
        assert all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==sha for p,sha in before.items())
        sys.path.insert(0,str(config.ROOT))
        from npcgen.voice.drama_studio import shared
        shared(app.cfg)
        from worker_pool import process_is_running
        pid=row['model']['native_pid'];deadline=time.monotonic()+8
        while process_is_running(pid) and time.monotonic()<deadline:time.sleep(.1)
        assert not process_is_running(pid)
        report.update(status='passed',job=row,streamed_before_complete=streamed,source_unchanged=True,native_worker_exited=True);save()
        print('PASS Forge cast studio, streamed PCM, cue-free transcript, frozen runtime and ZIP; model unloaded',flush=True)
        if args.serve:print('REVIEW '+base,flush=True);thread.join()
    except BaseException as exc:report.update(status='failed',error=str(exc));save();raise
    finally:http.shutdown();http.server_close();thread.join(5)


if __name__=='__main__':main()
