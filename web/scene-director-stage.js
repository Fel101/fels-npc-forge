// Extend the shared stage without changing Body Designer's stage3d.js integration.
import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { Stage3D } from './stage3d.js';
import { blockingAt, gestureOffset, smooth } from './scene-blocking.js';
import { ActorPose } from './scene-pose.js';
export { blockingAt } from './scene-blocking.js';

export class DirectorStage extends Stage3D {
  constructor(...args) { super(...args); this.props = []; this.poses = new Map(); this.appliesLegacyMouth = false; }
  async load(scene, npcs, background, modelUrl, props = []) {
    await super.load(scene, npcs, background, modelUrl);
    for (const [id, actor] of Object.entries(this.actors)) {
      actor.walkSpeed = npcs[id]?.assets?.rig_3d?.clip_info?.find(row => row.name.toLowerCase() === 'walk')?.speed_m_s || 1.25;
    }
    const loader = new GLTFLoader();
    for (const prop of props) {
      try {
        const gltf = await loader.loadAsync(prop.url);
        if (this.disposed) return;
        const mover = new THREE.Group(); mover.add(gltf.scene);
        mover.position.set(...prop.position); mover.rotation.y = prop.rotation * Math.PI / 180; mover.scale.setScalar(prop.scale);
        this.scene.add(mover); this.props.push(mover);
      } catch (error) { this.status.textContent = `Prop could not load: ${error.message}`; }
    }
  }
  frame(scene, timeline, frame) {
    if (this.disposed) return;
    const state = blockingAt(scene, timeline, frame);
    for (const id of scene.cast) {
      const actor = this.actors[id]; if (!actor) continue;
      if (!this.poses.has(actor)) this.poses.set(actor, new ActorPose(actor));
      const pose = this.poses.get(actor); pose.reset();
      const seconds = frame / timeline.fps, relative = Math.max(0, frame - state.beat.start) / timeline.fps;
      const duration = state.beat.frames / timeline.fps;
      const idle = actor.clips.idle;
      if (idle) pose.sample(idle, seconds % Math.max(.001, idle.duration));
      const wanted = state.actions[id].toLowerCase(), clip = actor.clips[wanted];
      if (clip && wanted !== 'idle') {
        const idlePose = pose.snapshot();
        if (wanted === 'walk') {
          // Advance feet by distance travelled, rather than running a full-speed walk while sliding a short mark.
          pose.sample(clip, (state.walked / Math.max(.1, actor.walkSpeed || 1.25)) % Math.max(.001, clip.duration));
          pose.blendFrom(idlePose, Math.min(smooth(relative / .25), smooth((1 - state.walkPart) / .15)));
        } else {
          // Wave is a single clip, beginning at the beat, with a soft return to idle.
          pose.sample(clip, Math.min(relative, clip.duration));
          pose.blendFrom(idlePose, Math.min(smooth(relative / .25), smooth((Math.min(duration, clip.duration) - relative) / .3)));
        }
      }
      const [x, z] = state.positions[id]; actor.mover.position.set(x, 0, z);
      actor.mover.rotation.y = state.rotations[id] * Math.PI / 180;
      const direction = state.acting[id], head = Object.entries(actor.bones).find(([name]) => /(^|[_:])head$/.test(name))?.[1];
      if (head) {
        const targetId = direction.look_at === 'speaker' ? state.beat.speaker_id : direction.look_at;
        if (targetId && targetId !== id) {
          const target = targetId === 'camera' ? [this.camera.position.x, this.camera.position.z] : state.positions[targetId];
          if (target) {
            let angle = Math.atan2(target[0] - x, target[1] - z) - actor.mover.rotation.y;
            angle = Math.atan2(Math.sin(angle), Math.cos(angle));
            const gaze = Math.min(smooth(relative / .55), smooth((duration - relative) / .35));
            head.rotation.y += Math.max(-.35, Math.min(.35, angle)) * gaze;
          }
        }
        const pulse = gestureOffset(direction.gesture, relative, duration);
        if (direction.gesture === 'nod') head.rotation.x += pulse;
        if (direction.gesture === 'shake') head.rotation.y += pulse;
      }
      if (direction.gesture === 'point') {
        const arm = Object.entries(actor.bones).find(([name]) => /upperarm.*r$|upperarm_r$|rightarm$/.test(name))?.[1];
        if (arm) arm.rotation.z -= .35 * Math.min(smooth(relative / .35), 1 - smooth((relative - .9) / .4));
      }
      actor.mover.traverse(mesh => {
        if (!mesh.morphTargetDictionary || !mesh.morphTargetInfluences) return;
        const selected = ({happy: /smile|happy/i, laughing: /smile|happy/i, sad: /sad|frown/i, angry: /angry/i,
          surprised: /surprise|brow.*up/i, thinking: /thinking/i})[direction.expression];
        for (const [name, index] of Object.entries(mesh.morphTargetDictionary)) {
          if (/smile|happy|sad|frown|angry|surprise|brow.*up|thinking/i.test(name)) mesh.morphTargetInfluences[index] = selected?.test(name) ? .65 : 0;
        }
      });
    }
    this.renderer.render(this.scene, this.camera);
  }
  dispose() {
    for (const pose of this.poses.values()) pose.dispose(); this.poses.clear();
    for (const prop of this.props) prop.traverse(item => {
      item.geometry?.dispose();
      const materials = Array.isArray(item.material) ? item.material : [item.material];
      for (const material of materials) { if (!material) continue; for (const value of Object.values(material)) if (value?.isTexture) value.dispose(); material.dispose(); }
    });
    this.props = []; super.dispose();
  }
}
