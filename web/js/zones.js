import {assetURL, request, waferPath} from './api.js';
import {byId, node, options, table} from './dom.js';
import {flash, labels} from './status.js';

function currentZone() {
  const select = byId('zone-pick');
  return select.value || null;
}

function selectedPlan(zone) {
  return zone?.plan ?? null;
}

export function renderZones(data, action) {
  const summary = byId('zone-summary');
  const select = byId('zone-pick');
  const zones = data?.zone?.zones ?? [];
  const zoned = Boolean(data?.zoned);
  byId('release-zoned').hidden = !zoned;
  byId('seal').hidden = zoned;
  if (!zoned) {
    summary.replaceChildren(node('p', '本片未启用分区交付，按整片签出。'));
    select.replaceChildren();
    select.disabled = true;
    byId('zone-plan').disabled = true;
    byId('zone-review').disabled = true;
    byId('zone-release').disabled = true;
    byId('ledger-get').disabled = true;
    return;
  }
  select.disabled = false;
  summary.replaceChildren(node('p', `共 ${zones.length} 个交付区，合计 ${data.zone.testable} 个可测格点。`));
  summary.append(table(['交付区', '可测格点', '已终判', '状态', '最新方案', '正式件'], zones.map(zone => [
    zone.id,
    String(zone.sites.length),
    String(zone.finalized),
    zone.release ? '已正式交付' : zone.plan ? `可交付；方案第 ${zone.plan} 号` : zone.ready ? '已终判，未准备' : '仍有未测或待复测',
    zone.plan ? String(zone.plan) : '—',
    zone.release ? '已冻结' : '—',
  ])));
  const choice = select.value;
  options(select, zones.map(zone => [zone.id, `交付区 ${zone.id}`]), choice);
  select.disabled = false;
  byId('zone-plan').disabled = false;
  byId('zone-review').disabled = false;
  byId('zone-release').disabled = false;
  byId('ledger-get').disabled = !zones.every(zone => zone.release);
}

function renderPlan(view) {
  const box = byId('zone-plan-view');
  box.replaceChildren();
  if (!view) return;
  box.append(node('p', `交付区 ${view.zone} · 方案第 ${view.seq} 号 · ${view.stale ? '现场已变化，需重新准备' : '与现场一致'}`));
  box.append(table(['格点', '冻结档位', '量测来源', '现场代次', '仍一致'], view.entries.map(entry => [
    `(${entry.site.join(', ')})`, labels[entry.bin], entry.batch, String(entry.revision), entry.unchanged ? '是' : '否',
  ])));
}

export function clearZones() {
  byId('zone-plan-view').replaceChildren();
  byId('ledger-view').replaceChildren();
  byId('zone-status').textContent = '';
  byId('ledger-manifest').hidden = true;
  byId('ledger-sorter').hidden = true;
}

export function bindZones(action, current, reload) {
  byId('zone-plan').addEventListener('click', action(async () => {
    const {wafer, revision} = current();
    const zone = currentZone();
    if (!zone) throw new Error('请选择交付区');
    const result = await request(waferPath(wafer.id, '/zone-plans'), {expected: revision, zone});
    await reload();
    flash(result.duplicate ? `交付区 ${result.zone} 的现状与方案第 ${result.seq} 号一致` : `已准备交付区 ${result.zone} 的方案第 ${result.seq} 号，共 ${result.sites} 颗`);
  }));
  byId('zone-review').addEventListener('click', action(async () => {
    const {wafer} = current();
    const zone = currentZone();
    if (!zone) throw new Error('请选择交付区');
    const listed = (current().zone?.zones ?? []).find(item => item.id === zone);
    const seq = selectedPlan(listed);
    if (!seq) throw new Error('本区尚未准备待交付方案');
    renderPlan(await request(waferPath(wafer.id, `/zone-plans/${encodeURIComponent(zone)}/${seq}`)));
  }));
  byId('zone-release').addEventListener('click', action(async () => {
    const {wafer, revision} = current();
    const zone = currentZone();
    if (!zone) throw new Error('请选择交付区');
    const listed = (current().zone?.zones ?? []).find(item => item.id === zone);
    const seq = selectedPlan(listed);
    if (!seq) throw new Error('本区尚未准备待交付方案');
    const result = await request(waferPath(wafer.id, '/zone-releases'), {expected: revision, zone, plan: seq});
    await reload();
    flash(result.duplicate ? `交付区 ${result.zone} 已正式交付，重取同一份清单` : `交付区 ${result.zone} 已正式交付 ${result.rows} 颗`);
  }));
  byId('ledger-get').addEventListener('click', action(async () => {
    const {wafer} = current();
    const value = await request(waferPath(wafer.id, '/zone-ledger'));
    const verification = await request(waferPath(wafer.id, '/zone-ledger/verify'), value);
    const box = byId('ledger-view');
    box.replaceChildren(node('p', `整片交接清册：${verification.zones} 个正式件，${verification.cells} 个格点，复算一致。`));
    box.append(table(['格点', '交付区', '档位', '来源批次', '现场代次'], value.payload.cells.map(cell => [
      `(${cell.site.join(', ')})`, cell.zone, labels[cell.bin], cell.batch, String(cell.revision),
    ])));
    const manifest = byId('ledger-manifest');
    manifest.href = assetURL(wafer.id, '/zone-ledger/manifest');
    manifest.hidden = false;
    const sorter = byId('ledger-sorter');
    sorter.href = assetURL(wafer.id, '/zone-ledger/csv');
    sorter.hidden = false;
  }));
}
