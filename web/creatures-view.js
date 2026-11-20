import { $, S, api, h, isActive, safe, toast } from './core.js';
import { loadLocalRenderer } from './model-preview.js';

// The Creatures tab, which is the creature library: the animals and monsters the games need. Each creature belongs to one game
// (its project) and is made in three steps: a concept picture, a 3D model made from it, and the game model (scaled, decimated, one
// colour, and a rig where the body plan has one). The server keeps them (npcgen/creatures.py), makes them as jobs on the same queue
// as everything else (npcgen/creaturemaker.py), and packs a finished one for a game (npcgen/creaturepack.py). The body plans and
// what each one builds come from data/creature_plans.json. This page lists them, edits what a creature is, asks for the steps, and
// shows what was made: the concept picture, the 3D model in a viewer, the measurements, the licence rating and the pack.

const STAGES = [['concept', 'Concept picture'], ['3d', '3D model'], ['prep', 'Game model']];
const LICENCE_SHORT = {
  yes: 'OK for commercial work', conditions: 'Allowed, check the conditions', unclear: 'Not cleared',
  no: 'Not for commercial work', none: 'Nothing made yet',
};

// The Creatures tab's own styles, added once. (style.css is shared with the rest of the app, so the tab keeps its own.)
const STYLE = `
.creatures { padding: 20px; display: flex; flex-direction: column; gap: 16px; }
.cr-title { margin: 0; font-size: 20px; }
.cr-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 12px; }
.cr-card { display: flex; flex-direction: column; gap: 6px; text-align: left; padding: 10px; border-radius: 10px; border: 1px solid var(--border); background: var(--panel); cursor: pointer; color: inherit; font: inherit; }
.cr-card:hover { background: var(--panel-2); }
.cr-thumb { width: 100%; aspect-ratio: 1 / 1; object-fit: contain; border-radius: 8px; background: #5b6563; }
.cr-thumb-empty { width: 100%; aspect-ratio: 1 / 1; border-radius: 8px; border: 1px dashed var(--border); display: flex; align-items: center; justify-content: center; font-size: 12px; opacity: .6; }
.cr-card-name { font-weight: 700; }
.cr-card-meta { font-size: 12px; opacity: .8; }
.cr-chips { display: flex; flex-wrap: wrap; gap: 4px; }
.cr-chip { font-size: 11px; padding: 2px 7px; border-radius: 999px; border: 1px solid var(--border); opacity: .55; }
.cr-chip.on { opacity: 1; border-color: var(--accent); }
.cr-chip.cr-stale, .cr-step.cr-stale { color: #b45309; }
.cr-editor { display: flex; flex-direction: column; gap: 14px; }
.cr-form { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 12px; }
.cr-field { display: flex; flex-direction: column; gap: 4px; }
.cr-field input, .cr-field select, .cr-field textarea { font: inherit; }
.cr-seed { display: flex; gap: 8px; align-items: center; }
.cr-steps { display: flex; flex-direction: column; gap: 10px; }
.cr-boxes { display: flex; flex-wrap: wrap; gap: 14px; }
.cr-step { display: inline-flex; align-items: center; gap: 6px; }
.cr-again { font-size: 12px; opacity: .85; }
.cr-progress { margin: 0; padding-left: 20px; font-size: 13px; }
.cr-progress .cr-error { color: var(--danger); }
.cr-results { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 14px; align-items: start; }
.cr-figure { margin: 0; display: flex; flex-direction: column; gap: 6px; }
.cr-figure img { width: 100%; max-width: 360px; aspect-ratio: 1 / 1; object-fit: contain; border-radius: 10px; border: 1px solid var(--border); background: #5b6563; }
.cr-viewer { width: 100%; height: 360px; border-radius: 10px; border: 1px solid var(--border); background: #2a302f; overflow: hidden; display: flex; align-items: center; justify-content: center; }
.cr-viewer model-viewer { width: 100%; height: 100%; }
.cr-stats { font-size: 13px; line-height: 1.6; }
.cr-warning { color: var(--danger); font-size: 13px; margin: 4px 0; }
.cr-licence { font-size: 13px; line-height: 1.5; }
.cr-downloads { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.cr-candidates { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 10px; }
.cr-candidate { margin: 0; display: flex; flex-direction: column; gap: 6px; }
.cr-candidate img { width: 100%; aspect-ratio: 1 / 1; object-fit: contain; border-radius: 8px; border: 1px solid var(--border); background: #5b6563; }
.cr-subtitle { margin: 0; font-size: 16px; }
.cr-projects, .cr-accept-block { display: flex; flex-direction: column; gap: 8px; }
.cr-project-list { display: flex; flex-direction: column; gap: 6px; }
.cr-project { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; padding: 8px 10px; border-radius: 8px; border: 1px solid var(--border); background: var(--panel); }
.cr-project > span:first-child { flex: 1 1 260px; }
.cr-accept { display: flex; flex-direction: column; gap: 10px; }
`;

const fileUrl = (id, file, stamp) => `/api/creatures/${id}/files/${file}?v=${encodeURIComponent(stamp || '')}`;
// Four different seeds for a picture grid (the server checks them again: 1 to 4 different whole numbers).
const randomSeeds = (count = 4) => {
  const seeds = new Set();
  while (seeds.size < count) seeds.add(Math.floor(Math.random() * 2147483647));
  return [...seeds];
};
const field = (label, control, hint) => h('div', { class: 'cr-field' }, h('label', { class: 'lbl' }, label), control, hint && h('p', { class: 'hint' }, hint));
const pluralize = (count, word) => `${count} ${word}${count === 1 ? '' : 's'}`;

export function createCreatureController({ render }) {
  if (!document.getElementById('creatures-style')) document.head.append(h('style', { id: 'creatures-style' }, STYLE));

  let data = null;      // the library from the server: creatures, briefs, body plans, the plans' details, the stages
  let open = null;      // the open creature as the server has it (null: the library)
  let form = null;      // the editor's fields, with any edits (null: the library)
  let projectForm = null; // a game's fields as the Projects panel edits them (null: not editing a game)
  let game = null;      // the library's game filter: null = every game, '' = not yet given a game
  let timer = null;     // watching a running job so the page follows it
  let jobs = [];        // the queue's jobs, fetched with each refresh (the app's own list may not have a new job yet)

  const config = () => data || { creatures: [], briefs: [], body_plans: [], rig_built: [], plans: {}, projects: [], engines: [] };
  const jobOf = id => jobs.filter(job => job.target_type === 'creature' && job.creature_id === id).slice(-1)[0];
  const busy = id => jobs.some(job => isActive(job) && job.target_type === 'creature' && job.creature_id === id);
  const planText = plan => {
    const info = config().plans?.[plan];
    return info ? `${info.label} (${info.rig_text})` : plan;
  };

  // ---- going between the library and a creature ----------------------------------------------------------------------------------------
  async function openTab() {
    S.view = 'creatures';
    render();
    await refresh();
  }

  async function refresh() {
    data = await api('GET', '/api/creatures');
    jobs = await api('GET', '/api/jobs').catch(() => jobs);
    if (open) {
      open = await api('GET', `/api/creatures/${open.id}`).catch(() => null);
      if (!open) form = null;
    }
    renderCreatures();
    watch();
  }

  function watch() {
    clearTimeout(timer);
    timer = null;
    const running = config().creatures.some(c => busy(c.id)) || (open && busy(open.id));
    if (running) timer = setTimeout(() => refresh().catch(() => { /* the next poll tries again */ }), 3000);
  }

  async function openById(id) {
    open = await api('GET', `/api/creatures/${id}`);
    form = toForm(open);
    renderCreatures();
    watch();
  }

  function newCreature() {
    open = null;
    form = { name: '', project: game || '', shore: '', body_plan: 'serpentine', length_m: 2.5, segments: 12, tris: budgetOf(game || ''), seed: '', prompt: '', notes: '', key: '', form: '', tier: '', route: '' };
    renderCreatures();
  }

  function toForm(creature) {
    return { name: creature.name, project: creature.project || '', shore: creature.shore || '', body_plan: creature.body_plan, length_m: creature.length_m,
      segments: creature.segments || 12, tris: creature.tris || 20000, seed: creature.seed ?? '', prompt: creature.prompt || '', notes: creature.notes || '',
      key: creature.key || '', form: creature.form || '', tier: creature.tier || '', route: creature.route || '' };
  }

  // ---- the library -----------------------------------------------------------------------------------------------------------------------
  function renderCreatures() {
    if (!data) {
      $('#view').replaceChildren(h('p', { class: 'hint' }, 'Loading the creature library…'));
      return;
    }
    $('#view').replaceChildren(h('div', { class: 'creatures' }, form ? editor() : projectForm ? projectEditor() : library()));
  }

  function library() {
    const all = config().creatures;
    const shown = game === null ? all : all.filter(c => (c.project || '') === game);
    const made = shown.filter(c => c.has?.prep).length;
    const games = [...new Set(all.map(c => c.project || ''))].sort((a, b) => (a === '') - (b === '') || a.localeCompare(b));
    const gameFilter = h('select', { 'aria-label': 'Game', onchange: event => { game = event.target.value === '*' ? null : event.target.value; renderCreatures(); } },
      [h('option', { value: '*' }, 'All games'), ...games.map(g => h('option', { value: g }, g || 'Not given a game yet'))]);
    gameFilter.value = game === null ? '*' : game;
    const bar = h('div', { class: 'lib-bar' },
      h('h2', { class: 'cr-title' }, 'Creature library'),
      h('span', { class: 'grow' }),
      gameFilter,
      h('button', { class: 'btn', type: 'button', onclick: safe(addBestiary) }, 'Add the bestiary creatures'),
      h('button', { class: 'btn primary', type: 'button', onclick: newCreature }, 'New creature'));
    const intro = h('p', { class: 'hint' }, all.length
      ? `${pluralize(shown.length, 'creature')} listed${game === null ? '' : ` for ${game || 'no game yet'}`}, ${made} with a game model. Open one to make its steps and see what was made.`
      : 'The creatures the games need. Each is made in three steps: a concept picture, a 3D model made from it, and the game model. Each creature is for one game, and its pack carries that game\'s name.');
    if (shown.length === 0) {
      return h('div', {}, bar, projectsPanel(), intro, h('p', { class: 'hint' }, all.length ? 'No creatures for this game yet.' : 'No creatures yet. Add the bestiary creatures to start the library.'));
    }
    const cards = shown.map(card => h('button', { class: 'cr-card', type: 'button', onclick: safe(() => openById(card.id)) },
      card.has?.concept
        ? h('img', { class: 'cr-thumb', src: fileUrl(card.id, 'concept.png', card.updated), alt: `${card.name}: concept picture`, loading: 'lazy' })
        : h('span', { class: 'cr-thumb-empty' }, 'No picture yet'),
      h('span', { class: 'cr-card-name' }, card.name),
      h('span', { class: 'cr-card-meta' }, `${card.shore || '—'} · ${planText(card.body_plan)}`),
      h('span', { class: 'cr-card-meta' }, `Licence: ${LICENCE_SHORT[card.licence] || card.licence}`),
      h('span', { class: 'cr-chips' }, STAGES.map(([id, label]) => {
        const state = card.freshness?.[id]?.state;
        const stale = state === 'stale';
        return h('span', { class: `cr-chip${card.has?.[id] ? ' on' : ''}${stale ? ' cr-stale' : ''}`, title: card.freshness?.[id]?.reason || '' },
          `${label}${card.has?.[id] ? ' ✓' : ''}${stale ? ' · out of date' : ''}`);
      }))));
    return h('div', {}, bar, projectsPanel(), intro, h('div', { class: 'cr-grid' }, cards));
  }

  async function addBestiary() {
    const result = await api('POST', '/api/creatures', game ? { seed_bestiary: true, project: game } : { seed_bestiary: true });
    toast(result.added ? `Added ${result.added} creature${result.added === 1 ? '' : 's'} from the bestiary` : 'All the bestiary creatures are already listed');
    await refresh();
  }

  // ---- the games: each one's triangle budget, picture style and licence rule (npcgen/creature_projects.py) ----------------------------
  function gameOf(slug) {
    return (slug && config().projects.find(p => p.slug === slug)) || null;
  }

  function budgetOf(slug) {
    return gameOf(slug)?.tri_budget ?? 20000;
  }

  // The same slug the server makes of a game's name ("Dust Moon" is dust-moon).
  function slugOf(name) {
    return (name || '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 40);
  }

  function budgetText(picked) {
    return picked
      ? `${picked.name}'s budget: ${picked.tri_budget.toLocaleString()} triangles. A creature takes it unless you change it here.`
      : 'The most triangles the game model may have: 20,000 for a creature. A game may ask for fewer.';
  }

  function promptText(picked) {
    return picked
      ? `Drawn with Qwen-Image 2512. ${picked.name}'s style, "${picked.style_prefix}", goes in front of this prompt when the picture is drawn, unless the prompt starts with it. Keep the side view.`
      : 'Drawn with Qwen-Image 2512. Keep the side view and the flat backdrop: the 3D model reads a side view best.';
  }

  function todayISO() {
    const now = new Date();
    return new Date(now.getTime() - now.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
  }

  function acceptedText(p) {
    if (!p.commercial) return 'Not sold: no acceptance needed';
    const accepted = p.licence_acceptance;
    return accepted ? `Conditions accepted by ${accepted.accepted_by} on ${accepted.date}` : 'Conditions not accepted yet';
  }

  function projectsPanel() {
    const games = config().projects;
    return h('section', { class: 'cr-projects' },
      h('div', { class: 'lib-bar' }, h('h3', { class: 'cr-subtitle' }, 'Games'), h('span', { class: 'grow' }),
        h('button', { class: 'btn', type: 'button', onclick: newProject }, 'New game')),
      games.length
        ? h('div', { class: 'cr-project-list' }, games.map(p => h('div', { class: 'cr-project' },
          h('span', {}, h('strong', {}, p.name), ` · ${p.slug}`),
          h('span', { class: 'cr-card-meta' }, `${p.tri_budget.toLocaleString()} triangles · ${p.commercial ? 'sold' : 'not sold'} · ${acceptedText(p)}`),
          h('button', { class: 'btn', type: 'button', onclick: () => openProject(p) }, 'Edit'))))
        : h('p', { class: 'hint' }, 'No games yet. A game sets its creatures\' triangle budget, picture style and licence rule.'));
  }

  function newProject() {
    open = null;
    form = null;
    projectForm = {
      slug: '', name: '', tri_budget: 20000, style_prefix: 'Stylised 3D game concept art, render3d style,', negative_extra: '',
      background: 'Flat solid green background.', commercial: true, engine: 'godot-4', pack_dir: '', key_prefix: '',
      clip_map: JSON.stringify({ serpentine: { idle: 'Idle', moving: 'Swim', attacking: 'Bite', hurt: 'Hit' } }, null, 2),
      licence_acceptance: null, isNew: true, accept_by: '', accept_reason: '',
    };
    renderCreatures();
  }

  function openProject(p) {
    open = null;
    form = null;
    projectForm = { ...p, clip_map: JSON.stringify(p.clip_map || {}, null, 2), isNew: false, accept_by: '', accept_reason: '' };
    renderCreatures();
  }

  function readProject() {
    const f = projectForm;
    let clipMap;
    try {
      clipMap = JSON.parse(f.clip_map || '{}');
    } catch {
      throw new Error('The clip names are not valid JSON: check the commas, colons and quotes.');
    }
    return {
      name: f.name, tri_budget: Number(f.tri_budget), style_prefix: f.style_prefix, negative_extra: f.negative_extra,
      background: f.background, commercial: !!f.commercial, engine: f.engine, pack_dir: f.pack_dir, key_prefix: f.key_prefix,
      clip_map: clipMap,                                             // (the licence acceptance is sent only when it changes)
    };
  }

  async function saveProject(changes = {}) {
    const slug = projectForm.slug.trim();
    if (projectForm.isNew && !/^[a-z0-9]+(-[a-z0-9]+)*$/.test(slug)) {
      throw new Error('A game\'s slug is lower-case letters, digits and dashes (for example ember-hollow).');
    }
    const body = { ...readProject(), ...changes };
    const saved = projectForm.isNew
      ? await api('POST', '/api/creature-projects', { slug, ...body })
      : await api('PUT', `/api/creature-projects/${encodeURIComponent(slug)}`, body);
    projectForm = { ...saved, clip_map: JSON.stringify(saved.clip_map || {}, null, 2), isNew: false, accept_by: '', accept_reason: '' };
    toast(`Saved ${saved.name}`);
    await refresh();
  }

  async function acceptConditions() {
    const by = (projectForm.accept_by || '').trim();
    const reason = (projectForm.accept_reason || '').trim();
    if (!by || !reason) throw new Error('Say who accepts the conditions, and why.');
    await saveProject({ licence_acceptance: { verdict: 'conditions', accepted_by: by, date: todayISO(), reason } });
  }

  function projectEditor() {
    const f = projectForm;
    const set = key => event => { f[key] = event.target.value; };
    const engines = config().engines.length ? config().engines : ['godot-4'];
    const fields = h('div', { class: 'cr-form' },
      field('Slug', h('input', { type: 'text', value: f.slug, oninput: set('slug'), disabled: !f.isNew, 'aria-label': 'Slug', placeholder: 'ember-hollow' }),
        'The name a creature gives as its Game. It can not be changed once the game exists.'),
      field('Name', h('input', { type: 'text', value: f.name, oninput: set('name'), 'aria-label': 'Game name' })),
      field('Triangle budget', h('input', { type: 'number', min: '500', max: '20000', step: '500', value: f.tri_budget, oninput: set('tri_budget'), 'aria-label': 'Game triangle budget' }),
        'The most triangles a creature of this game may have, unless the creature names its own.'),
      field('Sold commercially', h('input', { type: 'checkbox', checked: !!f.commercial, 'aria-label': 'Sold commercially',
        onchange: event => { f.commercial = event.target.checked; renderCreatures(); } }),
      'A sold game takes only licences that allow sale, and conditions once it has accepted them.'),
      field('Engine', h('select', { 'aria-label': 'Engine', onchange: set('engine') }, engines.map(e => h('option', { value: e, selected: e === f.engine }, e))),
        'The engine a pack is installed for.'),
      field('Style', h('textarea', { rows: '2', oninput: set('style_prefix'), 'aria-label': 'Style' }, f.style_prefix),
        'Put in front of each concept prompt, unless the prompt already starts with it.'),
      field('Extra negative words', h('input', { type: 'text', value: f.negative_extra, oninput: set('negative_extra'), 'aria-label': 'Extra negative words' }),
        'Added to the negative prompt of every concept in this game.'),
      field('Background', h('input', { type: 'text', value: f.background, oninput: set('background'), 'aria-label': 'Background' }),
        'Asked for in each prompt, unless the prompt already names a background.'),
      field('Pack folder', h('input', { type: 'text', value: f.pack_dir, oninput: set('pack_dir'), 'aria-label': 'Pack folder' }),
        'Where a pack for this game is installed. The install is not built yet.'),
      field('Key prefix', h('input', { type: 'text', value: f.key_prefix, oninput: set('key_prefix'), 'aria-label': 'Key prefix' }),
        'Starts the keys of this game\'s creatures (lower-case letters, digits and dashes).'),
      field('Clip names', h('textarea', { rows: '5', oninput: set('clip_map'), 'aria-label': 'Clip names' }, f.clip_map),
        'Per body plan: the state a unit is in, and the clip it plays, as JSON.'));
    const accepted = f.licence_acceptance;
    const licence = !f.commercial
      ? h('p', { class: 'hint' }, 'This game is not sold, so its creatures\' licence conditions need no acceptance.')
      : accepted
        ? h('div', { class: 'cr-accept' },
          h('p', {}, `The licence conditions were accepted by ${accepted.accepted_by} on ${accepted.date}.`),
          h('p', { class: 'hint' }, accepted.reason),
          h('button', { class: 'btn', type: 'button', onclick: safe(() => saveProject({ licence_acceptance: null })) }, 'Remove the acceptance'))
        : h('div', { class: 'cr-accept' },
          h('p', { class: 'hint' }, 'A creature rated "conditions" can not be packed for this game until its conditions are accepted. Read the conditions first. Accepting them is a decision for the game\'s director, and it is recorded with today\'s date.'),
          field('Accepted by', h('input', { type: 'text', value: f.accept_by || '', oninput: set('accept_by'), 'aria-label': 'Accepted by' })),
          field('Why the conditions are accepted', h('textarea', { rows: '2', oninput: set('accept_reason'), 'aria-label': 'Reason for accepting' }, f.accept_reason || '')),
          h('button', { class: 'btn primary', type: 'button', onclick: safe(acceptConditions) }, 'Accept the conditions'));
    const back = h('button', { class: 'btn', type: 'button', onclick: () => { projectForm = null; renderCreatures(); } }, 'Back to the library');
    const save = h('button', { class: 'btn primary', type: 'button', onclick: safe(() => saveProject()) }, f.isNew ? 'Create the game' : 'Save the game');
    return h('div', { class: 'cr-editor' },
      h('div', { class: 'lib-bar' }, h('h2', { class: 'cr-title' }, f.isNew ? 'New game' : f.name), h('span', { class: 'grow' }), back, save),
      fields,
      h('section', { class: 'cr-accept-block' }, h('h3', { class: 'cr-subtitle' }, 'Licence'), licence));
  }

  // ---- the editor ---------------------------------------------------------------------------------------------------------------------------
  function editor() {
    const isNew = !open;
    const set = key => event => { form[key] = event.target.value; };
    const plans = config().body_plans.map(plan => h('option', { value: plan }, planText(plan)));
    const planSelect = h('select', { 'aria-label': 'Body plan', onchange: set('body_plan') }, plans);
    planSelect.value = form.body_plan;
    const projects = [...new Set([...config().projects.map(p => p.slug), ...config().creatures.map(c => c.project).filter(Boolean)])];
    const picked = gameOf(form.project);                                // the game picked, when it is on the list
    const budgetInput = h('input', { type: 'number', min: '500', max: '20000', step: '500', value: form.tris, oninput: set('tris'), 'aria-label': 'Triangle budget' });
    const budgetHint = h('p', { class: 'hint' }, budgetText(picked));
    const promptArea = h('textarea', { rows: '6', oninput: set('prompt'), 'aria-label': 'Concept prompt' }, form.prompt);
    const promptHint = h('p', { class: 'hint' }, promptText(picked));
    const gameChosen = event => {                                       // the name is kept as the game's slug; a new creature takes the budget
      form.project = slugOf(event.target.value);
      event.target.value = form.project;
      const chosen = gameOf(form.project);
      if (isNew && chosen) {
        form.tris = chosen.tri_budget;
        budgetInput.value = String(chosen.tri_budget);
      }
      budgetHint.textContent = budgetText(chosen);
      promptHint.textContent = promptText(chosen);
    };
    const gameInput = h('input', { type: 'text', value: form.project, oninput: set('project'), onchange: gameChosen, 'aria-label': 'Game', placeholder: 'ember-hollow' });
    gameInput.setAttribute('list', 'cr-projects');                    // (list is read-only as a property, so it is set as an attribute)
    const back = h('button', { class: 'btn', type: 'button', onclick: () => { open = null; form = null; refresh().catch(() => { }); } }, 'Back to the library');
    const save = h('button', { class: 'btn primary', type: 'button', onclick: safe(saveForm) }, isNew ? 'Create creature' : 'Save changes');
    const actions = [back, save];
    if (!isNew) actions.push(h('button', { class: 'btn danger', type: 'button', onclick: safe(deleteOpen) }, 'Delete creature'));
    const fields = h('div', { class: 'cr-form' },
      field('Name', h('input', { type: 'text', value: form.name, oninput: set('name'), 'aria-label': 'Name' })),
      field('Game', gameInput, 'The game this creature is for. Its pack is named for that game.'),
      field('Shore', h('input', { type: 'text', value: form.shore, oninput: set('shore'), 'aria-label': 'Shore', placeholder: 'GUL, TIT…' })),
      field('Body plan', planSelect, config().plans?.[form.body_plan]?.note),
      field('Length (m)', h('input', { type: 'number', step: '0.1', min: '0.3', max: '6', value: form.length_m, oninput: set('length_m'), 'aria-label': 'Length in metres' })),
      field('Segments', h('input', { type: 'number', min: '4', max: '24', value: form.segments, oninput: set('segments'), 'aria-label': 'Body segments' }), 'The number of rig bones along the body (rigged plans).'),
      h('div', { class: 'cr-field' }, h('label', { class: 'lbl' }, 'Triangle budget'), budgetInput, budgetHint),
      field('Seed', h('span', { class: 'cr-seed' },
        h('input', { type: 'number', value: form.seed, oninput: set('seed'), 'aria-label': 'Seed' }),
        h('button', { class: 'btn', type: 'button', onclick: () => { form.seed = Math.floor(Math.random() * 2147483647); renderCreatures(); } }, 'New seed'))),
      h('div', { class: 'cr-field' }, h('label', { class: 'lbl' }, 'Concept prompt'), promptArea, promptHint),
      field('Notes', h('textarea', { rows: '2', oninput: set('notes'), 'aria-label': 'Notes' }, form.notes)));
    const games = h('datalist', { id: 'cr-projects' }, projects.map(p => h('option', { value: p })));
    return h('div', { class: 'cr-editor' },
      h('div', { class: 'lib-bar' }, h('h2', { class: 'cr-title' }, isNew ? 'New creature' : form.name), h('span', { class: 'grow' }), ...actions),
      fields, games,
      isNew ? h('p', { class: 'hint' }, 'Create the creature, then make its steps.') : stepsPanel());
  }

  function stepsPanel() {
    const job = jobOf(open.id);
    const running = busy(open.id);
    const makes = config().plans?.[open.body_plan]?.make || STAGES.map(([id]) => id);     // a plan may allow fewer steps
    const boxes = STAGES.filter(([id]) => makes.includes(id)).map(([id, label]) => {
      const made = !!open.has?.[id];
      const fresh = open.freshness?.[id] || {};
      const stale = fresh.state === 'stale';
      const make = h('input', { type: 'checkbox', 'data-stage': id, checked: !made });
      const again = h('input', { type: 'checkbox', 'data-again': id, checked: stale });     // out of date: offered for making again
      const note = stale ? ` — out of date: ${fresh.reason}` : fresh.state === 'unknown' ? ' (made before the freshness check)' : '';
      return h('label', { class: `cr-step${stale ? ' cr-stale' : ''}` }, make, ` ${label}${made ? ' (made)' : ''}${note} `,
        made && h('span', { class: 'cr-again' }, again, ' make again'));
    });
    const grid = h('label', { class: 'cr-step' }, h('input', { type: 'checkbox', 'data-grid': '' }),
      ' Draw four pictures with different seeds, to choose from (with the concept step)');
    const make = h('button', { class: 'btn primary', type: 'button', disabled: running, onclick: safe(makeOpen) }, running ? 'Making…' : 'Make the checked steps');
    const progress = job ? h('ol', { class: 'cr-progress' }, (job.steps || []).map(step => h('li', { class: `cr-${step.status}` },
      `${step.label}: ${step.status}${step.error ? ` — ${step.error}` : ''}`))) : null;
    const waiting = open.has?.concept && !open.concept_approved && makes.includes('3d')
      ? h('p', { class: 'hint' }, 'Approve the concept picture below before its 3D model is made.') : null;
    return h('div', { class: 'cr-steps' },
      h('h3', {}, 'Steps'),
      h('div', { class: 'cr-boxes' }, boxes, grid),
      make,
      waiting,
      progress,
      results());
  }

  // What was made, shown where it was made: the picture, the 3D viewer, what the game-model step measured, the licence rating,
  // and the files (the pack a game takes, and the models themselves).
  function results() {
    const parts = [];
    if (open.candidates?.length) parts.push(pictures());
    if (open.has?.concept) {
      const approval = open.concept_approved
        ? h('p', { class: 'hint' }, 'Approved: the 3D model is made from this picture.')
        : h('div', { class: 'cr-downloads' },
          h('button', { class: 'btn primary', type: 'button', onclick: safe(approveConcept) }, 'Approve this picture'),
          h('span', { class: 'hint' }, 'Look at it, then approve it. The 3D model is made only from an approved picture.'));
      parts.push(h('figure', { class: 'cr-figure' },
        h('img', { src: fileUrl(open.id, 'concept.png', open.updated), alt: `${open.name}: concept picture` }),
        h('figcaption', { class: 'hint' }, 'Concept picture'),
        approval));
    }
    const modelFile = open.has?.prep ? 'model.glb' : open.has?.['3d'] ? 'raw/model.glb' : null;
    if (modelFile) {
      const viewer = h('div', { class: 'cr-viewer' }, h('span', { class: 'hint' }, 'Loading the 3D viewer…'));
      mountViewer(viewer, fileUrl(open.id, modelFile, open.updated), open.name);
      const clips = open.report?.clips || [];
      const clipPick = clips.length ? h('label', { class: 'hint' }, 'Clip ', h('select', { 'aria-label': 'Clip',
        onchange: event => viewer.querySelector('model-viewer')?.setAttribute('animation-name', event.target.value) },
      clips.map(name => h('option', { value: name }, name)))) : null;
      parts.push(h('figure', { class: 'cr-figure' }, viewer,
        h('figcaption', { class: 'hint' }, open.has?.prep ? 'Game model (drag to turn it)' : 'Raw 3D model (drag to turn it)'),
        clipPick));
    }
    const stats = [];
    if (open.report) {
      const r = open.report;
      stats.push(h('p', {}, `${r.triangles_after.toLocaleString()} triangles (from ${r.triangles_before.toLocaleString()})`));
      stats.push(h('p', {}, r.bones ? `${r.bones} bones, clips: ${r.clips.join(', ')}` : 'Static model: no rig'));
      stats.push(h('p', {}, `Size: ${r.size_m.map(v => `${v} m`).join(' × ')} (width × length × height)`));
      if (r.head_flipped) stats.push(h('p', { class: 'hint' }, 'The head was turned to the front.'));
      for (const warning of r.checks?.warnings || []) stats.push(h('p', { class: 'cr-warning' }, warning));
    }
    if (stats.length) parts.push(h('div', { class: 'cr-stats' }, h('h3', {}, 'Game model'), ...stats));
    const licence = open.licence;
    if (licence && licence.verdict !== 'none') {
      parts.push(h('div', { class: 'cr-licence' }, h('h3', {}, 'Licence'),
        h('p', {}, licence.label),
        ...licence.models.map(m => h('p', { class: 'hint' }, `${m.name}: ${m.short || m.licence}`)),
        ...(licence.unknown || []).map(text => h('p', { class: 'cr-warning' }, text))));
    }
    const downloads = [];
    const packHint = !open.project ? 'Give the creature its game (the Game field) to make a pack.'
      : !open.has?.prep ? 'Make the game model to make a pack.' : null;
    downloads.push(h('button', { class: 'btn primary', type: 'button', disabled: !!packHint, onclick: safe(downloadPack) }, 'Download the pack'));
    if (packHint) downloads.push(h('span', { class: 'hint' }, packHint));
    if (open.has?.['3d']) downloads.push(h('a', { class: 'btn', href: fileUrl(open.id, 'raw/model.glb', open.updated), download: `${open.key || open.id}-3d.glb` }, 'Download the 3D model'));
    if (open.has?.prep) downloads.push(h('a', { class: 'btn', href: fileUrl(open.id, 'model.glb', open.updated), download: `${open.key || open.id}.glb` }, 'Download the game model'));
    parts.push(h('div', { class: 'cr-downloads' }, ...downloads));
    if (!open.has?.concept && !modelFile) return h('div', {}, h('p', { class: 'hint' }, 'Nothing made yet. Check the steps above and press Make.'), ...parts);
    return h('div', { class: 'cr-results' }, ...parts);
  }

  // The last picture grid: one picture per seed. Choosing one makes it the concept picture, and approves it.
  function pictures() {
    return h('div', { class: 'cr-figure' }, h('h3', {}, 'Pictures to choose from'),
      h('p', { class: 'hint' }, 'Each picture comes from a different seed. Choose the one that reads best from the side. Choosing one makes it the concept picture and approves it.'),
      h('div', { class: 'cr-candidates' }, open.candidates.map(c => h('figure', { class: 'cr-candidate' },
        h('img', { src: fileUrl(open.id, c.file, open.updated), alt: `Picture with seed ${c.seed}`, loading: 'lazy' }),
        h('figcaption', { class: 'hint' }, `Seed ${c.seed}${c.seed === open.seed && open.has?.concept ? ' (the concept now)' : ''}`),
        h('button', { class: 'btn', type: 'button', onclick: safe(() => chooseConcept(c.seed)) }, 'Use this picture')))));
  }

  async function chooseConcept(seed) {
    open = await api('POST', `/api/creatures/${open.id}/concept/choose`, { seed });
    toast(`Seed ${seed} is the concept picture now, and it is approved`);
    await refresh();
  }

  async function approveConcept() {
    open = await api('POST', `/api/creatures/${open.id}/approve`);
    toast('Approved: the 3D model can be made now');
    await refresh();
  }

  // The pack is a ZIP from the server. A refusal (no game yet, a licence not cleared) comes back as a reason, which is shown.
  async function downloadPack() {
    const response = await fetch(`/api/creatures/${open.id}/pack.zip`);
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      throw new Error(body.error || 'The pack could not be made');
    }
    const url = URL.createObjectURL(await response.blob());
    const link = h('a', { href: url, download: `${open.key || open.id}-pack.zip` });
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 60000);
  }

  // The Forge's own 3D viewer (the same one the Items tab uses). The page keeps working if the viewer will not start.
  function mountViewer(slot, url, name) {
    loadLocalRenderer().then(() => {
      const viewer = document.createElement('model-viewer');
      for (const [key, value] of Object.entries({ src: url, 'camera-controls': '', 'touch-action': 'pan-y', 'shadow-intensity': '0.8',
        exposure: '1.1', 'environment-image': 'neutral', 'camera-orbit': '90deg 72deg auto', 'interaction-prompt': 'none', loading: 'eager',
        autoplay: '', alt: `${name}: 3D model` })) viewer.setAttribute(key, value);
      slot.replaceChildren(viewer);
    }).catch(error => {
      slot.replaceChildren(h('span', { class: 'hint' }, `The 3D viewer could not start: ${error.message}`));
    });
  }

  // ---- what the buttons do ------------------------------------------------------------------------------------------------------------------
  function readForm() {
    return { name: form.name, project: form.project, shore: form.shore, body_plan: form.body_plan, length_m: Number(form.length_m),
      segments: Number(form.segments), tris: Number(form.tris), seed: form.seed === '' ? undefined : Number(form.seed), prompt: form.prompt,
      notes: form.notes, key: form.key, form: form.form, tier: form.tier, route: form.route };
  }

  async function saveForm() {
    const body = readForm();
    if (!open) {
      const created = await api('POST', '/api/creatures', body);
      toast(`Created ${created.name}`);
      open = created;
      form = toForm(created);
    } else {
      open = await api('PUT', `/api/creatures/${open.id}`, { ...body, seed: body.seed ?? open.seed });
      form = toForm(open);
      toast('Saved');
    }
    await refresh();
  }

  async function makeOpen() {
    const steps = [...document.querySelectorAll('[data-stage]')].filter(box => box.checked).map(box => box.dataset.stage);
    const redo = [...document.querySelectorAll('[data-again]')].filter(box => box.checked).map(box => box.dataset.again);
    const wanted = [...new Set([...steps, ...redo])];
    if (wanted.length === 0) { toast('Check at least one step to make', 'error'); return; }
    const grid = !!document.querySelector('[data-grid]')?.checked;
    if (grid && !wanted.includes('concept')) { toast('Check the concept step to draw a grid of pictures', 'error'); return; }
    await api('PUT', `/api/creatures/${open.id}`, { ...readForm(), seed: open.seed });    // the editor's words are what gets made
    const options = grid ? { seeds: randomSeeds() } : {};
    if (!grid) {     // a picture grid changes no stage above the others, so it is not previewed
      const preview = await api('POST', `/api/creatures/${open.id}/make`, { request: { steps: wanted, redo }, preview: true });
      if (preview.out_of_date?.length) {     // making these leaves the stages above it changed: say so before queuing (a preview queues nothing)
        const names = preview.out_of_date.map(id => STAGES.find(([stage]) => stage === id)?.[1] || id).join(' and ');
        if (!window.confirm(`Making these leaves the ${names} out of date until it is made again. Make them anyway?`)) {
          await refresh();          // the editor's changes were saved already, so the page shows them
          return;
        }
      }
    }
    const { job } = await api('POST', `/api/creatures/${open.id}/make`, { request: { steps: wanted, redo }, options });
    toast(`Queued ${job.steps.length} step${job.steps.length === 1 ? '' : 's'} for ${open.name}`);
    await refresh();
  }

  async function deleteOpen() {
    if (!window.confirm(`Delete ${open.name} and everything made for it? This cannot be undone.`)) return;
    await api('DELETE', `/api/creatures/${open.id}`);
    toast(`Deleted ${open.name}`);
    open = null;
    form = null;
    await refresh();
  }

  return { openTab, renderCreatures, refresh };
}
