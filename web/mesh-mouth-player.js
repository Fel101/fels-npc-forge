/** Apply mouth controls around Stage3D's existing animation without changing that file. */
import { mouthWeightsAt } from './character-rig-player.js';

export function softMouthWeightsAt(lip, mouth, seconds) {
  if (seconds < 0 || seconds >= (lip?.seconds || 0)) return {sil: 1};
  const result = {};
  for (const [offset, weight] of [[-.08, .1], [-.04, .2], [0, .4], [.02, .2], [.04, .1]]) {
    for (const [shape, amount] of Object.entries(mouthWeightsAt(lip, mouth, seconds + offset))) result[shape] = (result[shape] || 0) + amount * weight;
  }
  return result;
}

export class MeshMouthPlayer {
  constructor() { this.previous = new Map(); }
  prepare(actor) {
    const old = this.previous.get(actor);
    if (old?.bone) old.bone.rotation[old.axis] -= old.delta;
    for (const [mesh, index] of old?.morphs || []) mesh.morphTargetInfluences[index] = 0;
    this.previous.delete(actor);
  }
  apply(actor, rig, weights, legacyAmount = 0) {
    if (!actor || rig?.status !== 'ready') return;
    const config = rig.config;
    // Remove only Stage3D's mouth increment; the mixer animation remains underneath.
    const legacyJaw = actor.bones.jaw || actor.bones.jaw_01;
    if (legacyJaw) legacyJaw.rotation.x -= legacyAmount * .28;
    else if (actor.bones.head) actor.bones.head.rotation.x -= legacyAmount * .045;
    const amounts = {};
    let jaw = 0;
    for (const [group, weight] of Object.entries(weights)) {
      const target = group === 'mouth_open' ? config.amplitude_target : config.mapping[group];
      if (target) amounts[target] = Math.min(1, (amounts[target] || 0) + weight);
      jaw += weight * (group === 'mouth_open' ? 1 : config.jaw_amounts[group] || 0);
    }
    const applied = [];
    actor.mover.traverse(mesh => {
      if (!mesh.morphTargetDictionary || !mesh.morphTargetInfluences) return;
      for (const target of new Set([...Object.values(config.mapping), config.amplitude_target].filter(Boolean))) {
        const index = mesh.morphTargetDictionary[target];
        if (index == null) continue;
        mesh.morphTargetInfluences[index] = amounts[target] || 0; applied.push([mesh, index]);
      }
    });
    const bone = actor.bones[config.jaw_bone.toLowerCase()];
    const delta = Math.min(1, jaw) * config.jaw_radians * config.jaw_sign;
    if (bone) bone.rotation[config.jaw_axis] += delta;
    this.previous.set(actor, { bone, axis: config.jaw_axis, delta, morphs: applied });
  }
  clear() { for (const actor of this.previous.keys()) this.prepare(actor); }
}
