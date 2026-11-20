import { $, S, api, btn, h, safe, toast } from './core.js';
import { createRigPainter, mouthWeightsAt } from './character-rig-player.js';
import { MeshMouthPlayer, softMouthWeightsAt } from './mesh-mouth-player.js';
import { SceneClock } from './scene-clock.js';
import { CAMERA_SHOTS, applyCamera } from './scene-camera.js';
import { readinessView, timelineView, timeLabel } from './scene-director-view.js';
import { SceneHistory, authoringCopy } from './scene-history.js';
import { stagingView, castView, propsView } from './scene-staging-view.js';
import { takesView } from './scene-takes-view.js';
import { actingView, cameraMovementFields } from './scene-acting-view.js';
import { SceneSoundPlayer, soundDataUrl } from './scene-sound.js';
import { soundView } from './scene-sound-view.js';
import { writerView } from './scene-writer-view.js';

// Scene editing and playback; speech uses NPC Forge's voice queue, while MP4 composition stays on the CPU.
const BACKDROPS = [['slate', 'Slate'], ['ember', 'Ember'], ['forest', 'Forest'], ['violet', 'Violet']];
const BACKDROP_COLORS = { slate: '#15202b', ember: '#39221f', forest: '#142b25', violet: '#29213b' };
const MOODS = ['neutral', 'happy', 'excited', 'sad', 'angry', 'surprised', 'scared', 'whisper', 'flirty', 'tired'];
const EXPRESSIONS = ['', 'happy', 'sad', 'angry', 'surprised', 'scared', 'laughing', 'tired', 'smug', 'embarrassed', 'thinking', 'flirty'];
const SHOTS = [['split', 'Two portraits'], ['close', 'Speaker close-up'], ['sprite', 'Full-body sprites']];
const MOTIONS = [['stand', 'Stand and talk'], ['walk', 'Walk to mark'], ['wave', 'Wave']];
const SCENE_VOICES = [['', 'Use character voice'], ['af_heart', 'Heart · American woman'],
  ['af_bella', 'Bella · American woman'], ['af_nicole', 'Nicole · American woman'],
  ['bf_emma', 'Emma · British woman'], ['am_michael', 'Michael · American man'],
  ['am_puck', 'Puck · American man'], ['bm_george', 'George · British man']];
const DEFAULT_BACKGROUND = 'An abandoned forest checkpoint at dusk, an empty gravel path leading through a rusted gate, cinematic natural light';
export function createSceneController({ render, afterSubmit, openNpc }) {
  let scenes = [], draft = null, npcs = {}, timeline = null, capability = null, savedFingerprint = '';
  let playing = false, request = 0, audio = null, currentBeat = '';
  let renderJob = null;
  let stage3d = null, stageToken = 0;
  let recording = false;
  let exportProgress = '';
  const rigPainters = new Map();
  const meshMouths = new MeshMouthPlayer();
  let voiceDirections = {};
  let frameClock = new SceneClock(), cursor = 0, selectedBeat = '', compiledFingerprint = '', blocking = null;
  let audioToken = 0;
  const history = new SceneHistory(typeof localStorage === 'undefined' ? null : localStorage);
  let historyId = undefined, recoveredDraft = null;
  let stageItems = [];
  let writerProposal = null;
  let previewScroll = 0;
  const openSections = new Set();
  let deferredSceneRefresh = false;
  let startingPlayback = false;
  const sectionKey = node => `${node.closest('.scene-beat')?.dataset.beat || 'scene'}:${node.className}`;
  const soundPlayer = new SceneSoundPlayer(error => { pause(); toast('Sound playback stopped: ' + error.message, 'error'); });

  const name = npc => npc ? `${npc.profile?.first_name || ''} ${npc.profile?.last_name || ''}`.trim() : 'Missing NPC';
  const sceneFile = (scene, rel) => `/api/scenes/${scene.id}/files/${rel}`;
  const npcFile = (id, rel, stamp) => `/api/npcs/${id}/files/${rel}?v=${encodeURIComponent(stamp || '')}`;
  const fingerprint = scene => JSON.stringify(authoringCopy(scene));
  const editorBody = scene => ({...authoringCopy(scene), ...(scene.id ? {id: scene.id, revision: scene.revision} : {})});
  const dirty = () => !draft?.id || fingerprint(draft) !== savedFingerprint;
  function updateSoundStatus() {
    for (const row of soundPlayer.status()) {
      const output = document.querySelector(`.scene-track-status[data-track="${row.id}"]`);
      if (output) output.textContent = `${row.loading ? 'Loading' : row.queued ? 'Queued' : row.playing ? 'Playing' : 'Paused'} · ${timeLabel(row.time)} · ${Math.round(row.volume * 100)}% volume`;
    }
  }

  function resetHistory() {
    history.reset(draft); historyId = draft.id || 'new'; recoveredDraft = history.recoverable(draft);
  }
  function captureEdit() {
    if (!draft) return;
    history.capture(draft);
    const undo = $('#sceneUndo'), redo = $('#sceneRedo');
    if (undo) undo.disabled = history.undoStack.length < 2;
    if (redo) redo.disabled = !history.redoStack.length;
  }
  async function travelHistory(direction) {
    pause();
    if (!history[direction](draft)) return;
    await loadCast(); await refreshPreview();
  }
  async function recoverDraft() {
    if (!recoveredDraft) return;
    history.apply(draft, recoveredDraft.value); recoveredDraft = null;
    await loadCast(); await refreshPreview();
  }
  async function duplicateScene() {
    if (dirty()) return toast('Save this scene before making a copy.', 'error');
    const copy = await api('POST', `/api/scenes/${draft.id}/duplicate`, {});
    await load(copy.id); toast('Scene copy created');
  }

  function pause() {
    const wasPlaying = playing;
    if (wasPlaying && timeline) {
      const beat = timeline.beats.find(row => row.id === currentBeat);
      cursor = Math.max(0, Math.min(timeline.frames - 1, frameClock.sample(performance.now(), beat, audio)));
    }
    playing = false;
    cancelAnimationFrame(request);
    audio?.pause();
    soundPlayer.pause();
    updateSoundStatus();
    frameClock.pause(cursor);
    updateTransport();
    if (wasPlaying && timeline) stageFrame(cursor);
  }

  function stop(reset = true) {
    pause(); audioToken++;
    if (audio) { audio.pause(); audio = null; }
    currentBeat = '';
    if (reset) { cursor = 0; frameClock.seek(0); }
    meshMouths.clear();
    if (stage3d) stage3d.renderer.render(stage3d.scene, stage3d.camera);
    for (const entry of rigPainters.values()) entry.painter.draw({sil: 1});
    updateTransport();
  }

  function disposeStage() {
    for (const clip of document.querySelectorAll('.scene-takes audio')) clip.pause();
    meshMouths.clear();
    stageToken++;
    stage3d?.dispose();
    stage3d = null;
    for (const value of rigPainters.values()) value.painter.dispose(); rigPainters.clear();
  }

  async function showList() {
    stop(); disposeStage(); draft = null; timeline = null; renderJob = null;
    S.view = 'scenes'; render();
    scenes = await api('GET', '/api/scenes');
    capability = await api('GET', '/api/scenes/capabilities');
    render();
  }

  async function load(id) {
    stop();
    disposeStage();
    renderJob = null;
    writerProposal = null;
    previewScroll = 0;
    draft = await api('GET', `/api/scenes/${id}`);
    savedFingerprint = fingerprint(draft);
    resetHistory();
    await loadCast();
    timeline = await api('GET', `/api/scenes/${id}/timeline`);
    compiledFingerprint = fingerprint(draft);
    capability ??= await api('GET', '/api/scenes/capabilities');
    S.view = 'scenes'; render();
  }

  async function loadCast() {
    const info=await api('GET','/api/voice/engines');
    voiceDirections=Object.fromEntries(info.engines.map(row=>[row.id,row.caps.direction===true]));
    npcs = {};
    for (const id of draft.cast) {
      try { npcs[id] = await api('GET', `/api/npcs/${id}`); } catch { /* missing NPC is shown in the editor */ }
    }
    const items = await api('GET', '/api/items'); stageItems = Array.isArray(items) ? items : [];
  }

  async function addCast(id) {
    if (!id || draft.cast.includes(id) || draft.cast.length >= 6) return;
    draft.cast.push(id); await loadCast(); await refreshPreview();
  }
  function remapCast(oldId, newId) {
    if (draft.actors?.[oldId]) { if (newId) draft.actors[newId] = draft.actors[oldId]; delete draft.actors[oldId]; }
    const fallback = newId || draft.cast[0];
    for (const beat of draft.beats) {
      if (beat.camera_target === oldId) beat.camera_target = fallback;
      if (beat.speaker_id === oldId) { beat.speaker_id = fallback; beat.line_id = ''; beat.performance_id = null; }
      if (beat.acting?.[oldId]) delete beat.acting[oldId];
      for (const direction of Object.values(beat.acting || {})) for (const key of ['facing', 'look_at']) if (direction[key] === oldId) direction[key] = fallback;
    }
    if (draft.voice_bindings) delete draft.voice_bindings[oldId];
    if (draft.voice_overrides) delete draft.voice_overrides[oldId];
  }
  async function removeCast(id) {
    if (draft.cast.length <= 2) return;
    draft.cast = draft.cast.filter(value => value !== id); remapCast(id, null);
    await loadCast(); await refreshPreview();
  }
  function stagingPanel() {
    return stagingView({scene: draft, npcs, beat: draft.beats.find(beat => beat.id === selectedBeat) || draft.beats[0],
      commit: refreshPreview, preview: () => stageFrame(cursor), pause});
  }

  function create() {
    if (S.library.length < 2) return toast('Make at least two NPCs first', 'error');
    stop();
    S.view = 'scenes';
    const cast = [...S.library.filter(n => n.has?.rig_3d), ...S.library.filter(n => !n.has?.rig_3d)].slice(0, 2).map(n => n.id);
    draft = { title: 'New 3D scene', cast, aspect: 'landscape', backdrop: 'slate', mode: 'stage3d',
      background_prompt: DEFAULT_BACKGROUND,
      beats: [{ id: `beat-${Date.now().toString(36)}`, kind: 'line', speaker_id: cast[0],
        text: 'What brings you here?', layout: 'split', line_id: '', mood: 'neutral', expression: '',
        motion: 'walk', walk_to: [0, 0] }] };
    timeline = null; writerProposal = null; resetHistory(); loadCast().then(render).catch(error => toast(error.message, 'error'));
    render();
  }

  function createFromPack(pack) {
    const ids = (pack.members || []).map(member => member.npc_id).filter(id => S.library.some(npc => npc.id === id));
    if (ids.length < 2) return toast('This cast pack needs at least two available NPCs', 'error');
    stop(); S.view = 'scenes'; timeline = null; renderJob = null;
    draft = { title: `${pack.name} scene`, cast: ids.slice(0, 2), aspect: 'landscape', backdrop: 'slate',
      mode: 'stage3d', background_prompt: DEFAULT_BACKGROUND,
      beats: [{ id: `beat-${Date.now().toString(36)}`, kind: 'line', speaker_id: ids[0],
        text: 'What brings you here?', layout: 'split', line_id: '', mood: 'neutral', expression: '',
        motion: 'walk', walk_to: [0, 0] }] };
    writerProposal = null; resetHistory(); render();
    loadCast().then(render).catch(error => toast(error.message, 'error'));
  }

  function createWithNpc(npc) {
    const partner = S.library.find(item => item.id !== npc.id);
    if (!partner) return toast('Make one more NPC to start a scene', 'error');
    stop(); S.view = 'scenes'; timeline = null; renderJob = null;
    draft = { title: `${name(npc)} scene`, cast: [npc.id, partner.id], aspect: 'landscape', backdrop: 'slate',
      mode: 'stage3d', background_prompt: DEFAULT_BACKGROUND,
      beats: [{ id: `beat-${Date.now().toString(36)}`, kind: 'line', speaker_id: npc.id,
        text: 'What brings you here?', layout: 'split', line_id: '', mood: 'neutral', expression: '',
        motion: 'walk', walk_to: [0, 0] }] };
    writerProposal = null; resetHistory(); render();
    loadCast().then(render).catch(error => toast(error.message, 'error'));
  }

  function field(label, control) { return h('label', { class: 'scene-field' }, h('span', {}, label), control); }
  function select(items, value, onchange, attributes = {}) {
    const el = h('select', { ...attributes, onchange: safe(event => onchange(event.target.value)) },
      items.map(([id, label]) => h('option', { value: id }, label)));
    el.value = value || '';
    return el;
  }

  function renderList() {
    $('#view').replaceChildren(h('div', { class: 'scenes-page' },
      h('div', { class: 'scene-head' }, h('div', {}, h('h1', {}, 'Scenes'),
        h('p', { class: 'hint' }, 'Build a setting, stage rigged 3D characters, and make them walk and speak.')),
      btn('New scene', 'plus', create, { cls: 'primary', disabled: S.library.length < 2 })),
      !capability?.mp4 && h('p', { class: 'hint' }, 'MP4 export needs FFmpeg on this computer; editing and preview still work.'),
      scenes.length ? h('div', { class: 'scene-list' }, scenes.map(scene =>
        h('section', { class: 'card scene-list-card' }, h('div', {}, h('h3', {}, scene.title),
          h('p', { class: 'hint' }, `${scene.beats.length} beats · ${scene.aspect} · ${scene.cast.map(id => S.library.find(n => n.id === id)?.name || id).join(' and ')}`)),
          btn('Open', 'eye', safe(() => load(scene.id)), { cls: 'small' }))))
        : h('p', { class: 'hint' }, 'No scenes yet. Choose two NPCs to start.')));
  }

  function dialogueChoices(beat) {
    const npc = npcs[beat.speaker_id];
    const lines = Object.values(npc?.dialogue?.lines || {}).flat();
    return field('Use a saved dialogue line', select([['', 'Custom text'],
      ...lines.map(line => [line.id, line.text.slice(0, 75)])], beat.line_id, id => {
      const line = lines.find(item => item.id === id);
      beat.line_id = id;
      if (line) beat.text = line.text;
      render();
    }));
  }

  function stageMotionFields(beat) {
    if (draft.mode !== 'stage3d') return [];
    const motion = beat.motion || 'stand';
    return [field('3D action', select(MOTIONS, motion, value => {
      beat.motion = value;
      if (value === 'walk' && !beat.walk_to) beat.walk_to = [0, 0];
      render();
    })), motion === 'walk' && field('Walk to X', h('input', { type: 'number', min: -3, max: 3, step: 0.1,
      value: beat.walk_to?.[0] ?? 0, onchange: e => { beat.walk_to[0] = Number(e.target.value); } })),
    motion === 'walk' && field('Walk to depth', h('input', { type: 'number', min: -2, max: 2, step: 0.1,
      value: beat.walk_to?.[1] ?? 0, onchange: e => { beat.walk_to[1] = Number(e.target.value); } }))].filter(Boolean);
  }

  function cameraFields(beat) {
    if (draft.mode !== 'stage3d') return [];
    const update = safe(async (key, value) => { beat[key] = value; await refreshPreview(); });
    return [field('Camera shot', select(CAMERA_SHOTS, beat.camera_shot || 'wide', value => update('camera_shot', value))),
      field('Camera subject', select(draft.cast.map(id => [id, name(npcs[id])]), beat.camera_target || beat.speaker_id || draft.cast[0], value => update('camera_target', value))),
      field('Camera side', select([['left', 'Left'], ['right', 'Right']], beat.camera_side || 'left', value => update('camera_side', value))),
      ...(capability?.studio_schema ? cameraMovementFields(beat, field, select, update) : [])];
  }

  function actingFields(beat) {
    return draft.mode === 'stage3d' && capability?.studio_schema
      ? actingView({scene: draft, npcs, timeline, beat, commit: refreshPreview, field, select}) : null;
  }

  function voicePinField(id) {
    const voice = npcs[id]?.voice || {}, current = voice.preset_identity || voice.reference;
    const versions = [...(voice.reference_history || [])];
    if (current && !versions.some(v => v.id === current.id)) versions.push(current);
    const binding = draft.voice_bindings?.[id];
    if (binding && !versions.some(v => v.id === binding.reference_id)) versions.push(binding.snapshot.preset_identity
      ? { ...binding.snapshot.preset_identity, voice_snapshot: binding.snapshot } : binding.snapshot.reference);
    return field(`${name(npcs[id])} voice version`, select([['', 'Follow character voice'], ...versions.map((ref, index) =>
      [ref.id, `${ref.kind === 'model_preset' ? 'Pin preset' : 'Pin reference'} ${index + 1}${ref.id === current?.id ? ' · current' : ''}${ref.source?.startsWith('library:') ? ' · library' : ''}`])],
    binding?.reference_id || '', value => {
      draft.voice_bindings ||= {};
      if (value) {
        const ref = versions.find(ref => ref.id === value);
        const snapshot = value === current?.id ? voice : ref?.voice_snapshot;
        draft.voice_bindings[id] = { reference_id: value, ...(snapshot ? { snapshot: structuredClone(snapshot) } : {}) };
        if (draft.voice_overrides) delete draft.voice_overrides[id];
      } else delete draft.voice_bindings[id];
      // The server reconstructs the authoritative pin on save; this snapshot previews its preset choices.
      for (const beat of draft.beats) if (beat.speaker_id === id) beat.performance_id = null;
      render();
    }));
  }

  function performanceField(beat) {
    const binding = draft.voice_bindings?.[beat.speaker_id];
    const voice = binding?.snapshot || npcs[beat.speaker_id]?.voice || {};
    const choices = draft.voice_overrides?.[beat.speaker_id] ? [] : voice.studio?.presets || [];
    return field('Saved performance', select([['__default', 'Character default'], ['', 'Voice settings'],
      ...choices.map(p => [p.id, p.name])], beat.performance_id ?? '__default', value => {
      beat.performance_id = value === '__default' ? null : value;
    }));
  }

  function beatEditor(beat, index) {
    const move = async delta => { const next = index + delta; if (next < 0 || next >= draft.beats.length) return;
      [draft.beats[index], draft.beats[next]] = [draft.beats[next], draft.beats[index]]; await refreshPreview(); };
    const controls = h('div', { class: 'scene-beat-actions' },
      btn('↑', null, safe(() => move(-1)), { cls: 'small ghost', disabled: index === 0, title: 'Move up' }),
      btn('↓', null, safe(() => move(1)), { cls: 'small ghost', disabled: index === draft.beats.length - 1, title: 'Move down' }),
      btn('Remove', 'trash', safe(async () => { if (draft.beats.length > 1) { draft.beats.splice(index, 1); await refreshPreview(); } }),
        { cls: 'small ghost danger', disabled: draft.beats.length === 1 }));
    const props = { class: 'scene-beat' + (selectedBeat === beat.id ? ' selected' : ''), 'data-beat': beat.id };
    if (beat.kind === 'pause') return h('section', props,
      h('div', { class: 'scene-beat-head' }, h('strong', {}, `Beat ${index + 1} · Pause`), controls),
      field('Seconds', h('input', { type: 'number', min: 0.2, max: 8, step: 0.1, value: beat.duration,
        oninput: e => { beat.duration = Number(e.target.value); },
        onchange: safe(async e => { beat.duration = Number(e.target.value); await refreshPreview(); }) })), ...cameraFields(beat), actingFields(beat));
    if (beat.kind === 'reaction') return h('section', props,
      h('div', { class: 'scene-beat-head' }, h('strong', {}, `Beat ${index + 1} · Reaction`), controls),
      h('div', { class: 'scene-form-grid' },
        field('Character', select(draft.cast.map(id => [id, name(npcs[id])]), beat.speaker_id,
          id => { beat.speaker_id = id; render(); })),
        field('Seconds', h('input', { type: 'number', min: 0.2, max: 8, step: 0.1, value: beat.duration,
          oninput: e => { beat.duration = Number(e.target.value); },
          onchange: safe(async e => { beat.duration = Number(e.target.value); await refreshPreview(); }) })),
        field('Expression', select(EXPRESSIONS.map(id => [id, id || 'Resting']), beat.expression,
          id => { beat.expression = id; })),
        draft.mode === 'stage3d' ? [...stageMotionFields(beat), ...cameraFields(beat)] : field('Shot', select(SHOTS, beat.layout, id => { beat.layout = id; }))), actingFields(beat));
    const resolved = timeline?.beats.find(item => item.id === beat.id);
    const hasVoice = !!resolved?.voice;
    const voice=draft.voice_bindings?.[beat.speaker_id]?.snapshot || npcs[beat.speaker_id]?.voice || {};
    const voiceEngine=draft.voice_overrides?.[beat.speaker_id]?'kokoro':voice.speaker || voice.engine || 'qwen';
    const moodControl=select(MOODS.map(id=>[id,id[0].toUpperCase()+id.slice(1)]),beat.mood||'neutral',value=>{beat.mood=value;});
    moodControl.disabled=!voiceDirections[voiceEngine];
    if(moodControl.disabled)moodControl.title='This voice model does not apply text mood direction.';
    return h('section', props,
      h('div', { class: 'scene-beat-head' }, h('strong', {}, `Beat ${index + 1} · Line`), controls),
      h('div', { class: 'scene-form-grid' },
        field('Speaker', select(draft.cast.map(id => [id, name(npcs[id])]), beat.speaker_id, id => {
          if (beat.camera_target === beat.speaker_id) beat.camera_target = id;
          beat.speaker_id = id; beat.line_id = ''; render(); })),
        draft.mode === 'stage3d' ? [...stageMotionFields(beat), ...cameraFields(beat)] : field('Shot', select(SHOTS, beat.layout, value => { beat.layout = value; })),
        dialogueChoices(beat),
        field('Voice mood', moodControl),
        capability?.voice_studio_schema ? performanceField(beat) : null,
        draft.mode !== 'stage3d' && field('Expression', select(EXPRESSIONS.map(id => [id, id || 'Resting']), beat.expression || '',
          value => { beat.expression = value; }))),
      field('Line / subtitle', h('textarea', { rows: 2, value: beat.text,
        oninput: event => { beat.text = event.target.value; beat.line_id = ''; }, onchange: safe(refreshPreview) })),
      h('div', { class: 'row' },
        btn(hasVoice ? 'Redo voice' : 'Voice line', 'zap', safe(() => voiceLines([beat.id], hasVoice)),
          { cls: 'small', disabled: dirty() || !draft.id }),
        h('span', { class: 'hint' }, hasVoice ? `${resolved.voice.source === 'scene' ? 'Scene voice' : 'Saved dialogue voice'} ready · ${({viseme_frames:'mapped viseme artwork',amplitude_frames:'amplitude mouth artwork',viseme_mesh:'mapped 3D visemes',amplitude_mesh:'amplitude 3D mouth'})[resolved.voice.visual_method] || 'amplitude preview fallback'}`
          : beat.line_id ? 'Saved dialogue audio is missing or outdated' : 'Subtitles only until voiced')),
      actingFields(beat), capability?.studio_schema && takesView({scene: draft, beat, current: timeline?.takes?.[beat.id], dirty: dirty(),
        choose: chooseTake, generate: generateTakes, pause}));
  }

  async function add(kind) {
    if (draft.beats.length >= 20) return toast('A scene can have at most 20 beats', 'error');
    const id = `beat-${Math.random().toString(36).slice(2, 11)}`;
    draft.beats.push(kind === 'pause' ? { id, kind, duration: 1 }
      : kind === 'reaction' ? { id, kind, speaker_id: draft.cast[0], duration: 1.5, layout: 'close', expression: 'surprised', motion: 'wave' }
      : { id, kind, speaker_id: draft.cast[0], text: 'New line', layout: 'split', line_id: '', mood: 'neutral', expression: '',
        motion: 'stand' });
    await refreshPreview();
  }

  async function save() {
    const wasNew = !draft.id;
    const previousDraft = draft;
    const saved = await api(wasNew ? 'POST' : 'PUT', wasNew ? '/api/scenes' : `/api/scenes/${draft.id}`, editorBody(draft));
    history.clear(previousDraft);
    draft = saved;
    savedFingerprint = fingerprint(draft);
    resetHistory();
    await loadCast();
    timeline = await api('GET', `/api/scenes/${draft.id}/timeline`);
    compiledFingerprint = fingerprint(draft);
    if (wasNew) scenes = [draft, ...scenes];
    toast('Scene saved'); render();
  }

  async function remove() {
    if (!confirm(`Delete "${draft.title}" and its video exports?`)) return;
    await api('DELETE', `/api/scenes/${draft.id}`);
    await showList();
  }

  async function voiceLines(ids, redo = false) {
    if (dirty()) return toast('Save the scene before voicing lines', 'error');
    const result = await api('POST', `/api/scenes/${draft.id}/voice`, { ids, redo });
    afterSubmit({ jobs: [result.job] });
  }

  async function chooseTake(beatId, takeId, approve) {
    if (dirty()) return toast('Save this scene before selecting a voice take.', 'error');
    const updated = await api('POST', `/api/scenes/${draft.id}/takes/${beatId}/select`, {revision: draft.revision, take_id: takeId, approve});
    await load(updated.id);
  }
  async function generateTakes(beatId) {
    if (dirty()) return toast('Save this scene before generating takes.', 'error');
    const id = draft.id;
    for (let index = 0; index < 3; index++) {
      const result = await api('POST', `/api/scenes/${id}/voice`, {ids: [beatId], redo: true});
      afterSubmit({jobs: [result.job]});
    }
    toast('Three voice alternatives queued');
  }
  async function uploadSound(file) {
    if (dirty()) return toast('Save this scene before importing sound.', 'error');
    const id = draft.id, revision = draft.revision;
    const audio = await soundDataUrl(file);
    const updated = await api('POST', `/api/scenes/${id}/media`, {revision, name: file.name.slice(0, 100), audio});
    if (draft?.id === id) await load(updated.id);
  }
  async function suggestScene(brief) {
    const snapshot = fingerprint(draft), id = draft.id;
    const proposal = await api('POST', '/api/scenes/suggest', {...brief, scene: editorBody(draft)});
    if (draft?.id !== id || fingerprint(draft) !== snapshot) return toast('Scene changed while drafting; request a new suggestion.', 'error');
    writerProposal = proposal; render();
  }
  async function applyProposal(append) {
    if (!writerProposal) return;
    const original = structuredClone(draft.beats);
    draft.beats = append ? [...draft.beats, ...structuredClone(writerProposal.beats)] : structuredClone(writerProposal.beats);
    try { await refreshPreview(); writerProposal = null; render(); }
    catch (error) { draft.beats = original; render(); throw error; }
  }

  async function onJobChange(job) {
    if (job.target_type !== 'scene' || job.scene_id !== draft?.id || !['done', 'error', 'cancelled'].includes(job.status)) return;
    if (recording) { deferredSceneRefresh = true; return; }
    const saved = await api('GET', `/api/scenes/${draft.id}`);
    if (draft?.id !== saved.id) return;
    draft.audio = saved.audio;
    draft.voice_takes = saved.voice_takes;
    draft.background = saved.background;
    timeline = await api('GET', `/api/scenes/${draft.id}/timeline`);
    compiledFingerprint = fingerprint(saved);
    if (S.view === 'scenes') render();
  }

  function source(id, key) {
    const assets = npcs[id]?.assets || {};
    let meta = key === 'sprite' ? assets.sprite : assets.portrait;
    if (key === 'talking' && assets.living && !assets.living.stale) return npcFile(id, 'living/talking.png', assets.living.created);
    if (key === 'blink' && assets.living && !assets.living.stale) return npcFile(id, 'living/blink.png', assets.living.created);
    if (key.startsWith('expression:')) {
      const id2 = key.split(':')[1];
      if (assets.living?.frames?.includes(id2) && !assets.living.stale)
        return npcFile(id, `living/${id2}.png`, assets.living.created);
      const expression = assets.expressions?.[id2];
      if (expression?.file && !expression.stale) return npcFile(id, expression.file, expression.created);
    }
    if (!meta?.file) meta = assets.portrait;
    return meta?.file ? npcFile(id, meta.file, meta.created) : '';
  }

  function drawMeshFrame(frame, beat, mouthTime) {
    for (const actor of Object.values(stage3d?.actors || {})) meshMouths.prepare(actor);
    if (stage3d && blocking) applyCamera(stage3d, draft, timeline, frame, blocking);
    stage3d?.frame(draft, timeline, frame);
    for (const [id, actor] of Object.entries(stage3d?.actors || {})) {
      const active = beat?.kind === 'line' && id === beat.speaker_id && beat.voice;
      const weights = active ? softMouthWeightsAt(beat.voice.lip_sync || {seconds: beat.voice.seconds, mouth_fps: 25}, beat.voice.mouth, mouthTime) : {sil: 1};
      const legacy = active && stage3d.appliesLegacyMouth !== false ? (beat.voice.mouth[frame - beat.start] || 0) : 0;
      meshMouths.apply(actor, timeline.mesh_rigs?.[id], weights, legacy);
      if (active && timeline.mesh_rigs?.[id]?.status !== 'ready' && stage3d.appliesLegacyMouth === false) {
        const jaw = actor.bones.jaw || actor.bones.jaw_01;
        const amplitude = softMouthWeightsAt({seconds: beat.voice.seconds, mouth_fps: 25}, beat.voice.mouth, mouthTime);
        if (jaw) jaw.rotation.x += .12 * (amplitude.mouth_open || 0);
      }
    }
    if (stage3d) stage3d.renderer.render(stage3d.scene, stage3d.camera);
  }

  function castReview() {
    return h('details', {class: 'scene-cast-review'}, h('summary', {}, 'Cast and voice check'),
      h('p', {class: 'hint'}, 'Compare each portrait, on-stage model and voice. Word and timing checks do not judge character fit or acting quality.'),
      draft.cast.map(id => {
        const npc = npcs[id], portrait = npc?.assets?.portrait;
        const voice = draft.voice_bindings?.[id]?.snapshot || npc?.voice || {};
        const clip = timeline?.beats?.find(beat => beat.speaker_id === id && beat.voice)?.voice;
        const url = clip ? (clip.source === 'scene' ? sceneFile(draft, clip.file) : npcFile(id, clip.file, clip.created))
          : !draft.voice_overrides?.[id] && voice.reference?.file ? npcFile(id, voice.reference.file, voice.reference.created) : null;
        return h('article', {class: 'scene-cast-check'},
          portrait?.file && h('img', {src: npcFile(id, portrait.file, portrait.created), alt: `${name(npc)} portrait`, loading: 'lazy'}),
          h('div', {}, h('strong', {}, name(npc)), h('p', {}, `${npc?.profile?.age || ''} · ${npc?.profile?.gender || 'Unspecified'}`),
            h('p', {class: 'hint'}, draft.voice_overrides?.[id] ? `Scene voice: ${draft.voice_overrides[id]}` : `Voice: ${voice.engine || 'Not assigned'}`),
            url ? h('audio', {controls: true, preload: 'none', src: url, 'aria-label': `${name(npc)} cast voice sample`, onplay: event => {
              pause(); for (const other of document.querySelectorAll('.scene-cast-review audio')) if (other !== event.target) other.pause();
            }}) : h('p', {class: 'hint'}, 'Generate a voice line to hear this casting.')));
      }));
  }

  function stageFrame(frame) {
    if (!timeline || !draft) return;
    const beat = timeline.beats.find(item => item.start <= frame && frame < item.end) || timeline.beats.at(-1);
    if (!beat) return;
    cursor = Math.max(0, Math.min(timeline.frames - 1, frame));
    const waitingForSpeech = beat.voice && currentBeat === beat.id && audio && !audio.ended && !frameClock.tail
      && (audio.paused || audio.readyState < 2);
    soundPlayer.update(draft, timeline, cursor, playing && !waitingForSpeech);
    updateSoundStatus();
    updateTransport(beat.id);
    const stage = $('#sceneStage'); if (!stage) return;
    const relative = frame - beat.start;
    const mouthTime = playing && currentBeat === beat.id && audio ? audio.currentTime : relative / timeline.fps;
    if (draft.mode === 'stage3d') {
      drawMeshFrame(frame, beat, mouthTime);
      stage.querySelector('canvas')?.setAttribute('aria-label', `Animated 3D scene preview: ${CAMERA_SHOTS.find(([id]) => id === (beat.camera_shot || 'wide'))?.[1] || 'Wide'}`);
      stage.querySelector('.scene-subtitle').textContent = beat.kind === 'line'
        ? `${name(npcs[beat.speaker_id])}: ${beat.text}` : '';
      if (playing && currentBeat !== beat.id) playBeatAudio(beat);
      return;
    }
    stage.dataset.layout = beat.layout || 'split';
    stage.dataset.active = beat.speaker_id || '';
    for (const id of draft.cast) {
      const image = stage.querySelector(`[data-npc="${id}"] img`);
      const panel = stage.querySelector(`[data-npc="${id}"]`);
      if (!image || !panel) continue;
      panel.classList.toggle('active', id === beat.speaker_id);
      const kind = beat.layout === 'sprite' ? 'sprite' : 'portrait';
      const rig = timeline.rigs?.[id]?.[kind];
      if (id === beat.speaker_id && beat.voice && rig?.status === 'ready') {
        const key = `${id}:${kind}:${rig.version}`;
        let entry = rigPainters.get(id);
        if (entry?.key !== key || !entry?.canvas.isConnected) {
          entry?.painter.dispose(); entry?.canvas.remove();
          const canvas = h('canvas', { class: 'scene-mouth-canvas', 'aria-label': `${name(npcs[id])} mouth animation` });
          panel.append(canvas); entry = { key, canvas, painter: createRigPainter(canvas, rig) }; rigPainters.set(id, entry);
        }
        image.style.visibility = 'hidden';
        entry.painter.draw(mouthWeightsAt(beat.voice.lip_sync, beat.voice.mouth, mouthTime));
        continue;
      }
      const previous = rigPainters.get(id); if (previous) { previous.painter.dispose(); previous.canvas.remove(); rigPainters.delete(id); }
      image.style.visibility = '';
      const mouth = beat.voice?.mouth || [];
      const mouthIndex = Math.floor(mouthTime * 25);
      const talking = id === beat.speaker_id && mouthIndex < mouth.length && mouth[mouthIndex] > 0.28;
      const blink = relative % 100 === 75 || relative % 100 === 76;
      const key = beat.layout === 'sprite' ? 'sprite' : talking ? 'talking'
        : id === beat.speaker_id && beat.expression ? `expression:${beat.expression}` : blink ? 'blink' : 'portrait';
      const url = source(id, key);
      if (image.getAttribute('src') !== url) image.src = url;
    }
    const subtitle = stage.querySelector('.scene-subtitle');
    subtitle.textContent = beat.kind === 'line' ? `${name(npcs[beat.speaker_id])}: ${beat.text}` : '';
    if (playing && currentBeat !== beat.id) {
      playBeatAudio(beat);
    }
  }

  function playBeatAudio(beat) {
    currentBeat = beat.id;
    const token = ++audioToken;
    if (audio) { audio.pause(); audio = null; }
    frameClock.attach(cursor, performance.now());
    if (beat.voice) {
      audio = new Audio(beat.voice.source === 'scene' ? sceneFile(draft, beat.voice.file)
        : npcFile(beat.speaker_id, beat.voice.file, beat.voice.created));
      const clip = audio, offset = Math.max(0, (cursor - beat.start) / timeline.fps);
      const begin = () => {
        if (token !== audioToken || !playing) return;
        clip.currentTime = Math.min(offset, clip.duration || offset);
        if (offset >= clip.duration) { frameClock.tail = true; return; }
        clip.play().catch(error => { if (token === audioToken) { pause(); toast('Speech playback stopped: ' + error.message, 'error'); } });
      };
      if (clip.readyState >= 1) begin();
      else clip.addEventListener('loadedmetadata', begin, { once: true });
      clip.addEventListener('error', () => { if (token === audioToken) { pause(); toast('Could not load this scene’s speech clip.', 'error'); } });
    }
  }

  function updateTransport(beatId) {
    const slider = $('#sceneScrub'), time = $('#sceneTime'), playButton = $('#scenePlay'), pauseButton = $('#scenePause');
    if (slider && slider !== document.activeElement) slider.value = cursor;
    if (time && timeline) time.textContent = `${timeLabel(cursor / timeline.fps)} / ${timeLabel(timeline.frames / timeline.fps)}`;
    if (playButton) { playButton.textContent = startingPlayback ? 'Loading sound…' : cursor > 0 ? 'Resume' : 'Play'; playButton.disabled = playing || startingPlayback; }
    if (pauseButton) pauseButton.disabled = !playing;
    if (beatId) for (const el of document.querySelectorAll('.scene-timeline-beat')) {
      el.classList.toggle('active', el.dataset.beat === beatId);
      el.setAttribute('aria-current', el.dataset.beat === beatId ? 'true' : 'false');
    }
  }

  async function ensureTimeline() {
    if (timeline && compiledFingerprint === fingerprint(draft)) return;
    const snapshot = editorBody(draft), expected = fingerprint(snapshot);
    const updated = await api('POST', '/api/scenes/preview', snapshot);
    if (!draft || fingerprint(draft) !== expected) throw new Error('The scene changed while preview was updating. Try again.');
    timeline = updated; compiledFingerprint = expected;
    cursor = Math.min(cursor, Math.max(0, timeline.frames - 1));
  }

  async function seek(frame, beatId) {
    pause();
    const previous = timeline;
    await ensureTimeline();
    if (previous !== timeline) render();
    stop(false);
    cursor = Math.max(0, Math.min(timeline.frames - 1, beatId ? timeline.beats.find(row => row.id === beatId)?.start ?? frame : frame));
    selectedBeat = beatId || timeline.beats.find(row => row.start <= cursor && cursor < row.end)?.id || '';
    frameClock.seek(cursor);
    stageFrame(cursor);
    for (const el of document.querySelectorAll('.scene-beat')) el.classList.toggle('selected', el.dataset.beat === selectedBeat);
    if (capability?.studio_schema && draft.mode === 'stage3d') $('.scene-staging')?.replaceWith(stagingPanel());
    if (beatId) document.querySelector(`.scene-beat[data-beat="${beatId}"]`)?.scrollIntoView({ block: 'nearest' });
  }

  async function refreshPreview() {
    pause(); captureEdit(); await ensureTimeline(); render();
  }

  async function reorderBeat(from, target) {
    if (from === target) return;
    const row = draft.beats.find(beat => beat.id === from);
    if (!row || !draft.beats.some(beat => beat.id === target)) return;
    draft.beats.splice(draft.beats.indexOf(row), 1);
    draft.beats.splice(draft.beats.findIndex(beat => beat.id === target), 0, row);
    selectedBeat = row.id;
    await refreshPreview();
  }

  function reviewClip(row) {
    const beat = timeline?.beats.find(beat => beat.id === row.beat_id), voice = beat?.voice;
    if (!voice) return;
    pause();
    $('#sceneClipReview')?.remove();
    const clip = h('audio', { controls: true, src: voice.source === 'scene' ? sceneFile(draft, voice.file) : npcFile(beat.speaker_id, voice.file, voice.created) });
    const lip = voice.lip_sync || {};
    const dialog = h('dialog', { id: 'sceneClipReview', 'aria-label': 'Review scene speech', onclose: () => { clip.pause(); dialog.remove(); } },
      h('h2', {}, 'Review scene speech'), h('p', {}, beat.text), clip,
      lip.validation?.recognized_text ? h('p', {}, 'Recognized words: ' + lip.validation.recognized_text) : null,
      h('p', { class: 'hint' }, lip.reason || 'Word timing passed; voice identity and delivery still need listening.'),
      btn('Close', '', () => dialog.close()));
    document.body.append(dialog); dialog.showModal();
  }

  async function generateBackground() {
    if (dirty()) return toast('Save the scene before generating its background', 'error');
    // The picture is drawn with the engine the Models panel names (and its Mage-Flow fallback switch); a recipe without them leaves it to the server's defaults.
    const o = S.recipe?.options || {}, options = {};
    if (o.image_model) options.image_model = o.image_model;
    if (typeof o.engine_fallback === 'boolean') options.engine_fallback = o.engine_fallback;
    const result = await api('POST', `/api/scenes/${draft.id}/background`, { options });
    afterSubmit({ jobs: [result.job] });
  }

  async function play() {
    if (playing || startingPlayback || recording) return;
    startingPlayback = true; updateTransport();
    try {
    const previous = timeline;
    await ensureTimeline();
    if (previous !== timeline) render();
    const expected = fingerprint(draft);
    await soundPlayer.prepare(draft, timeline);
    if (expected !== fingerprint(draft)) throw Error('The scene changed while sound was loading. Play again when ready.');
    if (cursor >= timeline.frames - 1) cursor = 0;
    playing = true; frameClock.start(performance.now());
    const beat = timeline.beats.find(row => row.start <= cursor && cursor < row.end);
    if (beat && (currentBeat !== beat.id || !audio || audio.ended)) playBeatAudio(beat);
    else if (audio) audio.play().catch(error => { pause(); toast(error.message, 'error'); });
    updateTransport();
    const tick = now => {
      if (!playing) return;
      const active = timeline.beats.find(row => row.id === currentBeat);
      const frame = frameClock.sample(now, active, audio);
      if (frame >= timeline.frames) { cursor = timeline.frames - 1; pause(); stageFrame(cursor); return; }
      stageFrame(frame);
      request = requestAnimationFrame(tick);
    };
    request = requestAnimationFrame(tick);
    } finally { startingPlayback = false; updateTransport(); }
  }

  async function renderVideo() {
    if (dirty()) return toast('Save changes before rendering', 'error');
    if (!draft.id) return toast('Save the scene first', 'error');
    if (!capability?.mp4) return toast('Install FFmpeg to export MP4 video', 'error');
    if (draft.mode === 'stage3d') return recordStage();
    renderJob = (await api('POST', `/api/scenes/${draft.id}/render`)).job;
    render(); pollRender(renderJob.id);
  }

  function paintRecordedFrame(context, frame) {
    const [width, height] = timeline.size;
    const beat = timeline.beats.find(item => item.start <= frame && frame < item.end) || timeline.beats.at(-1);
    drawMeshFrame(frame, beat, (frame - (beat?.start || 0)) / timeline.fps);
    context.drawImage(stage3d.canvas, 0, 0, width, height);
    context.fillStyle = '#09131bbb';
    context.fillRect(0, 0, width, height * 0.13);
    context.fillStyle = 'white';
    context.font = `bold ${Math.round(width * 0.031)}px sans-serif`;
    context.fillText(draft.title, width * 0.035, height * 0.078);
    if (beat?.kind !== 'line') return;
    context.fillStyle = '#06111ce0';
    context.fillRect(width * 0.02, height * 0.85, width * 0.96, height * 0.12);
    context.fillStyle = 'white';
    context.font = `bold ${Math.round(width * 0.025)}px sans-serif`;
    context.textAlign = 'center';
    const label = `${name(npcs[beat.speaker_id])}: ${beat.text}`;
    const words = label.split(' '), lines = [];
    let line = '';
    for (const word of words) {
      const next = `${line} ${word}`.trim();
      if (line && context.measureText(next).width > width * 0.9) { lines.push(line); line = word; }
      else line = next;
    }
    if (line) lines.push(line);
    lines.slice(0, 2).forEach((part, index) =>
      context.fillText(part, width / 2, height * (lines.length > 1 ? 0.89 : 0.92) + index * height * 0.045));
    context.textAlign = 'start';
  }

  async function recordStage() {
    if (recording) return;
    if (!stage3d || !timeline) return toast('Wait for the 3D models to load before export', 'error');
    const missing = draft.cast.filter(id => !stage3d.actors[id]);
    if (missing.length) return toast('All cast members need rigged 3D models before export', 'error');
    stop(); recording = true; exportProgress = 'Rendering 3D frames…';
    const controls = [...document.querySelectorAll('.scenes-page input, .scenes-page select, .scenes-page textarea, .scenes-page button, #tabs button')]
      .map(control => [control, control.disabled]);
    for (const [control] of controls) control.disabled = true;
    const button = [...document.querySelectorAll('.scene-export button')].find(item => item.textContent.includes('Export 3D'));
    if (button) { button.disabled = true; button.textContent = exportProgress; }
    const [width, height] = timeline.size;
    const output = document.createElement('canvas');
    output.width = width; output.height = height;
    const context = output.getContext('2d');
    try {
      if (!context) throw new Error('This browser cannot render the 3D export canvas');
      const header = new ArrayBuffer(12);
      new Uint8Array(header).set([78, 80, 67, 51, 68, 49, 0, 0]);
      new DataView(header).setUint32(8, timeline.frames, true);
      const parts = [header];
      let bytes = 12;
      stage3d.setOutputSize(width, height);
      for (let frame = 0; frame < timeline.frames; frame++) {
        paintRecordedFrame(context, frame);
        const jpeg = await new Promise(resolve => output.toBlob(resolve, 'image/jpeg', 0.82));
        if (!jpeg) throw new Error(`Could not capture 3D frame ${frame + 1}`);
        const length = new ArrayBuffer(4);
        new DataView(length).setUint32(0, jpeg.size, true);
        parts.push(length, jpeg);
        bytes += 4 + jpeg.size;
        if (bytes > 80_000_000) throw new Error('The 3D export is too large. Shorten the scene and try again.');
        if (frame % 10 === 0) {
          exportProgress = `Rendering frame ${frame + 1}/${timeline.frames}…`;
          if (button) button.textContent = exportProgress;
          await new Promise(requestAnimationFrame);
        }
      }
      exportProgress = 'Encoding MP4…';
      if (button) button.textContent = exportProgress;
      const response = await fetch(`/api/scenes/${draft.id}/frames`, {
        method: 'POST', headers: { 'Content-Type': 'application/x-npcforge-frames',
          'X-Scene-Revision': String(draft.revision), 'X-Scene-Fingerprint': timeline.fingerprint },
        body: new Blob(parts, { type: 'application/x-npcforge-frames' }),
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result.error || `MP4 export failed (${response.status})`);
      draft.render = result.render;
      toast('3D scene MP4 is ready');
    } finally {
      stage3d?.resize();
      recording = false;
      exportProgress = '';
      for (const [control, disabled] of controls) control.disabled = disabled;
      if (deferredSceneRefresh) {
        deferredSceneRefresh = false;
        await onJobChange({target_type: 'scene', scene_id: draft.id, status: 'done'});
      } else render();
    }
  }

  async function pollRender(id) {
    while (renderJob?.id === id && ['queued', 'running'].includes(renderJob.status)) {
      await new Promise(resolve => setTimeout(resolve, 1200));
      try { renderJob = await api('GET', `/api/scenes/jobs/${id}`); } catch { break; }
      if (S.view === 'scenes' && draft) render();
    }
    if (renderJob?.status === 'done' && draft?.id === renderJob.scene_id) {
      const saved = await api('GET', `/api/scenes/${draft.id}`);
      draft.render = saved.render;
      toast('Scene video is ready'); render();
    } else if (renderJob?.status === 'error') toast(renderJob.error || 'Render failed', 'error');
  }

  function renderEditor() {
    const options = S.library.map(n => [n.id, n.name]);
    const video = draft.render?.file && sceneFile(draft, draft.render.file);
    const stage = draft.mode === 'stage3d'
      ? h('div', { id: 'sceneStage', class: `scene-stage3d ${draft.aspect}` },
        h('canvas', { class: 'scene-3d-canvas', 'aria-label': 'Animated 3D scene preview' }),
        h('div', { class: 'scene-title' }, draft.title), h('div', { class: 'scene-subtitle' }))
      : h('div', { id: 'sceneStage', class: `scene-stage ${draft.aspect}`,
        style: { background: BACKDROP_COLORS[draft.backdrop] || BACKDROP_COLORS.slate } },
        h('div', { class: 'scene-title' }, draft.title),
        draft.cast.map(id => h('div', { class: 'scene-actor', dataset: { npc: id } },
          h('img', { src: source(id, 'portrait'), alt: name(npcs[id]) }), h('span', {}, name(npcs[id])))),
        h('div', { class: 'scene-subtitle' }));
    $('#view').replaceChildren(h('div', { class: 'scenes-page', oninput: captureEdit, onchange: captureEdit },
      h('div', { class: 'scene-head' }, h('div', {}, h('h1', {}, draft.id ? draft.title : 'New scene'),
        h('p', { class: 'hint' }, draft.mode === 'stage3d'
          ? 'Generate a setting, position rigged 3D models, and direct their movement and dialogue.'
          : 'Script your cast, voice custom lines, preview, and export a short video.')),
        h('div', { class: 'row' }, btn('All scenes', null, safe(showList), { cls: 'ghost' }),
          h('button', { id: 'sceneUndo', type: 'button', class: 'btn ghost', disabled: history.undoStack.length < 2,
            onclick: safe(() => travelHistory('undo')) }, 'Undo'),
          h('button', { id: 'sceneRedo', type: 'button', class: 'btn ghost', disabled: !history.redoStack.length,
            onclick: safe(() => travelHistory('redo')) }, 'Redo'),
          btn('Save scene', 'save', safe(save), { cls: 'primary' }),
          draft.id && btn('Duplicate scene', null, safe(duplicateScene), { cls: 'ghost', disabled: dirty() }),
          draft.id && btn('Delete', 'trash', safe(remove), { cls: 'ghost danger' }))),
      recoveredDraft && h('div', { class: 'notice row wrap', role: 'status' },
        h('span', {}, recoveredDraft.conflict ? 'A recovered draft belongs to an earlier saved revision. Review it before saving.' : 'An unfinished draft is available on this browser.'),
        btn('Recover draft', null, safe(recoverDraft)),
        btn('Keep saved scene', null, () => { history.clear(draft); recoveredDraft = null; render(); }, { cls: 'ghost' })),
      history.error && h('p', { class: 'notice', role: 'status' }, history.error),
      dirty() && h('p', { class: 'hint' }, 'Preview these changes now; save before voicing or rendering.'),
      h('div', { class: 'scene-editor-grid' },
        h('div', { class: 'scene-script' },
          h('section', { class: 'card scene-settings' },
            field('Title', h('input', { value: draft.title, maxlength: 100, oninput: e => { draft.title = e.target.value; } })),
            field('Format', select([['landscape', 'Widescreen 16:9'], ['portrait', 'Vertical 9:16']], draft.aspect,
              value => { draft.aspect = value; render(); })),
            field('Scene type', select([['stage3d', 'Animated 3D stage'], ['cards', 'Portrait cards']], draft.mode || 'cards',
              value => { draft.mode = value; if (value === 'stage3d' && !draft.background_prompt) draft.background_prompt = DEFAULT_BACKGROUND; render(); })),
            draft.mode === 'stage3d'
              ? field('Describe the setting', h('textarea', { rows: 3, maxlength: 500,
                value: draft.background_prompt || '', oninput: e => { draft.background_prompt = e.target.value; } }))
              : field('Backdrop', select(BACKDROPS, draft.backdrop || 'slate', value => { draft.backdrop = value; render(); })),
            draft.cast.map((id, index) => field(`NPC ${index + 1}`, select(options, id, safe(async value => {
              if (draft.cast.includes(value)) return toast('Choose different NPCs for each cast slot', 'error');
              draft.cast[index] = value;
              remapCast(id, value);
              await loadCast(); await refreshPreview();
            }), { 'data-cast': String(index) }))),
            capability?.voice_studio_schema ? draft.cast.map(voicePinField) : null,
            capability?.voice_studio_schema && h('p', { class: 'hint' }, 'Pins preserve the reference, speaking settings, pronunciation rules and performances when saved. Follow character voice to use future character changes.'),
            draft.mode === 'stage3d' && draft.cast.map(id => field(`${name(npcs[id])} scene voice`,
              select(capability?.stock_voices ? [['','Use character voice'],...capability.stock_voices.map(row=>[row.id,`${row.label} · ${row.accent}`]),
                ...SCENE_VOICES.filter(([key])=>key===draft.voice_overrides?.[id]&&!capability.stock_voices.some(row=>row.id===key))] : SCENE_VOICES, draft.voice_overrides?.[id] || '', value => {
                draft.voice_overrides ||= {};
                if (value) draft.voice_overrides[id] = value;
                else delete draft.voice_overrides[id];
                if (value && draft.voice_bindings) delete draft.voice_bindings[id];
                for (const beat of draft.beats) if (beat.speaker_id === id) beat.performance_id = null;
                render();
              })))),
          capability?.studio_schema && castView({scene: draft, library: S.library, add: addCast, remove: removeCast}),
          castReview(),
          capability?.studio_schema && draft.mode === 'stage3d' && propsView({scene: draft, items: stageItems, commit: refreshPreview}),
          capability?.studio_schema && soundView({scene: draft, dirty: dirty(), upload: uploadSound, commit: refreshPreview, field, select}),
          capability?.studio_schema && writerView({scene: draft, field, select, suggest: suggestScene, proposal: writerProposal, apply: applyProposal}),
          h('h2', {}, 'Script'), draft.beats.map(beatEditor),
          h('div', { class: 'row' }, btn('Add line', 'plus', safe(() => add('line')), { cls: 'small' }),
            btn('Add reaction', 'plus', safe(() => add('reaction')), { cls: 'small ghost' }),
            btn('Add pause', 'plus', safe(() => add('pause')), { cls: 'small ghost' }))),
        h('aside', { class: 'scene-preview card' }, h('h2', {}, 'Preview'), stage,
          draft.mode === 'stage3d' && h('p', { class: 'hint', id: 'scene3dStatus', role: 'status' }, 'Loading 3D stage…'),
          capability?.director_schema ? timelineView({ timeline, frame: cursor, selected: selectedBeat,
            seek: safe(seek), reorder: safe(reorderBeat) }) : h('p', { class: 'notice' }, 'Restart Forge to enable timeline and camera controls.'),
          h('div', { class: 'row' },
            h('button', { id: 'scenePlay', type: 'button', class: 'btn', onclick: safe(play), disabled: playing }, cursor ? 'Resume' : 'Play'),
            h('button', { id: 'scenePause', type: 'button', class: 'btn ghost', onclick: pause, disabled: !playing }, 'Pause'),
            btn('Stop', 'x', () => { stop(); if (timeline) stageFrame(0); meshMouths.clear();
              if (stage3d) stage3d.renderer.render(stage3d.scene, stage3d.camera); }, { cls: 'ghost', disabled: !timeline }),
            btn('Update preview', 'refresh', safe(refreshPreview), { cls: 'small ghost' })),
          draft.mode === 'stage3d' && h('div', { class: 'row' },
            btn(draft.background?.prompt === draft.background_prompt ? 'Regenerate background' : 'Generate background',
              'zap', safe(generateBackground), { disabled: dirty() || !draft.id }),
            h('span', { class: 'hint' }, draft.background?.prompt === draft.background_prompt
              ? 'Generated setting ready' : 'A plain setting appears until generation finishes.')),
          h('div', { class: 'row' }, btn('Voice missing lines', 'zap', safe(() => voiceLines(undefined, false)),
            { disabled: dirty() || !draft.id || !draft.beats.some(beat => beat.kind === 'line' && !timeline?.beats.find(item => item.id === beat.id)?.voice) })),
          timeline && h('p', { class: 'hint' }, `${(timeline.frames / timeline.fps).toFixed(1)} seconds · ${timeline.size.join(' × ')} · 25 fps`),
          timeline?.readiness ? readinessView({ timeline, mp4: capability?.mp4, dirty: dirty(), actions: {
            voice: safe(row => voiceLines([row.beat_id], true)), background: safe(generateBackground), review: reviewClip,
            character: openNpc ? safe(async row => { if (dirty()) return toast('Save the scene before opening a character.', 'error'); pause(); await openNpc(row.npc_id); }) : null,
            cast: row => document.querySelector(`select[data-cast="${draft.cast.indexOf(row.npc_id)}"]`)?.focus(), export: safe(renderVideo) } })
            : timeline?.warnings?.length ? h('div', { class: 'scene-warnings' }, timeline.warnings.map(w => h('p', {}, w))) : null,
          capability?.studio_schema && draft.mode === 'stage3d' && stagingPanel(),
          h('div', { class: 'scene-export' }, h('h3', {}, 'Video export'),
            btn(draft.mode === 'stage3d' ? (exportProgress || 'Export 3D MP4') : 'Render MP4 on CPU', 'download', safe(renderVideo),
              { disabled: dirty() || !capability?.mp4 || timeline?.readiness?.export_blocked || recording || ['queued', 'running'].includes(renderJob?.status) }),
            !capability?.mp4 && h('p', { class: 'hint' }, 'Install FFmpeg to enable MP4 export.'),
            renderJob && h('p', { class: 'hint' }, `${renderJob.status} · ${renderJob.done}/${renderJob.total}${renderJob.error ? ` · ${renderJob.error}` : ''}`),
            ['queued', 'running'].includes(renderJob?.status) && btn('Cancel render', 'x', safe(async () => {
              renderJob = await api('POST', `/api/scenes/jobs/${renderJob.id}/cancel`); render(); }), { cls: 'small ghost' }),
            video && h('div', {}, h('video', { controls: true, src: video, class: 'scene-video' }),
              h('a', { class: 'btn small', href: video, download: `${draft.title.replace(/[^a-z0-9]+/gi, '-').toLowerCase()}.mp4` }, 'Download MP4'),
              h('a', { class: 'btn small', href: `/api/scenes/${draft.id}/export.zip`, download: `${draft.title.replace(/[^a-z0-9]+/gi, '-').toLowerCase()}-scene.zip` }, 'Download scene package'),
              (draft.render.revision !== draft.revision || (draft.render.fingerprint && draft.render.fingerprint !== timeline?.fingerprint))
                && h('p', { class: 'hint' }, 'This video uses older scene assets or audio.')))))));
    for (const node of document.querySelectorAll('.scenes-page details')) node.open = openSections.has(sectionKey(node));
    const previewPanel = $('.scene-preview'); if (previewPanel) previewPanel.scrollTop = previewScroll;
    if (timeline) { cursor = Math.min(cursor, timeline.frames - 1); stageFrame(cursor); }
    if (draft.mode === 'stage3d') {
      const token = ++stageToken;
      const status = $('#scene3dStatus');
      import('./scene-director-stage.js').then(async ({ DirectorStage, blockingAt }) => {
        if (token !== stageToken || !stage.isConnected) return;
        stage3d = new DirectorStage(stage.querySelector('canvas'), status);
        blocking = blockingAt;
        const background = draft.background?.prompt === draft.background_prompt
          ? sceneFile(draft, draft.background.file) : null;
        const models=Object.fromEntries(Object.entries(npcs).map(([id,npc])=>{
          const rig=timeline?.mesh_rigs?.[id];
          return [id,rig?.status==='ready'&&rig.source.snapshot_file?{...npc,assets:{...npc.assets,rig_3d:{...npc.assets.rig_3d,file:rig.source.snapshot_file}}}:npc];
        }));
        await stage3d.load(draft, models, background, npcFile, timeline?.props || []);
        if (token === stageToken && timeline) stageFrame(cursor);
      }).catch(error => { if (token === stageToken) status.textContent = `3D preview unavailable: ${error.message}`; });
    }
  }

  function renderScenes() {
    if (recording) return;
    const previewPanel = $('.scene-preview'); if (previewPanel) previewScroll = previewPanel.scrollTop;
    for (const node of document.querySelectorAll('.scenes-page details')) {
      if (node.open) openSections.add(sectionKey(node)); else openSections.delete(sectionKey(node));
    }
    stop(false); disposeStage();
    if (draft) {
      if (historyId !== (draft.id || 'new')) resetHistory();
      else if (!recoveredDraft) captureEdit();
      renderEditor();
    } else { historyId = undefined; renderList(); }
  }
  return { showList, load, createFromPack, createWithNpc, onJobChange, renderScenes, stop };
}
