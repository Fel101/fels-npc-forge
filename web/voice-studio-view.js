import { $, api, btn, h, safe, toast } from './core.js';
import { effectControls } from './voice-effects-controls.js';

const emptyStudio = () => ({ pronunciations: [], presets: [], default_performance: '', line_performances: {} });
export const studioOf = voice => ({ ...emptyStudio(), ...structuredClone(voice?.studio || {}) });

export function createVoiceStudio({ selectedNpc, onSaved, engineInfo }) {
  let owner = null, studio = null, selected = '', busy = false, preview = null, effectPreview = null, previewSource = '';
  const field = (name, input) => h('label', { class: 'stack' }, h('span', { class: 'lbl' }, name), input);
  const invalidateEffectPreview = () => { effectPreview = null; $('#voiceEffectPreview')?.remove(); };
  const caps = () => {
    const voice = owner.voice || {}, id = voice.speaker || voice.engine;
    return engineInfo().performance_caps?.[id] || { direction: false, parameters: [], limits: {} };
  };
  const action = fn => safe(async () => {
    if (busy) return;
    busy = true;
    $('#voiceStudioControls').disabled = true;
    try { await fn(); }
    finally { busy = false; if ($('#voiceStudio')?.open) $('#voiceStudioControls').disabled = false; }
  });

  function open(section) {
    owner = selectedNpc();
    if (!owner?.id) throw new Error('Choose a saved character first.');
    studio = studioOf(owner.voice); selected = studio.presets[0]?.id || ''; preview = null; effectPreview = null; previewSource = '';
    let dialog = $('#voiceStudio');
    if (!dialog) {
      dialog = h('dialog', { id: 'voiceStudio', class: 'about voice-studio-dialog', 'aria-labelledby': 'voiceStudioTitle' });
      dialog.oncancel = e => { if (busy) e.preventDefault(); };
      document.body.append(dialog);
    }
    if (!dialog.open) dialog.showModal(); paint();
    if (section === 'effects') $('#voiceEffectsEditor')?.scrollIntoView({ block: 'start' });
  }

  function pronunciation() {
    return h('section', { class: 'card stack' }, h('h3', {}, 'Pronunciation dictionary'),
      h('p', { class: 'hint' }, 'Written terms match whole words or phrases, ignoring case. Spoken spellings affect synthesis only; dialogue and captions keep your original text.'),
      studio.pronunciations.map((rule, index) => h('div', { class: 'voice-pronunciation-row' },
        field('Written term', h('input', { type: 'text', value: rule.term, maxlength: 100, 'aria-label': `Written term ${index + 1}`,
          oninput: e => { rule.term = e.target.value; } })),
        field('Spoken spelling', h('input', { type: 'text', value: rule.spoken, maxlength: 150, 'aria-label': `Spoken spelling ${index + 1}`,
          oninput: e => { rule.spoken = e.target.value; } })),
        btn('Remove', '', () => { studio.pronunciations.splice(index, 1); paint(); }, { cls: 'small' }))),
      btn('Add pronunciation', 'plus', () => { studio.pronunciations.push({ term: '', spoken: '' }); paint(); },
        { cls: 'small', disabled: studio.pronunciations.length >= 200 }),
      h('p', { class: 'hint' }, 'Saving a changed rule marks affected generated lines outdated. Unused rules leave their audio fresh. Existing WAV files remain available.'));
  }

  function performances() {
    const limits = caps(), preset = studio.presets.find(p => p.id === selected);
    const unsupported = preset ? Object.keys(preset.parameters).filter(key => !limits.parameters.includes(key)) : [];
    if (preset?.direction && !limits.direction) unsupported.push('direction');
    const choose = h('select', { 'aria-label': 'Edit saved performance', onchange: e => { selected = e.target.value; paint(); } },
      studio.presets.map(p => h('option', { value: p.id }, p.name || 'Unnamed performance')));
    choose.value = selected;
    const defaultPicker = h('select', { 'aria-label': 'Default performance', onchange: e => { studio.default_performance = e.target.value; } },
      h('option', { value: '' }, 'Use voice settings'), studio.presets.map(p => h('option', { value: p.id }, p.name || 'Unnamed')));
    defaultPicker.value = studio.default_performance;
    return h('section', { class: 'card stack' }, h('h3', {}, 'Saved performances'),
      h('p', { class: 'hint' }, limits.direction ? 'Acting direction steers delivery while the assigned voice preserves identity.'
        : 'This speaking adapter has no verified acting-direction control. Only its effective parameters are offered here; sound tags remain available where supported.'),
      studio.presets.length ? field('Edit performance', choose) : null,
      btn('Add performance', 'plus', () => {
        const id = 'performance-' + crypto.randomUUID().slice(0, 8);
        studio.presets.push({ id, name: 'New performance', direction: '', parameters: {} }); selected = id; paint();
      }, { cls: 'small', disabled: studio.presets.length >= 30 }),
      preset && h('div', { class: 'stack' }, field('Performance name', h('input', { type: 'text', value: preset.name, maxlength: 80,
        'aria-label': 'Performance name', oninput: e => { preset.name = e.target.value; } })),
        unsupported.length ? h('div', { class: 'notice' }, `This speaking adapter does not apply: ${unsupported.join(', ')}.`,
          btn('Remove unsupported controls', '', () => {
            for (const key of unsupported) { if (key === 'direction') preset.direction = ''; else delete preset.parameters[key]; }
            paint();
          }, { cls: 'small' })) : null,
        limits.direction ? field('Acting direction', h('textarea', { rows: 2, maxlength: 300, value: preset.direction,
          'aria-label': 'Acting direction', oninput: e => { preset.direction = e.target.value; } })) : null,
        limits.parameters.map(key => {
          const [min, max, defaultValue] = limits.limits[key];
          const label = { cfg_value: 'guidance strength (CFG)', inference_timesteps: 'inference steps',
            cfg_weight: 'voice guidance (CFG)', top_k: 'top-k sampling', top_p: 'top-p sampling', min_p: 'minimum probability',
            repetition_penalty: 'repetition penalty', max_new_tokens: 'maximum speech tokens',
            cfg_scale: 'text guidance (CFG)', stg_scale: 'acting guidance (STG)', duration_multiplier: 'duration allowance',
            max_seconds:'maximum seconds per chunk' }[key] || key;
          const enabled = Object.hasOwn(preset.parameters, key);
          const input = h('input', { type: 'number', min, max, step: limits.integer_parameters?.includes(key) ? 1 : .05, value: preset.parameters[key] ?? defaultValue,
            disabled: !enabled, 'aria-label': `Performance ${label}`, oninput: e => { preset.parameters[key] = Number(e.target.value); } });
          return h('div', { class: 'row' }, h('label', { class: 'check' }, h('input', { type: 'checkbox', checked: enabled,
            onchange: e => { input.disabled = !e.target.checked; if (e.target.checked) preset.parameters[key] = Number(input.value); else delete preset.parameters[key]; } }),
          `Override ${label}`), input);
        }),
        limits.parameters.includes('cfg') ? h('p', { class: 'hint' }, 'Breeze CFG needs a nonempty acting direction; a plain clone uses CFG 1.') : null,
        engineInfo().effects_schema ? h('div', { class: 'stack' },
          h('label', { class: 'check' }, h('input', { type: 'checkbox', checked: Object.hasOwn(preset, 'effects'),
            onchange: e => { if (e.target.checked) preset.effects = structuredClone(studio.effects || engineInfo().effects_caps.presets.dry); else delete preset.effects; paint(); } }),
          'Override voice effects for this performance'),
          preset.effects ? effectControls({ value: preset.effects, caps: engineInfo().effects_caps, prefix: 'Performance effects',
            onChange: value => { preset.effects = value; }, repaint: paint }) : null) : null,
        btn('Remove performance', '', () => {
          studio.presets = studio.presets.filter(p => p.id !== preset.id);
          if (studio.default_performance === preset.id) studio.default_performance = '';
          for (const id of Object.keys(studio.line_performances)) if (studio.line_performances[id] === preset.id) delete studio.line_performances[id];
          selected = studio.presets[0]?.id || ''; paint();
        }, { cls: 'small' })), field('Default performance for new speech', defaultPicker),
      h('p', { class: 'hint' }, 'Dialogue lines and scene beats can choose another saved performance. Performances preserve the assigned voice identity.'));
  }

  function voiceEffects() {
    const effects = engineInfo().effects_caps || {}, voice = owner.voice || {};
    const sources = [voice.reference?.file && ['reference', 'Assigned reference / audition'],
      ...Object.entries(voice.tests || {}).map(([key, meta]) => [`test:${key}`, `Test: ${meta.text.slice(0, 65)}`]),
      ...Object.entries(owner.voice_lines || {}).map(([key, meta]) => [`line:${key}`, `Dialogue: ${meta.text.slice(0, 65)}`]),
      ...(voice.takes || []).map(meta => [`take:${meta.id}`, `Take ${meta.n}: ${meta.text.slice(0, 45)}`])].filter(Boolean);
    if (!sources.some(([id]) => id === previewSource)) previewSource = sources[0]?.[0] || '';
    const sourcePicker = h('select', { 'aria-label': 'Effect preview source', onchange: e => { previewSource = e.target.value; invalidateEffectPreview(); } },
      sources.map(([id, label]) => h('option', { value: id }, label)));
    sourcePicker.value = previewSource;
    return h('section', { id: 'voiceEffectsEditor', class: 'card stack' }, h('h3', {}, 'Voice effects'),
      h('p', { class: 'hint' }, 'Creature and robot effects process delivered speech on the CPU. The assigned voice and its dry original stay intact. Disable effects to return to the original; a performance can override these defaults.'),
      effects.available ? null : h('p', { class: 'notice' }, `Effects unavailable: ${effects.reason || 'required FFmpeg filters are missing'}`),
      effectControls({ value: studio.effects, caps: effects, prefix: 'Voice effects', onChange: value => { studio.effects = value; invalidateEffectPreview(); }, repaint: paint }),
      h('p', { class: 'hint' }, 'Duration stays unchanged. Formants can follow pitch or remain preserved; independent formant scaling is unavailable. Layering mixes a pitched copy of this same voice.'),
      sources.length ? field('Preview an existing clip', sourcePicker) : h('p', { class: 'hint' }, 'Generate a speech test or dialogue line first to preview these effects.'),
      sources.length ? btn('Preview voice effects', '', action(async () => {
        effectPreview = await api('POST', `/api/npcs/${owner.id}/voice/effects/preview`, { source: previewSource, effects: studio.effects || {} }); paint();
      }), { cls: 'small', disabled: !effects.available }) : null,
      effectPreview ? h('div', { id: 'voiceEffectPreview', class: 'stack' }, h('strong', {}, 'Original'), h('audio', { controls: true, src: effectPreview.original_url, 'aria-label': 'Dry effect preview' }),
        h('strong', {}, 'With current voice effects'), h('audio', { controls: true, src: effectPreview.audio_url, 'aria-label': 'Processed effect preview' }),
        h('p', { class: 'hint' }, `${effectPreview.postprocessing.output_samples} samples at ${effectPreview.postprocessing.sample_rate} Hz · ${effectPreview.source_role === 'preserved_dry_master' ? 'preserved dry master' : 'existing clip used as source'}. This preview does not change assignment or generated dialogue.`)) : null);
  }

  function paint() {
    const dialog = $('#voiceStudio');
    const text = h('textarea', { rows: 2, 'aria-label': 'Pronunciation preview text', value: preview?.authored_text || '',
      placeholder: 'Write a test line with a character or place name…' });
    dialog.replaceChildren(h('div', { class: 'about-body' }, h('header', { class: 'about-head' },
      h('h2', { id: 'voiceStudioTitle', class: 'grow' }, 'Pronunciation & performances'),
      btn('Close', '', () => { if (!busy) dialog.close(); }, { cls: 'small' })),
      h('fieldset', { id: 'voiceStudioControls', class: 'voice-library-controls', disabled: busy },
        h('div', { class: 'voice-studio-grid' }, pronunciation(), performances()),
        engineInfo().effects_schema ? voiceEffects() : null,
        h('section', { class: 'card stack' }, h('h3', {}, 'Check spoken text'), text,
          btn('Preview spoken text', '', action(async () => {
            preview = await api('POST', `/api/npcs/${owner.id}/voice/studio/preview`, { text: text.value, studio }); paint();
          }), { cls: 'small' }),
          preview ? h('output', { class: 'voice-spoken-preview' }, preview.synthesis_text) : null,
          h('p', { class: 'hint' }, 'This previews the words sent to the model. Save, then audition speech in Voice Designer.')),
        btn('Save pronunciation & performances', 'save', action(async () => {
          const fresh = await api('PUT', `/api/npcs/${owner.id}/voice/studio`, studio);
          owner = fresh; await onSaved(fresh); toast('Studio settings saved; affected audio is marked outdated.'); dialog.close();
        }), { cls: 'primary' }))));
  }
  return { open };
}
