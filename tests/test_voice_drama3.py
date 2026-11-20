"""Native Drama 3 picker, saved performance, cloning and runtime binding regressions."""
import hashlib
import json
import os
from pathlib import Path
import sys
import time

import pytest
from npcgen.voice import authoring, binding, engines, markup, record
from npcgen.voice.steps import speak_request


def test_capabilities_and_persisted_performance_use_only_applied_controls():
    caps=authoring.capabilities('drama3')
    assert caps['direction'] and caps['parameters']==['temperature','top_k','top_p','max_seconds']
    preset=authoring.validate_performance({'direction':'Whisper urgently.',
        'parameters':{'temperature':1.1,'top_k':400,'top_p':.85,'max_seconds':20}},'drama3')
    assert preset['parameters']['top_k']==400
    with pytest.raises(ValueError):authoring.validate_performance({'parameters':{'cfg_scale':2}},'drama3')


def test_neutral_markup_converts_to_actual_fish_event_tags():
    assert markup.for_engine('[laugh] Fine. [sigh] We go together.','drama3')=='[laughs] Fine. [sighs] We go together.'
    assert markup.plain('[laughs] Fine.')=='Fine.'


def test_native_saved_clone_keeps_transcript_and_direction():
    npc={'voice':{'engine':'drama3','mode':'upload','reference':{'id':'ref','file':'voice/ref.wav',
        'text':'The original reference words.'},'language':'English'}}
    v=record.voice_of(npc)
    v['studio']={'presets':[{'id':'quiet','name':'Quiet','direction':'Whisper urgently.',
        'parameters':{'max_seconds':12.,'top_k':30}}],'default_performance':'quiet'}
    effective,text,_=authoring.prepare(v,'[sigh] Keep the gate closed.','sad')
    engine,request=speak_request(npc,effective,text,'sad',0)
    assert engine=='drama3' and request['cmd']=='clone' and request['seed']==0
    assert request['ref_text']=='The original reference words.'
    assert request['text']=='[sighs] Keep the gate closed.' and 'Whisper urgently.' in request['instruct']
    assert request['params']['max_seconds']==12


def test_queue_captures_quantized_model_native_binary_license_and_immutable_paths(tmp_path):
    model=tmp_path/'model';model.mkdir()
    for name in binding.REQUIRED['drama3']:(model/name).write_text('fixture')
    native=tmp_path/'speech.exe';native.write_text('native fixture')
    receipt=tmp_path/'installation.json'
    receipt.write_text(json.dumps({'repo':'sakasegawa/drama-3-GGUF','revision':binding.PINS['drama3']['clone'][1],
        'upstream_revision':'upstream','model_path':str(model/'Drama-35B-A3B-v3-Q4_K.gguf'),'runtime_path':str(native)}))
    cfg={'voice':{'engines':{'drama3':{'python':sys.executable,
        'env':{'VOICE_LAB':str(tmp_path),'DRAMA3_INSTALL_RECEIPT':str(receipt)}}}}}
    # A real lab marker lets discovery resolve the separately installed runtime.
    (tmp_path/'catalog').mkdir();(tmp_path/'catalog/engines.json').write_text('{}')
    cfg['voice']['lab_path']=str(tmp_path)
    frozen=binding.capture(cfg,'drama3',['clone','design'])
    assert str(native) in frozen['runtime_files'] and str(receipt) in frozen['runtime_files']
    assert str(model/'LICENSE') in frozen['parts']['clone']['files']
    assert binding.receipt(frozen,'clone')['checkpoint']=='sakasegawa/drama-3-GGUF'
    cfg['voice']['engines']['drama3']['env']['DRAMA3_INSTALL_RECEIPT']='changed-after-queue'
    binding.verify(frozen,'drama3','clone')
    native.write_text('tampered executable')
    with pytest.raises(ValueError,match='dependency changed'):binding.verify(frozen,'drama3','clone',force=True)


@pytest.mark.skipif(os.name!='nt',reason='Windows venv/native ownership')
def test_forge_cancellation_kills_the_actual_venv_child_and_its_native_job(tmp_path,monkeypatch):
    from npcgen.voice import runtime
    lab=engines.lab_root(__import__('npcgen.config',fromlist=['load']).load())
    if lab is None:pytest.skip('Existing Voice Lab installation required for the ownership helper')
    script=tmp_path/'owned_worker.py';pidfile=tmp_path/'native.pid'
    script.write_text(f'''import json,os,subprocess,sys
sys.path.insert(0,{str(lab)!r})
from drama3_runtime import ChildJob
job=ChildJob()
child=subprocess.Popen([getattr(sys,'_base_executable',sys.executable),'-c','import time; time.sleep(120)'])
job.add(child)
open({str(pidfile)!r},'w').write(str(child.pid))
print(json.dumps({{'id':0,'ok':True,'ready':True,'engine':'drama3','pid':os.getpid(),'parent_pid':os.getppid()}}),flush=True)
sys.stdin.read()
''')
    monkeypatch.setattr(runtime,'WORKER',script)
    worker=runtime.Worker('drama3',str(lab/'.venv/Scripts/python.exe'),{},tmp_path/'worker.log')
    try:
        worker.wait_ready()
        native=int(pidfile.read_text())
        handle=worker.native_kernel.OpenProcess(0x00100000,False,native)
        assert handle and worker.native_kernel.WaitForSingleObject(handle,0)==0x102
        worker.kill()
        try:assert worker.native_kernel.WaitForSingleObject(handle,5000)==0
        finally:worker.native_kernel.CloseHandle(handle)
        assert not worker.alive() and worker.native_runtime_handle is None
    finally:worker.kill()
