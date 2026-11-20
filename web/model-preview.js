import { assetUrl, btn, fullName, h, toast } from './core.js';

const VIEWER_BASE = '/vendor/model-viewer/';
let rendererPromise;

export function loadLocalRenderer() {
  if (!rendererPromise) {
    rendererPromise = import(`${VIEWER_BASE}model-viewer.min.js`).then(({ ModelViewerElement }) => {
      if (!ModelViewerElement || !customElements.get('model-viewer')) {
        throw new Error('The 3D viewer did not start in this browser.');
      }
      // Keep optional GLB decoders on the NPC Forge server too. Models and decoder code never go to a hosted viewer.
      ModelViewerElement.dracoDecoderLocation = `${VIEWER_BASE}draco/`;
      ModelViewerElement.ktx2TranscoderLocation = `${VIEWER_BASE}basis/`;
      ModelViewerElement.meshoptDecoderLocation = `${VIEWER_BASE}meshopt_decoder.js`;   // (model-viewer adds it as a classic <script>: no `export` in it)
      ModelViewerElement.lottieLoaderLocation = `${VIEWER_BASE}lottie-loader.js`;
      return ModelViewerElement;
    }).catch(error => {
      rendererPromise = null;
      throw error;
    });
  }
  return rendererPromise;
}

function buildAnimationControls(model, status) {
  let playing = false;
  const choices = h('select', { class: 'model3d-animation', 'aria-label': 'Animation clip', disabled: true },
    h('option', { value: '' }, 'Loading animations…'));
  const play = btn('Play', null, () => {
    if (playing) {
      model.pause();
      playing = false;
      play.textContent = 'Play';
      return;
    }
    model.animationName = choices.value || model.availableAnimations?.[0] || '';
    model.currentTime = 0;
    model.play();
    playing = true;
    play.textContent = 'Pause';
  }, { cls: 'small', disabled: true, title: 'Play or pause the selected animation' });

  choices.addEventListener('change', () => {
    model.pause();
    playing = false;
    model.animationName = choices.value;
    model.currentTime = 0;
    play.textContent = 'Play';
  });
  model.addEventListener('load', () => {
    const clips = model.availableAnimations || [];
    choices.replaceChildren(...(clips.length
      ? clips.map(name => h('option', { value: name }, name))
      : [h('option', { value: '' }, 'No animations in this model')]));
    choices.disabled = !clips.length;
    play.disabled = !clips.length;
    if (clips.length) model.animationName = clips[0];
    status.textContent = clips.length
      ? `${clips.length} animation${clips.length === 1 ? '' : 's'} available. Drag to orbit; scroll to zoom.`
      : 'Drag to orbit; scroll to zoom. This model has no embedded animation clips.';
  });
  model.addEventListener('progress', event => {
    const progress = event.detail?.totalProgress;
    if (model.loaded) return;                     // a last progress event can arrive after 'load' and hide the clip count
    if (Number.isFinite(progress)) status.textContent = progress < 1
      ? `Loading model… ${Math.round(progress * 100)}%`
      : 'Preparing interactive preview…';
  });
  model.addEventListener('error', () => {
    status.classList.add('model3d-viewer-error');
    status.textContent = 'This GLB could not be rendered in the current browser or Remote Desktop graphics session. The saved model and image preview are unchanged.';
  });
  return h('div', { class: 'row wrap model3d-viewer-tools' }, choices, play);
}

export function modelPreviewButton(npc, meta) {
  return btn('Open 3D preview', null, async event => {
    const button = event.currentTarget;
    // The card can hold two models (the plain one and the rigged one); each button owns the viewer right after its row.
    const next = button.closest('.model3d-actions')?.nextElementSibling;
    const current = next?.classList.contains('model3d-interactive') ? next : null;
    if (current) {
      const model = current.querySelector('model-viewer');
      if (model) {
        model.pause();
        model.removeAttribute('src');
      }
      current.remove();
      button.textContent = 'Open 3D preview';
      return;
    }

    button.disabled = true;
    button.textContent = 'Loading viewer…';
    try {
      await loadLocalRenderer();
      if (!button.isConnected) return;

      const status = h('p', { class: 'hint model3d-viewer-status', role: 'status', 'aria-live': 'polite' }, 'Loading model…');
      const model = document.createElement('model-viewer');
      model.setAttribute('class', 'model3d-canvas');
      model.setAttribute('camera-controls', '');
      model.setAttribute('touch-action', 'pan-y');
      model.setAttribute('shadow-intensity', '0.8');
      model.setAttribute('exposure', '1');
      model.setAttribute('loading', 'eager');
      model.setAttribute('alt', `Interactive 3D model of ${fullName(npc)}`);
      if (meta.preview) model.setAttribute('poster', assetUrl(meta.preview, meta.created));

      const panel = h('div', { class: 'model3d-interactive' }, model, buildAnimationControls(model, status), status);
      const actions = button.closest('.model3d-actions');
      if (!actions) throw new Error('The 3D preview controls are no longer available.');
      actions.after(panel);
      button.textContent = 'Close 3D preview';
      button.disabled = false;
      model.setAttribute('src', assetUrl(meta.file, meta.created));
    } catch (error) {
      button.disabled = false;
      button.textContent = 'Open 3D preview';
      toast(`Could not start the local 3D viewer: ${error.message || String(error)}`, 'error');
    }
  }, { cls: 'btn small primary', title: 'Open a local interactive GLB preview' });
}
