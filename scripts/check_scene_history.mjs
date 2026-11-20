import assert from 'node:assert/strict';
import test from 'node:test';
import { SceneHistory } from '../web/scene-history.js';

const storage = () => { const rows = new Map(); return {getItem: k => rows.get(k), setItem: (k, v) => rows.set(k, v), removeItem: k => rows.delete(k)}; };
const scene = () => ({id: 'scene-aaaaaaaaaa', revision: 3, title: 'Original', cast: ['a', 'b'], beats: [{id: 'first', text: 'Hello'}], audio: {first: {file: 'server.wav'}}, media: {music: {file: 'music.wav'}}});

test('undo/redo preserve newer server-owned speech and media', () => {
  const s = scene(), history = new SceneHistory(storage()); history.reset(s);
  s.title = 'Changed'; history.capture(s);
  s.audio.first.file = 'new-server.wav'; s.revision = 4;
  assert.ok(history.undo(s)); assert.equal(s.title, 'Original');
  assert.equal(s.audio.first.file, 'new-server.wav'); assert.equal(s.revision, 4);
  assert.ok(history.redo(s)); assert.equal(s.title, 'Changed'); assert.equal(s.media.music.file, 'music.wav');
  assert.ok(history.undo(s)); s.title = 'Different branch'; history.capture(s);
  assert.equal(history.redo(s), false);
});

test('drafts survive a new editor, expose revision conflicts and clear after save', () => {
  const store = storage(), s = scene(), first = new SceneHistory(store); first.reset(s);
  s.beats[0].text = 'Recover this'; first.capture(s);
  const reopened = scene(), next = new SceneHistory(store); next.reset(reopened);
  const recovery = next.recoverable(reopened); assert.equal(recovery.conflict, false);
  next.apply(reopened, recovery.value); assert.equal(reopened.beats[0].text, 'Recover this');
  assert.equal(reopened.audio.first.file, 'server.wav');
  const changedServer = scene(); changedServer.revision = 5;
  assert.equal(next.recoverable(changedServer).conflict, true);
  next.clear(reopened); assert.equal(next.recoverable(changedServer), null);
});

test('quota failure keeps undo available and reports missing recovery', () => {
  const history = new SceneHistory({setItem() { throw Error('quota'); }}), s = scene(); history.reset(s);
  s.title = 'Draft'; history.capture(s); assert.ok(history.error); assert.ok(history.undo(s));
});
