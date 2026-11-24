"""Native Drama 3 picker, saved performance, cloning and runtime binding regressions."""
import hashlib
import pytest

from npcgen.voice import authoring, engines, markup


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
