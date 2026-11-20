// The Who card's "Advanced options" popup: choose any character-sheet field before rolling, from the setting's own tables.
//
// A choice is stored per setting in S.recipe.presets[setting] as { field path: value } and sent to the server as a locked field
// (npcgen/roll.py roll_npc), for one roll and for a batch alike. Fields the Who card already narrows (gender, age group, occupation,
// origin, body plan, hair colour) edit those same constraints here, so the card and the popup can never disagree. Every list comes
// from GET /api/choices (roll.field_choices): "Random" first, the table's entries, then "Custom…", which opens a text box.
import { $, S, api, btn, h, saveRecipe } from './core.js';

const RANDOM = '', NONE = '__none__', CUSTOM = '__custom__';
const CHOICES = {};                 // setting id -> the /api/choices answer, fetched once per page load
const customOpen = new Set();       // fields whose "Custom…" box is open while it is still empty
const MAX_TRAITS = 3, MAX_HOBBIES = 2;

export function createAdvanced({ renderSidebar }) {
  const recipe = () => S.recipe;
  const setting = () => S.cfg.settings[recipe().setting];
  const presets = () => (recipe().presets[recipe().setting] ||= {});

  function setPreset(path, value) {
    const p = presets();
    if (value === undefined) delete p[path]; else p[path] = value;
    saveRecipe();
  }

  /** What rolling sends as `locked`: the popup's choices for the current setting. */
  function presetLocks() {
    return { ...(recipe().presets?.[recipe().setting] || {}) };
  }

  /** How many choices the popup holds beyond what the Who card already shows. */
  function presetCount() {
    return Object.keys(presetLocks()).length + (recipe().constraints.hair_style ? 1 : 0);
  }

  function clearPresets(all = false) {
    delete recipe().presets[recipe().setting];
    recipe().constraints.hair_style = '';
    if (all) for (const k of ['gender', 'age_group', 'occupation', 'origin', 'hair_color', 'morphology']) recipe().constraints[k] = '';
    customOpen.clear();
    saveRecipe();
  }

  async function openAdvanced() {
    const sid = recipe().setting;
    CHOICES[sid] ||= await api('GET', `/api/choices?setting=${encodeURIComponent(sid)}`);
    let dlg = $('#advanced');
    if (!dlg) {
      // The sidebar is redrawn by whatever closes the popup, not only by the dialog's 'close' event: an embedded browser was
      // measured never firing it (3 s after dialog.close()), which left the Who card without its "fields chosen" line.
      dlg = h('dialog', { id: 'advanced', class: 'about advanced', 'aria-labelledby': 'advancedTitle',
        onclick: e => { if (e.target === e.currentTarget) finish(); },         // a click on the backdrop
        onclose: () => renderSidebar(), oncancel: () => setTimeout(renderSidebar) });   // Esc
      document.body.append(dlg);
    }
    paint();
    if (!dlg.open) dlg.showModal();
  }

  function finish() {
    $('#advanced')?.close();
    renderSidebar();
  }

  // ---- controls ---------------------------------------------------------------------------------------------------------
  const labelFor = (text, id) => h('label', { class: 'lbl', htmlFor: id }, text);
  const idOf = path => 'adv-' + path.replace(/\./g, '-');

  /** A dropdown of table entries ({value, label} or plain strings) bound to a locked field, with Random, None (for fields where
   *  nothing is a real answer), and Custom… that opens a text box for your own words. */
  function pick(label, path, entries, { optional = false, hint = '', randomLabel = 'Random', wide = false } = {}) {
    const p = presets(), id = idOf(path);
    const items = entries.map(e => (typeof e === 'string' ? { value: e, label: e } : e));
    const has = Object.prototype.hasOwnProperty.call(p, path), value = p[path];
    const listed = has && items.some(e => e.value === value);
    const mode = !has ? (customOpen.has(path) ? CUSTOM : RANDOM) : (value === '' && optional ? NONE : listed ? value : CUSTOM);
    const select = h('select', { id, onchange: e => {
      const v = e.target.value;
      customOpen.delete(path);
      if (v === RANDOM) setPreset(path, undefined);
      else if (v === NONE) setPreset(path, '');
      else if (v === CUSTOM) { setPreset(path, undefined); customOpen.add(path); }
      else setPreset(path, v);
      paint();
      if (v === CUSTOM) document.getElementById(id + '-text')?.focus();
    } },
      h('option', { value: RANDOM }, randomLabel),
      optional && h('option', { value: NONE }, 'None'),
      items.map(e => h('option', { value: e.value }, e.label)),
      h('option', { value: CUSTOM }, 'Custom…'));
    select.value = mode;
    const text = mode === CUSTOM && h('input', { type: 'text', id: id + '-text', value: listed ? '' : (value || ''), placeholder: 'Your own words',
      'aria-label': `${label}: your own words`, oninput: e => {
        const v = e.target.value.trim();
        if (v) { setPreset(path, v); customOpen.delete(path); } else { setPreset(path, undefined); customOpen.add(path); }
      } });
    return h('div', { class: 'field' + (wide ? ' wide' : '') }, labelFor(label, id), select, text || null, hint ? h('p', { class: 'hint' }, hint) : null);
  }

  /** A dropdown bound to a Who-card constraint (an id), whose Custom… writes a locked field instead (text the table does not have). */
  function constraintPick(label, key, items, customPath, hint = '', customHint = '') {
    const r = recipe(), p = presets(), id = 'adv-c-' + key;
    const custom = customPath && (Object.prototype.hasOwnProperty.call(p, customPath) || customOpen.has(customPath));
    const select = h('select', { id, onchange: e => {
      const v = e.target.value;
      if (customPath) { setPreset(customPath, undefined); customOpen.delete(customPath); }
      if (v === CUSTOM) { r.constraints[key] = ''; customOpen.add(customPath); }
      else r.constraints[key] = v;
      saveRecipe(); paint();
      if (v === CUSTOM) document.getElementById(id + '-text')?.focus();
    } },
      h('option', { value: '' }, 'Random'),
      items.map(([v, text]) => h('option', { value: v }, text)),
      customPath && h('option', { value: CUSTOM }, 'Custom…'));
    select.value = custom ? CUSTOM : (r.constraints[key] || '');
    const text = custom && h('input', { type: 'text', id: id + '-text', value: p[customPath] || '', placeholder: 'Your own words',
      'aria-label': `${label}: your own words`, oninput: e => {
        const v = e.target.value.trim();
        if (v) { setPreset(customPath, v); customOpen.delete(customPath); } else { setPreset(customPath, undefined); customOpen.add(customPath); }
      } });
    const note = custom && customHint ? customHint : hint;
    return h('div', { class: 'field' }, labelFor(label, id), select, text || null, note ? h('p', { class: 'hint' }, note) : null);
  }

  function textField(label, path, { type = 'text', rows = 0, wide = false, placeholder = 'Random', min, max } = {}) {
    const id = idOf(path), value = presets()[path] ?? '';
    const control = rows
      ? h('textarea', { id, rows, value, placeholder, oninput: e => setPreset(path, e.target.value.trim() || undefined) })
      : h('input', { id, type, value, placeholder, min, max, oninput: e => {
        const v = e.target.value.trim();
        setPreset(path, v === '' ? undefined : type === 'number' ? Number(v) : v);
      } });
    return h('div', { class: 'field' + (wide ? ' wide' : '') }, labelFor(label, id), control);
  }

  /** Chips for a list field (traits, hobbies). Up to `max`; `clash(v, chosen)` greys out one that contradicts a chosen one. */
  function chips(label, path, entries, max, clash = () => false, partial = 'the rest are rolled') {
    const chosen = presets()[path] || [];
    const toggle = v => {
      const next = chosen.includes(v) ? chosen.filter(x => x !== v) : [...chosen, v];
      setPreset(path, next.length ? next : undefined);
      paint();
    };
    return h('div', { class: 'field wide' }, h('span', { class: 'lbl' }, label),
      h('div', { class: 'chips' }, entries.map(e => {
        const on = chosen.includes(e.value);
        const blocked = !on && (chosen.length >= max || clash(e.value, chosen));
        return h('button', { type: 'button', class: 'chip' + (on ? ' on' : ''), 'aria-pressed': String(on), disabled: blocked,
          title: blocked && chosen.length < max ? 'Clashes with a trait you chose' : '', onclick: () => toggle(e.value) }, e.label);
      })),
      h('p', { class: 'hint' }, chosen.length ? `${chosen.length} of up to ${max} chosen; ${partial}.` : `Pick up to ${max}, or leave them all random.`));
  }

  // ---- the popup ----------------------------------------------------------------------------------------------------------
  function paint() {
    const dlg = $('#advanced'); if (!dlg) return;
    const keep = dlg.querySelector('.about-body')?.scrollTop || 0;
    const r = recipe(), st = setting(), c = S.cfg, ch = CHOICES[r.setting];
    const gender = r.constraints.gender, origin = r.constraints.origin, occ = r.constraints.occupation;
    const grid = (...kids) => h('div', { class: 'form-grid' }, ...kids);
    const group = (title, ...kids) => h('section', { class: 'adv-group' }, h('h4', { class: 'lbl' }, title), grid(...kids));

    const originItems = st.origins.map(o => [o.id, o.label]);
    const bodyPlans = [...new Map((st.has_races ? st.origins.map(o => [o.morphology || 'bipedal', c.morphologies?.[o.morphology || 'bipedal']?.label || 'Bipedal'])
      : [['bipedal', 'Bipedal']])).entries()];
    const builds = ch.builds.filter(b => !gender || !b.g || b.g.includes(gender)).map(b => b.label);
    const outfits = occ ? (ch.outfits[occ]?.length ? ch.outfits[occ] : ch.default_outfits)
      .filter(o => !gender || !o.g || o.g === gender || gender === 'nonbinary').map(o => o.t) : [];
    const exclusive = ch.trait_exclusive;
    const traitClash = (v, chosen) => exclusive.some(([a, b]) => (a === v && chosen.includes(b)) || (b === v && chosen.includes(a)));
    const modeHint = r.mode === 'describe' ? 'Describe mode: your description draws the character, so the Appearance choices only fill in the data sheet.'
      : r.mode === 'photo' ? 'From a photo: the face, skin and hair come from your photo. The outfit, features and everything else still apply.' : '';

    dlg.replaceChildren(
      h('div', { class: 'about-body' },
        h('div', { class: 'about-head' },
          h('div', { class: 'grow' }, h('h2', { id: 'advancedTitle' }, 'Advanced options'),
            h('p', { class: 'hint' }, `${st.label}. Anything left on Random is rolled. These apply to Roll NPC and to batches, in this setting only.`)),
          btn('Close', '', finish, { cls: 'small ghost' })),
        modeHint ? h('p', { class: 'notice' }, modeHint) : null,

        group('Identity',
          textField('First name', 'profile.first_name'),
          textField('Last name', 'profile.last_name'),
          constraintPick('Gender', 'gender', (st.genders || c.genders).map(g => [g.id, g.label])),
          constraintPick('Age group', 'age_group', (st.age_groups || c.age_groups).map(g => [g.id, g.label])),
          textField('Exact age', 'profile.age', { type: 'number', min: st.min_age || 1, max: 120, placeholder: 'Random (wins over the age group)' }),
          constraintPick(st.origin_label, 'origin', originItems, st.has_races ? 'profile.race' : 'profile.heritage'),
          constraintPick('Body plan', 'morphology', bodyPlans),
          constraintPick('Occupation', 'occupation', st.occupations.map(o => [o.id, o.label]), 'profile.occupation', '',
            'A job of your own has no outfits listed for it: write an outfit below, or it is dressed in the setting\'s everyday clothes.')),

        group('Appearance',
          pick('Build', 'appearance.build', builds),
          pick('Skin tone', 'appearance.skin_tone', ch.skin[origin] || ch.skin.all),
          constraintPick('Hair colour', 'hair_color', (st.hair_colors || []).map(x => [x, x])),
          constraintPick('Hair style', 'hair_style', ch.hair_styles.map(x => [x, x]), 'appearance.hair',
            'Custom replaces the whole hair description, colour included.'),
          pick('Eyes', 'appearance.eyes', ch.eyes[origin] || ch.eyes.all),
          pick('Facial hair', 'appearance.facial_hair', ch.facial_hair, { optional: true }),
          pick('Glasses', 'appearance.glasses', ch.glasses, { optional: true }),
          pick('Distinguishing features', 'appearance.distinguishing', ch.distinguishing, { optional: true }),
          pick('Accessory', 'appearance.accessory', ch.accessories, { optional: true }),
          pick('Default expression', 'appearance.expression', ch.expressions, { randomLabel: 'Suits the traits' }),
          pick('Outfit', 'appearance.outfit', outfits, { randomLabel: 'Suits the job', wide: true,
            hint: occ ? '' : 'Choose an occupation above to pick from its outfits, or write your own.' }),
          textField('Extra notes for the artist', 'appearance.extra', { rows: 2, wide: true, placeholder: 'Optional' })),

        group('Personality',
          chips('Traits', 'profile.personality', ch.traits.map(t => ({ value: t, label: t })), MAX_TRAITS, traitClash),
          chips('Hobbies', 'profile.hobbies', ch.hobbies, MAX_HOBBIES, undefined, 'only these are used'),
          pick('Quirk', 'profile.quirk', ch.quirks),
          pick('Life goal', 'profile.goal', ch.goals),
          pick('Pet peeve', 'profile.peeve', ch.peeves),
          pick('How they talk', 'profile.speech', ch.speech, { randomLabel: 'Suits the traits' }),
          pick('How they come across', 'profile.demeanor', ch.demeanor, { randomLabel: 'Suits the traits' })),

        group('Bio',
          textField('Bio', 'profile.bio', { rows: 3, wide: true, placeholder: 'Random: written from the rest of the sheet' }))),

      h('div', { class: 'about-foot adv-foot' },
        h('span', { class: 'hint grow' }, presetCount() ? `${presetCount()} field${presetCount() === 1 ? '' : 's'} chosen here, plus the Who card.` : 'Everything here is random.'),
        btn('Clear all', 'x', () => { clearPresets(true); paint(); }, { cls: 'small ghost', title: 'Set every field here, and the Who card, back to Random' }),
        btn('Done', '', finish, { cls: 'small primary' })));
    const body = dlg.querySelector('.about-body'); if (body) body.scrollTop = keep;
  }

  return { openAdvanced, presetLocks, presetCount, clearPresets };
}
