import { visemeWeightsAt } from './viseme-track.js';

export function mouthWeightsAt(lip, mouth, seconds) {
  if (seconds < 0 || seconds >= (lip?.seconds || 0)) return { sil: 1 };
  if (lip.status === 'aligned' && lip.visemes) return visemeWeightsAt(lip.visemes, seconds);
  const value = Math.min(1, Math.max(0, Number(mouth?.[Math.floor(seconds * (lip.mouth_fps || 25))]) || 0));
  return { sil: 1 - value, mouth_open: value };
}

export function rigFrameWeights(rig, weights) {
  const result = {};
  for (const [group, value] of Object.entries(weights)) {
    const name = group === 'mouth_open' ? rig.amplitude_open : rig.mapping[group] || rig.mapping.sil;
    result[name] = (result[name] || 0) + value;
  }
  if (rig.interpolation === 'dominant') {
    const name = Object.keys(result).reduce((a, b) => (result[b] || 0) > (result[a] || 0) ? b : a, rig.mapping.sil);
    return { [name]: 1 };
  }
  return result;
}

export function createRigPainter(canvas, rig) {
  const context = canvas.getContext('2d'), images = new Map();
  canvas.width = rig.source.size[0]; canvas.height = rig.source.size[1];
  let dead = false, latest = { sil: 1 };
  for (const [name, frame] of Object.entries(rig.frames)) {
    const image = new Image();
    image.onload = () => { if (!dead) draw(latest); };
    image.onerror = () => { if (!dead) canvas.setAttribute('aria-label', 'Character mouth frame failed to load'); };
    images.set(name, image); image.src = frame.url;
  }
  function draw(weights) {
    if (dead) return;
    latest = weights;
    const values = rigFrameWeights(rig, weights);
    context.clearRect(0, 0, canvas.width, canvas.height);
    context.globalCompositeOperation = 'lighter';
    for (const [name, value] of Object.entries(values)) {
      const image = images.get(name);
      if (value > 0 && image?.complete && image.naturalWidth) {
        context.globalAlpha = value; context.drawImage(image, 0, 0, canvas.width, canvas.height);
      }
    }
    context.globalAlpha = 1; context.globalCompositeOperation = 'source-over';
  }
  return { draw, dispose: () => { dead = true; for (const image of images.values()) image.onload = image.onerror = null; images.clear(); } };
}
