import { S, api, btn, h, safe, toast } from './core.js';
import { loadLocalRenderer } from './model-preview.js';

// Try fitted clothes on a character: pick a character whose 3D model is a designed body, and the server fits the garment to that body (the Body Designer's own fit, about
// half a minute, in a process of its own: npcgen/dressing.py) and keeps the result with the item. The viewer then shows the character wearing it, in any of its animations;
// a colour or cloth saved afterwards is put on the kept file at once (no new fit), while a new looseness or length asks for one. Same shape as tryon-view.js:
// dressPanel(item, {onItem}) -> {element, update(item), destroy()}.

const stamp = value => encodeURIComponent(value || '');
const put = (parent, ...children) => parent.replaceChildren(...children.filter(Boolean));
const WEARS = { ForgeShirt: 'shirt', ForgeTrousers: 'trousers', ForgeSkirt: 'skirt', ForgeDress: 'dress', ForgeJacket: 'jacket', ForgeApron: 'apron' };
const FIRST = 'Idle';

export function dressPanel(first, { onItem }) {
  let item = first;
  let characters = [];
  let npc = null;                          // the open character's record (its name and model)
  let status = null;                       // the fit's status from the server
  let pollTimer = null, gone = false, loadSeq = 0;
  let viewer = null, shownUrl = '', clip = FIRST, orbit = null;

  const slot = h('div', { class: 'g-dress-slot' }, h('p', { class: 'hint' }, 'Choose a character.'));
  const note = h('p', { class: 'hint g-dress-note', 'aria-live': 'polite' }, '');
  const picker = h('select', { 'aria-label': 'Character', onchange: event => safe(choose)(event.target.value) });
  const line = h('p', { class: 'hint', 'aria-live': 'polite' }, '');
  const actions = h('div', { class: 'row wrap' });
  const report = h('div', { class: 'g-tryon-report', 'aria-live': 'polite' });
  const clips = h('select', { class: 'model3d-animation', 'aria-label': 'Animation clip', disabled: true, onchange: event => { clip = event.target.value; playClip(); } });
  const pause = btn('Pause', null, () => togglePlay(), { cls: 'small', disabled: true });
  const saveRow = h('div', { class: 'row wrap model3d-actions' });
  const noun = () => (['shirt', 'trousers', 'skirt', 'dress', 'jacket', 'apron'].includes(item?.kind) ? item.kind : 'garment');

  const element = h('div', { class: 'card g-tryon g-dress' },
    h('h3', {}, 'Try it on a character'),
    h('p', { class: 'hint' }, 'Pick a character whose 3D model is a designed body (press Use this body on the character’s Designed body card). The Body Designer fits this garment to that body, in place of the one of the same ' +
      'kind it was made with: it takes about half a minute (about a minute for a character with face shapes, whose body is built again) and is kept with this item, so it is there next time; the character itself is not changed. The download is the character wearing it, and the garment follows every animation. ' +
      'A colour or cloth you save is shown at once; a new looseness or length needs a new fit. A jacket or an apron goes over the shirt and trousers the body was made with, takes off the other one of the two, and needs a body that wears both; a skirt takes the trousers off, and a dress takes the shirt and the trousers off. ' +
      '(Save your changes to see them here.)'),
    h('div', { class: 'g-tryon-grid' },
      h('div', { class: 'g-tryon-view g-dress-view' }, slot,
        h('div', { class: 'row g-dress-bar' }, clips, pause), note),
      h('div', { class: 'stack' },
        h('div', {}, h('label', { class: 'lbl' }, 'Character'), picker),
        line, actions, report, saveRow)));

  // ---- the characters ------------------------------------------------------------------------------------------------------------------
  async function start() {
    try { characters = (await api('GET', '/api/npcs')).filter(card => card.has.designed).sort((a, b) => a.name.localeCompare(b.name)); }
    catch (error) { note.textContent = error.message; return; }
    if (!characters.length) {
      picker.replaceChildren(h('option', { value: '' }, 'No character has a designed body yet'));
      picker.disabled = true;
      line.textContent = 'Open the character’s Designed body card and press Use this body: only a body made by the Body Designer can wear fitted clothes.';
      return;
    }
    picker.replaceChildren(h('option', { value: '' }, 'Choose a character…'), ...characters.map(card => h('option', { value: card.id }, card.name)));
    draw();
  }

  async function choose(id) {
    clearTimeout(pollTimer);
    const seq = ++loadSeq;
    status = null; npc = null;
    put(slot, h('p', { class: 'hint' }, id ? 'Loading…' : 'Choose a character.'));
    shownUrl = ''; viewer = null;
    clips.replaceChildren(); clips.disabled = true; pause.disabled = true;
    note.textContent = '';
    draw();
    if (!id) return;
    try {
      [npc, status] = await Promise.all([api('GET', `/api/npcs/${id}`), api('GET', `/api/items/${item.id}/dress/${id}`)]);
    } catch (error) { if (seq === loadSeq) { line.textContent = error.message; line.classList.add('err'); } return; }
    if (seq !== loadSeq || gone) return;
    draw();
    await show();
    if (working()) poll();
  }

  const working = () => status?.state === 'queued' || status?.state === 'running';

  // ---- what is shown -----------------------------------------------------------------------------------------------------------------------------
  /** The worn file when one is kept, else the character as it is (so it is clear who is being dressed). */
  function wantedUrl() {
    if (!npc || !status) return '';
    if (status.fit) return `/api/items/${item.id}/worn/${npc.id}.glb?v=${stamp(`${status.fit.created}|${item.recipe?.colour}|${item.recipe?.texture}`)}`;
    const model = npc.assets?.rig_3d?.file ? npc.assets.rig_3d : npc.assets?.model_3d;
    return model?.file ? `/api/npcs/${npc.id}/files/${model.file}?v=${stamp(model.created)}` : '';
  }

  async function show() {
    const url = wantedUrl();
    if (!url || url === shownUrl || gone) return;
    try { await loadLocalRenderer(); } catch (error) { note.textContent = error.message; return; }
    if (gone) return;
    if (!viewer) {
      viewer = document.createElement('model-viewer');
      for (const [key, value] of Object.entries({ class: 'model3d-canvas g-canvas', 'camera-controls': '', 'touch-action': 'pan-y', 'shadow-intensity': '0.8', exposure: '1.1',
        'environment-image': 'neutral', 'camera-orbit': '25deg 80deg auto', 'interaction-prompt': 'none', loading: 'eager', alt: 'The character in 3D. Drag to turn, scroll to zoom.' })) viewer.setAttribute(key, value);
      viewer.addEventListener('load', onLoad);
      put(slot, viewer);
    } else if (viewer.loaded) {
      orbit = viewer.getCameraOrbit();                         // (keep the angle the person turned it to when the colour changes)
    }
    shownUrl = url;
    note.textContent = status.fit ? 'Loading the character wearing it…' : 'Loading the character…';
    viewer.setAttribute('src', url);
  }

  function onLoad() {
    note.textContent = status?.fit ? 'Drag to turn, scroll to zoom.' : 'This is the character as it is. Press Put it on to see it wearing the garment.';
    if (orbit) { viewer.cameraOrbit = `${orbit.theta}rad ${orbit.phi}rad ${orbit.radius}m`; viewer.jumpCameraToGoal?.(); orbit = null; }
    const names = [...(viewer.availableAnimations || [])].sort((a, b) => (a === FIRST ? -1 : b === FIRST ? 1 : a.localeCompare(b)));
    clips.replaceChildren(...(names.length ? names.map(name => h('option', { value: name }, name)) : [h('option', { value: '' }, 'No animations in this model')]));
    clips.disabled = pause.disabled = !names.length;
    if (names.length) {
      if (!names.includes(clip)) clip = names[0];
      clips.value = clip;
      playClip();
    }
  }

  function playClip() {
    if (!viewer || !clip) return;
    viewer.animationName = clip;
    viewer.currentTime = 0;
    viewer.play();
    pause.textContent = 'Pause';
  }

  function togglePlay() {
    if (!viewer) return;
    if (viewer.paused) { viewer.play(); pause.textContent = 'Pause'; } else { viewer.pause(); pause.textContent = 'Play'; }
  }

  // ---- the fit ---------------------------------------------------------------------------------------------------------------------------------------
  function say(...lines) {
    put(report, ...lines.filter(Boolean).map(([text, kind]) => h('p', { class: `hint${kind ? ` ${kind}` : ''}` }, text)));
  }

  const sizes = recipe => (recipe ? `looseness ${recipe.ease_mm} mm, length ×${recipe.length}` : '');

  function draw() {
    line.classList.remove('err');
    if (!npc || !status) { put(actions); say(); put(saveRow); line.textContent = characters.length ? '' : line.textContent; return; }
    const fit = status.fit;
    line.textContent = working() ? `${status.message || 'Working'}…` : status.state === 'error' ? status.message : !status.eligible ? status.why
      : fit ? `${npc.profile?.first_name || 'The character'} wears it${status.stale ? ' (an earlier fit)' : ''}.` : 'It has not been fitted to this body yet.';
    line.classList.toggle('err', status.state === 'error' || !status.eligible);
    put(actions,
      working() ? btn('Stop', 'x', safe(stop), { cls: 'small' })
        : btn(fit ? 'Fit it again' : 'Put it on', 'zap', safe(() => fitNow(!!fit)), { cls: fit && !status.stale ? 'small' : 'primary small', disabled: !status.eligible,
          title: 'The Body Designer fits the garment to this body (about half a minute; about a minute for a body with face shapes or swinging hair or cloth)' }));
    const wore = fit?.replaced?.length ? fit.replaced.map(name => WEARS[name] || name.replace(/^Forge/, '').toLowerCase()).join(' and ') : '';
    const unswung = Object.keys(fit?.swinging_left_off || {});
    say(fit && [`${status.stale ? 'The fit that is kept' : `The ${fit.kind} ${fit.kind === 'trousers' ? 'fit' : 'fits'}`}: ${(fit.triangles || 0).toLocaleString()} triangles` + (fit.over_underwear_mm != null ? `, ${fit.over_underwear_mm} mm off the underwear where it is worn over it` : '')
        + `, fitted in ${fit.seconds} s (${sizes(fit.recipe)}).`],
      wore && [`It is worn in place of the ${wore} this body was made with.`],
      unswung.length && [`In this try-on the body has no swinging ${unswung.join(' or ')}: ${Object.values(fit.swinging_left_off).join('; ')}. The character itself keeps it.`],
      fit && status.stale && [`This fit is for ${sizes(fit.recipe)}, not for the saved garment (${sizes(item.recipe)}) or for this body as it is now: fit it again.`, 'warn'],
      fit && S.cfg?.items?.clothing?.kinds?.[fit.kind]?.layer_notes && [S.cfg.items.clothing.kinds[fit.kind].layer_notes],
      fit && status.recoloured && !status.stale && ['The colour and cloth are the saved ones: they go onto the fit that is kept, so no new fit is needed.']);
    put(saveRow,
      fit ? h('a', { class: 'btn small primary', href: `/api/items/${item.id}/worn/${npc.id}.glb?v=${stamp(fit.created)}`, download: `${npc.id}-${item.id}.glb`,
        title: 'The character’s animated model with the garment added to it' }, 'Download the character wearing it') : null,
      fit ? h('span', { class: 'hint' }, `Fitted ${new Date(fit.created).toLocaleString()}`) : null);
  }

  async function fitNow(force) {
    status = await api('POST', `/api/items/${item.id}/dress`, { npc: npc.id, force });
    draw();
    poll();
  }

  async function stop() {
    status = await api('DELETE', `/api/items/${item.id}/dress/${npc.id}`);
    draw();
  }

  function poll() {
    clearTimeout(pollTimer);
    const seq = loadSeq;
    pollTimer = setTimeout(async () => {
      if (gone || seq !== loadSeq || !npc) return;
      try { status = await api('GET', `/api/items/${item.id}/dress/${npc.id}`); } catch { poll(); return; }
      draw();
      if (working()) poll();
      else { await show(); if (status.state === 'done') toast(`The ${noun()} is fitted.`); }
    }, 1000);
  }

  start().catch(() => {});
  return {
    element,
    /** The item was saved again: what is kept is judged again (a new colour shows at once, a new looseness or length makes the fit out of date). */
    update(next) {
      item = next;
      if (!npc || gone) return;
      const seq = loadSeq;
      api('GET', `/api/items/${item.id}/dress/${npc.id}`).then(async fresh => {
        if (seq !== loadSeq || gone) return;
        status = fresh;
        draw();
        await show();
      }).catch(() => {});
    },
    destroy() { gone = true; clearTimeout(pollTimer); viewer?.remove(); viewer = null; },
  };
}
