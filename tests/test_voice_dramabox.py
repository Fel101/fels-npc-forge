import json
import sys

import pytest
from npcgen.voice import authoring, binding, engines, record
from npcgen.voice.adapters.dramabox import controls, prompt


def fixture(tmp_path):
    source=tmp_path/'source';model=tmp_path/binding.dramabox_runtime.REVISION;gemma=tmp_path/binding.dramabox_runtime.GEMMA_REVISION
    for folder,names in [(source,['src/inference_server.py','ltx2/ltx_pipelines/utils/blocks.py']),
                         (model,binding.REQUIRED['dramabox']),
                         (gemma,['tokenizer.model','preprocessor_config.json','model-1.safetensors'])]:
        for name in names:
            path=folder/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('fixture')
    (gemma/'config.json').write_text(json.dumps({'model_type':'gemma3','quantization_config':{'load_in_4bit':True,'bnb_4bit_quant_type':'nf4'}}))
    (gemma/'model.safetensors.index.json').write_text(json.dumps({'weight_map':{'weight':'model-1.safetensors'}}))
    cfg={'voice':{'engines':{'dramabox':{'python':sys.executable,'env':{'DRAMABOX_SOURCE':str(source),
        'DRAMABOX_MODEL_DIR':str(model),'DRAMABOX_GEMMA_DIR':str(gemma),'DRAMABOX_SITE_OVERLAYS':'','HF_TOKEN':'not-a-real-token'}}}}}
    return cfg,source,model,gemma


def test_queue_freezes_gemma_and_source_and_private_settings(tmp_path):
    cfg,source,model,gemma=fixture(tmp_path);frozen=binding.capture(cfg,'dramabox',['clone','design'])
    assert 'HF_TOKEN' not in frozen['env']
    assert str(gemma/'model-1.safetensors') in frozen['parts']['clone']['files']
    receipt=binding.receipt(frozen,'clone')
    assert receipt['auxiliary_models']['gemma']['revision']==gemma.name
    assert receipt['checkpoint_revision']==model.name
    assert 'Gemma' in receipt['licence_conditions']
    cfg['voice']['engines']['dramabox']['env']['DRAMABOX_GEMMA_DIR']='changed-after-queue'
    assert binding.verify(frozen,'dramabox','clone')==frozen
    (gemma/'model-1.safetensors').write_text('changed')
    with pytest.raises(ValueError,match='dependency changed'):binding.verify(frozen,'dramabox','clone',force=True)


def test_missing_encoder_disables_engine_without_downloading(tmp_path):
    cfg,source,model,gemma=fixture(tmp_path)
    row=next(r for r in engines.choices(cfg) if r['id']=='dramabox')
    assert row['installed'] and row['caps']['clone'] and row['licence_conditions']
    (gemma/'model-1.safetensors').unlink()
    row=next(r for r in engines.choices(cfg) if r['id']=='dramabox')
    assert not row['installed'] and not row['caps']['design']


def test_prompts_keep_dialogue_separate_and_unsupported_controls_hidden():
    result=prompt('Say "hello", please.', 'Whisper "quietly".', clone=True)
    assert result.count('"')==2 and result.startswith('The reference speaker')
    assert 'Say ’hello’, please.' in result
    caps=authoring.capabilities('dramabox')
    assert caps['direction'] and caps['parameters']==list(controls({}))
    assert 'temperature' not in caps['parameters'] and 'inference_timesteps' not in caps['parameters']
    for invalid in [float('nan'),True,6]:
        with pytest.raises(ValueError):controls({'cfg_scale':invalid})
    with pytest.raises(ValueError,match='does not apply'):authoring.validate_performance({'parameters':{'temperature':.9}},'dramabox')


def test_native_line_requests_retain_authored_text_and_effective_direction():
    from npcgen.voice.steps import speak_request
    npc={'profile':{'age':30},'voice':{'engine':'dramabox','mode':'upload','language':'English','reference':{'file':'voice/ref.wav','id':'ref'}}}
    v=record.voice_of(npc);v['studio']={'presets':[{'id':'whisper','name':'Whisper','direction':'Whisper urgently.',
        'parameters':{'cfg_scale':2.,'stg_scale':1.,'duration_multiplier':1.}}],'default_performance':'whisper'}
    effective,text,provenance=authoring.prepare(v,'Do not open the door.','sad')
    engine,request=speak_request(npc,effective,text,'sad',42)
    assert engine=='dramabox' and request['cmd']=='clone' and request['text']=='Do not open the door.'
    assert 'Whisper urgently.' in request['instruct'] and request['params']['cfg_scale']==2.
    assert provenance['synthesis_text']=='Do not open the door.'


def test_reference_bounds_chunking_and_receipts_clear_old_identity(tmp_path, monkeypatch):
    import types
    import numpy as np
    from npcgen.voice import audio
    from npcgen.voice.adapters import dramabox
    calls=[]
    class Tensor:
        def detach(self):return self
        def cpu(self):return self
        def float(self):return self
        def numpy(self):return np.ones((1,100),dtype=np.float32)*.1
    class Server:
        def generate(self,**kwargs):calls.append(('short',kwargs));return Tensor(),48000
        def generate_long(self,**kwargs):calls.append(('long',kwargs));return Tensor(),48000
    engine=dramabox.Engine.__new__(dramabox.Engine)
    engine.torch=object();engine.server=Server();engine.watermarker=types.SimpleNamespace(apply_watermark=lambda x,sample_rate:x)
    engine.model_identity={};monkeypatch.setattr(dramabox,'seed_all',lambda *_:None)
    monkeypatch.setitem(sys.modules,'inference_server',types.SimpleNamespace(estimate_duration=lambda *_:50))
    ref=tmp_path/'reference.wav';ref.write_bytes(audio.write_wav(np.ones(35*8000)*.1,8000))
    engine.clone('Long scene.',str(ref),'','English',9,'Whisper.',{})
    assert calls[0][0]=='long' and calls[0][1]['ref_duration']==30
    assert engine.model_identity['reference_seconds']==35
    assert engine.model_identity['watermark_applied']
    engine.design('Hello.','An adult speaker.','English',9,{})
    assert 'reference_sha256' not in engine.model_identity
    ref.write_bytes(audio.write_wav(np.ones(8000)*.1,8000))
    with pytest.raises(ValueError,match='3–40'):engine.clone('Hello.',str(ref),'','English',9,None,{})
    with pytest.raises(ValueError,match='English only'):engine.design('Bonjour.','Adult.','French',9,{})
