// Character voices (npcgen/voice): the Voice card on the character sheet and the Voice designer popup.
//
// The designer edits a copy of the character's voice settings (engine, source, description, voice type, chips, language, stock voice,
// speaking engine) and saves them with PUT /api/npcs/<id>/voice. Takes, "try a line" and mood previews are quick jobs (they run
// between picture jobs, never inside one); their clips appear when the job queue reports the character changed (onDraftChanged).
// The suggested description comes from the character sheet (POST .../voice/suggest); what the model reads is that description plus
// the voice type and chips, composed the same way as npcgen/voice/describe.compose.
import { $, S, api, btn, fullName, h, isActive, safe, toast } from './core.js';
import { conditionsTag, engineNotice } from './voice-engine-notices.js';
import { licenceNote, optionTag } from './licences-view.js';
import { auditionSection } from './voice-audition-view.js';

const SOURCES = [['design', 'Describe'], ['stock', 'Stock voice'], ['upload', 'Upload a recording'], ['copy', 'Copy a character']];
const MAX_UPLOAD_SECONDS = 40, UPLOAD_RATE = 16000;

/** What goes after a voice model's name in a drop-down: the licence list's wording (data/licences.json), or the model's own flag on an older server. */
const engineTag = x => (S.cfg?.licences ? optionTag('voice:' + x.id) : (x.commercial_ok ? '' : ' · non-commercial'));

export function createVoiceDesigner({ afterSubmit, renderGallery, saveDraft, standalone = false, render, refreshLibrary, openNpc, openWorkspace }) {
  let ENG = null;                    // GET /api/voice/engines, fetched once per page load
  let labCatalog;
  let draft = null;                  // the designer's working copy of the voice settings
  let suggestion = null;             // POST .../voice/suggest for the open character
  let tab = 'design';
  let upload = { file: null, text: '', consent: false, speaker: '' };
  let tryText = '', tryMood = 'neutral';
  let selectedNpc = null, loading = false, working = false, search = '', filter = '';
  let savedSettings = '', selectionVersion = 0;
  const sessions = new Map();
  let voiceLibrary = null;
  let voiceStudio = null;
  let voiceLipSync = null;
  let dramaDialog=null,dramaView=null;
  async function openDramaStudio() {
    if(!ENG?.drama_studio_schema)throw new Error('Restart NPC Forge to enable Drama studio.');
    if(!dramaDialog){
      const mount=h('div',{});
      dramaDialog=h('dialog',{class:'drama-studio-dialog','aria-label':'Drama scene and narration studio'},
        btn('Close','',()=>dramaDialog.close(),{cls:'small'}),mount);
      dramaDialog.addEventListener('close',()=>dramaView?.stopListening());
      document.body.append(dramaDialog);dramaDialog.showModal();
      const {mountDramaStudio}=await import('./voice-drama3-studio.js');
      dramaView=await mountDramaStudio(mount,{base:'/api/voice/drama3'});
    }else dramaDialog.showModal();
  }

  async function openLipSync() {
    await loadEngines();
    if (!voiceLipSync) {
      const { createVoiceLipSync } = await import('./voice-lip-sync-view.js');
      voiceLipSync = createVoiceLipSync({ selectedNpc: npc, onSaved: applyNpc, engineInfo: () => ENG });
    }
    await voiceLipSync.open();
  }

  async function openVoiceStudio(section) {
    await loadEngines();
    if (!voiceStudio) {
      const { createVoiceStudio } = await import('./voice-studio-view.js');
      voiceStudio = createVoiceStudio({ selectedNpc: npc, onSaved: applyNpc, engineInfo: () => ENG });
    }
    voiceStudio.open(section);
  }

  const npc = () => standalone ? selectedNpc : S.draft;
  const voice = () => npc()?.voice || {};
  const presetVoice = v => v.engine === 'qwen_custom' && v.mode === 'stock' && v.preset_identity;
  const assigned = v => !!(v.lab_voice || v.reference || presetVoice(v));
  const identityId = v => v.lab_voice?.identity_id || v.lab_voice?.voice_id || v.lab_voice?.name || presetVoice(v)?.id || v.reference?.id;
  const engine = id => ENG?.engines.find(e => e.id === id);
  const presets = () => ENG.presets;
  const url = meta => `/api/npcs/${npc().id}/files/${meta.file}?v=${encodeURIComponent(meta.created || '')}`;
  const player = (meta, label) => h('audio', { controls: true, preload: 'none', src: url(meta), 'aria-label': label || 'Play' });
  // Hold the selected character steady for an action, including asynchronous audio decoding.
  const act = fn => safe(async (...args) => {
    if (!standalone) return fn(...args);
    if (working || loading) return;
    working = true; paint();
    try { return await fn(...args); } finally { working = false; paint(); }
  });

  async function loadEngines(fresh = false) {
    if (!ENG || fresh) {
      ENG = await api('GET', '/api/voice/engines');
      if (ENG.lab_api) await loadLab();
    }
    return ENG;
  }

  async function loadLab() {
    labCatalog = await api('GET', '/api/voice/lab').catch(error => ({ connected: false, message: error.message }));
  }

  async function makeLabVoice(request) {
    const res = await api('POST', `/api/npcs/${npc().id}/voice/lab`, request);
    afterSubmit({ jobs: [res.job] });
    toast('Voice Lab voice queued');
  }

  function labControls(n, v, busy) {
    if (!labCatalog) return null;
    const refresh = btn('Refresh Voice Lab', 'refresh', act(async () => { await loadLab(); paint(); }), { cls: 'small' });
    if (!labCatalog.connected) return h('section', { class: 'card stack' }, h('h3', {}, 'Voice Lab'),
      h('p', {}, labCatalog.message), h('p', {}, h('a', { href: 'https://github.com/Fel101/fels-voice-lab/releases' }, 'Get Voice Lab')), refresh);

    const drafts = labCatalog.voices || [];
    const choices = h('select', { 'aria-label': 'Voice Lab voice' }, h('option', { value: '' }, 'Choose a voice'),
      ...drafts.map(row => h('option', { value: row.name, disabled: row.ready !== true },
        `${row.name} · ${row.model_name || row.model}${row.ready ? '' : ` · ${row.why_not || 'Unavailable'}`}`)));
    choices.value = v.lab_voice?.name || '';
    const locked = !!busy.voice_lab_create;
    const description = h('textarea', { id: 'labVoiceDescription', rows: 3, maxLength: 1000,
      value: draft.labDescription ?? (compose(draft) || suggestion?.description || ''), 'aria-label': 'Voice Lab description',
      oninput: event => { draft.labDescription = event.target.value; } });
    const recorded = h('details', {}, h('summary', {}, 'Use a recording'), (() => {
      const hasSaved = !!(v.reference?.file && v.reference?.text);
      const file = h('input', { type: 'file', accept: 'audio/*', 'aria-label': 'Recording to send to Voice Lab' });
      const transcript = h('textarea', { rows: 2, maxLength: 600, 'aria-label': 'Exact words of the new recording' });
      const origin = h('input', { type: 'text', 'aria-label': 'Recording origin' });
      const allowedUse = h('input', { type: 'text', 'aria-label': 'Recording permission terms' });
      const consent = h('input', { type: 'checkbox' });
      const cloner = h('select', { 'aria-label': 'Model for the recording' },
        h('option', { value: 'qwen3-tts-1.7b-base' }, 'Qwen3-TTS 1.7B Base'),
        h('option', { value: 'turbo' }, 'Chatterbox Turbo'), h('option', { value: 'voxcpm2' }, 'VoxCPM2'));
      return h('div', { class: 'stack' }, h('label', {}, 'Choose an audio file', file),
        hasSaved ? h('p', { class: 'hint' }, 'Leave the file blank to use the saved recording and its transcript.') : null,
        h('label', {}, 'Exact words of the new recording', transcript), h('label', {}, 'Who made the recording?', origin),
        h('label', {}, 'How may the recording be used?', allowedUse), h('label', {}, 'Voice Lab model', cloner),
        h('label', { class: 'check' }, consent, 'I have permission to use this recording in Voice Lab.'),
        btn('Make the voice from this recording', 'zap', act(async () => {
          if (!consent.checked) throw new Error('Confirm that you may use the recording in Voice Lab.');
          const upload = file.files[0];
          if (!upload && !hasSaved) throw new Error('Choose a recording first.');
          const request = { source: 'recording', origin: origin.value, allowed_use: allowedUse.value, consent: true, engine: cloner.value };
          if (upload) {
            request.audio = await toWavDataUrl(upload);
            request.transcript = transcript.value;
          }
          await makeLabVoice(request);
        }), { cls: 'small', disabled: locked }));
    })());
    return h('section', { class: 'card stack' }, h('h3', {}, 'Voice Lab'),
      v.lab_voice ? h('p', {}, `New speech uses ${v.lab_voice.name} · ${v.lab_voice.label}`) : null,
      h('div', { class: 'row wrap' }, choices, btn('Use this voice', '', act(async () => {
        if (!choices.value) throw new Error('Choose a Voice Lab voice first.');
        applyNpc(await api('PUT', `/api/npcs/${n.id}/voice/lab`, { name: choices.value }));
        toast('Voice Lab voice assigned');
      }), { cls: 'small', disabled: locked }), refresh),
      h('label', {}, 'Describe a voice to make in Voice Lab', description),
      btn('Make this voice in Voice Lab', 'zap', act(() => makeLabVoice({ source: 'description', description: description.value })),
        { cls: 'small', disabled: locked }), recorded);
  }

  /** What a design model reads: the accent chip first, then the description, the voice type and the other chips. With an
   *  accent picked, the preset's accent_free text and a description without the setting's accent cue (npcgen/voice/describe.compose). */
  function compose(d) {
    const p = presets(), picked = [];
    for (const g of p.chip_groups) { const c = g.chips.find(x => x.id === d.chips?.[g.id]); if (c) picked.push([g.id, c.text]); }
    const accent = (picked.find(([id]) => id === 'accent') || [])[1] || '';
    let desc = (d.description || '').replace(/\s+/g, ' ').trim();
    if (accent) for (const s of p.setting_accents || []) { const cue = p.setting_voice?.[s]; if (cue) desc = desc.split(cue).join('').replace(/\s+/g, ' ').trim(); }
    const parts = [accent, desc];
    const vt = p.voice_types.find(v => v.id === d.voice_type);
    if (vt) parts.push(accent && vt.accent_free ? vt.accent_free : vt.text);
    for (const [id, text] of picked) if (id !== 'accent') parts.push(text);
    let out = '';
    for (const part of parts.filter(Boolean)) if (!out || out.length + 1 + part.length <= 700) out = (out + ' ' + part).trim();
    return out.slice(0, 700).trim();
  }

  /** The voice steps of this character's queued or running jobs: {kind: label} */
  function busyVoice() {
    const out = {};
    for (const j of S.jobs) {
      if (j.npc_id !== npc()?.id || !isActive(j)) continue;
      for (const s of j.steps) if (s.kind.startsWith('voice_') && ['pending', 'running'].includes(s.status))
        out[s.kind] = (out[s.kind] && out[s.kind] !== 'Queued') ? out[s.kind] : (s.status === 'running' ? s.label + '…' : 'Queued');
    }
    return out;
  }

  // ---- the Voice card on the sheet -----------------------------------------------------------------------------------------
  function voiceCard() {
    const n = npc();
    if (!n?.id) return h('section', { class: 'card' }, h('h3', {}, 'Voice'), h('p', { class: 'hint' }, 'Save this NPC first to give them a voice.'));
    const v = voice(), ref = v.reference, busy = busyVoice(), running = Object.values(busy)[0];
    const e = ENG && engine(v.engine), pending = v.review === 'pending';
    const lines = n.dialogue?.lines ? Object.values(n.dialogue.lines).flat() : [];
    const badges = [];
    if (assigned(v)) badges.push(h('span', { class: 'badge accent' }, v.lab_voice?.label || e?.label || v.engine));
    if (pending) badges.push(h('span', { class: 'badge warn', title: 'The first take is used until you pick one' }, 'take to review'));
    const defaultEffects = v.studio?.presets?.find(p => p.id === v.studio.default_performance)?.effects ?? v.studio?.effects;
    if (defaultEffects?.enabled) badges.push(h('span', { class: 'badge' }, 'delivery effects enabled'));
    if (v.commercial_ok === false) badges.push(h('span', { class: 'badge warn', title: 'Made or spoken with a non-commercial model (see NOTICE.md)' }, 'non-commercial'));
    if (v.sheet_changed) badges.push(h('span', { class: 'badge', title: 'The character sheet changed after this voice was made. Your voice is kept; Suggest again in the designer if you want a new one.' }, 'sheet changed'));
    const source = { design: 'Designed from a description', stock: `Stock voice ${v.stock_voice || ''}`, upload: 'From your recording',
      copy: 'Copied from another character' }[v.mode] || '';
    const migrate = labCatalog && !v.lab_voice && assigned(v);
    return h('section', { class: 'card voice-card' }, h('h3', {}, 'Voice'),
      running ? h('div', { class: 'notice' }, h('div', { class: 'spinner' }), running) : null,
      v.lab_voice ? h('p', {}, `${v.lab_voice.name} · Voice Lab`) : null,
      ref ? h('div', { class: 'voice-now' }, player(ref, `${fullName(n)}'s voice`),
        h('div', { class: 'clip-head' }, ...badges), h('p', { class: 'hint' }, [source, v.mode === 'design' ? v.description : ''].filter(Boolean).join(': ').slice(0, 220)))
        : presetVoice(v) ? h('p', {}, `${v.stock_voice} · preset identity · no cloning reference needed`)
          : !v.lab_voice ? h('p', { class: 'hint' }, v.takes?.length ? 'Takes are waiting: open the designer and pick one.' : 'No voice yet. The designer suggests one from the character sheet.') : null,
      pending && v.takes?.length ? quickTakes(v) : null,
      h('div', { class: 'chips', style: { marginTop: '10px' } },
        btn(migrate ? 'Make this voice in Voice Lab' : assigned(v) ? 'Voice designer…' : 'Design a voice…', 'zap',
          safe(async () => {
            if (migrate && labCatalog.connected && v.mode === 'design' && v.description?.trim()) {
              await makeLabVoice({ source: 'description' });
            } else await openDesigner();
          }), { cls: 'small', disabled: !!busy.voice_lab_create }),
        assigned(v) && ENG?.effects_schema ? btn('Voice effects', '', safe(() => openVoiceStudio('effects')), { cls: 'small' }) : null,
        assigned(v) && ENG?.alignment_schema ? btn('Lip sync', '', safe(openLipSync), { cls: 'small' }) : null,
        assigned(v) && lines.length ? btn('Voice the dialogue', 'refresh', safe(() => voiceLines()), { cls: 'small', disabled: !!busy.voice_line,
          title: 'Speak every dialogue line in this voice (the Dialogue pack card plays them)' }) : null,
        assigned(v) || v.takes?.length ? btn('', 'trash', safe(removeVoice), { cls: 'small icon ghost danger', title: 'Delete the voice, its takes and voiced lines' }) : null),
      h('p', { class: 'hint' }, 'A voice of their own for every character: suggested from the sheet, then yours to change. Dialogue lines are spoken by cloning the chosen voice, so they always sound like the same person.'));
  }

  function quickTakes(v) {
    return h('div', { class: 'take-list compact' }, v.takes.slice(-4).map(t => takeRow(t, v, true)));
  }

  function takeRow(t, v, compact = false) {
    const inUse = v.chosen_take === t.id;
    const e = ENG && engine(t.engine);
    return h('div', { class: 'take' + (inUse ? ' in-use' : '') },
      player(t, `Take ${t.n}`),
      h('span', { class: 'cap' }, [`#${t.n}`, t.stock_voice || (e ? e.label : t.engine), t.seconds ? `${t.seconds}s` : null].filter(Boolean).join(' · ')),
      inUse ? h('span', { class: 'badge ok' }, v.review === 'pending' ? 'in use (not reviewed)' : 'in use')
        : btn(standalone ? 'Assign to character' : 'Use this voice', '', act(() => selectTake(t.id)), { cls: 'small' }),
      inUse && v.review === 'pending' ? btn(standalone ? 'Confirm this voice' : 'Keep it', '', act(() => selectTake(t.id)), { cls: 'small', title: 'Mark this take as reviewed' }) : null,
      v.reference?.file ? btn('Compare with reference', '', act(() => checkVoiceMatch(t.id)), { cls: 'small',
        title: 'Proxy score against the chosen voice reference. A design candidate can differ from it; a rough guide, not a listening test' }) : null,
      compact ? null : btn('', 'trash', act(() => deleteTake(t.id)), { cls: 'small icon ghost danger', title: 'Delete this take',
        disabled: inUse || (v.comparisons || []).some(s => s.candidates.some(c => c.id === t.id)) }));
  }

  async function checkVoiceMatch(takeId) {
    const m = await api('GET', `/api/npcs/${npc().id}/voice/takes/${takeId}/match`);
    toast(m.available ? `Voice match (proxy) ${m.score.toFixed(2)}: ${m.meaning}. ${m.note}` : m.reason);
  }

  // ---- actions --------------------------------------------------------------------------------------------------------------
  async function ensureSaved() {
    if (standalone) {
      if (!npc()?.id) throw new Error('Choose a saved character first.');
    } else if (!npc()?.id || S.dirty || S.pendingPhoto) await saveDraft();
  }

  function applyNpc(fresh) {
    if (standalone) selectedNpc = fresh;
    if (S.draft?.id === fresh.id) {
      S.draft.voice = fresh.voice; S.draft.voice_lines = fresh.voice_lines; S.draft.assets = fresh.assets;
    }
    if (standalone) Promise.resolve(refreshLibrary()).then(paint).catch(() => {});
    renderGallery(); paint();
  }

  async function saveSettings(quiet = false) {
    await ensureSaved();
    const fresh = await api('PUT', `/api/npcs/${npc().id}/voice`, settingsBody());
    savedSettings = JSON.stringify(settingsBody());
    applyNpc(fresh);
    if (!quiet) toast('Voice settings saved');
  }

  function settingsBody() {
    const d = draft;
    return { engine: d.engine, mode: d.mode, speaker: d.speaker || null, description: d.description, voice_type: d.voice_type || null,
      chips: d.chips, language: d.language, stock_voice: d.stock_voice || null, settings: d.settings, script: d.script || '',
      edited: !!d.edited, auto_description: d.auto_description || '' };
  }

  async function makeTakes() {
    await ensureSaved();
    const res = await api('POST', `/api/npcs/${npc().id}/voice/takes`, { count: draft.count || 3, settings: settingsBody() });
    savedSettings = JSON.stringify(settingsBody());
    afterSubmit({ jobs: [res.job] });
    toast(`Making ${draft.count || 3} take${(draft.count || 3) === 1 ? '' : 's'}: they appear here as they finish.`);
    paint();
  }

  async function speak(text, mood, preview = false) {
    await ensureSaved();
    if (!preview && !voice().lab_voice) await saveSettings(true);
    const res = await api('POST', `/api/npcs/${npc().id}/voice/test`, { text, mood, preview });
    afterSubmit({ jobs: [res.job] });
    paint();
  }

  async function selectTake(id) {
    const fresh = await api('POST', `/api/npcs/${npc().id}/voice/takes/${id}/select`);
    if (draft) {                     // the voice is now that take, model and source included: the designer follows it
      Object.assign(draft, { engine: fresh.voice.engine, mode: fresh.voice.mode, stock_voice: fresh.voice.stock_voice || draft.stock_voice });
      if (tab === 'design' || tab === 'stock') tab = draft.mode;
    }
    applyNpc(fresh);
    toast('Voice chosen');
  }

  async function deleteTake(id) {
    applyNpc(await api('DELETE', `/api/npcs/${npc().id}/voice/takes/${id}`));
  }

  async function voiceLines(ids, moods) {
    if (!standalone && S.dialogueDirty) throw new Error('Save the dialogue lines first: the saved text is what gets voiced.');
    const res = await api('POST', `/api/npcs/${npc().id}/voice/lines`, { ...(ids ? { ids } : {}), ...(moods ? { moods } : {}) });
    afterSubmit({ jobs: [res.job] });
    toast(`Voicing ${res.job.total} line${res.job.total === 1 ? '' : 's'}`);
  }

  // ---- voiced dialogue lines (the Dialogue pack card) ------------------------------------------------------------------------
  const lineMoods = {};              // line id -> the mood picked before voicing it
  const MOODS = ['neutral', 'happy', 'excited', 'sad', 'angry', 'surprised', 'scared', 'whisper', 'flirty', 'tired'];

  const hasVoice = () => !!(voice().lab_voice || voice().reference) || (voice().mode === 'stock' && !!voice().stock_voice);

  /** 'none' | 'ok' | 'stale' (the text changed, or the voice changed, since the line was voiced; npcgen/voice/record.line_is_stale) */
  function lineState(line) {
    const meta = npc()?.voice_lines?.[line.id];
    if (!meta) return 'none';
    const ref = identityId(voice());
    return meta.stale || meta.text !== line.text || (ref && meta.reference !== ref) ? 'stale' : 'ok';
  }

  function lineBusy(id) {
    for (const j of S.jobs) {
      if (j.npc_id !== npc()?.id || !isActive(j)) continue;
      const s = j.steps.find(x => x.kind === 'voice_line' && x.key === id && ['pending', 'running'].includes(x.status));
      if (s) return s.status === 'running' ? 'Voicing…' : 'Queued';
    }
    return '';
  }

  /** Under each dialogue line: its voiced clip, whether it is out of date, a mood, and Voice / Redo. */
  function lineControls(line) {
    const n = npc();
    if (!n?.id) return null;
    const meta = n.voice_lines?.[line.id], state = lineState(line), busy = lineBusy(line.id);
    const adult = n.profile?.age == null || Number(n.profile.age) >= 18;
    const moodEnabled=engine(voice().speaker || voice().engine)?.caps.direction===true;
    const mood = h('select', { class: 'line-mood', 'aria-label': 'Mood for this line', disabled:!moodEnabled,
      title:moodEnabled?'':'This voice model does not apply text mood direction.', onchange: e => { lineMoods[line.id] = e.target.value; } },
      MOODS.filter(m => m !== 'flirty' || adult).map(m => h('option', { value: m }, m)));
    mood.value = lineMoods[line.id] || meta?.mood || 'neutral';
    const studio = voice().studio || {}, performances = studio.presets || [];
    const performance = h('select', { 'aria-label': 'Performance for this line', onchange: act(async e => {
      const studio = structuredClone(voice().studio || { pronunciations: [], presets: [], default_performance: '', line_performances: {} });
      studio.line_performances ||= {};
      if (e.target.value === '__default') delete studio.line_performances[line.id];
      else studio.line_performances[line.id] = e.target.value;
      applyNpc(await api('PUT', `/api/npcs/${n.id}/voice/studio`, studio));
    }) }, h('option', { value: '__default' }, 'Character default'), h('option', { value: '' }, 'Voice settings'),
      performances.map(p => h('option', { value: p.id }, p.name)));
    performance.value = studio.line_performances?.[line.id] ?? '__default';
    return h('div', { class: 'line-voice' },
      meta ? h('audio', { controls: true, preload: 'none', src: url(meta), 'aria-label': `Voiced: ${line.text}` }) : null,
      state === 'stale' ? h('span', { class: 'badge warn', title: 'The line or the voice changed after this was voiced' }, 'outdated') : null,
      busy ? h('span', { class: 'cap hint' }, busy) : null,
      mood,
      ENG?.studio_schema && performances.length ? performance : null,
      meta?.synthesis_text && meta.synthesis_text !== line.text ? h('span', { class: 'cap hint', title: meta.synthesis_text }, 'Pronunciation applied') : null,
      meta?.postprocessing?.backend === 'ffmpeg' ? h('span', { class: 'badge' }, 'Vocal effects applied') : null,
      meta?.original ? h('details', {}, h('summary', {}, 'Original speech'), player(meta.original, `Original: ${line.text}`)) : null,
      meta?.lip_sync?.status === 'amplitude_fallback' ? h('span', { class: 'cap hint', title: meta.lip_sync.reason }, 'Amplitude mouth track') : null,
      meta?.lip_sync_stale ? h('span', { class: 'badge warn' }, 'Lip-sync settings changed') : null,
      meta?.lip_sync?.status === 'aligned' ? h('span', { class: 'badge' }, 'Word / phoneme timing') : null,
      btn(meta ? 'Redo' : 'Voice', '', act(() => voiceLines([line.id], { [line.id]: mood.value })), { cls: 'small',
        disabled: !hasVoice() || !!busy, title: hasVoice() ? 'Speak this line in the character\'s voice' : 'Give the character a voice first (Voice card)' }));
  }

  /** The Dialogue pack card's voice buttons: voice every line that is missing or out of date, and the Voice pack download. */
  function dialogueVoiceActions(pack) {
    const n = npc();
    if (!n?.id || !pack) return null;
    const lines = Object.values(pack.lines || {}).flat();
    const todo = lines.filter(l => lineState(l) !== 'ok');
    const voiced = lines.length - todo.length;
    return h('div', { class: 'row wrap' },
      btn(!todo.length ? 'All lines voiced' : todo.length === lines.length ? 'Voice all lines' : `Voice ${todo.length} missing or outdated`, 'zap',
        safe(() => voiceLines(todo.map(l => l.id), Object.fromEntries(todo.filter(l => lineMoods[l.id]).map(l => [l.id, lineMoods[l.id]])))),
        { cls: 'small', disabled: !hasVoice() || !todo.length, title: hasVoice() ? '' : 'Give the character a voice first (Voice card)' }),
      assigned(voice()) ? h('a', { class: 'btn small ghost', href: `/api/npcs/${n.id}/voice-pack.zip`, download: '',
        title: 'The voice and every up-to-date voiced line as WAV files, with lines.json (text, mood, lip-sync track) and the dialogue files' },
        `Voice pack (${voiced} line${voiced === 1 ? '' : 's'})`) : null);
  }

  async function removeVoice() {
    if (!confirm('Delete this voice, its takes and every voiced line?')) return;
    applyNpc(await api('DELETE', `/api/npcs/${npc().id}/assets/voice`));
  }

  async function suggest(replace) {
    const n = npc();
    suggestion = await api('POST', `/api/npcs/${n.id}/voice/suggest`, { npc: { profile: n.profile, setting: n.setting, style: n.style, morphology: n.morphology } });
    if (replace) { draft.description = suggestion.description; draft.auto_description = suggestion.description; draft.edited = false; }
    if (!draft.stock_voice) draft.stock_voice = suggestion.stock[0];
  }

  // ---- upload: any clip the browser can decode -> 16 kHz mono 16-bit WAV (40 s fits the 2 MB request limit) ------------------------
  async function toWavDataUrl(file) {
    const Ctx = window.AudioContext || window.webkitAudioContext;
    const ctx = new Ctx();
    let decoded;
    try { decoded = await ctx.decodeAudioData(await file.arrayBuffer()); } catch { throw new Error('This file could not be read as audio: choose another recording.'); } finally { ctx.close(); }
    if (decoded.duration > MAX_UPLOAD_SECONDS) throw new Error(`The recording is ${Math.round(decoded.duration)} s long: trim it to ${MAX_UPLOAD_SECONDS} s or less.`);
    if (decoded.duration < 3) throw new Error('The recording is too short: 3 seconds or more of clear speech is needed.');
    const off = new OfflineAudioContext(1, Math.ceil(decoded.duration * UPLOAD_RATE), UPLOAD_RATE);
    const src = off.createBufferSource(); src.buffer = decoded; src.connect(off.destination); src.start(0);
    const pcm = (await off.startRendering()).getChannelData(0);
    const bytes = new DataView(new ArrayBuffer(44 + pcm.length * 2));
    const str = (o, s) => [...s].forEach((c, i) => bytes.setUint8(o + i, c.charCodeAt(0)));
    str(0, 'RIFF'); bytes.setUint32(4, 36 + pcm.length * 2, true); str(8, 'WAVE'); str(12, 'fmt ');
    bytes.setUint32(16, 16, true); bytes.setUint16(20, 1, true); bytes.setUint16(22, 1, true); bytes.setUint32(24, UPLOAD_RATE, true);
    bytes.setUint32(28, UPLOAD_RATE * 2, true); bytes.setUint16(32, 2, true); bytes.setUint16(34, 16, true); str(36, 'data'); bytes.setUint32(40, pcm.length * 2, true);
    for (let i = 0; i < pcm.length; i++) bytes.setInt16(44 + i * 2, Math.max(-1, Math.min(1, pcm[i])) * 32767, true);
    let binary = ''; const u8 = new Uint8Array(bytes.buffer);
    for (let i = 0; i < u8.length; i += 0x8000) binary += String.fromCharCode(...u8.subarray(i, i + 0x8000));
    return 'data:audio/wav;base64,' + btoa(binary);
  }

  async function sendUpload() {
    if (!upload.file) throw new Error('Choose a recording first.');
    await ensureSaved();
    const audio = await toWavDataUrl(upload.file);
    applyNpc(await api('POST', `/api/npcs/${npc().id}/voice/upload`, { audio, text: upload.text, consent: upload.consent, speaker: upload.speaker || null }));
    if (standalone) adoptAssignedVoice();
    toast('The recording is now this character\'s voice');
  }

  async function copyFrom(id) {
    await ensureSaved();
    applyNpc(await api('POST', `/api/npcs/${npc().id}/voice/copy`, { from: id }));
    if (standalone) adoptAssignedVoice();
    toast('Voice copied');
  }

  // ---- the popup ------------------------------------------------------------------------------------------------------------
  async function openDesigner() {
    await ensureSaved();
    if (openWorkspace) return openWorkspace(npc().id);
    await loadEngines(true);
    const v = voice();
    draft = { engine: v.engine || ENG.default, mode: v.mode || 'design', speaker: v.speaker || '', description: v.description || '',
      voice_type: v.voice_type || '', chips: { ...(v.chips || {}) }, language: v.language || 'English', stock_voice: v.stock_voice || '',
      settings: { temperature: 0.9, cfg: 4, speed: 1, ...(v.settings || {}) }, script: v.script || '', edited: !!v.edited,
      auto_description: v.auto_description || '', count: 3 };
    await suggest(!draft.description);
    tab = draft.mode;
    const greeting = npc().dialogue?.lines?.greeting?.[0]?.text;
    tryText = tryText || greeting || '';
    let dlg = $('#voiceDesigner');
    if (!dlg) {
      dlg = h('dialog', { id: 'voiceDesigner', class: 'about advanced voice-designer', 'aria-labelledby': 'voiceTitle',
        onclick: e => { if (e.target === e.currentTarget) close(); } });
      document.body.append(dlg);
    }
    paint();
    if (!dlg.open) dlg.showModal();
  }

  function close() { $('#voiceDesigner')?.close(); renderGallery(); }

  /** Called when the job queue says this character changed (takes and tests finished): repaint what is open. */
  function onDraftChanged() { if ($('#voiceDesigner')?.open) paint(); }

  function rememberSession() {
    if (selectedNpc && draft) sessions.set(selectedNpc.id, { draft, suggestion, upload, tryText, tryMood, tab, savedSettings });
  }

  function adoptAssignedVoice() {
    const v = voice();
    Object.assign(draft, { engine: v.engine, mode: v.mode, speaker: v.speaker || '', language: v.language || 'English',
      description: v.description || '', voice_type: v.voice_type || '', chips: { ...(v.chips || {}) },
      settings: { temperature: 0.9, cfg: 4, speed: 1, ...(v.settings || {}) }, stock_voice: v.stock_voice || '',
      script: v.script || '', edited: !!v.edited, auto_description: v.auto_description || '' });
    savedSettings = JSON.stringify(settingsBody());
    paint();
  }

  async function selectCharacter(id, force = false) {
    if (working || loading || (!force && id === selectedNpc?.id)) return;
    rememberSession();
    const version = ++selectionVersion;
    loading = true; paint();
    try {
      // Don't replace Create's draft: it may contain an unfinished character or dialogue edit.
      const fresh = await api('GET', `/api/npcs/${id}`);
      const v = fresh.voice || {};
      const cached = sessions.get(id);
      const nextDraft = cached?.draft || {
        engine: v.engine || ENG.default, mode: v.mode || 'design', speaker: v.speaker || '', description: v.description || '',
        voice_type: v.voice_type || '', chips: { ...(v.chips || {}) }, language: v.language || 'English', stock_voice: v.stock_voice || '',
        settings: { temperature: 0.9, cfg: 4, speed: 1, ...(v.settings || {}) }, script: v.script || '', edited: !!v.edited,
        auto_description: v.auto_description || '', count: 3 };
      const nextSuggestion = await api('POST', `/api/npcs/${id}/voice/suggest`, { npc: {
        profile: fresh.profile, setting: fresh.setting, style: fresh.style, morphology: fresh.morphology } });
      if (version !== selectionVersion) return;
      selectedNpc = fresh; draft = nextDraft; suggestion = nextSuggestion;
      upload = cached?.upload || { file: null, text: '', consent: false, speaker: '' };
      tryText = cached?.tryText ?? fresh.dialogue?.lines?.greeting?.[0]?.text ?? '';
      tryMood = cached?.tryMood || 'neutral'; tab = cached?.tab || draft.mode;
      savedSettings = cached?.savedSettings || JSON.stringify(settingsBody());
      if (!cached && draft.mode === 'design' && !draft.description) {
        draft.description = suggestion.description; draft.auto_description = suggestion.description; draft.edited = false;
      }
      if (!draft.stock_voice && draft.mode === 'stock') draft.stock_voice = suggestion.stock[0];
    } finally { loading = false; paint(); }
  }

  async function openTab(id) {
    await loadEngines(true);
    await refreshLibrary();
    S.view = 'voices'; render();
    const wanted = id || selectedNpc?.id || S.draft?.id || S.library[0]?.id;
    if (wanted && S.library.some(n => n.id === wanted)) await selectCharacter(wanted, true);
  }

  async function onJobChange(job) {
    if (job.npc_id !== selectedNpc?.id) { paintRoster(); return; }
    const id = selectedNpc.id;
    const fresh = await api('GET', `/api/npcs/${id}`);
    if (selectedNpc?.id === id) {
      selectedNpc = fresh;
      // Preserve the editor's unsaved controls while new takes arrive.
      paint();
    }
  }

  function paintRoster() {
    const list = $('#voiceRoster'); if (!list) return;
    const rows = S.library.filter(n => (!filter || n.voice === filter || (filter === 'none' && (!n.voice || n.voice === 'none')))
      && `${n.name} ${n.occupation || ''} ${n.setting}`.toLowerCase().includes(search.toLowerCase()));
    list.replaceChildren(...(rows.length ? rows.map(n => h('button', { type: 'button', class: 'voice-person' + (selectedNpc?.id === n.id ? ' active' : ''),
      'aria-pressed': String(selectedNpc?.id === n.id), disabled: loading || working,
      onclick: safe(() => selectCharacter(n.id)) },
      n.thumb ? h('img', { src: `/api/npcs/${n.id}/files/thumb.png?v=${encodeURIComponent(n.updated || '')}`, alt: '', loading: 'lazy' })
        : h('span', { class: 'voice-initial' }, (n.name || '?').slice(0, 1)),
      h('span', { class: 'grow' }, h('strong', {}, n.name), h('span', { class: 'hint' }, n.occupation || n.setting)),
      h('span', { class: 'badge' }, { picked: 'Assigned', review: 'Review' }[n.voice] || 'No voice')))
      : [h('p', { class: 'hint' }, S.library.length ? 'No matching characters.' : 'Save a character in Create to design their voice.')]));
  }

  function renderWorkspace() {
    const q = h('input', { type: 'text', value: search, placeholder: 'Search characters…', 'aria-label': 'Search voice characters',
      oninput: e => { search = e.target.value; paintRoster(); } });
    const status = h('select', { 'aria-label': 'Filter characters by voice', onchange: e => { filter = e.target.value; paintRoster(); } },
      [['', 'All characters'], ['picked', 'Assigned voices'], ['review', 'Voices to review'], ['none', 'No voice']].map(([value, label]) => h('option', { value }, label)));
    status.value = filter;
    $('#view').replaceChildren(h('div', { class: 'voices-page' },
      h('header', { class: 'voice-page-head' }, h('div', {}, h('h1', {}, 'Voice Designer'),
        h('p', { class: 'hint' }, 'Find a voice, audition it, and give your character someone to sound like.')),
        h('div', { class: 'row wrap' }, btn('Voice library', '', act(() => openVoiceLibrary()), { cls: 'small', disabled: !ENG?.library_schema }),
          btn('Drama studio','',safe(openDramaStudio),{cls:'small',disabled:!ENG?.drama_studio_schema}),
          btn('Refresh characters', 'refresh', safe(async () => { await refreshLibrary(); paintRoster(); }), { cls: 'small' }),
          h('button', { type: 'button', class: 'btn small', id: 'voiceReviewButton', onclick: safe(openReview),
            disabled: !S.library.some(n => n.voice === 'review') }, 'Review voices'))),
      h('div', { class: 'voice-workspace' },
        h('aside', { class: 'voice-roster card', 'aria-label': 'Characters and voices' }, h('h3', {}, 'Characters and voices'), q, status,
          h('div', { id: 'voiceRoster', class: 'voice-roster-list' })),
        h('div', { id: 'voiceEditor', class: 'voice-editor', oninput: updateSettingsStatus, onchange: updateSettingsStatus }))));
    paint();
  }

  function updateSettingsStatus() {
    const badge = $('#voiceSettingsStatus');
    if (badge && draft) badge.textContent = JSON.stringify(settingsBody()) !== savedSettings ? 'Unsaved voice settings' : 'Voice settings saved';
  }

  async function openVoiceLibrary(saveCurrent = false) {
    if (!ENG.library_schema) throw new Error('Restart the NPC Forge server to enable the new voice library APIs.');
    if (saveCurrent) await saveSettings(true);
    if (!voiceLibrary) {
      const { createVoiceLibrary } = await import('./voice-library-view.js');
      voiceLibrary = createVoiceLibrary({ selectedNpc: npc, engineInfo: () => ENG, onAssigned: async updated => {
        const current = updated.find(n => n.id === selectedNpc?.id);
        if (current) { applyNpc(current); adoptAssignedVoice(); }
        await refreshLibrary(); paint();
      } });
    }
    return voiceLibrary.open({ saveCurrent });
  }

  async function restoreReference(id) {
    const fresh = await api('POST', `/api/npcs/${npc().id}/voice/references/${id}/select`);
    applyNpc(fresh); adoptAssignedVoice(); toast('Reference version restored.');
  }

  function paintWorkspace() {
    if (S.view !== 'voices') return;
    paintRoster();
    const reviewButton = $('#voiceReviewButton');
    if (reviewButton) reviewButton.disabled = working || loading || !S.library.some(n => n.voice === 'review');
    const editor = $('#voiceEditor'); if (!editor) return;
    if (!selectedNpc || !draft || !ENG) {
      editor.replaceChildren(h('section', { class: 'card empty' }, h('h2', {}, loading ? 'Loading character…' : 'Choose a character'),
        h('p', {}, 'Select an NPC from the list to describe, clone, or audition their voice.'))); return;
    }
    const n = selectedNpc, v = voice(), busy = busyVoice(), e = engine(draft.engine);
    const focused = editor.ownerDocument?.activeElement;
    const restoreFocus = focused?.id && editor.contains(focused) ? {
      id: focused.id, start: focused.selectionStart, end: focused.selectionEnd } : null;
    const allowed = { design: e.caps.design, stock: e.caps.stock, upload: true, copy: true };
    const sources = h('div', { class: 'seg voice-sources', role: 'group', 'aria-label': 'Voice source' }, SOURCES.map(([id, label]) =>
      h('button', { type: 'button', 'aria-pressed': String(tab === id), disabled: !allowed[id], title: allowed[id] ? '' : `${e.label} cannot do this`,
        onclick: () => { tab = id; if (id === 'design' || id === 'stock') draft.mode = id; paint(); } }, label)));
    const body = { design: describeTab, stock: stockTab, upload: uploadTab, copy: copyTab }[tab]();
    const lines = Object.values(n.dialogue?.lines || {}).flat();
    const changed = JSON.stringify(settingsBody()) !== savedSettings;
    const current = h('section', { class: 'card stack' }, h('h3', {}, 'Assigned voice'),
      v.lab_voice ? h('p', {}, `${v.lab_voice.name} · Voice Lab`) : v.reference ? player(v.reference, `${fullName(n)}'s assigned voice`) : presetVoice(v)
        ? h('p', {}, `${v.stock_voice} · reference-free preset identity`) : h('p', { class: 'hint' }, 'No voice assigned yet. Generate candidates and choose a take.'),
      assigned(v) ? h('span', { class: 'badge ' + (v.review === 'pending' ? 'warn' : 'ok') }, v.review === 'pending' ? 'First take in use · awaiting review' : 'Assigned to character') : null,
      v.commercial_ok === false ? h('span', { class: 'badge warn' }, 'Non-commercial') : null,
      v.library_binding ? h('span', { class: 'badge' }, `Library version ${v.library_binding.number} · pinned`) : null,
      assigned(v) ? btn('Save voice to library', 'save', act(() => openVoiceLibrary(true)),
        { cls: 'small', disabled: !ENG.library_schema, title: ENG.library_schema ? '' : 'Restart Forge to enable voice library APIs' }) : null,
      btn('Browse reusable voices', '', act(() => openVoiceLibrary()), { cls: 'small', disabled: !ENG.library_schema }),
      ENG.studio_schema ? btn('Pronunciation & performances', '', act(openVoiceStudio), { cls: 'small' }) : null,
      ENG.effects_schema ? btn('Voice effects', '', act(() => openVoiceStudio('effects')), { cls: 'small' }) : null,
      ENG.alignment_schema ? btn('Lip sync', '', act(openLipSync), { cls: 'small' }) : null,
      assigned(v) && lines.length ? btn('Voice the dialogue', 'zap', act(() => voiceLines()), { cls: 'small', disabled: !!busy.voice_line }) : null,
      assigned(v) ? h('a', { class: 'btn small', href: `/api/npcs/${n.id}/voice-pack.zip`, download: '' }, 'Download voice pack') : null,
      v.reference_history?.length ? h('details', { class: 'voice-reference-history' }, h('summary', {}, `Reference history (${v.reference_history.length})`),
        h('div', { class: 'take-list' }, [...v.reference_history].reverse().map((ref, index) => h('div', { class: 'take' },
          ref.file ? player(ref, `Reference version ${v.reference_history.length - index}`) : h('span', {}, `Preset ${ref.voice_snapshot?.stock_voice || ''}`),
          h('span', { class: 'cap' }, `Version ${v.reference_history.length - index} · ${ref.engine || 'recording'} · ${ref.created?.slice(0, 10) || ''}`),
          ref.id === identityId(v) ? h('span', { class: 'badge ok' }, 'current') : btn('Restore this reference', '', act(() => restoreReference(ref.id)), { cls: 'small' }))))) : null,
      h('p', { class: 'hint' }, 'Assigned voices are used by this character’s Dialogue pack and Scenes.'));
    editor.replaceChildren(...[
      h('header', { class: 'voice-character-head' }, h('div', {}, h('h2', {}, fullName(n)),
        h('p', { class: 'hint' }, [n.profile.age, n.profile.gender, n.profile.occupation].filter(Boolean).join(' · '))),
        h('div', { class: 'row wrap' }, h('span', { id: 'voiceSettingsStatus', class: 'badge' }, changed ? 'Unsaved voice settings' : 'Voice settings saved'),
          btn('Save settings', 'save', act(() => saveSettings()), { cls: 'small primary' }),
          btn('Open character', '', safe(() => openNpc(n.id)), { cls: 'small', disabled: working || loading }))),
      loading || working || Object.values(busy).length ? h('div', { class: 'notice', role: 'status' }, h('span', { class: 'spinner small' }),
        loading ? 'Loading character…' : working ? 'Saving request…' : Object.values(busy).join(' · ')) : null,
      h('fieldset', { class: 'voice-columns', disabled: working || loading, 'aria-label': 'Voice design and audition' },
        h('div', { class: 'voice-design-column stack' }, labControls(n, v, busy), h('section', { class: 'card stack' }, modelSection()),
          h('section', { class: 'card stack' }, h('h3', {}, 'Design a voice'), sources, body), h('section', { class: 'card stack' }, speakerSection(v))),
        h('div', { class: 'voice-audition-column stack' }, current,
          ENG.audition_schema ? auditionSection({ npc: n, jobs: S.jobs, busy: !!busy.voice_test, changed,
            canRun: assigned(v) && v.language === 'English', player, act, modelLabel: id => engine(id)?.label || id,
            run: async names => {
              const result = await api('POST', `/api/npcs/${n.id}/voice/auditions`, { names });
              applyNpc(result.npc); afterSubmit({ jobs: [result.job] }); toast('Character audition queued');
            },
            review: async (suite, row, body) => applyNpc(await api('PUT', `/api/npcs/${n.id}/voice/auditions/${suite}/cases/${row}`, body)) }) : null,
          h('section', { class: 'card stack' }, takesSection(v, busy)),
          v.lab_voice || v.reference || draft.mode === 'design' || (draft.mode === 'stock' && draft.stock_voice)
            ? h('section', { class: 'card stack' }, trySection(v, busy)) : null,
          lines.length ? h('section', { class: 'card stack' }, h('h3', {}, 'Dialogue audition'),
            ...lines.map(line => h('div', { class: 'dialogue-line' }, h('p', {}, line.text), lineControls(line)))) : null))].filter(Boolean));
    // Submit buttons sit outside the disabled fieldset.
    editor.querySelector('.voice-character-head .primary').disabled = working || loading;
    if (restoreFocus) {
      const control = editor.querySelector('#' + restoreFocus.id);
      if (control && !control.disabled) {
        control.focus({ preventScroll: true });
        if (typeof control.setSelectionRange === 'function' && restoreFocus.start != null)
          control.setSelectionRange(restoreFocus.start, restoreFocus.end);
      }
    }
  }

  const chipRow = (items, value, onpick) => h('div', { class: 'chips' }, items.map(([id, label, title]) =>
    h('button', { type: 'button', class: 'chip' + (value === id ? ' on' : ''), 'aria-pressed': String(value === id), title: title || '',
      onclick: () => onpick(value === id ? '' : id) }, label)));

  function modelSection() {
    const e = engine(draft.engine);
    const caps = e.caps;
    const languageCount=e.languages.filter(name=>name!=='Auto').length;
    const can = [caps.design && 'makes voices from a description', caps.clone && 'copies voices', caps.stock && 'stock voices',
      caps.direction && (caps.direction === 'experimental' ? 'moods (experimental)' : 'moods'), caps.tags?.length && `sounds: ${caps.tags.join(', ')}`, caps.accents && 'accents',
      `${languageCount} language${languageCount === 1 ? '' : 's'}`].filter(Boolean);
    const select = h('select', { id: 'voiceEngine', 'aria-label': 'Voice model', onchange: ev => {
      draft.engine = ev.target.value;
      const ne = engine(draft.engine);
      if (draft.mode === 'design' && !ne.caps.design) draft.mode = ne.caps.stock ? 'stock' : 'upload';
      if (draft.mode === 'stock' && !ne.caps.stock) draft.mode = ne.caps.design ? 'design' : 'upload';
      if (draft.mode === 'stock' && !(ENG.stock[draft.engine] || []).some(v => v.id === draft.stock_voice)) draft.stock_voice = ENG.stock[draft.engine]?.[0]?.id || '';
      if (!ne.caps.stock && !(ENG.stock.kokoro || []).some(v => v.id === draft.stock_voice)) draft.stock_voice = '';
      if (draft.engine === 'qwen_custom') draft.speaker = '';
      if (!ne.languages.includes(draft.language)) draft.language = 'English';
      tab = draft.mode; paint();
    } }, ENG.engines.map(x => h('option', { value: x.id, disabled: !x.installed && x.id !== draft.engine },
      x.label + (x.installed ? '' : ' (not installed)') + conditionsTag(x) + engineTag(x))));
    select.value = draft.engine;
    return h('section', { class: 'adv-group' }, h('h4', { class: 'lbl' }, 'Model'),
      h('div', { class: 'field' }, select),
      h('p', { class: 'hint' }, e.note),
      engineNotice(e),
      e.id==='drama3' ? btn('Open cast & narration studio','',safe(openDramaStudio),{disabled:!ENG?.drama_studio_schema||!e.installed}) : null,
      h('div', { class: 'chips' }, can.map(c => h('span', { class: 'badge' }, c))),
      e.installed ? null : h('p', { class: 'notice' }, `Not installed on this PC. Run: ${e.install_hint}`),
      licenceNote('voice:' + e.id) || (e.commercial_ok ? null : h('p', { class: 'hint' }, `Licence: ${e.licence}. Fine for personal projects; not for a game you sell (see NOTICE.md).`)));
  }

  /** Qwen3-TTS ignores accents (measured): say so where the accent is picked, and offer a model that does them. */
  function accentNote(e) {
    const accent = draft.chips?.accent;
    if (accent && accent !== 'american' && e.id === 'dramabox') return h('p', { class: 'hint' }, 'Accent controls have not been verified for DramaBox. Review its actual takes before choosing an accent.');
    if (!accent || accent === 'american' || e.caps.accents) return null;
    const doer = ENG.engines.find(x => x.caps.accents && x.caps.design && x.installed);
    return h('div', { class: 'notice' },
      h('p', {}, `${e.label} ignores accents: in our tests it always spoke American English, however the accent was written. ` +
        (doer ? `${doer.label} does them${doer.commercial_ok ? '' : ' (non-commercial licence)'}. Make the takes with it; ` +
          'lines spoken by cloning that take with Qwen3-TTS keep a lighter accent, with every word clear.' : 'No installed model that does accents was found.')),
      doer ? btn(`Switch to ${doer.label}`, 'zap', () => {
        draft.engine = doer.id;
        if (!doer.languages.includes(draft.language)) draft.language = 'English';
        paint();
      }, { cls: 'small' }) : null);
  }

  function describeTab() {
    const p = presets();
    const e = engine(draft.engine);
    const desc = h('textarea', { id: 'voiceDesc', rows: 4, value: draft.description, 'aria-label': 'Voice description',
      oninput: ev => { draft.description = ev.target.value; draft.edited = draft.description !== draft.auto_description; preview.textContent = compose(draft); } });
    const preview = h('div', { class: 'prompt-box' }, compose(draft));
    return h('div', { class: 'stack' },
      h('div', { class: 'row' }, h('label', { class: 'lbl grow', htmlFor: 'voiceDesc' }, draft.edited ? 'Description (edited by you)' : 'Description (suggested from the character sheet)'),
        btn('Suggest again', 'dice', act(async () => { await suggest(true); paint(); }), { cls: 'small ghost', title: 'Write the description from the character sheet again' })),
      desc,
      h('span', { class: 'lbl' }, 'Voice type'),
      chipRow(p.voice_types.map(v => [v.id, v.label, v.text]), draft.voice_type, id => { draft.voice_type = id; paint(); }),
      ...p.chip_groups.map(g => h('div', {}, h('span', { class: 'lbl' }, g.label),
        chipRow(g.chips.map(c => [c.id, c.label, c.text]), draft.chips[g.id] || '', id => { if (id) draft.chips[g.id] = id; else delete draft.chips[g.id]; paint(); }))),
      accentNote(e),
      h('span', { class: 'lbl' }, 'What the model reads'), preview,
      h('div', { class: 'form-grid' },
        h('div', { class: 'field' }, h('label', { class: 'lbl', htmlFor: 'voiceLang' }, 'Language'),
          (() => { const s = h('select', { id: 'voiceLang', onchange: ev => { draft.language = ev.target.value; } }, e.languages.map(l => h('option', { value: l }, l))); s.value = draft.language; return s; })()),
        h('div', { class: 'field' }, h('label', { class: 'lbl', htmlFor: 'voiceScript' }, 'What a take reads'),
          h('input', { id: 'voiceScript', type: 'text', value: draft.script, placeholder: suggestion?.script || '', oninput: ev => { draft.script = ev.target.value; } }))));
  }

  function stockTab() {
    const custom = draft.engine === 'qwen_custom', e = engine(draft.engine);
    const list = ENG.stock[draft.engine] || [];
    const order = [...(custom ? [] : suggestion?.stock || []), ...list.map(v => v.id)].filter((id, i, a) => a.indexOf(id) === i);
    return h('div', { class: 'stack' },
      h('p', { class: 'hint' }, custom ? 'Choose a model preset and save settings to assign a reference-free identity. Audition takes keep this speaker; each gets a new seed.'
        : 'Kokoro\'s ready-made voices, best match for the sheet first. Takes read the script in your pick, then the next best matches.'),
      h('div', { class: 'stock-grid' }, order.map(id => {
        const v = list.find(x => x.id === id); if (!v) return null;
        return h('label', { class: 'style-card' + (draft.stock_voice === id ? ' active' : '') },
          h('input', { type: 'radio', name: 'stockVoice', checked: draft.stock_voice === id, onchange: () => { draft.stock_voice = id; paint(); } }),
          h('strong', {}, v.label), h('span', { class: 'hint', style: { margin: 0 } }, custom ? 'Pinned model preset' : `${v.gender}, ${v.accent} · quality grade ${v.grade}`));
      })),
      custom ? h('div', { class: 'form-grid' },
        h('label', {}, 'Preset language', (() => { const s = h('select', { 'aria-label': 'Preset language', onchange: ev => { draft.language = ev.target.value; } }, e.languages.map(l => h('option', { value: l }, l))); s.value = draft.language; return s; })()),
        h('label', {}, 'Audition script', h('input', { type: 'text', 'aria-label': 'Preset audition script', value: draft.script, oninput: ev => { draft.script = ev.target.value; } }))) :
      h('div', { class: 'field' }, h('label', { class: 'lbl', htmlFor: 'voiceSpeed' }, `Speed ${Number(draft.settings.speed).toFixed(2)}x`),
        h('input', { id: 'voiceSpeed', type: 'range', min: 0.6, max: 1.6, step: 0.05, value: draft.settings.speed,
          oninput: ev => { draft.settings.speed = Number(ev.target.value); ev.target.previousSibling.textContent = `Speed ${Number(ev.target.value).toFixed(2)}x`; } })));
  }

  function uploadTab() {
    const cloners = ENG.engines.filter(e => e.caps.clone);
    const sp = h('select', { id: 'uploadSpeaker', onchange: ev => { upload.speaker = ev.target.value; } },
      cloners.map(e => h('option', { value: e.id, disabled: !e.installed }, e.label + (e.installed ? '' : ' (not installed)') + conditionsTag(e))));
    sp.value = upload.speaker || (cloners.find(e => e.installed) || cloners[0]).id;
    upload.speaker = sp.value;
    return h('div', { class: 'stack' },
      h('p', { class: 'notice' }, 'Only upload a voice you have the right to use: your own, or someone who agreed to it. Cloning a real person\'s voice without their permission is not okay, and in many places not legal.'),
      h('div', { class: 'field' }, h('label', { class: 'lbl', htmlFor: 'uploadFile' }, 'Recording (3 to 40 seconds of clear speech, one speaker, no music)'),
        h('input', { id: 'uploadFile', type: 'file', accept: 'audio/*', onchange: ev => { upload.file = ev.target.files[0] || null; } }),
        upload.file ? h('p', { class: 'hint' }, `Selected: ${upload.file.name}`) : null),
      h('div', { class: 'field' }, h('label', { class: 'lbl', htmlFor: 'uploadText' }, 'Exactly what the recording says'),
        h('textarea', { id: 'uploadText', rows: 3, value: upload.text, oninput: ev => { upload.text = ev.target.value; } })),
      h('div', { class: 'field' }, h('label', { class: 'lbl', htmlFor: 'uploadSpeaker' }, 'Speaks with'), sp),
      h('label', { class: 'check' }, h('input', { type: 'checkbox', checked: upload.consent, onchange: ev => { upload.consent = ev.target.checked; } }),
        h('span', {}, h('span', { class: 'name' }, 'I have the right to use this voice'))),
      h('div', { class: 'row' }, btn('Use this recording', 'save', act(sendUpload), { cls: 'small primary' })));
  }

  function copyTab() {
    const others = S.library.filter(n => n.id !== npc().id && n.voice && n.voice !== 'none');
    return h('div', { class: 'stack' },
      h('p', { class: 'hint' }, 'Give this character another character\'s voice: twins, a family, or one actor playing several parts.'),
      others.length ? h('div', { class: 'take-list' }, others.slice(0, 60).map(o => h('div', { class: 'take' },
        h('span', { class: 'grow' }, `${o.name} · ${o.occupation || ''}`), btn('Copy this voice', '', act(() => copyFrom(o.id)), { cls: 'small' }))))
        : h('p', { class: 'hint' }, 'No other character has a voice yet.'));
  }

  function takesSection(v, busy) {
    const takes = [...(v.takes || [])].reverse();
    const canTake = draft.mode === 'design' || draft.mode === 'stock';
    const count = h('select', { 'aria-label': 'How many takes', onchange: ev => { draft.count = Number(ev.target.value); } }, [1, 2, 3, 4].map(n => h('option', { value: n }, `${n} take${n === 1 ? '' : 's'}`)));
    count.value = String(draft.count || 3);
    return h('section', { class: 'adv-group' }, h('h4', { class: 'lbl' }, 'Takes'),
      canTake ? h('div', { class: 'row' }, count,
        btn(busy.voice_take ? busy.voice_take : 'Make takes', 'zap', act(makeTakes), { cls: 'small primary', disabled: !!busy.voice_take || !engine(draft.engine).installed }),
        h('span', { class: 'hint grow' }, draft.mode === 'stock'
          ? draft.engine === 'qwen_custom' ? 'Each take reads the same script with this preset speaker and a new seed. Play them and choose an audition.'
            : 'Each take reads the script in your stock voice, then in the next best matches. Play them and use the one you like.'
          : 'Each take is a different voice from the same description (a new seed). Play them and use the one you like.'))
        : h('p', { class: 'hint' }, 'An uploaded or copied voice is used as it is; there is nothing to take.'),
      takes.length ? h('div', { class: 'take-list' }, takes.map(t => takeRow(t, v))) : null,
      ENG.studio_schema ? comparisonSection(v) : null);
  }

  function comparisonSection(v) {
    const groups = new Map();
    for (const take of v.takes || []) {
      const key = JSON.stringify([take.text, take.language || 'English']);
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(take);
    }
    const sessions = v.comparisons || [];
    return h('section', { class: 'voice-comparisons stack' }, h('h4', {}, 'Same-script comparisons'),
      h('p', { class: 'hint' }, 'Compare candidates reading the exact same script and language. Stars and favorites record your listening judgment; retained comparisons protect their clips.'),
      [...groups.values()].filter(group => group.length >= 2).map((group, index) => btn(`Compare script ${index + 1} (${Math.min(12, group.length)} candidates)`, '',
        act(async () => applyNpc(await api('POST', `/api/npcs/${npc().id}/voice/comparisons`, {
          take_ids: group.slice(-12).map(t => t.id), title: `Audition ${sessions.length + 1}` }))), { cls: 'small', title: group[0].text })),
      [...sessions].reverse().map(session => h('section', { class: 'card stack' }, h('h4', {}, session.title),
        h('p', {}, session.script), h('span', { class: 'hint' }, session.language),
        h('div', { class: 'voice-comparison-grid' }, session.candidates.map((candidate, index) => {
          const rating = h('select', { 'aria-label': `Candidate ${index + 1} rating` }, h('option', { value: '' }, 'Unrated'),
            [1, 2, 3, 4, 5].map(n => h('option', { value: n }, `${n} star${n === 1 ? '' : 's'}`)));
          rating.value = candidate.rating ?? '';
          const notes = h('textarea', { rows: 2, value: candidate.notes, 'aria-label': `Candidate ${index + 1} listening notes` });
          const review = body => api('PUT', `/api/npcs/${npc().id}/voice/comparisons/${session.id}/candidates/${candidate.id}`, body).then(applyNpc);
          return h('div', { class: 'take stack' }, h('strong', {}, `Candidate ${index + 1}`), player(candidate, `Compare candidate ${index + 1}`),
            h('span', { class: 'cap' }, `${engine(candidate.engine)?.label || candidate.engine} · seed ${candidate.seed}`), rating, notes,
            btn('Save rating & notes', 'save', act(() => review({ rating: rating.value ? Number(rating.value) : null, notes: notes.value })), { cls: 'small' }),
            btn(candidate.favorite ? '★ Favorite candidate' : '☆ Favorite candidate', '', act(() => review({ favorite: !candidate.favorite })), { cls: 'small' }),
            v.chosen_take === candidate.id ? h('span', { class: 'badge ok' }, 'Assigned') : btn('Assign candidate', '', act(() => selectTake(candidate.id)), { cls: 'small' }));
        })) )));
  }

  function trySection(v, busy) {
    const e = engine(draft.speaker || draft.engine), speakerCaps = v.lab_voice ? { direction: v.lab_voice.takes_direction } : e?.caps || {};
    if (!speakerCaps.direction) tryMood = 'neutral';
    const text = h('textarea', { id: 'voiceTry', rows: 2, value: tryText, placeholder: suggestion?.script || 'Type a line…', oninput: ev => { tryText = ev.target.value; } });
    const mood = h('select', { 'aria-label': 'Mood', disabled: !speakerCaps.direction, onchange: ev => { tryMood = ev.target.value; } },
      presets().moods.filter(m => m !== 'flirty' || suggestion?.adult).map(m => h('option', { value: m }, m)));
    mood.value = tryMood;
    const insert = tag => { const at = text.selectionStart ?? tryText.length; tryText = tryText.slice(0, at) + `[${tag}] ` + tryText.slice(at); text.value = tryText; text.focus(); };
    const tests = v.tests || {};
    const moodKeys = ['happy', 'sad', 'angry', 'surprised'];
    return h('section', { class: 'adv-group' }, h('h4', { class: 'lbl' }, 'Try it'),
      text,
      h('div', { class: 'row wrap' }, mood,
        (speakerCaps.tags || []).map(t => btn(`[${t}]`, '', () => insert(t), { cls: 'small ghost', title: `Insert a ${t} (this model performs it)` })),
        btn(busy.voice_test ? busy.voice_test : 'Speak', 'zap', act(() => speak(tryText, tryMood)), { cls: 'small primary', disabled: !!busy.voice_test })),
      tests.try ? h('div', { class: 'take' }, player(tests.try, 'Your line'), h('span', { class: 'cap' }, `${tests.try.mood} · ${engine(tests.try.engine)?.label || tests.try.engine}`)) : null,
      h('span', { class: 'lbl' }, 'Moods'),
      h('div', { class: 'row wrap' }, moodKeys.map(m => h('div', { class: 'mood' },
        btn(m, '', act(() => speak(tryText, m, true)), { cls: 'small', disabled: !!busy.voice_test || !speakerCaps.direction,
          title: speakerCaps.direction ? `Hear this voice ${m}` : `${e.label} does not support mood direction` }),
        tests['mood_' + m] ? player(tests['mood_' + m], m) : null))),
      h('p', { class: 'hint' }, speakerCaps.direction ? (speakerCaps.direction === 'experimental'
        ? 'Moods on a copied voice are experimental with this model; Breeze TTS 2 is trained for them.'
        : 'Moods steer the copied voice.') : 'This model cannot change the mood of a copied voice; sound tags still work where listed.'));
  }

  function speakerSection(v) {
    const options = ENG.engines.filter(e => draft.engine === 'qwen_custom' && draft.mode === 'stock' ? e.id === 'qwen_custom'
      : e.caps.clone || (e.id === 'kokoro' && draft.mode === 'stock'));
    const s = h('select', { id: 'voiceSpeaker', onchange: ev => { draft.speaker = ev.target.value; paint(); } },
      h('option', { value: '' }, 'Automatic (the model that made the voice)'),
      options.map(e => h('option', { value: e.id, disabled: !e.installed }, e.label + (e.installed ? '' : ' (not installed)') + conditionsTag(e) + engineTag(e))));
    s.value = draft.speaker || '';
    const temp = h('input', { type: 'range', min: 0.5, max: 1.2, step: 0.05, value: draft.settings.temperature, 'aria-label': 'Variation',
      oninput: ev => { draft.settings.temperature = Number(ev.target.value); } });
    const performanceCaps = ENG.performance_caps?.[draft.speaker || draft.engine];
    const hasTemperature = performanceCaps ? performanceCaps.parameters.includes('temperature') : ['qwen', 'breeze', 'fake'].includes(draft.speaker || draft.engine);
    return h('section', { class: 'adv-group' }, h('h4', { class: 'lbl' }, 'Speaking'),
      draft.speaker && draft.speaker !== draft.engine ? engineNotice(engine(draft.speaker)) : null,
      h('div', { class: 'form-grid' },
        h('div', { class: 'field' }, h('label', { class: 'lbl', htmlFor: 'voiceSpeaker' }, 'Speaks lines with'), s,
          h('p', { class: 'hint' }, draft.engine === 'qwen_custom' && draft.mode === 'stock'
            ? 'Lines are spoken directly by the saved preset speaker. Audition recordings are never used as cloning references.'
            : draft.mode==='stock' && draft.engine==='kokoro' && (!draft.speaker || draft.speaker==='kokoro')
              ? 'Lines use the saved Kokoro speaker embedding. The audition recording is kept for playback; automatic Kokoro speech does not clone it.'
              : 'Lines are spoken by cloning the chosen voice. Qwen3-TTS is fastest; Breeze TTS 2 does moods and sighs; Chatterbox Turbo supports sound tags.')),
        hasTemperature ? h('div', { class: 'field' }, h('label', { class: 'lbl' }, 'Variation (lower is steadier)'), temp) : null));
  }

  function paint() {
    if (standalone) { paintWorkspace(); return; }
    const dlg = $('#voiceDesigner'); if (!dlg || !draft || !ENG) return;
    const keep = dlg.querySelector('.about-body')?.scrollTop || 0;
    const v = voice(), busy = busyVoice(), n = npc();
    const e = engine(draft.engine);
    const allowed = { design: e.caps.design, stock: e.caps.stock, upload: true, copy: true };
    const tabs = h('div', { class: 'seg', role: 'group' }, SOURCES.map(([id, label]) =>
      h('button', { type: 'button', 'aria-pressed': String(tab === id), disabled: !allowed[id],
        title: allowed[id] ? '' : `${e.label} cannot do this`, onclick: () => { tab = id; if (id === 'design' || id === 'stock') draft.mode = id; paint(); } }, label)));
    const body = { design: describeTab, stock: stockTab, upload: uploadTab, copy: copyTab }[tab]();
    dlg.replaceChildren(
      h('div', { class: 'about-body' },
        h('div', { class: 'about-head' },
          h('div', { class: 'grow' }, h('h2', { id: 'voiceTitle' }, `Voice designer · ${fullName(n)}`),
            h('p', { class: 'hint' }, [n.profile.age, n.profile.gender, n.profile.occupation].filter(Boolean).join(' · '))),
          btn('Close', '', close, { cls: 'small ghost' })),
        v.reference ? h('div', { class: 'voice-now' }, h('span', { class: 'lbl' }, 'Current voice'), player(v.reference, 'Current voice')) : null,
        modelSection(),
        h('section', { class: 'adv-group' }, h('h4', { class: 'lbl' }, 'Where the voice comes from'), tabs, body),
        takesSection(v, busy),
        labControls(n, v, busy),
        v.lab_voice || v.reference || draft.mode === 'design' || (draft.mode === 'stock' && draft.stock_voice) ? trySection(v, busy) : null,
        speakerSection(v)),
      h('div', { class: 'about-foot adv-foot' },
        h('span', { class: 'hint grow' }, v.reference ? (v.review === 'pending' ? 'Using take 1 until you pick one.' : 'Voice chosen.') : 'Make takes, then use the one you like.'),
        btn('Save settings', 'save', safe(() => saveSettings()), { cls: 'small' }),
        btn('Done', '', safe(async () => { await saveSettings(true); close(); }), { cls: 'small primary' })));
    const b = dlg.querySelector('.about-body'); if (b) b.scrollTop = keep;
  }

  // ---- many characters: "Voice for N selected" and the Review page -------------------------------------------------------------
  const fileUrl = (npcId, meta) => `/api/npcs/${npcId}/files/${meta.file}?v=${encodeURIComponent(meta.created || '')}`;

  function dialog(id, title, ...body) {
    let dlg = $('#' + id);
    if (!dlg) {
      dlg = h('dialog', { id, class: 'about advanced voice-designer', 'aria-labelledby': id + 'Title',
        onclick: e => { if (e.target === e.currentTarget) e.currentTarget.close(); } });
      document.body.append(dlg);
    }
    dlg.replaceChildren(h('div', { class: 'about-body' },
      h('div', { class: 'about-head' }, h('h2', { id: id + 'Title', class: 'grow' }, title), btn('Close', '', () => dlg.close(), { cls: 'small ghost' })),
      ...body));
    if (!dlg.open) dlg.showModal();
    return dlg;
  }

  /** "Voice for N selected": the voice each sheet suggests, takes (the first used until you review), optionally the dialogue. */
  async function openBatch(ids) {
    await loadEngines(true);
    const makers = ENG.engines.filter(e => e.caps.design || e.caps.stock);
    const opts = { engine: (makers.find(e => e.id === ENG.default && e.installed) || makers.find(e => e.installed) || makers[0]).id,
      takes: 3, lines: false, redo: false };
    const estimate = h('p', { class: 'hint' });
    const update = () => {
      const e = engine(opts.engine);
      const perTake = 9 * e.rtf + 1, perLine = 4 * e.rtf + 0.5;           // a take reads about 9 s, a line about 4 s (measured)
      const minutes = ids.length * (opts.takes * perTake + (opts.lines ? 12 * perLine : 0)) / 60;
      estimate.textContent = `About ${minutes < 1 ? 'a minute' : Math.round(minutes) + ' minutes'} of GPU time for ${ids.length} character${ids.length === 1 ? '' : 's'}` +
        ` (measured speeds on a 32 GB Radeon; the first clip also loads the model). Characters whose voice you already picked are skipped unless you ask for new takes.`;
    };
    const engineSel = h('select', { onchange: ev => { opts.engine = ev.target.value; update(); } },
      makers.map(e => h('option', { value: e.id, disabled: !e.installed }, e.label + (e.installed ? '' : ' (not installed)') + conditionsTag(e) + engineTag(e))));
    engineSel.value = opts.engine;
    const takesSel = h('select', { onchange: ev => { opts.takes = Number(ev.target.value); update(); } }, [1, 2, 3, 4].map(n => h('option', { value: n }, `${n} take${n === 1 ? '' : 's'}`)));
    takesSel.value = '3';
    const check = (label, key, hint) => h('label', { class: 'check' },
      h('input', { type: 'checkbox', checked: opts[key], onchange: ev => { opts[key] = ev.target.checked; update(); } }),
      h('span', {}, h('span', { class: 'name' }, label), h('span', { class: 'hint', style: { display: 'block' } }, hint)));
    update();
    const dlg = dialog('voiceBatch', `Voice ${ids.length} character${ids.length === 1 ? '' : 's'}`,
      h('p', { class: 'hint' }, 'Each character gets the voice its character sheet suggests (unless you already set one up in the designer). ' +
        'The first take is used straight away; pick the one you like best on the Review voices page.'),
      h('div', { class: 'form-grid' }, h('div', { class: 'field' }, h('span', { class: 'lbl' }, 'Model'), engineSel),
        h('div', { class: 'field' }, h('span', { class: 'lbl' }, 'Takes each'), takesSel)),
      check('Voice their dialogue too', 'lines', 'Made from the character sheet first if a character has none.'),
      check('New takes even where a voice is picked', 'redo', 'Otherwise those characters keep their voice.'),
      estimate,
      h('div', { class: 'row' }, h('span', { class: 'grow' }), btn('Queue', 'zap', safe(async () => {
        const res = await api('POST', '/api/voice/batch', { npc_ids: ids, ...opts });
        afterSubmit({ jobs: res.jobs });
        dlg.close();
        toast(`Queued ${res.jobs.length} character${res.jobs.length === 1 ? '' : 's'}` + (res.skipped ? `; ${res.skipped} already had everything` : ''));
      }), { cls: 'small primary' })));
  }

  /** Review voices: every character whose first take is standing in. Keys: 1-4 use that take and go to the next character,
   *  P plays the next take, R makes 3 new takes, ↓/J and ↑/K move. */
  async function openReview() {
    await loadEngines();
    let rows = await api('GET', '/api/voice/review');
    let at = 0, playing = null, playIdx = -1;
    const stopPlaying = () => { if (playing) { playing.pause(); playing = null; } };
    const play = (row, i) => {
      stopPlaying();
      const t = row.takes[i]; if (!t) return;
      playing = new Audio(fileUrl(row.id, t)); playing.play().catch(() => {}); playIdx = i;
    };
    const choose = async (row, i) => {
      const t = row.takes[i]; if (!t) return;
      stopPlaying();
      const fresh = await api('POST', `/api/npcs/${row.id}/voice/takes/${t.id}/select`);
      if (standalone && selectedNpc?.id === row.id) {
        Object.assign(draft, { engine: fresh.voice.engine, mode: fresh.voice.mode, stock_voice: fresh.voice.stock_voice || draft.stock_voice });
        if (tab === 'design' || tab === 'stock') tab = draft.mode;
        applyNpc(fresh);
      }
      rows = rows.filter(r => r !== row); at = Math.min(at, rows.length - 1); playIdx = -1;
      paintReview();
      if (rows[at]) play(rows[at], 0);
    };
    const more = async row => {
      const res = await api('POST', `/api/npcs/${row.id}/voice/takes`, { count: 3 });
      afterSubmit({ jobs: [res.job] });
      toast(`Making 3 new takes for ${row.name}; reopen the review to hear them.`);
    };
    const onKey = e => {
      const row = rows[at]; if (!row || ['INPUT', 'SELECT', 'TEXTAREA'].includes(e.target.tagName)) return;
      if (/^[1-4]$/.test(e.key)) { e.preventDefault(); safe(choose)(row, Number(e.key) - 1); }
      else if (e.key === 'p' || e.key === 'P' || e.key === ' ') { e.preventDefault(); play(row, (playIdx + 1) % row.takes.length); }
      else if (e.key === 'r' || e.key === 'R') { e.preventDefault(); safe(more)(row); }
      else if (e.key === 'ArrowDown' || e.key === 'j') { e.preventDefault(); at = Math.min(rows.length - 1, at + 1); playIdx = -1; paintReview(); }
      else if (e.key === 'ArrowUp' || e.key === 'k') { e.preventDefault(); at = Math.max(0, at - 1); playIdx = -1; paintReview(); }
    };
    function paintReview() {
      const list = rows.length ? h('div', { class: 'review-list' }, rows.map((row, r) => {
        const e = engine(row.engine);
        return h('div', { class: 'review-row' + (r === at ? ' current' : ''), onclick: () => { if (at !== r) { at = r; playIdx = -1; paintReview(); } } },
          h('img', { class: 'review-thumb', src: `/api/npcs/${row.id}/files/thumb.png`, alt: '', loading: 'lazy' }),
          h('div', { class: 'review-who' }, h('strong', {}, row.name), h('span', { class: 'hint' }, [row.age, row.occupation, e?.label].filter(Boolean).join(' · ')),
            h('span', { class: 'hint review-desc' }, row.description || '')),
          h('div', { class: 'review-takes' }, row.takes.map((t, i) => h('div', { class: 'take' + (row.chosen_take === t.id ? ' in-use' : '') },
            h('span', { class: 'badge' }, String(i + 1)), h('audio', { controls: true, preload: 'none', src: fileUrl(row.id, t) }),
            btn('Use', '', safe(() => choose(row, i)), { cls: 'small' })))),
          btn('3 new takes', 'refresh', safe(() => more(row)), { cls: 'small ghost', title: 'R' }));
      })) : h('div', { class: 'empty' }, h('h2', {}, 'Nothing to review'), h('p', {}, 'Every voice has been picked.'));
      const dlg = dialog('voiceReview', `Review voices · ${rows.length} waiting`,
        h('p', { class: 'hint' }, 'Keys: 1-4 use that take and go to the next character · P or Space plays the next take · R makes 3 new takes · ↑ ↓ move. ' +
          'Until you pick, each character speaks with take 1 (marked).'), list);
      dlg.onkeydown = onKey;
      dlg.onclose = () => {
        stopPlaying(); dlg.onkeydown = null;
        if (standalone) Promise.resolve(refreshLibrary()).then(paint).catch(() => {});
      };
      dlg.querySelector('.review-row.current')?.scrollIntoView({ block: 'nearest' });
    }
    paintReview();
  }

  return { voiceCard, openDesigner, onDraftChanged, loadEngines, voiceLines, lineControls, dialogueVoiceActions, openBatch, openReview,
    openTab, renderWorkspace, onJobChange };
}
