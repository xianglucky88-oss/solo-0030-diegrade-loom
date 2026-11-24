import {byId, node} from './dom.js';
import {labels} from './status.js';

const MANUAL = {hold: '复核', suspend: '触点停用'};

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
  if (!cell.chain.length) {
    panel.append(node('p', '没有探针记录'));
    return;
  }
  const list = node('ol', undefined, 'chain');
  for (const event of cell.chain) {
    const entry = node('li');
    const title = event.event === 'measurement' ? `${event.batch} · ${labels[event.state]}`
      : `第 ${event.revision} 代 · ${MANUAL[event.event] ?? event.event}`;
    entry.append(node('strong', title));
    entry.append(node('span', event.why));
    if (event.event === 'suspend') {
      entry.append(node('small', `卡 ${event.card} · 触点 ${event.tip} · 工作段 ${event.segment} · 父证据批次 ${event.batch}`));
    }
    if (event.record) {
      const row = event.record;
      if (row.contact === 'ok') entry.append(node('small', `裕量 ${row.margin_mv} mV / 下限 ${event.margin_min} mV · 漏电 ${row.leak_na} nA`));
      entry.append(node('small', `设备坐标 (${row.x}, ${row.y})${row.parent ? ` · 父批次 ${row.parent}` : ''}`));
      if (row.card) entry.append(node('small', `卡 ${row.card} · 触点 ${row.tip} · 工作段 ${row.segment}`));
      if (row.raw) entry.append(node('small', `原始行 ${row.raw}`));
    }
    list.append(entry);
  }
  panel.append(list);
}
