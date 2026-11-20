import { h, btn, safe } from './core.js';
import { initialPosition } from './scene-blocking.js';

const label = (title, control) => h('label', {class: 'scene-field'}, h('span', {}, title), control);
const numeric = (title, value, min, max, step, change) => label(title, h('input', {type: 'number', value, min, max, step,
  'aria-label': title, onchange: safe(e => change(Number(e.target.value)))}));

export function stagingView({scene, npcs, beat, commit, preview, pause}) {
  let selected = beat?.speaker_id || scene.cast[0], mode = 'position', dragging = null;
  const name = id => [npcs[id]?.profile?.first_name, npcs[id]?.profile?.last_name].filter(Boolean).join(' ') || id;
  const map = h('div', {class: 'scene-stage-map', role: 'group', 'aria-label': 'Top-down stage map'},
    h('span', {class: 'scene-map-back'}, 'Back'), h('span', {class: 'scene-map-front'}, 'Camera / front'));
  const tokens = new Map();
  const actor = id => {
    scene.actors ||= {};
    return scene.actors[id] ||= {position: initialPosition(scene, id), rotation: 0};
  };
  const place = () => {
    for (const [id, token] of tokens) {
      const position = scene.actors?.[id]?.position || initialPosition(scene, id);
      token.style.left = `${(position[0] + 3) / 6 * 100}%`; token.style.top = `${(position[1] + 2) / 4 * 100}%`;
      token.classList.toggle('selected', id === selected);
      token.querySelector('i').style.transform = `rotate(${scene.actors?.[id]?.rotation || 0}deg)`;
    }
    marker.hidden = mode !== 'walk' || !beat?.walk_to;
    if (beat?.walk_to) { marker.style.left = `${(beat.walk_to[0] + 3) / 6 * 100}%`; marker.style.top = `${(beat.walk_to[1] + 2) / 4 * 100}%`; }
  };
  const marker = h('span', {class: 'scene-walk-mark', hidden: true, 'aria-label': 'Walk destination'}, '×');
  map.append(marker);
  for (const id of scene.cast) {
    const token = h('button', {type: 'button', class: 'scene-stage-token', 'data-actor': id,
      'aria-label': `Position ${name(id)}`, title: `${name(id)} — drag to move`,
      onkeydown: safe(async event => {
        const movement = {ArrowLeft: [-.1, 0], ArrowRight: [.1, 0], ArrowUp: [0, -.1], ArrowDown: [0, .1]}[event.key];
        if (!movement) return; event.preventDefault(); pause(); selected = id;
        actor(id).position = actor(id).position.map((v, i) => Math.max(i ? -2 : -3, Math.min(i ? 2 : 3, Math.round((v + movement[i]) * 100) / 100)));
        place(); await commit();
      })}, h('i', {}, '▼'), h('span', {}, name(id)));
    tokens.set(id, token); map.append(token);
  }
  const move = event => {
    const rect = map.getBoundingClientRect();
    const position = [Math.max(-3, Math.min(3, (event.clientX - rect.left) / rect.width * 6 - 3)),
      Math.max(-2, Math.min(2, (event.clientY - rect.top) / rect.height * 4 - 2))].map(v => Math.round(v * 100) / 100);
    if (mode === 'walk' && beat?.speaker_id) { beat.motion = 'walk'; beat.walk_to = position; }
    else if (dragging) actor(dragging).position = position;
    place(); preview();
  };
  map.addEventListener('pointerdown', event => {
    if (event.button !== 0) return;
    const target = event.target.closest('[data-actor]');
    if (mode !== 'walk' && !target) return;
    if (mode === 'walk' && !beat?.speaker_id) return;
    event.preventDefault(); pause(); dragging = mode === 'walk' ? beat.speaker_id : target.dataset.actor;
    selected = dragging; map.setPointerCapture(event.pointerId); move(event);
  });
  map.addEventListener('pointermove', event => { if (dragging) move(event); });
  const finish = safe(async event => {
    if (!dragging) return; dragging = null;
    if (map.hasPointerCapture(event.pointerId)) map.releasePointerCapture(event.pointerId);
    await commit();
  });
  map.addEventListener('pointerup', finish); map.addEventListener('pointercancel', finish);
  const picker = h('select', {'aria-label': 'Stage character', onchange: event => {
    selected = event.target.value; rotation.value = scene.actors?.[selected]?.rotation || 0; place();
  }}, scene.cast.map(id => h('option', {value: id}, name(id))));
  picker.value = selected;
  const rotation = h('input', {type: 'number', min: -180, max: 180, step: 5, value: scene.actors?.[selected]?.rotation || 0,
    oninput: event => { actor(selected).rotation = Number(event.target.value); place(); preview(); },
    'aria-label': 'Stage character rotation', onchange: safe(async event => { actor(selected).rotation = Number(event.target.value); await commit(); })});
  place();
  return h('section', {class: 'scene-staging stack', 'aria-label': 'Visual staging'}, h('h3', {}, 'Visual staging'),
    h('p', {class: 'hint'}, 'Top-down view. Drag a character to set its starting position, or focus it and use arrow keys. Use Walk destination to place the selected beat’s mark.'),
    h('div', {class: 'row wrap'}, btn('Starting positions', null, () => { mode = 'position'; place(); }),
      btn('Walk destination', null, () => { mode = 'walk'; place(); }, {disabled: !beat?.speaker_id})), map,
    h('div', {class: 'scene-form-grid'}, label('Stage character', picker), label('Rotation in degrees', rotation)));
}

export function castView({scene, library, add, remove}) {
  const unused = library.filter(row => !scene.cast.includes(row.id));
  const select = h('select', {'aria-label': 'Add scene character'}, unused.map(row => h('option', {value: row.id}, row.name)));
  return h('section', {class: 'stack'}, h('h3', {}, `Cast · ${scene.cast.length}/6`),
    h('div', {class: 'row wrap'}, select, btn('Add cast member', 'plus', safe(() => add(select.value)), {disabled: !unused.length || scene.cast.length >= 6})),
    scene.cast.length > 2 && h('div', {class: 'row wrap'}, scene.cast.map(id => btn(`Remove ${library.find(n => n.id === id)?.name || id}`, null,
      safe(() => remove(id)), {cls: 'small ghost'}))));
}

export function propsView({scene, items, commit}) {
  const available = items.filter(item => item.has?.model_3d);
  const select = h('select', {'aria-label': 'Stage prop item'}, available.map(item => h('option', {value: item.id}, item.name)));
  return h('section', {class: 'stack'}, h('h3', {}, 'Props and scenery'),
    h('div', {class: 'row wrap'}, select, btn('Add prop', 'plus', safe(async () => {
      scene.props ||= []; scene.props.push({id: `prop-${crypto.randomUUID().slice(0, 8)}`, item_id: select.value, position: [0, 0, 0], rotation: 0, scale: 1}); await commit();
    }), {disabled: !available.length || (scene.props?.length || 0) >= 16})),
    !available.length && h('p', {class: 'hint'}, 'Create an item with a 3D model in Items to place it here.'),
    (scene.props || []).map(prop => h('div', {class: 'scene-prop-fields stack'},
      h('strong', {}, items.find(item => item.id === prop.item_id)?.name || prop.item_id),
      h('div', {class: 'scene-form-grid'},
        ...['X', 'Height', 'Depth'].map((title, index) => numeric(`Prop ${title}`, prop.position[index], index === 1 ? 0 : index === 0 ? -3 : -2,
          index === 1 ? 3 : index === 0 ? 3 : 2, .1, async value => { prop.position[index] = value; await commit(); })),
        numeric('Prop rotation', prop.rotation, -180, 180, 5, async value => { prop.rotation = value; await commit(); }),
        numeric('Prop scale', prop.scale, .05, 10, .05, async value => { prop.scale = value; await commit(); })),
      btn('Remove prop', 'trash', safe(async () => { scene.props.splice(scene.props.indexOf(prop), 1); await commit(); }), {cls: 'small ghost'}))));
}
