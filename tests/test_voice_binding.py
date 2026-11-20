"""Queue/runtime bindings freeze actual offline dependencies without changing them."""
import copy
import os
from pathlib import Path
import sys

import pytest

from npcgen.voice import binding


def fixture(tmp_path):
    root=tmp_path/'hf/hub/models--hexgrad--Kokoro-82M/snapshots/f3ff3571791e39611d31c381e3a41a3af07b4987'
    (root/'voices').mkdir(parents=True)
    for name in ['config.json','kokoro-v1_0.pth','voices/af_heart.pt']:(root/name).write_bytes(b'test-only fixture')
    cfg={'voice':{'engines':{'kokoro':{'python':sys.executable,'env':{'HF_HOME':str(tmp_path/'hf'),'HF_TOKEN':'private-test-placeholder'}}}}}
    return cfg,root


def test_capture_freezes_local_checkpoint_and_excludes_authentication(tmp_path):
    cfg,root=fixture(tmp_path);before=copy.deepcopy(cfg)
    value=binding.capture(cfg,'kokoro',['stock'])
    assert cfg==before and 'HF_TOKEN' not in value['env']
    assert value['parts']['stock']['directory']==str(root.resolve())
    assert value['env']['KOKORO_MODEL_DIR']==str(root.resolve())
    assert binding.verify(value,'kokoro','stock')==value
    assert binding.receipt(value,'stock')['queue_binding']['source']=='queue'
    cfg['voice']['engines']['kokoro']['env']['HF_HOME']='another-cache'
    assert binding.verify(value,'kokoro','stock')==value
    assert [row['id'] for row in binding.available_stock(before)]==['af_heart']


def test_changed_weights_and_metadata_only_tampering_are_rejected(tmp_path):
    cfg,root=fixture(tmp_path);value=binding.capture(cfg,'kokoro',['stock'])
    altered=copy.deepcopy(value);altered['parts']['stock']['revision']='different'
    with pytest.raises(ValueError,match='binding checksum'):binding.verify(altered,'kokoro','stock')
    with pytest.raises(ValueError,match='another engine'):binding.verify(value,'qwen','clone')
    path=root/'kokoro-v1_0.pth';stat=path.stat();path.write_bytes(b'changed-fixture!!')
    os.utime(path,ns=(stat.st_atime_ns,stat.st_mtime_ns))
    with pytest.raises(ValueError,match='dependency changed'):binding.verify(value,'kokoro','stock',force=True)


def test_missing_cached_files_are_explicit_and_effect_only_jobs_can_queue(tmp_path):
    cfg,root=fixture(tmp_path);(root/'config.json').unlink()
    assert binding.discover(cfg,'kokoro')['status']=='unavailable'
    assert binding.available_stock(cfg)==[]
    saved=binding.freeze_job(cfg,{'kokoro':['stock']})
    assert 'missing config.json' in saved['kokoro']['unavailable']


def test_discovery_lists_only_real_embeddings_and_masks_uncached_operations(tmp_path):
    from npcgen.voice import engines
    cfg,root=fixture(tmp_path)
    assert [row['id'] for row in engines.stock_voices('kokoro',cfg)]==['af_heart']
    selected=next(row for row in engines.choices(cfg) if row['id']=='kokoro')
    assert selected['installed'] and selected['caps']['stock']
    assert selected['runtime_binding']['parts']['stock']['revision']==root.name
    (root/'kokoro-v1_0.pth').unlink()
    selected=next(row for row in engines.choices(cfg) if row['id']=='kokoro')
    assert not selected['installed'] and not selected['caps']['stock']
    assert 'directory' not in selected['runtime_binding']['parts']['stock']


def test_npc_and_scene_queue_builders_capture_the_effective_operation(tmp_path):
    cfg,root=fixture(tmp_path)
    npc={'id':'binding-fixture','profile':{'age':30},'voice':{'engine':'kokoro','mode':'stock','stock_voice':'af_heart','language':'English'}}
    queued=binding.for_npc_job(cfg,npc,{'voice':{'voice_snapshot':npc['voice'],'takes':1,'mode':'stock','engine':'kokoro'}},
        [{'kind':'voice_take'},{'kind':'voice_line'}])
    assert set(queued['kokoro']['parts'])=={'stock'}
    class Lib:
        def get(self,npc_id):return npc
    rows={'beat':{'speaker_id':npc['id'],'voice_snapshot':npc['voice'],'text':'Hello.','mood':'neutral','seed':42}}
    scene=binding.for_scene_job(cfg,Lib(),rows)
    assert scene['kokoro']['id']==queued['kokoro']['id']
