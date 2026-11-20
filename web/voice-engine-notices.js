import { h, S } from './core.js';

export const conditionsTag = engine => engine?.licence_conditions && !S.cfg?.licences?.by_selector?.['voice:' + engine.id]
  ? ' · commercial with conditions' : '';

export function engineNotice(engine) {
  if (!engine?.licence_conditions) return null;
  if (engine.id === 'drama3') return h('div', { class: 'notice stack' },
    h('strong', {}, 'Built with Fish Audio'), h('p', {}, engine.licence_conditions),
    h('p', {}, 'Cloning runs on CPU and can take several minutes. GPU design is experimental. GPU cloning is disabled after a graphics-driver crash on this PC.'),
    h('p', {}, h('a', { href:'https://huggingface.co/fishaudio/drama-3/blob/main/LICENSE', target:'_blank', rel:'noopener' }, 'Fish Audio Community License')),
    h('p', { class:'hint' }, 'Quantized community runtime. Delivery cues may be ignored or spoken. Review words, identity and acting before approving a take.'));
  return h('div', { class: 'notice stack' },
    h('strong', {}, 'Commercial use has conditions'),
    h('p', {}, engine.licence_conditions),
    h('p', {},
      h('a', { href: 'https://github.com/resemble-ai/DramaBox/blob/a70a5818e103c1c9fef22409c1e0c707ebf4f8a7/LICENSE', target: '_blank', rel: 'noopener' }, 'LTX-2 license'),
      ' · ', h('a', { href: 'https://ai.google.dev/gemma/terms', target: '_blank', rel: 'noopener' }, 'Gemma terms')),
    h('p', { class: 'hint' }, 'Save acting direction and guidance in Pronunciation & performances. English only; expressive clips may use lip-sync fallback.'));
}
