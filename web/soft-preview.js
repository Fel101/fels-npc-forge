import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { clone } from 'three/addons/utils/SkeletonUtils.js';
import { h } from './core.js';
import { View, disposeTree } from './tryon-view.js';
import { SoftChain, chainSpec, firmness, scaledTurn, stepsDue } from './soft-solver.js';

// The live jiggle preview of the Designed body card. The server (GET /api/npcs/<id>/body-3d/soft-preview.glb) sends the character's animated body with the spring bones of all four soft-body
// parts built in at full strength; this plays one of its 85 clips on two copies of it side by side, the left copy as the animation made it and the right copy with the spring bones moved by
// the engine's own arithmetic (web/soft-solver.js, checked against recordings of Godot 4.7.2). The sliders choose how much of each part shows: a body built with a smaller share of the skin
// on a spring bone moves about as the full-strength body with the bone turned that much less, which is what is done here, live, with nothing sent to the server. It changes nothing on the
// character: "Use these numbers" only puts them on the card, and the body is made again from there.
//
// The simulation runs in fixed steps of 1 / (the game's frame rate), whatever this screen's refresh rate, because Godot's simulator uses the frame's own time step (so the same body moves more
// at a low frame rate and less at a high one): the frame rate is a choice on the panel. Hair and cloth chains in the file are left still, and nothing stops one part passing into another.

const SLIDERS = [
  ['intensity', 'Intensity', 'How much of the skin around the chosen parts follows the spring bones. Each part’s slider takes its share of this.'],
  ['chest', 'Chest', 'The chest’s share of the intensity.'],
  ['butt', 'Butt', 'The butt’s share of the intensity.'],
  ['belly', 'Belly', 'The belly’s share of the intensity.'],
  ['thighs', 'Thighs', 'The thighs’ share of the intensity.'],
  ['softness', 'Softness', 'How loosely the springs swing: firm (0) is small and quick, soft (100) is bigger and slower; 50 is as suggested.'],
];
const FEATURED = ['Jog_Fwd_Loop', 'Walk', 'Sprint_Loop', 'Dance_Loop', 'Idle', 'Walk_Formal_Loop', 'Jump_Loop', 'Roll', 'Sword_Heavy_Combo'];
const RATES = [[30, '30 a second'], [60, '60 a second'], [120, '120 a second']];
const SPEEDS = [[1, 'Normal'], [0.5, 'Half'], [0.25, 'Quarter']];
const SIDE_BY_SIDE = 0.38;                                          // metres each copy stands from the middle when both are shown

/** Where the camera looks for each button: a part of the body to look at (a landmark of the loaded body), from which way round, and how far (as a share of the body's height). */
const LOOKS = [
  { id: 'whole', label: 'Whole', at: m => m.height * 0.55, turn: 0, far: 1.85, near: 1.7, wide: 1.5, narrow: 0.9 },
  { id: 'chest', label: 'Chest', at: m => m.chest, turn: 0, far: 1.0, near: 0.55, wide: 1.2, narrow: 0.7 },
  { id: 'belly', label: 'Belly', at: m => m.belly, turn: 0, far: 1.05, near: 0.6, wide: 1.2, narrow: 0.7 },
  { id: 'hips', label: 'Hips', at: m => m.hips, turn: Math.PI, far: 1.2, near: 0.62, wide: 1.2, narrow: 0.7 },
  { id: 'thighs', label: 'Thighs', at: m => m.thigh, turn: 0, far: 1.15, near: 0.62, wide: 1.2, narrow: 0.7 },
  { id: 'side', label: 'Side', at: m => m.height * 0.55, turn: Math.PI / 2, far: 1.85, near: 1.7, wide: 1.5, narrow: 0.9 },
];

const STYLE = `
.g-softprev { padding: 12px; }
.g-softprev .sp-top { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; }
.g-softprev .sp-top h4 { margin: 0; }
.g-softprev .sp-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 400px), 1fr)); gap: 16px; align-items: start; }
.g-softprev .g-tryon-canvas { height: min(62vh, 520px); min-height: 300px; }
.g-softprev .sp-status { min-height: 1.4em; }
.g-softprev .sp-controls select { width: auto; max-width: 100%; }
.g-softprev .sp-clip { min-width: 12em; }
.g-softprev .sp-sliders { gap: 6px; }
.g-softprev .sp-limits { margin-top: 8px; }
.g-softprev .sp-views { flex-wrap: wrap; justify-content: flex-end; max-width: calc(100% - 16px); }
.g-softprev .sp-views button[aria-pressed="true"] { background: var(--accent-soft); color: var(--accent); box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--accent) 55%, transparent); }
`;
function addStyle() {
  if (document.getElementById('soft-preview-style')) return;
  const style = document.createElement('style');
  style.id = 'soft-preview-style';
  style.textContent = STYLE;
  document.head.append(style);
}

const loader = new GLTFLoader();
const percent = value => `${Math.round(value * 100)} %`;

/** A name on a card that always faces the camera. */
function nameplate(text) {
  const canvas = document.createElement('canvas');
  canvas.width = 640;
  canvas.height = 96;
  const g = canvas.getContext('2d');
  g.fillStyle = 'rgba(20, 24, 32, 0.62)';
  g.beginPath();
  g.roundRect(4, 8, 632, 80, 40);
  g.fill();
  g.font = '600 44px system-ui, -apple-system, "Segoe UI", sans-serif';
  g.textAlign = 'center';
  g.textBaseline = 'middle';
  g.fillStyle = '#ffffff';
  g.fillText(text, 320, 50);
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  const sprite = new THREE.Sprite(new THREE.SpriteMaterial({ map: texture, depthTest: false, transparent: true }));
  sprite.scale.set(0.5, 0.5 * 96 / 640, 1);
  sprite.renderOrder = 10;
  return sprite;
}

/** One copy of the body: its own skeleton and animation mixer. The copy that jiggles also holds the solver for each soft chain in the file. */
class Figure {
  constructor(scene, x, jiggles) {
    this.root = clone(scene);
    this.holder = new THREE.Group();
    this.holder.position.x = x;
    this.holder.add(this.root);
    this.mixer = new THREE.AnimationMixer(this.root);
    this.chains = [];
    this.skinned = [];
    this.root.traverse(node => { if (node.isSkinnedMesh) { node.frustumCulled = false; this.skinned.push(node); } });
    const bones = this.skinned[0]?.skeleton.bones || [];
    this.space = (bones.find(bone => !bone.parent?.isBone) || bones[0])?.parent || this.root;          // (what the engine calls the skeleton's space: where the root bone hangs)
    this.inverse = new THREE.Matrix4();
    this.product = new THREE.Matrix4();
    if (jiggles) this.findChains();
  }

  findChains() {
    const blocks = [];
    this.root.traverse(node => { if (node.userData?.forge_secondary) blocks.push(node.userData.forge_secondary); });
    for (const block of blocks) {
      const pair = (block.bones || []).map(name => this.root.getObjectByName(name));
      const parent = this.root.getObjectByName(block.parent);
      const rest = bone => ({ t: bone.position.toArray(), q: bone.quaternion.toArray() });
      const spec = pair.length === 2 && pair.every(Boolean) && parent ? chainSpec(block, rest(pair[0]), rest(pair[1])) : null;
      if (spec) this.chains.push({ kind: block.kind, name: block.chain, parent, bones: pair, solver: new SoftChain(spec), stiffness: spec.stiffness, amount: Number(block.amount) || 1, last: null, scale: 0 });
    }
  }

  play(clip) {
    this.mixer.stopAllAction();
    this.action = this.mixer.clipAction(clip);
    this.action.reset().play();
    this.mixer.update(0);
    this.resetChains();
  }

  resetChains() {
    for (const chain of this.chains) { chain.solver.reset(); chain.last = null; this.put(chain); }
  }

  /** The animation moves on by `dt` seconds; the spring bones follow (the engine's step: the parent bones are posed first, then each chain is moved from where its parent now is). */
  tick(dt, jiggle) {
    this.mixer.update(dt);
    if (!jiggle || !this.chains.length) return;
    this.holder.updateWorldMatrix(true, true);                       // (everything from the scene down, once, so that the parent bones and the skeleton's space come from the same moment)
    this.inverse.copy(this.space.matrixWorld).invert();
    for (const chain of this.chains) {                                // (a part at nothing is stepped too: its bones show nothing, and show the movement as it is the moment its slider is raised)
      const e = this.product.multiplyMatrices(this.inverse, chain.parent.matrixWorld).elements;
      chain.last = chain.solver.step({ R: [e[0], e[4], e[8], e[1], e[5], e[9], e[2], e[6], e[10]], t: [e[12], e[13], e[14]] }, dt);
      this.put(chain);
    }
  }

  /** The chain's two bones as the last step left them, kept to the share the sliders ask for (nothing at rest, a smaller share a smaller turn). */
  put(chain) {
    const [first, second] = chain.bones, solver = chain.solver;
    const share = chain.scale / chain.amount;
    if (!chain.last || share <= 0) {
      first.quaternion.fromArray(solver.rest1);
      second.quaternion.fromArray(solver.rest2);
    } else {
      first.quaternion.fromArray(scaledTurn(solver.rest1, chain.last.q1, share));
      second.quaternion.fromArray(scaledTurn(solver.rest2, chain.last.q2, share));
    }
  }

  /** `values` are the slider numbers (0 to 1); `restSoftness` the softness the file was made at. */
  setValues(values, restSoftness) {
    for (const chain of this.chains) {
      chain.scale = (values[chain.kind] ?? 0) * values.intensity;
      chain.solver.stiffness = chain.stiffness * firmness(values.softness) / firmness(restSoftness);
      this.put(chain);
    }
  }
}

/** The 3D view: the camera stays in front and each body turns on the spot, so the two stay side by side whichever way you look at them. */
class SoftView extends View {
  constructor(canvas) {
    super(canvas);
    this.camera.fov = 36;
    this.camera.updateProjectionMatrix();
    this.spinning = [];                                            // the groups that turn (each body's holder)
    this.invalidate();
  }

  reset(theta) {
    this.theta = this.spin ?? theta;
    this.phi = Math.PI / 2 - 0.04;
    this.radius = this.distance ?? 2.5;
    this.target = (this.focus || new THREE.Vector3()).clone();
    this.invalidate?.();
  }

  /** Look from the front at height `y`, from `distance` metres, with the bodies turned by `spin` radians. */
  look(y, distance, spin) {
    this.focus = new THREE.Vector3(0, y, 0);
    this.distance = distance;
    this.spin = spin;
    this.reset(spin);
  }

  draw() {
    if (!this.canvas.isConnected) return;                           // (the card is not on show: nothing is drawn or stepped, and the view wakes when it is put back, the size observer says so)
    this.onFrame?.();
    const theta = this.theta;
    for (const group of this.spinning) group.rotation.y = -theta;
    this.theta = 0;
    try { super.draw(); } finally { this.theta = theta; }
    if (this.playing) this.invalidate();
  }

  resize() {
    super.resize();
    this.onResize?.();
  }

  destroy() {
    const renderer = this.renderer;
    super.destroy();
    renderer?.forceContextLoss?.();                                 // (a browser keeps only a handful of WebGL contexts: give this one back at once)
  }
}

/** The panel. `options`: { npcId, stamp (changes when the body is made again), values (the card's six numbers, 0 to 1), canUse (false when the numbers cannot go on the card), onUse(values), onClose() }.
 *  Returns { element, destroy(), state() }; state() is a plain look at what the panel is doing (for checks). */
export function softPreviewPanel(options) {
  addStyle();
  const values = { ...options.values };
  const start = { ...options.values };
  const state = { playing: true, fps: 60, speed: 1, jiggle: true, compare: true, look: 'whole', clip: '', accumulated: 0, last: 0, restSoftness: 0.5, ready: false, ticks: 0 };
  let view = null, figures = [], clips = [], measures = null, gone = false, left = null, right = null, plates = [], disc = null, source = null;

  const canvas = h('canvas', { class: 'g-tryon-canvas', role: 'img', 'aria-label': 'The body moving, in 3D: the animation alone on the left, with the spring bones on the right. Drag to turn, scroll to zoom.' });
  const status = h('p', { class: 'hint sp-status', 'aria-live': 'polite' }, 'Loading the body…');
  const clipPicker = h('select', { class: 'sp-clip', 'aria-label': 'Animation clip', disabled: true, onchange: event => chooseClip(event.target.value) });
  const play = h('button', { type: 'button', class: 'btn small', onclick: () => setPlaying(!state.playing) }, 'Pause');
  const slow = h('select', { 'aria-label': 'Speed', onchange: event => { state.speed = Number(event.target.value); } }, SPEEDS.map(([value, label]) => h('option', { value }, label)));
  const rate = h('select', { 'aria-label': 'Frame rate of the game', title: 'The game engine moves the spring bones once a frame, so how much they move depends on the frame rate of the game: more at a low rate, less at a high one.',
    onchange: event => { state.fps = Number(event.target.value); state.accumulated = 0; resetChains(); } }, RATES.map(([value, label]) => h('option', { value, selected: value === state.fps }, label)));
  const looks = h('div', { class: 'g-tryon-views row sp-views' }, LOOKS.map(look => h('button', { type: 'button', class: 'btn small', 'aria-pressed': 'false', 'data-look': look.id, onclick: () => lookAt(look.id) }, look.label)));
  const numbers = {};

  const slider = ([key, label, note]) => {
    const range = h('input', { type: 'range', min: 0, max: 100, step: 5, value: Math.round(values[key] * 100), 'aria-label': label, title: note });
    const shown = h('span', { class: 'g-label', style: { textAlign: 'right' } }, percent(values[key]));
    range.addEventListener('input', () => { values[key] = Number(range.value) / 100; shown.textContent = percent(values[key]); changed(); });
    numbers[key] = { range, shown };
    return h('div', { class: 'g-row', title: note }, h('span', { class: 'g-label' }, label), range, shown, h('span', {}));
  };
  const check = (label, note, checked, onchange) => h('label', { class: 'check', title: note },
    h('input', { type: 'checkbox', checked, 'aria-label': label, onchange: event => onchange(event.target.checked) }), h('span', {}, h('span', { class: 'name' }, label)));

  const useButton = h('button', { type: 'button', class: 'btn small primary', disabled: !options.canUse, title: options.canUse ? 'Put these six numbers on the card’s Soft-body motion sliders (the body is made again from there)' : 'These numbers cannot go on the card right now',
    onclick: () => options.onUse?.({ ...values }) }, 'Use these numbers on the card');
  const backButton = h('button', { type: 'button', class: 'btn small', title: 'Move the sliders back to the numbers the card had when this was opened',
    onclick: () => { Object.assign(values, start); sync(); changed(); } }, 'Back to the card’s numbers');
  const closeButton = h('button', { type: 'button', class: 'btn small', onclick: () => options.onClose?.() }, 'Close');

  const element = h('div', { class: 'model3d-interactive g-softprev' },
    h('div', { class: 'sp-top' }, h('div', {}, h('h4', {}, 'Preview the jiggle'),
      h('p', { class: 'hint' }, 'The body’s animation clips played with the spring maths of the game engine, so you can see how much each part moves before you make the body again. Nothing here changes the character.')), closeButton),
    h('div', { class: 'sp-grid' },
      h('div', { class: 'stack' }, h('div', { class: 'g-tryon-view' }, canvas, looks), status),
      h('div', { class: 'stack sp-controls' },
        h('div', { class: 'row wrap' }, h('label', { class: 'row' }, h('span', { class: 'hint' }, 'Clip'), clipPicker), play),
        h('div', { class: 'row wrap' }, h('label', { class: 'row' }, h('span', { class: 'hint' }, 'Speed'), slow), h('label', { class: 'row' }, h('span', { class: 'hint' }, 'Game frame rate'), rate)),
        h('div', { class: 'row wrap' }, check('Jiggle', 'Switch the spring bones off to see the animation alone', true, on => { state.jiggle = on; resetChains(); view?.invalidate(); }),
          check('Beside the animation alone', 'Show a second body that moves without the spring bones', true, on => { state.compare = on; arrange(); })),
        h('div', { class: 'stack sp-sliders' }, SLIDERS.map(slider)),
        h('div', { class: 'row wrap' }, useButton, backButton),
        h('p', { class: 'hint' }, 'Left, the animation alone; right, with the spring bones. Drag to turn, scroll to zoom, shift-drag to move. The sliders are the card’s: intensity, the share of each part, and softness.'))),
    h('p', { class: 'hint sp-limits' }, 'How close this is to the game: the movement is the engine’s own arithmetic (checked against recordings of Godot 4.7.2), but a game sets its own frame rate, and the same body moves more at a low one '
      + 'and less at a high one (choose it above). The sliders scale the movement of a body that has every part at full strength; a body built with that share of the skin moves a little differently at the extremes. '
      + 'Nothing stops one part passing into another, and hair and cloth are not shown.'));
  element.softPreview = { state: () => ({ ...state, values: { ...values }, measures, chains: chainReport() }), tick: count => manualTicks(count), set: next => { Object.assign(values, next); sync(); changed(); }, look: id => lookAt(id),
    figures: () => figures, frame: () => frame(), play: on => setPlaying(on), view: () => view,
    render: () => { const started = performance.now(); view.draw(); view.renderer.getContext().finish(); return performance.now() - started; } };

  // ---- what the sliders and buttons do -----------------------------------------------------------------------------------------------------------
  function sync() {
    for (const [key, { range, shown }] of Object.entries(numbers)) { range.value = Math.round(values[key] * 100); shown.textContent = percent(values[key]); }
  }
  function changed() {
    for (const figure of figures) figure.setValues(values, state.restSoftness);
    view?.invalidate();
  }
  function resetChains() {
    for (const figure of figures) figure.resetChains();
    changed();
  }
  function setPlaying(on) {
    state.playing = on;
    play.textContent = on ? 'Pause' : 'Play';
    state.last = 0;
    if (view) { view.playing = on; view.invalidate(); }
  }
  function chooseClip(name) {
    state.clip = name;
    const clip = clips.find(item => item.name === name);
    if (!clip) return;
    for (const figure of figures) figure.play(clip);
    changed();
    state.accumulated = 0;
    view?.invalidate();
  }
  function arrange() {
    if (!left || !right) return;
    left.holder.visible = state.compare;
    right.holder.position.x = state.compare ? SIDE_BY_SIDE : 0;
    left.holder.position.x = -SIDE_BY_SIDE;
    plates[0].visible = state.compare;
    plates[1].visible = state.compare;
    disc.position.x = 0;
    lookAt(state.look);
  }
  function lookAt(id) {
    const look = LOOKS.find(item => item.id === id) || LOOKS[0];
    state.look = look.id;
    for (const button of looks.children) button.setAttribute('aria-pressed', String(button.dataset.look === look.id));
    if (!view || !measures) return;
    const aspect = Math.max(0.2, view.canvas.clientWidth / Math.max(1, view.canvas.clientHeight));
    view.camera.fov = aspect < 0.8 ? 50 : 36;                       // (a wider lens for a picture that is taller than it is wide, so the camera need not go farther than the zoom lets it)
    view.camera.updateProjectionMatrix();
    const across = (state.compare ? look.wide : look.narrow) / (2 * Math.tan(THREE.MathUtils.degToRad(view.camera.fov / 2)) * aspect);       // (far enough back for the bodies to be as wide as the picture: a phone held upright is narrow)
    state.aspect = aspect;
    view.look(look.at(measures), Math.max(measures.height * (state.compare ? look.far : look.near), across), look.turn);
  }

  /** The animation and the spring bones move on by what the clock says, in whole steps of the game's frame time. */
  function frame() {
    if (!state.playing || !state.ready) return;
    const now = performance.now();
    const due = stepsDue(state.accumulated, state.last ? (now - state.last) / 1000 : 0, state.fps);
    state.last = now;
    state.accumulated = due.accumulated;
    for (let i = 0; i < due.steps; i++) advance(state.speed / state.fps);
  }
  function advance(dt) {
    state.ticks++;
    for (const figure of figures) figure.tick(dt, figure === right && state.jiggle);
  }
  function manualTicks(count) {
    for (let i = 0; i < count; i++) advance((1 / state.fps) * state.speed);
    view?.invalidate();
    return state.ticks;
  }
  function chainReport() {
    return (right?.chains || []).map(chain => ({ name: chain.name, scale: chain.scale, clamped: !!chain.last?.clamped,
      turn1: chain.bones[0].quaternion.angleTo(new THREE.Quaternion().fromArray(chain.solver.rest1)) * 180 / Math.PI,
      turn2: chain.bones[1].quaternion.angleTo(new THREE.Quaternion().fromArray(chain.solver.rest2)) * 180 / Math.PI }));
  }

  // ---- loading the body -----------------------------------------------------------------------------------------------------------------------
  async function load() {
    try {
      const response = await fetch(`/api/npcs/${options.npcId}/body-3d/soft-preview.glb?v=${encodeURIComponent(options.stamp || '')}`, { cache: 'no-store' });
      if (!response.ok) {
        const said = await response.json().catch(() => ({}));
        throw new Error(said.error || `The server answered with code ${response.status}.`);
      }
      status.textContent = 'Reading the body…';
      const buffer = await response.arrayBuffer();
      const gltf = await new Promise((done, fail) => loader.parse(buffer, '', done, fail));
      if (gone) { disposeTree(gltf.scene); return; }
      start3d(gltf);
    } catch (error) {
      if (!gone) status.textContent = `The preview could not be made: ${error.message || error}`;
    }
  }

  function start3d(gltf) {
    try { view = new SoftView(canvas); } catch { status.textContent = 'This browser cannot draw 3D (WebGL is off).'; return; }
    source = gltf.scene;
    view.onFrame = frame;
    view.onResize = () => { const aspect = view.canvas.clientWidth / Math.max(1, view.canvas.clientHeight); if (measures && Math.abs(aspect / (state.aspect || aspect) - 1) > 0.1) lookAt(state.look); };
    view.resize();
    left = new Figure(source, -SIDE_BY_SIDE, false);
    right = new Figure(source, SIDE_BY_SIDE, true);
    figures = [left, right];
    state.restSoftness = Number(gltf.asset?.extras?.forge_soft?.softness ?? 0.5);
    const box = new THREE.Box3();
    for (const mesh of right.skinned) box.expandByObject(mesh);
    const world = name => right.root.getObjectByName(name)?.getWorldPosition(new THREE.Vector3());
    right.root.updateMatrixWorld(true);
    const height = box.max.y - box.min.y || 1.6;
    measures = { height, chest: world('soft_chest_l_02')?.y ?? height * 0.74, belly: world('soft_belly_c_02')?.y ?? height * 0.6, hips: world('soft_butt_l_02')?.y ?? height * 0.52,
      thigh: world('soft_thighs_l_02')?.y ?? height * 0.45 };
    const floor = new THREE.Mesh(new THREE.CircleGeometry(0.85, 48), new THREE.MeshBasicMaterial({ color: 0x808899, transparent: true, opacity: 0.16 }));
    floor.rotation.x = -Math.PI / 2;
    floor.position.y = box.min.y + 0.002;
    disc = floor;
    plates = [nameplate('Animation alone'), nameplate('With the spring bones')];
    plates[0].position.set(-SIDE_BY_SIDE, box.max.y + 0.12, 0);
    plates[1].position.set(SIDE_BY_SIDE, box.max.y + 0.12, 0);
    view.scene.add(floor, left.holder, right.holder, ...plates);
    view.spinning = [left.holder, right.holder];
    clips = gltf.animations || [];
    const names = clips.map(clip => clip.name);
    const first = FEATURED.filter(name => names.includes(name));
    const rest = names.filter(name => !first.includes(name));
    clipPicker.replaceChildren(...first.map(name => h('option', { value: name }, name)), ...(first.length && rest.length ? [h('option', { disabled: true }, '──────────')] : []), ...rest.map(name => h('option', { value: name }, name)));
    clipPicker.disabled = !names.length;
    state.ready = true;
    const soft = right.chains.length;
    status.textContent = soft ? `${names.length} clips. ${soft} spring-bone chains are moving.` : 'This file has no soft-body spring bones to move.';
    arrange();
    chooseClip(first[0] || names[0] || '');
    clipPicker.value = state.clip;
    changed();
    view.playing = true;
    view.invalidate();
  }

  load();
  return {
    element,
    state: element.softPreview.state,
    destroy() {
      gone = true;
      view?.destroy();
      disposeTree(source);
      for (const figure of figures) disposeTree(figure.root);
      for (const plate of plates) { plate.material.map?.dispose(); plate.material.dispose(); }
      disc?.geometry.dispose();
      disc?.material.dispose();
      element.remove();
    },
  };
}
