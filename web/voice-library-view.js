// Reusable voice library and reference editor. Audio changes create new versions.
import { $, S, api, btn, fullName, h, safe, toast } from './core.js';

export function createVoiceLibrary({ selectedNpc, onAssigned, engineInfo }) {
  let rows = [], selected = '', versionId = '', query = '', favorites = false, saving = false;
  let targets = new Set(), owner = null, abort = null;
  let recorder = null, microphone = null, recordingBlob = null, recordingUrl = '', recordTimer = null;
  let sourceFile = null, transcript = '', recordTranscript = '', origin = '', consent = false;
  let start = 0, end = 0, checkedTranscript = false, normalize = false;
  const entry = () => rows.find(v => v.id === selected);
  const version = () => entry()?.versions.find(v => v.id === versionId);
  const field = (label, control, hint = '') => h('label', { class: 'stack' }, h('span', { class: 'lbl' }, label), control,
    hint ? h('span', { class: 'hint' }, hint) : null);
  const action = fn => safe(async () => {
    if (saving) return;
    saving = true;
    $('#voiceLibraryBusy').hidden = false;
    $('#voiceLibraryControls').disabled = true;
    let succeeded = false;
    try { await fn(); succeeded = true; } finally {
      saving = false;
      if (succeeded) paint();
      else { $('#voiceLibraryBusy').hidden = true; $('#voiceLibraryControls').disabled = false; }
    }
  });

  async function refresh() { rows = (await api('GET', '/api/voice/library')).voices; }
  function choose(id, chosenVersion) {
    selected = id;
    const v = entry();
    versionId = chosenVersion || v.preferred_version || v.versions[0]?.id;
    const ref = version();
    start = 0; end = ref?.quality.duration_seconds || 0; transcript = ref?.reference.text || '';
    checkedTranscript = false; normalize = false;
    paint();
  }

  async function open({ saveCurrent = false } = {}) {
    owner = selectedNpc(); targets = new Set(owner?.id ? [owner.id] : []);
    await refresh();
    let dlg = $('#voiceLibrary');
    if (!dlg) {
      dlg = h('dialog', { id: 'voiceLibrary', class: 'about voice-library-dialog', 'aria-labelledby': 'voiceLibraryTitle' });
      document.body.append(dlg);
      dlg.onclose = releaseAudio;
      dlg.oncancel = e => { if (saving) e.preventDefault(); };
    }
    if (!dlg.open) dlg.showModal();
    if (!rows.some(v => v.id === selected)) selected = rows[0]?.id || '';
    if (selected) choose(selected); else paint();
    if (saveCurrent) openSave();
  }

  function releaseAudio() {
    abort?.abort();
    clearInterval(recordTimer); recordTimer = null;
    if (recorder?.state === 'recording') recorder.stop();
    microphone?.getTracks().forEach(track => track.stop()); microphone = null;
    if (recordingUrl) URL.revokeObjectURL(recordingUrl);
    recordingUrl = ''; recordingBlob = null; sourceFile = null;
    recordTranscript = ''; origin = ''; consent = false;
  }

  function openSave() {
    if (!owner?.voice?.reference && !owner?.voice?.preset_identity) throw new Error('Assign a character voice first, then save it to the reusable library.');
    const name = h('input', { type: 'text', value: fullName(owner) + ' voice', 'aria-label': 'New library voice name' });
    const tags = h('input', { type: 'text', placeholder: 'fantasy, gruff, guard', 'aria-label': 'Library voice tags' });
    const destination = h('select', { 'aria-label': 'Save voice to' }, h('option', { value: '' }, 'Create a new named voice'),
      rows.map(v => h('option', { value: v.id }, `Add a version to ${v.name}`)));
    const notes = h('textarea', { rows: 2, 'aria-label': 'Voice version notes' });
    const section = h('section', { class: 'card stack voice-library-save' }, h('h3', {}, 'Save assigned voice'),
      h('p', { class: 'hint' }, `Capture ${fullName(owner)}’s assigned identity and speaking settings. New versions wait for your approval.`),
      field('Save to', destination), field('Name', name), field('Tags, separated by commas', tags), field('Version notes', notes),
      btn('Save reference version', 'save', action(async () => {
        const res = await api('POST', '/api/voice/library', { npc_id: owner.id, voice_id: destination.value || undefined,
          name: name.value, tags: tags.value.split(',').map(x => x.trim()).filter(Boolean), version_notes: notes.value });
        await refresh(); choose(res.voice.id, res.voice.versions[0].id); toast('Reference version saved; audition it before approving.');
      }), { cls: 'small primary' }));
    $('#voiceLibraryBody').prepend(section); name.focus();
  }

  function list() {
    const matches = rows.filter(v => (!favorites || v.favorite) && `${v.name} ${v.tags.join(' ')} ${v.notes}`.toLowerCase().includes(query.toLowerCase()));
    return h('aside', { class: 'voice-library-list stack' },
      h('input', { type: 'text', value: query, 'aria-label': 'Search reusable voices', placeholder: 'Search voices or tags…',
        oninput: e => { query = e.target.value; $('#voiceLibraryListRows').replaceChildren(...listRows()); } }),
      h('label', { class: 'check' }, h('input', { type: 'checkbox', checked: favorites, onchange: e => { favorites = e.target.checked; paint(); } }), 'Favorites only'),
      h('div', { id: 'voiceLibraryListRows', class: 'stack' }, listRows(matches)));
  }

  function listRows(matches) {
    matches ||= rows.filter(v => (!favorites || v.favorite) && `${v.name} ${v.tags.join(' ')} ${v.notes}`.toLowerCase().includes(query.toLowerCase()));
    return matches.length ? matches.map(v => h('button', { type: 'button', class: 'voice-person' + (v.id === selected ? ' active' : ''),
      onclick: () => choose(v.id), 'aria-pressed': String(v.id === selected) }, h('span', { class: 'grow' },
        h('strong', {}, (v.favorite ? '★ ' : '') + v.name), h('span', { class: 'hint' }, v.tags.join(' · ') || `${v.versions.length} version(s)`))))
      : [h('p', { class: 'hint' }, rows.length ? 'No matching voices.' : 'Save an assigned character voice to begin your reusable library.')];
  }

  function details(v, ref) {
    const name = h('input', { type: 'text', value: v.name, 'aria-label': 'Library voice name' });
    const tags = h('input', { type: 'text', value: v.tags.join(', '), 'aria-label': 'Edit voice tags' });
    const notes = h('textarea', { rows: 2, value: v.notes, 'aria-label': 'Library voice notes' });
    const picker = h('select', { 'aria-label': 'Reference version', onchange: e => choose(v.id, e.target.value) }, v.versions.map(r =>
      h('option', { value: r.id }, `Version ${r.number} · ${r.approved ? 'Approved' : 'Draft'}${r.id === v.preferred_version ? ' · default' : ''}`)));
    picker.value = ref.id;
    return h('section', { class: 'card stack' }, h('div', { class: 'row wrap' }, h('h3', { class: 'grow' }, v.name),
      btn(v.favorite ? '★ Favorite' : '☆ Favorite', '', action(async () => { await api('PUT', `/api/voice/library/${v.id}`, { favorite: !v.favorite }); await refresh(); }), { cls: 'small' })),
      field('Voice name', name), field('Tags', tags), field('Notes', notes),
      btn('Save voice details', 'save', action(async () => { await api('PUT', `/api/voice/library/${v.id}`, {
        name: name.value, tags: tags.value.split(',').map(x => x.trim()).filter(Boolean), notes: notes.value }); await refresh(); }), { cls: 'small' }),
      field('Reference version', picker),
      h('p', { class: 'hint' }, `Version ${ref.number} · ${ref.created.slice(0, 10)} · ${ref.snapshot.engine} · ${ref.reference.language || ref.snapshot.language}`),
      h('div', { class: 'row wrap' }, btn(ref.approved ? 'Withdraw approval' : 'Approve this version', '', action(async () => {
        await api('POST', `/api/voice/library/versions/${ref.id}/approval`, { approved: !ref.approved }); await refresh();
      }), { cls: 'small' }),
      btn('Make default version', '', action(async () => { await api('PUT', `/api/voice/library/${v.id}`, { preferred_version: ref.id }); await refresh(); }),
        { cls: 'small', disabled: !ref.approved || ref.id === v.preferred_version })),
      h('p', { class: 'hint' }, 'Default versions guide new assignments. Characters keep their pinned version until you explicitly assign another.'));
  }

  function referenceEditor(ref) {
    const player = h('audio', { id: 'libraryRefAudio', controls: true, preload: 'metadata', src: ref.audio_url, 'aria-label': 'Library reference audio',
      ontimeupdate: e => { if (e.target.dataset.region === '1' && e.target.currentTime >= end) { e.target.pause(); e.target.dataset.region = ''; } } });
    const wave = h('canvas', { id: 'referenceWaveform', width: 960, height: 120, role: 'img', 'aria-label': 'Reference waveform',
      onpointerdown: e => { const box = e.target.getBoundingClientRect(); player.currentTime = (e.clientX - box.left) / box.width * ref.quality.duration_seconds; } });
    const number = (label, value, set) => field(label, h('input', { type: 'number', min: 0, max: ref.quality.duration_seconds, step: .01,
      value, 'aria-label': label, oninput: e => set(Number(e.target.value)) }));
    const warnings = ref.quality.warnings;
    return h('section', { class: 'card stack' }, h('h3', {}, 'Reference editor'), player, wave,
      h('p', { id: 'referenceWaveformStatus', class: 'hint' }, 'Loading waveform…'),
      h('p', { class: 'hint' }, `${ref.quality.duration_seconds.toFixed(2)} s · ${ref.quality.sample_rate} Hz · RMS ${ref.quality.rms_dbfs} dBFS · clipped ${(ref.quality.clipped_fraction * 100).toFixed(2)}%`),
      warnings.length ? h('p', { class: 'notice' }, warnings.join('. ')) : null,
      h('div', { class: 'form-grid' }, number('Trim start (seconds)', start, value => { start = value; }), number('Trim end (seconds)', end, value => { end = value; })),
      btn('Play selected region', '', safe(() => {
        if (!(start >= 0 && start < end && end <= ref.quality.duration_seconds)) throw new Error('Choose a valid trim region first.');
        player.currentTime = start; player.dataset.region = '1'; return player.play();
      }), { cls: 'small' }),
      btn('Suggest transcript from this region', '', action(async () => {
        const result = await api('POST', `/api/voice/library/versions/${ref.id}/transcript-draft`, { start_seconds: start, end_seconds: end });
        transcript = result.draft.text; checkedTranscript = false; paint();
        toast('Draft transcript added. It is not saved: check every word against the selected region first.');
      }), { cls: 'small' }),
      field('Exact transcript for this region', h('textarea', { rows: 3, value: transcript, 'aria-label': 'Trimmed reference transcript', oninput: e => { transcript = e.target.value; } })),
      h('label', { class: 'check' }, h('input', { type: 'checkbox', checked: checkedTranscript, onchange: e => { checkedTranscript = e.target.checked; } }), 'I checked the transcript against the selected region'),
      h('label', { class: 'check' }, h('input', { type: 'checkbox', checked: normalize, onchange: e => { normalize = e.target.checked; } }), 'Normalize this new reference version'),
      btn('Save trimmed version', 'save', action(async () => {
        if (!checkedTranscript) throw new Error('Check the exact transcript for the selected region first.');
        const result = await api('POST', `/api/voice/library/versions/${ref.id}/derive`, { start_seconds: start, end_seconds: end, transcript, normalize });
        await refresh(); choose(selected, result.version.id); toast('New reference version saved. The original is preserved.');
      }), { cls: 'small primary' }),
      btn('Clean background noise into a new draft', '', action(async () => {
        const result = await api('POST', `/api/voice/library/versions/${ref.id}/denoise`, {});
        await refresh(); choose(selected, result.version.id);
        toast('Noise reduced in a new draft. Words and timing are unchanged, but the voice is not checked against a clean reference. Listen before approving.');
      }), { cls: 'small' }),
      h('p', { class: 'hint' }, 'Trimming and transcript corrections create a new draft version. Original audio, provenance, and existing assignments are preserved.'));
  }

  async function waveform(ref) {
    abort?.abort(); abort = new AbortController(); const signal = abort.signal;
    const context = new (window.AudioContext || window.webkitAudioContext)();
    try {
      const response = await fetch(ref.audio_url, { signal });
      if (!response.ok) throw new Error('Reference audio could not be loaded');
      const decoded = await context.decodeAudioData(await response.arrayBuffer());
      const canvas = $('#referenceWaveform');
      if (signal.aborted || !canvas || versionId !== ref.id) return;
      const ctx = canvas.getContext('2d'), samples = decoded.getChannelData(0), step = Math.max(1, Math.ceil(samples.length / canvas.width));
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      ctx.strokeStyle = getComputedStyle(canvas).color; ctx.lineWidth = 1;
      ctx.beginPath();
      for (let x = 0; x < canvas.width; x++) {
        let low = 0, high = 0;
        for (let i = x * step; i < Math.min((x + 1) * step, samples.length); i++) { low = Math.min(low, samples[i]); high = Math.max(high, samples[i]); }
        ctx.moveTo(x, canvas.height / 2 + low * canvas.height * .45); ctx.lineTo(x, canvas.height / 2 + high * canvas.height * .45);
      }
      ctx.stroke(); $('#referenceWaveformStatus').textContent = 'Click the waveform to seek. Measurements do not assess speaker identity or background noise.';
    } catch (e) { if (!signal.aborted && $('#referenceWaveformStatus')) $('#referenceWaveformStatus').textContent = e.message; }
    finally { await context.close(); }
  }

  function assignment(ref) {
    const badLength = ref.quality.warnings.some(w => w.includes('needs at least') || w.includes('Trim the cloning'));
    return h('section', { class: 'card stack' }, h('h3', {}, 'Assign this version'),
      h('p', { class: 'hint' }, 'Each selected character receives an independent, pinned copy. Existing dialogue made in another voice becomes outdated.'),
      h('div', { class: 'voice-assignment-list' }, S.library.map(n => h('label', { class: 'check' },
        h('input', { type: 'checkbox', checked: targets.has(n.id), onchange: e => { if (e.target.checked) targets.add(n.id); else targets.delete(n.id); updateAssignButton(ref); } }), n.name))),
      h('button', { id: 'assignLibraryVoice', type: 'button', class: 'btn small primary', disabled: !ref.approved || badLength || !targets.size,
        onclick: action(async () => {
          const result = await api('POST', `/api/voice/library/versions/${ref.id}/assign`, { npc_ids: [...targets] });
          await onAssigned(result.npcs); toast(`Assigned version ${ref.number} to ${result.npcs.length} character(s).`);
        }) }, `Assign approved version to ${targets.size} character(s)`),
      !ref.approved ? h('p', { class: 'hint' }, 'Approve this version after auditioning it to enable assignment.') : null);
  }

  function updateAssignButton(ref) {
    const button = $('#assignLibraryVoice'); if (!button) return;
    button.textContent = `Assign approved version to ${targets.size} character(s)`;
    button.disabled = !ref.approved || !targets.size || ref.quality.warnings.some(w => w.includes('needs at least') || w.includes('Trim the cloning'));
  }

  async function recordMic() {
    if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) throw new Error('Microphone recording is unavailable in this browser; upload a recording instead.');
    consent = false;
    microphone = await navigator.mediaDevices.getUserMedia({ audio: true });
    if (!$('#voiceLibrary')?.open) { microphone.getTracks().forEach(t => t.stop()); return; }
    const chunks = [];
    try { recorder = new MediaRecorder(microphone); }
    catch (error) { microphone.getTracks().forEach(t => t.stop()); microphone = null; throw error; }
    recorder.ondataavailable = e => { if (e.data.size) chunks.push(e.data); };
    recorder.onstop = () => {
      clearInterval(recordTimer); recordTimer = null;
      microphone?.getTracks().forEach(t => t.stop()); microphone = null;
      if (!$('#voiceLibrary')?.open) return;
      recordingBlob = new Blob(chunks, { type: recorder.mimeType }); sourceFile = null;
      consent = false;
      if (recordingUrl) URL.revokeObjectURL(recordingUrl);
      recordingUrl = URL.createObjectURL(recordingBlob); paint();
    };
    recorder.start();
    let elapsed = 0;
    recordTimer = setInterval(() => { elapsed++; const status = $('#micRecordStatus'); if (status) status.textContent = `Recording ${elapsed} / 40 seconds`;
      if (elapsed >= 40 && recorder.state === 'recording') recorder.stop(); }, 1000);
    paint();
  }

  async function wavUpload(blob) {
    const context = new (window.AudioContext || window.webkitAudioContext)(); let decoded;
    try { decoded = await context.decodeAudioData(await blob.arrayBuffer()); } finally { await context.close(); }
    if (decoded.duration < 3 || decoded.duration > 40) throw new Error('Use 3-40 seconds of clear speech.');
    const rate = 16000, off = new OfflineAudioContext(1, Math.ceil(decoded.duration * rate), rate);
    const source = off.createBufferSource(); source.buffer = decoded; source.connect(off.destination); source.start();
    const samples = (await off.startRendering()).getChannelData(0), buffer = new ArrayBuffer(44 + samples.length * 2), view = new DataView(buffer);
    const str = (at, value) => [...value].forEach((c, i) => view.setUint8(at + i, c.charCodeAt(0)));
    str(0, 'RIFF'); view.setUint32(4, 36 + samples.length * 2, true); str(8, 'WAVE'); str(12, 'fmt ');
    view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true); view.setUint32(24, rate, true);
    view.setUint32(28, rate * 2, true); view.setUint16(32, 2, true); view.setUint16(34, 16, true); str(36, 'data'); view.setUint32(40, samples.length * 2, true);
    samples.forEach((value, i) => view.setInt16(44 + i * 2, Math.max(-1, Math.min(1, value)) * 32767, true));
    let binary = ''; const bytes = new Uint8Array(buffer);
    for (let i = 0; i < bytes.length; i += 32768) binary += String.fromCharCode(...bytes.subarray(i, i + 32768));
    return 'data:audio/wav;base64,' + btoa(binary);
  }

  function recording(v, ref) {
    const engines = engineInfo().engines.filter(e => e.caps.clone);
    const speaker = h('select', { 'aria-label': 'Recording speaking model' }, engines.map(e => h('option', { value: e.id }, e.label)));
    speaker.value = ref.snapshot.speaker || ref.snapshot.engine;
    const active = recorder?.state === 'recording';
    return h('details', { class: 'card stack voice-recording', open: active || !!recordingBlob || !!sourceFile }, h('summary', {}, 'Add a recording version'),
      h('div', { class: 'row wrap' }, btn('Record microphone', '', safe(recordMic), { cls: 'small', disabled: active }),
        btn('Stop recording', '', () => { if (recorder?.state === 'recording') recorder.stop(); }, { cls: 'small', disabled: !active })),
      h('p', { id: 'micRecordStatus', class: 'hint' }, active ? 'Recording…' : 'Record or upload 3-40 seconds of one speaker.'),
      h('input', { type: 'file', accept: 'audio/*', 'aria-label': 'New reference recording', onchange: e => {
        sourceFile = e.target.files[0] || null; recordingBlob = null; consent = false;
        if (recordingUrl) URL.revokeObjectURL(recordingUrl);
        recordingUrl = sourceFile ? URL.createObjectURL(sourceFile) : ''; paint();
      } }),
      sourceFile ? h('span', { class: 'hint' }, `Selected: ${sourceFile.name}`) : null,
      recordingUrl ? h('audio', { src: recordingUrl, controls: true, 'aria-label': 'New reference recording preview' }) : null,
      field('Exact recording transcript', h('textarea', { rows: 3, 'aria-label': 'New recording transcript', value: recordTranscript, oninput: e => { recordTranscript = e.target.value; } })),
      field('Recording origin / speaker', h('input', { type: 'text', value: origin, 'aria-label': 'Recording origin', oninput: e => { origin = e.target.value; } })),
      field('Speaks with', speaker),
      h('label', { class: 'check' }, h('input', { type: 'checkbox', checked: consent, onchange: e => { consent = e.target.checked; } }), 'I have permission to use this recording'),
      btn('Save recording version', 'save', action(async () => {
        if (active) throw new Error('Stop the recording before saving.');
        if (!consent) throw new Error('Confirm permission to use this recording.');
        if (!sourceFile && !recordingBlob) throw new Error('Record or choose an audio file first.');
        const result = await api('POST', `/api/voice/library/${v.id}/recordings`, { audio: await wavUpload(sourceFile || recordingBlob),
          transcript: recordTranscript, origin, consent, speaker: speaker.value });
        await refresh(); choose(selected, result.version.id);
      }), { cls: 'small' }));
  }

  function paint() {
    const dlg = $('#voiceLibrary'); if (!dlg?.open) return;
    const v = entry(), ref = version();
    dlg.replaceChildren(h('div', { class: 'about-body' }, h('header', { class: 'about-head' }, h('div', { class: 'grow' },
      h('h2', { id: 'voiceLibraryTitle' }, 'Voice library'), h('p', { class: 'hint' }, 'Reusable voices and preserved reference versions')),
      btn('Close', '', () => { if (!saving) dlg.close(); }, { cls: 'small', disabled: saving })),
      h('p', { id: 'voiceLibraryBusy', class: 'notice', hidden: !saving, role: 'status' }, 'Saving…'),
      h('fieldset', { id: 'voiceLibraryControls', class: 'voice-library-controls', disabled: saving },
        owner?.voice?.reference || owner?.voice?.preset_identity ? btn('Save assigned voice to library', 'save', safe(openSave), { cls: 'small' }) : null,
        h('div', { id: 'voiceLibraryBody', class: 'voice-library-grid' }, list(), v && ref ? h('div', { class: 'stack' }, details(v, ref), assignment(ref)) : null,
          ref ? h('div', { class: 'stack' }, ref.snapshot.preset_identity
            ? h('section', { class: 'card stack' }, h('h3', {}, 'Preset identity'), h('strong', {}, ref.snapshot.preset_identity.speaker),
              h('p', { class: 'hint' }, 'This identity renders directly from its pinned model preset. An audition clip is optional; it is never a cloning reference.'),
              ref.audio_url ? h('audio', { controls: true, src: ref.audio_url, 'aria-label': 'Preset audition' }) : null)
            : [referenceEditor(ref), recording(v, ref)]) : null))));
    if (ref && !ref.snapshot.preset_identity) waveform(ref);
  }
  return { open };
}
