// Deterministic placement and acting; consumed by the stage, map and camera renderer.
export function initialPosition(scene, id, index = scene.cast.indexOf(id)) {
  return [...(scene.actors?.[id]?.position || (scene.cast.length === 2 ? [index ? 1.35 : -1.35, 0]
    : [-2.7 + 5.4 * index / Math.max(1, scene.cast.length - 1), 0]))];
}
export function blockingAt(scene, timeline, frame) {
  const positions = Object.fromEntries(scene.cast.map((id, i) => [id, initialPosition(scene, id, i)]));
  let beat = timeline.beats.at(-1); const directions = {};
  let rotations = Object.fromEntries(scene.cast.map(id => [id, scene.actors?.[id]?.rotation || 0]));
  let walked = 0, walkPart = 1;
  for (const row of timeline.beats) {
    if (row.start > frame) break;
    for (const [id, direction] of Object.entries(row.acting || {})) directions[id] = {...directions[id], ...direction};
    const at = Math.min(frame, row.end), relative = Math.max(0, at - row.start) / timeline.fps;
    if (row.motion === 'walk' && row.walk_to && positions[row.speaker_id]) {
      walkPart = Math.max(0, Math.min(1, (at - row.start) / Math.max(1, row.frames * .8)));
      const eased = smooth(walkPart), start = positions[row.speaker_id];
      walked = Math.hypot(row.walk_to[0] - start[0], row.walk_to[1] - start[1]) * eased;
      positions[row.speaker_id] = start.map((v, i) => v + (row.walk_to[i] - v) * eased);
    } else { walked = 0; walkPart = 1; }
    const next = {};
    for (const id of scene.cast) {
      const dir = directions[id] || {}, face = dir.facing === 'speaker' ? row.speaker_id : dir.facing;
      let wanted = rotations[id];
      if (dir.facing === 'rotation') wanted = dir.rotation || 0;
      else if (face === 'camera') wanted = 0;
      else if (positions[face] && face !== id) wanted = Math.atan2(positions[face][0] - positions[id][0], positions[face][1] - positions[id][1]) * 180 / Math.PI;
      // A speaker keeps their orientation instead of snapping back to the scene's initial rotation.
      next[id] = easedTurn(rotations[id], wanted, relative);
    }
    rotations = next;
    if (frame < row.end) { beat = row; break; }
  }
  const actions = Object.fromEntries(scene.cast.map(id => [id, 'Idle']));
  if (beat?.motion === 'walk' && positions[beat.speaker_id] && beat.walk_to) {
    if (walkPart < 1) actions[beat.speaker_id] = 'Walk';
  } else if (beat?.motion === 'wave') actions[beat.speaker_id] = 'Wave';
  const acting = {};
  for (const id of scene.cast) {
    const dir = directions[id] || {};
    acting[id] = {...dir, gesture: beat?.acting?.[id]?.gesture || 'none', expression: beat?.acting?.[id]?.expression || ''};
    if (acting[id].gesture === 'wave' && actions[id] === 'Idle') actions[id] = 'Wave';
  }
  return {beat, positions, actions, rotations, acting, walked, walkPart};
}

export const smooth = t => { t = Math.max(0, Math.min(1, t)); return t * t * (3 - 2 * t); };
export function easedTurn(from, to, seconds) {
  const difference = ((to - from + 540) % 360) - 180;
  // Smoothstep's maximum slope is 1.5: this duration bounds turning to 90 degrees/second.
  const duration = Math.max(.65, Math.abs(difference) / 60);
  return from + difference * smooth(seconds / duration);
}

export function gestureOffset(gesture, seconds, duration) {
  // One restrained nod/shake, with a neutral pose at both ends; never an oscillator for a whole line.
  const length = Math.min(.95, duration), phase = seconds / Math.max(.001, length);
  if (phase <= 0 || phase >= 1 || !['nod', 'shake'].includes(gesture)) return 0;
  return Math.sin(phase * Math.PI * 2) * Math.sin(phase * Math.PI) ** 2 * (gesture === 'nod' ? .07 : .1);
}
