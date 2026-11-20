/** Read real GLB bone keyframes without loading textures or changing the model. */
import * as THREE from '../web/vendor/three/three.module.js';
import {readFileSync, writeFileSync} from 'node:fs';
import assert from 'node:assert/strict';
import {ActorPose} from '../web/scene-pose.js';
import {blockingAt, gestureOffset, smooth} from '../web/scene-blocking.js';

const file = process.argv[2];
if (!file) throw Error('Usage: node scripts/check_scene_motion_real.mjs model.glb [report.json]');
const bytes = readFileSync(file), jsonLength = bytes.readUInt32LE(12);
const doc = JSON.parse(bytes.subarray(20, 20 + jsonLength).toString());
const binary = bytes.subarray(28 + jsonLength);
const jointIds = new Set(doc.skins.flatMap(skin => skin.joints));
const nodes = doc.nodes.map((row, index) => {
  const node = jointIds.has(index) ? new THREE.Bone() : new THREE.Group();
  node.name = row.name || `node_${index}`;
  if (row.translation) node.position.fromArray(row.translation);
  if (row.rotation) node.quaternion.fromArray(row.rotation);
  if (row.scale) node.scale.fromArray(row.scale);
  if (row.matrix) new THREE.Matrix4().fromArray(row.matrix).decompose(node.position, node.quaternion, node.scale);
  return node;
});
for (let i = 0; i < nodes.length; i++) for (const child of doc.nodes[i].children || []) nodes[i].add(nodes[child]);
const root = new THREE.Group(); for (const node of nodes) if (!node.parent) root.add(node);
function floats(id) {
  const accessor = doc.accessors[id], view = doc.bufferViews[accessor.bufferView];
  assert.equal(accessor.componentType, 5126);
  const size = {SCALAR: 1, VEC3: 3, VEC4: 4}[accessor.type], values = new Float32Array(accessor.count * size);
  assert.ok(size);
  for (let i = 0; i < accessor.count; i++) for (let j = 0; j < size; j++) values[i * size + j] = binary.readFloatLE((view.byteOffset || 0) + (accessor.byteOffset || 0) + i * (view.byteStride || size * 4) + j * 4);
  return values;
}
const clips = {};
for (const animation of doc.animations || []) {
  const tracks = [];
  for (const channel of animation.channels) {
    const sampler = animation.samplers[channel.sampler], target = nodes[channel.target.node];
    const property = {rotation: 'quaternion', translation: 'position', scale: 'scale'}[channel.target.path];
    if (!property) continue;
    assert.ok(['LINEAR', 'STEP', undefined].includes(sampler.interpolation));
    const Track = property === 'quaternion' ? THREE.QuaternionKeyframeTrack : THREE.VectorKeyframeTrack;
    tracks.push(new Track(`${target.uuid}.${property}`, floats(sampler.input), floats(sampler.output), sampler.interpolation === 'STEP' ? THREE.InterpolateDiscrete : THREE.InterpolateLinear));
  }
  clips[animation.name.toLowerCase()] = new THREE.AnimationClip(animation.name, -1, tracks);
}
assert.ok(clips.idle && clips.idle.tracks.length);
class BaseStage { constructor() { this.scene = new THREE.Scene(); this.camera = new THREE.PerspectiveCamera(); this.actors = {}; this.renderer = {render() {}}; } }
const source = readFileSync(new URL('../web/scene-director-stage.js', import.meta.url), 'utf8').replace(/^import .*;\r?\n/gm, '').replace(/^export \{.*;\r?\n/gm, '').replace('export class', 'class');
const Director = new Function('THREE', 'Stage3D', 'blockingAt', 'ActorPose', 'gestureOffset', 'smooth', source + '\nreturn DirectorStage;')(THREE, BaseStage, blockingAt, ActorPose, gestureOffset, smooth);
const stage = new Director(), bones = Object.fromEntries(nodes.filter(node => node.isBone).map(node => [node.name.toLowerCase(), node]));
stage.actors.a = {mover: root, bones, clips, mixer: new THREE.AnimationMixer(root)};
const scene = {cast: ['a', 'b']}, timeline = {fps: 25, beats: [{id: 'idle', kind: 'line', speaker_id: 'a', start: 0, end: 125, frames: 125}]};
const snapshot = () => Object.values(bones).map(bone => ({q: bone.quaternion.clone(), p: bone.position.clone()}));
const expected = new Map();
for (const frame of [0, 1, 25, 26, 50, 75, 100, 124]) { stage.frame(scene, timeline, frame); expected.set(frame, snapshot()); }
for (const frame of [25, 25, 26, 0, 124, 50, 75, 1, 100, 26]) {
  stage.frame(scene, timeline, frame); const current = snapshot();
  current.forEach((row, i) => { assert.ok(row.q.clone().normalize().angleTo(expected.get(frame)[i].q.clone().normalize()) < 2e-6, `Pose changed at frame ${frame}, joint ${Object.keys(bones)[i]}`); assert.ok(row.p.distanceTo(expected.get(frame)[i].p) < 1e-7); });
}
let previous, maximumHeadStep = 0;
for (let frame = 0; frame < 125; frame++) {
  stage.frame(scene, timeline, frame);
  if (previous && bones.head) maximumHeadStep = Math.max(maximumHeadStep, bones.head.quaternion.clone().normalize().angleTo(previous));
  previous = bones.head?.quaternion.clone().normalize();
}
assert.ok(maximumHeadStep < .4, 'Idle head has an abrupt discontinuity');
const report = {status: 'passed', file, joints: Object.keys(bones).length, clips: Object.keys(clips), repeated_frames_and_random_seek: 'passed', idle_max_head_step_degrees: maximumHeadStep * 180 / Math.PI};
if (process.argv[3]) writeFileSync(process.argv[3], JSON.stringify(report, null, 2));
console.log(JSON.stringify(report));
