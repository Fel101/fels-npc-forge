"""Character mouth frames preserve artwork/audio and provide explicit fallback rigs."""
import base64
import hashlib
import io
import json

import pytest
from PIL import Image

from npcgen.library import Library
from npcgen.voice import authoring, character_rig


def png(color, size=(128, 128)):
    out = io.BytesIO(); Image.new('RGBA', size, color).save(out, 'PNG'); return out.getvalue()


def actor(tmp_path):
    lib = Library(tmp_path/'library')
    npc = lib.create({'profile':{'first_name':'Mouth','last_name':'QA'}, 'assets':{'portrait':{'file':'portrait.png'}},
        'voice':{'engine':'qwen','mode':'design','description':'A narrator.','language':'English'}})
    base = png((50, 90, 120, 255)); talking = png((130, 90, 80, 255))
    lib.write_asset(npc['id'],'portrait.png',base)
    lib.write_asset(npc['id'],'living/talking.png',talking)
    lib.write_asset(npc['id'],'living/living.json',json.dumps({'portrait':{'sha256':hashlib.sha256(base).hexdigest()},'frames':{'talking':{}}}).encode())
    return lib,npc,base


def test_default_living_bridge_and_pixel_blending_preserve_source_and_voice(tmp_path):
    lib,npc,base = actor(tmp_path)
    signature = authoring.prepare(npc['voice'],'Hello')[2]['render_signature']
    saved = character_rig.save(lib,npc['id'],'portrait',{'enabled':True})
    rig = character_rig.describe(lib,saved,'portrait')
    assert rig['status']=='ready' and set(rig['frames'])=={'rest','talking'}
    assert rig['mapping']['PP']=='rest' and rig['mapping']['aa']=='talking'
    assert character_rig.image(lib,npc['id'],rig,{'sil':.5,'aa':.5}).getpixel((40,40))==(90,90,100,255)
    assert character_rig.image(lib,npc['id'],rig,{'sil':1}).getpixel((40,40))==(50,90,120,255)
    assert lib.read_asset(npc['id'],'portrait.png')==base and saved['voice']==npc['voice']
    assert authoring.prepare(saved['voice'],'Hello')[2]['render_signature']==signature


def test_imported_frames_and_mapping_create_versions_without_overwriting_parent(tmp_path):
    lib,npc,base=actor(tmp_path)
    saved=character_rig.save(lib,npc['id'],'portrait',{})
    parent=saved['voice_rigs']['portrait']; parent_bytes=lib.read_asset(npc['id'],parent['version_file'])
    uploaded=character_rig.upload(lib,npc['id'],'portrait',{'name':'closed','image':base64.b64encode(png((25,80,110,255))).decode()})
    rig=uploaded['voice_rigs']['portrait']; mapping={**rig['mapping'],'sil':'closed'}
    saved=character_rig.save(lib,npc['id'],'portrait',{'mapping':mapping,'amplitude_open':'talking'})
    assert saved['voice_rigs']['portrait']['mapping']['sil']=='closed'
    assert len(saved['voice_rig_versions']['portrait'])==3
    assert lib.read_asset(npc['id'],parent['version_file'])==parent_bytes
    restored=character_rig.restore(lib,npc['id'],'portrait',parent['version'])
    assert restored['voice_rigs']['portrait']['version']==parent['version']
    assert set(restored['voice_rigs']['portrait']['frames'])=={'rest','talking'}
    assert lib.read_asset(npc['id'],'portrait.png')==base
    with pytest.raises(ValueError):character_rig.upload(lib,npc['id'],'portrait',{'name':'../escape','image':''})
    with pytest.raises(ValueError):character_rig.upload(lib,npc['id'],'portrait',{'name':'bad','image':base64.b64encode(png('red',(100,100))).decode()})


def test_artwork_regeneration_marks_rig_stale_and_rebinding_preserves_old_frames(tmp_path):
    lib,npc,_=actor(tmp_path)
    saved=character_rig.save(lib,npc['id'],'portrait',{})
    previous=saved['voice_rigs']['portrait']; old=lib.read_asset(npc['id'],previous['frames']['rest']['file'])
    lib.write_asset(npc['id'],'portrait.png',png('green'))
    assert character_rig.describe(lib,lib.get(npc['id']),'portrait')['status']=='stale'
    with pytest.raises(ValueError):character_rig.save(lib,npc['id'],'portrait',{})
    rebound=character_rig.save(lib,npc['id'],'portrait',{'reset':True})
    assert character_rig.describe(lib,rebound,'portrait')['status']=='ready'
    assert len(rebound['voice_rig_versions']['portrait'])==2
    assert lib.read_asset(npc['id'],previous['frames']['rest']['file'])==old


def test_sprite_chooses_dominant_pose_and_fallback_never_claims_phonemes(tmp_path):
    lib,npc,_=actor(tmp_path)
    lib.write_asset(npc['id'],'sprite.png',png((25,50,80,128)))
    lib.update(npc['id'],lambda rec:rec['assets'].update(sprite={'file':'sprite.png'}))
    saved=character_rig.save(lib,npc['id'],'sprite',{})
    uploaded=character_rig.upload(lib,npc['id'],'sprite',{'name':'open','image':base64.b64encode(png((90,80,75,128))).decode()})
    rig=uploaded['voice_rigs']['sprite']; mapping={**rig['mapping'],'aa':'open'}
    saved=character_rig.save(lib,npc['id'],'sprite',{'mapping':mapping,'amplitude_open':'open'})
    rig=saved['voice_rigs']['sprite']
    assert character_rig.frame_weights(rig,{'sil':.2,'aa':.8})=={'open':1}
    assert character_rig.weights_at({'status':'amplitude_fallback','seconds':1,'mouth_fps':25},[.4],0)=={'sil':.6,'mouth_open':.4}
    assert character_rig.weights_at({'seconds':1},[1],1)=={'sil':1}
