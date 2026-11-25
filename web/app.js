import { $, S, api, assetUrl, btn, debounce, fullName, getPath, h, icon, isActive, loadRecipe, npcPayload, photoToSquareDataUrl,
  safe, saveRecipe, setPath, slug, toast } from './core.js';
import { createAdvanced } from './advanced.js';
import { openCharacterCard } from './character-card.js';
import { createLibraryController } from './library-view.js';
import { createQueueController } from './queue-view.js';
import { createVoiceDesigner } from './voice-designer.js';
import { modelPreviewButton } from './model-preview.js';
import { createSceneController } from './scenes-view.js';
import { createItemController } from './items-view.js';
import { createCreatureController } from './creatures-view.js';
import { commercialBadge, licenceNote, openLicences, optionTag } from './licences-view.js';

// The page is read from disk on every request but the server is a long-running process, so after an update the two can
// disagree. The server reports its api_version; if it is older than this page needs, features that depend on the
// newer server are switched off and a banner says how to fix it (instead of crashing on a missing field).
// Keep in sync with API_VERSION in server.py (a test checks this).
const REQUIRED_API = 49;

// ---------- small components ---------------------------------------------------------------------------------
const card = (title, ...kids) => h('section', { class: 'card' }, h('h3', {}, title), ...kids);
const field = (label, control, hint) => h('div', {}, h('label', { class: 'lbl' }, label), control, hint && h('p', { class: 'hint' }, hint));

function seg(items, value, onpick) {
  return h('div', { class: 'seg', role: 'group' }, items.map(([id, label]) =>
    h('button', { type: 'button', 'aria-pressed': String(id === value), onclick: () => onpick(id) }, label)));
}
function sel(items, value, onchange, blank, props = {}) {
  const el = h('select', { ...props, onchange: e => onchange(e.target.value) },
    blank !== undefined && h('option', { value: '' }, blank),
    items.map(([id, label]) => h('option', { value: id }, label)));
  el.value = value ?? '';
  return el;
}
const check = (name, checked, onchange, hint) =>
  h('label', { class: 'check' }, h('input', { type: 'checkbox', checked, onchange: e => onchange(e.target.checked) }),
    h('span', {}, h('span', { class: 'name' }, name), hint && h('span', { class: 'hint', style: { display: 'block' } }, hint)));

// ---------- shell: tabs, status, banner ------------------------------------------------------------------------
// The newer tabs work, but have been checked on fewer characters than Create, Library and Items (README, Status).
const experimental = (label = 'Experimental') => h('span', { class: 'tab-tag', title: 'This feature has been tested less than Create, Library and Items. See the README.' }, label);
function renderTabs() {
  $('#tabs').replaceChildren(
    h('button', { class: 'tab', 'aria-current': S.view === 'create' ? 'page' : null, onclick: () => { S.view = 'create'; render(); } }, 'Create'),
    h('button', { class: 'tab', 'aria-current': S.view === 'library' ? 'page' : null, onclick: () => { S.view = 'library'; refreshLibrary(); render(); } },
      'Library', h('span', { class: 'count' }, String(S.library.length))),
    h('button', { class: 'tab', 'aria-current': S.view === 'items' ? 'page' : null,
      disabled: S.outdated, onclick: safe(() => itemController.openTab()) }, 'Items'),
    h('button', { class: 'tab', 'aria-current': S.view === 'creatures' ? 'page' : null,
      disabled: S.outdated, onclick: safe(() => creatureController.openTab()) }, 'Creatures', experimental()),
    h('button', { class: 'tab', 'aria-current': S.view === 'voices' ? 'page' : null,
      disabled: S.outdated, onclick: safe(() => voiceWorkspace.openTab()) }, 'Voice Designer', experimental('Preview')),
    h('button', { class: 'tab', 'aria-current': S.view === 'scenes' ? 'page' : null,
      disabled: S.outdated, onclick: safe(() => sceneController.showList()) }, 'Scenes', experimental()),
    // pages added by local extensions (an older server has no `extensions` field: then there are none)
    ...(S.cfg.extensions || []).map(e => h('a', { class: 'tab', href: e.url }, e.label)));
}

/** The Mage-Flow files ComfyUI lacks only matter while Mage-Flow can draw: it is chosen in Models, or it may take over when another model cannot (the default engines are Qwen's). */
function mageInUse() {
  const o = S.recipe?.options || {};
  return o.image_model === 'mage' || o.edit_model === 'mage' || o.engine_fallback === true;
}

function renderStatus() {
  const hl = S.health;
  let dot = 'warn', text = 'Checking ComfyUI…', title = '';
  if (hl) {
    if (!hl.online) { dot = 'bad'; text = 'ComfyUI offline'; title = hl.error || ''; }
    else if (hl.missing?.length && mageInUse()) { dot = 'warn'; text = 'ComfyUI online · models missing'; title = 'Missing: ' + hl.missing.join(', '); }
    else { dot = 'ok'; text = `ComfyUI online${hl.gpu ? ' · ' + hl.gpu.replace(/^cuda:\d+\s*/, '').replace(/\s*:\s*native$/, '') : ''}`; }
    const g = hl.gpu_rule;
    if (hl.online && g?.one_model_at_a_time) {
      text += ' · one model at a time';
      title = [title, `On the GPU now: ${g.loaded || 'nothing from NPC Forge'}.`,
        g.last_switch ? `Last switch: ${g.last_switch.from || 'nothing'} → ${g.last_switch.to}, ${g.last_switch.seconds} s to unload and empty the card.` : '',
        `Everything is unloaded before a different model loads, and ${Math.round((g.unload_idle_seconds ?? 120) / 60 * 10) / 10} min after the queue runs empty. Turn this off with One model at a time on the Models card.`].filter(Boolean).join('\n');
    }
  }
  $('#status').replaceChildren(h('span', { class: `dot ${dot}` }), h('span', { title }, text));
  const banner = $('#banner');
  if (S.outdated) {
    banner.className = 'banner warn'; banner.hidden = false;
    banner.textContent = 'This page is newer than the NPC Forge server that is running (it was started before the update), so newer features including Scenes are switched off. Close the server window and run start.bat again.';
  } else if (hl && !hl.online) {
    banner.className = 'banner'; banner.hidden = false;
    banner.textContent = `Cannot reach ComfyUI at ${S.cfg.comfy_url}. Start ComfyUI and this page will pick it up automatically.`;
  } else if (hl?.missing?.length && mageInUse()) {
    banner.className = 'banner warn'; banner.hidden = false;
    banner.textContent = 'These Mage-Flow files were not found in ComfyUI: ' + hl.missing.join(', ');
  } else banner.hidden = true;
}

async function refreshHealth() {
  const before = JSON.stringify(S.health?.engines_missing || {});
  try { S.health = await api('GET', '/api/health'); } catch (e) { S.health = { online: false, error: e.message }; }
  renderStatus();
  if (JSON.stringify(S.health.engines_missing || {}) !== before) rerenderSettings();      // Models & options greys out what is not installed
}

const gpuTime = new Intl.DateTimeFormat(undefined, { hour: 'numeric', minute: '2-digit' });
let gpuSnapshot, gpuRefreshing = false;

// GET /api/gpu?lock=1: {suite_lock, one_model_at_a_time (the switch), alone, switch_applies}, or null while unreachable.
let gpuRule = null;

function renderGpuLight(answer, force = false) {
  const light = $('#gpuLight');
  const lock = answer?.suite_lock;
  const snapshot = JSON.stringify(answer);
  if ((snapshot === gpuSnapshot && !force) || (!lock && light.hidden)) return;
  gpuSnapshot = snapshot;
  light.hidden = false;

  const holders = lock?.holders || [];
  const exclusive = holders.find(holder => holder.alone === true);
  const request = exclusive ? null : lock?.alone;
  const state = !lock ? 'status unavailable' : exclusive ? 'alone' : request ? 'making room' : holders.length ? 'shared' : 'free';
  const dot = state === 'free' ? 'ok' : holders.length || request ? 'warn' : '';
  $('.dot', light).className = `dot ${dot}`;
  $('#gpuLabel').textContent = exclusive ? `${exclusive.name}, alone: ${exclusive.what || 'a model'}` : `GPU ${state}`;

  const line = (holder, mode) => {
    const since = Date.parse(holder.since);
    const time = Number.isFinite(since) ? ` · since ${gpuTime.format(since)}` : '';
    return mode === 'alone' ? `${holder.name}, alone: ${holder.what || 'a model'}${time}` : `${holder.name} · ${holder.what || 'a model'}${mode}${time}`;
  };
  const lines = holders.map(holder => line(holder, holder.alone ? 'alone' : ''));
  if (request) lines.unshift(line(request, ' · making room on the GPU'));
  if (!lines.length) lines.push(lock ? 'The suite GPU lock is free.' : 'Reconnect to NPC Forge to check the GPU lock.');
  $('summary', light).title = lines.join('\n');
  $('#gpuDetails').replaceChildren(...lines.map(text => h('p', {}, text)), answer ? oneModelSwitch() : '');
}

async function refreshGpuLight() {
  if (document.hidden || gpuRefreshing) return;
  gpuRefreshing = true;
  try { gpuRule = await api('GET', '/api/gpu?lock=1'); }
  catch { gpuRule = null; }
  finally { gpuRefreshing = false; }
  renderGpuLight(gpuRule);
}

/** One model at a time (contract 8): the same switch on the Models card and in the GPU light's popup. Saved in config.json. */
function oneModelSwitch() {
  const on = gpuRule?.one_model_at_a_time ?? S.health?.gpu_rule?.switch ?? S.health?.gpu_rule?.one_model_at_a_time ?? true;
  const later = gpuRule?.switch_applies === 'next_job'
    ? h('span', { class: 'hint', style: { display: 'block' } }, 'A job is running: it finishes as it started, and the change applies from the next one.') : null;
  const box = h('input', { type: 'checkbox', checked: on, 'aria-label': 'One model at a time',
    onchange: async event => {
      const wanted = event.target.checked;
      box.disabled = true;
      try {
        gpuRule = { ...(gpuRule || {}), ...await api('PUT', '/api/gpu/settings', { one_model_at_a_time: wanted }) };
        toast(wanted ? 'One model at a time is on: pictures wait for an empty GPU.' : 'One model at a time is off: pictures share the GPU.', 'ok');
      } catch (error) {
        box.checked = !wanted;
        toast(error.message, 'error');
      } finally {
        const place = box.closest('dialog, details');      // both copies are drawn again: keep the keyboard on this one
        box.disabled = false;
        renderGpuLight(gpuRule, true);
        renderSettings();
        place?.querySelector('.one-model input')?.focus();
      }
    } });
  return h('label', { class: 'check one-model' }, box,
    h('span', {}, h('span', { class: 'name' }, 'One model at a time'),
      h('span', { class: 'hint', style: { display: 'block' } }, 'Before NPC Forge makes a picture, it asks Voice Lab, Altera, LM Studio and Ollama to unload their models, and they wait until it is done. Slower, but needs the least memory. Turn it off if your card has room for everything at once.'),
      later));
}

// ---------- create view: sidebar -----------------------------------------------------------------------------------
function buildCreate() {
  $('#view').replaceChildren(h('div', { class: 'create' }, h('aside', { id: 'sidebar', 'aria-label': 'Recipe' }), h('section', { class: 'sheet', id: 'sheet' })));
  renderSidebar();
  renderSheet();
}

function setStyle(id) {
  S.recipe.style = id;
  if (S.draft) { S.draft.style = id; if (S.draft.id) S.dirty = true; updateHead(); refreshPromptsSoon(); }
  saveRecipe(); renderSidebar();
}

function renderSidebar() {
  const c = S.cfg, r = S.recipe, st = c.settings[r.setting];
  const sidebar = $('#sidebar'); if (!sidebar) return;
  const keep = sidebar.scrollTop;
  const toggleExpr = id => {
    const set = new Set(r.assets.expressions);
    set.has(id) ? set.delete(id) : set.add(id);
    r.assets.expressions = c.expressions.map(e => e.id).filter(x => set.has(x));
    saveRecipe(); renderSidebar();
  };
  const style = c.styles[r.style] || {};

  sidebar.replaceChildren(
    step(1, 'Start from',
      S.outdated ? null : seg([['roll', 'Random roll'], ['describe', 'Describe'], ['photo', 'From a photo']], r.mode,
        id => { r.mode = id; saveRecipe(); renderSidebar(); }),
      r.mode === 'describe' ? h('div', { class: 'stack', style: { marginTop: '10px' } },
        h('textarea', { rows: 4, placeholder: 'e.g. a weathered lighthouse keeper in his sixties, thick wool sweater, white beard', value: r.describe,
          'aria-label': 'Describe the character', oninput: e => { r.describe = e.target.value; saveRecipe(); } }),
        h('p', { class: 'hint' }, 'Your words drive the art. Name, age and gender are only rolled for the data sheet.'))
        : r.mode === 'photo' ? photoPicker()
        : h('p', { class: 'hint' }, 'Rolls looks, job and personality from the tables. Edit anything afterwards.')),

    step(2, 'Setting',
      seg(Object.entries(c.settings).map(([id, s]) => [id, s.short || s.label.split(' ')[0]]), r.setting, id => {
        r.setting = id; r.constraints.occupation = ''; r.constraints.origin = ''; r.constraints.hair_color = ''; r.constraints.hair_style = ''; r.constraints.morphology = '';
        const s = c.settings[id];       // narrowing choices that this setting does not have would silently roll "Any"
        if (!(s.genders || c.genders).some(g => g.id === r.constraints.gender)) r.constraints.gender = '';
        if (!(s.age_groups || c.age_groups).some(g => g.id === r.constraints.age_group)) r.constraints.age_group = '';
        if (s.default_style && c.styles[s.default_style]) r.style = s.default_style;       // a setting may prefer an art style
        saveRecipe(); renderSidebar();
      }),
      h('p', { class: 'hint' }, st.blurb)),

    // a setting may allow only some genders and ages (`genders`, `min_age`, its own `age_groups`): offer just those
    step(3, 'Who', h('div', { class: 'stack' },
      field('Gender', sel((st.genders || c.genders).map(g => [g.id, g.label]), r.constraints.gender, v => { r.constraints.gender = v; saveRecipe(); }, 'Any')),
      field('Age', sel((st.age_groups || c.age_groups).map(g => [g.id, g.label]), r.constraints.age_group, v => { r.constraints.age_group = v; saveRecipe(); }, 'Any adult'),
        st.min_age >= 18 ? `Everyone in this setting is ${st.min_age} or older.` : 'Children and teens are only rolled when you pick them.'),
      field('Occupation', sel(st.occupations.map(x => [x.id, x.label]), r.constraints.occupation, v => { r.constraints.occupation = v; saveRecipe(); }, 'Any')),
      field(st.origin_label, sel(st.origins.map(x => [x.id, x.label]), r.constraints.origin, v => { r.constraints.origin = v; saveRecipe(); }, 'Any')),
      field('Body plan', sel([...new Map((st.has_races ? st.origins.map(origin => [origin.morphology || 'bipedal',
        c.morphologies?.[origin.morphology || 'bipedal']?.label || 'Bipedal']) : [['bipedal', 'Bipedal']])).entries()],
        r.constraints.morphology || '', v => { r.constraints.morphology = v; saveRecipe(); }, 'Any')),
      (st.hair_colors || []).length > 0 && field('Hair colour', sel(st.hair_colors.map(x => [x, x]), r.constraints.hair_color || '', v => { r.constraints.hair_color = v; saveRecipe(); }, 'Any')),
      h('p', { class: 'hint' }, 'These narrow the random roll. Fields you lock on the NPC sheet always win.'),
      S.outdated ? null : h('div', { class: 'row' },
        btn('Advanced options…', 'plus', safe(openAdvanced), { cls: 'small grow', title: 'Choose any field of the character sheet before rolling: build, outfit, traits, quirk…' }),
        presetCount() ? btn('Clear', 'x', () => { clearPresets(); renderSidebar(); }, { cls: 'small ghost', title: 'Set the advanced options back to Random' }) : null),
      !S.outdated && presetCount() ? h('p', { class: 'hint' }, `${presetCount()} more field${presetCount() === 1 ? '' : 's'} chosen in Advanced options.`) : null)),

    // The chosen style as a sample picture; all of them side by side in a popup, bigger than a sidebar could show them.
    step(4, 'Art style',
      h('button', { type: 'button', class: 'style-now', title: 'Choose an art style', onclick: openStylePicker },
        styleSample(r.style),
        h('span', { class: 'style-now-text' }, h('strong', {}, style.label || r.style), h('span', { class: 'hint', style: { margin: 0 } }, style.blurb || ''),
          h('span', { class: 'style-change' }, `Change style · ${Object.keys(c.styles).length} to choose from`)))),

    step(5, 'What to make', h('div', { class: 'stack' },
      check('Portrait', r.assets.portrait, v => { r.assets.portrait = v; saveRecipe(); }, 'Head and shoulders, for dialogue boxes and UI.'),
      check('Full-body sprite', r.assets.sprite, v => { r.assets.sprite = v; saveRecipe(); }, 'Standing figure with a transparent background.'),
      !S.outdated && check('3D model', r.assets.model_3d, v => { r.assets.model_3d = v; saveRecipe(); },
        `Uses ${c.engines.three_d?.find(e => e.id === r.options.three_d_model)?.label || 'the selected local generator'} through ComfyUI; Pixal3D uses full-body turnarounds for shape and head close-ups for face texture detail.`),
      check('Expression set', r.assets.expressions.length > 0, v => {
        r.assets.expressions = v ? c.expressions.filter(e => e.default).map(e => e.id) : []; saveRecipe(); renderSidebar();
      }, 'The same face in different moods, edited from the portrait.'),
      r.assets.expressions.length > 0 && h('div', { class: 'chips' }, c.expressions.map(e =>
        h('button', { type: 'button', class: 'chip' + (r.assets.expressions.includes(e.id) ? ' on' : ''), onclick: () => toggleExpr(e.id) }, e.label))),
      !S.outdated && check('Animation', r.anim.on, v => { r.anim.on = v; saveRecipe(); renderSidebar(); }, 'Walk cycles in up to four directions, ready for your engine.'),
      !S.outdated && r.anim.on && animOptions(),
      !S.outdated && check('Model sheet', r.sheet.on, v => { r.sheet.on = v; saveRecipe(); renderSidebar(); }, 'Portrait, front, both sides and back on white: a turnaround for 3D modelling.'),
      !S.outdated && r.sheet.on && sheetOptions(),
      !S.outdated && check('Voice', r.voice.on, v => { r.voice.on = v; saveRecipe(); renderSidebar(); }, 'Voice Lab assigns voices for the characters in this batch.'),
      !S.outdated && r.voice.on && voiceOptions(),
      h('p', { class: 'hint' }, 'The data sheet (name, traits, bio, appearance) is always created, since it drives the prompts.'),
      h('p', { class: 'hint' }, 'Models, quality, seed and backgrounds are under ', h('button', { type: 'button', class: 'linkish', onclick: openSettings }, 'Models & options'), '.'))),

    card('Batch', h('div', { class: 'row' },
      h('div', { style: { width: '84px' } }, h('input', { type: 'number', min: 1, max: 50, value: r.count, 'aria-label': 'How many NPCs', oninput: e => { r.count = Math.max(1, Math.min(50, +e.target.value || 1)); saveRecipe(); } })),
      btn('Generate batch', 'zap', safe(generateBatch), { cls: 'grow', title: 'Roll new NPCs and generate everything ticked above for each' }))),

    h('div', { class: 'sidebar-foot' },
      h('div', { class: 'sticky-actions' },
        btn(r.mode === 'describe' ? 'Create' : 'Roll NPC', 'dice', safe(rollNpc), { title: r.mode === 'describe' ? 'Create a character from your description' : 'Roll a new NPC into the editor (name, job and personality; locked fields are kept)' }),
        btn('Generate', 'zap', safe(() => generateDraft()), { cls: 'primary', title: 'Generate the ticked assets for the NPC being edited' })),
      btn('About', 'info', openAbout, { cls: 'small ghost about-btn', title: 'About NPC Forge' })));

  sidebar.scrollTop = keep;
}

/** A numbered sidebar card: the steps read top to bottom, from how to start to what to make. */
const step = (n, title, ...kids) => h('section', { class: 'card step' }, h('h3', {}, h('span', { class: 'step-num', 'aria-hidden': 'true' }, String(n)), title), ...kids);

/** The same character drawn in each style (web/styles/, made with Z-Image Turbo, see NOTICE.md). A style without a sample
 *  (one added later, or by a local extension) shows its name's first letters instead. */
function styleSample(id) {
  const label = S.cfg.styles[id]?.label || id;
  const box = h('span', { class: 'style-sample' }, label.split(/\s+/).map(w => w[0]).join('').slice(0, 2).toUpperCase());
  const img = h('img', { src: `/styles/${id}.webp`, alt: '', loading: 'lazy', draggable: false, onerror: () => img.remove() });
  box.append(img);
  return box;
}

function openStylePicker() {
  let dlg = $('#stylePicker');
  if (!dlg) {
    dlg = h('dialog', { id: 'stylePicker', class: 'about style-picker', 'aria-labelledby': 'stylePickerTitle',
      onclick: e => { if (e.target === e.currentTarget) e.currentTarget.close(); } });
    document.body.append(dlg);
  }
  const current = S.draft?.style || S.recipe.style;
  dlg.replaceChildren(h('div', { class: 'about-body' },
    h('div', { class: 'about-head' },
      h('div', { class: 'grow' }, h('h2', { id: 'stylePickerTitle' }, 'Art style'),
        h('p', { class: 'hint' }, 'The same character in every style. New characters and the one open now are drawn in the style you pick; pictures already made keep theirs until you redo them.')),
      btn('Close', '', () => dlg.close(), { cls: 'small ghost' })),
    h('div', { class: 'style-grid' }, Object.entries(S.cfg.styles).map(([id, s]) =>
      h('button', { type: 'button', class: 'style-tile' + (id === current ? ' active' : ''), 'aria-pressed': String(id === current), title: s.blurb,
        onclick: () => { setStyle(id); dlg.close(); } },
        styleSample(id), h('strong', {}, s.label), h('span', { class: 'hint', style: { margin: 0 } }, s.blurb))))));
  if (!dlg.open) dlg.showModal();
}

// ---------- Models & options (the gear in the top bar): chosen once, used for every picture ----------------------------------------
function openSettings() {
  let dlg = $('#settings');
  if (!dlg) {
    dlg = h('dialog', { id: 'settings', class: 'about advanced', 'aria-labelledby': 'settingsTitle',
      onclick: e => { if (e.target === e.currentTarget) e.currentTarget.close(); } });
    document.body.append(dlg);
  }
  paintSettings(dlg);
  if (!dlg.open) dlg.showModal();
}

/** After a change: the top-bar button always, the dialog only while it is open (it is painted afresh when it opens). */
function renderSettings() {
  renderSettingsButton();
  const dlg = $('#settings');
  if (dlg?.open) paintSettings(dlg);
}

function paintSettings(dlg) {
  const keep = dlg.querySelector('.about-body')?.scrollTop || 0;
  dlg.replaceChildren(
    h('div', { class: 'about-body' },
      h('div', { class: 'about-head' },
        h('div', { class: 'grow' }, h('h2', { id: 'settingsTitle' }, 'Models & options'),
          h('p', { class: 'hint' }, 'Used for every picture you generate from now on, single characters and batches alike.')),
        btn('Close', '', () => dlg.close(), { cls: 'small ghost' })),
      h('section', { class: 'adv-group' }, h('h4', { class: 'lbl' }, 'Models'), modelsSection()),
      h('section', { class: 'adv-group' }, h('h4', { class: 'lbl' }, 'Options'), optionsSection())),
    h('div', { class: 'about-foot adv-foot' },
      S.cfg.licences ? btn('Licences of every model', '', safe(() => openLicences()), { cls: 'small ghost', title: 'Which models may be used commercially, with their licences' }) : null,
      h('span', { class: 'grow' }), btn('Done', '', () => dlg.close(), { cls: 'small primary' })));
  const body = dlg.querySelector('.about-body'); if (body) body.scrollTop = keep;
}

/** The top-bar button names the models in use, so the choice is visible without opening the dialog. */
function renderSettingsButton() {
  const el = $('#settingsBtn'); if (!el || !S.cfg) return;
  const o = S.recipe.options, eng = S.cfg.engines || {};
  const name = (kind, id) => (eng[kind] || []).find(e => e.id === id)?.label?.replace(/\s*\(.*\)$/, '') || id;
  el.hidden = false;
  el.title = `Image model: ${name('image', o.image_model)}\nEdit model: ${name('edit', o.edit_model)}\n3D: ${name('three_d', o.three_d_model)}\nClick to change models, quality, seed and backgrounds.`;
  el.replaceChildren(icon('gear'), h('span', { class: 'settings-label' }, name('image', o.image_model)));
}

const rerenderSettings = () => { renderSidebar(); renderSettings(); };

function optionsSection() {
  const c = S.cfg, o = S.recipe.options;
  return h('div', { class: 'stack' },
    field('Quality', sel(Object.entries(c.quality).map(([id, q]) => [id, `${q.label} · ${q.steps} steps · ${q.portrait[0]}px`]), o.quality, v => { o.quality = v; saveRecipe(); })),
    field('Sprite source', seg([['auto', 'Auto'], ['match', 'Match portrait'], ['independent', 'Independent']], o.sprite_mode, v => { o.sprite_mode = v; saveRecipe(); rerenderSettings(); }),
      { auto: 'Edits the portrait for ordinary characters, so the face and outfit match. Characters made from a description are drawn from that description, which keeps unusual designs (a robot with a teapot head) intact.',
        match: 'Always edits the portrait into a full-body figure. Faithful for people, but unusual designs can lose features.',
        independent: 'Always generated from the description alone. Faster, but the face will differ from the portrait.' }[o.sprite_mode]),
    field('Sprite view', sel(c.views.map(v => [v, { front: 'Front', three_quarter: 'Three-quarter', side: 'Side profile' }[v] || v]), o.sprite_view, v => { o.sprite_view = v; saveRecipe(); })),
    check('Transparent sprite background', o.transparent_sprite, v => { o.transparent_sprite = v; saveRecipe(); }, 'Generated on a flat colour, then cut out.'),
    check('Transparent portrait background', o.transparent_portrait, v => { o.transparent_portrait = v; saveRecipe(); }, 'Otherwise the portrait gets a plain backdrop.'),
    check('Save portrait and sprite rerolls as candidates', o.save_candidates, v => { o.save_candidates = v; saveRecipe(); }, 'Keep the active image until you choose a saved variant below.'),
    field('Seed', h('input', { type: 'text', inputMode: 'numeric', placeholder: 'Random', value: o.seed || '', oninput: e => { o.seed = e.target.value.replace(/\D/g, '') || null; saveRecipe(); } }),
      'Leave empty for a random look each time.'));
}

/** Which model draws from text and which one edits (npcgen/engines.py). One that ComfyUI does not have is listed but cannot be picked. */
function modelsSection() {
  const eng = S.cfg.engines, o = S.recipe.options;
  if (!eng) return h('p', { class: 'hint' }, 'This server offers only the built-in Mage-Flow models.');
  const missing = S.health?.engines_missing || {};
  const fallsBack = o.engine_fallback === true;
  const picker = (kind, key, label, missingModels = missing, fallbackNote = fallsBack ? 'Mage-Flow draws instead.' : 'Pictures that need it fail until it is installed.') => {
    const list = eng[kind], chosen = list.find(e => e.id === o[key]) || list[0];
    const gone = id => (missingModels[id] || []).length > 0;
    const control = h('select', { 'aria-label': label, onchange: e => { o[key] = e.target.value; saveRecipe(); rerenderSettings(); } },
      list.map(e => h('option', { value: e.id, disabled: gone(e.id) && e.id !== chosen.id, title: gone(e.id) ? 'ComfyUI is missing ' + missingModels[e.id].join(', ') : '' },
        e.label + (gone(e.id) ? ' (not installed)' : '') + optionTag(`${kind}:${e.id}`))));
    control.value = chosen.id;
    const out = field(label, control, gone(chosen.id) ? `Unavailable: ComfyUI is missing ${missingModels[chosen.id].join(', ')}. ${fallbackNote}` : chosen.note);
    const note = licenceNote(`${kind}:${chosen.id}`);         // (null where the server lists no licences)
    if (note) out.append(note);
    return out;
  };
  const big = o.image_model !== 'mage' || o.edit_model !== 'mage';
  const pixal = o.three_d_model === 'pixal3d', trellis = o.three_d_model === 'trellis2', hunyuan = !pixal && !trellis;
  const qualities = S.cfg.three_d_qualities || [];
  const selectedQuality = qualities.find(q => q.id === o.three_d_quality) || qualities.find(q => q.id === 'medium');
  const qualityPicker = pixal ? field('Pixal3D detail', sel(qualities.map(q => [q.id, q.label]), selectedQuality?.id,
    v => { o.three_d_quality = v; saveRecipe(); rerenderSettings(); }, undefined, { 'aria-label': 'Pixal3D detail' }),
  selectedQuality?.summary || 'Choose the mesh and texture detail for Pixal3D.') : null;
  const faceSources = S.cfg.three_d_face_sources || [];
  const selectedFaceSource = faceSources.find(source => source.id === o.three_d_face_source) || faceSources.find(source => source.id === 'turnaround');
  const faceSourcePicker = pixal ? field('Pixal3D face source', sel(faceSources.map(source => [source.id, source.label]), selectedFaceSource?.id,
    v => { o.three_d_face_source = v; saveRecipe(); rerenderSettings(); }, undefined, { 'aria-label': 'Pixal3D face source' }),
  selectedFaceSource?.summary || 'Choose the close-up source for front face texture.') : null;
  // Trellis2 (and an import) keeps this many faces: the same three settings as Pixal3D's detail, but nothing is baked
  const importDetails = S.cfg.three_d_import_details || [];
  const selectedFaces = importDetails.find(d => d.id === o.three_d_quality) || importDetails.find(d => d.id === 'medium');
  const facesPicker = trellis && importDetails.length ? field('Faces to keep', sel(importDetails.map(d => [d.id, d.label]), selectedFaces?.id,
    v => { o.three_d_quality = v; saveRecipe(); rerenderSettings(); }, undefined, { 'aria-label': 'Faces to keep' }),
  selectedFaces?.summary || '') : null;
  // Trellis2 only: the eyes filled with skin in the pictures its SHAPE is made from (npcgen/eyesoft.py); the paint reads the originals
  const eyePose = S.health?.rig_pose, eyeMissing = !eyePose ? ['a check of what is installed'] : eyePose.missing || [];
  if (eyeMissing.length) o.three_d_soften_eyes = false;
  const softenEyes = trellis ? h('label', { class: 'check', title: eyeMissing.length ? `Needs ${eyeMissing.join(' and ')}` : '' },
    h('input', { type: 'checkbox', checked: !!o.three_d_soften_eyes, disabled: !!eyeMissing.length, 'aria-label': 'Soften the eyes for the shape',
      onchange: e => { o.three_d_soften_eyes = e.target.checked; saveRecipe(); } }),
    h('span', {}, h('span', { class: 'name' }, 'Soften the eyes for the shape'),
      h('span', { class: 'hint', style: { display: 'block' } }, eyeMissing.length ? `Needs ${eyeMissing.join(' and ')}.`
        : 'Fills each eye with skin in the pictures the shape is made from (the paint still uses the originals), so the shape has no slits or raised rims round the eyes. Finds the eyes with SDPose in ComfyUI. Measured: on Natalie it took away the slits and the hair visor over the eyes, on Devi it left a smooth face or a flat band. Not done for a character with glasses (filled in, the frames come out as solid lenses), and best left off when the eyes already come out well.')))
    : null;
  // Trellis2 only: the glasses taken off copies of the pictures the model is made and painted from (npcgen/glassesoff.py); the character's own keep them
  const glassesOff = trellis ? h('label', { class: 'check' },
    h('input', { type: 'checkbox', checked: !!o.three_d_glasses_off, 'aria-label': 'Leave the glasses off the 3D model',
      onchange: e => { o.three_d_glasses_off = e.target.checked; saveRecipe(); } }),
    h('span', {}, h('span', { class: 'name' }, 'Leave the glasses off the 3D model'),
      h('span', { class: 'hint', style: { display: 'block' } }, 'For a character whose sheet has glasses: the Edit model takes them off copies of the four model-sheet pictures, and the close-ups of the detailed head are drawn without them. Measured on Laura, with the glasses in the pictures the shape has solid lenses or the attached head shows the arms of the glasses twice, and the eyes cannot be softened; without them none of that happens. The pictures of the character itself keep the glasses and the 3D model has none: the glasses come back as a separate item, made in the Items tab (Glasses) and put on the character.')))
    : null;
  const hunyuanSources = S.cfg.three_d_hunyuan_sources || [];
  const selectedHunyuanSource = hunyuanSources.find(source => source.id === o.three_d_hunyuan_source) || hunyuanSources[0];
  const hunyuanSourcePicker = hunyuan && hunyuanSources.length ? field('Hunyuan3D picture', sel(hunyuanSources.map(source => [source.id, source.label]), selectedHunyuanSource?.id,
    v => { o.three_d_hunyuan_source = v; saveRecipe(); rerenderSettings(); }, undefined, { 'aria-label': 'Hunyuan3D picture' }),
  selectedHunyuanSource?.summary || '') : null;
  const hunyuanDetails = S.cfg.three_d_hunyuan_details || [];
  const selectedHunyuanDetail = hunyuanDetails.find(d => d.id === o.three_d_hunyuan_detail) || hunyuanDetails[0];
  const hunyuanDetailPicker = hunyuan && hunyuanDetails.length ? field('Hunyuan3D detail', sel(hunyuanDetails.map(d => [d.id, d.label]), selectedHunyuanDetail?.id,
    v => { o.three_d_hunyuan_detail = v; saveRecipe(); rerenderSettings(); }, undefined, { 'aria-label': 'Hunyuan3D detail' }),
  selectedHunyuanDetail?.summary || '') : null;
  return h('div', { class: 'stack' },
    oneModelSwitch(),
    picker('image', 'image_model', 'Image model'),
    picker('edit', 'edit_model', 'Edit model'),
    check('Let Mage-Flow draw when the chosen model cannot', fallsBack, v => { o.engine_fallback = v; saveRecipe(); rerenderSettings(); },
      'Off (the default): a picture whose model cannot draw it (not enough free memory, files missing) fails and says why, so no picture is quietly drawn by a model whose licence is unclear. On: Mage-Flow draws it instead and the picture says so.'),
    picker('three_d', 'three_d_model', '3D model generator', S.health?.three_d_missing || {}, 'This generator cannot run until its local ComfyUI files and nodes are available.'),
    qualityPicker,
    facesPicker,
    softenEyes,
    glassesOff,
    faceSourcePicker,
    hunyuanSourcePicker,
    hunyuanDetailPicker,
    trellis ? h('p', { class: 'hint' }, 'Trellis2 runs in ComfyUI with the Trellis2 node pack and Pixal3D\'s multi-view model: about four minutes for the shape of the four model-sheet pictures (made first if missing). It has no colour of its own: use Paint from pictures + clean up on the 3D card afterwards. See docs/model-import.md.') : null,
    pixal ? h('p', { class: 'hint' }, 'Pixal3D runs locally in ComfyUI and can take a long time. High detail may take over an hour and uses much more VRAM; runtime depends on GPU load and queued work.') : null,
    h('p', { class: 'hint' }, S.recipe.mode === 'photo'
      ? 'From a photo: the portrait is an edit of your photo, so it uses the Edit model. The Image model draws independent sprites.'
      : 'Image draws the portrait (and independent sprites, and a scene\'s stage background). Edit makes the sprite, expressions, model sheet and animation from it.'),
    h('p', { class: 'hint' }, 'All 3D generation stays local in ComfyUI. Hunyuan makes the shape only, from the sprite or the model sheet\'s front picture; Pixal3D generates four turnaround views when needed and bakes a textured GLB; Trellis2 makes the shape from the same four views.'),
    big ? h('p', { class: 'hint' }, fallsBack
      ? 'The bigger models unload everything else first and stop safely if memory runs short: that picture is then drawn with Mage-Flow, and the job says why.'
      : 'The bigger models unload everything else first and stop safely if memory runs short: that picture then fails and the job says why, so try it again when the GPU is free. Nothing is drawn with Mage-Flow unless you turn that on above.') : null);
}

// ---------- create view: character sheet ------------------------------------------------------------------------------
const FIELDS = [
  { group: 'Identity', items: [
    { path: 'profile.first_name', label: 'First name' }, { path: 'profile.last_name', label: 'Last name' },
    { path: 'profile.gender', label: 'Gender', type: 'gender' }, { path: 'profile.age', label: 'Age', type: 'number' },
    { path: 'profile.heritage', label: 'Heritage', when: s => !S.cfg.settings[s]?.has_races },
    { path: 'profile.race', label: 'Race', type: 'origin', when: s => S.cfg.settings[s]?.has_races },
    { path: 'morphology', label: 'Body plan', type: 'morphology', wide: true },
    { path: 'profile.occupation', label: 'Occupation' }] },
  { group: 'Appearance', items: [
    { path: 'appearance.build', label: 'Build' }, { path: 'appearance.skin_tone', label: 'Skin tone' }, { path: 'appearance.hair', label: 'Hair' },
    { path: 'appearance.eyes', label: 'Eyes' }, { path: 'appearance.facial_hair', label: 'Facial hair' }, { path: 'appearance.glasses', label: 'Glasses' },
    { path: 'appearance.distinguishing', label: 'Distinguishing features' }, { path: 'appearance.accessory', label: 'Accessory' },
    { path: 'appearance.expression', label: 'Default expression' },
    { path: 'appearance.outfit', label: 'Outfit', type: 'textarea', wide: true },
    { path: 'appearance.extra', label: 'Extra notes for the artist (optional)', type: 'textarea', wide: true }] },
  { group: 'Personality', items: [
    { path: 'profile.personality', label: 'Traits (comma separated)', list: true }, { path: 'profile.hobbies', label: 'Hobbies (comma separated)', list: true },
    { path: 'profile.quirk', label: 'Quirk' }, { path: 'profile.goal', label: 'Life goal' }, { path: 'profile.peeve', label: 'Pet peeve' },
    { path: 'profile.speech', label: 'How they talk', suggest: true, wide: true }, { path: 'profile.demeanor', label: 'How they come across', suggest: true, wide: true }] },
  { group: 'Bio', items: [{ path: 'profile.bio', label: 'Bio', type: 'textarea', wide: true, rows: 4 }] },
];

// A freeform character is drawn from the description alone, so the structured appearance fields are replaced by it.
const DESCRIPTION_GROUP = { group: 'Description', items: [{ path: 'appearance.extra', label: 'Description (drives the art)', type: 'textarea', wide: true, rows: 4 }] };
const sheetGroups = npc => (npc.freeform ? [FIELDS[0], DESCRIPTION_GROUP, ...FIELDS.slice(2)] : FIELDS);

function dialogueCard(npc) {
  if (!npc.id) return card('Dialogue pack', h('p', { class: 'hint' }, 'Save this NPC first to create and store dialogue lines.'));
  const pack = npc.dialogue;
  const status = h('span', { class: 'hint' }, S.dialogueDirty ? 'Unsaved dialogue edits' : pack?.stale ? 'Profile changed · regenerate or keep editing' : pack ? 'Generated locally from this profile' : 'No dialogue pack yet');
  const save = btn('Save lines', 'save', safe(async () => { await saveDialogue(); renderSheet(); }), { cls: 'small', disabled: !pack || !S.dialogueDirty });
  const generate = btn(pack ? 'Regenerate from profile' : 'Generate dialogue pack', 'dice', safe(async () => {
    if (S.dialogueDirty && !confirm('Replace the unsaved dialogue edits?')) return;
    if (S.dirty || S.pendingPhoto) await saveDraft();
    const result = await api('POST', `/api/npcs/${npc.id}/dialogue/generate`);
    S.draft.dialogue = result.dialogue; S.dialogueDirty = false; renderSheet();
  }), { cls: 'small' });
  const editors = pack && Object.entries(pack.lines).map(([category, rows]) =>
    h('div', { class: 'dialogue-group' }, h('h4', { class: 'lbl' }, ({ greeting: 'Greetings', idle: 'Idle', trade: 'Trade', quest: 'Quest', farewell: 'Farewells' })[category] || category),
      rows.map((line, index) => h('div', { class: 'dialogue-line' },
        h('textarea', { rows: 2, value: line.text, 'aria-label': `${category} line ${index + 1}`, oninput: e => {
          line.text = e.target.value; S.dialogueDirty = true; status.textContent = 'Unsaved dialogue edits'; save.disabled = false; updateHead();
        } }),
        lineControls(line)))));      // voiced clip, outdated badge, mood, Voice / Redo (web/voice-designer.js)
  const exports = pack && h('div', { class: 'row wrap' }, ['json', 'ink', 'yarn'].map(format =>
    h('a', { class: 'btn small ghost', href: `/api/npcs/${npc.id}/dialogue/export/${format}`, download: '' }, `Export ${format.toUpperCase()}`)));
  const el = card('Dialogue pack', status, h('p', { class: 'hint' }, 'Local templates use the NPC’s occupation, goal, hobbies, quirk, pet peeve and voice notes. Lines stay editable; no text model or cloud service is used. Add [laugh], [sigh] or [chuckle] where the voice should make the sound (the models that can, do).'),
    h('div', { class: 'row wrap' }, generate, save, exports), dialogueVoiceActions(pack), editors || null);
  el.id = 'dialogueCard';
  return el;
}

/** Redraw the Dialogue pack card when its voiced lines change (a voice job finished), unless you are in the middle of editing it. */
function refreshDialogueCard() {
  const old = $('#dialogueCard');
  if (old && S.draft && !S.dialogueDirty && !old.contains(document.activeElement)) old.replaceWith(dialogueCard(S.draft));
}

function renderSheet() {
  const box = $('#sheet'); if (!box) return;
  const npc = S.draft;
  if (!npc) {
    box.replaceChildren(h('div', { class: 'empty' }, h('h2', {}, 'No NPC yet'),
      h('p', {}, 'Roll a character and change anything you like. The character sheet needs no graphics card. Pictures, sprites and generated 3D models need ComfyUI.'),
      btn('Roll NPC', 'dice', safe(rollNpc), { cls: 'primary' })));
    return;
  }
  const label = h('div', { class: 'sheet-head' },
    h('div', {}, h('h2', { id: 'sheet-title' }), h('div', { class: 'meta', id: 'sheet-meta' })),
    h('div', { class: 'actions' },
      btn('Roll again', 'dice', safe(rollNpc), { title: 'Re-roll every field that is not locked' }),
      btn('Save', 'save', safe(saveDraft), { title: 'Save this NPC to the library' }),
      npc.id && btn('Export character card', 'download', safe(() => {
        if (S.dirty || S.dialogueDirty) throw new Error('Save your character and any dialogue edits before exporting its card.');
        return openCharacterCard(npc);
      }), { disabled: S.outdated }),
      npc.id && btn('Start scene', 'plus', () => sceneController.createWithNpc(npc),
        { title: 'Start a scene with this NPC', disabled: S.outdated || S.dirty || S.dialogueDirty }),
      npc.id && S.health?.body_3d?.bases?.length > 0 && btn('Body options', null, jumpToBody,
        { title: 'Go to the body options in the 3D model card' }),
      btn('Generate', 'zap', safe(() => generateDraft()), { cls: 'primary' }),
      npc.id && btn('', 'trash', safe(deleteDraft), { cls: 'icon danger', title: 'Delete this NPC' })));

  const prompts = h('details', { class: 'card', id: 'prompts', open: S.promptsOpen, ontoggle: e => { S.promptsOpen = e.target.open; refreshPrompts(); } },
    h('summary', {}, 'Prompts sent to the picture model'), h('div', { id: 'prompts-body', class: 'prompt-block' }));

  // The pictures and the panel beside them are separate boxes on purpose: renderGallery redraws the pictures whenever a
  // job moves on, and must never redraw the panel, where you may be typing.
  box.replaceChildren(label,
    h('div', { class: 'hero' }, h('div', { id: 'hero-media', class: 'hero-media' }), h('section', { id: 'profile-panel', class: 'card profile-panel' })),
    h('div', { id: 'gallery', class: 'gallery' }), prompts);
  updateHead();
  renderPanel();
  renderGallery();
  refreshPrompts();
}

// ---------- profile panel: a readable summary, the sheet to edit, the dialogue ----------------------------------------------------
const PANEL_TABS = [['profile', 'Profile'], ['edit', 'Edit sheet'], ['dialogue', 'Dialogue']];

function renderPanel() {
  const box = $('#profile-panel'); if (!box || !S.draft) return;
  const tab = PANEL_TABS.some(([id]) => id === S.panelTab) ? S.panelTab : 'profile';
  const pick = id => { S.panelTab = id; renderPanel(); };
  box.replaceChildren(
    h('div', { class: 'panel-tabs', role: 'tablist', 'aria-label': 'Character' }, PANEL_TABS.map(([id, label]) =>
      h('button', { type: 'button', role: 'tab', 'aria-selected': String(id === tab), onclick: () => pick(id) }, label))),
    tab === 'edit' ? sheetForm(S.draft) : tab === 'dialogue' ? dialogueCard(S.draft) : profileSummary(S.draft, () => pick('edit')));
}

function sheetForm(npc) {
  return h('div', {},
    h('p', { class: 'hint', style: { margin: '0 0 12px' } }, npc.freeform
      ? 'Character made from a description: the description is the art prompt. Name and age only appear on the data sheet. The Gender is added to the art prompt unless your description already says one.'
      : 'Edit anything. Use the padlock to keep a field when you roll again.'),
    h('div', { class: 'stack', style: { gap: '18px' } }, sheetGroups(npc).map(g =>
      h('div', {}, h('h4', { class: 'lbl' }, g.group), h('div', { class: 'form-grid' }, g.items.filter(i => !i.when || i.when(npc.setting)).map(fieldRow))))));
}

/** The character at a glance: who they are, how they behave, how they look. Empty fields and "none" are left out. */
function profileSummary(npc, onEdit) {
  const p = npc.profile || {}, a = npc.appearance || {}, st = S.cfg.settings[npc.setting] || {};
  const text = v => String(Array.isArray(v) ? v.join(', ') : v ?? '').trim();
  const shown = v => text(v) && text(v).toLowerCase() !== 'none';
  const facts = rows => {
    const kept = rows.filter(([, v]) => shown(v));
    return kept.length ? h('dl', { class: 'facts' }, kept.map(([k, v]) => [h('dt', {}, k), h('dd', {}, text(v))])) : null;
  };
  const section = (title, body) => body ? h('div', { class: 'profile-section' }, h('h4', { class: 'lbl' }, title), body) : null;
  const gender = (st.genders || S.cfg.genders).find(g => g.id === p.gender)?.label;
  return h('div', { class: 'profile' },
    h('p', { class: 'profile-who' }, [gender, p.age ? `age ${p.age}` : '', st.has_races ? p.race : p.heritage, p.occupation].filter(shown).join(' · ')),
    shown(p.bio) ? h('p', { class: 'profile-bio' }, text(p.bio)) : h('p', { class: 'hint' }, 'No bio yet. Write one under Edit sheet.'),
    section('Traits', (p.personality || []).length ? h('div', { class: 'chips' }, p.personality.map(t => h('span', { class: 'chip static' }, t))) : null),
    section('Manner', facts([['How they talk', p.speech], ['Comes across', p.demeanor], ['Quirk', p.quirk], ['Life goal', p.goal], ['Pet peeve', p.peeve], ['Hobbies', p.hobbies]])),
    section('Looks', npc.freeform ? (shown(a.extra) ? h('p', {}, text(a.extra)) : null)
      : facts([['Build', a.build], ['Skin', a.skin_tone], ['Hair', a.hair], ['Eyes', a.eyes], ['Facial hair', a.facial_hair], ['Glasses', a.glasses],
        ['Marks', a.distinguishing], ['Accessory', a.accessory], ['Outfit', a.outfit]])),
    section('World', h('p', { class: 'hint', style: { margin: 0 } }, h('strong', {}, st.label || npc.setting), st.blurb ? ` · ${st.blurb}` : '')),
    h('div', { class: 'row' }, btn('Edit sheet', null, onEdit, { cls: 'small' })));
}

function updateHead() {
  const npc = S.draft; if (!npc || !$('#sheet-title')) return;
  $('#sheet-title').textContent = fullName(npc);
  const p = npc.profile;
  const status = !npc.id ? ['Unsaved draft', 'warn'] : (S.dirty || S.dialogueDirty) ? ['Unsaved changes', 'warn'] : ['Saved', 'ok'];
  $('#sheet-meta').replaceChildren(
    h('span', {}, [p.age, p.occupation].filter(Boolean).join(' · ')),
    h('span', { class: 'badge' }, S.cfg.settings[npc.setting]?.label || npc.setting),
    h('span', { class: 'badge accent' }, S.cfg.styles[npc.style]?.label || npc.style),
    h('span', { class: `badge ${status[1]}` }, status[0]),
    ...[commercialBadge(npc)].filter(Boolean));              // (replaceChildren would write "null" for a missing badge)
}

function fieldRow(it) {
  const npc = S.draft, id = 'f-' + it.path.replace('.', '-');
  const raw = getPath(npc, it.path);
  const value = Array.isArray(raw) ? raw.join(', ') : (raw ?? '');
  let morphologyHint = null;
  const updateMorphologyHint = plan => {
    const info = S.cfg.morphologies?.[plan];
    if (morphologyHint) morphologyHint.textContent = info?.description || '';
  };
  const onval = v => {
    setPath(npc, it.path, it.list ? v.split(',').map(s => s.trim()).filter(Boolean) : it.type === 'number' ? (v === '' ? '' : Number(v)) : v);
    if (it.path === 'profile.age' && npc.profile) delete npc.profile.age_source;   // typed by hand: no longer a rolled age
    if (it.path === 'profile.race') {
      const origin = (S.cfg.settings[npc.setting]?.origins || []).find(item => item.label === v);
      if (origin?.morphology) {
        npc.morphology = origin.morphology;
        const planControl = document.getElementById('f-morphology');
        if (planControl) planControl.value = origin.morphology;
        updateMorphologyHint(origin.morphology);
      }
    }
    if (npc.id) S.dirty = true;
    if (it.path === 'morphology' || it.path === 'profile.race') renderGallery();
    updateHead(); refreshPromptsSoon();
  };
  let control;
  const own = S.cfg.settings[npc.setting] || {};                     // the setting's own limits, if it states any
  if (it.type === 'gender') control = sel((own.genders || S.cfg.genders).map(g => [g.id, g.label]), value, onval, undefined, { id });
  else if (it.type === 'origin') control = sel((own.origins || []).map(origin => [origin.label, origin.label]), value, onval, undefined, { id });
  else if (it.type === 'morphology') control = sel(Object.entries(S.cfg.morphologies || {}).map(([key, item]) => [key, item.label]), value,
    next => { onval(next); updateMorphologyHint(next); }, undefined, { id });
  else if (it.type === 'textarea') control = h('textarea', { id, rows: it.rows || 2, value, oninput: e => onval(e.target.value) });
  else control = h('input', { id, type: it.type === 'number' ? 'number' : 'text', min: it.type === 'number' ? (own.min_age || 1) : null, max: it.type === 'number' ? 120 : null, value, oninput: e => onval(e.target.value) });

  // "How they talk" and "How they come across" can be suggested from the traits (the same tables a roll uses).
  const suggest = it.suggest && h('button', { type: 'button', class: 'lock', title: 'Suggest one that suits the traits', 'aria-label': `Suggest ${it.label}`,
    onclick: safe(async () => {
      const got = await api('POST', '/api/manner', { setting: npc.setting, traits: npc.profile.personality || [] });
      const key = it.path.split('.')[1];
      if (!got[key]) return toast('This setting has no suggestions for that.', 'error');
      setPath(npc, it.path, got[key]);
      const box = document.getElementById(id); if (box) box.value = got[key];
      if (npc.id) S.dirty = true;
      refreshPromptsSoon();
    }) }, icon('dice'));
  const lock = h('button', { type: 'button', class: 'lock', 'aria-label': `Keep ${it.label} when rolling again` });
  const paint = () => {
    const on = S.locks.has(it.path);
    lock.classList.toggle('on', on); lock.setAttribute('aria-pressed', String(on));
    lock.title = on ? 'Locked: kept when you roll again' : 'Unlocked: will be re-rolled';
    lock.replaceChildren(icon(on ? 'lock' : 'unlock'));
  };
  lock.addEventListener('click', () => { S.locks.has(it.path) ? S.locks.delete(it.path) : S.locks.add(it.path); paint(); });
  paint();
  const morphologyInfo = it.type === 'morphology' ? S.cfg.morphologies?.[value] : null;
  if (morphologyInfo) morphologyHint = h('p', { class: 'hint morphology-hint' }, morphologyInfo.description);
  const label = ['profile.heritage', 'profile.race'].includes(it.path) ? S.cfg.settings[npc.setting]?.origin_label || it.label : it.label;
  return h('div', { class: 'field' + (it.wide ? ' wide' : '') }, h('label', { class: 'lbl', htmlFor: id }, label),
    h('div', { class: 'field-row' }, control, suggest || null, lock), morphologyHint);
}

// ---------- gallery ------------------------------------------------------------------------------------------------------
function busyMap(npcId) {
  const m = {};
  for (const j of S.jobs) {
    if (j.npc_id !== npcId || !isActive(j)) continue;
    for (const s of j.steps) {
      if (s.status !== 'pending' && s.status !== 'running') continue;
      const key = s.kind === 'expression' ? 'expr:' + s.key : s.kind === 'sheet' ? 'sheet:' + s.key : s.kind;
      if (m[key] !== 'Generating…') m[key] = s.status === 'running' ? 'Generating…' : 'Queued';
    }
  }
  return m;
}

// The name the server gives a picture that keeps its earlier versions: portrait, sprite, expressions/<id> or sheet/<view>.
const pictureSlot = (kind, key) => kind === 'expression' ? `expressions/${key}` : kind === 'sheet' ? `sheet/${key}` : kind;

/**
 * The earlier versions of one picture: what it was before it was drawn again (kept automatically), and variants saved on purpose.
 * Each can be brought back (the picture in use is kept in turn, so nothing is lost) or deleted for good.
 */
function versionsList(npc, slot, title, meta, versions, saved) {
  const open = S.versionsOpen[slot] ?? (slot === 'portrait' || slot === 'sprite');
  const put = async (method, path, done) => {
    const updated = await api(method, `/api/npcs/${npc.id}/assets/${slot}/candidates/${path}`);
    S.draft.assets = updated.assets;
    if (updated.style && updated.style !== S.draft.style && !S.dirty) S.draft.style = updated.style;
    renderGallery(); refreshLibrary();
    toast(done);
  };
  const describe = c => [c.seed != null ? `Seed ${c.seed}` : null, c.style ? (S.cfg.styles[c.style]?.label || c.style) : null,
    c.created ? `made ${c.created.replace('T', ' ').slice(0, 16)}` : null, c.replaced ? `replaced ${c.replaced.replace('T', ' ').slice(0, 16)}` : null]
    .filter(Boolean).join(' · ');
  return h('details', { class: 'candidate-list versions', open, ontoggle: event => { S.versionsOpen[slot] = event.target.open; } },
    h('summary', { class: 'cap hint' }, `Earlier versions · ${versions.length}`),
    h('div', { class: 'candidate-strip' }, versions.slice().reverse().map((candidate, i) => {
      const inUse = meta?.candidate_id === candidate.id;
      return h('div', { class: 'version' + (inUse ? ' selected' : '') },
        h('a', { href: assetUrl(candidate.file, candidate.created), target: '_blank', rel: 'noopener', title: `${describe(candidate)}. Click to see it full size.` },
          h('img', { src: assetUrl(candidate.thumb || candidate.file, candidate.created), alt: `${title}, earlier version ${versions.length - i}`, loading: 'lazy' })),
        h('span', { class: 'cap' }, inUse ? 'in use' : candidate.seed != null ? `#${candidate.seed}` : (candidate.replaced || candidate.created || '').slice(0, 10) || 'earlier'),
        h('div', { class: 'row' },
          btn('', 'undo', safe(() => put('POST', `${candidate.id}/select`, 'Brought back. The picture it replaced is kept in this list.')),
            { cls: 'small icon ghost', title: inUse ? 'This version is the one in use' : 'Bring this version back (the one in use is kept in this list)', disabled: inUse || !saved }),
          btn('', 'trash', safe(async () => {
            if (!confirm('Delete this earlier version for good? This cannot be undone.')) return;
            await put('DELETE', candidate.id, 'Deleted.');
          }), { cls: 'small icon ghost danger', title: 'Delete this version for good', disabled: !saved })));
    })));
}

function assetTile({ cls, title, kind, key, meta, busy, checker }) {
  const npc = S.draft, saved = !!npc.id;
  const slot = pictureSlot(kind, key);
  const versions = npc.assets?.candidates?.[slot] || [];
  const frame = h('div', { class: 'frame' + (checker && meta?.transparent ? ' checker' : '') + (busy ? ' busy' : ''), dataset: busy ? { busy } : {} },
    meta ? h('img', { src: assetUrl(meta.file, meta.created), alt: title, draggable: false })
      : h('div', { class: 'placeholder' }, saved ? 'Not generated yet' : 'Save or generate to create images'));
  const caption = [];
  if (meta) {
    caption.push(h('span', { class: 'cap' }, [meta.size?.join('×'), meta.transparent ? 'transparent' : null, kind === 'sprite' ? (meta.mode === 'match' ? 'from portrait' : 'independent') : null,
      kind === 'portrait' && meta.source === 'photo' ? 'from your photo' : null, meta.engine || null].filter(Boolean).join(' · ')));
    if (meta.engine_note) caption.push(h('span', { class: 'badge warn', title: meta.engine_note }, 'drawn with Mage-Flow'));
    if (meta.stale) caption.push(h('span', { class: 'badge warn', title: 'The portrait was regenerated after this was made. Regenerate it to match.' }, 'outdated'));
    if (kind === 'sprite' && !S.cfg.morphologies?.[npc.morphology]?.full_body_supported)
      caption.push(h('span', { class: 'badge warn', title: S.cfg.morphologies?.[npc.morphology]?.description || 'Body plan is not supported by the full-body pipeline.' }, 'body plan unsupported'));
    if (meta.style && meta.style !== npc.style) caption.push(h('span', { class: 'badge warn', title: `Made in the "${S.cfg.styles[meta.style]?.label || meta.style}" style` }, 'other style'));
    if (meta.key_error) caption.push(h('span', { class: 'badge warn', title: meta.key_error }, 'background not removed'));
  }
  const file = `${slug(fullName(npc))}-${kind}${key ? '-' + key : ''}.png`;
  return h('div', { class: `tile ${cls}` },
    h('div', { class: 'title' }, h('span', {}, title),
      h('div', { class: 'row' },
        saved && btn('', 'refresh', safe(() => regen(kind, key)), { cls: 'small icon ghost', title: meta ? 'Regenerate with a new seed' : 'Generate' }),
        meta && h('a', { class: 'btn small icon ghost', href: assetUrl(meta.file, meta.created), download: file, title: 'Download PNG', 'aria-label': 'Download PNG' }, icon('download')),
        meta && saved && kind !== 'sheet' && btn('', 'trash', safe(() => removeAsset(kind, key)), { cls: 'small icon ghost danger', title: 'Delete this image' }))),
    frame, caption.length ? h('div', { class: 'foot' }, caption) : null,
    versions.length ? versionsList(npc, slot, title, meta, versions, saved) : null);
}

function renderGallery() {
  const media = $('#hero-media'), box = $('#gallery');
  if (!media || !box || !S.draft) {
    bodyOptions.forEach(option => option.close?.());
    return;
  }
  stopAnimTimers();
  const npc = S.draft, a = npc.assets || {}, busy = busyMap(npc.id);
  const plan = S.cfg.morphologies?.[npc.morphology];
  const planWarning = plan && !plan.full_body_supported
    ? h('div', { class: 'notice morphology-warning', role: 'status' },
      h('strong', {}, `${plan.label} body plan`), h('span', {}, `${plan.description} Portraits and expressions can still be generated.`)) : null;
  media.replaceChildren(
    assetTile({ cls: 'portrait', title: 'Portrait', kind: 'portrait', meta: a.portrait, busy: busy.portrait, checker: true }),
    assetTile({ cls: 'sprite', title: 'Full body', kind: 'sprite', meta: a.sprite, busy: busy.sprite, checker: true }));
  // Every card is a small tile until it has a lot to show (a model sheet, clips, many expressions), then it takes a whole row.
  const voice = voiceCard();
  voice.classList.toggle('wide', !!npc.voice?.takes?.length && npc.voice?.review === 'pending');
  box.replaceChildren(...[planWarning,              // replaceChildren would print a null as the text "null": drop the missing ones
    h('div', { class: 'asset-grid' },
      sheetCard(npc, a, busy), model3dCard(npc, a, busy), expressionsCard(npc, a, busy), livingCard(npc, a, busy), voice, animationsCard(npc, a))].filter(Boolean));
  onVoiceChanged();
  refreshDialogueCard();
}

function expressionsCard(npc, a, busy) {
  const exprs = a.expressions || {};
  // (an expression that was deleted but still has earlier versions keeps its tile, so one can be brought back)
  const shown = S.cfg.expressions.filter(e => exprs[e.id] || busy['expr:' + e.id] || npc.assets?.candidates?.['expressions/' + e.id]?.length);
  return h('section', { class: 'card' + (shown.length > 2 ? ' wide' : '') }, h('h3', {}, 'Expressions'),
    shown.length ? h('div', { class: 'expr-grid' }, shown.map(e =>
      assetTile({ cls: 'expr', title: e.label, kind: 'expression', key: e.id, meta: exprs[e.id], busy: busy['expr:' + e.id], checker: true }))) : null,
    h('div', { class: 'chips', style: { marginTop: shown.length ? '12px' : 0 } },
      h('span', { class: 'cap hint', style: { alignSelf: 'center', margin: 0 } }, shown.length ? 'Add or redo:' : 'Generate one:'),
      S.cfg.expressions.map(e => h('button', { type: 'button', class: 'chip' + (exprs[e.id] ? ' on' : ''), disabled: !!busy['expr:' + e.id],
        title: exprs[e.id] ? 'Regenerate with a new seed' : 'Generate this expression', onclick: safe(() => regen('expression', e.id)) },
        exprs[e.id] ? e.label : '+ ' + e.label))),
    h('p', { class: 'hint' }, 'Expressions are edited from the portrait, so it is generated first if the NPC has none.'));
}

function model3dCard(npc, assets, busy) {
  // a cleaned-up model keeps its generation time but its files change: stamp their URLs with the clean-up time
  const meta = assets.model_3d?.refined ? { ...assets.model_3d, created: assets.model_3d.refined.created } : assets.model_3d;
  const running = busy.model_3d || busy.import_3d || busy.body_3d;
  const plan = S.cfg.morphologies?.[npc.morphology], unsupported = plan && !plan.full_body_supported;
  const selected = S.cfg.engines.three_d?.find(e => e.id === S.recipe.options.three_d_model)?.label || 'the selected local generator';
  const quality = S.cfg.three_d_qualities?.find(q => q.id === S.recipe.options.three_d_quality);
  const generatedQuality = meta?.quality_label ? ` · ${meta.quality_label} detail` : '';
  const frontReference = meta?.front_reference === 'portrait' ? ' · portrait face reference' : meta?.front_reference === 'turnaround' ? ' · turnaround face reference' : '';
  const runtime = meta?.generation_seconds ? ` · generated in ${Math.floor(meta.generation_seconds / 60)}m ${meta.generation_seconds % 60}s` : '';
  const count = n => n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : `${Math.round(n / 1000)}k`;
  const eyesSoftened = (meta?.glasses_off ? ' · glasses left off the model' : meta?.glasses_not_taken_off ? ' · glasses not taken off' : '')
    + (meta?.eyes_softened ? ' · eyes softened for the shape' : meta?.eyes_not_softened ? ' · eyes not softened (glasses)' : '');
  const imported = meta?.imported ? ` · ${count(meta.imported.faces_in)} faces in` + (meta.imported.cleaned ? `, ${count(meta.imported.faces)} out after ${meta.imported.seconds} s` : ', kept as it is') : '';
  return h('section', { class: 'card model3d-card' }, h('h3', {}, '3D model'),
    running ? h('div', { class: 'notice' }, h('div', { class: 'spinner' }), running) : null,
    meta?.stale ? h('span', { class: 'badge warn', title: 'The character or source sprite changed after this model was generated.' }, 'outdated') : null,
    meta?.preview ? h('img', { class: 'model3d-preview', src: assetUrl(meta.preview, meta.created), alt: `3D render of ${fullName(npc)}` }) : null,
    meta ? h('div', { class: 'row wrap model3d-actions' },
      modelPreviewButton(npc, meta),
      h('a', { class: 'btn small', href: assetUrl(meta.file, meta.created), download: `${slug(fullName(npc))}-3d.glb`, title: 'Download the 3D model' }, icon('download'), 'Download GLB'),
      btn('', 'refresh', safe(make3d), { cls: 'small icon ghost', title: 'Regenerate this model', disabled: !!running || unsupported || S.outdated }),
      btn('', 'trash', safe(remove3d), { cls: 'small icon ghost danger', title: 'Delete this 3D model' })) : null,
    !meta && !S.outdated ? btn('Generate 3D model', 'zap', safe(make3d), { cls: 'small', disabled: !!running || unsupported }) : null,
    npc.id ? uploadModelRow(npc, busy) : null,
    unsupported ? h('p', { class: 'hint' }, `There is no checked method yet for making a 3D model of a character with a ${plan.label} body plan.`) : null,
    h('p', { class: 'hint' }, meta?.size ? `${meta.designed && !S.health?.body_3d?.bases?.length ? 'Designed model' : meta.engine || '3D model'}${generatedQuality}${eyesSoftened}${imported}${frontReference}${runtime}${meta.textured ? ' · textured' : ''} · ${(meta.size / (1024 * 1024)).toFixed(1)} MB · GLB` :
      S.recipe.options.three_d_model === 'trellis2' ? 'Makes the shape locally with Trellis2 in ComfyUI from the model sheet\'s four pictures (made first if missing). It has no colour of its own: paint it with the clean-up below once it is made.' :
      `Generates a GLB locally with ${selected} in ComfyUI. Pixal3D uses full-body turnarounds for shape and the selected face source for close-up texture; its turnaround face crop is recommended, while the portrait crop is experimental.`),
    !meta && S.recipe.options.three_d_model === 'pixal3d' && quality ? h('p', { class: 'hint' }, `${quality.label}. ${quality.estimate}; actual time varies, and the high setting can take longer than an hour.`) : null,
    earlierModels(npc, assets, busy),
    bestSection(busy, unsupported),
    npc.id ? importSection(npc, meta, busy) : null,
    npc.body_extension_notice ? h('p', { class: 'notice', role: 'status' }, npc.body_extension_notice) : null,
    bodySection(npc, assets, busy),
    meta && !meta.designed ? refineSection(npc, assets.model_3d, busy) : null,   // (a designed body is not generated from pictures: nothing to clean up or paint)
    meta ? rigSection(npc, assets, busy) : null,
    headSection(npc, assets, busy));
}

// A .glb from the owner's computer as the character's 3D model, exactly as it is (npcgen/jobs.py use_uploaded_model). The raw bytes go to the server with a content type of their own,
// because the server reads JSON everywhere else. The model it replaces is kept among the earlier ones.
const MAX_MODEL_UPLOAD = 320 * 1048576;                      // (server.py MAX_MODEL_BODY)

function uploadModelRow(npc, busy) {
  const uploading = S.uploadingModel === npc.id;
  const off = uploading || Object.keys(busy).length > 0 || S.outdated;
  return h('div', { class: 'row wrap model3d-actions' },
    h('input', { type: 'file', id: 'model-file', accept: '.glb,model/gltf-binary', hidden: true, onchange: safe(onModelFileChosen) }),
    btn('Use my own 3D model…', 'upload', () => $('#model-file').click(), { cls: 'small', disabled: off,
      title: 'Pick a .glb file on this computer and use it as this character’s 3D model, exactly as it is. The current model is kept in the list of earlier 3D models.' }),
    uploading ? h('span', { class: 'hint' }, h('span', { class: 'spinner' }), ' Sending the file…') : null);
}

async function onModelFileChosen(event) {
  const input = event.target, file = input.files && input.files[0];
  input.value = '';                                          // (so that the same file can be chosen again)
  if (!file || !S.draft?.id) return;
  if (!/\.glb$/i.test(file.name)) throw new Error('Choose a .glb file: a binary glTF, which most 3D programs can export (“Export as glb”).');
  if (file.size > MAX_MODEL_UPLOAD) throw new Error(`${file.name} is ${Math.round(file.size / 1048576)} MB and the most that can be sent here is ${Math.round(MAX_MODEL_UPLOAD / 1048576)} MB. A raw shape from a generator is bigger: put it in ComfyUI’s input\\3d or output folder and use “Import a 3D model made elsewhere” below.`);
  const current = S.draft.assets?.model_3d;
  if (current && !confirm(`This uses ${file.name} as the 3D model instead of the current one. The current one is kept in the list of earlier 3D models, so you can bring it back. Continue?`)) return;
  const id = S.draft.id;
  S.uploadingModel = id; renderGallery();
  try {
    let response;
    try {
      response = await fetch(`/api/npcs/${id}/model-upload?name=${encodeURIComponent(file.name)}`, { method: 'POST', headers: { 'Content-Type': 'application/x-npcforge-glb' }, body: file });
    } catch { throw new Error('Cannot reach the NPC server. Is it still running?'); }
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `The server reported an error (code ${response.status}) and gave no details.`);
    if (S.draft?.id === id) S.draft.assets = data.assets;
    (data.upload?.warnings || []).forEach(warning => toast(warning[0].toUpperCase() + warning.slice(1) + '.', 'error'));
    toast(`${file.name} is the 3D model now${current ? '. The one it replaced is in the list of earlier 3D models' : ''}.`);
    refreshLibrary();
  } finally {
    S.uploadingModel = null;
    if (S.draft?.id === id) renderGallery();
  }
}

// The 3D models this character had before (npcgen/library.py archive_model): a model that is replaced (by another generator's, an import, a designed body) or deleted is kept
// with its skeleton and everything else it had, and one click brings it back. The model in use is kept in its place, so nothing is lost either way.
function earlierModels(npc, assets, busy) {
  const list = assets.model_history || [];
  if (!npc.id || !list.length) return null;
  const working = Object.keys(busy).length > 0;                   // (the server refuses while this character has work queued or running)
  const when = text => (text || '').replace('T', ' ').slice(0, 16);
  const mb = bytes => bytes >= 10 * 1048576 ? `${Math.round(bytes / 1048576)} MB` : `${(bytes / 1048576).toFixed(1)} MB`;
  const put = async (method, path, done) => {
    const updated = await api(method, `/api/npcs/${npc.id}/model-history/${path}`);
    S.draft.assets = updated.assets;
    renderGallery(); refreshLibrary();
    toast(done);
  };
  return h('details', { class: 'candidate-list versions', open: S.versionsOpen.models ?? true, ontoggle: event => { S.versionsOpen.models = event.target.open; } },
    h('summary', { class: 'cap hint' }, `Earlier 3D models · ${list.length}`),
    h('div', { class: 'stack' }, list.slice().reverse().map(entry => {
      const model = entry.model || {};
      const picture = model.preview && (entry.names || []).includes(model.preview) ? `model_history/${entry.id}/${model.preview}` : null;
      const what = [model.engine || '3D model', model.textured ? 'textured' : null, entry.rig ? 'with its skeleton' : null, mb(entry.bytes || 0)].filter(Boolean).join(' · ');
      return h('div', { class: 'row', style: { alignItems: 'center', gap: '10px' } },
        picture ? h('img', { src: assetUrl(picture, entry.replaced), alt: `Earlier 3D model: ${model.engine || ''}`, loading: 'lazy', style: { width: '44px', height: '66px', objectFit: 'contain', flex: '0 0 auto' } }) : null,
        h('div', { style: { flex: '1 1 auto', minWidth: 0 } },
          h('div', {}, what),
          h('div', { class: 'cap hint' }, `made ${when(model.created)} · replaced ${when(entry.replaced)}`)),
        btn('Bring back', 'undo', safe(() => put('POST', `${entry.id}/restore`, 'Brought back. The 3D model that was in use is kept in this list.')),
          { cls: 'small', disabled: working || S.outdated, title: 'Make this the 3D model again, with its skeleton (the one in use is kept in this list)' }),
        btn('', 'trash', safe(async () => {
          if (!confirm('Delete this earlier 3D model for good? This cannot be undone.')) return;
          await put('DELETE', entry.id, 'Deleted.');
        }), { cls: 'small icon ghost danger', disabled: working || S.outdated, title: 'Delete this earlier 3D model for good' }));
    })),
    h('p', { class: 'hint' }, `The last ${S.cfg.library?.model_history ?? 2} models are kept when one is replaced or deleted. Each takes room: the size is shown.`));
}

// The whole chain that came out best, as one job (npcgen/best3d.py ticks it for this character). Every part of it is also an option or a
// button of its own below: a future generator or LoRA may not need some of it, so this is only ever a shortcut, done when it is pressed.
function bestSection(busy, unsupported) {
  const running = busy.model_3d || busy.import_3d || busy.refine_3d || busy.head_3d || busy.head_view;
  const status = S.health?.best_3d, missing = !status ? ['a check of what is installed'] : status.missing || [];
  const off = !!running || !!unsupported || S.outdated || !!missing.length;
  const about = S.cfg.best_3d || { steps: [], estimate: '' };
  return h('div', { class: 'model3d-rig stack' },
    h('h4', {}, 'Best quality'),
    h('div', { class: 'row wrap model3d-actions' },
      btn('Best quality: make it all', 'zap', safe(makeBest), { cls: 'small', disabled: off,
        title: missing.length ? `Needs ${missing.join(' and ')}` : 'Makes the whole chain that came out best, as one job' })),
    h('p', { class: 'hint' }, missing.length ? `Needs ${missing.join(' and ')}.`
      : `Makes, in one job: ${about.steps.join('; then ')}. ${about.estimate} Each part is also an option below, and none of it runs unless this is pressed.`));
}

async function makeBest() {
  if (S.draft?.assets?.model_3d && !confirm('This makes a new 3D model and replaces the current one, with its clean-up. A detailed head that is up to date is kept. Continue?')) return;
  await generateDraft({ portrait: false, sprite: false, expressions: [], best_3d: true }, { reroll: true });
}

// A 3D model made elsewhere (npcgen/import3d.py): a GLB that ComfyUI can see (one it saved in its output folder, or one put in its
// input or input/3d folder), closed and simplified by ComfyUI's own mesh nodes and stored as this character's model, which the Game
// version section below then cleans up and paints. Only ever done from here: nothing is imported unless asked.
function importState(npcId) {
  if (S.importState?.npcId !== npcId)
    S.importState = { npcId, open: false, sources: null, loading: false, file: '', typed: '', info: null, clean: true, detail: 'medium', error: '' };
  return S.importState;
}

async function loadImportSources() {
  const st = S.importState;
  st.loading = true; st.error = ''; renderGallery();
  try { st.sources = (await api('GET', '/api/import-3d/sources')).sources; }
  catch (error) { st.sources = []; st.error = error.message; }
  st.loading = false; renderGallery();
}

// `file` is a name from the list, or typed (a file in a sub-folder of ComfyUI's output folder is not in the list); the server says
// which folder a typed name without one is in, and what is inside
async function pickImportFile(file, typed = false) {
  const st = S.importState;
  st.file = typed ? '' : file; st.typed = typed ? file : ''; st.info = null; st.error = '';
  if (file) {
    try {
      st.info = await api('GET', '/api/import-3d/inspect?model_file=' + encodeURIComponent(file));
      st.clean = st.info.clean;
      st.file = st.info.model_file;
    } catch (error) { st.error = error.message; }
  }
  renderGallery();
}

async function importModel() {
  const st = S.importState;
  if (S.draft.assets?.model_3d && !confirm('This replaces the current 3D model and its clean-up, and marks its skeleton out of date. Continue?')) return;
  await generateDraft({ portrait: false, sprite: false, expressions: [], import_3d: { model_file: st.file, clean: st.clean, detail: st.detail } });
}

function importSection(npc, meta, busy) {
  const st = importState(npc.id), running = busy.import_3d;
  const status = S.health?.import_3d, missing = !status ? ['a check of what is installed'] : status.missing || [];
  const off = !!running || !!busy.model_3d || S.outdated;
  const details = S.cfg.three_d_import_details || [], files = st.sources || [], info = st.info;
  const count = n => n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : `${Math.round(n / 1000)}k`;
  return h('details', { class: 'model3d-rig model3d-import', open: st.open, ontoggle: e => {
      st.open = e.target.open;
      if (st.open && !st.sources && !st.loading) loadImportSources();
    } },
    h('summary', {}, meta?.imported ? 'Import another 3D model' : 'Import a 3D model made elsewhere'),
    h('div', { class: 'stack' },
      h('p', { class: 'hint' }, 'Use a model from another ComfyUI workflow instead of generating one here, e.g. a shape from the Trellis2 nodes made from this character\'s four model-sheet pictures. Choose a .glb that ComfyUI can see: one it saved in its output folder, or one you put in its input or input/3d folder.'),
      missing.length ? h('p', { class: 'hint' }, `Needs ${missing.join(' and ')} in ComfyUI.`) : null,
      h('div', { class: 'row wrap' },
        sel(files.map(f => [f.model_file, `${f.name} · ComfyUI ${f.folder}`]), st.typed ? '' : st.file, v => safe(pickImportFile)(v),
          st.loading ? 'Looking…' : files.length ? 'Choose a GLB…' : 'No GLB files found', { 'aria-label': 'GLB file to import', disabled: off || st.loading }),
        btn('', 'refresh', safe(loadImportSources), { cls: 'small icon ghost', title: 'Look at ComfyUI\'s folders again', disabled: st.loading || off })),
      h('label', { class: 'row wrap', title: 'For a file in a sub-folder of ComfyUI\'s output folder, which the list does not show. Without a folder in brackets, its output folder is tried first, then its input folder.' },
        h('span', { class: 'hint' }, 'or type its name'),
        h('input', { type: 'text', value: st.typed, placeholder: '3D/Trellis2_00001_.glb [output]', 'aria-label': 'GLB file name', disabled: off,
          style: { flex: '1 1 220px' }, onchange: e => safe(pickImportFile)(e.target.value.trim(), true) })),
      st.error ? h('p', { class: 'hint' }, st.error) : null,
      info ? h('p', { class: 'hint' }, [`${count(info.faces)} faces`, info.textured ? 'has its own colour' : 'no colour of its own',
        `${Math.round(info.bytes / 1048576)} MB`, ...info.warnings].join(' · ')) : null,
      info ? check('Close the holes and simplify the mesh in ComfyUI', st.clean, v => { st.clean = v; renderGallery(); },
        'Rebuilds the surface as one closed mesh with no holes and brings it down to the face count below (about half a minute). A raw generator shape of millions of faces needs it to be painted; it drops a texture the file has.') : null,
      info && st.clean && details.length ? field('Faces to keep', sel(details.map(d => [d.id, d.label]), st.detail, v => { st.detail = v; renderGallery(); }),
        details.find(d => d.id === st.detail)?.summary) : null,
      h('div', { class: 'row wrap model3d-actions' },
        btn(running ? 'Importing…' : 'Import', 'zap', safe(importModel), { cls: 'small', disabled: off || !info || !!missing.length,
          title: 'Make this the character\'s 3D model; paint it from the pictures afterwards in Game version below' }))));
}

// The game version of a textured model: hidden inner skin removed, painted from the turnaround pictures, simplified
// with the detail kept in baked maps (npcgen/cleanup3d.py). Options, ticked per character: a sharper face (head close-ups
// upscaled in ComfyUI and a finer head texture, npcgen/headdetail.py), smoothed eye rings (npcgen/eyes.py), hair
// painted only from hair (npcgen/hairmask.py), clean five-finger hands (npcgen/hands3d.py) and the detailed head's face laid over
// the model's own (npcgen/headgraft.py). Only ever done from this section: a future generator or LoRA may not need any of it.
const REFINE_OPTIONS = [
  { key: 'face', flag: 'sharp_face', label: 'Sharper face', health: 'sharp_face',
    hint: a => `Upscales the head of each turnaround picture 4× with ${a.upscaler} in ComfyUI${a.licence ? ` (licence: ${a.licence})` : ''} and gives the head a finer texture of its own.` },
  { key: 'eyes', flag: 'smooth_eyes', label: 'Smooth eye rings', health: 'smooth_eyes',
    hint: a => `Only for models with ring- or goggle-like loops sculpted around the eyes: it flattens normal eyes too. Finds the eyes with ${a.model} in LM Studio.` },
  { key: 'hair', flag: 'match_hair', label: 'Match the hair', health: 'match_hair',
    hint: () => 'For hair the generator made bigger than the pictures have it (a lower fringe, locks over the cheeks): paints the model\'s hair only from hair, not the skin behind it. Finds the hair with SAM 3.1 in ComfyUI (SAM Licence).' },
  { key: 'hands', flag: 'clean_hands', label: 'Clean hands', health: 'clean_hands',
    hint: () => 'Replaces the generator\'s mitten-like hands with clean five-finger ones in the skin colour, so the medium skeleton\'s finger bones have real fingers to move. Not for paws, claws or hooves. Finds the hands with SDPose in ComfyUI.' },
  { key: 'head', flag: 'attach_head', label: 'Attach the detailed head', health: 'attach_head',
    need: npc => npc.assets?.head_3d?.file ? '' : 'the detailed head (make it below first)',
    hint: () => 'Lays the detailed head\'s face (chin to the hairline, cheek to cheek) over the model\'s own mask of a face, so the character, its skeleton and its game version have the fine face; the body keeps its own hair, ears and neck. Finds the faces with SDPose in ComfyUI. A first version: the eyes, nose and lips come out much sharper and the patch ends along a smooth curve at the hairline, but the seam shows from the side as a faint crease and glasses and hair over the face come out worst, so look at the result before you rely on it. Not for characters without a human face, or with something over it.' },
];

function refineSection(npc, meta, busy) {
  const running = busy.refine_3d || (busy.model_3d || busy.import_3d ? 'Waiting for the new model…' : null);
  const blender = S.health?.rig_3d, noBlender = blender && !blender.blender;
  const r = meta.refined, face = r?.face, eyes = r?.eyes, hair = r?.hair, hands = r?.hands, attached = r?.head;
  // a model with no colour of its own (Hunyuan's shapes) gets a texture layout and is painted from the model sheet;
  // missing sheet pictures are queued first (npcgen/unwrap.py, Jobs.plan)
  const blank = !meta.textured, sheetViews = npc.assets?.sheet?.views || {};
  const toMake = blank ? ['front', 'side_left', 'back', 'side_right'].filter(v => !sheetViews[v]?.file || sheetViews[v]?.stale) : [];
  // the game version's size is a choice per character: what it was made with last time, else the server's usual size
  const chosen = (S.refineOptions ||= {})[npc.id] ||= { face: !!face, eyes: !!eyes, hair: !!hair, hands: !!hands && !hands.error, head: !!attached && !attached.error,
    faces: r?.faces_requested || S.cfg.defaults?.options?.game_faces || 100000 };
  const k = n => n >= 1000 ? `${Math.round(n / 1000)}k` : `${n}`;
  const headMade = npc.assets?.head_3d?.created;
  const facts = r ? [`${k(r.faces)} faces (the generator made ${k(r.faces_generated)}; ${k(r.inner_skin_removed)} of them hidden inner skin)`,
    r.painted_from?.length ? `painted from ${r.painted_from.length} turnaround pictures${r.unwrapped ? ' onto a texture layout of its own' : ''}` : 'not painted: no turnaround pictures',
    face ? `sharper face: head close-ups upscaled with ${face.upscaler}${face.licence ? ` (${face.licence})` : ''}, ${face.texels_before && face.texels ? `${(face.texels / face.texels_before).toFixed(1)}× the texture detail on the head` : 'a finer head texture'}` : null,
    eyes ? `eye rings smoothed${eyes.model ? ` (eyes found by ${eyes.model})` : ''}` : null,
    hair ? `hair painted only from hair (found by SAM 3.1${hair.texels_refused ? `; ${k(hair.texels_refused)} texture pixels repainted from hair instead of the skin behind it` : ''})` : null,
    hands ? (hands.error ? `hands left as the generator made them: ${hands.error}` : `clean five-finger hands (placed with SDPose${hands.colour_note ? `; ${hands.colour_note}` : ''})`) : null,
    attached ? (attached.error ? `face left as the generator made it: ${attached.error}` : `the detailed head's face laid over the model's (${k(attached.patch_faces)} faces of it${attached.fit?.points != null ? `, fitted on ${attached.fit.points} marked points of the face` : ''}${headMade && attached.source_head_created && attached.source_head_created !== headMade ? '; the head has been made again since: clean up again to use the new one' : ''})`) : null]
    .filter(Boolean).join(' · ') : '';
  const off = !!running || noBlender || S.outdated;
  // a dense shape (an import, Trellis2: hundreds of thousands of faces) takes most of its time in the texture layout: measured 15 minutes at 700k faces
  const dense = meta.engine_id === 'import' || meta.engine_id === 'trellis2';
  const shapeBy = meta.engine_id === 'import' ? 'an imported shape has its form only' : `${meta.engine || 'the generator'} makes the shape only`;
  const blankTime = dense ? 'about 15 minutes for 700,000 faces (measured; fewer faces are quicker)' : `about ${meta.quality === 'fine' ? 'four' : 'two'} minutes`;
  const options = REFINE_OPTIONS.map(o => {
    const a = S.health?.[o.health], missing = (!a ? ['a check of what is installed'] : a.missing || []).concat(o.need ? [o.need(npc)].filter(Boolean) : []);
    if (missing.length) chosen[o.key] = false;
    return h('label', { class: 'check', title: missing.length ? `Needs ${missing.join(' and ')}` : '' },
      h('input', { type: 'checkbox', checked: !!chosen[o.key], disabled: off || !!missing.length,
        onchange: e => { chosen[o.key] = e.target.checked; if (o.key === 'hands') renderGallery(); } }),
      h('span', {}, h('span', { class: 'name' }, o.label),
        h('span', { class: 'hint', style: { display: 'block' } }, missing.length ? `Needs ${missing.join(' and ')}.` : o.hint(a))));
  });
  const go = () => refine3d(chosen);
  const sizes = (S.cfg.game_sizes || []).map(s => [String(s.faces), s.label]);
  if (sizes.length && !sizes.some(([faces]) => +faces === +chosen.faces)) sizes.push([String(chosen.faces), `${(+chosen.faces).toLocaleString('en-US')} faces (custom)`]);
  const sizePicker = sizes.length ? field('Game size', sel(sizes, String(chosen.faces), v => { chosen.faces = +v; renderGallery(); }, undefined, { 'aria-label': 'Game version size', disabled: off }),
    'How many faces the game version keeps, for this character. Fewer load and animate faster in a game (the detail stays in the baked normal map, and the file shrinks from 26 MB at 200,000 faces to 16 MB at 25,000, mostly texture); more keep the fingers and the face rounder. The clean-up takes the same time at every size. An attached detailed head adds about 19,000 faces to the size you pick.') : null;
  // measured on three characters: a hand has 15 to 130 faces at 25,000, 120 to 520 at 100,000 and 320 to 1,200 at 200,000
  const handsWarning = chosen.hands && +chosen.faces < 100000 ? h('p', { class: 'hint warn-text' },
    'Clean hands need 100,000 faces or more to have real fingers: at this size a hand is only 15 to 130 faces, a mitten again.') : null;
  return h('div', { class: 'model3d-rig stack' },
    h('h4', {}, 'Game version'),
    running ? h('div', { class: 'notice' }, h('div', { class: 'spinner' }), running) : null,
    sizePicker,
    handsWarning,
    h('div', { class: 'stack model3d-options' }, options),
    r ? h('div', { class: 'row wrap model3d-actions' },
      h('a', { class: 'btn small ghost', href: assetUrl(meta.raw, meta.created), download: `${slug(fullName(npc))}-3d-original.glb`,
        title: 'The model exactly as the generator made it' }, icon('download'), 'Original GLB'),
      btn('Clean up again', 'refresh', safe(go), { cls: 'small', disabled: off, title: 'Clean up and paint again from the original, with the options ticked above' }))
      : h('div', { class: 'row wrap model3d-actions' },
        btn(blank ? 'Paint from pictures + clean up' : 'Clean up + paint from pictures', 'zap', safe(go), { cls: 'small', disabled: off,
          title: noBlender ? 'Blender was not found' : 'Remove hidden faces, paint the model from the turnaround pictures and make a lighter game version, with the options ticked above' })),
    r ? checkSection(npc, meta, busy, off) : null,
    h('p', { class: 'hint' }, r ? facts : noBlender ? 'Blender was not found. Install it from blender.org, or set "blender" in config.json.'
      : blank ? `This model has no colour of its own (${shapeBy}). It gets a texture layout and is painted with the model sheet's front, side and back pictures${toMake.length ? ` (${toMake.map(v => v.replace('_', ' ')).join(', ')} made first in ComfyUI)` : ''}; what no picture sees takes the nearest painted colour. Then a lighter game version: ${blankTime} on the CPU. The original is kept.`
      : 'Removes the hidden inner skin, paints the model with the turnaround pictures\' own pixels and makes a lighter game version, about two minutes on the CPU (about three with the options). The original is kept.'));
}

// A before/after check of the clean-up by a vision model in LM Studio (npcgen/check3d.py): its opinion, asked twice with
// the two models swapped, only when the button is pressed.
function checkSection(npc, meta, busy, off) {
  const c = meta.refined?.check, qa = S.health?.check_3d, missing = !qa ? ['a check of what is installed'] : qa.missing || [];
  const front = npc.assets?.sheet?.views?.front?.file;
  const running = busy.check_3d;
  const why = missing.length ? `Needs ${missing.join(' and ')}.` : !front ? 'Needs the model sheet\'s front picture.' : '';
  const said = c ? { after: 'The clean-up looks better than the original.', before: 'The original looks better than the clean-up.',
    unclear: c.agreed === false ? 'No clear difference: asked twice with the two swapped, the answers disagreed.' : 'No clear difference.' }[c.verdict] : '';
  const list = (label, side) => side?.problems?.length ? h('p', { class: 'hint' }, `${label}: ${side.problems.join('; ')}.`) : null;
  return h('div', { class: 'stack' },
    running ? h('div', { class: 'notice' }, h('div', { class: 'spinner' }), running) : null,
    h('div', { class: 'row wrap model3d-actions' },
      btn(c ? 'Check again' : 'Check with LM Studio', 'eye', safe(check3d), { cls: 'small ghost', disabled: off || !!running || !!why,
        title: why || 'Render the original and the cleaned-up model and ask a vision model in LM Studio which is closer to the model sheet, and what looks wrong' })),
    c ? h('a', { href: assetUrl(c.board, c.created), target: '_blank', title: 'The pictures the vision model compared' },
      h('img', { class: 'model3d-preview', src: assetUrl(c.board, c.created), alt: 'The model sheet front, the original and the cleaned-up model' })) : null,
    c ? h('p', { class: 'hint' }, `${said} Scores ${c.before.score} → ${c.after.score} of 10, by ${c.model}. Its opinion, not a measurement: look before you trust it.`) : null,
    c ? list('It saw in the cleaned-up model', c.after) : null,
    c ? list('…and in the original', c.before) : null,
    !c ? h('p', { class: 'hint' }, why || `Optional: renders the original and the cleaned-up model from the front and asks ${qa.model} in LM Studio which is closer to the model sheet, and what looks wrong. About two minutes.`) : null);
}

async function check3d() {
  await generateDraft({ portrait: false, sprite: false, expressions: [], check_3d: true });
}

const defaultColour = kind => S.cfg?.items?.clothing?.kinds?.[kind]?.recipe?.colour || '#808080';      // the Body Designer's own colour for a garment
const KIND_NAME = { shirt: 'a sleeveless shirt', trousers: 'fitted trousers', skirt: 'a knee-length skirt', dress: 'a sleeveless knee-length dress', jacket: 'an open jacket', apron: 'a bib apron' };    // the garments of a designed body, as a sentence names them
const GARMENT_NAME = { shirt: 'shirt', trousers: 'trousers', skirt: 'skirt', dress: 'dress', jacket: 'jacket', apron: 'apron' };
// A designed body (npcgen/bodymaker.py): the Body Designer's adult body built from its recipe, rigged, and given the animation library's clips, with
// Blender on the CPU and no GPU. It becomes this character's 3D model and skeleton in one step. Only ever done from here: nothing is made unless asked.
const bodyOptions = [];

function bodyState(npcId) {
  if (S.bodyState?.npcId !== npcId) S.bodyState = { npcId, loaded: false, loading: false, saved: false, base: '', skeleton: 'low', useSaved: false, face: '', texture: 2048, match: true, said: {}, skipped: {}, clothes: 'basic', outer: 'sheet', colours: {}, recolouring: false, hair: '', outfit: {}, hairSheet: {}, marks: '', marksInfo: null, expressions: '', swingHair: '', swingCloth: '', shape: null };
  return S.bodyState;
}

async function loadBodyState(npcId) {
  const st = bodyState(npcId);
  if (st.loaded || st.loading) return;
  st.loading = true;
  try { const info = await api('GET', `/api/npcs/${npcId}/body-3d`); st.saved = info.saved; st.base ||= info.base || ''; st.said = info.match?.said || {}; st.skipped = info.match?.skipped || {}; st.outfit = info.outfit || {}; st.hairSheet = info.match?.hair || {}; st.marksInfo = info.marks || null; st.shape = info.shape || null; }
  catch { /* the card works without it */ }
  st.loading = false; st.loaded = true;
  if (S.draft?.id === npcId) renderGallery();
}

/** What the card's Face expressions says: 'on' or 'off', or '' for a body taken from the Body Designer tab (as saved). A body made again starts as the one it replaces. */
const shapesValue = (st, designed) => st.expressions || (st.useSaved && st.saved ? '' : designed?.recipe?.expressions ? 'on' : 'off');

/** What a swinging option says (`key` is the card's field, `part` 'hair' or 'cloth'): 'on' or 'off', or '' for a body taken from the Body Designer tab (as saved). A body made again starts as the one it replaces. */
const swingValue = (st, key, part, designed) => st[key] || (st.useSaved && st.saved ? '' : designed?.recipe?.secondary?.[part] ? 'on' : 'off');

/** The choice in the Clothes list that a local extension added (npcgen/bodymaker.py EXTRA_CLOTHES), if that is what is chosen: it has no garments, and the underwear it names. */
const extraClothes = (st, health) => (health.clothes || []).find(x => x.id === st.clothes && x.extra);

/** Which of the two swinging options the other choices on the card allow: swinging hair needs the Long hair, swinging cloth a skirt or a dress with nothing over it. Each is true, or a sentence saying what is missing. */
function swingAllowed(st, health) {
  const bare = extraClothes(st, health);
  if (bare && !bare.underwear?.length) { const why = 'The Body Designer adds swinging to a body in its underwear, and the clothes chosen leave it off.'; return { hair: why, cloth: why }; }
  const savedBody = st.useSaved && st.saved, hairValue = st.hair || (savedBody ? '' : 'sheet'), hs = st.hairSheet || {};
  const longHair = hairValue === 'long' || !hairValue || (hairValue === 'sheet' && (hs.style === 'long' || (hs.near === 'long' && (health.hairs || []).some(x => x.id === 'long'))));    // ('' is a saved body: the server looks)
  const kinds = st.clothes === 'none' ? [] : (st.outfit?.[st.clothes]?.[st.outer || 'sheet']?.pieces || []).map(p => p.kind);
  const lower = kinds.filter(k => k === 'skirt' || k === 'dress').length === 1, over = kinds.some(k => k === 'jacket' || k === 'apron');
  return { hair: longHair || 'It needs the Long hair, so choose Long hair above.',
    cloth: lower && !over ? true : over ? 'It is made for a skirt or a dress with nothing over it.' : 'It needs a skirt or a dress on the body, so see Clothes above.' };
}

/** The `secondary` field of a request for a body: which parts swing, or null to say nothing (both "as saved"). A part the other choices do not allow is off. */
function swingRequest(st, health, designed) {
  if (!health.swinging) return null;
  const allowed = swingAllowed(st, health), hair = swingValue(st, 'swingHair', 'hair', designed), cloth = swingValue(st, 'swingCloth', 'cloth', designed);
  if (!hair && !cloth) return null;
  return { hair: allowed.hair === true && hair === 'on', cloth: allowed.cloth === true && cloth === 'on' };
}

/** Words as a list in a sentence: 'a', 'a and b', 'a, b and c'. */
/** The "Body options" button in a character's header: the Designed body section is a long way down the 3D model card, so scroll it into view (below the bar that stays at the top of the page). */
function jumpToBody() {
  const target = document.getElementById('designed-body');
  if (!target) return;
  const bar = document.querySelector('header.topbar');
  window.scrollTo(0, target.getBoundingClientRect().top + window.scrollY - (bar ? bar.getBoundingClientRect().height : 0) - 12);
}

async function useBody() {
  const st = S.bodyState, health = S.health?.body_3d, designed = S.draft.assets?.model_3d?.designed;
  const shapes = shapesValue(st, designed), swing = swingRequest(st, health, designed);
  const base = st.base || health.bases[0].id;
  const body = !(st.useSaved && st.saved) && designed?.base === base ? designed.body : null;
  const additions = Object.assign({}, ...bodyOptions.map(option => option.request?.({ st, health, designed, base })));
  if (S.draft.assets?.model_3d && !confirm('This replaces the current 3D model and its skeleton with a new adult body. A model made from pictures takes a long time to make again. Continue?')) return;
  await generateDraft({ portrait: false, sprite: false, expressions: [],
    body_3d: { base: st.base || health.bases[0].id, skeleton: st.skeleton, saved: st.useSaved && st.saved, replace: true, texture: st.texture, match: st.match,
      ...(health.clothes?.length ? (extraClothes(st, health) ? { clothes: st.clothes, outer: 'none' } : { clothes: st.clothes, outer: st.outer, colours: st.colours }) : {}), ...(st.hair ? { hair: st.hair } : {}),
      ...(st.face ? { face: st.face } : {}), ...(health.marks?.length && st.marks ? { marks: st.marks } : {}),
      ...(health.expressions && shapes && st.face !== 'classic' ? { expressions: shapes === 'on' } : {}), ...(swing ? { secondary: swing } : {}), ...(body ? { body } : {}), ...additions } });          // (no face, hair or marks chosen: the Natural face, the sheet's hair and the sheet's marks for a new body, the saved ones for a saved one; face shapes and swinging parts only as chosen)
}

/** The colours chosen on the card go onto the clothes the body wears now, with no new body (npcgen/bodymaker.py `recolour`: the cloth of both files and the card's picture). */
async function recolourBody() {
  const st = S.bodyState, designed = S.draft.assets?.model_3d?.designed;
  const colours = Object.fromEntries((designed?.clothes || []).filter(c => st.colours[c.kind]).map(c => [c.kind, st.colours[c.kind]]));
  st.recolouring = true; renderGallery();
  try {
    const done = await api('POST', `/api/npcs/${S.draft.id}/body-3d/colours`, { colours });
    const npc = await api('GET', `/api/npcs/${S.draft.id}`);
    S.draft.assets = npc.assets;
    toast(Object.keys(done.changed).length ? `Colours changed: ${Object.keys(done.changed).map(k => GARMENT_NAME[k] || k).join(', ')}.` : 'It already has those colours.');
    refreshLibrary();
  } finally { st.recolouring = false; renderGallery(); }
}

function bodySection(npc, assets, busy) {
  const health = S.health?.body_3d;
  if (!health?.bases?.length || !npc.id) {
    bodyOptions.forEach(option => option.close?.());
    return null;
  }
  const st = bodyState(npc.id);
  loadBodyState(npc.id);
  const running = busy.body_3d, designed = assets.model_3d?.designed, missing = health.missing || [];
  const twoLegged = !npc.morphology || npc.morphology === 'bipedal';
  const off = !!running || !!busy.model_3d || !!busy.import_3d || !!busy.rig_3d || !!busy.refine_3d || !!missing.length || !twoLegged || S.outdated;
  const why = missing.length ? `Needs ${missing.join(' and ')}.` : !twoLegged ? 'Bodies are made for two-legged characters only.' : '';
  const chosen = health.bases.find(b => b.id === st.base) || health.bases[0];
  const skeleton = health.skeletons.find(k => k.id === st.skeleton) || health.skeletons[0];
  const faces = health.faces || [], textures = health.textures || [];
  const savedBody = st.useSaved && st.saved, faceValue = st.face || (savedBody ? '' : 'natural');
  const faceNote = faces.find(f => f.id === faceValue)?.note || 'The face the Body Designer tab saved with this body.';
  const sayings = { build: v => `${v} build`, skin_tone: v => `${v} skin`, eyes: v => `${v} eyes`, hair: v => `brows from “${v}”`, hair_style: v => `hair style “${v}”` };
  const cap = t => t ? t[0].toUpperCase() + t.slice(1) : '';
  const kindName = KIND_NAME;
  const dot = c => h('span', { title: c, style: { display: 'inline-block', width: '0.85em', height: '0.85em', borderRadius: '50%', background: c, boxShadow: '0 0 0 1px var(--border)', margin: '0 0.1em 0 0.25em', verticalAlign: '-0.1em' } });
  const clothesList = health.clothes || [], hairList = health.hairs || [], outersList = health.outers || [], marksList = health.marks || [], shapesInfo = health.expressions || null;
  if (clothesList.length && !clothesList.some(x => x.id === st.clothes)) st.clothes = 'basic';          // (a choice a local extension has stopped showing is not kept)
  const extra = extraClothes(st, health);
  const hairValue = st.hair || (savedBody ? '' : 'sheet'), hairSaid = st.said.hair_style || st.skipped.hair_style || '';
  const wearing = st.outfit?.[st.clothes]?.[st.outer || 'sheet'];
  const unmade = (wearing?.left || []).filter(w => !wearing.why?.[w]), held = (wearing?.left || []).filter(w => wearing.why?.[w]);      // the words the Body Designer cannot make / makes but is not putting on
  const clothesNote = extra ? extra.note : st.clothes === 'none' ? 'No clothes: the body stays in its underwear.' : !wearing ? '' : h('span', {},
    wearing.problem ? h('span', { class: 'err' }, `${wearing.problem}. `) : '',
    wearing.pieces.length ? ['Wears ', ...wearing.pieces.flatMap((p, i) => [i ? (i === wearing.pieces.length - 1 ? ' and ' : ', ') : '', kindName[p.kind], dot(p.colour || defaultColour(p.kind))]), ' (the colours are the sheet’s where it names them).']
      : 'Wears nothing over its underwear: the sheet’s outfit names no shirt, trousers, skirt or dress.',
    unmade.length ? ` The sheet also names ${unmade.join(', ')}, which the Body Designer does not make yet.` : '',
    ...held.map(w => ` The sheet also names ${w}, which is not put on: ${wearing.why[w][0].toLowerCase()}${wearing.why[w].slice(1)}.`));
  const touched = Object.fromEntries((designed?.clothes || []).filter(c => st.colours[c.kind] && st.colours[c.kind].toLowerCase() !== String(c.colour || '').toLowerCase()).map(c => [c.kind, st.colours[c.kind]]));
  const colourRow = clothesList.length && st.clothes !== 'none' && wearing?.pieces?.length ? h('div', { class: 'row wrap' },
    h('span', { class: 'hint' }, 'Colours'),
    wearing.pieces.map(p => h('label', { class: 'row', title: `The colour of the ${GARMENT_NAME[p.kind]}: the sheet’s where it names one, else the Body Designer’s own` },
      h('span', { class: 'hint' }, GARMENT_NAME[p.kind]),
      h('input', { type: 'color', class: 'g-colour', value: st.colours[p.kind] || p.colour || defaultColour(p.kind), 'aria-label': `Colour of the ${GARMENT_NAME[p.kind]}`,
        oninput: event => { st.colours[p.kind] = event.target.value; }, onchange: () => renderGallery() }),
      st.colours[p.kind] ? h('button', { type: 'button', class: 'btn small', title: 'Back to the sheet’s colour', 'aria-label': `Back to the sheet’s colour for the ${GARMENT_NAME[p.kind]}`,
        onclick: () => { delete st.colours[p.kind]; renderGallery(); } }, '↺') : null))) : null;
  const hs = st.hairSheet || {};
  const hairNote = hairValue === 'sheet' ? (hs.style && hs.style !== 'none' ? `${cap(hs.style)} hair${hairSaid ? `, like “${hairSaid}”` : ''}.${hs.style === 'long' ? ' It is straight and smooth: the Body Designer has one long style.' : ''}`
      : hs.style === 'none' ? 'A bare head: the sheet says bald.'
      : hs.near && hairList.some(x => x.id === hs.near) ? `${cap(hs.near)} hair as the nearest: it is straight and smooth, and the sheet says “${hairSaid}”.`
      : hairSaid ? `Crop hair as a stand-in: the Body Designer has no hair for “${hairSaid}” yet.` : 'The sheet has no hair to take: Crop hair.')
    : hairList.find(x => x.id === hairValue)?.note || 'The hair the Body Designer tab saved with this body.';
  const mi = st.marksInfo, marksValue = st.marks || (savedBody ? '' : 'sheet'), shapesChoice = shapesValue(st, designed);
  const marksNote = marksValue === 'sheet' ? (mi ? `${mi.text ? `${cap(mi.text)}.` : 'The sheet names no skin marks the body can paint.'}`
        + `${mi.assumed?.length ? ` The sheet names ${mi.assumed.join(' and ')} but not where, so the places are the usual ones for such skin.` : ''}`
        + `${mi.skipped?.length ? ` The sheet also names ${mi.skipped.join('; ')}, which the Body Designer does not paint.` : ''}${mi.dropped ? ` ${mi.dropped} more than the body can have were left out.` : ''}`
        + ' They are painted into the skin for good: to change them, make the body again.' + (st.texture === 512 ? ' At the 512 px skin small marks blur or vanish: use 1024 or 2048.' : '') : '')
    : marksValue === 'none' ? 'Clear skin.' : 'The marks the Body Designer tab saved with this body.';
  const shapesNote = !shapesInfo ? '' : shapesChoice === 'on' ? `${shapesInfo.note} Clothes tried on from the Items tab then take about a minute, not half a minute: the body is built again to fit them.`
    : shapesChoice === 'off' ? 'No face shapes: the face stays still.' : 'As the Body Designer tab saved them.';
  const said = Object.entries(st.said || {}).map(([k, v]) => (sayings[k] || (x => x))(v)), left = Object.entries(st.skipped || {}).map(([k, v]) => `${({ skin_tone: 'skin', hair_style: 'hair style' })[k] || k} “${v}”`);
  const matchNote = said.length || left.length ? `From the sheet: ${said.join(', ') || 'nothing the body can use'}.${left.length ? ` Left out, not known to the body: ${left.join(', ')}.` : ''}`
    : 'The sheet has no build, skin or eye colour to take.';
  const swingInfo = health.swinging || null, allowed = swingInfo ? swingAllowed(st, health) : {};
  const swingSelect = (part, key) => {                      // (a part the other choices do not allow shows Off and is greyed, and says why when the pointer rests on it)
    const info = swingInfo[part], ok = allowed[part] === true;
    return h('label', { class: 'row wrap', title: ok ? info.note : `${info.label} is off. ${allowed[part]}` }, h('span', { class: 'hint' }, info.label),
      sel([...(savedBody && ok ? [['', 'As saved']] : []), ['off', 'Off'], ['on', `On (+${info.bones} bones)`]], ok ? swingValue(st, key, part, designed) : 'off', v => { st[key] = v; renderGallery(); },
        undefined, { 'aria-label': info.label, disabled: !ok }));
  };
  const swungNames = designed?.swinging ? designed.swinging.parts.map(p => p === 'hair' ? 'hair' : p) : [];
  const shaped = designed ? Object.entries(designed.recipe?.values || {}).filter(([, v]) => v).map(([k, v]) => `${k.replace(/_/g, ' ')} ${v > 0 ? '+' : ''}${v}`) : [];
  const wornList = designed ? [...(designed.wears?.kinds?.length ? [[`${designed.wears.kinds.join(' and ')} (underwear)`]] : designed.clothes_choice ? [[(clothesList.find(x => x.id === designed.clothes_choice)?.label || designed.clothes_choice).toLowerCase()]] : []),
    ...(designed.clothes || []).map(c => [kindName[c.kind] || c.kind, c.colour ? dot(c.colour) : ''])] : [];
  return h('div', { class: 'model3d-rig stack', id: 'designed-body' },
    h('h4', {}, 'Designed body'),
    running ? h('div', { class: 'notice' }, h('div', { class: 'spinner' }), running) : null,
    st.recolouring ? h('div', { class: 'notice' }, h('div', { class: 'spinner' }), 'Putting the new colours on the body…') : null,
    designed ? h('p', { class: 'hint' }, `This character's model is a designed body: ${designed.label}, ${designed.skeleton} skeleton, ${designed.height_m} m tall`
      + `${shaped.length ? ', ' + shaped.join(', ') : ', neutral proportions'}${designed.face ? `, ${designed.recipe?.version === 11 ? 'Polish' : designed.face === 'natural' ? 'Natural' : 'Classic'} face` : ''}${designed.recipe?.hair ? `, ${cap(designed.recipe.hair.style)} hair` : ''}${designed.marks?.text ? `, skin marks (${designed.marks.text})` : ''}${designed.expressions ? ', face expressions' : ''}${swungNames.length ? `, spring bones on its ${swungNames.join(' and ')} (${designed.swinging.joints - designed.swinging.core_joints} more bones)` : ''}`,
      wornList.length ? [', wearing ', ...wornList.flatMap((n, i) => [i ? ', ' : '', ...n])] : '',
      `${Object.keys(designed.clothes_failed || {}).length ? ` (no ${Object.keys(designed.clothes_failed).join(' or ')}: it did not fit this body)` : ''}${Object.keys(designed.swinging_left_off || {}).length ? ` (no swinging ${Object.keys(designed.swinging_left_off).join(' or ')}: ${Object.values(designed.swinging_left_off).join('; ')})` : ''}${designed.from_saved ? ' (from the Body Designer tab)' : ''}`
      + `${designed.matched?.said && Object.keys(designed.matched.said).length ? `, matched to the sheet (${Object.entries(designed.matched.said).map(([k, v]) => (sayings[k] || (x => x))(v)).join(', ')})` : ''}.`) : null,
    h('div', { class: 'row wrap' },
      h('label', { class: 'row wrap' }, h('span', { class: 'hint' }, 'Body'),
        sel(health.bases.map(b => [b.id, b.label]), chosen.id, v => { st.base = v; bodyOptions.forEach(option => option.reset?.(st)); renderGallery(); }, undefined, { 'aria-label': 'Body' })),
      h('label', { class: 'row wrap', title: skeleton.note }, h('span', { class: 'hint' }, 'Skeleton'),
        sel(health.skeletons.map(k => [k.id, k.label]), skeleton.id, v => { st.skeleton = v; renderGallery(); }, undefined, { 'aria-label': 'Skeleton of the designed body' })),
      faces.length > 1 ? h('label', { class: 'row wrap', title: faceNote }, h('span', { class: 'hint' }, 'Face'),
        sel([...(savedBody ? [['', 'As saved']] : []), ...faces.map(f => [f.id, f.label])], faceValue, v => { st.face = v; renderGallery(); },
          undefined, { 'aria-label': 'Face of the designed body' })) : null,
      hairList.length ? h('label', { class: 'row wrap', title: hairNote }, h('span', { class: 'hint' }, 'Hair'),
        sel([...(savedBody ? [['', 'As saved']] : []), ...hairList.map(x => [x.id, x.label])], hairValue, v => { st.hair = v; renderGallery(); },
          undefined, { 'aria-label': 'Hair of the designed body' })) : null,
      marksList.length ? h('label', { class: 'row wrap', title: marksNote }, h('span', { class: 'hint' }, 'Skin marks'),
        sel([...(savedBody ? [['', 'As saved']] : []), ...marksList.map(x => [x.id, x.label])], marksValue, v => { st.marks = v; renderGallery(); },
          undefined, { 'aria-label': 'Skin marks of the designed body' })) : null,
      shapesInfo && faceValue !== 'classic' ? h('label', { class: 'row wrap', title: shapesNote }, h('span', { class: 'hint' }, 'Face expressions'),
        sel([...(savedBody ? [['', 'As saved']] : []), ['off', 'Off'], ['on', `On (${shapesInfo.presets.length} shapes)`]], shapesChoice, v => { st.expressions = v; renderGallery(); },
          undefined, { 'aria-label': 'Face expressions of the designed body' })) : null,
      swingInfo ? swingSelect('hair', 'swingHair') : null,
      swingInfo ? swingSelect('cloth', 'swingCloth') : null,
      clothesList.length ? h('label', { class: 'row wrap', title: clothesList.find(x => x.id === st.clothes)?.note }, h('span', { class: 'hint' }, 'Clothes'),
        sel(clothesList.map(x => [x.id, x.label]), st.clothes, v => { st.clothes = v; renderGallery(); }, undefined, { 'aria-label': 'Clothes of the designed body' })) : null,
      outersList.length && st.clothes !== 'none' && !extra ? h('label', { class: 'row wrap', title: outersList.find(x => x.id === st.outer)?.note }, h('span', { class: 'hint' }, 'Over them'),
        sel(outersList.map(x => [x.id, x.label]), st.outer, v => { st.outer = v; renderGallery(); }, undefined, { 'aria-label': 'What goes over the shirt and trousers' })) : null,
      textures.length ? h('label', { class: 'row wrap', title: textures.find(t => t.id === st.texture)?.note }, h('span', { class: 'hint' }, 'Skin'),
        sel(textures.map(t => [t.id, t.label]), st.texture, v => { st.texture = Number(v); renderGallery(); },
          undefined, { 'aria-label': 'Skin size of the designed body' })) : null),
    ...bodyOptions.map(option => option.render?.({ npc, assets, health, st, designed, chosen, off, running,
      outdated: S.outdated, redraw: renderGallery })),
    hairList.length || clothesList.length ? h('p', { class: 'hint' }, hairList.length ? `Hair: ${hairNote} ` : '', clothesList.length ? ['Clothes: ', clothesNote] : '') : null,
    marksList.length || (shapesInfo && faceValue !== 'classic') ? h('p', { class: 'hint' }, marksList.length ? `Skin marks: ${marksNote} ` : '', shapesInfo && faceValue !== 'classic' ? `Face expressions: ${shapesNote}` : '') : null,
    swingInfo ? h('p', { class: 'hint' }, `Swinging hair and cloth: ${swingInfo.note}`, ...['hair', 'cloth'].filter(part => allowed[part] !== true).map(part => ` ${swingInfo[part].label} is off: ${allowed[part][0].toLowerCase()}${allowed[part].slice(1)}`)) : null,
    colourRow,
    savedBody ? null : check('Match the body to the character', st.match, v => { st.match = v; renderGallery(); }, matchNote),
    st.saved ? check('Take the body saved in the Body Designer tab', st.useSaved, v => { st.useSaved = v; bodyOptions.forEach(option => option.reset?.(st)); renderGallery(); },
      'Its proportions, colours and face. For the other body: its proportions only, with the colours the sheet describes.') : null,
    h('div', { class: 'row wrap model3d-actions' },
      btn(designed ? 'Make the body again' : 'Use this body', 'zap', safe(useBody), { cls: 'small', disabled: off || !!wearing?.problem || !!npc.body_extension_notice,
        title: why || wearing?.problem || npc.body_extension_notice || "Build the body, rig it and add the animation library's clips" }),
      designed?.clothes?.length ? btn('Change the colours of this body', null, safe(recolourBody), { cls: 'small', disabled: off || st.recolouring || !Object.keys(touched).length,
        title: Object.keys(touched).length ? 'Put the colours chosen above on the clothes this body wears, without making it again (a few seconds)' : 'Choose a colour above first' }) : null),
    h('p', { class: 'hint' }, why || "Builds the Body Designer's adult body (no GPU; about a minute), rigs it with the animation library's 85 clips and makes it this "
      + (extra ? "character's 3D model. What it wears is as the Clothes choice above says, with the hair chosen above." : "character's 3D model. It wears plain underwear, a layer of its own, then the clothes and the hair chosen above.")
      + (assets.model_3d && !designed ? ' It replaces the current model and skeleton.' : '')));
}

async function refine3d(chosen) {
  const flags = Object.fromEntries(REFINE_OPTIONS.filter(o => chosen[o.key]).map(o => [o.flag, true]));
  await generateDraft({ portrait: false, sprite: false, expressions: [], refine_3d: true, ...flags }, { game_faces: +chosen.faces || undefined });
}

// A skeleton, skin weights and animation clips, made by Blender on the CPU from the 3D model (npcgen/rig3d.py): Idle, Walk
// and Wave made here, or the clips of Quaternius' animation library when it is installed (scripts/install_animations.py).
function rigSection(npc, assets, busy) {
  const rig = assets.rig_3d, running = busy.rig_3d || ((busy.model_3d || busy.refine_3d) && rig ? 'Waiting for the new model…' : null);
  const designed = !!assets.model_3d?.designed;     // a designed body brings its own skeleton and clips (bodySection)
  const blender = S.health?.rig_3d, noBlender = blender && !blender.blender;
  const twoLegged = !npc.morphology || npc.morphology === 'bipedal';
  const blocked = noBlender || !twoLegged || S.outdated;
  const why = noBlender ? 'Blender was not found. Install it from blender.org, or set "blender" in config.json.'
    : !twoLegged ? 'Skeletons are made for two-legged characters only so far.' : '';
  const skeletons = S.cfg.rig_skeletons || [];
  const pose = S.health?.rig_pose, noPose = !pose || pose.missing?.length;
  const chosen = skeletons.find(k => k.id === S.recipe.options.rig_skeleton) || skeletons[0];
  const mediumBlocked = chosen?.id === 'medium' && noPose;
  const clipSets = S.cfg.rig_clip_sets || [], libraryReady = !!S.health?.rig_library?.installed;
  const clipChoice = S.recipe.options.rig_clips || 'auto';
  const clips = clipChoice === 'auto' ? (libraryReady ? 'library' : 'basic') : clipChoice;
  const libraryBlocked = clips === 'library' && !libraryReady;
  const clipNote = clipSets.find(k => k.id === clips)?.note || '';
  const clipPicker = clipSets.length ? h('label', { class: 'row wrap', title: clipNote },
    h('span', { class: 'hint' }, 'Clips'),
    sel(clipSets.map(k => [k.id, k.label + (k.id === 'library' && !libraryReady ? ' (not installed)' : '')]), clips,
      v => { S.recipe.options.rig_clips = v; saveRecipe(); renderGallery(); }, undefined, { 'aria-label': 'Animation clips' })) : null;
  const adds = clips === 'library' ? "the animation library's clips" : 'Idle, Walk and Wave clips';
  const skeletonPicker = skeletons.length ? h('label', { class: 'row wrap', title: chosen?.note || '' },
    h('span', { class: 'hint' }, 'Skeleton'),
    sel(skeletons.map(k => [k.id, k.label + (k.id === 'medium' && noPose ? ' (needs SDPose)' : '')]), chosen?.id,
      v => { S.recipe.options.rig_skeleton = v; saveRecipe(); renderGallery(); }, undefined, { 'aria-label': 'Skeleton' })) : null;
  const facts = rig ? [`${rig.bones || 'Humanoid'} bones${rig.skeleton ? ` (${rig.skeleton}${rig.core_bones ? `: ${rig.core_bones} of the body and ${rig.bones - rig.core_bones} spring bones that the game engine swings` : ''})` : ''}, Unreal mannequin names`,
    rig.joints_from ? (rig.joints_from === 'designed body' ? 'the designed body\'s own skeleton' : `joints from ${rig.joints_from === 'pose keypoints' ? 'SDPose' : 'the outline'}`) : '',
    rig.clip_set === 'library' ? `${(rig.clips || []).length} clips (Quaternius' animation library, CC0, and our Wave)`
      : `clips: ${(rig.clips || []).join(', ')}`,
    rig.size ? `${(rig.size / (1024 * 1024)).toFixed(1)} MB · GLB` : ''].filter(Boolean).join(' · ') : '';
  return h('div', { class: 'model3d-rig stack' },
    h('h4', {}, 'Skeleton and animations'),
    running ? h('div', { class: 'notice' }, h('div', { class: 'spinner' }), running) : null,
    rig?.stale ? h('span', { class: 'badge warn', title: 'The 3D model changed after this skeleton was made.' }, 'outdated') : null,
    rig?.preview ? h('img', { class: 'model3d-preview', src: assetUrl(rig.preview, rig.created), alt: `${fullName(npc)} mid-walk` }) : null,
    designed ? null : skeletonPicker,
    designed ? null : clipPicker,
    rig ? h('div', { class: 'row wrap model3d-actions' },
      modelPreviewButton(npc, rig),
      h('a', { class: 'btn small', href: assetUrl(rig.file, rig.created), download: `${slug(fullName(npc))}-rigged.glb`, title: 'Download the rigged, animated model' }, icon('download'), 'Download rigged GLB'),
      designed ? null : btn('', 'refresh', safe(makeRig), { cls: 'small icon ghost', title: `Make the skeleton again (${chosen?.label || 'low'}, ${adds})`, disabled: !!running || blocked || mediumBlocked || libraryBlocked }),
      btn('', 'trash', safe(removeRig), { cls: 'small icon ghost danger', title: 'Delete the skeleton and animations' }))
      : btn('Add skeleton + animations', 'zap', safe(makeRig), { cls: 'small', disabled: !!running || blocked || mediumBlocked || libraryBlocked, title: why || `Rig this model and add ${adds}` }),
    mediumBlocked ? h('p', { class: 'hint' }, 'The medium skeleton needs SDPose in ComfyUI (' + (pose?.missing || ['ComfyUI']).join(', ') + ').') : null,
    libraryBlocked ? h('p', { class: 'hint' }, 'The animation library is not installed. Run: python scripts/install_animations.py') : null,
    h('p', { class: 'hint' }, rig ? facts : why || (chosen?.note ? chosen.note + ' ' : '') + `Adds skin weights and ${adds} with Blender (a few minutes); the joints are found with SDPose in ComfyUI when it is installed. Works best when the model sheet is in an A-pose.`),
    rig?.clip_set === 'library' ? h('details', { class: 'hint' }, h('summary', {}, 'Clip names'), (rig.clips || []).join(', ')) : null,
    rig?.notes?.length ? h('p', { class: 'hint' }, 'Notes: ' + rig.notes.join('; ') + '.') : null);
}

// A detailed head: four close-ups of the head drawn from the portrait, and a separate, much finer Pixal3D model of it
// (npcgen/head3d.py). The whole-body run reads a face from about 125 pixels; this one fills the frame with the head.
// Only ever made from this section: a future generator or LoRA may not need it.
function headSection(npc, assets, busy) {
  const head = assets.head_3d, model = head?.file ? head : null, views = head?.views || {};
  const running = busy.head_3d || busy.head_view;
  const health = S.health?.head_3d, missing = !health ? ['a check of what is installed'] : health.missing || [];
  const qualities = S.cfg.head_qualities || [], expressions = S.cfg.head_expressions || [];
  const quality = qualities.find(q => q.id === S.recipe.options.head_quality) || qualities[0];
  const off = !!running || !!missing.length || S.outdated;
  const why = missing.length ? `Needs ${missing.join(' and ')}.` : '';
  const qualityPicker = qualities.length ? h('label', { class: 'row wrap', title: quality?.summary || '' },
    h('span', { class: 'hint' }, 'Detail'),
    sel(qualities.map(q => [q.id, q.label]), quality?.id, v => { S.recipe.options.head_quality = v; saveRecipe(); renderGallery(); },
      undefined, { 'aria-label': 'Detailed head quality' })) : null;
  const expressionPicker = expressions.length ? h('label', { class: 'row wrap',
    title: 'A neutral face suits a head whose jaw will move; "as in the portrait" keeps a smile, and bakes it into the shape.' },
    h('span', { class: 'hint' }, 'Face'),
    sel(expressions.map(e => [e.id, e.label]), S.recipe.options.head_expression, v => { S.recipe.options.head_expression = v; saveRecipe(); renderGallery(); },
      undefined, { 'aria-label': 'Detailed head expression' })) : null;
  const hairHealth = S.health?.match_hair, hairMissing = !hairHealth ? ['a check of what is installed'] : hairHealth.missing || [];
  if (hairMissing.length) S.recipe.options.head_match_hair = false;
  const hairOption = h('label', { class: 'check', title: hairMissing.length ? `Needs ${hairMissing.join(' and ')}` : '' },
    h('input', { type: 'checkbox', checked: !!S.recipe.options.head_match_hair, disabled: off || !!hairMissing.length,
      onchange: e => { S.recipe.options.head_match_hair = e.target.checked; saveRecipe(); } }),
    h('span', {}, h('span', { class: 'name' }, 'Match the hair'),
      h('span', { class: 'hint', style: { display: 'block' } }, hairMissing.length ? `Needs ${hairMissing.join(' and ')}.`
        : 'Keeps skin off hair that falls over the face (a side picture shows the cheek behind a lock that the front picture shows as hair), '
          + 'with SAM 3.1 in ComfyUI. About 3 minutes more; it helps most with long hair, and can leave a grey patch.')));
  const k = n => n >= 1000 ? `${Math.round(n / 1000)}k` : `${n}`;
  const facts = model ? [`${k(model.faces)} faces (the generator made ${k(model.faces_generated)}: ${k(model.inner_skin_removed)} were hidden inner skin${model.specks_removed ? `, ${k(model.specks_removed)} loose specks` : ''})`,
    `${model.quality_label} detail`, model.generation_seconds ? `generated in ${Math.floor(model.generation_seconds / 60)}m ${model.generation_seconds % 60}s` : '',
    model.expression === 'neutral' ? 'neutral face' : model.expression === 'keep' ? 'the portrait\'s expression' : '',
    typeof model.reference === 'string' ? `drawn from the ${model.reference}` : '',
    `${(model.size / (1024 * 1024)).toFixed(1)} MB · GLB`].filter(Boolean).join(' · ') : '';
  const pictures = ['front', 'side_left', 'back', 'side_right'].filter(v => views[v]?.file);
  // the close-ups are drawn from the model sheet's front picture (or the portrait): redrawn since, they still show the character as they were
  const latest = [assets.sheet?.views?.front?.created, assets.portrait?.created].filter(Boolean).sort().pop();
  const oldPictures = !!(pictures.length && views.front?.created && latest && views.front.created < latest);
  return h('div', { class: 'model3d-rig stack' },
    h('h4', {}, 'Detailed head'),
    running ? h('div', { class: 'notice' }, h('div', { class: 'spinner' }), running) : null,
    head?.stale ? h('span', { class: 'badge warn', title: 'The portrait or its close-ups changed after this head was made.' }, 'outdated') : null,
    model?.preview ? h('img', { class: 'model3d-preview', src: assetUrl(model.preview, model.created), alt: `3D render of ${fullName(npc)}'s head` }) : null,
    oldPictures ? h('p', { class: 'hint' }, h('span', { class: 'err' }, 'These close-ups are out of date. '),
      'They were drawn before the portrait or the model sheet was last redrawn, so they still show the earlier drawing.') : null,
    pictures.length ? h('div', { class: 'head-pictures' }, pictures.map(v =>
      h('figure', {},
        h('a', { href: assetUrl(views[v].file, views[v].created), target: '_blank', title: `${v.replace('_', ' ')} picture` },
          h('img', { src: assetUrl(views[v].file, views[v].created), alt: `${fullName(npc)}, ${v.replace('_', ' ')} close-up`, loading: 'lazy' })),
        btn('Redo', '', safe(() => redoHeadPicture(v)), { cls: 'small ghost', disabled: off,
          title: v === 'front' ? 'Draw the front picture again with a new seed, and the other three from it (they are all drawn from the front one)'
            : `Draw the ${v.replace('_', ' ')} picture again with a new seed, from the front picture` })))) : null,
    h('div', { class: 'row wrap' }, qualityPicker, expressionPicker),
    hairOption,
    pictures.length && (head.stale || !model) ? h('div', { class: 'row wrap model3d-actions' },
      oldPictures ? btn('Draw the close-ups again and make the head', 'zap', safe(makeHead),
        { cls: 'small', disabled: off, title: 'Draw all four close-ups again from the character as they are now, then make the 3D head from them' })
        : btn(model ? 'Rebuild the head from these pictures' : 'Make the head from these pictures', 'zap', safe(rebuildHead),
          { cls: 'small', disabled: off || pictures.length < 4,
            title: pictures.length < 4 ? 'Needs all four pictures' : 'Keep the four close-ups as they are and make the 3D head from them' })) : null,
    model ? h('div', { class: 'row wrap model3d-actions' },
      modelPreviewButton(npc, model),
      h('a', { class: 'btn small', href: assetUrl(model.file, model.created), download: `${slug(fullName(npc))}-head.glb`, title: 'Download the head model' }, icon('download'), 'Download head GLB'),
      h('a', { class: 'btn small ghost', href: assetUrl(model.raw, model.created), download: `${slug(fullName(npc))}-head-original.glb`,
        title: 'The head exactly as the generator made it, with its hidden inner skin' }, icon('download'), 'Original GLB'),
      btn('', 'refresh', safe(makeHead), { cls: 'small icon ghost', disabled: off, title: 'Make the pictures and the head again with a new seed' }),
      btn('', 'trash', safe(removeHead), { cls: 'small icon ghost danger', title: 'Delete the detailed head' }))
      : h('div', { class: 'row wrap model3d-actions' },
        btn('Make a detailed head', 'zap', safe(makeHead), { cls: 'small', disabled: off,
          title: why || 'Draw four close-ups of the head from the portrait (made first if there is none) and make a separate, finer 3D model of it' })),
    h('p', { class: 'hint' }, model ? facts : why ||
      `A separate 3D model of the head alone: four close-ups are drawn from the portrait, then Pixal3D builds real eyelids, nostrils and lips from them, about ${quality?.estimate?.replace('About ', '') || '20 minutes'}. The body model is not touched.`),
    model?.notes?.length ? h('p', { class: 'hint' }, 'Notes: ' + model.notes.join('; ') + '.') : null);
}

async function makeHead() {
  await generateDraft({ portrait: false, sprite: false, expressions: [], head_3d: true }, { reroll: true });
}

// One close-up drawn again with a new seed (the head is then outdated until it is rebuilt from the four), or the head
// rebuilt from the four the character already has. The other close-ups are kept.
async function redoHeadPicture(view) {
  await generateDraft({ portrait: false, sprite: false, expressions: [], head_3d: { views: [view], model: false } }, { reroll: true });
}

async function rebuildHead() {
  await generateDraft({ portrait: false, sprite: false, expressions: [], head_3d: { views: [], model: true } }, { reroll: true });
}

async function removeHead() {
  if (!confirm('Delete the detailed head and its four close-ups? The body model stays.')) return;
  const npc = await api('DELETE', `/api/npcs/${S.draft.id}/assets/head_3d`);
  S.draft.assets = npc.assets; renderGallery(); refreshLibrary();
}

async function makeRig() {
  await generateDraft({ portrait: false, sprite: false, expressions: [], rig_3d: true });
}

async function removeRig() {
  if (!confirm('Delete the skeleton and animations? The 3D model stays.')) return;
  const npc = await api('DELETE', `/api/npcs/${S.draft.id}/assets/rig_3d`);
  S.draft.assets = npc.assets; renderGallery(); refreshLibrary();
}

async function make3d() {
  await generateDraft({ portrait: false, sprite: false, expressions: [], model_3d: true }, { reroll: !!S.draft?.assets?.model_3d });
}

async function remove3d() {
  if (!confirm('Delete this 3D model?')) return;
  const npc = await api('DELETE', `/api/npcs/${S.draft.id}/assets/model_3d`);
  S.draft.assets = npc.assets; renderGallery(); refreshLibrary();
}

// ---------- living portrait ------------------------------------------------------------------------------------------------
// Frames for companion apps: the portrait, with only the face blended in from each "living" expression (npcgen/living.py).
function livingCard(npc, a, busy) {
  const meta = a.living, running = busy.living;
  const wanted = S.cfg.expressions.filter(e => e.living), exprs = a.expressions || {};
  const missing = wanted.filter(e => !exprs[e.id] || exprs[e.id].stale);
  const ready = meta && !meta.stale && a.portrait;
  const badges = [];
  if (meta?.stale) badges.push(h('span', { class: 'badge warn', title: 'The portrait was regenerated after this was made.' }, 'outdated'));
  if (ready) badges.push(h('span', { class: 'badge ok' }, `${meta.frames.length} frames`));
  if (ready && meta.missing?.length) badges.push(h('span', { class: 'badge warn', title: 'Update to make them' }, `${meta.missing.length} missing`));
  return h('section', { class: 'card' + (ready ? ' wide' : '') }, h('h3', {}, 'Living portrait'),
    running ? h('div', { class: 'notice' }, h('div', { class: 'spinner' }), running) : null,
    meta ? h('div', { class: 'clip-head' }, ...badges, h('span', { class: 'grow' }),
      btn('', 'trash', safe(removeLiving), { cls: 'small icon ghost danger', title: 'Delete the living portrait (the expressions stay)' })) : null,
    ready ? livingPreview(npc, a) : null,
    ready ? livingFrames(a, busy, !!running) : null,
    npc.id || !meta ? h('div', { class: 'chips', style: { marginTop: '10px' } },
      btn(meta && !meta.stale ? 'Update living portrait' : 'Make living portrait', 'zap', safe(makeLiving), { cls: 'small', disabled: !!running }),
      missing.length ? h('span', { class: 'cap hint', style: { alignSelf: 'center', margin: 0 } },
        `${missing.length} expression${missing.length === 1 ? '' : 's'} to draw first (${missing.map(e => e.label.toLowerCase()).join(', ')})`) : null) : null,
    h('p', { class: 'hint' }, 'For companion and chat apps: the portrait blinks, moves its mouth while talking and changes expression. ' +
      'It is made of ' + wanted.map(e => e.label.toLowerCase()).join(', ') + '. Only the missing ones are drawn, then each is lined up with ' +
      'the portrait and only the part of the face that changed is blended in, so the hair, clothes and background never move.'));
}

/** The portrait, living: it blinks, talks for a moment, then shows one of her expressions, over and over. */
function livingPreview(npc, a) {
  const frames = a.living.frames || [], url = id => assetUrl(`living/${id}.png`, a.living.created);
  const base = assetUrl(a.portrait.file, a.portrait.created);
  frames.forEach(id => { const pre = new Image(); pre.src = url(id); });
  const moods = frames.filter(id => id !== 'blink' && id !== 'talking');
  const img = h('img', { src: base, alt: `Living portrait of ${fullName(npc)}`, draggable: false });
  const label = h('span', { class: 'living-label' }, 'resting');
  let tick = 0, mood = 0, talk = null;        // talk: {audio, mouth} while a voiced line plays
  const show = (src, text) => { if (img.getAttribute('src') !== src) img.src = src; label.textContent = text; };
  // Lip sync: a voiced dialogue line plays and the mouth follows its mouth-opening track (25 values a second, npcgen/voice/audio.py)
  const ref = npc.voice?.reference?.id;
  const lineText = id => Object.values(npc.dialogue?.lines || {}).flat().find(l => l.id === id)?.text;
  const voiced = Object.entries(npc.voice_lines || {}).filter(([id, m]) => m.mouth?.length && m.text === lineText(id) && (!ref || m.reference === ref));
  const say = () => {
    if (talk) { talk.audio.pause(); talk = null; return; }
    const [id, meta] = voiced[Math.floor(Math.random() * voiced.length)];
    const audio = new Audio(assetUrl(meta.file, meta.created));
    talk = { audio, mouth: meta.mouth };
    // Speech stays loud through a whole phrase (measured on real lines: dips between syllables are small), so loudness alone
    // held the mouth open for seconds. With two frames the classic way reads better: the track says WHEN she speaks (pauses
    // close the mouth), and while she speaks the mouth flaps at about four and a half syllables a second, opening early on a
    // sudden rise in loudness.
    const at = i => talk.mouth[i] || 0;
    // A 40 ms timer (the track's own rate), not requestAnimationFrame: that one stops whenever the page is not being drawn,
    // which froze the mouth while the line kept playing.
    let timer = null;
    const stop = () => { clearInterval(timer); if (talk?.audio === audio) talk = null; show(base, 'resting'); };
    const step = () => {
      if (!talk || talk.audio !== audio) return clearInterval(timer);
      const t = audio.currentTime, i = Math.floor(t * 25);
      const open = at(i) > 0.35 && (Math.floor(t * 9) % 2 === 0 || at(i) > at(i - 1) + 0.15);
      show(open && frames.includes('talking') ? url('talking') : base, `“${lineText(id)}”`);
    };
    audio.onended = audio.onerror = stop;
    audio.play().then(() => { timer = setInterval(step, 40); S.animTimers.push(timer); }).catch(() => { talk = null; });
  };
  S.animTimers.push(setInterval(() => {
    if (talk) return;                            // a voiced line is playing: the mouth follows it instead
    const t = tick++ % 44;                       // one round is 44 ticks of 150 ms: rest, blink, talk, an expression
    if (t === 10 && frames.includes('blink')) show(url('blink'), 'blink');
    else if (t >= 14 && t < 28 && frames.includes('talking')) show(t % 2 ? url('talking') : base, 'talking');
    else if (t >= 28 && moods.length) { if (t === 28) mood = (mood + 1) % moods.length; show(url(moods[mood]), moods[mood]); }
    else show(base, 'resting');
  }, 150));
  return h('div', { class: 'living-preview' }, img, label,
    voiced.length ? btn('Say a line', 'zap', say, { cls: 'small living-say', title: 'Play one of the voiced dialogue lines with the mouth moving in time' }) : null);
}

/** Every frame, to look over one by one. Now and then the edit model redraws the whole head (bigger, turned), which no
 *  blending can hide: it shows as a doubled cheek or jaw. Redo draws that one expression again and rebuilds the set. */
function livingFrames(a, busy, running) {
  const label = id => (S.cfg.expressions.find(e => e.id === id) || { label: id }).label;
  const [fy, fx] = a.living.face || [0.36, 0.5];     // zoomed in on her face: a doubled cheek is invisible in a whole portrait
  const zoom = { transformOrigin: `${(fx * 100).toFixed(1)}% ${(fy * 100).toFixed(1)}%` };
  return h('div', { class: 'living-frames' },
    a.living.frames.map(id => h('figure', { class: 'living-frame' },
      h('div', { class: 'living-crop' },
        h('img', { src: assetUrl(`living/${id}.png`, a.living.created), alt: label(id), loading: 'lazy', draggable: false, style: zoom })),
      h('figcaption', {}, h('span', {}, label(id)),
        btn(busy['expr:' + id] ? '…' : 'Redo', '', safe(() => redoLivingFrame(id)),
          { cls: 'small ghost', disabled: running || !!busy['expr:' + id], title: `Draw ${label(id).toLowerCase()} again with a new seed and rebuild the living portrait` })))));
}

async function redoLivingFrame(id) {
  await generateDraft({ portrait: false, sprite: false, expressions: [id], living: true }, { reroll: true });
}

async function makeLiving() {
  await generateDraft({ portrait: false, sprite: false, expressions: [], living: true });
}

async function removeLiving() {
  if (!confirm('Delete the living portrait? The expressions it was made from stay.')) return;
  const npc = await api('DELETE', `/api/npcs/${S.draft.id}/assets/living`);
  S.draft.assets = npc.assets; renderGallery();
}

// ---------- animations -----------------------------------------------------------------------------------------------------
function stopAnimTimers() { S.animTimers.forEach(clearInterval); S.animTimers = []; }

/** What the animation steps of the running job are doing, e.g. "Walk: right (frame 2/4)". */
function animStatus(npcId) {
  for (const j of S.jobs) {
    if (j.npc_id !== npcId || !isActive(j)) continue;
    const s = j.steps.find(x => x.status === 'running' && ['view', 'anim', 'assemble'].includes(x.kind));
    if (s) return `${s.label}${s.detail ? ` (${s.detail})` : ''}`;
    if (j.steps.some(x => x.status === 'pending' && ['view', 'anim', 'assemble'].includes(x.kind))) return 'Animation queued';
  }
  return '';
}

function animOptions() {
  const n = S.recipe.anim, c = S.cfg.animations;
  const pick = (list, id, order) => {
    const set = new Set(list); set.has(id) ? set.delete(id) : set.add(id);
    const next = order.filter(x => set.has(x));
    return next.length ? next : [id];
  };
  return h('div', { class: 'stack sub' },
    h('div', { class: 'chips' }, c.clips.map(x => h('button', { type: 'button', class: 'chip' + (n.clips.includes(x.id) ? ' on' : ''),
      title: x.quality_note || (x.experimental ? 'Experimental: review each generated clip by eye' : x.kind === 'procedural' ? 'Made from the reference view, nothing extra to generate' : ''),
      onclick: () => { n.clips = pick(n.clips, x.id, c.clips.map(y => y.id)); saveRecipe(); renderSidebar(); } }, x.label))),
    h('div', { class: 'chips' }, c.directions.map(x => h('button', { type: 'button', class: 'chip' + (n.dirs.includes(x.id) ? ' on' : ''),
      onclick: () => { n.dirs = pick(n.dirs, x.id, c.directions.map(y => y.id)); saveRecipe(); renderSidebar(); } }, x.label))),
    field('Frames per direction', sel([['4', '4 frames'], ['8', '8 frames']], String(n.frames), v => { n.frames = +v; saveRecipe(); })),
    field('Candidates per frame', sel([['1', '1 (fastest)'], ['2', 'Best of 2'], ['3', 'Best of 3']], String(n.candidates), v => { n.candidates = +v; saveRecipe(); }),
      'More candidates take longer but give steadier frames.'),
    check('Mirror right to make left', n.mirror_left, v => { n.mirror_left = v; saveRecipe(); }),
    h('p', { class: 'hint' }, 'A 4-direction, 4-frame walk is 12 frames plus 2 reference views, roughly 20 to 40 seconds each: a walk frame that comes out zoomed in or in the wrong pose is drawn again.'));
}

function animPreview(npc, clipId, meta, dir) {
  const urls = meta.files[dir].map(f => assetUrl(f, meta.created));
  urls.forEach(u => { new Image().src = u; });                       // preload so the loop never flickers
  const img = h('img', { src: urls[0], alt: `${clipId}, facing ${dir}`, draggable: false });
  let i = 0;
  S.animTimers.push(setInterval(() => { i = (i + 1) % urls.length; img.src = urls[i]; }, 1000 / meta.fps));
  const label = S.cfg.animations.directions.find(x => x.id === dir)?.label || dir;
  return h('div', { class: 'tile anim' },
    h('div', { class: 'title' }, h('span', {}, label)),
    h('div', { class: 'frame checker' }, img),
    h('div', { class: 'cap' }, `${meta.frames} frames · ${meta.fps} fps${meta.mirrored?.[dir] ? ' · mirrored' : ''}`));
}

function exportControls(npc, clipId, meta) {
  const presets = S.cfg.animations.presets;
  const usable = p => !(p.frames && p.frames !== meta.frames);
  const sel_ = h('select', { 'aria-label': 'Export preset', title: 'Export preset', style: { width: 'auto' } },
    presets.map(p => h('option', { value: p.id, disabled: !usable(p), title: p.label }, p.label.split(':')[0])));
  const size = h('input', { type: 'number', min: 16, max: 1024, placeholder: 'px', 'aria-label': 'Frame size in pixels', title: 'Frame size in pixels (blank = preset default)', style: { width: '76px' } });
  const link = h('a', { class: 'btn small', download: '' }, icon('download'), 'Export');
  const update = () => {
    const q = new URLSearchParams(); if (size.value) q.set('frame', size.value);
    link.href = `/api/npcs/${npc.id}/export/${clipId}/${sel_.value}.zip${q.toString() ? '?' + q : ''}`;
  };
  sel_.addEventListener('change', update); size.addEventListener('input', update);
  update();
  return h('div', { class: 'row' }, sel_, size, link);
}

function clipBlock(npc, id, meta) {
  const rep = meta.report || {}, flags = rep.flags || [];
  const definition = S.cfg.animations.clips.find(clip => clip.id === id);
  const experimental = !!(meta.experimental || definition?.experimental);
  const qualityNote = meta.quality_note || definition?.quality_note || '';
  const badges = [];
  if (meta.stale) badges.push(h('span', { class: 'badge warn', title: 'The sprite was regenerated after this was made.' }, 'outdated'));
  if (meta.kind === 'generated') badges.push(h('span', { class: flags.length || experimental ? 'badge warn' : 'badge ok',
    title: experimental ? 'Automatic checks do not establish that the motion reads correctly; inspect the clip.' : '' },
    flags.length ? `${flags.length} to check` : experimental ? 'experimental' : 'checks passed'));
  const stats = Object.entries(rep.directions || {}).map(([d, r]) => `${d}: legs ${r.leg_variation.toFixed(2)}, head ${r.head_stability.toFixed(2)}, palette ${Math.round(r.palette_coverage * 100)}%`).join('  ·  ');
  return h('div', { class: 'clip' },
    h('div', { class: 'clip-head' }, h('strong', {}, meta.label), ...badges, h('span', { class: 'grow' }), exportControls(npc, id, meta),
      btn('', 'refresh', safe(() => regenClip(id)), { cls: 'small icon ghost', title: 'Regenerate this clip' }),
      btn('', 'trash', safe(() => removeClip(id)), { cls: 'small icon ghost danger', title: 'Delete this clip' })),
    h('div', { class: 'anim-grid' }, meta.order.map(d => animPreview(npc, id, meta, d))),
    qualityNote ? h('p', { class: 'hint' }, qualityNote) : null,
    flags.length ? h('ul', { class: 'flags' }, flags.map(f => h('li', {}, f))) : null,
    stats ? h('p', { class: 'hint' }, `Measured per direction. Legs: how much the legs move (about 0.3 is a real walk, under 0.12 is static). Head: how stable the head shape is. Palette: colour fidelity. ${stats}`) : null);
}

function animationsCard(npc, a) {
  const clips = a.animations || {}, running = animStatus(npc.id);
  const made = Object.values(clips).some(m => m.files);
  return h('section', { class: 'card' + (made ? ' wide' : '') }, h('h3', {}, 'Animations'),
    running ? h('div', { class: 'notice' }, h('div', { class: 'spinner' }), running) : null,
    ...Object.entries(clips).filter(([, m]) => m.files).map(([id, m]) => clipBlock(npc, id, m)),
    Object.keys(clips).length ? null : h('p', { class: 'hint' }, npc.id ? 'No animation yet. Tick Animation in the sidebar and press Generate, or start one here.' : 'Save or generate the character first.'),
    npc.id ? h('div', { class: 'chips', style: { marginTop: '10px' } },
      h('span', { class: 'cap hint', style: { alignSelf: 'center', margin: 0 } }, 'Generate:'),
      S.cfg.animations.clips.map(c => h('button', { type: 'button', class: 'chip' + (clips[c.id] ? ' on' : ''), disabled: !!running,
        title: (c.quality_note ? `${c.quality_note} ` : c.experimental ? 'Experimental. ' : '') + (clips[c.id] ? 'Regenerate with a new seed' : 'Uses the animation settings in the sidebar'),
        onclick: safe(() => regenClip(c.id)) }, (clips[c.id] ? '' : '+ ') + c.label))) : null,
    h('p', { class: 'hint' }, 'Animation starts from the full-body sprite, which is generated first if there is none.'));
}

async function regenClip(id) {
  const existing = S.draft.assets?.animations?.[id], n = S.recipe.anim;
  const kind = S.cfg.animations.clips.find(c => c.id === id)?.kind;
  const anim = existing && existing.files
    ? { clips: [id], dirs: existing.order, frames: existing.frames, candidates: n.candidates, mirror_left: !!existing.mirrored?.left }
    : { clips: [id], dirs: n.dirs, frames: n.frames, candidates: n.candidates, mirror_left: n.mirror_left };
  await generateDraft({ portrait: false, sprite: false, expressions: [], animations: anim }, { reroll: !!existing && kind === 'generated' });
}

async function removeClip(id) {
  if (!confirm(`Delete the ${id} animation?`)) return;
  const npc = await api('DELETE', `/api/npcs/${S.draft.id}/assets/animations/${id}`);
  S.draft.assets = npc.assets; renderGallery();
}

// ---------- model sheet ------------------------------------------------------------------------------------------------------
function sheetStatus(npcId) {
  for (const j of S.jobs) {
    if (j.npc_id !== npcId || !isActive(j)) continue;
    const s = j.steps.find(x => x.status === 'running' && ['sheet', 'sheet_stitch'].includes(x.kind));
    if (s) return s.label;
    if (j.steps.some(x => x.status === 'pending' && ['sheet', 'sheet_stitch'].includes(x.kind))) return 'Model sheet queued';
  }
  return '';
}

function sheetOptions() {
  const n = S.recipe.sheet, c = S.cfg.sheet;
  const toggle = id => {
    const set = new Set(n.views); set.has(id) ? set.delete(id) : set.add(id);
    const next = c.views.map(v => v.id).filter(x => set.has(x));
    n.views = next.length ? next : [id]; saveRecipe(); renderSidebar();
  };
  return h('div', { class: 'stack sub' },
    h('div', { class: 'chips' }, c.views.map(v => h('button', { type: 'button', class: 'chip' + (n.views.includes(v.id) ? ' on' : ''), onclick: () => toggle(v.id) }, v.label))),
    field('Pose', sel(c.poses.map(p => [p.id, p.label]), n.pose, v => { n.pose = v; saveRecipe(); })),
    h('p', { class: 'hint' }, 'Drawn from the full-body sprite, which is made first if there is none. All five views take about two to three minutes at Standard quality.'));
}

function photoPicker() {
  const attached = S.draft?.assets?.source_photo, pending = S.pendingPhoto;
  return h('div', { class: 'stack', style: { marginTop: '10px' } },
    h('input', { type: 'file', id: 'photo-file', accept: 'image/*', hidden: true, onchange: safe(onPhotoChosen) }),
    (pending || attached) && h('div', { style: { width: '160px', aspectRatio: '1', overflow: 'hidden', borderRadius: '8px' } },
      h('img', { src: pending ? pending.dataUrl : assetUrl(attached.file, attached.created), alt: 'Your photo',
        style: { width: '100%', height: '100%', objectFit: 'cover' } })),
    h('div', { class: 'row' },
      btn(attached || pending ? 'Change photo' : 'Choose photo', 'plus', () => $('#photo-file').click()),
      (attached || pending) && btn('Remove', 'trash', safe(removeSourcePhoto), { cls: 'small ghost danger' })),
    pending && !attached ? h('p', { class: 'hint' }, 'Attached when you save or generate.') : null,
    h('p', { class: 'hint' },
      "Your photo is edited into the chosen setting and style, keeping your face. Name, job and personality are still rolled at random. Stays on this computer."));
}

async function onPhotoChosen(e) {
  const file = e.target.files[0]; e.target.value = '';
  if (!file) return;
  S.pendingPhoto = { dataUrl: await photoToSquareDataUrl(file) };
  renderSidebar();
}

async function attachPendingPhoto() {
  const npc = await api('POST', `/api/npcs/${S.draft.id}/photo`, { image: S.pendingPhoto.dataUrl });
  S.draft.assets = npc.assets;
  S.pendingPhoto = null;
}

async function removeSourcePhoto() {
  if (!S.draft?.assets?.source_photo) { S.pendingPhoto = null; renderSidebar(); return; }
  // Not reusing removeAsset(): it shows its own generic "Delete this image?" confirm, which would stack a second,
  // less informative dialog on top of the one below.
  if (!confirm('Remove your uploaded photo? The current portrait (if any) is kept, but regenerating it later will start from scratch instead of your photo.')) return;
  const npc = await api('DELETE', `/api/npcs/${S.draft.id}/assets/source_photo`);
  S.draft.assets = npc.assets;
  renderSidebar();
}

function sheetCard(npc, a, busy) {
  const meta = a.sheet, running = sheetStatus(npc.id), views = meta?.views || {};
  const flags = meta?.report?.flags || [];
  const shown = S.cfg.sheet.views.filter(v => views[v.id] || busy['sheet:' + v.id]);
  const badges = [];
  if (meta?.stale) badges.push(h('span', { class: 'badge warn', title: 'The sprite or portrait was regenerated after this was made.' }, 'outdated'));
  if (meta?.report) badges.push(h('span', { class: flags.length ? 'badge warn' : 'badge ok' }, flags.length ? `${flags.length} to check` : 'checks passed'));
  return h('section', { class: 'card' + (meta || shown.length ? ' wide' : '') }, h('h3', {}, 'Model sheet'),
    running ? h('div', { class: 'notice' }, h('div', { class: 'spinner' }), running) : null,
    meta ? h('div', { class: 'clip-head' }, ...badges, h('span', { class: 'grow' }),
      h('a', { class: 'btn small', href: `/api/npcs/${npc.id}/sheet.zip`, download: '', title: 'Every view as its own PNG, the stitched sheet and a JSON record' }, icon('download'), 'Export'),
      btn('', 'refresh', safe(regenSheet), { cls: 'small icon ghost', title: 'Redraw every view with new seeds' }),
      btn('', 'trash', safe(removeSheet), { cls: 'small icon ghost danger', title: 'Delete the model sheet' })) : null,
    meta?.file ? h('div', { class: 'sheet-strip' }, h('img', { src: assetUrl(meta.file, meta.created), alt: `Model sheet of ${fullName(npc)}`, draggable: false })) : null,
    // Once the stitched sheet exists the single views repeat it, so they fold away (open while one is being redrawn).
    shown.length ? (() => {
      const grid = h('div', { class: 'sheet-grid' }, shown.map(v => assetTile({ cls: 'view', title: v.label, kind: 'sheet', key: v.id, meta: views[v.id], busy: busy['sheet:' + v.id] })));
      if (!meta?.file) return grid;
      const open = S.sheetViewsOpen || shown.some(v => busy['sheet:' + v.id]);
      return h('details', { class: 'sheet-views', open, ontoggle: e => { S.sheetViewsOpen = e.target.open; } },
        h('summary', {}, `Single views · ${shown.length} (redo one at a time)`), grid);
    })() : null,
    flags.length ? h('ul', { class: 'flags' }, flags.map(f => h('li', {}, f))) : null,
    meta ? null : h('p', { class: 'hint' }, npc.id ? 'No model sheet yet.' : 'Save or generate the character first.'),
    npc.id && !meta ? h('div', { class: 'chips', style: { marginTop: '10px' } }, btn('Generate model sheet', 'zap', safe(regenSheet), { cls: 'small', disabled: !!running })) : null,
    h('p', { class: 'hint' }, 'Each view is a separate edit of the full-body sprite on plain white, so one that came out wrong (bare feet, the wrong hair from behind) can be redone on its own with the refresh button on its tile.'));
}

async function regenSheet() {
  const m = S.recipe.sheet, existing = S.draft.assets?.sheet;
  await generateDraft({ portrait: false, sprite: false, expressions: [], sheet: { views: m.views.length ? m.views : S.cfg.sheet.views.map(v => v.id), pose: existing?.pose || m.pose } }, { reroll: !!existing });
}

async function removeSheet() {
  if (!confirm('Delete the model sheet?')) return;
  const npc = await api('DELETE', `/api/npcs/${S.draft.id}/assets/sheet`);
  S.draft.assets = npc.assets; renderGallery();
}

// ---------- cast import and batch on selection --------------------------------------------------------------------------------
async function importCast(e) {
  const file = e.target.files[0]; e.target.value = '';
  if (!file) return;
  const res = await api('POST', '/api/cast', { text: await file.text(), format: /\.csv$/i.test(file.name) ? 'csv' : 'json', setting: S.recipe.setting, style: S.recipe.style });
  res.warnings.forEach(w => toast(w, 'error'));
  toast(`Imported ${res.count} character${res.count === 1 ? '' : 's'}. They are selected: press "Generate for selected" to make their art.`);
  S.selected = new Set(res.npc_ids);
  await refreshLibrary();
}

async function generateSelected() {
  const ids = [...S.selected], assets = currentAssets();
  if (!ids.length) return;
  if (!hasAssets(assets)) return toast('Tick at least one thing to make in the Create tab first.', 'error');
  afterSubmit(await api('POST', '/api/generate', { npc_ids: ids, assets, options: { ...S.recipe.options, reroll: false } }));
  toast(`Queued ${ids.length} character${ids.length === 1 ? '' : 's'} with the recipe from the Create tab.`);
}

// ---------- create view: actions -------------------------------------------------------------------------------------------
async function rollNpc() {
  if (S.draft?.id && (S.dirty || S.dialogueDirty) && !confirm('This NPC has unsaved changes. Roll a new one anyway?')) return;
  const locked = presetLocks();                             // Advanced options first; a padlock on the sheet wins over them
  if (S.draft) for (const p of S.locks) { const v = getPath(S.draft, p); locked[p] = v ?? ''; }
  const freeform = S.recipe.mode === 'describe' ? S.recipe.describe.trim() : '';
  if (S.recipe.mode === 'describe' && !freeform) return toast('Describe the character first.', 'error');
  const npc = await api('POST', '/api/roll', { setting: S.recipe.setting, style: S.recipe.style, constraints: S.recipe.constraints, locked, ...(freeform ? { freeform } : {}) });
  S.draft = npc; S.dirty = false; S.dialogueDirty = false;
  if (S.draft.setting !== S.recipe.setting) S.locks.clear();
  S.view = 'create';
  if (!$('#sheet')) render(); else { renderSheet(); }
}

async function saveDraft() {
  if (S.dialogueDirty) await saveDialogue(true);
  const n = S.draft;
  S.draft = n.id ? await api('PUT', `/api/npcs/${n.id}`, npcPayload(n)) : await api('POST', '/api/npcs', npcPayload(n));
  S.dirty = false;
  if (S.pendingPhoto) await attachPendingPhoto();
  updateHead(); refreshLibrary();
  toast('Saved to the library');
}

async function saveDialogue(quiet = false) {
  if (!S.draft?.id || !S.draft.dialogue || !S.dialogueDirty) return;
  const result = await api('PUT', `/api/npcs/${S.draft.id}/dialogue`, { dialogue: S.draft.dialogue });
  S.draft.dialogue = result.dialogue; S.dialogueDirty = false;
  updateHead(); refreshLibrary();
  if (!quiet) toast('Dialogue pack saved');
}

async function deleteDraft() {
  const days = S.cfg.library?.trash_days ?? 30;
  if (!confirm(`Delete ${fullName(S.draft)} with all their images? They move to the Library's Trash, where you can bring them back${days > 0 ? ` for ${days} days` : ''}.`)) return;
  const name = fullName(S.draft);
  await api('DELETE', `/api/npcs/${S.draft.id}`);
  S.draft = null; S.dirty = false; S.dialogueDirty = false; S.locks.clear();
  await refreshLibrary(); renderSheet();
  toast(`${name} moved to the Trash.`);
}

function currentAssets() {
  const a = S.recipe.assets, n = S.recipe.anim, m = S.recipe.sheet, v = S.recipe.voice;
  return { portrait: a.portrait, sprite: a.sprite, model_3d: a.model_3d, expressions: [...a.expressions],
    ...(n.on && !S.outdated ? { animations: { clips: n.clips, dirs: n.dirs, frames: n.frames, candidates: n.candidates, mirror_left: n.mirror_left } } : {}),
    ...(m.on && !S.outdated ? { sheet: { views: m.views, pose: m.pose } } : {}),
    ...(v.on && !S.outdated ? { voice: { engine: v.engine, lines: v.lines } } : {}) };
}
const hasAssets = a => !!(a.portrait || a.sprite || a.model_3d || a.import_3d || a.body_3d || a.rig_3d || a.refine_3d || a.sharp_face || a.smooth_eyes || a.match_hair || a.clean_hands || a.attach_head || a.head_3d || a.check_3d || a.best_3d || (a.expressions || []).length || a.animations || a.sheet || a.living || a.voice);

/** The Voice Lab options under "What to make". */
function voiceOptions() {
  const v = S.recipe.voice;
  const makers = [['qwen', 'Qwen3-TTS (describe)'], ['breeze', 'Breeze TTS 2 (describe)'], ['kokoro', 'Kokoro (stock voices)']]
    .map(([id, label]) => [id, label + optionTag('voice:' + id)]);          // (the licence list says which ones are non-commercial)
  return h('div', { class: 'stack sub' },
    field('Model', sel(makers, v.engine, x => { v.engine = x; saveRecipe(); renderSidebar(); })),
    licenceNote('voice:' + v.engine),
    check('Voice the dialogue', v.lines, x => { v.lines = x; saveRecipe(); }, 'Makes the dialogue pack first if needed, then speaks every line in the voice.'));
}

function supportedAssetsForMorphology(npc, assets) {
  const info = S.cfg.morphologies?.[npc?.morphology];
  if (!info || info.full_body_supported || !(assets.sprite || assets.model_3d || assets.best_3d || assets.animations || assets.sheet)) return assets;
  const kept = { ...assets, sprite: false, model_3d: false, best_3d: false };
  delete kept.animations; delete kept.sheet;
  if (!hasAssets(kept)) {
    toast(`${info.label} body plan: ${info.description} Portraits and expressions are still available.`, 'error');
    return null;
  }
  toast(`Skipped sprite, 3D model, animation, and model-sheet requests. ${info.description}`);
  return kept;
}

async function generateDraft(assets, extra = {}) {
  assets = assets || currentAssets();
  if (!hasAssets(assets)) return toast('Tick at least one thing to make.', 'error');
  if (!S.draft) await rollNpc();
  if (!S.draft) return;
  assets = supportedAssetsForMorphology(S.draft, assets);
  if (!assets || !hasAssets(assets)) return;
  if (S.pendingPhoto && !S.draft.id) await saveDraft();      // the photo endpoint needs a real npc id
  const hadAssets = !!S.draft.id && Object.keys(S.draft.assets || {}).length > 0;
  const options = { ...S.recipe.options, reroll: hadAssets, ...extra };
  let res;
  if (S.draft.id) {
    if (S.dirty || S.pendingPhoto) await saveDraft();
    res = await api('POST', '/api/generate', { npc_id: S.draft.id, style: S.draft.style, assets, options });
  } else {
    res = await api('POST', '/api/generate', { npc: npcPayload(S.draft), style: S.draft.style, assets, options });
    S.draft = await api('GET', `/api/npcs/${res.npc_ids[0]}`); S.dirty = false;
  }
  afterSubmit(res);
  updateHead(); renderGallery();
  toast(`Queued ${res.jobs[0].total} step${res.jobs[0].total === 1 ? '' : 's'} for ${fullName(S.draft)}`);
}

const regen = (kind, key) => generateDraft({ portrait: kind === 'portrait', sprite: kind === 'sprite', expressions: kind === 'expression' ? [key] : [],
  ...(kind === 'sheet' ? { sheet: { views: [key], pose: S.draft.assets?.sheet?.pose || S.recipe.sheet.pose } } : {}) }, { reroll: true });

async function removeAsset(kind, key) {
  if (!confirm(kind === 'model_3d' ? 'Delete this 3D model?' : 'Delete this image?')) return;
  const path = kind === 'expression' ? `expressions/${key}` : kind;
  const npc = await api('DELETE', `/api/npcs/${S.draft.id}/assets/${path}`);
  S.draft.assets = npc.assets; renderGallery(); refreshLibrary();
}

async function generateBatch() {
  const r = S.recipe, assets = currentAssets();
  if (!hasAssets(assets)) return toast('Tick at least one thing to make.', 'error');
  const st = S.cfg.settings[r.setting];
  const selectedOrigin = st.origins.find(origin => origin.id === r.constraints.origin);
  const bodyPlan = r.constraints.morphology || selectedOrigin?.morphology;
  const generationAssets = bodyPlan && S.cfg.morphologies[bodyPlan]?.full_body_supported === false
    ? supportedAssetsForMorphology({ morphology: bodyPlan }, assets) : assets;
  if (!generationAssets || !hasAssets(generationAssets)) return;
  const res = await api('POST', '/api/generate', { count: r.count, setting: r.setting, style: r.style, constraints: r.constraints, locked: presetLocks(),
    assets: generationAssets, options: { ...r.options, reroll: false } });
  afterSubmit(res);
  (res.warnings || []).forEach(warning => toast(warning));
  toast(`Queued ${res.npc_ids.length} NPC${res.npc_ids.length === 1 ? '' : 's'}. They will appear in the Library as they finish.`);
  refreshLibrary();
}

// ---------- prompts panel ------------------------------------------------------------------------------------------------------
async function refreshPrompts() {
  const body = $('#prompts-body');
  if (!S.promptsOpen || !S.draft || !body) return;
  try {
    const p = await api('POST', '/api/preview', { npc: npcPayload(S.draft), npc_id: S.draft.id, options: S.recipe.options });
    const block = (title, x) => [h('h4', {}, title), h('div', { class: 'prompt-box' }, x.positive), h('div', { class: 'hint' }, 'Negative: ' + x.negative)];
    body.replaceChildren(
      ...block('Portrait', p.portrait),
      ...block('Full-body sprite: edited from the portrait', p.sprite_match),
      ...block('Full-body sprite: independent', p.sprite_independent),
      ...block(`Expression example (${p.expression_example.label})`, p.expression_example),
      h('p', { class: 'hint' }, `Cut-out backdrop colour for this NPC: ${p.key_colour} (chosen to avoid their clothing colours).`));
  } catch (e) { body.textContent = e.message; }
}
const refreshPromptsSoon = debounce(refreshPrompts, 500);

// ---------- library + queue controllers -----------------------------------------------------------------------------
const { openAdvanced, presetLocks, presetCount, clearPresets } = createAdvanced({ renderSidebar });
const { refreshLibrary, renderLibrary, openNpc } = createLibraryController({ renderTabs, updateHead, importCast, generateSelected, render,
  voiceBatch: ids => openVoiceBatch(ids), voiceReview: () => openVoiceReview(),
  createSceneFromPack: pack => sceneController.createFromPack(pack) });
const { afterSubmit, renderQueue, startPolling } = createQueueController({ openNpc, renderGallery, refreshLibrary,
  openScene: id => sceneController.load(id), onSceneJobChange: job => sceneController.onJobChange(job),
  openItem: id => itemController.load(id), onItemJobChange: job => itemController.onJobChange(job),
  onNpcJobChange: job => voiceWorkspace.onJobChange(job) });
const { voiceCard, onDraftChanged: onVoiceChanged, loadEngines: loadVoiceEngines, lineControls, dialogueVoiceActions,
  openBatch: openVoiceBatch, openReview: openVoiceReview } =
  createVoiceDesigner({ afterSubmit, renderGallery, saveDraft, openWorkspace: id => voiceWorkspace.openTab(id) });
const voiceWorkspace = createVoiceDesigner({ afterSubmit, renderGallery, saveDraft, standalone: true, render, refreshLibrary, openNpc });
const sceneController = createSceneController({ render, afterSubmit, openNpc });
const itemController = createItemController({ render, afterSubmit });
const creatureController = createCreatureController({ render });

// ---------- boot ----------------------------------------------------------------------------------------------------------------------------
function render() {
  stopAnimTimers();
  renderTabs();
  renderSettingsButton();
  if (S.view === 'create') buildCreate();
  else if (S.view === 'library') renderLibrary();
  else if (S.view === 'items') itemController.renderItems();
  else if (S.view === 'creatures') creatureController.renderCreatures();
  else if (S.view === 'voices') voiceWorkspace.renderWorkspace();
  else sceneController.renderScenes();
}

function applyConfig(cfg) {
  S.cfg = cfg;
  S.outdated = (cfg.api_version || 1) < REQUIRED_API;
  if (S.outdated) { cfg.animations = { clips: [], directions: [], presets: [] }; cfg.sheet = { views: [], poses: [], default_pose: '' }; }      // stub, so nothing reads a missing field
}

/** Re-read the server's config and redraw. A local extension calls this after it switches something on or off (a hidden
 *  setting, a tab): what the server lists has changed, and anything open that belonged to it is closed. */
async function reloadConfig() {
  applyConfig(await api('GET', '/api/config'));
  if (S.draft?.id) {
    const draft = S.draft;
    const saved = await api('GET', '/api/npcs/' + draft.id).catch(() => null);
    if (saved) draft.body_extension_notice = saved.body_extension_notice;
  }
  if (!S.cfg.settings[S.recipe.setting]) {
    S.recipe.setting = S.cfg.defaults.setting; S.recipe.constraints.occupation = ''; S.recipe.constraints.origin = ''; S.recipe.constraints.hair_color = '';
    S.recipe.constraints.hair_style = ''; saveRecipe();
  }
  if (S.filter.setting && !S.cfg.settings[S.filter.setting]) S.filter.setting = '';
  if (S.draft && !S.cfg.settings[S.draft.setting]) { S.draft = null; S.dirty = false; S.dialogueDirty = false; S.locks.clear(); }
  await refreshLibrary();
  render();
  refreshHealth().then(renderGallery);
}

// ---------- about + extensions --------------------------------------------------------------------------------------
function openAbout() {
  const dlg = $('#about');
  $('#aboutMeta').textContent = `Version ${S.cfg.version || 'unknown'} · ComfyUI at ${S.cfg.comfy_url} · server API ${S.cfg.api_version || 1}`;
  $('#aboutLicences').hidden = !S.cfg.licences;                // (an older server has no licence list)
  loadUpdateState();
  if (!dlg.open) dlg.showModal();
}

// ---------- updates (About > Check for updates / Update now; the update itself is scripts/update.py's, as update.bat's) ----------
function showUpdateAnswer(answer) {
  const newer = !!(answer && answer.newer);
  $('#aboutUpdateMsg').replaceChildren(...(!answer ? [] : answer.error ? [`Could not check: ${answer.error}`] : [
    answer.message || '',
    newer && answer.release?.url ? h('span', {}, ' ', h('a', { href: answer.release.url, target: '_blank', rel: 'noopener' }, 'What is new')) : '',
    answer.checked_at ? ` (checked ${new Date(answer.checked_at).toLocaleString()})` : '']).filter(Boolean));
  $('#aboutUpdateNow').hidden = !newer || S.update?.update_now === false;
  if (!newer) $('#aboutUpdatePlan').hidden = true;
}

async function loadUpdateState() {
  S.update = await api('GET', '/api/update').catch(() => null);
  showUpdateAnswer(S.update?.last || null);
}

$('#aboutUpdateCheck').addEventListener('click', async () => {
  const button = $('#aboutUpdateCheck');
  button.disabled = true;
  $('#aboutUpdateMsg').textContent = 'Asking GitHub...';
  try { showUpdateAnswer(await api('POST', '/api/update/check')); }
  catch (e) { showUpdateAnswer({ error: e.message }); }
  button.disabled = false;
});

$('#aboutUpdateNow').addEventListener('click', async () => {
  const box = $('#aboutUpdatePlan');
  box.hidden = false;
  box.replaceChildren(h('p', { class: 'hint' }, 'Reading what the update will change...'));
  let plan;
  try { plan = await api('POST', '/api/update/plan'); } catch (e) { box.replaceChildren(h('p', { class: 'err' }, e.message)); return; }
  if (!plan.ok) { box.replaceChildren(h('p', { class: 'err' }, plan.problem)); return; }
  if (!plan.newer) { box.replaceChildren(h('p', { class: 'hint' }, plan.message)); return; }
  const busy = (S.jobs || []).some(isActive);
  const go = h('button', { type: 'button', class: 'btn small primary' }, busy ? 'Stop the jobs, update and restart' : 'Update and restart');
  go.addEventListener('click', async () => {
    go.disabled = true;
    try {
      await api('POST', '/api/update/start', { stop_jobs: busy });
      box.replaceChildren(h('p', {}, `NPC Forge is closing for the update to ${plan.to}. A window of its own shows the update, then NPC Forge starts `
        + 'again and opens in a new browser tab. This tab can be closed.'));
    } catch (e) { go.disabled = false; box.append(h('p', { class: 'err' }, e.message)); }
  });
  box.replaceChildren(...[
    h('p', {}, h('strong', {}, `Update from ${plan.from} to ${plan.to}`)),
    h('ol', { class: 'about-update-steps' }, (plan.steps || []).map(s => h('li', {}, s))),
    ...(plan.notes || []).map(n => h('p', { class: 'hint' }, n)),
    busy ? h('p', { class: 'err' }, 'Jobs are waiting or running: updating stops them. Their finished pictures are kept.') : null,
    h('div', { class: 'about-update' }, go, h('button', { type: 'button', class: 'btn small ghost', onclick: () => { box.hidden = true; } }, 'Not now')),
  ].filter(Boolean));
});

// What the last update (or undo) left to say: shown once, the first time the page opens after it.
async function showUpdateNotice() {
  const notice = S.update?.notice;
  if (!notice) return;
  const text = notice.state === 'updated' ? `NPC Forge was updated from ${notice.from} to ${notice.to}.`
    : notice.state === 'undone' ? `NPC Forge is back to version ${notice.from}.`
    : notice.state === 'current' ? notice.message || 'NPC Forge is up to date.'
    : `The update did not happen: ${notice.message || 'see the update window.'}`;
  toast(text + ((notice.problems || []).length ? ' ' + notice.problems.join(' ') : ''), notice.state === 'failed' || (notice.problems || []).length ? 'error' : 'info');
  await api('DELETE', '/api/update/notice').catch(() => {});
}

$('#aboutClose').addEventListener('click', () => $('#about').close());
$('#aboutLicences').addEventListener('click', safe(async () => { $('#about').close(); await openLicences(); }));
$('#settingsBtn').addEventListener('click', openSettings);
$('#about').addEventListener('click', e => { if (e.target === e.currentTarget) e.currentTarget.close(); });     // a click on the backdrop

// What a local extension's script may use (see "Local extensions" in the README). Defined before any of them load.
window.NPCForge = {
  version: 1, reload: reloadConfig, toast, aboutFooter: () => $('#aboutFoot'),
  addBodyOptions(option) { bodyOptions.push(option); renderGallery(); },
};

function loadExtensionUi() {
  for (const u of S.cfg.ui || []) {
    if (u.style) document.head.append(h('link', { rel: 'stylesheet', href: u.style }));
    if (u.script) document.body.append(h('script', { src: u.script }));
  }
}

async function init() {
  applyConfig(await api('GET', '/api/config'));
  S.recipe = loadRecipe();
  if (S.outdated) { S.recipe.mode = 'roll'; S.recipe.anim.on = false; S.recipe.sheet.on = false; }
  const jobs = await api('GET', '/api/jobs').catch(() => []);
  for (const j of jobs) { S.seen[j.id] = j.version; if (!isActive(j)) S.notified.add(j.id); }
  S.jobs = jobs;
  new ResizeObserver(([entry]) => {
    document.documentElement.style.setProperty('--topbar-h', `${entry.target.offsetHeight}px`);
  }).observe($('.topbar'), { box: 'border-box' });
  refreshHealth(); setInterval(refreshHealth, 20000);
  refreshGpuLight(); setInterval(refreshGpuLight, 5000);
  document.addEventListener('visibilitychange', refreshGpuLight);
  if (!S.outdated) loadVoiceEngines().then(() => renderGallery()).catch(() => { /* the Voice card works without the labels */ });
  await refreshLibrary();
  render(); renderQueue();
  if (jobs.some(isActive)) startPolling();
  loadExtensionUi();
  loadUpdateState().then(showUpdateNotice).catch(() => { /* the page works without it */ });
}

init().catch(e => { document.body.replaceChildren(h('pre', { style: { padding: '24px' } }, 'The NPC Forge page could not start: ' + e.message)); });
