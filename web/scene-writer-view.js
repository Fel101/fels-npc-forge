import {h, btn, safe} from './core.js';

export function writerView({scene, field, select, suggest, proposal, apply}) {
  let objective = '', location = scene.background_prompt || '', tone = 'calm', template = 'negotiation';
  return h('details', {class: 'scene-writer'}, h('summary', {}, 'Scene-writing assistant'),
    h('p', {class: 'hint'}, 'Local profile-based drafts use the selected cast’s demeanor and personal goals. No extra model is required. Review the proposal before applying it.'),
    field('Scene objective', h('textarea', {rows: 2, maxlength: 500, 'aria-label': 'Scene objective', placeholder: 'Persuade the guard to let the travelers cross the gate', oninput: e => { objective = e.target.value; }})),
    field('Scene location', h('input', {value: location, maxlength: 300, 'aria-label': 'Scene location', oninput: e => { location = e.target.value; }})),
    h('div', {class: 'scene-form-grid'}, field('Story template', select([['negotiation', 'Negotiation'], ['confrontation', 'Confrontation'], ['discovery', 'Discovery'], ['farewell', 'Farewell']], template, value => { template = value; })),
      field('Scene tone', select([['calm', 'Calm'], ['tense', 'Tense'], ['hopeful', 'Hopeful'], ['playful', 'Playful']], tone, value => { tone = value; }))),
    btn('Suggest scene', 'eye', safe(() => suggest({objective, location, tone, template}))),
    proposal && h('div', {class: 'scene-writer-proposal stack'}, h('p', {class: 'hint'}, proposal.review_note),
      proposal.notes.map(note => h('p', {}, h('strong', {}, note.name + ': '), [note.demeanor, note.speech, note.goal].filter(Boolean).join(' · '))),
      proposal.beats.map((beat, index) => h('p', {}, `${index + 1}. ${proposal.notes.find(row => row.npc_id === beat.speaker_id)?.name || beat.speaker_id}: ${beat.text}`)),
      h('div', {class: 'row wrap'}, btn('Replace script with draft', null, safe(() => apply(false))),
        btn('Append draft', null, safe(() => apply(true)), {disabled: scene.beats.length + proposal.beats.length > 20}))));
}
