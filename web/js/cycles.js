import {request, waferPath} from './api.js';
import {byId, node, options, table} from './dom.js';
import {flash} from './status.js';

const stateText = {open: '尚未收到标记', undetermined: '可行位姿未确定', inconsistent: '观测不一致', confirmed: '位姿已确认'};

function poseText(pose) {
  return pose ? `${pose.turn * 90}°${pose.mirror ? ' · 镜像' : ''} · 平移 (${pose.offset.join(', ')})` : '—';
}

function markersText(markers) {
  return Object.entries(markers || {}).map(([name, site]) => `${name}@(${site.join(', ')})`).join(' · ');
}

export function renderCycles(data) {
  const target = byId('cycles');
  const cycles = data?.cycles ?? [];
  const select = byId('mark-form').elements.cycle;
  const current = select.value;
  options(select, cycles.map(cycle => [cycle.id, `${cycle.id} · ${stateText[cycle.state]}`]), current);
  target.replaceChildren();
  if (!cycles.length) {
    target.append(node('p', '尚未登记装夹周期。'));
    return;
  }
  target.append(table(['周期', '状态', '允许位姿', '标记', '锁定位姿', '观测'], cycles.map(cycle => [
    cycle.id, stateText[cycle.state],
    cycle.poses.map(pose => `${pose.turn * 90}°${pose.mirror ? '镜' : ''}`).join(' / '),
    markersText(cycle.markers), poseText(cycle.pose),
    cycle.observations.map(o => `${o.marker}(${o.device.join(', ')})`).join(' · ') || '—',
  ])));
}

function turns(text) {
  const values = String(text || '').split(',').map(item => Number(item.trim())).filter(item => Number.isInteger(item));
  if (!values.length) throw new Error('请给出允许的旋转，如 0,180');
  return values.map(value => value >= 90 ? value / 90 : value);
}

export function bindCycles(action, current, open) {
  byId('cycle-form').addEventListener('submit', action(async event => {
    const {wafer, revision} = current();
    const values = new FormData(event.target);
    let markers;
    try { markers = JSON.parse(values.get('markers')); }
    catch { throw new Error('标记须为 JSON 数组'); }
    const poses = [];
    for (const turn of turns(values.get('turns'))) {
      poses.push({turn, mirror: false});
      if (values.has('mirror')) poses.push({turn, mirror: true});
    }
    await request(waferPath(wafer.id, '/cycles'), {expected: revision, cycle: {id: values.get('id'), poses, markers}});
    await open();
    flash(`装夹周期 ${values.get('id')} 已登记`);
  }));
  byId('mark-form').addEventListener('submit', action(async event => {
    const {wafer, revision} = current();
    const values = new FormData(event.target);
    const outcome = await request(waferPath(wafer.id, `/cycles/${encodeURIComponent(values.get('cycle'))}/marks`), {
      expected: revision, marker: values.get('marker'),
      device: [Number(values.get('x')), Number(values.get('y'))],
    });
    await open();
    const state = outcome.state?.state ?? (outcome.duplicate ? 'duplicate' : 'unknown');
    if (state === 'inconsistent') flash('标记观测相互不一致，不存在可行位姿', true);
    else if (state === 'confirmed') flash(`位姿已确认：${poseText(outcome.state.pose)}`);
    else if (state === 'undetermined') flash('标记观测仍可对应多个位姿，量测暂不可接收');
    else flash('相同标记与设备坐标已送过，不重复计入证据');
  }));
}
