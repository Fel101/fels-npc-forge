import json

import pytest

from npcgen import dialogue


def test_local_dialogue_pack_uses_profile_details_and_keeps_voice_provenance():
    npc = {"id": "mira-vale-123456", "profile": {
        "first_name": "Mira", "last_name": "Vale", "occupation": "clockmaker", "goal": "restore the old observatory",
        "hobbies": ["stargazing"], "quirk": "counts every gear twice", "peeve": "people touching the lenses",
        "speech": "careful and exact", "demeanor": "warm but guarded",
    }}
    pack = dialogue.generate(npc)
    all_lines = " ".join(line["text"] for rows in pack["lines"].values() for line in rows)
    assert len(pack["lines"]) == 5
    assert all(word in all_lines for word in ("Mira Vale", "clockmaker", "stargazing", "observatory", "gear", "lenses"))
    assert pack["voice"] == {"speech": "careful and exact", "demeanor": "warm but guarded"}
    assert pack["generator"] == "npcforge-profile-templates"


def test_dialogue_pack_validation_and_json_ink_yarn_exports():
    pack = dialogue.generate({"profile": {"first_name": "Ada", "last_name": "Vale", "occupation": "tinker"}})
    pack["lines"]["greeting"][0]["text"] = 'Hello "friend" <<command>>'
    clean = dialogue.validate(pack)
    assert json.loads(dialogue.render(clean, "json")[0])["lines"]["greeting"][0]["text"] == 'Hello "friend" <<command>>'
    ink = dialogue.render(clean, "ink")[0]
    assert '+ [Greetings] -> greeting' in ink and r'Hello \"friend\" ‹‹command››' in ink
    yarn = dialogue.render(clean, "yarn")[0]
    assert "title: npc_greeting_1" in yarn and "<<command>>" not in yarn
    with pytest.raises(ValueError, match="1 to 500"):
        dialogue.validate({"lines": {"greeting": [{"text": " "}]}})
    with pytest.raises(ValueError, match="Unknown dialogue format"):
        dialogue.render(clean, "godot")


def test_voiced_lines_are_referenced_in_the_json_and_tagged_in_yarn():
    pack = dialogue.validate(dialogue.generate({"profile": {"first_name": "Ada", "last_name": "Vale", "occupation": "tinker"}}))
    audio = {"greeting_1": "voice/lines/greeting_1.wav"}
    data = json.loads(dialogue.render(pack, "json", audio)[0])
    assert data["lines"]["greeting"][0]["audio"] == "voice/lines/greeting_1.wav" and "audio" not in data["lines"]["greeting"][1]
    yarn = dialogue.render(pack, "yarn", audio)[0]
    assert "#line:greeting_1" in yarn and "#line:greeting_2" not in yarn
    assert "#line:" not in dialogue.render(pack, "yarn")[0]                  # nothing voiced: the export is as before


def test_quest_lines_read_right_for_table_goals_and_typed_goals():
    table = dialogue.generate({"profile": {"first_name": "Hattie", "occupation": "saloon performer", "goal": "opening a saloon"}})
    typed = dialogue.generate({"profile": {"first_name": "Mira", "occupation": "clockmaker", "goal": "restore the old observatory"}})
    quest = lambda pack: " ".join(line["text"] for line in pack["lines"]["quest"])
    assert "My aim is opening a saloon." in quest(table) and "to opening" not in quest(table)
    assert "My aim is to restore the old observatory." in quest(typed)
