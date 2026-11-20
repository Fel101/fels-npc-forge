import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { api, btn, debounce, h, safe, toast } from './core.js';

// Try a pair of glasses on a character: the character's model and the pair are shown together, the pair placed where the server fits it
// (npcgen/facefit.py: from the eyes and the shape of the face, which the server finds once per character and keeps), with sliders to nudge it
// and buttons to keep the fit or download the character wearing them. Only the placement comes from the server: the nudges are sent as they
// change and the answer moves the pair, so there is one implementation of the fitting.

const NUDGES = [
  { key: 'up', label: 'Up or down', unit: 'mm', min: -20, max: 20, step: 0.5, limits: [-40, 40] },
  { key: 'forward', label: 'Forward or back', unit: 'mm', min: -20, max: 20, step: 0.5, limits: [-40, 40] },
  { key: 'tilt', label: 'Tilt (top forward)', unit: 'deg', min: -20, max: 20, step: 0.5, limits: [-30, 30] },
  { key: 'size', label: 'Size', unit: '×', min: 0.6, max: 1.6, step: 0.01, limits: [0.5, 2] },
];
const NEUTRAL = { up: 0, forward: 0, tilt: 0, size: 1 };
const VIEWS = [['Front', 0], ['Three-quarter', 0.6], ['Side', Math.PI / 2]];
const loader = new GLTFLoader();
const shown = (value, step) => Number(value).toFixed(String(step).includes('.') ? String(step).split('.')[1].length : 0);
const query = nudge => Object.entries(nudge).map(([key, value]) => `${key}=${value}`).join('&');
const stamp = value => encodeURIComponent(value || '');
/** replaceChildren that leaves out what is not there (it would put the word "null" on the page). */
const put = (parent, ...children) => parent.replaceChildren(...children.filter(Boolean));

export function disposeTree(root) {
  root?.traverse(node => {
    node.geometry?.dispose();
    for (const material of node.material ? [].concat(node.material) : []) {
      for (const value of Object.values(material)) if (value?.isTexture) value.dispose();
      material.dispose();
    }
  });
}

/** The three.js scene: the character, the pair on a group of its own, and a small orbit control (drag turns, shift-drag or right-drag pans,
 * the wheel zooms, a double click starts again). It draws only when something changed. */
export class View {
  constructor(canvas) {
    this.canvas = canvas;
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    this.scene = new THREE.Scene();
    this.scene.add(new THREE.HemisphereLight(0xffffff, 0x8a8f9a, 1.3));
    const sun = new THREE.DirectionalLight(0xffffff, 1.7);
    sun.position.set(-0.6, 1.2, 1.5);
    this.scene.add(sun);
    this.camera = new THREE.PerspectiveCamera(26, 1, 0.01, 50);
    this.pair = new THREE.Group();
    this.pair.visible = false;
    this.scene.add(this.pair);
    this.character = null;
    this.focus = new THREE.Vector3(0, 1.56, 0.05);
    this.unit = 1;
    this.target = this.focus.clone();
    this.frame = 0;
    this.reset(0.5);
    this.observer = new ResizeObserver(() => this.resize());
    this.observer.observe(canvas.parentElement);
    this.controls();
  }

  controls() {
    const canvas = this.canvas;
    let drag = null;
    canvas.addEventListener('pointerdown', event => {
      canvas.setPointerCapture(event.pointerId);
      drag = { x: event.clientX, y: event.clientY, pan: event.shiftKey || event.button !== 0 };
    });
    canvas.addEventListener('pointermove', event => {
      if (!drag) return;
      const dx = event.clientX - drag.x, dy = event.clientY - drag.y;
      drag.x = event.clientX; drag.y = event.clientY;
      if (drag.pan) {
        const k = this.radius * 0.0016;
        const right = new THREE.Vector3().setFromMatrixColumn(this.camera.matrixWorld, 0);
        const up = new THREE.Vector3().setFromMatrixColumn(this.camera.matrixWorld, 1);
        this.target.addScaledVector(right, -dx * k).addScaledVector(up, dy * k);
      } else {
        this.theta -= dx * 0.008;
        this.phi = Math.min(2.9, Math.max(0.25, this.phi - dy * 0.008));
      }
      this.invalidate();
    });
    const end = () => { drag = null; };
    canvas.addEventListener('pointerup', end);
    canvas.addEventListener('pointercancel', end);
    canvas.addEventListener('wheel', event => {
      event.preventDefault();
      this.radius = Math.min(3, Math.max(0.1, this.radius * Math.exp(event.deltaY * 0.0012)));
      this.invalidate();
    }, { passive: false });
    canvas.addEventListener('dblclick', () => this.reset(this.theta));
    canvas.addEventListener('contextmenu', event => event.preventDefault());
  }

  /** Look at the face again from `theta` radians round from the front. */
  reset(theta) {
    this.theta = theta;
    this.phi = Math.PI / 2 - 0.04;
    this.radius = 0.55 * this.unit;
    this.target = this.focus.clone().add(new THREE.Vector3(0, -0.015 * this.unit, 0));
    this.invalidate?.();
  }

  frameOn(focus, height) {
    this.focus.fromArray(focus);
    this.unit = (height || 1.7) / 1.7;
    this.reset(this.theta);
  }

  place(placement) {
    this.pair.position.fromArray(placement.position);
    this.pair.quaternion.fromArray(placement.rotation);
    this.pair.scale.setScalar(placement.scale);
    this.pair.visible = true;
    this.invalidate();
  }

  setCharacter(root) {
    if (this.character) { this.scene.remove(this.character); disposeTree(this.character); }
    this.character = root;
    if (root) this.scene.add(root);
    this.invalidate();
  }

  setPair(root) {
    for (const old of [...this.pair.children]) { this.pair.remove(old); disposeTree(old); }
    if (root) this.pair.add(root);
    this.invalidate();
  }

  resize() {
    const box = this.canvas.parentElement.getBoundingClientRect();
    const width = Math.max(1, Math.round(box.width)), height = Math.max(1, Math.round(this.canvas.clientHeight || box.height));
    this.renderer.setSize(width, height, false);
    this.camera.aspect = width / height;
    this.camera.updateProjectionMatrix();
    this.invalidate();
  }

  invalidate() {
    if (this.frame || !this.renderer) return;
    this.frame = requestAnimationFrame(() => { this.frame = 0; this.draw(); });
  }

  draw() {
    const r = this.radius, sinPhi = Math.sin(this.phi);
    this.camera.position.set(this.target.x + r * sinPhi * Math.sin(this.theta), this.target.y + r * Math.cos(this.phi), this.target.z + r * sinPhi * Math.cos(this.theta));
    this.camera.lookAt(this.target);
    this.camera.updateMatrixWorld();
    this.renderer.render(this.scene, this.camera);
  }

  destroy() {
    cancelAnimationFrame(this.frame);
    this.observer.disconnect();
    this.setCharacter(null);
    this.setPair(null);
    this.renderer.dispose();
    this.renderer = null;
  }
}

/** The panel for one saved pair of glasses. Returns {element, update(item), destroy()}; onItem(item) hears of a fit being kept or forgotten. */
export function tryOnPanel(first, { onItem }) {
  let item = first;
  let characters = [];
  let npcId = '';
  let status = null;                       // the face's status from the server
  let nudge = { ...NEUTRAL };
  let placement = null;
  let pairBuild = null;                    // which build of the pair is shown
  let fitSeq = 0, loadSeq = 0, pollTimer = null, view = null, gone = false;
  const sliders = {};

  const canvas = h('canvas', { class: 'g-tryon-canvas', role: 'img', 'aria-label': 'The character wearing the glasses, in 3D. Drag to turn, scroll to zoom.' });
  const message = h('p', { class: 'hint g-tryon-note', 'aria-live': 'polite' }, '');
  const report = h('div', { class: 'g-tryon-report', 'aria-live': 'polite' });          // the facts of the fit, then each note on a line of its own
  const say = (...lines) => report.replaceChildren(...lines.filter(Boolean).map(([text, kind]) => h('p', { class: `hint${kind ? ` ${kind}` : ''}` }, text)));
  const picker = h('select', { 'aria-label': 'Character', onchange: event => safe(chooseCharacter)(event.target.value) });
  const faceLine = h('p', { class: 'hint' }, '');
  const actions = h('div', { class: 'row wrap' });
  const saveRow = h('div', { class: 'row wrap model3d-actions' });
  const modelOf = () => item?.assets?.model_3d || {};
  const saved = () => (item?.fits || {})[npcId];

  const slider = spec => {
    const range = h('input', { type: 'range', min: spec.min, max: spec.max, step: spec.step, value: NEUTRAL[spec.key], 'aria-label': spec.label });
    const number = h('input', { type: 'number', class: 'g-num', min: spec.limits[0], max: spec.limits[1], step: spec.step, value: shown(NEUTRAL[spec.key], spec.step),
      'aria-label': `${spec.label} (number)` });
    const set = value => {
      const clamped = Math.min(spec.limits[1], Math.max(spec.limits[0], value));
      nudge[spec.key] = clamped;
      number.value = shown(clamped, spec.step);
      range.value = clamped;
      refitSoon();
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

  const element = h('div', { class: 'card g-tryon' },
    h('h3', {}, 'Try them on a character'),
    h('p', { class: 'hint' }, 'Pick a character that has a 3D model. The first time, NPC Forge looks at the face (about 40 seconds) to find the eyes and the nose, then puts the saved pair there ' +
      '(save your changes to see them here). Nudge it if you like and keep the fit; the download is the character with the pair on its head joint, so it follows every animation.'),
    h('div', { class: 'g-tryon-grid' },
      h('div', { class: 'g-tryon-view' }, canvas,
        h('div', { class: 'g-tryon-views row' }, VIEWS.map(([label, theta]) => h('button', { type: 'button', class: 'btn small', onclick: () => { view?.reset(theta); } }, label))),
        message),
      h('div', { class: 'stack' },
        h('div', {}, h('label', { class: 'lbl' }, 'Character'), picker),
        faceLine, actions,
        h('div', { class: 'stack g-nudges' }, NUDGES.map(slider),
          h('div', { class: 'row wrap' }, btn('Reset the nudge', 'refresh', () => { setNudge(NEUTRAL); refitSoon(); drawButtons(); }, { cls: 'small' }))),
        report, saveRow)));

  // ---- the characters and the face ------------------------------------------------------------------------------------------------------
  async function start() {
    try { characters = (await api('GET', '/api/npcs')).filter(npc => npc.has.model_3d || npc.has.rig_3d).sort((a, b) => a.name.localeCompare(b.name)); }
    catch (error) { message.textContent = error.message; return; }
    if (!characters.length) {
      picker.replaceChildren(h('option', { value: '' }, 'No character has a 3D model yet'));
      picker.disabled = true;
      faceLine.textContent = 'Make or import a 3D model for a character first (the Library has the buttons).';
      return;
    }
    picker.replaceChildren(h('option', { value: '' }, 'Choose a character…'), ...characters.map(npc => h('option', { value: npc.id }, `${npc.name}${npc.has.rig_3d ? ' (rigged)' : ''}`)));
    drawButtons();
  }

  async function chooseCharacter(id) {
    clearTimeout(pollTimer);
    npcId = id;
    placement = null;
    if (view) { view.pair.visible = false; view.invalidate(); }
    say();
    faceLine.textContent = '';
    actions.replaceChildren();
    drawButtons();
    if (!id) { view?.setCharacter(null); message.textContent = ''; return; }
    const seq = ++loadSeq;
    status = await api('GET', `/api/npcs/${id}/face`);
    if (seq !== loadSeq || gone) return;
    await showCharacter(seq);
    if (seq !== loadSeq || gone) return;
    drawFace();
    if (status.state === 'queued' || status.state === 'running') poll();
    else if (status.scan) await refit(true);
  }

  async function ensureView() {
    if (!view) {
      try { view = new View(canvas); } catch { message.textContent = 'This browser cannot draw 3D (WebGL is off).'; throw new Error('No WebGL'); }
      view.resize();
    }
    if (pairBuild !== (modelOf().build || modelOf().created)) await loadPair();
  }

  async function loadPair() {
    const model = modelOf();
    if (!model.file) return;
    const gltf = await loader.loadAsync(`/api/items/${item.id}/files/${model.file}?v=${stamp(model.build || model.created)}`);
    view.setPair(gltf.scene);
    pairBuild = model.build || model.created;
  }

  async function showCharacter(seq) {
    if (!status.model) { message.textContent = 'This character has no 3D model.'; return; }
    await ensureView();
    message.textContent = 'Loading the character…';
    const url = `/api/npcs/${npcId}/files/${status.model.file}?v=${stamp(status.model.created)}`;
    let gltf;
    try {
      gltf = await loader.loadAsync(url, event => { if (seq === loadSeq && event.total) message.textContent = `Loading the character… ${Math.round(100 * event.loaded / event.total)}%`; });
    } catch { if (seq === loadSeq) message.textContent = 'The character’s model could not be loaded.'; return; }
    if (seq !== loadSeq || gone) { disposeTree(gltf.scene); return; }
    view.setCharacter(gltf.scene);
    const box = new THREE.Box3().setFromObject(gltf.scene);
    view.frameOn([(box.min.x + box.max.x) / 2, box.min.y + (box.max.y - box.min.y) * 0.934, box.max.z * 0.5], box.max.y - box.min.y);
    message.textContent = status.scan ? '' : 'Press “Find the face” to place the glasses.';
  }

  function drawFace() {
    const scan = status.scan;
    const working = status.state === 'queued' || status.state === 'running';
    faceLine.textContent = working ? `${status.message || 'Working'}…`
      : status.state === 'error' ? `The face could not be scanned: ${status.message}`
      : scan ? [scan.source === 'sdpose' ? `Face found (score ${scan.score})` : scan.source === 'landmarks' ? 'Eyes taken from the body\'s own landmarks' : 'Placed by the usual proportions', `eyes ${scan.ipd_mm} mm apart`,
        scan.face_width_mm && `face ${scan.face_width_mm} mm wide`, ...scan.notes].filter(Boolean).join(' · ')
      : 'The face has not been looked at yet.';
    faceLine.classList.toggle('err', status.state === 'error');
    put(actions,
      working ? btn('Stop', 'x', safe(async () => { status = await api('DELETE', `/api/npcs/${npcId}/face/scan`); drawFace(); }), { cls: 'small' })
        : btn(scan ? 'Find the face again' : 'Find the face', 'eye', safe(() => scan_(scan ? { force: true } : {})), { cls: scan ? 'small' : 'primary small' }),
      working ? null : btn('Place by proportions', null, safe(() => scan_({ force: true, estimate: true })), { cls: 'small',
        title: 'For a face SDPose cannot find (a mask, a helmet, an animal): the eyes are put where this library’s characters usually have them. No GPU.' }));
    drawButtons();
  }

  async function scan_(body) {
    status = await api('POST', `/api/npcs/${npcId}/face/scan`, body);
    drawFace();
    poll();
  }

  function poll() {
    clearTimeout(pollTimer);
    const seq = loadSeq;
    pollTimer = setTimeout(async () => {
      if (gone || seq !== loadSeq) return;
      try { status = await api('GET', `/api/npcs/${npcId}/face`); } catch { poll(); return; }
      drawFace();
      if (status.state === 'queued' || status.state === 'running') poll();
      else if (status.scan) await refit(true);
    }, 1000);
  }

  // ---- the fit ------------------------------------------------------------------------------------------------------------------------------
  const refitSoon = debounce(() => safe(refit)(false), 120);

  async function refit(initial) {
    if (!npcId || !status?.scan) return;
    const seq = ++fitSeq;
    let result;
    try { result = await api('POST', `/api/items/${item.id}/fit`, initial ? { npc: npcId } : { npc: npcId, nudge }); }
    catch (error) { say([error.message, 'err']); return; }
    if (seq !== fitSeq || gone) return;
    if (initial) setNudge(result.report.nudge);                                  // (what is kept for this character, else neutral)
    placement = result;
    await ensureView();
    if (seq !== fitSeq || gone) return;
    view.place(result);
    if (initial) view.frameOn(result.focus, result.height_m);                    // (the scan knows where the eyes are better than the model's box does)
    const r = result.report;
    say([[`Scale ${r.scale}`, r.vertex_distance_mm != null && `lenses ${r.vertex_distance_mm} mm from the eyes`, r.nose_gap_mm != null && `nose gap ${r.nose_gap_mm} mm`]
      .filter(Boolean).join(' · ')], ...r.notes.map(note => [note, 'warn']));
    message.textContent = '';
    drawButtons();
  }

  function drawButtons() {
    const keep = saved();
    const changed = !keep || JSON.stringify(keep.nudge) !== JSON.stringify(nudge);
    put(saveRow,
      btn(keep ? 'Update the saved fit' : 'Keep this fit', 'save', safe(keepFit), { cls: 'primary small', disabled: !placement || (!changed && !!keep),
        title: 'Remembers this nudge for this character. The pair is fitted again to the face whenever the pair or the character changes.' }),
      keep ? btn('Forget the saved fit', 'trash', safe(forgetFit), { cls: 'small' }) : null,
      placement ? h('a', { class: 'btn small', href: `/api/items/${item.id}/worn/${npcId}.glb?${query(nudge)}`, download: `${npcId}-${item.id}.glb`,
        title: 'The character’s model with the pair added under its head joint' }, 'Download the character wearing them') : null,
      keep ? h('span', { class: 'hint' }, `Saved ${new Date(keep.created).toLocaleString()}`) : null);
  }

  async function keepFit() {
    item = await api('PUT', `/api/items/${item.id}/fits/${npcId}`, { nudge });
    onItem?.(item);
    drawButtons();
    toast('Fit kept for this character.');
  }

  async function forgetFit() {
    item = await api('DELETE', `/api/items/${item.id}/fits/${npcId}`);
    onItem?.(item);
    drawButtons();
    toast('Saved fit forgotten.');
  }

  start().catch(() => {});
  return {
    element,
    /** The item changed (saved again): a new pair is shown and fitted again. */
    update(next) {
      item = next;
      if (view && npcId && pairBuild !== (modelOf().build || modelOf().created)) loadPair().then(() => refit(false)).catch(() => {});
      drawButtons();
    },
    destroy() { gone = true; clearTimeout(pollTimer); view?.destroy(); view = null; },
  };
}
