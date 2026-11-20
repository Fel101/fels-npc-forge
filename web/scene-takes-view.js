import { h, btn, safe } from './core.js';

export function takesView({scene, beat, current, dirty, choose, generate, pause}) {
  const group = scene.voice_takes?.[beat.id], clips = [];
  return h('details', {class: 'scene-takes'}, h('summary', {}, `Voice takes · ${group?.takes?.length || 0}`),
    h('p', {class: 'hint'}, 'Generate alternatives, compare them, then select or approve one. Approval is your listening judgment. An approved take stays selected while new alternatives finish.'),
    btn('Generate 3 takes', 'zap', safe(() => generate(beat.id)), {disabled: dirty || !scene.id, cls: 'small'}),
    (group?.takes || []).map((take, index) => {
      const meta = take.meta, outdated = meta.text !== beat.text || meta.mood !== (beat.mood || 'neutral') || meta.speaker_id !== beat.speaker_id;
      const check = current?.find(row => row.id === take.id), stale = outdated || check?.current === false;
      const clip = h('audio', {controls: true, preload: 'none', 'aria-label': `Voice take ${index + 1}`,
        src: `/api/scenes/${scene.id}/files/${meta.file}`, onplay: () => { pause(); for (const other of clips) if (other !== clip) other.pause(); }});
      clips.push(clip);
      return h('div', {class: 'scene-take-row stack'},
        h('strong', {}, `Take ${index + 1}${group.selected_id === take.id ? ' · selected' : ''}${group.approved_id === take.id && !stale ? ' · approved' : ''}${stale ? ' · outdated' : ''}`), clip,
        stale && h('p', {class: 'hint'}, check?.reason || 'This take uses an earlier script.'),
        h('p', {class: 'hint'}, `${meta.engine || 'Saved voice'} · ${(meta.seconds || 0).toFixed(1)}s · ${meta.created || ''}`),
        h('div', {class: 'row wrap'}, btn('Use take', null, safe(() => choose(beat.id, take.id, false)), {disabled: dirty || stale, cls: 'small ghost'}),
          btn('Approve take', null, safe(() => choose(beat.id, take.id, true)), {disabled: dirty || stale, cls: 'small ghost'})));
    }));
}
