import { h, btn, safe } from './core.js';

export function cameraMovementFields(beat, field, select, update) {
  return [field('Camera movement', select([['follow', 'Follow framing (legacy)'], ['static', 'Hold shot'], ['push', 'Push in'], ['pan_left', 'Pan left'], ['pan_right', 'Pan right'], ['track', 'Track subject']],
    beat.camera_move || 'follow', value => update('camera_move', value), {'aria-label': 'Camera movement'})),
    field('Movement amount', h('input', {type: 'number', min: 0, max: 1, step: .1, value: beat.camera_strength ?? .3,
      oninput: e => { beat.camera_strength = Number(e.target.value); },
      'aria-label': 'Camera movement amount', onchange: e => update('camera_strength', Number(e.target.value))})),
    field('Shot transition seconds', h('input', {type: 'number', min: 0, max: 3, step: .1, value: beat.camera_transition || 0,
      oninput: e => { beat.camera_transition = Number(e.target.value); },
      'aria-label': 'Shot transition seconds', onchange: e => update('camera_transition', Number(e.target.value))}))];
}

export function actingView({scene, npcs, timeline, beat, commit, field, select}) {
  const name = id => [npcs[id]?.profile?.first_name, npcs[id]?.profile?.last_name].filter(Boolean).join(' ') || id;
  const targets = [['speaker', 'Current speaker'], ['camera', 'Camera'], ...scene.cast.map(id => [id, name(id)])];
  const expressions = {happy: /smile|happy/i, laughing: /smile|happy/i, sad: /sad|frown/i, angry: /angry/i, surprised: /surprise|brow.*up/i, thinking: /thinking/i};
  const set = async (id, key, value) => { beat.acting ||= {}; beat.acting[id] ||= {}; beat.acting[id][key] = value; await commit(); };
  return h('details', {class: 'scene-acting'}, h('summary', {}, 'Acting and listening reactions'),
    h('p', {class: 'hint'}, 'Facing persists until changed. Gestures and expressions apply to this beat. Head and arm gestures use available bones; expressions require matching morph targets.'),
    btn('Listeners watch speaker', null, safe(async () => {
      beat.acting ||= {};
      for (const id of scene.cast.filter(id => id !== beat.speaker_id)) beat.acting[id] = {...beat.acting[id], facing: 'speaker', look_at: 'speaker', gesture: 'nod'};
      await commit();
    }), {cls: 'small ghost', disabled: !beat.speaker_id}),
    scene.cast.map(id => {
      const direction = beat.acting?.[id] || {}, morphs = timeline?.mesh_rigs?.[id]?.source?.targets || [];
      const supported = Object.entries(expressions).filter(([, regex]) => morphs.some(value => regex.test(value)));
      return h('div', {class: 'scene-form-grid scene-actor-direction'}, h('strong', {}, name(id)),
        field('Face toward', select([['rotation', 'Use rotation'], ...targets], direction.facing || 'rotation', value => set(id, 'facing', value), {'aria-label': `${name(id)} facing`})),
        field('Facing degrees', h('input', {type: 'number', min: -180, max: 180, step: 5, value: direction.rotation || 0,
          oninput: e => { beat.acting ||= {}; beat.acting[id] ||= {}; beat.acting[id].rotation = Number(e.target.value); },
          onchange: safe(e => set(id, 'rotation', Number(e.target.value)))})),
        field('Look toward', select([['', 'Animation default'], ...targets], direction.look_at || '', value => set(id, 'look_at', value))),
        field('Gesture', select([['none', 'Animation default'], ['wave', 'Wave'], ['nod', 'Nod'], ['shake', 'Shake head'], ['point', 'Point']], direction.gesture || 'none', value => set(id, 'gesture', value))),
        field('Facial expression', select([['', supported.length ? 'Neutral' : 'No expression morphs on this rig'], ...supported.map(([key]) => [key, key])],
          direction.expression || '', value => set(id, 'expression', value), {disabled: !supported.length})));
    }));
}
