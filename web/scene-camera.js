// Pure camera geometry, applied after character blocking in both preview and export.
export const CAMERA_SHOTS = [['wide', 'Wide'], ['medium', 'Medium'], ['close', 'Close-up'], ['shoulder', 'Over the shoulder']];

export function cameraPose(positions, speaker, shot = 'wide', side = 'left', aspect = 16 / 9) {
  if (shot === 'wide' || !positions[speaker]) return { position: [0, 1.65, aspect < 1 ? 11 : 7.2], target: [0, 1, 0], fov: 34 };
  const [x, z] = positions[speaker];
  if (shot === 'shoulder') {
    const other = Object.keys(positions).find(id => id !== speaker);
    if (other) {
      const [ox, oz] = positions[other], dx = x - ox, dz = z - oz, length = Math.hypot(dx, dz);
      if (length > .3) {
        const ux = dx / length, uz = dz / length, sign = side === 'right' ? -1 : 1;
        return { position: [ox - ux * .65 - uz * .45 * sign, 1.55, oz - uz * .65 + ux * .45 * sign],
          target: [x, 1.3, z], fov: 45 };
      }
    }
    shot = 'medium';
  }
  return { position: [x + (side === 'right' ? -.25 : .25), shot === 'close' ? 1.5 : 1.45,
    z + (shot === 'close' ? 1.25 : 2) * (aspect < 1 ? 1.2 : 1)],
    target: [x, shot === 'close' ? 1.45 : 1.3, z], fov: 34 };
}

export function directedPose(scene, timeline, frame, blocking) {
  const state = blocking(scene, timeline, frame);
  const beat = state.beat || {};
  const subject = beat.camera_target || beat.speaker_id || scene.cast[0];
  const aspect = timeline.size[0] / timeline.size[1];
  const positions = beat.camera_move === 'static' ? blocking(scene, timeline, beat.start || 0).positions : state.positions;
  const pose = cameraPose(positions, subject, beat.camera_shot || 'wide', beat.camera_side || 'left', aspect);
  const amount = beat.camera_strength ?? .3;
  const progress = Math.max(0, Math.min(1, (frame - (beat.start || 0)) / Math.max(1, (beat.frames || 1) - 1)));
  if (beat.camera_move === 'push') pose.position = pose.position.map((v, i) => pose.target[i] + (v - pose.target[i]) * (1 - amount * .55 * progress));
  if (['pan_left', 'pan_right'].includes(beat.camera_move)) {
    const shift = (beat.camera_move === 'pan_left' ? -1 : 1) * amount * 2 * progress;
    pose.position[0] += shift; pose.target[0] += shift;
  }
  if (beat.camera_move === 'track') {
    const previous = blocking(scene, timeline, Math.max(beat.start || 0, frame - amount * timeline.fps));
    const prior = cameraPose(previous.positions, subject, beat.camera_shot || 'wide', beat.camera_side || 'left', aspect);
    pose.position = prior.position;
  }
  return pose;
}

export function applyCamera(stage, scene, timeline, frame, blocking) {
  let pose = directedPose(scene, timeline, frame, blocking);
  const beat = blocking(scene, timeline, frame).beat;
  const duration = (beat?.camera_transition || 0) * timeline.fps;
  if (duration && beat.start > 0 && frame - beat.start < duration) {
    const previous = directedPose(scene, timeline, beat.start - 1, blocking);
    const t = Math.min(1, (frame - beat.start) / duration), ease = t * t * (3 - 2 * t);
    const lerp = (a, b) => a + (b - a) * ease;
    pose = {position: pose.position.map((v, i) => lerp(previous.position[i], v)),
      target: pose.target.map((v, i) => lerp(previous.target[i], v)), fov: lerp(previous.fov, pose.fov)};
  }
  stage.camera.position.set(...pose.position);
  stage.camera.lookAt(...pose.target);
  stage.camera.fov = pose.fov;
  stage.camera.updateProjectionMatrix();
  return pose;
}
