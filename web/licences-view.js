// What every model is licensed for and whether it may be used commercially (data/licences.json, npcgen/licences.py): the badge and the
// one-line note under each model the page offers, the list of every model, and the report on one character.
import { $, S, api, btn, h, safe } from './core.js';

const CLASS = { yes: 'ok', conditions: 'accent', unclear: 'warn', no: 'bad' };
const MARK = { yes: '✓', conditions: '!', unclear: '?', no: '✕' };
const TAG = { yes: 'commercial OK', conditions: 'commercial, with conditions', unclear: 'not cleared', no: 'non-commercial' };

const catalogue = () => S.cfg?.licences;
export const classOf = status => CLASS[status] || '';

/** The model a selector reaches ("image:zimage", "voice:qwen"...), or null when the server does not list licences. */
export function modelFor(selector) {
  const c = catalogue();
  const id = c?.by_selector?.[selector];
  return id ? c.models.find(m => m.id === id) || null : null;
}

/** A small badge for a status: coloured, with a mark, and the meaning of the status as its tooltip. */
export function statusBadge(status, text) {
  const info = (catalogue()?.statuses || []).find(s => s.id === status);
  return h('span', { class: `badge licence ${CLASS[status] || ''}`, title: info?.meaning || '' }, `${MARK[status] || ''} ${text ?? info?.short ?? status}`.trim());
}

/** What goes after a model's name in a drop-down, so the choice is visible without opening anything: " · non-commercial". */
export function optionTag(selector) {
  const model = modelFor(selector);
  return model ? ` · ${TAG[model.effective] || ''}` : '';
}

/** Under a picker: the badge, the licence in a few words, and a button that opens the full list at that model. */
export function licenceNote(selector) {
  const model = modelFor(selector);
  if (!model) return null;
  return h('div', { class: 'licence-note' }, statusBadge(model.effective), h('span', { class: 'licence-short' }, `${model.licence} · ${model.short}`),
    btn('Details', '', safe(() => openLicences({ focus: model.id })), { cls: 'small ghost', title: 'What its licence says, and the links' }));
}

/** The name of each rating for lists and filters ("Commercial use allowed", ... "Nothing made yet"), as the server's licence list words them. */
export function ratingNames() {
  const names = Object.fromEntries((catalogue()?.statuses || []).map(s => [s.id, s.label]));
  names.none = 'Nothing made yet';
  return names;
}

/** A small coloured mark for a Library card: the character's rating, the meaning of it as the tooltip. Nothing for a character with nothing made. */
export function commercialDot(npc) {
  const info = (catalogue()?.statuses || []).find(s => s.id === npc?.commercial);
  if (!info) return null;
  return h('span', { class: `licence-dot ${CLASS[info.id] || ''}`, role: 'img', 'aria-label': info.label, title: `${info.label}. ${info.meaning}` }, MARK[info.id]);
}

const countOf = status => S.library.filter(npc => npc.commercial === status).length;

/** The badge on a character's header: the verdict from the Library index, and a click opens the report with the models behind it. */
export function commercialBadge(npc) {
  const row = npc?.id && S.library.find(n => n.id === npc.id);
  if (!row || !row.commercial || row.commercial === 'none') return null;
  const label = (catalogue()?.statuses || []).find(s => s.id === row.commercial)?.short || row.commercial;
  return h('button', { type: 'button', class: `badge licence licence-btn ${CLASS[row.commercial] || ''}`,
    title: 'Which models made this character and what each is licensed for. Click for the report.',
    onclick: safe(() => openLicences({ npc })) }, `${MARK[row.commercial] || ''} ${label}`.trim());
}

function modelCard(m, focus) {
  const byId = id => catalogue().models.find(x => x.id === id);
  const links = [...(m.links || []).map(l => [l.label, l.url]), ...(m.licence_url ? [['Licence text', m.licence_url]] : [])];
  return h('div', { class: 'licence-model' + (m.id === focus ? ' focus' : ''), id: `licence-${m.id}` },
    h('div', { class: 'licence-head' }, h('strong', {}, m.name), statusBadge(m.effective),
      m.role === 'helper' ? h('span', { class: 'badge', title: 'It only measures or checks: nothing it makes is part of a character' }, 'helper') : null),
    h('div', { class: 'hint' }, `Licence: ${m.licence}`),
    h('p', {}, m.summary),
    m.effective !== m.status ? h('p', { class: 'hint' }, `On its own it is "${TAG[m.status]}", but it also depends on ${m.uses.map(id => byId(id)?.name || id).join(', ')}, so it is rated by the strictest of them.`) : null,
    h('p', { class: 'hint' }, `Used for: ${m.used_for}`),
    h('div', { class: 'row wrap' }, links.map(([label, url]) => h('a', { class: 'btn small ghost', href: url, target: '_blank', rel: 'noopener' }, label)),
      h('span', { class: 'hint' }, `Checked ${m.checked}`)));
}

function librarySummary() {
  if (!S.library.length || !S.library.some(npc => npc.commercial)) return null;
  const statuses = catalogue().statuses;
  const parts = [...statuses.map(s => [s, countOf(s.id)]), [{ id: 'none', short: 'nothing made yet' }, countOf('none')]].filter(([, n]) => n);
  return h('div', { class: 'licence-library' },
    h('h3', {}, 'Your library'),
    h('p', {}, `Of ${S.library.length} characters, rated by the worst model that made anything of them:`),
    h('div', { class: 'row wrap' }, parts.map(([s, n]) => h('span', { class: 'licence-count' }, s.id === 'none' ? h('span', { class: 'badge' }, `${n} nothing made yet`) : statusBadge(s.id, `${n} ${s.short.toLowerCase()}`)))),
    h('p', { class: 'hint' }, 'The Library tab can show only one kind: use the "Commercial use" filter there. A character made with a model that is not cleared or is non-commercial is easy to remake with the Apache-2.0 engines (Z-Image Turbo or Qwen-Image 2512 to draw, Qwen-Image-Edit 2511 to edit) from the Models and options page.'));
}

function reportSection(report, name) {
  const statuses = catalogue().statuses;
  return h('div', { class: 'licence-report' },
    h('h3', {}, name),
    h('div', { class: 'row wrap' }, statusBadge(report.verdict, statuses.find(s => s.id === report.verdict)?.label || report.label),
      h('span', { class: 'hint' }, 'rated by the worst model that made anything of this character')),
    h('table', { class: 'licence-table' },
      h('thead', {}, h('tr', {}, ['Model', 'Commercial use', 'Made'].map(t => h('th', {}, t)))),
      h('tbody', {}, report.models.map(m => h('tr', {},
        h('td', {}, h('a', { href: `#licence-${m.id}`, onclick: e => { e.preventDefault(); $(`#licence-${m.id}`)?.scrollIntoView({ block: 'center' }); } }, m.name),
          m.role === 'helper' ? h('span', { class: 'hint' }, ' (helper)') : null, h('div', { class: 'hint' }, m.licence)),
        h('td', {}, statusBadge(m.effective), h('div', { class: 'hint' }, m.short)),
        h('td', {}, m.used_for.join(', ')))))),
    report.unknown.length ? h('div', { class: 'notice' }, h('ul', {}, report.unknown.map(u => h('li', {}, u)))) : null,
    report.models.length || report.unknown.length ? null : h('p', { class: 'hint' }, 'Nothing has been made for this character yet.'));
}

function paint(dlg, { focus, npc, report }) {
  const c = catalogue();
  dlg.replaceChildren(h('div', { class: 'about-body' },
    h('div', { class: 'about-head' },
      h('div', { class: 'grow' }, h('h2', { id: 'licencesTitle' }, 'Licences of every model'),
        h('p', { class: 'hint' }, `What each model's own page or licence file says, read on ${c.checked}.`)),
      btn('Close', '', () => dlg.close(), { cls: 'small ghost' })),
    h('p', { class: 'hint' }, c.disclaimer),
    report ? reportSection(report, npc ? (npc.profile ? `${npc.profile.first_name} ${npc.profile.last_name}`.trim() : npc.name) : '') : null,
    h('div', { class: 'licence-legend' }, c.statuses.map(s => h('div', { class: 'row' }, statusBadge(s.id, s.label), h('span', { class: 'hint' }, s.meaning)))),
    report ? null : librarySummary(),
    c.kinds.map(kind => {
      const rows = c.models.filter(m => m.kind === kind.id);
      return rows.length ? h('section', { class: 'adv-group' }, h('h4', { class: 'lbl' }, kind.label), h('div', { class: 'stack' }, rows.map(m => modelCard(m, focus)))) : null;
    }),
    h('section', { class: 'adv-group' }, h('h4', { class: 'lbl' }, 'Also'), h('ul', { class: 'licence-notes' }, c.notes.map(n => h('li', { class: 'hint' }, n))))),
    h('div', { class: 'about-foot adv-foot' }, h('span', { class: 'grow' }), btn('Done', '', () => dlg.close(), { cls: 'small primary' })));
  const target = focus && $(`#licence-${focus}`);
  if (target) target.scrollIntoView({ block: 'center' });
}

/** The list of every model, opened at `focus` (a model's id) when given; with `npc`, first the report on that character. */
export async function openLicences({ focus, npc } = {}) {
  if (!catalogue()) return;
  let dlg = $('#licences');
  if (!dlg) {
    dlg = h('dialog', { id: 'licences', class: 'about advanced licences', 'aria-labelledby': 'licencesTitle',
      onclick: e => { if (e.target === e.currentTarget) e.currentTarget.close(); } });
    document.body.append(dlg);
  }
  const report = npc?.id ? await api('GET', `/api/npcs/${npc.id}/licences`) : null;
  paint(dlg, { focus, npc, report });
  if (!dlg.open) dlg.showModal();
  const target = focus && $(`#licence-${focus}`);
  if (target) target.scrollIntoView({ block: 'center' });
}
