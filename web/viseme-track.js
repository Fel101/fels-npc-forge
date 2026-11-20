/** Hold the last stored sample at its exact timestamp, matching Unreal Constant curves. */
export function visemeWeightsAt(track, seconds) {
  const frames = track?.frames;
  if (!frames?.length || seconds < 0) return { sil: 1 };
  let low = 0, high = frames.length;
  while (low < high) {
    const middle = Math.floor((low + high) / 2);
    if (frames[middle].time <= seconds) low = middle + 1;
    else high = middle;
  }
  return frames[Math.max(0, low - 1)].weights;
}
