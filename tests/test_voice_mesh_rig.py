import hashlib
import json
import struct
import io
import zipfile

import pytest

from npcgen import media
from npcgen.library import Library
from npcgen.voice import mesh_rig


def glb(targets=('viseme_aa','jawOpen')):
    document={'asset':{'version':'2.0'},'nodes':[{'name':'head'},{'name':'jaw'}],
        'skins':[{'joints':[0,1]}],'meshes':[{'extras':{'targetNames':list(targets)}}]}
    data=json.dumps(document).encode();data+=b' '*((-len(data))%4)
    return struct.pack('<III',0x46546c67,2,len(data)+20)+struct.pack('<II',len(data),0x4e4f534a)+data


def actor(tmp_path):
    lib=Library(tmp_path/'library');npc=lib.create({'profile':{'first_name':'Mesh','last_name':'QA'},'assets':{'rig_3d':{'file':'rig.glb'}},'voice':{'engine':'qwen','description':'A narrator.'}})
    data=glb();lib.write_asset(npc['id'],'rig.glb',data);return lib,npc,data


def test_mesh_rig_discovers_real_names_and_binds_without_editing_model_or_voice(tmp_path):
    lib,npc,data=actor(tmp_path);source=mesh_rig.inspect(lib,npc)
    assert source['targets']==['jawOpen','viseme_aa'] and source['joints']==['head','jaw']
    saved=mesh_rig.save(lib,npc['id'],{})
    rig=mesh_rig.describe(lib,saved)
    assert rig['status']=='ready' and rig['config']['jaw_bone']=='jaw'
    assert rig['config']['mapping']['aa']=='viseme_aa' and rig['config']['mapping']['FF']==''
    assert rig['source']['sha256']==hashlib.sha256(data).hexdigest()
    assert saved['voice']==npc['voice'] and lib.read_asset(npc['id'],'rig.glb')==data
    lib.write_asset(npc['id'],'rig.glb',glb(('other',)))
    assert mesh_rig.describe(lib,lib.get(npc['id']))['status']=='stale'


def test_mesh_controls_reject_missing_targets_nonfinite_angles_and_invalid_joint(tmp_path):
    lib,npc,_=actor(tmp_path);source=mesh_rig.inspect(lib,npc)
    for controls in ({'jaw_bone':'fake'},{'jaw_axis':'w'},{'jaw_radians':float('nan')},{'jaw_sign':True},{'enabled':'true'},{'amplitude_target':'missing'}):
        with pytest.raises(ValueError):mesh_rig.clean(controls,source)
    c=mesh_rig.defaults(source);c['mapping']['aa']='missing'
    with pytest.raises(ValueError):mesh_rig.clean(c,source)


def test_mesh_export_checks_snapshot_and_keeps_immutable_versions(tmp_path):
    lib,npc,data=actor(tmp_path)
    saved=mesh_rig.save(lib,npc['id'],{})
    original=saved['voice_mesh_rig'];old=lib.read_asset(npc['id'],original['version_file'])
    changed=mesh_rig.save(lib,npc['id'],{'jaw_radians':.4})
    assert len(changed['voice_mesh_versions'])==2
    assert lib.read_asset(npc['id'],original['version_file'])==old
    output=io.BytesIO()
    with zipfile.ZipFile(output,'w') as archive:
        descriptor=mesh_rig.write_export(archive,lib,changed)
    with zipfile.ZipFile(output) as archive:
        rig=json.loads(archive.read(descriptor))
        assert hashlib.sha256(archive.read(rig['model_file'])).hexdigest()==rig['model_sha256']
        assert archive.read(rig['model_file']) == media.label(data) and rig['config']['jaw_radians'] == .4
    assert lib.read_asset(npc['id'], changed['voice_mesh_rig']['source']['snapshot_file']) == data
    lib.write_asset(npc['id'],changed['voice_mesh_rig']['source']['snapshot_file'],b'corrupt')
    assert mesh_rig.describe(lib,lib.get(npc['id']))['status']=='stale'
    with zipfile.ZipFile(io.BytesIO(),'w') as archive:
        assert mesh_rig.write_export(archive,lib,lib.get(npc['id'])) is None
