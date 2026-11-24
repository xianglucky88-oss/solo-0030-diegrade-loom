import {downloadURL, request, waferPath} from './api.js';
import {byId, node, options, table} from './dom.js';
import {flash, labels} from './status.js';

function download(text, wafer, revision, format) {
  const link = node('a', text);
  link.href = downloadURL(wafer, revision, format);
  return link;
}

export function renderReleases(data, action) {
  const list = byId('archives');
  const form = byId('compare');
  const releases = (data?.releases ?? []);
  byId('seal').disabled = !data?.ready;
  byId('release-status').textContent = !data ? '尚未选择晶圆' : data.ready ? '全片分档完成' : '有未测或待复测格点';
  list.replaceChildren(...releases.map(seq => {
    const row = node('div', undefined, 'archive row');
    const replay = node('button', '复算');
    replay.addEventListener('click', action(async () => {
      const result = await request(waferPath(data.wafer.id, `/releases/${seq}/replay`));
      flash(`第 ${seq} 代复算一致，共 ${result.rows} 颗；摘要 ${result.sha256}`);
    }));
    row.append(node('strong', `第 ${seq} 代`), download('分选表', data.wafer.id, seq, 'sorter'),
      download('证据包', data.wafer.id, seq, 'manifest'), replay);
    return row;
  }));
  form.hidden = releases.length < 2;
  const values = releases.map(value => [value, `第 ${value} 代`]);
  options(form.elements.older, values, releases[1]);
  options(form.elements.newer, values, releases[0]);
}

export function clearComparison() { byId('comparison').replaceChildren(); }

export function bindReleases(action, current, reload) {
  byId('seal').addEventListener('click', action(async () => {
    const {wafer, revision} = current();
    await request(waferPath(wafer.id, '/releases'), {expected: revision});
    await reload();
    flash(`已签出第 ${revision} 代分选表`);
  }));
  byId('compare').addEventListener('submit', action(async event => {
    const {wafer} = current();
    const values = new FormData(event.target);
    clearComparison();
    const result = await request(waferPath(wafer.id, `/compare/${values.get('older')}/${values.get('newer')}`));
    if (current().wafer.id !== wafer.id) return;
    const area = byId('comparison');
    if (!result.changes.length) area.append(node('p', '分档和来源均无变化'));
    else area.append(table(['格点', '旧档', '新档', '原来源', '新来源', '旧设备坐标', '新设备坐标', '变化'], result.changes.map(c => {
      const device = coords => Array.isArray(coords) && coords[0] !== null ? `(${coords.join(', ')})` : '—';
      return [c.site.join(', '), labels[c.before], labels[c.after], c.from_batch, c.to_batch,
        device(c.from_device), device(c.to_device), c.kind === 'grade' ? '改档' : '同档来源变化'];
    })));
  }));
}
