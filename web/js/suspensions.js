import {request} from './api.js';
import {byId, node, table} from './dom.js';
import {flash} from './status.js';

export async function loadSuspensions() {
  const list = await request('/suspensions');
  byId('suspensions').replaceChildren(...list.map(item => {
    const row = node('div', undefined, 'archive row');
    const sites = item.affected.reduce((total, wafer) => total + wafer.sites.length, 0);
    row.append(node('strong', `${item.id} · 卡 ${item.card} · 触点 ${item.tip} · 工作段 ${item.segment}`),
      node('span', `${item.reason} · 影响 ${item.affected.length} 片晶圆 / ${sites} 个格点`));
    return row;
  }));
}

export function bindSuspensions(action, reload) {
  byId('suspend-form').addEventListener('submit', action(async event => {
    const values = new FormData(event.target);
    const payload = {id: values.get('id'), card: values.get('card'), tip: Number(values.get('tip')),
      segment: values.get('segment'), reason: values.get('reason')};
    const outcome = await request('/suspensions', payload);
    const area = byId('suspend-result');
    area.replaceChildren();
    if (outcome.affected.length) {
      area.append(table(['晶圆', '新代次', '开放复测点'], outcome.affected.map(wafer =>
        [wafer.wafer, wafer.revision, wafer.sites.map(site => `(${site.join(', ')})`).join('、')])));
    } else {
      area.append(node('p', '没有晶圆以该组合作为当前终档证据'));
    }
    await Promise.all([loadSuspensions(), reload()]);
    flash(outcome.duplicate ? `停用 ${payload.id} 此前已生效` : `停用 ${payload.id} 已生效`);
  }));
}
