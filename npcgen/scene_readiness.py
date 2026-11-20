"""Actionable readiness, derived from the exact compiled scene; never generates assets."""
def assess(scene, timeline, npcs, exists, background_ready=True):
    checks = []
    def add(code, level, message, action=None, **target):
        checks.append({'code': code, 'level': level, 'message': message, 'action': action, **target})
    for npc_id in scene['cast']:
        npc = npcs.get(npc_id)
        if not npc:
            add('missing_character', 'blocker', 'A cast member is missing. Choose a replacement.', 'cast', npc_id=npc_id)
            continue
        name = ' '.join(str(npc.get('profile', {}).get(key) or '') for key in ('first_name', 'last_name')).strip() or npc_id
        assets = npc.get('assets') or {}
        kind = 'rig_3d' if scene.get('mode') == 'stage3d' else 'portrait'
        asset = assets.get(kind) or {}
        if not exists(npc_id, asset):
            add('missing_visual', 'blocker' if kind == 'rig_3d' else 'warning',
                f'{name} needs a {"rigged 3D model" if kind == "rig_3d" else "portrait"}.', 'character', npc_id=npc_id)
        elif asset.get('stale'):
            add('outdated_visual', 'warning', f'{name} has an outdated {"3D rig" if kind == "rig_3d" else "portrait"}.', 'character', npc_id=npc_id)
    if scene.get('mode') == 'stage3d' and not background_ready:
        add('background', 'warning', 'The setting is missing or outdated; preview uses a plain background.', 'background')
    for index, beat in enumerate(timeline['beats'], 1):
        if beat['kind'] != 'line': continue
        voice = beat.get('voice')
        if not voice:
            add('missing_speech', 'warning', f'Beat {index} has missing or outdated speech; it will use subtitles only.', 'voice', beat_id=beat['id'])
            continue
        lip = voice.get('lip_sync') or {}
        validation = lip.get('validation') or {}
        if validation.get('transcript_match') is False or 'Recognized words disagree' in str(lip.get('reason') or ''):
            add('word_mismatch', 'warning', f'Beat {index} failed its word check. Listen for extra, missing or changed words.', 'review', beat_id=beat['id'])
        elif lip.get('status') not in ('aligned', 'word_aligned'):
            add('word_unchecked', 'info', f'Beat {index} has no validated word check: {lip.get("reason") or "timing unavailable"}.', 'review', beat_id=beat['id'])
        if lip.get('status') != 'aligned':
            add('mouth_fallback', 'info', f'Beat {index} uses an amplitude mouth track.', 'review', beat_id=beat['id'])
    old = scene.get('render') or {}
    if old and (old.get('revision') != scene['revision'] or old.get('fingerprint') != timeline['fingerprint']):
        add('old_export', 'warning', 'The saved video uses an earlier scene or earlier assets. Render again when ready.', 'export')
    return {'schema': 1, 'checks': checks, 'export_blocked': any(row['level'] == 'blocker' for row in checks),
            'speech_ready': sum(bool(row.get('voice')) for row in timeline['beats'] if row['kind'] == 'line'),
            'speech_total': sum(row['kind'] == 'line' for row in timeline['beats'])}
