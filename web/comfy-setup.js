// Set up ComfyUI (npcgen/comfy_setup.py, docs/comfyui-setup.md): where ComfyUI keeps its models, what each size of graphics card
// needs, what is there already, and installing what is missing after a yes, with progress and Stop.
import { $, api, btn, h, safe, toast } from './core.js';
import { statusBadge } from './licences-view.js';

const TIERS = [['16', 'About 16 GB'], ['24', 'About 24 GB'], ['32', '32 GB']];
const size = bytes => (bytes >= 1e9 ? `${(bytes / 1e9).toFixed(1)} GB` : `${Math.max(1, Math.round((bytes || 0) / 1e6))} MB`);
const time = seconds => (seconds < 90 ? `${Math.max(1, Math.round(seconds))} s` : seconds < 5400 ? `${Math.round(seconds / 60)} min` : `${(seconds / 3600).toFixed(1)} h`);
const FILE_WORDS = {
  ready: () => 'There',
  other: f => `A different copy is there (${size(f.have)}): NPC Forge uses it as it is and leaves it alone`,
  partial: f => `${size(f.have)} of ${size(f.size)} downloaded: Install carries on from there`,
  missing: f => `To download, ${size(f.size)}`,
  no_source: () => 'NPC Forge has no download for it: put it in ComfyUI yourself',
  no_folder: () => 'This ComfyUI has no folder for it: update ComfyUI',
  unknown: f => (f.size ? size(f.size) : ''),
};
const NODE_WORDS = {
  ready: 'Copied and loaded', restart: 'Copied: restart ComfyUI to load it', older: 'An older copy: Install replaces it',
  conflict: 'Another node has its folder, so NPC Forge leaves it alone', missing: 'Install copies it into custom_nodes', unknown: '',
};
const STEP_WORDS = { waiting: 'waiting', working: 'working', done: 'done', failed: 'failed', stopped: 'stopped' };
const ACTIVE = new Set(['running', 'stopping']);
const plural = (n, one, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

const view = { status: null, tier: null, ticked: new Set(), confirm: false, timer: null, onChange: null, busy: false };

/** Open the dialog. `onChange` runs after an install put something in (the page then reads ComfyUI's lists again). */
export async function openComfySetup({ onChange, portrait, status } = {}) {
  view.onChange = onChange || view.onChange;
  if (view.portrait && !portrait) view.status = null;
  view.portrait = portrait;
  let dlg = $('#comfySetup');
  if (!dlg) {
    dlg = h('dialog', { id: 'comfySetup', class: 'about advanced comfy-setup', 'aria-labelledby': 'comfySetupTitle',
      onclose: () => stopPolling(), onclick: e => { if (e.target === e.currentTarget) e.currentTarget.close(); } });
    document.body.append(dlg);
  }
  dlg.replaceChildren(h('div', { class: 'about-body' }, h('h2', { id: 'comfySetupTitle' }, 'Set up ComfyUI'), h('p', { class: 'hint' }, 'Looking for ComfyUI…')));
  if (!dlg.open) dlg.showModal();
  if (portrait && status) {
    view.status = status;
    view.tier = status.tier;
    view.ticked = new Set([portrait]);
    view.confirm = false;
    paint();
    if (ACTIVE.has(status.install?.state)) startPolling();
  } else {
    await load();
  }
}

async function load() {
  const status = await api('GET', '/api/comfy-setup');
  const first = !view.status;
  view.status = status;
  if (first || (!view.tier && !view.portrait)) {
    view.tier = status.tier;
    view.ticked = new Set(status.tier ? status.tiers[status.tier] : []);
  }
  for (const id of [...view.ticked]) if (!status.parts.some(p => p.id === id)) view.ticked.delete(id);
  paint();
  if (ACTIVE.has(status.install?.state)) startPolling();
}

const reload = safe(load);

// ---- the dialog ---------------------------------------------------------------------------------------------------------------
function paint() {
  const dlg = $('#comfySetup');
  if (!dlg || !view.status) return;
  const s = view.status;
  const keep = document.activeElement?.dataset?.focusKey;
  dlg.replaceChildren(
    h('div', { class: 'about-body' },
      h('div', { class: 'about-head' },
        h('div', { class: 'grow' }, h('h2', { id: 'comfySetupTitle' }, view.portrait ? 'Add a portrait' : 'Set up ComfyUI'),
          h('p', { class: 'hint' }, view.portrait
            ? 'Portraits need ComfyUI and a compatible graphics card. Z-Image Turbo was tested on a card with 16 GB of dedicated memory. The portrait model below is selected and shows its estimated download.'
            : 'NPC Forge draws its pictures and 3D models in ComfyUI. This puts in the model files each size of graphics card needs, from the Hugging Face pages listed under each part.')),
        btn('Close', '', () => dlg.close(), { cls: 'small ghost' })),
      whereSection(s),
      tierSection(s),
      h('section', { class: 'adv-group', 'aria-labelledby': 'setupPartsTitle' },
        h('h3', { id: 'setupPartsTitle' }, 'What to install'),
        h('div', { class: 'stack' }, s.parts.map(partCard))),
      h('p', { class: 'hint' }, `Every file comes from a pinned Hugging Face commit and is checked against its SHA-256 (pins checked ${s.checked}). Characters, dialogue and cast packs need none of this.`)),
    h('div', { class: 'about-foot adv-foot setup-foot' }, footer(s)));
  if (keep) dlg.querySelector(`[data-focus-key="${keep}"]`)?.focus();
}

function whereSection(s) {
  const c = s.comfy;
  const lines = [];
  if (c.how === 'running' && !c.problem) {
    lines.push(h('p', {}, `ComfyUI ${c.version || ''} is running at ${c.url}.`.replace('  ', ' ')),
      s.models_folder ? h('p', { class: 'hint' }, `New files go into ${s.models_folder}, each in the folder for its kind${s.free_bytes ? `. ${size(s.free_bytes)} is free there` : ''}.`) : null);
  } else if (c.how === 'folder' && !c.problem) {
    lines.push(h('p', {}, 'ComfyUI is not running, so NPC Forge uses the ComfyUI folder you chose.'),
      h('p', { class: 'hint' }, `New files go into ${s.models_folder}${s.free_bytes ? `, where ${size(s.free_bytes)} is free` : ''}. Start ComfyUI afterwards to draw.`));
  } else {
    lines.push(h('p', { class: 'notice' }, c.problem || 'ComfyUI was not found.'));
  }
  const running = c.how === 'running';
  const typed = h('input', { type: 'text', 'aria-label': 'ComfyUI folder', placeholder: 'D:\\ComfyUI', 'data-focus-key': 'typedFolder', class: 'grow' });
  const useTyped = safe(async () => {
    await api('POST', '/api/comfy-setup/folder', { folder: typed.value });
    toast('NPC Forge will use that ComfyUI folder.', 'ok');
    await load();
  });
  typed.addEventListener('keydown', e => { if (e.key === 'Enter') useTyped(); });
  return h('section', { class: 'adv-group', 'aria-labelledby': 'setupWhereTitle' },
    h('h3', { id: 'setupWhereTitle' }, 'ComfyUI'),
    lines,
    h('div', { class: 'row wrap' },
      btn('Look again', 'refresh', reload, { cls: 'small' }),
      running ? null : btn('Choose the ComfyUI folder…', '', safe(async () => {
        toast('The folder picker is open on this computer (it may be behind this window).');
        const answer = await api('POST', '/api/comfy-setup/folder', { pick: true });
        if (answer.changed) toast('NPC Forge will use that ComfyUI folder.', 'ok');
        await load();
      }), { cls: 'small' }),
      !running && c.how === 'folder' ? btn('Forget this folder', '', safe(async () => {
        await api('POST', '/api/comfy-setup/folder', { folder: null });
        await load();
      }), { cls: 'small ghost' }) : null),
    running ? null : h('details', {}, h('summary', {}, 'Details: type the folder instead'),
      h('div', { class: 'row' }, typed, btn('Use this folder', '', useTyped, { cls: 'small' })),
      h('p', { class: 'hint' }, 'The folder that has ComfyUI\'s models and custom_nodes folders in it (for the portable ZIP, the folder you unpacked works too).')),
    c.how === 'running' ? null : h('p', { class: 'hint' }, 'No ComfyUI yet? Get ComfyUI Desktop or the portable ZIP from ',
      h('a', { href: s.get_comfyui, target: '_blank', rel: 'noopener' }, 'comfy.org'), ', start it once, then press Look again.'));
}

function tierSection(s) {
  const pick = tier => {
    view.tier = tier;
    view.ticked = new Set(s.tiers[tier] || []);
    view.confirm = false;
    paint();
  };
  return h('section', { class: 'adv-group', 'aria-labelledby': 'setupTierTitle' },
    h('h3', { id: 'setupTierTitle' }, 'Your graphics card'),
    h('div', { class: 'tier-choice', role: 'radiogroup', 'aria-labelledby': 'setupTierTitle' },
      TIERS.map(([tier, label]) => h('label', { class: view.tier === tier ? 'on' : '' },
        h('input', { type: 'radio', name: 'setupTier', value: tier, checked: view.tier === tier, 'data-focus-key': `tier${tier}`,
          onchange: () => pick(tier) }),
        h('span', {}, label, s.tier === tier ? ' (this PC)' : '')))),
    h('p', { class: 'hint' }, s.tier ? 'Picking a size ticks what it can make (docs/hardware.md has the measurements). Tick or untick anything below.'
      : 'Start ComfyUI to see which size this PC has, or pick it here. Picking a size ticks what it can make.'));
}

function partCard(p) {
  const ticked = view.ticked.has(p.id);
  const missing = p.files.filter(f => f.state === 'missing' || f.state === 'partial');
  const unknown = p.files.length && p.files.every(f => f.state === 'unknown');
  const state = p.ready ? 'Ready'
    : unknown ? `${plural(p.files.length, 'file')}, ${size(p.files.reduce((sum, f) => sum + (f.size || 0), 0))} (find ComfyUI to see which are there)`
    : !p.installable ? 'NPC Forge cannot install all of it (see Details)'
    : missing.length ? `${missing.length} of ${p.files.length} files to download, ${size(p.download_bytes)}`
    : p.nodes.some(n => n.state === 'restart') ? 'Restart ComfyUI to load NPC Forge\'s node'
    : p.packs.some(k => k.state === 'missing') ? `Needs ${p.packs.map(k => k.name).join(' and ')}` : 'Almost ready';
  const box = h('input', { type: 'checkbox', checked: ticked, 'data-focus-key': `part-${p.id}`, 'aria-describedby': `part-state-${p.id.replace(':', '-')}`,
    onchange: e => { if (e.target.checked) view.ticked.add(p.id); else view.ticked.delete(p.id); view.confirm = false; paint(); } });
  const forTiers = p.tiers.length ? `For ${p.tiers.map(t => (t === '32' ? '32 GB' : `about ${t} GB`)).join(', ')} cards.` : 'Not picked for any size: tick it if you want it.';
  return h('article', { class: `setup-part${p.ready ? ' ready' : ''}` },
    h('label', { class: 'check' }, box,
      h('span', { class: 'grow' }, h('span', { class: 'name' }, p.what),
        h('span', { class: `hint setup-state${p.ready ? ' ok' : ''}`, id: `part-state-${p.id.replace(':', '-')}`, style: { display: 'block' } }, state))),
    h('div', { class: 'licence-note' }, p.licence ? statusBadge(p.licence.status) : null,
      h('span', { class: 'licence-short' }, p.licence ? `${p.licence.licence} · ${p.licence.short}` : ''), h('span', { class: 'hint' }, forTiers)),
    p.note ? h('p', { class: 'hint' }, p.note) : null,
    h('details', {}, h('summary', {}, 'Details'),
      h('table', { class: 'setup-files' },
        h('tbody', {}, p.files.map(f => h('tr', {},
          h('td', {}, h('code', {}, `${f.folder}/${f.name}`)),
          h('td', {}, FILE_WORDS[f.state]?.(f) ?? f.state),
          h('td', {}, f.source ? h('a', { href: f.source.url, target: '_blank', rel: 'noopener' }, f.source.repo) : '')))),
        p.nodes.map(n => h('tr', {}, h('td', {}, n.name), h('td', {}, NODE_WORDS[n.state] ?? n.state), h('td', {}, 'Part of NPC Forge'))),
        p.packs.map(k => h('tr', {}, h('td', {}, `${k.name}${k.also ? `, ${k.also}` : ''}`),
          h('td', {}, k.state === 'ready' ? 'Loaded in ComfyUI' : 'Not installed: NPC Forge cannot install other people\'s nodes. Its page says how.'),
          h('td', {}, h('a', { href: k.url, target: '_blank', rel: 'noopener' }, 'Its page'))))),
      p.files.some(f => f.where) ? h('p', { class: 'hint' }, `In ${[...new Set(p.files.map(f => f.where).filter(Boolean))].join(', ')}.`) : null));
}

// ---- what Install will do, the yes, and the progress -------------------------------------------------------------------------
function planOf(s) {
  const seen = new Set();
  const plan = { files: 0, download: 0, checks: 0, check: 0, nodes: 0 };
  const parts = s.parts.filter(p => view.ticked.has(p.id));
  for (const p of parts) {
    for (const f of p.files) {
      const key = `${f.folder}/${f.name}`;
      if (seen.has(key)) continue;
      seen.add(key);
      if (f.state === 'missing' || f.state === 'partial') { plan.files += 1; plan.download += f.size - (f.state === 'partial' ? f.have : 0); }
      else if (f.state === 'ready' && f.size) { plan.checks += 1; plan.check += f.size; }
    }
    plan.nodes += p.nodes.filter(n => n.state === 'missing' || n.state === 'older').length;
  }
  plan.parts = parts;
  plan.blocked = parts.filter(p => !p.installable);
  return plan;
}


function footer(s) {
  const job = s.install;
  if (job && ACTIVE.has(job.state)) {
    return h('div', { class: 'setup-run grow' },
      h('div', { id: 'setupProgress', role: 'status', 'aria-live': 'polite' }, progressLines(job)),
      h('div', { class: 'row' }, h('span', { class: 'grow' }),
        btn(job.state === 'stopping' ? 'Stopping…' : 'Stop', 'x', safe(async () => {
          view.status.install = (await api('POST', '/api/comfy-setup/stop')).install;
          paintProgress();
        }), { cls: 'small', disabled: job.state === 'stopping' })));
  }
  const plan = planOf(s);
  const findable = s.comfy.local && !s.comfy.problem;
  const last = job && job.state !== 'idle' && job.message
    ? h('p', { class: `notice setup-result ${job.state}`, role: 'status' }, job.message) : null;
  const nothing = !plan.parts.length ? 'Tick what to install.'
    : plan.blocked.length ? `${plan.blocked.map(p => p.what).join(', ')}: NPC Forge cannot install all of it (see Details). Untick it to go on.`
    : !plan.files && !plan.checks && !plan.nodes ? 'What is ticked is there already.' : null;
  if (view.confirm && findable && !nothing) {
    const words = [plan.files ? `Download ${plural(plan.files, 'file')}, ${size(plan.download)}, from Hugging Face into ${s.models_folder}?` : null,
      plan.checks ? `${plan.files ? 'First, check' : 'Check'} the ${plural(plan.checks, 'file')} already there (${size(plan.check)}) against their pinned SHA-256${plan.files ? '.' : '?'}` : null,
      plan.nodes ? `Copy NPC Forge's own ${plural(plan.nodes, 'node')} into custom_nodes${plan.files || plan.checks ? '.' : '?'}` : null,
      plan.files && s.free_bytes ? `${size(s.free_bytes)} is free there. Stop keeps what arrived, and Install carries on from it.` : null].filter(Boolean).join(' ');
    return h('div', { class: 'stack grow' }, last,
      h('p', { class: 'setup-confirm', tabindex: -1 }, words),
      h('div', { class: 'row' }, h('span', { class: 'grow' }),
        btn('Not now', '', () => { view.confirm = false; paint(); $('#comfySetup [data-focus-key="install"]')?.focus(); }, { cls: 'small ghost' }),
        btn(plan.files ? 'Download' : plan.checks ? 'Check' : 'Copy', 'download', safe(start), { cls: 'small primary', label: plan.files ? `Download ${size(plan.download)}` : null })));
  }
  const label = plan.files ? `Install: ${plural(plan.files, 'file')}, ${size(plan.download)}` : plan.checks ? `Check ${plural(plan.checks, 'file')}` : 'Install';
  const install = btn(label, 'download', () => { view.confirm = true; paint(); $('#comfySetup .setup-confirm')?.focus(); },
    { cls: 'small primary', disabled: !findable || !!nothing });
  install.dataset.focusKey = 'install';
  return h('div', { class: 'stack grow' }, last,
    h('div', { class: 'row' },
      h('span', { class: 'hint grow' }, !findable ? 'Find ComfyUI first (above).' : nothing || 'Nothing is downloaded until you say yes on the next step.'),
      install));
}

async function start() {
  const parts = view.status.parts.filter(p => view.ticked.has(p.id)).map(p => p.id);
  view.confirm = false;
  view.status.install = (await api('POST', '/api/comfy-setup/install', { parts, yes: true })).install;
  paint();
  startPolling();
}

function progressLines(job) {
  const total = job.bytes_total || 0, done = job.bytes_done || 0;
  const current = (job.steps || []).find(step => step.state === 'working');
  const verb = step => (step.action === 'check' ? 'Checking' : step.action === 'node' ? 'Copying' : 'Downloading');
  return [
    h('progress', { max: total || 1, value: Math.min(done, total || 1), 'aria-label': 'Set up ComfyUI: progress' }),
    h('p', {}, `${size(done)} of ${size(total)}${job.speed ? ` · ${size(job.speed)}/s` : ''}${job.eta ? ` · about ${time(job.eta)} left` : ''}${job.state === 'stopping' ? ' · stopping' : ''}`),
    current ? h('p', { class: 'hint' }, `${verb(current)} ${current.name}${current.message && !/^(downloading|checking)$/.test(current.message) ? ` (${current.message})` : ''}`) : null,
    h('ol', { class: 'setup-steps' }, (job.steps || []).map(step => h('li', { class: `setup-${step.state}` },
      `${step.name}: ${step.state === 'working' ? `${size(step.done || 0)} of ${size(step.size)}` : step.message || STEP_WORDS[step.state] || step.state}`))),
  ];
}

function paintProgress() {
  const box = $('#setupProgress');
  if (box && view.status?.install && ACTIVE.has(view.status.install.state)) box.replaceChildren(...progressLines(view.status.install));
  else paint();
}

function startPolling() {
  if (view.timer) return;
  view.timer = setInterval(async () => {
    let job;
    try { job = (await api('GET', '/api/comfy-setup?progress=1')).install; } catch { return; }
    view.status.install = job;
    if (ACTIVE.has(job.state)) { paintProgress(); return; }
    stopPolling();
    toast(job.message || 'The install finished.', job.state === 'done' ? 'ok' : job.state === 'failed' ? 'error' : 'info');
    await reload();
    if (job.state === 'done') view.onChange?.();
  }, 1000);
}

function stopPolling() {
  clearInterval(view.timer);
  view.timer = null;
}
