// Exercise the real controller with a small DOM fixture and an in-memory API.
// No browser, model worker, or user library is touched.
import { readFileSync } from 'node:fs';
import assert from 'node:assert/strict';
import test from 'node:test';

class Element {
  constructor(tag, props = {}, children = []) { this.tag = tag; Object.assign(this, props); this.children = children; this.nodeType = 1; }
  replaceChildren(...children) { this.children = children; }
  get textContent() { return this.children.map(c => c instanceof Element ? c.textContent : String(c)).join(''); }
  set textContent(value) { this.children = [value]; }
  all() { return [this, ...this.children.filter(c => c instanceof Element).flatMap(c => c.all())]; }
  querySelector(selector) {
    const parts = selector.split(' '), matches = (node, part) => part.startsWith('#') ? node.id === part.slice(1)
      : part.startsWith('.') ? (node.class || '').split(' ').includes(part.slice(1)) : node.tag === part;
    let roots = [this];
    for (const part of parts) roots = roots.flatMap(root => root.all().filter(node => matches(node, part)));
    return roots[0] || null;
  }
}
const h = (tag, props, ...children) => new Element(tag, props, children.flat(Infinity).filter(c => c != null && c !== false));
const btn = (text, icon, onclick, opts = {}) => h('button', { onclick, disabled: opts.disabled, class: 'btn ' + (opts.cls || '') }, text);
const source = readFileSync(new URL('../web/voice-designer.js', import.meta.url), 'utf8')
  .replace(/^import .*?;\r?\n/gm, '').replace('export function createVoiceDesigner', 'function createVoiceDesigner');
const auditionSource = readFileSync(new URL('../web/voice-audition-view.js', import.meta.url), 'utf8')
  .replace(/^import .*?;\r?\n/gm, '').replaceAll('export ', '');

function fixture(connected = true) {
  const records = Object.fromEntries(['a', 'b'].map(id => [id, { id, profile: { first_name: id.toUpperCase(), last_name: 'Actor', age: 30 },
    setting: 'fantasy', style: 'render3d', assets: {}, voice: { engine: 'qwen', mode: 'design', description: 'Saved ' + id,
      language: 'English', takes: [], settings: {} }, dialogue: { lines: {} } }]));
  const S = { view: 'create', draft: { id: 'unfinished', profile: { first_name: 'Unsaved' }, dialogue: { text: 'Keep me' } },
    dirty: true, dialogueDirty: true, pendingPhoto: { file: 'photo' }, jobs: [], library: [] };
  const root = h('main', { id: 'view' });
  const $ = selector => root.querySelector(selector);
  const requests = [], errors = [], submitted = [];
  let beforeSave = null, beforeCopy = null;
  const linked = { name: 'Harbor', kind: 'cast', model: 'turbo', label: 'Chatterbox Turbo', takes_direction: true, ready: true };
  const catalog = { lab_api: 1, default: 'qwen', stock: { kokoro: [] }, presets: { voice_types: [], chip_groups: [], moods: ['neutral'], setting_accents: [] },
    engines: [{ id: 'qwen', label: 'Qwen', installed: true, commercial_ok: true, languages: ['English'], caps: { design: true, clone: true, direction: 'experimental', tags: [] } }] };
  const refreshLibrary = async () => { S.library = Object.values(records).map(n => ({ id: n.id, name: n.profile.first_name + ' Actor',
    setting: n.setting, voice: n.voice.reference ? 'picked' : 'none' })); };
  const api = async (method, path, body) => {
    requests.push({ method, path, body: body && structuredClone(body) });
    if (path === '/api/voice/engines') return structuredClone(catalog);
    if (path === '/api/voice/lab') return connected ? { connected, voices: [linked] }
      : { connected, message: 'Start Voice Lab to design and use voices in NPC Forge.' };
    const [, id, suffix = ''] = path.match(/^\/api\/npcs\/([^/]+)(.*)$/) || [];
    const record = records[id]; assert.ok(record, path);
    if (method === 'GET') return structuredClone(record);
    if (suffix === '/voice/suggest') return { description: 'Suggested ' + id, stock: [], script: 'Hello', adult: true };
    if (method === 'PUT' && suffix === '/voice') { if (beforeSave) await beforeSave(); Object.assign(record.voice, body); return structuredClone(record); }
    if (suffix === '/voice/copy') {
      if (beforeCopy) await beforeCopy();
      record.voice = { ...structuredClone(records[body.from].voice), mode: 'copy', speaker: 'qwen', review: 'picked' };
      return structuredClone(record);
    }
    if (suffix === '/voice/lab' && method === 'POST') {
      return { job: { id: 'design', npc_id: id, status: 'queued', steps: [], total: 1 } };
    }
    if (suffix === '/voice/takes') {
      Object.assign(record.voice, body.settings);
      return { job: { id: 'job', npc_id: id, status: 'queued', steps: [], total: body.count } };
    }
    if (suffix === '/voice/auditions') return { npc: structuredClone(record), job: { id: 'audition', npc_id: id, status: 'queued', steps: [], total: 7 } };
    throw new Error('Unexpected API request: ' + method + ' ' + path);
  };
  const safe = fn => async (...args) => { try { return await fn(...args); } catch (error) { errors.push(error); throw error; } };
  const factory = new Function('$', 'S', 'api', 'btn', 'fullName', 'h', 'isActive', 'safe', 'toast',
    'auditionSection', 'licenceNote', 'optionTag', 'ratingTag', 'statusBadge', source + '\nreturn createVoiceDesigner;')
    ($, S, api, btn, n => n.profile.first_name + ' Actor', h, j => ['queued', 'running'].includes(j.status), safe, () => {},
     new Function('h','btn',auditionSource+'\nreturn auditionSection;')(h,btn),
     () => null, () => '', () => '', () => null);
  let workspace;
  workspace = factory({ standalone: true, afterSubmit: result => submitted.push(result), renderGallery() {},
    saveDraft() { throw new Error('Create draft must never be saved here'); }, render: () => workspace.renderWorkspace(), refreshLibrary, openNpc() {} });
  const click = text => {
    const node = root.all().find(n => n.tag === 'button' && n.textContent === text);
    assert.ok(node, 'Missing button: ' + text); assert.ok(!node.disabled, 'Disabled button: ' + text); return node.onclick();
  };
  const edit = value => { const node = $('#voiceDesc'); node.value = value; node.oninput({ target: node }); };
  return { S, workspace, records, catalog, linked, requests, errors, submitted, $, root, click, edit,
    blockSave: fn => { beforeSave = fn; }, blockCopy: fn => { beforeCopy = fn; } };
}

test('opening Voice Designer keeps unfinished Create and dialogue edits intact', async () => {
  const f = fixture(), previous = structuredClone(f.S);
  await f.workspace.openTab('a');
  assert.deepEqual(f.S.draft, previous.draft);
  assert.equal(f.S.dirty, true); assert.equal(f.S.dialogueDirty, true); assert.deepEqual(f.S.pendingPhoto, previous.pendingPhoto);
  assert.equal(f.requests.filter(r => r.method === 'PUT').length, 0);
  assert.equal(f.$('#voiceDesc').value, 'Saved a');
  assert.ok(!f.$('#voiceEditor').children.includes(null), 'Native replaceChildren must not render null as text');
});

test('character audition submits saved assigned voice and guards unsaved settings', async () => {
  const f=fixture(); f.catalog.audition_schema=1;
  f.records.a.voice.lab_voice = f.linked;
  await f.workspace.openTab('a');
  assert.match(f.root.textContent,/Character audition suite/);
  const names=f.root.all().find(node=>node['aria-label']==='Audition names to test');
  names.oninput({target:{value:'Vael Harbor'}});
  await f.click('Run character audition');
  assert.equal(f.requests.find(row=>row.path==='/api/npcs/a/voice/auditions').body.names,'Vael Harbor');
  assert.equal(f.submitted[0].jobs[0].total,7);
  assert.equal(f.requests.filter(row=>row.method==='PUT').length,0);
  f.edit('Unsaved change'); f.workspace.renderWorkspace();
  assert.ok(f.root.all().find(node=>node.textContent==='Run character audition').disabled);
  assert.equal(f.S.draft.id,'unfinished');
});

test('audition cards keep automatic evidence separate from human judgment', async () => {
  const f=fixture(); f.catalog.audition_schema=1;
  f.records.a.voice.tests={audition_test:{file:'voice/tests/a.wav',seconds:3}};
  f.records.a.voice.auditions=[{id:'a'.repeat(12),created:'today',engine:'qwen',source:{},cases:[{
    id:'neutral',key:'audition_test',label:'Neutral',text:'Hello.',status:'ready',stale:true,
    quality:{sha256:'sha',word_check:'amplitude_fallback',flags:[{message:'Word check failed'}]},
    review:{identity:'unreviewed',pronunciation:'clear',delivery:'issue'},notes:'Listen again.'}]}];
  await f.workspace.openTab('a');
  assert.match(f.root.textContent,/Word check failed/);
  assert.match(f.root.textContent,/Listening review: needs work/);
  assert.match(f.root.textContent,/changed since this audition/);
  assert.ok(f.root.all().some(node=>node.tag==='a' && node.textContent==='Download audition & report'));
  const notes=f.root.all().find(node=>node['aria-label']==='Neutral: listening notes');
  notes.oninput({target:{value:'Keep these unfinished notes'}});
  f.workspace.renderWorkspace();
  assert.equal(f.root.all().find(node=>node['aria-label']==='Neutral: listening notes').value,'Keep these unfinished notes');
  assert.equal(f.records.a.voice.auditions[0].cases[0].notes,'Listen again.');
});

test('offline Voice Lab leaves saved audio and voice pack available', async () => {
  const f = fixture(false);
  f.records.a.voice.reference = { id: 'reference', file: 'voice/reference.wav' };
  await f.workspace.openTab('a');
  assert.match(f.root.textContent, /Start Voice Lab to design and use voices in NPC Forge/);
  assert.ok(f.root.all().some(node => node.tag === 'audio'));
  assert.ok(f.root.all().some(node => node.tag === 'a' && node.textContent === 'Download voice pack'));
  assert.ok(!f.root.all().some(node => node.textContent === 'Make this voice in Voice Lab'));
});

test('voice edits and audition text survive switching characters and tabs', async () => {
  const f = fixture(); f.records.a.voice.lab_voice = f.linked;
  await f.workspace.openTab('a'); f.edit('Gravelly captain');
  const line = f.$('#voiceTry'); line.oninput({ target: { value: 'Hold the line!' } });
  await f.click('BB ActorfantasyNo voice');
  assert.equal(f.$('#voiceDesc').value, 'Saved b');
  await f.click('AA ActorfantasyNo voice');
  assert.equal(f.$('#voiceDesc').value, 'Gravelly captain'); assert.equal(f.$('#voiceTry').value, 'Hold the line!');
  f.S.view = 'items'; await f.workspace.openTab();
  assert.equal(f.$('#voiceDesc').value, 'Gravelly captain');
});

test('saving targets the selected voice without changing the Create draft', async () => {
  const f = fixture(); await f.workspace.openTab('b'); f.edit('Velvety narrator'); await f.click('Save settings');
  assert.equal(f.records.b.voice.description, 'Velvety narrator'); assert.equal(f.records.a.voice.description, 'Saved a');
  assert.equal(f.S.draft.id, 'unfinished'); assert.equal(f.$('#voiceSettingsStatus').textContent, 'Voice settings saved');
});

test('new takes arriving from the queue preserve unsaved voice controls and correct audio URLs', async () => {
  const f = fixture(); await f.workspace.openTab('a'); f.edit('Unsaved direction');
  f.records.a.voice.takes = [{ id: 'take', n: 1, engine: 'qwen', file: 'voice/takes/take.wav', created: 'now' }];
  await f.workspace.onJobChange({ npc_id: 'a' });
  assert.equal(f.$('#voiceDesc').value, 'Unsaved direction');
  const audio = f.root.all().find(n => n.tag === 'audio'); assert.ok(audio.src.startsWith('/api/npcs/a/files/'));
  assert.ok(f.root.all().some(n => n.textContent === 'Assign to character'));
});

test('a pending save cannot be redirected by character switching or leaving the tab', async () => {
  const f = fixture(); await f.workspace.openTab('a'); f.edit('Save to A');
  let release; f.blockSave(() => new Promise(resolve => { release = resolve; }));
  const pending = f.click('Save settings');
  await Promise.resolve();
  const rosterB = f.root.all().find(n => n.tag === 'button' && n.textContent === 'BB ActorfantasyNo voice');
  assert.equal(rosterB.disabled, true);
  f.S.view = 'create'; release(); await pending;
  assert.equal(f.records.a.voice.description, 'Save to A'); assert.equal(f.records.b.voice.description, 'Saved b');
  assert.equal(f.S.draft.id, 'unfinished');
});

test('copied voice identity persists when settings are saved afterwards', async () => {
  const f = fixture(); f.records.b.voice.reference = { id: 'ref', file: 'voice/reference.wav' };
  f.records.b.voice.description = 'A copied narrator'; f.records.b.voice.settings = { temperature: 0.6 };
  await f.workspace.openTab('a'); await f.click('Copy this voice');
  await f.click('Save settings');
  assert.equal(f.records.a.voice.mode, 'copy'); assert.equal(f.records.a.voice.reference.id, 'ref');
  assert.equal(f.records.a.voice.description, 'A copied narrator'); assert.equal(f.records.a.voice.settings.temperature, 0.6);
});

test('candidate generation submits the selected character to the existing job queue', async () => {
  const f = fixture(); await f.workspace.openTab('b'); await f.click('Preview candidate voices');
  const request = f.requests.find(r => r.path === '/api/npcs/b/voice/takes'); assert.equal(request.body.count, 3);
  assert.equal(f.submitted[0].jobs[0].npc_id, 'b'); assert.equal(f.S.draft.id, 'unfinished');
  assert.equal(f.$('#voiceSettingsStatus').textContent, 'Voice settings saved');
});

test('saving a voice updates shared voice data without overwriting unfinished character or dialogue edits', async () => {
  const f = fixture(); f.S.draft.id = 'a'; const profile = structuredClone(f.S.draft.profile), dialogue = structuredClone(f.S.draft.dialogue);
  await f.workspace.openTab('a'); f.edit('A warm, low voice'); await f.click('Save settings');
  assert.equal(f.S.draft.voice.description, 'A warm, low voice'); assert.deepEqual(f.S.draft.profile, profile); assert.deepEqual(f.S.draft.dialogue, dialogue);
  assert.equal(f.S.dirty, true); assert.equal(f.S.dialogueDirty, true);
});

test('failed save restores the character picker and leaves the draft available to retry', async () => {
  const f = fixture(); await f.workspace.openTab('a'); f.edit('Keep this draft');
  f.blockSave(() => { throw new Error('offline'); });
  await assert.rejects(f.click('Save settings'), /offline/);
  assert.equal(f.$('#voiceDesc').value, 'Keep this draft');
  f.blockSave(null); await f.click('Save settings'); assert.equal(f.records.a.voice.description, 'Keep this draft');
  await f.click('BB ActorfantasyNo voice'); assert.equal(f.$('#voiceDesc').value, 'Saved b');
});

test('Voice Designer submits the edited description to Voice Lab', async () => {
  const f = fixture(); await f.workspace.openTab('a'); f.edit('A warm, steady narrator');
  await f.click('Make this voice in Voice Lab');
  const request = f.requests.find(r => r.method === 'POST' && r.path === '/api/npcs/a/voice/lab');
  assert.equal(request.body.description, 'A warm, steady narrator');
  assert.equal(f.submitted[0].jobs[0].npc_id, 'a');
});
