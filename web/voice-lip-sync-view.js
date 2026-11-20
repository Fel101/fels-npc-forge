import { $, api, btn, fullName, h, safe, toast } from './core.js';
import { visemeWeightsAt } from './viseme-track.js';
import { createRigPainter, mouthWeightsAt } from './character-rig-player.js';
import { createCharacterRigEditor } from './character-rig-editor.js';
import { createMeshRigEditor } from './mesh-rig-editor.js';

const targets = { sil: [32, 1], PP: [25, 2], FF: [28, 6], TH: [30, 10], DD: [30, 11], kk: [33, 14],
  CH: [22, 14], SS: [31, 5], nn: [30, 12], RR: [24, 10], aa: [32, 26], E: [39, 12], ih: [37, 7], oh: [21, 22], ou: [14, 15] };

export function createVoiceLipSync({ selectedNpc, onSaved, engineInfo }) {
  let owner, settings, overrideRows, selectedSource = '', result = null, busy = false, frame = null;
  let rigEditor = null, painter = null, painterKey = '', manualPose = null;
  let meshEditor=null,meshPreview=null,meshLoading=false,meshKey='',previewType='portrait';
  let meshGeneration=0;
  const defaults = () => engineInfo().alignment_caps.defaults;
  const normalize = value => ({ ...structuredClone(defaults()), ...structuredClone(value || {}) });
  const field = (name, control) => h('label', { class: 'stack' }, h('span', { class: 'lbl' }, name), control);
  const invalidate = () => { result = null; $('#lipSyncAudio')?.pause(); stop(); $('#lipSyncResult')?.remove();
    const button = $('#lipSyncUpdate button'); if (button) button.disabled = !canUpdate(); };
  const stop = () => { if (frame != null) cancelAnimationFrame(frame); frame = null; };
  const config = () => ({ ...settings, phoneme_overrides: Object.fromEntries(overrideRows.map(row => [row.word.trim().toLowerCase(), row.phones.trim().toUpperCase().split(/\s+/)])) });
  const canUpdate = () => engineInfo().alignment_caps.available && settings.enabled &&
    (selectedSource.startsWith('line:') || selectedSource.startsWith('test:')) &&
    JSON.stringify(config()) === JSON.stringify(normalize(owner.voice?.studio?.lip_sync));
  const action = fn => safe(async () => {
    if (busy) return;
    busy = true; $('#lipSyncControls').disabled = true;
    try { await fn(); } finally { busy = false; if ($('#voiceLipSync')?.open) $('#lipSyncControls').disabled = false; }
  });
  async function open() {
    owner = selectedNpc(); if (!owner?.id) throw new Error('Choose a saved character');
    settings = normalize(owner.voice?.studio?.lip_sync);
    overrideRows = Object.entries(settings.phoneme_overrides).map(([word, phones]) => ({ word, phones: phones.join(' ') }));
    result = null; selectedSource = ''; stop(); manualPose = null; painter?.dispose(); painter = null; painterKey = '';
    rigEditor = engineInfo().character_rig_schema === 1 ? createCharacterRigEditor({ npcId: () => owner.id,
      onSaved: async next => { owner = next; await onSaved(next); }, onChange: refreshArtwork }) : null;
    if (rigEditor) await rigEditor.load();
    meshGeneration++;meshPreview?.dispose();meshPreview=null;meshKey='';meshLoading=false;previewType='portrait';
    meshEditor=engineInfo().mesh_rig_schema===1?createMeshRigEditor({npcId:()=>owner.id,
      onSaved:async next=>{owner=next;await onSaved(next);},onChange:refreshArtwork}):null;
    if(meshEditor)await meshEditor.load();
    let dialog = $('#voiceLipSync');
    if (!dialog) {
      dialog = h('dialog', { id: 'voiceLipSync', class: 'about voice-studio-dialog', 'aria-labelledby': 'lipSyncTitle' });
      dialog.oncancel = e => { if (busy || rigEditor?.busy() || meshEditor?.busy()) e.preventDefault(); else stop(); };
      dialog.onclose = () => { $('#lipSyncAudio')?.pause(); stop(); painter?.dispose(); painter = null; painterKey = '';meshGeneration++;meshPreview?.dispose();meshPreview=null;meshKey=''; }; document.body.append(dialog);
    }
    paint(); dialog.showModal();
  }
  function refreshArtwork() {
    const host = $('#characterRigControls');
    if (host && rigEditor) {
      const expanded = host.querySelector('details')?.open;
      host.replaceChildren(rigEditor.view()); if (expanded) host.querySelector('details').open = true;
    }
    const meshHost=$('#meshRigControls');
    if(meshHost&&meshEditor){const expanded=meshHost.querySelector('details')?.open;meshHost.replaceChildren(meshEditor.view());if(expanded)meshHost.querySelector('details').open=true;}
    const container = $('#characterLipPreview'); if (!container || !rigEditor) return;
    if(previewType==='mesh'){
      painter?.dispose();painter=null;painterKey='';
      const rig=meshEditor?.rig();
      if(!rig?.source || ['stale','missing'].includes(rig.status)){container.replaceChildren(h('p',{class:'hint'},rig?.reason||'Choose a rigged model'));return;}
      const key=rig.source.sha256;
      if(meshPreview&&meshKey===key&&$('#meshLipCanvas')){meshPreview.setRig(rig);drawCharacter();return;}
      if(meshLoading)return;
      meshPreview?.dispose();meshPreview=null;meshKey=key;meshLoading=true;
      const generation=++meshGeneration,modelNpc=owner;
      const canvas=h('canvas',{id:'meshLipCanvas',style:{width:'100%',maxWidth:'420px',height:'420px'},'aria-label':'3D character lip-sync preview'});
      const status=h('p',{class:'hint'},'Loading the character model…');
      container.replaceChildren(canvas,status,h('p',{id:'characterMouthCue',class:'hint'}));
      import('./mesh-mouth-preview.js').then(async({createMeshMouthPreview})=>{
        const created=await createMeshMouthPreview(canvas,status,modelNpc,rig);
        if(generation!==meshGeneration||!canvas.isConnected||!$('#voiceLipSync')?.open||previewType!=='mesh'){created.dispose();return;}
        meshPreview=created;drawCharacter();
      }).catch(error=>{status.textContent=error.message;}).finally(()=>{
        if(generation===meshGeneration){meshLoading=false;if(previewType==='mesh'&&$('#voiceLipSync')?.open&&!$('#meshLipCanvas'))refreshArtwork();}
      });
      return;
    }
    meshPreview?.dispose();meshPreview=null;meshKey='';
    const rig = rigEditor.rig(), key = rig ? `${rig.version}:${rigEditor.kind()}` : '';
    if (!rig || rig.status !== 'ready') {
      painter?.dispose(); painter = null; painterKey = '';
      container.replaceChildren(h('p', { class: 'hint' }, 'Configure and enable character mouth artwork to preview this NPC.')); return;
    }
    if (key !== painterKey || !painter || !$('#characterLipCanvas')) {
      painter?.dispose(); painterKey = key;
      const canvas = h('canvas', { id: 'characterLipCanvas', 'aria-label': 'Character lip-sync preview', style: { width: '100%', maxWidth: '420px' } });
      container.replaceChildren(h('strong', {}, `${fullName(owner)} · ${rigEditor.kind()} mouth preview`), canvas,
        h('p', { id: 'characterMouthCue', class: 'hint' }),
        field('Preview mouth pose', h('select', { 'aria-label': 'Preview mouth pose', onchange: e => {
          $('#lipSyncAudio')?.pause(); stop(); manualPose = e.target.value; drawCharacter();
        } }, ['sil', ...Object.keys(rig.mapping).filter(shape => shape !== 'sil')].map(shape => h('option', { value: shape, selected: shape === (manualPose || 'sil') }, shape)))),
        h('p', { class: 'hint' }, 'Play or seek speech to animate the mapped character images. Choose a mouth pose to inspect its artwork.'));
      painter = createRigPainter(canvas, rig);
    }
    drawCharacter();
  }
  function drawCharacter(time = $('#lipSyncAudio')?.currentTime || 0) {
    const weights = manualPose ? { [manualPose]: 1 } : result ? mouthWeightsAt(result.lip_sync, result.mouth, time) : { sil: 1 };
    painter?.draw(weights);
    meshPreview?.draw(weights,time,result?.lip_sync,result?.mouth||[]);
    const cue = $('#characterMouthCue');
    if (cue) {
      const lip = result?.lip_sync, word = lip?.words.find(row => row.start <= time && time < row.end)?.word || '';
      const phone = lip?.phonemes.find(row => row.start <= time && time < row.end)?.phone || '';
      const shape = Object.entries(weights).sort((a,b) => b[1]-a[1])[0]?.[0] || 'sil';
      cue.textContent = manualPose ? `Artwork pose: ${manualPose}` : `${time.toFixed(2)}s · ${word} · ${phone} · ${shape}`;
    }
  }
  function sources() {
    const voice = owner.voice || {};
    return [...Object.entries(owner.voice_lines || {}).map(([id, row]) => [`line:${id}`, `Dialogue: ${row.text.slice(0, 70)}`]),
      ...Object.entries(voice.tests || {}).map(([id, row]) => [`test:${id}`, `Test: ${row.text.slice(0, 70)}`]),
      ...(voice.takes || []).map(row => [`take:${row.id}`, `Take ${row.n}: ${row.text.slice(0, 45)}`]),
      voice.reference?.file && ['reference', 'Assigned reference / audition']].filter(Boolean);
  }
  function draw(time = 0) {
    drawCharacter(time);
    const canvas = $('#visemeTimingCanvas'); if (!canvas || !result) return;
    const lip = result.lip_sync, ctx = canvas.getContext('2d');
    const weights = visemeWeightsAt(lip.visemes, time);
    let width = 0, height = 0;
    for (const [shape, weight] of Object.entries(weights)) { width += (targets[shape]?.[0] || 32) * weight; height += (targets[shape]?.[1] || 1) * weight; }
    ctx.clearRect(0, 0, 420, 240); ctx.fillStyle = '#18222a'; ctx.fillRect(0, 0, 420, 240);
    ctx.strokeStyle = '#d0b999'; ctx.lineWidth = 3; ctx.beginPath(); ctx.ellipse(210, 110, 75, 94, 0, 0, Math.PI * 2); ctx.stroke();
    ctx.fillStyle = '#d0b999'; ctx.beginPath(); ctx.arc(180, 84, 4, 0, Math.PI * 2); ctx.arc(240, 84, 4, 0, Math.PI * 2); ctx.fill();
    ctx.beginPath(); ctx.moveTo(210, 96); ctx.lineTo(202, 118); ctx.lineTo(213, 119); ctx.stroke();
    ctx.fillStyle = '#06090c'; ctx.beginPath(); ctx.ellipse(210, 150, width, Math.max(1, height), 0, 0, Math.PI * 2); ctx.fill();
    ctx.strokeStyle = '#ddadad'; ctx.stroke(); ctx.fillStyle = '#e8e1d7';
    if (height > 5 && (weights.FF || weights.TH || weights.E || weights.ih || weights.SS)) ctx.fillRect(210 - width * .6, 151 - height, width * 1.2, 3);
    const word = lip.words?.find(row => row.start <= time && time < row.end)?.word || '';
    const phone = lip.phonemes?.find(row => row.start <= time && time < row.end)?.phone || '';
    const shape = Object.entries(weights).sort((a, b) => b[1] - a[1])[0]?.[0] || 'sil';
    ctx.font = '14px Segoe UI'; ctx.textAlign = 'center'; ctx.fillText(`${time.toFixed(2)}s · ${word} · ${phone} · ${shape}`, 210, 225);
  }
  function animate() { draw($('#lipSyncAudio')?.currentTime || 0); if ($('#lipSyncAudio') && !$('#lipSyncAudio').paused) frame = requestAnimationFrame(animate); }
  function preview() {
    if (!result) return null;
    const lip = result.lip_sync;
    return h('section', { id: 'lipSyncResult', class: 'card stack' },
      h('strong', {}, lip.status === 'aligned' ? 'Word and phoneme checks passed' : lip.status === 'word_aligned' ? 'Word timing only · amplitude mouth fallback' : 'Amplitude mouth fallback'),
      h('p', { class: 'hint' }, lip.reason),
      h('p', {}, `${lip.seconds.toFixed(2)} seconds · ${lip.words.length} words · ${lip.phonemes.length} phonemes · final audio snapshot`),
      h('audio', { id: 'lipSyncAudio', controls: true, src: result.audio_url, 'aria-label': 'Lip-sync speech preview',
        onplay: () => { manualPose = null; stop(); animate(); }, onpause: () => { stop(); draw($('#lipSyncAudio').currentTime); },
        onseeked: () => { manualPose = null; draw($('#lipSyncAudio').currentTime); }, onended: () => { stop(); draw(lip.seconds); } }),
      field('Preview time (seconds)', h('input', { type: 'number', min: 0, max: lip.seconds, step: .01, value: 0,
        'aria-label': 'Preview time (seconds)', oninput: e => {
          const time = Math.max(0, Math.min(lip.seconds, Number(e.target.value) || 0));
          manualPose = null; if ($('#lipSyncAudio')) $('#lipSyncAudio').currentTime = time; draw(time);
        } })),
      rigEditor ? h('section', { id: 'characterLipPreview', class: 'stack' }) : null,
      lip.visemes ? h('div', { class: 'stack' }, h('canvas', { id: 'visemeTimingCanvas', width: 420, height: 240,
        style: { width: '100%', maxWidth: '420px' }, 'aria-label': 'Viseme timing diagram' }),
        h('p', { class: 'hint' }, engineInfo().character_rig_schema === 1
          ? 'Compare the model timing diagram with the character’s mapped mouth artwork. Several visemes can share a pose; timing and artwork still need visual review.'
          : 'Play or seek the audio to inspect the model timing diagram. Character mouth artwork requires the updated Forge backend.')) : null,
      lip.words.length ? h('div', { class: 'lip-sync-table-scroll' }, h('table', {}, h('thead', {}, h('tr', {}, ['Spoken word', 'Start (s)', 'End (s)'].map(s => h('th', {}, s)))),
        h('tbody', {}, lip.words.map(word => h('tr', {}, h('td', {}, word.word), h('td', {}, word.start.toFixed(2)), h('td', {}, word.end.toFixed(2))))))) : null);
  }
  function paint() {
    $('#lipSyncAudio')?.pause(); stop(); const dialog = $('#voiceLipSync'), caps = engineInfo().alignment_caps;
    const options = sources(); if (!options.some(([id]) => id === selectedSource)) selectedSource = options[0]?.[0] || '';
    const picker = h('select', { 'aria-label': 'Lip-sync speech clip', onchange: e => { selectedSource = e.target.value; invalidate(); } }, options.map(([id, label]) => h('option', { value: id }, label)));
    picker.value = selectedSource;
    dialog.replaceChildren(h('div', { class: 'about-body' }, h('header', { class: 'about-head' },
      h('h2', { id: 'lipSyncTitle', class: 'grow' }, `${fullName(owner)} · Lip sync`), btn('Close', '', () => { if (!busy && !rigEditor?.busy()&&!meshEditor?.busy()) dialog.close(); }, { cls: 'small' })),
      h('fieldset', { id: 'lipSyncControls', class: 'stack', disabled: busy },
        h('p', { class: 'hint' }, 'Align the final delivered audio on the CPU. Words and English phonemes must pass independent speech checks. Failed checks retain the amplitude mouth track; model timings still need listening and visual review.'),
        caps.available ? null : h('p', { class: 'notice' }, caps.reason),
        h('label', { class: 'check' }, h('input', { type: 'checkbox', checked: settings.enabled, onchange: e => { settings.enabled = e.target.checked; invalidate(); } }), 'Enable word and phoneme lip sync'),
        h('p', { class: 'hint' }, 'When enabled, new speech is aligned automatically. Existing clips can update their timing without changing their audio. Disabling this mode uses the amplitude fallback.'),
        engineInfo().game_export_schema === 1 ? h('p', { class: 'hint' }, 'Voice and scene packs include Godot playback helpers, Unreal mouth curves, and editable character target mappings. Exported clips retain their timing or fallback status.') : null,
        h('div', { class: 'form-grid' }, ...[['fps', 'Viseme frame rate', 24, 120], ['blend_ms', 'Transition blend (ms)', 0, 120]].map(([key, label, min, max]) =>
          field(label, h('input', { type: 'number', value: settings[key], min, max, step: 1, 'aria-label': label,
            oninput: e => { settings[key] = Number(e.target.value); invalidate(); } })))),
        h('details', {}, h('summary', {}, 'Configure phoneme → viseme mapping'), h('div', { class: 'form-grid' }, caps.phones.map(phone =>
          field(phone, h('select', { 'aria-label': `Viseme for ${phone}`, value: settings.mapping[phone], onchange: e => { settings.mapping[phone] = e.target.value; invalidate(); } },
            caps.visemes.map(shape => h('option', { value: shape, selected: settings.mapping[phone] === shape }, shape))))))),
        h('details', {}, h('summary', {}, 'English phonemes for invented spoken tokens'),
          h('p', { class: 'hint' }, 'Use lowercase spoken tokens and space-separated ARPABET phones, for example meer: M IH R. These guide alignment only; pronunciation rules control what the voice says.'),
          overrideRows.map((row, i) => h('div', { class: 'form-grid' },
            field('Spoken token', h('input', { value: row.word, 'aria-label': `Alignment token ${i + 1}`, oninput: e => { row.word = e.target.value; invalidate(); } })),
            field('ARPABET phones', h('input', { value: row.phones, 'aria-label': `Alignment phones ${i + 1}`, oninput: e => { row.phones = e.target.value; invalidate(); } })),
            btn('Remove token', '', () => { overrideRows.splice(i, 1); invalidate(); paint(); }, { cls: 'small' }))),
          btn('Add token phonemes', 'plus', () => { overrideRows.push({ word: '', phones: '' }); invalidate(); paint(); }, { cls: 'small', disabled: overrideRows.length >= 200 })),
        btn('Save lip-sync settings', 'save', action(async () => {
          owner = await api('PUT', `/api/npcs/${owner.id}/voice/studio`, { ...(owner.voice?.studio || {}), lip_sync: config() });
          settings = normalize(owner.voice?.studio?.lip_sync); await onSaved(owner); paint(); toast('Lip-sync settings saved');
        }), { cls: 'small primary' }),
        rigEditor ? h('section', { id: 'characterRigControls' }, rigEditor.view()) : null,
        meshEditor?h('section',{id:'meshRigControls'},meshEditor.view()):null,
        meshEditor?field('Character preview type',h('select',{'aria-label':'Character preview type',onchange:e=>{previewType=e.target.value;refreshArtwork();}},
          h('option',{value:'portrait',selected:previewType==='portrait'},'Portrait / sprite artwork'),h('option',{value:'mesh',selected:previewType==='mesh'},'3D model'))):null,
        rigEditor && !result ? h('section', { id: 'characterLipPreview', class: 'stack' }) : null,
        options.length ? field('Existing speech', picker) : h('p', {}, 'Generate speech first to analyze its timing.'),
        h('div', { class: 'row wrap' }, btn('Analyze speech timing', '', action(async () => {
          result = await api('POST', `/api/npcs/${owner.id}/voice/lip-sync/preview`, { source: selectedSource, settings: { ...config(), enabled: true } }); paint(); draw();
        }), { cls: 'small', disabled: !caps.available || !options.length }),
        h('span', { id: 'lipSyncUpdate' }, btn('Update saved clip timing', '', action(async () => {
          owner = await api('POST', `/api/npcs/${owner.id}/voice/lip-sync`, { source: selectedSource }); await onSaved(owner); toast('Clip timing updated; original audio preserved');
        }), { cls: 'small', disabled: !canUpdate() }))), preview())));
    refreshArtwork(); if (result) draw();
  }
  return { open };
}
