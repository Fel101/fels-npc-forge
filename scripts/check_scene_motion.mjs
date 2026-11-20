import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import * as THREE from '../web/vendor/three/three.module.js';
import {ActorPose} from '../web/scene-pose.js';
import {blockingAt, gestureOffset, smooth} from '../web/scene-blocking.js';
import {MeshMouthPlayer, softMouthWeightsAt} from '../web/mesh-mouth-player.js';

class BaseStage { constructor() { this.scene = new THREE.Scene(); this.camera = new THREE.PerspectiveCamera(); this.actors = {}; this.renderer = {render() {}}; } }
const source = readFileSync(new URL('../web/scene-director-stage.js', import.meta.url), 'utf8')
  .replace(/^import .*;\r?\n/gm, '').replace(/^export \{.*;\r?\n/gm, '').replace('export class', 'class');
const Director = new Function('THREE', 'Stage3D', 'blockingAt', 'ActorPose', 'gestureOffset', 'smooth', source + '\nreturn DirectorStage;')(THREE, BaseStage, blockingAt, ActorPose, gestureOffset, smooth);
const scene = {cast: ['a', 'b'], actors: {a: {position: [-1, 0], rotation: 30}, b: {position: [1, 0], rotation: -30}}};
const timeline = {fps: 25, beats: [{id: 'one', kind: 'line', speaker_id: 'a', start: 0, end: 100, frames: 100, acting: {b: {facing: 'speaker', look_at: 'speaker', gesture: 'nod'}}},
  {id: 'two', kind: 'line', speaker_id: 'b', start: 100, end: 200, frames: 100, acting: {a: {facing: 'speaker', look_at: 'speaker'}}}]};
function actor() {
  const mover = new THREE.Group(), head = new THREE.Bone(), jaw = new THREE.Bone(); head.name = 'head'; jaw.name = 'jaw'; head.add(jaw); mover.add(head);
  const q = new THREE.Quaternion().setFromEuler(new THREE.Euler(.3, 0, 0));
  const clip = new THREE.AnimationClip('Idle', 2, [new THREE.QuaternionKeyframeTrack('head.quaternion', [0, 2], [...q.toArray(), ...q.toArray()])]);
  return {mover, bones: {head, jaw}, clips: {idle: clip}, mixer: new THREE.AnimationMixer(mover)};
}
test('real held animation survives repeated frames and arbitrary seek order', () => {
  const stage = new Director(); stage.actors.a = actor();
  for (const frame of [25, 25, 26, 190, 0, 25]) {
    stage.frame(scene, timeline, frame);
    assert.ok(Math.abs(stage.actors.a.bones.head.rotation.x - .3) < 1e-6);
  }
});
test('body facing is continuous at speaker changes and turns at no more than 90 degrees/second', () => {
  let previous = blockingAt(scene, timeline, 0);
  for (let frame = 1; frame < 200; frame++) {
    const current = blockingAt(scene, timeline, frame);
    for (const id of scene.cast) assert.ok(Math.abs(current.rotations[id] - previous.rotations[id]) <= 90 / 25 + .001);
    previous = current;
  }
  assert.ok(Math.abs(blockingAt(scene, timeline, 100).rotations.b - blockingAt(scene, timeline, 99).rotations.b) < .1);
});
test('nod/shake settle after one restrained gesture instead of looping through a long beat', () => {
  for (const gesture of ['nod', 'shake']) {
    assert.notEqual(gestureOffset(gesture, .3, 5), 0);
    for (const seconds of [0, 1, 2, 3, 5]) assert.equal(gestureOffset(gesture, seconds, 5), 0);
  }
});
test('wave starts at its beat, finishes once and returns to idle', () => {
  const stage = new Director(), a = actor(); stage.actors.a = a;
  a.clips.wave = new THREE.AnimationClip('Wave', 1, [new THREE.NumberKeyframeTrack('jaw.rotation[x]', [0, .5, 1], [0, .4, 0])]);
  const tl = {fps: 25, beats: [{id: 'wave', kind: 'reaction', speaker_id: 'a', start: 0, end: 100, frames: 100, motion: 'wave'}]};
  stage.frame(scene, tl, 12); assert.ok(a.bones.jaw.rotation.x > .1);
  for (const frame of [26, 50, 75, 99]) { stage.frame(scene, tl, frame); assert.ok(Math.abs(a.bones.jaw.rotation.x) < 1e-6); }
});
test('walk phase follows travelled distance and blends to idle at the destination', () => {
  const stage = new Director(), a = actor(); stage.actors.a = a; a.walkSpeed = 1;
  a.clips.walk = new THREE.AnimationClip('Walk', 2, [new THREE.NumberKeyframeTrack('jaw.rotation[x]', [0, 2], [0, 1])]);
  const walk = distance => ({fps: 25, beats: [{id: 'walk', kind: 'line', speaker_id: 'a', start: 0, end: 100, frames: 100, motion: 'walk', walk_to: [-1 + distance, 0]}]});
  stage.frame(scene, walk(1), 10); const short = a.bones.jaw.rotation.x;
  stage.frame(scene, walk(2), 10); assert.ok(Math.abs(a.bones.jaw.rotation.x - short * 2) < 1e-6);
  for (const frame of [80, 99]) {
    stage.frame(scene, walk(2), frame);
    assert.ok(Math.abs(a.bones.jaw.rotation.x) < 1e-6);
    assert.equal(a.mover.position.x, 1);
  }
});

test('jaw overlay restores the sampled pose, does not accumulate and closes outside speech', () => {
  const stage = new Director(), a = actor(), mouth = new MeshMouthPlayer(); stage.actors.a = a;
  const rig = {status:'ready',config:{mapping:{sil:'',aa:''},jaw_bone:'jaw',jaw_axis:'x',jaw_radians:.08,jaw_sign:1,jaw_amounts:{sil:0,aa:1}}};
  for (const frame of [25, 25, 26, 25]) { mouth.prepare(a); stage.frame(scene,timeline,frame); mouth.apply(a,rig,{aa:1}); assert.ok(Math.abs(a.bones.jaw.rotation.x-.08)<1e-6); }
  mouth.prepare(a); stage.frame(scene,timeline,25); mouth.apply(a,rig,{sil:1}); assert.ok(Math.abs(a.bones.jaw.rotation.x)<1e-6);
  assert.deepEqual(softMouthWeightsAt({seconds:1,mouth_fps:25},[1,0,1],1),{sil:1});
  const soft = softMouthWeightsAt({seconds:1,mouth_fps:25},[1,0,1],.04);
  assert.ok(soft.mouth_open>0 && soft.mouth_open<1);
});
