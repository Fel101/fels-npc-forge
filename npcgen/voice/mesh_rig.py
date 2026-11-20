"""Hash-bound artist mappings for existing GLB morph targets and jaw joints."""
import hashlib
import json
import math
import struct

from ..library import now
from .alignment import VISEMES

JAW = dict(zip(VISEMES,(0.,0.,.15,.35,.3,.45,.35,.12,.28,.35,1.,.45,.25,.8,.55)))


def inspect(lib,npc):
    file=((npc.get('assets') or {}).get('rig_3d') or {}).get('file')
    if not file:raise ValueError('Rig or import a 3D character model first')
    data=lib.read_asset(npc['id'],file)
    if len(data)<20 or data[:4]!=b'glTF' or struct.unpack_from('<I',data,4)[0]!=2 or struct.unpack_from('<I',data,8)[0]!=len(data):
        raise ValueError('Use a valid glTF2 binary model')
    length,kind=struct.unpack_from('<II',data,12)
    if kind!=0x4E4F534A or length>8_000_000 or 20+length>len(data):raise ValueError('The GLB JSON header is invalid')
    document=json.loads(data[20:20+length]);targets=set()
    for mesh in document.get('meshes',[]):
        names=(mesh.get('extras') or {}).get('targetNames') or []
        if any(not isinstance(n,str) or len(n)>128 for n in names):raise ValueError('Invalid morph target names')
        targets.update(names)
    joint_ids={index for skin in document.get('skins',[]) for index in skin.get('joints',[])}
    joints=sorted({node['name'] for index,node in enumerate(document.get('nodes',[])) if index in joint_ids and node.get('name')})
    return {'file':file,'sha256':hashlib.sha256(data).hexdigest(),'targets':sorted(targets),'joints':joints,
        'model_url':f"/api/npcs/{npc['id']}/files/{file}"}


def defaults(source):
    names=set(source['targets'])
    mapping={group:next((name for name in (f'viseme_{group}',group) if name in names),'') for group in VISEMES}
    mapping['sil']=''
    jaw=next((name for name in source['joints'] if name.lower() in ('jaw','jaw_01','lowerjaw','mandible')),'')
    amplitude=next((name for name in ('jawOpen','JawOpen','mouthOpen','viseme_aa') if name in names),'')
    return {'enabled':bool(jaw or any(mapping.values()) or amplitude),'mapping':mapping,'amplitude_target':amplitude,
        'jaw_bone':jaw,'jaw_axis':'x','jaw_radians':.28,'jaw_sign':1,'jaw_amounts':dict(JAW)}


def clean(raw,source):
    fields=set(defaults(source))
    if not isinstance(raw,dict) or set(raw)-fields:raise ValueError('Use the supported 3D mouth mapping controls')
    value={**defaults(source),**raw}
    if not isinstance(value['enabled'],bool):raise ValueError('3D mouth enabled must be true or false')
    mapping=value['mapping']
    if not isinstance(mapping,dict) or set(mapping)!=set(VISEMES) or any(name and name not in source['targets'] for name in mapping.values()):
        raise ValueError('Map each viseme to an existing morph target or neutral')
    if value['amplitude_target'] and value['amplitude_target'] not in source['targets']:raise ValueError('Choose an existing amplitude morph target')
    if value['jaw_bone'] and value['jaw_bone'] not in source['joints']:raise ValueError('Choose an existing jaw joint')
    if value['jaw_axis'] not in ('x','y','z') or isinstance(value['jaw_sign'],bool) or value['jaw_sign'] not in (-1,1):raise ValueError('Choose a jaw axis and direction')
    angle=value['jaw_radians']
    if isinstance(angle,bool) or not isinstance(angle,(int,float)) or not math.isfinite(angle) or not 0<=angle<=.6:raise ValueError('Jaw rotation must be between zero and 0.6 radians')
    amounts=value['jaw_amounts']
    if not isinstance(amounts,dict) or set(amounts)!=set(VISEMES) or any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or not 0<=v<=1 for v in amounts.values()):
        raise ValueError('Each jaw pose must have an amount from zero to one')
    return value


def save(lib,npc_id,body):
    with lib.lock:
        npc=lib.get(npc_id);source=inspect(lib,npc)
        previous=npc.get('voice_mesh_rig')
        config=clean({**(previous.get('config',{}) if previous and previous['source']['sha256']==source['sha256'] else {}),**body},source)
        snapshot_file=f"voice/visual-rigs/mesh/models/{source['sha256']}.glb"
        path=lib.resolve(npc_id,snapshot_file);path.parent.mkdir(parents=True,exist_ok=True)
        data=lib.read_asset(npc_id,source['file'])
        if hashlib.sha256(data).hexdigest()!=source['sha256']:raise ValueError('The model changed during mapping; try again')
        try:
            with path.open('xb') as stream:stream.write(data)
        except FileExistsError:
            if hashlib.sha256(path.read_bytes()).hexdigest()!=source['sha256']:raise ValueError('Immutable model snapshot checksum failed')
        source['snapshot_file']=snapshot_file
        stored={'schema':1,'source':{k:v for k,v in source.items() if k!='model_url'},'config':config}
        data=json.dumps(stored,sort_keys=True).encode();version=hashlib.sha256(data).hexdigest()
        file=f'voice/visual-rigs/mesh/versions/{version}.json';path=lib.resolve(npc_id,file);path.parent.mkdir(parents=True,exist_ok=True)
        try:
            with path.open('xb') as stream:stream.write(data)
        except FileExistsError:
            if path.read_bytes()!=data:raise ValueError('3D mouth version checksum failed')
        stored.update(version=version,version_file=file,created=now())
        def apply(record):
            record['voice_mesh_rig']=stored
            history=record.setdefault('voice_mesh_versions',[])
            if not any(row['version']==version for row in history):history.append({'version':version,'file':file,'created':stored['created']})
        return lib.update(npc_id,apply)


def describe(lib,npc):
    rig=npc.get('voice_mesh_rig')
    source=None
    try:
        source=inspect(lib,npc)
        if not rig:return {'status':'unconfigured','source':source,'config':defaults(source),'reason':'Save an artist mouth mapping for this model'}
        if source['sha256']!=rig['source']['sha256']:raise ValueError('The model changed; save mappings for the current model')
        config=clean(rig['config'],source)
        snapshot=lib.read_asset(npc['id'],rig['version_file'])
        if hashlib.sha256(snapshot).hexdigest()!=rig['version'] or json.loads(snapshot)!={'schema':1,'source':rig['source'],'config':rig['config']}:raise ValueError('3D mouth version changed')
        supported=bool(config['jaw_bone'] or config['amplitude_target'] or any(config['mapping'].values()))
        snapshot_file=rig['source']['snapshot_file']
        if hashlib.sha256(lib.read_asset(npc['id'],snapshot_file)).hexdigest()!=source['sha256']:raise ValueError('Immutable model snapshot changed')
        return {**rig,'source':{**source,'snapshot_file':snapshot_file,'model_url':f"/api/npcs/{npc['id']}/files/{snapshot_file}"},'status':'ready' if config['enabled'] and supported else 'disabled',
            'reason':None if supported else 'No jaw or mouth morph targets are mapped; amplitude animation remains',
            'versions':npc.get('voice_mesh_versions',[])}
    except (KeyError,OSError,ValueError,struct.error,TypeError) as error:
        current={'source':source,'config':defaults(source)} if source else {}
        return {**(rig or {}),**current,'status':'stale' if rig else 'missing','reason':str(error)}


def write_export(archive,lib,npc,root='',folder='game/character-rigs/mesh'):
    rig=describe(lib,npc)
    if rig.get('status')!='ready':return None
    file=f'{folder}/rig.json'
    model_file=f'{folder}/model.glb'
    archive.writestr(root+model_file,lib.read_asset(npc['id'],rig['source']['snapshot_file']))
    archive.writestr(root+file,json.dumps({'schema':1,'forge_version':rig['version'],'model_sha256':rig['source']['sha256'],
        'source_model':rig['source']['file'],'model_file':model_file,'targets':rig['source']['targets'],'joints':rig['source']['joints'],'config':rig['config']},indent=2))
    return file
