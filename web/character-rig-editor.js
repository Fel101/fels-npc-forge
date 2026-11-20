import { api, btn, h, safe, toast } from './core.js';

export function createCharacterRigEditor({ npcId, onSaved, onChange }) {
  let rigs = {}, kind = 'portrait', busy = false, name = '', file = null, restoreVersion = '';
  const action = fn => safe(async () => {
    if (busy) return; busy = true; onChange();
    try { await fn(); } finally { busy = false; onChange(); }
  });
  const load = async () => { rigs = await api('GET', `/api/npcs/${npcId()}/voice/rigs`); };
  const current = () => rigs[kind];
  const rig = () => {
    const value = current();
    if (!value?.frames || ['stale', 'missing', 'unconfigured'].includes(value.status)) return null;
    value.status = value.enabled ? 'ready' : 'disabled'; return value;
  };
  async function save(body) {
    const npc = await api('PUT', `/api/npcs/${npcId()}/voice/rig/${kind}`, body);
    await onSaved(npc); await load(); onChange(); toast('Character mouth artwork saved; speech preserved');
  }
  async function importFrame() {
    const value = current(); if (!file || !name.trim()) throw new Error('Name a mouth frame and choose its image');
    const bitmap = await createImageBitmap(file, { imageOrientation: 'from-image' });
    const [width, height] = value.source.size;
    try {
      if (Math.abs(bitmap.width / bitmap.height - width / height) > .01) throw new Error('Use the same image layout and aspect ratio as the character artwork');
      const canvas = document.createElement('canvas'); canvas.width = width; canvas.height = height;
      canvas.getContext('2d').drawImage(bitmap, 0, 0, width, height);
      const image = canvas.toDataURL('image/png');
      const npc = await api('POST', `/api/npcs/${npcId()}/voice/rig/${kind}/frames`, { name: name.trim().toLowerCase(), image });
      await onSaved(npc); await load(); name = ''; file = null; toast('Immutable mouth frame imported');
    } finally { bitmap.close(); }
  }
  function view() {
    const value = current();
    const usable = value?.frames && !['stale', 'missing', 'unconfigured'].includes(value.status);
    const history = (value?.versions || []).filter(row => row.version !== value.version).slice().reverse();
    if (!history.some(row => row.version === restoreVersion)) restoreVersion = history[0]?.version || '';
    const choose = (label, selected, change) => h('label', { class: 'stack' }, h('span', { class: 'lbl' }, label),
      h('select', { 'aria-label': label, disabled: busy, value: selected, onchange: e => { change(e.target.value); onChange(); } },
        Object.keys(value.frames).map(id => h('option', { value: id, selected: id === selected }, id))));
    return h('details', { class: 'character-mouth-editor' }, h('summary', {}, 'Character mouth artwork'),
      h('p', { class: 'hint' }, 'Animate this character’s own portrait or sprite with saved mouth frames. Aligned visemes use the mappings below; fallback audio uses the amplitude-open frame. Uploaded frames must share the character layout.'),
      h('label', { class: 'stack' }, h('span', { class: 'lbl' }, 'Character visual'), h('select', { 'aria-label': 'Character visual', value: kind, disabled: busy,
        onchange: e => { kind = e.target.value; onChange(); } }, ['portrait', 'sprite'].map(id => h('option', { value: id, selected: kind === id }, id === 'portrait' ? 'Portrait' : 'Sprite')))),
      h('p', { class: 'hint' }, value?.reason || `${Object.keys(value?.frames || {}).length} immutable frames · ${value?.interpolation || 'unconfigured'}`),
      value?.status === 'missing' ? null : !usable ? btn(value?.status === 'stale' ? 'Rebind to current artwork' : 'Create mouth artwork', '', action(() => save({ enabled: true, reset: true })), { cls: 'small', disabled: busy }) :
        h('div', { class: 'stack' },
          h('label', { class: 'check' }, h('input', { type: 'checkbox', checked: value.enabled, disabled: busy,
            onchange: e => { value.enabled = e.target.checked; onChange(); } }), 'Enable character mouth animation'),
          h('p', { class: 'hint' }, 'When several mouth groups share one image, they produce the same pose. A living-portrait talk frame is a two-frame starting point; import more artwork for distinct mouth shapes. Mapping edits preview immediately; save them for scenes and exports.'),
          h('div', { class: 'form-grid' }, Object.keys(value.mapping).map(group => choose(`Mouth image for ${group}`, value.mapping[group], id => { value.mapping[group] = id; })),
            choose('Amplitude-open image', value.amplitude_open, id => { value.amplitude_open = id; })),
          btn('Save mouth mappings', 'save', action(() => save({ enabled: value.enabled, mapping: value.mapping, amplitude_open: value.amplitude_open })), { cls: 'small', disabled: busy }),
          history.length ? h('div', { class: 'stack' }, h('label', { class: 'stack' }, 'Earlier mouth artwork',
            h('select', { 'aria-label': 'Earlier mouth artwork', disabled: busy, onchange: e => { restoreVersion = e.target.value; } },
              history.map(row => h('option', { value: row.version, selected: row.version === restoreVersion }, `${row.created} · ${row.version.slice(0, 8)}`)))),
            btn('Restore mouth artwork', '', action(async () => {
              const npc = await api('POST', `/api/npcs/${npcId()}/voice/rig/${kind}/restore/${restoreVersion}`, {});
              await onSaved(npc); await load(); toast('Earlier mouth artwork restored');
            }), { cls: 'small', disabled: busy })) : null,
          h('label', { class: 'stack' }, h('span', { class: 'lbl' }, 'Frame name'), h('input', { value: name, 'aria-label': 'Mouth frame name', disabled: busy, oninput: e => { name = e.target.value; } })),
          h('label', { class: 'stack' }, h('span', { class: 'lbl' }, 'Aligned mouth image'), h('input', { type: 'file', accept: 'image/png,image/jpeg,image/webp', 'aria-label': 'Aligned mouth image', disabled: busy,
            onchange: e => { file = e.target.files[0]; } })),
          h('p', { class: 'hint' }, 'Use a short lowercase name, for example open or closed. Original rest/talking frames stay intact. Images are copied into previews up to 512 pixels; source files are untouched.'),
          btn('Import mouth frame', 'upload', action(importFrame), { cls: 'small', disabled: busy })));
  }
  return { load, view, rig, kind: () => kind, busy: () => busy };
}
