// The π. A small π in the lower right of NPC Forge's About box opens a terminal-looking box that types a short "access granted"
// screen, then shows the 18+ box and, once it is ticked, the adult options: the picture engine for each level, the engine for a
// Models character's main picture, the picture quality, the optional age-check model, and the way into the Adult studio.
// Ticking the box shows the Models setting and the Adult studio tab; un-ticking hides them again (nothing is deleted).
// Hiding it is for tidiness, not security: the server's own checks (the 18+ confirmation, the hard rules in safety.py) are what
// keep it off. Everything is built from DOM nodes and textContent, never from markup strings.
(function () {
  'use strict';
  const NF = window.NPCForge;
  const foot = NF && NF.aboutFooter && NF.aboutFooter();
  if (!foot) return;                                          // an NPC Forge without the About box: nothing to attach to

  const LINES = [
    '> npcforge --whoami',
    '  someone who knows what the π was for',
    '> unlock /ext/adult',
    '  checking the age on the card ....... 18+ only',
    '  loading the hard rules ............. locked in',
    '> ACCESS GRANTED',
  ];
  const SCREEN = LINES.join('\n');
  const STUDIO_URL = '/ext/adult/';

  function make(tag, props, ...kids) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(props || {})) {
      if (v === false || v == null) continue;
      if (k === 'class') el.className = v;
      else if (k === 'text') el.textContent = v;
      else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v);
      else el.setAttribute(k, v === true ? '' : v);
    }
    for (const kid of kids.flat()) if (kid != null && kid !== false) el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
    return el;
  }

  async function call(method, path, body) {
    // NPC Forge's server refuses anything but GET without a JSON content type
    const write = method !== 'GET';
    const res = await fetch('/api/ext/adult/' + path, {
      method, headers: write ? { 'Content-Type': 'application/json' } : undefined,
      body: write ? JSON.stringify(body || {}) : undefined,
    });
    let data = null;
    try { data = await res.json(); } catch { /* no body */ }
    if (!res.ok) throw new Error((data && data.error) || `Request failed (HTTP ${res.status})`);
    return data;
  }

  const screen = make('pre', { class: 'pi-screen', 'aria-hidden': 'true', title: 'Click to skip' });
  const box = make('input', { type: 'checkbox', id: 'piAdultBox' });
  const status = make('p', { class: 'pi-status', role: 'status' });
  const options = make('div', { class: 'pi-options' });
  const body = make('div', { class: 'pi-body' },
    make('h2', { id: 'piTitle', class: 'pi-title', text: 'Adult features (18+)' }),
    make('p', { text: 'The Adult studio and the Models setting are for adults. Confirm that you are 18 or older to show them. ' +
      'Characters under 18, anything that reads as a minor, real people and anything non-consensual are refused at every level.' }),
    make('label', { class: 'pi-check', for: 'piAdultBox' }, box, make('span', {}, make('strong', { text: '18+' }), ' I am 18 or older')),
    status, options,
    make('div', { class: 'pi-actions' }, make('button', { type: 'button', class: 'pi-btn', text: 'Close', onclick: () => dialog.close() })));
  const dialog = make('dialog', { class: 'pi-dialog', 'aria-labelledby': 'piTitle' }, screen, body);
  dialog.addEventListener('click', e => { if (e.target === dialog) dialog.close(); });
  dialog.addEventListener('close', stopIntro);
  screen.addEventListener('click', finishIntro);
  document.body.append(dialog);

  let timer = null;
  function stopIntro() { if (timer !== null) clearTimeout(timer); timer = null; }
  function finishIntro() { stopIntro(); screen.textContent = SCREEN; body.hidden = false; }
  function reducedMotion() { try { return matchMedia('(prefers-reduced-motion: reduce)').matches; } catch { return false; } }

  function field(label, control, hint) {
    return make('label', { class: 'pi-field' }, make('span', { class: 'pi-label', text: label }), control, hint ? make('span', { class: 'pi-hint', text: hint }) : null);
  }

  function engineSelect(choices, current, onchange) {
    const sel = make('select', { onchange: e => onchange(e.target.value) },
      choices.map(c => make('option', { value: c.id, text: c.label + (c.missing && c.missing.length ? ' (files missing in ComfyUI)' : ''), selected: c.id === current })));
    return sel;
  }

  async function save(changes) {
    try { render(await call('PUT', 'settings', changes)); status.textContent = 'Saved.'; }
    catch (e) { status.textContent = 'Not saved: ' + e.message; }
  }

  function render(state) {
    box.checked = !!state.enabled;
    status.textContent = state.enabled ? 'On. The Models setting and the Adult studio tab are showing.' : 'Off. The adult features are hidden.';
    options.replaceChildren();
    if (!state.enabled) return;
    const eng = state.engines || {};
    const levels = (state.levels || []).map(lv => field(`Pictures: ${lv.label}`,
      engineSelect(eng.choices || [], (eng.levels || {})[lv.id], v => save({ engines: { [lv.id]: v } }))));
    options.append(
      make('h3', { class: 'pi-sub', text: 'Options' }),
      ...levels,
      field('Main picture of a Models character', engineSelect(eng.portrait_choices || [], (eng.levels || {}).portrait, v => save({ engines: { portrait: v } })),
        'Only portraits drawn from now on use it.'),
      field('Quality', make('select', { onchange: e => save({ quality: e.target.value }) },
        (state.qualities || []).map(q => make('option', { value: q, text: q[0].toUpperCase() + q.slice(1), selected: q === state.quality })))),
      field('Age-check model (optional)', make('input', { type: 'text', value: state.age_check_model || '', placeholder: 'a vision model in LM Studio',
        onchange: e => save({ age_check_model: e.target.value }) }), 'Blank: pictures are marked unchecked here. Altera checks every picture again when it imports one.'),
      make('div', { class: 'pi-actions start' }, make('button', { type: 'button', class: 'pi-btn primary', text: 'Open the Adult studio', onclick: () => { location.href = STUDIO_URL; } })));
  }

  async function refresh() {
    try { render(await call('GET', 'state')); } catch (e) { box.checked = false; options.replaceChildren(); status.textContent = 'Could not check: ' + e.message; }
  }

  function open() {
    const about = document.getElementById('about');
    if (about && about.open) about.close();
    status.textContent = '';
    refresh();
    dialog.showModal();
    stopIntro();
    if (reducedMotion()) return finishIntro();
    body.hidden = true;
    let shown = 0;
    const step = () => {
      shown += 1;
      screen.textContent = SCREEN.slice(0, shown);
      if (shown >= SCREEN.length) return finishIntro();
      timer = setTimeout(step, SCREEN[shown - 1] === '\n' ? 260 : 16);
    };
    step();
  }

  box.addEventListener('change', async () => {
    const want = box.checked;
    box.disabled = true;
    try {
      render(want ? await call('POST', 'confirm', { confirm_adult: true }) : await call('DELETE', 'confirm'));
      await NF.reload();                                      // NPC Forge lists the tab and the setting again (or stops listing them)
    } catch (e) {
      box.checked = !want;                                    // it did not work: put the box back
      status.textContent = e.message;
    }
    box.disabled = false;
  });

  foot.append(make('button', { type: 'button', class: 'about-pi', 'aria-label': 'π', text: 'π', onclick: open }));
})();
