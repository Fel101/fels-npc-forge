import { h } from './core.js';

// The glasses designer: every control for one pair, drawn from the tables the server sends in /api/config (items.glasses, from
// data/glasses.json, so the ranges here are the ranges the server checks). The recipe is changed in place; the page that owns it
// hears about each change (to redraw the live preview and its unsaved-changes note).

const OPEN = new Set(['style', 'lenses']);               // which sections stay open when the controls are drawn again
const near = (a, b) => (typeof a === 'number' && typeof b === 'number' ? Math.abs(a - b) < 1e-6 : String(a).toLowerCase() === String(b).toLowerCase());

/** The recipe a new pair of this kind starts as: the defaults, the kind's style, then the kind's own lenses (what the server works out too). */
export function recipeFor(cat, kind) {
  const preset = cat.presets.find(p => p.id === kind.preset) || cat.presets[0];
  const recipe = { ...cat.defaults, ...preset.recipe, ...(kind.recipe || {}), preset: preset.id };
  const tint = cat.tints[recipe.lens_tint] || cat.tints.clear;
  if (!kind.recipe?.lens_color && recipe.lens_tint !== 'custom') recipe.lens_color = tint.hex;
  if (kind.recipe?.lens_opacity === undefined) recipe.lens_opacity = tint.opacity;
  return recipe;
}

/** A style replaces the shape, frame and arms; the lenses, the pose and the scale stay as they are. */
export function applyPreset(cat, recipe, id) {
  const preset = cat.presets.find(p => p.id === id);
  if (preset) Object.assign(recipe, preset.recipe, { preset: id });
}

/** A tint sets the lens's colour and darkness (and a glass finish, not a mirror). */
export function applyTint(cat, recipe, id) {
  const tint = cat.tints[id];
  Object.assign(recipe, { lens_tint: id, lens_finish: 'glass', lens_opacity: tint.opacity });
  if (id !== 'custom') recipe.lens_color = tint.hex;
}

/** Another kind of glasses (sunglasses for glasses) brings its own lenses; a pair that is not made yet starts again from the kind's style too. */
export function applyKind(cat, recipe, kind, restyle) {
  if (restyle) applyPreset(cat, recipe, kind.preset);
  applyTint(cat, recipe, kind.recipe?.lens_tint || 'clear');
}

const shown = (value, step) => Number(value).toFixed(String(step).includes('.') ? String(step).split('.')[1].length : 0);

/** The controls for `recipe`. Returns {element, redraw}: redraw() draws them again after the recipe was changed from outside. */
export function glassesDesigner(cat, recipe, onChange) {
  const box = h('div', { class: 'g-designer' });
  const set = (key, value, redraw = false) => { recipe[key] = value; onChange(key); if (redraw) draw(); };
  const row = (label, control) => h('div', { class: 'g-row g-pick' }, h('span', { class: 'g-label' }, label), control);

  const slider = key => {
    const f = cat.fields[key];
    const range = h('input', { type: 'range', min: f.min, max: f.max, step: f.step, value: recipe[key], 'aria-label': f.label });
    const number = h('input', { type: 'number', class: 'g-num', min: f.min, max: f.max, step: f.step, value: shown(recipe[key], f.step),
      'aria-label': `${f.label} (number)` });
    range.addEventListener('input', () => { number.value = shown(range.value, f.step); set(key, Number(range.value)); });
    number.addEventListener('change', () => {
      const value = Math.min(f.max, Math.max(f.min, Number(number.value)));
      if (Number.isNaN(value)) { number.value = shown(recipe[key], f.step); return; }
      number.value = shown(value, f.step); range.value = value; set(key, value);
    });
    return h('div', { class: 'g-row' }, h('span', { class: 'g-label' }, f.label), range, number, h('span', { class: 'g-unit' }, f.unit));
  };

  const choice = (key, label, redraw = true, also) => {
    const element = h('select', { 'aria-label': label, onchange: event => { also?.(event.target.value); set(key, event.target.value, redraw); } },
      Object.entries(cat.choices[key]).map(([id, text]) => h('option', { value: id }, text)));
    element.value = recipe[key];
    return row(label, element);
  };

  const toggle = (key, label, hint, redraw = false) => h('label', { class: 'check g-check' },
    h('input', { type: 'checkbox', checked: !!recipe[key], onchange: event => set(key, event.target.checked, redraw) }),
    h('span', {}, h('span', { class: 'name' }, label), hint && h('span', { class: 'hint', style: { display: 'block' } }, hint)));

  const swatches = (list, current, pick, custom) => h('div', { class: 'g-swatches', role: 'group' },
    list.map(c => h('button', { type: 'button', class: 'g-swatch', style: { background: c.hex }, title: c.label, 'aria-label': c.label,
      'aria-pressed': String(String(current).toLowerCase() === c.hex.toLowerCase()), onclick: () => pick(c) })), custom);
  const colourInput = (value, label, pick) => h('input', { type: 'color', class: 'g-colour', value, title: label, 'aria-label': label,
    oninput: event => pick(event.target.value) });

  const section = (id, title, ...content) => {
    const element = h('details', { class: 'g-section', open: OPEN.has(id) }, h('summary', {}, title), h('div', { class: 'stack g-body' }, content));
    element.addEventListener('toggle', () => { if (element.open) OPEN.add(id); else OPEN.delete(id); });
    return element;
  };

  function presetRow() {
    const matching = cat.presets.find(p => p.id === recipe.preset && Object.entries(p.recipe).every(([k, v]) => near(recipe[k], v)));
    const element = h('select', { 'aria-label': 'Style', onchange: event => { if (event.target.value !== 'custom') { applyPreset(cat, recipe, event.target.value); onChange('preset'); draw(); } } },
      ...cat.presets.map(p => h('option', { value: p.id }, p.label)), h('option', { value: 'custom', disabled: true }, 'Custom (changed by hand)'));
    element.value = matching ? matching.id : 'custom';
    return row('Style', element);
  }

  function lensTint() {
    const mirror = recipe.lens_finish === 'mirror';
    const tints = Object.entries(cat.tints).filter(([id]) => id !== 'custom').map(([id, t]) => ({ id, label: t.label, hex: t.hex }));
    const pickTint = c => { applyTint(cat, recipe, c.id); onChange('lens_tint'); draw(); };
    const pickCustom = value => { if (recipe.lens_tint !== 'custom') recipe.lens_opacity = Math.max(recipe.lens_opacity, 0.5); Object.assign(recipe, { lens_tint: 'custom', lens_color: value }); onChange('lens_color'); };
    return [
      row('Finish', (() => {
        const element = h('select', { 'aria-label': 'Lens finish', onchange: event => {
          if (event.target.value === 'mirror') Object.assign(recipe, { lens_finish: 'mirror', lens_tint: 'custom', lens_color: cat.mirrors[0].hex, lens_opacity: 1 });
          else applyTint(cat, recipe, recipe.lens_tint === 'custom' ? 'grey' : recipe.lens_tint);
          onChange('lens_finish'); draw();
        } }, Object.entries(cat.choices.lens_finish).map(([id, text]) => h('option', { value: id }, text)));
        element.value = recipe.lens_finish;
        return element;
      })()),
      mirror
        ? row('Mirror', swatches(cat.mirrors, recipe.lens_color, c => { Object.assign(recipe, { lens_tint: 'custom', lens_color: c.hex, lens_opacity: 1 }); onChange('lens_color'); draw(); },
          colourInput(recipe.lens_color, 'Custom mirror colour', pickCustom)))
        : row('Tint', swatches(tints, recipe.lens_tint === 'custom' ? '' : cat.tints[recipe.lens_tint]?.hex, pickTint,
          colourInput(recipe.lens_color, 'Custom lens colour', pickCustom))),
      mirror ? null : slider('lens_opacity'),
      mirror ? null : toggle('lens_gradient', 'Darker at the top', 'A gradient lens, like a driver’s sunglasses.'),
    ];
  }

  function draw() {
    const metal = recipe.frame_material === 'metal';
    const rimless = recipe.rim === 'rimless';
    box.replaceChildren(
      section('style', 'Style and shape', presetRow(), choice('shape', 'Lens shape', false), choice('rim', 'Rim'), choice('bridge_style', 'Bridge', false)),
      section('lenses', 'Lenses', slider('lens_w'), slider('lens_h'), slider('bridge'), slider('base_curve'), ...lensTint()),
      section('frame', 'Frame', choice('frame_material', 'Material', true, value => Object.assign(recipe, cat.material_looks[value])),
        row('Colour', swatches(cat.colours[metal ? 'metal' : 'plastic'], recipe.frame_color, c => { set('frame_color', c.hex, true); },
          colourInput(recipe.frame_color, 'Custom frame colour', value => set('frame_color', value)))),
        rimless ? null : slider('rim_w'), rimless ? null : slider('rim_d'), recipe.rim === 'browline' ? slider('brow_w') : null,
        slider('bridge_height'), slider('wrap'),
        toggle('pads', 'Nose pads', 'Small pads on arms, as wire frames have; plastic frames rest on the nose themselves.')),
      section('arms', 'Arms', slider('temple_len'), slider('temple_h'), slider('temple_w'), slider('temple_drop'), slider('temple_bend'), slider('splay'), slider('hinge_y')),
      section('extras', 'Pose and size', choice('pose', 'Pose', false), slider('scale')));
  }
  draw();
  return { element: box, redraw: draw };
}
