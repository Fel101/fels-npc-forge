import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { api, btn, debounce, h, safe, toast } from './core.js';
import { View, disposeTree } from './tryon-view.js';

// Put a held item or a hat on a character: the character's rigged model and the item are shown together, the item hung on the bone the server says (npcgen/equip.py
// works out where from the character's rest pose: the head and the palms from its skin weights, which way it faces from its hands), with sliders to nudge it and buttons to keep
// what you chose or download the character holding it. The server answers with the bone and the transform of the item's origin in that bone's own space, and that is all this
// draws from: the item is made a child of the bone, which is also what a game does with the same numbers.

const NUDGES = [
  { key: 'up', label: 'Up or down', unit: 'mm', metres: true, min: -100, max: 100, step: 1, limits: [-200, 200] },
  { key: 'forward', label: 'Forward or back', unit: 'mm', metres: true, min: -100, max: 100, step: 1, limits: [-200, 200] },
  { key: 'side', label: 'Toward the right or left', unit: 'mm', metres: true, min: -100, max: 100, step: 1, limits: [-200, 200] },
  { key: 'turn', label: 'Turn', unit: 'deg', min: -180, max: 180, step: 1, limits: [-180, 180] },
  { key: 'tilt', label: 'Tilt (tip it up)', unit: 'deg', min: -60, max: 60, step: 1, limits: [-90, 90] },
  { key: 'size', label: 'Size', unit: '×', min: 0.6, max: 1.6, step: 0.01, limits: [0.4, 2.5] },
];
const NEUTRAL = { up: 0, forward: 0, side: 0, turn: 0, tilt: 0, size: 1 };
const SLOTS = { hand_r: 'Right hand', hand_l: 'Left hand', head: 'Head' };
const loader = new GLTFLoader();
const shown = (value, step) => Number(value).toFixed(String(step).includes('.') ? String(step).split('.')[1].length : 0);
const stamp = value => encodeURIComponent(value || '');
const put = (parent, ...children) => parent.replaceChildren(...children.filter(Boolean));
/** The nudge as the server wants it: millimetres sent as metres. */
const toApi = nudge => Object.fromEntries(NUDGES.map(spec => [spec.key, spec.metres ? nudge[spec.key] / 1000 : nudge[spec.key]]));
const fromApi = nudge => Object.fromEntries(NUDGES.map(spec => [spec.key, spec.metres ? Math.round((nudge[spec.key] ?? 0) * 1000) : (nudge[spec.key] ?? NEUTRAL[spec.key])]));
const query = (slot, nudge) => new URLSearchParams({ slot, ...Object.fromEntries(Object.entries(toApi(nudge)).map(([key, value]) => [key, String(value)])) }).toString();

/** The 3D scene of the try-on panel, looking at the whole figure, or at its head or a hand. */
class EquipView extends View {
  reset(theta) {
    this.theta = theta;
    this.phi = Math.PI / 2 - 0.04;
    this.radius = this.distance ?? 0.55 * this.unit;
    this.target = this.focus.clone();
    this.invalidate?.();
  }

  look(focus, height, distance) {
    this.focus.fromArray(focus);
    this.unit = (height || 1.7) / 1.7;
    this.distance = distance * this.unit;
    this.reset(this.theta);
  }
}

/** The panel for one saved item that can be carried or worn. `how` is its kind's {carry, slot, slots} from /api/config. Returns {element, update(item), destroy()}; onItem(item)
 * hears of what is kept being saved or forgotten. */
export function equipPanel(first, { how, onItem }) {
  let item = first;
  let characters = [];
  let npcId = '';
  let npc = null;                           // the character's record
  let slot = how.slot;
  let nudge = { ...NEUTRAL };
  let placement = null;
  let itemBuild = null;                     // which build of the item is shown
  let holder = null;                        // the group the item hangs in, a child of the bone
  let loadSeq = 0, placeSeq = 0, view = null, gone = false;
  const sliders = {};

  const canvas = h('canvas', { class: 'g-tryon-canvas', role: 'img', 'aria-label': 'The character holding the item, in 3D. Drag to turn, scroll to zoom.' });
  const message = h('p', { class: 'hint g-tryon-note', 'aria-live': 'polite' }, '');
  const report = h('div', { class: 'g-tryon-report', 'aria-live': 'polite' });
  const say = (...lines) => report.replaceChildren(...lines.filter(Boolean).map(([text, kind]) => h('p', { class: `hint${kind ? ` ${kind}` : ''}` }, text)));
  const picker = h('select', { 'aria-label': 'Character', onchange: event => safe(chooseCharacter)(event.target.value) });
  const slotPicker = h('select', { 'aria-label': 'Where it goes', onchange: event => { slot = event.target.value; placeSoon(); drawButtons(); } },
    how.slots.map(id => h('option', { value: id }, SLOTS[id] || id)));
  const saveRow = h('div', { class: 'row wrap model3d-actions' });
  const modelOf = () => item?.assets?.model_3d || {};
  const kept = () => (item?.equips || {})[npcId];

  const slider = spec => {
    const range = h('input', { type: 'range', min: spec.min, max: spec.max, step: spec.step, value: NEUTRAL[spec.key], 'aria-label': spec.label });
    const number = h('input', { type: 'number', class: 'g-num', min: spec.limits[0], max: spec.limits[1], step: spec.step, value: shown(NEUTRAL[spec.key], spec.step), 'aria-label': `${spec.label} (number)` });
    const set = value => {
      const clamped = Math.min(spec.limits[1], Math.max(spec.limits[0], value));
      nudge[spec.key] = clamped;
      number.value = shown(clamped, spec.step);
      range.value = clamped;
      placeSoon();
      drawButtons();
    };
    range.addEventListener('input', () => set(Number(range.value)));
    number.addEventListener('change', () => { const value = Number(number.value); if (Number.isNaN(value)) number.value = shown(nudge[spec.key], spec.step); else set(value); });
    sliders[spec.key] = { range, number, spec };
    return h('div', { class: 'g-row' }, h('span', { class: 'g-label' }, spec.label), range, number, h('span', { class: 'g-unit' }, spec.unit));
  };

  function setNudge(next) {
    nudge = { ...NEUTRAL, ...next };
    for (const { range, number, spec } of Object.values(sliders)) { range.value = nudge[spec.key]; number.value = shown(nudge[spec.key], spec.step); }
  }

  const what = how.carry === 'hat' ? 'wear it' : 'hold it';
  const element = h('div', { class: 'card g-tryon' },
    h('h3', {}, 'Put it on a character'),
    h('p', { class: 'hint' }, `Pick a character that has a skeleton (a rigged 3D model) and NPC Forge puts the item where it belongs: ${how.carry === 'hat' ? 'on the head, sized to it' : how.carry === 'shield' ? 'on the left forearm, facing forward' : 'in the hand, gripped at its handle'}. ` +
      'Nudge it if the rules got it wrong and keep what you chose. Nothing is changed on the character: the download is its model with the item hung under the bone, so it follows every animation, ' +
      'and the bone and transform are given for a game that would rather attach the item itself.'),
    h('div', { class: 'g-tryon-grid' },
      h('div', { class: 'g-tryon-view' }, canvas,
        h('div', { class: 'g-tryon-views row' }, [['Whole', 'whole'], [how.carry === 'hat' ? 'Head' : 'Hand', 'close'], ['Side', 'side']].map(([label, key]) =>
          h('button', { type: 'button', class: 'btn small', onclick: () => frame(key) }, label))),
        message),
      h('div', { class: 'stack' },
        h('div', {}, h('label', { class: 'lbl' }, 'Character'), picker),
        how.slots.length > 1 ? h('div', {}, h('label', { class: 'lbl' }, 'Where it goes'), slotPicker) : null,
        h('div', { class: 'stack g-nudges' }, NUDGES.map(slider),
          h('div', { class: 'row wrap' }, btn('Reset the nudge', 'refresh', () => { setNudge(NEUTRAL); placeSoon(); drawButtons(); }, { cls: 'small' }))),
        report, saveRow)));

  // ---- the characters ---------------------------------------------------------------------------------------------------------------------
  async function start() {
    try { characters = (await api('GET', '/api/npcs')).filter(card => card.has.rig_3d).sort((a, b) => a.name.localeCompare(b.name)); }
    catch (error) { message.textContent = error.message; return; }
    if (!characters.length) {
      picker.replaceChildren(h('option', { value: '' }, 'No character has a skeleton yet'));
      picker.disabled = true;
      say([`Make a skeleton for a character first (a rigged 3D model, or a designed body): then you can ${what} on it here.`]);
      return;
    }
    picker.replaceChildren(h('option', { value: '' }, 'Choose a character…'), ...characters.map(card => h('option', { value: card.id }, card.name)));
    drawButtons();
  }

  async function ensureView() {
    if (!view) {
      try { view = new EquipView(canvas); } catch { message.textContent = 'This browser cannot draw 3D (WebGL is off).'; throw new Error('No WebGL'); }
      view.resize();
    }
  }

  function detach() {
    holder?.removeFromParent();
  }

  async function chooseCharacter(id) {
    npcId = id;
    placement = null;
    detach();
    say();
    drawButtons();
    if (!id) { view?.setCharacter(null); message.textContent = ''; npc = null; return; }
    const seq = ++loadSeq;
    npc = await api('GET', `/api/npcs/${id}`);
    if (seq !== loadSeq || gone) return;
    const model = npc.assets?.rig_3d || {};
    if (!model.file) { message.textContent = 'This character has no skeleton.'; return; }
    await ensureView();
    message.textContent = 'Loading the character…';
    let gltf;
    try {
      gltf = await loader.loadAsync(`/api/npcs/${id}/files/${model.file}?v=${stamp(model.created)}`,
        event => { if (seq === loadSeq && event.total) message.textContent = `Loading the character… ${Math.round(100 * event.loaded / event.total)}%`; });
    } catch { if (seq === loadSeq) message.textContent = 'The character’s model could not be loaded.'; return; }
    if (seq !== loadSeq || gone) { disposeTree(gltf.scene); return; }
    detach();
    view.setCharacter(gltf.scene);
    frame('whole');
    message.textContent = '';
    await place(true);
  }

  /** Look at the whole figure, at what the item is hung from, or from the side. */
  function frame(key) {
    if (!view?.character) return;
    const box = new THREE.Box3().setFromObject(view.character);
    const height = box.max.y - box.min.y;
    const centre = [(box.min.x + box.max.x) / 2, box.min.y + height * 0.5, (box.min.z + box.max.z) / 2];
    if (key === 'close' && placement) {
      view.character.updateMatrixWorld(true);
      const bone = view.character.getObjectByName(placement.bone);
      if (bone) { view.look(new THREE.Vector3().setFromMatrixPosition(bone.matrixWorld).toArray(), height, how.carry === 'hat' ? 0.9 : 1.1); return; }
    }
    view.look(centre, height, 4.4);                     // (the lens sees 26 degrees: the whole figure at this distance, with a margin)
    if (key === 'side') view.reset(Math.PI / 2);
  }

  // ---- the placement ----------------------------------------------------------------------------------------------------------------------
  const placeSoon = debounce(() => safe(() => place(false))(), 120);

  async function place(initial) {
    if (!npcId) return;
    const seq = ++placeSeq;
    let result;
    try { result = await api('POST', `/api/items/${item.id}/equip`, initial ? { npc: npcId } : { npc: npcId, slot, nudge: toApi(nudge) }); }
    catch (error) { say([error.message, 'err']); return; }
    if (seq !== placeSeq || gone) return;
    if (initial) { slot = result.slot; slotPicker.value = slot; setNudge(fromApi(result.nudge)); }        // (what is kept for this character, else the kind's usual place)
    placement = result;
    await ensureView();
    await showItem();
    if (seq !== placeSeq || gone) return;
    hang(result.attach);
    const bits = [`${SLOTS[result.slot] || result.slot}, on the bone ${result.bone}`, how.carry === 'hat' ? `scaled ×${result.scale} to this head` : result.scale !== 1 ? `scaled ×${result.scale}` : null];
    say([bits.filter(Boolean).join(' · ')], ...result.notes.map(note => [note, 'warn']));
    drawButtons();
  }

  async function showItem() {
    const model = modelOf();
    const build = model.build || model.created;
    if (holder && itemBuild === build) return;
    if (!model.file) return;
    const gltf = await loader.loadAsync(`/api/items/${item.id}/files/${model.file}?v=${stamp(build)}`);
    if (gone) { disposeTree(gltf.scene); return; }
    if (holder) { holder.removeFromParent(); disposeTree(holder); }
    holder = new THREE.Group();
    holder.add(gltf.scene);
    itemBuild = build;
  }

  /** Hang the item from its bone at the server's transform (the item's origin, in the bone's own space). */
  function hang(attach) {
    const bone = view?.character?.getObjectByName(attach.bone);
    if (!bone || !holder) { message.textContent = `The character has no bone called ${attach.bone}.`; return; }
    holder.removeFromParent();
    bone.add(holder);
    if (attach.matrix) { holder.matrixAutoUpdate = false; holder.matrix.fromArray(attach.matrix); }
    else {
      holder.matrixAutoUpdate = true;
      holder.position.fromArray(attach.translation);
      holder.quaternion.fromArray(attach.rotation);
      holder.scale.fromArray(attach.scale);
    }
    holder.updateMatrixWorld(true);
    view.invalidate();
  }

  function drawButtons() {
    const keep = kept();
    const changed = !keep || keep.slot !== slot || JSON.stringify(fromApi(keep.nudge)) !== JSON.stringify(nudge);
    const attachJson = () => JSON.stringify({ item: item.id, kind: item.kind, slot: placement.slot, ...placement.attach, scale_applied: placement.scale }, null, 2);
    put(saveRow,
      btn(keep ? 'Update what is kept' : 'Keep this', 'save', safe(keepIt), { cls: 'primary small', disabled: !placement || (!changed && !!keep),
        title: 'Remembers this slot and nudge for this character. Nothing is put on the character.' }),
      keep ? btn('Forget it', 'trash', safe(forgetIt), { cls: 'small' }) : null,
      placement ? h('a', { class: 'btn small', href: `/api/items/${item.id}/worn/${npcId}.glb?${query(slot, nudge)}`, download: `${npcId}-${item.id}.glb`,
        title: 'The character’s model with the item added under its bone' }, 'Download the character with it') : null,
      placement ? btn('Download the attach data', null, () => {
        const link = h('a', { href: URL.createObjectURL(new Blob([attachJson()], { type: 'application/json' })), download: `${item.id}-attach.json` });
        document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(link.href), 4000);
      }, { cls: 'small', title: 'The bone and the transform of the item’s origin in the bone’s space, to attach the item in your game' }) : null,
      keep ? h('span', { class: 'hint' }, `Kept ${new Date(keep.created).toLocaleString()}`) : null);
  }

  async function keepIt() {
    item = await api('PUT', `/api/items/${item.id}/equips/${npcId}`, { slot, nudge: toApi(nudge) });
    onItem?.(item);
    drawButtons();
    toast('Kept for this character.');
  }

  async function forgetIt() {
    item = await api('DELETE', `/api/items/${item.id}/equips/${npcId}`);
    onItem?.(item);
    drawButtons();
    toast('Forgotten.');
  }

  start().catch(() => {});
  return {
    element,
    /** The item changed (saved or made again): a new model is shown, placed again. */
    update(next) {
      item = next;
      const build = modelOf().build || modelOf().created;
      if (view && npcId && itemBuild !== build) { holder?.removeFromParent(); holder = null; place(false).catch(() => {}); }
      drawButtons();
    },
    destroy() { gone = true; detach(); view?.destroy(); view = null; if (holder) disposeTree(holder); },
  };
}
