// Filters for a list of characters, shared by the Library tab and any page that lists characters.
// A plain script with no dependencies: it defines window.Facets.
//
// A facet is { key, any, values(row) -> [strings], label?(value) -> text, order? -> [values in display order] }.
// The value chosen for a facet lives in filter[key] ('' = any) and the search words in filter.q. Only values that exist in the
// list are offered, each with how many characters it shows given the OTHER choices, so a choice never leads to an empty page.
(function () {
  'use strict';

  const AGE_BANDS = [['Under 18', 0, 17], ['18-24', 18, 24], ['25-29', 25, 29], ['30-39', 30, 39], ['40-49', 40, 49], ['50-64', 50, 64], ['65 and over', 65, 999]];
  const ageBand = age => {
    const n = typeof age === 'number' ? age : parseInt(age, 10);
    const band = Number.isFinite(n) ? AGE_BANDS.find(b => n >= b[1] && n <= b[2]) : null;
    return band ? band[0] : '';
  };
  const cap = s => (s ? String(s).charAt(0).toUpperCase() + String(s).slice(1) : '');
  const list = v => (Array.isArray(v) ? v : [v]).filter(x => x !== '' && x !== false && x != null);

  // What every character list can be filtered by (the rows come from /api/npcs; see Library.list in npcgen/library.py)
  const TRAITS = [
    { key: 'gender', any: 'Any gender', values: n => [cap(n.gender)] },
    { key: 'ethnicity', any: 'Any ethnicity', values: n => [n.ethnicity] },
    { key: 'heritage', any: 'Any heritage', values: n => [n.heritage] },
    { key: 'hair_color', any: 'Any hair colour', values: n => [n.hair_color] },
    { key: 'eyes', any: 'Any eye colour', values: n => [cap(n.eyes)] },
    { key: 'age', any: 'Any age', values: n => [ageBand(n.age)], order: AGE_BANDS.map(b => b[0]) },
    { key: 'build', any: 'Any build', values: n => [cap(n.build)] },
  ];

  const shown = (facet, value) => (facet.label ? facet.label(value) : value);

  // Everything a person might type: the visible words of every facet plus the name, job, skin and hair description
  function haystack(n, facets) {
    const words = [n.name, n.occupation, n.hair, n.skin_tone];
    for (const f of facets) for (const v of list(f.values(n))) words.push(shown(f, v));
    return words.filter(w => w != null && w !== '').join(' ').toLowerCase();
  }

  // skip: a facet key to leave out (used to count what a facet's own values would show)
  function matches(n, filter, facets, skip) {
    for (const f of facets) {
      const want = filter[f.key];
      if (f.key !== skip && want && !list(f.values(n)).includes(want)) return false;
    }
    const words = String(filter.q || '').toLowerCase().split(/\s+/).filter(Boolean);
    if (!words.length) return true;
    const text = haystack(n, facets);
    return words.every(w => text.includes(w));
  }

  const apply = (rows, filter, facets) => rows.filter(n => matches(n, filter, facets));

  // [{value, label, count}] for one facet, in its own order (or alphabetical)
  function options(rows, filter, facets, facet) {
    const counts = new Map();
    for (const n of rows) {
      if (!matches(n, filter, facets, facet.key)) continue;
      for (const v of new Set(list(facet.values(n)))) counts.set(v, (counts.get(v) || 0) + 1);
    }
    const chosen = filter[facet.key];
    if (chosen && !counts.has(chosen)) counts.set(chosen, 0);
    const out = [...counts].map(([value, count]) => ({ value, count, label: shown(facet, value) }));
    return out.sort(facet.order ? (a, b) => facet.order.indexOf(a.value) - facet.order.indexOf(b.value) : (a, b) => a.label.localeCompare(b.label));
  }

  // One <select> per facet that has something to choose from (a facet with a single value and no choice made is left out)
  function controls(rows, filter, facets, onChange) {
    const out = [];
    for (const facet of facets) {
      const opts = options(rows, filter, facets, facet);
      if (opts.length < 2 && !filter[facet.key]) continue;
      const el = document.createElement('select');
      el.className = 'facet' + (filter[facet.key] ? ' on' : '');
      el.setAttribute('aria-label', facet.any.replace(/^(Any|All) /, ''));
      el.append(new Option(facet.any, ''), ...opts.map(o => new Option(`${o.label} (${o.count})`, o.value)));
      el.value = filter[facet.key] || '';
      el.addEventListener('change', () => { filter[facet.key] = el.value; onChange(); });
      out.push(el);
    }
    return out;
  }

  const active = (filter, facets) => Boolean(String(filter.q || '').trim()) || facets.some(f => filter[f.key]);
  function clear(filter, facets) {
    filter.q = '';
    for (const f of facets) filter[f.key] = '';
  }

  window.Facets = { AGE_BANDS, TRAITS, ageBand, matches, apply, options, controls, active, clear };
})();
