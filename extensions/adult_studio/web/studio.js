// Adult studio (18+). Everything shown is built from DOM nodes and textContent, never from markup strings.
'use strict';

const S = { state: null, people: [], current: null, record: null, custom: [], picked: new Set(), revealed: new Set(), error: '', busy: false,
            cfg: { styles: {}, settings: {} }, filter: { q: '' }, pic: { level: '', keep: '' }, aside: null, search: null };
const $ = sel => document.querySelector(sel);

function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === false || v == null) continue;
    if (k === 'class') el.className = v;
    else if (k === 'text') el.textContent = v;
    else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? '' : v);
  }
  for (const c of children.flat(Infinity)) if (c != null && c !== false && c !== '') el.append(c.nodeType ? c : document.createTextNode(String(c)));
  return el;
}

async function api(method, path, body) {
  // NPC Forge's server refuses anything but GET without a JSON content type, so every write sends one
  const write = method !== 'GET';
  const res = await fetch('/api/ext/adult' + path, {
    method, headers: write ? { 'Content-Type': 'application/json' } : undefined, body: write ? JSON.stringify(body ?? {}) : undefined,
  });
  let data = null;
  try { data = await res.json(); } catch { /* no body */ }
  if (!res.ok) throw new Error((data && data.error) || `Request failed (HTTP ${res.status})`);
  return data;
}

const blurOn = () => $('#blurToggle').checked;
const levelLabel = id => (S.state.levels.find(l => l.id === id) || {}).label || id;

// ---------- gate --------------------------------------------------------------------------------------------------
function renderGate() {
  const box = h('input', { type: 'checkbox' });
  const go = h('button', { class: 'btn primary', text: 'Open the studio', disabled: true, onclick: async () => {
    try { S.state = await api('POST', '/confirm', { confirm_adult: true }); await boot(); } catch (e) { S.error = e.message; render(); }
  } });
  box.addEventListener('change', () => { go.disabled = !box.checked; });
  $('#view').replaceChildren(h('div', { class: 's-card s-gate' },
    h('h2', { text: 'Adult studio' }),
    h('p', { class: 's-hint', text: 'Make and curate adult-level pictures of a character, then keep the good ones so Altera can send them instantly. ' +
      'Everything stays on this computer. It is for adults only, and off until you confirm.' }),
    h('label', { class: 'confirm' }, box, h('span', { text: 'I am 18 or older, and I want this.' })),
    h('p', { class: 's-hint', text: 'Some things are never allowed, whatever you type: characters under 18, anything involving minors or childlike details, ' +
      'real people, or anything non-consensual.' }),
    S.error && h('div', { class: 's-error', text: S.error }), go));
}

// ---------- studio ------------------------------------------------------------------------------------------------
// What the character list can be filtered by (web/facets.js, shared with the Library). Choosing here never touches the open character.
function peopleFacets() {
  const label = (table, id) => (table[id] || {}).label || id;
  return [...Facets.TRAITS,
    { key: 'style', any: 'All styles', values: n => [n.style], label: id => label(S.cfg.styles, id) },
    { key: 'setting', any: 'All settings', values: n => [n.setting], label: id => label(S.cfg.settings, id) },
    { key: 'kept', any: 'Any pictures', order: ['Has kept pictures', 'None kept yet', 'No pictures yet'],
      values: n => [n.kept ? 'Has kept pictures' : n.items ? 'None kept yet' : 'No pictures yet'] },
    { key: 'usable', any: 'Usable or not', order: ['Ready to use', 'Not usable'], values: n => [n.eligible ? 'Ready to use' : 'Not usable'] }];
}

// The search box is one element for the life of the page: rebuilding what is around it must not drop the caret
function keepSearchFocus(build) {
  const had = S.search && document.activeElement === S.search;
  const at = had ? [S.search.selectionStart, S.search.selectionEnd] : null;
  build();
  if (had) { S.search.focus(); try { S.search.setSelectionRange(...at); } catch { /* not a text field */ } }
}

function refreshAside() {
  if (!S.aside) return render();
  keepSearchFocus(() => S.aside.replaceChildren(renderPeople()));
}

function renderFilters(facets, shown) {
  if (!S.search) S.search = h('input', { type: 'text', placeholder: 'Search name, heritage, hair…', 'aria-label': 'Search characters',
    oninput: () => { S.filter.q = S.search.value; refreshAside(); } });
  return h('div', { class: 's-filters' }, S.search, Facets.controls(S.people, S.filter, facets, refreshAside),
    h('div', { class: 's-filterfoot' }, h('span', { class: 's-hint', text: `${shown.length} of ${S.people.length}` }),
      Facets.active(S.filter, facets) ? h('button', { type: 'button', class: 's-link', text: 'Clear filters',
        onclick: () => { Facets.clear(S.filter, facets); S.search.value = ''; refreshAside(); } }) : null));
}

function renderPeople() {
  const facets = peopleFacets(), shown = Facets.apply(S.people, S.filter, facets);
  return h('div', {}, renderFilters(facets, shown), h('div', { class: 's-people' }, shown.length ? shown.map(p => h('button', {
    class: 's-person', 'aria-current': S.current === p.id ? 'true' : null, disabled: !p.eligible, onclick: () => choose(p.id),
  }, p.thumb ? h('img', { src: `/api/npcs/${p.id}/files/thumb.png`, alt: '' }) : h('span', { class: 'noimg' }),
    h('span', {}, h('span', { class: 'name', text: p.name || p.id }),
      h('span', { class: 'meta', text: [p.age ?? 'no age', p.heritage, p.hair_color].filter(Boolean).join(' · ') }),
      h('span', { class: 'meta', text: `${p.kept} kept of ${p.items}` }),
      !p.eligible && h('span', { class: 'why', text: p.reason }))))
    : h('p', { class: 's-hint', text: S.people.length ? 'No character matches these filters.' : 'No characters yet.' })));
}

// Bust size, shape and areolas: saved as soon as one is changed, so every later picture (and Altera) uses them
function renderBody(r) {
  if (!r.body || !S.state.body_choices) return null;                 // a server started before the body options existed
  return [h('h3', { text: 'Body' }),
    h('p', { class: 's-hint', text: (r.body_saved ? 'Saved for this character. ' : 'Chosen for this character to start with, so every character differs. ') +
      'Bust size and shape go into every picture; the areolas only into Explicit ones, where they show.' }),
    h('div', { class: 's-bodygrid' }, S.state.body_choices.map(c => {
    const pick = h('select', { 'aria-label': c.label }, c.options.map(o => h('option', { value: o.id, text: o.label })));
    pick.value = r.body[c.field];
    pick.addEventListener('change', async () => {
      try { S.record = await api('PUT', `/characters/${r.npc.id}/body`, { [c.field]: pick.value }); S.error = ''; } catch (e) { S.error = e.message; }
      render();
    });
    return h('label', { class: 's-field' }, h('span', { text: c.label }), pick);
  }))];
}

function renderNotes(r) {
  const area = h('textarea', { class: 's-notes', maxlength: 300, placeholder: 'e.g. athletic build, defined waist, toned arms' });
  area.value = r.notes;
  const save = h('button', { class: 'btn', text: 'Save notes', onclick: async () => {
    try { S.record = await api('PUT', `/characters/${r.npc.id}/notes`, { notes: area.value }); S.error = ''; } catch (e) { S.error = e.message; }
    render();
  } });
  const stale = r.items.filter(i => i.outdated.length);
  return h('div', { class: 's-card' }, h('h2', { text: 'Perfect the look' }),
    h('p', { class: 's-hint', text: 'Everything here is added to every picture made here and is imported into Altera, so its own pictures match. ' +
      'Change something, redo, compare, repeat. To change the face, make a new portrait in NPC Forge: pictures made from the old one are marked.' }),
    renderBody(r),
    h('h3', { text: 'Notes' }),
    area, h('div', { class: 's-row' }, save,
      stale.length ? h('button', { class: 'btn', text: `Redo ${stale.length} outdated`, onclick: async () => {
        try { await api('POST', `/characters/${r.npc.id}/redo-outdated`, {}); S.error = ''; await refresh(); } catch (e) { S.error = e.message; render(); }
      } }) : null));
}

// Which engine draws the pictures: chosen per pair of levels and saved at once
function renderEngines() {
  const e = S.state.engines;
  if (!e) return null;                                               // a server started before the engines existed
  const used = new Set(Object.values(e.levels));                     // only the engines a level uses can hold a picture up
  const gone = e.choices.filter(c => used.has(c.id) && c.missing && c.missing.length).flatMap(c => c.missing);
  const pick = (title, levels) => {
    const sel = h('select', { 'aria-label': title }, e.choices.map(c => h('option', { value: c.id, text: c.label + (c.id !== 'mage' && c.missing && c.missing.length ? ' (files missing)' : '') })));
    sel.value = e.levels[levels[0]];
    sel.addEventListener('change', async () => {
      try { S.state = await api('PUT', '/settings', { engines: Object.fromEntries(levels.map(l => [l, sel.value])) }); S.error = ''; } catch (err) { S.error = err.message; }
      render();
    });
    return h('label', { class: 's-field' }, h('span', { text: title }), sel);
  };
  // the main picture (portrait) of a Models character: its own list of engines
  const portraits = e.portrait_choices || [];
  const chosen = portraits.find(c => c.id === (e.levels.portrait || 'zimage'));
  const goneP = chosen && chosen.missing && chosen.missing.length ? chosen.missing : [];
  const pickPortrait = () => {
    const sel = h('select', { 'aria-label': 'Main picture of a Models character' }, portraits.map(c => h('option', { value: c.id, text: c.label + (c.missing && c.missing.length ? ' (files missing)' : '') })));
    sel.value = e.levels.portrait || 'zimage';
    sel.addEventListener('change', async () => {
      try { S.state = await api('PUT', '/settings', { engines: { portrait: sel.value } }); S.error = ''; } catch (err) { S.error = err.message; }
      render();
    });
    return h('label', { class: 's-field' }, h('span', { text: 'Main picture of a Models character' }), sel);
  };
  return h('div', { class: 's-card' }, h('h2', { text: 'Picture engine' }),
    h('p', { class: 's-hint', text: 'Qwen-Image-Edit draws far better whole bodies, about 35 seconds a picture. It needs about 30 GB of free RAM and unloads its ' +
      'models after every picture. If it cannot run, that picture is made with Mage-Flow and marked, so nothing is lost. The realism version adds a LoRA for ' +
      'skin texture and light; it is tried on two scenes only, and it can pull the frame closer.' }),
    gone.length ? h('div', { class: 's-error', text: 'ComfyUI is missing: ' + gone.join(', ') }) : null,
    h('div', { class: 's-bodygrid' }, pick('Everyday and Romantic', ['off', 'romantic']), pick('Suggestive and Explicit', ['suggestive', 'explicit'])),
    portraits.length ? h('div', { class: 's-bodygrid' }, pickPortrait()) : null,
    portraits.length ? h('p', { class: 's-hint', text: 'The main picture is the portrait every other picture starts from. Z-Image Turbo draws the most model-like faces ' +
      '(about 16 seconds). Qwen-Image 2512 draws more natural, filmic women (about 30 seconds, and it reads older to the age check). Mage-Flow is built in and is what any ' +
      'of them falls back to. Altera refuses to import a portrait that reads as under its minimum age: draw it again or pick another engine. This only changes portraits you ' +
      'draw from now on: to switch an existing character, draw its Portrait again in NPC Forge, and the studio pictures made from the old one are marked "old portrait".' }) : null,
    goneP.length ? h('div', { class: 's-error', text: 'ComfyUI is missing: ' + goneP.join(', ') }) : null);
}

// A level's heading with how many of its scenes are ticked, and one click to tick or clear them all
function groupHead(g) {
  const ids = g.scenes.map(s => s.id), on = ids.filter(id => S.picked.has(id)).length, all = on === ids.length;
  return h('div', { class: 's-grouphead' }, h('h3', { text: `${g.level.label} · ${on ? `${on} of ` : ''}${ids.length}` }),
    h('button', { type: 'button', class: 's-link', text: all ? 'Clear' : 'Select all', onclick: () => { ids.forEach(id => (all ? S.picked.delete(id) : S.picked.add(id))); render(); } }));
}

function renderScenes(r) {
  const groups = S.state.levels.map(l => ({ level: l, scenes: S.state.scenes.filter(s => s.level === l.id) })).filter(g => g.scenes.length);
  const text = h('input', { type: 'text', maxlength: 300, placeholder: 'Describe a scene: where, what, what is worn (no names)' });
  const level = h('select', {}, S.state.levels.map(l => h('option', { value: l.id, text: l.label })));
  level.value = 'suggestive';
  const add = () => { const t = text.value.trim(); if (!t) return; S.custom.push({ text: t, level: level.value }); text.value = ''; render(); };
  text.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); add(); } });
  const total = S.picked.size + S.custom.length;
  const max = S.state.max_batch || 16;
  const label = total > max ? `${total} picked: the most is ${max} at once` : total ? `Make ${total} picture${total > 1 ? 's' : ''}` : 'Pick scenes first';
  const make = h('button', { class: 'btn primary', text: label, disabled: !total || total > max || S.busy, onclick: async () => {
    S.busy = true; render();
    try {
      await api('POST', `/characters/${r.npc.id}/items`, { scene_ids: [...S.picked], custom: S.custom });
      S.picked.clear(); S.custom = []; S.error = '';
    } catch (e) { S.error = e.message; }
    S.busy = false; await refresh();
  } });
  return h('div', { class: 's-card' }, h('h2', { text: 'Make pictures' }),
    h('p', { class: 's-hint', text: 'Each picture is an edit of the portrait, about 30 seconds on your GPU. Scenes come from scenes.json in this extension: edit it to add your own.' }),
    groups.map(g => [groupHead(g), h('div', { class: 's-scenes' }, g.scenes.map(s => {
      const box = h('input', { type: 'checkbox' });
      box.checked = S.picked.has(s.id);
      box.addEventListener('change', () => { box.checked ? S.picked.add(s.id) : S.picked.delete(s.id); render(); });
      return h('label', { class: 's-scene', title: s.text }, box, h('span', {}, s.label, h('span', { class: 'sub', text: s.text })));
    }))]),
    h('h3', { text: 'Your own scene' }),
    h('div', { class: 's-row' }, text, level, h('button', { class: 'btn', text: 'Add', onclick: add })),
    S.custom.length ? h('div', { class: 's-row' }, S.custom.map((c, i) => h('span', { class: 's-chip', title: c.text },
      `${levelLabel(c.level)}: ${c.text.slice(0, 36)}`, h('button', { text: '×', title: 'Remove', onclick: () => { S.custom.splice(i, 1); render(); } })))) : null,
    h('div', { class: 's-row' }, make));
}

function renderTile(r, item) {
  const shot = h('div', { class: 's-shot' });
  if (item.status === 'ready') {
    const img = h('img', { src: `/api/ext/adult/characters/${r.npc.id}/items/${item.id}/image?${item.finished || ''}`, alt: item.label });
    shot.classList.toggle('blurred', blurOn() && !S.revealed.has(item.id));
    img.addEventListener('click', () => {
      if (shot.classList.contains('blurred')) { S.revealed.add(item.id); shot.classList.remove('blurred'); }
      else window.open(img.src, '_blank', 'noopener');
    });
    shot.append(img, h('div', { class: 'veil', text: 'Click to reveal' }));
  } else if (item.status === 'pending' || item.status === 'running') {
    shot.append(h('div', { class: 'state' }, h('div', { class: 'spinner' }), item.status === 'running' ? 'Making it…' : 'Waiting in line…'));
  } else {
    shot.append(h('div', { class: 'state bad', text: item.reason || 'This picture could not be made.' }));
  }
  const ready = item.status === 'ready';
  const act = (fn) => async () => { try { await fn(); S.error = ''; } catch (e) { S.error = e.message; } await refresh(); };
  return h('div', { class: 's-tile' + (item.keep ? ' kept' : '') }, shot,
    h('div', { class: 's-meta' }, h('span', { class: 'label', text: item.label }),
      h('div', { class: 's-badges' }, h('span', { class: 's-badge', text: levelLabel(item.level) }),
        ready && item.engine ? h('span', { class: 's-badge' + (item.engine_note ? ' warn' : ''), title: item.engine_note || 'Made with ' + item.engine,
          text: ({ qwen: 'Qwen', qwen_real: 'Qwen+realism' })[item.engine] || 'Mage-Flow' }) : null,
        ready && !item.checked ? h('span', { class: 's-badge warn', title: 'Not age-checked in the studio. Altera checks it again when it imports the picture.', text: 'unchecked' }) : null,
        ...item.outdated.map(o => h('span', { class: 's-badge warn', title: 'Redo it to apply the change', text: { portrait: 'old portrait', frame: 'old framing' }[o] || 'old body' })))),
    h('div', { class: 's-tools' },
      ready ? h('button', { class: 'btn small' + (item.keep ? ' primary' : ''), text: item.keep ? '✓ Kept' : 'Keep',
        title: 'Only kept pictures are imported into Altera', onclick: act(() => api('PUT', `/characters/${r.npc.id}/items/${item.id}`, { keep: !item.keep })) }) : null,
      item.status !== 'pending' && item.status !== 'running' ? h('button', { class: 'btn small', text: 'Redo', onclick: act(() => api('POST', `/characters/${r.npc.id}/items/${item.id}/redo`, {})) }) : null,
      h('button', { class: 'btn small danger', text: 'Delete', onclick: () => { if (confirm('Delete this picture?')) act(() => api('DELETE', `/characters/${r.npc.id}/items/${item.id}`))(); } })));
}

// Level and kept-or-not, over the pictures of the open character
function renderPictureFilters(r) {
  const pick = (options, key, any) => {
    const el = h('select', { class: 'facet' + (S.pic[key] ? ' on' : ''), 'aria-label': any },
      h('option', { value: '', text: any }), options.map(([value, text]) => h('option', { value, text })));
    el.value = S.pic[key];
    el.addEventListener('change', () => { S.pic[key] = el.value; render(); });
    return el;
  };
  const levels = S.state.levels.filter(l => r.items.some(i => i.level === l.id))
    .map(l => [l.id, `${l.label} (${r.items.filter(i => i.level === l.id).length})`]);
  return h('div', { class: 's-row' }, pick(levels, 'level', 'All levels'), pick([['kept', 'Kept'], ['not', 'Not kept']], 'keep', 'Kept or not'));
}

function renderMain() {
  const r = S.record;
  if (!r) return h('div', { class: 's-card' }, h('p', { class: 's-hint', text: 'Choose a character on the left. Only characters aged 18 or older with a portrait can be used.' }));
  const kept = r.items.filter(i => i.keep && i.status === 'ready').length;
  const shownItems = [...r.items].reverse().filter(i => (!S.pic.level || i.level === S.pic.level)
    && (!S.pic.keep || (S.pic.keep === 'kept') === Boolean(i.keep && i.status === 'ready')));
  const model = h('input', { type: 'text', placeholder: 'vision model name in LM Studio, e.g. qwen/qwen3.8-27b', value: S.state.age_check_model });
  return h('div', {},
    h('div', { class: 's-card s-head' }, h('img', { src: `/api/npcs/${r.npc.id}/files/${'thumb.png'}`, alt: '' }),
      h('div', {}, h('div', { class: 'name', text: r.npc.name }), h('div', { class: 's-hint', text: `${r.npc.age} · ${r.npc.gender || ''} · ${kept} kept. In Altera: Settings > Avatar > Ready-made pictures.` }))),
    S.error && h('div', { class: 's-error', text: S.error }),
    renderNotes(r), renderEngines(), renderScenes(r),
    h('div', { class: 's-card' }, h('h2', { text: `Pictures (${shownItems.length === r.items.length ? r.items.length : `${shownItems.length} of ${r.items.length}`})` }),
      h('p', { class: 's-hint', text: 'Keep the good ones, redo or delete the rest. Click a picture to reveal it, click again to open it full size.' }),
      r.items.length > 1 ? renderPictureFilters(r) : null,
      r.items.length ? (shownItems.length ? h('div', { class: 's-gallery' }, shownItems.map(i => renderTile(r, i))) : h('p', { class: 's-hint', text: 'No picture matches.' }))
        : h('p', { class: 's-hint', text: 'Nothing yet.' })),
    h('div', { class: 's-card' }, h('h2', { text: 'Age check (optional here)' }),
      h('p', { class: 's-hint', text: 'A vision model can check every picture as it is made; one that reads as under 18 is discarded. Blank = pictures are not checked here. ' +
        'Altera always checks again when it imports a picture, and refuses Suggestive and higher without a passing check.' }),
      h('div', { class: 's-row' }, model, h('button', { class: 'btn', text: 'Save', onclick: async () => {
        try { S.state = await api('PUT', '/settings', { age_check_model: model.value }); S.error = ''; } catch (e) { S.error = e.message; }
        render();
      } }))));
}

function render() {
  if (!S.state) return;
  if (!S.state.enabled) return renderGate();
  // replaceChildren turns a null argument into the text "null", so only real nodes go in
  keepSearchFocus(() => {
    S.aside = h('aside', {}, renderPeople());
    $('#view').replaceChildren(...[S.error && !S.current ? h('div', { class: 's-error', text: S.error }) : null,
      h('div', { class: 's-layout' }, S.aside, h('section', {}, renderMain()))].filter(Boolean));
  });
}

let timer = null;
async function refresh() {
  clearTimeout(timer);
  if (!S.current) return render();
  try {
    S.record = await api('GET', `/characters/${S.current}`);
    S.people = (await api('GET', '/characters')).characters;
  } catch (e) { S.error = e.message; }
  render();
  if (S.record && S.record.busy) timer = setTimeout(refresh, 3000);
}

async function choose(id) { S.current = id; S.record = null; S.error = ''; S.picked.clear(); S.custom = []; S.pic = { level: '', keep: '' }; await refresh(); }

async function boot() {
  if (S.state.enabled) {
    S.people = (await api('GET', '/characters')).characters;
    try { S.cfg = { styles: {}, settings: {}, ...await (await fetch('/api/config')).json() }; } catch { /* the filters then show ids instead of labels */ }
  }
  render();
}

$('#blurToggle').checked = localStorage.getItem('adultStudioBlur') !== 'off';
$('#blurToggle').addEventListener('change', () => {
  localStorage.setItem('adultStudioBlur', $('#blurToggle').checked ? 'on' : 'off');
  S.revealed.clear();
  render();
});
api('GET', '/state').then(s => { S.state = s; return boot(); })
  .catch(e => { $('#view').replaceChildren(h('pre', { style: 'padding:24px' }, 'The studio could not start: ' + e.message)); });
