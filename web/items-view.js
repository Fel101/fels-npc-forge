import { $, S, api, btn, debounce, h, isActive, safe, slug, toast } from './core.js';
import { loadLocalRenderer } from './model-preview.js';
import * as glassesView from './glasses-view.js';
import * as clothingView from './clothing-view.js';
import { tryOnPanel } from './tryon-view.js';
import { dressPanel } from './dress-view.js';
import { equipPanel } from './equip-view.js';

// The Items tab: things a character holds, wears or finds, made with the same steps as a character's 3D model (a main picture,
// four views, a Trellis2 shape, then the painting and the game version). The server does the work (npcgen/items.py,
// npcgen/itemmaker.py); this page lists items, edits what an item is, asks for the steps and shows what came back.
// Glasses and fitted clothes are the exception: they are built from measurements, with no pictures and no job, so their editor is a designer
// (glasses-view.js, clothing-view.js) with a live preview (the server builds the model for each change in a few hundredths of a second, a dress in up to two) and
// a try-on panel under it (tryon-view.js: glasses on a face; dress-view.js: clothes on a designed body). MAKERS below is what differs.

const seg = (items, value, onpick) => h('div', { class: 'seg', role: 'group' }, items.map(([id, label]) =>
  h('button', { type: 'button', 'aria-pressed': String(id === value), onclick: () => onpick(id) }, label)));
const field = (label, control, hint) => h('div', {}, h('label', { class: 'lbl' }, label), control, hint && h('p', { class: 'hint' }, hint));
const select = (options, value, onchange, label) => {
  const element = h('select', { 'aria-label': label, onchange: event => onchange(event.target.value) },
    options.map(([id, text]) => h('option', { value: id }, text)));
  element.value = value;
  return element;
};
const fileUrl = (id, file, stamp) => `/api/items/${id}/files/${file}?v=${encodeURIComponent(stamp || '')}`;
const VIEWS = [['front', 'Front'], ['side_left', 'Left'], ['back', 'Back'], ['side_right', 'Right']];
const EDITABLE = ['name', 'family', 'kind', 'description', 'setting', 'look', 'size_m', 'faces', 'high_views', 'recipe'];
const sameValue = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const HIGH = [['front_high', 'High front'], ['back_high', 'High back']];
const metres = value => `${Math.round(value * 100) / 100} m`;

/** What each family built from a recipe brings: where its tables are in /api/config, its designer and starting recipe, its try-on panel, its words. */
const MAKERS = {
  glasses: {
    cat: cfg => cfg.glasses, recipeFor: glassesView.recipeFor, designer: glassesView.glassesDesigner, tryOn: tryOnPanel,
    applyKind: (cat, recipe, kind, restyle) => glassesView.applyKind(cat, recipe, kind, restyle),
    this: 'This pair', fresh: 'A new pair', file: 'glasses', alt: 'Interactive 3D preview of the glasses', detail: true, example: 'round gold wire glasses with green lenses',
    placeholder: kind => `e.g. ${kind.hint}`,
    wordsHint: 'A note for yourself. Or press the button and the controls below are set from your words: a style (aviator, cat-eye, round wire…), wire or plastic, a colour, “green lenses”, “oversized”, “folded”…',
    noWords: 'None of those words changed a setting. Try a style (aviator, cat-eye, round wire…), a colour, or “green lenses”.',
    tryTitle: 'Try them on a character',
    tryHint: 'Save the pair first. Then you can put it on any character that has a 3D model, nudge it into place and keep the fit.',
    game: (item, model, spec) => [
      h('li', {}, `Size: ${metres(item.size_m)} on its longest side (${model.frame_width_mm} mm wide, ${item.recipe.temple_len} mm arms) in real metres, facing +Z, like the characters.`),
      h('li', {}, 'Origin: the middle of the bridge, on the line through the middle of the lenses. Put it between the eyes.'),
      spec.attach ? h('li', {}, `Usually attached to the bone ${spec.attach} (the names of the animation library’s skeleton).`) : null,
      h('li', {}, 'Nodes: frame, lenses, temple_left and temple_right. An arm turns about its own origin, the hinge, so a game can fold it.'),
      h('li', {}, 'Materials: frame, hardware, pads and lens. The lens is alpha-blended: set its transparency mode in your engine, and swap its colour at run time to change the tint.'),
      h('li', {}, 'The settings are saved inside the file (asset extras) as well as in the item.')],
  },
  garment: {
    cat: cfg => cfg.clothing, recipeFor: clothingView.recipeFor, designer: clothingView.clothingDesigner, tryOn: dressPanel,
    applyKind: (cat, recipe, kind) => clothingView.applyKind(cat, recipe, kind),
    this: 'This garment', fresh: 'A new garment', file: 'garment', alt: 'Interactive 3D preview of the garment’s pattern', detail: false, example: 'a dark red loose shirt',
    placeholder: kind => `e.g. a dark red ${({ trousers: 'pair of loose trousers', skirt: 'long skirt', dress: 'knee-length dress', jacket: 'snug jacket', apron: 'woven apron' })[kind.id] || 'loose shirt'}`,
    wordsHint: 'A note for yourself. Or press the button and the controls below are set from your words: a colour (“dark red”, “navy”, “canvas”), “loose”, “snug”, “baggy”, “cropped”, “long”, “woven”…',
    noWords: 'None of those words changed a setting. Try a colour (“dark red”, “navy”), “loose” or “snug”, “cropped” or “long”, “woven”.',
    tryTitle: 'Try it on a character',
    tryHint: 'Save the garment first. Then you can put it on any character whose 3D model is a designed body (press Use this body on the character’s Designed body card); the Body Designer fits it to that body. A jacket or an apron needs a body that wears a shirt and trousers; a skirt takes the trousers off, and a dress the shirt and the trousers.',
    game: (item, model, spec) => [
      h('li', {}, `The pattern on its own: ${(model.fit?.size_m || []).map(v => `${Math.round(v * 1000)}`).join(' × ')} mm (x, y, z), real metres, standing where it would on an adult 1.7 m tall, the origin on the floor between the feet. It is not fitted to a body.`),
      h('li', {}, 'Worn: Try it on fits it to a character’s body. The download there is the character’s animated model with the garment added as a mesh of its own (ForgeShirt, ForgeTrousers, ForgeSkirt, ForgeDress, ForgeJacket or ForgeApron), skinned to the same skeleton as the body, so every animation moves it.'),
      h('li', {}, 'Material: the mesh’s name and Fabric (ForgeShirtFabric, ForgeTrousersFabric, ForgeSkirtFabric, ForgeDressFabric, ForgeJacketFabric or ForgeApronFabric), a small cloth texture; change it at run time to recolour the garment.'),
      h('li', {}, 'A body that was made with a shirt (or trousers, a skirt, a dress, a jacket, an apron) on loses the one of the same kind when this is put on: the new one replaces it. A jacket and an apron replace each other and go only over a shirt and trousers, and a skirt takes the trousers off and a dress the shirt and the trousers (and a jacket or an apron with them: neither has been checked with either). The body’s underwear stays, and so does the character: only this item keeps the fit.'),
      h('li', {}, 'The settings are saved inside the pattern file (asset extras) as well as in the item.')],
  },
};

export function createItemController({ render, afterSubmit }) {
  let list = [];            // the cards of the Items list
  let item = null;          // the open item as the server has it (null: the list, or a new item that is not saved yet)
  let form = null;          // what the form shows: the open item's fields with any edits, or a new item's
  let family = '';          // the list's family filter
  let query = '';
  let finish = true;        // "paint it and make the game version"
  let best = true;          // "best quality pictures": the engines that measured best, instead of the ones picked under Models
  let resultPane = null;    // the right-hand side of the editor, redrawn as the job makes things
  let viewerOpen = false;
  let designer = null;      // the glasses controls of the open pair
  let previewSeq = 0;       // which live preview is the newest (an older answer arriving late is dropped)
  let previewUrl = null;
  let pictureTimer = null;  // watching for the picture a pair of glasses gets a few seconds after it is saved
  let listTimer = null;
  let tryPane = null;       // the "try them on a character" section under a saved pair of glasses
  let tryOn = null;         // its panel, kept (with the character and the view) while the same pair stays open
  let tryOnFor = null;      // the pair it is for

  const config = () => S.cfg.items;
  const families = () => config().families;
  const familyOf = id => families().find(f => f.id === id) || families()[0];
  const kindOf = (familyId, kindId) => familyOf(familyId).kinds.find(k => k.id === kindId) || familyOf(familyId).kinds[0];
  const isRecipe = familyId => familyOf(familyId).maker !== 'photo';       // glasses and fitted clothes: built from a recipe, not drawn
  const makerOf = familyId => MAKERS[familyOf(familyId).maker];
  const startRecipe = (familyId, kind) => { const made = makerOf(familyId); return made.recipeFor(made.cat(config()), kind); };
  const itemJob = () => S.jobs.filter(job => job.target_type === 'item' && job.item_id === item?.id).slice(-1)[0];
  const busy = () => S.jobs.some(job => isActive(job) && job.target_type === 'item' && job.item_id === item?.id);

  // ---- going between the list and an item ---------------------------------------------------------------------------------------------
  async function refresh() {
    try { list = await api('GET', '/api/items'); } catch (error) { list = []; toast(error.message, 'error'); }
  }

  async function showList() {
    if (!(await leaveForm())) return;
    item = null; form = null; viewerOpen = false; designer = null;
    leaveTryOn();
    clearTimeout(pictureTimer);
    S.view = 'items';
    await refresh();
    render();
    watchList();
  }

  /** A pair of glasses gets its list picture a few seconds after it is saved: look again while the list is showing and one is missing. */
  function watchList(round = 0) {
    clearTimeout(listTimer);
    if (round > 12 || !list.some(card => card.maker !== 'photo' && !card.thumb)) return;
    listTimer = setTimeout(async () => {
      if (S.view !== 'items' || form) return;
      await refresh();
      renderItems();
      watchList(round + 1);
    }, 3000);
  }

  /** The Items tab: from another tab it comes back to what was open (an item being edited, unsaved changes and all); on the tab itself it
   *  goes to the list. */
  async function openTab() {
    if (S.view === 'items' && form) return showList();
    S.view = 'items';
    if (!form) await refresh();
    render();
  }

  async function leaveForm() {
    return !isDirty() || confirm('This item has changes you have not saved. Leave without saving them?');
  }

  function fieldsOf(record) {
    // (an item made before the high-angle views existed has no such field: it stays off until it is ticked)
    return Object.fromEntries(EDITABLE.map(key => [key, key === 'high_views' ? record[key] === true
      : key === 'recipe' ? (record.recipe ? JSON.parse(JSON.stringify(record.recipe)) : undefined) : record[key]]));
  }

  async function load(id) {
    if (item?.id !== id && !(await leaveForm())) return;
    item = await api('GET', `/api/items/${id}`);
    form = fieldsOf(item);
    viewerOpen = false;
    S.view = 'items';
    render();
  }

  function startNew(familyId = '') {
    const fam = familyOf(familyId || family || families()[0].id);
    const kind = fam.kinds[0];
    item = null;
    form = { name: '', family: fam.id, kind: kind.id, description: '', setting: '', look: 'realistic', size_m: kind.size_m, faces: config().default_faces,
      high_views: kind.high, recipe: isRecipe(fam.id) ? startRecipe(fam.id, kind) : undefined };
    viewerOpen = false;
    S.view = 'items';
    render();
  }

  const isDirty = () => !!form && (!item || EDITABLE.some(key => (key === 'recipe' ? !sameValue(form.recipe, item.recipe) : form[key] !== item[key])));   // (size and faces are numbers on both sides)

  async function save() {
    const body = { ...form };
    const saved = item ? await api('PUT', `/api/items/${item.id}`, body) : await api('POST', '/api/items', body);
    item = saved;
    form = fieldsOf(saved);
    refresh().catch(() => {});
    return saved;
  }

  async function remove() {
    if (!item || !confirm(`Delete “${item.name}” with all its pictures and its 3D model? This cannot be undone.`)) return;
    await api('DELETE', `/api/items/${item.id}`);
    item = null; form = null;
    toast('Item deleted.');
    await showList();
  }

  // ---- asking for work --------------------------------------------------------------------------------------------------------------------
  function jobOptions(reroll) {
    const o = S.recipe.options;
    return { best, quality: o.quality, image_model: o.image_model, edit_model: o.edit_model, three_d_quality: o.three_d_quality, reroll };
  }

  async function make(request, label, reroll = false) {
    const saved = isDirty() ? await save() : item;
    const result = await api('POST', `/api/items/${saved.id}/make`, { request, options: jobOptions(reroll) });
    afterSubmit({ jobs: [result.job] });
    toast(`${label}: queued ${result.job.steps.length} step${result.job.steps.length === 1 ? '' : 's'}.`);
    renderForm();
    renderResults();
  }

  async function dropPart(kind, label) {
    if (!confirm(`Remove ${label}? ${kind === 'model_3d' ? '' : 'Whatever was made from it is marked out of date.'}`.trim())) return;
    item = await api('DELETE', `/api/items/${item.id}/assets/${kind}`);
    renderResults();
    refresh().catch(() => {});
  }

  /** The queue calls this when a job changes: the open item is read again (its form is left alone, so nothing typed is lost). */
  async function onJobChange(job) {
    if (S.view !== 'items') return;
    if (item && job.item_id === item.id) {
      try {
        const fresh = await api('GET', `/api/items/${item.id}`);
        if (item?.id === fresh.id) { item = fresh; if (busy() !== formBusy) renderForm(); renderResults(); renderFormState(); }
      } catch { /* deleted meanwhile */ }
    } else if (!item && !form) {
      await refresh();
      render();
    }
  }

  // ---- the list ----------------------------------------------------------------------------------------------------------------------------
  function renderItems() {
    if (form) return renderEditor();
    const wanted = query.trim().toLowerCase();
    const shown = list.filter(card => (!family || card.family === family)
      && (!wanted || [card.name, card.kind_label, card.description].some(text => (text || '').toLowerCase().includes(wanted))));
    const search = h('input', { type: 'text', placeholder: 'Search items…', value: query, 'aria-label': 'Search items',
      oninput: debounce(event => { query = event.target.value; renderItems(); $('.item-bar input')?.focus(); }, 250) });
    $('#view').replaceChildren(h('div', { class: 'library items' },
      h('div', { class: 'lib-bar item-bar' }, search, h('span', { class: 'grow' }),
        btn('New item', 'plus', () => startNew(), { cls: 'primary' })),
      h('div', { class: 'lib-filters' }, seg([['', 'All'], ...families().map(f => [f.id, f.label])], family, id => { family = id; renderItems(); }),
        list.length ? h('span', { class: 'lib-count' }, `${shown.length} of ${list.length}`) : null),
      shown.length ? h('div', { class: 'lib-grid' }, shown.map(itemCard))
        : h('div', { class: 'empty' }, h('h2', {}, list.length ? 'Nothing matches' : 'No items yet'),
          h('p', {}, list.length ? 'Try a different search or family.'
            : 'Items are weapons, armour, helmets and hats, glasses, shoes, bags, props and garments. Most are drawn (one picture, four views, a 3D model and a game version); glasses and fitted clothes are built from measurements.'),
          list.length ? null : btn('Make the first item', 'plus', () => startNew(), { cls: 'primary' }))));
  }

  function itemCard(card) {
    const built = card.maker !== 'photo';                                        // (glasses and fitted clothes: built from measurements)
    const parts = built ? (card.has.model_3d ? 'built from measurements' : '')
      : [card.has.hero && 'picture', card.has.views && `${card.has.views} view${card.has.views === 1 ? '' : 's'}`,
        card.has.model_3d && (card.has.finished ? 'game version' : 'shape')].filter(Boolean).join(' · ');
    return h('article', { class: 'npc-card', onclick: safe(() => load(card.id)) },
      h('div', { class: `thumb${built ? ' contain' : ''}` }, card.thumb ? h('img', { src: fileUrl(card.id, 'thumb.png', card.updated), alt: '', loading: 'lazy' })
        : built && card.has.model_3d ? 'Picture on its way' : 'Nothing made yet'),
      h('div', { class: 'body' }, h('div', { class: 'name' }, card.name), h('div', { class: 'sub' }, `${card.kind_label} · ${metres(card.size_m)}`),
        h('div', { class: 'row wrap', style: { gap: '4px' } }, h('span', { class: 'badge accent' }, familyOf(card.family).label),
          card.stale ? h('span', { class: 'badge warn', title: 'Its description changed after this was made' }, 'out of date') : null,
          parts && h('span', { class: 'cap' }, parts))));
  }

  // ---- the editor ------------------------------------------------------------------------------------------------------------------------------
  let formPane = null;
  let formBusy = false;     // whether the form was drawn while a job was making the item

  function renderEditor() {
    formPane = h('section', { class: 'item-form' });
    resultPane = h('section', { class: 'item-results' });
    leaveTryOn();
    tryPane = h('section', { class: 'item-tryon' });
    $('#view').replaceChildren(h('div', { class: 'item-editor' }, formPane, resultPane, tryPane));
    renderForm();
    renderResults();
  }

  function setKind(kindId) {
    form.kind = kindId;
    form.size_m = kindOf(form.family, kindId).size_m;           // a new kind starts at its usual size
    form.high_views = kindOf(form.family, kindId).high;         // and at what it usually wants
    if (isRecipe(form.family)) { const made = makerOf(form.family); made.applyKind(made.cat(config()), form.recipe, kindOf(form.family, kindId), !item); }       // (sunglasses bring dark lenses, trousers their own colour)
  }

  function changeFamily(id) {
    const kind = familyOf(id).kinds[0];
    form.family = id;
    form.kind = kind.id; form.size_m = kind.size_m; form.high_views = kind.high;
    if (isRecipe(id)) form.recipe = startRecipe(id, kind); else delete form.recipe;
  }

  function renderForm() {
    if (!formPane) return;
    $('.item-editor')?.classList.toggle('recipe', isRecipe(form.family));
    if (isRecipe(form.family)) return renderRecipeForm();
    designer = null;
    const fam = familyOf(form.family);
    const kind = kindOf(form.family, form.kind);
    const set = (key, value) => { form[key] = value; renderFormState(); };
    const settings = Object.entries(S.cfg.settings).map(([id, setting]) => [id, setting.label]);
    const locked = busy();
    formBusy = locked;
    formPane.replaceChildren(
      h('div', { class: 'card' },
        h('h3', {}, item ? 'This item' : 'A new item'),
        h('div', { class: 'stack' },
          field('Family', select(families().filter(f => !item || f.maker === familyOf(item.family).maker).map(f => [f.id, f.label]), form.family, id => {
            changeFamily(id); renderForm(); renderResults();
          }, 'Family'), fam.blurb),
          field('Kind', select(fam.kinds.map(k => [k.id, k.label]), form.kind, id => { setKind(id); renderForm(); }, 'Kind')),
          field('Name', h('input', { type: 'text', value: form.name, maxlength: 80, placeholder: kind.label, 'aria-label': 'Name',
            oninput: event => set('name', event.target.value) }),
            form.kind === 'other' ? 'For a thing that is not in the list this is also what it is called when the pictures are drawn.' : ''),
          field('Describe it', h('textarea', { rows: 4, maxlength: 500, value: form.description, 'aria-label': 'Description',
            placeholder: kind.hint || 'What it is, what it is made of, how it looks: e.g. a brass spyglass with a leather grip',
            oninput: event => set('description', event.target.value) }),
            'Left empty, the kind’s own description above is used. The more you say about shape and materials, the better it comes out.'),
          field('Setting', select([['', 'No particular setting'], ...settings], form.setting, id => set('setting', id), 'Setting'),
            'Makes it fit a world (the characters’ settings).'),
          field('Look', select(config().looks.map(l => [l.id, l.label]), form.look, id => set('look', id), 'Look')),
          field('Size', h('input', { type: 'number', min: config().size_range[0], max: config().size_range[1], step: 0.01, value: form.size_m,
            'aria-label': 'Size in metres', oninput: event => set('size_m', Number(event.target.value)) }),
            `The longest side of the finished model in metres. ${kind.label} is usually about ${metres(kind.size_m)}.`),
          field('Game version', select(config().faces.map(f => [f.value, f.label]), form.faces, value => set('faces', Number(value)), 'Game version size'),
            'How many faces the painted game model keeps. Light suits a prop seen from far, Detailed an item seen up close.'),
          h('label', { class: 'check' }, h('input', { type: 'checkbox', checked: !!form.high_views, onchange: event => set('high_views', event.target.checked) }),
            h('span', {}, h('span', { class: 'name' }, 'Two high-angle views for the paint'),
              h('span', { class: 'hint', style: { display: 'block' } },
                'Pictures from 45 degrees above, in front and behind, so the top (a helmet dome, a crate lid, a boot opening) is painted from a picture and not filled in. Not for long thin things: a sword stood up or turned upside down in them.'))),
          h('div', { class: 'row wrap' },
            btn(item ? 'Save changes' : 'Create item', 'save', safe(async () => { await save(); toast('Saved.'); renderForm(); renderResults(); }),
              { cls: 'primary item-save', disabled: !isDirty() || locked, title: locked ? 'This item is being made' : '' }),
            btn('Back to the list', null, safe(showList)),
            item ? btn('Delete', 'trash', safe(remove), { cls: 'danger', disabled: locked, title: locked ? 'Cancel its job first' : '' }) : null),
          h('p', { class: 'hint form-state' }, formState()))));
  }

  const formState = () => (isDirty() ? (item ? 'You have unsaved changes. They are saved when you make the item.' : 'Not saved yet. It is saved when you make it.') : '');
  function renderFormState() {
    const element = $('.form-state');
    if (element) element.textContent = formState();
    const save = $('.item-save');
    if (save) save.disabled = !isDirty() || formBusy;                  // (it was only worked out when the form was drawn: an edit never enabled it)
  }

  // ---- glasses and fitted clothes: built from measurements, no pictures and no job ---------------------------------------------------------------------
  function renderRecipeForm() {
    const fam = familyOf(form.family);
    const kind = kindOf(form.family, form.kind);
    const made = makerOf(form.family);
    const set = (key, value) => { form[key] = value; renderFormState(); };
    formBusy = false;
    designer = made.designer(made.cat(config()), form.recipe, () => { renderFormState(); previewSoon(); });
    const choices = families().filter(f => !item || f.maker === familyOf(item.family).maker);
    formPane.replaceChildren(
      h('div', { class: 'card' },
        h('h3', {}, item ? made.this : made.fresh),
        h('div', { class: 'stack' },
          choices.length > 1 ? field('Family', select(choices.map(f => [f.id, f.label]), form.family, id => { changeFamily(id); renderForm(); renderResults(); }, 'Family'), fam.blurb)
            : h('p', { class: 'hint' }, fam.blurb),
          field('Kind', select(fam.kinds.map(k => [k.id, k.label]), form.kind, id => { setKind(id); renderForm(); previewSoon(); }, 'Kind')),
          field('Name', h('input', { type: 'text', value: form.name, maxlength: 80, placeholder: kind.label, 'aria-label': 'Name', oninput: event => set('name', event.target.value) })),
          field('Describe it', h('textarea', { rows: 3, maxlength: 500, value: form.description, 'aria-label': 'Description', placeholder: made.placeholder(kind),
            oninput: event => set('description', event.target.value) }),
            made.wordsHint),
          btn('Set the controls from my words', 'zap', safe(readWords), { cls: 'small' }))),
      h('div', { class: 'card' }, h('h3', {}, 'Design'), designer.element),
      h('div', { class: 'card' },
        h('div', { class: 'stack' },
          made.detail ? field('Detail', select(config().faces.map(f => [f.value, f.label]), form.faces, value => { set('faces', Number(value)); previewSoon(); }, 'Detail'),
            'How finely the curves are built. Glasses are small, so Standard is plenty; Detailed is for a close-up.') : null,
          h('div', { class: 'row wrap' },
            btn(item ? 'Save changes' : 'Create item', 'save', safe(saveGlasses), { cls: 'primary item-save', disabled: !isDirty() }),
            btn('Back to the list', null, safe(showList)),
            item ? btn('Delete', 'trash', safe(remove), { cls: 'danger' }) : null),
          h('p', { class: 'hint form-state' }, formState()))));
  }

  async function readWords() {
    if (!form.description.trim()) { toast(`Write a few words in the description first: for instance “${makerOf(form.family).example}”.`); return; }
    const result = await api('POST', '/api/items/recipe-from-text', { text: form.description, family: form.family, kind: form.kind, recipe: form.recipe });
    Object.assign(form.recipe, result.recipe);                          // in place: the controls hold this very object
    designer.redraw();
    renderFormState();
    previewSoon();
    toast(result.understood.length ? `Understood: ${result.understood.join(', ')}.` : makerOf(form.family).noWords);
  }

  async function saveGlasses() {
    const wasNew = !item;
    await save();
    toast(wasNew ? 'Created.' : 'Saved.');
    renderForm();
    renderResults();
    watchPicture();
  }

  /** The picture for the list is drawn a few seconds after the model is saved: read the item again until it is there (or says why not). */
  function watchPicture(round = 0) {
    clearTimeout(pictureTimer);
    const model = item?.assets?.model_3d;
    if (!item || !model || model.preview_of === model.build || model.preview_error || round > 40) return;
    pictureTimer = setTimeout(async () => {
      if (S.view !== 'items' || !item) return;
      try {
        const fresh = await api('GET', `/api/items/${item.id}`);
        if (item?.id === fresh.id) { item = fresh; renderPictureNote(); }
      } catch { return; }
      watchPicture(round + 1);
    }, 2000);
  }

  function renderPictureNote() {
    const note = $('.g-picture');
    const model = item?.assets?.model_3d;
    if (!note || !model) return;
    note.textContent = model.preview_error ? `No picture for the list: ${model.preview_error}`
      : model.preview_of === model.build ? 'The picture for the list is ready.' : 'Drawing the picture for the list (a few seconds)…';
  }

  async function redrawPicture() {
    const result = await api('POST', `/api/items/${item.id}/make`, { request: { make: true } });
    item = result.item;
    renderPictureNote();
    watchPicture();
    toast('Building again and drawing the picture.');
    refresh().catch(() => {});
  }

  const previewQuick = debounce(() => previewNow(), 220);
  const previewSlow = debounce(() => previewNow(), 600);                  // (a dress takes half a second to two to build: it waits for the slider to rest)
  const previewSoon = () => (form?.kind === 'dress' ? previewSlow : previewQuick)();

  /** The live preview: the server builds the glasses from the form as it is now (saved or not) and the viewer shows them. */
  async function previewNow() {
    const slot = $('.g-viewer-slot');
    if (!slot || !form || !isRecipe(form.family)) return;
    const seq = ++previewSeq;
    const status = text => { const element = $('.g-status'); if (element) { element.textContent = text; element.classList.toggle('err', false); } };
    let response;
    try {
      response = await fetch('/api/items/recipe-preview', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ family: form.family, kind: form.kind, recipe: form.recipe, faces: form.faces }) });
    } catch { status('Cannot reach the NPC server. Is it still running?'); return; }
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      const element = $('.g-status');
      if (element && seq === previewSeq) { element.textContent = data.error || 'The preview failed.'; element.classList.add('err'); }
      return;
    }
    const blob = await response.blob();
    if (seq !== previewSeq) return;
    const info = JSON.parse(response.headers.get('X-Item-Info') || '{}');
    await loadLocalRenderer();
    if (seq !== previewSeq) return;
    let viewer = slot.querySelector('model-viewer');
    if (!viewer) {
      viewer = document.createElement('model-viewer');
      for (const [key, value] of Object.entries({ class: 'model3d-canvas g-canvas', 'camera-controls': '', 'touch-action': 'pan-y', 'shadow-intensity': '0.8',
        exposure: '1.1', 'environment-image': 'neutral', 'camera-orbit': '32deg 78deg auto', 'interaction-prompt': 'none', loading: 'eager',
        alt: makerOf(form.family).alt })) viewer.setAttribute(key, value);
      slot.replaceChildren(viewer);
    }
    const orbit = viewer.loaded ? viewer.getCameraOrbit() : null;          // (keep the angle the user turned it to)
    const old = previewUrl;
    previewUrl = URL.createObjectURL(blob);
    if (orbit) viewer.addEventListener('load', () => { viewer.cameraOrbit = `${orbit.theta}rad ${orbit.phi}rad ${orbit.radius}m`; viewer.jumpCameraToGoal?.(); }, { once: true });
    viewer.setAttribute('src', previewUrl);
    if (old) setTimeout(() => URL.revokeObjectURL(old), 4000);
    const size = (info.size_m || []).map(v => `${Math.round(v * 1000)}`).join(' × ');
    status(`${(info.triangles || 0).toLocaleString()} triangles · ${size} mm (x, y, z)`);
  }

  function renderRecipeResults() {
    const model = item?.assets?.model_3d;
    const spec = kindOf(form.family, form.kind);
    const made = makerOf(form.family);
    resultPane.replaceChildren(...[
      h('div', { class: 'card g-viewer-card' },
        h('h3', {}, 'Preview'),
        h('div', { class: 'g-viewer-slot' }, h('p', { class: 'hint' }, 'Building…')),
        h('p', { class: 'hint g-status' }, ''),
        h('p', { class: 'hint g-picture' }, ''),
        h('div', { class: 'row wrap model3d-actions' },
          item && model ? h('a', { class: 'btn small', href: fileUrl(item.id, model.file, model.build || model.created), download: `${slug(item.name) || made.file}.glb`,
            title: 'The saved model as a GLB file' }, 'Download .glb') : null,
          item ? h('a', { class: 'btn small', href: `/api/items/${item.id}/export.zip`, title: 'The settings, the picture and the model in one ZIP' }, 'Export .zip') : null,
          item ? btn('Draw the picture again', 'refresh', safe(redrawPicture), { cls: 'small', title: 'Builds the model again and draws the picture the list shows' }) : null)),
      item && model ? h('details', { class: 'item-game' }, h('summary', {}, 'For your game'), h('ul', { class: 'plain' }, made.game(item, model, spec))) : null].filter(Boolean));
    renderPictureNote();
    previewNow();
    watchPicture();
    syncTryOn();
  }

  function leaveTryOn() { tryOn?.destroy(); tryOn = null; tryOnFor = null; }

  /** What a kind can be put on a character as (the held items and hats the server can place: /api/config's `equip`), or null. Not for glasses and fitted clothes: they have panels of their own. */
  const equipOf = () => (item && !isRecipe(item.family) ? kindOf(item.family, item.kind).equip || null : null);

  /** The try-on panel is for a saved pair (or garment) that has a model, and for a saved held item or hat that has one. It stays (character, view and all) while it is saved again; a changed one is fitted again. */
  function syncTryOn() {
    const how = equipOf();
    const wanted = !!tryPane && !!item && (isRecipe(item.family) || !!how) && !!item.assets?.model_3d?.file;
    if (!wanted) {
      leaveTryOn();
      const made = form && isRecipe(form.family) ? makerOf(form.family) : null;
      const equippable = !made && form ? kindOf(form.family, form.kind).equip : null;
      tryPane?.replaceChildren(...(made ? [h('div', { class: 'card' }, h('h3', {}, made.tryTitle), h('p', { class: 'hint' }, made.tryHint))]
        : equippable ? [h('div', { class: 'card' }, h('h3', {}, 'Put it on a character'),
          h('p', { class: 'hint' }, 'Once this item has its 3D model you can put it on any character that has a skeleton here: a held item goes in the hand, a hat on the head, and you can nudge it into place.'))] : []));
      return;
    }
    if (tryOn && tryOnFor === item.id && tryPane.contains(tryOn.element)) { tryOn.update(item); return; }
    leaveTryOn();
    const onItem = kept => { if (item?.id === kept.id) item = kept; };
    tryOn = how ? equipPanel(item, { how, onItem }) : makerOf(item.family).tryOn(item, { onItem });
    tryOnFor = item.id;
    tryPane.replaceChildren(tryOn.element);
  }

  // ---- what has been made -----------------------------------------------------------------------------------------------------------------------
  const fresh = meta => !!meta?.file && !meta.stale;

  function complete() {
    if (!item) return false;
    const a = item.assets || {};
    const model = a.model_3d || {};
    const high = !item.high_views || HIGH.every(([id]) => fresh(a.high?.[id]));
    const used = (id) => (model.refined?.painted_from || []).includes(id);
    return fresh(a.hero) && VIEWS.every(([id]) => fresh(a.views?.[id])) && high && fresh(model)
      && (!finish || (!!model.refined?.created && !model.game_stale && (!item.high_views || HIGH.every(([id]) => used(id)))));
  }

  function renderResults() {
    if (!resultPane || !form) return;
    if (isRecipe(form.family)) return renderRecipeResults();
    const a = item?.assets || {};
    const locked = busy();
    const job = itemJob();
    const model = a.model_3d;
    const askMake = () => make({ make: true, finish }, 'Make this item');
    // What the item chain needs and this PC lacks (ComfyUI, the Trellis2 nodes, Blender: npcgen/items.availability via /api/health).
    const status = S.health?.items, missing = !status ? ['a check of what is installed'] : status.missing || [];
    resultPane.replaceChildren(
      h('div', { class: 'card item-make' },
        h('h3', {}, 'Make it'),
        h('p', { class: 'hint' }, 'A main picture, four views drawn from it, a 3D shape from the four views, two high-angle views for the top (for the things that have one), then the shape painted from the pictures and made into a game model. Only what is missing or out of date is made.'),
        h('div', { class: 'row wrap' },
          btn(locked ? 'Making…' : complete() ? 'Everything is made' : isMadeAtAll() ? 'Make the rest' : 'Make this item', 'zap', safe(askMake),
            { cls: 'primary', disabled: locked || complete() || (!complete() && !!missing.length),
              title: complete() ? 'Use a Redo button to make a part again' : missing.length ? `Needs ${missing.join(' and ')}` : '' })),
        !complete() && missing.length ? h('p', { class: 'err' }, `Cannot be made on this PC yet: it needs ${missing.join(', ')}. Once that is installed or running, open the item again.`) : null,
        h('label', { class: 'check' }, h('input', { type: 'checkbox', checked: finish, onchange: event => { finish = event.target.checked; renderResults(); } }),
          h('span', {}, h('span', { class: 'name' }, 'Paint it and make the game version'),
            h('span', { class: 'hint', style: { display: 'block' } }, 'Off: stops at the grey shape.'))),
        h('label', { class: 'check' }, h('input', { type: 'checkbox', checked: best, onchange: event => { best = event.target.checked; } }),
          h('span', {}, h('span', { class: 'name' }, 'Best quality pictures'),
            h('span', { class: 'hint', style: { display: 'block' } },
              'Z-Image Turbo for the main picture and Qwen-Image-Edit 2511 for the views, which kept every view the same object in tests. One that is not installed is replaced by the built-in Mage-Flow model, and the step says so. Off: the engines under Models are used.'))),
        job ? jobProgress(job) : null),
      h('div', { class: 'card' }, h('h3', {}, 'Pictures'), picturesBlock(a, locked)),
      h('div', { class: 'card' }, h('h3', {}, '3D model'), model ? modelBlock(model, locked) : h('p', { class: 'hint' }, 'Not made yet.')));
    syncTryOn();
  }

  const isMadeAtAll = () => !!item && Object.keys(item.assets || {}).length > 0;

  function jobProgress(job) {
    const mark = { done: '✓', running: '●', error: '✕', cancelled: '–', pending: '○' };
    return h('div', { class: `item-job ${job.status}` },
      h('div', { class: 'row wrap' }, job.steps.map(step => h('span', { class: `badge ${step.status === 'done' ? 'ok' : step.status === 'error' ? 'warn' : ''}`,
        title: step.error || step.note || '' }, `${mark[step.status] || '○'} ${step.label}${step.status === 'running' && step.detail ? ` (${step.detail})` : ''}`))),
      job.steps.find(step => step.note) ? h('p', { class: 'hint' }, job.steps.find(step => step.note).note) : null,
      job.error ? h('p', { class: 'err' }, job.error) : null);
  }

  function tile(title, meta, file, className, actions) {
    const stamp = meta?.created;
    return h('div', { class: `tile ${className}` },
      h('div', { class: 'title' }, h('span', {}, title), meta?.stale ? h('span', { class: 'badge warn', title: 'Made before the description changed' }, 'out of date') : null),
      h('div', { class: 'frame' }, meta?.file ? h('img', { src: fileUrl(item.id, file, stamp), alt: title, loading: 'lazy' })
        : h('span', { class: 'placeholder' }, 'Not made yet')),
      h('div', { class: 'foot' }, actions));
  }

  function picturesBlock(a, locked) {
    if (!item) return h('p', { class: 'hint' }, 'Pictures appear here once the item is made.');
    const views = a.views || {};
    const haveViews = VIEWS.some(([id]) => views[id]?.file);
    const high = a.high || {};
    const haveHigh = HIGH.some(([id]) => high[id]?.file);
    return h('div', { class: 'item-pictures' },
      tile('Main picture', a.hero, 'hero.png', 'item-hero',
        h('span', { class: 'row' }, btn('Redraw', 'refresh', safe(() => make({ redo: ['hero'] }, 'Redraw the main picture', true)),
          { cls: 'small', disabled: locked, title: 'A new picture from the same description (the views and shape made from the old one are marked out of date)' }),
        a.hero ? btn('', 'trash', safe(() => dropPart('hero', 'the main picture')), { cls: 'small icon ghost', title: 'Remove', disabled: locked }) : null)),
      h('div', { class: 'item-views' },
        h('div', { class: 'row wrap' }, h('strong', {}, 'Four views'), btn('Redraw the views', 'refresh', safe(() => make({ redo: ['views'] }, 'Redraw the views', true)),
          { cls: 'small', disabled: locked || !a.hero, title: a.hero ? 'Draw all four again from the main picture' : 'Make the main picture first' }),
        haveViews ? btn('', 'trash', safe(() => dropPart('views', 'the four views')), { cls: 'small icon ghost', title: 'Remove', disabled: locked }) : null),
        h('div', { class: 'row wrap' }, VIEWS.map(([id, label]) => tile(label, views[id], `views/${id}.png`, 'item-view', null))),
        item.high_views || haveHigh ? h('div', { class: 'row wrap item-high' }, h('strong', {}, 'High-angle views'),
          btn('Redraw', 'refresh', safe(() => make({ redo: ['high'] }, 'Redraw the high-angle views', true)),
            { cls: 'small', disabled: locked || !a.hero, title: a.hero ? 'Two pictures from 45 degrees above, for painting the top' : 'Make the main picture first' }),
          haveHigh ? btn('', 'trash', safe(() => dropPart('high', 'the high-angle views')), { cls: 'small icon ghost', title: 'Remove', disabled: locked }) : null) : null,
        item.high_views || haveHigh ? h('div', { class: 'row wrap' }, HIGH.map(([id, label]) => tile(label, high[id], `high/${id}.png`, 'item-view', null))) : null));
  }

  function modelBlock(model, locked) {
    const fit = model.fit || {};
    const spec = kindOf(item.family, item.kind);
    const facts = [model.refined?.created ? (model.engine || '').replace(/ \(shape only\)$/, '') : model.engine, model.refined?.faces ? `${model.refined.faces.toLocaleString()} faces` : model.quality_label ? `${model.quality_label} detail` : '',
      fit.size_m ? `${fit.size_m.map(v => metres(v)).join(' × ')} (x, y, z)` : '',
      model.refined?.created ? `painted from the ${(model.refined.painted_from || []).length || 4} pictures` : 'grey shape, not painted yet'].filter(Boolean).join(' · ');
    const notes = [model.stale && 'It was made before the description changed.', model.game_stale && 'The game version size changed: make the game version again.',
      model.fit_error && `It could not be scaled: ${model.fit_error}`].filter(Boolean);
    const viewer = h('div', { class: 'model3d-viewer-slot' });
    const toggle = btn(viewerOpen ? 'Close 3D preview' : 'Open 3D preview', null, safe(async () => { viewerOpen = !viewerOpen; await showViewer(viewer, model); toggle.textContent = viewerOpen ? 'Close 3D preview' : 'Open 3D preview'; }),
      { cls: 'small primary' });
    if (viewerOpen) showViewer(viewer, model).catch(() => {});
    return h('div', { class: 'item-model' },
      model.preview ? h('img', { class: 'model3d-preview', src: fileUrl(item.id, model.preview, model.created), alt: 'Preview of the 3D model' }) : null,
      h('p', { class: 'hint' }, facts),
      ...notes.map(text => h('p', { class: 'hint warn' }, text)),
      h('div', { class: 'row wrap model3d-actions' }, toggle,
        h('a', { class: 'btn small', href: fileUrl(item.id, model.file, model.created), download: `${slug(item.name) || 'item'}.glb`, title: 'The model as a GLB file' }, 'Download .glb'),
        h('a', { class: 'btn small', href: `/api/items/${item.id}/export.zip`, title: 'The settings, the pictures and the model in one ZIP' }, 'Export .zip'),
        btn('Redo the shape', 'refresh', safe(() => make({ redo: ['shape'] }, 'Redo the 3D shape', true)), { cls: 'small', disabled: locked, title: 'A new shape from the four views' }),
        btn(model.refined?.created ? 'Paint again' : 'Paint and make the game version', 'zap', safe(() => make({ redo: ['finish'] }, 'Paint and make the game version')),
          { cls: 'small', disabled: locked }),
        btn('', 'trash', safe(() => dropPart('model_3d', 'the 3D model')), { cls: 'small icon ghost', title: 'Remove', disabled: locked })),
      viewer,
      h('details', { class: 'item-game' }, h('summary', {}, 'For your game'),
        h('ul', { class: 'plain' },
          h('li', {}, `Size: the longest side is ${metres(item.size_m)}; the model stands on +Y and faces +Z, like the characters.`),
          h('li', {}, `Origin: ${spec.pivot === 'grip' ? 'the grip, where a hand holds it' : spec.pivot === 'centre' ? 'the middle of the model' : 'the middle of the bottom'}.`),
          spec.attach ? h('li', {}, `Usually attached to the bone ${spec.attach} (the names of the animation library’s skeleton).`) : null)));
  }

  async function showViewer(slot, model) {
    slot.replaceChildren();
    if (!viewerOpen) return;
    await loadLocalRenderer();
    const viewer = document.createElement('model-viewer');
    for (const [key, value] of Object.entries({ class: 'model3d-canvas', 'camera-controls': '', 'touch-action': 'pan-y', 'shadow-intensity': '0.8',
      exposure: '1', loading: 'eager', alt: `Interactive 3D model of ${item.name}` })) viewer.setAttribute(key, value);
    if (model.preview) viewer.setAttribute('poster', fileUrl(item.id, model.preview, model.created));
    slot.append(h('div', { class: 'model3d-interactive' }, viewer, h('p', { class: 'hint model3d-viewer-status' }, 'Drag to orbit; scroll to zoom.')));
    viewer.setAttribute('src', fileUrl(item.id, model.file, model.created));
  }

  return { showList, openTab, load, renderItems, onJobChange };
}
