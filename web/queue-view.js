import { $, S, api, btn, h, isActive, safe, toast } from './core.js';

// On a wide screen the queue is a column beside the page (it never covers anything); on a narrow one it floats. Either way it
// can be hidden: the Queue button in the top bar brings it back, and a new job shows it again.
const HIDDEN_KEY = 'npcforge.queue.hidden';
let hidden = false;
try { hidden = localStorage.getItem(HIDDEN_KEY) === '1'; } catch { /* storage unavailable: start shown */ }
function setHidden(value) {
  hidden = value;
  try { localStorage.setItem(HIDDEN_KEY, value ? '1' : '0'); } catch { /* ignore */ }
}

export function createQueueController({ openNpc, openScene, onSceneJobChange, openItem, onItemJobChange, onNpcJobChange, renderGallery, refreshLibrary }) {
  function afterSubmit(result) {
    for (const job of result.jobs) {
      S.jobs = S.jobs.filter(existing => existing.id !== job.id).concat(job);
      S.seen[job.id] = job.version;
    }
    setHidden(false);
    renderQueue(); startPolling(); refreshLibrary();
  }

  function thumb(job) {
    if (job.target_type === 'scene') return h('button', { type: 'button', class: 'job-thumb', title: 'Open this scene',
      'aria-label': `Open scene ${job.npc_name}`, onclick: safe(() => openScene(job.scene_id)) }, 'SC');
    if (job.target_type === 'item') return h('button', { type: 'button', class: 'job-thumb', title: 'Open this item',
      'aria-label': `Open item ${job.npc_name}`, onclick: safe(() => openItem(job.item_id)) }, 'IT');
    const row = S.library.find(npc => npc.id === job.npc_id);
    const initials = (job.npc_name || '?').split(/\s+/).map(word => word[0]).join('').slice(0, 2).toUpperCase();
    return h('button', { type: 'button', class: 'job-thumb', title: 'Open this NPC', 'aria-label': `Open ${job.npc_name}`, onclick: safe(() => openNpc(job.npc_id)) },
      row?.thumb ? h('img', { src: `/api/npcs/${job.npc_id}/files/thumb.png?v=${encodeURIComponent(row.updated)}`, alt: '', loading: 'lazy' }) : initials);
  }

  function jobCard(job) {
    const kindName = { scene: 'scene', item: 'item' }[job.target_type] || 'NPC';
    const openTarget = () => job.target_type === 'scene' ? openScene(job.scene_id) : job.target_type === 'item' ? openItem(job.item_id) : openNpc(job.npc_id);
    const current = job.steps.find(step => step.status === 'running');
    const label = { queued: 'Waiting', running: current ? current.label + (current.detail ? ` (${current.detail})` : '') : 'Working',
      done: 'Done', error: 'Finished with errors', cancelled: 'Cancelled' }[job.status];
    return h('div', { class: `job ${job.status}` }, thumb(job), h('div', { class: 'job-main' },
      h('div', { class: 'top' }, h('button', { class: 'btn small ghost name', title: `Open this ${kindName}`,
        onclick: safe(openTarget) }, job.npc_name),
        isActive(job) ? btn('', 'x', safe(() => api('POST', `/api/jobs/${job.id}/cancel`).then(tick)), { cls: 'small icon ghost', title: 'Cancel' })
          : h('span', { class: 'row' },
            ['error', 'cancelled'].includes(job.status) ? btn('Retry', 'refresh', safe(async () => { await api('POST', `/api/jobs/${job.id}/retry`); startPolling(); }), { cls: 'small' }) : null,
            btn('', 'x', () => { S.dismissed.add(job.id); renderQueue(); }, { cls: 'small icon ghost', title: 'Dismiss' }))),
      h('div', { class: 'bar' }, h('i', { style: { width: `${job.total ? (100 * job.done) / job.total : 0}%` } })),
      h('div', { class: 'row', style: { justifyContent: 'space-between' } },
        h('span', { class: 'cap hint', style: { margin: 0 } }, `${label} · ${job.done}/${job.total}`),
        h('span', { class: 'steps' }, job.steps.map(step => h('i', { class: step.status,
          title: `${step.label}: ${step.status}${step.seconds ? ` (${step.seconds}s)` : ''}` })))),
      job.steps.find(step => step.note) ? h('div', { class: 'hint' }, job.steps.find(step => step.note).note) : null,
      job.error ? h('div', { class: 'err' }, job.error) : null));
  }

  function renderQueue() {
    const element = $('#queue'), toggle = $('#queueToggle');
    const list = S.jobs.filter(job => !S.dismissed.has(job.id)).slice(-12).reverse();
    const active = list.filter(isActive).length;
    element.hidden = !list.length || hidden;
    document.body.classList.toggle('queue-open', !element.hidden);
    toggle.hidden = !list.length || !hidden;
    toggle.replaceChildren(...(active ? [h('span', { class: 'spinner small' }), `Queue · ${active} running`] : [`Queue · ${list.length}`]));
    toggle.onclick = () => { setHidden(false); renderQueue(); };
    if (element.hidden) return;
    element.replaceChildren(
      h('header', {}, h('span', {}, active ? `Queue · ${active} active` : 'Queue'),
        h('div', { class: 'row' }, active ? btn('Cancel all', null, safe(() => api('POST', '/api/jobs/cancel-all').then(tick)), { cls: 'small' }) : null,
          btn('Clear', null, () => { S.jobs.filter(job => !isActive(job)).forEach(job => S.dismissed.add(job.id)); renderQueue(); }, { cls: 'small ghost' }),
          btn('Hide', null, () => { setHidden(true); renderQueue(); }, { cls: 'small ghost', title: 'Hide the queue (the Queue button at the top brings it back)' }))),
      ...list.map(jobCard));
  }

  function startPolling() { if (!S.polling) { S.polling = true; tick(); } }

  async function tick() {
    try { handleJobs(await api('GET', '/api/jobs')); } catch { /* status chip reports outages */ }
    if (S.jobs.some(isActive)) setTimeout(tick, 1200); else S.polling = false;
  }

  function handleJobs(jobs) {
    let draftChanged = false; let libraryChanged = false;
    for (const job of jobs) {
      if (S.seen[job.id] !== job.version) {
        S.seen[job.id] = job.version;
        if (job.target_type === 'scene') Promise.resolve(onSceneJobChange(job)).catch(() => {});
        else if (job.target_type === 'item') Promise.resolve(onItemJobChange(job)).catch(() => {});
        else {
          libraryChanged = true; if (S.draft?.id === job.npc_id) draftChanged = true;
          if (onNpcJobChange) Promise.resolve(onNpcJobChange(job)).catch(() => {});
        }
      }
      if (!isActive(job) && !S.notified.has(job.id)) {
        S.notified.add(job.id);
        if (job.status === 'error') toast(`${job.npc_name}: ${job.error}`, 'error');
        else if (job.status === 'done') toast(`${job.npc_name} is ready`);
      }
    }
    S.jobs = jobs;
    renderQueue();
    if (draftChanged) refreshDraftAssets();
    if (libraryChanged) Promise.resolve(refreshLibrary()).then(renderQueue).catch(() => { /* thumbnails catch up next time */ });   // a new portrait = a new thumbnail
  }

  async function refreshDraftAssets() {
    const id = S.draft?.id; if (!id) return;
    try {
      const fresh = await api('GET', `/api/npcs/${id}`);
      // the voice too (takes, tests and voiced lines land there); the dialogue text is left alone: it may have unsaved edits
      if (S.draft?.id === id) { S.draft.assets = fresh.assets; S.draft.voice = fresh.voice; S.draft.voice_lines = fresh.voice_lines; renderGallery(); }
    } catch { /* NPC may have been deleted meanwhile */ }
  }

  return { afterSubmit, renderQueue, startPolling };
}
