"""Offline model discovery and durable, checksum-bound worker inputs.

Private job bindings contain paths, never authentication variables. Public receipts
contain checkpoint/runtime digests. No model, environment or cache is modified.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import threading

from . import engines
from . import dramabox_runtime

PINS = {
    'drama3': {part:('sakasegawa/drama-3-GGUF','9a01354d04ab6eb521bc9cdfc56ab6824c474af2','DRAMA3_MODEL_DIR') for part in ('design','clone')},
    'qwen': {
        'design': ('Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign','5ecdb67327fd37bb2e042aab12ff7391903235d3','QWEN_DESIGN_MODEL'),
        'clone': ('Qwen/Qwen3-TTS-12Hz-1.7B-Base','fd4b254389122332181a7c3db7f27e918eec64e3','QWEN_CLONE_MODEL')},
    'kokoro': {'stock': ('hexgrad/Kokoro-82M','f3ff3571791e39611d31c381e3a41a3af07b4987','KOKORO_MODEL_DIR')},
    'chatterbox': {'clone': ('ResembleAI/chatterbox-turbo','749d1c1a46eb10492095d68fbcf55691ccf137cd','CHATTERBOX_MODEL_DIR')},
    'breeze': {part: ('drbaph/Breeze-TTS-2-comfyui','a6bfd9d0d4e8b3b61c3a5ed3ecf55468c8ab88e4','BREEZE_MODEL_DIR') for part in ('design','clone')},
    'voxcpm2': {part: ('openbmb/VoxCPM2','32279effe8c19989596f05d353d1447f51d9e915',None) for part in ('design','clone')},
    'qwen_custom': {'stock': (engines.CUSTOM_REPO,engines.CUSTOM_REVISION,None)},
    'chatterbox_original': {'clone': ('ResembleAI/chatterbox',engines.ORIGINAL_REVISION,None)},
    'dramabox': {part: (dramabox_runtime.REPO, dramabox_runtime.REVISION, 'DRAMABOX_MODEL_DIR') for part in ('design', 'clone')},
}
REQUIRED = {
    'drama3':['Drama-35B-A3B-v3-Q4_K.gguf','LICENSE','NOTICE'],
    'qwen': ['config.json','model.safetensors','speech_tokenizer/config.json','speech_tokenizer/model.safetensors'],
    'qwen_custom': ['config.json','model.safetensors','speech_tokenizer/config.json','speech_tokenizer/model.safetensors'],
    'kokoro': ['config.json','kokoro-v1_0.pth'],
    'chatterbox': ['ve.safetensors','t3_turbo_v1.safetensors','s3gen_meanflow.safetensors','conds.pt','tokenizer_config.json','vocab.json','merges.txt'],
    'chatterbox_original': ['ve.safetensors','t3_cfg.safetensors','s3gen.safetensors','conds.pt','tokenizer.json'],
    'breeze': ['config.json','Breeze-TTS-2-bf16.safetensors','audio_tokenizer/model.safetensors'],
    'voxcpm2': ['config.json','model.safetensors','audiovae.pth','tokenizer.json'],
    'dramabox': ['config.json', 'dramabox-dit-v1.safetensors', 'dramabox-audio-components.safetensors'],
}
ENV_KEYS = {'HF_HOME','HF_HUB_CACHE','HIP_VISIBLE_DEVICES','CUDA_VISIBLE_DEVICES','NPCFORGE_VOICE_DEVICE',
            'DRAMA3_INSTALL_RECEIPT','DRAMA3_MODEL_DIR',
            'VOICE_LAB','BREEZE_CODE','BREEZE_MODELS','BREEZE_DECODE','BREEZE_MODEL_DIR','KOKORO_MODEL_DIR',
            'CHATTERBOX_MODEL_DIR','QWEN_DESIGN_MODEL','QWEN_CLONE_MODEL','OMP_NUM_THREADS','MIOPEN_FIND_MODE',
            'DRAMABOX_SOURCE','DRAMABOX_MODEL_DIR','DRAMABOX_GEMMA_DIR','DRAMABOX_SITE_OVERLAYS','PYTHONDONTWRITEBYTECODE'}
MODEL_SUFFIXES = {'.json','.safetensors','.pth','.pt','.bin','.txt','.model','.tiktoken','.py','.gguf'}
_cache = {}
_lock = threading.RLock()


def digest(path,force=False):
    path=Path(path);stat=path.stat();key=(str(path.resolve()),stat.st_size,stat.st_mtime_ns,stat.st_ctime_ns)
    with _lock:
        if not force and key in _cache:return _cache[key]
    value=hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda:stream.read(4*1024*1024),b''):value.update(chunk)
    after=path.stat()
    if (after.st_size,after.st_mtime_ns,after.st_ctime_ns)!=(stat.st_size,stat.st_mtime_ns,stat.st_ctime_ns):
        raise ValueError(f'Voice dependency changed while being checked: {path.name}')
    result=value.hexdigest()
    with _lock:_cache[key]=result
    return result


def signature(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def model(cfg,engine_id,part):
    repo,revision,variable=PINS[engine_id][part]
    env=engines.engine_config(cfg,engine_id)['env'];lab=engines.lab_root(cfg)
    if engine_id == 'drama3':
        receipt=json.loads(Path(env['DRAMA3_INSTALL_RECEIPT']).read_text('utf-8'))
        root=Path(receipt['model_path']).parent.resolve()
        if receipt.get('revision')!=revision or receipt.get('repo')!=repo:
            raise ValueError('Drama 3 receipt does not match the pinned model revision')
        if not all((root/name).is_file() for name in REQUIRED[engine_id]) or not Path(receipt['runtime_path']).is_file():
            raise ValueError('Drama 3 model/runtime installation is incomplete')
        return {'checkpoint':repo,'revision':revision,'directory':str(root),'variable':variable,
                'upstream_checkpoint':'fishaudio/drama-3','upstream_revision':receipt['upstream_revision']}
    if engine_id == 'dramabox': dramabox_runtime.assets(env)
    override=env.get(variable) if variable else None
    if override and Path(override).is_dir():
        root=Path(override).resolve()
        revision=root.name if re.fullmatch('[a-f0-9]{40}',root.name) else 'local-files'
    elif engine_id=='breeze':
        code=Path(env.get('BREEZE_CODE') or lab or '')
        base=Path(env['BREEZE_MODELS'])/'breezetts2' if env.get('BREEZE_MODELS') else code/'breeze/models/breezetts2'
        root=base/'drbaph_Breeze-TTS-2-comfyui'
    else:
        if override and override!=repo:raise ValueError(f'{engine_id}: name an existing local checkpoint folder for {part}')
        if engine_id in engines.LAB_IDS:
            hf=Path(env.get('VOICE_LAB') or lab or '')/'hf-cache'
        else:hf=Path(env.get('HF_HOME') or Path.home()/'.cache/huggingface')
        hub=Path(env.get('HF_HUB_CACHE') or hf/'hub')
        candidates=[hub/('models--'+repo.replace('/','--'))/'snapshots'/revision,
                    hf/('models--'+repo.replace('/','--'))/'snapshots'/revision]
        root=next((p for p in candidates if p.is_dir()),candidates[0])
    root=root.resolve()
    missing=[name for name in REQUIRED[engine_id] if not (root/name).is_file()]
    if missing:raise ValueError(f'{engine_id} {part}: cached checkpoint is missing {", ".join(missing)}')
    if engine_id in ('qwen','qwen_custom'):
        document=json.loads((root/'config.json').read_text('utf-8'))
        kind={'design':'voice_design','clone':'base','stock':'custom_voice'}[part]
        if document.get('tts_model_type')!=kind or document.get('tts_model_size')!='1b7':
            raise ValueError(f'{engine_id} {part}: use the supported 1.7B {kind} checkpoint')
    return {'checkpoint':repo,'revision':revision,'directory':str(root),'variable':variable}


def available_stock(cfg):
    try:root=Path(model(cfg,'kokoro','stock')['directory'])
    except (KeyError,OSError,ValueError):return []
    return [row for row in engines.STOCK_VOICES['kokoro'] if (root/'voices'/f"{row['id']}.pt").is_file()]


def discover(cfg,engine_id):
    if engine_id not in PINS:return {'status':'test_adapter','parts':{}}
    parts={}
    for part in PINS[engine_id]:
        try:parts[part]={'available':True,**model(cfg,engine_id,part)}
        except (KeyError,OSError,ValueError) as error:parts[part]={'available':False,'reason':str(error)}
    return {'status':'ready' if any(p['available'] for p in parts.values()) else 'unavailable','parts':parts}


def _manifest(files):
    return {str(path.absolute()):digest(path) for path in sorted(set(files)) if path.is_file()}


def capture(cfg,engine_id,parts,source='queue'):
    if engine_id not in PINS:return None
    conf=engines.engine_config(cfg,engine_id);python=Path(conf['python'])
    if not python.is_file():
        hint='Set voice.lab_path to the existing Voice Lab installation' if engine_id in engines.LAB_IDS else f'python scripts/install_voice.py {engine_id}'
        raise ValueError(f'{engine_id} is not installed; configured Python is unavailable. {hint}')
    env={key:str(conf['env'].get(key,os.environ.get(key,''))) for key in ENV_KEYS if conf['env'].get(key) or os.environ.get(key)}
    env.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',HF_HUB_DISABLE_TELEMETRY='1')
    selected={};files=[]
    for part in sorted(set(parts)):
        row=model(cfg,engine_id,part);root=Path(row['directory'])
        row['files']=_manifest(p for p in root.rglob('*') if p.suffix in MODEL_SUFFIXES and '.cache' not in p.parts)
        if engine_id == 'drama3':
            row['files'].update(_manifest([root/'LICENSE',root/'NOTICE']))
        row['sha256']=signature({str(Path(file).relative_to(root)):sha for file,sha in row['files'].items()});selected[part]=row
        if engine_id == 'dramabox':
            _, _, gemma = dramabox_runtime.assets(conf['env'])
            extra = _manifest(p for p in gemma.rglob('*') if p.suffix in MODEL_SUFFIXES and '.cache' not in p.parts)
            gemma_sha = signature({str(Path(file).relative_to(gemma)):sha for file,sha in extra.items()})
            row['files'].update(extra)
            row['auxiliary_models'] = {'gemma': {'checkpoint':dramabox_runtime.GEMMA_REPO,
                'revision':gemma.name if re.fullmatch('[a-f0-9]{40}',gemma.name) else 'local-files', 'sha256':gemma_sha}}
            row['sha256'] = signature({'speech':row['sha256'],'auxiliary_models':row['auxiliary_models']})
        if row['variable']:env[row['variable']]=str(root)
    own=Path(__file__).parent
    files=[python,own/'worker.py',own/'binding.py',own/'engines.py',own/'adapters'/f'{engine_id}.py',own/'adapters/_torch.py']
    lab=Path(env['VOICE_LAB']) if env.get('VOICE_LAB') else engines.lab_root(cfg)
    if lab:
        if engine_id=='drama3':
            receipt_path=Path(env['DRAMA3_INSTALL_RECEIPT'])
            receipt=json.loads(receipt_path.read_text('utf-8'))
            files += [lab/'drama3_runtime.py',lab/'drama3_production.py',receipt_path]
            files += [own/'drama3_languages.json']
            files += [p for p in Path(receipt['runtime_path']).parent.iterdir() if p.is_file()]
        if engine_id=='breeze':files+=list((Path(env.get('BREEZE_CODE') or lab)/'breeze').rglob('*.py'))
        if engine_id=='voxcpm2':
            files+=[lab/'voxcpm_worker.py',lab/'voxcpm_support.py',lab/'gpu_gate.py']
            files+=list((lab/'out/m6/source/src/voxcpm').rglob('*.py'))
        if engine_id=='qwen_custom':files+=[lab/'qwen_worker.py',lab/'qwen_support.py',lab/'gpu_gate.py']
        if engine_id=='chatterbox_original':files+=[lab/'chatterbox_support.py']
    sites=[python.parent.parent/'Lib/site-packages']
    if engine_id == 'dramabox':
        drama_source, _, _ = dramabox_runtime.assets(conf['env'])
        files += [own/'dramabox_runtime.py', drama_source/'.git/HEAD', drama_source/'.git/packed-refs'] + list((drama_source/'.git/refs').rglob('*'))
        files += list((drama_source/'src').rglob('*.py')) + list((drama_source/'ltx2').rglob('*.py')) + list((drama_source/'ltx2').rglob('*.txt'))
        files += list(sites[0].glob('*.pth')) + list((sites[0]/'bitsandbytes').rglob('*.dll'))
        for startup in sites[0].glob('*.pth'):
            sites += [Path(line.strip()) for line in startup.read_text('utf-8-sig').splitlines()
                      if line.strip() and not line.startswith(('import ', '#')) and Path(line.strip()).is_dir()]
        sites += [Path(p) for p in conf['env'].get('DRAMABOX_SITE_OVERLAYS','').split(os.pathsep) if p]
    if engine_id=='voxcpm2' and lab:sites.append(lab/'.venv-fqwen/Lib/site-packages')
    package_names={'qwen':['faster_qwen3_tts','qwen_tts','transformers'],'qwen_custom':['faster_qwen3_tts','qwen_tts','transformers'],
                   'kokoro':['kokoro','misaki'],'chatterbox':['chatterbox'],'chatterbox_original':['chatterbox'],
                   'breeze':['transformers'],'voxcpm2':['transformers'],
                   'dramabox':['transformers','bitsandbytes','perth','accelerate','safetensors','soundfile','torchaudio']}.get(engine_id,[])
    for site in sites:
        for package in package_names:files+=list((site/package).rglob('*.py'))
        if engine_id == 'dramabox': files += list((site/'perth').rglob('*.pt')) + list((site/'perth').rglob('*.safetensors'))
        files+=list(site.glob('*dist-info/METADATA'))
    runtime=_manifest(files)
    value={'schema':1,'engine':engine_id,'source':source,'python':str(python.resolve()),'env':env,
           'parts':selected,'runtime_files':runtime,'runtime_sha256':signature(runtime)}
    value['id']=signature(value)
    return value


def verify(value,engine_id,part=None,force=False):
    if not isinstance(value,dict) or value.get('schema')!=1 or value.get('engine')!=engine_id:
        raise ValueError('Queued voice runtime binding is missing or belongs to another engine')
    if value.get('id')!=signature({k:v for k,v in value.items() if k!='id'}):raise ValueError('Queued voice runtime binding checksum failed')
    if part and part not in value['parts']:raise ValueError('Queued voice checkpoint does not support this operation')
    maps=[value['runtime_files'],*[row['files'] for row in value['parts'].values()]]
    for manifest in maps:
        for file,sha in manifest.items():
            if digest(file,force)!=sha:raise ValueError(f'Queued voice dependency changed: {Path(file).name}; submit a new job')
    return value


def receipt(value,part):
    row=value['parts'][part]
    return {'checkpoint':row['checkpoint'],'checkpoint_revision':row['revision'],'checkpoint_sha256':row['sha256'],
            **({'upstream_checkpoint':row['upstream_checkpoint'],'upstream_checkpoint_revision':row['upstream_revision']}
               if row.get('upstream_checkpoint') else {}),
            **({'auxiliary_models':row['auxiliary_models']} if row.get('auxiliary_models') else {}),
            **({'licence':engines.spec(value['engine'])['licence'], 'licence_conditions':engines.spec(value['engine'])['licence_conditions']}
               if engines.spec(value['engine']).get('licence_conditions') else {}),
            'runtime_sha256':value['runtime_sha256'],'queue_binding':{'id':value['id'],'source':value['source'],'operation':part},
            'forge_adapter_sha256':value['runtime_files'][str((Path(__file__).parent/'adapters'/f"{value['engine']}.py").resolve())]}


def freeze_job(cfg,voices):
    """Capture every engine used by this job; missing models fail only if TTS is needed.

    Effects-only reuse remains possible without the original inference environment.
    """
    wanted={}
    for engine_id,parts in voices.items():
        try:wanted[engine_id]=capture(cfg,engine_id,parts)
        except (KeyError,OSError,ValueError) as error:wanted[engine_id]={'unavailable':str(error)}
    return wanted


def for_npc_job(cfg,npc,options,steps):
    from . import record
    from .steps import speak_request
    request=options.get('voice') or {}
    v=request.get('voice_snapshot') or record.voice_of(npc)
    wanted={}
    def add(engine,part):wanted.setdefault(engine,set()).add(part)
    if any(step['kind']=='voice_take' for step in steps):
        engine=request.get('engine') or v['engine'];mode=request.get('mode') or v['mode']
        add(engine,'stock' if mode=='stock' else 'design')
    if any(step['kind'] in ('voice_line','voice_test') for step in steps):
        if request.get('auto_pick') and request.get('takes') and v['mode']!='stock':
            add(v.get('speaker') or request.get('engine') or v['engine'],'clone')
        else:
            engine,command=speak_request(npc,v,'Runtime binding check.','neutral',0)
            add(engine,command['cmd'])
    return freeze_job(cfg,wanted)


def for_scene_job(cfg,lib,beats):
    from .steps import speak_request
    wanted={}
    for row in beats.values():
        npc=lib.get(row['speaker_id']);v=row['voice_snapshot']
        engine,command=speak_request(npc,v,row.get('synthesis_text') or row['text'],row['mood'],row['seed'])
        wanted.setdefault(engine,set()).add(command['cmd'])
    return freeze_job(cfg,wanted)
