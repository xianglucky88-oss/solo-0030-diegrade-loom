import {byId, node} from './dom.js';

export const labels = {a: '甲档', b: '乙档', reject: '不合格', review: '待复测', missing: '未测', reserved: '保留点'};

export function flash(message, error = false) {
  const target = byId('status');
  target.hidden = !message;
  target.textContent = message;
  target.classList.toggle('error', error);
}

export function renderCounters(data) {
  byId('counts').replaceChildren(...Object.entries(labels).map(([key, title]) => {
    const box = node('div', undefined, 'counter');
    const caption = node('span', title);
    caption.prepend(node('i', '', `swatch ${key}`));
    box.append(caption, node('strong', data ? (data.counts[key] ?? 0) : '—'));
    return box;
  }));
}
