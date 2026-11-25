import {request, waferPath} from './api.js';
import {byId, node, table} from './dom.js';
import {flash} from './status.js';

const statusLabels = {collecting: '收件中', ready: '两站齐备', decided: '已裁决', void: '已作废'};

function evidence(item) {
  const row = item.record;
  if (row.contact === 'open') return `${item.station}：${item.batch}（开路）`;
  return `${item.station}：${item.batch} · 裕量 ${row.margin_mv} mV`;
}

export function renderRounds(data) {
  const panel = byId('round-list');
  const rounds = data?.rounds ?? [];
  if (!rounds.length) {
    panel.replaceChildren(node('p', '本片尚未开立两站复测轮次'));
    return;
  }
  panel.replaceChildren(table(['轮次', '格点', '父量测', '状态', '来件', '结论'], rounds.map(found => [
    found.id, found.site.join(', '), found.parent, statusLabels[found.status] ?? found.status,
    found.submissions.map(evidence).join('；') || '—',
    found.verdict ? `采用 ${found.verdict.station} · ${found.verdict.id}`
      : found.void ? `作废 · ${found.void.id}` : '—',
  ])));
}

function frameOf(values) {
  return {offset: [Number(values.get('dx')), Number(values.get('dy'))],
    turn: Number(values.get('turn')), mirror: values.has('mirror')};
}

export function bindRounds(action, current, reload, site) {
  byId('round-form').addEventListener('submit', action(async event => {
    event.preventDefault();
    const {wafer, revision} = current();
    const point = site();
    if (!point) throw new Error('请先在格点图或单颗证据中选择要复测的格点');
    const values = new FormData(event.target);
    const result = await request(waferPath(wafer.id, '/rounds'), {
      expected: revision, id: values.get('id'), site: point, reason: values.get('reason'),
      stations: [values.get('station_a'), values.get('station_b')]});
    await reload();
    flash(`轮次 ${values.get('id')} 已开立，锁定父量测 ${result.parent}`);
  }));
  byId('submission-form').addEventListener('submit', action(async event => {
    event.preventDefault();
    const {wafer, revision} = current();
    const values = new FormData(event.target);
    const round = values.get('round');
    const result = await request(waferPath(wafer.id, `/rounds/${encodeURIComponent(round)}/submissions`), {
      expected: revision, station: values.get('station'),
      batch: {id: values.get('id'), kind: 'repeat', frame: frameOf(values), csv: values.get('csv')}});
    await reload();
    flash(`轮次 ${round} 已收 ${result.round.received.join('、')} 站来件；状态 ${statusLabels[result.round.status]}`);
  }));
  byId('verdict-form').addEventListener('submit', action(async event => {
    event.preventDefault();
    const {wafer, revision} = current();
    const values = new FormData(event.target);
    const round = values.get('round');
    await request(waferPath(wafer.id, `/rounds/${encodeURIComponent(round)}/verdicts`), {
      expected: revision, id: values.get('id'), station: values.get('station'), reason: values.get('reason')});
    await reload();
    flash(`轮次 ${round} 已裁决，采用 ${values.get('station')} 站来件`);
  }));
  byId('void-form').addEventListener('submit', action(async event => {
    event.preventDefault();
    const {wafer, revision} = current();
    const values = new FormData(event.target);
    const round = values.get('round');
    await request(waferPath(wafer.id, `/rounds/${encodeURIComponent(round)}/voids`), {
      expected: revision, id: values.get('id'), reason: values.get('reason')});
    await reload();
    flash(`轮次 ${round} 已作废，格点仍待复测`);
  }));
}
