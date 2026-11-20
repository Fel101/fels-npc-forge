import * as THREE from './vendor/three/three.module.js';

// Sample the animation's bindings directly. Resetting a bone behind AnimationMixer's
// cached PropertyMixer values makes repeated frames lose their animation pose.
export class ActorPose {
  constructor(actor) {
    this.root = actor.mixer?.getRoot() || actor.mover;
    this.rest = []; this.clips = new Map();
    actor.mover.traverse(node => {
      if (node === actor.mover) return;
      this.rest.push({node, position: node.position.clone(), quaternion: node.quaternion.clone(), scale: node.scale.clone(),
        morphs: node.morphTargetInfluences?.slice()});
    });
  }
  reset() {
    for (const row of this.rest) {
      row.node.position.copy(row.position); row.node.quaternion.copy(row.quaternion); row.node.scale.copy(row.scale);
      if (row.morphs) row.node.morphTargetInfluences.splice(0, row.morphs.length, ...row.morphs);
    }
  }
  sample(clip, seconds) {
    if (!clip) return;
    if (!this.clips.has(clip)) this.clips.set(clip, clip.tracks.map(track => ({
      binding: THREE.PropertyBinding.create(this.root, track.name), interpolation: track.createInterpolant()})));
    for (const row of this.clips.get(clip)) row.binding.setValue(row.interpolation.evaluate(seconds), 0);
  }
  blendFrom(base, amount) {
    for (let i = 0; i < this.rest.length; i++) {
      const node = this.rest[i].node, previous = base[i];
      node.position.lerpVectors(previous.position, node.position, amount);
      node.quaternion.slerp(previous.quaternion, 1 - amount);
      node.scale.lerpVectors(previous.scale, node.scale, amount);
      if (previous.morphs) for (let j = 0; j < previous.morphs.length; j++) node.morphTargetInfluences[j] = previous.morphs[j] + (node.morphTargetInfluences[j] - previous.morphs[j]) * amount;
    }
  }
  snapshot() { return this.rest.map(({node}) => ({position: node.position.clone(), quaternion: node.quaternion.clone(), scale: node.scale.clone(), morphs: node.morphTargetInfluences?.slice()})); }
  dispose() { for (const rows of this.clips.values()) for (const {binding} of rows) binding.unbind(); this.clips.clear(); }
}
