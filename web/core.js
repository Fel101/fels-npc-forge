// Shared browser primitives and state. This module has no view-specific rendering.
export const $ = (sel, root = document) => root.querySelector(sel);

export function h(tag, props, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k === 'style' && typeof v === 'object') Object.assign(el.style, v);
    else if (k === 'dataset') Object.assign(el.dataset, v);
    else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2).toLowerCase(), v);
    else if (k in el) el[k] = v;
    else el.setAttribute(k, v === true ? '' : v);
  }
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child.nodeType ? child : document.createTextNode(String(child)));
  }
  return el;
}

const ICONS = {
  lock: '<rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>',
  unlock: '<rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 9.9-1"/>',
  refresh: '<polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/>',
  undo: '<polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"/>',
  upload: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/>',
  download: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>',
  trash: '<polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6M14 11v6"/><path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/>',
  dice: '<polyline points="16 3 21 3 21 8"/><line x1="4" y1="20" x2="21" y2="3"/><polyline points="21 16 21 21 16 21"/><line x1="15" y1="15" x2="21" y2="21"/><line x1="4" y1="4" x2="9" y2="9"/>',
  zap: '<polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>',
  eye: '<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/>',
  save: '<path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/>',
  plus: '<line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>',
  x: '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>',
  info: '<circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/>',
  gear: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>',
};

export function icon(name) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  for (const [k, v] of Object.entries({ viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': 2, 'stroke-linecap': 'round', 'stroke-linejoin': 'round', 'aria-hidden': 'true' })) svg.setAttribute(k, v);
  svg.innerHTML = ICONS[name]; // static markup from ICONS above, never user data
  return svg;
}

export const btn = (label, iconName, onclick, options = {}) =>
  h('button', { type: 'button', class: `btn ${options.cls || ''}`, onclick, title: options.title, disabled: options.disabled,
    'aria-label': options.label || (label ? null : options.title) }, iconName && icon(iconName), label);

export const getPath = (object, path) => path.split('.').reduce((value, key) => (value == null ? value : value[key]), object);
export function setPath(object, path, value) {
  const keys = path.split('.'); const last = keys.pop();
  keys.reduce((item, key) => (item[key] ??= {}), object)[last] = value;
}
export const debounce = (fn, ms) => { let timer; return (...args) => { clearTimeout(timer); timer = setTimeout(() => fn(...args), ms); }; };

export function toast(message, kind = 'info', ms = kind === 'error' ? 9000 : 4000) {
  const item = h('div', { class: `toast ${kind}`, role: 'status' }, message);
  $('#toasts').append(item);
  setTimeout(() => item.remove(), ms);
}

export async function api(method, path, body) {
  const options = { method, headers: { 'Content-Type': 'application/json' } };
  if (body !== undefined) options.body = JSON.stringify(body);
  else if (method !== 'GET') options.body = '{}';
  let response;
  try { response = await fetch(path, options); } catch { throw new Error('Cannot reach the NPC server. Is it still running?'); }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `The server reported an error (code ${response.status}) and gave no details.`);
  return data;
}
export const safe = fn => async (...args) => { try { return await fn(...args); } catch (error) { toast(error.message || String(error), 'error'); } };

const LS_KEY = 'npcforge.recipe.v1';
// The engines that draw by default changed on 2026-10-13 (Qwen-Image 2512 draws, Qwen-Image-Edit 2511 edits, and no quiet Mage-Flow fallback). A recipe saved before then holds the old defaults
// as if they had been chosen, so the new ones would never reach this browser: the first time it is loaded, an option that still has the old default is given the server's; anything else stays.
const ENGINES_STAMP = 'engines-2026-10-13';
const OLD_ENGINE_DEFAULTS = { image_model: 'mage', edit_model: 'mage', engine_fallback: true };
export const S = {
  cfg: null, health: null, view: 'create', outdated: false,
  recipe: null,
  draft: null, dirty: false, dialogueDirty: false, locks: new Set(),
  library: [], castPacks: [], filter: { q: '', setting: '', style: '' }, selected: new Set(),
  trash: [], trashOpen: false,      // the characters deleted from the library (GET /api/trash), and whether the Library shows them
  versionsOpen: {},                 // which pictures have their list of earlier versions open (by slot: portrait, expressions/happy...)
  jobs: [], seen: {}, notified: new Set(), dismissed: new Set(), polling: false,
  promptsOpen: false, prompts: null, animTimers: [],
  panelTab: 'profile',  // the tab open in the panel beside the pictures: profile, edit or dialogue
  pendingPhoto: null,   // { dataUrl } staged client-side before S.draft has a real id; never persisted (see saveRecipe)
};

function defaultRecipe() {
  const defaults = S.cfg.defaults;
  return {
    setting: defaults.setting, style: defaults.style,
    assets: { portrait: defaults.assets.portrait, sprite: defaults.assets.sprite, model_3d: defaults.assets.model_3d, expressions: [...defaults.assets.expressions] },
    engines_stamp: S.outdated ? undefined : ENGINES_STAMP,         // (a recipe saved from now on has chosen its engines, or kept the defaults, on purpose: see loadRecipe; an older server still publishes the old defaults, so nothing is stamped)
    options: { ...defaults.options }, constraints: { gender: '', age_group: '', occupation: '', origin: '', hair_color: '', hair_style: '', morphology: '' },
    presets: {},          // { setting id: { 'appearance.build': 'stocky', ... } }: the Advanced options, sent as locked fields when rolling
    count: 5, mode: 'roll', describe: '',
    anim: { on: false, clips: ['walk'], dirs: ['down', 'left', 'right', 'up'], frames: 4, candidates: 1, mirror_left: true },
    sheet: { on: false, views: (S.cfg.sheet?.views || []).map(view => view.id), pose: S.cfg.sheet?.default_pose || '' },
    voice: { on: false, engine: 'qwen', takes: 3, lines: false },
  };
}

export function loadRecipe() {
  const base = defaultRecipe();
  try {
    const saved = JSON.parse(localStorage.getItem(LS_KEY));
    if (saved && typeof saved === 'object') {
      const recipe = { ...base, ...saved, assets: { ...base.assets, ...saved.assets }, options: { ...base.options, ...saved.options },
        constraints: { ...base.constraints, ...saved.constraints }, anim: { ...base.anim, ...saved.anim }, sheet: { ...base.sheet, ...saved.sheet }, voice: { ...base.voice, ...saved.voice },
        presets: saved.presets && typeof saved.presets === 'object' && !Array.isArray(saved.presets) ? saved.presets : {} };
      if (!S.cfg.sheet?.poses.some(pose => pose.id === recipe.sheet.pose)) recipe.sheet.pose = base.sheet.pose;
      if (!S.cfg.settings[recipe.setting]) recipe.setting = base.setting;
      if (!S.cfg.styles[recipe.style]) recipe.style = base.style;
      if (!S.cfg.quality[recipe.options.quality]) recipe.options.quality = base.options.quality;
      recipe.engines_stamp = saved.engines_stamp;          // (not the fresh recipe's: this one has not been moved yet)
      if (!S.outdated && saved.engines_stamp !== ENGINES_STAMP) {      // (an older server still says Mage-Flow: wait for the restart, or the stamp would keep it)
        for (const [key, was] of Object.entries(OLD_ENGINE_DEFAULTS)) if (recipe.options[key] === was) recipe.options[key] = base.options[key];
        recipe.engines_stamp = ENGINES_STAMP;
      }
      for (const [key, kind] of [['image_model', 'image'], ['edit_model', 'edit']])      // a model this server does not offer
        if (!(S.cfg.engines?.[kind] || []).some(e => e.id === recipe.options[key])) recipe.options[key] = base.options[key];
      if (typeof recipe.options.engine_fallback !== 'boolean') recipe.options.engine_fallback = base.options.engine_fallback;
      if (!(S.cfg.engines?.three_d || []).some(e => e.id === recipe.options.three_d_model)) recipe.options.three_d_model = 'hunyuan21';
      if (!['low', 'medium', 'high'].includes(recipe.options.three_d_quality)) recipe.options.three_d_quality = 'medium';
      if (!['turnaround', 'portrait'].includes(recipe.options.three_d_face_source)) recipe.options.three_d_face_source = 'turnaround';
      if (!['sprite', 'turnaround'].includes(recipe.options.three_d_hunyuan_source)) recipe.options.three_d_hunyuan_source = 'sprite';
      if (!['standard', 'fine'].includes(recipe.options.three_d_hunyuan_detail)) recipe.options.three_d_hunyuan_detail = 'standard';
      if (!['low', 'medium'].includes(recipe.options.rig_skeleton)) recipe.options.rig_skeleton = 'low';
      if (!['auto', 'basic', 'library'].includes(recipe.options.rig_clips)) recipe.options.rig_clips = 'auto';
      if (!['medium', 'high'].includes(recipe.options.head_quality)) recipe.options.head_quality = 'medium';
      if (!['neutral', 'keep'].includes(recipe.options.head_expression)) recipe.options.head_expression = 'neutral';
      recipe.options.head_match_hair = recipe.options.head_match_hair === true;
      recipe.options.three_d_soften_eyes = recipe.options.three_d_soften_eyes === true;
      recipe.options.three_d_glasses_off = recipe.options.three_d_glasses_off === true;
      return recipe;
    }
  } catch { /* storage unavailable or corrupt: fall back to defaults */ }
  return base;
}

export function saveRecipe() { try { localStorage.setItem(LS_KEY, JSON.stringify(S.recipe)); } catch { /* ignore */ } }
export const npcPayload = npc => ({ setting: npc.setting, style: npc.style, seed: npc.seed, freeform: !!npc.freeform, morphology: npc.morphology,
  profile: npc.profile, appearance: npc.appearance });
export const assetUrl = (file, stamp) => `/api/npcs/${S.draft.id}/files/${file}?v=${encodeURIComponent(stamp || '')}`;
export const fullName = npc => `${npc.profile.first_name} ${npc.profile.last_name}`.trim() || 'Unnamed NPC';
export const isActive = job => job.status === 'queued' || job.status === 'running';
export const slug = text => text.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');

// Every portrait in this app is square (see .tile.portrait .frame in style.css); a photo from a phone never is, so
// it is center-cropped here, client-side, before it ever reaches the server. imageOrientation: 'from-image' bakes
// in the photo's EXIF rotation, since canvas drawImage otherwise ignores it. JPEG (not PNG) because photographic
// content compresses far smaller lossy, which matters given the server's request-body size cap.
export async function photoToSquareDataUrl(file, maxSize = 1536, maxBytes = 1_400_000) {
  const bitmap = await createImageBitmap(file, { imageOrientation: 'from-image' });
  const side = Math.min(bitmap.width, bitmap.height);
  const out = Math.min(maxSize, side);
  const canvas = document.createElement('canvas');
  canvas.width = canvas.height = out;
  canvas.getContext('2d').drawImage(bitmap, (bitmap.width - side) / 2, (bitmap.height - side) / 2, side, side, 0, 0, out, out);
  for (const quality of [0.85, 0.7, 0.5, 0.35]) {
    const url = canvas.toDataURL('image/jpeg', quality);
    if (url.length * 0.75 < maxBytes) return url;   // base64 -> raw bytes is ~0.75x the encoded string length
  }
  return canvas.toDataURL('image/jpeg', 0.3);
}
