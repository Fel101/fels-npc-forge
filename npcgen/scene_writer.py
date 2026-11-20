"""Local, editable scene proposals from NPC profiles and explicit story templates."""
import secrets

from .scenes import clean_scene

TEMPLATES = {
    'negotiation': ('Before we make a deal, we need to understand each other.', "What are you asking me to risk?", "Then let's agree on the first step."),
    'confrontation': ('Stop a moment. We need to settle this.', "I need a better explanation before I can trust you.", "I'll hear you out. What happens next is up to you."),
    'discovery': ('Wait. There is something here we should look at together.', 'Tell me what you found.', "We should investigate together, carefully."),
    'farewell': ('Before we part, there is one thing left to say.', 'Go on. I am listening.', 'Until we meet again. Take care of yourself.')}


def suggest(raw, lib, previous=None):
    if not isinstance(raw, dict) or not isinstance(raw.get('scene'), dict):
        raise ValueError('Choose a scene for the writing assistant')
    objective, location = raw.get('objective'), raw.get('location')
    for value, title, limit in ((objective, 'Objective', 500), (location, 'Location', 300)):
        if not isinstance(value, str) or not value.strip() or len(value) > limit:
            raise ValueError(f'{title} needs 1–{limit} characters')
    template, tone = raw.get('template', 'negotiation'), raw.get('tone', 'calm')
    if template not in TEMPLATES or tone not in ('calm', 'tense', 'hopeful', 'playful'):
        raise ValueError('Choose a supported scene template and tone')
    original = clean_scene(raw['scene'], lib, previous)
    cast = original['cast']; profiles = {id: lib.get(id).get('profile', {}) for id in cast}
    opening, challenge, resolution = TEMPLATES[template]
    if tone == 'tense': challenge = 'Make it quick. I need straight answers, and I need them now.'
    elif tone == 'hopeful': challenge = 'Perhaps we can help each other. Tell me what you have in mind.'
    elif tone == 'playful': challenge = "All right, you've got my attention. Is this the sensible plan, or the interesting one?"
    lines = [(cast[0], opening), (cast[1], challenge),
             (cast[0], "Here's what needs to happen: " + objective.strip().rstrip('.!?') + '.')]
    for id in cast:
        profile = profiles[id]; goal = str(profile.get('goal') or '').strip().rstrip('.!?')
        demeanor = str(profile.get('demeanor') or '').lower()
        if any(word in demeanor for word in ('warm', 'kind', 'friendly', 'gentle')):
            personal = 'I want us both to come out of this safely.'
        elif any(word in demeanor for word in ('guard', 'cold', 'suspicious', 'stern')):
            personal = 'Trust has to be earned. I will watch what you do.'
        else:
            personal = 'I will listen, but I need to make my own decision.'
        if goal:
            personal += ' I have my own aim: ' + goal[:130] + '.'
        lines.append((id, personal))
    lines.extend([(cast[0], 'We can start here, at ' + location.strip().rstrip('.!?') + '.'), (cast[1], resolution)])
    beats = []
    for index, (speaker, text) in enumerate(lines):
        beats.append({'id': 'draft-' + secrets.token_hex(5), 'kind': 'line', 'speaker_id': speaker, 'text': text[:300],
            'layout': 'split', 'line_id': '', 'mood': 'neutral', 'expression': '', 'motion': 'stand',
            'camera_shot': 'wide' if index == 0 else 'close' if index == 2 else 'medium', 'camera_target': speaker,
            'camera_move': 'push' if index == 2 else 'static', 'camera_strength': .2, 'camera_transition': .35 if index else 0,
            'acting': {id: {'facing': 'speaker', 'look_at': 'speaker', 'gesture': 'nod' if tone == 'hopeful' else 'none'} for id in cast if id != speaker}})
    proposal = clean_scene({**original, 'beats': beats}, lib, previous)
    return {'generator': 'local-profile-templates-v1', 'template': template, 'tone': tone, 'location': location.strip(),
        'objective': objective.strip(), 'beats': proposal['beats'],
        'notes': [{'npc_id': id, 'name': ' '.join(filter(None, [profile.get('first_name'), profile.get('last_name')])),
            'demeanor': profile.get('demeanor', ''), 'speech': profile.get('speech', ''), 'goal': profile.get('goal', '')}
            for id, profile in profiles.items()],
        'review_note': 'Local profile-based template draft, not language-model output. Review dialogue and acting before applying or voicing.'}
