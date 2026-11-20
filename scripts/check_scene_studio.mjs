import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import * as THREE from '../web/vendor/three/three.module.js';
import {blockingAt, gestureOffset, smooth} from '../web/scene-blocking.js';
import {ActorPose} from '../web/scene-pose.js';
import {directedPose, applyCamera} from '../web/scene-camera.js';
import {duckGain, SceneSoundPlayer} from '../web/scene-sound.js';

const scene = {cast: ['a', 'b', 'c'], actors: {a: {position: [-1, 0], rotation: 0}, b: {position: [1, 0], rotation: 0}, c: {position: [0, -1], rotation: 0}}};
const beats = [{id: 'one', kind: 'line', speaker_id: 'a', start: 0, end: 25, frames: 25, motion: 'walk', walk_to: [0, 0], camera_target: 'a', camera_shot: 'medium', camera_move: 'push', camera_strength: .5,
  acting: {b: {facing: 'speaker', look_at: 'speaker', gesture: 'nod', expression: 'happy'}}},
  {id: 'two', kind: 'line', speaker_id: 'b', start: 25, end: 50, frames: 25, motion: 'stand', camera_target: 'b', camera_shot: 'close', camera_transition: .4}];
const timeline = {fps: 25, size: [1280, 720], frames: 50, beats};

test('ensemble blocking and listener facing are deterministic when seeking', () => {
  assert.equal(Object.keys(blockingAt(scene, timeline, 0).positions).length, 3);
  assert.deepEqual(blockingAt(scene, timeline, 30).positions.a, [0, 0]);
  const expected = blockingAt(scene, timeline, 10); blockingAt(scene, timeline, 45);
  assert.deepEqual(blockingAt(scene, timeline, 10), expected);
  assert.ok(expected.rotations.b < 0 && expected.rotations.b > -90); assert.equal(expected.acting.b.gesture, 'nod');
  assert.equal(blockingAt(scene, timeline, 30).acting.b.gesture, 'none');
});

test('push-in and shot blending follow the same deterministic camera in export frames', () => {
  const first = directedPose(scene, timeline, 0, blockingAt), last = directedPose(scene, timeline, 24, blockingAt);
  assert.ok(last.position[2] < first.position[2]);
  const camera = new THREE.PerspectiveCamera(34, 16 / 9, .1, 100), stage = {camera};
  const boundary = applyCamera(stage, scene, timeline, 25, blockingAt);
  assert.deepEqual(boundary, last);
  const complete = applyCamera(stage, scene, timeline, 40, blockingAt);
  assert.deepEqual(complete, directedPose(scene, timeline, 40, blockingAt));
  const a = applyCamera(stage, scene, timeline, 30, blockingAt); applyCamera(stage, scene, timeline, 49, blockingAt);
  assert.deepEqual(applyCamera(stage, scene, timeline, 30, blockingAt), a);
});

test('both pan directions and tracking change the shared camera pose', () => {
  const tl = structuredClone(timeline), first = tl.beats[0]; first.camera_move = 'follow';
  const base = directedPose(scene, tl, 10, blockingAt);
  first.camera_move = 'pan_left'; assert.ok(directedPose(scene, tl, 10, blockingAt).position[0] < base.position[0]);
  first.camera_move = 'pan_right'; assert.ok(directedPose(scene, tl, 10, blockingAt).position[0] > base.position[0]);
  first.camera_move = 'track'; const tracking = directedPose(scene, tl, 10, blockingAt);
  assert.ok(tracking.position[0] < base.position[0]); assert.deepEqual(tracking.target, base.target);
  first.camera_move = 'static'; assert.deepEqual(directedPose(scene, tl, 0, blockingAt), directedPose(scene, tl, 24, blockingAt));
});

test('procedural acting does not accumulate across pause and repeated seek', () => {
  class BaseStage { constructor() { this.scene = new THREE.Scene(); this.camera = new THREE.PerspectiveCamera(); this.actors = {}; this.renderer = {render() {}}; } }
  const source = readFileSync(new URL('../web/scene-director-stage.js', import.meta.url), 'utf8')
    .replace(/^import .*;\r?\n/gm, '').replace(/^export \{.*;\r?\n/gm, '').replace('export class', 'class');
  const Director = new Function('THREE', 'Stage3D', 'blockingAt', 'ActorPose', 'gestureOffset', 'smooth', source + '\nreturn DirectorStage;')(THREE, BaseStage, blockingAt, ActorPose, gestureOffset, smooth);
  const stage = new Director(), mover = new THREE.Group(), head = new THREE.Bone(), jaw = new THREE.Bone(); head.name = 'Head'; jaw.name = 'Jaw'; head.add(jaw); mover.add(head);
  const face = new THREE.Mesh(new THREE.BufferGeometry(), new THREE.MeshBasicMaterial());
  face.morphTargetDictionary = {Smile: 0, Frown: 1, Unrelated: 2}; face.morphTargetInfluences = [0, 0, .2]; mover.add(face);
  stage.actors.b = {mover, bones: {head, jaw}, clips: {}};
  stage.frame(scene, timeline, 5); const expected = head.quaternion.clone();
  assert.equal(face.morphTargetInfluences[0], .65);
  stage.frame(scene, timeline, 30); assert.equal(face.morphTargetInfluences[0], 0); assert.equal(face.morphTargetInfluences[2], .2);
  stage.frame(scene, timeline, 20); stage.frame(scene, timeline, 5);
  assert.ok(head.quaternion.angleTo(expected) < .000001);
  assert.ok(Math.abs(mover.rotation.y - blockingAt(scene, timeline, 5).rotations.b * Math.PI / 180) < .000001);
});

test('ducking attacks before speech and releases after the actual audio, not beat padding', () => {
  const beat = {start: 25, frames: 50, voice: {seconds: .4}};
  assert.equal(duckGain(.5, [beat], 25), 1); assert.equal(duckGain(1.1, [beat], 25), .3);
  assert.ok(Math.abs(duckGain(.96, [beat], 25) - .65) < .000001);
  assert.ok(Math.abs(duckGain(1.48, [beat], 25) - .65) < .000001);
  assert.equal(duckGain(1.7, [beat], 25), 1);
});

test('sound effects schedule on the audio clock without animation frames and resume loop offsets', async () => {
  const sources = [], gains = [], ctx = {currentTime: 10, state: 'suspended', destination: {},
    async resume() { this.state = 'running'; }, async decodeAudioData() { return {duration: 1}; },
    createBufferSource() { const source = {connect() {}, start(...args) { this.started = args; }, stop(at) { this.stopped = at; }}; sources.push(source); return source; },
    createGain() { const events = [], node = {connect() {}, disconnect() {}, gain: {
      setValueAtTime(...args) { events.push(['set', ...args]); }, linearRampToValueAtTime(...args) { events.push(['ramp', ...args]); }}}; gains.push(events); return node; }};
  const player = new SceneSoundPlayer(undefined, {context: () => ctx, fetch: async () => ({ok: true, arrayBuffer: async () => new ArrayBuffer(8)})});
  const music = {id: 'music', media: {file: 'media/music.wav', seconds: 1}, offset: .3, start: 0, duration: 3, volume: .5, loop: true, duck: true};
  const effect = {id: 'effect', media: {file: 'media/effect.wav', seconds: .1}, offset: 0, start: 1.5, duration: .1, volume: .6, loop: false, duck: false};
  const tl = {fps: 25, frames: 100, beats: [{start: 25, frames: 25, voice: {seconds: .4}}], soundtracks: [music, effect]};
  player.update({id: 'scene-test'}, tl, 0, false); assert.equal(player.context, null);
  await player.prepare({id: 'scene-test'}, tl); player.update({id: 'scene-test'}, tl, 0, true);
  assert.deepEqual(sources[0].started, [10, .3]); assert.deepEqual(sources[1].started, [11.5, 0]);
  assert.ok(gains[0].some(event => event[0] === 'ramp' && event[1] === .15 && event[2] === 11));
  ctx.currentTime = 11.55; assert.equal(player.status().find(row => row.id === 'effect').playing, true);
  ctx.currentTime = 11.7; assert.equal(player.status().find(row => row.id === 'effect').playing, false);
  ctx.currentTime = 11.08; player.update({id: 'scene-test'}, tl, 27, true);
  assert.ok(Math.abs(player.status().find(row => row.id === 'music').time - .68) < .000001);
  player.pause(); assert.equal(player.status().some(row => row.playing), false);
  ctx.currentTime = 12; player.update({id: 'scene-test'}, tl, 30, true);
  assert.ok(Math.abs(sources.at(-2).started[1] - .8) < .000001);
  assert.ok(Math.abs(sources.at(-1).started[0] - 12.3) < .000001);
  player.clear(); assert.equal(player.tracks.size, 0);
});
