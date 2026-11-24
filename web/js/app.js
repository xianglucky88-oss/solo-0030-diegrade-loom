import {request, waferPath} from './api.js';
import {bindBatch, clearPreview} from './batch-form.js';
import {bindCatalog, loadRules} from './catalog.js';
import {byId, options} from './dom.js';
import {bindTabs} from './navigation.js';
import {bindReleases, clearComparison, renderReleases} from './releases.js';
import {flash, renderCounters} from './status.js';
import {renderTrace} from './trace.js';
import {bindTrials, resetTrials} from './trials.js';
import {renderMap} from './wafer-map.js';

let data, selected, loadSequence = 0, traceSequence = 0;

function action(fn) {
  return async event => {
    if (event?.type === 'submit') event.preventDefault();
    flash('');
    try { await fn(event); }
    catch (error) { flash(error.message, true); }
  };
}

function current() {
  if (!data) throw new Error('请先选择晶圆');
  if (byId('wafer').value !== data.wafer.id) throw new Error('正在切换晶圆');
  return data;
}

const pick = action(async site => {
  const snapshot = current();
  const token = ++traceSequence;
  const cell = await request(waferPath(snapshot.wafer.id, `/cells/${site[0]}/${site[1]}`));
  if (token !== traceSequence || data !== snapshot) return;
  if (cell.revision !== snapshot.revision) throw new Error('现场代次已变化，请刷新');
  selected = site;
  byId('inspect').elements.x.value = site[0];
  byId('inspect').elements.y.value = site[1];
  renderMap(data, selected, pick);
  renderTrace(cell);
});

function render(data) {
  byId('wafer-title').textContent = data?.wafer.id ?? '晶圆工作区';
  const rule = data?.wafer.rule;
  byId('wafer-meta').textContent = data ? `第 ${data.revision} 代 · ${rule.name} / 第 ${rule.version} 版` : '';
  renderCounters(data);
  renderMap(data, selected, pick);
  renderTrace(null);
  renderReleases(data, action);
  byId('flag-focus').disabled = !data?.cells.some(c => c.state === 'missing' || c.state === 'review');
}

async function open(id = byId('wafer').value) {
  const token = ++loadSequence;
  ++traceSequence;
  const wafers = await request('/wafers');
  const choice = wafers.some(item => item.id === id) ? id : wafers[0]?.id;
  const next = choice ? await request(waferPath(choice)) : null;
  if (token !== loadSequence) return;
  if (data?.wafer.id !== choice) selected = null;
  data = next;
  resetTrials(choice);
  options(byId('wafer'), wafers.map(item => [item.id, item.id]), choice);
  clearComparison();
  clearPreview();
  render(data);
  if (selected) await pick(selected);
}

function initialize() {
  bindTabs();
  bindBatch(action, current, open);
  bindCatalog(action, open);
  bindReleases(action, current, open);
  bindTrials(action, current);
  byId('reload').addEventListener('click', action(() => open()));
  byId('wafer').addEventListener('change', action(event => open(event.target.value)));
  byId('flag-focus').addEventListener('click', () => {
    const cell = data?.cells.find(item => item.state === 'review') ?? data?.cells.find(item => item.state === 'missing');
    if (cell) pick(cell.site);
  });
  byId('inspect').addEventListener('submit', action(event => {
    const fields = new FormData(event.target);
    return pick([Number(fields.get('x')), Number(fields.get('y'))]);
  }));
  byId('hold-form').addEventListener('submit', action(async event => {
    const {wafer, revision} = current();
    if (!selected) throw new Error('请选择要复核的格点');
    const values = new FormData(event.target);
    await request(waferPath(wafer.id, '/holds'), {expected: revision, id: values.get('id'), reason: values.get('reason'), site: selected});
    await open();
    flash('已登记复核，等待新的探针批次');
  }));
  render(null);
  action(() => Promise.all([open(), loadRules()]))();
}

initialize();
