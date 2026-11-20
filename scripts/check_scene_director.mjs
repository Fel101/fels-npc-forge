import { readFileSync } from 'node:fs';
import assert from 'node:assert/strict';
import test from 'node:test';
import { SceneClock } from '../web/scene-clock.js';
import { CAMERA_SHOTS, applyCamera } from '../web/scene-camera.js';
import { SceneHistory, authoringCopy } from '../web/scene-history.js';
import { SceneSoundPlayer } from '../web/scene-sound.js';

class Element {
  constructor(tag, props, children) { this.tag = tag; Object.assign(this, props); this.children = children; for (const child of children) if (child instanceof Element) child.parentElement = this; this.dataset ||= {}; this.nodeType = 1; this.style ||= {}; }
  all() { return [this, ...this.children.filter(row => row instanceof Element).flatMap(row => row.all())]; }
  get textContent() { return this.children.map(row => row instanceof Element ? row.textContent : String(row)).join(''); }
  set textContent(value) { this.children = [value]; }
  replaceChildren(...children) { this.children = children; for (const child of children) if (child instanceof Element) child.parentElement = this; }
  closest(selector) { for (let node = this; node; node = node.parentElement) if (node.matches(selector)) return node; return null; }
  matches(selector) {
    const attribute = selector.match(/\[([^=]+)="([^"]+)"\]/);
    if (attribute && (this[attribute[1]] ?? this.dataset[attribute[1].replace('data-', '')]) !== attribute[2]) return false;
    const prefix = selector.replace(/\[.*\]/, '');
    return !prefix || (prefix.startsWith('#') ? this.id === prefix.slice(1) : prefix.startsWith('.') ? (this.class || '').split(' ').includes(prefix.slice(1)) : this.tag === prefix);
  }
  querySelectorAll(selector) { let nodes = [this]; for (const part of selector.split(' ')) nodes = nodes.flatMap(row => row.all().filter(node => node.matches(part))); return nodes; }
  querySelector(selector) { return this.querySelectorAll(selector)[0]; }
  setAttribute(key, value) { this[key] = value; }
  getAttribute(key) { return this[key]; }
  get classList() { return { toggle: (name, enabled) => { const names = new Set((this.class || '').split(' ')); enabled ? names.add(name) : names.delete(name); this.class = [...names].join(' '); } }; }
  scrollIntoView() {}
}
const h = (tag, props, ...children) => new Element(tag, props || {}, children.flat(Infinity).filter(value => value != null && value !== false));
const btn = (label, icon, onclick, options = {}) => h('button', { onclick, disabled: options.disabled, class: options.cls }, label);
const text = path => readFileSync(new URL(path, import.meta.url), 'utf8').replace(/^import .*?;\r?\n/gm, '').replaceAll('export ', '');

function fixture() {
  const root = h('main', { id: 'view' }), calls = [], clips = [];
  const document = { querySelector: selector => root.querySelector(selector), querySelectorAll: selector => root.querySelectorAll(selector), activeElement: null };
  const S = { view: 'scenes', library: ['a', 'b'].map(id => ({ id, name: id })) };
  let saved = { id: 'scene-aaaaaaaaaa', title: 'Test', cast: ['a', 'b'], mode: 'cards', aspect: 'landscape', backdrop: 'slate', beats: [
    { id: 'pause-one', kind: 'pause', duration: 1 }, { id: 'spoken-two', kind: 'line', speaker_id: 'a', text: 'Hello.', mood: 'neutral', layout: 'split', line_id: '' }] };
  const timeline = scene => { let cursor = 0; return { fps: 25, frames: Math.round(scene.beats.reduce((n, row) => n + (row.duration || 1), 0) * 25), size: [1280, 720],
    readiness: { checks: [], speech_ready: 1, speech_total: 1 }, beats: scene.beats.map(row => {
      const start = cursor, frames = Math.round((row.duration || 1) * 25); cursor += frames;
      return { ...row, start, end: cursor, frames, voice: row.kind === 'line' ? { source: 'scene', file: 'audio/a.wav', seconds: .8, mouth: [], lip_sync: { status: 'aligned' } } : null };
    }) }; };
  const api = async (method, path, body) => {
    calls.push({ method, path, body: body && structuredClone(body) });
    if (path === '/api/scenes/capabilities') return { director_schema: 1, mp4: true };
    if (path === '/api/voice/engines') return { engines: [{ id: 'qwen', caps: { direction: false } }] };
    if (path.startsWith('/api/npcs/')) return { profile: { first_name: path.split('/').at(-1) }, voice: {}, assets: {} };
    if (path.endsWith('/timeline')) return timeline(saved);
    if (path === '/api/scenes/preview') return timeline(body);
    if (method === 'PUT') saved = structuredClone(body);
    return structuredClone(saved);
  };
  class Audio {
    constructor(src) { this.src = src; this.duration = .8; this.readyState = 4; this.currentTime = 0; this.paused = true; this.ended = false; clips.push(this); }
    play() { this.paused = false; return Promise.resolve(); }
    pause() { this.paused = true; }
    addEventListener() {}
  }
  let now = 0, raf;
  const helpers = new Function('h', 'btn', text('../web/scene-director-view.js') + '\nreturn {timelineView,readinessView,timeLabel};')(h, btn);
  const build = new Function('$', 'S', 'api', 'btn', 'h', 'safe', 'toast', 'MeshMouthPlayer', 'createRigPainter', 'mouthWeightsAt',
    'SceneClock', 'CAMERA_SHOTS', 'applyCamera', 'timelineView', 'readinessView', 'timeLabel', 'document', 'performance', 'Audio', 'requestAnimationFrame', 'cancelAnimationFrame', 'SceneHistory', 'authoringCopy', 'SceneSoundPlayer',
    text('../web/scenes-view.js') + '\nreturn createSceneController;');
  let controller;
  controller = build(selector => document.querySelector(selector), S, api, btn, h, fn => fn, () => {}, class { clear() {} }, () => {}, () => {},
    SceneClock, CAMERA_SHOTS, applyCamera, helpers.timelineView, helpers.readinessView, helpers.timeLabel,
    document, { now: () => now }, Audio, callback => { raf = callback; return 1; }, () => { raf = null; }, SceneHistory, authoringCopy, SceneSoundPlayer)({ render: () => controller.renderScenes(), afterSubmit() {} });
  const click = async label => { const node = root.all().find(row => row.tag === 'button' && row.textContent === label); assert.ok(node, label); assert.ok(!node.disabled, label); await node.onclick(); };
  return { controller, root, calls, clips, click, tick: value => { now = value; const callback = raf; raf = null; callback?.(value); }, get saved() { return saved; } };
}

test('pause edit is captured from input, updates timeline and saves only on explicit save', async () => {
  const f = fixture(); await f.controller.load('scene-aaaaaaaaaa');
  const seconds = f.root.all().find(row => row.tag === 'input' && row.type === 'number');
  seconds.oninput({ target: { value: '1.3' } });
  await f.click('Update preview');
  assert.equal(f.root.querySelector('#sceneScrub').max, 56);
  assert.equal(f.saved.beats[0].duration, 1);
  await f.click('Save scene'); assert.equal(f.saved.beats[0].duration, 1.3);
});

test('seek, pause and resume preserve the speech offset and stop returns to frame zero', async () => {
  const f = fixture(); await f.controller.load('scene-aaaaaaaaaa');
  await f.root.all().find(row => row['aria-label'] === 'Timeline beat 2: line').onclick();
  await f.click('Resume');
  assert.equal(f.clips.at(-1).currentTime, 0);
  f.clips.at(-1).currentTime = .4; f.tick(400);
  assert.equal(f.root.querySelector('#sceneScrub').value, 35);
  await f.click('Pause'); f.tick(10000);
  assert.equal(f.root.querySelector('#sceneScrub').value, 35);
  await f.click('Resume'); assert.equal(f.clips.at(-1).currentTime, .4);
  f.clips.at(-1).currentTime = .6; f.tick(10200);
  assert.equal(f.root.querySelector('#sceneScrub').value, 40);
  await f.click('Stop'); assert.equal(f.root.querySelector('#sceneScrub').value, 0);
});

test('timeline drops reorder retained beat IDs without rewriting saved scene', async () => {
  const f = fixture(); await f.controller.load('scene-aaaaaaaaaa');
  const target = f.root.all().find(row => row['aria-label'] === 'Timeline beat 1: pause');
  await target.ondrop({ preventDefault() {}, dataTransfer: { getData: () => 'spoken-two' } });
  assert.equal(f.calls.find(row => row.path === '/api/scenes/preview').body.beats[0].id, 'spoken-two');
  assert.equal(f.saved.beats[0].id, 'pause-one');
});

test('pause samples the actual voice time even when no animation frame has arrived', async () => {
  const f = fixture(); await f.controller.load('scene-aaaaaaaaaa');
  await f.root.all().find(row => row['aria-label'] === 'Timeline beat 2: line').onclick();
  await f.click('Resume'); f.clips.at(-1).currentTime = .7;
  await f.click('Pause'); assert.equal(f.root.querySelector('#sceneScrub').value, 42);
  await f.click('Resume'); assert.equal(f.clips.at(-1).currentTime, .7);
});
