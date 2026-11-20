import { h } from './core.js';

const labels = { pitch_semitones: 'Pitch (semitones)', highpass_hz: 'High-pass (Hz)', lowpass_hz: 'Low-pass (Hz)',
  distortion: 'Distortion (0–1)', ring_mod_hz: 'Robot modulation (Hz)', layer_mix: 'Second layer level (0–1)',
  layer_pitch_semitones: 'Second layer pitch (semitones)', layer_delay_ms: 'Second layer delay (ms)' };

export function effectControls({ value, caps, prefix, onChange, repaint }) {
  const current = { ...(caps.presets?.dry || {}), ...(value || {}) };
  const change = (key, value) => { current[key] = value; onChange(structuredClone(current)); };
  const picker = h('select', { 'aria-label': `${prefix} starting preset`, onchange: e => {
    if (e.target.value !== 'custom') { onChange(structuredClone(caps.presets[e.target.value])); repaint(); }
  } }, [['custom', 'Custom settings'], ['dry', 'Dry / original'], ['creature', 'Creature'], ['robot', 'Robot']].map(([id, label]) =>
    h('option', { value: id }, label)));
  picker.value = Object.keys(caps.presets || {}).find(id => JSON.stringify(caps.presets[id]) === JSON.stringify(current)) || 'custom';
  return h('div', { class: 'stack' }, h('label', { class: 'stack' }, h('span', { class: 'lbl' }, 'Starting preset'), picker),
    h('label', { class: 'check' }, h('input', { type: 'checkbox', checked: !!current.enabled, disabled: !caps.available,
      onchange: e => { change('enabled', e.target.checked); repaint(); } }), `${prefix} enabled`),
    h('fieldset', { class: 'form-grid voice-effects-fields', disabled: !caps.available || !current.enabled },
      Object.entries(labels).map(([key, label]) => h('label', { class: 'stack' }, h('span', { class: 'lbl' }, label),
        h('input', { type: 'number', min: caps.bounds?.[key]?.[0], max: caps.bounds?.[key]?.[1],
          step: ['distortion', 'layer_mix'].includes(key) ? .05 : 1, value: current[key] ?? 0,
          'aria-label': `${prefix} ${label}`, oninput: e => change(key, Number(e.target.value)) }))),
      h('label', { class: 'stack' }, h('span', { class: 'lbl' }, 'Formants during pitch shift'),
        h('select', { 'aria-label': `${prefix} formants`, value: current.formants || 'preserve',
          onchange: e => change('formants', e.target.value) },
          h('option', { value: 'preserve', selected: current.formants !== 'shift' }, 'Preserve formants'),
          h('option', { value: 'shift', selected: current.formants === 'shift' }, 'Shift formants with pitch')))));
}
