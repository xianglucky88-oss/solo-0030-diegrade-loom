import {request, waferPath} from './api.js';
import {byId, node, options, table} from './dom.js';
import {flash, labels} from './status.js';

const handling = {accepted: '已接纳', quarantined: '隔离中', revoked: '已撤销', corrected: '已更正'};

export function clearConsignPreview() { byId('consign-result').replaceChildren(); }

function parseDispositions(text) {
  return text.split('\n').map(row => row.trim()).filter(Boolean).map(row => {
    const cells = row.split(',').map(cell => cell.trim());
    const [line, action, ...rest] = cells;
    if (action === 'revoke' && !rest.length) return {line: Number(line), action};
    if (action === 'correct' && rest.length === 6) {
      const [x, y, contact, leak, margin, parent] = rest;
      return {line: Number(line), action, record: {x: Number(x), y: Number(y), contact,
        leak_na: contact === 'open' ? null : Number(leak),
        margin_mv: contact === 'open' ? null : Number(margin), parent}};
    }
    throw new Error(`行处置格式不正确：${row}`);
  });
}

export function bindConsign(action, current, reload) {
  const form = byId('consign-form');
  form.addEventListener('submit', action(async event => {
    const preview = (event.submitter?.value || 'preview') === 'preview';
    const values = new FormData(form);
    const {wafer, revision} = current();
    const consignment = {id: values.get('id'), csv: values.get('csv'),
      frame: {offset: [Number(values.get('dx')), Number(values.get('dy'))], turn: Number(values.get('turn')), mirror: values.has('mirror')}};
    const outcome = await request(waferPath(wafer.id, preview ? '/consignments/preview' : '/consignments'), {expected: revision, consignment});
    if (!preview) {
      flash(outcome.duplicate ? `来件 ${consignment.id} 已收录过` : `来件 ${consignment.id} 已送交第 ${outcome.revision} 代`);
      await reload();
      return;
    }
    const area = byId('consign-result');
    area.replaceChildren();
    if (outcome.duplicate) {
      area.append(node('p', '相同来件已经送交'));
      return;
    }
    area.append(node('p', `预检：接纳 ${outcome.accepted.length} 行，隔离 ${outcome.quarantined.length} 行，未写入`));
    const rows = [
      ...outcome.accepted.map(item => [item.line, item.site.join(', '), '拟接纳', labels[item.state]]),
      ...outcome.quarantined.map(item => [item.line, item.site ? item.site.join(', ') : '—', '拟隔离', item.reason]),
    ].sort((a, b) => a[0] - b[0]);
    if (rows.length) area.append(table(['行号', '物理格点', '处置', '说明'], rows));
  }));
  byId('rework-form').addEventListener('submit', action(async event => {
    const values = new FormData(event.target);
    const {wafer, revision} = current();
    const rework = {id: values.get('id'), source: values.get('source'), lines: parseDispositions(values.get('lines'))};
    const outcome = await request(waferPath(wafer.id, '/reworks'), {expected: revision, rework});
    flash(outcome.duplicate ? `更正 ${rework.id} 已收录过` : `更正 ${rework.id} 已登记第 ${outcome.revision} 代`);
    await reload();
  }));
}

export function renderConsignments(data) {
  const list = data?.consignments ?? [];
  const select = byId('rework-form').elements.source;
  const kept = select.value;
  options(select, list.map(item => [item.id, `${item.id}（第 ${item.revision} 代）`]), kept);
  byId('consignments').replaceChildren(...list.map(item => {
    const box = node('div', undefined, 'archive');
    box.append(node('strong', `来件 ${item.id} · 第 ${item.revision} 代`));
    box.append(table(['行号', '物理格点', '处置', '说明'], item.lines.map(row => [
      row.line, row.site ? row.site.join(', ') : '—',
      handling[row.status] + (row.by ? ` · ${row.by}` : ''),
      row.reason ?? (row.state ? labels[row.state] : ''),
    ])));
    return box;
  }));
}
