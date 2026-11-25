import {byId, node} from './dom.js';
import {labels} from './status.js';

export function renderTrace(cell) {
  const panel = byId('trace');
  if (!cell) {
    panel.replaceChildren(node('p', '尚未选中格点'));
    byId('hold-site').textContent = '尚未选中格点';
    byId('round-site').textContent = '尚未选中格点';
    return;
  }
  const site = `(${cell.site.join(', ')})`;
  const heading = node('strong', `${site} · ${labels[cell.state]}`);
  const revision = node('div', `第 ${cell.revision} 代 · ${cell.edge ? '边缘格点' : '内部格点'}`, 'trace-info');
  panel.replaceChildren(heading, revision);
  byId('hold-site').textContent = `${site} · ${labels[cell.state]}`;
  byId('round-site').textContent = cell.round
    ? `${site} · ${labels[cell.state]} · 轮次 ${cell.round} 仍未结案`
    : `${site} · ${labels[cell.state]}${cell.state === 'review' ? ' · 可开立两站轮次' : ''}`;
  if (!cell.chain.length) {
    panel.append(node('p', '没有探针记录'));
    return;
  }
  const list = node('ol', undefined, 'chain');
  for (const event of cell.chain) {
    const entry = node('li');
    const label = {hold: '复核', round: '开立轮次', submission: '工位来件', verdict: '裁决', void: '作废'}[event.event];
    entry.append(node('strong', label ? `第 ${event.revision} 代 · ${label}` : `${event.batch} · ${labels[event.state]}`));
    entry.append(node('span', event.why));
    if (event.event === 'submission') {
      entry.append(node('small', `${event.station} 站 · 轮次 ${event.round} · 批次 ${event.batch} · 父量测 ${event.parent}`));
    }
    if (event.event === 'verdict') {
      entry.append(node('small', `采用 ${event.station} 站来件 · 轮次 ${event.round} · 批次 ${event.batch}`));
    }
    if (event.event === 'round') {
      entry.append(node('small', `轮次 ${event.id} · 父量测 ${event.parent} · 两站 ${event.stations.join('、')}`));
    }
    if (event.record) {
      const row = event.record;
      if (row.contact === 'ok') entry.append(node('small', `裕量 ${row.margin_mv} mV / 下限 ${event.margin_min} mV · 漏电 ${row.leak_na} nA`));
      entry.append(node('small', `设备坐标 (${row.x}, ${row.y})${row.parent ? ` · 父批次 ${row.parent}` : ''}`));
    }
    list.append(entry);
  }
  panel.append(list);
}
