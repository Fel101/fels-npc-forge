import { h } from './core.js';

// The clothing designer: every control for one shirt, pair of trousers, jacket or apron, drawn from the tables the server sends in /api/config (items.clothing, from
// data/clothing.json and the Body Designer's own catalogue: each kind has its own range for each control, and the Body Designer's reason where it has one; a control whose
// range is a single value is not offered, and a garment that goes over others says what it was checked over and what is not simulated).
// The recipe is changed in place; the page that owns it hears about each change (to redraw the live preview and its unsaved-changes note). It has the same
// shape as glasses-view.js: recipeFor / applyKind / designer(cat, recipe, onChange) -> {element, redraw}.

const OPEN = new Set(['colour', 'fit']);                 // which sections stay open when the controls are drawn again
const shown = (value, step) => Number(value).toFixed(String(step).includes('.') ? String(step).split('.')[1].length : 0);
const kindOf = (cat, recipe) => cat.kinds[recipe.kind] || Object.values(cat.kinds)[0];

/** The recipe a new garment of this kind starts as: the Body Designer's own starting values for it. */
export function recipeFor(cat, kind) {
  return { ...(cat.kinds[kind.id] || Object.values(cat.kinds)[0]).recipe };
}

/** Another kind of garment (trousers for a shirt) is another recipe: its own colour, looseness and cloth. */
export function applyKind(cat, recipe, kind) {
  Object.assign(recipe, recipeFor(cat, kind));
}

/** The controls for `recipe`. Returns {element, redraw}: redraw() draws them again after the recipe was changed from outside. */
export function clothingDesigner(cat, recipe, onChange) {
  const box = h('div', { class: 'g-designer' });
  const refreshers = [];                                   // what to look at again whenever a setting changes (the Body Designer's notes about a value it does not offer)
  const set = (key, value, redraw = false) => { recipe[key] = value; refreshers.forEach(refresh => refresh()); onChange(key); if (redraw) draw(); };
  const row = (label, control) => h('div', { class: 'g-row g-pick' }, h('span', { class: 'g-label' }, label), control);

  // A slider offers this kind's range. A design saved when the Body Designer offered more (a shirt of length 1.1) keeps its value: the number shows it, the slider rests at its end and
  // `reason` says so, until the length is moved; what is typed or dragged is always inside the range.
  const slider = key => {
    const f = cat.fields[key];
    const [min, max] = kindOf(cat, recipe).controls[key];
    if (!(max > min)) return [];                              // (a range of one value is nothing to choose: the Body Designer does not offer the control for this garment)
    const range = h('input', { type: 'range', min, max, step: f.step, value: recipe[key], 'aria-label': f.label });
    const number = h('input', { type: 'number', class: 'g-num', min, max, step: f.step, value: shown(recipe[key], f.step), 'aria-label': `${f.label} (number)` });
    range.addEventListener('input', () => { number.value = shown(range.value, f.step); set(key, Number(range.value)); });
    number.addEventListener('change', () => {
      const value = Math.min(max, Math.max(min, Number(number.value)));
      if (Number.isNaN(value)) { number.value = shown(recipe[key], f.step); return; }
      number.value = shown(value, f.step); range.value = value; set(key, value);
    });
    return [h('div', { class: 'g-row' }, h('span', { class: 'g-label' }, f.label), range, number, h('span', { class: 'g-unit' }, f.unit)),
      f.hint ? h('p', { class: 'hint g-hint' }, f.hint) : null];
  };

  /** What the Body Designer says about a control's range, in its own words (the kind's `notes` in /api/config): in grey as the reason for the range, in orange while the value is outside
   *  what it offers (a design saved earlier). */
  const reason = key => {
    const element = h('p', { class: 'hint g-hint', hidden: true, role: 'status' });
    const refresh = () => {
      const kind = kindOf(cat, recipe);
      const [low, high] = kind.controls[key];
      const outside = recipe[key] < low - 1e-9 || recipe[key] > high + 1e-9;
      const note = kind.notes?.[key] || (outside ? `The Body Designer offers ${cat.fields[key].label.toLowerCase()} from ${low} to ${high}.` : '');
      element.hidden = !note;
      element.classList.toggle('warn', outside);
      element.textContent = note;
    };
    refresh();
    refreshers.push(refresh);
    return element;
  };

  /** What the Body Designer says about the layers of a garment that is worn with others (the jacket and the apron go over a shirt and trousers, the skirt takes the place of trousers, the dress that of the shirt and the trousers):
   *  what it was checked with, and what is not simulated. */
  const layers = () => {
    const kind = kindOf(cat, recipe);
    const names = { shirt: 'a shirt', trousers: 'trousers' };
    const say = list => list.map(k => names[k] || k).join(' and ');
    const line = kind.over?.length ? `Goes over ${say(kind.over)} and nothing else: that is all it has been checked with.`
      : kind.instead_of?.length ? `Takes the place of ${say(kind.instead_of)} and is worn ${kind.with?.length ? `with ${say(kind.with)}` : 'on its own'}; nothing else has been checked with it.` : '';
    return line ? [h('p', { class: 'hint g-hint' }, line), kind.layer_notes ? h('p', { class: 'hint g-hint' }, kind.layer_notes) : null] : [];
  };

  const swatches = () => h('div', { class: 'g-swatches', role: 'group' },
    cat.colours.map(c => h('button', { type: 'button', class: 'g-swatch', style: { background: c.hex }, title: c.label, 'aria-label': c.label,
      'aria-pressed': String(String(recipe.colour).toLowerCase() === c.hex.toLowerCase()), onclick: () => set('colour', c.hex, true) })),
    h('input', { type: 'color', class: 'g-colour', value: recipe.colour, title: 'Any colour', 'aria-label': 'Custom colour', oninput: event => set('colour', event.target.value) }));

  const section = (id, title, ...content) => {
    const element = h('details', { class: 'g-section', open: OPEN.has(id) }, h('summary', {}, title), h('div', { class: 'stack g-body' }, content.flat().filter(Boolean)));
    element.addEventListener('toggle', () => { if (element.open) OPEN.add(id); else OPEN.delete(id); });
    return element;
  };

  function draw() {
    const cloth = h('select', { 'aria-label': 'Cloth', onchange: event => set('texture', event.target.value) },
      Object.entries(cat.choices.texture).map(([id, text]) => h('option', { value: id }, text)));
    cloth.value = recipe.texture;
    refreshers.length = 0;
    box.replaceChildren(
      section('colour', 'Colour', row('Colour', swatches())),
      section('fit', 'Fit', slider('ease_mm'), reason('ease_mm'), slider('length'), reason('length'), layers()),
      section('cloth', 'Cloth', row('Cloth', cloth)));
  }
  draw();
  return { element: box, redraw: draw };
}
