import { api, btn, h, toast } from './core.js';

export async function openCharacterCard(npc) {
  const path = `/api/npcs/${npc.id}/card`;
  const status = h('p', { role: 'status', class: 'hint' }, 'Checking available export options…');
  const error = h('p', { role: 'alert', hidden: true });
  const choices = h('div', { class: 'actions' });
  const voiceOptions = h('div', { hidden: true });
  const handoff = h('p', { class: 'hint', hidden: true });
  let working = false;
  const close = btn('Close', null, () => dialog.close(), { cls: 'small ghost' });
  const dialog = h('dialog', { class: 'about', 'aria-labelledby': 'characterCardTitle',
    oncancel: event => { if (working) event.preventDefault(); }, onclose: () => dialog.remove() },
    h('div', { class: 'about-body' },
      h('div', { class: 'about-head' }, h('h2', { id: 'characterCardTitle', class: 'grow' }, 'Export character card'), close),
      h('p', {}, 'PNG carries the portrait and character text. CHARX also carries the available expressions, sprite, living portrait and voice.'),
      voiceOptions, choices, handoff, status, error));
  document.body.append(dialog);
  dialog.showModal();

  try {
    const options = await api('GET', `${path}-options`);
    if (!dialog.open) return;
    const cast = options.cast_voice;
    const permission = h('input', { type: 'checkbox' });
    const include = h('input', { type: 'checkbox', checked: true });
    voiceOptions.hidden = !cast;
    if (cast?.consent_for) {
      voiceOptions.append(h('label', {}, permission, ` Include ${cast.name} in the CHARX file. If this voice uses a person’s recording, I have their permission to share it.`));
    } else if (cast) {
      voiceOptions.append(h('label', {}, include, ` Include ${cast.name} from Voice Lab in the CHARX file`));
    }
    if (!options.altera_inbox) {
      handoff.hidden = false;
      handoff.append('To enable Send to Altera, start ',
        h('a', { href: 'https://github.com/Fel101/Altera', target: '_blank', rel: 'noopener' }, 'Fel’s Altera'), ' once.');
    }

    async function run(format, send = false) {
      const voice = format === 'charx' && cast && (cast.consent_for ? permission.checked : include.checked);
      working = true;
      for (const button of choices.children) button.disabled = true;
      close.disabled = permission.disabled = include.disabled = true;
      error.hidden = true;
      status.hidden = false;
      status.textContent = voice
        ? 'Voice Lab is making the audition clip. This can take several minutes and runs until Voice Lab finishes.'
        : 'Preparing the character card…';
      try {
        const request = { send, include_voice: include.checked, permission: permission.checked, consent_for: cast?.consent_for };
        if (send) {
          await api('POST', `${path}.charx`, request);
          toast('Card sent. Open Altera to review and import it.', 'ok');
        } else {
          const response = await fetch(`${path}.${format}`, format === 'charx' ? {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(request),
          } : undefined);
          if (!response.ok) {
            const data = await response.json().catch(() => ({}));
            throw new Error(data.error || 'Forge could not export the card. Check that Forge is running and try again.');
          }
          const url = URL.createObjectURL(await response.blob());
          const link = h('a', { href: url, download: `${npc.id}.${format}` });
          document.body.append(link);
          link.click();
          link.remove();
          setTimeout(() => URL.revokeObjectURL(url), 30_000);
        }
        dialog.close();
      } catch (failure) {
        error.textContent = failure.message;
        error.hidden = false;
      } finally {
        working = false;
        status.hidden = true;
        for (const button of choices.children) button.disabled = false;
        close.disabled = permission.disabled = include.disabled = false;
      }
    }

    choices.append(btn('Download PNG', 'download', () => run('png')),
      btn('Download CHARX', 'download', () => run('charx')));
    if (options.altera_inbox) choices.append(btn('Send to Altera', null, () => run('charx', true)));
  } catch (failure) {
    error.textContent = failure.message;
    error.hidden = false;
  } finally {
    status.hidden = true;
  }
}
