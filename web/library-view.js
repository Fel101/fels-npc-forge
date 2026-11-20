import { $, S, api, btn, debounce, h, safe, saveRecipe, toast } from './core.js';
import { commercialDot, ratingNames } from './licences-view.js';

const Facets = window.Facets;

export function createLibraryController({ renderTabs, updateHead, importCast, generateSelected, render, voiceBatch, voiceReview, createSceneFromPack }) {
  let packDraft = null;
  let selectedPackId = '';

  async function refreshLibrary() {
    try { S.library = await api('GET', '/api/npcs'); } catch { return; }
    if (!S.outdated) {
      try { S.trash = await api('GET', '/api/trash'); } catch { S.trash = []; }
      if (!S.trash.length) S.trashOpen = false;
      try { S.castPacks = await api('GET', '/api/cast-packs'); } catch { S.castPacks = []; }
      if (selectedPackId && !S.castPacks.some(pack => pack.id === selectedPackId)) {
        selectedPackId = ''; packDraft = null;
      }
    }
    renderTabs();
    updateHead?.();                                       // (the open character's commercial-use badge reads the new list)
    if (S.view === 'library') renderLibrary();
  }

  function libraryFacets() {
    const label = (table, id) => table[id]?.label || id;
    return [
      { key: 'setting', any: 'All settings', values: npc => [npc.setting], label: id => label(S.cfg.settings, id) },
      { key: 'style', any: 'All styles', values: npc => [npc.style], label: id => label(S.cfg.styles, id) },
      ...Facets.TRAITS,
      { key: 'has', any: 'Any images', order: ['With portrait', 'With sprite', 'With expressions', 'With model sheet', 'No images yet'],
        values: npc => {
          const has = [npc.has.portrait && 'With portrait', npc.has.sprite && 'With sprite', npc.has.expressions && 'With expressions',
            npc.has.sheet && 'With model sheet'].filter(Boolean);
          return has.length ? has : ['No images yet'];
        } },
      { key: 'voice', any: 'Any voice', order: ['Voice picked', 'Voice to review', 'No voice'],
        values: npc => [{ picked: 'Voice picked', review: 'Voice to review' }[npc.voice] || 'No voice'] },
      ...commercialFacet(),
    ];
  }

  // Whether a character may be used in work you sell, as the licence list rates the models that made it (npcgen/licences.py)
  function commercialFacet() {
    if (!S.cfg.licences) return [];
    const names = ratingNames();
    return [{ key: 'commercial', any: 'Any commercial use', order: Object.values(names), values: npc => [names[npc.commercial] || names.none] }];
  }

  const reviewCount = () => S.library.filter(npc => npc.voice === 'review').length;

  const trashDays = () => S.cfg.library?.trash_days ?? 30;

  // The characters that were deleted: kept for trash_days days (0: until the trash is emptied), and brought back from here.
  function renderTrash() {
    const days = trashDays();
    const left = row => Math.max(0, Math.ceil(days - (Date.now() - new Date(row.deleted).getTime()) / 86400000));
    const when = row => new Date(row.deleted).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
    const card = row => h('article', { class: 'npc-card trash-card' },
      h('div', { class: 'thumb' }, row.thumb ? h('img', { src: `/api/trash/${row.folder}/thumb.png`, alt: '', loading: 'lazy' }) : 'No images'),
      h('div', { class: 'body' }, h('div', { class: 'name' }, row.name || row.id),
        h('div', { class: 'sub' }, [row.age, row.occupation].filter(Boolean).join(' · ')),
        h('div', { class: 'sub' }, `Deleted ${when(row)}`),
        days > 0 ? h('div', { class: 'sub' + (left(row) <= 3 ? ' warn-text' : '') },
          left(row) ? `Removed for good in ${left(row)} day${left(row) === 1 ? '' : 's'}` : 'Removed for good on the next start') : null,
        h('div', { class: 'row wrap', style: { gap: '6px', marginTop: '6px' } },
          btn('Restore', 'undo', safe(() => restoreTrashed(row)), { cls: 'small primary', title: 'Bring this character back to the library with everything it had' }),
          btn('Delete forever', 'trash', safe(() => purgeTrashed(row)), { cls: 'small danger', title: 'Remove this character for good: it cannot be brought back' }))));
    $('#view').replaceChildren(h('div', { class: 'library' },
      h('div', { class: 'lib-bar' }, btn('Back to the library', 'x', () => { S.trashOpen = false; renderLibrary(); }, { cls: 'ghost' }),
        h('h2', { class: 'trash-title' }, `Trash · ${S.trash.length}`), h('span', { class: 'grow' }),
        btn('Empty the trash', 'trash', safe(emptyTrash), { cls: 'danger', disabled: !S.trash.length })),
      h('p', { class: 'hint' }, days > 0
        ? `A deleted character waits here for ${days} days and is then removed for good. Restore brings it back with everything it had (its pictures, 3D model, voice), except its place in cast packs.`
        : 'A deleted character waits here until you empty the trash. Restore brings it back with everything it had (its pictures, 3D model, voice), except its place in cast packs.'),
      h('div', { class: 'lib-grid' }, S.trash.map(card))));
  }

  async function restoreTrashed(row) {
    const card = await api('POST', `/api/trash/${row.id}/restore`);
    toast(`${card.name || row.name} is back in the library.`);
    await refreshLibrary();
  }

  async function purgeTrashed(row) {
    if (!confirm(`Delete ${row.name || row.id} for good? This cannot be undone.`)) return;
    await api('DELETE', `/api/trash/${row.folder}`);
    toast(`${row.name || row.id} was removed for good.`);
    await refreshLibrary();
  }

  async function emptyTrash() {
    if (!confirm(`Remove ${S.trash.length} character${S.trash.length === 1 ? '' : 's'} for good? This cannot be undone.`)) return;
    const { removed } = await api('POST', '/api/trash/empty');
    toast(`Removed ${removed} character${removed === 1 ? '' : 's'} for good.`);
    await refreshLibrary();
  }

  function renderLibrary() {
    if (S.trashOpen && S.trash.length) return renderTrash();
    const filter = S.filter; const facets = libraryFacets();
    const items = Facets.apply(S.library, filter, facets);
    const ids = [...S.selected].filter(id => S.library.some(npc => npc.id === id));
    const search = h('input', { type: 'text', placeholder: 'Search name, job, heritage, hair…', value: filter.q, 'aria-label': 'Search',
      oninput: debounce(event => { filter.q = event.target.value; renderLibrary(); $('.lib-bar input')?.focus(); }, 250) });
    $('#view').replaceChildren(h('div', { class: 'library' },
      castPackPanel(),
      h('div', { class: 'lib-bar' }, search, h('span', { class: 'grow' }),
        btn(ids.length ? `Export ${ids.length} selected` : 'Export selected', 'download', () => { location.href = '/api/export.zip?ids=' + ids.join(','); }, { disabled: !ids.length }),
        btn('Export all', 'download', () => { location.href = '/api/export.zip?ids=all'; }, { disabled: !S.library.length }),
        h('input', { type: 'file', id: 'cast-file', accept: '.json,.csv,.txt,application/json,text/csv', hidden: true, onchange: safe(importCast) }),
        btn('Import cast', 'plus', () => $('#cast-file').click(), { disabled: S.outdated, title: S.outdated ? 'Needs the updated server: restart NPC Forge' : 'Create characters from a JSON or CSV file' }),
        btn(ids.length ? `Generate for ${ids.length}` : 'Generate for selected', 'zap', safe(generateSelected), { disabled: !ids.length || S.outdated, title: 'Generate the assets ticked in the Create tab for the selected characters' }),
        btn(ids.length ? `Voice ${ids.length}` : 'Voice selected', 'zap', safe(() => voiceBatch(ids)), { disabled: !ids.length || S.outdated, title: 'Give the selected characters a voice (the one their sheet suggests) and optionally voice their dialogue' }),
        reviewCount() ? btn(`Review voices (${reviewCount()})`, 'refresh', safe(voiceReview), { title: 'Play the takes of each character and pick one (keys 1-4)' }) : null,
        btn('Delete selected', 'trash', safe(deleteSelected), { cls: 'danger', disabled: !ids.length }),
        btn(S.trash.length ? `Trash (${S.trash.length})` : 'Trash', 'trash', () => { S.trashOpen = true; renderLibrary(); },
          { cls: 'ghost', disabled: !S.trash.length, title: S.trash.length ? 'Deleted characters wait here: bring one back, or remove it for good' : 'Nothing has been deleted' })),
      h('div', { class: 'lib-filters' }, Facets.controls(S.library, filter, facets, renderLibrary),
        Facets.active(filter, facets) && btn('Clear filters', 'x', () => { Facets.clear(filter, facets); renderLibrary(); }, { cls: 'small ghost' }),
        S.library.length ? h('span', { class: 'lib-count' }, `${items.length} of ${S.library.length}`) : null),
      items.length ? h('div', { class: 'lib-grid' }, items.map(libCard))
        : h('div', { class: 'empty' }, h('h2', {}, S.library.length ? 'Nothing matches' : 'Your library is empty'),
          h('p', {}, S.library.length ? 'Try a different search or filter.' : 'Generated NPCs are saved here as folders of PNGs plus an npc.json.'),
          S.library.length ? null : btn('Create an NPC', 'dice', () => { S.view = 'create'; render(); }, { cls: 'primary' }))));
  }

  function castPackPanel() {
    const options = [h('option', { value: '' }, 'Choose a saved pack…'), ...S.castPacks.map(pack =>
      h('option', { value: pack.id }, `${pack.name} · ${pack.members.length} NPC${pack.members.length === 1 ? '' : 's'}`))];
    const picker = h('select', { 'aria-label': 'Saved cast packs', onchange: event => {
      selectedPackId = event.target.value;
      const selected = S.castPacks.find(pack => pack.id === selectedPackId);
      packDraft = selected ? structuredClone(selected) : null;
      renderLibrary();
    } }, options);
    picker.value = selectedPackId;
    return h('section', { class: 'cast-pack-panel' },
      h('div', { class: 'cast-pack-head' },
        h('div', {}, h('h2', {}, 'Cast packs'),
          h('p', { class: 'hint' }, 'Group NPCs with shared context, roles, relationships, and story hooks.')),
        h('div', { class: 'row wrap' },
          picker,
          btn('Scene from pack', 'plus', () => createSceneFromPack(packDraft),
            { disabled: S.outdated || !packDraft || packDraft.members.length < 2,
              title: 'Start a scene with the first two pack members' }),
          btn('Create from selected', 'plus', safe(newPackFromSelection), { disabled: !S.selected.size || S.outdated }),
          h('input', { type: 'file', id: 'cast-pack-file', accept: '.json,.csv,.txt,application/json,text/csv', hidden: true, onchange: safe(importPack) }),
          btn('Import as pack', 'download', () => $('#cast-pack-file').click(), { disabled: S.outdated,
            title: 'Import a JSON or CSV cast and group its NPCs into a pack' }))),
      packDraft ? renderPackEditor() : null);
  }

  function renderPackEditor() {
    const draft = packDraft;
    const set = (key, value) => { draft[key] = value; };
    const input = (label, key, placeholder = '') => h('label', { class: 'pack-field' }, h('span', {}, label),
      h('input', { type: 'text', value: draft[key] || '', placeholder, maxlength: key === 'name' ? 100 : undefined,
        oninput: event => set(key, event.target.value) }));
    const textarea = (label, key, hint = '') => h('label', { class: 'pack-field' }, h('span', {}, label),
      h('textarea', { value: Array.isArray(draft[key]) ? draft[key].join('\n') : draft[key] || '', placeholder: hint,
        oninput: event => set(key, key === 'shared_context' ? event.target.value : event.target.value.split(/\r?\n/).map(line => line.trim()).filter(Boolean)) }));
    const select = (value, options, onchange, label) => {
      const element = h('select', { 'aria-label': label, onchange }, options.map(([id, text]) => h('option', { value: id }, text)));
      element.value = value;
      return element;
    };
    const available = S.library.filter(npc => !draft.members.some(member => member.npc_id === npc.id));
    const npcName = id => S.library.find(npc => npc.id === id)?.name || `Missing NPC (${id})`;
    const memberOptions = draft.members.map(member => [member.npc_id, npcName(member.npc_id)]);
    const templateOptions = [['custom', 'Custom roster'], ['party', 'Adventuring party'], ['guild', 'Guild'], ['town', 'Town'], ['ship_crew', 'Ship crew']];
    const roleSuggestions = { party: ['Frontliner', 'Support', 'Scout', 'Face'], guild: ['Leader', 'Agent', 'Specialist', 'Member'],
      town: ['Official', 'Merchant', 'Resident', 'Outsider'], ship_crew: ['Captain', 'First mate', 'Navigator', 'Crew'] };
    return h('div', { class: 'cast-pack-editor' },
      h('div', { class: 'pack-grid' },
        input('Pack name', 'name', 'e.g. Lanternwatch crew'),
        h('label', { class: 'pack-field' }, h('span', {}, 'Roster type'), select(draft.template || 'custom', templateOptions,
          event => set('template', event.target.value), 'Roster type')),
        h('div', { class: 'pack-field wide' }, textarea('Shared setting and context', 'shared_context', 'Where this group belongs, what they share, and how to use them…')),
        h('label', { class: 'pack-field wide' }, h('span', {}, 'Role rule'),
          h('span', { class: 'row' }, h('input', { type: 'checkbox', checked: !!draft.unique_roles,
            onchange: event => set('unique_roles', event.target.checked) }), 'Require a different role for each member')),
        h('div', { class: 'pack-field wide' }, textarea('Additional uniqueness notes', 'uniqueness_constraints', 'Notes to guide your cast; NPC Forge does not check that they are followed'))),
      h('div', { class: 'pack-subhead' }, h('h3', {}, `Members (${draft.members.length})`),
        btn('Suggest roles', 'dice', () => { const roles = roleSuggestions[draft.template] || [];
          draft.members.forEach((member, index) => { if (!member.role.trim() && roles[index]) member.role = roles[index]; }); renderLibrary(); },
          { cls: 'small', disabled: !roleSuggestions[draft.template] || !draft.members.some(member => !member.role.trim()) }),
        btn('Add selected', 'plus', () => { addSelectedMembers(); renderLibrary(); }, { cls: 'small', disabled: ![...S.selected].some(id => available.some(npc => npc.id === id)) }),
        btn('Add member', 'plus', () => { if (!available.length) return toast('Every library NPC is already in this pack.', 'error');
          draft.members.push({ npc_id: available[0].id, role: '', notes: '' }); renderLibrary(); }, { cls: 'small', disabled: !available.length })),
      h('div', { class: 'pack-members' }, draft.members.map((member, index) => h('div', { class: 'pack-member' },
        select(member.npc_id, [...memberOptions, ...available.map(npc => [npc.id, npc.name])], event => {
          if (draft.members.some((other, i) => i !== index && other.npc_id === event.target.value)) {
            toast('An NPC can only appear once in a pack.', 'error'); event.target.value = member.npc_id; return;
          }
          const removed = member.npc_id;
          member.npc_id = event.target.value;
          draft.relationships = draft.relationships.filter(edge => edge.from !== removed && edge.to !== removed);
        }, `Member ${index + 1}`),
        h('input', { type: 'text', value: member.role || '', placeholder: 'Role', 'aria-label': `Role for ${npcName(member.npc_id)}`,
          oninput: event => { member.role = event.target.value; } }),
        h('input', { type: 'text', value: member.notes || '', placeholder: 'Member notes', 'aria-label': `Notes for ${npcName(member.npc_id)}`,
          oninput: event => { member.notes = event.target.value; } }),
        btn('', 'x', () => { const removed = member.npc_id; draft.members.splice(index, 1);
          draft.relationships = draft.relationships.filter(edge => edge.from !== removed && edge.to !== removed); renderLibrary(); },
          { cls: 'small ghost', title: 'Remove member' })))),
      h('div', { class: 'pack-subhead' }, h('h3', {}, `Relationships (${draft.relationships.length})`),
        btn('Add relationship', 'plus', () => { if (draft.members.length < 2) return toast('Add at least two members first.', 'error');
          draft.relationships.push({ from: draft.members[0].npc_id, to: draft.members[1].npc_id, type: 'ally', notes: '' }); renderLibrary(); },
          { cls: 'small', disabled: draft.members.length < 2 })),
      h('div', { class: 'pack-relationships' }, draft.relationships.map((edge, index) => h('div', { class: 'pack-relation' },
        select(edge.from, memberOptions, event => { edge.from = event.target.value; }, `Relationship ${index + 1} source`),
        select(edge.to, memberOptions, event => { edge.to = event.target.value; }, `Relationship ${index + 1} target`),
        h('input', { type: 'text', value: edge.type || '', placeholder: 'Relationship', 'aria-label': 'Relationship type', oninput: event => { edge.type = event.target.value; } }),
        h('input', { type: 'text', value: edge.notes || '', placeholder: 'Notes', 'aria-label': 'Relationship notes', oninput: event => { edge.notes = event.target.value; } }),
        btn('', 'x', () => { draft.relationships.splice(index, 1); renderLibrary(); }, { cls: 'small ghost', title: 'Remove relationship' })))),
      h('div', { class: 'pack-grid' },
        h('div', { class: 'pack-field' }, textarea('Shared rumors', 'rumors', 'One rumor per line')),
        h('div', { class: 'pack-field' }, textarea('Plot hooks', 'plot_hooks', 'One hook per line'))),
      h('div', { class: 'row wrap pack-actions' },
        btn(draft.id ? 'Save pack' : 'Create pack', 'save', safe(savePack), { cls: 'primary' }),
        draft.id && btn('Export pack', 'download', () => { location.href = `/api/cast-packs/${draft.id}/export.zip`; }),
        draft.id && btn('Delete pack', 'trash', safe(deletePack), { cls: 'danger' }),
        btn('Close editor', 'x', () => { packDraft = null; selectedPackId = ''; renderLibrary(); }, { cls: 'ghost' })));
  }

  function addSelectedMembers() {
    const present = new Set(packDraft.members.map(member => member.npc_id));
    for (const id of S.selected) if (!present.has(id) && S.library.some(npc => npc.id === id)) {
      packDraft.members.push({ npc_id: id, role: '', notes: '' }); present.add(id);
    }
  }

  function newPackFromSelection() {
    const members = [...S.selected].filter(id => S.library.some(npc => npc.id === id)).map(npc_id => ({ npc_id, role: '', notes: '' }));
    if (!members.length) return toast('Select one or more NPCs in the library first.', 'error');
    selectedPackId = '';
    packDraft = { name: 'New cast pack', template: 'custom', shared_context: '', unique_roles: false, uniqueness_constraints: [],
      rumors: [], plot_hooks: [], members, relationships: [] };
    renderLibrary();
  }

  async function savePack() {
    if (!packDraft.name.trim()) return toast('Give this cast pack a name.', 'error');
    const saved = await api(packDraft.id ? 'PUT' : 'POST', packDraft.id ? `/api/cast-packs/${packDraft.id}` : '/api/cast-packs', packDraft);
    selectedPackId = saved.id; packDraft = structuredClone(saved);
    S.castPacks = await api('GET', '/api/cast-packs');
    renderLibrary(); toast('Cast pack saved');
  }

  async function deletePack() {
    if (!packDraft?.id || !confirm(`Delete cast pack “${packDraft.name}”? Its NPCs will remain in the library.`)) return;
    await api('DELETE', `/api/cast-packs/${packDraft.id}`);
    selectedPackId = ''; packDraft = null; S.castPacks = await api('GET', '/api/cast-packs'); renderLibrary();
    toast('Cast pack deleted');
  }

  async function importPack(event) {
    const file = event.target.files[0]; event.target.value = '';
    if (!file) return;
    const baseName = file.name.replace(/\.[^.]+$/, '') || 'Imported cast';
    const result = await api('POST', '/api/cast-packs/import', { text: await file.text(),
      format: /\.csv$/i.test(file.name) ? 'csv' : 'json', pack: { name: baseName },
      setting: S.recipe.setting, style: S.recipe.style });
    result.warnings.forEach(warning => toast(warning, 'error'));
    S.selected = new Set(result.npc_ids);
    await refreshLibrary();
    selectedPackId = result.pack.id; packDraft = structuredClone(result.pack); renderLibrary();
    toast(`Imported ${result.count} NPC${result.count === 1 ? '' : 's'} into “${result.pack.name}”.`);
  }

  function libCard(npc) {
    const selected = S.selected.has(npc.id);
    const bits = [npc.has.portrait && 'portrait', npc.has.sprite && 'sprite', npc.has.expressions && `${npc.has.expressions} expression${npc.has.expressions === 1 ? '' : 's'}`].filter(Boolean).join(' · ');
    return h('article', { class: 'npc-card' + (selected ? ' sel' : ''), onclick: safe(() => openNpc(npc.id)) },
      h('input', { type: 'checkbox', class: 'pick', checked: selected, 'aria-label': `Select ${npc.name}`, onclick: event => event.stopPropagation(),
        onchange: event => { event.target.checked ? S.selected.add(npc.id) : S.selected.delete(npc.id); renderLibrary(); } }),
      h('div', { class: 'thumb' }, npc.thumb ? h('img', { src: `/api/npcs/${npc.id}/files/thumb.png?v=${encodeURIComponent(npc.updated)}`,
        alt: '', loading: 'lazy' }) : 'No images yet'),
      h('div', { class: 'body' }, h('div', { class: 'name' }, npc.name), h('div', { class: 'sub' }, [npc.age, npc.occupation].filter(Boolean).join(' · ')),
        h('div', { class: 'row wrap', style: { gap: '4px' } }, h('span', { class: 'badge accent' }, S.cfg.styles[npc.style]?.label || npc.style || '—'),
          commercialDot(npc), bits && h('span', { class: 'cap' }, bits))));
  }

  async function openNpc(id) {
    if (S.draft?.id && (S.dirty || S.dialogueDirty) && !confirm('The NPC you are editing has unsaved changes. Open another anyway?')) return;
    S.draft = await api('GET', `/api/npcs/${id}`); S.dirty = false; S.dialogueDirty = false; S.locks.clear();
    if (S.cfg.settings[S.draft.setting]) S.recipe.setting = S.draft.setting;
    S.recipe.style = S.draft.style; saveRecipe();
    S.view = 'create'; render();
  }

  async function deleteSelected() {
    const ids = [...S.selected];
    const days = trashDays();
    if (!ids.length || !confirm(`Delete ${ids.length} NPC${ids.length === 1 ? '' : 's'} with all their images? They move to the Trash, where you can bring them back${days > 0 ? ` for ${days} days` : ''}.`)) return;
    await Promise.all(ids.map(id => api('DELETE', `/api/npcs/${id}`)));
    if (S.draft && ids.includes(S.draft.id)) { S.draft = null; S.dirty = false; S.dialogueDirty = false; }
    S.selected.clear();
    await refreshLibrary();
    toast(`Moved ${ids.length} NPC${ids.length === 1 ? '' : 's'} to the Trash.`);
  }

  return { refreshLibrary, renderLibrary, openNpc };
}
