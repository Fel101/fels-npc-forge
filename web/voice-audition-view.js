import { btn, h } from './core.js';

const namesByNpc = new Map();
const listeningDrafts = new Map();
const judgments = [['identity', 'Voice identity'], ['pronunciation', 'Words & pronunciation'], ['delivery', 'Delivery']];

export function auditionSection({ npc, jobs, busy, changed, canRun, run, review, player, act, modelLabel = id => id }) {
  const suites = npc.voice?.auditions || [];
  const names = h('input', { id: 'voiceAuditionNames', type: 'text', maxLength: 160, value: namesByNpc.get(npc.id) || '',
    'aria-label': 'Audition names to test', placeholder: 'Character name, places or difficult words',
    oninput: event => namesByNpc.set(npc.id, event.target.value) });
  return h('section', { class: 'card stack', 'aria-label': 'Character audition suite' },
    h('h3', {}, 'Character audition suite'),
    h('p', { class: 'hint' }, 'Test the saved assigned voice with greetings, the same line in different moods, names and a longer speech. Unsupported moods are skipped. Word checks run on the CPU; saved lip-sync settings stay as they are.'),
    h('label', {}, 'Names & difficult words', names),
    btn('Run character audition', 'zap', act(() => run(namesByNpc.get(npc.id) || '')), { cls: 'small primary',
      disabled: busy || changed || !canRun, title: changed ? 'Save voice settings before auditioning' : !canRun ? 'Assign an English voice first' : '' }),
    changed ? h('p', { class: 'hint' }, 'Save voice settings before running this audition.') : null,
    h('p', { class: 'hint' }, 'Automatic flags help you find clips to review. Listen for voice identity, accurate words and the intended delivery; alignment success alone does not approve a voice.'),
    [...suites].reverse().map(suite => {
      const ready = suite.cases.filter(row => row.status === 'ready').length;
      const eligible = suite.cases.filter(row => row.status !== 'skipped').length;
      const job = jobs.find(row => row.id === suite.job);
      return h('details', { class: 'stack', open: true },
        h('summary', {}, `Audition ${suite.created?.replace('T', ' ')} · ${ready}/${eligible} clips ready`),
        h('p', { class: 'hint' }, `${modelLabel(suite.engine)} · voice captured at audition time`),
        suite.queue_error ? h('p', { class: 'notice' }, `Could not queue: ${suite.queue_error}`) : null,
        suite.source.reference ? h('div', {}, h('h4', {}, 'Assigned voice at audition time'), player(suite.source.reference, 'Audition reference')) : null,
        ready ? h('a', { class: 'btn small', href: `/api/npcs/${npc.id}/voice/auditions/${suite.id}/pack.zip`, download: '' }, 'Download audition & report') : null,
        h('div', { class: 'voice-comparison-grid' }, suite.cases.map(row => {
          const clip = npc.voice.tests?.[row.key];
          const step = job?.steps?.find(item => item.key === row.key);
          const status = row.status === 'pending' ? (step?.status || (job?.status === 'error' || job?.status === 'cancelled' ? job.status : 'Not generated yet')) : row.status;
          if (!clip || row.status !== 'ready') return h('section', { class: 'take stack' }, h('h4', {}, row.label),
            h('p', {}, row.text), h('span', { class: 'badge' }, status),
            h('p', { class: 'hint' }, row.reason || step?.error || step?.detail || 'Progress appears in the generation queue.'));
          const draftKey = `${npc.id}:${suite.id}:${row.id}:${row.quality.sha256}`;
          if (!listeningDrafts.has(draftKey)) listeningDrafts.set(draftKey, { ...row.review, notes: row.notes });
          const listening = listeningDrafts.get(draftKey);
          const picks = Object.fromEntries(judgments.map(([key, label]) => {
            const control = h('select', { id: `audition-${suite.id}-${row.id}-${key}`, 'aria-label': `${row.label}: ${label}`,
              onchange: event => { listening[key] = event.target.value; } },
              [['unreviewed', 'Not reviewed'], ['clear', 'Sounds right'], ['issue', 'Needs work']].map(([value, text]) => h('option', { value }, text)));
            control.value = listening[key];
            return [key, control];
          }));
          const notes = h('textarea', { id: `audition-${suite.id}-${row.id}-notes`, rows: 2, maxLength: 1000,
            value: listening.notes, 'aria-label': `${row.label}: listening notes`, oninput: event => { listening.notes = event.target.value; } });
          return h('section', { class: 'take stack' }, h('h4', {}, row.label), h('p', {}, row.text),
            player(clip, `Audition ${row.label}`),
            h('span', { class: 'hint' }, `${clip.seconds}s · ${{aligned:'Phoneme timing checked',word_aligned:'Word timing checked',amplitude_fallback:'Mouth track fallback'}[row.quality.word_check] || 'Word check unavailable'}`),
            clip.synthesis_text && clip.synthesis_text !== row.text ? h('p', { class: 'hint' }, 'Synthesized pronunciation: ' + clip.synthesis_text) : null,
            row.quality.recognized_text ? h('p', { class: 'hint' }, 'Recognized words: ' + row.quality.recognized_text) : null,
            row.stale ? h('span', { class: 'badge warn' }, 'Voice or performance changed since this audition') : null,
            row.quality.flags.length ? h('ul', {}, row.quality.flags.map(flag => h('li', {}, flag.message))) : h('p', { class: 'hint' }, 'No automatic flags. Listening review still required.'),
            judgments.map(([key, label]) => h('label', {}, label, picks[key])), notes,
            btn('Save listening review', 'save', act(async () => {
              await review(suite.id, row.id, { sha256: row.quality.sha256, ...listening });
              listeningDrafts.delete(draftKey);
            }), { cls: 'small' }),
            h('span', { class: 'badge' }, Object.values(row.review).some(value => value === 'issue') ? 'Listening review: needs work'
              : Object.values(row.review).every(value => value === 'clear') ? 'Listening review: sounds right' : 'Listening review pending'));
        })));
    }));
}
