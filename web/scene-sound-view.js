import { h, btn, safe } from './core.js';

export function soundView({scene, dirty, upload, commit, field, select}) {
  const media = Object.values(scene.media || {});
  const picker = h('select', {'aria-label': 'Sound recording'}, media.map(row => h('option', {value: row.id}, `${row.name} · ${row.seconds.toFixed(1)}s`)));
  const number = (label, value, min, max, step, change) => field(label, h('input', {type: 'number', min, max, step, value,
    oninput: e => change(Number(e.target.value)),
    'aria-label': label, onchange: safe(async e => { change(Number(e.target.value)); await commit(); })}));
  return h('details', {class: 'scene-sound'}, h('summary', {}, `Sound tracks · ${scene.soundtracks?.length || 0}`),
    h('p', {class: 'hint'}, 'Import up to 60 seconds of audio per file. Music and ambience can loop. Dialogue ducking lowers a track during actual speech, in preview and video export.'),
    field('Import sound', h('input', {type: 'file', accept: 'audio/*', 'aria-label': 'Import scene sound', disabled: dirty || !scene.id,
      onchange: safe(async event => { const file = event.target.files?.[0]; if (file) await upload(file); })})),
    dirty && h('p', {class: 'hint'}, 'Save this scene before importing sound files.'),
    h('div', {class: 'row wrap'}, picker, btn('Add sound track', 'plus', safe(async () => {
      scene.soundtracks ||= []; const source = scene.media[picker.value];
      scene.soundtracks.push({id: `track-${crypto.randomUUID().slice(0, 8)}`, media_id: source.id, kind: 'ambience', start: 0,
        offset: 0, duration: Math.min(60, source.seconds), volume: .4, loop: false, duck: true}); await commit();
    }), {disabled: !media.length || (scene.soundtracks?.length || 0) >= 12})),
    (scene.soundtracks || []).map(track => h('div', {class: 'scene-sound-row stack'},
      h('strong', {}, scene.media?.[track.media_id]?.name || 'Missing sound'),
      h('output', {class: 'hint scene-track-status', 'data-track': track.id,
        'aria-label': `${scene.media?.[track.media_id]?.name || 'Sound'} playback`}, 'Ready'),
      h('div', {class: 'scene-form-grid'}, field('Track kind', select([['ambience', 'Ambience'], ['music', 'Music'], ['effect', 'Sound effect']], track.kind,
        async value => { track.kind = value; await commit(); })),
        number('Start seconds', track.start, 0, 60, .1, v => { track.start = v; }),
        number('Recording offset', track.offset, 0, scene.media?.[track.media_id]?.seconds || 60, .1, v => { track.offset = v; }),
        number('Track duration', track.duration, .1, 60, .1, v => { track.duration = v; }),
        number('Track volume', track.volume, 0, 1, .05, v => { track.volume = v; })),
      h('div', {class: 'row wrap'}, ...[['loop', 'Loop recording'], ['duck', 'Duck during dialogue']].map(([key, title]) =>
        h('label', {class: 'row'}, h('input', {type: 'checkbox', checked: track[key], onchange: safe(async e => { track[key] = e.target.checked; await commit(); })}), title))),
      btn('Remove sound track', 'trash', safe(async () => { scene.soundtracks.splice(scene.soundtracks.indexOf(track), 1); await commit(); }), {cls: 'small ghost'}))));
}
