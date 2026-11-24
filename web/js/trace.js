import {byId, node} from './dom.js';
import {labels} from './status.js';

export function renderTrace(cell) {
  const panel = byId('trace');
  if (!cell) {
    panel.replaceChildren(node('p', '尚未选中格点'));
    byId('hold-site').textContent = '尚未选中格点';
    return;
  }
  const site = `(${cell.site.join(', ')})`;
  const heading = node('strong', `${site} · ${labels[cell.state]}`);
  const revision = node('div', `第 ${cell.revision} 代 · ${cell.edge ? '边缘格点' : '内部格点'}`, 'trace-info');
  panel.replaceChildren(heading, revision);
  byId('hold-site').textContent = `${site} · ${labels[cell.state]}`;
  if (cell.conditions) {
    const parts = Object.entries(cell.conditions).map(([name, part]) => {
      const margin = part.margin_mv === null ? '' : ` · 裕量 ${part.margin_mv} mV`;
      return `条件 ${name}：${labels[part.state]}${part.batch ? ` · ${part.batch}` : ''}${margin}`;
    });
    panel.append(node('div', parts.join('；'), 'trace-info'));
  }
  if (!cell.chain.length) {
    panel.append(node('p', '没有探针记录'));
    return;
  }
  const list = node('ol', undefined, 'chain');
  for (const event of cell.chain) {
    const condition = event.condition ? ` · 条件 ${event.condition}` : '';
    const entry = node('li');
    entry.append(node('strong', event.event === 'hold' ? `第 ${event.revision} 代 · 复核${condition}` : `${event.batch} · ${labels[event.state]}${condition}`));
    entry.append(node('span', event.why));
    if (event.record) {
      const row = event.record;
      if (row.contact === 'ok') entry.append(node('small', `裕量 ${row.margin_mv} mV / 下限 ${event.margin_min} mV · 漏电 ${row.leak_na} nA`));
      entry.append(node('small', `设备坐标 (${row.x}, ${row.y})${row.parent ? ` · 父批次 ${row.parent}` : ''}`));
    }
    list.append(entry);
  }
  panel.append(list);
}
