import { btn, h } from './core.js';

export const timeLabel = seconds => `${Math.floor(seconds / 60)}:${(seconds % 60).toFixed(1).padStart(4, '0')}`;

export function timelineView({ timeline, frame, selected, seek, reorder }) {
  if (!timeline) return h('p', { class: 'hint' }, 'Update the preview to build the timeline.');
  const fps = timeline.fps;
  return h('section', { class: 'scene-timeline stack', 'aria-label': 'Scene timeline' },
    h('div', { class: 'row' }, h('h3', {}, 'Timeline'),
      h('output', { id: 'sceneTime', class: 'hint' }, `${timeLabel(frame / fps)} / ${timeLabel(timeline.frames / fps)}`)),
    h('input', { id: 'sceneScrub', type: 'range', min: 0, max: Math.max(0, timeline.frames - 1), step: 1,
      value: frame, 'aria-label': 'Scene playhead', oninput: event => seek(Number(event.target.value)) }),
    h('div', { class: 'scene-timeline-beats', role: 'list', 'aria-label': 'Scene beats' }, timeline.beats.map((beat, index) =>
      h('button', { type: 'button', class: 'scene-timeline-beat' + (selected === beat.id ? ' active' : ''),
        'data-beat': beat.id, 'aria-label': `Timeline beat ${index + 1}: ${beat.kind}`,
        title: `Beat ${index + 1} · ${beat.kind} · ${timeLabel(beat.start / fps)}–${timeLabel(beat.end / fps)}${beat.text ? '\n' + beat.text : ''}`,
        style: { flexGrow: String(beat.frames) }, draggable: true, onclick: () => seek(beat.start, beat.id),
        ondragstart: event => { event.dataTransfer.setData('text/plain', beat.id); },
        ondragover: event => event.preventDefault(),
        ondrop: event => { event.preventDefault(); reorder(event.dataTransfer.getData('text/plain'), beat.id); } },
      `${index + 1} · ${beat.kind}`, h('small', {}, `${(beat.frames / fps).toFixed(1)}s`)))),
    h('p', { class: 'hint' }, 'Scrub to preview a moment. Select a beat to edit it; drag beats to reorder, or use the script’s up/down buttons. Speech length follows its audio; pause and reaction lengths are editable.'));
}

export function readinessView({ timeline, mp4, dirty, actions }) {
  const ready = timeline?.readiness;
  if (!ready) return null;
  const rows = [...ready.checks, ...(!mp4 ? [{ code: 'encoder', level: 'blocker', message: 'MP4 export requires the installed FFmpeg encoder.' }] : [])];
  const blocked = !mp4 || ready.export_blocked;
  const labels = { voice: 'Voice this line', background: 'Generate setting', character: 'Open character', cast: 'Choose cast', review: 'Review clip', export: 'Render updated video' };
  return h('section', { class: 'scene-readiness stack', 'aria-label': 'Scene readiness' }, h('h3', {}, 'Scene readiness'),
    h('p', { role: 'status' }, `${ready.speech_ready}/${ready.speech_total} lines voiced · ${blocked ? 'Export needs attention' : rows.some(row => row.level === 'warning') ? 'Export available with warnings' : 'Export available'}`),
    dirty ? h('p', { class: 'hint' }, 'Save scene changes before generating assets or exporting.') : null,
    rows.length ? h('ul', {}, rows.map(row => h('li', { class: row.level },
      h('span', {}, row.message), actions[row.action] ? btn(labels[row.action], '', () => actions[row.action](row),
        { cls: 'small ghost', disabled: dirty && !['review', 'cast'].includes(row.action) }) : null)))
      : h('p', { class: 'hint' }, 'No asset or timing warnings. Listen and watch before approving the scene.'));
}
