import json
from pathlib import Path
import threading
import wave
from types import SimpleNamespace
import pytest
from npcgen.voice import markup, engines, audition, drama_studio
from npcgen.voice.worker import handle


def test_forge_http_studio_poll_artifacts_and_language_discovery(tmp_path):
    import urllib.request,time
    import server
    from npcgen import config
    from npcgen.app import App
    cfg=config.load();cfg['library_path']=tmp_path/'library'
    lab=engines.lab_root(cfg)
    if not lab or not (lab/'drama3_production.py').is_file():pytest.skip('needs a Voice Lab installation with the Drama studio')
    app=App(cfg);common=drama_studio.shared(cfg)
    def runner(body,refs,directory,emit,cancel):
        pcm=b'\x01\x00'*100;emit(pcm,44100)
        with wave.open(str(directory/'take.wav'),'wb') as stream:
            stream.setnchannels(1);stream.setsampwidth(2);stream.setframerate(44100);stream.writeframes(pcm)
        return {'device':'cpu'}
    app.drama_studio=common.Studio(tmp_path/'jobs',lambda:[],lambda _:None,runner)
    http=server.create_server(('127.0.0.1',0),app);thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
    base=f'http://127.0.0.1:{http.server_port}/api/voice/drama3'
    try:
        with urllib.request.urlopen(base+'/info') as response:assert len(json.load(response)['languages'])==84
        request=urllib.request.Request(base+'/jobs',data=json.dumps({'text':'A line.'}).encode(),headers={'Content-Type':'application/json'})
        with urllib.request.urlopen(request) as response:job=json.load(response)['job']
        for _ in range(20):
            with urllib.request.urlopen(base+'/jobs/'+job['id']+'?offset=0') as response:data=json.load(response)
            if data['job']['status']=='complete':break
            time.sleep(.01)
        assert data['job']['status']=='complete' and data['offset']==200
        with urllib.request.urlopen(base+'/jobs/'+job['id']+'/package') as response:assert response.headers['Content-Type']=='application/zip'
    finally:http.shutdown();http.server_close();thread.join(3)


def test_native_cues_and_speaker_tags_are_protected_from_pronunciation_and_word_checks():
    text='<|speaker:1|> [speaks softly] Stay close. (laugh)'
    assert markup.plain(text,'drama3')=='Stay close.'
    assert markup.plain('[written] note.')=='[written] note.'
    assert markup.transform_words(text,lambda s:s.replace('softly','loudly').replace('Stay','Wait'),'drama3')=='<|speaker:1|> [speaks softly] Wait close. (laugh)'
    assert 'Hindi' in engines.ENGINES['drama3']['languages']
    assert len(engines.ENGINES['drama3']['languages'])==84


def test_worker_production_stream_is_exactly_the_exported_wav(tmp_path):
    pcm=b'\x00\x80\x01\x00\xff\x7f'*100
    class Engine:
        name='drama3';model_identity={'device':'cpu'}
        def production(self,text,refs,description,direction,settings,emit):
            assert refs[0]['speaker']==1 and settings['seed']==0
            emit(pcm,44100)
            return [],44100
    out=tmp_path/'take.wav'
    reply=handle(Engine(),{'cmd':'clone','_studio':True,'text':'Hello.','out':str(out),
        '_studio_references':[{'speaker':1}], 'params':{'seed':0}})
    with wave.open(str(out),'rb') as stream:assert stream.readframes(stream.getnframes())==pcm
    assert out.with_suffix('.pcm').read_bytes()==pcm
    assert reply['model_identity']['device']=='cpu'


def test_drama_audition_adds_cue_and_identity_cases_and_flags_long_events():
    npc={'profile':{'first_name':'Keeper'},'voice':{'engine':'drama3','mode':'upload','language':'English'}}
    rows=audition.cases(npc)
    assert {'drama-inline','drama-event','drama-identity'}<={x['id'] for x in rows}
    import io,numpy as np
    from npcgen.voice import audio
    data=audio.write_wav(np.ones(44100*15,dtype=np.float32)*.1,44100)
    result=audition.quality(data,{'engine':'drama3','synthesis_text':'[sighs] Fine.',
        'model_identity':{'prompt':'[speaks softly] [sighs] Fine.'},
        'lip_sync':{'status':'aligned','validation':{'recognized_text':'speaks softly fine'}}})
    assert {'extended_event','spoken_cue'}<={x['code'] for x in result['flags']}
