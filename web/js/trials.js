import {waferPath, request} from './api.js';
import {byId, node, options, table} from './dom.js';
import {flash} from './status.js';

let currentWafer = '';

export function bindTrials(action, current) {
  byId('trial-form').addEventListener('submit', action(async event => {
    const values = new FormData(event.target);
    const [rule, version] = String(values.get('rule') || '').split(':');
    if (!rule) throw new Error('请先选择备选规则版本');
    const payload = {id: values.get('id'), release: Number(values.get('release')),
      rule, rule_version: Number(version)};
    await request(waferPath(current().wafer.id, '/trials'), payload);
    flash(`试评 ${payload.id} 已固定原签件第 ${payload.release} 代与备选规则`);
    await refresh(action, current);
  }));
  byId('trial-pick').addEventListener('submit', action(event => event.preventDefault() || refresh(action, current)));
  byId('trial-batch-form').addEventListener('submit', action(async event => {
    const trial = byId('trial-select').value;
    const values = new FormData(event.target);
    const frame = {offset: [Number(values.get('dx')), Number(values.get('dy'))],
      turn: Number(values.get('turn')), mirror: values.get('mirror') === 'on'};
    const payload = {batch: {id: values.get('id'), kind: 'repeat', frame, csv: values.get('csv')}};
    const result = await request(waferPath(current().wafer.id, `/trials/${encodeURIComponent(trial)}/batches`), payload);
    flash(result.duplicate ? '原样重送，未重复入账' : `回来件 ${result.batch} 已按单接收`);
    await refresh(action, current);
  }));
}

async function refresh(action, current) {
  const wafer = current().wafer.id;
  if (wafer === currentWafer) return draw();
  currentWafer = wafer;
  const ruleSelect = byId('trial-rule');
  const rules = await request('/rules');
  options(ruleSelect, rules.map(rule => [`${rule.name}:${rule.version}`, `${rule.name} · 第 ${rule.version} 版`]));
  return draw();

  async function draw() {
    const trials = await request(waferPath(wafer, '/trials'));
    options(byId('trial-select'), trials.map(item => [item.id, `${item.id}（签件 r${item.release} · ${item.rule}:${item.rule_version}）`]));
    const trialId = byId('trial-select').value;
    const panel = byId('trial-view');
    panel.replaceChildren();
    if (!trialId) {
      panel.append(node('p', '本片尚无试评，先在左侧固定原签件与备选规则。'));
      return;
    }
    const view = await request(waferPath(wafer, `/trials/${encodeURIComponent(trialId)}`));
    const orderRows = view.orders.map(order => [
      order.id, `(${order.site.join(', ')})`, order.status === 'open' ? '待补测' : '已关闭', order.reason]);
    panel.append(node('h3', `试评 ${view.id} · ${view.ready ? '补测单已全部关闭' : '仍有补测单等待回来件'}`));
    panel.append(node('p', `备选规则 ${view.candidate_rule.name} 第 ${view.candidate_rule.version} 版 · 原签件第 ${view.release} 代（现场代次与分选指令不受试评影响）`));
    panel.append(table(['补测单', '物理格点', '状态', '原因'], orderRows));
    const cellRows = view.cells.map(cell => [
      `(${cell.site.join(', ')})`, cell.edge ? '边缘' : '内部', labels(cell.state), cell.basis,
      cell.order?.id ?? '—']);
    panel.append(node('h3', '逐点试评结论'));
    panel.append(table(['格点', '类型', '结论', '依据', '补测单'], cellRows));
  }
}

function labels(state) {
  return {a: '甲档', b: '乙档', reject: '判退', review: '待复测', missing: '未测', reserved: '保留'}[state] ?? state;
}

export function resetTrials(wafer) {
  if (wafer === currentWafer) return;
  currentWafer = wafer;
}
